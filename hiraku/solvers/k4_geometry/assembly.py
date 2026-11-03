"""Assembly order by taking the vehicle apart.

The frame stays (it is the fixture). A part can come out along one of the
six directions (+x, -x, +y, -y, +z, -z) when the shape it sweeps on the way
out meets nothing still in place. Bodies it already overlaps where it is
bolted (boxes are rough) do not block it if a small move along the direction
does not make that overlap worse. The reverse of the taking apart is the
assembly order.

Each suspension corner (the parts riding with one support: wheel, hub, arms,
knuckle, damper; model.corners) is a small group: it is built off the vehicle
first, by taking the group alone apart, and comes off and goes on the frame
as one thing.

Which part comes out next, of those that can: the latest stage of the build
first (STAGES: corners, drive, battery, electrics, then the rest: floor,
seats, body), then the higher one (built from the bottom up), then the one
with the most free directions. Inside a corner, the part fastened furthest
from the frame first (the wheel before the arm it is bolted to). A corner,
and a part bolted to one other part, tries first the directions leading away
from what it is bolted to (route.away_from). Parts that cannot come out are
blocked by each other: "this one
has to go in first, or the other cannot get in afterwards". Occupants and
cargo are not assembled and are left out.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import OrderedDict

import numpy as np

from .capsule import swept_overlap
from .fastening import part_fastenings
from .model import TOUCH_MM, WELDED, Body, bodies, corners, shape_overlap
from .obb import OBB

DIRECTIONS = {"+x": (1, 0, 0), "-x": (-1, 0, 0), "+y": (0, 1, 0), "-y": (0, -1, 0), "+z": (0, 0, 1), "-z": (0, 0, -1)}
NOT_ASSEMBLED = ("occupant", "cargo")


def _frame(b: Body) -> bool:
    """Members and the parts welded into the frame (gussets, through pieces): in place before assembly."""
    return b.kind == "member" or b.kind in WELDED
NUDGE_MM = 20.0
CORNER = "corner"  # unit id prefix of a corner group: "corner:<support id>"
# Build stages by part kind, earliest first; a corner group is stage 0 and kinds not listed are
# in the last stage (with floor, seats and body). Only the choice among parts that can come out
# at the same time follows this; the geometry decides what can.
STAGES = (("motor", "gearbox", "reducer", "driveshaft", "propshaft", "halfshaft", "axle_shaft"),
          ("battery",),
          ("controller", "electrical", "inverter", "charger", "harness"))
# The hands and a short tool, from the outside of what is built to each fastening point: a
# straight sweep, as wide as the opening for two hands (200 x 125 mm) from MIL-STD-1472H
# (Department of Defense Design Criteria Standard: Human Engineering), figure 57, not checked in
# this repository, and at least TOOL_DEPTH_MM long. The reach (606 mm) is item C3 Grip reach
# from back, young adult female 5th percentile, of the AIST anthropometric database 1991-92
# (see people.py), read from https://www.airc.aist.go.jp/dhrt/91-92/data/list.html on 2026-11-03.
TOOL_OPENING_MM = (200.0, 125.0)
TOOL_DEPTH_MM = 300.0  # assumption: room for a hand and a short tool in front of what it fastens
REACH_MM = 606.0  # source: AIST item C3 (above)
SIDE_NAMES = {"+z": "above", "-z": "below", "+x": "the front", "-x": "the back", "+y": "the left", "-y": "the right"}


def stage(kind: str) -> int:
    return next((i + 1 for i, kinds in enumerate(STAGES) if kind in kinds), len(STAGES) + 1)


def swept(obb: OBB, direction, travel: float) -> OBB:
    """Box covering obb slid by `travel` along `direction`: exact when a box axis is along
    the direction, otherwise the world-aligned box around the sweep (larger, safe side)."""
    d = np.asarray(direction, float)
    k = int(np.argmax(np.abs(obb.axes @ d)))
    if abs(float(obb.axes[k] @ d)) > 1.0 - 1e-9:
        half = obb.half.copy()
        half[k] += travel / 2.0
        return OBB(obb.center + d * travel / 2.0, obb.axes, half, obb.name)
    pts = np.vstack([obb.corners(), obb.corners() + d * travel])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    return OBB((lo + hi) / 2.0, np.eye(3), (hi - lo) / 2.0, obb.name)


_CORNER_SIGNS = np.array([[i, j, k] for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)], float)


def _shape_bounds(b: Body | OBB) -> tuple[np.ndarray, np.ndarray]:
    """World-aligned box (lo, hi) around what shape_overlap reads of b: its capsule and its box."""
    obb = b.obb if isinstance(b, Body) else b
    pts = obb.center + (_CORNER_SIGNS * obb.half) @ obb.axes
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    cap = b.capsule if isinstance(b, Body) else None
    if cap is not None:
        lo = np.minimum(lo, np.minimum(cap.a, cap.b) - cap.r)
        hi = np.maximum(hi, np.maximum(cap.a, cap.b) + cap.r)
    return lo, hi


def _near(part: Body, nudged: Body, box: OBB | None, others: list[Body], d: np.ndarray, travel: float) -> np.ndarray:
    """Which of `others` may touch the part where it is, nudged or along its sweep: those whose world box
    comes within SWEEP_SKIP_MM of the world box around all three. The others are apart by more than that
    on a world axis, so every overlap test below gives less than zero for them (a separating axis
    exists, or the capsule's distance is more than its radius) and they can be passed over."""
    los, his = zip(*(_shape_bounds(x) for x in (part, nudged, box) if x is not None))
    lo, hi = np.min(los, axis=0), np.max(his, axis=0)
    if box is None:  # the capsule's sweep: its axis's ends slid by travel, and its radius
        c = nudged.capsule
        ends = np.vstack([c.a, c.b, c.a + d * travel, c.b + d * travel])
        lo, hi = np.minimum(lo, ends.min(axis=0) - c.r), np.maximum(hi, ends.max(axis=0) + c.r)
    olo, ohi = (np.array(x) for x in zip(*(_shape_bounds(o) for o in others))) if others else (np.zeros((0, 3)),) * 2
    gap = np.maximum(olo - hi, lo - ohi).max(axis=1) if len(others) else np.zeros(0)
    return gap <= SWEEP_SKIP_MM


SWEEP_SKIP_MM = 1.0  # world-box gap past which a body cannot be hit (round-off room; any overlap test is < 0 there)


def _blocker1(part: Body, others: list[Body], d: np.ndarray, travel: float) -> str | None:
    nudged = part.moved(d * NUDGE_MM)
    box = swept(nudged.obb, d, travel) if part.capsule is None else None
    near = _near(part, nudged, box, others, d, travel)
    for o, close in zip(others, near):
        if not close:
            continue
        start = shape_overlap(part, o)
        if start > TOUCH_MM:  # already overlapping (bolted): moving away must not make it worse
            if shape_overlap(nudged, o) > start + TOUCH_MM:
                return o.id
            continue
        if box is not None:
            hit = shape_overlap(box, o)
        else:
            hit = swept_overlap(nudged.capsule, d, travel, o.capsule if o.capsule is not None else o.obb)
        if hit > TOUCH_MM:
            return o.id
    return None


def blocker(part: Body | list[Body], others: list[Body], direction, travel: float) -> str | None:
    """The first body that stops `part` (or a group of bodies moving as one) leaving along
    `direction`, or None."""
    d = np.asarray(direction, float)
    for p in part if isinstance(part, list) else [part]:
        hit = _blocker1(p, others, d, travel)
        if hit is not None:
            return hit
    return None


def _take_apart(units: dict[str, list[Body]], fixed: list[Body], travel: float, rank: dict[str, tuple],
                prefer: dict[str, tuple[str, ...]]) -> tuple[list, dict]:
    """Units out one at a time ((unit, moves) in the order they come out) and what is left
    blocked ({unit: [blocker ids]})."""
    from .route import two_moves

    remaining = dict(units)
    removed: list[tuple[str, list[tuple[str, float]]]] = []
    # blocker(unit k, others_of(k), direction) is the first body in others_of(k) that stops it. Taking a unit
    # out only removes bodies from that list (the order of the rest stays), so the answer stays the same
    # unless the body it named was taken out: kept here and asked again only then.
    first_hit: dict[tuple[str, str], str | None] = {}
    gone: set[str] = set()

    def others_of(k):
        return fixed + [b for kk, u in remaining.items() if kk != k for b in u]

    def hit(k: str, d: str) -> str | None:
        key = (k, d)
        if key not in first_hit or (first_hit[key] is not None and first_hit[key] in gone):
            first_hit[key] = blocker(remaining[k], others_of(k), DIRECTIONS[d], travel)
        return first_hit[key]

    def straight(unit_key, prefer_k):  # route.straight on the kept answers
        return [d for d in prefer_k if hit(unit_key, d) is None]

    while remaining:
        best = None
        for k in sorted(remaining):
            free = straight(k, prefer[k])
            if free and (best is None or (rank[k], len(free)) > (rank[best[0]], len(best[1]))):
                best = (k, free)
        if best is None:  # nothing comes out straight: the shortest two-move route
            for k in sorted(remaining):
                moves = two_moves(remaining[k], others_of(k), travel, prefer[k])
                if moves and (best is None or moves[0][1] < best[1][0][1]):
                    best = (k, moves)
            if best is None:
                break
            k, moves = best
        else:
            k, moves = best[0], [(best[1][0], travel)]
        removed.append((k, moves))
        gone.update(b.id for b in remaining[k])
        del remaining[k]
    blocked = {}
    for k, u in remaining.items():
        blocked[k] = sorted({hit(k, d) for d in DIRECTIONS} - {None})
    return removed, blocked


def _fastening_depth(design: dict, bs: dict[str, Body], k: str, seen: frozenset = frozenset()) -> int:
    """0 for a part fastened to the frame (or to nothing), 1 + that of the part it is bolted to."""
    pid = k.split(":", 1)[1]
    ups = [f["to"].split(" ", 1)[1] for f in part_fastenings(design, pid, bs) if f["to"].startswith("part ")]
    ups = [f"part:{u}" for u in ups if f"part:{u}" in bs and f"part:{u}" not in seen]
    return 1 + max(_fastening_depth(design, bs, u, seen | {k}) for u in ups) if ups else 0


def _points(fast: list[dict]) -> list[list[float]]:
    out = []
    for f in fast:
        if "at_mm" in f and f["at_mm"] not in out:
            out.append(f["at_mm"])
    return out


def _bottom(unit: list[Body]) -> float:
    """Height of the unit's lowest point, rounded to the mm (within a stage, lower goes in first)."""
    return round(float(min(b.obb.corners()[:, 2].min() for b in unit)))


def _held_by(design: dict, bs: dict[str, Body], k: str) -> list[np.ndarray]:
    """Centres of the parts that part k is bolted to (one point for a wheel on its hub)."""
    ups = [f"part:{f['to'].split(' ', 1)[1]}" for f in part_fastenings(design, k.split(":", 1)[1], bs)
           if f["to"].startswith("part ")]
    return [bs[u].obb.center for u in ups if u in bs]


def _centre(unit: list[Body]) -> np.ndarray:
    pts = np.vstack([b.obb.corners() for b in unit])
    return (pts.min(axis=0) + pts.max(axis=0)) / 2.0


def frame_fastenings(design: dict, unit: list[Body], bs: dict[str, Body]) -> list[dict]:
    """The fastenings of a unit to what is outside it (a group's parts bolted to each other
    are left out), each with "owner": the body id of the part that has it."""
    ids = {b.id for b in unit}
    out = []
    for b in unit:
        for f in part_fastenings(design, b.id.split(":", 1)[1], bs):
            if f["to"].startswith("part ") and "part:" + f["to"].split(" ", 1)[1] in ids:
                continue
            out.append(dict(f, owner=b.id))
    return out


def disassemble(design: dict, bs: dict[str, Body] | None = None, groups: bool = True) -> dict:
    """{"order": [(body id, direction going in)] part by part (a corner's parts at the place the
    corner goes on), "units": [(unit id, direction going in)] (a part's unit id is its body id,
    a corner's is "corner:<support>"), "groups": {corner unit id: {"parts", "order", "blocked"}},
    "routes": {unit or body id: moves out}, "blocked": {unit or body id: [blockers]}}.
    groups=False takes every part apart on its own (no corner groups)."""
    from .route import PREFERRED, away_from

    bs = bs or bodies(design)
    key = _disassembly_key(design, bs, groups)
    if key in _DISASSEMBLED:  # the same design and bodies taken apart before in this process (H1, H3, H5)
        _DISASSEMBLED.move_to_end(key)
        return copy.deepcopy(_DISASSEMBLED[key])
    members = [b for b in bs.values() if _frame(b)]
    parts = {k: b for k, b in bs.items() if b.kind not in NOT_ASSEMBLED and not _frame(b)}
    pts = np.vstack([b.obb.corners() for b in bs.values()])
    travel = float(np.max(np.ptp(pts, axis=0))) * 1.5
    found = corners(parts) if groups else {}
    units = {f"{CORNER}:{sid}": [bs[k] for k in ks] for sid, ks in found.items()}
    in_group = {k for ks in found.values() for k in ks}
    units.update({k: [b] for k, b in parts.items() if k not in in_group})
    rank = {u: (0 if u.startswith(CORNER + ":") else stage(body[0].kind), _bottom(body)) for u, body in units.items()}
    prefer = {u: away_from(_centre(body), _points(frame_fastenings(design, body, bs)), PREFERRED)
              if u.startswith(CORNER + ":") else PREFERRED for u, body in units.items()}
    removed, blocked = _take_apart(units, members, travel, rank, prefer)
    routes = dict(removed)
    sub = {}
    for sid, ks in found.items():
        g = {k: [bs[k]] for k in ks}
        grank = {k: (_fastening_depth(design, bs, k), _bottom(g[k])) for k in ks}
        gprefer = {k: away_from(bs[k].obb.center, _held_by(design, bs, k), PREFERRED) for k in ks}
        g_removed, g_blocked = _take_apart(g, [], travel, grank, gprefer)
        routes.update(dict(g_removed))
        blocked.update(g_blocked)
        sub[f"{CORNER}:{sid}"] = {"parts": ks, "order": [(k, _reverse(m[0][0])) for k, m in reversed(g_removed)],
                                  "blocked": g_blocked}
    unit_order = [(k, _reverse(moves[0][0])) for k, moves in reversed(removed)]
    order = []
    for k, d in unit_order:
        order += sub[k]["order"] if k in sub else [(k, d)]
    out = {"order": order, "units": unit_order, "groups": sub, "routes": routes, "blocked": blocked}
    _DISASSEMBLED[key] = copy.deepcopy(out)
    while len(_DISASSEMBLED) > DISASSEMBLED_KEPT:
        _DISASSEMBLED.popitem(last=False)
    return out


DISASSEMBLED_KEPT = 4
_DISASSEMBLED: OrderedDict = OrderedDict()  # disassemble()'s results by _disassembly_key


def _disassembly_key(design: dict, bs: dict[str, Body], groups: bool) -> str:
    """Hash of everything disassemble() reads: the design, the bodies (boxes, capsules, kinds, supports,
    nodes) and groups."""
    def body(b: Body) -> list:
        cap = None if b.capsule is None else [b.capsule.a.tolist(), b.capsule.b.tolist(), float(b.capsule.r)]
        return [b.id, b.kind, b.support, sorted(b.nodes), b.obb.center.tolist(), b.obb.axes.tolist(),
                b.obb.half.tolist(), cap]

    text = json.dumps([design, [body(bs[k]) for k in bs], bool(groups)], sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_attach(step: dict) -> bool:
    """Whether an assembly step puts a corner group on the frame (not a part of the design)."""
    return str(step.get("part", "")).startswith(CORNER + ":")


def steps(design: dict, bs: dict[str, Body] | None = None) -> list[dict]:
    """The assembly order as contract `assembly_step` records, with access checks per step:
    no_interference (the way in is clear), tool_clearance (the hands and a short tool get from
    the outside to every fastening point: _hands), hand_reach (each of those points within
    REACH_MM of the outside of what is built), depends_on (parts already in place it touches).

    A corner group gives its own steps first, built off the vehicle (only the group's parts are
    there), then a step with part "corner:<support>" that puts it on the frame."""
    bs = bs or bodies(design)
    asm = disassemble(design, bs)
    members = [b for b in bs.values() if _frame(b)]
    names = {p["id"]: p.get("name", p["id"]) for p in design.get("parts", [])}
    extent = np.vstack([b.obb.corners() for b in bs.values()])
    allowed = _step_fields()
    at_node: dict[str, set[str]] = {}
    for k, b in bs.items():
        if not _frame(b) and b.kind not in NOT_ASSEMBLED:
            for f in part_fastenings(design, k.split(":", 1)[1], bs):
                if f["to"].startswith("node "):
                    at_node.setdefault(f["to"].split(" ", 1)[1], set()).add(k)
    placed: list[Body] = []
    out: list[dict] = []

    def add(step: dict, extra: dict):
        step["order"] = len(out) + 1
        step.update({key: v for key, v in extra.items() if key in allowed})
        out.append(step)
    for k, d_in in asm["units"]:
        if k in asm["groups"]:
            g = asm["groups"][k]
            sid = k.split(":", 1)[1]
            bench: list[Body] = []
            for i, (pk, pd) in enumerate(g["order"]):
                part = bs[pk]
                step, extra = _step(design, bs, [part], pd, asm["routes"][pk], bench, bench, names,
                                    np.vstack([bs[x].obb.corners() for x in g["parts"]]), at_node, bench=True)
                step["note"] = f"sub-assembly {k}, built off the vehicle ({i + 1} of {len(g['order'])}); " + step["note"]
                extra["text"] = f"{sid} の角の組 (車の外で組む): " + extra["text"]
                add(step, extra)
                bench.append(part)
            unit = [bs[pk] for pk, _ in g["order"]]
        else:
            unit = [bs[k]]
        env = members + placed
        step, extra = _step(design, bs, unit, d_in, asm["routes"][k], env, placed, names, extent, at_node,
                            attach=k if k in asm["groups"] else None)
        add(step, extra)
        placed += unit
    for k, blockers in sorted(asm["blocked"].items()):
        add({"part": k.split(":", 1)[1] if not k.startswith(CORNER + ":") else k, "direction": "none",
             "no_interference": False, "tool_clearance": False, "hand_reach": False,
             "blocked_by": [b.split(":", 1)[1] for b in blockers],
             "note": "cannot go in along any of the six directions, nor by sliding first and then one of them"}, {})
    return out


def _step(design: dict, bs: dict[str, Body], unit: list[Body], d_in: str, moves: list, env: list[Body],
          placed: list[Body], names: dict, extent: np.ndarray, at_node: dict[str, set[str]],
          attach: str | None = None, bench: bool = False) -> tuple[dict, dict]:
    """One step record (without "order") and its optional fields (fastenings, path, text)."""
    from .fastening import text_of
    from .route import path_in, text_in

    d_from = -np.asarray(DIRECTIONS[d_in], float)  # the hands work from where the part comes from
    fast = frame_fastenings(design, unit, bs)
    if bench:  # off the vehicle only the fastenings to the group's own parts are made
        fast = [f for f in fast if f["to"].startswith("part ")]
    ids = {b.id for b in unit}
    hands = _hands(fast, unit, env + unit, d_from, at_node)
    touching = {o.id.split(":", 1)[1] for o in placed for b in unit if shape_overlap(b, o) > -TOUCH_MM}
    if attach:
        touching |= {b.id.split(":", 1)[1] for b in unit}
    pid = attach or unit[0].id.split(":", 1)[1]
    step = {"part": pid, "direction": d_in, "depends_on": sorted(touching), "no_interference": True,
            "tool_clearance": hands["ok"], "hand_reach": hands["depth"] <= REACH_MM, "note": hands["note"]}
    if attach:
        members_of = ", ".join(sorted(i.split(":", 1)[1] for i in ids))
        step["note"] = f"puts sub-assembly {attach} ({members_of}) on the frame; " + step["note"]
    if hands["in_way"]:  # it goes in, but the hands cannot get to a fastening (blocked_by is for a part that cannot go in)
        step["note"] += f"; the hands' way is taken by {', '.join(b.split(':', 1)[1] for b in hands['in_way'])}"
    moves = _clear_of(unit, moves, extent)
    plain = [{key: v for key, v in f.items() if key != "owner"} for f in fast]
    if attach:
        sid = attach.split(":", 1)[1]
        what = "・".join(names.get(b.id.split(":", 1)[1], b.id.split(":", 1)[1]) for b in unit)
        text = f"{sid} の角の組 ({what}) を{text_in(moves)}、{text_of(plain, names)}"
    else:
        text = f"{names.get(pid, pid)}を{text_in(moves)}、{text_of(plain, names)}"
    extra = {"fastenings": plain, "path_mm": path_in(_centre(unit), moves), "text": text}
    if attach:
        extra["group_parts"] = sorted(i.split(":", 1)[1] for i in ids)
    if hands["in_way"]:
        extra["tool_blocked_by"] = [b.split(":", 1)[1] for b in hands["in_way"]]
    return step, extra


def _clear_of(unit: list[Body], moves: list[tuple[str, float]], extent: np.ndarray) -> list[tuple[str, float]]:
    """The route with its last move only as long as it takes the unit this margin clear of everything."""
    pts = np.vstack([b.obb.corners() for b in unit])
    for d, dist in moves[:-1]:
        pts = pts + np.asarray(DIRECTIONS[d], float) * dist
    d, _ = moves[-1]
    v = np.asarray(DIRECTIONS[d], float)
    gap = float(np.max(extent @ v) - np.min(pts @ v)) + PATH_MARGIN_MM
    return list(moves[:-1]) + [(d, gap)]


PATH_MARGIN_MM = 100.0  # the path starts this far outside everything (display)


def _step_fields() -> set[str]:
    """The assembly_step fields the contract has (new ones appear when the contract gets them)."""
    from hiraku import contract

    return set(contract.schema()["$defs"]["assembly_step"]["properties"])


def hand_sweep(point, side: np.ndarray, edge: float) -> OBB:
    """The box the hands, forearms and a short tool sweep going from outside (`edge` along
    `side`) straight in to `point`: TOOL_OPENING_MM across, from the point out to the edge (at
    least TOOL_DEPTH_MM)."""
    p = np.asarray(point, float)
    s = np.asarray(side, float)
    k = int(np.argmax(np.abs(s)))
    others = [i for i in range(3) if i != k]
    length = max(edge - float(p @ s), TOOL_DEPTH_MM)
    half = np.array([length / 2.0, TOOL_OPENING_MM[0] / 2.0, TOOL_OPENING_MM[1] / 2.0])
    return OBB(p + s * (length / 2.0 + TOUCH_MM), np.eye(3)[[k, *others]], half)


def hand_way(point, side: np.ndarray, around: list[Body], skip: set[str],
             own: set[str] = frozenset()) -> tuple[float, list[str]]:
    """The hands going straight in to `point` from `side`: (how deep the point is, mm, from the
    outer face of what the way meets, the ids of the bodies in the way). The way is hand_sweep()
    out past everything; a body that starts more than REACH_MM out along it is not met (the way
    is straight only for the arm's reach: beyond it the arm goes around, e.g. under a roll bar's
    top tube). Bodies in `skip` are not in the way (the boxes are rough around a shared mount)
    but still make the point deep, except those in `own` (the part's own box)."""
    p = np.asarray(point, float)
    s = np.asarray(side, float)
    far = max(float(np.max(o.obb.corners() @ s)) for o in around) if around else float(p @ s)
    way = hand_sweep(p, s, far + TOOL_DEPTH_MM)
    met = [o for o in around if o.id not in own and shape_overlap(way, o) > TOUCH_MM
           and float(np.min(o.obb.corners() @ s)) - float(p @ s) <= REACH_MM]  # farther out: the arm goes around it
    edge = max([float(p @ s)] + [float(np.max(o.obb.corners() @ s)) for o in met])
    return edge - float(p @ s), sorted(o.id for o in met if o.id not in skip)


def _hands(fast: list[dict], unit: list[Body], around: list[Body], d_from: np.ndarray,
           at_node: dict[str, set[str]] | None = None) -> dict:
    """Whether the hands get to every fastening point of the unit, each from the side the unit
    comes in from or else from another side (above first), the first free side within REACH_MM
    when there is one: {"ok", "depth" (the deepest point
    reached, mm in along the way), "in_way" (body ids in the way where no side is free),
    "note"}. In the way is anything in `around` other than the part that has the fastening,
    what it is fastened to there, and the other parts bolted at the same node (`at_node`: node
    id -> body ids; their boxes are rough around the shared mount). A unit with no fastening is
    reached at its face on the side it comes from."""
    from .route import PREFERRED

    first = next(n for n, v in DIRECTIONS.items() if np.allclose(v, d_from))
    sides = [first] + [n for n in PREFERRED if n != first]
    targets = []
    for f in fast:
        if "at_mm" not in f:
            continue
        skip = {f["owner"]}
        kind, name = f["to"].split(" ", 1)
        if kind == "node":
            skip |= {o.id for o in around if o.kind == "member" and (name in o.nodes or _ends_near(o, f["at_mm"]))}
            skip |= (at_node or {}).get(name, set())
        else:
            skip.add(f"{kind}:{name}")
        targets.append((f["to"], np.asarray(f["at_mm"], float), skip, {f["owner"]}))
    if not targets:
        b = unit[0]
        reach_out = float(np.sum(b.obb.half * np.abs(b.obb.axes @ d_from)))
        targets.append(("its face", b.obb.center + d_from * reach_out, {b.id for b in unit}, {b.id for b in unit}))
    seen = set()
    done, failed, depth = [], [], 0.0
    for where, p, skip, own in targets:
        if where in seen:
            continue
        seen.add(where)
        tried, free = None, None
        for n in sides:
            deep, in_way = hand_way(p, np.asarray(DIRECTIONS[n], float), around, skip, own)
            if tried is None:
                tried = (n, in_way, deep)
            if not in_way:
                free = free or (where, n, deep)
                if deep <= REACH_MM:  # the first free side within reach (else the first free side)
                    free = (where, n, deep)
                    break
        if free is not None:
            done.append(free)
        else:
            failed.append((where, *tried))
    depth = max([d for *_, d in done] + [d for *_, d in failed] + [0.0])
    parts = [f"hands to {w} from {SIDE_NAMES[n]}, {d:.0f} mm in" for w, n, d in done]
    parts += [f"the hands' way to {w} is taken from every side; from {SIDE_NAMES[n]} by "
              f"{', '.join(i.split(':', 1)[1] for i in iw)}" for w, n, iw, _ in failed]
    in_way = sorted({i for *_, iw, _ in failed for i in iw})
    return {"ok": not failed, "depth": depth, "in_way": in_way, "note": "; ".join(parts)}


def _ends_near(member: Body, point) -> bool:
    """Whether a member ends within half the hands' opening of a point (a member of the same joint,
    e.g. the next piece of a rail split at a node beside it)."""
    o = member.obb
    p = np.asarray(point, float)
    ends = (o.center + o.axes[0] * o.half[0], o.center - o.axes[0] * o.half[0])
    return min(float(np.linalg.norm(e - p)) for e in ends) <= TOOL_OPENING_MM[0] / 2.0 + float(max(o.half[1:]))


def _reverse(direction: str) -> str:
    """The way the part moves going in, when it came out along `direction`."""
    return ("-" if direction[0] == "+" else "+") + direction[1]

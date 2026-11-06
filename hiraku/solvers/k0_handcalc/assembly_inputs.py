"""Inputs of the K0 assembly counts (H1 and H5), read from the design with boxes aligned with the vehicle's axes.

The hand calculation's own geometry: every part not turned (no rpy_deg) as its box, every member that runs along
x, y or z as the box of its length and its section (h toward up, or toward x for an upright member, b across: the
design's own convention for a tube's section). Turned parts and members on a slant are left out: the axis-aligned
box round them is far bigger than they are (on version 1 that box puts the steering column into the dash cross
member by 30 mm and the occupants' thighs into it by 20 mm, none of them real), so the count can miss an overlap of
a turned part or a slanted member, never add one.
Two boxes overlap by the smallest of their overlaps along x, y and z. What may overlap how far is the definition of an
interference, not a calculation, so it is read from the same rule the tool uses (k4_geometry.model.allowance:
welded joints and a part and what it is bolted to up to MOUNT_MM, the rest TOUCH_MM)."""

from __future__ import annotations

import math

import numpy as np

from ..k4_geometry import model as k4
from ..k4_geometry.people import occupants, slide_ends

# not assembled: the people and the load ride in, the frame and what is welded to it is there before any part (as
# catalog.builders.analysis.NOT_ASSEMBLED, which the solvers may not import)
NOT_ASSEMBLED = ("occupant", "cargo", "frame", "gusset", "rollbar", "bracket")
AXIS_TOL = 1e-6  # a member runs along an axis when its other two components are below this share of its length


def turned(part: dict) -> bool:
    return any(abs(float(a)) > 1e-9 for a in part["box"].get("rpy_deg") or (0.0, 0.0, 0.0))


def part_box(part: dict) -> tuple[np.ndarray, np.ndarray] | None:
    """(lo, hi) of a part's box; None for a turned part."""
    if turned(part):
        return None
    c = np.asarray(part["box"]["center_mm"], float)
    half = np.asarray(part["box"]["size_mm"], float) / 2.0
    return c - half, c + half


def member_box(design: dict, member: dict) -> tuple[np.ndarray, np.ndarray] | None:
    """(lo, hi) of a member along x, y or z; None for a slanted one."""
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    a, b = nodes[member["node_a"]], nodes[member["node_b"]]
    d = b - a
    L = float(np.linalg.norm(d))
    if L <= 0.0:
        return None
    along = int(np.argmax(np.abs(d)))
    if np.any(np.abs(np.delete(d, along)) > AXIS_TOL * L):
        return None
    dims = design["sections"][member["section"]]["dims_mm"]
    if "d" in dims:
        h = w = float(dims["d"])
    else:
        h, w = float(dims["h"]), float(dims["b"])
    half = np.zeros(3)
    if along == 2:  # upright: h toward x, b across (y)
        half[0], half[1] = h / 2.0, w / 2.0
    else:  # h up, b across
        half[2] = h / 2.0
        half[1 - along] = w / 2.0
    lo, hi = np.minimum(a, b) - half, np.maximum(a, b) + half
    return lo, hi


def boxes(design: dict) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Body id ("part:<id>", "member:<id>", as k4_geometry.model.bodies) -> (lo, hi); turned parts and slanted
    members left out."""
    out = {f"part:{p['id']}": part_box(p) for p in design.get("parts", []) if not turned(p)}
    for m in design.get("members", []):
        bx = member_box(design, m)
        if bx is not None:
            out[f"member:{m['id']}"] = bx
    return out


def box_overlap(a, b) -> float:
    """How far two axis-aligned boxes overlap (the smallest overlap over x, y, z; negative when apart)."""
    return float(np.min(np.minimum(a[1], b[1]) - np.maximum(a[0], b[0])))


def overlaps(design: dict, only: set[str] | None = None) -> list[tuple[str, str, float]]:
    """Pairs of boxes overlapping by more than the rule allows: (a, b, mm), deepest first. `only`: pairs with one of
    these bodies."""
    bs = k4.bodies(design)
    bx = {k: v for k, v in boxes(design).items() if k in bs}
    keys = sorted(bx)
    out = []
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            if only is not None and ka not in only and kb not in only:
                continue
            o = box_overlap(bx[ka], bx[kb])
            if o <= 0.0:
                continue
            allowed = k4.allowance(bs[ka], bs[kb], design)
            if o > allowed:
                out.append((ka, kb, o))
    return sorted(out, key=lambda t: -t[2])


def overlap_inputs(design: dict) -> dict:
    """assembly.overlap_count's inputs: overlaps at rest, and those of the driver's seat moved to each end of its
    slide (only the pairs with a moved part, as the tool), plus where the deepest is. As the tool
    (shelf.assembly._rider_pair), a moved seat part (seat, back, head restraint) against an occupant's body or one of
    its segments is not counted at the slide's ends: the body stays at the travel's middle there."""
    rest = overlaps(design)
    riders = {f"part:{o['id']}" for o in occupants(design)}
    ends = []
    for _label, d_end, moved in slide_ends(design):
        keys = {f"part:{pid}" for pid in moved}
        ends += [(a, b, o) for a, b, o in overlaps(d_end, keys)
                 if not any(x in keys and y.split(".", 1)[0] in riders for x, y in ((a, b), (b, a)))]
    allp = sorted(rest + ends, key=lambda t: -t[2])
    left = (sum(1 for m in design.get("members", []) if member_box(design, m) is None)
            + sum(1 for p in design.get("parts", []) if turned(p)))
    return {"n_rest": float(len(rest)), "n_slide": float(len(ends)), "pairs": allp, "left_out": float(left)}


STEER_STEP_DEG = 1.0  # the turned wheel checked every this many degrees
PRUNE_MM = 1.0  # room for round-off when boxes that cannot be reached are left out before the exact checks


def _plan_overlap(c, R, half_xy, lo, hi) -> float:
    """How far a rectangle (centre c, axes the columns of R, half sizes half_xy) and an axis-aligned rectangle
    (lo, hi) overlap in plan: the smallest over the four separating axes (negative when apart)."""
    box_c, box_h = (lo + hi) / 2.0, (hi - lo) / 2.0
    corners = np.array([c + R @ np.array([sx * half_xy[0], sy * half_xy[1]]) for sx in (-1, 1) for sy in (-1, 1)])
    best = np.inf
    for axis in (np.array([1.0, 0.0]), np.array([0.0, 1.0]), R[:, 0], R[:, 1]):
        p = corners @ axis
        q = np.array([box_c + np.array([sx * box_h[0], sy * box_h[1]]) for sx in (-1, 1) for sy in (-1, 1)]) @ axis
        best = min(best, min(p.max(), q.max()) - max(p.min(), q.min()))
    return float(best)


def motion_inputs(design: dict) -> dict:
    """assembly.motion_count's inputs: each wheel (kind wheel) as its box, steered wheels turned about the support's
    vertical axis through every STEER_STEP_DEG from -max_steer_angle to +max_steer_angle, and moved up and down by
    half the support's travel_mm; a pair counts once, against the axis-aligned boxes of everything not of its own
    corner and not mounted to it (k4_geometry.model.mounted, the tool's rule)."""
    from ..k4_geometry.model import TOUCH_MM, mounted
    from ..k4_geometry.motion import max_steer

    bs = k4.bodies(design)
    bx = {k: v for k, v in boxes(design).items() if k in bs}
    steer_max = max_steer(design)
    hits = {}
    for s in design["supports"]:
        wheel = next((p for p in design.get("parts", []) if p["kind"] == "wheel"
                      and bs.get(f"part:{p['id']}") is not None and bs[f"part:{p['id']}"].support == s["id"]), None)
        if wheel is None:
            continue
        wk = f"part:{wheel['id']}"
        c = np.asarray(s["position_mm"], float)
        wc = np.asarray(wheel["box"]["center_mm"], float)
        half = np.asarray(wheel["box"]["size_mm"], float) / 2.0
        travel = float(s.get("suspension", {}).get("travel_mm") or 0.0)
        z_lo, z_hi = wc[2] - half[2] - travel / 2.0, wc[2] + half[2] + travel / 2.0
        th = steer_max if s["actuation"]["steer"] and steer_max > 0 else 0.0
        n = max(1, int(math.ceil(2.0 * th / STEER_STEP_DEG)))
        angles = np.linspace(-th, th, n + 1) if th > 0 else [0.0]
        # the turned wheel's corners stay within reach of the support's axis (plus PRUNE_MM for round-off): a box
        # apart from that square along x or y has a negative plan overlap at every angle (_plan_overlap's x or y term)
        reach = float(np.linalg.norm(wc[:2] - c[:2])) + float(np.linalg.norm(half[:2])) + PRUNE_MM
        for k, other in bx.items():
            if bs[k].support == s["id"] or mounted(bs[wk], bs[k], design):
                continue
            dz = min(z_hi, other[1][2]) - max(z_lo, other[0][2])
            if dz <= TOUCH_MM:
                continue
            if any(other[0][i] > c[i] + reach or other[1][i] < c[i] - reach for i in (0, 1)):
                continue
            for a in angles:
                r = math.radians(float(a))
                R = np.array([[math.cos(r), -math.sin(r)], [math.sin(r), math.cos(r)]])
                centre = c[:2] + R @ (wc[:2] - c[:2])
                o = min(_plan_overlap(centre, R, half[:2], other[0][:2], other[1][:2]), dz)
                if o > TOUCH_MM and o > hits.get((wk, k), 0.0):
                    hits[(wk, k)] = o
        for rod in design.get("parts", []):
            rk = f"part:{rod['id']}"
            if rod["kind"] not in ROD_KINDS or rk not in bs or bs[rk].support != s["id"]:
                continue
            for k, o in _rod_hits(design, bs, bx, s, rod, c, th, travel).items():
                if o > hits.get((rk, k), 0.0):
                    hits[(rk, k)] = o
    pairs = sorted(((a, b, o) for (a, b), o in hits.items()), key=lambda t: -t[2])
    return {"n_wheel": float(sum(1 for a, _, _ in pairs if a not in _rod_ids(design))),
            "n_rod": float(sum(1 for a, _, _ in pairs if a in _rod_ids(design))), "pairs": pairs}


ROD_KINDS = ("tie_rod",)  # links with one end on the frame side and one on the wheel (the steering's)
ROD_POINT_MM = 5.0  # points along a rod this far apart
TRAVEL_STEP_MM = 5.0  # the wheel moved up and down in steps of this


def _rod_ids(design: dict) -> set[str]:
    return {f"part:{p['id']}" for p in design.get("parts", []) if p["kind"] in ROD_KINDS}


def _rod_hits(design, bs, bx, s, rod, c, th, travel) -> dict[str, float]:
    """A rod as a straight line of radius half its section, between the ends of its box's long side (the box's x
    axis turned by rpy_deg): the inner end (nearer the vehicle's middle) stays, the outer end turns with the wheel about
    the support's vertical axis and moves up and down with it. Points along it every ROD_POINT_MM; a point's sphere
    against an axis-aligned box."""
    from ..k4_geometry.model import TOUCH_MM, mounted

    r_, p_, y_ = (math.radians(float(a)) for a in rod["box"].get("rpy_deg") or (0.0, 0.0, 0.0))
    axis = np.array([math.cos(y_) * math.cos(p_), math.sin(y_) * math.cos(p_), -math.sin(p_)])
    size = np.asarray(rod["box"]["size_mm"], float)
    mid = np.asarray(rod["box"]["center_mm"], float)
    e1, e2 = mid - axis * size[0] / 2.0, mid + axis * size[0] / 2.0
    inner, outer = (e1, e2) if abs(e1[1]) <= abs(e2[1]) else (e2, e1)
    radius = float(max(size[1], size[2])) / 2.0
    n_pts = max(2, int(math.ceil(float(np.linalg.norm(outer - inner)) / ROD_POINT_MM)) + 1)
    t = np.linspace(0.0, 1.0, n_pts)[:, None]
    angles = np.linspace(-th, th, max(1, int(math.ceil(2.0 * th / STEER_STEP_DEG))) + 1) if th > 0 else [0.0]
    dzs = np.linspace(-travel / 2.0, travel / 2.0, max(1, int(math.ceil(travel / TRAVEL_STEP_MM))) + 1) if travel > 0         else [0.0]
    rk = f"part:{rod['id']}"
    others = {k: v for k, v in bx.items() if bs[k].support != s["id"] and not mounted(bs[rk], bs[k], design)}
    out: dict[str, float] = {}
    if not others:
        return out
    # a box apart by more than the radius (plus PRUNE_MM) from the box round every place of the rod (the inner end,
    # and the outer end anywhere on its circle about the support's axis, up and down by half the travel) is never
    # reached: its depths are below 0 at every angle and height
    rho = float(np.linalg.norm(outer[:2] - c[:2]))
    lo_all = np.minimum(inner, [c[0] - rho, c[1] - rho, outer[2] - travel / 2.0]) - radius - PRUNE_MM
    hi_all = np.maximum(inner, [c[0] + rho, c[1] + rho, outer[2] + travel / 2.0]) + radius + PRUNE_MM
    others = {k: v for k, v in others.items()
              if all(v[0][i] <= hi_all[i] and v[1][i] >= lo_all[i] for i in range(3))}
    if not others:
        return out
    keys = list(others)
    LO = np.array([np.asarray(others[k][0], float) for k in keys])[None, :, :]  # (1, boxes, 3)
    HI = np.array([np.asarray(others[k][1], float) for k in keys])[None, :, :]
    for a in angles:
        ra = math.radians(float(a))
        R = np.array([[math.cos(ra), -math.sin(ra), 0.0], [math.sin(ra), math.cos(ra), 0.0], [0.0, 0.0, 1.0]])
        for dz in dzs:
            o = c + R @ (outer - c) + np.array([0.0, 0.0, float(dz)])
            P = (inner + t * (o - inner))[:, None, :]  # (points, 1, 3): every box at once, the same arithmetic
            d_out = np.linalg.norm(np.maximum(LO - P, 0.0) + np.maximum(P - HI, 0.0), axis=2)
            d_in = np.min(np.minimum(P - LO, HI - P), axis=2)
            depths = np.max(np.where(d_out > 0.0, radius - d_out, radius + d_in), axis=0)
            for k, depth in zip(keys, depths.tolist()):
                if depth > TOUCH_MM and depth > out.get(k, 0.0):
                    out[k] = depth
    return out


def blocked_inputs(design: dict) -> dict:
    """assembly.blocked_count's inputs: the parts to assemble (kinds not in builders NOT_ASSEMBLED, not turned) that
    cannot leave along any of +-x, +-y, +-z: their box pushed to the far side meets a member or a welded piece of the
    frame (kinds frame, gusset, rollbar, bracket; there before any part) by more than the rule allows. A piece that
    already overlaps the part at rest is not counted as stopping it (that overlap is H1-interference's)."""
    bs = k4.bodies(design)
    bx = {k: v for k, v in boxes(design).items() if k in bs}
    # the frame and what is welded to it; and a side net bolted to the side frame (part of the guard, put on with the
    # frame), not one on quick-release clips that opens (k4_geometry.entry.openable_parts)
    from ..k4_geometry.entry import openable_parts

    opens = set(openable_parts(design))
    fixed = {k: v for k, v in bx.items() if k.startswith("member:") or bs[k].kind in NOT_ASSEMBLED
             or (bs[k].kind == "side_net" and k.split(":", 1)[1] not in opens)}
    at_rest = {frozenset((a, b)) for a, b, _ in overlaps(design)}  # counted in H1-interference, not here
    far = 1.0e6
    blocked = []
    def hits(k, lo, hi) -> bool:
        return any(box_overlap((lo, hi), f) > k4.allowance(bs[k], bs[kf], design)
                   for kf, f in fixed.items() if kf != k and frozenset((k, kf)) not in at_rest)

    def straight(k, lo, hi, axis, sign) -> bool:
        s_lo, s_hi = lo.copy(), hi.copy()
        if sign > 0:
            s_hi[axis] = far
        else:
            s_lo[axis] = -far
        return not hits(k, s_lo, s_hi)

    for p in design.get("parts", []):
        k = f"part:{p['id']}"
        if p["kind"] in NOT_ASSEMBLED or k not in bx:
            continue
        lo, hi = bx[k]
        free = any(straight(k, lo, hi, a, s) for a in range(3) for s in (-1.0, 1.0))
        if not free:
            # or a slide first: along one axis by half or all of its own size (the box swept over the slide clear),
            # then straight along another
            size = hi - lo
            for a in range(3):
                for s in (-1.0, 1.0):
                    for frac in (0.5, 1.0):
                        shift = np.zeros(3)
                        shift[a] = s * frac * size[a]
                        sw_lo, sw_hi = np.minimum(lo, lo + shift), np.maximum(hi, hi + shift)
                        if hits(k, sw_lo, sw_hi):
                            continue
                        if any(straight(k, lo + shift, hi + shift, b, t) for b in range(3) if b != a
                               for t in (-1.0, 1.0)):
                            free = True
                            break
                    if free:
                        break
                if free:
                    break
        if not free:
            blocked.append(p["id"])
    return {"n_blocked": float(len(blocked)), "parts": blocked}


def _all_boxes(design: dict) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Every part as the axis-aligned box round its box's corners (turned by rpy_deg as the design defines it,
    k4_geometry.obb.rpy_matrix), a member along an axis as member_box, a slanted one as the axis-aligned box round
    its end points widened by half its section's largest side: never smaller than the shapes, so a gap from these
    boxes is never more than the real one."""
    from ..k4_geometry.obb import rpy_matrix

    out = {}
    for p in design.get("parts", []):
        c = np.asarray(p["box"]["center_mm"], float)
        half = np.asarray(p["box"]["size_mm"], float) / 2.0
        if turned(p):
            half = np.abs(rpy_matrix(p["box"]["rpy_deg"])) @ half
        out[f"part:{p['id']}"] = (c - half, c + half)
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])}
    for m in design.get("members", []):
        dims = design["sections"][m["section"]]["dims_mm"]
        r = max(float(v) for k, v in dims.items() if k in ("h", "b", "d")) / 2.0
        a, b = nodes[m["node_a"]], nodes[m["node_b"]]
        out[f"member:{m['id']}"] = member_box(design, m) or (np.minimum(a, b) - r, np.maximum(a, b) + r)
    return out


JOINT_GAP_MM = 2.0  # a joint's ends may be this far apart: HIRAKU's choice, an assumption, no source
NOT_TOUCHING_VIA = ("wire", "hose", "strap")  # strap: a belt's webbing to its seat


def joint_inputs(design: dict) -> dict:
    """assembly.joint_count's inputs: n_gap, the mechanical connects_to (not wire or hose) whose two ends are more
    than JOINT_GAP_MM apart, measured box to box (or box to the node's or the support's point) with _all_boxes;
    n_none, the parts with no mechanical connects_to."""
    bx = _all_boxes(design)
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])}
    supports = {s["id"]: np.asarray(s["position_mm"], float) for s in design.get("supports", [])}

    def gap(a, b) -> float:
        return float(np.linalg.norm(np.maximum(np.maximum(a[0] - b[1], b[0] - a[1]), 0.0)))

    gaps, none = [], []
    tubes: dict[str, list] = {}  # a part made of members (the roll bar) is its tubes' boxes
    for m in design.get("members", []):
        if m.get("part") and f"member:{m['id']}" in bx:
            tubes.setdefault(m["part"], []).append(bx[f"member:{m['id']}"])
    for p in design.get("parts", []):
        links = [c for c in p.get("connects_to") or [] if c.get("via") not in NOT_TOUCHING_VIA]
        if not links:
            none.append(p["id"])
            continue
        mine = tubes.get(p["id"]) or [bx[f"part:{p['id']}"]]
        for c in links:
            to = c["to"]
            pt = nodes.get(to, supports.get(to))
            other = (pt, pt) if pt is not None else bx.get(f"part:{to}") or bx.get(f"member:{to}")
            if other is None:
                continue
            g = min(gap(me, other) for me in mine)
            if "at_mm" in c:  # where the joint says the two meet: in (or on) both boxes
                at = np.asarray(c["at_mm"], float)
                g = max(g, min(gap(me, (at, at)) for me in mine), gap(other, (at, at)))
            else:
                g = np.inf  # a joint that does not say where it is
            if g > JOINT_GAP_MM:
                gaps.append((p["id"], to, g))
    return {"n_gap": float(len(gaps)), "n_none": float(len(none)), "gaps": sorted(gaps, key=lambda t: -t[2]),
            "none": none}


def envelope_inputs(design: dict) -> dict:
    """assembly.envelope_count's input: the bodies whose _all_boxes box is not inside any one box of their module's
    envelope (design.modules), more than 0.5 mm out."""
    envs = {m["module_id"]: m.get("envelope", []) for m in design.get("modules") or []}
    mods = {f"part:{p['id']}": p.get("module") for p in design.get("parts", [])}
    mods.update({f"member:{m['id']}": m.get("module") for m in design.get("members", [])})
    out = []
    for k, (lo, hi) in _all_boxes(design).items():
        m = mods.get(k)
        if m is None or m == "package" or m not in envs:
            continue
        best = min((float(max(np.max((np.asarray(e["center_mm"]) - np.asarray(e["size_mm"]) / 2.0) - lo),
                              np.max(hi - (np.asarray(e["center_mm"]) + np.asarray(e["size_mm"]) / 2.0)), 0.0))
                    for e in envs[m]), default=np.inf)
        if best > 0.5:
            out.append((k, m, best))
    return {"n_out": float(len(out)), "out": sorted(out, key=lambda t: -t[2])}

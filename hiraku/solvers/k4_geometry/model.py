"""Bodies of a design for geometry checks: part boxes and member boxes, which
pairs are mounted on each other, and the moving wheel groups.

Long thin parts (SLENDER_KINDS) are also held as capsules, and every check
between bodies (shape_overlap) uses the capsule for them instead of the box.

Mounted pairs may overlap, the boxes being rough: members that share a node
(welded joints), a part of kind frame and the members at its carried_by
nodes, frame and gusset parts at the same joint, and the unsprung parts of
the same support (hub in the wheel), a long thin part and the body one of its ends (a joint) lies
in (joined), and a steering column and the panel it goes through any amount; a part and the members that end at its carried_by nodes,
and a suspension part and the members at its support's frame node (bolted
mounts), up to MOUNT_MM. Anything deeper is interference. Brackets (kind bracket) are
welded in like gussets. A person's body parts (kind occupant, one person per id before the
first ".") touch each other and the seat, belt, floors and panels, pedals and steering wheel
any amount (OCCUPANT_REST_KINDS); against anything else they interfere.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

from ..k0_handcalc.vehicle import member_props
from .capsule import Capsule, capsule_box_overlap, capsule_overlap, from_obb, from_points
from .obb import OBB, from_box, from_member, overlap

TOUCH_MM = 1.0  # overlap up to this is touching, not interference
MOUNT_MM = 25.0  # a bolted part's box may sink this far into the member it is mounted on
# Part kinds held as capsules: shafts, steering links and rods. Kinds not in a design yet
# are listed so that designs using them get capsules without a change here.
SLENDER_KINDS = ("driveshaft", "propshaft", "halfshaft", "axle_shaft", "tie_rod", "drag_link", "steering_column",
                 "steering_shaft", "steering_rack", "stabilizer_link", "link", "rod", "damper", "pushrod")
WELDED = {"frame", "gusset", "rollbar", "bracket"}  # part kinds welded into the frame at their carried_by joints
# The parts of a person (kind occupant, one person per id before the first ".": hiraku.contract.loading.
# person_of) touch each other and what the person sits on, rests the feet on and holds any amount; anything
# else they meet is interference.
OCCUPANT_REST_KINDS = ("seat", "seat_belt", "body", "accelerator_pedal", "brake_pedal", "pedal", "steering",
                       "steering_wheel")
# Part kinds that stand for frame members (their box is a member's box): they overlap the members
# at their carried_by nodes any amount (the roll bar part is its top beam, kit/safety.py; a care bed's side
# rail part is the box round its tubes, designgen/care_bed.py).
FRAME_PIECES = ("frame", "rollbar", "side_rail")
# Links with a joint at each end, fixed at the inner end and moving with the wheel at the outer
# end (motion.sweep turns them about the inner end).
# links with an inner end on the body and an outer end on the wheel (the arms' legs, designgen kit/arms.py: link)
LINK_KINDS = ("driveshaft", "halfshaft", "axle_shaft", "tie_rod", "drag_link", "stabilizer_link", "link")
# Kinds that go through a hole in a panel (a part of kind body) on their way: the steering column
# through the floor (assumption: the panel has the hole).
# anchor: a bolt through a sheet, pushrod: the brake pedal's through the bulkhead (designgen kit/joints)
THROUGH_PANEL_KINDS = ("steering_column", "steering_shaft", "anchor", "pushrod")
PANEL_KINDS = ("body",)


@dataclass
class Body:
    id: str  # "part:<id>" or "member:<id>"
    obb: OBB
    kind: str  # part kind, or "member"
    support: str | None = None  # unsprung parts: the support they ride with
    nodes: set[str] = field(default_factory=set)  # members: end nodes; parts: carried_by nodes
    capsule: Capsule | None = None  # long thin parts: checked as this capsule instead of the box

    def moved(self, offset) -> "Body":
        return replace(self, obb=self.obb.moved(offset),
                       capsule=self.capsule.moved(offset) if self.capsule is not None else None)

    def turned(self, R: np.ndarray, about) -> "Body":
        return replace(self, obb=self.obb.turned(R, about),
                       capsule=self.capsule.turned(R, about) if self.capsule is not None else None)


Ends = Callable[[dict, dict], "tuple[list[float], list[float]] | None"]


def part_capsule(part: dict, obb: OBB, ends: Ends | None = None, design: dict | None = None) -> Capsule | None:
    """The capsule of a long thin part, None for other kinds. With `ends` (design, part) -> (p, q)
    giving two known points of the part (e.g. its joints from the suspension hard points), the
    axis runs between them and the radius is half the box's middle side; otherwise from_obb."""
    if part["kind"] not in SLENDER_KINDS:
        return None
    pq = ends(design or {}, part) if ends is not None else None
    if pq is not None:
        return from_points(pq[0], pq[1], float(np.sort(obb.half)[1]), obb.name)
    return from_obb(obb)


def bodies(design: dict, ends: Ends | None = None) -> dict[str, Body]:
    """Members and parts. `ends`: see part_capsule (none given: capsules from the boxes)."""
    out: dict[str, Body] = {}
    for mid, mp in member_props(design).items():
        sec = design["sections"][mp["member"]["section"]]
        dims = sec.get("dims_mm", {})
        if "d" in dims:
            w = h = float(dims["d"])
        elif "b" in dims and "h" in dims:
            w, h = float(dims["b"]), float(dims["h"])
        else:
            w = h = 2.0 * max(float(dims.get("cy", 10.0)), float(dims.get("cz", 10.0)))
        out[f"member:{mid}"] = Body(f"member:{mid}", from_member(mp["a"], mp["b"], w, h, mid), "member",
                                    nodes={mp["member"]["node_a"], mp["member"]["node_b"]})
    supports = design.get("supports", [])
    for p in design.get("parts", []):
        box = p["box"]
        o = from_box(box["center_mm"], box["size_mm"], box.get("rpy_deg"), p["id"])
        carried = set(p.get("carried_by") or [])
        sup = None
        if not carried and supports:  # unsprung: rides with the nearest support
            c = np.asarray(box["center_mm"], float)
            sup = min(supports, key=lambda s: float(np.linalg.norm(np.asarray(s["position_mm"], float) - c)))["id"]
        out[f"part:{p['id']}"] = Body(f"part:{p['id']}", o, p["kind"], sup, carried, part_capsule(p, o, ends, design))
    return out


def corners(bs: dict[str, Body], skip: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """The suspension corners as small groups, {support id: [body ids]}: the parts that ride with
    a support (no carried_by: the wheel, hub, arms, knuckle, damper of that corner). Kinds in
    `skip` are left out. Supports with a single part are not groups."""
    out: dict[str, list[str]] = {}
    for k, b in sorted(bs.items()):
        if b.kind != "member" and b.support is not None and b.kind not in skip:
            out.setdefault(b.support, []).append(k)
    return {s: ks for s, ks in out.items() if len(ks) > 1}


def shape_overlap(a: Body | OBB, b: Body | OBB) -> float:
    """overlap() of two bodies (or boxes), with capsules for the bodies that have one."""
    ca = a.capsule if isinstance(a, Body) else None
    cb = b.capsule if isinstance(b, Body) else None
    oa = a.obb if isinstance(a, Body) else a
    ob = b.obb if isinstance(b, Body) else b
    if ca is None and cb is None:
        return overlap(oa, ob)
    if ca is not None and cb is not None:
        return capsule_overlap(ca, cb)
    return capsule_box_overlap(ca, ob) if ca is not None else capsule_box_overlap(cb, oa)


def mounted(a: Body, b: Body, design: dict) -> bool:
    return allowance(a, b, design) > TOUCH_MM


def allowance(a: Body, b: Body, design: dict) -> float:
    """How far (mm) two boxes may overlap before it counts as interference: welded joints and
    a wheel's own parts any amount, bolted mounts up to MOUNT_MM, everything else TOUCH_MM."""
    if a.kind == "member" and b.kind == "member":
        return float("inf") if a.nodes & b.nodes else TOUCH_MM
    if a.kind == "member" or b.kind == "member":
        part, mem = (b, a) if a.kind == "member" else (a, b)
        if part.kind in FRAME_PIECES and part.nodes & mem.nodes:  # a piece of the frame welded in at the joint
            return float("inf")
        if part.nodes & mem.nodes:
            return MOUNT_MM
        if part.kind == "gusset" and _on_the_line_of(part, mem, design):
            return MOUNT_MM  # welded along a through tube that goes on in another member past the joint's node
        if part.capsule is not None and _ends_in_bracket_on(part, mem, design):
            return float("inf")  # a link or damper pinned in a bracket welded on this member's end
        if part.support and part.kind == "suspension":  # arms are bolted to the frame at the mount
            fn = next((s.get("frame_node") for s in design["supports"] if s["id"] == part.support), None)
            return MOUNT_MM if fn in mem.nodes else TOUCH_MM
        return TOUCH_MM
    if {a.kind, b.kind} <= WELDED and a.nodes & b.nodes:  # frame pieces and gussets welded at the same joint
        return float("inf")
    if "occupant" in (a.kind, b.kind):
        o, other = (a, b) if a.kind == "occupant" else (b, a)
        if other.kind == "occupant" and _person(o.id) == _person(other.id):
            return float("inf")
        if other.kind in OCCUPANT_REST_KINDS:
            return float("inf")
    if joined(a, b):  # a link and what it is jointed to at an end (rack and tie rod, shaft and reducer)
        return float("inf")
    if (a.kind in THROUGH_PANEL_KINDS and b.kind in PANEL_KINDS) or (b.kind in THROUGH_PANEL_KINDS and a.kind in PANEL_KINDS):
        return float("inf")
    return float("inf") if a.support is not None and a.support == b.support else TOUCH_MM


def _person(body_id: str) -> str:
    """The person of a body "part:<id>": the id up to the first "." (hiraku.contract.loading.person_of)."""
    return body_id.split(":", 1)[-1].split(".", 1)[0]


def _on_the_line_of(part: Body, mem: Body, design: dict) -> bool:
    """Whether the member lies on the straight line of a member that ends at one of the part's carried_by
    nodes (the same tube going on past another node)."""
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])}
    ms = {m["id"]: m for m in design.get("members", [])}
    me = ms.get(mem.id.split(":", 1)[-1])
    if me is None:
        return False
    a, b = nodes[me["node_a"]], nodes[me["node_b"]]
    d = (b - a) / max(float(np.linalg.norm(b - a)), 1e-12)
    for m in ms.values():
        if m is me or not {m["node_a"], m["node_b"]} & part.nodes:
            continue
        p, q = nodes[m["node_a"]], nodes[m["node_b"]]
        e = (q - p) / max(float(np.linalg.norm(q - p)), 1e-12)
        if abs(abs(float(d @ e)) - 1.0) < 1e-9 and float(np.linalg.norm(np.cross(p - a, d))) < 1e-6:
            return True
    return False


def _ends_in_bracket_on(part: Body, mem: Body, design: dict) -> bool:
    """Whether an end of a long thin part's capsule lies in a bracket part welded (carried_by) at a node of
    the member: the link is pinned in that clevis, which sits on the member's end."""
    for p in design.get("parts", []):
        if p.get("kind") != "bracket" or not set(p.get("carried_by") or []) & mem.nodes:
            continue
        o = from_box(p["box"]["center_mm"], p["box"]["size_mm"], p["box"].get("rpy_deg"), p["id"])
        br = Body(f"part:{p['id']}", o, "bracket", None, set(p.get("carried_by") or []))
        if _inside(part.capsule.a, br) or _inside(part.capsule.b, br):
            return True
    return False


def _inside(point: np.ndarray, b: Body) -> bool:
    """Whether a point is in a body's shape (its capsule, else its box), within TOUCH_MM."""
    if b.capsule is not None:
        c = b.capsule
        d = c.b - c.a
        t = float(np.clip((point - c.a) @ d / max(float(d @ d), 1e-12), 0.0, 1.0))
        return float(np.linalg.norm(point - (c.a + t * d))) <= c.r + TOUCH_MM
    local = b.obb.axes @ (point - b.obb.center)
    return bool(np.all(np.abs(local) <= b.obb.half + TOUCH_MM))


def joined(a: Body, b: Body) -> bool:
    """Whether a long thin part ends in the other body: an end of its capsule's axis (a joint) lies in it."""
    for x, y in ((a, b), (b, a)):
        if x.capsule is not None and (_inside(x.capsule.a, y) or _inside(x.capsule.b, y)):
            return True
    return False


def reach(bs: dict[str, Body], keys: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """(centres (n, 3), radii (n,)): a sphere about each body's box centre holding its shape (the box's half diagonal,
    or its capsule's farther end plus the capsule's radius when that is larger)."""
    return spheres([bs[k] for k in keys])


def spheres(items: list[Body]) -> tuple[np.ndarray, np.ndarray]:
    """reach() of a list of bodies, in its order."""
    C = np.array([b.obb.center for b in items], float).reshape(-1, 3)
    R = np.empty(len(items))
    for i, b in enumerate(items):
        r = float(np.linalg.norm(b.obb.half))
        if b.capsule is not None:
            r = max(r, max(float(np.linalg.norm(b.capsule.a - C[i])), float(np.linalg.norm(b.capsule.b - C[i])))
                    + b.capsule.r)
        R[i] = r
    return C, R


def interferences(design: dict, items: dict[str, Body] | None = None) -> list[tuple[str, str, float]]:
    """Pairs whose shapes overlap by more than they are allowed to: (a, b, overlap mm)."""
    bs = items or bodies(design)
    keys = sorted(bs)
    C, R = reach(bs, keys)
    # shapes whose bounding spheres are apart overlap by a negative amount, never more than an allowance (>= TOUCH_MM)
    near = np.sqrt(((C[:, None, :] - C[None, :, :]) ** 2).sum(axis=2)) <= R[:, None] + R[None, :]
    out = []
    for i, ka in enumerate(keys):
        for j in np.nonzero(near[i, i + 1:])[0] + i + 1:
            kb = keys[j]
            a, b = bs[ka], bs[kb]
            allowed = allowance(a, b, design)
            if allowed == float("inf"):
                continue
            o = shape_overlap(a, b)
            if o > allowed:
                out.append((ka, kb, o))
    return sorted(out, key=lambda t: -t[2])

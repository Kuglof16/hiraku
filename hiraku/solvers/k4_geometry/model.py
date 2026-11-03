"""Bodies of a design for geometry checks: part boxes and member boxes, which
pairs are mounted on each other, and the moving wheel groups.

Long thin parts (SLENDER_KINDS) are also held as capsules, and every check
between bodies (shape_overlap) uses the capsule for them instead of the box.

Mounted pairs may overlap, the boxes being rough: members that share a node
(welded joints), a part of kind frame and the members at its carried_by
nodes, frame and gusset parts at the same joint, and the unsprung parts of
the same support (hub in the wheel) any amount; a part and the members that end at its carried_by nodes,
and a suspension part and the members at its support's frame node (bolted
mounts), up to MOUNT_MM. Anything deeper is interference.
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
                 "steering_rack", "stabilizer_link", "link", "rod", "damper")
WELDED = {"frame", "gusset"}  # part kinds welded into the frame at their carried_by joints


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
        if part.kind == "frame" and part.nodes & mem.nodes:  # a piece of the frame welded in at the joint
            return float("inf")
        if part.nodes & mem.nodes:
            return MOUNT_MM
        if part.support and part.kind == "suspension":  # arms are bolted to the frame at the mount
            fn = next((s.get("frame_node") for s in design["supports"] if s["id"] == part.support), None)
            return MOUNT_MM if fn in mem.nodes else TOUCH_MM
        return TOUCH_MM
    if {a.kind, b.kind} <= WELDED and a.nodes & b.nodes:  # frame pieces and gussets welded at the same joint
        return float("inf")
    return float("inf") if a.support is not None and a.support == b.support else TOUCH_MM


def interferences(design: dict, items: dict[str, Body] | None = None) -> list[tuple[str, str, float]]:
    """Pairs whose shapes overlap by more than they are allowed to: (a, b, overlap mm)."""
    bs = items or bodies(design)
    keys = sorted(bs)
    out = []
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            a, b = bs[ka], bs[kb]
            allowed = allowance(a, b, design)
            if allowed == float("inf"):
                continue
            o = shape_overlap(a, b)
            if o > allowed:
                out.append((ka, kb, o))
    return sorted(out, key=lambda t: -t[2])

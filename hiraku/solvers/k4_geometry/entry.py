"""Getting in and out of a seat from the side: the opening beside each seat, what blocks it, the first step and
the hand holds (test H7, proposed; the entry_opening tool and the K0 readers in k0_handcalc.entry_inputs).

The seat's side: a seat whose box is off the vehicle's middle (|y| of its centre > 1 mm) is entered from the side it
is on (outboard, +y for a seat on the left). Its passage is everything outboard of the seat box's outer face.

The seat's span (the window): along x from the seat box's rear face to the front of the seated person's body parts
(the occupant whose trunk stands on the seat and the parts <trunk id>.<segment>); the seat box's front face when no
person is on it. The opening is looked for in this span: getting out, the person moves out of the place they sit in
(an assumption of this repository; no source gives where along the vehicle the opening must be).

Heights: z_seat is the top of the seat box (the cushion), z_floor the top of the highest part of kind body under the
seat's middle (the floor; the seat box's bottom when there is none), the ground the plane through the lowest tyre
bottom (as the spray lines', spray.py).

Every member and part is taken as its axis-aligned bounding box (an oblique box as the box round it: a little larger
than it is). A body blocks a band of heights [za, zb] beside a seat when its box reaches outboard of the seat's outer
face by more than TOUCH_MM, and into the band by more than TOUCH_MM; it then blocks its x range (less TOUCH_MM at
each end). Occupants and cargo do not block (they are the people getting in, or loads).

What opens (openable_parts): HIRAKU's reading of the parts' kinds and fastenings, no source:
- a net (kind in NET_KINDS) held only through quick-release clips: at least one part of kind net_clip touches it
  (box gap <= TOUCH_MM) and every node it is carried by is a node one of those clips is carried by. A net bolted to
  the frame on its own nodes (no clip, or a node of its own) does not open;
- a door (kind door) with at least one hinge (kind door_hinge) and one latch (kind door_latch) touching it;
- the clips, hinges and latches of an opening part go with it: they sit at the opening's edge and are not counted
  (an assumption: they are small, CLIP-sized).
Everything else is fixed: a bolted or welded part, a frame member.

The doorway (a template): an upper box Q93 wide from the seat top up to max(z_seat + Q95, z_floor + Q96), and a lower
box Q94 wide from the floor up to the seat top at the front end of the upper one (the legs go out ahead of the hip).
It slides along x within the seat's span (from the rear face to the front of the feet, less its width); blocked is
the least count of fixed bodies in it over the places (where the opening would best be).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .model import bodies

TOUCH_MM = 1.0  # a box reaching into a band or the passage by at most this does not block
HEIGHT_CAP_MM = 3000.0  # a clear height with nothing above is reported as this (assumption)
NET_KINDS = ("side_net",)  # as hiraku.solvers.shelf.load_path.NET_KINDS
CLIP_KIND = "net_clip"
DOOR_KIND = "door"
HINGE_KIND = "door_hinge"
LATCH_KIND = "door_latch"
STEP_KINDS = ("step",)  # parts a foot is put on between the ground and the floor
HANDHOLD_KINDS = ("grab_handle",)  # parts made to be held (members of a graspable section count too)
NOT_OBSTACLES = ("occupant", "cargo")


@dataclass
class Box:
    """An axis-aligned box: lo, hi (mm)."""

    id: str
    kind: str
    lo: np.ndarray
    hi: np.ndarray


@dataclass
class SeatSpace:
    seat: str
    side: float  # +1 left (enter from +y), -1 right
    y_out: float  # the seat box's outer face
    y_mid: float  # the seat box's centre
    x_back: float
    x_front: float
    z_seat: float
    z_floor: float
    person: str | None = None
    obstacles: list[Box] = field(default_factory=list)  # fixed bodies reaching into the passage


def aabb(design: dict) -> dict[str, Box]:
    """Every member and part (k4_geometry.bodies) as its axis-aligned bounding box."""
    out = {}
    for k, b in bodies(design).items():
        c = b.obb.corners()
        out[k] = Box(k, b.kind, c.min(axis=0), c.max(axis=0))
    return out


def gap(a: Box, b: Box) -> float:
    """The largest gap between two axis-aligned boxes along an axis (<= 0: they touch or overlap)."""
    return float(np.max(np.maximum(a.lo - b.hi, b.lo - a.hi)))


def openable_parts(design: dict, boxes: dict[str, Box] | None = None) -> dict[str, str]:
    """{part id: why it opens} for the nets held by clips, the doors with a hinge and a latch, and their fittings
    (see the module docstring)."""
    boxes = boxes or aabb(design)
    parts = {p["id"]: p for p in design.get("parts", [])}

    def touching(pid: str, kind: str) -> list[str]:
        me = boxes[f"part:{pid}"]
        return [q for q, p in parts.items() if p["kind"] == kind and q != pid and gap(me, boxes[f"part:{q}"]) <= TOUCH_MM]

    out: dict[str, str] = {}
    for pid, p in parts.items():
        if p["kind"] in NET_KINDS:
            clips = touching(pid, CLIP_KIND)
            held = {n for c in clips for n in (parts[c].get("carried_by") or [])}
            own = set(p.get("carried_by") or [])
            if clips and own <= held:
                out[pid] = f"net held by {len(clips)} quick-release clips ({CLIP_KIND})"
                out.update({c: f"clip of {pid}" for c in clips})
        elif p["kind"] == DOOR_KIND:
            hinges, latches = touching(pid, HINGE_KIND), touching(pid, LATCH_KIND)
            if hinges and latches:
                out[pid] = f"door on {len(hinges)} hinges ({HINGE_KIND}) with {len(latches)} latch ({LATCH_KIND})"
                out.update({f: f"fitting of {pid}" for f in hinges + latches})
    return out


def ground_z(design: dict) -> float:
    """The plane through the lowest tyre bottom (wheels), else the lowest box bottom."""
    wheels = [s for s in design.get("supports", []) if s.get("kind") == "wheel" and "radius_mm" in s]
    if wheels:
        return min(float(s["position_mm"][2]) - float(s["radius_mm"]) for s in wheels)
    return float(min(b.lo[2] for b in aabb(design).values()))


def seat_spaces(design: dict, boxes: dict[str, Box] | None = None, openable: dict[str, str] | None = None) -> list[SeatSpace]:
    """Each side seat's span, heights and the fixed bodies reaching into its passage (openable ones left out:
    `openable`, None for none)."""
    boxes = boxes or aabb(design)
    parts = design.get("parts", [])
    trunks = [p for p in parts if p["kind"] == "occupant" and "." not in p["id"]]
    out = []
    for s in (p for p in parts if p["kind"] == "seat"):
        b = boxes[f"part:{s['id']}"]
        yc = float(np.asarray(s["box"]["center_mm"], float)[1])
        if abs(yc) <= 1.0:
            continue  # a seat on the middle: no side of its own
        side = float(np.sign(yc))
        cx = float(np.asarray(s["box"]["center_mm"], float)[0])
        person = next((t["id"] for t in trunks
                       if b.lo[0] <= t["box"]["center_mm"][0] <= b.hi[0] and b.lo[1] <= t["box"]["center_mm"][1] <= b.hi[1]),
                      None)
        x_front = float(b.hi[0])
        if person:
            mine = [boxes[f"part:{p['id']}"] for p in parts if p["id"] == person or p["id"].startswith(person + ".")]
            x_front = max(x_front, max(float(m.hi[0]) for m in mine))
        floors = [x for x in (boxes[f"part:{p['id']}"] for p in parts if p["kind"] == "body")
                  if x.lo[0] <= cx <= x.hi[0] and x.lo[1] <= yc <= x.hi[1] and x.hi[2] <= b.lo[2] + TOUCH_MM]
        z_floor = max((float(f.hi[2]) for f in floors), default=float(b.lo[2]))
        y_out = float(b.hi[1] if side > 0 else b.lo[1])
        sp = SeatSpace(s["id"], side, y_out, yc, float(b.lo[0]), x_front, float(b.hi[2]), z_floor, person)
        for k, o in boxes.items():
            if o.kind in NOT_OBSTACLES or k == f"part:{s['id']}":
                continue
            if k.startswith("part:") and openable is not None and k.split(":", 1)[1] in openable:
                continue
            if (o.hi[1] > y_out + TOUCH_MM) if side > 0 else (o.lo[1] < y_out - TOUCH_MM):
                sp.obstacles.append(o)
        out.append(sp)
    return out


def in_band(o: Box, za: float, zb: float) -> bool:
    return o.hi[2] > za + TOUCH_MM and o.lo[2] < zb - TOUCH_MM


def crosses(o: Box, xa: float, xb: float) -> bool:
    return o.hi[0] > xa + TOUCH_MM and o.lo[0] < xb - TOUCH_MM


def clear_width(sp: SeatSpace, za: float, zb: float) -> tuple[float, tuple[float, float]]:
    """The longest stretch of the seat's span that nothing blocks in the band [za, zb]: (length, (x0, x1)), between
    the blocking boxes' faces."""
    cuts = sorted((float(o.lo[0]), float(o.hi[0])) for o in sp.obstacles if in_band(o, za, zb))
    best, at, x = 0.0, (sp.x_back, sp.x_back), sp.x_back
    for a, b in cuts + [(sp.x_front, sp.x_front)]:
        a = min(max(a, sp.x_back), sp.x_front)
        if a - x > best:
            best, at = a - x, (x, a)
        x = max(x, min(max(b, sp.x_back), sp.x_front))
    return best, at


def _places(sp: SeatSpace, width: float, edges: list[float]) -> list[float]:
    """Where an interval `width` long may start in the seat's span: its two ends and the given edges, kept inside."""
    hi = max(sp.x_back, sp.x_front - width)
    return sorted({min(max(x, sp.x_back), hi) for x in [sp.x_back, hi] + edges})


def clear_height(sp: SeatSpace, base: float, width: float, cap: float = HEIGHT_CAP_MM) -> tuple[float, float]:
    """The greatest clear height above `base` over an interval `width` long placed in the seat's span: (height,
    where it starts). A body in the way at `base` gives 0; nothing above gives `cap`."""
    above = [o for o in sp.obstacles if o.hi[2] > base + TOUCH_MM]
    best, at = -1.0, sp.x_back
    for x0 in _places(sp, width, [float(o.hi[0]) - TOUCH_MM for o in above] + [float(o.lo[0]) + TOUCH_MM - width
                                                                                  for o in above]):
        h = min([max(0.0, float(o.lo[2]) - base) for o in above if crosses(o, x0, x0 + width)] + [cap])
        if h > best:
            best, at = h, x0
    return best, at


def doorway(sp: SeatSpace, w_upper: float, w_lower: float, h_seat: float, h_floor: float) -> tuple[list[str], float]:
    """The fewest fixed bodies in the doorway template over its places in the seat's span: (their ids, where the
    upper box starts). Upper: [x0, x0 + w_upper] x [z_seat, max(z_seat + h_seat, z_floor + h_floor)]; lower:
    [x0 + w_upper - w_lower, x0 + w_upper] x [z_floor, z_seat]."""
    top = max(sp.z_seat + h_seat, sp.z_floor + h_floor)
    up = [o for o in sp.obstacles if in_band(o, sp.z_seat, top)]
    low = [o for o in sp.obstacles if in_band(o, sp.z_floor, sp.z_seat)]
    shift = w_upper - w_lower
    edges = ([float(o.hi[0]) - TOUCH_MM for o in up] + [float(o.lo[0]) + TOUCH_MM - w_upper for o in up]
             + [float(o.hi[0]) - TOUCH_MM - shift for o in low] + [float(o.lo[0]) + TOUCH_MM - w_upper for o in low])
    best: tuple[list[str], float] | None = None
    for x0 in _places(sp, w_upper, edges):
        ids = sorted({o.id for o in up if crosses(o, x0, x0 + w_upper)}
                     | {o.id for o in low if crosses(o, x0 + shift, x0 + w_upper)})
        if best is None or len(ids) < len(best[0]):
            best = (ids, x0)
    return best


def step_surfaces(design: dict, sp: SeatSpace, boxes: dict[str, Box] | None = None) -> list[tuple[str, float]]:
    """The surfaces a foot goes on from the ground to the floor beside the seat, lowest first: the tops of the steps
    (STEP_KINDS) on the seat's side whose x range meets the seat's span, then the floor (id "floor")."""
    boxes = boxes or aabb(design)
    steps = []
    for p in design.get("parts", []):
        if p["kind"] not in STEP_KINDS:
            continue
        b = boxes[f"part:{p['id']}"]
        if crosses(b, sp.x_back, sp.x_front) and np.sign(float(np.asarray(p["box"]["center_mm"], float)[1])) == sp.side \
                and b.hi[2] < sp.z_floor - TOUCH_MM:
            steps.append((p["id"], float(b.hi[2])))
    return sorted(steps, key=lambda t: t[1]) + [("floor", sp.z_floor)]


def grip_size(design: dict, body_id: str, box: Box) -> tuple[float, float] | None:
    """(smaller, larger) outer size across a member's section (round: d, d; rectangular: b, h), or of a hand-hold
    part's box across its length (its two smaller sides); None for anything else."""
    if body_id.startswith("member:"):
        m = next(x for x in design["members"] if x["id"] == body_id.split(":", 1)[1])
        dims = design["sections"][m["section"]].get("dims_mm", {})
        if "d" in dims:
            return float(dims["d"]), float(dims["d"])
        if "b" in dims and "h" in dims:
            return tuple(sorted((float(dims["b"]), float(dims["h"]))))
        return None
    if box.kind in HANDHOLD_KINDS:
        s = sorted(float(v) for v in box.hi - box.lo)
        return s[0], s[1]
    return None


def handholds(design: dict, sp: SeatSpace, surface: float, d_min: float, d_max: float, reach: float,
              boxes: dict[str, Box] | None = None) -> list[str]:
    """Members and hand-hold parts a hand can close round (outer size from d_min to d_max) with some of their box
    within the seat's span along x, outboard of the seat's centre line, and from `surface` (where the feet are) up
    to `surface + reach`."""
    boxes = boxes or aabb(design)
    out = []
    for k, b in boxes.items():
        if b.kind in NOT_OBSTACLES:
            continue
        g = grip_size(design, k, b)
        if g is None or g[0] < d_min - 1e-9 or g[1] > d_max + 1e-9:
            continue
        outboard = b.hi[1] > sp.y_mid if sp.side > 0 else b.lo[1] < sp.y_mid
        if outboard and crosses(b, sp.x_back, sp.x_front) and in_band(b, surface, surface + reach):
            out.append(k)
    return sorted(out)

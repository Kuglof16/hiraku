"""The pedals, placed from the driver's foot, and the occupant's body as parts.

    feet = body.place_pedals(lay, seat="seat_R", floor="floor_front")
    body.occupant_parts(lay, "occupant_R", seat="seat_R", floor="floor_front", grips=feet...)

Pedals: the large driver (AIST young adult male 95th percentile, k4_geometry.people) sits in the
seat with the heel on the floor and the knee at people.KNEE_ANGLE_DEG, or with the ball of the right
foot moved along x to a given ball_x (the knee then bends or opens); the accelerator's pad is
under the ball of the right foot, square to the sole (people.FOOT_ANGLE_DEG), its upper face at
the ball, but not further out than outboard_limit (the generator passes the frame rails' outer
face: the pedals stand in the footwell inside the front wheels' sweep, assumption); the brake's
pad is BRAKE_OFFSET_MM inboard of it at the same place along x and z. Their mass (PEDAL_KG each)
is taken out of the floor they stand on.

The body as parts (occupant_parts): the occupant's mass split by segment (SEGMENT_SHARE) into the
trunk (the part keeps the occupant's id), the head and the neck ("<occupant id>.head", ".neck") and,
on each side, the thigh, shank, foot, upper arm ("arm", the id the whole arm had), forearm and hand,
"<occupant id>.<segment>_<L or R>". Every part is kind occupant; hiraku.contract.loading counts the
parts sharing the id before the first "." as one person (person_of), and so do K4's checks
(k4_geometry.people.occupants). The segment shares are D. A. Winter's (below, not checked in this
repository); the generator writes the bodies (ladder_4w.BODY_PARTS).

The segments' sizes are the AIST items in k4_geometry.people.SIZES (people.SOURCE, people.BODY_SOURCE: the
AIST anthropometric database 1991-92, each item's source and the date it was read are there):
- trunk: from the seat back to twice the hip's distance ahead of it (2 x J9) along x, the bideltoid breadth D2
  across, from the seat top up to the cervicale (I4 Cervicale height, sitting);
- neck: a square prism as wide as the neck's diameter (F1 Neck circumference / pi), from the cervicale up to the
  head's bottom; the head: A1 Head length x A2 Head breadth x A36 Total head height, its top at the sitting height
  I1 (its bottom at I1 - A36, the gnathion). The neck's and the head's backs are at the trunk's back face
  (assumption: the cervicale and the occiput over the seat back);
- arms: the upper arm from the shoulder point (as before) to the elbow, C7 Upper arm length; the forearm from the
  elbow toward the grip, C8 Forearm length, to the wrist; the hand from the wrist on along the same line, L1 Hand
  length long, L10 Hand breadth x L16 Hand thickness across. The grip (the point the hand holds: on the steering
  wheel, or on the knee) is C12 Elbow-grip length from the elbow; the elbow is where the arm of those two lengths
  (C7, C12) folds between the shoulder and the grip (elbow_point), bent down (assumption; ELBOW_OUTWARD). The rods'
  radii are the girths F25 Upper arm circumference and F26 Forearm circumference over 2 pi (round sections,
  assumption). C7 is acromion to radiale and C12 from the olecranon's back: taking both from one elbow point is
  an assumption.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from hiraku.designgen.kit.links import link_box
from hiraku.designgen.kit.package import Item, Layout

Record = dict[str, Any]

# The pedals' mass, size and spacing and the shoulder's inset: HIRAKU's choices (assumptions, no source)
PEDAL_KG = 0.8  # one pedal with its arm (assumption)
PEDAL_PAD_MM = (100.0, 80.0, 20.0)  # pad along the sole, across, thick (assumption)
BRAKE_OFFSET_MM = 110.0  # the brake pad's middle this far inboard of the accelerator's (assumption)
ARM_RADIUS_MM = 45.0  # the shoulder point this far inside the bideltoid breadth's half (assumption, as before)
# Segment masses as shares of the body mass: D. A. Winter, Biomechanics and Motor Control of Human
# Movement, 4th ed., Wiley 2009, table 4.1 (after Dempster 1955): head and neck 0.081, trunk 0.497,
# upper arm 0.028, forearm 0.016, hand 0.006, thigh 0.100, leg 0.0465, foot 0.0145 (the "leg" is the shank
# here). Not checked: the book's table was not read in
# this repository (README, the table of values not checked). The table has the head and the neck as one segment: occupant_parts splits its share
# between the head's and the neck's boxes by their volumes (the same density: assumption).
SEGMENT_SHARE = {"trunk": 0.497, "head_neck": 0.081, "thigh": 0.100, "shank": 0.0465, "foot": 0.0145,
                 "arm": 0.028, "forearm": 0.016, "hand": 0.006}
SEGMENTS = ("thigh", "shank", "foot", "arm", "forearm", "hand")  # one on each side; "arm" is the upper arm
# The elbow bends off the shoulder-grip line toward (0, ELBOW_OUTWARD x outward, -1): down (assumption). Bent
# outward and down (ELBOW_OUTWARD 1) the elbows went 84.4 mm into the side net and 8.0 mm into the side bar
# (options side_guard) and the inboard arms into the person in the next seat, so they bend down only.
ELBOW_OUTWARD = 0.0


def person_of(part_id: str) -> str:
    """The occupant a body part belongs to: the id before the first dot."""
    return part_id.split(".", 1)[0]


def _seat_frame(lay: Layout, seat: str, floor: str) -> tuple[float, float, float, float]:
    lo, hi = lay.box(seat)
    return float(lo[0]), float(hi[2]), float(lay.part(seat)["box"]["center_mm"][1]), lay.top_of(floor)


def driver_pose(lay: Layout, seat: str, floor: str):
    """The large driver at people.KNEE_ANGLE_DEG with the heels on the floor."""
    from hiraku.solvers.k4_geometry import people

    back, top, y, fz = _seat_frame(lay, seat, floor)
    return people.seated(back, top, y, fz, people.BIG)


def driver_on_pedal(lay: Layout, seat: str, floor: str, ball_x: float | None = None):
    """The large driver with the heels on the floor: the knee at people.KNEE_ANGLE_DEG (ball_x None, or
    the ball of the foot of that pose already at ball_x), else the right ball of the foot moved along x to
    ball_x at the same height (the heel stays on the floor, the knee bends or opens: people.seated with
    right_ball, as K4's poses() sits the driver on a pedal)."""
    from hiraku.solvers.k4_geometry import people

    pose = driver_pose(lay, seat, floor)
    ball = pose.legs["R"].ball
    if ball_x is None or float(ball_x) == float(ball[0]):
        return pose
    back, top, y, fz = _seat_frame(lay, seat, floor)
    return people.seated(back, top, y, fz, people.BIG, right_ball=(float(ball_x), float(ball[2])))


def place_pedals(lay: Layout, seat: str, floor: str, outboard_limit: float | None = None,
                 ball_x: float | None = None) -> Record:
    """Put the accelerator and brake pedals in under the driver's right foot; returns
    {"ball": the right ball of the foot, "pose": the driver's pose}. outboard_limit: the pads' outer
    edge stays at most this far from the centre line (the footwell between the frame rails, inside
    the front wheels' sweep); a foot further out turns in to the pedal (the pad moves in along y only).
    ball_x: the accelerator pad's upper face middle (where the ball of the foot presses) at this x
    (driver_on_pedal); None: under the ball of the foot with the knee at people.KNEE_ANGLE_DEG."""
    from hiraku.solvers.k4_geometry import people

    pose = driver_on_pedal(lay, seat, floor, ball_x)
    ball = pose.legs["R"].ball
    a = np.radians(people.FOOT_ANGLE_DEG)
    along = np.array([np.cos(a), 0.0, np.sin(a)])
    normal = np.array([-np.sin(a), 0.0, np.cos(a)])
    t = PEDAL_PAD_MM[2]
    centre = ball - normal * t / 2.0
    if outboard_limit is not None and abs(centre[1]) + PEDAL_PAD_MM[1] / 2.0 > outboard_limit:
        centre[1] = np.sign(centre[1]) * (outboard_limit - PEDAL_PAD_MM[1] / 2.0)
    _, _, rpy = link_box(centre - along, centre + along, 1.0)
    inboard = 1.0 if ball[1] < 0 else -1.0  # toward the centre line
    for pid, label, kind, dy in (("pedal_accel", "アクセルのペダル", "accelerator_pedal", 0.0),
                                 ("pedal_brake", "ブレーキのペダル", "brake_pedal", inboard * BRAKE_OFFSET_MM)):
        p = lay.put(Item(pid, label, kind, PEDAL_PAD_MM, PEDAL_KG), centre[0], centre[1] + dy, centre[2])
        p["box"]["rpy_deg"] = rpy
    fl = lay.part(floor)
    if fl["mass_kg"] <= 2 * PEDAL_KG:
        raise ValueError(f"{floor} is lighter than the pedals taken out of it")
    fl["mass_kg"] = float(fl["mass_kg"]) - 2 * PEDAL_KG
    return {"ball": ball, "pose": pose}


def elbow_point(shoulder, grip, upper: float, lower: float, outward: float) -> tuple[np.ndarray, bool]:
    """The elbow of an arm from `shoulder` to `grip` whose upper arm is `upper` long and whose elbow is `lower`
    from the grip: off the shoulder-grip line toward (0, outward, -1) (outward > 0 toward +y, < 0 toward -y, 0: down,
    the part of it square to the line), and whether
    the grip is within reach. Beyond reach the arm is straight toward the grip (the elbow `upper` along the
    line); nearer than |upper - lower| the arm folds as at that distance."""
    s, g = np.asarray(shoulder, float), np.asarray(grip, float)
    v = g - s
    d = float(np.linalg.norm(v))
    if d <= 0.0:
        raise ValueError("the grip is at the shoulder")
    u = v / d
    reached = d <= upper + lower
    dd = min(max(d, abs(upper - lower)), upper + lower)
    a = (upper * upper - lower * lower + dd * dd) / (2.0 * dd)
    h = math.sqrt(max(upper * upper - a * a, 0.0))
    w = np.array([0.0, outward, -1.0])
    n = w - float(w @ u) * u
    if float(np.linalg.norm(n)) < 1e-9:  # the line runs along the bend: bend down
        n = np.array([0.0, 0.0, -1.0]) - u[2] * u
    n /= float(np.linalg.norm(n))
    return s + a * u + h * n, reached


def _bar(a, b, across_y: float, across_z: float) -> tuple[list[float], list[float], list[float] | None]:
    """(centre, size, rpy_deg or None) of a box from a to b (its length), across_y wide (level, square to the
    line) and across_z thick."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    centre, _, rpy = link_box(a, b, 1.0)
    length = float(np.linalg.norm(b - a))
    if rpy is not None:
        return centre, [length, across_y, across_z], rpy
    k = int(np.argmax(np.abs(b - a)))
    return centre, [[length, across_y, across_z], [across_y, length, across_z], [across_z, across_y, length]][k], None


def occupant_parts(lay: Layout, occupant: str, pose, grips: list | None = None) -> list[Record]:
    """Replace the occupant's mass box by its body (module docstring): the trunk (same id, on the seat between
    the seat back and the hip's front, up to the cervicale), the neck and the head above it, and the limbs along
    the pose (k4_geometry.people.Pose); the arms from the shoulder to `grips` (two points, the driver's hands on
    the wheel) or to the knees' tops, the elbow folded between."""
    from hiraku.solvers.k4_geometry import people

    o = lay.part(occupant)
    mass = float(o["mass_kg"])
    s = people.SIZES[pose.size]
    carried = o.get("carried_by")
    lay.parts.remove(o)
    out = []

    def put(pid: str, label: str, centre, size, rpy=None) -> Record:
        p = lay.put(Item(pid, f"{o['name']}: {label}" if pid != occupant else o["name"], "occupant", tuple(size), 0.0),
                    *[float(v) for v in centre], carried_by=carried)
        if rpy is not None:
            p["box"]["rpy_deg"] = rpy
        out.append(p)
        return p

    hip = pose.legs["L"].hip
    back = float(pose.shoulder[0] - s["back_shoulder"])
    seat_top = float(hip[2] - s["trochanter_height"])
    depth = float(hip[0] - back) * 2.0
    y = float(pose.shoulder[1])
    width = people.AIST["shoulder_breadth_m95"]
    neck_z0, head_z0 = seat_top + s["cervicale_height"], seat_top + s["sitting_height"] - s["head_height"]
    if head_z0 <= neck_z0:
        raise ValueError(f"the head's bottom ({head_z0:g} mm) is not above the cervicale ({neck_z0:g} mm)")
    neck_w = s["neck_circumference"] / math.pi
    put(occupant, "trunk", (back + depth / 2.0, y, seat_top + s["cervicale_height"] / 2.0),
        (depth, width, s["cervicale_height"]))
    put(f"{occupant}.neck", "neck", (back + neck_w / 2.0, y, (neck_z0 + head_z0) / 2.0), (neck_w, neck_w, head_z0 - neck_z0))
    put(f"{occupant}.head", "head", (back + s["head_length"] / 2.0, y, head_z0 + s["head_height"] / 2.0),
        (s["head_length"], s["head_breadth"], s["head_height"]))
    r_upper = s["upper_arm_circumference"] / (2.0 * math.pi)
    r_fore = s["forearm_circumference"] / (2.0 * math.pi)
    hands = grips or [pose.legs[side].knee + np.array([0.0, 0.0, pose.radii["thigh"]]) for side in ("L", "R")]
    for side, hand in zip(("L", "R"), sorted(hands, key=lambda p: -p[1])):
        g = pose.legs[side]
        sign = 1.0 if side == "L" else -1.0
        sh = pose.shoulder + np.array([0.0, sign * (width / 2.0 - ARM_RADIUS_MM), 0.0])
        grip = np.asarray(hand, float)
        elbow, _ = elbow_point(sh, grip, s["upper_arm"], s["elbow_grip"], sign * ELBOW_OUTWARD)
        u = (grip - elbow) / float(np.linalg.norm(grip - elbow))
        wrist = elbow + u * s["forearm_length"]
        for seg, a, b, r in (("thigh", g.hip, g.knee, pose.radii["thigh"]), ("shank", g.knee, g.ankle, pose.radii["shank"]),
                             ("arm", sh, elbow, r_upper), ("forearm", elbow, wrist, r_fore)):
            put(f"{occupant}.{seg}_{side}", seg, *link_box(a, b, r))
        put(f"{occupant}.hand_{side}", "hand", *_bar(wrist, wrist + u * s["hand_length"], s["hand_breadth"], s["hand_thickness"]))
        # the foot: a box along the sole from the heel to the toe, as high as the ankle, on the sole
        u = (g.toe - g.heel) / float(np.linalg.norm(g.toe - g.heel))
        up = np.array([-u[2], 0.0, u[0]])
        c = (g.heel + g.toe) / 2.0 + up * s["malleolus_height"] / 2.0
        _, _, rpy = link_box(g.heel, g.toe, 1.0)
        put(f"{occupant}.foot_{side}", "foot", c.tolist(), (s["foot_length"], s["foot_breadth"], s["malleolus_height"]), rpy)
    vol = {k: float(np.prod(lay.part(f"{occupant}.{k}")["box"]["size_mm"])) for k in ("head", "neck")}
    shares = {}
    for p in out:
        seg = "trunk" if p["id"] == occupant else p["id"].split(".", 1)[1]
        if seg in vol:
            shares[p["id"]] = SEGMENT_SHARE["head_neck"] * vol[seg] / (vol["head"] + vol["neck"])
        else:
            shares[p["id"]] = SEGMENT_SHARE[seg if seg == "trunk" else seg[:-2]]
    total = sum(shares.values())
    for p in out:
        p["mass_kg"] = mass * shares[p["id"]] / total
    out[0]["mass_kg"] = mass - sum(p["mass_kg"] for p in out[1:])  # the sum is the occupant's mass to the last digit
    return out

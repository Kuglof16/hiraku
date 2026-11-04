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
trunk with the head (the part keeps the occupant's id) and thighs, shanks, feet and arms, each
"<occupant id>.<segment>_<L or R>". Every part is kind occupant; hiraku.contract.loading counts the
parts sharing the id before the first "." as one person (person_of), and so do K4's checks
(k4_geometry.people.occupants). The segment shares are D. A. Winter's (below, not checked in this
repository); the generator writes the bodies (ladder_4w.BODY_PARTS).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from hiraku.designgen.kit.links import link_box
from hiraku.designgen.kit.package import Item, Layout

Record = dict[str, Any]

PEDAL_KG = 0.8  # one pedal with its arm (assumption)
PEDAL_PAD_MM = (100.0, 80.0, 20.0)  # pad along the sole, across, thick (assumption)
BRAKE_OFFSET_MM = 110.0  # the brake pad's middle this far inboard of the accelerator's (assumption)
ARM_RADIUS_MM = 45.0  # the arm as a rod (assumption, no AIST item read for it)
# Segment masses as shares of the body mass: D. A. Winter, Biomechanics and Motor Control of Human
# Movement, 4th ed., Wiley 2009, table 4.1 (after Dempster 1955): head and neck 0.081, trunk 0.497,
# upper arm 0.028, forearm 0.016, hand 0.006, thigh 0.100, leg 0.0465, foot 0.0145. Not checked
# in this repository.
SEGMENT_SHARE = {"trunk": 0.081 + 0.497, "thigh": 0.100, "shank": 0.0465, "foot": 0.0145, "arm": 0.028 + 0.016 + 0.006}
SEGMENTS = ("thigh", "shank", "foot", "arm")


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


def occupant_parts(lay: Layout, occupant: str, pose, grips: list | None = None) -> list[Record]:
    """Replace the occupant's mass box by its body: the trunk with the head (same id, on the seat
    between the seat back and the hip's front, as tall as the sitting height), and the limbs along
    the pose (k4_geometry.people.Pose); the arms from the shoulder to `grips` (two points, the
    driver's hands on the wheel) or down to the knees."""
    from hiraku.solvers.k4_geometry import people

    o = lay.part(occupant)
    mass = float(o["mass_kg"])
    s = people.SIZES[pose.size]
    carried = o.get("carried_by")
    lay.parts.remove(o)
    out = []
    hip = pose.legs["L"].hip
    back = float(pose.shoulder[0] - s["back_shoulder"])
    seat_top = float(hip[2] - s["trochanter_height"])
    depth = float(hip[0] - back) * 2.0
    trunk = lay.put(Item(occupant, o["name"], "occupant", (depth, people.AIST["shoulder_breadth_m95"], s["sitting_height"]),
                         0.0), back + depth / 2.0, float(pose.shoulder[1]), seat_top + s["sitting_height"] / 2.0,
                    carried_by=carried)
    out.append(trunk)
    hands = grips or [pose.legs[side].knee + np.array([0.0, 0.0, pose.radii["thigh"]]) for side in ("L", "R")]
    for side, hand in zip(("L", "R"), sorted(hands, key=lambda p: -p[1])):
        g = pose.legs[side]
        sign = 1.0 if side == "L" else -1.0
        sh = pose.shoulder + np.array([0.0, sign * (people.AIST["shoulder_breadth_m95"] / 2.0 - ARM_RADIUS_MM), 0.0])
        for seg, a, b, r in (("thigh", g.hip, g.knee, pose.radii["thigh"]), ("shank", g.knee, g.ankle, pose.radii["shank"]),
                             ("arm", sh, np.asarray(hand, float), ARM_RADIUS_MM)):
            c, size, rpy = link_box(a, b, r)
            p = lay.put(Item(f"{occupant}.{seg}_{side}", f"{o['name']}: {seg}", "occupant", tuple(size), 0.0), *c,
                        carried_by=carried)
            if rpy is not None:
                p["box"]["rpy_deg"] = rpy
            out.append(p)
        # the foot: a box along the sole from the heel to the toe, as high as the ankle, on the sole
        u = (g.toe - g.heel) / float(np.linalg.norm(g.toe - g.heel))
        up = np.array([-u[2], 0.0, u[0]])
        c = (g.heel + g.toe) / 2.0 + up * s["malleolus_height"] / 2.0
        _, _, rpy = link_box(g.heel, g.toe, 1.0)
        p = lay.put(Item(f"{occupant}.foot_{side}", f"{o['name']}: foot", "occupant",
                         (s["foot_length"], s["foot_breadth"], s["malleolus_height"]), 0.0), *c.tolist(), carried_by=carried)
        if rpy is not None:
            p["box"]["rpy_deg"] = rpy
        out.append(p)
    shares = {p["id"]: SEGMENT_SHARE["trunk"] if p["id"] == occupant else SEGMENT_SHARE[p["id"].split(".")[1][:-2]]
              for p in out}
    total = sum(shares.values())
    for p in out:
        p["mass_kg"] = mass * shares[p["id"]] / total
    out[0]["mass_kg"] = mass - sum(p["mass_kg"] for p in out[1:])  # the sum is the occupant's mass to the last digit
    return out

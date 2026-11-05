"""Occupant fit against body-size statistics (H3), and the seated body: legs, feet and
arms placed against the seat, the floor, the pedals and the steering wheel.

Source of the values (mm): percentiles of the young adults (18-29 years) in
the AIST anthropometric database 1991-92 (Makiko Kouchi and Masaaki Mochimaru,
2005: AIST Anthropometric Database, National Institute of Advanced Industrial
Science and Technology, H16PRO 287), read from the item tables at
https://www.airc.aist.go.jp/dhrt/91-92/data/list.html on 2026-11-03 (item ID
and name as in that list). Only statistics are used (no individual data).

The seated body (seated()) is a chain of straight links in the side view of each
leg, from the item values: the hip joint at the buttock-trochanterion length
(J9) ahead of the seat back and the trochanterion height (I11) above the seat;
the thigh from the hip to the knee joint, J7 - J9 long (the knee joint is the
lateral condyle; the thigh is level when J7 and J9 are measured); the shank
from the knee joint to the ankle, I13 - M4 long (knee joint height over the
floor less the ankle's height over the sole); the foot from the heel, the ankle
M24 along the sole and M4 above it, the ball of the foot M20 along the sole,
the toe M15. The legs are side by side, a thigh breadth (D15) apart. The links
being straight lines through the joints is an assumption.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

AIST = {
    "sitting_height_m95": 985.2,  # source: I1 Sitting height, male 95th percentile
    "sitting_height_f5": 824.1,  # source: I1 Sitting height, female 5th percentile
    "shoulder_breadth_m95": 495.3,  # source: D2 Bideltoid breadth, male 95th percentile
    "hip_breadth_seated_f95": 391.0,  # source: J2 Hip breadth, sitting, feet supported, female 95th percentile
    "buttock_knee_m95": 616.4,  # source: J5 Buttock-knee length, sitting, male 95th percentile
}
SOURCE = ("AIST anthropometric database 1991-92 (Kouchi and Mochimaru 2005, AIST H16PRO 287), young adults; items I1, D2, "
          "J2, J5, J7, J9, I3, I11, I13, C3, C7, C12, C16, D15, D17, M4, M15, M16, M20, M24 read from "
          "https://www.airc.aist.go.jp/dhrt/91-92/data/list.html on 2026-11-03")
# The items for the body's segments (head, neck, forearm, hand, the arms' girths), same database and percentiles,
# read from the item tables (gif/<ID>.gif) linked from the same list on 2026-11-04.
BODY_SOURCE = ("AIST anthropometric database 1991-92 (Kouchi and Mochimaru 2005, AIST H16PRO 287), young adults; items A1, "
               "A2, A36, C8, F1, F25, F26, I4, L1, L10, L16 read from https://www.airc.aist.go.jp/dhrt/91-92/data/list.html "
               "on 2026-11-04")

# Body sizes for the seated body: young adult male 95th percentile (the large person: room,
# clearance) and young adult female 5th percentile (the small person: reach). Same source as AIST.
SIZES: dict[str, dict[str, float]] = {
    "m95": {
        "buttock_trochanter": 146.0,  # source: J9 Buttock-trochanterion length, sitting
        "trochanter_height": 82.0,  # source: I11 Trochanterion height, sitting (over the seat)
        "buttock_condyle": 570.3,  # source: J7 Buttock-lateral condyle length, sitting
        "buttock_knee": 616.4,  # source: J5 Buttock-knee length, sitting
        "knee_joint_height": 501.4,  # source: I13 Knee joint height, sitting (over the floor)
        "malleolus_height": 81.6,  # source: M4 Lateral malleolus height
        "heel_malleolus": 62.1,  # source: M24 Heel to lateral malleolus
        "instep_length": 199.5,  # source: M20 Instep length (heel to the ball of the foot)
        "foot_length": 272.5,  # source: M15 Foot length, direct
        "foot_breadth": 107.0,  # source: M16 Foot breadth, direct
        "thigh_breadth": 184.0,  # source: D15 Thigh breadth
        "calf_breadth": 126.0,  # source: D17 Maximum lower leg breadth
        "shoulder_height": 637.5,  # source: I3 Acromiale height, sitting (over the seat)
        "back_shoulder": 94.0,  # source: C16 Back to acromiale, arms forward
        "grip_reach": 763.0,  # source: C3 Grip reach from back
        "upper_arm": 336.4,  # source: C7 Upper arm length
        "elbow_grip": 358.0,  # source: C12 Elbow-grip length
        "sitting_height": 985.2,  # source: I1 Sitting height
        # the body's segments (designgen kit/body.py), read 2026-11-04 (BODY_SOURCE)
        "head_length": 201.0,  # source: A1 Head length
        "head_breadth": 171.0,  # source: A2 Head breadth
        "head_height": 251.5,  # source: A36 Total head height (gnathion to vertex)
        "cervicale_height": 714.0,  # source: I4 Cervicale height, sitting (over the seat)
        "neck_circumference": 386.2,  # source: F1 Neck circumference
        "forearm_length": 271.2,  # source: C8 Forearm length (radiale to stylion)
        "upper_arm_circumference": 324.0,  # source: F25 Upper arm circumference
        "forearm_circumference": 287.0,  # source: F26 Forearm circumference (maximum)
        "hand_length": 204.2,  # source: L1 Hand length, tip to wrist crease
        "hand_breadth": 88.3,  # source: L10 Hand breadth, diagonal (metacarpale radiale to ulnare)
        "hand_thickness": 32.0,  # source: L16 Hand thickness
    },
    "f5": {
        "buttock_trochanter": 65.9,
        "trochanter_height": 58.0,
        "buttock_condyle": 470.1,
        "buttock_knee": 511.0,
        "knee_joint_height": 399.1,
        "malleolus_height": 56.0,
        "heel_malleolus": 41.0,
        "instep_length": 159.5,
        "foot_length": 216.1,
        "foot_breadth": 84.8,
        "thigh_breadth": 146.0,
        "calf_breadth": 92.1,
        "shoulder_height": 519.0,
        "back_shoulder": 65.0,
        "grip_reach": 606.0,
        "upper_arm": 265.0,
        "elbow_grip": 283.0,
        "sitting_height": 824.1,
        "head_length": 170.0,
        "head_breadth": 145.0,
        "head_height": 218.0,
        "cervicale_height": 586.0,
        "neck_circumference": 287.0,
        "forearm_length": 211.0,
        "upper_arm_circumference": 223.1,
        "forearm_circumference": 212.0,
        "hand_length": 163.0,
        "hand_breadth": 68.0,
        "hand_thickness": 22.0,
    },
}
BIG, SMALL = "m95", "f5"
# Posture (assumptions, no source): the sole tilted up toward the toe, resting on the pedals and the
# toe board; the knee angle (between thigh and shank) of a person sitting with the feet forward on
# the floor, used to place the pedals and the feet of a seat without pedals. pedal_reach measures
# against the straight leg (180 deg, the links' geometry): whether the foot reaches the pedal at all;
# how comfortable the knee is, is knee_angle_room's (KNEE_COMFORT_DEG).
FOOT_ANGLE_DEG = 20.0
KNEE_ANGLE_DEG = 100.0
KNEE_MAX_DEG = 180.0
# The comfortable range of the knee angle (between thigh and shank) in the driving posture: 99-138 deg,
# Porter and Gyi 1998 (Int. J. Vehicle Design 19(3) 255-266; 55 drivers' preferred postures, goniometer on the
# right side), as Schmidt et al. 2014 reproduce it (Applied Ergonomics 45, 247-260, Table 1, p. 251). Read in
# that review; the original paper was not read (not verified). The review does not draw the angle; that it is
# the inner angle (180 = straight leg), as KNEE_ANGLE_DEG here, is taken from its values (assumption).
KNEE_COMFORT_DEG = (99.0, 138.0)
KNEE_COMFORT_SOURCE = ("knee angle 99-138 deg: Porter and Gyi 1998 (IJVD 19(3) 255-266), read in Schmidt et al. 2014, "
                       "Applied Ergonomics 45 247-260, Table 1 p. 251; the original not verified")
# The driver's seat slides along x (design variable SLIDE_VARIABLE, mm, the whole travel; the seat's box at its
# middle): each body size sits at its own best place in it (poses, best_offset). SLIDE_LEVELS levels across the
# travel, then a golden-section search to SLIDE_RESOLUTION_MM (design choices of this search).
SLIDE_VARIABLE = "seat_slide"
SLIDE_LEVELS = 13
SLIDE_RESOLUTION_MM = 0.5
PEDAL_KINDS = ("pedal", "accelerator_pedal", "brake_pedal")
WHEEL_KINDS = ("steering", "steering_wheel")  # the steering wheel (designs with pedals only, see body_fit)
# Bodies the legs and feet are not checked against: the person's own seat, the occupants' mass
# boxes (the body itself) and, for the feet, the floor and pedals they rest on.
LEG_SKIP_KINDS = ("seat", "occupant")
FOOT_SKIP_KINDS = ("seat", "occupant", *PEDAL_KINDS)


def _box(p):
    c = np.asarray(p["box"]["center_mm"], float)
    h = np.asarray(p["box"]["size_mm"], float) / 2.0
    return c - h, c + h


def _extent(p):
    """(lower, upper corner) of a part's box, turned by its rpy_deg when it has one."""
    from .obb import from_box

    o = from_box(p["box"]["center_mm"], p["box"]["size_mm"], p["box"].get("rpy_deg"))
    c = o.corners()
    return c.min(axis=0), c.max(axis=0)


# ---------------------------------------------------------------------------- the seated body
@dataclass
class Leg:
    hip: np.ndarray
    knee: np.ndarray
    ankle: np.ndarray
    heel: np.ndarray
    ball: np.ndarray
    toe: np.ndarray
    knee_angle: float  # deg, between thigh and shank
    reached: bool = True  # False: the foot's target is beyond the straight leg (the leg is stretched toward it)


@dataclass
class Pose:
    size: str
    legs: dict[str, Leg]  # "L" (+y) and "R" (-y)
    shoulder: np.ndarray  # the shoulder point in the person's middle plane (the arms reach from it)
    arm_reach: float  # mm from the shoulder to the grip, the arm straight forward (C3 - C16)
    radii: dict[str, float] = field(default_factory=dict)


def _rot(a_deg: float, v) -> np.ndarray:
    """The side-view vector v = (along x, along z) turned up by a (toe up), as (dx, dz)."""
    a = math.radians(a_deg)
    return np.array([v[0] * math.cos(a) - v[1] * math.sin(a), v[0] * math.sin(a) + v[1] * math.cos(a)])


def _links(s: dict) -> tuple[float, float]:
    return s["buttock_condyle"] - s["buttock_trochanter"], s["knee_joint_height"] - s["malleolus_height"]


def hip_point(seat_back_x: float, seat_top_z: float, size: str) -> np.ndarray:
    s = SIZES[size]
    return np.array([seat_back_x + s["buttock_trochanter"], seat_top_z + s["trochanter_height"]])


def _ankle_from_heel(size: str, foot_angle: float) -> np.ndarray:
    s = SIZES[size]
    return _rot(foot_angle, (s["heel_malleolus"], s["malleolus_height"]))


def _knee(hip: np.ndarray, ankle: np.ndarray, l1: float, l2: float) -> tuple[np.ndarray, float, bool]:
    """Knee point (above the hip-ankle line), knee angle (deg) and whether the ankle is within reach."""
    v = ankle - hip
    d = float(np.linalg.norm(v))
    reached = d <= l1 + l2
    d = min(max(d, abs(l1 - l2) + 1e-9), l1 + l2)
    base = math.atan2(v[1], v[0])
    alpha = math.acos(max(-1.0, min(1.0, (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d))))
    knee = hip + l1 * np.array([math.cos(base + alpha), math.sin(base + alpha)])
    theta = math.degrees(math.acos(max(-1.0, min(1.0, (l1 * l1 + l2 * l2 - d * d) / (2 * l1 * l2)))))
    return knee, theta, reached


def leg_at_knee_angle(hip: np.ndarray, floor_z: float, size: str, knee_angle: float = KNEE_ANGLE_DEG,
                      foot_angle: float = FOOT_ANGLE_DEG) -> tuple[np.ndarray, ...]:
    """Side view (x, z) of a leg with the heel on the floor and the knee at knee_angle:
    (knee, ankle, heel, ball, toe)."""
    s = SIZES[size]
    l1, l2 = _links(s)
    off = _ankle_from_heel(size, foot_angle)
    az = floor_z + off[1]
    d = math.sqrt(l1 * l1 + l2 * l2 - 2 * l1 * l2 * math.cos(math.radians(knee_angle)))
    dz = hip[1] - az
    if abs(dz) > d:
        raise ValueError(f"the ankle cannot be {dz:.0f} mm below the hip with the knee at {knee_angle:g} deg")
    ankle = np.array([hip[0] + math.sqrt(d * d - dz * dz), az])
    heel = ankle - off
    return _foot(hip, heel, size, foot_angle)


def _foot(hip, heel, size, foot_angle):
    s = SIZES[size]
    l1, l2 = _links(s)
    ankle = heel + _ankle_from_heel(size, foot_angle)
    knee, _, _ = _knee(hip, ankle, l1, l2)
    return knee, ankle, heel, heel + _rot(foot_angle, (s["instep_length"], 0.0)), heel + _rot(foot_angle, (s["foot_length"], 0.0))


def leg_to_ball(hip: np.ndarray, ball: np.ndarray, size: str, foot_angle: float = FOOT_ANGLE_DEG) -> Leg:
    """A leg (side view, x z) whose ball of the foot is at `ball`."""
    s = SIZES[size]
    l1, l2 = _links(s)
    heel = ball - _rot(foot_angle, (s["instep_length"], 0.0))
    ankle = heel + _ankle_from_heel(size, foot_angle)
    knee, theta, reached = _knee(hip, ankle, l1, l2)
    toe = heel + _rot(foot_angle, (s["foot_length"], 0.0))
    return Leg(hip, knee, ankle, heel, ball, toe, theta, reached)


def leg_spacing(size: str) -> float:
    """Half the distance between the legs' middle lines: thighs side by side (D15)."""
    return SIZES[size]["thigh_breadth"] / 2.0


def _3d(p2, y: float) -> np.ndarray:
    return np.array([float(p2[0]), y, float(p2[1])])


def seated(seat_back_x: float, seat_top_z: float, y: float, floor_z: float, size: str = BIG,
           right_ball=None, knee_angle: float = KNEE_ANGLE_DEG, foot_angle: float = FOOT_ANGLE_DEG) -> Pose:
    """The seated body of one person, centred at y: both legs with the heel on the floor and the knee
    at knee_angle, or the right leg's ball of the foot on right_ball (x, z) when given (a pedal);
    the left foot then beside it at the same x. 3D points (mm)."""
    s = SIZES[size]
    hip = hip_point(seat_back_x, seat_top_z, size)
    h = leg_spacing(size)
    legs = {}
    if right_ball is None:
        knee, ankle, heel, ball, toe = leg_at_knee_angle(hip, floor_z, size, knee_angle, foot_angle)
        free = Leg(hip, knee, ankle, heel, ball, toe, knee_angle)
        sides = {"L": free, "R": free}
    else:
        r = leg_to_ball(hip, np.asarray(right_ball, float), size, foot_angle)
        # the left foot rests on the floor level with the right heel: the same leg, its heel down on the floor
        drop = r.heel[1] - floor_z
        left = leg_to_ball(hip, r.ball - np.array([0.0, drop]), size, foot_angle)
        sides = {"L": left, "R": r}
    for side, sign in (("L", 1.0), ("R", -1.0)):
        g = sides[side]
        yy = y + sign * h
        legs[side] = Leg(*(_3d(p, yy) for p in (g.hip, g.knee, g.ankle, g.heel, g.ball, g.toe)), g.knee_angle, g.reached)
    shoulder = np.array([seat_back_x + s["back_shoulder"], y, seat_top_z + s["shoulder_height"]])
    radii = {"thigh": s["thigh_breadth"] / 2.0, "shank": s["calf_breadth"] / 2.0}
    return Pose(size, legs, shoulder, s["grip_reach"] - s["back_shoulder"], radii)


def leg_bodies(pose: Pose, owner: str) -> dict:
    """K4 bodies of the legs: thighs and shanks as capsules, feet as boxes along the sole."""
    from .capsule import from_points
    from .model import Body
    from .obb import OBB

    s = SIZES[pose.size]
    out = {}
    for side, g in pose.legs.items():
        for link, a, b, r in (("thigh", g.hip, g.knee, pose.radii["thigh"]), ("shank", g.knee, g.ankle, pose.radii["shank"])):
            c = from_points(a, b, r, f"{owner}.{link}_{side}")
            out[f"leg:{owner}.{link}_{side}"] = Body(f"leg:{owner}.{link}_{side}", _link_box(a, b, r), "leg", capsule=c)
        u = (g.toe - g.heel) / float(np.linalg.norm(g.toe - g.heel))
        up = np.cross(np.cross(u, [0.0, 0.0, 1.0]), u)
        up /= float(np.linalg.norm(up))
        side_ax = np.cross(up, u)
        hz = s["malleolus_height"] / 2.0
        centre = (g.heel + g.toe) / 2.0 + up * hz
        out[f"leg:{owner}.foot_{side}"] = Body(f"leg:{owner}.foot_{side}",
                                               OBB(centre, np.array([u, side_ax, up]),
                                                   np.array([s["foot_length"] / 2.0, s["foot_breadth"] / 2.0, hz]),
                                                   f"{owner}.foot_{side}"), "leg")
    return out


def _link_box(a, b, r):
    from .obb import from_member

    o = from_member(a, b, 2 * r, 2 * r)
    o.half[0] += r
    return o


# ---------------------------------------------------------------------------- checks
def occupants(design: dict) -> list[dict]:
    """One part per person, in the parts' order: the occupant parts sharing the id before the first "."
    are one person (hiraku.contract.loading.person_of); the person's part is the one whose id is that
    (the trunk of a body split into segments), else the person's first part."""
    from hiraku.contract.loading import person_of

    out: dict[str, dict] = {}
    for p in design.get("parts", []):
        if p["kind"] != "occupant":
            continue
        who = person_of(p)
        if who not in out or p["id"] == who:
            out[who] = p
    return list(out.values())


def fit(design: dict) -> dict:
    """Margins (mm, negative = does not fit) for the occupants and seats in the design."""
    parts = design.get("parts", [])
    occ = occupants(design)
    seats = [p for p in parts if p["kind"] == "seat"]
    out: dict[str, tuple[float, str]] = {}
    if not occ:
        return out
    ys = [p["box"]["center_mm"][1] + s * p["box"]["size_mm"][1] / 2 for p in parts for s in (-1, 1)]
    ys += [n["xyz_mm"][1] for n in design.get("nodes", [])]
    width = max(ys) - min(ys)
    out["shoulder_room"] = (width - len(occ) * AIST["shoulder_breadth_m95"], "vehicle")
    for seat in seats:
        out[f"hip_room.{seat['id']}"] = (seat["box"]["size_mm"][1] - AIST["hip_breadth_seated_f95"], seat["id"])
    for o in occ:
        lo, hi = _box(o)
        seat = _seat_of(o, seats)
        seat_top = _box(seat)[1][2] if seat else lo[2]
        head = seat_top + AIST["sitting_height_m95"]
        above = [p for p in parts if p is not o and p["kind"] not in ("occupant",)
                 and _box(p)[0][2] >= seat_top and _overlaps_xy(p, o)]
        if above:
            roof = min(_box(p)[0][2] for p in above)
            out[f"head_clearance.{o['id']}"] = (roof - head, o["id"])
    ps = poses(design) if any(p["kind"] in PEDAL_KINDS for p in parts) or seat_slide(design) > 0.0 else None
    for oid, (length, obstacle) in knee_space(design, ps).items():
        out[f"knee_room.{oid}"] = (length - AIST["buttock_knee_m95"], obstacle)
    out.update(body_fit(design, ps))
    return out


def driver_pedal(design: dict, occupant: dict) -> dict | None:
    """The pedal under the driver's right foot: of the pedals, the one nearest in y to the
    occupant's right leg (the first occupant drives). None without pedals."""
    pedals = [p for p in design.get("parts", []) if p["kind"] in PEDAL_KINDS]
    if not pedals:
        return None
    y = float(occupant["box"]["center_mm"][1]) - leg_spacing(BIG)
    return min(pedals, key=lambda p: abs(float(p["box"]["center_mm"][1]) - y))


def pedal_face(pedal: dict) -> np.ndarray:
    """The middle of the pedal pad's upper face (where the ball of the foot presses), 3D."""
    from .obb import from_box

    o = from_box(pedal["box"]["center_mm"], pedal["box"]["size_mm"], pedal["box"].get("rpy_deg"))
    k = int(np.argmax(np.abs(o.axes[:, 2])))
    n = o.axes[k] * np.sign(o.axes[k][2])
    return o.center + n * o.half[k]


def floor_under(design: dict, x: float, y: float, below: float) -> dict | None:
    """The highest part of kind body whose box covers (x, y) in plan and whose top is below `below`."""
    best = None
    for p in design.get("parts", []):
        if p["kind"] != "body":
            continue
        lo, hi = _extent(p)
        if lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1] and hi[2] < below and (best is None or hi[2] > _extent(best)[1][2]):
            best = p
    return best


def feet_floor(design: dict, seat_back_x: float, seat_top_z: float, y: float) -> dict | None:
    """The floor the big person's heels rest on, sitting at KNEE_ANGLE_DEG: of the body parts below the
    seat top, the highest one under the heel."""
    hip = hip_point(seat_back_x, seat_top_z, BIG)
    cands = sorted((p for p in design.get("parts", []) if p["kind"] == "body" and _extent(p)[1][2] < seat_top_z),
                   key=lambda p: -_extent(p)[1][2])
    for p in cands:
        lo, hi = _extent(p)
        try:
            heel = leg_at_knee_angle(hip, float(hi[2]), BIG)[2]
        except ValueError:
            continue
        if lo[0] <= heel[0] <= hi[0] and lo[1] <= y <= hi[1]:
            return p
    return None


def seat_slide(design: dict) -> float:
    """The driver's seat's fore-and-aft travel (mm): design variable SLIDE_VARIABLE, 0 when the design has none.
    The seat's box is at the middle of the travel; each person sits where suits them best within it (poses)."""
    q = design.get("variables", {}).get(SLIDE_VARIABLE)
    if not q:
        return 0.0
    if q.get("unit") != "mm":
        raise ValueError(f"{SLIDE_VARIABLE} must be in mm, got {q.get('unit')!r}")
    v = float(q["value"])
    if v < 0.0:
        raise ValueError(f"{SLIDE_VARIABLE} must not be negative, got {v:g} mm")
    return v


def best_offset(score, slide: float) -> float:
    """The offset s (mm, along x from the middle of the travel, -slide/2 to slide/2) with the largest score(s):
    SLIDE_LEVELS levels across the travel, then a golden-section search between the best level's neighbours
    down to SLIDE_RESOLUTION_MM; equal scores (within 1e-9 mm): the one nearer the middle. 0 without travel."""
    if slide <= 0.0:
        return 0.0
    half = slide / 2.0
    seen: dict[float, float] = {}

    def f(s: float) -> float:
        s = float(min(max(s, -half), half))
        if s not in seen:
            seen[s] = float(score(s))
        return seen[s]

    xs = [-half + slide * i / (SLIDE_LEVELS - 1) for i in range(SLIDE_LEVELS)]
    k = max(range(SLIDE_LEVELS), key=lambda i: (f(xs[i]), -abs(xs[i])))
    a, b = xs[max(k - 1, 0)], xs[min(k + 1, SLIDE_LEVELS - 1)]
    g = (math.sqrt(5.0) - 1.0) / 2.0
    c, d = b - g * (b - a), a + g * (b - a)
    while b - a > SLIDE_RESOLUTION_MM:
        if f(c) >= f(d):
            b, d = d, c
            c = b - g * (b - a)
        else:
            a, c = c, d
            d = a + g * (b - a)
    return max(seen, key=lambda s: (round(seen[s], 9), -abs(s)))


def _aabb(b) -> tuple[np.ndarray, np.ndarray]:
    """The axis-aligned box around a K4 body's shape (its capsule when it has one, else its box)."""
    if b.capsule is not None:
        c = b.capsule
        return np.minimum(c.a, c.b) - c.r, np.maximum(c.a, c.b) + c.r
    pts = b.obb.corners()
    return pts.min(axis=0), pts.max(axis=0)


def _aabbs(bs: dict) -> tuple[list[str], np.ndarray, np.ndarray]:
    """The keys of `bs` in order and the axis-aligned boxes round their shapes (_aabb) as two arrays (n, 3)."""
    keys = list(bs)
    boxes = [_aabb(bs[k]) for k in keys]
    return keys, np.array([b[0] for b in boxes]).reshape(-1, 3), np.array([b[1] for b in boxes]).reshape(-1, 3)


def _leg_checks(pose: Pose, oid: str, ctx: dict, bs: dict, legs: bool = True,
                feet: bool = True, boxes: tuple | None = None) -> dict[str, tuple[float, str]]:
    """The big person's legs and feet against the vehicle: with legs leg_clearance, with feet foot_room and
    foot_support (mm). The thighs and shanks are capsules, whose overlap with any body is the negative of the exact
    distance between the two shapes when apart, so a body whose axis-aligned box is farther from the leg's than
    the nearest body found so far is not measured (it cannot be nearer); equal overlaps keep the body first in
    `bs`, as measuring them all in that order does. boxes: _aabbs(bs), when the caller measures many poses."""
    from .model import shape_overlap

    seat_key = f"part:{ctx['seat']['id']}"
    floor_key = f"part:{ctx['floor']['id']}"
    order = {kb: i for i, kb in enumerate(bs)}
    worst_leg, worst_foot = (-math.inf, -1, ""), (-math.inf, -1, "")
    for k, lb in leg_bodies(pose, oid).items():
        foot = ".foot_" in k
        if not (feet if foot else legs):
            continue
        cands = [(kb, b) for kb, b in bs.items()
                 if not (kb == seat_key or b.kind in (FOOT_SKIP_KINDS if foot else LEG_SKIP_KINDS)
                         or (foot and kb == floor_key))]
        if not foot:
            lo_a, hi_a = _aabb(lb)
            if boxes is None:
                boxes = _aabbs(bs)
            _, lo_all, hi_all = boxes
            idx = np.array([order[kb] for kb, _ in cands], dtype=int)
            lo_b, hi_b = lo_all[idx], hi_all[idx]
            gaps = np.linalg.norm(np.maximum(0.0, np.maximum(lo_b - hi_a, lo_a - hi_b)), axis=1)
            # the same order as sorting (gap, then the order in bs): a stable sort of the gaps
            cands = [(float(gaps[i]), *cands[i]) for i in np.argsort(gaps, kind="stable")]
        else:
            cands = [(None, kb, b) for kb, b in cands]
        for g, kb, b in cands:
            cur = worst_foot if foot else worst_leg
            if g is not None and g > 0.0 and -g < cur[0]:
                break  # apart by at least g, sorted by it: no body from here on can be nearer
            o = shape_overlap(lb, b)
            if o > cur[0] or (o == cur[0] and order[kb] < cur[1]):
                cur = (o, order[kb], kb.split(":", 1)[1])
            if foot:
                worst_foot = cur
            else:
                worst_leg = cur
    out: dict[str, tuple[float, str]] = {}
    if worst_foot[2]:
        out["foot_room"] = (-worst_foot[0], worst_foot[2])
    if worst_leg[2]:
        out["leg_clearance"] = (-worst_leg[0], worst_leg[2])
    if not feet:
        return out
    lo, hi = _extent(ctx["floor"])
    s = SIZES[BIG]
    margins = []
    for g in pose.legs.values():
        for p in (g.heel, g.toe):
            for sy in (-1.0, 1.0):
                yy = p[1] + sy * s["foot_breadth"] / 2.0
                margins.append(min(p[0] - lo[0], hi[0] - p[0], yy - lo[1], hi[1] - yy))
    out["foot_support"] = (float(min(margins)), ctx["floor"]["id"])
    return out


def _small_checks(pose: Pose, ctx: dict, wheel: dict | None) -> dict[str, tuple[float, str]]:
    """The small person on the pedal: pedal_reach and wheel_reach (mm)."""
    out = {"pedal_reach": (pedal_margin(pose), ctx["pedal"]["id"])}
    if wheel is not None:
        out["wheel_reach"] = (wheel_margin(pose, wheel), wheel["id"])
    return out


def poses(design: dict) -> dict[str, dict]:
    """For each occupant with a seat: {"big": Pose (m95), "small": Pose (f5) or None, "pedal": part or None,
    "floor": part, "seat": part, "offset": {"big": mm, "small": mm or None}}. The first occupant drives: the
    big and the small person's right foot on the pedal under it. The others sit with the knee at
    KNEE_ANGLE_DEG. Occupants without a floor ahead are left out.

    The driver's seat slides (seat_slide): each of the two sits at the offset along x from the middle of the
    travel (the seat's box) where the smallest of their own H3 margins is the largest (best_offset), the big
    person's: knee_room, foot_room, leg_clearance, foot_support and the two sides of knee_angle_margins; the
    small person's: pedal_reach, wheel_reach and the two sides of knee_angle_margins. The other occupants sit
    at the middle."""
    from .model import bodies

    parts = design.get("parts", [])
    seats = [p for p in parts if p["kind"] == "seat"]
    occ = occupants(design)
    out = {}
    bs = None
    for i, o in enumerate(occ):
        seat = _seat_of(o, seats)
        if seat is None:
            continue
        lo, hi = _box(seat)
        y = float(seat["box"]["center_mm"][1])
        floor = feet_floor(design, lo[0], hi[2], y)
        pedal = driver_pedal(design, o) if i == 0 else None
        if pedal is not None:
            face = pedal_face(pedal)
            floor = floor_under(design, float(face[0]), float(face[1]), float(face[2])) or floor
        if floor is None:
            continue
        fz = float(_extent(floor)[1][2])
        if pedal is None:
            out[o["id"]] = {"big": seated(lo[0], hi[2], y, fz, BIG), "small": None, "pedal": None, "floor": floor,
                            "seat": seat, "offset": {"big": 0.0, "small": None}}
            continue
        ball_xz = (float(face[0]), float(face[2]))
        ctx = {"seat": seat, "floor": floor, "pedal": pedal}
        slide = seat_slide(design)

        def at(size: str, s: float) -> Pose:
            return seated(lo[0] + s, hi[2], y, fz, size, right_ball=ball_xz)

        s_big = s_small = 0.0
        if slide > 0.0:
            bs = bs if bs is not None else bodies(design)
            boxes = _aabbs(bs)
            extents = [_extent(p) for p in parts]
            wheel = steering_wheel_of(design, seat)

            # the feet stay on the pedal and the floor wherever the seat is: their margins once
            fixed = [v for v, _ in _leg_checks(at(BIG, 0.0), o["id"], ctx, bs, legs=False).values()]

            def big_score(s: float, boxes=boxes, bs=bs, ctx=ctx, o=o) -> float:
                p = at(BIG, s)
                vals = fixed + [v for v, _ in _leg_checks(p, o["id"], ctx, bs, feet=False, boxes=boxes).values()]
                vals += list(knee_angle_margins(p))
                knee = _knee_length(design, o, seat, lo[0] + s, extents)
                if knee is not None:
                    vals.append(knee[0] - AIST["buttock_knee_m95"])
                return min(vals)

            def small_score(s: float) -> float:
                p = at(SMALL, s)
                return min([v for v, _ in _small_checks(p, ctx, wheel).values()] + list(knee_angle_margins(p)))

            s_big, s_small = best_offset(big_score, slide), best_offset(small_score, slide)
        out[o["id"]] = {"big": at(BIG, s_big), "small": at(SMALL, s_small), "pedal": pedal, "floor": floor,
                        "seat": seat, "offset": {"big": s_big, "small": s_small}}
    return out


def body_fit(design: dict, ps: dict | None = None) -> dict[str, tuple[float, str]]:
    """The seated body against the vehicle (mm, negative = does not fit), for designs with pedals:
    foot_room (the big person's feet to the nearest body other than what they rest on), foot_support
    (how far inside the floor's edge in plan the feet are), leg_clearance (the big person's thighs and
    shanks to the nearest body, as capsules), pedal_reach (the small person's ball of the foot onto the
    pedal: the hip-ankle distance of the straight leg less the distance needed; negative only when the foot
    does not reach), knee_angle_room (the
    smaller of knee_angle_margins over the big and the small person: the pedal foot's knee angle inside
    KNEE_COMFORT_DEG, as mm of the hip-ankle distance) and wheel_reach
    (the small person's arm reach, C3 - C16, less the distance from the shoulder to the grip on the
    steering wheel's rim beside its centre). Each person where poses() seats them (ps: its result, when the
    caller has it)."""
    from .model import bodies

    if not any(p["kind"] in PEDAL_KINDS for p in design.get("parts", [])):
        return {}
    bs = bodies(design)
    out: dict[str, tuple[float, str]] = {}
    for oid, p in (poses(design) if ps is None else ps).items():
        for k, v in _leg_checks(p["big"], oid, p, bs).items():
            out[f"{k}.{oid}"] = v
        if p["small"] is not None:
            small = _small_checks(p["small"], p, steering_wheel_of(design, p["seat"]))
            out[f"pedal_reach.{oid}"] = small["pedal_reach"]
            out[f"knee_angle_room.{oid}"] = (min(min(knee_angle_margins(p[k])) for k in ("big", "small")),
                                             p["pedal"]["id"])
            if "wheel_reach" in small:
                out[f"wheel_reach.{oid}"] = small["wheel_reach"]
    return out


def pedal_margin(pose: Pose, knee_max: float = KNEE_MAX_DEG) -> float:
    """How much closer (mm, negative: farther) the right ankle is to the hip than the leg reaches
    with the knee at knee_max (default: straight, so negative only when the foot does not reach)."""
    s = SIZES[pose.size]
    l1, l2 = _links(s)
    g = pose.legs["R"]
    d_max = math.sqrt(l1 * l1 + l2 * l2 - 2 * l1 * l2 * math.cos(math.radians(knee_max)))
    need = float(np.linalg.norm((g.ankle - g.hip)[[0, 2]]))
    return d_max - need


def _hip_ankle(s: dict, knee_angle: float) -> float:
    """The hip-ankle distance (mm, side view) of a leg of sizes s with the knee at knee_angle (deg)."""
    l1, l2 = _links(s)
    return math.sqrt(l1 * l1 + l2 * l2 - 2 * l1 * l2 * math.cos(math.radians(knee_angle)))


def knee_angle_margins(pose: Pose, comfort: tuple[float, float] = KNEE_COMFORT_DEG) -> tuple[float, float]:
    """The right leg's (the pedal foot's) knee angle against the comfortable range, as mm of the hip-ankle
    distance (side view, as pedal_margin): (bent, straight). bent = the distance now less the distance at the
    range's lower angle (negative: the knee is bent more than the range, the pedal too near); straight = the
    distance at the upper angle less the distance now (negative: the leg is straighter than the range, the
    pedal too far). The distance grows with the angle, so each is negative exactly when the angle is out of range."""
    s = SIZES[pose.size]
    g = pose.legs["R"]
    now = float(np.linalg.norm((g.ankle - g.hip)[[0, 2]]))
    return now - _hip_ankle(s, comfort[0]), _hip_ankle(s, comfort[1]) - now


def knee_angles(design: dict) -> dict[str, dict[str, float]]:
    """{occupant id: {"big": deg, "small": deg}}: the right knee's angle of the drivers (the occupants with a
    pedal), as the poses have them. A leg that does not reach its pedal is at 180."""
    return {oid: {k: float(ps[k].legs["R"].knee_angle) for k in ("big", "small")}
            for oid, ps in poses(design).items() if ps["small"] is not None}


def steering_wheel_of(design: dict, seat: dict) -> dict | None:
    wheels = [p for p in design.get("parts", []) if p["kind"] in WHEEL_KINDS]
    if not wheels:
        return None
    y = float(seat["box"]["center_mm"][1])
    return min(wheels, key=lambda p: abs(float(p["box"]["center_mm"][1]) - y))


def grip_points(wheel: dict) -> list[np.ndarray]:
    """The two grips on the rim, at the ends of the wheel's diameter that runs most nearly along y."""
    from .obb import from_box

    o = from_box(wheel["box"]["center_mm"], wheel["box"]["size_mm"], wheel["box"].get("rpy_deg"))
    k = int(np.argmax(np.abs(o.axes[:, 1]) * (o.half > o.half.min() + 1e-9)))
    return [o.center + o.axes[k] * o.half[k], o.center - o.axes[k] * o.half[k]]


def wheel_margin(pose: Pose, wheel: dict) -> float:
    """Arm reach less the distance from the shoulder to the farther grip, both measured in the side
    view (the hands are taken at shoulder width: assumption)."""
    far = max(float(np.linalg.norm((g - pose.shoulder)[[0, 2]])) for g in grip_points(wheel))
    return pose.arm_reach - far


KNEE_BAND_MM = 300.0  # parts this far above the seat top are at knee height (assumption)


def _seat_of(o: dict, seats: list[dict]) -> dict | None:
    return min(seats, key=lambda s: abs(s["box"]["center_mm"][1] - o["box"]["center_mm"][1])) if seats else None


def _knee_length(design: dict, o: dict, seat: dict | None, seat_back: float,
                 extents: list | None = None) -> tuple[float, str] | None:
    """(length mm from seat_back to the nearest part ahead at knee height, that part's id), None with nothing ahead.
    extents: [_extent(p) for p in the design's parts], when the caller measures many seat_back."""
    parts = design.get("parts", [])
    ext = extents if extents is not None else [_extent(p) for p in parts]
    lo, _ = _box(o)
    seat_top = _box(seat)[1][2] if seat else lo[2]
    ahead = [(p, e) for p, e in zip(parts, ext) if p is not o and p is not seat
             and p["kind"] not in ("occupant", "seat")
             and e[0][0] > seat_back and e[0][2] < seat_top + KNEE_BAND_MM and e[1][2] > seat_top and _overlaps_y(p, o)]
    if not ahead:
        return None
    first, e = min(ahead, key=lambda pe: pe[1][0][0])
    return float(e[0][0] - seat_back), first["id"]


def slide_ends(design: dict) -> list[tuple[str, dict, set[str]]]:
    """The design with the driver's seat (the seat of each occupant with a pedal) at each end of its slide:
    [("front" | "rear", design copy, {seat part ids moved})]; [] without a slide. The seat's box is at the middle
    of the travel in the design itself, so the interference at rest there does not see its ends."""
    import copy

    slide = seat_slide(design)
    parts = design.get("parts", [])
    if slide <= 0.0 or not any(p["kind"] in PEDAL_KINDS for p in parts):
        return []
    seats = [p for p in parts if p["kind"] == "seat"]
    moved = {s["id"] for o in occupants(design) for s in [_seat_of(o, seats)]
             if s is not None and _drives(design, o)}
    out = []
    for label, dx in (("front", slide / 2.0), ("rear", -slide / 2.0)):
        d = copy.deepcopy(design)
        for p in d["parts"]:
            if p["id"] in moved:
                p["box"]["center_mm"][0] = float(p["box"]["center_mm"][0]) + dx
        out.append((label, d, moved))
    return out


def _drives(design: dict, occupant: dict) -> bool:
    """Whether the occupant has a pedal in front of them (the driver): poses' rule (the first occupant, with a seat,
    a pedal and a floor under the feet), without placing the people (the same as `occupant["id"] in
    knee_angles(design)`)."""
    occ = occupants(design)
    if not occ or occ[0]["id"] != occupant["id"]:
        return False
    seat = _seat_of(occupant, [p for p in design.get("parts", []) if p["kind"] == "seat"])
    if seat is None:
        return False
    pedal = driver_pedal(design, occupant)
    if pedal is None:
        return False
    lo, hi = _box(seat)
    face = pedal_face(pedal)
    floor = (floor_under(design, float(face[0]), float(face[1]), float(face[2]))
             or feet_floor(design, lo[0], hi[2], float(seat["box"]["center_mm"][1])))
    return floor is not None


def knee_space(design: dict, ps: dict | None = None) -> dict[str, tuple[float, str]]:
    """For each occupant: (length mm from the seat back to the nearest part ahead at knee
    height, that part's id). Occupants with nothing ahead are left out. Parts turned by
    rpy_deg are taken at the box around their turned box. On a sliding driver's seat (seat_slide) the seat
    back is where poses() seats the big person (ps: its result, when the caller has it)."""
    parts = design.get("parts", [])
    seats = [p for p in parts if p["kind"] == "seat"]
    offsets = {}
    if seat_slide(design) > 0.0:
        offsets = {k: p["offset"]["big"] for k, p in (poses(design) if ps is None else ps).items()}
    out = {}
    for o in occupants(design):
        seat = _seat_of(o, seats)
        seat_back = (_box(seat)[0][0] if seat else _box(o)[0][0]) + offsets.get(o["id"], 0.0)
        got = _knee_length(design, o, seat, seat_back)
        if got is not None:
            out[o["id"]] = got
    return out


def _overlaps_y(p, o) -> bool:
    a0, a1 = _extent(p)
    b0, b1 = _box(o)
    return a0[1] < b1[1] and b0[1] < a1[1]


def _overlaps_xy(p, o) -> bool:
    a0, a1 = _extent(p)
    b0, b1 = _box(o)
    return _overlaps_y(p, o) and a0[0] < b1[0] and b0[0] < a1[0]

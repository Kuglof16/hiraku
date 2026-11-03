"""A two-seat electric UTV on a space frame of round steel tube, built from its variables.

Four wheels on double wishbones (front steer, rear drive), right-hand drive (the driver is the first
occupant in the parts list, as in ladder_4w). The frame is made of three pieces joined into one:

- the floor: two floor rails (y = +-floor_half_width) from just behind the steered front tyre to just
  ahead of the rear tyre, three cross tubes (front, at the main hoop, rear) and an X of diagonals in
  the floor bay ahead of the main hoop;
- a suspension subframe at each axle whose nodes are the body side pickup points of the double
  wishbones (K2's hardpoints_for): lower arm pivots (lca_front, lca_rear), upper arm pivots
  (uca_front, uca_rear), the damper's top (damper_upper) and the tie rod's inner joint (tie_inner).
  A tube runs along each arm's pivot axis, posts join the lower and upper pivots (the rear post
  through tie_inner), two tubes rise from the upper pivots to the damper top (the tower), and
  cross tubes join the left and right subframes. Tubes from the subframes reach the floor's front
  and rear cross tubes and the cage;
- the cage around the occupants: A-pillars from the floor's front corners to the roof (with a node
  at the dash height for the dash bar and the front subframe's upper link), the main hoop behind the
  seats (with a node at the deck height for the deck rails and the seat back bar), roof rails, roof
  and hoop cross tubes, an X in the roof and in the hoop's upper bay, and stays from the hoop's top
  to the rear damper tops.

Everything is placed by rule from the variables. The tube sizes are JIS G 3444 standard sizes
(kit.sections.pick; the size list's source is in data/sections/standard_tubes.json): the smallest
standard outside diameter at or above the one asked for, with the lightest standard wall at or above
the wall asked for. The material is STKM13A (data/materials/materials.json, JIS G 3445): that the
G 3444 sizes are made in STKM13A is assumed, not checked; its yield (215 MPa in that file) is the
one the frame is judged against.

Parts are mass-equivalent boxes as in ladder_4w; the battery sits on the floor under the seats (the
seats stand on it), the motor on the rear subframe's lower cross tubes, the controller on the floor
behind the main hoop, the wiring box in the front subframe, the cargo on a deck over the rear
subframe. Clearances below are design choices (assumptions) unless a comment gives a source.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from hiraku.designgen.kit.frame import Frame
from hiraku.designgen.kit.package import Item, Layout, bottom_at, top_at
from hiraku.designgen.kit.running_gear import Suspension, corner_name, four_wheels
from hiraku.designgen.kit.sections import pick, standard
from hiraku.designgen.ladder_4w import BATTERY_V1, MOTOR_V1, WHEEL_MASS_KG, DesignGenError, Var
from hiraku.designgen.ladder_4w import VARIABLES as LADDER_VARIABLES

Record = dict[str, Any]

MATERIALS = Path(__file__).resolve().parents[2] / "data" / "materials" / "materials.json"
MATERIAL = "stkm13a"

# Variables of ladder_4w that this design carries unchanged (requirements, powertrain, chassis, tyres,
# aerodynamics, thermal and fatigue values): the same meaning, default and range.
SHARED = ("max_speed", "occupants", "occupant_mass", "cargo_mass", "cargo_height", "front_spring_rate",
          "rear_spring_rate", "suspension_travel", "max_steer_angle", "motor_peak_power", "motor_peak_torque",
          "motor_max_speed", "gear_ratio", "drivetrain_efficiency", "motor_efficiency", "controller_efficiency",
          "regen_efficiency", "aux_power", "battery_voltage", "battery_capacity", "battery_resistance", "mass_factor",
          "rolling_resistance", "battery_heat_capacity", "battery_thermal_resistance", "motor_heat_capacity",
          "motor_thermal_resistance", "controller_heat_capacity", "controller_thermal_resistance",
          "battery_max_temperature", "controller_max_temperature", "motor_max_temperature", "drag_coefficient",
          "frontal_area", "side_force_coefficient", "side_area", "centre_of_pressure_height", "air_density",
          "front_cornering_stiffness", "rear_cornering_stiffness", "bumper_stiffness", "bumper_stroke",
          "crush_stiffness", "fatigue_fat_class", "fatigue_slope", "fatigue_knee_cycles", "fatigue_slope_after_knee",
          "tyre_stiffness", "tyre_damping_ratio", "tyre_slip_stiffness_coeff", "bump_stop_factor")

VARIABLES: dict[str, Var] = {
    # layout (design choices; the defaults are assumptions)
    "wheelbase": Var("mm", 1900.0, 1600.0, 2300.0, "front axle to rear axle"),
    "track": Var("mm", 1150.0, 1000.0, 1400.0, "wheel centre to wheel centre across"),
    "wheel_radius": Var("mm", 290.0, 250.0, 350.0, "tyre radius"),
    "wheel_width": Var("mm", 200.0, 150.0, 280.0, "tyre width"),
    "floor_half_width": Var("mm", 450.0, 350.0, 600.0, "floor rail centre line off the vehicle centre line"),
    "floor_height": Var("mm", 240.0, 200.0, 350.0, "height of the floor rails' centre line"),
    "seat_x": Var("mm", -870.0, -1200.0, -650.0, "seat centre (x); the main hoop stands behind the occupants"),
    "seat_y": Var("mm", 280.0, 230.0, 350.0, "seat centre off the centre line, each side"),
    "dash_height": Var("mm", 850.0, 650.0, 1000.0, "height of the dash bar (its node on each A-pillar); the "
                       "steering hangs below it"),
    # clearances (assumptions, not from a standard)
    "head_clearance": Var("mm", 100.0, 50.0, 200.0, "from the top of an occupant's box to the roof tubes' surface"),
    "cage_clearance": Var("mm", 60.0, 30.0, 150.0, "from an occupant's box to the cage tubes ahead, behind and "
                          "beside it"),
    "tyre_clearance": Var("mm", 60.0, 30.0, 150.0, "from the tyre's swept circle (front: steered about the "
                          "wheel centre) to the floor's front and rear cross tubes"),
    # tube sizes asked for (each becomes a JIS G 3444 standard size, see the docstring)
    "floor_tube_d": Var("mm", 42.7, 27.2, 76.3, "floor rails and floor cross tubes, outside diameter"),
    "floor_tube_t": Var("mm", 2.3, 2.0, 4.0, "floor rails and floor cross tubes, wall"),
    "cage_tube_d": Var("mm", 42.7, 27.2, 76.3, "cage (pillars, hoop, roof), outside diameter"),
    "cage_tube_t": Var("mm", 2.3, 2.0, 4.0, "cage (pillars, hoop, roof), wall"),
    "brace_tube_d": Var("mm", 34.0, 21.7, 60.5, "subframes, diagonals and stays, outside diameter"),
    "brace_tube_t": Var("mm", 2.3, 2.0, 4.0, "subframes, diagonals and stays, wall"),
    "dash_tube_d": Var("mm", 48.6, 27.2, 76.3, "dash bar (the front subframe's upper links meet at its middle), "
                       "outside diameter"),
    "dash_tube_t": Var("mm", 3.2, 2.0, 4.0, "dash bar, wall"),
    **{k: LADDER_VARIABLES[k] for k in SHARED},
}
# A design built here carries these; ladder_4w's signature (rail_spacing, ...) is not among them.
SIGNATURE = ("wheelbase", "track", "wheel_radius", "floor_half_width", "floor_height", "head_clearance",
             "cage_clearance")
DERIVED = {
    "tyre_relaxation_length": ("mm", "assumption: the tyre's relaxation length, taken as the wheel radius"),
    "wheel_inertia": ("kg*m^2", "rotary inertia of one wheel part, as a solid disc: m r^2 / 2"),
    "upper_joint_z": ("mm", "height of the upper ball joint chosen so the arms and dampers clear the frame "
                      "(model.hardpoints_for)"),
    "damper_upper_y": ("mm", "side position |y| of the damper's top chosen so it clears the frame"),
}
CHOICES: dict[str, tuple[str, ...]] = {}
TITLE = "2 人乗り 4 輪の丸パイプ立体フレームの電動 UTV (前輪で操舵、後輪で駆動、右ハンドル、ダブルウィッシュボーン)"
DESCRIPTION = "2-seat electric UTV, round-tube space frame with a cage, double wishbones, rear drive"

# Sizes and clearances of the layout (design choices, assumptions)
SEAT_SIZE = (500.0, 500.0, 260.0)  # as ladder_4w
OCCUPANT_SIZE = (450.0, 450.0, 520.0)  # as ladder_4w
OCCUPANT_BACK_MM = 55.0  # the occupant's box centre this far behind the seat centre (as ladder_4w)
ON_TUBES_MM = 10.0  # panels and parts sit this far above the tubes they rest on (brackets)
ARM_RADIUS_MM = 12.5  # a wishbone leg taken as a 25 mm tube
DAMPER_RADIUS_MM = 25.0  # a coil-over damper taken as a 50 mm cylinder
TIE_ROD_RADIUS_MM = 8.0
ARM_CLEARANCE_MM = 10.0  # least gap from an arm, damper or tie rod (through the travel) to a frame tube
TRAVEL_SAMPLES = 7  # wheel travels checked, evenly over the suspension travel
HARDPOINT_STEP_MM = 5.0  # the search for upper_joint_z and damper_upper_y moves them in these steps
HARDPOINT_STEPS = 12  # at most this many steps each way
DECK_GAP_MM = 60.0  # the deck starts this far behind the main hoop's surface
CONTROLLER_SIZE = (200.0, 250.0, 80.0)
AUX_SIZE = (200.0, 300.0, 100.0)
STEERING_SIZE = (200.0, 200.0, 300.0)
FRONT_BODY_SIZE = (360.0, 600.0, 200.0)
SUSP_SIZE = (250.0, 120.0, 200.0)
SUSP_MASS_KG = 8.0  # as ladder_4w
MIN_BAY_MM = 100.0  # tubes closer than this along x would leave no room between them
KNEE_MARGIN_MM = 20.0  # the steering's rear face this much beyond the knee length

ARM_KEYS = ("lca_front", "lca_rear", "uca_front", "uca_rear", "damper_upper", "tie_inner")


def spec() -> list[Record]:
    """Every variable: name, unit, default, min and max (revisable ones), and what it is."""
    out = []
    for name, v in VARIABLES.items():
        r: Record = {"name": name, "unit": v.unit, "default": v.default, "revisable": v.revisable, "meaning": v.meaning}
        if v.revisable:
            r.update(min=v.lo, max=v.hi)
        out.append(r)
    for name, (unit, meaning) in DERIVED.items():
        out.append({"name": name, "unit": unit, "revisable": False, "derived": True, "meaning": meaning})
    return out


def _values(given: dict | None) -> dict[str, float]:
    from hiraku.solvers.k0_handcalc import units

    vals = {k: v.default for k, v in VARIABLES.items()}
    for name, x in (given or {}).items():
        if name not in VARIABLES:
            raise DesignGenError(f"unknown variable {name!r}")
        var = VARIABLES[name]
        try:
            val = units.to_unit(x, var.unit, name) if isinstance(x, dict) else float(x)
        except ValueError as e:
            raise DesignGenError(str(e)) from None
        if var.revisable and not var.lo <= val <= var.hi:
            raise DesignGenError(f"{name} = {val:g} {var.unit} is outside {var.lo:g} to {var.hi:g} {var.unit}")
        vals[name] = val
    return vals


def values_of(design: Record) -> dict[str, Record]:
    """The generator variables a design carries (design.variables), as quantities."""
    return {k: v for k, v in design.get("variables", {}).items() if k in VARIABLES}


def apply_choices(choices: dict | None, values: dict) -> dict:
    if choices:
        raise DesignGenError(f"space_frame_utv has no choices, got {sorted(choices)}")
    return dict(values)


def choices_of(design: Record) -> dict[str, str]:
    return {}


def applies(flags: dict) -> bool:
    """For products that move by themselves and carry people or cargo."""
    return bool(flags.get("self_propelled")) and bool(flags.get("ride") or flags.get("cargo"))


def _q(v: float, unit: str) -> Record:
    return {"value": v, "unit": unit}


def tube(d: float, t: float) -> tuple[str, Record]:
    """The JIS G 3444 standard round tube with the smallest outside diameter >= d and, of that diameter,
    the lightest wall >= t."""
    ds = sorted({sec["dims_mm"]["d"] for _, sec in standard("round_tube")})
    for dd in ds:
        if dd >= d - 1e-9:
            try:
                return pick("round_tube", d=dd, t_min=t)
            except ValueError:
                continue
    raise DesignGenError(f"no JIS G 3444 standard round tube with d >= {d:g} mm and t >= {t:g} mm")


# ---------------------------------------------------------------- geometry helpers


def _seg_points(a, b, n: int = 24) -> np.ndarray:
    a, b = np.asarray(a, float), np.asarray(b, float)
    return a + np.linspace(0.0, 1.0, n)[:, None] * (b - a)


def segment_distance(p0, p1, q0, q1) -> float:
    """Least distance between segments p0-p1 and q0-q1 (closed form with clamping)."""
    p0, p1, q0, q1 = (np.asarray(v, float) for v in (p0, p1, q0, q1))
    d1, d2, r = p1 - p0, q1 - q0, p0 - q0
    a, e, f = d1 @ d1, d2 @ d2, d2 @ r
    if a <= 1e-12 and e <= 1e-12:
        return float(np.linalg.norm(r))
    if a <= 1e-12:
        s, t = 0.0, min(max(f / e, 0.0), 1.0)
    else:
        c = d1 @ r
        if e <= 1e-12:
            t, s = 0.0, min(max(-c / a, 0.0), 1.0)
        else:
            b = d1 @ d2
            den = a * e - b * b
            s = min(max((b * f - c * e) / den, 0.0), 1.0) if den > 1e-12 else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, min(max(-c / a, 0.0), 1.0)
            elif t > 1.0:
                t, s = 1.0, min(max((b - c) / a, 0.0), 1.0)
    return float(np.linalg.norm(p0 + d1 * s - (q0 + d2 * t)))


def segment_box_distance(a, b, lo, hi, n: int = 200) -> float:
    """Least distance from segment a-b to the axis-aligned box [lo, hi] (sampled at n points, then the
    best sample refined by golden section; 0 when the segment enters the box)."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    a, b = np.asarray(a, float), np.asarray(b, float)

    def dist(s: float) -> float:
        p = a + s * (b - a)
        return float(np.linalg.norm(np.maximum(np.maximum(lo - p, p - hi), 0.0)))

    ss = np.linspace(0.0, 1.0, n)
    k = int(np.argmin([dist(s) for s in ss]))
    x0, x1 = ss[max(k - 1, 0)], ss[min(k + 1, n - 1)]
    g = (math.sqrt(5.0) - 1.0) / 2.0
    for _ in range(60):
        c, d = x1 - g * (x1 - x0), x0 + g * (x1 - x0)
        if dist(c) < dist(d):
            x1 = d
        else:
            x0 = c
    return min(dist(0.5 * (x0 + x1)), dist(ss[k]))


# ---------------------------------------------------------------- suspension links against the frame


def links_through_travel(hp_mm: dict, radius_mm: float, travel_mm: float, samples: int = TRAVEL_SAMPLES) -> list:
    """The moving links of a LEFT double wishbone (hardpoints in mm, design frame) at `samples` wheel
    travels evenly over +-travel/2: [(name, mount key, point a, point b, link radius)] in mm, a at the
    body mount. The lower arm's legs, the upper arm's legs, the damper (from its point on the lower
    arm to its top) and the tie rod (from its inner joint to the knuckle)."""
    from hiraku.solvers.k2_vehicle import kinematics

    hp = kinematics.Hardpoints.from_json(dict({k: list(v) for k, v in hp_mm.items()}, axle=[0.0, 1.0, 0.0],
                                              radius_mm=radius_mm))
    out = []
    for tr in np.linspace(-travel_mm / 2000.0, travel_mm / 2000.0, samples):
        sol = kinematics.solve_at_travel(hp, float(tr))
        lbj, ubj, rk = sol["lbj"], sol["ubj"], sol["rotation"]
        tro = lbj + rk @ (hp.tie_outer - hp.lca_outer)
        ea = (hp.lca_rear - hp.lca_front) / np.linalg.norm(hp.lca_rear - hp.lca_front)
        u0 = hp.lca_outer - hp.lca_front - ea * float((hp.lca_outer - hp.lca_front) @ ea)
        u1 = lbj - hp.lca_front - ea * float((lbj - hp.lca_front) @ ea)
        rot = kinematics.rotation_between(u0, u1)
        dl = hp.lca_front + rot @ (hp.damper_lower - hp.lca_front)
        mm = lambda v: np.asarray(v, float) * 1000.0  # noqa: E731
        out += [("lower arm front leg", "lca_front", mm(hp.lca_front), mm(lbj), ARM_RADIUS_MM),
                ("lower arm rear leg", "lca_rear", mm(hp.lca_rear), mm(lbj), ARM_RADIUS_MM),
                ("upper arm front leg", "uca_front", mm(hp.uca_front), mm(ubj), ARM_RADIUS_MM),
                ("upper arm rear leg", "uca_rear", mm(hp.uca_rear), mm(ubj), ARM_RADIUS_MM),
                ("damper", "damper_upper", mm(hp.damper_upper), mm(dl), DAMPER_RADIUS_MM),
                ("tie rod", "tie_inner", mm(hp.tie_inner), mm(tro), TIE_ROD_RADIUS_MM)]
    return out


def arm_clearance(frame: Frame, corner: str, hp_mm: dict, radius_mm: float, travel_mm: float) -> tuple[float, str]:
    """Least gap (mm) between the links of a left corner through the travel and the frame tubes, and
    where. Tubes that end at a link's own body mount are its bracket and are not checked (assumption:
    the bracket holds the joint clear of them)."""
    nodes = frame.nodes
    tubes = []
    for m in frame.members:
        r = frame.sections[m["section"]]["dims_mm"]["d"] / 2.0
        tubes.append((m["id"], m["node_a"], m["node_b"], np.asarray(nodes[m["node_a"]]), np.asarray(nodes[m["node_b"]]), r))
    best, where = math.inf, ""
    for name, key, a, b, rl in links_through_travel(hp_mm, radius_mm, travel_mm):
        own = f"L{corner}_{key}"
        for mid, na, nb, p, q, r in tubes:
            if own in (na, nb):
                continue
            gap = segment_distance(a, b, p, q) - r - rl
            if gap < best:
                best, where = gap, f"{name} of {corner} and tube {mid}"
    return best, where


# ---------------------------------------------------------------- the frame


def _frame(v: dict[str, float], hp: dict[str, dict], secs: dict[str, tuple[str, Record]]) -> tuple[Frame, dict]:
    """The frame from the variables and the left corners' hardpoints {"A": front, "B": rear}. Returns the
    frame and the layout numbers the parts need."""
    wb, r_w, w_w = v["wheelbase"], v["wheel_radius"], v["wheel_width"]
    fz, fy = v["floor_height"], v["floor_half_width"]
    rf = secs["floor"][1]["dims_mm"]["d"] / 2.0
    rc = secs["cage"][1]["dims_mm"]["d"] / 2.0
    frame = Frame(MATERIAL)
    sec = {k: frame.section(*s) for k, s in secs.items()}

    # floor stations: behind the steered front tyre (the swept circle of the tyre's corner about the
    # wheel centre) and ahead of the rear tyre
    reach = math.hypot(r_w, w_w / 2.0)
    x_f = -(reach + v["tyre_clearance"] + rf)
    x_r = -wb + r_w + v["tyre_clearance"] + rf
    # the occupants and the cage around them
    ox, oy = v["seat_x"] - OCCUPANT_BACK_MM, v["seat_y"]
    battery_h = BATTERY_V1["height"]
    floor_top = fz + rf + ON_TUBES_MM + 20.0  # the floor panel (20 mm) on the floor rails
    occ_lo = np.array([ox - OCCUPANT_SIZE[0] / 2, oy - OCCUPANT_SIZE[1] / 2, floor_top + battery_h + SEAT_SIZE[2]])
    occ_hi = occ_lo + np.array(OCCUPANT_SIZE)
    roof_z = occ_hi[2] + v["head_clearance"] + rc
    cage_y = occ_hi[1] + v["cage_clearance"] + rc
    seat_rear = v["seat_x"] - SEAT_SIZE[0] / 2
    x_hoop = min(occ_lo[0], seat_rear) - v["cage_clearance"] - rc
    x_roof = occ_hi[0] + v["cage_clearance"] + rc
    if x_hoop - x_r < MIN_BAY_MM + 2 * rf:
        raise DesignGenError(f"the main hoop (x {x_hoop:.0f} mm) is too close to the rear floor cross tube "
                             f"(x {x_r:.0f} mm); move seat_x forward or lengthen the wheelbase")
    if x_f - x_hoop < MIN_BAY_MM:
        raise DesignGenError("the floor ahead of the main hoop is too short for the seats; move seat_x back")
    if x_roof > x_f + 300.0:
        raise DesignGenError("the occupants reach the front floor cross tube; move seat_x back")

    def lerp(a, b, z):
        a, b = np.asarray(a, float), np.asarray(b, float)
        return a + (z - a[2]) / (b[2] - a[2]) * (b - a)

    A, B = hp["A"], hp["B"]
    left: dict[str, list[float]] = {}
    for ax, h in (("A", A), ("B", B)):
        for k in ARM_KEYS:
            left[f"{ax}_{k}"] = h[k]
    y_in = A["lca_front"][1]
    p1, p2, p3 = [x_f, fy, fz], [x_hoop, fy, fz], [x_r, fy, fz]
    h1, h2 = [x_roof, cage_y, roof_z], [x_hoop, cage_y, roof_z]
    deck_z = B["damper_upper"][2]
    ad = lerp(p1, h1, v["dash_height"])
    hm = lerp(p2, h2, deck_z)
    left.update({"P1": p1, "P2": p2, "P3": p3, "Q1": [x_f, y_in, fz], "Q3": [x_r, B["lca_front"][1], fz],
                 "H1": h1, "H2": h2, "Ad": ad.tolist(), "Hm": hm.tolist()})
    for k, p in left.items():
        frame.node(f"L{k}", p)
        frame.node(f"R{k}", [p[0], -p[1], p[2]])
    # the crossing points of the X braces (each diagonal straight through it); the hoop is narrower at Hm
    # than at the roof, so its X crosses below the middle
    z_c2 = hm[2] + hm[1] / (hm[1] + cage_y) * (roof_z - hm[2])
    centre = {"C1": [(x_f + x_hoop) / 2, 0.0, fz], "C2": [x_hoop, 0.0, float(z_c2)],
              "C3": [(x_roof + x_hoop) / 2, 0.0, roof_z], "D0": [float(ad[0]), 0.0, float(ad[2])]}
    for k, p in centre.items():
        frame.node(k, p)

    def both(mid: str, a: str, b: str, s: str) -> None:
        frame.member(f"{mid}_L", f"L{a}", f"L{b}", sec[s])
        frame.member(f"{mid}_R", f"R{a}", f"R{b}", sec[s])

    def across(mid: str, a: str, s: str) -> None:
        frame.member(mid, f"L{a}", f"R{a}", sec[s])

    # subframes
    for ax in ("A", "B"):
        both(f"{ax}_lower_pivot", f"{ax}_lca_front", f"{ax}_lca_rear", "brace")
        both(f"{ax}_upper_pivot", f"{ax}_uca_front", f"{ax}_uca_rear", "brace")
        both(f"{ax}_front_post", f"{ax}_lca_front", f"{ax}_uca_front", "brace")
        both(f"{ax}_rear_post_low", f"{ax}_lca_rear", f"{ax}_tie_inner", "brace")
        both(f"{ax}_rear_post_up", f"{ax}_tie_inner", f"{ax}_uca_rear", "brace")
        both(f"{ax}_tower_front", f"{ax}_uca_front", f"{ax}_damper_upper", "brace")
        both(f"{ax}_tower_rear", f"{ax}_uca_rear", f"{ax}_damper_upper", "brace")
        across(f"{ax}_cross_lca_front", f"{ax}_lca_front", "brace")
        across(f"{ax}_cross_lca_rear", f"{ax}_lca_rear", "brace")
        across(f"{ax}_cross_damper", f"{ax}_damper_upper", "brace")
    across("A_cross_uca_front", "A_uca_front", "brace")  # the rear subframe leaves room for the motor
    across("A_cross_uca_rear", "A_uca_rear", "brace")
    # subframes to the floor and the cage
    both("front_lower_link", "A_lca_rear", "Q1", "brace")
    both("front_diagonal", "A_uca_rear", "Q1", "brace")
    # to the dash bar's middle: inboard of the steered tyre and of the steering ahead of the driver. The two
    # links' force bends the dash bar across its span, so the dash bar has its own (heavier) tube: with the
    # brace tube (34 x 2.3) it reached 91 MPa there, with 48.6 x 3.2 52 MPa (shell at 20 mm). Routes
    # tried and not taken (shell at 20 mm, K2 standing still): to the A-pillar's dash node, the largest stress
    # falls from 91 to 45 MPa but the link passes 10 mm from the front tyre at full lock and full bump
    # (tyre_clearance), against the 60 mm the floor's front cross tube keeps; to a pillar node higher up or
    # the roof's front corner, the driver's link passes through the steering's box.
    frame.member("front_upper_link_L", "LA_uca_rear", "D0", sec["brace"])
    frame.member("front_upper_link_R", "RA_uca_rear", "D0", sec["brace"])
    both("rear_lower_link", "B_lca_front", "Q3", "brace")
    both("rear_diagonal", "B_uca_front", "Q3", "brace")
    both("deck_rail", "Hm", "B_damper_upper", "brace")
    both("stay", "H2", "B_damper_upper", "brace")
    # floor
    both("floor_rail_front", "P1", "P2", "floor")
    both("floor_rail_rear", "P2", "P3", "floor")
    both("floor_front_outer", "P1", "Q1", "floor")
    frame.member("floor_front_mid", "LQ1", "RQ1", sec["floor"])
    both("floor_rear_outer", "P3", "Q3", "floor")
    frame.member("floor_rear_mid", "LQ3", "RQ3", sec["floor"])
    across("floor_hoop_cross", "P2", "floor")
    frame.member("floor_diag_L1", "LP1", "C1", sec["brace"])
    frame.member("floor_diag_R2", "C1", "RP2", sec["brace"])
    frame.member("floor_diag_R1", "RP1", "C1", sec["brace"])
    frame.member("floor_diag_L2", "C1", "LP2", sec["brace"])
    # cage
    both("a_pillar_low", "P1", "Ad", "cage")
    both("a_pillar_up", "Ad", "H1", "cage")
    both("hoop_low", "P2", "Hm", "cage")
    both("hoop_up", "Hm", "H2", "cage")
    both("roof_rail", "H1", "H2", "cage")
    across("roof_front_cross", "H1", "cage")
    across("roof_rear_cross", "H2", "cage")
    frame.member("dash_bar_L", "LAd", "D0", sec["dash"])
    frame.member("dash_bar_R", "D0", "RAd", sec["dash"])
    across("seat_back_bar", "Hm", "brace")
    frame.member("hoop_diag_L1", "LHm", "C2", sec["brace"])
    frame.member("hoop_diag_R2", "C2", "RH2", sec["brace"])
    frame.member("hoop_diag_R1", "RHm", "C2", sec["brace"])
    frame.member("hoop_diag_L2", "C2", "LH2", sec["brace"])
    frame.member("roof_diag_L1", "LH1", "C3", sec["brace"])
    frame.member("roof_diag_R2", "C3", "RH2", sec["brace"])
    frame.member("roof_diag_R1", "RH1", "C3", sec["brace"])
    frame.member("roof_diag_L2", "C3", "LH2", sec["brace"])
    geo = {"x_f": x_f, "x_r": x_r, "x_hoop": x_hoop, "x_roof": x_roof, "roof_z": roof_z, "cage_y": cage_y,
           "floor_top": floor_top, "occ_lo": occ_lo, "occ_hi": occ_hi, "deck_z": deck_z, "rf": rf, "rc": rc}
    return frame, geo


def _hardpoints(supports: list[Record], uz: float | None, dy: float | None) -> dict[str, dict]:
    from hiraku.solvers.k2_vehicle.model import DesignError, hardpoints_for

    out = {}
    for s in supports:
        try:
            out[s["id"]] = hardpoints_for(s, upper_joint_z_mm=uz, damper_upper_y_mm=dy)
        except DesignError as e:
            raise DesignGenError(str(e)) from None
    return out


def choose_hardpoints(v: dict[str, float], supports: list[Record], secs: dict) -> tuple[dict, float, float, float, str]:
    """The upper ball-joint height and the damper top's |y| nearest to the generic layout's that keep every
    arm, damper and tie rod ARM_CLEARANCE_MM clear of the frame through the travel: candidates in
    HARDPOINT_STEP_MM steps (up to HARDPOINT_STEPS each way, both moved), the smallest move first.
    Returns ({support: hardpoints}, upper_joint_z, damper_upper_y, least gap, where)."""
    base = _hardpoints(supports, None, None)
    fl = next(s for s in supports if s["id"] == "FL")
    uz0, dy0 = base["FL"]["uca_outer"][2], base["FL"]["damper_upper"][1]
    steps = range(-HARDPOINT_STEPS, HARDPOINT_STEPS + 1)
    cands = sorted(((i, j) for i in steps for j in steps), key=lambda ij: (abs(ij[0]) + abs(ij[1]), abs(ij[0]), ij))
    last = None
    for i, j in cands:
        uz, dy = uz0 + i * HARDPOINT_STEP_MM, dy0 + j * HARDPOINT_STEP_MM
        try:
            hp = base if (i, j) == (0, 0) else _hardpoints(supports, uz, dy)
        except DesignGenError as e:  # the linkage does not work there: try the next
            last = (math.nan, str(e))
            continue
        frame, _ = _frame(v, {"A": hp["FL"], "B": hp["RL"]}, secs)  # a layout that does not close stops here
        worst, where = math.inf, ""
        for corner, s in (("A", fl), ("B", next(x for x in supports if x["id"] == "RL"))):
            g, w = arm_clearance(frame, corner, hp[s["id"]], float(s["radius_mm"]),
                                 float(s["suspension"]["travel_mm"]))
            if g < worst:
                worst, where = g, w
        if worst >= ARM_CLEARANCE_MM:
            return hp, uz, dy, worst, where
        last = (worst, where)
    raise DesignGenError(f"no upper ball-joint height or damper top within {HARDPOINT_STEPS * HARDPOINT_STEP_MM:g} mm "
                         f"of the generic layout keeps the suspension {ARM_CLEARANCE_MM:g} mm clear of the frame "
                         f"(last try: {last[1]}, gap {last[0]:.1f} mm)")


# ---------------------------------------------------------------- checks the tests use


def occupant_clearance(design: Record) -> tuple[float, str]:
    """Least gap (mm) between the occupants' boxes and the frame tubes' surfaces, and where."""
    nodes = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
    best, where = math.inf, ""
    for p in design["parts"]:
        if p["kind"] != "occupant":
            continue
        c, s = np.asarray(p["box"]["center_mm"]), np.asarray(p["box"]["size_mm"])
        for m in design["members"]:
            r = design["sections"][m["section"]]["dims_mm"]["d"] / 2.0
            g = segment_box_distance(nodes[m["node_a"]], nodes[m["node_b"]], c - s / 2, c + s / 2) - r
            if g < best:
                best, where = g, f"{p['id']} and {m['id']}"
    return best, where


TYRE_STEER_SAMPLES = 9  # steer angles checked, evenly over +-max_steer_angle (steered wheels only)
TYRE_SEGMENT_SAMPLES = 80  # points along each tube's centre line (the best one refined by golden section)


def _cylinder_distance(p: np.ndarray, c: np.ndarray, axis: np.ndarray, r: float, half_w: float) -> np.ndarray:
    """Least distance from each point p (n, 3) to solid cylinders (centres c (k, 3), unit axes (k, 3), radius r,
    half width); 0 inside."""
    w = p[None, :, :] - c[:, None, :]
    h = np.einsum("kni,ki->kn", w, axis)
    rho = np.linalg.norm(w - h[:, :, None] * axis[:, None, :], axis=2)
    return np.hypot(np.maximum(rho - r, 0.0), np.maximum(np.abs(h) - half_w, 0.0)).min(axis=0)


def tyre_clearance(design: Record) -> tuple[float, str]:
    """Least gap (mm) between the frame tubes' surfaces and the tyres, and where. Each tyre is a solid
    cylinder (radius_mm, width_mm) at its wheel centre moved over +-travel/2 vertically (TRAVEL_SAMPLES) and,
    when steered, turned about the vertical through the wheel centre over +-max_steer_angle (assumption: the
    kingpin's offset, the camber change and the tyre's bulge are not modelled)."""
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    steer = float(design["variables"]["max_steer_angle"]["value"])
    best, where = math.inf, ""
    for s in design["supports"]:
        c0 = np.asarray(s["position_mm"], float)
        r, hw = float(s["radius_mm"]), float(s["width_mm"]) / 2.0
        travel = float(s["suspension"]["travel_mm"])
        angles = np.radians(np.linspace(-steer, steer, TYRE_STEER_SAMPLES)) if s["actuation"].get("steer") else [0.0]
        poses = [(c0 + [0.0, 0.0, dz], np.array([-math.sin(a), math.cos(a), 0.0]))
                 for dz in np.linspace(-travel / 2.0, travel / 2.0, TRAVEL_SAMPLES) for a in angles]
        C, AX = np.array([p for p, _ in poses]), np.array([q for _, q in poses])
        reach = math.hypot(r, hw) + travel / 2.0  # every pose lies within this of the wheel centre
        for m in design["members"]:
            a, b = nodes[m["node_a"]], nodes[m["node_b"]]
            rt = design["sections"][m["section"]]["dims_mm"]["d"] / 2.0
            if segment_distance(a, b, c0, c0) - reach - rt > best:
                continue  # cannot come closer than the least gap found so far

            def gap(t: np.ndarray) -> np.ndarray:
                p = a + np.asarray(t)[:, None] * (b - a)
                return _cylinder_distance(p, C, AX, r, hw) - rt

            ts = np.linspace(0.0, 1.0, TYRE_SEGMENT_SAMPLES)
            g = gap(ts)
            k = int(np.argmin(g))
            lo, hi = ts[max(k - 1, 0)], ts[min(k + 1, len(ts) - 1)]
            phi = (math.sqrt(5.0) - 1.0) / 2.0
            for _ in range(40):
                x1, x2 = hi - phi * (hi - lo), lo + phi * (hi - lo)
                if gap(np.array([x1]))[0] < gap(np.array([x2]))[0]:
                    hi = x2
                else:
                    lo = x1
            v = min(float(g[k]), float(gap(np.array([(lo + hi) / 2.0]))[0]))
            if v < best:
                best, where = v, f"tyre of {s['id']} and tube {m['id']}"
    return best, where


def connected(design: Record) -> bool:
    """True when the members join every node into one piece."""
    ids = [n["id"] for n in design["nodes"]]
    parent = {k: k for k in ids}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for m in design["members"]:
        parent[find(m["node_a"])] = find(m["node_b"])
    return len({find(k) for k in ids}) == 1


# ---------------------------------------------------------------- the design


def build(values: dict | None = None, *, version: int = 1, parent: int | None = None, reason: str | None = None,
          name: str | None = None) -> Record:
    """A design record from variables ({name: quantity or number}; the others take their defaults).
    Raises DesignGenError when a value is out of its range or the layout does not close."""
    v = _values(values)
    wb, track, r, ww = v["wheelbase"], v["track"], v["wheel_radius"], v["wheel_width"]
    secs = {"floor": tube(v["floor_tube_d"], v["floor_tube_t"]), "cage": tube(v["cage_tube_d"], v["cage_tube_t"]),
            "brace": tube(v["brace_tube_d"], v["brace_tube_t"]), "dash": tube(v["dash_tube_d"], v["dash_tube_t"])}

    def spring(rate: float) -> Suspension:
        return Suspension("kc_table", rate, 1.2, v["suspension_travel"], "builtin:double_wishbone")

    supports = four_wheels(wb, track, r, ww, spring(v["front_spring_rate"]), spring(v["rear_spring_rate"]),
                           front_station="A_lca_front", rear_station="B_lca_front")
    hp, uz, dy, gap, gap_where = choose_hardpoints(v, supports, secs)
    for s in supports:
        s["suspension"]["hardpoints_mm"] = {k: [float(x) for x in p] for k, p in hp[s["id"]].items()}
    frame, geo = _frame(v, {"A": hp["FL"], "B": hp["RL"]}, secs)
    for s in supports:  # the subframe nodes are the body side hardpoints
        side = "L" if s["position_mm"][1] > 0 else "R"
        ax = "A" if s["position_mm"][0] > -wb / 2 else "B"
        for k in ARM_KEYS:
            if np.linalg.norm(np.asarray(frame.nodes[f"{side}{ax}_{k}"]) - hp[s["id"]][k]) > 1e-6:
                raise DesignGenError(f"internal: node {side}{ax}_{k} is not at {s['id']}'s {k}")

    lay = Layout({**frame.part(), "supports": supports, "parts": []})
    rf, rb = geo["rf"], secs["brace"][1]["dims_mm"]["d"] / 2.0
    floor_top, x_f, x_r, x_hoop = geo["floor_top"], geo["x_f"], geo["x_r"], geo["x_hoop"]
    fy = v["floor_half_width"]
    seat_x, seat_y = v["seat_x"], v["seat_y"]
    floor_nodes = ["LP1", "RP1", "LP2", "RP2"]
    lay.put(Item("floor", "床 (前の横パイプから主フープまで)", "body", (x_f - x_hoop, 2 * fy, 20.0), 9.0),
            x=(x_f + x_hoop) / 2, y=0.0, z=bottom_at(floor_top - 20.0), carried_by=floor_nodes)
    # the battery under the seats, its size and mass with the capacity (ladder_4w's v1 pack)
    scale = v["battery_capacity"] / BATTERY_V1["capacity"]
    b_l = BATTERY_V1["length"] * scale
    lay.put(Item("battery", f"電池 (LiFePO4 {v['battery_voltage']:g} V {v['battery_capacity']:g} Ah、座席の下)",
                 "battery", (b_l, BATTERY_V1["width"], BATTERY_V1["height"]), BATTERY_V1["mass"] * scale),
            x=seat_x, y=0.0, z=bottom_at(floor_top), carried_by=floor_nodes + ["C1"])
    if seat_x + b_l / 2 > x_f - rf or seat_x - b_l / 2 < x_hoop + rf:
        raise DesignGenError("the battery under the seats does not fit between the front floor cross tube and the "
                             "main hoop; lower battery_capacity or move seat_x")
    batt_top = lay.top_of("battery")
    motor_mass = MOTOR_V1["mass"] * max(v["motor_peak_power"] / MOTOR_V1["power"],
                                        v["motor_peak_torque"] / MOTOR_V1["torque"])
    low_b = hp["RL"]["lca_front"][2]
    lay.put(Item("motor", "モーターと減速機 (後ろのサブフレームの下の横パイプの上)", "motor", (250.0, 300.0, 220.0),
                 motor_mass), x=-wb, y=0.0, z=bottom_at(low_b + rb + ON_TUBES_MM),
            carried_by=["LB_lca_front", "RB_lca_front", "LB_lca_rear", "RB_lca_rear"])
    lay.put(Item("controller", "モーターの制御器 (主フープの後ろの床)", "controller", CONTROLLER_SIZE, 4.0),
            x=(x_hoop + x_r) / 2, y=0.0, z=bottom_at(geo["floor_top"] - 20.0), carried_by=["LP2", "RP2", "LP3", "RP3"])
    low_a = hp["FL"]["lca_front"][2]
    lay.put(Item("aux", "配線と 12 V の補機 (前のサブフレームの中)", "electrical", AUX_SIZE, 6.0),
            x=0.0, y=0.0, z=bottom_at(low_a + rb + ON_TUBES_MM),
            carried_by=["LA_lca_front", "RA_lca_front", "LA_lca_rear", "RA_lca_rear"])
    top_a = hp["FL"]["damper_upper"][2]
    lay.put(Item("front_body", "前の外板とバンパー (前のダンパーの上)", "body", FRONT_BODY_SIZE, 15.0),
            x=hp["FL"]["lca_front"][0], y=0.0, z=bottom_at(top_a + rb + ON_TUBES_MM),
            carried_by=["LA_damper_upper", "RA_damper_upper", "LA_uca_front", "RA_uca_front"])
    # the steering hangs below the dash bar, its rear face a 95th percentile knee length ahead of the
    # seat back (K4's knee_room: AIST item J5, hiraku.solvers.k4_geometry.people)
    from hiraku.solvers.k4_geometry.people import AIST

    ad = frame.nodes["LAd"]
    rd = secs["dash"][1]["dims_mm"]["d"] / 2.0
    steer_rear = seat_x - SEAT_SIZE[0] / 2 + AIST["buttock_knee_m95"] + KNEE_MARGIN_MM
    lay.put(Item("steering", "ハンドルとペダル (ダッシュの横パイプから吊る)", "steering", STEERING_SIZE, 12.0),
            x=steer_rear + STEERING_SIZE[0] / 2, y=-seat_y, z=top_at(ad[2] - rd - ON_TUBES_MM),
            carried_by=["RAd", "D0"])
    for pid, label, y in (("seat_R", "運転席", -seat_y), ("seat_L", "助手席", seat_y)):
        lay.put(Item(pid, label, "seat", SEAT_SIZE, 12.0), x=seat_x, y=y, z=bottom_at(batt_top),
                carried_by=floor_nodes)
    for pid, label, y in (("occupant_R", "運転者 (体重は強さの不利側)", -seat_y), ("occupant_L", "同乗者 (体重は強さの不利側)", seat_y)):
        lay.put(Item(pid, label, "occupant", OCCUPANT_SIZE, v["occupant_mass"]), x=seat_x - OCCUPANT_BACK_MM, y=y,
                z=bottom_at(lay.top_of(f"seat_{pid[-1]}")), carried_by=floor_nodes)
    # the deck on the deck rails, from behind the hoop to the rear subframe's end
    deck_front = x_hoop - geo["rc"] - DECK_GAP_MM
    deck_rear = hp["RL"]["lca_rear"][0]
    deck_w = 2 * hp["RL"]["damper_upper"][1]
    deck_nodes = ["LHm", "RHm", "LB_damper_upper", "RB_damper_upper"]
    lay.put(Item("deck", "荷台の床 (荷台のレールの上)", "body", (deck_front - deck_rear, deck_w, 20.0), 14.0),
            x=(deck_front + deck_rear) / 2, y=0.0, z=bottom_at(geo["deck_z"] + rb + ON_TUBES_MM), carried_by=deck_nodes)
    lay.put(Item("cargo", "荷", "cargo", (deck_front - deck_rear - 50.0, deck_w, v["cargo_height"]), v["cargo_mass"]),
            x=(deck_front + deck_rear) / 2, y=0.0, z=bottom_at(lay.top_of("deck")), carried_by=deck_nodes)
    for s in supports:
        x, y, z = s["position_mm"]
        corner = corner_name(s, supports)
        lay.put(Item(f"wheel_{s['id']}", f"{corner}の車輪 (タイヤ・ホイール・ハブ・ブレーキ)", "wheel", (2 * r, ww, 2 * r),
                     WHEEL_MASS_KG), x=x, y=y, z=z, carried_by=None)
        h = hp[s["id"]]
        mid = (np.asarray(h["lca_outer"]) + np.asarray(h["uca_outer"])) / 2
        lay.put(Item(f"susp_{s['id']}", f"{corner}のサスペンション (アーム・ナックル・ダンパー)", "suspension", SUSP_SIZE,
                     SUSP_MASS_KG), x=x, y=float(mid[1]) - math.copysign(SUSP_SIZE[1] / 2, y), z=float(mid[2]),
                carried_by=None)
    material = json.loads(MATERIALS.read_text(encoding="utf-8"))[MATERIAL]

    variables = {k: _q(v[k], VARIABLES[k].unit) for k in VARIABLES}
    variables["tyre_relaxation_length"] = _q(r, "mm")
    variables["wheel_inertia"] = _q(WHEEL_MASS_KG * (r / 1000.0) ** 2 / 2.0, "kg*m^2")
    variables["upper_joint_z"] = _q(float(uz), "mm")
    variables["damper_upper_y"] = _q(float(dy), "mm")
    return {
        "design_version": version,
        "parent_version": parent,
        "name": name or f"utv-spaceframe v{version}: {DESCRIPTION}",
        "change_reason": reason or "first version (space frame from its variables)",
        "variables": variables,
        "supports": supports,
        "parts": lay.parts,
        **frame.part(),
        "materials": {MATERIAL: material},
    }


__all__ = ["CHOICES", "DESCRIPTION", "SIGNATURE", "TITLE", "VARIABLES", "applies", "apply_choices", "arm_clearance",
           "build", "choices_of", "choose_hardpoints", "connected", "links_through_travel", "occupant_clearance",
           "segment_box_distance", "segment_distance", "spec", "tube", "tyre_clearance", "values_of"]

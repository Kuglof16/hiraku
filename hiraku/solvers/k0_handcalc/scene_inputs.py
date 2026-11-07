"""Inputs of the K0 formulas of the assembly, people and launch tests, read off a design by code.

Each returns {"inputs": {formula input name: {"value", "unit"}}, ...} (the shape of
frame_inputs.ladder_twist_inputs), with what it picked beside the inputs:

- steer_room_inputs(design): geometry.free_steer_angle (H1), w, l and c of the steered
  wheel that reaches the frame first (k4_geometry.steer_room).
- knee_space_inputs(design, loading): occupant.knee_room (H3), L (seat back to the nearest
  part ahead at knee height, the shortest over the occupants on board) and L_bk (AIST J5,
  k4_geometry.AIST).
- launch_inputs(design, loading, params): launch.peak_tractive_force (P3) T, i, eta and r
  of the driven wheels (r the loaded radius, loaded_radius), and of launch.time_to_speed / launch.top_speed (P2, P3) the motor
  power P, eta, C_rr, C_d, A and rho, as the K2 launch tool takes them (load case params,
  else design variables, else its declared defaults). m, grade, lambda and v are not read
  here: other_inputs names where they come from.

A design these cannot read raises DesignError (a ValueError) saying what is missing.
"""

from __future__ import annotations

from ...contract.loading import on_board
from .units import quantity
from .vehicle import DesignError


def steer_room_inputs(design: dict) -> dict:
    """geometry.free_steer_angle's w, l, c for the steered wheel with the smallest free steer angle."""
    from ..k4_geometry import steer_room
    from .library_geometry import _free_steer

    rooms = steer_room(design)
    if not rooms:
        raise DesignError("no steered wheel has a frame member beside it")
    angle = {sid: _free_steer(r)["theta"] for sid, r in rooms.items()}
    sid = min(rooms, key=angle.get)
    r = rooms[sid]
    return {
        "inputs": {"w": quantity(float(r["w"]), "mm"), "l": quantity(float(r["l"]), "mm"),
                   "c": quantity(float(r["c"]), "mm")},
        "support": sid,
        "supports": {k: {"w_mm": float(v["w"]), "l_mm": float(v["l"]), "c_mm": float(v["c"]),
                         "free_steer_angle_deg": float(angle[k])} for k, v in rooms.items()},
    }


def knee_space_inputs(design: dict, loading: str | None = None) -> dict:
    """occupant.knee_room's L (the shortest over the occupants on board for `loading`) and L_bk."""
    from ..k4_geometry import AIST, knee_space

    space = knee_space(on_board(design, loading) if loading is not None else design)
    if not space:
        raise DesignError("no occupant has a part ahead of the knees")
    oid = min(space, key=lambda k: space[k][0])
    length, part = space[oid]
    return {
        "inputs": {"L": quantity(float(length), "mm"), "L_bk": quantity(float(AIST["buttock_knee_m95"]), "mm")},
        "occupant": oid,
        "part_ahead": part,
        "occupants": {k: {"L_mm": float(v[0]), "part_ahead": v[1]} for k, v in space.items()},
        "source": "L_bk: AIST J5 buttock-knee length, sitting, young adult male 95th percentile (k4_geometry.people)",
    }


def knee_angle_inputs(design: dict, loading: str | None = None) -> dict:
    """occupant.knee_angle_room's inputs for the driver (the occupant with a pedal; the one with the smallest
    margin when there are more): both body sizes' thigh and shank lengths, their knee angles as K4 seats them
    (people.poses, each at their best place in the seat's slide) and the comfortable range."""
    from ..k4_geometry import people

    on = on_board(design, loading) if loading is not None else design
    angles = people.knee_angles(on)
    if not angles:
        raise DesignError("no occupant has a pedal: there is no driver's knee angle")
    fit = people.fit(on)
    oid = min(angles, key=lambda o: fit.get(f"knee_angle_room.{o}", (0.0, ""))[0])
    out = {"theta_lo": quantity(people.KNEE_COMFORT_DEG[0], "deg"),
           "theta_hi": quantity(people.KNEE_COMFORT_DEG[1], "deg")}
    for k, size in (("big", people.BIG), ("small", people.SMALL)):
        l1, l2 = people._links(people.SIZES[size])
        out.update({f"l1_{k}": quantity(l1, "mm"), f"l2_{k}": quantity(l2, "mm"),
                    f"theta_{k}": quantity(angles[oid][k], "deg")})
    return {"inputs": out, "occupant": oid, "source": people.KNEE_COMFORT_SOURCE}


LAUNCH_OTHER_INPUTS = {
    "m": "total mass on board for the load case's loading (design summary, loadings.<loading>.mass)",
    "grade": "load case param grade (%)",
    "v": "load case param target_speed (km/h)",
    "lambda": "design variable mass_factor",
    "F_max": "the prediction of launch.peak_tractive_force (output F_max) from T, i, eta, r here",
}


LOADED_RADIUS_ASSUMPTION = ("駆動輪の半径は荷重でたわんだ半径 (自由半径 − 止まっているときの輪の荷重 / タイヤの縦のばね "
                            "tyre_stiffness、駆動輪の平均)")
# Why the demo designs' tyre_stiffness is 60 N/mm (its source: the basis of design.variables.tyre_stiffness,
# hiraku/designgen/ladder_4w.py TYRE_STIFFNESS_BASIS); written into the assumptions of the records whose
# predictions use tyre_stiffness
TYRE_STIFFNESS_NOTE = ("タイヤの縦のばね tyre_stiffness 60 N/mm は、ATV のタイヤの 3 つの資料から読んだ幅 45〜90 N/mm の"
                       "中ほど (出どころの無かった 150 N/mm、そのあと 80 N/mm を置き換えたもの)。段差の当たりを判定する所では、"
                       "幅の両端 45 と 90 N/mm でも出す")
FREE_RADIUS_ASSUMPTION = ("駆動輪の半径は自由半径 (設計にタイヤの縦のばね tyre_stiffness が無いので、荷重でたわんだ半径を"
                          "出せない。たわみの分だけ F_max を小さめに出す)")


def loaded_radius(design: dict, loading: str | None, free_mm: dict[str, float]) -> dict:
    """The driven wheels' radius for launch.peak_tractive_force: each wheel's free radius (free_mm, by support
    id) less its tyre's deflection at rest, W / k_t (W the ground force at the support from K0 statics,
    support_loads, at the loading; k_t the design variable tyre_stiffness, N/mm), averaged. Without
    tyre_stiffness the free radius. Returns {"r_mm", "free_r_mm", "kind" ("loaded" or "free"),
    "deflection_mm" by support, "assumption"}."""
    from .vehicle import support_loads

    free = sum(free_mm.values()) / len(free_mm)
    kt = (design.get("variables") or {}).get("tyre_stiffness")
    if not kt or kt.get("unit") != "N/mm" or float(kt["value"]) <= 0:
        return {"r_mm": free, "free_r_mm": free, "kind": "free", "deflection_mm": {},
                "assumption": FREE_RADIUS_ASSUMPTION}
    on = on_board(design, loading) if loading is not None else design
    ground = support_loads(on)["supports"]
    defl = {sid: float(ground[sid]["force_N"][2]) / float(kt["value"]) for sid in free_mm}
    r = sum(free_mm[sid] - defl[sid] for sid in free_mm) / len(free_mm)
    return {"r_mm": r, "free_r_mm": free, "kind": "loaded", "deflection_mm": defl,
            "assumption": LOADED_RADIUS_ASSUMPTION}


def launch_inputs(design: dict, loading: str | None = None, params: dict | None = None) -> dict:
    """The K2 launch tool's motor and road load for launch.peak_tractive_force and launch.time_to_speed /
    top_speed: T (motor_torque), i (gear_ratio), eta (efficiency), r (the driven wheels' radius under load,
    loaded_radius: K2's corner radius less the tyre's deflection at rest; the free radius when the design
    has no tyre_stiffness, as radius["assumption"] says), P (motor_power), C_rr (the first wheel's rolling
    resistance), C_d, A (front), rho. `params`: the load case params (they come first, as in the launch
    tool)."""
    from ..k2_vehicle.drive import on_board as k2_on_board
    from ..k2_vehicle.model import build_vehicle
    from ..k2_vehicle.shelf import LaunchTool

    lp = dict(params or {})
    if loading is None:
        loading = lp.get("loading")
    veh = build_vehicle(k2_on_board(design, loading))
    tool = LaunchTool()
    tool._vars = design.get("variables") or {}
    motor = tool.motor(lp)
    aero = tool.aero(veh, lp)
    driven = [c for c in veh.corners if c.drive]
    if not driven:
        raise DesignError("no driven wheel: launch needs supports with actuation.drive")
    radius = loaded_radius(design, loading, {c.id: float(c.radius) * 1000.0 for c in driven})
    r = radius["r_mm"] / 1000.0  # m
    return {
        "inputs": {
            "T": quantity(float(motor.torque), "N*m"), "i": quantity(float(motor.gear), "1"),
            "eta": quantity(float(motor.efficiency), "1"), "rolling_radius": quantity(float(r) * 1000.0, "mm"),
            "P": quantity(float(motor.power) / 1000.0, "kW"), "C_rr": quantity(float(veh.corners[0].c_rr), "1"),
            "C_d": quantity(float(aero.cd), "1"), "A": quantity(float(aero.front_area), "m^2"),
            "rho": quantity(float(aero.rho), "kg/m^3"),
        },
        "driven_wheels": [c.id for c in driven],
        "radius": radius,
        "other_inputs": dict(LAUNCH_OTHER_INPUTS),
        "assumed": list(tool.__dict__.get("_assumed", [])),
    }

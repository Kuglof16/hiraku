"""Inputs of the K0 formulas of the assembly, people and launch tests, read off a design by code.

Each returns {"inputs": {formula input name: {"value", "unit"}}, ...} (the shape of
frame_inputs.ladder_twist_inputs), with what it picked beside the inputs:

- steer_room_inputs(design): geometry.free_steer_angle (H1), w, l and c of the steered
  wheel that reaches the frame first (k4_geometry.steer_room).
- knee_space_inputs(design, loading): occupant.knee_room (H3), L (seat back to the nearest
  part ahead at knee height, the shortest over the occupants on board) and L_bk (AIST J5,
  k4_geometry.AIST).
- launch_inputs(design, loading, params): launch.peak_tractive_force (P3) T, i, eta and r
  of the driven wheels, and of launch.time_to_speed / launch.top_speed (P2, P3) the motor
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


LAUNCH_OTHER_INPUTS = {
    "m": "total mass on board for the load case's loading (design summary, loadings.<loading>.mass)",
    "grade": "load case param grade (%)",
    "v": "load case param target_speed (km/h)",
    "lambda": "design variable mass_factor",
    "F_max": "the prediction of launch.peak_tractive_force (output F_max) from T, i, eta, r here",
}


def launch_inputs(design: dict, loading: str | None = None, params: dict | None = None) -> dict:
    """The K2 launch tool's motor and road load for launch.peak_tractive_force and launch.time_to_speed /
    top_speed: T (motor_torque), i (gear_ratio), eta (efficiency), r (mean radius of the driven wheels,
    K2's corner radius), P (motor_power), C_rr (the first wheel's rolling resistance), C_d, A (front),
    rho. `params`: the load case params (they come first, as in the launch tool)."""
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
    r = sum(c.radius for c in driven) / len(driven)  # m
    return {
        "inputs": {
            "T": quantity(float(motor.torque), "N*m"), "i": quantity(float(motor.gear), "1"),
            "eta": quantity(float(motor.efficiency), "1"), "r": quantity(float(r) * 1000.0, "mm"),
            "P": quantity(float(motor.power) / 1000.0, "kW"), "C_rr": quantity(float(veh.corners[0].c_rr), "1"),
            "C_d": quantity(float(aero.cd), "1"), "A": quantity(float(aero.front_area), "m^2"),
            "rho": quantity(float(aero.rho), "kg/m^3"),
        },
        "driven_wheels": [c.id for c in driven],
        "other_inputs": dict(LAUNCH_OTHER_INPUTS),
        "assumed": list(tool.__dict__.get("_assumed", [])),
    }

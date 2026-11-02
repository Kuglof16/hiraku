"""A two-seat electric off-road vehicle on a ladder frame, built from its variables.

Four wheels (front steer, rear drive), a ladder frame of rectangular steel
tube, right-hand drive: the driver sits on the right and is the first
occupant in the parts list (a load case's params.loading boards occupants in
list order). Part boxes are mass-equivalent boxes centred at each part's
estimated centre of gravity; parts without carried_by rest on the nearest
wheel (unsprung).

Frame stations along x (x forward, origin under the front axle): F front end,
A front axle, S1 and S2 the cross members at the front and back of the seats
(seat_x +- 275 mm), B rear axle, E rear end. Dimensions, masses and the other
variables' defaults are design choices (assumptions) unless a comment gives a
source; the defaults build data/designs/offroad-4w-v1.json.

Occupant and cargo masses are requirements (Q33, Q40), not design choices:
the default occupant mass is AIST anthropometric database 1991-92 item N1
Weight, young adult male 95th percentile (source: read from
https://www.airc.aist.go.jp/dhrt/91-92/data/list.html on 2026-11-03).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Record = dict[str, Any]

MATERIALS = Path(__file__).resolve().parents[2] / "data" / "materials" / "materials.json"
MIN_STATION_GAP_MM = 100.0  # stations closer than this would make members shorter than the joints (assumption)


class DesignGenError(ValueError):
    """The variables do not make a vehicle: out of range, or parts that cannot be placed."""


@dataclass(frozen=True)
class Var:
    unit: str
    default: float
    lo: float | None = None  # None: not a design choice (a requirement or a fixed value)
    hi: float | None = None
    meaning: str = ""

    @property
    def revisable(self) -> bool:
        return self.lo is not None


# Variables the design may change (with their ranges) and fixed values carried along.
VARIABLES: dict[str, Var] = {
    # layout
    "wheelbase": Var("mm", 1700.0, 1400.0, 2200.0, "front axle to rear axle"),
    "track": Var("mm", 1100.0, 900.0, 1400.0, "wheel centre to wheel centre across"),
    "wheel_radius": Var("mm", 300.0, 200.0, 380.0, "tyre radius"),
    "wheel_width": Var("mm", 200.0, 120.0, 280.0, "tyre width"),
    "rail_spacing": Var("mm", 700.0, 500.0, 900.0, "frame rail centre to centre across"),
    "rail_height": Var("mm", 380.0, 300.0, 500.0, "height of the rail centre line"),
    "front_overhang": Var("mm", 400.0, 250.0, 700.0, "frame ahead of the front axle"),
    "rear_overhang": Var("mm", 400.0, 200.0, 700.0, "frame behind the rear axle"),
    "rail_b": Var("mm", 40.0, 30.0, 80.0, "rail tube width"),
    "rail_h": Var("mm", 80.0, 50.0, 150.0, "rail tube height"),
    "rail_t": Var("mm", 2.3, 1.6, 4.5, "rail tube wall"),
    "cross_b": Var("mm", 40.0, 30.0, 80.0, "cross member tube width"),
    "cross_h": Var("mm", 40.0, 30.0, 120.0, "cross member tube height"),
    "cross_t": Var("mm", 2.3, 1.6, 4.5, "cross member tube wall"),
    # people and parts
    "seat_x": Var("mm", -725.0, -1100.0, -500.0, "seat centre (x); the seat cross members are 275 mm ahead and behind"),
    "seat_y": Var("mm", 300.0, 220.0, 400.0, "seat centre off the centre line, each side"),
    "steering_x": Var("mm", -250.0, -400.0, 0.0, "centre of the steering column and pedal box (x)"),
    "battery_x": Var("mm", -725.0, -1300.0, -300.0, "battery centre (x); it hangs from the cross members either side "
                     "and must fit between them (seat_x moves the seat cross members)"),
    # chassis
    "front_spring_rate": Var("N/mm", 20.0, 5.0, 60.0, "front suspension spring rate per wheel"),
    "rear_spring_rate": Var("N/mm", 20.0, 5.0, 60.0, "rear suspension spring rate per wheel"),
    "suspension_travel": Var("mm", 150.0, 80.0, 250.0, "wheel travel, bump to rebound"),
    "max_steer_angle": Var("deg", 30.0, 15.0, 40.0, "steering lock each way"),
    # requirements (not design choices)
    "max_speed": Var("km/h", 40.0, meaning="Q50"),
    "occupants": Var("1", 2.0, meaning="seats"),
    "occupant_mass": Var("kg", 76.6, meaning="Q33; source: AIST item N1 Weight, male 95th percentile (see the docstring)"),
    "cargo_mass": Var("kg", 150.0, meaning="Q40"),
    # powertrain and battery: a 51.2 V LiFePO4 pack (16 cells at 3.2 V, taken as the nominal
    # voltage of a LiFePO4 cell, not checked against a cell data sheet in this repository),
    # a 10 kW peak motor driving the rear axle through a reduction
    "motor_peak_power": Var("kW", 10.0, 5.0, 20.0, "motor peak power (the motor's mass goes with it)"),
    "motor_peak_torque": Var("N*m", 50.0, 30.0, 120.0, "motor peak torque (the motor's mass goes with it)"),
    "motor_max_speed": Var("rpm", 6000.0, 3000.0, 10000.0, "motor speed limit (with gear_ratio and the wheel radius it "
                           "sets the top speed)"),
    "gear_ratio": Var("1", 10.0, 6.0, 14.0, "reduction from motor to wheel"),
    "drivetrain_efficiency": Var("1", 0.95, meaning="assumption"),
    "motor_efficiency": Var("1", 0.88, meaning="assumption"),
    "controller_efficiency": Var("1", 0.96, meaning="assumption"),
    "regen_efficiency": Var("1", 0.5, meaning="assumption"),
    "aux_power": Var("W", 150.0, meaning="assumption"),
    "battery_voltage": Var("V", 51.2, meaning="16 x 3.2 V, not checked (see above)"),
    "battery_capacity": Var("Ah", 100.0, 50.0, 200.0, "battery capacity (its mass and length go with it)"),
    "battery_resistance": Var("ohm", 0.02, meaning="assumption"),
    "mass_factor": Var("1", 1.05, meaning="assumption: the turning parts add 5 % to the mass in acceleration"),
    "rolling_resistance": Var("1", 0.03, meaning="assumption"),
    # lumped heat capacity and thermal resistance to the air (assumptions, no fan or liquid loop)
    "battery_heat_capacity": Var("J/K", 50000.0, meaning="assumption"),
    "battery_thermal_resistance": Var("K/W", 0.25, meaning="assumption"),
    "motor_heat_capacity": Var("J/K", 11000.0, meaning="assumption"),
    "motor_thermal_resistance": Var("K/W", 0.06, meaning="assumption"),
    "controller_heat_capacity": Var("J/K", 3600.0, meaning="assumption"),
    "controller_thermal_resistance": Var("K/W", 0.1, meaning="assumption"),
    # temperature limits of the parts (not checked in this repository):
    # source: EVE LF50K product specification, cells up to 55 degC,
    # https://www.rcscomponents.kiev.ua/datasheets/LF50K32V50AhProduct%20Specification.pdf
    "battery_max_temperature": Var("degC", 55.0, meaning="cell limit, EVE LF50K product specification (not checked)"),
    # source: Curtis 1236 manual, output cut back from 85 degC at the heatsink,
    # https://www.bintelli.com/wp-content/uploads/2023/06/Curtis-Controller-1236-Manual.pdf
    "controller_max_temperature": Var("degC", 85.0, meaning="cutback temperature, Curtis 1236 manual (not checked)"),
    # source: thermal class F (155 degC) of IEC 60085 for the winding insulation
    "motor_max_temperature": Var("degC", 155.0, meaning="insulation class F, IEC 60085 (not checked)"),
    # aerodynamics (assumptions; the drag coefficient is set high because the seats are open)
    "drag_coefficient": Var("1", 0.8, meaning="assumption"),
    "frontal_area": Var("m^2", 1.6, meaning="assumption"),
    "side_force_coefficient": Var("1", 1.0, meaning="assumption"),
    "side_area": Var("m^2", 2.4, meaning="assumption"),
    "centre_of_pressure_height": Var("mm", 800.0, meaning="assumption"),
    # source: Manual of the ICAO Standard Atmosphere (ICAO Doc 7488), sea level; not checked in this repository
    "air_density": Var("kg/m^3", 1.225, meaning="ICAO standard atmosphere, sea level"),
    # tyre cornering stiffness per axle, both tyres (assumptions)
    "front_cornering_stiffness": Var("N/rad", 25000.0, meaning="assumption"),
    "rear_cornering_stiffness": Var("N/rad", 30000.0, meaning="assumption"),
    # front against a rigid wall: a rubber bumper, then a crush zone in the front overhang
    "bumper_stiffness": Var("N/mm", 100.0, meaning="assumption"),
    "bumper_stroke": Var("mm", 80.0, 20.0, 150.0, "rubber bumper stroke"),
    "crush_stiffness": Var("N/mm", 600.0, 200.0, 1500.0, "crush zone stiffness"),
    # fatigue of the welded joints: the S-N form (slope 3, knee at 1e7 cycles) follows
    # A. F. Hobbacher, Recommendations for Fatigue Design of Welded Joints and Components,
    # 2nd ed., Springer 2016 (IIW), not checked in this repository; FAT 71 for the
    # fillet-welded tube joints and the slope 22 beyond the knee are assumptions
    "fatigue_fat_class": Var("MPa", 71.0, meaning="assumption (see above)"),
    "fatigue_slope": Var("1", 3.0, meaning="IIW form, not checked"),
    "fatigue_knee_cycles": Var("1", 1e7, meaning="IIW form, not checked"),
    "fatigue_slope_after_knee": Var("1", 22.0, meaning="assumption"),
    "tyre_stiffness": Var("N/mm", 150.0, meaning="assumption"),
    # tyre and suspension details of the 3D vehicle model (K2), assumptions with no source
    "tyre_damping_ratio": Var("1", 0.05, meaning="assumption: vertical damping ratio of the tyre"),
    "tyre_slip_stiffness_coeff": Var("1", 10.0, meaning="assumption: longitudinal slip stiffness per unit slip, "
                                     "as a multiple of the wheel's static load"),
    "bump_stop_factor": Var("1", 10.0, meaning="assumption: bump and rebound stop stiffness, as a multiple of the spring"),
}
# Written into design.variables by build() from the other variables (not inputs).
DERIVED = {
    "tyre_relaxation_length": ("mm", "assumption: the tyre's relaxation length, taken as the wheel radius"),
    "wheel_inertia": ("kg*m^2", "rotary inertia of one wheel part, as a solid disc: m r^2 / 2"),
}
WHEEL_MASS_KG = 14.0  # tyre, rim, hub and brake of one wheel (assumption)
# Order of design.variables as in the first v1 file (the variables added later come after them).
_V1_ORDER = ["wheelbase", "track", "wheel_radius", "wheel_width", "rail_spacing", "rail_height", "front_overhang",
             "rear_overhang", "max_speed", "occupants", "occupant_mass", "cargo_mass", "motor_peak_power",
             "motor_peak_torque", "motor_max_speed", "gear_ratio", "drivetrain_efficiency", "motor_efficiency",
             "controller_efficiency", "regen_efficiency", "aux_power", "battery_voltage", "battery_capacity",
             "battery_resistance", "mass_factor", "rolling_resistance", "battery_heat_capacity",
             "battery_thermal_resistance", "motor_heat_capacity", "motor_thermal_resistance", "controller_heat_capacity",
             "controller_thermal_resistance", "drag_coefficient", "frontal_area", "side_force_coefficient", "side_area",
             "centre_of_pressure_height", "air_density", "front_cornering_stiffness", "rear_cornering_stiffness",
             "max_steer_angle", "bumper_stiffness", "bumper_stroke", "crush_stiffness", "fatigue_fat_class", "fatigue_slope",
             "fatigue_knee_cycles", "fatigue_slope_after_knee", "tyre_stiffness"]
SEAT_HALF_MM = 275.0  # seat cross members ahead and behind the seat centre (assumption)
BATTERY_V1 = {"length": 500.0, "width": 600.0, "height": 200.0, "mass": 50.0, "capacity": 100.0}  # the v1 pack (assumption)
# The v1 motor and reducer (assumption): a bigger motor weighs more, in proportion to
# whichever of its peak power and peak torque grows more.
MOTOR_V1 = {"mass": 25.0, "power": 10.0, "torque": 50.0}


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


def _q(v: float, unit: str) -> Record:
    return {"value": v, "unit": unit}


def _tube(b: float, h: float, t: float) -> tuple[str, Record]:
    return f"rt{b:g}x{h:g}x{t:g}", {"shape": "rect_tube", "dims_mm": {"b": b, "h": h, "t": t}}


def build(values: dict | None = None, *, version: int = 1, parent: int | None = None, reason: str | None = None,
          name: str | None = None) -> Record:
    """A design record from variables ({name: quantity or number}; the others take their defaults).

    Raises DesignGenError when a value is out of its range or the layout does not close
    (stations out of order, rails over the wheels). Interference of the parts is left to K4.
    """
    v = _values(values)
    wb, track, r, ww = v["wheelbase"], v["track"], v["wheel_radius"], v["wheel_width"]
    rail_y, rail_z = v["rail_spacing"] / 2.0, v["rail_height"]
    stations = {"F": v["front_overhang"], "A": 0.0, "S1": v["seat_x"] + SEAT_HALF_MM, "S2": v["seat_x"] - SEAT_HALF_MM,
                "B": -wb, "E": -wb - v["rear_overhang"]}
    for (a, xa), (b, xb) in zip(list(stations.items()), list(stations.items())[1:]):
        if xa - xb < MIN_STATION_GAP_MM:
            raise DesignGenError(f"frame stations {a} ({xa:g} mm) and {b} ({xb:g} mm) are closer than "
                                 f"{MIN_STATION_GAP_MM:g} mm; move seat_x or change the wheelbase")
    if rail_y + v["rail_b"] / 2.0 >= track / 2.0 - ww / 2.0:
        raise DesignGenError("the rails reach the wheels: rail_spacing + rail_b must be less than track - wheel_width")

    def node(side, st):
        return f"{side}{st}"

    nodes = [{"id": node(side, st), "xyz_mm": [x, y, rail_z]} for side, y in (("L", rail_y), ("R", -rail_y))
             for st, x in stations.items()]
    rail_key, rail_sec = _tube(v["rail_b"], v["rail_h"], v["rail_t"])
    cross_key, cross_sec = _tube(v["cross_b"], v["cross_h"], v["cross_t"])
    order = list(stations)
    members = [{"id": f"rail_{side}_{a}{b}", "node_a": node(side, a), "node_b": node(side, b),
                "section": rail_key, "material": "stkr400", "part": "frame"}
               for side in "LR" for a, b in zip(order, order[1:])]
    members += [{"id": f"cross_{st}", "node_a": node("L", st), "node_b": node("R", st),
                 "section": cross_key, "material": "stkr400", "part": "frame"} for st in order]

    def wheel(sid, x, y, front):
        rate = v["front_spring_rate"] if front else v["rear_spring_rate"]
        return {
            "id": sid, "kind": "wheel", "position_mm": [x, y, r], "radius_mm": r, "width_mm": ww,
            "suspension": {"type": "spring", "stiffness_N_per_mm": rate, "damping_Ns_per_mm": 1.2,
                           "travel_mm": v["suspension_travel"]},
            "actuation": {"drive": not front, "brake": True, "steer": front},
            "frame_node": node("L" if y > 0 else "R", "A" if front else "B"),
        }

    supports = [wheel("FL", 0.0, track / 2, True), wheel("FR", 0.0, -track / 2, True),
                wheel("RL", -wb, track / 2, False), wheel("RR", -wb, -track / 2, False)]

    def part(pid, label, kind, center, size, mass, carried_by=None):
        p = {"id": pid, "name": label, "kind": kind, "box": {"center_mm": center, "size_mm": size}, "mass_kg": mass}
        if carried_by:
            p["carried_by"] = carried_by
        return p

    def between(x):
        """The frame nodes of the two stations either side of x (both rails)."""
        ahead = min((st for st in order if stations[st] >= x), key=lambda st: stations[st], default=order[0])
        behind = max((st for st in order if stations[st] < x), key=lambda st: stations[st], default=order[-1])
        return [node("L", ahead), node("R", ahead), node("L", behind), node("R", behind)]

    S1, S2, E, F = stations["S1"], stations["S2"], stations["E"], stations["F"]
    seat_x, seat_y = v["seat_x"], v["seat_y"]
    # the battery fits between the rails; its volume and mass go with the capacity
    scale = v["battery_capacity"] / BATTERY_V1["capacity"]
    b_w = min(BATTERY_V1["width"], v["rail_spacing"] - v["rail_b"] - 20.0)
    b_l = BATTERY_V1["length"] * BATTERY_V1["width"] / b_w * scale
    deck_front = S2 - 150.0
    parts = [
        part("battery", f"電池 (LiFePO4 {v['battery_voltage']:g} V {v['battery_capacity']:g} Ah)", "battery",
             [v["battery_x"], 0.0, 330.0], [b_l, b_w, BATTERY_V1["height"]], BATTERY_V1["mass"] * scale, between(v["battery_x"])),
        part("motor", "モーターと減速機", "motor", [-wb + 150.0, 0.0, 310.0], [250.0, 300.0, 220.0],
             MOTOR_V1["mass"] * max(v["motor_peak_power"] / MOTOR_V1["power"], v["motor_peak_torque"] / MOTOR_V1["torque"]),
             [node("L", "B"), node("R", "B")]),
        part("controller", "モーターの制御器", "controller", [S2 - 110.0, 0.0, 300.0], [250.0, 200.0, 80.0], 4.0,
             [node("L", "S2"), node("R", "S2")]),
        part("aux", "配線と 12 V の補機", "electrical", [S1 + 150.0, 0.0, 300.0], [300.0, 300.0, 100.0], 6.0,
             [node("L", "A"), node("R", "A"), node("L", "S1"), node("R", "S1")]),
        part("floor_front", "足元の床", "body", [S1 / 2.0, 0.0, 440.0], [-S1, 600.0, 20.0], 6.0,
             [node("L", "A"), node("R", "A"), node("L", "S1"), node("R", "S1")]),
        part("floor", "座席の下の床", "body", [(S1 + S2) / 2.0, 0.0, 440.0], [S1 - S2, track, 20.0], 8.0,
             [node("L", "S1"), node("R", "S1"), node("L", "S2"), node("R", "S2")]),
        part("deck", "荷台の床", "body", [(E + deck_front) / 2.0, 0.0, 440.0], [deck_front - E, 880.0, 20.0], 18.0,
             [node("L", "S2"), node("R", "S2"), node("L", "B"), node("R", "B"), node("L", "E"), node("R", "E")]),
        part("front_body", "前の外板とバンパー", "body", [F - 180.0, 0.0, 570.0], [360.0, 600.0, 280.0], 15.0,
             [node("L", "F"), node("R", "F"), node("L", "A"), node("R", "A")]),
        part("steering", "ハンドルとペダル", "steering", [v["steering_x"], -seat_y + 100.0, 750.0], [300.0, 200.0, 500.0], 12.0,
             [node("R", "A"), node("R", "S1")]),
        part("seat_R", "運転席", "seat", [seat_x, -seat_y, 580.0], [500.0, 500.0, 260.0], 12.0,
             [node("R", "S1"), node("R", "S2")]),
        part("seat_L", "助手席", "seat", [seat_x, seat_y, 580.0], [500.0, 500.0, 260.0], 12.0,
             [node("L", "S1"), node("L", "S2")]),
        part("occupant_R", "運転者 (体重は強さの不利側)", "occupant", [seat_x - 55.0, -seat_y, 970.0], [450.0, 450.0, 520.0],
             v["occupant_mass"], [node("R", "S1"), node("R", "S2")]),
        part("occupant_L", "同乗者 (体重は強さの不利側)", "occupant", [seat_x - 55.0, seat_y, 970.0], [450.0, 450.0, 520.0],
             v["occupant_mass"], [node("L", "S1"), node("L", "S2")]),
        part("cargo", "荷", "cargo", [(E + deck_front) / 2.0, 0.0, 600.0], [deck_front - E - 50.0, 880.0, 300.0], v["cargo_mass"],
             [node("L", "S2"), node("R", "S2"), node("L", "B"), node("R", "B"), node("L", "E"), node("R", "E")]),
    ]
    for s in supports:
        x, y, z = s["position_mm"]
        side = "左" if y > 0 else "右"
        end = "前" if x > -wb / 2 else "後"
        parts.append(part(f"wheel_{s['id']}", f"{end}{side}の車輪 (タイヤ・ホイール・ハブ・ブレーキ)", "wheel",
                          [x, y, z], [2 * r, ww, 2 * r], WHEEL_MASS_KG))
        parts.append(part(f"susp_{s['id']}", f"{end}{side}のサスペンション (アーム・ナックル・ダンパー)", "suspension",
                          [x, (rail_y + 60.0) * (1 if y > 0 else -1), 330.0], [250.0, 120.0, 200.0], 8.0))

    variables = {k: _q(v[k], VARIABLES[k].unit) for k in _V1_ORDER}
    variables.update({k: _q(v[k], var.unit) for k, var in VARIABLES.items() if k not in variables})
    variables["tyre_relaxation_length"] = _q(r, "mm")
    variables["wheel_inertia"] = _q(WHEEL_MASS_KG * (r / 1000.0) ** 2 / 2.0, "kg*m^2")
    return {
        "design_version": version,
        "parent_version": parent,
        "name": name or f"offroad-4w v{version}: 2-seat electric off-road vehicle, ladder frame, rear drive, right-hand drive",
        "change_reason": reason or "first version (fixed demo design)",
        "variables": variables,
        "supports": supports,
        "parts": parts,
        "nodes": nodes,
        "members": members,
        "sections": {rail_key: rail_sec, cross_key: cross_sec},
        "materials": {"stkr400": json.loads(MATERIALS.read_text(encoding="utf-8"))["stkr400"]},
    }

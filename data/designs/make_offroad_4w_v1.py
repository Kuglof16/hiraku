"""Write data/designs/offroad-4w-v1.json, the fixed demo vehicle.

Run from the repository root:  python data/designs/make_offroad_4w_v1.py

A small two-seat electric off-road vehicle for forest roads: ladder frame of
rectangular steel tube, four wheels (front steer, rear drive), up to 40 km/h.
Dimensions and masses here are design choices (assumptions), not catalogue
values. Occupant mass uses the strength-side value from the requirements
(Q33: 76.6 kg, AIST anthropometric database 1991-92, young male 95th
percentile, not checked in this repository); cargo is Q40 (150 kg).
Right-hand drive: the driver sits on the right and is the first occupant in
the parts list (a load case's params.loading boards occupants in list order).
Part boxes are mass-equivalent boxes centred at each part's estimated centre
of gravity. Parts without carried_by rest on the nearest wheel (unsprung).
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

WHEELBASE = 1700.0
TRACK = 1100.0
WHEEL_R = 300.0
WHEEL_W = 200.0
RAIL_Y = 350.0          # half spacing of the two frame rails
RAIL_Z = 380.0          # height of the rail centre line
STATIONS = {            # x of the frame cross members (x forward, origin at the front axle)
    "F": 400.0,         # front end
    "A": 0.0,           # front axle, front suspension mounts
    "S1": -450.0,       # seat front, battery front
    "S2": -1000.0,      # seat rear, battery rear, bed front
    "B": -WHEELBASE,    # rear axle, motor, rear suspension mounts
    "E": -2100.0,       # rear end
}
OCCUPANT_KG = 76.6      # Q33
CARGO_KG = 150.0        # Q40


def q(v, unit):
    return {"value": v, "unit": unit}


# Powertrain and battery (design assumptions): a 51.2 V 100 Ah LiFePO4 pack and
# a 10 kW peak motor driving the rear axle through a 10:1 reduction.
POWERTRAIN = {
    "motor_peak_power": q(10.0, "kW"),
    "motor_peak_torque": q(50.0, "N*m"),
    "motor_max_speed": q(6000.0, "rpm"),
    "gear_ratio": q(10.0, "1"),
    "drivetrain_efficiency": q(0.95, "1"),
    "motor_efficiency": q(0.88, "1"),
    "controller_efficiency": q(0.96, "1"),
    "regen_efficiency": q(0.5, "1"),
    "aux_power": q(150.0, "W"),
    "battery_voltage": q(51.2, "V"),
    "battery_capacity": q(100.0, "Ah"),
    "battery_resistance": q(0.02, "ohm"),
    "mass_factor": q(1.05, "1"),
    "rolling_resistance": q(0.03, "1"),
}
# Lumped heat capacity and thermal resistance to the air of the parts the
# thermal tests watch (design assumptions, no cooling fan or liquid loop).
THERMAL = {
    "battery_heat_capacity": q(50000.0, "J/K"),
    "battery_thermal_resistance": q(0.25, "K/W"),
    "motor_heat_capacity": q(11000.0, "J/K"),
    "motor_thermal_resistance": q(0.06, "K/W"),
    "controller_heat_capacity": q(3600.0, "J/K"),
    "controller_thermal_resistance": q(0.1, "K/W"),
}
# Aerodynamics (design assumptions; the drag coefficient is set above the 0.512
# reported for a golf cart because the occupants sit in the open). Air density
# is the ICAO standard atmosphere at sea level, not checked in this repository.
AERO = {
    "drag_coefficient": q(0.8, "1"),
    "frontal_area": q(1.6, "m^2"),
    "side_force_coefficient": q(1.0, "1"),
    "side_area": q(2.4, "m^2"),
    "centre_of_pressure_height": q(800.0, "mm"),
    "air_density": q(1.225, "kg/m^3"),
}
# Tyre cornering stiffness per axle (both tyres; design assumption).
HANDLING = {
    "front_cornering_stiffness": q(25000.0, "N/rad"),
    "rear_cornering_stiffness": q(30000.0, "N/rad"),
}
# Front against a rigid wall (design assumption): a rubber bumper, then a crush
# zone in the front overhang once the bumper bottoms out.
CRASH = {
    "bumper_stiffness": q(100.0, "N/mm"),
    "bumper_stroke": q(80.0, "mm"),
    "crush_stiffness": q(600.0, "N/mm"),
}


def node(side, st):
    return f"{side}{st}"


def build() -> dict:
    nodes = []
    for side, y in (("L", RAIL_Y), ("R", -RAIL_Y)):
        for st, x in STATIONS.items():
            nodes.append({"id": node(side, st), "xyz_mm": [x, y, RAIL_Z]})

    members = []
    order = list(STATIONS)
    for side in "LR":
        for a, b in zip(order, order[1:]):
            members.append({"id": f"rail_{side}_{a}{b}", "node_a": node(side, a), "node_b": node(side, b),
                            "section": "rt40x80x2.3", "material": "stkr400", "part": "frame"})
    for st in order:
        members.append({"id": f"cross_{st}", "node_a": node("L", st), "node_b": node("R", st),
                        "section": "rt40x40x2.3", "material": "stkr400", "part": "frame"})

    def wheel(sid, x, y, front):
        return {
            "id": sid, "kind": "wheel", "position_mm": [x, y, WHEEL_R], "radius_mm": WHEEL_R, "width_mm": WHEEL_W,
            "suspension": {"type": "spring", "stiffness_N_per_mm": 20.0, "damping_Ns_per_mm": 1.2, "travel_mm": 150.0},
            "actuation": {"drive": not front, "brake": True, "steer": front},
            "frame_node": node("L" if y > 0 else "R", "A" if front else "B"),
        }

    supports = [
        wheel("FL", 0.0, TRACK / 2, True),
        wheel("FR", 0.0, -TRACK / 2, True),
        wheel("RL", -WHEELBASE, TRACK / 2, False),
        wheel("RR", -WHEELBASE, -TRACK / 2, False),
    ]

    def part(pid, name, kind, center, size, mass, carried_by=None):
        p = {"id": pid, "name": name, "kind": kind, "box": {"center_mm": center, "size_mm": size}, "mass_kg": mass}
        if carried_by:
            p["carried_by"] = carried_by
        return p

    mid4 = ["LS1", "RS1", "LS2", "RS2"]
    bed = ["LS2", "RS2", "LB", "RB", "LE", "RE"]
    parts = [
        part("battery", "電池 (LiFePO4 51.2 V 100 Ah)", "battery", [-725.0, 0.0, 330.0], [500.0, 600.0, 200.0], 50.0, mid4),
        part("motor", "モーターと減速機", "motor", [-1550.0, 0.0, 310.0], [250.0, 300.0, 220.0], 25.0, ["LB", "RB"]),
        part("controller", "モーターの制御器", "controller", [-1110.0, 0.0, 300.0], [250.0, 200.0, 80.0], 4.0, ["LS2", "RS2"]),
        part("aux", "配線と 12 V の補機", "electrical", [-300.0, 0.0, 300.0], [300.0, 300.0, 100.0], 6.0, ["LA", "RA", "LS1", "RS1"]),
        part("floor_front", "足元の床", "body", [-225.0, 0.0, 440.0], [450.0, 1100.0, 20.0], 6.0, ["LA", "RA", "LS1", "RS1"]),
        part("floor", "座席の下の床", "body", [-725.0, 0.0, 440.0], [550.0, 1100.0, 20.0], 8.0, mid4),
        part("deck", "荷台の床", "body", [-1625.0, 0.0, 440.0], [950.0, 1100.0, 20.0], 18.0, bed),
        part("front_body", "前の外板とバンパー", "body", [220.0, 0.0, 560.0], [360.0, 880.0, 280.0], 15.0, ["LF", "RF", "LA", "RA"]),
        part("steering", "ハンドルとペダル", "steering", [-250.0, -300.0, 750.0], [300.0, 400.0, 500.0], 12.0, ["RA", "RS1"]),
        part("seat_R", "運転席", "seat", [-725.0, -300.0, 580.0], [500.0, 500.0, 260.0], 12.0, ["RS1", "RS2"]),
        part("seat_L", "助手席", "seat", [-725.0, 300.0, 580.0], [500.0, 500.0, 260.0], 12.0, ["LS1", "LS2"]),
        part("occupant_R", "運転者 (体重は強さの不利側)", "occupant", [-780.0, -300.0, 970.0], [450.0, 450.0, 520.0], OCCUPANT_KG, ["RS1", "RS2"]),
        part("occupant_L", "同乗者 (体重は強さの不利側)", "occupant", [-780.0, 300.0, 970.0], [450.0, 450.0, 520.0], OCCUPANT_KG, ["LS1", "LS2"]),
        part("cargo", "荷", "cargo", [-1625.0, 0.0, 600.0], [900.0, 1000.0, 300.0], CARGO_KG, bed),
    ]
    for s in supports:
        x, y, z = s["position_mm"]
        side = "左" if y > 0 else "右"
        end = "前" if x > -WHEELBASE / 2 else "後"
        parts.append(part(f"wheel_{s['id']}", f"{end}{side}の車輪 (タイヤ・ホイール・ハブ・ブレーキ)", "wheel",
                          [x, y, z], [2 * WHEEL_R, WHEEL_W, 2 * WHEEL_R], 14.0))
        parts.append(part(f"susp_{s['id']}", f"{end}{side}のサスペンション (アーム・ナックル・ダンパー)", "suspension",
                          [x, (RAIL_Y + 60.0) * (1 if y > 0 else -1), 330.0], [250.0, 120.0, 200.0], 8.0))

    return {
        "design_version": 1,
        "parent_version": None,
        "name": "offroad-4w v1: 2-seat electric off-road vehicle, ladder frame, rear drive, right-hand drive",
        "change_reason": "first version (fixed demo design)",
        "variables": {
            "wheelbase": q(WHEELBASE, "mm"),
            "track": q(TRACK, "mm"),
            "wheel_radius": q(WHEEL_R, "mm"),
            "wheel_width": q(WHEEL_W, "mm"),
            "rail_spacing": q(2 * RAIL_Y, "mm"),
            "rail_height": q(RAIL_Z, "mm"),
            "front_overhang": q(STATIONS["F"], "mm"),
            "rear_overhang": q(-STATIONS["E"] - WHEELBASE, "mm"),
            "max_speed": q(40.0, "km/h"),
            "occupants": q(2.0, "1"),
            "occupant_mass": q(OCCUPANT_KG, "kg"),
            "cargo_mass": q(CARGO_KG, "kg"),
            **POWERTRAIN,
            **THERMAL,
            **AERO,
            **HANDLING,
            **CRASH,
        },
        "supports": supports,
        "parts": parts,
        "nodes": nodes,
        "members": members,
        "sections": {
            "rt40x80x2.3": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.3}},
            "rt40x40x2.3": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 2.3}},
        },
        "materials": {"stkr400": json.loads((ROOT / "data" / "materials" / "materials.json").read_text(encoding="utf-8"))["stkr400"]},
    }


def main() -> None:
    out = HERE / "offroad-4w-v1.json"
    out.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()

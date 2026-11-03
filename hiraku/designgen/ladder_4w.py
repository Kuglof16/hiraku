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

from hiraku.designgen.kit.frame import Frame
from hiraku.designgen.kit.package import Item, Layout, bottom_at, front_at, left_at, rear_at, right_at, top_at
from hiraku.designgen.kit.running_gear import Suspension, corner_name, four_wheels
from hiraku.designgen.kit.sections import rect_tube

Record = dict[str, Any]

MATERIALS = Path(__file__).resolve().parents[2] / "data" / "materials" / "materials.json"
MIN_STATION_GAP_MM = 100.0  # stations closer than this would make members shorter than the joints (assumption)
GUSSET_LEG_MM = 60.0  # gusset leg set by CHOICES joints = gusset unless given (assumption: 1.5 x the cross member width)
GUSSET_LEG_MIN_MM = 30.0  # a gusset leg that is not 0 is at least this (assumption: smaller plates than the joint)


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
    "cargo_height": Var("mm", 300.0, meaning="height of the load stacked on the deck (its centre of gravity half of it "
                        "above the deck); describes the load, so the designer does not change it"),
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
    # joint reinforcement at the T joints (where a cross member ends on the side of a rail): the shape
    # follows from these variables alone (CHOICES["joints"] is a short way to set them). Written into
    # design.variables only when changed from their defaults, so a design without reinforcement is
    # the same as before these variables existed.
    "gusset_leg": Var("mm", 0.0, 0.0, 150.0, "gusset leg along the rail and along the cross member; 0: no gussets, "
                      f"else {GUSSET_LEG_MIN_MM:g} to 150 mm: four triangular plates at every T joint, ahead of and "
                      "behind the cross member on its upper and lower wall (CHOICES joints = gusset sets "
                      f"{GUSSET_LEG_MM:g} mm, 1.5 x the cross member width, an assumption)"),
    "gusset_thickness": Var("mm", 2.3, 1.6, 4.5, "gusset plate thickness (used when gusset_leg > 0); the default (the "
                            "tube wall) is an assumption"),
    "cross_members_through": Var("1", 0.0, 0.0, 1.0, "1: the cross members pass through a hole in the rail's inner "
                                 "wall to its outer wall and are welded to both (needs cross_h - cross_t < rail_h - "
                                 "rail_t); parts through_<member>_<node> carry the tube inside the rails, the beam "
                                 "model's members are unchanged; 0: they end on the rail's side"),
}
SWITCHES = ("cross_members_through",)  # 0 or 1
# Reinforcement variables, written into design.variables only when one of their group differs from its default.
OPTIONAL = {"gusset_leg": ("gusset_leg", "gusset_thickness"), "cross_members_through": ("cross_members_through",)}
LEGACY = ("gussets",)  # the gusset switch of earlier designs (0: no gussets, whatever gusset_leg says); not an input now
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
# A design built here carries these from the first version on; variables added since
# take their defaults (which build v1) when an older design lacks them.
SIGNATURE = ("wheelbase", "track", "wheel_radius", "wheel_width", "rail_spacing", "rail_height", "front_overhang",
             "rear_overhang")
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
        if name in SWITCHES and val not in (0.0, 1.0):
            raise DesignGenError(f"{name} is a switch: 0 or 1, got {val:g}")
        if name == "gusset_leg" and 0.0 < val < GUSSET_LEG_MIN_MM:
            raise DesignGenError(f"gusset_leg is 0 (no gussets) or {GUSSET_LEG_MIN_MM:g} to {var.hi:g} mm, got {val:g}")
        vals[name] = val
    return vals


def choice_values(choices: dict | None) -> dict[str, Record]:
    """The variables a set of CHOICES stands for ({"joints": "gusset"} -> gusset_leg 60 mm, cross_members_through 0).
    A size it sets (CHOICE_SIZES) may be given as a value too; the switches may not."""
    out: dict[str, Record] = {}
    for k, opt in (choices or {}).items():
        if k not in CHOICES:
            raise DesignGenError(f"no choice {k!r}; the choices: {', '.join(CHOICES)}")
        if opt not in CHOICES[k]:
            raise DesignGenError(f"{k} must be one of {', '.join(CHOICES[k])}, got {opt!r}")
        out.update({name: _q(v, VARIABLES[name].unit) for name, v in JOINT_VALUES[opt].items()})
    return out


def apply_choices(choices: dict | None, values: dict) -> dict:
    """values with the variables the choices stand for: a size given in values (CHOICE_SIZES) is kept,
    a switch given in values is an error. The result must still be the chosen option."""
    set_by = choice_values(choices)
    clash = sorted(k for k in set_by if k in values and k not in CHOICE_SIZES)
    if clash:
        raise DesignGenError(f"{', '.join(clash)} come from the choices {choices}; do not give them as values")
    out = {**set_by, **values}
    for k, opt in (choices or {}).items():
        got = _joints({n: (q["value"] if isinstance(q, dict) else q) for n, q in out.items()})
        if got != opt:
            raise DesignGenError(f"{k} = {opt} does not go with the values given (they make {got}); "
                                 f"a gusset needs gusset_leg > 0")
    return out


def _joints(v: dict) -> str:
    """butt / gusset / through (or gusset+through) from the variables (missing ones at their defaults)."""
    leg = float(v.get("gusset_leg", VARIABLES["gusset_leg"].default))
    through = float(v.get("cross_members_through", 0.0)) == 1.0
    return {(False, False): "butt", (True, False): "gusset", (False, True): "through"}.get((leg > 0, through),
                                                                                        "gusset+through")


def choices_of(design: Record) -> dict[str, str]:
    """The CHOICES a design was built with (from its variables)."""
    return {"joints": _joints({k: q["value"] for k, q in values_of(design).items()})}


def values_of(design: Record) -> dict[str, Record]:
    """The generator variables a design carries (design.variables), as quantities. A design from before
    gusset_leg alone set the gussets (variable gussets) is read as the same shape: gussets 0 -> gusset_leg 0."""
    now = design.get("variables", {})
    out = {k: v for k, v in now.items() if k in VARIABLES}
    if "gussets" in now and float(now["gussets"]["value"]) == 0.0:
        out.pop("gusset_leg", None)
        out.pop("gusset_thickness", None)
    return out


def _q(v: float, unit: str) -> Record:
    return {"value": v, "unit": unit}


# Layout clearances and offsets (design choices, assumptions), used against the frame and the parts:
ON_RAILS_MM = 10.0  # floors, deck, front body and suspension tops sit this far above the rail top (brackets)
DECK_GAP_MM = 150.0  # the deck starts this far behind the rear seat cross member
MOTOR_GAP_MM = 25.0  # the motor's back face this far ahead of the rear axle station
CONTROLLER_LEAD_MM = 15.0  # the controller's front face this far ahead of the rear seat cross member
AUX_DROP_MM = 10.0  # the wiring box's top this far above the rail bottom
STEERING_LIFT_MM = 50.0  # the steering and pedal box starts this far above the footwell floor
STEERING_INBOARD_MM = 100.0  # its centre this far inboard of the driver's seat centre
OCCUPANT_BACK_MM = 55.0  # the occupant's mass box centre this far behind the seat centre
CARGO_SHORT_MM = 50.0  # the cargo box is this much shorter than the deck
TITLE = "2 人乗り 4 輪のはしご形フレームの電動車 (前輪で操舵、後輪で駆動、右ハンドル)"
DESCRIPTION = "2-seat electric off-road vehicle, ladder frame, rear drive, right-hand drive"
# How the T joints (a cross member ending on the side of a rail) are made: butt (the cross member
# welded on the rail's side, the first version), gusset (plus triangular plates, gusset_leg and
# gusset_thickness) or through (the cross member through the rail's inner wall to its outer wall).
# A shortcut for the variables in JOINT_VALUES; the variables alone give the same design.
CHOICES: dict[str, tuple[str, ...]] = {"joints": ("butt", "gusset", "through")}
JOINT_VALUES = {"butt": {"gusset_leg": 0.0, "cross_members_through": 0.0},
                "gusset": {"gusset_leg": GUSSET_LEG_MM, "cross_members_through": 0.0},
                "through": {"gusset_leg": 0.0, "cross_members_through": 1.0}}
CHOICE_SIZES = ("gusset_leg",)  # set by a choice, but may be given as a value with it


def applies(flags: dict) -> bool:
    """For products that move by themselves and carry people or cargo."""
    return bool(flags.get("self_propelled")) and bool(flags.get("ride") or flags.get("cargo"))


# Part list order of v1 (the rest after them as placed: each wheel, then its suspension)
PART_ORDER = ["battery", "motor", "controller", "aux", "floor_front", "floor", "deck", "front_body", "steering", "seat_R",
              "seat_L", "occupant_R", "occupant_L", "cargo"]


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

    # frame: two rails through the stations, a cross member at each
    frame = Frame("stkr400")
    rail = frame.section(*rect_tube(v["rail_b"], v["rail_h"], v["rail_t"]))
    cross = frame.section(*rect_tube(v["cross_b"], v["cross_h"], v["cross_t"]))
    frame.ladder(stations, rail_y, rail_z, rail, cross)

    # running gear: front steer, rear drive, coil springs
    def spring(rate: float) -> Suspension:
        return Suspension("spring", rate, 1.2, v["suspension_travel"])

    supports = four_wheels(wb, track, r, ww, spring(v["front_spring_rate"]), spring(v["rear_spring_rate"]))

    # parts, each against the frame or a part already placed
    lay = Layout({**frame.part(), "supports": supports, "parts": []})
    on_rails = lay.rail_top() + ON_RAILS_MM
    S1, S2, E = (lay.station_x(k) for k in ("S1", "S2", "E"))
    seat_x, seat_y = v["seat_x"], v["seat_y"]
    lay.put(Item("floor_front", "足元の床", "body", (lay.station_x("A") - S1, 600.0, 20.0), 6.0),
            x=lay.between("A", "S1"), y=0.0, z=bottom_at(on_rails))
    lay.put(Item("floor", "座席の下の床", "body", (S1 - S2, track, 20.0), 8.0), x=lay.between("S1", "S2"), y=0.0,
            z=bottom_at(on_rails))
    deck_len = S2 - DECK_GAP_MM - E
    lay.put(Item("deck", "荷台の床", "body", (deck_len, 880.0, 20.0), 18.0), x=rear_at(E), y=0.0, z=bottom_at(on_rails),
            carried_by=lay.at("S2", "B", "E"))
    lay.put(Item("front_body", "前の外板とバンパー", "body", (360.0, 600.0, 280.0), 15.0), x=front_at(lay.station_x("F")),
            y=0.0, z=bottom_at(on_rails))
    # the battery fits between the rails under the floor; its volume and mass go with the capacity
    scale = v["battery_capacity"] / BATTERY_V1["capacity"]
    b_w = min(BATTERY_V1["width"], lay.rail_inner_gap() - 20.0)
    b_l = BATTERY_V1["length"] * BATTERY_V1["width"] / b_w * scale
    lay.put(Item("battery", f"電池 (LiFePO4 {v['battery_voltage']:g} V {v['battery_capacity']:g} Ah)", "battery",
                 (b_l, b_w, BATTERY_V1["height"]), BATTERY_V1["mass"] * scale),
            x=v["battery_x"], y=0.0, z=top_at(lay.bottom_of("floor")))
    motor_mass = MOTOR_V1["mass"] * max(v["motor_peak_power"] / MOTOR_V1["power"], v["motor_peak_torque"] / MOTOR_V1["torque"])
    lay.put(Item("motor", "モーターと減速機", "motor", (250.0, 300.0, 220.0), motor_mass),
            x=rear_at(lay.station_x("B") + MOTOR_GAP_MM), y=0.0, z=top_at(lay.rail_top()), carried_by=lay.at("B"))
    lay.put(Item("controller", "モーターの制御器", "controller", (250.0, 200.0, 80.0), 4.0),
            x=front_at(S2 + CONTROLLER_LEAD_MM), y=0.0, z=top_at(lay.rail_bottom()), carried_by=lay.at("S2"))
    lay.put(Item("aux", "配線と 12 V の補機", "electrical", (300.0, 300.0, 100.0), 6.0),
            x=rear_at(S1), y=0.0, z=top_at(lay.rail_bottom() + AUX_DROP_MM))
    lay.put(Item("steering", "ハンドルとペダル", "steering", (300.0, 200.0, 500.0), 12.0),
            x=v["steering_x"], y=-seat_y + STEERING_INBOARD_MM, z=bottom_at(lay.top_of("floor_front") + STEERING_LIFT_MM))
    for pid, label, y in (("seat_R", "運転席", -seat_y), ("seat_L", "助手席", seat_y)):
        lay.put(Item(pid, label, "seat", (500.0, 500.0, 260.0), 12.0), x=seat_x, y=y, z=bottom_at(lay.top_of("floor")))
    for pid, label, seat in (("occupant_R", "運転者 (体重は強さの不利側)", "seat_R"), ("occupant_L", "同乗者 (体重は強さの不利側)", "seat_L")):
        sy = lay.part(seat)["box"]["center_mm"][1]
        lay.put(Item(pid, label, "occupant", (450.0, 450.0, 520.0), v["occupant_mass"]),
                x=seat_x - OCCUPANT_BACK_MM, y=sy, z=bottom_at(lay.top_of(seat)))
    lay.put(Item("cargo", "荷", "cargo", (deck_len - CARGO_SHORT_MM, 880.0, v["cargo_height"]), v["cargo_mass"]),
            x=lay.part("deck")["box"]["center_mm"][0], y=0.0, z=bottom_at(lay.top_of("deck")), carried_by=lay.at("S2", "B", "E"))
    for s in supports:  # each wheel (unsprung) and its arms, their tops level with the floors, inner face on the rail
        x, y, z = s["position_mm"]
        corner = corner_name(s, supports)
        lay.put(Item(f"wheel_{s['id']}", f"{corner}の車輪 (タイヤ・ホイール・ハブ・ブレーキ)", "wheel", (2 * r, ww, 2 * r),
                     WHEEL_MASS_KG), x=x, y=y, z=z, carried_by=None)
        lay.put(Item(f"susp_{s['id']}", f"{corner}のサスペンション (アーム・ナックル・ダンパー)", "suspension",
                     (250.0, 120.0, 200.0), 8.0),
                x=x, y=right_at(rail_y) if y > 0 else left_at(-rail_y), z=top_at(on_rails), carried_by=None)
    lay.reorder(PART_ORDER)
    material = json.loads(MATERIALS.read_text(encoding="utf-8"))["stkr400"]
    rho = float(material["density_kg_m3"])
    tee = list(stations)[1:-1]  # the T joints: every station but the frame's two ends
    if v["gusset_leg"] > 0:
        for st in tee:
            for side in "LR":
                lay.parts.extend(frame.gussets(f"{side}{st}", f"cross_{st}", v["gusset_leg"], v["gusset_thickness"], rho))
    if v["cross_members_through"] == 1:
        if v["cross_h"] - v["cross_t"] >= v["rail_h"] - v["rail_t"]:
            raise DesignGenError("cross_members_through = 1 needs cross members lower than the rails "
                                 "(cross_h - cross_t < rail_h - rail_t), else their walls would lie in the rails' walls")
        for st in tee:
            for side in "LR":
                lay.parts.append(frame.through_part(f"{side}{st}", f"cross_{st}", rho))

    written = {k for names in OPTIONAL.values() if any(v[n] != VARIABLES[n].default for n in names) for k in names}
    variables = {k: _q(v[k], VARIABLES[k].unit) for k in _V1_ORDER}
    variables.update({k: _q(v[k], var.unit) for k, var in VARIABLES.items()
                      if k not in variables and (k in written or not any(k in g for g in OPTIONAL.values()))})
    variables["tyre_relaxation_length"] = _q(r, "mm")
    variables["wheel_inertia"] = _q(WHEEL_MASS_KG * (r / 1000.0) ** 2 / 2.0, "kg*m^2")
    return {
        "design_version": version,
        "parent_version": parent,
        "name": name or f"offroad-4w v{version}: {DESCRIPTION}",
        "change_reason": reason or "first version (fixed demo design)",
        "variables": variables,
        "supports": supports,
        "parts": lay.parts,
        **frame.part(),
        "materials": {"stkr400": material},
    }

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
source; the defaults build data/designs/offroad-4w-v1.json. With rollbar = 1 (the
default) a roll bar stands behind the seats (build_rollbar): the rails are split at a
node H between S2 and B, outriggers carry the posts' feet out to the
rollbar_width, and a top beam joins the posts.
The suspension meets the frame as it would on a real ladder frame (kit/mounts.py): the
hard points are moved until the upper arms and the dampers clear the rails through the
wheel travel, each damper's top is the end node of a tower on the rail, each lower arm's
pivots are nodes at the ends of legs down from the rail (joined left to right by lower
cross members, lower_cross), and brackets (parts of kind bracket) hold the upper arms'
pivots and the tie rods' and toe links' inner ends. The footwell starts behind the front
wheels' steering sweep, at a front bulkhead (a panel) with the dash posts and the dash
cross member that holds the steering column (build_dash); bulkhead_dx brings the bulkhead (in the front
towers' rear posts) forward where a wider track leaves the sweep farther forward.
The driver's place comes from four variables: the seats' cushion top seat_height above the floor; the
accelerator's pad at pedal_x with the large driver's right foot on it (kit/body.place_pedals); the
steering wheel's centre steering_wheel_height above the seats' cushion top at steering_x less
STEERING_WHEEL_BEHIND_MM; the column from the wheel's centre forward and down at steering_column_angle
to above the pinion on the rack (kit/steering.place). Their defaults put the parts where the generator
put them before they were variables (_driver_v1); their ranges come from the AIST body sizes.
The parts of the drive, steering, brakes, restraints and the battery's switch come from
the kits (kit/drivetrain.py, steering.py, body.py, safety.py), and every part carries its
system and links (kit/systems.py) when the contract has those fields. The occupants are
their bodies as parts (kit/body.py): the trunk keeps the occupant's id, each limb is
<occupant id>.<segment>_<L|R> (one person per id before the first ".", as
hiraku.contract.loading.person_of reads them); the segments' mass shares are D. A. Winter's
(kit/body.SEGMENT_SHARE, not checked in this repository).

Options a failed requirement may call for, all off by default (each a variable, CHOICES the short ways to set
them): mounts from the parts to the frame (mount_<group>, kit/attach.py; FN-15, load_path R8 free parts), stands
for the pedals (pedal_mount; FN-12, R7 foot), a side frame outboard of the seats (side_bars, side_net; FN-05 and
FN-07, R8 push at point R), the roll bar's outrigger section, rear stays and front posts (rollbar_foot_*,
rollbar_stays, rollbar_front_posts; FN-01, R4) and fenders on the uprights (fenders; FN-19, shape only). The
members the options add start with rops_ or side_, and build() refuses an option whose members or parts reach
into another body further than K4 allows.

Occupant and cargo masses are requirements (Q33, Q40), not design choices:
the default occupant mass is AIST anthropometric database 1991-92 item N1
Weight, young adult male 95th percentile (source: read from
https://www.airc.aist.go.jp/dhrt/91-92/data/list.html on 2026-11-03).
"""

from __future__ import annotations

import functools
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from hiraku.designgen.kit import body, drivetrain, mounts, safety, steering, systems
from hiraku.designgen.kit.frame import Frame
from hiraku.designgen.kit.package import Item, Layout, bottom_at, front_at, left_at, rear_at, right_at, top_at
from hiraku.designgen.kit.running_gear import Suspension, corner_name, four_wheels
from hiraku.designgen.kit.sections import rect_tube, round_tube

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
    # layout (wheelbase, seat_x, steering_x and battery_x: 320 mm longer, back and behind than the first v1, so
    # that the large driver's feet are behind the front wheels' steering sweep, assumption, see FOOT_SHIFT_MM)
    "wheelbase": Var("mm", 2020.0, 1400.0, 2400.0, "front axle to rear axle"),
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
    # the suspension's mounts on the frame (kit/mounts.py) and the dash: square tubes of STKR400; the sizes
    # are design choices (assumptions, no source)
    "tower_b": Var("mm", 30.0, 25.0, 60.0, "square tube width of the suspension towers (posts, top beam, the arm "
                   "out to the damper's top), the dash posts and the dash cross member (narrower than the rail, "
                   "so that a post's foot sits inside the rail's top wall)"),
    "tower_t": Var("mm", 2.3, 1.6, 4.5, "wall of the tower and dash tubes"),
    "lower_b": Var("mm", 40.0, 30.0, 60.0, "square tube width of the legs down to the lower arms' pivots and the "
                   "lower cross members joining them (at the default 40 x 2.3 the front legs are R3's worst welds, "
                   "damage 0.29; 50 x 3.2 gives 0.13)"),
    "lower_t": Var("mm", 2.3, 1.6, 4.5, "wall of the leg and lower cross member tubes"),
    "lower_cross": Var("1", 1.0, 0.0, 1.0, "1: lower cross members join the left and right lower arms' pivots (at "
                       "each axle where the motor and the reducer are not in the way); 0: none, each pivot on its leg "
                       "alone"),
    # people and parts
    "seat_x": Var("mm", -1045.0, -1500.0, -500.0, "seat centre (x); the seat cross members are 275 mm ahead and behind"),
    "seat_y": Var("mm", 300.0, 220.0, 400.0, "seat centre off the centre line, each side"),
    "steering_x": Var("mm", -570.0, -900.0, 0.0, "steering position (x): the steering wheel's centre is "
                      "150 mm behind it (where the back of the earlier steering box was)"),
    "battery_x": Var("mm", -1045.0, -1700.0, -300.0, "battery centre (x); it hangs from the cross members either side "
                     "and must fit between them (seat_x moves the seat cross members)"),
    # the driver's place (seat_height, steering_column_angle, steering_wheel_height, pedal_x) are added after
    # the layout constants below: their defaults and ranges come from the AIST body sizes (_DRIVER_V1)
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
    "fatigue_slope_after_knee_constant_amplitude": Var("1", 22.0, meaning="assumption"),
    "tyre_stiffness": Var("N/mm", 150.0, meaning="assumption"),
    # tyre and suspension details of the 3D vehicle model (K2), assumptions with no source
    "tyre_damping_ratio": Var("1", 0.05, meaning="assumption: vertical damping ratio of the tyre"),
    "tyre_slip_stiffness_coeff": Var("1", 10.0, meaning="assumption: longitudinal slip stiffness per unit slip, "
                                     "as a multiple of the wheel's static load"),
    "bump_stop_factor": Var("1", 10.0, meaning="assumption: bump and rebound stop stiffness, as a multiple of the spring"),
    # driveshafts: steel tubes from the reducer to the driven wheels (kit/drivetrain.py), assumptions
    "driveshaft_diameter": Var("mm", 30.0, meaning="assumption: driveshaft tube outer diameter"),
    "driveshaft_wall": Var("mm", 5.0, meaning="assumption: driveshaft tube wall"),
    "driveshafts": Var("1", 1.0, 0.0, 1.0, "1: a driveshaft from the reducer to each driven wheel; 0: none, the drive "
                       "ends at the reducer and no torque reaches the wheels (the shafts' mass stays in the reducer, so "
                       "the mass does not change)"),
    # lamps (kit/safety.py): needed on public roads only
    "lamps": Var("1", 0.0, 0.0, 1.0, "1: headlamps, tail lamps, stop lamps and turn signals on the front body and "
                 "behind the frame's rear end (needed on public roads); 0: none (off public roads)"),
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
    # roll bar (ROPS): a portal hoop behind the seats, two posts and a top beam (build_rollbar places it from
    # the frame and the seats). On by default (the demo vehicle has the systems it needs: kit/safety.py); a
    # design that has no rollbar variable and no roll bar members (built before it was on by default) is read
    # as rollbar = 0 (values_of). The sizes and defaults are design choices (assumptions, no source).
    "rollbar": Var("1", 1.0, 0.0, 1.0, "1: a roll bar (portal hoop) behind the seats, standing on the rails at a node "
                   "rollbar_offset behind the seat's rear cross member S2, on outriggers out to rollbar_width; 0: none"),
    "rollbar_height": Var("mm", 1100.0, 900.0, 1400.0, "top beam centre line above the seat cushion top (the seats' "
                          "box top); assumption: the default puts the tube above the seated head of a young male 95th "
                          "percentile (AIST I1 sitting height; the gap is k4_geometry.rollbar.occupant_clearance's)"),
    "rollbar_width": Var("mm", 1200.0, 700.0, 1400.0, "post centre to centre across (at least rail_spacing; wider than "
                         "the rails, outriggers of the cross member section carry the posts' feet out from the rails); "
                         "assumption: the default puts the posts outside the shoulders (AIST D2) of both occupants"),
    "rollbar_offset": Var("mm", 80.0, 40.0, 400.0, "the posts' centre line this far behind the seat's rear cross member "
                          "S2 (the rails are split at a node there; it must stay 100 mm ahead of the rear axle station)"),
    "rollbar_square": Var("1", 0.0, 0.0, 1.0, "0: round tube rollbar_d x rollbar_t of STKM13A; 1: square tube rollbar_b "
                          "x rollbar_b x rollbar_t of STKR400 (the materials are assumptions)"),
    "rollbar_d": Var("mm", 42.7, 25.0, 76.3, "round tube outside diameter (rollbar_square = 0)"),
    "rollbar_b": Var("mm", 40.0, 25.0, 80.0, "square tube width (rollbar_square = 1)"),
    "rollbar_t": Var("mm", 2.3, 1.6, 4.5, "roll bar tube wall"),
}
SWITCHES = ("cross_members_through", "rollbar", "rollbar_square", "driveshafts", "lamps", "lower_cross")  # 0 or 1
ROLLBAR_VARS = ("rollbar", "rollbar_height", "rollbar_width", "rollbar_offset", "rollbar_square", "rollbar_d", "rollbar_b",
                "rollbar_t")
ROLLBAR_MATERIALS = {0.0: "stkm13a", 1.0: "stkr400"}  # by rollbar_square (assumption)
# Optional variables, written into design.variables only when one of their group differs from its default.
OPTIONAL = {"gusset_leg": ("gusset_leg", "gusset_thickness"), "cross_members_through": ("cross_members_through",)}
LEGACY = ("gussets",)  # the gusset switch of earlier designs (0: no gussets, whatever gusset_leg says); not an input now
# Written into design.variables by build() from the other variables (not inputs).
DERIVED = {
    "tyre_relaxation_length": ("mm", "assumption: the tyre's relaxation length, taken as the wheel radius"),
    "wheel_inertia": ("kg*m^2", "rotary inertia of one wheel part, as a solid disc: m r^2 / 2"),
    "motor_rotor_inertia": ("kg*m^2", "rotary inertia of the motor's rotor (kit/drivetrain.py: a solid cylinder, "
                            "assumption)"),
    "reducer_output_inertia": ("kg*m^2", "rotary inertia of the reducer's output gear, turning with the wheels "
                               "(kit/drivetrain.py: a disc, assumption)"),
    "driveshaft_inner_drop": ("mm", "the driveshafts' inner joints (and the reducer's outputs) this far below the "
                              "wheel centres, the least that keeps each shaft DRIVESHAFT_GAP_MM under the rail at full "
                              "bump (kit/drivetrain.drop_to_clear)"),
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
             "fatigue_knee_cycles", "fatigue_slope_after_knee_constant_amplitude", "tyre_stiffness"]
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
        if name in INTEGERS and val != round(val):
            raise DesignGenError(f"{name} is a whole number, got {val:g}")
        if name in ZERO_OR_AT_LEAST and 0.0 < val < ZERO_OR_AT_LEAST[name]:
            raise DesignGenError(f"{name} is 0 (as the cross members) or {ZERO_OR_AT_LEAST[name]:g} to {var.hi:g} "
                                 f"{var.unit}, got {val:g}")
        vals[name] = val
    if vals["side_bar_low"] >= vals["side_bar_high"] and vals["side_bars"] == 1:
        raise DesignGenError(f"side_bar_low ({vals['side_bar_low']:g} mm) must be below side_bar_high "
                             f"({vals['side_bar_high']:g} mm)")
    return vals


def choice_values(choices: dict | None) -> dict[str, Record]:
    """The variables a set of CHOICES stands for ({"joints": "gusset"} -> gusset_leg 60 mm, cross_members_through 0;
    {"part_mounts": "brackets"} -> mount_floor 1, ...). A size it sets (CHOICE_SIZES) may be given as a value too;
    the switches may not."""
    out: dict[str, Record] = {}
    for k, opt in (choices or {}).items():
        if k not in CHOICES:
            raise DesignGenError(f"no choice {k!r}; the choices: {', '.join(CHOICES)}")
        if opt not in CHOICES[k]:
            raise DesignGenError(f"{k} must be one of {', '.join(CHOICES[k])}, got {opt!r}")
        table = JOINT_VALUES if k == "joints" else OPTION_VALUES[k]
        out.update({name: _q(v, VARIABLES[name].unit) for name, v in table[opt].items()})
    return out


def _option(k: str, v: dict) -> str:
    """The option of choice k that the variables make (missing ones at their defaults); "some" when none matches."""
    if k == "joints":
        return _joints(v)
    for opt, sets in OPTION_VALUES[k].items():
        if all(float(v.get(n, VARIABLES[n].default)) == x for n, x in sets.items()):
            return opt
    return "some"


def apply_choices(choices: dict | None, values: dict) -> dict:
    """values with the variables the choices stand for: a size given in values (CHOICE_SIZES) is kept,
    a switch given in values is an error. The result must still be the chosen option."""
    set_by = choice_values(choices)
    clash = sorted(k for k in set_by if k in values and k not in CHOICE_SIZES)
    if clash:
        raise DesignGenError(f"{', '.join(clash)} come from the choices {choices}; do not give them as values")
    out = {**set_by, **values}
    for k, opt in (choices or {}).items():
        got = _option(k, {n: (q["value"] if isinstance(q, dict) else q) for n, q in out.items()})
        if got != opt:
            raise DesignGenError(f"{k} = {opt} does not go with the values given (they make {got})"
                                 + ("; a gusset needs gusset_leg > 0" if k == "joints" else ""))
    return out


def _joints(v: dict) -> str:
    """butt / gusset / through (or gusset+through) from the variables (missing ones at their defaults)."""
    leg = float(v.get("gusset_leg", VARIABLES["gusset_leg"].default))
    through = float(v.get("cross_members_through", 0.0)) == 1.0
    return {(False, False): "butt", (True, False): "gusset", (False, True): "through"}.get((leg > 0, through),
                                                                                        "gusset+through")


def choices_of(design: Record) -> dict[str, str]:
    """The CHOICES a design was built with (from its variables)."""
    v = {k: q["value"] for k, q in values_of(design).items()}
    return {k: _option(k, v) for k in CHOICES}


def values_of(design: Record) -> dict[str, Record]:
    """The generator variables a design carries (design.variables), as quantities. A design from before
    gusset_leg alone set the gussets (variable gussets) is read as the same shape: gussets 0 -> gusset_leg 0."""
    now = design.get("variables", {})
    out = {k: v for k, v in now.items() if k in VARIABLES}
    if "gussets" in now and float(now["gussets"]["value"]) == 0.0:
        out.pop("gusset_leg", None)
        out.pop("gusset_thickness", None)
    if "rollbar" not in now and not any(m["id"].startswith("rollbar_") for m in design.get("members", [])):
        out["rollbar"] = _q(0.0, "1")  # built when the roll bar was off by default
    if "pedal_x" not in now and "seat_x" in now:
        # built when the pedals were under the large driver's foot in the seat: they moved with seat_x
        shift = float(now["seat_x"]["value"]) - VARIABLES["seat_x"].default
        if shift:
            out["pedal_x"] = _q(VARIABLES["pedal_x"].default + shift, "mm")
    return out


def _q(v: float, unit: str) -> Record:
    return {"value": v, "unit": unit}


# Layout clearances and offsets (design choices, assumptions), used against the frame and the parts:
ON_RAILS_MM = 10.0  # floors, deck, front body and suspension tops sit this far above the rail top (brackets)
DECK_GAP_MM = 150.0  # the deck starts this far behind the rear seat cross member
CONTROLLER_LEAD_MM = 15.0  # the controller's front face this far ahead of the rear seat cross member
AUX_DROP_MM = 10.0  # the wiring box's top this far above the rail bottom
STEERING_INBOARD_MM = 130.0  # the pinion this far inboard of the driver's seat centre (100 in the first v1; 130
# keeps the intermediate shaft clear of the rack's clamp at the tie rod's inner end)
OCCUPANT_BACK_MM = 55.0  # the occupant's mass box centre this far behind the seat centre
CARGO_SHORT_MM = 50.0  # the cargo box is this much shorter than the deck
STEERING_WHEEL_BEHIND_MM = 150.0  # the steering wheel's centre this far behind steering_x
TYRE_GAP_MM = 10.0  # the bulkhead and the footwell floor this far behind the front wheels' steering sweep
# The front towers' posts (the rear one is in the bulkhead) at least this far from the axle along x, so that the
# leg from the rear post's foot to the lower arm's rear pivot passes behind the rack's clamp (assumption: v1's
# value, where the steering sweep puts the bulkhead)
TOWER_MIN_DX_MM = 285.0
# bulkhead_dx's lower end: the front towers' rear post (the bulkhead and the dash post in it) GAP_MM (kit/mounts)
# behind the bracket of the upper arm's rear pivot, which stands on the rail beside it: v1's upper arm rear pivot
# is 100 mm behind the axle (K2's builtin double wishbone), the bracket reaches mounts.BRACKET_HALF_X_MM (25) behind
# it, then GAP_MM (10) and the post's half width (tower_b 30 / 2). Computed from v1's hard points in
# tests/solvers/test_designgen_footroom.py; build() checks each design's own (_check_rear_post).
BULKHEAD_DX_MIN_MM = 150.0
FOOT_FLOOR_MARGIN_MM = 10.0  # the footwell floor this far beyond the large driver's feet, each side
PANEL_T_MM = 20.0  # floor and bulkhead panels' thickness (as the first floors)
PANEL_KG_M2 = 6.0 / (0.45 * 0.6)  # the first v1's footwell floor (6 kg, 450 x 600 mm) per area: footwell and bulkhead
DECK_V1 = {"length": 950.0, "width": 880.0, "mass": 18.0}  # the first v1's deck; a narrower deck weighs less per area
DECK_TOWER_GAP_MM = 10.0  # the deck and the load this far inside the rear towers' posts
DRIVESHAFT_GAP_MM = 10.0  # the driveshafts this far under the rails at full bump
SUSP_PIVOT_GAP_MM = 25.0  # the arms' box (around the lower arm) this far off its pivots' legs and cross members
SUSP_HALF_HEIGHT_MM = 30.0  # the arms' box this far above and below the lower arm
DASH_GAP_MM = 10.0  # the dash cross member this far below the steering column
# The rails either side of the rear axle stay one member each (the hand calculations read the rail pieces at
# the rear axle joint as they are: frame_inputs.rail_joint_inputs, end_cross_member_inputs, and the roll bar's
# portal_frame_inputs at the node it splits them at): each rear tower is one post on the axle's rail node, and
# the brackets there are carried by that node.
REAR_OVERHANG_RAILS = ("rail_L_S2B", "rail_R_S2B", "rail_L_BE", "rail_R_BE")
# The first v1 put the large driver's toes 5 mm behind the front axle, beside the front wheels (the right
# shank 5 mm into the wheel, the feet 146 mm off the footwell floor). The defaults of wheelbase, seat_x,
# steering_x and battery_x move everything behind the footwell back by this much, so that the toes are
# behind the bulkhead (FOOT_SHIFT_MM: 320 mm puts them 15 mm behind it; K4 people.fit foot_room checks it).
FOOT_SHIFT_MM = 320.0
SEAT_LENGTH_MM = 500.0  # the seat's box along x and across (assumption, as the first v1)
SEAT_WIDTH_MM = 500.0
SEAT_HEIGHT_V1_MM = 260.0  # the first v1's seat box height: its cushion top this far above the floor (assumption)
SEAT_HEIGHT_MIN_MM = 200.0  # the seat's frame and cushion at least this tall (assumption, no source)
COLUMN_ANGLE_RANGE_DEG = (20.0, 50.0)  # steering_column_angle's range (its meaning below says why)


def _driver_v1() -> dict[str, float]:
    """The driver's place in the default design, from the AIST body sizes (k4_geometry.people) and the other
    variables' defaults, as the generator placed it before these were variables: the large driver (m95) in
    the seat with the heels on the footwell floor and the knee at people.KNEE_ANGLE_DEG, the accelerator
    under the ball of the right foot (pedal_x), the steering wheel's rim KNEE_GAP_MM above the knee's top
    with the column at kit/steering.COLUMN_ANGLE_DEG (steering_wheel_height: its centre above the seat's
    cushion top). Also the values the ranges come from (see the variables' meanings)."""
    from hiraku.solvers.k4_geometry import people

    d = {k: x.default for k, x in VARIABLES.items()}
    floor_top = d["rail_height"] + d["rail_h"] / 2.0 + ON_RAILS_MM + PANEL_T_MM  # the floors' top (both 20 mm)
    seat_top = floor_top + SEAT_HEIGHT_V1_MM
    back = d["seat_x"] - SEAT_LENGTH_MM / 2.0
    big = people.seated(back, seat_top, 0.0, floor_top, people.BIG)
    knee_top = float(big.legs["R"].knee[2]) + big.radii["thigh"]
    wheel_z = float(steering.wheel_centre(0.0, 0.0, knee_top, steering.COLUMN_ANGLE_DEG)[2])
    small = people.seated(back, seat_top, 0.0, floor_top, people.SMALL)
    f5 = people.SIZES[people.SMALL]
    return {
        "pedal_x": float(big.legs["R"].ball[0]),
        "steering_wheel_height": wheel_z - seat_top,
        "knee_top_above_seat": knee_top - seat_top,
        "small_ball_ahead_of_back": float(small.legs["R"].ball[0]) - back,
        "small_level_thigh_seat": f5["knee_joint_height"] - f5["trochanter_height"],
        "big_shoulder_above_seat": people.SIZES[people.BIG]["shoulder_height"],
    }


_DRIVER_V1 = _driver_v1()
_DRIVER_VARS = {
    "seat_height": Var("mm", SEAT_HEIGHT_V1_MM, SEAT_HEIGHT_MIN_MM, float(math.floor(_DRIVER_V1["small_level_thigh_seat"])),
                       "the seats' cushion top (the seat box's top) above the floor under them; the occupants, the "
                       "steering wheel (steering_wheel_height) and the roll bar's top (rollbar_height) go up with it. "
                       f"Range: at least {SEAT_HEIGHT_MIN_MM:g} mm for the seat's frame and cushion (assumption, no "
                       "source); at most the small person's (AIST young adult female 5th percentile) knee joint height "
                       "sitting I13 less the trochanterion height I11 (399.1 - 58.0 mm): higher, her thigh slopes down "
                       "from the hip to the knee with the heels on the floor"),
    "steering_column_angle": Var("deg", steering.COLUMN_ANGLE_DEG, *COLUMN_ANGLE_RANGE_DEG,
                                 "the steering column below the horizontal, forward and down from the steering wheel's "
                                 "centre (the wheel square to it); its length runs from the wheel's centre to above the "
                                 "pinion on the rack (kit/steering.column_length), and the intermediate shaft from there "
                                 "to the pinion. Range: from 20 deg (assumption, no source: flatter, the wheel stands "
                                 "within 20 deg of upright before the chest) to 50 deg (at the default steering_x a column "
                                 "steeper than about 50.6 deg puts the dash at or below the tops of the front towers' "
                                 "rear posts, which the dash posts stand on, and the layout does not close)"),
    "steering_wheel_height": Var("mm", _DRIVER_V1["steering_wheel_height"],
                                 float(math.ceil(_DRIVER_V1["knee_top_above_seat"])),
                                 _DRIVER_V1["big_shoulder_above_seat"],
                                 "the steering wheel's centre above the seats' cushion top (seat_height moves it with the "
                                 "seat). The default is where the generator put it before: the rim's lowest point "
                                 "50 mm above the large driver's knee top (AIST young adult male 95th percentile, "
                                 "knee at 100 deg, heels on the floor) with the column at 35 deg. Range: from the large "
                                 "driver's knee top above the seat (lower, the hub is at the knee) to his acromiale "
                                 "height sitting I3 (637.5 mm; higher, the hands reach up past the shoulders)"),
    "pedal_x": Var("mm", _DRIVER_V1["pedal_x"],
                   float(math.floor(VARIABLES["seat_x"].lo - SEAT_LENGTH_MM / 2.0 + _DRIVER_V1["small_ball_ahead_of_back"])),
                   0.0,
                   "where the ball of the driver's right foot presses the accelerator (x of the middle of the pad's "
                   "upper face, at the height of the ball with the heel on the footwell floor; the brake pedal beside "
                   "it); the driver's right leg reaches it (the knee bends or opens), and it stays when seat_x moves. "
                   "The default is under the large driver's ball of the foot with the knee at 100 deg in the default "
                   "seat. Range: from the small person's (AIST female 5th percentile) ball of the foot, knee at 100 "
                   "deg, in the seat at its rearmost seat_x, to the front axle (the footwell is behind it); whether "
                   "the pads stand on the footwell floor clear of the bulkhead is K4's (foot_support, foot_room)"),
}
_DRIVER_VARS["bulkhead_dx"] = Var(
    "mm", TOWER_MIN_DX_MM, BULKHEAD_DX_MIN_MM, TOWER_MIN_DX_MM,
    "the front towers' rear posts' centre line at least this far behind the front axle (the front bulkhead's "
    "front face is at the posts' front face, the dash posts stand on them, the footwell floor's front edge is at "
    "their rear face); farther back where the "
    "front wheels' steering sweep at the rails' outer face reaches farther (TYRE_GAP_MM behind it), and the "
    "footwell floor's front edge no farther forward than the sweep at the floor's width allows. Less than the "
    "default moves only the front towers' rear posts forward: the lower arms' rear legs keep their rail nodes "
    "(v1's leg passes 0.2 mm behind the rack's clamp; a post moved with it would reach the clamp). Range: from "
    f"{BULKHEAD_DX_MIN_MM:g} mm (the post GAP_MM behind the bracket of the upper arm's rear pivot on the rail, at "
    "v1's hard points; build() refuses a design where it is nearer) to the default (v1's, TOWER_MIN_DX_MM: farther "
    "back only shortens the footwell). Written into design.variables only when it is not the default")
VARIABLES = {k: x for name, var in VARIABLES.items()
             for k, x in ([(name, var)] + (list(_DRIVER_VARS.items()) if name == "battery_x" else []))}
OPTIONAL["bulkhead_dx"] = ("bulkhead_dx",)

# Options a failed requirement may call for (off by default, so that the defaults still build v1). Each is a
# variable (or a few) the designer may revise, with CHOICES below as short ways to set them.
# - part mounts (FN-15, R8 free_parts): a spacer or bracket (kit/attach.py) from each part of a group to the
#   nearest frame member, sized from the gap; a part none can reach is held through what it touches.
MOUNT_GROUPS = {
    "mount_floor": ("floor", "floor_front", "deck", "front_body"),
    "mount_electrics": ("battery", "controller", "aux", "main_switch"),
    "mount_seats": ("seat_R", "seat_L", "seat_belt_R", "seat_belt_L"),
    "mount_drive": ("reducer", "motor"),
}
_MOUNT_WORDS = {"mount_floor": "the floor panels, the deck and the front body (each on the rails or cross members "
                               "under it)",
                "mount_electrics": "the battery, the controller, the wiring box and the main switch (to the cross "
                                   "members beside, above or below them)",
                "mount_seats": "the seats and the seat belts' anchors (the seats stand on the floor, so a seat is "
                               "held through the floor; each belt's anchor goes to the nearest member). Without "
                               "mount_floor the floor, the seats and what touches them hang on the two belt anchors "
                               "alone (load_path R8-stress on version 1 with mount_seats only: 357 MPa in "
                               "rollbar_post_L, against 163 MPa allowed)",
                "mount_drive": "the reducer and the motor (to the rear cross member)"}
_OPTION_VARS: dict[str, Var] = {
    k: Var("1", 0.0, 0.0, 1.0, f"1: mounts (kit/attach.py: a spacer up to {20:g} mm, else a bracket of two plates; "
                               f"SS400, painted) from {_MOUNT_WORDS[k]}, sized from the gap to the nearest member; "
                               "0: none (the parts rest where they are placed, ON_RAILS_MM and the other gaps)")
    for k in MOUNT_GROUPS}
_OPTION_VARS["pedal_mount"] = Var(
    "1", 0.0, 0.0, 2.0, "0: the pedals are placed without a mount; 1: a stand under each pedal from the footwell "
    "floor up to the pedal's lowest edge (the floor must be held: mount_floor = 1); 2: a bracket from the front "
    "bulkhead's rear face back to each pedal's front edge (a pedal box). The stands follow pedal_x (kit/attach."
    "pedal_stands; SS400, painted)")
# - side guard (FN-05 push at point R, FN-07): a side frame each side outboard of the seats
from hiraku.solvers.k4_geometry import people as _people  # noqa: E402

_BIG = _people.SIZES[_people.BIG]
_OPTION_VARS.update({
    "side_bars": Var("1", 0.0, 0.0, 1.0, "1: a side frame each side, outboard of the seats at rollbar_width / 2: "
                     "posts on outriggers from the rails at the seat cross members' stations S1 and S2, and "
                     "side_bar_count bars along x between them from side_bar_low to side_bar_high above the seats' "
                     "cushion top (round tube side_bar_d x side_bar_t, STKM13A); with rollbar_front_posts = 1 the "
                     "posts at S1 are the front posts; 0: none"),
    "side_bar_count": Var("1", 2.0, 1.0, 4.0, "bars each side (whole number): evenly from side_bar_low to "
                          "side_bar_high (one bar: at side_bar_high)"),
    "side_bar_low": Var("mm", _BIG["trochanter_height"], 40.0, 400.0,
                        "the lowest bar's centre line above the seats' cushion top; the default is the hip of point R "
                        "(AIST young adult male 95th percentile trochanterion height sitting I11, k4_geometry.people), "
                        "where load_path pushes"),
    "side_bar_high": Var("mm", _BIG["shoulder_height"], 200.0, 800.0,
                         "the highest bar's centre line above the seats' cushion top (the posts' top); the default is "
                         "the shoulder of point R (AIST male 95th percentile acromiale height sitting I3)"),
    "side_bar_d": Var("mm", 42.7, 25.0, 60.5, "side bar and side post round tube outside diameter"),
    "side_bar_t": Var("mm", 2.3, 1.6, 4.5, "side bar and side post tube wall"),
    "side_net": Var("1", 0.0, 0.0, 1.0, "1: a net between the lowest and highest bar and the posts each side "
                    "(welded wire mesh, SS400 galvanized: SIDE_NET_WIRE_MM wires at SIDE_NET_PITCH_MM, an "
                    "assumption); needs side_bars = 1 and side_bar_count >= 2; 0: none"),
    # - ROPS shape (FN-01, R4)
    "rollbar_foot_b": Var("mm", 0.0, 0.0, 80.0, "outrigger (the roll bar's feet out from the rails, also the side "
                          "frame's) rect tube width; 0: as the cross members (cross_b), else at least 30 mm"),
    "rollbar_foot_h": Var("mm", 0.0, 0.0, 120.0, "outrigger rect tube height; 0: as the cross members (cross_h), else "
                          "at least 30 mm"),
    "rollbar_foot_t": Var("mm", 0.0, 0.0, 4.5, "outrigger tube wall; 0: as the cross members (cross_t), else at least "
                          "1.6 mm"),
    "rollbar_stays": Var("1", 0.0, 0.0, 1.0, "1: a rear stay each side from the roll bar's top corner back and down to "
                         "the rail at the frame's rear end E (members rops_stay_L/R, round tube rollbar_stay_d x "
                         "rollbar_stay_t of the roll bar's material); needs rollbar = 1; 0: none"),
    "rollbar_stay_d": Var("mm", 42.7, 25.0, 60.5, "rear stay round tube outside diameter"),
    "rollbar_stay_t": Var("mm", 2.3, 1.6, 4.5, "rear stay tube wall"),
    "rollbar_front_posts": Var("1", 0.0, 0.0, 1.0, "1: four posts: front posts on outriggers at the seat front cross "
                               "member S1 up to the roll bar's top height, a top rail each side back to the roll bar's "
                               "corners and a front top beam (members rops_*; the roll bar's tube); needs rollbar = 1; "
                               "0: the roll bar alone"),
    # - fenders (FN-19, shape only: the spray is not computed yet)
    "fenders": Var("1", 0.0, 0.0, 1.0, "1: a fender over each wheel on its upright (turns and travels with the wheel, "
                   "unsprung), from fender_front_deg ahead of the top to fender_rear_deg behind it, fender_gap over the "
                   "tyre, fender_width wide (molded HDPE, FENDER_T_MM thick, FENDER_PIECES straight pieces); 0: none"),
    "fender_front_deg": Var("deg", 30.0, 0.0, 90.0, "how far the fender reaches ahead of the wheel's top, as the angle at "
                            "the wheel centre from the vertical; the default is the intake department's FN-19 value "
                            "(細目告示 第 178 条 第 2 項 in their table, not read here)"),
    "fender_rear_deg": Var("deg", 50.0, 0.0, 120.0, "how far it reaches behind the top (FN-19's 50 deg, as above)"),
    "fender_width": Var("mm", 240.0, 120.0, 400.0, "fender width across the wheel (at least the tyre's)"),
    "fender_gap": Var("mm", 40.0, 10.0, 150.0, "the fender's inner face this far over the tyre (assumption)"),
})
INTEGERS = ("side_bar_count", "pedal_mount")
ZERO_OR_AT_LEAST = {"rollbar_foot_b": 30.0, "rollbar_foot_h": 30.0, "rollbar_foot_t": 1.6}
SWITCHES = SWITCHES + tuple(MOUNT_GROUPS) + ("side_bars", "side_net", "rollbar_stays", "rollbar_front_posts", "fenders")
VARIABLES.update(_OPTION_VARS)
OPTIONAL.update({
    "part_mounts": tuple(MOUNT_GROUPS),
    "pedal_mount": ("pedal_mount",),
    "side_bars": ("side_bars", "side_bar_count", "side_bar_low", "side_bar_high", "side_bar_d", "side_bar_t", "side_net"),
    "rollbar_foot": ("rollbar_foot_b", "rollbar_foot_h", "rollbar_foot_t"),
    "rollbar_stays": ("rollbar_stays", "rollbar_stay_d", "rollbar_stay_t"),
    "rollbar_front_posts": ("rollbar_front_posts",),
    "fenders": ("fenders", "fender_front_deg", "fender_rear_deg", "fender_width", "fender_gap"),
})
SIDE_NET_WIRE_MM = 3.0  # the side net's wire diameter (assumption)
SIDE_NET_PITCH_MM = 50.0  # its mesh pitch both ways (assumption)
FENDER_T_MM = 3.0  # fender wall (assumption)
FENDER_PIECES = 4
HDPE = {"name": "HDPE (high-density polyethylene), molded", "E_MPa": 1000.0, "nu": 0.4, "density_kg_m3": 950.0,
        "yield_MPa": 25.0, "source": "assumption: round values of the order of HDPE's; not checked against a cited "
                                    "source", "verified": False}
# The occupants' bodies as parts (kit/body.py, hiraku.contract.loading.person_of: the parts of a person
# share the id before the first "."): the trunk keeps the occupant's id, the limbs are <id>.<segment>_<L|R>.
BODY_PARTS = True
TITLE = "2 人乗り 4 輪のはしご形フレームの電動車 (前輪で操舵、後輪で駆動、右ハンドル)"
DESCRIPTION = "2-seat electric off-road vehicle, ladder frame, rear drive, right-hand drive"
# How the T joints (a cross member ending on the side of a rail) are made: butt (the cross member
# welded on the rail's side, the first version), gusset (plus triangular plates, gusset_leg and
# gusset_thickness) or through (the cross member through the rail's inner wall to its outer wall).
# A shortcut for the variables in JOINT_VALUES; the variables alone give the same design.
JOINT_VALUES = {"butt": {"gusset_leg": 0.0, "cross_members_through": 0.0},
                "gusset": {"gusset_leg": GUSSET_LEG_MM, "cross_members_through": 0.0},
                "through": {"gusset_leg": 0.0, "cross_members_through": 1.0}}
# The other choices: each option stands for the switches it sets (the variables alone give the same design).
# part_mounts: none or brackets for every group (MOUNT_GROUPS; a mix of groups reads as "some"); pedal_mount;
# side_guard: none, bars or bars and a net; rops: the roll bar alone (hoop), with rear stays, or four posts with
# stays; fenders.
OPTION_VALUES: dict[str, dict[str, dict[str, float]]] = {
    "part_mounts": {"none": {k: 0.0 for k in MOUNT_GROUPS}, "brackets": {k: 1.0 for k in MOUNT_GROUPS}},
    "pedal_mount": {"none": {"pedal_mount": 0.0}, "floor": {"pedal_mount": 1.0}, "bulkhead": {"pedal_mount": 2.0}},
    "side_guard": {"none": {"side_bars": 0.0, "side_net": 0.0}, "bars": {"side_bars": 1.0, "side_net": 0.0},
                   "bars_net": {"side_bars": 1.0, "side_net": 1.0}},
    "rops": {"hoop": {"rollbar_stays": 0.0, "rollbar_front_posts": 0.0},
             "hoop_stays": {"rollbar_stays": 1.0, "rollbar_front_posts": 0.0},
             "four_post": {"rollbar_stays": 0.0, "rollbar_front_posts": 1.0},
             "four_post_stays": {"rollbar_stays": 1.0, "rollbar_front_posts": 1.0}},
    "fenders": {"none": {"fenders": 0.0}, "wheel": {"fenders": 1.0}},
}
CHOICES: dict[str, tuple[str, ...]] = {"joints": ("butt", "gusset", "through"),
                                       **{k: tuple(opts) for k, opts in OPTION_VALUES.items()}}
CHOICE_SIZES = ("gusset_leg",)  # set by a choice, but may be given as a value with it


def applies(flags: dict) -> bool:
    """For products that move by themselves and carry people or cargo."""
    return bool(flags.get("self_propelled")) and bool(flags.get("ride") or flags.get("cargo"))


# Part list order of v1 (the rest after them as placed: each wheel, then its suspension)
PART_ORDER = ["battery", "main_switch", "motor", "reducer", "driveshaft_RL", "driveshaft_RR", "controller", "aux",
              "floor_front", "pedal_accel", "pedal_brake", "brake_master", "floor", "deck", "front_body", "bulkhead",
              "steering_wheel",
              "steering_column", "steering_shaft", "steering_rack", "tie_rod_FL", "tie_rod_FR", "seat_R", "seat_belt_R",
              "seat_L", "seat_belt_L", "occupant_R", "occupant_L", "cargo"]


def _kit(fn, *args, **kw):
    """Call a kit's placing function; a part it cannot place is a DesignGenError (it says why)."""
    try:
        return fn(*args, **kw)
    except DesignGenError:
        raise
    except ValueError as e:
        raise DesignGenError(str(e)) from None


@functools.lru_cache(maxsize=1)
def _reference_shafts_kg() -> float:
    """The driveshafts' mass of the default design (v1): MOTOR_V1's mass was the motor, the reducer and these."""
    d = build(_reference=True)
    return float(sum(p["mass_kg"] for p in d["parts"] if p["kind"] == "driveshaft"))


def build(values: dict | None = None, *, version: int = 1, parent: int | None = None, reason: str | None = None,
          name: str | None = None, _reference: bool | None = None) -> Record:
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

    # running gear: front steer, rear drive, double wishbones with coil springs (K2's builtin K&C table;
    # the spring rate is the wheel rate along the wheel centre's vertical travel)
    def spring(rate: float) -> Suspension:
        return Suspension("kc_table", rate, 1.2, v["suspension_travel"], "builtin:double_wishbone")

    supports = four_wheels(wb, track, r, ww, spring(v["front_spring_rate"]), spring(v["rear_spring_rate"]))
    rail = {"y": rail_y, "z": rail_z, "b": v["rail_b"], "h": v["rail_h"]}
    write_hardpoints({"supports": supports}, rail, v["tower_b"] / 2.0)  # before the parts: the kits place from them

    # parts, each against the frame or a part already placed
    lay = Layout({**frame.part(), "supports": supports, "parts": []})
    on_rails = lay.rail_top() + ON_RAILS_MM
    S1, S2, E = (lay.station_x(k) for k in ("S1", "S2", "E"))
    seat_x, seat_y = v["seat_x"], v["seat_y"]
    # the footwell: the front bulkhead's front face TYRE_GAP_MM behind the front wheels' steering sweep at
    # the rails' outer face, the dash posts in it, the footwell floor from the posts' back face (and clear
    # of the sweep at its own width) to S1, wide enough for the large driver's feet (assumption: the feet
    # rest at the seat's y +- the legs' spacing, k4_geometry.people)
    from hiraku.solvers.k4_geometry import people

    sweep_r = math.hypot(r, ww / 2.0)
    x_a = lay.station_x("A")
    sweep_front = x_a - _behind(sweep_r, track / 2.0 - rail_y - v["rail_b"] / 2.0) - TYRE_GAP_MM
    bulkhead_front = min(sweep_front, x_a - v["bulkhead_dx"] + v["tower_b"] / 2.0)
    post_x = bulkhead_front - v["tower_b"] / 2.0
    # the towers' posts and the lower arms' legs everywhere else as at bulkhead_dx's default
    post_dx = x_a - (min(sweep_front, x_a - TOWER_MIN_DX_MM + v["tower_b"] / 2.0) - v["tower_b"] / 2.0)
    feet_half = seat_y + people.leg_spacing(people.BIG) + people.SIZES[people.BIG]["foot_breadth"] / 2.0
    floor_half = feet_half + FOOT_FLOOR_MARGIN_MM
    floor_front_x = min(post_x - v["tower_b"] / 2.0, x_a - _behind(sweep_r, track / 2.0 - floor_half) - TYRE_GAP_MM)
    if floor_front_x - S1 < MIN_STATION_GAP_MM:
        raise DesignGenError(f"the footwell floor from x = {floor_front_x:.0f} mm (behind the front wheels' steering "
                             f"sweep) to the seat cross member S1 ({S1:g} mm) is shorter than {MIN_STATION_GAP_MM:g} "
                             f"mm; move seat_x back or make the wheelbase longer")
    ff_size = (floor_front_x - S1, 2.0 * floor_half, PANEL_T_MM)
    lay.put(Item("floor_front", "足元の床", "body", ff_size, PANEL_KG_M2 * ff_size[0] * ff_size[1] * 1e-6),
            x=rear_at(S1), y=0.0, z=bottom_at(on_rails))
    lay.put(Item("floor", "座席の下の床", "body", (S1 - S2, track, 20.0), 8.0), x=lay.between("S1", "S2"), y=0.0,
            z=bottom_at(on_rails))
    deck_len = S2 - DECK_GAP_MM - E
    # the deck and the load between the rear suspension towers and the upper arms' pivot brackets (both stand
    # on the rails); the front body between the front ones
    def inside_mounts(front: bool) -> float:
        ss = [s for s in supports if s["actuation"]["steer"] == front]
        uca = min(abs(s["suspension"]["hardpoints_mm"][k][1]) for s in ss for k in ("uca_front", "uca_rear"))
        return min(rail_y - v["tower_b"] / 2.0, uca - mounts.BRACKET_PAD_MM) - DECK_TOWER_GAP_MM

    deck_w = min(DECK_V1["width"], 2.0 * inside_mounts(False))
    lay.put(Item("deck", "荷台の床", "body", (deck_len, deck_w, 20.0),
                 DECK_V1["mass"] * deck_len * deck_w / (DECK_V1["length"] * DECK_V1["width"])),
            x=rear_at(E), y=0.0, z=bottom_at(on_rails), carried_by=lay.at("S2", "B", "E"))
    lay.put(Item("front_body", "前の外板とバンパー", "body", (360.0, min(600.0, 2.0 * inside_mounts(True)), 280.0), 15.0),
            x=front_at(lay.station_x("F")), y=0.0, z=bottom_at(on_rails))
    # the battery fits between the rails under the floor; its volume and mass go with the capacity
    scale = v["battery_capacity"] / BATTERY_V1["capacity"]
    b_w = min(BATTERY_V1["width"], lay.rail_inner_gap() - 20.0)
    b_l = BATTERY_V1["length"] * BATTERY_V1["width"] / b_w * scale
    lay.put(Item("battery", f"電池 (LiFePO4 {v['battery_voltage']:g} V {v['battery_capacity']:g} Ah)", "battery",
                 (b_l, b_w, BATTERY_V1["height"]), BATTERY_V1["mass"] * scale),
            x=v["battery_x"], y=0.0, z=top_at(lay.bottom_of("floor")))
    motor_mass = MOTOR_V1["mass"] * max(v["motor_peak_power"] / MOTOR_V1["power"], v["motor_peak_torque"] / MOTOR_V1["torque"])
    material = json.loads(MATERIALS.read_text(encoding="utf-8"))["stkr400"]
    rho = float(material["density_kg_m3"])
    shaft = (v["driveshaft_diameter"], v["driveshaft_wall"])
    # the reducer's outputs low enough that the driveshafts pass under the rails at full bump
    drop = drivetrain.drop_to_clear(lay.design, rail_y, rail_z - v["rail_h"] / 2.0, v["driveshaft_diameter"] / 2.0,
                                    DRIVESHAFT_GAP_MM)
    drop = float(math.ceil(drop))
    lay.design["variables"] = {drivetrain.DROP_VARIABLE: _q(drop, "mm")}
    ref = None if _reference is not None else _reference_shafts_kg()  # the earlier motor's mass held v1's shafts
    drive = _kit(drivetrain.place, lay, motor_mass, "B", shaft, rho, shafts=v["driveshafts"] == 1, reference_shafts=ref)
    lay.put(Item("controller", "モーターの制御器", "controller", (250.0, 200.0, 80.0), 4.0),
            x=front_at(S2 + CONTROLLER_LEAD_MM), y=0.0, z=top_at(lay.rail_bottom()), carried_by=lay.at("S2"))
    lay.put(Item("aux", "配線と 12 V の補機", "electrical", (300.0, 300.0, 100.0), 6.0),
            x=rear_at(S1), y=0.0, z=top_at(lay.rail_bottom() + AUX_DROP_MM))
    _kit(safety.place_main_switch, lay, "aux")
    if v["lamps"] == 1:
        _kit(safety.place_lamps, lay, "front_body", E - v["cross_b"] / 2.0, rail_z, "deck")
    for pid, label, y in (("seat_R", "運転席", -seat_y), ("seat_L", "助手席", seat_y)):
        lay.put(Item(pid, label, "seat", (SEAT_LENGTH_MM, SEAT_WIDTH_MM, v["seat_height"]), 12.0), x=seat_x, y=y,
                z=bottom_at(lay.top_of("floor")))
    _kit(safety.place_belts, lay, ("seat_R", "seat_L"))
    for pid, label, seat in (("occupant_R", "運転者 (体重は強さの不利側)", "seat_R"), ("occupant_L", "同乗者 (体重は強さの不利側)", "seat_L")):
        sy = lay.part(seat)["box"]["center_mm"][1]
        lay.put(Item(pid, label, "occupant", (450.0, 450.0, 520.0), v["occupant_mass"]),
                x=seat_x - OCCUPANT_BACK_MM, y=sy, z=bottom_at(lay.top_of(seat)))
    # the pedals at pedal_x with the driver's right foot on them, then the steering wheel at steering_wheel_height
    # above the seat and its column at steering_column_angle (kit/body.py, kit/steering.py)
    feet = _kit(body.place_pedals, lay, "seat_R", "floor_front", outboard_limit=rail_y + v["rail_b"] / 2.0,
                ball_x=v["pedal_x"])
    _kit(steering.place, lay, 12.0, v["steering_x"] - STEERING_WHEEL_BEHIND_MM, -seat_y, -seat_y + STEERING_INBOARD_MM,
         None, rho, avoid=("pedal_accel", "pedal_brake"), column_angle=v["steering_column_angle"],
         wheel_z=lay.top_of("seat_R") + v["steering_wheel_height"])
    if BODY_PARTS:
        from hiraku.solvers.k4_geometry.people import grip_points

        for occ, seat in (("occupant_R", "seat_R"), ("occupant_L", "seat_L")):
            pose = feet["pose"] if occ == "occupant_R" else body.driver_pose(lay, seat, "floor_front")
            grips = grip_points(lay.part("steering_wheel")) if occ == "occupant_R" else None
            body.occupant_parts(lay, occ, pose, grips)
    lay.put(Item("cargo", "荷", "cargo", (deck_len - CARGO_SHORT_MM, deck_w, v["cargo_height"]), v["cargo_mass"]),
            x=lay.part("deck")["box"]["center_mm"][0], y=0.0, z=bottom_at(lay.top_of("deck")), carried_by=lay.at("S2", "B", "E"))
    for s in supports:  # each wheel (unsprung) and its arms: the arms' box around the lower arm
        x, y, z = s["position_mm"]
        corner = corner_name(s, supports)
        lay.put(Item(f"wheel_{s['id']}", f"{corner}の車輪 (タイヤ・ホイール・ハブ・ブレーキの円板)", "wheel", (2 * r, ww, 2 * r),
                     WHEEL_MASS_KG), x=x, y=y, z=z, carried_by=None)
        hp = s["suspension"]["hardpoints_mm"]
        lo_x, hi_x = sorted((hp["lca_rear"][0], hp["lca_front"][0]))
        ys = sorted((hp["lca_front"][1] + math.copysign(SUSP_PIVOT_GAP_MM, y), hp["lca_outer"][1]))
        lay.put(Item(f"susp_{s['id']}", f"{corner}のサスペンション (アームとダンパー。箱は下の腕のまわり)", "suspension",
                     (hi_x - lo_x - 2 * SUSP_PIVOT_GAP_MM, ys[1] - ys[0], 2 * SUSP_HALF_HEIGHT_MM), 8.0),
                x=(lo_x + hi_x) / 2.0, y=(ys[0] + ys[1]) / 2.0, z=hp["lca_outer"][2], carried_by=None)
    _kit(steering.place_corners, lay, rho)  # knuckles and the rear toe links, out of the suspension parts
    # the front bulkhead (a panel between the rails' outer faces, from the rails up to the dash cross member
    # under the steering column) and the brakes' master cylinder on its back face
    dash_z = _dash_height(lay, post_x, v["tower_b"])
    bh_h = dash_z - v["tower_b"] / 2.0 - on_rails
    if bh_h <= 0:
        raise DesignGenError("the dash cross member under the steering column would be below the rails; "
                             "move steering_x or seat_x")
    bh_size = (PANEL_T_MM, 2.0 * (rail_y + v["rail_b"] / 2.0), bh_h)
    lay.put(Item("bulkhead", "前の隔壁 (足元と前の区画の仕切りの板)", "body", bh_size,
                 PANEL_KG_M2 * bh_size[1] * bh_size[2] * 1e-6),
            x=front_at(bulkhead_front), y=0.0, z=bottom_at(on_rails), carried_by=None)
    _kit(safety.place_brakes, lay, "pedal_brake", "bulkhead", "floor_front", feet["pose"])
    lay.reorder(PART_ORDER)
    materials = {"stkr400": material}
    # the suspension's mounts on the frame (kit/mounts.py): towers, the lower arms' legs and cross members,
    # brackets; then the dash posts and cross member on the front towers' rear posts, holding the column
    tower = frame.section(*rect_tube(v["tower_b"], v["tower_b"], v["tower_t"]))
    lower = frame.section(*rect_tube(v["lower_b"], v["lower_b"], v["lower_t"]))
    for key, (b_, t_) in (("tower", (v["tower_b"], v["tower_t"])), ("lower", (v["lower_b"], v["lower_t"]))):
        if 2.0 * t_ >= b_:
            raise DesignGenError(f"{key}_t ({t_:g} mm) must be less than half of {key}_b ({b_:g} mm)")
    if v["tower_b"] >= v["rail_b"]:
        raise DesignGenError(f"tower_b ({v['tower_b']:g} mm) must be less than rail_b ({v['rail_b']:g} mm): the towers' "
                             f"posts stand on the rails' top walls")
    try:
        placed = mounts.place(frame, lay, supports, {"tower": tower, "leg": lower, "lower": lower,
                                                     "dims": {"lower": {"b": v["lower_b"], "h": v["lower_b"]}}},
                              rho, post_dx, rail, avoid=("motor", "reducer"), lower_cross=v["lower_cross"] == 1,
                              whole=REAR_OVERHANG_RAILS,
                              rear_post_dx={"A": x_a - post_x} if x_a - post_x != post_dx else None)
    except (mounts.MountError, ValueError) as e:
        raise DesignGenError(str(e)) from None
    _check_rear_post(supports, post_x, v["tower_b"])
    dash = build_dash(frame, lay, post_x, dash_z, rail_y, tower, rho)
    lay.part("bulkhead")["carried_by"] = dash["bulkhead_nodes"]
    rack_nodes = [lay.part(f"bracket_tie_{s['id']}")["carried_by"][0] for s in supports if s["actuation"]["steer"]]
    lay.part("steering_rack")["carried_by"] = rack_nodes  # the rack hangs on its clamps
    for pid in ("steering_column", "steering_wheel", "steering_shaft"):
        lay.part(pid)["carried_by"] = list(dash["column_nodes"])
    # the parts that were fixed at the front axle's rail nodes (between the front tower's posts now) are
    # fixed where they are: the footwell parts at the bulkhead's posts and S1, the wiring box and the
    # main switch at S1, the front body at the front towers' front posts
    feet_nodes = {"L": "LAp2", "R": "RAp2"}
    front_nodes = {"L": "LAp1", "R": "RAp1"}
    for p in lay.parts:
        cb = p.get("carried_by") or []
        if not any(n in ("LA", "RA") for n in cb) or p["kind"] in ("bracket", "gusset", "frame"):
            continue
        if p["id"] == "front_body":
            p["carried_by"] = [front_nodes[n[0]] if n in ("LA", "RA") else n for n in cb]
        elif p["id"] in ("aux", "main_switch"):
            p["carried_by"] = [n for n in cb if n not in ("LA", "RA")] or cb
        else:
            p["carried_by"] = [feet_nodes[n[0]] if n in ("LA", "RA") else n for n in cb]
    # joint reinforcement last, so that a design's other parts come first as in the design without it
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
    foot = cross
    if any(v[k] for k in ZERO_OR_AT_LEAST):
        fb, fh, ft = (v[f"rollbar_foot_{k}"] or v[f"cross_{k}"] for k in ("b", "h", "t"))
        if 2.0 * ft >= min(fb, fh):
            raise DesignGenError(f"the outriggers' wall ({ft:g} mm) must be less than half of their size ({fb:g} x {fh:g} mm)")
        foot = frame.section(*rect_tube(fb, fh, ft))
    if v["rollbar"] == 1:
        key = build_rollbar(frame, lay, v, foot)
        materials.setdefault(key, json.loads(MATERIALS.read_text(encoding="utf-8"))[key])
    elif v["rollbar_stays"] == 1 or v["rollbar_front_posts"] == 1:
        raise DesignGenError("rollbar_stays and rollbar_front_posts need the roll bar (rollbar = 1)")
    columns = build_rops_extra(frame, lay, v, foot) if v["rollbar"] == 1 else {}
    guard = build_side_guard(frame, lay, v, foot, columns) if v["side_bars"] == 1 else None
    if v["side_net"] == 1 and (guard is None or len(guard["heights"]) < 2):
        raise DesignGenError("side_net needs the side bars (side_bars = 1) and at least two of them (side_bar_count >= 2)")
    if guard is not None:
        materials.setdefault("stkm13a", json.loads(MATERIALS.read_text(encoding="utf-8"))["stkm13a"])

    written ={k for names in OPTIONAL.values() if any(v[n] != VARIABLES[n].default for n in names) for k in names}
    variables = {k: _q(v[k], VARIABLES[k].unit) for k in _V1_ORDER}
    variables.update({k: _q(v[k], var.unit) for k, var in VARIABLES.items()
                      if k not in variables and (k in written or not any(k in g for g in OPTIONAL.values()))})
    variables["tyre_relaxation_length"] = _q(r, "mm")
    variables["wheel_inertia"] = _q(WHEEL_MASS_KG * (r / 1000.0) ** 2 / 2.0, "kg*m^2")
    variables["motor_rotor_inertia"] = _q(drive["rotor_inertia"], "kg*m^2")
    variables["reducer_output_inertia"] = _q(drive["gear_inertia"], "kg*m^2")
    variables[drivetrain.DROP_VARIABLE] = _q(drop, "mm")
    design = {
        "design_version": version,
        "parent_version": parent,
        "name": name or f"offroad-4w v{version}: {DESCRIPTION}",
        "change_reason": reason or "first version (fixed demo design)",
        "variables": variables,
        "supports": supports,
        "parts": lay.parts,
        **frame.part(),
        "materials": materials,
    }
    safety.add_rollbar_part(design)
    for m in design["members"]:
        if m["id"].startswith("rops_"):
            m["part"] = safety.ROLLBAR_PART
    add_options(design, v, guard, rho)
    systems.write(design)  # system and connects_to of every part, when the contract has them
    if x_a - post_x != post_dx:
        _check_rear_legs(design)
    return design


def _behind(radius: float, across: float) -> float:
    """How far (mm) behind a wheel's centre its steering sweep reaches at `across` mm inboard of the centre:
    the disc of the tyre box's corner radius (any steer angle; the safe side)."""
    return math.sqrt(radius * radius - across * across) if across < radius else 0.0


def rear_post_room(supports: list[Record], post_x: float, tower_b: float) -> float:
    """How far (mm) the front towers' rear posts' front face is behind the brackets of the front upper arms'
    rear pivots (which stand on the rail beside the post, kit/mounts), less mounts.GAP_MM: negative, the post
    is too near."""
    pivots = [s["suspension"]["hardpoints_mm"]["uca_rear"][0] for s in supports
              if s["actuation"]["steer"] and "hardpoints_mm" in s.get("suspension", {})]
    if not pivots:
        return math.inf
    return min(pivots) - mounts.BRACKET_HALF_X_MM - (post_x + tower_b / 2.0) - mounts.GAP_MM


def _check_rear_post(supports: list[Record], post_x: float, tower_b: float) -> None:
    room = rear_post_room(supports, post_x, tower_b)
    if room < 0.0:
        raise DesignGenError(f"the front towers' rear posts (x = {post_x:.0f} mm) come {-room:.1f} mm too near the "
                             f"brackets of the upper arms' rear pivots; make bulkhead_dx larger")


def _check_rear_legs(design: Record) -> None:
    """With the front towers' rear posts moved forward (bulkhead_dx), each front lower arm's rear leg starts
    from its own rail node behind the post: it must not pass through the post or the rail beside it (K4's
    member boxes, more than k4_geometry.model.TOUCH_MM into a member it shares no node with)."""
    from hiraku.solvers.k4_geometry.model import TOUCH_MM, bodies, shape_overlap

    bs = {k: b for k, b in bodies(design).items() if b.kind == "member"}
    for side in "LR":
        leg = bs.get(f"member:lower_leg_{side}A2")
        if leg is None:
            continue
        for k, b in bs.items():
            if b is leg or leg.nodes & b.nodes:
                continue
            o = shape_overlap(leg, b)
            if o > TOUCH_MM:
                raise DesignGenError(f"with bulkhead_dx = {design['variables']['bulkhead_dx']['value']:g} mm the front "
                                     f"lower arm's rear leg (from its rail node behind the moved post) goes {o:.1f} mm "
                                     f"into {k.split(':', 1)[1]}; move the post farther forward (smaller bulkhead_dx) "
                                     f"or leave it at the default")


def _dash_height(lay: Layout, x: float, b: float) -> float:
    """The dash cross member's centre height at x: DASH_GAP_MM below the steering column (its radius and the
    member's half size kept off), the column's axis taken from its box."""
    from hiraku.designgen.kit.steering import COLUMN_RADIUS_MM, _axis

    top, low = _axis(lay.part("steering_column"))
    if not min(low[0], top[0]) <= x <= max(low[0], top[0]):
        raise DesignGenError(f"the steering column ({top[0]:.0f} to {low[0]:.0f} mm along x) does not pass over the "
                             f"dash at x = {x:.0f} mm; move steering_x")
    z_col = float(top[2] + (low[2] - top[2]) * (x - top[0]) / (low[0] - top[0]))
    slope = math.atan2(abs(top[2] - low[2]), abs(top[0] - low[0]))
    return z_col - (COLUMN_RADIUS_MM + b / 2.0 + DASH_GAP_MM) / math.cos(slope)


def build_dash(frame: Frame, lay: Layout, post_x: float, dash_z: float, rail_y: float, section: str,
               rho: float) -> Record:
    """The dash posts (on from the front towers' rear posts up to dash_z), the dash cross member between their
    tops (members dash_post_L, dash_post_R, dash_cross) and the column's bracket (kind bracket) from the
    cross member up around the column. Returns {"column_nodes", "bulkhead_nodes"}."""
    from hiraku.designgen.kit.steering import COLUMN_RADIUS_MM, _axis

    tops = {}
    for side, sy in (("L", 1.0), ("R", -1.0)):
        below = next((k for k, q in frame.nodes.items() if abs(q[0] - post_x) < 1e-6 and abs(q[1] - sy * rail_y) < 1e-6
                      and q[2] < dash_z and any(m["id"].startswith("tower_post_") and k == m["node_b"]
                                                for m in frame.members)), None)
        if below is None:
            raise DesignGenError(f"no front tower post at x = {post_x:.0f} mm on the {side} rail for the dash post")
        tops[side] = frame.node(f"{side}DT", [post_x, sy * rail_y, dash_z])
        frame.member(f"dash_post_{side}", below, tops[side], section)
    frame.member("dash_cross", tops["L"], tops["R"], section)
    col = lay.part("steering_column")
    top, low = _axis(col)
    z_col = float(top[2] + (low[2] - top[2]) * (post_x - top[0]) / (low[0] - top[0]))
    slope = math.atan2(abs(top[2] - low[2]), abs(top[0] - low[0]))
    y_col = float(col["box"]["center_mm"][1])
    # the clamp's seat: from the cross member's centre up to just under the column along its length
    # (the clamp's band round the column is not drawn)
    lo = [post_x - mounts.BRACKET_HALF_X_MM, y_col - COLUMN_RADIUS_MM - mounts.BRACKET_PAD_MM, dash_z]
    hi = [post_x + mounts.BRACKET_HALF_X_MM, y_col + COLUMN_RADIUS_MM + mounts.BRACKET_PAD_MM,
          z_col - COLUMN_RADIUS_MM / math.cos(slope) - mounts.BRACKET_HALF_X_MM * math.tan(slope) - 1.0]
    nodes = [tops["L"], tops["R"]]
    lay.parts.append(mounts._bracket("bracket_column", "ステアリングのコラムの金具 (ダッシュの横の部材の上)", lo, hi, nodes,
                                     rho))
    posts_low = [m["node_a"] for m in frame.members if m["id"].startswith("dash_post_")]
    return {"column_nodes": nodes, "bulkhead_nodes": sorted(set(posts_low) | set(nodes)) +
            sorted({m["node_a"] for m in frame.members if m["node_b"] in posts_low and m["id"].startswith("tower_post_")})}


def write_hardpoints(design: dict, rail: dict | None = None, top_half: float = 0.0) -> dict:
    """Each support on the builtin K&C table carries its double-wishbone hard points (suspension.hardpoints_mm),
    so that K2, the frame and the screens read the same points: K2's builtin layout, its upper arm raised and
    the damper's top moved out until both clear the rail through the travel and the damper's top raised over
    the upper ball joint at full bump (kit/mounts.fit_hardpoints; rail = {"y", "z", "b", "h"} of the left
    rail, top_half: half the tower tube's size), or K2's builtin points as they are without a rail.
    Returns {support id: the clearances (mounts.rail_clearances and the moved values)}."""
    from hiraku.solvers.k2_vehicle.model import hardpoints_of

    out = {}
    for s in design["supports"]:
        sus = s.get("suspension", {})
        if sus.get("type") == "kc_table" and str(sus.get("kc_table", "")).startswith("builtin:"):
            try:
                if rail is None:
                    points = hardpoints_of(design, s["id"])
                else:
                    points, out[s["id"]] = mounts.fit_hardpoints(s, rail, float(sus.get("travel_mm") or 0.0), top_half)
            except (ValueError, mounts.MountError) as e:  # K2's arms do not close for these sizes
                raise DesignGenError(f"the suspension of {s['id']} cannot be laid out ({e}); change the wheel radius, "
                                     f"the track or the travel") from None
            sus["hardpoints_mm"] = {k: [float(x) for x in p] for k, p in points.items() if k not in ("axle", "radius_mm")}
    return out


ROLLBAR_STATION = "H"  # the rail nodes LH, RH under the roll bar; LHO, RHO the outriggers' ends; LHT, RHT the top corners


def frame_end(frame: Frame, member_id: str, i: int) -> str:
    """Node a (i = 0) or b (i = 1) of a frame member."""
    m = next(x for x in frame.members if x["id"] == member_id)
    return m["node_a"] if i == 0 else m["node_b"]


def build_rollbar(frame: Frame, lay: Layout, v: dict[str, float], foot_section: str) -> str:
    """Add the roll bar to the frame from the variables, and return its material key.

    Placed from what is there: x = the seat's rear cross member S2 minus rollbar_offset (the two rails
    are split at a node there); feet at the rail centre height, out to y = +-rollbar_width/2 on outriggers
    of foot_section (none when the width is the rail spacing); posts straight up to the top beam, its centre
    line rollbar_height above the seats' box top. Members rollbar_foot_<L|R>, rollbar_post_<L|R>, rollbar_top.
    The parts are placed before this, so their carried_by nodes do not change."""
    s2, b = lay.station_x("S2"), lay.station_x("B")
    x = s2 - v["rollbar_offset"]
    if x - b < MIN_STATION_GAP_MM:
        raise DesignGenError(f"the roll bar at x = {x:g} mm is closer than {MIN_STATION_GAP_MM:g} mm to the rear axle "
                             f"station B ({b:g} mm); make rollbar_offset smaller")
    half = v["rollbar_width"] / 2.0
    rail_y, rail_z = v["rail_spacing"] / 2.0, v["rail_height"]
    if half < rail_y - 1e-9:
        raise DesignGenError(f"rollbar_width ({v['rollbar_width']:g} mm) must be at least rail_spacing "
                             f"({v['rail_spacing']:g} mm): the posts stand on the rails or outboard of them")
    seat_top = max(lay.top_of(p["id"]) for p in lay.parts if p["kind"] == "seat")
    z_top = seat_top + v["rollbar_height"]
    if v["rollbar_square"] == 1:
        sec = frame.section(*rect_tube(v["rollbar_b"], v["rollbar_b"], v["rollbar_t"]))
        size = v["rollbar_b"]
    else:
        sec = frame.section(*round_tube(v["rollbar_d"], v["rollbar_t"]))
        size = v["rollbar_d"]
    if 2.0 * v["rollbar_t"] >= size:
        raise DesignGenError(f"rollbar_t ({v['rollbar_t']:g} mm) must be less than half of the tube size ({size:g} mm)")
    mat = ROLLBAR_MATERIALS[v["rollbar_square"]]
    h = ROLLBAR_STATION
    corners = {}
    for side, sy in (("L", 1.0), ("R", -1.0)):
        rail = mounts.member_through(frame, [x, sy * rail_y, rail_z]) or f"rail_{side}_S2B"
        ids = ((f"rail_{side}_S2{h}", f"rail_{side}_{h}B") if rail == f"rail_{side}_S2B" else
               (f"rail_{side}_{frame_end(frame, rail, 0)}_{side}{h}", f"rail_{side}_{side}{h}_{frame_end(frame, rail, 1)}"))
        foot = frame.split(rail, f"{side}{h}", [x, sy * rail_y, rail_z], ids)
        if half > rail_y + 1e-9:
            tip = frame.node(f"{side}{h}O", [x, sy * half, rail_z])
            frame.member(f"rollbar_foot_{side}", foot, tip, foot_section)
            foot = tip
        corners[side] = frame.node(f"{side}{h}T", [x, sy * half, z_top])
        frame.member(f"rollbar_post_{side}", foot, corners[side], sec, mat)
    frame.member("rollbar_top", corners["L"], corners["R"], sec, mat)
    return mat


def _rollbar_section(frame: Frame, v: dict[str, float]) -> tuple[str, str]:
    """(section key, material key) of the roll bar's tube (as build_rollbar makes them)."""
    if v["rollbar_square"] == 1:
        return frame.section(*rect_tube(v["rollbar_b"], v["rollbar_b"], v["rollbar_t"])), ROLLBAR_MATERIALS[1.0]
    return frame.section(*round_tube(v["rollbar_d"], v["rollbar_t"])), ROLLBAR_MATERIALS[0.0]


def _seat_top(lay: Layout) -> float:
    return max(lay.top_of(p["id"]) for p in lay.parts if p["kind"] == "seat")


def side_heights(v: dict[str, float]) -> list[float]:
    """The side bars' heights above the seats' cushion top (side_bar_count of them, side_bar_low to side_bar_high)."""
    n = int(round(v["side_bar_count"]))
    lo, hi = v["side_bar_low"], v["side_bar_high"]
    return [hi] if n == 1 else [lo + (hi - lo) * k / (n - 1) for k in range(n)]


def _outrigger(frame: Frame, mid: str, root: str, xyz, section: str) -> str:
    """A member across from a rail node out to xyz (none when xyz is the node): its outer node <root>O."""
    if float(np.linalg.norm(np.asarray(frame.nodes[root]) - np.asarray(xyz, float))) <= frame.merge_mm:
        return root
    tip = frame.node(f"{root}O", xyz)
    frame.member(mid, root, tip, section)
    return tip


def _column(frame: Frame, name: str, node_prefix: str, foot: str, x: float, y: float, zs: list[float], section: str,
            material: str) -> list[str]:
    """Straight up from `foot` through the heights zs (ascending): members <name> (one piece) or <name>_<k>, nodes
    <node_prefix><k>; returns the nodes at zs."""
    out, below = [], foot
    for k, z in enumerate(zs, 1):
        n = frame.node(f"{node_prefix}{k}", [x, y, z])
        frame.member(name if len(zs) == 1 else f"{name}_{k}", below, n, section, material)
        out.append(n)
        below = n
    return out


def build_rops_extra(frame: Frame, lay: Layout, v: dict[str, float], foot_section: str) -> dict:
    """The roll bar's rear stays and front posts (rollbar_stays, rollbar_front_posts), on the roll bar build_rollbar
    placed. Stays: rops_stay_<L|R> from the corner <side>HT to the rail node <side>E at the frame's rear end. Front
    posts: an outrigger rops_front_foot_<side> from the rail node <side>S1 out to rollbar_width / 2, the post up to
    the roll bar's top height (rops_front_post_<side>, in pieces at the side bars' heights when side_bars = 1), a
    top rail rops_roof_<side> back to the corner and rops_front_top across. The members start with rops_, not
    rollbar_: the K0 portal (frame_inputs.portal_frame_inputs) and the gaps (rops_inputs) read the hoop alone.
    Returns {side: [the front post's nodes at the side bars' heights]} for build_side_guard ({} without front posts)."""
    s1, e = lay.station_x("S1"), lay.station_x("E")
    half, rail_y, rail_z = v["rollbar_width"] / 2.0, v["rail_spacing"] / 2.0, v["rail_height"]
    seat_top = _seat_top(lay)
    z_top = seat_top + v["rollbar_height"]
    h = ROLLBAR_STATION
    out: dict[str, list[str]] = {}
    sec, mat = _rollbar_section(frame, v)
    if v["rollbar_stays"] == 1:
        stay = frame.section(*round_tube(v["rollbar_stay_d"], v["rollbar_stay_t"]))
        if 2.0 * v["rollbar_stay_t"] >= v["rollbar_stay_d"]:
            raise DesignGenError("rollbar_stay_t must be less than half of rollbar_stay_d")
        for side in "LR":
            frame.member(f"rops_stay_{side}", f"{side}{h}T", f"{side}E", stay, mat)
    if v["rollbar_front_posts"] == 1:
        tops = {}
        for side, sy in (("L", 1.0), ("R", -1.0)):
            root = f"{side}S1"
            tip = _outrigger(frame, f"rops_front_foot_{side}", root, [s1, sy * half, rail_z], foot_section)
            zs = sorted({seat_top + z for z in side_heights(v)} if v["side_bars"] == 1 else set())
            zs = [z for z in zs if z < z_top - MIN_STATION_GAP_MM / 2.0] + [z_top]
            nodes = _column(frame, f"rops_front_post_{side}", f"{side}S1V", tip, s1, sy * half, zs, sec, mat)
            tops[side] = nodes[-1]
            out[side] = nodes[:-1]
            frame.member(f"rops_roof_{side}", tops[side], f"{side}{h}T", sec, mat)
        frame.member("rops_front_top", tops["L"], tops["R"], sec, mat)
    return out


def build_side_guard(frame: Frame, lay: Layout, v: dict[str, float], foot_section: str, front: dict) -> dict:
    """The side frame each side (side_bars): an outrigger side_foot_<side><S1|S2> from the rail node at the seat
    cross members' stations out to rollbar_width / 2, a post up to the highest bar (side_post_<side><S1|S2>, in
    pieces at the bars' heights), and the bars side_bar_<side><k> along x between the posts (round tube side_bar_d x
    side_bar_t, STKM13A). With front posts (build_rops_extra) the post at S1 is the front post. Returns {"heights"
    (above the seats' top), "x": (S2, S1), "y": half width, "d", "nodes": {side: [node ids]}, "members": {side: [ids]}}."""
    s1, s2 = lay.station_x("S1"), lay.station_x("S2")
    half, rail_y, rail_z = v["rollbar_width"] / 2.0, v["rail_spacing"] / 2.0, v["rail_height"]
    if half < rail_y:
        raise DesignGenError("side_bars need rollbar_width at least rail_spacing (the side frame stands out from the rails)")
    d, t = v["side_bar_d"], v["side_bar_t"]
    if 2.0 * t >= d:
        raise DesignGenError("side_bar_t must be less than half of side_bar_d")
    seat_top = _seat_top(lay)
    heights = side_heights(v)
    zs = [seat_top + z for z in heights]
    sec = frame.section(*round_tube(d, t))
    before = {m["id"] for m in frame.members}
    nodes: dict[str, list[str]] = {}
    for side, sy in (("L", 1.0), ("R", -1.0)):
        cols = {}
        for st, x in (("S2", s2), ("S1", s1)):
            if st == "S1" and front.get(side):
                cols[st] = [n for n in front[side] if any(abs(frame.nodes[n][2] - z) < 1e-6 for z in zs)]
                continue
            tip = _outrigger(frame, f"side_foot_{side}{st}", f"{side}{st}", [x, sy * half, rail_z], foot_section)
            cols[st] = _column(frame, f"side_post_{side}{st}", f"{side}{st}V", tip, x, sy * half, zs, sec, "stkm13a")
        if len(cols["S1"]) != len(zs):
            raise DesignGenError("the front posts do not meet the side bars' heights")
        for k, (a, b) in enumerate(zip(cols["S2"], cols["S1"]), 1):
            frame.member(f"side_bar_{side}{k}", a, b, sec, "stkm13a")
        nodes[side] = cols["S2"] + cols["S1"]
    members = {side: [m["id"] for m in frame.members if m["id"] not in before and m["id"].startswith("side_")
                      and m["id"][len("side_"):].split("_", 1)[-1][:1] == side] for side in "LR"}
    return {"heights": heights, "x": (s2, s1), "y": half, "d": d, "nodes": nodes, "members": members, "z": zs}


def add_options(design: Record, v: dict[str, float], guard: dict | None, rho: float) -> None:
    """The parts of the options, on the finished design (after the roll bar part): the side nets,
    the fenders, the pedal stands, then the part mounts (kit/attach.py). The materials they name are added."""
    from hiraku.designgen.kit import attach

    mats = json.loads(MATERIALS.read_text(encoding="utf-8"))
    used = set()
    if guard is not None:
        for side in "LR":
            # the side frame's members carry the label part = side_guard (no part record: the tubes are members,
            # their mass is the members')
            for m in design["members"]:
                if m["id"] in guard["members"][side]:
                    m["part"] = "side_guard"
            if v["side_net"] == 1:
                x0, x1 = guard["x"][0] + guard["d"] / 2.0, guard["x"][1] - guard["d"] / 2.0
                z0, z1 = guard["z"][0] + guard["d"] / 2.0, guard["z"][-1] - guard["d"] / 2.0
                y = guard["y"] if side == "L" else -guard["y"]
                w = SIDE_NET_WIRE_MM
                areal = 2.0 * math.pi * w * w / 4.0 / SIDE_NET_PITCH_MM * float(mats["ss400"]["density_kg_m3"]) * 1e-9
                design["parts"].append({
                    "id": f"side_net_{side}", "name": f"{'左' if side == 'L' else '右'}の横の網 (棒と柱のあいだ)",
                    "kind": "side_net", "box": {"center_mm": [(x0 + x1) / 2.0, y, (z0 + z1) / 2.0],
                                                 "size_mm": [x1 - x0, w, z1 - z0]},
                    "mass_kg": float(areal * (x1 - x0) * (z1 - z0)), "carried_by": list(guard["nodes"][side]),
                    "material": "ss400", "finish": "galvanized"})
                used.add("ss400")
    if v["fenders"] == 1:
        if v["fender_width"] < v["wheel_width"]:
            raise DesignGenError(f"fender_width ({v['fender_width']:g} mm) must be at least the tyre's ({v['wheel_width']:g} mm)")
        for s in design["supports"]:
            design["parts"].extend(fender_parts(s, v, float(HDPE["density_kg_m3"])))
        design["materials"]["hdpe"] = dict(HDPE)
    if v["pedal_mount"]:
        target = "floor_front" if v["pedal_mount"] == 1 else "bulkhead"
        if target == "floor_front" and v["mount_floor"] != 1:
            raise DesignGenError("pedal_mount = 1 stands the pedals on the footwell floor, which is held only with "
                                 "mount_floor = 1")
        try:
            attach.pedal_stands(design, [p["id"] for p in design["parts"] if p["kind"] in ("accelerator_pedal", "brake_pedal")],
                                target, rho)
        except attach.AttachError as e:
            raise DesignGenError(str(e)) from None
        used.add("ss400")
    ids = [pid for k, group in MOUNT_GROUPS.items() if v[k] == 1 for pid in group]
    if ids:
        attach.mount_parts(design, ids, rho, always=ids)
        used.add("ss400")
    for k in sorted(used):
        design["materials"].setdefault(k, mats[k])
    _check_options_clear(design)


OPTION_MEMBERS = ("rops_", "side_")  # the options' members
OPTION_PARTS = ("side_net_", "fender_")  # the options' parts (the mounts and stands check themselves: kit/attach)


def _check_options_clear(design: Record) -> None:
    """The options' members and parts reach into nothing further than K4 allows (k4_geometry.model.allowance);
    else DesignGenError naming the first clash."""
    from hiraku.solvers.k4_geometry import model as k4

    mine = [f"member:{m['id']}" for m in design["members"] if m["id"].startswith(OPTION_MEMBERS)]
    mine += [f"part:{p['id']}" for p in design["parts"] if p["id"].startswith(OPTION_PARTS)]
    if not mine:
        return
    bs = k4.bodies(design)
    for k in mine:
        b = bs[k]
        r = float(np.linalg.norm(b.obb.half))
        for o, ob in bs.items():
            if o == k or np.linalg.norm(ob.obb.center - b.obb.center) > r + float(np.linalg.norm(ob.obb.half)) + 1.0:
                continue
            ov = k4.shape_overlap(b, ob)
            if ov > k4.allowance(b, ob, design):
                raise DesignGenError(f"{k.split(':', 1)[1]} goes {ov:.1f} mm into {o.split(':', 1)[1]}; "
                                     + ("make rollbar_width wider (the side frame and the front posts stand at "
                                        "rollbar_width / 2)" if k.startswith("member:") else "change the option's sizes"))


def fender_parts(support: Record, v: dict[str, float], density: float) -> list[Record]:
    """A fender over a wheel: FENDER_PIECES flat pieces (kind fender, no carried_by: on the upright, with the wheel)
    from fender_front_deg ahead of the top to fender_rear_deg behind, each tangent at its middle to the circle
    fender_gap over the tyre (radius wheel_radius + fender_gap), fender_width across, centred on the wheel."""
    x, y, z = (float(c) for c in support["position_mm"])
    R = float(support["radius_mm"]) + v["fender_gap"]
    a0, a1 = -math.radians(v["fender_front_deg"]), math.radians(v["fender_rear_deg"])
    step = (a1 - a0) / FENDER_PIECES
    out = []
    for k in range(FENDER_PIECES):
        th = a0 + (k + 0.5) * step  # from the top, positive toward the rear
        r = R + FENDER_T_MM / 2.0
        length = 2.0 * R * math.tan(step / 2.0)
        c = [x - r * math.sin(th), y, z + r * math.cos(th)]
        out.append({"id": f"fender_{support['id']}_{k + 1}", "name": f"{support['id']} の泥よけ ({k + 1})", "kind": "fender",
                    "box": {"center_mm": [float(q) for q in c], "size_mm": [length, v["fender_width"], FENDER_T_MM],
                            "rpy_deg": [0.0, float(-math.degrees(th)), 0.0]},
                    "mass_kg": float(density * 1e-9 * length * v["fender_width"] * FENDER_T_MM),
                    "material": "hdpe", "finish": "molded"})
    return out

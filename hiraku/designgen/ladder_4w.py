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
https://www.airc.aist.go.jp/dhrt/91-92/data/list.html on 2026-11-03, as Q33's basis in
data/requirements/offroad-4w.yaml says). The README's table of values not checked still
lists 76.6 kg as not checked (the AIST statistics could not be read); not re-checked
2026-11-05 (no web access in that session).
"""

from __future__ import annotations

import functools
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from hiraku.designgen.kit import arms, body, drivetrain, mounts, safety, steering, systems
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
    # where an outside or assumed value comes from (contract: design.variables[...]["basis"]); None for the
    # variables the design chooses
    basis: str | None = None

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
    # C1 notes (rail_t, tower_t, seat_x, battery_x): K2's crash_beam (claude/k2-crash-contact 9bffe19c), 40 km/h,
    # tyre 60 N/mm, on version 7 of the real run 20261105-160725, the least room to the occupant boxes
    # (C1-40kmh >= 0), 2026-11-06; tool results, not checked against a physical test
    "rail_t": Var("mm", 2.3, 1.6, 4.5, "rail tube wall. C1 (front crash): a thicker rail behind the towers bends less, so "
                  "the towers and posts fold less into the feet (v7 of a run: 2.3 -> 4.5 mm took the least room to "
                  "the feet from -27 to -13 mm)"),
    "cross_b": Var("mm", 40.0, 30.0, 80.0, "cross member tube width"),
    "cross_h": Var("mm", 40.0, 30.0, 120.0, "cross member tube height"),
    "cross_t": Var("mm", 2.3, 1.6, 4.5, "cross member tube wall"),
    # the suspension's mounts on the frame (kit/mounts.py) and the dash: square tubes of STKR400; the sizes
    # are design choices (assumptions, no source)
    "tower_b": Var("mm", 30.0, 25.0, 60.0, "square tube width of the suspension towers (posts, top beam, the arm "
                   "out to the damper's top), the dash posts and the dash cross member (narrower than the rail, "
                   "so that a post's foot sits inside the rail's top wall)"),
    "tower_t": Var("mm", 2.3, 1.6, 4.5, "wall of the tower and dash tubes. C1 (front crash): thicker towers carry more of "
                   "the front's push back into the feet and can make it worse (v7 of a run: 2.3 -> 3.2 mm took the "
                   "least room to the feet from -27 to -178 mm); do not thicken them for C1"),
    "lower_b": Var("mm", 40.0, 30.0, 60.0, "square tube width of the legs down to the lower arms' pivots and the "
                   "lower cross members joining them (at the default 40 x 2.3 the front legs are R3's worst welds, "
                   "damage 0.29; 50 x 3.2 gives 0.13)"),
    "lower_t": Var("mm", 2.3, 1.6, 4.5, "wall of the leg and lower cross member tubes"),
    "lower_cross": Var("1", 1.0, 0.0, 1.0, "1: lower cross members join the left and right lower arms' pivots (at "
                       "each axle where the motor and the reducer are not in the way); 0: none, each pivot on its leg "
                       "alone"),
    # people and parts
    "seat_x": Var("mm", -1045.0, -1500.0, -500.0, "seat centre (x); the seat cross members are 275 mm ahead and behind. "
                  "C1 (front crash): moving the seats back (with battery_x, so the packs still fit between the seat "
                  "cross members) adds room to the feet and helps most (v7 of a run: seat_x and battery_x -1045 -> "
                  "-1170 mm took the least room from -27 to +19 mm, +40 mm with rail_t 4.5)"),
    "seat_y": Var("mm", 300.0, 220.0, 400.0, "seat centre off the centre line, each side"),
    "steering_x": Var("mm", -570.0, -900.0, 0.0, "steering position (x): the steering wheel's centre is "
                      "150 mm behind it (where the back of the earlier steering box was)"),
    "battery_x": Var("mm", -1045.0, -1700.0, -300.0, "battery centre (x); it hangs from the cross members either side "
                     "and must fit between them (seat_x moves the seat cross members). C1 (front crash): move it "
                     "with seat_x when the seats go back (seat_x alone is refused when the packs no longer fit)"),
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
    # powertrain and battery: a 51.2 V LiFePO4 pack (16 cells at 3.2 V: the cell's nominal voltage 3.2 V is
    # EVE LF50K product specification LF50K-73103 Ver. D, section 3, table row 2,
    # https://www.rcscomponents.kiev.ua/datasheets/LF50K32V50AhProduct%20Specification.pdf, checked 2026-11-03
    # (README, the table of values checked); 16 in series is HIRAKU's choice),
    # a 10 kW peak motor driving the rear axle through a reduction. The motor's power, torque, speed, the
    # efficiencies, the resistance and the heat capacities below are assumptions, HIRAKU's choice, no source
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
    "battery_voltage": Var("V", 51.2, meaning="16 x 3.2 V (EVE LF50K cell nominal voltage, checked 2026-11-03; see above)"),
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
    # temperature limits of the parts (two of three checked, as the README's table of values not checked records):
    # source: EVE LF50K product specification, cells up to 55 degC (section 3),
    # https://www.rcscomponents.kiev.ua/datasheets/LF50K32V50AhProduct%20Specification.pdf; checked (README, the
    # table of values not checked, the temperature row; the row gives no date)
    "battery_max_temperature": Var("degC", 55.0, meaning="cell limit, EVE LF50K product specification section 3 "
                                   "(checked)"),
    # source: Curtis 1234/36/38 manual, Appendix D Table D-1: output cut back from 85 degC at the heatsink,
    # https://www.bintelli.com/wp-content/uploads/2023/06/Curtis-Controller-1236-Manual.pdf; checked (README, the
    # table of values not checked, the temperature row; no date)
    "controller_max_temperature": Var("degC", 85.0, meaning="cutback temperature at the heatsink, Curtis 1234/36/38 "
                                      "manual Appendix D Table D-1 (checked)"),
    # source: thermal class F (155 degC) of IEC 60085 for the winding insulation; IEC 60085 is not readable here
    # (only a Wikipedia page was read), not checked. Taking class F for this motor is an assumption
    "motor_max_temperature": Var("degC", 155.0, meaning="insulation class F, IEC 60085 (not checked)"),
    # aerodynamics (assumptions; the drag coefficient is set high because the seats are open)
    "drag_coefficient": Var("1", 0.8, meaning="assumption"),
    "frontal_area": Var("m^2", 1.6, meaning="assumption"),
    "side_force_coefficient": Var("1", 1.0, meaning="assumption"),
    "side_area": Var("m^2", 2.4, meaning="assumption"),
    "centre_of_pressure_height": Var("mm", 800.0, meaning="assumption"),
    # source: Manual of the ICAO Standard Atmosphere (ICAO Doc 7488; the README also cites ISO 2533), sea level;
    # not checked in this repository (neither text was opened)
    "air_density": Var("kg/m^3", 1.225, meaning="ICAO standard atmosphere, sea level (not checked)"),
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
    # fillet-welded tube joints and the slope 22 beyond the knee are assumptions. The README (the table of values
    # not checked, the fatigue row) records that for variable amplitude loads summed by Miner's rule the IIW takes
    # 2 m1 - 1 (= 5) beyond the knee (IIW-1823-07, 2008, 4.3.1); 22 is the constant amplitude slope
    "fatigue_fat_class": Var("MPa", 71.0, meaning="assumption (see above)"),
    "fatigue_slope": Var("1", 3.0, meaning="IIW form, not checked"),
    "fatigue_knee_cycles": Var("1", 1e7, meaning="IIW form, not checked"),
    "fatigue_slope_after_knee_constant_amplitude": Var("1", 22.0, meaning="assumption"),
    # 60 N/mm: the centre of 45-90 N/mm read from the three sources in TYRE_STIFFNESS_BASIS (a judgement, not a
    # value in them); it replaces 80 N/mm (a retailer's page) and before that an unsourced 150 N/mm
    "tyre_stiffness": Var("N/mm", 60.0, meaning="vertical stiffness of one tyre (see basis)"),
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
    "gusset_ends": Var("1", 0.0, 0.0, 1.0, "1: gussets (gusset_leg, gusset_thickness) also at the frame's two end "
                       "corners, where a rail ends on its end cross member (an L joint: the inside corner only); 0: "
                       "the T joints only. Written into design.variables only when it is 1"),
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
TYRE_STIFFNESS_BASIS = (
    "60 N/mm, range 45 to 90 N/mm: HIRAKU's judgement from three sources on ATV-class tyres (not measurements of "
    "this vehicle's tyre), read 2026-11-06 via web.archive.org copies: (1) D. R. Krueger, Investigation of Lateral "
    "Performance of an ATV Tire on Natural, Deformable Surfaces, MS thesis, Auburn University, 2007, Appendix B, "
    "Fig. 6.4: Dunlop KT 821 22x8-10 measured statically at 0.69 / 1.03 / 1.38 bar, about 57 / 62 / 74 N/mm read "
    "off the figure; (2) BKT technical data sheets, Sierra Max Pro AT 25x8 R12 and Sierra Max AT 25x10 R12: from "
    "the rated load and the static loaded radius HIRAKU computes the mean stiffness from 0 to the rated load, "
    "54.3 and 71.7 N/mm (the sheets give no pressure for the loaded radius; such a secant is below the tangent); "
    "(3) G. N. Wilson, Towards Speed Selection for High-Speed Operation of Autonomous Ground Vehicles on Rough "
    "Off-Road Terrains, PhD thesis, University of Calgary, 2018, Appendix B.3.5.1: ATV 16x8-7 at about 0.69 bar, "
    "36 to 44 N/mm static and 0.1-10 Hz dynamic (dynamic / static about 1.0-1.2)")
TYRE_STIFFNESS_RANGE_N_MM = (45.0, 90.0)  # the ends of the range in TYRE_STIFFNESS_BASIS
# Where the outside and assumed values come from (design.variables[...]["basis"]), copied from their meaning and
# the comments above; the variables the design chooses and the requirements (Q50, Q40, seats, cargo_height) have none.
_BASIS = {
    **dict.fromkeys(
        ("drivetrain_efficiency", "motor_efficiency", "controller_efficiency", "regen_efficiency", "aux_power",
         "battery_resistance", "mass_factor", "rolling_resistance", "battery_heat_capacity",
         "battery_thermal_resistance", "motor_heat_capacity", "motor_thermal_resistance", "controller_heat_capacity",
         "controller_thermal_resistance", "drag_coefficient", "frontal_area", "side_force_coefficient", "side_area",
         "centre_of_pressure_height", "front_cornering_stiffness", "rear_cornering_stiffness", "bumper_stiffness",
         "fatigue_fat_class", "fatigue_slope_after_knee_constant_amplitude", "tyre_damping_ratio",
         "tyre_slip_stiffness_coeff", "bump_stop_factor", "driveshaft_diameter", "driveshaft_wall"),
        "assumption"),
    "occupant_mass": "Q33: AIST anthropometric database 1991-92 item N1 Weight, young adult male 95th percentile "
                     "(the README lists it as not checked)",
    "battery_voltage": "16 x 3.2 V: cell nominal voltage, EVE LF50K product specification LF50K-73103 Ver. D, section "
                       "3 (checked 2026-11-03); 16 in series is HIRAKU's choice",
    "battery_max_temperature": "cell limit, EVE LF50K product specification, section 3 (checked)",
    "controller_max_temperature": "Curtis 1234/36/38 manual, Appendix D Table D-1 (checked)",
    "motor_max_temperature": "assumption: insulation class F of IEC 60085 (not checked)",
    "air_density": "ICAO standard atmosphere (ICAO Doc 7488), sea level (not checked)",
    "fatigue_slope": "IIW form: A. F. Hobbacher, Recommendations for Fatigue Design of Welded Joints and Components, "
                     "2nd ed., Springer 2016 (not checked)",
    "fatigue_knee_cycles": "IIW form: A. F. Hobbacher, Recommendations for Fatigue Design of Welded Joints and "
                           "Components, 2nd ed., Springer 2016 (not checked)",
    "tyre_stiffness": TYRE_STIFFNESS_BASIS,
}
VARIABLES.update({k: replace(VARIABLES[k], basis=b) for k, b in _BASIS.items()})
DERIVED_BASIS = {"tyre_relaxation_length": "assumption"}  # basis of the DERIVED variables that have one
SWITCHES = ("cross_members_through", "gusset_ends", "rollbar", "rollbar_square", "driveshafts", "lamps", "lower_cross")  # 0 or 1
ROLLBAR_VARS = ("rollbar", "rollbar_height", "rollbar_width", "rollbar_offset", "rollbar_square", "rollbar_d", "rollbar_b",
                "rollbar_t")
ROLLBAR_MATERIALS = {0.0: "stkm13a", 1.0: "stkr400"}  # by rollbar_square (assumption)
# Optional variables, written into design.variables only when one of their group differs from its default.
OPTIONAL = {"gusset_leg": ("gusset_leg", "gusset_thickness"), "gusset_ends": ("gusset_ends",),
            "cross_members_through": ("cross_members_through",)}
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
        if v.basis:
            r["basis"] = v.basis
        out.append(r)
    for name, (unit, meaning) in DERIVED.items():
        out.append({"name": name, "unit": unit, "revisable": False, "derived": True, "meaning": meaning,
                    **({"basis": DERIVED_BASIS[name]} if name in DERIVED_BASIS else {})})
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
        if name in PLATE_T_VARS and val not in PLATE_KG_M2:
            raise DesignGenError(f"{name} is a standard sheet thickness: one of "
                                 f"{', '.join(f'{t:g}' for t in PLATE_KG_M2)} mm, got {val:g}")
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
# The plate parts (the floors, the deck, the bulkhead): each box is the plate itself, its thin side the sheet's
# thickness (load_path takes a part that thin as a plate of its box's thickness and its part.material). They are
# steel floor plate (checkered) of SS400 grade. Source, read on 2026-11-04: Nippon Steel's catalogue A101
# (床用鋼板・たてじま鋼板, A101_02_202406f, https://www.nipponsteel.com/product/catalog_download/pdf/A101.pdf):
# its structural floor plate NFP-400 meets the chemical and mechanical requirements of JIS G 3101 SS400 (and its
# dimensions and shape but for the checkers); its standard thicknesses are 2.3, 3.2, 4.5, 6.0, ... mm, with a unit
# mass of 19.73 kg/m2 at 2.3 mm and 26.79 kg/m2 at 3.2 mm (the checkers included). Which standard thickness each
# part takes is HIRAKU's choice (assumption, not sized): 3.2 mm where people stand or cargo lies, the thinnest
# standard 2.3 mm for the bulkhead. A plate's mass is its area x the catalogue's unit mass at its thickness
# (PLATE_KG_M2, plate_mass; the checkers included). Before, it was area x thickness x the material's density, without
# the checkers (7 % less at 3.2 mm, 9 % less at 2.3 mm). The catalogue's values are not in the README's tables yet
# and were not re-read on 2026-11-05 (no web access in that session).
PLATE_STEEL = "ss400"
PLATE_FINISH = "painted"  # assumption
FLOOR_PLATE_T_MM = 3.2  # the footwell floor, the floor under the seats and the deck (one value: _driver_v1)
BULKHEAD_PLATE_T_MM = 2.3
PLATE_T_MM = {"floor_front": FLOOR_PLATE_T_MM, "floor": FLOOR_PLATE_T_MM, "deck": FLOOR_PLATE_T_MM,
              "bulkhead": BULKHEAD_PLATE_T_MM}  # the defaults of the thickness variables (PLATE_T_VARS)
# The catalogue's unit mass (kg/m2, the checkers included) of each NFP-400 standard thickness read from A101 (see
# above). Only these thicknesses may be chosen: 4.5, 6.0 mm and the others are standard sizes too, but their unit
# masses were not read here.
PLATE_KG_M2 = {2.3: 19.73, 3.2: 26.79}
# The plate thickness variables and the plate parts each sets (floor_t: both floors, the seats and the feet on one
# level, as _driver_v1 takes it)
PLATE_T_VARS = {"floor_t": ("floor_front", "floor"), "deck_t": ("deck",), "bulkhead_t": ("bulkhead",)}
# The front body (outer panel and bumper) is a box around them, not one plate: its mass stays the first v1's
# (assumption) and it is taken as steel of PLATE_STEEL (assumption, not checked)
FRONT_BODY_KG = 15.0
DECK_WIDTH_MM = 880.0  # the deck at most this wide (the first v1's deck, assumption)
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
# steering_column_angle's range (its meaning below says why; 50 deg's top came down to 49 when the floors became
# 3.2 mm sheets instead of 20 mm boxes, the driver and the column 16.8 mm lower)
COLUMN_ANGLE_RANGE_DEG = (20.0, 49.0)


def _driver_v1() -> dict[str, float]:
    """The driver's place in the default design, from the AIST body sizes (k4_geometry.people) and the other
    variables' defaults, as the generator placed it before these were variables: the large driver (m95) in
    the seat with the heels on the footwell floor and the knee at people.KNEE_ANGLE_DEG, the accelerator
    under the ball of the right foot (pedal_x), the steering wheel's rim KNEE_GAP_MM above the knee's top
    with the column at kit/steering.COLUMN_ANGLE_DEG (steering_wheel_height: its centre above the seat's
    cushion top). Also the values the ranges come from (see the variables' meanings)."""
    from hiraku.solvers.k4_geometry import people

    d = {k: x.default for k, x in VARIABLES.items()}
    floor_top = d["rail_height"] + d["rail_h"] / 2.0 + ON_RAILS_MM + FLOOR_PLATE_T_MM  # the floors' top
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
# The AIST body sizes quoted below (I3 637.5, I13 399.1, I11 58.0 mm, ...) are k4_geometry.people's values (AIST
# anthropometric database 1991-92); the README's table of values not checked lists the body sizes as not checked.
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
                                 "within 20 deg of upright before the chest) to 49 deg (at the default steering_x a column "
                                 "steeper than about 49.8 deg puts the dash at or below the tops of the front towers' "
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
SEAT_SLIDE_MAX_MM = 300.0  # seat_slide's upper end (assumption; no source for a vehicle's seat travel was read)
_DRIVER_VARS["seat_slide"] = Var(
    "mm", 0.0, 0.0, SEAT_SLIDE_MAX_MM,
    "the driver's seat's fore-and-aft travel (the whole length of its slide); seat_x is the travel's middle. The "
    "seat's box stays at the middle (its mass and the interference check, H1, are there); K4 seats the large and "
    "the small driver each where their own H3 margins are best within the travel (k4_geometry.people.poses), and "
    "the driver's body parts are where the large driver sits. The driver's seat belt stands behind the seat back "
    "at the rearmost end of the travel. The passenger's seat does not slide. 0: a fixed seat (v1). Range: 0 to "
    f"{SEAT_SLIDE_MAX_MM:g} mm (assumption, not verified: no source for a seat's travel was read). Written into "
    "design.variables only when it is not 0")
VARIABLES = {k: x for name, var in VARIABLES.items()
             for k, x in ([(name, var)] + (list(_DRIVER_VARS.items()) if name == "battery_x" else []))}
OPTIONAL["bulkhead_dx"] = ("bulkhead_dx",)
OPTIONAL["seat_slide"] = ("seat_slide",)

# Options a failed requirement may call for (off by default, so that the defaults still build v1). Each is a
# variable (or a few) the designer may revise, with CHOICES below as short ways to set them.
# - part mounts (FN-15, R8 free_parts): a spacer or bracket (kit/attach.py) from each part of a group to the
#   nearest frame member, sized from the gap; a part none can reach is held through what it touches.
MOUNT_GROUPS = {
    "mount_floor": ("floor", "floor_front", "deck", "front_body"),
    "mount_electrics": ("battery", "battery2", "controller", "aux", "main_switch"),
    "mount_seats": ("seat_R", "seat_L", "seat_belt_R", "seat_belt_L"),
    "mount_drive": ("reducer", "motor"),
}
_MOUNT_WORDS = {"mount_floor": "the floor panels, the deck and the front body (each on the rails or cross members "
                               "under it)",
                "mount_electrics": "the battery (each pack), the controller, the wiring box and the main switch (to the cross "
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
    "1", 0.0, 0.0, 3.0, "0: the pedals are placed without a mount; 1: a stand under each pedal from the footwell "
    "floor up to the pedal's lowest edge (the floor must be held: mount_floor = 1); 2: a bracket from the front "
    "bulkhead's rear face back to each pedal's front edge (a pedal box); 3: a beam (frame member pedal_beam, the "
    "tower tube) out from the front tower's rear post on the pedals' side, inboard at the height of the pedals' "
    "front edges to PEDAL_BEAM_PAST_MM past the innermost pedal's stand, and a stand from its rear face back to each "
    "pedal's front edge (build_pedal_beam). The stands follow pedal_x (kit/attach.pedal_stands; SS400, painted)")
PEDAL_BEAM_PAST_MM = 0.0  # the pedal beam ends at the inner side of the innermost stand (assumption)
# - side guard (FN-05 push at point R, FN-07): a side frame each side outboard of the seats
from hiraku.solvers.k4_geometry import people as _people  # noqa: E402

_BIG = _people.SIZES[_people.BIG]
SIDE_NET_WIRE_MM = 3.0  # the side net's wire diameter (assumption; side_net_wire_d's default)
SIDE_NET_PITCH_MM = 50.0  # its mesh pitch both ways (assumption; side_net_pitch's default)
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
    "side_net": Var("1", 0.0, 0.0, 1.0, "1: a net each side (welded wire mesh, SS400 galvanized: SIDE_NET_WIRE_MM "
                    "wires at SIDE_NET_PITCH_MM, an assumption). With side_bars = 1 (side_bar_count >= 2): between the "
                    "lowest and highest bar and the side posts. With side_bars = 0: in the line of the roll bar's posts, "
                    "held by quick-release clips (parts) on the roll bar's post and, in front, on the front post "
                    "(rollbar_front_posts = 1) or on a short outrigger from the rail (a triangle; build_side_net), "
                    "covering point R; needs rollbar = 1; 0: none"),
    "side_net_wire_d": Var("mm", SIDE_NET_WIRE_MM, 1.0, 10.0, "the side nets' wire diameter (the default SIDE_NET_WIRE_MM is an "
                           "assumption: no catalogue or standard value was checked)"),
    "side_net_pitch": Var("mm", SIDE_NET_PITCH_MM, 10.0, 200.0, "the side nets' mesh pitch, both ways (the default SIDE_NET_PITCH_MM "
                          "is an assumption: not checked)"),
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
                            "(Q87: 細目告示 (2023.9.22 版) 第 178 条 第 2 項 第 1 号, read by the intake department; not "
                            "checked by the analysis department; a public-road rule, borrowed)"),
    "fender_rear_deg": Var("deg", 50.0, 0.0, 120.0, "how far it reaches behind the top (FN-19's 50 deg, Q88, the same "
                           "article, as above)"),
    "fender_width": Var("mm", 240.0, 120.0, 400.0, "fender width across the wheel (at least the tyre's)"),
    "fender_gap": Var("mm", 40.0, 10.0, 150.0, "the fender's inner face this far over the tyre (assumption)"),
})
INTEGERS = ("side_bar_count", "pedal_mount")
ZERO_OR_AT_LEAST = {"rollbar_foot_b": 30.0, "rollbar_foot_h": 30.0, "rollbar_foot_t": 1.6}
# part kinds of the frame itself, which a joint's gussets may meet (kit/frame.Frame.gussets avoids the others)
GUSSET_NEIGHBOURS = ("frame", "gusset", "bracket", "rollbar")
SWITCHES = SWITCHES + tuple(MOUNT_GROUPS) + ("side_bars", "side_net", "rollbar_stays", "rollbar_front_posts", "fenders")
VARIABLES.update(_OPTION_VARS)
OPTIONAL.update({
    "part_mounts": tuple(MOUNT_GROUPS),
    "pedal_mount": ("pedal_mount",),
    "side_bars": ("side_bars", "side_bar_count", "side_bar_low", "side_bar_high", "side_bar_d", "side_bar_t", "side_net",
                  "side_net_wire_d", "side_net_pitch"),
    "rollbar_foot": ("rollbar_foot_b", "rollbar_foot_h", "rollbar_foot_t"),
    "rollbar_stays": ("rollbar_stays", "rollbar_stay_d", "rollbar_stay_t"),
    "rollbar_front_posts": ("rollbar_front_posts",),
    "fenders": ("fenders", "fender_front_deg", "fender_rear_deg", "fender_width", "fender_gap"),
})
# - getting in and out (test H7, proposed: k4_geometry.entry): the side frame (side_bars) as a fixed guard round point R
#   only, and an opening ahead of it that opens (a net on quick-release clips, or a door on hinges with a latch); a
#   grab bar on the side frame's front post. The guard keeps point R behind fixed bars (a net a person fastens does
#   not count as the guard at point R: the program management's decision after CPSC's proposed 16 CFR 1422.5(c)(4),
#   as the intake department read it); the opening is where the person passes.
SIDE_OPENING_REACH_MM = 100.0  # the side frame's front post this far ahead of point R (HIRAKU's choice, no source)
DOOR_T_MM = 2.3  # the door's sheet (HIRAKU's choice, no source for a door's thickness): the thinnest NFP-400
#   thickness whose unit mass was read (PLATE_KG_M2)
# The door's hinges and latch take a clip's size and mass (SIDE_NET_CLIP_MM, SIDE_NET_CLIP_KG 0.05 kg): HIRAKU's
#   choice, no source for a hinge's or a latch's mass
# a JIS G 3444 standard size (data/sections/standard_tubes.json, Table 6; the README's table of values checked
# records that table checked 2026-11-03: its 128 rows counted; 27.2 x 2.3 is not among the sizes named as read)
GRAB_BAR_D_MM, GRAB_BAR_T_MM = 27.2, 2.3
# the hand's room between the grip and the post: ISO 2867:1994 Table 2 C as the intake department read it, not
# checked by the analysis department (see grab_bar_gap)
GRAB_BAR_GAP_MM = 75.0
_OPTION_VARS_ENTRY = {
    "side_opening": Var("1", 0.0, 0.0, 2.0,
                        "0: the side frame (side_bars) runs from S2 to S1 (v1's options); 1 or 2: its front post stands "
                        "side_bar_reach ahead of the most forward point R (load_path.point_r, the driver's spread by "
                        "seat_slide) on an outrigger from the rail, and the stretch from there to "
                        "SIDE_NET_FRONT_GAP_MM behind the front wheels is an opening: 1 a net in it on quick-release "
                        "clips (kind net_clip) on the front post and on a short outrigger at the front (a triangle, as "
                        "build_side_net), 2 a door in it (kind door, a DOOR_T_MM sheet of PLATE_STEEL) on two hinges "
                        "(door_hinge) on the front post with a latch (door_latch) on that outrigger. Needs side_bars = 1 "
                        "and not rollbar_front_posts (their post at S1 would stand in the opening)"),
    "side_bar_reach": Var("mm", SIDE_OPENING_REACH_MM, 40.0, 300.0,
                          "with side_opening: how far ahead of the most forward point R the side frame's front post "
                          "stands (its centre line); the default 100 mm is HIRAKU's choice (no source)"),
    "grab_bar": Var("1", 0.0, 0.0, 1.0,
                    "1: a grab bar each side on the side frame's front post (side_bars = 1): two stand-offs out from the "
                    "post at the lowest and the highest side bar, and the grip up between them, grab_bar_gap clear of "
                    "the post (members grab_<side>_1..3, round tube grab_bar_d x grab_bar_t, STKM13A); 0: none"),
    "grab_bar_d": Var("mm", GRAB_BAR_D_MM, 16.0, 38.0,
                      "the grab bar's outside diameter: the default 27.2 mm is a JIS G 3444 standard size (the list in "
                      "data/sections/standard_tubes.json), the nearest above the 25 mm ISO 2867:1980 7.3 recommends "
                      "for a grab rail; the range is that clause's 16 mm min and 38 mm max (requirement items Q49 and "
                      "Q89 as the intake department read them, 2026-11-04; not checked by the analysis department; an "
                      "older edition of a standard for earth-moving machinery, borrowed)"),
    "grab_bar_t": Var("mm", GRAB_BAR_T_MM, 1.6, 4.5, "the grab bar's tube wall (27.2 x 2.3: JIS G 3444 standard size)"),
    "grab_bar_gap": Var("mm", GRAB_BAR_GAP_MM, 50.0, 150.0,
                        "the clear room between the grip and the post's face: the default is the 75 mm hand clearance "
                        "of ISO 2867:1994 Table 2 C as the intake department read it (not a requirement item here; "
                        "borrowed, not checked by the analysis department)"),
}
_OPTION_VARS.update(_OPTION_VARS_ENTRY)
VARIABLES.update(_OPTION_VARS_ENTRY)
INTEGERS = INTEGERS + ("side_opening",)
SWITCHES = SWITCHES + ("grab_bar",)
OPTIONAL.update({"side_opening": ("side_opening", "side_bar_reach"),
                 "grab_bar": ("grab_bar", "grab_bar_d", "grab_bar_t", "grab_bar_gap")})
_PLATE_WORDS = {"floor_t": "the footwell floor and the floor under the seats (the seats, the people, the pedals and the "
                           "steering wheel stand on its top, so they move up or down with it)",
                "deck_t": "the deck (the cargo on it)", "bulkhead_t": "the front bulkhead"}
for _k, _pids in PLATE_T_VARS.items():
    _t0 = PLATE_T_MM[_pids[0]]
    VARIABLES[_k] = Var("mm", _t0, min(PLATE_KG_M2), max(PLATE_KG_M2),
                        f"the sheet thickness of {_PLATE_WORDS[_k]}: one of the NFP-400 standard thicknesses whose unit "
                        f"mass was read from Nippon Steel's catalogue A101 ({', '.join(f'{t:g}' for t in PLATE_KG_M2)} "
                        f"mm; PLATE_KG_M2). The default {_t0:g} mm is an assumption, not sized. Written into "
                        "design.variables only when it is not the default")
    OPTIONAL[_k] = (_k,)
# The seats' cushion depth (seat_depth): the seat box's length along x. The seat back (the box's rear face, where
# K4 and kit/body.py seat the people: k4_geometry.people.seated, body._seat_frame) stays at
# seat_x - SEAT_LENGTH_MM / 2 whatever the depth, so the people, the pedals, the steering wheel and the seat belts
# stay where they are; the cushion's front edge moves. The range is from the AIST body sizes read in
# k4_geometry.people (young adults, AIST anthropometric database 1991-92, read 2026-11-03):
# - at least the large person's trunk box (kit/body.occupant_parts: from the seat back to twice the hip's distance
#   ahead of it, J9 Buttock-trochanterion length, sitting, male 95th percentile 146.0 mm): 292.0 mm. Shorter, the
#   trunk's box reaches past the cushion's front edge;
# - at most the small person's buttock-knee length (J5 Buttock-knee length, sitting, female 5th percentile 511.0 mm).
#   Longer, the cushion's front edge is past the front of her knees.
# Rule HU-02 (data/knowledge/human.yaml) limits the depth to J6 Buttock-popliteal length, sitting, female 5th
# percentile 406.0 mm (its basis: https://www.airc.aist.go.jp/dhrt/91-92/data/gif/J6.gif, read 2026-11-03); J6 is
# not among the values in k4_geometry.people, so the range here does not use it. The seat's mass does not change with
# the depth (the 12 kg is an assumption, as before).
SEAT_DEPTH_RANGE_MM = (2.0 * _people.SIZES[_people.BIG]["buttock_trochanter"], _people.SIZES[_people.SMALL]["buttock_knee"])
VARIABLES["seat_depth"] = Var(
    "mm", SEAT_LENGTH_MM, *SEAT_DEPTH_RANGE_MM,
    "the seats' cushion depth (the seat box's length along x); the seat back stays at seat_x - "
    f"{SEAT_LENGTH_MM / 2.0:g} mm, the front edge moves (the people sit where they did). The default is the first "
    f"v1's {SEAT_LENGTH_MM:g} mm (assumption). Range: from {SEAT_DEPTH_RANGE_MM[0]:g} mm, the large person's trunk box "
    "(twice the AIST young adult male 95th percentile buttock-trochanterion length J9, 146.0 mm), to "
    f"{SEAT_DEPTH_RANGE_MM[1]:g} mm, the small person's buttock-knee length (AIST young adult female 5th percentile "
    "J5). Rule HU-02 asks for at most the female 5th percentile buttock-popliteal length J6 (406 mm). Written into "
    "design.variables only when it is not the default")
OPTIONAL["seat_depth"] = ("seat_depth",)
# The battery as two packs (battery_split = 2): the v1 pack's box cut in two across y, each half its width and
# mass, side by side under the floor between the seat cross members with BATTERY_SPLIT_GAP_MM between them (the
# two together BATTERY_SPLIT_GAP_MM wider than the one pack). Each hangs from the seat cross members on its side
# (Layout.mounts), as the one pack does from both sides. The two are wired in series (each 8 of the 16 cells: half
# the voltage, the whole capacity), the second pack to the first, the first to the main switch (kit/systems.links).
# A pack's own case, terminals and the wire between the packs are not added (no source for their mass).
BATTERY_SPLIT_GAP_MM = 20.0  # between the two packs (assumption: room for the wire and the hands)
BATTERY_RAIL_GAP_MM = 20.0  # the pack(s) together this much narrower than the clear width between the rails (as v1)
VARIABLES["battery_split"] = Var(
    "1", 1.0, 1.0, 2.0,
    "1: one battery pack (v1); 2: two packs, the v1 pack cut in two across y (each half the mass), side by side "
    f"{BATTERY_SPLIT_GAP_MM:g} mm apart under the floor between the seat cross members, each on the cross members "
    "on its side, wired in series (the second to the first). build() refuses when the packs do not fit between the "
    "rails or between the seat cross members. Written into design.variables only when it is 2")
OPTIONAL["battery_split"] = ("battery_split",)
# The suspension brackets' joint (part.joint, docs/contract.md 金具の継ぎ手; load case R9 サスペンション取付):
# the clevis plates' thickness, the pin's diameter and the fillet weld's leg, revisable by the suspension's
# designer. The number of pins is not a variable: one through each clevis (kit/mounts.PINS_PER_BRACKET). The
# brackets' mass follows from mount_plate_t (kit/mounts._bracket). Written into design.variables only when one
# of the three is not its default (so the default design is as before).
VARIABLES["mount_plate_t"] = Var(
    "mm", mounts.BRACKET_T_MM, 2.3, 9.0,
    "the suspension brackets' plate thickness (two plates per clevis): the default 3.2 mm is the brackets' plate as "
    "before (kit/mounts.BRACKET_T_MM, an assumption, not sized); the range is HIRAKU's for R9 to size it")
VARIABLES["mount_bolt_d"] = Var(
    "mm", mounts.BOLT_D_MM, 6.0, 20.0,
    "the suspension brackets' pin (bolt) diameter, grade 8.8: the default 10 mm is HIRAKU's assumption (not sized); "
    "the range is HIRAKU's for R9 to size it")
VARIABLES["mount_weld_leg"] = Var(
    "mm", mounts.WELD_LEG_MM, 2.0, 8.0,
    "the fillet weld's leg along each bracket plate's base (throat 0.7 leg): the default 3 mm is HIRAKU's assumption "
    "(not sized); the range is HIRAKU's for R9 to size it")
OPTIONAL["mount_joint"] = ("mount_plate_t", "mount_bolt_d", "mount_weld_leg")


def mount_joint(v: dict) -> Record:
    """The suspension brackets' part.joint from the variables (missing ones at their defaults)."""
    def get(k):
        return float(v.get(k, VARIABLES[k].default))
    return mounts.joint_sizes(get("mount_plate_t"), get("mount_bolt_d"), get("mount_weld_leg"))
INTEGERS = INTEGERS + ("battery_split",)
# The net without side bars (build_side_net), assumptions without a source:
SIDE_NET_CLIP_MM = 20.0  # a quick-release clip's size (a cube), between the net's edge and the post
SIDE_NET_CLIP_PITCH_MM = 100.0  # clips along a post at most this far apart
SIDE_NET_CLIP_KG = 0.05  # a clip's mass
SIDE_NET_COVER_MM = 100.0  # every point R at least this far inside the net's taut outline
SIDE_NET_FRONT_GAP_MM = 100.0  # the net's front end at least this far behind the front wheels' tyres
FENDER_T_MM = 3.0  # fender wall (assumption)
FENDER_PIECES = 4
# nu 0.46: Goodfellow's generic HDPE page, as the material card does
HDPE = {"name": "HDPE (high-density polyethylene), molded", "E_MPa": 1000.0, "nu": 0.46, "density_kg_m3": 950.0,
        "yield_MPa": 25.0, "source": "material card data/materials/cards/hdpe.json: E 1000 MPa and yield 25 MPa are "
                                    "round values inside Goodfellow's generic HDPE ranges (E 0.5-1.2 GPa, tensile "
                                    "strength 15-40 MPa; product page 'HDPE Granules' ET32-GL-000110, secondary), not "
                                    "checked; density 950 kg/m3 from the same page (checked 2026-11-06); nu 0.46 is "
                                    "the value the card's nu note says that page lists (not checked)",
        "verified": False}
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
# side_guard: none, bars, bars and a net, or a net on the roll bar's posts without bars; rops: the roll bar alone (hoop), with rear stays, or four posts with
# stays; fenders.
OPTION_VALUES: dict[str, dict[str, dict[str, float]]] = {
    "part_mounts": {"none": {k: 0.0 for k in MOUNT_GROUPS}, "brackets": {k: 1.0 for k in MOUNT_GROUPS}},
    "pedal_mount": {"none": {"pedal_mount": 0.0}, "floor": {"pedal_mount": 1.0}, "bulkhead": {"pedal_mount": 2.0},
                    "post": {"pedal_mount": 3.0}},
    "side_guard": {"none": {"side_bars": 0.0, "side_net": 0.0}, "bars": {"side_bars": 1.0, "side_net": 0.0},
                   "bars_net": {"side_bars": 1.0, "side_net": 1.0}, "net": {"side_bars": 0.0, "side_net": 1.0}},
    "rops": {"hoop": {"rollbar_stays": 0.0, "rollbar_front_posts": 0.0},
             "hoop_stays": {"rollbar_stays": 1.0, "rollbar_front_posts": 0.0},
             "four_post": {"rollbar_stays": 0.0, "rollbar_front_posts": 1.0},
             "four_post_stays": {"rollbar_stays": 1.0, "rollbar_front_posts": 1.0}},
    "fenders": {"none": {"fenders": 0.0}, "wheel": {"fenders": 1.0}},
    # side_opening: the side frame round point R only and an opening ahead of it, closed by a net on clips or a door
    # (with side_guard bars or bars_net); grab_bar: on the side frame's front post
    "side_opening": {"none": {"side_opening": 0.0}, "clip_net": {"side_opening": 1.0}, "door": {"side_opening": 2.0}},
    "grab_bar": {"none": {"grab_bar": 0.0}, "front_post": {"grab_bar": 1.0}},
}
CHOICES: dict[str, tuple[str, ...]] = {"joints": ("butt", "gusset", "through"),
                                       **{k: tuple(opts) for k, opts in OPTION_VALUES.items()}}
CHOICE_SIZES = ("gusset_leg",)  # set by a choice, but may be given as a value with it


def applies(flags: dict) -> bool:
    """For products that move by themselves and carry people or cargo."""
    return bool(flags.get("self_propelled")) and bool(flags.get("ride") or flags.get("cargo"))


# Part list order of v1 (the rest after them as placed: each wheel, then its suspension)
PART_ORDER = ["battery", "battery2", "main_switch", "motor", "reducer", "driveshaft_RL", "driveshaft_RR", "controller", "aux",
              "floor_front", "pedal_accel", "pedal_brake", "brake_master", "floor", "deck", "front_body", "bulkhead",
              "steering_wheel",
              "steering_column", "steering_shaft", "steering_rack", "tie_rod_FL", "tie_rod_FR", "seat_R", "seat_belt_R",
              "seat_L", "seat_belt_L", "occupant_R", "occupant_L", "cargo"]


def plate_mass(size) -> float:
    """kg of a plate whose box is `size` (mm; the thinnest side its thickness, one of PLATE_KG_M2's): its area (the
    two other sides) x the catalogue's unit mass at that thickness."""
    s = sorted(float(x) for x in size)
    t = next((k for k in PLATE_KG_M2 if abs(k - s[0]) < 1e-9), None)
    if t is None:
        raise DesignGenError(f"no unit mass for a {s[0]:g} mm sheet (PLATE_KG_M2: {sorted(PLATE_KG_M2)} mm)")
    return s[1] * s[2] * 1e-6 * PLATE_KG_M2[t]


def plate_masses(lay: Layout) -> None:
    """The plate parts (PLATE_T_MM) of PLATE_STEEL, each weighing its own sheet (plate_mass): the kits took the
    pedals', the master cylinder's and the rear lamps' masses out of the plates they stand on (kit/body.py,
    kit/safety.py), which held only while the plates' masses were a budget; those parts now weigh on top."""
    for pid in PLATE_T_MM:  # the plate parts
        p = lay.part(pid)
        p["mass_kg"] = plate_mass(p["box"]["size_mm"])
        p["material"], p["finish"] = PLATE_STEEL, PLATE_FINISH
    fb = lay.part("front_body")
    fb["material"], fb["finish"] = PLATE_STEEL, PLATE_FINISH


FLOOR_TRIM = (("floor", 1), ("floor_front", 0))  # the floor plate and the axis it is trimmed along
FLOOR_BRACKET_GAP_MM = 1.0  # a trimmed floor's edge this far from the bracket's face


def _floors_clear_of_brackets(design: Record) -> None:
    """Trim the floors so that none lies under a bracket (welded to the frame) above it, and each can be
    lowered in from above (K4 assembly): floor narrowed in y to the brackets' inner faces (it stays one
    plate the seats stand on), floor_front's end toward the bracket pulled back in x; the mass scales
    with the area. Each trimmed floor's name says what it was trimmed for."""
    brackets = [p for p in design["parts"] if p["kind"] == "bracket"]
    for pid, axis in FLOOR_TRIM:
        f = next((p for p in design["parts"] if p["id"] == pid), None)
        if f is None:
            continue
        c, s = np.asarray(f["box"]["center_mm"], float), np.asarray(f["box"]["size_mm"], float)
        lo, hi = c - s / 2.0, c + s / 2.0
        by = []
        for b in brackets:
            bc, bs = np.asarray(b["box"]["center_mm"], float), np.asarray(b["box"]["size_mm"], float)
            blo, bhi = bc - bs / 2.0, bc + bs / 2.0
            if bhi[2] <= hi[2] or not all(blo[i] < hi[i] and bhi[i] > lo[i] for i in (0, 1)):
                continue  # not above the floor's top, or not over it
            if blo[2] <= hi[2] + FLOOR_BRACKET_GAP_MM:
                continue  # standing on the floor (a pedal stand on it): put on after the floor, not in its way
            if axis == 1:  # narrower, about the floor's centre line
                inner = (blo[1] - c[1]) if bc[1] > c[1] else (c[1] - bhi[1])
                half = min(hi[1] - c[1], inner - FLOOR_BRACKET_GAP_MM)
                lo[1], hi[1] = c[1] - half, c[1] + half
            elif bc[0] > c[0]:
                hi[0] = min(hi[0], blo[0] - FLOOR_BRACKET_GAP_MM)
            else:
                lo[0] = max(lo[0], bhi[0] + FLOOR_BRACKET_GAP_MM)
            by.append(b["id"])
        if not by:
            continue
        new = hi - lo
        if new[0] <= 0 or new[1] <= 0:
            raise DesignGenError(f"{pid} would vanish to keep clear of the brackets above it ({', '.join(by)})")
        f["mass_kg"] = float(f["mass_kg"]) * float(new[0] * new[1]) / float(s[0] * s[1])
        f["box"]["center_mm"] = [float(v) for v in (lo + hi) / 2.0]
        f["box"]["size_mm"] = [float(v) for v in new]
        f["name"] += f" ({'、'.join(sorted(set(by)))} の下から外した寸法)"


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
    (stations out of order, rails over the wheels). Interference of the parts is left to K4, except the options'
    members and parts (_check_options_clear); a clash of an option's member names the rollbar_width that clears it.
    """
    kw = dict(version=version, parent=parent, reason=reason, name=name, _reference=_reference)
    try:
        return _build(values, **kw)
    except _WidthClash as e:
        raise DesignGenError(_width_advice(e, values, kw)) from None
    except _GussetClash as e:
        raise DesignGenError(_gusset_advice(e, values, kw)) from None


def _build(values: dict | None = None, *, version: int = 1, parent: int | None = None, reason: str | None = None,
           name: str | None = None, _reference: bool | None = None) -> Record:
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
    steel = json.loads(MATERIALS.read_text(encoding="utf-8"))[PLATE_STEEL]
    plate_t = {pid: v[k] for k, pids in PLATE_T_VARS.items() for pid in pids}
    ff_size = (floor_front_x - S1, 2.0 * floor_half, plate_t["floor_front"])
    lay.put(Item("floor_front", "足元の床", "body", ff_size, plate_mass(ff_size)),
            x=rear_at(S1), y=0.0, z=bottom_at(on_rails))
    fl_size = (S1 - S2, track, plate_t["floor"])
    lay.put(Item("floor", "座席の下の床", "body", fl_size, plate_mass(fl_size)), x=lay.between("S1", "S2"),
            y=0.0, z=bottom_at(on_rails))
    deck_len = S2 - DECK_GAP_MM - E
    # the deck and the load between the rear suspension towers and the upper arms' pivot brackets (both stand
    # on the rails); the front body between the front ones
    def inside_mounts(front: bool) -> float:
        ss = [s for s in supports if s["actuation"]["steer"] == front]
        uca = min(abs(s["suspension"]["hardpoints_mm"][k][1]) for s in ss for k in ("uca_front", "uca_rear"))
        return min(rail_y - v["tower_b"] / 2.0, uca - mounts.BRACKET_PAD_MM) - DECK_TOWER_GAP_MM

    deck_w = min(DECK_WIDTH_MM, 2.0 * inside_mounts(False))
    dk_size = (deck_len, deck_w, plate_t["deck"])
    lay.put(Item("deck", "荷台の床", "body", dk_size, plate_mass(dk_size)),
            x=rear_at(E), y=0.0, z=bottom_at(on_rails), carried_by=lay.at("S2", "B", "E"))
    lay.put(Item("front_body", "前の外板とバンパー", "body", (360.0, min(600.0, 2.0 * inside_mounts(True)), 280.0),
                 FRONT_BODY_KG), x=front_at(lay.station_x("F")), y=0.0, z=bottom_at(on_rails))
    place_battery(lay, v)
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
    seat_back = seat_x - SEAT_LENGTH_MM / 2.0  # the seat back stays here whatever seat_depth is
    for pid, label, y in (("seat_R", "運転席", -seat_y), ("seat_L", "助手席", seat_y)):
        lay.put(Item(pid, label, "seat", (v["seat_depth"], SEAT_WIDTH_MM, v["seat_height"]), 12.0), x=rear_at(seat_back),
                y=y, z=bottom_at(lay.top_of("floor")))
    _kit(safety.place_belts, lay, ("seat_R", "seat_L"))
    if v["seat_slide"] > 0:
        _belt_behind_travel(lay, "seat_belt_R", v["seat_slide"] / 2.0)
    for pid, label, seat in (("occupant_R", "運転者 (体重は強さの不利側)", "seat_R"), ("occupant_L", "同乗者 (体重は強さの不利側)", "seat_L")):
        sy = lay.part(seat)["box"]["center_mm"][1]
        lay.put(Item(pid, label, "occupant", (450.0, 450.0, 520.0), v["occupant_mass"]),
                x=lay.rear_of(seat) + SEAT_LENGTH_MM / 2.0 - OCCUPANT_BACK_MM, y=sy, z=bottom_at(lay.top_of(seat)))
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
    bh_size = (plate_t["bulkhead"], 2.0 * (rail_y + v["rail_b"] / 2.0), bh_h)
    lay.put(Item("bulkhead", "前の隔壁 (足元と前の区画の仕切りの板)", "body", bh_size, plate_mass(bh_size)),
            x=front_at(bulkhead_front), y=0.0, z=bottom_at(on_rails), carried_by=None)
    _kit(safety.place_brakes, lay, "pedal_brake", "bulkhead", "floor_front", feet["pose"])
    _kit(arms.place, lay, rho)  # the arms, dampers and ball joints in place of the suspension boxes
    plate_masses(lay)
    lay.reorder(PART_ORDER)
    materials = {"stkr400": material, PLATE_STEEL: steel}
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
                              rear_post_dx={"A": x_a - post_x} if x_a - post_x != post_dx else None,
                              joint=mount_joint(v))
    except (mounts.MountError, ValueError) as e:
        raise DesignGenError(str(e)) from None
    _check_rear_post(supports, post_x, v["tower_b"])
    dash = build_dash(frame, lay, post_x, dash_z, rail_y, tower, rho)
    lay.part("bulkhead")["carried_by"] = dash["bulkhead_nodes"]
    if v["pedal_mount"] == 3:
        root = build_pedal_beam(frame, lay, post_x, rail_y, tower, v["tower_b"])
        # the bulkhead lies on the beam's front face as on the posts' (the same 2.3 mm into the tube's box)
        lay.part("bulkhead")["carried_by"] = sorted(set(dash["bulkhead_nodes"]) | {root})
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
        others = [p for p in lay.parts if p["kind"] not in GUSSET_NEIGHBOURS]  # the gussets keep out of these
        # gusset_ends: also the front and rear end corners (L joints: the rail ends on its end cross member)
        ends = [list(stations)[0], list(stations)[-1]] if v.get("gusset_ends", 0.0) == 1 else []
        for st in tee + ends:
            for side in "LR":
                lay.parts.extend(frame.gussets(f"{side}{st}", f"cross_{st}", v["gusset_leg"], v["gusset_thickness"], rho,
                                               avoid=others))
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
    # The side frame's and the front posts' outriggers leave the rails at the seat cross members' nodes, in line with
    # the cross members: in the cross members' own section they would continue them as one tube through the rail
    # node, which the shell mesh cannot join (_check_tubes_through). Without rollbar_foot_* they are the cross
    # members' width and wall at the rails' height (rail_h; HIRAKU's choice, not sized); the roll bar's own feet
    # stand off the cross members' stations and keep the cross members' section.
    side_foot = foot
    if foot == cross and not any(v[k] for k in ZERO_OR_AT_LEAST):
        side_foot = frame.section(*rect_tube(v["cross_b"], v["rail_h"], v["cross_t"]))
    columns = build_rops_extra(frame, lay, v, side_foot) if v["rollbar"] == 1 else {}
    if (v["side_opening"] or v["grab_bar"]) and v["side_bars"] != 1:
        raise DesignGenError("side_opening and grab_bar are on the side frame: they need side_bars = 1")
    guard = build_side_guard(frame, lay, v, side_foot, columns) if v["side_bars"] == 1 else None
    if v["side_net"] == 1 and guard is not None and len(guard["heights"]) < 2:
        raise DesignGenError("side_net with the side bars needs at least two of them (side_bar_count >= 2)")
    net = build_side_net(frame, lay, v, foot) if v["side_net"] == 1 and guard is None else None
    closure = build_side_opening(frame, lay, v, guard, side_foot) if v["side_opening"] else None
    if v["grab_bar"] == 1:
        build_grab_bars(frame, v, guard)
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
    for k, q in variables.items():
        basis = VARIABLES[k].basis if k in VARIABLES else DERIVED_BASIS.get(k)
        if basis:
            q["basis"] = basis
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
    add_options(design, v, guard, rho, net, closure)
    if v["seat_slide"] > 0 and BODY_PARTS:
        _driver_on_the_slide(design, "occupant_R")
    arms.pivot_brackets(design, rho, mount_joint(v))  # clevis brackets at the lower arm pivots and damper tops on bare nodes
    _floors_clear_of_brackets(design)  # each floor can be lowered in from above
    systems.write(design)  # system and connects_to of every part, when the contract has them
    if x_a - post_x != post_dx:
        _check_rear_legs(design)
    _check_tubes_through(design)
    if v["gusset_leg"] > 0:
        _check_gussets_meshable(design)
    from .kit import joints

    joints.join(design)  # every part's joints touch, with where (at_mm) and the axis of the turning ones
    design["archetype"] = "ladder_4w"  # designgen.ARCHETYPES
    from .ladder_4w_modules import split

    from .ladder_4w_names import write as write_names

    # module on every part and member, joint on the nodes, design.modules; then the members' and variables' names
    return write_names(split(design))


class _GussetClash(DesignGenError):
    """A gusset the shell mesh cannot place (k1_shell.mesh.joint_layout); build() adds the gusset_leg that fits."""


GUSSET_STEP_MM = 10.0  # the steps build() tries gusset_leg down in, after a gusset the shell mesh cannot place


def _check_gussets_meshable(design: Record) -> None:
    """Every gusset sits on a T joint as the shell mesh lays the joints out at its default element size
    (k1_shell.mesh.joint_layout: a joint whose grid lines clash with a neighbour's, often a long gusset's legs over
    the tower posts' joints next to it, becomes tied and loses its gussets); else _GussetClash with the mesh's
    reason. Without a gusset, or a frame the mesh cannot read, nothing is checked here (the mesh reports it)."""
    from hiraku.solvers.k1_shell import mesh as shell_mesh

    try:
        shell_mesh.joint_layout(design)
    except shell_mesh.DesignError as e:
        if str(e).startswith("gusset "):
            raise _GussetClash(f"the shell mesh cannot place the gussets: {e}") from None
    except Exception:  # noqa: BLE001 - the shell mesh's own errors are reported when it is made
        return


def _gusset_advice(e: _GussetClash, values: dict | None, kw: dict) -> str:
    """The clash's message with what places the gussets: the same gussets with cross_members_through = 0 (with the
    side frame's feet on the rails' outer walls, the cross members through to those walls clash with them), else the
    largest gusset_leg (GUSSET_STEP_MM steps down to GUSSET_LEG_MIN_MM), else gusset_leg 0."""
    v = _values(values)
    now = v["gusset_leg"]
    head = f"{e} at gusset_leg = {now:g} mm"
    if v["cross_members_through"] == 1:
        try:
            _build({**(values or {}), "cross_members_through": 0.0}, **kw)
            return (f"{head}; with cross_members_through = 0 these gussets are placed (the cross members through the "
                    f"rails clash with the joints next to them, such as the side frame's feet on the outer walls): make "
                    f"cross_members_through 0 to keep the gussets, or gusset_leg 0 to keep the through members")
        except DesignGenError:
            pass
    leg = now
    while leg - GUSSET_STEP_MM >= GUSSET_LEG_MIN_MM - 1e-9:
        leg -= GUSSET_STEP_MM
        try:
            _build({**(values or {}), "gusset_leg": leg}, **kw)
        except _GussetClash:
            continue
        except DesignGenError as other:
            return f"{head}; at {leg:g} mm the design stops on something else: {other}"
        return f"{head}; make gusset_leg {leg:g} mm or less (or 0: no gussets)"
    return f"{head}; no gusset_leg down to {GUSSET_LEG_MIN_MM:g} mm is placed: make it 0 (no gussets)"


def _check_tubes_through(design: Record) -> None:
    """No node where two tubes both pass straight through (the shell mesh, k1_shell.mesh, cannot join them there):
    members that continue straight with the same section are one tube for the shell mesh, read with its own rule
    (k1_shell.mesh._runs). Else DesignGenError naming the tubes and the node. The usual case: the side frame's or
    the front posts' outriggers (rollbar_foot_*, 0: the cross members' section) continue a seat cross member across a
    rail node, so the rail and the cross member's run cross there."""
    from hiraku.solvers.k1_shell import mesh as shell_mesh

    try:
        runs, ends = shell_mesh._runs(design, shell_mesh._members(design))
    except Exception:  # noqa: BLE001 - the shell mesh's own errors are reported when it is made
        return
    for node in ends:
        through = [r for r in runs if node in r.nodes_at and r.start_node != node and r.end_node != node]
        if len(through) > 1:
            names = ", ".join(r.members[0] for r in through)
            hint = (" give the outriggers their own section (rollbar_foot_b / rollbar_foot_h different from cross_b / "
                    "cross_h)" if any(m.startswith(("side_foot_", "rops_front_foot_", "rollbar_foot_"))
                                      for r in through for m in r.members) else
                    " give one of them another section, or move one so they meet at separate nodes")
            raise DesignGenError(f"tubes ({names}) pass straight through each other at node {node!r}: the shell mesh "
                                 f"needs one of them to end there;{hint}")


BATTERY_IDS = ("battery", "battery2")  # the packs' part ids (battery_split of them)


def place_battery(lay: Layout, v: dict[str, float]) -> list[str]:
    """The battery under the floor between the rails, its volume and mass going with the capacity: one pack
    (battery_split = 1, v1), or the same pack cut in two across y (battery_split = 2), each half the width and mass,
    BATTERY_SPLIT_GAP_MM apart, centred on battery_x. Returns the packs' ids. Two packs that do not fit between the
    rails, or along x between the seat cross members' faces, are a DesignGenError (the one pack is left to K4)."""
    n = int(v["battery_split"])
    gap = BATTERY_SPLIT_GAP_MM if n == 2 else 0.0
    scale = v["battery_capacity"] / BATTERY_V1["capacity"]
    room = lay.rail_inner_gap() - BATTERY_RAIL_GAP_MM - gap  # for the packs' width together
    if room <= 0.0:
        raise DesignGenError(f"battery_split = 2: the clear width between the rails ({lay.rail_inner_gap():g} mm) leaves "
                             f"no room for two packs {gap:g} mm apart; make rail_spacing wider or use one pack")
    b_w = min(BATTERY_V1["width"], room)
    b_l = BATTERY_V1["length"] * BATTERY_V1["width"] / b_w * scale
    x = v["battery_x"]
    if n == 2:
        front = lay.station_x("S1") - v["cross_b"] / 2.0
        rear = lay.station_x("S2") + v["cross_b"] / 2.0
        if x + b_l / 2.0 > front + TOL_FIT_MM or x - b_l / 2.0 < rear - TOL_FIT_MM:
            raise DesignGenError(f"battery_split = 2: the packs ({b_l:.0f} mm long at battery_x = {x:g} mm) do not fit "
                                 f"between the seat cross members' faces ({rear:g} to {front:g} mm); make "
                                 "battery_capacity smaller, rail_spacing wider (the packs get shorter) or move battery_x")
    z = top_at(lay.bottom_of("floor"))
    if n == 1:
        lay.put(Item("battery", f"電池 (LiFePO4 {v['battery_voltage']:g} V {v['battery_capacity']:g} Ah)", "battery",
                     (b_l, b_w, BATTERY_V1["height"]), BATTERY_V1["mass"] * scale), x=x, y=0.0, z=z)
        return ["battery"]
    half_w = b_w / 2.0
    out = []
    for k, (pid, sign, side) in enumerate(zip(BATTERY_IDS, (-1.0, 1.0), ("右", "左")), 1):
        lay.put(Item(pid, f"電池 {k} ({side}。LiFePO4 {v['battery_voltage'] / 2.0:g} V {v['battery_capacity']:g} Ah、"
                          f"2 つを直列で {v['battery_voltage']:g} V)", "battery",
                     (b_l, half_w, BATTERY_V1["height"]), BATTERY_V1["mass"] * scale / 2.0),
                x=x, y=sign * (half_w + gap) / 2.0, z=z)
        out.append(pid)
    return out


TOL_FIT_MM = 1e-6


def _belt_behind_travel(lay: Layout, belt: str, back_mm: float) -> None:
    """Put the seat belt (as kit/safety.place_belts placed it, behind the seat back at the travel's middle)
    back_mm farther back: behind the seat back at the rearmost end of the travel. Placed again, so that the
    nodes it is carried by are those of where it now is."""
    p = lay.part(belt)
    lay.parts.remove(p)
    c, size = p["box"]["center_mm"], p["box"]["size_mm"]
    lay.put(Item(p["id"], p["name"], p["kind"], tuple(size), float(p["mass_kg"])), x=float(c[0]) - back_mm,
            y=float(c[1]), z=float(c[2]))


def _driver_on_the_slide(design: Record, occupant: str) -> None:
    """The driver's body parts (kit/body.occupant_parts) where K4 seats the large driver within the seat's
    travel (k4_geometry.people.poses), each part in its place in the parts list. K4's place for him does not
    depend on the body parts (the legs are not checked against occupant parts, the knee room skips them)."""
    from hiraku.solvers.k4_geometry.people import grip_points, poses

    pose = poses(design)[occupant]["big"]
    parts = design["parts"]
    mine = {p["id"]: i for i, p in enumerate(parts) if body.person_of(p["id"]) == occupant}
    trunk = parts[mine[occupant]]
    whole = dict(trunk, mass_kg=float(sum(parts[i]["mass_kg"] for i in mine.values())))
    lay = Layout({**design, "parts": [whole]})
    wheel = next((p for p in parts if p["id"] == "steering_wheel"), None)
    new = body.occupant_parts(lay, occupant, pose, grip_points(wheel) if wheel is not None else None)
    if {p["id"] for p in new} != set(mine):
        raise DesignGenError(f"{occupant}'s body parts changed on the seat's slide")
    for p in new:
        parts[mine[p["id"]]] = p


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


def pedal_beam_line(lay: Layout, post_x: float, rail_y: float, tower_b: float) -> Record:
    """Where the pedal beam (pedal_mount = 3) goes, from the pedals and the front tower's rear post: side ("L" or
    "R": the pedals' side of the centre line), z (the mean height of the pedals' front edges, where
    kit/attach.pedal_stands puts the stands' middle), the root on the post's axis [post_x, +-rail_y, z] and the
    tip, inboard along y to the inner side of the innermost pedal's stand (attach.STAND_PAD_MM / 2 past the pedal's
    middle) plus PEDAL_BEAM_PAST_MM. Raises DesignGenError when a pedal is outboard of the post's axis or the beam
    would be shorter than the tube is wide."""
    from hiraku.designgen.kit import attach

    pedals = [p for p in lay.parts if p["kind"] in ("accelerator_pedal", "brake_pedal")]
    if not pedals:
        raise DesignGenError("pedal_mount = 3 needs the pedals")
    ys = [float(p["box"]["center_mm"][1]) for p in pedals]
    sy = 1.0 if sum(ys) > 0 else -1.0
    zs = []
    for p in pedals:
        cs = attach._corners(p)
        x0 = float(np.max(cs[:, 0]))
        zs.append(float(np.mean(cs[np.abs(cs[:, 0] - x0) < 1e-6][:, 2])))
    inboard = [rail_y - sy * y for y in ys]  # each pedal's middle this far inboard of the post's axis
    if min(inboard) < 0.0:
        raise DesignGenError(f"a pedal is outboard of the front tower's post (y = {sy * rail_y:g} mm): no pedal beam "
                             f"from the post reaches its stand")
    length = max(inboard) + attach.STAND_PAD_MM / 2.0 + PEDAL_BEAM_PAST_MM
    if length < tower_b:
        raise DesignGenError(f"the pedal beam would be {length:.0f} mm long, shorter than the tube ({tower_b:g} mm)")
    z = float(np.mean(zs))
    return {"side": "L" if sy > 0 else "R", "z": z, "length": length, "root": [post_x, sy * rail_y, z],
            "tip": [post_x, sy * (rail_y - length), z]}


def build_pedal_beam(frame: Frame, lay: Layout, post_x: float, rail_y: float, section: str, tower_b: float) -> str:
    """The pedal beam (pedal_mount = 3, pedal_beam_line): the front tower's rear post on the pedals' side is split at
    the beam's height (node <side>AK; pieces tower_post_<side>A2 below and tower_post_<side>A2_up above) and the
    member pedal_beam goes from there inboard along y to node <side>AKT, of the tower's section. Returns the root
    node. Raises DesignGenError when the height is not on the post at least tower_b off its ends."""
    line = pedal_beam_line(lay, post_x, rail_y, tower_b)
    side, z = line["side"], line["z"]
    post = next((m for m in frame.members if m["id"].startswith("tower_post_")
                 and all(abs(frame.nodes[m[k]][0] - post_x) < 1e-6 and abs(frame.nodes[m[k]][1] - line["root"][1]) < 1e-6
                         for k in ("node_a", "node_b"))), None)
    if post is None:
        raise DesignGenError(f"no front tower post at x = {post_x:.0f} mm on the {side} rail for the pedal beam")
    lo, hi = sorted(frame.nodes[post[k]][2] for k in ("node_a", "node_b"))
    if not lo + tower_b <= z <= hi - tower_b:
        raise DesignGenError(f"the pedals' front edges (z = {z:.0f} mm) are not on the front tower's post ({lo:.0f} to "
                             f"{hi:.0f} mm, {tower_b:g} mm off its ends) for the pedal beam; move pedal_x")
    root = frame.split(post["id"], f"{side}AK", line["root"], (post["id"], post["id"] + "_up"))
    tip = frame.node(f"{side}AKT", line["tip"])
    frame.member("pedal_beam", root, tip, section)
    return root


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
    x_front, front_st = s1, "S1"
    if v["side_opening"]:  # the front post just ahead of point R, the rest of the side open (side_opening)
        if any(front.values()):
            raise DesignGenError("side_opening leaves the side open ahead of point R: the four posts' front post at S1 "
                                 "would stand in the opening (use rops without front posts)")
        x_front, front_st = max(x for x, _ in net_points(lay, v)) + v["side_bar_reach"], "SG"
        if not s2 + MIN_STATION_GAP_MM <= x_front <= s1 - d:
            raise DesignGenError(f"side_opening: the side frame's front post ({x_front:.0f} mm, side_bar_reach ahead of "
                                 f"point R) must stand between S2 + {MIN_STATION_GAP_MM:g} mm ({s2 + MIN_STATION_GAP_MM:g} "
                                 f"mm) and S1 less a post ({s1 - d:g} mm); change side_bar_reach")
    for side, sy in (("L", 1.0), ("R", -1.0)):
        cols = {}
        for st, x in (("S2", s2), (front_st, x_front)):
            if st == "S1" and front.get(side):
                cols[st] = [n for n in front[side] if any(abs(frame.nodes[n][2] - z) < 1e-6 for z in zs)]
                continue
            root = f"{side}{st}"
            if st == "SG":  # a rail node of its own for the front post's outrigger
                root = mounts.split_rail(frame, [x, sy * rail_y, rail_z], root, {f"{side}S1", f"{side}S2"}, "")
            tip = _outrigger(frame, f"side_foot_{side}{st}", root, [x, sy * half, rail_z], foot_section)
            cols[st] = _column(frame, f"side_post_{side}{st}", f"{side}{st}V", tip, x, sy * half, zs, sec, "stkm13a")
        if len(cols[front_st]) != len(zs):
            raise DesignGenError("the front posts do not meet the side bars' heights")
        for k, (a, b) in enumerate(zip(cols["S2"], cols[front_st]), 1):
            frame.member(f"side_bar_{side}{k}", a, b, sec, "stkm13a")
        nodes[side] = cols["S2"] + cols[front_st]
    members = {side: [m["id"] for m in frame.members if m["id"] not in before and m["id"].startswith("side_")
                      and m["id"][len("side_"):].split("_", 1)[-1][:1] == side] for side in "LR"}
    return {"heights": heights, "x": (s2, x_front), "y": half, "d": d, "nodes": nodes, "members": members, "z": zs,
            "front": {side: nodes[side][len(zs):] for side in "LR"}, "front_station": front_st}


def build_side_opening(frame: Frame, lay: Layout, v: dict[str, float], guard: dict, foot_section: str) -> dict:
    """What closes the opening ahead of the side frame (side_opening = 1: a net on clips, 2: a door), each side: an
    outrigger side_open_foot_<side> from the rail at the opening's front end x_f (SIDE_NET_FRONT_GAP_MM behind the
    front wheels' tyres) out to the side frame's line, and the closure in that line from a clip's length ahead of
    the front post's face to x_f, from a clip's height over the outrigger's top to the highest side bar. The net
    (1) is a triangle: clips (net_clip) up the front post every at most SIDE_NET_CLIP_PITCH_MM and one on the
    outrigger's end (a taut net spans the hull of its fastenings, as build_side_net). The door (2) is a rectangle of
    DOOR_T_MM sheet: two hinges (door_hinge) on the post's face at its bottom and top, one latch (door_latch) on the
    outrigger's end under its front edge; the hinges and the latch are SIDE_NET_CLIP_MM cubes of SIDE_NET_CLIP_KG
    (HIRAKU's choice, no source). Returns {side: {"kind", "box", "fittings": [(pid, kind, lo, hi, nodes)],
    "carried_by", "area"}}."""
    x_g, half, rail_y, rail_z = guard["x"][1], guard["y"], v["rail_spacing"] / 2.0, v["rail_height"]
    c = SIDE_NET_CLIP_MM
    x_r = x_g + guard["d"] / 2.0  # the front post's front face
    fh = frame.sections[foot_section]["dims_mm"]["h"]
    z_lo = rail_z + fh / 2.0 + c
    z_hi = guard["z"][-1]
    x_f = lay.station_x("A") - v["wheel_radius"] - SIDE_NET_FRONT_GAP_MM
    if x_f - x_r < 4.0 * c:
        raise DesignGenError(f"side_opening: no room for the opening between the side frame's front post ({x_r:.0f} mm) "
                             f"and the front wheels ({x_f:.0f} mm)")
    out = {}
    for side, sy in (("L", 1.0), ("R", -1.0)):
        y = sy * half
        post = guard["front"][side]
        root = mounts.split_rail(frame, [x_f - c / 2.0, sy * rail_y, rail_z], f"{side}SO", {f"{side}S1", f"{side}S2"}, "")
        tip = _outrigger(frame, f"side_open_foot_{side}", root, [x_f - c / 2.0, y, rail_z], foot_section)
        fits = []
        if v["side_opening"] == 1:
            n_clip = max(2, int(math.ceil((z_hi - z_lo - c) / SIDE_NET_CLIP_PITCH_MM)) + 1)
            for k in range(n_clip):
                z = z_lo + c / 2.0 + (z_hi - z_lo - c) * k / (n_clip - 1)
                fits.append((f"side_open_clip_{side}{k + 1}", "net_clip", [x_r, y - c / 2.0, z - c / 2.0],
                             [x_r + c, y + c / 2.0, z + c / 2.0], list(post)))
            fits.append((f"side_open_clip_{side}{n_clip + 1}", "net_clip", [x_f - c, y - c / 2.0, rail_z + fh / 2.0],
                         [x_f, y + c / 2.0, z_lo], [root, tip]))
            thick, kind, area = v["side_net_wire_d"], "side_net", (x_f - x_r - c) * (z_hi - z_lo) / 2.0
        else:
            for k, z in enumerate((z_lo + c / 2.0, z_hi - c / 2.0), 1):
                fits.append((f"side_door_hinge_{side}{k}", "door_hinge", [x_r, y - c / 2.0, z - c / 2.0],
                             [x_r + c, y + c / 2.0, z + c / 2.0], list(post)))
            fits.append((f"side_door_latch_{side}", "door_latch", [x_f - c, y - c / 2.0, rail_z + fh / 2.0],
                         [x_f, y + c / 2.0, z_lo], [root, tip]))
            thick, kind, area = DOOR_T_MM, "door", (x_f - x_r - c) * (z_hi - z_lo)
        held = sorted({n for f in fits for n in f[4]}) if kind == "side_net" else list(post)
        out[side] = {"kind": kind, "box": {"center_mm": [(x_r + c + x_f) / 2.0, y, (z_lo + z_hi) / 2.0],
                                            "size_mm": [x_f - x_r - c, thick, z_hi - z_lo]},
                     "fittings": fits, "carried_by": held, "area": area}
    return out


def build_grab_bars(frame: Frame, v: dict[str, float], guard: dict) -> None:
    """A grab bar each side on the side frame's front post (grab_bar = 1): stand-offs grab_<side>_1 and _3 out from
    the post's nodes at the lowest and the highest side bar, and the grip grab_<side>_2 up between their ends, its
    centre line grab_bar_gap + grab_bar_d / 2 clear of the post's outer face (round tube grab_bar_d x grab_bar_t,
    STKM13A)."""
    d, t = v["grab_bar_d"], v["grab_bar_t"]
    if 2.0 * t >= d:
        raise DesignGenError("grab_bar_t must be less than half of grab_bar_d")
    if len(guard["z"]) < 2:
        raise DesignGenError("grab_bar runs between the lowest and the highest side bar: side_bar_count >= 2")
    sec = frame.section(*round_tube(d, t))
    out_y = guard["y"] + guard["d"] / 2.0 + v["grab_bar_gap"] + d / 2.0
    for side, sy in (("L", 1.0), ("R", -1.0)):
        lo, hi = guard["front"][side][0], guard["front"][side][-1]
        a = frame.node(f"{side}GB1", [frame.nodes[lo][0], sy * out_y, frame.nodes[lo][2]])
        b = frame.node(f"{side}GB2", [frame.nodes[hi][0], sy * out_y, frame.nodes[hi][2]])
        frame.member(f"grab_{side}_1", lo, a, sec, "stkm13a")
        frame.member(f"grab_{side}_2", a, b, sec, "stkm13a")
        frame.member(f"grab_{side}_3", hi, b, sec, "stkm13a")


def net_points(lay: Layout, v: dict[str, float]) -> list[tuple[float, float]]:
    """(x, z) of the occupants' point R (hip and shoulder, load_path.point_r: AIST 95th percentile male), each
    seated occupant's x spread by +-seat_slide / 2 (the driver may sit anywhere on the slide)."""
    from hiraku.solvers.shelf.load_path import point_r

    out = []
    for t in (p for p in lay.parts if p["kind"] == "occupant" and "." not in p["id"]):
        for _, x, _ in point_r(t):
            for dx in ((-v["seat_slide"] / 2.0, v["seat_slide"] / 2.0) if v["seat_slide"] > 0 else (0.0,)):
                out.append((float(x[0]) + dx, float(x[2])))
    return out


def _inside_by(tri: list, p) -> float:
    """How far p is inside the counter-clockwise polygon tri (the least distance to its sides; negative outside)."""
    out = math.inf
    for a, b in zip(tri, tri[1:] + tri[:1]):
        e = (b[0] - a[0], b[1] - a[1])
        L = math.hypot(*e)
        out = min(out, (e[0] * (p[1] - a[1]) - e[1] * (p[0] - a[0])) / L)
    return out


def build_side_net(frame: Frame, lay: Layout, v: dict[str, float], foot_section: str) -> dict:
    """The net each side without side bars (side_net = 1, side_bars = 0): a net in the plane of the roll bar's
    posts (y = +-rollbar_width / 2), fastened by quick-release clips (parts, net_clip) to the roll bar's post every
    at most SIDE_NET_CLIP_PITCH_MM from just over the post's foot to just under the top beam. In front: with the
    four posts (rollbar_front_posts = 1) clips on the front post the same way (a rectangle between the posts);
    without them a short outrigger side_net_foot_<side> from the rail out to the post's line (of the roll bar's foot
    section) with one clip on its end: the net is the triangle between the post and that clip (a taut net cannot
    reach beyond the hull of its fastenings). Its length is the least that keeps every point R (net_points) at
    least SIDE_NET_COVER_MM inside the triangle (the clips' size taken off its corners). Returns {side: {"box",
    "clips": [(pid, lo, hi, carried_by)], "carried_by", "area"}}."""
    if v["rollbar"] != 1:
        raise DesignGenError("side_net without side bars is fastened to the roll bar's posts: it needs rollbar = 1")
    half, rail_y, rail_z = v["rollbar_width"] / 2.0, v["rail_spacing"] / 2.0, v["rail_height"]
    if half <= rail_y + SIDE_NET_CLIP_MM:
        raise DesignGenError("side_net without side bars needs rollbar_width wider than rail_spacing (the net stands "
                             "in the line of the roll bar's posts, outboard of the rails)")
    r_post = (v["rollbar_b"] if v["rollbar_square"] == 1 else v["rollbar_d"]) / 2.0
    xh = lay.station_x("S2") - v["rollbar_offset"]
    fb, fh = (v[f"rollbar_foot_{k}"] or v[f"cross_{k}"] for k in ("b", "h"))
    c = SIDE_NET_CLIP_MM
    z_lo = rail_z + fh / 2.0 + c  # the net's bottom edge: a clip's height over the outriggers' top
    z_hi = _seat_top(lay) + v["rollbar_height"] - r_post  # the top beam's underside
    x_r = xh + r_post + c  # the net's rear edge: a clip's length in front of the post
    H = z_hi - z_lo
    pts = [(x - x_r, z - z_lo) for x, z in net_points(lay, v)]
    front_posts = v["rollbar_front_posts"] == 1
    s1 = lay.station_x("S1")
    if front_posts:
        x_f = s1 - r_post - c
        tri = [(c, c), (x_f - x_r - c, c), (x_f - x_r - c, H - c), (c, H - c)]
    else:
        x_max = lay.station_x("A") - v["wheel_radius"] - SIDE_NET_FRONT_GAP_MM  # behind the front wheels

        def tri_of(W):
            return [(c, c), (W - c, c), (c, H - c)]

        lo_, hi_ = 3.0 * c, x_max - x_r
        if min(_inside_by(tri_of(hi_), p) for p in pts) < SIDE_NET_COVER_MM:
            raise DesignGenError(f"a net from the roll bar's post to the rail cannot cover point R by {SIDE_NET_COVER_MM:g} "
                                 f"mm before x = {x_max:.0f} mm; use the side bars or the four posts")
        for _ in range(60):
            mid = 0.5 * (lo_ + hi_)
            lo_, hi_ = (mid, hi_) if min(_inside_by(tri_of(mid), p) for p in pts) < SIDE_NET_COVER_MM else (lo_, mid)
        x_f = x_r + math.ceil(hi_)
        tri = tri_of(x_f - x_r)
    if front_posts and min(_inside_by(tri, p) for p in pts) < SIDE_NET_COVER_MM:
        raise DesignGenError(f"the net between the posts does not cover point R by {SIDE_NET_COVER_MM:g} mm")
    out = {}
    n_clip = max(2, int(math.ceil((H - c) / SIDE_NET_CLIP_PITCH_MM)) + 1)
    zs = [z_lo + c / 2.0 + (H - c) * k / (n_clip - 1) for k in range(n_clip)]
    for side, sy in (("L", 1.0), ("R", -1.0)):
        y = sy * half
        post = (f"{side}{ROLLBAR_STATION}O" if f"{side}{ROLLBAR_STATION}O" in frame.nodes
                else f"{side}{ROLLBAR_STATION}", f"{side}{ROLLBAR_STATION}T")
        clips = [(f"side_net_clip_{side}{k + 1}", [xh + r_post, y - c / 2.0, z - c / 2.0], [x_r, y + c / 2.0, z + c / 2.0],
                  list(post)) for k, z in enumerate(zs)]
        carried = list(post)
        if front_posts:
            col = [m for m in frame.members if m["id"].startswith(f"rops_front_post_{side}")]
            fnodes = sorted({m[k] for m in col for k in ("node_a", "node_b")})
            clips += [(f"side_net_clip_{side}{len(zs) + k + 1}", [x_f, y - c / 2.0, z - c / 2.0],
                       [s1 - r_post, y + c / 2.0, z + c / 2.0], fnodes) for k, z in enumerate(zs)]
            carried += fnodes
        else:
            root = mounts.split_rail(frame, [x_f - c / 2.0, sy * rail_y, rail_z], f"{side}N", set(), "")
            tip = _outrigger(frame, f"side_net_foot_{side}", root, [x_f - c / 2.0, y, rail_z], foot_section)
            clips.append((f"side_net_clip_{side}{len(zs) + 1}", [x_f - c, y - c / 2.0, rail_z + fh / 2.0],
                          [x_f, y + c / 2.0, z_lo], [root, tip]))
            carried += [tip]
        area = (x_f - x_r) * H / (1.0 if front_posts else 2.0)  # the net's own outline
        out[side] = {"box": {"center_mm": [(x_r + x_f) / 2.0, y, (z_lo + z_hi) / 2.0],
                             "size_mm": [x_f - x_r, v["side_net_wire_d"], H]},
                     "clips": clips, "carried_by": carried, "area": area, "shape": "rectangle" if front_posts else "triangle"}
    return out


def add_options(design: Record, v: dict[str, float], guard: dict | None, rho: float, net: dict | None = None,
                closure: dict | None = None) -> None:
    """The parts of the options, on the finished design (after the roll bar part): the side nets, the opening's net
    or door (side_opening), the fenders, the pedal stands, then the part mounts (kit/attach.py). The materials they
    name are added."""
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
                w = v["side_net_wire_d"]
                areal = 2.0 * math.pi * w * w / 4.0 / v["side_net_pitch"] * float(mats["ss400"]["density_kg_m3"]) * 1e-9
                design["parts"].append({
                    "id": f"side_net_{side}", "name": f"{'左' if side == 'L' else '右'}の横の網 (棒と柱のあいだ)",
                    "kind": "side_net", "box": {"center_mm": [(x0 + x1) / 2.0, y, (z0 + z1) / 2.0],
                                                 "size_mm": [x1 - x0, w, z1 - z0]},
                    "mass_kg": float(areal * (x1 - x0) * (z1 - z0)), "carried_by": list(guard["nodes"][side]),
                    "material": "ss400", "finish": "galvanized"})
                used.add("ss400")
    for side, n in (net or {}).items():
        w = v["side_net_wire_d"]
        areal = 2.0 * math.pi * w * w / 4.0 / v["side_net_pitch"] * float(mats["ss400"]["density_kg_m3"]) * 1e-9
        name = "左" if side == "L" else "右"
        shape = "柱のあいだ" if n["shape"] == "rectangle" else "柱とレールのあいだの三角"
        design["parts"].append({
            "id": f"side_net_{side}", "name": f"{name}の横の網 (ロールバーの柱に留める、外せる; {shape})",
            "kind": "side_net", "box": n["box"], "mass_kg": float(areal * n["area"]), "carried_by": n["carried_by"],
            "material": "ss400", "finish": "galvanized"})
        for pid, lo, hi, nodes in n["clips"]:
            design["parts"].append({
                "id": pid, "name": f"{name}の網の留め具 (外せる)", "kind": "net_clip",
                "box": {"center_mm": [(a + b) / 2.0 for a, b in zip(lo, hi)], "size_mm": [b - a for a, b in zip(lo, hi)]},
                "mass_kg": SIDE_NET_CLIP_KG, "carried_by": list(nodes), "material": "ss400", "finish": "galvanized"})
        used.add("ss400")
    fitting_words = {"net_clip": "網の留め具 (外せる)", "door_hinge": "扉の蝶番", "door_latch": "扉の掛け金"}
    for side, n in (closure or {}).items():
        name = "左" if side == "L" else "右"
        if n["kind"] == "side_net":
            w = v["side_net_wire_d"]
            areal = 2.0 * math.pi * w * w / 4.0 / v["side_net_pitch"] * float(mats["ss400"]["density_kg_m3"]) * 1e-9
            design["parts"].append({
                "id": f"side_open_net_{side}", "name": f"{name}の乗り降りの開口の網 (外せる留め具で留める; 柱とレールのあいだの三角)",
                "kind": "side_net", "box": n["box"], "mass_kg": float(areal * n["area"]), "carried_by": n["carried_by"],
                "material": "ss400", "finish": "galvanized"})
        else:
            design["parts"].append({
                "id": f"side_door_{side}", "name": f"{name}の乗り降りの扉 (蝶番と掛け金)", "kind": "door", "box": n["box"],
                "mass_kg": plate_mass(n["box"]["size_mm"]), "carried_by": n["carried_by"],
                "material": PLATE_STEEL, "finish": PLATE_FINISH})
        for pid, kind, lo, hi, nodes in n["fittings"]:
            design["parts"].append({
                "id": pid, "name": f"{name}の{fitting_words[kind]}", "kind": kind,
                "box": {"center_mm": [(a + b) / 2.0 for a, b in zip(lo, hi)], "size_mm": [b - a for a, b in zip(lo, hi)]},
                "mass_kg": SIDE_NET_CLIP_KG, "carried_by": list(nodes), "material": "ss400", "finish": "galvanized"})
        used.add("ss400")
    if v["fenders"] == 1:
        if v["fender_width"] < v["wheel_width"]:
            raise DesignGenError(f"fender_width ({v['fender_width']:g} mm) must be at least the tyre's ({v['wheel_width']:g} mm)")
        for s in design["supports"]:
            design["parts"].extend(fender_parts(s, v, float(HDPE["density_kg_m3"])))
        design["materials"]["hdpe"] = dict(HDPE)
    if v["pedal_mount"]:
        target = {1.0: "floor_front", 2.0: "bulkhead", 3.0: "member:pedal_beam"}[v["pedal_mount"]]
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


OPTION_MEMBERS = ("rops_", "side_", "pedal_", "grab_")  # the options' members
OPTION_PARTS = ("side_net_", "fender_", "side_open_", "side_door_")  # the options' parts (the mounts and stands check themselves: kit/attach)


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
                if k.startswith("member:"):
                    raise _WidthClash(k.split(":", 1)[1], o.split(":", 1)[1], ov)
                raise DesignGenError(f"{k.split(':', 1)[1]} goes {ov:.1f} mm into {o.split(':', 1)[1]}; "
                                     "change the option's sizes")


class _WidthClash(DesignGenError):
    """An option's member (the side frame, the front posts: at rollbar_width / 2) reaches into another body;
    build() turns it into advice on rollbar_width."""

    def __init__(self, member: str, other: str, mm: float):
        super().__init__(f"{member} goes {mm:.1f} mm into {other}")
        self.member, self.other, self.mm = member, other, mm


WIDTH_STEP_MM = 50.0  # the steps build() tries rollbar_width in, after a clash of an option's member


def _width_advice(e: _WidthClash, values: dict | None, kw: dict) -> str:
    """The clash's message with the rollbar_width that clears it: the generator is rebuilt at wider widths
    (WIDTH_STEP_MM steps up to the range's top) and the first that builds is named; else what stops it."""
    var = VARIABLES["rollbar_width"]
    now = _values(values)["rollbar_width"]
    head = f"{e} at rollbar_width = {now:g} mm (the side frame and the front posts stand at rollbar_width / 2)"
    tried = []
    w = now
    while w < var.hi:
        w = min(w + WIDTH_STEP_MM, var.hi)
        try:
            _build({**(values or {}), "rollbar_width": w}, **kw)
        except _WidthClash as c:
            tried.append(f"{c.mm:.1f} mm at {w:g}")
            continue
        except DesignGenError as other:
            seen = f" ({', '.join(tried)})" if tried else ""
            return (f"{head}; wider{seen}, at {w:g} mm the design stops on something else: {other}; "
                    "change the option or its sizes instead")
        seen = f" (still {', '.join(tried)})" if tried else ""
        return f"{head}; make rollbar_width at least {w:g} mm{seen}, the range is {var.lo:g} to {var.hi:g} mm"
    if now >= var.hi:
        return f"{head}; rollbar_width is at the top of its range ({var.hi:g} mm): change the option or its sizes instead"
    return (f"{head}; widening rollbar_width to the top of its range ({var.hi:g} mm) does not clear it "
            f"({', '.join(tried)}): change the option or its sizes instead")


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

"""A home-use electric care bed (back raise, knee raise, height), built from its variables.

Not a vehicle: it stands on four casters (supports of kind caster, rigid, braked) and nothing
drives it. The axes are the contract's: x toward the head end, y left, z up, origin on the floor
below the middle of the head-end casters, so the foot-end casters are at x = -caster_base.

Frame (rectangular steel tube, beams that the K1 frame model and the shell mesher take):
- the base frame on the casters: two base rails (base_L, base_R) from x = 0 to -caster_base at
  +-caster_track / 2, base cross members at both ends (base_cross_H, base_cross_F) and one under
  the lift actuator (base_cross_M, at the knee hinge's x);
- four lift posts (post_<side>H, post_<side>F) from the base corners up to the upper frame. The lift
  (in a real bed a linkage moved by the lift actuator) is modelled as these posts at the lift's
  working height deck_height: an assumption, the posts take the load as fixed columns;
- the upper (deck) frame: two rails at y = +-(deck_width - rail_b) / 2 through the stations below
  and a cross member at each of H, P1, KA, B, K, A, P2, F;
- the side rails: on each side a portal of round tube (two posts and a top bar split at its middle
  node <side>SM) standing on short sockets (socket_<side><station>) outboard of the upper rails at
  stations P1 and K; their members carry part side_rail_<side> (a 0 kg part, like the roll bar of
  ladder_4w). With side_rail_sleeve_height > 0 each post's foot stands in a sleeve (a round tube
  over the post, member side_rail_<side>_sleeve<station> up to node <side>SS<station>), modelled as one
  tube from the post's inside to the sleeve's outside.

Upper frame stations along x (head to foot): H head end, P1 head posts (x = 0), KA the knee
actuator's anchor, B the back hinge, K the knee hinge (the seat's foot end, also the lift
actuator's top), A the back actuator's anchor, T the thigh-leg hinge, P2 foot posts
(x = -caster_base), F foot end. The deck is centred over the casters.

Deck: four steel boards (deck_back, deck_seat, deck_thigh, deck_leg) on the upper rails, flat (the
analyses that raise the back or the knees turn them in their own models). The back and knee
actuators push levers that hang from the back and thigh boards at their hinges (lever length
back_lever, knee_lever below the hinge); their far ends pin in clevis brackets (bracket_back,
bracket_knee: two lugs bracket_t thick, the thick part meshed as solid bricks by bracket_mesh)
welded under the anchor cross members. At the flat position the lever tip and the anchor pin are
level, so the actuator lies along x.

Electric chain (part.system and connects_to, all written here): power_cord (mains) -> main_switch
-> control_box -> actuator_back, actuator_knee, actuator_lift; hand_switch -> control_box (signal).

Loads that come from the requirements: occupant_load (Q33, the user lying on the bed) and
safe_working_load (Q40, SWL = the user, the mattress and the accessories); the mattress
parts carry SWL - user. Both are split over the four deck boards in proportion to their lengths. Q56 is the back raise angle. Every dimension, mass and actuator rating below is
a design choice (an assumption) unless its meaning gives a source; the actuator ratings are of the
kind printed in bed actuator catalogues and are not checked in this repository.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hiraku.designgen.kit.frame import Frame, FrameError
from hiraku.designgen.kit.sections import rect_tube, round_tube
from hiraku.designgen.ladder_4w import DesignGenError  # one error class for every generator (designgen.DesignGenError)

Record = dict[str, Any]

MATERIALS = Path(__file__).resolve().parents[2] / "data" / "materials" / "materials.json"
TUBE = "stkr400"  # rectangular tubes (assumption)
ROUND = "stkm13a"  # the side rails' round tube (assumption)
PLATE = "ss400"  # deck boards and brackets (assumption)
G0 = 9.80665  # m/s^2, standard gravity (as hiraku.solvers.k0_handcalc.units.STANDARD_GRAVITY)

MIN_STATION_GAP_MM = 100.0  # stations closer than this make members shorter than the joints (assumption)
MIN_LEG_MM = 300.0  # the leg board is at least this long (assumption)
MIN_POST_MM = 80.0  # lift posts at least this long between the base and the upper frame (assumption)
CASTER_FORK_MM = 40.0  # caster fork and plate between the wheel's top and the base rail's bottom (assumption)
CASTER_WIDTH_MM = 30.0  # caster wheel width (assumption)
BOARD_GAP_MM = 1.0  # deck boards sit this far above the rail tops; gaps between parts that touch (assumption)
MATTRESS_T_MM = 150.0  # mattress thickness: the sample answers' note says 150 mm or less (taken at 150)
OCCUPANT_BOX_MM = (1700.0, 500.0, 250.0)  # a lying person as a mass box, centred on the deck (assumption)
ACTUATOR_SECTION_MM = 60.0  # actuator body as a box this wide and high (assumption)
ACTUATOR_Y_MM = 120.0  # back actuator at y = +this, knee actuator at -this (they pass each other; assumption)
CLEVIS_GAP_MM = 20.0  # gap between the two lugs for the actuator's eye (assumption)
PIN_MARGIN_MM = 15.0  # lug material below the anchor pin's centre (assumption)
BOARDS = (("back", "背の部分", "H", "B"), ("seat", "腰の部分", "B", "K"), ("thigh", "ももの部分", "K", "T"),
          ("leg", "脚の部分", "T", "F"))  # deck boards: (name, label, head-side station, foot-side station)
SOCKET_GAP_MM = 1.0  # side rail post's face this far outboard of the upper rail's outer face (assumption)


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


VARIABLES: dict[str, Var] = {
    # layout (assumptions: sizes of a home care bed's deck, no source)
    "bed_length": Var("mm", 1910.0, 1800.0, 2100.0, "deck length, head end to foot end (the mattress length)"),
    "deck_width": Var("mm", 910.0, 800.0, 1000.0, "deck width, outer face to outer face of the upper rails"),
    "deck_height": Var("mm", 450.0, 300.0, 700.0, "deck top above the floor at the lift's working height (the frame "
                       "is built at this height; the lift posts take its length)"),
    "caster_base": Var("mm", 1500.0, 1100.0, 1800.0, "head casters to foot casters (x); the lift posts stand there"),
    "caster_track": Var("mm", 880.0, 600.0, 950.0, "caster centre to caster centre across (y); the default puts the "
                        "lift posts upright under the upper rails"),
    "caster_radius": Var("mm", 50.0, 40.0, 75.0, "caster wheel radius"),
    # deck sections (the leg board takes the rest of bed_length)
    "back_length": Var("mm", 700.0, 550.0, 850.0, "back board, head end to the back hinge B"),
    "seat_length": Var("mm", 200.0, 150.0, 300.0, "seat board between the back hinge B and the knee hinge K"),
    "thigh_length": Var("mm", 350.0, 250.0, 450.0, "thigh board between the knee hinge K and the thigh-leg hinge T"),
    "deck_t": Var("mm", 1.2, 0.8, 3.2, f"deck board steel plate thickness ({PLATE})"),
    # tubes
    "rail_b": Var("mm", 30.0, 25.0, 60.0, "upper rail tube width"),
    "rail_h": Var("mm", 60.0, 40.0, 100.0, "upper rail tube height"),
    "rail_t": Var("mm", 2.3, 1.6, 3.2, "upper rail tube wall"),
    "cross_b": Var("mm", 30.0, 25.0, 60.0, "upper cross member tube width"),
    "cross_h": Var("mm", 30.0, 25.0, 60.0, "upper cross member tube height"),
    "cross_t": Var("mm", 1.6, 1.2, 3.2, "upper cross member tube wall"),
    "base_b": Var("mm", 40.0, 30.0, 80.0, "base rail and base cross member tube width"),
    "base_h": Var("mm", 40.0, 30.0, 80.0, "base rail and base cross member tube height"),
    "base_t": Var("mm", 2.3, 1.6, 3.2, "base tube wall"),
    "post_b": Var("mm", 40.0, 30.0, 60.0, "lift post square tube width"),
    "post_t": Var("mm", 2.3, 1.6, 4.5, "lift post tube wall"),
    # side rails (round tube portals at stations P1 and K)
    "side_rail_height": Var("mm", 400.0, 250.0, 500.0, "side rail top bar's centre above the deck top"),
    "side_rail_d": Var("mm", 25.4, 19.0, 34.0, f"side rail round tube outside diameter ({ROUND})"),
    "side_rail_t": Var("mm", 1.6, 1.0, 2.3, "side rail tube wall"),
    "side_rail_sleeve_height": Var("mm", 0.0, 0.0, 200.0, "sleeve (insertion holder) each side rail post is inserted "
                                   f"in: round tube ({ROUND}) over the post's foot, this long up from the socket's centre "
                                   "line (the upper rail's centre height); 0: no sleeve, the post stands in the socket"),
    "side_rail_sleeve_t": Var("mm", 2.3, 1.6, 4.5, "sleeve wall (its inside diameter is side_rail_d); used when "
                              "side_rail_sleeve_height > 0. The socket stub under it then takes the sleeve's outside "
                              "width and this wall"),
    # actuators and their linkage
    "back_lever": Var("mm", 120.0, 60.0, 200.0, "back lever: from the back hinge down to the actuator's pin"),
    "back_actuator_reach": Var("mm", 400.0, 300.0, 600.0, "back actuator's anchor this far from the back hinge B "
                               "toward the foot (the actuator's pin-to-pin length at the flat position)"),
    "knee_lever": Var("mm", 80.0, 50.0, 150.0, "knee lever: from the knee hinge down to the actuator's pin"),
    "knee_actuator_reach": Var("mm", 400.0, 300.0, 600.0, "knee actuator's anchor this far from the knee hinge K "
                               "toward the head (pin-to-pin length at the flat position)"),
    "knee_angle_max": Var("deg", 25.0, 10.0, 40.0, "thigh board's largest raise (no requirement gives it)"),
    "lift_lever_ratio": Var("1", 1.5, 0.5, 3.0, "lift actuator force per newton of the weight on the lift (the lift "
                            "linkage's ratio; the linkage itself is not modelled)"),
    "actuator_rated_force": Var("N", 4000.0, 2000.0, 8000.0, "push rating of the actuator picked (each of the three); "
                                "assumption, of the kind in bed actuator catalogues (not checked)"),
    "actuator_speed": Var("m/s", 0.006, 0.003, 0.015, "actuator stroke speed at its rated load; assumption (not "
                          "checked)"),
    "actuator_stroke": Var("mm", 200.0, 100.0, 300.0, "actuator stroke available (each of the three)"),
    "bracket_t": Var("mm", 6.0, 4.0, 12.0, f"actuator bracket lug thickness ({PLATE}; the thick part, meshed as solid)"),
    "bracket_width": Var("mm", 40.0, 30.0, 80.0, "actuator bracket lug width (along x)"),
    "pin_d": Var("mm", 10.0, 8.0, 16.0, "actuator pin diameter"),
    # requirements (not design choices)
    "occupant_load": Var("N", 1350.0, meaning="Q33: the user's weight on the deck"),
    "safe_working_load": Var("N", 1700.0, meaning="Q40: safe working load (user + mattress + accessories)"),
    "back_angle_max": Var("deg", 70.0, meaning="Q56: back raise range"),
    # fixed values (assumptions, no source)
    "actuator_mass": Var("kg", 2.5, meaning="assumption: one actuator (motor, gear, spindle)"),
    "control_box_mass": Var("kg", 1.5, meaning="assumption: control box with its power supply"),
    "board_mass": Var("kg", 5.0, meaning="assumption: head board, and the foot board, each"),
    "caster_mass": Var("kg", 0.8, meaning="assumption: one caster with its brake"),
    "actuator_rated_duty": Var("%", 10.0, meaning="assumption: duty rating of the actuator picked, of the kind in bed "
                               "actuator catalogues (not checked)"),
    "actuator_max_on_time": Var("s", 120.0, meaning="assumption: longest continuous run of the actuator picked (not "
                                "checked)"),
    "actuator_motor_speed": Var("rpm", 3000.0, meaning="assumption: actuator motor speed under load (not checked)"),
    # fatigue of the welded joints, as ladder_4w: the S-N form (slope 3, knee at 1e7 cycles) follows
    # A. F. Hobbacher, Recommendations for Fatigue Design of Welded Joints and Components, 2nd ed.,
    # Springer 2016 (IIW), not checked in this repository; FAT 71 and the slope 22 are assumptions.
    # The counts of use (R3) are a spectrum: the damage takes 2 m - 1 below the knee (IIW-1823-07 (2008)
    # sec. 4.3.1, p.110; library_fatigue.spectrum_slope), not fatigue_slope_after_knee_constant_amplitude
    "fatigue_fat_class": Var("MPa", 71.0, meaning="assumption (see the module)"),
    "fatigue_slope": Var("1", 3.0, meaning="IIW form, not checked"),
    "fatigue_knee_cycles": Var("1", 1e7, meaning="IIW form, not checked"),
    "fatigue_slope_after_knee_constant_amplitude": Var("1", 22.0, meaning="assumption: the constant-amplitude slope beyond the knee; the "
                                    "damage over the counts of use (a spectrum) does not use it, it takes 2 m - 1"),
}
# requirement key -> (variable, unit): propose() takes these from the requirements, not from the caller
REQUIREMENTS = {"Q33": ("occupant_load", "N"), "Q40": ("safe_working_load", "N"), "Q56": ("back_angle_max", "deg")}
SIGNATURE = ("bed_length", "deck_width", "deck_height", "caster_base", "back_length")
TITLE = "在宅の電動介護ベッド (背上げ・膝上げ・高さ調整、キャスター 4 つ)"
DESCRIPTION = "home-use electric care bed, back and knee raise, height lift, four casters"
CHOICES: dict[str, tuple[str, ...]] = {}

SYSTEMS = {  # part kind -> system
    "deck": "deck", "mattress": "deck", "board": "frame", "side_rail": "side_rail", "caster": "support",
    "actuator": "actuator", "bracket": "actuator", "power_supply": "power", "switch": "power",
    "controller": "control", "hand_switch": "control", "occupant": "occupant", "cargo": "cargo",
}


def applies(flags: dict) -> bool:
    """For products that stand on the floor, carry a person lying or sitting and move by actuators, not by
    themselves."""
    return bool(flags.get("standing") and flags.get("lean") and flags.get("electric")
                and not flags.get("self_propelled"))


def spec() -> list[Record]:
    out = []
    for name, v in VARIABLES.items():
        r: Record = {"name": name, "unit": v.unit, "default": v.default, "revisable": v.revisable, "meaning": v.meaning}
        if v.revisable:
            r.update(min=v.lo, max=v.hi)
        out.append(r)
    return out


def _q(v: float, unit: str) -> Record:
    return {"value": float(v), "unit": unit}


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
        if var.revisable and not var.lo - 1e-9 <= val <= var.hi + 1e-9:
            raise DesignGenError(f"{name} = {val:g} {var.unit} is outside {var.lo:g} to {var.hi:g} {var.unit}")
        if not var.revisable and val <= 0:
            raise DesignGenError(f"{name} must be positive, got {val:g} {var.unit}")
        vals[name] = val
    return vals


def values_of(design: Record) -> dict[str, Record]:
    return {k: v for k, v in design.get("variables", {}).items() if k in VARIABLES}


def apply_choices(choices: dict | None, values: dict) -> dict:
    if choices:
        raise DesignGenError(f"the care bed has no choices; got {sorted(choices)}")
    return dict(values)


def choices_of(design: Record) -> dict[str, str]:
    return {}


def requirement_values(requirements: Record) -> dict[str, Record]:
    """The variables the requirements set (REQUIREMENTS), converted to the variables' units."""
    from hiraku.solvers.k0_handcalc import units

    out = {}
    for r in requirements.get("requirements", []):
        if r["key"] in REQUIREMENTS and isinstance(r.get("value"), (int, float)) and not isinstance(r["value"], bool):
            name, unit = REQUIREMENTS[r["key"]]
            try:
                out[name] = _q(units.convert(float(r["value"]), r.get("unit", unit), unit, r["key"]), unit)
            except ValueError as e:
                raise DesignGenError(f"requirement {r['key']}: {e}") from None
    if "occupant_load" in out and "safe_working_load" in out and \
            out["safe_working_load"]["value"] < out["occupant_load"]["value"]:
        raise DesignGenError("Q40 (safe working load) must be at least Q33 (the user's weight): it includes the user")
    return out


# ---------------------------------------------------------------- geometry from the variables


def stations(v: dict[str, float]) -> dict[str, float]:
    """x of every upper frame station, head to foot (raises when they do not close)."""
    x_head = (v["bed_length"] - v["caster_base"]) / 2.0
    x_foot = x_head - v["bed_length"]
    xb = x_head - v["back_length"]
    xk = xb - v["seat_length"]
    xt = xk - v["thigh_length"]
    leg = xt - x_foot
    if leg < MIN_LEG_MM:
        raise DesignGenError(f"the leg board would be {leg:g} mm, less than {MIN_LEG_MM:g} mm; shorten the back, seat "
                             f"or thigh boards or lengthen bed_length")
    st = {"H": x_head, "P1": 0.0, "KA": xk + v["knee_actuator_reach"], "B": xb, "K": xk,
          "A": xb - v["back_actuator_reach"], "T": xt, "P2": -v["caster_base"], "F": x_foot}
    order = sorted(st.items(), key=lambda kv: -kv[1])
    if [k for k, _ in order] != list(st):
        raise DesignGenError("the upper frame stations are out of order (head to foot: "
                             + ", ".join(f"{k} {x:g} mm" for k, x in order)
                             + "); the casters must stand under the deck between its ends and the actuators' anchors "
                               "between the hinges and the posts")
    for (a, xa), (b, xb_) in zip(order, order[1:]):
        if xa - xb_ < MIN_STATION_GAP_MM:
            raise DesignGenError(f"upper frame stations {a} ({xa:g} mm) and {b} ({xb_:g} mm) are closer than "
                                 f"{MIN_STATION_GAP_MM:g} mm; change the board lengths, caster_base or the actuator reaches")
    return st


def levels(v: dict[str, float]) -> dict[str, float]:
    """Heights (mm): the deck top, the upper rail centre, the base rail centre, the actuators' pins."""
    deck_top = v["deck_height"]
    z_u = deck_top - v["deck_t"] - BOARD_GAP_MM - v["rail_h"] / 2.0
    z_b = 2.0 * v["caster_radius"] + CASTER_FORK_MM + v["base_h"] / 2.0
    if (z_u - v["rail_h"] / 2.0) - (z_b + v["base_h"] / 2.0) < MIN_POST_MM:
        raise DesignGenError(f"the lift posts would be shorter than {MIN_POST_MM:g} mm between the base and the upper "
                             f"frame; raise deck_height or use smaller casters or tubes")
    return {"deck_top": deck_top, "upper": z_u, "base": z_b, "back_pin": z_u - v["back_lever"],
            "knee_pin": z_u - v["knee_lever"]}


def build(values: dict | None = None, *, version: int = 1, parent: int | None = None, reason: str | None = None,
          name: str | None = None) -> Record:
    """A design record from variables ({name: quantity or number}; the others take their defaults)."""
    v = _values(values)
    if v["safe_working_load"] < v["occupant_load"]:
        raise DesignGenError("safe_working_load (Q40) must be at least occupant_load (Q33): it includes the user")
    if 2.0 * v["side_rail_t"] >= v["side_rail_d"]:
        raise DesignGenError("side_rail_t must be less than half of side_rail_d")
    for tube in ("rail", "cross", "base"):
        if 2.0 * v[f"{tube}_t"] >= min(v[f"{tube}_b"], v[f"{tube}_h"]):
            raise DesignGenError(f"{tube}_t must be less than half of {tube}_b and {tube}_h")
    if v["caster_track"] > v["deck_width"] + 2.0 * v["caster_radius"]:
        raise DesignGenError("caster_track is wider than the deck by more than a caster: the casters stick out")
    st = stations(v)
    z = levels(v)
    # the sleeve's range (up to 200 mm) leaves at least 37 mm of post above it: the post is side_rail_height (250 mm
    # or more) plus deck_t, BOARD_GAP_MM and rail_h / 2 long, and side_rail_d is 34 mm at most
    mats = json.loads(MATERIALS.read_text(encoding="utf-8"))
    rho = {k: float(mats[k]["density_kg_m3"]) for k in (TUBE, ROUND, PLATE)}
    y_r = (v["deck_width"] - v["rail_b"]) / 2.0  # upper rail centre line
    y_b = v["caster_track"] / 2.0

    f = Frame(TUBE)
    rail = f.section(*rect_tube(v["rail_b"], v["rail_h"], v["rail_t"]))
    cross = f.section(*rect_tube(v["cross_b"], v["cross_h"], v["cross_t"]))
    base = f.section(*rect_tube(v["base_b"], v["base_h"], v["base_t"]))
    post = f.section(*rect_tube(v["post_b"], v["post_b"], v["post_t"]))
    srail = f.section(*round_tube(v["side_rail_d"], v["side_rail_t"]))
    sleeve = v["side_rail_sleeve_height"] > 0.0
    # the post's foot: in a sleeve, the post and the sleeve bend as one tube from the post's inside to the sleeve's
    # outside (their annuli touch, so the second moments add up to that tube's)
    d_out = v["side_rail_d"] + 2.0 * v["side_rail_sleeve_t"] if sleeve else v["side_rail_d"]
    sleeved = f.section(*round_tube(d_out, v["side_rail_t"] + v["side_rail_sleeve_t"])) if sleeve else None
    # the sockets: short square stubs as wide as the side rail tube, or its sleeve (a section of their own, so that
    # the shell mesher sees the cross member end at the rail and the socket start there, not one tube through it)
    sock = f.section(*rect_tube(d_out, d_out, v["side_rail_sleeve_t"] if sleeve else v["cross_t"]))
    try:
        # upper frame
        f.stations(st, y_r, z["upper"])
        f.rails(list(st), rail)
        for s in ("H", "P1", "KA", "B", "K", "A", "P2", "F"):
            f.cross(s, cross)
        # base frame and lift posts
        xm = st["K"]  # the lift actuator stands under the knee hinge
        for side, sy in (("L", 1.0), ("R", -1.0)):
            for tag, x in (("BH", 0.0), ("BM", xm), ("BF", -v["caster_base"])):
                f.node(f"{side}{tag}", [x, sy * y_b, z["base"]])
            f.member(f"base_{side}_H", f"{side}BH", f"{side}BM", base)
            f.member(f"base_{side}_F", f"{side}BM", f"{side}BF", base)
            f.member(f"post_{side}H", f"{side}BH", f"{side}P1", post)
            f.member(f"post_{side}F", f"{side}BF", f"{side}P2", post)
        for tag in ("BH", "BM", "BF"):
            f.member(f"base_cross_{tag[1]}", f"L{tag}", f"R{tag}", base)
        # side rails: sockets outboard of the upper rails at P1 and K, a portal on them
        y_s = y_r + v["rail_b"] / 2.0 + SOCKET_GAP_MM + d_out / 2.0
        z_top = z["deck_top"] + v["side_rail_height"]
        for side, sy in (("L", 1.0), ("R", -1.0)):
            feet = []
            for s in ("P1", "K"):
                foot = f.node(f"{side}S{s}", [st[s], sy * y_s, z["upper"]])
                f.member(f"socket_{side}{s}", f"{side}{s}", foot, sock)
                top = f.node(f"{side}ST{s}", [st[s], sy * y_s, z_top])
                if sleeve:
                    sl = f.node(f"{side}SS{s}", [st[s], sy * y_s, z["upper"] + v["side_rail_sleeve_height"]])
                    f.member(f"side_rail_{side}_sleeve{s}", foot, sl, sleeved, ROUND)
                    foot = sl
                f.member(f"side_rail_{side}_post{s}", foot, top, srail, ROUND)
                feet.append(top)
            mid = f.node(f"{side}SM", [(st["P1"] + st["K"]) / 2.0, sy * y_s, z_top])
            f.member(f"side_rail_{side}_topP1", feet[0], mid, srail, ROUND)
            f.member(f"side_rail_{side}_topK", mid, feet[1], srail, ROUND)
    except FrameError as e:
        raise DesignGenError(str(e)) from None
    for m in f.members:
        if m["id"].startswith("side_rail_"):
            m["part"] = f"side_rail_{m['id'][10]}"

    nodes = dict(f.nodes)
    parts: list[Record] = []

    def rail_nodes(x_hi: float, x_lo: float) -> list[str]:
        return [f"{side}{s}" for side in "LR" for s, x in st.items() if x_lo - 1e-6 <= x <= x_hi + 1e-6]

    def put(pid, label, kind, center, size, mass, carried_by):
        parts.append({"id": pid, "name": label, "kind": kind,
                      "box": {"center_mm": [float(c) for c in center], "size_mm": [float(s) for s in size]},
                      "mass_kg": float(mass), "carried_by": list(carried_by)})

    # deck boards on the rails
    board_w = v["deck_width"] - 2.0 * BOARD_GAP_MM
    zb = z["upper"] + v["rail_h"] / 2.0 + BOARD_GAP_MM + v["deck_t"] / 2.0
    for board, _, a, b in BOARDS:
        pid = f"deck_{board}"
        label = {"back": "背の板 (背上げで起きる)", "seat": "腰の板 (固定)", "thigh": "ももの板 (膝上げで起きる)", "leg": "脚の板"}[board]
        length = st[a] - st[b] - 2.0 * BOARD_GAP_MM
        put(pid, label, "deck", ((st[a] + st[b]) / 2.0, 0.0, zb), (length, board_w, v["deck_t"]),
            rho[PLATE] * 1e-9 * length * board_w * v["deck_t"], rail_nodes(st[a], st[b]))
    # the mattress and the user, each in one piece per deck board, its mass in proportion to the board's length
    # (assumption: the load is uniform along the deck), carried by that board's rail nodes; the user's pieces are
    # occupant.<board> (one person: hiraku.contract.loading.person_of)
    deck_top = z["deck_top"]
    mattress_w = min(2.0 * (y_s - d_out / 2.0 - BOARD_GAP_MM), v["deck_width"])
    extra_n = v["safe_working_load"] - v["occupant_load"]
    occ_w = min(OCCUPANT_BOX_MM[1], mattress_w)
    for board, label, a, b in BOARDS:
        length = st[a] - st[b]
        share = length / v["bed_length"]
        xc = (st[a] + st[b]) / 2.0
        carried = rail_nodes(st[a], st[b])
        put(f"mattress_{board}", f"マットレスと付属品の{label} (SWL の残り {extra_n:g} N の {share:.0%}、厚さ {MATTRESS_T_MM:g} mm)",
            "mattress", (xc, 0.0, deck_top + BOARD_GAP_MM + MATTRESS_T_MM / 2.0),
            (length - 2.0 * BOARD_GAP_MM, mattress_w, MATTRESS_T_MM), extra_n * share / G0, carried)
        put(f"occupant.{board}", f"利用者の{label} (寝た姿勢、体重 Q33 {v['occupant_load']:g} N の {share:.0%})", "occupant",
            (xc, 0.0, deck_top + 2 * BOARD_GAP_MM + MATTRESS_T_MM + OCCUPANT_BOX_MM[2] / 2.0),
            (length - 2.0 * BOARD_GAP_MM, occ_w, OCCUPANT_BOX_MM[2]), v["occupant_load"] * share / G0, carried)
    # head and foot boards, outboard of the deck ends
    for pid, label, s, sx in (("head_board", "頭の側の板 (ヘッドボード)", "H", 1.0), ("foot_board", "足の側の板 (フットボード)", "F", -1.0)):
        put(pid, label, "board", (st[s] + sx * (v["rail_h"] / 2.0 + 15.0 + BOARD_GAP_MM), 0.0, deck_top + 100.0),
            (30.0, v["deck_width"], 500.0), v["board_mass"], [f"L{s}", f"R{s}"])
    # side rails: 0 kg parts (their tubes are members)
    for side in "LR":
        mine = [m for m in f.members if m.get("part") == f"side_rail_{side}"]
        pts = [nodes[m[k]] for m in mine for k in ("node_a", "node_b")]
        lo = [min(p[i] for p in pts) - d_out / 2.0 for i in range(3)]
        hi = [max(p[i] for p in pts) + d_out / 2.0 for i in range(3)]
        put(f"side_rail_{side}", f"{'左' if side == 'L' else '右'}のサイドレール (柵。管は骨組みの部材)", "side_rail",
            [(a + b) / 2.0 for a, b in zip(lo, hi)], [b - a for a, b in zip(lo, hi)], 0.0,
            [f"{side}P1", f"{side}K"] + sorted({m[k] for m in mine for k in ("node_a", "node_b")}))
    # actuators and their brackets
    w = ACTUATOR_SECTION_MM
    h_lug = {"back": v["back_lever"], "knee": v["knee_lever"]}
    for tag, hinge, anchor, y, pin in (("back", "B", "A", ACTUATOR_Y_MM, z["back_pin"]),
                                       ("knee", "K", "KA", -ACTUATOR_Y_MM, z["knee_pin"])):
        x0, x1 = sorted((st[hinge], st[anchor]))
        put(f"actuator_{tag}", f"{'背上げ' if tag == 'back' else '膝上げ'}のアクチュエーター", "actuator",
            ((x0 + x1) / 2.0, y, pin), (x1 - x0 - 2.0 * v["bracket_width"], w, w), v["actuator_mass"],
            [f"L{hinge}", f"R{hinge}", f"L{anchor}", f"R{anchor}"])
        top = z["upper"] - v["cross_h"] / 2.0
        drop = top - (pin - PIN_MARGIN_MM)
        if drop <= v["pin_d"]:
            raise DesignGenError(f"the {tag} lever is too short for its bracket under the cross member: make "
                                 f"{tag}_lever longer than cross_h / 2 + pin_d + {PIN_MARGIN_MM:g} mm")
        span_y = 2.0 * v["bracket_t"] + CLEVIS_GAP_MM
        put(f"bracket_{tag}", f"{'背上げ' if tag == 'back' else '膝上げ'}のアクチュエーターの金具 (2 枚の耳、厚さ {v['bracket_t']:g} mm)",
            "bracket", (st[anchor], y, top - drop / 2.0), (v["bracket_width"], span_y, drop),
            rho[PLATE] * 1e-9 * 2.0 * v["bracket_t"] * v["bracket_width"] * drop, [f"L{anchor}", f"R{anchor}"])
    z_lo, z_hi = z["base"] + v["base_h"] / 2.0 + BOARD_GAP_MM, z["upper"] - v["cross_h"] / 2.0 - BOARD_GAP_MM
    put("actuator_lift", "高さのアクチュエーター (昇降のリンクを動かす)", "actuator", (st["K"], 0.0, (z_lo + z_hi) / 2.0),
        (w, w, z_hi - z_lo), v["actuator_mass"], ["LBM", "RBM", "LK", "RK"])
    # electrics under the head part of the deck, between P1 and KA
    xe = (st["P1"] + st["KA"]) / 2.0
    under = z["upper"] - v["rail_h"] / 2.0 - BOARD_GAP_MM
    put("control_box", "制御器 (電源つき)", "controller", (xe, 0.0, under - 40.0), (150.0, 250.0, 80.0),
        v["control_box_mass"], ["LP1", "RP1", "LKA", "RKA"])
    put("main_switch", "主電源のスイッチ", "switch", (xe, 150.0, under - 20.0), (40.0, 40.0, 40.0), 0.1,
        ["LP1", "LKA"])
    put("power_cord", "電源コードとプラグ (商用電源)", "power_supply", (xe, -170.0, under - 20.0), (60.0, 60.0, 40.0), 0.3,
        ["RP1", "RKA"])
    put("hand_switch", "手元スイッチ (サイドレールに掛ける)", "hand_switch",
        ((st["P1"] + st["K"]) / 2.0, y_s + d_out / 2.0 + 25.0 + BOARD_GAP_MM, deck_top + v["side_rail_height"] - 80.0),
        (120.0, 50.0, 30.0), 0.2, ["LSM"])
    # casters (supports) and their parts
    supports = []
    for sid, x, y in (("FL", 0.0, y_b), ("FR", 0.0, -y_b), ("RL", -v["caster_base"], y_b), ("RR", -v["caster_base"], -y_b)):
        tag = ("L" if y > 0 else "R") + ("BH" if x == 0.0 else "BF")
        supports.append({"id": sid, "kind": "caster", "position_mm": [x, y, v["caster_radius"]],
                         "radius_mm": v["caster_radius"], "width_mm": CASTER_WIDTH_MM, "suspension": {"type": "rigid"},
                         "actuation": {"drive": False, "brake": True, "steer": False}, "frame_node": tag})
        hgt = 2.0 * v["caster_radius"] + CASTER_FORK_MM
        put(f"caster_{sid}", f"キャスター {sid} (ブレーキつき)", "caster", (x, y, hgt / 2.0),
            (2.0 * v["caster_radius"], CASTER_WIDTH_MM, hgt - BOARD_GAP_MM), v["caster_mass"], [tag])

    variables = {k: _q(v[k], VARIABLES[k].unit) for k in VARIABLES}
    design = {
        "design_version": version,
        "parent_version": parent,
        "name": name or f"care-bed v{version}: {DESCRIPTION}",
        "change_reason": reason or "first version",
        "variables": variables,
        "supports": supports,
        "parts": parts,
        **f.part(),
        "materials": {k: mats[k] for k in (TUBE, ROUND, PLATE)},
    }
    write_links(design)
    return design


# ---------------------------------------------------------------- systems and links


def _c(to: str, via: str, carries: str) -> Record:
    return {"to": to, "via": via, "carries": carries}


def links(design: Record) -> dict[str, Record]:
    """{part id: {"system", "connects_to"}}: mounting (load), the deck's hinges and the actuators' pins (load), the
    power chain (power cord -> main switch -> control box -> each actuator), the hand switch's signal, the casters
    to their supports."""
    parts = {p["id"]: p for p in design["parts"]}
    out = {pid: {"system": SYSTEMS.get(p["kind"], "other"), "connects_to": []} for pid, p in parts.items()}

    def add(pid, *conns):
        if pid in out:
            out[pid]["connects_to"].extend(conns)

    hinged = {"deck_back": "deck_seat", "deck_thigh": "deck_seat", "deck_leg": "deck_thigh"}
    for pid, p in parts.items():
        kind = p["kind"]
        if kind == "occupant":
            add(pid, _c("mattress_" + pid.split(".", 1)[1], "rest", "load"))
        elif kind == "mattress":
            add(pid, _c("deck_" + pid.split("_", 1)[1], "rest", "load"))
        elif pid in hinged:
            add(pid, _c(hinged[pid], "pin", "load"))
        elif kind == "actuator" and pid != "actuator_lift":
            tag = pid.split("_", 1)[1]
            add(pid, _c(f"bracket_{tag}", "pin", "load"), _c({"back": "deck_back", "knee": "deck_thigh"}[tag], "pin", "load"))
        elif pid == "actuator_lift":
            add(pid, *(_c(n, "pin", "load") for n in p["carried_by"]))
        elif kind == "side_rail":  # its posts go into the sockets at the upper rail's nodes P1 and K
            add(pid, *(_c(n, "insert", "load") for n in p["carried_by"][:2]))
        elif kind in ("bracket",):
            add(pid, *(_c(n, "weld", "load") for n in p["carried_by"]))
        elif kind == "hand_switch":
            add(pid, _c(f"side_rail_L", "hook", "load"))
        else:
            add(pid, *(_c(n, "bolt", "load") for n in p.get("carried_by") or []))
    for s in design["supports"]:
        add(f"caster_{s['id']}", _c(s["id"], "rest", "load"))
    add("power_cord", _c("main_switch", "wire", "power"))
    add("main_switch", _c("control_box", "wire", "power"))
    add("control_box", *(_c(a, "wire", "power") for a in ("actuator_back", "actuator_knee", "actuator_lift") if a in parts))
    add("hand_switch", _c("control_box", "wire", "signal"))
    return out


def write_links(design: Record) -> None:
    from hiraku.designgen.kit.systems import contract_has_fields

    if not contract_has_fields():
        return
    for pid, rec in links(design).items():
        p = next(q for q in design["parts"] if q["id"] == pid)
        p["system"] = rec["system"]
        if rec["connects_to"]:
            p["connects_to"] = rec["connects_to"]


def power_reach(design: Record) -> set[str]:
    """Parts reached from the power cord along links that carry power (one way, as written)."""
    by = {p["id"]: p for p in design["parts"]}
    seen, todo = {"power_cord"}, ["power_cord"]
    while todo:
        for c in by.get(todo.pop(), {}).get("connects_to", []):
            if c.get("carries") == "power" and c["to"] not in seen:
                seen.add(c["to"])
                todo.append(c["to"])
    return seen


# ---------------------------------------------------------------- the actuator brackets as solid bricks


def bracket_mesh(design: Record, size: float = 3.0, order: int = 1, which: tuple[str, ...] = ("back", "knee")) -> Record:
    """The actuator brackets' lugs as solid bricks: a contract mesh record (element_type solid, mesh_id not set),
    element_member the bracket part's id. Each bracket is two lugs bracket_t thick (in y), bracket_width long (in x),
    from the cross member's underside down to the pin's margin, with the clevis gap between them (no pin hole: the
    pin's load goes in at the nodes around the pin's centre line). size: the brick size (mm); order 1 hex8, 2 hex20."""
    from hiraku.solvers.fe.mesh import box_mesh

    v = {k: q["value"] for k, q in design["variables"].items()}
    parts = {p["id"]: p for p in design["parts"]}
    boxes, names = [], []
    for tag in which:
        p = parts[f"bracket_{tag}"]
        c, s = p["box"]["center_mm"], p["box"]["size_mm"]
        for sy in (1.0, -1.0):
            yc = c[1] + sy * (CLEVIS_GAP_MM + v["bracket_t"]) / 2.0
            boxes.append(([c[0] - s[0] / 2.0, yc - v["bracket_t"] / 2.0, c[2] - s[2] / 2.0],
                          [c[0] + s[0] / 2.0, yc + v["bracket_t"] / 2.0, c[2] + s[2] / 2.0]))
            names.append(p["id"])
    hm = box_mesh(boxes, size, order, names)
    kind = "hex8" if order == 1 else "hex20"
    return {"design_version": int(design.get("design_version", 1)), "element_type": "solid", "element_size_mm": float(size),
            "nodes_mm": [[float(x) for x in n] for n in hm.nodes], "elements": [[int(i) for i in e] for e in hm.conn],
            "element_kind": [kind] * len(hm.conn), "element_member": list(hm.part),
            "notes": "actuator bracket lugs as solid bricks (designgen.care_bed.bracket_mesh); no pin hole"}


__all__ = ["CHOICES", "DESCRIPTION", "DesignGenError", "REQUIREMENTS", "SIGNATURE", "TITLE", "VARIABLES", "applies",
           "apply_choices", "bracket_mesh", "build", "choices_of", "levels", "links", "power_reach",
           "requirement_values", "spec", "stations", "values_of"]

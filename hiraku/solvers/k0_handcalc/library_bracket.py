"""K0: a suspension mount's clevis bracket (load case R9, サスペンション取付): the pin in shear and bearing, the
bracket plates bent at their base, the fillet welds along the plates' bases.

Shape: two plates (a clevis the arm's end passes between) standing on the frame's surface, each fillet-welded on
both sides along its base; the pin (bolt) passes through both plates. The force F the body receives at the mount
point is split by the pin axis u (normal to the plates) and the plates' base line (split_force):

    F_n = F.u                      along the pin: bends the plates out of their plane
    F_p = F - F_n u                in the plates' plane: shears the pin
    F_t = F_p.base                 along the base line
    F_v = F_p.(u x base)           in the plates' plane, normal to the base (pulling the plates off)

d = pin (bolt) diameter, n = pins, t = plate thickness, s = weld leg, a = 0.7 s, b = plate base width, e = height
of the pin centre above the base (the weld). Each plate takes half of every force.

    pin shear (double shear, shank area, threads out of the shear planes)  tau = |F_p| / (2 n pi d^2 / 4)
    bearing (each plate; the frame's wall is not checked)                    sigma_b = |F_p| / (2 n d t)
    plate at its base corner (each plate, the three added: safe side)
        sigma = 6 (|F_n|/2) e / (b t^2) + 6 (|F_t|/2) e / (t b^2) + (|F_v|/2) / (b t)
    weld (each plate: two lines of length b, throat a)
        tau_w = [ (|F|/2) / (2 b) + (|F_n|/2) e / (b t) + 3 (|F_t|/2) e / b^2 ] / a

Resistances, EN 1993-1-8:2005 with its recommended partial factors gamma_M0 = 1.0, gamma_M2 = 1.25:
    f_v = alpha_v f_ub / gamma_M2, alpha_v = 0.6 (Table 3.4, shear plane through the shank; grade 8.8)
    f_b = k1 alpha_b f_u / gamma_M2, with the edge distances at their minimum 1.2 d0 (d0 = d + 2 mm):
          alpha_b = e1 / (3 d0) = 0.4, k1 = 2.8 e2 / d0 - 1.7 = 1.66 (Table 3.4)
    plate: f_y / gamma_M0
    weld:  f_vw = f_u / (sqrt(3) beta_w gamma_M2), beta_w = 0.8 (4.5.3.3 and Table 4.1, the S235 value: STKR400
           taken as S235-like, an assumption)
The utilisations (contract names, unit 1; the criterion would be <= 1) are tau / f_v, sigma_b / f_b,
sigma / (f_y / gamma_M0) and tau_w / f_vw.

Sources (not read in this repository; the values above are the ones agreed with the QA department):
EN 1993-1-8:2005 Table 3.4, 4.5.3.3 and Table 4.1: not checked. ISO 898-1 property class 8.8, nominal tensile
strength 800 MPa: not checked. f_y 245 MPa and f_u 400 MPa of STKR400 from data/materials/materials.json (JIS G
3466, "verified": false).

clevis_utilisations(F, u, base, ...) is the public form for a force vector (the QA department's independent version
is called with the same inputs).
"""

from __future__ import annotations

import math

import numpy as np
import sympy as sp

from .registry import Formula

GAMMA_M0 = 1.0  # EN 1993-1-8:2005 2.2 Note 2 / EN 1993-1-1 6.1 recommended values (not checked)
GAMMA_M2 = 1.25
ALPHA_V = 0.6  # EN 1993-1-8:2005 Table 3.4, grades 4.6, 5.6 and 8.8 (not checked)
F_UB_8_8_MPA = 800.0  # ISO 898-1 property class 8.8, nominal tensile strength (not checked)
HOLE_CLEARANCE_MM = 2.0  # d0 = d + 2 mm (the normal clearance for M16 to M24 in EN 1090-2 Table 11; not checked)
EDGE_FACTOR = 1.2  # e1 = e2 = 1.2 d0: the minimum end and edge distances of EN 1993-1-8:2005 Table 3.3 (not checked)
ALPHA_B = EDGE_FACTOR / 3.0  # e1 / (3 d0) = 0.4
K1 = 2.8 * EDGE_FACTOR - 1.7  # 2.8 e2 / d0 - 1.7 = 1.66
BETA_W = 0.8  # EN 1993-1-8:2005 Table 4.1, S235 (not checked); STKR400 taken as S235-like (assumption)
THROAT = 0.7  # a = 0.7 s (fillet weld of equal legs at 90 degrees)

SOURCE = ("EN 1993-1-8:2005 Table 3.4 (bolt shear alpha_v 0.6, bearing k1 alpha_b with e1 = e2 = 1.2 d0), 4.5.3.3 "
          "and Table 4.1 (simplified fillet weld resistance, beta_w 0.8 for S235), recommended partial factors "
          "gamma_M0 1.0 and gamma_M2 1.25: not checked (not read in this repository; the values agreed with the QA "
          "department); ISO 898-1 property class 8.8 f_ub 800 MPa: not checked; STKR400 f_y 245 and f_u 400 MPa from "
          "data/materials/materials.json (JIS G 3466, not verified); the plate and weld stresses by textbook beam "
          "bending of the plate's base section and of the weld lines")

QUANTITIES = ("bolt_shear_utilisation", "bolt_bearing_utilisation", "plate_bending_utilisation", "weld_utilisation")
FORMULA_OF = {"bolt_shear_utilisation": "mount_bracket.pin_shear",
              "bolt_bearing_utilisation": "mount_bracket.pin_bearing",
              "plate_bending_utilisation": "mount_bracket.plate_bending",
              "weld_utilisation": "mount_bracket.weld"}
INPUTS_OF = {"bolt_shear_utilisation": ("F_t", "F_v", "d", "n", "f_ub"),
             "bolt_bearing_utilisation": ("F_t", "F_v", "d", "n", "t", "f_u"),
             "plate_bending_utilisation": ("F_n", "F_t", "F_v", "t", "b", "e", "f_y"),
             "weld_utilisation": ("F_n", "F_t", "F_v", "t", "s", "b", "e", "f_u")}


# ---------------------------------------------------------------- plain numbers (QA's test calls these)
def split_force(F, u, base) -> dict:
    """{F_n, F_p, F_t, F_v} (N, signed components; F_p the magnitude) of the force F (N, 3) for the pin axis u and
    the plates' base line `base` (both made unit here; base must be normal to u)."""
    F, u, base = (np.asarray(x, float) for x in (F, u, base))
    u = u / float(np.linalg.norm(u))
    base = base / float(np.linalg.norm(base))
    if abs(float(u @ base)) > 1e-6:
        raise ValueError("the plates' base line must be normal to the pin axis")
    Fn = float(F @ u)
    Fp = F - Fn * u
    return {"F_n": Fn, "F_p": float(np.linalg.norm(Fp)), "F_t": float(Fp @ base),
            "F_v": float(Fp @ np.cross(u, base))}


def stresses(F_n: float, F_t: float, F_v: float, d: float, n: int, t: float, s: float | None, b: float | None,
             e: float | None) -> dict:
    """tau (pin), sigma_b (bearing), and with a bracket (s, b, e given) sigma (plate) and tau_w (weld), MPa."""
    Fn, Ft, Fv = abs(F_n), abs(F_t), abs(F_v)
    Fp = math.hypot(Ft, Fv)
    out = {"tau": Fp / (2.0 * n * math.pi * d * d / 4.0), "sigma_b": Fp / (2.0 * n * d * t)}
    if s is not None and b is not None and e is not None:
        F = math.sqrt(Fn * Fn + Fp * Fp)
        out["sigma"] = 6.0 * (Fn / 2.0) * e / (b * t * t) + 6.0 * (Ft / 2.0) * e / (t * b * b) + (Fv / 2.0) / (b * t)
        w = (F / 2.0) / (2.0 * b) + (Fn / 2.0) * e / (b * t) + 3.0 * (Ft / 2.0) * e / (b * b)
        out["tau_w"] = w / (THROAT * s)
    return out


def resistances(f_y: float, f_u: float, f_ub: float = F_UB_8_8_MPA) -> dict:
    """f_v, f_b, f_plate, f_vw (MPa)."""
    return {"f_v": ALPHA_V * f_ub / GAMMA_M2, "f_b": K1 * ALPHA_B * f_u / GAMMA_M2, "f_plate": f_y / GAMMA_M0,
            "f_vw": f_u / (math.sqrt(3.0) * BETA_W * GAMMA_M2)}


def clevis_utilisations(F, u, base, d: float, n: int, t: float, s: float | None, b: float | None, e: float | None,
                        f_y: float = 245.0, f_u: float = 400.0, f_ub: float = F_UB_8_8_MPA) -> dict:
    """The four utilisations (unit 1) for the force vector F (N) at a clevis with pin axis u and base line `base`;
    with s, b or e None (no bracket) only bolt_shear_utilisation and bolt_bearing_utilisation."""
    f = split_force(F, u, base)
    st = stresses(f["F_n"], f["F_t"], f["F_v"], d, n, t, s, b, e)
    r = resistances(f_y, f_u, f_ub)
    out = {"bolt_shear_utilisation": st["tau"] / r["f_v"], "bolt_bearing_utilisation": st["sigma_b"] / r["f_b"]}
    if "sigma" in st:
        out["plate_bending_utilisation"] = st["sigma"] / r["f_plate"]
        out["weld_utilisation"] = st["tau_w"] / r["f_vw"]
    return out


# ---------------------------------------------------------------- the registry's formulas
def _inputs(names) -> dict:
    spec = {
        "F_n": {"unit": "N", "description": "|F.u|: the mount force along the pin (bracket_inputs)", "min": 0},
        "F_t": {"unit": "N", "description": "|the in-plane force along the plates' base line|", "min": 0},
        "F_v": {"unit": "N", "description": "|the in-plane force normal to the base| (pulling the plates off)", "min": 0},
        "d": {"unit": "mm", "description": "pin (bolt) diameter (part.joint.bolt_d_mm)", "min": 0},
        "n": {"unit": "1", "description": "pins carrying the force (part.joint.bolts)", "min": 1},
        "t": {"unit": "mm", "description": "plate thickness (part.joint.plate_t_mm)", "min": 0},
        "s": {"unit": "mm", "description": "fillet weld leg (part.joint.weld_leg_mm; throat 0.7 s)", "min": 0},
        "b": {"unit": "mm", "description": "plate base width (from the bracket's box)", "min": 0},
        "e": {"unit": "mm", "description": "height of the pin centre above the plate's base (from the box and the hard point)",
              "min": 0},
        "f_y": {"unit": "MPa", "description": "plate yield strength (STKR400 taken, materials.json)", "min": 0},
        "f_u": {"unit": "MPa", "description": "plate tensile strength (STKR400 taken, materials.json)", "min": 0},
        "f_ub": {"unit": "MPa", "description": "pin tensile strength (grade 8.8: 800 MPa, ISO 898-1, not checked)", "min": 0},
    }
    return {k: spec[k] for k in names}


_COMMON = [
    "two plates (a clevis) standing on the frame's surface, each fillet-welded on both sides along its base; the pin "
    "through both; each plate takes half of every force",
    "the force is K2's at the mount point (the largest |F| of each upstream analysis; the worst utilisation over the "
    "analyses); the A-arm's force along the arm is split half and half between its two inner pivots by K2",
    "recommended partial factors of EN 1993-1-8 (gamma_M0 1.0, gamma_M2 1.25), not a national annex",
]


def _formula(fid: str, title: str, expr_text: str, names, outputs: dict, assumptions: list[str]) -> Formula:
    return Formula(formula_id=fid, title=title, domain="strength", expression=expr_text, inputs=_inputs(names),
                   outputs=outputs, assumptions=_COMMON + assumptions, source=SOURCE, primary="u",
                   requires=[(sp.Symbol("d", real=True) > 0, "the pin diameter must be positive")] if "d" in names else [])


def _pin_shear() -> Formula:
    Ft, Fv, d, n, f_ub = sp.symbols("F_t F_v d n f_ub", real=True)
    tau = sp.sqrt(Ft**2 + Fv**2) / (2 * n * sp.pi * d**2 / 4)
    fv = sp.Float(ALPHA_V) * f_ub / sp.Float(GAMMA_M2)
    return _formula(
        "mount_bracket.pin_shear", "サスペンションの取付の金具のピンのせん断 (2 面せん断、軸部の断面)",
        "tau = |F_p| / (2 n pi d^2 / 4), |F_p| = sqrt(F_t^2 + F_v^2); f_v = 0.6 f_ub / 1.25; u = tau / f_v",
        ("F_t", "F_v", "d", "n", "f_ub"),
        {"u": ("1", "bolt_shear_utilisation: tau / f_v", tau / fv), "tau": ("MPa", "pin shear stress", tau),
         "f_v": ("MPa", "shear resistance per area alpha_v f_ub / gamma_M2", fv)},
        ["the shear planes pass through the pin's shank (threads out of the shear planes)", "grade 8.8 pin"])


def _pin_bearing() -> Formula:
    Ft, Fv, d, n, t, f_u = sp.symbols("F_t F_v d n t f_u", real=True)
    sb = sp.sqrt(Ft**2 + Fv**2) / (2 * n * d * t)
    fb = sp.Float(K1 * ALPHA_B) * f_u / sp.Float(GAMMA_M2)
    return _formula(
        "mount_bracket.pin_bearing", "サスペンションの取付の金具の板の支圧 (ピンが板の穴を押す)",
        "sigma_b = |F_p| / (2 n d t); f_b = k1 alpha_b f_u / 1.25, alpha_b = 0.4, k1 = 1.66 (e1 = e2 = 1.2 d0); "
        "u = sigma_b / f_b",
        ("F_t", "F_v", "d", "n", "t", "f_u"),
        {"u": ("1", "bolt_bearing_utilisation: sigma_b / f_b", sb / fb), "sigma_b": ("MPa", "bearing stress", sb),
         "f_b": ("MPa", "bearing resistance per area", fb)},
        ["the bracket plates only (the frame's wall is not checked)",
         "the hole's end and edge distances at their minimum 1.2 d0, d0 = d + 2 mm (the smallest alpha_b and k1)"])


def _plate() -> Formula:
    Fn, Ft, Fv, t, b, e, f_y = sp.symbols("F_n F_t F_v t b e f_y", real=True)
    sig = 6 * (Fn / 2) * e / (b * t**2) + 6 * (Ft / 2) * e / (t * b**2) + (Fv / 2) / (b * t)
    fy = f_y / sp.Float(GAMMA_M0)
    return Formula(
        formula_id="mount_bracket.plate_bending", title="サスペンションの取付の金具の板の根元の曲げ (板の外へ・板の面内・引きはがし)",
        domain="strength",
        expression="sigma = 6 (F_n/2) e / (b t^2) + 6 (F_t/2) e / (t b^2) + (F_v/2) / (b t); u = sigma / (f_y / 1.0)",
        inputs=_inputs(("F_n", "F_t", "F_v", "t", "b", "e", "f_y")),
        outputs={"u": ("1", "plate_bending_utilisation: sigma / (f_y / gamma_M0)", sig / fy),
                 "sigma": ("MPa", "the three stresses at the plate's base corner, added", sig)},
        assumptions=_COMMON + ["each plate a cantilever of height e from its welded base, base section b x t",
                               "out-of-plane, in-plane bending and the pull added at the base corner (safe side)",
                               "only where the point has a bracket part (a member end: plate not checked)"],
        source=SOURCE, primary="u")


def _weld() -> Formula:
    Fn, Ft, Fv, t, s, b, e, f_u = sp.symbols("F_n F_t F_v t s b e f_u", real=True)
    F = sp.sqrt(Fn**2 + Ft**2 + Fv**2)
    w = (F / 2) / (2 * b) + (Fn / 2) * e / (b * t) + 3 * (Ft / 2) * e / b**2
    tw = w / (sp.Float(THROAT) * s)
    fvw = f_u / (sp.sqrt(3) * sp.Float(BETA_W) * sp.Float(GAMMA_M2))
    return Formula(
        formula_id="mount_bracket.weld", title="サスペンションの取付の金具の根元のすみ肉溶接",
        domain="strength",
        expression="w = (|F|/2) / (2 b) + (F_n/2) e / (b t) + 3 (F_t/2) e / b^2 (N/mm); tau_w = w / (0.7 s); "
                   "f_vw = f_u / (sqrt(3) 0.8 1.25); u = tau_w / f_vw",
        inputs=_inputs(("F_n", "F_t", "F_v", "t", "s", "b", "e", "f_u")),
        outputs={"u": ("1", "weld_utilisation: tau_w / f_vw", tw / fvw),
                 "tau_w": ("MPa", "the weld's stress on its throat (the line forces added)", tw),
                 "f_vw": ("MPa", "the simplified fillet weld resistance", fvw)},
        assumptions=_COMMON + ["each plate welded on both sides along its base: two lines of length b, throat 0.7 s",
                               "the out-of-plane moment taken by the two lines as a couple t apart; the in-plane "
                               "moment by the lines' bending (b^2 / 6 per line, both lines); the line forces added "
                               "(safe side)",
                               "STKR400 taken as S235-like for beta_w (an assumption)",
                               "only where the point has a bracket part (a member end: weld not checked)"],
        source=SOURCE, primary="u")


def all_formulas() -> list[Formula]:
    return [_pin_shear(), _pin_bearing(), _plate(), _weld()]


__all__ = ["FORMULA_OF", "INPUTS_OF", "QUANTITIES", "all_formulas", "clevis_utilisations", "resistances",
           "split_force", "stresses"]

"""K0: a roll bar (two posts and a top beam) on its feet, loaded as in a roll-over (ROPS) static test (R4).

Three formulas, one per loading, on one model (portal_pushover): the roll bar alone as a small 3D frame whose feet
stand on what holds them, written out as members, not as one spring number:

- the posts (length h) and the top beam (span w, post centre to post centre) with rigid corners, so the top beam
  holds the posts' heads (in the portal's plane by its bending, out of it by its torsion and its bending about the
  vertical) and the posts share the load by their stiffness, whatever the load point;
- each foot on an outrigger (a member of length e from the rail out to the foot; e = 0: the post stands on the
  rail), and the rail as two pieces from the outrigger's root to where the rail is held (L_a ahead, L_b behind;
  fixed there; both 0: the root itself fixed). The feet's springs (the outrigger's bending and twist, the rail's
  twist and bending both ways) and the feet's sideways offset from the rail (the vertical force at a foot bends the
  outrigger with the lever e) come out of the solve;
- plastic hinges at the members' ends, about each bending axis (M_p = Z_p f_y) and in torsion (T_p), forming one by
  one as the load grows (event to event) until the frame is a mechanism; then the load stays at the collapse load.

    frame_static.portal_frame_side: a horizontal force across the vehicle at the top of a post (the left corner),
        pushed until the energy E_in is absorbed (the area under the force-deflection line, which is a polyline:
        elastic, then softer after each hinge, then flat at the collapse load H_c);
    frame_static.portal_frame_rear: a horizontal force along the vehicle (+x) on the top beam, a fraction f of the
        span from the left corner (the near post), until the energy E_in is absorbed;
    frame_static.portal_frame_top: a force down at the middle of the top beam, up to F. When F is above the
        collapse load F_c the beam (or the frame) collapses before F: the small-deflection theory gives no finite
        deflection, and delta is then taken as h (the corners down a post's length, the end of the crushing
        motion of rops_inputs).

Known answers in the tests (closed forms): the fixed-base portal's sway stiffness (24 E I / h^3 with a rigid beam,
6 E I / h^3 with none, 12 E I / h^3 (a + 6b) / (2a + 3b) between), cantilevers on a foot spring in series, the
sway mechanism 4 M_p / h and with the feet offset by e, 2 M_p / h for the rear with both feet hinged, the beam
mechanism 8 M_p / w, and the energy as the area of the force-deflection polyline.

Source: matrix stiffness (slope-deflection) and step-by-step plastic analysis of frames, textbook structural
analysis (W. McGuire, R. H. Gallagher, R. D. Ziemian, Matrix Structural Analysis; B. G. Neal, The Plastic Methods
of Structural Analysis; J. Heyman, Plastic Design of Frames); written out and solved here, the books not re-read
for this; no table values. The inputs come from frame_inputs.portal_frame_inputs.
"""

from __future__ import annotations

import math

import numpy as np

from . import portal_pushover as pp
from .registry import Formula

J_TO_NMM = 1000.0

_GROUP_WORD = {"c": "a post", "b": "the top beam", "o": "an outrigger (unused when e = 0)",
               "r": "the rail at the foot (unused when L_a = L_b = 0)"}
_AXES = {"c": ("sideways, in the portal's plane", "along the vehicle"),
         "b": ("about the vertical (horizontal bending)", "vertical bending, in the portal's plane"),
         "o": ("about the vertical (horizontal bending)", "vertical bending"),
         "r": ("about the vertical (horizontal bending)", "vertical bending")}


def _pos(d: str, unit: str) -> dict:
    return {"unit": unit, "description": d, "min": 0}


def _member_inputs() -> dict:
    out = {}
    for g, word in _GROUP_WORD.items():
        y_ax, z_ax = _AXES[g]
        out.update({
            f"A_{g}": _pos(f"area of {word}", "mm^2"),
            f"Iy_{g}": _pos(f"second moment of {word} about its local y axis (K1 axes: bending {y_ax})", "mm^4"),
            f"Iz_{g}": _pos(f"second moment of {word} about its local z axis (K1 axes: bending {z_ax})", "mm^4"),
            f"J_{g}": _pos(f"torsion constant of {word}", "mm^4"),
            f"Mpy_{g}": _pos(f"plastic moment of {word} about its local y axis (Z_p f_y)", "N*mm"),
            f"Mpz_{g}": _pos(f"plastic moment of {word} about its local z axis (Z_p f_y)", "N*mm"),
            f"Tp_{g}": _pos(f"plastic torque of {word} (2 A_m t f_y / sqrt 3, thin closed tube)", "N*mm"),
        })
    return out


COMMON_INPUTS = {
    "E": _pos("Young's modulus (the roll bar's; the feet and rail are taken with the same)", "MPa"),
    "nu": {"unit": "1", "description": "Poisson's ratio (G = E / (2 (1 + nu)))", "min": 0, "max": 0.5},
    "h": _pos("post length, foot to the top beam's centre line", "mm"),
    "w": _pos("top beam span, post centre to post centre", "mm"),
    "e": {"unit": "mm", "description": "outrigger length: the foot's offset outboard of the rail (0: the post on the "
                                        "rail; negative: inboard)"},
    "L_a": _pos("rail length from the outrigger's root to where the rail is held, ahead (0: none)", "mm"),
    "L_b": _pos("rail length from the outrigger's root to where the rail is held, behind (0: none)", "mm"),
    **_member_inputs(),
}
COMMON = tuple(COMMON_INPUTS)

_ENERGY = _pos("energy the structure must absorb (R4: the load case's side_energy or rear_energy)", "J")
_SOURCE = ("matrix stiffness (slope-deflection) and step-by-step (event-to-event) plastic analysis of frames, textbook "
           "structural analysis (W. McGuire, R. H. Gallagher, R. D. Ziemian, Matrix Structural Analysis; B. G. Neal, "
           "The Plastic Methods of Structural Analysis; J. Heyman, Plastic Design of Frames); written out and solved "
           "here, the books not re-read for this; no table values")
_MODEL = [
    "the roll bar alone: two equal upright posts, a straight top beam and rigid corners; each foot on an outrigger "
    "of length e across the vehicle (none when e = 0) from a rail along the vehicle, the rail held fixed at L_a "
    "ahead and L_b behind the outrigger's root (frame_inputs.portal_frame_inputs: where the chassis is held on its "
    "wheels; the cross members, the other rail and the rest of the chassis between are left out, which makes the "
    "feet softer)",
    "slender 3D beams (no shear deformation), small displacements (no change of geometry)",
    "plastic hinges at member ends when the moment about one bending axis reaches Z_p f_y or the torque reaches the "
    "plastic torque; capacities independent (no interaction of bending, torsion and axial force), no unloading of "
    "hinges, no strain hardening; once a mechanism, the load stays at the collapse load along the mechanism's shape",
]


def _check(v: dict) -> bool:
    need = ["E", "h", "w"] + [f"{k}_{g}" for g in ("c", "b") for k in ("A", "Iy", "Iz", "J")]
    if abs(v["e"]) > 1e-9:
        need += [f"{k}_o" for k in ("A", "Iy", "Iz", "J")]
    if v["L_a"] > 0 or v["L_b"] > 0:
        need += [f"{k}_r" for k in ("A", "Iy", "Iz", "J")]
    return all(v[n] > 0 for n in need)


_REQUIRES = [(_check, "E, h, w and the area, second moments and torsion constant of the posts, the top beam and "
                      "(when used) the outriggers and the rail must be positive")]


def _run(v: dict, kind: str, f: float = 1.0 / 6.0):
    fr, caps, nm = pp.build(v, f)
    watch = [nm["corner_L"], nm["corner_R"], nm["q"], nm["mid"]]
    if kind == "side":
        node, d = nm["corner_L"], np.array([0.0, -1.0, 0.0])
    elif kind == "rear":
        node, d = nm["q"], np.array([1.0, 0.0, 0.0])
    else:
        node, d = nm["mid"], np.array([0.0, 0.0, -1.0])
    res = pp.push(fr, caps, node, d, watch)
    return res, nm, d


def _summary(res: dict) -> dict:
    path = res["path"]
    if len(path) < 2:
        raise ValueError("the frame is a mechanism before any hinge forms (a member or a foot gives no stiffness)")
    En = pp.energy_at(path)
    k0 = path[1][0] / path[1][1]
    F_c, d_c = path[-1]
    return {"k": k0, "first": path[1][0], "collapse": F_c, "delta_y": d_c, "E_y": En[-1] / J_TO_NMM,
            "n_hinges": float(len(res["hinges"])), "n_steps": float(len(path) - 1)}


def _side(v: dict) -> dict:
    res, nm, d = _run(v, "side")
    s = _summary(res)
    at = pp.follow(res, "energy", v["E_in"] * J_TO_NMM)
    uL, uR = (float(at["U"][nm[c]] @ d) for c in ("corner_L", "corner_R"))
    return {"k": s["k"], "H_first": s["first"], "H_c": s["collapse"], "delta_y": s["delta_y"], "E_y": s["E_y"],
            "delta_E": at["d"], "H_E": at["F"], "plastic": 1.0 if at["stage"] > 1 or at["beyond"] else 0.0,
            "u_far_E": uR, "n_hinges": s["n_hinges"], "n_steps": s["n_steps"]}


def _rear(v: dict) -> dict:
    f = v["f"]
    res, nm, d = _run(v, "rear", f)
    s = _summary(res)
    at = pp.follow(res, "energy", v["E_in"] * J_TO_NMM)
    near, far = (float(at["U"][nm[c]] @ d) / at["d"] for c in ("corner_L", "corner_R"))
    sh = res["shape"] or {}
    m_near = float(sh[nm["corner_L"]] @ d) if sh else near
    m_far = float(sh[nm["corner_R"]] @ d) if sh else far
    return {"k": s["k"], "P_first": s["first"], "P_c": s["collapse"], "delta_y": s["delta_y"], "E_y": s["E_y"],
            "delta_E": at["d"], "P_E": at["F"], "plastic": 1.0 if at["stage"] > 1 or at["beyond"] else 0.0,
            "share_near": near, "share_far": far, "mech_near": m_near, "mech_far": m_far,
            "n_hinges": s["n_hinges"], "n_steps": s["n_steps"]}


def _top(v: dict) -> dict:
    res, nm, d = _run(v, "top")
    s = _summary(res)
    F = v["F"]
    at = pp.follow(res, "force", F)
    I_min = min(v["Iy_c"], v["Iz_c"])
    return {"delta": v["h"] if at["beyond"] else at["d"], "delta_at_F_or_collapse": at["d"], "k": s["k"],
            "F_first": s["first"], "F_c": s["collapse"], "delta_c": s["delta_y"],
            "collapsed": 1.0 if at["beyond"] else 0.0, "utilisation": F / s["collapse"],
            "F_squash": 2.0 * v["A_c"] * v["f_y"], "F_euler": 2.0 * math.pi**2 * v["E"] * I_min / (2.0 * v["h"]) ** 2,
            "n_hinges": s["n_hinges"]}


def side(v: dict) -> dict:
    return _side(v)


def rear(v: dict) -> dict:
    return _rear(v)


def top(v: dict) -> dict:
    return _top(v)


_ENERGY_OUT = {
    "delta_y": ("mm", "deflection at the load point when the mechanism forms", None),
    "E_y": ("J", "energy absorbed when the mechanism forms", None),
    "plastic": ("1", "1: E_in is reached after the first hinge", None),
    "n_hinges": ("1", "hinges formed up to the mechanism", None),
    "n_steps": ("1", "steps of the force-deflection polyline up to the mechanism", None),
}


def _portal_side() -> Formula:
    return Formula(
        formula_id="frame_static.portal_frame_side",
        title="ロールバーを足の張り出しとレールに立つ門形の骨組とみて、上の角に横から力 (塑性ヒンジを順に入れ、エネルギーを吸うまでのたわみ)",
        domain="strength",
        expression="3D frame of the roll bar on its outriggers and rail pieces (portal_pushover.build); load across "
                   "the vehicle at the left corner; event-to-event plastic hinges to the mechanism (H_c); delta_E where "
                   "the area under the force-deflection polyline (flat at H_c after the mechanism) is E_in",
        inputs={**COMMON_INPUTS, "E_in": _ENERGY},
        outputs={
            "k": ("N/mm", "elastic sideways stiffness at the loaded corner", None),
            "H_first": ("N", "load at the first plastic hinge", None),
            "H_c": ("N", "collapse load (the mechanism)", None),
            "delta_E": ("mm", "sideways deflection of the loaded corner when E_in has been absorbed", None),
            "H_E": ("N", "force at that deflection", None),
            "u_far_E": ("mm", "the other corner's sideways deflection then", None),
            **_ENERGY_OUT,
        },
        primary="delta_E",
        assumptions=["the side force acts at the left corner, in the portal's plane, across the vehicle", *_MODEL],
        source=_SOURCE,
        requires=_REQUIRES,
        fn=_side,
    )


def _portal_rear() -> Formula:
    return Formula(
        formula_id="frame_static.portal_frame_rear",
        title="ロールバーの上の梁に後ろから力 (両方の柱の分担・塑性ヒンジを順に入れ、エネルギーを吸うまでのたわみ)",
        domain="strength",
        expression="the frame of portal_frame_side; load along the vehicle (+x) on the top beam at f w from the left "
                   "corner; event-to-event plastic hinges to the mechanism (P_c); delta_E where the area under the "
                   "force-deflection polyline is E_in",
        inputs={**COMMON_INPUTS,
                "f": {"unit": "1", "description": "load point's distance from the near (left) post over the span "
                                                  "(R4: 1/6)", "min": 0, "max": 0.5},
                "E_in": _ENERGY},
        outputs={
            "k": ("N/mm", "elastic stiffness at the load point along the vehicle", None),
            "P_first": ("N", "load at the first plastic hinge", None),
            "P_c": ("N", "collapse load (the mechanism)", None),
            "delta_E": ("mm", "deflection at the load point when E_in has been absorbed", None),
            "P_E": ("N", "force at that deflection", None),
            "share_near": ("1", "the near corner's travel along the vehicle per mm of the load point's, at E_in", None),
            "share_far": ("1", "the far corner's travel per mm of the load point's, at E_in", None),
            "mech_near": ("1", "the near corner's travel per mm of the load point's on the mechanism", None),
            "mech_far": ("1", "the far corner's travel per mm of the load point's on the mechanism", None),
            **_ENERGY_OUT,
        },
        primary="delta_E",
        assumptions=["the rear force acts along the vehicle on the top beam's centre line at f w from the near post; "
                     "the top beam carries it to both posts by its bending about the vertical and its torsion and "
                     "the posts' torsion (no rear stays or braces)", *_MODEL],
        source=_SOURCE,
        requires=_REQUIRES,
        fn=_rear,
    )


def _portal_top() -> Formula:
    return Formula(
        formula_id="frame_static.portal_frame_top",
        title="ロールバーの上の梁の真ん中に上から力 (柱の分担・塑性ヒンジを順に入れ、力までのたわみ。崩れれば柱の長さ)",
        domain="strength",
        expression="the frame of portal_frame_side; load down at mid-span of the top beam; event-to-event plastic "
                   "hinges up to F; delta at F, or h when the mechanism forms below F (F_c < F); "
                   "F_squash = 2 A_c f_y; F_euler = 2 pi^2 E min(Iy_c, Iz_c) / (2h)^2",
        inputs={**COMMON_INPUTS,
                "f_y": _pos("yield stress of the posts (for the squash load)", "MPa"),
                "F": _pos("crushing force down at the middle of the top beam (R4: the load case's crush_force)", "N")},
        outputs={
            "delta": ("mm", "deflection down at the middle of the top beam at F (h when the frame collapses below F)",
                      None),
            "delta_at_F_or_collapse": ("mm", "deflection at F, or when the mechanism forms if that is below F", None),
            "k": ("N/mm", "elastic stiffness down at mid-span", None),
            "F_first": ("N", "load at the first plastic hinge", None),
            "F_c": ("N", "collapse load (the mechanism)", None),
            "delta_c": ("mm", "deflection when the mechanism forms", None),
            "collapsed": ("1", "1: F is above the collapse load", None),
            "utilisation": ("1", "F / F_c", None),
            "F_squash": ("N", "both posts' squash load", None),
            "F_euler": ("N", "both posts' Euler load as cantilevers (K = 2, the safe side)", None),
            "n_hinges": ("1", "hinges formed up to the mechanism", None),
        },
        primary="delta",
        assumptions=["the crushing force acts down at the middle of the top beam, in the portal's plane", *_MODEL,
                     "a collapse below F is given as delta = h (unbounded in small-deflection theory; the corners "
                     "taken down a post's length, the end of the crushing motion of rops_inputs); the posts' "
                     "squash and buckling loads are given to compare, not in the path"],
        source=_SOURCE,
        requires=_REQUIRES,
        fn=_top,
    )


def all_formulas() -> list[Formula]:
    return [_portal_side(), _portal_rear(), _portal_top()]

"""K0: a roll bar as a portal frame (two posts and a top beam), loaded as in a roll-over.

Three formulas, one per load of the roll-over (ROPS) static test (R4):

- frame_static.portal_frame_side: a horizontal force H at the top corner in the portal's plane (side loading).
  Elastic sway stiffness by slope-deflection: both top joints turn by theta, both feet by phi (a rotational
  spring of flexibility c_f at each foot; c_f = 0 is a fixed foot; the formulas take it as theta_f, the foot's
  rotation for 1 kN*m in rad, c_f = theta_f / 1e6 per N*mm), the posts sway by Delta (psi = Delta / h):
      post:  M_top = 2a (2 theta + phi - 3 psi),  M_foot = 2a (2 phi + theta - 3 psi),  a = E I_c / h
      beam (antisymmetric, both ends turning theta): M = 6 b theta,  b = E I_b / w
      foot:  M_foot + phi / c_f = 0   (written c_f M_foot + phi = 0, so c_f = 0 is phi = 0)
      joint: M_top + 6 b theta = 0;   H = -2 (M_top + M_foot) / h
  Plastic collapse: the sway mechanism, hinges at both feet (M_pf) and both top corners (M_pt):
      H_c = 2 (M_pf + M_pt) / h.
- frame_static.portal_frame_rear: a horizontal force P along the vehicle (out of the portal's plane) on the
  top beam, a fraction f of the span from one post (the near post). The top beam is taken rigid out of its
  plane and free to turn about the vertical (the posts' torsion is not counted), so it sits on the two posts'
  tops like a beam on two supports: the near post takes (1 - f) P, the far one f P (statics, whatever the
  stiffnesses). Each post is a cantilever on its foot spring: k_post = 1 / (h^3 / (3 E I_c) + h^2 c_f).
      k = k_post / ((1 - f)^2 + f^2)    (at the load point)
      P_c = M_pf / ((1 - f) h)           (the near post's foot hinge; then the beam turns about the far post)
- frame_static.portal_frame_top: a vertical force F down at the middle of the top beam (crushing). The beam's
  ends are held by the posts' rotational stiffness at their tops, k_r = 4a (3a c_f + 1) / (4a c_f + 1)
  (fixed foot: 4a; c_f -> inf: 3a, a pinned foot):
      M_end = F w^2 / (16 E I_b) / (1 / k_r + w / (2 E I_b)),  M_mid = F w / 4 - M_end
      delta = F w^3 / (48 E I_b) - M_end w^2 / (8 E I_b) + (F / 2) h / (E A_c)   (beam bending + posts' shortening)
      F_c = 4 (M_pt + M_pb) / w   (beam mechanism: hinges at both ends and the middle)
  with the posts' squash load 2 A_c f_y and Euler load 2 pi^2 E I_min / (2 h)^2 (each post a cantilever,
  K = 2, the safe side) as checks.

The side and rear formulas also give the deflection at which the required energy E_in is absorbed, on an
elastic, perfectly plastic (bilinear) force-deflection line: elastic up to the collapse load, then flat:
      E_y = F_c^2 / (2 k);  delta_E = sqrt(2 E_in / k) when E_in <= E_y, else F_c / k + (E_in - E_y) / F_c.
The bilinear line is above the true one between the first hinge and the mechanism (the hinges form one by
one), so the deflection is underestimated there; strain hardening and the change of geometry (large
deflection) are left out.

Sources: slope-deflection equations and the plastic hinge (upper bound) method of portal frames, textbook
structural analysis (e.g. J. Heyman, Plastic Design of Frames, Cambridge University Press; B. G. Neal, The
Plastic Methods of Structural Analysis); the equations are written out and solved here, no table values are
used. The plastic moments come from the section's plastic modulus (frame_inputs.portal_frame_inputs).
"""

from __future__ import annotations

import math

import numpy as np

from .registry import Formula

J_TO_NMM = 1000.0


def _bilinear(k: float, F_c: float, E_in_J: float) -> dict:
    """Deflection (mm) and force (N) at which an elastic, perfectly plastic line has absorbed E_in (J)."""
    E = E_in_J * J_TO_NMM
    d_y = F_c / k
    E_y = F_c * d_y / 2.0
    if E <= E_y:
        d = math.sqrt(2.0 * E / k)
        return {"delta_y": d_y, "E_y": E_y / J_TO_NMM, "delta_E": d, "F_E": k * d, "plastic": 0.0}
    return {"delta_y": d_y, "E_y": E_y / J_TO_NMM, "delta_E": d_y + (E - E_y) / F_c, "F_E": F_c, "plastic": 1.0}


def sway(E: float, h: float, w: float, I_c: float, I_b: float, c_f: float, psi: float = 1.0) -> dict:
    """The portal's sway by slope-deflection for a sway angle psi: theta, phi, the post moments and H."""
    a, b = E * I_c / h, E * I_b / w
    # unknowns theta, phi:  joint  (4a + 6b) theta + 2a phi = 6a psi
    #                       foot   2a c_f theta + (4a c_f + 1) phi = 6a c_f psi
    A = np.array([[4.0 * a + 6.0 * b, 2.0 * a], [2.0 * a * c_f, 4.0 * a * c_f + 1.0]])
    rhs = np.array([6.0 * a * psi, 6.0 * a * c_f * psi])
    theta, phi = np.linalg.solve(A, rhs)
    M_top = 2.0 * a * (2.0 * theta + phi - 3.0 * psi)
    M_foot = 2.0 * a * (2.0 * phi + theta - 3.0 * psi)
    H = -2.0 * (M_top + M_foot) / h
    return {"theta": float(theta), "phi": float(phi), "M_top": float(M_top), "M_foot": float(M_foot), "H": float(H)}


def _side(v: dict) -> dict:
    s = sway(v["E"], v["h"], v["w"], v["I_c"], v["I_b"], v["theta_f"] / 1.0e6)
    k = s["H"] / v["h"]  # N/mm: H for Delta = h psi with psi = 1
    per = 1000.0 / s["H"]  # moments for 1 kN
    M_top, M_foot = abs(s["M_top"]) * per, abs(s["M_foot"]) * per
    H_c = 2.0 * (v["M_pf"] + v["M_pt"]) / v["h"]
    firsts = [cap / m * 1000.0 for cap, m in ((v["M_pt"], M_top), (v["M_pf"], M_foot)) if m > 0]
    out = {"k": k, "H_c": H_c, "H_first": min(firsts) if firsts else H_c,
           "M_top_per_kN": M_top, "M_foot_per_kN": M_foot,
           "n_hinges": 2.0 * ((v["M_pf"] > 0) + (v["M_pt"] > 0))}
    b = _bilinear(k, H_c, v["E_in"])
    out.update({"delta_y": b["delta_y"], "E_y": b["E_y"], "delta_E": b["delta_E"], "H_E": b["F_E"], "plastic": b["plastic"]})
    return out


def _rear(v: dict) -> dict:
    E, h, f = v["E"], v["h"], v["f"]
    k_post = 1.0 / (h**3 / (3.0 * E * v["I_c"]) + h**2 * v["theta_f"] / 1.0e6)
    k = k_post / ((1.0 - f) ** 2 + f**2)
    P_c = v["M_pf"] / ((1.0 - f) * h)
    out = {"k": k, "P_c": P_c, "k_post": k_post, "near_share": 1.0 - f, "n_hinges": 1.0}
    b = _bilinear(k, P_c, v["E_in"])
    out.update({"delta_y": b["delta_y"], "E_y": b["E_y"], "delta_E": b["delta_E"], "P_E": b["F_E"], "plastic": b["plastic"]})
    return out


def post_top_stiffness(E: float, h: float, I_c: float, c_f: float) -> float:
    """Rotational stiffness (N*mm/rad) of a post at its top, no sway, foot on a spring of flexibility c_f."""
    a = E * I_c / h
    return 4.0 * a * (3.0 * a * c_f + 1.0) / (4.0 * a * c_f + 1.0)


def _top(v: dict) -> dict:
    E, h, w, F = v["E"], v["h"], v["w"], v["F"]
    EI = E * v["I_b"]
    k_r = post_top_stiffness(E, h, v["I_c"], v["theta_f"] / 1.0e6)
    M_end = F * w**2 / (16.0 * EI) / (1.0 / k_r + w / (2.0 * EI))
    M_mid = F * w / 4.0 - M_end
    d_beam = F * w**3 / (48.0 * EI) - M_end * w**2 / (8.0 * EI)
    d_post = F / 2.0 * h / (E * v["A_c"])
    F_c = 4.0 * (v["M_pt"] + v["M_pb"]) / w
    return {"delta": d_beam + d_post, "delta_beam": d_beam, "delta_posts": d_post, "M_end": M_end, "M_mid": M_mid,
            "N_post": F / 2.0, "F_c": F_c, "F_squash": 2.0 * v["A_c"] * v["f_y"],
            "F_euler": 2.0 * math.pi**2 * E * v["I_min"] / (2.0 * h) ** 2, "utilisation": F / F_c, "n_hinges": 3.0}


def _pos(d: str, unit: str) -> dict:
    return {"unit": unit, "description": d, "min": 0}


_COMMON = {
    "E": _pos("Young's modulus of the roll bar", "MPa"),
    "h": _pos("post length, foot to the top beam's centre line", "mm"),
}
_FOOT = ("rotation of each foot for 1 kN*m about the axis the post bends about (c_f = theta_f / 1e6 per N*mm): 0 fixed; "
         "frame_inputs.portal_frame_inputs gives the outriggers' and the rail's (assumption: the rail held at the "
         "next cross members)")
_ENERGY = _pos("energy the structure must absorb (R4: the load case's side_energy or rear_energy)", "J")
_SOURCE = ("slope-deflection equations and the plastic hinge (upper bound) method of portal frames (textbook structural "
           "analysis, e.g. J. Heyman, Plastic Design of Frames; B. G. Neal, The Plastic Methods of Structural Analysis); "
           "written out and solved here, no table values")
_BILINEAR = ("force-deflection elastic, perfectly plastic (bilinear) up to the energy: the hinges are taken to form "
             "together at the collapse load (the true line is lower between the first hinge and the mechanism), no "
             "strain hardening, small deflections (no change of geometry)")


def _portal_side() -> Formula:
    return Formula(
        formula_id="frame_static.portal_frame_side",
        title="ロールバーを門形の梁とみて、上の角に横から力 (横の剛さ・崩壊の荷重・エネルギーを吸うまでのたわみ)",
        domain="strength",
        expression="c_f = theta_f/1e6; slope-deflection: (4a + 6b) theta + 2a phi = 6a psi, 2a c_f theta + (4a c_f + 1) phi = 6a c_f psi, "
                   "a = E I_c/h, b = E I_b/w; H = -2 (M_top + M_foot)/h, k = H/(h psi); H_c = 2 (M_pf + M_pt)/h; "
                   "delta_E = sqrt(2 E_in/k) if E_in <= H_c^2/(2k) else H_c/k + (E_in - H_c^2/(2k))/H_c",
        inputs={
            **_COMMON,
            "w": _pos("top beam span, post centre to post centre", "mm"),
            "I_c": _pos("second moment of a post for bending in the portal's plane", "mm^4"),
            "I_b": _pos("second moment of the top beam for bending in the portal's plane", "mm^4"),
            "theta_f": _pos(_FOOT, "rad"),
            "M_pf": _pos("plastic moment at a foot (the smaller of the post's and what holds the foot)", "N*mm"),
            "M_pt": _pos("plastic moment at a top corner (the smaller of the post's and the beam's)", "N*mm"),
            "E_in": _ENERGY,
        },
        outputs={
            "k": ("N/mm", "elastic sideways stiffness at the top", None),
            "H_c": ("N", "plastic collapse load (sway mechanism, hinges at both feet and both top corners)", None),
            "H_first": ("N", "load at which the first hinge section reaches its plastic moment (elastic moments)", None),
            "delta_y": ("mm", "deflection at the collapse load on the elastic line (H_c / k)", None),
            "E_y": ("J", "energy absorbed up to the collapse load on the elastic line", None),
            "delta_E": ("mm", "sideways deflection at the top when E_in has been absorbed (bilinear)", None),
            "H_E": ("N", "force at that deflection", None),
            "plastic": ("1", "1: E_in is reached on the plastic part of the line", None),
            "M_top_per_kN": ("N*mm", "elastic moment at a top corner per kN of H", None),
            "M_foot_per_kN": ("N*mm", "elastic moment at a foot per kN of H", None),
            "n_hinges": ("1", "plastic hinges in the mechanism", None),
        },
        primary="delta_E",
        assumptions=[
            "the roll bar is a plane portal: two equal straight posts and a straight top beam, rigid corners",
            "the side force acts at the height of the top beam's centre line, in the portal's plane",
            "each foot is a rotational spring (c_f = theta_f / 1e6) and does not move sideways or up (the rail and outrigger "
            "deflections other than the rotation are left out)",
            "axial shortening of the members and shear deformation are left out (thin tubes, slender members)",
            _BILINEAR,
        ],
        source=_SOURCE,
        requires=[(lambda v: v["h"] > 0 and v["w"] > 0 and v["I_c"] > 0 and v["I_b"] > 0 and v["E"] > 0,
                   "h, w, I_c, I_b and E must be positive"),
                  (lambda v: v["M_pf"] + v["M_pt"] > 0, "the mechanism needs a plastic moment at the feet or the corners")],
        fn=_side,
    )


def _portal_rear() -> Formula:
    return Formula(
        formula_id="frame_static.portal_frame_rear",
        title="ロールバーの上の梁に後ろから力 (前後の剛さ・崩壊の荷重・エネルギーを吸うまでのたわみ)",
        domain="strength",
        expression="c_f = theta_f/1e6; k_post = 1/(h^3/(3 E I_c) + h^2 c_f); k = k_post/((1 - f)^2 + f^2); P_c = M_pf/((1 - f) h); "
                   "delta_E bilinear as portal_frame_side",
        inputs={
            **_COMMON,
            "f": {"unit": "1", "description": "load point's distance from the near post over the span (R4: 1/6 from "
                                              "the outer corner)", "min": 0, "max": 0.5},
            "I_c": _pos("second moment of a post for bending along the vehicle (out of the portal's plane)", "mm^4"),
            "theta_f": _pos(_FOOT, "rad"),
            "M_pf": _pos("plastic moment at the near foot for bending along the vehicle (the smaller of the post's "
                         "and what holds the foot)", "N*mm"),
            "E_in": _ENERGY,
        },
        outputs={
            "k": ("N/mm", "elastic stiffness at the load point along the vehicle", None),
            "P_c": ("N", "collapse load (the near post's foot hinge)", None),
            "k_post": ("N/mm", "one post as a cantilever on its foot spring", None),
            "near_share": ("1", "share of the load the near post takes (1 - f)", None),
            "delta_y": ("mm", "deflection at the collapse load on the elastic line", None),
            "E_y": ("J", "energy absorbed up to the collapse load on the elastic line", None),
            "delta_E": ("mm", "deflection at the load point when E_in has been absorbed (bilinear)", None),
            "P_E": ("N", "force at that deflection", None),
            "plastic": ("1", "1: E_in is reached on the plastic part of the line", None),
            "n_hinges": ("1", "plastic hinges in the mechanism", None),
        },
        primary="delta_E",
        assumptions=[
            "each post a cantilever on its foot spring, bending along the vehicle",
            "the top beam rigid out of the portal's plane and free to turn about the vertical: the posts' torsion and "
            "the beam's twist give the posts' tops no fixity (on the safe side), so the load splits by the lever rule",
            "no rear stays or braces",
            _BILINEAR,
        ],
        source=_SOURCE,
        requires=[(lambda v: v["h"] > 0 and v["I_c"] > 0 and v["E"] > 0 and v["M_pf"] > 0,
                   "h, I_c, E and M_pf must be positive")],
        fn=_rear,
    )


def _portal_top() -> Formula:
    return Formula(
        formula_id="frame_static.portal_frame_top",
        title="ロールバーの上の梁の真ん中に上から力 (梁の曲げと柱の圧縮)",
        domain="strength",
        expression="c_f = theta_f/1e6; k_r = 4a (3a c_f + 1)/(4a c_f + 1), a = E I_c/h; M_end = F w^2/(16 E I_b)/(1/k_r + w/(2 E I_b)); "
                   "M_mid = F w/4 - M_end; delta = F w^3/(48 E I_b) - M_end w^2/(8 E I_b) + F h/(2 E A_c); "
                   "F_c = 4 (M_pt + M_pb)/w; F_squash = 2 A_c f_y; F_euler = 2 pi^2 E I_min/(2h)^2",
        inputs={
            **_COMMON,
            "w": _pos("top beam span, post centre to post centre", "mm"),
            "I_c": _pos("second moment of a post for bending in the portal's plane", "mm^4"),
            "I_b": _pos("second moment of the top beam for bending in the portal's plane", "mm^4"),
            "I_min": _pos("smallest second moment of a post (for its buckling)", "mm^4"),
            "A_c": _pos("area of a post", "mm^2"),
            "theta_f": _pos(_FOOT, "rad"),
            "M_pb": _pos("plastic moment of the top beam", "N*mm"),
            "M_pt": _pos("plastic moment at a top corner (the smaller of the post's and the beam's)", "N*mm"),
            "f_y": _pos("yield stress of the posts", "MPa"),
            "F": _pos("crushing force down at the middle of the top beam (R4: the load case's crush_force)", "N"),
        },
        outputs={
            "delta": ("mm", "elastic deflection down at the middle of the top beam (beam bending and posts' shortening)",
                      None),
            "delta_beam": ("mm", "of it, the top beam's bending", None),
            "delta_posts": ("mm", "of it, the posts' shortening", None),
            "M_end": ("N*mm", "elastic moment at the beam's ends (the top corners)", None),
            "M_mid": ("N*mm", "elastic moment at the middle of the beam", None),
            "N_post": ("N", "compression in each post", None),
            "F_c": ("N", "plastic collapse load of the top beam (hinges at both ends and the middle)", None),
            "F_squash": ("N", "both posts' squash load", None),
            "F_euler": ("N", "both posts' Euler load as cantilevers (K = 2, the safe side)", None),
            "utilisation": ("1", "F / F_c (1 or more: the beam collapses before F is reached)", None),
            "n_hinges": ("1", "plastic hinges in the beam mechanism", None),
        },
        primary="delta",
        assumptions=[
            "the crushing force acts in the portal's plane at the middle of the top beam (R4: the resultant in the "
            "vehicle's median plane)",
            "symmetric: the corners do not sway; each post holds the beam's end with its rotational stiffness at the "
            "top, its foot on a spring (c_f = theta_f / 1e6)",
            "elastic deflection and moments; the collapse load from the beam mechanism alone (the posts' squash and "
            "buckling loads are given to compare)",
        ],
        source=_SOURCE,
        requires=[(lambda v: min(v["h"], v["w"], v["I_c"], v["I_b"], v["A_c"], v["E"]) > 0,
                   "h, w, I_c, I_b, A_c and E must be positive")],
        fn=_top,
    )


def all_formulas() -> list[Formula]:
    return [_portal_side(), _portal_rear(), _portal_top()]

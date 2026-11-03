"""K0 formulas for a care bed (hiraku.designgen.care_bed): the deck frame under a uniform load, tipping under an
edge load, the side rail pushed outward, the actuators' levers and brackets, the lift, the actuators' duty and
the fatigue damage over the counts of use.

The inputs are made from the design by functions in hiraku.catalog.builders.care_bed (inputs_from).
"""

from __future__ import annotations

import math

import numpy as np
import sympy as sp

from .registry import Formula

THETA_STEPS = 721  # angles scanned between 0 and the largest raise (the force's largest is between two of them)


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def _rect_I(b: float, h: float, t: float) -> float:
    """Second moment of a rectangular tube about the axis across h (bending in the plane of h), mm^4."""
    return (b * h**3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12.0


def _rect_A(b: float, h: float, t: float) -> float:
    return b * h - (b - 2 * t) * (h - 2 * t)


# ---------------------------------------------------------------- the deck frame: a closed portal in slope-deflection


def _deck_frame(v: dict) -> dict:
    """Upper rail (span L between the posts, overhangs a1 at the head and a2 at the foot, uniform w = W / (a1 + L +
    a2)), two posts of length h, the base rail of span L, joined rigidly at the four corners; no sway. Slope-
    deflection with the four joint rotations; member-end moments clockwise positive."""
    E, L, a1, a2, h, W = (v[k] for k in ("E", "L", "a1", "a2", "h", "W"))
    I_r = _rect_I(v["b_r"], v["h_r"], v["t_r"])
    I_p = _rect_I(v["b_p"], v["b_p"], v["t_p"])
    I_g = _rect_I(v["b_g"], v["h_g"], v["t_g"])
    w = W / (a1 + L + a2)
    kr, kp, kg = 2 * E * I_r / L, 2 * E * I_p / h, 2 * E * I_g / L
    fem = w * L**2 / 12.0
    m1o, m2o = w * a1**2 / 2.0, -w * a2**2 / 2.0
    # unknowns th1 (top head), th2 (top foot), th3 (bottom head), th4 (bottom foot)
    K = np.array([[2 * kr + 2 * kp, kr, kp, 0.0],
                  [kr, 2 * kr + 2 * kp, 0.0, kp],
                  [kp, 0.0, 2 * kp + 2 * kg, kg],
                  [0.0, kp, kg, 2 * kp + 2 * kg]])
    rhs = -np.array([-fem + m1o, fem + m2o, 0.0, 0.0])
    th = np.linalg.solve(K, rhs)
    M12 = kr * (2 * th[0] + th[1]) - fem
    M21 = kr * (2 * th[1] + th[0]) + fem
    M13, M31 = kp * (2 * th[0] + th[2]), kp * (2 * th[2] + th[0])
    M24, M42 = kp * (2 * th[1] + th[3]), kp * (2 * th[3] + th[1])
    x = np.linspace(0.0, L, 401)
    # internal moment, sagging positive: the clockwise end moment M12 is the internal moment at the left end, -M21 at the right
    span = w * x * (L - x) / 2.0 + M12 * (1 - x / L) - M21 * (x / L)
    M_rail = max(abs(M12), abs(M21), abs(m1o), abs(m2o), float(np.max(np.abs(span))))
    M_post = max(abs(M13), abs(M31), abs(M24), abs(M42))
    # the posts' axial loads: the rail's reactions (beam statics with the end moments)
    R1 = w * a1 + w * L / 2.0 - (M12 + M21) / L
    R2 = W - R1
    A_p = _rect_A(v["b_p"], v["b_p"], v["t_p"])
    Z_r, Z_p = 2 * I_r / v["h_r"], 2 * I_p / v["b_p"]
    s_rail = M_rail / Z_r
    s_post = M_post / Z_p + max(R1, R2) / A_p
    return {"sigma": max(s_rail, s_post), "sigma_rail": s_rail, "sigma_post": s_post, "M_rail": M_rail,
            "M_post": M_post, "R_head": R1, "R_foot": R2}


def _deck_frame_formula() -> Formula:
    tube = lambda what: {f"b_{what[0]}": _in("mm", f"{what[1]} tube width", min=0),  # noqa: E731
                         f"t_{what[0]}": _in("mm", f"{what[1]} tube wall", min=0)}
    inputs = {"W": _in("N", "load on one upper rail (its share of the deck's load and the rail's own weight)", min=0),
              "L": _in("mm", "post to post along the rail", min=0),
              "a1": _in("mm", "rail beyond the head posts", min=0), "a2": _in("mm", "rail beyond the foot posts", min=0),
              "h": _in("mm", "post length, base rail centre to upper rail centre", min=0),
              "E": _in("MPa", "Young's modulus", min=0),
              **tube(("r", "upper rail")), "h_r": _in("mm", "upper rail tube height", min=0),
              **tube(("p", "post (square)")),
              **tube(("g", "base rail")), "h_g": _in("mm", "base rail tube height", min=0)}
    names = list(inputs)
    return Formula(
        formula_id="bed.deck_frame_udl",
        title="ベッドの床の枠: 上のレール・柱・下のレールの閉じた枠に一様な荷重 (たわみ角法)",
        domain="strength",
        expression=("w = W / (a1 + L + a2); FEM = w L^2 / 12; overhang moments w a1^2 / 2, w a2^2 / 2; "
                    "M_ij = (2 E I / L)(2 th_i + th_j) + FEM_ij for the upper rail, the posts and the base rail; "
                    "joint equilibrium at the 4 corners (no sway) -> th; sigma = max(M_rail / Z_r, M_post / Z_p + R / A_p)"),
        inputs=inputs,
        outputs={"sigma": ("MPa", "largest bending stress of the rail and the posts (the larger)", None),
                 "sigma_rail": ("MPa", "upper rail's largest bending stress", None),
                 "sigma_post": ("MPa", "post's largest bending plus axial stress", None),
                 "M_rail": ("N*mm", "upper rail's largest moment", None),
                 "M_post": ("N*mm", "post's largest moment", None),
                 "R_head": ("N", "head post's axial load", None), "R_foot": ("N", "foot post's axial load", None)},
        assumptions=["the load is uniform along the upper rail (the weights spread along the deck)",
                     "one side of the bed as a plane frame: the cross members and the other side are left out",
                     "joints rigid, no sway of the posts", "Euler-Bernoulli beams, bending only (the posts' axial "
                     "stress added)"],
        source="slope-deflection method for rigid plane frames (textbook structural analysis)",
        requires=[(lambda v: v["L"] > 0 and v["h"] > 0 and 2 * v["t_r"] < min(v["b_r"], v["h_r"])
                   and 2 * v["t_p"] < v["b_p"] and 2 * v["t_g"] < min(v["b_g"], v["h_g"]),
                   "L and h positive; every tube's wall less than half of its sides")],
        fn=lambda v: _deck_frame({k: v[k] for k in names}),
    )


# ---------------------------------------------------------------- the actuators' levers


def lever_force(M0: float, r: float, a_x: float, a_z: float, theta_max_deg: float, steps: int = THETA_STEPS) -> dict:
    """The actuator force that holds a board raised by theta (0 .. theta_max) about its hinge.

    Plane of the hinge (origin), x along the board at the flat position, z up. The load's moment about the hinge is
    M0 cos(theta) (the board's weight and its load act down at a fixed distance along the board). A lever of length
    r hangs at a right angle to the board (its tip at r (sin theta, -cos theta)); the actuator runs from the anchor
    (-a_x, -a_z) to the tip. Force F = M / d, d the hinge's distance from the actuator's line."""
    th = np.radians(np.linspace(0.0, theta_max_deg, steps))
    tip = np.stack([r * np.sin(th), -r * np.cos(th)], axis=1)
    anchor = np.array([-a_x, -a_z])
    e = tip - anchor
    length = np.linalg.norm(e, axis=1)
    e = e / length[:, None]
    d = np.abs(tip[:, 0] * e[:, 1] - tip[:, 1] * e[:, 0])
    if np.any(d <= 1e-9 * r):
        raise ValueError("the actuator's line passes through the hinge at some angle: it cannot raise the board there")
    F = M0 * np.cos(th) / d
    k = int(np.argmax(F))
    return {"F_max": float(F[k]), "theta_at_max": float(np.degrees(th[k])), "F_flat": float(F[0]),
            "stroke": float(length[-1] - length[0]), "length_flat": float(length[0])}


def _lever_formula() -> Formula:
    return Formula(
        formula_id="bed.lever_actuator_force",
        title="背上げ・膝上げのアクチュエーターの力 (板をちょうつがいのまわりに起こす腕)",
        domain="powertrain",
        expression=("M(theta) = M0 cos(theta); tip = r (sin theta, -cos theta); anchor (-a_x, -a_z); "
                    "d = |tip x e|, e the unit vector anchor -> tip; F = M / d, the largest over 0 .. theta_max; "
                    "stroke = |tip(theta_max) - anchor| - |tip(0) - anchor|"),
        inputs={"M0": _in("N*mm", "moment of the board and its load about the hinge, flat", min=0),
                "r": _in("mm", "lever length (hinge to the actuator's pin, at right angles to the board)", min=0),
                "a_x": _in("mm", "anchor's distance from the hinge along the frame, on the side the actuator pushes from",
                           min=0),
                "a_z": _in("mm", "anchor below the hinge"),
                "theta_max": _in("deg", "largest raise", min=0, max=89)},
        outputs={"F_max": ("N", "largest actuator force over the raise", None),
                 "theta_at_max": ("deg", "raise angle of the largest force", None),
                 "F_flat": ("N", "actuator force at the flat position (start of the raise)", None),
                 "stroke": ("mm", "actuator stroke used for the raise", None),
                 "length_flat": ("mm", "pin-to-pin length at the flat position", None)},
        assumptions=["quasi-static (no acceleration, no friction in the hinge and the pins)",
                     "the load stays where it is on the board as it rises (it turns with the board)",
                     f"scanned at {THETA_STEPS} angles"],
        source="moment balance of a lever about its hinge (textbook statics)",
        requires=[(lambda v: v["r"] > 0 and v["a_x"] > 0, "r and a_x must be positive")],
        fn=lambda v: lever_force(v["M0"], v["r"], v["a_x"], v["a_z"], v["theta_max"]),
    )


# ---------------------------------------------------------------- everything else


def all_formulas() -> list[Formula]:
    out = [_deck_frame_formula(), _lever_formula()]
    s = {n: sp.Symbol(n, real=True) for n in ("W", "y_cg", "F", "y_F", "y_s")}
    W, ycg, F, yF, ys = (s[k] for k in ("W", "y_cg", "F", "y_F", "y_s"))
    R_far = (W * (ys - ycg) - F * (yF - ys)) / (2 * ys)
    out.append(Formula(
        formula_id="bed.edge_load_tipping",
        title="ベッドの側端に下向きの力をかけたときの、反対側のキャスターに残る力 (倒れ)",
        domain="handling",
        expression="R_far = (W (y_s - y_cg) - F (y_F - y_s)) / (2 y_s); R_near = W + F - R_far",
        inputs={"W": _in("N", "weight of the bed and what is on it (without the edge load)", min=0),
                "y_cg": _in("mm", "centre of gravity's offset toward the loaded side"),
                "F": _in("N", "edge load, straight down", min=0),
                "y_F": _in("mm", "edge load's offset from the centre line, on the loaded side", min=0),
                "y_s": _in("mm", "caster line's offset from the centre line (half the caster track)", min=0)},
        outputs={"R_far": ("N", "load left on the casters of the far side (both together); below 0 the bed tips", R_far),
                 "R_near": ("N", "load on the casters of the loaded side (both together)", W + F - R_far)},
        assumptions=["rigid bed on a level floor; the casters' contact points on two lines along the bed",
                     "the load and the weight act straight down"],
        source="moment balance about the loaded side's caster line (textbook statics)",
        requires=[(ys > 0, "y_s must be positive")],
        symbols=s,
    ))
    s = {n: sp.Symbol(n, real=True) for n in ("F", "H", "S", "d", "t")}
    F, H, S, d, t = (s[k] for k in ("F", "H", "S", "d", "t"))
    I = sp.pi * (d**4 - (d - 2 * t) ** 4) / 64
    Z = 2 * I / d
    out.append(Formula(
        formula_id="bed.side_rail_push",
        title="サイドレールの上の棒の真ん中を外へ押したときの柱の根元と棒の曲げ",
        domain="strength",
        expression="Z = pi (d^4 - (d - 2t)^4) / (32 d); sigma_post = (F / 2) H / Z; sigma_bar = F S / 8 / Z",
        inputs={"F": _in("N", "outward push at the top bar's middle", min=0),
                "H": _in("mm", "post length, socket to top bar", min=0),
                "S": _in("mm", "top bar span, post to post", min=0),
                "d": _in("mm", "round tube outside diameter", min=0), "t": _in("mm", "tube wall", min=0)},
        outputs={"sigma": ("MPa", "the larger of the two", sp.Max(F / 2 * H / Z, F * S / 8 / Z)),
                 "sigma_post": ("MPa", "post's bending at its foot", F / 2 * H / Z),
                 "sigma_bar": ("MPa", "top bar's bending (its ends taken as fixed)", F * S / 8 / Z)},
        assumptions=["the two posts share the push equally and bend as cantilevers from their sockets",
                     "the sockets and the upper rail do not turn", "the top bar's ends are held by the posts (fixed)"],
        source="cantilever and fixed-fixed beam bending (textbook)",
        requires=[(sp.And(2 * t < d, d > 0), "t must be less than half of d")],
        symbols=s,
    ))
    s = {n: sp.Symbol(n, real=True) for n in ("F", "h_p", "w", "t", "d")}
    F, hp, w, t, d = (s[k] for k in ("F", "h_p", "w", "t", "d"))
    out.append(Formula(
        formula_id="bed.lug_bending",
        title="アクチュエーターの金具 (2 枚の耳) の根元の曲げとピンの面圧",
        domain="strength",
        expression="each lug F / 2 at the pin, h_p below its root: sigma_root = (F / 2) h_p / (t w^2 / 6) = 3 F h_p / (t w^2); "
                   "sigma_bearing = F / (2 d t)",
        inputs={"F": _in("N", "actuator force along the actuator (horizontal at the flat position)", min=0),
                "h_p": _in("mm", "pin centre below the lug's root (the cross member's underside)", min=0),
                "w": _in("mm", "lug width (along the force)", min=0), "t": _in("mm", "lug thickness", min=0),
                "d": _in("mm", "pin diameter", min=0)},
        outputs={"sigma_root": ("MPa", "bending stress at the lug's root", 3 * F * hp / (t * w**2)),
                 "sigma_bearing": ("MPa", "pin bearing stress", F / (2 * d * t))},
        assumptions=["each lug a cantilever from its root, loaded at the pin by half the force",
                     "the force is along the lug's width (the actuator lies along x at the flat position)",
                     "no hole: the section at the root is the full lug"],
        source="cantilever bending, bearing pressure on the projected area (textbook machine design)",
        requires=[(sp.And(w > 0, t > 0, d > 0), "w, t and d must be positive")],
        symbols=s,
    ))
    s = {n: sp.Symbol(n, real=True) for n in ("W", "ratio")}
    out.append(Formula(
        formula_id="bed.lift_actuator_force",
        title="高さのアクチュエーターの力",
        domain="powertrain",
        expression="F = ratio W",
        inputs={"W": _in("N", "weight on the lift (the upper frame, the deck and its load)", min=0),
                "ratio": _in("1", "lift linkage's ratio: actuator force per newton on the lift", min=0)},
        outputs={"F": ("N", "lift actuator force", s["ratio"] * s["W"])},
        assumptions=["the lift linkage's ratio is constant over its travel (the linkage is not modelled)"],
        source="definition of the ratio (arithmetic)",
        symbols=s,
    ))
    s = {n: sp.Symbol(n, real=True) for n in ("n", "k", "s", "v")}
    n, k, st, vel = (s[x] for x in ("n", "k", "s", "v"))
    t_stroke = st / 1000 / vel
    out.append(Formula(
        formula_id="bed.actuator_duty",
        title="アクチュエーターの使う割合 (いちばん忙しい 1 時間)",
        domain="thermal",
        expression="t_stroke = s / v; on_time = n k t_stroke; duty = on_time / 3600 s",
        inputs={"n": _in("1", "uses in the busiest hour", min=0), "k": _in("1", "strokes per use (up and down: 2)", min=0),
                "s": _in("mm", "stroke used", min=0), "v": _in("m/s", "stroke speed", min=0)},
        outputs={"duty": ("%", "share of the hour the actuator runs", 100 * n * k * t_stroke / 3600),
                 "on_time": ("s", "time it runs in the hour", n * k * t_stroke),
                 "run_time": ("s", "longest continuous run (one stroke)", t_stroke)},
        assumptions=["each stroke at the rated speed", "the hour is the busiest one: every use of the day in it"],
        source="time = stroke / speed (arithmetic)",
        requires=[(vel > 0, "v must be positive")],
        symbols=s,
    ))
    names = ("n1", "dS1", "n2", "dS2", "n3", "dS3", "n4", "dS4", "FAT", "m", "N_knee", "m2")

    def damage(v: dict) -> dict:
        out_d = {}
        knee = v["FAT"] * (2e6 / v["N_knee"]) ** (1.0 / v["m"])  # the range at the knee
        for i in "1234":
            ds, cycles = v[f"dS{i}"], v[f"n{i}"]
            if ds <= 0 or cycles <= 0:
                out_d[f"D{i}"] = 0.0
                continue
            N = 2e6 * (v["FAT"] / ds) ** v["m"] if ds >= knee else v["N_knee"] * (knee / ds) ** v["m2"]
            out_d[f"D{i}"] = cycles / N
        out_d["D"] = sum(out_d[f"D{i}"] for i in "1234")
        return out_d

    out.append(Formula(
        formula_id="bed.use_damage",
        title="使う回数ごとの応力の幅から、溶接の疲労の損傷 (マイナー則、4 つの使い方の和)",
        domain="strength",
        expression="N_i = 2e6 (FAT / dS_i)^m above the knee, N_knee (dS_knee / dS_i)^m2 below; D = sum n_i / N_i",
        inputs={**{f"n{i}": _in("1", f"count of use {i}", min=0) for i in "1234"},
                **{f"dS{i}": _in("MPa", f"stress range of one use {i}", min=0) for i in "1234"},
                "FAT": _in("MPa", "fatigue class: range at 2e6 cycles", min=0), "m": _in("1", "slope", min=0),
                "N_knee": _in("1", "cycles at the knee", min=0), "m2": _in("1", "slope beyond the knee", min=0)},
        outputs={"D": ("1", "Miner damage of all four uses (failure at 1)", None),
                 **{f"D{i}": ("1", f"damage of use {i}", None) for i in "1234"}},
        assumptions=["each use is one cycle of its stress range (0 to the loaded stress)",
                     "IIW-type S-N curve (no mean stress correction)", "linear damage accumulation (Miner)"],
        source=("S-N curve N = 2e6 (FAT / range)^m and Miner's rule: A. F. Hobbacher, Recommendations for Fatigue "
                "Design of Welded Joints and Components, 2nd ed., Springer 2016 (IIW); not checked in this repository"),
        requires=[(lambda v: v["FAT"] > 0 and v["m"] > 0 and v["N_knee"] > 0 and v["m2"] > 0,
                   "FAT, m, N_knee and m2 must be positive")],
        primary="D",
        terms={f"D{i}": None for i in "1234"},
        fn=damage,
    ))
    return out


__all__ = ["all_formulas", "lever_force"]

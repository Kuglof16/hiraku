"""K0 formulas for natural frequencies and excitation."""

from __future__ import annotations

import sympy as sp

from .registry import Formula


def _sym(*names: str) -> dict[str, sp.Symbol]:
    return {n: sp.Symbol(n, real=True) for n in names}


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


_RECT_TUBE = {
    "b": _in("mm", "tube width", min=0),
    "h": _in("mm", "tube height (in the plane of vibration)", min=0),
    "t": _in("mm", "wall thickness", min=0),
}


def all_formulas() -> list[Formula]:
    out = []
    s = _sym("k", "m")
    k, m = s["k"], s["m"]
    out.append(Formula(
        formula_id="modal.spring_mass",
        title="ばねと質量の固有振動数",
        domain="nvh",
        expression="f = sqrt(k/m) / (2 pi)",
        inputs={"k": _in("N/mm", "spring stiffness", min=0), "m": _in("kg", "mass on the spring", min=0)},
        outputs={"f": ("Hz", "natural frequency", sp.sqrt(k * 1000 / m) / (2 * sp.pi))},
        assumptions=["one degree of freedom", "linear spring, no damping"],
        source="single degree of freedom oscillator (textbook vibration)",
        requires=[(m > 0, "m must be positive")],
        symbols=s,
    ))
    s = _sym("k_s", "k_t", "m_s")
    ks, kt, ms = s["k_s"], s["k_t"], s["m_s"]
    k_ride = ks * kt / (ks + kt)
    out.append(Formula(
        formula_id="modal.quarter_car",
        title="1/4 車両の上下の固有振動数 (乗り心地の振動数)",
        domain="nvh",
        expression="k_ride = k_s k_t / (k_s + k_t); f = sqrt(k_ride / m_s) / (2 pi)",
        inputs={"k_s": _in("N/mm", "suspension rate at the wheel", min=0),
                "k_t": _in("N/mm", "tyre vertical stiffness", min=0),
                "m_s": _in("kg", "sprung mass on this corner", min=0)},
        outputs={"f": ("Hz", "body bounce frequency of the corner", sp.sqrt(k_ride * 1000 / ms) / (2 * sp.pi)),
                 "k_ride": ("N/mm", "ride rate (suspension and tyre in series)", k_ride)},
        assumptions=["unsprung mass small next to the sprung mass", "linear springs, no damping"],
        source="quarter-car model, springs in series (textbook vehicle dynamics)",
        requires=[(sp.And(ks > 0, kt > 0, ms > 0), "k_s, k_t and m_s must be positive")],
        symbols=s,
    ))
    s = _sym("E", "rho", "L", "b", "h", "t")
    E, rho, L, b, h, t = (s[x] for x in ("E", "rho", "L", "b", "h", "t"))
    I = (b * h**3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12 * 1e-12  # m^4
    A = (b * h - (b - 2 * t) * (h - 2 * t)) * 1e-6  # m^2
    Lm = L / 1000
    out.append(Formula(
        formula_id="modal.simple_beam_first_mode",
        title="両端を支えた角パイプの梁の 1 次の曲げの固有振動数",
        domain="nvh",
        expression="f1 = (pi / (2 L^2)) sqrt(E I / (rho A))",
        inputs={"E": _in("MPa", "Young's modulus", min=0), "rho": _in("kg/m^3", "density", min=0),
                "L": _in("mm", "span between the supports", min=0), **_RECT_TUBE},
        outputs={"f1": ("Hz", "first bending frequency", sp.pi / (2 * Lm**2) * sp.sqrt(E * 1e6 * I / (rho * A)))},
        assumptions=["Euler-Bernoulli beam, simply supported", "the beam's own mass only (no added masses)",
                     "bending in the plane of h"],
        source="first mode of a simply supported uniform beam (textbook vibration)",
        requires=[(sp.And(2 * t < b, 2 * t < h, rho > 0), "wall thickness t must be less than half of b and h, rho positive")],
        symbols=s,
    ))
    s = _sym("v", "r", "i")
    v, r, i = s["v"], s["r"], s["i"]
    f_wheel = (v / 3.6) / (2 * sp.pi * r / 1000)
    out.append(Formula(
        formula_id="modal.rotation_excitation",
        title="車輪とモーターの回転で揺する振動数",
        domain="nvh",
        expression="f_wheel = v / (2 pi r); f_motor = i f_wheel",
        inputs={"v": _in("km/h", "speed", min=0), "r": _in("mm", "wheel radius", min=0),
                "i": _in("1", "reduction ratio from motor to wheel", min=0)},
        outputs={"f_wheel": ("Hz", "once per wheel turn", f_wheel), "f_motor": ("Hz", "once per motor turn", i * f_wheel)},
        assumptions=["no slip", "first order of rotation only (imbalance, out-of-round)"],
        source="kinematics of rolling (textbook)",
        requires=[(r > 0, "r must be positive")],
        symbols=s,
    ))
    return out

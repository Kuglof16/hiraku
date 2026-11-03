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
    s = _sym("E", "nu", "J_r", "J_c", "n_c", "w", "L", "I0", "r_x", "k_f", "k_r", "t_f", "t_r", "d_f", "d_r")
    G = s["E"] / (2 * (1 + s["nu"]))
    K_frame = 2 * G * s["J_r"] * s["L"] + s["n_c"] * G * s["J_c"] * s["w"]  # N*mm^3 per (rad/mm)^2
    K_spring = 2 * s["k_f"] * (s["t_f"] / 2) ** 2 * s["d_f"] ** 2 + 2 * s["k_r"] * (s["t_r"] / 2) ** 2 * s["d_r"] ** 2
    M_twist = s["I0"] * 1e6 * s["r_x"] ** 2  # kg*mm^2 * mm^2
    out.append(Formula(
        formula_id="modal.ladder_twist_mode",
        title="はしご形のフレームのねじりの固有振動数 (左右のレールが逆に動くモード)",
        domain="nvh",
        expression="psi = psi' (x - x_I); K = 2 G J_r L + n_c G J_c w + sum over wheels k (t/2)^2 d^2; "
                   "f = sqrt(K / (I0 r_x^2)) / (2 pi) (Rayleigh)",
        inputs={"E": _in("MPa", "Young's modulus", min=0), "nu": _in("1", "Poisson's ratio", min=0, max=0.5),
                "J_r": _in("mm^4", "torsion constant of a rail (Bredt for a tube)", min=0),
                "J_c": _in("mm^4", "torsion constant of a cross member", min=0),
                "n_c": _in("1", "number of cross members", min=0),
                "w": _in("mm", "distance between the rails (length of a cross member)", min=0),
                "L": _in("mm", "length of the frame", min=0),
                "I0": _in("kg*m^2", "roll inertia of everything on the frame about the vehicle's x axis", min=0),
                "r_x": _in("mm", "spread of that roll inertia along x (radius of gyration about its centroid x_I)", min=0),
                "k_f": _in("N/mm", "front wheel rate", min=0), "k_r": _in("N/mm", "rear wheel rate", min=0),
                "t_f": _in("mm", "front track", min=0), "t_r": _in("mm", "rear track", min=0),
                "d_f": _in("mm", "front axle ahead of x_I"), "d_r": _in("mm", "rear axle ahead of x_I (negative behind)")},
        outputs={"f": ("Hz", "first twist frequency of the frame", sp.sqrt(1e3 * (K_frame + K_spring) / M_twist) / (2 * sp.pi)),
                 "spring_share": ("1", "share of the suspension springs in the twist stiffness", K_spring / (K_frame + K_spring))},
        assumptions=["the twist angle varies linearly along x about the roll inertia's centroid (a Rayleigh upper bound)",
                     "rails and cross members twist (Bredt), joints rigid; the rails' opposite bending is left out",
                     "the weights sit on the frame nodes that carry them; the suspension springs are vertical"],
        source="Rayleigh's method for the lowest mode; Saint-Venant torsion of thin-walled closed tubes (Bredt) "
               "(textbook vibration and structures)",
        requires=[(sp.And(s["I0"] > 0, s["r_x"] > 0, s["L"] > 0), "I0, r_x and L must be positive")],
        symbols=s,
    ))
    s = _sym("E", "rho", "L", "D", "t")
    E, rho, L, D, t = (s[x] for x in ("E", "rho", "L", "D", "t"))
    d = D - 2 * t
    I = sp.pi * (D**4 - d**4) / 64 * 1e-12  # m^4
    A = sp.pi * (D**2 - d**2) / 4 * 1e-6  # m^2
    Lm = L / 1000
    out.append(Formula(
        formula_id="modal.tube_shaft_bending",
        title="両端を支えた丸パイプの軸 (ドライブシャフト) の 1 次の曲げの固有振動数",
        domain="nvh",
        expression="I = pi (D^4 - d^4) / 64, A = pi (D^2 - d^2) / 4, d = D - 2 t; f1 = (pi / (2 L^2)) sqrt(E I / (rho A))",
        inputs={"E": _in("MPa", "Young's modulus", min=0), "rho": _in("kg/m^3", "density", min=0),
                "L": _in("mm", "joint to joint", min=0), "D": _in("mm", "tube outer diameter", min=0),
                "t": _in("mm", "tube wall", min=0)},
        outputs={"f1": ("Hz", "first bending frequency", sp.pi / (2 * Lm**2) * sp.sqrt(E * 1e6 * I / (rho * A)))},
        assumptions=["Euler-Bernoulli beam, held across at both joints and free to turn there (simply supported)",
                     "the tube's own mass only (the joints' mass sits at the supports)"],
        source="first mode of a simply supported uniform beam (textbook vibration)",
        requires=[(sp.And(2 * t < D, rho > 0, L > 0), "the wall t must be less than half of D; rho and L positive")],
        symbols=s,
    ))
    s = _sym("E", "nu", "L", "D", "t", "J_1", "J_2")
    d = s["D"] - 2 * s["t"]
    Jp = sp.pi * (s["D"]**4 - d**4) / 32 * 1e-12  # m^4
    k_t = s["E"] * 1e6 / (2 * (1 + s["nu"])) * Jp / (s["L"] / 1000)  # N*m per rad
    J1, J2 = s["J_1"], s["J_2"]
    out.append(Formula(
        formula_id="modal.two_inertia_torsion",
        title="2 つの回る質量を軸のねじりでつないだ固有振動数 (モーターと減速機・車輪とドライブシャフト)",
        domain="nvh",
        expression="k = G J_p / L, G = E / (2 (1 + nu)), J_p = pi (D^4 - d^4) / 32, d = D - 2 t; "
                   "f = sqrt(k (J_1 + J_2) / (J_1 J_2)) / (2 pi)",
        inputs={"E": _in("MPa", "Young's modulus", min=0), "nu": _in("1", "Poisson's ratio", min=0, max=0.5),
                "L": _in("mm", "shaft length, joint to joint", min=0), "D": _in("mm", "tube outer diameter", min=0),
                "t": _in("mm", "tube wall", min=0),
                "J_1": _in("kg*m^2", "turning inertia at one end (motor and reducer as seen at this shaft)", min=0),
                "J_2": _in("kg*m^2", "turning inertia at the other end (the wheel)", min=0)},
        outputs={"f": ("Hz", "torsion frequency of the two inertias on the shaft", sp.sqrt(k_t * (J1 + J2) / (J1 * J2)) / (2 * sp.pi)),
                 "k": ("N*m", "torsion stiffness of the shaft (torque per radian of twist)", k_t)},
        assumptions=["the shaft's own turning inertia is left out (small next to the two ends)",
                     "both ends turn free (no grip of the tyre on the road)"],
        source="two-disc torsional vibration on a massless shaft; Saint-Venant torsion of a round tube (textbook vibration)",
        requires=[(sp.And(2 * s["t"] < s["D"], J1 > 0, J2 > 0, s["L"] > 0), "t < D/2, J_1, J_2 and L positive")],
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

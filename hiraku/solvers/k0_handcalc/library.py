"""The formulas on the K0 shelf.

A prediction picks one output of a formula and names its own `quantity`
(e.g. output F of step_climb.wheel_force_rigid as front_wheel_force); the
CAE tools report results under those quantity names.
"""

from __future__ import annotations

import sympy as sp

from .registry import Formula
from .units import STANDARD_GRAVITY

G = sp.Float(STANDARD_GRAVITY)


def _sym(*names: str) -> dict[str, sp.Symbol]:
    return {n: sp.Symbol(n, real=True) for n in names}


# ---------------------------------------------------------------- step_climb

def _step_climb() -> list[Formula]:
    s = _sym("r", "h", "W")
    r, h, W = s["r"], s["h"], s["W"]
    d = sp.sqrt(2 * r * h - h**2)
    ratio = d / (r - h)
    inputs = {
        "r": {"unit": "mm", "description": "wheel radius", "min": 0},
        "h": {"unit": "mm", "description": "step height", "min": 0},
        "W": {"unit": "N", "description": "vertical load carried by this wheel at the moment it climbs", "min": 0},
    }
    assumptions = [
        "rigid wheel and rigid step corner",
        "quasi-static: no speed or impact effect",
        "at lift-off the whole wheel load rests on the corner",
        "h < r",
    ]
    source = "static equilibrium of a rigid wheel on a step corner (textbook statics)"
    below = (sp.StrictLessThan(h, r), "step height h must be below the wheel radius r: a rigid wheel cannot climb a step as high as its axle (raise r or lower the step)")
    return [
        Formula(
            formula_id="step_climb.wheel_force_rigid",
            title="剛い輪が段の角に当たったときに要る押す力",
            domain="powertrain",
            expression="d = sqrt(2*r*h - h^2); F = W*d/(r - h)",
            inputs=inputs,
            outputs={
                "F": ("N", "horizontal push at the axle that lifts the wheel onto the corner", W * ratio),
                "d": ("mm", "horizontal distance from the wheel centre to the step corner", d),
            },
            assumptions=assumptions + ["the push acts at the axle, so the corner reaction points at the wheel centre"],
            source=source,
            requires=[below],
            symbols=s,
        ),
        Formula(
            formula_id="step_climb.climb_limit",
            title="剛い輪が越えられる段の高さ (輪の半径まで)",
            domain="powertrain",
            expression="climbable = 1 if h < r else 0; step_margin = r - h; max_step = r",
            inputs={k: inputs[k] for k in ("r", "h")},
            outputs={
                "climbable": ("1", "1 when a rigid wheel can be lifted onto the step, 0 when it cannot",
                              sp.Piecewise((1, sp.StrictLessThan(h, r)), (0, True))),
                "step_margin": ("mm", "how far the step stays below the wheel centre (negative: too high)", r - h),
                "max_step": ("mm", "the highest step a rigid wheel can climb (its radius)", r),
            },
            assumptions=["rigid wheel and rigid step corner",
                         "at h >= r the corner meets the wheel at or above its centre, so the corner's push on the wheel "
                         "has no upward part and no push at the axle lifts it; a tyre that deflects is not counted"],
            source=source,
            symbols={k: s[k] for k in ("r", "h")},
        ),
        _pushed_wheel_load(),
        Formula(
            formula_id="step_climb.self_climb",
            title="押されずに輪が自分のトルクで段を登るのに要るトルクと摩擦",
            domain="powertrain",
            expression="d = sqrt(2*r*h - h^2); tau = W*d; mu = d/(r - h)",
            inputs=inputs,
            outputs={
                "tau": ("N*m", "axle torque for the wheel to climb by itself", W * d / 1000),
                "mu": ("1", "friction coefficient the wheel needs at the step corner", ratio),
            },
            assumptions=assumptions + ["the body does not push: the corner reaction is vertical and the wheel torque turns it about the corner"],
            source=source,
            requires=[below],
            symbols=s,
        ),
    ]


def _pushed_wheel_load() -> Formula:
    s = _sym("m", "a", "L", "n", "r", "h")
    m, a, L, n, r, h = (s[k] for k in ("m", "a", "L", "n", "r", "h"))
    k = sp.sqrt(2 * r * h - h**2) / (r - h)
    W = m * G * (L - a) / (L * n) / (1 + k * r / L)
    return Formula(
        formula_id="step_climb.pushed_wheel_load",
        title="後輪に押されて前輪が段を登るときの前輪 1 本の荷重と押す力",
        domain="powertrain",
        expression="k = sqrt(2*r*h - h^2)/(r - h); W = m*g*(L - a)/(L*n) / (1 + k*r/L); F = k*W",
        inputs={
            "m": {"unit": "kg", "description": "total mass on board", "min": 0},
            "a": {"unit": "mm", "description": "distance from the front axle back to the centre of gravity (positive rearward)", "min": 0},
            "L": {"unit": "mm", "description": "wheelbase", "min": 0},
            "n": {"unit": "1", "description": "number of front wheels on the step", "min": 1},
            "r": {"unit": "mm", "description": "front wheel radius", "min": 0},
            "h": {"unit": "mm", "description": "step height", "min": 0},
        },
        outputs={
            "W": ("N", "vertical load on one front wheel at the moment it lifts off", W),
            "F": ("N", "push on one front wheel (k W)", k * W),
        },
        assumptions=[
            "rigid wheels; quasi-static at the moment the front wheels lift off",
            "the rear wheels push with traction at the ground; the step pushes back at the front axle height r",
            "that couple (k W r) moves load from the front to the rear axle",
            "centre of gravity on the centre line; all front wheels on the step",
            "g = 9.80665 m/s^2",
        ],
        source="moment balance of the whole vehicle about the rear contact line (textbook statics)",
        requires=[(sp.StrictLessThan(h, r), "step height h must be below the wheel radius r"),
                  (sp.And(a >= 0, a <= L), "the centre of gravity must lie between the axles (0 <= a <= L); a is measured rearward from the front axle")],
        symbols=s,
    )


# ---------------------------------------------------------------- mass_props

def _mass_props() -> list[Formula]:
    s = _sym("m", "a", "L", "t", "e")
    m, a, L, t, e = (s[k] for k in ("m", "a", "L", "t", "e"))
    front = m * G * (L - a) / L
    rear = m * G * a / L
    return [
        Formula(
            formula_id="mass_props.static_wheel_loads",
            title="止まっているときの各輪の荷重 (てこの比)",
            domain="handling",
            expression="front = m*g*(L - a)/L; rear = m*g*a/L; wheel_load_FL = front*(1/2 + e/t); wheel_load_FR = front*(1/2 - e/t); same for RL/RR with rear",
            inputs={
                "m": {"unit": "kg", "description": "total mass of the vehicle with its load", "min": 0},
                "a": {"unit": "mm", "description": "distance from the front axle back to the centre of gravity", "min": 0},
                "L": {"unit": "mm", "description": "wheelbase", "min": 0},
                "t": {"unit": "mm", "description": "track (same front and rear)", "min": 0},
                "e": {"unit": "mm", "description": "sideways offset of the centre of gravity, positive to the left (0 when centred)"},
            },
            outputs={
                "axle_load_front": ("N", "load on the front axle", front),
                "axle_load_rear": ("N", "load on the rear axle", rear),
                "wheel_load_FL": ("N", "front left wheel load", front * (sp.Rational(1, 2) + e / t)),
                "wheel_load_FR": ("N", "front right wheel load", front * (sp.Rational(1, 2) - e / t)),
                "wheel_load_RL": ("N", "rear left wheel load", rear * (sp.Rational(1, 2) + e / t)),
                "wheel_load_RR": ("N", "rear right wheel load", rear * (sp.Rational(1, 2) - e / t)),
            },
            assumptions=[
                "rigid body at rest on level ground, all four wheels touching",
                "front/rear split by the lever rule, then left/right split by the lever rule on each axle",
                "frame and spring stiffness are ignored (no warp)",
                "g = 9.80665 m/s^2",
            ],
            source="static equilibrium (textbook statics)",
            requires=[
                (sp.And(a >= 0, a <= L), "the centre of gravity must lie between the axles (0 <= a <= L)"),
                (sp.And(e >= -t / 2, e <= t / 2), "the centre of gravity must lie between the wheels (|e| <= t/2)"),
                (L > 0, "wheelbase L must be positive"),
                (t > 0, "track t must be positive"),
            ],
            symbols=s,
        ),
        _front_wheel_load(),
    ]


def _front_wheel_load() -> Formula:
    s = _sym("m", "a", "L", "n")
    m, a, L, n = (s[k] for k in ("m", "a", "L", "n"))
    return Formula(
        formula_id="mass_props.front_wheel_load",
        title="止まっているときの前輪 1 本の荷重 (てこの比)",
        domain="powertrain",
        expression="W = m*g*(L - a)/(L*n)",
        inputs={
            "m": {"unit": "kg", "description": "total mass on board", "min": 0},
            "a": {"unit": "mm", "description": "distance from the front axle back to the centre of gravity (positive rearward)", "min": 0},
            "L": {"unit": "mm", "description": "wheelbase", "min": 0},
            "n": {"unit": "1", "description": "number of front wheels", "min": 1},
        },
        outputs={"W": ("N", "load on one front wheel", m * G * (L - a) / (L * n))},
        assumptions=[
            "rigid body at rest on level ground",
            "front/rear split by the lever rule",
            "the front axle load shared equally by the front wheels (centre of gravity on the centre line)",
            "g = 9.80665 m/s^2",
        ],
        source="static equilibrium (textbook statics)",
        requires=[(sp.And(a >= 0, a <= L), "the centre of gravity must lie between the axles (0 <= a <= L); a is measured rearward from the front axle"),
                  (L > 0, "wheelbase L must be positive")],
        symbols=s,
    )


# ---------------------------------------------------------------- frame_static

def _rect_tube_I(b, h, t):
    return (b * h**3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12


_RECT_TUBE_INPUTS = {
    "b": {"unit": "mm", "description": "tube width (across the bending plane)", "min": 0},
    "h": {"unit": "mm", "description": "tube height (in the bending plane)", "min": 0},
    "t": {"unit": "mm", "description": "wall thickness", "min": 0},
}

_BEAM_ASSUMPTIONS = [
    "Euler-Bernoulli beam: linear elastic, small deflection, no shear deformation",
    "rectangular tube bent about the axis across its width (stress at the top and bottom faces)",
    "self-weight not included unless it is the load",
]


def _wall_ok(b, h, t):
    return (sp.And(2 * t < b, 2 * t < h), "wall thickness t must be less than half of b and h")


def _frame_static() -> list[Formula]:
    out = []

    s = _sym("F", "L", "b", "h", "t")
    F, L, b, h, t = (s[k] for k in ("F", "L", "b", "h", "t"))
    I = _rect_tube_I(b, h, t)
    out.append(Formula(
        formula_id="frame_static.rail_bending",
        title="レールを両端で支えた梁とみて、真ん中に力をかけたときの曲げ応力",
        domain="strength",
        expression="M = F*L/4; Z = I/(h/2); sigma = M/Z; I = (b*h^3 - (b-2t)*(h-2t)^3)/12",
        inputs={
            "F": {"unit": "N", "description": "force on the rail, across it (e.g. the wheel force brought in by the suspension)", "min": 0},
            "L": {"unit": "mm", "description": "span of the rail between its supports", "min": 0},
            **_RECT_TUBE_INPUTS,
        },
        outputs={
            "sigma": ("MPa", "bending stress at mid-span, outer face", F * L / 4 * (h / 2) / I),
            "M": ("N*mm", "bending moment at mid-span", F * L / 4),
            "Z": ("mm^3", "section modulus I/(h/2)", I / (h / 2)),
        },
        assumptions=_BEAM_ASSUMPTIONS + [
            "one rail as a simply supported beam, the force at mid-span",
            "the force bends the rail about the axis across its width",
        ],
        source="simply supported beam with a centre load, M = F L / 4, sigma = M / Z (textbook beam tables)",
        requires=[_wall_ok(b, h, t)],
        symbols=s,
    ))

    s = _sym("P", "L", "a", "E", "b", "h", "t")
    P, L, a, E, b, h, t = (s[k] for k in ("P", "L", "a", "E", "b", "h", "t"))
    I = _rect_tube_I(b, h, t)
    M = P * a * (L - a) / L
    out.append(Formula(
        formula_id="frame_static.simple_beam_point_load",
        title="両端を支えた角パイプの梁に 1 点の荷重",
        domain="strength",
        expression="M = P*a*(L - a)/L; max_stress = M*(h/2)/I; deflection at the load = P*a^2*(L - a)^2/(3*E*I*L); I = (b*h^3 - (b-2t)*(h-2t)^3)/12",
        inputs={
            "P": {"unit": "N", "description": "point load", "min": 0},
            "L": {"unit": "mm", "description": "span between the supports", "min": 0},
            "a": {"unit": "mm", "description": "distance from the left support to the load", "min": 0},
            "E": {"unit": "MPa", "description": "Young's modulus", "min": 0},
            **_RECT_TUBE_INPUTS,
        },
        outputs={
            "max_stress": ("MPa", "largest bending stress (under the load)", M * (h / 2) / I),
            "max_moment": ("N*mm", "largest bending moment (under the load)", M),
            "max_displacement": ("mm", "deflection under the load", P * a**2 * (L - a) ** 2 / (3 * E * I * L)),
        },
        assumptions=_BEAM_ASSUMPTIONS + ["simple supports at both ends (free to rotate)"],
        source="simply supported beam with a point load (textbook beam tables)",
        requires=[(sp.And(a >= 0, a <= L), "the load position a must be within the span (0 <= a <= L)"), _wall_ok(b, h, t)],
        symbols=s,
    ))

    s = _sym("P", "L", "E", "b", "h", "t")
    P, L, E, b, h, t = (s[k] for k in ("P", "L", "E", "b", "h", "t"))
    I = _rect_tube_I(b, h, t)
    out.append(Formula(
        formula_id="frame_static.simple_beam_center_load",
        title="両端を支えた角パイプの梁の真ん中に荷重",
        domain="strength",
        expression="M = P*L/4; max_stress = M*(h/2)/I; max_displacement = P*L^3/(48*E*I)",
        inputs={
            "P": {"unit": "N", "description": "load at mid-span", "min": 0},
            "L": {"unit": "mm", "description": "span between the supports", "min": 0},
            "E": {"unit": "MPa", "description": "Young's modulus", "min": 0},
            **_RECT_TUBE_INPUTS,
        },
        outputs={
            "max_stress": ("MPa", "largest bending stress (mid-span)", P * L / 4 * (h / 2) / I),
            "max_moment": ("N*mm", "largest bending moment (mid-span)", P * L / 4),
            "max_displacement": ("mm", "mid-span deflection", P * L**3 / (48 * E * I)),
        },
        assumptions=_BEAM_ASSUMPTIONS + ["simple supports at both ends (free to rotate)"],
        source="simply supported beam with a centre load (textbook beam tables)",
        requires=[_wall_ok(b, h, t)],
        symbols=s,
    ))

    s = _sym("w", "L", "E", "b", "h", "t")
    w, L, E, b, h, t = (s[k] for k in ("w", "L", "E", "b", "h", "t"))
    I = _rect_tube_I(b, h, t)
    out.append(Formula(
        formula_id="frame_static.simple_beam_udl",
        title="両端を支えた角パイプの梁に一様な荷重",
        domain="strength",
        expression="M = w*L^2/8; max_stress = M*(h/2)/I; max_displacement = 5*w*L^4/(384*E*I)",
        inputs={
            "w": {"unit": "N/mm", "description": "load per length", "min": 0},
            "L": {"unit": "mm", "description": "span between the supports", "min": 0},
            "E": {"unit": "MPa", "description": "Young's modulus", "min": 0},
            **_RECT_TUBE_INPUTS,
        },
        outputs={
            "max_stress": ("MPa", "largest bending stress (mid-span)", w * L**2 / 8 * (h / 2) / I),
            "max_moment": ("N*mm", "largest bending moment (mid-span)", w * L**2 / 8),
            "max_displacement": ("mm", "mid-span deflection", 5 * w * L**4 / (384 * E * I)),
        },
        assumptions=_BEAM_ASSUMPTIONS + ["simple supports at both ends (free to rotate)"],
        source="simply supported beam with a uniform load (textbook beam tables)",
        requires=[_wall_ok(b, h, t)],
        symbols=s,
    ))

    s = _sym("P", "L", "E", "b", "h", "t")
    P, L, E, b, h, t = (s[k] for k in ("P", "L", "E", "b", "h", "t"))
    I = _rect_tube_I(b, h, t)
    out.append(Formula(
        formula_id="frame_static.cantilever_tip_load",
        title="片持ちの角パイプの先に荷重",
        domain="strength",
        expression="M = P*L; max_stress = M*(h/2)/I; max_displacement = P*L^3/(3*E*I)",
        inputs={
            "P": {"unit": "N", "description": "load at the free end", "min": 0},
            "L": {"unit": "mm", "description": "length from the fixed end", "min": 0},
            "E": {"unit": "MPa", "description": "Young's modulus", "min": 0},
            **_RECT_TUBE_INPUTS,
        },
        outputs={
            "max_stress": ("MPa", "largest bending stress (at the fixed end)", P * L * (h / 2) / I),
            "max_moment": ("N*mm", "largest bending moment (at the fixed end)", P * L),
            "max_displacement": ("mm", "deflection of the free end", P * L**3 / (3 * E * I)),
        },
        assumptions=_BEAM_ASSUMPTIONS + ["fully fixed at one end"],
        source="cantilever with an end load (textbook beam tables)",
        requires=[_wall_ok(b, h, t)],
        symbols=s,
    ))

    s = _sym("M", "b", "h", "t")
    M, b, h, t = (s[k] for k in ("M", "b", "h", "t"))
    I = _rect_tube_I(b, h, t)
    out.append(Formula(
        formula_id="frame_static.bending_stress",
        title="角パイプの曲げ応力 M/Z",
        domain="strength",
        expression="max_stress = M/Z; Z = I/(h/2); I = (b*h^3 - (b-2t)*(h-2t)^3)/12",
        inputs={"M": {"unit": "N*mm", "description": "bending moment", "min": 0}, **_RECT_TUBE_INPUTS},
        outputs={"max_stress": ("MPa", "bending stress at the outer face", M * (h / 2) / I)},
        assumptions=["linear elastic", "pure bending about the axis across the width"],
        source="beam bending stress sigma = M/Z (textbook)",
        requires=[_wall_ok(b, h, t)],
        symbols=s,
    ))
    out.append(_rail_step_bending())
    out.append(_rail_joint_stress())
    out.append(_end_cross_member_stress())
    return out


def _tube(b: float, h: float, t: float) -> dict:
    bi, hi = b - 2 * t, h - 2 * t
    return {"A": b * h - bi * hi, "I_v": (b * h**3 - bi * hi**3) / 12.0, "I_l": (h * b**3 - hi * bi**3) / 12.0,
            "Am": (b - t) * (h - t), "J": 4.0 * ((b - t) * (h - t)) ** 2 * t / (2.0 * ((b - t) + (h - t)))}


def _rail_joint(v: dict) -> dict:
    """Stress in the rail at the rear axle joint while the rear wheels push the front ones up a step.

    Vertical bending: the overhang moment plus the traction moment F z (left and right are loaded
    alike, so the cross member turns with the joint and takes none of it). Torsion W e and the
    sideways moment F e come in at the joint from the wheel's offset; one step of moment
    distribution shares them between the rail ahead (far end held: G J / L1, 4 E I / L1) and the
    cross member (bent by the mirror-image moments from both sides: 2 E I / L_c); the rail behind
    the joint ends free and takes none. Stresses add at the worst section corner.
    """
    r = _tube(v["b"], v["h"], v["t"])
    c = _tube(v["b_c"], v["h_c"], v["t_c"])
    E, G = v["E"], v["E"] / (2.0 * (1.0 + v["nu"]))
    F, W = v["F"], v["W"]
    M_v = v["M_oh"] + F * v["z"]
    k_t = G * r["J"] / v["L1"]
    k_cv = 2.0 * E * c["I_v"] / v["L_c"]  # cross member bent vertically by the twist at both ends
    T = W * v["e"] * k_t / (k_t + k_cv)
    k_l = 4.0 * E * r["I_l"] / v["L1"]
    k_cl = 2.0 * E * c["I_l"] / v["L_c"]  # cross member bent sideways
    M_l = F * v["e"] * k_l / (k_l + k_cl)
    s_ax = F / r["A"]
    s_v = abs(M_v) * (v["h"] / 2.0) / r["I_v"]
    s_l = abs(M_l) * (v["b"] / 2.0) / r["I_l"]
    tau = abs(T) / (2.0 * r["Am"] * v["t"])
    sigma = s_ax + s_v + s_l
    return {"sigma": (sigma**2 + 3.0 * tau**2) ** 0.5, "sigma_vertical": s_v, "sigma_sideways": s_l,
            "sigma_axial": s_ax, "tau_torsion": tau, "M_vertical": M_v, "M_sideways": M_l, "T_rail": T}


def _rail_joint_stress() -> Formula:
    mm = lambda d: {"unit": "mm", "description": d, "min": 0}  # noqa: E731
    return Formula(
        formula_id="frame_static.rail_joint_stress",
        title="段差を押して登るときの後ろの車軸の所のレールの応力 (張り出し・横の曲げ・ねじりを入れる)",
        domain="strength",
        expression="M_v = M_oh + F z; T = W e kt/(kt + kc,v); M_l = F e kl/(kl + kc,l); "
                   "kt = G J/L1, kl = 4 E I_l/L1, kc = 2 E I_c/L_c; sigma = F/A + M_v (h/2)/I_v + M_l (b/2)/I_l; "
                   "von Mises sqrt(sigma^2 + 3 tau^2), tau = T/(2 A_m t)",
        inputs={
            "M_oh": {"unit": "N*mm", "description": "hogging moment at the joint from the loads on this rail behind the axle "
                                                    "(rail_joint_inputs computes it from the part masses and x positions)", "min": 0},
            "F": {"unit": "N", "description": "push of the step on one front wheel = traction of one rear wheel (P1 front_wheel_force)", "min": 0},
            "W": {"unit": "N", "description": "vertical load on the rear wheel while climbing (P1 wheel_load of that wheel)", "min": 0},
            "z": mm("height of the rail centre above the ground (lever of the traction)"),
            "e": mm("sideways offset of the wheel centre from the rail centre"),
            "L1": mm("length of the rail piece ahead of the joint"),
            "L_c": mm("length of the cross member at the joint"),
            "E": {"unit": "MPa", "description": "Young's modulus", "min": 0},
            "nu": {"unit": "1", "description": "Poisson's ratio", "min": 0, "max": 0.5},
            "b": mm("rail tube width"), "h": mm("rail tube height"), "t": mm("rail wall"),
            "b_c": mm("cross member width"), "h_c": mm("cross member height"), "t_c": mm("cross member wall"),
        },
        outputs={
            "sigma": ("MPa", "von Mises stress at the worst corner of the rail at the joint", None),
            "sigma_vertical": ("MPa", "from vertical bending (overhang and traction)", None),
            "sigma_sideways": ("MPa", "from sideways bending (traction at the wheel offset)", None),
            "sigma_axial": ("MPa", "from the push (compression)", None),
            "tau_torsion": ("MPa", "shear from the twist (wheel load at the offset)", None),
            "M_vertical": ("N*mm", "vertical bending moment in the rail at the joint", None),
            "M_sideways": ("N*mm", "sideways bending moment in the rail at the joint", None),
            "T_rail": ("N*mm", "torque in the rail at the joint", None),
        },
        assumptions=[
            "both front wheels on the step, each rear wheel pushes with F; quasi-static",
            "left and right loaded alike (mirror images)",
            "the wheel's loads reach the rail at the joint through the suspension, with the moments of the offset e",
            "one step of moment distribution at the joint: far ends of the rail ahead held, the rail behind free at its end",
            "loads behind the axle enter at their carried_by nodes, shared equally (no transfer moments)",
            "thin-walled rectangular tubes (Bredt torsion); shear from transverse forces left out",
        ],
        source="beam statics, moment distribution at one joint (Hardy Cross) and Bredt's torsion formula (textbook structures)",
        requires=[(lambda v: all(2 * v[t] < min(v[b], v[h]) for b, h, t in (("b", "h", "t"), ("b_c", "h_c", "t_c"))),
                   "wall thickness must be less than half of the tube width and height"),
                  (lambda v: v["L1"] > 0 and v["L_c"] > 0, "L1 and L_c must be positive")],
        fn=_rail_joint,
    )


def _end_cross(v: dict) -> dict:
    """Stress at a corner of the end cross member of a ladder's rear overhang for 1 kN up at each wheel.

    The wheel stands e outboard of its rail, so its force twists the rail at the rear joint by e F.
    Both rear wheels up (symmetric): opposite twists on the two rails, shared at the joint (one step
    of moment distribution) between the overhang (the rail behind in torsion, then the end cross
    member bent in single curvature, 2 E I / w), the cross member at the joint (2 E I / w) and the
    rail ahead (G J / L1, far end held). Left up and right down (antisymmetric): the ladder twists, and
    at the rail ends the rails' own turn and the chord's would part by the misfit if the end cross
    member did not bend; it closes the misfit in double curvature (w / (6 E I) per unit end moment) in
    series with the give of the rest of the frame (the rails' torsion and bending behind the joint, the
    joint's cross member bending, the ladder ahead warping). Misfit and give come from a beam chain of
    the ladder's antisymmetric half with the masses' inertia relief (end_cross_member_inputs).
    """
    r = _tube(v["b"], v["h"], v["t"])
    c = _tube(v["b_c"], v["h_c"], v["t_c"])
    j = _tube(v["b_j"], v["h_j"], v["t_j"])
    E, G = v["E"], v["E"] / (2.0 * (1.0 + v["nu"]))
    w, a = v["w"], v["a"]
    k_over = 1.0 / (a / (G * r["J"]) + w / (2.0 * E * c["I_v"]))
    k_joint = 2.0 * E * j["I_v"] / w
    k_fwd = G * r["J"] / v["L1"]
    M_sym = 1000.0 * v["e"] * k_over / (k_over + k_joint + k_fwd) / 2.0  # one wheel: half the symmetric pair
    flex = w / (6.0 * E * c["I_v"]) + v["give_per_kNm"] / 1.0e6
    M_r = v["misfit_rear"] / flex / 2.0  # one wheel: half the antisymmetric pair
    M_f = v["misfit_front"] / flex / 2.0
    z = (v["h_c"] / 2.0) / c["I_v"]
    return {"sigma_ra": (M_sym + M_r) * z, "sigma_rb": (M_sym - M_r) * z, "sigma_fa": M_f * z, "sigma_fb": -M_f * z,
            "M_sym": M_sym, "M_anti_rear": M_r, "M_anti_front": M_f}


def _end_cross_member_stress() -> Formula:
    mm = lambda d: {"unit": "mm", "description": d, "min": 0}  # noqa: E731
    per_kN = "stress at the corner of the end cross member where the rear wheels' effects add, for 1 kN up at"
    return Formula(
        formula_id="frame_static.end_cross_member_stress",
        title="後ろの張り出しの端の横の部材の応力 (輪 1 つに 1 kN、輪のずれのねじりとはしごのねじれ)",
        domain="strength",
        expression="symmetric: M = 1000 e k_o/(k_o + k_j + k_f) / 2, k_o = 1/(a/(G J) + w/(2 E I_c)), k_j = 2 E I_j/w, "
                   "k_f = G J/L1; antisymmetric: M = misfit / (w/(6 E I_c) + give_per_kNm/1e6) / 2; "
                   "sigma_ra,rb = (M_sym +- M_anti_rear) (h_c/2)/I_c, sigma_fa,fb = +-M_anti_front (h_c/2)/I_c",
        inputs={
            "e": mm("sideways offset of the wheel centre outboard of the rail centre"),
            "a": mm("length of the rail behind the rear joint (the overhang)"),
            "w": mm("distance between the rail centres (length of the cross members)"),
            "L1": mm("length of the rail piece ahead of the rear joint"),
            "E": {"unit": "MPa", "description": "Young's modulus", "min": 0},
            "nu": {"unit": "1", "description": "Poisson's ratio", "min": 0, "max": 0.5},
            "b": mm("rail tube width"), "h": mm("rail tube height"), "t": mm("rail wall"),
            "b_c": mm("end cross member width"), "h_c": mm("end cross member height"), "t_c": mm("end cross member wall"),
            "b_j": mm("cross member at the joint, width"), "h_j": mm("its height"), "t_j": mm("its wall"),
            "misfit_rear": {"unit": "rad", "description": "for 1 kN up at the left and down at the right rear wheel: how far "
                                                          "the rails' own turn and the chord's turn part at the rail ends when "
                                                          "the end cross member does not bend (end_cross_member_inputs, from a "
                                                          "beam chain of the ladder's antisymmetric half with inertia relief)",
                            "min": 0},
            "misfit_front": {"unit": "rad", "description": "the same for the front wheels, with its sign against misfit_rear "
                                                           "(negative: the end cross member is bent the other way)"},
            "give_per_kNm": {"unit": "rad", "description": "how far the rest of the frame lets that mismatch close for 1 kN*m "
                                                           "at each end of the end cross member (with its shears): the rails' "
                                                           "torsion and bending behind the joint, the joint's cross member, the "
                                                           "ladder ahead (end_cross_member_inputs)", "min": 0},
        },
        outputs={
            "sigma_ra": ("MPa", per_kN + " rear wheel a (the left one)", None),
            "sigma_rb": ("MPa", per_kN + " rear wheel b (the right one)", None),
            "sigma_fa": ("MPa", per_kN + " front wheel a (left, same track as rear a)", None),
            "sigma_fb": ("MPa", per_kN + " front wheel b", None),
            "M_sym": ("N*mm", "end cross member moment from 1 kN on one rear wheel, symmetric half", None),
            "M_anti_rear": ("N*mm", "the same, antisymmetric half", None),
            "M_anti_front": ("N*mm", "end cross member moment from 1 kN on one front wheel (antisymmetric half), signed "
                                     "against M_anti_rear", None),
        },
        assumptions=[
            "the wheel's force reaches the rail at the rear joint with the twist of its sideways offset e",
            "symmetric half: one step of moment distribution at the joint (far end of the rail ahead held)",
            "antisymmetric half: the end cross member is the one redundant; misfit and give come from a beam chain "
            "of the ladder's antisymmetric half (rails' opposite bending and torsion between stations, cross "
            "members' double-curvature bending and torsion at the stations), the wheel's force with the moment of "
            "its offset at its frame node, the roll moment taken by the masses' d'Alembert loads (inertia relief) "
            "shared over the frame nodes as the beam model shares them",
            "the end cross member's corner stress from its vertical bending only (its sideways bending and axial "
            "force are left out)",
            "the weights' own bending of the overhang rails is not counted (it does not bend the end cross member)",
            "thin-walled rectangular tubes (Bredt torsion), sharp corners",
        ],
        source="beam statics, moment distribution at one joint (Hardy Cross), Bredt's torsion formula and the force "
               "method with one redundant on a beam chain of the ladder frame (textbook structures)",
        requires=[(lambda v: all(2 * v[t] < min(v[b], v[h]) for b, h, t in (("b", "h", "t"), ("b_c", "h_c", "t_c"),
                                                                             ("b_j", "h_j", "t_j"))),
                   "wall thickness must be less than half of the tube width and height"),
                  (lambda v: v["a"] > 0 and v["w"] > 0 and v["L1"] > 0, "a, w and L1 must be positive")],
        fn=_end_cross,
    )


def _rail_step_moments(v: dict) -> dict:
    """Moment along one rail: uniform half weight between the axle supports, minus the
    hogging end moments of the step push (front, at the wheel centre) and the
    traction (rear, at the ground), both below the rail."""
    W, L, F, z, r, b, h, t = (v[k] for k in ("W", "L", "F", "z", "r", "b", "h", "t"))
    I = (b * h**3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12.0
    A = b * h - (b - 2 * t) * (h - 2 * t)
    w = W / 2.0 / L
    MA, MB = F * (z - r), F * z
    M = lambda x: w * x * (L - x) / 2.0 - MA * (1 - x / L) - MB * x / L
    xs = [0.0, L]
    x_star = L / 2.0 + (MA - MB) / (w * L) if w > 0 else -1.0
    if 0.0 < x_star < L:
        xs.append(x_star)
    x_max = max(xs, key=lambda x: abs(M(x)))
    sig = lambda x: abs(M(x)) * (h / 2.0) / I + F / A
    return {"sigma": sig(x_max), "sigma_rear": sig(L), "sigma_mid": sig(L / 2.0),
            "M_rear": -M(L), "x_max": x_max}


def _rail_step_bending() -> Formula:
    return Formula(
        formula_id="frame_static.rail_step_bending",
        title="段差を押して登るときのレールの曲げ応力 (両端を車軸で支えた梁)",
        domain="strength",
        expression="M(x) = w x (L - x)/2 - M_A (1 - x/L) - M_B x/L; w = W/(2L); M_A = F (z - r); M_B = F z; "
                   "sigma = max|M|/Z + F/A",
        inputs={
            "W": {"unit": "N", "description": "weight carried by the frame (the sprung weight; the whole weight is on the safe side)", "min": 0},
            "L": {"unit": "mm", "description": "wheelbase (the rail's span between the axles)", "min": 0},
            "F": {"unit": "N", "description": "push of the step on one front wheel (P1's front_wheel_force)", "min": 0},
            "z": {"unit": "mm", "description": "height of the rail's centre line above the ground", "min": 0},
            "r": {"unit": "mm", "description": "wheel radius (the push acts at the front wheel centre)", "min": 0},
            **_RECT_TUBE_INPUTS,
        },
        outputs={
            "sigma": ("MPa", "largest stress along the rail between the axles (bending and the push as compression)", None),
            "sigma_rear": ("MPa", "stress at the rear axle", None),
            "sigma_mid": ("MPa", "stress at mid-span", None),
            "M_rear": ("N*mm", "hogging moment at the rear axle", None),
            "x_max": ("mm", "where the largest stress is, measured back from the front axle", None),
        },
        assumptions=_BEAM_ASSUMPTIONS[:2] + [
            "one rail as a beam simply supported at the two axles, carrying half the weight as a uniform load",
            "both front wheels on the step, pushed by the two rear wheels with the same force F each",
            "the push and the traction act below the rail and reach it as end moments F (z - r) and F z",
            "loads behind the rear axle (overhang), sideways bending and torsion are not included",
        ],
        source="superposition of a simply supported beam under a uniform load and end moments (textbook beam tables)",
        requires=[(lambda v: 2 * v["t"] < min(v["b"], v["h"]), "wall thickness t must be less than half of b and h"),
                  (lambda v: v["L"] > 0 and v["z"] >= v["r"], "L must be positive and the rail at or above the wheel centre (z >= r)")],
        fn=_rail_step_moments,
    )


def all_formulas() -> list[Formula]:
    from . import (library_crash, library_dynamics, library_energy, library_fatigue, library_geometry, library_joint,
                   library_kc, library_nvh, library_portal)

    return (_step_climb() + _mass_props() + _frame_static() + library_joint.all_formulas() + library_dynamics.all_formulas()
            + library_energy.all_formulas() + library_crash.all_formulas() + library_nvh.all_formulas()
            + library_fatigue.all_formulas() + library_geometry.all_formulas() + library_kc.all_formulas()
            + library_portal.all_formulas())

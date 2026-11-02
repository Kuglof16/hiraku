"""The formulas on the K0 shelf.

Output names are chosen to match the `results[].quantity` names of the CAE
tool that checks them (step_climb: wheel_force, force_ratio, ...;
frame_static: max_stress, max_displacement, wheel_load_FL, ...), so a
prediction's `quantity` can be the output name.
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
    return [
        Formula(
            formula_id="step_climb.wheel_force_rigid",
            title="剛い輪が段の角に当たったときに要る押す力",
            domain="powertrain",
            expression="d = sqrt(2*r*h - h^2); force_ratio = d/(r - h); wheel_force = W*d/(r - h); self_climb_torque = W*d",
            inputs={
                "r": {"unit": "mm", "description": "wheel radius", "min": 0},
                "h": {"unit": "mm", "description": "step height", "min": 0},
                "W": {"unit": "N", "description": "vertical load carried by this wheel at the moment it climbs", "min": 0},
            },
            outputs={
                "wheel_force": ("N", "horizontal push at the axle that lifts the wheel onto the corner", W * ratio),
                "force_ratio": ("1", "wheel_force / W, also the friction coefficient a self-driven wheel needs at the corner", ratio),
                "corner_distance": ("mm", "horizontal distance from the wheel centre to the step corner", d),
                "self_climb_torque": ("N*m", "axle torque for the wheel to climb by itself without a push (W*d)", W * d / 1000),
            },
            assumptions=[
                "rigid wheel and rigid step corner",
                "quasi-static: no speed or impact effect",
                "at lift-off the whole wheel load rests on the corner",
                "the push acts at the axle, so the corner reaction points at the wheel centre",
                "h < r",
            ],
            source="static equilibrium of a rigid wheel on a step corner (textbook statics)",
            requires=[(sp.StrictLessThan(h, r), "step height h must be below the wheel radius r: a rigid wheel cannot climb a step as high as its axle (raise r or lower the step)")],
            symbols=s,
        ),
    ]


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
    ]


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
    return out


def all_formulas() -> list[Formula]:
    return _step_climb() + _mass_props() + _frame_static()

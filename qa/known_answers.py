"""Closed-form answers the solvers must reproduce.

Every expected value is computed here from a formula; no number is typed in
as a result. Each function names where its formula comes from. Units are
N, mm, MPa (= N/mm^2), kg, s unless the name says otherwise.

Sources used below:
- [geom]   plane geometry (Pythagoras), derived in the docstring
- [static] static equilibrium of a rigid body (sum of forces and moments = 0)
- [EB]     Euler-Bernoulli beam theory; the deflection formulas are re-derived
           symbolically from E I w'''' = q in tests/verification/test_known_answers.py
- [SI]     unit definitions as given by scipy.constants (CODATA / SI)
- [vib]    single-degree-of-freedom vibration, f = sqrt(k/m) / (2 pi)
"""

from __future__ import annotations

import math

import scipy.constants as sc

G_N = sc.g  # standard acceleration of gravity, 9.80665 m/s^2 [SI]
G_MM_S2 = G_N * 1000.0


# --- step climb (rigid wheel on a step corner) ---------------------------------

def step_corner_distance(r: float, h: float) -> float:
    """Horizontal distance from the wheel centre to the step corner [geom].

    Wheel of radius r touching the ground and the corner of a step of height h:
    the corner is r - h below the centre and r away from it, so
    d^2 = r^2 - (r - h)^2 = 2 r h - h^2.
    """
    if not 0 <= h < r:
        raise ValueError(f"need 0 <= h < r, got r={r}, h={h}")
    return math.sqrt(2 * r * h - h * h)


def step_force_ratio(r: float, h: float) -> float:
    """F/W = d / (r - h) for a rigid wheel at the moment it leaves the ground [static].

    At lift-off the ground reaction is zero and the axle carries no torque, so the
    corner reaction passes through the wheel centre. Its horizontal part balances
    the push F and its vertical part balances the load W.
    """
    return step_corner_distance(r, h) / (r - h)


# --- static wheel loads -----------------------------------------------------------

def axle_loads(weight: float, wheelbase: float, cg_behind_front: float) -> tuple[float, float]:
    """(front axle, rear axle) load of a two-axle body on level ground [static].

    Moments about the rear axle: front = W b / L with b = L - a,
    a = distance from the front axle back to the centre of gravity.
    """
    a = cg_behind_front
    return weight * (wheelbase - a) / wheelbase, weight * a / wheelbase


def wheel_loads_equal_rates(weight: float, wheelbase: float, track: float,
                            cg_behind_front: float, cg_left: float = 0.0) -> dict[str, float]:
    """Four wheel loads of a rigid body on four equal corner springs [static].

    With equal springs the loads are a plane F = A + B x + C y over the four
    corners (no warp), so the side-to-side shift W y / t is split equally
    between the axles: F_left = axle / 2 + W y / (2 t). For y = 0 this is the
    plain axle load halved, and left equals right exactly.
    """
    front, rear = axle_loads(weight, wheelbase, cg_behind_front)
    shift = weight * cg_left / (2 * track)
    return {"FL": front / 2 + shift, "FR": front / 2 - shift,
            "RL": rear / 2 + shift, "RR": rear / 2 - shift}


# --- sections ----------------------------------------------------------------------

def rect_tube(b: float, h: float, t: float) -> dict[str, float]:
    """Area and second moments of a rectangular tube with sharp corners [geom].

    b = width (along y), h = height (along z), t = wall. Iy bends about y
    (resists vertical load), Iz about z.
    """
    bi, hi = b - 2 * t, h - 2 * t
    return {
        "A": b * h - bi * hi,
        "Iy": (b * h**3 - bi * hi**3) / 12,
        "Iz": (h * b**3 - hi * bi**3) / 12,
        "c_z": h / 2,
        "c_y": b / 2,
    }


def round_tube(d: float, t: float) -> dict[str, float]:
    """Area and second moment of a round tube [geom]."""
    di = d - 2 * t
    i = math.pi * (d**4 - di**4) / 64
    return {"A": math.pi * (d**2 - di**2) / 4, "Iy": i, "Iz": i, "J": 2 * i, "c": d / 2}


# --- beams [EB] ------------------------------------------------------------------------

def simply_supported_centre_load(P: float, L: float, E: float, I: float) -> dict[str, float]:
    """Simply supported span L, point load P at mid-span."""
    return {"deflection": P * L**3 / (48 * E * I), "moment": P * L / 4, "reaction": P / 2}


def simply_supported_uniform_load(w: float, L: float, E: float, I: float) -> dict[str, float]:
    """Simply supported span L, uniform load w (N/mm)."""
    return {"deflection": 5 * w * L**4 / (384 * E * I), "moment": w * L**2 / 8, "reaction": w * L / 2}


def cantilever_tip_load(P: float, L: float, E: float, I: float) -> dict[str, float]:
    """Cantilever of length L, point load P at the free end."""
    return {"deflection": P * L**3 / (3 * E * I), "slope": P * L**2 / (2 * E * I),
            "moment": P * L, "reaction": P}


def portal_sway_fixed_bases(H: float, h: float, E: float, I_col: float) -> float:
    """Sway of a portal frame with a rigid beam and two fixed-base columns of height h.

    Each column is fixed at the foot and guided (no rotation) at the beam, so its
    lateral stiffness is 12 E I / h^3; two columns in parallel give
    delta = H h^3 / (24 E I).
    """
    return H * h**3 / (24 * E * I_col)


def portal_sway_pinned_bases(H: float, h: float, E: float, I_col: float) -> float:
    """Same portal with pinned feet: each column 3 E I / h^3, delta = H h^3 / (6 E I)."""
    return H * h**3 / (6 * E * I_col)


def bending_stress(M: float, I: float, c: float) -> float:
    """Extreme-fibre bending stress M c / I [EB]."""
    return M * c / I


# --- vibration [vib] --------------------------------------------------------------------

def simply_supported_first_frequency(L: float, E: float, I: float, rho_t_mm3: float, A: float) -> float:
    """First bending frequency (Hz) of a simply supported uniform beam.

    f1 = (pi / (2 L^2)) sqrt(E I / (rho A)); in N-mm-t units rho is t/mm^3.
    """
    return math.pi / (2 * L**2) * math.sqrt(E * I / (rho_t_mm3 * A))


def cantilever_tip_mass_frequency(L: float, E: float, I: float, mass_t: float) -> float:
    """Frequency (Hz) of a massless cantilever with a tip mass: k = 3 E I / L^3."""
    return math.sqrt(3 * E * I / (mass_t * L**3)) / (2 * math.pi)


def quarter_car_ride_frequency(k_spring: float, k_tyre: float, sprung_mass: float) -> float:
    """Ride frequency (Hz) with the spring and tyre in series (N/m, kg)."""
    k_ride = k_spring * k_tyre / (k_spring + k_tyre)
    return math.sqrt(k_ride / sprung_mass) / (2 * math.pi)


def quarter_car_two_dof_frequencies(k_spring: float, k_tyre: float, sprung_mass: float,
                                    unsprung_mass: float) -> tuple[float, float]:
    """(body, wheel-hop) frequencies (Hz) of the undamped two-mass quarter car.

    Eigenvalues of M^-1 K with K = [[ks, -ks], [-ks, ks + kt]]. As the unsprung
    mass goes to zero the body mode tends to quarter_car_ride_frequency().
    """
    import numpy as np

    K = np.array([[k_spring, -k_spring], [-k_spring, k_spring + k_tyre]])
    M = np.diag([sprung_mass, unsprung_mass])
    w2 = np.sort(np.linalg.eigvals(np.linalg.solve(M, K)).real)
    return tuple(float(math.sqrt(x) / (2 * math.pi)) for x in w2)


# --- vehicle quasi-statics [static] -------------------------------------------------------

def tilt_angle_rigid_deg(track: float, cg_height: float) -> float:
    """Tilt-table angle where the upper wheels unload, rigid springs and tyres."""
    return math.degrees(math.atan((track / 2) / cg_height))


def braking_distance_m(speed_m_s: float, mu: float) -> float:
    """Stopping distance v^2 / (2 mu g) at a constant deceleration mu g."""
    return speed_m_s**2 / (2 * mu * G_N)


def side_force_N(rho: float, c_y: float, area_m2: float, speed_m_s: float) -> float:
    """Aerodynamic side force 1/2 rho C_y A v^2."""
    return 0.5 * rho * c_y * area_m2 * speed_m_s**2


def free_fall_semi_implicit_euler(y0: float, g: float, dt: float, steps: int) -> float:
    """Height after `steps` semi-implicit Euler steps from rest.

    v_{n+1} = v_n - g dt, y_{n+1} = y_n + v_{n+1} dt  =>  y_N = y0 - g dt^2 N (N + 1) / 2.
    """
    return y0 - g * dt * dt * steps * (steps + 1) / 2


# --- units [SI] --------------------------------------------------------------------------

# Factor to the SI base of each unit in the contract's unit list, plus offset for
# temperatures: si = value * factor + offset. Dimension names group units that
# may be converted into one another.
UNIT_TO_SI: dict[str, tuple[str, float, float]] = {
    "1": ("ratio", 1.0, 0.0),
    "%": ("ratio", 0.01, 0.0),
    "mm": ("length", sc.milli, 0.0),
    "mm^2": ("area", sc.milli**2, 0.0),
    "mm^3": ("length^3", sc.milli**3, 0.0),  # volume or section modulus
    "mm^4": ("length^4", sc.milli**4, 0.0),  # second moment of area
    "m": ("length", 1.0, 0.0),
    "km": ("length", sc.kilo, 0.0),
    "kg": ("mass", 1.0, 0.0),
    "N": ("force", 1.0, 0.0),
    "kN": ("force", sc.kilo, 0.0),
    "N*m": ("moment", 1.0, 0.0),
    "N*mm": ("moment", sc.milli, 0.0),
    "N/mm": ("stiffness", 1.0 / sc.milli, 0.0),
    "MPa": ("pressure", sc.mega, 0.0),
    "Pa": ("pressure", 1.0, 0.0),
    "deg": ("angle", sc.degree, 0.0),
    "rad": ("angle", 1.0, 0.0),
    "s": ("time", 1.0, 0.0),
    "h": ("time", sc.hour, 0.0),
    "m/s": ("speed", 1.0, 0.0),
    "km/h": ("speed", sc.kmh, 0.0),
    "m/s^2": ("acceleration", 1.0, 0.0),
    "g": ("acceleration", sc.g, 0.0),
    "Hz": ("frequency", 1.0, 0.0),
    "rpm": ("frequency", 1.0 / sc.minute, 0.0),  # revolutions per minute as cycles per second
    "W": ("power", 1.0, 0.0),
    "kW": ("power", sc.kilo, 0.0),
    "J": ("energy", 1.0, 0.0),
    "Wh": ("energy", sc.hour, 0.0),
    "kWh": ("energy", sc.kilo * sc.hour, 0.0),
    "V": ("voltage", 1.0, 0.0),
    "A": ("current", 1.0, 0.0),
    "degC": ("temperature", 1.0, sc.zero_Celsius),
    "K": ("temperature", 1.0, 0.0),
    "K/W": ("thermal_resistance", 1.0, 0.0),
}


def convert(value: float, unit_from: str, unit_to: str) -> float:
    """Convert between two contract units of the same dimension [SI]."""
    dim_a, fa, oa = UNIT_TO_SI[unit_from]
    dim_b, fb, ob = UNIT_TO_SI[unit_to]
    if dim_a != dim_b:
        raise ValueError(f"cannot convert {unit_from} ({dim_a}) to {unit_to} ({dim_b})")
    return (value * fa + oa - ob) / fb


# --- the cases solvers are checked against -------------------------------------------------

# Step climb ratios. r = 300 mm with three step heights, and a small caster
# (r = 75 mm, h = 20 mm) as a second product.
STEP_CASES = [
    {"case": "r300_h100", "r": 300.0, "h": 100.0},
    {"case": "r300_h150", "r": 300.0, "h": 150.0},
    {"case": "r300_h250", "r": 300.0, "h": 250.0},
    {"case": "r75_h20", "r": 75.0, "h": 20.0},
]

# Beam cases share one aluminium-like section so every value is easy to recompute.
# E and the section are inputs of the check, not material data claimed for a product.
BEAM = {"E": 69000.0, "I": 1.0e5, "L": 1000.0, "A": 400.0, "rho_t_mm3": 2.7e-9}

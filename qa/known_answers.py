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
- [Kirsch] the plane-stress field around a circular hole in an infinite plate under
           uniaxial tension (Kirsch, 1898); re-derived symbolically from its Airy
           stress function in tests/verification/test_known_answers.py
- [Pilkey] the net-section Kt of a central hole in a finite-width plate, the fit
           Kt_n = 3.000 - 3.140 x + 3.667 x^2 - 1.527 x^3 (x = d/W, 0 <= x <= 1), as
           amesweb.info gives it from W. D. Pilkey, Formulas for Stress, Strain, and
           Structural Matrices, 2nd ed., Wiley, 2005 (the book itself: 確かめていない)
- [thin]   thin-walled sections on their mid-line (what a shell model of the walls
           represents): Bredt's shear flow for a closed cell, derived in the
           docstring, and the webs carrying the shear force
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


# --- plates and shells ---------------------------------------------------------------------

def kirsch_stresses(sigma: float, a: float, r: float, theta: float) -> dict[str, float]:
    """Plane stresses (rr, tt, rt) at (r, theta) around a hole of radius a in an
    infinite plate under uniaxial tension sigma along theta = 0 [Kirsch].

    From the Airy stress function
    phi = sigma / 4 (r^2 - 2 a^2 ln r - (r^2 - 2 a^2 + a^4 / r^2) cos 2 theta):
    rr = phi_r / r + phi_tt / r^2, tt = phi_rr, rt = -(phi_t / r)_r.
    """
    q2, q4, c, s = (a / r) ** 2, (a / r) ** 4, math.cos(2 * theta), math.sin(2 * theta)
    return {"rr": sigma / 2 * (1 - q2) + sigma / 2 * (1 - 4 * q2 + 3 * q4) * c,
            "tt": sigma / 2 * (1 + q2) - sigma / 2 * (1 + 3 * q4) * c,
            "rt": -sigma / 2 * (1 + 2 * q2 - 3 * q4) * s}


def kirsch_kt() -> float:
    """Kt of a circular hole in an infinite plate: the hoop stress at the side of the hole."""
    return kirsch_stresses(1.0, 1.0, 1.0, math.pi / 2)["tt"]


def kirsch_across(sigma: float, a: float, y: float) -> float:
    """Stress along the load, sigma_x, at a distance y >= a from the hole centre on the
    line through the hole across the load (theta = 90 deg) [Kirsch]: how fast the peak
    dies away, for checking a mesh away from the edge of the hole."""
    return kirsch_stresses(sigma, a, y, math.pi / 2)["tt"]


def hole_kt_net(d_over_w: float) -> float:
    """Kt of a central circular hole in a plate of finite width W under tension,
    on the net-section stress P / ((W - d) t) [Pilkey]. 3 at d/W = 0 (Kirsch), 2 at d/W = 1."""
    x = d_over_w
    if not 0 <= x <= 1:
        raise ValueError(f"need 0 <= d/W <= 1, got {x}")
    return 3.000 - 3.140 * x + 3.667 * x**2 - 1.527 * x**3


def hole_kt_gross(d_over_w: float) -> float:
    """The same peak on the gross stress P / (W t): Kt_n / (1 - d/W)."""
    return hole_kt_net(d_over_w) / (1 - d_over_w)


def box_midline(b: float, h: float, t: float) -> dict[str, float]:
    """A rectangular tube as walls of thickness t on its mid-line (a shell model) [thin].

    b = outer width (along y), h = outer height (along z). The walls are b_m = b - t
    and h_m = h - t long between the corner lines. Iy (vertical load) adds the two
    webs (t h_m^3 / 12 each) and the two flanges (b_m t^3 / 12 + b_m t (h_m / 2)^2
    each). A_m is the area the mid-line encloses (for Bredt), A_web the webs that
    carry a vertical shear force.
    """
    bm, hm = b - t, h - t
    return {
        "b_m": bm, "h_m": hm, "A": 2 * t * (bm + hm),
        "Iy": 2 * t * hm**3 / 12 + 2 * (bm * t**3 / 12 + bm * t * (hm / 2) ** 2),
        "Iz": 2 * t * bm**3 / 12 + 2 * (hm * t**3 / 12 + hm * t * (bm / 2) ** 2),
        "A_m": bm * hm, "A_web": 2 * hm * t, "c_z": hm / 2,
    }


def shear_modulus(E: float, nu: float) -> float:
    """G = E / (2 (1 + nu)) of an isotropic material."""
    return E / (2 * (1 + nu))


def cantilever_box_tip_load(P: float, L: float, E: float, nu: float, b: float, h: float, t: float,
                            x: float = 0.0) -> dict[str, float]:
    """A rectangular tube cantilevered at x = 0, a vertical load P at the free end x = L.

    Tip deflection: bending P L^3 / (3 E I) [EB] plus shear P L / (G A_web) [thin] (the
    webs take the shear; an approximation, a few per cent of the shear part). Flange
    stress at the mid-surface at x: M c / I with M = P (L - x) and c = h_m / 2; read it
    at least one tube height from the clamped end, where the clamp's local effect has
    died away.
    """
    s = box_midline(b, h, t)
    bend = P * L**3 / (3 * E * s["Iy"])
    shear = P * L / (shear_modulus(E, nu) * s["A_web"])
    return {"deflection": bend + shear, "deflection_bending": bend, "deflection_shear": shear,
            "moment": P * (L - x), "flange_stress": P * (L - x) * s["c_z"] / s["Iy"]}


def bredt_torsion(T: float, L: float, G: float, b: float, h: float, t: float) -> dict[str, float]:
    """A closed thin-walled rectangular tube of one cell under a torque T [thin] (Bredt).

    The shear flow q = tau t is the same all round the cell (equilibrium of a wall
    element along the tube). Its moment about any point is q times twice the area the
    mid-line encloses: T = 2 A_m q, so tau = T / (2 A_m t). Work: T theta' / 2 equals the
    shear strain energy per length, the sum over the wall of q^2 / (2 G t) ds, so
    theta' = T (sum ds / t) / (4 A_m^2 G) and J = 4 A_m^2 / (sum ds / t), with
    sum ds / t = 2 (b_m + h_m) / t for one wall thickness. A square tube does not warp,
    so a clamped end does not change its twist.

    The walls also twist as thin strips at the same rate, a stiffness in parallel with
    the cell (what a shell model of the walls keeps): a strip of width s and thickness t
    has the Prandtl stress function phi = t^2 / 4 - y^2 across its thickness
    (laplacian -2, zero on both faces), so J_open = 2 * integral of phi = s t^3 / 3,
    leaving out the strip's ends. With both, theta' = T / (G (J + J_open)), and the cell
    carries T J / (J + J_open), so the membrane shear is that share of tau. The "_walls"
    values are these.
    """
    s = box_midline(b, h, t)
    perimeter = 2 * (s["b_m"] + s["h_m"])
    J = 4 * s["A_m"] ** 2 * t / perimeter
    J_open = perimeter * t**3 / 3
    q = T / (2 * s["A_m"])
    share = J / (J + J_open)
    return {"shear_flow": q, "shear_stress": q / t, "J": J, "twist_rate": T / (G * J), "twist": T * L / (G * J),
            "J_open": J_open, "twist_rate_walls": T / (G * (J + J_open)), "shear_stress_walls": share * q / t}


# --- element quality [geom] -----------------------------------------------------------------
# Single elements whose measures follow from their shape (qa.mesh_quality must give them), and
# bad ones a mesh check must catch. Points in mm.

def rectangle_quad(a: float, b: float) -> list[list[float]]:
    """a x b rectangle, counter-clockwise seen from +z: aspect a/b (a >= b), every angle 90 deg,
    jacobian ratio 1, warp 0, area a b."""
    return [[0, 0, 0], [a, 0, 0], [a, b, 0], [0, b, 0]]


def rhombus_quad(s: float, alpha_deg: float) -> list[list[float]]:
    """Rhombus of side s with corner angle alpha at node 0: angles alpha and 180 - alpha, aspect 1,
    jacobian ratio 1 (a parallelogram), area s^2 sin(alpha)."""
    c, d = math.cos(math.radians(alpha_deg)), math.sin(math.radians(alpha_deg))
    return [[0, 0, 0], [s, 0, 0], [s + s * c, s * d, 0], [s * c, s * d, 0]]


def folded_quad(theta_deg: float) -> list[list[float]]:
    """Square of diagonal 2 folded along its diagonal 0-2 (on the x axis) by theta: the two
    triangles of that diagonal meet at theta, so warp is at least theta."""
    t = math.radians(theta_deg)
    return [[-1, 0, 0], [0, -1, 0], [1, 0, 0], [0, math.cos(t), math.sin(t)]]


BAD_ELEMENTS = {
    # node 3 moved onto node 0: a quad collapsed into a triangle (an edge of length 0)
    "collapsed_quad": [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 0, 0]],
    # nodes 2 and 3 swapped: the edges cross (a bow-tie); two corners turn the other way
    "bowtie_quad": [[0, 0, 0], [10, 0, 0], [0, 10, 0], [10, 10, 0]],
    # node 2 pulled in past the diagonal 1-3: a re-entrant (arrow-head) quad
    "reentrant_quad": [[0, 0, 0], [10, 0, 0], [2, 2, 0], [0, 10, 0]],
}


def unit_hex(size: float = 1.0) -> list[list[float]]:
    """Cube: 0-3 the bottom counter-clockwise seen from above, node i + 4 above node i. Every
    corner jacobian is size^3, the volume size^3."""
    s = size
    bottom = [[0, 0, 0], [s, 0, 0], [s, s, 0], [0, s, 0]]
    return bottom + [[x, y, s] for x, y, _ in bottom]


def inverted_hex(size: float = 1.0) -> list[list[float]]:
    """The cube with its top and bottom faces swapped: inside out, every corner jacobian and the
    volume are -size^3."""
    h = unit_hex(size)
    return h[4:] + h[:4]


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
    "m^2": ("area", 1.0, 0.0),
    "kg/m^3": ("density", 1.0, 0.0),
    "kg*m^2": ("inertia", 1.0, 0.0),
    "N/rad": ("force_per_angle", 1.0, 0.0),  # cornering stiffness
    "ohm": ("resistance", 1.0, 0.0),
    "Ah": ("charge", sc.hour, 0.0),  # ampere-hour = 3600 C
    "J/K": ("heat_capacity", 1.0, 0.0),
    # kept apart from Hz and rpm (cycles per second): one cycle is 2 pi rad, so a shared
    # dimension would convert 1 Hz to 1 rad/s
    "rad/s": ("angular_rate", 1.0, 0.0),
    "deg/s": ("angular_rate", sc.degree, 0.0),
    "rad/s^2": ("angular_acceleration", 1.0, 0.0),
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

# Shell cases (inputs of the checks, like BEAM). Plates with a central hole under a
# tension sigma at both ends, long enough (4 W) for the ends not to reach the hole.
# A small hole for Kirsch, a wide one for the finite-width fit.
SHELL = {"E": 205000.0, "nu": 0.3}
HOLE_CASES = [
    {"case": "small_hole", "W": 400.0, "d": 20.0, "length": 1600.0, "t": 2.0, "sigma": 100.0},
    {"case": "wide_hole", "W": 100.0, "d": 50.0, "length": 400.0, "t": 2.0, "sigma": 100.0},
]
# Square tube 40 x 40 x 2 mm, 800 mm long, clamped at one end: a tip load and a tip torque.
BOX = {"b": 40.0, "h": 40.0, "t": 2.0, "L": 800.0, "P": 500.0, "T": 200000.0}

"""K0 formulas for stability and longitudinal performance: tilt, crosswind,
lane change, braking, launch."""

from __future__ import annotations

import math

import sympy as sp
from scipy.integrate import quad
from scipy.optimize import brentq

from .registry import Formula
from .units import STANDARD_GRAVITY

G = sp.Float(STANDARD_GRAVITY)
G_NOTE = "g = 9.80665 m/s^2"
RHO_INPUT = {"unit": "kg/m^3", "min": 0,
             "description": "air density (1.225 at sea level and 15 degC in the Manual of the ICAO Standard Atmosphere, "
                            "ICAO Doc 7488; not checked in this repository)"}


def _sym(*names: str) -> dict[str, sp.Symbol]:
    return {n: sp.Symbol(n, real=True) for n in names}


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


# ---------------------------------------------------------------- tilt

def _roll_stiffness(k_wheel_N_mm: float, t_mm: float, n_axles: float) -> float:
    """Roll stiffness from the wheel rates, N*mm/rad: each axle k t^2 / 2."""
    return n_axles * k_wheel_N_mm * t_mm**2 / 2.0


def _tilt_with_roll(v: dict) -> dict:
    """Tilt-table angle with the body rolling on its springs (exact statics, solved for theta)."""
    t, h, e, m, ms, hs, k, n = (v[x] for x in ("t", "h", "e", "m", "m_s", "h_s", "k", "n"))
    Kphi = _roll_stiffness(k, t, n)
    w_s = ms * STANDARD_GRAVITY  # N; moments in N*mm

    c = w_s * hs / Kphi  # < 1 is required (checked), so the fixed point converges

    def roll(theta: float) -> float:
        # K_phi phi = m_s g h_s sin(theta + phi)
        phi = 0.0
        for _ in range(500):
            nxt = c * math.sin(theta + phi)
            if abs(nxt - phi) < 1e-15:
                return nxt
            phi = nxt
        return phi

    def margin(theta: float) -> float:
        phi = roll(theta)
        dy = (ms / m) * hs * math.sin(phi)
        dz = -(ms / m) * hs * (1.0 - math.cos(phi))
        return (t / 2.0 - e - dy) * math.cos(theta) - (h + dz) * math.sin(theta)

    upper = math.atan2(t / 2.0 - e, h)
    theta = brentq(margin, 0.0, upper, xtol=1e-14) if margin(upper) < 0 else upper
    return {"theta": math.degrees(theta), "roll": math.degrees(roll(theta)), "ssf": math.tan(theta)}


def _tilt() -> list[Formula]:
    out = []
    s = _sym("t", "h", "e")
    t, h, e = s["t"], s["h"], s["e"]
    out.append(Formula(
        formula_id="tilt.rigid_angle",
        title="傾斜台で倒れる角度 (剛体)",
        domain="handling",
        expression="theta = atan((t/2 - e)/h); ssf = (t/2 - e)/h",
        inputs={"t": _in("mm", "track (same front and rear)", min=0),
                "h": _in("mm", "height of the centre of gravity", min=0),
                "e": _in("mm", "sideways offset of the centre of gravity toward the low side (0 when centred)")},
        outputs={"theta": ("deg", "table angle at which the high-side wheels lift", sp.atan((t / 2 - e) / h) * 180 / sp.pi),
                 "ssf": ("1", "static stability factor (t/2 - e)/h (T/2h when centred)", (t / 2 - e) / h)},
        assumptions=["rigid suspension and tyres", "the vehicle tips about the line through the low-side tyre contacts",
                     "same track front and rear", "the tyres do not slide (check tilt.slide_angle)"],
        source="moment balance about the low-side contact line (textbook statics)",
        requires=[(sp.And(e < t / 2, h > 0), "the centre of gravity must be inside the track (e < t/2) and above the ground (h > 0)")],
        symbols=s,
    ))
    out.append(Formula(
        formula_id="tilt.spring_roll_angle",
        title="傾斜台で倒れる角度 (ばねで車体がロールする分を入れる)",
        domain="handling",
        expression="tan(theta) = (t/2 - e - (m_s/m) h_s sin(phi)) / (h - (m_s/m) h_s (1 - cos(phi))); K_phi phi = m_s g h_s sin(theta + phi); K_phi = n k t^2 / 2",
        inputs={"t": _in("mm", "track (same front and rear)", min=0),
                "h": _in("mm", "height of the centre of gravity", min=0),
                "e": _in("mm", "sideways offset of the centre of gravity toward the low side (0 when centred)"),
                "m": _in("kg", "total mass", min=0),
                "m_s": _in("kg", "sprung mass (on the springs)", min=0),
                "h_s": _in("mm", "height of the sprung centre of gravity above the roll axis", min=0),
                "k": _in("N/mm", "vertical rate of one wheel's suspension (at the wheel)", min=0),
                "n": _in("1", "number of axles", min=1)},
        outputs={"theta": ("deg", "table angle at which the high-side wheels lift", None),
                 "roll": ("deg", "body roll on the springs at that angle", None),
                 "ssf": ("1", "tan(theta), the effective stability factor", None)},
        assumptions=["the body rolls about a fixed roll axis on linear springs; tyres are rigid",
                     "the unsprung mass stays with the axles", "tips about the low-side contact line",
                     "same track front and rear", G_NOTE],
        source="moment balance about the low-side contact line with roll equilibrium of the sprung mass (textbook vehicle dynamics)",
        requires=[(lambda v: v["e"] < v["t"] / 2 and v["h"] > 0, "the centre of gravity must be inside the track and above the ground"),
                  (lambda v: v["m_s"] <= v["m"], "the sprung mass cannot exceed the total mass"),
                  (lambda v: _roll_stiffness(v["k"], v["t"], v["n"]) > v["m_s"] * STANDARD_GRAVITY * v["h_s"],
                   "the springs are too soft to hold the body up (n k t^2/2 must exceed m_s g h_s)")],
        fn=_tilt_with_roll,
    ))
    s = _sym("m", "t", "h", "e", "beta")
    m, t, h, e, beta = (s[x] for x in ("m", "t", "h", "e", "beta"))
    br = beta * sp.pi / 180
    out.append(Formula(
        formula_id="tilt.side_slope_uphill_load",
        title="横の斜面に止まっているときの、上の側の輪の荷重の和",
        domain="handling",
        expression="W_up = m g (cos(beta) (t/2 - e) - sin(beta) h) / t",
        inputs={"m": _in("kg", "total mass", min=0),
                "t": _in("mm", "track (same front and rear)", min=0),
                "h": _in("mm", "height of the centre of gravity above the slope", min=0),
                "e": _in("mm", "sideways offset of the centre of gravity toward the low side (0 when centred)"),
                "beta": _in("deg", "side slope angle", min=0)},
        outputs={"W_up": ("N", "load on the uphill wheels together (they lift at 0)",
                          m * G * (sp.cos(br) * (t / 2 - e) - sp.sin(br) * h) / t)},
        assumptions=["rigid suspension and tyres", "standing still on a plane side slope",
                     "moment balance about the line through the downhill tyre contacts", "same track front and rear",
                     G_NOTE],
        source="moment balance about the downhill contact line (textbook statics)",
        requires=[(sp.And(t > 0, e < t / 2), "the track must be positive and the centre of gravity inside it")],
        symbols=s,
    ))
    s = _sym("mu")
    out.append(Formula(
        formula_id="tilt.slide_angle",
        title="傾斜台で滑り出す角度",
        domain="handling",
        expression="theta = atan(mu)",
        inputs={"mu": _in("1", "friction coefficient between tyre and table", min=0)},
        outputs={"theta": ("deg", "table angle at which the vehicle slides sideways", sp.atan(s["mu"]) * 180 / sp.pi)},
        assumptions=["Coulomb friction", "if this is below the tip angle, the vehicle slides before it tips"],
        source="friction on an inclined plane (textbook statics)",
        symbols=s,
    ))
    return out


# ---------------------------------------------------------------- crosswind

def _crosswind() -> list[Formula]:
    out = []
    s = _sym("C_y", "A", "v", "rho")
    Cy, A, v, rho = s["C_y"], s["A"], s["v"], s["rho"]
    out.append(Formula(
        formula_id="crosswind.side_force",
        title="横風の力",
        domain="aero",
        expression="F = 1/2 rho C_y A v^2",
        inputs={"C_y": _in("1", "side force coefficient (take the unfavourable end of its range)", min=0),
                "A": _in("m^2", "side area seen by the wind", min=0),
                "v": _in("m/s", "wind speed relative to the vehicle, across it", min=0),
                "rho": RHO_INPUT},
        outputs={"F": ("N", "side force", rho * Cy * A * v**2 / 2)},
        assumptions=["steady wind across the vehicle"],
        source="aerodynamic force F = 1/2 rho C A v^2 (textbook fluid mechanics)",
        symbols=s,
    ))
    s = _sym("m", "t", "F", "h_cp")
    m, t, F, hcp = s["m"], s["t"], s["F"], s["h_cp"]
    out.append(Formula(
        formula_id="crosswind.windward_wheel_load",
        title="横風を受けたときの風上の輪の荷重",
        domain="aero",
        expression="W_windward = m g / 2 - F h_cp / t",
        inputs={"m": _in("kg", "total mass", min=0), "t": _in("mm", "track", min=0),
                "F": _in("N", "side force of the wind", min=0),
                "h_cp": _in("mm", "height of the centre of pressure above the ground", min=0)},
        outputs={"W_windward": ("N", "load left on the windward wheels together (lift when it reaches 0)", m * G / 2 - F * hcp / t)},
        assumptions=["rigid vehicle, no roll", "centre of gravity on the centre line", "the vehicle tips about the leeward contact line",
                     "no aerodynamic lift", G_NOTE],
        source="moment balance about the leeward contact line (textbook statics)",
        requires=[(t > 0, "track t must be positive")],
        symbols=s,
    ))
    s = _sym("m", "t", "C_y", "A", "h_cp", "rho")
    m, t, Cy, A, hcp, rho = (s[k] for k in ("m", "t", "C_y", "A", "h_cp", "rho"))
    out.append(Formula(
        formula_id="crosswind.critical_wind_speed",
        title="風上の輪が浮く風速",
        domain="aero",
        expression="v_crit = sqrt(m g t / (rho C_y A h_cp))",
        inputs={"m": _in("kg", "total mass", min=0), "t": _in("mm", "track", min=0),
                "C_y": _in("1", "side force coefficient (take the unfavourable end of its range)", min=0),
                "A": _in("m^2", "side area seen by the wind", min=0),
                "h_cp": _in("mm", "height of the centre of pressure above the ground", min=0),
                "rho": RHO_INPUT},
        outputs={"v_crit": ("m/s", "relative wind speed at which the windward wheels lift",
                            sp.sqrt(m * G * t / (rho * Cy * A * hcp)))},
        assumptions=["steady wind across the vehicle", "rigid vehicle, no roll", "centre of gravity on the centre line",
                     "no aerodynamic lift", G_NOTE],
        source="1/2 rho C_y A v^2 h_cp = m g t / 2 (textbook statics and fluid mechanics)",
        requires=[(sp.And(Cy > 0, A > 0, hcp > 0, rho > 0), "C_y, A, h_cp and rho must be positive")],
        symbols=s,
    ))
    return out


# ---------------------------------------------------------------- air drag

def _drag() -> list[Formula]:
    s = _sym("C_d", "A", "v", "rho")
    Cd, A, v, rho = s["C_d"], s["A"], s["v"], s["rho"]
    return [Formula(
        formula_id="drag.force",
        title="空気抵抗の力",
        domain="aero",
        expression="F = 1/2 rho C_d A v^2",
        inputs={"C_d": _in("1", "drag coefficient", min=0),
                "A": _in("m^2", "frontal area", min=0),
                "v": _in("m/s", "air speed relative to the vehicle, along it (the driving speed in still air)", min=0),
                "rho": RHO_INPUT},
        outputs={"F": ("N", "drag force", rho * Cd * A * v**2 / 2)},
        assumptions=["steady flow along the vehicle", "still air"],
        source="drag equation F = 1/2 rho C_d A v^2 (textbook fluid mechanics)",
        symbols=s,
    )]


# ---------------------------------------------------------------- lane change

def _lane_change() -> list[Formula]:
    out = []
    s = _sym("m", "a_y", "h", "t")
    m, ay, h, t = (s[k] for k in ("m", "a_y", "h", "t"))
    out.append(Formula(
        formula_id="lane_change.inner_wheel_load",
        title="曲がっているときの内側の輪の荷重 (剛体)",
        domain="handling",
        expression="W_inner = m g / 2 - m a_y h / t; a_y_lift = t / (2 h)",
        inputs={"m": _in("kg", "total mass", min=0), "a_y": _in("g", "lateral acceleration", min=0),
                "h": _in("mm", "height of the centre of gravity", min=0), "t": _in("mm", "track", min=0)},
        outputs={"W_inner": ("N", "load on the inner wheels together (they lift at 0)", m * G / 2 - m * ay * G * h / t),
                 "a_y_lift": ("g", "lateral acceleration at which the inner wheels lift", t / (2 * h))},
        assumptions=["steady cornering", "rigid suspension and tyres", "centre of gravity on the centre line", G_NOTE],
        source="moment balance about the outer contact line (textbook vehicle dynamics)",
        requires=[(sp.And(h > 0, t > 0), "h and t must be positive")],
        symbols=s,
    ))
    s = _sym("t", "h", "m", "m_s", "h_s", "k", "n")
    t, h, m, ms, hs, k, n = (s[x] for x in ("t", "h", "m", "m_s", "h_s", "k", "n"))
    Kphi = n * k * t**2 / 2
    R = ms * G * hs / (Kphi - ms * G * hs)  # roll angle (rad) per 1 g of lateral acceleration
    ay_lift = (t / 2) / (h + (ms / m) * hs * R)
    out.append(Formula(
        formula_id="lane_change.rollover_threshold",
        title="内側の輪が浮く横加速度 (ばねで車体がロールする分を入れる)",
        domain="handling",
        expression="R = m_s g h_s / (K_phi - m_s g h_s); a_y_lift = (t/2) / (h + (m_s/m) h_s R); K_phi = n k t^2 / 2",
        inputs={"t": _in("mm", "track (same front and rear)", min=0),
                "h": _in("mm", "height of the centre of gravity", min=0),
                "m": _in("kg", "total mass", min=0),
                "m_s": _in("kg", "sprung mass (on the springs)", min=0),
                "h_s": _in("mm", "height of the sprung centre of gravity above the roll axis", min=0),
                "k": _in("N/mm", "vertical rate of one wheel's suspension (at the wheel)", min=0),
                "n": _in("1", "number of axles", min=1)},
        outputs={"a_y_lift": ("g", "lateral acceleration at which the inner wheels lift", ay_lift),
                 "roll": ("deg", "body roll at that acceleration", R * ay_lift * 180 / sp.pi)},
        assumptions=["steady cornering", "small roll angle (linear)", "the body rolls about a fixed roll axis; tyres are rigid",
                     "centre of gravity on the centre line", G_NOTE],
        source="steady-state rollover threshold with suspension roll (textbook vehicle dynamics)",
        requires=[(Kphi > ms * G * hs, "the springs are too soft to hold the body up (n k t^2/2 must exceed m_s g h_s)"),
                  (sp.And(ms <= m, h > 0), "the sprung mass cannot exceed the total mass, and h must be positive")],
        symbols=s,
    ))
    s = _sym("v", "Y", "X")
    v, Y, X = s["v"], s["Y"], s["X"]
    vm = v / 3.6
    out.append(Formula(
        formula_id="lane_change.peak_lateral_acceleration",
        title="車線変更の道筋で要る横加速度の最大",
        domain="handling",
        expression="y(x) = Y/2 (1 - cos(pi x / X)); a_y_peak = v^2 * Y/2 * (pi/X)^2",
        inputs={"v": _in("km/h", "speed", min=0), "Y": _in("mm", "sideways shift of the lane", min=0),
                "X": _in("mm", "length over which the shift is made", min=0)},
        outputs={"a_y_peak": ("g", "largest lateral acceleration on the path", vm**2 * (Y / 2000) * (sp.pi / (X / 1000)) ** 2 / G)},
        assumptions=["the vehicle follows a half-cosine path exactly at constant speed", "small path angle", G_NOTE],
        source="curvature of y = Y/2 (1 - cos(pi x / X)) times v^2 (geometry)",
        requires=[(X > 0, "the shift length X must be positive")],
        symbols=s,
    ))
    s = _sym("L", "R", "W_f", "W_r", "C_f", "C_r", "v")
    L, R, Wf, Wr, Cf, Cr, v = (s[x] for x in ("L", "R", "W_f", "W_r", "C_f", "C_r", "v"))
    deg = 180 / sp.pi
    K = Wf / Cf - Wr / Cr  # rad per g
    ay = (v / 3.6) ** 2 / R / G
    out.append(Formula(
        formula_id="lane_change.steady_steer",
        title="一定の円を回るときの舵角 (2 輪モデル)",
        domain="handling",
        expression="delta = L/R + K a_y; K = W_f/C_af - W_r/C_ar; a_y = v^2/(R g)",
        inputs={"L": _in("mm", "wheelbase", min=0), "R": _in("m", "radius of the circle", min=0),
                "W_f": _in("N", "front axle load", min=0), "W_r": _in("N", "rear axle load", min=0),
                "C_f": _in("N/rad", "cornering stiffness of the front axle (both tyres)", min=0),
                "C_r": _in("N/rad", "cornering stiffness of the rear axle (both tyres)", min=0),
                "v": _in("km/h", "speed", min=0)},
        outputs={"delta": ("deg", "road-wheel steer angle", (L / 1000 / R + K * ay) * deg),
                 "K": ("deg", "understeer gradient: extra steer per 1 g (positive = understeer)", K * deg),
                 "a_y": ("g", "lateral acceleration", ay)},
        assumptions=["linear tyres (cornering force proportional to slip angle)", "small angles", "steady state",
                     "no load transfer effect on cornering stiffness", G_NOTE],
        source="steady-state bicycle model (textbook vehicle dynamics)",
        requires=[(sp.And(R > 0, Cf > 0, Cr > 0), "R, C_f and C_r must be positive")],
        symbols=s,
    ))
    return out


# ---------------------------------------------------------------- braking

def _braking() -> list[Formula]:
    out = []
    s = _sym("v", "mu", "t_r")
    v, mu, tr = s["v"], s["mu"], s["t_r"]
    vm = v / 3.6
    out.append(Formula(
        formula_id="braking.stopping_distance",
        title="急ブレーキで止まるまでの距離",
        domain="handling",
        expression="s = v t_r + v^2 / (2 mu g)",
        inputs={"v": _in("km/h", "initial speed", min=0), "mu": _in("1", "friction coefficient (take the unfavourable end)", min=0),
                "t_r": _in("s", "delay before full braking (reaction and brake build-up)", min=0)},
        outputs={"s": ("m", "stopping distance", vm * tr + vm**2 / (2 * mu * G)),
                 "s_braking": ("m", "distance while braking", vm**2 / (2 * mu * G)),
                 "a_x": ("g", "deceleration while braking", mu)},
        assumptions=["all wheels braked at the friction limit (no lock-up loss, no ABS cycling)", "level road",
                     "constant friction", G_NOTE],
        source="constant deceleration kinematics (textbook)",
        terms={"delay": vm * tr, "braking": vm**2 / (2 * mu * G)},
        requires=[(mu > 0, "mu must be positive")],
        symbols=s,
    ))
    s = _sym("m", "a", "L", "h", "a_x")
    m, a, L, h, ax = (s[x] for x in ("m", "a", "L", "h", "a_x"))
    out.append(Formula(
        formula_id="braking.load_transfer",
        title="ブレーキで前に移る荷重と、後輪が浮く減速度",
        domain="handling",
        expression="W_front = m g (L - a)/L + m a_x h / L; W_rear = m g a / L - m a_x h / L; a_x_tip = a / h",
        inputs={"m": _in("kg", "total mass", min=0),
                "a": _in("mm", "distance from the front axle back to the centre of gravity", min=0),
                "L": _in("mm", "wheelbase", min=0), "h": _in("mm", "height of the centre of gravity", min=0),
                "a_x": _in("g", "deceleration", min=0)},
        outputs={"W_rear": ("N", "rear axle load while braking (lifts at 0)", m * G * a / L - m * ax * G * h / L),
                 "W_front": ("N", "front axle load while braking", m * G * (L - a) / L + m * ax * G * h / L),
                 "a_x_tip": ("g", "deceleration at which the rear wheels lift (forward tip)", a / h)},
        assumptions=["rigid body, level road", "pitch from the springs ignored", G_NOTE],
        source="moment balance about the front contact line (textbook vehicle dynamics)",
        requires=[(sp.And(a >= 0, a <= L, h > 0), "0 <= a <= L and h > 0")],
        symbols=s,
    ))
    s = _sym("a", "L", "h", "grade")
    a, L, h, gr = (s[x] for x in ("a", "L", "h", "grade"))
    th = sp.atan(gr / 100)
    rear = a * sp.cos(th) + h * sp.sin(th)  # rear axle load / (m g) * L, facing uphill
    front = (L - a) * sp.cos(th) - h * sp.sin(th)
    out.append(Formula(
        formula_id="braking.parking_friction",
        title="坂に上り向きで止まっているのに要る摩擦係数 (全輪・後輪だけ・前輪だけのブレーキ)",
        domain="handling",
        expression=("theta = atan(grade); mu_all = tan(theta); mu_rear = sin(theta) L / (a cos(theta) + h sin(theta)); "
                    "mu_front = sin(theta) L / ((L - a) cos(theta) - h sin(theta))"),
        inputs={"a": _in("mm", "distance from the front axle back to the centre of gravity", min=0),
                "L": _in("mm", "wheelbase", min=0), "h": _in("mm", "height of the centre of gravity", min=0),
                "grade": _in("%", "grade (rise over run)", min=0)},
        outputs={"mu_all": ("1", "friction needed with all wheels braked", sp.tan(th)),
                 "mu_rear": ("1", "friction needed with the rear wheels braked only", sp.sin(th) * L / rear),
                 "mu_front": ("1", "friction needed with the front wheels braked only", sp.sin(th) * L / front)},
        assumptions=["rigid body standing still, facing uphill", "the braked wheels hold the whole slope force",
                     "the mass cancels out"],
        source="force and moment balance on a slope (textbook statics)",
        requires=[(sp.And(a >= 0, a <= L, h > 0), "0 <= a <= L and h > 0"),
                  (front > 0, "the vehicle tips backward on this grade (the front wheels lift)")],
        symbols=s,
    ))
    return out


# ---------------------------------------------------------------- launch

def _drive_force(v: float, F_max: float, P_w: float, eta: float) -> float:
    """Tractive force at speed v (m/s): torque-limited, then power-limited."""
    if v <= 0:
        return F_max
    return min(F_max, eta * P_w / v)


def _resistance(v: float, m: float, Crr: float, CdA_m2: float, grade: float, rho: float) -> float:
    th = math.atan(grade)
    return m * STANDARD_GRAVITY * (Crr * math.cos(th) + math.sin(th)) + 0.5 * rho * CdA_m2 * v * v


def _launch(v: dict) -> dict:
    m, F_max, P, eta, Crr, Cd, A, grade, lam, v_t, rho = (
        v[x] for x in ("m", "F_max", "P", "eta", "C_rr", "C_d", "A", "grade", "lambda", "v", "rho"))
    P_w, CdA, gr, vt = P * 1000.0, Cd * A, grade / 100.0, v_t / 3.6
    net = lambda u: _drive_force(u, F_max, P_w, eta) - _resistance(u, m, Crr, CdA, gr, rho)
    if net(0.0) <= 0:
        raise ValueError("the drive force cannot overcome the resistance at standstill (raise F_max or lower the grade)")
    vtop = _top_speed(net)
    if vt >= vtop:
        raise ValueError(f"the target speed is not reached: top speed is {vtop * 3.6:.1f} km/h")
    vb = eta * P_w / F_max  # corner speed where the power limit starts
    pts = [x for x in (vb,) if 0 < x < vt]
    t = quad(lambda u: lam * m / net(u), 0.0, vt, points=pts or None, limit=200)[0]
    s = quad(lambda u: lam * m * u / net(u), 0.0, vt, points=pts or None, limit=200)[0]
    return {"t": t, "s": s, "a_mean": vt / t}


def _top_speed(net) -> float:
    """Speed (m/s) where drive force equals resistance; inf if none below 1000 m/s."""
    return brentq(net, 1e-9, 1000.0, xtol=1e-12) if net(1000.0) < 0 else math.inf


def _top(v: dict) -> dict:
    m, P, eta, Crr, Cd, A, grade, rho = (v[x] for x in ("m", "P", "eta", "C_rr", "C_d", "A", "grade", "rho"))
    net = lambda u: (eta * P * 1000.0 / u if u > 0 else math.inf) - _resistance(u, m, Crr, Cd * A, grade / 100.0, rho)
    vtop = _top_speed(net)
    return {"v_top": vtop * 3.6, "F": eta * P * 1000.0 / vtop}


def _launch_formulas() -> list[Formula]:
    return [Formula(
        formula_id="launch.time_to_speed",
        title="止まった所から目標の速さまでの時間と最高速度",
        domain="powertrain",
        expression="t = integral lambda m dv / (F(v) - R(v)); F(v) = min(F_max, eta P / v); R(v) = m g (C_rr cos + sin) + 1/2 rho C_d A v^2",
        inputs={"m": _in("kg", "total mass", min=0),
                "F_max": _in("N", "largest tractive force at the wheels (torque limit)", min=0),
                "P": _in("kW", "motor power (power limit)", min=0),
                "eta": _in("1", "efficiency from motor shaft to wheels", min=0, max=1),
                "C_rr": _in("1", "rolling resistance coefficient", min=0),
                "C_d": _in("1", "drag coefficient", min=0),
                "A": _in("m^2", "frontal area", min=0),
                "grade": _in("%", "road grade (rise over run)", min=0),
                "lambda": _in("1", "mass factor for rotating parts (1 = none)", min=1),
                "v": _in("km/h", "target speed", min=0),
                "rho": RHO_INPUT},
        outputs={"t": ("s", "time from standstill to the target speed", None),
                 "s": ("m", "distance covered meanwhile", None),
                 "a_mean": ("m/s^2", "mean acceleration", None)},
        assumptions=["full drive force from standstill, no wheel spin (traction not checked)",
                     "drive force torque-limited, then power-limited", "steady grade, no wind", G_NOTE],
        source="Newton's second law integrated over speed (textbook vehicle performance)",
        requires=[(lambda v: v["F_max"] > 0 and v["P"] > 0 and v["eta"] > 0, "F_max, P and eta must be positive")],
        fn=_launch,
    ), Formula(
        formula_id="launch.top_speed",
        title="出力で決まる最高速度",
        domain="powertrain",
        expression="eta P = (m g (C_rr cos + sin) + 1/2 rho C_d A v^2) v, solved for v",
        inputs={"m": _in("kg", "total mass", min=0),
                "P": _in("kW", "motor power", min=0),
                "eta": _in("1", "efficiency from motor shaft to wheels", min=0, max=1),
                "C_rr": _in("1", "rolling resistance coefficient", min=0),
                "C_d": _in("1", "drag coefficient", min=0),
                "A": _in("m^2", "frontal area", min=0),
                "grade": _in("%", "road grade (rise over run)", min=0),
                "rho": RHO_INPUT},
        outputs={"v_top": ("km/h", "top speed (motor speed limits not included)", None),
                 "F": ("N", "tractive force at top speed", None)},
        assumptions=["power-limited at top speed", "motor and gear speed limits not included", "steady grade, no wind",
                     G_NOTE],
        source="power balance eta P = road load times speed (textbook vehicle performance)",
        requires=[(lambda v: v["P"] > 0 and v["eta"] > 0 and v["C_d"] * v["A"] > 0,
                   "P, eta and the drag area C_d A must be positive (without drag there is no top speed)")],
        fn=_top,
    )]


def _parts_reached(v: dict) -> dict:
    line = v["d_water"] + (v["V"] / 3.6) ** 2 / (2.0 * float(G)) * 1000.0
    return {"n_reached": float(sum(v[h] <= line for h in ("h_1", "h_2", "h_3"))), "water_line": line}


def _flooded() -> list[Formula]:
    return [Formula(
        formula_id="flooded.parts_reached",
        title="冠水路で水が届く部品の数",
        domain="aero",
        expression="n = the number of parts with h_i <= d_water + V^2 / (2 g)",
        inputs={"h_1": _in("mm", "height of the first part's lower face above the road", min=0),
                "h_2": _in("mm", "height of the second part's lower face above the road", min=0),
                "h_3": _in("mm", "height of the third part's lower face above the road", min=0),
                "d_water": _in("mm", "depth of the still water on the road", min=0),
                "V": _in("km/h", "the vehicle's speed through the water", min=0)},
        outputs={"n_reached": ("1", "how many of the parts the water can climb to", None),
                 "water_line": ("mm", "the highest water can climb on a blunt front: the still water plus the velocity head", None)},
        assumptions=["water meeting a blunt front at the vehicle's speed climbs by its velocity head, the upper end: it stands for every part, wherever it is on the vehicle",
                     "the vehicle level on the flat of the road", G_NOTE],
        source="Bernoulli's velocity head V^2 / 2 g (textbook fluid mechanics)",
        fn=_parts_reached,
    )]


def all_formulas() -> list[Formula]:
<<<<<<< HEAD
    return _tilt() + _crosswind() + _drag() + _lane_change() + _braking() + _launch_formulas()
=======
    return _tilt() + _crosswind() + _lane_change() + _braking() + _launch_formulas() + _flooded()
>>>>>>> d94c9e91

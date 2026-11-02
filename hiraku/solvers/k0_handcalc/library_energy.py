"""K0 formulas for energy and heat: drive energy over a loop, battery power at a
steady speed, steady and step temperature rise of a part."""

from __future__ import annotations

import sympy as sp

from .library_dynamics import G, G_NOTE, RHO_INPUT
from .registry import Formula
from .units import STANDARD_GRAVITY


def _sym(*names: str) -> dict[str, sp.Symbol]:
    return {n: sp.Symbol(n, real=True) for n in names}


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def _drive_energy() -> list[Formula]:
    out = []
    s = _sym("m", "d", "v", "C_rr", "C_d", "A", "H", "eta", "eta_regen", "E_batt", "rho")
    m, d, v, Crr, Cd, A, H, eta, eta_r, Eb, rho = (
        s[k] for k in ("m", "d", "v", "C_rr", "C_d", "A", "H", "eta", "eta_regen", "E_batt", "rho"))
    J_TO_WH = sp.Rational(1, 3600)
    vm = v / 3.6
    rolling = m * G * Crr * d * 1000 / eta * J_TO_WH
    aero = rho * Cd * A * vm**2 / 2 * d * 1000 / eta * J_TO_WH
    climb = m * G * H / eta * J_TO_WH
    regen = -eta_r * m * G * H * J_TO_WH
    E = rolling + aero + climb + regen
    out.append(Formula(
        formula_id="drive_energy.loop_energy",
        title="周回を走る電力量 (電力 × 時間の和)",
        domain="powertrain",
        expression="E = [(m g C_rr + 1/2 rho C_d A v^2) d + m g H] / eta - eta_regen m g H",
        inputs={"m": _in("kg", "total mass", min=0),
                "d": _in("km", "distance driven", min=0),
                "v": _in("km/h", "typical speed (for the air drag)", min=0),
                "C_rr": _in("1", "rolling resistance coefficient", min=0),
                "C_d": _in("1", "drag coefficient", min=0),
                "A": _in("m^2", "frontal area", min=0),
                "H": _in("m", "total height climbed over the distance (the loop comes back down as much)", min=0),
                "eta": _in("1", "efficiency from battery to wheels", min=0, max=1),
                "eta_regen": _in("1", "share of the descent's potential energy returned to the battery (0 without regeneration)", min=0, max=1),
                "E_batt": _in("kWh", "usable battery energy", min=0),
                "rho": RHO_INPUT},
        outputs={"E": ("Wh", "energy taken from the battery", E),
                 "soc_used": ("%", "share of the battery used", E / (Eb * 1000) * 100),
                 "range": ("km", "distance the battery lasts at this rate", d * Eb * 1000 / E)},
        assumptions=["constant speed, accelerations ignored", "small grades (cos = 1)",
                     "the descent returns eta_regen of its potential energy, after nothing else", "no auxiliary loads",
                     G_NOTE],
        source="sum of resistance work over the distance (textbook vehicle performance)",
        terms={"rolling": rolling, "aero": aero, "climb": climb, "regen": regen},
        requires=[(sp.And(eta > 0, Eb > 0), "eta and E_batt must be positive")],
        symbols=s,
    ))
    s = _sym("m", "v", "grade", "C_rr", "C_d", "A", "eta", "rho")
    m, v, gr, Crr, Cd, A, eta, rho = (s[k] for k in ("m", "v", "grade", "C_rr", "C_d", "A", "eta", "rho"))
    vm = v / 3.6
    th = sp.atan(gr / 100)
    F_roll = m * G * Crr * sp.cos(th)
    F_grade = m * G * sp.sin(th)
    F_aero = rho * Cd * A * vm**2 / 2
    out.append(Formula(
        formula_id="drive_energy.steady_power",
        title="一定の速さ・坂で走るときの電池の電力",
        domain="powertrain",
        expression="P = (m g (C_rr cos(theta) + sin(theta)) + 1/2 rho C_d A v^2) v / eta; theta = atan(grade)",
        inputs={"m": _in("kg", "total mass", min=0), "v": _in("km/h", "speed", min=0),
                "grade": _in("%", "road grade (rise over run, uphill positive)"),
                "C_rr": _in("1", "rolling resistance coefficient", min=0), "C_d": _in("1", "drag coefficient", min=0),
                "A": _in("m^2", "frontal area", min=0),
                "eta": _in("1", "efficiency from battery to wheels", min=0, max=1),
                "rho": RHO_INPUT},
        outputs={"P": ("kW", "battery power", (F_roll + F_grade + F_aero) * vm / eta / 1000),
                 "F": ("N", "tractive force at the wheels", F_roll + F_grade + F_aero)},
        assumptions=["steady speed", "no auxiliary loads", "driving (not regenerating)", G_NOTE],
        source="road load times speed (textbook vehicle performance)",
        terms={"rolling": F_roll * vm / eta / 1000, "grade": F_grade * vm / eta / 1000, "aero": F_aero * vm / eta / 1000},
        requires=[(eta > 0, "eta must be positive")],
        symbols=s,
    ))
    return out


def _thermal() -> list[Formula]:
    out = []
    s = _sym("P", "R", "T_amb")
    P, R, Ta = s["P"], s["R"], s["T_amb"]
    out.append(Formula(
        formula_id="thermal.steady_temperature",
        title="損失 × 熱抵抗で出す定常の温度",
        domain="thermal",
        expression="T = T_amb + P R",
        inputs={"P": _in("W", "heat loss in the part (mean over the use)", min=0),
                "R": _in("K/W", "thermal resistance from the part to the air", min=0),
                "T_amb": _in("degC", "air temperature")},
        outputs={"T": ("degC", "steady temperature of the part", Ta + P * R),
                 "dT": ("K", "temperature rise above the air (a difference)", P * R)},
        assumptions=["one lumped temperature per part", "steady state (long use)", "constant thermal resistance"],
        source="lumped thermal resistance (textbook heat transfer)",
        symbols=s,
    ))
    s = _sym("P", "R", "tau", "t", "T_amb")
    P, R, tau, t, Ta = (s[k] for k in ("P", "R", "tau", "t", "T_amb"))
    rise = P * R * (1 - sp.exp(-t / tau))
    out.append(Formula(
        formula_id="thermal.rc_step",
        title="一定の損失をかけたときの温度の上がり方 (熱容量と熱抵抗)",
        domain="thermal",
        expression="T(t) = T_amb + P R (1 - exp(-t/tau)); tau = R C",
        inputs={"P": _in("W", "heat loss in the part", min=0),
                "R": _in("K/W", "thermal resistance from the part to the air", min=0),
                "tau": _in("s", "thermal time constant R C", min=0),
                "t": _in("s", "time since the loss started", min=0),
                "T_amb": _in("degC", "air temperature (and the starting temperature)")},
        outputs={"T": ("degC", "temperature at time t", Ta + rise),
                 "dT": ("K", "temperature rise above the air at time t (a difference)", rise)},
        assumptions=["one lumped temperature per part", "constant loss from t = 0", "starts at the air temperature"],
        source="first-order RC thermal network step response (textbook heat transfer)",
        requires=[(tau > 0, "tau must be positive")],
        symbols=s,
    ))
    s = _sym("P_out", "eta")
    Po, eta = s["P_out"], s["eta"]
    out.append(Formula(
        formula_id="thermal.loss_from_efficiency",
        title="効率から出す損失",
        domain="thermal",
        expression="P_loss = P_out (1/eta - 1)",
        inputs={"P_out": _in("kW", "output power", min=0), "eta": _in("1", "efficiency at that output", min=0, max=1)},
        outputs={"P_loss": ("W", "heat loss", Po * 1000 * (1 / eta - 1))},
        assumptions=["all losses become heat in the part"],
        source="energy balance (textbook)",
        requires=[(eta > 0, "eta must be positive")],
        symbols=s,
    ))
    return out


def _soc_end() -> list[Formula]:
    s = _sym("E", "E_batt", "soc_start")
    E, Eb, s0 = s["E"], s["E_batt"], s["soc_start"]
    return [Formula(
        formula_id="drive_energy.soc_end",
        title="走ったあとの電池の残量",
        domain="powertrain",
        expression="soc_end = soc_start - E / E_batt",
        inputs={"E": _in("Wh", "energy taken from the battery", min=0),
                "E_batt": _in("kWh", "usable battery energy", min=0),
                "soc_start": _in("%", "state of charge at the start", min=0, max=100)},
        outputs={"soc_end": ("%", "state of charge at the end (below 0: the battery runs out first)",
                             s0 - E / (Eb * 1000) * 100)},
        assumptions=["the usable energy is the nominal voltage times the capacity"],
        source="energy balance (arithmetic)",
        requires=[(Eb > 0, "E_batt must be positive")],
        symbols=s,
    )]


LOOP_PARTS = ("battery", "motor", "controller")


def _loop_temperature(v: dict) -> dict:
    """Quasi-steady drive round the forest loop: the road load at each section's target speed
    through the efficiency chain, the battery current from (V - R I) I = P, losses averaged
    over the time; each part then warms as a first-order RC from the air temperature."""
    import math

    from ..k3_power_thermal.cycle import forest_loop, repeat

    segs = repeat(forest_loop(v["grade_low"] / 100, v["grade_high"] / 100, v["v_low"] / 3.6, v["v_high"] / 3.6),
                  v["d"] * 1000)
    t = loss_b = loss_m = loss_c = 0.0
    for s in segs:
        th = math.atan(s.grade)
        F = v["m"] * STANDARD_GRAVITY * (v["C_rr"] * math.cos(th) + math.sin(th)) + 0.5 * v["rho"] * v["C_d"] * v["A"] * s.speed**2
        P = F * s.speed
        if P >= 0:
            shaft = P / v["eta_d"]
            elec = shaft / v["eta_m"]
            p_in = elec / v["eta_c"]
            P_b, l_m, l_c = p_in + v["P_aux"], elec - shaft, p_in - elec
        else:  # regenerating, the losses shared as in driving
            P_b = P * v["eta_regen"] + v["P_aux"]
            l_m, l_c = -P * v["eta_d"] * (1 - v["eta_m"]), -P * v["eta_d"] * v["eta_m"] * (1 - v["eta_c"])
        disc = v["V"] ** 2 - 4 * v["R_b"] * P_b
        if disc < 0:
            raise ValueError(f"the battery cannot deliver {P_b / 1000:.1f} kW on a {s.grade * 100:g} % section")
        current = (v["V"] - math.sqrt(disc)) / (2 * v["R_b"]) if v["R_b"] > 0 else P_b / v["V"]
        dt = s.length / s.speed
        t += dt
        loss_b += current**2 * v["R_b"] * dt
        loss_m += l_m * dt
        loss_c += l_c * dt
    out = {"t": t}
    for part, loss in zip(LOOP_PARTS, (loss_b / t, loss_m / t, loss_c / t)):
        R, C = v[f"R_{part}"], v[f"C_{part}"]
        out[f"P_{part}"] = loss
        out[f"T_{part}"] = v["T_amb"] + loss * R * (1 - math.exp(-t / (R * C)))
    return out


def _loop_thermal() -> list[Formula]:
    inputs = {
        "m": _in("kg", "total mass", min=0), "C_rr": _in("1", "rolling resistance coefficient", min=0),
        "C_d": _in("1", "drag coefficient", min=0), "A": _in("m^2", "frontal area", min=0), "rho": RHO_INPUT,
        "eta_d": _in("1", "drivetrain efficiency", min=0, max=1), "eta_m": _in("1", "motor efficiency", min=0, max=1),
        "eta_c": _in("1", "controller efficiency", min=0, max=1),
        "eta_regen": _in("1", "share of the braking power at the wheels returned to the battery", min=0, max=1),
        "P_aux": _in("W", "auxiliary load", min=0), "V": _in("V", "battery voltage (open circuit)", min=0),
        "R_b": _in("ohm", "battery internal resistance", min=0),
        "d": _in("km", "distance driven round the 2 km forest loop", min=0),
        "grade_low": _in("%", "grade of the gentle climb and descent", min=0),
        "grade_high": _in("%", "grade of the steep climb and descent", min=0),
        "v_low": _in("km/h", "speed on the steep grades", min=0), "v_high": _in("km/h", "speed on the flat parts", min=0),
        "T_amb": _in("degC", "air temperature (and the parts' starting temperature)"),
    }
    for part in LOOP_PARTS:
        inputs[f"R_{part}"] = _in("K/W", f"thermal resistance from the {part} to the air", min=0)
        inputs[f"C_{part}"] = _in("J/K", f"heat capacity of the {part}", min=0)
    outputs = {"t": ("s", "time round the loop at the target speeds", None)}
    for part in LOOP_PARTS:
        outputs[f"P_{part}"] = ("W", f"mean heat loss in the {part}", None)
        outputs[f"T_{part}"] = ("degC", f"temperature of the {part} at the end of the drive", None)
    return [Formula(
        formula_id="thermal.loop_steady_temperature",
        title="林道の周回を一定の速さで走るとみたときの、電池・モーター・制御器の損失と温度",
        domain="thermal",
        expression=("per section: F = m g (C_rr cos(th) + sin(th)) + rho C_d A v^2 / 2, P = F v through the efficiency chain, "
                    "(V - R_b I) I = P_batt; losses averaged over the time t; T = T_amb + P_loss R (1 - exp(-t / (R C)))"),
        inputs=inputs,
        outputs=outputs,
        assumptions=["each section is driven at its target speed (no accelerating or braking)",
                     "the loop of the K3 drive (2 km laps: flat, gentle and steep climbs and descents)",
                     "each part is one lumped temperature with a constant mean loss, so the end is its warmest",
                     G_NOTE],
        source="road load times speed (textbook vehicle performance) and a first-order RC thermal network (textbook heat transfer)",
        requires=[(lambda v: v["d"] > 0 and v["v_low"] > 0 and v["v_high"] > 0 and v["V"] > 0,
                   "distance, speeds and voltage must be positive")],
        fn=_loop_temperature,
    )]


def all_formulas() -> list[Formula]:
    return _drive_energy() + _thermal() + _soc_end() + _loop_thermal()

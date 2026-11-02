"""K0 formulas for energy and heat: drive energy over a loop, battery power at a
steady speed, steady and step temperature rise of a part."""

from __future__ import annotations

import sympy as sp

from .library_dynamics import G, G_NOTE, RHO, RHO_NOTE
from .registry import Formula


def _sym(*names: str) -> dict[str, sp.Symbol]:
    return {n: sp.Symbol(n, real=True) for n in names}


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def _drive_energy() -> list[Formula]:
    out = []
    s = _sym("m", "d", "v", "C_rr", "C_d", "A", "H", "eta", "eta_regen", "E_batt")
    m, d, v, Crr, Cd, A, H, eta, eta_r, Eb = (s[k] for k in ("m", "d", "v", "C_rr", "C_d", "A", "H", "eta", "eta_regen", "E_batt"))
    J_TO_WH = sp.Rational(1, 3600)
    vm = v / 3.6
    rolling = m * G * Crr * d * 1000 / eta * J_TO_WH
    aero = RHO * Cd * A * 1e-6 * vm**2 / 2 * d * 1000 / eta * J_TO_WH
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
                "A": _in("mm^2", "frontal area", min=0),
                "H": _in("m", "total height climbed over the distance (the loop comes back down as much)", min=0),
                "eta": _in("1", "efficiency from battery to wheels", min=0, max=1),
                "eta_regen": _in("1", "share of the descent's potential energy returned to the battery (0 without regeneration)", min=0, max=1),
                "E_batt": _in("kWh", "usable battery energy", min=0)},
        outputs={"E": ("Wh", "energy taken from the battery", E),
                 "soc_used": ("%", "share of the battery used", E / (Eb * 1000) * 100),
                 "range": ("km", "distance the battery lasts at this rate", d * Eb * 1000 / E)},
        assumptions=["constant speed, accelerations ignored", "small grades (cos = 1)",
                     "the descent returns eta_regen of its potential energy, after nothing else", "no auxiliary loads",
                     RHO_NOTE, G_NOTE],
        source="sum of resistance work over the distance (textbook vehicle performance)",
        terms={"rolling": rolling, "aero": aero, "climb": climb, "regen": regen},
        requires=[(sp.And(eta > 0, Eb > 0), "eta and E_batt must be positive")],
        symbols=s,
    ))
    s = _sym("m", "v", "grade", "C_rr", "C_d", "A", "eta")
    m, v, gr, Crr, Cd, A, eta = (s[k] for k in ("m", "v", "grade", "C_rr", "C_d", "A", "eta"))
    vm = v / 3.6
    th = sp.atan(gr / 100)
    F_roll = m * G * Crr * sp.cos(th)
    F_grade = m * G * sp.sin(th)
    F_aero = RHO * Cd * A * 1e-6 * vm**2 / 2
    out.append(Formula(
        formula_id="drive_energy.steady_power",
        title="一定の速さ・坂で走るときの電池の電力",
        domain="powertrain",
        expression="P = (m g (C_rr cos(theta) + sin(theta)) + 1/2 rho C_d A v^2) v / eta; theta = atan(grade)",
        inputs={"m": _in("kg", "total mass", min=0), "v": _in("km/h", "speed", min=0),
                "grade": _in("%", "road grade (rise over run, uphill positive)"),
                "C_rr": _in("1", "rolling resistance coefficient", min=0), "C_d": _in("1", "drag coefficient", min=0),
                "A": _in("mm^2", "frontal area", min=0),
                "eta": _in("1", "efficiency from battery to wheels", min=0, max=1)},
        outputs={"P": ("kW", "battery power", (F_roll + F_grade + F_aero) * vm / eta / 1000),
                 "F": ("N", "tractive force at the wheels", F_roll + F_grade + F_aero)},
        assumptions=["steady speed", "no auxiliary loads", "driving (not regenerating)", RHO_NOTE, G_NOTE],
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


def all_formulas() -> list[Formula]:
    return _drive_energy() + _thermal()

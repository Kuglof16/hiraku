"""K0 formulas for fatigue: constant-amplitude damage on an IIW-type S-N curve and
the number of load cycles over a distance."""

from __future__ import annotations

import sympy as sp

from .registry import Formula


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def _damage(v: dict) -> dict:
    from ..k1_frame.fatigue import sn_cycles

    N = sn_cycles(v["dS"], v["FAT"], v["m"], v["N_knee"], v["m2"])
    return {"D": v["n"] / N, "N": N}


N0_PER_M = 0.1  # reference spatial frequency of ISO 8608 (cycle/m)
ROAD_BAND = (0.011, 2.83)  # cycle/m: the road model's band (a modelling choice, as in shelf/fatigue.py)
BAND_POINTS = 4000


def _road_damage(v: dict) -> dict:
    """One quarter car on an ISO 8608 road (G_d(n) = G_d(n0) (n/n0)^-2): the spread of the
    dynamic tyre force from the frequency response, the weld stress spread in proportion to
    the static stress per static load, and the Miner damage of Rayleigh cycles over the distance."""
    import math

    import numpy as np

    from ..k1_frame.fatigue import sn_cycles

    g = 9.80665
    W, m_u = v["W"], v["m_u"]
    m_s = W / g - m_u
    if m_s <= 0:
        raise ValueError("the wheel load must be more than the unsprung weight")
    k_s, k_t = v["k_s"] * 1e3, v["k_t"] * 1e3  # N/m
    c = 2.0 * v["zeta"] * math.sqrt(k_s * m_s)
    speed = v["v"] / 3.6
    n = np.linspace(ROAD_BAND[0], ROAD_BAND[1], BAND_POINTS)
    f = n * speed
    s = 2j * np.pi * f
    to_body = (c * s + k_s) / (m_s * s**2 + c * s + k_s)
    wheel = k_t / (m_u * s**2 + c * s + k_s + k_t - (c * s + k_s) * to_body)
    gain = np.abs(k_t * (1.0 - wheel)) ** 2  # (dynamic tyre force / road height)^2
    G_z = v["G_d"] * 1e-9 * (n / N0_PER_M) ** -2.0 / speed  # m^2 per Hz
    m0 = float(np.trapezoid(gain * G_z, f))
    m2 = float(np.trapezoid(f**2 * gain * G_z, f))
    sigma_F = math.sqrt(m0)
    nu0 = math.sqrt(m2 / m0)
    sigma_s = v["sigma_w"] / W * sigma_F
    a = np.linspace(sigma_s * 1e-4, 8.0 * sigma_s, BAND_POINTS)  # stress amplitudes, Rayleigh
    p = a / sigma_s**2 * np.exp(-a**2 / (2.0 * sigma_s**2))
    inv_n = np.array([1.0 / sn_cycles(2.0 * x, v["FAT"], v["m"], v["N_knee"], v["m2"]) for x in a])
    cycles = nu0 * v["d"] * 1000.0 / speed
    return {"D": cycles * float(np.trapezoid(p * inv_n, a)), "sigma_s": sigma_s, "sigma_F": sigma_F, "nu0": nu0,
            "n": cycles}


def all_formulas() -> list[Formula]:
    s = {k: sp.Symbol(k, real=True) for k in ("d", "v", "f")}
    d, v, f = s["d"], s["v"], s["f"]
    return [
        Formula(
            formula_id="fatigue.road_damage_quarter_car",
            title="1/4 車で見た悪路の疲労の損傷 (静的な応力 × 輪の力のゆれ、レイリー分布の繰り返し)",
            domain="strength",
            expression=("sigma_F^2 = integral |H_F(f)|^2 G_z(f) df (quarter car, G_d(n) = G_d(n0) (n/n0)^-2); "
                        "sigma_s = sigma_w sigma_F / W; nu0 = sqrt(m2/m0); D = nu0 (d/v) E[1/N(2a)], a ~ Rayleigh(sigma_s)"),
            inputs={"sigma_w": _in("MPa", "stress at the weld under the wheel's static load", min=0),
                    "W": _in("N", "static load of that wheel", min=0),
                    "m_u": _in("kg", "unsprung mass of that wheel", min=0),
                    "k_s": _in("N/mm", "wheel rate of its suspension", min=0),
                    "zeta": _in("1", "damping ratio of the sprung mass on that spring", min=0),
                    "k_t": _in("N/mm", "tyre stiffness", min=0),
                    "G_d": _in("mm^3", "road displacement PSD at n0 = 0.1 cycle/m (ISO 8608 class)", min=0),
                    "v": _in("km/h", "speed", min=0), "d": _in("km", "distance", min=0),
                    "FAT": _in("MPa", "fatigue class: stress range at 2e6 cycles", min=0),
                    "m": _in("1", "slope of the S-N curve", min=0),
                    "N_knee": _in("1", "cycles at the knee of the curve", min=0),
                    "m2": _in("1", "slope beyond the knee", min=0)},
            outputs={"D": ("1", "Miner damage over the distance (failure at 1)", None),
                     "sigma_s": ("MPa", "spread (standard deviation) of the weld stress", None),
                     "sigma_F": ("N", "spread of the dynamic tyre force", None),
                     "nu0": ("Hz", "mean rate of load cycles", None),
                     "n": ("1", "number of load cycles over the distance", None)},
            assumptions=["one wheel as a linear quarter car; the weld stress follows that wheel's force in proportion "
                         "to its static stress", "the other wheels and the body's pitch are left out",
                         "narrow-band cycles: Rayleigh amplitudes at the mean crossing rate",
                         "IIW-type S-N curve and Miner's rule, no mean stress correction"],
            source=("random vibration of a linear system (textbook): output spread from the frequency response and the "
                    "input spectrum; narrow-band Miner damage of Rayleigh cycles (textbook fatigue); the road spectrum form "
                    "of ISO 8608 and the S-N curve of the IIW recommendations (A. F. Hobbacher, 2nd ed., Springer 2016), "
                    "neither checked in this repository"),
            requires=[(lambda v: v["W"] > 0 and v["v"] > 0 and v["sigma_w"] > 0, "W, v and sigma_w must be positive")],
            fn=_road_damage,
        ),
        Formula(
            formula_id="fatigue.constant_amplitude_damage",
            title="一定の応力の幅をくり返したときの疲労の損傷 (マイナー則)",
            domain="strength",
            expression="N = 2e6 (FAT / dS)^m above the knee, N_knee (dS_knee / dS)^m2 below; D = n / N",
            inputs={"dS": _in("MPa", "stress range (peak to peak)", min=0),
                    "n": _in("1", "number of cycles", min=0),
                    "FAT": _in("MPa", "fatigue class: stress range at 2e6 cycles", min=0),
                    "m": _in("1", "slope of the S-N curve", min=0),
                    "N_knee": _in("1", "cycles at the knee of the curve", min=0),
                    "m2": _in("1", "slope beyond the knee", min=0)},
            outputs={"D": ("1", "Miner damage (failure at 1)", None), "N": ("1", "cycles to failure at this range", None)},
            assumptions=["constant stress range", "IIW-type S-N curve for welded joints (no mean stress correction)",
                         "linear damage accumulation (Miner)"],
            source=("S-N curve N = 2e6 (FAT / range)^m and Miner's rule: A. F. Hobbacher, Recommendations for Fatigue Design "
                    "of Welded Joints and Components, 2nd ed., Springer 2016 (IIW document IIW-2259-15); "
                    "not checked in this repository"),
            requires=[(lambda v: v["dS"] > 0 and v["FAT"] > 0 and v["m"] > 0 and v["N_knee"] > 0 and v["m2"] > 0,
                       "dS, FAT, m, N_knee and m2 must be positive")],
            fn=_damage,
        ),
        Formula(
            formula_id="fatigue.cycles_over_distance",
            title="走る距離で受ける荷重のくり返しの回数",
            domain="strength",
            expression="n = f d / v",
            inputs={"d": _in("km", "distance", min=0), "v": _in("km/h", "speed", min=0),
                    "f": _in("Hz", "frequency of the dominant load swing (e.g. body bounce or wheel hop)", min=0)},
            outputs={"n": ("1", "number of cycles", f * d / v * 3600)},
            assumptions=["one load cycle per period of the dominant frequency", "constant speed"],
            source="cycles = frequency x time (arithmetic)",
            requires=[(v > 0, "v must be positive")],
            symbols=s,
        ),
    ]

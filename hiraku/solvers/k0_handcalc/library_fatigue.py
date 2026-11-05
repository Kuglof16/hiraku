"""K0 formulas for fatigue: constant-amplitude damage on an IIW-type S-N curve,
road (variable-amplitude) damage and the number of load cycles over a distance.

Slope below the knee: for a stress spectrum summed by Miner's rule the S-N curve
is continued below the knee with m2 = 2 m - 1 (5 for m = 3): IIW-1823-07 (2008),
Recommendations for Fatigue Design of Welded Joints and Components, sec. 4.3.1,
PDF p.110; the value was read in that document by the quality department. The
steeper slope of the design variable fatigue_slope_after_knee_constant_amplitude (22 in the demo
designs) is the constant-amplitude, very-high-cycle slope (same document, sec. 3.2,
PDF p.44) and is used only by fatigue.constant_amplitude_damage.
"""

from __future__ import annotations

import sympy as sp

from .registry import Formula


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def spectrum_slope(m: float) -> float:
    """S-N slope below the knee for Miner summation of a stress spectrum: 2 m - 1 (IIW-1823-07 sec. 4.3.1, p.110)."""
    return 2.0 * m - 1.0


def _damage(v: dict) -> dict:
    from ..k1_frame.fatigue import sn_cycles

    N = sn_cycles(v["dS"], v["FAT"], v["m"], v["N_knee"], v["m2"])
    return {"D": v["n"] / N, "N": N}


# reference spatial frequency n0 of ISO 8608 (cycle/m), as cited; the ISO 8608 text was not read in this repository
# (not checked)
N0_PER_M = 0.1
# cycle/m: the road model's band, HIRAKU's choice as in shelf/fatigue.py, meant as ISO 8608's band; whether
# ISO 8608 gives this band was not checked in this repository
ROAD_BAND = (0.011, 2.83)
BAND_POINTS = 4000


def _road_band(G_d: float, v_kmh: float):
    """Spatial frequencies n (cycle/m), time frequencies f (Hz), s = 2 pi i f and the road height PSD G_z (m^2/Hz)
    of an ISO 8608 road G_d(n) = G_d(n0) (n/n0)^-2 (G_d in mm^3) driven at v_kmh."""
    import numpy as np

    speed = v_kmh / 3.6
    n = np.linspace(ROAD_BAND[0], ROAD_BAND[1], BAND_POINTS)
    f = n * speed
    return n, f, 2j * np.pi * f, G_d * 1e-9 * (n / N0_PER_M) ** -2.0 / speed


def _tyre_force_gain(W: float, m_u: float, k_s_N_mm: float, zeta: float, k_t_N_mm: float, s):
    """Dynamic tyre force per road height (N/m, complex) of a linear quarter car at s = 2 pi i f."""
    import math

    g = 9.80665  # standard gravity, the value of units.STANDARD_GRAVITY (not checked, see there)
    m_s = W / g - m_u
    if m_s <= 0:
        raise ValueError("the wheel load must be more than the unsprung weight")
    k_s, k_t = k_s_N_mm * 1e3, k_t_N_mm * 1e3  # N/m
    c = 2.0 * zeta * math.sqrt(k_s * m_s)
    to_body = (c * s + k_s) / (m_s * s**2 + c * s + k_s)
    wheel = k_t / (m_u * s**2 + c * s + k_s + k_t - (c * s + k_s) * to_body)
    return k_t * (1.0 - wheel)


def _rayleigh_damage(sigma_s: float, cycles: float, v: dict) -> float:
    """Miner damage of `cycles` cycles whose amplitudes are Rayleigh with spread sigma_s (narrow band)."""
    import numpy as np

    from ..k1_frame.fatigue import sn_cycles

    a = np.linspace(sigma_s * 1e-4, 8.0 * sigma_s, BAND_POINTS)  # stress amplitudes, Rayleigh
    p = a / sigma_s**2 * np.exp(-a**2 / (2.0 * sigma_s**2))
    m2 = spectrum_slope(v["m"])  # a spectrum, not constant amplitude: the design's m2 is not used
    inv_n = np.array([1.0 / sn_cycles(2.0 * x, v["FAT"], v["m"], v["N_knee"], m2) for x in a])
    return cycles * float(np.trapezoid(p * inv_n, a))


def _road_damage(v: dict) -> dict:
    """One quarter car on an ISO 8608 road (G_d(n) = G_d(n0) (n/n0)^-2): the spread of the
    dynamic tyre force from the frequency response, the weld stress spread in proportion to
    the static stress per static load, and the Miner damage of Rayleigh cycles over the distance."""
    import math

    import numpy as np

    W = v["W"]
    _, f, s, G_z = _road_band(v["G_d"], v["v"])
    gain = np.abs(_tyre_force_gain(W, v["m_u"], v["k_s"], v["zeta"], v["k_t"], s)) ** 2  # (dynamic tyre force / road height)^2
    m0 = float(np.trapezoid(gain * G_z, f))
    m2 = float(np.trapezoid(f**2 * gain * G_z, f))
    sigma_F = math.sqrt(m0)
    nu0 = math.sqrt(m2 / m0)
    sigma_s = v["sigma_w"] / W * sigma_F
    cycles = nu0 * v["d"] * 1000.0 / (v["v"] / 3.6)
    return {"D": _rayleigh_damage(sigma_s, cycles, v), "sigma_s": sigma_s, "sigma_F": sigma_F, "nu0": nu0, "n": cycles}


def _road_damage_four(v: dict) -> dict:
    """Four quarter cars on an ISO 8608 road, left and right tracks independent, the rear wheel on the
    front wheel's track a wheelbase later: the weld stress PSD from each wheel's stress per kN, its
    spread and mean rate of cycles, and the Miner damage of Rayleigh cycles over the distance."""
    import math

    import numpy as np

    _, f, s, G_z = _road_band(v["G_d"], v["v"])
    H_f = _tyre_force_gain(v["W_f"], v["m_uf"], v["k_sf"], v["zeta_f"], v["k_t"], s)
    H_r = _tyre_force_gain(v["W_r"], v["m_ur"], v["k_sr"], v["zeta_r"], v["k_t"], s)
    lag = np.exp(-s * (v["L"] / 1000.0) / (v["v"] / 3.6))
    per_N = 1e-3  # the stresses are per kN
    amp_a = (v["sigma_fa"] * H_f + v["sigma_ra"] * H_r * lag) * per_N
    amp_b = (v["sigma_fb"] * H_f + v["sigma_rb"] * H_r * lag) * per_N
    S = (np.abs(amp_a) ** 2 + np.abs(amp_b) ** 2) * G_z  # MPa^2 per Hz
    m0 = float(np.trapezoid(S, f))
    m2 = float(np.trapezoid(f**2 * S, f))
    sigma_s = math.sqrt(m0)
    nu0 = math.sqrt(m2 / m0)
    cycles = nu0 * v["d"] * 1000.0 / (v["v"] / 3.6)
    return {"D": _rayleigh_damage(sigma_s, cycles, v), "sigma_s": sigma_s, "nu0": nu0, "n": cycles}


def all_formulas() -> list[Formula]:
    s = {k: sp.Symbol(k, real=True) for k in ("d", "v", "f")}
    d, v, f = s["d"], s["v"], s["f"]
    return [
        Formula(
            formula_id="fatigue.road_damage_quarter_car",
            title="1/4 車で見た悪路の疲労の損傷 (静的な応力 × 輪の力のゆれ、レイリー分布の繰り返し)",
            domain="strength",
            expression=("sigma_F^2 = integral |H_F(f)|^2 G_z(f) df (quarter car, G_d(n) = G_d(n0) (n/n0)^-2); "
                        "sigma_s = sigma_w sigma_F / W; nu0 = sqrt(m2/m0); D = nu0 (d/v) E[1/N(2a)], a ~ Rayleigh(sigma_s); "
                        "N: slope m above the knee, 2 m - 1 below"),
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
                    "N_knee": _in("1", "cycles at the knee of the curve", min=0)},
            outputs={"D": ("1", "Miner damage over the distance (failure at 1)", None),
                     "sigma_s": ("MPa", "spread (standard deviation) of the weld stress", None),
                     "sigma_F": ("N", "spread of the dynamic tyre force", None),
                     "nu0": ("Hz", "mean rate of load cycles", None),
                     "n": ("1", "number of load cycles over the distance", None)},
            assumptions=["one wheel as a linear quarter car; the weld stress follows that wheel's force in proportion "
                         "to its static stress", "the other wheels and the body's pitch are left out",
                         "narrow-band cycles: Rayleigh amplitudes at the mean crossing rate",
                         "IIW-type S-N curve and Miner's rule, no mean stress correction; below the knee the curve goes on with "
                         "slope 2 m - 1 (IIW-1823-07 (2008) sec. 4.3.1, p.110, for Miner summation of a spectrum); the "
                         "design's slope after the knee (constant amplitude) is not used"],
            source=("random vibration of a linear system (textbook): output spread from the frequency response and the "
                    "input spectrum; narrow-band Miner damage of Rayleigh cycles (textbook fatigue); the road spectrum form "
                    "of ISO 8608 (not checked in this repository); the S-N curve of the IIW recommendations: m, the knee "
                    "and the slope 2 m - 1 below it for a spectrum as in IIW-1823-07 (2008) sec. 3.2 and 4.3.1, "
                    "pp.42 and 110, read there by the quality department; the 2016 edition (A. F. Hobbacher, 2nd ed., "
                    "Springer) not checked"),
            requires=[(lambda v: v["W"] > 0 and v["v"] > 0 and v["sigma_w"] > 0, "W, v and sigma_w must be positive")],
            fn=_road_damage,
        ),
        Formula(
            formula_id="fatigue.road_damage_four_wheels",
            title="4 輪の 1/4 車で見た悪路の疲労の損傷 (輪ごとの 1 kN あたりの応力、左右は別の道、後ろは前と同じ道)",
            domain="strength",
            expression=("S_sigma(f) = sum over the two tracks |sigma_f H_f(f) + sigma_r H_r(f) exp(-2 pi i f L / v)|^2 G_z(f) "
                        "(H: quarter-car tyre force per road height, G_d(n) = G_d(n0) (n/n0)^-2); sigma_s^2 = m0, "
                        "nu0 = sqrt(m2/m0); D = nu0 (d/v) E[1/N(2a)], a ~ Rayleigh(sigma_s); N: slope m above the knee, "
                        "2 m - 1 below"),
            inputs={"sigma_ra": _in("MPa", "weld stress for 1 kN up at rear wheel a"),
                    "sigma_rb": _in("MPa", "weld stress for 1 kN up at rear wheel b"),
                    "sigma_fa": _in("MPa", "weld stress for 1 kN up at front wheel a (the track of rear a)"),
                    "sigma_fb": _in("MPa", "weld stress for 1 kN up at front wheel b"),
                    "W_f": _in("N", "static load of a front wheel", min=0),
                    "m_uf": _in("kg", "unsprung mass of a front wheel", min=0),
                    "k_sf": _in("N/mm", "wheel rate of a front suspension", min=0),
                    "zeta_f": _in("1", "damping ratio of the sprung mass on a front spring", min=0),
                    "W_r": _in("N", "static load of a rear wheel", min=0),
                    "m_ur": _in("kg", "unsprung mass of a rear wheel", min=0),
                    "k_sr": _in("N/mm", "wheel rate of a rear suspension", min=0),
                    "zeta_r": _in("1", "damping ratio of the sprung mass on a rear spring", min=0),
                    "k_t": _in("N/mm", "tyre stiffness", min=0),
                    "L": _in("mm", "wheelbase (the rear wheel meets the front wheel's road L / v later)", min=0),
                    "G_d": _in("mm^3", "road displacement PSD at n0 = 0.1 cycle/m (ISO 8608 class)", min=0),
                    "v": _in("km/h", "speed", min=0), "d": _in("km", "distance", min=0),
                    "FAT": _in("MPa", "fatigue class: stress range at 2e6 cycles", min=0),
                    "m": _in("1", "slope of the S-N curve", min=0),
                    "N_knee": _in("1", "cycles at the knee of the curve", min=0)},
            outputs={"D": ("1", "Miner damage over the distance (failure at 1)", None),
                     "sigma_s": ("MPa", "spread (standard deviation) of the weld stress", None),
                     "nu0": ("Hz", "mean rate of load cycles", None),
                     "n": ("1", "number of load cycles over the distance", None)},
            assumptions=["each wheel a linear quarter car; the weld stress is linear in the four dynamic tyre forces",
                         "left and right tracks independent; the rear wheel runs on the front wheel's track",
                         "the two sides' quarter cars alike (one set of front and one of rear values)",
                         "narrow-band cycles: Rayleigh amplitudes at the mean crossing rate (more damage than "
                         "rainflow counting gives for a broad-band stress)",
                         "IIW-type S-N curve and Miner's rule, no mean stress correction; below the knee the curve goes on with "
                         "slope 2 m - 1 (IIW-1823-07 (2008) sec. 4.3.1, p.110, for Miner summation of a spectrum); the "
                         "design's slope after the knee (constant amplitude) is not used"],
            source=("random vibration of linear systems (textbook): output spread from the frequency responses, the "
                    "input spectrum and the lag between the axles; narrow-band Miner damage of Rayleigh cycles "
                    "(textbook fatigue); the road spectrum form of ISO 8608 (not checked in this repository); the S-N "
                    "curve of the IIW recommendations: m, the knee and the slope 2 m - 1 below it for a spectrum as in "
                    "IIW-1823-07 (2008) sec. 3.2 and 4.3.1, pp.42 and 110, read there by the quality department; the "
                    "2016 edition (A. F. Hobbacher, 2nd ed., Springer) not checked"),
            requires=[(lambda v: v["v"] > 0 and v["W_f"] > 0 and v["W_r"] > 0, "v, W_f and W_r must be positive"),
                      (lambda v: any(v[k] != 0 for k in ("sigma_ra", "sigma_rb", "sigma_fa", "sigma_fb")),
                       "at least one wheel must stress the weld")],
            fn=_road_damage_four,
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
                    "m2": _in("1", "slope beyond the knee for constant amplitude (not for a spectrum: there "
                                    "IIW uses 2 m - 1)", min=0)},
            outputs={"D": ("1", "Miner damage (failure at 1)", None), "N": ("1", "cycles to failure at this range", None)},
            assumptions=["constant stress range", "IIW-type S-N curve for welded joints (no mean stress correction)",
                         "linear damage accumulation (Miner)",
                         "m2 is the slope below the knee for constant amplitude (IIW-1823-07 (2008) sec. 3.2, p.44, "
                         "for very high cycles); for a stress spectrum IIW continues the curve with 2 m - 1 "
                         "(sec. 4.3.1, p.110), as the road formulas do"],
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

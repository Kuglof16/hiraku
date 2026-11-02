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


def all_formulas() -> list[Formula]:
    s = {k: sp.Symbol(k, real=True) for k in ("d", "v", "f")}
    d, v, f = s["d"], s["v"], s["f"]
    return [
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

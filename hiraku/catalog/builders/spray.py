"""Load case for the test the spray_reach tool takes: H6 (what the wheels throw up does not reach the occupants).

Lines leave each wheel's tread along the tangent, from the arc Q87 ahead of and Q88 behind straight above the
axle; none may reach an occupant's body part before another part (spray_reaching_occupant <= 0). Q87 and Q88 are
a public-road rule for other traffic (道路運送車両の保安基準の細目を定める告示 (2023.9.22 版) 第178条 第2項
第1号, as the intake department read it), borrowed: applying it to the occupants is an assumption, said in the
criterion's basis. Requirements without Q87 or Q88: not evaluated (BuildError naming the missing keys).

The plan predicts the uncovered arc first (spray.uncovered_arc, from the fender's angles) and then the lines that
reach an occupant (spray.reaching_lines, counted on the occupants' boxes and the fender's cylinder only); the tool
checks both on every part and member.
"""

from __future__ import annotations

from hiraku.catalog.builders.common import BuildError, Record, entry, loading, number, q, tolerance
from hiraku.catalog.loading import LOADING_JA

# The tool counts the arc in bins of at most arc_step (5 deg): one bin over the default fender's uncovered 28 deg
# is 18 %. The count of lines: K0 has fewer things in the way than the tool, so it is never smaller; measured
# (2026-11-04) on ladder_4w version 1 (37 against 37) and with fenders: wheel (0 against 0). 20 %.
H6_TOL = 0.2
KEY_NAMES = {"Q87": "泥や石を放す踏面の範囲 (前へ)", "Q88": "泥や石を放す踏面の範囲 (後ろへ)"}
BORROWED = ("Q87 and Q88 are 道路運送車両の保安基準の細目を定める告示 (2023.9.22 版) 第178条 第2項 第1号 as the "
            "intake department read it: a public-road rule for other traffic, borrowed; applying it to the occupants "
            "is an assumption")


def h6_spray(spec: Record, req: Record, design: Record) -> Record:
    from hiraku.solvers.k0_handcalc.spray_inputs import ARC_STEP_DEG, LINE_LENGTH_MM, LINES_ACROSS

    miss = [k for k in ("Q87", "Q88") if entry(req, k) is None]
    if miss:
        raise BuildError("評価できない: " + "、".join(f"{k} {KEY_NAMES[k]}" for k in miss) + "が要件に無い")
    load, keys = loading(req, design)
    a_f, a_r = number(req, "Q87", "deg"), number(req, "Q88", "deg")
    if a_f < 0 or a_r < 0 or a_f + a_r <= 0:
        raise BuildError(f"Q87 ({a_f:g} deg) and Q88 ({a_r:g} deg) must not be negative and must make an arc")
    return {
        "load_case_id": "H6-spray",
        "test_id": "H6",
        "domain": spec["domain"],
        "title": (f"車輪が巻き上げた物 (踏面の、車軸の真上から前 {a_f:g} 度・後ろ {a_r:g} 度) が乗員に届かないか "
                  f"({LOADING_JA[load]})"),
        "params": {"arc_front": q(a_f, "deg"), "arc_rear": q(a_r, "deg"), "arc_step": q(ARC_STEP_DEG, "deg"),
                   "lines_across": q(float(LINES_ACROSS), "1"), "line_length": q(LINE_LENGTH_MM, "mm"),
                   "loading": load},
        "requirement_keys": ["Q87", "Q88"] + (["Q31"] if entry(req, "Q31") else []) + keys,
        "criterion": {"quantity": "spray_reaching_occupant", "op": "<=", "limit": q(0.0, "1"),
                      "basis": (f"H6: no line thrown off the tread from {a_f:g} deg ahead of to {a_r:g} deg behind "
                                f"straight above the axle (Q87, Q88) reaches an occupant's body part before another "
                                f"part or member. {BORROWED}. The lines are straight along the tangent the way the "
                                f"tread moves going forward (no gravity, no air, not in reverse), {ARC_STEP_DEG:g} deg "
                                f"apart along the arc, {LINES_ACROSS} across the tyre, up to {LINE_LENGTH_MM:g} mm: "
                                "assumptions, not checked"),
                      "requirement_keys": ["Q87", "Q88"]},
        "tolerance_rel": tolerance(spec, H6_TOL),
    }


def _read(fn, design: Record, load_case: Record) -> Record:
    from hiraku.solvers.k0_handcalc.vehicle import DesignError

    try:
        return fn(design, load_case)
    except (DesignError, ValueError) as e:
        raise BuildError(str(e)) from None


def h6_arc_calc(design: Record, load_case: Record) -> Record:
    """spray.uncovered_arc's fender inputs: hiraku.solvers.k0_handcalc.spray_inputs.spray_arc_inputs."""
    from hiraku.solvers.k0_handcalc.spray_inputs import spray_arc_inputs

    got = _read(spray_arc_inputs, design, load_case)
    return {k: got[k] for k in ("f", "r", "R", "g")}


def h6_reach_calc(design: Record, load_case: Record) -> Record:
    """spray.reaching_lines' inputs: hiraku.solvers.k0_handcalc.spray_inputs.spray_reach_inputs."""
    from hiraku.solvers.k0_handcalc.spray_inputs import spray_reach_inputs

    return _read(spray_reach_inputs, design, load_case)


_LINES = ["踏面を離れた物は、前進のときに踏面が動く向き (車から見て) の接線に沿ってまっすぐ飛ぶ。重力・空気・後退は入れない (仮定)",
          "弧 (車軸の真上から前 Q87・後ろ Q88) は公道のほかの交通のための規則 (告示 第178条 第2項 第1号) を借りたもの。"
          "乗員に当てはめるのは仮定"]
H6_PLAN = {
    "tool": "spray_reach",
    "say": ("フェンダーを車輪の中心のまわりの円の弧とみて、踏面から接線に飛ぶ物がフェンダーに当たらない弧を先に出します。"
            "次に、乗員の体の部分の箱とフェンダーだけを置いて、乗員に届く線を数えます"),
    "predictions": [
        {"quantity": "spray_uncovered_arc", "formula_id": "spray.uncovered_arc", "output": "arc",
         "assumptions": _LINES + ["フェンダーは半径 R + g の円の弧で、車軸の真上から前 f・後ろ r まで。タイヤより幅が広い",
                                  "フェンダーのほかに踏面を覆う物は無いとみる (ツールはほかの部品・部材も見る)",
                                  "車輪の半径は、いちばん小さいもの"],
         "inputs": {"a_f": "lc.arc_front", "a_r": "lc.arc_rear", "f": "from.f", "r": "from.r", "R": "from.R",
                    "g": "from.g"},
         "inputs_from": h6_arc_calc},
        {"quantity": "spray_reaching_occupant", "formula_id": "spray.reaching_lines", "output": "n",
         "assumptions": _LINES + ["乗員の体の部分の箱とフェンダー (円の弧) だけを置く。ほかの部品・部材・地面は置かない "
                                  "(届く線はツールより少なくならない)"],
         "inputs": {"n_aim": "from.n_aim", "n_cover": "from.n_cover"}, "inputs_from": h6_reach_calc},
    ],
}

BUILDERS = {"H6": h6_spray}
PLANS = {"H6": H6_PLAN}

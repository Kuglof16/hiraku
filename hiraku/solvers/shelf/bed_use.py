"""Tools for a product that does not travel (designs of hiraku.designgen.care_bed), checked over how it is used:
use_fatigue (R3) sums the fatigue damage of the welds over the counts of each use, actuator_duty (T3) times the
back or knee actuator's strokes in the busiest hour.

The vehicle tools of the same tests (fatigue: a rough road over a distance; thermal: a drive loop) need a
distance or a road and refuse a bed; these read counts instead.

use_fatigue: each use is one cycle of a stress range at one weld.
- getting on and off (n_seat): the deck frame, the range the user's weight alone makes in the frame (the 3D frame
  of bed_static with only the occupant's parts weighing: the model is linear, so this is the range at every point);
- raising the back and the knees (n_raise; each raise one cycle at each bracket): the root of the actuator bracket's
  lugs, 3 F h_p / (t w^2) with F the largest actuator force over the raise (actuator_linkage's linkage_force) and h_p
  the pin's distance below the lug root (the formula of bed.lug_bending);
- the lift (n_lift) and the side rail's ups and downs (n_rail): no range at a modelled weld (the lift linkage is
  not modelled, the side rail's own weight is left out); counted and reported, damage 0.
The S-N curve is the design's (fatigue_fat_class at 2e6 cycles, fatigue_slope, fatigue_knee_cycles). The uses'
ranges differ, so the cycles are a spectrum: below the knee the curve goes on with slope 2 m - 1, as IIW-1823-07
(2008) sec. 4.3.1, p.110 gives for Miner summation of a spectrum (read in that document by the quality department;
library_fatigue.spectrum_slope). The design variable fatigue_slope_after_knee_constant_amplitude is the constant-amplitude slope and is
not used. The damage judged is the worst weld's.

actuator_duty: the stroke one raise takes is the lever's (the pin-to-pin length at the largest raise less that at
the flat position, as actuator_linkage's actuator_stroke), not the actuator's whole stroke (design variable
actuator_stroke, which only bounds it). run time = stroke / actuator_speed; on time = uses x strokes per use x run
time; duty = on time / 3600 s.
"""

from __future__ import annotations

import math

from ..k0_handcalc.library_fatigue import spectrum_slope
from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import DesignError
from ..k1_frame.fatigue import miner, sn_cycles
from .base import LOADING, BuiltinTool, LoadCaseError, Param, Predicts, Record, check_record, on_board, param_text, \
    param_value, result
from .bed_tools import ACTUATORS, _table_design, actuator_over_raise, linkage_force, solve_at_rest
from .fatigue import sn_series

SN_VARS = {"fatigue_fat_class": "MPa", "fatigue_slope": "1", "fatigue_knee_cycles": "1"}
USES = (("n_seat", "getting on and off"), ("n_raise", "raising the back and the knees"), ("n_lift", "the lift"),
        ("n_rail", "the side rail up and down"))
WELDS = {"frame": ("n_seat", "n_lift"), "lug_back": ("n_raise",), "lug_knee": ("n_raise",)}
SECONDS_PER_HOUR = 3600.0


def _var(design: Record, name: str) -> float | None:
    q = design.get("variables", {}).get(name)
    return None if q is None else float(q["value"])


def lug_root_stress(F: float, h_p: float, w: float, t: float) -> float:
    """Bending stress at the root of a clevis bracket's two lugs, each a cantilever h_p long under F / 2 (MPa)."""
    return 3.0 * F * h_p / (t * w**2)


def weld_damage(ranges: dict[str, float], counts: dict[str, float], fat: float, m: float, n_knee: float) -> dict:
    """Miner damage at each weld of WELDS: ranges {weld: MPa} (one range per weld), counts {use: cycles}; the slope
    below the knee 2 m - 1. Returns {weld: damage}."""
    m2 = spectrum_slope(m)
    return {w: miner([(ranges[w], 0.0, sum(counts.get(u, 0.0) for u in uses))], fat, m, n_knee, m2)
            if ranges[w] > 0 else 0.0 for w, uses in WELDS.items()}


def occupant_only(design: Record) -> Record:
    """A copy whose only weights are the occupant's parts (the members weightless, the other parts 0 kg)."""
    out = dict(design)
    out["parts"] = [dict(p, mass_kg=p["mass_kg"] if p["kind"] == "occupant" else 0.0) for p in design["parts"]]
    out["materials"] = {k: dict(v, density_kg_m3=1e-6) for k, v in design["materials"].items()}
    return out


def lug_lever(design: Record, which: str) -> float:
    """Pin centre below the bracket's lug root (the cross member's underside), mm."""
    parts = {p["id"]: p for p in design["parts"]}
    b = parts[f"bracket_{which}"]["box"]
    return (b["center_mm"][2] + b["size_mm"][2] / 2.0) - parts[f"actuator_{which}"]["box"]["center_mm"][2]


def use_fatigue_known_answers() -> list[Record]:
    out = []
    # FAT 71, m 3, knee 1e7: the shape of the IIW recommendations (Hobbacher 2016; not checked), here test values
    fat, m, nk = 71.0, 3.0, 1e7
    knee = fat * (2e6 / nk) ** (1.0 / m)
    src_sn = "IIW-type S-N curve N = 2e6 (FAT / range)^m above the knee and Miner's rule D = n / N"
    src_sn_ja = "IIW 形の S-N 曲線: 曲がり点より上は N = 2e6 (FAT / 応力の幅)^m。マイナー則 D = n / N"
    n_up, s_up = 1000.0, 100.0
    d = weld_damage({"frame": 0.0, "lug_back": s_up, "lug_knee": 0.0}, {"n_raise": n_up}, fat, m, nk)
    out.append(check_record("1000 raises at 100 MPa at the back lug, FAT 71, m 3", "damage_lug_back",
                            1000.0 / (2e6 * (fat / 100.0) ** m), d["lug_back"], "1", 1e-12, src_sn,
                            case_ja=f"背の金具の耳に {s_up:g} MPa の幅で {n_up:g} 回の上げ、FAT {fat:g}、m {m:g}",
                            source_ja=src_sn_ja))
    n_seat, n_lift = 6000.0, 4000.0
    d = weld_damage({"frame": knee / 2.0, "lug_back": 0.0, "lug_knee": 0.0}, {"n_seat": n_seat, "n_lift": n_lift},
                    fat, m, nk)
    m2 = 2 * m - 1
    out.append(check_record("10000 cycles (6000 seat, 4000 lift) at half the knee range at the frame", "damage_frame",
                            1e4 / (nk * 2.0 ** (2 * m - 1)), d["frame"], "1", 1e-9,
                            "below the knee the spectrum's slope 2 m - 1 = 5 (IIW-1823-07 (2008) sec. 4.3.1, p.110): "
                            "half the knee range lasts 2^5 times the knee cycles; the seat's and the lift's counts add",
                            case_ja=(f"床の枠で、曲がり点の幅の半分の繰り返しを {n_seat + n_lift:g} 回 (着座 {n_seat:g} 回、"
                                     f"昇降 {n_lift:g} 回)"),
                            source_ja=(f"曲がり点より下では、スペクトルの傾きは 2 m - 1 = {m2:g} (IIW-1823-07 (2008) 4.3.1 節、"
                                       f"p.110)。曲がり点の幅の半分なら、曲がり点の回数の 2^{m2:g} 倍もつ。着座と昇降の回数は"
                                       "足す")))
    ranges = {"frame": 30.0, "lug_back": 65.0, "lug_knee": 45.0}
    counts = {"n_seat": 1e4, "n_raise": 1e3}
    d = weld_damage(ranges, counts, fat, m, nk)
    expect = {"frame": 1e4 / (nk * (knee / 30.0) ** 5), "lug_back": 1e3 / (2e6 * (fat / 65.0) ** 3),
              "lug_knee": 1e3 / (2e6 * (fat / 45.0) ** 3)}
    out.append(check_record("three welds, the worst one", "damage_worst", max(expect.values()), max(d.values()), "1",
                            1e-9, "closed form of each weld; the worst is the back lug (65 MPa above the knee)",
                            case_ja=f"{len(ranges)} か所の溶接のうち、いちばん悪いもの",
                            source_ja=(f"溶接ごとに式で出す。いちばん悪いのは背の金具の耳 ({ranges['lug_back']:g} MPa、"
                                       "曲がり点より上)")))
    lug_F, lug_h, lug_w, lug_t = 2000.0, 100.0, 40.0, 6.0
    out.append(check_record("clevis lugs, 2 kN, pin 100 mm below the root, 40 x 6 mm", "lug_root_stress",
                            1000.0 * 100.0 / (6.0 * 40.0**2 / 6.0), lug_root_stress(lug_F, lug_h, lug_w, lug_t), "MPa",
                            1e-12, "each lug a cantilever: (F / 2) h_p / (t w^2 / 6)",
                            case_ja=(f"二又の金具の耳、{lug_F / 1e3:g} kN、ピンは根元から {lug_h:g} mm 下、耳は "
                                     f"{lug_w:g} x {lug_t:g} mm"),
                            source_ja="耳 1 枚ずつを片持ち梁とみる: (F / 2) h_p / (t w^2 / 6)"))
    # the occupant-only frame: a 100 kg person at a quarter of AB on the weightless triangle (lever rule)
    tri = _table_design()
    tri["parts"] = [{"id": "occupant.body", "name": "person", "kind": "occupant", "mass_kg": 100.0,
                     "box": {"center_mm": [-250.0, 0.0, 300.0], "size_mm": [10.0, 10.0, 10.0]}, "carried_by": ["Q"]},
                    {"id": "box", "name": "box", "kind": "cargo", "mass_kg": 50.0,
                     "box": {"center_mm": [0.0, 800.0, 300.0], "size_mm": [10.0, 10.0, 10.0]}, "carried_by": ["C"]}]
    res = solve_at_rest(occupant_only(tri))
    g = {k: float(v["force_N"][2]) for k, v in res["ground"].items()}
    W = 100.0 * STANDARD_GRAVITY
    person, box = (p["mass_kg"] for p in tri["parts"])
    only_ja = f"人の重さだけを載せる: {person:g} kg の人は AB の 4 分の 1 の所、C の {box:g} kg の箱は重さを入れない"
    out.append(check_record("only the occupant weighs: 100 kg at a quarter of AB, a 50 kg box at C left out",
                            "support_load_A", 0.75 * W, g["SA"], "N", 1e-6, "lever rule R_A = 3/4 W; the box adds nothing",
                            case_ja=only_ja, source_ja="てこの法則 R_A = 3/4 W。箱は何も足さない"))
    out.append(check_record("the same", "support_load_C", 0.0, g["SC"], "N", 1e-6, "the box at C weighs nothing here",
                            case_ja=only_ja, source_ja="ここでは C の箱の重さは 0"))
    return out


class UseFatigue(BuiltinTool):
    name = "use_fatigue"
    applies_to = ("care_bed",)
    stage = 1  # counts times closed-form S-N: one static frame solve and two lever scans, as bed_static (stage 1)
    title = "使う回数の疲労 (使い方ごとの回数と応力の幅 → S-N とマイナー則、溶接の悪い方)"
    domain = "strength"
    base = "K1"
    tests = ["R3"]
    formulas = ["bed.use_damage", "bed.lug_bending", "bed.lever_actuator_force", "bed.deck_frame_udl"]
    load_case_params = (
        LOADING,
        Param("n_seat", "1", "times the user gets on and off over the life (requirement Q92)", required=True),
        Param("n_raise", "1", "back and knee raises over the life (each one cycle at each actuator bracket; Q92)",
              required=True),
        Param("n_lift", "1", "lift movements over the life (Q92); no range at a modelled weld", default=0.0),
        Param("n_rail", "1", "side rail ups and downs over the life (Q92); no range at a modelled weld", default=0.0),
    )
    predicts = (
        Predicts("fatigue_damage", "bed.use_damage", "D_worst",
                 "dS1 from bed.deck_frame_udl (the user on less the user off), dS2 and dS3 from bed.lug_bending with "
                 "F = bed.lever_actuator_force F_max; the tool takes the frame's range in the 3D frame and the force with "
                 "the loads' heights"),
        Predicts("stress_range", "bed.use_damage", "dS_worst",
                 "the range at the worst weld; compared in place of the damage when both damages are next to 0 "
                 "(hiraku.catalog.builders.product_care_bed.R3_TINY_DAMAGE)"),
    )

    def known_answers(self) -> list[Record]:
        return use_fatigue_known_answers()

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        design = on_board(design, load_case)
        sn = {k: _var(design, k) for k in SN_VARS}
        missing = [f"{k} [{u}]" for k, u in SN_VARS.items() if sn[k] is None]
        if missing:
            raise DesignError("the design has no S-N data for: " + ", ".join(missing) + "; add them to design.variables")
        fat, m, n_knee = (sn[k] for k in SN_VARS)
        if fat <= 0 or m <= 0 or n_knee <= 0:
            raise DesignError("fatigue_fat_class, fatigue_slope and fatigue_knee_cycles must be positive")
        counts = {}
        for name, _ in USES:
            c = param_value(load_case, name, "1")
            if c is None or c < 0:
                raise LoadCaseError(f"{name} must be a count of 0 or more")
            counts[name] = c
        if not design.get("supports"):
            raise DesignError("the design has no supports to stand on")
        # the deck frame: the user's weight alone
        seat = solve_at_rest(occupant_only(design))
        frame_w = seat["worst"]
        ranges = {"frame": seat["stresses"][frame_w]["max"]}
        where = {"frame": frame_w}
        forces = {}
        v = {k: q["value"] for k, q in design["variables"].items()}
        for which in ACTUATORS:
            for k in ("bracket_width", "bracket_t"):
                if k not in v:
                    raise DesignError(f"use_fatigue reads a care bed (hiraku.designgen.care_bed); this design lacks {k}")
            res = actuator_over_raise(design, which, self.name)
            forces[which] = res["F_max"]
            h_p = lug_lever(design, which)
            ranges[f"lug_{which}"] = lug_root_stress(res["F_max"], h_p, v["bracket_width"], v["bracket_t"])
            where[f"lug_{which}"] = f"bracket_{which}"
        D = weld_damage(ranges, counts, fat, m, n_knee)
        worst = max(D, key=lambda w: (D[w], ranges[w]))  # with no damage anywhere: the weld of the largest range
        m2 = spectrum_slope(m)
        results = [result("fatigue_damage", D[worst], "1", where[worst]),
                   result("stress_range", ranges[worst], "MPa", where[worst]),
                   result("uses_life_factor", 1.0 / D[worst] if D[worst] > 0 else math.inf, "1", where[worst])]
        for w in WELDS:
            results.append(result(f"fatigue_damage_{w}", D[w], "1", where[w]))
            results.append(result(f"stress_range_{w}", ranges[w], "MPa", where[w]))
        for which, F in forces.items():
            results.append(result(f"actuator_force_{which}", F, "N", f"actuator_{which}"))
        cycles = [(ranges[worst], 0.0, sum(counts[u] for u in WELDS[worst]))]
        per_member = {mid: (counts["n_seat"] + counts["n_lift"]) / sn_cycles(s["max"], fat, m, n_knee, m2)
                      if s["max"] > 0 else 0.0 for mid, s in seat["stresses"].items()}
        effects = [{"name": f"{label}: {counts[n]:g} cycles", "value": counts[n], "unit": "1"} for n, label in USES]
        effects += [{"name": f"damage at {w} ({where[w]}), range {ranges[w]:.4g} MPa", "value": D[w], "unit": "1",
                     "share": D[w] / sum(D.values()) if sum(D.values()) > 0 else 0.0} for w in WELDS]
        return {
            "results": results,
            "fields": [{"kind": "damage", "label": "Miner damage of getting on and off and the lift at each frame member",
                        "unit": "1", "per_member": per_member}],
            "series": sn_series(fat, m, n_knee, m2, cycles, where[worst]),
            "effects": effects,
            "assumptions": [
                f"S-N の曲がり点より下は、傾き {m2:g} (= 2 × {m:g} − 1) で延ばした。使い方ごとに応力の幅が違うので振幅の変わる繰り返し "
                "(スペクトル) で、IIW-1823-07 (2008) 4.3.1 節 (p.110) は、その Miner の和に 2m − 1 を使う (品質保証部がその文書で"
                "確かめた)。設計の変数 fatigue_slope_after_knee_constant_amplitude は一定の振幅のときの傾きなので、ここでは使わない",
                "使い方 1 回が、その溶接の応力の幅 1 回 (0 から載せた分まで)。平均応力の補正はしない",
                "着座: 利用者の重さだけを 3D の枠に載せた応力 (枠は線形なので、それが幅)",
                "背上げ・膝上げ: 1 回の上げで背と膝の金具の耳の根元がそれぞれ 1 回、起こす間の最大の力の応力まで振れる",
                "昇降と柵の上げ下げは、模型にある溶接の応力を変えない (昇降のリンクは模型に無い、柵の自重は入れない)",
                "溶接は床の枠・背上げの金具の耳の根元・膝上げの金具の耳の根元の 3 か所で、判定は悪い方",
            ],
        }


def duty(stroke_mm: float, speed_m_s: float, uses: float, strokes_per_use: float) -> dict:
    run = stroke_mm / 1000.0 / speed_m_s
    on = uses * strokes_per_use * run
    return {"run_time": run, "on_time": on, "duty": 100.0 * on / SECONDS_PER_HOUR}


def upstream_stroke(upstream: dict | None, at: str) -> tuple[str, float] | None:
    """(load case id, mm) of the first ok upstream analysis with a result actuator_stroke where `at`."""
    for lc_id, ana in (upstream or {}).items():
        if not isinstance(ana, dict) or ana.get("status") != "ok":
            continue
        for r in ana.get("results", []):
            if r.get("quantity") == "actuator_stroke" and r.get("where") == at and r.get("unit") == "mm" \
                    and math.isfinite(float(r.get("value", math.nan))):
                return lc_id, abs(float(r["value"]))
    return None


def actuator_duty_known_answers() -> list[Record]:
    out = []
    r, ax = 100.0, 400.0
    ang = 45.0
    res = linkage_force((0.0, 0.0), (-ax, -r), r, [], ang, 1.0, steps=2)
    th = math.radians(ang)
    e = math.hypot(r * math.sin(th) + ax, r - r * math.cos(th))
    out.append(check_record("lever 100 mm, anchor 400 mm away level with the tip, raised 45 deg", "lever_stroke", e - ax,
                            res["stroke"], "mm", 1e-9, "stroke = |tip(45 deg) - anchor| - a (the lever's, not the "
                                                      "actuator's whole stroke)",
                            case_ja=f"長さ {r:g} mm のてこ、先と同じ高さで {ax:g} mm 離れた留め点、{ang:g}° 起こす",
                            source_ja=(f"ストローク = |先 ({ang:g}°) - 留め点| - a (てこのストロークで、アクチュエーターの"
                                       "全ストロークではない)")))
    s_mm, v_m_s, uses, k = 120.0, 0.006, 6.0, 2.0
    got = duty(s_mm, v_m_s, uses, k)
    run_ja = f"{s_mm:g} mm を {v_m_s * 1e3:g} mm/s で"
    out.append(check_record("120 mm at 6 mm/s", "run_time", 20.0, got["run_time"], "s", 1e-12, "t = s / v",
                            case_ja=run_ja, source_ja="動く時間 t = s / v"))
    out.append(check_record("6 uses, 2 strokes each, 120 mm at 6 mm/s", "duty", 100.0 * 6 * 2 * 20.0 / 3600.0,
                            got["duty"], "%", 1e-12, "duty = n k s / v / 3600 s",
                            case_ja=f"{uses:g} 回使い、1 回に {k:g} ストローク、{run_ja}",
                            source_ja=f"使う割合 = n k s / v / {SECONDS_PER_HOUR:g} s"))
    return out


class ActuatorDuty(BuiltinTool):
    name = "actuator_duty"
    applies_to = ("care_bed",)
    stage = 1  # arithmetic on one lever's geometry
    title = "アクチュエーターの使う割合 (てこのストロークと定格の速さ、いちばん忙しい 1 時間)"
    domain = "thermal"
    base = "K0"
    tests = ["T3"]
    formulas = ["bed.actuator_duty", "bed.lever_actuator_force"]
    load_case_params = (
        LOADING,
        Param("actuator", None, "which actuator: back or knee", required=True, choices=tuple(ACTUATORS)),
        Param("uses_per_hour", "1", "uses in the busiest hour (requirement Q03)", required=True),
        Param("strokes_per_use", "1", "strokes per use (up and down: 2)", default=2.0),
    )
    predicts = (
        Predicts("actuator_duty", "bed.actuator_duty", "duty", "s = bed.lever_actuator_force stroke (the lever's)"),
        Predicts("actuator_run_time", "bed.actuator_duty", "run_time"),
    )
    upstream = ("an ok analysis with the result actuator_stroke at actuator_<actuator> (actuator_linkage of P5, which "
                "feeds T3): its stroke replaces the one computed here")

    def known_answers(self) -> list[Record]:
        return actuator_duty_known_answers()

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        design = on_board(design, load_case)
        which = param_text(load_case, "actuator")
        res = actuator_over_raise(design, which, self.name)
        speed = _var(design, "actuator_speed")
        if speed is None or speed <= 0:
            raise DesignError("actuator_duty needs the design variable actuator_speed [m/s] (positive)")
        n = param_value(load_case, "uses_per_hour", "1")
        k = param_value(load_case, "strokes_per_use", "1")
        if n is None or n < 0 or k is None or k < 0:
            raise LoadCaseError("uses_per_hour and strokes_per_use must be 0 or more")
        at = f"actuator_{which}"
        own = abs(res["stroke"])
        up = upstream_stroke(params.get("upstream_analyses"), at)
        stroke = own if up is None else up[1]
        got = duty(stroke, speed, n, k)
        results = [result("actuator_duty", got["duty"], "%", at), result("actuator_run_time", got["run_time"], "s", at),
                   result("actuator_on_time", got["on_time"], "s", at), result("actuator_stroke", stroke, "mm", at)]
        if up is None:
            effects = [{"name": "stroke: the lever's, computed here (no upstream actuator_linkage analysis of this "
                                "actuator)", "value": own, "unit": "mm"}]
        else:
            effects = [{"name": f"stroke: from the upstream analysis {up[0]} (actuator_stroke at {at}); computed here "
                                f"{own:.4g} mm", "value": up[1], "unit": "mm"}]
        whole = _var(design, "actuator_stroke")
        if whole is not None:
            results.append(result("actuator_stroke_left", whole - stroke, "mm", at))
            effects.append({"name": f"the actuator's whole stroke {whole:g} mm (bounds the lever's {stroke:.4g} mm; "
                                    "not used for the time)", "value": whole, "unit": "mm"})
        longest = _var(design, "actuator_max_on_time")
        if longest is not None:
            effects.append({"name": f"one stroke runs {got['run_time']:.4g} s; the actuator's longest continuous run "
                                    f"{longest:g} s", "value": got["run_time"] / longest, "unit": "1"})
        return {"results": results, "effects": effects,
                "assumptions": ["1 回の動きは、てこのストローク (平らなときと最大に起こしたときのピンの間の長さの差)。"
                                "アクチュエーターの全ストローク (設計の変数 actuator_stroke) ではない",
                                "定格の速さ (actuator_speed) で動く", "1 日の使い方をいちばん忙しい 1 時間に集める"]}


__all__ = ["ActuatorDuty", "UseFatigue", "duty", "lug_root_stress", "occupant_only", "weld_damage"]

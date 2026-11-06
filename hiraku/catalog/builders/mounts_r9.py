"""R9 サスペンション取付 (stage 1): one load case per corner support with a suspension (hard points), shown, not
judged.

Each load case R9-<support> predicts, for each of the corner's mount points (k2_vehicle.kinematics.MOUNTS) and
for the corner's worst point, the four utilisations of the contract (docs/contract.md 金具の継ぎ手):
bolt_shear_utilisation, bolt_bearing_utilisation, plate_bending_utilisation and weld_utilisation (unit 1), by the
K0 formulas mount_bracket.* (hiraku.solvers.k0_handcalc.library_bracket) with the inputs of
hiraku.solvers.k0_handcalc.bracket_inputs: K2's mount force series from the upstream load cases (FEEDS: P1's
step driven in K2, S1 tilt, S3 lane change, S4 braking, S5 side slope, P2 and P3 launch; those the requirements
have), the largest |F| of each, the worst utilisation over them. A place's prediction is named
<quantity>.<mount> (e.g. weld_utilisation.uca_front); the corner's worst point gives the plain quantity.

Stage 2: the step's tool is frame_shell, which meshes the brackets that stand on a tube wall as plates
(k1_shell.brackets) and reports plate_bending_utilisation and weld_utilisation (and '<quantity>.<mount>') from
the shell under the same K2 mount series; the hand calculation's values of those two are the predictions it is
compared with. bolt_shear_utilisation and bolt_bearing_utilisation are not in the shell (the pin is not
modelled): their predictions stay the hand calculation's, no tool reports them. R9_CRITERIA has the two
quantities the shell judges. The load cases carry no criterion yet: a load case has one criterion
(load_case.criterion) and R9 judges two quantities per corner, so how they are attached (one load case per
quantity, or a list) is for the contract (the lead); until then the hooks skip the load case as before.

The prediction's reader r9_inputs(design, load_case, upstream=None) needs the upstream analyses: from its third
argument, else load_case["upstream_analyses"] or load_case["params"]["upstream_analyses"]; without them it raises
NoForces (a ValueError: the run records that the reader does not apply), it does not invent forces.
"""

from __future__ import annotations

from hiraku.catalog.builders.common import BuildError, Record, loading, q, tolerance
from hiraku.solvers.k0_handcalc import library_bracket as lb
from hiraku.solvers.k0_handcalc.bracket_inputs import MOUNTS, NoForces, bracket_inputs

TEST_ID = "R9"
R9_TOL = 0.10  # the hand calculation against the shell's plates and welds (stage 2): a first figure, no measurement
SHELL_QUANTITIES = ("plate_bending_utilisation", "weld_utilisation")  # frame_shell.R9_QUANTITIES
UPSTREAM_TESTS = ("P1", "S1", "S3", "S4", "S5", "P2", "P3")  # the K2 load cases whose tools record mount series
UNITS = {"F_n": "N", "F_t": "N", "F_v": "N", "d": "mm", "n": "1", "t": "mm", "s": "mm", "b": "mm", "e": "mm",
         "f_y": "MPa", "f_u": "MPa", "f_ub": "MPa"}
# the criteria of the two quantities the shell judges (frame_shell); not attached to the load cases yet (module doc)
R9_CRITERIA = {qty: {"quantity": qty, "op": "<=", "limit": {"value": 1.0, "unit": "1"},
                     "basis": "R9: EN 1993-1-8 resistances with the recommended partial factors (not checked); "
                              "docs/contract.md 金具の継ぎ手"} for qty in SHELL_QUANTITIES}
R9_TITLE = ("サスペンション取付 {sid} (判定なし: 手計算の予測だけ。金具の板を枠のシェルに入れて判定するのは次の段。"
            "ピンのせん断・支圧、板の根元の曲げ、根元の溶接を、K2 の取付点の力の最大で見る)")


def corners(design: Record) -> list[str]:
    return [s["id"] for s in design.get("supports", []) if (s.get("suspension") or {}).get("hardpoints_mm")]


def r9_mounts(spec: Record, req: Record, design: Record) -> list[Record]:
    """One load case per corner support with suspension.hardpoints_mm (see the module doc)."""
    ids = corners(design)
    if not ids:
        raise BuildError("R9 needs supports with suspension.hardpoints_mm (the design has none)")
    load, keys = loading(req, design)
    extra = [k for k in spec.get("requirement_keys", []) if k not in keys]
    return [{"load_case_id": f"{TEST_ID}-{sid}", "test_id": TEST_ID, "domain": spec.get("domain", "strength"),
             "title": R9_TITLE.format(sid=sid), "params": {"loading": load, "support": sid},
             "requirement_keys": keys + extra, "tolerance_rel": tolerance(spec, R9_TOL)} for sid in ids]


def r9_inputs(design: Record, load_case: Record, upstream: dict | None = None) -> Record:
    """The mount_bracket formulas' inputs for the load case's corner: '<quantity>.<input>' (the corner's worst point)
    and '<quantity>.<mount>.<input>' (each mount point), as quantities; plus 'worst.<quantity>' (the mount's name)."""
    up = upstream or load_case.get("upstream_analyses") or (load_case.get("params") or {}).get("upstream_analyses")
    if not up:
        raise NoForces(f"{load_case.get('load_case_id', 'R9')}: no upstream analyses with K2's mount series were given "
                       f"(R9 takes them from {', '.join(UPSTREAM_TESTS)}); the mount forces are not invented")
    got = bracket_inputs(design, load_case["params"]["support"], up)
    out: Record = {}
    for m, p in got["points"].items():
        for qty, w in p["worst"].items():
            for k, v in w["inputs"].items():
                out[f"{qty}.{m}.{k}"] = q(float(v), UNITS[k])
    for qty, (m, _) in got["worst"].items():
        out[f"worst.{qty}"] = m
        for k in lb.INPUTS_OF[qty]:
            out[f"{qty}.{k}"] = out[f"{qty}.{m}.{k}"]
    return out


_ASSUME = {
    "bolt_shear_utilisation": "ピンは 2 面せん断、軸部の断面 (ねじはせん断面の外)。強度区分 8.8",
    "bolt_bearing_utilisation": "金具の板の支圧だけ (枠の壁は見ない)。穴の縁までは最小の 1.2 d0",
    "plate_bending_utilisation": "板は根元からの片持ち。板の外への曲げ・面内の曲げ・引きはがしを根元の角で足す (安全側)",
    "weld_utilisation": "板ごとに根元の両側のすみ肉 2 本 (のど厚 0.7 脚長)。線の力を足す (安全側)。STKR400 を S235 相当とみる (仮定)",
}
_COMMON = ["K2 の取付点の力 (車体が受ける力)。上流の解析ごとに |F| が最大の瞬間、その中で利用率が最大のもの",
           "A アームの腕に沿う力は、K2 が内の 2 つの付け根に半分ずつ分けている (仮定)",
           "ピンは車体の x 向き (板は車の横向きの面)"]


def _prediction(qty: str, mount: str | None) -> Record:
    at = f"{qty}.{mount}" if mount else qty
    return {"quantity": at, "formula_id": lb.FORMULA_OF[qty], "output": "u",
            "assumptions": _COMMON + [_ASSUME[qty]] + ([] if mount else ["その角の取付点のうち、いちばん大きいもの"]),
            "inputs": {k: f"from.{at}.{k}" for k in lb.INPUTS_OF[qty]}, "inputs_from": r9_inputs}


R9_PLAN = {
    "tool": "frame_shell",
    "say": ("サスペンションの取付点ごとに、K2 の力の最大で、ピンのせん断と支圧、金具の板の根元の曲げ、根元の溶接の利用率を"
            "先に出します (判定なし: 手計算の予測だけ)"),
    "predictions": [_prediction(qty, m) for qty in lb.QUANTITIES for m in (None, *MOUNTS)],
}

BUILDERS = {TEST_ID: r9_mounts}
PLANS = {TEST_ID: R9_PLAN}
FEEDS = {t: [TEST_ID] for t in UPSTREAM_TESTS}

__all__ = ["BUILDERS", "FEEDS", "PLANS", "R9_CRITERIA", "R9_PLAN", "corners", "r9_inputs", "r9_mounts"]

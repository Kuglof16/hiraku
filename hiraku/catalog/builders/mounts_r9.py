"""R9 サスペンション取付 (stage 3): load cases per quantity and axle, each with one criterion.

For each group of corners (GROUP_BY = "axle": the front and the rear axle, both sides together; "support": one corner
each) three load cases:

    R9-plate-<group>  plate_bending_utilisation <= 1   tool frame_shell (the brackets meshed as plates, k1_shell.brackets)
    R9-weld-<group>   weld_utilisation <= 1            tool frame_shell
    R9-bolt-<group>   bolt_utilisation <= 1            tool frame_static (stage 1: the pin's hand formulas from K2's
                                                       mount forces; the pin is not in the shell)

bolt_utilisation is the larger of bolt_shear_utilisation and bolt_bearing_utilisation (K0 mount_bracket.pin), so that the
pin case has one criterion as the contract's load case does; the tool reports the two as well. A place is
'<support>.<mount>' (e.g. RL.uca_front): the tools report '<quantity>.<support>.<mount>' for each of the group's corners
and '<quantity>' the worst of them, whose where names the place (the side and the mount that gave the worse value); the
hot_spots rows keep each side's own values (frame_shell: one row per bracket; frame_static: one row per place).

The predictions are the hand estimates (K0 mount_bracket.*, hiraku.solvers.k0_handcalc.library_bracket) with the
inputs of hiraku.solvers.k0_handcalc.bracket_inputs: K2's mount force series from the upstream load cases (FEEDS: P1's
step driven in K2, S1 tilt, S3 lane change, S4 braking, S5 side slope, P2 and P3 launch; those the requirements have),
the largest |F| of each, the worst utilisation over them; the welded length b is the part of each bracket's base on the
tube wall (bracket_inputs.weld_span, the length the shell ties). A place's prediction is <quantity>.<support>.<mount>;
the group's worst point gives the plain quantity.

The prediction's reader r9_inputs(design, load_case, upstream=None) needs the upstream analyses: from its third
argument, else load_case["upstream_analyses"] or load_case["params"]["upstream_analyses"]; without them it raises
NoForces (a ValueError: the run records that the reader does not apply), it does not invent forces.
"""

from __future__ import annotations

from hiraku.catalog.builders.common import BuildError, Record, loading, q, tolerance
from hiraku.solvers.k0_handcalc import library_bracket as lb
from hiraku.solvers.k0_handcalc.bracket_inputs import MOUNTS, NoForces, bracket_inputs

TEST_ID = "R9"
GROUP_BY = "axle"  # "axle": R9-<kind>-front / -rear over both sides; "support": R9-<kind>-<support id>, one corner each
R9_TOL = 0.10  # the hand calculation against the shell's plates and welds: a first figure, no measurement
UPSTREAM_TESTS = ("P1", "S1", "S3", "S4", "S5", "P2", "P3")  # the K2 load cases whose tools record mount series
# the load cases per quantity: kind -> (the criterion's quantity, the tool that judges it)
KINDS = {"plate": ("plate_bending_utilisation", "frame_shell"),
         "weld": ("weld_utilisation", "frame_shell"),
         "bolt": (lb.BOLT_QUANTITY, "frame_static")}
SHELL_QUANTITIES = ("plate_bending_utilisation", "weld_utilisation")  # frame_shell.R9_QUANTITIES
# the groups' names in the load case ids and the support ids of ladder_4w's corners in each (the plans' places)
GROUPS = {"axle": {"front": ("FL", "FR"), "rear": ("RL", "RR")},
          "support": {s: (s,) for s in ("FL", "FR", "RL", "RR")}}
UNITS = {"F_n": "N", "F_t": "N", "F_v": "N", "d": "mm", "n": "1", "t": "mm", "s": "mm", "b": "mm", "e": "mm",
         "f_y": "MPa", "f_u": "MPa", "f_ub": "MPa"}
R9_CRITERIA = {qty: {"quantity": qty, "op": "<=", "limit": {"value": 1.0, "unit": "1"},
                     "basis": "R9: EN 1993-1-8 resistances with the recommended partial factors (not checked); "
                              "docs/contract.md 金具の継ぎ手"} for qty, _ in KINDS.values()}
_WHAT = {"plate": "金具の板の根元の曲げ", "weld": "金具の根元の溶接", "bolt": "ピンのせん断と支圧 (大きい方)"}
_GROUP_TITLE = {"front": "前の車軸 (左右)", "rear": "後の車軸 (左右)"}
R9_TITLE = "サスペンション取付 {group}: {what} (K2 の取付点の力の最大で。{tool} で判定)"


def corners(design: Record) -> list[Record]:
    return [s for s in design.get("supports", []) if (s.get("suspension") or {}).get("hardpoints_mm")]


def groups(design: Record) -> dict[str, list[str]]:
    """{group name: [support ids]} of the corners with hard points: by axle (the corners at one wheel centre x, front
    the foremost, rear the hindmost, others axle2.. from the front) or one per support (GROUP_BY)."""
    cs = corners(design)
    if GROUP_BY == "support":
        return {s["id"]: [s["id"]] for s in cs}
    by_x: dict[float, list[str]] = {}
    for s in cs:
        by_x.setdefault(round(float(s["position_mm"][0]), 0), []).append(s["id"])
    xs = sorted(by_x, reverse=True)
    out = {}
    for k, x in enumerate(xs):
        name = "front" if k == 0 else "rear" if k == len(xs) - 1 else f"axle{k + 1}"
        out[name] = sorted(by_x[x])
    return out


def r9_mounts(spec: Record, req: Record, design: Record) -> list[Record]:
    """Three load cases (plate, weld, bolt) per group of corners (see the module doc)."""
    gs = groups(design)
    if not gs:
        raise BuildError("R9 needs supports with suspension.hardpoints_mm (the design has none)")
    load, keys = loading(req, design)
    extra = [k for k in spec.get("requirement_keys", []) if k not in keys]
    out = []
    for name, sids in gs.items():
        for kind, (qty, tool) in KINDS.items():
            out.append({"load_case_id": f"{TEST_ID}-{kind}-{name}", "test_id": TEST_ID,
                        "domain": spec.get("domain", "strength"),
                        "title": R9_TITLE.format(group=_GROUP_TITLE.get(name, name), what=_WHAT[kind], tool=tool),
                        "params": {"loading": load, "supports": ",".join(sids)},
                        "criterion": dict(R9_CRITERIA[qty]),
                        "requirement_keys": keys + extra, "tolerance_rel": tolerance(spec, R9_TOL)})
    return out


def _supports_of(load_case: Record) -> list[str]:
    p = load_case.get("params") or {}
    v = p.get("supports") or p.get("support") or ""
    return [s.strip() for s in (v.split(",") if isinstance(v, str) else v) if s.strip()]


def r9_inputs(design: Record, load_case: Record, upstream: dict | None = None) -> Record:
    """The mount_bracket formulas' inputs for the load case's corners: '<quantity>.<support>.<mount>.<input>' (each
    place) and '<quantity>.<input>' (the worst place over the corners), as quantities; plus 'worst.<quantity>' (the
    place '<support>.<mount>')."""
    up = upstream or load_case.get("upstream_analyses") or (load_case.get("params") or {}).get("upstream_analyses")
    if not up:
        raise NoForces(f"{load_case.get('load_case_id', 'R9')}: no upstream analyses with K2's mount series were given "
                       f"(R9 takes them from {', '.join(UPSTREAM_TESTS)}); the mount forces are not invented")
    out: Record = {}
    worst: dict[str, tuple[str, float]] = {}
    for sid in _supports_of(load_case):
        got = bracket_inputs(design, sid, up)
        for m, p in got["points"].items():
            for qty, w in p["worst"].items():
                for k, v in w["inputs"].items():
                    out[f"{qty}.{sid}.{m}.{k}"] = q(float(v), UNITS[k])
                if qty not in worst or w["utilisation"] > worst[qty][1]:
                    worst[qty] = (f"{sid}.{m}", w["utilisation"])
    for qty, (place, _) in worst.items():
        out[f"worst.{qty}"] = place
        for k in lb.INPUTS_OF[qty]:
            out[f"{qty}.{k}"] = out[f"{qty}.{place}.{k}"]
    return out


_ASSUME = {
    "bolt_utilisation": "ピンは 2 面せん断、軸部の断面 (ねじはせん断面の外)。強度区分 8.8。金具の板の支圧だけ (枠の壁は見ない)、"
                        "穴の縁までは最小の 1.2 d0。せん断と支圧の利用率の大きい方",
    "plate_bending_utilisation": "板は根元からの片持ち。板の外への曲げ・面内の曲げ・引きはがしを根元の角で足す (安全側)",
    "weld_utilisation": "板ごとに根元の両側のすみ肉 2 本 (のど厚 0.7 脚長)。線の力を足す (安全側)。STKR400 を S235 相当とみる (仮定)",
}
_COMMON = ["K2 の取付点の力 (車体が受ける力)。上流の解析ごとに |F| が最大の瞬間、その中で利用率が最大のもの",
           "A アームの腕に沿う力は、K2 が内の 2 つの付け根に半分ずつ分けている (仮定)",
           "ピンは車体の x 向き (板は車の横向きの面)",
           "溶接した長さ (式の b) は、金具の根元のうち管の壁の上にある長さ (bracket_inputs.weld_span、シェルと同じ)。箱の幅ではない"]


def _prediction(qty: str, place: str | None) -> Record:
    at = f"{qty}.{place}" if place else qty
    return {"quantity": at, "formula_id": lb.FORMULA_OF[qty], "output": "u",
            "assumptions": _COMMON + [_ASSUME[qty]] + ([] if place else ["その荷重条件の角の取付点のうち、いちばん大きいもの"]),
            "inputs": {k: f"from.{at}.{k}" for k in lb.INPUTS_OF[qty]}, "inputs_from": r9_inputs}


# The plate and weld hand formulas against the shell (the brackets meshed as plates): on the demo run
# 20261106-185605's version 5 regenerated with the lca bracket base across the rail wall, mount_plate_t 9 and
# mount_weld_leg 6.3, the hand calculation gives plate 0.51 / weld 0.40 at rail_b 40 (0.42 / 0.40 at rail_b 45); the
# shell on the study set A (v005 + the same bracket values) gives 1.00 / 0.80 at rail_b 40 and 0.81 / 0.63 at
# rail_b 45 (the analysis department, 2026-11-06). The shell decides (tool_decides): R9 has a criterion.
_SHELL_MISMATCH = {
    "source": ("the analysis department, 2026-11-06: the demo scripted run 20261106-185605 version 5 with the lca "
               "bracket base across the rail wall, mount_plate_t 9, mount_weld_leg 6.3: hand plate 0.51 / weld 0.40 "
               "(rail_b 40), 0.42 / 0.40 (rail_b 45); frame_shell on the same bracket values: 1.00 / 0.80 (rail_b 40), "
               "0.81 / 0.63 (rail_b 45)"),
    "cause": ("手計算は、金具の板を管の壁から出た片持ちの梁、溶接をのど厚の平均の応力とみる。シェルでは、ピンの穴の周りに"
              "力が集まり、レールの壁も曲がるので、板の根元と溶接の端に応力が集まる。手計算はシェルの約半分に出る"),
    "fix": ("合否はシェル (frame_shell) の値で出す。手計算は力の道と大きさの目安として残す。直すなら、金具の板を厚く "
            "(mount_plate_t)、溶接の脚を大きく (mount_weld_leg)、溶接する長さを長く (rail_b)"),
    "tool_decides": True,
    "tool_decides_reason": ("ピンの穴の周りの集中と壁の曲がりは、手の式で持てない。手計算は届かないので、力の道を手計算で"
                            "確かめたうえで、シェルの値で決める"),
}


def _path_prediction() -> Record:
    """load_path.PATH_PREDICTION (unsupported_load by load_path.free_parts), imported late (load_path imports the
    builders' common module too)."""
    from hiraku.catalog.builders.load_path import PATH_PREDICTION

    return dict(PATH_PREDICTION)


def _plan(kind: str, sids) -> Record:
    qty, tool = KINDS[kind]
    places = [f"{s}.{m}" for s in sids for m in MOUNTS]
    plan = {"tool": tool,
            "say": (f"サスペンションの取付点ごとに、K2 の力の最大で、{_WHAT[kind]}の利用率を先に出します "
                    f"(手計算。{tool} の値で判定)"),
            # the path check first (as C1 and the other strength plans): the load reaches the frame
            "predictions": [_path_prediction()] + [_prediction(qty, p) for p in (None, *places)]}
    if tool == "frame_shell":  # one entry per compared quantity (the group's worst and each place)
        plan["expected_mismatch"] = [dict(_SHELL_MISMATCH, quantity=f"{qty}.{p}" if p else qty)
                                     for p in (None, *places)]
    return plan


R9_PLANS = {f"{TEST_ID}-{kind}-{name}": _plan(kind, sids)
            for name, sids in GROUPS[GROUP_BY].items() for kind in KINDS}
R9_PLAN = _plan("weld", ())  # a load case of R9 not named above: the shell's weld, the group's worst only

BUILDERS = {TEST_ID: r9_mounts}
PLANS = {TEST_ID: R9_PLAN, **R9_PLANS}
FEEDS = {t: [TEST_ID] for t in UPSTREAM_TESTS}

__all__ = ["BUILDERS", "FEEDS", "GROUP_BY", "KINDS", "PLANS", "R9_CRITERIA", "R9_PLAN", "R9_PLANS", "corners", "groups",
           "r9_inputs", "r9_mounts"]

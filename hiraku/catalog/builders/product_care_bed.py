"""Load cases of the care bed (product care-bed, designs of hiraku.designgen.care_bed), built from its
requirements: S1 tipping under an edge load (Q60), R5 the deck under the safe working load and the side rail
pushed outward, P5 the back and knee actuators' force (Q56), T3 the actuators' duty (Q03), R3 fatigue over the
counts of use (Q92).

PRODUCT names the product these builders are for; hiraku.catalog.builders takes them in place of the general
builders of the same tests when the requirements' product is this one. PLANS holds the scripted step of the tests
whose step is the same for every case (P5); STEPS the step of each load case by its id (S1, R5): the general PLANS
of S1 (K2's tilt table) does not fit a bed, and R5's two cases use different formulas. T3 and R3 have no step: no
tool on the shelf takes them (the thermal tool drives a forest loop, the fatigue tool a rough road); their K0
predictions are here (T3_PREDICTIONS, R3_PREDICTIONS) for a tool made in the run.

Every number comes from a requirement or the design, or is computed here; the values that are neither are
constants below, each marked as an assumption.
"""

from __future__ import annotations

import math
import re

from hiraku.catalog.builders.common import BuildError, Record, entry, number, q, tolerance

PRODUCT = "care-bed"
G0 = 9.80665  # m/s^2 (hiraku.solvers.k0_handcalc.units.STANDARD_GRAVITY)

# S1: JIS T 9254 (copy, not checked in this repository; the requirement Q60 cites it) puts the edge load on a
# 475 x 250 mm area at the side edge; the force is taken at the area's centre, 125 mm in from the deck's edge
# (half of 250 mm), at the knee hinge's station (where a person sits on the edge: an assumption, the standard's
# position along the bed is not checked here).
EDGE_AREA_ACROSS_MM = 250.0
# The far casters' load is the same moment balance about the near caster line in the hand calculation and in
# the frame model (the frame's bending does not move it): 2 % (HIRAKU's choice).
S1_TOL = 0.02
# R5 deck: the hand calculation takes one side as a plane frame (rail, posts, base rail) under a uniform load;
# the 3D frame gives the posts less fixity (the base rails and cross members twist), so its post moment is
# lower: on the default design 21.1 MPa (K0) against 18.5 MPa (K1). 20 % (HIRAKU's choice).
R5_DECK_TOL = 0.20
# R5 side rail: a person in bed pushes the rail outward. HIRAKU's assumption (no source): half the user's weight
# (Q33) sideways at the top bar's middle. JIS T 9254 has a side rail strength test; its force is not checked here.
RAIL_PUSH_SHARE = 0.5
# The hand calculation shares the push equally between the two posts and holds their feet; the 3D frame's
# sockets sit on rails that twist differently at the two posts: 20 % (HIRAKU's choice, see the tests).
R5_RAIL_TOL = 0.20
# P5: the hand calculation leaves out the loads' heights above the hinge (they shorten the arm as the board
# rises), the tool turns them with the board: the hand side is higher (safe side). 20 % (HIRAKU's choice).
P5_TOL = 0.20
# T3 (no tool): each use of the bed raises the back and lowers it again: 2 strokes per use (assumption); the
# busiest hour holds every use of the day (assumption, the safe side).
STROKES_PER_USE = 2.0
T3_TOL = 0.10  # HIRAKU's choice for a tool that times the strokes (none on the shelf yet)
R3_TOL = 0.5  # as the vehicle's R3: damage goes with the cube of the stress range (HIRAKU's choice)
LOADING_USER = "driver"  # the first occupant: the user lying on the bed (the mattress is always on)
LOADING_EMPTY = "empty"  # the user off the bed (the mattress stays)


def _design_values(design: Record) -> dict[str, float]:
    return {k: float(v["value"]) for k, v in design.get("variables", {}).items()}


def _check_bed(design: Record) -> None:
    from hiraku.designgen import care_bed

    if not set(care_bed.SIGNATURE) <= set(design.get("variables", {})):
        raise BuildError("these load cases are for a care bed built by hiraku.designgen.care_bed")


def _check_load(req: Record, design: Record) -> list[str]:
    """The design must carry Q33 (the user) and Q40 (SWL) as its occupant and mattress parts; the keys used."""
    q33, q40 = number(req, "Q33", "N"), number(req, "Q40", "N")
    occ = sum(p["mass_kg"] for p in design["parts"] if p["kind"] == "occupant") * G0
    swl = occ + sum(p["mass_kg"] for p in design["parts"] if p["kind"] == "mattress") * G0
    if not math.isclose(occ, q33, rel_tol=1e-6) or not math.isclose(swl, q40, rel_tol=1e-6):
        raise BuildError(f"the design carries the user {occ:g} N and SWL {swl:g} N, the requirements say Q33 {q33:g} N "
                         f"and Q40 {q40:g} N; build the design from these requirements (designgen.propose)")
    return ["Q33", "Q40"]


def _min_yield(design: Record, members) -> tuple[float, str]:
    mats = {m["material"] for m in members}
    y = min(design["materials"][k]["yield_MPa"] for k in mats)
    names = sorted({design["materials"][k]["name"] for k in mats if design["materials"][k]["yield_MPa"] == y})
    checked = all(design["materials"][k]["verified"] for k in mats)
    return y, ", ".join(names) + ("" if checked else ", not checked")


def _safety(req: Record) -> float:
    sf = number(req, "Q61", "1")
    if sf <= 0:
        raise BuildError(f"Q61 must be positive, got {sf:g}")
    return sf


# ---------------------------------------------------------------- S1 tipping under the edge load


def _edge_point(design: Record) -> tuple[str, list[float]]:
    from hiraku.designgen import care_bed

    v = _design_values(design)
    st = care_bed.stations(v)
    y = v["deck_width"] / 2.0 - EDGE_AREA_ACROSS_MM / 2.0
    return "LK", [st["K"], y, care_bed.levels(v)["deck_top"]]


def s1_tipping(spec: Record, req: Record, design: Record) -> Record:
    _check_bed(design)
    keys = _check_load(req, design)
    F = number(req, "Q60", "N")
    node, p = _edge_point(design)
    return {
        "load_case_id": f"S1-edge{F:g}N",
        "test_id": "S1",
        "domain": spec["domain"],
        "title": f"側端に {F:g} N を下向きにかけて倒れないか (利用者は降り、マットレスは載せたまま)",
        "params": {"loading": LOADING_EMPTY, "load_node": node, "load_z": q(-F, "N"),
                   "load_point_x": q(p[0], "mm"), "load_point_y": q(p[1], "mm"), "load_point_z": q(p[2], "mm")},
        "requirement_keys": ["Q60", *keys],
        "criterion": {"quantity": "lighter_side_load", "op": ">=", "limit": q(0.0, "N"),
                      "basis": (f"Q60: with {F:g} N on the side edge ({EDGE_AREA_ACROSS_MM:g} mm wide area, its centre "
                                f"{EDGE_AREA_ACROSS_MM / 2:g} mm in from the edge) the casters of the far side keep a load "
                                "(below 0 the bed tips)"),
                      "requirement_keys": ["Q60"]},
        "tolerance_rel": tolerance(spec, S1_TOL),
    }


def s1_inputs(design: Record, load_case: Record) -> Record:
    """W, y_cg (toward the loaded side), F, y_F, y_s of bed.edge_load_tipping."""
    from hiraku.contract.loading import loading_of, on_board
    from hiraku.solvers.k0_handcalc import units
    from hiraku.solvers.k0_handcalc.vehicle import mass_summary

    p = load_case["params"]
    on = on_board(design, loading_of(load_case))
    ms = mass_summary(on)
    y_F = units.to_unit(p["load_point_y"], "mm")
    side = 1.0 if y_F >= 0 else -1.0
    y_s = min(abs(s["position_mm"][1]) for s in design["supports"] if s["position_mm"][1] * side > 0)
    return {"W": q(ms["mass_kg"] * G0, "N"), "y_cg": q(side * float(ms["cg_mm"][1]), "mm"),
            "F": q(-units.to_unit(p["load_z"], "N"), "N"), "y_F": q(abs(y_F), "mm"), "y_s": q(y_s, "mm")}


S1_STEP = {
    "tool": "bed_static",
    "say": "側端の力と、ベッドの重さの、近い側のキャスターの線のまわりのモーメントから、遠い側に残る力を先に出します",
    "predictions": [{"quantity": "lighter_side_load", "formula_id": "bed.edge_load_tipping", "output": "R_far",
                     "assumptions": ["ベッドは硬い", "床は水平", "力はまっすぐ下"],
                     "inputs": {k: f"from.{k}" for k in ("W", "y_cg", "F", "y_F", "y_s")}, "inputs_from": s1_inputs}],
}

# ---------------------------------------------------------------- R5 the deck and the side rail


def r5_static(spec: Record, req: Record, design: Record) -> list[Record]:
    """Two cases: the deck under SWL (the user and the mattress, Q33 and Q40) and the side rail pushed outward."""
    _check_bed(design)
    keys = _check_load(req, design)
    sf = _safety(req)
    from hiraku.catalog.build import min_frame_yield

    y, names, verified = min_frame_yield(design)
    deck = {
        "load_case_id": "R5-deck",
        "test_id": "R5",
        "domain": spec["domain"],
        "title": f"床に安全動作荷重 (Q40 {number(req, 'Q40', 'N'):g} N: 利用者とマットレス・付属品) を載せたときの枠",
        "params": {"loading": LOADING_USER},
        "requirement_keys": [*keys, "Q61"],
        "criterion": {"quantity": "max_stress", "op": "<=", "limit": q(y / sf, "MPa"),
                      "basis": (f"yield {y:g} MPa ({', '.join(names)}{'' if verified else ', not checked'}) / Q61 {sf:g} "
                                "(Q61 is the factor for parts that wear or tire; taken here for the static load too, "
                                "the safe side)"),
                      "requirement_keys": ["Q61"]},
        "tolerance_rel": tolerance(spec, R5_DECK_TOL),
    }
    F = RAIL_PUSH_SHARE * number(req, "Q33", "N")
    rail = [m for m in design["members"] if m.get("part") == "side_rail_L"]
    if not rail:
        raise BuildError("the design has no side rail members (part side_rail_L)")
    yr, names_r = _min_yield(design, rail)
    push = {
        "load_case_id": "R5-side-rail",
        "test_id": "R5",
        "domain": spec["domain"],
        "title": f"サイドレールの上の棒の真ん中を外へ {F:g} N で押したとき (利用者の体重 Q33 の {RAIL_PUSH_SHARE:.0%}、仮定)",
        "params": {"loading": LOADING_EMPTY, "load_node": "LSM", "load_y": q(F, "N")},
        "requirement_keys": ["Q33", "Q61"],
        "criterion": {"quantity": "max_stress", "op": "<=", "limit": q(yr / sf, "MPa"),
                      "basis": f"yield {yr:g} MPa ({names_r}) / Q61 {sf:g}; the push is an assumption (half of Q33)",
                      "requirement_keys": ["Q61"]},
        "tolerance_rel": tolerance(spec, R5_RAIL_TOL),
    }
    return [deck, push]


def deck_load_per_rail(design: Record, loading: str) -> float:
    """N on one upper rail: the weights the upper frame carries (its members, the sockets and side rails, and the
    parts' shares on its nodes) for this loading, half of it."""
    from hiraku.contract.loading import on_board
    from hiraku.designgen import care_bed
    from hiraku.solvers.k0_handcalc.vehicle import mass_summary

    on = on_board(design, loading)
    st = care_bed.stations(_design_values(design))
    upper = {f"{s}{k}" for s in "LR" for k in st}
    members = {m["id"]: m for m in design["members"]}
    W = 0.0
    for it in mass_summary(on)["items"]:
        if it.carrier == "member":
            m = members[it.carried_by[0]]
            if (m["node_a"] in upper and m["node_b"] in upper) or m["id"].startswith(("socket_", "side_rail_")):
                W += it.mass_kg
        elif it.carrier != "support":
            W += it.mass_kg * sum(1 for n in it.carried_by if n in upper) / len(it.carried_by)
    return W * G0 / 2.0


def r5_deck_inputs(design: Record, load_case: Record) -> Record:
    """The inputs of bed.deck_frame_udl: one side's rail, posts and base rail, and its load."""
    from hiraku.contract.loading import loading_of
    from hiraku.designgen import care_bed

    v = _design_values(design)
    st = care_bed.stations(v)
    z = care_bed.levels(v)
    E = design["materials"][care_bed.TUBE]["E_MPa"]
    mm = lambda x: q(x, "mm")  # noqa: E731
    return {"W": q(deck_load_per_rail(design, loading_of(load_case)), "N"), "L": mm(st["P1"] - st["P2"]),
            "a1": mm(st["H"] - st["P1"]), "a2": mm(st["P2"] - st["F"]), "h": mm(z["upper"] - z["base"]), "E": q(E, "MPa"),
            "b_r": mm(v["rail_b"]), "h_r": mm(v["rail_h"]), "t_r": mm(v["rail_t"]), "b_p": mm(v["post_b"]),
            "t_p": mm(v["post_t"]), "b_g": mm(v["base_b"]), "h_g": mm(v["base_h"]), "t_g": mm(v["base_t"])}


def r5_rail_inputs(design: Record, load_case: Record) -> Record:
    """The inputs of bed.side_rail_push: the push, the posts' length, the top bar's span, the tube."""
    from hiraku.solvers.k0_handcalc import units

    nodes = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
    v = _design_values(design)
    F = units.to_unit(load_case["params"]["load_y"], "N")
    return {"F": q(abs(F), "N"), "H": q(nodes["LSTP1"][2] - nodes["LSP1"][2], "mm"),
            "S": q(nodes["LSTP1"][0] - nodes["LSTK"][0], "mm"), "d": q(v["side_rail_d"], "mm"),
            "t": q(v["side_rail_t"], "mm")}


R5_DECK_STEP = {
    "tool": "bed_static",
    "say": "片側の上のレール・柱・下のレールを閉じた枠とみて、一様な荷重でのモーメントをたわみ角法で解き、いちばん大きい応力を先に出します",
    "predictions": [{"quantity": "max_stress", "formula_id": "bed.deck_frame_udl", "output": "sigma",
                     "assumptions": ["荷重は床の長さに一様", "片側を平面の枠とみる (横の部材は入れない)", "柱は横にずれない"],
                     "inputs": {k: f"from.{k}" for k in ("W", "L", "a1", "a2", "h", "E", "b_r", "h_r", "t_r", "b_p", "t_p",
                                                         "b_g", "h_g", "t_g")},
                     "inputs_from": r5_deck_inputs}],
}
R5_RAIL_STEP = {
    "tool": "bed_static",
    "say": "上の棒の真ん中を押す力を 2 本の柱が半分ずつ受け、片持ちの梁として根元で曲がるとみて、応力を先に出します",
    "predictions": [{"quantity": "max_stress", "formula_id": "bed.side_rail_push", "output": "sigma",
                     "assumptions": ["2 本の柱が半分ずつ受ける", "柱の根元 (差し込み) は回らない"],
                     "inputs": {k: f"from.{k}" for k in ("F", "H", "S", "d", "t")}, "inputs_from": r5_rail_inputs}],
}

# ---------------------------------------------------------------- P5 the back and knee actuators


def p5_actuators(spec: Record, req: Record, design: Record) -> list[Record]:
    _check_bed(design)
    keys = _check_load(req, design)
    v = _design_values(design)
    rated = v["actuator_rated_force"]
    out = []
    for which, what, angle, key in (("back", "背上げ", v["back_angle_max"], "Q56"),
                                    ("knee", "膝上げ", v["knee_angle_max"], None)):
        out.append({
            "load_case_id": f"P5-{which}",
            "test_id": "P5",
            "domain": spec["domain"],
            "title": f"{what}のアクチュエーターの力 (安全動作荷重 Q40 を載せて 0〜{angle:g}° 起こす)",
            "params": {"loading": LOADING_USER, "actuator": which},
            "requirement_keys": ([key] if key else []) + keys,
            "criterion": {"quantity": "actuator_force", "op": "<=", "limit": q(rated, "N"),
                          "basis": (f"the design variable actuator_rated_force {rated:g} N (the actuator picked; an "
                                    "assumption of the generator, not checked against a catalogue)"
                                    + ("" if key else "; the knee raise angle is the design's knee_angle_max (no "
                                                      "requirement gives it)"))},
            "tolerance_rel": tolerance(spec, P5_TOL),
        })
    return out


def p5_inputs(design: Record, load_case: Record) -> Record:
    """M0, r, a_x, a_z, theta_max of bed.lever_actuator_force: the board's loads about the hinge at the flat
    position, their heights left out (each part's weight times its distance along the board); for the knee, half
    the leg board's load at the thigh board's far end."""
    from hiraku.contract.loading import loading_of, on_board

    which = load_case["params"]["actuator"]
    on = on_board(design, loading_of(load_case))
    v = _design_values(design)
    nodes = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
    parts = {p["id"]: p for p in on["parts"]}
    hinge = nodes["LB" if which == "back" else "LK"]
    board = "back" if which == "back" else "thigh"
    M0 = sum(p["mass_kg"] * G0 * abs(p["box"]["center_mm"][0] - hinge[0])
             for pid, p in parts.items() if pid in (f"deck_{board}", f"mattress_{board}", f"occupant.{board}"))
    if which == "knee":
        leg = sum(p["mass_kg"] for pid, p in parts.items() if pid in ("deck_leg", "mattress_leg", "occupant.leg")) * G0
        M0 += leg / 2.0 * abs(nodes["LT"][0] - hinge[0])
    anchor_x = parts[f"bracket_{which}"]["box"]["center_mm"][0]
    pin_z = parts[f"actuator_{which}"]["box"]["center_mm"][2]
    return {"M0": q(M0, "N*mm"), "r": q(v[f"{which}_lever"], "mm"), "a_x": q(abs(anchor_x - hinge[0]), "mm"),
            "a_z": q(hinge[2] - pin_z, "mm"), "theta_max": q(v[f"{which}_angle_max"], "deg")}


_P5_ASSUME = ["準静的 (加速と摩擦は入れない)", "板の上の重さは板と一緒に回り、ちょうつがいからの距離は平らなときの水平の距離 (高さは入れない)"]
P5_PLAN = {
    "tool": "actuator_linkage",
    "say": "板の重さのちょうつがいまわりのモーメントを、アクチュエーターの線までの腕で割って、起こす角度ごとの力の最大を先に出します",
    "predictions": [
        {"quantity": "actuator_force", "formula_id": "bed.lever_actuator_force", "output": "F_max", "assumptions": _P5_ASSUME,
         "inputs": {k: f"from.{k}" for k in ("M0", "r", "a_x", "a_z", "theta_max")}, "inputs_from": p5_inputs},
        {"quantity": "actuator_stroke", "formula_id": "bed.lever_actuator_force", "output": "stroke",
         "assumptions": _P5_ASSUME, "inputs": {k: f"from.{k}" for k in ("M0", "r", "a_x", "a_z", "theta_max")},
         "inputs_from": p5_inputs},
    ],
}

# ---------------------------------------------------------------- T3 the actuators' duty (no tool on the shelf)


def _count(text: str, label: str | None = None) -> float:
    """The number before 回 in a requirement's text (after `label` when given)."""
    if label is not None:
        i = text.find(label)
        if i < 0:
            raise BuildError(f"no {label!r} in {text!r}")
        text = text[i + len(label):]
    m = re.search(r"([0-9][0-9,]*)\s*回", text)
    if not m:
        raise BuildError(f"no count (a number before 回) in {text!r}")
    return float(m.group(1).replace(",", ""))


def t3_duty(spec: Record, req: Record, design: Record) -> Record:
    _check_bed(design)
    q03 = entry(req, "Q03")
    if q03 is None or not isinstance(q03.get("value"), str):
        raise BuildError("requirement Q03 (how the bed is used, e.g. 1 日 6 回の乗り降り) is missing or not text")
    uses = _count(q03["value"])
    v = _design_values(design)
    return {
        "load_case_id": f"T3-{uses:g}uses",
        "test_id": "T3",
        "domain": spec["domain"],
        "title": f"背上げのアクチュエーターの使う割合 (1 日 {uses:g} 回の使い方をいちばん忙しい 1 時間に集める)",
        "params": {"loading": LOADING_USER, "actuator": "back", "uses_per_hour": q(uses, "1"),
                   "strokes_per_use": q(STROKES_PER_USE, "1")},
        "requirement_keys": ["Q03"],
        "criterion": {"quantity": "actuator_duty", "op": "<=", "limit": q(v["actuator_rated_duty"], "%"),
                      "basis": (f"the design variable actuator_rated_duty {v['actuator_rated_duty']:g} % (the actuator "
                                "picked; an assumption of the generator, not checked); Q03's uses all in one hour "
                                f"and {STROKES_PER_USE:g} strokes per use are assumptions")},
        "tolerance_rel": tolerance(spec, T3_TOL),
    }


def t3_inputs(design: Record, load_case: Record) -> Record:
    from hiraku.solvers import formula_registry
    from hiraku.solvers.k0_handcalc import units

    p = load_case["params"]
    lever = formula_registry().evaluate("bed.lever_actuator_force", p5_inputs(design, {
        **load_case, "params": {"loading": p["loading"], "actuator": p["actuator"]}}))["outputs"]
    return {"n": q(units.to_unit(p["uses_per_hour"], "1"), "1"), "k": q(units.to_unit(p["strokes_per_use"], "1"), "1"),
            "s": lever["stroke"], "v": design["variables"]["actuator_speed"]}


T3_PREDICTIONS = [{"quantity": "actuator_duty", "formula_id": "bed.actuator_duty", "output": "duty",
                   "assumptions": ["1 回の上げ下げは定格の速さ", "1 日の使い方を 1 時間に集める (いちばん忙しい時間)"],
                   "inputs": {k: f"from.{k}" for k in ("n", "k", "s", "v")}, "inputs_from": t3_inputs},
                  {"quantity": "actuator_run_time", "formula_id": "bed.actuator_duty", "output": "run_time",
                   "assumptions": ["1 回の動きは 1 ストローク"],
                   "inputs": {k: f"from.{k}" for k in ("n", "k", "s", "v")}, "inputs_from": t3_inputs}]

# ---------------------------------------------------------------- R3 fatigue over the counts of use (no tool on the shelf)

R3_USES = (("n_seat", "着座"), ("n_lift", "昇降"), ("n_raise", "背上げと膝上げ"), ("n_rail", "柵の上げ下げ"))


def r3_uses(spec: Record, req: Record, design: Record) -> Record:
    _check_bed(design)
    keys = _check_load(req, design)
    q92 = entry(req, "Q92")
    if q92 is None or not isinstance(q92.get("value"), str):
        raise BuildError("requirement Q92 (the counts of use) is missing or not text")
    counts = {name: _count(q92["value"], label) for name, label in R3_USES}
    return {
        "load_case_id": "R3-uses",
        "test_id": "R3",
        "domain": spec["domain"],
        "title": ("使う回数の疲労: " + "・".join(f"{label} {counts[n]:g} 回" for n, label in R3_USES)
                  + " (柵と昇降は枠の応力を変えないとみる)"),
        "params": {"loading": LOADING_USER, **{n: q(c, "1") for n, c in counts.items()}},
        "requirement_keys": ["Q92", *keys],
        "criterion": {"quantity": "fatigue_damage", "op": "<", "limit": q(1.0, "1"),
                      "basis": "Miner's rule over the counts of Q92: the welds last while the damage stays below 1",
                      "requirement_keys": ["Q92"]},
        "tolerance_rel": tolerance(spec, R3_TOL),
    }


def r3_inputs(design: Record, load_case: Record) -> Record:
    """bed.use_damage's inputs: use 1 the user getting on and off (the deck frame's stress with and without the
    user), use 2 the back raise and use 3 the knee raise (each actuator bracket lug's root stress at the largest
    force), use 4 the lift (0: the frame's stress does not change as the lift moves; the linkage is not modelled).
    The side rail's ups and downs load no weld that this design models and are left out."""
    from hiraku.solvers import formula_registry
    from hiraku.solvers.k0_handcalc import units

    reg = formula_registry()
    p = load_case["params"]
    v = _design_values(design)
    lc_user = {**load_case, "params": {"loading": LOADING_USER}}
    lc_empty = {**load_case, "params": {"loading": LOADING_EMPTY}}
    s_user = reg.evaluate("bed.deck_frame_udl", r5_deck_inputs(design, lc_user))["outputs"]["sigma"]["value"]
    s_empty = reg.evaluate("bed.deck_frame_udl", r5_deck_inputs(design, lc_empty))["outputs"]["sigma"]["value"]
    lug = {}
    parts = {x["id"]: x for x in design["parts"]}
    for which in ("back", "knee"):
        F = reg.evaluate("bed.lever_actuator_force", p5_inputs(design, {
            **load_case, "params": {"loading": LOADING_USER, "actuator": which}}))["outputs"]["F_max"]
        b = parts[f"bracket_{which}"]["box"]
        h_p = (b["center_mm"][2] + b["size_mm"][2] / 2.0) - parts[f"actuator_{which}"]["box"]["center_mm"][2]  # root to pin
        lug[which] = reg.evaluate("bed.lug_bending", {"F": F, "h_p": q(h_p, "mm"), "w": q(v["bracket_width"], "mm"),
                                                      "t": q(v["bracket_t"], "mm"), "d": q(v["pin_d"], "mm")})[
            "outputs"]["sigma_root"]["value"]
    n = {k: units.to_unit(p[k], "1") for k, _ in R3_USES}
    return {"n1": q(n["n_seat"], "1"), "dS1": q(s_user - s_empty, "MPa"),
            "n2": q(n["n_raise"], "1"), "dS2": q(lug["back"], "MPa"),
            "n3": q(n["n_raise"], "1"), "dS3": q(lug["knee"], "MPa"),
            "n4": q(n["n_lift"], "1"), "dS4": q(0.0, "MPa"),
            "FAT": design["variables"]["fatigue_fat_class"], "m": design["variables"]["fatigue_slope"],
            "N_knee": design["variables"]["fatigue_knee_cycles"], "m2": design["variables"]["fatigue_slope_after_knee"]}


R3_PREDICTIONS = [{"quantity": "fatigue_damage", "formula_id": "bed.use_damage", "output": "D",
                   "assumptions": ["着座: 利用者が乗り降りするたびに床の枠の応力が 0 から載せた分まで 1 回振れる",
                                   "背上げ・膝上げ: 金具の耳の根元が最大の力の応力まで 1 回振れる",
                                   "昇降: 枠の応力は変わらない (リンクは模型に無い)", "柵の上げ下げは入れない"],
                   "inputs": {k: f"from.{k}" for k in ("n1", "dS1", "n2", "dS2", "n3", "dS3", "n4", "dS4", "FAT", "m",
                                                       "N_knee", "m2")},
                   "inputs_from": r3_inputs}]

BUILDERS = {"S1": s1_tipping, "R5": r5_static, "P5": p5_actuators, "T3": t3_duty, "R3": r3_uses}
PLANS = {"P5": P5_PLAN}
STEPS = {"R5-deck": R5_DECK_STEP, "R5-side-rail": R5_RAIL_STEP}  # by load case id; S1's id carries Q60's force
STEP_PREFIXES = {"S1-edge": S1_STEP}  # by the start of the load case id


def step_for(load_case: Record) -> Record | None:
    """The scripted step of one of these load cases (None for T3 and R3: no tool on the shelf)."""
    lc_id = load_case["load_case_id"]
    if lc_id in STEPS:
        return STEPS[lc_id]
    for prefix, step in STEP_PREFIXES.items():
        if lc_id.startswith(prefix):
            return step
    return PLANS.get(load_case["test_id"])


__all__ = ["BUILDERS", "PLANS", "PRODUCT", "R3_PREDICTIONS", "STEPS", "T3_PREDICTIONS", "step_for"]

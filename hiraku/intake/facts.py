"""The numbers a rule can ask about: facts taken from a design, one set per thing a rule can hit.

A rule's check (hiraku.intake.expression) names facts. This module says which names exist, and
computes their values from a design (hiraku.contract "design") and the run's requirements. Every
number is computed here, by code. A rule that asks for a name the design cannot give is reported
as "not evaluable" with the name, so a rule is never answered by a guess.

What a rule can hit (a "target"):
  part     each design.parts entry: its box, mass, and the facts of the members that belong to it
  frame    each member whose `part` names no entry of design.parts (the demo's "frame"), under the member's own
           id: wall, outer size, section ratio ... of that member, so a hit names the tube
  support  each design.supports entry (wheel, caster, leg): suspension travel and stiffness, load
Boxes are read as they are given: size_x is the length along x (front-back), size_y across, size_z up;
the box rotation (rpy_deg) is not applied.
"""

from __future__ import annotations

import math
import re
from typing import Any

Record = dict[str, Any]

G = 9.80665  # m/s^2, standard gravity
WELD_THROAT_PER_LEG = 0.7  # a fillet weld's throat is 0.7 of its leg (the joint gives the leg)

# name -> (label, unit, scope). scope: part | member | support | design | declared
# "declared" names are part of the vocabulary; a part gives them in part["declared"] ({name: {value, unit, basis}}).
# A rule that uses one the part does not declare is reported as not evaluable, which tells the designer what to add.
FACTS: dict[str, tuple[str, str, str]] = {
    "mass_kg": ("質量", "kg", "part"),
    "size_x_mm": ("箱の前後の長さ", "mm", "part"),
    "size_y_mm": ("箱の左右の幅", "mm", "part"),
    "size_z_mm": ("箱の高さ", "mm", "part"),
    "size_min_mm": ("箱のいちばん短い辺", "mm", "part"),
    "size_max_mm": ("箱のいちばん長い辺", "mm", "part"),
    "thickness_mm": ("板厚 (箱のいちばん短い辺)", "mm", "part"),
    "center_x_mm": ("中心の x", "mm", "part"),
    "center_y_mm": ("中心の y", "mm", "part"),
    "center_z_mm": ("中心の高さ", "mm", "part"),
    "bottom_z_mm": ("箱の下端の高さ (地面から)", "mm", "part"),
    "top_z_mm": ("箱の上端の高さ (地面から)", "mm", "part"),
    "carried_by_count": ("支える節点の数", "1", "part"),
    "wall_mm": ("鋼管などの肉厚 (部材のうち最小)", "mm", "member"),
    "outer_mm": ("部材の断面の外形 (大きい方)", "mm", "member"),
    "section_ratio_max": ("断面の外形 / 肉厚 (部材ごとに出して最大)", "1", "member"),
    "section_ratio_rect_max": ("角形鋼管の外形 / 肉厚 (部材ごとに出して最大。角形鋼管でなければ 0)", "1", "member"),
    "section_ratio_round_max": ("円形鋼管の外径 / 肉厚 (部材ごとに出して最大。円形鋼管でなければ 0)", "1", "member"),
    "butt_end_count": ("角形鋼管の端のうち、別の部材の側面や角に突き当たる T・L の継ぎ手の数 (部材ごと。続きの部材が反対側にある端は数えない)", "1", "member"),
    "butt_end_ungusseted": ("その T・L の継ぎ手のうち、節点にガセット (kind が gusset の部品) が付いていないものの数", "1", "member"),
    "butt_end_partial_weld": ("その T・L の継ぎ手のうち、溶接が全周でないものの数 (節点の weld_extent が all_round でない。節点に weld_extent が無ければ、この名前は出さない)", "1", "member"),
    "member_count": ("部材の数", "1", "member"),
    "member_length_max_mm": ("部材のうち最長の長さ", "mm", "member"),
    "yield_MPa": ("部材の材料の降伏点 (最小)", "MPa", "member"),
    "travel_mm": ("サスのストローク", "mm", "support"),
    "stiffness_N_per_mm": ("サスのばね定数", "N/mm", "support"),
    "radius_mm": ("車輪の半径", "mm", "support"),
    "width_mm": ("車輪の幅", "mm", "support"),
    "static_load_N": ("接地点 1 つが支える静的な荷重 (全質量を均等に)", "N", "support"),
    "total_mass_kg": ("設計の全質量", "kg", "design"),
    "part_count": ("部品の数", "1", "design"),
    "support_count": ("接地点の数", "1", "design"),
    "bend_radius_mm": ("曲げの内側の半径", "mm", "declared"),
    "hole_edge_mm": ("穴の中心から縁までの距離", "mm", "declared"),
    "hole_diameter_mm": ("穴の径", "mm", "declared"),
    "bolt_diameter_mm": ("ボルトの呼び径", "mm", "declared"),
    "thread_engagement_mm": ("ねじのかみ合い長さ", "mm", "declared"),
    "weld_length_mm": ("溶接の長さ", "mm", "declared"),
    "weld_throat_mm": ("溶接ののど厚", "mm", "declared"),
    "clearance_mm": ("組み合う部品の隙間", "mm", "declared"),
    "tolerance_stack_mm": ("公差の積み上げ", "mm", "declared"),
    "clearance_hole_mm": ("隙間穴の径", "mm", "declared"),
    "position_tolerance_mm": ("位置の公差", "mm", "declared"),
    "closing_gap_mm": ("閉じる隙間", "mm", "declared"),
    # steering shaft joints (the part of kind steering)
    "uj_count": ("操舵の軸の折れる所 (自在継手) の数", "1", "declared"),
    "uj_angle_max_deg": ("操舵の軸の折れ角 (折れる所のうち最大)", "deg", "declared"),
    "uj_angle_diff_deg": ("2 か所の折れ角の差", "deg", "declared"),
    "uj_phase_error_deg": ("2 か所の継手の向き合わせ (位相) の、正しい向きからのずれ", "deg", "declared"),
    "steer_speed_variation_pct": ("ハンドルを一定に回したときの、車輪の側の回転のむら (折れる所が 1 か所のとき、折れ角から計算)", "%", "part"),
    # head restraint (the part of kind seat)
    "headrest_height_mm": ("頭を受け止める上端の高さ (座席の基準点から胴の線に沿って)", "mm", "declared"),
    "headrest_width_mm": ("頭を受け止める面の左右の幅 (上端から 65 mm 下)", "mm", "declared"),
    "headrest_backset_mm": ("頭の後ろと受け止める面との水平のすき間", "mm", "declared"),
    "headrest_gap_mm": ("背もたれと頭を受け止める部分のあいだのすき間", "mm", "declared"),
    # cargo securing (the part of kind cargo)
    "lashing_count": ("荷を止める固定 (帯・鎖) の本数", "1", "declared"),
    "topover_count": ("荷の上を越えて上から押さえる固定の本数", "1", "declared"),
    "lashing_pretension_N": ("上から押さえる固定 1 本の張り (小さい方)", "N", "declared"),
    "lashing_angle_deg": ("上から押さえる固定と荷台の床の角度 (最小)", "deg", "declared"),
    "lashing_angle_sin": ("上から押さえる固定の角度の sin", "1", "part"),
    "cargo_blocked_forward": ("荷が荷台の前の壁に当てて止めてあるか (1 = 止めてある、0 = ない)", "1", "declared"),
    "lashing_wll_min_N": ("荷につながる帯・金具・固定点のうち、使ってよい力の最小", "N", "part"),
    "lashing_wll_sum_N": ("荷につながる帯 (kind が strap) の使ってよい力の合計 (1 本ずつ 1 回数える)", "N", "part"),
    "anchor_strength_min_N": ("荷につながる固定点 (kind が anchor) が受けられる力の最小", "N", "part"),
    # wiring (a wire run = one wire or one bundle between two ends, the part of kind electrical)
    "wire_current_A": ("電線に続けて流れる電流", "A", "declared"),
    "wire_rated_current_A": ("電線 1 本が流してよい電流 (表の値、周りの温度と被覆の温度クラスのとき)", "A", "declared"),
    "wire_bundle_count": ("同じ束に触れ合って入っている電線の数 (1 本だけなら 1)", "1", "declared"),
    "wire_fuse_rating_A": ("その電線を守るヒューズ・ブレーカの定格電流", "A", "declared"),
    "wire_insulation_max_degC": ("電線の被覆が使える温度の上限", "degC", "declared"),
    "wire_place_max_degC": ("電線が通る場所のいちばん高い周りの温度", "degC", "declared"),
    "wire_voltage_rating_V": ("電線の定格の電圧", "V", "declared"),
    "wire_bend_radius_mm": ("電線を曲げる所の内側の半径 (最小)", "mm", "declared"),
    "wire_outer_diameter_mm": ("電線の外径 (束なら束の中でいちばん太いもの)", "mm", "declared"),
    "wire_support_spacing_mm": ("配線を支える点どうしの間隔 (最大)", "mm", "declared"),
    "wire_penetration_clearance_mm": ("板や縁を通り抜ける所で、留めていない配線と縁との隙間 (最小)", "mm", "declared"),
    "wire_control_clearance_mm": ("配線と、動かして操作する仕組みとの隙間 (最小)", "mm", "declared"),
    "battery_fuse_length_mm": ("電池の端子から最初に回路を守る器具までの電線の長さ", "mm", "declared"),
}
REQ_NAME = re.compile(r"^req_Q[0-9]{2}$")  # the number of a requirement, in the requirement's own unit
VAR_PREFIX = "var_"  # a design variable's value, in the variable's own unit


def is_known(name: str) -> bool:
    return name in FACTS or bool(REQ_NAME.match(name)) or name.startswith(VAR_PREFIX)


def unknown_names(names: list[str]) -> list[str]:
    return [n for n in names if not is_known(n)]


def vocabulary(design: Record | None = None) -> list[Record]:
    """The names for Claude and the screen: {name, label, unit, scope, in_designs}."""
    out = [{"name": n, "label": label, "unit": unit, "scope": scope, "in_designs": scope != "declared"}
           for n, (label, unit, scope) in FACTS.items()]
    out.append({"name": "req_Qnn", "label": "要件 Qnn の数値 (要件の単位のまま)", "unit": "", "scope": "design", "in_designs": True})
    out.append({"name": "var_<名前>", "label": "設計の変数の値 (変数の単位のまま)", "unit": "", "scope": "design", "in_designs": True})
    if design is not None:
        out[-1]["examples"] = [f"{VAR_PREFIX}{k}" for k in list(design.get("variables", {}))[:6]]
    return out


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def design_facts(design: Record, requirements: list[Record] | None = None) -> dict[str, float]:
    """Facts that are the same for every target: total mass, counts, design variables, requirement numbers."""
    parts = design.get("parts", [])
    supports = design.get("supports", [])
    total = sum(p["mass_kg"] for p in parts)
    out: dict[str, float] = {"total_mass_kg": total, "part_count": len(parts), "support_count": len(supports)}
    for name, q in design.get("variables", {}).items():
        out[VAR_PREFIX + name] = q["value"]
    for r in requirements or []:
        if _num(r.get("value")):
            out[f"req_{r['key']}"] = r["value"]
    return out


def _declared_facts(part: Record) -> dict[str, float]:
    """Values a part declares about itself (part["declared"]: {name: {value, unit "mm", basis}}), the names of the
    "declared" scope (bend radius, hole positions, bolt diameter ...). A name outside that vocabulary, a value that is
    not a number and a unit other than mm are not read: the rule that needs the fact stays not evaluable."""
    out: dict[str, float] = {}
    for name, d in (part.get("declared") or {}).items():
        if FACTS.get(name, ("", "", ""))[2] != "declared" or not isinstance(d, dict):
            continue
        if _num(d.get("value")) and d.get("unit", FACTS[name][1]) == FACTS[name][1]:  # each name has one unit
            out[name] = float(d["value"])
    joint = part.get("joint")  # the joint a part carries gives the same facts once (declared wins when both say it)
    if isinstance(joint, dict):
        if _num(joint.get("bolt_d_mm")):
            out.setdefault("bolt_diameter_mm", float(joint["bolt_d_mm"]))
        if _num(joint.get("weld_leg_mm")):
            out.setdefault("weld_throat_mm", WELD_THROAT_PER_LEG * float(joint["weld_leg_mm"]))
    return out


UNCONFIRMED = ("not checked", "assumption")  # a declared basis that says the value is not confirmed


def _declared_unchecked(part: Record) -> list[str]:
    """Names of the part's declared values whose basis says they are not confirmed (a bend radius assumed, a size not
    checked). A rule judged with one of them says so (rule_check.checked[].unchecked)."""
    read = _declared_facts(part)
    out = []
    for name, d in (part.get("declared") or {}).items():
        if name in read and any(m in str(d.get("basis", "")).lower() for m in UNCONFIRMED):
            out.append(name)
    return out


def _computed_facts(design: Record, part: Record, facts: dict[str, float]) -> dict[str, float]:
    """Facts worked out here, so the rule language needs no trigonometry and no sums over parts."""
    out: dict[str, float] = {}
    if "lashing_angle_deg" in facts:
        out["lashing_angle_sin"] = math.sin(math.radians(facts["lashing_angle_deg"]))
    # one universal joint: the output speed varies between cos(a) and 1/cos(a) times the input speed; two joints are not worked out
    if facts.get("uj_count") == 1 and "uj_angle_max_deg" in facts and facts["uj_angle_max_deg"] < 89:
        c = math.cos(math.radians(facts["uj_angle_max_deg"]))
        out["steer_speed_variation_pct"] = 100.0 * max(1.0 / c - 1.0, 1.0 - c)
    # the rated parts (part.rating: a strap's, a hook's, an anchor's working load) joined to this part, in either direction
    ids = {p["id"]: p for p in design.get("parts", [])}
    joined = {q["id"] for q in design.get("parts", []) if any(c.get("to") == part["id"] for c in q.get("connects_to", []))}
    joined |= {c.get("to") for c in part.get("connects_to", []) if c.get("to") in ids}
    rated = [ids[i] for i in joined if _num((ids[i].get("rating") or {}).get("working_load_N"))]
    if rated:
        out["lashing_wll_min_N"] = min(float(q["rating"]["working_load_N"]) for q in rated)
        straps = [q for q in rated if q.get("kind") == "strap"]
        if straps:
            out["lashing_wll_sum_N"] = sum(float(q["rating"]["working_load_N"]) for q in straps)
        # an anchor is joined to the strap, not to the load: look one step further
        reach = {q["id"] for q in design.get("parts", []) if any(c.get("to") in {s["id"] for s in straps} for c in q.get("connects_to", []))}
        reach |= {c.get("to") for s in straps for c in s.get("connects_to", []) if c.get("to") in ids}
        anchors = [ids[i] for i in (joined | reach) if ids[i].get("kind") == "anchor" and _num((ids[i].get("rating") or {}).get("working_load_N"))]
        if anchors:
            out["anchor_strength_min_N"] = min(float(a["rating"]["working_load_N"]) for a in anchors)
    return out


def _box_facts(part: Record) -> dict[str, float]:
    cx, cy, cz = part["box"]["center_mm"]
    sx, sy, sz = part["box"]["size_mm"]
    out = {
        "mass_kg": part["mass_kg"],
        "size_x_mm": sx, "size_y_mm": sy, "size_z_mm": sz,
        "size_min_mm": min(sx, sy, sz), "size_max_mm": max(sx, sy, sz), "thickness_mm": min(sx, sy, sz),
        "center_x_mm": cx, "center_y_mm": cy, "center_z_mm": cz,
        "bottom_z_mm": cz - sz / 2, "top_z_mm": cz + sz / 2,
    }
    if "carried_by" in part:
        out["carried_by_count"] = len(part["carried_by"])
    return out


ANTI_PARALLEL = -0.985  # cos of the angle (about 10 degrees) beyond which another member at a node continues this one


def _butt_facts(design: Record, members: list[Record]) -> dict[str, float]:
    """The T and L butt joints at the ends of these square tubes. A member's end is a butt joint when another member
    ends at the same node, is not the straight continuation of this one, and this one has no continuation (a rail
    that goes on past a cross member is not butted against it; the cross member is). Only rect tubes count."""
    nodes = {n["id"]: n for n in design.get("nodes", [])}
    sections = design.get("sections", {})
    all_members = design.get("members", [])
    at: dict[str, list[Record]] = {}
    for m in all_members:
        at.setdefault(m["node_a"], []).append(m)
        at.setdefault(m["node_b"], []).append(m)

    def direction(m: Record, node_id: str) -> list[float] | None:
        a, b = nodes.get(m["node_a"]), nodes.get(m["node_b"])
        if not a or not b:
            return None
        other = b if node_id == m["node_a"] else a
        here = nodes[node_id]["xyz_mm"]
        v = [o - h for o, h in zip(other["xyz_mm"], here)]
        n = math.hypot(*v)
        return [x / n for x in v] if n > 0 else None

    def rect(m: Record) -> bool:
        return sections.get(m["section"], {}).get("shape") == "rect_tube"

    gusseted = {n for p in design.get("parts", []) if p.get("kind") == "gusset" for n in p.get("carried_by") or []}
    count = ungusseted = partial = 0
    unknown_weld = False
    for m in members:
        if not rect(m):
            continue
        for node_id in (m["node_a"], m["node_b"]):
            if node_id not in nodes:
                continue
            mine = direction(m, node_id)
            others = [(k, direction(k, node_id)) for k in at.get(node_id, []) if k is not m and rect(k)]
            others = [(k, d) for k, d in others if d is not None]
            if mine is None or not others:
                continue
            dots = [sum(x * y for x, y in zip(mine, d)) for _, d in others]
            if any(dot <= ANTI_PARALLEL for dot in dots):
                continue  # this member goes on past the node
            if all(dot >= -ANTI_PARALLEL for dot in dots):
                continue  # only parallel members that start here too: not a corner or a T
            count += 1
            if node_id not in gusseted:
                ungusseted += 1
            extent = nodes[node_id].get("weld_extent")
            if extent is None:
                unknown_weld = True
            elif extent != "all_round":
                partial += 1
    out: dict[str, float] = {"butt_end_count": count, "butt_end_ungusseted": ungusseted}
    if not unknown_weld:
        out["butt_end_partial_weld"] = partial
    return out


def _member_facts(design: Record, members: list[Record]) -> dict[str, float]:
    nodes = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    sections = design.get("sections", {})
    materials = design.get("materials", {})
    walls: list[float] = []
    outers: list[float] = []
    ratios: list[float] = []
    rect_ratios: list[float] = []  # 0 for a member of another shape: a rule about square tubes passes on it
    round_ratios: list[float] = []
    lengths: list[float] = []
    yields: list[float] = []
    for m in members:
        dims = sections.get(m["section"], {}).get("dims_mm", {})
        if _num(dims.get("t")):
            walls.append(dims["t"])
        sizes = [v for k, v in dims.items() if _num(v) and not k.startswith("t")]
        if sizes:
            outers.append(max(sizes))
        if sizes and _num(dims.get("t")) and dims["t"] > 0:
            ratios.append(max(sizes) / dims["t"])  # per member: a thin wall on a small tube is not a thin wall on a big one
            shape = sections.get(m["section"], {}).get("shape")
            rect_ratios.append(ratios[-1] if shape == "rect_tube" else 0.0)
            round_ratios.append(ratios[-1] if shape == "round_tube" else 0.0)
        if m["node_a"] in nodes and m["node_b"] in nodes:
            lengths.append(math.dist(nodes[m["node_a"]], nodes[m["node_b"]]))
        y = materials.get(m["material"], {}).get("yield_MPa")
        if _num(y):
            yields.append(y)
    out: dict[str, float] = {"member_count": len(members)}
    out.update(_butt_facts(design, members))
    if walls:
        out["wall_mm"] = min(walls)
    if outers:
        out["outer_mm"] = max(outers)
    if ratios:
        out["section_ratio_max"] = max(ratios)
        out["section_ratio_rect_max"] = max(rect_ratios)
        out["section_ratio_round_max"] = max(round_ratios)
    if lengths:
        out["member_length_max_mm"] = max(lengths)
    if yields:
        out["yield_MPa"] = min(yields)
    return out


def _named_members(design: Record, part: Record, members_by_part: dict[str, list[Record]]) -> list[Record]:
    """The members a part without members of its own names in connects_to (a member id, or a part that has members)."""
    by_id = {m["id"]: m for m in design.get("members", [])}
    out: list[Record] = []
    for link in part.get("connects_to") or []:
        to = link.get("to")
        found = [by_id[to]] if to in by_id else members_by_part.get(to, [])
        out.extend(m for m in found if m not in out)
    return out


def targets(design: Record, requirements: list[Record] | None = None) -> list[Record]:
    """Everything a rule can hit in a design: [{id, type, kind, name, facts}], parts first."""
    shared = design_facts(design, requirements)
    members_by_part: dict[str, list[Record]] = {}
    for m in design.get("members", []):
        members_by_part.setdefault(m.get("part") or "frame", []).append(m)
    out: list[Record] = []
    part_ids = set()
    for p in design.get("parts", []):
        part_ids.add(p["id"])
        facts = {**shared, **_box_facts(p), **_declared_facts(p)}
        facts.update(_computed_facts(design, p, facts))
        if p["id"] in members_by_part:
            facts.update(_member_facts(design, members_by_part[p["id"]]))
        else:  # no member of its own: the section of the members it is connected to (connects_to names them)
            named = _named_members(design, p, members_by_part)
            if named:
                borrowed = _member_facts(design, named)
                borrowed.pop("member_count", None)  # these are not the part's own members
                facts.update(borrowed)
            mat = design.get("materials", {}).get(p.get("material"), {}).get("yield_MPa")
            if "yield_MPa" not in facts and _num(mat):
                facts["yield_MPa"] = mat
        out.append({"id": p["id"], "type": "part", "kind": p["kind"], "name": p["name"], "facts": facts,
                    "unchecked": _declared_unchecked(p)})
    for pid, members in members_by_part.items():
        if pid not in part_ids:
            for m in members:  # one target per member, under its id (the contract's rule_check names ids of the design)
                out.append({"id": m["id"], "type": "frame", "kind": "frame", "name": f"骨組みの部材 {m['id']}",
                            "facts": {**shared, **_member_facts(design, [m]), "member_count": len(members)}})
    n_support = len(design.get("supports", []))
    for s in design.get("supports", []):
        facts = {**shared, "radius_mm": s["radius_mm"]}
        if _num(s.get("width_mm")):
            facts["width_mm"] = s["width_mm"]
        susp = s.get("suspension", {})
        if _num(susp.get("travel_mm")):
            facts["travel_mm"] = susp["travel_mm"]
        if _num(susp.get("stiffness_N_per_mm")):
            facts["stiffness_N_per_mm"] = susp["stiffness_N_per_mm"]
        if n_support:
            facts["static_load_N"] = shared["total_mass_kg"] * G / n_support
        out.append({"id": f"support:{s['id']}", "type": "support", "kind": "support",
                    "name": f"接地点 {s['id']} ({s['kind']})", "facts": facts})
    return out

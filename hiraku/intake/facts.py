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
        if _num(d.get("value")) and d.get("unit", "mm") == "mm":
            out[name] = float(d["value"])
    joint = part.get("joint")  # the joint a part carries gives the same facts once (declared wins when both say it)
    if isinstance(joint, dict):
        if _num(joint.get("bolt_d_mm")):
            out.setdefault("bolt_diameter_mm", float(joint["bolt_d_mm"]))
        if _num(joint.get("weld_leg_mm")):
            out.setdefault("weld_throat_mm", WELD_THROAT_PER_LEG * float(joint["weld_leg_mm"]))
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
        out.append({"id": p["id"], "type": "part", "kind": p["kind"], "name": p["name"], "facts": facts})
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

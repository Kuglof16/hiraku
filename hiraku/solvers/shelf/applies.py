"""What a tool needs of a design, checked on the design dict alone (no model built, no solve): the parts of a
tool's applies(design) (BuiltinTool.applies, K2Tool.applies). Each check returns None when the design has it,
else a short reason in Japanese that names what is missing. A design Claude wrote (write_design) has no
archetype, so these checks, not the kind of design, decide whether a tool can run on it."""

from __future__ import annotations

from typing import Any, Iterable

Record = dict[str, Any]

TUBE_SHAPES = ("rect_tube", "round_tube")  # the sections the shell meshers build (k1_shell.mesh, fe.tubes)


def design_kind(design: Record) -> str | None:
    """The kind of design (hiraku.designgen ARCHETYPES key): design.archetype; None for a design Claude wrote
    (written_by); else the generator whose signature variables it carries (designgen.generator_of); None when
    none does."""
    if design.get("archetype"):
        return str(design["archetype"])
    if design.get("written_by"):
        return None
    try:
        from hiraku import designgen
    except ImportError:  # pragma: no cover
        return None
    try:
        gen = designgen.generator_of(design)
    except ValueError:
        return None
    return next((k for k, m in designgen.ARCHETYPES.items() if m is gen), None)


def _names(items: Iterable[str], most: int = 5) -> str:
    items = list(items)
    return ", ".join(items[:most]) + (f" ほか {len(items) - most}" if len(items) > most else "")


def first(*reasons: str | None) -> str | None:
    """The first reason that is not None."""
    return next((r for r in reasons if r), None)


def frame(design: Record) -> str | None:
    """A beam frame the K1 model builds: members whose nodes, sections and materials are in the design,
    materials with E_MPa, nu and density_kg_m3, and at least 3 supports on frame nodes that members use."""
    members = design.get("members") or []
    if not members:
        return "部材 (members) がありません (フレームの解析には骨組みが要ります)"
    nodes = {n.get("id") for n in design.get("nodes") or []}
    sections, materials = design.get("sections") or {}, design.get("materials") or {}
    bad = [m.get("id", "?") for m in members if m.get("node_a") not in nodes or m.get("node_b") not in nodes
           or m.get("section") not in sections or m.get("material") not in materials]
    if bad:
        return f"節・断面・材料の参照が設計に無い部材があります: {_names(bad)}"
    used = {m["material"] for m in members}
    short = [f"{k}.{f}" for k in sorted(used) for f in ("E_MPa", "nu", "density_kg_m3") if f not in materials[k]]
    if short:
        return f"材料に値がありません: {_names(short)}"
    on = {m["node_a"] for m in members} | {m["node_b"] for m in members}
    held = [s for s in design.get("supports") or [] if s.get("frame_node") in on]
    if len(held) < 3:
        return (f"フレームの節に付いた支持 (supports の frame_node) が {len(held)} 個です。フレームを支えるには 3 個以上が"
                "要ります")
    return None


def tube_sections(design: Record) -> str | None:
    """Every member's section a rect_tube or round_tube with dims_mm.t (the shell meshers' tubes)."""
    sections = design.get("sections") or {}
    used = sorted({m.get("section") for m in design.get("members") or []} & set(sections))
    bad = [k for k in used if sections[k].get("shape") not in TUBE_SHAPES or "t" not in (sections[k].get("dims_mm") or {})]
    if bad:
        return f"板の解析は角管・丸管 ({', '.join(TUBE_SHAPES)}、dims_mm.t つき) の部材だけを扱います。そうでない断面: {_names(bad)}"
    return None


def material_field(design: Record, field: str) -> str | None:
    used = {m.get("material") for m in design.get("members") or []}
    mats = design.get("materials") or {}
    bad = sorted(k for k in used if k in mats and field not in mats[k])
    return f"部材の材料に {field} がありません: {_names(bad)}" if bad else None


def variables(design: Record, names: Iterable[str], what: str) -> str | None:
    have = design.get("variables") or {}
    missing = [n for n in names if n not in have]
    if missing:
        return f"{what}に設計の変数 {', '.join(missing)} が要ります (design.variables に {{value, unit}} で書く)"
    return None


def support_with(design: Record, flag: str, what: str) -> str | None:
    """At least one support whose actuation has flag (drive, brake, steer)."""
    if not any((s.get("actuation") or {}).get(flag) for s in design.get("supports") or []):
        return f"{what}支持 (supports の actuation.{flag} が true) がありません"
    return None


def support_kind(design: Record, kind: str, what: str) -> str | None:
    if not any(s.get("kind") == kind for s in design.get("supports") or []):
        return f"{what} (supports の kind {kind}) がありません"
    return None


def wheels_with_radius(design: Record) -> str | None:
    """Every support but a leg has radius_mm > 0, and one at least is not a leg."""
    sup = design.get("supports") or []
    rolling = [s for s in sup if s.get("kind") != "leg"]
    if not rolling:
        return "転がる支持 (脚でない supports) がありません"
    bad = [s.get("id", "?") for s in rolling if not (isinstance(s.get("radius_mm"), (int, float)) and s["radius_mm"] > 0)]
    return f"支持に radius_mm がありません: {_names(bad)}" if bad else None


def part_kind(design: Record, kinds: Iterable[str], what: str) -> str | None:
    kinds = tuple(kinds)
    if not any(p.get("kind") in kinds for p in design.get("parts") or []):
        return f"{what} (parts の kind {' / '.join(kinds)}) がありません"
    return None


def vehicle(design: Record) -> str | None:
    """What the K2 vehicle model reads of each support (k2_vehicle.model.build_vehicle): kind, radius_mm (but a
    leg), suspension.type, actuation.drive / brake / steer; a kc_table names its table; and a mass."""
    sup = design.get("supports") or []
    if not sup:
        return "支持 (supports) がありません"
    bad = []
    for s in sup:
        sus, act = s.get("suspension") or {}, s.get("actuation") or {}
        if "kind" not in s or "type" not in sus or any(k not in act for k in ("drive", "brake", "steer")):
            bad.append(f"{s.get('id', '?')} (kind・suspension.type・actuation.drive/brake/steer)")
        elif sus["type"] == "kc_table" and not sus.get("kc_table"):
            bad.append(f"{s.get('id', '?')} (suspension.kc_table)")
    if bad:
        return f"車の動きのモデルが読む支持の欄がありません: {_names(bad)}"
    if not any(p.get("mass_kg") for p in design.get("parts") or []) and not design.get("members"):
        return "質量のある部品も部材もありません"
    return wheels_with_radius(design)


def kc_table(design: Record) -> str | None:
    if not any((s.get("suspension") or {}).get("type") == "kc_table" for s in design.get("supports") or []):
        return "K&C 表のサスペンション (supports の suspension.type kc_table) がありません (固いサスペンションの設計には使えません)"
    return None


# the care bed's linkage (hiraku.solvers.shelf.bed_tools.actuator_over_raise): per actuator its lever node,
# its bracket and actuator parts and its variables
BED_ACTUATORS = {
    "back": {"nodes": ("LB",), "parts": ("bracket_back", "actuator_back"), "vars": ("back_lever", "back_angle_max")},
    "knee": {"nodes": ("LK", "LT"), "parts": ("bracket_knee", "actuator_knee"), "vars": ("knee_lever", "knee_angle_max")},
}


def bed_actuator(design: Record, which: str) -> str | None:
    need = BED_ACTUATORS[which]
    nodes = {n.get("id") for n in design.get("nodes") or []}
    parts = {p.get("id") for p in design.get("parts") or []}
    have = design.get("variables") or {}
    missing = ([f"節 {n}" for n in need["nodes"] if n not in nodes] + [f"部品 {p}" for p in need["parts"] if p not in parts]
               + [f"変数 {v}" for v in need["vars"] if v not in have])
    if missing:
        return f"介護ベッド (care bed) の{'背' if which == 'back' else '膝'}上げの仕組みがありません ({_names(missing, 6)})"
    return None


def any_bed_actuator(design: Record) -> str | None:
    reasons = [bed_actuator(design, w) for w in BED_ACTUATORS]
    return None if not all(reasons) else reasons[0]


# ---------------------------------------------------------------- per tool: what its run reads of the design

def _powertrain_vars() -> tuple[str, ...]:
    from hiraku.solvers.k3_power_thermal.powertrain import VARIABLES

    return tuple(VARIABLES)


def _thermal_vars() -> tuple[str, ...]:
    from hiraku.solvers.k3_power_thermal.simulate import THERMAL_PARTS

    return tuple(f"{p}_{f}" for p in THERMAL_PARTS for f in ("heat_capacity", "thermal_resistance"))


FATIGUE_VARS = ("fatigue_fat_class", "fatigue_slope", "fatigue_knee_cycles")


def _drive_energy(d: Record) -> str | None:
    return first(variables(d, _powertrain_vars(), "電力の計算"), support_with(d, "drive", "駆動する"), wheels_with_radius(d))


def _seat_off_centre(d: Record) -> str | None:
    seats = [p for p in d.get("parts") or [] if p.get("kind") == "seat"]
    if not any(abs(float((p.get("box") or {}).get("center_mm", [0, 0, 0])[1])) > 1.0 for p in seats):
        return "乗り降りの口を見る座席 (parts の kind seat、中心が y = 0 から外れたもの) がありません"
    return None


def _load_path(d: Record) -> str | None:
    no_node = [s.get("id", "?") for s in d.get("supports") or [] if "frame_node" not in s]
    return first(f"支持に frame_node がありません: {_names(no_node)}" if no_node else None, frame(d))


def _k2(extra=None):
    return lambda d: first(vehicle(d), extra(d) if extra else None)


SHAPES = {
    # shelf (hiraku.solvers.shelf)
    "frame_static": frame,
    "frame_shell": lambda d: first(frame(d), tube_sections(d)),
    "modal": lambda d: first(frame(d), wheels_with_radius(d)),
    "step_climb": lambda d: first(frame(d), wheels_with_radius(d)),
    "drive_energy": _drive_energy,
    "thermal": lambda d: first(_drive_energy(d), variables(d, _thermal_vars(), "温度の計算")),
    "fatigue": lambda d: first(variables(d, FATIGUE_VARS, "疲労の計算 (S-N)"), frame(d)),
    "crash": lambda d: first(variables(d, ("bumper_stiffness", "bumper_stroke"), "衝突の計算"), frame(d),
                             material_field(d, "yield_MPa")),
    "spray_reach": lambda d: support_kind(d, "wheel", "泥はねを見る車輪"),
    "assembly": lambda d: None if (d.get("parts") or d.get("members")) else "部品も部材もありません",
    "entry_opening": _seat_off_centre,
    "load_path": _load_path,
    "bed_static": lambda d: first(frame(d), material_field(d, "yield_MPa")),
    "actuator_linkage": lambda d: first(any_bed_actuator(d), frame(d)),
    "use_fatigue": lambda d: first(bed_actuator(d, "back"), bed_actuator(d, "knee"),
                                   variables(d, FATIGUE_VARS + ("bracket_width", "bracket_t"), "使う回数の疲労の計算"),
                                   frame(d)),
    "actuator_duty": lambda d: first(any_bed_actuator(d), variables(d, ("actuator_speed",), "動かす時間の割合の計算")),
    # K2 (hiraku.solvers.k2_vehicle.shelf): the vehicle model's supports, and what the scenario drives
    "braking": _k2(lambda d: support_with(d, "brake", "ブレーキのある")),
    "crosswind": _k2(),
    "offroad_roll": _k2(),
    "tilt": _k2(),
    "turning_circle": _k2(lambda d: support_with(d, "steer", "かじを切る")),
    "lane_change": _k2(lambda d: support_with(d, "steer", "かじを切る")),
    "launch": _k2(lambda d: support_with(d, "drive", "駆動する")),
    "step_climb_dynamic": _k2(lambda d: support_with(d, "drive", "駆動する")),
    "kc_bench": _k2(kc_table),
    "crash_beam": lambda d: first(None if d.get("members") else "部材 (members) がありません",
                                  material_field(d, "yield_MPa")),
}
# frame_tubes has its own (shelf.frame_tubes.FrameTubes.shape: K&C supports whose body-side pickups are frame nodes)

# A design of no known kind (one Claude wrote) counts as the kind a tool's applies_to names when it has the
# kind's mark (BuiltinTool.applies; run() does not refuse on it): a care bed carries a mattress (the load
# product_care_bed's builders take as the SWL with the user).
KIND_MARKS = {"bed_static": lambda d: part_kind(d, ("mattress",), "介護ベッド (care bed) のマットレス")}


def shape_of(tool_name: str, design: Record) -> str | None:
    """SHAPES' answer for a tool (None for a tool without an entry)."""
    check = SHAPES.get(tool_name)
    return check(design) if check else None

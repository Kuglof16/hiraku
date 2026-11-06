"""frame_static: stresses, deflection and wheel loads of the frame (K1 static).

When an upstream analysis carries K2's mount force series (step_climb_dynamic:
mount_x/y/z.<support>.<mount> at tens of instants), the frame is also solved
at each instant with those forces at the design's hard points (in the body's
axes, gravity tilted with it) and the stresses go into field.frames, with the
largest at each instant as a series. The judged results stay those of the
upstream's vectors (the instant it picked).

Beside the overall max_stress (where = its member), each member's largest
von Mises is a result max_stress.<member id> (the stress field's per_member
values). Those are reported, not judged: the criterion names max_stress.
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc import units
from ..k0_handcalc.rigid import rpy_matrix
from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import DesignError
from ..k1_frame import analyze, build, stress_terms
from ..k1_frame.model import COMBO
from ..k1_frame.surface import surface_field
from . import fixtures, hot_spots, scene
from .base import (G_FACTOR, LOADING, BuiltinTool, LoadCaseError, Param, Predicts, Record, check_record, mesh_of,
                   on_board, param_list, param_value, plate_parts, result)

CLOSED_FORM_TOL = 1e-3
# R9 サスペンション取付: the corners a load case covers (mounts_r9 writes "supports", an axle's; "support" one corner)
R9_SUPPORTS = Param("supports", None, "R9: comma-separated corner support ids the load case covers, e.g. \"RL,RR\"")
R9_SUPPORT = Param("support", None, "R9: one corner support id (the load case of one corner)")
STEP_HEIGHT = Param("step_height", "mm", "height of the step the step_supports climb (without it: the vehicle at rest)")
STEP_SUPPORTS = Param("step_supports", None, "comma-separated support ids on the step, e.g. \"FL,FR\" "
                      "(without it: the front-most supports)")
JOINT_INPUTS = ("inputs other than F and W from hiraku.solvers.k0_handcalc.frame_inputs.rail_joint_inputs(design, "
                "loading=...); F = the push on the stepping wheel, W = its vertical load")
STEP_BENDING = "the rail between the axles only: misses the overhang behind the rear axle and the outboard wheel force"
FRAMES_ASSUMPTION = (
    "時刻ごとの応力 (field.frames): 上流の解析 {lc} の取付点ごとの力の時系列 (K2、車体が受ける力、世界の軸) を、その時刻の"
    "車体の姿勢 (sprung_roll・pitch・yaw) で車体の軸に回し、設計の hardpoints_mm の点に入れて解き直した。ばね下の部品の重さと"
    "慣性はその力に入っているので、ばね上の質量だけに重力 (車体の軸に回したもの) と慣性の釣り合いをかけた。駆動や制動が車体に"
    "返すトルク (body_torque) は、その支持の節にかけた。判定はこれまでどおり上流の vectors の瞬間の値で、時刻ごとの最大は参考")


NO_LOST_LOAD = ("どの部品の重さと慣性も、carried_by の節 (無ければいちばん近い支え) に載せた: 支えのない荷重は 0 "
                "(部品の形が枠に触れているかは見ていない。それは手計算の道の確かめ load_path.free_parts が見る)")


PATH_FORMULA = "load_path.free_parts"
PATH_PREDICTS = Predicts("unsupported_load", PATH_FORMULA, "n",
                         "the path check (hiraku.catalog.builders.load_path.PATH_PREDICTION): the parts the load case "
                         "loads, followed by their contacts to the members; the tool itself loses no load (0)")


def lost_load_outputs() -> tuple[Record, Record]:
    """The contract's unsupported_load of a frame tool, always 0, and the effect saying why (docs/contract.md
    手計算の役目): every part's load goes on its carried_by nodes or the nearest support (k0_handcalc.vehicle
    .mass_items; a carried_by node no member uses is refused before the solve)."""
    return result("unsupported_load", 0.0, "1"), {"name": NO_LOST_LOAD, "value": 0.0, "unit": "1"}


def frame_outputs(res: dict) -> Record:
    """results / fields / vectors / effects shared by the K1-based tools."""
    fm = res["model"]
    worst = res["worst_member"]
    node = res["worst_node"]
    safe_member = min(res["safety"], key=res["safety"].get)
    results = [
        result("max_stress", res["stresses"][worst]["max"], "MPa", worst),
        result("min_safety_factor", res["safety"][safe_member], "1", safe_member),
        result("max_displacement", float(np.linalg.norm(res["displacements"][node])), "mm", node),
        result("vehicle_mass", res["mass"]["mass_kg"], "kg"),
        result("frame_mass", res["mass"]["frame_mass_kg"], "kg"),
    ]
    for sid, g in res["ground"].items():
        results.append(result(f"wheel_load_{sid}", g["force_N"][2], "N", sid))
    fields = [
        {"kind": "stress", "label": "von Mises (max along each member)", "unit": "MPa",
         "per_member": {m: s["max"] for m, s in res["stresses"].items()}},
        {"kind": "displacement", "label": "frame deflection (wheel-centre rigid motion removed)", "unit": "mm",
         "per_node": {n: [float(v) for v in d] for n, d in res["displacements"].items()}},
    ]
    vectors = [{"kind": kind, "at": sid, "origin_mm": [float(v) for v in p], "vector_N": [float(v) for v in f]}
               for sid, g in res["ground"].items() for kind, p, f in g["vectors"]]
    cg = [float(v) for v in res["mass"]["cg_mm"]]
    vectors.append({"kind": "load", "at": "cg", "origin_mm": cg, "vector_N": [0.0, 0.0, -float(res["weight_N"])]})
    a = res["inertia_accel"]
    if float(np.linalg.norm(a)) * res["mass"]["mass_kg"] > 1e-6 * res["weight_N"]:
        vectors.append({"kind": "inertia", "at": "cg", "origin_mm": cg,
                        "vector_N": [float(v) for v in -res["mass"]["mass_kg"] * a]})
        results.append(result("inertia_acceleration", float(np.linalg.norm(a)), "m/s^2", "cg"))
    terms = stress_terms(res)
    for t in terms:  # the worst member's stress split into axial, the bendings and torsion (MPa each)
        part = t["name"].split(" (", 1)[0]
        results.append(result(f"max_stress_{part}", t["value"], "MPa", worst))
    lost, said = lost_load_outputs()
    results.append(lost)
    return {"results": results, "fields": fields, "vectors": vectors, "effects": terms + [said]}


def member_results(quantity: str, per_member: dict[str, float], unit: str = "MPa") -> list[Record]:
    """One result per member, `<quantity>.<member id>` with where = the member, in the dict's order.
    Reported only: the criterion is judged on the overall `quantity` (exact name match)."""
    return [result(f"{quantity}.{m}", float(v), unit, m) for m, v in per_member.items()]


def member_effect(quantity: str, per_member: dict[str, float], unit: str = "MPa") -> Record:
    """An effect naming where the overall largest of a per-member quantity is."""
    top = max(per_member, key=per_member.get)
    return {"name": (f"{quantity} per member: {len(per_member)} results {quantity}.<member id>; the largest "
                     f"({quantity}) is in {top}"), "value": float(per_member[top]), "unit": unit}


def _beam(length, section, material=fixtures.STEEL):
    return build(fixtures.beam_design(length, section, material))


def frame_known_answers() -> list[Record]:
    """K1 against closed-form beam results (also used by step_climb)."""
    out = []
    E = fixtures.STEEL["E_MPa"]
    nu = fixtures.STEEL["nu"]
    G = E / (2 * (1 + nu))
    b, h, t = 40.0, 80.0, 2.0
    Iz = (b * h**3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12.0
    Iy = (h * b**3 - (h - 2 * t) * (b - 2 * t) ** 3) / 12.0
    A = b * h - (b - 2 * t) * (h - 2 * t)
    rect = {"shape": "rect_tube", "dims_mm": {"b": b, "h": h, "t": t}}
    L, P = 1000.0, 1000.0

    fm = _beam(L, rect)
    fm.model.def_support("A", True, True, True, True, True, True)
    fm.add_force("B", (0.0, 0.0, -P))
    fm.solve()
    src = "cantilever with an end load: deflection P L^3 / (3 E I), root stress P L (h/2) / I"
    out.append(check_record("cantilever 40x80x2 tube, 1 kN down at the tip", "tip_deflection",
                            P * L**3 / (3 * E * Iz), -fm.displacement("B")[2], "mm", CLOSED_FORM_TOL,
                            src + "; vertical load uses the h (strong) axis"))
    out.append(check_record("cantilever 40x80x2 tube, 1 kN down at the tip", "root_stress",
                            P * L * (h / 2) / Iz, fm.member_stress("m")["max"], "MPa", CLOSED_FORM_TOL, src))

    fm = _beam(L, rect)
    fm.model.def_support("A", True, True, True, True, True, True)
    fm.add_force("B", (0.0, P, 0.0))
    fm.solve()
    out.append(check_record("cantilever 40x80x2 tube, 1 kN sideways at the tip", "tip_deflection",
                            P * L**3 / (3 * E * Iy), fm.displacement("B")[1], "mm", CLOSED_FORM_TOL,
                            src + "; sideways load uses the b (weak) axis"))

    span = 2000.0
    fm = _beam(span, rect)
    # PyNite axes here: X along the beam, Y = design z (up), Z = -design y
    fm.model.def_support("A", True, True, True, True, False, False)
    fm.model.def_support("B", False, True, True, False, False, False)
    fm.add_force("m~2", (0.0, 0.0, -P))
    fm.solve()
    out.append(check_record("simply supported 2 m span, 1 kN at mid-span", "mid_deflection",
                            P * span**3 / (48 * E * Iz), -fm.displacement("m~2")[2], "mm", CLOSED_FORM_TOL,
                            "simply supported beam, centre load: P L^3 / (48 E I)"))

    fm = _beam(span, rect)
    fm.model.def_support("A", True, True, True, True, False, False)
    fm.model.def_support("B", False, True, True, False, False, False)
    fm.model.add_member_self_weight("FY", -STANDARD_GRAVITY * 1000.0, "load")
    fm.solve()
    w = fixtures.STEEL["density_kg_m3"] * 1e-12 * A * STANDARD_GRAVITY * 1000.0
    out.append(check_record("simply supported 2 m span under its own weight", "mid_deflection",
                            5 * w * span**4 / (384 * E * Iz), -fm.displacement("m~2")[2], "mm", CLOSED_FORM_TOL,
                            "simply supported beam, uniform load: 5 w L^4 / (384 E I), w = density * A * g"))

    d, tt = 50.0, 3.0
    J = math.pi / 32.0 * (d**4 - (d - 2 * tt) ** 4)
    T = 500_000.0
    fm = _beam(L, {"shape": "round_tube", "dims_mm": {"d": d, "t": tt}})
    fm.model.def_support("A", True, True, True, True, True, True)
    fm.add_force("B", (0.0, 0.0, 0.0), (T, 0.0, 0.0))
    fm.solve()
    twist = fm.model.nodes["B"].RX[COMBO]
    out.append(check_record("cantilever 50x3 round tube, 500 N*m torque at the tip", "tip_twist",
                            T * L / (G * J), twist, "rad", CLOSED_FORM_TOL,
                            "torsion of a round tube: twist T L / (G J)"))
    out.append(check_record("cantilever 50x3 round tube, 500 N*m torque at the tip", "von_mises",
                            math.sqrt(3.0) * T * (d / 2) / J, fm.member_stress("m")["max"], "MPa", CLOSED_FORM_TOL,
                            "shear T r / J, von Mises sqrt(3) tau"))

    lad = fixtures.ladder_design()
    res = analyze(lad)
    m = res["mass"]["mass_kg"]
    a = -float(res["mass"]["cg_mm"][0])
    Lw = 2000.0
    front = sum(res["ground"][s]["force_N"][2] for s in ("FL", "FR"))
    out.append(check_record("symmetric ladder at rest on four rigid wheels", "front_axle_load",
                            m * STANDARD_GRAVITY * (Lw - a) / Lw, front, "N", CLOSED_FORM_TOL,
                            "lever rule m g (L - a) / L (exact for a frame symmetric left to right)"))
    total = sum(g["force_N"][2] for g in res["ground"].values())
    out.append(check_record("symmetric ladder at rest on four rigid wheels", "total_wheel_load",
                            m * STANDARD_GRAVITY, total, "N", 1e-9, "sum of reactions equals the weight"))
    return out


class FrameStatic(BuiltinTool):
    name = "frame_static"
    title = "フレームの静的 (3D の梁)"
    domain = "strength"
    base = "K1"
    tests = ["R1", "R9"]
    formulas = [
        "mount_bracket.pin",
        "mount_bracket.pin_shear",
        "mount_bracket.pin_bearing",
        "frame_static.rail_joint_stress",
        "frame_static.rail_joint_stress_closed",
        "frame_static.rail_step_bending",
        "frame_static.rail_bending",
        "frame_static.simple_beam_point_load",
        "frame_static.simple_beam_center_load",
        "frame_static.simple_beam_udl",
        "frame_static.cantilever_tip_load",
        "frame_static.bending_stress",
        "mass_props.static_wheel_loads",
        PATH_FORMULA,
    ]
    load_case_params = (LOADING, G_FACTOR, STEP_HEIGHT, STEP_SUPPORTS, R9_SUPPORTS, R9_SUPPORT)
    predicts = (
        Predicts("bolt_utilisation", "mount_bracket.pin", "u",
                 "R9 pins: the same formulas from K2's mount forces (the tool and the prediction agree by construction)"),
        Predicts("max_stress", "frame_static.rail_joint_stress", "sigma", JOINT_INPUTS),
        Predicts("max_stress", "frame_static.rail_step_bending", "sigma", STEP_BENDING),
        Predicts("max_stress", "frame_static.rail_bending", "sigma", "one rail as a simply supported beam, one wheel force"),
        Predicts("wheel_load_<support>", "mass_props.static_wheel_loads", "wheel_load_<support>",
                 "at rest only (no step_height, no upstream forces)"),
        PATH_PREDICTS,
    )
    upstream = ("an analysis with reaction and grip vectors at every support (step_climb, step_climb_dynamic): "
                "its ground forces load the frame instead of step_height")
    needs_mesh = True  # the beam stress is also drawn on the version's shell mesh

    def known_answers(self) -> list[Record]:
        return frame_known_answers()

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: load_case_params. Run params: upstream_analyses {load
        case id: analysis}; when one of them carries ground forces (reaction and
        grip vectors) at every support, those forces load the frame instead of
        being worked out here, and an "inertia" vector at "cg" in it is checked
        against what the forces leave unbalanced (inertia relief takes that part).
        mesh: the version's shell mesh record; the beam stress on the tubes'
        outer surface is added as a field with one value per mesh element.
        """
        design = on_board(design, load_case)
        if load_case.get("test_id") == "R9":  # the pins of the suspension mounts (r9_bolt_outputs)
            return r9_bolt_outputs(design, load_case, params.get("upstream_analyses"))
        g = g_factor(load_case)
        up = upstream_ground_forces(params.get("upstream_analyses"), design)
        step = param_value(load_case, "step_height", "mm")
        sup = None
        if up is None and step is not None:
            sup = param_list(load_case, "step_supports") or front_supports(design)
        res = analyze(design, g_factor=g, step_mm=None if up else step, step_supports=sup,
                      ground_forces=up[1] if up else None, inertia_N=up[2] if up else None)
        out = frame_outputs(res)
        per_member = {m: s["max"] for m, s in res["stresses"].items()}  # the same values as the stress field
        out["results"] += member_results("max_stress", per_member)
        out["effects"].append(member_effect("max_stress", per_member))
        hot = beam_hot_spots(self, load_case, per_member)
        if hot is not None:
            out["hot_spots"] = hot
        series = upstream_mount_series(params.get("upstream_analyses"), design)
        if series:
            add_frames(out, beam_frames(design, series))
        mesh, why = mesh_of(design, params)
        if mesh is not None:
            plates = plate_parts(design)
            out["fields"].append(surface_field(res["model"], mesh, plates=plates))
            on = plates & set(mesh["element_member"])
            kind = {q["id"]: q.get("kind") for q in design.get("parts", [])}
            if any(kind.get(p) == "gusset" for p in on):
                out["effects"].append({"name": "the gusset plates on the mesh are drawn at 0: the beam model carries "
                                               "them as mass only"})
            if any(kind.get(p) == "bracket" for p in on):
                out["effects"].append({"name": "the mount brackets' plates on the mesh are drawn at 0: the beam model "
                                               "carries them as mass only"})
        elif why:
            out["effects"].append({"name": why})
        if up is None and step is not None:
            out["scene"] = scene.steps(design, sup, step)
        return out


def r9_supports(load_case: Record) -> list[str]:
    """The corners an R9 load case covers: params.supports (comma-separated, an axle's: mounts_r9) or params.support."""
    sids = param_list(load_case, "supports") or param_list(load_case, "support")
    if not sids:
        raise LoadCaseError("an R9 load case names its corners in params.supports (or one in params.support)")
    return sids


R9_BOLT_ASSUMPTIONS = [
    "R9 (pins): K2's mount forces (the force the body receives) through the hand formulas of the pin (mount_bracket."
    "pin_shear, pin_bearing; k0_handcalc.bracket_inputs): no model of the pin or the plates; the largest |F| of each "
    "upstream analysis, the worst utilisation over them",
    "R9 (pins): bolt_utilisation is the larger of bolt_shear_utilisation and bolt_bearing_utilisation (mount_bracket."
    "pin): one criterion per load case",
]


def r9_bolt_outputs(design: Record, load_case: Record, upstream: dict | None) -> Record:
    """frame_static's record for an R9 pin load case: bolt_utilisation (and the shear and bearing utilisations) at each
    place '<support>.<mount>' of the load case's corners ('<quantity>.<support>.<mount>'), '<quantity>' the worst of
    them (its where the place); hot_spots one row per place for the criterion's quantity."""
    from ..k0_handcalc import library_bracket as lb
    from ..k0_handcalc.bracket_inputs import NoForces, bracket_inputs

    qs = (lb.BOLT_QUANTITY, "bolt_shear_utilisation", "bolt_bearing_utilisation")
    per: dict[tuple[str, str], Record] = {}
    used: list[str] = []
    assumptions = list(R9_BOLT_ASSUMPTIONS)
    for sid in r9_supports(load_case):
        try:
            got = bracket_inputs(design, sid, upstream)
        except NoForces as e:
            raise LoadCaseError(str(e)) from None
        used = list(dict.fromkeys(used + got["used"]))
        assumptions += [a for a in got["assumptions"] if a not in assumptions]
        for m, p in got["points"].items():
            for q in qs:
                if q in p["worst"]:
                    per[(q, f"{sid}.{m}")] = dict(p["worst"][q], bracket=p["bracket"])
    results, effects = [], []
    for q in qs:
        rows = {w: v for (qq, w), v in per.items() if qq == q}
        for w, v in sorted(rows.items()):
            results.append(result(f"{q}.{w}", v["utilisation"], "1", w))
        if rows:
            w, v = max(rows.items(), key=lambda kv: kv[1]["utilisation"])
            results.insert(len(results) - len(rows), result(q, v["utilisation"], "1", w))
    for (q, w), v in sorted(per.items()):
        if q == lb.BOLT_QUANTITY:
            effects.append({"name": f"{q} at {w} ({v['bracket'] or 'no bracket part'}): {v['load_case_id']} t "
                                    f"{v['t_s']:.3f} s, F {[round(x, 1) for x in v['F_N']]} N", "value": v["utilisation"],
                            "unit": "1"})
    out = {"results": results, "fields": [], "effects": effects, "assumptions": assumptions}
    crit = (load_case or {}).get("criterion") or {}
    if crit.get("quantity") in qs and crit.get("op") in ("<=", "<"):
        limit = float(crit["limit"]["value"])
        rows = [hot_spots.row(w, q, v["utilisation"], limit, unit="1",
                              note=f"{v['bracket'] or 'no bracket part'}: {v['load_case_id']} t {v['t_s']:.3f} s")
                for (q, w), v in per.items() if q == crit["quantity"]]
        if rows:
            out["hot_spots"] = hot_spots.top(rows)
    out["effects"].append({"name": "R9 pins: upstream analyses used: " + ", ".join(used)})
    return out


def beam_hot_spots(tool, load_case: Record, per_member: dict[str, float]) -> list[Record] | None:
    """analysis.hot_spots of the beam model (hot_spots module): one row per member, its largest von Mises
    (the per-member max_stress, as max_stress is judged), against the criterion's limit. None (no rows) when
    the load case's criterion is not a "<=" stress, or when it is judged on a quantity the beam has no
    per-member values of (only max_stress is)."""
    judged = hot_spots.judged_limit(tool, load_case)
    if judged is None or judged[0] != "max_stress":
        return None
    q, limit = judged
    return hot_spots.top([hot_spots.row(m, q, v, limit) for m, v in per_member.items()])


def upstream_mount_series(upstream: dict | None, design: Record):
    """(load case id, analysis id, instants) from the first upstream analysis whose series carry K2's mount
    forces (mount_x/y/z.<support>.<mount>, N, world axes) for every support and the sprung body's attitude
    (sprung_roll/pitch/yaw, deg); None when none does. Each instant: {"t_s", "mount_loads" (k1_frame.analyze's,
    (mount, point, force) at the design's suspension hardpoints_mm and each support's body_torque_x/y/z, in
    the body's axes), "gravity_mps2" (body axes; from the series gravity_x/y/z, world axes, when given, else
    straight down), "inertia_N" (inertia_force_x/y/z in the body's axes, or None)}."""
    ids = [s["id"] for s in design.get("supports", [])]
    hp = {s["id"]: (s.get("suspension") or {}).get("hardpoints_mm") or {} for s in design.get("supports", [])}
    for lc_id, ana in (upstream or {}).items():
        if not isinstance(ana, dict) or ana.get("status") != "ok":
            continue
        ch = {c["channel"]: c for c in ana.get("series", []) if c.get("t_s")}
        mounts: dict[str, list[str]] = {}
        for name in ch:
            if name.startswith("mount_x."):
                _, sid, mount = name.split(".", 2)
                mounts.setdefault(sid, []).append(mount)
        attitude = [ch.get(f"sprung_{a}") for a in ("roll", "pitch", "yaw")]
        if set(mounts) != set(ids) or not all(attitude):
            continue
        missing = [f"{sid}.{m}" for sid in ids for m in mounts[sid] if m not in hp[sid]]
        if missing:
            raise DesignError(f"the upstream analysis {lc_id} gives forces at mounts the design has no "
                              f"suspension.hardpoints_mm for: {', '.join(missing)}")
        t = attitude[0]["t_s"]

        def values(name: str, unit: str) -> list[float] | None:
            c = ch.get(name)
            if c is None:
                return None
            if c["t_s"] != t:
                raise DesignError(f"the upstream analysis {lc_id}: series {name} is not on the attitude's times")
            return [units.to_unit({"value": v, "unit": c["unit"]}, unit, name) for v in c["values"]]

        att = [values(f"sprung_{a}", "deg") for a in ("roll", "pitch", "yaw")]
        force = {(sid, m): [values(f"mount_{a}.{sid}.{m}", "N") for a in "xyz"] for sid in ids for m in mounts[sid]}
        torque = {sid: [values(f"body_torque_{a}.{sid}", "N*mm") for a in "xyz"] for sid in ids}
        inertia = [values(f"inertia_force_{a}", "N") for a in "xyz"]
        # gravity in world axes as K2 gives it (turned on a slope or a tilt table); straight down without it
        grav = [values(f"gravity_{a}", "m/s^2") for a in "xyz"]
        if not all(grav):
            grav = [[0.0] * len(t), [0.0] * len(t), [-STANDARD_GRAVITY] * len(t)]
        instants = []
        for k, tk in enumerate(t):
            Rt = rpy_matrix([att[0][k], att[1][k], att[2][k]]).T  # world -> body
            loads = {}
            for sid in ids:
                vs = [(m, np.asarray(hp[sid][m], float), Rt @ np.array([f[k] for f in force[(sid, m)]]))
                      for m in mounts[sid]]
                tq = np.array([c[k] if c else 0.0 for c in torque[sid]])
                loads[sid] = {"vectors": vs, "moment_Nmm": Rt @ tq}
            instants.append({"t_s": float(tk), "mount_loads": loads,
                             "gravity_mps2": Rt @ np.array([g[k] for g in grav]),
                             "inertia_N": Rt @ np.array([c[k] for c in inertia]) if all(inertia) else None})
        return lc_id, ana.get("analysis_id"), instants
    return None


def beam_frames(design: Record, series) -> Record:
    """frame_static's outputs over the instants of upstream_mount_series: field.frames (von Mises max along
    each member), the series of the largest, its largest instant as a result and an effect."""
    lc_id, ana_id, instants = series
    per_member, peak, where = [], [], []
    for inst in instants:
        r = analyze(design, mount_loads=inst["mount_loads"], gravity_mps2=inst["gravity_mps2"],
                    inertia_N=inst["inertia_N"])
        per_member.append({m: round(st["max"], 2) for m, st in r["stresses"].items()})
        peak.append(r["stresses"][r["worst_member"]]["max"])
        where.append(r["worst_member"])
    t = [inst["t_s"] for inst in instants]
    k = int(np.argmax(peak))
    frames = {"t_s": t, "per_member": per_member, "max": [round(v, 2) for v in peak]}
    if ana_id:
        frames["source_analysis_id"] = ana_id
    return {
        "fields": [{"kind": "stress", "label": "von Mises (max along each member) at each instant of the upstream "
                                               "mount forces", "unit": "MPa", "frames": frames}],
        "series": [{"channel": "max_stress", "unit": "MPa", "t_s": t, "values": [round(v, 3) for v in peak]}],
        "results": [result("max_stress_over_time", peak[k], "MPa", f"{where[k]} at t {t[k]:.3f} s")],
        "effects": [{"name": (f"frame solved at {len(t)} instants of {lc_id}'s mount forces (t {t[0]:.3f} to "
                              f"{t[-1]:.3f} s): largest {peak[k]:.1f} MPa in {where[k]} at t {t[k]:.3f} s"),
                     "value": peak[k], "unit": "MPa"}],
        "assumptions": [FRAMES_ASSUMPTION.format(lc=lc_id)],
    }


def add_frames(out: Record, more: Record) -> None:
    """Append beam_frames' (or the shell's) outputs to a tool's out."""
    for key in ("fields", "series", "results", "effects", "assumptions"):
        if more.get(key):
            out[key] = list(out.get(key, [])) + more[key]


def upstream_ground_forces(upstream: dict | None, design: Record):
    """(load case id, {support id: [(kind, point, force), ...]}, inertia force or None) from the first
    upstream analysis that has a reaction vector at every support of the design."""
    ids = [s["id"] for s in design.get("supports", [])]
    for lc_id, ana in (upstream or {}).items():
        if not isinstance(ana, dict) or ana.get("status") != "ok":
            continue
        got: dict[str, list] = {}
        inertia = None
        for v in ana.get("vectors", []):
            if v.get("kind") in ("reaction", "grip") and v.get("at") in ids:
                got.setdefault(v["at"], []).append((v["kind"], v["origin_mm"], v["vector_N"]))
            elif v.get("kind") == "inertia" and v.get("at") == "cg":
                inertia = np.asarray(v["vector_N"], float)
        if set(got) == set(ids) and all(any(k == "reaction" for k, _, _ in vs) for vs in got.values()):
            return lc_id, got, inertia
    return None


def g_factor(load_case: Record) -> float:
    """Vertical load factor: a plain number (unit "1") or an acceleration (e.g. 2 g)."""
    p = load_case.get("params", {}).get("g_factor")
    if p is None:
        return 1.0
    if isinstance(p, dict) and units.UNIT_TABLE.get(p.get("unit"), (0, 0, None))[2] == units.dimension("g"):
        return param_value(load_case, "g_factor", "g")
    return param_value(load_case, "g_factor", "1")


def front_supports(design: Record) -> list[str]:
    sup = design.get("supports", [])
    xmax = max(s["position_mm"][0] for s in sup)
    return [s["id"] for s in sup if abs(s["position_mm"][0] - xmax) < 1e-6]

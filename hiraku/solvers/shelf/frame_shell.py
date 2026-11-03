"""frame_shell: stresses of the frame on the shell mesh of the tube walls (K1 shell, test R1).

The same loads as frame_static (K1 beams) put on the mid-surface shell
model (hiraku.solvers.k1_shell.frame), so the stress at the joints (wall
bending where one tube meets the side of another, the load coming in at a
suspension mount) shows. The beam model is solved with the same ground
forces for comparison, and the effects say how the shell's peak splits into
membrane and wall bending, how it compares with the beam at the same place
and away from the joints, and what loads enter the joint next to it.

The quantity to judge is max_hot_spot_stress: the largest IIW structural
hot-spot stress along the joints' weld lines (k1_shell.stress.hot_spots, on
the mesh's weld strips). max_stress, the largest element stress, sits at a
sharp re-entrant corner of a joint where the shell's stress is singular, so
it grows as the elements shrink; it is kept as a reference.
"""

from __future__ import annotations

import numpy as np

from ..k1_frame import analyze as beam_analyze
from ..k1_frame import stress_terms
from ..k1_frame.surface import wall_von_mises
from ..k1_shell.frame import analyze_shell, element_place, membrane_bending
from ..k1_shell.stress import CORNER_SKIP_T, HOT_SPOT_READ_T, hot_spot_factors
from . import scene
from .base import G_FACTOR, LOADING, BuiltinTool, Predicts, Record, mesh_of, on_board, param_list, param_value, result
from .frame_static import (JOINT_INPUTS, STEP_BENDING, STEP_HEIGHT, STEP_SUPPORTS, front_supports, g_factor,
                           upstream_ground_forces)

WALL_INPUTS = ("inputs from hiraku.solvers.k0_handcalc.frame_inputs.joint_wall_inputs(design, loading=..., "
               "step_mm=<step_height or None>, step_supports=...): the joint (not at a wheel mount) where the "
               "side wall's plate bending is largest; the shell's hot spot is that wall bending at a cross member's end, "
               "read at the weld line")
AWAY_FACTOR = 2.0  # "away from the joints": element centre at least this many tube sizes from every design node
LABELS = {
    "max": "シェルの von Mises (外と内の面の大きい方)",
    "top": "シェルの von Mises (外の面)",
    "bottom": "シェルの von Mises (内の面)",
}
LABEL_MEMBER = "シェルの von Mises (部材ごとの最大)"
LABEL_HOT_SPOT = "構造的ホットスポット応力 (溶接線に直角、部材ごとの絶対値の最大)"
LABEL_BEAM = "梁の von Mises (部材ごとの最大、シェルと同じ地面の力)"
LABEL_DISP = "シェルの変位 (輪のつなぎ目の剛体の動きを除く)"
SOURCES = {
    "given": "ground forces from the upstream analysis {lc}",
    "K0 step": "ground forces from K0 rigid-body statics (support_loads, step {step:g} mm on {sup})",
    "K0 rest": ("ground forces from K0 rigid-body statics at rest (support_loads, the rigid body on its springs); "
                "the frame's own flexibility does not redistribute the wheel loads as in frame_static at rest"),
}


def _tube_size(design: Record, member: str) -> float:
    m = next(m for m in design["members"] if m["id"] == member)
    d = design["sections"][m["section"]]["dims_mm"]
    return float(max(d.get("b", 0.0), d.get("h", 0.0), d.get("d", 0.0)))


def _vec(v, nd: int = 1) -> list[float]:
    return [round(float(x), nd) for x in v]


MOUNT_ASSUMPTION = ("輪の力 (とばね下の重さ) は、support の frame_node を通る管の 4 面の壁の、節から管の高さ h の範囲 "
                    "(前後 ± h) の節に RBE3 で分けて入れる。取付金具の大きさとして仮定した (確かめていない)")
EXCLUDE_ASSUMPTION = ("取付の区画 (節がすべて上の範囲にある要素) は、最大の応力・安全率・応力集中の倍率・膜と曲げの内訳を"
                      "比べる場所から外した。実物では金具が溶接で重なって壁を補う所で、力を入れる所の局所の値はつなぎ目の"
                      "評価に使えないため。値は field に残す")
GUSSET_ASSUMPTION = ("ガセット (kind gusset の部品) はシェルでは板の要素 (材料は横の部材と同じ、脚の 2 辺は溶接で全長が"
                     "つながる) として入り、梁 (K1) では質量だけとして入る")
THROUGH_ASSUMPTION = ("横の部材を通す形 (cross_members_through = 1): シェルでは横の部材の 4 面の壁が手前の壁の穴を通って"
                      "向こうの壁の中面まで伸び、両方の壁と節を共有する。梁 (K1) の形は変わらない。レールの中を通る分 "
                      "(部品 through_<部材>_<節>、手前の壁の穴の分を引いた正味) は、梁と K0 では節にかかる部品として入り、"
                      "シェルでは横の部材の要素が持つので部品としては足さない")
HOT_SPOT_ASSUMPTION = (
    "判定に使う量は max_hot_spot_stress (IIW の構造的ホットスポット応力): つなぎ目の溶接線 (横の部材の壁が相手の管の壁に"
    "当たる線) の両側に幅 t (その壁の厚さ) の要素の帯を 2 列置き、溶接線から {a:g} t と {b:g} t の要素の中心の面の応力の、"
    "溶接線に直角な成分を線まで直線で外挿した値 ({fa:g} σ({a:g}t) {fb:+g} σ({b:g}t))。外と内の面の両方を読む。足跡の角から"
    "溶接線に沿って {skip:g} t より近い所 (角の正方形) と、取付の区画の要素は読まない。出典: A. F. Hobbacher, Recommendations "
    "for Fatigue Design of Welded Joints and Components, 2nd ed., Springer 2016 (このリポジトリでは確かめていない)。"
    "ガセットの脚の溶接線は読んでいない")
PEAK_ASSUMPTION = ("max_stress はつなぎ目の鋭い入隅の角の要素の値で、シェルではそこで応力が特異になるため、要素を細かくすると"
                   "大きくなる。参考として残し、判定には使わない")
MASS_ASSUMPTION = ("質量の二重数えを避けるため、シェルの要素が持つ部品 (板として入れたガセットと、通す形にした節の "
                   "through_ の部品) はシェルでは部品として足さない。全体の質量と重心 (mass_summary、K0・K1・K2) "
                   "にはどちらも部品として入っている。シェルの管の質量 (rho t 面積) と梁の質量 (密度 A L) の差は慣性の"
                   "釣り合いに任せる")


SIDE = {"host": "wall the branch meets", "branch": "branch's wall"}


def hot_spot_assumption() -> str:
    fa, fb = hot_spot_factors()
    a, b = HOT_SPOT_READ_T
    return HOT_SPOT_ASSUMPTION.format(a=a, b=b, fa=fa, fb=fb, skip=CORNER_SKIP_T)


def hot_spot_outputs(res: dict) -> tuple[list[Record], list[Record], list[Record]]:
    """results, fields and effects of the structural hot spots (none without weld strips or hot spots)."""
    hs, top = res.get("hot_spots") or [], res.get("hot_spot_max")
    if top is None:
        return [], [], [{"name": "no structural hot spot read (no shared joint with weld strips outside the wheel mounts)"}]
    v = top["value"]
    results = [result("max_hot_spot_stress", abs(v), "MPa", top["member"])]
    per_member: dict[str, float] = {}
    per_joint: dict[str, dict] = {}
    for h in hs:
        per_member[h["member"]] = max(per_member.get(h["member"], 0.0), abs(h["value"]))
        if h["joint"] not in per_joint or abs(h["value"]) > abs(per_joint[h["joint"]]["value"]):
            per_joint[h["joint"]] = h
    fields = [{"kind": "stress", "label": LABEL_HOT_SPOT, "unit": "MPa",
               "per_member": {m: round(x, 2) for m, x in per_member.items()}}]
    a, b = HOT_SPOT_READ_T
    fa, fb = hot_spot_factors()
    effects = [
        {"name": (f"structural hot spot max {abs(v):.1f} MPa (the quantity to judge, max_hot_spot_stress): joint "
                  f"{top['joint']} ({top['kind']} joint), on the {SIDE[top['side']]} ({top['member']}, {top['place']}, "
                  f"strip on the {top['towards']} side of the weld line), "
                  f"{'outer' if top['surface'] == 'top' else 'inner'} surface, at {_vec(top['position_mm'])} mm on the weld "
                  f"line; stress square to the weld line {top['sigma_05t']:.1f} MPa at {a:g} t and {top['sigma_15t']:.1f} "
                  f"MPa at {b:g} t (t {top['t_mm']:g} mm), extrapolated {fa:g} x {top['sigma_05t']:.1f} {fb:+g} x "
                  f"{top['sigma_15t']:.1f} = {v:+.1f} MPa"), "value": abs(v), "unit": "MPa"},
        {"name": (f"hot spots read: {len(hs)} (element pairs at {a:g} t and {b:g} t, outer and inner surface) along "
                  f"{len(res['mesh'].weld_lines)} weld line segments at {len(per_joint)} joints; the {CORNER_SKIP_T:g} t "
                  f"corner squares and the wheel mounts not read"), "value": len(hs), "unit": "1"},
    ]
    for node in sorted(per_joint):
        h = per_joint[node]
        effects.append({"name": (f"largest hot spot at joint {node} ({h['kind']}): {SIDE[h['side']]} ({h['member']}, "
                                 f"{h['place']}, strip towards {h['towards']}), "
                                 f"{'outer' if h['surface'] == 'top' else 'inner'} surface, signed "
                                 f"{h['value']:+.1f} MPa"), "value": abs(h["value"]), "unit": "MPa"})
    return results, fields, effects


def shell_outputs(design: Record, res: dict, beam: dict, up_lc: str | None, step, sup) -> Record:
    """results / fields / vectors / effects of one frame_shell analysis."""
    worst, e = res["worst_member"], res["worst_element"]
    peak = res["member_max"][worst][0]
    bworst = beam["worst_member"]
    ms = res["mesh"]
    members = {m["id"] for m in design["members"]}
    beam_member = ms.part_material.get(worst, worst)  # a gusset is compared with the branch it is welded to
    safe = min(res["safety"], key=res["safety"].get)
    m_tot = res["total_mass_kg"]
    results = [
        result("max_stress", peak, "MPa", worst),
        result("max_stress_beam", beam["stresses"][bworst]["max"], "MPa", bworst),
        result("min_safety_factor", res["safety"][safe], "1", safe),
        result("max_displacement", float(np.linalg.norm(res["displacements"][res["worst_node"]])), "mm",
               res["worst_node_member"]),
        result("vehicle_mass", m_tot, "kg"),
        result("frame_mass", res["shell_mass_kg"], "kg"),
        result("residual_ratio", res["residual_ratio"], "1"),
    ]
    for sid, g in res["ground"].items():
        results.append(result(f"wheel_load_{sid}", g["force_N"][2], "N", sid))
    hs_results, hs_fields, hs_effects = hot_spot_outputs(res)
    results = hs_results + results

    cg = _vec(res["cg_mm"], 3)
    vectors = [{"kind": kind, "at": sid, "origin_mm": [float(v) for v in p], "vector_N": [float(v) for v in f]}
               for sid, g in res["ground"].items() for kind, p, f in g["vectors"]]
    vectors.append({"kind": "load", "at": "cg", "origin_mm": cg, "vector_N": [0.0, 0.0, -float(res["weight_N"])]})
    a = res["inertia_accel"]
    if float(np.linalg.norm(a)) * m_tot > 1e-6 * res["weight_N"]:
        vectors.append({"kind": "inertia", "at": "cg", "origin_mm": cg, "vector_N": [float(v) for v in -m_tot * a]})
        results.append(result("inertia_acceleration", float(np.linalg.norm(a)), "m/s^2", "cg"))

    fields = [
        {"kind": "stress", "label": LABEL_MEMBER, "unit": "MPa",
         "per_member": {m: round(v, 2) for m, (v, _) in res["member_max"].items() if m in members}},
        {"kind": "stress", "label": LABEL_BEAM, "unit": "MPa",
         "per_member": {m: round(s["max"], 2) for m, s in beam["stresses"].items()}},
    ] + hs_fields

    # ---- effects: the material for explaining the shell peak
    place = element_place(res, e)
    mb = membrane_bending(res, e)
    beam_same = beam["stresses"][beam_member]["max"]
    fx = res["ground_source"]
    where = (f"{place['s_mm']:.0f} mm from its node_a" if worst in members else
             f"gusset plate welded to {beam_member}, {place['s_mm']:.0f} mm from its corner along the through tube")
    effects = hs_effects + [
        {"name": (f"shell max {peak:.1f} MPa (max_stress, a reference: the element peak at a sharp re-entrant corner, "
                  f"where the shell's stress is singular, so it grows as the elements shrink): element {e} of {worst}, "
                  f"{where}, {place['wall']} wall, {place['node_distance_mm']:.0f} mm from node {place['node']} "
                  f"({ms.joint_kinds.get(place['node'], '?')} joint); "
                  f"{'inner' if mb['inner_vm'] > mb['outer_vm'] else 'outer'} surface"),
         "value": peak, "unit": "MPa"},
        {"name": f"shell max / beam max in {beam_member} (stress concentration at the joint)",
         "value": peak / beam_same if beam_same > 0 else 0.0, "unit": "1"},
        {"name": f"beam max in {beam_member} (same ground forces)", "value": beam_same, "unit": "MPa"},
    ]
    if worst in members:
        beam_here = float(wall_von_mises(beam["model"], worst, [place["centre_mm"]])[0])
        effects.append({"name": f"beam stress at the same point of {worst} (outer surface, beam theory)",
                        "value": beam_here, "unit": "MPa"})
    effects += [
        {"name": "outer surface von Mises at the shell max", "value": mb["outer_vm"], "unit": "MPa"},
        {"name": "inner surface von Mises at the shell max", "value": mb["inner_vm"], "unit": "MPa"},
    ]
    tot = mb["membrane_vm"] + mb["bending_vm"]
    effects += [
        {"name": "membrane part at the shell max ((outer + inner) / 2, von Mises)", "value": mb["membrane_vm"],
         "unit": "MPa", "share": mb["membrane_vm"] / tot if tot > 0 else 0.0},
        {"name": "wall bending part at the shell max ((outer - inner) / 2, von Mises)", "value": mb["bending_vm"],
         "unit": "MPa", "share": mb["bending_vm"] / tot if tot > 0 else 0.0},
        {"name": f"stress along {worst} at the shell max, membrane part", "value": mb["axial_membrane"], "unit": "MPa"},
        {"name": f"stress along {worst} at the shell max, wall bending part", "value": mb["axial_bending"], "unit": "MPa"},
    ]
    jl = res["joint_loads"].get(place["node"])
    if jl is not None:
        effects.append({"name": f"loads put into joint {place['node']} (spread over its ring and wall nodes): force "
                                f"{_vec(jl[0])} N, moment about the node {_vec(jl[1] / 1000.0)} N*m",
                        "value": float(np.linalg.norm(jl[0])), "unit": "N"})
    # away from the joints: does the shell agree with the beam there?
    st = res["stress"]
    nodes = np.array(list(res["frame"].node_xyz.values()))
    dist = np.min(np.linalg.norm(st.centres[:, None, :] - nodes[None, :, :], axis=2), axis=1)
    size = {m: _tube_size(design, m) for m in res["member_max"] if m in members}
    far = np.array([m in size and dist[i] >= AWAY_FACTOR * size[m] and not res["excluded"][i]
                    for i, m in enumerate(ms.element_member)])
    if far.any():
        idx = np.nonzero(far)[0]
        f_e = int(idx[np.argmax(res["von_mises"]["max"][idx])])
        f_m = res["mesh"].element_member[f_e]
        f_beam = float(wall_von_mises(beam["model"], f_m, [st.centres[f_e]])[0])
        f_val = float(res["von_mises"]["max"][f_e])
        effects.append({"name": f"shell max away from the joints (>= {AWAY_FACTOR:g} tube sizes from every node): "
                                f"{f_m}, element {f_e}", "value": f_val, "unit": "MPa"})
        effects.append({"name": f"beam stress at that point of {f_m} (outer surface)", "value": f_beam, "unit": "MPa"})
    src = SOURCES[fx].format(lc=up_lc, step=step if step is not None else 0.0, sup=",".join(sup or []))
    n_ex = int(np.sum(res["excluded"]))
    if ms.mounts:
        mounts = ", ".join(f"{fn} +- {ms.mount_half_mm[fn]:g} mm" for fn in ms.mounts if fn in res["frame"].support_node.values())
        effects.append({"name": (f"wheel loads put in over the walls of the tube through each support's frame node ({mounts}); "
                                 f"{n_ex} elements inside those mounts left out of the peak, the safety factors, the "
                                 f"stress concentration and the membrane / bending split (their stresses stay in the "
                                 f"fields)"), "value": n_ex, "unit": "1"})
        if res["excluded_max"] is not None:
            v, ee = res["excluded_max"]
            effects.append({"name": f"largest stress inside the mounts (left out): element {ee} of {ms.element_member[ee]}",
                            "value": v, "unit": "MPa"})
    effects += [
        {"name": src},
        {"name": "hold reaction / weight (six restraints that only stop rigid-body motion)",
         "value": res["residual_ratio"], "unit": "1"},
        {"name": "ground forces against the weight (and inertia passed in): imbalance / weight",
         "value": res["balance_off"], "unit": "1"},
        {"name": (f"tube mass from the shell elements (rho t area) {res['shell_mass_kg']:.2f} kg vs "
                  f"{res['mass']['frame_mass_kg']:.2f} kg from density A L (beam); the difference is left to inertia relief"),
         "value": res["shell_mass_kg"] - res["mass"]["frame_mass_kg"], "unit": "kg"},
        {"name": (f"shell model: {ms.n_nodes} nodes, {ms.n_elements} elements "
                  f"(edge {ms.element_size_mm:g} mm); wheel loads enter as RBE3 forces on the mount nodes, part loads "
                  f"on the joint nodes (ring and wall patch); sharp corners, no welds"
                  + ("; strips of t x t elements beside the joints' weld lines (grid lines at t and 2t from them, and "
                     "at t, 2t and 3t from the footprints' corners along them)" if ms.weld_strips else "")),
         "value": res["timings"].get("total_s", 0.0), "unit": "s"},
        {"name": "largest element aspect ratio (longest / shortest edge)",
         "value": float(ms.aspect_ratios().max()), "unit": "1"},
    ]
    if ms.part_material:
        effects.append({"name": (f"{len(ms.part_material)} gussets meshed as plates (in the shell's mass, so not added "
                                 f"again as parts); the beam model carries them as mass only"),
                        "value": len(ms.part_material), "unit": "1"})
    if ms.through_joints:
        effects.append({"name": (f"cross members through the rails at {', '.join(ms.through_joints)} (shell only; the "
                                 f"beam model is unchanged)"), "value": len(ms.through_joints), "unit": "1"})
    through = [p for p in res.get("shell_parts", []) if p not in ms.part_material]
    if through:
        kg = sum(p["mass_kg"] for p in design["parts"] if p["id"] in through)
        effects.append({"name": (f"{len(through)} through parts (the tube inside the rails) carried by the shell's "
                                 f"elements, so not added again as parts; in vehicle_mass via the elements"),
                        "value": kg, "unit": "kg"})
    if float(np.linalg.norm(res["inertia_alpha"])) > 0:
        effects.append({"name": f"angular acceleration from inertia relief {_vec(res['inertia_alpha'], 3)} rad/s^2 "
                                f"(value: its size in rad/s^2)",
                        "value": float(np.linalg.norm(res["inertia_alpha"]))})
    effects += [dict(t, name="beam: " + t["name"]) for t in stress_terms(beam)]
    assumptions = [hot_spot_assumption(), PEAK_ASSUMPTION] if hs_results else [PEAK_ASSUMPTION]
    if ms.mounts:
        assumptions += [MOUNT_ASSUMPTION, EXCLUDE_ASSUMPTION]
    if ms.part_material:
        assumptions.append(GUSSET_ASSUMPTION)
    if ms.through_joints:
        assumptions.append(THROUGH_ASSUMPTION)
    if res.get("shell_parts"):
        assumptions.append(MASS_ASSUMPTION)
    out = {"results": results, "fields": fields, "vectors": vectors, "effects": effects}
    if assumptions:
        out["assumptions"] = assumptions
    return out


def mesh_fields(res: dict, mesh: Record) -> list[Record]:
    """Shell stress (three surfaces) and displacement on the passed mesh."""
    out = [{"kind": "stress", "label": LABELS[s], "unit": "MPa", "mesh_id": mesh["mesh_id"], "surface": s,
            "per_element": [round(float(v), 2) for v in res["von_mises"][s]]} for s in ("max", "top", "bottom")]
    out.append({"kind": "displacement", "label": LABEL_DISP, "unit": "mm", "mesh_id": mesh["mesh_id"],
                "per_mesh_node": [[round(float(x), 3) for x in d] for d in res["displacements"]]})
    return out


def same_mesh(res: dict, mesh: Record) -> str | None:
    """None when the passed mesh is the one solved here, else why not."""
    ms = res["mesh"]
    n, e = len(mesh.get("nodes_mm", [])), len(mesh.get("elements", []))
    if (n, e) != (ms.n_nodes, ms.n_elements):
        return (f"mesh {mesh.get('mesh_id')} has {n} nodes and {e} elements, the shell solved here "
                f"{ms.n_nodes} and {ms.n_elements}; not drawn")
    if list(mesh.get("element_member", [])) != list(ms.element_member):
        return f"mesh {mesh.get('mesh_id')} orders its elements differently from the shell solved here; not drawn"
    return None


class FrameShell(BuiltinTool):
    name = "frame_shell"
    title = "フレームの静的 (シェル: 管の壁の板)"
    domain = "strength"
    base = "K1"
    tests = ["R1"]
    formulas = [
        "frame_static.joint_wall_bending",
        "frame_static.rail_joint_stress",
        "frame_static.rail_step_bending",
        "frame_static.rail_bending",
        "frame_static.bending_stress",
        "mass_props.static_wheel_loads",
    ]
    load_case_params = (LOADING, G_FACTOR, STEP_HEIGHT, STEP_SUPPORTS)
    predicts = (
        Predicts("max_hot_spot_stress", "frame_static.joint_wall_bending", "sigma", WALL_INPUTS),
        Predicts("max_stress", "frame_static.rail_joint_stress", "sigma",
                 JOINT_INPUTS + "; the shell includes the stress concentration at the joints (wall bending where "
                                "tubes meet, the load coming in at the mount), which the formula does not"),
        Predicts("max_stress", "frame_static.rail_step_bending", "sigma",
                 STEP_BENDING + "; the shell includes the stress concentration at the joints, which the formula does not"),
        Predicts("wheel_load_<support>", "mass_props.static_wheel_loads", "wheel_load_<support>",
                 "at rest only (no step_height, no upstream forces)"),
    )
    upstream = ("an analysis with reaction and grip vectors at every support (step_climb, step_climb_dynamic): "
                "its ground forces load the frame instead of step_height")
    needs_mesh = True

    def known_answers(self) -> list[Record]:
        from ..k1_shell.checks import shell_known_answers  # k1_shell.checks imports this package's fixtures

        return shell_known_answers()

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: load_case_params. Run params: upstream_analyses (as
        frame_static: ground forces and the inertia force from an upstream
        analysis), mesh (the version's shell mesh record: the shell stresses
        and displacements are added as fields on it when it is the mesh solved
        here)."""
        design = on_board(design, load_case)
        g = g_factor(load_case)
        up = upstream_ground_forces(params.get("upstream_analyses"), design)
        step = param_value(load_case, "step_height", "mm")
        sup = None
        if up is None and step is not None:
            sup = param_list(load_case, "step_supports") or front_supports(design)
        inertia = up[2] if up else None
        res = analyze_shell(design, g_factor=g, step_mm=None if up else step, step_supports=sup,
                            ground_forces=up[1] if up else None, inertia_N=inertia)
        ground = {sid: [(k, p, f) for k, p, f in gr["vectors"]] for sid, gr in res["ground"].items()}
        beam = beam_analyze(design, g_factor=g, ground_forces=ground, inertia_N=inertia)
        out = shell_outputs(design, res, beam, up[0] if up else None, None if up else step, sup)
        mesh, why = mesh_of(design, params)
        if mesh is not None:
            why = same_mesh(res, mesh)
            if why is None:
                out["fields"] += mesh_fields(res, mesh)
        if why:
            out["effects"].append({"name": why})
        if up is None and step is not None:
            out["scene"] = scene.steps(design, sup, step)
        return out

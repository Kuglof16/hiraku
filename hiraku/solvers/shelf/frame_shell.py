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

Run parameters (analyze's params): element_size, the shell elements' target
edge (the version's mesh record is made at the default, so another size is
solved but not drawn on it); mesh_convergence "3" (default) solves the same
design on three meshes, coarse to fine by a ratio of 2 (2s, s, s/2; 4s, 2s, s
when s/2 would be under 5 mm), and writes analysis.mesh_convergence for the
judged hot spot read at the same weld line on every level (change, Richardson
order and extrapolation, verdict; k1_shell.convergence), with the element peak
and the global error estimate of every level in the effects; "off" skips it.
Every run also carries the Zienkiewicz-Zhu error estimate of the element
stresses (k1_shell.convergence.zz_error): a per-element field (kind
error_estimate, %) on the version's mesh when it is the mesh solved here, and
its largest value, where, and the global value in the effects.

With K2's mount force series upstream (frame_static.upstream_mount_series),
the mesh convergence is not run, and the shell is solved again at each
instant on the same factorisation: the element stresses go into field.frames on the version's mesh (when it is the
mesh solved here) and per member, and the largest hot spot and element
stress at each instant into series. The judged results stay those of the
upstream's vectors.
"""

from __future__ import annotations

import numpy as np

from ..k1_frame import analyze as beam_analyze
from ..k1_frame import stress_terms
from ..k1_frame.surface import wall_von_mises
from ..k1_shell.convergence import (LIMIT_PCT, REFINE_RATIO, convergence_assumption, convergence_record, verdict_of,
                                    zz_assumption, zz_error)
from ..k1_shell.frame import analyze_shell, element_place, membrane_bending
from ..k1_shell.mesh import ELEMENT_SIZE_MM
from ..k1_shell.stress import CORNER_SKIP_T, HOT_SPOT_READ_T, hot_spot_factors
from . import scene
from .base import (G_FACTOR, LOADING, BuiltinTool, LoadCaseError, Param, Predicts, Record, mesh_of, on_board, param_list,
                   param_value, result, run_param)
from .frame_static import (FRAMES_ASSUMPTION, JOINT_INPUTS, STEP_BENDING, STEP_HEIGHT, STEP_SUPPORTS, add_frames,
                           front_supports, g_factor, upstream_ground_forces, upstream_mount_series)

WALL_INPUTS = ("inputs from hiraku.solvers.k0_handcalc.frame_inputs.joint_wall_inputs(design, loading=..., "
               "step_mm=<step_height or None>, step_supports=...): the joint (not at a wheel mount) where the "
               "side wall's plate bending is largest; the shell's hot spot is that wall bending at a cross member's end, "
               "read at the weld line")
# element_size: 5 mm is about half a minute for the demo frame (80,630 elements, 33 s on the development PC);
# past 20 mm the elements are far wider than the weld strips (t to 2t) beside them. HIRAKU's choice.
ELEMENT_SIZE_RANGE_MM = (5.0, 20.0)
ELEMENT_SIZE = Param("element_size", "mm", "target edge of the shell elements (the version's mesh record has the default "
                     f"size; another size is solved but not drawn on it); {ELEMENT_SIZE_RANGE_MM[0]:g} to "
                     f"{ELEMENT_SIZE_RANGE_MM[1]:g} mm", default=ELEMENT_SIZE_MM)
MESH_CONVERGENCE = Param("mesh_convergence", None, "'3': also solve on two more meshes (three levels coarse to fine by "
                         f"a ratio of {REFINE_RATIO:g}: 2s, s, s/2, or 4s, 2s, s when s/2 is under "
                         f"{ELEMENT_SIZE_RANGE_MM[0]:g} mm) and write mesh_convergence for max_hot_spot_stress at the "
                         "same weld line (the 5 mm level of the demo frame takes about 30 s); 'off': not",
                         default="3", choices=("off", "3"))
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
LABEL_ZZ = "誤差の目安 (Zienkiewicz-Zhu: なめらかに直した応力と要素の応力の差、外と内の面)"
LABEL_FRAMES = "シェルの von Mises (外と内の面の大きい方、上流の取付点の力の時刻ごと)"
LABEL_FRAMES_MEMBER = "シェルの von Mises (部材ごとの最大、上流の取付点の力の時刻ごと)"
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
    "ガセットの脚の溶接線は読んでいない。IIW ではこれは疲労のための構造応力で、ここでは静的な強さの判定に、溶接の止端の構造応力 "
    "(膜と曲げ) を使う。局所の降伏を許さない、安全側の扱い")
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


def mesh_fields(res: dict, mesh: Record, zz: dict | None = None) -> list[Record]:
    """Shell stress (three surfaces), the error estimate (zz, when given) and displacement on the passed mesh."""
    out = [{"kind": "stress", "label": LABELS[s], "unit": "MPa", "mesh_id": mesh["mesh_id"], "surface": s,
            "per_element": [round(float(v), 2) for v in res["von_mises"][s]]} for s in ("max", "top", "bottom")]
    if zz is not None:
        out.append({"kind": "error_estimate", "label": LABEL_ZZ, "unit": "%", "mesh_id": mesh["mesh_id"],
                    "per_element": [round(float(v), 1) for v in zz["per_element"]]})
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


def shell_frames(design: Record, res: dict, series, mesh: Record | None) -> Record:
    """frame_shell's outputs over the instants of upstream_mount_series, solved on res's factorised frame:
    field.frames per element (on `mesh`, when given: the mesh solved here) and per member, series of the
    largest hot spot and element stress, their largest instants as results, and an effect."""
    lc_id, ana_id, instants = series
    members = {m["id"] for m in design["members"]}
    per_element, per_member, peak, hot, hot_at = [], [], [], [], []
    for inst in instants:
        r = analyze_shell(design, frame=res["frame"], mount_loads=inst["mount_loads"],
                          gravity_mps2=inst["gravity_mps2"], inertia_N=inst["inertia_N"])
        if mesh is not None:  # whole MPa: tens of instants of every element (about 6 MB for the demo frame)
            per_element.append([int(round(float(v))) for v in r["von_mises"]["max"]])
        per_member.append({m: round(v, 2) for m, (v, _) in r["member_max"].items() if m in members})
        peak.append(r["member_max"][r["worst_member"]][0])
        top = r.get("hot_spot_max")
        hot.append(abs(top["value"]) if top else 0.0)
        hot_at.append(f"joint {top['joint']} ({top['member']})" if top else "")
    t = [inst["t_s"] for inst in instants]
    src = {"source_analysis_id": ana_id} if ana_id else {}
    fields = [{"kind": "stress", "label": LABEL_FRAMES_MEMBER, "unit": "MPa",
               "frames": {"t_s": t, "per_member": per_member, "max": [round(v, 2) for v in peak], **src}}]
    if mesh is not None:
        fields.insert(0, {"kind": "stress", "label": LABEL_FRAMES, "unit": "MPa", "mesh_id": mesh["mesh_id"],
                          "surface": "max", "frames": {"t_s": t, "per_element": per_element,
                                                       "max": [round(max(v), 2) for v in per_element], **src}})
    k, j = int(np.argmax(hot)), int(np.argmax(peak))
    return {
        "fields": fields,
        "series": [{"channel": "max_hot_spot_stress", "unit": "MPa", "t_s": t, "values": [round(v, 3) for v in hot]},
                   {"channel": "max_stress", "unit": "MPa", "t_s": t, "values": [round(v, 3) for v in peak]}],
        "results": [result("max_hot_spot_stress_over_time", hot[k], "MPa", f"{hot_at[k]} at t {t[k]:.3f} s"),
                    result("max_stress_over_time", peak[j], "MPa", f"t {t[j]:.3f} s")],
        "effects": [{"name": (f"shell solved at {len(t)} instants of {lc_id}'s mount forces (t {t[0]:.3f} to "
                              f"{t[-1]:.3f} s, one factorisation): largest hot spot {hot[k]:.1f} MPa at "
                              f"{hot_at[k]}, t {t[k]:.3f} s; largest element stress {peak[j]:.1f} MPa at t "
                              f"{t[j]:.3f} s"), "value": hot[k], "unit": "MPa"}],
        "assumptions": [FRAMES_ASSUMPTION.format(lc=lc_id)],
    }


def convergence_sizes(size: float) -> list[float]:
    """Element sizes of the three levels, coarse to fine: 2s, s, s/2, or 4s, 2s, s when s/2 is under the range."""
    r = REFINE_RATIO
    if size / r < ELEMENT_SIZE_RANGE_MM[0] - 1e-9:
        return [size * r * r, size * r, size]
    return [size * r, size, size / r]


def hot_spot_key(h: dict) -> tuple:
    """The weld line and surface a hot spot is read on: joint, side, place, member, towards, surface."""
    return (h["joint"], h["side"], h["place"], h["member"], h["towards"], h["surface"])


def hot_spot_on(res: dict, key: tuple) -> dict | None:
    """The hot spot of largest size read on the weld line and surface `key` (None when that line is not read)."""
    hs = [h for h in res.get("hot_spots") or [] if hot_spot_key(h) == key]
    return max(hs, key=lambda h: abs(h["value"])) if hs else None


def _where_hot(key: tuple) -> str:
    joint, side, place, member, towards, surface = key
    return (f"joint {joint}, {SIDE[side]} ({member}, {place}, strip towards {towards}), "
            f"{'outer' if surface == 'top' else 'inner'} surface")


def _peak_where(res: dict) -> str:
    e = res["worst_element"]
    p = element_place(res, e)
    return f"{p['member']}, {p['wall']} wall, {p['node_distance_mm']:.0f} mm from node {p['node']}"


def zz_effects(res: dict, zz: dict) -> list[Record]:
    """Effects of the error estimate: its largest value outside the wheel mounts and where, and the global value."""
    eta = zz["per_element"]
    kept = ~res["excluded"]
    idx = np.nonzero(kept)[0] if kept.any() else np.arange(len(eta))
    e = int(idx[np.argmax(eta[idx])])
    p = element_place(res, e)
    floored = int(np.sum(zz["floored"]))
    out = [
        {"name": (f"error estimate (Zienkiewicz-Zhu) largest {eta[e]:.1f} % outside the wheel mounts: element {e} of "
                  f"{p['member']}, {p['wall']} wall, {p['node_distance_mm']:.0f} mm from node {p['node']} "
                  f"({res['mesh'].joint_kinds.get(p['node'], '?')} joint); where the mesh should be finer"),
         "value": float(eta[e]), "unit": "%"},
        {"name": (f"error estimate (Zienkiewicz-Zhu) of the whole model, mesh {res['mesh'].element_size_mm:g} mm "
                  f"(energy of the difference over that of the recovered stress, all elements); {floored} elements "
                  f"with little stress measured against the floor of the denominator"),
         "value": float(zz["global_pct"]), "unit": "%"},
    ]
    return out


def mesh_convergence(design: Record, res: dict, solve: dict, mesh: Record | None,
                     zz_res: dict) -> tuple[Record, list[Record]]:
    """analysis.mesh_convergence and the effects, from res (solved at its element size) and the two other levels
    of convergence_sizes solved here. The judged hot spot is read on the same weld line and surface on every
    level: the one of largest size on the level that is the version's mesh (else res's level)."""
    size = res["mesh"].element_size_mm
    levels_res = {}
    times = {}
    for s in convergence_sizes(size):
        if abs(s - size) < 1e-9:
            levels_res[s] = res
            continue
        r = analyze_shell(design, element_size_mm=s, **solve)
        times[s] = r["timings"].get("total_s", 0.0)
        levels_res[s] = r
    sizes = list(levels_res)
    recorded = {s: mesh is not None and same_mesh(r, mesh) is None for s, r in levels_res.items()}
    base = next((r for s, r in levels_res.items() if recorded[s]), res)
    # per level: the hot spot on the base's weld line, the element peak, the global error estimate
    rows = []
    for s in sizes:
        r = levels_res[s]
        zz = zz_error(r["frame"].model, r["stress"]) if r is not res else None
        rows.append({"size": s, "res": r, "zz": zz})
    top = base.get("hot_spot_max")
    notes = []
    if top is not None:
        key = hot_spot_key(top)
        quantity, unit = "max_hot_spot_stress", "MPa"
        values = []
        for row in rows:
            h = hot_spot_on(row["res"], key)
            if h is None:
                break
            values.append(abs(h["value"]))
            other = row["res"].get("hot_spot_max")
            if other is not None and hot_spot_key(other) != key:
                notes.append(f"at {row['size']:g} mm the largest hot spot is elsewhere: {_where_hot(hot_spot_key(other))}, "
                             f"{abs(other['value']):.1f} MPa")
        if len(values) < len(rows):
            top = None  # the weld line is not read on every level: fall back to the element peak
    if top is None:
        quantity, unit = "max_stress", "MPa"
        values = [r["res"]["member_max"][r["res"]["worst_member"]][0] for r in rows]
    levels = []
    for row, v in zip(rows, values):
        lv = {"element_size_mm": float(row["size"]), "elements": int(row["res"]["mesh"].n_elements), "value": float(v)}
        if recorded[row["size"]]:
            lv["mesh_id"] = mesh["mesh_id"]
        levels.append(lv)
    where = _where_hot(key) if quantity == "max_hot_spot_stress" else "the largest element stress"
    notes.insert(0, f"read at {where} on every level")
    if quantity == "max_hot_spot_stress":
        notes.append("the strip elements it is read on are t x t on every level (IIW); the levels refine the mesh "
                     "around them")
    rec = convergence_record(quantity, unit, levels, LIMIT_PCT, REFINE_RATIO, "; ".join(notes))

    # effects: the judged value, the element peak, the error estimate and the time
    sz = " / ".join(f"{s:g}" for s in sizes)
    vs = " / ".join(f"{v:.1f}" for v in values)
    rich = f", observed order {rec['observed_order']:.2f}" if "observed_order" in rec else ""
    if "extrapolated" in rec:
        rich += f", extrapolated {rec['extrapolated']:.1f} {unit}"
        if "error_pct" in rec:
            rich += f" (finest {rec['error_pct']:.1f} % from it)"
    effects = [{"name": (f"mesh convergence of {quantity} at {where}: {sz} mm -> {vs} {unit}; change between the two "
                         f"finest {rec['change_pct']:.1f} % (limit {LIMIT_PCT:g} %){rich}; {rec['verdict']}"),
                "value": float(rec["change_pct"]), "unit": "%"}]
    if rec["verdict"] == "singular":
        effects.append({"name": (f"{quantity} keeps rising as the elements shrink (singular): it cannot be judged as it "
                                 f"stands")})
    peaks = [r["res"]["member_max"][r["res"]["worst_member"]][0] for r in rows]
    pv, pchange, prich = verdict_of(peaks, LIMIT_PCT, REFINE_RATIO)
    places = [element_place(r["res"], r["res"]["worst_element"]) for r in rows]
    moved = len({(p["member"], p["wall"], p["node"]) for p in places}) > 1
    p_rich = f", observed order {prich['observed_order']:.2f}" if prich and prich["observed_order"] is not None else ""
    if prich and prich["extrapolated"] is not None and pv != "singular":
        p_rich += f", extrapolated {prich['extrapolated']:.1f} MPa"
    edges = [float(r["res"]["mesh"].edge_lengths()[r["res"]["worst_element"]].max()) for r in rows]
    if max(edges) <= 1.01 * min(edges):
        p_rich += (f"; the peak element is {edges[-1]:.2g} mm on every level (sized by the weld strips' grid, not by "
                   f"element_size), so the element at the peak itself is not refined")
    effects.append({"name": (f"mesh convergence of max_stress (the element peak, a reference): {sz} mm -> "
                             f"{' / '.join(f'{v:.1f}' for v in peaks)} MPa at {'; '.join(_peak_where(r['res']) for r in rows)}"
                             f"{' (the place moves)' if moved else ''}; change between the two finest {pchange:.1f} %"
                             f"{p_rich}; {pv}"),
                    "value": float(pchange), "unit": "%"})
    globs = [(row["zz"] or zz_res)["global_pct"] for row in rows]
    effects.append({"name": (f"error estimate (Zienkiewicz-Zhu) of the whole model per level: {sz} mm -> "
                             f"{' / '.join(f'{g:.1f}' for g in globs)} %"), "value": float(globs[-1]), "unit": "%"})
    extra = sum(times.values())
    effects.append({"name": ("mesh convergence solves: " + ", ".join(
        f"{s:g} mm ({levels_res[s]['mesh'].n_elements} elements) {t:.1f} s" for s, t in times.items())),
        "value": extra, "unit": "s"})
    return rec, effects


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
    run_params = (ELEMENT_SIZE, MESH_CONVERGENCE)
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
        analysis), mesh (the version's shell mesh record: the shell stresses,
        the error estimate and the displacements are added as fields on it when
        it is the mesh solved here), and run_params."""
        design = on_board(design, load_case)
        g = g_factor(load_case)
        up = upstream_ground_forces(params.get("upstream_analyses"), design)
        step = param_value(load_case, "step_height", "mm")
        sup = None
        if up is None and step is not None:
            sup = param_list(load_case, "step_supports") or front_supports(design)
        inertia = up[2] if up else None
        size, conv = run_param(params, ELEMENT_SIZE), run_param(params, MESH_CONVERGENCE)
        lo, hi = ELEMENT_SIZE_RANGE_MM
        if not lo <= size <= hi:
            raise LoadCaseError(f"run parameter element_size must be {lo:g} to {hi:g} mm, got {size:g} mm")
        solve = dict(g_factor=g, step_mm=None if up else step, step_supports=sup, ground_forces=up[1] if up else None,
                     inertia_N=inertia)
        res = analyze_shell(design, element_size_mm=size, **solve)
        ground = {sid: [(k, p, f) for k, p, f in gr["vectors"]] for sid, gr in res["ground"].items()}
        beam = beam_analyze(design, g_factor=g, ground_forces=ground, inertia_N=inertia)
        out = shell_outputs(design, res, beam, up[0] if up else None, None if up else step, sup)
        zz = zz_error(res["frame"].model, res["stress"])
        out["effects"] += zz_effects(res, zz)
        out.setdefault("assumptions", []).append(zz_assumption())
        mesh, why = mesh_of(design, params)
        if mesh is not None:
            why = same_mesh(res, mesh)
            if why is None:
                out["fields"] += mesh_fields(res, mesh, zz)
        if why:
            out["effects"].append({"name": why})
        series = upstream_mount_series(params.get("upstream_analyses"), design)
        if series:
            add_frames(out, shell_frames(design, res, series, mesh if mesh is not None and why is None else None))
        elif conv == "3":
            out["mesh_convergence"], eff = mesh_convergence(design, res, solve, mesh, zz)
            out["effects"] += eff
            out["assumptions"].append(convergence_assumption())
        if up is None and step is not None:
            out["scene"] = scene.steps(design, sup, step)
        return out

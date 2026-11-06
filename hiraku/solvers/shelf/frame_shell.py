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

Per member, max_stress.<member id> (the largest element stress of the member
or gusset plate outside the wheel mounts, as max_stress and the per-member
stress field) and max_hot_spot_stress.<member id> (as the per-member hot-spot
field) are reported beside the overall values; they are not judged. The
element stress fields on the mesh include the mount elements, so their largest
can be above max_stress; the effects give the largest outside and inside the
mounts.

Run parameters (analyze's params): element_size, the shell elements' target
edge (the version's mesh record is made at the default, so another size is
solved but not drawn on it); mesh_convergence "3" (asked for once, on the
version that looks final; the default is "off") solves the same
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

import multiprocessing
import os
import sys
import time
from dataclasses import dataclass

import numpy as np

from ..k0_handcalc.vehicle import DesignError
from ..k1_frame import analyze as beam_analyze
from ..k1_frame import stress_terms
from ..k1_frame.surface import wall_von_mises
from ..k1_shell.convergence import (LIMIT_PCT, REFINE_RATIO, convergence_assumption, convergence_record, verdict_of,
                                    zz_assumption, zz_error)
from ..k1_shell.frame import ShellFrame, analyze_shell, element_place, membrane_bending
from ..k1_shell.mesh import ELEMENT_SIZE_MM, build_mesh
from ..k1_shell.solve import solver_assumption
from ..k1_shell.stress import CORNER_SKIP_T, HOT_SPOT_READ_T, hot_spot_factors
from . import hot_spots, scene
from .base import (G_FACTOR, LOADING, BuiltinTool, LoadCaseError, Param, Predicts, Record, mesh_of, on_board, param_list,
                   param_value, result, run_param)
from .frame_static import (R9_SUPPORT, R9_SUPPORTS, r9_supports)
from .frame_static import (FRAMES_ASSUMPTION, JOINT_INPUTS, STEP_BENDING, STEP_HEIGHT, STEP_SUPPORTS, add_frames,
                           PATH_FORMULA, PATH_PREDICTS, front_supports, g_factor, lost_load_outputs, member_effect,
                           member_results,
                           upstream_ground_forces,
                           upstream_mount_series)

WALL_INPUTS = ("inputs from hiraku.solvers.k0_handcalc.frame_inputs.joint_wall_inputs(design, loading=..., "
               "step_mm=<step_height or None>, step_supports=...): the joint (the wheel mounts' joints included) where the "
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
                         "same weld line (the 5 mm level of the demo frame takes about 30 s); 'off' (default): one mesh. "
                         "Ask for '3' once, on the judged load cases of the version that looks final (HK5)",
                         default="off", choices=("off", "3"))
AWAY_FACTOR = 2.0  # "away from the joints": element centre at least this many tube sizes from every design node
MOUNT_NOTE = "車輪の取付の当て板の要素も含む (max_stress はそれを外した最大)"
LABELS = {
    "max": f"シェルの von Mises (外と内の面の大きい方、{MOUNT_NOTE})",
    "top": f"シェルの von Mises (外の面、{MOUNT_NOTE})",
    "bottom": f"シェルの von Mises (内の面、{MOUNT_NOTE})",
}
LABEL_MEMBER = "シェルの von Mises (部材ごとの最大)"
LABEL_HOT_SPOT = "構造的ホットスポット応力 (溶接線に直角、部材ごとの絶対値の最大)"
LABEL_BEAM = "梁の von Mises (部材ごとの最大、シェルと同じ地面の力)"
LABEL_DISP = "シェルの変位 (輪のつなぎ目の剛体の動きを除く)"
LABEL_ZZ = "誤差の目安 (Zienkiewicz-Zhu: なめらかに直した応力と要素の応力の差、外と内の面)"
LABEL_FRAMES = f"シェルの von Mises (外と内の面の大きい方、上流の取付点の力の時刻ごと、{MOUNT_NOTE})"
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
JUDGED_ASSUMPTION = ("判定に使う量: 荷重条件の基準の量が max_stress のとき、この道具で判定に使うのは max_hot_spot_stress "
                     "(溶接の止端の構造応力)。要素の最大 max_stress は記録に出すが、判定には使わない (契約)")
PEAK_ASSUMPTION = ("max_stress はつなぎ目の鋭い入隅の角の要素の値で、シェルではそこで応力が特異になる (角の要素を細かくするほど"
                   "大きくなる)。角の要素は溶接線の帯の t×t の要素で、要素の大きさ (element_size) を変えても変わらないので、"
                   "mesh_convergence で値が動かなくても、角を解いた値ではない。参考として残し、判定には使わない")
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
    per_member: dict[str, float] = {}
    per_joint: dict[str, dict] = {}
    for h in hs:
        per_member[h["member"]] = max(per_member.get(h["member"], 0.0), abs(h["value"]))
        if h["joint"] not in per_joint or abs(h["value"]) > abs(per_joint[h["joint"]]["value"]):
            per_joint[h["joint"]] = h
    # per member: reported, not judged (the criterion is judged on max_hot_spot_stress)
    results = ([result("max_hot_spot_stress", abs(v), "MPa", top["member"])]
               + member_results("max_hot_spot_stress", per_member))
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
    effects.append(member_effect("max_hot_spot_stress", per_member))
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
    # per member (and gusset plate): the largest element stress outside the wheel mounts, as max_stress
    member_peak = {m: v for m, (v, _) in res["member_max"].items()}
    results += member_results("max_stress", member_peak)
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
        # the stress fields (surface max / top / bottom) include the mount elements; max_stress does not
        effects.append({"name": (f"largest element stress outside the wheel mounts (max_stress): element {e} of "
                                 f"{worst}"), "value": peak, "unit": "MPa"})
        if res["excluded_max"] is not None:
            v, ee = res["excluded_max"]
            effects.append({"name": (f"largest element stress inside the wheel mounts (left out of max_stress; in the "
                                     f"stress fields, whose largest it is when above max_stress): element {ee} of "
                                     f"{ms.element_member[ee]} (the mount brackets' plates and welds are judged by "
                                     f"R9's plate and weld load cases, the pins by R9's bolt load cases)"),
                        "value": v, "unit": "MPa"})
    effects.append(member_effect("max_stress", member_peak))
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
         "value": float(ms.n_elements), "unit": "1"},  # its clock times go to timings_s (shell_timings)
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
    assumptions = ([JUDGED_ASSUMPTION, hot_spot_assumption(), PEAK_ASSUMPTION] if hs_results
                   else [PEAK_ASSUMPTION])
    if ms.mounts:
        assumptions += [MOUNT_ASSUMPTION, EXCLUDE_ASSUMPTION]
    if ms.part_material:
        assumptions.append(GUSSET_ASSUMPTION)
    if ms.through_joints:
        assumptions.append(THROUGH_ASSUMPTION)
    if res.get("shell_parts"):
        assumptions.append(MASS_ASSUMPTION)
    assumptions.append(solver_assumption(res.get("linear_solver", "superlu")))
    lost, said = lost_load_outputs()
    results.append(lost)
    effects.append(said)
    out = {"results": results, "fields": fields, "vectors": vectors, "effects": effects}
    if assumptions:
        out["assumptions"] = assumptions
    return out


UNTIED_ASSUMPTION = ("判定していない継ぎ手: {ids} (節を共有せず、RBE2 の剛な棒でつないだ継ぎ手 (斜め、丸い管、3 本以上の端、"
                     "格子の線がぶつかる所)。溶接の帯が無く、ホットスポット応力を読んでいない)")


def untied_joints(res: dict) -> list[str]:
    """Design nodes of the joints tied by rigid links without weld strips (mesh joint kind "tied"): no hot
    spot is read there."""
    return sorted(n for n, k in res["mesh"].joint_kinds.items() if k == "tied")


def shell_hot_spots(tool, load_case: Record, res: dict) -> list[Record] | None:
    """analysis.hot_spots of the shell (hot_spots module). The judged quantity is max_hot_spot_stress
    (judged_as): one row per weld line group, a member at a joint (the largest size of the hot spots read
    there, outer and inner surface; where = "<member>@<joint>", unique per weld line group), against the
    criterion's limit. The element peak (max_stress, at a sharp re-entrant corner, singular) is one more row
    of quantity max_stress with SHARP_NOTE: not the judged quantity, so it never makes a red. The wheel mounts'
    elements are not read (hot spots and member_max leave them out). None when the criterion is not a "<="
    stress, or is judged on a quantity the shell has no per-place values of."""
    judged = hot_spots.judged_limit(tool, load_case)
    if judged is None or judged[0] != "max_hot_spot_stress":
        return None
    q, limit = judged
    groups: dict[tuple[str, str], dict] = {}
    for h in res.get("hot_spots") or []:
        k = (h["member"], h["joint"])
        if k not in groups or abs(h["value"]) > abs(groups[k]["value"]):
            groups[k] = h
    rows = [hot_spots.row(f"{h['member']}@{h['joint']}", q, abs(h["value"]), limit,
                          note=(f"joint {h['joint']} ({h['kind']}), {SIDE[h['side']]}, {h['place']}, strip towards "
                                f"{h['towards']}, {'outer' if h['surface'] == 'top' else 'inner'} surface"),
                          at_mm=h["position_mm"])
            for h in groups.values()]
    worst = res["worst_member"]
    peak, e = res["member_max"][worst]
    sharp = hot_spots.row(worst, "max_stress", peak, limit, note=hot_spots.SHARP_NOTE,
                          at_mm=element_place(res, e)["centre_mm"])
    return hot_spots.top(rows, keep=[sharp])


# ---------------------------------------------------------------- R9 サスペンション取付: the brackets in the shell
R9_QUANTITIES = ("plate_bending_utilisation", "weld_utilisation")  # judged by the shell; bolt_* stay the hand calc's
R9_ASSUMPTIONS = [
    "R9: 金具 (kind bracket で joint のある部品) のうち、管の壁の上に立つものは、シェルの板 2 枚 (ピンに直角の耳、板厚 "
    "plate_t_mm) にした。根元の節は、壁の上にある範囲で壁の節と共有 (溶接)。ピンの穴 (d + 2 mm) の縁に、K2 の取付点の力を "
    "split_force で分け、耳ごとに半分、面内の分は力に向いた半周に cos の重みで、ピンに沿う分は穴の全周に等しく載せた "
    "(ピンそのものは入れていない)。k1_shell.brackets",
    "R9: 溶接 weld_utilisation と板 plate_bending_utilisation は、耳ごとに、根元の節に耳の要素がかける力の合力 R と、溶接した"
    "範囲の中央まわりのモーメント M から、溶接した長さ L で、library_bracket の式 (mount_bracket.weld、.plate_bending と同じ"
    "形、線の力と 3 つの応力を足す: 安全側) で出した公称の値: w = |R|/(2L) + |M.base|/(L t_p) + 3|M.u|/L^2 を 0.7 s で割り "
    "f_vw (library_bracket.resistances、EN 1993-1-8 4.5.3.3 の簡易法、確かめていない) で割る。sigma = 6|M.base|/(L t_p^2) + "
    "6|M.u|/(t_p L^2) + |R.up|/(L t_p) を f_y (STKR400 245 MPa、materials.json、確かめていない) で割る。根元の端の線の力と根元の"
    "要素の von Mises は、硬い耳が薄い壁に着く端で特異 (細かくすると上がる) なので、出すが判定しない",
    "R9: 溶接した長さ L は、金具の根元のうち管の壁の上にある長さ (壁の中面の幅の中で、箱の幅と重なる所。"
    "bracket_inputs.weld_span: 手計算の b も同じ関数の値)。壁からはみ出た所は板の縁で、溶接していない (金具ごとの L と"
    "箱の幅は effects)。段 3 より前の手計算は箱の幅の全部を溶接とみていた",
    "R9: 穴の縁の 1 列の要素は、ピンの力を少ない節に載せた尖り (細かくすると上がる): max_stress として出し、判定しない",
    "R9: bolt_shear_utilisation と bolt_bearing_utilisation はシェルに無い (ピンを入れていない): 手計算 "
    "(mount_bracket.pin_shear / pin_bearing) の値のまま",
    "R9: 力は上流の解析ごとに、その取付点の |F| が最大の瞬間の、全部の取付点の力でシェルを解き、その中で利用率が最大のもの "
    "(手計算と同じ選び方)",
    "R9: 車輪の取付の区画 (support の frame_node の管の壁、節から管の高さの範囲) は、今までどおり最大の応力から外す: "
    "地面の力、支えのモーメント、ばね下の重さはそこから入れるため。金具の板とその根元はそこから外していない",
]


def _r9_skipped(design: Record, ms, sid: str) -> dict[str, str]:
    from ..k0_handcalc.bracket_inputs import mount_points

    ids = {g["bracket"] for g in mount_points(design, sid).values() if g.get("bracket")}
    return {k: v for k, v in ms.bracket_skipped.items() if k in ids}


def r9_values(design: Record, sid: str, upstream: dict | None, size: float) -> dict:
    """The shell's R9 values of support sid's meshed brackets: for each upstream analysis with K2's mount series and
    each bracket's mount, the shell solved at the instant of that mount's largest |F| (all the mounts' forces of
    that instant), the worst over the analyses. {"worst": {(quantity, mount): {...}}, "used", "solves", "frame"}."""
    from ..k1_shell.frame import shared_frame
    from .frame_static import upstream_mount_series

    frame, _ = shared_frame(design, size)
    mine = [b for b in frame.mesh.brackets if b.support == sid]
    worst: dict[tuple[str, str], dict] = {}
    used, solves = [], 0
    rejected: dict[str, str] = {}  # upstream analyses whose mount forces the shell cannot balance, and why
    for lc_id, ana in (upstream or {}).items():
        got = upstream_mount_series({lc_id: ana}, design)
        if got is None:
            continue
        _, ana_id, instants = got
        peak: dict[str, tuple[int, float]] = {}
        for k, inst in enumerate(instants):
            for m, _, f in inst["mount_loads"][sid]["vectors"]:
                mag = float(np.linalg.norm(f))
                if m not in peak or mag > peak[m][1]:
                    peak[m] = (k, mag)
        solved: dict[int, dict] = {}
        mine_worst: dict[tuple[str, str], dict] = {}
        try:
            for b in mine:
                if b.mount not in peak:
                    continue
                k = peak[b.mount][0]
                if k not in solved:
                    inst = instants[k]
                    solved[k] = analyze_shell(design, frame=frame, mount_loads=inst["mount_loads"],
                                              gravity_mps2=inst["gravity_mps2"], inertia_N=inst["inertia_N"])["brackets"]
                v = solved[k][b.id]
                for q in R9_QUANTITIES:
                    mine_worst[(q, b.mount)] = {"value": float(v[q]), "bracket": b.id, "load_case_id": lc_id,
                                                "analysis_id": ana_id, "t_s": float(instants[k]["t_s"]), "vals": v}
        except DesignError as e:  # its forces do not balance the sprung weight here (mount_balance): left out, said
            rejected[lc_id] = str(e)
            solves += len(solved)
            continue
        used.append(lc_id)
        for key, w in mine_worst.items():
            if key not in worst or w["value"] > worst[key]["value"]:
                worst[key] = w
        solves += len(solved)
    return {"worst": worst, "used": used, "solves": solves, "frame": frame, "rejected": rejected}


def r9_hot_spots(load_case: Record, got: dict) -> list[Record] | None:
    """analysis.hot_spots of R9 (criterion on plate_bending_utilisation or weld_utilisation, limit 1): one row per
    mount bracket (where = the bracket part for the plate, '<bracket>@<member>' for its weld), and the hole edge's
    largest element stress as a max_stress row with SHARP_NOTE (not judged). None without such a criterion."""
    crit = (load_case or {}).get("criterion") or {}
    q = crit.get("quantity")
    if q not in R9_QUANTITIES or crit.get("op") not in ("<=", "<"):
        return None
    limit = float(crit["limit"]["value"])
    rows, sharp = [], None
    for (qq, mount), w in got["worst"].items():
        v = w["vals"]
        if qq == q == "plate_bending_utilisation":
            rows.append(hot_spots.row(w["bracket"], q, w["value"], limit, unit="1", at_mm=v["plate_at_mm"],
                                      note=(f"{mount}: plate root {v['plate_sigma']:.1f} MPa (nominal, from the "
                                            f"shell's base resultant over {v['weld_length_mm']:.1f} mm) / f_y "
                                            f"{v['f_plate']:g} MPa ({w['load_case_id']} t {w['t_s']:.3f} s)")))
        elif qq == q == "weld_utilisation":
            rows.append(hot_spots.row(f"{w['bracket']}@{v['member']}", q, w["value"], limit, unit="1",
                                      at_mm=v["weld_at_mm"],
                                      note=(f"{mount}: base weld line {v['weld_line_N_per_mm']:.1f} N/mm, tau_w "
                                            f"{v['tau_w']:.1f} MPa / f_vw {v['f_vw']:.1f} MPa ({w['load_case_id']} t "
                                            f"{w['t_s']:.3f} s)")))
        if qq == q and (sharp is None or v["hole_vm"] > sharp[0]):
            sharp = (v["hole_vm"], w["bracket"], v)
    if sharp is None:
        return rows or None
    vm, bid, v = sharp
    keep = hot_spots.row(bid, "max_stress", vm, v["f_plate"], note=hot_spots.SHARP_NOTE, at_mm=v["hole_at_mm"])
    return hot_spots.top(rows, keep=[keep])


def r9_outputs(design: Record, load_case: Record, upstream: dict | None, size: float) -> Record:
    """frame_shell's record for an R9 load case (r9_values of each of its corners, r9_hot_spots). A place is
    '<support>.<mount>': the results '<quantity>.<support>.<mount>' for each, and '<quantity>' the worst of them
    (its where the place)."""
    t0 = time.perf_counter()
    sids = r9_supports(load_case)
    got = {"worst": {}, "used": [], "solves": 0, "frame": None}
    for sid in sids:
        one = r9_values(design, sid, upstream, size)
        got["worst"].update({(q, f"{sid}.{m}"): w for (q, m), w in one["worst"].items()})
        got["used"] = list(dict.fromkeys(got["used"] + one["used"]))
        got["solves"] += one["solves"]
        got["frame"] = one["frame"]
        got.setdefault("rejected", {}).update(one["rejected"])
    if not got["used"]:
        raise LoadCaseError(f"{load_case.get('load_case_id', 'R9')}: no upstream analysis with K2's mount series "
                            f"(mount_x/y/z.<support>.<mount> of {', '.join(sids)}) was given; the mount forces are not "
                            f"invented")
    ms = got["frame"].mesh
    results, effects = [], []
    for q in R9_QUANTITIES:
        per = {m: w for (qq, m), w in got["worst"].items() if qq == q}
        for m, w in sorted(per.items()):
            results.append(result(f"{q}.{m}", w["value"], "1", m))
        if per:
            m, w = max(per.items(), key=lambda kv: kv[1]["value"])
            results.insert(0, result(q, w["value"], "1", m))
    for (q, m), w in sorted(got["worst"].items()):
        v = w["vals"]
        what = (f"base weld line {v['weld_line_N_per_mm']:.2f} N/mm over {v['weld_length_mm']:.1f} mm welded, tau_w "
                f"{v['tau_w']:.2f} MPa (local peak at the base's end {v['weld_line_peak_N_per_mm']:.1f} N/mm, "
                f"singular, not judged)" if q == "weld_utilisation"
                else f"plate root {v['plate_sigma']:.2f} MPa nominal (base row's peak von Mises "
                     f"{v['plate_vm_peak']:.1f} MPa, hole edge {v['hole_vm']:.1f} MPa: singular, not judged)")
        effects.append({"name": f"{q} at {m} ({w['bracket']}): {what}; {w['load_case_id']} t {w['t_s']:.3f} s",
                        "value": w["value"], "unit": "1"})
    lengths = {w["bracket"]: (w["vals"]["weld_length_mm"], w["vals"]["box_width_mm"]) for w in got["worst"].values()}
    for bid, (L, b) in sorted(lengths.items()):
        effects.append({"name": (f"{bid}: welded length {L:.1f} mm (the part of its base on the tube wall, the hand "
                                 f"calculation's b too) of the box's width {b:.1f} mm (note for the design department: "
                                 f"{'the base overhangs the wall' if L < b - 1e-6 else 'the whole base is on the wall'})"),
                        "value": L, "unit": "mm"})
    assumptions = list(R9_ASSUMPTIONS)
    skipped = {}
    for sid in sids:
        skipped.update(_r9_skipped(design, ms, sid))
    for lc_id, why in sorted(got.get("rejected", {}).items()):
        assumptions.append(f"R9: 上流の {lc_id} の取付点の力はシェルで釣り合わないので使わなかった (手計算は使う): {why}")
    if skipped:
        assumptions.append("R9: シェルの板にしていない金具 (手計算だけ。力はこれまでどおり carried_by の節に): "
                           + "; ".join(f"{k} ({v})" for k, v in sorted(skipped.items())))
    effects.append({"name": (f"shell model: {ms.n_nodes} nodes, {ms.n_elements} elements (edge {ms.element_size_mm:g} mm), "
                             f"{len(ms.brackets)} mount brackets meshed as two plates each; solved at {got['solves']} "
                             f"instants of {', '.join(got['used'])}"), "value": float(ms.n_elements), "unit": "1"})
    lost, said = lost_load_outputs()  # the path check reads unsupported_load on the analysis that decides
    results.append(lost)
    effects.append(said)
    out = {"results": results, "fields": [], "effects": effects, "assumptions": assumptions,
           "timings_s": {"total": time.perf_counter() - t0}}
    hot = r9_hot_spots(load_case, got)
    if hot is not None:
        out["hot_spots"] = hot
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
    per_element, per_member, peak, hot, hot_at, hot_t = [], [], [], [], [], []
    for inst in instants:
        r = analyze_shell(design, frame=res["frame"], mount_loads=inst["mount_loads"],
                          gravity_mps2=inst["gravity_mps2"], inertia_N=inst["inertia_N"])
        if mesh is not None:  # whole MPa: tens of instants of every element (about 6 MB for the demo frame)
            per_element.append([int(round(float(v))) for v in r["von_mises"]["max"]])
        per_member.append({m: round(v, 2) for m, (v, _) in r["member_max"].items() if m in members})
        peak.append(r["member_max"][r["worst_member"]][0])
        top = r.get("hot_spot_max")
        if top:  # an instant with no hot spot read is left out of the hot-spot series (not 0 MPa)
            hot.append(abs(top["value"]))
            hot_at.append(f"joint {top['joint']} ({top['member']})")
            hot_t.append(inst["t_s"])
    t = [inst["t_s"] for inst in instants]
    src = {"source_analysis_id": ana_id} if ana_id else {}
    fields = [{"kind": "stress", "label": LABEL_FRAMES_MEMBER, "unit": "MPa",
               "frames": {"t_s": t, "per_member": per_member, "max": [round(v, 2) for v in peak], **src}}]
    if mesh is not None:
        fields.insert(0, {"kind": "stress", "label": LABEL_FRAMES, "unit": "MPa", "mesh_id": mesh["mesh_id"],
                          "surface": "max", "frames": {"t_s": t, "per_element": per_element,
                                                       "max": [round(max(v), 2) for v in per_element], **src}})
    j = int(np.argmax(peak))
    series_out, results = [], []
    if hot:
        k = int(np.argmax(hot))
        series_out.append({"channel": "max_hot_spot_stress", "unit": "MPa", "t_s": hot_t,
                           "values": [round(v, 3) for v in hot]})
        results.append(result("max_hot_spot_stress_over_time", hot[k], "MPa", f"{hot_at[k]} at t {hot_t[k]:.3f} s"))
        said = f"largest hot spot {hot[k]:.1f} MPa at {hot_at[k]}, t {hot_t[k]:.3f} s"
        if len(hot) < len(t):
            said += f" (hot spots read at {len(hot)} of the {len(t)} instants)"
    else:
        said = "no hot spot read at any instant"
    series_out.append({"channel": "max_stress", "unit": "MPa", "t_s": t, "values": [round(v, 3) for v in peak]})
    results.append(result("max_stress_over_time", peak[j], "MPa", f"t {t[j]:.3f} s"))
    return {
        "fields": fields,
        "series": series_out,
        "results": results,
        "effects": [{"name": (f"shell solved at {len(t)} instants of {lc_id}'s mount forces (t {t[0]:.3f} to "
                              f"{t[-1]:.3f} s, one factorisation): {said}; largest element stress {peak[j]:.1f} MPa "
                              f"at t {t[j]:.3f} s"), "value": hot[k] if hot else peak[j], "unit": "MPa"}],
        "assumptions": [FRAMES_ASSUMPTION.format(lc=lc_id)],
    }


# A finer level with more elements than this is left out (the study keeps its other levels): the sparse LU of the
# shell fills about 400 nonzeros per degree of freedom with the orderings scipy has, and the demo frame at 5 mm
# (about 130000 elements) took 30 s to 5 min to factorise on the development PC (an assumption, no source).
MAX_LEVEL_ELEMENTS = 100_000


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


# Levels of the convergence study solved in other processes while this one solves the base level
# (HIRAKU_SHELL_WORKERS, default 2; 0 or 1: one after another in this process). Processes add up their
# memory, so the levels go to other processes only when this process's memory now plus the estimated
# peak of every level solved at once stays within MEMORY_BUDGET (and within MEMORY_SHARE of the free
# memory). The estimate of one level's peak is BYTES_PER_ELEMENT x its elements, measured on the demo
# frame (10 mm, 49,612 elements: 2.3 GB of committed memory at the peak of one shell solve in one process
# with one BLAS thread, factorised by hiraku.solvers.sparse_lu); an estimate for other frames. With it the
# demo frame's 20 mm level goes to another process beside its 10 mm base (its 5 mm level is over
# MAX_LEVEL_ELEMENTS).
WORKERS = 2
PARALLEL_MIN_ELEMENTS = 10_000  # a smaller base level is solved in a few seconds: no processes for it
BYTES_PER_ELEMENT = 48_000
MEMORY_BUDGET = 5_000_000_000
MEMORY_SHARE = 0.6
THREAD_ENV = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}  # the levels' processes: one BLAS thread


def level_workers() -> int:
    try:
        return max(int(os.environ.get("HIRAKU_SHELL_WORKERS", WORKERS)), 0)
    except ValueError:
        return WORKERS


def _memory() -> tuple[int | None, int | None]:
    """(free physical memory, this process's committed memory) in bytes; None where it cannot be read."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

            st = MemoryStatus()
            st.dwLength = ctypes.sizeof(MemoryStatus)
            free = int(st.ullAvailPhys) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)) else None
            c = Counters()
            c.cb = ctypes.sizeof(Counters)
            k32 = ctypes.WinDLL("kernel32")
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            info = k32.K32GetProcessMemoryInfo
            info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            info.restype = wintypes.BOOL
            ok = info(k32.GetCurrentProcess(), ctypes.byref(c), c.cb)
            return free, (int(c.PagefileUsage) if ok else None)
        page = int(os.sysconf("SC_PAGE_SIZE"))
        free = int(os.sysconf("SC_AVPHYS_PAGES")) * page
        with open("/proc/self/statm") as f:
            used = int(f.read().split()[1]) * page
        return free, used
    except (AttributeError, OSError, ValueError, IndexError):
        return None, None


def parallel_memory(base_elements: int, sizes: list[float], base_size: float) -> tuple[bool, str]:
    """Whether the levels `sizes` may be solved in other processes beside the base level (base_elements at
    base_size), and why or why not."""
    est = [base_elements * (base_size / s) ** 2 for s in sizes]  # elements go as 1 / size^2
    solved = [e for e in est if e <= MAX_LEVEL_ELEMENTS]
    free, used = _memory()
    need = (used or 0) + (base_elements + sum(solved)) * BYTES_PER_ELEMENT
    cap = MEMORY_BUDGET if free is None else min(MEMORY_BUDGET, MEMORY_SHARE * free)
    if need > cap:
        return False, f"about {need / 1e9:.1f} GB with all levels at once, over the {cap / 1e9:.1f} GB allowed"
    return True, f"about {need / 1e9:.1f} GB with all levels at once"


@dataclass
class _LevelFrame:
    """What the convergence study reads of a level's ShellFrame (the factorised model stays in its process)."""
    node_xyz: dict
    model: object = None


def solve_level(design: Record, s: float, solve: dict, base_size: float) -> dict:
    """One level of the convergence study (in this process or another): {"size", "left_out": elements} for a
    finer level above MAX_LEVEL_ELEMENTS, else {"size", "res" (analyze_shell's result without the
    factorised frame and U), "zz" (zz_error), "seconds" (analyze_shell), "n_dof_free", "elements",
    "factor_s"}."""
    if s < base_size:
        n = build_mesh(design, s).n_elements
        if n > MAX_LEVEL_ELEMENTS:
            return {"size": s, "left_out": n}
    # a frame of its own, not the process's cache (that keeps the base level's frame for the next load case)
    fr = ShellFrame.build(design, s)
    r = analyze_shell(design, element_size_mm=s, frame=fr, **solve)
    zz = zz_error(fr.model, r["stress"])
    out = {k: v for k, v in r.items() if k not in ("frame", "U")}
    out["frame"] = _LevelFrame(fr.node_xyz)
    return {"size": s, "res": out, "zz": zz, "seconds": r["timings"].get("total_s", 0.0),
            "n_dof_free": int(fr.model.timings.get("n_dof_free", 0)), "elements": int(fr.mesh.n_elements),
            "factor_s": float(fr.model.timings.get("factor_s", 0.0))}


class Levels:
    """The other levels of the convergence study, started before the base level is solved: in other
    processes when level_workers() > 1 and the memory estimate fits, else solved here and now, one
    after another (each level's factors are freed before the base level is factorised, so this
    process holds one factorisation at a time)."""

    def __init__(self, design: Record, size: float, solve: dict, base_elements: int | None = None):
        self.design, self.size, self.solve = design, size, solve
        self.sizes = [s for s in convergence_sizes(size) if abs(s - size) >= 1e-9]
        self.pool = None
        self.futures = {}
        self.where = "this process, one after another"
        self._done = None
        self._start(base_elements)
        if self.pool is None:
            self._done = [solve_level(self.design, s, self.solve, self.size) for s in self.sizes]

    def _start(self, base_elements: int | None) -> None:
        size, design, solve = self.size, self.design, self.solve
        n = min(level_workers(), len(self.sizes))
        if n <= 1 or base_elements is None or base_elements < PARALLEL_MIN_ELEMENTS:
            return
        ok, why = parallel_memory(base_elements, self.sizes, size)
        if not ok:
            self.where = f"this process, one after another ({why})"
            return
        saved = {k: os.environ.get(k) for k in THREAD_ENV}
        os.environ.update(THREAD_ENV)  # the spawned processes read it when they start (here, in Pool())
        try:
            self.pool = multiprocessing.get_context("spawn").Pool(n)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.futures = {s: self.pool.apply_async(solve_level, (design, s, solve, size)) for s in self.sizes}
        self.where = f"{n} other processes, beside the base level"

    def results(self) -> list[dict]:
        try:
            if self.pool is None:
                return self._done
            return [self.futures[s].get() for s in self.sizes]
        finally:
            self.close()

    def close(self) -> None:
        """Stop the processes (a level still being solved is dropped)."""
        if self.pool is not None:
            self.pool.terminate()
            self.pool.join()
            self.pool = None


def mesh_convergence(design: Record, res: dict, solve: dict, mesh: Record | None,
                     zz_res: dict, levels: Levels | None = None) -> tuple[Record, list[Record]]:
    """analysis.mesh_convergence and the effects, from res (solved at its element size) and the two other levels
    of convergence_sizes (`levels`, started before res was solved; None: solved here). The judged hot spot is
    read on the same weld line and surface on every level: the one of largest size on the level that is the
    version's mesh (else res's level). The free DOF of every level go into the effects, its clock time
    into res's model.timings["convergence_levels"] (shell_timings reads it for timings_s)."""
    size = res["mesh"].element_size_mm
    levels = levels or Levels(design, size, solve)
    t0 = time.perf_counter()
    done = {lv["size"]: lv for lv in levels.results()}
    waited = time.perf_counter() - t0
    solved_in = levels.where
    levels_res = {}
    times = {}
    left_out = []
    level_zz = {}
    timing_rows = []
    for s in convergence_sizes(size):
        if abs(s - size) < 1e-9:
            levels_res[s] = res
            m = res["frame"].model
            timing_rows.append({"element_size_mm": float(s), "elements": int(res["mesh"].n_elements),
                                "n_dof_free": int(m.timings.get("n_dof_free", 0)),
                                "total_s": float(res["timings"].get("total_s", 0.0)),
                                "factor_s": float(m.timings.get("factor_s", 0.0)), "where": "base"})
            continue
        lv = done[s]
        if "left_out" in lv:
            n = lv["left_out"]
            left_out.append(f"the {s:g} mm level ({n} elements) is left out: more than {MAX_LEVEL_ELEMENTS} "
                            f"elements (its factorisation takes minutes; an assumption)")
            continue
        times[s] = lv["seconds"]
        levels_res[s] = lv["res"]
        level_zz[s] = lv["zz"]
        timing_rows.append({"element_size_mm": float(s), "elements": lv["elements"], "n_dof_free": lv["n_dof_free"],
                            "total_s": float(lv["seconds"]), "factor_s": lv["factor_s"], "where": solved_in})
    res["frame"].model.timings["convergence_levels"] = timing_rows
    # not ending in _s: analyze_shell copies the model's *_s timings, and the model may be reused (cache.py)
    res["frame"].model.timings["convergence_wait"] = waited
    sizes = list(levels_res)
    recorded = {s: mesh is not None and same_mesh(r, mesh) is None for s, r in levels_res.items()}
    base = next((r for s, r in levels_res.items() if recorded[s]), res)
    # per level: the hot spot on the base's weld line, the element peak, the global error estimate
    rows = []
    for s in sizes:
        r = levels_res[s]
        zz = level_zz.get(s) if r is not res else None
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
    # edge of the element at the peak on each level: the same on every level when the weld strips' grid sizes it
    edges = [float(r["res"]["mesh"].edge_lengths()[r["res"]["worst_element"]].max()) for r in rows]
    peak_fixed = max(edges) <= 1.01 * min(edges)
    levels = []
    for row, v in zip(rows, values):
        lv = {"element_size_mm": float(row["size"]), "elements": int(row["res"]["mesh"].n_elements), "value": float(v)}
        if recorded[row["size"]]:
            lv["mesh_id"] = mesh["mesh_id"]
        levels.append(lv)
    where = _where_hot(key) if quantity == "max_hot_spot_stress" else "the largest element stress"
    notes.insert(0, f"read at {where} on every level")
    notes += left_out
    if quantity == "max_hot_spot_stress":
        notes.append("the strip elements it is read on are t x t on every level (IIW); the levels refine the mesh "
                     "around them, not the read elements, so the verdict says how the value settles as the mesh "
                     "around the read points is refined")
    rec = convergence_record(quantity, unit, levels, LIMIT_PCT, REFINE_RATIO, "; ".join(notes))
    if quantity == "max_stress" and peak_fixed and rec["verdict"] == "converged":
        # the element peak sits in a strip element that does not shrink with element_size (PEAK_ASSUMPTION):
        # a small change says nothing of convergence there
        rec["verdict"] = "not_converged"
        rec["notes"] = "; ".join(x for x in (rec.get("notes"), (
            f"the element at the peak is {edges[-1]:.2g} mm on every level (sized by the weld strips' grid, not by "
            f"element_size), so its small change is not convergence; not judged converged")) if x)

    # effects: the judged value, the element peak, the error estimate and the time
    sz = " / ".join(f"{s:g}" for s in sizes)
    vs = " / ".join(f"{v:.1f}" for v in values)
    rich = f", observed order {rec['observed_order']:.2f}" if "observed_order" in rec else ""
    if "extrapolated" in rec:
        rich += f", extrapolated {rec['extrapolated']:.1f} {unit}"
        if "error_pct" in rec:
            rich += f" (finest {rec['error_pct']:.1f} % from it)"
    effects = [{"name": (f"mesh convergence of {quantity} at {where}: {sz} mm -> {vs} {unit}; change between the two "
                         f"finest {rec['change_pct']:.1f} % (limit {LIMIT_PCT:g} %){rich}; {rec['verdict']}"
                         + "".join(f"; {x}" for x in left_out)),
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
    if peak_fixed:
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
    dof = {round(r["element_size_mm"], 9): r["n_dof_free"] for r in timing_rows}
    effects.append({"name": ("mesh convergence solves: " + ", ".join(
        f"{s:g} mm ({levels_res[s]['mesh'].n_elements} elements, {dof.get(round(float(s), 9), 0)} free DOF)"
        for s in times) + " (where and how long each took: timings_s)"),
        "value": float(len(times)), "unit": "1"})
    return rec, effects


def shell_timings(res: dict, converged: bool) -> dict[str, float]:
    """analysis.timings_s (clock times, never in results, effects or text): the shell solve's parts
    (analyze_shell's *_s timings) and, after mesh_convergence, each other level's solve (named with where it ran:
    this process or another, which hangs on the free memory) and the wait for them."""
    out = {f"shell_{k[:-2]}": float(v) for k, v in res["timings"].items() if k.endswith("_s")}
    if converged:
        t = res["frame"].model.timings
        for row in t.get("convergence_levels", []):
            if row["where"] != "base":
                where = "other_process" if "other process" in row["where"] else "this_process"
                out[f"convergence_level_{row['element_size_mm']:g}mm_{where}"] = float(row["total_s"])
        out["convergence_wait"] = float(t.get("convergence_wait", 0.0))
    return out


class FrameShell(BuiltinTool):
    name = "frame_shell"
    stage = 3  # shell FE, mesh convergence
    applies_to = ("ladder_4w",)  # the mesh is built on a ladder's rails and cross members (frame_tubes: space frames)
    title = "フレームの静的 (シェル: 管の壁の板)"
    domain = "strength"
    base = "K1"
    tests = ["R1", "R9"]
    formulas = [
        "mount_bracket.plate_bending",
        "mount_bracket.weld",
        "frame_static.joint_wall_bending",
        "frame_static.rail_joint_stress",
        "frame_static.rail_step_bending",
        "frame_static.rail_bending",
        "frame_static.bending_stress",
        "mass_props.static_wheel_loads",
        PATH_FORMULA,
    ]
    load_case_params = (LOADING, G_FACTOR, STEP_HEIGHT, STEP_SUPPORTS, R9_SUPPORTS, R9_SUPPORT)
    run_params = (ELEMENT_SIZE, MESH_CONVERGENCE)
    judged_as = {"max_stress": "max_hot_spot_stress"}  # the contract: max_stress is reported, not judged
    predicts = (
        Predicts("max_hot_spot_stress", "frame_static.joint_wall_bending", "sigma", WALL_INPUTS),
        Predicts("max_stress", "frame_static.rail_joint_stress", "sigma",
                 JOINT_INPUTS + "; the shell includes the stress concentration at the joints (wall bending where "
                                "tubes meet, the load coming in at the mount), which the formula does not"),
        Predicts("max_stress", "frame_static.rail_step_bending", "sigma",
                 STEP_BENDING + "; the shell includes the stress concentration at the joints, which the formula does not"),
        Predicts("wheel_load_<support>", "mass_props.static_wheel_loads", "wheel_load_<support>",
                 "at rest only (no step_height, no upstream forces)"),
        # R9 plate and weld cases (the brackets meshed as plates): declared so that the run's stage rule
        # (tools.shelf.load_case_stage) counts frame_shell as the tool that gives them
        Predicts("plate_bending_utilisation", "mount_bracket.plate_bending", "u",
                 "R9 plates: the hand formula takes the plate as a cantilever from the tube wall; the shell has the "
                 "concentration around the pin hole and the wall's bending"),
        Predicts("weld_utilisation", "mount_bracket.weld", "u",
                 "R9 welds: the hand formula takes the throat's mean stress; the shell has the weld line's ends"),
        PATH_PREDICTS,
    )
    upstream = ("an analysis with reaction and grip vectors at every support (step_climb, step_climb_dynamic): "
                "its ground forces load the frame instead of step_height")
    needs_mesh = True

    def known_answers(self) -> list[Record]:
        from ..k1_shell.checks import shell_known_answers  # k1_shell.checks imports this package's fixtures

        return shell_known_answers()

    @staticmethod
    def _elements(design: Record, size: float) -> int | None:
        """Elements of the base level's mesh (for the memory estimate of the levels), None if it cannot be made."""
        try:
            return build_mesh(design, size).n_elements
        except Exception:  # analyze_shell says what is wrong with the design
            return None

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: load_case_params. Run params: upstream_analyses (as
        frame_static: ground forces and the inertia force from an upstream
        analysis), mesh (the version's shell mesh record: the shell stresses,
        the error estimate and the displacements are added as fields on it when
        it is the mesh solved here), and run_params."""
        design = on_board(design, load_case)
        if load_case.get("test_id") == "R9":
            size = run_param(params, ELEMENT_SIZE)
            up = params.get("upstream_analyses") or load_case.get("upstream_analyses")  # (not a declared load case param)
            return r9_outputs(design, load_case, up, size)
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
        series = upstream_mount_series(params.get("upstream_analyses"), design)
        levels = None
        if not series and conv == "3":  # the other levels start now, beside the base level
            levels = Levels(design, size, solve, self._elements(design, size))
        try:
            res = analyze_shell(design, element_size_mm=size, **solve)
        except BaseException:
            if levels is not None:
                levels.close()
            raise
        ground = {sid: [(k, p, f) for k, p, f in gr["vectors"]] for sid, gr in res["ground"].items()}
        beam = beam_analyze(design, g_factor=g, ground_forces=ground, inertia_N=inertia)
        out = shell_outputs(design, res, beam, up[0] if up else None, None if up else step, sup)
        hot = shell_hot_spots(self, load_case, res)
        if hot is not None:
            out["hot_spots"] = hot
        untied = untied_joints(res)
        if untied:
            out.setdefault("assumptions", []).append(UNTIED_ASSUMPTION.format(ids=", ".join(untied)))
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
        if series:
            add_frames(out, shell_frames(design, res, series, mesh if mesh is not None and why is None else None))
        elif conv == "3":
            out["mesh_convergence"], eff = mesh_convergence(design, res, solve, mesh, zz, levels)
            out["effects"] += eff
            out["assumptions"].append(convergence_assumption())
        out["timings_s"] = shell_timings(res, not series and conv == "3")
        if up is None and step is not None:
            out["scene"] = scene.steps(design, sup, step)
        return out

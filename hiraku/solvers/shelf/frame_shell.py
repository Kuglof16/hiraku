"""frame_shell: stresses of the frame on the shell mesh of the tube walls (K1 shell, test R1).

The same loads as frame_static (K1 beams) put on the mid-surface shell
model (hiraku.solvers.k1_shell.frame), so the stress at the joints (wall
bending where one tube meets the side of another, the load coming in at a
suspension mount) shows. The beam model is solved with the same ground
forces for comparison, and the effects say how the shell's peak splits into
membrane and wall bending, how it compares with the beam at the same place
and away from the joints, and what loads enter the joint next to it.
"""

from __future__ import annotations

import numpy as np

from ..k1_frame import analyze as beam_analyze
from ..k1_frame import stress_terms
from ..k1_frame.surface import wall_von_mises
from ..k1_shell.frame import analyze_shell, element_place, membrane_bending
from . import scene
from .base import G_FACTOR, LOADING, BuiltinTool, Predicts, Record, mesh_of, on_board, param_list, param_value, result
from .frame_static import (JOINT_INPUTS, STEP_BENDING, STEP_HEIGHT, STEP_SUPPORTS, front_supports, g_factor,
                           upstream_ground_forces)

AWAY_FACTOR = 2.0  # "away from the joints": element centre at least this many tube sizes from every design node
LABELS = {
    "max": "シェルの von Mises (外と内の面の大きい方)",
    "top": "シェルの von Mises (外の面)",
    "bottom": "シェルの von Mises (内の面)",
}
LABEL_MEMBER = "シェルの von Mises (部材ごとの最大)"
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


def shell_outputs(design: Record, res: dict, beam: dict, up_lc: str | None, step, sup) -> Record:
    """results / fields / vectors / effects of one frame_shell analysis."""
    worst, e = res["worst_member"], res["worst_element"]
    peak = res["member_max"][worst][0]
    bworst = beam["worst_member"]
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
         "per_member": {m: round(v, 2) for m, (v, _) in res["member_max"].items()}},
        {"kind": "stress", "label": LABEL_BEAM, "unit": "MPa",
         "per_member": {m: round(s["max"], 2) for m, s in beam["stresses"].items()}},
    ]

    # ---- effects: the material for explaining the shell peak
    place = element_place(res, e)
    mb = membrane_bending(res, e)
    beam_same = beam["stresses"][worst]["max"]
    beam_here = float(wall_von_mises(beam["model"], worst, [place["centre_mm"]])[0])
    fx = res["ground_source"]
    effects = [
        {"name": (f"shell max {peak:.1f} MPa: element {e} of {worst}, {place['s_mm']:.0f} mm from its node_a, "
                  f"{place['wall']} wall, {place['node_distance_mm']:.0f} mm from node {place['node']} "
                  f"({res['mesh'].joint_kinds.get(place['node'], '?')} joint); "
                  f"{'inner' if mb['inner_vm'] > mb['outer_vm'] else 'outer'} surface"),
         "value": peak, "unit": "MPa"},
        {"name": f"shell max / beam max in {worst} (stress concentration at the joint)",
         "value": peak / beam_same if beam_same > 0 else 0.0, "unit": "1"},
        {"name": f"beam max in {worst} (same ground forces)", "value": beam_same, "unit": "MPa"},
        {"name": f"beam stress at the same point of {worst} (outer surface, beam theory)", "value": beam_here,
         "unit": "MPa"},
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
    size = {m: _tube_size(design, m) for m in res["member_max"]}
    far = np.array([dist[i] >= AWAY_FACTOR * size[m] for i, m in enumerate(res["mesh"].element_member)])
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
    effects += [
        {"name": src},
        {"name": "hold reaction / weight (six restraints that only stop rigid-body motion)",
         "value": res["residual_ratio"], "unit": "1"},
        {"name": "ground forces against the weight (and inertia passed in): imbalance / weight",
         "value": res["balance_off"], "unit": "1"},
        {"name": (f"tube mass from the shell elements (rho t area) {res['shell_mass_kg']:.2f} kg vs "
                  f"{res['mass']['frame_mass_kg']:.2f} kg from density A L (beam); the difference is left to inertia relief"),
         "value": res["shell_mass_kg"] - res["mass"]["frame_mass_kg"], "unit": "kg"},
        {"name": (f"shell model: {res['mesh'].n_nodes} nodes, {res['mesh'].n_elements} elements "
                  f"(edge {res['mesh'].element_size_mm:g} mm); suspension and part loads enter as RBE3 forces on "
                  f"the joint nodes (ring and wall patch); sharp corners, no welds"),
         "value": res["timings"].get("total_s", 0.0), "unit": "s"},
    ]
    if float(np.linalg.norm(res["inertia_alpha"])) > 0:
        effects.append({"name": f"angular acceleration from inertia relief {_vec(res['inertia_alpha'], 3)} rad/s^2 "
                                f"(value: its size in rad/s^2)",
                        "value": float(np.linalg.norm(res["inertia_alpha"]))})
    effects += [dict(t, name="beam: " + t["name"]) for t in stress_terms(beam)]
    return {"results": results, "fields": fields, "vectors": vectors, "effects": effects}


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
        "frame_static.rail_joint_stress",
        "frame_static.rail_step_bending",
        "frame_static.rail_bending",
        "frame_static.bending_stress",
        "mass_props.static_wheel_loads",
    ]
    load_case_params = (LOADING, G_FACTOR, STEP_HEIGHT, STEP_SUPPORTS)
    predicts = (
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

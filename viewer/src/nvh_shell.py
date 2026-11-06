"""NVH from the shell modal record (modal_shell, N1): python -m viewer.src.nvh_shell <run dir> [--out viewer/data]

<run dir>: a run folder (v###/ with analyses/ and meshes/) whose newest modal_shell record is read, or a folder
holding one record as analysis.json with meshes/<mesh_id>.json, and design.json or run.json (run.json's
"design" path and "load_case" are read; --design overrides the design). Writes viewer/data/nvh_shell.js (VIEW_DATA["nvh_shell"]);
the page shows it in the NVH field, with a beam / shell switch when data/nvh.js is of the same design
(source.design_key).

Field keys added to the common object (see common.py):
    model "shell"
    shell {n_nodes, n_elements, element_size_mm, nodes (f32, n x 3, mm), quads (i32, ne x 4, node indices),
           member (i16 index per element into member_names), member_names, thickness_mm {member: t}}
    modes [{n, label, kind (body / frame / rollbar), f_Hz,
            shape (i16, n x 3, the record's per_mesh_node, largest |u| = 1),
            strain (i16 per element, the record's relative strain field, 0..1; null if the record has none),
            beam {mode, kind, f_Hz, mac, diff} (the record's pairing by MAC), share_bending,
            parts {c, t, w, rel} (as nvh.py part_motion), wheels [[ux, uy, uz] per wheel]}]
    first {body, frame, rollbar}: 0-based mode index or null (the record's first_*_mode field_index / 2)
    beam_first {frame, body}: the beam model's Hz from the same record (first_frame_mode_beam, first_body_mode_beam)
    wheels, part_nodes as nvh.py
    joint_method: text, how the design nodes' motion is taken from the mesh
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

from viewer.src import common
from viewer.src import nvh as beam

KIND = {"body on springs": "body", "frame": "frame", "roll bar": "rollbar"}
JOINT_METHOD = (
    "設計の節の動き = その節に集まる部材の要素の、節から (断面の対角の半分 + 要素 1.5 個分) 以内のシェルの節の、"
    "並進の平均 (modal_shell が梁と MAC を取るときの、節ごとのシェルの節の平均と同じ考え。節の選び方はビューアのもの)。"
    "車輪の中心は、支えの frame_node のその節の群れに当てはめた剛体の動きで動かす (記録の仮定: 車輪の中心は取付に剛な棒)。")
PAIR = re.compile(r"shell mode (\d+) \((.+?)\) ([\d.]+) Hz vs beam mode (\d+) \((.+?)\) ([\d.]+) Hz, MAC ([\d.]+)")


def find_record(args) -> tuple[Path, Path, dict, dict, dict | None, dict]:
    """(run, vdir, record, design, load case, mesh) from a run folder or a one-record folder."""
    run = Path(args.run)
    one = run / "analysis.json"
    if one.is_file():
        rec = common.read_json(one)
        rec["_path"] = str(one)
        meta = common.read_json(run / "run.json") if (run / "run.json").is_file() else {}
        dpath = Path(args.design or (run / "design.json" if (run / "design.json").is_file() else meta.get("design")) or "")
        if not dpath.is_file():
            raise FileNotFoundError(f"{run}: give --design (run.json names no design file that exists)")
        design = common.read_json(dpath)
        lc = meta.get("load_case") if (meta.get("load_case") or {}).get("load_case_id") == rec.get("load_case_id") else None
        vdir = run
    else:
        vdir, rec = common.find_analysis(run, "modal_shell", version=args.version, analysis_id=args.analysis)
        design = common.read_json(Path(args.design) if args.design else vdir / "design.json")
        lc = common.load_case_of(vdir, rec["load_case_id"])
    mid = next((f.get("mesh_id") for f in rec.get("fields", []) if f.get("mesh_id")), None)
    if mid is None:
        raise LookupError(f"{rec['_path']}: no fields on a mesh (the record was solved on another mesh size)")
    mesh = common.read_json(vdir / "meshes" / f"{mid}.json")
    return run, vdir, rec, design, lc, mesh


def joint_nodes(design: dict, mesh: dict) -> dict[str, list[int]]:
    """Per design node used by a member: the mesh nodes of its members' elements within its radius (JOINT_METHOD)."""
    xyz = {n["id"]: [float(c) for c in n["xyz_mm"]] for n in design.get("nodes", [])}
    secs = design.get("sections", {})
    at: dict[str, list[dict]] = {}
    for m in design.get("members", []):
        for k in ("node_a", "node_b"):
            at.setdefault(m[k], []).append(m)
    by_member: dict[str, set[int]] = {}
    for q, mid in zip(mesh["elements"], mesh["element_member"]):
        by_member.setdefault(mid, set()).update(q)
    P = mesh["nodes_mm"]
    h = float(mesh.get("element_size_mm", 10.0))
    out = {}
    for nid, ms in at.items():
        if nid not in xyz:
            continue
        x = xyz[nid]
        rad = 0.0
        for m in ms:
            sp = beam.section_props(secs.get(m.get("section")))
            if sp:
                rad = max(rad, math.hypot(sp["c_v"], sp["c_s"]))
        r2 = (rad + 1.5 * h) ** 2
        cand = set().union(*(by_member.get(m["id"], set()) for m in ms))
        out[nid] = sorted(i for i in cand if (P[i][0] - x[0]) ** 2 + (P[i][1] - x[1]) ** 2 + (P[i][2] - x[2]) ** 2 <= r2)
    return out


def node_motion(design: dict, mesh: dict, joints: dict[str, list[int]], u: list[list[float]]) -> dict[str, list[float]]:
    """Design node -> mean translation of its joint's mesh nodes; wc:<support> -> the rigid motion of the support's
    frame node joint at the wheel centre."""
    out = {}
    for nid, idx in joints.items():
        if idx:
            out[nid] = [sum(u[i][k] for i in idx) / len(idx) for k in range(3)]
    P = mesh["nodes_mm"]
    for s in design.get("supports", []):
        idx = joints.get(s.get("frame_node"), [])
        if not idx:
            continue
        c, t, w = common.rigid_fit([P[i] for i in idx], [u[i] for i in idx])
        p = [float(v) for v in s.get("position_mm", c)]
        r = [p[k] - c[k] for k in range(3)]
        out[f"wc:{s['id']}"] = [t[0] + w[1] * r[2] - w[2] * r[1], t[1] + w[2] * r[0] - w[0] * r[2],
                                t[2] + w[0] * r[1] - w[1] * r[0]]
    return out


def pairs_of(rec: dict) -> dict[int, dict]:
    out = {}
    for e in rec.get("effects", []):
        m = PAIR.match(str(e.get("name", "")))
        if m:
            out[int(m.group(1))] = {"mode": int(m.group(4)), "kind": KIND.get(m.group(5), m.group(5)),
                                    "f_Hz": float(m.group(6)), "mac": float(m.group(7)), "diff": e.get("value")}
    return out


def build(args) -> dict:
    run, vdir, rec, design, lc, mesh = find_record(args)
    obj = common.base_object("nvh", "NVH (シェル): 管の壁の板の固有振動", run, vdir, rec, design)
    obj["model"] = "shell"
    if (Path(run) / "analysis.json").is_file():  # a one-record folder: its own name, the design's version
        obj["source"]["run"] = Path(run).resolve().name
        obj["source"]["version"] = f"v{int(design.get('design_version') or rec.get('design_version') or 1):03d}"
    obj["ledger"] = common.parts_ledger("modal", design, lc, rec, obj["parts"])
    nodes, quads = mesh["nodes_mm"], mesh["elements"]
    names = sorted(set(mesh["element_member"]))
    ix = {n: i for i, n in enumerate(names)}
    obj["shell"] = {"n_nodes": len(nodes), "n_elements": len(quads), "element_size_mm": mesh.get("element_size_mm"),
                    "nodes": common.pack_f32([c for p in nodes for c in p], [len(nodes), 3]),
                    "quads": common.pack_i32([i for q in quads for i in q], [len(quads), 4]),
                    "member": common.pack_i16([ix[m] for m in mesh["element_member"]], 1.0, [len(quads)]),
                    "member_names": names, "thickness_mm": mesh.get("member_thickness_mm", {})}
    joints = joint_nodes(design, mesh)
    effects = {e["name"]: e.get("value") for e in rec.get("effects", [])}
    pairs = pairs_of(rec)
    fields = rec.get("fields", [])
    shapes = [f for f in fields if f.get("kind") == "mode_shape"]
    strains = {round(float(f["frequency_Hz"]), 9): f for f in fields if f.get("kind") == "stress"}
    modes, wheels = [], None
    for i, f in enumerate(shapes):
        n = i + 1
        m = re.match(r"mode (\d+) \((.+?)\)", f.get("label", ""))
        if m:
            n = int(m.group(1))
        kind = KIND.get(m.group(2), m.group(2)) if m else ""
        u = f["per_mesh_node"]
        st = strains.get(round(float(f["frequency_Hz"]), 9))
        per = node_motion(design, mesh, joints, u)
        pm = beam.part_motion(obj["parts"], obj["ledger"], design, per)
        if wheels is None:
            wheels = beam.wheels_of(design, per)
            obj["part_nodes"] = pm["nodes"]
        share = next((v for k, v in effects.items() if k.startswith(f"mode {n} (") and "bends the frame" in k), None)
        modes.append({
            "n": n, "label": f.get("label", f"mode {n}"), "kind": kind, "f_Hz": float(f["frequency_Hz"]),
            "shape": common.pack_i16([c for p in u for c in p], 1.0 / 32767, [len(u), 3]),
            "strain": common.pack_i16(st["per_element"], 1.0 / 32767, [len(quads)]) if st else None,
            "beam": pairs.get(n), "share_bending": share,
            "parts": {"t": common.pack_f32([c for v in pm["t"] for c in v], [len(pm["t"]), 3]),
                      "w": common.pack_f32([c for v in pm["w"] for c in v], [len(pm["w"]), 3]),
                      "c": common.pack_f32([c for v in pm["c"] for c in v], [len(pm["c"]), 3]), "rel": pm["rel"]},
            "wheels": [[float(c) for c in per.get(w["node"], [0.0, 0.0, 0.0])] if w["node"] else [0.0, 0.0, 0.0]
                       for w in wheels]})
    if not modes:
        raise LookupError(f"{rec['_path']}: no mode_shape fields")
    obj["modes"] = modes
    obj["wheels"] = wheels or []
    fi = {r["quantity"]: r.get("field_index") for r in rec.get("results", [])}
    obj["first"] = {k: (fi.get(f"first_{k}_mode") // 2 if fi.get(f"first_{k}_mode") is not None else None)
                    for k in ("body", "frame", "rollbar")}
    obj["beam_first"] = {k: common.result(rec, f"first_{k}_mode_beam") for k in ("frame", "body")}
    obj["joint_method"] = JOINT_METHOD
    lines = []
    for q, lab in (("wheel_rotation_frequency", "wheel rotation"), ("motor_rotation_frequency", "motor rotation")):
        v = common.result(rec, q)
        if v is not None:
            lines.append({"label": lab, "f_Hz": v})
    obj["lines"] = lines
    nfreq = sum(1 for r in rec["results"] if re.fullmatch(r"mode_\d+", r["quantity"]))
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"]) + [
        {"label": "shell mesh", "value": f"{len(nodes)} nodes, {len(quads)} quads, edge {mesh.get('element_size_mm')} mm",
         "source": f"the record's mesh ({mesh.get('mesh_id')}, {mesh.get('formulation', '')[:40]})"},
        {"label": "modes", "value": f"{len(modes)} mode shapes stored of {nfreq} frequencies",
         "source": "record fields (mode_shape per_mesh_node, stress per_element)"},
        {"label": "excitation", "value": ", ".join(f"{l['label']} {l['f_Hz']:.2f} Hz" for l in lines) or "none in the record",
         "source": "the record's results at the case's speed"},
    ]
    obj["notes"] += [
        "モード形は大きさを持たない (相対値)。変形は表示用の倍率で、実寸ではない。",
        "色のひずみは記録の値 (要素の中心の von Mises 応力 = E x ひずみ、外と内の面の大きい方、そのモードの最大を 1)。",
        "梁との対応は記録のもの (設計の節の並進での MAC がいちばん大きい梁のモード)。",
        beam.PART_MOTION_NOTE,
        JOINT_METHOD,
    ]
    return obj


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m viewer.src.nvh_shell", description="Write viewer/data/nvh_shell.js")
    ap.add_argument("run", type=Path, help="run folder (v###/) or a folder with analysis.json, meshes/ and run.json")
    ap.add_argument("--out", type=Path, default=common.DEFAULT_OUT)
    ap.add_argument("--version")
    ap.add_argument("--analysis")
    ap.add_argument("--design", help="the design.json the record was solved on (default: the run's)")
    args = ap.parse_args(argv)
    obj = build(args)
    path = common.write_data("nvh_shell", obj, args.out)
    print(f"wrote {path} ({path.stat().st_size} bytes) from {obj['source']['run']} {obj['source']['analysis_id']}")
    return path


if __name__ == "__main__":
    main(sys.argv[1:])

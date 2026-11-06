"""Strength (R1 / R1s and the step hit) data for the viewer.

    python -m viewer.src.strength <run dir>                                   frame_static record (beams, the light version)
    python -m viewer.src.strength <version dir> --frames <dir> [--name NAME]  saved shell frames (the step hit)

model "beam" (frame_static, K1 beams at one instant):
    stress {label, unit, per_member (frame.member_ids order, null where none), max}
    disp {per_node (packed f32 n x 3, mm), max}; part_motion {c, t, w (packed nparts x 3)}: each part box's rigid motion per
        unit of the displacement (nvh.part_motion on the nodes the tool loads it on)
    hot_spots [{where, quantity, value, unit}], criterion {quantity, op, limit, value, ok} | null, marks (yields)
model "shell" (frames saved by a step-hit run of frame_shell with K2's mount loads, nothing solved here):
    the files: frames.npz (nodes, elements, member, excluded, t_s, vm_max [T, E] MPa = max(outer, inner) at the element
    centre, disp [T, N, 3] mm = U less the rigid fit of the wheel-mount joints), frames_meta.pkl (hot: the IIW hot spot per
    instant; weld_lines; element_size; design: the variables changed from the version's design), load_paths.json (each
    mount force at one instant and the mesh nodes it is spread on, mount_path).
    mesh {nodes (packed f32 N x 3), quads (packed u32 E x 4), member (packed u16 E: index in members), members,
          excluded (packed u8 E)}
    vm (packed u16 T x E, MPa x 10), disp (packed i16 T x N x 3: q = round(d / disp_scale), instant 0 as is, instant i as
          q[i] - q[i-1]; per instant all low bytes then all high bytes), disp_scale
    t [s per instant], hot [{value, joint, member, pos} | null per instant], el_max [[MPa, element, member] per instant]
    mounts [{support, mount, hp, F, how, n, xyz (packed), f (packed)}] at forces_at_t_s; mount_path
    joints [{name, centre}] (weld-line ends' mean per joint), part_off (packed f32 T x nparts x 3): each part box moved by
          the mean displacement of the mesh nodes at the design nodes the shell loads it on
Packed arrays: as common.pack_f32 with dtype f32 / u32 / u16 / i16 / u8."""

from __future__ import annotations

import base64
import gzip
import json
import pickle
import re
import sys
from pathlib import Path

import numpy as np

from viewer.src import common, ledgers, names, nvh

NEAR_MM = 15.0  # mesh nodes within the nearest distance + this of a design node move a part box carried there


def pack_np(a, dtype: str) -> dict:
    np_t = {"f32": "<f4", "u32": "<u4", "u16": "<u2", "i16": "<i2", "u8": "u1"}[dtype]
    a = np.ascontiguousarray(np.asarray(a).astype(np_t))
    z = gzip.compress(a.tobytes(), compresslevel=6, mtime=0)
    return {"dtype": dtype, "shape": list(a.shape), "b64": base64.b64encode(z).decode("ascii")}


def unpack_np(p: dict) -> np.ndarray:
    np_t = {"f32": "<f4", "u32": "<u4", "u16": "<u2", "i16": "<i2", "u8": "u1"}[p["dtype"]]
    return np.frombuffer(gzip.decompress(base64.b64decode(p["b64"])), np_t).reshape(p["shape"])


def encode_disp(D: np.ndarray) -> tuple[dict, float]:
    """int16 per instant, as differences in time, byte-shuffled (see the module docstring)."""
    dmax = float(np.abs(D).max()) or 1.0
    scale = dmax / 32767.0
    q = np.rint(D / scale).astype(np.int32)
    dd = q.copy()
    dd[1:] = q[1:] - q[:-1]
    if np.abs(dd).max() > 32767:
        raise ValueError("displacement steps too large for 16-bit differences")
    b = dd.astype("<i2").reshape(len(D), -1).view(np.uint8).reshape(len(D), -1, 2)
    sh = np.concatenate([b[:, :, 0], b[:, :, 1]], axis=1)
    return pack_np(sh, "u8") | {"shape": list(D.shape), "dtype": "i16s"}, scale


def decode_disp(p: dict, scale: float) -> np.ndarray:
    raw = np.frombuffer(gzip.decompress(base64.b64decode(p["b64"])), np.uint8)
    T = p["shape"][0]
    raw = raw.reshape(T, 2, -1)
    dd = (raw[:, 0].astype(np.uint16) | (raw[:, 1].astype(np.uint16) << 8)).view(np.int16).astype(np.int32)
    return (np.cumsum(dd, axis=0) * scale).reshape(p["shape"])


def _yields(design: dict) -> list[dict]:
    """The yield stresses of the materials the frame's members use (not the parts' materials)."""
    mats = design.get("materials", {})
    used = {m.get("material") for m in design.get("members", [])}
    y = sorted({float(mats[k]["yield_MPa"]) for k in used if k in mats and mats[k].get("yield_MPa")})
    return [{"v": v, "label": f"降伏 {v:g}"} for v in y[:2]]


def _ok(op, v, lim):
    return {"<": v < lim, "<=": v <= lim, ">": v > lim, ">=": v >= lim}[op]


# ---------------------------------------------------------------- beam: frame_static record
def build_beam(args) -> dict:
    vdir, rec = common.find_analysis(args.run, "frame_static", version=args.version, analysis_id=args.analysis)
    design = common.read_json(vdir / "design.json")
    lc = common.load_case_of(vdir, rec["load_case_id"])
    obj = common.base_object("strength", names.title("strength", "フレームの梁の応力 (K1)"), args.run, vdir, rec, design)
    obj["model"] = "beam"
    fr = obj["frame"]
    st = next((f for f in rec.get("fields", []) if f.get("kind") == "stress" and "per_member" in f), None)
    dp = next((f for f in rec.get("fields", []) if f.get("kind") == "displacement" and "per_node" in f), None)
    if st is None or dp is None:
        raise LookupError(f"{rec['_path']}: needs a per_member stress field and a per_node displacement field")
    pm = [st["per_member"].get(m) for m in fr["member_ids"]]
    obj["stress"] = {"label": st.get("label", "stress"), "unit": st.get("unit", "MPa"), "per_member": pm,
                     "max": max((v for v in pm if v is not None), default=0.0)}
    per_node = {n: [float(c) for c in v] for n, v in dp["per_node"].items()}
    flat = [c for n in fr["node_ids"] for c in per_node.get(n, [0.0, 0.0, 0.0])]
    obj["disp"] = {"label": dp.get("label", ""), "per_node": common.pack_f32(flat, [len(fr["node_ids"]), 3]),
                   "max": max((float(np.linalg.norm(v)) for v in per_node.values()), default=0.0)}
    obj["ledger"] = ledgers.k1_ledger("frame_static", design, lc, rec, obj["parts"])
    pmo = nvh.part_motion(obj["parts"], obj["ledger"], design, per_node)
    n = len(obj["parts"])
    obj["part_motion"] = {k: common.pack_f32([c for v in pmo[k] for c in v], [n, 3]) for k in ("c", "t", "w")}
    obj["hot_spots"] = [{"where": h.get("where", ""), "quantity": h.get("quantity", ""),
                         "value": h["value"]["value"] if isinstance(h.get("value"), dict) else h.get("value"),
                         "unit": h["value"].get("unit", "") if isinstance(h.get("value"), dict) else ""}
                        for h in rec.get("hot_spots", []) or []]
    crit = (lc or {}).get("criterion")
    v = common.result(rec, crit["quantity"]) if crit else None
    obj["criterion"] = ({"quantity": crit["quantity"], "op": crit["op"], "limit": float(crit["limit"]["value"]),
                         "unit": crit["limit"].get("unit", ""), "value": v,
                         "ok": _ok(crit["op"], v, float(crit["limit"]["value"])) if v is not None else None}
                        if crit else None)
    obj["marks"] = _yields(design)
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"]) + [
        {"label": "wheel loads", "value": ", ".join(f"{s['id']} {common.result(rec, 'wheel_load_' + s['id']) or 0:.0f} N"
                                                     for s in design.get("supports", [])), "source": "record results wheel_load_<support>"},
        {"label": "mass", "value": f"{common.result(rec, 'vehicle_mass') or 0:.1f} kg", "source": "record result vehicle_mass"}]
    obj["notes"] = ["梁は K1 の 1 本の線 (管のつなぎ目の壁の応力は見えない: R1s のシェルで見る)。色は部材の中の相当応力の最大。",
                    "変形は記録の節点の変位 (車輪の中心の剛体の動きを除いたもの) × 倍率。部品の箱は載っている節点と一緒に動く。"]
    return obj


# ---------------------------------------------------------------- shell: saved frames
def _changes(text: str, base: dict) -> tuple[dict, list[str]]:
    """The variables in load_paths.json's design text ('v005 + tower_b 35, ..., tyre 60 N/mm'), with the units of the
    base design's variables; the tyre is K2's input only (the loads), not the geometry. Variables the version's design
    does not hold (the frames came from a checkout whose generator knows more) are returned apart, not applied."""
    out, unknown = {}, []
    body = text.split("+", 1)[1] if "+" in text else text
    for item in body.split(","):
        m = re.fullmatch(r"\s*(\w+)\s+(-?[\d.]+)(\s*\S+)?\s*", item)
        if not m or m.group(1) == "tyre":
            continue
        var = (base.get("variables") or {}).get(m.group(1))
        if var is None:
            unknown.append(item.strip())
            continue
        out[m.group(1)] = {"value": float(m.group(2)), "unit": var.get("unit", "mm")}
    return out, unknown


def build_shell(args) -> dict:
    from hiraku import designgen

    fdir = Path(args.frames)
    vdir = common.version_dirs(args.run)[-1] if not (Path(args.run) / "design.json").is_file() else Path(args.run)
    base = common.read_json(vdir / "design.json")
    z = np.load(fdir / "frames.npz")
    with open(fdir / "frames_meta.pkl", "rb") as f:
        meta = pickle.load(f)
    lp = json.loads((fdir / "load_paths.json").read_text(encoding="utf-8"))
    changes, unknown = _changes(lp.get("design", ""), base)
    design = designgen.regenerate(base, changes) if changes else base
    rec = {"analysis_id": "frames", "tool": "frame_shell", "load_case_id": f"R1s (mounts from {meta.get('lc', 'K2')})"}
    name = args.name or common.run_name(args.run, vdir)
    obj = common.base_object("strength", names.title("strength", "段差を越えるときのフレームのシェルの応力"), Path(args.run), vdir, rec, design)
    obj["source"]["run"] = name
    obj["model"] = "shell"
    X, E, member, excl = z["nodes"], z["elements"], z["member"], z["excluded"].astype(bool)
    ts, VM, D = z["t_s"], z["vm_max"], z["disp"]
    every = max(1, int(getattr(args, "every", 1) or 1))
    keep = list(range(0, len(ts), every))
    ts, VM, D = ts[keep], VM[keep], D[keep]
    members = sorted(set(member.tolist()))
    mid = {s: i for i, s in enumerate(members)}
    obj["mesh"] = {"nodes": pack_np(X, "f32"), "quads": pack_np(E, "u32"),
                   "member": pack_np([mid[s] for s in member], "u16"), "members": members,
                   "excluded": pack_np(excl, "u8"), "element_size_mm": meta.get("element_size")}
    obj["vm"] = pack_np(np.clip(np.rint(VM * 10.0), 0, 65535), "u16")
    obj["disp"], obj["disp_scale"] = encode_disp(D)
    obj["t"] = [float(t) for t in ts]
    hot = [meta["hot"][i] for i in keep]
    obj["hot"] = [None if not h else {"value": float(h["value"]), "joint": h["joint"], "member": h["member"],
                                      "pos": [float(c) for c in h["position_mm"]]} for h in hot]
    el = []
    for v in VM:
        k = int(np.argmax(np.where(excl, -1.0, v)))
        el.append([float(v[k]), k, str(member[k])])
    obj["el_max"] = el
    obj["vm_max"] = float(VM[:, ~excl].max())
    obj["disp_max"] = float(np.linalg.norm(D, axis=2).max())
    obj["mounts"] = [{"support": q["support"], "mount": q["mount"], "hp": q["hard_point_mm"], "F": q["force_N"],
                      "how": q.get("applied_on", ""), "n": len(q["nodes"]),
                      "xyz": pack_np(q["nodes_xyz_mm"], "f32"), "f": pack_np(q["nodal_force_N"], "f32")}
                     for q in lp["mounts"]]
    obj["forces_at_t_s"] = lp.get("forces_at_t_s")
    obj["mount_path"] = lp.get("mount_path")
    joints = {}
    for w in meta.get("weld_lines", []):
        joints.setdefault(w["joint"], []).extend([w["p0"], w["p1"]])
    obj["joints"] = [{"name": j, "centre": np.mean(p, axis=0).round(1).tolist()} for j, p in joints.items()]
    # parts layer: which parts the saved mesh holds (element labels), how the shell loads the rest; the boxes
    # move with the mesh nodes at their design nodes
    lc = {"params": {}}
    obj["ledger"] = ledgers.shell_ledger(design, lc, obj["parts"], set(members))
    xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])}
    near: dict[str, np.ndarray] = {}

    def nodes_at(n):
        if n not in near:
            d = np.linalg.norm(X - xyz[n], axis=1)
            near[n] = np.nonzero(d <= d.min() + NEAR_MM)[0]
        return near[n]

    sets = []
    for e in obj["ledger"]["parts"]:
        ns = [nodes_at(n) for n, _ in e["nodes"] if n in xyz] if e["cat"] != "not_in" else []
        sets.append(np.concatenate(ns) if ns else None)
    off = np.zeros((len(ts), len(sets), 3), np.float32)
    for j, s in enumerate(sets):
        if s is not None and len(s):
            off[:, j] = D[:, s].mean(axis=1)
    obj["part_off"] = pack_np(off, "f32")
    obj["marks"] = _yields(design)
    obj["inputs"] = [
        {"label": "frames", "value": f"{len(ts)} instants of the step hit ({ts[0]:.3f} to {ts[-1]:.3f} s)",
         "source": f"saved frames of a frame_shell run with the mount forces of {meta.get('lc', 'K2')} (K2 {meta.get('k2_status', '')})"},
        {"label": "design", "value": lp.get("design", ""), "source": "load_paths.json design (the version's design with these variables)"},
        *([{"label": "タイヤの剛さ", "value": f"{float(_tyre.group(1)):g} N/mm",
            "source": "取付の力を出した K2 の段差の計算で使った値 (load_paths.json の設計の文。版の設計ファイルの値ではない)"}]
          if (_tyre := re.search(r"tyre\s+([\d.]+)\s*N/mm", lp.get("design", ""))) else []),
        {"label": "parts layer", "value": "the version's design regenerated with " + (", ".join(f"{k} {v['value']:g}" for k, v in changes.items()) or "no change")
         + (f"; not applied (this generator has no such variable): {', '.join(unknown)}" if unknown else ""),
         "source": "hiraku.designgen.regenerate; the mesh itself is the saved one"},
        {"label": "mesh", "value": f"{len(X)} nodes, {len(E)} quads, element {meta.get('element_size')} mm", "source": "frames.npz"},
        {"label": "load path", "value": lp.get("mount_path", ""), "source": "load_paths.json mount_path"},
        {"label": "loads shown", "value": f"mount forces at t = {lp.get('forces_at_t_s')} s", "source": "load_paths.json"},
    ]
    obj["notes"] = [
        "色は要素の中心の相当応力 (外面と内面の大きい方)。車輪の取付の当たり面 (除外) は判定に使わない要素。",
        "変形は車輪の取付の剛体の動きを除いた変位 × 倍率 (実寸ではない)。",
        "ホットスポットは溶接の止端の IIW の外挿の値 (記録の値)。",
        f"矢印は t = {lp.get('forces_at_t_s')} s の取付の力。点はその力を受け持つメッシュの節点 ({lp.get('mount_path')})。",
        "部品の箱は載っている節点のまわりのメッシュの節点の平均の変位で動く (箱は変形しない)。",
    ]
    return obj


def build(args) -> dict:
    return build_shell(args) if getattr(args, "frames", None) else build_beam(args)


def _extra(ap):
    ap.add_argument("--frames", help="folder with frames.npz, frames_meta.pkl and load_paths.json (the shell)")
    ap.add_argument("--name", help="the run's name on screen (the shell; default: the run folder's name)")
    ap.add_argument("--every", type=int, default=1, help="keep every n-th instant (the shell)")


if __name__ == "__main__":
    common.main("strength", build, extra=_extra, argv=sys.argv[1:])

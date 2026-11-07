"""Strength field data built from one record alone (viewer.src.build.for_analysis): two more models besides
strength.py's beam and saved-frames shell.

model "shell" with single = <text> (a frame_shell record: one instant, nothing saved): the same keys as strength.py's
    shell model (mesh, vm, disp, disp_scale, t, hot, el_max, vm_max, disp_max, mounts, joints, part_off) with T = 1:
    vm = the record's per_element von Mises (surface max: the larger of outer and inner, the wheel-mount patch elements
    included, as the record's label says); disp = its per_mesh_node displacement; the mesh = the record's mesh
    (meshes/<mesh_id>.json: nodes_mm, elements, element_member); hot = the record's largest structural hot spot
    (value from max_hot_spot_stress, joint and position from its effect line); joints = the joints the record names
    in its "largest hot spot at joint" effects, at the design node of that id; mounts = the record's reaction
    vectors at the wheel centres (no mesh nodes: the record does not keep how they are spread).
    member_plot [{member, value}]: the record's per-member shell von Mises (the field's per_member), largest first.

model "load_path" (a load_path record, R7 hand / foot, R8 inertia / push; no stress field in the record):
    load_kind; loaded [ids the record's load vectors are at]; free [part ids: results free_part.<id>];
    contacts [{a, b, p, path}]: the contacts the solver's own hiraku.solvers.shelf.load_path.find_contacts finds on
    the record's design (on_board with the case's loading, the record's touch), less those of free parts (and, for
    the push, of the openable parts the tool takes as open), path = on a path from a loaded part to a member
    (through parts only); members [ids reached by the path]; n_contacts_record (the record's contacts result);
    hot {member, stress, unit, utilisation, allowable} | null (results max_stress / max_utilisation)."""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path

import numpy as np

from viewer.src import common, ledgers, names, strength

HOT_MAX = re.compile(r"structural hot spot max [-\d.]+ MPa.*?joint (\S+) .*?at \[([-\d.]+), ([-\d.]+), ([-\d.]+)\] mm")
HOT_JOINT = re.compile(r"largest hot spot at joint (\S+) ")


def _field(rec: dict, kind: str, key: str, **match):
    return next((f for f in rec.get("fields", []) or [] if f.get("kind") == kind and key in f
                 and all(f.get(k) == v for k, v in match.items())), None)


# ---------------------------------------------------------------- frame_shell: one instant
def shell_record(run: Path, vdir: Path, rec: dict) -> dict | str:
    st = _field(rec, "stress", "per_element", surface="max") or _field(rec, "stress", "per_element")
    dp = _field(rec, "displacement", "per_mesh_node")
    if st is None or dp is None:
        return "記録に要素ごとの応力 (per_element) とメッシュの節点ごとの変位 (per_mesh_node) がない"
    mid = st.get("mesh_id")
    mf = Path(vdir) / "meshes" / f"{mid}.json"
    if not mf.is_file():
        return f"記録のメッシュ ({mid}) が run のフォルダにない"
    mesh = common.read_json(mf)
    X = np.asarray(mesh["nodes_mm"], float)
    E = np.asarray(mesh["elements"], np.int64)
    VM = np.asarray([v if v is not None else 0.0 for v in st["per_element"]], float)
    D = np.asarray(dp["per_mesh_node"], float)
    if len(VM) != len(E) or D.shape != X.shape:
        return f"記録の場の大きさがメッシュ ({mid}) と合わない"
    design = common.read_json(Path(vdir) / "design.json")
    lc = common.load_case_of(vdir, rec["load_case_id"])
    obj = common.base_object("strength", names.title("strength", "段差でのフレームのシェルの応力 (記録の 1 時刻)"),
                             run, vdir, rec, design)
    obj["model"] = "shell"
    member = [str(m) for m in mesh["element_member"]]
    members = sorted(set(member))
    idx = {s: i for i, s in enumerate(members)}
    obj["mesh"] = {"nodes": strength.pack_np(X, "f32"), "quads": strength.pack_np(E, "u32"),
                   "member": strength.pack_np([idx[s] for s in member], "u16"), "members": members,
                   "excluded": strength.pack_np(np.zeros(len(E)), "u8"), "element_size_mm": mesh.get("element_size_mm")}
    obj["vm"] = strength.pack_np(np.clip(np.rint(VM * 10.0), 0, 65535)[None], "u16")
    obj["disp"], obj["disp_scale"] = strength.encode_disp(D[None])
    obj["t"] = [0.0]
    k = int(np.argmax(VM))
    obj["el_max"] = [[float(VM[k]), k, member[k]]]
    obj["vm_max"] = float(VM.max())
    obj["disp_max"] = float(np.linalg.norm(D, axis=1).max())
    hs = common.result(rec, "max_hot_spot_stress")
    hs_where = next((r.get("where") for r in rec.get("results", []) if r["quantity"] == "max_hot_spot_stress"), None)
    m = next((HOT_MAX.search(str(e.get("name", ""))) for e in rec.get("effects", []) or []
              if HOT_MAX.search(str(e.get("name", "")))), None)
    obj["hot"] = [{"value": float(hs), "joint": m.group(1) if m else "", "member": hs_where or "",
                   "pos": [float(m.group(i)) for i in (2, 3, 4)]} if hs is not None and m else None]
    xyz = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    joints = [j.group(1) for e in rec.get("effects", []) or [] if (j := HOT_JOINT.match(str(e.get("name", ""))))]
    obj["joints"] = [{"name": j, "centre": [float(c) for c in xyz[j]]} for j in dict.fromkeys(joints) if j in xyz]
    empty = strength.pack_np(np.zeros((0, 3)), "f32")
    obj["mounts"] = [{"support": v["at"], "mount": "反力", "hp": v["o"], "F": v["f"], "how": "記録の輪の反力 (輪の中心)",
                      "n": 0, "xyz": empty, "f": empty} for v in obj["vectors"] if v["kind"] == "reaction"]
    obj["forces_at_t_s"] = None
    obj["mount_path"] = None
    obj["ledger"] = ledgers.shell_ledger(design, {"params": {}}, obj["parts"], set(members))
    obj["part_off"] = strength.pack_np(strength.part_offsets(obj["ledger"], design, X, D[None]), "f32")
    obj["marks"] = strength._yields(design)
    pm = _field(rec, "stress", "per_member")
    obj["member_plot"] = sorted(({"member": k2, "value": float(v)} for k2, v in (pm or {}).get("per_member", {}).items()
                                 if v is not None), key=lambda r: -r["value"])[:20]
    obj["single"] = "記録の 1 時刻 (段差に乗る瞬間の静的な力)。時系列は記録にない (段差を越える時間の動きは段差の計算が保存したフレームから見る)"
    ms = common.result(rec, "max_stress")
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"]) + [
        {"label": "mesh", "value": f"{len(X)} nodes, {len(E)} quads, element {mesh.get('element_size_mm')} mm",
         "source": f"the record's mesh {mid} (mesh.created)"},
        {"label": "colour", "value": st.get("label", ""), "source": f"record field stress per_element (surface {st.get('surface', '-')})"},
        {"label": "max_stress (取付の区画を除く)", "value": f"{ms:.1f} MPa" if ms is not None else "-", "source": "record result max_stress"},
        {"label": "hot spot", "value": f"{hs:.1f} MPa ({hs_where})" if hs is not None else "-",
         "source": "record result max_hot_spot_stress; joint and position from its effect"},
        {"label": "time", "value": "1 instant", "source": "the record holds one static instant; no time series"}]
    obj["notes"] = [
        "1 時刻だけ: frame_shell の記録は段差に乗る瞬間の静的な力で解いた 1 つの状態。時間の動き (時系列) は記録にない。",
        "色は要素の相当応力 (外面と内面の大きい方)。車輪の取付の区画の要素も含む (記録の max_stress はそれを除いた最大)。",
        "変形は輪のつなぎ目の剛体の動きを除いた変位 × 倍率 (実寸ではない)。",
        "紫の球は記録のいちばん大きい溶接の止端のホットスポット (IIW の外挿の値)。つなぎ目のボタンは記録が名前を出したつなぎ目 (設計の節の位置)。",
        "矢印は記録の輪の反力 (輪の中心)。メッシュのどの節に分けたかは記録にない。",
    ]
    return obj


# ---------------------------------------------------------------- load_path
def _touch(rec: dict) -> float:
    from hiraku.solvers.shelf.load_path import TOUCH_MM
    for a in rec.get("assumptions", []) or []:
        m = re.search(r"触れる許し ([\d.]+) mm", str(a))
        if m:
            return float(m.group(1))
    return TOUCH_MM


def load_path_record(run: Path, vdir: Path, rec: dict) -> dict | str:
    from hiraku.solvers.shelf import load_path as lpt
    from hiraku.solvers.shelf.base import on_board, param_text
    design0 = common.read_json(Path(vdir) / "design.json")
    lc = common.load_case_of(vdir, rec["load_case_id"])
    if lc is None:
        return f"記録の荷重の条件 ({rec['load_case_id']}) が run にない"
    kind = param_text(lc, "load_kind", "inertia")
    design = on_board(design0, lc)
    title = {"hand": "荷重の経路: 手で押す (R7)", "foot": "荷重の経路: 足で踏む (R7)", "inertia": "荷重の経路: 部品の慣性 (R8)",
             "push": "荷重の経路: 乗員が外へ押す (R8)"}.get(kind, "荷重の経路")
    obj = common.base_object("strength", names.title("strength", title), run, vdir, rec, design)
    obj["model"] = "load_path"
    obj["load_kind"] = kind
    free = [r.get("where") for r in rec.get("results", []) if r["quantity"].startswith("free_part.") and r.get("where")]
    opened = set(lpt.openable_parts(design)) if kind == "push" else set()
    out = {f"part:{p}" for p in free} | {f"part:{p}" for p in opened}
    contacts = [c for c in lpt.find_contacts(design, _touch(rec)) if c.a not in out and c.b not in out]
    part_ids = {p["id"] for p in design.get("parts", [])}
    member_ids = {m["id"] for m in design.get("members", [])}
    loaded = []
    for v in obj["vectors"]:
        at = v["at"]
        if at not in part_ids and "." in at:     # the push: <occupant>.<point R>; the body it lands on is not named
            at = None
        if at:
            loaded.append(at)
    loaded = list(dict.fromkeys(loaded))
    # the path: from each loaded part through the contacts between parts until a member
    adj: dict[str, list[int]] = {}
    for i, c in enumerate(contacts):
        adj.setdefault(c.a, []).append(i)
        adj.setdefault(c.b, []).append(i)
    on_path, reached = set(), set()
    for start in loaded:
        s = f"member:{start}" if start in member_ids else f"part:{start}"
        if s.startswith("member:"):
            reached.add(start)
            continue
        seen, q = {s}, deque([s])
        while q:
            u = q.popleft()
            for i in adj.get(u, []):
                c = contacts[i]
                w = c.b if c.a == u else c.a
                on_path.add(i)
                if w.startswith("member:"):
                    reached.add(w.split(":", 1)[1])
                elif w not in seen:
                    seen.add(w)
                    q.append(w)
    obj["contacts"] = [{"a": c.a, "b": c.b, "p": [round(float(x), 1) for x in c.point], "path": i in on_path}
                       for i, c in enumerate(contacts)]
    obj["loaded"] = loaded
    obj["free"] = free
    obj["members"] = sorted(reached)
    n_rec = common.result(rec, "contacts")
    obj["n_contacts_record"] = n_rec
    ms = next((r for r in rec.get("results", []) if r["quantity"] == "max_stress"), None)
    mu = next((r for r in rec.get("results", []) if r["quantity"] == "max_utilisation"), None)
    al = common.result(rec, "allowable_stress")
    obj["hot"] = ({"member": ms.get("where"), "stress": float(ms["value"]), "unit": ms.get("unit", "MPa"),
                   "util_member": mu.get("where") if mu else None, "utilisation": float(mu["value"]) if mu else None,
                   "allowable": al} if ms else None)
    obj["effects"] = [str(e.get("name", "")) for e in rec.get("effects", []) or []]
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"]) + [
        {"label": "loads", "value": f"{len(obj['vectors'])} arrows at " + (", ".join(loaded) or "-"),
         "source": "record vectors (kind load; the push's at = occupant.point R)"},
        {"label": "free parts", "value": ", ".join(free) or "none", "source": "record results free_part.<part>"},
        {"label": "contacts", "value": f"{len(contacts)} here / {n_rec:.0f} in the record" if n_rec is not None else f"{len(contacts)} here",
         "source": "hiraku.solvers.shelf.load_path.find_contacts on the record's design (the tool's own function), "
                   "less the free parts" + (" and the openable parts (open for the push)" if kind == "push" else "")},
        {"label": "max_stress", "value": f"{ms['value']:.1f} MPa ({ms.get('where')})" if ms else "-", "source": "record result max_stress"},
        {"label": "max_utilisation", "value": f"{mu['value']:.2f} ({mu.get('where')})" if mu else "-",
         "source": "record result max_utilisation (stress / (yield / safety factor))"}]
    obj["notes"] = [
        "荷重の経路: 矢印 (橙) は記録の荷重。部品は箱、部品と部品・部材が触れている所 (ばね) は点。荷重の掛かる部品から部材まで、"
        "触れている所を部品だけ伝って届く経路の点と線を濃く、部材に届いた所の部材を青で示す。",
        "触れている所は記録には数だけがある。ここでは道具の関数 (find_contacts) を記録の設計にもう一度掛けて求めた (解くのではない)。",
        "赤い箱はどこにも繋がらない部品 (記録の free_part)。荷重はそこで止まり、枠まで届かない。",
        "記録に部材ごとの応力はない。応力がいちばん大きい部材 (max_stress) と許しにいちばん近い部材 (max_utilisation) だけ色を付けた。",
    ]
    return obj

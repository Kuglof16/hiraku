"""Shared parts of the viewer's data scripts: reading a run folder, packing arrays, and the
design's frame (beam lines) and part boxes.

A run folder holds v001/, v002/, ... each with design.json and analyses/ana-*.json
(and optionally meshes/). A version folder (with analyses/ in it) is accepted too.

Data written for the page (viewer/data/<field>.js):

    window.VIEW_DATA = window.VIEW_DATA || {};
    window.VIEW_DATA["<field>"] = {...};

Every field's object has these keys (the page's shared parts read them):
    field, title, source {run (the run folder's name, never a path), version, analysis_id, tool, load_case_id,
           design_name, design_version}
    frame {node_ids, nodes (packed f32, n x 3, mm), members [[ia, ib], ...], member_ids,
           member_names, radius_mm}
    parts [{id, name, kind, module, c, s, r}]   (box centre, size, rpy_deg; mm and deg)
    scene [{kind, label, c, s}]                 (boxes from the record's scene)
    vectors [{kind, at, o, f}]                  (origin mm, force N)
    inputs [{label, value, source}]             (the case's inputs and where they come from)
    notes [text]                                (the longer explanations; the page keeps them in a closed panel)
    ledger {...}                                (parts_ledger: how each part enters the calculation)
    tyre {label, value, source} | null          (tyre_row: the design's tyre_stiffness, for the tools that use it)
plus the field's own keys (see each script).

Packed arrays: {"dtype": "f32", "shape": [...], "b64": base64(gzip(little-endian bytes))}; "i16" (with "scale":
value = int * scale) and "i32" the same way (pack_i16, pack_i32). source.design_key: design_key(design).
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import re
import struct
from pathlib import Path
from typing import Any, Callable, Iterable

Record = dict[str, Any]

VIEWER = Path(__file__).resolve().parents[1]
DEFAULT_OUT = VIEWER / "data"


# ---------------------------------------------------------------- run folders

def version_dirs(run: Path) -> list[Path]:
    """The version folders of a run, in order. A version folder given directly is returned alone."""
    run = Path(run)
    if (run / "analyses").is_dir():
        return [run]
    vs = sorted(p for p in run.iterdir() if p.is_dir() and re.fullmatch(r"v\d+", p.name))
    if not vs:
        raise FileNotFoundError(f"{run}: no v### folders and no analyses/ folder")
    return vs


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def analyses(vdir: Path) -> list[Record]:
    """The analysis records of one version, in file order, each with _path set."""
    out = []
    for f in sorted((Path(vdir) / "analyses").glob("ana-*.json")):
        a = read_json(f)
        a["_path"] = str(f)
        out.append(a)
    return out


def find_analysis(run: Path, tool: str, load_case: str | None = None, version: str | None = None,
                  analysis_id: str | None = None, ok_only: bool = True) -> tuple[Path, Record]:
    """The newest record of `tool` (the last version that has one, the last file in it), optionally
    narrowed to a load case prefix, a version folder name (v003) or an analysis id."""
    found = None
    for vd in version_dirs(run):
        if version and vd.name != version:
            continue
        for a in analyses(vd):
            if a.get("tool") != tool:
                continue
            if load_case and not str(a.get("load_case_id", "")).startswith(load_case):
                continue
            if analysis_id and a.get("analysis_id") != analysis_id:
                continue
            if ok_only and a.get("status") != "ok":
                continue
            found = (vd, a)
    if found is None:
        raise LookupError(f"{run}: no {'ok ' if ok_only else ''}record of tool {tool}"
                          + (f", load case {load_case}" if load_case else "")
                          + (f", version {version}" if version else "")
                          + (f", analysis {analysis_id}" if analysis_id else ""))
    return found


def load_case_of(vdir: Path, load_case_id: str) -> Record | None:
    """The load case as the run stored it: project.json's load_cases (run folder above the version),
    or load_case.json in the version folder (a run made for the viewer)."""
    vdir = Path(vdir)
    lc_file = vdir / "load_case.json"
    if lc_file.is_file():
        lc = read_json(lc_file)
        if lc.get("load_case_id") == load_case_id:
            return lc
    proj = vdir.parent / "project.json"
    if proj.is_file():
        for lc in read_json(proj).get("load_cases", []):
            if lc.get("load_case_id") == load_case_id:
                return lc
    return None


# ---------------------------------------------------------------- packing

def pack_f32(values: Iterable[float], shape: list[int] | None = None) -> Record:
    vals = [float(v) for v in values]
    raw = struct.pack(f"<{len(vals)}f", *vals)
    z = gzip.compress(raw, compresslevel=9, mtime=0)
    return {"dtype": "f32", "shape": shape or [len(vals)], "b64": base64.b64encode(z).decode("ascii")}


def unpack_f32(p: Record) -> list[float]:
    raw = gzip.decompress(base64.b64decode(p["b64"]))
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def _pack_raw(raw: bytes, dtype: str, shape: list[int], **extra) -> Record:
    z = gzip.compress(raw, compresslevel=9, mtime=0)
    return {"dtype": dtype, "shape": shape, "b64": base64.b64encode(z).decode("ascii"), **extra}


def pack_i16(values: Iterable[float], scale: float, shape: list[int] | None = None) -> Record:
    """Values as int16 steps of `scale` (value = int * scale), clipped to +-32767 steps: for values of a known range
    (relative shapes and fields, |v| <= 32767 scale). The page multiplies back to float32."""
    q = [max(-32767, min(32767, int(round(float(v) / scale)))) for v in values]
    return _pack_raw(struct.pack(f"<{len(q)}h", *q), "i16", shape or [len(q)], scale=scale)


def pack_i32(values: Iterable[int], shape: list[int] | None = None) -> Record:
    """Integers (indices) as int32."""
    q = [int(v) for v in values]
    return _pack_raw(struct.pack(f"<{len(q)}i", *q), "i32", shape or [len(q)])


def unpack(p: Record) -> list[float] | list[int]:
    """Any packed array (f32, i16 times its scale, i32)."""
    raw = gzip.decompress(base64.b64decode(p["b64"]))
    dt = p.get("dtype", "f32")
    if dt == "i16":
        return [v * p["scale"] for v in struct.unpack(f"<{len(raw) // 2}h", raw)]
    if dt == "i32":
        return list(struct.unpack(f"<{len(raw) // 4}i", raw))
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def design_key(design: Record) -> str:
    """A short fingerprint of what the solvers read (nodes, members, sections, materials, parts, supports), so the
    page can tell whether two records are of the same design."""
    import hashlib
    body = json.dumps([design.get(k) for k in ("nodes", "members", "sections", "materials", "parts", "supports")],
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]


def data_js(field: str, obj: Record) -> str:
    body = json.dumps(obj, ensure_ascii=True, separators=(",", ":"))
    return ("window.VIEW_DATA = window.VIEW_DATA || {};\n"
            f"window.VIEW_DATA[{json.dumps(field)}] = {body};\n")


def read_data_js(path: Path) -> tuple[str, Record]:
    """The field name and object of a data file (for tests)."""
    text = Path(path).read_text(encoding="ascii")
    m = re.search(r'window\.VIEW_DATA\[("[^"]+")\] = (.*);\s*$', text, re.S)
    if not m:
        raise ValueError(f"{path}: not a viewer data file")
    return json.loads(m.group(1)), json.loads(m.group(2))


def write_data(field: str, obj: Record, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{field}.js"
    from viewer.src import ja   # the page's texts in Japanese (imported here: ja imports this module)
    obj = ja.translate(obj)
    path.write_text(data_js(field, obj), encoding="ascii")
    return path


# ---------------------------------------------------------------- design geometry

def _radius(section: Record | None) -> float:
    """Half the larger outer size of a tube section (mm), for drawing; 15 mm if unknown."""
    if not section:
        return 15.0
    d = section.get("dims_mm", {})
    if "d" in d:
        return float(d["d"]) / 2.0
    if "b" in d or "h" in d:
        return max(float(d.get("b", 0.0)), float(d.get("h", 0.0))) / 2.0
    return 15.0


def frame_of(design: Record) -> Record:
    """Beam lines of the design's frame: its nodes and members (members whose nodes exist)."""
    ids = [n["id"] for n in design.get("nodes", [])]
    xyz = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    index = {nid: i for i, nid in enumerate(ids)}
    sections = design.get("sections", {})
    members, mids, names, radius = [], [], [], []
    for m in design.get("members", []):
        a, b = m.get("node_a"), m.get("node_b")
        if a not in index or b not in index:
            continue
        members.append([index[a], index[b]])
        mids.append(m["id"])
        names.append(m.get("name", m["id"]))
        radius.append(_radius(sections.get(m.get("section"))))
    flat = [c for nid in ids for c in xyz[nid]]
    return {"node_ids": ids, "nodes": pack_f32(flat, [len(ids), 3]), "members": members,
            "member_ids": mids, "member_names": names, "radius_mm": radius}


def parts_of(design: Record) -> list[Record]:
    """Part boxes: centre, size and rpy_deg (the design's box; rpy as the product's screen turns it,
    three.js Euler order ZYX)."""
    out = []
    for p in design.get("parts", []):
        box = p.get("box")
        if not box:
            continue
        out.append({"id": p["id"], "name": p.get("name", p["id"]), "kind": p.get("kind", ""),
                    "module": p.get("module", ""), "c": [float(v) for v in box["center_mm"]],
                    "s": [float(v) for v in box["size_mm"]],
                    "r": [float(v) for v in box.get("rpy_deg", [0.0, 0.0, 0.0])]})
    return out


def scene_of(record: Record) -> list[Record]:
    out = []
    for s in record.get("scene", []) or []:
        if "position_mm" in s and "size_mm" in s:
            out.append({"kind": s.get("kind", ""), "label": s.get("label", s.get("kind", "")),
                        "c": [float(v) for v in s["position_mm"]], "s": [float(v) for v in s["size_mm"]]})
    return out


def ground_of(design: Record) -> Record | None:
    """The ground under the tyres at the static ride height, from the design's supports: each support's tyre
    touches at its wheel centre z less its radius (or its loaded radius, `loaded_radius_mm`, when the design gives
    one); the ground plane is the lowest of those, and each support's own gap to it is kept (0 when the supports
    stand level). None without supports."""
    out = []
    for s in design.get("supports", []) or []:
        p, r = s.get("position_mm"), s.get("loaded_radius_mm", s.get("radius_mm"))
        if not p or r is None:
            continue
        out.append({"id": s.get("id", ""), "c": [float(v) for v in p], "r": float(r),
                    "width": float(s.get("width_mm", 0.0) or 0.0), "contact_z": float(p[2]) - float(r),
                    "loaded": "loaded_radius_mm" in s})
    if not out:
        return None
    z = min(w["contact_z"] for w in out)
    for w in out:
        w["gap_mm"] = w["contact_z"] - z
    return {"z": z, "wheels": out,
            "from": "設計の支持点: 車輪の中心の高さ − " + ("負荷時の半径" if any(w["loaded"] for w in out) else "半径")}


def vectors_of(record: Record) -> list[Record]:
    return [{"kind": v.get("kind", ""), "at": v.get("at", ""), "o": [float(x) for x in v["origin_mm"]],
             "f": [float(x) for x in v["vector_N"]]} for v in record.get("vectors", []) or []]


def results_of(record: Record) -> list[Record]:
    return [{"q": r["quantity"], "v": r["value"], "u": r.get("unit", ""), "where": r.get("where", "")}
            for r in record.get("results", [])]


def result(record: Record, quantity: str) -> float | None:
    for r in record.get("results", []):
        if r["quantity"] == quantity:
            return float(r["value"])
    return None


def series(record: Record, channel: str) -> Record | None:
    for s in record.get("series", []) or []:
        if s.get("channel") == channel:
            return s
    return None


def qty(q: Any) -> str:
    """A {"value", "unit"} as text."""
    if isinstance(q, dict) and "value" in q:
        return f"{q['value']:g} {q.get('unit', '')}".strip()
    return str(q)


def run_name(run: Path, vdir: Path) -> str:
    """The run's name for the screen: the run folder's last name (never a path). A version folder given directly:
    the name of the folder above it."""
    run, vdir = Path(run).resolve(), Path(vdir).resolve()
    return (vdir.parent.name if run == vdir else run.name) or vdir.name


def base_object(field: str, title: str, run: Path, vdir: Path, record: Record, design: Record) -> Record:
    return {
        "field": field,
        "title": title,
        "source": {"run": run_name(run, vdir), "version": Path(vdir).name,
                   "analysis_id": record.get("analysis_id", ""), "tool": record.get("tool", ""),
                   "load_case_id": record.get("load_case_id", ""), "design_name": design.get("name", ""),
                   "design_version": design.get("design_version"), "design_key": design_key(design)},
        "frame": frame_of(design),
        "parts": parts_of(design),
        "scene": scene_of(record),
        "ground": ground_of(design),
        "vectors": vectors_of(record),
        "results": results_of(record),
        "assumptions": list(record.get("assumptions", []) or []),
        "inputs": [],
        "notes": [],
        "tyre": tyre_row(design, record.get("tool", "")),
    }


# The tools whose calculation uses design.variables.tyre_stiffness (hiraku/solvers: k2_vehicle.model and its shelf
# tools, crash_beam's wheel at the wall, shelf.fatigue's quarter cars). frame_shell's step hit uses it through the
# K2 run that gave its mount forces. K1 modal and frame_static stand on the suspension springs (k1_frame.model
# restrain_on_wheels), K3 and K4 have no tyre.
TYRE_TOOLS = {"crash_beam": "K2 の壁との接触 (タイヤの半径の向き)", "tilt": "K2 の車両モデル", "braking": "K2 の車両モデル",
              "lane_change": "K2 の車両モデル", "offroad_roll": "K2 の車両モデル", "launch": "K2 の車両モデル",
              "step_climb_dynamic": "K2 の車両モデル", "crosswind": "K2 の車両モデル", "turning": "K2 の車両モデル",
              "rollover_tilt": "K2 の車両モデル",
              "fatigue": "疲労の 1/4 車 (路面からの輪の力)", "frame_shell": "取付の力を出した K2 の段差の計算"}
NUM_JA = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def tyre_basis_ja(basis: str | None) -> str:
    """The design's basis for tyre_stiffness in short Japanese ('3 つの資料からの判断 45〜90 の中ほど')."""
    if not basis:
        return "設計の値 (出典なし)"
    n = re.search(r"from (\w+) sources", basis)
    rng = re.search(r"range ([\d.]+) to ([\d.]+) N/mm", basis)
    k = NUM_JA.get(n.group(1), n.group(1)) if n else None
    out = (f"{k} つの資料からの判断" if k else "設計の出典 (basis) による")
    if rng:
        out += f" {float(rng.group(1)):g}〜{float(rng.group(2)):g} の中ほど"
    return out if n or rng else "設計の出典 (basis) あり: " + basis[:60]


def tyre_row(design: Record, tool: str) -> Record | None:
    """The input row of the tyre stiffness, read from the record's design, for a tool that uses it; else None."""
    if tool not in TYRE_TOOLS:
        return None
    v = (design.get("variables") or {}).get("tyre_stiffness")
    if not isinstance(v, dict) or v.get("value") is None:
        return {"label": "タイヤの剛さ", "value": "設計にない (ツールの既定)", "source": TYRE_TOOLS[tool]}
    return {"label": "タイヤの剛さ", "value": f"{float(v['value']):g} {v.get('unit', 'N/mm')}",
            "source": f"設計の tyre_stiffness: {tyre_basis_ja(v.get('basis'))}。{TYRE_TOOLS[tool]}で使う"}


def case_inputs(lc: Record | None, load_case_id: str) -> list[Record]:
    """The load case's title and params as input rows."""
    if lc is None:
        return [{"label": "試験条件", "value": load_case_id, "source": "実行の記録にこの条件がない"}]
    rows = [{"label": "試験条件", "value": f"{lc['load_case_id']}: {lc.get('title', '')}",
             "source": f"試験 {lc.get('test_id', '')}、カタログで作成"}]
    for k, v in (lc.get("params") or {}).items():
        rows.append({"label": PARAM_JA.get(k, k), "value": LOADING_VALUE_JA.get(v, qty(v)) if isinstance(v, str) else qty(v),
                     "source": "試験条件の値"})
    crit = lc.get("criterion")
    if crit:
        rows.append({"label": "判定基準", "value": f"{crit['quantity']} {crit['op']} {qty(crit['limit'])}",
                     "source": crit.get("basis", "")})
    return rows


PARAM_JA = {   # load-case param names on screen
    "speed": "速さ", "loading": "乗員・荷物", "mu": "摩擦係数", "wind_speed": "風速", "wind_from": "風の向き",
    "drift_time": "流れを見る時間", "target_speed": "目標の速さ", "step_height": "段差の高さ", "side_slope": "横の傾き",
    "length": "走る長さ", "parking_slope": "止める坂", "grade": "坂", "wall_mu": "壁の摩擦", "distance": "距離",
    "soc_start": "電池の残り (始め)", "find_limit": "限界を探す", "ramp_time": "傾ける時間", "road_class": "路面の粗さ",
    "brake_rise_time": "ブレーキの立ち上がり", "cargo_cg_above_deck": "荷の重心の高さ", "hinge_break_angle": "折れる角度",
    "direction": "傾ける向き", "reaction_time": "反応の時間", "gust_rise": "突風の立ち上がり", "grade_low": "坂 (緩)", "grade_high": "坂 (急)", "speed_low": "速さ (遅)", "speed_high": "速さ (速)",
}
LOADING_VALUE_JA = {"empty": "空車", "driver": "運転者だけ", "two": "2 人",   # = hiraku.contract.loading.LOADING_JA
                    "two_cargo": "2 人 + 荷", "left": "左", "right": "右", "both": "両方"}


# ---------------------------------------------------------------- parts in the calculation
# How each part of the design enters a tool's calculation, worked out by calling the tool's own functions
# (hiraku.contract.loading.on_board, hiraku.solvers.k0_handcalc.vehicle.mass_items) and following the rule of
# the function named in each entry. Nothing here is a guess: where a rule is copied (crash_beam is on another
# branch and cannot be imported), the function it copies is named.

CATEGORIES = {   # key -> the short Japanese label on screen (the English meaning in the comment)
    "frame_nodes": "フレームの節点に質量",          # mass on frame nodes
    "unsprung": "ばね下 (支持点に質量)",            # unsprung, mass at the support
    "stiffness": "剛性ありでモデル化",              # meshed / modelled with stiffness
    "mass_only": "質量だけ (剛性なし)",            # mass only, no stiffness
    "body": "形だけ (質量なし)",                    # a body, shape only, no mass
    "not_in": "この計算に入っていない",             # not in this calculation
    "sprung": "ばね上 (車体と一緒の剛体)",          # K2: in the one rigid sprung body
    "half": "半分ばね下・半分ばね上",               # K2: suspension kinds, half with the wheel
}

FN_ON_BOARD = "hiraku.contract.loading.parts_on_board"
FN_MASS_ITEMS = "hiraku.solvers.k0_handcalc.vehicle.mass_items"
FN_MODAL = "hiraku.solvers.k1_frame.modal.add_masses"
FN_CRASH_A = "hiraku.solvers.shelf.crash.CrashTool.analyse (mass_summary -> plastic_impact; crash_frame at the peak)"
FN_CRASH_B = "hiraku.solvers.k2_vehicle.crash_beam.mesh._lump_parts (branch claude/k2-crash-beam-b)"
FN_ASSEMBLY = "hiraku.solvers.k4_geometry.model.bodies, assembly.steps"
FN_SHAFT = "hiraku.solvers.k1_frame.shaft.driveshaft_modes"
FN_K2 = "hiraku.solvers.k2_vehicle.model.build_vehicle (_unsprung: UNSPRUNG_KINDS / HALF_UNSPRUNG_KINDS)"
FN_K3 = "hiraku.solvers.k3_power_thermal.from_design (one mass)"
K2_TOOLS = ("tilt", "braking", "lane_change", "offroad_roll", "crosswind", "launch", "step_climb_dynamic", "rollover_tilt")


def _stated_b(record: Record) -> Record | None:
    """crash_beam states its mass in an assumption: 'mass M kg: frame F kg on its element nodes, parts P kg; the
    parts without carried_by ... : RL 24.7 kg at LB, ...'."""
    for a in record.get("assumptions", []) or []:
        m = re.match(r"mass ([\d.]+) kg: frame ([\d.]+) kg[^,]*, parts ([\d.]+) kg", a)
        if m:
            sup = {s: (float(kg), node) for s, kg, node in re.findall(r"(\w+) ([\d.]+) kg at (\S+?)[,;]", a + ";")}
            return {"total": float(m.group(1)), "frame": float(m.group(2)), "parts": float(m.group(3)),
                    "unsprung": sup, "text": a}
    return None


def parts_ledger(tool: str, design: Record, load_case: Record | None, record: Record, parts: list[Record]) -> Record:
    """Per part of `parts` (parts_of(design), same order): its category in `tool`'s calculation, its mass, the
    nodes it sits on with kg each, the deciding function and a sentence; the frame members' mass; the mass table
    per category against the record's own stated mass; the parts with no stiffness and those not in the
    calculation. tool: modal, crash, crash_beam or assembly."""
    from hiraku.contract import loading as contract_loading
    from hiraku.contract.protocols import DEFAULT_LOADING

    lc_loading = ((load_case or {}).get("params") or {}).get("loading")
    loading = lc_loading or DEFAULT_LOADING
    loading_src = ("load case params.loading" if lc_loading else
                   f"default {DEFAULT_LOADING} (hiraku.contract.loading.loading_of: the load case gives none"
                   + ("" if load_case else "; the run does not hold this load case") + ")")
    out: Record = {"tool": tool, "loading": loading, "loading_source": loading_src, "categories": CATEGORIES,
                   "parts": [], "members": None, "table": [], "stated": [], "error": None, "notes": []}
    dparts = {p["id"]: p for p in design.get("parts", [])}
    try:
        on_ids = {p["id"] for p in contract_loading.parts_on_board(design, loading)}
    except ValueError as e:
        out["error"] = f"{FN_ON_BOARD}: {e}"
        on_ids = set(dparts)
    on = {**design, "parts": [p for p in design.get("parts", []) if p["id"] in on_ids]}
    items, frame_kg = {}, None
    if tool != "assembly":
        try:
            from hiraku.solvers.k0_handcalc.vehicle import mass_items
            its = mass_items(on)
            items = {it.id.split(":", 1)[1]: it for it in its if it.carrier != "member"}
            frame_kg = sum(it.mass_kg for it in its if it.carrier == "member")
        except Exception as e:  # the design as stored cannot be read by the tool's own function
            out["error"] = f"{FN_MASS_ITEMS} could not read this design: {e}"
    nodes_xyz = {n["id"]: [float(c) for c in n["xyz_mm"]] for n in design.get("nodes", [])}
    used = sorted({m[k] for m in design.get("members", []) for k in ("node_a", "node_b")} & set(nodes_xyz))
    supports = {s["id"]: s for s in design.get("supports", [])}

    def nearest_used(xyz):
        return min(used, key=lambda n: sum((a - b) ** 2 for a, b in zip(nodes_xyz[n], xyz))) if used else None

    steps, groups = {}, {}
    for s in record.get("assembly", []) or []:
        steps[s.get("part")] = s.get("order")
        for g in s.get("group_parts", []) or []:
            groups[g] = s.get("part")
    shaft_ids = set()
    if tool == "modal":
        try:
            from hiraku.solvers.k1_frame.shaft import shaft_inputs
            shaft_ids = set(shaft_inputs(on))
        except Exception as e:
            out["notes"].append(f"{FN_SHAFT}: not reproduced ({e})")
    k2 = k2_vehicle(design, loading) if tool in K2_TOOLS else None
    for p in parts:
        pid, src = p["id"], dparts.get(p["id"], {})
        mass = float(src.get("mass_kg", 0.0) or 0.0)
        e: Record = {"id": pid, "kind": p.get("kind", ""), "mass_kg": mass, "cat": "not_in", "nodes": [], "fn": "",
                     "how": "", "stiffness": False}
        if pid not in on_ids:
            e.update(fn=FN_ON_BOARD, how=f"left off by the loading {loading} (occupants board in design order; cargo only "
                                         f"with two_cargo): not in the design the tool is given")
            out["parts"].append(e)
            continue
        if k2 is not None or tool == "drive_energy":
            e.update(k2_entry(src, mass, k2))
            out["parts"].append(e)
            continue
        if tool == "assembly":
            if pid in steps:
                how = f"a body (its box as an OBB / capsule) with its own step {steps[pid]} in the sequence"
            elif pid in groups:
                how = f"a body in the corner group put on at step of {groups[pid]}"
            else:
                how = "a body in the interference checks; no step of its own in this record"
            carried = list(src.get("carried_by") or [])
            e.update(cat="body", fn=FN_ASSEMBLY, how=how + "; masses are not used by the assembly tool",
                     nodes=[[n, None] for n in carried])
            out["parts"].append(e)
            continue
        it = items.get(pid)
        if it is None:
            e.update(cat="not_in", fn=FN_MASS_ITEMS, how="not reproduced: " + (out["error"] or "no mass item"))
            out["parts"].append(e)
            continue
        if it.carrier == "nodes":
            if tool == "crash_beam":
                share: dict[str, float] = {}
                moved = []
                for n in it.carried_by:
                    k = n if n in used else nearest_used(nodes_xyz[n])
                    if k != n:
                        moved.append(f"{n} -> {k}")
                    share[k] = share.get(k, 0.0) + it.mass_kg / len(it.carried_by)
                nodes = [[k, v] for k, v in share.items()]
                how = ("point masses in equal shares on its carried_by nodes; the part's own rotational inertia and its "
                       "stiffness are not in the model, it has no contact with the wall (only frame nodes touch it)"
                       + (f"; carried_by nodes no member uses moved to the nearest frame node: {', '.join(moved)}" if moved else ""))
                e.update(cat="frame_nodes", fn=FN_CRASH_B, how=how, nodes=nodes)
            elif tool == "crash":
                nodes = [[n, it.mass_kg / len(it.carried_by)] for n in it.carried_by]
                e.update(cat="mass_only", fn=FN_CRASH_A, nodes=nodes,
                         how="in the one rigid mass of the 1-D model (mass_summary), no stiffness, does not crush; in K1 at "
                             "the peak its d'Alembert force (and moment) is shared equally by its carried_by nodes "
                             "(hiraku.solvers.k1_frame.model inertia loads)")
            else:
                nodes = [[n, it.mass_kg / len(it.carried_by)] for n in it.carried_by]
                e.update(cat="frame_nodes", fn=FN_MODAL, nodes=nodes,
                         how="lumped translational mass, equal shares on its carried_by frame nodes (node loads in the "
                             "mass case); no stiffness, no rotational inertia")
        else:  # support: no carried_by, rests on the nearest support (K0's rule)
            sid = it.carried_by[0]
            s = supports.get(sid, {})
            fn_node = s.get("frame_node")
            if tool == "crash_beam":
                k = fn_node if fn_node in used else nearest_used([float(c) for c in s.get("position_mm", [0, 0, 0])])
                e.update(cat="unsprung", fn=FN_CRASH_B, nodes=[[k, it.mass_kg]],
                         how=f"no carried_by: a point mass at support {sid}'s frame node {k} (the nearest support); no "
                             f"stiffness, no wheel or tyre model, no contact with the wall")
            else:
                pos = [float(c) for c in s.get("position_mm", [0, 0, 0])]
                at_frame = fn_node in nodes_xyz and sum((a - b) ** 2 for a, b in zip(nodes_xyz[fn_node], pos)) < 1e-12
                node = fn_node if at_frame else f"wc:{sid}"
                where = (f"the wheel centre node {node} (hiraku.solvers.k1_frame.model.wheel_node), joined to frame node "
                         f"{fn_node} by a stiff link member" if not at_frame else f"frame node {node} (the support is there)")
                if tool == "crash":
                    e.update(cat="mass_only", fn=FN_CRASH_A, nodes=[[node, it.mass_kg]],
                             how=f"no carried_by (unsprung, nearest support {sid}): in the one rigid mass of the 1-D model, "
                                 f"no stiffness; in K1 at the peak its force is at {where}")
                else:
                    e.update(cat="unsprung", fn=FN_MODAL, nodes=[[node, it.mass_kg]],
                             how=f"no carried_by (nearest support {sid}): lumped mass at {where}; the support's spring "
                                 f"(if any) runs from that node to the ground; no stiffness of its own")
        if pid in shaft_ids:
            e["how"] += f"; also its own shaft beam with stiffness for driveshaft_bending/torsion_mode ({FN_SHAFT})"
        out["parts"].append(e)

    # the frame members
    if k2 is not None or tool == "drive_energy":
        out["members"] = {"count": len(design.get("members", [])), "mass_kg": frame_kg, "fn": FN_K2 if k2 else FN_K3,
                          "how": ("細い棒として重さだけ車体 (ばね上) に入る。剛性はなく、フレームはたわまない"
                                  if k2 else "車両の一つの質量に入る。剛性はない")}
    elif tool == "assembly":
        out["members"] = {"count": len(design.get("members", [])), "mass_kg": None, "fn": FN_ASSEMBLY,
                          "how": "each member is a body (its tube as an OBB) for the interference checks; no mass"}
    else:
        how = {"modal": "beams with stiffness (PyNite, subdivided); consistent mass from density x A x L "
                        "(add_member_self_weight in the mass case)",
               "crash": "in the one rigid mass; the crush zone's rails fold at their mean crushing force (the only "
                        "stiffness of the 1-D model besides the bumper spring from design.variables); K1 at the peak "
                        "uses every member's stiffness",
               "crash_beam": "co-rotational beam elements with plastic hinges (stiffness, yield, break); each element's "
                             "own mass half to each of its two nodes"}[tool]
        fn = {"modal": FN_MODAL, "crash": FN_CRASH_A, "crash_beam": "hiraku.solvers.k2_vehicle.crash_beam.mesh.build_mesh"}[tool]
        m: Record = {"count": len(design.get("members", [])), "mass_kg": frame_kg, "fn": fn, "how": how}
        if tool == "crash":
            try:
                from hiraku.solvers.k0_handcalc.library_crash import crush_zone
                m["crush_members"] = crush_zone(on)["members"]
            except Exception as ex:
                m["crush_members"] = None
                out["notes"].append(f"hiraku.solvers.k0_handcalc.library_crash.crush_zone: not reproduced ({ex})")
        out["members"] = m

    # the mass table
    cats = {}
    for e in out["parts"]:
        c = cats.setdefault(e["cat"], {"cat": e["cat"], "label": CATEGORIES[e["cat"]], "count": 0, "mass_kg": 0.0})
        c["count"] += 1
        c["mass_kg"] += e["mass_kg"]
    out["table"] = [cats[k] for k in CATEGORIES if k in cats]
    in_calc = sum(e["mass_kg"] for e in out["parts"] if e["cat"] not in ("not_in", "body"))
    out["parts_in_kg"] = in_calc if tool != "assembly" else None
    out["total_kg"] = (in_calc + frame_kg) if (frame_kg is not None and tool != "assembly") else None
    stated = []
    if tool == "crash":
        for q, mine in (("vehicle_mass", out["total_kg"]), ("frame_mass", frame_kg)):
            v = result(record, q)
            if v is not None:
                stated.append({"label": q, "stated_kg": v, "computed_kg": mine, "tol_kg": 1e-6 * max(1.0, v),
                               "source": f"record result {q}"})
        if not stated:
            out["notes"].append("the crash record states no vehicle_mass")
    elif tool == "crash_beam":
        sb = _stated_b(record)
        if sb:
            stated += [{"label": "mass (total)", "stated_kg": sb["total"], "computed_kg": out["total_kg"], "tol_kg": 0.051,
                        "source": "record assumption 'mass ... kg' (1 decimal)"},
                       {"label": "frame", "stated_kg": sb["frame"], "computed_kg": frame_kg, "tol_kg": 0.051,
                        "source": "same assumption"},
                       {"label": "parts", "stated_kg": sb["parts"], "computed_kg": in_calc, "tol_kg": 0.051,
                        "source": "same assumption"}]
            for sid, (kg, node) in sb["unsprung"].items():
                mine = sum(e["mass_kg"] for e in out["parts"] if e["cat"] == "unsprung" and e["nodes"]
                           and e["nodes"][0][0] == node)
                stated.append({"label": f"unsprung at {sid} ({node})", "stated_kg": kg, "computed_kg": mine,
                               "tol_kg": 0.051, "source": "same assumption, per support"})
        else:
            out["notes"].append("the crash_beam record states no mass assumption")
    elif tool == "drive_energy":
        v = result(record, "vehicle_mass")
        if v is not None:
            stated.append({"label": "vehicle_mass", "stated_kg": v, "computed_kg": out["total_kg"], "tol_kg": 0.05,
                           "source": "record result vehicle_mass"})
    elif k2 is not None:
        out["notes"].append("K2 の記録には質量の値がない (照合なし)")
    elif tool == "modal":
        out["notes"].append(f"the modal record states no mass; the parts on board follow the load case's loading "
                            f"({loading}, {loading_src})")
    else:
        out["notes"].append("the assembly tool uses no masses; every part on board is a body")
    for s in stated:
        s["diff_kg"] = None if s["computed_kg"] is None else s["computed_kg"] - s["stated_kg"]
        s["agrees"] = s["diff_kg"] is not None and abs(s["diff_kg"]) <= s["tol_kg"]
        if not s["agrees"]:
            s["why"] = ("not computed: " + (out["error"] or "") if s["diff_kg"] is None else
                        "the design.json read here is not the design the tool weighed (a trimmed or edited copy), or "
                        "the loading differs; computed from this version's design.json with loading " + loading)
    out["stated"] = stated
    out["no_stiffness"] = [e["id"] for e in out["parts"] if e["cat"] not in ("not_in", "body") and not e["stiffness"]]
    out["not_in"] = [{"id": e["id"], "why": e["how"]} for e in out["parts"] if e["cat"] == "not_in"]
    return out


# ---------------------------------------------------------------- parts moving with their nodes
# A part has no degrees of freedom of its own in these records: it moves with the nodes it sits on.

def rigid_fit(points: list[list[float]], disp: list[list[float]]) -> tuple[list[float], list[float], list[float]]:
    """The rigid motion that best fits the displacements `disp` of `points` (least squares, small rotation):
    u_i ~ t + w x (p_i - c). Returns (c, t, w): c the points' centroid, t the mean displacement, w the rotation
    vector (rad per unit of disp; mm-free). One point, or points on one line (rotation about that line is not
    seen): the unseen part of w is 0."""
    n = len(points)
    c = [sum(p[k] for p in points) / n for k in range(3)]
    t = [sum(u[k] for u in disp) / n for k in range(3)]
    J = [[0.0] * 3 for _ in range(3)]
    b = [0.0, 0.0, 0.0]
    for p, u in zip(points, disp):
        r = [p[k] - c[k] for k in range(3)]
        rr = r[0] * r[0] + r[1] * r[1] + r[2] * r[2]
        for i in range(3):
            for j in range(3):
                J[i][j] += (rr if i == j else 0.0) - r[i] * r[j]
        v = [u[k] - t[k] for k in range(3)]
        b[0] += r[1] * v[2] - r[2] * v[1]
        b[1] += r[2] * v[0] - r[0] * v[2]
        b[2] += r[0] * v[1] - r[1] * v[0]
    tr = J[0][0] + J[1][1] + J[2][2]
    if tr <= 1e-12:
        return c, t, [0.0, 0.0, 0.0]
    eps = 1e-9 * tr   # a line of points: J is singular along it, and b has no part along it
    A = [[J[i][j] + (eps if i == j else 0.0) for j in range(3)] for i in range(3)]
    return c, t, _solve3(A, b)


def _solve3(A, b):
    det = (A[0][0] * (A[1][1] * A[2][2] - A[1][2] * A[2][1]) - A[0][1] * (A[1][0] * A[2][2] - A[1][2] * A[2][0])
           + A[0][2] * (A[1][0] * A[2][1] - A[1][1] * A[2][0]))
    out = []
    for k in range(3):
        M = [[b[i] if j == k else A[i][j] for j in range(3)] for i in range(3)]
        out.append((M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1]) - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
                    + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0])) / det)
    return out


def motion_nodes(ledger_entry: Record, design: Record, has_node: Callable[[str], bool]) -> list[str]:
    """The nodes a part moves with: the nodes the tool puts its mass on (ledger), where a wheel centre node
    wc:<support> the record does not hold is replaced by that support's frame node. Empty: the part stays."""
    if ledger_entry["cat"] in ("not_in", "body"):
        return []
    sup = {s["id"]: s for s in design.get("supports", [])}
    out = []
    for n, _kg in ledger_entry["nodes"]:
        if not has_node(n) and n.startswith("wc:"):
            n = sup.get(n[3:], {}).get("frame_node", n)
        if has_node(n) and n not in out:
            out.append(n)
    return out


# ---------------------------------------------------------------- K2 (rigid 6-DOF vehicle) replays
# K2 has one rigid sprung body and, per sprung corner, a wheel that moves along its suspension path. A K2 record's
# series (hiraku.solvers.k2_vehicle.record) give the body pose (design origin carried by the body, R = Rz Ry Rx) and
# each wheel's centre, contact, forces and angles; the frame's flexing is not in the model.

def k2_vehicle(design: Record, loading: str):
    """K2's own vehicle for the design with the loading's occupants and cargo on board (the tools' rule:
    hiraku.contract.loading.on_board, then hiraku.solvers.k2_vehicle.model.build_vehicle). None if K2 cannot read it."""
    from hiraku.contract import loading as contract_loading
    from hiraku.solvers.k2_vehicle.model import build_vehicle
    try:
        return build_vehicle(contract_loading.on_board(design, loading))
    except Exception:  # the design cannot be read by K2 here: the parts stay with the body, said in the ledger
        return None


def k2_entry(src: Record, mass: float, veh) -> Record:
    """A part's place in K2's model (model._unsprung): a wheel kind goes to the nearest corner's unsprung mass, a
    suspension kind half; a corner without its own suspension travel (rigid) keeps them in the body; everything
    else is the sprung body. veh None: drive_energy (K3), one mass. Returns the ledger fields plus 'corner'."""
    if veh is None:
        return {"cat": "mass_only", "fn": FN_K3, "nodes": [["車両", mass]], "corner": None,
                "how": "車両の一つの質量に入る (電力の計算の重さ)。形と剛性は使わない"}
    from hiraku.solvers.k2_vehicle.model import HALF_UNSPRUNG_KINDS, UNSPRUNG_KINDS
    kind = str(src.get("kind", "")).lower()
    c = [float(v) / 1000.0 for v in src["box"]["center_mm"]]
    if kind in UNSPRUNG_KINDS or kind in HALF_UNSPRUNG_KINDS:
        i = min(range(len(veh.corners)), key=lambda j: sum((a - b) ** 2 for a, b in zip(c, veh.corners[j].pos)))
        cn = veh.corners[i]
        if cn.dof:
            if kind in UNSPRUNG_KINDS:
                return {"cat": "unsprung", "fn": FN_K2, "nodes": [[f"輪 {cn.id}", mass]], "corner": i,
                        "how": f"ばね下: いちばん近い輪 {cn.id} の質量。輪と一緒に上下する (形は剛体の箱)"}
            return {"cat": "half", "fn": FN_K2, "nodes": [[f"輪 {cn.id}", mass / 2], ["車体", mass / 2]], "corner": i,
                    "how": f"半分は輪 {cn.id} と一緒 (ばね下)、半分は車体 (ばね上)。箱は輪と車体の中ほどに描く"}
        return {"cat": "sprung", "fn": FN_K2, "nodes": [["車体", mass]], "corner": None,
                "how": f"輪 {cn.id} にばねがないので、車体と一緒の剛体"}
    return {"cat": "sprung", "fn": FN_K2, "nodes": [["車体", mass]], "corner": None,
            "how": "ばね上: 車体の一つの剛体に質量と慣性が入る。剛性はなく、変形しない"}


def rot_zyx(roll: float, pitch: float, yaw: float) -> list[list[float]]:
    """R = Rz(yaw) Ry(pitch) Rx(roll), angles in degrees (hiraku.solvers.k2_vehicle.record)."""
    import math
    a, b, c = (math.radians(v) for v in (roll, pitch, yaw))
    ca, sa, cb, sb, cc, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    return [[cc * cb, cc * sb * sa - sc * ca, cc * sb * ca + sc * sa],
            [sc * cb, sc * sb * sa + cc * ca, sc * sb * ca - cc * sa],
            [-sb, cb * sa, cb * ca]]


WHEEL_CH = ("wheel_center_x", "wheel_center_y", "wheel_center_z", "contact_x", "contact_y", "contact_z",
            "reaction_x", "reaction_y", "reaction_z", "grip_x", "grip_y", "grip_z",
            "wheel_load", "friction_use", "wheel_steer", "wheel_spin", "wheel_stroke", "sliding")
W_MM = 6   # the first six (centre and contact) are m in the record, mm here


def k2_motion(record: Record, veh, ledger: Record) -> Record:
    """The replay of a K2 record: t (packed, nT), pose (packed nT x 6: design origin x, y, z mm, roll, pitch, yaw deg),
    wheels (packed nT x nw x len(WHEEL_CH)), corners [{id, r, width, cb (wheel centre in the body frame, mm, from the
    first instant: R0^T (centre0 - origin0)), drive, brake, steer, dof}], part_mode per part (0 body, 1 wheel, 2
    half) and part_corner, channels (WHEEL_CH order). Every channel is the record's own, at the body series' times."""
    ser = {s["channel"]: s for s in record.get("series", []) or []}
    if "body_x" not in ser:
        raise LookupError("the record has no body_x series (not a K2 replay)")
    t = [float(v) for v in ser["body_x"]["t_s"]]

    def at(ch):
        s = ser.get(ch)
        if s is None:
            return [0.0] * len(t)
        ts, vs = s.get("t_s") or [], s["values"]
        if len(ts) == len(t):
            return [float(v) for v in vs]
        out, j = [], 0
        for tv in t:   # linear, the series sorted in time
            while j + 1 < len(ts) - 1 and ts[j + 1] < tv:
                j += 1
            if len(ts) < 2:
                out.append(float(vs[0]))
                continue
            a, b = ts[j], ts[j + 1]
            w = min(1.0, max(0.0, (tv - a) / (b - a))) if b > a else 0.0
            out.append(float(vs[j]) * (1 - w) + float(vs[j + 1]) * w)
        return out

    body = [at(c) for c in ("body_x", "body_y", "body_z", "roll", "pitch", "yaw")]
    pose = [v for k in range(len(t)) for v in (body[0][k] * 1000, body[1][k] * 1000, body[2][k] * 1000,
                                                 body[3][k], body[4][k], body[5][k])]
    ids = [c.id for c in veh.corners] if veh is not None else sorted(
        {c.split(".", 1)[1] for c in ser if c.startswith("wheel_load.")})
    W = {cid: [at(f"{ch}.{cid}") for ch in WHEEL_CH] for cid in ids}
    wheels = [W[cid][j][k] * (1000.0 if j < W_MM else 1.0) for k in range(len(t)) for cid in ids for j in range(len(WHEEL_CH))]
    R0 = rot_zyx(body[3][0], body[4][0], body[5][0])
    o0 = [body[a][0] * 1000 for a in range(3)]
    corners = []
    for i, cid in enumerate(ids):
        c0 = [W[cid][a][0] * 1000 - o0[a] for a in range(3)]
        cb = [sum(R0[r][a] * c0[r] for r in range(3)) for a in range(3)]   # R0^T c0
        cn = veh.corners[i] if veh is not None else None
        corners.append({"id": cid, "cb": cb, "r": (cn.radius * 1000 if cn else 300.0), "width": (cn.width * 1000 if cn else 200.0),
                        "drive": bool(cn and cn.drive), "brake": bool(cn and cn.brake), "steer": bool(cn and cn.steer),
                        "dof": bool(cn and cn.dof)})
    mode, corner = [], []
    for e in ledger["parts"]:
        ci = e.get("corner")
        mode.append({"unsprung": 1, "half": 2}.get(e["cat"], 0) if ci is not None else 0)
        corner.append(ci if ci is not None else -1)
    return {"t": pack_f32(t), "pose": pack_f32(pose, [len(t), 6]),
            "wheels": pack_f32(wheels, [len(t), len(ids), len(WHEEL_CH)]), "channels": list(WHEEL_CH),
            "corners": corners, "part_mode": mode, "part_corner": corner, "nT": len(t),
            "t_end": t[-1] if t else 0.0}


def k2_plot_series(record: Record, channels: list[str]) -> Record:
    """{channel: {unit, t (packed), v (packed)}} for the plots (the record's own times)."""
    out = {}
    for s in record.get("series", []) or []:
        if s["channel"] in channels and s.get("t_s"):
            out[s["channel"]] = {"unit": s.get("unit", ""), "t": pack_f32(s["t_s"]), "v": pack_f32(s["values"])}
    return out


def k2_scene(record: Record) -> list[Record]:
    """The record's scene for a K2 replay, every kind (gate and cone have no box; road_profile has its profile):
    {kind, label, c, s, rpy, angle_channel, profile}."""
    out = []
    for s in record.get("scene", []) or []:
        e = {"kind": s.get("kind", ""), "label": s.get("label", s.get("kind", "")),
             "c": [float(v) for v in s.get("position_mm", [0, 0, 0])], "s": [float(v) for v in s.get("size_mm", [0, 0, 0])],
             "rpy": [float(v) for v in s.get("rpy_deg", [0, 0, 0])], "angle_channel": s.get("angle_channel")}
        if s.get("profile"):
            p = s["profile"]
            e["profile"] = {"x": pack_f32(p["x_mm"]), "left": pack_f32(p["z_mm"]["left"]), "right": pack_f32(p["z_mm"]["right"])}
            m = re.search(r"y = \+([\d.]+) mm", s.get("label", ""))
            e["profile"]["half_track"] = float(m.group(1)) if m else None
        out.append(e)
    return out


def hot_spots_k2(record: Record) -> list[Record]:
    out = []
    for h in record.get("hot_spots", []) or []:
        v, lim = h.get("value") or {}, h.get("limit") or {}
        out.append({"where": h.get("where", ""), "quantity": h.get("quantity", ""), "value": v.get("value"),
                    "unit": v.get("unit", ""), "limit": lim.get("value"), "u": h.get("utilisation")})
    return out


def judge(record: Record, load_case: Record | None) -> Record | None:
    """The load case's criterion against the record's result of that quantity: {quantity, op, limit, value, ok}."""
    crit = (load_case or {}).get("criterion")
    if not crit:
        return None
    v = result(record, crit["quantity"])
    lim = float(crit["limit"]["value"])
    ops = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, "<": lambda a, b: a < b}
    ok = None if v is None or crit["op"] not in ops else ops[crit["op"]](v, lim)
    return {"quantity": crit["quantity"], "op": crit["op"], "limit": lim, "unit": crit["limit"].get("unit", ""),
            "value": v, "ok": ok, "basis": crit.get("basis", "")}


def parser(field: str, extra: Callable[[argparse.ArgumentParser], None] | None = None) -> argparse.ArgumentParser:
    """The command line of a field's script (main); viewer.src.build makes the same arguments for one analysis."""
    ap = argparse.ArgumentParser(prog=f"python -m viewer.src.{field}",
                                 description=f"Write viewer/data/{field}.js from a run folder")
    ap.add_argument("run", type=Path, help="run folder (with v###/) or one version folder (with analyses/)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder (default viewer/data)")
    ap.add_argument("--version", help="version folder name, e.g. v003 (default: the newest with a record)")
    ap.add_argument("--analysis", help="analysis id, e.g. ana-0038")
    if extra:
        extra(ap)
    return ap


def main(field: str, build: Callable[[argparse.Namespace], Record], extra: Callable[[argparse.ArgumentParser], None] | None = None,
         argv: list[str] | None = None) -> Path:
    args = parser(field, extra).parse_args(argv)
    obj = build(args)
    path = write_data(field, obj, args.out)
    print(f"wrote {path} ({path.stat().st_size} bytes) from {obj['source']['version']} {obj['source']['analysis_id']}")
    return path

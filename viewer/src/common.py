"""Shared parts of the viewer's data scripts: reading a run folder, packing arrays, and the
design's frame (beam lines) and part boxes.

A run folder holds v001/, v002/, ... each with design.json and analyses/ana-*.json
(and optionally meshes/). A version folder (with analyses/ in it) is accepted too.

Data written for the page (viewer/data/<field>.js):

    window.VIEW_DATA = window.VIEW_DATA || {};
    window.VIEW_DATA["<field>"] = {...};

Every field's object has these keys (the page's shared parts read them):
    field, title, source {run, version, analysis_id, tool, load_case_id, design_name}
    frame {node_ids, nodes (packed f32, n x 3, mm), members [[ia, ib], ...], member_ids,
           member_names, radius_mm}
    parts [{id, name, kind, module, c, s, r}]   (box centre, size, rpy_deg; mm and deg)
    scene [{kind, label, c, s}]                 (boxes from the record's scene)
    vectors [{kind, at, o, f}]                  (origin mm, force N)
    inputs [{label, value, source}]             (the case's inputs and where they come from)
    not_computed [text]                         (what the record does not hold)
    notes [text]
plus the field's own keys (see each script).

Packed arrays: {"dtype": "f32", "shape": [...], "b64": base64(gzip(little-endian bytes))}.
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


def base_object(field: str, title: str, run: Path, vdir: Path, record: Record, design: Record) -> Record:
    return {
        "field": field,
        "title": title,
        "source": {"run": str(Path(run).resolve()), "version": Path(vdir).name,
                   "analysis_id": record.get("analysis_id", ""), "tool": record.get("tool", ""),
                   "load_case_id": record.get("load_case_id", ""), "design_name": design.get("name", ""),
                   "design_version": design.get("design_version")},
        "frame": frame_of(design),
        "parts": parts_of(design),
        "scene": scene_of(record),
        "vectors": vectors_of(record),
        "results": results_of(record),
        "assumptions": list(record.get("assumptions", []) or []),
        "inputs": [],
        "not_computed": [],
        "notes": [],
    }


def case_inputs(lc: Record | None, load_case_id: str) -> list[Record]:
    """The load case's title and params as input rows."""
    if lc is None:
        return [{"label": "load case", "value": load_case_id,
                 "source": "the run does not hold this load case (no project.json entry)"}]
    rows = [{"label": "load case", "value": f"{lc['load_case_id']}: {lc.get('title', '')}",
             "source": f"test {lc.get('test_id', '')}, catalog builder"}]
    for k, v in (lc.get("params") or {}).items():
        rows.append({"label": k, "value": qty(v), "source": "load case params"})
    crit = lc.get("criterion")
    if crit:
        rows.append({"label": "criterion", "value": f"{crit['quantity']} {crit['op']} {qty(crit['limit'])}",
                     "source": crit.get("basis", "")})
    return rows


def main(field: str, build: Callable[[argparse.Namespace], Record], extra: Callable[[argparse.ArgumentParser], None] | None = None,
         argv: list[str] | None = None) -> Path:
    ap = argparse.ArgumentParser(prog=f"python -m viewer.src.{field}",
                                 description=f"Write viewer/data/{field}.js from a run folder")
    ap.add_argument("run", type=Path, help="run folder (with v###/) or one version folder (with analyses/)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder (default viewer/data)")
    ap.add_argument("--version", help="version folder name, e.g. v003 (default: the newest with a record)")
    ap.add_argument("--analysis", help="analysis id, e.g. ana-0038")
    if extra:
        extra(ap)
    args = ap.parse_args(argv)
    obj = build(args)
    path = write_data(field, obj, args.out)
    print(f"wrote {path} ({path.stat().st_size} bytes) from {obj['source']['version']} {obj['source']['analysis_id']}")
    return path

"""VTK files of a run's fields, for ParaView: legacy ASCII (# vtk DataFile Version 3.0), written with
numpy only. Values are the records' own.

- The frame as beams: the design's nodes as points and its members as lines (VTK_LINE);
  per_member values are cell data, per_node values point data (vec3 as VECTORS).
- The shell mesh (a `mesh` record): nodes_mm as points, elements as triangles or quads
  (VTK_TRIANGLE / VTK_QUAD); per_element is cell data, per_mesh_node point data.
- A field with `frames` gives one file per kept moment (…-t000.vtk, …), the numbered set
  ParaView reads as a time series.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from hiraku.store import RunStore

Record = dict[str, Any]

VTK_LINE, VTK_TRIANGLE, VTK_QUAD = 3, 5, 9


def _name(field: Record, n: int) -> str:
    """A data array name ParaView shows: kind, surface and label, no spaces."""
    parts = [field["kind"], field.get("surface") or "", field.get("label") or str(n)]
    return re.sub(r"[^A-Za-z0-9_]+", "_", "_".join(p for p in parts if p)).strip("_")[:120] or f"field_{n}"


def _array(name: str, values: list[Any]) -> list[str]:
    """SCALARS or VECTORS lines for one array (None as NaN)."""
    if values and isinstance(values[0], (list, tuple)):
        a = np.array([[np.nan] * 3 if v is None else v for v in values], dtype=float).reshape(-1, 3)
        return [f"VECTORS {name} double"] + [" ".join(repr(float(x)) for x in row) for row in a]
    a = np.array([np.nan if v is None else v for v in values], dtype=float)
    return [f"SCALARS {name} double 1", "LOOKUP_TABLE default"] + [repr(float(x)) for x in a]


def write_vtk(path: Path, title: str, points: list[list[float]], cells: list[list[int]],
              point_data: dict[str, list[Any]] | None = None, cell_data: dict[str, list[Any]] | None = None) -> Path:
    pts = np.asarray(points, dtype=float).reshape(-1, 3)
    kinds = {2: VTK_LINE, 3: VTK_TRIANGLE, 4: VTK_QUAD}
    lines = ["# vtk DataFile Version 3.0", re.sub(r"\s+", " ", title)[:250], "ASCII", "DATASET UNSTRUCTURED_GRID",
             f"POINTS {len(pts)} double"]
    lines += [" ".join(repr(float(x)) for x in p) for p in pts]
    lines.append(f"CELLS {len(cells)} {sum(len(c) + 1 for c in cells)}")
    lines += [" ".join(str(int(i)) for i in [len(c), *c]) for c in cells]
    lines.append(f"CELL_TYPES {len(cells)}")
    lines += [str(kinds[len(c)]) for c in cells]
    if cell_data:
        lines.append(f"CELL_DATA {len(cells)}")
        for name, values in cell_data.items():
            lines += _array(name, values)
    if point_data:
        lines.append(f"POINT_DATA {len(pts)}")
        for name, values in point_data.items():
            lines += _array(name, values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def read_vtk(path: Path) -> Record:
    """{points, cells, cell_types, cell_data {name: list}, point_data {name: list}} of a file written here."""
    words = Path(path).read_text(encoding="ascii").split("\n")
    out: Record = {"points": [], "cells": [], "cell_types": [], "cell_data": {}, "point_data": {}}
    i, where = 4, None
    while i < len(words):
        head = words[i].split()
        if not head:
            i += 1
            continue
        if head[0] == "POINTS":
            n = int(head[1])
            out["points"] = [[float(x) for x in words[i + 1 + k].split()] for k in range(n)]
            i += 1 + n
        elif head[0] == "CELLS":
            n = int(head[1])
            out["cells"] = [[int(x) for x in words[i + 1 + k].split()[1:]] for k in range(n)]
            i += 1 + n
        elif head[0] == "CELL_TYPES":
            n = int(head[1])
            out["cell_types"] = [int(words[i + 1 + k]) for k in range(n)]
            i += 1 + n
        elif head[0] in ("CELL_DATA", "POINT_DATA"):
            where, count = ("cell_data" if head[0] == "CELL_DATA" else "point_data"), int(head[1])
            i += 1
        elif head[0] == "SCALARS":
            out[where][head[1]] = [float(words[i + 2 + k]) for k in range(count)]
            i += 2 + count
        elif head[0] == "VECTORS":
            out[where][head[1]] = [[float(x) for x in words[i + 1 + k].split()] for k in range(count)]
            i += 1 + count
        else:
            i += 1
    return out


def _versions(store: RunStore, versions: Iterable[int] | None) -> list[int]:
    have = sorted(store.project.get("design_versions") or [])
    return [v for v in have if versions is None or v in set(versions)]


def _frame_file(path: Path, a: Record, design: Record) -> Path | None:
    """The analysis's beam fields (per_member, per_node) on the design's frame; None without any."""
    # numbered by their place in the analysis's fields, as in the mesh files
    fields = [(n, f) for n, f in enumerate(a.get("fields", []), 1) if f.get("per_member") or f.get("per_node")]
    if not fields:
        return None
    nodes = [n for n in design.get("nodes", [])]
    index = {n["id"]: i for i, n in enumerate(nodes)}
    members = [m for m in design.get("members", []) if m["node_a"] in index and m["node_b"] in index]
    cell_data, point_data = {}, {}
    for n, f in fields:
        if f.get("per_member"):
            cell_data[_name(f, n)] = [f["per_member"].get(m["id"]) for m in members]
        if f.get("per_node"):
            got = [f["per_node"].get(nd["id"]) for nd in nodes]
            shape = next((g for g in got if g is not None), 0.0)
            point_data[_name(f, n)] = [g if g is not None else ([None] * 3 if isinstance(shape, list) else None)
                                       for g in got]
    return write_vtk(path, f"{a['analysis_id']} {a['tool']} {a['load_case_id']} (members as lines)",
                     [nd["xyz_mm"] for nd in nodes], [[index[m["node_a"]], index[m["node_b"]]] for m in members],
                     point_data, cell_data)


def _mesh_files(folder: Path, a: Record, meshes: dict[str, Record]) -> list[Path]:
    """The analysis's mesh fields (per_element, per_mesh_node, frames) on their meshes."""
    out = []
    by_mesh: dict[str, list[tuple[int, Record]]] = {}
    for n, f in enumerate(a.get("fields", []), 1):
        if f.get("mesh_id") in meshes:
            by_mesh.setdefault(f["mesh_id"], []).append((n, f))
    for mesh_id, fields in by_mesh.items():
        mesh = meshes[mesh_id]
        cell_data = {_name(f, n): f["per_element"] for n, f in fields if f.get("per_element")}
        point_data = {_name(f, n): f["per_mesh_node"] for n, f in fields if f.get("per_mesh_node")}
        title = f"{a['analysis_id']} {a['tool']} {a['load_case_id']} on {mesh_id}"
        out.append(write_vtk(folder / f"{a['analysis_id']}-{mesh_id}.vtk", title, mesh["nodes_mm"], mesh["elements"],
                             point_data, cell_data))
        timed = [(n, f) for n, f in fields if (f.get("frames") or {}).get("per_element")]
        for n, f in timed:
            fr = f["frames"]
            for k, (t, values) in enumerate(zip(fr["t_s"], fr["per_element"])):
                out.append(write_vtk(folder / f"{a['analysis_id']}-{mesh_id}-t{k:03d}.vtk", f"{title} t = {t:g} s",
                                     mesh["nodes_mm"], mesh["elements"], None, {_name(f, n): values}))
    return out


def write_fields(store: RunStore, folder: Path, versions: Iterable[int] | None = None) -> list[Path]:
    """VTK files of every analysis with fields: vtk/v<NNN>/<analysis_id>-frame.vtk (beams),
    <analysis_id>-<mesh_id>.vtk (shell) and its moments; and each mesh alone, <mesh_id>.vtk."""
    out = []
    for v in _versions(store, versions):
        sub = folder / f"v{v:03d}"
        meshes = {m["mesh_id"]: m for m in store.records("mesh", v)}
        for m in meshes.values():  # the mesh alone, each element with its member's wall thickness
            thickness = [m["member_thickness_mm"].get(mid) for mid in m["element_member"]]
            out.append(write_vtk(sub / f"{m['mesh_id']}.vtk", f"{m['mesh_id']} design version {v}", m["nodes_mm"],
                                 m["elements"], None, {"thickness_mm": thickness}))
        design = store.design(v)
        for a in store.records("analysis", v):
            got = _frame_file(sub / f"{a['analysis_id']}-frame.vtk", a, design)
            if got:
                out.append(got)
            out += _mesh_files(sub, a, meshes)
    return out

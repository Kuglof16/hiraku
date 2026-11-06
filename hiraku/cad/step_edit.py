"""Edit a STEP file: read it, change the solids, write it again (STEP keeps no history).

edit_step(text, edits) -> text
edit_step_report(text, edits) -> (text, log)

Solids that no edit touches are copied entity for entity (their B-rep is not rebuilt), with the
file's units and assembly placements baked in, so their name and geometry stay the same in mm.
The output is one assembly of one product per solid, like design_to_step.

Edits (a list of dicts, applied in order; `name` is a solid's name and must be unique in the file):
  {"op": "move",   "name", "by_mm": [dx, dy, dz]}
  {"op": "rotate", "name", "axis": [x, y, z], "angle_deg", "about_mm"?: [x, y, z] (default: the solid's centre)}
  {"op": "delete", "name"}
  {"op": "add_box", "name", "center_mm", "size_mm", "rpy_deg"?}
  {"op": "set_section", "name", "section": {"shape", "dims_mm"}}   the solid must be a straight tube or bar
  {"op": "set_wall", "name", "t_mm"}                                a rect_tube or round_tube keeps its outside
  {"op": "add_brace", "name", "from_mm", "to_mm", "section": {"shape", "dims_mm"}}
"""

from __future__ import annotations

from typing import Any

import numpy as np

from hiraku.cad import CadError
from hiraku.cad.geom import member_axes, rotation_about, rpy_matrix, unit
from hiraku.cad.import_design import _rpy, recognise
from hiraku.cad.step_read import Solid, read_step_entities
from hiraku.cad.step_write import StepBuilder, box_solid, section_solid

OPS = ("move", "rotate", "delete", "add_box", "set_section", "set_wall", "add_brace")


def _vec(e: dict, key: str) -> np.ndarray:
    v = e.get(key)
    if not isinstance(v, (list, tuple)) or len(v) != 3 or not all(isinstance(x, (int, float)) for x in v):
        raise CadError(f"edit {e.get('op')} {e.get('name')!r}: {key} must be three numbers")
    return np.array(v, float)


class _Item:
    def __init__(self, name: str, kind: str, **kw):
        self.name, self.kind = name, kind  # kind: copy | box | prism
        self.solid: Solid | None = kw.get("solid")
        self.extra = np.eye(4)  # applied after the solid's own transform (copy items)
        self.box: dict | None = kw.get("box")
        self.prism: dict | None = kw.get("prism")  # a, b, ey, ez, section
        self.touched = kind != "copy"

    def centre(self) -> np.ndarray:
        if self.kind == "copy":
            return self.extra[:3, :3] @ np.array(self.solid.center_mm) + self.extra[:3, 3]
        if self.kind == "box":
            return np.array(self.box["center_mm"], float)
        return (self.prism["a"] + self.prism["b"]) / 2

    def transform(self, R: np.ndarray, t: np.ndarray) -> None:
        """x -> R x + t."""
        if self.kind == "copy":
            M = np.eye(4)
            M[:3, :3], M[:3, 3] = R, t
            self.extra = M @ self.extra
        elif self.kind == "box":
            c = R @ np.array(self.box["center_mm"], float) + t
            R0 = rpy_matrix(self.box["rpy_deg"]) if self.box.get("rpy_deg") is not None else np.eye(3)
            self.box = {"center_mm": [float(x) for x in c], "size_mm": self.box["size_mm"], "rpy_deg": _rpy(R @ R0)}
        else:
            p = self.prism
            p["a"], p["b"] = R @ p["a"] + t, R @ p["b"] + t
            p["ey"], p["ez"] = R @ p["ey"], R @ p["ez"]
        self.touched = True

    def as_prism(self, op: str) -> dict:
        if self.kind == "prism":
            return self.prism
        if self.kind == "box":
            raise CadError(f"{op} {self.name!r}: it is a box added by an edit, not a tube")
        rec = recognise(self.solid)
        if not rec.get("prism"):
            raise CadError(f"{op} {self.name!r}: not a straight tube or bar ({rec['reason']})")
        R, t = self.extra[:3, :3], self.extra[:3, 3]
        self.prism = {"a": R @ rec["a"] + t, "b": R @ rec["b"] + t, "ey": R @ rec["ey"], "ez": R @ rec["ez"],
                      "section": {"shape": rec["shape"], "dims_mm": dict(rec["dims_mm"])}}
        self.kind = "prism"
        self.touched = True
        return self.prism


def edit_step_report(text: str, edits: list[dict[str, Any]]) -> tuple[str, list[dict]]:
    entities, solids = read_step_entities(text)
    items = [_Item(s.name, "copy", solid=s) for s in solids]
    log = []

    def find(e: dict) -> _Item:
        name = e.get("name")
        hits = [it for it in items if it.name == name]
        if not hits:
            raise CadError(f"edit {e.get('op')}: no solid named {name!r} (there are {len(items)}: "
                           f"{', '.join(it.name for it in items[:12])}{' ...' if len(items) > 12 else ''})")
        if len(hits) > 1:
            raise CadError(f"edit {e.get('op')}: {len(hits)} solids are named {name!r}; the name must be unique")
        return hits[0]

    def new_name(e: dict) -> str:
        name = e.get("name")
        if not isinstance(name, str) or not name:
            raise CadError(f"edit {e.get('op')}: give the new solid a name")
        if any(it.name == name for it in items):
            raise CadError(f"edit {e.get('op')}: a solid named {name!r} is already there")
        return name

    for k, e in enumerate(edits):
        op = e.get("op")
        if op not in OPS:
            raise CadError(f"edit {k}: op {op!r} is not one of {', '.join(OPS)}")
        if op == "move":
            it = find(e)
            it.transform(np.eye(3), _vec(e, "by_mm"))
        elif op == "rotate":
            it = find(e)
            R = rotation_about(_vec(e, "axis"), float(e.get("angle_deg", 0.0)))
            c = _vec(e, "about_mm") if e.get("about_mm") is not None else it.centre()
            it.transform(R, c - R @ c)
        elif op == "delete":
            it = find(e)
            items.remove(it)
        elif op == "add_box":
            box = {"center_mm": list(_vec(e, "center_mm")), "size_mm": list(_vec(e, "size_mm"))}
            if e.get("rpy_deg") is not None:
                box["rpy_deg"] = list(_vec(e, "rpy_deg"))
            items.append(_Item(new_name(e), "box", box=box))
        elif op == "set_section":
            it = find(e)
            sec = e.get("section")
            if not isinstance(sec, dict) or "shape" not in sec:
                raise CadError(f"set_section {it.name!r}: section must be {{shape, dims_mm}}")
            p = it.as_prism(op)
            p["section"] = {"shape": sec["shape"], "dims_mm": dict(sec.get("dims_mm") or {})}
        elif op == "set_wall":
            it = find(e)
            p = it.as_prism(op)
            if p["section"]["shape"] not in ("rect_tube", "round_tube"):
                raise CadError(f"set_wall {it.name!r}: it is a {p['section']['shape']}, not a tube")
            t = e.get("t_mm")
            if not isinstance(t, (int, float)) or t <= 0:
                raise CadError(f"set_wall {it.name!r}: t_mm must be a positive number")
            p["section"]["dims_mm"]["t"] = float(t)
        else:  # add_brace
            a, b = _vec(e, "from_mm"), _vec(e, "to_mm")
            if np.linalg.norm(b - a) < 1e-6:
                raise CadError(f"add_brace {e.get('name')!r}: from_mm and to_mm are the same point")
            ey, ez = member_axes(unit(b - a))
            sec = e.get("section")
            if not isinstance(sec, dict) or "shape" not in sec:
                raise CadError(f"add_brace {e.get('name')!r}: section must be {{shape, dims_mm}}")
            items.append(_Item(new_name(e), "prism", prism={"a": a, "b": b, "ey": ey, "ez": ez,
                                                             "section": {"shape": sec["shape"], "dims_mm": dict(sec.get("dims_mm") or {})}}))
        log.append({"edit": k, "op": op, "name": e.get("name")})

    b = StepBuilder()
    out = []
    for it in items:
        if it.kind == "copy":
            s = it.solid
            M = it.extra @ s.transform
            R3 = M[:3, :3]
            scale = float(np.cbrt(abs(np.linalg.det(R3)))) or 1.0
            Rn = R3 / scale
            u = s.unit_mm
            sid = b.copy_solid(entities, s.entity_id,
                               point_fn=lambda p, R3=R3, M=M, u=u: R3 @ (p * u) + M[:3, 3],
                               dir_fn=lambda d, Rn=Rn: Rn @ d,
                               length_scale=u * scale, angle_scale=s.angle_rad, name=it.name)
        elif it.kind == "box":
            sid = box_solid(b, it.name, it.box)
        else:
            p = it.prism
            sid = section_solid(b, it.name, p["a"], p["b"], p["section"], p["ey"], p["ez"])
        out.append((it.name, sid))
        if it.touched:
            log.append({"written": it.name, "how": {"copy": "copied, moved", "box": "box", "prism": "rebuilt from its section"}[it.kind]})
    return b.text("edited", out), log


def edit_step(text: str, edits: list[dict[str, Any]]) -> str:
    return edit_step_report(text, edits)[0]

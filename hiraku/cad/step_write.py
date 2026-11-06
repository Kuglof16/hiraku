"""Write a design record as STEP (ISO 10303-21, AP214 automotive_design), in mm.

One MANIFOLD_SOLID_BREP per member and per part, each in its own product named by the member's or
part's id, under one assembly product named after the design (identity placements, so every solid
is in design coordinates).

- A member is its section swept along the straight line from node_a to node_b (the ends are cut
  square at the nodes, so members overlap at joints, as in a centreline frame model).
  rect_tube {b, h, t}: a hollow prism, planar faces, the wall a real through hole (one closed
  shell of genus 1: outer sides, inner sides, end faces with an inner bound).
  round_tube {d, t}: cylindrical surfaces (outer and inner) bounded by circles, flat annular ends.
  rect_solid {b, h} and round_solid {d}: the same without the hole.
  Any other shape (angle, channel, i_beam, custom) raises CadError naming the shape and member.
  h lies along the member's ey and b along ez (hiraku.cad.geom.member_axes).
- A part is its box: center_mm, size_mm, rpy_deg if present (R = Rz Ry Rx).
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

import numpy as np

from hiraku.cad import CadError
from hiraku.cad.geom import member_axes, rpy_matrix, unit
from hiraku.cad.part21 import Ref, fmt, fmt_entity_body, fmt_float, refs_in, remap

SCHEMA_AP214 = "AUTOMOTIVE_DESIGN { 1 0 10303 214 1 1 1 1 }"
SHAPES = ("rect_tube", "round_tube", "rect_solid", "round_solid")

# Entity arguments that are lengths (index in args), scaled when a solid is copied from a file in
# other units; CONICAL_SURFACE's semi-angle (index 3) is an angle.
LENGTH_ARGS = {
    "VECTOR": (2,), "CIRCLE": (2,), "ELLIPSE": (2, 3), "CYLINDRICAL_SURFACE": (2,),
    "CONICAL_SURFACE": (2,), "SPHERICAL_SURFACE": (2,), "TOROIDAL_SURFACE": (2, 3),
    "DEGENERATE_TOROIDAL_SURFACE": (2, 3), "OFFSET_SURFACE": (2,), "OFFSET_CURVE_3D": (2,),
}


def _f3(p) -> str:
    return "(" + ",".join(fmt_float(float(x)) for x in p) + ")"


class StepBuilder:
    """Collects entity lines with ids; text() wraps the solids in products and an assembly."""

    def __init__(self):
        self.lines: list[str] = []
        self.n = 0
        a = self.add
        self.app = a("APPLICATION_CONTEXT('automotive design')")
        a(f"APPLICATION_PROTOCOL_DEFINITION('international standard','automotive_design',2000,#{self.app})")
        self.prod_ctx = a(f"PRODUCT_CONTEXT('',#{self.app},'mechanical')")
        self.pd_ctx = a(f"PRODUCT_DEFINITION_CONTEXT('part definition',#{self.app},'design')")
        mm = a("(LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.))")
        rad = a("(NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.))")
        sr = a("(NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT())")
        unc = a(f"UNCERTAINTY_MEASURE_WITH_UNIT(LENGTH_MEASURE(1.E-03),#{mm},'distance_accuracy_value','')")
        self.ctx = a(f"(GEOMETRIC_REPRESENTATION_CONTEXT(3) GLOBAL_UNCERTAINTY_ASSIGNED_CONTEXT((#{unc})) "
                     f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{mm},#{rad},#{sr})) REPRESENTATION_CONTEXT('',''))")
        self.origin = self.axis((0, 0, 0), (0, 0, 1), (1, 0, 0))

    def add(self, body: str) -> int:
        self.n += 1
        self.lines.append(f"#{self.n}={body};")
        return self.n

    def pt(self, p) -> int:
        return self.add(f"CARTESIAN_POINT('',{_f3(p)})")

    def dir(self, d) -> int:
        return self.add(f"DIRECTION('',{_f3(unit(d))})")

    def axis(self, o, z, x) -> int:
        return self.add(f"AXIS2_PLACEMENT_3D('',#{self.pt(o)},#{self.dir(z)},#{self.dir(x)})")

    def _line_edge(self, va: int, pa, vb: int, pb, pa_id: int) -> int:
        d = np.asarray(pb, float) - np.asarray(pa, float)
        L = float(np.linalg.norm(d))
        vec = self.add(f"VECTOR('',#{self.dir(d)},{fmt_float(L)})")
        line = self.add(f"LINE('',#{pa_id},#{vec})")
        return self.add(f"EDGE_CURVE('',#{va},#{vb},#{line},.T.)")

    def _oe(self, edge: int, forward: bool) -> int:
        return self.add(f"ORIENTED_EDGE('',*,*,#{edge},{'.T.' if forward else '.F.'})")

    def _face(self, loops: list[list[tuple[int, bool]]], surface: int, same_sense: bool = True) -> int:
        bounds = []
        for k, loop in enumerate(loops):
            el = self.add("EDGE_LOOP('',(" + ",".join(f"#{self._oe(e, f)}" for e, f in loop) + "))")
            kind = "FACE_OUTER_BOUND" if k == 0 else "FACE_BOUND"
            bounds.append(self.add(f"{kind}('',#{el},.T.)"))
        return self.add("ADVANCED_FACE('',(" + ",".join(f"#{b}" for b in bounds) + f"),#{surface},"
                        f"{'.T.' if same_sense else '.F.'})")

    def _plane(self, o, n, x) -> int:
        return self.add(f"PLANE('',#{self.axis(o, n, x)})")

    def prism(self, name: str, A, B, ey, ez, loops2d: list[list[tuple[float, float]]]) -> int:
        """A straight prism from A to B; loops2d in (ey, ez) coordinates, the first counter-clockwise
        seen from B (the outline), the rest clockwise (holes)."""
        A, B, ey, ez = (np.asarray(v, float) for v in (A, B, ey, ez))
        d = unit(B - A)
        rings = []  # per loop: (pts A, pts B, vtx A, vtx B, cp A, cp B)
        for loop in loops2d:
            pa = [A + s * ey + t * ez for s, t in loop]
            pb = [B + s * ey + t * ez for s, t in loop]
            ca = [self.pt(p) for p in pa]
            cb = [self.pt(p) for p in pb]
            va = [self.add(f"VERTEX_POINT('',#{c})") for c in ca]
            vb = [self.add(f"VERTEX_POINT('',#{c})") for c in cb]
            rings.append((pa, pb, va, vb, ca, cb))
        faces = []
        end_a, end_b = [], []
        for pa, pb, va, vb, ca, cb in rings:
            n = len(pa)
            ring_a = [self._line_edge(va[i], pa[i], va[(i + 1) % n], pa[(i + 1) % n], ca[i]) for i in range(n)]
            ring_b = [self._line_edge(vb[i], pb[i], vb[(i + 1) % n], pb[(i + 1) % n], cb[i]) for i in range(n)]
            longs = [self._line_edge(va[i], pa[i], vb[i], pb[i], ca[i]) for i in range(n)]
            for i in range(n):
                j = (i + 1) % n
                e = pa[j] - pa[i]
                plane = self._plane(pa[i], np.cross(e, d), e)
                faces.append(self._face([[(ring_a[i], True), (longs[j], True), (ring_b[i], False), (longs[i], False)]], plane))
            end_b.append([(ring_b[i], True) for i in range(n)])
            end_a.append([(ring_a[i], False) for i in reversed(range(n))])
        faces.append(self._face(end_b, self._plane(B, d, ey)))
        faces.append(self._face(end_a, self._plane(A, -d, ey)))
        shell = self.add("CLOSED_SHELL('',(" + ",".join(f"#{f}" for f in faces) + "))")
        return self.add(f"MANIFOLD_SOLID_BREP({fmt(name)},#{shell})")

    def cylinder(self, name: str, A, B, ey, r_out: float, r_in: float | None = None) -> int:
        """A round bar (r_in None) or tube from A to B with cylindrical faces and circular edges."""
        A, B, ey = (np.asarray(v, float) for v in (A, B, ey))
        d = unit(B - A)
        L = float(np.linalg.norm(B - A))
        ax_a, ax_b = self.axis(A, d, ey), self.axis(B, d, ey)

        def ring(r):
            ca, cb = self.pt(A + r * ey), self.pt(B + r * ey)
            va, vb = self.add(f"VERTEX_POINT('',#{ca})"), self.add(f"VERTEX_POINT('',#{cb})")
            circ_a = self.add(f"CIRCLE('',#{ax_a},{fmt_float(r)})")
            circ_b = self.add(f"CIRCLE('',#{ax_b},{fmt_float(r)})")
            ea = self.add(f"EDGE_CURVE('',#{va},#{va},#{circ_a},.T.)")
            eb = self.add(f"EDGE_CURVE('',#{vb},#{vb},#{circ_b},.T.)")
            vec = self.add(f"VECTOR('',#{self.dir(d)},{fmt_float(L)})")
            seam = self.add(f"EDGE_CURVE('',#{va},#{vb},#{self.add(f'LINE({chr(39)}{chr(39)},#{ca},#{vec})')},.T.)")
            surf = self.add(f"CYLINDRICAL_SURFACE('',#{ax_a},{fmt_float(r)})")
            return ea, eb, seam, surf

        oa, ob, oseam, osurf = ring(r_out)
        faces = [self._face([[(oa, True), (oseam, True), (ob, False), (oseam, False)]], osurf, True)]
        end_b, end_a = [[(ob, True)]], [[(oa, False)]]
        if r_in:
            ia, ib, iseam, isurf = ring(r_in)
            faces.append(self._face([[(ia, False), (iseam, True), (ib, True), (iseam, False)]], isurf, False))
            end_b.append([(ib, False)])
            end_a.append([(ia, True)])
        faces.append(self._face(end_b, self.add(f"PLANE('',#{ax_b})"), True))
        faces.append(self._face(end_a, self.add(f"PLANE('',#{ax_a})"), False))
        shell = self.add("CLOSED_SHELL('',(" + ",".join(f"#{f}" for f in faces) + "))")
        return self.add(f"MANIFOLD_SOLID_BREP({fmt(name)},#{shell})")

    def copy_solid(self, entities: dict, root: int, point_fn: Callable, dir_fn: Callable,
                   length_scale: float = 1.0, angle_scale: float = 1.0, name: str | None = None) -> int:
        """Copy the entities a solid uses from a parsed file, moving 3D points and directions."""
        ids: dict[int, int] = {}
        order: list[int] = []
        seen = set()
        stack = [(root, False)]
        while stack:
            eid, done = stack.pop()
            if done:
                order.append(eid)
                continue
            if eid in seen:
                continue
            seen.add(eid)
            if eid not in entities:
                raise CadError(f"entity #{eid} is referred to but missing from the file")
            stack.append((eid, True))
            for _, args in entities[eid].parts:
                for r in refs_in(args):
                    if r not in seen:
                        stack.append((r, False))
        for eid in order:
            ent = entities[eid]
            parts = []
            for tname, args in ent.parts:
                args = remap(list(args), ids)
                if tname == "CARTESIAN_POINT" and len(args[1]) == 3:
                    args[1] = [float(x) for x in point_fn(np.asarray(args[1], float))]
                elif tname == "DIRECTION" and len(args[1]) == 3:
                    args[1] = [float(x) for x in dir_fn(np.asarray(args[1], float))]
                elif len(ent.parts) == 1 and tname in LENGTH_ARGS:
                    for i in LENGTH_ARGS[tname]:
                        args[i] = float(args[i]) * length_scale
                    if tname == "CONICAL_SURFACE":
                        args[3] = float(args[3]) * angle_scale
                if eid == root and name is not None and tname in ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS", "FACETED_BREP"):
                    args[0] = name
                parts.append((tname, args))
            ids[eid] = self.add(fmt_entity_body(parts))
        return ids[root]

    def text(self, assembly_name: str, solids: Iterable[tuple[str, int]], file_name: str = "design.stp") -> str:
        a = self.add

        def product(name: str):
            p = a(f"PRODUCT({fmt(name)},{fmt(name)},'',(#{self.prod_ctx}))")
            pdf = a(f"PRODUCT_DEFINITION_FORMATION('','',#{p})")
            return a(f"PRODUCT_DEFINITION('design','',#{pdf},#{self.pd_ctx})")

        root_pd = product(assembly_name)
        root_sr = a(f"SHAPE_REPRESENTATION({fmt(assembly_name)},(#{self.origin}),#{self.ctx})")
        a(f"SHAPE_DEFINITION_REPRESENTATION(#{a(f'PRODUCT_DEFINITION_SHAPE({chr(39)}{chr(39)},{chr(39)}{chr(39)},#{root_pd})')},#{root_sr})")
        for k, (name, solid) in enumerate(solids, 1):
            pd = product(name)
            sr = a(f"ADVANCED_BREP_SHAPE_REPRESENTATION({fmt(name)},(#{solid},#{self.origin}),#{self.ctx})")
            a(f"SHAPE_DEFINITION_REPRESENTATION(#{a(f'PRODUCT_DEFINITION_SHAPE({chr(39)}{chr(39)},{chr(39)}{chr(39)},#{pd})')},#{sr})")
            nauo = a(f"NEXT_ASSEMBLY_USAGE_OCCURRENCE({fmt(str(k))},{fmt(name)},'',#{root_pd},#{pd},$)")
            pds = a(f"PRODUCT_DEFINITION_SHAPE('','',#{nauo})")
            idt = a(f"ITEM_DEFINED_TRANSFORMATION('','',#{self.origin},#{self.origin})")
            rr = a(f"(REPRESENTATION_RELATIONSHIP('','',#{sr},#{root_sr}) "
                   f"REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION(#{idt}) SHAPE_REPRESENTATION_RELATIONSHIP())")
            a(f"CONTEXT_DEPENDENT_SHAPE_REPRESENTATION(#{rr},#{pds})")
        head = ("ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('HIRAKU design'),'2;1');\n"
                f"FILE_NAME({fmt(file_name)},'2026-01-01T00:00:00',(''),(''),'hiraku.cad','hiraku.cad','');\n"
                f"FILE_SCHEMA(('{SCHEMA_AP214}'));\nENDSEC;\nDATA;\n")
        return head + "\n".join(self.lines) + "\nENDSEC;\nEND-ISO-10303-21;\n"


# ---- design -> solids ----

def _dims(section: dict, keys: tuple[str, ...], where: str) -> list[float]:
    dims = section.get("dims_mm") or {}
    missing = [k for k in keys if not isinstance(dims.get(k), (int, float)) or dims[k] <= 0]
    if missing:
        raise CadError(f"{where}: section shape {section.get('shape')!r} needs dims_mm {', '.join(keys)} > 0 "
                       f"(missing or not positive: {', '.join(missing)})")
    return [float(dims[k]) for k in keys]


def section_solid(b: StepBuilder, name: str, A, B, section: dict, ey=None, ez=None) -> int:
    """The solid of a section swept from A to B (ey, ez default to member_axes)."""
    A, B = np.asarray(A, float), np.asarray(B, float)
    if np.linalg.norm(B - A) < 1e-6:
        raise CadError(f"{name}: its two ends are at the same point {list(A)}")
    if ey is None or ez is None:
        ey, ez = member_axes(unit(B - A))
    shape = section.get("shape")
    where = f"member {name}"
    if shape == "rect_tube":
        bb, h, t = _dims(section, ("b", "h", "t"), where)
        if 2 * t >= min(bb, h):
            raise CadError(f"{where}: rect_tube wall t={t} leaves no hole in {bb} x {h}")
        ho, bo, hi, bi = h / 2, bb / 2, h / 2 - t, bb / 2 - t
        outer = [(-ho, -bo), (ho, -bo), (ho, bo), (-ho, bo)]
        inner = [(-hi, -bi), (-hi, bi), (hi, bi), (hi, -bi)]
        return b.prism(name, A, B, ey, ez, [outer, inner])
    if shape == "rect_solid":
        bb, h = _dims(section, ("b", "h"), where)
        return b.prism(name, A, B, ey, ez, [[(-h / 2, -bb / 2), (h / 2, -bb / 2), (h / 2, bb / 2), (-h / 2, bb / 2)]])
    if shape == "round_tube":
        d, t = _dims(section, ("d", "t"), where)
        if 2 * t >= d:
            raise CadError(f"{where}: round_tube wall t={t} leaves no hole in d={d}")
        return b.cylinder(name, A, B, ey, d / 2, d / 2 - t)
    if shape == "round_solid":
        (d,) = _dims(section, ("d",), where)
        return b.cylinder(name, A, B, ey, d / 2, None)
    raise CadError(f"{where}: section shape {shape!r} cannot be written as a solid yet "
                   f"(written: {', '.join(SHAPES)})")


def box_solid(b: StepBuilder, name: str, box: dict) -> int:
    c = np.asarray(box["center_mm"], float)
    s = [float(x) for x in box["size_mm"]]
    if min(s) <= 0:
        raise CadError(f"part {name}: box size_mm {s} has a side that is not positive")
    R = rpy_matrix(box["rpy_deg"]) if box.get("rpy_deg") is not None else np.eye(3)
    A, B = c - R[:, 0] * s[0] / 2, c + R[:, 0] * s[0] / 2
    hy, hz = s[1] / 2, s[2] / 2
    return b.prism(name, A, B, R[:, 1], R[:, 2], [[(-hy, -hz), (hy, -hz), (hy, hz), (-hy, hz)]])


def design_solids(b: StepBuilder, design: dict[str, Any]) -> list[tuple[str, int]]:
    nodes = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    sections = design.get("sections", {})
    out = []
    for m in design.get("members", []):
        for end in ("node_a", "node_b"):
            if m[end] not in nodes:
                raise CadError(f"member {m['id']}: {end} {m[end]!r} is not in nodes")
        if m["section"] not in sections:
            raise CadError(f"member {m['id']}: section {m['section']!r} is not in sections")
        out.append((m["id"], section_solid(b, m["id"], nodes[m["node_a"]], nodes[m["node_b"]], sections[m["section"]])))
    for p in design.get("parts", []):
        out.append((p["id"], box_solid(b, p["id"], p["box"])))
    return out


def design_to_step(design: dict[str, Any]) -> str:
    """STEP text of the design: a solid per member and per part, named by id, in mm."""
    b = StepBuilder()
    solids = design_solids(b, design)
    return b.text(str(design.get("name") or "design"), solids)

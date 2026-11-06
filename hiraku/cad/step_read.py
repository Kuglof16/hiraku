"""Read solids from STEP text: names, bounding boxes, volumes and centres in mm.

read_step(text, refine=1) -> [Solid]

- Solids: MANIFOLD_SOLID_BREP, BREP_WITH_VOIDS (voids subtracted) and FACETED_BREP found in the
  items of any representation, placed into the top assembly's coordinates through
  NEXT_ASSEMBLY_USAGE_OCCURRENCE + CONTEXT_DEPENDENT_SHAPE_REPRESENTATION (representation
  relationships with ITEM_DEFINED_TRANSFORMATION), plain SHAPE_REPRESENTATION_RELATIONSHIP and
  MAPPED_ITEM. A product used twice gives two solids.
- Units: the length unit of each representation's context (mm, m, inch and other SI prefixes or
  conversion-based units) becomes mm; plane angles in degrees are read as such.
- Volume and centre by the divergence theorem over the closed shell (hiraku.cad.tessellate):
  planar faces exactly from their polygon loops (method "exact" when every edge is straight);
  faces on PLANE, CYLINDRICAL_SURFACE, CONICAL_SURFACE, SPHERICAL_SURFACE, TOROIDAL_SURFACE,
  SURFACE_OF_REVOLUTION, SURFACE_OF_LINEAR_EXTRUSION and B_SPLINE_SURFACE (all its forms, rational
  or not; also through RECTANGULAR_TRIMMED_SURFACE) by sampling their bounds in the surface's
  parameters and integrating over the trimmed region (method "approximate"). Edges on LINE,
  POLYLINE, CIRCLE, ELLIPSE and B_SPLINE_CURVE (all its forms), also through SURFACE_CURVE,
  SEAM_CURVE, INTERSECTION_CURVE and TRIMMED_CURVE. Circles get 256 segments per turn, B-spline
  edges max(32, 8 x control points) over their whole range, both times `refine`.
  A solid with an entity that is not read gets the volume and centre of its bounding box, method
  "bounding_box", and a note naming the entity.
- The bounding box comes from the sampled edges plus sample points inside each curved face.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from hiraku.cad import CadError
from hiraku.cad import surfaces as sf
from hiraku.cad.part21 import Entity, Enum, Ref, Typed, parse
from hiraku.cad.tessellate import curved_face, plane_integrals

SEG_PER_TURN = 256
SOLID_TYPES = ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS", "FACETED_BREP")
CURVED = ("CYLINDRICAL_SURFACE", "CONICAL_SURFACE", "SPHERICAL_SURFACE", "TOROIDAL_SURFACE",
          "DEGENERATE_TOROIDAL_SURFACE")
STRAIGHT = ("LINE", "POLYLINE")

SI_PREFIX = {None: 1.0, "MILLI": 1e-3, "CENTI": 1e-2, "DECI": 1e-1, "KILO": 1e3, "MICRO": 1e-6, "NANO": 1e-9,
             "DECA": 10.0, "HECTO": 100.0}


@dataclass
class Face:
    surface: str  # PLANE, CYLINDRICAL_SURFACE, ... (world mm)
    params: dict  # origin, z, x, y (unit), radius, radius2, semi_angle (rad); analytic surfaces only
    loops: list  # [np.ndarray (n, 3)] closed polylines, world mm, oriented as in the shell
    same_sense: bool
    edge_kinds: list = field(default_factory=list)  # per loop: set of curve types


@dataclass
class _LocalFace:
    """A face in the file's own units and coordinates."""
    id: int
    surface: str
    surf_ref: Any
    surf: Any  # surfaces.Surface, or None for a plane
    loops: list  # per loop: [(points start..end, edge key, straight)]
    same_sense: bool
    edge_kinds: list

    def flat_loops(self) -> list:
        out = []
        for chunks in self.loops:
            if len(chunks) == 1 and len(chunks[0][0]) == 1:
                out.append(chunks[0][0])
            else:
                out.append(np.vstack([p[:-1] for p, _, _ in chunks]) if chunks else np.zeros((0, 3)))
        return out


@dataclass
class Solid:
    name: str
    bbox_min: list
    bbox_max: list
    volume_mm3: float
    center_mm: list
    method: str  # exact | approximate | bounding_box
    notes: list = field(default_factory=list)
    entity_id: int = 0
    product: str = ""
    transform: Any = None  # 4x4 from the solid's own coordinates (file units) to assembly mm
    unit_mm: float = 1.0
    angle_rad: float = 1.0
    faces: list = field(default_factory=list, repr=False)
    voids: int = 0

    @property
    def size_mm(self) -> list:
        return [b - a for a, b in zip(self.bbox_min, self.bbox_max)]

    def summary(self) -> dict:
        return {"name": self.name, "volume_mm3": self.volume_mm3, "center_mm": self.center_mm,
                "bbox_min_mm": self.bbox_min, "bbox_max_mm": self.bbox_max, "size_mm": self.size_mm,
                "volume_method": self.method, "notes": list(self.notes)}


def _M(R=None, t=None, s: float = 1.0) -> np.ndarray:
    M = np.eye(4)
    if R is not None:
        M[:3, :3] = R * s
    elif s != 1.0:
        M[:3, :3] *= s
    if t is not None:
        M[:3, 3] = t
    return M


def _type(e: Entity, family: str) -> str:
    """The entity's type; a complex entity of the B-spline family is named by `family`."""
    if len(e.parts) == 1:
        return e.parts[0][0]
    return family if e.has(family) else "+".join(p[0] for p in e.parts)


class _File:
    def __init__(self, text: str, refine: int = 1):
        self.e: dict[int, Entity] = parse(text)
        self.refine = max(1, int(refine))
        self.edge_cache: dict = {}
        self.unit_cache: dict = {}
        self.curve_cache: dict = {}
        self.surf_cache: dict = {}
        self.solid_cache: dict = {}

    def get(self, ref, *types) -> Entity:
        if not isinstance(ref, Ref):
            raise CadError(f"expected a reference but found {ref!r}")
        ent = self.e.get(int(ref))
        if ent is None:
            raise CadError(f"entity #{int(ref)} is referred to but missing from the file")
        if types and not any(ent.has(t) for t in types):
            raise CadError(f"#{ent.id} is {ent.type}, expected {' or '.join(types)}")
        return ent

    # ---- units ----
    def _unit_factor(self, ref, kind: str) -> float:
        u = self.get(ref)
        if u.has("SI_UNIT"):
            prefix, name = u.part("SI_UNIT")
            prefix = str(prefix) if isinstance(prefix, Enum) else None
            if prefix not in SI_PREFIX:
                raise CadError(f"#{u.id}: SI prefix {prefix} is not known")
            f = SI_PREFIX[prefix]
            if kind == "length":
                return f * 1000.0  # metre -> mm
            return f
        if u.has("CONVERSION_BASED_UNIT"):
            _, measure = u.part("CONVERSION_BASED_UNIT")
            m = self.get(measure)
            args = next((p[1] for p in m.parts if "MEASURE_WITH_UNIT" in p[0] and len(p[1]) >= 2), None)
            if args is None:
                raise CadError(f"#{m.id}: {m.type} gives no value and unit")
            val = args[0]
            val = float(val.args[0]) if isinstance(val, Typed) else float(val)
            return val * self._unit_factor(args[1], kind)
        raise CadError(f"#{u.id}: unit {u.type} is not understood")

    def units(self, ctx_ref) -> tuple[float, float]:
        key = int(ctx_ref)
        if key in self.unit_cache:
            return self.unit_cache[key]
        ctx = self.get(ctx_ref)
        length, angle = 1.0, 1.0
        if ctx.has("GLOBAL_UNIT_ASSIGNED_CONTEXT"):
            for u in ctx.part("GLOBAL_UNIT_ASSIGNED_CONTEXT")[0]:
                ue = self.get(u)
                if ue.has("LENGTH_UNIT"):
                    length = self._unit_factor(u, "length")
                elif ue.has("PLANE_ANGLE_UNIT"):
                    angle = self._unit_factor(u, "angle")
        self.unit_cache[key] = (length, angle)
        return length, angle

    # ---- geometry ----
    def point(self, ref) -> np.ndarray:
        p = self.get(ref, "CARTESIAN_POINT", "VERTEX_POINT")
        if p.has("VERTEX_POINT"):
            return self.point(p.part("VERTEX_POINT")[1])
        c = [float(x) for x in p.part("CARTESIAN_POINT")[1]]
        return np.array((c + [0.0, 0.0])[:3])

    def direction(self, ref) -> np.ndarray:
        d = self.get(ref, "DIRECTION")
        v = np.array(([float(x) for x in d.part("DIRECTION")[1]] + [0.0, 0.0])[:3])
        n = np.linalg.norm(v)
        if n == 0:
            raise CadError(f"#{d.id}: DIRECTION of zero length")
        return v / n

    def vector(self, ref) -> np.ndarray:
        v = self.get(ref, "VECTOR", "DIRECTION")
        if v.has("DIRECTION"):
            return self.direction(ref)
        _, d, mag = v.part("VECTOR")
        return self.direction(d) * float(mag)

    def placement(self, ref) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """(origin, x, y, z) of an AXIS2_PLACEMENT_3D, in file units."""
        a = self.get(ref)
        if a.has("AXIS2_PLACEMENT_3D"):
            _, loc, axis, ref_dir = a.part("AXIS2_PLACEMENT_3D")
            o = self.point(loc)
            z = self.direction(axis) if axis is not None else np.array([0.0, 0.0, 1.0])
            x = self.direction(ref_dir) if ref_dir is not None else (np.array([1.0, 0, 0]) if abs(z[0]) < 0.9 else np.array([0, 1.0, 0]))
            x = x - (x @ z) * z
            x /= np.linalg.norm(x)
            return o, x, np.cross(z, x), z
        if a.has("CARTESIAN_TRANSFORMATION_OPERATOR_3D") or a.has("CARTESIAN_TRANSFORMATION_OPERATOR"):
            args = a.part("CARTESIAN_TRANSFORMATION_OPERATOR")
            a1, a2, o_ref = args[2], args[3], args[4]
            a3 = a.part("CARTESIAN_TRANSFORMATION_OPERATOR_3D")[0] if a.has("CARTESIAN_TRANSFORMATION_OPERATOR_3D") else None
            z = self.direction(a3) if isinstance(a3, Ref) else np.array([0, 0, 1.0])
            x = self.direction(a1) if isinstance(a1, Ref) else np.array([1.0, 0, 0])
            x = x - (x @ z) * z
            x /= np.linalg.norm(x)
            return self.point(o_ref), x, np.cross(z, x), z
        raise CadError(f"#{a.id}: placement {a.type} is not understood")

    def placement_matrix(self, ref, unit_mm: float) -> np.ndarray:
        o, x, y, z = self.placement(ref)
        a = self.get(ref)
        s = 1.0
        if a.has("CARTESIAN_TRANSFORMATION_OPERATOR"):
            sc = a.part("CARTESIAN_TRANSFORMATION_OPERATOR")[5] if len(a.part("CARTESIAN_TRANSFORMATION_OPERATOR")) > 5 else None
            s = float(sc) if isinstance(sc, (int, float)) else 1.0
        return _M(np.column_stack([x, y, z]), o * unit_mm, s)

    # ---- curves ----
    def curve(self, ref) -> sf.Curve:
        key = int(ref)
        if key not in self.curve_cache:
            self.curve_cache[key] = self._curve(self.get(ref))
        return self.curve_cache[key]

    def _curve(self, c: Entity) -> sf.Curve:
        t = _type(c, "B_SPLINE_CURVE")
        a = c.args if len(c.parts) == 1 else None
        if t in ("SURFACE_CURVE", "SEAM_CURVE", "INTERSECTION_CURVE", "BOUNDED_SURFACE_CURVE", "TRIMMED_CURVE"):
            return self.curve(a[1])
        if t == "LINE":
            return sf.Line(self.point(a[1]), self.vector(a[2]))
        if t == "POLYLINE":
            return sf.Polyline([self.point(r) for r in a[1]])
        if t in ("CIRCLE", "ELLIPSE"):
            o, x, y, _ = self.placement(a[1])
            ra = float(a[2])
            return sf.Conic(t, o, x, y, ra, float(a[3]) if t == "ELLIPSE" else ra)
        if t in ("B_SPLINE_CURVE", "B_SPLINE_CURVE_WITH_KNOTS", "BEZIER_CURVE", "UNIFORM_CURVE", "QUASI_UNIFORM_CURVE"):
            return self._bspline_curve(c)
        raise CadError(f"#{c.id}: curve {t} is not read")

    def _bspline_curve(self, c: Entity) -> sf.BSplineCurve:
        W = None
        if len(c.parts) == 1:
            t, a = c.parts[0][0], c.args
            deg, ctrl, rest = int(a[1]), a[2], a[6:]
        else:
            bc = c.part("B_SPLINE_CURVE")
            deg, ctrl = int(bc[0]), bc[1]
            t = next((p[0] for p in c.parts if p[0] in ("B_SPLINE_CURVE_WITH_KNOTS", "BEZIER_CURVE",
                                                         "UNIFORM_CURVE", "QUASI_UNIFORM_CURVE")), None)
            if t is None:
                raise CadError(f"#{c.id}: a B_SPLINE_CURVE without knots is not read")
            rest = c.part(t)
            if c.has("RATIONAL_B_SPLINE_CURVE"):
                W = [float(w) for w in c.part("RATIONAL_B_SPLINE_CURVE")[0]]
        P = np.array([self.point(r) for r in ctrl])
        kind = "WITH_KNOTS" if t == "B_SPLINE_CURVE_WITH_KNOTS" else t[:-len("_CURVE")]
        try:
            U = sf.knot_vector(kind, deg, len(P), *(rest[:2] if kind == "WITH_KNOTS" else ()))
            if W is not None and len(W) != len(P):
                raise CadError(f"{len(W)} weights for {len(P)} control points")
            return sf.BSplineCurve(deg, P, U, W)
        except CadError as e:
            raise CadError(f"#{c.id}: {e}") from None

    def edge_points(self, ref) -> tuple[np.ndarray, str, bool]:
        """Points from the edge's start vertex to its end vertex (file units), the curve type, straight?"""
        key = int(ref)
        if key in self.edge_cache:
            return self.edge_cache[key]
        e = self.get(ref, "EDGE_CURVE")
        _, v1, v2, curve, same = e.part("EDGE_CURVE")
        p1, p2 = self.point(v1), self.point(v2)
        same = same != Enum("F")
        C = self.curve(curve)
        if isinstance(C, sf.Line):
            pts = np.array([p1, p2])
        elif isinstance(C, sf.Polyline):
            pts = C.P if same else C.P[::-1]
        else:
            closed = int(v1) == int(v2) or np.linalg.norm(p1 - p2) <= 1e-9 * max(1.0, float(np.abs(p1).max()))
            pts = self._sample(C, p1, p2, same, closed)
        out = (pts, C.kind, C.straight)
        self.edge_cache[key] = out
        return out

    def _sample(self, C: sf.Curve, p1, p2, same: bool, closed: bool) -> np.ndarray:
        t1, t2 = (float(x) for x in C.param(np.array([p1, p2])))
        if C.period:
            P = C.period
            if closed:
                t2 = t1 + (P if same else -P)
            elif same:
                t2 = t1 + (t2 - t1) % P
            else:
                t2 = t1 - (t1 - t2) % P
        elif closed:
            t1, t2 = C.domain if same else C.domain[::-1]
        if isinstance(C, sf.BSplineCurve):
            frac = abs(t2 - t1) / max(C.domain[1] - C.domain[0], 1e-300)
            n = max(4, int(math.ceil(max(32, 8 * C.n) * frac * self.refine)))
        else:
            n = max(2, int(math.ceil(abs(t2 - t1) / (2 * math.pi) * SEG_PER_TURN * self.refine)))
        pts = C.ev(np.linspace(t1, t2, n + 1))[0]
        pts[0], pts[-1] = p1, p2
        return pts

    def loop_chunks(self, ref) -> tuple[list, set]:
        lp = self.get(ref)
        if lp.has("POLY_LOOP"):
            pts = np.array([self.point(r) for r in lp.part("POLY_LOOP")[1]])
            return [(np.vstack([pts, pts[:1]]), None, True)], {"LINE"}
        if lp.has("VERTEX_LOOP"):
            return [(np.array([self.point(lp.part("VERTEX_LOOP")[1])]), None, False)], set()
        if not lp.has("EDGE_LOOP"):
            raise CadError(f"#{lp.id}: loop {lp.type} is not understood")
        chunks, kinds = [], set()
        for oe_ref in lp.part("EDGE_LOOP")[1]:
            oe = self.get(oe_ref, "ORIENTED_EDGE")
            _, _, _, edge, orient = oe.part("ORIENTED_EDGE")
            pts, kind, straight = self.edge_points(edge)
            kinds.add(kind)
            if orient == Enum("F"):
                pts = pts[::-1]
            chunks.append((pts, int(edge), straight))
        if not chunks:
            raise CadError(f"#{lp.id}: EDGE_LOOP without edges")
        return chunks, kinds

    # ---- surfaces ----
    def surface(self, ref, unit: float, angle: float) -> tuple[str, dict]:
        """(type, params in mm) of an analytic surface; params are {} for the others."""
        s = self.get(ref)
        t = _type(s, "B_SPLINE_SURFACE")
        if t == "RECTANGULAR_TRIMMED_SURFACE":
            return self.surface(s.args[1], unit, angle)
        if t in ("PLANE",) + CURVED:
            o, x, y, z = self.placement(s.args[1])
            p = {"origin": o * unit, "x": x, "y": y, "z": z}
            if t != "PLANE":
                p["radius"] = float(s.args[2]) * unit
            if t == "CONICAL_SURFACE":
                p["semi_angle"] = float(s.args[3]) * angle
            if t in ("TOROIDAL_SURFACE", "DEGENERATE_TOROIDAL_SURFACE"):
                p["radius2"] = float(s.args[3]) * unit
                t = "TOROIDAL_SURFACE"
            return t, p
        if t.startswith("B_SPLINE_SURFACE") or t in ("BEZIER_SURFACE", "UNIFORM_SURFACE", "QUASI_UNIFORM_SURFACE"):
            t = "B_SPLINE_SURFACE"
        return t, {}

    def surf(self, ref, angle: float) -> sf.Surface | None:
        """The surface in file units (None for a plane)."""
        key = int(ref)
        if key not in self.surf_cache:
            s = self.get(ref)
            try:
                self.surf_cache[key] = self._surf(s, angle)
            except CadError as e:
                msg = str(e)
                raise CadError(msg if msg.startswith("#") else f"#{s.id}: {msg}") from None
        return self.surf_cache[key]

    def _surf(self, s: Entity, angle: float):
        t = _type(s, "B_SPLINE_SURFACE")
        a = s.args if len(s.parts) == 1 else None
        if t == "RECTANGULAR_TRIMMED_SURFACE":
            return self.surf(a[1], angle)
        if t == "PLANE":
            return None
        if t in CURVED:
            o, x, y, z = self.placement(a[1])
            r = float(a[2])
            if t == "CYLINDRICAL_SURFACE":
                return sf.Cylinder(o, x, y, z, r)
            if t == "CONICAL_SURFACE":
                return sf.Cone(o, x, y, z, r, float(a[3]) * angle)
            if t == "SPHERICAL_SURFACE":
                return sf.Sphere(o, x, y, z, r)
            return sf.Torus(o, x, y, z, r, float(a[3]))
        if t == "SURFACE_OF_REVOLUTION":
            ax = self.get(a[2], "AXIS1_PLACEMENT")
            _, loc, axis = ax.part("AXIS1_PLACEMENT")
            d = self.direction(axis) if axis is not None else np.array([0.0, 0.0, 1.0])
            return sf.Revolution(self.curve(a[1]), self.point(loc), d)
        if t == "SURFACE_OF_LINEAR_EXTRUSION":
            return sf.Extrusion(self.curve(a[1]), self.vector(a[2]))
        if t in ("B_SPLINE_SURFACE", "B_SPLINE_SURFACE_WITH_KNOTS", "BEZIER_SURFACE", "UNIFORM_SURFACE",
                 "QUASI_UNIFORM_SURFACE"):
            return self._bspline_surface(s)
        raise CadError(f"#{s.id}: surface {t} is not read")

    def _bspline_surface(self, s: Entity) -> sf.BSplineSurface:
        W = None
        kinds = ("B_SPLINE_SURFACE_WITH_KNOTS", "BEZIER_SURFACE", "UNIFORM_SURFACE", "QUASI_UNIFORM_SURFACE")
        if len(s.parts) == 1:
            t, a = s.parts[0][0], s.args
            pu, pv, ctrl, rest = int(a[1]), int(a[2]), a[3], a[8:]
        else:
            bs = s.part("B_SPLINE_SURFACE")
            pu, pv, ctrl = int(bs[0]), int(bs[1]), bs[2]
            t = next((p[0] for p in s.parts if p[0] in kinds), None)
            if t is None:
                raise CadError(f"#{s.id}: a B_SPLINE_SURFACE without knots is not read")
            rest = s.part(t)
            if s.has("RATIONAL_B_SPLINE_SURFACE"):
                W = np.array([[float(w) for w in row] for row in s.part("RATIONAL_B_SPLINE_SURFACE")[0]])
        P = np.array([[self.point(r) for r in row] for row in ctrl])
        if P.ndim != 3:
            raise CadError(f"#{s.id}: B-spline surface control points are not a full grid")
        nu, nv = P.shape[:2]
        kind = "WITH_KNOTS" if t == "B_SPLINE_SURFACE_WITH_KNOTS" else t[:-len("_SURFACE")]
        try:
            if kind == "WITH_KNOTS":
                Uu = sf.knot_vector(kind, pu, nu, rest[0], rest[2])
                Uv = sf.knot_vector(kind, pv, nv, rest[1], rest[3])
            else:
                Uu, Uv = sf.knot_vector(kind, pu, nu), sf.knot_vector(kind, pv, nv)
            return sf.BSplineSurface(pu, pv, P, Uu, Uv, W)
        except CadError as e:
            raise CadError(f"#{s.id}: {e}") from None


# ---- solids ----

def _shell_faces(f: _File, shell_ref, angle: float) -> list[_LocalFace]:
    sh = f.get(shell_ref)
    if sh.has("ORIENTED_CLOSED_SHELL"):
        _, _, inner, orient = sh.part("ORIENTED_CLOSED_SHELL")
        faces = _shell_faces(f, inner, angle)
        if orient == Enum("F"):
            faces = [_LocalFace(fc.id, fc.surface, fc.surf_ref, fc.surf, [_reverse(c) for c in fc.loops],
                                not fc.same_sense, fc.edge_kinds) for fc in faces]
        return faces
    if not (sh.has("CLOSED_SHELL") or sh.has("OPEN_SHELL")):
        raise CadError(f"#{sh.id}: shell {sh.type} is not understood")
    out = []
    for face_ref in sh.args[1]:
        fe = f.get(face_ref)
        if fe.has("ADVANCED_FACE") or fe.has("FACE_SURFACE"):
            args = fe.part("ADVANCED_FACE" if fe.has("ADVANCED_FACE") else "FACE_SURFACE")
            bounds, surf_ref, same = args[1], args[2], args[3] != Enum("F")
            kind = f.surface(surf_ref, 1.0, angle)[0]
        elif fe.has("FACE"):
            bounds, kind, surf_ref, same = fe.part("FACE")[1], "PLANE", None, True
        else:
            raise CadError(f"#{fe.id}: face {fe.type} is not understood")
        loops, kinds = [], []
        for b_ref in bounds:
            b = f.get(b_ref, "FACE_BOUND", "FACE_OUTER_BOUND")
            bargs = b.parts[0][1]
            try:
                chunks, ks = f.loop_chunks(bargs[1])
            except CadError as e:
                raise CadError(f"face #{fe.id}: {e}") from None
            if bargs[2] == Enum("F"):
                chunks = _reverse(chunks)
            loops.append(chunks)
            kinds.append(ks)
        out.append(_LocalFace(fe.id, kind, surf_ref, None, loops, same, kinds))
    return out


def _reverse(chunks: list) -> list:
    return [(p[::-1], k, s) for p, k, s in chunks[::-1]]


def _local_integrals(f: _File, faces: list[_LocalFace], angle: float) -> tuple[np.ndarray, str, np.ndarray]:
    """[V, Sx, Sy, Sz] of the shell in file units and coordinates, the method, sample points."""
    pts = np.vstack([L for fc in faces for L in fc.flat_loops() if len(L)])
    about = (pts.min(axis=0) + pts.max(axis=0)) / 2
    total = np.zeros(4)
    method = "exact"
    extra = []
    for fc in faces:
        if any(k - set(STRAIGHT) for k in fc.edge_kinds):
            method = "approximate"
        if fc.surface == "PLANE":
            loops = [L - about for L in fc.flat_loops()]
            if loops and len(loops[0]) >= 3:
                total += plane_integrals(loops)
            continue
        method = "approximate"
        S = f.surf(fc.surf_ref, angle)
        try:
            got, smp = curved_face(S, fc.loops, fc.same_sense, about, f.refine)
        except CadError as e:
            raise CadError(f"face #{fc.id}: {e}") from None
        total += got
        extra.append(smp)
    total[1:] += total[0] * about
    return total, method, np.vstack(extra) if extra else np.zeros((0, 3))


def _solid(f: _File, ent: Entity, M: np.ndarray, unit: float, angle: float, product: str) -> Solid:
    t = ent.parts[0][0]
    args = ent.args
    name = args[0] if isinstance(args[0], str) and args[0] else product
    key = (ent.id, unit, angle)
    if key not in f.solid_cache:
        faces = _shell_faces(f, args[1], angle)
        void_faces = [_shell_faces(f, v, angle) for v in args[2]] if t == "BREP_WITH_VOIDS" else []
        notes: list = []
        try:
            total, method, extra = _local_integrals(f, faces, angle)
            for vf in void_faces:
                vt, vm, ve = _local_integrals(f, vf, angle)
                if vt[0] > 0:  # a void shell given outward: subtract it
                    vt = -vt
                total = total + vt
                if vm != "exact":
                    method = "approximate"
            if total[0] < 0:
                notes.append("the shell's faces point inward; volume taken as positive")
                total = -total
            if not total[0] > 0:
                raise CadError("the closed shell encloses no volume")
            local = (total, method, extra)
        except CadError as e:
            local = (None, "bounding_box", np.zeros((0, 3)))
            notes.append(f"volume and centre are the bounding box's, not computed from the surface ({e})")
        f.solid_cache[key] = (faces, void_faces, local, notes)
    faces, void_faces, (total, method, extra), notes = f.solid_cache[key]
    R, tr = M[:3, :3], M[:3, 3]
    L = R * unit
    world = _world_faces(f, faces, R, tr, unit, angle)
    pts = np.vstack([Lp for fc in world for Lp in fc.loops if len(Lp)])
    if len(extra):
        pts = np.vstack([pts, extra @ L.T + tr])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    if total is None:
        V = float(np.prod(hi - lo))
        center = (lo + hi) / 2
    else:
        det = abs(float(np.linalg.det(L)))
        V = det * float(total[0])
        center = (L @ (total[1:] / total[0])) + tr
    return Solid(name=name, bbox_min=[float(x) for x in lo], bbox_max=[float(x) for x in hi], volume_mm3=V,
                 center_mm=[float(x) for x in center], method=method, notes=list(notes), entity_id=ent.id,
                 product=product, transform=M, unit_mm=unit, angle_rad=angle, faces=world, voids=len(void_faces))


def _world_faces(f: _File, faces: list[_LocalFace], R, t, unit: float, angle: float) -> list[Face]:
    s = float(np.cbrt(abs(np.linalg.det(R)))) or 1.0
    Rn = R / s
    out = []
    for fc in faces:
        loops = [(L * unit) @ R.T + t for L in fc.flat_loops()]
        params = {}
        if fc.surf_ref is not None:
            kind, params = f.surface(fc.surf_ref, unit, angle)
            if params:
                params = dict(params)
                params["origin"] = params["origin"] @ R.T + t
                for k in ("x", "y", "z"):
                    params[k] = Rn @ params[k]
                for k in ("radius", "radius2"):
                    if k in params:
                        params[k] *= s
        out.append(Face(fc.surface, params, loops, fc.same_sense, fc.edge_kinds))
    return out


def _rep_tree(f: _File):
    """Representations, their solids, the links between them and the product of each."""
    E = f.e
    reps = {i: e for i, e in E.items() if any(p[0].endswith("REPRESENTATION") and len(p[1]) >= 3
                                               and isinstance(p[1][1], list) for p in e.parts)
            and not e.has("SHAPE_DEFINITION_REPRESENTATION") and not e.has("CONTEXT_DEPENDENT_SHAPE_REPRESENTATION")}

    def rep_args(e: Entity):
        for p in e.parts:
            if p[0].endswith("REPRESENTATION") and len(p[1]) >= 3 and isinstance(p[1][1], list):
                return p[1]
        if e.has("REPRESENTATION"):
            return e.part("REPRESENTATION")
        raise CadError(f"#{e.id}: not a representation")

    # product name and product definition per representation
    pd_of_pds = {}
    rep_pd = {}
    pd_name = {}
    for i, e in E.items():
        if e.has("PRODUCT_DEFINITION"):
            try:
                pdf = f.get(e.part("PRODUCT_DEFINITION")[2])
                prod = f.get(pdf.parts[0][1][2])
                pd_name[i] = str(prod.parts[0][1][1] or prod.parts[0][1][0] or "")
            except (CadError, IndexError):
                pd_name[i] = ""
    for i, e in E.items():
        if e.has("PRODUCT_DEFINITION_SHAPE"):
            d = e.part("PRODUCT_DEFINITION_SHAPE")[2]
            if isinstance(d, Ref):
                pd_of_pds[i] = int(d)
    for i, e in E.items():
        if e.has("SHAPE_DEFINITION_REPRESENTATION"):
            pds, rep = e.part("SHAPE_DEFINITION_REPRESENTATION")
            pd = pd_of_pds.get(int(pds))
            if pd in pd_name:
                rep_pd[int(rep)] = pd
    same: dict[int, set] = {}
    children: dict[int, list] = {}
    child_reps = set()
    nauo_child = {}
    for i, e in E.items():
        if e.has("NEXT_ASSEMBLY_USAGE_OCCURRENCE"):
            a = e.part("NEXT_ASSEMBLY_USAGE_OCCURRENCE")
            nauo_child[i] = (int(a[3]), int(a[4]), str(a[1] or a[0] or ""))
    for i, e in E.items():
        if e.has("REPRESENTATION_RELATIONSHIP") and not e.has("REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION"):
            _, _, r1, r2 = e.part("REPRESENTATION_RELATIONSHIP")
            same.setdefault(int(r1), set()).add(int(r2))
            same.setdefault(int(r2), set()).add(int(r1))
        elif e.has("SHAPE_REPRESENTATION_RELATIONSHIP") and len(e.parts) == 1:
            _, _, r1, r2 = e.args
            same.setdefault(int(r1), set()).add(int(r2))
            same.setdefault(int(r2), set()).add(int(r1))
    for i, e in E.items():
        if not e.has("CONTEXT_DEPENDENT_SHAPE_REPRESENTATION"):
            continue
        rr_ref, pds_ref = e.part("CONTEXT_DEPENDENT_SHAPE_REPRESENTATION")
        rr = f.get(rr_ref)
        if not rr.has("REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION"):
            continue
        _, _, r1, r2 = rr.part("REPRESENTATION_RELATIONSHIP")
        r1, r2 = int(r1), int(r2)
        occ = pd_of_pds.get(int(pds_ref))
        parent_pd, child_pd, occ_name = nauo_child.get(occ, (None, None, ""))
        child, parent = r1, r2
        if rep_pd.get(r2) == child_pd and rep_pd.get(r1) == parent_pd and child_pd is not None:
            child, parent = r2, r1
        xf = f.get(rr.part("REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION")[0])
        children.setdefault(parent, []).append((child, ("idt", xf, child, parent), occ_name))
        child_reps.add(child)
    for i, e in reps.items():
        for it in rep_args(e)[1]:
            ie = E.get(int(it)) if isinstance(it, Ref) else None
            if ie is not None and ie.has("MAPPED_ITEM"):
                _, rmap, target = ie.part("MAPPED_ITEM")
                origin, mapped = f.get(rmap).parts[0][1][:2]
                children.setdefault(i, []).append((int(mapped), ("map", origin, target, i, int(mapped)), str(ie.args[0] or "")))
                child_reps.add(int(mapped))
    return reps, rep_args, same, children, child_reps, rep_pd, pd_name


def read_step(text: str, refine: int = 1) -> list[Solid]:
    """Every solid in the file, in assembly coordinates (mm). refine multiplies the sampling of edges
    and the Gauss points of curved faces (2 halves every step)."""
    return read_step_entities(text, refine)[1]


def read_step_entities(text: str, refine: int = 1) -> tuple[dict, list[Solid]]:
    """(entities by id, solids): the parsed file too, for copying solids unchanged."""
    f = _File(text, refine)
    reps, rep_args, same, children, child_reps, rep_pd, pd_name = _rep_tree(f)

    def group(r):
        out, stack = set(), [r]
        while stack:
            x = stack.pop()
            if x in out:
                continue
            out.add(x)
            stack += list(same.get(x, ()))
        return out

    def units_of(r):
        e = f.e.get(r)
        if e is None:
            return 1.0, 1.0
        return f.units(rep_args(e)[2])

    def xf_matrix(kind_args) -> np.ndarray:
        if kind_args[0] == "idt":
            _, xf, child, parent = kind_args
            if xf.has("ITEM_DEFINED_TRANSFORMATION"):
                _, _, i1, i2 = xf.part("ITEM_DEFINED_TRANSFORMATION")
                cg, pg = group(child), group(parent)
                in_child = lambda it: any(isinstance(x, Ref) and int(x) == int(it) for r in cg if r in reps for x in rep_args(reps[r])[1])
                in_parent = lambda it: any(isinstance(x, Ref) and int(x) == int(it) for r in pg if r in reps for x in rep_args(reps[r])[1])
                ci, pi = i1, i2
                if in_parent(i1) and in_child(i2) and not (in_child(i1) and in_parent(i2)):
                    ci, pi = i2, i1
                uc, up = units_of(child)[0], units_of(parent)[0]
                Mc = f.placement_matrix(ci, uc)
                Mp = f.placement_matrix(pi, up)
                return Mp @ np.linalg.inv(Mc)
            # a CARTESIAN_TRANSFORMATION_OPERATOR as the transformation itself
            return f.placement_matrix(xf, units_of(parent)[0])
        _, origin, target, parent, child = kind_args
        Mo = f.placement_matrix(origin, units_of(child)[0])
        Mt = f.placement_matrix(target, units_of(parent)[0])
        return Mt @ np.linalg.inv(Mo)

    solids: list[Solid] = []
    roots = [r for r in reps if r not in child_reps and not (group(r) & child_reps)]
    done_groups = set()

    def walk(r: int, M: np.ndarray, product: str, depth: int, seen: frozenset):
        if depth > 32 or r in seen:
            raise CadError(f"assembly loops back at representation #{r}")
        g = group(r)
        seen = seen | g
        for x in sorted(g):
            if x not in reps:
                continue
            name = pd_name.get(rep_pd.get(x), "") or product
            unit, angle = units_of(x)
            for it in rep_args(reps[x])[1]:
                ie = f.e.get(int(it)) if isinstance(it, Ref) else None
                if ie is not None and len(ie.parts) == 1 and ie.parts[0][0] in SOLID_TYPES:
                    try:
                        solids.append(_solid(f, ie, M, unit, angle, name))
                    except CadError as e:
                        raise CadError(f"solid #{ie.id} ({ie.args[0] or name}): {e}") from None
            for child, how, occ in children.get(x, []):
                walk(child, M @ xf_matrix(how), pd_name.get(rep_pd.get(child), "") or occ or name, depth + 1, seen)

    for r in sorted(roots):
        key = frozenset(group(r))
        if key in done_groups:
            continue
        done_groups.add(key)
        walk(r, np.eye(4), pd_name.get(rep_pd.get(r), ""), 0, frozenset())
    if not solids:
        raise CadError("the file has no solids (MANIFOLD_SOLID_BREP, BREP_WITH_VOIDS or FACETED_BREP)")
    return f.e, solids

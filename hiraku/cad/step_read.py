"""Read solids from STEP text: names, bounding boxes, volumes and centres in mm.

read_step(text) -> [Solid]

- Solids: MANIFOLD_SOLID_BREP, BREP_WITH_VOIDS (voids subtracted) and FACETED_BREP found in the
  items of any representation, placed into the top assembly's coordinates through
  NEXT_ASSEMBLY_USAGE_OCCURRENCE + CONTEXT_DEPENDENT_SHAPE_REPRESENTATION (representation
  relationships with ITEM_DEFINED_TRANSFORMATION), plain SHAPE_REPRESENTATION_RELATIONSHIP and
  MAPPED_ITEM. A product used twice gives two solids.
- Units: the length unit of each representation's context (mm, m, inch and other SI prefixes or
  conversion-based units) becomes mm; plane angles in degrees are read as such.
- Volume and centre by the divergence theorem over the closed shell:
  planar faces exactly from their polygon loops (method "exact"); faces on cylinders, cones,
  spheres and tori, and circular or spline edges, by tessellating the edges (256 segments per turn)
  and integrating in the surface's parameter space (Green's theorem), method "approximate";
  a solid with any other surface (B-spline, revolution, extrusion, offset) gets the volume and
  centre of its bounding box, method "bounding_box" (not computed from the surface).
- The bounding box comes from the tessellated edges plus sample points inside each curved face.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from hiraku.cad import CadError
from hiraku.cad.part21 import Entity, Enum, Ref, Typed, parse

SEG_PER_TURN = 256
SOLID_TYPES = ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS", "FACETED_BREP")
CURVED = ("CYLINDRICAL_SURFACE", "CONICAL_SURFACE", "SPHERICAL_SURFACE", "TOROIDAL_SURFACE",
          "DEGENERATE_TOROIDAL_SURFACE")
_GL_X, _GL_W = np.polynomial.legendre.leggauss(24)

SI_PREFIX = {None: 1.0, "MILLI": 1e-3, "CENTI": 1e-2, "DECI": 1e-1, "KILO": 1e3, "MICRO": 1e-6, "NANO": 1e-9,
             "DECA": 10.0, "HECTO": 100.0}


@dataclass
class Face:
    surface: str  # PLANE, CYLINDRICAL_SURFACE, ... (world mm)
    params: dict  # origin, z, x, y (unit), radius, radius2, semi_angle (rad)
    loops: list  # [np.ndarray (n, 3)] closed polylines, world mm, oriented as in the shell
    same_sense: bool
    edge_kinds: list = field(default_factory=list)  # per loop: set of curve types


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


class _File:
    def __init__(self, text: str):
        self.e: dict[int, Entity] = parse(text)
        self.edge_cache: dict = {}
        self.unit_cache: dict = {}

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

    # ---- edges ----
    def edge_points(self, ref) -> tuple[np.ndarray, str]:
        """Points from the edge's start vertex to its end vertex (file units), and the curve type."""
        key = int(ref)
        if key in self.edge_cache:
            return self.edge_cache[key]
        e = self.get(ref, "EDGE_CURVE")
        _, v1, v2, curve, same = e.part("EDGE_CURVE")
        p1, p2 = self.point(v1), self.point(v2)
        pts, kind = self._curve_points(curve, p1, p2, same != Enum("F"), e.id)
        self.edge_cache[key] = (pts, kind)
        return pts, kind

    def _curve_points(self, ref, p1, p2, same: bool, eid: int) -> tuple[np.ndarray, str]:
        c = self.get(ref)
        t = c.parts[0][0] if len(c.parts) == 1 else "B_SPLINE_CURVE"
        if t in ("SURFACE_CURVE", "SEAM_CURVE", "INTERSECTION_CURVE"):
            return self._curve_points(c.args[1], p1, p2, same, eid)
        if t == "TRIMMED_CURVE":
            return self._curve_points(c.args[1], p1, p2, same, eid)
        if t == "LINE" or t == "POLYLINE":
            if t == "POLYLINE":
                pts = np.array([self.point(r) for r in c.args[1]])
                if not same:
                    pts = pts[::-1]
                return pts, "POLYLINE"
            return np.array([p1, p2]), "LINE"
        if t in ("CIRCLE", "ELLIPSE"):
            o, x, y, z = self.placement(c.args[1])
            ra = float(c.args[2])
            rb = float(c.args[3]) if t == "ELLIPSE" else ra

            def ang(p):
                d = p - o
                return math.atan2((d @ y) / rb, (d @ x) / ra)

            a1, a2 = ang(p1), ang(p2)
            closed = np.linalg.norm(p1 - p2) <= 1e-9 * max(1.0, ra)
            if same:
                sweep = (a2 - a1) % (2 * math.pi)
                if closed or sweep < 1e-12:
                    sweep = 2 * math.pi if closed else sweep
            else:
                sweep = -((a1 - a2) % (2 * math.pi))
                if closed or sweep > -1e-12:
                    sweep = -2 * math.pi if closed else sweep
            n = max(2, int(math.ceil(abs(sweep) / (2 * math.pi) * SEG_PER_TURN)))
            a = a1 + sweep * np.linspace(0.0, 1.0, n + 1)
            pts = o + np.outer(ra * np.cos(a), x) + np.outer(rb * np.sin(a), y)
            pts[0], pts[-1] = p1, p2
            return pts, t
        if t in ("B_SPLINE_CURVE_WITH_KNOTS", "B_SPLINE_CURVE"):
            pts = self._bspline(c)
            i1 = int(np.argmin(np.linalg.norm(pts - p1, axis=1)))
            i2 = int(np.argmin(np.linalg.norm(pts - p2, axis=1)))
            if np.linalg.norm(p1 - p2) < 1e-9:
                seg = pts if same else pts[::-1]
            elif same:
                seg = pts[i1:i2 + 1] if i2 >= i1 else np.vstack([pts[i1:], pts[1:i2 + 1]])
            else:
                seg = pts[i2:i1 + 1][::-1] if i1 >= i2 else np.vstack([pts[i2:], pts[1:i1 + 1]])[::-1]
            seg = np.vstack([p1, seg[1:-1], p2]) if len(seg) >= 2 else np.array([p1, p2])
            return seg, "B_SPLINE_CURVE"
        return np.array([p1, p2]), f"UNKNOWN:{t}"

    def _bspline(self, c: Entity) -> np.ndarray:
        if len(c.parts) == 1:
            args = c.args
            deg, ctrl, mults, knots = int(args[1]), args[2], args[6], args[7]
            weights = None
        else:
            bc = c.part("B_SPLINE_CURVE")
            deg, ctrl = int(bc[0]), bc[1]
            kw = c.part("B_SPLINE_CURVE_WITH_KNOTS")
            mults, knots = kw[0], kw[1]
            weights = [float(w) for w in c.part("RATIONAL_B_SPLINE_CURVE")[0]] if c.has("RATIONAL_B_SPLINE_CURVE") else None
        P = np.array([self.point(r) for r in ctrl])
        U = np.repeat([float(k) for k in knots], [int(m) for m in mults])
        W = np.ones(len(P)) if weights is None else np.array(weights)
        n = len(P)
        if len(U) != n + deg + 1:
            raise CadError(f"#{c.id}: B-spline knots ({len(U)}) do not fit {n} points of degree {deg}")
        ts = np.linspace(U[deg], U[n], max(64, 16 * n))
        out = []
        H = np.column_stack([P * W[:, None], W])
        for t in ts:
            k = min(max(int(np.searchsorted(U, t, side="right")) - 1, deg), n - 1)
            d = [H[j + k - deg].copy() for j in range(deg + 1)]
            for r in range(1, deg + 1):
                for j in range(deg, r - 1, -1):
                    i = j + k - deg
                    den = U[i + deg + 1 - r] - U[i]
                    al = 0.0 if den == 0 else (t - U[i]) / den
                    d[j] = (1 - al) * d[j - 1] + al * d[j]
            out.append(d[deg][:3] / d[deg][3])
        return np.array(out)

    def loop_points(self, ref) -> tuple[np.ndarray, set]:
        lp = self.get(ref)
        if lp.has("POLY_LOOP"):
            return np.array([self.point(r) for r in lp.part("POLY_LOOP")[1]]), {"LINE"}
        if lp.has("VERTEX_LOOP"):
            return np.array([self.point(lp.part("VERTEX_LOOP")[1])]), set()
        if not lp.has("EDGE_LOOP"):
            raise CadError(f"#{lp.id}: loop {lp.type} is not understood")
        chunks, kinds = [], set()
        for oe_ref in lp.part("EDGE_LOOP")[1]:
            oe = self.get(oe_ref, "ORIENTED_EDGE")
            _, _, _, edge, orient = oe.part("ORIENTED_EDGE")
            pts, kind = self.edge_points(edge)
            kinds.add(kind)
            if orient == Enum("F"):
                pts = pts[::-1]
            chunks.append(pts[:-1])
        if not chunks:
            raise CadError(f"#{lp.id}: EDGE_LOOP without edges")
        return np.vstack(chunks), kinds

    def surface(self, ref, unit: float, angle: float) -> tuple[str, dict]:
        s = self.get(ref)
        t = s.parts[0][0] if len(s.parts) == 1 else "B_SPLINE_SURFACE"
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
        return t, {}


# ---- volume pieces ----

def _plane_integrals(loops: list[np.ndarray]) -> np.ndarray:
    """[V, Sx, Sy, Sz] of the cones from the origin to a planar face (signed by loop orientation)."""
    q = loops[0][0]
    out = np.zeros(4)
    for L in loops:
        if len(L) < 3:
            continue
        a = L
        b = np.roll(L, -1, axis=0)
        qa = np.broadcast_to(q, a.shape)
        vol = np.einsum("ij,ij->i", qa, np.cross(a, b)) / 6.0
        out[0] += vol.sum()
        out[1:] += ((qa + a + b) / 4.0 * vol[:, None]).sum(axis=0)
    return out


class _Param:
    """x(u, v) of a curved surface in world mm, its inverse, and periodicity."""

    def __init__(self, kind: str, p: dict):
        self.kind, self.p = kind, p
        self.o, self.X, self.Y, self.Z = p["origin"], p["x"], p["y"], p["z"]
        self.r = p.get("radius", 0.0)
        self.v_periodic = kind == "TOROIDAL_SURFACE"

    def xyz(self, u, v):
        u, v = np.asarray(u, float), np.asarray(v, float)
        cu, su = np.cos(u)[..., None], np.sin(u)[..., None]
        X, Y, Z, o = self.X, self.Y, self.Z, self.o
        if self.kind == "CYLINDRICAL_SURFACE":
            return o + self.r * (cu * X + su * Y) + v[..., None] * Z
        if self.kind == "CONICAL_SURFACE":
            rr = (self.r + v * math.tan(self.p["semi_angle"]))[..., None]
            return o + rr * (cu * X + su * Y) + v[..., None] * Z
        if self.kind == "SPHERICAL_SURFACE":
            cv, sv = np.cos(v)[..., None], np.sin(v)[..., None]
            return o + self.r * (cv * (cu * X + su * Y) + sv * Z)
        R, r = self.r, self.p["radius2"]
        cv, sv = np.cos(v)[..., None], np.sin(v)[..., None]
        return o + (R + r * cv) * (cu * X + su * Y) + r * sv * Z

    def normal_j(self, u, v):
        """x_u x x_v (outward, length = area element)."""
        h = 1e-6
        xu = (self.xyz(u + h, v) - self.xyz(u - h, v)) / (2 * h)
        xv = (self.xyz(u, v + h) - self.xyz(u, v - h)) / (2 * h)
        return np.cross(xu, xv)

    def uv(self, pts: np.ndarray) -> np.ndarray:
        d = pts - self.o
        dx, dy, dz = d @ self.X, d @ self.Y, d @ self.Z
        u = np.arctan2(dy, dx)
        if self.kind in ("CYLINDRICAL_SURFACE", "CONICAL_SURFACE"):
            v = dz
        elif self.kind == "SPHERICAL_SURFACE":
            v = np.arcsin(np.clip(dz / self.r, -1, 1))
        else:
            v = np.arctan2(dz, np.hypot(dx, dy) - self.r)
        return np.column_stack([u, v])

    def integrand(self, u, v):
        """[ (1/3) x.N, (1/4) x (x.N) ] per point (the fields whose divergence is 1 and x)."""
        x = self.xyz(u, v)
        n = self.normal_j(u, v)
        xn = np.einsum("...i,...i->...", x, n)
        return np.concatenate([(xn / 3.0)[..., None], x * (xn / 4.0)[..., None]], axis=-1)

    def F(self, u, v):
        """Integral of the integrand over u from 0 (Gauss-Legendre, in pieces of at most pi/2)."""
        u, v = np.asarray(u, float), np.asarray(v, float)
        pieces = max(1, int(np.ceil(np.max(np.abs(u)) / (math.pi / 2)))) if u.size else 1
        total = np.zeros(u.shape + (4,))
        for k in range(pieces):
            a, b = u * k / pieces, u * (k + 1) / pieces
            mid, half = (a + b) / 2, (b - a) / 2
            for xg, wg in zip(_GL_X, _GL_W):
                total += wg * half[..., None] * self.integrand(mid + half * xg, v)
        return total


def _unwrap(a: np.ndarray, start: float | None = None) -> np.ndarray:
    out = np.unwrap(a)
    if start is not None:
        out += 2 * math.pi * round((start - out[0]) / (2 * math.pi))
    return out


def _curved_integrals(kind: str, params: dict, loops: list[np.ndarray], same_sense: bool) -> tuple[np.ndarray, list]:
    S = _Param(kind, params)
    total = np.zeros(4)
    open_loops = []
    uv_loops = []
    for L in loops:
        if len(L) < 2:
            continue  # a vertex loop (apex or pole)
        uv = S.uv(np.vstack([L, L[:1]]))
        uv[:, 0] = _unwrap(uv[:, 0])
        if S.v_periodic:
            uv[:, 1] = _unwrap(uv[:, 1])
            if abs(uv[-1, 1] - uv[0, 1]) > math.pi:
                raise CadError("a torus face that goes round both ways is not integrated")
        k = int(round((uv[-1, 0] - uv[0, 0]) / (2 * math.pi)))
        a, b = uv[:-1], uv[1:]
        m = (a + b) / 2
        dv = (b[:, 1] - a[:, 1])
        Fa, Fm, Fb = S.F(a[:, 0], a[:, 1]), S.F(m[:, 0], m[:, 1]), S.F(b[:, 0], b[:, 1])
        total += ((Fa + 4 * Fm + Fb) / 6.0 * dv[:, None]).sum(axis=0)
        if k:
            open_loops.append((k, float(uv[0, 1])))
        uv_loops.append(uv)
    if open_loops:
        ksum = sum(k for k, _ in open_loops)
        if ksum == 0:
            vref = open_loops[0][1]
        elif kind == "SPHERICAL_SURFACE":
            side = 1 if (open_loops[0][0] > 0) == same_sense else -1
            vref = side * math.pi / 2
        elif kind == "CONICAL_SURFACE":
            vref = -S.r / math.tan(S.p["semi_angle"])
        else:
            raise CadError(f"a {kind} face bounded by loops that go round it unevenly is not integrated")

        def Pv(v):  # full-turn integral at v
            return S.F(np.full(np.shape(v), 2 * math.pi), v)

        for k, v in open_loops:
            vs = vref + (v - vref) * (_GL_X + 1) / 2
            Q = (_GL_W[:, None] * Pv(vs)).sum(axis=0) * (v - vref) / 2
            total -= k * Q
    return total, uv_loops


def _inside(poly: np.ndarray, pts: np.ndarray) -> np.ndarray:
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    x1, y1 = poly[:-1, 0][None], poly[:-1, 1][None]
    x2, y2 = poly[1:, 0][None], poly[1:, 1][None]
    cond = (y1 > y) != (y2 > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
    return (cond & (x < xc)).sum(axis=1) % 2 == 1


def _surface_samples(kind: str, params: dict, uv_loops: list) -> np.ndarray:
    if not uv_loops:
        return np.zeros((0, 3))
    S = _Param(kind, params)
    allp = np.vstack(uv_loops)
    u0, v0 = allp.min(axis=0)
    u1, v1 = allp.max(axis=0)
    if u1 - u0 > 2 * math.pi - 1e-6 and any(abs(uv[-1, 0] - uv[0, 0]) > 1 for uv in uv_loops):
        g = np.stack(np.meshgrid(np.linspace(u0, u1, 25), np.linspace(v0, v1, 25)), -1).reshape(-1, 2)
    else:
        g = np.stack(np.meshgrid(np.linspace(u0, u1, 25), np.linspace(v0, v1, 25)), -1).reshape(-1, 2)
        ins = np.zeros(len(g), bool)
        for uv in uv_loops:
            ins ^= _inside(uv, g)
        g = g[ins]
    return S.xyz(g[:, 0], g[:, 1]).reshape(-1, 3) if len(g) else np.zeros((0, 3))


# ---- solids ----

def _shell_faces(f: _File, shell_ref, M: np.ndarray, unit: float, angle: float) -> list[Face]:
    sh = f.get(shell_ref)
    if sh.has("ORIENTED_CLOSED_SHELL"):
        _, _, inner, orient = sh.part("ORIENTED_CLOSED_SHELL")
        faces = _shell_faces(f, inner, M, unit, angle)
        if orient == Enum("F"):
            for fc in faces:
                fc.loops = [L[::-1] for L in fc.loops]
                fc.same_sense = not fc.same_sense
        return faces
    if not (sh.has("CLOSED_SHELL") or sh.has("OPEN_SHELL")):
        raise CadError(f"#{sh.id}: shell {sh.type} is not understood")
    R, t = M[:3, :3], M[:3, 3]
    s = float(np.cbrt(abs(np.linalg.det(R)))) or 1.0
    Rn = R / s
    out = []
    for face_ref in sh.args[1]:
        fe = f.get(face_ref)
        if fe.has("ADVANCED_FACE") or fe.has("FACE_SURFACE"):
            args = fe.part("ADVANCED_FACE" if fe.has("ADVANCED_FACE") else "FACE_SURFACE")
            bounds, surf, same = args[1], args[2], args[3] != Enum("F")
            kind, params = f.surface(surf, unit, angle)
        elif fe.has("FACE"):
            bounds, kind, params, same = fe.part("FACE")[1], "PLANE", {}, True
        else:
            raise CadError(f"#{fe.id}: face {fe.type} is not understood")
        loops, kinds = [], []
        for b_ref in bounds:
            b = f.get(b_ref, "FACE_BOUND", "FACE_OUTER_BOUND")
            bargs = b.parts[0][1]
            try:
                pts, ks = f.loop_points(bargs[1])
            except CadError as e:
                raise CadError(f"face #{fe.id}: {e}") from None
            if bargs[2] == Enum("F"):
                pts = pts[::-1]
            loops.append((pts * unit) @ R.T + t)
            kinds.append(ks)
        if params:
            params = dict(params)
            params["origin"] = params["origin"] @ R.T + t
            for k in ("x", "y", "z"):
                params[k] = Rn @ params[k]
            for k in ("radius", "radius2"):
                if k in params:
                    params[k] *= s
        out.append(Face(kind, params, loops, same, kinds))
    return out


def _integrate(faces: list[Face]) -> tuple[np.ndarray, str, list, np.ndarray]:
    total = np.zeros(4)
    method, notes = "exact", []
    extra = []
    for fc in faces:
        if any(k - {"LINE", "POLYLINE"} for k in fc.edge_kinds):
            method = "approximate"
        if fc.surface == "PLANE":
            if fc.loops and len(fc.loops[0]) >= 3:
                total += _plane_integrals(fc.loops)
        elif fc.surface in CURVED or fc.surface == "TOROIDAL_SURFACE":
            method = "approximate"
            got, uvl = _curved_integrals(fc.surface, fc.params, fc.loops, fc.same_sense)
            total += got
            extra.append(_surface_samples(fc.surface, fc.params, uvl))
        else:
            raise CadError(f"surface {fc.surface}")
    unknown = sorted({k for fc in faces for ks in fc.edge_kinds for k in ks if k.startswith("UNKNOWN:")})
    if unknown:
        notes.append("edges on curves not read, taken straight: " + ", ".join(u[8:] for u in unknown))
    return total, method, notes, np.vstack(extra) if extra else np.zeros((0, 3))


def _solid(f: _File, ent: Entity, M: np.ndarray, unit: float, angle: float, product: str) -> Solid:
    t = ent.parts[0][0]
    args = ent.args
    name = args[0] if isinstance(args[0], str) and args[0] else product
    faces = _shell_faces(f, args[1], M, unit, angle)
    void_faces = []
    if t == "BREP_WITH_VOIDS":
        for v in args[2]:
            void_faces.append(_shell_faces(f, v, M, unit, angle))
    pts = np.vstack([L for fc in faces for L in fc.loops])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    notes: list = []
    try:
        total, method, notes, extra = _integrate(faces)
        for vf in void_faces:
            vt, vm, vn, _ = _integrate(vf)
            if vt[0] > 0:  # a void shell given outward: subtract it
                vt = -vt
            total += vt
            notes += vn
            if vm != "exact":
                method = "approximate"
        if len(extra):
            lo, hi = np.minimum(lo, extra.min(axis=0)), np.maximum(hi, extra.max(axis=0))
        V = float(total[0])
        if V < 0:
            notes.append("the shell's faces point inward; volume taken as positive")
            V, total = -V, -total
        if V <= 0:
            raise CadError("the closed shell encloses no volume")
        center = total[1:] / total[0]
    except CadError as e:
        method = "bounding_box"
        V = float(np.prod(hi - lo))
        center = (lo + hi) / 2
        notes.append(f"volume and centre are the bounding box's, not computed from the surface ({e})")
    return Solid(name=name, bbox_min=[float(x) for x in lo], bbox_max=[float(x) for x in hi], volume_mm3=V,
                 center_mm=[float(x) for x in center], method=method, notes=notes, entity_id=ent.id, product=product,
                 transform=M, unit_mm=unit, angle_rad=angle, faces=faces, voids=len(void_faces))


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


def read_step(text: str) -> list[Solid]:
    """Every solid in the file, in assembly coordinates (mm)."""
    return read_step_entities(text)[1]


def read_step_entities(text: str) -> tuple[dict, list[Solid]]:
    """(entities by id, solids): the parsed file too, for copying solids unchanged."""
    f = _File(text)
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

"""Curved solids written by hand as STEP B-reps (ADVANCED_FACE), with their answers from formulas.

Each function returns (STEP text, volume mm3, centre mm). The faces follow the STEP rule: every
bound runs counter-clockwise seen from outside the solid (against the face's normal when
same_sense is .F.).
"""

from __future__ import annotations

import math

from tests.cad.handmade import HEAD, MM_CONTEXT, TAIL


class B:
    """Numbers entities one by one and writes them as STEP lines."""

    def __init__(self):
        self.n = 0
        self.lines: list[str] = []

    def add(self, text: str) -> int:
        self.n += 1
        self.lines.append(f"#{self.n}={text};")
        return self.n

    def pt(self, x, y, z):
        return self.add(f"CARTESIAN_POINT('',({float(x)!r},{float(y)!r},{float(z)!r}))")

    def dir(self, x, y, z):
        return self.add(f"DIRECTION('',({float(x)!r},{float(y)!r},{float(z)!r}))")

    def ax(self, o, z, x):
        return self.add(f"AXIS2_PLACEMENT_3D('',#{self.pt(*o)},#{self.dir(*z)},#{self.dir(*x)})")

    def vx(self, *p):
        return self.add(f"VERTEX_POINT('',#{self.pt(*p)})")

    def circle(self, o, z, x, r):
        return self.add(f"CIRCLE('',#{self.ax(o, z, x)},{float(r)!r})")

    def ellipse(self, o, z, x, a, b):
        return self.add(f"ELLIPSE('',#{self.ax(o, z, x)},{float(a)!r},{float(b)!r})")

    def line(self, p, d):
        n = math.sqrt(sum(c * c for c in d))
        vec = self.add(f"VECTOR('',#{self.dir(*d)},{n!r})")
        return self.add(f"LINE('',#{self.pt(*p)},#{vec})")

    def edge(self, v1, v2, curve, same=True):
        return self.add(f"EDGE_CURVE('',#{v1},#{v2},#{curve},{'.T.' if same else '.F.'})")

    def loop(self, *oriented):
        oes = [self.add(f"ORIENTED_EDGE('',*,*,#{e},{'.T.' if s else '.F.'})") for e, s in oriented]
        return self.add(f"EDGE_LOOP('',({','.join(f'#{i}' for i in oes)}))")

    def face(self, surf, same, outer, *holes):
        bounds = [self.add(f"FACE_OUTER_BOUND('',#{outer},.T.)")]
        bounds += [self.add(f"FACE_BOUND('',#{h},.T.)") for h in holes]
        return self.add(f"ADVANCED_FACE('',({','.join(f'#{b}' for b in bounds)}),#{surf},{'.T.' if same else '.F.'})")

    def vertex_loop(self, v):
        return self.add(f"VERTEX_LOOP('',#{v})")

    def plane(self, o, z, x):
        return self.add(f"PLANE('',#{self.ax(o, z, x)})")

    def solid(self, name, faces) -> str:
        shell = self.add(f"CLOSED_SHELL('',({','.join(f'#{f}' for f in faces)}))")
        brep = self.add(f"MANIFOLD_SOLID_BREP('{name}',#{shell})")
        self.add(f"ADVANCED_BREP_SHAPE_REPRESENTATION('',(#{brep}),#902)")
        return HEAD + MM_CONTEXT + "\n".join(self.lines) + "\n" + TAIL


Z, X, NZ = (0, 0, 1), (1, 0, 0), (0, 0, -1)


def tube(R=10.0, r=4.0, h=20.0):
    """A cylinder with a coaxial hole. The outer face has a seam line (one loop of four edges),
    the inner face is bounded by its two circles."""
    b = B()
    vb, vt = b.vx(R, 0, 0), b.vx(R, 0, h)
    wb, wt = b.vx(r, 0, 0), b.vx(r, 0, h)
    cb, ct = b.edge(vb, vb, b.circle((0, 0, 0), Z, X, R)), b.edge(vt, vt, b.circle((0, 0, h), Z, X, R))
    ib, it = b.edge(wb, wb, b.circle((0, 0, 0), Z, X, r)), b.edge(wt, wt, b.circle((0, 0, h), Z, X, r))
    seam = b.edge(vb, vt, b.line((R, 0, 0), (0, 0, h)))
    outer = b.face(b.add(f"CYLINDRICAL_SURFACE('',#{b.ax((0, 0, 0), Z, X)},{R!r})"), True,
                   b.loop((cb, True), (seam, True), (ct, False), (seam, False)))
    inner = b.face(b.add(f"CYLINDRICAL_SURFACE('',#{b.ax((0, 0, 0), Z, X)},{r!r})"), False,
                   b.loop((ib, False)), b.loop((it, True)))
    top = b.face(b.plane((0, 0, h), Z, X), True, b.loop((ct, True)), b.loop((it, False)))
    bottom = b.face(b.plane((0, 0, 0), Z, X), False, b.loop((cb, False)), b.loop((ib, True)))
    return b.solid("tube", [outer, inner, top, bottom]), math.pi * (R * R - r * r) * h, (0.0, 0.0, h / 2)


def cone(r=10.0, semi_angle_deg=30.0):
    """A full cone: base disc at z = 0, apex above at z = r / tan(semi_angle) (a VERTEX_LOOP).
    The conical surface's axis points down, so its radius grows downward from the apex."""
    a = math.radians(semi_angle_deg)
    H = r / math.tan(a)
    b = B()
    v = b.vx(r, 0, 0)
    apex = b.vx(0, 0, H)
    c = b.edge(v, v, b.circle((0, 0, 0), Z, X, r))
    surf = b.add(f"CONICAL_SURFACE('',#{b.ax((0, 0, 0), NZ, X)},{r!r},{a!r})")
    # seen from outside, the base circle runs clockwise about +z (counter-clockwise about the
    # cone's outward normal, which points away from the axis and up)
    side = b.face(surf, True, b.loop((c, True)), b.vertex_loop(apex))
    base = b.face(b.plane((0, 0, 0), Z, X), False, b.loop((c, False)))
    return b.solid("cone", [side, base]), math.pi * r * r * H / 3, (0.0, 0.0, H / 4)


def sphere_halves(r=10.0):
    """A sphere as two hemispheres, each bounded only by the equator (no seam, no pole edge)."""
    b = B()
    v = b.vx(r, 0, 0)
    eq = b.edge(v, v, b.circle((0, 0, 0), Z, X, r))
    s = b.add(f"SPHERICAL_SURFACE('',#{b.ax((0, 0, 0), Z, X)},{r!r})")
    up = b.face(s, True, b.loop((eq, True)))
    down = b.face(s, True, b.loop((eq, False)))
    return b.solid("sphere", [up, down]), 4 / 3 * math.pi * r ** 3, (0.0, 0.0, 0.0)


def sphere_seam(r=10.0, o=(3.0, -2.0, 5.0)):
    """A sphere as one face whose loop is a seam meridian from pole to pole, there and back."""
    b = B()
    S, N = b.vx(o[0], o[1], o[2] - r), b.vx(o[0], o[1], o[2] + r)
    mer = b.edge(S, N, b.circle(o, (0, -1, 0), X, r))  # through (o + r x) from south to north
    s = b.add(f"SPHERICAL_SURFACE('',#{b.ax(o, Z, X)},{r!r})")
    face = b.face(s, True, b.loop((mer, True), (mer, False)))
    return b.solid("ball", [face]), 4 / 3 * math.pi * r ** 3, tuple(float(c) for c in o)


def torus(R=20.0, r=5.0):
    """A full torus as one face bounded by its two seams (an equator circle and a tube circle)."""
    b = B()
    v = b.vx(R + r, 0, 0)
    eq = b.edge(v, v, b.circle((0, 0, 0), Z, X, R + r))
    tube_c = b.edge(v, v, b.circle((R, 0, 0), (0, -1, 0), X, r))
    s = b.add(f"TOROIDAL_SURFACE('',#{b.ax((0, 0, 0), Z, X)},{R!r},{r!r})")
    face = b.face(s, True, b.loop((eq, True), (tube_c, True), (eq, False), (tube_c, False)))
    return b.solid("torus", [face]), 2 * math.pi ** 2 * R * r * r, (0.0, 0.0, 0.0)


def cut_cylinder(r=10.0, h=20.0, slope=0.5):
    """A cylinder from z = 0 to the tilted plane z = h + slope x: the top edge is an ellipse.
    V = pi r^2 h; centre x = slope r^2 / (8 h) * ... = slope (pi r^4 / 4) / (2 V)."""
    b = B()
    n = math.sqrt(1 + slope * slope)
    zt, xt = (-slope / n, 0.0, 1 / n), (1 / n, 0.0, slope / n)
    vb, vt = b.vx(r, 0, 0), b.vx(r, 0, h + slope * r)
    cb = b.edge(vb, vb, b.circle((0, 0, 0), Z, X, r))
    el = b.edge(vt, vt, b.ellipse((0, 0, h), zt, xt, r * n, r))
    side = b.face(b.add(f"CYLINDRICAL_SURFACE('',#{b.ax((0, 0, 0), Z, X)},{r!r})"), True,
                  b.loop((cb, True)), b.loop((el, False)))
    top = b.face(b.plane((0, 0, h), zt, xt), True, b.loop((el, True)))
    bottom = b.face(b.plane((0, 0, 0), Z, X), False, b.loop((cb, False)))
    V = math.pi * r * r * h
    Sx = slope * math.pi * r ** 4 / 4  # integral of x * (h + slope x) over the disc
    Sz = math.pi * r * r * h * h / 2 + slope * slope * math.pi * r ** 4 / 8  # of (h + slope x)^2 / 2
    return b.solid("cut", [side, top, bottom]), V, (Sx / V, 0.0, Sz / V)

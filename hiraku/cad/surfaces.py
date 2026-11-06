"""Curves and surfaces of a STEP B-rep as vectorised evaluators (points, first derivatives, inversion).

All in the file's own units and coordinates. Every curve has ev(t) -> (P, dP/dt) and param(p) (the
parameter of the closest point). Every surface has ev(u, v) -> (P, P_u, P_v), inv(points) -> (u, v),
the period of each parameter (None when it is not periodic) and, for faces that close at a pole,
pole(axis, side): the value of the other parameter where the surface shrinks to a point or a line.

Parameters (u is the first, v the second):
- CYLINDRICAL_SURFACE: u angle from the placement's x, v height along z (ISO 10303-42).
- CONICAL_SURFACE: u angle, v height; radius = r + v tan(semi_angle) (ISO 10303-42).
- SPHERICAL_SURFACE: u longitude, v latitude in [-pi/2, pi/2].
- TOROIDAL_SURFACE: u angle about the axis, v angle round the tube.
- SURFACE_OF_REVOLUTION: u angle about the axis, v the profile curve's parameter.
- SURFACE_OF_LINEAR_EXTRUSION: u the swept curve's parameter, v length along the extrusion.
- B_SPLINE_SURFACE (with knots, Bezier, uniform, quasi-uniform; rational or not): its own (u, v);
  a surface whose two u edges (or v edges) meet is treated as closed with the knot range as period.
The order of u and v for the swept surfaces is this module's own; nothing outside uses it.
"""

from __future__ import annotations

import math

import numpy as np

from hiraku.cad import CadError

TWO_PI = 2 * math.pi
CHUNK = 20000


def basis(U: np.ndarray, p: int, n: int, t: np.ndarray, deriv: bool = False):
    """(span, N, dN) of the B-spline basis of degree p with n control points at parameters t.
    N[:, q] is the function of control point span - p + q (Cox-de Boor, all points at once)."""
    t = np.clip(np.asarray(t, float), U[p], U[n])
    span = np.clip(np.searchsorted(U, t, side="right") - 1, p, n - 1)
    m = t.shape[0]
    N = np.ones((m, 1))
    dN = np.zeros((m, 1)) if deriv else None
    for d in range(1, p + 1):
        Nn = np.zeros((m, d + 1))
        last = deriv and d == p
        if last:
            dN = np.zeros((m, d + 1))
        for r in range(d):
            i = span - d + 1 + r
            den = U[i + d] - U[i]
            a = np.where(den > 0, N[:, r] / np.where(den > 0, den, 1.0), 0.0)
            Nn[:, r] += (U[i + d] - t) * a
            Nn[:, r + 1] += (t - U[i]) * a
            if last:
                dN[:, r] -= p * a
                dN[:, r + 1] += p * a
        N = Nn
    return span, N, dN


class Basis:
    """basis() for one knot vector, fast: on each knot span the functions are polynomials of degree p,
    fitted once from basis() at p + 1 Chebyshev points of the span and then evaluated by matrix product."""

    def __init__(self, U: np.ndarray, p: int, n: int):
        self.U, self.p, self.n = U, int(p), int(n)
        self.e = np.arange(self.p + 1)
        self.coef: dict = {}

    def _c(self, span: int):
        c = self.coef.get(span)
        if c is None:
            a, b = float(self.U[span]), float(self.U[span + 1])
            s = (1 - np.cos(np.pi * (self.e + 0.5) / (self.p + 1))) / 2
            N = basis(self.U, self.p, self.n, a + s * (b - a))[1]
            c = self.coef[span] = (np.linalg.solve(s[:, None] ** self.e, N), a, b - a)
        return c

    def __call__(self, t):
        U, p, n = self.U, self.p, self.n
        t = np.clip(np.asarray(t, float), U[p], U[n])
        span = np.clip(np.searchsorted(U, t, side="right") - 1, p, n - 1)
        N, dN = np.empty((len(t), p + 1)), np.empty((len(t), p + 1))
        e = self.e
        for sp in np.unique(span):
            idx = np.flatnonzero(span == sp)
            C, a, h = self._c(int(sp))
            s = (t[idx] - a) / h
            T = s[:, None] ** e
            dT = np.zeros_like(T)
            dT[:, 1:] = e[1:] * T[:, :-1]
            N[idx] = T @ C
            dN[idx] = (dT @ C) / h
        return span, N, dN


def knot_vector(kind: str, p: int, n: int, mults=None, knots=None) -> np.ndarray:
    if kind == "WITH_KNOTS":
        U = np.repeat([float(k) for k in knots], [int(m) for m in mults])
    elif kind == "BEZIER":
        U = np.array([0.0] * (p + 1) + [1.0] * (p + 1))
    elif kind == "UNIFORM":
        U = np.arange(-p, n + 1, dtype=float)
    elif kind == "QUASI_UNIFORM":
        inner = np.arange(1, n - p, dtype=float)
        U = np.concatenate([[0.0] * (p + 1), inner, [float(n - p)] * (p + 1)])
    else:
        raise CadError(f"B-spline knots of kind {kind} are not read")
    if len(U) != n + p + 1:
        raise CadError(f"B-spline knots ({len(U)}) do not fit {n} control points of degree {p}")
    return U


# ---------------------------------------------------------------- curves

class Curve:
    period: float | None = None
    domain: tuple | None = None  # (t0, t1); None when unbounded
    kind = "CURVE"
    straight = False

    def ev(self, t):
        raise NotImplementedError

    def param(self, p, Q=None) -> np.ndarray:
        """Parameter of the closest point to each p (after projecting with Q when given)."""
        lo, hi = self.domain if self.period is None else (self.domain[0], self.domain[0] + self.period)
        return _closest_on_curve(self, np.atleast_2d(p), Q, lo, hi, self.samples, self.period is None)

    samples = 128


def _closest_on_curve(c: Curve, p: np.ndarray, Q, lo: float, hi: float, K: int, clamp: bool) -> np.ndarray:
    ts = np.linspace(lo, hi, K + 1)
    C = c.ev(ts)[0]
    out = np.empty(len(p))
    for a in range(0, len(p), 512):
        q = p[a:a + 512]
        diff = C[None] - q[:, None]
        if Q is not None:
            diff = diff @ Q.T
        out[a:a + 512] = ts[np.argmin(np.einsum("mki,mki->mk", diff, diff), axis=1)]
    t = out
    for _ in range(12):
        P, D = c.ev(t)
        r = P - p
        if Q is not None:
            r, D = r @ Q.T, D @ Q.T
        dd = np.einsum("ij,ij->i", D, D)
        step = -np.einsum("ij,ij->i", r, D) / np.where(dd > 0, dd, 1.0)
        step = np.clip(step, -(hi - lo) / 4, (hi - lo) / 4)
        t = t + step
        if clamp:
            t = np.clip(t, lo, hi)
        if np.all(np.abs(step) <= 1e-13 * max(1.0, hi - lo)):
            break
    return t


class Line(Curve):
    kind = "LINE"
    straight = True

    def __init__(self, o, d):
        self.o, self.d = np.asarray(o, float), np.asarray(d, float)

    def ev(self, t):
        t = np.atleast_1d(np.asarray(t, float))
        return self.o + t[:, None] * self.d, np.broadcast_to(self.d, (len(t), 3)).copy()

    def param(self, p, Q=None):
        d = self.d if Q is None else Q @ self.d
        r = np.atleast_2d(p) - self.o
        if Q is not None:
            r = r @ Q.T
        return r @ d / (d @ d)


class Polyline(Curve):
    kind = "POLYLINE"
    straight = True

    def __init__(self, pts):
        self.P = np.asarray(pts, float)
        self.domain = (0.0, float(len(self.P) - 1))

    def ev(self, t):
        t = np.clip(np.atleast_1d(np.asarray(t, float)), 0, len(self.P) - 1)
        i = np.minimum(t.astype(int), len(self.P) - 2)
        d = self.P[i + 1] - self.P[i]
        return self.P[i] + (t - i)[:, None] * d, d


class Conic(Curve):
    """CIRCLE (ra = rb) or ELLIPSE, t the angle from the placement's x."""
    period = TWO_PI
    domain = (0.0, TWO_PI)

    def __init__(self, kind, o, x, y, ra, rb):
        self.kind, self.o, self.x, self.y, self.ra, self.rb = kind, o, x, y, float(ra), float(rb)

    def ev(self, t):
        t = np.atleast_1d(np.asarray(t, float))
        c, s = np.cos(t)[:, None], np.sin(t)[:, None]
        return (self.o + self.ra * c * self.x + self.rb * s * self.y,
                -self.ra * s * self.x + self.rb * c * self.y)

    def param(self, p, Q=None):
        if Q is not None:
            return Curve.param(self, p, Q)
        d = np.atleast_2d(p) - self.o
        return np.arctan2((d @ self.y) / self.rb, (d @ self.x) / self.ra)


class BSplineCurve(Curve):
    kind = "B_SPLINE_CURVE"

    def __init__(self, p: int, P: np.ndarray, U: np.ndarray, W=None):
        self.p, self.U = int(p), U
        self.n = len(P)
        W = np.ones(self.n) if W is None else np.asarray(W, float)
        if np.any(W <= 0):
            raise CadError("a rational B-spline curve with weights that are not positive")
        self.Pw = np.column_stack([P * W[:, None], W])
        self.b = Basis(U, self.p, self.n)
        self.domain = (float(U[self.p]), float(U[self.n]))
        self.samples = max(64, 8 * self.n)
        a, b = self.ev(np.array(self.domain))[0]
        size = float(np.ptp(P, axis=0).max()) or 1.0
        if np.linalg.norm(a - b) <= 1e-7 * size:
            self.period = self.domain[1] - self.domain[0]

    def ev(self, t):
        t = np.atleast_1d(np.asarray(t, float))
        if self.period:
            t = self.domain[0] + np.mod(t - self.domain[0], self.period)
        span, N, dN = self.b(t)
        idx = span[:, None] - self.p + np.arange(self.p + 1)
        G = self.Pw[idx]
        A = np.einsum("mq,mqk->mk", N, G)
        dA = np.einsum("mq,mqk->mk", dN, G)
        w = A[:, 3:]
        P = A[:, :3] / w
        return P, (dA[:, :3] - dA[:, 3:] * P) / w


# ---------------------------------------------------------------- surfaces

class Surface:
    kind = "SURFACE"
    periods: tuple = (None, None)
    domain: tuple = (None, None)  # per parameter (lo, hi) or None
    scale = 1.0  # a length of the surface, for tolerances

    def ev(self, u, v):
        raise NotImplementedError

    def inv(self, pts: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def pole(self, axis: int, side: int, uv: np.ndarray) -> float:
        raise CadError(f"a {self.kind} face bounded by a loop that goes round it once "
                       "(and no pole to close it at)")

    def pieces(self, lo, hi) -> int:
        """Gauss pieces along a fan triangle that spans the parameter box lo..hi."""
        return 1 + int(math.ceil(max(hi[0] - lo[0], hi[1] - lo[1]) / (math.pi / 2)))


def _frame(X, Y, u):
    cu, su = np.cos(u)[:, None], np.sin(u)[:, None]
    return cu * X + su * Y, -su * X + cu * Y


class Cylinder(Surface):
    kind = "CYLINDRICAL_SURFACE"
    periods = (TWO_PI, None)

    def __init__(self, o, X, Y, Z, r):
        self.o, self.X, self.Y, self.Z, self.r = o, X, Y, Z, float(r)
        self.scale = abs(self.r)

    def ev(self, u, v):
        e, de = _frame(self.X, self.Y, u)
        return self.o + self.r * e + v[:, None] * self.Z, self.r * de, np.broadcast_to(self.Z, e.shape)

    def inv(self, pts):
        d = pts - self.o
        return np.column_stack([np.arctan2(d @ self.Y, d @ self.X), d @ self.Z])

    def pieces(self, lo, hi):
        return 1 + int(math.ceil((hi[0] - lo[0]) / (math.pi / 2)))


class Cone(Surface):
    kind = "CONICAL_SURFACE"
    periods = (TWO_PI, None)

    def __init__(self, o, X, Y, Z, r, semi_angle):
        self.o, self.X, self.Y, self.Z, self.r = o, X, Y, Z, float(r)
        self.k = math.tan(semi_angle)
        if abs(self.k) < 1e-15:
            raise CadError("a CONICAL_SURFACE with a semi-angle of zero")
        self.scale = max(abs(self.r), 1e-9)

    def ev(self, u, v):
        e, de = _frame(self.X, self.Y, u)
        rr = (self.r + v * self.k)[:, None]
        return self.o + rr * e + v[:, None] * self.Z, rr * de, self.k * e + self.Z

    def inv(self, pts):
        d = pts - self.o
        return np.column_stack([np.arctan2(d @ self.Y, d @ self.X), d @ self.Z])

    def pole(self, axis, side, uv):
        apex = -self.r / self.k
        if axis != 0 or (apex - float(np.mean(uv[:, 1]))) * side < 0:
            Surface.pole(self, axis, side, uv)
        return apex

    def pieces(self, lo, hi):
        return 1 + int(math.ceil((hi[0] - lo[0]) / (math.pi / 2)))


class Sphere(Surface):
    kind = "SPHERICAL_SURFACE"
    periods = (TWO_PI, None)

    def __init__(self, o, X, Y, Z, r):
        self.o, self.X, self.Y, self.Z, self.r = o, X, Y, Z, float(r)
        self.scale = abs(self.r)

    def ev(self, u, v):
        e, de = _frame(self.X, self.Y, u)
        cv, sv = np.cos(v)[:, None], np.sin(v)[:, None]
        return (self.o + self.r * (cv * e + sv * self.Z), self.r * cv * de, self.r * (-sv * e + cv * self.Z))

    def inv(self, pts):
        d = pts - self.o
        return np.column_stack([np.arctan2(d @ self.Y, d @ self.X), np.arcsin(np.clip(d @ self.Z / self.r, -1, 1))])

    def pole(self, axis, side, uv):
        if axis != 0:
            Surface.pole(self, axis, side, uv)
        return side * math.pi / 2


class Torus(Surface):
    kind = "TOROIDAL_SURFACE"
    periods = (TWO_PI, TWO_PI)

    def __init__(self, o, X, Y, Z, R, r):
        self.o, self.X, self.Y, self.Z, self.R, self.r = o, X, Y, Z, float(R), float(r)
        self.scale = abs(self.R) + abs(self.r)

    def ev(self, u, v):
        e, de = _frame(self.X, self.Y, u)
        cv, sv = np.cos(v)[:, None], np.sin(v)[:, None]
        rr = self.R + self.r * cv
        return self.o + rr * e + self.r * sv * self.Z, rr * de, self.r * (-sv * e + cv * self.Z)

    def inv(self, pts):
        d = pts - self.o
        dx, dy, dz = d @ self.X, d @ self.Y, d @ self.Z
        return np.column_stack([np.arctan2(dy, dx), np.arctan2(dz, np.hypot(dx, dy) - self.R)])


def _perp(a: np.ndarray, d: np.ndarray) -> np.ndarray:
    return d - np.outer(d @ a, a) if d.ndim == 2 else d - (d @ a) * a


class Revolution(Surface):
    """SURFACE_OF_REVOLUTION: the profile curve turned about the axis (o, a); u the angle, v the profile's t."""
    kind = "SURFACE_OF_REVOLUTION"

    def __init__(self, curve: Curve, o, a):
        self.c, self.o, self.a = curve, np.asarray(o, float), np.asarray(a, float)
        lo, hi = curve.domain if curve.domain else (-1.0, 1.0)
        pts = curve.ev(np.linspace(lo, hi, 33))[0]
        perp = _perp(self.a, pts - self.o)
        k = int(np.argmax(np.linalg.norm(perp, axis=1)))
        if np.linalg.norm(perp[k]) < 1e-12:
            raise CadError("a SURFACE_OF_REVOLUTION whose profile lies on its axis")
        self.X = perp[k] / np.linalg.norm(perp[k])
        self.Y = np.cross(self.a, self.X)
        self.periods = (TWO_PI, curve.period)
        self.domain = (None, None if curve.period else curve.domain)
        self.scale = float(np.linalg.norm(perp, axis=1).max())

    def ev(self, u, v):
        C, D = self.c.ev(v)
        d = C - self.o
        z = d @ self.a
        dp = d - np.outer(z, self.a)
        Dz = D @ self.a
        Dp = D - np.outer(Dz, self.a)
        cu, su = np.cos(u)[:, None], np.sin(u)[:, None]
        w, Dw = np.cross(self.a, dp), np.cross(self.a, Dp)
        P = self.o + np.outer(z, self.a) + cu * dp + su * w
        return P, -su * dp + cu * w, np.outer(Dz, self.a) + cu * Dp + su * Dw

    def _angle(self, d):
        return np.arctan2(d @ self.Y, d @ self.X)

    def inv(self, pts):
        d = pts - self.o
        z = d @ self.a
        perp = d - np.outer(z, self.a)
        r = np.linalg.norm(perp, axis=1)
        v = self.c.param(self.o + np.outer(z, self.a) + np.outer(r, self.X))
        C = self.c.ev(v)[0] - self.o
        return np.column_stack([self._angle(perp) - self._angle(C), v])

    def pole(self, axis, side, uv):
        if axis != 0:
            Surface.pole(self, axis, side, uv)
        c = self.c
        if c.domain is not None and c.period is None:
            v = c.domain[1] if side > 0 else c.domain[0]
        elif isinstance(c, Line):
            dp, op = _perp(self.a, c.d), _perp(self.a, c.o - self.o)
            v = -float(op @ dp) / float(dp @ dp)
        else:
            Surface.pole(self, axis, side, uv)
        r = np.linalg.norm(_perp(self.a, c.ev(np.array([v]))[0][0] - self.o))
        if r > 1e-6 * max(self.scale, 1e-9):
            Surface.pole(self, axis, side, uv)
        return float(v)


class Extrusion(Surface):
    """SURFACE_OF_LINEAR_EXTRUSION: u the swept curve's t, v the length along the unit direction."""
    kind = "SURFACE_OF_LINEAR_EXTRUSION"

    def __init__(self, curve: Curve, d):
        self.c = curve
        self.d = np.asarray(d, float) / np.linalg.norm(d)
        self.Q = np.eye(3) - np.outer(self.d, self.d)
        self.periods = (curve.period, None)
        self.domain = (None if curve.period else curve.domain, None)
        lo, hi = curve.domain if curve.domain else (-1.0, 1.0)
        self.scale = float(np.ptp(curve.ev(np.linspace(lo, hi, 33))[0], axis=0).max()) or 1.0

    def ev(self, u, v):
        C, D = self.c.ev(u)
        return C + np.outer(v, self.d), D, np.broadcast_to(self.d, C.shape)

    def inv(self, pts):
        u = self.c.param(pts, self.Q)
        return np.column_stack([u, (pts - self.c.ev(u)[0]) @ self.d])

    def pieces(self, lo, hi):
        c = self.c
        if isinstance(c, BSplineCurve):
            k = np.unique(c.U)
            return 1 + int(np.count_nonzero((k > lo[0]) & (k < hi[0])))
        return Surface.pieces(self, lo, (hi[0], lo[1]))


class BSplineSurface(Surface):
    kind = "B_SPLINE_SURFACE"

    def __init__(self, pu: int, pv: int, P: np.ndarray, Uu: np.ndarray, Uv: np.ndarray, W=None):
        self.pu, self.pv, self.Uu, self.Uv = int(pu), int(pv), Uu, Uv
        self.nu, self.nv = P.shape[:2]
        W = np.ones(P.shape[:2]) if W is None else np.asarray(W, float)
        if W.shape != P.shape[:2]:
            raise CadError("B-spline surface weights do not fit its control points")
        if np.any(W <= 0):
            raise CadError("a rational B-spline surface with weights that are not positive")
        self.Pw = np.concatenate([P * W[..., None], W[..., None]], axis=-1)
        self.bu, self.bv = Basis(Uu, self.pu, self.nu), Basis(Uv, self.pv, self.nv)
        du =(float(Uu[self.pu]), float(Uu[self.nu]))
        dv = (float(Uv[self.pv]), float(Uv[self.nv]))
        self.dom = (du, dv)
        self.scale = float(np.ptp(P.reshape(-1, 3), axis=0).max()) or 1.0
        self.periods = (None, None)
        s = np.linspace(0, 1, 17)
        pu_, pv_ = [], []
        a = self.ev(np.full(17, du[0]), dv[0] + s * (dv[1] - dv[0]))[0]
        b = self.ev(np.full(17, du[1]), dv[0] + s * (dv[1] - dv[0]))[0]
        if np.abs(a - b).max() <= 1e-6 * self.scale:
            pu_ = du[1] - du[0]
        a = self.ev(du[0] + s * (du[1] - du[0]), np.full(17, dv[0]))[0]
        b = self.ev(du[0] + s * (du[1] - du[0]), np.full(17, dv[1]))[0]
        if np.abs(a - b).max() <= 1e-6 * self.scale:
            pv_ = dv[1] - dv[0]
        self.periods = (pu_ or None, pv_ or None)
        self.domain = (None if pu_ else du, None if pv_ else dv)
        gu = np.linspace(du[0], du[1], int(np.clip(3 * self.nu + 1, 13, 151)))
        gv = np.linspace(dv[0], dv[1], int(np.clip(3 * self.nv + 1, 13, 151)))
        UU, VV = np.meshgrid(gu, gv, indexing="ij")
        self.grid_uv = np.column_stack([UU.ravel(), VV.ravel()])
        self.grid_p = self.ev(self.grid_uv[:, 0], self.grid_uv[:, 1])[0]

    def _wrap(self, x, ax):
        P = self.periods[ax]
        if P is None:
            return x
        lo = self.dom[ax][0]
        return lo + np.mod(x - lo, P)

    def ev(self, u, v):
        u = self._wrap(np.atleast_1d(np.asarray(u, float)), 0)
        v = self._wrap(np.atleast_1d(np.asarray(v, float)), 1)
        m = len(u)
        pu, pv = self.pu, self.pv
        su, Nu, dNu = self.bu(u)
        sv, Nv, dNv = self.bv(v)
        A, Au, Av = np.empty((m, 4)), np.empty((m, 4)), np.empty((m, 4))
        key = su * (self.nv + 1) + sv
        order = np.argsort(key, kind="stable")
        ks = key[order]
        cuts = np.flatnonzero(np.diff(ks)) + 1
        for grp in np.split(order, cuts):
            if not len(grp):
                continue
            i, j = int(su[grp[0]]), int(sv[grp[0]])
            patch = self.Pw[i - pu:i + 1, j - pv:j + 1]  # (pu+1, pv+1, 4)
            nv_, dnv = Nv[grp], dNv[grp]
            Bu = (Nu[grp] @ patch.reshape(pu + 1, -1)).reshape(len(grp), pv + 1, 4)
            dBu = (dNu[grp] @ patch.reshape(pu + 1, -1)).reshape(len(grp), pv + 1, 4)
            A[grp] = np.einsum("kb,kbc->kc", nv_, Bu)
            Au[grp] = np.einsum("kb,kbc->kc", nv_, dBu)
            Av[grp] = np.einsum("kb,kbc->kc", dnv, Bu)
        w = A[:, 3:]
        P = A[:, :3] / w
        Su = (Au[:, :3] - Au[:, 3:] * P) / w
        Sv = (Av[:, :3] - Av[:, 3:] * P) / w
        return P, Su, Sv

    def inv(self, pts):
        pts = np.atleast_2d(pts)
        uv = np.empty((len(pts), 2))
        for a in range(0, len(pts), 256):
            q = pts[a:a + 256]
            d2 = (q * q).sum(1)[:, None] - 2 * q @ self.grid_p.T + (self.grid_p * self.grid_p).sum(1)[None]
            uv[a:a + 256] = self.grid_uv[np.argmin(d2, axis=1)]
        (u0, u1), (v0, v1) = self.dom
        span = np.array([u1 - u0, v1 - v0])
        for _ in range(20):
            P, Su, Sv = self.ev(uv[:, 0], uv[:, 1])
            r = pts - P
            a11 = np.einsum("ij,ij->i", Su, Su)
            a12 = np.einsum("ij,ij->i", Su, Sv)
            a22 = np.einsum("ij,ij->i", Sv, Sv)
            b1 = np.einsum("ij,ij->i", Su, r)
            b2 = np.einsum("ij,ij->i", Sv, r)
            eps = 1e-12 * (a11 + a22 + 1e-300)
            det = (a11 + eps) * (a22 + eps) - a12 * a12
            du = ((a22 + eps) * b1 - a12 * b2) / det
            dv = ((a11 + eps) * b2 - a12 * b1) / det
            step = np.column_stack([du, dv])
            step = np.clip(step, -span / 4, span / 4)
            uv = uv + step
            for ax, (lo, hi) in enumerate(self.dom):
                if self.periods[ax] is None:
                    uv[:, ax] = np.clip(uv[:, ax], lo, hi)
            if np.all(np.abs(step) <= 1e-10 * span):
                break
        for ax in (0, 1):
            uv[:, ax] = self._wrap(uv[:, ax], ax)
        return uv

    def pole(self, axis, side, uv):
        lo, hi = self.dom[1 - axis]
        return hi if side > 0 else lo

    def pieces(self, lo, hi):
        ku, kv = np.unique(self.Uu), np.unique(self.Uv)
        n = int(np.count_nonzero((ku > lo[0]) & (ku < hi[0]))) + int(np.count_nonzero((kv > lo[1]) & (kv < hi[1])))
        return 2 + n

"""Three-dimensional two-node beams (6 DOFs per node) as an fe element group.

Formulation (written here from the textbook forms):
- Axial bar and St Venant torsion: linear interpolation, k = EA / L and GJ / L.
- Bending: cubic Hermite interpolation (Euler-Bernoulli). With shear areas given, the Timoshenko
  form of the two-node beam with the shear parameter Phi = 12 E I / (G A_s L^2):
      E I / ((1 + Phi) L^3) [[12, 6L, -12, 6L], [6L, (4 + Phi) L^2, -6L, (2 - Phi) L^2], ...]
  (Przemieniecki, Theory of Matrix Structural Analysis, 1968, chapter 5; the same matrix is in
  McGuire, Gallagher & Ziemian, Matrix Structural Analysis, 2nd ed. 2000 - neither checked here).
  Phi = 0 is the Euler-Bernoulli matrix, which the code checks against the integral of EI H''^T H''.
- Consistent mass: rho A of the Hermite cubics on the bending DOFs and of the linear functions on the
  axial DOF, rho Ip (Ip = Iy + Iz) of the linear functions on the twist (Cook, Malkus, Plesha & Witt,
  Concepts and Applications of FEA, 4th ed. 2002, chapter 11 - not checked here). Rotary inertia of
  the section in bending is left out. The integrals are done here by 4-point Gauss quadrature, which is
  exact for these polynomials.
- Geometric (initial-stress) stiffness from the axial force N (tension positive):
      N * integral of H'^T H' dx on each bending plane, and N Ip / (A L) [[1, -1], [-1, 1]] on the twist
  (Przemieniecki 1968, chapter on stability; not checked here). Shear deformation is not in K_G.
- Geometric stiffness from the bending moments (lateral-torsional buckling), derived here: with the
  section rotating rigidly by the twist phi, a point (y, z) moves u_y = v - z phi, u_z = w + y phi, and the
  second-order part of the axial Green strain (1/2)(u_y'^2 + u_z'^2) times sigma = N/A + My z / Iy - Mz y / Iz
  integrated over a doubly symmetric section gives
      (1/2) integral [N (v'^2 + w'^2) + N Ip / A phi'^2 - 2 My v' phi' - 2 Mz w' phi'] dx.
  The moment terms are used in the form + integral (My phi v'' + Mz phi w'') dx, which equals the above when
  M is constant and phi = 0 at the ends (fork supports) and is the form of the lateral-buckling energy for
  beams with transverse loads at the shear centre (the 2 M phi u'' term of Trahair, Flexural-Torsional
  Buckling of Structures, 1993, and Timoshenko & Gere 1961, chapter 6 - neither checked here). Left out:
  the Wagner terms of sections that are not doubly symmetric (integral sigma y (y^2 + z^2) dA), warping
  (the element has no warping DOF: Cw = 0), the torque's terms and load-height effects. Moments here are
  internal moments, Mz = E Iz v'' and My = E Iy theta_y'; they vary linearly along the element.
  With these terms K_G is not zero for a rigid rotation of a bent element (the known non-invariance of
  the linearised beam K_G under end moments); Beam.kg_moments = False leaves them out.

Local axes: x from node a to node b; y the part of `ref` (default global z, or global x for an element
along z) normal to x; z = x cross y. Iz is about local z (bending in the x-y plane: v, theta_z = dv/dx),
Iy about local y (bending in x-z: w, theta_y = -dw/dx). Element vector node-major
(ux, uy, uz, rx, ry, rz) in global axes. Units: N, mm, MPa, tonne.
"""

from __future__ import annotations

import numpy as np

from .model import ElementGroup

GAUSS4 = np.polynomial.legendre.leggauss(4)


def hermite(xi: float, L: np.ndarray):
    """Cubic Hermite functions H (E, 4) and dH/dx (E, 4), d2H/dx2 (E, 4) at xi in [0, 1] for DOFs
    (v1, v1', v2, v2')."""
    L = np.asarray(L, float)
    one = np.ones_like(L)
    H = np.stack([one * (1 - 3 * xi ** 2 + 2 * xi ** 3), L * (xi - 2 * xi ** 2 + xi ** 3),
                  one * (3 * xi ** 2 - 2 * xi ** 3), L * (-xi ** 2 + xi ** 3)], axis=1)
    dH = np.stack([(-6 * xi + 6 * xi ** 2) / L, one * (1 - 4 * xi + 3 * xi ** 2),
                   (6 * xi - 6 * xi ** 2) / L, one * (-2 * xi + 3 * xi ** 2)], axis=1)
    d2H = np.stack([(-6 + 12 * xi) / L ** 2, (-4 + 6 * xi) / L, (6 - 12 * xi) / L ** 2, (-2 + 6 * xi) / L], axis=1)
    return H, dH, d2H


def _integrate(L: np.ndarray, fn) -> np.ndarray:
    """integral over [0, L] of fn(xi) (E, a, b) by 4-point Gauss."""
    p, w = GAUSS4
    out = 0.0
    for a, wa in zip(p, w):
        xi = 0.5 * (a + 1.0)
        out = out + fn(xi) * (0.5 * wa * L)[:, None, None]
    return out


V_PLANE = np.array([1, 5, 7, 11])  # v, theta_z at both nodes (theta_z = dv/dx)
W_PLANE = np.array([2, 4, 8, 10])  # w, theta_y at both nodes (theta_y = -dw/dx)
W_SIGN = np.array([1.0, -1.0, 1.0, -1.0])


def bending_block(EI: np.ndarray, L: np.ndarray, phi: np.ndarray) -> np.ndarray:
    """Two-node Timoshenko (Phi > 0) or Euler-Bernoulli (Phi = 0) bending stiffness (E, 4, 4) for
    (v1, v1', v2, v2')."""
    c = EI / ((1.0 + phi) * L ** 3)
    k = np.zeros((len(L), 4, 4))
    L2 = L * L
    k[:, 0, 0] = k[:, 2, 2] = 12
    k[:, 0, 2] = k[:, 2, 0] = -12
    k[:, 0, 1] = k[:, 1, 0] = k[:, 0, 3] = k[:, 3, 0] = 6 * L
    k[:, 2, 1] = k[:, 1, 2] = k[:, 2, 3] = k[:, 3, 2] = -6 * L
    k[:, 1, 1] = k[:, 3, 3] = (4 + phi) * L2
    k[:, 1, 3] = k[:, 3, 1] = (2 - phi) * L2
    return k * c[:, None, None]


def _bar(c: np.ndarray) -> np.ndarray:
    return c[:, None, None] * np.array([[1.0, -1.0], [-1.0, 1.0]])[None]


class Beam(ElementGroup):
    """Two-node 3D beams. conn (E, 2); E, G (MPa); A (mm^2); Iy, Iz, J (mm^4); rho (t/mm^3), per element or
    one value. shear_area_y / shear_area_z (mm^2): shear areas for the Timoshenko form (None = Euler-
    Bernoulli). ref: (3,) or (E, 3) vector that sets local y."""

    kind = "beam3d"
    n_comp = 6

    def __init__(self, conn, E, G, A, Iy, Iz, J, rho=0.0, ref=None, shear_area_y=None, shear_area_z=None,
                 name: str = "beam"):
        self.conn = np.asarray(conn, int).reshape(-1, 2)
        ne = len(self.conn)

        def arr(v):
            return np.broadcast_to(np.asarray(v, float), (ne,)).copy()

        self.E, self.G, self.A = arr(E), arr(G), arr(A)
        self.Iy, self.Iz, self.J, self.rho = arr(Iy), arr(Iz), arr(J), arr(rho)
        self.Asy = None if shear_area_y is None else arr(shear_area_y)
        self.Asz = None if shear_area_z is None else arr(shear_area_z)
        self.ref = None if ref is None else np.broadcast_to(np.asarray(ref, float), (ne, 3)).copy()
        self.name = name

    # --- geometry

    def frames(self, nodes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """R (E, 3, 3) with rows ex, ey, ez (local = R @ global) and lengths L (E,)."""
        X = nodes[self.conn]
        d = X[:, 1] - X[:, 0]
        L = np.linalg.norm(d, axis=1)
        if np.any(L <= 0):
            raise ValueError(f"{self.name}: an element has zero length")
        ex = d / L[:, None]
        if self.ref is None:
            ref = np.tile([0.0, 0.0, 1.0], (len(L), 1))
            along_z = np.abs(ex[:, 2]) > 0.999
            ref[along_z] = [1.0, 0.0, 0.0]
        else:
            ref = self.ref
        ey = ref - np.einsum("ei,ei->e", ref, ex)[:, None] * ex
        n = np.linalg.norm(ey, axis=1)
        if np.any(n < 1e-9):
            raise ValueError(f"{self.name}: the reference vector is along an element's axis")
        ey /= n[:, None]
        ez = np.cross(ex, ey)
        return np.stack([ex, ey, ez], axis=1), L

    @staticmethod
    def to_global(K_local: np.ndarray, R: np.ndarray) -> np.ndarray:
        ne = len(R)
        T = np.zeros((ne, 12, 12))
        for b in range(4):
            T[:, 3 * b:3 * b + 3, 3 * b:3 * b + 3] = R
        return np.einsum("eji,ejk,ekl->eil", T, K_local, T)

    @staticmethod
    def local_vector(u_global: np.ndarray, R: np.ndarray) -> np.ndarray:
        ne = len(R)
        return np.einsum("eij,ebj->ebi", R, u_global.reshape(ne, 4, 3)).reshape(ne, 12)

    # --- matrices in local axes

    def _phi(self, EI: np.ndarray, As, L: np.ndarray) -> np.ndarray:
        if As is None:
            return np.zeros_like(L)
        return 12.0 * EI / (self.G * As * L * L)

    def local_stiffness(self, L: np.ndarray) -> np.ndarray:
        ne = len(L)
        k = np.zeros((ne, 12, 12))
        ax = [0, 6]
        k[:, np.ix_(ax, ax)[0], np.ix_(ax, ax)[1]] = _bar(self.E * self.A / L)
        tw = [3, 9]
        k[:, np.ix_(tw, tw)[0], np.ix_(tw, tw)[1]] = _bar(self.G * self.J / L)
        kv = bending_block(self.E * self.Iz, L, self._phi(self.E * self.Iz, self.Asy, L))
        k[:, V_PLANE[:, None], V_PLANE[None, :]] = kv
        kw = bending_block(self.E * self.Iy, L, self._phi(self.E * self.Iy, self.Asz, L))
        k[:, W_PLANE[:, None], W_PLANE[None, :]] = kw * np.outer(W_SIGN, W_SIGN)[None]
        return k

    def local_mass(self, L: np.ndarray, lumped: bool = False) -> np.ndarray:
        ne = len(L)
        m = np.zeros((ne, 12, 12))
        mA = self.rho * self.A * L
        mI = self.rho * (self.Iy + self.Iz) * L
        if lumped:
            for i in (0, 1, 2, 6, 7, 8):
                m[:, i, i] = mA / 2.0
            m[:, 3, 3] = m[:, 9, 9] = mI / 2.0
            return m
        lin = np.array([[2.0, 1.0], [1.0, 2.0]]) / 6.0
        ax = np.array([0, 6])
        m[:, ax[:, None], ax[None, :]] = mA[:, None, None] * lin
        tw = np.array([3, 9])
        m[:, tw[:, None], tw[None, :]] = mI[:, None, None] * lin
        hh = _integrate(L, lambda xi: np.einsum("ea,eb->eab", hermite(xi, L)[0], hermite(xi, L)[0]))
        hh = hh * (self.rho * self.A)[:, None, None]
        m[:, V_PLANE[:, None], V_PLANE[None, :]] = hh
        m[:, W_PLANE[:, None], W_PLANE[None, :]] = hh * np.outer(W_SIGN, W_SIGN)[None]
        return m

    def local_geometric(self, L: np.ndarray, N: np.ndarray, My=None, Mz=None) -> np.ndarray:
        """Initial-stress stiffness (E, 12, 12) in local axes from the axial force N (E,) and, when given, the
        bending moments My, Mz (E, 2) at the two ends (internal moments, linear along the element)."""
        ne = len(L)
        g = np.zeros((ne, 12, 12))
        dd = _integrate(L, lambda xi: np.einsum("ea,eb->eab", hermite(xi, L)[1], hermite(xi, L)[1]))
        dd = dd * N[:, None, None]
        g[:, V_PLANE[:, None], V_PLANE[None, :]] = dd
        g[:, W_PLANE[:, None], W_PLANE[None, :]] = dd * np.outer(W_SIGN, W_SIGN)[None]
        tw = np.array([3, 9])
        g[:, tw[:, None], tw[None, :]] = _bar(N * (self.Iy + self.Iz) / (self.A * L))
        if My is not None or Mz is not None:
            # integral of M(x) phi(x) v''(x) dx: M linear, phi linear (1 - xi, xi), v'' from the Hermite cubics
            def coupling(M):
                M = np.asarray(M, float)
                return _integrate(L, lambda xi: np.einsum(
                    "e,ea,b->eab", M[:, 0] * (1 - xi) + M[:, 1] * xi, hermite(xi, L)[2], np.array([1 - xi, xi])))
            for M, plane, sign in ((My, V_PLANE, np.ones(4)), (Mz, W_PLANE, W_SIGN)):
                if M is None:
                    continue
                c = coupling(M) * sign[None, :, None]
                g[:, plane[:, None], tw[None, :]] += c
                g[:, tw[:, None], plane[None, :]] += np.transpose(c, (0, 2, 1))
        return g

    # --- the ElementGroup interface

    def stiffness(self, nodes: np.ndarray) -> np.ndarray:
        R, L = self.frames(nodes)
        return self.to_global(self.local_stiffness(L), R)

    def mass(self, nodes: np.ndarray, lumped: bool = False) -> np.ndarray:
        R, L = self.frames(nodes)
        return self.to_global(self.local_mass(L, lumped), R)

    def mass_total(self, nodes: np.ndarray) -> float:
        _, L = self.frames(nodes)
        return float(np.sum(self.rho * self.A * L))

    def end_forces(self, nodes: np.ndarray, ue: np.ndarray, thermal=None) -> np.ndarray:
        """Local end forces k_local u_local (E, 12) (minus the thermal load when `thermal` = (alpha, dT))."""
        R, L = self.frames(nodes)
        f = np.einsum("eij,ej->ei", self.local_stiffness(L), self.local_vector(ue, R))
        if thermal is not None:
            f = f - self.local_thermal(L, *thermal)
        return f

    def axial_force(self, nodes: np.ndarray, ue: np.ndarray, thermal=None) -> np.ndarray:
        """N (E,), tension positive."""
        return self.end_forces(nodes, ue, thermal)[:, 6]

    def end_moments(self, nodes: np.ndarray, ue: np.ndarray, thermal=None) -> tuple[np.ndarray, np.ndarray]:
        """Internal bending moments My, Mz (E, 2) at the two ends: M(0) = -(end force on the moment DOF at a),
        M(L) = +(end force at b), so that Mz = E Iz v'' and My = E Iy theta_y' (theta_y = -w')."""
        f = self.end_forces(nodes, ue, thermal)
        return np.stack([-f[:, 4], f[:, 10]], axis=1), np.stack([-f[:, 5], f[:, 11]], axis=1)

    def geometric_stiffness(self, nodes: np.ndarray, ue: np.ndarray, thermal=None) -> np.ndarray:
        """K_G of the axial force and, with self.kg_moments (default True), of the bending moments (lateral-
        torsional coupling, see the module docstring)."""
        R, L = self.frames(nodes)
        N = self.axial_force(nodes, ue, thermal)
        if getattr(self, "kg_moments", True):
            My, Mz = self.end_moments(nodes, ue, thermal)
            return self.to_global(self.local_geometric(L, N, My, Mz), R)
        return self.to_global(self.local_geometric(L, N), R)

    def local_thermal(self, L: np.ndarray, alpha, dT) -> np.ndarray:
        """Equivalent local end forces (E, 12) of a uniform temperature change dT (per element)."""
        ne = len(L)
        f = np.zeros((ne, 12))
        n = self.E * self.A * np.broadcast_to(np.asarray(alpha, float), (ne,)) * np.broadcast_to(np.asarray(dT, float), (ne,))
        f[:, 0], f[:, 6] = -n, n
        return f

    def thermal_forces(self, nodes: np.ndarray, alpha, dT) -> np.ndarray:
        """Equivalent element forces (E, 12) in global axes of a uniform temperature change per element."""
        R, L = self.frames(nodes)
        ne = len(L)
        fl = self.local_thermal(L, alpha, dT)
        return np.einsum("eij,ebi->ebj", R, fl.reshape(ne, 4, 3)).reshape(ne, 12)


def line_mesh(p0, p1, n: int) -> tuple[np.ndarray, np.ndarray]:
    """n + 1 nodes evenly from p0 to p1 and the n two-node elements between them."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    s = np.linspace(0.0, 1.0, n + 1)[:, None]
    return p0 + s * (p1 - p0), np.stack([np.arange(n), np.arange(1, n + 1)], axis=1)


__all__ = ["Beam", "bending_block", "hermite", "line_mesh"]

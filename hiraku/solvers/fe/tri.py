"""Three-node flat shell triangles (6 DOFs per node) as an fe element group: DKT bending, CST membrane and
a small drilling stiffness.

Choice: DKT + CST by default, MITC3 bending as an option (bending="mitc3"). DKT is a Kirchhoff (thin)
plate, so it has no transverse shear and cannot lock in shear, passes the bending patch test and
converges well for the thin walls the core is used for (measured in tests/solvers/test_fe_tri.py: Navier
plate -1.3 % at 8 x 8 squares cut in two, -0.31 % at 16 x 16). MITC3 is Reissner-Mindlin like MITC4 and
ties the same tangential edge shear, so a mesh mixing MITC3 and MITC4 passes the bending patch test,
which DKT with MITC4 does not (the Kirchhoff triangle puts forces on w for a constant moment, the Mindlin
quad does not; measured 0.75 % curvature error on the test's patch). But MITC3 is stiff: -3.6 % on the
16 x 16 Navier plate at t = 2 and -63 % at t = 0.2 (locking), so DKT stays the default. The price of DKT:
no transverse shear flexibility (thin walls only). Both use the CST membrane, which is stiff in in-plane
bending (a strip bent in its own plane needs several triangles across).

Formulation (written here from the assumptions of the papers, no outside code):
- Local frame: e1 along edge 1-2, n = (x2 - x1) x (x3 - x1) / |...| (nodes counter-clockwise seen from the
  top side, as for MITC4), e2 = n x e1.
- Bending: the Discrete Kirchhoff Triangle (Batoz, Bathe & Ho 1980, "A study of three-node triangular
  plate bending elements", IJNME 15, 1771-1812; explicit form in Batoz 1982, IJNME 18, 1077-1089 - neither
  checked here). The rotation field beta = (beta_x, beta_y) (a point at height z moves z beta in plane)
  is quadratic on the 6-node triangle; its values at the corners are the nodal rotations, and at each
  mid-side k of edge i-j (length l, unit tangent s, normal n):
      beta_s(k) = -w_,s(k) of the cubic w along the edge = 3 / (2 l) (w_i - w_j) - (beta_s,i + beta_s,j) / 4,
      beta_n(k) = (beta_n,i + beta_n,j) / 2,
  (the Kirchhoff constraint beta = -grad w at the corners and mid-sides, w cubic and beta_n linear along
  the edges). The relation is built as a matrix per element, so the explicit polynomials are not used.
  Curvatures (kx, ky, kxy) = (beta_x,x, beta_y,y, beta_x,y + beta_y,x); 3-point Gauss rule (exact: the
  curvatures are linear).
  With MITC4's sign rule (rotation vector (rx, ry) about local x, y): beta_x = ry, beta_y = -rx.
- Membrane: the constant-strain triangle (linear u, v).
- Drilling rotation: DRILL_FACTOR * G * t * area / 3 times the sum over the corners of
  (theta_n,i - omega)^2, omega = (v_,x - u_,y) / 2 the element's in-plane rotation (a Hughes-Brezzi type
  penalty, the same scale as MITC4's). Rigid motions get no energy.
- Mass: consistent, rho t integral(N_a N_b) dA with the linear N on the translations and rho t^3 / 12 times
  the same on the rotations (MITC4's rule); lumped: a third of rho t A per node.
- Initial-stress (geometric) stiffness: the membrane forces are constant in a CST (exact for the element),
  integral grad(N)^T S grad(N) dA with the linear N on the three translations, and the thickness term
  t^2 / 12 integral grad(beta_k)^T S grad(beta_k) dA on beta_x, beta_y of the DKT field (a point at height z
  moves z beta in plane).
- Pressure: p n A / 3 on each node's translations (the linear N); no moments.

Units: N, mm, MPa, tonne.
"""

from __future__ import annotations

import numpy as np

from ..k1_shell import element as mitc4
from ..k1_shell.stress import ShellStress
from .model import ElementGroup

DRILL_FACTOR = mitc4.DRILL_FACTOR

# 3-point Gauss rule on the triangle (exact for quadratics): area coordinates and weights (sum 1)
GAUSS3 = np.array([[2 / 3, 1 / 6, 1 / 6], [1 / 6, 2 / 3, 1 / 6], [1 / 6, 1 / 6, 2 / 3]])
W3 = np.full(3, 1.0 / 3.0)
EDGES = ((0, 1), (1, 2), (2, 0))  # mid-side nodes 4, 5, 6

_MEM = np.array([6 * i + k for i in range(3) for k in (0, 1)])
_BEN = np.array([6 * i + k for i in range(3) for k in (2, 3, 4)])
_DRL = np.array([6 * i + 5 for i in range(3)])


def frames(xyz: np.ndarray):
    """R (E, 3, 3) rows e1, e2, n; local in-plane corner coordinates xl (E, 3, 2) about the centroid; area (E,)."""
    a = xyz[:, 1] - xyz[:, 0]
    b = xyz[:, 2] - xyz[:, 0]
    n = np.cross(a, b)
    nn = np.linalg.norm(n, axis=1)
    if np.any(nn <= 1e-12 * np.einsum("ei,ei->e", a, a)):
        raise ValueError("a triangle has no area (collinear or repeated nodes)")
    n = n / nn[:, None]
    e1 = a / np.linalg.norm(a, axis=1)[:, None]
    e2 = np.cross(n, e1)
    R = np.stack([e1, e2, n], axis=1)
    c = xyz.mean(axis=1)
    xl = np.einsum("eij,enj->eni", R[:, :2, :], xyz - c[:, None, :])
    return R, xl, 0.5 * nn


def linear_derivatives(xl: np.ndarray):
    """dL_i/dx, dL_i/dy (E, 2, 3) of the area coordinates and the area (E,)."""
    x, y = xl[:, :, 0], xl[:, :, 1]
    j, k = [1, 2, 0], [2, 0, 1]
    b = y[:, j] - y[:, k]
    c = x[:, k] - x[:, j]
    A2 = x[:, 0] * (y[:, 1] - y[:, 2]) + x[:, 1] * (y[:, 2] - y[:, 0]) + x[:, 2] * (y[:, 0] - y[:, 1])
    if np.any(A2 <= 0):
        raise ValueError("a triangle has a non-positive area in its own frame")
    return np.stack([b, c], axis=1) / A2[:, None, None], 0.5 * A2


def quadratic_derivatives(Lc: np.ndarray, dL: np.ndarray) -> np.ndarray:
    """Cartesian derivatives (E, 2, 6) of the 6-node quadratic functions at area coordinates Lc (3,):
    corners L_i (2 L_i - 1), mid-sides 4 L_i L_j on EDGES."""
    g = np.zeros((3, 6))  # dN_a / dL_i
    for i in range(3):
        g[i, i] = 4 * Lc[i] - 1
    for m, (i, j) in enumerate(EDGES):
        g[i, 3 + m] = 4 * Lc[j]
        g[j, 3 + m] = 4 * Lc[i]
    return np.einsum("eri,ia->era", dL, g)


def quadratic_values(Lc: np.ndarray) -> np.ndarray:
    N = np.zeros(6)
    N[:3] = Lc * (2 * Lc - 1)
    for m, (i, j) in enumerate(EDGES):
        N[3 + m] = 4 * Lc[i] * Lc[j]
    return N


def dkt_beta_matrix(xl: np.ndarray) -> np.ndarray:
    """T (E, 6, 2, 9): beta at the 6 nodes (corners, then mid-sides of EDGES), components (x, y), from the
    bending DOFs (w, rx, ry) of the three corners (see the module docstring)."""
    ne = len(xl)
    T = np.zeros((ne, 6, 2, 9))
    for i in range(3):
        T[:, i, 0, 3 * i + 2] = 1.0  # beta_x = ry
        T[:, i, 1, 3 * i + 1] = -1.0  # beta_y = -rx
    for m, (i, j) in enumerate(EDGES):
        d = xl[:, j] - xl[:, i]
        l = np.linalg.norm(d, axis=1)
        s = d / l[:, None]
        nrm = np.stack([s[:, 1], -s[:, 0]], axis=1)
        Bi, Bj = T[:, i], T[:, j]  # (E, 2, 9)
        bs = (np.einsum("ec,ecq->eq", s, Bi + Bj)) * -0.25
        bs[:, 3 * i] += 1.5 / l
        bs[:, 3 * j] -= 1.5 / l
        bn = 0.5 * np.einsum("ec,ecq->eq", nrm, Bi + Bj)
        T[:, 3 + m] = s[:, :, None] * bs[:, None, :] + nrm[:, :, None] * bn[:, None, :]
    return T


def linear_beta_matrix(ne: int) -> np.ndarray:
    """T (E, 6, 2, 9) of a linear rotation field (MITC3): the corners' rotations, their means at the mid-sides
    (the quadratic interpolation of a linear field is the field)."""
    T = np.zeros((ne, 6, 2, 9))
    for i in range(3):
        T[:, i, 0, 3 * i + 2] = 1.0
        T[:, i, 1, 3 * i + 1] = -1.0
    for m, (i, j) in enumerate(EDGES):
        T[:, 3 + m] = 0.5 * (T[:, i] + T[:, j])
    return T


def mitc3_shear(xl: np.ndarray, dL: np.ndarray):
    """MITC3 transverse shear: the coefficients P (E, 3, 9) of (a1, a2, c) of the assumed field
    gamma(x, y) = (a1 - c y, a2 + c x) (x, y about the centroid) from the bending DOFs, and a function that
    gives the shear matrix (E, 2, 9) at area coordinates Lc.

    The field's tangential component is constant along each edge; it is set equal to the tangential shear
    w_,s + beta . s of the linear w and beta at the edge's mid-point (Lee & Bathe 2004, "Development of MITC
    isotropic triangular shell finite elements", Computers & Structures 82, 945-962: tying of the covariant
    shear along the edges - written here from that description, the paper's equations not checked)."""
    ne = len(xl)
    Tl = linear_beta_matrix(ne)
    M = np.zeros((ne, 3, 3))
    rhs = np.zeros((ne, 3, 9))
    for m, (i, j) in enumerate(EDGES):
        d = xl[:, j] - xl[:, i]
        l = np.linalg.norm(d, axis=1)
        s = d / l[:, None]
        p = 0.5 * (xl[:, i] + xl[:, j])
        M[:, m, 0] = s[:, 0]
        M[:, m, 1] = s[:, 1]
        M[:, m, 2] = s[:, 1] * p[:, 0] - s[:, 0] * p[:, 1]
        rhs[:, m] = np.einsum("ec,ecq->eq", s, Tl[:, 3 + m])
        rhs[:, m, 3 * j] += 1.0 / l
        rhs[:, m, 3 * i] -= 1.0 / l
    P = np.linalg.solve(M, rhs)

    def at(Lc):
        x = np.einsum("i,eic->ec", np.asarray(Lc, float), xl)
        B = np.zeros((ne, 2, 9))
        B[:, 0] = P[:, 0] - x[:, 1:2] * P[:, 2]
        B[:, 1] = P[:, 1] + x[:, 0:1] * P[:, 2]
        return B

    return P, at


def beta_gradients(T: np.ndarray, dN6: np.ndarray) -> np.ndarray:
    """d beta_c / d x_r (E, 2 comps, 2 dirs, 9) at one point."""
    return np.einsum("era,eacq->ecrq", dN6, T)


def curvature_B(T: np.ndarray, dN6: np.ndarray) -> np.ndarray:
    """(E, 3, 9) curvature matrix (kx, ky, kxy) at one point."""
    G = beta_gradients(T, dN6)
    return np.stack([G[:, 0, 0], G[:, 1, 1], G[:, 0, 1] + G[:, 1, 0]], axis=1)


def membrane_B(dL: np.ndarray) -> np.ndarray:
    """(E, 3, 6) constant strain matrix for (u1, v1, u2, v2, u3, v3)."""
    ne = len(dL)
    B = np.zeros((ne, 3, 6))
    B[:, 0, 0::2] = dL[:, 0]
    B[:, 1, 1::2] = dL[:, 1]
    B[:, 2, 0::2] = dL[:, 1]
    B[:, 2, 1::2] = dL[:, 0]
    return B


def to_global(K_local: np.ndarray, R: np.ndarray) -> np.ndarray:
    """Element matrices (E, 6k, 6k) from local to global axes (blocks of 3)."""
    ne, nd = K_local.shape[0], K_local.shape[1]
    nb = nd // 3
    K5 = K_local.reshape(ne, nb, 3, nb, 3)
    return np.einsum("eji,eajbl,elk->eaibk", R, K5, R).reshape(ne, nd, nd)


def _local_vectors(R: np.ndarray, u: np.ndarray) -> np.ndarray:
    ne = len(R)
    return np.einsum("eij,enj->eni", R, u.reshape(ne, -1, 3)).reshape(ne, -1)


def _thermal_args(thermal) -> tuple:
    t = tuple(thermal)
    return t + (0.0, False)[len(t) - 2:] if len(t) < 4 else t


class Tri3(ElementGroup):
    """Three-node shell triangles: conn (E, 3) counter-clockwise seen from the top side; t, E, nu, rho per
    element or one value."""

    shell = True
    n_comp = 6

    def __init__(self, conn, t, E, nu, rho=0.0, drill_factor: float = DRILL_FACTOR, bending: str = "dkt",
                 name: str = "tri"):
        self.conn = np.asarray(conn, int).reshape(-1, 3)
        ne = len(self.conn)

        def arr(v):
            return np.broadcast_to(np.asarray(v, float), (ne,)).copy()

        self.t, self.E, self.nu, self.rho = arr(t), arr(E), arr(nu), arr(rho)
        self.drill_factor = drill_factor
        if bending not in ("dkt", "mitc3"):
            raise ValueError(f"bending must be 'dkt' or 'mitc3', not {bending!r}")
        self.bending = bending
        self.kind = "dkt3" if bending == "dkt" else "mitc3"
        self.name = name

    # --- geometry and strain matrices

    def _geometry(self, nodes: np.ndarray):
        R, xl, _ = frames(nodes[self.conn])
        dL, A = linear_derivatives(xl)
        T = dkt_beta_matrix(xl) if self.bending == "dkt" else linear_beta_matrix(len(xl))
        return R, xl, dL, A, T

    def local_stiffness(self, nodes: np.ndarray) -> np.ndarray:
        R, xl, dL, A, T = self._geometry(nodes)
        ne = len(A)
        D = mitc4.plane_stress_matrix(self.E, self.nu)
        Db = D * (self.t ** 3 / 12.0)[:, None, None]
        Bm = membrane_B(dL)
        K = np.zeros((ne, 18, 18))
        K[:, _MEM[:, None], _MEM[None, :]] = np.einsum("eaj,eab,ebk,e->ejk", Bm, D, Bm, A * self.t)
        Kb = np.zeros((ne, 9, 9))
        for Lc, w in zip(GAUSS3, W3):
            Bb = curvature_B(T, quadratic_derivatives(Lc, dL))
            Kb += np.einsum("eaj,eab,ebk,e->ejk", Bb, Db, Bb, w * A)
        if self.bending == "mitc3":
            _, shear_at = mitc3_shear(xl, dL)
            ds = mitc4.SHEAR_FACTOR * self.E / (2.0 * (1.0 + self.nu)) * self.t
            for Lc, w in zip(GAUSS3, W3):
                Bs = shear_at(Lc)
                Kb += np.einsum("eaj,eak,e->ejk", Bs, Bs, ds * w * A)
        K[:, _BEN[:, None], _BEN[None, :]] = Kb
        # drilling: (theta_n,i - omega) at each corner, omega = (v_,x - u_,y) / 2
        G = self.E / (2.0 * (1.0 + self.nu))
        kd = self.drill_factor * G * self.t * A / 3.0
        idx = np.concatenate([_MEM, _DRL])
        Kd = np.zeros((ne, 9, 9))
        for i in range(3):
            b = np.zeros((ne, 9))
            b[:, 0:6:2] = 0.5 * dL[:, 1]  # -(-u_,y / 2)
            b[:, 1:6:2] = -0.5 * dL[:, 0]
            b[:, 6 + i] += 1.0
            Kd += np.einsum("ej,ek,e->ejk", b, b, kd)
        K[:, idx[:, None], idx[None, :]] += Kd
        return K

    def stiffness(self, nodes: np.ndarray) -> np.ndarray:
        R, _, _ = frames(nodes[self.conn])
        return to_global(self.local_stiffness(nodes), R)

    def areas(self, nodes: np.ndarray) -> np.ndarray:
        return frames(nodes[self.conn])[2]

    def mass_total(self, nodes: np.ndarray) -> float:
        return float(np.sum(self.rho * self.t * self.areas(nodes)))

    def mass(self, nodes: np.ndarray, lumped: bool = False) -> np.ndarray:
        A = self.areas(nodes)
        ne = len(A)
        M = np.zeros((ne, 18, 18))
        if lumped:
            mm = np.eye(3)[None] * (A / 3.0)[:, None, None]
        else:
            mm = (np.ones((3, 3)) + np.eye(3))[None] * (A / 12.0)[:, None, None]
        mt = (self.rho * self.t)[:, None, None] * mm
        mr = (self.rho * self.t ** 3 / 12.0)[:, None, None] * mm
        for k in range(6):
            idx = 6 * np.arange(3) + k
            M[:, idx[:, None], idx[None, :]] = mt if k < 3 else mr
        return M

    # --- strains and stresses

    def _temperatures(self, values, nodal: bool) -> np.ndarray:
        v = np.asarray(values, float)
        if v.ndim == 0:
            return np.full(self.conn.shape, float(v))
        if nodal:
            return v[self.conn]
        return np.broadcast_to(v.reshape(-1, 1), (len(self.conn), 3)).astype(float)

    def strains(self, nodes: np.ndarray, ue: np.ndarray, at=(1 / 3, 1 / 3, 1 / 3)):
        """Membrane strain and curvature (E, 3) each in local axes at area coordinates `at`; R."""
        R, xl, dL, A, T = self._geometry(nodes)
        ul = _local_vectors(R, ue)
        eps = np.einsum("eak,ek->ea", membrane_B(dL), ul[:, _MEM])
        kap = np.einsum("eak,ek->ea", curvature_B(T, quadratic_derivatives(np.asarray(at, float), dL)), ul[:, _BEN])
        return eps, kap, R

    def membrane_forces(self, nodes: np.ndarray, ue: np.ndarray, thermal=None) -> np.ndarray:
        """(nx, ny, nxy) N/mm (E, 3), constant in the element, local axes."""
        eps, _, _ = self.strains(nodes, ue)
        if thermal is not None:
            alpha, Tm, _, nodal = _thermal_args(thermal)
            tc = self._temperatures(Tm, nodal).mean(axis=1)
            eps = eps - (np.broadcast_to(np.asarray(alpha, float), tc.shape) * tc)[:, None] * np.array([1.0, 1.0, 0.0])
        D = mitc4.plane_stress_matrix(self.E, self.nu)
        return self.t[:, None] * np.einsum("eab,eb->ea", D, eps)

    def stresses(self, nodes: np.ndarray, ue: np.ndarray, thermal=None) -> ShellStress:
        """Centroid stresses (top, mid, bottom), as MITC4.stresses."""
        xyz = nodes[self.conn]
        eps, kap, R = self.strains(nodes, ue)
        if thermal is not None:
            alpha, Tm, dT, nodal = _thermal_args(thermal)
            a = np.broadcast_to(np.asarray(alpha, float), (len(xyz),))
            unit = np.array([1.0, 1.0, 0.0])
            eps = eps - (a * self._temperatures(Tm, nodal).mean(axis=1))[:, None] * unit
            kap = kap - (a * self._temperatures(dT, nodal).mean(axis=1) / self.t)[:, None] * unit
        D = mitc4.plane_stress_matrix(self.E, self.nu)
        h = self.t / 2.0
        sig = np.stack([np.einsum("eab,eb->ea", D, eps + h[:, None] * kap),
                        np.einsum("eab,eb->ea", D, eps),
                        np.einsum("eab,eb->ea", D, eps - h[:, None] * kap)], axis=1)
        return ShellStress(sig, R, xyz.mean(axis=1))

    # --- buckling

    def geometric_stiffness(self, nodes: np.ndarray, ue: np.ndarray, thermal=None, thickness_terms: bool = True
                            ) -> np.ndarray:
        """Initial-stress stiffness (E, 18, 18) in global axes (see the module docstring)."""
        R, xl, dL, A, T = self._geometry(nodes)
        n = self.membrane_forces(nodes, ue, thermal)
        S = np.stack([np.stack([n[:, 0], n[:, 2]], 1), np.stack([n[:, 2], n[:, 1]], 1)], 1)
        ne = len(A)
        Kl = np.zeros((ne, 18, 18))
        kt = np.einsum("eia,eij,ejb,e->eab", dL, S, dL, A)
        for k in range(3):
            idx = 6 * np.arange(3) + k
            Kl[:, idx[:, None], idx[None, :]] = kt
        if thickness_terms:
            kr = np.zeros((ne, 9, 9))
            for Lc, w in zip(GAUSS3, W3):
                Gb = beta_gradients(T, quadratic_derivatives(Lc, dL))  # (E, comp, dir, 9)
                kr += np.einsum("eciq,eij,ecjr,e->eqr", Gb, S, Gb, w * A * self.t ** 2 / 12.0)
            Kl[:, _BEN[:, None], _BEN[None, :]] += kr
        return to_global(Kl, R)

    # --- loads

    def pressure_forces(self, nodes: np.ndarray, p, side: str = "bottom") -> np.ndarray:
        """(E, 18) global: p n A / 3 on each node; 'bottom' pushes along +n, 'top' along -n."""
        R, _, A = frames(nodes[self.conn])
        ne = len(A)
        pv = np.broadcast_to(np.asarray(p, float), (ne,)) * (1.0 if side == "bottom" else -1.0)
        f = np.zeros((ne, 3, 6))
        f[:, :, :3] = (pv * A / 3.0)[:, None, None] * R[:, None, 2, :]
        return f.reshape(ne, 18)

    def thermal_forces(self, nodes: np.ndarray, alpha, T_mid, dT=0.0, nodal: bool = False) -> np.ndarray:
        """Equivalent forces (E, 18) global of a mid-surface change T_mid and a through-thickness difference
        dT = T_top - T_bottom (MITC4.thermal_forces' rule): integral Bm^T Dm eps_th + Bb^T Db kappa_th dA."""
        R, xl, dL, A, T = self._geometry(nodes)
        ne = len(A)
        a = np.broadcast_to(np.asarray(alpha, float), (ne,))
        Tm = self._temperatures(T_mid, nodal)
        dTt = self._temperatures(dT, nodal)
        D = mitc4.plane_stress_matrix(self.E, self.nu)
        unit = np.array([1.0, 1.0, 0.0])
        f = np.zeros((ne, 18))
        eps = (a * Tm.mean(axis=1))[:, None] * unit
        f[:, _MEM] = np.einsum("eak,eab,eb,e->ek", membrane_B(dL), D, eps, A * self.t)
        Db = D * (self.t ** 3 / 12.0)[:, None, None]
        for Lc, w in zip(GAUSS3, W3):
            kap = (a * (dTt @ Lc) / self.t)[:, None] * unit
            f[:, _BEN] += np.einsum("eak,eab,eb,e->ek", curvature_B(T, quadratic_derivatives(Lc, dL)), Db, kap, w * A)
        return np.einsum("eij,ebi->ebj", R, f.reshape(ne, 6, 3)).reshape(ne, 18)


def split_quads(quads: np.ndarray, pattern: str = "alternate") -> np.ndarray:
    """Two triangles per quad (counter-clockwise kept): 'same' cuts every quad on the diagonal 0-2,
    'alternate' switches the diagonal from one quad to the next (in the order given) for a less
    directional mesh."""
    q = np.asarray(quads, int)
    out = []
    for i, (a, b, c, d) in enumerate(q):
        if pattern == "same" or i % 2 == 0:
            out += [[a, b, c], [a, c, d]]
        else:
            out += [[a, b, d], [b, c, d]]
    return np.array(out, int)


__all__ = ["Tri3", "dkt_beta_matrix", "frames", "split_quads", "to_global"]

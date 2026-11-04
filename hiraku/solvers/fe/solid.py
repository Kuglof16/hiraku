"""Solid hexahedra: HEX20 (20-node serendipity, quadratic) and HEX8 (8-node trilinear), isotropic linear elastic.

Written here from the textbook formulas:
- Serendipity shape functions of the 20-node brick: Zienkiewicz, Taylor & Zhu, The Finite Element Method:
  Its Basis and Fundamentals, 6th ed. (2005), chapter 6 (the serendipity family) - chapter number not
  checked here. Corner i: N = (1 + xi xi_i)(1 + eta eta_i)(1 + zeta zeta_i)(xi xi_i + eta eta_i + zeta zeta_i - 2) / 8;
  mid-edge node with xi_i = 0: N = (1 - xi^2)(1 + eta eta_i)(1 + zeta zeta_i) / 4 (and the same for the others).
- Isoparametric stiffness K = sum_g B^T D B det J w_g with Gauss-Legendre points (Cook, Malkus, Plesha &
  Witt, Concepts and Applications of Finite Element Analysis, 4th ed. 2002, chapter 6).
- Stresses are sampled at the 2 x 2 x 2 Gauss points (the optimal sampling points of quadratic
  elements; Barlow 1976, IJNME 10, 243-251, not checked here) and extrapolated to the corners with the
  trilinear functions of those 8 points; a mid-edge node takes the mean of its two corners (Cook et al.,
  section on stress extrapolation; not checked here). Nodal values are averaged over the elements that
  share the node.

Node order (corner signs of (xi, eta, zeta)):
  0 (-,-,-)  1 (+,-,-)  2 (+,+,-)  3 (-,+,-)  4 (-,-,+)  5 (+,-,+)  6 (+,+,+)  7 (-,+,+)
  mid-edge nodes  8: 0-1   9: 1-2  10: 2-3  11: 3-0  12: 4-5  13: 5-6  14: 6-7  15: 7-4
                 16: 0-4  17: 1-5  18: 2-6  19: 3-7
(the order commonly used for 20-node bricks, e.g. by VTK; not checked against any program here).
Corners 0-3 go counter-clockwise seen from +zeta, so a right-handed element has det J > 0.

Integration (chosen from the checks in tests/solvers/test_fe_solid.py): HEX20 uses 3 x 3 x 3 points by
default. On the 2 x 2 x 20 cantilever the two rules differ by 0.1 % in tip deflection (-1.04 % and -0.94 %
from Timoshenko) and not at all in the mid-span bending stress, but a lone reduced (2 x 2 x 2) HEX20 has
6 spurious zero-energy modes besides the 6 rigid motions (in a 2 x 2 x 1 block they are gone; a brick
held at a few nodes only may keep them - not tested here). Full integration
costs about 3 times the element time, which is small next to the factorisation. integration=2 stays
available.
"""

from __future__ import annotations

import math

import numpy as np

from .model import ElementGroup

CORNER_SIGNS = np.array([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
                         [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], float)
EDGES = np.array([[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4],
                  [0, 4], [1, 5], [2, 6], [3, 7]])
HEX20_NAT = np.vstack([CORNER_SIGNS, 0.5 * (CORNER_SIGNS[EDGES[:, 0]] + CORNER_SIGNS[EDGES[:, 1]])])

# faces: 4 corners counter-clockwise seen from outside, then the 4 mid-edge nodes (c0-c1, c1-c2, c2-c3, c3-c0)
FACES20 = np.array([[0, 3, 2, 1, 11, 10, 9, 8],  # zeta = -1
                    [4, 5, 6, 7, 12, 13, 14, 15],  # zeta = +1
                    [0, 1, 5, 4, 8, 17, 12, 16],  # eta = -1
                    [3, 7, 6, 2, 19, 14, 18, 10],  # eta = +1
                    [0, 4, 7, 3, 16, 15, 19, 11],  # xi = -1
                    [1, 2, 6, 5, 9, 18, 13, 17]])  # xi = +1
FACES8 = FACES20[:, :4]


def gauss_1d(n: int) -> tuple[np.ndarray, np.ndarray]:
    """Gauss-Legendre points and weights on [-1, 1]."""
    return np.polynomial.legendre.leggauss(n)


def gauss_3d(n: int) -> tuple[np.ndarray, np.ndarray]:
    p, w = gauss_1d(n)
    P = np.array([(a, b, c) for c in p for b in p for a in p])
    W = np.array([wa * wb * wc for wc in w for wb in w for wa in w])
    return P, W


def shape_hex8(xi: float, eta: float, zeta: float) -> tuple[np.ndarray, np.ndarray]:
    """Trilinear N (8,) and dN/d(xi, eta, zeta) (3, 8)."""
    s = CORNER_SIGNS
    a, b, c = 1 + s[:, 0] * xi, 1 + s[:, 1] * eta, 1 + s[:, 2] * zeta
    N = a * b * c / 8.0
    dN = np.array([s[:, 0] * b * c, a * s[:, 1] * c, a * b * s[:, 2]]) / 8.0
    return N, dN


def shape_hex20(xi: float, eta: float, zeta: float) -> tuple[np.ndarray, np.ndarray]:
    """Serendipity N (20,) and dN/d(xi, eta, zeta) (3, 20)."""
    N = np.zeros(20)
    dN = np.zeros((3, 20))
    s = CORNER_SIGNS
    a, b, c = 1 + s[:, 0] * xi, 1 + s[:, 1] * eta, 1 + s[:, 2] * zeta
    q = s[:, 0] * xi + s[:, 1] * eta + s[:, 2] * zeta - 2.0
    N[:8] = a * b * c * q / 8.0
    dN[0, :8] = s[:, 0] * b * c * (q + a) / 8.0
    dN[1, :8] = s[:, 1] * a * c * (q + b) / 8.0
    dN[2, :8] = s[:, 2] * a * b * (q + c) / 8.0
    m = HEX20_NAT[8:]
    for i, (p0, p1, p2) in enumerate(m):
        k = 8 + i
        if p0 == 0:
            N[k] = (1 - xi * xi) * (1 + p1 * eta) * (1 + p2 * zeta) / 4.0
            dN[:, k] = [-2 * xi * (1 + p1 * eta) * (1 + p2 * zeta) / 4.0,
                        (1 - xi * xi) * p1 * (1 + p2 * zeta) / 4.0,
                        (1 - xi * xi) * (1 + p1 * eta) * p2 / 4.0]
        elif p1 == 0:
            N[k] = (1 - eta * eta) * (1 + p0 * xi) * (1 + p2 * zeta) / 4.0
            dN[:, k] = [(1 - eta * eta) * p0 * (1 + p2 * zeta) / 4.0,
                        -2 * eta * (1 + p0 * xi) * (1 + p2 * zeta) / 4.0,
                        (1 - eta * eta) * (1 + p0 * xi) * p2 / 4.0]
        else:
            N[k] = (1 - zeta * zeta) * (1 + p0 * xi) * (1 + p1 * eta) / 4.0
            dN[:, k] = [(1 - zeta * zeta) * p0 * (1 + p1 * eta) / 4.0,
                        (1 - zeta * zeta) * (1 + p0 * xi) * p1 / 4.0,
                        -2 * zeta * (1 + p0 * xi) * (1 + p1 * eta) / 4.0]
    return N, dN


def shape_quad8(xi: float, eta: float) -> tuple[np.ndarray, np.ndarray]:
    """8-node serendipity quadrilateral (face of a HEX20): corners then mid-edges c0-c1, c1-c2, c2-c3, c3-c0.
    Returns N (8,), dN/d(xi, eta) (2, 8)."""
    cs = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]], float)
    N = np.zeros(8)
    dN = np.zeros((2, 8))
    for i, (a, b) in enumerate(cs):
        N[i] = (1 + a * xi) * (1 + b * eta) * (a * xi + b * eta - 1) / 4.0
        dN[0, i] = a * (1 + b * eta) * (2 * a * xi + b * eta) / 4.0
        dN[1, i] = b * (1 + a * xi) * (a * xi + 2 * b * eta) / 4.0
    mids = [(0, -1), (1, 0), (0, 1), (-1, 0)]
    for j, (a, b) in enumerate(mids):
        k = 4 + j
        if a == 0:
            N[k] = (1 - xi * xi) * (1 + b * eta) / 2.0
            dN[:, k] = [-xi * (1 + b * eta), (1 - xi * xi) * b / 2.0]
        else:
            N[k] = (1 - eta * eta) * (1 + a * xi) / 2.0
            dN[:, k] = [(1 - eta * eta) * a / 2.0, -eta * (1 + a * xi)]
    return N, dN


def shape_quad4(xi: float, eta: float) -> tuple[np.ndarray, np.ndarray]:
    cs = np.array([[-1, -1], [1, -1], [1, 1], [-1, 1]], float)
    N = (1 + cs[:, 0] * xi) * (1 + cs[:, 1] * eta) / 4.0
    dN = np.array([cs[:, 0] * (1 + cs[:, 1] * eta), cs[:, 1] * (1 + cs[:, 0] * xi)]) / 4.0
    return N, dN


def elasticity_matrix(E: np.ndarray, nu: np.ndarray) -> np.ndarray:
    """Isotropic D (n, 6, 6) for (exx, eyy, ezz, gxy, gyz, gzx) with engineering shear strains."""
    E = np.atleast_1d(np.asarray(E, float))
    nu = np.atleast_1d(np.asarray(nu, float))
    lam = E * nu / ((1 + nu) * (1 - 2 * nu))
    mu = E / (2 * (1 + nu))
    D = np.zeros((len(E), 6, 6))
    for i in range(3):
        for j in range(3):
            D[:, i, j] = lam
        D[:, i, i] = lam + 2 * mu
        D[:, 3 + i, 3 + i] = mu
    return D


def strain_matrix(dNx: np.ndarray) -> np.ndarray:
    """B (E, 6, 3n) from Cartesian derivatives dNx (E, 3, n), element vector node-major (ux, uy, uz)."""
    ne, _, n = dNx.shape
    B = np.zeros((ne, 6, 3 * n))
    B[:, 0, 0::3] = dNx[:, 0]
    B[:, 1, 1::3] = dNx[:, 1]
    B[:, 2, 2::3] = dNx[:, 2]
    B[:, 3, 0::3] = dNx[:, 1]
    B[:, 3, 1::3] = dNx[:, 0]
    B[:, 4, 1::3] = dNx[:, 2]
    B[:, 4, 2::3] = dNx[:, 1]
    B[:, 5, 0::3] = dNx[:, 2]
    B[:, 5, 2::3] = dNx[:, 0]
    return B


def jacobian(dN: np.ndarray, X: np.ndarray):
    """J (E, 3, 3) with rows dx/dxi, dx/deta, dx/dzeta; det (E,); dNx (E, 3, n)."""
    J = np.einsum("an,enb->eab", dN, X)
    det = np.linalg.det(J)
    inv = np.linalg.inv(J)
    dNx = np.einsum("eab,bn->ean", inv, dN)
    return J, det, dNx


def von_mises(s: np.ndarray) -> np.ndarray:
    """von Mises stress of Voigt rows (sxx, syy, szz, sxy, syz, szx)."""
    sx, sy, sz, txy, tyz, tzx = (s[..., k] for k in range(6))
    return np.sqrt(np.maximum(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2)
                              + 3.0 * (txy ** 2 + tyz ** 2 + tzx ** 2), 0.0))


def _solid_thermal_args(thermal) -> tuple:
    """(alpha, T, nodal) from (alpha, T) or (alpha, T, nodal); nodal False when left out."""
    t = tuple(thermal)
    if len(t) == 2:
        return t + (False,)
    if len(t) == 3:
        return t
    raise ValueError(f"solid thermal must be (alpha, T) or (alpha, T, nodal), got {len(t)} items")


class _Hex(ElementGroup):
    n_comp = 3
    n_nodes = 8
    default_integration = 2
    faces = FACES8

    def __init__(self, conn, E, nu, rho=0.0, integration: int | None = None, name: str = ""):
        self.conn = np.asarray(conn, int).reshape(-1, self.n_nodes)
        ne = len(self.conn)
        self.E = np.broadcast_to(np.asarray(E, float), (ne,)).copy()
        self.nu = np.broadcast_to(np.asarray(nu, float), (ne,)).copy()
        self.rho = np.broadcast_to(np.asarray(rho, float), (ne,)).copy()
        self.integration = integration or self.default_integration
        self.name = name or self.kind

    def shape(self, xi, eta, zeta):  # pragma: no cover - set by subclasses
        raise NotImplementedError

    def det_j(self, nodes: np.ndarray, points: np.ndarray) -> np.ndarray:
        """det J (E, P) at natural points (P, 3)."""
        X = nodes[self.conn]
        out = np.zeros((len(X), len(points)))
        for i, p in enumerate(points):
            _, dN = self.shape(*p)
            out[:, i] = np.linalg.det(np.einsum("an,enb->eab", dN, X))
        return out

    def stiffness(self, nodes: np.ndarray) -> np.ndarray:
        X = nodes[self.conn]
        ne, n = X.shape[:2]
        D = elasticity_matrix(self.E, self.nu)
        K = np.zeros((ne, 3 * n, 3 * n))
        P, W = gauss_3d(self.integration)
        for p, w in zip(P, W):
            _, dN = self.shape(*p)
            _, det, dNx = jacobian(dN, X)
            if np.any(det <= 0):
                raise ValueError(f"{self.name}: {int(np.sum(det <= 0))} element(s) with det J <= 0 at a Gauss point "
                                 f"(inverted or badly distorted)")
            B = strain_matrix(dNx)
            DB = np.matmul(D, B) * (det * w)[:, None, None]
            K += np.matmul(B.transpose(0, 2, 1), DB)
        return K

    def volumes(self, nodes: np.ndarray) -> np.ndarray:
        P, W = gauss_3d(3)
        return self.det_j(nodes, P) @ W

    def mass_total(self, nodes: np.ndarray) -> float:
        return float(np.sum(self.rho * self.volumes(nodes)))

    def mass(self, nodes: np.ndarray, lumped: bool = False) -> np.ndarray:
        """Element mass matrices (E, 3n, 3n): consistent rho integral(N_a N_b) dV I (3 x 3 x 3 Gauss points),
        or lumped by the HRZ rule - the diagonal of the consistent matrix scaled so that each direction
        carries the element mass (Hinton, Rock & Zienkiewicz 1976, Earthquake Eng. Struct. Dyn. 4, 245-249;
        not checked here). Row sums are not used: for the 20-node brick they give negative corner masses."""
        X = nodes[self.conn]
        ne, n = X.shape[:2]
        P, W = gauss_3d(3)
        mm = np.zeros((ne, n, n))
        for p, w in zip(P, W):
            N, dN = self.shape(*p)
            det = np.linalg.det(np.einsum("an,enb->eab", dN, X))
            mm += np.einsum("a,b,e->eab", N, N, det * w)
        mm *= self.rho[:, None, None]
        if lumped:
            d = np.einsum("eaa->ea", mm)
            total = mm.sum(axis=(1, 2))
            d = d * (total / np.maximum(d.sum(axis=1), 1e-300))[:, None]
            mm = np.zeros_like(mm)
            mm[:, np.arange(n), np.arange(n)] = d
        M = np.zeros((ne, 3 * n, 3 * n))
        for k in range(3):
            M[:, k::3, k::3] = mm
        return M

    def _nodal_field(self, values, nodal: bool) -> np.ndarray:
        """(E, n) element-node values: from a per-node array (nodal=True, indexed by the model's node numbers)
        or a per-element array (E,) / one value (nodal=False). Any other shape is an error."""
        v = np.asarray(values, float)
        if v.ndim == 0:
            return np.full(self.conn.shape, float(v))
        if nodal:
            if v.ndim != 1 or len(v) <= int(self.conn.max()):
                raise ValueError(f"{self.name}: nodal temperatures need one value per model node (shape (N,), "
                                 f"N > {int(self.conn.max())}), got shape {v.shape}")
            return v[self.conn]
        if v.shape != (len(self.conn),):
            raise ValueError(f"{self.name}: per-element temperatures need shape ({len(self.conn)},), got {v.shape}"
                             f" (per-node values need nodal=True)")
        return np.repeat(v[:, None], self.conn.shape[1], axis=1)

    def _thermal_strain(self, N: np.ndarray, thermal) -> np.ndarray:
        """Free thermal strain alpha T (1, 1, 1, 0, 0, 0) at a point with shape values N; (E, 6).
        thermal = (alpha, T) or (alpha, T, nodal)."""
        alpha, T, nodal = _solid_thermal_args(thermal)
        Tp = self._nodal_field(T, nodal) @ N
        a = np.broadcast_to(np.asarray(alpha, float), Tp.shape)
        out = np.zeros((len(Tp), 6))
        out[:, :3] = (a * Tp)[:, None]
        return out

    def point_stresses(self, nodes: np.ndarray, ue: np.ndarray, points: np.ndarray, thermal=None) -> list:
        """[(sigma (E, 6), dNx (E, 3, n), det (E,))] at natural points (P, 3); thermal = (alpha, T[, nodal])
        takes off the free thermal strain (T per element (E,) or one value; per node (N,) with nodal=True)."""
        X = nodes[self.conn]
        D = elasticity_matrix(self.E, self.nu)
        out = []
        for p in points:
            N, dN = self.shape(*p)
            _, det, dNx = jacobian(dN, X)
            eps = np.einsum("eak,ek->ea", strain_matrix(dNx), ue)
            if thermal is not None:
                eps = eps - self._thermal_strain(N, thermal)
            out.append((np.einsum("eab,eb->ea", D, eps), dNx, det))
        return out

    def geometric_stiffness(self, nodes: np.ndarray, ue: np.ndarray, thermal=None) -> np.ndarray:
        """Initial-stress stiffness (E, 3n, 3n) from the stresses of ue at the integration points:
        K_G = integral grad(N_a)^T S grad(N_b) dV times I3, S the 3 x 3 Cauchy stress (Bathe, Finite Element
        Procedures, 1996, section 6.3, the linearised updated/total Lagrangian K_NL term; not checked here)."""
        P, W = gauss_3d(self.integration)
        ne, n = self.conn.shape
        kg = np.zeros((ne, n, n))
        for (s, dNx, det), w in zip(self.point_stresses(nodes, ue, P, thermal), W):
            S = np.stack([np.stack([s[:, 0], s[:, 3], s[:, 5]], 1), np.stack([s[:, 3], s[:, 1], s[:, 4]], 1),
                          np.stack([s[:, 5], s[:, 4], s[:, 2]], 1)], 1)
            kg += np.einsum("eia,eij,ejb,e->eab", dNx, S, dNx, det * w)
        K = np.zeros((ne, 3 * n, 3 * n))
        for k in range(3):
            K[:, k::3, k::3] = kg
        return K

    def thermal_forces(self, nodes: np.ndarray, alpha, T, nodal: bool = False) -> np.ndarray:
        """Equivalent element forces (E, 3n) of a temperature change: f = integral B^T D alpha T (1,1,1,0,0,0) dV,
        T uniform per element (E,) / one value, or per node (N,) with nodal=True (interpolated in the element)."""
        X = nodes[self.conn]
        D = elasticity_matrix(self.E, self.nu)
        P, W = gauss_3d(self.integration)
        f = np.zeros((len(X), 3 * self.conn.shape[1]))
        for p, w in zip(P, W):
            N, dN = self.shape(*p)
            _, det, dNx = jacobian(dN, X)
            f += np.einsum("eak,eab,eb,e->ek", strain_matrix(dNx), D, self._thermal_strain(N, (alpha, T, nodal)),
                           det * w)
        return f

    def gauss_stresses(self, nodes: np.ndarray, ue: np.ndarray, thermal=None) -> np.ndarray:
        """Stresses (E, 8, 6) at the 2 x 2 x 2 Gauss points ordered like the corners (point i has the signs
        of corner i times 1/sqrt(3)). thermal = (alpha, T): sigma = D (eps - eps_thermal)."""
        g = 1.0 / math.sqrt(3.0)
        pts = self.point_stresses(nodes, ue, g * CORNER_SIGNS, thermal)
        return np.stack([s for s, _, _ in pts], axis=1)

    def element_nodal_stresses(self, nodes: np.ndarray, ue: np.ndarray, thermal=None) -> np.ndarray:
        """Stresses extrapolated to each element's own nodes (E, n, 6)."""
        sg = self.gauss_stresses(nodes, ue, thermal)
        r3 = math.sqrt(3.0)
        Ex = np.array([shape_hex8(*(r3 * c))[0] for c in CORNER_SIGNS])  # (corner, gauss point)
        corners = np.einsum("cg,egk->eck", Ex, sg)
        if self.n_nodes == 8:
            return corners
        mids = 0.5 * (corners[:, EDGES[:, 0]] + corners[:, EDGES[:, 1]])
        return np.concatenate([corners, mids], axis=1)

    def nodal_stresses(self, nodes: np.ndarray, ue: np.ndarray, n_nodes_total: int | None = None, thermal=None):
        """Nodal-averaged stresses (N, 6) and the number of elements behind each node (N,)."""
        en = self.element_nodal_stresses(nodes, ue, thermal)
        n = n_nodes_total or len(nodes)
        acc = np.zeros((n, 6))
        cnt = np.zeros(n)
        np.add.at(acc, self.conn.ravel(), en.reshape(-1, 6))
        np.add.at(cnt, self.conn.ravel(), 1.0)
        return acc / np.maximum(cnt, 1.0)[:, None], cnt


class Hex8(_Hex):
    """8-node trilinear brick, 2 x 2 x 2 Gauss points (full integration)."""

    kind = "hex8"
    n_nodes = 8
    default_integration = 2
    faces = FACES8

    def shape(self, xi, eta, zeta):
        return shape_hex8(xi, eta, zeta)


class Hex20(_Hex):
    """20-node serendipity brick, 3 x 3 x 3 Gauss points by default (integration=2 for reduced)."""

    kind = "hex20"
    n_nodes = 20
    default_integration = 3
    faces = FACES20

    def shape(self, xi, eta, zeta):
        return shape_hex20(xi, eta, zeta)


def surface_load(nodes: np.ndarray, faces: np.ndarray, traction, n_nodes: int | None = None,
                 points: int = 3) -> np.ndarray:
    """Consistent nodal forces (N, 6) of a surface traction on faces (F, 8 or 4) of solid elements:
    f_i = integral of N_i t dA by points x points Gauss quadrature. traction: (3,) N/mm^2, or a function
    (x (P, 3), n (P, 3) outward unit normal for faces ordered counter-clockwise from outside) -> (P, 3)."""
    faces = np.atleast_2d(np.asarray(faces, int))
    shp = shape_quad8 if faces.shape[1] == 8 else shape_quad4
    p, w = gauss_1d(points)
    out = np.zeros((n_nodes or len(nodes), 6))
    X = nodes[faces]  # (F, n, 3)
    for a, wa in zip(p, w):
        for b, wb in zip(p, w):
            N, dN = shp(a, b)
            J = np.einsum("an,fnb->fab", dN, X)
            nrm = np.cross(J[:, 0], J[:, 1])
            dA = np.linalg.norm(nrm, axis=1)
            x = np.einsum("n,fnb->fb", N, X)
            t = traction(x, nrm / dA[:, None]) if callable(traction) else np.broadcast_to(np.asarray(traction, float), x.shape)
            f = N[None, :, None] * (t * (dA * wa * wb)[:, None])[:, None, :]
            np.add.at(out[:, :3], faces.ravel(), f.reshape(-1, 3))
    return out


def solid_stresses(sol, group, thermal=None) -> dict:
    """Nodal-averaged stresses (N, 6) and von Mises (N,) of one solid group in a Solution
    (nodes that the group does not touch get 0). thermal = (alpha, T[, nodal]) for a solution with thermal
    loads (nodal=True when T is per node)."""
    g = sol.model.groups[group] if isinstance(group, int) else group
    ue = sol.element_displacements(g)
    s, cnt = g.nodal_stresses(sol.model.nodes, ue, thermal=thermal)
    return {"stress": s, "von_mises": von_mises(s), "count": cnt}


__all__ = ["CORNER_SIGNS", "EDGES", "FACES20", "FACES8", "HEX20_NAT", "Hex20", "Hex8", "elasticity_matrix",
           "gauss_3d", "shape_hex20", "shape_hex8", "shape_quad4", "shape_quad8", "solid_stresses", "von_mises"]

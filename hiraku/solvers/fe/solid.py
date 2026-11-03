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
6 spurious zero-energy modes besides the 6 rigid motions (they vanish once bricks are joined, but a brick
held at a few nodes only - a corner of a part, a brick joined through a tie - keeps them). Full integration
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

    def gauss_stresses(self, nodes: np.ndarray, ue: np.ndarray) -> np.ndarray:
        """Stresses (E, 8, 6) at the 2 x 2 x 2 Gauss points ordered like the corners (point i has the signs
        of corner i times 1/sqrt(3))."""
        X = nodes[self.conn]
        D = elasticity_matrix(self.E, self.nu)
        g = 1.0 / math.sqrt(3.0)
        out = np.zeros((len(X), 8, 6))
        for i, sgn in enumerate(CORNER_SIGNS):
            _, dN = self.shape(*(g * sgn))
            _, _, dNx = jacobian(dN, X)
            eps = np.einsum("eak,ek->ea", strain_matrix(dNx), ue)
            out[:, i] = np.einsum("eab,eb->ea", D, eps)
        return out

    def element_nodal_stresses(self, nodes: np.ndarray, ue: np.ndarray) -> np.ndarray:
        """Stresses extrapolated to each element's own nodes (E, n, 6)."""
        sg = self.gauss_stresses(nodes, ue)
        r3 = math.sqrt(3.0)
        Ex = np.array([shape_hex8(*(r3 * c))[0] for c in CORNER_SIGNS])  # (corner, gauss point)
        corners = np.einsum("cg,egk->eck", Ex, sg)
        if self.n_nodes == 8:
            return corners
        mids = 0.5 * (corners[:, EDGES[:, 0]] + corners[:, EDGES[:, 1]])
        return np.concatenate([corners, mids], axis=1)

    def nodal_stresses(self, nodes: np.ndarray, ue: np.ndarray, n_nodes_total: int | None = None):
        """Nodal-averaged stresses (N, 6) and the number of elements behind each node (N,)."""
        en = self.element_nodal_stresses(nodes, ue)
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


def solid_stresses(sol, group) -> dict:
    """Nodal-averaged stresses (N, 6) and von Mises (N,) of one solid group in a Solution
    (nodes that the group does not touch get 0)."""
    g = sol.model.groups[group] if isinstance(group, int) else group
    ue = sol.element_displacements(g)
    s, cnt = g.nodal_stresses(sol.model.nodes, ue)
    return {"stress": s, "von_mises": von_mises(s), "count": cnt}


__all__ = ["CORNER_SIGNS", "EDGES", "FACES20", "FACES8", "HEX20_NAT", "Hex20", "Hex8", "elasticity_matrix",
           "gauss_3d", "shape_hex20", "shape_hex8", "shape_quad4", "shape_quad8", "solid_stresses", "von_mises"]

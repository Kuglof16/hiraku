"""MITC4 four-node shell element, vectorised over many elements.

Formulation (written here from the papers, no outside code):

- Each quadrilateral is treated as flat: a local frame (e1, e2, n) is set on
  its mean plane, n = g1 x g2 with g1, g2 the mid-side vectors in the xi and
  eta directions. For nodes listed counter-clockwise seen from outside, n
  points outward ("top" surface = outer side of a tube wall). For a flat
  element this is the same as the degenerated-shell MITC4; warped elements
  are projected onto the mean plane (`warp` reports how far nodes are off it;
  the frame mesher only makes flat ones).
- Bending and transverse shear: Reissner-Mindlin plate with the MITC4
  assumed shear strain of Dvorkin & Bathe (1984), "A continuum mechanics
  based four-node shell element for general non-linear analysis",
  Engineering Computations 1, 77-88 (and the plate form in Bathe & Dvorkin
  1985, IJNME 21, 367-383): the covariant shear strains e_xi z and e_eta z
  are taken at the mid-points of the element edges and interpolated linearly
  across the element, which removes shear locking. Shear correction 5/6.
- Membrane: bilinear displacements plus Wilson's incompatible modes
  (1 - xi^2, 1 - eta^2) with Taylor's correction (derivatives through the
  centre Jacobian, scaled by detJ0 / detJ: Taylor, Beresford & Wilson 1976,
  IJNME 10, 1211-1219), condensed out per element. This keeps in-plane
  bending of tube walls from locking and passes the membrane patch test on
  distorted elements.
- Drilling rotation (about n) has no physical stiffness. It is held by a
  small fictitious stiffness DRILL_FACTOR * G * t * area on two terms:
  the centre value of (theta_n - in-plane rotation of the membrane field)
  (a Hughes-Brezzi type penalty) and the hourglass pattern (+, -, +, -) of
  the nodal theta_n. Rigid-body motions and the linear theta_n field of
  in-plane bending get no energy from either.

Degrees of freedom: 6 per node in global axes (ux, uy, uz, rx, ry, rz),
element vector ordered node by node. Units: N, mm, MPa, tonne.

Local sign convention: rotation vector (rx, ry) about local x, y; a point at
height z above the mid-surface moves (z ry, -z rx). So
    curvatures  kx = d ry/dx,  ky = -d rx/dy,  kxy = d ry/dy - d rx/dx
    shear       gxz = dw/dx + ry,  gyz = dw/dy - rx
and the stress at height z is D_plane (eps_membrane + z kappa).
"""

from __future__ import annotations

import math

import numpy as np

DRILL_FACTOR = 1.0e-3  # a numerical penalty, HIRAKU's choice, no source
SHEAR_FACTOR = 5.0 / 6.0  # the textbook shear correction of a Reissner-Mindlin plate (homogeneous section)

XI_N = np.array([-1.0, 1.0, 1.0, -1.0])
ETA_N = np.array([-1.0, -1.0, 1.0, 1.0])
_G = 1.0 / math.sqrt(3.0)
GAUSS_2x2 = [(-_G, -_G), (_G, -_G), (_G, _G), (-_G, _G)]


def shape(xi: float, eta: float) -> tuple[np.ndarray, np.ndarray]:
    """Bilinear shape functions N (4,) and their derivatives dN/d(xi, eta) (2, 4)."""
    N = 0.25 * (1.0 + XI_N * xi) * (1.0 + ETA_N * eta)
    dN = np.array([0.25 * XI_N * (1.0 + ETA_N * eta), 0.25 * ETA_N * (1.0 + XI_N * xi)])
    return N, dN


def local_frames(xyz: np.ndarray):
    """Element frames for corner coordinates xyz (E, 4, 3).

    Returns R (E, 3, 3) with rows e1, e2, n (local = R @ global), the local
    in-plane corner coordinates xl (E, 4, 2) about the centre c (E, 3), and
    warp (E,), the largest distance of a corner from the mean plane (mm).
    """
    c = xyz.mean(axis=1)
    g1 = 0.5 * ((xyz[:, 1] + xyz[:, 2]) - (xyz[:, 0] + xyz[:, 3]))
    g2 = 0.5 * ((xyz[:, 2] + xyz[:, 3]) - (xyz[:, 0] + xyz[:, 1]))
    n = np.cross(g1, g2)
    n /= np.linalg.norm(n, axis=1)[:, None]
    e1 = g1 - np.einsum("ei,ei->e", g1, n)[:, None] * n
    e1 /= np.linalg.norm(e1, axis=1)[:, None]
    e2 = np.cross(n, e1)
    R = np.stack([e1, e2, n], axis=1)
    d = xyz - c[:, None, :]
    xl = np.einsum("eij,enj->eni", R[:, :2, :], d)
    warp = np.abs(np.einsum("enj,ej->en", d, n)).max(axis=1)
    return R, xl, c, warp


def _jacobian(dN: np.ndarray, xl: np.ndarray):
    """J (E,2,2) with rows x_xi, x_eta; detJ (E,); invJ (E,2,2)."""
    J = np.einsum("ak,ekb->eab", dN, xl)
    det = J[:, 0, 0] * J[:, 1, 1] - J[:, 0, 1] * J[:, 1, 0]
    if np.any(det <= 0.0):
        raise ValueError("an element has a non-positive Jacobian (badly shaped or wrongly ordered nodes)")
    inv = np.empty_like(J)
    inv[:, 0, 0] = J[:, 1, 1] / det
    inv[:, 1, 1] = J[:, 0, 0] / det
    inv[:, 0, 1] = -J[:, 0, 1] / det
    inv[:, 1, 0] = -J[:, 1, 0] / det
    return J, det, inv


def plane_stress_matrix(E: np.ndarray, nu: np.ndarray) -> np.ndarray:
    """Plane-stress elasticity D (E_count, 3, 3) for strains (ex, ey, gxy)."""
    f = E / (1.0 - nu * nu)
    D = np.zeros((len(E), 3, 3))
    D[:, 0, 0] = D[:, 1, 1] = f
    D[:, 0, 1] = D[:, 1, 0] = f * nu
    D[:, 2, 2] = f * (1.0 - nu) / 2.0
    return D


def membrane_B(dNx: np.ndarray) -> np.ndarray:
    """Membrane strain matrix (E, 3, 8) for (u1, v1, ..., u4, v4); dNx (E, 2, 4) Cartesian derivatives."""
    E = dNx.shape[0]
    B = np.zeros((E, 3, 8))
    B[:, 0, 0::2] = dNx[:, 0]
    B[:, 1, 1::2] = dNx[:, 1]
    B[:, 2, 0::2] = dNx[:, 1]
    B[:, 2, 1::2] = dNx[:, 0]
    return B


def bending_B(dNx: np.ndarray) -> np.ndarray:
    """Curvature matrix (E, 3, 12) for (w1, rx1, ry1, ..., w4, rx4, ry4)."""
    E = dNx.shape[0]
    B = np.zeros((E, 3, 12))
    B[:, 0, 2::3] = dNx[:, 0]
    B[:, 1, 1::3] = -dNx[:, 1]
    B[:, 2, 2::3] = dNx[:, 1]
    B[:, 2, 1::3] = -dNx[:, 0]
    return B


def _covariant_shear_row(xl: np.ndarray, xi: float, eta: float, direction: int) -> np.ndarray:
    """Row (E, 12) giving the covariant transverse shear strain along xi (0) or eta (1) at (xi, eta)."""
    N, dN = shape(xi, eta)
    g = np.einsum("k,ekb->eb", dN[direction], xl)  # (E, 2): x_xi or x_eta
    E = xl.shape[0]
    row = np.zeros((E, 12))
    row[:, 0::3] = dN[direction]
    row[:, 1::3] = -N[None, :] * g[:, 1:2]  # beta = (ry, -rx); beta . g
    row[:, 2::3] = N[None, :] * g[:, 0:1]
    return row


def mitc4_shear_B(xl: np.ndarray, xi: float, eta: float, invJ: np.ndarray) -> np.ndarray:
    """Cartesian transverse shear strain matrix (E, 2, 12) with MITC4 edge tying."""
    a_lo = _covariant_shear_row(xl, 0.0, -1.0, 0)  # e_xi z on edge 1-2
    a_hi = _covariant_shear_row(xl, 0.0, 1.0, 0)  # e_xi z on edge 4-3
    b_lo = _covariant_shear_row(xl, -1.0, 0.0, 1)  # e_eta z on edge 1-4
    b_hi = _covariant_shear_row(xl, 1.0, 0.0, 1)  # e_eta z on edge 2-3
    cov = np.stack([0.5 * (1.0 - eta) * a_lo + 0.5 * (1.0 + eta) * a_hi,
                    0.5 * (1.0 - xi) * b_lo + 0.5 * (1.0 + xi) * b_hi], axis=1)
    return np.einsum("eab,ebk->eak", invJ, cov)


# local DOF positions within the 24-vector (node-major, 6 per node)
_MEM = np.array([6 * i + k for i in range(4) for k in (0, 1)])
_BEN = np.array([6 * i + k for i in range(4) for k in (2, 3, 4)])
_DRL = np.array([6 * i + 5 for i in range(4)])
_HOURGLASS = np.array([1.0, -1.0, 1.0, -1.0])


def local_stiffness(xl: np.ndarray, t: np.ndarray, E: np.ndarray, nu: np.ndarray,
                    drill_factor: float = DRILL_FACTOR, incompatible: bool = True) -> np.ndarray:
    """Element stiffness in local axes (Ecount, 24, 24).

    incompatible=False drops the incompatible membrane modes (plain bilinear
    membrane, which locks in in-plane bending); only for showing that effect.
    """
    ne = xl.shape[0]
    D = plane_stress_matrix(E, nu)
    Dm = D * t[:, None, None]
    Db = D * (t**3 / 12.0)[:, None, None]
    G = E / (2.0 * (1.0 + nu))
    ds = SHEAR_FACTOR * G * t

    _, dN0 = shape(0.0, 0.0)
    _, det0, inv0 = _jacobian(dN0, xl)

    Kmm = np.zeros((ne, 8, 8))
    Kmi = np.zeros((ne, 8, 4))
    Kii = np.zeros((ne, 4, 4))
    Kb = np.zeros((ne, 12, 12))
    area = np.zeros(ne)
    for xi, eta in GAUSS_2x2:
        _, dN = shape(xi, eta)
        _, det, inv = _jacobian(dN, xl)
        dNx = np.einsum("eab,bk->eak", inv, dN)
        Bm = membrane_B(dNx)
        # incompatible modes P1 = 1 - xi^2, P2 = 1 - eta^2 (Taylor: centre Jacobian, detJ0/detJ)
        dP = np.array([[-2.0 * xi, 0.0], [0.0, -2.0 * eta]])  # rows: d/dxi, d/deta ; cols: P1, P2
        dPx = np.einsum("eab,bk->eak", inv0, dP) * (det0 / det)[:, None, None]  # (E, 2, 2): d/dx,d/dy of P1,P2
        Bi = np.zeros((ne, 3, 4))  # (a1 u-P1, a2 u-P2, a3 v-P1, a4 v-P2)
        Bi[:, 0, 0:2] = dPx[:, 0]
        Bi[:, 1, 2:4] = dPx[:, 1]
        Bi[:, 2, 0:2] = dPx[:, 1]
        Bi[:, 2, 2:4] = dPx[:, 0]
        DmBm = np.einsum("eab,ebk->eak", Dm, Bm)
        DmBi = np.einsum("eab,ebk->eak", Dm, Bi)
        Kmm += np.einsum("eaj,eak->ejk", Bm, DmBm) * det[:, None, None]
        Kmi += np.einsum("eaj,eak->ejk", Bm, DmBi) * det[:, None, None]
        Kii += np.einsum("eaj,eak->ejk", Bi, DmBi) * det[:, None, None]
        Bb = bending_B(dNx)
        Bs = mitc4_shear_B(xl, xi, eta, inv)
        Kb += np.einsum("eaj,eab,ebk->ejk", Bb, Db, Bb) * det[:, None, None]
        Kb += np.einsum("eaj,eak->ejk", Bs, Bs) * (ds * det)[:, None, None]
        area += det
    Km = Kmm
    if incompatible:
        Km = Kmm - np.einsum("eij,ejk->eik", Kmi, np.linalg.solve(Kii, np.transpose(Kmi, (0, 2, 1))))

    K = np.zeros((ne, 24, 24))
    K[:, _MEM[:, None], _MEM[None, :]] = Km
    K[:, _BEN[:, None], _BEN[None, :]] = Kb

    # drilling: centre penalty on (theta_n - omega) plus a hourglass term on theta_n
    dNx0 = np.einsum("eab,bk->eak", inv0, dN0)
    idx = np.concatenate([_MEM, _DRL])  # u1 v1 .. u4 v4, rz1..rz4
    Bd = np.zeros((ne, 12))
    Bd[:, 0:8:2] = 0.5 * dNx0[:, 1]  # u_i
    Bd[:, 1:8:2] = -0.5 * dNx0[:, 0]  # v_i
    Bd[:, 8:12] = 0.25
    kd = drill_factor * G * t * area
    Kd = np.einsum("ej,ek->ejk", Bd, Bd) * kd[:, None, None]
    Kd[:, 8:12, 8:12] += kd[:, None, None] * (np.outer(_HOURGLASS, _HOURGLASS) / 4.0)[None]
    K[:, idx[:, None], idx[None, :]] += Kd
    return K


def to_global(K_local: np.ndarray, R: np.ndarray) -> np.ndarray:
    """Rotate element matrices (E, 24, 24) from local to global axes (R rows = local axes)."""
    ne = K_local.shape[0]
    K5 = K_local.reshape(ne, 8, 3, 8, 3).transpose(0, 1, 3, 2, 4)  # (E, 8, 8, 3, 3)
    Rb = R[:, None, None, :, :]
    Kg = np.matmul(np.matmul(np.transpose(Rb, (0, 1, 2, 4, 3)), K5), Rb)
    return Kg.transpose(0, 1, 3, 2, 4).reshape(ne, 24, 24)


def global_stiffness_blocks(xyz: np.ndarray, t: np.ndarray, E: np.ndarray, nu: np.ndarray,
                            drill_factor: float = DRILL_FACTOR, incompatible: bool = True) -> np.ndarray:
    """Element stiffness matrices in global axes (Ecount, 24, 24) for corners xyz (E, 4, 3)."""
    R, xl, _, _ = local_frames(xyz)
    return to_global(local_stiffness(xl, t, E, nu, drill_factor, incompatible), R)


def element_area(xl: np.ndarray) -> np.ndarray:
    """Area of each (flat) element from its local corner coordinates (E, 4, 2)."""
    x, y = xl[:, :, 0], xl[:, :, 1]
    return 0.5 * np.abs(np.sum(x * np.roll(y, -1, axis=1) - np.roll(x, -1, axis=1) * y, axis=1))


def lumped_mass(xyz: np.ndarray, t: np.ndarray, rho: np.ndarray) -> np.ndarray:
    """Lumped (diagonal) mass per element and local DOF (E, 24).

    A quarter of rho * t * area on each node's three translations, and the
    same quarter times t^2 / 12 on each of its three rotations (the rotary
    inertia of the wall about its mid-surface, used for all three axes so the
    block is unchanged by the rotation to global axes). rho in t/mm^3.
    """
    _, xl, _, _ = local_frames(xyz)
    m = rho * t * element_area(xl) / 4.0
    out = np.zeros((len(t), 24))
    for i in range(4):
        out[:, 6 * i:6 * i + 3] = m[:, None]
        out[:, 6 * i + 3:6 * i + 6] = (m * t * t / 12.0)[:, None]
    return out


def centre_strains(xyz: np.ndarray, u_global: np.ndarray):
    """Membrane strain, curvature (both (E, 3)) and transverse shear (E, 2) at element centres.

    u_global (E, 24) element displacement vectors in global axes. The
    incompatible membrane modes have zero derivative at the centre, so the
    centre membrane strain comes from the nodal displacements alone.
    Returns (eps, kappa, gamma, R).
    """
    R, xl, _, _ = local_frames(xyz)
    ne = xyz.shape[0]
    ul = np.einsum("eij,enj->eni", R, u_global.reshape(ne, 8, 3)).reshape(ne, 24)
    _, dN0 = shape(0.0, 0.0)
    _, _, inv0 = _jacobian(dN0, xl)
    dNx = np.einsum("eab,bk->eak", inv0, dN0)
    eps = np.einsum("eak,ek->ea", membrane_B(dNx), ul[:, _MEM])
    kap = np.einsum("eak,ek->ea", bending_B(dNx), ul[:, _BEN])
    gam = np.einsum("eak,ek->ea", mitc4_shear_B(xl, 0.0, 0.0, inv0), ul[:, _BEN])
    return eps, kap, gam, R

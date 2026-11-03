"""Known-answer checks of the three-node shell triangles (tri.Tri3) alone and mixed with MITC4.

Each expected value is computed here from its formula (sources as in load_checks and eigen_checks):
- [Patch] a linear in-plane displacement field and a quadratic deflection w (rotations rx = w_,y,
  ry = -w_,x) given on the boundary nodes of an irregular patch; the inner nodes must take the same field
  and every element the constant strain and curvature (Irons' patch test, as described in Zienkiewicz,
  Taylor & Zhu 2005; not checked here).
- [Navier] load_checks.navier.
- [Cantilever] a plate strip with nu = 0 under an end line load P bends as a beam: w = P L^3 / (3 E I),
  I = b t^3 / 12 (nu = 0 removes the plate's anticlastic stiffening, so the beam formula is exact for a
  Kirchhoff plate); MITC4 adds the shear term P L / (k G b t), k = 5/6.
"""

from __future__ import annotations

import numpy as np

from . import loads
from .eigen_checks import E_ST, NU_ST, plate_shell
from .load_checks import navier
from .model import FEModel, StaticSolver
from .shell import MITC4
from .tri import Tri3, split_quads


def mixed_groups(model: FEModel, quads: np.ndarray, kind: str, t: float, E: float, nu: float, rho: float = 0.0,
                 tri_where=None, bending: str = "dkt") -> list:
    """Element groups on a quad mesh: 'quad' (MITC4 only), 'tri' (every quad split, alternating diagonals) or
    'mixed' (the quads whose centre satisfies tri_where(x, y) split, the others MITC4)."""
    quads = np.asarray(quads, int)
    if kind == "quad":
        return [model.add(MITC4(quads, t, E, nu, rho))]
    if kind == "tri":
        return [model.add(Tri3(split_quads(quads), t, E, nu, rho, bending=bending))]
    c = model.nodes[quads].mean(axis=1)
    sel = np.asarray(tri_where(c[:, 0], c[:, 1]), bool)
    gs = [model.add(Tri3(split_quads(quads[sel]), t, E, nu, rho, bending=bending))]
    if np.any(~sel):
        gs.append(model.add(MITC4(quads[~sel], t, E, nu, rho)))
    return gs


def patch_mesh(a: float = 100.0, b: float = 60.0, nx: int = 4, ny: int = 3, shake: float = 0.18, seed: int = 3):
    """A plate_shell grid with the inner nodes moved by up to `shake` of the spacing (fixed seed)."""
    nodes, q = plate_shell(a, b, nx, ny)
    rng = np.random.default_rng(seed)
    x, y = nodes[:, 0], nodes[:, 1]
    inner = (x > 1e-9) & (x < a - 1e-9) & (y > 1e-9) & (y < b - 1e-9)
    nodes = nodes.copy()
    nodes[inner, 0] += shake * (a / nx) * rng.uniform(-1, 1, inner.sum())
    nodes[inner, 1] += shake * (b / ny) * rng.uniform(-1, 1, inner.sum())
    return nodes, q, inner


PATCH_U = np.array([1e-3, -2e-3, 4e-4, -3e-4, 6e-4, 5e-4])  # u = c0 + c2 x + c3 y, v = c1 + c4 x + c5 y
PATCH_W = np.array([0.02, 3e-5, -2e-5, 4e-5])  # w = c0 + c1 x^2 + c2 x y + c3 y^2


def patch_field(xy: np.ndarray) -> np.ndarray:
    """(N, 6) displacements and rotations of [Patch]'s field at points xy (N, 2) of a plate in z = 0."""
    x, y = xy[:, 0], xy[:, 1]
    cu, cw = PATCH_U, PATCH_W
    u = cu[0] + cu[2] * x + cu[3] * y
    v = cu[1] + cu[4] * x + cu[5] * y
    w = cw[0] + cw[1] * x * x + cw[2] * x * y + cw[3] * y * y
    wx = 2 * cw[1] * x + cw[2] * y
    wy = cw[2] * x + 2 * cw[3] * y
    rz = np.full_like(x, 0.5 * (cu[4] - cu[3]))
    return np.stack([u, v, w, wy, -wx, rz], axis=1)


def patch_test(kind: str = "tri", t: float = 2.0, bending: str = "dkt") -> dict:
    """[Patch] on patch_mesh, every DOF of the boundary nodes given, no load. Returns the largest error of
    the inner nodes' DOFs (relative to the field's largest value) and of the element strains and curvatures."""
    nodes, q, inner = patch_mesh()
    model = FEModel(nodes)
    gs = mixed_groups(model, q, kind, t, E_ST, NU_ST, tri_where=lambda x, y: (x > 50.0) | (y < 20.0), bending=bending)
    exact = patch_field(nodes[:, :2])
    for n in np.nonzero(~inner)[0]:
        for k in range(6):
            model.prescribe(int(n), k, float(exact[n, k]))
    sol = StaticSolver(model).solve(np.zeros((len(nodes), 6)))
    d = sol.displacements
    cu, cw = PATCH_U, PATCH_W
    eps_x = np.array([cu[2], cu[5], cu[3] + cu[4]])
    kap_x = np.array([-2 * cw[1], -2 * cw[3], -2 * cw[2]])
    err_e = err_k = 0.0
    for g in gs:
        ue = sol.element_displacements(g)
        if isinstance(g, Tri3):
            eps, kap, R = g.strains(model.nodes, ue)
            # several points in the element (the field must be constant everywhere)
            for at in ((0.6, 0.3, 0.1), (0.1, 0.1, 0.8)):
                kap = np.vstack([kap, g.strains(model.nodes, ue, at)[1]])
        else:
            from ..k1_shell import element as mitc4
            eps, kap, _, R = mitc4.centre_strains(model.nodes[g.conn], ue)
        # local axes of a flat plate in z = 0 are rotated about z: compare invariants by rotating back
        c, s = R[:, 0, 0], R[:, 0, 1]
        reps = np.stack([c * c * eps_x[0] + s * s * eps_x[1] + c * s * eps_x[2],
                         s * s * eps_x[0] + c * c * eps_x[1] - c * s * eps_x[2],
                         -2 * c * s * eps_x[0] + 2 * c * s * eps_x[1] + (c * c - s * s) * eps_x[2]], axis=1)
        rk = np.stack([c * c * kap_x[0] + s * s * kap_x[1] + c * s * kap_x[2],
                       s * s * kap_x[0] + c * c * kap_x[1] - c * s * kap_x[2],
                       -2 * c * s * kap_x[0] + 2 * c * s * kap_x[1] + (c * c - s * s) * kap_x[2]], axis=1)
        err_e = max(err_e, float(np.abs(eps[:len(R)] - reps).max() / np.abs(eps_x).max()))
        err_k = max(err_k, float(np.abs(kap - np.tile(rk, (len(kap) // len(R), 1))).max() / np.abs(kap_x).max()))
    err_u = float(np.abs(d[inner] - exact[inner]).max() / np.abs(exact).max())
    return {"inner_error": err_u, "strain_error": err_e, "curvature_error": err_k, "n_inner": int(inner.sum())}


def ss_plate(kind: str = "tri", a: float = 200.0, b: float = 200.0, t: float = 2.0, q: float = 0.01, nx: int = 16,
             ny: int = 16, bending: str = "dkt") -> dict:
    """[Navier] simply supported plate (w = 0 and the rotation about the edge's normal held on the edges, as
    load_checks.ss_plate_pressure), uniform pressure q on the bottom face. 'mixed': triangles on x > a / 2."""
    nodes, qd = plate_shell(a, b, nx, ny)
    model = FEModel(nodes)
    gs = mixed_groups(model, qd, kind, t, E_ST, NU_ST, tri_where=lambda x, y: x > a / 2, bending=bending)
    x, y = nodes[:, 0], nodes[:, 1]
    model.fix(np.nonzero((x < 1e-9) | (x > a - 1e-9) | (y < 1e-9) | (y > b - 1e-9))[0], [2])
    model.fix(np.nonzero((x < 1e-9) | (x > a - 1e-9))[0], [3])
    model.fix(np.nonzero((y < 1e-9) | (y > b - 1e-9))[0], [4])
    model.fix(0, [0, 1])
    model.fix(nx, [1])
    F = sum(loads.shell_pressure(model, g, q, "bottom") for g in gs)
    sol = StaticSolver(model).solve(F)
    c = int(np.argmin(np.hypot(x - a / 2, y - b / 2)))
    return {"w_centre": float(sol.displacements[c, 2]), "w_navier": navier(a, b, t, q, a / 2, b / 2)["w"],
            "total_load": float(F[:, 2].sum()), "q_area": q * a * b,
            "w_quarter": float(sol.displacements[int(np.argmin(np.hypot(x - a / 4, y - b / 2))), 2]),
            "w_three_quarter": float(sol.displacements[int(np.argmin(np.hypot(x - 3 * a / 4, y - b / 2))), 2])}


def cantilever_strip(kind: str = "tri", L: float = 200.0, b: float = 20.0, t: float = 2.0, P: float = 10.0,
                     nx: int = 20, ny: int = 2, E: float = E_ST, bending: str = "dkt") -> dict:
    """[Cantilever] strip clamped on x = 0 with nu = 0, end line load P (total, along -z... here +z) on x = L
    shared by the edge nodes (half on the two corners). 'mixed': triangles on the half x > L / 2."""
    nodes, qd = plate_shell(L, b, nx, ny)
    model = FEModel(nodes)
    mixed_groups(model, qd, kind, t, E, 0.0, tri_where=lambda x, y: x > L / 2, bending=bending)
    x = nodes[:, 0]
    model.fix(np.nonzero(x < 1e-9)[0], "all")
    tip = np.nonzero(x > L - 1e-9)[0]
    F = np.zeros((len(nodes), 6))
    w = np.full(len(tip), 1.0 / ny)
    w[np.isclose(nodes[tip, 1], 0.0) | np.isclose(nodes[tip, 1], b)] = 0.5 / ny
    F[tip, 2] = P * w
    sol = StaticSolver(model).solve(F)
    I = b * t ** 3 / 12.0
    G = E / 2.0
    return {"w_tip": float(sol.displacements[tip, 2].mean()), "w_beam": P * L ** 3 / (3 * E * I),
            "w_shear": P * L / (5.0 / 6.0 * G * b * t), "spread": float(np.ptp(sol.displacements[tip, 2]))}


DRILL_SPRING = 1e-3  # N mm / rad


def drill_springs(model: FEModel, ids, comp: int, k: float = DRILL_SPRING) -> None:
    """Very soft ground springs (k N mm/rad) on the global rotation `comp` (the walls' normal) of flat MITC4
    meshes. MITC4's drilling terms (k1_shell: the centre penalty and the hourglass pattern) leave the two
    linear patterns (1, 0, -1, 0), (0, 1, 0, -1) of an element's normal rotations free, and a flat structured
    mesh assembles them into zero-energy checkerboards (measured: two eigenvalues of 1e-17 of the largest on
    a 4 x 6 plate, which then fails the static solve). Holding the rotation instead is wrong: the drilling
    penalty (theta_n - omega) then puts reaction couples on the in-plane rotation omega and takes part of an
    in-plane bending moment from the membrane (measured on strip_lateral_buckling: M_cr 16 % high at L / h
    = 20, 2.4 times at L / h = 80). The springs here are 1e-8 of the drilling penalty's scale."""
    from .connect import Spring

    ids = np.asarray(ids, int)
    kk = np.zeros(6)
    kk[comp] = k
    model.add(Spring(np.stack([ids, -np.ones(len(ids), int)], axis=1), kk, rotations=True, name="drill springs"))


def ritz_linear_edge(a: float, b: float, t: float, psi: float, n_terms: int = 30, m_max: int = 8) -> dict:
    """[Ritz] buckling stress sigma0 of a simply supported a x b plate whose edges x = 0, a carry the
    compression sigma(y) = sigma0 (1 - psi y / b) (psi = 0 uniform, 2 pure in-plane bending), by the energy
    method with w = sum a_mn sin(m pi x / a) sin(n pi y / b), n = 1..n_terms (the method of Timoshenko &
    Gere 1961, chapter 9, for plates under in-plane bending; its tabulated k, e.g. 23.9 for pure bending,
    is not used here and not checked). Strain energy (pi^4 a b D / 8) sum a_mn^2 (m^2/a^2 + n^2/b^2)^2; work
    of the stress (t / 2) integral sigma(y) w_,x^2 dA = (t a / 4) (m pi / a)^2 sum_n,p a_mn a_mp I_np,
    I_np = integral_0^b sigma(y) / sigma0 sin(n pi y / b) sin(p pi y / b) dy (Gauss-Legendre, 200 points).
    The m terms do not couple; for each m the smallest positive eigenvalue of K a = sigma0 G a, then the
    smallest over m. A Ritz value: an upper bound that falls as n_terms grows."""
    from scipy.linalg import eigh

    D = E_ST * t ** 3 / (12 * (1 - NU_ST ** 2))
    gp, gw = np.polynomial.legendre.leggauss(200)
    y = 0.5 * b * (gp + 1.0)
    wy = 0.5 * b * gw
    n = np.arange(1, n_terms + 1)
    S = np.sin(np.outer(n, y) * np.pi / b)
    I = np.einsum("ny,py,y->np", S, S, wy * (1.0 - psi * y / b))
    best = None
    for m in range(1, m_max + 1):
        K = np.diag(np.pi ** 4 * a * b * D / 4.0 * (m * m / a ** 2 + n * n / b ** 2) ** 2)
        G = t * a / 2.0 * (m * np.pi / a) ** 2 * I
        lam = eigh(G, K, eigvals_only=True)  # G a = mu K a, sigma0 = 1 / mu for the largest mu > 0
        mu = lam.max()
        s = 1.0 / mu
        if best is None or s < best[0]:
            best = (s, m)
    return {"sigma_cr": best[0], "m": best[1], "k": best[0] * b * b * t / (np.pi ** 2 * D)}


def linear_edge_buckling(psi: float = 2.0, a: float = 200.0, b: float = 300.0, t: float = 2.0, nx: int = 8,
                         ny: int = 12, kind: str = "quad", at: str = "gauss", thickness_terms: bool = True) -> dict:
    """The plate of ritz_linear_edge in MITC4 (or triangles): w = 0 and the rotation about the edge's in-plane
    normal held on the four edges (eigen_checks.plate_buckling's support), ux = 0 on x = 0 and uy = 0 at
    (0, 0); the edge x = a carries the consistent nodal forces of the traction -sigma0 (1 - psi y / b) t
    (linear on each element edge: h (2 q_i + q_j) / 6). The stress inside is exactly sigma(y) (a linear
    sigma_x, sigma_y = tau = 0 satisfies equilibrium and compatibility), so it varies across every element.
    at, thickness_terms: passed to the MITC4 groups' geometric_stiffness."""
    from . import eigen

    nodes, q = plate_shell(a, b, nx, ny)
    model = FEModel(nodes)
    gs = mixed_groups(model, q, kind, t, E_ST, NU_ST, tri_where=lambda x, y: x > a / 2)
    x, y = nodes[:, 0], nodes[:, 1]
    model.fix(np.nonzero((x < 1e-9) | (x > a - 1e-9) | (y < 1e-9) | (y > b - 1e-9))[0], [2])
    model.fix(np.nonzero((x < 1e-9) | (x > a - 1e-9))[0], [3])
    model.fix(np.nonzero((y < 1e-9) | (y > b - 1e-9))[0], [4])
    model.fix(np.nonzero(x < 1e-9)[0], [0])
    model.fix(0, [1])
    drill_springs(model, np.arange(len(nodes)), 5)
    F = np.zeros((len(nodes), 6))
    right = np.nonzero(x > a - 1e-9)[0]
    right = right[np.argsort(y[right])]
    qv = -(1.0 - psi * y[right] / b) * t  # force per length along x for sigma0 = 1
    for i in range(len(right) - 1):
        h = y[right[i + 1]] - y[right[i]]
        F[right[i], 0] += h * (2 * qv[i] + qv[i + 1]) / 6.0
        F[right[i + 1], 0] += h * (qv[i] + 2 * qv[i + 1]) / 6.0
    for g in gs:
        if isinstance(g, MITC4):
            g.kg_options = {"at": at, "thickness_terms": thickness_terms}
    r = eigen.buckling(model, F, 2)
    rz = ritz_linear_edge(a, b, t, psi)
    return {"sigma_cr": float(r.factors[0]), "sigma_ritz": rz["sigma_cr"], "k_ritz": rz["k"], "m_ritz": rz["m"],
            "factors": r.factors}


def narrow_strip_torsion_constant(h: float, t: float) -> float:
    """[Strip] J of a thin rectangle h x t: h t^3 / 3 (1 - 0.630 t / h) (the series of Saint-Venant's solution
    cut to its first correction, as commonly quoted, e.g. Timoshenko & Goodier, Theory of Elasticity;
    not checked here)."""
    return h * t ** 3 / 3.0 * (1.0 - 0.630 * t / h)


def strip_lateral_buckling(L: float = 1000.0, h: float = 50.0, t: float = 2.0, nx: int = 40, nz: int = 4,
                           at: str = "gauss", thickness_terms: bool = True) -> dict:
    """[Strip] lateral-torsional buckling of a deep narrow strip (in the x-z plane, thickness t along y) bent in
    its own plane by equal and opposite end moments M about y, in MITC4 shells (nz elements over the depth).
    Fork ends: uy = 0 on every end node (lateral and twist held), uz = 0 at the bottom end nodes' middle
    (here the node at z = 0); ux = 0 at one node; the drilling rotation (global ry, the walls' normal) held
    held by drill_springs. The end moment is the linear axial traction sigma = M (z - h/2) / I
    over the end edge, consistent nodal forces. Against M_cr = (pi / L) sqrt(E I_z G J) with I_z = h t^3 / 12
    (Timoshenko & Gere 1961, chapter 6, for a beam without warping stiffness, not checked here; a thin
    rectangle's warping is negligible) - with E and with E / (1 - nu^2) (a plate's lateral bending; the strip lies between)."""
    from . import eigen

    xs, zs = np.linspace(0, L, nx + 1), np.linspace(0, h, nz + 1)
    nodes = np.array([[x, 0.0, z] for z in zs for x in xs])
    q = [[j * (nx + 1) + i, (j + 1) * (nx + 1) + i, (j + 1) * (nx + 1) + i + 1, j * (nx + 1) + i + 1]
         for j in range(nz) for i in range(nx)]
    model = FEModel(nodes)
    g = model.add(MITC4(q, t, E_ST, NU_ST))
    g.kg_options = {"at": at, "thickness_terms": thickness_terms}
    x, z = nodes[:, 0], nodes[:, 2]
    ends = [np.nonzero(x < 1e-9)[0], np.nonzero(x > L - 1e-9)[0]]
    for e in ends:
        model.fix(e, [1])
        model.fix(e[np.argmin(z[e])], [2])
    model.fix(ends[0][np.argmin(z[ends[0]])], [0])
    drill_springs(model, np.arange(len(nodes)), 4)
    I = t * h ** 3 / 12.0
    M = 1.0e5
    F = np.zeros((len(nodes), 6))
    for sgn, e in zip((-1.0, 1.0), ends):
        e = e[np.argsort(z[e])]
        qv = sgn * M * (zs - h / 2) / I * t  # N/mm along x: the moment about +y at x = L is M
        for i in range(nz):
            dz = zs[i + 1] - zs[i]
            F[e[i], 0] += dz * (2 * qv[i] + qv[i + 1]) / 6.0
            F[e[i + 1], 0] += dz * (qv[i] + 2 * qv[i + 1]) / 6.0
    r = eigen.buckling(model, F, 2)
    G = E_ST / (2 * (1 + NU_ST))
    Iz = h * t ** 3 / 12.0
    J = narrow_strip_torsion_constant(h, t)
    Mb = np.pi / L * np.sqrt(E_ST * Iz * G * J)
    return {"M_cr": float(r.factors[0] * M), "M_beam": Mb, "M_plate": Mb / np.sqrt(1 - NU_ST ** 2),
            "factors": r.factors}


def tri_plate_buckling(a: float = 200.0, b: float = 200.0, nx: int = 16, ny: int = 16, kind: str = "tri",
                       bending: str = "dkt") -> dict:
    """eigen_checks.plate_buckling (simply supported plate, uniaxial compression) on triangles."""
    from .eigen_checks import plate_buckling

    return plate_buckling(a=a, b=b, nx=nx, ny=ny, kind=kind, bending=bending)


__all__ = ["cantilever_strip", "tri_plate_buckling", "mixed_groups", "patch_field", "patch_mesh", "patch_test", "ss_plate"]

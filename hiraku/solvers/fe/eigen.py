"""Eigenproblems on the fe core: linear buckling and natural modes.

Both work on the reduced unknowns of the static solver (u = T u_r, the constraints and supports
eliminated as in model.py), so every constraint and support of a static model applies unchanged.

Linear buckling
---------------
A static solve of the reference load F gives U; each element group builds its initial-stress
(geometric) stiffness from U (group.geometric_stiffness(nodes, ue)), assembled to K_G. The load
factors are the eigenvalues of
    (K + lambda K_G) phi = 0,    i.e.  K phi = lambda (-K_G) phi
(Bathe, Finite Element Procedures, 1996, section 6.8.2; Cook, Malkus, Plesha & Witt 2002, chapter on
buckling - neither checked here). The critical load is lambda_1 F.
How it is solved: scipy.sparse.linalg.eigsh on the inverted pencil (-K_G) phi = mu K phi with M = K,
using the static solver's factorisation of K for K^-1 (ARPACK mode 2, Lanczos in the K inner product),
and lambda = 1 / mu for the largest positive mu. This is shift-invert about lambda = 0: scipy's own
'buckling' mode needs a non-zero shift and degenerates at sigma = 0 (OP = I). A shift sigma > 0 (a load
factor near the wanted ones) can be given and then eigsh's 'buckling' mode is used; the tests check that
both give the same factors. K_G may be indefinite (tension and compression); only positive load factors
(the loads as given, not reversed) are returned.

Natural modes
-------------
K phi = omega^2 M phi with M assembled from group.mass(nodes, lumped) plus point masses on the
translations, solved by eigsh shift-invert (sigma = 0 for a held model, a small negative sigma when the
model can move as a rigid body so the factorisation of K - sigma M exists). The choice of mass matrix
is in `modes` (lumped=False by default; see the report of tests/solvers/test_fe_eigen.py for the
numbers behind it).

Units: N, mm, tonne, so omega^2 is in 1/s^2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from .model import FEModel, Layout, ModelError, Solution, StaticSolver, eliminate

V0_SEED = 0  # start vector of ARPACK: a fixed one so that a run gives the same modes every time


def _assemble(model: FEModel, lay: Layout, blocks: list) -> sp.csr_matrix:
    """Sum of element blocks [(group, (E, nd, nd))] into an n_dof square matrix."""
    rows, cols, vals = [], [], []
    for g, Ke in blocks:
        dofs = lay.element_dofs(g.conn, g.n_comp)
        nd = dofs.shape[1]
        keep = dofs >= 0
        if not np.all(keep):
            Ke = Ke * (keep[:, :, None] & keep[:, None, :])
            dofs = np.where(keep, dofs, 0)
        rows.append(np.repeat(dofs, nd, axis=1).ravel())
        cols.append(np.tile(dofs, (1, nd)).ravel())
        vals.append(Ke.ravel())
    n = lay.n_dof
    if not rows:
        return sp.csr_matrix((n, n))
    A = sp.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)).tocsr()
    return ((A + A.T) * 0.5).tocsr()


def mass_matrix(model: FEModel, lay: Layout | None = None, lumped: bool = False) -> sp.csr_matrix:
    """Global mass (tonne): every group that has mass(nodes, lumped), plus point masses on the translations."""
    lay = lay or model.layout()
    blocks = [(g, g.mass(model.nodes, lumped)) for g in model.groups if hasattr(g, "mass")]
    M = _assemble(model, lay, blocks).tolil()
    for node, m in model.point_masses.items():
        for k in range(3):
            d = lay.dof(node, k)
            M[d, d] = M[d, d] + m
    return M.tocsr()


def geometric_stiffness(sol: Solution, thermal: dict | None = None) -> sp.csr_matrix:
    """K_G of the stresses of a static solution. thermal {group: (alpha, T, ...)} for groups whose
    stresses must exclude a thermal strain. Groups without geometric_stiffness (springs, ...) add nothing."""
    model, lay = sol.model, sol.layout
    blocks = []
    for g in model.groups:
        if not hasattr(g, "geometric_stiffness"):
            continue
        ue = sol.element_displacements(g)
        th = (thermal or {}).get(id(g)) or (thermal or {}).get(g.name)
        blocks.append((g, g.geometric_stiffness(model.nodes, ue, th) if th is not None
                       else g.geometric_stiffness(model.nodes, ue)))
    return _assemble(model, lay, blocks)


def _signed(phi: np.ndarray) -> np.ndarray:
    """Each column with its largest-magnitude component positive."""
    i = np.argmax(np.abs(phi), axis=0)
    s = np.sign(phi[i, np.arange(phi.shape[1])])
    s[s == 0] = 1.0
    return phi * s


@dataclass
class Buckling:
    """factors (k,) ascending load factors; shapes (n_dof, k) with the largest component 1; the static solution."""

    factors: np.ndarray
    shapes: np.ndarray
    static: Solution
    layout: Layout
    info: dict = field(default_factory=dict)

    def node_shape(self, i: int) -> np.ndarray:
        return self.layout.node_array(self.shapes[:, i])


def buckling(model: FEModel, F, n_modes: int = 3, sigma: float | None = None, thermal: dict | None = None,
             solver: StaticSolver | None = None, tol: float = 0.0) -> Buckling:
    """Linear buckling load factors of the reference load F (N, 6) or (n_dof,). sigma: optional shift
    (a load factor) for eigsh's buckling mode; None = the inverted pencil (shift-invert about 0)."""
    st = solver or StaticSolver(model)
    if st.method not in ("eliminate", "penalty"):
        raise ModelError("buckling needs the eliminated (or penalty) system: a positive definite K")
    sol = st.solve(F)
    KG = geometric_stiffness(sol, thermal)
    T = st.T
    Kr = st.A
    KGr = (T.T @ KG @ T).tocsc()
    n = Kr.shape[0]
    k = max(1, min(n_modes, n - 2))
    v0 = np.random.default_rng(V0_SEED).random(n)
    if sigma is None:
        Kinv = spla.LinearOperator((n, n), matvec=st.lu.solve, dtype=float)
        # extra pairs: negative mu (tension-type factors) may come out among the largest magnitudes
        mu, vec = spla.eigsh(-KGr, k=min(n - 2, 2 * k + 2), M=Kr, Minv=Kinv, which="LA", v0=v0, tol=tol)
        pos = mu > 0
        lam = 1.0 / mu[pos]
        vec = vec[:, pos]
        how = "inverted pencil, eigsh mode 2 with the static factorisation of K"
    else:
        lam, vec = spla.eigsh(Kr, k=k, M=(-KGr).tocsc(), sigma=sigma, which="LM", mode="buckling", v0=v0, tol=tol)
        pos = lam > 0
        lam, vec = lam[pos], vec[:, pos]
        how = f"eigsh buckling mode, sigma {sigma:g}"
    order = np.argsort(lam)[:k]
    lam, vec = lam[order], vec[:, order]
    phi = T @ vec
    phi = _signed(phi / np.maximum(np.abs(phi).max(axis=0), 1e-300))
    return Buckling(lam, phi, sol, st.layout, {"method": how, "n_reduced": int(n)})


@dataclass
class Modes:
    """frequencies (k,) Hz ascending; omega2 (k,) 1/s^2; shapes (n_dof, k) mass-normalised (phi^T M phi = 1);
    M the global mass used."""

    frequencies: np.ndarray
    omega2: np.ndarray
    shapes: np.ndarray
    layout: Layout
    M: sp.csr_matrix
    info: dict = field(default_factory=dict)

    def node_shape(self, i: int) -> np.ndarray:
        return self.layout.node_array(self.shapes[:, i])

    def effective_mass(self) -> np.ndarray:
        """Modal effective mass (k, 3) of the translations x, y, z (tonne), (phi^T M r)^2 for unit r."""
        lay = self.layout
        r = np.zeros((lay.n_dof, 3))
        used = lay.counts > 0
        for j in range(3):
            r[lay.offsets[used] + j, j] = 1.0
        return (self.shapes.T @ (self.M @ r)) ** 2


def modes(model: FEModel, n_modes: int = 6, lumped: bool = False, sigma: float | None = None,
          f_shift_hz: float = 1.0, tol: float = 0.0) -> Modes:
    """Natural frequencies and mode shapes. Supports and constraints of the model are applied by
    elimination. sigma: shift of omega^2 (1/s^2); default 0 for a held model and -(2 pi f_shift_hz)^2 for a
    model that can move as a rigid body (its rigid modes come out near 0 Hz)."""
    from .precheck import rigid_body_check

    lay = model.layout()
    K = model.stiffness(lay)
    M = mass_matrix(model, lay, lumped)
    fixed = model.fixed_dofs(lay)
    T, _, free, info = eliminate(lay.n_dof, fixed, model.equations(lay.counts), lay)
    Kr = (T.T @ K @ T).tocsc()
    Mr = (T.T @ M @ T).tocsc()
    if sigma is None:
        rb = rigid_body_check(model, lay, fixed)
        sigma = 0.0 if rb["free_rigid_modes"] == 0 else -(2 * np.pi * f_shift_hz) ** 2
    n = Kr.shape[0]
    k = max(1, min(n_modes, n - 2))
    v0 = np.random.default_rng(V0_SEED).random(n)
    try:
        w2, vec = spla.eigsh(Kr, k=k, M=Mr, sigma=sigma, which="LM", v0=v0, tol=tol)
    except RuntimeError as e:
        raise ModelError(f"the eigenproblem could not be factorised at sigma {sigma:g} ({e})") from None
    order = np.argsort(w2)
    w2, vec = w2[order], vec[:, order]
    phi = T @ vec
    nrm = np.sqrt(np.einsum("ik,ik->k", phi, M @ phi))
    phi = _signed(phi / nrm)
    f = np.sqrt(np.maximum(w2, 0.0)) / (2 * np.pi)
    return Modes(f, w2, phi, lay, M, {**info, "sigma": float(sigma), "lumped": bool(lumped), "n_reduced": int(n)})


def mac(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Modal assurance criterion |a_i . b_j|^2 / ((a_i . a_i)(b_j . b_j)) of the columns of a and b."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    num = (a.T @ b) ** 2
    return num / np.outer(np.einsum("ik,ik->k", a, a), np.einsum("ik,ik->k", b, b))


__all__ = ["Buckling", "Modes", "buckling", "geometric_stiffness", "mac", "mass_matrix", "modes"]

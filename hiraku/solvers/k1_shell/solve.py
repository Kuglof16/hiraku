"""Assembly and linear static solution of a shell model (scipy.sparse).

DOF numbering: 6 per node, dof = 6 * node + k with k = 0..5 for
(ux, uy, uz, rx, ry, rz) in global axes. Units: N, mm, MPa, tonne.

Constraints:
- fixed DOFs (value zero),
- rigid links (multi-point constraints, RBE2 type): a slave node follows a
  master node rigidly, u_s = u_m + theta_m x (x_s - x_m), theta_s = theta_m.
  Chains of links are resolved to the last master.

Both are eliminated with a reduction matrix T (u = T u_r), the reduced
matrix T^T K T is factorised once and every load case reuses the factors.

The factorisation is SuperLU (scipy) or, when the package pypardiso (Intel MKL PARDISO) can be
imported, PARDISO: HIRAKU_SOLVER=superlu|pardiso chooses, the default is pardiso when it is
installed (solver_choice). The known answers and the byte-for-byte record comparisons are made
with superlu. PARDISO_REL_TOL is how far the two may differ.
"""

from __future__ import annotations

import contextlib
import contextvars
import importlib
import os
import time
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp

from ..sparse_lu import superlu
from . import element

DOF_NAMES = ("ux", "uy", "uz", "rx", "ry", "rz")


@dataclass
class ShellModel:
    """Nodes, quadrilateral elements and their wall properties.

    nodes (N, 3) mm; elements (E, 4) node indices; per element thickness
    (mm), E (MPa), nu and rho (t/mm^3). Extra nodes without elements (load or
    reference points tied by rigid links) can be added with add_node.
    """

    nodes: np.ndarray
    elements: np.ndarray
    thickness: np.ndarray
    E: np.ndarray
    nu: np.ndarray
    rho: np.ndarray
    links: list[tuple[int, int]] = field(default_factory=list)  # (slave, master)
    drill_factor: float = element.DRILL_FACTOR
    incompatible: bool = True  # False only to show membrane locking
    timings: dict = field(default_factory=dict)
    _K: sp.csr_matrix | None = None

    @classmethod
    def from_mesh(cls, mesh, design: dict, drill_factor: float = element.DRILL_FACTOR) -> "ShellModel":
        """Model of a k1_shell.mesh.ShellMesh with the materials of the design's members (a gusset part
        takes the material of the member named in mesh.part_material)."""
        mat_of = {m["id"]: design["materials"][m["material"]] for m in design["members"]}
        for part, member in getattr(mesh, "part_material", {}).items():
            mat_of[part] = mat_of[member]
        mats = [mat_of[m] for m in mesh.element_member]
        E = np.array([float(m["E_MPa"]) for m in mats])
        nu = np.array([float(m["nu"]) for m in mats])
        rho = np.array([float(m["density_kg_m3"]) * 1e-12 for m in mats])
        t = np.array([mesh.member_thickness_mm[m] for m in mesh.element_member], float)
        return cls(np.array(mesh.nodes, float), np.array(mesh.elements, int), t, E, nu, rho,
                   list(mesh.links), drill_factor)

    @property
    def n_nodes(self) -> int:
        return len(self.nodes)

    @property
    def n_dof(self) -> int:
        return 6 * len(self.nodes)

    def add_node(self, xyz) -> int:
        """Add a node without elements (e.g. a load point for rigid links); returns its index."""
        self.nodes = np.vstack([self.nodes, np.asarray(xyz, float)[None, :]])
        self._K = None
        return len(self.nodes) - 1

    def element_xyz(self) -> np.ndarray:
        return self.nodes[self.elements]

    def element_dofs(self) -> np.ndarray:
        return (6 * self.elements[:, :, None] + np.arange(6)[None, None, :]).reshape(len(self.elements), 24)

    def stiffness(self) -> sp.csr_matrix:
        """Global stiffness matrix (cached)."""
        if self._K is not None and self._K.shape[0] == self.n_dof:
            return self._K
        t0 = time.perf_counter()
        Ke = element.global_stiffness_blocks(self.element_xyz(), self.thickness, self.E, self.nu, self.drill_factor,
                                             self.incompatible)
        t1 = time.perf_counter()
        dofs = self.element_dofs().astype(np.int64)
        rows = np.repeat(dofs, 24, axis=1).ravel()
        cols = np.tile(dofs, (1, 24)).ravel()
        K = sp.coo_matrix((Ke.ravel(), (rows, cols)), shape=(self.n_dof, self.n_dof)).tocsr()
        K = (K + K.T) * 0.5
        self._K = K.tocsr()
        self.timings["element_matrices_s"] = t1 - t0
        self.timings["assemble_s"] = time.perf_counter() - t0
        return self._K

    def mass_diagonal(self) -> np.ndarray:
        """Lumped mass on every DOF (n_dof,), tonne and tonne*mm^2."""
        me = element.lumped_mass(self.element_xyz(), self.thickness, self.rho)
        return np.bincount(self.element_dofs().ravel(), weights=me.ravel(), minlength=self.n_dof)

    def mass(self) -> sp.csr_matrix:
        return sp.diags(self.mass_diagonal()).tocsr()

    def total_mass(self) -> float:
        """rho * t * area summed over the elements (tonne)."""
        _, xl, _, _ = element.local_frames(self.element_xyz())
        return float(np.sum(self.rho * self.thickness * element.element_area(xl)))


def dofs_of(nodes, comps: str = "all") -> np.ndarray:
    """DOF indices of nodes for components: 'all', 'xyz' (translations), 'r' (rotations) or a subset like 'xz'."""
    nodes = np.atleast_1d(np.asarray(nodes, int))
    if comps == "all":
        ks = range(6)
    elif comps == "r":
        ks = (3, 4, 5)
    else:
        ks = ["xyz".index(c) for c in comps]
    return (6 * nodes[:, None] + np.array(list(ks))[None, :]).ravel()


def _skew_offset(r: np.ndarray) -> np.ndarray:
    """S with theta x r = S theta."""
    x, y, z = r
    return np.array([[0.0, z, -y], [-z, 0.0, x], [y, -x, 0.0]])


def reduction_matrix(nodes: np.ndarray, links, fixed: np.ndarray) -> tuple[sp.csr_matrix, np.ndarray]:
    """T (n_dof x n_free) with u = T u_r, and the global DOF index of each reduced DOF."""
    n = len(nodes)
    master = np.arange(n)
    for s, m in links:
        if s == m:
            continue
        if master[s] != s:
            raise ValueError(f"node {s} is the slave of two rigid links")
        master[s] = m

    def root(i):
        seen = set()
        while master[i] != i:
            if i in seen:
                raise ValueError("rigid links form a loop")
            seen.add(i)
            i = master[i]
        return i

    final = np.array([root(i) for i in range(n)])
    fixed = np.unique(np.asarray(fixed, int))
    is_fixed = np.zeros(6 * n, bool)
    is_fixed[fixed] = True
    slaves = np.nonzero(final != np.arange(n))[0]
    indep = ~is_fixed
    if len(slaves):
        if np.any(is_fixed[dofs_of(slaves)]):
            raise ValueError("a fixed DOF belongs to a slave node of a rigid link; fix its master instead")
        indep[dofs_of(slaves)] = False
    free = np.nonzero(indep)[0]
    col = -np.ones(6 * n, int)
    col[free] = np.arange(len(free))

    rows, cols, vals = [free], [col[free]], [np.ones(len(free))]
    for s in slaves:
        m = final[s]
        S = _skew_offset(nodes[s] - nodes[m])
        for i in range(3):
            # translation: u_s,i = u_m,i + sum_j S_ij theta_m,j
            terms = [(6 * m + i, 1.0)] + [(6 * m + 3 + j, S[i, j]) for j in range(3) if S[i, j] != 0.0]
            for d, v in terms:
                if col[d] >= 0:
                    rows.append(np.array([6 * s + i])); cols.append(np.array([col[d]])); vals.append(np.array([v]))
            d = 6 * m + 3 + i
            if col[d] >= 0:
                rows.append(np.array([6 * s + 3 + i])); cols.append(np.array([col[d]])); vals.append(np.array([1.0]))
    T = sp.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                      shape=(6 * n, len(free))).tocsr()
    return T, free


SOLVERS = ("superlu", "pardiso")
# How far a PARDISO solve may differ from the SuperLU one, relative to the largest absolute value of
# the same quantity in the model (displacements, stresses): both are direct factorisations in double
# precision of the same matrix, and their round-off on the demo frame's matrices (free DOF to 3e5) is
# well below this. An assumption, checked on small models in tests/solvers/test_shell_pardiso.py.
PARDISO_REL_TOL = 1e-6
# Largest relative residual |Kr ur - r| / |r| of a solve accepted: a direct solve of a well-held model
# leaves round-off (about 1e-12 on the demo frame); a model that is barely held (a near mechanism)
# can give large finite displacements that do not satisfy the equations. The same bound as the
# solid FE core's (an assumption).
RESIDUAL_TOL = 1e-6


def pardiso_module():
    """The pypardiso module, or None when it cannot be imported."""
    try:
        return importlib.import_module("pypardiso")
    except Exception:  # not installed, or MKL cannot be loaded on this machine
        return None


_FORCED: contextvars.ContextVar[str | None] = contextvars.ContextVar("hiraku_shell_solver", default=None)


@contextlib.contextmanager
def using(name: str):
    """Solve with `name` ('superlu' or 'pardiso') inside the with block, whatever HIRAKU_SOLVER says
    (the known answers use superlu)."""
    if name not in SOLVERS:
        raise ValueError(f"solver must be one of {SOLVERS}, got {name!r}")
    token = _FORCED.set(name)
    try:
        yield
    finally:
        _FORCED.reset(token)


def solver_choice() -> str:
    """'superlu' or 'pardiso': the one of an enclosing using(), else HIRAKU_SOLVER when it names one
    (pardiso only when it can be imported), else pardiso when it can be imported, else superlu."""
    if _FORCED.get() is not None:
        return _FORCED.get()
    asked = os.environ.get("HIRAKU_SOLVER", "").strip().lower()
    if asked == "superlu":
        return "superlu"
    return "pardiso" if pardiso_module() is not None else "superlu"


def solver_assumption(name: str) -> str:
    """The analysis assumption that says which factorisation solved the shell."""
    if name == "pardiso":
        return ("linear solver: Intel MKL PARDISO through pypardiso (real nonsymmetric storage of the "
                "symmetric reduced stiffness); the results may differ from the SuperLU solve by up to "
                f"{PARDISO_REL_TOL:g} of each quantity's largest absolute value in the model (round-off of "
                "two direct solvers; an assumption)")
    if name == "superlu":
        return ("linear solver: SuperLU (scipy.sparse.linalg.spilu with no dropping, the complete LU; "
                "MMD_AT_PLUS_A ordering, symmetric mode, no pivoting threshold)")
    return f"linear solver: {name}"


class _Pardiso:
    """The factors of PARDISO for one matrix, with the solve() of a SuperLU object."""

    def __init__(self, A: sp.csc_matrix, module) -> None:
        self.A = sp.csr_matrix(A)
        self.ps = module.PyPardisoSolver()  # mtype 11, real nonsymmetric: the full matrix, no triangle to pass
        self.ps.factorize(self.A)

    def solve(self, b: np.ndarray) -> np.ndarray:
        b = np.asarray(b, float)
        if b.ndim == 1:
            return np.asarray(self.ps.solve(self.A, b), float)
        return np.column_stack([np.asarray(self.ps.solve(self.A, np.ascontiguousarray(b[:, j])), float)
                                for j in range(b.shape[1])])


def relative_residual(A, x: np.ndarray, b: np.ndarray) -> float:
    """Largest |A x - b| / |b| over the columns of b (columns with b = 0: |A x|, which must be 0 too)."""
    r = A @ x - b
    if b.ndim == 1:
        r, b = r[:, None], b[:, None]
    nb = np.linalg.norm(b, axis=0)
    nr = np.linalg.norm(r, axis=0)
    return float(np.max(np.where(nb > 0, nr / np.where(nb > 0, nb, 1.0), nr))) if nr.size else 0.0


class StaticSolver:
    """Factorise the constrained stiffness once; solve any number of load cases.

    fixed: global DOF indices held at zero. Rigid links are taken from
    model.links. Raises ValueError when the constrained model is singular
    (a mechanism: missing supports or disconnected parts). solver: 'superlu'
    or 'pardiso' (None: solver_choice()); the one used is self.solver_name.
    """

    def __init__(self, model: ShellModel, fixed, solver: str | None = None) -> None:
        self.model = model
        name = solver or solver_choice()
        if name not in SOLVERS:
            raise ValueError(f"solver must be one of {SOLVERS}, got {name!r}")
        mod = pardiso_module() if name == "pardiso" else None
        if name == "pardiso" and mod is None:
            raise ValueError("solver 'pardiso' needs the package pypardiso, which cannot be imported")
        K = model.stiffness()
        t0 = time.perf_counter()
        self.T, self.free = reduction_matrix(model.nodes, model.links, np.asarray(fixed, int))
        Kr = (self.T.T @ K @ self.T).tocsc()
        t1 = time.perf_counter()
        try:
            if mod is not None:
                self.lu = _Pardiso(Kr, mod)
            else:
                self.lu = superlu(Kr)
        except (RuntimeError, ValueError, ArithmeticError) as e:
            raise ValueError(f"the shell model cannot be solved ({e}): it is not held against all rigid-body "
                             f"motions or a part is not connected") from None
        except Exception as e:  # pypardiso's own error class
            if mod is None:
                raise
            raise ValueError(f"the shell model cannot be solved ({e}): it is not held against all rigid-body "
                             f"motions or a part is not connected") from None
        self.solver_name = name
        self.Kr = Kr
        model.timings["reduce_s"] = t1 - t0
        model.timings["factor_s"] = time.perf_counter() - t1
        model.timings["n_dof_free"] = int(Kr.shape[0])

    def solve(self, F: np.ndarray) -> np.ndarray:
        """Displacements (n_dof,) or (n_dof, n_cases) for loads of the same shape."""
        t0 = time.perf_counter()
        F = np.asarray(F, float)
        rhs = np.asarray(self.T.T @ F)
        ur = self.lu.solve(rhs)
        if not np.all(np.isfinite(ur)):
            raise ValueError("the shell solution is not finite; the model is singular or ill-conditioned")
        worst = relative_residual(self.Kr, ur, rhs)
        if worst > RESIDUAL_TOL:
            raise ValueError(f"the shell solution does not satisfy the equations (relative residual {worst:.1e}, "
                             f"more than {RESIDUAL_TOL:g}): the model is nearly singular, held by too little "
                             f"or a part is barely connected")
        self.model.timings["residual"] = worst
        U = self.T @ ur
        self.model.timings["solve_s"] = time.perf_counter() - t0
        return U

    def reactions(self, U: np.ndarray, F: np.ndarray) -> np.ndarray:
        """K u - F on every DOF (non-zero at fixed DOFs and at rigid-link nodes)."""
        return self.model.stiffness() @ U - F


def spread_load(nodes_xyz: np.ndarray, node_ids, force=(0.0, 0.0, 0.0), moment=(0.0, 0.0, 0.0),
                about=None, n_dof: int | None = None, weights=None) -> np.ndarray:
    """Nodal forces equivalent to a force and moment at `about` (RBE3 type, translations only).

    The force is shared by the weights (equal by default); the moment, plus
    the moment of the force's offset from the weighted centroid, is carried
    as forces w_i * (M x r_i) / sum(w_i |r_i|^2) with r_i taken from the
    weighted centroid, projected perpendicular to M. The result has exactly
    the given resultant force and moment about `about`.
    """
    ids = np.asarray(node_ids, int)
    x = nodes_xyz[ids]
    w = np.ones(len(ids)) if weights is None else np.asarray(weights, float)
    w = w / w.sum()
    c = (w[:, None] * x).sum(axis=0)
    F = np.asarray(force, float)
    M = np.asarray(moment, float) + (np.cross(np.asarray(about, float) - c, F) if about is not None else 0.0)
    f = w[:, None] * F[None, :]
    if np.linalg.norm(M) > 0:
        r = x - c
        # least-squares: forces f_i = w_i (A^-1 M) x r_i with A = sum w_i (|r|^2 I - r r^T)
        A = np.einsum("i,ij,ik->jk", w, r, r)
        A = np.trace(A) * np.eye(3) - A
        a = np.linalg.lstsq(A, M, rcond=None)[0]
        f += w[:, None] * np.cross(a[None, :], r)
    out = np.zeros(n_dof if n_dof is not None else 6 * len(nodes_xyz))
    for k in range(3):
        np.add.at(out, 6 * ids + k, f[:, k])
    return out

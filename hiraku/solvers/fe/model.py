"""The common finite-element core: nodes with 3 or 6 DOFs, element groups, constraints, assembly, solve.

Degrees of freedom
------------------
Each node carries 0, 3 or 6 DOFs in global axes, in the order
(ux, uy, uz, rx, ry, rz). A node gets 6 when any element group or
constraint that touches it needs rotations (shells, springs with rotational
stiffness, the master of a rigid link, the reference of a distributing
link), 3 when only solids touch it, and 0 when nothing does. Global DOF
numbers are node-major: dof(node, k) = offset[node] + k.

Element groups
--------------
An element group is many elements of one kind, evaluated together with
numpy. It gives:
  conn       (E, n) node indices
  n_comp     3 or 6: the DOFs it uses at every one of its nodes
  stiffness(nodes) -> (E, n * n_comp, n * n_comp), element vector node-major
  mass_total(nodes) -> float, tonne (optional, 0 when the group has no mass)
New element kinds are added by writing a class with these members
(see ElementGroup).

Constraints
-----------
Every constraint is a set of linear equations sum_k c_k u_k = g on node
DOFs (Equation). Fixed DOFs are kept apart (a value per DOF). Rigid links,
distributing links, ties and shell-to-solid couplings in connect.py only
make equations.

How constraints are applied (chosen by comparing the three ways, see
tests/solvers/test_fe_core.py::ConstraintMethodTest and the report):
- "eliminate" (the default): master-slave elimination, u = T u_r + g.
  Each equation, after substituting the DOFs already eliminated, picks one
  DOF to become dependent (the equation's preferred one when its
  coefficient is not small, otherwise the largest coefficient), and that DOF
  is substituted out of the earlier dependent expressions. The reduced
  matrix T^T K T stays symmetric positive definite, has fewer unknowns and
  satisfies the constraints exactly, with no parameter to tune. Reference:
  Cook, Malkus, Plesha & Witt, Concepts and Applications of Finite Element
  Analysis, 4th ed. (2002), section 9.2 (constraints by transformation) -
  section number not checked here.
- "penalty": K + alpha C^T C (alpha = penalty_scale * the largest diagonal
  of K, default 1e2). Constraints are met only to about 1 / penalty_scale and a
  large alpha worsens the condition number (Cook et al., the same chapter).
  Measured on a shell plate with a rigid link over 22 nodes: condition number
  1.0e8 eliminated, 9e13 with penalty_scale 1e2, and from 1e4 up the solve
  loses all accuracy (refused by the residual check).
- "lagrange": the bordered system [[K, C^T], [C, 0]], exact but indefinite
  (needs pivoting) and larger (Bathe, Finite Element Procedures, 1996,
  section 3.4 - not checked here).

Units: N, mm, MPa, tonne (density t/mm^3), as in k1_shell.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from ..sparse_lu import superlu

DOF_NAMES = ("ux", "uy", "uz", "rx", "ry", "rz")


class ModelError(ValueError):
    """The model cannot be solved as given (singular, inconsistent constraints, bad DOF use)."""


# ------------------------------------------------------------------ element groups


class ElementGroup:
    """Base class of an element group. Subclasses set conn, n_comp, name and define stiffness()."""

    name: str = "elements"
    kind: str = "element"
    conn: np.ndarray
    n_comp: int = 3

    def stiffness(self, nodes: np.ndarray) -> np.ndarray:  # pragma: no cover - interface
        raise NotImplementedError

    def mass_total(self, nodes: np.ndarray) -> float:
        return 0.0

    @property
    def n_elements(self) -> int:
        return len(self.conn)


# ------------------------------------------------------------------ constraints


@dataclass
class Equation:
    """sum over terms of coef * u(node, comp) = rhs. `prefer` (node, comp) is the DOF to make dependent."""

    terms: list  # [(node, comp, coef)]
    rhs: float = 0.0
    prefer: tuple | None = None
    source: str = ""


@dataclass
class Constraint:
    """A named group of equations and the nodes that must carry rotations for it.

    `maker` (optional) makes the equations once the DOF counts are known: maker(counts) -> [Equation]
    (e.g. a rigid link ties a slave's rotations only when the slave has them). Such a constraint lists
    every node it touches in `nodes` and the ones that need rotations in `needs_rotation`.
    """

    name: str
    equations: list = field(default_factory=list)
    needs_rotation: tuple = ()
    nodes: tuple = ()  # every node the constraint touches (for connectivity checks)
    maker: object = None

    def make(self, counts: np.ndarray) -> list:
        return list(self.equations) + (list(self.maker(counts)) if self.maker is not None else [])


# ------------------------------------------------------------------ the model


@dataclass
class FEModel:
    """Nodes (N, 3) mm, element groups, constraints, fixed DOFs and point masses."""

    nodes: np.ndarray
    groups: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    fixed: dict = field(default_factory=dict)  # (node, comp) -> value
    point_masses: dict = field(default_factory=dict)  # node -> tonne
    rotation_nodes: set = field(default_factory=set)  # nodes forced to carry 6 DOFs
    fix_requests: dict = field(default_factory=dict)  # node -> [(comps, value)] from fix()
    timings: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.nodes = np.asarray(self.nodes, float).reshape(-1, 3)

    # --- building

    def add_nodes(self, xyz) -> np.ndarray:
        xyz = np.asarray(xyz, float).reshape(-1, 3)
        start = len(self.nodes)
        self.nodes = np.vstack([self.nodes, xyz])
        return np.arange(start, start + len(xyz))

    def add_node(self, xyz) -> int:
        return int(self.add_nodes(xyz)[0])

    def add(self, item):
        """Add an element group or a constraint; returns it."""
        if isinstance(item, Constraint):
            self.constraints.append(item)
        else:
            self.groups.append(item)
        return item

    def fix(self, nodes, comps="all", value: float = 0.0) -> None:
        """Hold DOFs of nodes. comps: 'all' (every DOF the node has), 'xyz', 'r', or a list of indices.
        Several calls on one node add up."""
        for n in np.atleast_1d(np.asarray(nodes, int)):
            self.fix_requests.setdefault(int(n), []).append((comps, float(value)))

    def prescribe(self, node: int, comp: int, value: float) -> None:
        self.fixed[(int(node), int(comp))] = float(value)

    # --- DOF layout

    def dof_counts(self) -> np.ndarray:
        n = len(self.nodes)
        cnt = np.zeros(n, int)
        for g in self.groups:
            idx = np.unique(g.conn[g.conn >= 0])
            cnt[idx] = np.maximum(cnt[idx], g.n_comp)
        for c in self.constraints:
            for node, comp, _ in (t for eq in c.equations for t in eq.terms):
                cnt[node] = max(cnt[node], 6 if comp >= 3 else 3)
            for node in c.needs_rotation:
                cnt[node] = 6
            for node in c.nodes:
                cnt[node] = max(cnt[node], 3)
        for node in self.rotation_nodes:
            cnt[node] = 6
        return cnt

    def layout(self) -> "Layout":
        return Layout(self.dof_counts())

    # --- assembly

    def _stiffness_key(self, lay: "Layout") -> tuple:
        return (tuple((id(g), g.n_elements) for g in self.groups), self.nodes.shape, lay.counts.tobytes(),
                hash(self.nodes.tobytes()))

    def stiffness(self, layout: "Layout | None" = None) -> sp.csr_matrix:
        """Global stiffness (cached until the groups, nodes or DOF layout change)."""
        lay = layout or self.layout()
        key = self._stiffness_key(lay)
        cache = getattr(self, "_k_cache", None)
        if cache is not None and cache[0] == key:
            return cache[1]
        rows, cols, vals = [], [], []
        t0 = time.perf_counter()
        for g in self.groups:
            Ke = g.stiffness(self.nodes)
            dofs = lay.element_dofs(g.conn, g.n_comp)
            nd = dofs.shape[1]
            keep = (dofs >= 0)
            if not np.all(keep):
                Ke = Ke * (keep[:, :, None] & keep[:, None, :])
                dofs = np.where(keep, dofs, 0)
            rows.append(np.repeat(dofs, nd, axis=1).ravel())
            cols.append(np.tile(dofs, (1, nd)).ravel())
            vals.append(Ke.ravel())
        n = lay.n_dof
        if rows:
            K = sp.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(n, n)).tocsr()
        else:
            K = sp.csr_matrix((n, n))
        K = ((K + K.T) * 0.5).tocsr()
        self.timings["assemble_s"] = time.perf_counter() - t0
        self._k_cache = (key, K)
        return K

    def total_mass(self) -> float:
        """Mass of the model (tonne): element groups plus point masses."""
        return float(sum(g.mass_total(self.nodes) for g in self.groups) + sum(self.point_masses.values()))

    def fixed_dofs(self, lay: "Layout") -> dict:
        """Global DOF -> prescribed value, from fix() requests and prescribe()."""
        out = {}
        for n, reqs in self.fix_requests.items():
            nc = lay.counts[n]
            for comps, v in reqs:
                if comps == "all":
                    ks = range(nc)
                elif comps == "xyz":
                    ks = range(min(3, nc))
                elif comps == "r":
                    ks = range(3, nc)
                elif isinstance(comps, str):
                    ks = ["xyz".index(c) for c in comps]
                else:
                    ks = [int(k) for k in comps]
                for k in ks:
                    out[lay.dof(n, k)] = v
        for (n, k), v in self.fixed.items():
            out[lay.dof(n, k)] = v
        return out

    def equations(self, counts: np.ndarray | None = None) -> list:
        counts = self.dof_counts() if counts is None else counts
        return [eq for c in self.constraints for eq in c.make(counts)]

    def constraint_matrix(self, lay: "Layout") -> tuple[sp.csr_matrix, np.ndarray]:
        """C (m x n_dof) and g (m,) of every constraint equation (fixed DOFs not included)."""
        eqs = self.equations(lay.counts)
        r, c, v = [], [], []
        for i, eq in enumerate(eqs):
            for node, comp, coef in eq.terms:
                r.append(i)
                c.append(lay.dof(node, comp))
                v.append(coef)
        C = sp.coo_matrix((v, (r, c)), shape=(len(eqs), lay.n_dof)).tocsr()
        return C, np.array([eq.rhs for eq in eqs], float)

    def connectivity_groups(self) -> np.ndarray:
        """Label (N,) of the connected group of every node (elements and constraints join nodes); -1 for unused."""
        n = len(self.nodes)
        parent = np.arange(n)

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def join(ids):
            ids = [int(i) for i in ids if i >= 0]
            if not ids:
                return
            r0 = find(ids[0])
            for i in ids[1:]:
                ri = find(i)
                if ri != r0:
                    parent[ri] = r0

        for g in self.groups:
            for row in g.conn:
                join(row)
        for c in self.constraints:
            for eq in c.equations:
                join([t[0] for t in eq.terms])
            join(list(c.nodes))  # lazy constraints list their nodes here
        used = self.dof_counts() > 0
        roots = np.array([find(i) for i in range(n)])
        labels = -np.ones(n, int)
        _, inv = np.unique(roots[used], return_inverse=True)
        labels[used] = inv
        return labels

    def rigid_modes(self, lay: "Layout | None" = None) -> np.ndarray:
        """The six rigid-body motions of the whole model (n_dof, 6): translations x, y, z then rotations about
        the axes through the centroid of the used nodes."""
        lay = lay or self.layout()
        used = lay.counts > 0
        c = self.nodes[used].mean(axis=0) if np.any(used) else np.zeros(3)
        Phi = np.zeros((lay.n_dof, 6))
        for k in range(3):
            Phi[lay.offsets[used] + k, k] = 1.0
        r = self.nodes - c
        for j in range(3):
            w = np.zeros(3)
            w[j] = 1.0
            u = np.cross(w[None, :], r)  # theta x r
            for k in range(3):
                Phi[lay.offsets[used] + k, 3 + j] = u[used, k]
            six = lay.counts == 6
            Phi[lay.offsets[six] + 3 + j, 3 + j] = 1.0
        return Phi


class Layout:
    """DOF numbering of a model: counts (N,) and offsets (N,) of each node."""

    def __init__(self, counts: np.ndarray) -> None:
        self.counts = np.asarray(counts, int)
        self.offsets = np.concatenate([[0], np.cumsum(self.counts)[:-1]]).astype(np.int64)
        self.n_dof = int(self.counts.sum())

    def dof(self, node: int, comp: int) -> int:
        if comp >= self.counts[node]:
            raise ModelError(f"node {node} has {self.counts[node]} DOFs; component {DOF_NAMES[comp]} does not exist "
                             f"(a solid node has no rotations)")
        return int(self.offsets[node] + comp)

    def element_dofs(self, conn: np.ndarray, n_comp: int) -> np.ndarray:
        conn = np.asarray(conn, int)
        if np.any(self.counts[conn[conn >= 0]] < n_comp):
            raise ModelError("an element uses more DOFs at a node than the node has")
        d = self.offsets[np.where(conn >= 0, conn, 0)][:, :, None] + np.arange(n_comp)[None, None, :]
        d = np.where((conn >= 0)[:, :, None], d, -1)
        return d.reshape(len(conn), -1)

    def node_array(self, U: np.ndarray) -> np.ndarray:
        """Per-node values (N, 6) from a DOF vector (missing components are 0)."""
        out = np.zeros((len(self.counts), 6))
        for k in range(6):
            has = self.counts > k
            out[has, k] = U[self.offsets[has] + k]
        return out

    def from_node_array(self, A: np.ndarray) -> np.ndarray:
        """DOF vector from per-node values (N, 6); components a node lacks must be 0."""
        A = np.asarray(A, float)
        out = np.zeros(self.n_dof)
        for k in range(6):
            has = self.counts > k
            out[self.offsets[has] + k] = A[has, k]
            lost = (~has) & (A[:, k] != 0.0)
            if np.any(lost):
                raise ModelError(f"a load or value on {DOF_NAMES[k]} at node {int(np.nonzero(lost)[0][0])}, "
                                 f"which has no such DOF")
        return out


# ------------------------------------------------------------------ elimination


def eliminate(n_dof: int, fixed: dict, equations: list, lay: Layout, tol: float = 1e-12):
    """Master-slave elimination. Returns T (n_dof x n_r) csr, g (n_dof,), free (n_r,) global DOFs, info dict.

    u = T u_r + g satisfies every fixed value and every equation.
    info["G"] (n_dof x n_fixed, columns in info["fixed_dofs"] order) is du/dc of every fixed value c: a
    dependent DOF's constant is tracked as a combination of the fixed values, so the reaction at a fixed DOF
    that only constraints touch (e.g. the master of a rigid link) is G^T (K u - F) (virtual work: the
    reduced residual T^T (K u - F) is zero, so only the fixed values' own motion does work).
    """
    dep: dict[int, dict] = {}  # dependent DOF -> {independent DOF: coef}
    dep_c: dict[int, float] = {}  # dependent DOF -> constant
    dep_f: dict[int, dict] = {}  # dependent DOF -> {fixed DOF: coef} (the constant's dependence on fixed values)
    users: dict[int, set] = {}  # independent DOF -> dependents whose expression uses it
    redundant = 0
    for eq in equations:
        row: dict[int, float] = {}
        rhs = float(eq.rhs)
        rf: dict[int, float] = {}
        scale = max(abs(t[2]) for t in eq.terms) if eq.terms else 1.0
        for node, comp, coef in eq.terms:
            d = lay.dof(node, comp)
            if d in fixed:
                rhs -= coef * fixed[d]
                rf[d] = rf.get(d, 0.0) - coef
            elif d in dep:
                rhs -= coef * dep_c[d]
                for k, v in dep_f[d].items():
                    rf[k] = rf.get(k, 0.0) - coef * v
                for k, v in dep[d].items():
                    row[k] = row.get(k, 0.0) + coef * v
            else:
                row[d] = row.get(d, 0.0) + coef
        row = {k: v for k, v in row.items() if abs(v) > tol * scale}
        if not row:
            if abs(rhs) > 1e-9 * max(scale, 1.0):
                raise ModelError(f"constraint equations contradict each other ({eq.source or 'equation'})")
            redundant += 1
            continue
        big = max(abs(v) for v in row.values())
        pick = None
        if eq.prefer is not None:
            pd = lay.dof(*eq.prefer)
            if pd in row and abs(row[pd]) >= 0.1 * big:
                pick = pd
        if pick is None:
            pick = max(row, key=lambda k: abs(row[k]))
        cp = row.pop(pick)
        expr = {k: -v / cp for k, v in row.items()}
        const = rhs / cp
        constf = {k: v / cp for k, v in rf.items() if v != 0.0}
        # substitute the new dependent DOF out of earlier expressions
        for other in list(users.get(pick, ())):
            e = dep[other]
            a = e.pop(pick, None)
            if a is None:
                continue
            dep_c[other] += a * const
            if constf:
                of = dep_f[other]
                for k, v in constf.items():
                    of[k] = of.get(k, 0.0) + a * v
            for k, v in expr.items():
                nv = e.get(k, 0.0) + a * v
                if abs(nv) > 0.0:
                    e[k] = nv
                    users.setdefault(k, set()).add(other)
                else:
                    e.pop(k, None)
                    users.get(k, set()).discard(other)
        users.pop(pick, None)
        dep[pick] = expr
        dep_c[pick] = const
        dep_f[pick] = constf
        for k in expr:
            users.setdefault(k, set()).add(pick)
    is_indep = np.ones(n_dof, bool)
    if fixed:
        is_indep[list(fixed)] = False
    if dep:
        is_indep[list(dep)] = False
    free = np.nonzero(is_indep)[0]
    col = -np.ones(n_dof, np.int64)
    col[free] = np.arange(len(free))
    rows, cols, vals = [free], [col[free]], [np.ones(len(free))]
    g = np.zeros(n_dof)
    for d, v in fixed.items():
        g[d] = v
    for d, e in dep.items():
        g[d] = dep_c[d]
        if e:
            ks = np.fromiter(e.keys(), np.int64, len(e))
            rows.append(np.full(len(e), d))
            cols.append(col[ks])
            vals.append(np.fromiter(e.values(), float, len(e)))
    T = sp.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                      shape=(n_dof, len(free))).tocsr()
    fixed_dofs = np.fromiter(fixed.keys(), np.int64, len(fixed))
    fcol = {int(d): i for i, d in enumerate(fixed_dofs)}
    gr, gc, gv = list(fixed_dofs), list(range(len(fixed_dofs))), [1.0] * len(fixed_dofs)
    for d, ef in dep_f.items():
        for k, v in ef.items():
            if v != 0.0:
                gr.append(d)
                gc.append(fcol[k])
                gv.append(v)
    G = sp.coo_matrix((gv, (gr, gc)), shape=(n_dof, len(fixed_dofs))).tocsr()
    return T, g, free, {"n_dependent": len(dep), "n_fixed": len(fixed), "redundant_equations": redundant,
                        "G": G, "fixed_dofs": fixed_dofs}


# ------------------------------------------------------------------ solving


@dataclass
class Solution:
    """A static solution: U (n_dof,) and the bookkeeping to read it back."""

    model: FEModel
    layout: Layout
    U: np.ndarray
    F: np.ndarray
    K: sp.csr_matrix
    fixed: dict
    method: str
    info: dict = field(default_factory=dict)
    G: sp.csr_matrix | None = None  # du/dc of the fixed values (columns in the order of `fixed`), from eliminate

    @property
    def displacements(self) -> np.ndarray:
        """(N, 6) per node; rotations of 3-DOF nodes are 0."""
        return self.layout.node_array(self.U)

    def residual_forces(self) -> np.ndarray:
        """K u - F on every DOF (N, 6): reactions at fixed DOFs, constraint forces at constrained ones."""
        return self.layout.node_array(self.K @ self.U - self.F)

    def reactions(self) -> np.ndarray:
        """(N, 6) forces the supports apply (non-zero only at fixed DOFs). With constraints eliminated, a
        fixed DOF's reaction includes what the constraints carry to it (G^T (K u - F), see eliminate), so a
        support on the master of a rigid link gets the forces of its slaves."""
        r = self.K @ self.U - self.F
        out = np.zeros(self.layout.n_dof)
        idx = np.fromiter(self.fixed.keys(), np.int64, len(self.fixed))
        out[idx] = (self.G.T @ r) if self.G is not None else r[idx]
        return self.layout.node_array(out)

    def element_forces(self, group) -> np.ndarray:
        """Ke ue (E, n * n_comp) of one element group (index or object): the nodal forces each element takes."""
        g = self.model.groups[group] if isinstance(group, int) else group
        dofs = self.layout.element_dofs(g.conn, g.n_comp)
        ue = np.where(dofs >= 0, self.U[np.where(dofs >= 0, dofs, 0)], 0.0)
        return np.einsum("eij,ej->ei", g.stiffness(self.model.nodes), ue)

    def element_displacements(self, group) -> np.ndarray:
        g = self.model.groups[group] if isinstance(group, int) else group
        dofs = self.layout.element_dofs(g.conn, g.n_comp)
        return np.where(dofs >= 0, self.U[np.where(dofs >= 0, dofs, 0)], 0.0)

    def strain_energy(self) -> float:
        return 0.5 * float(self.U @ (self.K @ self.U))


def resultant(nodes: np.ndarray, node_vals: np.ndarray, about=(0.0, 0.0, 0.0)) -> np.ndarray:
    """Total force (3) and moment about `about` (3) of nodal forces and moments (N, 6)."""
    r = nodes - np.asarray(about, float)
    f = node_vals[:, :3]
    return np.concatenate([f.sum(axis=0), (np.cross(r, f) + node_vals[:, 3:]).sum(axis=0)])


class StaticSolver:
    """Assemble, constrain and factorise a model once; solve any number of load cases.

    method: "eliminate" (default), "penalty" or "lagrange" (see the module docstring).
    check_rigid: run precheck.rigid_body_check before the factorisation and refuse a model that is
    free to move as a rigid body (a mechanism of the whole model) instead of letting the factorisation fail.
    """

    def __init__(self, model: FEModel, method: str = "eliminate", penalty_scale: float = 1.0e2,
                 check_rigid: bool = True) -> None:
        self.model = model
        self.method = method
        self.layout = lay = model.layout()
        self.K = model.stiffness(lay)
        self.fixed = model.fixed_dofs(lay)
        eqs = model.equations(lay.counts)
        t0 = time.perf_counter()
        if check_rigid:
            from .precheck import rigid_body_check  # local import: precheck uses this module

            rb = rigid_body_check(model, lay, self.fixed)
            self.rigid = rb
            if rb["free_rigid_modes"] > 0:
                raise ModelError(f"the model can move as a rigid body ({rb['free_rigid_modes']} free motion(s): "
                                 f"{', '.join(rb['free_motions'])}); add supports before solving")
        if method == "eliminate":
            self.T, self.g, self.free, info = eliminate(lay.n_dof, self.fixed, eqs, lay)
            self.G = info.pop("G")
            info.pop("fixed_dofs")
            A = (self.T.T @ self.K @ self.T).tocsc()
        elif method in ("penalty", "lagrange"):
            self.T, self.g, self.free, info = eliminate(lay.n_dof, self.fixed, [], lay)
            info.pop("G")
            info.pop("fixed_dofs")
            self.G = None
            C, gc = model.constraint_matrix(lay)
            Cr = (C @ self.T).tocsr()
            rc = gc - C @ self.g
            Kr = (self.T.T @ self.K @ self.T).tocsr()
            if method == "penalty":
                alpha = penalty_scale * float(Kr.diagonal().max()) if Kr.shape[0] else 0.0
                A = (Kr + alpha * (Cr.T @ Cr)).tocsc()
                self._pen = (alpha, Cr, rc)
            else:
                A = sp.bmat([[Kr, Cr.T], [Cr, None]]).tocsc()
                self._lag = (Cr, rc)
            info = {**info, "n_constraint_rows": int(C.shape[0])}
        else:
            raise ValueError(f"unknown constraint method {method!r}")
        self.A = A
        zero = np.nonzero(np.abs(A.diagonal()[: len(self.free)]) <= 0.0)[0]
        if method != "lagrange" and len(zero):
            d = int(self.free[zero[0]])
            node = int(np.searchsorted(lay.offsets, d, side="right") - 1)
            raise ModelError(f"{len(zero)} DOF(s) have no stiffness (first: node {node} {DOF_NAMES[d - lay.offsets[node]]}); "
                             f"nothing holds them")
        t1 = time.perf_counter()
        try:
            if method in ("lagrange", "penalty"):  # indefinite, or badly scaled by alpha: let SuperLU pivot
                self.lu = spla.splu(A)
            else:
                self.lu = superlu(A)  # splu's reservation, not its factors, is most of its memory
        except RuntimeError as e:
            raise ModelError(f"the model cannot be solved ({e}): a mechanism or a part that is not held") from None
        self.info = {**info, "n_dof": lay.n_dof, "n_reduced": int(A.shape[0])}
        model.timings["reduce_s"] = t1 - t0
        model.timings["factor_s"] = time.perf_counter() - t1

    def solve(self, F) -> Solution:
        """F: DOF vector (n_dof,) or per-node array (N, 6)."""
        F = np.asarray(F, float)
        if F.ndim == 2:
            F = self.layout.from_node_array(F)
        Kg = self.K @ self.g
        rhs = self.T.T @ (F - Kg)
        if self.method == "penalty":
            alpha, Cr, rc = self._pen
            rhs = rhs + alpha * (Cr.T @ rc)
            ur = self.lu.solve(rhs)
        elif self.method == "lagrange":
            Cr, rc = self._lag
            x = self.lu.solve(np.concatenate([rhs, rc]))
            ur = x[: len(self.free)]
        else:
            ur = self.lu.solve(rhs)
        if not np.all(np.isfinite(ur)):
            raise ModelError("the solution is not finite: the model is singular or badly conditioned")
        U = self.T @ ur + self.g
        r = self.A @ (ur if self.method != "lagrange" else x)
        b = rhs if self.method != "lagrange" else np.concatenate([rhs, rc])
        rel = float(np.linalg.norm(r - b) / max(np.linalg.norm(b), 1e-300))
        sol = Solution(self.model, self.layout, U, F, self.K, self.fixed, self.method,
                       {**self.info, "solve_residual": rel}, self.G)
        if rel > 1e-6:
            raise ModelError(f"the linear solve did not converge (relative residual {rel:.2e}): the model is nearly "
                             f"singular (a mechanism or a part held only weakly)")
        return sol


def solve_static(model: FEModel, F, method: str = "eliminate", **kw) -> Solution:
    return StaticSolver(model, method, **kw).solve(F)

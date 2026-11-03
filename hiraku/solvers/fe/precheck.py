"""Checks before and after a solve: rigid-body freedom, load balance, model mass.

rigid_body_check runs before the factorisation: a model that can still move
as a rigid body (missing supports, or a group of elements joined to nothing)
is found from the rigid-body motions of each connected group, not from a
failed or meaningless factorisation.

The results come out as contract `result` items ({quantity, value, unit}).
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp

from .model import DOF_NAMES, FEModel, Layout, Solution, resultant

RIGID_NAMES = ("translation x", "translation y", "translation z", "rotation x", "rotation y", "rotation z")


def group_rigid_modes(model: FEModel, lay: Layout, labels: np.ndarray) -> tuple[np.ndarray, list]:
    """Rigid-body motions of every connected group (n_dof, 6 * groups), each about its own centroid.
    Returns the matrix and a label (group, motion name) per column."""
    n_groups = int(labels.max()) + 1 if np.any(labels >= 0) else 0
    cols, names = [], []
    for gi in range(n_groups):
        sel = np.nonzero(labels == gi)[0]
        c = model.nodes[sel].mean(axis=0)
        r = model.nodes[sel] - c
        off = lay.offsets[sel]
        six = lay.counts[sel] == 6
        for j in range(6):
            col = np.zeros(lay.n_dof)
            if j < 3:
                col[off + j] = 1.0
            else:
                w = np.zeros(3)
                w[j - 3] = 1.0
                u = np.cross(w[None, :], r)
                for k in range(3):
                    col[off + k] = u[:, k]
                col[off[six] + j] = 1.0
            cols.append(col)
            names.append((gi, RIGID_NAMES[j]))
    if not cols:
        return np.zeros((lay.n_dof, 0)), []
    return np.stack(cols, axis=1), names


def rigid_body_check(model: FEModel, lay: Layout | None = None, fixed: dict | None = None,
                     tol: float = 1e-10) -> dict:
    """How many rigid-body motions the supports, constraints and grounded elements leave free.

    For every connected group the six rigid motions Phi are tested against the
    fixed DOFs (rows of Phi there), the constraint equations (C Phi) and the
    stiffness (Phi^T K Phi, for springs to ground). A combination of the
    motions that none of them resists is a free rigid-body motion. The test
    works on 6 x groups columns, before any factorisation.
    """
    lay = lay or model.layout()
    fixed = model.fixed_dofs(lay) if fixed is None else fixed
    labels = model.connectivity_groups()
    n_groups = int(labels.max()) + 1 if np.any(labels >= 0) else 0
    if n_groups == 0:
        return {"free_rigid_modes": 0, "free_motions": [], "groups": 0}
    # orthonormal basis of each group's motions (groups do not share DOFs, so the basis is block diagonal);
    # motions that move nothing (a lone 3-DOF node's rotations) drop out
    rows, cols, vals, col_names, col_coef = [], [], [], [], []
    for gi in range(n_groups):
        sel = np.nonzero(labels == gi)[0]
        sub = Layout(lay.counts[sel])
        local = FEModel(model.nodes[sel])
        Phi_g, names_g = group_rigid_modes(local, sub, np.zeros(len(sel), int))
        Uq, s, Vt = np.linalg.svd(Phi_g, full_matrices=False)
        keep = s > 1e-9 * s.max()
        gdofs = (lay.offsets[sel][:, None] + np.arange(6)[None, :])
        gdofs = gdofs[np.arange(6)[None, :] < lay.counts[sel][:, None]]
        for j in np.nonzero(keep)[0]:
            c = len(col_names)
            nz = np.nonzero(Uq[:, j])[0]
            rows.append(gdofs[nz])
            cols.append(np.full(len(nz), c))
            vals.append(Uq[nz, j])
            col_names.append(gi)
            # rigid coefficients of this basis vector, rotations scaled by the group's RMS radius so that
            # both are displacements (for naming the motion)
            rms = float(np.sqrt(np.mean(np.sum((local.nodes - local.nodes.mean(axis=0)) ** 2, axis=1)))) or 1.0
            col_coef.append(Vt[j] / s[j] * np.array([1, 1, 1, rms, rms, rms]))
    Q = sp.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))),
                      shape=(lay.n_dof, len(col_names))).tocsc()
    S = np.zeros((Q.shape[1], Q.shape[1]))
    if fixed:
        idx = np.fromiter(fixed.keys(), np.int64, len(fixed))
        Qf = Q[idx]
        S += (Qf.T @ Qf).toarray()
    C, _ = model.constraint_matrix(lay)
    if C.shape[0]:
        rn = np.sqrt(np.asarray(C.multiply(C).sum(axis=1)).ravel())
        Cn = sp.diags(1.0 / np.where(rn > 0, rn, 1.0)) @ C
        CQ = (Cn @ Q).tocsc()
        S += (CQ.T @ CQ).toarray()
    K = model.stiffness(lay)
    dmax = float(np.abs(K.diagonal()).max()) if K.shape[0] else 1.0
    S += (Q.T @ (K @ Q)).toarray() / dmax
    S = 0.5 * (S + S.T)
    w, V = np.linalg.eigh(S)
    free = np.nonzero(w < tol)[0]
    motions = []
    col_names = np.array(col_names)
    by_group: dict[int, list] = {}
    for i in free:
        v = V[:, i]
        gi = int(col_names[np.argmax(np.abs(v))])  # the group the free motion lives in
        by_group.setdefault(gi, []).append(sum(v[c] * col_coef[c] for c in np.nonzero(col_names == gi)[0]))
    for gi, rows in sorted(by_group.items()):
        # name a basis of the free motions by row reduction with rotations tried first as pivots: a rotation
        # about an axis away from the centroid (a rotation plus a translation) is then named as the rotation
        order = [3, 4, 5, 0, 1, 2]
        A = np.array(rows)[:, order]
        piv = []
        r = 0
        for c in range(6):
            if r == len(A):
                break
            p = r + int(np.argmax(np.abs(A[r:, c])))
            if abs(A[p, c]) < 1e-6 * np.abs(A).max():
                continue
            A[[r, p]] = A[[p, r]]
            A[r] /= A[r, c]
            for k in range(len(A)):
                if k != r:
                    A[k] -= A[k, c] * A[r]
            piv.append(order[c])
            r += 1
        for j in sorted(piv):
            motions.append(f"group {gi} {RIGID_NAMES[j]}" if n_groups > 1 else RIGID_NAMES[j])
    return {"free_rigid_modes": int(len(free)), "free_motions": motions, "groups": n_groups,
            "smallest_eigenvalue": float(w.min())}


def rigid_body_energy(model: FEModel) -> dict:
    """Strain energy and constraint residual of the six rigid motions of the whole model (no supports).

    energy_ratio: max over the motions of Phi^T K Phi / Phi^T diag(K) Phi (0 when no element resists a rigid
    motion); constraint_residual: max |C Phi| / max |C| (0 when every constraint lets the model move rigidly).
    """
    lay = model.layout()
    K = model.stiffness(lay)
    Phi = model.rigid_modes(lay)
    d = K.diagonal()
    num = np.einsum("ij,ij->j", Phi, K @ Phi)
    den = np.einsum("ij,ij->j", Phi * d[:, None], Phi)
    ratio = np.abs(num) / np.where(den > 0, den, 1.0)
    C, _ = model.constraint_matrix(lay)
    if C.shape[0]:
        cres = float(np.abs(C @ Phi).max() / max(np.abs(C).max(), 1e-300))
    else:
        cres = 0.0
    return {"energy_ratio": float(ratio.max()), "per_motion": ratio, "constraint_residual": cres}


def equilibrium(sol: Solution, intended=None, about=(0.0, 0.0, 0.0)) -> dict:
    """Balance of the loads and the support reactions.

    applied: resultant (force, moment about `about`) of the load vector that was solved; reaction: of the
    support reactions. residual = |applied + reaction| / scale (force and moment parts each over their own
    scale: the largest applied or reacted force, and that force times the model size). With `intended`
    (6,) - the resultant the load case was meant to have - the same is done between the intended load and
    the reactions, which shows a load lost on the way into the model (on a missing DOF, a wrong node).
    """
    nodes = sol.model.nodes
    lay = sol.layout
    applied = resultant(nodes, lay.node_array(sol.F), about)
    react = resultant(nodes, sol.reactions(), about)
    size = float(np.ptp(nodes, axis=0).max()) if len(nodes) else 1.0

    def ratio(a, b):
        fs = max(np.linalg.norm(a[:3]), np.linalg.norm(b[:3]), 1e-300)
        ms = max(np.linalg.norm(a[3:]), np.linalg.norm(b[3:]), fs * size, 1e-300)
        return float(max(np.linalg.norm((a + b)[:3]) / fs, np.linalg.norm((a + b)[3:]) / ms))

    out = {"applied": applied, "reaction": react, "residual": ratio(applied, react)}
    if intended is not None:
        intended = np.asarray(intended, float)
        out["intended"] = intended
        out["intended_residual"] = ratio(intended, react)
    return out


def mass_check(model: FEModel, design_mass_kg: float | None = None) -> dict:
    """Model mass (kg) and its difference from the design's mass (%)."""
    m = model.total_mass() * 1000.0
    out = {"model_mass_kg": m}
    if design_mass_kg is not None and design_mass_kg > 0:
        out["design_mass_kg"] = float(design_mass_kg)
        out["mass_difference_pct"] = 100.0 * (m - design_mass_kg) / design_mass_kg
    return out


def result_items(eq: dict | None = None, mass: dict | None = None, rigid: dict | None = None) -> list[dict]:
    """Contract `result` items of the checks."""
    out = []
    if eq is not None:
        out.append({"quantity": "equilibrium_residual", "value": float(eq["residual"]), "unit": "1"})
        if "intended_residual" in eq:
            out.append({"quantity": "load_balance_residual", "value": float(eq["intended_residual"]), "unit": "1"})
    if mass is not None:
        out.append({"quantity": "model_mass", "value": float(mass["model_mass_kg"]), "unit": "kg"})
        if "mass_difference_pct" in mass:
            out.append({"quantity": "mass_difference", "value": float(mass["mass_difference_pct"]), "unit": "%"})
    if rigid is not None:
        out.append({"quantity": "free_rigid_body_modes", "value": float(rigid["free_rigid_modes"]), "unit": "1"})
    return out


__all__ = ["DOF_NAMES", "equilibrium", "group_rigid_modes", "mass_check", "result_items", "rigid_body_check",
           "rigid_body_energy"]

"""Connections: rigid links (RBE2), distributing links (RBE3), springs and bushings, shell-to-solid coupling,
and ties between meshes whose nodes do not match.

All links are constraint equations on the fe core (model.Constraint), eliminated with the rest; springs
are an element group. Formulas are written here; sources are named at each one.
"""

from __future__ import annotations

import numpy as np

from .model import Constraint, ElementGroup, Equation


def skew(r: np.ndarray) -> np.ndarray:
    """[r]x: [r]x a = r x a."""
    x, y, z = r
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


# ------------------------------------------------------------------ RBE2


def rigid_link(master: int, slaves, nodes: np.ndarray, name: str = "rbe2", comps: str = "all") -> Constraint:
    """Rigid link (RBE2 type): every slave follows the master rigidly,
        u_s = u_m + theta_m x (x_s - x_m),  theta_s = theta_m  (the latter when the slave has rotations).
    Small-rotation rigid-body kinematics (Cook et al., Concepts and Applications of FEA, 4th ed., section on
    rigid links; not checked here). The master carries 6 DOFs; comps "xyz" ties translations only."""
    master = int(master)
    slaves = [int(s) for s in np.atleast_1d(slaves) if int(s) != master]
    xm = np.asarray(nodes[master], float).copy()
    xs = {s: np.asarray(nodes[s], float).copy() for s in slaves}

    def make(counts):
        eqs = []
        for s in slaves:
            S = -skew(xs[s] - xm)  # theta x r = -r x theta = -[r]x theta
            for i in range(3):
                terms = [(s, i, 1.0), (master, i, -1.0)]
                terms += [(master, 3 + j, -S[i, j]) for j in range(3) if S[i, j] != 0.0]
                eqs.append(Equation(terms, prefer=(s, i), source=name))
            if comps == "all" and counts[s] == 6:
                for j in range(3):
                    eqs.append(Equation([(s, 3 + j, 1.0), (master, 3 + j, -1.0)], prefer=(s, 3 + j), source=name))
        return eqs

    return Constraint(name, needs_rotation=(master,), nodes=tuple([master] + slaves), maker=make)


# ------------------------------------------------------------------ RBE3


def rbe3_matrices(ref_xyz, xyz: np.ndarray, weights=None):
    """Coefficients of the distributing link: u_ref = sum_i H_i u_i, theta_ref = sum_i G_i u_i (3 x 3 each).

    The reference motion is the weighted least-squares rigid motion of the connected nodes' translations:
    minimise sum_i w_i |u_i - u_c - theta x rho_i|^2 with rho_i = x_i - c about the weighted centroid c,
    giving u_c = sum w_i u_i / sum w_i and theta = A^-1 sum_i w_i rho_i x u_i, A = sum_i w_i (|rho_i|^2 I -
    rho_i rho_i^T); then u_ref = u_c + theta x (x_ref - c). Its transpose spreads a force F and moment M at
    the reference as f_i = H_i^T F + G_i^T M, which has the same resultant and adds no stiffness (the
    interpolation constraint of Nastran's RBE3 as commonly described; derived here, the program's own
    documentation not checked). Needs three nodes not on one line for the rotations."""
    x = np.asarray(xyz, float)
    w = np.ones(len(x)) if weights is None else np.asarray(weights, float)
    W = w.sum()
    c = (w[:, None] * x).sum(axis=0) / W
    rho = x - c
    A = np.einsum("i,ij,ik->jk", w, rho, rho)
    A = np.trace(A) * np.eye(3) - A
    if np.linalg.matrix_rank(A, tol=1e-9 * max(np.trace(A), 1e-300)) < 3:
        raise ValueError("a distributing link needs at least three connected nodes not on one line")
    Ainv = np.linalg.inv(A)
    G = np.array([wi * Ainv @ skew(r) for wi, r in zip(w, rho)])  # theta = sum G_i u_i
    d = np.asarray(ref_xyz, float) - c
    H = np.array([(wi / W) * np.eye(3) - skew(d) @ Gi for wi, Gi in zip(w, G)])  # theta x d = -[d]x theta
    return H, G


def distributing_link(ref: int, nodes_ids, nodes: np.ndarray, weights=None, name: str = "rbe3") -> Constraint:
    """Distributing link (RBE3 type): the reference node (6 DOFs, dependent) moves with the weighted average
    rigid motion of the connected nodes' translations (rbe3_matrices). A load on the reference reaches the
    connected nodes without making them stiffer."""
    ref = int(ref)
    ids = [int(i) for i in np.atleast_1d(nodes_ids)]
    H, G = rbe3_matrices(nodes[ref], nodes[ids], weights)
    eqs = []
    for k in range(3):
        terms = [(ref, k, 1.0)]
        terms += [(i, l, -H[n, k, l]) for n, i in enumerate(ids) for l in range(3) if H[n, k, l] != 0.0]
        eqs.append(Equation(terms, prefer=(ref, k), source=name))
    for k in range(3):
        terms = [(ref, 3 + k, 1.0)]
        terms += [(i, l, -G[n, k, l]) for n, i in enumerate(ids) for l in range(3) if G[n, k, l] != 0.0]
        eqs.append(Equation(terms, prefer=(ref, 3 + k), source=name))
    return Constraint(name, eqs, needs_rotation=(ref,), nodes=tuple([ref] + ids))


def rbe3_forces(ref_xyz, xyz: np.ndarray, force, moment=(0.0, 0.0, 0.0), weights=None) -> np.ndarray:
    """Nodal forces (n, 3) that a distributing link gives the connected nodes for a force and moment at the
    reference: f_i = H_i^T F + G_i^T M."""
    H, G = rbe3_matrices(ref_xyz, xyz, weights)
    return np.einsum("nkl,k->nl", H, np.asarray(force, float)) + np.einsum("nkl,k->nl", G, np.asarray(moment, float))

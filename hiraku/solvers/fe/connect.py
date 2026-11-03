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

"""MITC4 shells (k1_shell.element) as an fe element group, and a k1_shell-compatible solver on the core.

The element itself is k1_shell's (Dvorkin & Bathe MITC4 with incompatible membrane modes and a small
drilling stiffness; see k1_shell/element.py). This module only calls it:
    global_stiffness_blocks(xyz (E, 4, 3), t, E, nu) -> (E, 24, 24), 6 DOFs per node in global axes.
Stresses at the element centres use element.centre_strains and the plane-stress matrix, as
k1_shell.stress does: sigma = D (eps +- t/2 kappa).
"""

from __future__ import annotations

import numpy as np

from ..k1_shell import element as mitc4
from ..k1_shell.stress import ShellStress
from .connect import rigid_link
from .model import ElementGroup, FEModel, StaticSolver


class MITC4(ElementGroup):
    """Four-node shells: conn (E, 4) counter-clockwise seen from the outer ("top") side; t, E, nu, rho per element."""

    kind = "mitc4"
    n_comp = 6

    def __init__(self, conn, t, E, nu, rho=0.0, drill_factor: float = mitc4.DRILL_FACTOR, incompatible: bool = True,
                 name: str = "shell"):
        self.conn = np.asarray(conn, int).reshape(-1, 4)
        ne = len(self.conn)
        self.t = np.broadcast_to(np.asarray(t, float), (ne,)).copy()
        self.E = np.broadcast_to(np.asarray(E, float), (ne,)).copy()
        self.nu = np.broadcast_to(np.asarray(nu, float), (ne,)).copy()
        self.rho = np.broadcast_to(np.asarray(rho, float), (ne,)).copy()
        self.drill_factor = drill_factor
        self.incompatible = incompatible
        self.name = name

    def stiffness(self, nodes: np.ndarray) -> np.ndarray:
        return mitc4.global_stiffness_blocks(nodes[self.conn], self.t, self.E, self.nu, self.drill_factor,
                                             self.incompatible)

    def areas(self, nodes: np.ndarray) -> np.ndarray:
        _, xl, _, _ = mitc4.local_frames(nodes[self.conn])
        return mitc4.element_area(xl)

    def mass_total(self, nodes: np.ndarray) -> float:
        return float(np.sum(self.rho * self.t * self.areas(nodes)))

    def stresses(self, nodes: np.ndarray, ue: np.ndarray) -> ShellStress:
        """Centre stresses (top, mid, bottom) from element displacement vectors ue (E, 24)."""
        xyz = nodes[self.conn]
        eps, kap, _, R = mitc4.centre_strains(xyz, ue)
        D = mitc4.plane_stress_matrix(self.E, self.nu)
        h = self.t / 2.0
        sig = np.stack([np.einsum("eab,eb->ea", D, eps + h[:, None] * kap),
                        np.einsum("eab,eb->ea", D, eps),
                        np.einsum("eab,eb->ea", D, eps - h[:, None] * kap)], axis=1)
        return ShellStress(sig, R, xyz.mean(axis=1))


def shell_stresses(sol, group) -> ShellStress:
    g = sol.model.groups[group] if isinstance(group, int) else group
    return g.stresses(sol.model.nodes, sol.element_displacements(g))


def from_shell_model(sm) -> tuple[FEModel, MITC4]:
    """An FEModel of a k1_shell ShellModel: the same nodes, one MITC4 group, its rigid links as RBE2
    constraints. Every node keeps 6 DOFs so the DOF numbers are k1_shell's (6 * node + k)."""
    model = FEModel(np.array(sm.nodes, float))
    g = model.add(MITC4(sm.elements, sm.thickness, sm.E, sm.nu, sm.rho, sm.drill_factor, sm.incompatible))
    masters: dict[int, list[int]] = {}
    for s, m in sm.links:
        if s != m:
            masters.setdefault(int(m), []).append(int(s))
    for m, slaves in masters.items():
        model.add(rigid_link(m, slaves, model.nodes, name=f"link {m}"))
    model.rotation_nodes.update(range(len(sm.nodes)))
    return model, g


def from_mesh_record(rec: dict, materials: dict) -> tuple[FEModel, MITC4]:
    """An FEModel of a contract shell `mesh` record. materials: element_member id -> {E_MPa, nu, density_kg_m3}."""
    nodes = np.asarray(rec["nodes_mm"], float)
    el = np.asarray(rec["elements"], int)
    mem = rec["element_member"]
    t = np.array([rec["member_thickness_mm"][m] for m in mem], float)
    E = np.array([float(materials[m]["E_MPa"]) for m in mem])
    nu = np.array([float(materials[m]["nu"]) for m in mem])
    rho = np.array([float(materials[m].get("density_kg_m3", 0.0)) * 1e-12 for m in mem])
    model = FEModel(nodes)
    g = model.add(MITC4(el, t, E, nu, rho))
    return model, g


class K1CompatSolver:
    """The k1_shell StaticSolver interface (model, fixed DOF indices; solve(F); reactions(U, F)) on the fe core,
    so k1_shell's own checks can run unchanged on the core (tests patch it in)."""

    def __init__(self, model, fixed) -> None:
        self.shell_model = model
        self.fe, self.group = from_shell_model(model)
        lay = self.fe.layout()
        for d in np.unique(np.asarray(fixed, int)):
            self.fe.prescribe(int(d) // 6, int(d) % 6, 0.0)
        try:
            self.solver = StaticSolver(self.fe)
        except ValueError as e:
            raise ValueError(f"the shell model cannot be solved on the fe core: {e}") from None
        self.layout = lay

    def solve(self, F: np.ndarray) -> np.ndarray:
        F = np.asarray(F, float)
        if F.ndim == 1:
            return self.solver.solve(F).U
        return np.stack([self.solver.solve(F[:, i]).U for i in range(F.shape[1])], axis=1)

    def reactions(self, U: np.ndarray, F: np.ndarray) -> np.ndarray:
        return self.solver.K @ U - F


__all__ = ["K1CompatSolver", "MITC4", "from_mesh_record", "from_shell_model", "shell_stresses"]

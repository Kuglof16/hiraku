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


def _thermal_args(thermal) -> tuple:
    """(alpha, T_mid, dT, nodal) from a tuple of 2 to 4 of them (dT 0 and nodal False when left out)."""
    t = tuple(thermal)
    return t + (0.0, False)[len(t) - 2:] if len(t) < 4 else t


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
        self.kg_options: dict = {}  # defaults of geometric_stiffness (at, thickness_terms) when eigen calls it

    def stiffness(self, nodes: np.ndarray) -> np.ndarray:
        return mitc4.global_stiffness_blocks(nodes[self.conn], self.t, self.E, self.nu, self.drill_factor,
                                             self.incompatible)

    def areas(self, nodes: np.ndarray) -> np.ndarray:
        _, xl, _, _ = mitc4.local_frames(nodes[self.conn])
        return mitc4.element_area(xl)

    def mass_total(self, nodes: np.ndarray) -> float:
        return float(np.sum(self.rho * self.t * self.areas(nodes)))

    def _gauss(self, nodes: np.ndarray):
        """Per 2 x 2 Gauss point: N (4,), Cartesian dN (E, 2, 4) in the element's local axes and det J (E,);
        plus R (E, 3, 3) and the element's own nodes."""
        xyz = nodes[self.conn]
        R, xl, _, _ = mitc4.local_frames(xyz)
        pts = []
        for xi, eta in mitc4.GAUSS_2x2:
            N, dN = mitc4.shape(xi, eta)
            J = np.einsum("ak,ekb->eab", dN, xl)
            det = J[:, 0, 0] * J[:, 1, 1] - J[:, 0, 1] * J[:, 1, 0]
            if np.any(det <= 0):
                raise ValueError(f"{self.name}: an element has a non-positive Jacobian")
            dNx = np.einsum("eab,bk->eak", np.linalg.inv(J), dN)
            pts.append((N, dNx, det))
        return pts, R, xyz

    def mass(self, nodes: np.ndarray, lumped: bool = False) -> np.ndarray:
        """Element mass matrices (E, 24, 24) in global axes.

        consistent: rho t integral(N_a N_b) dA on the three translations, and rho t^3 / 12 times the same on
        the three rotations (the wall's rotary inertia about its mid-surface; the drilling rotation gets it
        too so that the 3 x 3 rotation block is a multiple of I and is unchanged by the rotation to global
        axes - the same rule as k1_shell.element.lumped_mass). Bilinear N, 2 x 2 Gauss points (exact for
        a parallelogram).
        lumped: k1_shell.element.lumped_mass on the diagonal.
        """
        ne = len(self.conn)
        M = np.zeros((ne, 24, 24))
        if lumped:
            d = mitc4.lumped_mass(nodes[self.conn], self.t, self.rho)
            M[:, np.arange(24), np.arange(24)] = d
            return M
        pts, _, _ = self._gauss(nodes)
        mm = sum(np.einsum("a,b,e->eab", N, N, det) for N, _, det in pts)
        mt = (self.rho * self.t)[:, None, None] * mm
        mr = (self.rho * self.t ** 3 / 12.0)[:, None, None] * mm
        for k in range(6):
            idx = 6 * np.arange(4) + k
            M[:, idx[:, None], idx[None, :]] = mt if k < 3 else mr
        return M

    def _temperatures(self, values, nodal: bool) -> np.ndarray:
        """(E, 4) corner values: from a per-node array (nodal) or a per-element value."""
        v = np.asarray(values, float)
        if v.ndim == 0:
            return np.full(self.conn.shape, float(v))
        if nodal:
            return v[self.conn]
        return np.broadcast_to(v.reshape(-1, 1) if v.ndim else v, (len(self.conn), 4)).astype(float)

    def membrane_forces(self, nodes: np.ndarray, ue: np.ndarray, thermal=None) -> np.ndarray:
        """Membrane stress resultants (nx, ny, nxy) N/mm (E, 3) at the element centres in the local axes.
        thermal = (alpha, T_mid[, dT[, nodal]]): the free thermal strain alpha T_mid is taken off first."""
        eps, _, _, _ = mitc4.centre_strains(nodes[self.conn], ue)
        if thermal is not None:
            alpha, Tm, _, nodal = _thermal_args(thermal)
            tc = self._temperatures(Tm, nodal).mean(axis=1)
            eps = eps - (np.broadcast_to(np.asarray(alpha, float), tc.shape) * tc)[:, None] * np.array([1.0, 1.0, 0.0])
        D = mitc4.plane_stress_matrix(self.E, self.nu)
        return self.t[:, None] * np.einsum("eab,eb->ea", D, eps)

    def gauss_membrane_strains(self, nodes: np.ndarray, ue: np.ndarray) -> np.ndarray:
        """Membrane strains (E, 4, 3) in the local axes at the 2 x 2 Gauss points (mitc4.GAUSS_2x2 order),
        with the incompatible modes: their amplitudes are recovered from the nodal displacements as the
        condensation in k1_shell.element.local_stiffness eliminates them, alpha = -Kii^-1 Kmi^T u_m (the same
        matrices, rebuilt here: Wilson's modes with Taylor's centre-Jacobian correction)."""
        xyz = nodes[self.conn]
        R, xl, _, _ = mitc4.local_frames(xyz)
        ne = len(xyz)
        um = np.einsum("eij,enj->eni", R, ue.reshape(ne, 8, 3)).reshape(ne, 24)[:, mitc4._MEM]
        _, dN0 = mitc4.shape(0.0, 0.0)
        _, det0, inv0 = mitc4._jacobian(dN0, xl)
        Dm = mitc4.plane_stress_matrix(self.E, self.nu) * self.t[:, None, None]
        Bms, Bis = [], []
        Kmi = np.zeros((ne, 8, 4))
        Kii = np.zeros((ne, 4, 4))
        for xi, eta in mitc4.GAUSS_2x2:
            _, dN = mitc4.shape(xi, eta)
            _, det, inv = mitc4._jacobian(dN, xl)
            Bm = mitc4.membrane_B(np.einsum("eab,bk->eak", inv, dN))
            dP = np.array([[-2.0 * xi, 0.0], [0.0, -2.0 * eta]])
            dPx = np.einsum("eab,bk->eak", inv0, dP) * (det0 / det)[:, None, None]
            Bi = np.zeros((ne, 3, 4))
            Bi[:, 0, 0:2] = dPx[:, 0]
            Bi[:, 1, 2:4] = dPx[:, 1]
            Bi[:, 2, 0:2] = dPx[:, 1]
            Bi[:, 2, 2:4] = dPx[:, 0]
            Kmi += np.einsum("eaj,eab,ebk,e->ejk", Bm, Dm, Bi, det)
            Kii += np.einsum("eaj,eab,ebk,e->ejk", Bi, Dm, Bi, det)
            Bms.append(Bm)
            Bis.append(Bi)
        if self.incompatible:
            alpha = -np.linalg.solve(Kii, np.einsum("ejk,ej->ek", Kmi, um)[:, :, None])[:, :, 0]
        else:
            alpha = np.zeros((ne, 4))
        return np.stack([np.einsum("eak,ek->ea", Bm, um) + np.einsum("eak,ek->ea", Bi, alpha)
                         for Bm, Bi in zip(Bms, Bis)], axis=1)

    def gauss_membrane_forces(self, nodes: np.ndarray, ue: np.ndarray, thermal=None) -> np.ndarray:
        """(nx, ny, nxy) N/mm (E, 4, 3) at the 2 x 2 Gauss points, local axes; thermal as membrane_forces
        (with nodal temperatures interpolated bilinearly to each point)."""
        eps = self.gauss_membrane_strains(nodes, ue)
        if thermal is not None:
            alpha, Tm, _, nodal = _thermal_args(thermal)
            Tc = self._temperatures(Tm, nodal)
            a = np.broadcast_to(np.asarray(alpha, float), (len(self.conn),))
            for g, (xi, eta) in enumerate(mitc4.GAUSS_2x2):
                N, _ = mitc4.shape(xi, eta)
                eps[:, g] -= (a * (Tc @ N))[:, None] * np.array([1.0, 1.0, 0.0])
        D = mitc4.plane_stress_matrix(self.E, self.nu)
        return self.t[:, None, None] * np.einsum("eab,egb->ega", D, eps)

    def geometric_stiffness(self, nodes: np.ndarray, ue: np.ndarray, thermal=None, at: str | None = None,
                            thickness_terms: bool | None = None) -> np.ndarray:
        """Initial-stress stiffness (E, 24, 24) in global axes from the membrane forces of ue:
        K_G = integral over the element of grad(N_a)^T S grad(N_b) dA, S = [[nx, nxy], [nxy, ny]], times I on
        the three translations (the membrane part of the initial-stress matrix of a plate or shell: Cook,
        Malkus, Plesha & Witt 2002, chapter on buckling; Bathe 1996 section 6.3 - neither checked here). The
        sum over the three displacement components is the same in any axes, so the global translations are
        used directly.
        at: "gauss" (default) uses the membrane forces at each 2 x 2 Gauss point (gauss_membrane_forces), so a
        stress that varies inside the element (in-plane bending of a wall) is followed; "centre" uses the
        element-centre value for the whole element (the earlier form, kept for comparison).
        thickness_terms: a point at height z moves z beta in plane, beta = (ry, -rx) in local axes, so the
        initial stress also does work on the rotations: t^2 / 12 integral grad(N_a)^T S grad(N_b) dA on the
        local rx and ry (the block (I - n n^T) in global axes; drilling none). Derived here from the
        displacement field of the shell; the same kind of term as the rotational initial-stress terms of
        degenerated shells (Bathe 1996, not checked here). Small for thin walls (of order (t / wavelength)^2).
        Left as None, at and thickness_terms come from self.kg_options (default "gauss", True)."""
        at = self.kg_options.get("at", "gauss") if at is None else at
        thickness_terms = self.kg_options.get("thickness_terms", True) if thickness_terms is None else thickness_terms
        pts, R, _ = self._gauss(nodes)
        ne = len(self.conn)
        if at == "gauss":
            n = self.gauss_membrane_forces(nodes, ue, thermal)
        elif at == "centre":
            n = np.repeat(self.membrane_forces(nodes, ue, thermal)[:, None, :], 4, axis=1)
        else:
            raise ValueError(f"at must be 'gauss' or 'centre', not {at!r}")
        kg = np.zeros((ne, 4, 4))
        for g, (_, dNx, det) in enumerate(pts):
            S = np.stack([np.stack([n[:, g, 0], n[:, g, 2]], 1), np.stack([n[:, g, 2], n[:, g, 1]], 1)], 1)
            kg += np.einsum("eia,eij,ejb,e->eab", dNx, S, dNx, det)
        Kg = np.zeros((ne, 24, 24))
        for k in range(3):
            idx = 6 * np.arange(4) + k
            Kg[:, idx[:, None], idx[None, :]] = kg
        if thickness_terms:
            nrm = R[:, 2]
            P = np.eye(3)[None] - np.einsum("ei,ej->eij", nrm, nrm)
            blk = np.einsum("eab,eij,e->eaibj", kg, P, self.t ** 2 / 12.0)
            for a in range(4):
                for b in range(4):
                    Kg[:, 6 * a + 3:6 * a + 6, 6 * b + 3:6 * b + 6] += blk[:, a, :, b, :]
        return Kg

    def thermal_forces(self, nodes: np.ndarray, alpha, T_mid, dT=0.0, nodal: bool = False) -> np.ndarray:
        """Equivalent element forces (E, 24) in global axes of a temperature field: mid-surface change T_mid
        and through-thickness difference dT = T_top - T_bottom (top = the side the normal points to), linear
        through the thickness. Free thermal strain: membrane alpha T_mid (1, 1, 0), curvature alpha dT / t
        (1, 1, 0) (a point at height z has strain alpha (T_mid + z dT / t)). f = integral Bm^T Dm eps_th +
        Bb^T Db kappa_th dA. Values per element, or per node with nodal=True (bilinear in the element).
        The incompatible membrane modes get no thermal load (zero for a uniform field by Taylor's correction)."""
        pts, R, _ = self._gauss(nodes)
        ne = len(self.conn)
        a = np.broadcast_to(np.asarray(alpha, float), (ne,))
        Tm = self._temperatures(T_mid, nodal)
        dTt = self._temperatures(dT, nodal)
        D = mitc4.plane_stress_matrix(self.E, self.nu)
        Dm = D * self.t[:, None, None]
        Db = D * (self.t ** 3 / 12.0)[:, None, None]
        f = np.zeros((ne, 24))
        unit = np.array([1.0, 1.0, 0.0])
        for N, dNx, det in pts:
            eps = (a * (Tm @ N))[:, None] * unit
            kap = (a * (dTt @ N) / self.t)[:, None] * unit
            f[:, mitc4._MEM] += np.einsum("eak,eab,eb,e->ek", mitc4.membrane_B(dNx), Dm, eps, det)
            f[:, mitc4._BEN] += np.einsum("eak,eab,eb,e->ek", mitc4.bending_B(dNx), Db, kap, det)
        return np.einsum("eij,ebi->ebj", R, f.reshape(ne, 8, 3)).reshape(ne, 24)

    def pressure_forces(self, nodes: np.ndarray, p, side: str = "bottom") -> np.ndarray:
        """Consistent element forces (E, 24) of a pressure p (MPa, per element or one value) on one face:
        f_a = integral N_a p n dA over the bilinear surface of the four nodes (2 x 2 Gauss points,
        n dA = x_xi cross x_eta). The normal n points to the top (outer) side, so a pressure on the
        bottom face pushes along +n and one on the top face along -n."""
        from .solid import shape_quad4

        X = nodes[self.conn]
        ne = len(X)
        pv = np.broadcast_to(np.asarray(p, float), (ne,)) * (1.0 if side == "bottom" else -1.0)
        f = np.zeros((ne, 4, 6))
        for xi, eta in mitc4.GAUSS_2x2:
            N, dN = shape_quad4(xi, eta)
            J = np.einsum("an,enb->eab", dN, X)
            ndA = np.cross(J[:, 0], J[:, 1])
            f[:, :, :3] += N[None, :, None] * (pv[:, None] * ndA)[:, None, :]
        return f.reshape(ne, 24)

    def stresses(self, nodes: np.ndarray, ue: np.ndarray, thermal=None) -> ShellStress:
        """Centre stresses (top, mid, bottom) from element displacement vectors ue (E, 24).
        thermal = (alpha, T_mid, dT[, nodal]): the free thermal strain is taken off (sigma = D (eps - eps_th))."""
        xyz = nodes[self.conn]
        eps, kap, _, R = mitc4.centre_strains(xyz, ue)
        if thermal is not None:
            alpha, Tm, dT, nodal = _thermal_args(thermal)
            a = np.broadcast_to(np.asarray(alpha, float), (len(xyz),))
            unit = np.array([1.0, 1.0, 0.0])
            eps = eps - (a * self._temperatures(Tm, nodal).mean(axis=1))[:, None] * unit
            kap = kap - (a * self._temperatures(dT, nodal).mean(axis=1) / self.t)[:, None] * unit
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

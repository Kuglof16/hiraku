"""Known-answer checks of the load types (prescribed displacement, pressure, temperature).

Each expected value is computed here from its formula; sources (formulas as commonly quoted, the books
not opened for this code, sections not checked):
- [Cantilever] tip force of a cantilever given a tip deflection d: P = 3 E I d / L^3 (Euler-Bernoulli);
  with shear, d = P L^3 / (3 E I) + P L / (k G A) (Timoshenko & Gere, Mechanics of Materials).
- [Hoop] thin cylinder under internal pressure: sigma_theta = p r / t, radial displacement p r^2 / (E t) with
  free ends (Timoshenko, Strength of Materials, part II).
- [Navier] simply supported rectangular plate under a uniform load q: w = 16 q / (pi^6 D)
  sum over odd m, n of sin(m pi x / a) sin(n pi y / b) / (m n ((m/a)^2 + (n/b)^2)^2), and
  M_x = 16 q / pi^4 sum ((m/a)^2 + nu (n/b)^2) / (m n ((m/a)^2 + (n/b)^2)^2) sin sin
  (Timoshenko & Woinowsky-Krieger, Theory of Plates and Shells, 2nd ed. 1959, chapter 5).
- [Restrained bar] a bar held at both ends and heated uniformly by dT: sigma = -E alpha dT (free sideways).
- [Free plate] a free plate with a linear temperature difference dT through its thickness bends to the
  curvature alpha dT / t in every direction without stress (Timoshenko & Woinowsky-Krieger 1959, the
  section on thermal bending).
"""

from __future__ import annotations

import math

import numpy as np

from . import loads
from .beam import Beam, line_mesh
from .connect import rigid_link
from .eigen_checks import E_ST, G_ST, NU_ST, bar_mesh, plate_shell, polygon_section, tube_section_points, tube_shell
from .model import FEModel, StaticSolver, resultant
from .shell import MITC4
from .solid import Hex20, solid_stresses
from ..k1_shell import element as mitc4

ALPHA_ST = 1.2e-5  # 1/K, a round value for steel (not from a standard)


# ------------------------------------------------------------------ prescribed displacement


def prescribed_beam(L: float = 500.0, d: float = 2.0, n_el: int = 8) -> dict:
    """[Cantilever] beam clamped at x = 0, tip moved by d along y (rotation free)."""
    sec = polygon_section(tube_section_points("square", 40.0, 4), 2.0)
    x, c = line_mesh((0, 0, 0), (L, 0, 0), n_el)
    model = FEModel(x)
    model.add(Beam(c, E_ST, G_ST, sec["A"], sec["Iy"], sec["Iz"], sec["J"]))
    model.fix(0, "all")
    loads.prescribe(model, n_el, [1], d)
    sol = StaticSolver(model).solve(np.zeros((n_el + 1, 6)))
    R = sol.reactions()
    return {"tip_force": float(R[n_el, 1]), "root_force": float(R[0, 1]), "root_moment": float(R[0, 5]),
            "expected": 3 * E_ST * sec["Iz"] * d / L ** 3, "L": L}


def prescribed_tube(L: float = 500.0, d: float = 2.0, n_c: int = 16, n_l: int = 50) -> dict:
    """The same with a 40 x 2 square shell tube: base ring clamped, tip ring tied rigidly (RBE2) to a node on
    the axis whose y is prescribed (its rotations free). The reaction there comes through the rigid link.
    Expected with shear: P = d / (L^3 / (3 E I) + L / (k G A)), k A = the webs' area 2 h t (assumed)."""
    pts = tube_section_points("square", 40.0, n_c)
    nodes, q, r0, r1 = tube_shell(pts, L, n_l)
    model = FEModel(nodes)
    model.add(MITC4(q, 2.0, E_ST, NU_ST))
    model.fix(r0, "all")
    tip = model.add_node((L, 0, 0))
    model.add(rigid_link(tip, r1, model.nodes))
    loads.prescribe(model, tip, [1], d)
    sol = StaticSolver(model).solve(np.zeros((len(model.nodes), 6)))
    R = sol.reactions()
    sec = polygon_section(pts, 2.0)
    I = sec["Iz"]
    return {"tip_force": float(R[tip, 1]), "base_force": float(R[r0, 1].sum()),
            "expected_bending": 3 * E_ST * I * d / L ** 3,
            "expected": d / (L ** 3 / (3 * E_ST * I) + L / (G_ST * 2 * 40.0 * 2.0))}


# ------------------------------------------------------------------ pressure


def cylinder_pressure(R: float = 50.0, t: float = 1.0, L: float = 200.0, n_c: int = 48, n_l: int = 20,
                      p: float = 1.0) -> dict:
    """[Hoop] open-ended cylinder of MITC4 shells, internal pressure p on the bottom (inner) face, held only
    against rigid motion (ux on the ring x = 0, and three tangential holds on it). The facets' centres are
    at r_f = R cos(pi / n_c) from the axis; the hoop membrane force of the polygon is p r_f exactly (the
    pressure on half the polygon over the cut's projected width)."""
    pts = tube_section_points("round", R, n_c)
    nodes, q, r0, _ = tube_shell(pts, L, n_l)
    model = FEModel(nodes)
    g = model.add(MITC4(q, t, E_ST, NU_ST))
    model.fix(r0, [0])
    model.fix(r0[0], [2])  # at angle 0 (y = R): z is tangential
    model.fix(r0[n_c // 4], [1])  # at 90 deg: y is tangential
    model.fix(r0[n_c // 2], [2])  # at 180 deg
    F = loads.shell_pressure(model, g, p, "bottom")
    sol = StaticSolver(model).solve(F)
    st = g.stresses(model.nodes, sol.element_displacements(g))
    mid = st.sigma[:, 1]  # (E, 3) local (s11, s22, s12); local 1 is along the ring (the quads' first edge)
    rf = R * math.cos(math.pi / n_c)
    xc = model.nodes[g.conn].mean(axis=1)[:, 0]
    inner = (xc > 0.25 * L) & (xc < 0.75 * L)
    radial = np.einsum("ij,ij->i", sol.displacements[:len(nodes), 1:3], nodes[:, 1:3]) / R
    sel = (nodes[:, 0] > 0.25 * L) & (nodes[:, 0] < 0.75 * L)
    return {"hoop_mean": float(mid[inner, 0].mean()), "hoop_spread": float(np.ptp(mid[inner, 0])),
            "axial_max": float(np.abs(mid[inner, 1]).max()), "expected": p * rf / t, "expected_R": p * R / t,
            "radial": float(radial[sel].mean()), "radial_expected": p * R * R / (E_ST * t),
            "total_force": float(np.abs(F[:, :3].sum(axis=0)).max())}


def navier(a: float, b: float, t: float, q: float, x: float, y: float, n_terms: int = 101) -> dict:
    """[Navier] deflection and M_x, M_y at (x, y), odd m, n up to n_terms."""
    D = E_ST * t ** 3 / (12 * (1 - NU_ST ** 2))
    w = mx = my = 0.0
    for m in range(1, n_terms + 1, 2):
        for n in range(1, n_terms + 1, 2):
            s = math.sin(m * math.pi * x / a) * math.sin(n * math.pi * y / b)
            den = m * n * ((m / a) ** 2 + (n / b) ** 2) ** 2
            w += s / den
            mx += ((m / a) ** 2 + NU_ST * (n / b) ** 2) * s / den
            my += ((n / b) ** 2 + NU_ST * (m / a) ** 2) * s / den
    return {"w": 16 * q / (math.pi ** 6 * D) * w, "Mx": 16 * q / math.pi ** 4 * mx, "My": 16 * q / math.pi ** 4 * my}


def ss_plate_pressure(a: float = 200.0, b: float = 200.0, t: float = 2.0, q: float = 0.01, nx: int = 16,
                      ny: int = 16, hard: bool = True) -> dict:
    """Simply supported plate (w = 0 on the edges) under a uniform pressure q on the bottom face (along +z),
    held in plane at two corners. hard: the "hard" simple support of a Reissner-Mindlin plate (the rotation
    about the edge's in-plane normal also held), the one that tends to the Kirchhoff plate; the "soft" one
    (only w held) has a boundary layer and comes out more flexible as the mesh resolves it. Centre deflection and the top-surface bending stress sigma_x = 6 M_x / t^2
    (the plate bows towards +z, so the top is the convex, stretched side) of the four elements at the centre
    against [Navier] at their centres."""
    if nx % 2 or ny % 2:
        raise ValueError("use even divisions so that a node sits at the centre")
    nodes, qd = plate_shell(a, b, nx, ny)
    model = FEModel(nodes)
    g = model.add(MITC4(qd, t, E_ST, NU_ST))
    x, y = nodes[:, 0], nodes[:, 1]
    model.fix(np.nonzero((x < 1e-9) | (x > a - 1e-9) | (y < 1e-9) | (y > b - 1e-9))[0], [2])
    if hard:  # the rotation about the edge's normal in the plane is held too (w = 0 along the edge)
        model.fix(np.nonzero((x < 1e-9) | (x > a - 1e-9))[0], [3])
        model.fix(np.nonzero((y < 1e-9) | (y > b - 1e-9))[0], [4])
    model.fix(0, [0, 1])
    model.fix(nx, [1])
    F = loads.shell_pressure(model, g, q, "bottom")
    sol = StaticSolver(model).solve(F)
    c = int(np.argmin(np.hypot(x - a / 2, y - b / 2)))
    nv = navier(a, b, t, q, a / 2, b / 2)
    st = g.stresses(model.nodes, sol.element_displacements(g))
    xc = model.nodes[g.conn].mean(axis=1)
    near = np.argsort(np.hypot(xc[:, 0] - a / 2, xc[:, 1] - b / 2))[:4]  # the four elements at the centre
    top = st.sigma[near, 0, 0].mean()
    nvq = navier(a, b, t, q, a / 2 - a / nx / 2, b / 2 - b / ny / 2)  # at those element centres
    return {"w_centre": float(sol.displacements[c, 2]), "w_navier": nv["w"], "sigma_top": float(top),
            "sigma_navier": 6 * nvq["Mx"] / t ** 2, "total_load": float(F[:, 2].sum()), "q_area": q * a * b}


def hex20_face_pressure(size: float = 10.0, p: float = 1.0) -> dict:
    """Pressure on the face x = size of one cube HEX20: corner and mid-edge shares of the total p size^2."""
    m = bar_mesh(size, size, 1, 1)
    model = FEModel(m.nodes)
    model.add(Hex20(m.conn, E_ST, NU_ST))
    faces = m.faces_where(lambda x, y, z: np.abs(x - size) < 1e-9)
    F = loads.solid_pressure(model, faces, p)
    total = p * size * size
    f = faces[0]
    return {"corner_share": F[f[:4], 0] / total, "mid_share": F[f[4:], 0] / total, "sum": float(F[:, 0].sum() / total)}


def hex20_block_pressure(L: float = 40.0, b: float = 20.0, p: float = 5.0) -> dict:
    """A HEX20 block pressed by p on its end x = L, held by ux = 0 on x = 0 (and a 3-2-1 sideways): sigma_xx = -p
    everywhere, the others 0."""
    m = bar_mesh(L, b, 2, 2)
    model = FEModel(m.nodes)
    g = model.add(Hex20(m.conn, E_ST, NU_ST))
    _hold_bar_ends(model, m, L, b, both=False)
    F = loads.solid_pressure(model, m.faces_where(lambda x, y, z: np.abs(x - L) < 1e-9), p)
    sol = StaticSolver(model).solve(F)
    s = solid_stresses(sol, g)["stress"][np.unique(m.conn)]
    return {"sxx_min": float(s[:, 0].min()), "sxx_max": float(s[:, 0].max()), "others": float(np.abs(s[:, 1:]).max()),
            "expected": -p}


def _hold_bar_ends(model: FEModel, m, L: float, b: float, both: bool = True) -> None:
    """ux = 0 on the end x = 0 (and x = L when both), and three sideways holds at x = 0 against rigid motion."""
    model.fix(m.nodes_where(lambda x, y, z: np.abs(x) < 1e-9), [0])
    if both:
        model.fix(m.nodes_where(lambda x, y, z: np.abs(x - L) < 1e-9), [0])
    c0 = m.nodes_where(lambda x, y, z: (np.abs(x) < 1e-9) & (np.abs(y + b / 2) < 1e-9) & (np.abs(z + b / 2) < 1e-9))[0]
    c1 = m.nodes_where(lambda x, y, z: (np.abs(x) < 1e-9) & (np.abs(y - b / 2) < 1e-9) & (np.abs(z + b / 2) < 1e-9))[0]
    model.fix(c0, [1, 2])
    model.fix(c1, [2])


# ------------------------------------------------------------------ temperature


def restrained_bar(dT: float = 50.0, alpha: float = ALPHA_ST, L: float = 200.0, b: float = 20.0) -> dict:
    """[Restrained bar] for a beam (both ends clamped), a HEX20 bar (ux = 0 on both ends, free sideways, the
    temperature given per node) and a MITC4 strip (ux = 0 on both ends, free sideways)."""
    out = {"expected": -E_ST * alpha * dT}
    # beam
    x, c = line_mesh((0, 0, 0), (L, 0, 0), 4)
    bm = FEModel(x)
    gb = bm.add(Beam(c, E_ST, G_ST, b * b, b ** 4 / 12, b ** 4 / 12, 0.1406 * b ** 4))
    bm.fix([0, 4], "all")
    sol = StaticSolver(bm).solve(loads.beam_thermal(bm, gb, alpha, dT))
    out["beam_sigma"] = float(gb.axial_force(bm.nodes, sol.element_displacements(gb), (alpha, dT)).mean() / (b * b))
    out["beam_u_max"] = float(np.abs(sol.displacements).max())
    # HEX20
    m = bar_mesh(L, b, 4, 2)
    sm = FEModel(m.nodes)
    gs = sm.add(Hex20(m.conn, E_ST, NU_ST))
    _hold_bar_ends(sm, m, L, b)
    T = np.full(len(m.nodes), dT)
    sol = StaticSolver(sm).solve(loads.solid_thermal(sm, gs, alpha, T))
    s = solid_stresses(sol, gs, thermal=(alpha, T))["stress"][np.unique(m.conn)]
    out["solid_sxx"] = (float(s[:, 0].min()), float(s[:, 0].max()))
    out["solid_others"] = float(np.abs(s[:, 1:]).max())
    lat = sol.displacements[m.nodes_where(lambda x, y, z: np.abs(y - b / 2) < 1e-9), 1]
    out["solid_lateral"] = float(lat.mean())  # y expansion of the face y = b/2 (the Poisson-free part)
    out["solid_lateral_expected"] = (1 + NU_ST) * alpha * dT * b  # uniaxial stress: eps_y = alpha dT - nu sigma / E
    # shell strip in plane
    nodes, q = plate_shell(L, b, 8, 2)
    pm = FEModel(nodes)
    gp = pm.add(MITC4(q, 2.0, E_ST, NU_ST))
    xx = nodes[:, 0]
    pm.fix(np.nonzero((xx < 1e-9) | (xx > L - 1e-9))[0], [0])
    pm.fix(np.arange(len(nodes)), [2, 3, 4])  # flat, in-plane problem
    pm.fix(0, [1])
    sol = StaticSolver(pm).solve(loads.shell_thermal(pm, gp, alpha, dT))
    st = gp.stresses(pm.nodes, sol.element_displacements(gp), thermal=(alpha, dT, 0.0))
    out["shell_sxx"] = (float(st.sigma[:, 1, 0].min()), float(st.sigma[:, 1, 0].max()))
    out["shell_syy"] = float(np.abs(st.sigma[:, 1, 1]).max())
    return out


def free_plate_gradient(dT: float = 20.0, alpha: float = ALPHA_ST, a: float = 100.0, b: float = 60.0,
                        t: float = 2.0, nx: int = 10, ny: int = 6) -> dict:
    """[Free plate] MITC4 plate held only against rigid motion, top dT warmer than the bottom (given per node):
    curvatures (k_x, k_y, k_xy) at the element centres against (alpha dT / t, alpha dT / t, 0), and the stresses
    with the thermal strain taken off against 0."""
    nodes, q = plate_shell(a, b, nx, ny)
    model = FEModel(nodes)
    g = model.add(MITC4(q, t, E_ST, NU_ST))
    model.fix(0, "xyz")
    model.fix(nx, [1, 2])
    model.fix(len(nodes) - 1, [2])
    F = loads.shell_thermal(model, g, alpha, 0.0, np.full(len(nodes), dT), nodal=True)
    sol = StaticSolver(model).solve(F)
    ue = sol.element_displacements(g)
    _, kap, _, _ = mitc4.centre_strains(model.nodes[g.conn], ue)
    st = g.stresses(model.nodes, ue, thermal=(alpha, 0.0, np.full(len(nodes), dT), True))
    return {"kappa": kap, "expected": alpha * dT / t, "stress_max": float(np.abs(st.sigma).max()),
            "load_resultant": float(np.abs(resultant(model.nodes, F)).max())}


__all__ = ["cylinder_pressure", "free_plate_gradient", "hex20_block_pressure", "hex20_face_pressure", "navier",
           "prescribed_beam", "prescribed_tube", "restrained_bar", "ss_plate_pressure"]

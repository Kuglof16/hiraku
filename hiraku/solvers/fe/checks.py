"""Known-answer checks of the fe core (solids first; connections and shells further down).

Every expected value is computed here from its formula, and each function
names where the formula comes from. The functions take mesh sizes so the
tests can run coarse meshes every time and fine ones only with
HIRAKU_SLOW_TESTS=1.
"""

from __future__ import annotations

import math

import numpy as np

from qa import known_answers as ka

from . import mesh as fm
from .model import FEModel, StaticSolver
from .precheck import equilibrium, rigid_body_energy
from .solid import Hex8, Hex20, solid_stresses, surface_load

# E 205000 MPa, nu 0.3 (and 7850 kg/m^3 below): the round steel of hiraku.solvers.shelf.fixtures.STEEL, an
# assumption for the known-answer checks, no source (the expected values use the same numbers)
E_ST = 205000.0  # MPa
NU_ST = 0.3
G_ST = E_ST / (2.0 * (1.0 + NU_ST))

SRC_COWPER = ("Timoshenko shear coefficient of a rectangle k = 10 (1 + nu) / (12 + 11 nu): Cowper (1966), The shear "
              "coefficient in Timoshenko's beam theory, J. Appl. Mech. 33, 335-340 (formula as commonly quoted; "
              "not checked against the paper here)")
SRC_LAME = ("Lame's thick cylinder under internal pressure p: sigma_r = A - B / r^2, sigma_t = A + B / r^2, "
            "A = p a^2 / (b^2 - a^2), B = p a^2 b^2 / (b^2 - a^2); plane strain, sigma_z = nu (sigma_r + sigma_t), "
            "u = r [sigma_t - nu (sigma_r + sigma_z)] / E (Timoshenko & Goodier, Theory of Elasticity, 3rd ed. 1970, "
            "section on the thick cylinder; section not checked here)")


def solid_class(kind: str):
    return {"hex20": Hex20, "hex8": Hex8}[kind]


def _order(kind: str) -> int:
    return 2 if kind == "hex20" else 1


# ------------------------------------------------------------------ 1. patch test


def patch_test(kind: str = "hex20", shift=(0.13, -0.09, 0.07)) -> dict:
    """2 x 2 x 2 bricks of a unit-ish cube with the inner corner node moved (and, for HEX20, every mid-edge
    node kept at the middle of its now straight edge). Boundary nodes get the linear field
    u = a + B x; the inner nodes are solved. Returns the largest stress error over all Gauss points relative
    to the exact constant stress, and the largest error of the inner nodes' displacements."""
    order = _order(kind)
    m = fm.box_mesh([((0, 0, 0), (1, 1, 1))], 0.5, order)
    c = np.nonzero(np.all(np.abs(m.nodes - 0.5) < 1e-12, axis=1))[0][0]
    m.nodes[c] += np.asarray(shift) * 0.5
    if order == 2:  # put mid-edge nodes back on the middle of their (straight) edges
        from .solid import EDGES
        for e in m.conn:
            for k, (a, b) in enumerate(EDGES):
                m.nodes[e[8 + k]] = 0.5 * (m.nodes[e[a]] + m.nodes[e[b]])
    E, nu = 1000.0, 0.25
    model = FEModel(m.nodes)
    g = model.add(solid_class(kind)(m.conn, E, nu))
    Bm = np.array([[1.0, 0.4, -0.3], [0.2, -0.5, 0.6], [0.1, 0.7, 0.8]]) * 1e-3
    a0 = np.array([0.01, -0.02, 0.03])

    def field(x):
        return a0 + x @ Bm.T

    bnd = m.nodes_where(lambda x, y, z: (np.minimum.reduce([x, y, z, 1 - x, 1 - y, 1 - z]) < 1e-12))
    for n in bnd:
        for k in range(3):
            model.prescribe(n, k, field(m.nodes[n])[k])
    sol = StaticSolver(model).solve(np.zeros((len(m.nodes), 6)))
    inner = np.setdiff1d(np.unique(m.conn), bnd)
    du = np.abs(sol.displacements[inner, :3] - field(m.nodes[inner])).max()
    eps = 0.5 * (Bm + Bm.T)
    ev = np.array([eps[0, 0], eps[1, 1], eps[2, 2], 2 * eps[0, 1], 2 * eps[1, 2], 2 * eps[2, 0]])
    from .solid import elasticity_matrix
    s_exact = elasticity_matrix([E], [nu])[0] @ ev
    sg = g.gauss_stresses(model.nodes, sol.element_displacements(g))
    err = np.abs(sg - s_exact[None, None, :]).max() / np.abs(s_exact).max()
    return {"stress_error_rel": float(err), "inner_displacement_error": float(du), "n_inner": int(len(inner)),
            "rigid": rigid_body_energy(model)}


# ------------------------------------------------------------------ 2, 5, 6. cantilever bar


def cantilever(kind: str = "hex20", n_side: int = 2, n_len: int | None = None, L: float = 200.0, b: float = 20.0,
               h: float = 20.0, P: float = 1000.0, integration: int | None = None) -> dict:
    """Square bar along x, root face x = 0 clamped (all three translations of every node), uniform shear
    traction -P / (b h) in z on the tip face. Tip deflection: mean uz of the tip face nodes.

    Expected: Timoshenko beam P L^3 / (3 E I) + P L / (k G A), k from Cowper. Bending stress compared at
    x = L/2 on the top surface (z = h/2, y = 0): sigma_xx = P (L - x) (h/2) / I. Mid-span is one bar depth
    or more from both the clamp (where the held Poisson contraction makes the stress singular at the
    corners) and the loaded face (Saint-Venant)."""
    n_len = n_len or 10 * n_side
    order = _order(kind)
    xs = np.linspace(0, L, n_len + 1)
    ys = np.linspace(-b / 2, b / 2, n_side + 1)
    zs = np.linspace(-h / 2, h / 2, n_side + 1)
    m = fm.merge([fm.grid_block(xs, ys, zs, order)], ["bar"])
    model = FEModel(m.nodes)
    g = model.add(solid_class(kind)(m.conn, E_ST, NU_ST, integration=integration))
    model.fix(m.nodes_where(lambda x, y, z: np.abs(x) < 1e-9), "xyz")
    tip = m.nodes_where(lambda x, y, z: np.abs(x - L) < 1e-9)
    F = surface_load(m.nodes, m.faces_where(lambda x, y, z: np.abs(x - L) < 1e-9), (0.0, 0.0, -P / (b * h)))
    sol = StaticSolver(model).solve(F)
    I = b * h**3 / 12.0
    k = 10 * (1 + NU_ST) / (12 + 11 * NU_ST)
    d_eb = P * L**3 / (3 * E_ST * I)
    d_exp = d_eb + P * L / (k * G_ST * b * h)
    st = solid_stresses(sol, g)
    pt = np.nonzero(np.all(np.abs(m.nodes - [L / 2, 0.0, h / 2]) < 1e-9, axis=1))[0]
    s_mid = float(st["stress"][pt[0], 0]) if len(pt) else float("nan")
    s_exp = P * (L / 2) * (h / 2) / I
    eq = equilibrium(sol, intended=np.array([0, 0, -P, 0, 0, 0]) + np.r_[0, 0, 0, np.cross([L, 0, 0], [0, 0, -P])])
    return {"tip": float(-sol.displacements[tip, 2].mean()), "expected": d_exp, "euler_bernoulli": d_eb,
            "stress_mid": s_mid, "stress_expected": s_exp, "n_elements": len(m.conn), "n_dof": sol.layout.n_dof,
            "energy": sol.strain_energy(), "equilibrium": eq["residual"], "load_balance": eq["intended_residual"],
            "model": model, "solution": sol}


def convergence(kind: str = "hex20", sides=(1, 2, 4)) -> dict:
    """Tip deflection of the cantilever on meshes n_side = 1, 2, 4 (10 n_side along): differences between
    successive meshes and the observed order p = log2(|d1 - d2| / |d2 - d3|) (Richardson)."""
    tips = [cantilever(kind, n)["tip"] for n in sides]
    diffs = [abs(tips[i + 1] - tips[i]) for i in range(len(tips) - 1)]
    p = math.log(diffs[0] / diffs[1], sides[1] / sides[0]) if len(diffs) > 1 and diffs[1] > 0 else float("nan")
    r = sides[1] / sides[0]
    extrap = tips[-1] + (tips[-1] - tips[-2]) / (r**p - 1) if np.isfinite(p) else float("nan")
    return {"tips": tips, "diffs": diffs, "order": p, "extrapolated": extrap}


def locking(n_len: int = 10) -> dict:
    """One brick through the depth and the width, n_len along: HEX8 (full 2 x 2 x 2) locks in shear, HEX20 does
    not. Ratios of the tip deflection to the Timoshenko value."""
    out = {}
    for kind in ("hex8", "hex20"):
        r = cantilever(kind, 1, n_len)
        out[kind] = r["tip"] / r["expected"]
    out["expected"] = cantilever("hex20", 1, n_len)["expected"]
    return out


# ------------------------------------------------------------------ 3. thick cylinder (Lame)


def lame_cylinder(n_r: int = 4, n_t: int = 4, a: float = 50.0, b: float = 100.0, p: float = 10.0,
                  length: float = 20.0, kind: str = "hex20") -> dict:
    """Quarter of a thick ring (0..90 deg), plane strain: uz = 0 on both end faces z = 0 and z = length,
    uy = 0 on the face theta = 0, ux = 0 on theta = 90 deg (symmetry). Internal pressure p on r = a.
    Stresses are the nodal-averaged values in polar components at the nodes of the face z = 0; the
    largest error over all nodes of sigma_r and sigma_t relative to p, and u_r at r = a."""
    m = fm.ring_mesh(a, b, length, n_r, n_t, 1, order=_order(kind))
    model = FEModel(m.nodes)
    g = model.add(solid_class(kind)(m.conn, E_ST, NU_ST))
    model.fix(m.nodes_where(lambda x, y, z: (np.abs(z) < 1e-9) | (np.abs(z - length) < 1e-9)), "z")
    model.fix(m.nodes_where(lambda x, y, z: np.abs(y) < 1e-9), "y")
    model.fix(m.nodes_where(lambda x, y, z: np.abs(x) < 1e-9), "x")
    r_of = np.linalg.norm(m.nodes[:, :2], axis=1)
    faces = m.faces_where(lambda x, y, z: np.abs(np.hypot(x, y) - a) < 1e-6 * a)

    def traction(x, n):
        er = np.c_[x[:, :2], np.zeros(len(x))]
        return p * er / np.linalg.norm(er, axis=1)[:, None]

    F = surface_load(m.nodes, faces, traction)
    sol = StaticSolver(model).solve(F)
    st = solid_stresses(sol, g)["stress"]
    sel = m.nodes_where(lambda x, y, z: np.abs(z) < 1e-9)
    r = r_of[sel]
    c, s = m.nodes[sel, 0] / r, m.nodes[sel, 1] / r
    sx, sy, txy = st[sel, 0], st[sel, 1], st[sel, 3]
    sr = c * c * sx + s * s * sy + 2 * c * s * txy
    stt = s * s * sx + c * c * sy - 2 * c * s * txy
    A = p * a * a / (b * b - a * a)
    B = p * a * a * b * b / (b * b - a * a)
    sr_e, st_e = A - B / r**2, A + B / r**2
    inner = sel[np.abs(r - a) < 1e-6 * a]
    ur = np.einsum("ij,ij->i", sol.displacements[inner, :2], m.nodes[inner, :2]) / a
    sz = NU_ST * 2 * A
    ur_e = a * ((A + B / a**2) - NU_ST * ((A - B / a**2) + sz)) / E_ST
    return {"sr_err": float(np.abs(sr - sr_e).max() / p), "st_err": float(np.abs(stt - st_e).max() / p),
            "st_inner": float(stt[np.abs(r - a) < 1e-6 * a].mean()), "st_inner_expected": A + B / a**2,
            "sr_inner": float(sr[np.abs(r - a) < 1e-6 * a].mean()), "sr_inner_expected": -p,
            "ur_inner": float(ur.mean()), "ur_inner_expected": ur_e, "n_elements": len(m.conn),
            "equilibrium": equilibrium(sol)["residual"]}


# ------------------------------------------------------------------ 8. shell + solid cantilever


def shell_solid_cantilever(L: float = 200.0, L_solid: float = 80.0, b: float = 20.0, t: float = 4.0,
                           P: float = 100.0, n_w: int = 2, size: float = 5.0) -> dict:
    """A strip b x t (bending about its thin direction), clamped at x = 0, tip load P (-z) at x = L.

    mixed: HEX20 from 0 to L_solid (n_w bricks across b, one through t), MITC4 on the mid-plane from L_solid to
    L, the shell's root edge coupled to the solid's end face by shell_to_solid. all-shell: MITC4 from 0 to L
    (root edge clamped in all six DOFs). all-solid: HEX20 from 0 to L. Root of the solids: all translations
    of the face held. Tip load: equal shares along the shell's tip edge (half at the corners), a uniform
    traction on the solid's tip face. Beam: Timoshenko P L^3 / (3 E I) + P L / (k G b t), k from Cowper.
    """
    from .connect import shell_node_normals, shell_to_solid
    from .shell import MITC4

    def shell_strip(x0, x1, nx, ny):
        xs, ys = np.linspace(x0, x1, nx + 1), np.linspace(-b / 2, b / 2, ny + 1)
        X, Y = np.meshgrid(xs, ys, indexing="ij")
        idx = np.arange(X.size).reshape(nx + 1, ny + 1)
        q = np.stack([idx[:-1, :-1], idx[1:, :-1], idx[1:, 1:], idx[:-1, 1:]], axis=-1).reshape(-1, 4)
        return np.c_[X.ravel(), Y.ravel(), np.zeros(X.size)], q, idx

    def tip_edge_load(F, ids):
        w = np.ones(len(ids))
        w[[0, -1]] = 0.5
        F[ids, 2] -= P * w / w.sum()

    ny = 2 * n_w  # shell nodes at every solid face node across the width (corners and mid-edges)
    out = {}
    # all-shell
    xyz, q, idx = shell_strip(0.0, L, int(round(L / size)), ny)
    m = FEModel(xyz)
    m.add(MITC4(q, t, E_ST, NU_ST))
    m.fix(idx[0], "all")
    F = np.zeros((len(xyz), 6))
    tip_edge_load(F, idx[-1])
    sol = StaticSolver(m).solve(F)
    out["all_shell"] = float(-sol.displacements[idx[-1], 2].mean())
    # all-solid
    xs = np.linspace(0, L, int(round(L / (2 * size))) + 1)
    hm = fm.merge([fm.grid_block(xs, np.linspace(-b / 2, b / 2, n_w + 1), [-t / 2, t / 2], 2)])
    m = FEModel(hm.nodes)
    m.add(Hex20(hm.conn, E_ST, NU_ST))
    m.fix(hm.nodes_where(lambda x, y, z: x < 1e-9), "xyz")
    F = surface_load(hm.nodes, hm.faces_where(lambda x, y, z: np.abs(x - L) < 1e-9), (0, 0, -P / (b * t)))
    sol = StaticSolver(m).solve(F)
    out["all_solid"] = float(-sol.displacements[hm.nodes_where(lambda x, y, z: np.abs(x - L) < 1e-9), 2].mean())
    # mixed
    xs = np.linspace(0, L_solid, int(round(L_solid / (2 * size))) + 1)
    hm = fm.merge([fm.grid_block(xs, np.linspace(-b / 2, b / 2, n_w + 1), [-t / 2, t / 2], 2)])
    sxyz, q, idx = shell_strip(L_solid, L, int(round((L - L_solid) / size)), ny)
    m = FEModel(hm.nodes)
    sh_ids = m.add_nodes(sxyz)
    m.add(Hex20(hm.conn, E_ST, NU_ST))
    shell = m.add(MITC4(sh_ids[q], t, E_ST, NU_ST))
    m.fix(hm.nodes_where(lambda x, y, z: x < 1e-9), "xyz")
    face = hm.nodes_where(lambda x, y, z: np.abs(x - L_solid) < 1e-9)
    normals = shell_node_normals(shell.conn, m.nodes)
    root = sh_ids[idx[0]]
    m.add(shell_to_solid(root, normals, m.nodes, face, t))
    F = np.zeros((len(m.nodes), 6))
    tip_edge_load(F, sh_ids[idx[-1]])
    sol = StaticSolver(m).solve(F)
    out["mixed"] = float(-sol.displacements[sh_ids[idx[-1]], 2].mean())
    out["mixed_equilibrium"] = equilibrium(sol)["residual"]
    out["mixed_rigid"] = rigid_body_energy(m)
    I = b * t**3 / 12.0
    k = 10 * (1 + NU_ST) / (12 + 11 * NU_ST)
    out["beam"] = P * L**3 / (3 * E_ST * I) + P * L / (k * G_ST * b * t)
    return out


# ------------------------------------------------------------------ 9. tied T joints


STEEL = {"name": "steel for checks", "E_MPa": E_ST, "nu": NU_ST, "density_kg_m3": 7850.0}


def _design(nodes: dict, members: list, sections: dict) -> dict:
    return {"design_version": 1, "name": "t joint", "variables": {}, "supports": [], "parts": [],
            "nodes": [{"id": k, "xyz_mm": list(map(float, v))} for k, v in nodes.items()],
            "members": [{"id": m, "node_a": a, "node_b": b, "section": s, "material": "st"} for m, a, b, s in members],
            "sections": sections, "materials": {"st": STEEL}}


def rect_t_joint(size: float = 10.0, tied: bool = True, L: float = 400.0, Lb: float = 300.0,
                 host=(40.0, 80.0, 2.3), branch=(30.0, 30.0, 2.0), P=(0.0, 0.0, -1000.0), radius: float = 60.0,
                 off_corner_mm: float = 10.0) -> dict:
    """A rect tube along x (b x h x t: b across in y, h vertical) clamped at both ends (all DOFs of the end
    rings) with a square branch along +y at mid-length; load P at a node on the branch axis at its tip, tied
    to the tip ring by a rigid link.

    tied=False: k1_shell.build_mesh's T joint (the branch's end ring shares the host wall's nodes).
    tied=True: the two tubes meshed apart (k1_shell.build_mesh of each alone; the host has no grid lines
    through the branch) and the branch's end ring tied to the host's wall faces.
    Returns the tip displacement and the largest von Mises (outer or inner surface) of the elements whose
    centres are within `radius` of the point where the branch axis meets the host wall (vm_max_near_joint),
    the distance of that element's centre from the branch's footprint on the host wall (d_foot_of_max; inf
    when it is not on that wall), and the largest von Mises of the host wall's elements whose centres are at
    least `off_corner_mm` from the footprint (vm_max_off_corner).

    The footprint's corners are singular (a square tube ending on a wall), so vm_max_near_joint grows with
    every refinement there and depends on how fine the elements at the corner are (k1_shell.build_mesh's weld
    strips cut them to t x t on the shared mesh only); vm_max_off_corner is the value to compare between
    meshes."""
    from ..k1_shell.mesh import build_mesh
    from ..k1_shell.solve import ShellModel
    from .connect import rigid_link, tie
    from .shell import from_shell_model, shell_stresses

    bh, hh, th = host
    bb, hb, tb = branch
    wall = (bh - th) / 2.0
    secs = {"h": {"shape": "rect_tube", "dims_mm": {"b": bh, "h": hh, "t": th}},
            "b": {"shape": "rect_tube", "dims_mm": {"b": bb, "h": hb, "t": tb}}}
    joint = np.array([L / 2, wall, 0.0])
    tip_xyz = np.array([L / 2, wall + Lb, 0.0])
    if not tied:
        d = _design({"A": (0, 0, 0), "M": (L / 2, 0, 0), "B": (L, 0, 0), "C": tip_xyz},
                    [("h1", "A", "M", "h"), ("h2", "M", "B", "h"), ("br", "M", "C", "b")], secs)
        ms = build_mesh(d, size)
        if ms.joint_kinds.get("M") != "T":
            raise ValueError("expected a shared-node T joint")
        model, g = from_shell_model(ShellModel.from_mesh(ms, d))
        ends = [ms.end_rings[("h1", "a")], ms.end_rings[("h2", "b")]]
        tip_ring = ms.end_rings[("br", "b")]
        groups = [g]
    else:
        dh = _design({"A": (0, 0, 0), "B": (L, 0, 0)}, [("h", "A", "B", "h")], secs)
        db = _design({"P": joint, "C": tip_xyz}, [("br", "P", "C", "b")], secs)
        mh, mb = build_mesh(dh, size), build_mesh(db, size)
        smh, smb = ShellModel.from_mesh(mh, dh), ShellModel.from_mesh(mb, db)
        model, gh = from_shell_model(smh)
        off = len(model.nodes)
        model.add_nodes(smb.nodes)
        from .shell import MITC4
        gb = model.add(MITC4(smb.elements + off, smb.thickness, smb.E, smb.nu, smb.rho))
        model.rotation_nodes.update(range(len(model.nodes)))
        ends = [mh.end_rings[("h", "a")], mh.end_rings[("h", "b")]]
        tip_ring = mb.end_rings[("br", "b")] + off
        c = model.add(tie(mb.end_rings[("br", "a")] + off, gh.conn, model.nodes, max_gap=0.5 * size))
        groups = [gh, gb]
    for e in ends:
        model.fix(e, "all")
    tip = model.add_node(tip_xyz)
    model.add(rigid_link(tip, tip_ring, model.nodes))
    F = np.zeros((len(model.nodes), 6))
    F[tip, :3] = P
    sol = StaticSolver(model).solve(F)
    vm, cen, on_host = [], [], []
    for k, g in enumerate(groups):
        st = shell_stresses(sol, g)
        near = np.linalg.norm(st.centres - joint, axis=1) <= radius
        vm.append(st.von_mises("max")[near])
        cen.append(st.centres[near])
        host_el = np.isin(ms.element_member, ["h1", "h2"]) if not tied else np.full(len(st.centres), k == 0)
        on_host.append(host_el[near])
    vm, cen, on_host = np.concatenate(vm), np.concatenate(cen), np.concatenate(on_host)
    # distance of each element centre from the branch's footprint (its mid-surface rectangle) in the host wall
    # it stands on (x along the host, z up); only elements of that wall have one
    on_wall = on_host & (np.abs(cen[:, 1] - wall) < th / 2.0)
    dx = np.maximum(np.abs(cen[:, 0] - L / 2) - (bb - tb) / 2.0, 0.0)
    dz = np.maximum(np.abs(cen[:, 2]) - (hb - tb) / 2.0, 0.0)
    d_foot = np.where(on_wall, np.hypot(dx, dz), np.inf)
    off = on_wall & (d_foot >= off_corner_mm)
    return {"tip": sol.displacements[tip, :3].copy(), "vm_max_near_joint": float(vm.max()),
            "vm_max_off_corner": float(vm[off].max()), "d_foot_of_max": float(d_foot[np.argmax(vm)]),
            "n_nodes": len(model.nodes), "equilibrium": equilibrium(sol)["residual"], "model": model}


def round_tube_shell(R: float, length: float, n_c: int, n_l: int, x0: float = 0.0):
    """Shell mesh of a round tube along x (mid-surface radius R, nodes on the circle). The quads are
    counter-clockwise seen from inside: the element normal (k1_shell.element.local_frames) points to the
    axis (measured: n . radial = -1). eigen_checks.tube_shell makes outward-facing tubes. Returns nodes (N, 3),
    quads, end rings (start, end)."""
    a = 2 * np.pi * np.arange(n_c) / n_c
    xs = x0 + np.linspace(0, length, n_l + 1)
    nodes = np.array([[x, R * np.cos(t), R * np.sin(t)] for x in xs for t in a])
    q = []
    for i in range(n_l):
        for j in range(n_c):
            p, r = i * n_c + j, i * n_c + (j + 1) % n_c
            q.append([p, p + n_c, r + n_c, r])
    return nodes, np.array(q), np.arange(n_c), n_l * n_c + np.arange(n_c)


def oblique_branch_shell(R: float, r: float, angle_deg: float, Lb: float, n_c: int, n_l: int):
    """Shell mesh of a round branch (mid-surface radius r) whose axis leaves the host tube's axis (the x axis,
    host mid-surface radius R) at the origin at angle_deg from +x in the x-y plane, cut where it meets the
    host's mid-surface and ending square at distance Lb along its axis. On each generator line
    p(s) = s d + r (cos phi e1 + sin phi e2) (e2 = z, e1 = e2 x d) the start is the outer root of
    y^2 + z^2 = R^2: s0 = (sqrt(R^2 - r^2 sin^2 phi) - r cos phi cos(angle)) / sin(angle).
    Returns nodes, quads, root ring, tip ring, axis direction."""
    al = np.radians(angle_deg)
    d = np.array([np.cos(al), np.sin(al), 0.0])
    e2 = np.array([0.0, 0.0, 1.0])
    e1 = np.cross(e2, d)
    phi = 2 * np.pi * np.arange(n_c) / n_c
    s0 = (np.sqrt(R * R - (r * np.sin(phi)) ** 2) - r * np.cos(phi) * np.cos(al)) / np.sin(al)
    nodes = []
    for i in range(n_l + 1):
        f = i / n_l
        s = s0 + f * (Lb - s0)
        nodes.append(s[:, None] * d[None, :] + r * (np.cos(phi)[:, None] * e1 + np.sin(phi)[:, None] * e2))
    nodes = np.vstack(nodes)
    q = []
    for i in range(n_l):
        for j in range(n_c):
            p, k = i * n_c + j, i * n_c + (j + 1) % n_c
            q.append([p, k, k + n_c, p + n_c])  # counter-clockwise seen from outside (radial outward normal)
    return nodes, np.array(q), np.arange(n_c), n_l * n_c + np.arange(n_c), d


def oblique_round_t(angle_deg: float = 45.0, n_c: int = 16, R: float = 30.0, r: float = 15.0, t: float = 2.0,
                    L: float = 400.0, Lb: float = 200.0, P: float = 500.0) -> dict:
    """A round host tube along x (from -L/2 to L/2, both end rings clamped) and a round branch at angle_deg,
    tied to the host wall (no shared nodes). n_c: facets around the host; the branch has n_c / 2 (half the
    radius) and elements about as long as wide. Load P along -z at a node on the branch axis at its tip,
    tied to the tip ring rigidly. Returns the tip displacement."""
    from .connect import rigid_link, tie
    from .shell import MITC4

    size = 2 * np.pi * R / n_c
    nh, qh, ra, rb = round_tube_shell(R, L, n_c, int(round(L / size)), x0=-L / 2)
    nbc = max(8, n_c // 2)
    nb, qb, root, tipring, d = oblique_branch_shell(R, r, angle_deg, Lb, nbc, int(round(Lb / size)))
    model = FEModel(nh)
    gh = model.add(MITC4(qh, t, E_ST, NU_ST))
    off = len(model.nodes)
    model.add_nodes(nb)
    model.add(MITC4(qb + off, t, E_ST, NU_ST))
    c = model.add(tie(root + off, gh.conn, model.nodes, max_gap=0.5 * size))
    model.fix(np.r_[ra, rb], "all")
    tip = model.add_node(Lb * d)
    model.add(rigid_link(tip, tipring + off, model.nodes))
    F = np.zeros((len(model.nodes), 6))
    F[tip, 2] = -P
    sol = StaticSolver(model).solve(F)
    gaps = [p["gap"] for p in c.projection]
    return {"tip": sol.displacements[tip, :3].copy(), "n_nodes": len(model.nodes), "max_gap": float(max(gaps)),
            "all_inside": all(p["inside"] for p in c.projection), "equilibrium": equilibrium(sol)["residual"],
            "model": model}


# ------------------------------------------------------------------ 4. plate with a hole, 3D


def holed_plate(W: float = 100.0, d: float = 20.0, length: float = 200.0, T: float = 10.0, n_quarter: int = 6,
                n_radial: int = 6, n_thick: int = 2, ratio: float = 1.25, sigma: float = 100.0,
                kind: str = "hex20") -> dict:
    """An eighth of a plate W x length x T with a central hole d (x = 0, y = 0 and the mid-plane z = 0 are
    symmetry planes: ux, uy, uz held there). Tension sigma along y on the end y = length/2. Kt = nodal
    sigma_yy at the hole edge on the x axis (x = d/2, y = 0) over the gross stress, at the mid-plane and on
    the surface z = T/2. Expected: [Pilkey] gross Kt for d/W (qa.known_answers.hole_kt_gross), whose
    d/W -> 0 limit is [Kirsch]'s 3 (kirsch_kt). Both are plane-stress results; through the thickness the
    peak is higher at the mid-plane and lower at the surface."""
    m = fm.holed_plate_mesh(W, length, T / 2.0, d, n_quarter, n_radial, n_thick, ratio, order=_order(kind))
    model = FEModel(m.nodes)
    g = model.add(solid_class(kind)(m.conn, E_ST, NU_ST))
    model.fix(m.nodes_where(lambda x, y, z: np.abs(x) < 1e-9), "x")
    model.fix(m.nodes_where(lambda x, y, z: np.abs(y) < 1e-9), "y")
    model.fix(m.nodes_where(lambda x, y, z: np.abs(z) < 1e-9), "z")
    F = surface_load(m.nodes, m.faces_where(lambda x, y, z: np.abs(y - length / 2) < 1e-9), (0.0, sigma, 0.0))
    sol = StaticSolver(model).solve(F)
    st = solid_stresses(sol, g)["stress"]
    edge = m.nodes_where(lambda x, y, z: (np.abs(x - d / 2) < 1e-9) & (np.abs(y) < 1e-9))
    z = m.nodes[edge, 2]
    syy = st[edge, 1]
    mid = syy[np.argmin(z)] / sigma
    surf = syy[np.argmax(z)] / sigma
    return {"kt_mid": float(mid), "kt_surface": float(surf), "kt_pilkey": ka.hole_kt_gross(d / W),
            "kt_kirsch": ka.kirsch_kt(), "kt_profile": [(float(zz), float(s / sigma)) for zz, s in
                                                        sorted(zip(z, syy))],
            "n_elements": len(m.conn), "equilibrium": equilibrium(sol)["residual"]}

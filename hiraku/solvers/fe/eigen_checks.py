"""Known-answer checks of linear buckling and natural modes on the fe core.

Every expected value is computed here from its formula; each function names the source. Mesh sizes are
arguments so the tests run coarse meshes every time and finer ones only with HIRAKU_SLOW_TESTS=1.

Sources (formulas as commonly quoted; the books were not opened for this code, so section numbers are
not checked):
- [Euler] column buckling P = pi^2 E I / (K L)^2 with K = 1 for pinned-pinned and K = 2 for fixed-free:
  Timoshenko & Gere, Theory of Elastic Stability, 2nd ed. (1961), chapter 2.
- [Engesser] shear-corrected column load P = P_e / (1 + P_e / (k G A)) (Timoshenko & Gere 1961, the section
  on the effect of shearing force; not checked here). Only reported, for the size of the shear effect.
- [Plate] simply supported plate in uniaxial compression sigma_cr = k pi^2 D / (b^2 t),
  k = min over m of (m b / a + a / (m b))^2, D = E t^3 / (12 (1 - nu^2)) (Timoshenko & Gere 1961, chapter 9).
- [Beam modes] Euler-Bernoulli beam f_n = (lambda_n^2 / (2 pi L^2)) sqrt(E I / (rho A)), lambda_1 = 1.875104
  for a cantilever (the root of cos x cosh x = -1, solved here), lambda_n = n pi pinned-pinned (Blevins,
  Formulas for Natural Frequency and Mode Shape, 1979, table 8-1; not checked here).
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq

from . import eigen
from . import mesh as fm
from .beam import Beam, line_mesh
from .connect import distributing_link, rigid_link
from .model import FEModel
from .shell import MITC4
from .solid import Hex20

# Round values for steel for the known-answer checks (the expected values use the same numbers): an
# assumption, no source
E_ST = 205000.0  # MPa
NU_ST = 0.3
G_ST = E_ST / (2.0 * (1.0 + NU_ST))
RHO_ST = 7.85e-9  # t/mm^3 (7850 kg/m^3, a round value for steel)


def euler_load(E: float, I: float, L: float, end: str) -> float:
    """[Euler] P_cr; end 'pinned' (K = 1) or 'cantilever' (fixed-free, K = 2)."""
    K = {"pinned": 1.0, "cantilever": 2.0}[end]
    return math.pi ** 2 * E * I / (K * L) ** 2


def engesser(P_e: float, kGA: float) -> float:
    return P_e / (1.0 + P_e / kGA)


def cantilever_lambda(n: int = 1) -> float:
    """n-th root of cos x cosh x + 1 = 0 (cantilever beam), by bracketing near (n - 1/2) pi."""
    f = lambda x: math.cos(x) * math.cosh(x) + 1.0  # noqa: E731
    c = (n - 0.5) * math.pi
    return brentq(f, c - 0.5 * math.pi + 1e-6, c + 0.5 * math.pi - 1e-6) if n > 1 else brentq(f, 1.0, 2.5)


def beam_frequency(E: float, I: float, rho: float, A: float, L: float, lam: float) -> float:
    """[Beam modes] Hz."""
    return lam ** 2 / (2 * math.pi * L * L) * math.sqrt(E * I / (rho * A))


def plate_k(a: float, b: float, m_max: int = 20) -> tuple[float, int]:
    """[Plate] buckling coefficient and the number of half waves m along a."""
    ks = [((m * b / a + a / (m * b)) ** 2, m) for m in range(1, m_max + 1)]
    return min(ks)


# ------------------------------------------------------------------ section of a polygonal tube wall


def polygon_section(pts_yz: np.ndarray, t: float) -> dict:
    """Thin-walled closed section along the mid-line polygon pts_yz (n, 2): A, Iy (about z-axis... see below).
    Iz = integral of y^2 t ds (bending in x-y), Iy = integral of z^2 t ds, each segment integrated exactly
    (integral of y^2 over a straight segment = s (y1^2 + y1 y2 + y2^2) / 3) plus its own t^3 / 12 term; the
    centroid is at the origin for the symmetric sections used here. J by Bredt: 4 A_m^2 t / perimeter."""
    p = np.asarray(pts_yz, float)
    q = np.roll(p, -1, axis=0)
    s = np.linalg.norm(q - p, axis=1)
    y1, z1, y2, z2 = p[:, 0], p[:, 1], q[:, 0], q[:, 1]
    ny = (z2 - z1) / s  # unit normal of the segment in the y-z plane (y component)
    nz = -(y2 - y1) / s
    Iz = float(np.sum(t * s * (y1 * y1 + y1 * y2 + y2 * y2) / 3.0 + s * t ** 3 / 12.0 * ny ** 2))
    Iy = float(np.sum(t * s * (z1 * z1 + z1 * z2 + z2 * z2) / 3.0 + s * t ** 3 / 12.0 * nz ** 2))
    Am = 0.5 * abs(float(np.sum(y1 * z2 - y2 * z1)))
    return {"A": float(t * s.sum()), "Iy": Iy, "Iz": Iz, "J": 4 * Am * Am * t / float(s.sum()), "perimeter": float(s.sum())}


def tube_section_points(shape: str, size: float, n_c: int) -> np.ndarray:
    """Mid-line points (n_c, 2) in y-z, counter-clockwise about +x: 'round' (radius size, nodes on the circle)
    or 'square' (side size, n_c / 4 elements per side, starting at a corner)."""
    if shape == "round":
        a = 2 * np.pi * np.arange(n_c) / n_c
        return np.stack([size * np.cos(a), size * np.sin(a)], axis=1)
    if n_c % 4:
        raise ValueError("a square tube needs n_c divisible by 4")
    k = n_c // 4
    h = size / 2.0
    s = np.linspace(-h, h, k + 1)[:-1]
    sides = [np.stack([np.full(k, h), s], 1), np.stack([-s, np.full(k, h)], 1),
             np.stack([np.full(k, -h), -s], 1), np.stack([s, np.full(k, -h)], 1)]
    return np.vstack(sides)


def tube_shell(pts_yz: np.ndarray, length: float, n_l: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Shell mesh of a straight tube along x from 0 to length with section pts_yz (counter-clockwise about +x).
    Quads [p, next around, next around + along, p + along], so the element normal points out of the tube.
    Returns nodes, quads, start ring, end ring."""
    n_c = len(pts_yz)
    xs = np.linspace(0.0, length, n_l + 1)
    nodes = np.array([[x, y, z] for x in xs for y, z in pts_yz])
    q = []
    for i in range(n_l):
        for j in range(n_c):
            p, r = i * n_c + j, i * n_c + (j + 1) % n_c
            q.append([p, r, r + n_c, p + n_c])
    return nodes, np.array(q), np.arange(n_c), n_l * n_c + np.arange(n_c)


def plate_shell(a: float, b: float, nx: int, ny: int):
    """Flat rectangular plate in x-y (0..a, 0..b), quads counter-clockwise seen from +z. Returns nodes, quads."""
    xs, ys = np.linspace(0, a, nx + 1), np.linspace(0, b, ny + 1)
    nodes = np.array([[x, y, 0.0] for y in ys for x in xs])
    q = [[j * (nx + 1) + i, j * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i + 1, (j + 1) * (nx + 1) + i]
         for j in range(ny) for i in range(nx)]
    return nodes, np.array(q)


def bar_mesh(L: float, b: float, n_len: int, n_side: int = 1) -> fm.HexMesh:
    """HEX20 bricks of a bar along x (0..L) with a square b x b section centred on the axis."""
    ys = np.linspace(-b / 2, b / 2, n_side + 1)
    return fm.merge([fm.grid_block(np.linspace(0, L, n_len + 1), ys, ys, 2)])


def _hold_column(model: FEModel, m0: int, m1: int, end: str) -> None:
    """Supports of a column along x with end nodes m0 (base) and m1 (loaded end)."""
    if end == "pinned":
        model.fix(m0, "xyz")
        model.fix(m0, [3])  # twist
        model.fix(m1, [1, 2])
    elif end == "cantilever":
        model.fix(m0, "all")
    else:
        raise ValueError(end)


def _axial(model: FEModel, node: int, P: float) -> np.ndarray:
    F = np.zeros((len(model.nodes), 6))
    F[node, 0] = -P
    return F


# ------------------------------------------------------------------ buckling checks


def beam_column(end: str = "pinned", n_el: int = 20, L: float = 2000.0, size: float = 40.0, t: float = 2.0,
                timoshenko: bool = False) -> dict:
    """A square tube column of beams (section from polygon_section) under an axial load P = 1 kN."""
    sec = polygon_section(tube_section_points("square", size, 4), t)
    x, c = line_mesh((0, 0, 0), (L, 0, 0), n_el)
    model = FEModel(x)
    As = 2 * size * t if timoshenko else None  # web area of the square tube (assumed shear area)
    model.add(Beam(c, E_ST, G_ST, sec["A"], sec["Iy"], sec["Iz"], sec["J"], RHO_ST, shear_area_y=As, shear_area_z=As))
    _hold_column(model, 0, n_el, end)
    P = 1000.0
    r = eigen.buckling(model, _axial(model, n_el, P), 2)
    Pe = euler_load(E_ST, sec["Iz"], L, end)
    return {"P_cr": float(r.factors[0] * P), "P_euler": Pe, "factors": r.factors, "section": sec,
            "P_engesser": engesser(Pe, G_ST * 2 * size * t)}


def shell_column(shape: str = "square", end: str = "pinned", n_c: int = 16, n_l: int = 80, L: float = 2000.0,
                 size: float = 40.0, t: float = 2.0, n_modes: int = 2) -> dict:
    """A tube column of MITC4 shells (round: mid-surface radius size / 2; square: mid-line side size), each
    end ring tied rigidly (RBE2) to a node on the axis; supports and load on those nodes."""
    R = size / 2.0 if shape == "round" else size
    pts = tube_section_points(shape, R, n_c)
    nodes, q, r0, r1 = tube_shell(pts, L, n_l)
    model = FEModel(nodes)
    model.add(MITC4(q, t, E_ST, NU_ST, RHO_ST))
    m0, m1 = model.add_node((0, 0, 0)), model.add_node((L, 0, 0))
    model.add(rigid_link(m0, r0, model.nodes, name="end 0"))
    model.add(rigid_link(m1, r1, model.nodes, name="end 1"))
    _hold_column(model, m0, m1, end)
    P = 1000.0
    r = eigen.buckling(model, _axial(model, m1, P), n_modes)
    sec = polygon_section(pts, t)
    Pe = euler_load(E_ST, sec["Iz"], L, end)
    k_shear = 0.5 if shape == "round" else 2 * size * t / sec["A"]  # thin round tube 1/2; square: the webs
    return {"P_cr": float(r.factors[0] * P), "P_euler": Pe, "factors": r.factors, "section": sec,
            "P_engesser": engesser(Pe, k_shear * G_ST * sec["A"]), "n_dof": int(r.info["n_reduced"]),
            "shape": r.node_shape(0)[:len(nodes)]}


def solid_column(end: str = "pinned", n_len: int = 40, L: float = 1000.0, b: float = 20.0, n_side: int = 1) -> dict:
    """A square bar b x b of HEX20 bricks; end faces tied rigidly (RBE2) to nodes on the axis."""
    m = bar_mesh(L, b, n_len, n_side)
    model = FEModel(m.nodes)
    model.add(Hex20(m.conn, E_ST, NU_ST, RHO_ST))
    f0 = m.nodes_where(lambda x, y, z: np.abs(x) < 1e-9)
    f1 = m.nodes_where(lambda x, y, z: np.abs(x - L) < 1e-9)
    m0, m1 = model.add_node((0, 0, 0)), model.add_node((L, 0, 0))
    model.add(rigid_link(m0, f0, model.nodes, name="end 0"))
    model.add(rigid_link(m1, f1, model.nodes, name="end 1"))
    _hold_column(model, m0, m1, end)
    P = 1000.0
    r = eigen.buckling(model, _axial(model, m1, P), 2)
    I = b ** 4 / 12.0
    Pe = euler_load(E_ST, I, L, end)
    k = 10 * (1 + NU_ST) / (12 + 11 * NU_ST)  # Cowper's rectangle (see checks.SRC_COWPER)
    return {"P_cr": float(r.factors[0] * P), "P_euler": Pe, "factors": r.factors, "n_elements": len(m.conn),
            "P_engesser": engesser(Pe, k * G_ST * b * b)}


def plate_buckling(a: float = 300.0, b: float = 200.0, t: float = 2.0, nx: int = 30, ny: int = 20,
                   sigma0: float = 1.0, hard: bool = True, kind: str = "quad", bending: str = "dkt") -> dict:
    """Simply supported plate (w = 0 on the four edges; hard: the rotation about each edge's in-plane normal
    held too, the Reissner-Mindlin support that tends to Kirchhoff's - see load_checks.ss_plate_pressure)
    compressed along x by a uniform
    stress sigma0 on the edge x = a; ux = 0 on x = 0, uy = 0 at the corner (0, 0); the edges y = 0, b are
    free in plane so the stress is uniaxial. kind 'tri' / 'mixed': shell triangles (shell_checks.mixed_groups)."""
    nodes, q = plate_shell(a, b, nx, ny)
    model = FEModel(nodes)
    if kind == "quad":
        model.add(MITC4(q, t, E_ST, NU_ST))
    else:
        from .shell_checks import mixed_groups

        mixed_groups(model, q, kind, t, E_ST, NU_ST, tri_where=lambda x, y: x > a / 2, bending=bending)
    x, y = nodes[:, 0], nodes[:, 1]
    edge = np.nonzero((x < 1e-9) | (x > a - 1e-9) | (y < 1e-9) | (y > b - 1e-9))[0]
    model.fix(edge, [2])
    if hard:
        model.fix(np.nonzero((x < 1e-9) | (x > a - 1e-9))[0], [3])
        model.fix(np.nonzero((y < 1e-9) | (y > b - 1e-9))[0], [4])
    model.fix(np.nonzero(x < 1e-9)[0], [0])
    model.fix(0, [1])
    F = np.zeros((len(nodes), 6))
    right = np.nonzero(x > a - 1e-9)[0]
    h = b / ny
    w = np.full(len(right), h)
    w[np.isclose(y[right], 0) | np.isclose(y[right], b)] = h / 2
    F[right, 0] = -sigma0 * t * w
    r = eigen.buckling(model, F, 3)
    k, m = plate_k(a, b)
    D = E_ST * t ** 3 / (12 * (1 - NU_ST ** 2))
    s_cr = k * math.pi ** 2 * D / (b * b * t)
    # half waves of the computed mode along x: sign changes of w on the line y = b / 2
    mid = np.nonzero(np.isclose(y, b / 2))[0]
    mid = mid[np.argsort(x[mid])]
    wz = r.node_shape(0)[mid, 2]
    wz = wz[np.abs(wz) > 1e-6 * np.abs(wz).max()]
    waves = int(np.sum(np.diff(np.sign(wz)) != 0) + 1)
    return {"sigma_cr": float(r.factors[0] * sigma0), "sigma_formula": s_cr, "k": k, "m": m, "m_fe": waves,
            "factors": r.factors}


# ------------------------------------------------------------------ buckling under temperature and given displacements

ALPHA = 1.2e-5  # 1/K, a round value for steel (as load_checks.ALPHA_ST): an assumption for the checks, no source


def thermal_column(kind: str = "beam", n_el: int = 20, L: float = 2000.0, size: float = 40.0, t: float = 2.0,
                   n_c: int = 16, n_side: int = 1, b: float = 20.0, ends: str = "rbe3") -> dict:
    """[Thermal column] a pinned column held axially at both ends and heated uniformly by dT buckles at
    dT_cr = pi^2 I / (alpha A L^2) (the axial force -E A alpha dT reaching Euler's load; derived from
    [Euler]). The buckling runs on the reference temperature dT = 1 K: eigen.buckling with the thermal load
    and the thermal strain taken off the stresses. kind 'beam' (square tube section), 'shell' (40 x 2 square
    tube of MITC4) or 'solid' (b x b HEX20 bar, L = 1000 by default when solid). The shell's end rings and
    the bar's end faces are joined to nodes on the axis, where the supports are, by distributing links
    (ends 'rbe3', the default) or rigid links ('rbe2'). A rigid link stops the end section's own thermal
    expansion, so near the ends the wall is compressed in both directions (sigma_axial up to
    -E alpha dT / (1 - nu)) and dT_cr comes out low; a distributing link adds no stiffness and lets the
    section expand."""
    from . import loads

    dT = 1.0
    if kind == "beam":
        sec = polygon_section(tube_section_points("square", size, 4), t)
        x, c = line_mesh((0, 0, 0), (L, 0, 0), n_el)
        model = FEModel(x)
        g = model.add(Beam(c, E_ST, G_ST, sec["A"], sec["Iy"], sec["Iz"], sec["J"], name="column"))
        m0, m1 = 0, n_el
        F = loads.beam_thermal(model, g, ALPHA, dT)
        th = {g.name: (ALPHA, dT)}
        A, I = sec["A"], sec["Iz"]
    elif kind == "shell":
        pts = tube_section_points("square", size, n_c)
        nodes, q, r0, r1 = tube_shell(pts, L, n_el)
        model = FEModel(nodes)
        g = model.add(MITC4(q, t, E_ST, NU_ST, name="column"))
        m0, m1 = model.add_node((0, 0, 0)), model.add_node((L, 0, 0))
        link = rigid_link if ends == "rbe2" else distributing_link
        model.add(link(m0, r0, model.nodes, name="end 0"))
        model.add(link(m1, r1, model.nodes, name="end 1"))
        F = loads.shell_thermal(model, g, ALPHA, dT)
        th = {g.name: (ALPHA, dT, 0.0)}
        sec = polygon_section(pts, t)
        A, I = sec["A"], sec["Iz"]
    elif kind == "solid":
        L = 1000.0 if L == 2000.0 else L
        m = bar_mesh(L, b, n_el, n_side)
        model = FEModel(m.nodes)
        g = model.add(Hex20(m.conn, E_ST, NU_ST, name="column"))
        f0 = m.nodes_where(lambda x, y, z: np.abs(x) < 1e-9)
        f1 = m.nodes_where(lambda x, y, z: np.abs(x - L) < 1e-9)
        m0, m1 = model.add_node((0, 0, 0)), model.add_node((L, 0, 0))
        link = rigid_link if ends == "rbe2" else distributing_link
        model.add(link(m0, f0, model.nodes, name="end 0"))
        model.add(link(m1, f1, model.nodes, name="end 1"))
        F = loads.solid_thermal(model, g, ALPHA, dT)
        th = {g.name: (ALPHA, dT)}
        A, I = b * b, b ** 4 / 12.0
    else:
        raise ValueError(kind)
    _hold_column(model, m0, m1, "pinned")
    model.fix(m1, [0])
    r = eigen.buckling(model, F, 2, thermal=th)
    expected = math.pi ** 2 * I / (ALPHA * A * L * L)
    N = None
    if kind == "beam":
        N = float(g.axial_force(model.nodes, r.static.element_displacements(g), (ALPHA, dT)).mean())
    return {"dT_cr": float(r.factors[0] * dT), "dT_formula": expected, "axial_force": N,
            "axial_expected": -E_ST * A * ALPHA * dT, "factors": r.factors}


def thermal_plate(kind: str = "quad", a: float = 200.0, b: float = 200.0, t: float = 2.0, nx: int = 16,
                  ny: int = 16) -> dict:
    """[Thermal plate] a simply supported plate (the hard support of plate_buckling) held in plane on all four
    edges and heated uniformly by dT: sigma_x = sigma_y = -E alpha dT / (1 - nu) everywhere, and equal biaxial
    compression N buckles at N = pi^2 D (1 / a^2 + 1 / b^2) (m = n = 1; Timoshenko & Gere 1961, chapter 9, the
    biaxial case, not checked here - derived here from w = sin sin), so dT_cr = (1 - nu) pi^2 D (1/a^2 + 1/b^2) / (E alpha t).
    kind 'quad' (MITC4, soft drilling springs), 'tri' (DKT triangles) or 'mixed'."""
    from . import loads
    from .shell_checks import drill_springs, mixed_groups

    nodes, q = plate_shell(a, b, nx, ny)
    model = FEModel(nodes)
    gs = mixed_groups(model, q, kind, t, E_ST, NU_ST, tri_where=lambda x, y: x > a / 2)
    if kind != "tri":
        drill_springs(model, np.arange(len(nodes)), 5)
    x, y = nodes[:, 0], nodes[:, 1]
    edge = np.nonzero((x < 1e-9) | (x > a - 1e-9) | (y < 1e-9) | (y > b - 1e-9))[0]
    model.fix(edge, [0, 1, 2])
    model.fix(np.nonzero((x < 1e-9) | (x > a - 1e-9))[0], [3])
    model.fix(np.nonzero((y < 1e-9) | (y > b - 1e-9))[0], [4])
    dT = 1.0
    F = sum(loads.shell_thermal(model, g, ALPHA, dT) for g in gs)
    th = {id(g): (ALPHA, dT, 0.0) for g in gs}
    r = eigen.buckling(model, F, 2, thermal=th)
    D = E_ST * t ** 3 / (12 * (1 - NU_ST ** 2))
    expected = (1 - NU_ST) * math.pi ** 2 * D * (1 / a ** 2 + 1 / b ** 2) / (E_ST * ALPHA * t)
    nm = gs[0].membrane_forces(model.nodes, r.static.element_displacements(gs[0]), (ALPHA, dT, 0.0))
    return {"dT_cr": float(r.factors[0] * dT), "dT_formula": expected, "n_membrane": nm,
            "n_expected": -E_ST * ALPHA * dT * t / (1 - NU_ST), "factors": r.factors}


def prescribed_column(kind: str = "beam", n_el: int = 20, L: float = 2000.0, size: float = 40.0, t: float = 2.0,
                      n_c: int = 16, d: float = 1.0) -> dict:
    """[Euler] with the load given as a displacement: the top of a pinned column is pushed down by d (ux = -d
    prescribed, no force), and the buckling factor scales d. P_cr = factor times the reaction at the top.
    kind 'beam' or 'shell' (square tube of MITC4, the prescribed node the master of the end ring's RBE2)."""
    if kind == "beam":
        sec = polygon_section(tube_section_points("square", size, 4), t)
        x, c = line_mesh((0, 0, 0), (L, 0, 0), n_el)
        model = FEModel(x)
        model.add(Beam(c, E_ST, G_ST, sec["A"], sec["Iy"], sec["Iz"], sec["J"]))
        m0, m1 = 0, n_el
    else:
        pts = tube_section_points("square", size, n_c)
        nodes, q, r0, r1 = tube_shell(pts, L, n_el)
        model = FEModel(nodes)
        model.add(MITC4(q, t, E_ST, NU_ST))
        m0, m1 = model.add_node((0, 0, 0)), model.add_node((L, 0, 0))
        model.add(rigid_link(m0, r0, model.nodes, name="end 0"))
        model.add(rigid_link(m1, r1, model.nodes, name="end 1"))
        sec = polygon_section(pts, t)
    _hold_column(model, m0, m1, "pinned")
    model.prescribe(m1, 0, -d)
    r = eigen.buckling(model, np.zeros((len(model.nodes), 6)), 2)
    P_ref = float(-r.static.reactions()[m1, 0])  # the force pushing the top down
    return {"P_cr": float(r.factors[0] * P_ref), "P_euler": euler_load(E_ST, sec["Iz"], L, "pinned"),
            "P_ref": P_ref, "P_ref_expected": E_ST * sec["A"] * d / L, "factors": r.factors}


# ------------------------------------------------------------------ lateral-torsional buckling


def ibeam_section(h: float = 200.0, b: float = 100.0, tf: float = 8.5, tw: float = 5.6) -> dict:
    """Doubly symmetric I-section of plates (total depth h, flange width b, thicknesses tf, tw), computed here:
    A = 2 b tf + (h - 2 tf) tw; Iy (strong, about the axis along the flanges) and Iz (weak) of the rectangles;
    J = (2 b tf^3 + (h - tf) tw^3) / 3 (thin-walled open section, mid-lines; the junctions' fillets left out);
    Cw = Iz_flanges h0^2 / 4 = tf b^3 h0^2 / 24, h0 = h - tf the distance between the flanges' mid-planes
    (the usual thin-walled values, e.g. Trahair 1993, not checked here)."""
    hw = h - 2 * tf
    A = 2 * b * tf + hw * tw
    Iy = b * h ** 3 / 12.0 - (b - tw) * hw ** 3 / 12.0
    Iz = 2 * tf * b ** 3 / 12.0 + hw * tw ** 3 / 12.0
    h0 = h - tf
    J = (2 * b * tf ** 3 + h0 * tw ** 3) / 3.0
    Cw = tf * b ** 3 * h0 ** 2 / 24.0
    return {"A": A, "Iy": Iy, "Iz": Iz, "J": J, "Cw": Cw, "h0": h0, "h": h, "b": b, "tf": tf, "tw": tw}


def lateral_torsional_moment(E: float, G: float, Iz: float, J: float, L: float, Cw: float = 0.0) -> float:
    """[LTB] M_cr = (pi / L) sqrt(E Iz G J) sqrt(1 + pi^2 E Cw / (G J L^2)) for a simply supported (fork) beam
    under a uniform moment (Timoshenko & Gere 1961, chapter 6; not checked here)."""
    return math.pi / L * math.sqrt(E * Iz * G * J) * math.sqrt(1.0 + math.pi ** 2 * E * Cw / (G * J * L * L))


def beam_lateral_buckling(L: float = 4000.0, n_el: int = 16, sec: dict | None = None, ratio: float = 1.0) -> dict:
    """An I-beam of Beam elements along x, web along z (strong bending in x-z, about local y = global y), fork
    ends (uy = uz = 0, twist held; ux = 0 at x = 0), end moments about y: M at x = L and -ratio M at x = 0
    (ratio 1 = uniform moment). Against [LTB] with Cw = 0 (the element has no warping stiffness)."""
    sec = sec or ibeam_section()
    x, c = line_mesh((0, 0, 0), (L, 0, 0), n_el)
    model = FEModel(x)
    model.add(Beam(c, E_ST, G_ST, sec["A"], sec["Iy"], sec["Iz"], sec["J"], ref=(0.0, 1.0, 0.0)))
    for n in (0, n_el):
        model.fix(n, [1, 2, 3])
    model.fix(0, [0])
    M = 1.0e6
    F = np.zeros((n_el + 1, 6))
    F[n_el, 4] = M
    F[0, 4] = -ratio * M
    r = eigen.buckling(model, F, 2)
    return {"M_cr": float(r.factors[0] * M), "M_formula": lateral_torsional_moment(E_ST, G_ST, sec["Iz"], sec["J"], L),
            "M_warping": lateral_torsional_moment(E_ST, G_ST, sec["Iz"], sec["J"], L, sec["Cw"]),
            "factors": r.factors, "section": sec, "shape": r.node_shape(0)}


def shell_ibeam_lateral_buckling(L: float = 4000.0, n_l: int = 80, n_flange: int = 4, n_web: int = 4,
                                 sec: dict | None = None) -> dict:
    """The same I-beam in MITC4 shells: flanges in the planes z = +-h0 / 2 (width b, n_flange elements), web in
    y = 0 (depth h0, n_web elements), sharing the junction lines. Fork ends: uy = 0 on every end node, uz = 0
    on the end nodes of the web (warping, ux, free); ux = 0 at the web's centre at x = 0. End moments as the
    axial traction sigma = M z / Iy_shell over the end section (consistent nodal forces), Iy_shell the
    mid-line section's own Iy. Soft drilling springs (shell_checks.drill_springs) on each wall's normal.
    Shells carry warping, so against [LTB] with Cw (and the web may distort a little)."""
    from .shell_checks import drill_springs

    sec = sec or ibeam_section()
    b, h0, tf, tw = sec["b"], sec["h0"], sec["tf"], sec["tw"]
    xs = np.linspace(0.0, L, n_l + 1)
    ys = np.linspace(-b / 2, b / 2, n_flange + 1)
    zs = np.linspace(-h0 / 2, h0 / 2, n_web + 1)
    if n_flange % 2:
        raise ValueError("n_flange must be even (the web meets the flange at a node)")
    pts, key = [], {}

    def node(x, y, z):
        k = (round(x, 9), round(y, 9), round(z, 9))
        if k not in key:
            key[k] = len(pts)
            pts.append((x, y, z))
        return key[k]

    top, bot, web = [], [], []
    for i in range(n_l):
        x0, x1 = xs[i], xs[i + 1]
        for j in range(n_flange):
            for zf, lst in ((h0 / 2, top), (-h0 / 2, bot)):
                lst.append([node(x0, ys[j], zf), node(x1, ys[j], zf), node(x1, ys[j + 1], zf), node(x0, ys[j + 1], zf)])
        for j in range(n_web):
            web.append([node(x0, 0.0, zs[j]), node(x0, 0.0, zs[j + 1]), node(x1, 0.0, zs[j + 1]), node(x1, 0.0, zs[j])])
    nodes = np.array(pts)
    model = FEModel(nodes)
    model.add(MITC4(np.array(top + bot), tf, E_ST, NU_ST, name="flanges"))
    model.add(MITC4(np.array(web), tw, E_ST, NU_ST, name="web"))
    x, y, z = nodes[:, 0], nodes[:, 1], nodes[:, 2]
    on_web = np.abs(y) < 1e-9
    on_fl = np.abs(np.abs(z) - h0 / 2) < 1e-9
    drill_springs(model, np.nonzero(on_fl)[0], 5)
    drill_springs(model, np.nonzero(on_web)[0], 4)
    ends = [np.nonzero(x < 1e-9)[0], np.nonzero(x > L - 1e-9)[0]]
    for e in ends:
        model.fix(e, [1])
        model.fix(e[on_web[e]], [2])
    model.fix(int(np.nonzero((x < 1e-9) & on_web & (np.abs(z) < 1e-9))[0][0]) if n_web % 2 == 0 else ends[0][0], [0])
    Iy = 2 * b * tf * (h0 / 2) ** 2 + 2 * b * tf ** 3 / 12.0 + tw * h0 ** 3 / 12.0  # mid-line section
    M = 1.0e6
    F = np.zeros((len(nodes), 6))
    for sgn, e in zip((-1.0, 1.0), ends):
        for row, t_, coord in ((e[on_web[e]], tw, z), (e[on_fl[e] & (z[e] > 0)], tf, y), (e[on_fl[e] & (z[e] < 0)], tf, y)):
            row = row[np.argsort(coord[row])]
            qv = sgn * M * z[row] / Iy * t_
            for i in range(len(row) - 1):
                ds = coord[row[i + 1]] - coord[row[i]]
                F[row[i], 0] += ds * (2 * qv[i] + qv[i + 1]) / 6.0
                F[row[i + 1], 0] += ds * (qv[i] + 2 * qv[i + 1]) / 6.0
    r = eigen.buckling(model, F, 2)
    return {"M_cr": float(r.factors[0] * M),
            "M_warping": lateral_torsional_moment(E_ST, G_ST, sec["Iz"], sec["J"], L, sec["Cw"]),
            "M_formula": lateral_torsional_moment(E_ST, G_ST, sec["Iz"], sec["J"], L), "n_nodes": len(nodes),
            "moment_applied": float(np.sum(F[ends[1], 0] * z[ends[1]]))}


# ------------------------------------------------------------------ mode checks


def cantilever_plate_modes(L: float = 500.0, b: float = 50.0, t: float = 2.0, nx: int = 40, ny: int = 4,
                           lumped: bool = False) -> dict:
    """A plate strip L x b x t clamped along x = 0: the first bending frequency against [Beam modes] with
    lambda_1 = 1.875 (E for a narrow strip; E / (1 - nu^2) is the bound of a wide one)."""
    nodes, q = plate_shell(L, b, nx, ny)
    model = FEModel(nodes)
    model.add(MITC4(q, t, E_ST, NU_ST, RHO_ST))
    model.fix(np.nonzero(nodes[:, 0] < 1e-9)[0], "all")
    r = eigen.modes(model, 4, lumped=lumped)
    I = b * t ** 3 / 12.0
    lam = cantilever_lambda(1)
    f_beam = beam_frequency(E_ST, I, RHO_ST, b * t, L, lam)
    f_plate = f_beam / math.sqrt(1 - NU_ST ** 2)
    # the first mode whose shape is mostly out of plane (z) and bending (not twist): same sign across the width
    i = _first_bending(r, nodes, axis=2, across=1)
    return {"f": float(r.frequencies[i]), "f_beam": f_beam, "f_plate_bound": f_plate, "lambda1": lam,
            "frequencies": r.frequencies, "mode_index": i}


def _first_bending(r, nodes: np.ndarray, axis: int, across: int) -> int:
    """Index of the first mode dominated by translation along `axis` that does not change sign across
    the `across` coordinate at the free end (bending, not twist)."""
    tip = np.nonzero(nodes[:, 0] > nodes[:, 0].max() - 1e-9)[0]
    for i in range(len(r.frequencies)):
        u = r.node_shape(i)[: len(nodes), :3]
        if np.abs(u[:, axis]).max() < 0.7 * np.abs(u).max():
            continue
        w = u[tip, axis]
        if np.all(w * np.sign(w[np.argmax(np.abs(w))]) > -1e-9 * np.abs(w).max()):
            return i
    raise AssertionError("no bending mode found among the computed modes")


def tube_cantilever_modes(shape: str = "square", n_c: int = 16, n_l: int = 50, L: float = 1000.0,
                          size: float = 40.0, t: float = 2.0, lumped: bool = False, n_modes: int = 6) -> dict:
    """A tube clamped at x = 0 (all DOFs of the base ring), shells; the first two bending frequencies (y, z)
    against [Beam modes] lambda_1 with the section of the same mid-line polygon, and a beam model of the
    same tube."""
    R = size / 2.0 if shape == "round" else size
    pts = tube_section_points(shape, R, n_c)
    nodes, q, r0, _ = tube_shell(pts, L, n_l)
    model = FEModel(nodes)
    model.add(MITC4(q, t, E_ST, NU_ST, RHO_ST))
    model.fix(r0, "all")
    r = eigen.modes(model, n_modes, lumped=lumped)
    sec = polygon_section(pts, t)
    lam = cantilever_lambda(1)
    f_formula = beam_frequency(E_ST, sec["Iz"], RHO_ST, sec["A"], L, lam)
    # beam model of the same tube (Euler-Bernoulli, consistent mass)
    x, c = line_mesh((0, 0, 0), (L, 0, 0), 20)
    bm = FEModel(x)
    bm.add(Beam(c, E_ST, G_ST, sec["A"], sec["Iy"], sec["Iz"], sec["J"], RHO_ST))
    bm.fix(0, "all")
    rb = eigen.modes(bm, 2)
    iy = _first_bending(r, nodes, axis=1, across=2)
    iz = _first_bending(r, nodes, axis=2, across=1)
    return {"f_y": float(r.frequencies[iy]), "f_z": float(r.frequencies[iz]), "f_formula": f_formula,
            "f_beam_fe": float(rb.frequencies[0]), "frequencies": r.frequencies, "section": sec,
            "mass": model.total_mass()}


def solid_cantilever_modes(L: float = 400.0, b: float = 20.0, n_len: int = 20, lumped: bool = False) -> dict:
    """A HEX20 bar b x b x L clamped at x = 0: the first bending frequency against [Beam modes]."""
    m = bar_mesh(L, b, n_len, 1)
    model = FEModel(m.nodes)
    model.add(Hex20(m.conn, E_ST, NU_ST, RHO_ST))
    model.fix(m.nodes_where(lambda x, y, z: np.abs(x) < 1e-9), "all")
    r = eigen.modes(model, 3, lumped=lumped)
    f = beam_frequency(E_ST, b ** 4 / 12.0, RHO_ST, b * b, L, cantilever_lambda(1))
    return {"f": float(r.frequencies[0]), "f_formula": f, "frequencies": r.frequencies, "n_elements": len(m.conn)}


__all__ = ["beam_column", "beam_frequency", "cantilever_lambda", "cantilever_plate_modes", "engesser", "euler_load",
           "plate_buckling", "plate_k", "plate_shell", "polygon_section", "shell_column", "solid_cantilever_modes",
           "solid_column", "tube_cantilever_modes", "tube_section_points", "tube_shell"]

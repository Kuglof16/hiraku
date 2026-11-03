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
from .connect import rigid_link
from .model import FEModel
from .shell import MITC4
from .solid import Hex20

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

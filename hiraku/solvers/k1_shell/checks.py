"""Known-answer checks of the shell solver (tool_check records).

Every expected value is computed here from its formula; each record names
the formula and where it comes from. shell_known_answers() is the set a
tool runs every time (small meshes, about 3 s); the functions below take
mesh sizes so tests can run finer ones.
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc import sections
from ..k0_handcalc.units import STANDARD_GRAVITY
from ..shelf import fixtures
from ..shelf.base import Record, check_record
from . import element
from .mesh import build_mesh
from .solve import ShellModel, StaticSolver, dofs_of
from .stress import element_stresses

STEEL = fixtures.STEEL
E_ST = STEEL["E_MPa"]
NU_ST = STEEL["nu"]
G_ST = E_ST / (2.0 * (1.0 + NU_ST))

SRC_MH = ("MacNeal & Harder (1985), A proposed standard set of problems to test finite element accuracy, "
          "Finite Elements in Analysis and Design 1, 3-20: patch test geometry (inner nodes as commonly quoted; "
          "coordinates not checked against the paper here)")
SRC_BEAM = ("cantilever, tip load: P L^3 / (3 E I) + P L / (G A_s) (Euler-Bernoulli plus Timoshenko shear; A_s taken "
            "as the walls parallel to the load, 2 t (wall mid-line length), an approximation); I from "
            "k0_handcalc.sections.rect_tube")
SRC_BREDT = "Bredt's thin-walled closed section: twist rate T / (G J), J = 4 Am^2 t / perimeter; shear T / (2 Am t)"
SRC_NAVIER = ("simply supported square plate, uniform pressure: Navier double series for the centre deflection "
              "(Timoshenko & Woinowsky-Krieger, Theory of Plates and Shells, 2nd ed. 1959), summed here")
SRC_HEYWOOD = ("finite-width plate with a central hole in tension: Kt_net = 2 + (1 - d/W)^3 (Heywood), Kt_gross = "
               "Kt_net / (1 - d/W); limit d/W -> 0 is Kirsch's 3. Formula as quoted in Peterson's Stress Concentration "
               "Factors (Pilkey & Pilkey, 3rd ed., Wiley 2008), chapter 4 (holes); not checked against the book here")


# ------------------------------------------------------------------ flat helpers


def flat_model(xy: np.ndarray, quads: np.ndarray, t: float, E: float = E_ST, nu: float = NU_ST,
               **kw) -> ShellModel:
    n = len(quads)
    nodes = np.c_[np.asarray(xy, float), np.zeros(len(xy))]
    return ShellModel(nodes, np.asarray(quads, int), np.full(n, t), np.full(n, E), np.full(n, nu), np.zeros(n), **kw)


def grid(nx: int, ny: int, lx: float, ly: float, x0: float = 0.0, y0: float = 0.0):
    """Rectangular grid of nx x ny quads: node coordinates (N, 2) and quads (E, 4), counter-clockwise."""
    xs, ys = np.linspace(x0, x0 + lx, nx + 1), np.linspace(y0, y0 + ly, ny + 1)
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    idx = np.arange((nx + 1) * (ny + 1)).reshape(nx + 1, ny + 1)
    q = np.stack([idx[:-1, :-1], idx[1:, :-1], idx[1:, 1:], idx[:-1, 1:]], axis=-1).reshape(-1, 4)
    return np.stack([X.ravel(), Y.ravel()], axis=1), q, idx


def _prescribed(model: ShellModel, fixed: np.ndarray, values: np.ndarray, F: np.ndarray | None = None) -> np.ndarray:
    """Solve with fixed DOFs held at given values."""
    U0 = np.zeros(model.n_dof)
    U0[fixed] = values
    rhs = (np.zeros(model.n_dof) if F is None else F) - model.stiffness() @ U0
    rhs[fixed] = 0.0
    U = StaticSolver(model, fixed).solve(rhs)
    U[fixed] = values
    return U


# ------------------------------------------------------------------ patch tests

PATCH_XY = np.array([[0.0, 0.0], [0.24, 0.0], [0.24, 0.12], [0.0, 0.12],
                     [0.04, 0.02], [0.18, 0.03], [0.16, 0.08], [0.08, 0.08]])
PATCH_QUADS = np.array([[0, 1, 5, 4], [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7], [4, 5, 6, 7]])


def patch_tests() -> tuple[list[Record], dict]:
    """Membrane and bending patch tests on the distorted 5-element patch (E = 1e6, nu = 0.25, t = 0.001)."""
    Em, num, t = 1.0e6, 0.25, 0.001
    model = flat_model(PATCH_XY, PATCH_QUADS, t, Em, num)
    bnd = np.arange(4)
    fixed = dofs_of(bnd)
    D = element.plane_stress_matrix(np.array([Em]), np.array([num]))[0]
    out, detail = [], {}

    def field_values(f):
        return np.concatenate([f(*PATCH_XY[i]) for i in bnd])

    # membrane: u = 1e-3 (x + y/2), v = 1e-3 (y + x/2)
    U = _prescribed(model, fixed, field_values(lambda x, y: [1e-3 * (x + y / 2), 1e-3 * (y + x / 2), 0, 0, 0, 0]))
    s = element_stresses(model, U).in_plane((1.0, 0.0, 0.0), "mid")
    expect = D @ np.array([1e-3, 1e-3, 1e-3])
    detail["membrane"] = s
    for k, name in enumerate(("sxx", "syy", "sxy")):
        worst = s[np.argmax(np.abs(s[:, k] - expect[k])), k]
        out.append(check_record("membrane patch test, distorted 5-element patch", f"{name} (worst element)",
                                expect[k], worst, "MPa", 1e-6, SRC_MH + "; expected D * constant strain"))
    # bending: w = 1e-3 (x^2 + x y + y^2) / 2, rx = dw/dy, ry = -dw/dx
    U = _prescribed(model, fixed, field_values(
        lambda x, y: [0, 0, 1e-3 * (x * x + x * y + y * y) / 2, 1e-3 * (y + x / 2), -1e-3 * (x + y / 2), 0]))
    st = element_stresses(model, U)
    kappa = np.array([-1e-3, -1e-3, -1e-3])  # kx = d ry/dx, ky = -d rx/dy, kxy = d ry/dy - d rx/dx
    expect = D @ kappa * (t / 2)
    detail["bending_top"] = st.in_plane((1.0, 0.0, 0.0), "top")
    for k, name in enumerate(("sxx", "syy", "sxy")):
        s = detail["bending_top"][:, k]
        worst = s[np.argmax(np.abs(s - expect[k]))]
        out.append(check_record("bending patch test, distorted 5-element patch", f"top {name} (worst element)",
                                expect[k], worst, "MPa", 1e-6, SRC_MH + "; expected D * constant curvature * t/2"))
    return out, detail


# ------------------------------------------------------------------ in-plane bending (membrane locking)


def inplane_cantilever(nx: int = 8, ny: int = 2, incompatible: bool = True, L: float = 100.0, h: float = 10.0,
                       t: float = 1.0) -> tuple[float, float]:
    """Plate strip in its own plane, end moment as linear axial tractions. Returns (tip deflection, expected)."""
    xy, q, idx = grid(nx, ny, L, h, y0=-h / 2)
    model = flat_model(xy, q, t, incompatible=incompatible)
    root = idx[0]
    mid = root[np.argmin(np.abs(xy[root, 1]))]
    alln = np.arange(len(xy))
    # in-plane problem: hold w, rx, ry everywhere; root free to contract (only ux, and uy at the middle)
    fixed = np.concatenate([dofs_of(root, "x"), dofs_of(mid, "y"), dofs_of(alln, "z"), 6 * alln + 3, 6 * alln + 4])
    I = t * h**3 / 12.0
    M = 1.0e5
    F = np.zeros(model.n_dof)
    tip = idx[-1]
    for a, b in zip(tip[:-1], tip[1:]):  # linear traction sigma = -M y / I on each segment
        y0, y1 = xy[a, 1], xy[b, 1]
        s0, s1 = -M * y0 / I, -M * y1 / I
        l = abs(y1 - y0)
        F[6 * a] += t * l * (2 * s0 + s1) / 6.0
        F[6 * b] += t * l * (s0 + 2 * s1) / 6.0
    U = StaticSolver(model, np.unique(fixed)).solve(F)
    tip_mid = tip[np.argmin(np.abs(xy[tip, 1]))]
    return float(U[6 * tip_mid + 1]), M * L**2 / (2.0 * E_ST * I)


# ------------------------------------------------------------------ rect tube cantilever


def _tube(size, L, b, h, t, drill_factor):
    design = fixtures.beam_design(L, {"shape": "rect_tube", "dims_mm": {"b": b, "h": h, "t": t}}, STEEL)
    ms = build_mesh(design, size)
    return ms, ShellModel.from_mesh(ms, design, drill_factor)


def tube_cantilever(size: float = 10.0, L: float = 1000.0, b: float = 40.0, h: float = 80.0, t: float = 2.3,
                    P: float = 1000.0, T: float = 1.0e6, drill_factor: float = element.DRILL_FACTOR,
                    torsion: bool = True) -> dict:
    """A b x h x t tube along +x (h vertical).

    Bending (one factorisation, U columns 0 and 1): root ring clamped (all six
    DOFs), tip ring tied rigidly (RBE2) to a node on the axis; case 0 P down
    (-z), case 1 P sideways (+y) at that node.
    Torsion (U_torsion): both ends free to warp, as Bredt's theory assumes.
    Root ring: uy, uz held at every node, ux at the middle of each wall
    (where the warping of a doubly symmetric box is zero). Tip ring: the
    torque as Bredt's uniform shear flow q = T / (2 Am), i.e. q * (segment
    length) along each ring segment, half to each end node.
    """
    ms, model = _tube(size, L, b, h, t, drill_factor)
    tip = model.add_node((L, 0.0, 0.0))
    model.links += [(int(n), tip) for n in ms.end_rings[("m", "b")]]
    solver = StaticSolver(model, dofs_of(ms.end_rings[("m", "a")]))
    F = np.zeros((model.n_dof, 2))
    F[6 * tip + 2, 0] = -P
    F[6 * tip + 1, 1] = P
    U = solver.solve(F)
    out = {"mesh": ms, "model": model, "U": U, "tip": tip, "L": L, "b": b, "h": h, "t": t, "P": P, "T": T,
           "section": sections.rect_tube(b, h, t)}
    if not torsion:
        return out

    ms_t, mt = _tube(size, L, b, h, t, drill_factor)
    root, ring = ms_t.end_rings[("m", "a")], ms_t.end_rings[("m", "b")]
    yz = mt.nodes[root, 1:]
    mid = root[(np.abs(yz[:, 0]) < 1e-6) | (np.abs(yz[:, 1]) < 1e-6)]
    if len(mid) != 4:
        raise ValueError("the torsion check needs a node at the middle of each wall (an even split of each wall)")
    Ft = np.zeros(mt.n_dof)
    q = T / (2.0 * (b - t) * (h - t))
    x = mt.nodes[ring]
    for i in range(len(ring)):  # ring nodes run counter-clockwise about the tube axis (+x)
        j = (i + 1) % len(ring)
        seg = x[j] - x[i]
        for k in (i, j):
            Ft[6 * ring[k]:6 * ring[k] + 3] += q * seg / 2.0
    out["U_torsion"] = StaticSolver(mt, np.concatenate([dofs_of(root, "yz"), dofs_of(mid, "x")])).solve(Ft)
    out["torsion_model"] = mt
    return out


def _ring_twist(model: ShellModel, U: np.ndarray, x: float) -> float:
    """Least-squares rigid rotation about +x of the ring of nodes at axial position x (axis on y = z = 0)."""
    n = model.nodes
    sel = np.nonzero(np.abs(n[:, 0] - x) < 1e-6)[0]
    r = n[sel].copy()
    r[:, 0] = 0.0
    u = np.stack([U[6 * sel + k] for k in range(3)], axis=1)
    return float(np.sum(np.cross(r, u)[:, 0]) / np.sum(r[:, 1] ** 2 + r[:, 2] ** 2))


def _wall_element(ms, s_target: float, y: float | None, z: float | None) -> int:
    """Element of member 'm' nearest station s with section coordinate y (or z) on a wall, centred on it."""
    s, yz = ms.station_s_mm, ms.station_yz_mm
    if y is not None:
        on = np.abs(yz[:, 0] - y) < 1e-6
        score = np.abs(s - s_target) + np.abs(yz[:, 1])
    else:
        on = np.abs(yz[:, 1] - z) < 1e-6
        score = np.abs(s - s_target) + np.abs(yz[:, 0])
    cand = np.nonzero(on)[0]
    return int(cand[np.argmin(score[cand])])


def tube_cantilever_records(res: dict) -> list[Record]:
    ms, model, U, tip = res["mesh"], res["model"], res["U"], res["tip"]
    L, b, h, t, P, T = (res[k] for k in ("L", "b", "h", "t", "P", "T"))
    sec = res["section"]
    case = f"cantilever {b:g}x{h:g}x{t:g} tube, L {L:g} mm, mesh {ms.element_size_mm:g} mm"
    out = []
    # vertical: strong axis (h), shear on the two h walls
    d_exp = P * L**3 / (3 * E_ST * sec["Iz"]) + P * L / (G_ST * 2 * t * (h - t))
    out.append(check_record(case + ", 1 kN down at the tip", "tip_deflection", d_exp, -U[6 * tip + 2, 0], "mm",
                            0.03, SRC_BEAM + "; tip ring tied rigidly to the load node"))
    st = element_stresses(model, U[:, 0])
    e = _wall_element(ms, L / 2, y=(h - t) / 2, z=None)
    s_c = ms.station_s_mm[e]
    out.append(check_record(case + ", 1 kN down at the tip", f"top wall outer axial stress at s = {s_c:.1f} mm",
                            P * (L - s_c) * (h / 2) / sec["Iz"], st.in_plane((1.0, 0.0, 0.0), "top")[e, 0], "MPa",
                            0.03, "M c / I with M = P (L - s), c = h/2 (outer surface), I from sections.rect_tube; "
                                  "element at the middle of the top wall"))
    # sideways (+y): weak axis (b); local z of the member is -y, so the +y side is local z = -(b-t)/2
    d_exp = P * L**3 / (3 * E_ST * sec["Iy"]) + P * L / (G_ST * 2 * t * (b - t))
    out.append(check_record(case + ", 1 kN sideways (+y) at the tip", "tip_deflection", d_exp, U[6 * tip + 1, 1],
                            "mm", 0.03, SRC_BEAM + "; weak axis"))
    st = element_stresses(model, U[:, 1])
    e = _wall_element(ms, L / 2, y=None, z=(b - t) / 2)  # the -y side wall: tension
    s_c = ms.station_s_mm[e]
    out.append(check_record(case + ", 1 kN sideways (+y) at the tip", f"-y wall outer axial stress at s = {s_c:.1f} mm",
                            P * (L - s_c) * (b / 2) / sec["Iy"], st.in_plane((1.0, 0.0, 0.0), "top")[e, 0], "MPa",
                            0.03, "M c / I with c = b/2, I = Iy from sections.rect_tube"))
    if "U_torsion" not in res:
        return out
    # torsion (free warping at both ends)
    mt, Ut = res["torsion_model"], res["U_torsion"]
    x0, x1 = 0.3 * L, 0.7 * L
    twist = _ring_twist(mt, Ut, x1) - _ring_twist(mt, Ut, x0)
    src_t = SRC_BREDT + "; torque applied as the uniform shear flow on the tip ring, both ends free to warp"
    j_open = 2.0 * ((b - t) + (h - t)) * t**3 / 3.0  # the walls' own (open-section) torsion, which the shell has
    out.append(check_record(case + f", {T / 1e6:g} kN*m torque at the tip", f"twist between {x0:g} and {x1:g} mm",
                            T * (x1 - x0) / (G_ST * (sec["J"] + j_open)), twist, "rad", 0.005,
                            src_t + "; twist = rate * length; J from sections.rect_tube plus the walls' own "
                                    "torsion as plates, mid-line perimeter x t^3 / 3"))
    st = element_stresses(mt, Ut)
    Am = (b - t) * (h - t)
    for label, e in (("h wall", _wall_element(ms, L / 2, y=None, z=(b - t) / 2)),
                     ("b wall", _wall_element(ms, L / 2, y=(h - t) / 2, z=None))):
        tau = abs(st.in_plane((1.0, 0.0, 0.0), "mid")[e, 2])
        out.append(check_record(case + f", {T / 1e6:g} kN*m torque at the tip",
                                f"shear stress, middle of the {label} at s = {ms.station_s_mm[e]:.1f} mm",
                                T / (2 * Am * t), tau, "MPa", 0.03, src_t))
    return out


# ------------------------------------------------------------------ simply supported plate


def navier_centre_deflection(a: float, q: float, D: float, terms: int = 400) -> float:
    """Centre deflection of a simply supported square plate (side a) under pressure q, Navier series."""
    m = np.arange(1, terms, 2, dtype=float)
    M, N = np.meshgrid(m, m, indexing="ij")
    sign = np.sin(M * np.pi / 2) * np.sin(N * np.pi / 2)
    s = np.sum(sign / (M * N * (M**2 / a**2 + N**2 / a**2) ** 2))
    return 16.0 * q / (np.pi**6 * D) * s


def square_plate(n: int, a: float = 100.0, t: float = 1.0, q: float = 0.01) -> tuple[float, float]:
    """n x n mesh, hard simple support (w and tangential rotation held). Returns (centre w, Navier w)."""
    xy, quads, idx = grid(n, n, a, a)
    model = flat_model(xy, quads, t)
    allnodes = np.arange(len(xy))
    fixed = [dofs_of(allnodes, "xy"), 6 * allnodes + 5]
    xe = np.concatenate([idx[0], idx[-1]])  # x = 0, a: tangent y, hold w and rx (= dw/dy)
    ye = np.concatenate([idx[:, 0], idx[:, -1]])  # y = 0, a: hold w and ry
    fixed += [dofs_of(xe, "z"), 6 * xe + 3, dofs_of(ye, "z"), 6 * ye + 4]
    F = np.zeros(model.n_dof)
    area = (a / n) ** 2
    for e in quads:
        F[6 * e + 2] -= q * area / 4.0
    U = StaticSolver(model, np.unique(np.concatenate(fixed))).solve(F)
    c = idx[n // 2, n // 2]
    D = E_ST * t**3 / (12.0 * (1.0 - NU_ST**2))
    return -float(U[6 * c + 2]), navier_centre_deflection(a, q, D)


# ------------------------------------------------------------------ plate with a hole


def holed_plate_mesh(W: float, d: float, length: float, n_quarter: int, n_radial: int, grading: float = 1.15):
    """Quarter of a plate (x in [0, W/2], y in [0, length/2]) with a central hole of diameter d.

    A square block [0, W/2]^2 around the hole is mapped from the hole arc to
    the block edges (2 n_quarter segments over 90 degrees, n_radial rings
    growing geometrically by `grading`), and a structured strip above it
    reaches y = length/2. Returns xy (N, 2), quads (E, 4), the hole-ring
    element indices of the first angular column (from the x axis outwards).
    """
    S, a = W / 2.0, d / 2.0
    nq = 2 * n_quarter
    phi = 0.5 * np.pi * np.arange(nq + 1) / nq
    hole = np.stack([a * np.cos(phi), a * np.sin(phi)], axis=1)
    outer = []
    for j in range(nq + 1):  # x = S from y 0 to S, then y = S from x S to 0
        if j <= n_quarter:
            outer.append((S, S * j / n_quarter))
        else:
            outer.append((S * (1 - (j - n_quarter) / n_quarter), S))
    outer = np.array(outer)
    g = grading ** np.arange(n_radial + 1)
    f = (g - 1.0) / (g[-1] - 1.0)
    P = hole[None, :, :] + f[:, None, None] * (outer - hole)[None, :, :]  # (radial, angular, 2)
    idx = np.arange(P.shape[0] * P.shape[1]).reshape(P.shape[:2])
    xy = [P.reshape(-1, 2)]
    quads = [np.stack([idx[:-1, :-1], idx[1:, :-1], idx[1:, 1:], idx[:-1, 1:]], axis=-1).reshape(-1, 4)]
    first_col = np.arange(n_radial) * nq  # elements of angular column 0, rings 0..n_radial-1
    # strip above: bottom row = the block's top edge (angular indices n_quarter..nq at the outer ring)
    top = idx[-1, n_quarter:][::-1]  # x from 0 to S
    ny = max(1, math.ceil((length / 2.0 - S) / (S / n_quarter)))
    ys = np.linspace(S, length / 2.0, ny + 1)[1:]
    xs = np.linspace(0.0, S, n_quarter + 1)
    base = len(xy[0])
    new = base + np.arange(ny * (n_quarter + 1)).reshape(ny, n_quarter + 1)
    X, Y = np.meshgrid(xs, ys)
    xy.append(np.stack([X.ravel(), Y.ravel()], axis=1))
    rows = np.vstack([top[None, :], new])
    quads.append(np.stack([rows[:-1, :-1], rows[:-1, 1:], rows[1:, 1:], rows[1:, :-1]], axis=-1).reshape(-1, 4))
    xy = np.vstack(xy)
    quads = np.vstack(quads)
    # orient counter-clockwise (positive area seen from +z)
    p = xy[quads]
    area2 = np.sum(p[:, :, 0] * np.roll(p[:, :, 1], -1, axis=1) - np.roll(p[:, :, 0], -1, axis=1) * p[:, :, 1], axis=1)
    quads[area2 < 0] = quads[area2 < 0][:, ::-1]
    return xy, quads, first_col


def holed_plate_kt(W: float = 100.0, d: float = 25.0, length: float = 300.0, n_quarter: int = 12,
                   n_radial: int = 14, grading: float = 1.2, t: float = 1.0, sigma: float = 100.0) -> dict:
    """Peak sigma_yy at the hole edge (on the x axis) under gross tension sigma along y."""
    xy, quads, col = holed_plate_mesh(W, d, length, n_quarter, n_radial, grading)
    model = flat_model(xy, quads, t)
    alln = np.arange(len(xy))
    fixed = [dofs_of(alln, "z"), 6 * alln + 3, 6 * alln + 4,
             dofs_of(np.nonzero(np.abs(xy[:, 0]) < 1e-9)[0], "x"), dofs_of(np.nonzero(np.abs(xy[:, 1]) < 1e-9)[0], "y")]
    F = np.zeros(model.n_dof)
    edge = np.nonzero(np.abs(xy[:, 1] - length / 2.0) < 1e-9)[0]
    edge = edge[np.argsort(xy[edge, 0])]
    for p, q in zip(edge[:-1], edge[1:]):
        l = xy[q, 0] - xy[p, 0]
        F[6 * p + 1] += sigma * t * l / 2.0
        F[6 * q + 1] += sigma * t * l / 2.0
    U = StaticSolver(model, np.unique(np.concatenate(fixed))).solve(F)
    st = element_stresses(model, U)
    syy = st.in_plane((0.0, 1.0, 0.0), "mid")[:, 0]
    c = st.centres[col[:2], :2]
    r = np.linalg.norm(c, axis=1)
    s = syy[col[:2]]
    peak = s[0] + (s[1] - s[0]) * (d / 2.0 - r[0]) / (r[1] - r[0])
    ratio = d / W
    kt_net = 2.0 + (1.0 - ratio) ** 3
    return {"kt": peak / sigma, "kt_heywood_gross": kt_net / (1.0 - ratio), "first_centre_kt": s[0] / sigma,
            "n_elements": len(quads), "r_centres": r}


# ------------------------------------------------------------------ the ladder frame (frame_shell)


def _top_wall_element(ms, member: str, s_target: float, h: float, t: float) -> int:
    """Element of `member` at the middle of its upper wall (local y = (h - t)/2) nearest station s."""
    sel = np.array([m == member for m in ms.element_member])
    yz, s = ms.station_yz_mm, ms.station_s_mm
    cand = np.nonzero(sel & (np.abs(yz[:, 0] - (h - t) / 2.0) < 1e-6))[0]
    return int(cand[np.argmin(np.abs(s[cand] - s_target) + np.abs(yz[cand, 1]))])


def ladder_frame_checks(element_size_mm: float = 20.0) -> list[Record]:
    """fixtures.ladder_design() on the shell frame solver (k1_shell.frame): at rest, and braking at 0.5 g."""
    from .frame import ShellFrame, analyze_shell

    lad = fixtures.ladder_design()
    fr = ShellFrame.build(lad, element_size_mm)
    res = analyze_shell(lad, frame=fr)
    case = f"symmetric ladder at rest on four rigid wheels, shell mesh {element_size_mm:g} mm"
    out = []
    W = res["mass"]["mass_kg"] * STANDARD_GRAVITY
    total = float(sum(g["force_N"][2] for g in res["ground"].values()))
    out.append(check_record(case, "total_wheel_load", W, total, "N", 1e-9,
                            "sum of the ground forces (K0 rigid-body statics) equals the weight"))
    out.append(check_record(case, "hold_reaction", 0.0, res["residual_ratio"], "1", 1e-6,
                            "loads balanced by inertia relief: the six holding restraints carry nothing "
                            "(reaction / weight)"))
    # rail L ahead of the middle cross member, 500 mm behind the front joint: beam bending M c / I
    sec = lad["sections"]["rt"]["dims_mm"]
    b, h, t = sec["b"], sec["h"], sec["t"]
    tube = sections.rect_tube(b, h, t)
    ms = res["mesh"]
    e = _top_wall_element(ms, "railLa", 500.0, h, t)
    s = float(ms.station_s_mm[e])
    rho_g = STEEL["density_kg_m3"] * 1e-9 * STANDARD_GRAVITY  # N/mm^3
    W_FL = float(res["ground"]["FL"]["force_N"][2])
    half_cross = rho_g * tube["A"] * 800.0 / 2.0
    M = (W_FL - half_cross) * s - rho_g * tube["A"] * s * s / 2.0
    got = float(res["stress"].in_plane((1.0, 0.0, 0.0), "top")[e, 0])
    out.append(check_record(case, f"rail top wall outer axial stress at {s:.0f} mm behind the front joint",
                            -M * (h / 2.0) / tube["Iz"], got, "MPa", 0.03,
                            "beam bending -M c / I, M = (W_FL - half the front cross member's weight) s - w s^2 / 2 "
                            "(free body of the rail ahead of the cut; symmetric, so the cross member puts no "
                            "vertical moment into the rail), c = h/2, I from sections.rect_tube; W_FL from the "
                            "K0 ground forces; element next to the middle of the upper wall (normal stress from the "
                            "restrained twist of the rail, which the formula leaves out, is not zero there)"))
    # braking: each wheel's grip 0.5 x its load backwards; the pitch is left to the angular acceleration
    m = res["mass"]["mass_kg"]
    gf = {sid: [("reaction", g["point_mm"], g["force_N"]),
                ("grip", g["point_mm"], np.array([-0.5 * g["force_N"][2], 0.0, 0.0]))]
          for sid, g in res["ground"].items()}
    brk = analyze_shell(lad, ground_forces=gf, inertia_N=np.array([0.5 * m * STANDARD_GRAVITY, 0.0, 0.0]), frame=fr)
    case = f"symmetric ladder braking, grip 0.5 x each wheel load, shell mesh {element_size_mm:g} mm"
    out.append(check_record(case, "inertia_acceleration", 0.5 * STANDARD_GRAVITY,
                            float(np.linalg.norm(brk["inertia_accel"])), "m/s^2", 0.01,
                            "d'Alembert: a = (ground forces + weight) / m = 0.5 g; within 1 % because the shell's "
                            "tube mass (rho t area) differs slightly from the beam bookkeeping the forces were made with"))
    out.append(check_record(case, "hold_reaction", 0.0, brk["residual_ratio"], "1", 1e-6,
                            "inertia relief with an angular acceleration (the grip at the ground pitches the "
                            "vehicle): the six holding restraints carry nothing"))
    return out


# ------------------------------------------------------------------ the routine set


def shell_known_answers() -> list[Record]:
    """Checks a shell tool runs every time (about 4 s)."""
    out, _ = patch_tests()
    got, exp = inplane_cantilever(8, 2)
    out.append(check_record("in-plane cantilever strip 100 x 10 x 1, 8 x 2 elements, end moment", "tip_deflection",
                            exp, got, "mm", 1e-6, "pure bending M L^2 / (2 E I) (exact plane-stress solution); "
                                                  "incompatible membrane modes reproduce it on rectangles"))
    res = tube_cantilever(size=10.0)
    out += tube_cantilever_records(res)
    soft = tube_cantilever(size=10.0, drill_factor=element.DRILL_FACTOR * 1e-3)
    out.append(check_record("cantilever 40x80x2.3 tube, 1 kN down: drilling stiffness 1e-3 vs 1e-6",
                            "tip_deflection", -soft["U"][6 * soft["tip"] + 2, 0], -res["U"][6 * res["tip"] + 2, 0],
                            "mm", 1e-3, "the fictitious drilling stiffness must not change the answer"))
    got, exp = square_plate(16)
    out.append(check_record("simply supported square plate 100 x 100 x 1, uniform 0.01 MPa, 16 x 16", "centre_deflection",
                            exp, got, "mm", 0.01, SRC_NAVIER))
    kt = holed_plate_kt()
    out.append(check_record("plate W 100 x 300, central hole d 25 (d/W 0.25), tension 100 MPa", "Kt_gross",
                            kt["kt_heywood_gross"], kt["kt"], "1", 0.03,
                            SRC_HEYWOOD + "; FE value: element-centre sigma_yy of the two rings next to the hole "
                                          "on the x axis, extrapolated linearly in r to the edge"))
    out += ladder_frame_checks()
    return out

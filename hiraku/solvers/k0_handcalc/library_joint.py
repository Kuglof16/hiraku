"""K0: the side wall of a rect tube bent as a plate where a cross member ends on it (frame_static.joint_wall_bending).

The cross member's end moment about the rail's axis reaches the rail's side
wall as the normal stresses of its four walls: line loads along the cross
member's wall mid-lines (thin-walled bending, sigma = M z / I). The rail's
side wall between its upper and lower walls is a Kirchhoff plate strip of
width a = h - t (the mid-surface height), simply supported along those two
walls and running on along the rail. Levy's method: sine series across the
strip, and along it for each harmonic the infinite-strip Green's function
(1 + beta |x|) exp(-beta |x|) / (4 D beta^3) integrated over the line loads in
closed form. Surface stress 6 m / t^2 from the plate moments; the peak is the
largest von Mises of the surface stresses over a grid around the joint. The
wall's rotation under the moment (work of the line loads on the deflection
over M^2) gives the joint's rotation stiffness, which frame_inputs uses to
share the wheels' twisting moments among the cross members.

Gussets (leg > 0) spread the cross member's upper and lower wall loads along
the rail over b_c - t_c + 2 leg (uniformly; the gusset's own stiffness is not
counted). A cross member through the rail (through = 1) is welded to both
side walls: its moment is taken in the plane of those walls as vertical shear
along the two side walls of the cross member, V = M / (b - t), so the joint
is rigid and no plate bending is counted there.

A rail that ends at the joint (open_end = 1, an L joint at the frame's end)
is computed as if the wall ran on past the joint (the cross member's end
wall closes the rail's end over the joint); sigma_open_end gives the same
loads on a wall with a free edge at the outer face of the cross member, as a
bound.
"""

from __future__ import annotations

import math

import numpy as np

from .registry import Formula

N_TERMS = 240  # harmonics across the strip
N_X = 161  # points along the rail where the stresses are read
N_Z = 49  # points across the strip (plus the cross member's wall lines)
N_LINE = 40  # points along each line load for the rotation (work) integral


def _tube_I(B: float, H: float, t: float) -> float:
    """Thin-walled second moment of a rect tube of mid-surface sizes B (across the bending plane) and H."""
    return t * (H**3 / 6.0 + B * H**2 / 2.0)


def _g(k: int, r: np.ndarray, b: np.ndarray) -> np.ndarray:
    """k-th x derivative (k = -1: x integral) of (1 + b|r|) exp(-b|r|); r (N,P), b (N,1)."""
    ar = np.abs(r)
    e = np.exp(-b * ar)
    s = np.sign(r)
    if k == -1:
        return s * (2.0 - (2.0 + b * ar) * e) / b
    if k == 0:
        return (1.0 + b * ar) * e
    if k == 1:
        return -b * b * r * e
    if k == 2:
        return b * b * (b * ar - 1.0) * e
    if k == 3:
        return b**3 * (2.0 - b * ar) * e * s
    raise ValueError(k)


class Strip:
    """A plate strip of width a (thickness t) along x, simply supported at z = 0 and z = a, infinite both ways
    or (free_edge = x_e) ending at x = x_e with a free edge (the plate on x >= x_e), under line loads:
    flanges [(x1, x2, z, q)] (q N/mm along x from x1 to x2 at height z) and webs [(x, z1, z2, q1, q2)] (along z
    at x, q N/mm varying linearly from q1 at z1 to q2 at z2). Levy series of n_terms sine harmonics in z."""

    def __init__(self, a: float, t: float, E: float, nu: float, flanges, webs, free_edge: float | None = None,
                 n_terms: int = N_TERMS):
        self.a, self.t, self.nu = a, t, nu
        self.D = E * t**3 / (12.0 * (1.0 - nu**2))
        self.b = np.arange(1, n_terms + 1, dtype=float)[:, None] * math.pi / a  # (N,1)
        b = self.b
        self.flanges = [(x1, x2, (2.0 / a) * q * np.sin(b * z)) for x1, x2, z, q in flanges]
        self.webs = []
        for x, z1, z2, q1, q2 in webs:
            B = (q2 - q1) / (z2 - z1)
            A = q1 - B * z1
            F = lambda z: -(A + B * z) * np.cos(b * z) / b + B * np.sin(b * z) / b**2  # noqa: E731
            self.webs.append((x, (2.0 / a) * (F(z2) - F(z1))))
        self.c = 1.0 / (4.0 * self.D * b**3)
        self.free_edge = free_edge
        if free_edge is not None:
            e0 = np.array([free_edge])
            bb = b[:, 0]
            R1 = self._along(e0, 2)[:, 0] - nu * bb**2 * self._along(e0, 0)[:, 0]
            R2 = self._along(e0, 3)[:, 0] - (2.0 - nu) * bb**2 * self._along(e0, 1)[:, 0]
            # (A + B u) exp(-b u), u = x - x_e, cancels m_x and the Kirchhoff shear of the loads at the edge
            det = bb**4 * (1 - nu) * (1 + nu) + 2 * bb**4 * (1 - nu)
            self.A = ((-R1 * bb**2 * (1 + nu) - 2 * bb * R2) / det)[:, None]
            self.B = ((-bb**2 * (1 - nu) * R2 + bb**3 * (1 - nu) * R1) / det)[:, None]

    def _along(self, x, k: int) -> np.ndarray:
        """k-th x derivative of each harmonic's deflection (N,P) from the loads alone."""
        x = np.asarray(x, float)[None, :]
        b, c = self.b, self.c
        out = np.zeros((len(b), x.shape[1]))
        for x1, x2, P in self.flanges:
            out += c * P * (_g(k - 1, x - x1, b) - _g(k - 1, x - x2, b))
        for x0, P in self.webs:
            out += c * P * _g(k, x - x0, b)
        return out

    def harmonics(self, x) -> list[np.ndarray]:
        """w_n, w_n', w_n'' (N,P) at x."""
        w = [self._along(x, k) for k in range(3)]
        if self.free_edge is not None:
            b, A, B = self.b, self.A, self.B
            u = np.asarray(x, float)[None, :] - self.free_edge
            e = np.exp(-b * u)
            w[0] = w[0] + (A + B * u) * e
            w[1] = w[1] + (B - b * (A + B * u)) * e
            w[2] = w[2] + (-2 * b * B + b * b * (A + B * u)) * e
        return w

    def moments(self, xs, zs) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Plate moments m_x, m_z, m_xz (N*mm/mm) on the grid (len(xs), len(zs))."""
        b, D, nu = self.b, self.D, self.nu
        w0, w1, w2 = self.harmonics(xs)
        sn, cs = np.sin(b * np.asarray(zs)[None, :]), np.cos(b * np.asarray(zs)[None, :])
        mx = -D * ((w2 - nu * b**2 * w0).T @ sn)
        mz = -D * ((-(b**2) * w0 + nu * w2).T @ sn)
        mxz = -D * (1.0 - nu) * ((b * w1).T @ cs)
        return mx, mz, mxz

    def deflection(self, xs, zs) -> np.ndarray:
        return self.harmonics(xs)[0].T @ np.sin(self.b * np.asarray(zs)[None, :])

    def surface_von_mises(self, xs, zs) -> np.ndarray:
        mx, mz, mxz = self.moments(xs, zs)
        k6 = 6.0 / self.t**2
        sx, sz, txz = k6 * mx, k6 * mz, k6 * mxz
        return np.sqrt(np.maximum(sx**2 - sx * sz + sz**2 + 3.0 * txz**2, 0.0))


def ring_plate(a: float, t: float, E: float, nu: float, Bp: float, Hp: float, tc: float, M: float,
               flange_len: float | None = None, free_edge: bool = False, n_terms: int = N_TERMS) -> dict:
    """The rail's side wall (Strip of width a) under the end of a cross member of mid-surface sizes Bp (along
    the rail) x Hp (across), wall tc, carrying moment M (N*mm) about the rail's axis; the end centred at x = 0,
    z = a/2. flange_len: length along the rail over which the upper and lower walls' loads are spread
    (default Bp). free_edge: the wall ends at x = -Bp/2.

    Returns sigma (largest surface von Mises, MPa), its place (x, z - a/2, mm), and theta (rotation, rad)."""
    I = _tube_I(Bp, Hp, tc)
    zc = a / 2.0
    zf = (zc + Hp / 2.0, zc - Hp / 2.0)
    L = flange_len or Bp
    qf = M * (Hp / 2.0) * tc / I * Bp / L  # N/mm on each flange line (+ upper, - lower)
    qw = M * (Hp / 2.0) * tc / I  # web line load at the flanges (linear through 0 at the centre)
    xw = (-Bp / 2.0, Bp / 2.0)
    flanges = [(-L / 2.0, L / 2.0, zf[0], qf), (-L / 2.0, L / 2.0, zf[1], -qf)]
    webs = [(x, zf[1], zf[0], -qw, qw) for x in xw]
    x_edge = -Bp / 2.0
    plate = Strip(a, t, E, nu, flanges, webs, x_edge if free_edge else None, n_terms)
    span = L / 2.0 + a
    xs = np.unique(np.concatenate([np.linspace(x_edge if free_edge else -span, span, N_X), [-L / 2, L / 2, *xw]]))
    zs = np.unique(np.concatenate([np.linspace(0.0, a, N_Z), zf]))
    vm = plate.surface_von_mises(xs, zs)
    i, j = np.unravel_index(int(np.argmax(vm)), vm.shape)
    # rotation: work of the line loads on the deflection (midpoint rule along each line), over M
    u = (np.arange(N_LINE) + 0.5) / N_LINE
    xf = -L / 2.0 + L * u
    work = sum(q * L / N_LINE * float(np.sum(plate.deflection(xf, [z]))) for q, z in ((qf, zf[0]), (-qf, zf[1])))
    zw = zf[1] + Hp * u
    for x0 in xw:
        work += float(np.sum(qw * (zw - zc) / (Hp / 2.0) * Hp / N_LINE * plate.deflection([x0], zw)[0]))
    return {"sigma": float(vm[i, j]), "x": float(xs[i]), "z": float(zs[j] - zc), "theta": work / M}


def _joint(v: dict) -> dict:
    a, t = v["h"] - v["t"], v["t"]
    Bp, Hp, tc = v["b_c"] - v["t_c"], v["h_c"] - v["t_c"], v["t_c"]
    M = v["M"]
    if v["through"] >= 0.5:
        tau = M / (v["b"] - v["t"]) / (2.0 * Hp) / t
        s = math.sqrt(3.0) * tau
        return {"sigma": s, "sigma_open_end": s, "theta_per_kNm": 0.0, "M_flange_force": M / Hp, "x_peak": 0.0, "z_peak": 0.0}
    fl = Bp + 2.0 * v["leg"] if v["leg"] > 0 else None
    M_unit = 1.0e6  # the plate is linear: solve for 1 kN*m, scale
    r = ring_plate(a, t, v["E"], v["nu"], Bp, Hp, tc, M_unit, fl)
    out = {"sigma": r["sigma"] * M / M_unit, "theta_per_kNm": r["theta"], "M_flange_force": M / Hp,
           "x_peak": r["x"], "z_peak": r["z"]}
    if v["open_end"] >= 0.5:
        out["sigma_open_end"] = ring_plate(a, t, v["E"], v["nu"], Bp, Hp, tc, M_unit, fl, free_edge=True)["sigma"] * M / M_unit
    else:
        out["sigma_open_end"] = out["sigma"]
    return out


def joint_wall(v: dict) -> dict:
    """The formula's numbers for a dict of plain values (mm, N*mm, MPa)."""
    return _joint(v)


def _joint_wall_bending() -> Formula:
    mm = lambda d: {"unit": "mm", "description": d, "min": 0}  # noqa: E731
    return Formula(
        formula_id="frame_static.joint_wall_bending",
        title="横の部材の端のモーメントで、レールの側の壁が板として曲がる応力 (つなぎ目の壁の曲げ)",
        domain="strength",
        expression="line loads of the cross member's walls q = M z t_c / I_c (I_c = t_c (H^3/6 + B H^2/2), B = b_c - t_c, "
                   "H = h_c - t_c) on a plate strip a = h - t wide, simply supported at the rail's upper and lower walls "
                   "(Levy series, D = E t^3/(12 (1 - nu^2))); sigma = max von Mises of 6 m/t^2; theta_per_kNm = work / M at M = 1 kN*m; "
                   "gussets: upper and lower loads over B + 2 leg; through: sigma = sqrt(3) M / ((b - t) 2 H t), rigid",
        inputs={
            "M": {"unit": "N*mm", "description": "the cross member's end moment at the joint about the rail's axis "
                                                 "(frame_inputs.joint_wall_inputs shares the wheels' twisting moments "
                                                 "among the cross members)", "min": 0},
            "b": mm("rail tube width (across the wall's plane)"), "h": mm("rail tube height"), "t": mm("rail wall"),
            "b_c": mm("cross member width (along the rail)"), "h_c": mm("cross member height"),
            "t_c": mm("cross member wall"),
            "E": {"unit": "MPa", "description": "Young's modulus", "min": 0},
            "nu": {"unit": "1", "description": "Poisson's ratio", "min": 0, "max": 0.5},
            "leg": mm("gusset leg along the rail (0: no gussets)"),
            "through": {"unit": "1", "description": "1: the cross member runs through the rail to its far wall", "min": 0,
                        "max": 1},
            "open_end": {"unit": "1", "description": "1: the rail ends at this joint (L joint)", "min": 0, "max": 1},
        },
        outputs={
            "sigma": ("MPa", "largest surface von Mises in the rail's side wall from its plate bending (through: from "
                             "the in-plane shear)", None),
            "sigma_open_end": ("MPa", "the same with a free edge where the rail ends (L joints; a bound, see the "
                                      "assumptions)", None),
            "theta_per_kNm": ("rad", "rotation of the joint for 1 kN*m from the wall's bending (0: rigid); the joint's "
                                     "rotation stiffness is 1e6 N*mm / theta_per_kNm", None),
            "M_flange_force": ("N", "force in the cross member's upper (lower) wall line if it carried all of M", None),
            "x_peak": ("mm", "where sigma is, along the rail from the cross member's centre line", None),
            "z_peak": ("mm", "where sigma is, up from the middle of the rail's side wall (signed)", None),
        },
        assumptions=[
            "Kirchhoff plate, linear elastic; the rail's side wall simply supported along its upper and lower walls "
            "(their rotational restraint is not counted) and running on along the rail both ways",
            "the cross member's end loads the wall as line loads of thin-walled beam bending (force-controlled; the "
            "end ring's own stiffness, which would draw load to its corners, is not counted)",
            "only the moment about the rail's axis (the cross member's vertical bending); its axial force, sideways "
            "bending and torsion are left out",
            "membrane stress in the wall is left out (wall bending only)",
            "L joint (open_end = 1): the cross member's end wall is taken to close the rail's end over the joint, so "
            "the wall runs on; sigma_open_end with a free edge there is a bound, not the estimate",
            "gussets: the upper and lower wall loads spread evenly over the gussets' welds along the rail; the plates' "
            "own stiffness is not counted",
            "through: the moment goes into both side walls as in-plane shear along the cross member's side walls; the "
            "joint is rigid",
            "against the K1 shell's hot spots (R1 step, 10 mm): butt and gusset ladders -4 to -13 %, but at the rail-end "
            "L joints by two errors that cancel (the chain gives them too much moment, the closed-end wall too little "
            "stress per moment); with through cross members the rail-end joint comes out 23 to 41 % low. Not counted: "
            "the rail's section distortion at its open end (a beam on an elastic foundation in the shell's numbers) and "
            "how the wheel mount puts its load onto the wall",
        ],
        source="Kirchhoff plate theory, Levy's single-series solution of a strip simply supported on two edges, and "
               "thin-walled beam bending (textbook plates and shells, e.g. Timoshenko and Woinowsky-Krieger, Theory of "
               "Plates and Shells; the series is evaluated by code here, no table values are used)",
        requires=[(lambda v: 2 * v["t"] < min(v["b"], v["h"]) and 2 * v["t_c"] < min(v["b_c"], v["h_c"]),
                   "wall thickness must be less than half of the tube width and height"),
                  (lambda v: v["h_c"] - v["t_c"] < v["h"] - v["t"],
                   "the cross member must be lower than the rail (h_c - t_c < h - t) for its end to sit on the side wall")],
        fn=_joint,
    )


def all_formulas() -> list[Formula]:
    return [_joint_wall_bending()]

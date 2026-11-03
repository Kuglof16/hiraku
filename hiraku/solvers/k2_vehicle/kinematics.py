"""K&C bench: double-wishbone kinematics solved exactly, its compliance, and the wheel path tables.

The bench moves the lower arm through its range, closes the loop for the
upper arm (ball-joint distance) and the tie rod (tie-rod length), and records
the wheel centre, camber, toe and contact point against wheel travel (the "K"
of K&C). At every travel it also solves the small elastic motion of the arms
and the knuckle on their bushings under a unit force at the contact point and a
unit aligning moment (the "C"): the toe and camber change per force. The
vehicle model runs on these tables, kinematics and compliance.

Conventions (body frame, ISO 8855: x forward, y left, z up). Tables are
written for a LEFT wheel; a right wheel uses the mirror image (y -> -y).
camber: top of the wheel outward is positive. toe: toe-in is positive.
travel: wheel-centre vertical motion relative to the body, bump positive.
Compliance forces act at the contact point in body axes (Fx forward, Fy to
the left), the aligning moment Mz about the body z axis.
"""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy.optimize import brentq, minimize

POLY_DEGREE = 5


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v)


def rotate_about(point: np.ndarray, origin: np.ndarray, axis: np.ndarray, angle: float) -> np.ndarray:
    """Rodrigues rotation of `point` about the line (origin, axis)."""
    k = _unit(axis)
    v = point - origin
    c, s = math.cos(angle), math.sin(angle)
    return origin + v * c + np.cross(k, v) * s + k * float(k @ v) * (1.0 - c)


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Smallest rotation matrix taking direction a to direction b."""
    a, b = _unit(a), _unit(b)
    v = np.cross(a, b)
    c = float(a @ b)
    s = float(np.linalg.norm(v))
    if s < 1e-15:
        return np.eye(3)
    k = v / s
    kx = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + s * kx + (1.0 - c) * (kx @ kx)


def axis_rotation(axis: np.ndarray, angle: float) -> np.ndarray:
    k = _unit(axis)
    kx = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + math.sin(angle) * kx + (1.0 - math.cos(angle)) * (kx @ kx)


def camber_toe_left(axle: np.ndarray) -> tuple[float, float]:
    """Camber and toe (rad) of a left wheel from its axle direction (pointing +y)."""
    a = _unit(axle)
    camber = -math.asin(max(-1.0, min(1.0, float(a[2]))))
    toe = math.atan2(float(a[0]), float(a[1]))
    return camber, toe


@dataclass
class Hardpoints:
    """Double-wishbone hardpoints of a LEFT corner, metres, body/design frame."""

    lca_front: np.ndarray
    lca_rear: np.ndarray
    lca_outer: np.ndarray
    uca_front: np.ndarray
    uca_rear: np.ndarray
    uca_outer: np.ndarray
    tie_inner: np.ndarray
    tie_outer: np.ndarray
    wheel_center: np.ndarray
    axle: np.ndarray  # unit vector of the spin axis at design, pointing +y
    radius: float
    damper_lower: np.ndarray | None = None  # on the lower arm
    damper_upper: np.ndarray | None = None  # on the body

    def to_json(self) -> dict:
        out = {k: [float(x) * 1000.0 for x in getattr(self, k)] for k in (
            "lca_front", "lca_rear", "lca_outer", "uca_front", "uca_rear", "uca_outer",
            "tie_inner", "tie_outer", "wheel_center")}
        out["axle"] = [float(x) for x in self.axle]
        out["radius_mm"] = self.radius * 1000.0
        for k in ("damper_lower", "damper_upper"):
            if getattr(self, k) is not None:
                out[k] = [float(x) * 1000.0 for x in getattr(self, k)]
        return out

    @classmethod
    def from_json(cls, d: dict) -> "Hardpoints":
        kw = {k: np.asarray(d[k], dtype=float) / 1000.0 for k in (
            "lca_front", "lca_rear", "lca_outer", "uca_front", "uca_rear", "uca_outer",
            "tie_inner", "tie_outer", "wheel_center")}
        for k in ("damper_lower", "damper_upper"):
            if k in d:
                kw[k] = np.asarray(d[k], dtype=float) / 1000.0
        return cls(axle=_unit(np.asarray(d["axle"], dtype=float)), radius=float(d["radius_mm"]) / 1000.0, **kw)


def generic_sla(wheel_center: np.ndarray, radius: float) -> Hardpoints:
    """A generic short-long-arm layout scaled to the wheel (left side).

    Not taken from any real vehicle: lower arm 0.6 and upper arm 0.42 of the
    lateral distance from the centreline to the ball joints, upper arm inner
    pivots 15 mm lower than the outer joint, tie rod behind the axle, the damper
    from the lower arm up to the body.
    """
    wc = np.asarray(wheel_center, dtype=float)
    x, yw, zw = wc
    r = radius
    lbj = np.array([x, yw - 0.2 * r, zw - 0.40 * r])
    ubj = np.array([x, yw - 0.3 * r, zw + 0.45 * r])
    y_in_l = lbj[1] - 0.6 * lbj[1]
    y_in_u = ubj[1] - 0.42 * ubj[1]
    lf = np.array([x + 0.12, y_in_l, lbj[2]])
    lr = np.array([x - 0.12, y_in_l, lbj[2]])
    uf = np.array([x + 0.10, y_in_u, ubj[2] - 0.015])
    ur = np.array([x - 0.10, y_in_u, ubj[2] - 0.015])
    tro = np.array([x - 0.4 * r, yw - 0.25 * r, zw - 0.05 * r])
    tri = np.array([tro[0], tro[1] - 0.55 * tro[1], tro[2]])
    # damper: on the lower arm at 0.7 of the way from its inner axis to the ball joint, up to the
    # body 0.9 r above the wheel centre and 0.1 r further in
    dl = 0.5 * (lf + lr) + 0.7 * (lbj - 0.5 * (lf + lr))
    du = np.array([dl[0], dl[1] - 0.1 * r, zw + 0.9 * r])
    return Hardpoints(lf, lr, lbj, uf, ur, ubj, tri, tro, wc.copy(), np.array([0.0, 1.0, 0.0]), r, dl, du)


class DoubleWishboneBench:
    """Solve the double-wishbone loop for a given lower-arm rotation."""

    def __init__(self, hp: Hardpoints):
        self.hp = hp
        self.l_axis = hp.lca_rear - hp.lca_front
        self.u_axis = hp.uca_rear - hp.uca_front
        self.l_king = float(np.linalg.norm(hp.uca_outer - hp.lca_outer))
        self.l_tie = float(np.linalg.norm(hp.tie_outer - hp.tie_inner))
        self._phi = 0.0
        self._psi = 0.0

    def _ubj(self, phi: float) -> np.ndarray:
        return rotate_about(self.hp.uca_outer, self.hp.uca_front, self.u_axis, phi)

    def _knuckle(self, lbj: np.ndarray, ubj: np.ndarray, psi: float) -> np.ndarray:
        r0 = rotation_between(self.hp.uca_outer - self.hp.lca_outer, ubj - lbj)
        return axis_rotation(ubj - lbj, psi) @ r0

    @staticmethod
    def _root(f, x0: float, span: float = 0.05) -> float:
        a, b = x0 - span, x0 + span
        fa, fb = f(a), f(b)
        n = 0
        while fa * fb > 0.0:
            span *= 2.0
            a, b = x0 - span, x0 + span
            fa, fb = f(a), f(b)
            n += 1
            if n > 6:
                raise ValueError("double-wishbone loop does not close at this arm angle")
        return brentq(f, a, b, xtol=1e-14, rtol=1e-14, maxiter=200)

    def solve(self, theta: float) -> dict:
        hp = self.hp
        lbj = rotate_about(hp.lca_outer, hp.lca_front, self.l_axis, theta)
        phi = self._root(lambda p: float(np.linalg.norm(self._ubj(p) - lbj)) - self.l_king, self._phi)
        ubj = self._ubj(phi)

        def tie_err(psi: float) -> float:
            rk = self._knuckle(lbj, ubj, psi)
            tro = lbj + rk @ (hp.tie_outer - hp.lca_outer)
            return float(np.linalg.norm(tro - hp.tie_inner)) - self.l_tie

        psi = self._root(tie_err, self._psi, 0.02)
        self._phi, self._psi = phi, psi
        rk = self._knuckle(lbj, ubj, psi)
        wc = lbj + rk @ (hp.wheel_center - hp.lca_outer)
        axle = rk @ hp.axle
        camber, toe = camber_toe_left(axle)
        down = np.array([0.0, 0.0, -1.0])
        u = down - float(down @ axle) * axle / float(axle @ axle)
        cp = wc + hp.radius * _unit(u)
        return {"lbj": lbj, "ubj": ubj, "wheel_center": wc, "axle": axle, "camber": camber, "toe": toe,
                "contact": cp, "rotation": rk}


@dataclass(frozen=True)
class Bushings:
    """Stiffness (N/m) of a double-wishbone corner's elastic joints: each arm's two inner bushings
    across the pivot axis (radial) and along it (axial), and the tie rod along its length (rod and
    its inner joint). Ball joints and the arms themselves are taken as stiff."""

    radial: float
    axial: float
    tie_rod: float

    def to_json(self) -> dict:
        return {"radial_N_per_mm": self.radial / 1000.0, "axial_N_per_mm": self.axial / 1000.0,
                "tie_rod_N_per_mm": self.tie_rod / 1000.0}


STIFF = 1e10  # N/m: the ball joints, and the wheel pad that holds the wheel centre's height on the bench

# compliance per unit load at the contact point (body axes), LEFT wheel: rad/N, and rad/(N m) for mz
COMPLIANCE_KEYS = ("toe_per_fx", "camber_per_fx", "toe_per_fy", "camber_per_fy", "toe_per_mz", "camber_per_mz")


def _skew(r: np.ndarray) -> np.ndarray:
    return np.array([[0.0, -r[2], r[1]], [r[2], 0.0, -r[0]], [-r[1], r[0], 0.0]])


def _g(p: np.ndarray, o: np.ndarray) -> np.ndarray:
    """Small displacement of the point p of a body with reference point o: u + theta x (p - o) = G [u; theta]."""
    return np.hstack([np.eye(3), -_skew(p - o)])


def compliance(hp: Hardpoints, sol: dict, bushings: Bushings) -> dict:
    """Toe and camber change (rad) of a LEFT wheel per newton at the contact point (body x and y)
    and per newton metre about body z, at the bench solution `sol` (one travel).

    Three rigid bodies on springs, small displacements: the lower and the upper arm each on its
    two inner bushings (radial and axial about the pivot axis, free to turn about it), both joined
    to the knuckle by stiff ball joints; the knuckle also held by the tie rod (a spring along the
    rod, its inner end fixed: the rack does not move) and at the wheel centre by a stiff vertical
    spring (the wheel pad of a K&C rig keeps the travel). K q = f for the 18 displacements.
    """
    lbj, ubj, wc, cp, rk = sol["lbj"], sol["ubj"], sol["wheel_center"], sol["contact"], sol["rotation"]
    tro = lbj + rk @ (hp.tie_outer - hp.lca_outer)
    refs = (hp.lca_front, hp.uca_front, wc)  # reference points: lower arm, upper arm, knuckle
    kk = np.zeros((18, 18))

    def to_ground(b: int, p: np.ndarray, ks: np.ndarray) -> None:
        g = _g(p, refs[b])
        s = slice(6 * b, 6 * b + 6)
        kk[s, s] += g.T @ ks @ g

    def between(a: int, b: int, p: np.ndarray, ks: np.ndarray) -> None:
        ga, gb = _g(p, refs[a]), _g(p, refs[b])
        sa, sb = slice(6 * a, 6 * a + 6), slice(6 * b, 6 * b + 6)
        kk[sa, sa] += ga.T @ ks @ ga
        kk[sb, sb] += gb.T @ ks @ gb
        kk[sa, sb] -= ga.T @ ks @ gb
        kk[sb, sa] -= gb.T @ ks @ ga

    for b, (pa, pb) in ((0, (hp.lca_front, hp.lca_rear)), (1, (hp.uca_front, hp.uca_rear))):
        e = _unit(pb - pa)
        ks = bushings.radial * (np.eye(3) - np.outer(e, e)) + bushings.axial * np.outer(e, e)
        to_ground(b, pa, ks)
        to_ground(b, pb, ks)
    between(0, 2, lbj, STIFF * np.eye(3))
    between(1, 2, ubj, STIFF * np.eye(3))
    nt = _unit(tro - hp.tie_inner)
    to_ground(2, tro, bushings.tie_rod * np.outer(nt, nt))
    ez = np.array([0.0, 0.0, 1.0])
    to_ground(2, wc, STIFF * np.outer(ez, ez))

    axle = sol["axle"]
    c0, t0 = camber_toe_left(axle)
    gk = _g(cp, wc)
    out = {}
    for name, f6 in (("fx", gk.T @ np.array([1.0, 0.0, 0.0])), ("fy", gk.T @ np.array([0.0, 1.0, 0.0])),
                     ("mz", np.array([0.0, 0.0, 0.0, 0.0, 0.0, 1.0]))):
        f = np.zeros(18)
        f[12:] = f6
        theta = np.linalg.solve(kk, f)[15:18]
        c1, t1 = camber_toe_left(axle + np.cross(theta, axle))
        out[f"toe_per_{name}"] = t1 - t0
        out[f"camber_per_{name}"] = c1 - c0
    return out


MOUNTS = ("lca_front", "lca_rear", "uca_front", "uca_rear", "damper_upper", "tie_inner")


def solve_at_travel(hp: Hardpoints, travel: float) -> dict:
    """The bench solution at a wheel travel (m): march the lower arm from design, then refine."""
    bench = DoubleWishboneBench(hp)
    z0 = float(hp.wheel_center[2])
    arm = float(np.linalg.norm(hp.lca_outer - 0.5 * (hp.lca_front + hp.lca_rear)))
    if travel == 0.0:
        return bench.solve(0.0)
    step = 0.002 / arm
    if (float(bench.solve(step)["wheel_center"][2]) - z0) * travel < 0.0:
        step = -step  # the arm turns the other way for this travel
    bench._phi, bench._psi = 0.0, 0.0
    theta, prev = 0.0, 0.0
    while True:
        prev, theta = theta, theta + step
        dz = float(bench.solve(theta)["wheel_center"][2]) - z0
        if abs(dz) >= abs(travel):
            break
        if abs(theta) > 1.5:
            raise ValueError("the bench does not reach this travel")
    f = lambda t: float(bench.solve(t)["wheel_center"][2]) - z0 - travel
    return bench.solve(brentq(f, min(prev, theta), max(prev, theta), xtol=1e-14, rtol=1e-14))


def mount_forces(hp: Hardpoints, sol: dict, force: np.ndarray, contact: np.ndarray | None = None,
                 moment: np.ndarray | None = None) -> dict:
    """Forces (N, body axes) that a LEFT double wishbone puts on the body at its mounts when the
    road pushes the tyre with `force` (N, body axes) at the contact point (default: the bench's,
    under the wheel), with a further `moment` (N*m, body axes) on the knuckle, at the bench
    solution `sol` (one travel). Returns {mount: force} for MOUNTS, the knuckle's ball joints and
    the damper and tie rod forces (+ tension).

    Rigid links and frictionless joints. The knuckle is in balance under the tyre force, the two
    ball joints and the tie rod (a two-force member, along it); each arm turns freely about its
    inner axis, so the moments about that axis are zero, and the damper (a two-force member from
    the lower arm to the body) holds the lower arm: 6 + 1 + 1 linear equations for the 3 + 3
    ball-joint forces, the tie-rod and the damper force. An arm's two inner joints take the arm's
    remaining force and moment; the share of the force along the axis is not fixed by the balance
    and is split half and half (assumed).
    """
    if hp.damper_lower is None or hp.damper_upper is None:
        raise ValueError("the layout has no damper mounts")
    f = np.asarray(force, dtype=float)
    rk = sol["rotation"]
    lbj, ubj = sol["lbj"], sol["ubj"]
    cp = sol["contact"] if contact is None else np.asarray(contact, dtype=float)
    tro = lbj + rk @ (hp.tie_outer - hp.lca_outer)
    # the lower arm turns with the knuckle's lower ball joint about the lower axis: the damper's
    # lower mount turns with it
    ea, eu = _unit(hp.lca_rear - hp.lca_front), _unit(hp.uca_rear - hp.uca_front)
    rot_l = rotation_between(hp.lca_outer - hp.lca_front - ea * float((hp.lca_outer - hp.lca_front) @ ea),
                             lbj - hp.lca_front - ea * float((lbj - hp.lca_front) @ ea))
    dl = hp.lca_front + rot_l @ (hp.damper_lower - hp.lca_front)
    nt = _unit(hp.tie_inner - tro)       # tie rod force on the knuckle = t nt (t > 0: tension)
    nd = _unit(hp.damper_upper - dl)     # damper force on the lower arm = d nd (d > 0: tension)
    a = np.zeros((8, 8))
    b = np.zeros(8)
    # knuckle forces: F_lbj + F_ubj + t nt + f = 0
    a[0:3, 0:3] = np.eye(3)
    a[0:3, 3:6] = np.eye(3)
    a[0:3, 6] = nt
    b[0:3] = -f
    # knuckle moments about the lower ball joint
    a[3:6, 3:6] = _skew(ubj - lbj)
    a[3:6, 6] = np.cross(tro - lbj, nt)
    b[3:6] = -np.cross(cp - lbj, f)
    if moment is not None:
        b[3:6] -= np.asarray(moment, dtype=float)
    # lower arm about its axis: the knuckle pushes it with -F_lbj at lbj, the damper with d nd at dl
    a[6, 0:3] = -(ea @ _skew(lbj - hp.lca_front))
    a[6, 7] = float(ea @ np.cross(dl - hp.lca_front, nd))
    # upper arm about its axis: -F_ubj at ubj
    a[7, 3:6] = -(eu @ _skew(ubj - hp.uca_front))
    x = np.linalg.solve(a, b)
    f_l, f_u, t, d = x[0:3], x[3:6], float(x[6]), float(x[7])

    def inner(front, rear, e, loads):
        """Forces from the body on an arm's front and rear joints for the loads (force, point) on it."""
        total = sum(fo for fo, _ in loads)
        m = sum(np.cross(p - front, fo) for fo, p in loads)
        span = rear - front
        p_rear = np.cross(-m, span) / float(span @ span)  # its moment about front cancels m (across the axis)
        p_rear = p_rear + e * (-float(total @ e) / 2.0)
        return -total - p_rear, p_rear

    lf_, lr_ = inner(hp.lca_front, hp.lca_rear, ea, [(-f_l, lbj), (d * nd, dl)])
    uf_, ur_ = inner(hp.uca_front, hp.uca_rear, eu, [(-f_u, ubj)])
    # on the body: minus the body's force on each joint; the damper and the tie rod pull the body
    # toward the arm and the knuckle when in tension
    return {"lca_front": -lf_, "lca_rear": -lr_, "uca_front": -uf_, "uca_rear": -ur_,
            "damper_upper": -d * nd, "tie_inner": -t * nt, "ball_joint_lower": f_l, "ball_joint_upper": f_u,
            "tie_rod_tension": t, "damper_tension": d, "damper_lower_point": dl}


def corner_mount_forces(table: dict, side: float, travel: float, force: np.ndarray,
                        contact: np.ndarray | None = None, moment: np.ndarray | None = None) -> dict:
    """mount_forces of a corner on a K&C table (a left-wheel table; side < 0 mirrors it) at a
    travel (m, held inside the table) for the force (N, body axes) at `contact` (design frame, m;
    default under the wheel) and a moment on the knuckle (N*m, body axes): {mount: (point, force)},
    design frame, metres and newtons."""
    hp = Hardpoints.from_json(table["hardpoints_mm"])
    rows = table["rows"]
    lo, hi = rows[0]["travel_mm"] / 1000.0, rows[-1]["travel_mm"] / 1000.0
    flip = np.array([1.0, -1.0 if side < 0 else 1.0, 1.0])
    sol = solve_at_travel(hp, min(max(travel, lo), hi))
    # a moment is an axial vector: mirrored in y it keeps its y part and turns its x and z parts
    r = mount_forces(hp, sol, np.asarray(force, dtype=float) * flip,
                     None if contact is None else np.asarray(contact, dtype=float) * flip,
                     None if moment is None else np.asarray(moment, dtype=float) * flip * flip[1])
    points = {m: getattr(hp, m) for m in MOUNTS}
    return {m: (points[m] * flip, r[m] * flip) for m in MOUNTS}


def front_view_ic(hp: Hardpoints) -> np.ndarray | None:
    """Front-view instant centre (y, z) of the arm lines at design, when the pivot axes are along x."""
    def line(inner_a, inner_b, outer):
        inner = 0.5 * (inner_a + inner_b)
        return np.array([inner[1], inner[2]]), np.array([outer[1], outer[2]])

    p1, p2 = line(hp.lca_front, hp.lca_rear, hp.lca_outer)
    q1, q2 = line(hp.uca_front, hp.uca_rear, hp.uca_outer)
    d1, d2 = p2 - p1, q2 - q1
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-15:
        return None  # parallel arms: the IC is at infinity
    t = ((q1[0] - p1[0]) * d2[1] - (q1[1] - p1[1]) * d2[0]) / den
    return p1 + t * d1


def _sweep(hp: Hardpoints, travel: float, n: int) -> tuple[np.ndarray, list[dict]]:
    """The bench solutions at n travels evenly over +-travel (m), and those travels."""
    bench = DoubleWishboneBench(hp)
    z0 = float(hp.wheel_center[2])
    # Map lower-arm angle to travel: march outward from design in small steps.
    samples = {0.0: bench.solve(0.0)}
    for direction in (+1.0, -1.0):
        bench._phi, bench._psi = 0.0, 0.0
        theta = 0.0
        arm = float(np.linalg.norm(hp.lca_outer - 0.5 * (hp.lca_front + hp.lca_rear)))
        dtheta = direction * (0.002 / arm)
        while True:
            theta += dtheta
            sol = bench.solve(theta)
            samples[theta] = sol
            if abs(sol["wheel_center"][2] - z0) > 1.2 * travel:
                break
    thetas = sorted(samples)
    tz = np.array([samples[t]["wheel_center"][2] - z0 for t in thetas])
    if not np.all(np.diff(tz) * np.sign(tz[-1] - tz[0]) > 0):
        raise ValueError("wheel travel is not monotonic in the lower-arm angle")
    order = np.argsort(tz)
    tz = tz[order]
    th = np.array(thetas)[order]
    grid = np.linspace(-travel, travel, n)
    bench._phi, bench._psi = 0.0, 0.0
    sols = []
    for z in grid:
        theta = float(np.interp(z, tz, th))
        # refine theta so that the travel is exact
        f = lambda t: float(bench.solve(t)["wheel_center"][2] - z0 - z)
        lo, hi = theta - 0.01, theta + 0.01
        theta = brentq(f, lo, hi, xtol=1e-14, rtol=1e-14)
        sols.append(bench.solve(theta))
    return grid, sols


def build_table(hp: Hardpoints, travel: float, n: int = 81, bushings: Bushings | None = None) -> dict:
    """Sweep the bench over +-travel (m) of wheel travel and return a K&C table (mm, deg); with
    bushings, every row also has the compliance (deg per kN at the contact point, deg per kN m
    about z)."""
    grid, sols = _sweep(hp, travel, n)
    rows = []
    wc0 = hp.wheel_center
    cp = np.array([s["contact"] for s in sols])
    dy_dz = np.gradient(cp[:, 1], cp[:, 2])
    for z, s, slope in zip(grid, sols, dy_dz):
        d = s["wheel_center"] - wc0
        row = {
            "travel_mm": float(z) * 1000.0,
            "wheel_center_mm": [float(x) * 1000.0 for x in d],
            "camber_deg": math.degrees(s["camber"]),
            "toe_deg": math.degrees(s["toe"]),
            "contact_mm": [float(x) * 1000.0 for x in s["contact"]],
            # front-view kinematic roll centre from the contact point's motion
            "roll_center_height_mm": float(s["contact"][1] * slope + s["contact"][2]) * 1000.0,
        }
        if bushings is not None:
            for key, v in compliance(hp, s, bushings).items():
                row[f"{key}_{COMPLIANCE_UNIT[key]}"] = math.degrees(v) * 1000.0
        rows.append(row)
    out = {
        "kind": "kc_table",
        "suspension": "double_wishbone",
        "side": "left",
        "hardpoints_mm": hp.to_json(),
        "rows": rows,
    }
    if bushings is not None:
        out["bushings"] = bushings.to_json()
    return out


# the table's unit of each compliance column (per kN at the contact point, per kN m about z)
COMPLIANCE_UNIT = {k: ("deg_per_kNm" if k.endswith("_mz") else "deg_per_kN") for k in COMPLIANCE_KEYS}


def low_bump_steer(hp: Hardpoints, travel: float, n: int = 7) -> Hardpoints:
    """hp with the tie rod's inner joint moved (in y and z) to where the toe follows the travel
    least: the largest |toe - toe at design| at n travels over +-travel is minimised
    (Nelder-Mead from the given point). Aiming at no bump steer is K2's own choice for the
    generic layout (assumed; no source read in this repository).

    The arms' motion does not depend on the tie rod, so it is solved once (at the given
    layout's lower-arm angle for each travel); for a trial inner point only the knuckle's turn
    psi about the kingpin axis is solved, in closed form: with v(psi) the tie rod's outer point
    from the lower ball joint (Rodrigues about the axis u), |lbj + v(psi) - inner|^2 = l^2 is
    A cos psi + B sin psi + C = 0."""
    bench = DoubleWishboneBench(hp)
    poses = []
    for s in _sweep(hp, travel, n)[1]:
        lbj, ubj = s["lbj"], s["ubj"]
        r0 = bench._knuckle(lbj, ubj, 0.0)
        u = _unit(ubj - lbj)
        poses.append((lbj, u, r0 @ (hp.tie_outer - hp.lca_outer), r0 @ hp.axle))
    centre = n // 2

    def toe_at(pose, inner: np.ndarray, length2: float) -> float:
        lbj, u, v0, a0 = pose
        d = lbj - inner
        du, uv = float(d @ u), float(u @ v0)
        a = 2.0 * (float(d @ v0) - du * uv)
        b = 2.0 * float(d @ np.cross(u, v0))
        c = float(d @ d) + float(v0 @ v0) - length2 + 2.0 * du * uv
        rr = math.hypot(a, b)
        if rr < abs(c):
            raise ValueError("the tie rod does not reach")
        base = math.atan2(b, a)
        off = math.acos(-c / rr)
        psi = min((base + off, base - off), key=lambda p: abs(math.remainder(p, 2.0 * math.pi)))
        psi = math.remainder(psi, 2.0 * math.pi)
        ax = a0 * math.cos(psi) + np.cross(u, a0) * math.sin(psi) + u * float(u @ a0) * (1.0 - math.cos(psi))
        return math.atan2(float(ax[0]), float(ax[1]))

    def inner_of(p) -> np.ndarray:
        return np.array([float(hp.tie_inner[0]), float(p[0]), float(p[1])])

    def worst(p) -> float:
        inner = inner_of(p)
        length2 = float(np.sum((hp.tie_outer - inner) ** 2))
        try:
            toes = [toe_at(pose, inner, length2) for pose in poses]
        except ValueError:
            return 1e3
        return max(abs(t - toes[centre]) for t in toes)

    res = minimize(worst, [float(hp.tie_inner[1]), float(hp.tie_inner[2])], method="Nelder-Mead",
                   options={"xatol": 1e-5, "fatol": 1e-7, "maxiter": 400})
    return dataclasses.replace(hp, tie_inner=inner_of(res.x))


_POINTS = ("lca_front", "lca_rear", "lca_outer", "uca_front", "uca_rear", "uca_outer", "tie_inner", "tie_outer",
           "wheel_center")


@lru_cache(maxsize=64)
def _builtin(key: tuple) -> Hardpoints:
    y, z, radius, travel = key
    return low_bump_steer(generic_sla(np.array([0.0, y, z]), radius), travel)


def builtin_hardpoints(wheel_center: np.ndarray, radius: float, travel: float) -> Hardpoints:
    """The builtin double wishbone of a LEFT wheel: the generic layout with the tie rod placed for
    the least bump steer over +-travel (m). The layout does not depend on the wheel's x, so it is
    made at x = 0 and moved there; the same geometry always gives the same points."""
    x, y, z = (float(v) for v in wheel_center)
    hp = _builtin((y, z, float(radius), float(travel)))
    shift = np.array([x, 0.0, 0.0])
    moved = {k: getattr(hp, k) + shift for k in _POINTS + ("damper_lower", "damper_upper") if getattr(hp, k) is not None}
    return dataclasses.replace(hp, axle=np.array(hp.axle), **moved)


def save_table(table: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(table, indent=1), encoding="utf-8")


def load_table(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


@dataclass
class PathPoly:
    """Wheel path of one corner as polynomials in travel s (m), for the LEFT-hand table.

    coef rows: dx, dy, dz (m) of the wheel centre from its design position,
    and the change of camber and toe (rad) from design. Highest power first.
    camber0 / toe0: the design (static) camber and toe.
    comp rows (when the table has them): COMPLIANCE_KEYS in rad/N and rad/(N m).
    """

    coef: np.ndarray  # (5, deg+1)
    s_lo: float
    s_hi: float
    camber0: float = 0.0
    toe0: float = 0.0
    roll_center_height: float | None = None  # m, at design travel
    comp: np.ndarray | None = None  # (6, deg+1)

    @classmethod
    def vertical(cls) -> "PathPoly":
        coef = np.zeros((5, POLY_DEGREE + 1))
        coef[2, -2] = 1.0
        return cls(coef, -1.0, 1.0)

    @classmethod
    def from_table(cls, table: dict) -> "PathPoly":
        rows = table["rows"]
        s = np.array([r["travel_mm"] for r in rows]) / 1000.0
        wc = np.array([r["wheel_center_mm"] for r in rows]) / 1000.0
        camber = np.radians([r["camber_deg"] for r in rows])
        toe = np.radians([r["toe_deg"] for r in rows])
        c0, t0 = float(np.interp(0.0, s, camber)), float(np.interp(0.0, s, toe))
        ys = [wc[:, 0], wc[:, 1], wc[:, 2], camber - c0, toe - t0]
        coef = np.array([np.polyfit(s, y, POLY_DEGREE) for y in ys])
        coef[2] = 0.0
        coef[2, -2] = 1.0  # dz is the travel itself
        rc = None
        if "roll_center_height_mm" in rows[0]:
            rc = float(np.interp(0.0, s, [r["roll_center_height_mm"] for r in rows])) / 1000.0
        comp = None
        cols = [f"{k}_{COMPLIANCE_UNIT[k]}" for k in COMPLIANCE_KEYS]
        if all(c in rows[0] for c in cols):
            comp = np.array([np.polyfit(s, np.radians([r[c] for r in rows]) / 1000.0, POLY_DEGREE) for c in cols])
        return cls(coef, float(s[0]), float(s[-1]), c0, t0, rc, comp)

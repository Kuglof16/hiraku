"""K&C bench: double-wishbone kinematics solved exactly, and the wheel path tables.

The bench moves the lower arm through its range, closes the loop for the
upper arm (ball-joint distance) and the tie rod (tie-rod length), and records
the wheel centre, camber, toe and contact point against wheel travel. The
vehicle model then runs on these tables (the "K" of K&C). Compliance ("C")
is not modelled.

Conventions (body frame, ISO 8855: x forward, y left, z up). Tables are
written for a LEFT wheel; a right wheel uses the mirror image (y -> -y).
camber: top of the wheel outward is positive. toe: toe-in is positive.
travel: wheel-centre vertical motion relative to the body, bump positive.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

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

    def to_json(self) -> dict:
        out = {k: [float(x) * 1000.0 for x in getattr(self, k)] for k in (
            "lca_front", "lca_rear", "lca_outer", "uca_front", "uca_rear", "uca_outer",
            "tie_inner", "tie_outer", "wheel_center")}
        out["axle"] = [float(x) for x in self.axle]
        out["radius_mm"] = self.radius * 1000.0
        return out

    @classmethod
    def from_json(cls, d: dict) -> "Hardpoints":
        kw = {k: np.asarray(d[k], dtype=float) / 1000.0 for k in (
            "lca_front", "lca_rear", "lca_outer", "uca_front", "uca_rear", "uca_outer",
            "tie_inner", "tie_outer", "wheel_center")}
        return cls(axle=_unit(np.asarray(d["axle"], dtype=float)), radius=float(d["radius_mm"]) / 1000.0, **kw)


def generic_sla(wheel_center: np.ndarray, radius: float) -> Hardpoints:
    """A generic short-long-arm layout scaled to the wheel (left side).

    Not taken from any real vehicle: lower arm 0.6 and upper arm 0.42 of the
    lateral distance from the centreline to the ball joints, upper arm inner
    pivots 15 mm lower than the outer joint, tie rod behind the axle.
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
    return Hardpoints(lf, lr, lbj, uf, ur, ubj, tri, tro, wc.copy(), np.array([0.0, 1.0, 0.0]), r)


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


def build_table(hp: Hardpoints, travel: float, n: int = 81) -> dict:
    """Sweep the bench over +-travel (m) of wheel travel and return a K&C table (mm, deg)."""
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
    rows = []
    bench._phi, bench._psi = 0.0, 0.0
    sols = []
    for z in grid:
        theta = float(np.interp(z, tz, th))
        # refine theta so that the travel is exact
        f = lambda t: float(bench.solve(t)["wheel_center"][2] - z0 - z)
        lo, hi = theta - 0.01, theta + 0.01
        theta = brentq(f, lo, hi, xtol=1e-14, rtol=1e-14)
        sols.append(bench.solve(theta))
    wc0 = hp.wheel_center
    cp = np.array([s["contact"] for s in sols])
    dy_dz = np.gradient(cp[:, 1], cp[:, 2])
    for z, s, slope in zip(grid, sols, dy_dz):
        d = s["wheel_center"] - wc0
        rows.append({
            "travel_mm": float(z) * 1000.0,
            "wheel_center_mm": [float(x) * 1000.0 for x in d],
            "camber_deg": math.degrees(s["camber"]),
            "toe_deg": math.degrees(s["toe"]),
            "contact_mm": [float(x) * 1000.0 for x in s["contact"]],
            # front-view kinematic roll centre from the contact point's motion
            "roll_center_height_mm": float(s["contact"][1] * slope + s["contact"][2]) * 1000.0,
        })
    return {
        "kind": "kc_table",
        "suspension": "double_wishbone",
        "side": "left",
        "hardpoints_mm": hp.to_json(),
        "rows": rows,
    }


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
    """

    coef: np.ndarray  # (5, deg+1)
    s_lo: float
    s_hi: float
    camber0: float = 0.0
    toe0: float = 0.0
    roll_center_height: float | None = None  # m, at design travel

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
        return cls(coef, float(s[0]), float(s[-1]), c0, t0, rc)

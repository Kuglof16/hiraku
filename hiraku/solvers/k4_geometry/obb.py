"""Oriented boxes and the separating axis test (15 axes: 3 + 3 face normals and
9 edge cross products), in numpy.

overlap(a, b) gives, over the candidate axes, the smallest overlap when the
boxes intersect (an estimate of the penetration depth) and the largest gap
when they do not (a lower bound of the true distance).

ray_box(origin, direction, box) gives where a straight line enters and leaves a box.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..k0_handcalc.rigid import rpy_matrix


@dataclass
class OBB:
    center: np.ndarray  # mm
    axes: np.ndarray  # 3 x 3, rows are the box's unit axes
    half: np.ndarray  # half sizes along those axes, mm
    name: str = ""

    def corners(self) -> np.ndarray:
        s = np.array([[i, j, k] for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)], float)
        return self.center + (s * self.half) @ self.axes

    def moved(self, offset) -> "OBB":
        return OBB(self.center + np.asarray(offset, float), self.axes, self.half, self.name)

    def turned(self, R: np.ndarray, about) -> "OBB":
        """Rotated by R (3 x 3) about a point."""
        p = np.asarray(about, float)
        return OBB(p + R @ (self.center - p), (R @ self.axes.T).T, self.half, self.name)


def from_box(center, size, rpy_deg=None, name: str = "") -> OBB:
    R = rpy_matrix(rpy_deg) if rpy_deg is not None and any(rpy_deg) else np.eye(3)
    return OBB(np.asarray(center, float), R.T.copy(), np.asarray(size, float) / 2.0, name)


def from_member(a, b, width: float, height: float, name: str = "") -> OBB:
    """Box of a straight member: length along a-b, `height` toward up (or x for vertical members)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    x = b - a
    L = float(np.linalg.norm(x))
    x = x / L
    # (x . up) with up = z is x[2] exactly; the cross products as np.cross works them (the same products and
    # differences, one by one), without its general array handling
    up = (1.0, 0.0, 0.0) if abs(float(x[2])) > 0.999 else (0.0, 0.0, 1.0)
    z = np.array(_cross(x.tolist(), up))
    z /= np.linalg.norm(z)
    y = np.array(_cross(z.tolist(), x.tolist()))
    return OBB((a + b) / 2.0, np.array([x, y, z]), np.array([L / 2.0, height / 2.0, width / 2.0]), name)


def _cross(u, v) -> list[float]:
    """u x v of two 3-vectors as np.cross works it: (u1 v2 - u2 v1, u2 v0 - u0 v2, u0 v1 - u1 v0)."""
    return [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]


def ray_box(origin, direction, box: OBB) -> tuple[float, float] | None:
    """Where the line origin + t direction enters and leaves the box (slabs in the box's own axes):
    (t_in, t_out) with t_in <= t_out, in the units of t (mm for a unit direction). Either may be negative
    (t_in < 0 < t_out: the origin is inside). None when the line misses the box."""
    o = box.axes @ (np.asarray(origin, float) - box.center)
    d = box.axes @ np.asarray(direction, float)
    t_in, t_out = -np.inf, np.inf
    for i in range(3):
        if abs(d[i]) < 1e-12:
            if abs(o[i]) > box.half[i]:
                return None
            continue
        t1, t2 = (-box.half[i] - o[i]) / d[i], (box.half[i] - o[i]) / d[i]
        t_in, t_out = max(t_in, min(t1, t2)), min(t_out, max(t1, t2))
        if t_in > t_out:
            return None
    return float(t_in), float(t_out)


_EYE3 = np.eye(3)
_ZERO = np.zeros(1)
# indices into [R.ravel() (0..8, R[r, j] at 3 r + j), -R.ravel() (9..17), 0 (18)] for the rows of overlap's c
_CROSS_IDX = np.array([[[18, 9 + 6 + j, 3 + j], [6 + j, 18, 9 + j], [9 + 3 + j, j, 18]][i]
                       for i in range(3) for j in range(3)])


def overlap(a: OBB, b: OBB) -> float:
    """Positive: smallest overlap over the axes (they intersect). Negative: minus the largest gap.

    Worked in a's own axes: column j of R = a.axes b.axes^T is b's axis j there,
    and a_i x b_j is e_i x R[:, j] (the sign of an axis does not matter).
    """
    R = a.axes @ b.axes.T
    # row 3 i + j of c is e_i x R[:, j]: (0, -R[2, j], R[1, j]), (R[2, j], 0, -R[0, j]), (-R[1, j], R[0, j], 0),
    # picked from R, -R and a 0
    c = np.concatenate([R.ravel(), -R.ravel(), _ZERO])[_CROSS_IDX]
    n = np.sqrt(np.einsum("ij,ij->i", c, c))
    keep = n > 1e-9  # parallel edges give no axis
    axes = np.concatenate([_EYE3, R.T, c[keep] / n[keep, None]])
    o = np.abs(axes) @ a.half + np.abs(axes @ R) @ b.half - np.abs(axes @ (a.axes @ (b.center - a.center)))
    gaps = o[o < 0]
    return float(gaps.min()) if gaps.size else float(o.min())

"""Oriented boxes and the separating axis test (15 axes: 3 + 3 face normals and
9 edge cross products), in numpy.

overlap(a, b) gives, over the candidate axes, the smallest overlap when the
boxes intersect (an estimate of the penetration depth) and the largest gap
when they do not (a lower bound of the true distance).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


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


def rpy_matrix(rpy_deg) -> np.ndarray:
    r, p, y = np.radians(rpy_deg)
    Rx = np.array([[1, 0, 0], [0, np.cos(r), -np.sin(r)], [0, np.sin(r), np.cos(r)]])
    Ry = np.array([[np.cos(p), 0, np.sin(p)], [0, 1, 0], [-np.sin(p), 0, np.cos(p)]])
    Rz = np.array([[np.cos(y), -np.sin(y), 0], [np.sin(y), np.cos(y), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def from_box(center, size, rpy_deg=None, name: str = "") -> OBB:
    R = rpy_matrix(rpy_deg) if rpy_deg is not None and any(rpy_deg) else np.eye(3)
    return OBB(np.asarray(center, float), R.T.copy(), np.asarray(size, float) / 2.0, name)


def from_member(a, b, width: float, height: float, name: str = "") -> OBB:
    """Box of a straight member: length along a-b, `height` toward up (or x for vertical members)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    x = b - a
    L = float(np.linalg.norm(x))
    x = x / L
    up = np.array([0.0, 0.0, 1.0])
    if abs(x @ up) > 0.999:
        up = np.array([1.0, 0.0, 0.0])
    z = np.cross(x, up)
    z /= np.linalg.norm(z)
    y = np.cross(z, x)
    return OBB((a + b) / 2.0, np.array([x, y, z]), np.array([L / 2.0, height / 2.0, width / 2.0]), name)


def overlap(a: OBB, b: OBB) -> float:
    """Positive: smallest overlap over the axes (they intersect). Negative: minus the largest gap.

    Worked in a's own axes: column j of R = a.axes b.axes^T is b's axis j there,
    and a_i x b_j is e_i x R[:, j] (the sign of an axis does not matter).
    """
    R = a.axes @ b.axes.T
    z = np.zeros(3)
    c = np.array([[z, -R[2], R[1]], [R[2], z, -R[0]], [-R[1], R[0], z]]).transpose(0, 2, 1).reshape(9, 3)
    n = np.sqrt(np.einsum("ij,ij->i", c, c))
    keep = n > 1e-9  # parallel edges give no axis
    axes = np.concatenate([np.eye(3), R.T, c[keep] / n[keep, None]])
    o = np.abs(axes) @ a.half + np.abs(axes @ R) @ b.half - np.abs(axes @ (a.axes @ (b.center - a.center)))
    gaps = o[o < 0]
    return float(gaps.min()) if gaps.size else float(o.min())

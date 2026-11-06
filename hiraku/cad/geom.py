"""Small geometry used by the writer and the importer (written here so hiraku.cad stays on its own).

Conventions are the design record's (docs/contract.md): mm, x forward, y left, z up.
- A box's rpy_deg is R = Rz(yaw) Ry(pitch) Rx(roll), fixed axes x, y, z.
- A member's section axes follow the beam model's: ez = d x up (normalised), ey = ez x d; for a
  vertical member ey = -x (pointing up) or +x (pointing down) and ez = -y. A rect_tube's h lies
  along ey and b along ez.
"""

from __future__ import annotations

import math

import numpy as np

UP = np.array([0.0, 0.0, 1.0])


def rpy_matrix(rpy_deg) -> np.ndarray:
    r, p, y = (math.radians(float(a)) for a in rpy_deg)
    rx = np.array([[1, 0, 0], [0, math.cos(r), -math.sin(r)], [0, math.sin(r), math.cos(r)]])
    ry = np.array([[math.cos(p), 0, math.sin(p)], [0, 1, 0], [-math.sin(p), 0, math.cos(p)]])
    rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
    return rz @ ry @ rx


def member_axes(d) -> tuple[np.ndarray, np.ndarray]:
    """(ey, ez) for a member with unit direction d."""
    d = np.asarray(d, float)
    c = np.cross(d, UP)
    if np.linalg.norm(c) < 1e-9:
        ey = np.array([-1.0, 0.0, 0.0]) if d[2] > 0 else np.array([1.0, 0.0, 0.0])
        return ey, np.array([0.0, -1.0, 0.0])
    ez = c / np.linalg.norm(c)
    return np.cross(ez, d), ez


def unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    if n == 0:
        raise ValueError("zero-length vector")
    return v / n


def rotation_about(axis, angle_deg: float) -> np.ndarray:
    k = unit(axis)
    a = math.radians(angle_deg)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + math.sin(a) * K + (1 - math.cos(a)) * (K @ K)

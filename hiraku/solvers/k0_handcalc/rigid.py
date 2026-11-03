"""Rigid-body helpers shared by K0, K2 and K4: the roll-pitch-yaw rotation of a
part box (contract box.rpy_deg) and the inertia of a solid box."""

from __future__ import annotations

import numpy as np


def rpy_matrix(rpy_deg) -> np.ndarray:
    """R = Rz(yaw) Ry(pitch) Rx(roll) for [roll, pitch, yaw] in degrees (fixed axes x, y, z)."""
    r, p, y = np.radians(rpy_deg)
    Rx = np.array([[1, 0, 0], [0, np.cos(r), -np.sin(r)], [0, np.sin(r), np.cos(r)]])
    Ry = np.array([[np.cos(p), 0, np.sin(p)], [0, 1, 0], [-np.sin(p), 0, np.cos(p)]])
    Rz = np.array([[np.cos(y), -np.sin(y), 0], [np.sin(y), np.cos(y), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def box_inertia(mass: float, size, rpy_deg=None) -> np.ndarray:
    """Inertia of a solid box about its centre, rotated by rpy_deg: mass * length^2 in the units given
    (kg and mm give kg*mm^2)."""
    a, b, c = (float(s) for s in size)
    local = mass / 12.0 * np.diag([b * b + c * c, a * a + c * c, a * a + b * b])
    if rpy_deg is None or not any(rpy_deg):
        return local
    R = rpy_matrix(rpy_deg)
    return R @ local @ R.T

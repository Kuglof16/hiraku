"""Mass properties from the design's part boxes (solid boxes, uniform density)."""

from __future__ import annotations

import math

import numpy as np


def rpy_matrix(roll: float, pitch: float, yaw: float) -> np.ndarray:
    """R = Rz(yaw) Ry(pitch) Rx(roll), angles in rad."""
    cr, sr = math.cos(roll), math.sin(roll)
    cp, sp = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(yaw), math.sin(yaw)
    return np.array([
        [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
        [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
        [-sp, cp * sr, cp * cr],
    ])


def box_inertia(mass: float, size: np.ndarray, rpy: np.ndarray | None = None) -> np.ndarray:
    """Inertia of a solid box about its own centre: m (b^2 + c^2) / 12 on each axis."""
    a, b, c = (float(x) for x in size)
    i0 = np.diag([b * b + c * c, a * a + c * c, a * a + b * b]) * (mass / 12.0)
    if rpy is None or not np.any(rpy):
        return i0
    r = rpy_matrix(*rpy)
    return r @ i0 @ r.T


def point_inertia(mass: float, d: np.ndarray) -> np.ndarray:
    """Parallel-axis term m (|d|^2 E - d d^T)."""
    d = np.asarray(d, dtype=float)
    return mass * (float(d @ d) * np.eye(3) - np.outer(d, d))


def combine(items: list[tuple[float, np.ndarray, np.ndarray]]) -> tuple[float, np.ndarray, np.ndarray]:
    """items: (mass, centre (3,), inertia about own centre (3,3)) -> (m, cg, I about cg)."""
    m = sum(it[0] for it in items)
    if m <= 0.0:
        raise ValueError("total mass is zero")
    cg = sum(it[0] * np.asarray(it[1], dtype=float) for it in items) / m
    inertia = np.zeros((3, 3))
    for mi, ci, ii in items:
        inertia += ii + point_inertia(mi, np.asarray(ci, dtype=float) - cg)
    return m, cg, inertia


def part_items(parts: list[dict]) -> list[tuple[float, np.ndarray, np.ndarray, dict]]:
    """Design parts (mm, kg) -> (mass, centre m, inertia about own centre, part)."""
    out = []
    for p in parts:
        m = float(p["mass_kg"])
        if m <= 0.0:
            continue
        box = p["box"]
        centre = np.asarray(box["center_mm"], dtype=float) / 1000.0
        size = np.asarray(box["size_mm"], dtype=float) / 1000.0
        rpy = np.radians(np.asarray(box.get("rpy_deg", [0.0, 0.0, 0.0]), dtype=float))
        out.append((m, centre, box_inertia(m, size, rpy), p))
    return out

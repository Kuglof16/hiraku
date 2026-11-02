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


def section_area(section: dict) -> float:
    """Cross-section area (mm^2): A_mm2 if given, else from dims_mm (textbook formulas, sharp corners)."""
    if "A_mm2" in section:
        return float(section["A_mm2"])
    shape = section.get("shape")
    d = section.get("dims_mm", {})
    try:
        if shape == "rect_tube":
            b, h, t = float(d["b"]), float(d["h"]), float(d["t"])
            return b * h - (b - 2.0 * t) * (h - 2.0 * t)
        if shape == "round_tube":
            o, t = float(d["d"]), float(d["t"])
            return math.pi / 4.0 * (o * o - (o - 2.0 * t) ** 2)
        if shape == "rect_solid":
            return float(d["b"]) * float(d["h"])
        if shape == "round_solid":
            return math.pi / 4.0 * float(d["d"]) ** 2
    except KeyError as e:
        raise ValueError(f"section {shape} is missing dims_mm {e.args[0]}") from None
    raise ValueError(f"section shape {shape!r}: give A_mm2 so K2 can weigh the member")


def member_items(design: dict) -> list[tuple[float, np.ndarray, np.ndarray, dict]]:
    """Frame members as thin rods: mass = density x area x length at the member centre,
    inertia m L^2 / 12 about the axes across the member."""
    nodes = {n["id"]: np.asarray(n["xyz_mm"], dtype=float) / 1000.0 for n in design.get("nodes") or []}
    sections = design.get("sections") or {}
    materials = design.get("materials") or {}
    out = []
    for mb in design.get("members") or []:
        mid = mb.get("id", "?")
        if mb["node_a"] not in nodes or mb["node_b"] not in nodes:
            raise ValueError(f"member {mid}: node {mb['node_a']} or {mb['node_b']} is not in design.nodes")
        if mb["section"] not in sections:
            raise ValueError(f"member {mid}: section {mb['section']!r} is not in design.sections")
        mat = materials.get(mb["material"])
        if mat is None or "density_kg_m3" not in mat:
            raise ValueError(f"member {mid}: material {mb['material']!r} needs density_kg_m3")
        a, b = nodes[mb["node_a"]], nodes[mb["node_b"]]
        axis = b - a
        length = float(np.linalg.norm(axis))
        if length <= 0.0:
            continue
        m = float(mat["density_kg_m3"]) * section_area(sections[mb["section"]]) * 1e-6 * length
        u = axis / length
        inertia = m * length * length / 12.0 * (np.eye(3) - np.outer(u, u))
        out.append((m, 0.5 * (a + b), inertia, {"id": mid, "kind": "frame_member"}))
    return out


def items_symmetric(items: list[tuple[float, np.ndarray, np.ndarray, dict]], tol: float = 1e-9) -> bool:
    """True when every mass item has a mirror image (y -> -y) of the same mass and inertia."""
    flip = np.diag([1.0, -1.0, 1.0])
    used = [False] * len(items)
    for i, (m, c, inertia, _) in enumerate(items):
        if used[i]:
            continue
        mc = c * [1.0, -1.0, 1.0]
        mi = flip @ inertia @ flip
        for j in range(len(items)):
            if used[j] or (j == i and abs(c[1]) > tol):
                continue
            m2, c2, i2, _ = items[j]
            if abs(m - m2) <= tol * max(1.0, m) and np.allclose(c2, mc, atol=tol) and np.allclose(i2, mi, atol=tol * max(1.0, m)):
                used[i] = used[j] = True
                break
        else:
            return False
    return True


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

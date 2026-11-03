"""Mass properties from the design's part boxes (solid boxes, uniform density).

The box inertia (and its rotation) and the section area are K0's shared helpers
(hiraku.solvers.k0_handcalc.rigid and .sections), so every department weighs a part
the same way.
"""

from __future__ import annotations

import numpy as np

from hiraku.solvers.k0_handcalc.rigid import box_inertia
from hiraku.solvers.k0_handcalc.sections import area as section_area


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
        out.append((m, centre, box_inertia(m, size, box.get("rpy_deg")), p))
    return out

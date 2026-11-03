"""Structured hexahedral meshes: boxes, cylinders (O-grid), thick rings and a plate with a hole.

Every shape is made of structured blocks. A block is a lattice of node positions
P[i, j, k] (shape (2a+1, 2b+1, 2c+1, 3) for 20-node bricks, (a+1, b+1, c+1, 3)
for 8-node ones) computed from a mapping of the block's parameters, so the
mid-edge nodes of a 20-node brick lie on the mapped curve (on the true
circle for a cylinder or a hole), not on the straight chord. Blocks are
joined by merging nodes that coincide (within 1e-7 of the model size).

Boxes (box_mesh, parts_mesh): every face position of every box is collected
per axis into one set of grid lines; each interval between two lines is cut
into ceil(length / size) equal parts, and every box is meshed on the lines
inside it. Boxes that touch therefore share their nodes on the common face.
Cells covered by two boxes are kept once (the first box in the list).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from .solid import CORNER_SIGNS, EDGES, FACES20, FACES8

# lattice offsets (in half steps) of the 20 nodes of a brick, in the solid.py node order
_CORNER_OFF = ((CORNER_SIGNS + 1) // 2 * 2).astype(int)  # 0 or 2
_MID_OFF = ((_CORNER_OFF[EDGES[:, 0]] + _CORNER_OFF[EDGES[:, 1]]) // 2).astype(int)
_OFF20 = np.vstack([_CORNER_OFF, _MID_OFF])
_OFF8 = (_CORNER_OFF // 2).astype(int)


@dataclass
class HexMesh:
    """nodes (N, 3) mm; conn (E, 20 or 8) in the solid.py node order; part (E,) a label per element."""

    nodes: np.ndarray
    conn: np.ndarray
    part: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    @property
    def order(self) -> int:
        return 2 if self.conn.shape[1] == 20 else 1

    @property
    def faces(self) -> np.ndarray:
        return FACES20 if self.order == 2 else FACES8

    def nodes_where(self, pred) -> np.ndarray:
        """Indices of the nodes (used by an element) whose coordinates satisfy pred(x, y, z) -> bool array."""
        used = np.zeros(len(self.nodes), bool)
        used[self.conn.ravel()] = True
        x, y, z = self.nodes.T
        return np.nonzero(used & np.asarray(pred(x, y, z), bool))[0]

    def boundary_faces(self) -> np.ndarray:
        """(F, 8 or 4) node lists of the faces that belong to only one element (outer surface)."""
        f = self.conn[:, self.faces].reshape(-1, self.faces.shape[1])
        key = np.sort(f[:, :4], axis=1)
        _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
        return f[cnt[inv.ravel()] == 1]

    def faces_where(self, pred) -> np.ndarray:
        """Boundary faces all of whose nodes satisfy pred(x, y, z) -> bool array."""
        f = self.boundary_faces()
        x = self.nodes[f]
        ok = np.asarray(pred(x[..., 0], x[..., 1], x[..., 2]), bool)
        return f[np.all(ok, axis=1)]


def structured_block(P: np.ndarray, order: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Nodes and bricks of one block from its lattice P (for order 2, shape (2a+1, 2b+1, 2c+1, 3)).

    The lattice's face- and body-centre points are not used by 20-node bricks. If the lattice is
    left-handed the i direction is reversed so every brick has det J > 0.
    """
    P = np.asarray(P, float)
    st = 2 if order == 2 else 1
    a, b, c = ((np.array(P.shape[:3]) - 1) // st)
    x0 = P[0, 0, 0]
    v = np.linalg.det(np.array([P[st, 0, 0] - x0, P[0, st, 0] - x0, P[0, 0, st] - x0]))
    if v < 0:
        P = P[::-1]
    off = _OFF20 if order == 2 else _OFF8
    I, J, K = np.meshgrid(np.arange(a), np.arange(b), np.arange(c), indexing="ij")
    base = np.stack([I.ravel(), J.ravel(), K.ravel()], axis=1) * st  # (E, 3)
    lat = base[:, None, :] + off[None, :, :]  # (E, n, 3)
    shape = P.shape[:3]
    flat = np.ravel_multi_index((lat[..., 0], lat[..., 1], lat[..., 2]), shape)
    used, conn = np.unique(flat.ravel(), return_inverse=True)
    return P.reshape(-1, 3)[used], conn.reshape(flat.shape)


def merge(blocks: list, parts: list | None = None, tol_rel: float = 1e-7) -> HexMesh:
    """Join blocks [(nodes, conn)] into one mesh, merging coincident nodes and dropping repeated bricks."""
    nodes = np.vstack([b[0] for b in blocks])
    offs = np.cumsum([0] + [len(b[0]) for b in blocks])
    conn = np.vstack([b[1] + o for b, o in zip(blocks, offs[:-1])])
    part = []
    for i, b in enumerate(blocks):
        part += [parts[i] if parts else f"block{i}"] * len(b[1])
    size = float(np.ptp(nodes, axis=0).max()) or 1.0
    tree = cKDTree(nodes)
    pairs = tree.query_pairs(tol_rel * size, output_type="ndarray")
    rep = np.arange(len(nodes))
    for i, j in sorted(map(tuple, pairs)):  # union to the smallest index
        ri, rj = rep[i], rep[j]
        while rep[ri] != ri:
            ri = rep[ri]
        while rep[rj] != rj:
            rj = rep[rj]
        if ri != rj:
            rep[max(ri, rj)] = min(ri, rj)
    for i in range(len(rep)):
        r = i
        while rep[r] != r:
            r = rep[r]
        rep[i] = r
    conn = rep[conn]
    used, inv = np.unique(conn.ravel(), return_inverse=True)
    conn = inv.reshape(conn.shape)
    nodes = nodes[used]
    key = np.sort(conn[:, :8], axis=1)
    _, first = np.unique(key, axis=0, return_index=True)
    first = np.sort(first)
    dropped = len(conn) - len(first)
    m = HexMesh(nodes, conn[first], [part[i] for i in first])
    if dropped:
        m.notes.append(f"{dropped} brick(s) covered by two blocks kept once")
    return m


def _lattice_1d(lines: np.ndarray, order: int) -> np.ndarray:
    if order == 1:
        return np.asarray(lines, float)
    out = np.empty(2 * len(lines) - 1)
    out[0::2] = lines
    out[1::2] = 0.5 * (lines[:-1] + lines[1:])
    return out


def divide(lines, size: float) -> np.ndarray:
    """Sorted unique positions with every interval cut into ceil(length / size) equal parts."""
    lines = np.unique(np.round(np.asarray(lines, float), 9))
    out = [lines[:1]]
    for a, b in zip(lines[:-1], lines[1:]):
        n = max(1, math.ceil((b - a) / size - 1e-9))
        out.append(np.linspace(a, b, n + 1)[1:])
    return np.concatenate(out)


def grid_block(xs, ys, zs, order: int = 2) -> tuple[np.ndarray, np.ndarray]:
    X, Y, Z = np.meshgrid(_lattice_1d(np.asarray(xs, float), order), _lattice_1d(np.asarray(ys, float), order),
                          _lattice_1d(np.asarray(zs, float), order), indexing="ij")
    return structured_block(np.stack([X, Y, Z], axis=-1), order)


def box_mesh(boxes: list, size: float, order: int = 2, names: list | None = None) -> HexMesh:
    """Bricks for axis-aligned boxes [(lo (3,), hi (3,))]; touching boxes share nodes."""
    boxes = [(np.asarray(lo, float), np.asarray(hi, float)) for lo, hi in boxes]
    lines = [divide([v for lo, hi in boxes for v in (lo[k], hi[k])], size) for k in range(3)]
    blocks = []
    for lo, hi in boxes:
        sel = [lines[k][(lines[k] >= lo[k] - 1e-9) & (lines[k] <= hi[k] + 1e-9)] for k in range(3)]
        blocks.append(grid_block(*sel, order=order))
    return merge(blocks, names)


def parts_mesh(design: dict, size: float, part_ids=None, order: int = 2) -> HexMesh:
    """Bricks for the design parts' boxes (box.center_mm, box.size_mm), all parts or the listed ones."""
    parts = [p for p in design.get("parts", []) if "box" in p and (part_ids is None or p["id"] in part_ids)]
    if not parts:
        raise ValueError("no part with a box to mesh")
    boxes = []
    for p in parts:
        c = np.asarray(p["box"]["center_mm"], float)
        s = np.asarray(p["box"]["size_mm"], float)
        boxes.append((c - s / 2, c + s / 2))
    return box_mesh(boxes, size, order, [p["id"] for p in parts])


# ------------------------------------------------------------------ mapped blocks


def mapped_block(f, n: tuple, order: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Block from a mapping f(u, v, w) -> (..., 3) on the unit cube with n = (nu, nv, nw) bricks."""
    st = 2 if order == 2 else 1
    u = np.linspace(0, 1, st * n[0] + 1)
    v = np.linspace(0, 1, st * n[1] + 1)
    w = np.linspace(0, 1, st * n[2] + 1)
    U, V, W = np.meshgrid(u, v, w, indexing="ij")
    return structured_block(f(U, V, W), order)


def graded(v: np.ndarray, ratio: float) -> np.ndarray:
    """Map [0, 1] -> [0, 1] as (ratio^v - 1) / (ratio - 1): with n equal steps in v, successive intervals
    grow by ratio^(1/n), so `ratio` is the last interval over the first (about). 1: uniform."""
    if abs(ratio - 1.0) < 1e-12:
        return v
    return (np.power(ratio, v) - 1.0) / (ratio - 1.0)


def _frame(axis) -> np.ndarray:
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    t = np.array([1.0, 0.0, 0.0]) if abs(a[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = t - a * (t @ a)
    e1 /= np.linalg.norm(e1)
    return np.stack([e1, np.cross(a, e1), a])  # rows e1, e2, axis


def cylinder_mesh(radius: float, length: float, n_core: int, n_ring: int, n_len: int, order: int = 2,
                  centre=(0.0, 0.0, 0.0), axis=(0.0, 0.0, 1.0), core_ratio: float = 0.5) -> HexMesh:
    """Solid cylinder by an O-grid: a square core (half side core_ratio * radius, n_core bricks a side)
    and four blocks from the square's sides to the circle (n_ring bricks across). The outer nodes,
    mid-edge nodes included, lie on the circle. Axis from `centre` along `axis`, length `length`."""
    R, s = radius, core_ratio * radius
    Rm = _frame(axis)
    c0 = np.asarray(centre, float)

    def place(x, y, w):
        p = np.stack([x, y, w * length], axis=-1)
        return c0 + p @ Rm

    blocks = [mapped_block(lambda u, v, w: place(-s + 2 * s * u, -s + 2 * s * v, w), (n_core, n_core, n_len), order)]
    for k in range(4):
        ang = k * np.pi / 2

        def f(u, v, w, ang=ang):
            # u: radial (square side -> circle), v: along the side, w: axis
            sx, sy = s, -s + 2 * s * v
            phi = -np.pi / 4 + np.pi / 2 * v
            cx, cy = R * np.cos(phi), R * np.sin(phi)
            x = (1 - u) * sx + u * cx
            y = (1 - u) * sy + u * cy
            ca, sa = math.cos(ang), math.sin(ang)
            return place(ca * x - sa * y, sa * x + ca * y, w)

        blocks.append(mapped_block(f, (n_ring, n_core, n_len), order))
    return merge(blocks, ["cylinder"] * 5)


def ring_mesh(r_in: float, r_out: float, length: float, n_r: int, n_theta: int, n_len: int,
              theta=(0.0, np.pi / 2), order: int = 2, ratio: float = 1.0) -> HexMesh:
    """Thick ring sector about the z axis: r in [r_in, r_out], angle in theta, z in [0, length]. Nodes on true
    circles. ratio grades the radial intervals (outer / inner interval growth per element)."""
    def f(u, v, w):
        r = r_in + (r_out - r_in) * graded(u, ratio ** n_r if ratio != 1.0 else 1.0)
        t = theta[0] + (theta[1] - theta[0]) * v
        return np.stack([r * np.cos(t), r * np.sin(t), w * length], axis=-1)

    return merge([mapped_block(f, (n_r, n_theta, n_len), order)], ["ring"])


def holed_plate_mesh(width: float, length: float, thickness: float, d: float, n_quarter: int, n_radial: int,
                     n_thick: int, ratio: float = 1.0, order: int = 2, n_strip: int | None = None) -> HexMesh:
    """Quarter of a plate with a central hole (a box minus a cylinder): x in [0, W/2], y in [0, L/2],
    z in [0, thickness]. Around the hole a square [0, W/2]^2 is mapped from the arc (2 n_quarter bricks over
    90 degrees, n_radial across, intervals growing by `ratio` per brick away from the hole); a strip
    [0, W/2] x [W/2, L/2] above it. The hole's nodes, mid-edge nodes included, lie on the circle."""
    S, a = width / 2.0, d / 2.0
    if not a < S <= length / 2.0:
        raise ValueError("need d < W <= L")
    gr = ratio ** n_radial

    def half(lo):
        def f(u, v, w):
            g = graded(u, gr)
            if lo:  # arc 0..45 deg to the edge x = S
                phi = np.pi / 4 * v
                ox, oy = S * np.ones_like(v), S * v
            else:  # arc 45..90 deg to the edge y = S (x from S to 0)
                phi = np.pi / 4 + np.pi / 4 * v
                ox, oy = S * (1 - v), S * np.ones_like(v)
            hx, hy = a * np.cos(phi), a * np.sin(phi)
            return np.stack([hx + g * (ox - hx), hy + g * (oy - hy), w * thickness], axis=-1)
        return f

    blocks = [mapped_block(half(True), (n_radial, n_quarter, n_thick), order),
              mapped_block(half(False), (n_radial, n_quarter, n_thick), order)]
    names = ["plate", "plate"]
    if length / 2.0 > S + 1e-9:
        ny = n_strip or max(1, math.ceil((length / 2.0 - S) / (S / n_quarter)))
        blocks.append(mapped_block(lambda u, v, w: np.stack(
            [S * u, S + (length / 2.0 - S) * v, w * thickness], axis=-1), (n_quarter, ny, n_thick), order))
        names.append("plate")
    return merge(blocks, names)


__all__ = ["HexMesh", "box_mesh", "cylinder_mesh", "divide", "graded", "grid_block", "holed_plate_mesh",
           "mapped_block", "merge", "parts_mesh", "ring_mesh", "structured_block"]

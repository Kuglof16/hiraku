"""Shapes and how they cut the lattice.

Every shape is convex, so a straight lattice link meets it in one interval. A link between two nodes is
"cut" when that interval lies on it, also when both nodes are fluid: a 3 mm plate or a thin tube between
two nodes still stops the flow there, from both sides, instead of vanishing because no node centre falls
inside it. Each cut link keeps its distance to the wall (for the interpolated bounce-back), the part it
belongs to (forces per group, which parts the grid sees) and the wall velocity (rotating wheels).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np

from hiraku.cfd.lattice import C, OPP, W


class Shape:
    """A convex solid. Subclasses give inside and clip; coordinates are metres."""

    name: str = ""
    group: str = "body"
    reflect: float = 1.0  # 1: solid. Below 1: a screen; the share of each population that bounces back.
    velocity: Callable[[np.ndarray], np.ndarray] | None = None  # wall velocity (m/s) at points (n, 3)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        raise NotImplementedError

    def inside(self, p: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def clip(self, p: np.ndarray, d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(t_in, t_out) of the line p + t d inside the shape; no meeting when t_in > t_out."""
        raise NotImplementedError


class Polytope(Shape):
    """{x : normals x <= offsets}. vertices are used for the bounds."""

    def __init__(self, normals: Any, offsets: Any, vertices: Any, name: str = "", group: str = "body"):
        self.normals = np.asarray(normals, float)
        self.offsets = np.asarray(offsets, float)
        self.vertices = np.asarray(vertices, float)
        self.name, self.group = name, group

    def bounds(self):
        return self.vertices.min(axis=0), self.vertices.max(axis=0)

    def inside(self, p):
        return np.all(p @ self.normals.T <= self.offsets, axis=1)

    def clip(self, p, d):
        den = self.normals @ d
        num = self.offsets - p @ self.normals.T
        t_in = np.full(len(p), -np.inf)
        t_out = np.full(len(p), np.inf)
        neg, pos = den < -1e-14, den > 1e-14
        if neg.any():
            t_in = (num[:, neg] / den[neg]).max(axis=1)
        if pos.any():
            t_out = (num[:, pos] / den[pos]).min(axis=1)
        par = ~(neg | pos)
        if par.any():
            out = (num[:, par] < 0.0).any(axis=1)
            t_in = np.where(out, np.inf, t_in)
        return t_in, t_out

    def transformed(self, R: np.ndarray, o: np.ndarray) -> "Polytope":
        """The same shape after x -> R x + o."""
        n = self.normals @ R.T
        s = Polytope(n, self.offsets + n @ o, self.vertices @ R.T + o, self.name, self.group)
        s.reflect, s.velocity = self.reflect, self.velocity
        return s


def box(center: Any, size: Any, R: Any = None, name: str = "", group: str = "body") -> Polytope:
    """An oriented box: R's columns are the box axes."""
    c = np.asarray(center, float)
    h = np.abs(np.asarray(size, float)) / 2.0
    R = np.eye(3) if R is None else np.asarray(R, float)
    normals = np.vstack([R.T, -R.T])
    offsets = np.concatenate([normals[:3] @ c + h, normals[3:] @ c + h])
    corners = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], float) * h
    return Polytope(normals, offsets, corners @ R.T + c, name, group)


def prism(a: Any, b: Any, section: Sequence[Sequence[float]], ey: Any, ez: Any, name: str = "", group: str = "body") -> Polytope:
    """A straight prism from a to b; section is a convex counter-clockwise polygon in (ey, ez)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    ey, ez = np.asarray(ey, float), np.asarray(ez, float)
    axis = b - a
    length = np.linalg.norm(axis)
    d = axis / length
    sec = np.asarray(section, float)
    normals, offsets = [d, -d], [d @ b, -(d @ a)]
    for i in range(len(sec)):
        p, q = sec[i], sec[(i + 1) % len(sec)]
        e = q - p
        n2 = np.array([e[1], -e[0]])  # outward for a counter-clockwise polygon
        n2 /= np.linalg.norm(n2)
        n = n2[0] * ey + n2[1] * ez
        normals.append(n)
        offsets.append(n @ (a + p[0] * ey + p[1] * ez))
    verts = np.vstack([np.array([e0 + s[0] * ey + s[1] * ez for s in sec]) for e0 in (a, b)])
    return Polytope(np.array(normals), np.array(offsets), verts, name, group)


class Sphere(Shape):
    def __init__(self, center: Any, radius: float, name: str = "sphere", group: str = "body"):
        self.center = np.asarray(center, float)
        self.radius = float(radius)
        self.name, self.group = name, group

    def bounds(self):
        return self.center - self.radius, self.center + self.radius

    def inside(self, p):
        return ((p - self.center) ** 2).sum(axis=1) <= self.radius ** 2

    def clip(self, p, d):
        r = p - self.center
        a = d @ d
        b = r @ d
        c = (r * r).sum(axis=1) - self.radius ** 2
        disc = b * b - a * c
        ok = disc >= 0.0
        s = np.sqrt(np.where(ok, disc, 0.0))
        return np.where(ok, (-b - s) / a, np.inf), np.where(ok, (-b + s) / a, -np.inf)


class Cylinder(Shape):
    """A solid cylinder on the axis through center, radius r, width w along axis."""

    def __init__(self, center: Any, axis: Any, radius: float, width: float, name: str = "", group: str = "body"):
        self.center = np.asarray(center, float)
        a = np.asarray(axis, float)
        self.axis = a / np.linalg.norm(a)
        self.radius, self.half = float(radius), float(width) / 2.0
        self.name, self.group = name, group

    def bounds(self):
        a = self.axis
        ext = self.half * np.abs(a) + self.radius * np.sqrt(np.maximum(0.0, 1.0 - a * a))
        return self.center - ext, self.center + ext

    def inside(self, p):
        r = p - self.center
        s = r @ self.axis
        rad2 = (r * r).sum(axis=1) - s * s
        return (np.abs(s) <= self.half) & (rad2 <= self.radius ** 2)

    def clip(self, p, d):
        r = p - self.center
        ax = self.axis
        s0, ds = r @ ax, d @ ax
        # the caps
        if abs(ds) > 1e-14:
            t1, t2 = (-self.half - s0) / ds, (self.half - s0) / ds
            t_in, t_out = np.minimum(t1, t2), np.maximum(t1, t2)
        else:
            out = np.abs(s0) > self.half
            t_in, t_out = np.where(out, np.inf, -np.inf), np.where(out, -np.inf, np.inf)
        # the round side
        rp = r - np.outer(s0, ax)
        dp = d - ds * ax
        a = dp @ dp
        if a < 1e-20:
            outside = (rp * rp).sum(axis=1) > self.radius ** 2
            return np.where(outside, np.inf, t_in), t_out
        b = rp @ dp
        c = (rp * rp).sum(axis=1) - self.radius ** 2
        disc = b * b - a * c
        ok = disc >= 0.0
        q = np.sqrt(np.where(ok, disc, 0.0))
        return np.maximum(t_in, np.where(ok, (-b - q) / a, np.inf)), np.minimum(t_out, np.where(ok, (-b + q) / a, -np.inf))


@dataclass
class Grid:
    """Interior nodes at origin + (index + 1/2) dx. With a ground, the ground plane is z = origin[2]."""

    nx: int
    ny: int
    nz: int
    dx: float
    origin: np.ndarray

    @property
    def strides(self) -> tuple[int, int, int]:
        return (1, self.nx + 2, (self.nx + 2) * (self.ny + 2))

    @property
    def n_total(self) -> int:
        return (self.nx + 2) * (self.ny + 2) * (self.nz + 2)

    def node_index(self, i: Any, j: Any, k: Any) -> Any:
        return (i + 1) + (j + 1) * (self.nx + 2) + (k + 1) * (self.nx + 2) * (self.ny + 2)

    def ijk(self, n: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        X, Y = self.nx + 2, self.ny + 2
        k = n // (X * Y)
        j = (n - k * X * Y) // X
        return n - k * X * Y - j * X - 1, j - 1, k - 1

    def position(self, n: np.ndarray) -> np.ndarray:
        i, j, k = self.ijk(n)
        return self.origin + (np.stack([i, j, k], axis=1) + 0.5) * self.dx


@dataclass
class Voxels:
    grid: Grid
    mask: np.ndarray  # uint8 for all nodes with the ghost layer: 0 fluid, 1 solid, 2 ghost
    node: np.ndarray  # one entry per cut link: the fluid node
    dir: np.ndarray   # the population arriving at the node, c_i pointing from the wall to the node
    q: np.ndarray     # distance node to wall along the link, in link lengths (0..1)
    shape: np.ndarray  # index of the shape that cuts it
    groups: list[str] = field(default_factory=list)
    group_of_shape: np.ndarray = field(default_factory=lambda: np.zeros(0, int))
    shapes: list[Shape] = field(default_factory=list)
    interpolated: bool = True
    enclosed_nodes: int = 0  # fluid nodes the outside air cannot reach, turned solid

    @property
    def n_links(self) -> int:
        return len(self.node)


def voxelize(shapes: Sequence[Shape], grid: Grid, ground: bool = False) -> Voxels:
    """Solid nodes and cut links of the shapes on the grid."""
    mask = np.zeros(grid.n_total, np.uint8)
    # the ghost layer
    X, Y, Z = grid.nx + 2, grid.ny + 2, grid.nz + 2
    m3 = mask.reshape(Z, Y, X)
    m3[0, :, :] = m3[-1, :, :] = 2
    m3[:, 0, :] = m3[:, -1, :] = 2
    m3[:, :, 0] = m3[:, :, -1] = 2
    keys, ts, owners = [], [], []
    for s_id, shape in enumerate(shapes):
        lo, hi = shape.bounds()
        ilo = np.maximum(np.floor((lo - grid.origin) / grid.dx - 1.5).astype(int), 0)
        ihi = np.minimum(np.ceil((hi - grid.origin) / grid.dx + 0.5).astype(int), np.array([grid.nx, grid.ny, grid.nz]) - 1)
        if np.any(ihi < ilo):
            continue
        ii, jj, kk = np.meshgrid(np.arange(ilo[0], ihi[0] + 1), np.arange(ilo[1], ihi[1] + 1), np.arange(ilo[2], ihi[2] + 1), indexing="ij")
        ii, jj, kk = ii.ravel(), jj.ravel(), kk.ravel()
        p = grid.origin + (np.stack([ii, jj, kk], axis=1) + 0.5) * grid.dx
        n = grid.node_index(ii, jj, kk)
        ins = shape.inside(p)
        if shape.reflect >= 1.0:
            mask[n[ins]] = 1
        for q in range(1, 19):
            d = -C[q] * grid.dx  # from the node back along the arriving direction
            t_in, t_out = shape.clip(p, d.astype(float))
            hit = (t_in <= t_out) & (t_in < 1.0) & (t_out > 0.0) & ~ins
            if hit.any():
                keys.append(n[hit] * 19 + q)
                ts.append(np.maximum(t_in[hit], 0.0))
                owners.append(np.full(hit.sum(), s_id))
    if keys:
        key = np.concatenate(keys)
        t = np.concatenate(ts)
        own = np.concatenate(owners)
    else:
        key, t, own = np.zeros(0, np.int64), np.zeros(0), np.zeros(0, int)
    order = np.lexsort((t, key))
    key, t, own = key[order], t[order], own[order]
    first = np.ones(len(key), bool)
    first[1:] = key[1:] != key[:-1]
    key, t, own = key[first], t[first], own[first]
    node, q = key // 19, key % 19
    keep = mask[node] == 0
    node, q, t, own = node[keep], q[keep], t[keep], own[keep]

    # a fluid node next to a solid node always has its link (a tangent meeting can miss the clip)
    solid = np.flatnonzero(mask == 1)
    extra_keys, extra_own = [], []
    sx, sy, sz = grid.strides
    for qq in range(1, 19):
        target = solid + C[qq, 0] * sx + C[qq, 1] * sy + C[qq, 2] * sz
        ok = mask[target] == 0
        if ok.any():
            extra_keys.append(target[ok] * 19 + qq)
    if extra_keys:
        have = node * 19 + q
        wanted = np.unique(np.concatenate(extra_keys))
        missing = wanted[~np.isin(wanted, have)]
        if len(missing):
            node = np.concatenate([node, missing // 19])
            q = np.concatenate([q, missing % 19])
            t = np.concatenate([t, np.full(len(missing), 0.5)])
            own = np.concatenate([own, _nearest_owner(shapes, grid, missing // 19, missing % 19)])

    if ground:
        # below the first row the ground answers, unless a shape comes first (t < 1/2 of the link)
        _, _, kn = grid.ijk(node)
        drop = (kn == 0) & (C[q, 2] > 0) & (t >= 0.5)
        node, q, t, own = node[~drop], q[~drop], t[~drop], own[~drop]

    node, q, t, own, enclosed = _fill_enclosed(mask, grid, node, q, t, own, shapes, ground)

    names: list[str] = []
    gid = np.zeros(len(shapes), int)
    for i, s in enumerate(shapes):
        if s.group not in names:
            names.append(s.group)
        gid[i] = names.index(s.group)
    if not names:
        names = ["body"]
    vox = Voxels(grid, mask, node, q, np.clip(t, 0.0, 1.0), own, names, gid, list(shapes))
    vox.enclosed_nodes = enclosed
    return vox


def _fill_enclosed(mask: np.ndarray, grid: Grid, node: np.ndarray, q: np.ndarray, t: np.ndarray, own: np.ndarray,
                   shapes: Sequence[Shape], ground: bool) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    """Fluid nodes the outside air cannot reach through uncut links become solid.

    A sealed pocket has no way to exchange mass, so its pressure wanders and pushes on the walls around it
    (in the first v5 run, thousands of newtons of false force)."""
    if len(node) == 0:
        return node, q, t, own, 0
    i, j, k = grid.ijk(node)
    lo = np.maximum(np.array([i.min(), j.min(), k.min()]) - 2, 0)
    hi = np.minimum(np.array([i.max(), j.max(), k.max()]) + 2, np.array([grid.nx, grid.ny, grid.nz]) - 1)
    size = hi - lo + 1  # x, y, z
    shape3 = (size[2], size[1], size[0])
    X, Y = grid.nx + 2, grid.ny + 2
    m3 = mask.reshape(grid.nz + 2, Y, X)[lo[2] + 1:hi[2] + 2, lo[1] + 1:hi[1] + 2, lo[0] + 1:hi[0] + 2]
    fluid = m3 == 0
    solid_shape = np.array([s.reflect >= 1.0 for s in shapes], bool) if len(shapes) else np.zeros(0, bool)
    blocked = np.zeros((19,) + shape3, bool)
    hard = solid_shape[own]
    blocked[q[hard], k[hard] - lo[2], j[hard] - lo[1], i[hard] - lo[0]] = True
    reach = np.zeros(shape3, bool)
    reach[:, :, 0] = reach[:, :, -1] = True
    reach[:, 0, :] = reach[:, -1, :] = True
    reach[-1, :, :] = True
    if not (ground and lo[2] == 0):
        reach[0, :, :] = True
    reach &= fluid
    frontier = reach.copy()
    while frontier.any():
        new = np.zeros(shape3, bool)
        for qq in range(1, 19):
            cx, cy, cz = (int(v) for v in C[qq])
            # from x (frontier) to y = x + c
            src = frontier[max(0, -cz):shape3[0] - max(0, cz), max(0, -cy):shape3[1] - max(0, cy), max(0, -cx):shape3[2] - max(0, cx)]
            view = (slice(max(0, cz), shape3[0] - max(0, -cz)), slice(max(0, cy), shape3[1] - max(0, -cy)), slice(max(0, cx), shape3[2] - max(0, -cx)))
            back = (slice(max(0, -cz), shape3[0] - max(0, cz)), slice(max(0, -cy), shape3[1] - max(0, cy)), slice(max(0, -cx), shape3[2] - max(0, cx)))
            new[view] |= src & ~blocked[qq][view] & ~blocked[OPP[qq]][back]
        new &= fluid & ~reach
        reach |= new
        frontier = new
    pocket = fluid & ~reach
    count = int(pocket.sum())
    if count:
        m3[pocket] = 1
        keep = mask[node] == 0
        node, q, t, own = node[keep], q[keep], t[keep], own[keep]
    return node, q, t, own, count


def _nearest_owner(shapes: Sequence[Shape], grid: Grid, node: np.ndarray, q: np.ndarray) -> np.ndarray:
    """The shape holding the solid neighbour of each link."""
    out = np.zeros(len(node), int)
    p = grid.position(node) - C[q] * grid.dx
    for i, s in reversed(list(enumerate(shapes))):
        if s.reflect < 1.0:
            continue
        out[s.inside(p)] = i
    return out


@dataclass
class LinkArrays:
    """What the kernels read, sorted by group and chunked."""

    slot_a: np.ndarray
    slot_b: np.ndarray
    slot_d: np.ndarray
    a: np.ndarray
    b: np.ndarray
    dh: np.ndarray
    r: np.ndarray
    dir: np.ndarray
    arm: np.ndarray
    chunk_start: np.ndarray
    chunk_group: np.ndarray
    interpolated_share: float


def link_arrays(vox: Voxels, ref_point: np.ndarray, velocity_scale: float, interpolate: bool = True, chunk: int = 32) -> LinkArrays:
    """Kernel arrays of the cut links. velocity_scale turns m/s into lattice units."""
    g = vox.grid
    N = g.n_total
    sx, sy, sz = g.strides
    stride = C @ np.array([sx, sy, sz], dtype=np.int64)
    gid = vox.group_of_shape[vox.shape] if len(vox.shape) else np.zeros(0, int)
    order = np.argsort(gid, kind="stable")
    node, q, t, own, gid = vox.node[order], vox.dir[order], vox.q[order], vox.shape[order], gid[order]
    opp = OPP[q]
    slot_a = opp * N + node
    slot_d = q * N + (node - stride[q])
    a = np.ones(len(node), np.float32)
    b = np.zeros(len(node), np.float32)
    slot_b = slot_a.copy()
    have = np.sort(node * 19 + q)
    corr = np.ones(len(node), np.float64)
    used = np.zeros(len(node), bool)
    if interpolate and len(node):
        # the second node for t < 1/2 is the next one away from the wall
        n2 = node + stride[q]
        fluid2 = vox.mask[n2] == 0
        pos = np.searchsorted(have, n2 * 19 + q)
        pos = np.minimum(pos, len(have) - 1)
        free = have[pos] != n2 * 19 + q
        near = t < 0.5
        use_near = near & fluid2 & free
        a[use_near] = (2.0 * t[use_near]).astype(np.float32)
        b[use_near] = (1.0 - 2.0 * t[use_near]).astype(np.float32)
        slot_b[use_near] = opp[use_near] * N + n2[use_near]
        far = ~near
        a[far] = (0.5 / t[far]).astype(np.float32)
        b[far] = ((2.0 * t[far] - 1.0) / (2.0 * t[far])).astype(np.float32)
        slot_b[far] = q[far] * N + node[far]
        corr[far] = 0.5 / t[far]
        used = use_near | far
    # the hit point and the wall velocity there
    p = g.position(node)
    hit = p - (t[:, None] * C[q]) * g.dx
    r = np.ones(len(node), np.float32)
    dh = np.zeros(len(node), np.float32)
    for s_id in np.unique(own):
        shape = vox.shapes[int(s_id)]
        idx = np.flatnonzero(own == s_id)
        r[idx] = shape.reflect
        if shape.velocity is not None:
            u = shape.velocity(hit[idx]) * velocity_scale
            cu = (C[q[idx]] * u).sum(axis=1)
            dh[idx] = (6.0 * W[q[idx]] * cu * corr[idx]).astype(np.float32)
    if len(node) and np.any(dh):
        # A moving wall's term carries momentum but must not add mass: on a staircase the terms of one node's
        # links do not cancel, and a wheel of a few cells radius became a strong source and sink (density
        # +-8 % in the first v5 run). Each node's links share out their sum.
        un, inv, counts = np.unique(node, return_inverse=True, return_counts=True)
        net = np.bincount(inv, weights=dh.astype(np.float64), minlength=len(un))
        dh = dh - (net[inv] / counts[inv]).astype(np.float32)
    arm = ((hit - ref_point) / g.dx).astype(np.float32)
    # chunks that stay inside one group
    starts: list[int] = []
    groups: list[int] = []
    for g_id in np.unique(gid):
        idx = np.flatnonzero(gid == g_id)
        for s in range(idx[0], idx[-1] + 1, chunk):
            starts.append(int(s))
            groups.append(int(g_id))
    starts.append(len(node))
    return LinkArrays(slot_a.astype(np.uint32), slot_b.astype(np.uint32), slot_d.astype(np.uint32), a, b, dh, r,
                      q.astype(np.uint8), arm, np.array(starts, np.uint32), np.array(groups, np.uint16),
                      float(used.mean()) if len(node) else 0.0)


def geometry_report(vox: Voxels, wind: np.ndarray | None = None, samples_per_cell: int = 4) -> dict[str, Any]:
    """Solid volume and projected areas the grid sees, against the shapes sampled finer than the grid."""
    g = vox.grid
    dx = g.dx
    solid_nodes = int((vox.mask == 1).sum())
    report: dict[str, Any] = {"solid_nodes": solid_nodes, "cut_links": vox.n_links, "dx_m": dx, "enclosed_nodes_filled": vox.enclosed_nodes,
                              "voxel_volume_m3": solid_nodes * dx ** 3}
    solids = [s for s in vox.shapes if s.reflect >= 1.0]
    if not solids:
        return report
    lo = np.min([s.bounds()[0] for s in solids], axis=0)
    hi = np.max([s.bounds()[1] for s in solids], axis=0)
    h = dx / samples_per_cell
    # projected area along x and y on a fine grid of rays, by columns (each ray: does any shape meet it?)
    for axis, label in ((0, "x"), (1, "y"), (2, "z")):
        others = [a for a in range(3) if a != axis]
        u = np.arange(lo[others[0]] + h / 2, hi[others[0]], h)
        v = np.arange(lo[others[1]] + h / 2, hi[others[1]], h)
        uu, vv = np.meshgrid(u, v, indexing="ij")
        p = np.zeros((uu.size, 3))
        p[:, others[0]] = uu.ravel()
        p[:, others[1]] = vv.ravel()
        p[:, axis] = lo[axis] - 1.0
        d = np.zeros(3)
        d[axis] = 1.0
        hit = np.zeros(len(p), bool)
        for s in solids:
            t_in, t_out = s.clip(p, d)
            hit |= t_in <= t_out
        exact = float(hit.sum()) * h * h
        # the grid: a line of nodes along the axis is blocked when any of its axial links is cut or a node is solid
        seen = _blocked_lines(vox, axis)
        report[f"projected_area_{label}_m2"] = exact
        report[f"grid_projected_area_{label}_m2"] = seen * dx * dx
        report[f"grid_projected_area_{label}_error_pct"] = 100.0 * (seen * dx * dx - exact) / exact if exact > 0 else None
    # volume of the union on sample points
    nz_step = h
    xs = np.arange(lo[0] + h / 2, hi[0], nz_step)
    ys = np.arange(lo[1] + h / 2, hi[1], nz_step)
    zs = np.arange(lo[2] + h / 2, hi[2], nz_step)
    count = 0
    for z in zs:
        xx, yy = np.meshgrid(xs, ys, indexing="ij")
        pts = np.column_stack([xx.ravel(), yy.ravel(), np.full(xx.size, z)])
        ins = np.zeros(len(pts), bool)
        for s in solids:
            slo, shi = s.bounds()
            if slo[2] > z or shi[2] < z:
                continue
            ins |= s.inside(pts)
        count += int(ins.sum())
    exact_v = count * h ** 3
    report["shape_volume_m3"] = exact_v
    report["voxel_volume_error_pct"] = 100.0 * (solid_nodes * dx ** 3 - exact_v) / exact_v if exact_v > 0 else None
    # which shapes does the grid see?
    seen_shapes = np.zeros(len(vox.shapes), bool)
    if len(vox.shape):
        seen_shapes[np.unique(vox.shape)] = True
    report["shapes"] = len(vox.shapes)
    report["shapes_with_cut_links"] = int(seen_shapes.sum())
    report["shapes_without_cut_links"] = [s.name for i, s in enumerate(vox.shapes) if not seen_shapes[i]]
    return report


def _blocked_lines(vox: Voxels, axis: int) -> int:
    """How many lines of nodes along the axis are blocked by a solid node or a cut axial link."""
    g = vox.grid
    q_axial = {0: 1, 1: 3, 2: 5}[axis]
    i, j, k = g.ijk(vox.node[(vox.dir == q_axial) | (vox.dir == OPP[q_axial])])
    lines = {0: (j, k, g.nx + 2), 1: (i, k, g.nx + 2), 2: (i, j, g.nx + 2)}[axis]
    keys = lines[0] + 1 + (lines[1] + 1) * lines[2]
    solid = np.flatnonzero(vox.mask == 1)
    si, sj, sk = g.ijk(solid)
    sl = {0: (sj, sk), 1: (si, sk), 2: (si, sj)}[axis]
    skeys = sl[0] + 1 + (sl[1] + 1) * lines[2]
    return int(len(np.unique(np.concatenate([keys, skeys]))))

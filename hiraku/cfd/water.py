"""A vehicle at constant speed through a shallow flooded road, on the GPU. It is judged only on whether water reaches the battery, the
motor and the controller (test F5), by a calculation not compared with an experiment (STATEMENT).

The water is MLS-MPM (Hu et al. 2018, DOI 10.1145/3197517.3201293): particles carry it, a grid solves it each step, with
quadratic B-splines and APIC transfers. It is weakly compressible with a Tait equation of state. Why these:
  - particles keep the free surface, the thrown water and the road going dry without any surface tracking;
  - transfers and pressure forces keep total momentum exactly (only float32 rounding remains), and each change a boundary
    makes to a node's velocity is summed in double, so the water's momentum can be held against the impulses given to it;
  - the volume ratio is tracked per particle rather than summed from neighbours: still water stays quiet, with no noise
    from uneven particle spacing.

The collision shapes are the objects hiraku.cfd.vehicle.design_shapes builds, packed into a table (shape_table): each grid
node looks them up by signed distance. Nothing here says what depth a vehicle can cross, and the record says so.
"""

from __future__ import annotations

import ctypes as ct
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

from hiraku.cfd.cuda import Gpu, gpu_memory_mib
from hiraku.cfd.geometry import Cylinder, Polytope, Shape
from hiraku.cfd.solver import check_memory
from hiraku.cfd.vehicle import design_shapes

STATEMENT = "この判定は電池、モーター、制御器が水に浸かるかだけを見る (しぶきは数えない)。実験と比べていない計算による"
PROTECTED_PARTS = ("battery", "motor", "controller")  # the parts test F5 asks water not to reach (a powertrain part named so, or <name>_...)
PROTECTED_JA = {"battery": "電池", "motor": "モーター", "controller": "制御器"}


def part_names_ja(names: Sequence[str]) -> str:
    return "、".join(PROTECTED_JA.get(n.split("_")[0], n) for n in names)

RHO_WATER = 1000.0  # kg/m^3, rounded value for water; 確かめていない
GRAVITY = 9.80665  # m/s^2, standard gravity; 確かめていない
TAIT_GAMMA = 7  # fixed in water.cu, where the power is written out
# Reduced sound speed over the vehicle speed. The rule (see SOURCES) asks for more than ten times the largest velocity. Measured
# at 18 km/h on a 30 mm grid, sound speed over the largest particle speed was 2.1 at 50 m/s, 4.0 at 100 m/s, and 12.6 (wheels
# turning) or 10.0 (not turning) at 200 m/s: hence 40 times the vehicle speed.
SOUND_OVER_SPEED = 40.0
CFL = 0.4  # chosen here and checked by the runs; no outside source
E_MAX = 0.02  # how much expansion a particle remembers; chosen here, no outside source
E_MIN = -0.15  # a guard against collapse; chosen here, no outside source
DENSITY_LIMIT = 1.1  # over rest density. Chosen from a run: at t = 0.4 s, 99.9 % of the particles saw at most 1.03 and 0.03 % above 1.1
CROWD = 0.1  # share of the excess over the limit that becomes compression; chosen here, no outside source
SEED_JITTER = 0.25  # of the particle spacing; chosen here, no outside source
FRAME_DT = 1.0 / 30.0
PARTICLE_FLOATS = 16

SOURCES = {
    "method": "Hu et al. 2018, A Moving Least Squares Material Point Method with Displacement Discontinuity and Two-Way Rigid Body "
              "Coupling, ACM TOG 37(4), DOI 10.1145/3197517.3201293 (論文の所在を確かめた)",
    "equation_of_state": "Tait 型 p = K/7 (J^-7 - 1)、指数 7: Wei, Shi, Li, Katz, Dalrymple, Bilotta, Behavior of Oil under Breaking Waves by a Two-phase SPH Model, arXiv:1702.07760 で確かめた。"
                         "同論文が引く Monaghan 1992, 1994 の原文は確かめていない",
    "sound_speed": "音速を最大速度の 10 倍より大きくする目安は Monaghan 1994 によると同論文 (arXiv:1702.07760) にある。原文は確かめていない。"
                   f"ここでは音速を車速の {SOUND_OVER_SPEED:.0f} 倍とした。時速 18 km、格子 30 mm の実測で、粒子の最大の速さの 10.0 倍 (車輪を回さない) と 12.6 倍 (回す)",
    "density": "水の密度 1000 kg/m^3 は丸めた値。確かめていない",
    "gravity": "標準重力 9.80665 m/s^2。確かめていない",
    "numerical": f"CFL {CFL}、e の上限 {E_MAX}、下限 {E_MIN}、密度の上限 {DENSITY_LIMIT} 倍、その超過を圧縮に変える割合 {CROWD} はここで決めた計算上の値で、外の出典はない",
}


# ---------------------------------------------------------------- equations (plain numpy, tested without a GPU)

def tait_pressure(e: Any, bulk: float) -> np.ndarray:
    """Pressure (Pa, no tension) of a volume ratio J = 1 + e: K / 7 (J^-7 - 1), the form water.cu computes."""
    e = np.asarray(e, float)
    y = -e / (1.0 + e)
    power = y * (7.0 + y * (21.0 + y * (35.0 + y * (35.0 + y * (21.0 + y * (7.0 + y))))))
    return np.maximum(bulk / 7.0 * power, 0.0)


def hydrostatic_e(pressure: Any, bulk: float) -> np.ndarray:
    """The e whose Tait pressure is the given one."""
    return (1.0 + 7.0 * np.asarray(pressure, float) / bulk) ** (-1.0 / 7.0) - 1.0


def spline_weights(f: Any) -> np.ndarray:
    """Quadratic B-spline weights of the three nodes from a particle at f = x / dx - base, with f in [0.5, 1.5)."""
    f = np.asarray(f, float)
    return np.stack([0.5 * (1.5 - f) ** 2, 0.75 - (f - 1.0) ** 2, 0.5 * (f - 0.5) ** 2], axis=-1)


def time_step(dx: float, sound_ms: float, speed_max_ms: float, frame_dt: float = FRAME_DT, cfl: float = CFL) -> tuple[float, int]:
    """(dt, steps per frame): the largest dt under the CFL limit that fits a whole number of steps in a frame."""
    limit = cfl * dx / (sound_ms + speed_max_ms)
    steps = int(math.ceil(frame_dt / limit))
    return frame_dt / steps, steps


@dataclass
class Road:
    """A dip that holds the water: flat at z = 0, rising at the slope on both ends. The water line stays at half_length
    whatever the depth. depth 0 means a flat road without ramps (for the checks)."""

    depth_m: float = 0.0
    half_length_m: float = 4.0
    slope: float = 0.125
    blend_m: float = 0.3  # the corners are rounded over this length, so the vehicle's pitch rate stays finite

    def height(self, x: Any) -> np.ndarray:
        x = np.asarray(x, float)
        if self.depth_m <= 0.0:
            return np.zeros_like(x)
        foot = self.half_length_m - self.depth_m / self.slope  # where the flat part ends
        d = np.abs(x) - foot
        b = self.blend_m
        # slope times the integral of a raised-cosine step of width 2 b centred on the foot
        s = np.where(d <= -b, 0.0, np.where(d >= b, d, 0.5 * (d + b) - b / math.pi * np.cos(0.5 * math.pi * (d + b) / b) * np.sin(0.5 * math.pi * (d + b) / b)))
        return self.slope * s

    def gradient(self, x: Any) -> np.ndarray:
        x = np.asarray(x, float)
        h = 1e-4
        return (self.height(x + h) - self.height(x - h)) / (2.0 * h)


@dataclass
class WaterGrid:
    """Nodes at multiples of dx in a box: x and y centred on zero, z from the road's zero. pad layers outside on all sides."""

    length_m: float
    width_m: float
    height_m: float
    dx: float
    pad: int = 3

    @property
    def nodes(self) -> tuple[int, int, int]:
        p = 2 * self.pad + 1
        return int(round(self.length_m / self.dx)) + p, int(round(self.width_m / self.dx)) + p, int(round(self.height_m / self.dx)) + p

    @property
    def offset(self) -> np.ndarray:
        """Kernel coordinates minus world coordinates."""
        n = self.nodes
        return np.array([0.5 * (n[0] - 1) * self.dx, 0.5 * (n[1] - 1) * self.dx, self.pad * self.dx])

    @property
    def cells(self) -> int:
        n = self.nodes
        return n[0] * n[1] * n[2]


@dataclass
class ShapeTable:
    """The kernels' packed form of a list of shapes, in the shapes' own axes. See Tables in water.cu."""

    planes: np.ndarray
    shapes: np.ndarray
    bands: np.ndarray
    cyl: np.ndarray
    start: np.ndarray
    items: np.ndarray
    lo: np.ndarray
    hi: np.ndarray
    cell: float
    counts: tuple[int, int, int]
    groups: list[str]
    solids: list[Shape]
    left_out: list[str] = field(default_factory=list)

    @property
    def nbytes(self) -> int:
        return sum(a.nbytes for a in (self.planes, self.shapes, self.bands, self.cyl, self.start, self.items))


def spin_of(s: Shape) -> np.ndarray:
    """The angular velocity behind a shape's wall velocity: omega = 1/2 sum e_i x v(c + e_i)."""
    if s.velocity is None or not isinstance(s, Cylinder):
        return np.zeros(3)
    e = np.eye(3)
    v = np.asarray(s.velocity(s.center + e), float)
    return 0.5 * np.cross(e, v).sum(axis=0)


def widths_of(s: Shape) -> np.ndarray:
    """The widths of a shape across its own faces, smallest first."""
    if isinstance(s, Cylinder):
        return np.sort([2.0 * s.radius, 2.0 * s.radius, 2.0 * s.half])
    p: Any = s
    w = np.sort(p.offsets - (p.vertices @ p.normals.T).min(axis=0))
    return w[::2] if len(w) == 6 else w  # a box or a rectangular tube has each width twice


def thickness_of(s: Shape) -> float:
    return float(widths_of(s)[0])


def band_of(s: Shape, dx: float, plate_cells: float = 4.0) -> float:
    """How far from a thin shape a node still counts, so the grid does not miss it. A plate (wide in two directions) must hold
    water on both faces, so a row of nodes on each side counts. A bar only needs one row: two would make a 25 mm arm block like
    an 80 mm one, and the arms of a vehicle would close into a blade across the flow."""
    w = widths_of(s)
    if len(w) > 1 and w[1] >= plate_cells * dx:
        return max(0.0, dx - 0.5 * float(w[0]))
    return max(0.0, 0.5 * (dx - float(w[0])))


def shape_table(shapes: Sequence[Shape], dx: float, top: float | None = None, cell: float = 0.1, sticky_groups: Sequence[str] = ("wheels",)) -> ShapeTable:
    """Pack the solid shapes. A net (reflect below 1) lets water through. A part wholly above `top` is left out only because
    no node reaches it. Thin shapes get a band (band_of)."""
    solids, left = [], []
    for s in shapes:
        if s.reflect < 1.0:
            left.append(s.name)
        elif top is not None and s.bounds()[0][2] > top:
            left.append(s.name)
        else:
            solids.append(s)
    groups: list[str] = []
    planes, cyl, rows, bands = [], [], [], []
    for s in solids:
        if s.group not in groups:
            groups.append(s.group)
        g = groups.index(s.group)
        sticky = 1 if s.group in sticky_groups else 0
        bands.append(band_of(s, dx))
        if isinstance(s, Polytope):
            rows.append((sum(len(a) for a in planes), len(s.offsets), g, sticky))
            planes.append(np.column_stack([s.normals, s.offsets]))
        elif isinstance(s, Cylinder):
            rows.append((len(cyl), 0, g, sticky))
            cyl.append(np.concatenate([s.center, s.axis, [s.radius, s.half], spin_of(s), [0.0]]))
        else:
            raise TypeError(f"shape_table: {type(s).__name__} is not a polytope or a cylinder")
    band = np.array(bands, float) if bands else np.zeros(0)
    los = np.array([s.bounds()[0] for s in solids], float).reshape(-1, 3) - band[:, None] - 1e-4
    his = np.array([s.bounds()[1] for s in solids], float).reshape(-1, 3) + band[:, None] + 1e-4
    lo = los.min(axis=0) if len(solids) else np.zeros(3)
    hi = his.max(axis=0) if len(solids) else np.zeros(3)
    counts = tuple(int(v) for v in np.maximum(np.ceil((hi - lo) / cell), 1).astype(int))
    keys, owners = [], []
    for i in range(len(solids)):
        a = np.floor((los[i] - lo) / cell).astype(int)
        b = np.minimum(np.floor((his[i] - lo) / cell).astype(int), np.array(counts) - 1)
        ii, jj, kk = np.meshgrid(np.arange(a[0], b[0] + 1), np.arange(a[1], b[1] + 1), np.arange(a[2], b[2] + 1), indexing="ij")
        key = (ii + counts[0] * (jj + counts[1] * kk)).ravel()
        keys.append(key)
        owners.append(np.full(key.size, i))
    key = np.concatenate(keys) if keys else np.zeros(0, int)
    own = np.concatenate(owners) if owners else np.zeros(0, int)
    order = np.argsort(key, kind="stable")
    total = counts[0] * counts[1] * counts[2]
    start = np.concatenate([[0], np.cumsum(np.bincount(key, minlength=total))])
    return ShapeTable(np.vstack(planes).astype(np.float32) if planes else np.zeros((0, 4), np.float32),
                      np.array(rows, np.int32).reshape(-1, 4), band.astype(np.float32),
                      np.array(cyl, np.float32).reshape(-1, 12), start.astype(np.uint32), own[order].astype(np.uint32),
                      lo, hi, cell, counts, groups, solids, left)


def table_distance(table: ShapeTable, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The table read the way the kernels read it, in numpy: (signed distance of the deepest shape holding the point, or
    1e9; its index, or -1). No lookup cells: every shape is tried."""
    pts = np.asarray(points, float)
    best = np.full(len(pts), 1e9)
    who = np.full(len(pts), -1)
    for s, row in enumerate(table.shapes):
        first, count = int(row[0]), int(row[1])
        if count:
            pl = table.planes[first:first + count].astype(float)
            d = (pts @ pl[:, :3].T - pl[:, 3]).max(axis=1)
        else:
            c = table.cyl[first].astype(float)
            r = pts - c[:3]
            along = r @ c[3:6]
            q = r - np.outer(along, c[3:6])
            d = np.maximum(np.linalg.norm(q, axis=1) - c[6], np.abs(along) - c[7])
        hit = (d < 0.0) & (d < best)
        best[hit] = d[hit]
        who[hit] = s
    return best, who


def particles_inside(shapes: Sequence[Shape], points: np.ndarray) -> np.ndarray:
    """Which points the Shape objects themselves hold: the objects the picture is drawn from."""
    inside = np.zeros(len(points), bool)
    for s in shapes:
        lo, hi = s.bounds()
        near = np.flatnonzero(np.all((points >= lo) & (points <= hi), axis=1))
        if len(near):
            inside[near[s.inside(points[near])]] = True
    return inside


# ---------------------------------------------------------------- water and vehicle path

def seed_water(grid: WaterGrid, road: Road, depth_m: float, bulk: float, rho: float = RHO_WATER, gravity: float = GRAVITY,
               x_range: tuple[float, float] | None = None, y_range: tuple[float, float] | None = None, jitter: float = 0.0, rng_seed: int = 1) -> tuple[np.ndarray, float]:
    """Particles on a lattice of dx / 2 (8 per cell) below the level, at the hydrostatic volume ratio; (N, 16) in kernel
    coordinates, and the level reached: a whole number of particle layers. jitter moves each one by up to that share of the
    lattice spacing in all, so flying water does not show the lattice."""
    h = 0.5 * grid.dx
    rng = np.random.default_rng(rng_seed)
    layers = int(round(depth_m / h))
    level = layers * h
    xr = x_range or (-0.5 * grid.length_m, 0.5 * grid.length_m)
    yr = y_range or (-0.5 * grid.width_m, 0.5 * grid.width_m)
    xs = np.arange(xr[0] + 0.5 * h, xr[1], h)
    ys = np.arange(yr[0] + 0.5 * h, yr[1], h)
    zs = (np.arange(layers) + 0.5) * h
    ground = road.height(xs)
    out = []
    for i, x in enumerate(xs):
        z = zs[zs > ground[i]]
        if not len(z):
            continue
        yy, zz = np.meshgrid(ys, z, indexing="ij")
        part = np.zeros((yy.size, PARTICLE_FLOATS), np.float32)
        shift = rng.uniform(-0.5 * jitter * h, 0.5 * jitter * h, size=(yy.size, 3)) if jitter else np.zeros((yy.size, 3))
        height = np.maximum(zz.ravel() + shift[:, 2], ground[i])
        part[:, 0] = x + shift[:, 0] + grid.offset[0]
        part[:, 1] = yy.ravel() + shift[:, 1] + grid.offset[1]
        part[:, 2] = height + grid.offset[2]
        part[:, 15] = hydrostatic_e(rho * gravity * np.maximum(level - height, 0.0), bulk)
        out.append(part)
    return (np.concatenate(out) if out else np.zeros((0, PARTICLE_FLOATS), np.float32)), level


@dataclass
class Pose:
    x: float
    z: float
    pitch: float
    vx: float
    vz: float
    rate: float


class VehiclePath:
    """Constant speed along -x on the road: both axles stay on it, the rest of the vehicle follows rigidly."""

    def __init__(self, road: Road, speed_ms: float, x_start: float, x_front: float, x_rear: float):
        self.road, self.speed, self.x0 = road, speed_ms, x_start
        self.x_front, self.x_rear = x_front, x_rear

    def state(self, t: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        x = self.x0 - self.speed * np.asarray(t, float)
        gf = self.road.height(x + self.x_front)
        gr = self.road.height(x + self.x_rear)
        s = (gf - gr) / (self.x_rear - self.x_front)
        return x, 0.5 * (gf + gr), np.arcsin(s)

    def pose(self, t: float) -> Pose:
        x, z, p = self.state(t)
        h = 1e-4
        _, z1, p1 = self.state(t + h)
        _, z0, p0 = self.state(t - h)
        return Pose(float(x), float(z), float(p), -self.speed, float((z1 - z0) / (2.0 * h)), float((p1 - p0) / (2.0 * h)))


def to_vehicle(points: np.ndarray, pose: Pose | tuple[float, float, float]) -> np.ndarray:
    """World points to vehicle axes."""
    x0, z0, pitch = (pose.x, pose.z, pose.pitch) if isinstance(pose, Pose) else pose
    c, s = math.cos(pitch), math.sin(pitch)
    rx, rz = points[:, 0] - x0, points[:, 2] - z0
    return np.column_stack([c * rx - s * rz, points[:, 1], s * rx + c * rz])


# ---------------------------------------------------------------- the GPU case

class Params(ct.Structure):
    _fields_ = [("nx", ct.c_int), ("ny", ct.c_int), ("nz", ct.c_int), ("np", ct.c_int),
                ("pad", ct.c_int), ("vehicle", ct.c_int), ("project", ct.c_int), ("ngroups", ct.c_int),
                ("dx", ct.c_float), ("inv_dx", ct.c_float), ("dt", ct.c_float), ("gravity", ct.c_float),
                ("vol0", ct.c_float), ("mass", ct.c_float), ("k7", ct.c_float), ("e_max", ct.c_float),
                ("e_min", ct.c_float), ("px", ct.c_float), ("py", ct.c_float), ("pz", ct.c_float),
                ("pc", ct.c_float), ("ps", ct.c_float), ("pvx", ct.c_float), ("pvz", ct.c_float),
                ("pw", ct.c_float), ("lox", ct.c_float), ("loy", ct.c_float), ("loz", ct.c_float),
                ("hix", ct.c_float), ("hiy", ct.c_float), ("hiz", ct.c_float), ("bp_inv", ct.c_float),
                ("bpx", ct.c_int), ("bpy", ct.c_int), ("bpz", ct.c_int), ("mass_limit", ct.c_float), ("crowd", ct.c_float), ("open_ends", ct.c_int)]


def required_bytes(grid: WaterGrid, particles: int, table: ShapeTable | None) -> int:
    n = grid.cells * 16 + particles * PARTICLE_FLOATS * 4 + 8 * grid.nodes[0]
    n += table.nbytes if table is not None else 0
    return int(n) + 4096


class WaterSim:
    """Water on the GPU. step() advances it; the arrays are freed by close()."""

    BLOCK = 256

    def __init__(self, gpu: Gpu, grid: WaterGrid, particles: np.ndarray, road: Road, dt: float, sound_ms: float,
                 table: ShapeTable | None = None, project: bool = True, rho: float = RHO_WATER, gravity: float = GRAVITY,
                 e_max: float = E_MAX, density_limit: float = DENSITY_LIMIT, open_ends: bool = False, reserve_mib: int = 600):
        self.gpu, self.grid, self.road, self.dt, self.table = gpu, grid, road, dt, table
        self.np = len(particles)
        self.memory = check_memory(required_bytes(grid, self.np, table), reserve_mib)
        self.bulk = rho * sound_ms ** 2
        self.mass = rho * (0.5 * grid.dx) ** 3
        self.ngroups = len(table.groups) if table is not None else 0
        nx, ny, nz = grid.nodes
        p = Params()
        p.nx, p.ny, p.nz, p.np = nx, ny, nz, self.np
        p.pad, p.vehicle, p.project = grid.pad, 1 if table is not None else 0, 1 if (table is not None and project) else 0
        p.ngroups = self.ngroups
        p.dx, p.inv_dx, p.dt, p.gravity = grid.dx, 1.0 / grid.dx, dt, gravity
        p.vol0, p.mass, p.k7 = (0.5 * grid.dx) ** 3, self.mass, self.bulk / 7.0
        p.e_max, p.e_min = e_max, E_MIN
        p.mass_limit, p.crowd = density_limit * rho * grid.dx ** 3, CROWD
        p.open_ends = 1 if open_ends else 0
        p.pc, p.ps = 1.0, 0.0
        if table is not None:
            p.lox, p.loy, p.loz = (float(v) for v in table.lo)
            p.hix, p.hiy, p.hiz = (float(v) for v in table.hi)
            p.bp_inv = 1.0 / table.cell
            p.bpx, p.bpy, p.bpz = table.counts
        self.p = p
        self.buf: dict[str, Any] = {}
        self.buf["part"] = gpu.buffer(max(particles.nbytes, 4))
        self.buf["part"].write(particles)
        self.buf["grid"] = gpu.buffer(grid.cells * 16)
        self.buf["grid"].zero()
        xw = (np.arange(nx) * grid.dx) - grid.offset[0]
        self.buf["gz"] = gpu.buffer(4 * nx)
        self.buf["gz"].write((road.height(xw) + grid.offset[2] + 1e-6).astype(np.float32))
        self.buf["gs"] = gpu.buffer(4 * nx)
        self.buf["gs"].write(road.gradient(xw).astype(np.float32))
        arrays = {"planes": None, "shapes": None, "bands": None, "cyl": None, "start": None, "items": None}
        for name in arrays:
            arr = getattr(table, name) if table is not None else np.zeros(1, np.float32)
            self.buf[name] = gpu.buffer(max(arr.nbytes, 4))
            if arr.nbytes:
                self.buf[name].write(arr)
        self.buf["imp"] = gpu.buffer(8 * 3 * (self.ngroups + 2))
        self.buf["imp"].zero()
        self._build()
        self.steps = 0

    def _ptr(self, name: str) -> ct.c_ulonglong:
        return ct.c_ulonglong(self.buf[name].ptr.value)

    def _tables(self) -> list[ct.c_ulonglong]:
        return [self._ptr(n) for n in ("planes", "shapes", "bands", "cyl", "start", "items")]

    def _build(self) -> None:
        g, b = self.gpu, self.BLOCK
        nx, ny, nz = self.grid.nodes
        rows = (int(math.ceil(nx / b)), ny, nz)
        each = (max(int(math.ceil(self.np / b)), 1), 1, 1)
        self.l_clear = g.launch("grid_clear", [self._ptr("grid"), self.p], rows, (b, 1, 1))
        self.l_p2g = g.launch("p2g", [self._ptr("part"), self._ptr("grid"), self.p], each, (b, 1, 1))
        self.l_update = g.launch("grid_update", [self._ptr("grid"), self._ptr("gz"), self._ptr("gs"), *self._tables(), self._ptr("imp"), self.p], rows, (b, 1, 1))
        self.l_g2p = g.launch("g2p", [self._ptr("part"), self._ptr("grid"), self._ptr("gz"), *self._tables(), self._ptr("imp"), self.p], each, (b, 1, 1))

    def set_pose(self, pose: Pose, y: float = 0.0) -> None:
        o = self.grid.offset
        p = self.p
        p.px, p.py, p.pz = pose.x + o[0], y + o[1], pose.z + o[2]
        p.pc, p.ps = math.cos(pose.pitch), math.sin(pose.pitch)
        p.pvx, p.pvz, p.pw = pose.vx, pose.vz, pose.rate

    def step(self, n: int = 1, pose_at: Callable[[int], Pose] | None = None) -> None:
        """pose_at(step) gives the vehicle at the end of that step."""
        clear, p2g, update, g2p = self.l_clear, self.l_p2g, self.l_update, self.l_g2p
        for _ in range(n):
            self.steps += 1
            if pose_at is not None:
                self.set_pose(pose_at(self.steps))
            clear()
            p2g()
            update()
            g2p()
            if self.steps % 16 == 0:
                self.gpu.sync()
        self.gpu.sync()

    def read_particles(self) -> np.ndarray:
        self.gpu.sync()
        out = np.empty((self.np, PARTICLE_FLOATS), np.float32)
        self.buf["part"].read_into(out)
        return out

    def read_impulse(self) -> np.ndarray:
        """(groups + 2, 3): the impulse given to the water so far by each group of shapes, the road and the walls (N s)."""
        self.gpu.sync()
        out = np.zeros((self.ngroups + 2, 3), np.float64)
        self.buf["imp"].read_into(out)
        return out

    def momentum(self, particles: np.ndarray | None = None) -> np.ndarray:
        """The water's momentum (N s), summed in double."""
        part = self.read_particles() if particles is None else particles
        return self.mass * part[:, 3:6].sum(axis=0, dtype=np.float64)

    def probe(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """The table on the GPU at points in vehicle axes: (signed distance, or 1e9 outside all shapes; shape index, or -1)."""
        pts = np.asarray(points, np.float32)
        a, b = self.gpu.buffer(pts.nbytes), self.gpu.buffer(8 * len(pts))
        try:
            a.write(pts)
            count = ct.c_int(len(pts))
            launch = self.gpu.launch("probe", [ct.c_ulonglong(a.ptr.value), ct.c_ulonglong(b.ptr.value), *self._tables(), count, self.p],
                                     (int(math.ceil(len(pts) / self.BLOCK)), 1, 1), (self.BLOCK, 1, 1))
            launch()
            self.gpu.sync()
            out = np.zeros((len(pts), 2), np.float32)
            b.read_into(out)
        finally:
            a.free()
            b.free()
        return out[:, 0].astype(np.float64), np.rint(out[:, 1]).astype(np.int64)

    def close(self) -> None:
        for b in self.buf.values():
            b.free()
        self.buf = {}


# ---------------------------------------------------------------- the required checks: each returns what it measured

def check_still_water(gpu: Gpu, dx: float = 0.025, depth_m: float = 0.2, sound_ms: float = 200.0, seconds: float = 1.0) -> dict[str, float]:
    """Check (a): still water, seeded as the runs seed it, left alone. Does the level stay, and does the water come to rest?"""
    grid = WaterGrid(0.8, 0.4, 0.4, dx)
    road = Road()
    part, level = seed_water(grid, road, depth_m, RHO_WATER * sound_ms ** 2, jitter=SEED_JITTER)
    dt, per_frame = time_step(dx, sound_ms, 0.0)
    z0 = part[:, 2].astype(np.float64) - grid.offset[2]
    sim = WaterSim(gpu, grid, part, road, dt, sound_ms)
    try:
        sim.step(int(round(seconds / (per_frame * dt))) * per_frame)
        q = sim.read_particles()
    finally:
        sim.close()
    z = q[:, 2].astype(np.float64) - grid.offset[2]
    speed = np.sqrt((q[:, 3:6].astype(np.float64) ** 2).sum(axis=1))
    return {"seconds": seconds, "particles": len(q), "level_m": level, "mean_height_change_mm": (float(z.mean()) - float(z0.mean())) * 1000.0,
            "top_change_mm": (float(np.percentile(z, 99.9)) - float(np.percentile(z0, 99.9))) * 1000.0, "max_speed_ms": float(speed.max()),
            "rms_speed_ms": float(np.sqrt((speed ** 2).mean())), "long_wave_speed_ms": math.sqrt(GRAVITY * depth_m), "not_finite": int((~np.isfinite(q)).sum())}


def check_moving_box(gpu: Gpu, dx: float = 0.025, depth_m: float = 0.12, sound_ms: float = 200.0, speed_ms: float = 1.2, seconds: float = 0.4, open_ends: bool = False,
                     x0: float = 0.9) -> dict[str, float]:
    """Check (b): a box driven through water. Is the water's momentum the impulse summed from each node's velocity change, with
    no factor? Along x: the box and the walls. Along z: the box, the road, the walls and the weight. With open_ends, the walls'
    row holds what the water that left the box carried away."""
    from hiraku.cfd.geometry import box

    grid = WaterGrid(2.5, 0.5, 0.5, dx)
    road = Road()
    part, _ = seed_water(grid, road, depth_m, RHO_WATER * sound_ms ** 2, jitter=SEED_JITTER)
    block = box((0.0, 0.0, 0.15), (0.16, 0.3, 0.3), name="block")
    table = shape_table([block], dx, top=grid.height_m)
    pose_at = lambda n: Pose(x0 - speed_ms * n * dt, 0.0, 0.0, -speed_ms, 0.0, 0.0)
    world = part[:, :3].astype(np.float64) - grid.offset
    dt, per_frame = time_step(dx, sound_ms, speed_ms)
    part = part[~particles_inside([block], to_vehicle(world, pose_at(0)))]
    sim = WaterSim(gpu, grid, part, road, dt, sound_ms, table, open_ends=open_ends)
    try:
        sim.set_pose(pose_at(0))
        start = sim.momentum()
        steps = int(round(seconds / (per_frame * dt))) * per_frame
        sim.step(steps, pose_at)
        q = sim.read_particles()
        given = sim.momentum(q) - start
        imp = sim.read_impulse()
    finally:
        sim.close()
    weight = -sim.mass * sim.np * GRAVITY * steps * dt
    box_x, road_x, walls_x = (float(imp[i, 0]) for i in range(3))
    left = np.abs(q[:, 0].astype(np.float64) - grid.offset[0]) > 0.5 * grid.length_m
    return {"seconds": steps * dt, "particles": sim.np, "particles_left_box": int(left.sum()), "box_impulse_x_ns": box_x, "road_impulse_x_ns": road_x, "walls_impulse_x_ns": walls_x, "water_momentum_x_ns": float(given[0]),
            "error_x_over_box": abs(float(given[0]) - (box_x + road_x + walls_x)) / abs(box_x), "error_y_over_box": abs(float(given[1]) - float(imp[:, 1].sum())) / abs(box_x),
            "error_z_over_weight": abs(float(given[2]) - (weight + float(imp[:, 2].sum()))) / abs(weight)}


def check_standing_vehicle(gpu: Gpu, design: dict[str, Any], dx: float = 0.04, depth_m: float = 0.2, sound_ms: float = 200.0, seconds: float = 0.4, project: bool = True, points: int = 20000,
                           keep_particles: bool = False) -> dict[str, Any]:
    """Check (c): are the drawn and the collision shapes the same? The table on the GPU against the numpy reading of it and the
    Shape objects at random points, then water left to settle around the standing vehicle and counted against the Shape objects."""
    shapes, _ = design_shapes(design, 0.0, 1e-4, "two_cargo", rotate_wheels=True, dx=0.0)
    top = 0.6
    table = shape_table(shapes, dx, top=top)
    grid = WaterGrid(3.6, 1.8, top, dx)
    road = Road()
    part, _ = seed_water(grid, road, depth_m, RHO_WATER * sound_ms ** 2, jitter=SEED_JITTER)
    pose = Pose(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    world = part[:, :3].astype(np.float64) - grid.offset
    start_inside = particles_inside(table.solids, to_vehicle(world, pose))
    part = part[~start_inside]
    dt, per_frame = time_step(dx, sound_ms, 0.0)
    pts = np.random.default_rng(9).uniform(table.lo, table.hi, size=(points, 3))
    sim = WaterSim(gpu, grid, part, road, dt, sound_ms, table, project=project)
    try:
        sim.set_pose(pose)
        gpu_d, _ = sim.probe(pts)
        sim.step(int(round(seconds / (per_frame * dt))) * per_frame)
        q = sim.read_particles()
    finally:
        sim.close()
    cpu_d, _ = table_distance(table, pts.astype(np.float32).astype(np.float64))
    held = particles_inside(table.solids, pts.astype(np.float32).astype(np.float64))
    both = (gpu_d < 1e8) & (cpu_d < 1e8)
    local = to_vehicle(q[:, :3].astype(np.float64) - grid.offset, pose)
    inside = particles_inside(table.solids, local)
    speed = np.sqrt((q[:, 3:6].astype(np.float64) ** 2).sum(axis=1))
    out: dict[str, Any] = {"points": points, "shapes_in_table": len(table.solids), "gpu_differs_from_shape_objects": int(((gpu_d < 1e8) != held).sum()), "gpu_differs_from_numpy": int(((gpu_d < 1e8) != (cpu_d < 1e8)).sum()),
                           "largest_distance_gap_m": float(np.abs(gpu_d[both] - cpu_d[both]).max()) if both.any() else 0.0, "seconds": seconds, "particles": len(q), "moved_out_of_shapes": project,
                           "particles_inside_at_start_removed": int(start_inside.sum()), "particles_inside_after": int(inside.sum()), "max_speed_ms": float(speed.max()), "dx_m": dx}
    if keep_particles:
        out["local"] = local
        out["table"] = table
    return out


# ---------------------------------------------------------------- what the particles say about the underfloor

@dataclass
class Underfloor:
    """Columns in vehicle axes. floor: the height of the lowest part in each column (not the tyres); NaN where a tyre stands
    or nothing is above."""

    x0: float
    y0: float
    cell: float
    floor: np.ndarray  # (nx, ny)

    @property
    def lowest_m(self) -> float:
        return float(np.nanmin(self.floor))

    @property
    def columns(self) -> int:
        return int(np.isfinite(self.floor).sum())


def underfloor_columns(solids: Sequence[Shape], cell: float, wheel_group: str = "wheels") -> Underfloor:
    """Rays up from below the road through the shapes themselves. Only columns with a part at or below the underside of the
    body panels count: under a roll bar's top tube, with nothing lower, a wave is not at the underfloor."""
    body = [s for s in solids if s.group != wheel_group]
    wheels = [s for s in solids if s.group == wheel_group]
    panels = [s for s in body if s.group == "body"] or body
    limit = min(float(s.bounds()[0][2]) for s in panels)
    lo = np.min([s.bounds()[0] for s in body], axis=0)
    hi = np.max([s.bounds()[1] for s in body], axis=0)
    xs = np.arange(lo[0] + 0.5 * cell, hi[0], cell)
    ys = np.arange(lo[1] + 0.5 * cell, hi[1], cell)
    xx, yy = np.meshgrid(xs, ys, indexing="ij")
    start = -1.0
    p = np.column_stack([xx.ravel(), yy.ravel(), np.full(xx.size, start)])
    d = np.array([0.0, 0.0, 1.0])
    floor = np.full(xx.size, np.inf)
    for s in body:
        t_in, t_out = s.clip(p, d)
        hit = t_in <= t_out
        floor[hit] = np.minimum(floor[hit], start + np.maximum(t_in[hit], 0.0))
    for s in wheels:
        t_in, t_out = s.clip(p, d)
        floor[t_in <= t_out] = np.inf
    floor[~np.isfinite(floor) | (floor > limit + 1e-9)] = np.nan
    return Underfloor(float(lo[0]), float(lo[1]), cell, floor.reshape(xx.shape))


def underfloor_measure(uf: Underfloor, local: np.ndarray, particle_volume: float, reach_margin: float) -> dict[str, float]:
    """From particles in vehicle axes. depth: the volume of the particles below each column's lowest part over the column's
    area, so air and spray add no height and water above the part does not count. reached: the share of columns with a
    particle at or above reach_margin below their lowest part."""
    nx, ny = uf.floor.shape
    i = np.floor((local[:, 0] - uf.x0) / uf.cell).astype(np.int64)
    j = np.floor((local[:, 1] - uf.y0) / uf.cell).astype(np.int64)
    keep = (i >= 0) & (i < nx) & (j >= 0) & (j < ny)
    flat = i[keep] * ny + j[keep]
    z = local[keep, 2].astype(float)
    floor = uf.floor.ravel()
    below = z < np.nan_to_num(floor, nan=np.inf)[flat]
    count = np.bincount(flat[below], minlength=nx * ny).astype(float)
    top = np.full(nx * ny, -np.inf)
    np.maximum.at(top, flat, z)
    valid = np.isfinite(floor)
    # crowded particles can sum to more than the gap holds; no water is deeper than the gap
    depth = np.minimum(count[valid] * particle_volume / uf.cell ** 2, np.maximum(floor[valid], 0.0))
    reached = top[valid] >= floor[valid] - reach_margin
    return {"depth_mean_mm": float(depth.mean()) * 1000.0, "depth_max_mm": float(depth.max()) * 1000.0, "reached_share": float(reached.mean())}


def wet_parts(solids: Sequence[Shape], local: np.ndarray, margin: float) -> list[str]:
    """The parts with a particle inside them, or inside them when moved by the margin up or down: water seen at this part,
    by the shape's own test."""
    names = []
    for s in solids:
        lo, hi = s.bounds()
        near = np.flatnonzero(np.all((local >= lo - margin) & (local <= hi + margin), axis=1))
        if not len(near):
            continue
        q = local[near]
        below = q + np.array([0.0, 0.0, margin])
        above = q - np.array([0.0, 0.0, margin])
        if s.inside(q).any() or s.inside(below).any() or s.inside(above).any():
            names.append(s.name)
    return names


# ---------------------------------------------------------------- the run

def sound_speed_for(speed_ms: float, depth_m: float, gravity: float = GRAVITY) -> float:
    """SOUND_OVER_SPEED times the larger of the vehicle speed and the long-wave speed sqrt(g h)."""
    return SOUND_OVER_SPEED * max(speed_ms, math.sqrt(gravity * depth_m))


def plan_case(design: dict[str, Any], depth_m: float, speed_ms: float, dx: float, rotate_wheels: bool = True, loading: str | None = "two_cargo",
              length_m: float = 15.0, width_m: float = 3.5, height_m: float = 2.5, water_length_m: float = 8.0, slope: float = 0.125,
              lead_m: float = 0.2, sticky_groups: Sequence[str] = ("wheels",), rng_seed: int = 1, frame_dt: float = FRAME_DT) -> dict[str, Any]:
    """Everything a run needs that takes no GPU. The box is 15 m long, not 12: the pass ends with the rear clear of the water,
    so the front is 7.1 m from the middle; in a shorter box it would drive past the end wall and squeeze water against it.
    It is 2.5 m high, not 1.2: at 5 m/s water can rise V^2 / 2g = 1.27 m, and under a lid at 1.2 m the plume spread out flat."""
    shapes, info = design_shapes(design, 0.0, speed_ms, loading, rotate_wheels=rotate_wheels, dx=0.0)
    grid = WaterGrid(length_m, width_m, height_m, dx)
    road = Road(depth_m, 0.5 * water_length_m, slope)
    table = shape_table(shapes, dx, top=height_m, sticky_groups=sticky_groups)
    solid = [s for s in shapes if s.reflect >= 1.0]
    lo = np.min([s.bounds()[0] for s in solid], axis=0)
    hi = np.max([s.bounds()[1] for s in solid], axis=0)
    wheels = [s for s in solid if isinstance(s, Cylinder) and s.group == "wheels"]
    axle_x = [float(s.center[0]) for s in wheels]
    x_front, x_rear = min(axle_x), max(axle_x)
    sound = sound_speed_for(speed_ms, depth_m)
    dt, per_frame = time_step(dx, sound, 2.0 * speed_ms, frame_dt)
    x_start = road.half_length_m + lead_m - float(lo[0])
    x_end = -road.half_length_m - lead_m - float(hi[0])
    duration = (x_start - x_end) / speed_ms
    frames = int(math.ceil(duration / frame_dt))
    particles, level = seed_water(grid, road, depth_m, RHO_WATER * sound ** 2, jitter=SEED_JITTER, rng_seed=rng_seed)
    return {"shapes": shapes, "solid": solid, "info": info, "grid": grid, "road": road, "table": table, "sound": sound, "dt": dt, "per_frame": per_frame,
            "frames": frames, "duration": duration, "x_start": x_start, "path": VehiclePath(road, speed_ms, x_start, x_front, x_rear),
            "particles": particles, "level": level, "wheels": wheels, "x_front": x_front, "x_rear": x_rear}


def _quantize(world: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    scale = (hi - lo) / 65535.0
    return np.clip(np.rint((world - lo) / scale), 0, 65535).astype(np.uint16), scale


def replay_arrays(world: np.ndarray, grid: WaterGrid, road: Road, particle_volume: float, column: float, spray_margin: float) -> tuple[np.ndarray, np.ndarray]:
    """One frame's compact form: water height over columns in mm (from the particles' volume) and the particles flying
    above it (kept in full, positions in world metres)."""
    x0, y0 = -0.5 * grid.length_m, -0.5 * grid.width_m
    nx = int(round(grid.length_m / column))
    ny = int(round(grid.width_m / column))
    i = np.clip(np.floor((world[:, 0] - x0) / column).astype(np.int64), 0, nx - 1)
    j = np.clip(np.floor((world[:, 1] - y0) / column).astype(np.int64), 0, ny - 1)
    flat = i * ny + j
    depth = np.bincount(flat, minlength=nx * ny) * particle_volume / column ** 2
    ground = road.height(x0 + (np.arange(nx) + 0.5) * column)
    surface = depth.reshape(nx, ny) + ground[:, None]
    above = world[:, 2] > surface[i, j] + spray_margin
    return np.clip(np.rint(surface * 1000.0), 0, 65535).astype(np.uint16), world[above]


def run_flooded_road(gpu: Gpu, design: dict[str, Any], depth_m: float = 0.2, speed_ms: float = 5.0, dx: float = 0.03, rotate_wheels: bool = True,
                     out: str | Path = "water_case", loading: str | None = "two_cargo", keep_particles: bool = True, analyse: bool = True, reserve_mib: int = 600,
                     tests_passed: dict[str, Any] | None = None, progress: Callable[[str], None] | None = None, frame_dt: float = FRAME_DT, **scene: Any) -> dict[str, Any]:
    """A pass through the flooded road. Saves in `out`: case.json, frames/p*.npz (all particles as 16-bit positions) and, after the analysis, replay.npz (compact,
    for a PC without a GPU). With analyse=False the analysis is left to analyse_case(out, design), so the caller can give up
    its GPU turn first. frame_dt is the time between saved frames; the step is the largest under the CFL limit that fits a whole
    number of times in it, so a different frame_dt also moves the step slightly (case.json has both). Refuses, in Japanese, a run
    that does not fit the free GPU memory."""
    say = progress or (lambda s: None)
    memory_at_start = gpu_memory_mib()
    plan = plan_case(design, depth_m, speed_ms, dx, rotate_wheels, loading, frame_dt=frame_dt, **scene)
    grid: WaterGrid = plan["grid"]
    road: Road = plan["road"]
    table: ShapeTable = plan["table"]
    path: VehiclePath = plan["path"]
    dt, per_frame = plan["dt"], plan["per_frame"]
    particles = plan["particles"]
    pose0 = path.pose(0.0)
    world0 = particles[:, :3].astype(np.float64) - grid.offset
    inside0 = particles_inside(plan["solid"], to_vehicle(world0, pose0))
    particles = particles[~inside0]
    folder = Path(out)
    (folder / "frames").mkdir(parents=True, exist_ok=True)
    sim = WaterSim(gpu, grid, particles, road, dt, plan["sound"], table, open_ends=True, reserve_mib=reserve_mib)
    t0 = time.perf_counter()
    records: list[dict[str, Any]] = []
    bounds_lo = -grid.offset
    bounds_hi = np.array(grid.nodes) * grid.dx - grid.offset
    fault = None
    pose_at = lambda n: path.pose(n * dt)
    try:
        sim.set_pose(pose0)
        for frame in range(plan["frames"] + 1):
            part = sim.read_particles()
            t = frame * per_frame * dt
            pose = path.pose(t)
            bad = int((~np.isfinite(part[:, :6])).any(axis=1).sum())
            if bad:
                fault = f"{t:.3f} 秒で {bad} 個の粒子が数でなくなった"
                break
            world = part[:, :3].astype(np.float64) - grid.offset
            in_box = np.abs(world[:, 0]) <= 0.5 * grid.length_m  # the others have left along the road
            momentum = [float(v) for v in sim.mass * part[:, 3:6].sum(axis=0, dtype=np.float64)]
            e_min = float(part[in_box, 15].min())
            speed = np.sqrt((part[in_box, 3:6].astype(np.float64) ** 2).sum(axis=1))
            world = world[in_box]
            above_road = world[:, 2] - road.height(world[:, 0])
            records.append({"t": t, "pose": [pose.x, pose.z, pose.pitch], "particles_left_box": int((~in_box).sum()), "max_speed": float(speed.max()), "top_m": float(world[:, 2].max()), "mean_z_m": float(world[:, 2].mean()),
                            "share_above_0_5m": float((above_road > 0.5).mean()), "share_above_0_3m": float((above_road > 0.3).mean()),
                            "e_min": e_min, "momentum": momentum, "impulse": [[float(v) for v in row] for row in sim.read_impulse()]})
            xyz, scale = _quantize(world, bounds_lo, bounds_hi)
            np.savez(folder / "frames" / f"p{frame:04d}.npz", xyz=xyz, speed=np.clip(np.rint(speed * 10.0), 0, 255).astype(np.uint8), lo=bounds_lo, scale=scale)
            say(f"frame {frame}/{plan['frames']} t {t:.3f} vmax {speed.max():.1f} top {world[:, 2].max():.3f} e_min {e_min:.3f} left {int((~in_box).sum())} wall {time.perf_counter() - t0:.0f}s")
            if frame < plan["frames"]:
                sim.step(per_frame, pose_at)
    finally:
        sim.close()
    case = {
        "statement": STATEMENT,
        "inputs": {"design_version": design.get("design_version"), "depth_m": depth_m, "speed_ms": speed_ms, "speed_kmh": speed_ms * 3.6, "dx_m": dx, "rotate_wheels": rotate_wheels, "loading": loading},
        "road": {"water_length_m": 2.0 * road.half_length_m, "slope": road.slope, "level_m": plan["level"], "box_m": [grid.length_m, grid.width_m, grid.height_m]},
        "memory": {"at_start_mib": memory_at_start, "needed_bytes": required_bytes(grid, len(particles), table), "reserve_mib": reserve_mib},
        "particles": len(particles), "particles_removed_at_start": int(inside0.sum()), "cells": list(grid.nodes), "particles_per_cell": 8,
        "step_s": dt, "steps": sim.steps, "steps_per_frame": per_frame, "frames": len(records), "frame_interval_asked_s": frame_dt, "frame_s": per_frame * dt, "run_wall_s": time.perf_counter() - t0, "device": gpu.name,
        "sound_speed_ms": plan["sound"], "bulk_modulus_pa": RHO_WATER * plan["sound"] ** 2, "density_kg_m3": RHO_WATER, "gravity_ms2": GRAVITY, "cfl": CFL, "e_max": E_MAX, "e_min": E_MIN,
        "density_limit": DENSITY_LIMIT, "crowd": CROWD, "particle_mass_kg": sim.mass, "groups": table.groups, "faulted": fault,
        "shapes": {"table": len(table.solids), "planes": len(table.planes), "cylinders": len(table.cyl), "left_out": table.left_out, "wheels_spin_rad_s": [[float(v) for v in spin_of(s)] for s in plan["wheels"]]},
        "assumptions": ["車両は一定の速さで道路に沿って動き、水から力を受けても速さも姿勢も変わらない", "外形は部品の箱と部材の管と車輪の円柱。衝突と絵は同じ物から作る",
                        "網は水を通す", "道路と壁で水は滑る。タイヤの表面では水がタイヤと同じ速さになる (実際の溝の水の運び方とは違う)",
                        "格子より薄い部品は、棒なら格子点 1 列、板なら両側の 1 列ずつで水を止める", "水の表面張力と粘性、空気は入れない", f"音速を実際の水より下げた弱圧縮の水 (音速は車速の {SOUND_OVER_SPEED:.0f} 倍、{plan['sound']:.0f} m/s)",
                        f"粒子が密度の上限 ({DENSITY_LIMIT} 倍) を超えて集まった所は圧力で押し広げる", "道路は水をためるくぼみで、両端は坂。坂の先の箱の端から水は外へ出て行き、戻らない"] + plan["info"]["assumptions"],
        "sources": SOURCES,
        "tests_passed": tests_passed,
        "records": records,
    }
    (folder / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=1), encoding="utf-8")
    return analyse_case(folder, design, keep_particles, progress) if analyse else case


def protected_shapes(solid: Sequence[Shape]) -> dict[str, Shape]:
    """The parts test F5 asks water not to reach, by name."""
    return {s.name: s for s in solid if s.group == "powertrain" and s.name.split("_")[0] in PROTECTED_PARTS}


def part_gaps(protected: dict[str, Shape], local: np.ndarray, dx: float, above_road: np.ndarray | None = None) -> dict[str, tuple[float, float | None]]:
    """For one frame, in vehicle axes, each part's (splash gap, standing gap) in m: its lower face minus the highest water beside or
    under it. Beside means within 2 dx of the part's plan, since water cannot stand inside the part; water more than dx above the
    part's top is flying past and does not count. The splash gap takes any particle; the standing gap only water joined to the
    road: from the lowest particle there, up while no step between particles is larger than dx (None with no water on the road
    there; needs above_road, each particle's height over the road)."""
    out: dict[str, tuple[float, float | None]] = {}
    for name, s in protected.items():
        lo, hi = s.bounds()
        here = (np.abs(local[:, 0] - 0.5 * (lo[0] + hi[0])) <= 0.5 * (hi[0] - lo[0]) + 2.0 * dx) & (np.abs(local[:, 1] - 0.5 * (lo[1] + hi[1])) <= 0.5 * (hi[1] - lo[1]) + 2.0 * dx) & (local[:, 2] <= hi[2] + dx)
        if not here.any():
            continue
        standing = None
        if above_road is not None:
            order = np.argsort(above_road[here])
            h, z = above_road[here][order], local[here, 2][order]
            if h[0] <= dx:
                steps = np.flatnonzero(np.diff(h) > dx)
                top = steps[0] if len(steps) else len(h) - 1
                standing = float(lo[2] - z[: top + 1].max())
        out[name] = (float(lo[2] - local[here, 2].max()), standing)
    return out


def analyse_case(out: str | Path, design: dict[str, Any], keep_particles: bool = True, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """What the saved particles say, on the CPU: the underfloor numbers, the parts water reached, the compact replay and the
    momentum budget. No GPU, so no GPU turn is needed."""
    say = progress or (lambda s: None)
    folder = Path(out)
    case = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    inp = case["inputs"]
    dx = inp["dx_m"]
    grid = WaterGrid(*case["road"]["box_m"], dx)
    road = Road(inp["depth_m"], 0.5 * case["road"]["water_length_m"], case["road"]["slope"])
    shapes, _ = design_shapes(design, 0.0, inp["speed_ms"], inp["loading"], rotate_wheels=inp["rotate_wheels"], dx=0.0)
    solid = [s for s in shapes if s.reflect >= 1.0]
    lo = np.min([s.bounds()[0] for s in solid], axis=0) - dx
    hi = np.max([s.bounds()[1] for s in solid], axis=0) + dx
    uf = underfloor_columns(solid, dx)
    vol0 = (0.5 * dx) ** 3
    bounds_lo = -grid.offset
    bounds_hi = np.array(grid.nodes) * grid.dx - grid.offset
    surfaces, spray, spray_start = [], [], [0]
    wet_first: dict[str, float] = {}
    cube = 2.0 * dx
    cube_rest = (cube / (0.5 * dx)) ** 3
    n_cube = np.ceil((bounds_hi - bounds_lo) / cube).astype(np.int64) + 1
    protected = protected_shapes(solid)
    gaps: dict[str, dict[str, Any]] = {name: {"lower_face_mm": float(s.bounds()[0][2]) * 1000.0, "clearance_mm": float(s.bounds()[0][2]) * 1000.0, "reached_s": None,
                                              "splash_clearance_mm": float(s.bounds()[0][2]) * 1000.0, "splash_reached_s": None} for name, s in protected.items()}
    for frame, rec in enumerate(case["records"]):
        with np.load(folder / "frames" / f"p{frame:04d}.npz") as d:
            world = d["xyz"].astype(np.float64) * d["scale"] + d["lo"]
        local = to_vehicle(world, tuple(rec["pose"]))
        for name, (splash, standing) in part_gaps(protected, local, dx, world[:, 2] - road.height(world[:, 0])).items():
            g = gaps[name]
            g["splash_clearance_mm"] = min(g["splash_clearance_mm"], splash * 1000.0)
            if g["splash_reached_s"] is None and splash <= 0.5 * dx:
                g["splash_reached_s"] = rec["t"]
            if standing is not None:
                g["clearance_mm"] = min(g["clearance_mm"], standing * 1000.0)
                if g["reached_s"] is None and standing <= 0.5 * dx:
                    g["reached_s"] = rec["t"]
        near = np.all((local >= lo) & (local <= hi), axis=1)
        here = local[near]
        rec.update(underfloor_measure(uf, here, vol0, 0.5 * dx))
        for name in wet_parts(solid, here, 0.5 * dx):
            wet_first.setdefault(name, rec["t"])
        rec["inside_solids"] = int(particles_inside(solid, here).sum())
        above_road = world[:, 2] - road.height(world[:, 0])
        rec["share_above_0_5m"], rec["share_above_0_3m"] = float((above_road > 0.5).mean()), float((above_road > 0.3).mean())
        c = np.floor((world - bounds_lo) / cube).astype(np.int64)
        rec["densest_cube_over_rest"] = float(np.bincount(c[:, 0] + n_cube[0] * (c[:, 1] + n_cube[1] * c[:, 2])).max() / cube_rest)
        surf, fly = replay_arrays(world, grid, road, vol0, 2.0 * dx, 2.0 * dx)
        surfaces.append(surf)
        spray.append(_quantize(fly, bounds_lo, bounds_hi)[0])
        spray_start.append(spray_start[-1] + len(fly))
        say(f"frame {frame} t {rec['t']:.3f} floor depth {rec['depth_mean_mm']:.0f}/{rec['depth_max_mm']:.0f} mm reached {rec['reached_share'] * 100:.1f}% inside {rec['inside_solids']} densest {rec['densest_cube_over_rest']:.2f}")
    records = case["records"]
    case["underfloor"] = {"lowest_part_m": uf.lowest_m, "columns": uf.columns, "cell_m": uf.cell,
                          "columns_definition": "いちばん低い部品が車体の板の下面以下にある列 (タイヤの真下は除く)",
                          "depth_definition": "床下の各列で、いちばん低い部品より下にある粒子の体積を列の面積で割った値 (mm)。部品までの高さを上限とする",
                          "reach_definition": f"列のいちばん低い部品から {0.5 * dx * 1000:.0f} mm 下までに粒子がある列の割合",
                          "max_depth_mean_mm": max(r["depth_mean_mm"] for r in records), "max_depth_mm": max(r["depth_max_mm"] for r in records), "max_reached_share": max(r["reached_share"] for r in records)}
    case["wet_parts_first_seen_s"] = wet_first
    case["protected_parts"] = gaps
    case["protected_definition"] = (f"下面と水のすき間 = 部品の下面の高さ - 部品の横 {2.0 * dx * 1000:.0f} mm までと真下にある、路面からつながった水のいちばん高い粒子の高さ。"
                                    f"粒子の間が {dx * 1000:.0f} mm より空いた所から上は、つながっていない飛ぶ水 (しぶき) として数えない (要件 Q20 の IP54 はしぶきに耐える: IEC 60529 の本文は読んでいない)。"
                                    f"通過の全部のコマで最も小さい値。{0.5 * dx * 1000:.0f} mm 以下で水が届いた (浸かった) とする。splash_ はしぶきも数えた値")
    case["momentum_budget"] = momentum_budget(case)
    # Says whether the box was tall enough. The walls' row along z also holds what water leaving the box carried, so this is
    # an upper limit of the lid's share only when the frames show nothing near the lid.
    case["walls_impulse_z_ns"] = float(records[-1]["impulse"][-1][2])
    case["top_of_water_m"] = max(r["top_m"] for r in records)
    case["lid_m"] = grid.height_m
    case["statement"] = STATEMENT
    (folder / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=1), encoding="utf-8")
    np.savez_compressed(folder / "replay.npz", statement=STATEMENT, surface_mm=np.array(surfaces), column_m=2.0 * dx, x0_m=-0.5 * grid.length_m, y0_m=-0.5 * grid.width_m,
                        spray=np.concatenate(spray) if spray else np.zeros((0, 3), np.uint16), spray_start=np.array(spray_start),
                        spray_lo=bounds_lo, spray_scale=(bounds_hi - bounds_lo) / 65535.0, poses=np.array([r["pose"] for r in records]), frame_s=case["frame_s"])
    if not keep_particles:
        for p in (folder / "frames").glob("p*.npz"):
            p.unlink()
        (folder / "frames").rmdir()
    return case


def read_replay(out: str | Path, frame: int) -> dict[str, Any]:
    """One frame of replay.npz, in world metres: no GPU needed. surface_m[i, j] is the water's height over the column at
    x0 + (i + 1/2) column, y0 + (j + 1/2) column; spray is the particles flying above it; pose is the vehicle's x, z and pitch."""
    with np.load(Path(out) / "replay.npz") as d:
        a, b = int(d["spray_start"][frame]), int(d["spray_start"][frame + 1])
        return {"statement": str(d["statement"]), "t_s": frame * float(d["frame_s"]), "surface_m": d["surface_mm"][frame].astype(np.float64) / 1000.0, "column_m": float(d["column_m"]),
                "x0_m": float(d["x0_m"]), "y0_m": float(d["y0_m"]), "spray_m": d["spray"][a:b].astype(np.float64) * d["spray_scale"] + d["spray_lo"], "pose": d["poses"][frame].astype(np.float64)}


def momentum_budget(case: dict[str, Any]) -> dict[str, Any]:
    """The water's momentum at the end against the impulses summed by the kernels and its weight, with no factor."""
    last = case["records"][-1]
    impulse = np.array(last["impulse"])
    mass = case["particle_mass_kg"] * case["particles"]
    weight = np.array([0.0, 0.0, -mass * case["gravity_ms2"] * case["steps"] * case["step_s"]])
    given = impulse.sum(axis=0) + weight
    gross = np.abs(impulse).sum(axis=0) + np.abs(weight)
    residual = np.array(last["momentum"]) - given
    return {"water_ns": last["momentum"], "impulse_and_weight_ns": [float(v) for v in given], "residual_ns": [float(v) for v in residual],
            "residual_over_gross": [float(v) for v in np.abs(residual) / np.maximum(gross, 1e-30)]}


def load_design(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))

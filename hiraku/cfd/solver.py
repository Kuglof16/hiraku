"""One run on the GPU: arrays, the time loop, forces and fields.

The body force is the momentum exchange of every cut link at every step, summed in double precision per
group. A force over any interval is the difference of the running sum divided by the steps: instantaneous
values alternate between odd and even steps, so they are never sampled for a result.
"""

from __future__ import annotations

import ctypes as ct
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from hiraku.cfd.cuda import Gpu, gpu_memory_mib
from hiraku.cfd.geometry import Grid, LinkArrays, Voxels, link_arrays

BYTES_PER_NODE = 19 * 4 * 2 + 1  # two lattices and the mask
BLOCK_X = 256


class MemoryBudgetError(RuntimeError):
    """The arrays do not fit what is free on the GPU now."""


class GridStruct(ct.Structure):
    _fields_ = [("nx", ct.c_int), ("ny", ct.c_int), ("nz", ct.c_int), ("X", ct.c_int), ("Y", ct.c_int), ("N", ct.c_int),
                ("tau0", ct.c_float), ("cs2", ct.c_float), ("ux", ct.c_float), ("uy", ct.c_float), ("uz", ct.c_float),
                ("ug", ct.c_float), ("vg", ct.c_float), ("ramp", ct.c_float), ("cf", ct.c_float)]


@dataclass
class Flow:
    """Physical flow and its lattice mapping. Air moves along +x of the grid at speed_ms."""

    speed_ms: float
    rho: float = 1.225
    nu: float = 1.5e-5
    u_lattice: float = 0.05
    smagorinsky: float = 0.0
    ground_velocity_ms: tuple[float, float] | None = None  # None: no ground (far field below as well)
    ramp_steps: int = 0
    sponge_cells: tuple[int, int, int, int, int, int] = (12, 24, 12, 12, 0, 12)  # -x +x -y +y -z +z
    sponge_strength: float = 0.1
    ground_roughness_m: float = 1e-3  # z0 of the log law at the ground; a paved road's order of size, 確かめていない


@dataclass
class Units:
    dx: float
    dt: float
    force_N: float  # lattice force -> N
    moment_Nm: float  # lattice moment (arm in cells) -> N m
    nu_lattice: float
    tau0: float

    @staticmethod
    def of(flow: Flow, dx: float) -> "Units":
        dt = flow.u_lattice * dx / flow.speed_ms
        nu = flow.nu * dt / (dx * dx)
        force = flow.rho * dx ** 4 / (dt * dt)
        return Units(dx, dt, force, force * dx, nu, 0.5 + 3.0 * nu)


@dataclass
class Result:
    steps: np.ndarray  # cumulative steps at each record
    impulse: np.ndarray  # (records, groups, 6): running sum of lattice force and moment
    groups: list[str]
    units: Units
    grid: Grid
    stats: list[dict[str, float]] = field(default_factory=list)
    fields: dict[str, np.ndarray] | None = None  # mean dr, ux, uy, uz (lattice), [z, y, x] without ghosts; NaN in solids
    field_samples: int = 0
    wall_time_s: float = 0.0
    memory_bytes: int = 0
    mlups: float = 0.0
    device: str = ""
    faulted: str | None = None

    def mean(self, first_step: int, last_step: int | None = None) -> np.ndarray:
        """Mean lattice (Fx, Fy, Fz, Mx, My, Mz) per group over the steps after first_step, from the running sum."""
        a = int(np.searchsorted(self.steps, first_step))
        b = len(self.steps) - 1 if last_step is None else int(np.searchsorted(self.steps, last_step))
        return (self.impulse[b] - self.impulse[a]) / float(self.steps[b] - self.steps[a])

    def history(self, window: int) -> tuple[np.ndarray, np.ndarray]:
        """(mid-steps, mean lattice force and moment summed over the groups) over records `window` apart."""
        total = self.impulse.sum(axis=1)
        idx = np.arange(0, len(self.steps), window)
        mid = 0.5 * (self.steps[idx[1:]] + self.steps[idx[:-1]])
        d = (total[idx[1:]] - total[idx[:-1]]) / (self.steps[idx[1:]] - self.steps[idx[:-1]])[:, None]
        return mid, d


def required_bytes(grid: Grid, vox: Voxels | None = None, fields: bool = False) -> int:
    n = grid.n_total * (BYTES_PER_NODE + (16 if fields else 0))
    if vox is not None:
        n += vox.n_links * 50
    return int(n)


def check_memory(need_bytes: int, reserve_mib: int = 600) -> dict[str, Any]:
    """Refuse a run that does not fit what is free now, leaving reserve_mib for the other work on the GPU."""
    mem = gpu_memory_mib()
    if mem is None:
        return {"need_mib": need_bytes / 2 ** 20, "free_mib": None}
    need = need_bytes / 2 ** 20
    if need + reserve_mib > mem["free"]:
        raise MemoryBudgetError(f"GPU の空きが足りません: 要るのは {need:.0f} MiB + 余裕 {reserve_mib} MiB、空きは {mem['free']} MiB。待つか、格子を粗くしてください")
    return {"need_mib": need, "free_mib": mem["free"], "total_mib": mem["total"]}


def ground_cf(dx: float, z0: float, kappa: float = 0.41) -> float:
    """Friction coefficient at the first row (height dx/2) by the rough-wall log law: u / u_tau = ln(z / z0) / kappa."""
    return float(2.0 * (kappa / np.log(max(0.5 * dx / z0, 1.5))) ** 2)


def sponge_profile(n: int, low: int, high: int, strength: float) -> np.ndarray:
    """Quadratic absorbing layers at both ends of an axis of n nodes."""
    s = np.zeros(n, np.float32)
    if low:
        d = (low - np.arange(n)) / float(low)
        s = np.where(d > 0, np.maximum(s, strength * d * d), s)
    if high:
        d = (np.arange(n) - (n - 1 - high)) / float(high)
        s = np.where(d > 0, np.maximum(s, strength * d * d), s)
    return s.astype(np.float32)


class Simulation:
    """A case on the GPU. run() advances it; the arrays are freed by close()."""

    def __init__(self, gpu: Gpu, vox: Voxels, flow: Flow, ref_point: Any = (0.0, 0.0, 0.0), interpolate: bool = True,
                 fields: bool = False, reserve_mib: int = 600):
        self.gpu = gpu
        self.vox = vox
        self.flow = flow
        grid = vox.grid
        self.grid = grid
        self.units = Units.of(flow, grid.dx)
        self.want_fields = fields
        self.memory = check_memory(required_bytes(grid, vox, fields), reserve_mib)
        scale = flow.u_lattice / flow.speed_ms
        self.links: LinkArrays = link_arrays(vox, np.asarray(ref_point, float), scale, interpolate)
        self.g = GridStruct(grid.nx, grid.ny, grid.nz, grid.nx + 2, grid.ny + 2, grid.n_total, self.units.tau0,
                            flow.smagorinsky ** 2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ground_cf(grid.dx, flow.ground_roughness_m))
        self.ground = flow.ground_velocity_ms is not None
        self.buf: dict[str, Any] = {}
        total = grid.n_total
        self.buf["A"] = gpu.buffer(19 * 4 * total)
        self.buf["B"] = gpu.buffer(19 * 4 * total)
        self.buf["A"].zero()
        self.buf["B"].zero()
        self.buf["mask"] = gpu.buffer(total)
        self.buf["mask"].write(vox.mask)
        s = flow.sponge_cells
        sig = flow.sponge_strength
        self.buf["sx"] = gpu.buffer(4 * grid.nx)
        self.buf["sx"].write(sponge_profile(grid.nx, s[0], s[1], sig))
        self.buf["sy"] = gpu.buffer(4 * grid.ny)
        self.buf["sy"].write(sponge_profile(grid.ny, s[2], s[3], sig))
        self.buf["sz"] = gpu.buffer(4 * grid.nz)
        self.buf["sz"].write(sponge_profile(grid.nz, 0 if self.ground else s[4] or s[5], s[5], sig))
        L = self.links
        self.nlinks = len(L.a)
        self.nchunks = len(L.chunk_group)
        for name in ("slot_a", "slot_b", "slot_d", "a", "b", "dh", "r", "dir", "arm", "chunk_start", "chunk_group"):
            arr = getattr(L, name)
            self.buf[name] = gpu.buffer(max(arr.nbytes, 4))
            if arr.nbytes:
                self.buf[name].write(arr)
        self.buf["v"] = gpu.buffer(max(4 * self.nlinks, 4))
        self.ngroups = len(vox.groups)
        self.buf["accum"] = gpu.buffer(6 * 8 * self.ngroups)
        self.buf["accum"].zero()
        self.buf["stat_d"] = gpu.buffer(4 * 8)
        self.buf["stat_i"] = gpu.buffer(5 * 4)
        if fields:
            for name in ("m_dr", "m_ux", "m_uy", "m_uz"):
                self.buf[name] = gpu.buffer(4 * total)
                self.buf[name].zero()
        self.samples = 0
        self.step_count = 0
        self.src_is_a = True
        self._build_launches()
        self.fill_ramp(1.0 if flow.ramp_steps == 0 else 0.0)

    def _p(self, name: str) -> ct.c_ulonglong:
        return ct.c_ulonglong(self.buf[name].ptr.value)

    def _build_launches(self) -> None:
        gpu, g = self.gpu, self.grid
        block = (BLOCK_X, 1, 1)
        rows = (int(np.ceil(g.nx / BLOCK_X)), g.ny, g.nz)
        self.l_stream: dict[bool, Any] = {}
        self.l_links: dict[bool, Any] = {}
        self.l_write: dict[bool, Any] = {}
        self.l_ground: dict[bool, Any] = {}
        self.l_far: dict[bool, Any] = {}
        self.l_mean: dict[bool, Any] = {}
        self.l_stats: dict[bool, Any] = {}
        ramp = ct.c_float(1.0)
        self.c_ramp = ramp
        for src_a in (True, False):
            src, dst = ("A", "B") if src_a else ("B", "A")
            self.l_stream[src_a] = gpu.launch("stream_collide", [self._p(src), self._p(dst), self._p("mask"), self._p("sx"), self._p("sy"), self._p("sz"), self.g], rows, block)
            if self.nlinks:
                self.l_links[src_a] = gpu.launch(
                    "links_compute",
                    [self._p(src), self._p("slot_a"), self._p("slot_b"), self._p("slot_d"), self._p("a"), self._p("b"), self._p("dh"), self._p("r"),
                     self._p("dir"), self._p("arm"), self._p("chunk_start"), self._p("chunk_group"), self._p("v"), self._p("accum"), ct.c_uint(self.nchunks), ramp],
                    (int(np.ceil(self.nchunks / 128)), 1, 1), (128, 1, 1))
                self.l_write[src_a] = gpu.launch("links_write", [self._p(src), self._p("slot_d"), self._p("v"), ct.c_uint(self.nlinks)],
                                                 (int(np.ceil(self.nlinks / 256)), 1, 1), (256, 1, 1))
            self.l_ground[src_a] = gpu.launch("ground_fill", [self._p(src), self.g], (int(np.ceil(g.nx / BLOCK_X)), g.ny, 1), block)
            # threads cover all nodes: x-blocks of 256 times 65536 per y-block
            total_blocks = int(np.ceil(g.n_total / BLOCK_X))
            gx = min(total_blocks, 65536)
            gy = int(np.ceil(total_blocks / 65536))
            self.l_far[src_a] = gpu.launch("fill_far", [self._p(src), self._p("mask"), self.g], (gx, gy, 1), block)
            # stats and means look at the array just written
            if self.want_fields:
                self.l_mean[src_a] = gpu.launch("mean_sample", [self._p(src), self._p("mask"), self._p("m_dr"), self._p("m_ux"), self._p("m_uy"), self._p("m_uz"), self.g], rows, block)
            self.l_stats[src_a] = gpu.launch("stats", [self._p(src), self._p("mask"), self.g, self._p("stat_d"), self._p("stat_i")],
                                             (int(np.ceil(g.ny * g.nz / 64)), 1, 1), (64, 1, 1))

    def fill_ramp(self, f: float) -> None:
        """Set the far field, ground and wheels to the share f of their final speed."""
        u = self.flow.u_lattice
        self.g.ux, self.g.uy, self.g.uz = u * f, 0.0, 0.0
        if self.ground:
            scale = u / self.flow.speed_ms
            self.g.ug = self.flow.ground_velocity_ms[0] * scale * f
            self.g.vg = self.flow.ground_velocity_ms[1] * scale * f
        self.c_ramp.value = f
        self.l_far[True]()
        self.l_far[False]()

    def step(self, n: int = 1) -> None:
        ramp_steps = self.flow.ramp_steps
        for _ in range(n):
            if ramp_steps and self.step_count < ramp_steps:
                x = (self.step_count + 1) / ramp_steps
                self.fill_ramp(float(np.sin(0.5 * np.pi * x) ** 2))
            src = self.src_is_a
            if self.nlinks:
                self.l_links[src]()
            if self.ground:
                self.l_ground[src]()
            if self.nlinks:
                self.l_write[src]()
            self.l_stream[src]()
            self.src_is_a = not src
            self.step_count += 1

    def sample_mean(self) -> None:
        self.l_mean[self.src_is_a]()
        self.samples += 1

    def read_impulse(self) -> np.ndarray:
        out = np.zeros((self.ngroups, 6), np.float64)
        self.buf["accum"].read_into(out)
        return out

    def stats(self) -> dict[str, float]:
        self.buf["stat_d"].zero()
        self.buf["stat_i"].zero()
        self.l_stats[self.src_is_a]()
        d = np.zeros(4, np.float64)
        i = np.zeros(5, np.int32)
        self.buf["stat_d"].read_into(d)
        self.buf["stat_i"].read_into(i)
        return {"step": self.step_count, "mass_dev": float(d[0]), "momentum": [float(x) for x in d[1:]],
                "max_speed": float(np.sqrt(i[0] / 1e7)), "max_dr": float(i[1] / 1e7), "min_dr": float(-i[2] / 1e7),
                "nonfinite": int(i[3]), "negative_f": int(i[4])}

    def read_populations(self) -> np.ndarray:
        """All populations (19, N) of the current state: for the tests."""
        out = np.zeros((19, self.grid.n_total), np.float32)
        self.buf["A" if self.src_is_a else "B"].read_into(out)
        return out

    def write_populations(self, h: np.ndarray) -> None:
        self.buf["A" if self.src_is_a else "B"].write(h.astype(np.float32))

    def mean_fields(self) -> dict[str, np.ndarray]:
        g = self.grid
        out = {}
        solid = self.vox.mask.reshape(g.nz + 2, g.ny + 2, g.nx + 2)[1:-1, 1:-1, 1:-1] != 0
        for name in ("dr", "ux", "uy", "uz"):
            a = np.zeros(g.n_total, np.float32)
            self.buf["m_" + name].read_into(a)
            a = a.reshape(g.nz + 2, g.ny + 2, g.nx + 2)[1:-1, 1:-1, 1:-1] / max(self.samples, 1)
            a[solid] = np.nan
            out[name] = a
        return out

    def close(self) -> None:
        for b in self.buf.values():
            b.free()
        self.buf = {}


def run(sim: Simulation, steps: int, record_every: int = 50, stats_every: int = 500, average_from: int | None = None,
        sample_every: int = 11, on_record: Callable[[Result], None] | None = None) -> Result:
    """Advance by steps and return the record. Stops early (faulted) on nonfinite values."""
    t0 = time.perf_counter()
    gpu = sim.gpu
    recs = [0]
    imp = [sim.read_impulse()]
    stats: list[dict[str, float]] = []
    faulted = None
    done = 0
    start = sim.step_count
    while done < steps:
        block = min(record_every, steps - done)
        if sim.want_fields and average_from is not None and sim.step_count + block > average_from:
            # samples at an odd spacing, so both phases of the odd/even mode are in the mean
            k = 0
            while k < block:
                m = min(sample_every, block - k)
                sim.step(m)
                k += m
                if sim.step_count >= average_from:
                    sim.sample_mean()
        else:
            sim.step(block)
        done += block
        gpu.sync()
        recs.append(sim.step_count - start)
        imp.append(sim.read_impulse())
        if stats_every and (done % stats_every) < record_every:
            st = sim.stats()
            stats.append(st)
            if st["nonfinite"] > 0:
                faulted = f"step {sim.step_count}: {st['nonfinite']} nodes are not finite"
                break
    gpu.sync()
    wall = time.perf_counter() - t0
    n_cells = sim.grid.nx * sim.grid.ny * sim.grid.nz
    res = Result(np.array(recs), np.array(imp), sim.vox.groups, sim.units, sim.grid, stats, None, sim.samples, wall,
                 gpu.allocated, n_cells * done / wall / 1e6 if wall > 0 else 0.0, gpu.name, faulted)
    if sim.want_fields and sim.samples and faulted is None:
        res.fields = sim.mean_fields()
    return res

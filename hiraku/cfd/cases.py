"""Grids and flows for the reference bodies (sphere, plate, cube) and the runs that use them."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from hiraku.cfd.cuda import Gpu
from hiraku.cfd.geometry import Grid, Shape, Sphere, box, geometry_report, voxelize
from hiraku.cfd.solver import Flow, Result, Simulation, run


def schiller_naumann(re: float) -> float:
    """Sphere drag by the Schiller-Naumann (1933) correlation, valid to about Re 800-1000.

    An empirical fit, as tabulated in Clift, Grace & Weber, Bubbles, Drops and Particles (1978)."""
    return 24.0 / re * (1.0 + 0.15 * re ** 0.687)


@dataclass
class RefRun:
    name: str
    re: float
    cells: int
    cd: float
    reference_area_m2: float
    result: Result
    report: dict[str, Any]
    side_force: float
    stat_window_cd: list[float]
    u_lattice: float
    domain_cells: tuple[int, int, int]


def run_body(gpu: Gpu, shape: Shape, name: str, L: float, area: float, re: float, cells: int, u_lattice: float = 0.05,
             domain: tuple[float, float, float, float] = (5.0, 11.0, 5.0, 5.0), t_star: float = 60.0, average_last: float = 0.35,
             smagorinsky: float = 0.0, ramp_t_star: float = 3.0, interpolate: bool = True, nu: float | None = None) -> RefRun:
    """A body of length L (m) centred at the origin in a far-field box: domain = (upstream, downstream, half width, half height) in L."""
    dx = L / cells
    up, down, hw, hh = domain
    nx = int(round((up + down) * cells))
    ny = int(round(2 * hw * cells))
    nz = int(round(2 * hh * cells))
    origin = np.array([-up * L, -hw * L, -hh * L])
    grid = Grid(nx, ny, nz, dx, origin)
    U = 1.0
    nu_si = U * L / re if nu is None else nu
    vox = voxelize([shape], grid)
    sponge = max(8, int(round(0.6 * cells)))
    flow = Flow(U, rho=1.0, nu=nu_si, u_lattice=u_lattice, smagorinsky=smagorinsky,
                ramp_steps=int(ramp_t_star * cells / u_lattice),
                sponge_cells=(sponge, sponge * 2, sponge, sponge, sponge, sponge))
    steps = int(t_star * cells / u_lattice)
    sim = Simulation(gpu, vox, flow, ref_point=(0.0, 0.0, 0.0), interpolate=interpolate)
    try:
        res = run(sim, steps, record_every=max(10, cells // 2), stats_every=max(500, steps // 40))
    finally:
        sim.close()
    if res.faulted:
        raise RuntimeError(f"{name} D={cells}: {res.faulted}")
    first = int(steps * (1.0 - average_last))
    f = res.mean(first)[0]
    q = 0.5 * flow.rho * U * U * area
    cd = f[0] * res.units.force_N / q
    cy = f[1] * res.units.force_N / q
    last = len(res.steps) - 1
    windows = []
    for w in range(4):
        a = first + (steps - first) * w // 4
        b = first + (steps - first) * (w + 1) // 4
        windows.append(float(res.mean(a, b)[0, 0] * res.units.force_N / q))
    rep = geometry_report(vox)
    return RefRun(name, re, cells, float(cd), area, res, rep, float(cy), windows, u_lattice, (nx, ny, nz))


def sphere_case(gpu: Gpu, re: float, cells: int, **kw: Any) -> RefRun:
    D = 1.0
    return run_body(gpu, Sphere((0.0, 0.0, 0.0), D / 2.0), "sphere", D, math.pi * D * D / 4.0, re, cells, **kw)

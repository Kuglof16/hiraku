"""A design as shapes in the wind, and one vehicle run at a speed and a yaw angle.

Vehicle axes are the design's: x forward, y left, z up, origin at the moment centre (mid wheelbase, mid
track, on the ground). The grid has the air along +x and z up, so the vehicle stands turned about z: at
yaw 0 grid x = -vehicle x and grid y = -vehicle y. A positive yaw is wind from the vehicle's left.
The crosswind is square to the path: air speed U = V / cos(yaw), and the ground moves at V along the
vehicle's own axis, not along the air. All forces go back to vehicle axes through the same rotation.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from hiraku.cfd.cuda import Gpu
from hiraku.cfd.geometry import Cylinder, Grid, Shape, Voxels, box, geometry_report, prism, voxelize
from hiraku.cfd.solver import Flow, Result, Simulation, run
from hiraku.contract.loading import parts_on_board

MM = 1e-3
RHO_AIR = 1.225  # kg/m^3, the design variable's default (ISA sea level)
NU_AIR = 1.5e-5  # m^2/s, air near 20 degC (rounded textbook value)
SCREEN_K = "K = 1.3 (1 - e) + (1/e - 1)^2, e = open area share: wire screen loss after Idelchik; 確かめていない"


def rpy_matrix(rpy_deg: Any) -> np.ndarray:
    """R = Rz(yaw) Ry(pitch) Rx(roll), the design record's box convention."""
    r, p, y = (math.radians(float(a)) for a in rpy_deg)
    rx = np.array([[1, 0, 0], [0, math.cos(r), -math.sin(r)], [0, math.sin(r), math.cos(r)]])
    ry = np.array([[math.cos(p), 0, math.sin(p)], [0, 1, 0], [-math.sin(p), 0, math.cos(p)]])
    rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
    return rz @ ry @ rx


def member_axes(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(ey, ez) of a member with unit direction d, the design record's convention: h along ey, b along ez."""
    c = np.cross(d, (0.0, 0.0, 1.0))
    if np.linalg.norm(c) < 1e-9:
        return (np.array([-1.0, 0.0, 0.0]) if d[2] > 0 else np.array([1.0, 0.0, 0.0])), np.array([0.0, -1.0, 0.0])
    ez = c / np.linalg.norm(c)
    return np.cross(ez, d), ez


def to_grid(yaw_deg: float) -> np.ndarray:
    """Rotation from vehicle axes to grid axes (rows are the grid axes in vehicle axes)."""
    b = math.radians(yaw_deg)
    x = np.array([-math.cos(b), -math.sin(b), 0.0])  # where the air goes, seen from the vehicle
    z = np.array([0.0, 0.0, 1.0])
    return np.array([x, np.cross(z, x), z])


def moment_centre(design: dict[str, Any]) -> tuple[np.ndarray, float]:
    """Moment centre in design mm (mid wheelbase, mid track, ground) and the ground height in design mm."""
    sup = design.get("supports") or []
    if not sup:
        raise ValueError("design has no supports, so no ground or moment centre")
    pos = np.array([s["position_mm"] for s in sup], float)
    ground = min(s["position_mm"][2] - s.get("radius_mm", 0.0) for s in sup)
    c = 0.5 * (pos.min(axis=0) + pos.max(axis=0))
    return np.array([c[0], c[1], ground]), float(ground)


def _group(part: dict[str, Any]) -> str:
    kind = part.get("kind", "")
    if kind == "side_net":
        return "side_nets"
    if kind == "cargo":
        return "cargo"
    if kind == "occupant" or part.get("module") == "occupant":
        return "occupants"
    return part.get("module") or "body"


def design_shapes(design: dict[str, Any], yaw_deg: float, speed_ms: float, loading: str | None = "two_cargo",
                  u_lattice: float = 0.05, rotate_wheels: bool = True, dx: float = 0.0) -> tuple[list[Shape], dict[str, Any]]:
    """Shapes of the design on the grid frame (metres, moment centre at the origin, ground at z = 0).

    With dx, the wedge under each tyre gets a block sized by the grid (see below)."""
    R = to_grid(yaw_deg)
    ref, _ = moment_centre(design)
    info: dict[str, Any] = {"members": 0, "boxes": 0, "wheels": 0, "screens": [], "skipped": [], "assumptions": []}

    def P(mm: Any) -> np.ndarray:
        return R @ ((np.asarray(mm, float) - ref) * MM)

    shapes: list[Shape] = []
    nodes = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    for m in design.get("members", []):
        a, b = np.asarray(nodes[m["node_a"]], float), np.asarray(nodes[m["node_b"]], float)
        L = np.linalg.norm(b - a)
        if L <= 0:
            continue
        d = (b - a) / L
        sec = design["sections"][m["section"]]
        dims = sec.get("dims_mm", {})
        A, B = P(a), P(b)
        if sec.get("shape") == "round_tube":
            shapes.append(Cylinder(0.5 * (A + B), R @ d, dims["d"] * MM / 2.0, L * MM, m["id"], "frame"))
        elif sec.get("shape") == "rect_tube":
            ey, ez = member_axes(d)
            h, w = dims["h"] * MM / 2.0, dims["b"] * MM / 2.0
            sect = [(-h, -w), (h, -w), (h, w), (-h, w)]
            shapes.append(prism(A, B, sect, R @ ey, R @ ez, m["id"], "frame"))
        else:
            info["skipped"].append(m["id"])
            continue
        info["members"] += 1
    u_g = R @ np.array([-speed_ms, 0.0, 0.0])  # the ground, seen from the vehicle, moves backwards at V
    wheel_ids = set()
    for s in design.get("supports", []):
        if "radius_mm" not in s:
            continue
        c = P(s["position_mm"])
        radius = s["radius_mm"] * MM
        width = s.get("width_mm", 200.0) * MM
        cyl = Cylinder(c, R @ np.array([0.0, 1.0, 0.0]), radius, width, "wheel_" + s["id"], "wheels")
        if dx:
            # The wedge between tyre and ground closes below the grid: a node caught in it, with both walls driving
            # into it, reached 40 times the dynamic pressure. A block fills the wedge where the gap is under 1.5 cells.
            gap = 1.5 * dx
            half = math.sqrt(max(2.0 * radius * gap - gap * gap, 0.0))
            foot = np.array([c[0], c[1], gap / 2.0])
            shapes.append(box(foot, (2.0 * half, width, gap), R @ np.eye(3), "contact_" + s["id"], "wheels"))
        if rotate_wheels:
            omega = np.cross(u_g, (0.0, 0.0, 1.0)) / radius

            def surface_velocity(p: np.ndarray, omega: np.ndarray = omega, c: np.ndarray = c) -> np.ndarray:
                return np.cross(omega, p - c)

            cyl.velocity = surface_velocity
        shapes.append(cyl)
        wheel_ids.add("wheel_" + s["id"])
        info["wheels"] += 1
    if not rotate_wheels:
        info["assumptions"].append("車輪は回さない (地面だけ動かす)")
    for part in parts_on_board(design, loading):
        if part["id"] in wheel_ids or part.get("kind") == "wheel":
            continue
        bx = part.get("box")
        if not bx:
            info["skipped"].append(part["id"])
            continue
        Rb = rpy_matrix(bx.get("rpy_deg", (0.0, 0.0, 0.0)))
        shape = box(P(bx["center_mm"]), np.asarray(bx["size_mm"], float) * MM, R @ Rb, part["id"], _group(part))
        if part.get("kind") == "side_net":
            variables = design.get("variables") or {}
            wire = (variables.get("side_net_wire_d") or {}).get("value", 3.0)
            pitch = (variables.get("side_net_pitch") or {}).get("value", 50.0)
            open_share = (1.0 - wire / pitch) ** 2
            k = 1.3 * (1.0 - open_share) + (1.0 / open_share - 1.0) ** 2
            # a share r of the populations bouncing gives dp = 2 r u rho across the link plane (lattice units);
            # dp = K rho u^2 / 2 at the air speed gives r = K u / 4
            shape.reflect = k * u_lattice / 4.0
            info["screens"].append({"id": part["id"], "open_share": open_share, "K": k, "reflect": shape.reflect})
        shapes.append(shape)
        info["boxes"] += 1
    if info["screens"]:
        info["assumptions"].append("網は多孔の面: 損失係数 " + SCREEN_K)
    return shapes, info


@dataclass
class Domain:
    """Distances from the moment centre, metres: upstream, downstream, half width, top."""

    upstream: float = 5.0
    downstream: float = 10.0
    half_width: float = 4.5
    top: float = 5.5

    def grid(self, dx: float) -> Grid:
        nx = int(round((self.upstream + self.downstream) / dx))
        ny = int(round(2.0 * self.half_width / dx))
        nz = int(round(self.top / dx))
        # A fraction of a cell off, so a nearly symmetric design does not sit exactly symmetric on the grid: on reference bodies
        # an exactly centred grid held a symmetric wake and changed the mean drag.
        return Grid(nx, ny, nz, dx, np.array([-self.upstream + 0.26 * dx, -self.half_width + 0.13 * dx, 0.0]))


@dataclass
class VehicleRun:
    design_version: Any
    yaw_deg: float
    speed_ms: float
    air_speed_ms: float
    dx: float
    forces_N: dict[str, float]  # Fx Fy Fz Mx My Mz in vehicle axes about the moment centre
    groups_N: dict[str, list[float]]
    coefficients: dict[str, float]
    geometry: dict[str, Any]
    convergence: dict[str, Any]
    numerics: dict[str, Any]
    assumptions: list[str]
    result: Result
    fields: dict[str, np.ndarray] | None = None
    shape_info: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in ("design_version", "yaw_deg", "speed_ms", "air_speed_ms", "dx", "forces_N", "groups_N",
                                              "coefficients", "geometry", "convergence", "numerics", "assumptions")}

    def save(self, folder: Path) -> None:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "case.json").write_text(json.dumps(self.summary(), ensure_ascii=False, indent=1), encoding="utf-8")
        r = self.result
        np.savez_compressed(folder / "forces.npz", steps=r.steps, impulse=r.impulse, force_N=r.units.force_N, moment_Nm=r.units.moment_Nm, dt=r.units.dt)
        if self.fields is not None:
            g = r.grid
            np.savez_compressed(folder / "fields.npz", dx=g.dx, origin=g.origin, u_lattice=self.numerics["u_lattice"], air_speed_ms=self.air_speed_ms,
                                rho=RHO_AIR, dt=r.units.dt, **self.fields)


def frontal_area_m2(shapes: list[Shape], yaw_deg: float = 0.0, cell: float = 0.01) -> float:
    """Projected area of the solid shapes along the air on a ray grid of the given size."""
    solids = [s for s in shapes if s.reflect >= 1.0]
    lo = np.min([s.bounds()[0] for s in solids], axis=0)
    hi = np.max([s.bounds()[1] for s in solids], axis=0)
    ys = np.arange(lo[1] + cell / 2, hi[1], cell)
    zs = np.arange(lo[2] + cell / 2, hi[2], cell)
    yy, zz = np.meshgrid(ys, zs, indexing="ij")
    p = np.column_stack([np.full(yy.size, lo[0] - 1.0), yy.ravel(), zz.ravel()])
    d = np.array([1.0, 0.0, 0.0])
    hit = np.zeros(len(p), bool)
    for s in solids:
        t_in, t_out = s.clip(p, d)
        hit |= t_in <= t_out
    return float(hit.sum()) * cell * cell


def run_vehicle(gpu: Gpu, design: dict[str, Any], yaw_deg: float = 0.0, speed_ms: float = 40.0 / 3.6, dx: float = 0.08, u_lattice: float = 0.05,
                smagorinsky: float = 0.15, domain: Domain | None = None, flow_throughs: float = 1.2, average_L_over_U: float = 12.0,
                loading: str | None = "two_cargo", rotate_wheels: bool = True, interpolate: bool = True, fields: bool = True,
                reference_area_m2: float | None = None, air_speed_ms: float | None = None) -> VehicleRun:
    """One steady-air run. The air is uniform over height: no atmospheric boundary layer.

    The air speed is V / cos(yaw), a wind square to the path, unless air_speed_ms says otherwise (a vehicle
    at rest in a side wind: speed_ms 0, yaw 90, air_speed_ms the wind)."""
    domain = domain or Domain()
    air = air_speed_ms if air_speed_ms is not None else speed_ms / math.cos(math.radians(yaw_deg))
    shapes, info = design_shapes(design, yaw_deg, speed_ms, loading, u_lattice, rotate_wheels, dx)
    grid = domain.grid(dx)
    vox: Voxels = voxelize(shapes, grid, ground=True)
    R = to_grid(yaw_deg)
    u_g = R @ np.array([-speed_ms, 0.0, 0.0])
    cells = lambda m: max(6, int(round(m / dx)))
    flow = Flow(air, rho=RHO_AIR, nu=NU_AIR, u_lattice=u_lattice, smagorinsky=smagorinsky, ground_velocity_ms=(float(u_g[0]), float(u_g[1])),
                ramp_steps=int(2.0 * grid.nx / 0.577),
                sponge_cells=(cells(0.6), cells(1.2), cells(0.6), cells(0.6), 0, cells(0.6)))
    bb_lo = np.min([s.bounds()[0] for s in shapes], axis=0)
    bb_hi = np.max([s.bounds()[1] for s in shapes], axis=0)
    length = float((bb_hi - bb_lo)[:2].max())
    steps_per_L = length / dx / u_lattice
    warm = flow.ramp_steps + int(flow_throughs * grid.nx / u_lattice)
    average = int(average_L_over_U * steps_per_L)
    sim = Simulation(gpu, vox, flow, ref_point=(0.0, 0.0, 0.0), interpolate=interpolate, fields=fields)
    interpolated_share = sim.links.interpolated_share
    memory = dict(sim.memory)
    try:
        res = run(sim, warm + average, record_every=max(50, int(steps_per_L / 8)), stats_every=2000, average_from=warm, sample_every=11)
    finally:
        sim.close()
    if res.faulted:
        raise RuntimeError(f"yaw {yaw_deg} dx {dx}: {res.faulted}")
    u = res.units
    F = res.mean(warm)  # (groups, 6) lattice, grid axes, moment about the moment centre
    Fg = F.sum(axis=0)
    Rt = R.T
    fv = Rt @ (Fg[:3] * u.force_N)
    mv = Rt @ (Fg[3:] * u.moment_Nm)
    q = 0.5 * RHO_AIR * air * air
    area = reference_area_m2 if reference_area_m2 is not None else frontal_area_m2(design_shapes(design, 0.0, speed_ms, loading, u_lattice, rotate_wheels)[0])
    groups = {name: [float(x) for x in Rt @ (F[i, :3] * u.force_N)] for i, name in enumerate(res.groups)}
    # stationarity: means over the quarters of the averaging window
    quarters = []
    for w in range(4):
        a, b = warm + average * w // 4, warm + average * (w + 1) // 4
        quarters.append(float((Rt @ (res.mean(a, b).sum(axis=0)[:3] * u.force_N))[0]))
    rep = geometry_report(vox)
    rep["reference_area_m2"] = area
    last = res.stats[-1] if res.stats else {}
    max_u = max((s["max_speed"] for s in res.stats), default=0.0)
    numerics = {"u_lattice": u_lattice, "tau0": u.tau0, "dt_s": u.dt, "smagorinsky": smagorinsky, "steps": int(res.steps[-1]), "warm_steps": warm, "average_steps": average,
                "cells": [grid.nx, grid.ny, grid.nz], "mlups": res.mlups, "memory_bytes": res.memory_bytes, "memory": memory, "device": res.device,
                "interpolated_link_share": interpolated_share, "max_lattice_speed": max_u, "max_mach": max_u * math.sqrt(3.0), "last_stats": last,
                "ramp_steps": flow.ramp_steps, "domain_m": {"upstream": domain.upstream, "downstream": domain.downstream, "half_width": domain.half_width, "top": domain.top}}
    mean_fx = float(fv[0])
    conv = {"quarter_means_Fx_N": quarters, "spread_pct": 100.0 * (max(quarters) - min(quarters)) / abs(mean_fx) if mean_fx else None,
            "average_L_over_U": average_L_over_U, "flow_throughs_before": flow_throughs}
    coeff = {"q_Pa": q, "reference_area_m2": area, "Cd": -float(fv[0]) / (q * area), "CdA_m2": -float(fv[0]) / q, "Cy": float(fv[1]) / (q * area), "Cl": float(fv[2]) / (q * area),
             "side_centre_height_m": -float(mv[0]) / float(fv[1]) if abs(fv[1]) > 1e-6 else None}  # Mx = y Fz - z Fy
    assumptions = ["空気は高さによらず一様 (大気の境界層なし)", "外形は部品の箱と部材の管と車輪の円柱 (外板の曲面は設計に無い)",
                   "タイヤと地面のすき間が 1.5 格子より狭い所は箱で埋める",
                   f"壁は格子 {dx * 1000:.0f} mm の補間つき跳ね返し、壁関数なし、Smagorinsky Cs={smagorinsky}", "冷却の流れは入れない"] + info["assumptions"]
    return VehicleRun(design.get("design_version"), yaw_deg, speed_ms, air, dx,
                      {"Fx": float(fv[0]), "Fy": float(fv[1]), "Fz": float(fv[2]), "Mx": float(mv[0]), "My": float(mv[1]), "Mz": float(mv[2])},
                      groups, coeff, rep, conv, numerics, assumptions, res, res.fields, info)

"""Reference bodies, what they should measure and where those values come from, and the runs against them.

Every external number stands next to its source. `verified` says how far it was checked:
  "read"       the value (or formula) was read at the URL given, in the words or table of that page
  "secondary"  the page read at the URL quotes or redraws the original; the original itself was not read
  "確かめていない"  no page that could be read gives this value; it is listed because it is commonly used

The tolerances are this project's choice, fixed before the first run was compared with them. They are not an
external standard. A result outside them stays recorded as not validated; they are not widened to pass.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from hiraku.cfd.cuda import Gpu, gpu_turn, source_hash
from hiraku.cfd.geometry import Cylinder, Grid, Polytope, Shape, Sphere, Voxels, box, geometry_report, voxelize
from hiraku.cfd.solver import Flow, MemoryBudgetError, Result, Simulation, ground_cf, run

HERE = Path(__file__).resolve().parent
RECORD = HERE / "data" / "validation.json"
UNVERIFIED = "確かめていない"
TURN_S = 240.0  # one GPU turn at most, so the others on the GPU get theirs (gpu_turn is a first-come queue)
SETTLED_SPREAD_PCT = 5.0  # a level whose quarter-means differ more than this has no settled mean: it cannot count as validated


# --- sphere correlations -------------------------------------------------------------------------------------


def schiller_naumann(re: float) -> float:
    """Cd = 24/Re (1 + 0.15 Re^0.687).

    The form with the 1000 switch is in the OpenFOAM source (https://cpp.openfoam.org/v13/SchillerNaumannDrag_8C_source.html);
    MFIX-Exa's sphere tutorial uses it as 1.0917 at Re = 100
    (https://mfix.netl.doe.gov/doc/mfix-exa/guide/latest/tutorials/fluid/steady-flow-around-sphere.html).
    The 1933 paper was not read. A fit to measurements, not an exact value."""
    return 24.0 / re * (1.0 + 0.15 * re ** 0.687)


def clift_gauvin(re: float) -> float:
    """Cd = 24/Re (1 + 0.15 Re^0.687) + 0.42 / (1 + 42500 Re^-1.16), Re < 3e5, within 6 % of measurements.

    As given in arXiv:1810.08787, Eq. (4), which cites Clift & Gauvin; the original was not read."""
    return 24.0 / re * (1.0 + 0.15 * re ** 0.687) + 0.42 / (1.0 + 42500.0 * re ** -1.16)


def clift_low_re(re: float) -> float:
    """Cd = 24/Re (1 + 0.1935 Re^0.6305), 20 <= Re <= 260, as arXiv:2206.06875, Eq. (20)."""
    return 24.0 / re * (1.0 + 0.1935 * re ** 0.6305)


def morrison(re: float) -> float:
    """Morrison (2013), Data Correlation for Drag Coefficient for Sphere, Eq. (1), a fit to Schlichting's curve.

    https://galileoandeinstein.phys.virginia.edu/more_stuff/Applets/Projectile/DataCorrelationForSphereDrag2013.pdf"""
    a = re / 5.0
    b = re / 263000.0
    return 24.0 / re + 2.6 * a / (1.0 + a ** 1.52) + 0.411 * b ** -7.94 / (1.0 + b ** -8.0) + re ** 0.8 / 461000.0


# --- what each body should measure ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reference:
    value: float
    source: str
    url: str
    verified: str
    note: str = ""


SN = "Schiller-Naumann correlation"
SN_URL = "https://cpp.openfoam.org/v13/SchillerNaumannDrag_8C_source.html"
CG = "Clift-Gauvin correlation, quoted in arXiv:1810.08787 Eq. (4)"
CG_URL = "https://arxiv.org/pdf/1810.08787"
TABLE_2206 = "arXiv:2206.06875 Table 5"
TABLE_2206_URL = "https://arxiv.org/pdf/2206.06875"
MORRISON = "Morrison (2013) Eq. (1), fit to the curve in Schlichting (1955)"
MORRISON_URL = "https://galileoandeinstein.phys.virginia.edu/more_stuff/Applets/Projectile/DataCorrelationForSphereDrag2013.pdf"
HOERNER_CHART = "Hoerner, Fluid-Dynamic Drag, redrawn table (Wikimedia Commons, CMG Lee), Re 1e4 to 1e6"
HOERNER_URL = "https://commons.wikimedia.org/wiki/File:Hoerner_fluid_dynamic_drag_coefficients.svg"


def sphere_references(re: float) -> list[Reference]:
    """The first reference is the one the error is measured against."""
    refs: list[Reference] = []
    if re <= 1000.0:
        refs.append(Reference(schiller_naumann(re), SN, SN_URL, "read", "相関式。上限は Re 800 (arXiv:2401.10878) や 1000 (OpenFOAM) とされ、Re 1000 は上限いっぱい"))
    else:
        refs.append(Reference(clift_gauvin(re), CG, CG_URL, "secondary", "原論文は読んでいない。論文は測定値との差 6 % 以内と書いている"))
    refs.append(Reference(morrison(re), MORRISON, MORRISON_URL, "read", "相関式"))
    if re == 100.0:
        refs.append(Reference(1.08, "Roos & Willmarth (1971), AIAA J. 9(2), measured, as listed in " + TABLE_2206, TABLE_2206_URL, "secondary"))
        refs.append(Reference(1.0895, "Tabata & Itakura, computed, as listed in " + TABLE_2206, TABLE_2206_URL, "secondary"))
    if re == 200.0:
        refs.append(Reference(0.732, "Roos & Willmarth (1971), AIAA J. 9(2), measured, as listed in " + TABLE_2206, TABLE_2206_URL, "secondary"))
        refs.append(Reference(0.77176, "Tabata & Itakura, computed, as listed in " + TABLE_2206, TABLE_2206_URL, "secondary"))
    if 20.0 <= re <= 260.0:
        refs.append(Reference(clift_low_re(re), "correlation 24/Re (1 + 0.1935 Re^0.6305), " + TABLE_2206 + " Eq. (20)", TABLE_2206_URL, "read"))
    if re == 1e4:
        refs.append(Reference(0.38, "lowest sphere Cd of the Newton regime, at Re 5e3, arXiv:1810.08787 citing Clift & Gauvin", CG_URL, "secondary"))
    return refs


REFERENCES: dict[str, list[Reference]] = {
    "plate": [
        Reference(1.17, HOERNER_CHART, HOERNER_URL, "secondary", "Hoerner の原典のページは読んでいない。図の注記には 1.18 とも 1.1 とも書かれる"),
        Reference(1.17, "Wikipedia, Drag coefficient: a real square flat plate is often given as 1.17", "https://en.wikipedia.org/wiki/Drag_coefficient", "read", "出典の指定のない記述 (citation needed)"),
        Reference(1.28, "NASA Glenn, Shape Effects on Drag: a flat plate has Cd = 1.28", "https://www.grc.nasa.gov/www/k-12/VirtualAero/BottleRocket/airplane/shaped.html", "read", "板の形は書かれていない。正方形の値とは限らない"),
    ],
    "cube": [
        Reference(1.05, HOERNER_CHART, HOERNER_URL, "secondary", "面を流れに向けた立方体。Hoerner の原典のページは読んでいない"),
    ],
    "ahmed25": [
        Reference(0.2875, "SimScale's validation page, reading Fig. 4 of Ahmed et al. (1984), SAE 840300", "https://www.simscale.com/docs/validation-cases/aerodynamics-flow-around-the-ahmed-body/", "secondary",
                  "第三者が図から読み取った値で、表の値ではない。論文の本文は読んでいない。Re 4.29e6 の実験。SimScale はこの頁で投影面積に 0.115 m2 を使う"),
        Reference(0.285, "Ahmed et al. (1984), SAE 840300, as commonly quoted", "https://www.sae.org/publications/technical-papers/content/840300/", UNVERIFIED, "この値を書いたページを読めていない"),
        Reference(0.299, "Meile et al. (2011), CFD Letters 3(1), as listed in Table 1 of SimFlow's validation page", "https://help.sim-flow.com/validation/ahmed-body", "secondary",
                  "別の風洞の別の実験で、Re 2.784e6。Ahmed の値ではない。論文そのものは読んでいない"),
    ],
    "ahmed35": [
        Reference(0.26, "Ahmed et al. (1984), SAE 840300, as commonly quoted", "https://www.sae.org/publications/technical-papers/content/840300/", UNVERIFIED, "この値を書いたページを読めていない。検証の基準にはできない"),
    ],
}

# Project tolerances on the error against the first reference, fixed before the runs.
TOLERANCE_PCT: dict[str, float] = {"sphere": 5.0, "sphere_turbulent": 10.0, "plate": 10.0, "cube": 10.0, "ahmed25": 10.0, "ahmed35": 10.0}

# Ahmed body, from the dimensioned figure at https://www.cfd-online.com/Wiki/Ahmed_body (Fig. 1, from Hinterberger et al.
# 2004): length 1044, width 389, height 288, clearance 50, front radius 100 in side and plan views, slant length 222,
# stilts diameter 30 at 202 and 202 + 470 from the nose and 163.5 either side of the centre line.
# Flow from the same page: 60 m/s, Re = 4.29e6 on the length. ERCOFTAC's page says 4.9e6 for 60 m/s
# (https://kbwiki.ercoftac.org/w/index.php/AC_1-05); the 4.29e6 is also in arXiv:2001.11074, so it is what runs here.
AHMED_MM = {"length": 1044.0, "width": 389.0, "height": 288.0, "clearance": 50.0, "radius": 100.0, "slant_length": 222.0,
            "stilt_diameter": 30.0, "stilt_x": (202.0, 672.0), "stilt_y": 163.5}
AHMED_RE = 4.29e6
AHMED_AREA_M2 = 0.389 * 0.288  # the rear part is the full rectangle, so the rounded nose does not reduce it

# The ground under the Ahmed body. The test had a static floor (arXiv:2001.11074, section 1) whose boundary layer began
# at a ground board; the board's length and the layer's thickness at the model were not read. In this box a still
# ground starts its layer at the box's inlet instead, 1.25 L ahead of the nose, under the log-law stress of the
# solver's ground. That is the same kind of layer at a thickness the test did not necessarily have: by the 1/5-power
# textbook estimate 0.37 x Re_x^-0.2 (確かめていない) about 22 mm at the nose, against a clearance of 50 mm.
# A ground moving with the air has no layer at all, and no stress. Neither is the tunnel. The still ground is the
# baseline, since the test's floor did not move; the moving one is kept beside it as the size of that choice.
GROUND_WITH_AIR = (1.0, 0.0)
GROUND_STATIC = (0.0, 0.0)
LOG_KAPPA = 0.41  # log-law constants, textbook values: 確かめていない
LOG_B = 5.2


def smooth_wall_z0(nu: float, U: float, dx: float) -> float:
    """z0 of a hydraulically smooth wall, z0 = exp(-kappa B) nu / u_tau, with u_tau from the log law at the first row.

    The solver's default roughness is a road's. A wind tunnel's floor is smooth, so this is what the Ahmed runs use."""
    u_tau = 0.05 * U
    for _ in range(50):
        u_tau = U * LOG_KAPPA / (math.log(0.5 * dx * u_tau / nu) + LOG_KAPPA * LOG_B)
    return math.exp(-LOG_KAPPA * LOG_B) * nu / u_tau


def sphere(center: Sequence[float] = (0.0, 0.0, 0.0), diameter: float = 1.0) -> list[Shape]:
    return [Sphere(np.asarray(center, float), diameter / 2.0)]


def thin_plate(side: float = 1.0, thickness: float = 0.02, center: Sequence[float] = (0.0, 0.0, 0.0)) -> list[Shape]:
    """A square plate normal to the air (+x)."""
    return [box(center, (thickness, side, side), name="plate")]


def cube(side: float = 1.0, center: Sequence[float] = (0.0, 0.0, 0.0)) -> list[Shape]:
    return [box(center, (side, side, side), name="cube")]


# A plate or cube exactly centred on a symmetric grid keeps a symmetric wake until rounding noise breaks it, and the
# drag waits with it: at 12 cells the plate climbed at t* of 45 to 60, against 15 to 20 with this offset (see the record).
# When that happens depends on rounding, so a mean over a fixed window would too. A real body is not that symmetric.
OFFSET_CELLS = (0.13, 0.26)  # along y and z, in cells: x stays, so a thin plate stays between its two node planes


def _offset_center(cells: int) -> tuple[float, float, float]:
    return (0.0, OFFSET_CELLS[0] / cells, OFFSET_CELLS[1] / cells)


def ahmed_body(slant_deg: float, nose_x: float = 0.0, steps_deg: float = 10.0) -> Polytope:
    """The convex body, metres, air along +x, nose at nose_x, centre line y = 0, ground z = 0.

    The front is the box core swept by a ball of the nose radius: tangent planes every steps_deg along the round and
    every 15 degrees around it, so the facets stay under 1 mm from the round, a tenth of the finest cell here. The
    corner patch as a ball is a choice: the figure gives R100 in the side and plan views, not the corner's surface."""
    m = AHMED_MM
    mm = 1e-3
    L, W, H, c, r = m["length"] * mm, m["width"] * mm, m["height"] * mm, m["clearance"] * mm, m["radius"] * mm
    z0, z1 = c, c + H
    phi = math.radians(slant_deg)
    slant = m["slant_length"] * mm
    xs = nose_x + L - slant * math.cos(phi)
    normals: list[Sequence[float]] = [(1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0), (0.0, -1.0, 0.0), (math.sin(phi), 0.0, math.cos(phi))]
    offsets: list[float] = [nose_x + L, z1, -z0, W / 2.0, W / 2.0, math.sin(phi) * xs + math.cos(phi) * z1]
    # the four corners of the core at the nose
    core = np.array([[nose_x + r, sy * (W / 2.0 - r), sz] for sy in (-1.0, 1.0) for sz in (z0 + r, z1 - r)])
    normals.append((-1.0, 0.0, 0.0))
    offsets.append(-nose_x)
    alpha = np.arange(steps_deg, 90.0, steps_deg)
    beta = np.arange(0.0, 360.0, 15.0)
    for a in np.radians(alpha):
        for b in np.radians(beta):
            n = np.array([-math.cos(a), math.sin(a) * math.cos(b), math.sin(a) * math.sin(b)])
            normals.append(tuple(n))
            offsets.append(float((core @ n).max() + r))
    corners = np.array([[x, y, z] for x in (nose_x, nose_x + L) for y in (-W / 2.0, W / 2.0) for z in (z0, z1)])
    return Polytope(np.array(normals), np.array(offsets), corners, f"ahmed_{slant_deg:g}", "body")


def ahmed_stilts(nose_x: float = 0.0) -> list[Shape]:
    """Four stilts from the ground into the body's floor. Group "stilts", so the body's own drag stays apart."""
    m = AHMED_MM
    mm = 1e-3
    d = m["stilt_diameter"] * mm
    top = (m["clearance"] + 10.0) * mm  # 10 mm into the body, so nothing stands open between them
    out: list[Shape] = []
    for x in m["stilt_x"]:
        for sy in (-1.0, 1.0):
            out.append(Cylinder((nose_x + x * mm, sy * m["stilt_y"] * mm, top / 2.0), (0.0, 0.0, 1.0), d / 2.0, top, f"stilt_{x:g}_{sy:g}", "stilts"))
    return out


def ahmed_shapes(slant_deg: float, nose_x: float = 0.0) -> list[Shape]:
    return [ahmed_body(slant_deg, nose_x), *ahmed_stilts(nose_x)]


# --- one run -------------------------------------------------------------------------------------------------


@dataclass
class Case:
    """One run. Lengths in L. With a ground the box stands on z = 0 and `height` is its top; otherwise +-height."""

    body: str
    shapes: list[Shape]
    L: float
    area: float
    re: float
    cells: int  # cells per L
    u_lattice: float = 0.05
    smagorinsky: float = 0.0
    upstream: float = 5.0
    downstream: float = 11.0
    half_width: float = 5.0
    height: float = 5.0
    ground: tuple[float, float] | None = None  # ground velocity as a share of the air speed
    ground_roughness_m: float | None = None  # None: the smooth wall of smooth_wall_z0
    t_star: float = 60.0
    ramp_t_star: float = 3.0
    average_last: float = 0.35
    interpolate: bool = True
    sponge_L: float = 0.6  # absorbing layer, in L, at least 8 cells; twice that at the outlet
    sponge: tuple[int, int, int, int, int, int] | None = None  # cells, instead of sponge_L
    sponge_strength: float = 0.1
    tag: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        return f"{self.body} Re={self.re:g} cells={self.cells} u={self.u_lattice:g} Cs={self.smagorinsky:g} {self.tag}".strip()


def code_hashes() -> dict[str, str]:
    """Which code a number came from."""
    out = {"kernels.cu": source_hash()[:16]}
    for name in ("solver.py", "geometry.py"):
        out[name] = hashlib.sha256((HERE / name).read_bytes()).hexdigest()[:16]
    return out


def case_grid(case: Case) -> Grid:
    dx = case.L / case.cells
    nx = int(round((case.upstream + case.downstream) * case.cells))
    ny = int(round(2.0 * case.half_width * case.cells))
    if case.ground is not None:
        nz = int(round(case.height * case.cells))
        origin = np.array([-case.upstream * case.L, -case.half_width * case.L, 0.0])
    else:
        nz = int(round(2.0 * case.height * case.cells))
        origin = np.array([-case.upstream * case.L, -case.half_width * case.L, -case.height * case.L])
    return Grid(nx, ny, nz, dx, origin)


def case_memory_mib(case: Case) -> float:
    g = case_grid(case)
    return g.n_total * 153 / 2 ** 20


@dataclass
class Prepared:
    """A case made ready on the host: nothing here holds the GPU."""

    grid: Grid
    vox: Voxels
    flow: Flow
    sponge: tuple[int, int, int, int, int, int]
    z0: float
    steps: int
    record_every: int


def prepare(case: Case) -> Prepared:
    grid = case_grid(case)
    vox = voxelize(case.shapes, grid, ground=case.ground is not None)
    U = 1.0
    nu = U * case.L / case.re
    if case.sponge is not None:
        sponge = case.sponge
    else:
        s = max(8, int(round(case.sponge_L * case.cells)))
        sponge = (s, 2 * s, s, s, 0 if case.ground is not None else s, s)
    ground_velocity = None if case.ground is None else (case.ground[0] * U, case.ground[1] * U)
    z0 = case.ground_roughness_m if case.ground_roughness_m is not None else smooth_wall_z0(nu, U, grid.dx)
    flow = Flow(U, rho=1.0, nu=nu, u_lattice=case.u_lattice, smagorinsky=case.smagorinsky, ground_velocity_ms=ground_velocity, ground_roughness_m=z0,
                ramp_steps=int(case.ramp_t_star * case.cells / case.u_lattice), sponge_cells=sponge, sponge_strength=case.sponge_strength)
    return Prepared(grid, vox, flow, sponge, z0, int(case.t_star * case.cells / case.u_lattice), max(10, case.cells // 2))


def park_state(sim: Simulation, path: Path) -> None:
    """The current populations to a file, one population at a time: the host never holds the whole state."""
    n = sim.grid.n_total
    row = np.zeros(n, np.float32)
    source = sim.buf["A" if sim.src_is_a else "B"]
    with open(path, "wb") as f:
        for q in range(19):
            source.read_into(row, offset=q * 4 * n)
            row.tofile(f)


def restore_state(sim: Simulation, path: Path, done: int) -> None:
    """Put a parked state into a new Simulation, as if it had run `done` steps itself."""
    sim.step_count = done
    if sim.flow.ramp_steps == 0 or done >= sim.flow.ramp_steps:
        sim.fill_ramp(1.0)
    n = sim.grid.n_total
    target = sim.buf["A" if sim.src_is_a else "B"]
    with open(path, "rb") as f:
        for q in range(19):
            target.write(np.fromfile(f, np.float32, n), offset=q * 4 * n)


def run_case(gpu: Gpu, case: Case, reserve_mib: int = 600) -> dict[str, Any]:
    """Run one short case on the GPU the caller holds. Longer ones: run_case_in_turns."""
    p = prepare(case)
    sim = Simulation(gpu, p.vox, p.flow, ref_point=(0.0, 0.0, 0.0), interpolate=case.interpolate, reserve_mib=reserve_mib)
    share = sim.links.interpolated_share
    try:
        res = run(sim, p.steps, record_every=p.record_every, stats_every=max(500, p.steps // 40))
    finally:
        sim.close()
    return case_record(case, p, res, share)


def run_case_in_turns(case: Case, turn_s: float = TURN_S, reserve_mib: int = 600, memory_wait_s: float = 60.0, memory_waits: int = 60) -> dict[str, Any]:
    """Run one case in GPU turns of at most turn_s, so the others on the GPU get theirs in between.

    Between two turns the state waits in a temporary file: neither the GPU nor the host memory holds it. The running
    force sum carries over, so the record is the same as one long run (test_gpu checks a restart against a straight run)."""
    p = prepare(case)
    done = 0
    handle, name = tempfile.mkstemp(prefix="hiraku-cfd-state-", suffix=".bin")
    os.close(handle)
    state = Path(name)
    parked = False
    steps_log = [np.zeros(1, np.int64)]
    impulse_log = [np.zeros((1, len(p.vox.groups), 6))]
    stats: list[dict[str, float]] = []
    wall = 0.0
    faulted: str | None = None
    share = 0.0
    memory = 0
    device = ""
    units = None
    turns = 0
    waited = 0
    try:
        while done < p.steps and faulted is None:
            try:
                turn_done, turn_fault = _one_turn(case, p, state, parked, done, steps_log, impulse_log, stats, turn_s, reserve_mib)
            except MemoryBudgetError as e:
                waited += 1
                if waited > memory_waits:
                    raise
                print(f"[{time.strftime('%H:%M:%S')}] waits for memory: {case.describe()}: {e}", flush=True)
                time.sleep(memory_wait_s)
                continue
            done, faulted = turn_done["done"], turn_fault
            wall += turn_done["wall"]
            share, units, device = turn_done["share"], turn_done["units"], turn_done["device"]
            memory = max(memory, turn_done["memory"])
            parked = True
            turns += 1
            if done < p.steps:
                print(f"[{time.strftime('%H:%M:%S')}]   {case.describe()}: {done}/{p.steps} steps after turn {turns}", flush=True)
    finally:
        state.unlink(missing_ok=True)
    n_cells = p.grid.nx * p.grid.ny * p.grid.nz
    res = Result(np.concatenate(steps_log), np.concatenate(impulse_log), p.vox.groups, units, p.grid, stats=stats, wall_time_s=wall, memory_bytes=memory,
                 mlups=n_cells * done / wall / 1e6 if wall > 0 else 0.0, device=device, faulted=faulted)
    rec = case_record(case, p, res, share)
    rec["gpu_turns"] = turns
    return rec


def _one_turn(case: Case, p: Prepared, state: Path, parked: bool, done: int, steps_log: list[np.ndarray], impulse_log: list[np.ndarray], stats: list[dict[str, float]],
              turn_s: float, reserve_mib: int) -> tuple[dict[str, Any], str | None]:
    """One GPU turn: restore, advance until the turn is used up, park. Appends to the logs."""
    faulted: str | None = None
    with gpu_turn(), Gpu() as gpu:
        sim = Simulation(gpu, p.vox, p.flow, ref_point=(0.0, 0.0, 0.0), interpolate=case.interpolate, reserve_mib=reserve_mib)
        try:
            share, units, device, memory = sim.links.interpolated_share, sim.units, gpu.name, gpu.allocated
            if parked:
                restore_state(sim, state, done)
            offset = impulse_log[-1][-1].copy()
            t_turn = time.perf_counter()
            block = min(p.steps - done, 10 * p.record_every)
            while True:
                res = run(sim, block, record_every=p.record_every, stats_every=block)
                steps_log.append(res.steps[1:] + done)
                impulse_log.append(res.impulse[1:] + offset)
                stats += res.stats
                done += int(res.steps[-1])
                faulted = res.faulted
                elapsed = time.perf_counter() - t_turn
                if faulted or done >= p.steps or elapsed >= turn_s:
                    break
                # about ten seconds at a time, and never far past the end of the turn
                rate = int(res.steps[-1]) / max(res.wall_time_s, 1e-3)
                block = int(max(p.record_every, min(10.0, turn_s - elapsed + 1.0) * rate))
                block = min(p.steps - done, block - block % p.record_every or p.record_every)
            wall = time.perf_counter() - t_turn
            if done < p.steps and faulted is None:
                park_state(sim, state)
        finally:
            sim.close()
    return {"done": done, "wall": wall, "share": share, "units": units, "device": device, "memory": memory}, faulted


def case_record(case: Case, p: Prepared, res: Result, interpolated_share: float) -> dict[str, Any]:
    """The record of a finished run: numbers only, no fields."""
    grid, flow, steps = p.grid, p.flow, p.steps
    U = flow.speed_ms
    t0 = time.time()
    q = 0.5 * flow.rho * U * U * case.area
    k = res.units.force_N / q
    out: dict[str, Any] = {
        "body": case.body, "tag": case.tag, "re": case.re, "cells": case.cells, "u_lattice": case.u_lattice, "smagorinsky": case.smagorinsky,
        "tau0": res.units.tau0, "domain_L": {"upstream": case.upstream, "downstream": case.downstream, "half_width": case.half_width, "height": case.height},
        "ground": list(case.ground) if case.ground is not None else None, "ground_cf": ground_cf(grid.dx, p.z0) if case.ground is not None else None,
        "grid": [grid.nx, grid.ny, grid.nz], "sponge_cells": list(p.sponge), "sponge_strength": case.sponge_strength,
        "t_star": case.t_star, "average_last": case.average_last, "steps": int(res.steps[-1]), "interpolate": case.interpolate, "interpolated_share": interpolated_share,
        "mlups": res.mlups, "wall_s": res.wall_time_s, "memory_mib": res.memory_bytes / 2 ** 20, "device": res.device, "code": code_hashes(),
        "reference_area": case.area, "extra": case.extra, "faulted": res.faulted,
    }
    if res.faulted:
        return out
    vox = p.vox
    first = int(steps * (1.0 - case.average_last))
    mean = res.mean(first)
    total = mean.sum(axis=0)
    out["cd"] = float(total[0] * k)
    out["cy"] = float(total[1] * k)
    out["cz"] = float(total[2] * k)
    out["cd_by_group"] = {name: float(mean[i, 0] * k) for i, name in enumerate(res.groups)}
    windows = []
    for w in range(4):
        a = first + (steps - first) * w // 4
        b = first + (steps - first) * (w + 1) // 4
        windows.append(float(res.mean(a, b).sum(axis=0)[0] * k))
    out["cd_quarters"] = windows
    out["spread_pct"] = 100.0 * (max(windows) - min(windows)) / abs(out["cd"]) if out["cd"] else None
    # the whole history as twenty window means, so the record shows how the force settled
    edges = np.linspace(0, steps, 21).astype(int)
    out["cd_history"] = [float(res.mean(int(edges[i]), int(edges[i + 1])).sum(axis=0)[0] * k) for i in range(20)]
    if res.stats:
        out["max_mach"] = max(s["max_speed"] for s in res.stats) * math.sqrt(3.0)
        last = res.stats[-1]
        out["last_stats"] = {"max_dr": last["max_dr"], "min_dr": last["min_dr"], "negative_f": last["negative_f"], "mass_dev": last["mass_dev"]}
    report = geometry_report(vox)
    out["geometry"] = {key: report[key] for key in ("solid_nodes", "cut_links", "projected_area_x_m2", "shapes_without_cut_links") if key in report}
    # Named apart from the Cd errors on purpose: uncertainty.py takes the largest of every key ending in error_pct,
    # and how well the grid sees the area is not an error of a flow result.
    out["geometry"]["grid_area_x_diff_pct"] = report.get("grid_projected_area_x_error_pct")
    out["geometry"]["voxel_volume_diff_pct"] = report.get("voxel_volume_error_pct")
    out["enclosed_nodes"] = getattr(vox, "enclosed_nodes", 0)
    out["seconds_total"] = time.time() - t0
    return out


def richardson(levels: Sequence[tuple[float, float]]) -> dict[str, Any]:
    """Extrapolation in the cell size from three levels (cells per L, value), only when the values are monotone.

    Finds the order p with (v1 - v2) / (v2 - v3) = (h1^p - h2^p) / (h2^p - h3^p). Anything else says so."""
    ordered = sorted(levels, key=lambda a: a[0])
    if len(ordered) < 3:
        return {"monotone": None, "reason": "三つの格子がないので外挿しない"}
    (c1, v1), (c2, v2), (c3, v3) = ordered[-3:]
    d1, d2 = v1 - v2, v2 - v3
    if d1 == 0.0 or d2 == 0.0 or d1 * d2 < 0.0:
        return {"monotone": False, "reason": "単調でないので外挿しない", "values": [v1, v2, v3]}
    h1, h2, h3 = 1.0 / c1, 1.0 / c2, 1.0 / c3
    ratio = d1 / d2

    def f(p: float) -> float:
        return (h1 ** p - h2 ** p) / (h2 ** p - h3 ** p) - ratio

    lo, hi = 0.1, 8.0
    if f(lo) * f(hi) > 0.0:
        return {"monotone": True, "reason": "単調だが次数が 0.1 から 8 に収まらないので外挿しない", "values": [v1, v2, v3]}
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if f(lo) * f(mid) <= 0.0:
            hi = mid
        else:
            lo = mid
    p = 0.5 * (lo + hi)
    value = v3 + (v3 - v2) * h3 ** p / (h2 ** p - h3 ** p)
    return {"monotone": True, "order": p, "extrapolated": value, "levels": [c1, c2, c3]}


# --- the bodies as cases -------------------------------------------------------------------------------------


def sphere_case(re: float, cells: int, **kw: Any) -> Case:
    """A sphere. Its wake is steady at Re 100 and 200, so it stays centred there. Above that it gets the offset: centred,
    Re 300 stayed steady for the whole run, and Re 500 and 1000 held a symmetric low-drag wake until t* of 45 to 55."""
    cs = kw.pop("smagorinsky", 0.15 if re >= 5000.0 else 0.0)
    shape = kw.pop("shapes", None) or sphere(_offset_center(cells) if re > 250.0 else (0.0, 0.0, 0.0))
    return Case("sphere", shape, 1.0, math.pi / 4.0, re, cells, smagorinsky=cs, **kw)


def plate_case(cells: int, re: float = 1e5, **kw: Any) -> Case:
    kw.setdefault("smagorinsky", 0.15)
    kw.setdefault("upstream", 4.0)
    kw.setdefault("downstream", 8.0)
    kw.setdefault("half_width", 4.0)
    kw.setdefault("height", 4.0)
    kw.setdefault("t_star", 100.0)
    kw.setdefault("average_last", 0.5)
    shapes = kw.pop("shapes", None) or thin_plate(center=_offset_center(cells))
    return Case("plate", shapes, 1.0, 1.0, re, cells, **kw)


def cube_case(cells: int, re: float = 1e5, **kw: Any) -> Case:
    kw.setdefault("smagorinsky", 0.15)
    kw.setdefault("upstream", 4.0)
    kw.setdefault("downstream", 8.0)
    kw.setdefault("half_width", 4.0)
    kw.setdefault("height", 4.0)
    kw.setdefault("t_star", 100.0)
    kw.setdefault("average_last", 0.5)
    shapes = kw.pop("shapes", None) or cube(center=_offset_center(cells))
    return Case("cube", shapes, 1.0, 1.0, re, cells, **kw)


def ahmed_case(slant_deg: float, cells: int, ground: tuple[float, float] = GROUND_STATIC, **kw: Any) -> Case:
    """The body's nose at x = 0. The box in L: 1.25 ahead of the nose, 4.2 behind it, +-1.15, 1.9 high (blockage about 2.3 %)."""
    L = AHMED_MM["length"] * 1e-3
    kw.setdefault("smagorinsky", 0.15)
    kw.setdefault("upstream", 1.25)
    kw.setdefault("downstream", 4.2)
    kw.setdefault("half_width", 1.15)
    kw.setdefault("height", 1.9)
    kw.setdefault("t_star", 30.0)
    kw.setdefault("average_last", 0.5)
    kw.setdefault("sponge_L", 0.25)
    return Case(f"ahmed{slant_deg:g}", ahmed_shapes(slant_deg), L, AHMED_AREA_M2, AHMED_RE, cells, ground=ground, **kw)


def run_queue(cases: Sequence[Case], out_path: Path, turn_s: float = TURN_S, reserve_mib: int = 600) -> list[dict[str, Any]]:
    """Run the cases one after another, in GPU turns, appending each record to out_path as a JSON line."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for case in cases:
        print(f"[{time.strftime('%H:%M:%S')}] start {case.describe()} ({case_memory_mib(case):.0f} MiB)", flush=True)
        try:
            rec = run_case_in_turns(case, turn_s=turn_s, reserve_mib=reserve_mib)
        except MemoryBudgetError as e:
            rec = {"body": case.body, "tag": case.tag, "re": case.re, "cells": case.cells, "u_lattice": case.u_lattice, "faulted": f"did not fit the GPU memory free at the time: {e}"}
        results.append(rec)
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"[{time.strftime('%H:%M:%S')}] done  {case.describe()}: Cd={rec.get('cd')} spread={rec.get('spread_pct')} {rec.get('mlups', 0):.0f} MLUPS {rec.get('wall_s', 0):.0f}s turns={rec.get('gpu_turns')} fault={rec.get('faulted')}", flush=True)
    return results


# --- the committed record ------------------------------------------------------------------------------------


def body_id(body: str, re: float) -> str:
    return f"sphere_re{re:g}" if body == "sphere" else body


WANTED_BODIES = ("plate", "cube", "ahmed25", "sphere_re100", "sphere_re200", "sphere_re300", "sphere_re500", "sphere_re1000", "sphere_re10000", "ahmed35")


def tolerance_for(body: str, re: float) -> float:
    if body == "sphere":
        return TOLERANCE_PCT["sphere" if re <= 1000.0 else "sphere_turbulent"]
    return TOLERANCE_PCT[body]


def references_for(body: str, re: float) -> list[Reference]:
    return sphere_references(re) if body == "sphere" else REFERENCES[body]


def _reference_dict(r: Reference) -> dict[str, Any]:
    return {"value": r.value, "source": r.source, "url": r.url, "verified": r.verified, "note": r.note}


LEVEL_KEYS = ("cells", "cd", "u_lattice", "smagorinsky", "tau0", "t_star", "average_last", "grid", "steps", "spread_pct", "cd_quarters", "cy", "cz", "cd_by_group",
              "max_mach", "last_stats", "geometry", "memory_mib", "wall_s", "mlups", "code", "code_note", "faulted")


def summarize_body(body: str, re: float, levels: Sequence[dict[str, Any]], sensitivity: Sequence[dict[str, Any]] = (), note: str = "") -> dict[str, Any]:
    """One body's entry: its grid levels, the first reference, the error, and whether it counts as validated.

    uncertainty.py takes the largest of every key ending in error_pct in this file as the solver's error band. So a
    difference from a value nobody could read is kept under another key: it is not a measured error."""
    refs = references_for(body, re)
    main = refs[0]
    tol = tolerance_for(body, re)
    usable = main.verified != UNVERIFIED
    diff_key = "error_pct" if usable else "diff_from_unverified_pct"
    good = sorted((r for r in levels if r.get("cd") is not None), key=lambda r: r["cells"])
    rows = []
    for r in good:
        row = {k: r.get(k) for k in LEVEL_KEYS}
        row[diff_key] = 100.0 * (r["cd"] / main.value - 1.0)
        rows.append(row)
    entry: dict[str, Any] = {"body": body, "re": re, "reference": _reference_dict(main), "other_references": [_reference_dict(x) for x in refs[1:]],
                             "levels": rows, "tolerance_pct": tol, "reference_usable": usable, "sensitivity": list(sensitivity), "note": note}
    faulted = [{"cells": r.get("cells"), "faulted": r.get("faulted")} for r in levels if r.get("cd") is None]
    if faulted:
        entry["faulted_levels"] = faulted
    if not rows:
        entry.update(within_tolerance=False, validated=False, finest=None, richardson={"monotone": None, "reason": "結果がない"},
                     conclusion="結果がないので検証できていない。")
        return entry
    finest = rows[-1]
    entry["finest"] = {"cells": finest["cells"], "cd": finest["cd"], diff_key: finest[diff_key]}
    rich = richardson([(r["cells"], r["cd"]) for r in rows])
    if "extrapolated" in rich:
        rich[diff_key] = 100.0 * (rich["extrapolated"] / main.value - 1.0)
    entry["richardson"] = rich
    inside = abs(finest[diff_key]) <= tol
    settled = finest.get("spread_pct") is not None and finest["spread_pct"] <= SETTLED_SPREAD_PCT
    entry["within_tolerance"] = inside
    entry["settled"] = settled
    entry["validated"] = inside and usable and settled
    words = [f"一番細かい格子 ({finest['cells']} セル/代表長さ) で Cd = {finest['cd']:.4g}、基準 {main.value:.4g} との差 {finest[diff_key]:+.1f} %。"]
    words.append(f"許容 ±{tol:g} % に{'入る' if inside else '入らない'}。")
    if not usable:
        words.append("基準の値を読んで確かめられていないので、検証にはならない。")
    elif not inside:
        words.append("検証できていない。")
    elif not settled:
        words.append(f"力の平均がまだ落ち着いていないので (四分割の差 {finest['spread_pct']:.1f} %)、検証できていない。")
    if "extrapolated" in rich:
        words.append(f"格子を外挿すると {rich['extrapolated']:.4g} (次数 {rich['order']:.2f}、差 {rich[diff_key]:+.1f} %)。")
    else:
        words.append(str(rich.get("reason", "")))
    if note:
        words.append(note)
    entry["conclusion"] = "".join(words)
    return entry


def read_jsonl(paths: Sequence[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in paths:
        if Path(p).is_file():
            out += [json.loads(line) for line in Path(p).read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


def build_record(rows: Sequence[dict[str, Any]], sensitivity: dict[str, list[dict[str, Any]]], notes: dict[str, str], findings: Sequence[dict[str, Any]],
                 path: Path = RECORD) -> dict[str, Any]:
    """The committed record. rows without a tag are the grid levels; later runs of a level replace earlier ones."""
    groups: dict[str, dict[int, dict[str, Any]]] = {}
    meta: dict[str, tuple[str, float]] = {}
    for r in rows:
        if r.get("tag"):
            continue
        key = body_id(r["body"], r["re"])
        groups.setdefault(key, {})[r["cells"]] = r
        meta[key] = (r["body"], r["re"])
    bodies = {key: summarize_body(meta[key][0], meta[key][1], list(levels.values()), sensitivity.get(key, ()), notes.get(key, "")) for key, levels in groups.items()}
    record = {"schema": 1, "what": "GPU 流体計算の検証の記録。数値は run_case の出力そのまま。", "tolerances": "許容の幅はこの作品が走らせる前に決めたもので、外部の基準ではない。外れたものは検証できていないまま残し、幅を広げない。",
              "verified_words": {"read": "値か式をその URL の本文で読んだ", "secondary": "その URL が原典を引いている。原典そのものは読んでいない", UNVERIFIED: "この値を書いたページを読めていない"},
              "tolerance_pct": dict(TOLERANCE_PCT), "code": code_hashes(), "bodies": bodies, "not_run_yet": [key for key in WANTED_BODIES if key not in bodies], "findings": list(findings)}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8", newline="\n")
    return record


def _main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Runs the reference bodies (one at a time on the GPU).")
    parser.add_argument("set", choices=["sphere", "plate_cube", "ahmed"], help="sphere: Re 100 to 1e4; plate_cube: square plate and cube; ahmed: the Ahmed body. Three grid levels each")
    parser.add_argument("--out", default=os.environ.get("HIRAKU_BENCH_OUT", "benchmarks.jsonl"))
    args = parser.parse_args()
    cases: list[Case] = []
    if args.set == "sphere":
        for re in (100.0, 200.0, 300.0, 500.0, 1000.0, 1e4):
            cases += [sphere_case(re, c) for c in (10, 14, 20)]
    elif args.set == "plate_cube":
        cases += [plate_case(c) for c in (12, 16, 24)]
        cases += [cube_case(c) for c in (12, 16, 24)]
    else:
        for slant in (25.0, 35.0):
            cases += [ahmed_case(slant, c) for c in (42, 60, 84)]
    run_queue(cases, Path(args.out))


if __name__ == "__main__":
    _main()

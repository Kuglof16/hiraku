"""The GPU kernels against numpy written from the equations, on small grids.

The whole module takes one GPU turn (one flow job at a time across processes). With no GPU, or when the turn does
not come within HIRAKU_GPU_WAIT_S seconds, every test skips with the reason (counted one by one as skipped). Steps are few and grids
small: the checks run in seconds. The tolerances are FP32 ones; the measured errors are well under them.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from hiraku.cfd.benchmarks import park_state, restore_state
from hiraku.cfd.cuda import Gpu, gpu_or_reason, gpu_turn
from hiraku.cfd.geometry import Grid, Sphere, box, voxelize
from hiraku.cfd.lattice import C, OPP, W, collide, equilibrium_h
from hiraku.cfd.solver import Flow, Simulation, ground_cf, sponge_profile

_turn: Any = None
_gpu: Gpu | None = None
_why_not = ""  # why the module has no GPU: each test skips with it, so a machine with no GPU counts every test as skipped


def setUpModule() -> None:
    global _turn, _gpu, _why_not
    _turn = gpu_turn(wait_s=float(os.environ.get("HIRAKU_GPU_WAIT_S", "1800")))
    try:
        _turn.__enter__()
    except TimeoutError as e:
        _turn, _why_not = None, str(e)
        return
    _gpu, reason = gpu_or_reason()
    if _gpu is None:
        _turn.__exit__(None, None, None)
        _turn, _why_not = None, reason


class OnGpu(unittest.TestCase):
    """A test that needs the module's GPU: skipped, with the reason, when there is none."""

    def setUp(self) -> None:
        if _gpu is None:
            self.skipTest(_why_not or "no GPU")


def tearDownModule() -> None:
    global _turn, _gpu
    if _gpu is not None:
        _gpu.close()
        _gpu = None
    if _turn is not None:
        _turn.__exit__(None, None, None)
        _turn = None


def lattice_grid(nx: int, ny: int, nz: int) -> Grid:
    """dx = 1 with the origin at zero: node (i, j, k) stands at (i + 1/2, j + 1/2, k + 1/2)."""
    return Grid(nx, ny, nz, 1.0, np.zeros(3))


def flow_for(tau: float, u: float = 0.05, sponge: tuple[int, int, int, int, int, int] = (0, 0, 0, 0, 0, 0), **kw: Any) -> Flow:
    """Speed 1 m/s and dx = 1 m: nu (m^2/s) so that the lattice has this relaxation time."""
    return Flow(1.0, rho=1.0, nu=(tau - 0.5) / (3.0 * u), u_lattice=u, sponge_cells=sponge, **kw)


def open_case(vox_grid: Grid, flow: Flow, shapes: list[Any] | None = None, interpolate: bool = True, ground: bool = False) -> Simulation:
    return Simulation(_gpu, voxelize(shapes or [], vox_grid, ground=ground), flow, interpolate=interpolate, reserve_mib=100)


def read3(sim: Simulation) -> np.ndarray:
    g = sim.grid
    return sim.read_populations().reshape(19, g.nz + 2, g.ny + 2, g.nx + 2).astype(np.float64)


def write_interior(sim: Simulation, interior: np.ndarray) -> np.ndarray:
    """Interior populations (19, nz, ny, nx) over the ghost layer the far field wrote. Returns the whole state."""
    h = read3(sim)
    h[:, 1:-1, 1:-1, 1:-1] = interior
    sim.write_populations(h.reshape(19, -1))
    return read3(sim)  # as stored: 32-bit


def uniform_interior(g: Grid, u: tuple[float, float, float], dr: float = 0.0) -> np.ndarray:
    shape = (g.nz, g.ny, g.nx)
    uu = np.stack([np.full(shape, c, float) for c in u])
    return equilibrium_h(np.full(shape, dr, float), uu)


def pull(h3: np.ndarray) -> np.ndarray:
    """Pull streaming, in place of the kernel's own: the interior (19, nz, ny, nx) after one stream."""
    nz, ny, nx = h3.shape[1] - 2, h3.shape[2] - 2, h3.shape[3] - 2
    out = np.empty((19, nz, ny, nx))
    for q in range(19):
        cx, cy, cz = (int(v) for v in C[q])
        out[q] = h3[q, 1 - cz:nz + 1 - cz, 1 - cy:ny + 1 - cy, 1 - cx:nx + 1 - cx]
    return out


def far_equilibrium(u: float) -> np.ndarray:
    """The far-field equilibrium (density one, velocity u along x) as h, shape (19,)."""
    cu = C[:, 0].astype(float) * u
    return W * (3.0 * cu + 4.5 * cu * cu - 1.5 * u * u)


def fluid_momentum(sim: Simulation, h3: np.ndarray) -> np.ndarray:
    """Sum of c_i h_i over the fluid nodes, in double precision."""
    g = sim.grid
    mask = sim.vox.mask.reshape(g.nz + 2, g.ny + 2, g.nx + 2)[1:-1, 1:-1, 1:-1]
    inner = h3[:, 1:-1, 1:-1, 1:-1]
    return np.array([sum(int(C[q, a]) * inner[q][mask == 0].sum() for q in range(19)) for a in range(3)])


def rough_field(g: Grid, seed: int) -> np.ndarray:
    """A nonuniform interior with a nonequilibrium part in every moment."""
    rng = np.random.default_rng(seed)
    shape = (g.nz, g.ny, g.nx)
    dr = 0.01 * rng.normal(size=shape)
    u = 0.03 * rng.normal(size=(3,) + shape)
    return equilibrium_h(dr, u) + 1e-3 * W.reshape(19, 1, 1, 1) * rng.normal(size=(19,) + shape)


class OneStep(OnGpu):
    """One GPU step against an independent pull stream and lattice.collide, population by population."""

    FP32 = 2e-6  # |h| ~ 0.1: a few units in the last place after the moments and the rebuild

    def check(self, tau: float, cs: float, sponge: tuple[int, int, int, int, int, int] = (0,) * 6) -> None:
        g = lattice_grid(9, 7, 6)
        flow = flow_for(tau, smagorinsky=cs, sponge=sponge, sponge_strength=0.1)
        sim = open_case(g, flow)
        try:
            h0 = write_interior(sim, rough_field(g, 21))
            sim.step(1)
            _gpu.sync()
            h1 = read3(sim)
            pulled = pull(h0)
            tau32 = float(np.float32(sim.units.tau0))
            cs232 = float(np.float32(cs * cs))
            want = collide(pulled, tau32, cs232)
            if any(sponge):
                s = flow.sponge_cells
                sig_x = sponge_profile(g.nx, s[0], s[1], flow.sponge_strength).astype(float)
                sig_y = sponge_profile(g.ny, s[2], s[3], flow.sponge_strength).astype(float)
                sig_z = sponge_profile(g.nz, s[4] or s[5], s[5], flow.sponge_strength).astype(float)
                sigma = sig_z[:, None, None] + sig_y[None, :, None] + sig_x[None, None, :]
                self.assertGreater(float(sigma.max()), 0.0)
                far = far_equilibrium(flow.u_lattice).reshape(19, 1, 1, 1)
                want = want + sigma * (far - want)
            err = np.abs(h1[:, 1:-1, 1:-1, 1:-1] - want).max()
            self.assertLess(err, self.FP32)
            # the far field of the ghost layer is what the equations say, and a step leaves it alone
            ghost = np.ones(h1.shape[1:], bool)
            ghost[1:-1, 1:-1, 1:-1] = False
            far_h = far_equilibrium(flow.u_lattice)
            for q in range(19):
                self.assertLess(np.abs(h1[q][ghost] - far_h[q]).max(), 1e-7)
        finally:
            sim.close()

    def test_no_model(self):
        self.check(0.6, 0.0)

    def test_low_viscosity(self):
        self.check(0.5005, 0.0)

    def test_smagorinsky(self):
        self.check(0.51, 0.17)

    def test_absorbing_layers(self):
        self.check(0.6, 0.0, sponge=(3, 3, 2, 2, 2, 2))


class OneStepWithLinks(OnGpu):
    """Bouzidi links and the momentum exchange against numpy written from the formulas."""

    def check(self, interpolate: bool) -> None:
        g = lattice_grid(16, 16, 16)
        sphere = Sphere((8.13, 7.91, 8.27), 3.37)
        flow = flow_for(0.6)
        sim = open_case(g, flow, [sphere], interpolate=interpolate)
        try:
            h0 = write_interior(sim, rough_field(g, 22))
            sim.step(1)
            _gpu.sync()
            h1 = read3(sim)
            force = sim.read_impulse()[0]
            vox = sim.vox
            pulled = pull(h0)
            stride = C @ np.array(g.strides, dtype=np.int64)
            links = set((vox.node * 19 + vox.dir).tolist())
            flat = h0.reshape(19, -1)
            want_force = np.zeros(3)
            X, Y = g.nx + 2, g.ny + 2
            for n, q, t in zip(vox.node.tolist(), vox.dir.tolist(), vox.q.tolist()):
                o = int(OPP[q])
                ha = float(flat[o, n])
                if not interpolate:
                    refl = ha
                elif t >= 0.5:
                    refl = ha / (2.0 * t) + (2.0 * t - 1.0) / (2.0 * t) * float(flat[q, n])
                else:
                    n2 = n + int(stride[q])
                    if vox.mask[n2] == 0 and n2 * 19 + q not in links:
                        refl = 2.0 * t * ha + (1.0 - 2.0 * t) * float(flat[o, n2])
                    else:
                        refl = ha
                kk, rest = n // (X * Y), n % (X * Y)
                jj, ii = rest // X, rest % X
                pulled[q, kk - 1, jj - 1, ii - 1] = refl
                want_force -= C[q] * (refl + ha)
            want = collide(pulled, float(np.float32(sim.units.tau0)), 0.0)
            fluid = vox.mask.reshape(g.nz + 2, g.ny + 2, g.nx + 2)[1:-1, 1:-1, 1:-1] == 0
            err = np.abs(h1[:, 1:-1, 1:-1, 1:-1] - want)[:, fluid].max()
            self.assertLess(err, OneStep.FP32)
            self.assertLess(np.abs(force[:3] - want_force).max(), 1e-4)
        finally:
            sim.close()

    def test_interpolated(self):
        self.check(True)

    def test_plain(self):
        self.check(False)


class SingleVoxelForce(OnGpu):
    """A uniform field on one solid node: the first step's force has a closed form."""

    def first_step_force(self, u: float, dr: float, interpolate: bool) -> np.ndarray:
        g = lattice_grid(9, 9, 9)
        # centred on node (4, 4, 4); half a side of 0.25 meets only the 18 links coming out of that node
        solid = box((4.5, 4.5, 4.5), (0.5, 0.5, 0.5))
        flow = flow_for(0.8, u=u)
        sim = open_case(g, flow, [solid], interpolate=interpolate)
        try:
            self.assertEqual(int((sim.vox.mask == 1).sum()), 1)
            self.assertEqual(sim.vox.n_links, 18)
            write_interior(sim, uniform_interior(g, (u, 0.0, 0.0), dr))
            sim.step(1)
            _gpu.sync()
            return sim.read_impulse()[0]
        finally:
            sim.close()

    def test_plain_bounce_back_is_2_rho_u(self):
        for u, dr in ((0.05, 0.0), (0.05, 0.02), (0.1, -0.01)):
            f = self.first_step_force(u, dr, False)
            self.assertAlmostEqual(f[0], 2.0 * (1.0 + dr) * u, delta=2e-6)
            self.assertLess(abs(f[1]) + abs(f[2]), 2e-6)

    def test_interpolated_links_at_three_quarters_give_rho_u_over_q(self):
        # all 18 links meet the wall at q = 3/4: reflected = (2/3) h_opp + (1/3) h_i, which gives F = rho u / q
        u = 0.05
        f = self.first_step_force(u, 0.0, True)
        self.assertAlmostEqual(f[0], u / 0.75, delta=2e-6)
        self.assertLess(abs(f[1]) + abs(f[2]), 2e-6)


class MomentumBudget(OnGpu):
    """What the fluid loses, the bodies carry: to rounding, with nothing else reaching the fluid."""

    def test_sphere_before_the_boundary_knows(self):
        # A lattice signal moves one node per step at most. The sphere's disturbance needs 11 steps to reach
        # the box's outer nodes; until then the uniform stream in and out of the box cancels node by node.
        g = lattice_grid(32, 32, 32)
        sphere = Sphere((16.13, 15.87, 16.21), 4.27)
        u = 0.05
        sim = open_case(g, flow_for(0.6, u=u), [sphere])
        try:
            h0 = write_interior(sim, uniform_interior(g, (u, 0.0, 0.0)))
            j0 = fluid_momentum(sim, h0)
            steps = 10
            sim.step(steps)
            _gpu.sync()
            h1 = read3(sim)
            impulse = sim.read_impulse()[0][:3]
            change = fluid_momentum(sim, h1) - j0
            self.assertGreater(impulse[0], 1.0, "the sphere must carry a real load")
            self.assertLess(np.abs(change + impulse).max(), 2e-3 * impulse[0])
            # and the outer nodes really have not heard yet
            inner = h1[:, 1:-1, 1:-1, 1:-1]
            far = far_equilibrium(u)
            self.assertLess(np.abs(inner[:, :, :, 0] - far.reshape(19, 1, 1)).max(), 1e-6)
            self.assertLess(np.abs(inner[:, :, :, -1] - far.reshape(19, 1, 1)).max(), 1e-6)
        finally:
            sim.close()

    def _walled_box(self, inset: float) -> tuple[Grid, list[Any]]:
        """A box of walls around all the nodes (no link reaches the ghost layer) and a sphere inside."""
        n = 24
        g = lattice_grid(n, n, n)
        big = 3.0 * n
        walls = [
            box((inset - 2.0, n / 2.0, n / 2.0), (4.0, big, big), name="x-", group="walls"),
            box((n - inset + 2.0, n / 2.0, n / 2.0), (4.0, big, big), name="x+", group="walls"),
            box((n / 2.0, inset - 2.0, n / 2.0), (big, 4.0, big), name="y-", group="walls"),
            box((n / 2.0, n - inset + 2.0, n / 2.0), (big, 4.0, big), name="y+", group="walls"),
            box((n / 2.0, n / 2.0, inset - 2.0), (big, big, 4.0), name="z-", group="walls"),
            box((n / 2.0, n / 2.0, n - inset + 2.0), (big, big, 4.0), name="z+", group="walls"),
        ]
        return g, walls + [Sphere((12.13, 11.87, 12.21), 3.77, name="sphere", group="sphere")]

    def test_closed_box_for_a_long_time(self):
        for inset, label in ((0.0, "walls halfway"), (0.2, "walls off the halfway point")):
            with self.subTest(label):
                g, shapes = self._walled_box(inset)
                u = 0.05
                sim = open_case(g, flow_for(0.6, u=u), shapes)
                try:
                    self.assertEqual(sim.vox.enclosed_nodes, 0)
                    # no fluid node pulls from the ghost layer without a link
                    h0 = write_interior(sim, uniform_interior(g, (u, 0.0, 0.0)))
                    j0 = fluid_momentum(sim, h0)
                    sim.step(300)
                    _gpu.sync()
                    h1 = read3(sim)
                    impulse = sim.read_impulse()
                    total = impulse[:, :3].sum(axis=0)
                    change = fluid_momentum(sim, h1) - j0
                    self.assertGreater(abs(total[0]), 50.0, "the walls must have taken real momentum")
                    self.assertGreater(abs(impulse[sim.vox.groups.index("sphere"), 0]), 1.0)
                    self.assertLess(np.abs(change + total).max(), 2e-3 * abs(total[0]))
                finally:
                    sim.close()


class Ground(OnGpu):
    """The ground is a specular wall with a log-law stress at the first row: tau = 1/2 cf |du| du, du = ground - air.

    The column looked at is 40 nodes from the sides, and a pressure signal moves 0.577 nodes per step: for up to 60
    steps the column does not know the box has sides, so what enters it can only come through the ground."""

    U = 0.05
    Z0 = 0.1  # rough on purpose: at dx = 1 this gives cf of about 0.13, a stress well above the rounding

    def case(self, ground: tuple[float, float], tau: float = 0.8, size: tuple[int, int, int] = (80, 80, 40)) -> Simulation:
        g = lattice_grid(*size)
        sim = open_case(g, flow_for(tau, u=self.U, ground_velocity_ms=ground, ground_roughness_m=self.Z0), ground=True)
        write_interior(sim, uniform_interior(g, (self.U, 0.0, 0.0)))
        return sim

    def column(self, sim: Simulation) -> np.ndarray:
        """The middle column as moments: (dr, jx, jy, jz) by height."""
        g = sim.grid
        h = read3(sim)[:, 1:-1, 1:-1, 1:-1][:, :, g.ny // 2, g.nx // 2]
        return np.stack([h.sum(axis=0), (C[:, 0][:, None] * h).sum(axis=0), (C[:, 1][:, None] * h).sum(axis=0), (C[:, 2][:, None] * h).sum(axis=0)])

    def stress(self, u: np.ndarray, ground: tuple[float, float]) -> np.ndarray:
        """The stress on one node from its velocity (ux, uy), by the formula in the docstring."""
        du = np.asarray(ground) * self.U - u
        return 0.5 * ground_cf(1.0, self.Z0) * np.hypot(du[0], du[1]) * du

    def test_one_step_adds_the_stress_to_the_first_row_and_no_mass(self):
        for ground in ((0.0, 0.0), (2.0, -1.0), (1.0, 1.0)):
            with self.subTest(ground):
                sim = self.case(ground)
                try:
                    before = self.column(sim)
                    sim.step(1)
                    _gpu.sync()
                    after = self.column(sim)
                    want = self.stress(np.array([self.U, 0.0]), ground)
                    self.assertGreater(float(np.abs(want).max()), 1e-5)
                    self.assertLess(np.abs(after[1:3, 0] - before[1:3, 0] - want).max(), 3e-7)
                    self.assertLess(np.abs(after[1:3, 1:] - before[1:3, 1:]).max(), 3e-7, "only the first row feels it in one step")
                    self.assertLess(np.abs(after[0] - before[0]).max(), 3e-7, "the ground adds no mass")
                finally:
                    sim.close()

    def test_the_column_takes_exactly_the_stress_step_after_step(self):
        ground = (0.0, 0.0)
        sim = self.case(ground, size=(80, 80, 50))
        try:
            total = 0.0
            for _ in range(40):
                m = self.column(sim)
                u_first = m[1:3, 0] / (1.0 + m[0, 0])
                j_before = m[1].sum()
                sim.step(1)
                _gpu.sync()
                want = float(self.stress(u_first, ground)[0])
                got = float(self.column(sim)[1].sum() - j_before)
                total += want
                self.assertLess(abs(got - want), 2e-5)
            self.assertLess(total, -1e-3, "a still ground must have taken real momentum from the air")
        finally:
            sim.close()

    def test_still_ground_grows_a_layer(self):
        sim = self.case((0.0, 0.0))
        try:
            sim.step(60)
            _gpu.sync()
            m = self.column(sim)
            ux = m[1] / (1.0 + m[0])
            self.assertTrue((np.diff(ux[:6]) > 0.0).all(), "slowest at the ground, faster with height")
            # A constant stress diffusing from a wall takes 2 tau sqrt(t / (pi nu)) off the speed at the wall. The real
            # stress weakens as the air slows, and the first row stands half a cell up, so the deficit is below that.
            nu = sim.units.nu_lattice
            ceiling = 2.0 * float(np.hypot(*self.stress(np.array([self.U, 0.0]), (0.0, 0.0)))) * np.sqrt(60.0 / (np.pi * nu))
            deficit = self.U - float(ux[0])
            self.assertLess(deficit, ceiling)
            self.assertGreater(deficit, 0.5 * ceiling)
            self.assertLess(np.abs(ux[-5:] - self.U).max(), 1e-3 * self.U, "the air far above has not heard")
            self.assertLess(np.abs(m[2] / (1.0 + m[0])).max(), 1e-5)
        finally:
            sim.close()

    def test_ground_drags_the_air_its_way(self):
        for ground, axis, sign in (((1.0, 1.0), 2, 1.0), ((1.0, -1.0), 2, -1.0), ((2.0, 0.0), 1, 1.0)):
            with self.subTest(ground):
                sim = self.case(ground)
                try:
                    sim.step(60)
                    _gpu.sync()
                    m = self.column(sim)
                    speed = m[axis] / (1.0 + m[0]) - (self.U if axis == 1 else 0.0)
                    self.assertGreater(float(sign * speed[0]), 0.05 * self.U)
                    self.assertTrue((np.diff(sign * speed[:6]) < 0.0).all(), "most at the ground, less with height")
                finally:
                    sim.close()

    def test_ground_with_the_air_changes_nothing(self):
        # near tau = 1/2, where a no-slip wall at the air's speed grew a reversed layer out of a uniform flow
        for tau in (0.8, 0.505):
            with self.subTest(tau=tau):
                sim = self.case((1.0, 0.0), tau=tau, size=(40, 40, 30))
                try:
                    sim.step(1000)
                    _gpu.sync()
                    h = read3(sim)[:, 1:-1, 1:-1, 1:-1]
                    dr = h.sum(axis=0)
                    ux = (C[:, 0].reshape(19, 1, 1, 1) * h).sum(axis=0) / (1.0 + dr)
                    uz = (C[:, 2].reshape(19, 1, 1, 1) * h).sum(axis=0) / (1.0 + dr)
                    self.assertLess(np.abs(ux - self.U).max(), 1e-4 * self.U)
                    self.assertLess(np.abs(uz).max(), 1e-4 * self.U)
                finally:
                    sim.close()


class RestartBetweenTurns(OnGpu):
    """A long case runs in GPU turns: state to a file, a new Simulation, state back. It must not show."""

    def check(self, ramp_steps: int, first: int) -> None:
        handle, name = tempfile.mkstemp(prefix="hiraku-cfd-test-", suffix=".bin")
        os.close(handle)
        state = Path(name)
        try:
            self.check_with(ramp_steps, first, state)
        finally:
            state.unlink(missing_ok=True)

    def check_with(self, ramp_steps: int, first: int, state: Path) -> None:
        g = lattice_grid(24, 18, 18)
        sphere = Sphere((8.13, 8.91, 9.27), 3.37)
        flow = flow_for(0.55, u=0.05, smagorinsky=0.1, sponge=(3, 3, 3, 3, 3, 3), ramp_steps=ramp_steps)
        total = first + 150
        whole = open_case(g, flow, [sphere])
        try:
            whole.step(total)
            _gpu.sync()
            want_h = whole.read_populations()
            want_impulse = whole.read_impulse()
        finally:
            whole.close()
        part = open_case(g, flow, [sphere])
        try:
            part.step(first)
            _gpu.sync()
            park_state(part, state)
            carried = part.read_impulse()
        finally:
            part.close()
        rest = open_case(g, flow, [sphere])
        try:
            restore_state(rest, state, first)
            rest.step(total - first)
            _gpu.sync()
            got_h = rest.read_populations()
            got_impulse = rest.read_impulse() + carried
        finally:
            rest.close()
        fluid = (rest.vox.mask == 0)
        self.assertLess(float(np.abs(got_h[:, fluid] - want_h[:, fluid]).max()), 1e-6)
        self.assertLess(float(np.abs(got_impulse - want_impulse).max()), 1e-6 * max(1.0, float(np.abs(want_impulse).max())))

    def test_after_the_ramp(self):
        self.check(ramp_steps=40, first=100)

    def test_during_the_ramp(self):
        self.check(ramp_steps=200, first=100)


class UniformFlow(OnGpu):
    """An empty far-field box has nothing to do: uniform stays uniform."""

    def test_uniform_flow_stays_uniform(self):
        g = lattice_grid(32, 24, 24)
        u = 0.05
        for label, cs in (("no model", 0.0), ("smagorinsky", 0.17)):
            with self.subTest(label):
                sim = open_case(g, flow_for(0.52, u=u, smagorinsky=cs, sponge=(4, 8, 4, 4, 4, 4)))
                try:
                    write_interior(sim, uniform_interior(g, (u, 0.0, 0.0)))
                    sim.step(500)
                    _gpu.sync()
                    h = read3(sim)[:, 1:-1, 1:-1, 1:-1]
                    dr = h.sum(axis=0)
                    ux = (C[:, 0].reshape(19, 1, 1, 1) * h).sum(axis=0) / (1.0 + dr)
                    uy = (C[:, 1].reshape(19, 1, 1, 1) * h).sum(axis=0) / (1.0 + dr)
                    uz = (C[:, 2].reshape(19, 1, 1, 1) * h).sum(axis=0) / (1.0 + dr)
                    self.assertLess(np.abs(dr).max(), 1e-5)
                    self.assertLess(np.abs(ux - u).max(), 1e-5)
                    self.assertLess(max(np.abs(uy).max(), np.abs(uz).max()), 1e-5)
                    st = sim.stats()
                    self.assertEqual(st["nonfinite"], 0)
                    self.assertLess(abs(st["mass_dev"]), 1e-5 * g.nx * g.ny * g.nz)
                finally:
                    sim.close()

    def test_a_ramp_from_rest_settles_to_the_far_field(self):
        g = lattice_grid(24, 24, 24)
        u = 0.05
        sim = open_case(g, flow_for(0.6, u=u, sponge=(4, 6, 4, 4, 4, 4), ramp_steps=400))
        try:
            sim.step(4000)
            _gpu.sync()
            h = read3(sim)[:, 1:-1, 1:-1, 1:-1]
            dr = h.sum(axis=0)
            ux = (C[:, 0].reshape(19, 1, 1, 1) * h).sum(axis=0) / (1.0 + dr)
            self.assertLess(np.abs(ux - u).max(), 1e-3 * u)
            self.assertLess(np.abs(dr).max(), 1e-3 * u)
        finally:
            sim.close()


if __name__ == "__main__":
    unittest.main()

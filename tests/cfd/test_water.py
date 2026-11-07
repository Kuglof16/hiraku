"""The flooded-road water: equations and tables without a GPU, then the three required checks on the GPU.

The GPU part takes one GPU turn (one job at a time across processes) and skips with the reason when there is no GPU, or when
the turn does not come within HIRAKU_GPU_WAIT_S seconds. Grids are small: the GPU tests take seconds.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np

from viewer.src import water as viewer_water
from hiraku.cfd.cuda import STAMP, Gpu, gpu_or_reason, gpu_turn, source_hash
from hiraku.cfd.geometry import Cylinder, box
from hiraku.cfd.render_water_blender import pass_order, polytope_faces, surface_nets
from hiraku.cfd.solver import MemoryBudgetError
from hiraku.cfd.vehicle import design_shapes
from hiraku.cfd.water import (GRAVITY, RHO_WATER, SOUND_OVER_SPEED, STATEMENT, Road, VehiclePath, WaterGrid, WaterSim, band_of, check_moving_box, check_standing_vehicle, check_still_water, hydrostatic_e,
                              load_design, particles_inside, plan_case, read_replay, replay_arrays, run_flooded_road, seed_water, shape_table, sound_speed_for, spin_of, spline_weights, table_distance, tait_pressure,
                              thickness_of, time_step, underfloor_columns, underfloor_measure)

DESIGN = Path(__file__).resolve().parents[1] / "verification" / "fixtures" / "n15-v005-design.json"
BULK = RHO_WATER * 30.0 ** 2
# The stated tolerances. Measured when they were set, at a sound speed of 200 m/s and on a 25 mm grid unless noted: still water after
# 1 s moved its mean height 0.0012 mm and its top 0.045 mm, with a top speed of 0.08 mm/s (the long wave runs at 1.4 m/s); the box
# budget closed to 7.3e-6 of the box's impulse; the table agreed with the shapes at all 20000 points, within 4.5e-8 m; none of the
# 152792 particles stayed inside (40 mm).
BOUNDS = {"still_mean_height_mm": 0.5, "still_top_mm": 3.0, "still_max_speed_ms": 0.005, "box_budget": 1e-4, "table_distance_m": 1e-6, "inside_share": 1e-4}


class FrameInterval(unittest.TestCase):
    def test_the_step_fits_a_whole_number_of_times_in_either_interval(self) -> None:
        dt_30, n_30 = time_step(0.03, 200.0, 10.0, frame_dt=1.0 / 30.0)
        dt_90, n_90 = time_step(0.03, 200.0, 10.0, frame_dt=1.0 / 90.0)
        self.assertEqual((n_30, n_90), (584, 195))
        self.assertAlmostEqual(dt_30 * 1e6, 57.08, places=2)
        self.assertAlmostEqual(dt_90 * 1e6, 56.98, places=2)

    def test_the_plan_takes_the_interval_and_keeps_the_default(self) -> None:
        design = load_design(DESIGN)
        scene = {"length_m": 8.0, "water_length_m": 5.0, "width_m": 2.4, "height_m": 0.8}
        usual = plan_case(design, 0.1, 3.0, 0.1, **scene)
        fine = plan_case(design, 0.1, 3.0, 0.1, frame_dt=1.0 / 90.0, **scene)
        self.assertAlmostEqual(usual["per_frame"] * usual["dt"], 1.0 / 30.0, places=12)
        self.assertAlmostEqual(fine["per_frame"] * fine["dt"], 1.0 / 90.0, places=12)
        self.assertEqual(usual["frames"], math.ceil(usual["duration"] * 30.0))
        self.assertEqual(fine["frames"], math.ceil(fine["duration"] * 90.0))
        self.assertAlmostEqual(usual["duration"], fine["duration"])

    def test_a_pass_repeats_nothing_inside_either_part(self) -> None:
        real_time, slow = pass_order(204, 3)
        self.assertEqual(len(real_time), 68)
        self.assertEqual(slow, list(range(204)))
        self.assertEqual(len(set(real_time)), len(real_time))
        self.assertEqual(real_time[:3], [0, 3, 6])


class Equations(unittest.TestCase):
    def test_tait_is_the_power_law_without_tension(self) -> None:
        e = np.array([-0.2, -0.05, -1e-3, -1e-5])
        direct = BULK / 7.0 * ((1.0 + e) ** -7 - 1.0)
        np.testing.assert_allclose(tait_pressure(e, BULK), direct, rtol=1e-9)
        self.assertEqual(float(tait_pressure(0.0, BULK)), 0.0)
        self.assertEqual(float(tait_pressure(0.01, BULK)), 0.0)

    def test_hydrostatic_e_inverts_tait(self) -> None:
        p = np.array([1.0, 100.0, 2000.0, 1e5])
        np.testing.assert_allclose(tait_pressure(hydrostatic_e(p, BULK), BULK), p, rtol=1e-9)

    def test_spline_moments_behind_the_momentum_balance(self) -> None:
        f = np.linspace(0.5, 1.4999, 41)
        w = spline_weights(f)
        d = np.arange(3.0)[None, :] - f[:, None]
        np.testing.assert_allclose(w.sum(axis=1), 1.0, atol=1e-12)
        np.testing.assert_allclose((w * d).sum(axis=1), 0.0, atol=1e-12)  # transfers keep momentum
        np.testing.assert_allclose((w * d * d).sum(axis=1), 0.25, atol=1e-12)  # D = dx^2 / 4, the 4 in the kernels

    def test_time_step_is_a_whole_number_per_frame_under_the_limit(self) -> None:
        dt, n = time_step(0.025, 50.0, 10.0, frame_dt=1.0 / 30.0, cfl=0.4)
        self.assertLessEqual(dt, 0.4 * 0.025 / 60.0)
        self.assertAlmostEqual(dt * n, 1.0 / 30.0, places=12)

    def test_sound_speed_follows_the_larger_speed(self) -> None:
        self.assertAlmostEqual(sound_speed_for(5.0, 0.2), SOUND_OVER_SPEED * 5.0)
        self.assertAlmostEqual(sound_speed_for(0.5, 0.2), SOUND_OVER_SPEED * math.sqrt(GRAVITY * 0.2))


class RoadAndPath(unittest.TestCase):
    def test_dip_holds_the_water_to_the_water_line(self) -> None:
        road = Road(0.2, 4.0, 0.125)
        self.assertAlmostEqual(float(road.height(0.0)), 0.0)
        self.assertAlmostEqual(float(road.height(4.0)), 0.2, places=9)
        self.assertAlmostEqual(float(road.height(-4.0)), 0.2, places=9)
        self.assertAlmostEqual(float(road.gradient(5.0)), 0.125, places=6)
        self.assertTrue(np.all(np.diff(road.height(np.linspace(0.0, 6.0, 2000))) >= -1e-12))

    def test_flat_road_for_the_checks(self) -> None:
        np.testing.assert_array_equal(Road().height(np.array([-3.0, 0.0, 3.0])), 0.0)

    def test_axles_stay_on_the_road_at_constant_speed(self) -> None:
        road = Road(0.2, 4.0, 0.125)
        path = VehiclePath(road, 5.0, 6.0, -1.0, 1.0)
        for t in (0.0, 0.3, 0.6, 1.0):
            x, z, pitch = path.state(t)
            self.assertAlmostEqual(float(x), 6.0 - 5.0 * t)
            for arm in (-1.0, 1.0):
                # the axle's point in world axes
                wx = x + math.cos(pitch) * arm
                wz = z - math.sin(pitch) * arm
                self.assertAlmostEqual(float(wz), float(road.height(x + arm)), delta=0.006)
                self.assertAlmostEqual(float(wx), float(x + arm), delta=0.03)


class Table(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.design = load_design(DESIGN)
        cls.shapes, _ = design_shapes(cls.design, 0.0, 5.0, "two_cargo", rotate_wheels=True, dx=0.0)
        cls.table = shape_table(cls.shapes, 0.025, top=1.2)

    def _random_points(self, n: int = 40000) -> np.ndarray:
        rng = np.random.default_rng(3)
        return rng.uniform(self.table.lo, self.table.hi, size=(n, 3))

    def test_the_table_holds_exactly_what_the_shapes_hold(self) -> None:
        pts = self._random_points()
        d, _ = table_distance(self.table, pts)
        np.testing.assert_array_equal(d < 0.0, particles_inside(self.table.solids, pts))

    def test_nets_let_water_through_and_high_parts_are_named(self) -> None:
        self.assertEqual({s.name for s in self.table.solids} & {s.name for s in self.shapes if s.reflect < 1.0}, set())
        names = {s.name for s in self.table.solids} | set(self.table.left_out)
        self.assertEqual(names, {s.name for s in self.shapes})
        self.assertTrue(all(s.bounds()[0][2] <= 1.2 for s in self.table.solids))

    def test_every_shape_is_in_the_lookup_cells_of_its_points(self) -> None:
        t = self.table
        rng = np.random.default_rng(5)
        for s_id, s in enumerate(t.solids):
            lo, hi = s.bounds()
            p = rng.uniform(lo, hi, size=(30, 3))
            p = p[s.inside(p)]
            c = np.minimum(((p - t.lo) / t.cell).astype(int), np.array(t.counts) - 1)
            for cx, cy, cz in c:
                cell = cx + t.counts[0] * (cy + t.counts[1] * cz)
                self.assertIn(s_id, t.items[t.start[cell]:t.start[cell + 1]], s.name)

    def test_wheel_spin_rolls_on_the_road(self) -> None:
        wheels = [s for s in self.table.solids if isinstance(s, Cylinder) and s.group == "wheels"]
        self.assertEqual(len(wheels), 4)
        for s in wheels:
            w = spin_of(s)
            np.testing.assert_allclose(np.asarray(s.velocity(s.center + [[0.1, 0.02, -0.3]]))[0], np.cross(w, [0.1, 0.02, -0.3]), atol=1e-9)
            # at the contact point the surface speed cancels the vehicle's speed along -x
            contact = np.cross(w, np.array([0.0, 0.0, -s.radius]))
            self.assertAlmostEqual(float(contact[0]) - 5.0, 0.0, places=9)

    def test_thin_shapes_get_a_band_and_thick_ones_none(self) -> None:
        bands = {s.name: float(b) for s, b in zip(self.table.solids, self.table.bands)}
        floor = next(s for s in self.table.solids if s.name == "floor")
        self.assertLess(thickness_of(floor), 0.005)
        self.assertAlmostEqual(bands["floor"], 0.025 - 0.5 * thickness_of(floor), places=6)  # a plate: a row on each face
        small = next(s for s in self.table.solids if s.name == "gusset_LA_front_lower")  # too small to hold water on both faces
        self.assertAlmostEqual(bands[small.name], 0.5 * (0.025 - thickness_of(small)), places=6)
        arm = next(s for s in self.table.solids if s.name == "lca_FL_f")  # a bar: one row of nodes is enough
        self.assertEqual(bands[arm.name], 0.0)
        self.assertAlmostEqual(band_of(arm, 0.04), 0.5 * (0.04 - thickness_of(arm)), places=6)
        self.assertEqual(bands["motor"], 0.0)
        self.assertEqual(bands["wheel_FL"], 0.0)


class Underfloor(unittest.TestCase):
    def test_columns_and_measure(self) -> None:
        plate = box((0.0, 0.0, 0.3), (1.0, 1.0, 0.1), name="plate")
        wheel = Cylinder((0.0, 0.0, 0.2), (0.0, 1.0, 0.0), 0.2, 0.2, "w", "wheels")
        uf = underfloor_columns([plate, wheel], 0.05)
        self.assertAlmostEqual(uf.lowest_m, 0.25, places=9)
        self.assertTrue(np.isnan(uf.floor[10, 10]))  # a tyre stands here
        self.assertAlmostEqual(float(uf.floor[1, 1]), 0.25, places=9)
        # a column with only a high part above it is not underfloor
        high = box((0.8, 0.0, 1.5), (0.2, 0.2, 0.1), name="bar", group="frame")
        wide = underfloor_columns([plate, wheel, high], 0.05)
        self.assertEqual(wide.columns, uf.columns)
        # a layer of water 100 mm deep made of particles of 25 mm cubes, under the whole plate
        h = 0.025
        xs = np.arange(-0.5 + h / 2, 0.5, h)
        ys = np.arange(-0.5 + h / 2, 0.5, h)
        zs = np.arange(h / 2, 0.1, h)
        pts = np.array(np.meshgrid(xs, ys, zs, indexing="ij")).reshape(3, -1).T
        m = underfloor_measure(uf, pts, h ** 3, 0.01)
        self.assertAlmostEqual(m["depth_mean_mm"], 100.0, places=6)
        self.assertAlmostEqual(m["depth_max_mm"], 100.0, places=6)
        self.assertEqual(m["reached_share"], 0.0)
        # raised to the plate's underside, all columns are reached, and the water above the underside does not count as depth
        m2 = underfloor_measure(uf, pts + [0.0, 0.0, 0.16], h ** 3, 0.01)
        self.assertEqual(m2["reached_share"], 1.0)
        self.assertLess(m2["depth_mean_mm"], 100.0)
        self.assertGreater(m2["depth_mean_mm"], 0.0)


class Seeds(unittest.TestCase):
    def test_eight_per_cell_at_the_hydrostatic_state(self) -> None:
        grid = WaterGrid(1.0, 0.5, 0.5, 0.05)
        part, level = seed_water(grid, Road(), 0.2, BULK)
        self.assertEqual(len(part), 8 * 20 * 10 * 4)
        self.assertAlmostEqual(level, 0.2)
        z = part[:, 2] - grid.offset[2]
        self.assertAlmostEqual(float(z.max()), 0.2 - 0.0125, places=6)
        np.testing.assert_allclose(tait_pressure(part[:, 15].astype(float), BULK), RHO_WATER * GRAVITY * (level - z), atol=2.0)

    def test_level_is_whole_layers_and_the_ramp_is_dry(self) -> None:
        grid = WaterGrid(12.0, 1.0, 1.0, 0.05)
        road = Road(0.2, 4.0, 0.125)
        part, level = seed_water(grid, road, 0.19, BULK)
        self.assertAlmostEqual(level, 0.2)
        x = part[:, 0] - grid.offset[0]
        z = part[:, 2] - grid.offset[2]
        self.assertTrue(np.all(z > road.height(x)))
        self.assertLessEqual(float(np.abs(x).max()), 4.0 + 0.1)


class Memory(unittest.TestCase):
    def test_refuses_in_japanese_without_touching_the_gpu(self) -> None:
        grid = WaterGrid(1.0, 0.5, 0.5, 0.05)
        part, _ = seed_water(grid, Road(), 0.2, BULK)
        with mock.patch("hiraku.cfd.solver.gpu_memory_mib", return_value={"total": 4000, "used": 3500, "free": 500}):
            with self.assertRaises(MemoryBudgetError) as ctx:
                WaterSim(None, grid, part, Road(), 1e-4, 30.0)  # type: ignore[arg-type]
        self.assertIn("GPU の空きが足りません", str(ctx.exception))


class SurfaceNets(unittest.TestCase):
    def test_a_sphere_is_closed_round_and_faces_out(self) -> None:
        n = 40
        c = (n - 1) / 2.0
        x, y, z = np.meshgrid(*[np.arange(n, dtype=float)] * 3, indexing="ij")
        field = 12.0 - np.sqrt((x - c) ** 2 + (y - c) ** 2 + (z - c) ** 2)
        v, q = surface_nets(field, 0.0)
        r = np.linalg.norm(v - c, axis=1)
        self.assertLess(float(np.abs(r - 12.0).max()), 0.1)
        # every edge belongs to two quads
        a = np.vstack([q[:, [0, 1]], q[:, [1, 2]], q[:, [2, 3]], q[:, [3, 0]]])
        edges = np.sort(a, axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        self.assertTrue(np.all(counts == 2))
        # the normals point outward: the divergence theorem gives the volume with the right sign
        tri = np.vstack([q[:, [0, 1, 2]], q[:, [0, 2, 3]]])
        p = v[tri] - c
        volume = np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6.0
        self.assertAlmostEqual(float(volume) / (4.0 / 3.0 * math.pi * 12.0 ** 3), 1.0, delta=0.02)

    def test_polytope_faces_are_the_planes_of_the_shape(self) -> None:
        s = box((0.0, 0.0, 0.0), (1.0, 2.0, 3.0))
        v, faces = polytope_faces(s)
        self.assertEqual(len(faces), 6)
        self.assertEqual(len(v), 8)
        np.testing.assert_allclose(sorted(np.abs(v).max(axis=0)), [0.5, 1.0, 1.5])


class Replay(unittest.TestCase):
    def test_surface_is_the_particles_volume_and_flying_ones_are_kept(self) -> None:
        grid = WaterGrid(2.0, 1.0, 1.0, 0.05)
        h = 0.025
        xs = np.arange(-1.0 + h / 2, 1.0, h)
        ys = np.arange(-0.5 + h / 2, 0.5, h)
        zs = np.arange(h / 2, 0.1, h)
        body = np.array(np.meshgrid(xs, ys, zs, indexing="ij")).reshape(3, -1).T
        world = np.vstack([body, [[0.0, 0.0, 0.8]]])
        surface, flying = replay_arrays(world, grid, Road(), h ** 3, 0.1, 0.1)
        self.assertEqual(surface.shape, (20, 10))
        self.assertAlmostEqual(float(np.median(surface)), 100.0, delta=1.0)
        np.testing.assert_allclose(flying, [[0.0, 0.0, 0.8]])


_turn: Any = None
_gpu: Gpu | None = None


class OnGpu(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        global _turn, _gpu
        _turn = gpu_turn(wait_s=float(os.environ.get("HIRAKU_GPU_WAIT_S", "1800")))
        try:
            _turn.__enter__()
        except TimeoutError as e:
            _turn = None
            raise unittest.SkipTest(str(e)) from None
        _gpu, reason = gpu_or_reason("water")
        if _gpu is None:
            _turn.__exit__(None, None, None)
            _turn = None
            raise unittest.SkipTest(reason)

    @classmethod
    def tearDownClass(cls) -> None:
        global _turn, _gpu
        if _gpu is not None:
            _gpu.close()
            _gpu = None
        if _turn is not None:
            _turn.__exit__(None, None, None)
            _turn = None

    def test_ptx_is_built_from_this_source(self) -> None:
        text = (Path(__file__).resolve().parents[2] / "hiraku" / "cfd" / "water.ptx").read_text(encoding="utf-8")
        self.assertTrue(text.startswith(STAMP + source_hash("water")))

    def test_p2g_against_the_equations(self) -> None:
        """One particle set, the kernel's node mass and momentum against numpy written from the equations."""
        grid = WaterGrid(0.5, 0.5, 0.5, 0.05)
        n = 40
        rng = np.random.default_rng(11)
        part = np.zeros((n, 16), np.float32)
        part[:, :3] = rng.uniform(0.2, 0.45, size=(n, 3))
        part[:, 3:6] = rng.uniform(-2.0, 2.0, size=(n, 3))
        part[:, 6:15] = rng.uniform(-5.0, 5.0, size=(n, 9))
        part[:, 15] = rng.uniform(-0.01, 0.005, size=n)
        dt, sound = 1e-4, 30.0
        sim = WaterSim(_gpu, grid, part, Road(), dt, sound)
        try:
            sim.l_clear()
            sim.l_p2g()
            _gpu.sync()
            nodes = np.zeros((grid.cells, 4), np.float32)
            sim.buf["grid"].read_into(nodes)
        finally:
            sim.close()
        dx = grid.dx
        nx, ny, nz = grid.nodes
        want = np.zeros((grid.cells, 4), np.float64)
        vol, m = (dx / 2) ** 3, RHO_WATER * (dx / 2) ** 3
        bulk = RHO_WATER * sound ** 2
        for q in part.astype(np.float64):
            pos = q[:3] / dx
            base = np.floor(pos - 0.5).astype(int)
            f = pos - base
            w = spline_weights(f)
            J = 1.0 + q[15]
            stress = dt * vol * 4.0 / dx ** 2 * J * float(tait_pressure(q[15], bulk))
            A = m * q[6:15].reshape(3, 3) + stress * np.eye(3)
            for a in range(3):
                for b in range(3):
                    for c in range(3):
                        wt = w[0][a] * w[1][b] * w[2][c]
                        d = (np.array([a, b, c]) - f) * dx
                        idx = (base[0] + a) + nx * ((base[1] + b) + ny * (base[2] + c))
                        want[idx, 0] += wt * m
                        want[idx, 1:] += wt * (m * q[3:6] + A @ d)
        np.testing.assert_allclose(nodes[:, 0], want[:, 0], rtol=2e-5, atol=1e-9)
        np.testing.assert_allclose(nodes[:, 1:], want[:, 1:], rtol=2e-4, atol=2e-6)

    def test_a_still_water_stays_put(self) -> None:
        """(a) The level stays and the water comes to rest, within BOUNDS."""
        m = check_still_water(_gpu)
        self.assertEqual(m["not_finite"], 0)
        self.assertLess(abs(m["mean_height_change_mm"]), BOUNDS["still_mean_height_mm"])
        self.assertLess(abs(m["top_change_mm"]), BOUNDS["still_top_mm"])
        self.assertLess(m["max_speed_ms"], BOUNDS["still_max_speed_ms"])

    def test_b_a_moving_box_gives_the_water_its_impulse(self) -> None:
        """(b) No factor anywhere: the budget is the summed node velocity changes against the water's momentum."""
        m = check_moving_box(_gpu)
        self.assertLess(m["box_impulse_x_ns"], -1.0, "the box pushed the water forward")
        self.assertEqual(m["road_impulse_x_ns"], 0.0)  # a flat road only pushes up
        self.assertLess(m["error_x_over_box"], BOUNDS["box_budget"])
        self.assertLess(m["error_y_over_box"], BOUNDS["box_budget"])
        self.assertLess(m["error_z_over_weight"], BOUNDS["box_budget"])

    def test_b_the_budget_still_closes_when_water_leaves_the_box(self) -> None:
        """The box is started near the end of the box, so it pushes water out through the open end."""
        m = check_moving_box(_gpu, open_ends=True, x0=-0.75, seconds=0.6)
        self.assertGreater(m["particles_left_box"], 100)
        self.assertLess(m["error_x_over_box"], BOUNDS["box_budget"])
        self.assertLess(m["error_y_over_box"], BOUNDS["box_budget"])
        self.assertLess(m["error_z_over_weight"], BOUNDS["box_budget"])

    def test_c_the_kernels_see_the_shapes_the_picture_is_drawn_from(self) -> None:
        """(c) With particles moved out of shapes, as in the runs."""
        m = check_standing_vehicle(_gpu, load_design(DESIGN))
        self.assertEqual(m["gpu_differs_from_shape_objects"], 0)
        self.assertEqual(m["gpu_differs_from_numpy"], 0)
        self.assertLess(m["largest_distance_gap_m"], BOUNDS["table_distance_m"])
        self.assertLessEqual(m["particles_inside_after"], BOUNDS["inside_share"] * m["particles"])

    def test_the_record_carries_the_statement(self) -> None:
        design = load_design(DESIGN)
        with tempfile.TemporaryDirectory() as folder:
            case = run_flooded_road(_gpu, design, 0.1, 3.0, 0.1, True, folder, keep_particles=False, length_m=8.0, water_length_m=5.0, width_m=2.4, height_m=0.8)
            saved = json.loads((Path(folder) / "case.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["statement"], STATEMENT)
            self.assertEqual(case["statement"], STATEMENT)
            self.assertEqual(STATEMENT, viewer_water.STATEMENT)  # the page says the same
            self.assertIsNone(saved["faulted"])
            for key in ("inputs", "memory", "particles", "cells", "step_s", "assumptions", "tests_passed"):
                self.assertIn(key, saved)
            self.assertFalse((Path(folder) / "frames").exists())
            with np.load(Path(folder) / "replay.npz") as replay:
                self.assertEqual(replay["surface_mm"].shape[0], saved["frames"])
                self.assertEqual(int(replay["spray_start"][-1]), len(replay["spray"]))
            last = saved["frames"] - 1
            frame = read_replay(folder, last)
            self.assertEqual(frame["statement"], STATEMENT)
            self.assertAlmostEqual(frame["t_s"], saved["records"][last]["t"], places=9)
            np.testing.assert_allclose(frame["pose"], saved["records"][last]["pose"])
            self.assertEqual(frame["spray_m"].shape[1], 3)
            self.assertGreater(float(frame["surface_m"].max()), 0.0)


if __name__ == "__main__":
    unittest.main()

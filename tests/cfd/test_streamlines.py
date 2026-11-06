"""Streamlines on analytic fields: uniform flow and solid-body rotation, whose paths are known. No GPU."""

import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from hiraku.cfd.geometry import Cylinder, box
from hiraku.cfd.streamlines import (STOP_DOMAIN, STOP_LENGTH, STOP_SLOW, STOP_SOLID, MeanField, ShapeStop, named_seeds, plane_seeds, read_json, seed_direction, trace, write_json)

U_LATTICE, AIR = 0.05, 10.0


def field_of(velocity, n=(40, 30, 20), dx=0.1, origin=(-2.0, -1.5, 0.0), dr=None, solid=None):
    """A MeanField whose physical velocity at metres (x, y, z) is velocity(x, y, z). Double precision, so the integration
    error is what the tests see."""
    nx, ny, nz = n
    origin = np.asarray(origin, float)
    z, y, x = np.meshgrid(*(origin[a] + (np.arange(n[a]) + 0.5) * dx for a in (2, 1, 0)), indexing="ij")
    ux, uy, uz = (np.broadcast_to(c, x.shape).astype(float) * U_LATTICE / AIR for c in velocity(x, y, z))
    d = np.zeros(x.shape) if dr is None else np.broadcast_to(dr(x, y, z), x.shape).astype(float)
    if solid is not None:
        m = solid(x, y, z)
        ux, uy, uz, d = (np.where(m, np.nan, a) for a in (ux, uy, uz, d))
    return MeanField(ux, uy, uz, d, dx, origin, U_LATTICE, AIR)


def uniform(x, y, z, u=4.0):
    return u, 0.0 * x, 0.0 * x


class TestSample(unittest.TestCase):
    def test_trilinear_is_exact_on_a_linear_field_and_keeps_the_axes_apart(self):
        f = field_of(lambda x, y, z: (1.0 + 2.0 * x - y + 0.5 * z, 3.0 * y, -z + 0.25 * x), dr=lambda x, y, z: 1e-3 * (x + 2.0 * y - 3.0 * z))
        rng = np.random.default_rng(1)
        p = rng.uniform(f.lo, f.hi, (200, 3))
        u, cp, ok, out = f.sample(p)
        x, y, z = p.T
        self.assertTrue(ok.all())
        self.assertFalse(out.any())
        np.testing.assert_allclose(u[:, 0], 1.0 + 2.0 * x - y + 0.5 * z, atol=1e-10)
        np.testing.assert_allclose(u[:, 1], 3.0 * y, atol=1e-10)
        np.testing.assert_allclose(u[:, 2], -z + 0.25 * x, atol=1e-10)
        np.testing.assert_allclose(cp, 1e-3 * (x + 2.0 * y - 3.0 * z) / 3.0 / (0.5 * U_LATTICE ** 2), atol=1e-9)

    def test_node_i_sits_half_a_cell_from_the_origin_and_the_array_is_z_y_x(self):
        nx, ny, nz = 6, 5, 4
        ux = np.arange(nx * ny * nz, dtype=float).reshape(nz, ny, nx)
        f = MeanField(ux, 0 * ux, 0 * ux, 0 * ux, 0.1, (1.0, 2.0, 3.0), U_LATTICE, AIR)
        np.testing.assert_allclose(f.node(0, 0, 0), (1.05, 2.05, 3.05))
        for i, j, k in ((0, 0, 0), (5, 0, 0), (0, 4, 0), (0, 0, 3), (2, 3, 1), (5, 4, 3)):
            u = f.sample(f.node(i, j, k)[None])[0][0, 0]
            self.assertAlmostEqual(u, ux[k, j, i] * AIR / U_LATTICE, places=6)

    def test_outside_the_nodes(self):
        f = field_of(uniform)
        p = np.array([[f.lo[0] - 1e-6, 0.0, 0.5], [0.0, f.hi[1] + 1e-6, 0.5], [0.0, 0.0, f.lo[2] - 1e-6]])
        u, cp, ok, out = f.sample(p)
        self.assertTrue(out.all())
        self.assertFalse(ok.any())
        self.assertTrue((u == 0).all())

    def test_a_solid_corner_gives_no_nan_and_half_the_weight_decides(self):
        f = field_of(uniform, solid=lambda x, y, z: x > 0.0)
        # the last fluid plane is x = -0.05 and the first solid one x = 0.05
        u, _, ok, _ = f.sample(np.array([[-0.01, 0.0, 0.5], [0.01, 0.0, 0.5]]))
        self.assertFalse(np.isnan(u).any())
        self.assertEqual(ok.tolist(), [True, False])
        np.testing.assert_allclose(u[0], (4.0, 0.0, 0.0), atol=1e-12)


class TestUniform(unittest.TestCase):
    def test_straight_line_with_the_known_time_and_speed(self):
        f = field_of(uniform, dr=lambda x, y, z: 0.0 * x + 2e-4)
        seed = np.array([[-1.5, 0.2, 0.7]])
        L = trace(f, seed, step_m=0.05, max_length_m=1.0)
        self.assertEqual(len(L), 1)
        np.testing.assert_allclose(L.points[:, 1:], np.tile(seed[0, 1:], (len(L.points), 1)), atol=1e-12)
        np.testing.assert_allclose(np.diff(L.points[:, 0]), 0.05, atol=1e-12)
        np.testing.assert_allclose(L.time, (L.points[:, 0] - seed[0, 0]) / 4.0, atol=1e-12)
        np.testing.assert_allclose(L.speed, 4.0, atol=1e-12)
        np.testing.assert_allclose(L.cp, 2e-4 / 3.0 / (0.5 * U_LATTICE ** 2), atol=1e-9)
        self.assertEqual(L.stop[0], STOP_LENGTH)
        self.assertAlmostEqual(L.points[-1, 0] - seed[0, 0], 1.0, places=9)

    def test_stops_at_the_end_of_the_nodes(self):
        f = field_of(uniform)
        L = trace(f, np.array([[1.0, 0.0, 0.5]]), step_m=0.03, max_length_m=50.0)
        self.assertEqual(L.stop[0], STOP_DOMAIN)
        self.assertLessEqual(L.points[-1, 0], f.hi[0])
        self.assertGreater(L.points[-1, 0], f.hi[0] - 0.03)

    def test_a_region_ends_lines_too(self):
        f = field_of(uniform)
        L = trace(f, np.array([[0.0, 0.0, 0.5]]), step_m=0.02, region=(f.lo, (0.5, f.hi[1], f.hi[2])))
        self.assertEqual(L.stop[0], STOP_DOMAIN)
        self.assertLessEqual(L.points[-1, 0], 0.5)
        self.assertGreater(L.points[-1, 0], 0.48)

    def test_seeds_run_together_and_each_keeps_its_own_line(self):
        f = field_of(uniform)
        seeds = plane_seeds((-1.5, -1.0, 0.2), (0.0, 2.0, 0.0), (0.0, 0.0, 1.0), 5, 4)
        L = trace(f, seeds, step_m=0.05, max_length_m=0.5)
        self.assertEqual(len(L), 20)
        self.assertEqual(L.seed.tolist(), list(range(20)))
        for n in range(20):
            line = L.line(n)
            self.assertEqual(len(line["points"]), 11)
            np.testing.assert_allclose(line["points"][:, 1:], np.tile(seeds[n, 1:], (11, 1)), atol=1e-12)

    def test_backward_and_both(self):
        f = field_of(uniform)
        seed = np.array([[0.0, 0.0, 0.5]])
        back = trace(f, seed, step_m=0.05, max_length_m=0.5, direction="backward")
        np.testing.assert_allclose(back.points[0, 0], -0.5, atol=1e-12)
        np.testing.assert_allclose(back.points[-1, 0], 0.0, atol=1e-12)
        both = trace(f, seed, step_m=0.05, max_length_m=0.5, direction="both")
        self.assertEqual(len(both.points), 21)
        self.assertTrue((np.diff(both.points[:, 0]) > 0).all())
        self.assertEqual(np.count_nonzero(np.isclose(both.points[:, 0], 0.0)), 1)
        self.assertEqual(both.time[0], 0.0)
        self.assertTrue((np.diff(both.time) > 0).all())
        np.testing.assert_allclose(both.time[-1], 1.0 / 4.0, atol=1e-12)
        self.assertEqual(trace(f, seed, step_m=0.05, max_length_m=0.5, direction="both").stop[0], STOP_LENGTH)
        with self.assertRaises(ValueError):
            trace(f, seed, direction="sideways")


class TestRotation(unittest.TestCase):
    """u = w (-y, x, 0) about the z axis at the middle. Trilinear is exact on it, and the RK4 step is known in closed form:
    in the complex plane x_n = x_0 R(z)^n, R(z) = 1 + z + z^2/2 + z^3/6 + z^4/24, z = i w dt, with dt = h / (w r)."""

    W = 2.0

    def setUp(self):
        self.f = field_of(lambda x, y, z: (-self.W * y, self.W * x, 0.0 * x), n=(30, 30, 6), origin=(-1.5, -1.5, 0.0))

    def run_circle(self, r, h, turns=1.0):
        return trace(self.f, np.array([[r, 0.0, 0.3]]), step_m=h, max_length_m=turns * 2.0 * math.pi * r, min_speed_ms=0.0)

    def test_the_points_are_the_closed_form_rk4_map(self):
        r, h = 1.0, 0.05
        L = self.run_circle(r, h)
        z = 1j * h / r  # w dt = h / r
        R = 1 + z + z ** 2 / 2 + z ** 3 / 6 + z ** 4 / 24
        # dt comes from the speed at the point, and |x_n| slowly drifts; the map is exact only for fixed dt, so compare the
        # first steps and bound the rest below
        n = np.arange(10)
        exact = r * R ** n
        c = L.points[:10, 0] + 1j * L.points[:10, 1]
        self.assertLess(np.abs(c - exact).max(), 1e-8)

    def test_error_after_one_turn_stays_under_the_rk4_bound(self):
        r, h = 1.0, 0.05
        L = self.run_circle(r, h)
        steps = len(L.points) - 1
        theta = h / r
        # one step: |R(i a) - e^(i a)| <= a^5 / 120 + (the series' next term); global error at most steps times that times r
        one = theta ** 5 / 120.0 * math.exp(theta)
        bound = steps * one * r
        p = L.points[-1]
        angle = math.atan2(p[1], p[0])
        exact = r * np.array([math.cos(steps * theta), math.sin(steps * theta)])
        err = float(np.hypot(*(p[:2] - exact)))
        self.assertLess(err, bound)
        self.assertLess(abs(np.hypot(p[0], p[1]) - r), bound)
        self.assertGreater(steps * theta, 2.0 * math.pi - 1e-9)
        self.assertLess(abs(angle), 2.0 * theta)
        np.testing.assert_allclose(L.points[:, 2], 0.3, atol=1e-12)
        np.testing.assert_allclose(L.speed, self.W * r, atol=1e-5)

    def test_fourth_order(self):
        def err(h):
            L = self.run_circle(1.0, h)
            steps = len(L.points) - 1
            p = L.points[-1]
            return float(np.hypot(p[0] - math.cos(steps * h), p[1] - math.sin(steps * h)))

        e1, e2 = err(0.1), err(0.05)
        self.assertGreater(e1 / e2, 2.0 ** 3.5)
        self.assertLess(e1 / e2, 2.0 ** 4.5)

    def test_the_travel_time_of_one_turn(self):
        L = self.run_circle(1.0, 0.02)
        self.assertAlmostEqual(L.time[-1], 2.0 * math.pi / self.W, delta=2.0 * 0.02 / self.W)


class TestStops(unittest.TestCase):
    def test_solid_nodes_stop_a_line_at_the_wall_and_no_nan_comes_out(self):
        f = field_of(uniform, solid=lambda x, y, z: (x > 0.5) & (x < 0.8), dr=lambda x, y, z: 1e-4 + 0.0 * x)
        L = trace(f, np.array([[-1.0, 0.0, 0.5]]), step_m=0.02)
        self.assertEqual(L.stop[0], STOP_SOLID)
        self.assertLess(L.points[-1, 0], 0.5)  # half way between the last fluid plane (0.45) and the first solid one (0.55)
        self.assertGreater(L.points[-1, 0], 0.45)
        for a in (L.points, L.speed, L.cp, L.time):
            self.assertFalse(np.isnan(a).any())

    def test_a_seed_inside_a_solid_gives_no_line(self):
        f = field_of(uniform, solid=lambda x, y, z: (x > 0.5) & (x < 0.8))
        L = trace(f, np.array([[0.65, 0.0, 0.5], [0.0, 0.0, 0.5]]), step_m=0.02)
        self.assertEqual(L.seed.tolist(), [1])
        self.assertEqual(len(trace(f, np.array([[0.65, 0.0, 0.5]]), step_m=0.02)), 0)

    def test_still_air_ends_a_line(self):
        f = field_of(lambda x, y, z: (np.maximum(0.0, 1.0 - x), 0.0 * x, 0.0 * x))
        L = trace(f, np.array([[0.0, 0.0, 0.5]]), step_m=0.01, min_speed_ms=0.2)
        self.assertEqual(L.stop[0], STOP_SLOW)
        self.assertGreater(L.speed[-1], 0.0)
        self.assertLess(L.speed[-1], 0.2)
        self.assertAlmostEqual(L.points[-1, 0], 0.8, delta=0.011)

    def test_a_thin_plate_with_no_solid_node_stops_a_line_but_a_screen_does_not(self):
        f = field_of(uniform)
        plate = box((0.5, 0.0, 0.5), (0.003, 1.0, 1.0))
        L = trace(f, np.array([[0.0, 0.0, 0.5]]), step_m=0.05, stop=ShapeStop([plate]))
        self.assertEqual(L.stop[0], STOP_SOLID)
        self.assertLess(L.points[-1, 0], 0.4985)
        self.assertGreater(L.points[-1, 0], 0.4985 - 0.05 - 1e-9)
        screen = box((0.5, 0.0, 0.5), (0.003, 1.0, 1.0))
        screen.reflect = 0.3
        solid_elsewhere = box((1.0, 1.0, 0.5), (0.1, 0.1, 0.1))
        L = trace(f, np.array([[0.0, 0.0, 0.5]]), step_m=0.05, max_length_m=1.0, stop=ShapeStop([screen, solid_elsewhere]))
        self.assertEqual(L.stop[0], STOP_LENGTH)

    def test_a_cylinder_stops_a_line(self):
        f = field_of(uniform)
        c = Cylinder((0.5, 0.0, 0.5), (0.0, 1.0, 0.0), 0.1, 0.4)
        stop = ShapeStop([c])
        L = trace(f, np.array([[0.0, 0.0, 0.5], [0.0, 0.5, 0.5]]), step_m=0.02, max_length_m=1.0, stop=stop)
        self.assertEqual(L.stop.tolist(), [STOP_SOLID, STOP_LENGTH])
        self.assertLess(L.line(0)["points"][-1, 0], 0.4)


class TestSeeds(unittest.TestCase):
    def test_the_planes(self):
        lo, hi = np.array([-1.0, -0.5, 0.0]), np.array([1.0, 0.5, 1.5])
        up = named_seeds("upstream", lo, hi, (4, 3), margin=0.2, upstream=2.0, floor=0.05)
        self.assertEqual(up.shape, (12, 3))
        np.testing.assert_allclose(up[:, 0], -3.0)
        self.assertTrue((up[:, 1] > -0.7).all() and (up[:, 1] < 0.7).all())
        self.assertTrue((up[:, 2] > 0.05).all() and (up[:, 2] < 1.7).all())
        np.testing.assert_allclose(named_seeds("beside", lo, hi, (4, 3), margin=0.2)[:, 1], 0.7)
        np.testing.assert_allclose(named_seeds("above", lo, hi, (4, 3), margin=0.2)[:, 2], 1.7)
        np.testing.assert_allclose(named_seeds("front", lo, hi, (4, 3), margin=0.2)[:, 0], -1.1)
        np.testing.assert_allclose(named_seeds("wake", lo, hi, (4, 3), margin=0.2)[:, 0], 1.1)
        with self.assertRaises(ValueError):
            named_seeds("under", lo, hi)

    def test_directions(self):
        self.assertEqual(seed_direction("upstream"), "forward")
        for k in ("front", "beside", "above", "wake"):
            self.assertEqual(seed_direction(k), "both")


class TestFilesAndThinning(unittest.TestCase):
    def setUp(self):
        self.f = field_of(lambda x, y, z: (4.0 + 0.0 * x, 0.5 * np.sin(x), 0.1 + 0.0 * x), dr=lambda x, y, z: 1e-4 * x)
        seeds = plane_seeds((-1.5, -0.5, 0.3), (0.0, 1.0, 0.0), (0.0, 0.0, 0.5), 3, 2)
        self.L = trace(self.f, seeds, step_m=0.03, max_length_m=1.0)

    def test_the_file_round_trips_within_its_steps(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_json(Path(d) / "sub" / "lines.json", self.L, {"yaw_deg": 0.0})
            text = path.read_text(encoding="ascii")
            obj = json.loads(text)
            self.assertNotIn("NaN", text)
            self.assertNotIn(" ", text)
            self.assertEqual(obj["meta"], {"yaw_deg": 0.0})
            self.assertTrue(all(isinstance(v, int) for v in obj["xyz"][:30]))
            R = read_json(path)
        self.assertEqual(R.offsets.tolist(), self.L.offsets.tolist())
        self.assertEqual(R.stop.tolist(), self.L.stop.tolist())
        np.testing.assert_allclose(R.points, self.L.points, atol=0.5e-3 + 1e-12)
        np.testing.assert_allclose(R.speed, self.L.speed, atol=0.5e-2 + 1e-12)
        np.testing.assert_allclose(R.cp, self.L.cp, atol=0.5e-3 + 1e-12)
        np.testing.assert_allclose(R.time, self.L.time, atol=0.5e-3 + 1e-12)

    def test_not_a_streamlines_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text('{"format":"other"}', encoding="ascii")
            with self.assertRaises(ValueError):
                read_json(p)

    def test_thinning_keeps_the_ends(self):
        T = self.L.thinned(4)
        self.assertEqual(len(T), len(self.L))
        for n in range(len(T)):
            a, b = self.L.line(n), T.line(n)
            np.testing.assert_array_equal(a["points"][0], b["points"][0])
            np.testing.assert_array_equal(a["points"][-1], b["points"][-1])
            self.assertLess(len(b["points"]), len(a["points"]))
            self.assertTrue((np.diff(b["time"]) > 0).all())

    def test_select(self):
        keep = np.array([True, False, True, False, False, True])
        S = self.L.select(keep)
        self.assertEqual(len(S), 3)
        self.assertEqual(S.seed.tolist(), self.L.seed[keep].tolist())
        np.testing.assert_array_equal(S.line(1)["points"], self.L.line(2)["points"])


class TestSavedCase(unittest.TestCase):
    def test_a_case_folder_as_a_run_saves_it(self):
        ux = np.full((6, 5, 4), 0.05, np.float32)
        ux[3, 2, 2] = np.nan
        z = np.zeros_like(ux)
        with tempfile.TemporaryDirectory() as d:
            np.savez_compressed(Path(d) / "fields.npz", dx=0.2, origin=np.array([-1.0, -0.5, 0.0]), u_lattice=0.05, air_speed_ms=11.0, rho=1.225, dt=0.001, dr=z, ux=ux, uy=z, uz=z)
            f = MeanField.from_case(Path(d))
        self.assertEqual(f.shape, (6, 5, 4))
        self.assertEqual(int((~f.fluid).sum()), 1)
        np.testing.assert_allclose(f.solid_box()[0], (-0.6, -0.1, 0.6))
        np.testing.assert_allclose(f.solid_box()[1], (-0.4, 0.1, 0.8))
        u = f.sample(np.array([[-0.9, -0.4, 0.1]]))[0]
        np.testing.assert_allclose(u[0], (11.0, 0.0, 0.0), atol=1e-4)


if __name__ == "__main__":
    unittest.main()

"""The parts of the pictures that need neither Blender nor a GPU: the meshes, the colours, the camera, and that the lights
stay on the stored streamlines and are hidden behind the vehicle."""

import json
import math
import tempfile
import unittest
from pathlib import Path

import numpy as np

from hiraku.cfd import render_blender as rb
from hiraku.cfd import render_video as rv
from hiraku.cfd.geometry import Cylinder, box
from hiraku.cfd.streamlines import Lines, ShapeStop, _meets_cylinder


def straight_lines(speed=2.0, n=101, count=3):
    """Lines along +x at the given speed, each 10 m long, in y = 0, 1, 2 ..."""
    x = np.linspace(0.0, 10.0, n)
    pts = np.vstack([np.column_stack([x, np.full(n, float(k)), np.full(n, 1.0)]) for k in range(count)])
    return Lines(pts, np.full(n * count, speed), np.zeros(n * count), np.tile(x / speed, count), np.arange(count + 1) * n, np.arange(count), np.zeros(count, np.int8))


class TestColours(unittest.TestCase):
    def test_the_ramp_is_the_viewers_stops_with_no_red(self):
        np.testing.assert_allclose(rb.ramp(0.0), np.array([0x2b, 0x59, 0xc3]) / 255.0)
        np.testing.assert_allclose(rb.ramp(1.0), np.array([0xe9, 0x89, 0x3a]) / 255.0)
        np.testing.assert_allclose(rb.ramp(0.5), np.array([0x4c, 0xbf, 0x7a]) / 255.0)
        np.testing.assert_allclose(rb.ramp(-3.0), rb.ramp(0.0))
        np.testing.assert_allclose(rb.ramp(3.0), rb.ramp(1.0))
        c = rb.ramp(np.linspace(0.0, 1.0, 200))
        self.assertEqual(c.shape, (200, 3))
        self.assertTrue((c[:, 1] > 0.3).all())  # green never falls away, so the map never turns red

    def test_linear_and_back(self):
        c = np.linspace(0.0, 1.0, 11)
        lin = rb.to_linear(c)
        back = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)
        np.testing.assert_allclose(back, c, atol=1e-6)

    def test_the_scale_is_round_and_reaches_the_value(self):
        self.assertEqual(rb.nice_scale(15.9), (16.0, 4.0))
        for v in (0.7, 3.3, 11.1, 15.0, 15.9, 18.0, 47.0):
            top, step = rb.nice_scale(v)
            self.assertGreaterEqual(top, v * 0.999)
            self.assertAlmostEqual(top / step, round(top / step))
            self.assertLessEqual(round(top / step), 5)


class TestMeshes(unittest.TestCase):
    def test_a_box_has_six_faces_turning_outwards(self):
        s = box((1.0, 2.0, 3.0), (2.0, 4.0, 6.0))
        v, faces = rb.polytope_mesh(s)
        self.assertEqual(len(faces), 6)
        centre = v.mean(axis=0)
        for f in faces:
            self.assertEqual(len(f), 4)
            a, b, c = v[f[0]], v[f[1]], v[f[2]]
            n = np.cross(b - a, c - b)
            self.assertGreater(n @ (v[f].mean(axis=0) - centre), 0.0)

    def test_a_cylinder_mesh_stays_on_its_surface(self):
        c = Cylinder((0.0, 0.0, 0.3), (0.0, 1.0, 0.0), 0.3, 0.2)
        v, faces = rb.cylinder_mesh(c, 16)
        self.assertEqual(len(v), 32)
        self.assertEqual(len(faces), 18)
        r = v - c.center
        np.testing.assert_allclose(np.hypot(r[:, 0], r[:, 2]), 0.3)
        np.testing.assert_allclose(np.abs(r[:, 1]), 0.1)

    def test_a_tube_follows_its_line(self):
        p = np.column_stack([np.linspace(0, 1, 11), np.zeros(11), np.ones(11)])
        v, f = rb.tube_mesh(p, 0.01, 6)
        self.assertEqual(v.shape, (66, 3))
        self.assertEqual(f.shape, (60, 4))
        rings = v.reshape(11, 6, 3)
        np.testing.assert_allclose(rings.mean(axis=1), p, atol=1e-12)
        np.testing.assert_allclose(np.linalg.norm(rings - p[:, None, :], axis=2), 0.01)

    def test_a_vertical_stretch_does_not_break_a_tube(self):
        p = np.column_stack([np.zeros(5), np.zeros(5), np.linspace(0, 1, 5)])
        v, _ = rb.tube_mesh(p, 0.01, 4)
        self.assertFalse(np.isnan(v).any())
        np.testing.assert_allclose(np.linalg.norm(v.reshape(5, 4, 3) - p[:, None, :], axis=2), 0.01)

    def test_a_net_is_wires_in_the_plane_of_its_box(self):
        s = box((0.0, 1.0, 1.0), (1.0, 0.003, 0.5))
        v, f = rb.net_mesh(s, 0.003, 0.05)
        self.assertEqual(f.shape[1], 4)
        self.assertEqual(len(v) % 8, 0)
        self.assertEqual(len(v) // 8, int(1.0 / 0.05) + 1 + int(0.5 / 0.05) + 1)
        self.assertLess(np.abs(v[:, 1] - 1.0).max(), 0.0021 + 1e-9)

    def test_the_builder_joins_meshes_with_their_own_numbers(self):
        m = rb.MeshBuilder()
        m.add(*rb.polytope_mesh(box((0, 0, 0), (1, 1, 1))))
        m.add(*rb.polytope_mesh(box((5, 0, 0), (1, 1, 1))))
        a = m.arrays("x")
        self.assertEqual(len(a["x_v"]), 16)
        self.assertEqual(len(a["x_s"]), 12)
        self.assertEqual(len(a["x_l"]), 48)
        self.assertEqual(a["x_l"].max(), 15)
        self.assertEqual(a["x_s"][6], 24)

    def test_lines_in_front_of_the_near_flank_are_picked_out_whole(self):
        lo, hi = np.array([-1.0, -0.5, 0.0]), np.array([1.0, 0.5, 1.5])
        rows = [((-3, -1, 0.5), (3, -1, 0.5)), ((-3, 1, 0.5), (3, 1, 0.5)), ((-3, -1, 2.0), (3, -1, 2.0)), ((-3, -1, 0.5), (-2.5, -1, 0.5))]
        pts = np.array(rows, float).reshape(-1, 3)
        L = Lines(pts, np.ones(8), np.zeros(8), np.zeros(8), np.arange(5) * 2, np.arange(4), np.zeros(4, np.int8))
        # by the stored points only: the first line's points are at x = -3 and 3, outside the vehicle's stretch
        self.assertEqual(rb.near_side_of(L, lo, hi).tolist(), [False, False, False, False])
        pts2 = np.array([(-1, -1, 0.5), (1, -1, 0.5), (-1, 1, 0.5), (1, 1, 0.5), (-1, -1, 2.0), (1, -1, 2.0), (-1, -0.5, 0.5), (1, -0.5, 0.5)], float)
        L2 = Lines(pts2, np.ones(8), np.zeros(8), np.zeros(8), np.arange(5) * 2, np.arange(4), np.zeros(4, np.int8))
        self.assertEqual(rb.near_side_of(L2, lo, hi).tolist(), [True, False, False, False])


class TestCylinderSegments(unittest.TestCase):
    def setUp(self):
        self.c = Cylinder((0.0, 0.0, 0.0), (0.0, 1.0, 0.0), 1.0, 2.0)

    def meets(self, a, b):
        return bool(_meets_cylinder(self.c, np.array([a], float), np.array([b], float))[0])

    def test_across_through_and_past(self):
        self.assertTrue(self.meets((-2, 0, 0), (2, 0, 0)))
        self.assertTrue(self.meets((0, 0, 0), (0.1, 0, 0)))  # all inside
        self.assertFalse(self.meets((-2, 0, 1.5), (2, 0, 1.5)))
        self.assertFalse(self.meets((-2, 0, 0), (-1.5, 0, 0)))  # stops short
        self.assertFalse(self.meets((1.5, 0, 0), (3, 0, 0)))  # starts past it

    def test_past_the_caps_and_along_the_axis(self):
        self.assertFalse(self.meets((-2, 1.5, 0), (2, 1.5, 0)))
        self.assertTrue(self.meets((0, -3, 0), (0, 3, 0)))
        self.assertFalse(self.meets((2, -3, 0), (2, 3, 0)))
        self.assertTrue(self.meets((0.5, 0.5, 0), (0.5, 3, 0)))
        self.assertTrue(self.meets((0, -3, 0), (0.1, 3, 0.1)))

    def test_a_tilted_segment_clips_a_cap_rim(self):
        self.assertTrue(self.meets((-2, 2, 0), (0.5, 0.5, 0)))
        self.assertFalse(self.meets((-2, 2, 0), (-0.5, 1.5, 0)))


class TestCamera(unittest.TestCase):
    def camera(self):
        # at z = 5, looking down along -z, with the picture's top towards +y (Blender's camera with no rotation)
        world = np.eye(4)
        world[2, 3] = 5.0
        return rv.Camera(world, 1000.0, (1920, 1080))

    def test_project_on_known_points(self):
        x, y, d = self.camera().project(np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 10.0]]))
        np.testing.assert_allclose(x[:3], (960.0, 960.0 + 200.0, 960.0))
        np.testing.assert_allclose(y[:3], (540.0, 540.0, 540.0 - 200.0))
        np.testing.assert_allclose(d, (5.0, 5.0, 5.0, -5.0))

    def test_read_from_blenders_file(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "c.json"
            p.write_text(json.dumps({"matrix_world": np.eye(4).tolist(), "lens": 36.0, "sensor_width": 36.0, "resolution": [1920, 1080]}), encoding="utf-8")
            c = rv.Camera.from_json(p)
        self.assertEqual(c.focal_px, 1920.0)
        self.assertEqual(c.size, (1920, 1080))
        np.testing.assert_array_equal(c.eye, (0.0, 0.0, 0.0))

    def test_what_stands_between_hides_a_point(self):
        eye = np.array([0.0, -5.0, 1.0])
        wall = box((0.0, 0.0, 1.0), (1.0, 0.1, 1.0))
        wheel = Cylinder((3.0, 0.0, 1.0), (0.0, 1.0, 0.0), 0.5, 0.2)
        screen = box((-3.0, 0.0, 1.0), (1.0, 0.1, 1.0))
        screen.reflect = 0.3
        pts = np.array([[0.0, 3.0, 1.0], [0.0, -3.0, 1.0], [3.0 * 8.0 / 5.0, 3.0, 1.0], [-3.0 * 8.0 / 5.0, 3.0, 1.0], [10.0, 3.0, 1.0]])
        seen = rv.seen_from(eye, pts, [wall, wheel, screen])
        self.assertEqual(seen.tolist(), [False, True, False, True, True])


class TestLights(unittest.TestCase):
    def test_lights_stay_on_the_line_and_move_at_its_speed_times_the_clock(self):
        L = straight_lines(speed=2.0)
        p0, spd, frac, near, gain = rv.light_samples(L, 0.0, every=1.0, tail=0.1, n_tail=4, air=2.0)
        np.testing.assert_allclose(gain, 1.0)
        np.testing.assert_allclose(p0[:, 2], 1.0)
        self.assertTrue(np.isin(np.round(p0[:, 1]), (0.0, 1.0, 2.0)).all())
        np.testing.assert_allclose(spd, 2.0)
        self.assertTrue((p0[:, 0] >= 0.0).all() and (p0[:, 0] <= 10.0 + 1e-9).all())
        self.assertTrue(((frac >= 0.0) & (frac < 1.0)).all())
        self.assertEqual(int(near.max()) < len(L.points), True)
        heads0 = p0[frac == 0.0]
        heads1 = rv.light_samples(L, 0.25, every=1.0, tail=0.1, n_tail=4, air=2.0)
        heads1 = heads1[0][heads1[2] == 0.0]
        d = heads1[:, 0] - heads0[:, 0]
        moved = d[d > 0.0]  # some lights wrapped to the start of their line
        self.assertGreater(len(moved), 0)
        np.testing.assert_allclose(moved, 2.0 * 0.25, atol=1e-9)

    def test_a_light_follows_a_bent_line_not_a_straight_cut(self):
        th = np.linspace(0.0, math.pi / 2.0, 200)
        pts = np.column_stack([np.cos(th), np.sin(th), np.zeros(200)])
        t = th.copy()  # unit speed on the unit circle
        L = Lines(pts, np.ones(200), np.zeros(200), t, np.array([0, 200]), np.array([0]), np.zeros(1, np.int8))
        p, *_ = rv.light_samples(L, 0.2, every=0.3, tail=0.2, n_tail=8, air=1.0)
        np.testing.assert_allclose(np.hypot(p[:, 0], p[:, 1]), 1.0, atol=1e-4)

    def test_tails_stop_at_the_start_of_the_line(self):
        L = straight_lines(count=1)
        p, *_ = rv.light_samples(L, 0.0, every=100.0, tail=2.0, n_tail=10, air=2.0)
        self.assertTrue((p[:, 0] >= 0.0).all())
        self.assertLess(len(p), 11)

    def test_slow_air_has_a_shorter_dimmer_tail_and_no_pile_up(self):
        L = straight_lines(speed=0.5, count=1)  # a quarter of the air speed: the floor of 30 % applies
        p, _, frac, _, gain = rv.light_samples(L, 12.0, every=100.0, tail=2.0, n_tail=10, air=2.0)
        self.assertEqual(len(p), 10)
        np.testing.assert_allclose(gain, 0.3)
        np.testing.assert_allclose(p[0, 0] - p[-1, 0], 2.0 * 0.3 * 0.9, atol=1e-9)
        np.testing.assert_allclose(np.diff(p[:, 0]), -2.0 * 0.3 / 10.0, atol=1e-9)  # evenly spread along the line

    def test_no_lines_no_lights(self):
        empty = Lines(np.zeros((0, 3)), np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(1, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int8))
        self.assertEqual(len(rv.light_samples(empty, 0.0, 1.0, 0.1, 4, 2.0)[0]), 0)

    def test_spots_add_up_where_they_fall(self):
        layer = rv.splat((64, 32), np.array([20.0, 20.0, 500.0]), np.array([10.0, 10.0, 5.0]), np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), 1.0)
        self.assertEqual(layer.shape, (32, 64, 3))
        self.assertAlmostEqual(float(layer[10, 20, 0]), 2.0, places=5)
        self.assertEqual(float(layer[:, :, 1].sum()), 0.0)
        self.assertLess(float(layer[10, 24, 0]), 0.01)


class TestFrame(unittest.TestCase):
    def test_a_frame_adds_lights_to_the_still_and_keeps_the_caption_on_top(self):
        world = np.eye(4)
        world[:3, 3] = (5.0, 0.0, 30.0)
        cam = rv.Camera(world, 1000.0, (320, 180))
        L = straight_lines(speed=2.0)
        seen = np.ones(len(L.points), bool)
        base = np.full((180, 320, 3), 0.02, np.float32)
        overlay = np.zeros((180, 320, 4), np.float32)
        overlay[:20, :, :3] = 0.5
        overlay[:20, :, 3] = 1.0
        im = rv.frame_of(base, overlay, cam, L, seen, 15.0, 1.0, 0.5, 0.2, 10, 2.0)
        self.assertEqual(im.shape, base.shape)
        self.assertGreater(float((im[20:] - base[20:]).max()), 0.05)
        np.testing.assert_allclose(im[:20], 0.5)
        blind = rv.frame_of(base, overlay, cam, L, np.zeros_like(seen), 15.0, 1.0, 0.5, 0.2, 10, 2.0)
        np.testing.assert_allclose(blind[20:], base[20:], atol=1e-6)


class TestRam(unittest.TestCase):
    def test_free_memory_reads_as_a_number_of_gigabytes(self):
        free = rb.free_ram_gb()
        self.assertTrue(free is None or 0.0 < free < 1e5)

    def test_waits_until_enough_is_free(self):
        said = [1.0, 2.0, 20.0]
        slept = []
        real_free, real_sleep = rb.free_ram_gb, rb.time.sleep
        rb.free_ram_gb, rb.time.sleep = (lambda: said.pop(0)), slept.append
        try:
            rb.wait_for_ram(15.0, poll_s=7.0)
        finally:
            rb.free_ram_gb, rb.time.sleep = real_free, real_sleep
        self.assertEqual(slept, [7.0, 7.0])
        self.assertEqual(said, [])


class TestCaption(unittest.TestCase):
    def test_the_words_and_the_bar_are_drawn(self):
        o = np.asarray(rb.caption_overlay((640, 360), 16.0, 4.0, ["v5 40 km/h"], "精度は確かめていない"))
        self.assertEqual(o.shape, (360, 640, 4))
        self.assertGreater(int(o[:, :, 3].max()), 200)
        self.assertEqual(int(o[180, 600, 3]), 0)  # the middle of the picture stays clear


if __name__ == "__main__":
    unittest.main()

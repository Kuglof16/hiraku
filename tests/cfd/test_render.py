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

    def test_lines_between_the_camera_and_the_vehicle_are_picked_out_whole(self):
        wall = box((0.0, 0.0, 1.0), (2.0, 1.0, 2.0))  # a vehicle's outline, x -1..1, y -0.5..0.5
        eye = np.array([0.0, -6.0, 1.0])

        def along(y, z, x0=-2.0, x1=2.0):
            x = np.arange(x0, x1 + 1e-9, 0.02)
            return np.column_stack([x, np.full(len(x), y), np.full(len(x), z)])

        rows = [along(-1.0, 1.0),  # between the camera and the box for 2 m and more: out
                along(1.5, 1.0),  # behind the box: stays
                along(0.0, 1.0),  # through the box itself, as a line through the open frame: stays
                along(-1.0, 3.5),  # above the box as the camera sees it: stays
                along(-1.0, 1.0, -1.15, -0.95)]  # before it for less than the limit: stays
        pts = np.vstack(rows)
        off = np.concatenate([[0], np.cumsum([len(r) for r in rows])])
        L = Lines(pts, np.ones(len(pts)), np.zeros(len(pts)), np.zeros(len(pts)), off, np.arange(5), np.zeros(5, np.int8))
        self.assertEqual(rb.in_front_of_vehicle(L, wall, eye).tolist(), [True, False, False, False, False])
        kept = L.select(~rb.in_front_of_vehicle(L, wall, eye))
        self.assertEqual(len(kept), 4)
        np.testing.assert_array_equal(kept.line(0)["points"], rows[1])  # whole, not cut

    def test_the_outline_is_the_vehicles_box_turned_with_it(self):
        from hiraku.cfd.vehicle import to_grid

        view = to_grid(20.0) @ to_grid(0.0).T
        # one long box as the vehicle at yaw 0, x -2..2, y -0.5..0.5, z 0..1.5; a side net outside it does not count
        at0 = box((0.0, 0.0, 0.75), (4.0, 1.0, 1.5))
        turned = at0.transformed(view, np.zeros(3))
        net = box((0.0, 3.0, 0.75), (1.0, 0.01, 1.0))
        net.reflect = 0.3
        wheel = Cylinder(view @ np.array([0.0, 0.0, 0.3]), view @ np.array([0.0, 1.0, 0.0]), 0.3, 0.2)
        out = rb.vehicle_outline([turned, net, wheel], view)
        np.testing.assert_allclose(np.sort(np.round(out.vertices @ view, 9), axis=0), np.sort(np.round(turned.vertices @ view, 9), axis=0), atol=1e-6)
        self.assertTrue(out.inside((view @ np.array([1.9, 0.4, 1.4]))[None]).all())
        self.assertFalse(out.inside((view @ np.array([0.0, 3.0, 0.75]))[None]).any())
        self.assertFalse(out.inside(np.array([[1.9, 0.4, 1.4]])).all())  # the same numbers on the grid are elsewhere: it is turned


class TestLook(unittest.TestCase):
    """Drawing only: what is added looks like a dummy and a wheel, and no shape moves."""

    DESIGN = Path(__file__).resolve().parents[1] / "verification" / "fixtures" / "n15-v005-design.json"

    def test_the_dummy_colours_are_the_screens(self):
        np.testing.assert_allclose(rb.MATERIALS["dummy"][0], rb.to_linear(np.array([0xe8, 0xae, 0x0e]) / 255.0))
        np.testing.assert_allclose(rb.MATERIALS["dummy_joint"][0], rb.to_linear(np.array([0x1c, 0x1c, 0x1c]) / 255.0))

    def test_each_occupant_part_gets_the_screens_colour(self):
        self.assertEqual(rb.occupant_segment("occupant_R"), "torso")
        self.assertEqual(rb.occupant_segment("occupant_R.thigh_L"), "thigh")
        kinds = {"occupant_R": "occupant", "occupant_R.head": "occupant", "occupant_R.thigh_L": "occupant", "occupant_R.neck": "occupant", "occupant_R.hand_L": "occupant", "occupant_L.foot_R": "occupant", "seat_R": "seat"}
        want = {"occupant_R": "dummy", "occupant_R.head": "dummy", "occupant_R.thigh_L": "dummy", "occupant_R.neck": "dummy_joint", "occupant_R.hand_L": "dummy_joint", "occupant_L.foot_R": "dummy_joint", "seat_R": "occupants"}
        for name, cat in want.items():
            self.assertEqual(rb.category_of(box((0, 0, 0), (1, 1, 1), None, name, "occupants"), kinds), cat, name)

    def test_a_slice_keeps_to_its_stretch_of_the_box(self):
        s = box((1.0, 2.0, 3.0), (4.0, 1.0, 0.5), None, "occupant_R.arm_L", "occupants")
        v, f = rb.box_slice(s, 0.25, 0.5, 0.01)
        self.assertEqual(v.shape, (8, 3))
        self.assertEqual(f.shape, (6, 4))
        np.testing.assert_allclose(v.min(axis=0), (0.0, 1.49, 2.74))
        np.testing.assert_allclose(v.max(axis=0), (1.0, 2.51, 3.26))
        centre = v.mean(axis=0)
        for q in f:  # the faces turn outwards
            n = np.cross(v[q[1]] - v[q[0]], v[q[2]] - v[q[1]])
            self.assertGreater(float(n @ (v[q].mean(axis=0) - centre)) * self._hand(s), 0.0)

    @staticmethod
    def _hand(s):
        """+1 when the box's vertex order keeps the slice's faces outward, -1 when it mirrors them; every face the same way."""
        v = s.vertices
        edges = [v[4] - v[0], v[2] - v[0], v[1] - v[0]]
        lengths = [float(np.linalg.norm(e)) for e in edges]
        long = int(np.argmax(lengths))
        a, b = [k for k in range(3) if k != long]
        return float(np.sign(np.dot(np.cross(edges[long], edges[a]), edges[b])))

    def test_joints_sit_at_a_limbs_ends_and_the_torsos_waist(self):
        arm = box((0.0, 0.0, 0.0), (0.44, 0.1, 0.1), None, "occupant_R.arm_L", "occupants")
        ends = rb.dummy_joints(arm)
        self.assertEqual(len(ends), 2)
        self.assertAlmostEqual(float(ends[0][0][:, 0].min()), -0.22, places=9)
        self.assertAlmostEqual(float(ends[1][0][:, 0].max()), 0.22, places=9)
        self.assertLess(float(ends[0][0][:, 0].max()), -0.1)
        torso = box((0.0, 0.0, 1.0), (0.3, 0.5, 1.0), None, "occupant_R", "occupants")
        waist = rb.dummy_joints(torso)
        self.assertEqual(len(waist), 1)
        np.testing.assert_allclose((waist[0][0][:, 2].min(), waist[0][0][:, 2].max()), (1.5 - 0.62, 1.5 - 0.5))  # shares from the top
        self.assertEqual(rb.dummy_joints(box((0, 0, 0), (0.2, 0.2, 0.2), None, "occupant_R.head", "occupants")), [])

    def test_the_wheels_look_stays_on_the_tyre_and_is_a_lighter_rim(self):
        c = Cylinder((0.0, 0.61, 0.3), (0.0, 1.0, 0.0), 0.3, 0.2, "wheel_FL", "wheels")
        look = rb.wheel_look(c, grooves=40)
        self.assertEqual(set(look), {"rim", "hub", "tread"})
        self.assertGreater(float(np.mean(rb.MATERIALS["rim"][0])), 5.0 * float(np.mean(rb.MATERIALS["wheels"][0])))
        self.assertLess(float(np.mean(rb.MATERIALS["tread"][0])), float(np.mean(rb.MATERIALS["wheels"][0])))
        for v, _ in look["rim"]:
            np.testing.assert_allclose(np.abs(v[:, 1] - 0.61), 0.1 + 0.0015)  # just off the tyre's side
            np.testing.assert_allclose(np.hypot(v[:, 0], v[:, 2] - 0.3), 0.62 * 0.3)
        v, f = look["tread"][0]
        self.assertEqual(len(f), 40 + 2 * 96)
        np.testing.assert_allclose(np.hypot(v[:, 0], v[:, 2] - 0.3), 0.3 + 0.0015)  # just off the tread
        self.assertLess(float(np.abs(v[:, 1] - 0.61).max()), 0.1)

    def test_no_shape_moves_and_the_air_sees_the_same_shapes(self):
        from hiraku.cfd.vehicle import design_shapes

        design = json.loads(self.DESIGN.read_text(encoding="utf-8"))
        shapes, _ = design_shapes(design, 20.0, 11.1, dx=0.056)
        before = [(s.vertices.copy() if hasattr(s, "vertices") else (s.center.copy(), s.axis.copy(), s.radius, s.half)) for s in shapes]
        meshes = rb.vehicle_meshes(design, shapes)
        for s, b in zip(shapes, before):
            if hasattr(s, "vertices"):
                np.testing.assert_array_equal(s.vertices, b)
            else:
                np.testing.assert_array_equal(s.center, b[0])
                self.assertEqual((s.radius, s.half), b[2:])
        tyres = [s for s in shapes if s.group == "wheels" and hasattr(s, "axis")]
        self.assertEqual(len(tyres), 4)
        drawn = meshes["wheels"].arrays("w")["w_v"]
        for t in tyres:  # each cylinder is drawn where it is, at its own radius and width
            r = drawn - t.center.astype(np.float32)
            along = r @ t.axis.astype(np.float32)
            at_cap = np.isclose(np.abs(along), t.half, atol=1e-5)
            radial = np.linalg.norm(r - np.outer(along, t.axis.astype(np.float32)), axis=1)
            self.assertGreaterEqual(int((at_cap & np.isclose(radial, t.radius, atol=1e-5)).sum()), 144)
        for cat in ("dummy", "dummy_joint", "rim", "hub", "tread"):
            self.assertIn(cat, meshes)
            self.assertIn(cat, rb.MATERIALS)


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

    def test_a_tall_panel_fits_the_sensor_to_its_height(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "c.json"
            p.write_text(json.dumps({"matrix_world": np.eye(4).tolist(), "lens": 36.0, "sensor_width": 36.0, "resolution": [960, 1080]}), encoding="utf-8")
            self.assertEqual(rv.Camera.from_json(p).focal_px, 1080.0)

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
        p0, spd, frac, near, gain = rv.light_samples(L, 0.0, spacing=1.0, tail=0.1, n_tail=4, air=2.0)
        np.testing.assert_allclose(gain, 1.0)
        np.testing.assert_allclose(p0[:, 2], 1.0)
        self.assertTrue(np.isin(np.round(p0[:, 1]), (0.0, 1.0, 2.0)).all())
        np.testing.assert_allclose(spd, 2.0)
        self.assertTrue((p0[:, 0] >= 0.0).all() and (p0[:, 0] <= 10.0 + 1e-9).all())
        self.assertTrue(((frac >= 0.0) & (frac < 1.0)).all())
        self.assertEqual(int(near.max()) < len(L.points), True)
        heads0 = p0[frac == 0.0]
        heads1 = rv.light_samples(L, 0.25, spacing=1.0, tail=0.1, n_tail=4, air=2.0)
        heads1 = heads1[0][heads1[2] == 0.0]
        self.assertGreater(len(heads0), 3)
        for h in heads0:  # every light is 0.25 s x 2 m/s further along its own line, unless it has run off the end
            if h[0] + 0.5 <= 10.0:
                on_line = heads1[np.isclose(heads1[:, 1], h[1])]
                self.assertLess(float(np.abs(on_line[:, 0] - (h[0] + 0.5)).min()), 1e-9)

    def test_the_lights_repeat_after_one_spacing_at_any_line_length(self):
        L = straight_lines(speed=2.0, n=101, count=5)
        # lines of different travel times: the pattern must not depend on them
        L.time[101:202] *= 1.37
        L.time[202:303] *= 0.61
        a = rv.light_samples(L, 0.3, spacing=0.5, tail=0.4, n_tail=8, air=2.0)
        b = rv.light_samples(L, 0.3 + 0.5, spacing=0.5, tail=0.4, n_tail=8, air=2.0)
        c = rv.light_samples(L, 0.3 + 3 * 0.5, spacing=0.5, tail=0.4, n_tail=8, air=2.0)
        for other in (b, c):
            self.assertEqual(len(a[0]), len(other[0]))
            np.testing.assert_allclose(a[0], other[0], atol=1e-9)
            np.testing.assert_allclose(a[1], other[1], atol=1e-9)
            np.testing.assert_allclose(a[4], other[4], atol=1e-9)

    def test_the_spacing_fits_the_clip_a_whole_number_of_times(self):
        self.assertAlmostEqual(rv.loop_spacing(1.0, 0.6), 0.5)
        self.assertAlmostEqual(rv.loop_spacing(1.0, 0.3), 1.0 / 3.0)
        self.assertAlmostEqual(rv.loop_spacing(0.8, 5.0), 0.8)  # never fewer than one per clip
        for flow, wanted in ((1.0, 0.6), (0.8, 0.35), (1.2, 0.7)):
            n = flow / rv.loop_spacing(flow, wanted)
            self.assertAlmostEqual(n, round(n))

    def test_the_frame_after_the_last_is_the_first(self):
        world = np.eye(4)
        world[:3, 3] = (5.0, 1.0, 30.0)
        cam = rv.Camera(world, 1000.0, (320, 180))
        L = straight_lines(speed=2.0)
        seen = np.ones(len(L.points), bool)
        base = np.full((180, 320, 3), 0.02, np.float32)
        frames, flow = 150, 1.0
        spacing = rv.loop_spacing(flow, 0.6)
        at = lambda f: rv.lit(base, cam, L, seen, 15.0, f / frames * flow, spacing, 0.4, 10, 2.0)
        np.testing.assert_allclose(at(150), at(0), atol=1e-3)
        self.assertGreater(float(np.abs(at(1) - at(0)).max()), 0.01)  # and it does move

    def test_a_light_runs_out_past_the_end_of_its_line(self):
        L = straight_lines(speed=2.0, count=1)
        lengths = []
        for clock in (4.9, 5.0, 5.05, 5.1, 5.15, 5.3):  # the line takes 5 s; the tail of 0.4 m takes 0.2 s more to leave
            p, _, frac, _, _ = rv.light_samples(L, clock, spacing=100.0, tail=0.4, n_tail=8, air=2.0)
            lengths.append(len(p))
            self.assertTrue((p[:, 0] <= 10.0 + 1e-9).all())
        self.assertEqual(lengths[0], 8)
        self.assertGreater(lengths[2], lengths[3])  # it goes out piece by piece, not all at once
        self.assertGreater(lengths[3], lengths[4])
        self.assertEqual(lengths[-1], 0)

    def test_a_light_follows_a_bent_line_not_a_straight_cut(self):
        th = np.linspace(0.0, math.pi / 2.0, 200)
        pts = np.column_stack([np.cos(th), np.sin(th), np.zeros(200)])
        t = th.copy()  # unit speed on the unit circle
        L = Lines(pts, np.ones(200), np.zeros(200), t, np.array([0, 200]), np.array([0]), np.zeros(1, np.int8))
        p, *_ = rv.light_samples(L, 0.2, spacing=0.3, tail=0.2, n_tail=8, air=1.0)
        np.testing.assert_allclose(np.hypot(p[:, 0], p[:, 1]), 1.0, atol=1e-4)

    def test_tails_stop_at_the_start_of_the_line(self):
        L = straight_lines(count=1)
        p, *_ = rv.light_samples(L, 0.0, spacing=100.0, tail=2.0, n_tail=10, air=2.0)
        self.assertTrue((p[:, 0] >= 0.0).all())
        self.assertLess(len(p), 11)

    def test_slow_air_has_a_shorter_dimmer_tail_and_no_pile_up(self):
        L = straight_lines(speed=0.5, count=1)  # a quarter of the air speed: the floor of 30 % applies
        p, _, frac, _, gain = rv.light_samples(L, 12.0, spacing=100.0, tail=2.0, n_tail=10, air=2.0)
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
        im = rv.covered(rv.lit(base, cam, L, seen, 15.0, 1.0, 0.5, 0.2, 10, 2.0), overlay)
        self.assertEqual(im.shape, base.shape)
        self.assertGreater(float((im[20:] - base[20:]).max()), 0.05)
        np.testing.assert_allclose(im[:20], 0.5)
        blind = rv.covered(rv.lit(base, cam, L, np.zeros_like(seen), 15.0, 1.0, 0.5, 0.2, 10, 2.0), overlay)
        np.testing.assert_allclose(blind[20:], base[20:], atol=1e-6)

    def test_the_clip_is_encoded_at_even_quality_and_at_most_the_threads_given(self):
        from unittest import mock

        with mock.patch.object(rv.shutil, "which", return_value="ffmpeg"), mock.patch.object(rv.subprocess, "run") as run:
            rv.encode(Path("frames"), Path("flow.mp4"), 30, 4)
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[cmd.index("-threads") + 1], "4")
        self.assertIn("ipratio=1.0", cmd[cmd.index("-x264-params") + 1])
        self.assertEqual(cmd[cmd.index("-framerate") + 1], "30")
        self.assertEqual(cmd[-1], "flow.mp4")

    def test_the_seam_report_reads_the_frames(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as t:
            for k, v in ((1, 10), (2, 12), (3, 14), (4, 16)):
                Image.fromarray(np.full((4, 4, 3), v, np.uint8)).save(Path(t) / f"f{k:04d}.png")
            self.assertEqual(rv.seam_report(Path(t), 4), (6.0, 2.0))


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
    STATUS = "精度は確かめていない"
    WORDS = ["v5  40 km/h  横風 0°  格子 56 mm", "計算した平均の流れの流線 (色は速さ)", "光の動きは計算した速さの 0.2 倍"]

    def overlay(self, blocks, size=(1920, 1080)):
        return np.asarray(rb.caption_overlay(size, 16.0, 4.0, blocks))

    def test_nothing_is_drawn_where_the_subtitles_go(self):
        o = self.overlay([(self.WORDS, self.STATUS)])
        self.assertEqual(o.shape, (1080, 1920, 4))
        self.assertEqual(int(o[910:, :, 3].max()), 0)
        self.assertGreater(int(o[850:909, :, 3].max()), 200)  # the colour bar ends just above them
        o2 = self.overlay([(self.WORDS[:1], self.STATUS), (self.WORDS[:1], self.STATUS)])
        self.assertEqual(int(o2[910:, :950, 3].max()), 0)
        self.assertEqual(int(o2[910:, 970:, 3].max()), 0)

    def test_the_colour_bar_is_right_of_the_near_wheel(self):
        o = self.overlay([(self.WORDS, self.STATUS)])
        rows = o[700:905]
        self.assertEqual(int(rows[:, :1300, 3].max()), 0)
        self.assertGreater(int(rows[:, 1400:, 3].max()), 200)

    def test_the_words_are_about_twice_their_old_size_and_the_status_is_amber(self):
        o = self.overlay([(self.WORDS, self.STATUS)])
        # the amber of the status line, under the three lines of words at 52 px x 1.24 per row
        amber = (o[:, :, 0] > 240) & (o[:, :, 1] > 200) & (o[:, :, 2] < 140) & (o[:, :, 3] > 200)
        ys = np.nonzero(amber.any(axis=1))[0]
        self.assertGreater(len(ys), 30)  # about as tall as a 52 px line
        self.assertGreater(int(ys.min()), 3 * 52)
        xs = np.nonzero(amber.any(axis=0))[0]
        self.assertGreater(int(xs.max() - xs.min()), 10 * 44)  # ten characters at about 52 px

    def test_each_panel_has_its_own_words_and_status(self):
        o = self.overlay([(["横風 0°"], self.STATUS), (["横風 +20°"], self.STATUS)])
        amber = (o[:, :, 0] > 240) & (o[:, :, 1] > 200) & (o[:, :, 2] < 140) & (o[:, :, 3] > 200)
        self.assertTrue(amber[:, :960].any())
        self.assertTrue(amber[:, 960:].any())

    def test_long_words_shrink_to_their_panel_but_stay_readable(self):
        long = ["あ" * 60]
        o = self.overlay([(long, self.STATUS), (long, self.STATUS)])
        band = o[60, :, 3] > 0
        self.assertFalse(band[:28].any())
        from PIL import Image, ImageDraw

        g = ImageDraw.Draw(Image.new("RGB", (4, 4)))
        self.assertEqual(rb._fitting_font(g, ["あ" * 60], 52, 800, 26).size, 26)  # never under the film's smallest
        self.assertLess(rb._fitting_font(g, ["あ" * 20], 52, 800, 26).size, 52)
        self.assertEqual(rb._fitting_font(g, ["あ"], 52, 800, 26).size, 52)

    def test_the_view_turns_with_the_vehicle(self):
        from hiraku.cfd.vehicle import to_grid

        view = to_grid(20.0) @ to_grid(0.0).T
        eye0 = np.array([-3.6, -7.2, 2.3])
        vehicle_point = np.array([1.0, 0.5, 0.3])  # in vehicle axes: where it is on the grid at each yaw, seen from each eye
        at0, at20 = to_grid(0.0) @ vehicle_point, to_grid(20.0) @ vehicle_point
        np.testing.assert_allclose(view @ at0, at20)
        np.testing.assert_allclose(np.linalg.norm(view @ eye0 - at20), np.linalg.norm(eye0 - at0))
        self.assertEqual(float((view @ eye0)[2]), 2.3)

    def test_yaw_words(self):
        self.assertEqual([rb.yaw_words(y) for y in (0.0, 20.0, -5.0)], ["0°", "+20°", "-5°"])


if __name__ == "__main__":
    unittest.main()

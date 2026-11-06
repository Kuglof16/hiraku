"""The design as shapes on the grid, without a GPU: axes, wheels, thin parts, sealed pockets."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku.cad.geom import member_axes as cad_member_axes
from hiraku.cad.geom import rpy_matrix as cad_rpy
from hiraku.cfd.geometry import Grid, box, geometry_report, link_arrays, voxelize
from hiraku.cfd.vehicle import Domain, design_shapes, frontal_area_m2, member_axes, moment_centre, rpy_matrix, to_grid

DESIGN = Path(__file__).resolve().parents[1] / "verification" / "fixtures" / "n15-v005-design.json"


def load_design() -> dict:
    return json.loads(DESIGN.read_text(encoding="utf-8"))


class Axes(unittest.TestCase):
    def test_a_proper_rotation_with_the_air_on_grid_x(self):
        for yaw in (0.0, 5.0, -5.0, 20.0):
            R = to_grid(yaw)
            np.testing.assert_allclose(R @ R.T, np.eye(3), atol=1e-14)
            self.assertAlmostEqual(np.linalg.det(R), 1.0, places=12)
            b = math.radians(yaw)
            air_in_vehicle_axes = np.array([-math.cos(b), -math.sin(b), 0.0])
            np.testing.assert_allclose(R @ air_in_vehicle_axes, (1.0, 0.0, 0.0), atol=1e-14)

    def test_wind_from_the_left_pushes_to_the_right(self):
        # positive yaw: a force along the air has a negative vehicle y part and a backward vehicle x part
        f_vehicle = to_grid(15.0).T @ np.array([100.0, 0.0, 0.0])
        self.assertLess(f_vehicle[1], 0.0)
        self.assertLess(f_vehicle[0], 0.0)

    def test_the_same_box_and_member_conventions_as_the_design_record(self):
        for rpy in ((0, 0, 0), (10, 20, 30), (-40, 5, 70)):
            np.testing.assert_allclose(rpy_matrix(rpy), cad_rpy(rpy), atol=1e-14)
        for d in ((1, 0, 0), (0, 1, 0), (0, 0, 1), (0, 0, -1), (0.6, 0.8, 0)):
            d = np.asarray(d, float)
            for mine, theirs in zip(member_axes(d), cad_member_axes(d)):
                np.testing.assert_allclose(mine, theirs, atol=1e-12)


class Design(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = load_design()

    def test_moment_centre_is_mid_wheelbase_on_the_ground(self):
        c, ground = moment_centre(self.design)
        self.assertEqual(ground, 0.0)
        np.testing.assert_allclose(c, (-1010.0, 0.0, 0.0))

    def test_wheels_stand_on_the_ground_and_roll_with_it(self):
        V = 11.0
        for yaw in (0.0, 15.0, -5.0):
            shapes, _ = design_shapes(self.design, yaw, V)
            u_ground = to_grid(yaw) @ np.array([-V, 0.0, 0.0])
            wheels = [s for s in shapes if s.name.startswith("wheel_")]
            self.assertEqual(len(wheels), 4)
            for w in wheels:
                self.assertAlmostEqual(w.bounds()[0][2], 0.0, places=9)
                contact = w.center - np.array([0.0, 0.0, w.radius])
                np.testing.assert_allclose(w.velocity(contact[None, :])[0], u_ground, atol=1e-9)

    def test_loading_decides_who_is_on_board(self):
        full, _ = design_shapes(self.design, 0.0, 10.0, loading="two_cargo")
        empty, _ = design_shapes(self.design, 0.0, 10.0, loading="empty")
        names = {s.name for s in full}
        self.assertIn("cargo", names)
        self.assertNotIn("cargo", {s.name for s in empty})
        self.assertLess(len(empty), len(full))

    def test_thin_parts_stay_on_a_coarse_grid(self):
        shapes, _ = design_shapes(self.design, 0.0, 10.0, dx=0.08)
        vox = voxelize(shapes, Domain().grid(0.08), ground=True)
        rep = geometry_report(vox)
        seen = {shapes[i].name for i in np.unique(vox.shape)}
        for thin in ("floor", "floor_front", "deck", "fender_FL_1"):  # 3 mm plates on an 80 mm grid
            self.assertIn(thin, seen)
        self.assertLess(abs(rep["grid_projected_area_x_error_pct"]), 5.0)
        self.assertLess(abs(rep["voxel_volume_error_pct"]), 10.0)

    def test_nets_are_screens_not_walls(self):
        shapes, info = design_shapes(self.design, 0.0, 10.0, dx=0.08)
        nets = [s for s in shapes if s.group == "side_nets"]
        self.assertTrue(nets)
        for n in nets:
            self.assertLess(n.reflect, 0.05)
        self.assertEqual(len(info["screens"]), len(nets))

    def test_frontal_area_does_not_count_nets(self):
        shapes, _ = design_shapes(self.design, 0.0, 10.0)
        a = frontal_area_m2(shapes)
        self.assertGreater(a, 1.0)
        self.assertLess(a, 3.0)


class Pockets(unittest.TestCase):
    def grid(self) -> Grid:
        return Grid(24, 24, 24, 0.1, np.array([-1.2, -1.2, -1.2]))

    def test_a_sealed_box_of_thin_plates_is_filled(self):
        t = 0.003
        h = 0.45
        plates = [box((sx * h, 0, 0), (t, 2 * h, 2 * h)) for sx in (-1, 1)]
        plates += [box((0, sy * h, 0), (2 * h, t, 2 * h)) for sy in (-1, 1)]
        plates += [box((0, 0, sz * h), (2 * h, 2 * h, t)) for sz in (-1, 1)]
        vox = voxelize(plates, self.grid())
        self.assertGreater(vox.enclosed_nodes, 0)
        # every node inside has no links left
        i, j, k = vox.grid.ijk(vox.node)
        p = vox.grid.origin + (np.stack([i, j, k], axis=1) + 0.5) * vox.grid.dx
        self.assertFalse(np.any(np.all(np.abs(p) < h - 0.01, axis=1)))

    def test_a_box_with_a_hole_stays_open(self):
        t = 0.003
        h = 0.45
        plates = [box((sx * h, 0, 0), (t, 2 * h, 2 * h)) for sx in (-1,)]  # one wall missing
        plates += [box((0, sy * h, 0), (2 * h, t, 2 * h)) for sy in (-1, 1)]
        plates += [box((0, 0, sz * h), (2 * h, 2 * h, t)) for sz in (-1, 1)]
        self.assertEqual(voxelize(plates, self.grid()).enclosed_nodes, 0)


class MovingWall(unittest.TestCase):
    def test_a_moving_wall_adds_no_mass_at_any_node(self):
        design = load_design()
        shapes, _ = design_shapes(design, 0.0, 11.0, dx=0.08)
        vox = voxelize(shapes, Domain().grid(0.08), ground=True)
        L = link_arrays(vox, np.zeros(3), 0.05 / 11.0)
        self.assertTrue(np.any(L.dh != 0.0))
        nodes = L.slot_a % vox.grid.n_total
        sums = np.bincount(np.searchsorted(np.unique(nodes), nodes), weights=L.dh.astype(float))
        self.assertLess(np.abs(sums).max(), 1e-6)


if __name__ == "__main__":
    unittest.main()

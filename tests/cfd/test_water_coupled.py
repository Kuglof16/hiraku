"""The flooded-road pass with the K2 vehicle answering the water: how the forces go in, against hand calculations. The GPU case is a
short coarse run; the rest needs no GPU."""

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from hiraku.cfd.cuda import Gpu, gpu_or_reason, gpu_turn
from hiraku.cfd.render_water_blender import build_scene, wheel_offsets
from hiraku.cfd.water import Road, load_design, momentum_budget, plan_case
from hiraku.cfd.water_coupled import WHEELS, CoupledVehicle, FloodRoad, TravellingWheel, coupled_table, couple_loads, run_coupled, shape_centres
from hiraku.tools.cfd_tools import k2_parts

K2 = k2_parts()
DESIGN = Path(__file__).resolve().parents[1] / "verification" / "fixtures" / "n15-v005-design.json"
LIFT_RATIO = 0.25  # any value below 1; the runs for the real one are not in the repository


def design():
    return load_design(DESIGN)


class AtRest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.veh = K2.build_vehicle(K2.on_board(design(), "two_cargo"))
        cls.k_z = [float(c.k_z) for c in cls.veh.corners]

    def settled(self) -> tuple[Any, FloodRoad]:
        road = FloodRoad(Road(0.0), 0.0, self.k_z)
        sim = K2.Sim(self.veh, road, dt=1e-3)
        K2.settle(sim, 3.0)
        return sim, road

    def run_on(self, sim: Any, ext: Any = None, steps: int = 4000) -> None:
        for _ in range(steps):
            sim.step(K2.hold(sim), ext)

    def test_lift_on_the_wheels_raises_them_by_the_tyre(self) -> None:
        sim, road = self.settled()
        z0, wheels0, n0 = sim.p[2], sim.wheel_centres()[:, 2].copy(), list(sim.out["ntot"])
        road.lift = [250.0] * 4
        self.run_on(sim)
        rise = 250.0 / self.k_z[0]
        self.assertAlmostEqual(sim.p[2] - z0, rise, delta=0.1 * rise)  # the spring's load does not change, so the body rises with the wheels
        self.assertAlmostEqual(float(np.mean(sim.wheel_centres()[:, 2] - wheels0)), rise, delta=0.1 * rise)
        for before, now in zip(n0, sim.out["ntot"]):  # the contact still carries the same load: the tyre's share is 250 N less
            self.assertAlmostEqual(now, before, delta=5.0)

    def test_force_pairs_are_the_same_moment(self) -> None:
        moment = np.array([120.0, -340.0, 56.0])
        force = np.array([10.0, -20.0, 30.0])
        loads = couple_loads(force, moment)
        total = sum(np.array(f) for f, _ in loads)
        turn = sum(np.cross(np.array(p), np.array(f)) for f, p in loads)
        np.testing.assert_allclose(total, force)
        np.testing.assert_allclose(turn, moment)

    def test_force_up_ahead_of_the_centre_lifts_the_nose(self) -> None:
        sim, _ = self.settled()
        self.run_on(sim, couple_loads(np.zeros(3), np.array([0.0, -1000.0, 0.0])))  # about +y: nose up
        self.assertLess(sim.attitude()[1], -0.005)


class OnTheRamp(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.design = design()
        cls.plan = plan_case(cls.design, 0.2, 5.0, 0.06, True, "two_cargo")
        cls.table = coupled_table(cls.plan["shapes"], 0.06, cls.plan["grid"].height_m)
        cls.interval = cls.plan["per_frame"] // 3 * cls.plan["dt"]

    def car(self) -> CoupledVehicle:
        return CoupledVehicle(K2, self.design, self.plan["road"], 5.0, self.plan["x_start"], self.interval, shape_centres(self.table), self.table.groups, LIFT_RATIO)

    def test_starts_where_the_rigid_pass_starts(self) -> None:
        x, z, pitch = self.car().pose_now()
        rigid = self.plan["path"].pose(0.0)
        self.assertAlmostEqual(x, rigid.x, places=6)
        self.assertAlmostEqual(z, rigid.z, delta=0.02)  # the tyres squash in K2
        self.assertAlmostEqual(pitch, rigid.pitch, delta=0.01)

    def test_wheels_are_on_the_road_in_the_water_axes(self) -> None:
        car = self.car()
        centres, spin = car.wheel_rows()
        x, z, pitch = car.pose_now()
        c, s = np.cos(pitch), np.sin(pitch)
        wx = x + c * centres[:, 0] + s * centres[:, 2]
        wz = z - s * centres[:, 0] + c * centres[:, 2]
        slope = self.plan["road"].gradient(wx)
        gap = (wz - self.plan["road"].height(wx)) / np.sqrt(1.0 + slope ** 2) - 0.3
        np.testing.assert_allclose(gap, 0.0, atol=2e-3)  # touching, not pressed in by the squash
        np.testing.assert_allclose(centres[:, 1], [-0.61, 0.61, -0.61, 0.61], atol=0.02)
        self.assertTrue(np.all(spin > 0.5))

    def test_water_force_slows_and_lifts(self) -> None:
        car = self.car()
        n = len(self.table.groups)
        before = car.sim.forward_speed()
        force = np.zeros((n, 3))
        force[car.others, 0] = 3000.0 / len(car.others)  # rearward on the vehicle, in water axes
        for _ in range(60):
            car.advance(force, self.interval)
        self.assertLess(car.sim.forward_speed(), before - 0.5)

    def test_wheel_rows_have_room_to_travel(self) -> None:
        wheels = [s for s in self.table.solids if isinstance(s, TravellingWheel)]
        self.assertEqual(len(wheels), 4)
        self.assertEqual({s.group for s in wheels}, {f"wheel_{w}" for w in WHEELS})
        lo, _ = wheels[0].bounds()
        self.assertLess(lo[2], -0.1)


class OnGpu(unittest.TestCase):
    def test_short_coarse_pass(self) -> None:
        turn = gpu_turn(wait_s=float(os.environ.get("HIRAKU_GPU_WAIT_S", "1800")))
        with turn:
            gpu, reason = gpu_or_reason("water")
            if gpu is None:
                self.skipTest(reason)
            with gpu:
                self.short_pass(gpu)

    def short_pass(self, gpu: Gpu) -> None:
        d = design()
        with tempfile.TemporaryDirectory() as folder:
            case = run_coupled(gpu, K2, d, LIFT_RATIO, dx=0.06, out=folder, frame_dt=1.0 / 90.0, t_max_s=0.8)
            self.assertIsNone(case["faulted"])
            records = case["records"]
            self.assertTrue(all("vehicle" in r for r in records))
            budget = momentum_budget(case)
            self.assertLess(max(budget["residual_over_gross"]), 1e-3)
            self.assertLess(records[-1]["vehicle"]["speed_ms"], records[0]["vehicle"]["speed_ms"])  # water in front of it, nothing pushing back
            info = build_scene(Path(folder), DESIGN, Path(folder) / "meshes")
            offsets = wheel_offsets(records[-1], info["wheel_centres"])
            self.assertEqual(set(offsets), {f"wheel_{w}" for w in WHEELS})
            self.assertTrue(all(abs(v[2]) < 0.2 for v in offsets.values()))
            with np.load(Path(folder) / "meshes" / "scene.npz") as s:
                self.assertIn("veh_wheel_FL_v", s.files)
                self.assertNotIn("veh_wheels_v", s.files)


if __name__ == "__main__":
    unittest.main()

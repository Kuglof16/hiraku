"""The suspension mount forces over a run (shelf/mounts.py) and kinematics.TravelSolver."""

import math
import unittest

import numpy as np

from hiraku import contract
from hiraku.solvers.k2_vehicle import kinematics
from hiraku.solvers.k2_vehicle.model import box_design, build_vehicle
from hiraku.solvers.k2_vehicle.shelf import TOOLS
from hiraku.solvers.k2_vehicle.shelf.braking import brake_stop
from hiraku.solvers.k2_vehicle.shelf.mounts import _load_map, corner_mounts, mount_peak, mount_series, peak_states
from hiraku.solvers.k2_vehicle.units import G0
from tests.solvers.k2.slow import SLOW

DESIGN = box_design(susp="kc_table", wheel_mass=15.0)
IDS = ("FL", "FR", "RL", "RR")


def mount_names():
    return [f"mount_{a}.{c}.{m}" for a in "xyz" for c in IDS for m in kinematics.MOUNTS]


def series_peaks(series) -> dict:
    """{'<support>.<mount>': largest |F| in the series}."""
    ch = {s["channel"]: s["values"] for s in series}
    out = {}
    for c in IDS:
        for m in kinematics.MOUNTS:
            xyz = np.array([ch[f"mount_{a}.{c}.{m}"] for a in "xyz"])
            out[f"{c}.{m}"] = float(np.sqrt((xyz ** 2).sum(axis=0)).max())
    return out


class TravelSolverTest(unittest.TestCase):
    def test_it_gives_solve_at_travel_bit_for_bit(self):
        hp = kinematics.Hardpoints.from_json(build_vehicle(DESIGN).corners[0].table["hardpoints_mm"])
        ts = kinematics.TravelSolver(hp)
        for travel in (0.031, -0.012, 0.0, 0.004, -0.047, 0.0305, -0.002):  # out of order: the march is reused
            a, b = kinematics.solve_at_travel(hp, travel), ts.solve(travel)
            for k in a:
                np.testing.assert_array_equal(np.asarray(a[k]), np.asarray(b[k]), err_msg=f"{travel} {k}")

    def test_near_is_within_a_march_step(self):
        veh = build_vehicle(DESIGN)
        ts = kinematics.table_solver(veh.corners[0].table)
        z0 = float(ts.hp.wheel_center[2])
        for travel in (0.0237, -0.0311):
            dz = float(ts.near(travel)["wheel_center"][2]) - z0
            self.assertLess(abs(dz - travel), 0.0012)


class LoadMapTest(unittest.TestCase):
    def test_the_map_is_corner_mount_forces(self):
        # for a fixed bench solution the mount forces are linear in the knuckle's force and moment
        veh = build_vehicle(DESIGN)
        for c in (veh.corners[0], veh.corners[1]):
            sol = kinematics.table_solution(c.table, 0.018)
            mounts, ref, a = _load_map(c, sol)
            f, mom = np.array([-700.0, 350.0 * c.side, 2600.0]), np.array([25.0, -90.0, 12.0])
            cp = np.array([c.pos[0] + 0.02, c.pos[1] + 0.01, c.pos[2] - 0.25])
            want = kinematics.corner_mount_forces(c.table, c.side, 0.018, f, cp, mom, sol=sol)
            got = (a @ np.concatenate([f, mom + np.cross(cp - ref, f)])).reshape(-1, 3)
            for j, m in enumerate(mounts):
                np.testing.assert_allclose(got[j], want[m][1], atol=1e-6)


class BrakingMountsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lc = {"load_case_id": "S4", "params": {"speed": {"value": 35.0, "unit": "km/h"}}}
        cls.rec = TOOLS["braking"].run(DESIGN, lc, [{"prediction_id": "pred-0001"}], {})

    def test_the_record_carries_the_mount_series_and_one_peak(self):
        rec = self.rec
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        ser = {s["channel"]: s for s in rec["series"]}
        names = mount_names()
        self.assertTrue(all(n in ser for n in names))
        t = ser[names[0]]["t_s"]
        for n in names + ["sprung_roll", "sprung_pitch", "inertia_force_x", "gravity_z"]:
            self.assertEqual(ser[n]["t_s"], t, n)
        self.assertAlmostEqual(ser["gravity_z"]["values"][0], -G0, places=3)
        peaks = [r for r in rec["results"] if r["quantity"] == "mount_force_peak"]
        self.assertEqual(len(peaks), 1)
        top = max(series_peaks(rec["series"]).items(), key=lambda kv: kv[1])
        self.assertAlmostEqual(peaks[0]["value"], top[1], places=6)
        self.assertTrue(peaks[0]["where"].startswith(top[0] + " at t "), peaks[0]["where"])
        joints = [v for v in rec["vectors"] if v["kind"] == "joint"]
        self.assertEqual(len(joints), 4 * len(kinematics.MOUNTS))

    def test_the_mount_forces_balance_the_sprung_body(self):
        # sum of the mount forces + the sprung weight + its d'Alembert force = 0 at every kept instant
        ser = {s["channel"]: s["values"] for s in self.rec["series"]}
        m_s = build_vehicle(DESIGN).m_s
        for a in "xyz":
            mounts = np.sum([ser[f"mount_{a}.{c}.{m}"] for c in IDS for m in kinematics.MOUNTS], axis=0)
            rest = m_s * np.array(ser[f"gravity_{a}"]) + np.array(ser[f"inertia_force_{a}"])
            np.testing.assert_allclose(mounts + rest, 0.0, atol=0.05)

    def test_each_mount_peak_is_kept(self):
        # the screening finds each mount's largest force: the series' largest is that of every logged
        # state solved exactly (FL and RR), to 0.5 %
        veh = build_vehicle(DESIGN)
        r = brake_stop(veh, 35.0 / 3.6, rec_hz=25.0, rise=0.2)
        series = mount_series(veh, r["states"], [])
        got = series_peaks(series)
        for i, cid in ((0, "FL"), (3, "RR")):
            exact = {m: 0.0 for m in kinematics.MOUNTS}
            for st in r["states"]:
                for m, (_, f) in corner_mounts(veh, st, i).items():
                    exact[m] = max(exact[m], float(np.linalg.norm(f)))
            for m in kinematics.MOUNTS:
                self.assertAlmostEqual(got[f"{cid}.{m}"] / exact[m], 1.0, delta=5e-3, msg=f"{cid}.{m}")
        self.assertLessEqual(len(peak_states(veh, r["states"])), 4 * len(kinematics.MOUNTS))
        self.assertIsNotNone(mount_peak(series))

    def test_frame_static_reads_the_braking_series(self):
        from hiraku.solvers.shelf.frame_static import upstream_mount_series
        from hiraku.solvers.k2_vehicle.model import hardpoints_of
        d = dict(DESIGN, supports=[dict(s, suspension=dict(s.get("suspension") or {},
                                                          hardpoints_mm=hardpoints_of(DESIGN, s["id"])))
                                   for s in DESIGN["supports"]])
        got = upstream_mount_series({"S4": dict(self.rec, analysis_id="ana-0001")}, d)
        self.assertIsNotNone(got)
        n = len({s["channel"]: s for s in self.rec["series"]}["mount_x.FL.lca_front"]["t_s"])
        self.assertEqual(len(got[2]), n)


@SLOW
class TiltMountsTest(unittest.TestCase):
    def test_the_tilt_gives_the_mount_forces_up_to_the_tip(self):
        rec = TOOLS["tilt"].run(DESIGN, {"load_case_id": "S1", "params": {"direction": "right"}},
                                [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        res = {r["quantity"]: r for r in rec["results"]}
        ser = {s["channel"]: s for s in rec["series"]}
        # the table turns gravity: at the last kept instant (the tip) it leans by the tip angle
        gy, gz = ser["gravity_y"]["values"][-1], ser["gravity_z"]["values"][-1]
        self.assertAlmostEqual(math.degrees(math.atan2(abs(gy), -gz)), res["tilt_angle"]["value"], delta=0.05)
        self.assertAlmostEqual(res["mount_force_peak"]["value"], max(series_peaks(rec["series"]).values()), places=6)
        self.assertTrue(any("cos(theta)" in a for a in rec["assumptions"]))


@SLOW
class DrivenMountsTest(unittest.TestCase):
    def check(self, tool, params):
        rec = TOOLS[tool].run(DESIGN, {"load_case_id": tool, "params": params}, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        peaks = [r for r in rec["results"] if r["quantity"] == "mount_force_peak"]
        self.assertEqual(len(peaks), 1)
        self.assertAlmostEqual(peaks[0]["value"], max(series_peaks(rec["series"]).values()), places=6)
        self.assertEqual(len([v for v in rec["vectors"] if v["kind"] == "joint"]), 4 * len(kinematics.MOUNTS))

    def test_the_lane_change_gives_the_mount_forces(self):
        self.check("lane_change", {"speed": {"value": 40.0, "unit": "km/h"}, "find_limit": False})

    def test_the_launch_gives_the_mount_forces(self):
        q = lambda v, u: {"value": v, "unit": u}
        self.check("launch", {"target_speed": q(20.0, "km/h"), "motor_torque": q(20.0, "N*m"),
                              "gear_ratio": q(8.0, "1"), "motor_power": q(5.0, "kW"), "max_time": q(15.0, "s")})

    def test_the_rough_slope_gives_the_mount_forces(self):
        self.check("offroad_roll", {"length": {"value": 20.0, "unit": "m"}})


if __name__ == "__main__":
    unittest.main()

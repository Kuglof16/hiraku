"""The K&C compliance (bushings and tie rod) and the K&C bench tool."""

import math
import unittest

import numpy as np

from hiraku import contract
from hiraku.solvers.k2_vehicle import kinematics
from hiraku.solvers.k2_vehicle.drive import SpeedController, controls, start_rolling
from hiraku.solvers.k2_vehicle.model import box_design, build_vehicle
from hiraku.solvers.k2_vehicle.scenario import new_sim
from hiraku.solvers.k2_vehicle.shelf import TOOLS


class Compliance(unittest.TestCase):
    def test_the_builtin_table_carries_the_compliance(self):
        veh = build_vehicle(box_design(susp="kc_table", wheel_mass=15.0))
        c = veh.corners[0]
        self.assertIsNotNone(c.path.comp)
        row = c.table["rows"][len(c.table["rows"]) // 2]
        self.assertEqual(row["travel_mm"], 0.0)
        for k in kinematics.COMPLIANCE_KEYS:
            self.assertIn(f"{k}_{kinematics.COMPLIANCE_UNIT[k]}", row)
        self.assertTrue(any("bushing" in a for a in veh.assumptions))
        self.assertEqual(c.table["bushings"], {"radial_N_per_mm": 3000.0, "axial_N_per_mm": 500.0,
                                               "tie_rod_N_per_mm": 10000.0})

    def test_the_vehicle_steers_on_its_compliance_in_a_corner(self):
        veh = build_vehicle(box_design(susp="kc_table", wheel_mass=15.0))
        sim = new_sim(veh, settle_s=1.0)
        start_rolling(sim, 10.0)
        ctrl = SpeedController(sim)
        for _ in range(3000):
            drive, brake = ctrl.torques(sim, 10.0)
            sim.step(controls(sim, math.radians(3.0), drive, brake))
        self.assertTrue(all(abs(x) > 1e-5 for x in sim.ct))  # rad: the side forces turn the wheels
        # the path's toe is the table's toe at the travel plus the compliance's
        i = 0
        with_c = sim._path(i, sim.s[i])[12]
        ct, sim.ct[i] = sim.ct[i], 0.0
        without = sim._path(i, sim.s[i])[12]
        self.assertAlmostEqual(with_c - without, ct, places=15)

    def test_stiffer_bushings_steer_less(self):
        hp = kinematics.generic_sla(np.array([0.0, 0.6, 0.3]), 0.3)
        sol = kinematics.DoubleWishboneBench(hp).solve(0.0)
        soft = kinematics.compliance(hp, sol, kinematics.Bushings(2e6, 0.5e6, 5e6))
        hard = kinematics.compliance(hp, sol, kinematics.Bushings(8e6, 2e6, 20e6))
        for k in kinematics.COMPLIANCE_KEYS:
            self.assertLess(abs(hard[k]), abs(soft[k]), k)


class KcBench(unittest.TestCase):
    def test_bench_series_are_against_travel_and_force(self):
        d = box_design(susp="kc_table", wheel_mass=15.0)
        rec = TOOLS["kc_bench"].run(d, {"load_case_id": "kc", "params": {"support": "FL"}},
                                    [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        xs = {s["channel"]: s["x"]["quantity"] for s in rec["series"]}
        self.assertEqual(xs["camber"], "travel")
        self.assertEqual(xs["roll_center_height"], "travel")
        self.assertEqual(xs["toe_lateral"], "lateral_force")
        self.assertEqual(xs["toe_longitudinal"], "longitudinal_force")
        self.assertEqual(xs["toe_aligning"], "aligning_moment")
        res = {r["quantity"]: r["value"] for r in rec["results"]}
        lat = [s for s in rec["series"] if s["channel"] == "toe_lateral"][0]
        self.assertAlmostEqual(lat["values"][-1], res["toe_at_1kN_lateral"] * lat["x"]["values"][-1])
        self.assertGreater(res["roll_center_height"], 0.0)

    def test_k0_predicts_the_bench(self):
        from hiraku import solvers
        from hiraku.solvers.k2_vehicle.shelf.kc_bench import k0_inputs
        d = box_design(susp="kc_table", wheel_mass=15.0)
        rec = TOOLS["kc_bench"].run(d, {"load_case_id": "kc", "params": {}}, [{"prediction_id": "p"}], {})
        res = {r["quantity"]: r["value"] for r in rec["results"]}
        ins = k0_inputs(build_vehicle(d).corners[0].table)
        reg = solvers.formula_registry()
        h = reg.evaluate("kc.roll_center_height", ins["kc.roll_center_height"])["outputs"]["h_rc"]
        self.assertEqual(h["unit"], "mm")
        self.assertAlmostEqual(h["value"] / res["roll_center_height"], 1.0, delta=2e-3)
        toe = reg.evaluate("kc.aligning_steer", dict(ins["kc.aligning_steer"], M={"value": 100.0, "unit": "N*m"}))
        toe = toe["outputs"]["toe"]["value"]
        # K0 gives way only at the tie rod: the same sign, less than K2's bench with the bushings
        self.assertLess(res["toe_at_aligning_moment"], toe)
        self.assertLess(toe, 0.0)

    def test_hardpoints_of_a_support_are_the_bench_points(self):
        from hiraku.solvers.k2_vehicle.model import hardpoints_of
        d = box_design(susp="kc_table", wheel_mass=15.0)
        veh = build_vehicle(d)
        fl, fr = hardpoints_of(d, "FL"), hardpoints_of(d, "FR")
        self.assertEqual(fl, veh.corners[0].table["hardpoints_mm"])
        self.assertEqual(fr["tie_inner"], [fl["tie_inner"][0], -fl["tie_inner"][1], fl["tie_inner"][2]])
        # the tie rod is placed for the least bump steer: under 0.1 deg over the table
        toe = [r["toe_deg"] for r in veh.corners[0].table["rows"]]
        self.assertLess(max(toe) - min(toe), 0.1)

    def test_mount_forces_match_their_closed_forms(self):
        from hiraku.solvers.k2_vehicle.shelf.kc_bench import _mount_checks
        for c in _mount_checks():
            self.assertTrue(c["pass"], c)

    def test_the_step_record_shows_the_mount_loads(self):
        d = box_design(susp="kc_table", wheel_mass=15.0)
        lc = {"load_case_id": "p1", "params": {"step_height": {"value": 100.0, "unit": "mm"}}}
        rec = TOOLS["step_climb_dynamic"].run(d, lc, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        loads = [v for v in rec["vectors"] if v["kind"] == "load"]
        self.assertEqual({v["at"].split(".")[1] for v in loads}, set(kinematics.MOUNTS))
        # per corner the loads on the body add up to that wheel's road force (reaction + grip)
        for cid in ("FL", "RR"):
            got = np.sum([v["vector_N"] for v in loads if v["at"].startswith(cid + ".")], axis=0)
            road = np.sum([v["vector_N"] for v in rec["vectors"] if v.get("at") == cid and v["kind"] in ("reaction", "grip")],
                          axis=0)
            np.testing.assert_allclose(got, road, atol=1e-6 * max(1.0, float(np.linalg.norm(road))))

    def test_a_design_without_a_kc_table_is_told_so(self):
        rec = TOOLS["kc_bench"].run(box_design(), {"load_case_id": "kc", "params": {}}, [{"prediction_id": "p"}], {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("kc_table", rec["error"])


if __name__ == "__main__":
    unittest.main()

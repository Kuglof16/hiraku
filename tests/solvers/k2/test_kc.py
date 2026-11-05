"""The K&C compliance (bushings and tie rod) and the K&C bench tool."""

import copy
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

    def test_bench_damper_and_steering_curves(self):
        d = box_design(susp="kc_table", wheel_mass=15.0)
        rec = TOOLS["kc_bench"].run(d, {"load_case_id": "kc", "params": {}}, [{"prediction_id": "pred-0001"}], {})
        s = {x["channel"]: x for x in rec["series"]}
        self.assertEqual(s["damper_length"]["x"]["quantity"], "travel")
        dl = s["damper_length"]["values"]
        self.assertGreater(dl[0], dl[-1])  # rebound to bump: the damper shortens
        mr = s["motion_ratio"]["values"]
        self.assertTrue(all(0.3 < m < 1.2 for m in mr))
        # its integral over the travel is the damper's shortening
        tr = s["damper_length"]["x"]["values"]
        np.testing.assert_allclose(dl[0] - dl[-1], np.trapezoid(mr, tr), rtol=0.02)
        left, right, acker = s["steer_left"], s["steer_right"], s["steer_outer_ackermann"]
        self.assertEqual(left["x"]["quantity"], "rack_travel")
        xs = left["x"]["values"]
        # mirror pair: the right wheel at rack x is minus the left wheel at rack -x
        for k in (5, 20, 40):
            np.testing.assert_allclose(right["values"][k], -np.interp(-xs[k], xs, left["values"]), atol=1e-9)
        # turning left the inner (left) wheel turns more than the outer one; Ackermann asks less of the outer
        k = int(np.argmax(left["values"]))
        self.assertGreater(left["values"][k], right["values"][k])
        self.assertLess(acker["values"][k], left["values"][k])

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
        loads = [v for v in rec["vectors"] if v["kind"] == "joint"]
        self.assertEqual({v["at"].split(".")[1] for v in loads}, set(kinematics.MOUNTS))
        self.assertEqual(len(loads), 4 * len(kinematics.MOUNTS))
        # per corner the loads on the body add up to that wheel's road force (reaction + grip)
        # and its unsprung weight (15 kg), less its inertia (small in the slow push: within 2 %)
        for cid in ("FL", "RR"):
            got = np.sum([v["vector_N"] for v in loads if v["at"].startswith(cid + ".")], axis=0)
            road = np.sum([v["vector_N"] for v in rec["vectors"] if v.get("at") == cid and v["kind"] in ("reaction", "grip")],
                          axis=0)
            np.testing.assert_allclose(got, road + [0.0, 0.0, -15.0 * 9.80665], atol=0.02 * np.linalg.norm(road))
        # the time series: every mount of every corner on one time base, closer around the lift-off
        ser = {s["channel"]: s for s in rec["series"]}
        names = [f"mount_{a}.{c}.{m}" for a in "xyz" for c in ("FL", "FR", "RL", "RR") for m in kinematics.MOUNTS]
        self.assertTrue(all(n in ser for n in names))
        t = ser[names[0]]["t_s"]
        self.assertTrue(20 <= len(t) <= 80, len(t))
        self.assertTrue(all(ser[n]["t_s"] == t and ser[n]["unit"] == "N" for n in names))
        for n in ("sprung_accel_x", "sprung_ang_vel_y", "inertia_force_z", "inertia_moment_y", "sprung_pitch",
                  "sprung_cg_z"):
            self.assertEqual(ser[n]["t_s"], t, n)
        gaps = np.diff(t)
        self.assertLess(gaps.min(), gaps.max() / 5.0)

    def test_the_mount_forces_balance_the_knuckle_load(self):
        # statics: the forces on the body at the mounts carry the knuckle's whole load, its moment
        # included, for a left and a mirrored right corner
        veh = build_vehicle(box_design(susp="kc_table", wheel_mass=15.0))
        for c in (veh.corners[0], veh.corners[1]):
            f, x = np.array([-900.0, 400.0 * c.side, 3000.0]), np.array([c.pos[0] + 0.01, c.pos[1], c.pos[2]])
            mom = np.array([30.0, -120.0, 15.0 * c.side])
            got = kinematics.corner_mount_forces(c.table, c.side, 0.02, f, x, mom)
            np.testing.assert_allclose(sum(v for _, v in got.values()), f, atol=1e-6)
            np.testing.assert_allclose(sum(np.cross(p, v) for p, v in got.values()), np.cross(x, f) + mom, atol=1e-6)

    def test_the_body_loads_balance_through_the_push(self):
        # the mount forces, the drive reactions on the body, its weight and its d'Alembert force
        # and moment balance at every kept instant (the knuckles carry the unsprung mass)
        from hiraku.solvers.k2_vehicle.shelf.step import corner_mounts, step_push
        veh = build_vehicle(box_design(susp="kc_table", wheel_mass=15.0))
        front = [i for i, c in enumerate(veh.corners) if c.steer]
        r = step_push(veh, 0.1, front, rec_hz=25.0)
        self.assertGreater(len(r["states"]), 50)
        for st in r["states"][::10]:
            force, moment = st["weight"] + st["inertia_force"], st["inertia_moment"].copy()
            for i in range(len(veh.corners)):
                for p, f in corner_mounts(veh, st, i).values():
                    force, moment = force + f, moment + np.cross(p - st["cg"], f)
                moment = moment + st["corners"][i]["body_torque"]
            w = float(np.linalg.norm(st["weight"]))
            self.assertLess(np.linalg.norm(force) / w, 1e-9)
            self.assertLess(np.linalg.norm(moment) / w, 1e-9)  # m

    def test_the_mount_points_come_from_the_design(self):
        # the design's suspension.hardpoints_mm: K2 builds its tables from them, and hardpoints_of
        # returns them; the builtin points given back run as the builtin table does
        from hiraku.solvers.k2_vehicle.model import hardpoints_of
        d = box_design(susp="kc_table", wheel_mass=15.0)
        given = copy.deepcopy(d)
        for s in given["supports"]:
            hp = hardpoints_of(d, s["id"])
            s["suspension"]["hardpoints_mm"] = {k: v for k, v in hp.items() if k not in ("axle", "radius_mm")}
        for s in given["supports"]:
            hp = hardpoints_of(given, s["id"])
            self.assertEqual(hp["tie_inner"], s["suspension"]["hardpoints_mm"]["tie_inner"])
            self.assertEqual(hp["axle"][1], 1.0 if s["position_mm"][1] > 0 else -1.0)
        a, b = build_vehicle(d), build_vehicle(given)
        self.assertTrue(any("hardpoints_mm" in n for n in b.assumptions))
        for ca, cb in zip(a.corners, b.corners):
            for ra, rb in zip(ca.table["rows"], cb.table["rows"]):
                for k, v in ra.items():
                    np.testing.assert_allclose(rb[k], v, rtol=1e-9, atol=1e-9, err_msg=k)
        lc = {"load_case_id": "p1", "params": {"step_height": {"value": 100.0, "unit": "mm"}}}
        ra, rb = (TOOLS["step_climb_dynamic"].run(x, lc, [{"prediction_id": "pred-0001"}], {}) for x in (d, given))
        for x, y in zip(ra["results"], rb["results"]):
            self.assertAlmostEqual(y["value"], x["value"], delta=1e-6 * max(1.0, abs(x["value"])), msg=x["quantity"])

    def test_moved_design_points_change_the_run(self):
        from hiraku.solvers.k2_vehicle.model import DesignError, hardpoints_of
        d = box_design(susp="kc_table", wheel_mass=15.0)
        for s in d["supports"]:
            hp = hardpoints_of(d, s["id"])
            hp = {k: v for k, v in hp.items() if k not in ("axle", "radius_mm")}
            hp["tie_inner"] = [hp["tie_inner"][0], hp["tie_inner"][1], hp["tie_inner"][2] + 20.0]
            s["suspension"]["hardpoints_mm"] = hp
            s["suspension"]["bushings"] = {"radial_N_per_mm": 6000.0}
        c = build_vehicle(d).corners[0]
        toe = [r["toe_deg"] for r in c.table["rows"]]
        self.assertGreater(max(toe) - min(toe), 0.1)  # a raised tie rod inner point steers with the travel
        self.assertEqual(c.table["bushings"], {"radial_N_per_mm": 6000.0, "axial_N_per_mm": 500.0,
                                               "tie_rod_N_per_mm": 10000.0})
        # the left and right tables mirror each other
        right = next(x for x in build_vehicle(d).corners if x.id == "FR")
        self.assertEqual(right.table["rows"], c.table["rows"])
        bad = copy.deepcopy(d)
        del bad["supports"][0]["suspension"]["hardpoints_mm"]["uca_outer"]
        with self.assertRaisesRegex(DesignError, "uca_outer"):
            build_vehicle(bad)
        bad = copy.deepcopy(d)
        bad["supports"][0]["suspension"]["hardpoints_mm"]["wheel_center"][2] += 5.0
        with self.assertRaisesRegex(DesignError, "wheel_center"):
            build_vehicle(bad)

    def test_hardpoints_for_moves_the_upper_arm_and_the_damper_mount(self):
        from hiraku.solvers.k2_vehicle.model import DesignError, hardpoints_for, hardpoints_of
        d = box_design(susp="kc_table", wheel_mass=15.0)
        fl, fr = (next(s for s in d["supports"] if s["id"] == k) for k in ("FL", "FR"))
        same = hardpoints_of(d, "FL")
        self.assertEqual(hardpoints_for(fl), {k: v for k, v in same.items() if k not in ("axle", "radius_mm")})
        up = same["uca_outer"][2] + 20.0
        hp = hardpoints_for(fl, upper_joint_z_mm=up, damper_upper_y_mm=same["damper_upper"][1] + 30.0)
        self.assertAlmostEqual(hp["uca_outer"][2], up, places=9)
        self.assertAlmostEqual(hp["uca_front"][2], up - 15.0, places=9)  # the arm keeps its slope
        self.assertAlmostEqual(hp["damper_upper"][1], same["damper_upper"][1] + 30.0, places=9)
        for k in ("lca_front", "lca_rear", "lca_outer", "wheel_center", "damper_lower"):
            self.assertEqual(hp[k], same[k], k)
        right = hardpoints_for(fr, upper_joint_z_mm=up, damper_upper_y_mm=same["damper_upper"][1] + 30.0)
        self.assertEqual(right["damper_upper"], [hp["damper_upper"][0], -hp["damper_upper"][1], hp["damper_upper"][2]])
        # written into the design, K2 runs on them: the tie rod was placed again, little bump steer
        for s in d["supports"]:
            s["suspension"]["hardpoints_mm"] = hardpoints_for(s, upper_joint_z_mm=up)
        toe = [r["toe_deg"] for r in build_vehicle(d).corners[0].table["rows"]]
        self.assertLess(max(toe) - min(toe), 0.1)
        with self.assertRaisesRegex(DesignError, "FL"):
            hardpoints_for(fl, upper_joint_z_mm=-500.0)

    def test_a_design_without_a_kc_table_is_told_so(self):
        rec = TOOLS["kc_bench"].run(box_design(), {"load_case_id": "kc", "params": {}}, [{"prediction_id": "p"}], {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("kc_table", rec["error"])


if __name__ == "__main__":
    unittest.main()

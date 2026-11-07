import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import designgen
from hiraku.solvers.k0_handcalc import frame_inputs
from hiraku.solvers.k0_handcalc.library import all_formulas
from hiraku.solvers.k0_handcalc.library_joint import Strip, joint_wall, ring_plate
from hiraku.solvers.k0_handcalc.registry import Registry

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
E, NU = 205000.0, 0.3
DEMO = {"b": 40.0, "h": 80.0, "t": 2.3, "b_c": 40.0, "h_c": 40.0, "t_c": 2.3, "E": E, "nu": NU, "leg": 0.0,
        "through": 0.0, "open_end": 0.0}


class StripTest(unittest.TestCase):
    """The Levy strip against plate results that do not need the series."""

    def test_long_line_load_is_cylindrical_bending(self):
        a, t, q, z0 = 77.7, 2.3, 10.0, 20.0
        p = Strip(a, t, E, NU, [(-60 * a, 60 * a, z0, q)], [], n_terms=400)
        D = E * t**3 / (12 * (1 - NU**2))
        mx, mz, _ = p.moments([0.0], [z0])
        beam = q * z0 * (a - z0) / a  # simply supported strip of unit width, point load q at z0
        self.assertAlmostEqual(mz[0, 0] / beam, 1.0, delta=0.005)
        self.assertAlmostEqual(mx[0, 0] / (NU * beam), 1.0, delta=0.005)
        w = p.deflection([0.0], [z0])[0, 0]
        self.assertAlmostEqual(w / (q * z0**2 * (a - z0) ** 2 / (3 * D * a)), 1.0, delta=0.002)

    def test_free_edge_carries_no_bending_across_it(self):
        a, t = 77.7, 2.3
        p = Strip(a, t, E, NU, [(0.0, 30.0, 20.0, 5.0)], [(30.0, 20.0, 50.0, 5.0, -5.0)], free_edge=0.0)
        zs = np.linspace(0.0, a, 41)
        mx_edge = p.moments([0.0], zs)[0]
        mz_in = p.moments([15.0], zs)[1]
        self.assertLess(np.abs(mx_edge).max(), 2e-3 * np.abs(mz_in).max())
        free = p.deflection([0.0], [20.0])[0, 0]
        cont = Strip(a, t, E, NU, [(0.0, 30.0, 20.0, 5.0)], [(30.0, 20.0, 50.0, 5.0, -5.0)]).deflection([0.0], [20.0])[0, 0]
        self.assertGreater(abs(free), abs(cont))  # the edge gives way more than a wall running on

    def test_ring_is_linear_in_the_moment(self):
        r1 = ring_plate(77.7, 2.3, E, NU, 37.7, 37.7, 2.3, 1.0e6)
        r2 = ring_plate(77.7, 2.3, E, NU, 37.7, 37.7, 2.3, 2.0e6)
        self.assertAlmostEqual(r2["sigma"] / r1["sigma"], 2.0, places=9)
        self.assertAlmostEqual(r2["theta"] / r1["theta"], 2.0, places=9)
        self.assertGreater(r1["theta"], 0.0)


class JointWallFormulaTest(unittest.TestCase):
    def test_through_is_in_plane_shear(self):
        out = joint_wall(dict(DEMO, M=1.0e5, through=1.0))
        self.assertAlmostEqual(out["sigma"], math.sqrt(3) * 1.0e5 / ((40 - 2.3) * 2 * (40 - 2.3) * 2.3), places=9)
        self.assertEqual(out["theta_per_kNm"], 0.0)

    def test_gussets_spread_the_load(self):
        butt = joint_wall(dict(DEMO, M=1.0e5))
        gus = joint_wall(dict(DEMO, M=1.0e5, leg=60.0))
        self.assertLess(gus["sigma"], butt["sigma"])
        self.assertLess(gus["theta_per_kNm"], butt["theta_per_kNm"])

    def test_open_end_bound_is_above(self):
        out = joint_wall(dict(DEMO, M=1.0e5, open_end=1.0))
        self.assertGreater(out["sigma_open_end"], out["sigma"])
        self.assertEqual(joint_wall(dict(DEMO, M=1.0e5))["sigma_open_end"], joint_wall(dict(DEMO, M=1.0e5))["sigma"])

    def test_open_end_with_gussets(self):
        # v5's rail-end L joints with gusset_ends 1 (gusset_leg 80, rail 45 x 80 x 4.5, cross member 40 x 60 x 2.3):
        # the spread over the gussets stops at the rail's free end (it gave NaN past it before)
        v = dict(DEMO, b=45.0, h=80.0, t=4.5, b_c=40.0, h_c=60.0, t_c=2.3, M=133580.0, open_end=1.0)
        bare, gusset = joint_wall(dict(v, leg=0.0)), joint_wall(dict(v, leg=80.0))
        for out in (bare, gusset):
            self.assertTrue(all(math.isfinite(x) for x in out.values()), out)
        self.assertLess(gusset["sigma_open_end"], bare["sigma_open_end"])  # the gussets spread the load
        self.assertGreater(gusset["sigma_open_end"], gusset["sigma"])  # the free edge still bounds it from above
        # the plate is linear: the free-edge case scales with the moment
        self.assertAlmostEqual(joint_wall(dict(v, leg=80.0, M=2 * v["M"]))["sigma_open_end"],
                               2 * gusset["sigma_open_end"], places=6)

    def test_record_and_refusals(self):
        reg = Registry(all_formulas())
        f = reg.get("frame_static.joint_wall_bending")
        self.assertIn("Levy", f.record()["source"])
        q = {k: {"value": v, "unit": "mm" if k not in ("E", "nu", "through", "open_end") else
                 ("MPa" if k == "E" else "1")} for k, v in DEMO.items()}
        with self.assertRaisesRegex(ValueError, "lower than the rail"):
            reg.evaluate("frame_static.joint_wall_bending", dict(q, M={"value": 1e5, "unit": "N*mm"},
                                                                 h_c={"value": 80.0, "unit": "mm"}))


class JointWallInputsTest(unittest.TestCase):
    """frame_inputs.joint_wall_inputs on the demo: the torsion chain and the joint it picks."""

    @classmethod
    def setUpClass(cls):
        cls.reg = Registry(all_formulas())
        cls.butt = frame_inputs.joint_wall_inputs(V1, loading="two_cargo")
        cls.through = frame_inputs.joint_wall_inputs(designgen.regenerate(V1, {"joints": "through"}), loading="two_cargo")

    def test_cross_members_and_mounts_take_all_the_twisting_moment(self):
        for ji in (self.butt, self.through):
            total = sum(ji["twisting_moments_Nmm"].values())
            taken = sum(j["M_Nmm"] for j in ji["joints"]) * np.sign(total) + sum(ji["mount_moments_Nmm"].values())
            self.assertAlmostEqual(taken, total, delta=1e-6 * abs(total))

    def test_picks_a_joint_off_the_wheel_mounts(self):
        """With wheel_mounts False (the mounts' joints in the chain, not compared)."""
        off = [frame_inputs.joint_wall_inputs(d, loading="two_cargo", wheel_mounts=False)
               for d in (V1, designgen.regenerate(V1, {"joints": "through"}))]
        self.assertEqual([ji["joints"] for ji in off], [self.butt["joints"], self.through["joints"]])
        for ji in off:
            self.assertNotIn(ji["joint"], ("LA", "LB"))
            self.assertFalse(ji["at_wheel_mount"])
            free = [j for j in ji["joints"] if not j["at_wheel_mount"]]
            self.assertEqual(max(free, key=lambda j: j["sigma_MPa"])["node"], ji["joint"])
            out = self.reg.evaluate("frame_static.joint_wall_bending", ji["inputs"])["outputs"]
            self.assertAlmostEqual(out["sigma"]["value"], max(j["sigma_MPa"] for j in free), places=6)
        self.assertEqual(off[0]["joint"], "LE")
        self.assertEqual({j["node"] for j in self.butt["joints"] if j["at_wheel_mount"]}, {"LA", "LB"})

    def test_through_joints_take_more_and_spare_the_end(self):
        """Rigid joints at A, S1, S2 and B draw the twisting moment off the rail ends."""
        m = lambda ji, n: next(j["M_Nmm"] for j in ji["joints"] if j["node"] == n)  # noqa: E731
        self.assertGreater(m(self.through, "LB"), m(self.butt, "LB"))
        self.assertLess(m(self.through, "LE"), 0.5 * m(self.butt, "LE"))
        self.assertTrue(all(j["theta_per_kNm"] == 0.0 for j in self.through["joints"] if j["kind"] == "through"))

    def test_step_raises_the_rear(self):
        step = frame_inputs.joint_wall_inputs(V1, loading="two_cargo", step_mm=100.0, step_supports=["FL", "FR"])
        self.assertEqual(step["mode"], "pushed_step")
        self.assertGreater(step["inputs"]["M"]["value"], self.butt["inputs"]["M"]["value"])


class SplitCrossMemberTest(unittest.TestCase):
    """Tower braces standing on cross_B (tower_brace_y > 0) split it into cross_B_L, cross_B, cross_B_R: the hand
    calculations still see one cross member between the rail joints (v5: R3's end cross member and R1s' station B)."""

    @classmethod
    def setUpClass(cls):
        cls.d = designgen.regenerate(V1, {"tower_brace_b": {"value": 35.0, "unit": "mm"},
                                          "tower_brace_t": {"value": 4.5, "unit": "mm"},
                                          "tower_brace_y": {"value": 150.0, "unit": "mm"}})

    def test_the_run_counts_as_one_member(self):
        props = frame_inputs.member_props(self.d)
        self.assertEqual([m for m in props if m.startswith("cross_B")], ["cross_B_L", "cross_B", "cross_B_R"])
        whole = frame_inputs._member_between(props, "LB", "RB")
        self.assertIsNotNone(whole)
        self.assertEqual(whole["run"], ["cross_B_L", "cross_B", "cross_B_R"])
        self.assertAlmostEqual(whole["length"], sum(props[m]["length"] for m in whole["run"]), places=6)
        self.assertIsNone(frame_inputs._member_between(props, "LB", "RE"))  # not on one line

    def test_r3_and_r1s_hand_inputs(self):
        frame_inputs.end_cross_member_inputs(self.d, loading="two_cargo")  # raised DesignError before
        from hiraku.solvers.shelf.frame_static import front_supports
        r = frame_inputs.joint_wall_inputs(self.d, "two_cargo", 100.0, front_supports(self.d))
        self.assertIn("LB", [j["node"] for j in r["joints"]])

    def test_r1_rail_joint_inputs(self):
        # R1's rail joint: the cross member is the whole run (700 mm, not cross_B_L's piece), and the rail ahead is
        # held at the next station with a cross member, not at the roll bar's foot node on the rail
        r = frame_inputs.rail_joint_inputs(self.d, loading="two_cargo")
        ins = {k: v["value"] for k, v in r["inputs"].items()}
        props = frame_inputs.member_props(self.d)
        self.assertAlmostEqual(ins["L_c"], frame_inputs._member_between(props, r["node"], "R" + r["node"][1:])["length"])
        self.assertGreater(ins["L1"], props[r["rail_member"]]["length"] - 1e-9)
        xs = {n["id"]: n["xyz_mm"][0] for n in self.d["nodes"]}
        station = xs[r["node"]] + ins["L1"]
        mirror_of = {n["id"]: n for n in self.d["nodes"]}
        ahead = [k for k, x in xs.items() if abs(x - station) < 1e-6 and abs(mirror_of[k]["xyz_mm"][1]
                 - mirror_of[r["node"]]["xyz_mm"][1]) < 1e-6 and abs(mirror_of[k]["xyz_mm"][2] - mirror_of[r["node"]]["xyz_mm"][2]) < 1e-6]
        self.assertEqual(len(ahead), 1)
        self.assertIsNotNone(frame_inputs._member_between(props, ahead[0], "R" + ahead[0][1:]))


if __name__ == "__main__":
    unittest.main()

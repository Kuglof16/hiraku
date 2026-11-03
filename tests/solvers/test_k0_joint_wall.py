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

    def test_cross_members_take_all_the_twisting_moment(self):
        for ji in (self.butt, self.through):
            total = sum(ji["twisting_moments_Nmm"].values())
            self.assertAlmostEqual(sum(j["M_Nmm"] for j in ji["joints"]), abs(total), delta=1e-6 * abs(total))

    def test_picks_a_joint_off_the_wheel_mounts(self):
        for ji in (self.butt, self.through):
            self.assertNotIn(ji["joint"], ("LA", "LB"))
            free = [j for j in ji["joints"] if not j["at_wheel_mount"]]
            self.assertEqual(max(free, key=lambda j: j["sigma_MPa"])["node"], ji["joint"])
            out = self.reg.evaluate("frame_static.joint_wall_bending", ji["inputs"])["outputs"]
            self.assertAlmostEqual(out["sigma"]["value"], max(j["sigma_MPa"] for j in free), places=6)
        self.assertEqual(self.butt["joint"], "LE")
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


if __name__ == "__main__":
    unittest.main()

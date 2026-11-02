import math
import unittest

from hiraku import contract
from hiraku.contract.protocols import FormulaRegistry
from hiraku.solvers.k0_handcalc import registry


def q(v, u):
    return {"value": v, "unit": u}


class FormulaLedgerTest(unittest.TestCase):
    def setUp(self):
        self.R = registry()

    def test_protocol_and_contract_shape(self):
        for name in ("list_formulas", "evaluate"):  # FormulaRegistry is not runtime_checkable
            self.assertTrue(callable(getattr(self.R, name)), name)
            self.assertTrue(hasattr(FormulaRegistry, name))
        ids = set()
        for f in self.R.list_formulas():
            contract.validate("formula", f)
            ids.add(f["formula_id"])
        for needed in ("step_climb.wheel_force_rigid", "mass_props.static_wheel_loads",
                       "frame_static.simple_beam_center_load", "frame_static.cantilever_tip_load",
                       "frame_static.bending_stress"):
            self.assertIn(needed, ids)

    def test_evaluate_returns_contract_quantities_and_terms(self):
        out = self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(100, "mm"), "W": q(1000, "N")})
        self.assertEqual(set(out), {"outputs", "terms", "expression"})
        for v in out["outputs"].values():
            contract.validate("quantity", v)
        for t in out["terms"]:
            contract.validate("term", t)

    def test_step_climb_known_values(self):
        # F/W = sqrt(2 r h - h^2) / (r - h): r = 0.3 m gives 1.118 (h 0.10), 1.732 (h 0.15), 5.92 (h 0.25)
        for h, ratio in ((100, 1.118), (150, 1.732), (250, 5.92)):
            out = self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(0.3, "m"), "h": q(h, "mm"), "W": q(1, "kN")})
            self.assertAlmostEqual(out["outputs"]["force_ratio"]["value"], ratio, delta=ratio * 1e-3)
            self.assertAlmostEqual(out["outputs"]["wheel_force"]["value"], 1000 * out["outputs"]["force_ratio"]["value"], places=9)
        out = self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(100, "mm"), "W": q(1000, "N")})
        self.assertAlmostEqual(out["outputs"]["corner_distance"]["value"], math.sqrt(50000.0), places=9)
        self.assertAlmostEqual(out["outputs"]["self_climb_torque"]["value"], math.sqrt(50000.0), places=9)  # 1000 N * 0.2236 m

    def test_elasticities_from_sympy(self):
        out = self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(100, "mm"), "W": q(1000, "N")})
        e = {t["name"]: t["share"] for t in out["terms"]}
        # d ln F / d ln r = r h / (2 r h - h^2) - r / (r - h) = 0.6 - 1.5
        self.assertAlmostEqual(e["r"], -0.9, places=12)
        self.assertAlmostEqual(e["h"], 0.9, places=12)
        self.assertAlmostEqual(e["W"], 1.0, places=12)

    def test_step_as_high_as_the_wheel_is_refused(self):
        with self.assertRaisesRegex(ValueError, "below the wheel radius"):
            self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(300, "mm"), "W": q(1000, "N")})

    def test_wrong_dimension_names_input(self):
        with self.assertRaisesRegex(ValueError, "input 'W'"):
            self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(100, "mm"), "W": q(100, "kg")})

    def test_missing_and_unknown_inputs(self):
        with self.assertRaisesRegex(ValueError, "missing input"):
            self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(100, "mm")})
        with self.assertRaisesRegex(ValueError, "unknown input"):
            self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(100, "mm"), "W": q(1, "N"), "x": q(1, "mm")})
        with self.assertRaisesRegex(ValueError, "unknown formula"):
            self.R.evaluate("no.such", {})

    def test_static_wheel_loads(self):
        m, a, L, t, e = 600.0, 1000.0, 1700.0, 1100.0, 50.0
        out = self.R.evaluate("mass_props.static_wheel_loads",
                              {"m": q(m, "kg"), "a": q(a, "mm"), "L": q(L, "mm"), "t": q(t, "mm"), "e": q(e, "mm")})["outputs"]
        g = 9.80665
        self.assertAlmostEqual(out["axle_load_front"]["value"], m * g * (L - a) / L, places=9)
        total = sum(out[f"wheel_load_{w}"]["value"] for w in ("FL", "FR", "RL", "RR"))
        self.assertAlmostEqual(total, m * g, places=9)
        self.assertGreater(out["wheel_load_FL"]["value"], out["wheel_load_FR"]["value"])  # CG to the left
        with self.assertRaisesRegex(ValueError, "between the axles"):
            self.R.evaluate("mass_props.static_wheel_loads",
                            {"m": q(m, "kg"), "a": q(2000, "mm"), "L": q(L, "mm"), "t": q(t, "mm"), "e": q(0, "mm")})

    def test_beam_formulas_against_hand_numbers(self):
        # 40 x 80 x 2 tube: I = (40*80^3 - 36*76^3) / 12 = (20,480,000 - 15,803,136) / 12 = 389,738.67 mm^4
        I = (40 * 80**3 - 36 * 76**3) / 12
        self.assertAlmostEqual(I, 4676864 / 12, places=6)
        sec = {"b": q(40, "mm"), "h": q(80, "mm"), "t": q(2, "mm")}
        out = self.R.evaluate("frame_static.cantilever_tip_load", {"P": q(1000, "N"), "L": q(1, "m"), "E": q(205000, "MPa"), **sec})["outputs"]
        self.assertAlmostEqual(out["max_displacement"]["value"], 1000 * 1000**3 / (3 * 205000 * I), places=9)
        self.assertAlmostEqual(out["max_stress"]["value"], 1000 * 1000 * 40 / I, places=9)
        out = self.R.evaluate("frame_static.simple_beam_center_load", {"P": q(1000, "N"), "L": q(2000, "mm"), "E": q(205000, "MPa"), **sec})["outputs"]
        self.assertAlmostEqual(out["max_displacement"]["value"], 1000 * 2000**3 / (48 * 205000 * I), places=9)
        # point load at mid-span equals the centre-load formula
        mid = self.R.evaluate("frame_static.simple_beam_point_load", {"P": q(1000, "N"), "L": q(2000, "mm"), "a": q(1000, "mm"), "E": q(205000, "MPa"), **sec})["outputs"]
        self.assertAlmostEqual(mid["max_displacement"]["value"], out["max_displacement"]["value"], places=9)
        self.assertAlmostEqual(mid["max_stress"]["value"], out["max_stress"]["value"], places=9)
        udl = self.R.evaluate("frame_static.simple_beam_udl", {"w": q(1, "N/mm"), "L": q(2000, "mm"), "E": q(205000, "MPa"), **sec})["outputs"]
        self.assertAlmostEqual(udl["max_displacement"]["value"], 5 * 2000**4 / (384 * 205000 * I), places=9)
        with self.assertRaisesRegex(ValueError, "wall thickness"):
            self.R.evaluate("frame_static.bending_stress", {"M": q(1, "N*mm"), "b": q(40, "mm"), "h": q(80, "mm"), "t": q(25, "mm")})


if __name__ == "__main__":
    unittest.main()

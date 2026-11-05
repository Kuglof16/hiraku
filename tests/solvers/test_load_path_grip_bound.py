"""load_path.grip_bound: the members-only frame's flexibility (frame_inputs.members_flexibility) against a cantilever's
closed forms, the formula's sum, and the bound at or above the load_path tool's grip_deflection on generated designs
(the complementary energy theorem: it must never be below)."""

import unittest

import numpy as np

from hiraku.catalog.builders import BUILDERS
from hiraku.catalog.builders.load_path import PLANS, grip_bound_inputs, pedal_bound_inputs
from hiraku.designgen import ladder_4w as GEN
from hiraku.solvers import formula_registry
from hiraku.solvers.k0_handcalc import sections
from hiraku.solvers.k0_handcalc.frame_inputs import members_flexibility
from hiraku.solvers.shelf import LoadPath
from tests.solvers.test_builders_load_path import REQ, SPECS, results

REG = formula_registry()
RECT = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.0}}
E = 205000.0
G = E / 2.6


def cantilever(L=1000.0):
    return {"design_version": 1, "nodes": [{"id": "A", "xyz_mm": [0.0, 0.0, 0.0]}, {"id": "B", "xyz_mm": [L, 0.0, 0.0]}],
            "members": [{"id": "m", "node_a": "A", "node_b": "B", "section": "s", "material": "steel"}],
            "sections": {"s": RECT}, "materials": {"steel": {"E_MPa": E, "nu": 0.3, "density_kg_m3": 0.0,
                                                             "yield_MPa": 245.0}},
            "supports": [], "parts": []}


class MembersFlexibilityTest(unittest.TestCase):
    def test_cantilever_closed_forms(self):
        s = sections.properties(RECT)
        for t, a in ((1.0, 1000.0), (0.5, 500.0)):
            C, x = members_flexibility(cantilever(), "m", t, {"A": "all"})
            np.testing.assert_allclose(x, [a, 0.0, 0.0])
            self.assertAlmostEqual(C[2, 2] / (a ** 3 / (3 * E * s["Iz"])), 1.0, places=9)  # up-down: local y is up
            self.assertAlmostEqual(C[1, 1] / (a ** 3 / (3 * E * s["Iy"])), 1.0, places=9)
            self.assertAlmostEqual(C[0, 0] / (a / (E * s["A"])), 1.0, places=9)
            self.assertAlmostEqual(C[3, 3] / (a / (G * s["J"])), 1.0, places=9)
            np.testing.assert_allclose(C, C.T, rtol=0, atol=1e-15)

    def test_free_frame_refused(self):
        from hiraku.solvers.k0_handcalc.vehicle import DesignError

        with self.assertRaises(DesignError):
            members_flexibility(cantilever(), "m", 1.0, {"A": "z"})


class FormulaTest(unittest.TestCase):
    def test_sum(self):
        out = REG.evaluate("load_path.grip_bound", {"F": {"value": 300.0, "unit": "N"},
                                                    "k_e": {"value": 50.0, "unit": "N/mm"},
                                                    "k_tr": {"value": 20.0, "unit": "N/mm"}})["outputs"]
        self.assertAlmostEqual(out["delta"]["value"], 300.0 / np.sqrt(1000.0), places=12)
        self.assertAlmostEqual(out["delta_along"]["value"], 6.0, places=12)


class AboveTheToolTest(unittest.TestCase):
    """The bound is at or above the tool's grip_deflection (the largest of its three hand directions)."""

    def test_generated_designs(self):
        tool = LoadPath()
        for values in ({}, GEN.apply_choices({"part_mounts": "brackets", "pedal_mount": "post",
                                               "side_guard": "bars_net"}, {})):
            d = GEN.build(values)
            lc = next(c for c in BUILDERS["R7"](SPECS["R7"], REQ, d) if c["load_case_id"] == "R7-hand-300N")
            got = results(tool.run(d, lc, [{"prediction_id": "pred-0001"}], {}))["grip_deflection"]["value"]
            bound = REG.evaluate("load_path.grip_bound", grip_bound_inputs(d, lc))["outputs"]["delta"]["value"]
            self.assertGreaterEqual(bound, got, values)

    def test_pedal_on_generated_designs(self):
        """The pedal's bound (pedal_bound_inputs) at or above the tool's pedal_deflection: as generated, and with the
        pedals on the post's pedal beam (where control_deflection's cantilever falls short of the tool)."""
        tool = LoadPath()
        for values in ({}, GEN.apply_choices({"part_mounts": "brackets", "pedal_mount": "post",
                                               "side_guard": "bars_net"}, {})):
            d = GEN.build(values)
            lc = next(c for c in BUILDERS["R7"](SPECS["R7"], REQ, d) if c["load_case_id"] == "R7-foot-500N")
            got = results(tool.run(d, lc, [{"prediction_id": "pred-0001"}], {}))["pedal_deflection"]["value"]
            bound = REG.evaluate("load_path.grip_bound", pedal_bound_inputs(d, lc))["outputs"]["delta"]["value"]
            self.assertGreaterEqual(bound, got, values)

    def test_plan_judges_on_the_bound(self):
        step = PLANS["R7"]
        for quantity in ("grip_deflection", "pedal_deflection"):
            pred = next(p for p in step["predictions"] if p["quantity"] == quantity)
            self.assertEqual(pred["formula_id"], "load_path.grip_bound")
            entry = next(e for e in step["expected_mismatch"] if e["quantity"] == quantity)
            self.assertNotIn("end", entry)
            self.assertNotIn("ends_case", entry)
            if quantity == "grip_deflection":  # the bound is loose: the path checked, the tool's value decides
                self.assertTrue(entry["tool_decides"])
                self.assertTrue(entry["tool_decides_reason"])
                self.assertNotIn("safe_side", entry)
            else:  # the pedal passes on its bound
                self.assertTrue(entry["safe_side"])
                self.assertNotIn("tool_decides", entry)
        # the path check of R7-hand: load_path.free_parts in the same step
        self.assertIn("load_path.free_parts", [p["formula_id"] for p in step["predictions"]])


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.solvers.k5_sensitivity import get_value, sensitivity, strongest, with_value
from hiraku.solvers.shelf import fixtures

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))


def _one_steel():
    """The demo vehicle without the roll bar (its tubes are another steel): one material, so scaling its E
    scales every stiffness alike."""
    from hiraku.designgen import ladder_4w
    return ladder_4w.build({"rollbar": {"value": 0.0, "unit": "1"}})
TOOLS = {t.name: t for t in solvers.builtin_tools()}
PRED = [{"prediction_id": "pred-0001"}]


def q(v, u):
    return {"value": v, "unit": u}


class PathTest(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(get_value(DESIGN, "sections.rt40x80x2.3.dims_mm.t"), (2.3, "mm"))
        self.assertEqual(get_value(DESIGN, "variables.motor_peak_power"), (10.0, "kW"))
        self.assertEqual(get_value(DESIGN, "parts.cargo.mass_kg"), (150.0, "kg"))
        # the cargo stands on the deck, a 3.2 mm sheet since 318ea65 (the floors' bottoms stay on the rails, so
        # what stands on them is 16.8 mm lower than with the thicker boxes before)
        self.assertEqual(get_value(DESIGN, "parts.cargo.box.center_mm.2")[0], 583.2)
        d2 = with_value(DESIGN, "parts.cargo.mass_kg", 100.0)
        self.assertEqual(get_value(d2, "parts.cargo.mass_kg")[0], 100.0)
        self.assertEqual(get_value(DESIGN, "parts.cargo.mass_kg")[0], 150.0)  # the original is untouched
        with self.assertRaises(KeyError):
            get_value(DESIGN, "parts.ghost.mass_kg")


class SensitivityTest(unittest.TestCase):
    def test_cantilever_known_elasticities(self):
        """Tip load P on a cantilever: deflection ~ P L^3 / (E I): elasticities 1 (P... through the load), -1 for E."""
        lad = fixtures.ladder_design(payload_kg=200.0)
        lc = {"load_case_id": "R1", "test_id": "R1", "domain": "strength", "title": "x", "params": {}, "tolerance_rel": 0.15}
        t = sensitivity(TOOLS["frame_static"], lad, lc, "max_displacement",
                        ["materials.st.E_MPa", "parts.payload.mass_kg"], rel_step=0.01)
        e = {x["name"].split(" to ")[1]: x.get("share") for x in t}
        self.assertAlmostEqual(e["materials.st.E_MPa"], -1.0, delta=1e-3)  # deflection ~ 1/E
        self.assertGreater(e["parts.payload.mass_kg"], 0.5)  # most of the load is the payload

    def test_through_run_params(self):
        lc = {"load_case_id": "R1", "test_id": "R1", "domain": "strength", "title": "x",
              "params": {"step_height": q(100, "mm")}, "tolerance_rel": 0.15}
        rec = TOOLS["frame_static"].run(_one_steel(), lc, PRED, {"sensitivity": {"quantity": "max_stress", "paths": [
            "sections.rt40x80x2.3.dims_mm.h", "materials.stkr400.E_MPa"]}})
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        sens = [x for x in rec["effects"] if x["name"].startswith("max_stress to")]
        self.assertEqual(len(sens), 2)
        self.assertLess(sens[0]["share"], -1.0)  # taller rail: less bending stress
        self.assertEqual(sens[1]["share"], 0.0)  # stiffness scale does not change these stresses
        self.assertIs(strongest(sens), sens[0])

    def test_a_variable_that_does_not_move_is_not_a_zero(self):
        """wheelbase builds the frame, but with_value changes only its value: frame_static's result stays, and
        K5 says it did not look (no share) instead of a sensitivity of 0 (a number outside the variables that does
        not move still gives 0: test_through_run_params)."""
        lc = {"load_case_id": "R1", "test_id": "R1", "domain": "strength", "title": "x",
              "params": {"step_height": q(100, "mm")}, "tolerance_rel": 0.15}
        t = sensitivity(TOOLS["frame_static"], DESIGN, lc, "max_stress", ["variables.wheelbase"])
        self.assertNotIn("share", t[0])
        self.assertIn("not looked at", t[0]["name"])
        self.assertTrue(t[0]["name"].startswith("max_stress to variables.wheelbase ("))
        self.assertIsNone(strongest(t))

    def test_a_rebuilt_shape_variable_moves_the_frame(self):
        """With the caller's rebuild (designgen.regenerate), a taller rail lowers frame_static's bending stress."""
        from hiraku import designgen
        lc = {"load_case_id": "R1", "test_id": "R1", "domain": "strength", "title": "x",
              "params": {"step_height": q(100, "mm")}, "tolerance_rel": 0.15}
        t = sensitivity(TOOLS["frame_static"], DESIGN, lc, "max_stress", ["variables.rail_h"],
                        rebuild=designgen.regenerate)
        self.assertEqual(t[0]["name"], "max_stress to variables.rail_h")
        self.assertLess(t[0]["share"], -0.5)


class _Stub:
    """A tool whose result is a * b, a from the variable a (read at run time) and b from parts.box.b (what the
    rebuild makes from the variable b)."""
    name, stage = "stub", 1

    def run(self, design, load_case, predictions, params):
        y = design["variables"]["a"]["value"] * design["parts"][0]["b"]
        return {"status": "ok", "results": [{"quantity": "y", "value": y, "unit": "1"}]}


def _stub_design(a=2.0, b=3.0):
    return {"variables": {"a": q(a, "1"), "b": q(b, "1")}, "parts": [{"id": "box", "b": b}]}


def _stub_rebuild(design, changes, top=None):
    if any(k != "b" for k in changes):
        raise ValueError("a cannot be changed by revising the design")
    b = changes.get("b", design["variables"]["b"])["value"]
    if top is not None and b > top:
        raise ValueError(f"b = {b:g} is outside 0 to {top:g}")
    return _stub_design(design["variables"]["a"]["value"], b)


class RebuildTest(unittest.TestCase):
    LC = {"load_case_id": "X", "params": {}}

    def terms(self, tool=None, **kw):
        return {t["name"].split(" to ")[1].split(" (")[0]: t for t in sensitivity(
            tool or _Stub(), _stub_design(), self.LC, "y", ["variables.a", "variables.b"], **kw)}

    def test_without_rebuild_the_built_variable_is_not_looked_at(self):
        t = self.terms()
        self.assertAlmostEqual(t["variables.a"]["share"], 1.0, places=9)  # read at run time: moved by value
        self.assertNotIn("share", t["variables.b"])
        self.assertIn("not looked at", t["variables.b"]["name"])

    def test_with_rebuild_the_built_variable_moves(self):
        t = self.terms(rebuild=_stub_rebuild)
        self.assertAlmostEqual(t["variables.a"]["share"], 1.0, places=9)  # the rebuild refuses it: by value
        self.assertAlmostEqual(t["variables.b"]["share"], 1.0, places=9)
        self.assertEqual(t["variables.b"]["name"], "y to variables.b")

    def test_a_rebuilt_design_is_reused_for_the_next_load_case(self):
        calls = []

        def counted(d, c):
            calls.append(dict(c))
            return _stub_rebuild(d, c)
        self.terms(rebuild=counted)
        first = len(calls)  # b at x0 (the check), the base, b + h, b - h; a refused once
        self.terms(rebuild=counted)  # a second load case on the same design
        self.assertEqual(len(calls), first)
        self.assertEqual(first, 5)

    def test_one_side_at_a_range_end(self):
        t = self.terms(rebuild=lambda d, c: _stub_rebuild(d, c, top=3.0))
        self.assertAlmostEqual(t["variables.b"]["share"], 1.0, places=9)
        self.assertIn("one side only", t["variables.b"]["name"])

    def test_a_heavy_tool_is_not_rebuilt_unless_asked(self):
        heavy = _Stub()
        heavy.stage = 3
        t = self.terms(heavy, rebuild=_stub_rebuild)
        self.assertNotIn("share", t["variables.b"])
        self.assertIn("heavy", t["variables.b"]["name"])
        self.assertAlmostEqual(t["variables.a"]["share"], 1.0, places=9)  # by value as before
        t = self.terms(heavy, rebuild=_stub_rebuild, heavy=True)
        self.assertAlmostEqual(t["variables.b"]["share"], 1.0, places=9)


class SensitivityTailTest(unittest.TestCase):
    def test_bad_request_is_reported(self):
        lc = {"load_case_id": "R1", "test_id": "R1", "domain": "strength", "title": "x", "params": {}, "tolerance_rel": 0.15}
        rec = TOOLS["frame_static"].run(DESIGN, lc, PRED, {"sensitivity": {"quantity": "nothing", "paths": ["parts.cargo.mass_kg"]}})
        self.assertEqual(rec["status"], "ok")
        self.assertIn("not computed", rec["effects"][-1]["name"])


if __name__ == "__main__":
    unittest.main()

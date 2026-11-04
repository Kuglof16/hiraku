"""H6 spray: k4_geometry.ray_box, the spray_reach tool, the K0 formulas spray.uncovered_arc and
spray.reaching_lines, and the H6 builder and plan, on the demo's version 1 and with fenders: wheel."""

from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, solvers
from hiraku.catalog import load_requirements
from hiraku.catalog.builders import BUILDERS, PLANS, BuildError
from hiraku.designgen import ladder_4w as GEN
from hiraku.solvers.k0_handcalc.spray_inputs import arc_bins, tread_lines
from hiraku.solvers.k4_geometry import from_box, ray_box
from hiraku.solvers.shelf import SprayReach

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
REQ = load_requirements(ROOT / "data" / "requirements" / "offroad-4w-n12.yaml")
SPEC = {"id": "H6", "domain": "assembly", "title": "泥のはね", "applies_if": "ride and outdoor and self_propelled",
        "requirement_keys": ["Q31", "Q50", "Q87", "Q88"]}
FORMULAS = solvers.formula_registry()
PRED = [{"prediction_id": "pred-0001"}]


def predict(design, lc) -> dict:
    """PLANS["H6"]'s predictions as the scripted run makes them (lc.<param>, from.<name>, then the formula)."""
    out = {}
    for p in PLANS["H6"]["predictions"]:
        given = p["inputs_from"](design, lc)
        inputs = {k: lc["params"][ref[3:]] if ref.startswith("lc.") else given[ref.split(".", 1)[1]]
                  for k, ref in p["inputs"].items()}
        out[p["quantity"]] = FORMULAS.evaluate(p["formula_id"], inputs)["outputs"][p["output"]]["value"]
    return out


def results(rec) -> dict:
    return {r["quantity"]: r for r in rec["results"]}


class RayBoxTest(unittest.TestCase):
    def test_enters_and_leaves(self):
        self.assertEqual(ray_box([-2000, 0, 0], [1, 0, 0], from_box([0, 0, 0], [1000, 1000, 1000])), (1500.0, 2500.0))

    def test_starts_inside(self):
        t_in, t_out = ray_box([0, 0, 0], [0, 0, 1], from_box([0, 0, 0], [1000, 1000, 1000]))
        self.assertAlmostEqual(t_in, -500.0)
        self.assertAlmostEqual(t_out, 500.0)

    def test_parallel_outside_misses(self):
        self.assertIsNone(ray_box([-2000, 0, 501], [1, 0, 0], from_box([0, 0, 0], [1000, 1000, 1000])))

    def test_diagonal_into_a_turned_box(self):
        d = np.array([1.0, 1.0, 0.0]) / math.sqrt(2)
        box = from_box([1000, 1000, 0], [200, 200, 200], [0, 0, 45])  # a face square to the line
        self.assertAlmostEqual(ray_box([0, 0, 0], d, box)[0], 1000 * math.sqrt(2) - 100.0)


class ToolTest(unittest.TestCase):
    def test_known_answers_and_spec(self):
        t = SprayReach()
        checks = t.check()
        self.assertTrue(all(c["pass"] for c in checks), [c for c in checks if not c["pass"]])
        self.assertEqual(contract.errors("tool_spec", t.spec()), [])
        self.assertTrue(t.spec()["usable"])
        self.assertIn("spray_reach", [x.name for x in solvers.builtin_tools()])

    def test_tread_lines(self):
        mids, h = arc_bins(30.0, 50.0, 5.0)
        self.assertEqual(len(mids), 16)
        self.assertAlmostEqual(h, 5.0)
        lines = tread_lines(V1, 30.0, 50.0, 5.0, 5)
        self.assertEqual(len(lines), 4 * 16 * 5)
        for ln in lines[:20]:
            s = next(s for s in V1["supports"] if s["id"] == ln.support)
            r = ln.origin - np.asarray(s["position_mm"], float)
            self.assertAlmostEqual(math.hypot(r[0], r[2]), s["radius_mm"])  # on the tread
            self.assertAlmostEqual(r[0] * ln.direction[0] + r[2] * ln.direction[2], 0.0)  # along the tangent
        top = [ln for ln in lines if abs(ln.theta_deg - 2.5) < 1e-9][0]
        self.assertGreater(top.direction[0], 0.99)  # near the top the tread moves forward (+x)

    def test_bins_not_a_multiple_of_the_step(self):
        mids, h = arc_bins(30.0, 52.0, 5.0)
        self.assertEqual(len(mids), 17)
        self.assertAlmostEqual(h, 82.0 / 17)
        self.assertAlmostEqual(mids[0], -30.0 + h / 2)
        self.assertAlmostEqual(mids[-1], 52.0 - h / 2)


class FormulaTest(unittest.TestCase):
    def arc(self, f, r, g, a_f=30.0, a_r=50.0, R=300.0):
        q = lambda v, u: {"value": v, "unit": u}  # noqa: E731
        return {k: v["value"] for k, v in FORMULAS.evaluate("spray.uncovered_arc", {
            "a_f": q(a_f, "deg"), "a_r": q(a_r, "deg"), "f": q(f, "deg"), "r": q(r, "deg"), "R": q(R, "mm"),
            "g": q(g, "mm")})["outputs"].items()}

    def test_no_fender_leaves_the_whole_arc(self):
        self.assertEqual(self.arc(0.0, 0.0, 0.0)["arc"], 80.0)
        self.assertEqual(self.arc(0.0, 0.0, 40.0)["arc"], 80.0)
        self.assertEqual(self.arc(0.0, 0.0, 0.0)["arc_radial"], 80.0)

    def test_radial_form(self):
        self.assertEqual(self.arc(20.0, 40.0, 40.0)["arc_radial"], 10.0 + 10.0)
        self.assertEqual(self.arc(40.0, 60.0, 40.0)["arc_radial"], 0.0)

    def test_tangent_lines_need_the_gap_angle_ahead(self):
        got = self.arc(30.0, 50.0, 40.0)
        alpha = math.degrees(math.acos(300.0 / 340.0))
        self.assertAlmostEqual(got["alpha"], alpha)
        self.assertAlmostEqual(got["arc"], alpha)  # the points from -30 to -30 + alpha throw past the front end
        self.assertEqual(self.arc(30.0, 50.0, 0.0)["arc"], 0.0)  # with no gap the radial and tangent forms agree
        self.assertAlmostEqual(self.arc(30.0 + alpha, 50.0, 40.0)["arc"], 0.0)

    def test_reaching_lines(self):
        q = lambda v: {"value": v, "unit": "1"}  # noqa: E731
        self.assertEqual(FORMULAS.evaluate("spray.reaching_lines", {"n_aim": q(37), "n_cover": q(12)})["outputs"]["n"]["value"], 25)
        with self.assertRaises(ValueError):
            FORMULAS.evaluate("spray.reaching_lines", {"n_aim": q(3), "n_cover": q(4)})


class BuilderTest(unittest.TestCase):
    def test_case_fits_the_contract_and_says_the_rule_is_borrowed(self):
        lc = BUILDERS["H6"](SPEC, REQ, V1)
        contract.validate("load_case", lc)
        SprayReach().declared(lc)
        self.assertEqual(lc["criterion"]["quantity"], "spray_reaching_occupant")
        self.assertEqual(lc["criterion"]["op"], "<=")
        self.assertEqual(lc["criterion"]["limit"], {"value": 0.0, "unit": "1"})
        self.assertEqual(lc["params"]["arc_front"], {"value": 30.0, "unit": "deg"})
        self.assertEqual(lc["params"]["arc_rear"], {"value": 50.0, "unit": "deg"})
        basis = lc["criterion"]["basis"]
        for word in ("第178条", "public-road rule for other traffic", "borrowed", "assumption"):
            self.assertIn(word, basis)
        self.assertIn("Q87", lc["requirement_keys"])
        self.assertIn("Q88", lc["requirement_keys"])
        for p in PLANS["H6"]["predictions"]:
            self.assertTrue(any("公道" in a for a in p["assumptions"]))

    def test_not_evaluated_without_the_keys(self):
        req = copy.deepcopy(REQ)
        req["requirements"] = [r for r in req["requirements"] if r["key"] != "Q88"]
        with self.assertRaises(BuildError) as e:
            BUILDERS["H6"](SPEC, req, V1)
        self.assertIn("評価できない", str(e.exception))
        self.assertIn("Q88", str(e.exception))

    def test_plan_predicts_the_criterion(self):
        quantities = [p["quantity"] for p in PLANS["H6"]["predictions"]]
        self.assertEqual(quantities, ["spray_uncovered_arc", "spray_reaching_occupant"])
        names = {p["quantity"] for p in SprayReach().describe()["predicts"]}
        self.assertLessEqual(set(quantities), names)
        self.assertEqual(PLANS["H6"]["tool"], "spray_reach")


class DesignsTest(unittest.TestCase):
    """K0 against the tool on version 1 (no fenders) and on fenders: wheel (FN-19's 30 and 50 deg, gap 40 mm)."""

    @classmethod
    def setUpClass(cls):
        cls.fendered = GEN.build({"fenders": 1})
        cls.out = {}
        for name, d in (("v1", V1), ("fenders", cls.fendered)):
            lc = BUILDERS["H6"](SPEC, REQ, d)
            rec = SprayReach().run(d, lc, PRED, {})
            cls.out[name] = (lc, rec, predict(d, lc))

    def test_version_1_spray_reaches_the_occupants(self):
        lc, rec, pred = self.out["v1"]
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        got = results(rec)
        self.assertGreater(got["spray_reaching_occupant"]["value"], 0)
        self.assertIn("occupant", got["spray_reaching_occupant"]["where"])
        self.assertEqual(pred["spray_uncovered_arc"], 80.0)
        self.assertEqual(got["spray_uncovered_arc"]["value"], 80.0)
        # K0 has fewer things in the way, so it never counts fewer lines; within the case's tolerance
        self.assertGreaterEqual(pred["spray_reaching_occupant"], got["spray_reaching_occupant"]["value"])
        self.assertLessEqual(pred["spray_reaching_occupant"] - got["spray_reaching_occupant"]["value"],
                             lc["tolerance_rel"] * pred["spray_reaching_occupant"])

    def test_fenders_keep_the_spray_off_the_occupants(self):
        lc, rec, pred = self.out["fenders"]
        got = results(rec)
        self.assertEqual(got["spray_reaching_occupant"]["value"], 0.0)
        self.assertEqual(pred["spray_reaching_occupant"], 0.0)
        # the arc the tangent lines leave uncovered ahead of the fender: K0 alpha = acos(300 / 340), the tool in bins
        self.assertAlmostEqual(pred["spray_uncovered_arc"], math.degrees(math.acos(300.0 / 340.0)))
        _, width = arc_bins(30.0, 50.0, 5.0)
        self.assertLessEqual(abs(got["spray_uncovered_arc"]["value"] - pred["spray_uncovered_arc"]), width)

    def test_records_fit_the_contract(self):
        for name, (lc, rec, _) in self.out.items():
            with self.subTest(design=name):
                full = dict(rec, analysis_id="ana-0001")
                self.assertEqual(contract.errors("analysis", full), [])
                self.assertTrue(any("公道" in a for a in rec["assumptions"]))


if __name__ == "__main__":
    unittest.main()

import copy
import json
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.catalog import load_catalog, load_requirements
from hiraku.catalog.builders import BUILDERS, PLANS, BuildError
from hiraku.tools.summary import design_summary

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
CATALOG = load_catalog()
OURS = ("C1", "N1", "R3", "P4", "T1", "T2", "H1", "H3", "H5")
PRED = [{"prediction_id": "pred-0001"}]


def _req(key, name, value, unit=None):
    r = {"key": key, "name": name, "value": value, "source": "default", "trust": 3, "basis": "assumption (test)"}
    if unit:
        r["unit"] = unit
    return r


def requirements():
    """The demo requirements plus the keys these tests need that the file may not have yet."""
    req = copy.deepcopy(load_requirements(ROOT / "data" / "requirements" / "offroad-4w.yaml"))
    have = {r["key"] for r in req["requirements"]}
    for r in (_req("Q12", "路面の粗さ", "C"), _req("Q13", "縦の坂", 12, "%"), _req("Q19", "気温", 40, "degC"),
              _req("Q54", "航続", 20, "km"), _req("Q65", "衝突の速さ", 40, "km/h"), _req("Q90", "悪路の走行距離", 5000, "km")):
        if r["key"] not in have:
            req["requirements"].append(r)
    contract.validate("requirements_file", req)
    return req


REQ = requirements()
TOOLS = {t.name: t for t in solvers.builtin_tools()}
FORMULAS = solvers.formula_registry()


def tool_for(test_id):
    return next(t for t in TOOLS.values() if test_id in t.tests and t.__class__.__module__.startswith("hiraku.solvers.shelf"))


def resolve(ref, design, lc, extra, preds=None):
    if isinstance(ref, dict):  # a quantity written in the plan
        return ref
    head, _, rest = ref.partition(".")
    if head == "pred":
        return preds[rest]
    if head == "lc":
        return lc["params"][rest]
    if head == "from":
        return extra[rest]
    if head == "design":
        kind, name = rest.split(".", 1)
        assert kind == "var", ref
        return design["variables"][name]
    if head == "load":
        node = design_summary(design)["loadings"][lc["params"].get("loading", "two_cargo")]
        for part in rest.split("."):
            node = node[part]
        return node
    if head == "summary":
        return design_summary(design)[rest]
    raise AssertionError(f"unknown ref {ref}")


LOOP = {"grade_low": {"value": 7, "unit": "%"}, "speed_low": {"value": 20, "unit": "km/h"}}  # the forest loop's other end


def spec(tid):
    """The catalog entry, with the loop params P4, T1 and T2 are to carry there."""
    s = CATALOG.test(tid)
    return dict(s, params={**s.get("params", {}), **LOOP}) if tid in ("P4", "T1", "T2") else s


class BuildersTest(unittest.TestCase):
    runs: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.cases = {tid: BUILDERS[tid](spec(tid), REQ, DESIGN) for tid in OURS}
        cls.runs = {tid: tool_for(tid).run(DESIGN, lc, PRED, {}) for tid, lc in cls.cases.items()}

    def test_every_test_has_a_builder(self):
        for tid in OURS:
            self.assertIn(tid, BUILDERS)
            self.assertIn(tid, {t["id"] for t in CATALOG.tests})

    def test_load_cases_fit_the_contract_and_run(self):
        for tid, lc in self.cases.items():
            contract.validate("load_case", lc)
            rec = self.runs[tid]
            self.assertEqual(rec["status"], "ok", f"{tid}: {rec.get('error')}")
            if "criterion" in lc:
                self.assertIn(lc["criterion"]["quantity"], {r["quantity"] for r in rec["results"]}, tid)

    def test_values_come_from_the_requirements(self):
        p = self.cases["P4"]["params"]
        self.assertEqual(p["distance"], {"value": 20.0, "unit": "km"})
        self.assertEqual((p["grade_high"]["value"], p["grade_low"]["value"]), (12.0, 7.0))
        self.assertEqual((p["speed_high"]["value"], p["speed_low"]["value"]), (40.0, 20.0))
        no_params = dict(CATALOG.test("P4"), params={})  # without the catalog's loop params: Q13, half of it and of Q50
        q13 = next(r["value"] for r in REQ["requirements"] if r["key"] == "Q13")
        bare = BUILDERS["P4"](no_params, REQ, DESIGN)["params"]
        self.assertEqual((bare["grade_high"]["value"], bare["grade_low"]["value"], bare["speed_low"]["value"]),
                         (q13, q13 / 2, 20.0))
        # Q13 is the steepest grade to stop and start on; the grade driven up for long is the catalog's grade_high
        steep = dict(REQ, requirements=[dict(r, value=14) if r["key"] == "Q13" else r for r in REQ["requirements"]])
        given = dict(CATALOG.test("P4"), params={**LOOP, "grade_high": {"value": 12, "unit": "%"}})
        lc = BUILDERS["P4"](given, steep, DESIGN)
        self.assertEqual((lc["params"]["grade_high"]["value"], lc["params"]["grade_low"]["value"]), (12.0, 7.0))
        self.assertNotIn("Q13", lc["requirement_keys"])
        self.assertEqual(self.cases["T1"]["criterion"]["limit"], {"value": 55.0, "unit": "degC"})
        self.assertEqual(self.cases["T2"]["criterion"]["limit"], {"value": 85.0, "unit": "degC"})
        self.assertEqual(self.cases["R3"]["params"]["road_class"], "C")
        self.assertEqual(self.cases["T1"]["params"]["ambient_temperature"], {"value": 40.0, "unit": "degC"})
        self.assertEqual(self.cases["H1"]["criterion"]["limit"], DESIGN["variables"]["max_steer_angle"])

    def test_known_defects_fail_their_criteria(self):
        r = {x["quantity"]: x["value"] for x in self.runs["H1"]["results"]}
        self.assertLess(r["free_steer_angle"], self.cases["H1"]["criterion"]["limit"]["value"])
        r = {x["quantity"]: x["value"] for x in self.runs["H3"]["results"]}
        self.assertLess(r["knee_room"], 0.0)

    def test_plans_predict_within_the_tolerance(self):
        for tid, plan in PLANS.items():
            if tid not in OURS:
                continue
            lc = self.cases[tid]
            self.assertEqual(plan["tool"], tool_for(tid).name)
            got = {r["quantity"]: r for r in self.runs[tid]["results"]}
            preds = {}
            expected = plan.get("expected_mismatch", {}).get("quantity")
            for p in plan["predictions"]:
                extra = p["inputs_from"](DESIGN, lc) if "inputs_from" in p else {}
                refs = [ref for ref in p["inputs"].values() if isinstance(ref, str)]
                self.assertEqual(any(ref.startswith("from.") for ref in refs), "inputs_from" in p)
                inputs = {k: resolve(ref, DESIGN, lc, extra, preds) for k, ref in p["inputs"].items()}
                out = FORMULAS.evaluate(p["formula_id"], inputs)["outputs"][p["output"]]
                preds[p["quantity"]] = out
                self.assertIn(p["formula_id"], tool_for(tid).formulas)
                if p["quantity"] not in got:  # an intermediate hand value, not compared
                    continue
                ana = got[p["quantity"]]
                self.assertEqual(out["unit"], ana["unit"], f"{tid} {p['quantity']}")
                rel = (ana["value"] - out["value"]) / abs(out["value"])
                if p["quantity"] == expected:  # the plan says it misses, with its cause: it must really miss
                    self.assertGreater(abs(rel), lc["tolerance_rel"], f"{tid} {p['quantity']}: {rel:+.3f}")
                else:
                    self.assertLessEqual(abs(rel), lc["tolerance_rel"], f"{tid} {p['quantity']}: {rel:+.3f}")

    def test_r3_predicts_the_end_cross_member(self):
        """The hand calculation looks at the weld the fatigue tool finds worst: the overhang's end cross member."""
        from hiraku.solvers.k0_handcalc.frame_inputs import end_cross_member_inputs

        self.assertNotIn("expected_mismatch", PLANS["R3"])
        worst = next(r for r in self.runs["R3"]["results"] if r["quantity"] == "fatigue_damage")
        self.assertEqual(worst["where"], end_cross_member_inputs(DESIGN)["end_cross_member"])

    def test_r3_plan_on_version_2(self):
        """Wider track (the wheels further outboard of the rails): still within the tolerance."""
        from hiraku import designgen

        v2 = designgen.regenerate(DESIGN, {"track": {"value": 1220, "unit": "mm"}, "steering_x": {"value": -200, "unit": "mm"}})
        lc = BUILDERS["R3"](spec("R3"), REQ, v2)
        got = {r["quantity"]: r["value"] for r in tool_for("R3").run(v2, lc, PRED, {})["results"]}["fatigue_damage"]
        preds = {}
        for p in PLANS["R3"]["predictions"]:
            extra = p["inputs_from"](v2, lc)
            out = FORMULAS.evaluate(p["formula_id"], {k: resolve(ref, v2, lc, extra, preds) for k, ref in p["inputs"].items()})
            preds[p["quantity"]] = out["outputs"][p["output"]]
        hand = preds["fatigue_damage"]["value"]
        self.assertLessEqual(abs(got - hand) / hand, lc["tolerance_rel"], f"hand {hand:.4f}, tool {got:.4f}")

    def test_plans_predict_the_criterion(self):
        """The scripted run judges a load case from a prediction of its criterion's quantity."""
        for tid, lc in self.cases.items():
            if "criterion" not in lc:
                continue
            self.assertIn(tid, PLANS)
            self.assertIn(lc["criterion"]["quantity"], [p["quantity"] for p in PLANS[tid]["predictions"]], tid)

    def test_missing_requirement_is_named(self):
        req = copy.deepcopy(REQ)
        req["requirements"] = [r for r in req["requirements"] if r["key"] != "Q90"]
        with self.assertRaisesRegex(BuildError, "Q90"):
            BUILDERS["R3"](CATALOG.test("R3"), req, DESIGN)
        req = copy.deepcopy(REQ)
        req["requirements"] = [r for r in req["requirements"] if r["key"] != "Q65"]
        self.assertEqual(BUILDERS["C1"](CATALOG.test("C1"), req, DESIGN)["params"]["speed"]["value"], 40.0)  # Q50

    def test_catalog_tolerance_wins(self):
        spec = dict(CATALOG.test("C1"), tolerance_rel=0.07)
        self.assertEqual(BUILDERS["C1"](spec, REQ, DESIGN)["tolerance_rel"], 0.07)


if __name__ == "__main__":
    unittest.main()

import copy
import json
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.catalog import load_catalog, load_requirements
from hiraku.catalog.builders import BUILDERS, PLANS, BuildError, calc
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


def resolve(ref, design, lc, extra):
    head, _, rest = ref.partition(".")
    if head == "lc":
        return lc["params"][rest]
    if head == "calc":
        return extra[rest]
    if head == "design":
        kind, name = rest.split(".", 1)
        assert kind == "var", ref
        return design["variables"][name]
    if head == "load":
        return design_summary(design)["loadings"][lc["params"].get("loading", "two_cargo")][rest]
    if head == "summary":
        return design_summary(design)[rest]
    raise AssertionError(f"unknown ref {ref}")


class BuildersTest(unittest.TestCase):
    runs: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.cases = {tid: BUILDERS[tid](CATALOG.test(tid), REQ, DESIGN) for tid in OURS}
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
        self.assertEqual((p["grade_high"]["value"], p["grade_low"]["value"]), (12.0, 6.0))
        self.assertEqual((p["speed_high"]["value"], p["speed_low"]["value"]), (40.0, 20.0))
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
            extra = calc(tid, DESIGN, lc)
            got = {r["quantity"]: r for r in self.runs[tid]["results"]}
            for p in plan["predictions"]:
                inputs = {k: resolve(ref, DESIGN, lc, extra) for k, ref in p["inputs"].items()}
                out = FORMULAS.evaluate(p["formula_id"], inputs)["outputs"][p["output"]]
                ana = got[p["quantity"]]
                self.assertEqual(out["unit"], ana["unit"], f"{tid} {p['quantity']}")
                rel = (ana["value"] - out["value"]) / abs(out["value"])
                self.assertLessEqual(abs(rel), lc["tolerance_rel"], f"{tid} {p['quantity']}: {rel:+.3f}")
                self.assertIn(p["formula_id"], tool_for(tid).formulas)

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

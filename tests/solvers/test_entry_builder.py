"""H7 (proposed) getting in and out: the H7 builder and plans, and the entry_opening tool against K0, on version 1, the version 2 option set (side_guard bars_net: the bars close the side) and with
side_opening (the side frame round point R, a door or a net on clips ahead of it, a grab bar)."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.catalog import load_requirements
from hiraku.catalog.builders import BUILDERS as ALL_BUILDERS
from hiraku.catalog.builders import BuildError
from hiraku.catalog.builders.entry import BUILDERS, PLANS
from hiraku.designgen import ladder_4w as GEN
from hiraku.solvers.shelf import EntryOpening

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
SPEC = {"id": "H7", "domain": "assembly", "title": "乗り降り", "applies_if": "ride and rough",
        "requirement_keys": ["Q47", "Q48", "Q49", "Q89", "Q93", "Q94", "Q95", "Q96", "Q97"]}
# the intake items' defaults (hiraku/intake/requirement_items.yaml of the intake department's branch; the
# requirements files here do not have them yet)
ENTRY_ITEMS = [("Q93", 450.0, "mm"), ("Q94", 300.0, "mm"), ("Q95", 985.2, "mm"), ("Q96", 1250.0, "mm"),
               ("Q97", 550.0, "mm"), ("Q47", "はい", None), ("Q48", 1600.0, "mm"), ("Q49", 16.0, "mm"),
               ("Q89", 38.0, "mm")]
FORMULAS = solvers.formula_registry()
PRED = [{"prediction_id": "pred-0001"}]
V2 = {"part_mounts": "brackets", "pedal_mount": "post", "side_guard": "bars_net"}
DOOR = {"part_mounts": "brackets", "pedal_mount": "post", "side_guard": "bars", "side_opening": "door",
        "grab_bar": "front_post"}
CLIP_NET = {"part_mounts": "brackets", "pedal_mount": "post", "side_guard": "bars_net", "side_opening": "clip_net",
            "grab_bar": "front_post"}


def requirements(drop=()) -> dict:
    req = load_requirements(ROOT / "data" / "requirements" / "offroad-4w-n12.yaml")
    req = copy.deepcopy(req)
    req["requirements"] = [r for r in req["requirements"] if r["key"] not in {k for k, _, _ in ENTRY_ITEMS}]
    for k, v, u in ENTRY_ITEMS:
        if k not in drop:
            req["requirements"].append({"key": k, "name": k, "value": v, "source": "default", "trust": 3,
                                        **({"unit": u} if u else {})})
    return req


REQ = requirements()


def predict(design, lc) -> dict:
    """The case's plan (its own, else H7's) as the scripted run makes it."""
    plan = PLANS.get(lc["load_case_id"]) or PLANS["H7"]
    out = {}
    for p in plan["predictions"]:
        given = p["inputs_from"](design, lc)
        inputs = {k: lc["params"][ref[3:]] if ref.startswith("lc.") else given[ref.split(".", 1)[1]]
                  for k, ref in p["inputs"].items()}
        out[p["quantity"]] = FORMULAS.evaluate(p["formula_id"], inputs)["outputs"][p["output"]]["value"]
    return out


def results(rec) -> dict:
    return {r["quantity"]: r for r in rec["results"]}


class BuilderTest(unittest.TestCase):
    def test_cases_fit_the_contract(self):
        cases = BUILDERS["H7"](SPEC, REQ, V1)
        self.assertEqual([lc["load_case_id"] for lc in cases], ["H7-opening", "H7-step", "H7-handhold"])
        for lc in cases:
            contract.validate("load_case", lc)
            EntryOpening().declared(lc)
            self.assertIn("borrowed", lc["criterion"]["basis"])
        opening, step, hand = cases
        self.assertEqual(opening["params"]["openable"], "yes")
        self.assertEqual(opening["criterion"]["limit"], {"value": 0.0, "unit": "1"})
        self.assertEqual(step["criterion"]["limit"], {"value": 550.0, "unit": "mm"})
        self.assertEqual((hand["criterion"]["op"], hand["criterion"]["limit"]["value"]), (">=", 1.0))
        self.assertIn("Q47", opening["requirement_keys"])
        self.assertIs(ALL_BUILDERS["H7"], BUILDERS["H7"])  # joined to the package as H6 (analysis.py)

    def test_not_evaluated_without_the_keys(self):
        with self.assertRaises(BuildError) as e:
            BUILDERS["H7"](SPEC, requirements(drop=[k for k, _, _ in ENTRY_ITEMS]), V1)
        self.assertIn("評価できない", str(e.exception))
        for k in ("Q93", "Q97", "Q48"):
            self.assertIn(k, str(e.exception))
        # the requirements files as they are (no Q93 to Q97 of this kind yet): not evaluated either way round
        req = load_requirements(ROOT / "data" / "requirements" / "offroad-4w.yaml")
        try:
            cases = BUILDERS["H7"](SPEC, req, V1)
        except BuildError as e:
            self.assertIn("評価できない", str(e))
        else:
            self.assertTrue(cases)

    def test_without_q47_everything_counts(self):
        lc = BUILDERS["H7"](SPEC, requirements(drop=("Q47",)), V1)[0]
        self.assertEqual(lc["params"]["openable"], "no")
        d = GEN.build(GEN.apply_choices(DOOR, {}))
        rec = EntryOpening().run(d, lc, PRED, {})
        self.assertEqual(results(rec)["entry_blocked"]["value"], 1.0)  # the door

    def test_plans_predict_the_criteria(self):
        names = {p["quantity"] for p in EntryOpening().describe()["predicts"]}
        for lc in BUILDERS["H7"](SPEC, REQ, V1):
            plan = PLANS.get(lc["load_case_id"]) or PLANS["H7"]
            self.assertEqual(plan["tool"], "entry_opening")
            quantities = [p["quantity"] for p in plan["predictions"]]
            self.assertIn(lc["criterion"]["quantity"], quantities)
            self.assertLessEqual(set(quantities), names)


class DesignsTest(unittest.TestCase):
    """The tool and K0 on version 1, version 2 (bars_net) and the side_opening sets."""

    @classmethod
    def setUpClass(cls):
        cls.out = {}
        for name, d in (("v1", V1), ("v2", GEN.build(GEN.apply_choices(V2, {}))),
                        ("door", GEN.build(GEN.apply_choices(DOOR, {}))),
                        ("clip_net", GEN.build(GEN.apply_choices(CLIP_NET, {})))):
            cases = {}
            for lc in BUILDERS["H7"](SPEC, REQ, d):
                rec = EntryOpening().run(d, lc, PRED, {})
                cases[lc["load_case_id"]] = (lc, rec, predict(d, lc))
            cls.out[name] = cases

    def verdict(self, name, case) -> bool:
        lc, rec, _ = self.out[name][case]
        c = lc["criterion"]
        v = results(rec)[c["quantity"]]["value"]
        return v <= c["limit"]["value"] if c["op"] == "<=" else v >= c["limit"]["value"]

    def test_records_fit_the_contract(self):
        for name, cases in self.out.items():
            for k, (_, rec, _) in cases.items():
                with self.subTest(design=name, case=k):
                    self.assertEqual(rec["status"], "ok", rec.get("error"))
                    self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])

    def test_version_2_cannot_be_entered(self):
        got = results(self.out["v2"]["H7-opening"][1])
        self.assertEqual(got["entry_blocked"]["value"], 3.0)
        self.assertIn("side_bar_R1", got["entry_blocked"]["where"])
        self.assertIn("side_net_R", got["entry_blocked"]["where"])
        self.assertLess(got["entry_opening_width"]["value"], 450.0)
        self.assertFalse(self.verdict("v2", "H7-opening"))
        self.assertFalse(self.verdict("v2", "H7-handhold"))  # 42.7 mm tubes: thicker than Q89's 38 mm

    def test_side_opening_passes_every_case(self):
        for name in ("door", "clip_net"):
            for case in ("H7-opening", "H7-step", "H7-handhold"):
                self.assertTrue(self.verdict(name, case), f"{name} {case}")
            got = results(self.out[name]["H7-opening"][1])
            self.assertGreaterEqual(got["entry_opening_width"]["value"], 450.0)
            self.assertGreaterEqual(got["entry_opening_width_low"]["value"], 300.0)

    def test_k0_against_the_tool(self):
        for name, cases in self.out.items():
            for k, (lc, rec, pred) in cases.items():
                got = results(rec)
                for quantity, v in pred.items():
                    with self.subTest(design=name, case=k, quantity=quantity):
                        tol = lc["tolerance_rel"] * max(abs(v), 1.0)
                        self.assertLessEqual(abs(got[quantity]["value"] - v), tol)
        # the widths and heights agree exactly here: nothing but the side frame reaches into the band above the seat
        for name in ("v2", "door"):
            lc, rec, pred = self.out[name]["H7-opening"]
            self.assertAlmostEqual(pred["entry_opening_width"], results(rec)["entry_opening_width"]["value"], places=6)
            self.assertAlmostEqual(pred["entry_opening_height"], results(rec)["entry_opening_height"]["value"], places=6)
        lc, rec, pred = self.out["v1"]["H7-step"]
        self.assertAlmostEqual(pred["entry_step_height"], results(rec)["entry_step_height"]["value"], places=6)


class NoSeatTest(unittest.TestCase):
    def test_no_side_seat(self):
        d = copy.deepcopy(V1)
        d["parts"] = [p for p in d["parts"] if p["kind"] != "seat"]
        lc = BUILDERS["H7"](SPEC, REQ, V1)[0]
        rec = EntryOpening().run(d, lc, PRED, {})
        self.assertEqual(rec["status"], "failed")


if __name__ == "__main__":
    unittest.main()

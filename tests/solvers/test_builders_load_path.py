"""Builders R7 and R8 (hiraku.catalog.builders.load_path) with the load_path tool, on the demo's version 1.

The catalog rows of R7 and R8 and the keys Q66-Q69 and Q75-Q78 are the design department's; until they are on
this branch the tests write the spec and the keys here (values and names as the design department wrote them).
"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.catalog import load_requirements
from hiraku.catalog.builders import BUILDERS, PLANS, BuildError
from hiraku.catalog.builders.load_path import NOT_BORROWED, R7_TOL
from hiraku.solvers.shelf import LoadPath
from tests.solvers.test_load_path import touch_nearest

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
# version 1 before designgen joined every part in shape (kit/joints.py): its floating pedals and column cases
UNJOINED = json.loads((ROOT / "tests" / "solvers" / "data" / "offroad-4w-v1-unjoined.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]
FORMULAS = solvers.formula_registry()
SPECS = {
    "R7": {"id": "R7", "domain": "strength", "title": "操作部の荷重", "applies_if": "ride and self_propelled",
           "requirement_keys": ["Q66", "Q67", "Q68", "Q69"]},
    "R8": {"id": "R8", "domain": "strength", "title": "荷重の通り道", "applies_if": "always",
           "requirement_keys": ["Q61", "Q75", "Q76", "Q77", "Q78"]},
}
NEW_KEYS = [("Q66", "手の操作部に掛かる力", 300, "N"), ("Q67", "手の操作部の握る点の動きの上限", 5, "mm"),
            ("Q68", "足の操作部に掛かる力", 500, "N"), ("Q69", "足の操作部の踏む面の支えの動きの上限", 5, "mm"),
            ("Q75", "乗員と部品に掛かる横の加速度", 0.70, "g"), ("Q76", "乗員と部品に掛かる前後の加速度", 0.90, "g"),
            ("Q77", "乗員の肩・腰の位置 (点 R) を外へ押す力", 725, "N"),
            ("Q78", "Q77 の力を除いたあとの点 R の永久の横の変形の上限", 25, "mm")]


def requirements(path="offroad-4w.yaml", drop=()) -> dict:
    req = copy.deepcopy(load_requirements(ROOT / "data" / "requirements" / path))
    have = {r["key"] for r in req["requirements"]}
    for key, name, value, unit in NEW_KEYS:
        if key not in have and key not in drop:
            req["requirements"].append({"key": key, "name": name, "value": value, "unit": unit, "source": "default",
                                        "trust": 3, "basis": "test copy of the design department's key"})
    req["requirements"] = [r for r in req["requirements"] if r["key"] not in drop]
    contract.validate("requirements_file", req)
    return req


REQ = requirements()


def results(rec) -> dict:
    return {r["quantity"]: r for r in rec["results"]}


def judged(lc, rec) -> bool | None:
    """The criterion on the analysis value (None: the tool gives no such value)."""
    c = lc["criterion"]
    r = results(rec).get(c["quantity"])
    if r is None:
        return None
    v, lim = r["value"], c["limit"]["value"]
    return {"<=": v <= lim, "<": v < lim, ">=": v >= lim, ">": v > lim}[c["op"]]


def predict(plan, design, lc) -> dict:
    """The plan's predictions as the scripted run makes them (inputs_from, then the K0 formula)."""
    out = {}
    for p in plan["predictions"]:
        try:
            given = p["inputs_from"](design, lc)
        except ValueError:  # the reader does not apply to this design (tools.plan: ReaderError, the formula refused)
            out.setdefault("refused", []).append(p["quantity"])
            continue
        inputs = {k: given[ref.split(".", 1)[1]] for k, ref in p["inputs"].items()}
        out[p["quantity"]] = FORMULAS.evaluate(p["formula_id"], inputs)["outputs"][p["output"]]["value"]
    return out


class BuildTest(unittest.TestCase):
    def test_cases_fit_the_contract(self):
        r7 = BUILDERS["R7"](SPECS["R7"], REQ, V1)
        r8 = BUILDERS["R8"](SPECS["R8"], REQ, V1)
        self.assertEqual([lc["load_case_id"] for lc in r7], ["R7-hand-300N", "R7-foot-500N"])
        self.assertEqual([lc["load_case_id"] for lc in r8], ["R8-free", "R8-stress", "R8-push-725N"])
        for lc in r7 + r8:
            contract.validate("load_case", lc)
            LoadPath().declared(lc)  # every param is one the tool declares
        crit = {lc["load_case_id"]: lc["criterion"] for lc in r7 + r8}
        self.assertEqual(crit["R7-hand-300N"]["quantity"], "grip_deflection")
        self.assertEqual(crit["R7-hand-300N"]["limit"], {"value": 5.0, "unit": "mm"})
        self.assertEqual(crit["R7-foot-500N"]["quantity"], "pedal_deflection")
        self.assertEqual(crit["R8-free"]["quantity"], "free_parts")
        self.assertEqual(crit["R8-stress"]["quantity"], "max_utilisation")
        self.assertEqual(crit["R8-stress"]["limit"], {"value": 1.0, "unit": "1"})
        self.assertEqual(crit["R8-push-725N"]["limit"], {"value": 25.0, "unit": "mm"})
        self.assertEqual(r7[0]["tolerance_rel"], R7_TOL)

    def test_occupant_forces_are_computed(self):
        p = BUILDERS["R8"](SPECS["R8"], REQ, V1)[0]["params"]
        m = next(r["value"] for r in REQ["requirements"] if r["key"] == "Q33")
        self.assertAlmostEqual(p["occupant_force_lateral"]["value"], m * 0.70 * 9.80665)
        self.assertAlmostEqual(p["occupant_force_longitudinal"]["value"], m * 0.90 * 9.80665)
        self.assertEqual(p["accel_vertical"], {"value": 1.0, "unit": "g"})

    def test_missing_keys_are_not_evaluated_with_the_reason(self):
        with self.assertRaises(BuildError) as e:
            BUILDERS["R8"](SPECS["R8"], requirements(drop=("Q75",)), V1)
        self.assertIn("Q75 横の加速度が要件に無い", str(e.exception))
        self.assertIn(NOT_BORROWED, str(e.exception))
        with self.assertRaises(BuildError) as e:
            BUILDERS["R7"](SPECS["R7"], requirements(drop=("Q66", "Q67", "Q68", "Q69")), V1)
        self.assertIn("評価できない", str(e.exception))
        self.assertIn("Q66", str(e.exception))
        # only the foot keys: the hand case alone
        r7 = BUILDERS["R7"](SPECS["R7"], requirements(drop=("Q68", "Q69")), V1)
        self.assertEqual([lc["load_case_id"] for lc in r7], ["R7-hand-300N"])
        # without Q77: no push case
        self.assertNotIn("R8-push-725N", [lc["load_case_id"] for lc in
                                          BUILDERS["R8"](SPECS["R8"], requirements(drop=("Q77",)), V1)])

    def test_products_without_the_keys(self):
        for name in ("care-bed.yaml", "electric-guitar.yaml", "serving-robot.yaml"):
            req = copy.deepcopy(load_requirements(ROOT / "data" / "requirements" / "products" / name))
            for tid in ("R7", "R8"):
                with self.assertRaises(BuildError, msg=f"{name} {tid}") as e:
                    BUILDERS[tid](SPECS[tid], req, V1)
                self.assertIn("評価できない", str(e.exception))


class DemoV1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {lc["load_case_id"]: lc for tid in ("R7", "R8") for lc in BUILDERS[tid](SPECS[tid], REQ, V1)}
        tool = LoadPath()
        cls.runs = {k: tool.run(V1, lc, PRED, {}) for k, lc in cls.cases.items()}

    def test_runs(self):
        for k, rec in self.runs.items():
            self.assertEqual(rec["status"], "ok", f"{k}: {rec.get('error')}")

    def test_verdicts_on_version_1(self):
        """On version 1 as generated before the joints (its pedals float, nothing stands outboard of point R)."""
        runs = {k: LoadPath().run(UNJOINED, lc, PRED, {}) for k, lc in self.cases.items()}
        got = {k: judged(lc, runs[k]) for k, lc in self.cases.items()}
        self.assertTrue(got["R7-hand-300N"])  # the column reaches the dash through bracket_column
        self.assertIsNone(got["R7-foot-500N"])  # both pedals float: no deflection, free_parts says why
        self.assertFalse(got["R8-free"])
        self.assertTrue(got["R8-stress"])
        self.assertIsNone(got["R8-push-725N"])  # nothing outboard of point R
        foot = results(runs["R7-foot-500N"])
        self.assertIn("pedal_brake", foot["free_parts"]["where"])
        self.assertEqual(results(runs["R8-push-725N"])["occupant_push_open"]["value"], 4.0)
        # nothing outboard of point R on version 1: the push's hand calculation does not apply (its reader refuses)
        self.assertEqual(predict(PLANS["R8"], UNJOINED, self.cases["R8-push-725N"]).get("refused"),
                         ["occupant_push_deflection"])

    def test_plans_predict(self):
        for k, lc in self.cases.items():
            plan = PLANS.get(k) or PLANS[lc["test_id"]]  # a load case's own plan first (R8-stress)
            self.assertEqual(plan["tool"], "load_path")
            pred = predict(plan, V1, lc)
            got = results(self.runs[k])
            self.assertEqual(pred["free_parts"], got["free_parts"]["value"], k)  # the same parts float
            q = lc["criterion"]["quantity"]
            from hiraku.tools.plan import expected_entries

            q_ = lc["criterion"]["quantity"]
            exp = next((e for e in expected_entries(plan) if e.get("quantity") == q_), {})
            if q in pred and q in got and exp.get("quantity") == q and exp.get("end"):
                # the plan records this mismatch's cause and ends the step without a verdict (R7's grip: the tool's
                # grip moves with the plated frame, which the hand calculation takes as rigid): smaller here
                self.assertLess(pred[q], got[q]["value"], k)
            elif q in pred and q in got and exp.get("quantity") == q and (exp.get("safe_side") or exp.get("tool_decides")):
                # the plan names this mismatch as safe side, or as the tool's to decide on a bound (R7's grip): the
                # prediction must be the unfavourable value
                self.assertGreaterEqual(pred[q], got[q]["value"], f"{k}: {q} predicted {pred[q]}, got {got[q]['value']}")
            elif q in pred and q in got:
                # as compare: a prediction of 0 divides by |analysed| (0 when both are 0; R8-free on the joined v1)
                rel = (abs(got[q]["value"] - pred[q]) / abs(pred[q]) if pred[q] else
                       (0.0 if not got[q]["value"] else 1.0))
                self.assertLessEqual(rel, lc["tolerance_rel"], f"{k}: {q} predicted {pred[q]}, got {got[q]['value']}")

    def test_column_held_two_ways_on_version_1(self):
        # version 1's column (before the joints) touches bracket_column (0.8 mm gap) and steering_shaft; without the
        # bracket the shaft and rack still hold it, so the hand case keeps a deflection
        d = copy.deepcopy(UNJOINED)
        d["parts"] = [p for p in d["parts"] if p["id"] != "bracket_column"]
        rec = LoadPath().run(d, self.cases["R7-hand-300N"], PRED, {})
        self.assertIn("grip_deflection", results(rec))
        self.assertNotIn("steering_column", results(rec)["free_parts"]["where"])

    def test_floating_column_is_found(self):
        # with neither the bracket nor the shaft, the column and the wheel float: no grip deflection, both named first
        d = copy.deepcopy(UNJOINED)
        d["parts"] = [p for p in d["parts"] if p["id"] not in ("bracket_column", "steering_shaft")]
        lc = self.cases["R7-hand-300N"]
        rec = LoadPath().run(d, lc, PRED, {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        r = results(rec)
        self.assertNotIn("grip_deflection", r)
        self.assertTrue(r["free_parts"]["where"].startswith("steering_wheel"))
        free = {x["where"] for x in rec["results"] if x["quantity"].startswith("free_part.")}
        self.assertTrue({"steering_column", "steering_wheel"} <= free)
        self.assertEqual(predict(PLANS["R7"], d, lc)["free_parts"], r["free_parts"]["value"])

    def test_pedals_moved_to_touch_are_held(self):
        """Both pedals are held once they touch. The accelerator on a tower post passes; the brake pedal on the
        bulkhead, a 2.3 mm sheet (designgen.ladder_4w.PLATE_T_MM) and not the 20 mm solid box it was, moves more
        than Q69. The K0 prediction with the plate strip (load_path.plate_strip) against the tool is in
        test_load_path_plate_k0."""
        d = touch_nearest(UNJOINED, "pedal_brake", "part:bulkhead")  # the floating pedals, before the joints
        d = touch_nearest(d, "pedal_accel", "member:tower_post_RA2")
        lc = self.cases["R7-foot-500N"]
        rec = LoadPath().run(d, lc, PRED, {})
        r = results(rec)
        self.assertNotIn("pedal_brake", r["free_parts"]["where"])
        self.assertNotIn("pedal_accel", r["free_parts"]["where"])
        lim = lc["criterion"]["limit"]["value"]
        self.assertLessEqual(r["pedal_deflection.pedal_accel"]["value"], lim)
        self.assertGreater(r["pedal_deflection.pedal_brake"]["value"], lim)
        self.assertFalse(judged(lc, rec))


class NoRiderR8Test(unittest.TestCase):
    """A product nobody rides (the serving robot: no occupant parts, no Q33): R8 builds without the occupants'
    inertia and without Q33 (common.loading reads Q33 only when the design carries people)."""

    def test_no_q33_without_occupants(self):
        d = copy.deepcopy(V1)
        d["parts"] = [p for p in d["parts"] if p.get("kind") != "occupant"]
        req = requirements(drop=("Q33",))  # Q30 stays two_cargo, as the serving robot run had it
        cases = BUILDERS["R8"](SPECS["R8"], req, d)
        for lc in cases:
            self.assertNotIn("occupant_mass", lc["params"])
            self.assertNotIn("Q33", lc["requirement_keys"])
        self.assertIn("R8-free", [lc["load_case_id"] for lc in cases])


if __name__ == "__main__":
    unittest.main()

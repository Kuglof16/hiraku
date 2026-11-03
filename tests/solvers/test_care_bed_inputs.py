"""The care bed's public calls for the roles' formula inputs (product_care_bed.predictions_for, inputs_for): one per
load case, the same predictions as the scripted steps, every from.<name> filled."""

import math
import unittest

from hiraku import designgen, solvers
from hiraku.catalog import load_catalog
from hiraku.catalog.build import build_load_cases
from hiraku.catalog.builders import product_care_bed as bed

from tests.solvers.test_designgen_care_bed import bed_requirements

REG = solvers.formula_registry()


class BedInputsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.req = bed_requirements()
        cls.design = designgen.propose("care_bed", {}, {}, cls.req)
        cls.cases = {lc["load_case_id"]: lc for lc in build_load_cases(load_catalog(), cls.req, cls.design,
                                                                       ["S1", "R5", "P5", "T3", "R3"])}

    def test_every_load_case_has_its_predictions(self):
        for lc_id, lc in self.cases.items():
            step = bed.step_for(lc)
            want = step["predictions"] if step else {"T3": bed.T3_PREDICTIONS, "R3": bed.R3_PREDICTIONS}[lc["test_id"]]
            got = bed.predictions_for(lc)
            self.assertTrue(got, lc_id)
            self.assertEqual([p["quantity"] for p in got], [p["quantity"] for p in want], lc_id)

    def test_inputs_fill_every_from_reference_and_evaluate(self):
        for lc_id, lc in self.cases.items():
            vals = bed.inputs_for(self.design, lc)
            for p in bed.predictions_for(lc):
                refs = [r.split(".", 1)[1] for r in p["inputs"].values() if r.startswith("from.")]
                self.assertEqual([r for r in refs if r not in vals], [], (lc_id, p["quantity"]))
                got = REG.evaluate(p["formula_id"], {k: vals[r.split(".", 1)[1]] for k, r in p["inputs"].items()})
                self.assertTrue(math.isfinite(got["outputs"][p["output"]]["value"]), (lc_id, p["quantity"]))

    def test_body_weight_in_newtons_is_taken_as_a_mass(self):
        from hiraku.catalog.builders import BUILDERS
        from hiraku.catalog.builders.common import body_mass_in_kg

        q33 = next(r for r in self.req["requirements"] if r["key"] == "Q33")
        self.assertEqual(q33["unit"], "N")
        kg = next(r for r in body_mass_in_kg(self.req)["requirements"] if r["key"] == "Q33")
        self.assertEqual(kg["unit"], "kg")
        self.assertAlmostEqual(kg["value"], q33["value"] / 9.80665)
        self.assertIn("as a mass", kg["basis"])
        driver = dict(self.req, requirements=[r for r in self.req["requirements"] if r["key"] != "Q30"] + [
            {"key": "Q30", "name": "loading", "value": "driver", "source": "default", "trust": 3, "basis": "test"}])
        for tid in ("H1", "H5"):  # interference and the assembly order apply to a bed
            self.assertTrue(BUILDERS[tid](load_catalog().test(tid), driver, self.design), tid)

    def test_seated_body_size_is_not_a_bed_test(self):
        flags = {k: True for k in ("lean", "electric", "pinch", "cargo", "standing", "assembly")}
        picked = load_catalog().select_tests(flags, "static")
        self.assertIn("H1", picked)
        self.assertNotIn("H3", picked)

    def test_r3_names_its_stand_in(self):
        crit = self.cases["R3-uses"]["criterion"]
        self.assertEqual(crit["stand_in_quantity"], "stress_range")
        self.assertEqual(bed.R3_TINY_DAMAGE, 1e-6 * crit["limit"]["value"])  # the contract's 1e-6 x the limit

    def test_other_load_cases_have_none(self):
        self.assertEqual(bed.predictions_for({"load_case_id": "R1-rest", "test_id": "R1"}), [])
        self.assertEqual(bed.inputs_for(self.design, {"load_case_id": "R1-rest", "test_id": "R1"}), {})


if __name__ == "__main__":
    unittest.main()

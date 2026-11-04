"""load_path's max_utilisation (each member's stress over its own material's yield / safety_factor) and the R8-stress
load case judged on it (hiraku.catalog.builders.load_path)."""

from __future__ import annotations

import unittest

from hiraku import contract, designgen
from hiraku.catalog.builders import BUILDERS
from hiraku.solvers.shelf import LoadPath
from hiraku.solvers.shelf import load_path as LP
from tests.solvers.test_builders_load_path import PRED, REQ, SPECS, V1, judged, results
from tests.solvers.test_load_path_plates import SET2


def stress_case(design):
    return next(lc for lc in BUILDERS["R8"](SPECS["R8"], REQ, design) if lc["load_case_id"] == "R8-stress")


class KnownAnswersTest(unittest.TestCase):
    def test_two_materials(self):
        rs = LP.utilisation_known_answers()
        for r in rs:
            self.assertTrue(r["pass"], r)
        self.assertEqual({r["quantity"] for r in rs},
                         {"max_stress", "max_utilisation", "max_utilisation_is_the_tip", "allowable_stress"})
        for r in rs:
            self.assertIn(r, LP.load_path_known_answers())


class LoadCaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2 = designgen.regenerate(V1, SET2)

    def test_case_carries_q61(self):
        lc = stress_case(self.v2)
        contract.validate("load_case", lc)
        LoadPath().declared(lc)
        sf = next(r["value"] for r in REQ["requirements"] if r["key"] == "Q61")
        self.assertEqual(lc["params"]["safety_factor"], {"value": float(sf), "unit": "1"})
        self.assertEqual(lc["criterion"]["quantity"], "max_utilisation")
        # both materials of the members are named with their yields
        self.assertIn("STKR400", lc["criterion"]["basis"])
        self.assertIn("STKM13A", lc["criterion"]["basis"])
        self.assertIn("215 MPa", lc["criterion"]["basis"])

    def test_each_member_against_its_own_yield(self):
        for d in (V1, self.v2):
            lc = stress_case(d)
            rec = LoadPath().run(d, lc, PRED, {})
            self.assertEqual(rec["status"], "ok", rec.get("error"))
            r = results(rec)
            sf = lc["params"]["safety_factor"]["value"]
            mid = r["max_utilisation"]["where"]
            mat = next(m["material"] for m in d["members"] if m["id"] == mid)
            self.assertAlmostEqual(r["allowable_stress"]["value"], d["materials"][mat]["yield_MPa"] / sf)
            self.assertEqual(r["allowable_stress"]["where"], mid)
            # the member of the largest stress is no more utilised than the one named
            top = r["max_stress"]["where"]
            y_top = d["materials"][next(m["material"] for m in d["members"] if m["id"] == top)]["yield_MPa"]
            self.assertGreaterEqual(r["max_utilisation"]["value"] + 1e-12, r["max_stress"]["value"] * sf / y_top)
            self.assertTrue(judged(lc, rec))


if __name__ == "__main__":
    unittest.main()

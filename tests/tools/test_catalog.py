import copy
import unittest

from hiraku import contract
from hiraku.catalog import (CatalogError, applies, build_load_cases, load_catalog, load_requirements, min_frame_yield,
                            owner_agent, parts_on_board, project_fields)
from hiraku.tools.summary import design_summary
from tests.tools.fakes import example_design, loaded_design
from tests.tools.helpers import REQUIREMENTS


def design():
    return loaded_design()


class ExprTest(unittest.TestCase):
    def test_applies(self):
        flags = {"self_propelled": True, "rough": False, "cargo": True}
        self.assertTrue(applies("always", {}))
        self.assertTrue(applies("self_propelled and not rough", flags))
        self.assertFalse(applies("self_propelled and rough", flags))
        self.assertTrue(applies("(cargo or lean) and self_propelled", flags))
        self.assertTrue(applies("self_propelled and speed_vehicle", flags, "vehicle"))
        self.assertFalse(applies("speed_vehicle", flags, "walk"))

    def test_rejects_other_python(self):
        for bad in ("made_up", "__import__('os')", "ride == 1", "ride + lean"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    applies(bad, {})


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog()
        self.req = load_requirements(REQUIREMENTS)

    def test_requirements_file_fits_the_contract(self):
        self.assertEqual(contract.errors("requirements_file", self.req), [])
        for r in self.req["requirements"]:
            with self.subTest(key=r["key"]):
                self.assertTrue(r.get("basis"), "every requirement says where it comes from")
                if r["trust"] == 3:
                    self.assertIn("assumption", r["basis"])

    def test_vehicle_tests_follow_the_flags(self):
        ids = self.catalog.select_tests(self.req["flags"], self.req["speed_class"])
        for t in ("S1", "P1", "R1", "R4", "C1", "T1", "H4"):
            self.assertIn(t, ids)
        for t in ("G1", "F3", "P5", "R5", "T3", "C2"):  # public road, liquid, actuator, no self-propulsion, near people
            self.assertNotIn(t, ids)
        table = self.catalog.select_tests({"lean": True, "cargo": True, "standing": True, "assembly": True}, "static")
        self.assertNotIn("P1", table)
        self.assertIn("R5", table)

    def test_load_cases(self):
        lcs = build_load_cases(self.catalog, self.req, design(), ["P1", "R1"])
        for lc in lcs:
            self.assertEqual(contract.errors("load_case", lc), [])
        p1, r1 = lcs
        self.assertEqual(p1["load_case_id"], "P1-step100")
        self.assertEqual(p1["feeds"], ["R1-step100"])
        self.assertEqual(p1["params"]["loading"], "two_cargo")
        self.assertEqual(r1["params"]["loading"], "two_cargo")
        self.assertEqual(p1["requirement_keys"], ["Q15", "Q30", "Q33", "Q40"])
        self.assertNotIn("criterion", p1)
        crit = r1["criterion"]
        self.assertAlmostEqual(crit["limit"]["value"], 235.0 / 1.5)
        self.assertIn("not checked", crit["basis"])
        self.assertEqual((r1["tolerance_rel"], p1["tolerance_rel"]), (0.15, 0.10))

    def test_limit_uses_the_weakest_material_and_q61(self):
        d = design()
        d["materials"]["alu"] = dict(d["materials"]["steel"], name="alu", yield_MPa=150.0, verified=True)
        d["members"][0]["material"] = "alu"
        self.assertEqual(min_frame_yield(d)[0], 150.0)
        req = copy.deepcopy(self.req)
        next(r for r in req["requirements"] if r["key"] == "Q61")["value"] = 2.0
        r1 = build_load_cases(self.catalog, req, d, ["R1"])[0]
        self.assertAlmostEqual(r1["criterion"]["limit"]["value"], 75.0)

    def test_missing_requirement_is_reported(self):
        req = copy.deepcopy(self.req)
        req["requirements"] = [r for r in req["requirements"] if r["key"] != "Q15"]
        with self.assertRaises(CatalogError) as cm:
            build_load_cases(self.catalog, req, design(), ["P1"])
        self.assertIn("Q15", str(cm.exception))
        with self.assertRaises(CatalogError):
            build_load_cases(self.catalog, self.req, design(), ["S1"])  # no builder yet

    def test_design_must_carry_the_requirements_load(self):
        cases = [
            (example_design(), "needs 2 occupant parts"),
            (loaded_design(person_kg=70.0), "Q33 = 76.6 kg"),
            (loaded_design(cargo_kg=100.0), "Q40 = 150 kg"),
        ]
        for d, needle in cases:
            with self.subTest(needle=needle):
                with self.assertRaises(CatalogError) as cm:
                    build_load_cases(self.catalog, self.req, d, ["P1"])
                self.assertIn(needle, str(cm.exception))
        req = copy.deepcopy(self.req)
        next(r for r in req["requirements"] if r["key"] == "Q30")["value"] = "three"
        with self.assertRaises(CatalogError):
            build_load_cases(self.catalog, req, design(), ["P1"])
        next(r for r in req["requirements"] if r["key"] == "Q30")["value"] = "two"
        p1 = build_load_cases(self.catalog, req, loaded_design(cargo_kg=1.0), ["P1"])[0]  # cargo stays off
        self.assertEqual((p1["params"]["loading"], p1["requirement_keys"]), ("two", ["Q15", "Q30", "Q33"]))

    def test_parts_on_board(self):
        d = design()
        ids = {name: [p["id"] for p in parts_on_board(d, name)] for name in ("empty", "driver", "two", "two_cargo")}
        self.assertEqual(ids["empty"], ["battery", "seat"])
        self.assertEqual(ids["driver"], ["battery", "seat", "driver"])
        self.assertEqual(ids["two"], ["battery", "seat", "driver", "passenger"])
        self.assertEqual(ids["two_cargo"], ["battery", "seat", "driver", "passenger", "cargo"])
        with self.assertRaises(ValueError):
            parts_on_board(d, "three")
        s = design_summary(d)["loadings"]
        self.assertAlmostEqual(s["empty"]["mass"]["value"], 60.0)
        self.assertAlmostEqual(s["two_cargo"]["mass"]["value"], 60.0 + 2 * 76.6 + 150.0)
        self.assertGreater(s["two_cargo"]["cg_x"]["value"], s["two"]["cg_x"]["value"])  # cargo sits behind

    def test_project_fields(self):
        lcs = build_load_cases(self.catalog, self.req, design(), ["P1", "R1"])
        f = project_fields(self.catalog, self.req, lcs)
        self.assertEqual([p["domain"] for p in f["domain_picks"]], ["strength"])
        self.assertEqual([a["agent_id"] for a in f["agents"]], ["lead", "cae.strength"])
        self.assertEqual(f["agents"][1]["prop"], "i_beam")
        self.assertEqual(owner_agent(lcs, ["strength"], "P1-step100"), "cae.strength")
        full = dict(f, run_id="20261102-203000-offroad-4w", created_at="2026-11-02T20:30:00.000+09:00", status="running")
        self.assertEqual(contract.errors("project", full), [])


if __name__ == "__main__":
    unittest.main()

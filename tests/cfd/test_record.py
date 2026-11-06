"""The sweep's record: analysis-shaped entries that fit the contract's schema, the words for what is not checked, and
a replay from the saved file. No GPU."""

import ast
import copy
import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

from hiraku import contract
from hiraku.cfd import aero_map, record, uncertainty
from tests.cfd.test_aero_map import F1_HAND, f1_crit, fake_run, make_map, rows

RECORD_PY = Path(record.__file__)


def fake_record(**run_kw):
    study = [aero_map.case_row(fake_run(y, dx=0.12, grid_gain=1.03, **run_kw)) for y in (0.0, 20.0)]
    aero = make_map(rows(**run_kw), study)
    table = uncertainty.table(aero, {"f1": {**F1_HAND, "criterion": f1_crit(30.0)}, "f2": {"rho": 1.225, "C_d": 0.8, "A_m2": 1.6}})
    return record.make_record(aero, table, design_version=5, requested={"dx_mm": 80.0})


def with_additions() -> jsonschema.Draft202012Validator:
    """The contract's analysis with what record.contract_additions() asks of the lead (nothing, once the lead has added it)."""
    root = copy.deepcopy(contract.schema())
    defs = root["$defs"]
    add = record.contract_additions()
    if add["base_enum"] not in defs["base"]["enum"]:
        defs["base"]["enum"].append(add["base_enum"])
    defs["analysis"]["properties"].setdefault("physically_validated", add["analysis.physically_validated"])
    return jsonschema.Draft202012Validator({"$schema": root["$schema"], "$defs": defs, "$ref": "#/$defs/analysis"})


def filled(analysis, n=1):
    return record.as_contract_analysis(analysis, f"ana-{n:04d}", ["pred-0001"])


class ShapeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = fake_record()

    def by_case(self, case):
        return next(a for a in self.rec["analyses"] if a["load_case_id"] == case)

    def test_every_case_is_one_analysis_with_its_load_case(self):
        ids = [a["load_case_id"] for a in self.rec["analyses"]]
        self.assertEqual(ids, ["CFD-40kmh-yaw0", "CFD-40kmh-yaw5L", "CFD-40kmh-yaw5R", "CFD-40kmh-yaw10L", "CFD-40kmh-yaw10R",
                               "CFD-40kmh-yaw15L", "CFD-40kmh-yaw15R", "CFD-40kmh-yaw20L", "CFD-40kmh-yaw20R"])
        pattern = contract.schema()["$defs"]["load_case"]["properties"]["load_case_id"]["pattern"]
        for a in self.rec["analyses"]:
            self.assertRegex(a["load_case_id"], pattern)
            self.assertEqual((a["tool"], a["design_version"], a["status"]), ("cfd_aero", 5, "ok"))

    def test_a_case_at_rest_says_so_in_its_load_case(self):
        rest = aero_map.case_row(fake_run(90.0, speed=0.0, air=30.0))
        self.assertEqual(record.load_case_id(rest), "CFD-rest-wind30-yaw90L")
        self.assertRegex(record.load_case_id(rest), contract.schema()["$defs"]["load_case"]["properties"]["load_case_id"]["pattern"])
        a = record.case_analysis(rest, aero_map.crosswind_form(rest, 1.8), design_version=5)
        self.assertEqual([e.message for e in with_additions().iter_errors(filled(a))], [])
        got = {r["quantity"]: r["value"] for r in a["results"]}
        self.assertEqual((got["speed"], got["air_speed"]), (0.0, 30.0))
        self.assertAlmostEqual(got["wind_across"], 30.0)

    def test_results_have_quantity_value_and_unit(self):
        got = {r["quantity"]: r for r in self.by_case("CFD-40kmh-yaw10L")["results"]}
        self.assertEqual(got["Fx"]["unit"], "N")
        self.assertEqual(got["Mx"]["unit"], "N*m")
        self.assertEqual(got["CdA"]["unit"], "m^2")
        self.assertEqual(got["Cd"]["unit"], "1")
        self.assertEqual(got["dynamic_pressure"]["unit"], "Pa")
        self.assertEqual(got["yaw"]["value"], 10.0)
        self.assertEqual(got["side_cp_height"]["unit"], "mm")
        self.assertAlmostEqual(got["side_cp_height"]["value"], 700.0)
        self.assertAlmostEqual(got["side_force_coefficient"]["value"], 7.0 / 1.8)
        self.assertEqual(got["side_area"]["unit"], "m^2")
        self.assertEqual(got["side_force_coefficient"]["where"], "wind from the left")
        at0 = {r["quantity"] for r in self.by_case("CFD-40kmh-yaw0")["results"]}
        self.assertIn("drag_force", at0)
        self.assertNotIn("side_force_coefficient", at0)  # no wind across at yaw 0

    def test_what_is_not_checked_says_so(self):
        self.assertIs(self.rec["physically_validated"], False)
        self.assertTrue(any("確かめていない" in a for a in self.rec["assumptions"]))
        for a in self.rec["analyses"]:
            self.assertIs(a["physically_validated"], False)
            self.assertTrue(any("確かめていない" in x for x in a["assumptions"]))

    def test_the_grid_study_is_a_mesh_convergence_block(self):
        block = self.by_case("CFD-40kmh-yaw0")["mesh_convergence"]
        self.assertEqual((block["quantity"], block["unit"]), ("drag_force", "N"))
        self.assertEqual([lv["element_size_mm"] for lv in block["levels"]], [120.0, 80.0])  # coarse to fine
        self.assertEqual(block["levels"][1]["elements"], 188 * 112 * 69)
        self.assertAlmostEqual(block["change_pct"], 3.0)
        self.assertEqual(block["limit_pct"], aero_map.GRID_CHANGE_LIMIT_PCT)
        self.assertEqual(block["verdict"], "converged")
        side = self.by_case("CFD-40kmh-yaw20L")["mesh_convergence"]
        self.assertEqual((side["quantity"], side["unit"]), ("Mx", "N*m"))  # what F1 reads
        self.assertNotIn("mesh_convergence", self.by_case("CFD-40kmh-yaw5L"))

    def test_a_large_change_is_not_converged(self):
        study = [aero_map.case_row(fake_run(0.0, dx=0.12, grid_gain=1.2))]
        rec = record.make_record(make_map(rows(), study), {"rows": []}, design_version=5)
        self.assertEqual(next(a for a in rec["analyses"] if a["load_case_id"] == "CFD-40kmh-yaw0")["mesh_convergence"]["verdict"], "not_converged")


class SchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = fake_record()

    def test_every_analysis_fits_the_contract_with_what_the_lead_is_asked_to_add(self):
        v = with_additions()
        for i, a in enumerate(self.rec["analyses"], 1):
            self.assertEqual([e.message for e in v.iter_errors(filled(a, i))], [])

    def test_only_the_asked_additions_are_missing_from_todays_contract(self):
        for i, a in enumerate(self.rec["analyses"], 1):
            rest = filled(a, i)
            rest.pop("physically_validated")
            rest["base"] = "K2" if record.BASE not in contract.BASES else record.BASE
            self.assertEqual(contract.errors("analysis", rest), [])

    def test_the_check_sees_a_wrong_record(self):
        wrong = filled(self.rec["analyses"][0])
        wrong["results"][0]["unit"] = "furlong"
        self.assertTrue(list(with_additions().iter_errors(wrong)))
        loose = filled(self.rec["analyses"][0])
        loose["physically_validated"] = "no"
        self.assertTrue(list(with_additions().iter_errors(loose)))

    def test_without_ids_the_entry_says_it_has_none(self):
        a = self.rec["analyses"][0]
        self.assertNotIn("analysis_id", a)  # the store assigns it
        self.assertEqual(a["prediction_ids"], [])  # analyze gives them

    def test_clock_times_stay_in_their_two_fields(self):
        slow = fake_run(10.0)
        slow.result.wall_time_s = 99.0
        quick = fake_run(10.0)
        quick.result.wall_time_s = 1.0
        a, b = (record.case_analysis(aero_map.case_row(r)) for r in (slow, quick))
        self.assertEqual((a["wall_time_s"], a["timings_s"], b["wall_time_s"]), (99.0, {"solve": 99.0}, 1.0))
        for k in ("wall_time_s", "timings_s"):
            a.pop(k), b.pop(k)
        self.assertEqual(a, b)


class LoadCaseValuesTest(unittest.TestCase):
    def test_the_params_are_k2s_with_the_source_in_the_basis(self):
        aero = make_map([*rows(), aero_map.case_row(fake_run(90.0, speed=0.0, air=30.0, cya=2.2))])
        rec = record.make_record(aero, {"rows": []}, design_version=5)
        got = record.load_case_values(rec, "cfd/v5/40kmh_dx80/aero_record.json")
        self.assertEqual(sorted(got["params"]), ["cd", "cy", "front_area", "side_area", "side_cp_height"])
        self.assertAlmostEqual(got["params"]["cy"]["value"], 2.2 / 1.8)
        self.assertEqual(got["params"]["side_area"], {"value": 1.8, "unit": "m^2"})
        self.assertEqual(got["params"]["side_cp_height"]["unit"], "mm")
        self.assertAlmostEqual(got["params"]["cd"]["value"], 1.4 / 1.7)
        self.assertEqual(got["params"]["front_area"], {"value": 1.7, "unit": "m^2"})
        for text in ("v5", "cfd/v5/40kmh_dx80/aero_record.json", "rest_wind30_yaw+90_dx80", "ヨー +90°", "40kmh_yaw+0_dx80", "確かめていない"):
            self.assertIn(text, got["basis"])
        self.assertIs(got["physically_validated"], False)
        schema = contract.schema()["$defs"]["load_case"]["properties"]
        check = jsonschema.Draft202012Validator({**schema["params"], "$defs": contract.schema()["$defs"]})
        self.assertEqual([e.message for e in check.iter_errors(got["params"])], [])

    def test_a_point_that_is_not_there_is_left_out(self):
        rec = record.make_record(make_map([aero_map.case_row(fake_run(0.0))]), {"rows": []}, design_version=5)
        self.assertEqual(sorted(record.load_case_values(rec)["params"]), ["cd", "front_area"])


class ReplayTest(unittest.TestCase):
    def test_a_saved_record_reads_back_the_same(self):
        rec = fake_record()
        with tempfile.TemporaryDirectory() as d:
            path = record.save_record(rec, Path(d) / "cfd" / "v5" / "x")
            self.assertEqual(path.name, record.RECORD_FILE)
            self.assertEqual(sorted(p.name for p in path.parent.iterdir()), [record.RECORD_FILE])  # no temporary file left
            back = record.load_record(path)
        self.assertEqual(back, json.loads(json.dumps(rec, ensure_ascii=False)))
        first, again = record.answer(rec, "cfd/x.json", False), record.answer(back, "cfd/x.json", True)
        first.pop("replayed"), again.pop("replayed")
        self.assertEqual(json.loads(json.dumps(first)), again)

    def test_a_file_that_is_no_record_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            for name, text in (("a.json", "{}"), ("b.json", "not json"), ("c.json", "[1]")):
                p = Path(d) / name
                p.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    record.load_record(p)
            with self.assertRaises(ValueError):
                record.load_record(Path(d) / "missing.json")

    def test_the_answer_is_small_and_says_what_is_not_checked(self):
        out = record.answer(fake_record(), "cfd/x.json", False)
        self.assertLess(len(json.dumps(out, ensure_ascii=False)), 40000)
        self.assertIs(out["physically_validated"], False)
        self.assertNotIn("geometry", out["table"][0])
        self.assertEqual(out["table"][0]["dx_mm"], 80.0)

    def test_the_record_module_touches_nothing_of_the_gpu(self):
        tree = ast.parse(RECORD_PY.read_text(encoding="utf-8"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add(node.module or "")
        self.assertEqual(sorted(m for m in mods if m.startswith("hiraku")), [])


if __name__ == "__main__":
    unittest.main()

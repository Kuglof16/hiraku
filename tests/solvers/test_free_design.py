"""The analysis department's side of a design and a load case Claude writes (docs/contract.md "Claude が書く設計"):
the limit from limit_from (hiraku.catalog.limits), the loading check on a design without occupants, the builders'
reasons on a design that lacks what they read, and each tool's applies(design)."""

import copy
import unittest

from hiraku.catalog import load_catalog, load_requirements
from hiraku.catalog.build import CatalogError, check_design_loading, loading_for
from hiraku.catalog.builders.common import BuildError
from hiraku.catalog.limits import limit_value, unit_of_field
from tests.solvers.written_designs import demo_design, serving_cart
from tests.tools.helpers import REQUIREMENTS

EXAMPLE = {"expr": "yield / Q61", "unit": "MPa", "refs": {"yield": "materials.steel.yield_MPa", "Q61": "Q61"}}


def requirements(**values):
    req = load_requirements(REQUIREMENTS)
    have = {r["key"] for r in req["requirements"]}
    for k, v in values.items():
        if k in have:
            next(r for r in req["requirements"] if r["key"] == k).update(v)
        else:
            req["requirements"].append({"key": k, **v})
    return req


class LimitValueTest(unittest.TestCase):
    def setUp(self):
        self.design = serving_cart()
        self.req = requirements(Q61={"value": 1.5, "unit": "1"})

    def test_the_example(self):
        got = limit_value(EXAMPLE, self.req, self.design)
        self.assertEqual(got["unit"], "MPa")
        self.assertAlmostEqual(got["value"], 245.0 / 1.5)  # the cart's steel 245 MPa over Q61 1.5
        self.assertEqual(got["from"]["yield"], {"value": 245.0, "unit": "MPa", "ref": "materials.steel.yield_MPa"})
        self.assertEqual(got["from"]["Q61"], {"value": 1.5, "unit": "1", "ref": "Q61"})

    def test_a_requirement_ref_with_its_unit(self):
        q15 = next(r for r in self.req["requirements"] if r["key"] == "Q15")
        got = limit_value({"expr": "step / 2", "unit": "mm", "refs": {"step": "Q15"}}, self.req, self.design)
        self.assertEqual(q15["unit"], "mm")
        self.assertAlmostEqual(got["value"], q15["value"] / 2)
        got = limit_value({"expr": "Q15 * 2", "unit": "mm"}, self.req, self.design)  # a key needs no ref
        self.assertAlmostEqual(got["value"], q15["value"] * 2)

    def test_a_section_ref_and_a_conversion(self):
        lf = {"expr": "2 * t", "unit": "m", "refs": {"t": "sections.sq20.dims_mm.t"}}
        got = limit_value(lf, self.req, self.design)
        self.assertEqual(got["unit"], "m")
        self.assertAlmostEqual(got["value"], 2 * 1.6e-3)  # mm from dims_mm, converted to m
        self.assertEqual(got["from"]["t"]["unit"], "mm")
        kpa = limit_value(dict(EXAMPLE, unit="Pa"), self.req, self.design)
        self.assertAlmostEqual(kpa["value"], 245.0e6 / 1.5)

    def test_a_variable_and_min(self):
        d = demo_design()
        got = limit_value({"expr": "min(wb, tr) / 10", "unit": "mm",
                           "refs": {"wb": "variables.wheelbase", "tr": "variables.track"}}, self.req, d)
        self.assertAlmostEqual(got["value"], min(d["variables"]["wheelbase"]["value"],
                                                 d["variables"]["track"]["value"]) / 10)

    def test_no_unit_takes_the_refs_of_the_same_dimension(self):
        got = limit_value({"expr": "yield / Q61", "refs": EXAMPLE["refs"]}, self.req, self.design)
        self.assertEqual(got["unit"], "MPa")
        ratio = limit_value({"expr": "nu * 2", "refs": {"nu": "materials.steel.nu"}}, self.req, self.design)
        self.assertEqual((ratio["unit"], ratio["value"]), ("1", 0.6))

    def test_field_suffixes(self):
        self.assertEqual(unit_of_field(["materials", "s", "density_kg_m3"]), "kg/m^3")
        self.assertEqual(unit_of_field(["sections", "s", "dims_mm", "t"]), "mm")
        self.assertEqual(unit_of_field(["materials", "s", "E_MPa"]), "MPa")
        self.assertIsNone(unit_of_field(["parts", "x", "mass_g"]))  # _g is gravity, not gram: not read

    def test_each_failure_says_why(self):
        cases = [
            ({"expr": "yield / Q61", "unit": "MPa", "refs": {"Q61": "Q61"}}, "has no ref"),
            ({"expr": "x / 2", "unit": "MPa", "refs": {"x": "materials.alu.yield_MPa"}}, "has no 'alu'"),
            ({"expr": "x / 2", "unit": "MPa", "refs": {"x": "Q98"}}, "does not have"),
            (dict(EXAMPLE, unit="mm"), "cannot be given in 'mm'"),
            ({"expr": "yield + t", "unit": "MPa",
              "refs": {"yield": "materials.steel.yield_MPa", "t": "sections.sq20.dims_mm.t"}}, "adding or subtracting"),
            (dict(EXAMPLE, expr="yield / "), "not an expression"),
            (dict(EXAMPLE, expr="__import__('os')"), "only calls are min"),
            (dict(EXAMPLE, expr="yield.real"), "may only use"),
            (dict(EXAMPLE, expr="yield / 0"), "division by zero"),
            ({"expr": "x", "unit": "1", "refs": {"x": "materials.steel.name"}}, "unit of"),
            ({"expr": "x", "unit": "1", "refs": {"x": "materials.steel.source"}}, "unit of"),
            (dict(EXAMPLE, unit="furlong"), "not a unit"),
            ({"expr": "", "unit": "MPa"}, "empty"),
            ("yield / Q61", "must be"),
        ]
        for lf, needle in cases:
            with self.subTest(lf=lf):
                with self.assertRaises(BuildError) as cm:
                    limit_value(lf, self.req, self.design)
                self.assertIn(needle, str(cm.exception))

    def test_a_requirement_that_is_not_a_number(self):
        with self.assertRaises(BuildError) as cm:
            limit_value({"expr": "Q30", "unit": "1"}, self.req, self.design)  # Q30 is the loading's name
        self.assertIn("not a number", str(cm.exception))


class LoadingWithoutOccupantsTest(unittest.TestCase):
    """hiraku.contract.loading: a design with no occupant parts carries no people under any loading."""

    def test_no_people_required_of_a_design_without_occupants(self):
        cart = serving_cart()
        req = requirements(Q40={"value": 8.0})  # the cart's plates
        for loading in ("empty", "driver", "two", "two_cargo"):
            with self.subTest(loading=loading):
                check_design_loading(req, cart, loading)
        self.assertEqual(loading_for(req, cart), ("two_cargo", ["Q30", "Q40"]))  # Q33 sets nothing here

    def test_cargo_is_still_checked(self):
        with self.assertRaises(CatalogError) as cm:
            check_design_loading(requirements(Q40={"value": 20.0}), serving_cart(), "two_cargo")
        self.assertIn("Q40 = 20 kg", str(cm.exception))

    def test_too_few_occupants_still_fail_with_the_rule(self):
        d = demo_design()
        people = list(dict.fromkeys(p["id"].split(".")[0] for p in d["parts"] if p["kind"] == "occupant"))
        self.assertEqual(len(people), 2)
        d["parts"] = [p for p in d["parts"] if not (p["kind"] == "occupant" and p["id"].split(".")[0] == people[1])]
        with self.assertRaises(CatalogError) as cm:
            check_design_loading(load_requirements(REQUIREMENTS), d, "two")
        self.assertIn("needs 2 occupant parts", str(cm.exception))
        self.assertIn("one with none carries no people", str(cm.exception))

    def test_the_builders_run_on_the_cart(self):
        from hiraku.catalog import build_load_cases

        cases = build_load_cases(load_catalog(), requirements(Q40={"value": 8.0}), serving_cart(), ["R1", "P4"])
        self.assertEqual({lc["test_id"] for lc in cases}, {"R1", "P4"})
        self.assertTrue(all("Q33" not in lc.get("requirement_keys", []) for lc in cases))


class BuilderReasonsTest(unittest.TestCase):
    """A design without the variables a builder's reader needs gets BuildError naming them, not a KeyError."""

    LC = {"load_case_id": "x", "test_id": "P4", "params": {
        "distance": {"value": 10.0, "unit": "km"}, "grade_low": {"value": 5.0, "unit": "%"},
        "grade_high": {"value": 10.0, "unit": "%"}, "speed_low": {"value": 5.0, "unit": "km/h"},
        "speed_high": {"value": 10.0, "unit": "km/h"}, "loading": "two_cargo"}}

    def test_p4_names_the_missing_variables(self):
        from hiraku.catalog.builders.analysis import p4_calc

        with self.assertRaises(BuildError) as cm:
            p4_calc(serving_cart(), self.LC)
        for name in ("drivetrain_efficiency", "battery_voltage", "battery_capacity"):
            self.assertIn(name, str(cm.exception))
        self.assertIn("battery_energy", p4_calc(demo_design(), self.LC))  # the demo vehicle has them

    def test_r3_names_the_missing_variables(self):
        from hiraku.catalog import build_load_cases
        from hiraku.catalog.builders.analysis import r3_legs

        d = demo_design()
        lc = build_load_cases(load_catalog(), load_requirements(REQUIREMENTS), d, ["R3"])[0]
        self.assertTrue(r3_legs(d, lc))
        for name in ("fatigue_fat_class", "fatigue_slope"):
            del d["variables"][name]
        with self.assertRaises(BuildError) as cm:
            r3_legs(d, lc)
        self.assertIn("fatigue_fat_class, fatigue_slope", str(cm.exception))

    def test_the_thermal_reader_names_a_missing_variable(self):
        from hiraku.catalog.builders.common import required_vars, variable_records

        with self.assertRaises(BuildError) as cm:
            variable_records(serving_cart(), ["ambient_x"], "T1")
        self.assertIn("ambient_x", str(cm.exception))
        d = demo_design()
        d["variables"]["battery_voltage"] = {"value": 48.0, "unit": "mm"}
        with self.assertRaises(BuildError):
            required_vars(d, {"battery_voltage": "V"}, "P4")


class AppliesTest(unittest.TestCase):
    """Each tool's applies(design): None when it applies, else why not; run() refuses with that reason."""

    @classmethod
    def setUpClass(cls):
        from hiraku.solvers import builtin_tools

        cls.tools = {t.name: t for t in builtin_tools()}
        cls.demo, cls.cart = demo_design(), serving_cart()

    BED = ("bed_static", "actuator_linkage", "use_fatigue", "actuator_duty")

    def test_every_vehicle_tool_applies_to_the_demo_vehicle(self):
        for name, tool in self.tools.items():
            with self.subTest(tool=name):
                why = tool.applies(self.demo)
                if name in self.BED or name == "frame_tubes":  # a care bed's / a space frame's tools
                    self.assertTrue(why)
                else:
                    self.assertIsNone(why)

    def test_the_written_cart(self):
        refused = {name: tool.applies(self.cart) for name, tool in self.tools.items()}
        needles = {
            "drive_energy": "battery_voltage", "thermal": "battery_voltage", "fatigue": "fatigue_fat_class",
            "crash": "bumper_stiffness", "entry_opening": "kind seat", "frame_tubes": "space_frame_utv",
            "kc_bench": "kc_table", "turning_circle": "actuation.steer", "lane_change": "actuation.steer",
            "steady_circle": "actuation.steer",
            "bed_static": "mattress", "actuator_linkage": "bracket_back", "use_fatigue": "bracket_back",
            "actuator_duty": "bracket_back",
        }
        for name, needle in needles.items():
            with self.subTest(tool=name):
                self.assertIn(needle, refused[name] or "")
                self.assertTrue(refused[name].startswith(name))
        # what runs on a frame of tubes on four wheels, whoever wrote it (each was run on the cart: ok)
        for name in ("frame_static", "frame_shell", "modal", "step_climb", "load_path", "assembly", "spray_reach",
                     "braking", "step_climb_dynamic", "tilt", "crash_beam"):
            with self.subTest(tool=name):
                self.assertIsNone(refused[name])

    def test_run_refuses_with_the_reason(self):
        lc = {"load_case_id": "x", "params": {"loading": "empty", "speed": {"value": 10.0, "unit": "km/h"}}}
        for name in ("crash", "kc_bench"):
            with self.subTest(tool=name):
                rec = self.tools[name].run(self.cart, lc, [{"prediction_id": "pred-0001"}], {})
                self.assertEqual((rec["status"], rec["error"]), ("failed", self.tools[name].applies(self.cart)))
                self.assertEqual(rec["results"], [])
        # a written frame without a mattress is not a care bed (applies), though bed_static's run reads no names
        self.assertIsNone(self.tools["bed_static"].shape(self.cart))

    def test_the_kind_of_design_still_counts(self):
        from hiraku.solvers.shelf.applies import design_kind

        self.assertEqual(design_kind(self.demo), "ladder_4w")
        self.assertIsNone(design_kind(self.cart))  # written_by: never guessed
        self.assertIn("care_bed の型", self.tools["bed_static"].applies(dict(self.demo, parts=self.demo["parts"] + [
            {"id": "m", "name": "m", "kind": "mattress", "mass_kg": 1.0,
             "box": {"center_mm": [0, 0, 500], "size_mm": [10, 10, 10]}}])))
        no_t = copy.deepcopy(self.cart)
        no_t["sections"]["sq20"] = {"shape": "rect_bar", "dims_mm": {"b": 20.0, "h": 20.0}}
        self.assertIn("角管・丸管", self.tools["frame_shell"].applies(no_t))
        no_node = copy.deepcopy(self.cart)
        for s in no_node["supports"]:
            del s["frame_node"]
        self.assertIn("frame_node", self.tools["load_path"].applies(no_node))
        self.assertIn("3 個以上", self.tools["frame_static"].applies(no_node))


if __name__ == "__main__":
    unittest.main()

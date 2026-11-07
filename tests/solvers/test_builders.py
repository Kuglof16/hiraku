import copy
import json
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.catalog import load_catalog, load_requirements
from hiraku.catalog.builders import BUILDERS, PLANS, BuildError
from hiraku.solvers.k2_vehicle.shelf.crash_beam import EDGE_NOTE
from hiraku.tools.summary import design_summary

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
CATALOG = load_catalog()
OURS = ("C1", "N1", "R3", "P4", "T1", "T2", "H1", "H3", "H5")
PRED = [{"prediction_id": "pred-0001"}]
# Formulas a plan uses that the tool's own list (its module, owned elsewhere) does not name yet: the R3 plan's leg
# and the larger of its two welds. They must be in the registry; the tool's list is to take them.
NOT_YET_LISTED = {("R3", "frame_static.mount_leg_stress"), ("R3", "fatigue.worst_of_two_welds")}


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


def plan_for(lc):
    """A load case's own step before its test's (as hiraku.catalog.build.recipe_for)."""
    return PLANS.get(lc["load_case_id"]) or PLANS.get(lc["test_id"])


class BuildersTest(unittest.TestCase):
    runs: dict = {}

    @classmethod
    def setUpClass(cls):
        # keyed by test id, or by load case id for a test that builds more than one (T2)
        cls.cases = {}
        for tid in OURS:
            got = BUILDERS[tid](spec(tid), REQ, DESIGN)
            cls.cases.update({lc["load_case_id"]: lc for lc in got} if isinstance(got, list) else {tid: got})
        # each case on its plan's tool (C1 is judged by K2's crash_beam)
        cls.runs = {key: TOOLS[plan_for(lc)["tool"]].run(DESIGN, lc, PRED, {}) for key, lc in cls.cases.items()}

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
        self.assertEqual(self.cases["T2-40C-controller"]["criterion"]["limit"], {"value": 85.0, "unit": "degC"})
        self.assertEqual(self.cases["T2-40C-motor"]["criterion"]["limit"], {"value": 155.0, "unit": "degC"})
        self.assertEqual(self.cases["R3"]["params"]["road_class"], "C")
        self.assertEqual(self.cases["T1"]["params"]["ambient_temperature"], {"value": 40.0, "unit": "degC"})
        self.assertEqual(self.cases["H1-steer"]["criterion"]["limit"], DESIGN["variables"]["max_steer_angle"])

    def test_known_defects_fail_their_criteria(self):
        r = {x["quantity"]: x["value"] for x in self.runs["H1-steer"]["results"]}
        self.assertLess(r["free_steer_angle"], self.cases["H1-steer"]["criterion"]["limit"]["value"])
        r = {x["quantity"]: x["value"] for x in self.runs["H3-body-size"]["results"]}
        self.assertLess(r["knee_room"], 0.0)

    def test_plans_predict_within_the_tolerance(self):
        for key, lc in self.cases.items():
            tid = lc["test_id"]
            plan = plan_for(lc)
            tool = TOOLS[plan["tool"]]
            self.assertIn(tid, tool.tests)
            self.assertEqual(plan["tool"], "crash_beam" if tid == "C1" else tool_for(tid).name)
            got = {r["quantity"]: r for r in self.runs[key]["results"]}
            preds = {}
            expected = plan.get("expected_mismatch", {}).get("quantity")
            when = plan.get("expected_mismatch", {}).get("when")  # high / low: the miss is expected that way only
            for p in plan["predictions"]:
                extra = p["inputs_from"](DESIGN, lc) if "inputs_from" in p else {}
                refs = [ref for ref in p["inputs"].values() if isinstance(ref, str)]
                self.assertEqual(any(ref.startswith("from.") for ref in refs), "inputs_from" in p)
                inputs = {k: resolve(ref, DESIGN, lc, extra, preds) for k, ref in p["inputs"].items()}
                out = FORMULAS.evaluate(p["formula_id"], inputs)["outputs"][p["output"]]
                preds[p["quantity"]] = out
                if (tid, p["formula_id"]) in NOT_YET_LISTED:
                    self.assertEqual(FORMULAS.get(p["formula_id"]).formula_id, p["formula_id"])
                else:  # a shelf tool lists its formulas; a K2 tool names them from its pairs (formula_ids)
                    listed = tool.formulas if hasattr(tool, "formulas") else tool.formula_ids()
                    self.assertIn(p["formula_id"], listed)
                if p["quantity"] not in got:  # an intermediate hand value, not compared
                    continue
                ana = got[p["quantity"]]
                self.assertEqual(out["unit"], ana["unit"], f"{tid} {p['quantity']}")
                # as compare: a prediction of 0 divides by |analysed| (0 when both are 0)
                rel = ((ana["value"] - out["value"]) / abs(out["value"]) if out["value"] else
                       (0.0 if not ana["value"] else (ana["value"] - out["value"]) / abs(ana["value"])))
                edge = next((a for a in self.runs[key].get("assumptions", []) if a.startswith(EDGE_NOTE)), "")
                if f"{p['quantity']} <=" in edge or f"{p['quantity']} >=" in edge:
                    # the record says the tool's verdict on this quantity turns inside crash_beam's break_band (a
                    # member at the break angle's edge, decided by the arithmetic's last digits): no fixed comparison
                    self.assertTrue("Members that break in one run and not in another" in edge
                                    or "Unbroken at" in edge, edge)
                    continue
                side = "high" if out["value"] > ana["value"] else "low"  # as scripted.expected_for
                if p["quantity"] == expected and when and (side != when or abs(rel) <= lc["tolerance_rel"]):
                    # the plan expects the miss one way only (when): on this design it is not that way, so it matches
                    self.assertLessEqual(abs(rel), lc["tolerance_rel"], f"{tid} {p['quantity']}: {rel:+.3f}")
                elif p["quantity"] == expected:  # the plan says it misses, with its cause: it must really miss
                    self.assertGreater(abs(rel), lc["tolerance_rel"], f"{tid} {p['quantity']}: {rel:+.3f}")
                else:
                    self.assertLessEqual(abs(rel), lc["tolerance_rel"], f"{tid} {p['quantity']}: {rel:+.3f}")

    def test_r3_looks_where_the_tool_finds_the_worst_weld(self):
        """With the suspension's mounts on the frame the fatigue tool's worst weld is a front lower arm's leg (or
        its mirror); the plan's larger hand damage is at that leg."""
        from hiraku.catalog.builders.analysis import r3_places

        self.assertNotIn("expected_mismatch", PLANS["R3"])
        worst = next(r for r in self.runs["R3"]["results"] if r["quantity"] == "fatigue_damage")
        places = r3_places(DESIGN, self.cases["R3"])
        hand_where = max(places)[1]
        self.assertEqual(hand_where, "lower_leg_LA2")
        self.assertIn(worst["where"], (hand_where, "lower_leg_RA2"))

    def test_r3_plan_on_version_2(self):
        """Wider track (the wheels further outboard of the rails), and that with thicker rails (stiffer in torsion:
        the ladder twists less, but the end cross member's moment stays): still within the tolerance."""
        from hiraku import designgen

        mm = lambda v: {"value": v, "unit": "mm"}  # noqa: E731
        wide = {"track": mm(1220), "steering_x": mm(-480)}
        for changes in (wide, dict(wide, rail_t=mm(4.5))):
            v2 = designgen.regenerate(DESIGN, changes)
            lc = BUILDERS["R3"](spec("R3"), REQ, v2)
            got = {r["quantity"]: r["value"] for r in tool_for("R3").run(v2, lc, PRED, {})["results"]}["fatigue_damage"]
            preds = {}
            for p in PLANS["R3"]["predictions"]:
                extra = p["inputs_from"](v2, lc) if "inputs_from" in p else {}
                out = FORMULAS.evaluate(p["formula_id"], {k: resolve(ref, v2, lc, extra, preds) for k, ref in p["inputs"].items()})
                preds[p["quantity"]] = out["outputs"][p["output"]]
            hand = preds["fatigue_damage"]["value"]
            self.assertLessEqual(abs(got - hand) / hand, lc["tolerance_rel"], f"{sorted(changes)}: hand {hand:.4f}, tool {got:.4f}")

    def test_plans_predict_the_criterion(self):
        """The scripted run judges a load case from a prediction of its criterion's quantity."""
        for key, lc in self.cases.items():
            if "criterion" not in lc:
                continue
            self.assertIsNotNone(plan_for(lc), key)
            self.assertIn(lc["criterion"]["quantity"], [p["quantity"] for p in plan_for(lc)["predictions"]], key)

    def test_missing_requirement_is_named(self):
        req = copy.deepcopy(REQ)
        req["requirements"] = [r for r in req["requirements"] if r["key"] != "Q90"]
        with self.assertRaisesRegex(BuildError, "Q90"):
            BUILDERS["R3"](CATALOG.test("R3"), req, DESIGN)
        req = copy.deepcopy(REQ)
        req["requirements"] = [r for r in req["requirements"] if r["key"] != "Q65"]
        self.assertEqual(BUILDERS["C1"](CATALOG.test("C1"), req, DESIGN)[0]["params"]["speed"]["value"], 40.0)  # Q50

    def test_t1_past_the_limit(self):
        """The ambient where the hand calculation puts the battery 5 K over its limit: the analysis is over it too."""
        lc = BUILDERS["T1"](dict(spec("T1"), variant="beyond"), REQ, DESIGN)
        self.assertTrue(lc["load_case_id"].endswith("-beyond") and lc["title"].startswith("限界の外"))
        self.assertNotIn("Q19", lc["requirement_keys"])
        rec = tool_for("T1").run(DESIGN, lc, PRED, {})
        got = {r["quantity"]: r["value"] for r in rec["results"]}["temp_battery_max"]
        self.assertGreater(got, lc["criterion"]["limit"]["value"])
        p = PLANS["T1"]["predictions"][0]
        hand = FORMULAS.evaluate(p["formula_id"], {k: resolve(ref, DESIGN, lc, {}) for k, ref in p["inputs"].items()})
        self.assertAlmostEqual(hand["outputs"][p["output"]]["value"], lc["criterion"]["limit"]["value"] + 5.0, places=6)

    def test_t2_judges_the_motor_and_the_controller(self):
        """T2 (モーター・制御器の温度): one load case per part, each with that part's temperature and limit."""
        t2 = sorted(k for k in self.cases if k.startswith("T2"))
        self.assertEqual(t2, ["T2-40C-controller", "T2-40C-motor"])
        for part, ja, other in (("motor", "モーター", "制御器"), ("controller", "制御器", "モーター")):
            lc = self.cases[f"T2-40C-{part}"]
            self.assertEqual((lc["test_id"], lc["criterion"]["quantity"]), ("T2", f"temp_{part}_max"))
            var = DESIGN["variables"][f"{part}_max_temperature"]
            self.assertEqual(lc["criterion"]["limit"], {"value": var["value"], "unit": var["unit"]})
            self.assertIn(f"{part}_max_temperature", lc["criterion"]["basis"])
            self.assertIn(f"{ja}の温度", lc["title"])
            self.assertNotIn(other, lc["title"])
        self.assertEqual([p["quantity"] for p in PLANS["T2"]["predictions"]],
                         ["temp_motor_max", "temp_controller_max", "drive_time"])

    def test_t2_past_the_limit(self):
        """Past the limit: one case per part at the ambient where the hand calculation puts that part 5 K over."""
        got = BUILDERS["T2"](dict(spec("T2"), variant="beyond"), REQ, DESIGN)
        self.assertEqual([lc["load_case_id"].rsplit("-", 2)[1:] for lc in got], [["motor", "beyond"], ["controller", "beyond"]])
        for lc, (quantity, output) in zip(got, (("temp_motor_max", "T_motor"), ("temp_controller_max", "T_controller"))):
            contract.validate("load_case", lc)
            self.assertTrue(lc["title"].startswith("限界の外"))
            self.assertEqual(lc["criterion"]["quantity"], quantity)
            rec = tool_for("T2").run(DESIGN, lc, PRED, {})
            self.assertGreater({r["quantity"]: r["value"] for r in rec["results"]}[quantity], lc["criterion"]["limit"]["value"])
            p = next(p for p in PLANS["T2"]["predictions"] if p["output"] == output)
            hand = FORMULAS.evaluate(p["formula_id"], {k: resolve(ref, DESIGN, lc, {}) for k, ref in p["inputs"].items()})
            self.assertAlmostEqual(hand["outputs"][output]["value"], lc["criterion"]["limit"]["value"] + 5.0, places=6)

    def test_climb_limit(self):
        out = lambda h: {k: v["value"] for k, v in FORMULAS.evaluate(  # noqa: E731
            "step_climb.climb_limit", {"r": {"value": 300.0, "unit": "mm"}, "h": {"value": h, "unit": "mm"}})["outputs"].items()}
        self.assertEqual((out(250.0)["climbable"], out(250.0)["step_margin"]), (1.0, 50.0))
        self.assertEqual((out(330.0)["climbable"], out(330.0)["step_margin"], out(330.0)["max_step"]), (0.0, -30.0, 300.0))
        self.assertEqual(out(300.0)["climbable"], 0.0)  # at the axle's height the corner pushes level

    def test_catalog_tolerance_wins(self):
        spec = dict(CATALOG.test("C1"), tolerance_rel=0.07)
        self.assertEqual({lc["tolerance_rel"] for lc in BUILDERS["C1"](spec, REQ, DESIGN)}, {0.07})


class DragBuilderTest(unittest.TestCase):
    """F2: the drag force at Q50, the loading of Q30, within the drive force left at Q50; the plan's prediction
    waits for a tool."""

    @classmethod
    def setUpClass(cls):
        cls.lc = BUILDERS["F2"](CATALOG.test("F2"), REQ, DESIGN)

    def test_f2_load_case_on_the_demo(self):
        contract.validate("load_case", self.lc)
        q50 = next(r["value"] for r in REQ["requirements"] if r["key"] == "Q50")
        q30 = next(r["value"] for r in REQ["requirements"] if r["key"] == "Q30")
        self.assertEqual(self.lc["load_case_id"], f"F2-drag{q50:g}kmh")
        self.assertEqual((self.lc["test_id"], self.lc["domain"]), ("F2", "aero"))
        self.assertEqual(self.lc["params"]["speed"], {"value": float(q50), "unit": "km/h"})
        self.assertEqual(self.lc["params"]["loading"], q30)
        self.assertIn("Q50", self.lc["requirement_keys"])
        self.assertIn("Q30", self.lc["requirement_keys"])

    def test_f2_criterion_is_the_drive_force_left_at_q50(self):
        from hiraku.contract.loading import on_board
        from hiraku.solvers.k0_handcalc.scene_inputs import launch_inputs
        from hiraku.solvers.k0_handcalc.vehicle import mass_summary

        c = self.lc["criterion"]
        self.assertEqual((c["quantity"], c["op"], c["limit"]["unit"]), ("drag_force", "<=", "N"))
        load = self.lc["params"]["loading"]
        li = {k: v["value"] for k, v in launch_inputs(DESIGN, load)["inputs"].items()}
        v = self.lc["params"]["speed"]["value"] / 3.6
        drive = min(li["T"] * li["i"] * li["eta"] / (li["rolling_radius"] / 1000.0), li["eta"] * li["P"] * 1000.0 / v)
        roll = mass_summary(on_board(DESIGN, load))["mass_kg"] * 9.80665 * li["C_rr"]
        self.assertAlmostEqual(c["limit"]["value"], drive - roll, places=6)
        self.assertGreater(c["limit"]["value"], 0.0)
        for words in ("駆動力", "転がり抵抗", "2026-11-07"):
            self.assertIn(words, c["basis"])

    def test_f2_plan_predicts_drag_force_on_the_demo(self):
        from hiraku.catalog.builders.drag import F2_PLAN

        self.assertNotIn("F2", PLANS)  # no tool takes F2 yet
        self.assertNotIn("tool", F2_PLAN)
        self.assertNotIn("expected_mismatch", F2_PLAN)
        (p,) = F2_PLAN["predictions"]
        self.assertEqual((p["quantity"], p["formula_id"]), ("drag_force", "drag.force"))
        inputs = {k: resolve(ref, DESIGN, self.lc, {}) for k, ref in p["inputs"].items()}
        F = FORMULAS.evaluate(p["formula_id"], inputs)["outputs"][p["output"]]
        var = {k: DESIGN["variables"][k]["value"] for k in ("drag_coefficient", "frontal_area", "air_density")}
        v = self.lc["params"]["speed"]["value"] / 3.6
        self.assertEqual(F["unit"], "N")
        self.assertAlmostEqual(F["value"], 0.5 * var["air_density"] * var["drag_coefficient"] * var["frontal_area"] * v * v,
                               places=9)


class RopsBuilderTest(unittest.TestCase):
    """R4: one load case, R4-rollover, judged by K2's rollover_beam (the rollover on the tilt table with the frame as
    beams); the hand calculation is the static push of OECD Code 4 (borrowed) after the path check."""

    @classmethod
    def setUpClass(cls):
        cls.rb = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))  # has the roll bar
        cls.lcs = BUILDERS["R4"](CATALOG.test("R4"), REQ, cls.rb)
        cls.lc = cls.lcs[0]

    def test_load_case_fits_the_contract(self):
        for lc in self.lcs:
            contract.validate("load_case", lc)
        self.assertEqual(self.lc["load_case_id"], "R4-rollover")
        self.assertEqual(self.lc["domain"], CATALOG.test("R4")["domain"])

    def test_two_grounds_soil_and_pavement(self):
        """The user's decision: R4 on a soil (R4-rollover, contact.K_CONTACT) and a pavement (R4-rollover-pavement,
        contact.K_PAVEMENT), the same otherwise; both grounds are HIRAKU's assumption and say so; both use the plan."""
        from hiraku.solvers.k2_vehicle.contact import K_CONTACT, K_PAVEMENT

        soil, pave = self.lcs
        self.assertEqual([lc["load_case_id"] for lc in self.lcs], ["R4-rollover", "R4-rollover-pavement"])
        self.assertEqual(soil["params"]["contact_stiffness"], {"value": K_CONTACT / 1000.0, "unit": "N/mm"})
        self.assertEqual(pave["params"]["contact_stiffness"], {"value": K_PAVEMENT / 1000.0, "unit": "N/mm"})
        self.assertEqual({k: v for k, v in soil["params"].items() if k != "contact_stiffness"},
                         {k: v for k, v in pave["params"].items() if k != "contact_stiffness"})
        self.assertEqual(soil["criterion"]["quantity"], pave["criterion"]["quantity"])
        for lc, ground in ((soil, "土"), (pave, "舗装")):
            self.assertTrue(lc["title"].startswith(ground + "の地面で"))
            self.assertIn("地面の硬さは HIRAKU の仮定。土と舗装の 2 通り", lc["criterion"]["basis"])
            self.assertIs(plan_for(lc), PLANS["R4-rollover"])

    def test_values_are_code_4_at_the_reference_mass(self):
        from hiraku.catalog.builders.analysis import R4_CODE4, R4_REFERENCE_MASS_KG

        p = self.lc["params"]
        self.assertEqual((p["side_energy"], p["rear_energy"], p["crush_force"]),
                         ({"value": 875.0, "unit": "J"}, {"value": 700.0, "unit": "J"}, {"value": 10000.0, "unit": "N"}))
        self.assertEqual(p["side_energy"]["value"], R4_CODE4["side"] * R4_REFERENCE_MASS_KG)
        self.assertAlmostEqual(p["rear_fraction"]["value"], 1 / 6)
        self.assertEqual(p["loading"], "two_cargo")
        given = dict(CATALOG.test("R4"), params={"crush_force": {"value": 12, "unit": "kN"}})
        self.assertEqual(BUILDERS["R4"](given, REQ, self.rb)[0]["params"]["crush_force"], {"value": 12000.0, "unit": "N"})

    def test_criterion_is_the_occupant_space(self):
        c = self.lc["criterion"]
        self.assertEqual((c["quantity"], c["op"], c["limit"]), ("rops_occupant_clearance", ">=", {"value": 0.0, "unit": "mm"}))
        self.assertIn("Code 4", c["basis"])
        self.assertIn("rollover", c["basis"])
        for text in (c["basis"], self.lc["title"]):
            self.assertIn("HIRAKU 独自の計算の確かめ。規格の試験ではない", text)
            self.assertNotIn("規格に基づく", text)
        for s in ("ISO 3471", "ROHVA", "「HIRAKU の仮定」", "10 kN"):
            self.assertIn(s, c["basis"])
        self.assertIn("Q31", self.lc["requirement_keys"])

    def test_requirement_keys_from_the_catalog_entry(self):
        self.assertNotIn("Q64", self.lc["requirement_keys"])  # the demo file has no Q64 yet
        req = copy.deepcopy(REQ)
        req["requirements"].append({"key": "Q64", "name": "ROPS", "value": "あり", "source": "default", "trust": 1,
                                    "basis": "assumption (test)"})
        lc = BUILDERS["R4"](CATALOG.test("R4"), req, self.rb)[0]
        self.assertEqual(lc["requirement_keys"][:2], ["Q64", "Q31"])
        self.assertEqual(lc["criterion"]["requirement_keys"], ["Q64", "Q31"])
        req["requirements"][-1]["value"] = "なし"
        with self.assertRaisesRegex(BuildError, "Q64"):
            BUILDERS["R4"](CATALOG.test("R4"), req, self.rb)

    def test_plan_by_load_case_with_rollover_beam_alone_on_the_shelf(self):
        """The plan is keyed by the load case: rollover_beam, the path check first, then R4_PREDICTIONS (the criterion's
        quantity last); every formula is one the tool pairs; the expected mismatch decides by the tool, with its cause.
        rollover_beam is the only tool covering R4 and its stage is 3 (no lighter tool takes R4's stage)."""
        from hiraku.catalog.builders.analysis import R4_PREDICTIONS
        from hiraku.catalog.builders.load_path import PATH_PREDICTION
        from hiraku.solvers.k2_vehicle.shelf import TOOLS as K2_TOOLS

        self.assertNotIn("R4", PLANS)
        plan = plan_for(self.lc)
        self.assertIs(plan, PLANS["R4-rollover"])
        self.assertEqual(plan["tool"], "rollover_beam")
        self.assertEqual(plan["predictions"], [PATH_PREDICTION] + R4_PREDICTIONS)
        self.assertEqual(plan["predictions"][-1]["quantity"], self.lc["criterion"]["quantity"])
        tool = K2_TOOLS["rollover_beam"]
        for p in plan["predictions"]:
            self.assertIn(p["formula_id"], tool.formula_ids())
        exp = plan["expected_mismatch"]
        self.assertEqual(exp["quantity"], "rops_occupant_clearance")
        self.assertTrue(exp["tool_decides"] and exp["tool_decides_reason"] and exp["cause"] and exp["source"])
        self.assertNotIn("safe_side", exp)
        self.assertEqual([t.name for t in TOOLS.values() if "R4" in t.tests], ["rollover_beam"])
        self.assertEqual((tool.stage, tool.planned_only), (3, False))

    def test_hand_formulas_named_for_the_prediction_evaluate(self):
        """The formulas the builder's comment names, with frame_inputs and the load case's params."""
        from hiraku.solvers.k0_handcalc.frame_inputs import portal_frame_inputs

        pi = portal_frame_inputs(self.rb)
        p = self.lc["params"]
        side = FORMULAS.evaluate("frame_static.portal_frame_side", {**pi["side"], "E_in": p["side_energy"]})
        rear = FORMULAS.evaluate("frame_static.portal_frame_rear", {**pi["rear"], "E_in": p["rear_energy"],
                                                                     "f": p["rear_fraction"]})
        top = FORMULAS.evaluate("frame_static.portal_frame_top", {**pi["top"], "F": p["crush_force"]})
        for r in (side, rear, top):
            self.assertTrue(all(v["value"] == v["value"] for v in r["outputs"].values()))


class OperatingTimeLoopTest(unittest.TestCase):
    """Q54 as a time (a product that runs for hours: the serving robot's 10 h at 1.2 m/s, 8.33 % ramps): P4, T1 and
    T2 drive the loop that long at Q50 with the product's own Q13 and Q50, not the catalog's forest loop."""

    @classmethod
    def setUpClass(cls):
        cls.req = copy.deepcopy(REQ)
        for r in cls.req["requirements"]:
            if r["key"] == "Q54":
                r.update(value=10, unit="h")
            if r["key"] == "Q50":
                r.update(value=1.2, unit="m/s")
            if r["key"] == "Q13":
                r.update(value=8.33, unit="%")

    def test_distance_is_time_times_q50(self):
        for tid in ("P4", "T1", "T2"):
            lcs = BUILDERS[tid](CATALOG.test(tid), self.req, DESIGN)
            for lc in (lcs if isinstance(lcs, list) else [lcs]):
                contract.validate("load_case", lc)
                p = lc["params"]
                self.assertAlmostEqual(p["distance"]["value"], 10 * 1.2 * 3.6, places=9, msg=tid)
                self.assertAlmostEqual(p["grade_high"]["value"], 8.33, places=9)
                self.assertAlmostEqual(p["speed_low"]["value"], 1.2 * 3.6 / 2, places=9)
                self.assertIn("Q54 10 h", p["distance_from"])
        self.assertIn("10 h", BUILDERS["P4"](CATALOG.test("P4"), self.req, DESIGN)["criterion"]["basis"])

    def test_a_range_keeps_the_forest_loop(self):
        lc = BUILDERS["P4"](CATALOG.test("P4"), REQ, DESIGN)
        self.assertNotIn("distance_from", lc["params"])
        self.assertEqual(lc["params"]["grade_high"]["value"], 12.0)  # the catalog's forest loop


if __name__ == "__main__":
    unittest.main()

import copy
import json
import math
import os
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


SLOW = os.environ.get("HIRAKU_SLOW_TESTS") == "1"
# Tests whose plan's tool is a heavy shell solve (N1: modal_shell on the linkage, about a minute at 20 mm): their load
# cases are built and checked always, analysed only in the slow tests (HIRAKU_SLOW_TESTS=1), at 20 mm.
HEAVY = {"N1": {"element_size": {"value": 20.0, "unit": "mm"}}}
V1_LINKAGE = json.loads((ROOT / "tests" / "solvers" / "data" / "modal_shell_v1_20mm_linkage_modes.json")
                        .read_text(encoding="utf-8"))


def tool_for(test_id):
    """The tool the test's plan names, else the first shelf tool for the test."""
    plan = PLANS.get(test_id)
    if plan is not None:
        return TOOLS[plan["tool"]]
    return next(t for t in TOOLS.values() if test_id in t.tests and t.__class__.__module__.startswith("hiraku.solvers.shelf"))


def run_params(lc):
    """The run params the plan's step gives analyze (its params), with the slow test's element size for a HEAVY one."""
    plan = PLANS.get(lc["load_case_id"]) or PLANS.get(lc["test_id"]) or {}
    return {**copy.deepcopy(plan.get("params") or {}), **HEAVY.get(lc["test_id"], {})}


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
        # each case on its plan's tool (C1 is judged by K2's crash_beam, N1 by modal_shell) with the plan's params
        cls.runs = {key: TOOLS[plan_for(lc)["tool"]].run(DESIGN, lc, PRED, run_params(lc)) for key, lc in cls.cases.items()
                    if SLOW or lc["test_id"] not in HEAVY}

    def test_every_test_has_a_builder(self):
        for tid in OURS:
            self.assertIn(tid, BUILDERS)
            self.assertIn(tid, {t["id"] for t in CATALOG.tests})

    def test_load_cases_fit_the_contract_and_run(self):
        for tid, lc in self.cases.items():
            contract.validate("load_case", lc)
            if tid not in self.runs:  # HEAVY: analysed in the slow tests only
                continue
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
            if key not in self.runs:  # HEAVY: analysed in the slow tests only
                continue
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


def hop_limit_by_hand(design, loading):
    """1.2 x the higher axle's f_hop = (1 / 2 pi) sqrt((k_w + 60 N/mm) / m_u): k_w the design's wheel rate
    (suspension.stiffness_N_per_mm), m_u K2's unsprung mass of the corner (build_vehicle)."""
    from hiraku.contract.loading import on_board
    from hiraku.solvers.k2_vehicle.model import build_vehicle

    m_u = {c.id: c.m_u for c in build_vehicle(on_board(design, loading)).corners}
    sup = design["supports"]
    x_mid = sum(s["position_mm"][0] for s in sup) / len(sup)
    f = {}
    for s in sup:
        axle = "front" if s["position_mm"][0] > x_mid else "rear"
        k_w = s["suspension"]["stiffness_N_per_mm"]
        f.setdefault(axle, []).append(math.sqrt((k_w + 60.0) * 1000.0 / m_u[s["id"]]) / (2.0 * math.pi))
    return 1.2 * max(sum(v) / len(v) for v in f.values())


class N1HopTest(unittest.TestCase):
    """N1 judged on first_frame_involving_mode >= 1.2 x the wheel hop, on modal_shell with the suspension linkage."""

    @classmethod
    def setUpClass(cls):
        cls.lc = BUILDERS["N1"](spec("N1"), REQ, DESIGN)

    def test_limit_on_v1(self):
        c = self.lc["criterion"]
        self.assertEqual((c["quantity"], c["op"], c["limit"]["unit"]), ("first_frame_involving_mode", ">=", "Hz"))
        self.assertNotIn("limit_from", c)
        self.assertAlmostEqual(c["limit"]["value"], hop_limit_by_hand(DESIGN, self.lc["params"]["loading"]), places=9)
        self.assertAlmostEqual(c["limit"]["value"], 12.713, places=3)  # rear: 20 N/mm, 18.05 kg, f_hop 10.59 Hz
        for words in ("確かめていない", "90 N/mm", "11.8 Hz", "RECSb40", "13.18 Hz", "+11.6 %", "2026-11-06", "10.59 Hz"):
            self.assertIn(words, c["basis"])
        # the rotation at Q50 x 1.2 (the old criterion, 7.07 Hz) lies below: still in the title and the plan
        self.assertIn("7.07 Hz", c["basis"])
        self.assertIn("車輪とモーターの回る速さ", self.lc["title"])
        self.assertTrue({"wheel_rotation_frequency", "motor_rotation_frequency"}
                        <= {p["quantity"] for p in PLANS["N1"]["predictions"]})

    def test_limit_with_another_spring_rate(self):
        from hiraku import designgen

        v2 = designgen.regenerate(DESIGN, {"rear_spring_rate": {"value": 40.0, "unit": "N/mm"}})
        self.assertEqual({s["suspension"]["stiffness_N_per_mm"] for s in v2["supports"]}, {20.0, 40.0})
        lc = BUILDERS["N1"](spec("N1"), REQ, v2)
        got = lc["criterion"]["limit"]["value"]
        self.assertAlmostEqual(got, hop_limit_by_hand(v2, lc["params"]["loading"]), places=9)
        self.assertGreater(got, self.lc["criterion"]["limit"]["value"])
        self.assertIn("k_w 40 N/mm", lc["criterion"]["basis"])

    def test_plan_tool_and_params(self):
        plan = PLANS["N1"]
        self.assertEqual(plan["tool"], "modal_shell")
        self.assertEqual(plan["params"], {"suspension": "linkage", "tyre_stiffness": {"value": 60.0, "unit": "N/mm"}})
        declared = {p.name: p for p in TOOLS["modal_shell"].run_params}
        for name, value in plan["params"].items():
            declared[name].check(value, "run parameter")
        em = plan["expected_mismatch"]
        self.assertEqual(em["quantity"], self.lc["criterion"]["quantity"])
        self.assertTrue(em["tool_decides"] and em["tool_decides_reason"] and em["cause"] and em["fix"] and em["source"])
        self.assertNotIn("safe_side", em)
        for p in plan["predictions"]:
            self.assertIn(p["formula_id"], TOOLS["modal_shell"].formulas)

    def test_prediction_is_the_beam_tools_first_frame_mode_and_misses_the_linkage(self):
        p = next(p for p in PLANS["N1"]["predictions"] if p["quantity"] == "first_frame_involving_mode")
        extra = p["inputs_from"](DESIGN, self.lc)
        pred = FORMULAS.evaluate(p["formula_id"], {k: resolve(r, DESIGN, self.lc, extra) for k, r in p["inputs"].items()})
        f = pred["outputs"][p["output"]]["value"]
        beam = TOOLS["modal"].run(DESIGN, dict(self.lc, params={**self.lc["params"], "n_modes": {"value": 20, "unit": "1"}}),
                                  PRED, {})
        self.assertAlmostEqual(f, {r["quantity"]: r["value"] for r in beam["results"]}["first_frame_mode"], places=9)
        self.assertAlmostEqual(f, V1_LINKAGE["results"]["first_frame_mode_beam"], delta=0.01)
        # against v1's 20 mm linkage record (the roll bar's mode): the plan's expected mismatch really misses
        modes = V1_LINKAGE["modes"]
        from hiraku.solvers.shelf.modal_shell import first_frame_involving

        i = first_frame_involving([m["kind"] for m in modes], [{"kinetic": m["kinetic"]} for m in modes])
        self.assertGreater(abs(modes[i]["frequency_Hz"] - f) / f, self.lc["tolerance_rel"])


class RopsBuilderTest(unittest.TestCase):
    """R4: one load case, R4-rollover, judged by K2's rollover_beam (the rollover on the tilt table with the frame as
    beams); the hand calculation is the static push of OECD Code 4 (borrowed) after the path check."""

    @classmethod
    def setUpClass(cls):
        cls.rb = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))  # has the roll bar
        cls.lc = BUILDERS["R4"](CATALOG.test("R4"), REQ, cls.rb)

    def test_load_case_fits_the_contract(self):
        contract.validate("load_case", self.lc)
        self.assertEqual(self.lc["load_case_id"], "R4-rollover")
        self.assertEqual(self.lc["domain"], CATALOG.test("R4")["domain"])

    def test_values_are_code_4_at_the_reference_mass(self):
        from hiraku.catalog.builders.analysis import R4_CODE4, R4_REFERENCE_MASS_KG

        p = self.lc["params"]
        self.assertEqual((p["side_energy"], p["rear_energy"], p["crush_force"]),
                         ({"value": 875.0, "unit": "J"}, {"value": 700.0, "unit": "J"}, {"value": 10000.0, "unit": "N"}))
        self.assertEqual(p["side_energy"]["value"], R4_CODE4["side"] * R4_REFERENCE_MASS_KG)
        self.assertAlmostEqual(p["rear_fraction"]["value"], 1 / 6)
        self.assertEqual(p["loading"], "two_cargo")
        given = dict(CATALOG.test("R4"), params={"crush_force": {"value": 12, "unit": "kN"}})
        self.assertEqual(BUILDERS["R4"](given, REQ, self.rb)["params"]["crush_force"], {"value": 12000.0, "unit": "N"})

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
        lc = BUILDERS["R4"](CATALOG.test("R4"), req, self.rb)
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


if __name__ == "__main__":
    unittest.main()

"""A design Claude writes (docs/contract.md Claude が書く設計): write_design makes version 1 and then the next
versions of a hand-written serving robot frame, check_design says what is wrong with a written design, and a
tool that does not apply to a design says why (its applies(design), else applies_to).

The design is written here (no archetype, no variables): four legs 500 x 400 mm, shelves at z 400 and 850 mm,
20 x 20 x 1.6 mm square tube, wheels in front and casters behind, two trays, 10 kg of cargo and a 3 kg battery.
Its sizes and the steel's values are the test's assumptions, not checked against a source. The requirements are
the fixed off-road file's with Q30 empty and Q40 10 kg (the catalog does not yet build a cargo-only loading for a
design without occupants; the empty loading lets the builders run on it).
"""

import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hiraku import contract
from hiraku.agent.session import start_run
from hiraku.catalog import read_data
from hiraku.tools import Attribution, ToolError, rules
from hiraku.tools.functions import check_design, revise_design, write_design
from tests.tools import fakes
from tests.tools.helpers import REQUIREMENTS, RunCase

WHEEL_R = 50.0
Z_FOOT, Z_LOW, Z_TOP = 80.0, 400.0, 850.0
CORNERS = {"F": 0.0, "R": -500.0}  # x forward, the origin under the front axle
SIDES = {"L": 200.0, "R": -200.0}  # y to the left


def serving_robot() -> dict:
    """The serving robot's frame as Claude would write it for write_design (no design_version, parent_version,
    change_reason or archetype)."""
    nodes, members = [], []
    for c, x in CORNERS.items():
        for s, y in SIDES.items():
            k = c + s
            for lvl, z in (("foot", Z_FOOT), ("low", Z_LOW), ("top", Z_TOP)):
                nodes.append({"id": f"{lvl}_{k}", "xyz_mm": [x, y, z], "joint": "weld"})
            members += [{"id": f"leg_lo_{k}", "node_a": f"foot_{k}", "node_b": f"low_{k}", "section": "sq20",
                         "material": "steel", "name": f"脚 {k} 下"},
                        {"id": f"leg_hi_{k}", "node_a": f"low_{k}", "node_b": f"top_{k}", "section": "sq20",
                         "material": "steel", "name": f"脚 {k} 上"}]
    for lvl in ("foot", "low", "top"):
        for a, b in (("FL", "FR"), ("RL", "RR"), ("FL", "RL"), ("FR", "RR")):
            members.append({"id": f"{lvl}_{a}_{b}", "node_a": f"{lvl}_{a}", "node_b": f"{lvl}_{b}", "section": "sq20",
                            "material": "steel", "name": f"{lvl} の枠 {a}-{b}"})
    supports = [{"id": c + s, "kind": "wheel" if c == "F" else "caster", "position_mm": [x, y, WHEEL_R],
                 "radius_mm": WHEEL_R, "width_mm": 25.0, "suspension": {"type": "rigid"},
                 "actuation": {"drive": c == "F", "brake": c == "F", "steer": False}, "frame_node": f"foot_{c + s}"}
                for c, x in CORNERS.items() for s, y in SIDES.items()]
    mid = (CORNERS["F"] + CORNERS["R"]) / 2
    corners = ("FL", "FR", "RL", "RR")

    def part(pid, name, kind, z, size, kg, lvl, **more):
        return {"id": pid, "name": name, "kind": kind, "box": {"center_mm": [mid, 0.0, z], "size_mm": size},
                "mass_kg": kg, "carried_by": [f"{lvl}_{k}" for k in corners], **more}

    parts = [part("tray_top", "上の棚板", "body", Z_TOP + 11, [520, 420, 2], 1.5, "top", system="body"),
             part("tray_low", "下の棚板", "body", Z_LOW + 11, [520, 420, 2], 1.5, "low", system="body"),
             part("dishes", "上の棚の荷", "cargo", Z_TOP + 62, [400, 300, 100], 10.0, "top", system="cargo",
                  connects_to=[{"to": "tray_top", "via": "rest", "carries": "load"}]),
             part("battery", "電池", "battery", Z_FOOT + 40, [200, 150, 60], 3.0, "foot", system="battery")]
    parts += [{"id": f"wheel_{s['id']}", "name": f"車輪 {s['id']}", "kind": "wheel",
               "box": {"center_mm": s["position_mm"], "size_mm": [100, 25, 100]}, "mass_kg": 0.4,
               "carried_by": [s["frame_node"]], "connects_to": [{"to": s["frame_node"], "via": "bolt", "carries": "load"}]}
              for s in supports]
    return {"name": "配膳ロボットの骨組み", "variables": {}, "supports": supports, "parts": parts, "nodes": nodes,
            "members": members,
            "sections": {"sq20": {"shape": "rect_tube", "dims_mm": {"b": 20.0, "h": 20.0, "t": 1.6},
                                  "source": "test assumption (not checked against a catalogue)"}},
            "materials": {"steel": {"name": "carbon steel tube (test)", "E_MPa": 205000.0, "nu": 0.3,
                                    "density_kg_m3": 7850.0, "yield_MPa": 245.0,
                                    "source": "test assumption, not checked", "verified": False}}}


def robot_requirements(folder: Path, tests=("R1",)) -> Path:
    req = read_data(REQUIREMENTS)
    req.pop("design_file")
    req["tests"] = list(tests)
    values = {"Q30": "empty", "Q40": 10}
    req["requirements"] = [dict(r, value=values[r["key"]]) if r["key"] in values else r for r in req["requirements"]]
    path = folder / "requirements.json"
    path.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
    return path


class WrittenCase:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.ctx = start_run(robot_requirements(root), root=root / "runs", formulas=fakes.FakeFormulas(),
                             tools=fakes.shelf())
        self.design = serving_robot()

    def kinds(self):
        return [e["kind"] for e in self.ctx.store.events()]

    def assert_events_fit(self):
        for e in self.ctx.store.events():
            self.assertEqual(contract.errors("event", e), [], e["kind"])


class WriteDesignTest(WrittenCase, unittest.TestCase):
    def test_the_first_call_makes_version_1(self):
        out = write_design(self.ctx, {"design": self.design, "reason": "配膳ロボの型が無いので骨組みを書いた"}, "designer")
        self.assertEqual((out["design_version"], out["parent_version"]), (1, None))
        self.assertEqual([lc["load_case_id"] for lc in out["load_cases"]], ["R1-step100"])
        self.assertEqual(out["load_cases"][0]["owner"], "cae.strength")
        d = self.ctx.store.design()
        self.assertEqual((d["design_version"], d["parent_version"], d["change_reason"]),
                         (1, None, "配膳ロボの型が無いので骨組みを書いた"))
        self.assertNotIn("archetype", d)
        self.assertEqual(d["nodes"], self.design["nodes"])  # the design as written
        kinds = self.kinds()
        self.assertLess(kinds.index("design.created"), kinds.index("project.updated"))
        self.assertEqual([j["state"] for j in self.ctx.store.judgements()], ["pending"])
        self.assertEqual(out["check"]["mass_kg"], sum(p["mass_kg"] for p in self.design["parts"]))
        self.assertEqual(out["check"]["interferences"]["count"], 0)
        self.assertIsNone(rules.design_refusal(self.ctx))
        self.assert_events_fit()

    def test_the_second_call_makes_the_next_version(self):
        write_design(self.ctx, {"design": self.design, "reason": "版 1"}, "designer")
        thicker = copy.deepcopy(self.design)
        thicker["sections"]["sq20"]["dims_mm"]["t"] = 2.0
        out = write_design(self.ctx, {"design": thicker, "reason": "R1 の応力に答えて管を厚くした"}, "designer")
        self.assertEqual((out["design_version"], out["parent_version"]), (2, 1))
        self.assertEqual(out["redo"], ["R1-step100"])
        self.assertEqual(out["revisions_left"], self.ctx.project["round_limit"] - 1)
        self.assertEqual(self.ctx.version, 2)
        self.assertEqual(self.ctx.store.design()["sections"]["sq20"]["dims_mm"]["t"], 2.0)
        self.assertGreater(out["check"]["frame_mass_kg"], 0)
        kinds = self.kinds()
        self.assertEqual(kinds.count("design.created"), 2)
        self.assertIn("load_cases.created", kinds[kinds.index("design.created", kinds.index("design.created") + 1):])
        self.assertEqual([j["state"] for j in self.ctx.store.judgements(2)], ["pending"])
        self.assert_events_fit()

    def test_the_rounds_and_the_cost_limit_hold(self):
        write_design(self.ctx, {"design": self.design, "reason": "版 1"}, "designer")
        with mock.patch.dict(self.ctx.project, {"round_limit": 0}):
            with self.assertRaisesRegex(ToolError, "round_limit"):
                write_design(self.ctx, {"design": self.design, "reason": "もう一度"}, "designer")
        with mock.patch.dict(self.ctx.project, {"cost_usd": 999.0}):
            with self.assertRaisesRegex(ToolError, "費用"):
                write_design(self.ctx, {"design": self.design, "reason": "もう一度"}, "designer")
        self.assertEqual(self.ctx.version, 1)

    def test_refusals(self):
        args = {"design": self.design, "reason": "書いた"}
        with self.assertRaisesRegex(ToolError, "設計役"):
            write_design(self.ctx, args, "lead")
        with self.assertRaisesRegex(ToolError, "reason"):
            write_design(self.ctx, dict(args, reason=" "), "designer")
        with self.assertRaisesRegex(ToolError, "入力の形"):
            write_design(self.ctx, {"design": self.design}, "designer")
        with self.assertRaisesRegex(ToolError, "modules"):
            write_design(self.ctx, dict(args, design=dict(self.design, modules=[{"module_id": "frame"}])), "designer")
        with self.assertRaisesRegex(ToolError, "tricycle"):
            write_design(self.ctx, dict(args, based_on="tricycle"), "designer")
        self.assertEqual(self.ctx.version, 0)

    def test_every_problem_is_said_at_once(self):
        bad = copy.deepcopy(self.design)
        bad["members"][0]["node_b"] = "nowhere"
        bad["members"][1]["section"] = "sq99"
        bad["parts"][0]["carried_by"] = ["ghost"]
        with self.assertRaises(ToolError) as cm:
            write_design(self.ctx, {"design": bad, "reason": "書いた"}, "designer")
        said = str(cm.exception)
        for word in ("nowhere", "sq99", "ghost", "(1)", "(3)", "write_design を呼び直して"):
            self.assertIn(word, said)
        self.assertEqual(self.ctx.version, 0)
        self.assertNotIn("design.created", self.kinds())

    def test_the_tool_sets_the_version_fields_and_drops_the_archetype(self):
        given = dict(self.design, design_version=7, parent_version=3, change_reason="x", archetype="care_bed")
        out = write_design(self.ctx, {"design": given, "reason": "ベッドの骨組みを下書きに", "based_on": "care_bed"},
                           "designer")
        d = self.ctx.store.design()
        self.assertEqual((d["design_version"], d["parent_version"]), (1, None))
        self.assertNotIn("archetype", d)  # the design is one Claude wrote; based_on is not a design field
        self.assertIn("care_bed", d["change_reason"])
        self.assertEqual(out["based_on"], "care_bed")
        self.assertIn("archetype", out["check"]["notes"][0])

    def test_a_written_design_is_revised_by_write_design(self):
        write_design(self.ctx, {"design": self.design, "reason": "版 1"}, "designer")
        with self.assertRaisesRegex(ToolError, "write_design で次の版"):
            revise_design(self.ctx, {"changes": {"track": {"value": 1200.0, "unit": "mm"}}, "reason": "r"}, "designer")

    def test_through_mcp_a_call_without_its_caller_is_refused(self):
        from hiraku.tools.mcp import HANDLERS, make_handler

        out = asyncio.run(make_handler(self.ctx, Attribution(), "write_design", HANDLERS["write_design"])(
            {"design": self.design, "reason": "書いた"}))
        self.assertTrue(out["is_error"])
        self.assertEqual(self.ctx.version, 0)

    def test_hk4_asks_for_write_design_when_no_archetype_suits(self):
        self.assertIn("propose_design", rules.stop_reason(self.ctx))
        with mock.patch("hiraku.tools.functions.archetypes", return_value=[]):
            reason = rules.stop_reason(self.ctx)
        self.assertIn("write_design", reason)
        self.assertNotIn("propose_design", reason)


class ModulesTest(unittest.TestCase):
    """A design with modules (ladder_4w): the module designers propose and the packager builds, not write_design."""

    def test_write_design_is_refused_on_a_design_with_modules(self):
        from hiraku.tools.functions import propose_design
        from tests.agent.test_first_design import requirements_without_design

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = start_run(requirements_without_design(root, ["R1"]), root=root / "runs",
                            formulas=fakes.FakeFormulas(), tools=fakes.shelf())
            propose_design(ctx, {"archetype": "ladder_4w", "reason": "要件から"}, "designer")
            with self.assertRaisesRegex(ToolError, "モジュールに分かれている"):
                write_design(ctx, {"design": serving_robot(), "reason": "書き直す"}, "designer")
            self.assertEqual(ctx.version, 1)


class CheckDesignTest(WrittenCase, unittest.TestCase):
    def problems(self, design):
        return check_design(self.ctx, {"design": design})["problems"]

    def test_a_whole_design_passes_with_its_masses(self):
        got = check_design(self.ctx, {"design": self.design})
        self.assertTrue(got["ok"], got["problems"])
        self.assertEqual(got["problems"], [])
        self.assertEqual(got["counts"], {"nodes": 12, "members": 20, "parts": 8, "supports": 4})
        self.assertEqual(got["mass_kg"], sum(p["mass_kg"] for p in self.design["parts"]))
        self.assertEqual(got["cargo_mass_kg"], 10.0)
        self.assertEqual(got["Q40_kg"], 10.0)
        self.assertEqual(got["occupants"], 0)
        self.assertEqual(got["interferences"], {"count": 0, "first": []})
        self.assertGreater(got["frame_mass_kg"], 0)
        self.assertEqual(self.ctx.version, 0)  # nothing written
        self.assertNotIn("design.created", self.kinds())

    def test_broken_references_are_named(self):
        bad = copy.deepcopy(self.design)
        bad["members"][0]["node_a"] = "nope"
        bad["members"][1]["material"] = "unobtainium"
        bad["parts"][2]["connects_to"] = [{"to": "no_tray", "via": "rest"}]
        bad["supports"][0]["frame_node"] = "gone"
        bad["nodes"].append(dict(bad["nodes"][0]))  # the same id twice
        said = " | ".join(self.problems(bad))
        for word in ("nope", "unobtainium", "no_tray", "gone", "id が重なって"):
            self.assertIn(word, said)

    def test_a_member_off_the_supports_is_named(self):
        bad = copy.deepcopy(self.design)
        bad["nodes"] += [{"id": "sign_a", "xyz_mm": [0.0, 0.0, 1200.0]}, {"id": "sign_b", "xyz_mm": [0.0, 0.0, 1400.0]}]
        bad["members"].append({"id": "sign_post", "node_a": "sign_a", "node_b": "sign_b", "section": "sq20",
                               "material": "steel"})
        bad["parts"][3]["carried_by"] = ["sign_a"]
        said = " | ".join(self.problems(bad))
        self.assertIn("sign_post", said)
        self.assertIn("battery", said)
        no_frame_node = copy.deepcopy(self.design)
        for s in no_frame_node["supports"]:
            del s["frame_node"]
        self.assertIn("frame_node", " | ".join(self.problems(no_frame_node)))

    def test_lengths_in_another_unit_are_named(self):
        metres = copy.deepcopy(self.design)
        for n in metres["nodes"]:
            n["xyz_mm"] = [x / 1000.0 for x in n["xyz_mm"]]
        self.assertIn("1 mm より短い", " | ".join(self.problems(metres)))
        microns = copy.deepcopy(self.design)
        for n in microns["nodes"]:
            n["xyz_mm"] = [x * 1000.0 for x in n["xyz_mm"]]
        said = " | ".join(self.problems(microns))
        self.assertIn("20 m", said)
        self.assertIn("原点から", said)

    def test_a_box_around_the_frame_shows_as_interference(self):
        boxed = copy.deepcopy(self.design)
        boxed["parts"].append({"id": "frame", "name": "骨組み", "kind": "frame", "mass_kg": 0.0,
                               "box": {"center_mm": [-250.0, 0.0, 465.0], "size_mm": [520, 420, 770]}})
        got = check_design(self.ctx, {"design": boxed})
        self.assertTrue(got["ok"])  # a note, not a refusal: the assembly test (H1) judges it
        self.assertGreater(got["interferences"]["count"], 0)
        self.assertTrue(any("frame" in (i["a"] + i["b"]) for i in got["interferences"]["first"]))
        notes = " | ".join(got["notes"])
        self.assertIn("骨組みを囲む箱", notes)
        self.assertIn("carried_by の無い部品: frame", notes)

    def test_cargo_against_q40(self):
        light = copy.deepcopy(self.design)
        next(p for p in light["parts"] if p["kind"] == "cargo")["mass_kg"] = 5.0
        got = check_design(self.ctx, {"design": light})
        self.assertTrue(got["ok"])
        self.assertEqual((got["cargo_mass_kg"], got["Q40_kg"]), (5.0, 10.0))
        self.assertIn("Q40", " | ".join(got["notes"]))

    def test_without_design_the_current_version(self):
        with self.assertRaisesRegex(ToolError, "まだ設計の版がありません"):
            check_design(self.ctx, {})
        write_design(self.ctx, {"design": self.design, "reason": "版 1"}, "designer")
        got = check_design(self.ctx, {})
        self.assertTrue(got["ok"])
        self.assertEqual(got["design_version"], 1)


class AppliesTest(RunCase, unittest.TestCase):
    """A tool's applies(design) decides whether it applies (before applies_to): its sentence refuses the analysis,
    keeps the tool from setting the load case's stage, and is named in HK4's reason."""

    WHY = "frame_static はこの設計に当てはまりません (試験の文)"

    def test_the_tools_sentence_is_said(self):
        from hiraku.tools.functions import analyze, not_applying
        from hiraku.tools.shelf import load_case_stage

        lc = self.ctx.load_case("R1-step100")
        self.assertEqual(load_case_stage(self.ctx, lc), 1)
        self.tools["frame_static"].applies = lambda design: self.WHY
        self.assertEqual(not_applying(self.ctx, "frame_static"), self.WHY)
        pred = self.predict_r1({"value": 1000.0, "unit": "N"})
        with self.assertRaisesRegex(ToolError, "試験の文"):
            analyze(self.ctx, {"tool": "frame_static", "load_case_id": "R1-step100",
                               "prediction_ids": [pred["prediction_id"]]})
        self.assertIsNone(load_case_stage(self.ctx, lc))
        self.assertNotIn("frame_static", rules._usable_tools(self.ctx, "R1"))
        self.assertIn(self.WHY, rules.stop_reason(self.ctx))

    def test_applies_answers_none_or_false(self):
        from hiraku.tools.functions import not_applying

        self.tools["frame_static"].applies = lambda design: None
        self.ctx.specs["frame_static"] = dict(self.ctx.specs["frame_static"], applies_to=["space_frame_utv"])
        self.assertIsNone(not_applying(self.ctx, "frame_static"))  # the tool's own check comes before applies_to
        self.tools["frame_static"].applies = lambda design: False
        self.assertIn("当てはまりません", not_applying(self.ctx, "frame_static"))


if __name__ == "__main__":
    unittest.main()

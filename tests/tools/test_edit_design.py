"""edit_design (docs/contract.md Claude が書く設計, tool_input.edit_design): the next version of a design Claude wrote,
as edits applied in order to the current version, on the hand-written serving robot frame of test_write_design.
An edit that names something not there or would break a reference is refused by its number, before anything is
written; the edited design then goes the same way as write_design's later versions."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.agent.session import start_run
from hiraku.tools import Attribution, ToolError
from hiraku.tools.functions import check_design, edit_design, write_design
from tests.tools import fakes
from tests.tools.test_write_design import WrittenCase, serving_robot

SQ25 = {"shape": "rect_tube", "dims_mm": {"b": 25.0, "h": 25.0, "t": 2.0},
        "source": "test assumption (not checked against a catalogue)"}
ALU = {"name": "aluminium tube (test)", "E_MPa": 69000.0, "nu": 0.33, "density_kg_m3": 2700.0, "yield_MPa": 160.0,
       "source": "test assumption, not checked", "verified": False}


class EditCase(WrittenCase):
    def setUp(self):
        super().setUp()
        write_design(self.ctx, {"design": self.design, "reason": "版 1"}, "designer")

    def edit(self, edits, reason="直した"):
        return edit_design(self.ctx, {"edits": edits, "reason": reason}, "designer")

    def refused(self, edits, *words):
        with self.assertRaises(ToolError) as cm:
            self.edit(edits)
        said = str(cm.exception)
        for w in words:
            self.assertIn(w, said)
        self.assertEqual(self.ctx.version, 1)  # nothing written
        self.assertEqual(self.kinds().count("design.created"), 1)
        return said

    @staticmethod
    def by_id(design, key, id_):
        return next((x for x in design[key] if x["id"] == id_), None)


class EditDesignTest(EditCase, unittest.TestCase):
    def test_every_op_applies_and_version_2_is_made(self):
        edits = [
            {"op": "split_member", "id": "top_FL_FR"},
            {"op": "add_node", "id": "mast", "xyz_mm": [0.0, 0.0, 1000.0], "joint": "weld"},
            {"op": "move_node", "id": "mast", "xyz_mm": [0.0, 0.0, 1050.0]},
            {"op": "set_section", "key": "sq25", "section": SQ25},
            {"op": "add_member", "member": {"id": "mast_post", "node_a": "top_FL_FR_mid", "node_b": "mast",
                                            "section": "sq20", "material": "steel", "name": "柱"}},
            {"op": "assign_section", "members": ["mast_post", "top_FL_FR_a"], "section": "sq25"},
            {"op": "add_part", "part": {"id": "lamp", "name": "灯", "kind": "body", "mass_kg": 0.5,
                                        "box": {"center_mm": [0.0, 0.0, 1100.0], "size_mm": [40, 40, 40]},
                                        "carried_by": ["mast"]}},
            {"op": "set_part", "id": "battery", "fields": {"mass_kg": 4.0, "system": None}},
            {"op": "remove_part", "id": "tray_low"},
            {"op": "set_support", "id": "FL", "fields": {"width_mm": 30.0}},
            {"op": "set_material", "key": "alu", "material": ALU},
            {"op": "set_variable", "name": "shelf_gap", "value": {"value": 450.0, "unit": "mm"},
             "basis": "assumption"},
            {"op": "add_node", "id": "spare", "xyz_mm": [-250.0, 0.0, 400.0]},
            {"op": "add_member", "member": {"id": "brace", "node_a": "low_FL", "node_b": "top_FR", "section": "sq20",
                                            "material": "steel"}},
            {"op": "remove_member", "id": "brace"},
            {"op": "remove_node", "id": "spare"},
        ]
        out = self.edit(edits, "R1 に答えて上の棚に柱を足した")
        self.assertEqual((out["design_version"], out["parent_version"]), (2, 1))
        self.assertEqual(out["change_reason"], "R1 に答えて上の棚に柱を足した")
        self.assertEqual(len(out["edits_applied"]), len(edits))
        for line, e in zip(out["edits_applied"], edits):
            self.assertTrue(line.startswith(e["op"]), line)
        self.assertIn("中点 (0, 0, 850) mm", out["edits_applied"][0])
        self.assertEqual(out["redo"], ["R1-step20"])
        self.assertEqual(out["revisions_left"], self.ctx.project["round_limit"] - 1)
        self.assertEqual(out["check"]["counts"], {"nodes": 14, "members": 22, "parts": 8, "supports": 4})

        d = self.ctx.store.design()
        self.assertEqual((d["design_version"], d["parent_version"], d["written_by"]), (2, 1, "claude"))
        self.assertEqual(self.by_id(d, "nodes", "mast")["xyz_mm"], [0.0, 0.0, 1050.0])
        self.assertEqual(self.by_id(d, "nodes", "mast")["joint"], "weld")
        self.assertIsNone(self.by_id(d, "nodes", "spare"))
        self.assertEqual(self.by_id(d, "members", "mast_post")["section"], "sq25")
        self.assertEqual(self.by_id(d, "members", "top_FL_FR_a")["section"], "sq25")
        self.assertEqual(self.by_id(d, "members", "top_FL_FR_b")["section"], "sq20")
        self.assertIsNone(self.by_id(d, "members", "brace"))
        self.assertEqual(d["sections"]["sq25"], SQ25)
        self.assertEqual(d["materials"]["alu"], ALU)
        self.assertEqual(d["variables"]["shelf_gap"], {"value": 450.0, "unit": "mm", "basis": "assumption"})
        battery = self.by_id(d, "parts", "battery")
        self.assertEqual(battery["mass_kg"], 4.0)
        self.assertNotIn("system", battery)  # null takes the field out
        self.assertIsNone(self.by_id(d, "parts", "tray_low"))
        self.assertEqual(self.by_id(d, "parts", "lamp")["carried_by"], ["mast"])
        self.assertEqual(self.by_id(d, "supports", "FL")["width_mm"], 30.0)
        self.assertEqual(self.by_id(d, "supports", "FR")["width_mm"], 25.0)

        # version 1 is untouched, version 2's load cases are built again and its tiles start pending
        self.assertIsNone(self.by_id(self.ctx.store.design(1), "members", "mast_post"))
        kinds = self.kinds()
        self.assertEqual(kinds.count("design.created"), 2)
        second = kinds.index("design.created", kinds.index("design.created") + 1)
        self.assertIn("load_cases.created", kinds[second:])
        self.assertEqual([j["state"] for j in self.ctx.store.judgements(2)], ["pending"])
        self.assert_events_fit()

    def test_split_member_makes_two_that_still_reach_the_supports(self):
        out = self.edit([{"op": "split_member", "id": "leg_lo_FL"}])
        d = self.ctx.store.design()
        self.assertIsNone(self.by_id(d, "members", "leg_lo_FL"))
        a, b = self.by_id(d, "members", "leg_lo_FL_a"), self.by_id(d, "members", "leg_lo_FL_b")
        self.assertEqual((a["node_a"], a["node_b"]), ("foot_FL", "leg_lo_FL_mid"))
        self.assertEqual((b["node_a"], b["node_b"]), ("leg_lo_FL_mid", "low_FL"))
        for half in (a, b):
            self.assertEqual((half["section"], half["material"]), ("sq20", "steel"))
        self.assertEqual(self.by_id(d, "nodes", "leg_lo_FL_mid")["xyz_mm"], [0.0, 200.0, 240.0])
        self.assertEqual(len(d["members"]), 21)
        got = check_design(self.ctx, {})
        self.assertTrue(got["ok"], got["problems"])
        self.assertNotIn("どの部材にも部品にも使われていない節点", " | ".join(out["check"]["notes"]))

    def test_split_member_at_a_given_node(self):
        out = self.edit([{"op": "add_node", "id": "p", "xyz_mm": [0.0, 0.0, 400.0]},
                         {"op": "split_member", "id": "low_FL_FR", "at_node": "p"},
                         {"op": "split_member", "id": "low_RL_RR", "at_node": "q"}])
        d = self.ctx.store.design()
        self.assertEqual(self.by_id(d, "members", "low_FL_FR_a")["node_b"], "p")
        self.assertEqual(self.by_id(d, "members", "low_FL_FR_b")["node_a"], "p")
        self.assertEqual(self.by_id(d, "nodes", "q")["xyz_mm"], [-500.0, 0.0, 400.0])  # not there: made at the middle
        self.assertNotIn("中点", out["edits_applied"][1])
        self.assertIn("中点", out["edits_applied"][2])

    def test_a_split_node_off_the_line_is_said(self):
        out = self.edit([{"op": "add_node", "id": "p", "xyz_mm": [0.0, 0.0, 450.0]},
                         {"op": "split_member", "id": "low_FL_FR", "at_node": "p"}])
        self.assertIn("50 mm 離れて", out["edits_applied"][1])

    def test_a_support_is_added_and_removed(self):
        caster = {"id": "C", "kind": "wheel", "position_mm": [-250.0, 0.0, 40.0], "radius_mm": 40.0, "width_mm": 25.0,
                  "suspension": {"type": "rigid"}, "actuation": {"drive": False, "brake": False, "steer": False},
                  "frame_node": "foot_RL"}
        self.refused([{"op": "add_support", "support": {**caster, "id": "FL"}}], "1 番目", "支え FL はもうあります")
        self.refused([{"op": "add_support", "support": {**caster, "frame_node": "nowhere"}}],
                     "1 番目", "frame_node 'nowhere' は節点")
        out = self.edit([{"op": "add_support", "support": caster}])
        self.assertEqual(out["edits_applied"], ["add_support C (節点 foot_RL)"])
        self.assertEqual(self.by_id(self.ctx.store.design(), "supports", "C"), caster)
        out = self.edit([{"op": "remove_support", "id": "C"}])
        self.assertIsNone(self.by_id(self.ctx.store.design(), "supports", "C"))
        self.assertEqual(out["check"]["counts"]["supports"], 4)

    def test_removing_a_support_in_use_is_refused(self):
        self.refused([{"op": "set_part", "id": "battery",
                       "fields": {"connects_to": [{"to": "FL", "via": "bolt", "carries": "load"}]}},
                      {"op": "remove_support", "id": "FL"}],
                     "2 番目の edit (remove_support FL)", "部品 battery の connects_to")
        self.refused([{"op": "remove_support", "id": "XX"}], "1 番目", "支え XX はありません")

    def test_written_by_and_based_on_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from tests.tools.test_write_design import robot_requirements

            ctx = start_run(robot_requirements(root), root=root / "runs", formulas=fakes.FakeFormulas(),
                            tools=fakes.shelf())
            write_design(ctx, {"design": serving_robot(), "reason": "版 1", "based_on": "care_bed"}, "designer")
            edit_design(ctx, {"edits": [{"op": "set_section", "key": "sq20", "section": SQ25}], "reason": "太く"},
                        "designer")
            d = ctx.store.design()
            self.assertEqual((d["design_version"], d["written_by"], d["based_on"]), (2, "claude", "care_bed"))
            self.assertNotIn("archetype", d)


class EditRefusalTest(EditCase, unittest.TestCase):
    def test_removing_a_node_in_use_is_refused_by_its_number(self):
        said = self.refused([{"op": "add_node", "id": "x", "xyz_mm": [0.0, 0.0, 100.0]},
                             {"op": "remove_node", "id": "foot_FL"}],
                            "2 番目の edit (remove_node foot_FL)", "部材 leg_lo_FL", "支え FL の frame_node",
                            "部品 wheel_FL の carried_by", "edit_design を呼び直して")
        self.assertIn("1 つも当てて", said)

    def test_removing_a_member_in_use_is_refused_by_its_number(self):
        self.refused([{"op": "set_part", "id": "battery",
                       "fields": {"connects_to": [{"to": "foot_FL_FR", "via": "bolt", "carries": "load"}]}},
                      {"op": "remove_member", "id": "foot_FL_FR"}],
                     "2 番目の edit (remove_member foot_FL_FR)", "部品 battery の connects_to")
        self.refused([{"op": "set_part", "id": "battery",
                       "fields": {"connects_to": [{"to": "foot_FL_FR", "via": "bolt", "carries": "load"}]}},
                      {"op": "split_member", "id": "foot_FL_FR"}],
                     "2 番目の edit (split_member foot_FL_FR)", "部品 battery の connects_to")

    def test_removing_a_part_in_use_is_refused_by_its_number(self):
        self.refused([{"op": "remove_part", "id": "tray_top"}],
                     "1 番目の edit (remove_part tray_top)", "部品 dishes の connects_to")
        self.refused([{"op": "set_support", "id": "FL", "fields": {"width_mm": 30.0}},
                      {"op": "add_member", "member": {"id": "x1", "node_a": "foot_FL", "node_b": "top_FR",
                                                      "section": "sq20", "material": "steel", "part": "battery"}},
                      {"op": "remove_part", "id": "battery"}],
                     "3 番目の edit (remove_part battery)", "部材 x1 の part")

    def test_names_that_are_not_there_are_refused_by_their_number(self):
        self.refused([{"op": "set_section", "key": "sq25", "section": SQ25},
                      {"op": "assign_section", "members": ["leg_lo_FL"], "section": "sq25"},
                      {"op": "move_node", "id": "ghost", "xyz_mm": [0.0, 0.0, 0.0]}],
                     "3 番目の edit (move_node ghost)", "節点 ghost はありません")
        self.refused([{"op": "assign_section", "members": ["leg_lo_FL"], "section": "sq99"}],
                     "1 番目", "断面 'sq99' は sections にありません")
        self.refused([{"op": "assign_section", "members": ["leg_lo_FL", "nope"], "section": "sq20"}],
                     "1 番目", "部材 nope はありません")
        self.refused([{"op": "set_part", "id": "nobody", "fields": {"mass_kg": 1.0}}], "1 番目", "部品 nobody はありません")
        self.refused([{"op": "set_support", "id": "XX", "fields": {"width_mm": 1.0}}], "1 番目", "支え XX はありません")
        self.refused([{"op": "remove_member", "id": "nope"}], "1 番目", "部材 nope はありません")
        self.refused([{"op": "split_member", "id": "nope"}], "1 番目", "部材 nope はありません")
        self.refused([{"op": "remove_part", "id": "nope"}], "1 番目", "部品 nope はありません")
        self.refused([{"op": "add_member", "member": {"id": "m", "node_a": "foot_FL", "node_b": "nowhere",
                                                      "section": "sq20", "material": "steel"}}],
                     "1 番目", "node_b 'nowhere'")
        self.refused([{"op": "set_part", "id": "battery", "fields": {"carried_by": ["ghost"]}}],
                     "1 番目", "carried_by 'ghost'")
        self.refused([{"op": "set_support", "id": "FL", "fields": {"frame_node": "ghost"}}],
                     "1 番目", "frame_node 'ghost'")

    def test_ids_that_are_there_already_and_wrong_fields(self):
        self.refused([{"op": "add_node", "id": "foot_FL", "xyz_mm": [0.0, 0.0, 0.0]}], "1 番目", "もうあります")
        self.refused([{"op": "add_part", "part": dict(serving_robot()["parts"][0])}], "1 番目", "もうあります")
        self.refused([{"op": "move_node", "id": "foot_FL"}], "1 番目", "xyz_mm が要ります")
        self.refused([{"op": "remove_node", "id": "foot_FL", "xyz_mm": [0.0, 0.0, 0.0]}], "1 番目", "xyz_mm を使いません")
        self.refused([{"op": "set_section", "key": "sq20", "section": "sq20"}], "1 番目", "assign_section")
        self.refused([{"op": "assign_section", "members": ["leg_lo_FL"], "section": SQ25}], "1 番目", "set_section")
        self.refused([{"op": "set_part", "id": "battery", "fields": {"id": "batt"}}], "1 番目", "id は fields で変えません")
        self.refused([{"op": "set_part", "id": "battery", "fields": {"mass_kg": "heavy"}}], "1 番目", "契約の part")
        self.refused([{"op": "split_member", "id": "leg_lo_FL", "at_node": "foot_FL"}], "1 番目", "端の節点")
        self.refused([{"op": "add_member", "member": {"id": "leg_lo_FL_a", "node_a": "foot_FL", "node_b": "top_FR",
                                                      "section": "sq20", "material": "steel"}},
                      {"op": "split_member", "id": "leg_lo_FL"}], "2 番目", "leg_lo_FL_a")

    def test_a_design_that_fails_check_design_is_refused(self):
        said = self.refused([{"op": "add_node", "id": "a", "xyz_mm": [0.0, 0.0, 1200.0]},
                             {"op": "add_node", "id": "b", "xyz_mm": [0.0, 0.0, 1400.0]},
                             {"op": "add_member", "member": {"id": "loose", "node_a": "a", "node_b": "b",
                                                             "section": "sq20", "material": "steel"}}],
                            "loose", "check_design")
        self.assertIn("edit_design を呼び直して", said)

    def test_who_and_when(self):
        edits = [{"op": "set_section", "key": "sq25", "section": SQ25}]
        with self.assertRaisesRegex(ToolError, "設計役"):
            edit_design(self.ctx, {"edits": edits, "reason": "r"}, "lead")
        with self.assertRaisesRegex(ToolError, "reason"):
            self.edit(edits, " ")
        with self.assertRaisesRegex(ToolError, "入力の形"):
            edit_design(self.ctx, {"edits": [], "reason": "r"}, "designer")
        with self.assertRaisesRegex(ToolError, "入力の形"):
            edit_design(self.ctx, {"edits": [{"op": "paint"}], "reason": "r"}, "designer")
        from unittest import mock

        with mock.patch.dict(self.ctx.project, {"round_limit": 0}):
            with self.assertRaisesRegex(ToolError, "round_limit"):
                self.edit(edits)
        self.assertEqual(self.ctx.version, 1)

    def test_through_mcp_a_call_without_its_caller_is_refused(self):
        from hiraku.tools.mcp import HANDLERS, make_handler

        out = asyncio.run(make_handler(self.ctx, Attribution(), "edit_design", HANDLERS["edit_design"])(
            {"edits": [{"op": "set_section", "key": "sq25", "section": SQ25}], "reason": "r"}))
        self.assertTrue(out["is_error"])
        self.assertEqual(self.ctx.version, 1)


class NotAWrittenDesignTest(WrittenCase, unittest.TestCase):
    EDITS = [{"op": "set_section", "key": "sq25", "section": SQ25}]

    def test_no_version_yet(self):
        with self.assertRaisesRegex(ToolError, "write_design"):
            edit_design(self.ctx, {"edits": self.EDITS, "reason": "r"}, "designer")
        self.assertEqual(self.ctx.version, 0)

    def test_a_design_from_an_archetype_is_refused(self):
        write_design(self.ctx, {"design": self.design, "reason": "版 1"}, "designer")
        typed = {k: v for k, v in self.ctx.store.design().items() if k not in ("written_by", "based_on")}
        self.ctx.store.write_design(dict(typed, design_version=2, parent_version=1, change_reason="型から",
                                         archetype="care_bed"))
        with self.assertRaisesRegex(ToolError, "revise_design"):
            edit_design(self.ctx, {"edits": self.EDITS, "reason": "r"}, "designer")
        self.assertEqual(self.ctx.version, 2)

    def test_a_design_with_modules_is_refused(self):
        from hiraku.tools.functions import propose_design
        from tests.agent.test_first_design import requirements_without_design

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = start_run(requirements_without_design(root, ["R1"]), root=root / "runs",
                            formulas=fakes.FakeFormulas(), tools=fakes.shelf())
            propose_design(ctx, {"archetype": "ladder_4w", "reason": "要件から"}, "designer")
            with self.assertRaisesRegex(ToolError, "モジュールに分かれている"):
                edit_design(ctx, {"edits": self.EDITS, "reason": "r"}, "designer")
            self.assertEqual(ctx.version, 1)


class ContractTest(unittest.TestCase):
    def test_every_op_of_the_contract_is_handled(self):
        from hiraku.tools.functions import EDIT_OPS

        ops = contract.schema()["$defs"]["tool_input.edit_design"]["properties"]["edits"]["items"]["properties"]["op"]
        self.assertEqual(set(EDIT_OPS), set(ops["enum"]))


if __name__ == "__main__":
    unittest.main()

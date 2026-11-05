"""Designs with modules: revise_design with module is a proposal (design.change_proposed), build_version (the
packager) builds the round's proposals into one version, HK6 keeps each role inside its module."""

import unittest
from unittest import mock

from hiraku import contract
from hiraku.store import RunStore
from hiraku.tools import judge, rules
from hiraku.tools.context import ToolError
from hiraku.tools.functions import build_version, get_context, revise_design
from tests.tools.helpers import RunCase
from tests.tools.modules import ModuleCase, ModuleDesigngen

RED_R1 = {"R1-step100": {"verdict_id": "ver-0001", "load_case_id": "R1-step100", "pass": False}}


class ProposalTest(ModuleCase, unittest.TestCase):
    def propose(self, module, agent=None, **kw):
        args = {"module": module, "reason": kw.pop("reason", "R1 が赤"), **kw}
        return revise_design(self.ctx, args, agent or f"designer.{module}")

    def test_a_proposal_is_recorded_and_evented_without_a_version(self):
        with mock.patch.object(judge, "latest_verdicts", return_value=RED_R1):
            out = self.propose("occupant", changes={"seat_x": self.mm(-1000)}, choices={"seat_kind": "slide"},
                               load_case_ids=["R1-step100"])
        self.assertEqual(out["change_id"], "chg-0001")
        self.assertEqual(out["based_on_version"], 1)
        self.assertEqual(self.ctx.version, 1)  # no version made
        ev = [e for e in self.ctx.store.events() if e["kind"] == "design.change_proposed"]
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertEqual(e["agent"], "designer.occupant")
        self.assertEqual(e["data"]["agent_id"], e["agent"])
        self.assertEqual(e["design_version"], 1)
        self.assertNotIn("domain", e)
        self.assertEqual(contract.errors("module_change", e["data"]), [])
        self.assertEqual(set(e["data"]), {"change_id", "module_id", "agent_id", "reason", "load_case_ids", "changes",
                                          "choices", "design_version"})
        self.assertEqual(e["data"]["design_version"], 1)
        self.assertEqual(e["data"]["changes"], {"seat_x": {"value": -1000.0, "unit": "mm"}})
        self.assertEqual([c["change_id"] for c in self.ctx.store.pending_proposals()], ["chg-0001"])
        self.assertEqual(self.propose("powertrain", changes={"battery_x": self.mm(-950)})["change_id"], "chg-0002")
        self.assert_events_fit()

    def test_load_case_ids_are_the_versions_reds_only(self):
        verdicts = {**RED_R1, "P1-step100": {"verdict_id": "ver-0002", "load_case_id": "P1-step100", "pass": True}}
        for ids, named in ((["R1-step100", "P1-step100"], "P1-step100"), (["H1-interference"], "H1-interference")):
            with self.subTest(ids=ids), mock.patch.object(judge, "latest_verdicts", return_value=verdicts), \
                    self.assertRaisesRegex(ToolError, f"版 1 で赤.*赤でないもの: {named}。.*reason に書いて"):
                self.propose("occupant", changes={"seat_x": self.mm(-1000)}, load_case_ids=ids)
        self.assertEqual(self.ctx.store.pending_proposals(), [])  # nothing recorded
        with mock.patch.object(judge, "latest_verdicts", return_value=verdicts):
            self.assertEqual(self.propose("occupant", changes={"seat_x": self.mm(-1000)},
                                          load_case_ids=["R1-step100"])["change_id"], "chg-0001")

    def test_ids_go_on_after_reopening(self):
        self.propose("occupant", changes={"seat_x": self.mm(-1000)})
        store = RunStore.open(self.ctx.store.path)
        self.assertEqual(store.next_id("module_change"), "chg-0002")
        self.assertEqual([c["change_id"] for c in store.pending_proposals(1)], ["chg-0001"])

    def test_out_of_module_variable_is_refused_naming_its_owner(self):
        with self.assertRaises(ToolError) as e:
            self.propose("occupant", changes={"battery_x": self.mm(-950)})
        self.assertIn("battery_x", str(e.exception))
        self.assertIn("powertrain", str(e.exception))
        with self.assertRaises(ToolError) as e:
            self.propose("occupant", choices={"battery_pack": "big"})
        self.assertIn("battery_pack", str(e.exception))
        ev = self.ctx.store.events()[-1]
        self.assertEqual((ev["kind"], ev["data"]["rule"], ev["data"]["gate"]), ("hook.denied", rules.HK6, "tool"))
        self.assertEqual(self.ctx.store.pending_proposals(), [])

    def test_wrong_agent_no_module_and_packager_are_refused(self):
        for args, agent, word in (
                ({"module": "occupant", "changes": {"seat_x": self.mm(-1000)}}, "designer.frame", "designer.occupant"),
                ({"changes": {"seat_x": self.mm(-1000)}}, "designer.occupant", "module"),
                ({"module": "occupant", "changes": {"seat_x": self.mm(-1000)}}, "designer", "designer.occupant"),
                ({"module": "package", "changes": {"track": self.mm(1400)}}, "packager", "build_version"),
                ({"module": "package", "changes": {"track": self.mm(1400)}}, "designer.frame", "パッケージ役")):
            with self.subTest(agent=agent, args=args), self.assertRaises(ToolError) as e:
                revise_design(self.ctx, {**args, "reason": "x"}, agent)
            self.assertIn(word, str(e.exception))
        self.assertEqual(self.ctx.store.pending_proposals(), [])
        self.assertEqual(self.ctx.version, 1)

    def test_range_and_choice_values_are_checked_as_before(self):
        with self.assertRaises(ToolError) as e:
            self.propose("occupant", changes={"seat_x": self.mm(-100)})
        self.assertIn("範囲", str(e.exception))
        with self.assertRaises(ToolError):
            self.propose("occupant", choices={"seat_kind": "hammock"})

    def test_get_context_shows_the_callers_module_and_the_pending_proposals(self):
        self.propose("powertrain", changes={"battery_x": self.mm(-950)})
        got = get_context(self.ctx, {"sections": ["modules"]}, "designer.occupant")["modules"]
        self.assertEqual([m["module_id"] for m in got["modules"]], ["occupant"])
        mine = got["modules"][0]
        self.assertEqual(mine["variables"], {"seat_x": {"value": -900.0, "unit": "mm"}})
        self.assertEqual(mine["choices"], {"seat_kind": ["fixed", "slide"]})
        self.assertEqual(len(mine["envelope"]), 1)
        self.assertTrue(mine["interfaces"][0]["at"][0].startswith("seat_mount"))  # grouped: "<id>@<node or point>"
        self.assertEqual([c["change_id"] for c in got["pending"]], ["chg-0001"])
        self.assertEqual(got["pending"][0]["changes"], {"battery_x": "-950 mm"})
        every = get_context(self.ctx, {"sections": ["modules"]}, "packager")["modules"]
        self.assertEqual(len(every["modules"]), 6 - 1)  # package, frame, occupant, powertrain, body
        # every module at once: the spaces in outline (run 094333: the whole answer went over Claude Code's limit)
        occ = next(m for m in every["modules"] if m["module_id"] == "occupant")
        self.assertEqual(occ["envelope"]["boxes"], 1)
        self.assertEqual(set(occ["envelope"]["bounds_mm"]), {"min", "max"})
        self.assertIn("shown", every)

    def test_a_generator_refusal_says_who_changes_the_named_variable(self):
        from hiraku.tools.functions import _who_changes

        design = self.ctx.store.design()
        said = _who_changes(design, "seat_x goes 21 mm into the floor; make seat_x larger", "build_version")
        self.assertIn("designer.occupant が revise_design (module=occupant) で出し直す", said)
        self.assertIn("track", _who_changes(design, "make track wider", "build_version"))
        self.assertEqual(_who_changes(design, "no variable here", "build_version"), "値を変えて build_version を呼び直してください")


class BuildVersionTest(ModuleCase, unittest.TestCase):
    def propose(self, module, **kw):
        return revise_design(self.ctx, {"module": module, "reason": f"{module} の直し", **kw}, f"designer.{module}")

    def test_two_proposals_make_one_version_with_module_changes(self):
        self.propose("occupant", changes={"seat_x": self.mm(-1000)})
        self.propose("powertrain", choices={"battery_pack": "big"})
        out = build_version(self.ctx, {"reason": "ぶつからない", "changes": {"track": self.mm(1400)}}, "packager")
        self.assertEqual(out["design_version"], 2)
        self.assertEqual(out["module_changes"], ["chg-0001", "chg-0002", "chg-0003"])
        d = self.ctx.store.design(2)
        self.assertEqual(contract.errors("design", d), [])
        self.assertEqual(d["parent_version"], 1)
        self.assertTrue(d["change_reason"].startswith("その回の提案をまとめた (chg-0001, chg-0002)"))
        self.assertIn("ぶつからない", d["change_reason"])
        mine = d["module_changes"][-1]
        self.assertEqual((mine["module_id"], mine["agent_id"]), ("package", "packager"))
        self.assertEqual(mine["changes"], {"track": {"value": 1400.0, "unit": "mm"}})
        self.assertEqual(d["variables"]["seat_x"]["value"], -1000.0)
        self.assertEqual(d["variables"]["track"]["value"], 1400.0)
        self.assertEqual(self.ctx.store.pending_proposals(), [])  # built
        self.assertEqual(self.ctx.store.built_change_ids(), {"chg-0001", "chg-0002", "chg-0003"})
        self.assertEqual(self.ctx.designgen.calls[-1], {"seat_x": {"value": -1000.0, "unit": "mm"},
                                                        "track": {"value": 1400.0, "unit": "mm"},
                                                        "battery_pack": "big"})
        created = [e for e in self.ctx.store.events() if e["kind"] == "design.created"][-1]
        self.assertEqual(created["agent"], "packager")
        self.assert_events_fit()

    def test_without_own_changes_no_packager_entry(self):
        self.propose("occupant", changes={"seat_x": self.mm(-1000)})
        out = build_version(self.ctx, {"reason": "そのまま"}, "packager")
        self.assertEqual(out["module_changes"], ["chg-0001"])
        self.assertEqual(len(self.ctx.store.design(2)["module_changes"]), 1)

    def test_the_round_is_counted_once(self):
        self.propose("occupant", changes={"seat_x": self.mm(-1000)})
        self.propose("powertrain", changes={"battery_x": self.mm(-950)})
        out = build_version(self.ctx, {"reason": "まとめ"}, "packager")
        self.assertEqual(out["revisions_left"], 0)  # round_limit 1: two proposals, one round
        with self.assertRaises(ToolError) as e:
            self.propose("occupant", changes={"seat_x": self.mm(-950)})
        self.assertIn("round_limit", str(e.exception))
        with self.assertRaises(ToolError):
            build_version(self.ctx, {"reason": "もう一度"}, "packager")

    def test_a_modules_new_proposal_replaces_its_earlier_one(self):
        """Run 20261105-091217: build_version refused chg-0005, the designer proposed chg-0006 again, and the two
        clashed with no way to take chg-0005 back. A module has one proposal per round: the new one replaces it."""
        self.propose("powertrain", choices={"battery_pack": "big"})
        self.propose("occupant", changes={"seat_x": self.mm(-1000)})
        out = self.propose("occupant", changes={"seat_x": self.mm(-950)})
        self.assertEqual(out["replaced"], ["chg-0002"])
        self.assertIn("chg-0002 は、この提案に置き換わりました", out["note"])
        self.assertEqual(out["pending"], ["chg-0001", "chg-0003"])
        self.assertEqual(self.ctx.store.superseded_change_ids(), ["chg-0002"])
        built = build_version(self.ctx, {"reason": "x"}, "packager")
        self.assertEqual(built["module_changes"], ["chg-0001", "chg-0003"])
        self.assertEqual(self.ctx.store.design(2)["variables"]["seat_x"]["value"], -950.0)
        self.assertEqual(self.ctx.store.pending_proposals(), [])

    def test_a_proposal_refused_by_build_version_is_replaced_and_the_version_is_built(self):
        """The N13 flow (contract 8762a06): build_version refuses a proposal (here the battery past its space), the
        module's designer proposes again, and the packager builds the version from the new proposal alone."""
        self.propose("powertrain", changes={"battery_x": self.mm(-700)})  # 100 mm past the envelope's end
        with self.assertRaises(ToolError):
            build_version(self.ctx, {"reason": "x"}, "packager")
        again = self.propose("powertrain", changes={"battery_x": self.mm(-850)})
        self.assertEqual(again["replaced"], ["chg-0001"])
        out = build_version(self.ctx, {"reason": "出し直しの提案で"}, "packager")
        self.assertEqual(out["module_changes"], ["chg-0002"])
        self.assertEqual(self.ctx.store.design(2)["variables"]["battery_x"]["value"], -850.0)
        self.assert_events_fit()

    def test_a_proposal_and_the_packager_on_the_same_variable_is_refused(self):
        # a variable listed by two modules (stand-in: the packager's own track and a frame entry for it)
        d = self.ctx.store.design()
        for m in d["modules"]:
            if m["module_id"] == "frame":
                m["variables"].append("track")
        self.ctx.store.design = lambda version=None, _d=d: _d
        self.propose("frame", changes={"track": self.mm(1350)})
        with self.assertRaises(ToolError) as e:
            build_version(self.ctx, {"reason": "x", "changes": {"track": self.mm(1400)}}, "packager")
        self.assertIn("chg-0001 と パッケージ役の changes", str(e.exception))

    def test_an_envelope_breach_is_refused(self):
        self.propose("powertrain", changes={"battery_x": self.mm(-700)})  # 100 mm past the envelope's end
        with self.assertRaises(ToolError) as e:
            build_version(self.ctx, {"reason": "x"}, "packager")
        msg = str(e.exception)
        self.assertIn("battery", msg)
        self.assertIn("chg-0001", msg)
        self.assertIn("100.0 mm", msg)
        self.assertEqual(self.ctx.version, 1)
        # a bigger space from the packager lets it through, and the new envelope is in the new design
        env = self.ctx.store.design()["modules"][3]["envelope"][0]
        wider = {"center_mm": [env["center_mm"][0] + 100.0, *env["center_mm"][1:]],
                 "size_mm": [env["size_mm"][0] + 200.0, *env["size_mm"][1:]]}
        out = build_version(self.ctx, {"reason": "電池の空間を前へ", "envelopes": {"powertrain": [wider]}}, "packager")
        d = self.ctx.store.design(out["design_version"])
        self.assertEqual(d["modules"][3]["envelope"], [wider])
        self.assertIn("空間を配り直した: powertrain", d["change_reason"])

    def test_a_proposal_that_moves_another_modules_part_out_of_its_space_is_refused(self):
        # as a steering proposal moves the frame's dash posts: here the seat's move carries the cargo (body) along
        plain = ModuleDesigngen.regenerate

        def carried(gen, design, changes):
            out = plain(gen, design, changes)
            if "seat_x" in changes:
                next(p for p in out["parts"] if p["id"] == "cargo")["box"]["center_mm"][0] += 300.0
            return out

        self.propose("occupant", changes={"seat_x": self.mm(-1000)})  # inside the occupant's space
        with mock.patch.object(ModuleDesigngen, "regenerate", carried), self.assertRaises(ToolError) as e:
            build_version(self.ctx, {"reason": "x"}, "packager")
        self.assertIn("(body) の cargo が空間から", str(e.exception))
        self.assertEqual(self.ctx.version, 1)

    def test_regrow_names_modules_of_the_design(self):
        self.propose("powertrain", changes={"battery_x": self.mm(-700)})
        with self.assertRaisesRegex(ToolError, "は envelopes と regrow の両方"):
            build_version(self.ctx, {"reason": "x", "regrow": ["powertrain"], "envelopes": {"powertrain": []}}, "packager")
        with self.assertRaisesRegex(ToolError, "package には空間がありません"):
            build_version(self.ctx, {"reason": "x", "regrow": ["package"]}, "packager")
        with self.assertRaisesRegex(ToolError, "roof"):  # not a module id (the input schema)
            build_version(self.ctx, {"reason": "x", "regrow": ["roof"]}, "packager")
        self.assertEqual(self.ctx.version, 1)  # (the space made again around the parts: tests.agent.test_first_design)

    def test_packager_only_and_package_variables_only(self):
        self.propose("occupant", changes={"seat_x": self.mm(-1000)})
        for agent in ("designer.occupant", "lead", "designer"):
            with self.subTest(agent=agent), self.assertRaises(ToolError) as e:
                build_version(self.ctx, {"reason": "x"}, agent)
            self.assertIn("packager", str(e.exception))
        with self.assertRaises(ToolError) as e:
            build_version(self.ctx, {"reason": "x", "changes": {"seat_x": self.mm(-950)}}, "packager")
        self.assertIn("occupant", str(e.exception))
        self.assertEqual(self.ctx.version, 1)

    def test_a_choice_that_moves_a_layout_variable_is_refused(self):
        self.propose("body", choices={"low_stance": "on"})
        with self.assertRaises(ToolError) as e:
            build_version(self.ctx, {"reason": "x"}, "packager")
        self.assertIn("track", str(e.exception))
        out = build_version(self.ctx, {"reason": "低く", "changes": {"track": self.mm(1200)}}, "packager")
        self.assertEqual(self.ctx.store.design(out["design_version"])["module_changes"][-1]["changes"],
                         {"track": {"value": 1200.0, "unit": "mm"}})


class WithoutModulesTest(RunCase, unittest.TestCase):
    def test_revise_design_still_makes_a_version(self):
        out = revise_design(self.ctx, {"changes": {"track": {"value": 1400.0, "unit": "mm"}}, "reason": "wider"},
                            "designer")
        self.assertEqual(out["design_version"], 2)
        self.assertNotIn("module_changes", self.ctx.store.design(2))
        self.assertIsNone(get_context(self.ctx, {"sections": ["modules"]})["modules"])

    def test_build_version_and_module_are_refused(self):
        with self.assertRaises(ToolError):
            build_version(self.ctx, {"reason": "x"}, "packager")
        with self.assertRaises(ToolError):
            revise_design(self.ctx, {"module": "frame", "changes": {"track": {"value": 1400.0, "unit": "mm"}},
                                     "reason": "x"}, "designer")
        self.assertIsNone(rules.check_call(self.ctx, "revise_design", {"reason": "x"}, "packager"))


if __name__ == "__main__":
    unittest.main()

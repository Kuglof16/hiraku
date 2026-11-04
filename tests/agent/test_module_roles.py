"""Designs with modules (docs/contract.md モジュールに分けた設計): the module designers' roles,
their SDK names, the packager's build_version, the routing of reds (roles.route_reds) and the prompts."""

from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from hiraku import contract
from hiraku.agent import prompts, roles
from hiraku.agent.roles import agent_id_from_sdk, route_reds, sdk_name, subagents

LEAD = {"agent_id": "lead", "role": "lead", "prop": "binder"}
DESIGNER = {"agent_id": "designer", "role": "designer", "prop": "pencil"}
PACKAGER = {"agent_id": "packager", "role": "packager", "prop": "spanner"}
STRENGTH = {"agent_id": "cae.strength", "role": "cae", "domain": "strength", "prop": "i_beam"}


def module(mid: str) -> dict:
    return {"module_id": mid, "owner": "packager" if mid == "package" else f"designer.{mid}", "envelope": [],
            "variables": [], "choices": []}


# a design split into four modules (no occupant, steering, body): parts and members carry their module
MODULAR_DESIGN = {
    "design_version": 1,
    "modules": [module(m) for m in ("package", "frame", "suspension", "powertrain")],
    "parts": [{"id": "battery", "module": "powertrain"}, {"id": "motor", "module": "powertrain"},
              {"id": "gusset_L", "module": "frame"}, {"id": "wheel_FL", "module": "suspension"},
              {"id": "frame", "module": "frame"}],
    "members": [{"id": "rail_L_FA", "part": "frame"}, {"id": "arm_FL", "module": "suspension"}],
}
PLAIN_DESIGN = {"design_version": 1, "parts": [{"id": "battery"}]}


class FakeStore:
    def __init__(self, design: dict, verdicts: list[dict] = (), analyses: list[dict] = ()):
        self._design = design
        self._verdicts = list(verdicts)
        self._analyses = {a["analysis_id"]: a for a in analyses}
        self.design_version = design["design_version"]

    def design(self, version=None):
        return self._design

    def records(self, kind, design_version=None):
        return list(self._verdicts) if kind == "verdict" else []

    def get(self, kind, record_id):
        return self._analyses.get(record_id) if kind == "analysis" else None


def fake_ctx(design: dict, load_cases: list[dict], agents: list[dict], verdicts=(), analyses=()) -> SimpleNamespace:
    store = FakeStore(design, verdicts, analyses)
    return SimpleNamespace(store=store, version=store.design_version, load_cases=load_cases,
                           project={"agents": agents, "load_cases": load_cases, "flags": {}, "round_limit": 4})


def lc(lcid: str, test_id: str, modules: list[str] | None = None) -> dict:
    out = {"load_case_id": lcid, "test_id": test_id, "domain": "strength"}
    if modules:
        out["modules"] = modules
    return out


def red(n: int, lcid: str, analysis_id: str | None = None) -> dict:
    v = {"verdict_id": f"ver-{n:04d}", "load_case_id": lcid, "pass": False}
    if analysis_id:
        v["analysis_id"] = analysis_id
    return v


LOAD_CASES = [lc("R1-frame-bending", "R1", ["frame"]), lc("H1-interference", "H1", ["package"])]


class SdkNamesTest(unittest.TestCase):
    def test_module_designers_round_trip(self):
        for a in contract.MODULE_DESIGNERS:
            with self.subTest(agent=a):
                self.assertEqual(sdk_name(a), a.replace(".", "-"))
                self.assertEqual(agent_id_from_sdk(sdk_name(a)), a)
                self.assertEqual(agent_id_from_sdk("hiraku:" + sdk_name(a)), a)
        self.assertIsNone(agent_id_from_sdk("designer-package"))  # the layout has no designer: the packager owns it
        self.assertIsNone(agent_id_from_sdk("designer-wings"))
        self.assertEqual(agent_id_from_sdk("designer"), "designer")


class ModuleRolesTest(unittest.TestCase):
    def test_module_designers_only_for_modules_in_the_design(self):
        ctx = fake_ctx(MODULAR_DESIGN, LOAD_CASES, [LEAD, DESIGNER, STRENGTH])
        agents = subagents(ctx)
        self.assertEqual(sorted(agents), ["cae-strength", "designer-frame", "designer-powertrain",
                                          "designer-suspension", "packager"])
        for name in ("designer-frame", "designer-powertrain", "designer-suspension"):
            d = agents[name]
            self.assertEqual(d.tools, [contract.mcp_name(t) for t in ("get_context", "list_tools", "revise_design")])
            self.assertIn(name.split("-", 1)[1], d.prompt)
            self.assertIn("revise_design", d.prompt)
            self.assertEqual(d.skills, ["hiraku:revise-a-module"])
            self.assertEqual((d.model, d.effort), ("inherit", roles.DESIGNER_EFFORT))
        self.assertIn("ガセット", agents["designer-frame"].prompt)
        self.assertIn("電池", agents["designer-powertrain"].prompt)
        self.assertNotEqual(agents["designer-frame"].description, agents["designer-powertrain"].description)
        self.assertIn(contract.mcp_name("build_version"), agents["packager"].tools)
        self.assertIn(contract.mcp_name("rule_check"), agents["packager"].tools)
        self.assertIn("build_version", agents["packager"].prompt)
        self.assertNotIn(contract.mcp_name("build_version"), agents["cae-strength"].tools)
        self.assertNotIn("hiraku:revise-a-module", agents["cae-strength"].skills)

    def test_possible_roles_carry_a_pet_per_module_designer(self):
        ctx = fake_ctx(MODULAR_DESIGN, LOAD_CASES, [LEAD, DESIGNER, STRENGTH])
        got = roles.possible_roles(ctx)
        ids = [a["agent_id"] for a in got]
        self.assertEqual(ids, ["lead", "designer.frame", "designer.suspension", "designer.powertrain", "cae.strength",
                               "packager"])
        for a in got:
            if a["agent_id"].startswith("designer."):
                self.assertEqual((a["role"], a["prop"], a["module"]), ("designer", "pencil", a["agent_id"][9:]))
            self.assertEqual(contract.errors("agent_id", a["agent_id"]), [])

    def test_without_modules_the_roles_are_as_before(self):
        ctx = fake_ctx(PLAIN_DESIGN, LOAD_CASES, [LEAD, DESIGNER, PACKAGER, STRENGTH])
        agents = subagents(ctx)
        self.assertEqual(sorted(agents), ["cae-strength", "designer", "packager"])
        self.assertEqual(agents["designer"].prompt, prompts.DESIGNER_PROMPT)
        self.assertEqual(agents["designer"].tools, [contract.mcp_name(t) for t in roles.DESIGNER_TOOLS])
        self.assertEqual(agents["packager"].tools, [contract.mcp_name(t) for t in roles.PACKAGER_TOOLS])
        self.assertEqual(agents["packager"].prompt, prompts.cae_prompt("assembly"))
        self.assertNotIn("build_version", agents["packager"].prompt)
        self.assertEqual(roles.possible_roles(ctx), [LEAD, DESIGNER, PACKAGER, STRENGTH])

    def test_a_mix_gives_module_designers_the_designers_model(self):
        mix = {"lead": {"model": "a", "effort": "high"}, "designer": {"model": "d", "effort": "max"},
               "packager": {"model": "p", "effort": "low"}, "cae": {"model": "c", "effort": "medium"}}
        self.assertEqual(roles.role_model("designer.frame", mix), ("d", "max"))
        self.assertEqual(roles.role_model("designer.frame", None), ("inherit", roles.DESIGNER_EFFORT))


class RouteRedsTest(unittest.TestCase):
    def route(self, load_cases, verdicts, analyses=(), design=MODULAR_DESIGN):
        return route_reds(fake_ctx(design, load_cases, [LEAD], verdicts, analyses))

    def test_a_red_goes_to_its_modules_designers(self):
        cases = [lc("R1-frame-bending", "R1", ["frame"]), lc("P1-wheel-force", "P1", ["suspension", "frame"]),
                 lc("C1-crash", "C1", ["frame"])]
        verdicts = [red(1, "R1-frame-bending"), red(2, "P1-wheel-force"), {**red(3, "C1-crash"), "pass": True}]
        self.assertEqual(self.route(cases, verdicts),
                         {"designer.frame": ["R1-frame-bending", "P1-wheel-force"],
                          "designer.suspension": ["P1-wheel-force"]})

    def test_newest_verdict_counts(self):
        cases = [lc("R1-frame-bending", "R1", ["frame"])]
        self.assertEqual(self.route(cases, [red(1, "R1-frame-bending"), {**red(2, "R1-frame-bending"), "pass": True}]),
                         {})

    def test_a_package_red_goes_to_the_packager(self):
        cases = [lc("H3-reach", "H3", ["package"]), lc("X1", "X1")]  # no modules: the packager lays out
        self.assertEqual(self.route(cases, [red(1, "H3-reach"), red(2, "X1")]), {"packager": ["H3-reach", "X1"]})

    def test_a_module_the_design_lacks_goes_to_the_packager(self):
        cases = [lc("B1-deck", "B1", ["body"])]
        self.assertEqual(self.route(cases, [red(1, "B1-deck")]), {"packager": ["B1-deck"]})

    def h1(self, where: str, interferences=()):
        analysis = {"analysis_id": "ana-0001", "results": [
            {"quantity": "interference_count", "value": 1.0, "unit": "1"},
            {"quantity": "worst_interference", "value": 34.4, "unit": "mm", "where": where},
            {"quantity": "worst_motion_interference", "value": 0.0, "unit": "mm"}],
            "interferences": list(interferences)}
        return self.route([lc("H1-interference", "H1", ["package"])], [red(1, "H1-interference", "ana-0001")],
                          [analysis])

    def test_an_overlap_across_two_modules_goes_to_the_packager(self):
        self.assertEqual(self.h1("part:gusset_L x part:battery"), {"packager": ["H1-interference"]})
        self.assertEqual(self.h1("member:rail_L_FA x part:motor"), {"packager": ["H1-interference"]})

    def test_an_overlap_inside_one_module_goes_to_that_designer(self):
        self.assertEqual(self.h1("part:battery x part:motor"), {"designer.powertrain": ["H1-interference"]})
        # a member without its own module is its part's (rail_L_FA is the frame's)
        self.assertEqual(self.h1("member:rail_L_FA x part:gusset_L"), {"designer.frame": ["H1-interference"]})

    def test_every_overlap_of_the_analysis_is_routed(self):
        got = self.h1("part:battery x part:motor", [
            {"a": "battery", "b": "motor", "depth_mm": 3.0, "state": "rest"},
            {"a": "wheel_FL", "b": "member arm_FL", "depth_mm": 1.0, "state": "motion"},
            {"a": "gusset_L", "b": "battery", "depth_mm": 2.0, "state": "rest"}])
        self.assertEqual(got, {"designer.suspension": ["H1-interference"], "designer.powertrain": ["H1-interference"],
                               "packager": ["H1-interference"]})
        self.assertEqual(list(got)[-1], "packager")  # the packager builds after the designers

    def test_unknown_parts_fall_back_to_the_load_cases_modules(self):
        self.assertEqual(self.h1("part:nowhere x part:battery"), {"packager": ["H1-interference"]})

    def test_without_modules_every_red_goes_to_the_designer(self):
        cases = [lc("R1-frame-bending", "R1"), lc("H1-interference", "H1")]
        self.assertEqual(self.route(cases, [red(1, "R1-frame-bending"), red(2, "H1-interference")], design=PLAIN_DESIGN),
                         {"designer": ["R1-frame-bending", "H1-interference"]})
        self.assertIsNone(roles.routing_text(fake_ctx(PLAIN_DESIGN, cases, [LEAD], [red(1, "R1-frame-bending")])))


class ModulePromptsTest(unittest.TestCase):
    def test_module_designer_prompt(self):
        for a in contract.MODULE_DESIGNERS:
            m = a.split(".", 1)[1]
            with self.subTest(module=m):
                p = prompts.module_designer_prompt(m)
                self.assertIn(f'revise_design(module="{m}"', p)
                self.assertIn("load_case_ids", p)
                self.assertIn("get_context", p)
                self.assertIn("パッケージ役", p)
                self.assertIn("hiraku:revise-a-module", p)
                self.assertIn(prompts.MODULE_PARTS_JA[m], p)
                self.assertNotIn("道具", p)

    def test_packager_and_lead_with_modules(self):
        self.assertIn("build_version", prompts.cae_prompt("assembly", modules=True))
        self.assertIn("自分の変更が無くても", prompts.cae_prompt("assembly", modules=True))
        self.assertEqual(prompts.lead_prompt(), prompts.LEAD_PROMPT)
        lead = prompts.lead_prompt(True)
        self.assertIn("build_version", lead)
        self.assertIn("1 つのメッセージで Agent", lead)
        self.assertIn("HIRAKU の振り分け", lead)

    def test_routing_text(self):
        ctx = fake_ctx(MODULAR_DESIGN, LOAD_CASES, [LEAD],
                       [red(1, "R1-frame-bending"), red(2, "H1-interference")])
        text = roles.routing_text(ctx)
        self.assertIn("版 1", text)
        self.assertIn("designer-frame", text)
        self.assertIn("R1-frame-bending", text)
        self.assertIn("packager (パッケージ役): H1-interference", text)
        self.assertIn("build_version で版 2", text)
        only_package = prompts.routing_text(3, {"packager": ["H1-interference"]})
        self.assertIn("build_version で版 4", only_package)


class ModuleSkillTest(unittest.TestCase):
    def test_revise_a_module(self):
        import re

        import yaml

        from hiraku.tools.functions import RUN_TOOLS

        self.assertEqual(roles.module_designer_skills(), ["hiraku:revise-a-module"])
        self.assertNotIn("hiraku:revise-a-module", roles.plugin_skills())
        self.assertIn("hiraku:revise-a-module", roles.all_plugin_skills())
        text = (roles.PLUGIN_DIR / "skills" / "revise-a-module" / "SKILL.md").read_text(encoding="utf-8")
        meta = yaml.safe_load(re.match(r"---\n(.*?)\n---\n", text.replace("\r\n", "\n"), re.S).group(1))
        self.assertEqual(meta["name"], "revise-a-module")
        self.assertTrue(meta["description"])
        for called in re.findall(r"`([a-z_]+)`", text):
            if called.endswith("_design") or called in ("build_version", "get_context"):
                self.assertIn(called, (*RUN_TOOLS, *contract.MCP_TOOLS_PACKAGER))
        for ref in re.findall(r"hiraku:([a-z-]+)", text):
            self.assertIn(f"hiraku:{ref}", roles.all_plugin_skills())
        self.assertIn("モジュールの無い設計", text)
        self.assertNotIn("道具", text)


class SessionAgentsTest(unittest.TestCase):
    def test_agents_with_modules(self):
        props = {"designer": "pencil", "packager": "spanner"}
        got = roles.agents_with_modules([LEAD, DESIGNER, STRENGTH], MODULAR_DESIGN, props.get)
        self.assertEqual([a["agent_id"] for a in got],
                         ["lead", "designer.frame", "designer.suspension", "designer.powertrain", "cae.strength",
                          "packager"])
        plain = [LEAD, DESIGNER]
        self.assertIs(roles.agents_with_modules(plain, copy.deepcopy(PLAIN_DESIGN), props.get), plain)


if __name__ == "__main__":
    unittest.main()

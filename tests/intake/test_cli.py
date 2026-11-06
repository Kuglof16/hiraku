import contextlib
import io
import json
import unittest
from unittest import mock

from hiraku.catalog import REPO_ROOT
from hiraku.intake import __main__ as cli
from hiraku.intake import agent, webdata


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        # main() reconfigures the real streams; the redirected ones have no such method
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class PickCliTest(unittest.TestCase):
    def test_pick_from_the_product_file(self):
        with mock.patch("hiraku.intake.domains.builder_for", lambda catalog, tid: object()):  # every test has a builder
            code, out, _ = run("pick", "--product", "serving-robot")
        self.assertEqual(code, 0)
        self.assertIn("確かめる分野を 7 つ、試験を", out)

    def test_pick_as_json(self):
        code, out, _ = run("pick", "--product", "electric-guitar", "--json")
        rec = json.loads(out)
        self.assertEqual([p["domain"] for p in rec["picks"]], ["strength", "nvh"])

    def test_claude_needs_a_key_and_says_so_without_calling(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit) as cm:
                run("pick", "--product", "care-bed", "--claude")
        self.assertIn("ANTHROPIC_API_KEY", str(cm.exception))

    def test_one_of_product_or_want_is_required(self):
        with self.assertRaises(SystemExit):
            run("pick")


class HearingCliTest(unittest.TestCase):
    def test_with_the_sample_answers(self):
        code, out, _ = run("hearing", "--product", "care-bed")
        self.assertEqual(code, 0)
        self.assertIn("3 つ答えてもらいました", out)  # Q03, Q30 and Q40 are the sample answers

    def test_bare_without_answers_cannot_proceed(self):
        code, out, _ = run("hearing", "--product", "care-bed", "--bare", "--no-answers")
        self.assertEqual(code, 1)
        self.assertIn("必須の答えが無いので進めない", out)


class PrepareCliTest(unittest.TestCase):
    def test_a_requirements_file_a_run_can_start_from(self):
        import tempfile
        from pathlib import Path

        from hiraku.catalog import load_requirements

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "care-bed.yaml"
            code, text, _ = run("prepare", "--product", "care-bed", "--bare", "--out", str(out))
            self.assertEqual(code, 0)
            self.assertIn("確かめる分野を 5 つ", text)
            req = load_requirements(out)  # validated against the contract
            self.assertEqual(req["product"], "care-bed")
            sources = {r["key"]: r["source"] for r in req["requirements"]}
            self.assertEqual((sources["Q40"], sources["Q60"]), ("hearing", "default"))

    def test_a_required_answer_missing_is_exit_1_and_nothing_pretends(self):
        code, text, _ = run("prepare", "--product", "care-bed", "--bare", "--no-answers")
        self.assertEqual(code, 1)
        self.assertIn("必須の答えが無い", text)

    def test_from_a_sentence_and_flag_names(self):
        code, text, _ = run("prepare", "--want", "Standing desk", "--flags", "standing,cargo", "--speed", "static", "--no-answers")
        self.assertEqual(code, 1)  # Q40 is required and nobody answered


class RulesCliTest(unittest.TestCase):
    def test_the_demo_design(self):
        code, out, _ = run("rules", "--design", "data/designs/offroad-4w-v1.json", "--product", "offroad-4w")
        self.assertEqual(code, 0)
        self.assertIn("60 規則", out)  # the demo design's numbers belong to the analysis department: no hit is asserted


class WebDataTest(unittest.TestCase):
    def test_data_js_is_up_to_date(self):
        on_disk = (REPO_ROOT / "web" / "js" / "intake" / "data.js").read_text(encoding="utf-8")
        self.assertEqual(on_disk, webdata.render_js(), "run: python -m hiraku.intake gen-js")

    def test_the_tables(self):
        t = webdata.tables()
        self.assertEqual(len(t["FLAGS"]), 19)
        self.assertEqual([d["name"] for d in t["DOMAINS"]], ["handling", "powertrain", "aero", "crash", "strength", "nvh", "thermal"])
        self.assertEqual(len(t["CATEGORIES"]), 10)
        self.assertFalse(t["FACTS"]["bend_radius_mm"]["inDesigns"])
        self.assertTrue(t["FACTS"]["mass_kg"]["inDesigns"])


class AgentTest(unittest.TestCase):
    """The Claude side is not called here; its options and its server are built from the real definitions."""

    def test_the_server_and_options_build(self):
        run_ = agent.PickRun()
        server = agent.build_server(run_)
        options = agent.build_options(server, model="m", max_budget_usd=0.1, max_turns=3)
        self.assertEqual(options.allowed_tools, ["mcp__hiraku__pick_domains", "mcp__hiraku__add_tests"])
        self.assertEqual(options.tools, [])
        self.assertEqual(options.setting_sources, [])
        self.assertTrue(options.strict_mcp_config)
        self.assertEqual(options.permission_mode, "dontAsk")

    def test_the_prompt_tells_claude_what_is_its_job_and_what_is_not(self):
        for word in ("pick_domains", "flag_reasons", "reasons", "shown_names", "計算"):
            self.assertIn(word, agent.PROMPT)

    def test_compare_with_the_product_file(self):
        run_ = agent.PickRun(record={"flags": {"self_propelled": True, "liquid": True, "ride": True},
                                     "picks": [{"domain": "handling"}, {"domain": "aero"}]})
        got = agent.compare_with_product(run_, {"self_propelled": True, "liquid": True, "near_people": True},
                                         ["handling", "aero", "crash"])
        self.assertEqual(got["flags_only_claude"], ["ride"])
        self.assertEqual(got["flags_only_file"], ["near_people"])
        self.assertFalse(got["same_domains"])

    def test_no_key_no_call(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(agent.MissingApiKey):
                agent.pick_with_claude("x")


if __name__ == "__main__":
    unittest.main()

import importlib.util
import json
import os
import re
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from promo import layout_build, layout_check as lc
from promo import n17b_data as nd

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None

# Identity and settings for the throwaway repository only (no real names).
ENV = {
    "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}
# The voice of N17b (sixth version): sentence 1 and 2, seconds from the cut's start
SENTENCE_1 = (0.30, 1.93)
SENTENCE_2 = (2.28, 5.01)


def read_data_file() -> dict:
    text = nd.OUT.read_text(encoding="utf-8")
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


class FakeRepo:
    """A small git history made in a temporary folder, with set times (JST)."""

    def __init__(self, path: Path):
        self.path = path
        self.git("init", "-q", "-b", "main")

    def git(self, *args, when=None):
        env = dict(os.environ, **ENV)
        if when:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
        return subprocess.run(["git", "-C", str(self.path), "-c", "commit.gpgsign=false",
                               "-c", "core.hooksPath=.no-hooks", *args],
                              env=env, capture_output=True, text=True, check=True).stdout

    def write(self, name, text):
        p = self.path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, name, text, msg, when):
        self.write(name, text)
        self.git("add", name)
        self.git("commit", "-q", "-m", msg, when=when)


def make_history(root: Path) -> FakeRepo:
    r = FakeRepo(root)
    r.write("hiraku/a.py", "import os\n\n# a comment\ndef test_one():\n    pass\n\n\ndef test_two():\n    pass\n")
    r.write("web/b.js", "// app\nconst x = 1;\nexport { x };")  # 3 lines, no newline at the end
    r.write("web/gen.js", "// Generated file: do not edit.\nconst DATA = {};\n")
    r.write("README.md", "# readme\n")
    r.git("add", ".")
    r.git("commit", "-q", "-m", "Start the repository", when="2026-11-02T18:00:00+09:00")
    r.git("checkout", "-q", "-b", "claude/dept-qa-ab12")
    r.commit("qa/test_q.py", "def test_q():\n    pass\n", "QA: first check", "2026-11-02T19:00:00+09:00")
    r.commit("web/grid.css", "a { b: c; }\n", "QA: grid", "2026-11-02T19:30:00+09:00")
    r.git("checkout", "-q", "main")
    r.commit("hiraku/contract.py", "Z = 3\n", "Contract: units", "2026-11-02T23:50:00+09:00")
    r.git("merge", "-q", "--no-ff", "-m", "Merge claude/dept-qa-ab12: checks", "claude/dept-qa-ab12",
          when="2026-11-03T10:00:00+09:00")
    return r


def ev(kind, data, run="20261103-000000-test"):
    return {"kind": kind, "run_id": run, "data": data}


def make_events() -> list[dict]:
    """A small run: 3 domains and assembly, a fix to version 2, records put out again."""
    picks = [{"domain": d, "shown_name": n} for d, n in (("strength", "強度"), ("crash", "衝突"), ("thermal", "熱"))]
    case = {"case": "c", "expected": 1.0, "got": 1.0, "pass": True}
    out = [
        ev("domains.picked", {"picks": picks[:2]}),
        ev("domains.picked", {"picks": picks}),              # the last one counts
        ev("design.created", {"design_version": 1}),
        ev("tool.checked", {"name": "frame", "usable": True, "checks": [case, case]}),
        ev("tool.checked", {"name": "draft", "usable": False, "checks": [{**case, "pass": False}]}),
        ev("tool.checked", {"name": "heat", "usable": False, "checks": [case]}),
        ev("tool.checked", {"name": "heat", "usable": True, "checks": [case, case, case]}),  # checked again
        ev("prediction.recorded", {"prediction_id": "pred-0001"}),
        ev("prediction.recorded", {"prediction_id": "pred-0002"}),
        ev("prediction.recorded", {"prediction_id": "pred-0003"}),
        ev("comparison.recorded", {"comparison_id": "cmp-0001", "status": "mismatch", "cause_id": None}),
        ev("cause.recorded", {"cause_id": "cause-0001", "comparison_id": "cmp-0001", "resolved": False}),
        ev("comparison.recorded", {"comparison_id": "cmp-0002", "status": "match", "cause_id": None}),
        ev("comparison.recorded", {"comparison_id": "cmp-0003", "status": "mismatch", "cause_id": None}),
        ev("judgement.updated", {"design_version": 1, "domain": "strength", "state": "red"}),
        ev("judgement.updated", {"design_version": 1, "domain": "assembly", "state": "green"}),
        ev("design.created", {"design_version": 2}),
        # cmp-0001 put out again with its cause, which is now resolved
        ev("comparison.recorded", {"comparison_id": "cmp-0001", "status": "mismatch", "cause_id": "cause-0001"}),
        ev("cause.recorded", {"cause_id": "cause-0001", "comparison_id": "cmp-0001", "resolved": True}),
        ev("comparison.recorded", {"comparison_id": "cmp-0004", "status": "match", "cause_id": None}),
        ev("judgement.updated", {"design_version": 2, "domain": "strength", "state": "pending"}),
        ev("judgement.updated", {"design_version": 2, "domain": "strength", "state": "green"}),
        ev("judgement.updated", {"design_version": 2, "domain": "crash", "state": "green"}),
        ev("judgement.updated", {"design_version": 2, "domain": "thermal", "state": "red"}),
        ev("judgement.updated", {"design_version": 2, "domain": "assembly", "state": "green"}),
    ]
    return out


class Amount(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = make_history(Path(cls.tmp.name))
        cls.a = nd.amount(cls.repo.path, "main")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_commits_and_days(self):
        self.assertEqual(self.a["commits"], 5)            # 4 commits and 1 merge
        self.assertEqual(self.a["merges"], 1)
        self.assertEqual(self.a["days"], 2)               # 11/2 and 11/3 (JST)
        self.assertEqual(self.a["first_commit"], "2026-11-02T18:00:00+09:00")
        self.assertEqual(self.a["head_time"], "2026-11-03T10:00:00+09:00")
        self.assertEqual(len(self.a["head"]), 7)

    def test_lines_and_tests(self):
        # a.py 9 + test_q.py 2 + contract.py 1; b.js 3 (gen.js left out); grid.css 1; README not code
        self.assertEqual(self.a["lines"], {"Python": 12, "JavaScript": 3, "CSS": 1})
        self.assertEqual(self.a["lines_total"], 16)
        self.assertEqual(self.a["generated_skipped"], ["web/gen.js"])
        self.assertEqual(self.a["tests"], 3)

    def test_one_day_when_all_on_one_date(self):
        self.assertEqual(nd.amount(self.repo.path, "main~1")["days"], 1)  # up to 23:50 on 11/2


class Depth(unittest.TestCase):
    def setUp(self):
        self.d = nd.depth(make_events())

    def test_areas_and_passed(self):
        d = self.d
        self.assertEqual(d["domains"], 3)
        self.assertEqual(d["domain_names"], ["強度", "衝突", "熱"])
        self.assertTrue(d["assembly"])
        self.assertEqual(d["areas"], 4)
        self.assertEqual(d["passed"], 3)                  # thermal is red in version 2
        self.assertEqual(d["versions"], 2)

    def test_tools_and_known_cases(self):
        # the last check of each name; the unusable draft does not count
        self.assertEqual(self.d["tools"], 2)
        self.assertEqual(self.d["known_cases"], 5)

    def test_comparisons_counted_once_per_id(self):
        d = self.d
        self.assertEqual(d["comparisons"], 4)
        self.assertEqual(d["mismatches"], 2)
        self.assertEqual(d["explained"], 1)               # cmp-0003 has no cause
        self.assertEqual(d["predictions"], 3)
        self.assertEqual(d["run_id"], "20261103-000000-test")

    def test_no_assembly(self):
        evs = [e for e in make_events() if not (e["kind"] == "judgement.updated" and e["data"]["domain"] == "assembly")]
        d = nd.depth(evs)
        self.assertFalse(d["assembly"])
        self.assertEqual(d["areas"], 3)

    def test_a_failed_case_of_a_usable_tool_stops(self):
        evs = make_events() + [ev("tool.checked", {"name": "bad", "usable": True, "checks": [{"pass": False}]})]
        with self.assertRaises(SystemExit):
            nd.depth(evs)

    def test_reads_the_run_folder(self):
        with tempfile.TemporaryDirectory() as t:
            run = Path(t)
            with open(run / "events.jsonl", "w", encoding="utf-8", newline="\n") as f:
                for e in make_events():
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
            (run / "repo").mkdir()
            repo = make_history(run / "repo")
            data = nd.collect(run, repo.path, "main", now=datetime.fromisoformat("2026-11-04T08:15:00+09:00"))
        self.assertEqual(data["depth"], self.d)
        self.assertEqual(data["counted_at"], "2026-11-04T08:15+09:00")
        text = nd.render_js(data)
        self.assertTrue(text.startswith("// Generated file: do not edit."))
        self.assertNotIn("test@example.invalid", text)
        self.assertNotIn(str(run), text)                  # no paths in the data
        self.assertEqual(json.loads(text[text.index("{"):text.rindex("}") + 1]), data)


class DataFileInTheRepo(unittest.TestCase):
    """promo/screens/n17b.data.js is what the script counts."""

    def setUp(self):
        self.d = read_data_file()

    def test_shape(self):
        d, a, p = self.d, self.d["amount"], self.d["depth"]
        text = nd.OUT.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("// Generated file: do not edit. Written by promo/n17b_data.py"))
        self.assertRegex(d["counted_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d\+09:00$")
        self.assertEqual(a["lines_total"], sum(a["lines"].values()))
        self.assertGreater(a["commits"], a["merges"])
        self.assertEqual(p["areas"], p["domains"] + (1 if p["assembly"] else 0))
        self.assertLessEqual(p["passed"], p["areas"])
        self.assertLessEqual(p["explained"], p["mismatches"])
        self.assertLessEqual(p["mismatches"], p["comparisons"])
        self.assertGreaterEqual(p["known_cases"], p["tools"])   # every tool checked on at least one case
        self.assertRegex(p["run_id"], r"^\d{8}-\d{6}-[a-z0-9-]+$")
        self.assertIsNone(re.search(r"@[a-z0-9.-]+\.[a-z]+", text))  # no e-mail addresses
        self.assertIsNone(re.search(r"[A-Za-z]:[\\/]", text))           # no paths

    def test_amount_is_the_count_of_the_commit(self):
        # counting the commit the file names again gives the same figures
        head = self.d["amount"]["head"]
        try:
            nd.git(nd.ROOT, "cat-file", "-e", head + "^{commit}")
        except subprocess.CalledProcessError:
            self.skipTest(f"{head} is not in this clone")
        self.assertEqual(nd.amount(nd.ROOT, head), self.d["amount"])

    @unittest.skipUnless(os.environ.get("N17B_RUN"), "set N17B_RUN to the run's folder to count the depth again")
    def test_depth_is_the_count_of_the_run(self):
        self.assertEqual(nd.depth(nd.read_events(Path(os.environ["N17B_RUN"]))), self.d["depth"])


class Rules(unittest.TestCase):
    """layout.json n17b and the page."""

    def setUp(self):
        self.rules = layout_build.load()
        self.n = self.rules["n17b"]
        self.html = (lc.SCREENS / "n17b.html").read_text(encoding="utf-8")

    def test_duration(self):
        m = re.search(r'data-duration="([0-9.]+)"', self.html)
        self.assertEqual(float(m.group(1)), self.n["duration_s"])
        self.assertEqual(self.n["duration_s"], 5.8)

    def test_boxes_inside_and_above_the_band(self):
        band, safe, stage = self.rules["subtitle_band"]["y"], self.rules["safe"], self.rules["stage"]
        boxes = [self.n[k] for k in ("amount", "head", "depth", "stamp")]
        for b in boxes:
            self.assertLessEqual(b["y"] + b["h"], band)
            self.assertGreaterEqual(b["y"], safe["y"])
            self.assertGreaterEqual(b["x"], safe["x"])
            self.assertLessEqual(b["x"] + b["w"], stage["w"] - safe["x"])
        for upper, lower in zip(boxes, boxes[1:]):    # top to bottom, not overlapping
            self.assertLessEqual(upper["y"] + upper["h"], lower["y"])
        am = self.n["amount"]
        self.assertLessEqual(am["cap_h"] + am["num_h"] + am["sub_h"], am["h"])
        dp = self.n["depth"]
        self.assertEqual(4 * dp["card_w"] + 3 * dp["gap_px"], dp["w"])

    def test_text_sizes(self):
        am, dp = self.n["amount"], self.n["depth"]
        body = self.rules["story"]["defaults"]["body_text_px"]
        for size in (am["cap_size_px"], am["sub_size_px"], self.n["head"]["size_px"],
                     dp["label_size_px"], dp["unit_size_px"], dp["sub_size_px"]):
            self.assertGreaterEqual(size, body)
        self.assertGreaterEqual(self.n["stamp"]["size_px"], self.rules["min_text_px"])
        self.assertGreater(am["num_size_px"], dp["num_size_px"])   # the amount is the larger figure

    def test_times_follow_the_voice(self):
        t, c = self.n["times"], self.n["count"]
        for k in t:
            if not k.startswith("_"):
                self.assertTrue(k.endswith("_s"), k)
        # the amount with sentence 1, counted before it ends
        self.assertGreaterEqual(t["amount_s"], SENTENCE_1[0])
        self.assertLessEqual(t["amount_count_s"] + c["amount_dur_s"], SENTENCE_1[1])
        self.assertLess(t["amount_sub_s"], SENTENCE_1[1])
        # the cards with sentence 2: the first as it starts, the last counted before it ends
        self.assertGreaterEqual(t["cards_s"], SENTENCE_2[0])
        self.assertLess(t["cards_s"], SENTENCE_2[0] + 0.3)
        self.assertLessEqual(t["dim_s"], t["cards_s"])
        last = t["cards_s"] + 3 * t["card_step_s"] + c["card_dur_s"]
        self.assertLessEqual(last, SENTENCE_2[1])
        # still from 5.0 s to the end
        self.assertLessEqual(max(last, t["stamp_s"] + c["in_dur_s"]), 5.0)

    def test_page_names_and_words(self):
        self.assertIsNone(re.search(r"--t-[a-z-]+\s*:", self.html))
        for name in re.findall(r'data-box="([^"$]+)"', self.html):
            self.assertTrue(name.startswith("n17b."), name)
        self.assertIn('<script src="n17b.data.js"></script>', self.html)
        self.assertIn("数えた日時", self.html)
        for banned in ("requestAnimationFrame", "Date.now", "Math.random", "参加者"):
            self.assertNotIn(banned, self.html)
        # the voice's words are burnt in as subtitles, not drawn on the screen
        self.assertNotIn("拓きました", self.html)
        self.assertNotIn("100,000", self.html)
        # no counted figure is typed into the page: they come from N17B_DATA
        d = read_data_file()
        figures = [d["amount"][k] for k in ("lines_total", "commits", "tests")] + \
                  [d["depth"][k] for k in ("tools", "comparisons", "known_cases", "mismatches", "predictions")]
        for v in figures:
            if v >= 10:  # one-digit figures are too common in CSS to tell apart
                pat = rf"(?<![\w,.])(?:{v:,}|{v})(?![\w,])"
                self.assertIsNone(re.search(pat, self.html), v)

    def test_story(self):
        st = self.rules["story"]["cuts"]["n17b"]
        self.assertIn("量", st["message"])
        self.assertIn("深", st["message"])
        lim = st["limits"]
        self.assertTrue(lim["why"])
        self.assertEqual(self.html.count('data-kind="number"'), 2)   # the amount and the cards' template
        self.assertGreaterEqual(lim["number"], 5)                     # 1 + 4 cards
        self.assertGreaterEqual(lim["label"], 7)                      # caption, its line, heading, 4 labels
        self.assertIn("n17b", lc.CUTS)


@unittest.skipUnless(HAS_PLAYWRIGHT, "needs playwright (the video Python, not the HIRAKU venv)")
class OnScreen(unittest.TestCase):
    SCRIPT = """() => {
      const shown = (el) => {
        const rows = el.querySelector('.rows'), box = el.querySelector('.count').getBoundingClientRect();
        return [...rows.children].filter((s) => {
          const r = s.getBoundingClientRect();
          return r.top >= box.top - 1 && r.bottom <= box.bottom + 1;
        }).map((s) => s.textContent);
      };
      const op = (sel) => getComputedStyle(document.querySelector(sel)).opacity;
      const out = { amount: shown(document.querySelector('[data-box="n17b.amount.num"]')), cards: {} };
      for (const el of document.querySelectorAll('.card'))
        out.cards[el.dataset.box.split('.')[2]] = shown(el.querySelector('.num'));
      out.sub = document.querySelector('.amount .sub').textContent;
      out.text = document.querySelector('.depth').textContent;
      out.stamp = document.querySelector('.stamp').textContent;
      out.op = { amount: op('.amount .num'), card0: op('.card'), card3: op('.card:last-child'),
                 head: op('.head'), stamp: op('.stamp') };
      return out;
    }"""

    @classmethod
    def setUpClass(cls):
        from promo import export

        cls.d = read_data_file()
        cap = export.Capture(lc.SCREENS / "n17b.html", scale=0.5)
        try:
            cls.at = {}
            for t in (0.1, 2.0, 3.0, 5.8):
                cap.page.evaluate("t => window.__export.seek(t)", t)
                cls.at[t] = cap.page.evaluate(cls.SCRIPT)
        finally:
            cap.close()

    def test_the_figures_shown_are_the_counts(self):
        a, p, end = self.d["amount"], self.d["depth"], self.at[5.8]
        fmt = lambda v: f"{v:,}"  # noqa: E731
        self.assertEqual(end["amount"], [fmt(a["lines_total"])])
        self.assertEqual(end["cards"], {"areas": [fmt(p["domains"])], "tools": [fmt(p["tools"])],
                                        "comparisons": [fmt(p["comparisons"])], "passed": [fmt(p["passed"])]})
        self.assertIn(fmt(a["commits"]), end["sub"])
        self.assertIn(fmt(a["tests"]), end["sub"])
        self.assertIn(f"{a['days']}日目", end["sub"])
        self.assertIn(f"{fmt(p['known_cases'])} 件", end["text"])
        self.assertIn(f"{p['mismatches']} 件", end["text"])
        self.assertIn(f"/ {p['areas']}", end["text"])
        self.assertIn(a["head"], end["stamp"])
        self.assertIn(p["run_id"], end["stamp"])

    def test_the_beats_follow_the_voice(self):
        self.assertLess(float(self.at[0.1]["op"]["amount"]), 0.05)        # nothing before the voice
        two = self.at[2.0]
        self.assertEqual(two["amount"], [f"{self.d['amount']['lines_total']:,}"])  # counted by sentence 1's end
        self.assertEqual(float(two["op"]["card0"]), 0)                     # no cards before sentence 2
        self.assertEqual(float(two["op"]["head"]), 0)
        three = self.at[3.0]
        self.assertGreater(float(three["op"]["card0"]), 0.95)
        self.assertEqual(float(three["op"]["card3"]), 0)                   # the cards come one after another
        self.assertEqual(float(three["op"]["stamp"]), 0)
        self.assertEqual(float(self.at[5.8]["op"]["stamp"]), 1)


if __name__ == "__main__":
    unittest.main()

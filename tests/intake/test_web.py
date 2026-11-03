"""The intake screens' view model (web/js/intake/model.js) under node, and the modules' syntax.

The model is pure data, so it runs without a browser; the DOM code (a1.js, a2.js, a5.js, index.js) is
imported to prove it parses and its imports resolve (nothing runs at import time). The screens
themselves are looked at in web/js/intake/demo.html. Skipped when node is not installed.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from hiraku.catalog import REPO_ROOT

JS = REPO_ROOT / "web" / "js" / "intake"
NODE = shutil.which("node")


def node(script: str) -> dict:
    proc = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8",
                          timeout=120, cwd=str(REPO_ROOT))
    if proc.returncode:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout.strip().splitlines()[-1])


def url(name: str) -> str:
    return (JS / name).as_uri()


@unittest.skipUnless(NODE, "node is not installed")
class ModelTest(unittest.TestCase):
    def test_every_demo_stream_builds_the_expected_model(self):
        got = node(f"""
import {{ DEMOS }} from "{url('demo-events.js')}";
import {{ buildModel, domainCards, hearingRows, allRules, ruleTree }} from "{url('model.js')}";
const out = {{}};
for (const slug of Object.keys(DEMOS)) {{
  const m = buildModel(DEMOS[slug]);
  out[slug] = {{
    lit: domainCards(m).filter((c) => c.state === 'lit').length,
    cards: domainCards(m).length,
    renamed: domainCards(m).filter((c) => c.renamed).map((c) => c.shownName),
    asked: hearingRows(m).length,
    open: hearingRows(m).filter((r) => r.state === 'open').length,
    reqs: m.requirements.length,
    fresh: m.requirements.filter((r) => r.fresh).length,
    rules: allRules(m).length,
    checks: m.checks.size,
    top: ruleTree(m).map((n) => n.label),
  }};
}}
console.log(JSON.stringify(out));
""")
        self.assertEqual({k: v["lit"] for k, v in got.items()},
                         {"offroad-4w": 7, "care-bed": 5, "electric-guitar": 2, "serving-robot": 7, "offroad-4w-file": 7})
        # the run started from the vehicle's file: the file stands, nothing is asked, two defaults are added
        self.assertEqual((got["offroad-4w-file"]["asked"], got["offroad-4w-file"]["open"]), (0, 0))
        self.assertEqual(got["offroad-4w-file"]["fresh"], 2)  # Q57 and Q63 are the only rows the hearing added
        self.assertTrue(all(v["cards"] == 7 for v in got.values()))
        self.assertEqual(got["serving-robot"]["renamed"], ["液体のこぼれ", "人との接触"])
        self.assertEqual(got["care-bed"]["renamed"], ["安定 (転倒)", "動力 (アクチュエータ)", "騒音", "熱 (デューティ)"])
        self.assertEqual(got["offroad-4w"]["rules"], 20)  # 18 built in + 2 from the sample notes
        self.assertEqual(got["care-bed"]["rules"], 18)
        self.assertEqual(got["offroad-4w"]["checks"], 20)
        self.assertEqual(got["care-bed"]["checks"], 0)  # no design yet: nothing applied
        self.assertEqual(got["care-bed"]["top"], ["板金", "締結", "溶接", "公差", "人", "電池", "組み立て", "サス", "製造"])
        self.assertEqual(got["offroad-4w"]["open"], 3)  # Q03, Q20, Q91: no answer and no default
        self.assertEqual(got["care-bed"]["open"], 0)
        self.assertGreater(got["care-bed"]["reqs"], 15)

    def test_a_replay_shows_the_state_at_that_point(self):
        got = node(f"""
import {{ DEMOS }} from "{url('demo-events.js')}";
import {{ buildModel, hearingRows, domainCards }} from "{url('model.js')}";
const ev = DEMOS['serving-robot'];
const upTo = (kind) => ev.slice(0, ev.findIndex((e) => e.kind === kind) + 1);
const before = buildModel(upTo('hearing.asked'));
const after = buildModel(upTo('hearing.answered'));
const start = buildModel(ev.slice(0, 1));
console.log(JSON.stringify({{
  startLit: domainCards(start).filter((c) => c.state === 'lit').length,
  startWait: domainCards(start).filter((c) => c.state === 'wait').length,
  beforeStates: [...new Set(hearingRows(before).map((r) => r.state))],
  beforeReqs: before.requirements.length,
  afterStates: [...new Set(hearingRows(after).map((r) => r.state))].sort(),
  afterReqs: after.requirements.length,
  fresh: after.requirements.filter((r) => r.fresh).length,
}}));
""")
        self.assertEqual(got["startLit"], 0)  # before the pick, the project holds no domain picks
        self.assertEqual(got["startWait"], 7)  # and the cards say "not decided yet", not "off"
        self.assertEqual(got["beforeStates"], ["asking"])
        self.assertEqual(got["beforeReqs"], 0)
        self.assertIn("answered", got["afterStates"])
        self.assertIn("defaulted", got["afterStates"])
        self.assertEqual(got["fresh"], got["afterReqs"])

    def test_the_last_event_for_a_document_wins_and_a_newer_check_replaces_an_older_one(self):
        got = node(f"""
import {{ buildModel, allRules, ruleState }} from "{url('model.js')}";
const rule = (id, text) => ({{ rule_id: id, doc_id: 'U1', category: 'human', text, check: 'mass_kg <= 1', origin: 'dropped', check_by: 'code', trust: 3, basis: 'b' }});
const doc = (rules) => ({{ doc: {{ doc_id: 'U1', filename: 'a.md', origin: 'dropped' }}, rules }});
const check = (v, hits) => ({{ rule_check_id: 'x', design_version: v, rule_id: 'U1-01', targets: ['a'], hits, pass: !hits.length }});
const e = (seq, kind, data, extra = {{}}) => ({{ seq, t: seq, ts: 'x', run_id: 'r', kind, data, ...extra }});
const m = buildModel([
  e(0, 'knowledge.imported', doc([rule('U1-01', 'one')])),
  e(1, 'knowledge.imported', doc([rule('U1-01', 'one'), rule('U1-02', 'two')])),
  e(2, 'design.created', {{ design_version: 2 }}),
  e(3, 'rule_check.recorded', check(2, [{{ part_id: 'a', values: {{}}, message: 'm' }}])),
  e(4, 'rule_check.recorded', check(1, [])),
]);
console.log(JSON.stringify({{ rules: allRules(m).length, hits: ruleState(m, allRules(m)[0]).hits.length, v: ruleState(m, allRules(m)[0]).designVersion }}));
""")
        self.assertEqual(got, {"rules": 2, "hits": 1, "v": 2})  # two rules; the version-2 check was not replaced by the older one

    def test_the_dom_modules_parse_and_their_imports_resolve(self):
        got = node(f"""
const names = ['dom.js', 'a1.js', 'a2.js', 'a5.js', 'index.js'];
const out = [];
for (const n of names) {{
  const m = await import(new URL(n, '{(JS / "x").as_uri()}'.replace(/x$/, '')).href);
  out.push([n, Object.keys(m).sort()]);
}}
console.log(JSON.stringify(out));
""")
        by = dict(got)
        self.assertIn("mountIntake", by["index.js"])
        self.assertEqual(by["a1.js"], ["renderA1"])
        self.assertEqual(by["a2.js"], ["renderA2"])
        self.assertEqual(by["a5.js"], ["renderA5"])


class StaticTest(unittest.TestCase):
    def test_the_files_a_screen_needs_are_there(self):
        for name in ("index.js", "model.js", "dom.js", "a1.js", "a2.js", "a5.js", "data.js", "intake.css", "demo.html", "demo-events.js"):
            self.assertTrue((JS / name).is_file(), name)

    def test_demo_events_are_events(self):
        from hiraku import contract

        text = (JS / "demo-events.js").read_text(encoding="utf-8")
        demos = json.loads(text[text.index("{"):text.rindex("}") + 1])
        for slug, events in demos.items():
            self.assertEqual([e["seq"] for e in events], list(range(len(events))), slug)
            for e in events:
                self.assertEqual(contract.errors("event", e), [], f"{slug} #{e['seq']} {e['kind']}")


if __name__ == "__main__":
    unittest.main()

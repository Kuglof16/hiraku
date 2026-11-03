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


# A document just big enough for the intake screens to draw into: elements keep their children, their
# properties and their listeners; there is no layout and no selector engine beyond what dom.js asks for.
FAKE_DOM = """
class Node {}
class Text extends Node { constructor(t) { super(); this.data = t; } }
class El extends Node {
  constructor(tag) {
    super();
    Object.assign(this, { tag, children: [], className: "", dataset: {}, style: {}, attrs: {}, listeners: {}, value: "", hidden: false,
      disabled: false, title: "", type: "", rows: 0, placeholder: "", href: "", rel: "", scrollTop: 0 });
  }
  get firstChild() { return this.children[0] || null; }
  removeChild(c) { this.children.splice(this.children.indexOf(c), 1); }
  append(...kids) { this.children.push(...kids); }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(k, fn) { (this.listeners[k] ||= []).push(fn); }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  get classList() { return { add() {}, remove() {} }; }
  all(tag) { return [this, ...this.children.flatMap((c) => (c instanceof El ? c.all(tag) : []))].filter((e) => e.tag === tag); }
  text() { return this.children.map((c) => (c instanceof Text ? c.data : c.text())).join(""); }
}
globalThis.Node = Node;
globalThis.El = El;
globalThis.document = { head: new El("head"), createElement: (t) => new El(t), createTextNode: (t) => new Text(t), querySelectorAll: () => [] };
"""


@unittest.skipUnless(NODE, "node is not installed")
class ModelTest(unittest.TestCase):
    def test_every_demo_stream_builds_the_expected_model(self):
        got = node(f"""
import {{ DEMOS }} from "{url('demo-events.js')}";
import {{ buildModel, domainCards, hearingRows, allRules, ruleTree }} from "{url('model.js')}";
const out = {{}};
for (const slug of Object.keys(DEMOS)) {{
  const m = buildModel(DEMOS[slug], {{ picksFinal: true }});
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
        from hiraku.intake import hearing, load_product

        file_req = load_product("offroad-4w")  # the rows the hearing adds are the defaults the file does not have
        added = hearing.plan_hearing(file_req, ask_confirm=False).defaulted
        self.assertEqual(got["offroad-4w-file"]["fresh"], len(added))
        self.assertTrue({"Q57", "Q63"} <= {r["key"] for r in added})
        self.assertTrue(all(v["cards"] == 7 for v in got.values()))
        self.assertEqual(got["serving-robot"]["renamed"], ["液体のこぼれ", "人との接触"])
        self.assertEqual(got["care-bed"]["renamed"], ["安定 (転倒)", "動力 (アクチュエータ)", "騒音", "熱 (デューティ)"])
        self.assertEqual(got["offroad-4w"]["rules"], 31)  # 29 built in + 2 from the sample notes
        self.assertEqual(got["care-bed"]["rules"], 29)
        self.assertEqual(got["offroad-4w"]["checks"], 31)
        self.assertEqual(got["care-bed"]["checks"], 0)  # no design yet: nothing applied
        self.assertEqual(got["care-bed"]["top"], ["板金", "締結", "溶接", "公差", "人", "電池", "組み立て", "サス", "製造", "系"])
        self.assertEqual(got["offroad-4w"]["open"], 0)  # the sample answers cover every question
        self.assertEqual(got["care-bed"]["open"], 0)
        self.assertGreater(got["care-bed"]["reqs"], 15)

    def test_a_replay_shows_the_state_at_that_point(self):
        got = node(f"""
import {{ DEMOS }} from "{url('demo-events.js')}";
import {{ buildModel, hearingRows, domainCards }} from "{url('model.js')}";
const ev = DEMOS['serving-robot'];
const upTo = (kind) => ev.slice(0, ev.findIndex((e) => e.kind === kind) + 1);
const before = buildModel(upTo('hearing.asked'), {{ picksFinal: true }});
const after = buildModel(upTo('hearing.answered'), {{ picksFinal: true }});
const start = buildModel(ev.slice(0, 1), {{ picksFinal: true }});
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

    def test_a_rule_says_which_values_it_needs(self):
        got = node(f"""
import {{ ruleNeeds }} from "{url('model.js')}";
const facts = {{ wall_mm: {{ label: "肉厚", inDesigns: true }}, hole_edge_mm: {{ label: "穴の縁", inDesigns: false }} }};
const names = (check) => ruleNeeds({{ check }}, facts).map((n) => [n.name, n.label, n.inDesigns]);
console.log(JSON.stringify({{
  wall: names("wall_mm >= 1.6"),
  declared: names("hole_edge_mm >= 1.2 * hole_diameter_mm and max(1, wall_mm) > 0"),
  req: names("req_Q19 <= 55 and var_battery_voltage <= 60"),
  system: names("system:drive_chain"),
}}));
""")
        self.assertEqual(got["wall"], [["wall_mm", "肉厚", True]])
        self.assertEqual(got["declared"], [["hole_edge_mm", "穴の縁", False], ["wall_mm", "肉厚", True]])  # unknown names and functions are not values
        self.assertEqual(got["req"], [["req_Q19", "要件 Q19 の数値", True], ["var_battery_voltage", "設計の変数の値", True]])
        self.assertEqual(got["system"], [])

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


@unittest.skipUnless(NODE, "node is not installed")
class WantTest(unittest.TestCase):
    """mountIntake(el, api, {want}) and setWant(text) fill A1's request box and never send it."""

    def run_script(self, body: str) -> dict:
        return node(f"""{FAKE_DOM}
import {{ mountIntake }} from "{url('index.js')}";
const sent = [];
const api = {{ events: () => [], onEvent: () => () => {{}}, send: (t) => sent.push(t) }};
const box = (el) => el.all("textarea")[0];
const mount = (options) => {{ const el = new El("div"); return [el, mountIntake(el, api, options)]; }};
{body}
""")

    def test_the_option_fills_the_box_without_sending(self):
        got = self.run_script("""
const [filled] = mount({ want: "ギターを作りたい" });
const [bare] = mount({});
console.log(JSON.stringify({ filled: box(filled).value, bare: box(bare).value, sent }));
""")
        self.assertEqual(got, {"filled": "ギターを作りたい", "bare": "", "sent": []})

    def test_set_want_replaces_the_text_on_any_screen_and_the_button_sends_what_is_in_the_box(self):
        got = self.run_script("""
const [el, intake] = mount({ want: "ギターを作りたい" });
intake.setWant("介護ベッドを作りたい");
const second = box(el).value;
intake.show("A5");
intake.setWant("配膳ロボットを作りたい");
intake.show("A1");
const third = box(el).value;
el.all("button").find((b) => b.text() === "送る").listeners.click[0]();
intake.setWant(undefined);
console.log(JSON.stringify({ second, third, sent, cleared: box(el).value }));
""")
        self.assertEqual(got, {"second": "介護ベッドを作りたい", "third": "配膳ロボットを作りたい",
                               "sent": ["配膳ロボットを作りたい"], "cleared": ""})


@unittest.skipUnless(NODE, "node is not installed")
class RuleDetailTest(unittest.TestCase):
    def test_the_detail_of_a_rule_lists_the_values_it_needs(self):
        got = node(f"""{FAKE_DOM}
import {{ DEMOS }} from "{url('demo-events.js')}";
import {{ buildModel, allRules }} from "{url('model.js')}";
import {{ renderA5 }} from "{url('a5.js')}";
const m = buildModel(DEMOS[Object.keys(DEMOS)[0]], {{ picksFinal: true }});
const show = (id) => {{
  const el = new El("div");
  const ui = {{ screen: "A5", mode: "category", filter: "all", query: "", selected: id, collapsed: new Set(), disabled: new Set(),
    dropped: [], litSeen: new Set(), rerender() {{}} }};
  renderA5(el, m, {{ events: () => [] }}, ui);
  return el.text();
}};
const sm = show("SM-02"), sy = show("SY-01");
console.log(JSON.stringify({{ sm: [sm.includes("判定に必要なデータ"), sm.includes("穴の中心から縁までの距離 (hole_edge_mm)"), sm.includes("今の設計には無いデータ")],
                              sy: /判定に必要なデータ(?!が)/.test(sy) }}));
""")
        self.assertEqual(got, {"sm": [True, True, True], "sy": False})  # a system rule's check is a function, not a list of values


@unittest.skipUnless(NODE, "node is not installed")
class RuleQuestionTest(unittest.TestCase):
    """A2 draws the rule hits the lead asked about and sends the decisions with the answers."""

    SCRIPT = f"""{FAKE_DOM}
import {{ mountIntake }} from "{url('index.js')}";
import {{ ruleRows }} from "{url('model.js')}";
const e = (seq, kind, data) => ({{ seq, t: seq, ts: 'x', run_id: 'r', kind, data }});
const asked = e(1, 'hearing.asked', {{ questions: [{{ req_key: 'Q34', name: 'x', text: 'どうしますか', ask: 'confirm' }}],
  rule_questions: [{{ rule_id: 'U1-04', parts: ['battery'], text: '電池が重い。直しますか、外しますか' }},
                   {{ rule_id: 'SM-01', parts: ['floor'], text: '曲げの半径', choices: ['drop_rule'] }}] }});
const answered = e(2, 'hearing.answered', {{ answers: [], rule_answers: [{{ rule_id: 'U1-04', choice: 'drop_rule', note: '電池は替える' }}] }});
let events = [asked];
const sent = [], focused = [];
const api = {{ events: () => events, onEvent: () => () => {{}}, answerHearing: async (b) => sent.push(b) }};
const el = new El('div');
const intake = mountIntake(el, api, {{ screen: 'A2', onRuleFocus: (ids) => focused.push(ids) }});
const texts = () => el.text();
{{BODY}}
"""

    def run_body(self, body: str) -> dict:
        return node(self.SCRIPT.replace("{BODY}", body))

    def test_the_rule_hits_are_rows_with_their_choices_and_answers(self):
        got = self.run_body("""
const open1 = ruleRows(intake.model).map((r) => [r.rule_id, r.state, r.choices]);
events = [asked, answered]; intake.update();
const done = ruleRows(intake.model).map((r) => [r.rule_id, r.state, r.answer && r.answer.choice]);
console.log(JSON.stringify({ open1, done, text: texts().includes('電池が重い') }));
""")
        self.assertEqual(got["open1"], [["U1-04", "asking", ["revise_design", "drop_rule"]], ["SM-01", "asking", ["drop_rule"]]])
        self.assertEqual(got["done"], [["U1-04", "answered", "drop_rule"], ["SM-01", "open", None]])
        self.assertTrue(got["text"])

    def test_the_screen_shows_the_hit_parts_the_two_choices_and_sends_the_decision(self):
        got = self.run_body("""
const card = (id) => el.all('article').find((a) => a.dataset.rule === id);
const buttons = (id) => card(id).all('button').map((b) => b.text());
const shown = { u1: buttons('U1-04'), sm: buttons('SM-01'), parts: card('U1-04').text().includes('battery') };
card('U1-04').listeners.mouseenter[0]();
card('U1-04').listeners.mouseleave[0]();
card('U1-04').all('button').find((b) => b.text() === 'このルールを使わない').listeners.click[0]();
const send = el.all('button').find((b) => b.text() === '答えて進める');
send.listeners.click[0]();
await new Promise((r) => setTimeout(r, 20));
console.log(JSON.stringify({ shown, focused, sent }));
""")
        self.assertEqual(got["shown"]["u1"], ["設計を直す", "このルールを使わない"])
        self.assertEqual(got["shown"]["sm"], ["このルールを使わない"])  # only the choices that were offered
        self.assertTrue(got["shown"]["parts"])
        self.assertEqual(got["focused"], [["battery"], []])  # the host is told which parts to light, then to stop
        self.assertEqual(got["sent"][0]["ruleAnswers"], [{"rule_id": "U1-04", "choice": "drop_rule"}])

    def test_a_new_asking_resets_the_earlier_answers(self):
        got = self.run_body("""
events = [asked, answered, e(3, 'hearing.asked', { rule_questions: [{ rule_id: 'U1-04', parts: ['battery'], text: 'もう一度' }] })];
intake.update();
console.log(JSON.stringify(ruleRows(intake.model).map((r) => [r.rule_id, r.state])));
""")
        self.assertEqual(got, [["U1-04", "asking"], ["SM-01", "asking"]])  # SM-01 was not answered: it stays, U1-04 is asked again

    def test_the_answered_hits_go_and_the_unanswered_stay_when_others_are_asked_later(self):
        got = self.run_body("""
events = [asked, answered, e(3, 'hearing.asked', { rule_questions: [{ rule_id: 'MF-01', parts: ['m1'], text: '薄い' }] })];
intake.update();
console.log(JSON.stringify(ruleRows(intake.model).map((r) => [r.rule_id, r.state])));
""")
        self.assertEqual(got, [["MF-01", "asking"], ["SM-01", "asking"]])  # U1-04 was answered: gone


@unittest.skipUnless(NODE, "node is not installed")
class DraftAndGlowTest(unittest.TestCase):
    """The code's first picks are pale candidates; Claude's choice lights the cards, and the glow follows the playback time."""

    SCRIPT = f"""{FAKE_DOM}
import {{ mountIntake }} from "{url('index.js')}";
import {{ domainCards }} from "{url('model.js')}";
const e = (seq, t, kind, data) => ({{ seq, t, ts: 'x', run_id: 'r', kind, data }});
const pick = (d) => ({{ domain: d, shown_name: d, reason: 'r', test_ids: ['T1'] }});
const first = e(1, 0.03, 'hearing.none', {{}});
const code = e(1, 0.03, 'domains.picked', {{ want: 'w', flags: {{}}, picks: [pick('strength'), pick('nvh')], off: [], assembly_test_ids: [] }});
const claude = e(2, 34.0, 'domains.picked', {{ want: 'w', flags: {{}}, flag_reasons: {{ ride: 'x' }}, picks: [pick('strength')], off: [{{ domain: 'nvh', reason: 'no' }}], assembly_test_ids: [] }});
let events = [code];
const api = {{ events: () => events, onEvent: () => () => {{}} }};
const el = new El('div');
const intake = mountIntake(el, api, {{ screen: 'A1' }});
const states = () => domainCards(intake.model).filter((c) => ['strength', 'nvh'].includes(c.domain)).map((c) => c.state);
const shadow = (d) => el.all('article').find((a) => a.dataset.domain === d).style.boxShadow || '';
{{BODY}}
"""

    def run_body(self, body: str) -> dict:
        return node(self.SCRIPT.replace("{BODY}", body))

    def test_the_codes_first_picks_are_candidates_until_claude_picks(self):
        got = self.run_body("""
const a = states(), text = el.text().includes('製品の性質から当てた候補');
events = [code, claude]; intake.update();
console.log(JSON.stringify({ a, b: states(), text, gone: !el.text().includes('製品の性質から当てた候補') }));
""")
        self.assertEqual(got["a"], ["draft", "draft"])
        self.assertEqual(got["b"], ["lit", "off"])
        self.assertTrue(got["text"])
        self.assertTrue(got["gone"])

    def test_a_sample_made_by_the_code_alone_can_say_its_picks_are_final(self):
        got = self.run_body("""
const el2 = new El('div');
const i2 = mountIntake(el2, api, { screen: 'A1', picksFinal: true });
console.log(JSON.stringify(domainCards(i2.model).filter((c) => c.domain === 'strength').map((c) => c.state)));
""")
        self.assertEqual(got, ["lit"])

    def test_the_glow_is_a_function_of_the_playback_time(self):
        got = self.run_body("""
events = [code, claude]; intake.update();
const at = {};
for (const t of [33.9, 34.0, 34.25, 34.5, 40]) { intake.setTime(t); at[t] = shadow('strength'); }
intake.setTime(34.25); const again = shadow('strength');
console.log(JSON.stringify({ at, again }));
""")
        self.assertEqual(got["at"]["33.9"].split("px")[0].split(" ")[-1], "6.00")  # before it is lit the glow is full, as at the pick
        self.assertIn("6.00px", got["at"]["34"])
        self.assertIn("3.00px", got["at"]["34.25"])
        self.assertEqual((got["at"]["34.5"], got["at"]["40"]), ("", ""))
        self.assertEqual(got["again"], got["at"]["34.25"])  # the same time, the same picture


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

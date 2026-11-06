"""The cut pages' tag for a scripted run (cut.js addDriverTag) in a headless browser.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
The run is the A6 fixture (tests.web.domains_fixture) on the N04 and N15 cuts, with its
run.started's project.driver set to scripted, to agent_sdk, or left out (the fixture has none).
"""

import copy
import unittest

from tests.web.domains_fixture import domain_events
from tests.web.test_browser import ENABLED
from tests.web import test_cut_grid_browser as grid  # the module, so its GridCutTest is not found here too
from tests.web.test_cut_grid_browser import crash_drawn, grid_layout
from tests.web.test_layout_lint_browser import LINT_JS

WORDS = "決まった手順で再現 (Claude なし)"  # labels.js SCRIPTED_CUT_JA: DRIVEN_JA.scripted and (Claude なし)
SAFE = {"x": 96, "y": 60, "w": 1728, "h": 850}  # promo/screens/layout.json safe

# The tag, and every other element on the stage that is shown and draws something (text or a canvas).
READ = """async (t) => {
  await window.hiraku.seek(t);
  const rect = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
  const shown = (el) => el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) && el.getBoundingClientRect().width > 0;
  const tags = [...document.querySelectorAll(".cut-driver")];
  const tag = tags[0];
  const others = [...document.querySelectorAll("#stage *")].filter((el) => el !== tag && !tag?.contains(el) && shown(el) &&
    (el.tagName === "CANVAS" || [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())));
  return {
    err: document.querySelector(".err")?.textContent ?? null, count: tags.length,
    tag: tag ? { text: tag.textContent, rect: rect(tag), shown: shown(tag), kind: tag.dataset.kind, box: tag.dataset.box,
                 px: parseFloat(getComputedStyle(tag).fontSize) } : null,
    others: others.map((el) => ({ what: el.dataset.box ?? el.className ?? el.tagName, rect: rect(el) })),
    over: window.hiraku.story()?.over ?? null,
  };
}"""


def with_driver(events, driver):
    out = copy.deepcopy(events)
    for e in out:
        if e["kind"] == "run.started":
            if driver is None:
                e["data"].pop("driver", None)
            else:
                e["data"]["driver"] = driver
    return out


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class CutDriverTagTest(grid.GridCutTest):
    def layout(self):
        layout = grid_layout()
        layout["safe"] = dict(SAFE)
        return layout

    def test_a_scripted_run_says_so_in_the_top_right_margin(self):
        """N04 and N15 at a few times: one tag with the words, in the margin above the safe area at
        its right edge, at least min_text_px, a note (not counted by the story); it crosses nothing
        else drawn on the stage, and the layout lint finds nothing on the page."""
        events = with_driver(crash_drawn(domain_events()), "scripted")
        base = self.serve(self.layout(), events={"20261102-120000-grid-scripted": events})
        for name, times in (("n04", (0.4, 1.5, 3.5)), ("n15", (0.5, 4.8))):
            page, errors = self.open(f"{base}/cut.html?cut={name}&run=20261102-120000-grid-scripted&version=1&t=0")
            for t in times:
                got = page.evaluate(READ, t)
                self.assertIsNone(got["err"])
                self.assertEqual(got["count"], 1, (name, t))
                tag = got["tag"]
                self.assertEqual((tag["text"], tag["kind"], tag["box"]), (WORDS, "note", "driver"))
                self.assertTrue(tag["shown"])
                self.assertGreaterEqual(tag["px"], 18)
                r = tag["rect"]
                self.assertAlmostEqual(r[2], SAFE["x"] + SAFE["w"], delta=0.5)  # the safe area's right edge
                self.assertGreaterEqual(r[1], 0)
                self.assertLessEqual(r[3], SAFE["y"])  # above the safe area: off the pictures, tags and subtitles
                for o in got["others"]:
                    q = o["rect"]
                    crosses = min(r[2], q[2]) - max(r[0], q[0]) > 0.5 and min(r[3], q[3]) - max(r[1], q[1]) > 0.5
                    self.assertFalse(crosses, f"{name} t={t}: the tag crosses {o['what']} {q}")
                self.assertEqual(got["over"], [], (name, t))
            self.assertEqual(page.evaluate(LINT_JS, {"root": "body"}), [], name)
            self.assertEqual(errors, [])
            page.close()

    def test_no_tag_for_claude_or_without_a_driver(self):
        """A run driven by Claude (agent_sdk) and a record without project.driver: no tag (not guessed)."""
        runs = {"20261102-120000-grid-claude": with_driver(domain_events(), "agent_sdk"),
                "20261102-120000-grid-none": with_driver(domain_events(), None)}
        base = self.serve(self.layout(), events=runs)
        for run in runs:
            for name in ("n04", "n15"):
                page, errors = self.open(f"{base}/cut.html?cut={name}&run={run}&version=1&t=0")
                got = page.evaluate(READ, 1.5)
                self.assertIsNone(got["err"])
                self.assertEqual(got["count"], 0, (run, name))
                self.assertEqual(errors, [])
                page.close()


# GridCutTest's own tests are run from test_cut_grid_browser, not again here.
for _name in [n for n in vars(grid.GridCutTest) if n.startswith("test_")]:
    setattr(CutDriverTagTest, _name, None)


if __name__ == "__main__":
    unittest.main()

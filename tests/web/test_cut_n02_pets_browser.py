"""N02's pets (cut.html?cut=n02): each pane with a record has the 3D pet of the agent that
ran its test, in the box layout.json n02.pane.pet gives, posed from the cut time t only.

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (as tests.web.test_browser),
and without the promo department's pet3d.js commit (as tests.web.test_cut_pets_browser).
The run is k2_fixture's tilt run (S1, recorded by cae.handling) with a second load case,
F1-wind (domain aero), recorded by an event whose agent is cae.strength: the pet follows
the event's agent, not the domain (shape example: the wind's motion is the tilt's).
"""

import copy
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.k2_fixture import tilt_events
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_cut_pets_browser import PETS_COMMIT, pets_source
from tests.web.test_cut_story_browser import story_layout

RUN = "20261106-090000-n02pets"

PETS = """async (t) => {
  if (t !== null) await window.hiraku.seek(t);
  const rect = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
  const cut = window.hiraku.cut;
  return {
    loaded: document.documentElement.dataset.pets ?? null,
    err: document.querySelector(".err")?.textContent ?? null,
    panes: [...document.querySelectorAll(".n02-pane")].map((el) => {
      const key = el.dataset.box.split(".")[2];
      const p = cut.panes.find((x) => x.key === key);
      const box = el.querySelector(".pet");
      const c = box?.querySelector("canvas.pet3d") ?? null;
      let ink = 0;
      let d = null;
      if (c) { d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 4) ink += d[i] > 0 ? 1 : 0; }
      const pet = cut.pets?.pets.find((x) => x.key === key);
      return { key, pane: rect(el), box: box ? rect(box) : null, canvas: c ? rect(c) : null, kind: c?.dataset.kind ?? null,
               role: box?.dataset.role ?? null, state: box?.dataset.petState ?? null, ink,
               from: p?.from ?? null, to: p?.to ?? null, pose: pet?.last ?? null, pixels: d ? Array.from(d) : null };
    }),
  };
}"""


def differing(a, b, tol=8):
    """The share of pixels (RGBA lists of one size) with a channel more than tol apart."""
    n = len(a) // 4
    return sum(1 for i in range(n) if any(abs(a[4 * i + j] - b[4 * i + j]) > tol for j in range(4))) / n


def n02_events():
    """The tilt run plus F1-wind recorded by cae.strength (see the module's doc)."""
    events, _info = tilt_events()
    out = copy.deepcopy(events)
    for e in out:
        e["run_id"] = RUN
    out[0]["data"]["load_cases"].append({"load_case_id": "F1-wind", "test_id": "F1", "domain": "aero", "title": "横風 (形の見本)", "params": {}, "tolerance_rel": 0.1})
    fin = next(e for e in out if e["kind"] == "analysis.finished")
    wind = copy.deepcopy(fin)
    wind["agent"] = "cae.strength"
    wind["domain"] = "aero"
    wind["data"].update({"analysis_id": "ana-0002", "load_case_id": "F1-wind", "prediction_ids": []})
    end = out.pop()
    wind["t"] = end["t"]
    end["t"] += 0.1
    out += [wind, end]
    for i, e in enumerate(out):
        e["seq"] = i
    return out


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class CutN02PetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = pets_source()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.pets_file = cls.tmp / "pet3d.js"
        if cls.src:
            cls.pets_file.write_bytes(cls.src)
        runs = cls.tmp / "runs"
        (runs / RUN).mkdir(parents=True)
        (runs / RUN / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in n02_events()), encoding="utf-8")
        cls.runs = runs
        cls.servers = []
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for s in cls.servers:
            s.shutdown()
            s.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def serve(self, layout):
        lf = self.tmp / f"layout-{len(self.servers)}.json"
        lf.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
        server = make_server("127.0.0.1", 0, self.runs, quiet=True, saved_dir=None, launcher=False, layout_file=lf, pets_file=self.pets_file)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def setUp(self):
        if not self.src:
            self.skipTest(f"needs the promo department's commit {PETS_COMMIT} (promo/pets/pet3d.js) in the repository")

    def open(self, base, t):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/cut.html?cut=n02&run={RUN}&t={t}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def by_key(self, got):
        return {p["key"]: p for p in got["panes"]}

    def test_pets_of_the_recording_agents_in_the_layouts_box(self):
        """The tilt pane's pet is cae.handling and the wind pane's cae.strength (the events'
        agents); the box is n02.pane.pet at the pane's bottom right (two layouts, so the
        values are the layout's); the pet is drawn and not counted as moving; the panes
        without a record and the legend have none."""
        for P in ({"w": 96, "h": 96, "right": 12, "bottom": 12}, {"w": 130, "h": 80, "right": 40, "bottom": 24}):
            layout = story_layout()
            layout["n02"]["pane"]["pet"] = dict(P)
            B = layout["n02"]["pane"]["border_px"]
            page, errors = self.open(self.serve(layout), 1.0)
            got = page.evaluate(PETS, 1.0)
            self.assertEqual(errors, [])
            self.assertIsNone(got["err"])
            self.assertEqual(got["loaded"], "on")
            panes = self.by_key(got)
            self.assertEqual({k: p["role"] for k, p in panes.items() if p["box"]}, {"tilt": "cae.handling", "wind": "cae.strength"})
            for k in ("tilt", "wind"):
                r = panes[k]["pane"]
                want = [r[0] + r[2] - B - P["right"] - P["w"], r[1] + r[3] - B - P["bottom"] - P["h"], P["w"], P["h"]]
                self.assertEqual([round(v) for v in panes[k]["box"]], want, (P, k))
                self.assertEqual([round(v) for v in panes[k]["canvas"]], want, (P, k))
                self.assertGreater(panes[k]["ink"], 0, k)
                self.assertIsNone(panes[k]["kind"], k)
            page.close()

    def test_the_pose_comes_from_t_only(self):
        """Idle before its pane plays, working while it plays, idle after (no verdict in this
        record); a page opened at t and one that went through other times first draw the
        same pet at t."""
        base = self.serve(story_layout())
        page, errors = self.open(base, 0)
        first = self.by_key(page.evaluate(PETS, 0.0))
        tilt = first["tilt"]
        mid = (tilt["from"] + tilt["to"]) / 2
        self.assertEqual(first["tilt"]["state"], "idle")
        wind_from = first["wind"]["from"]
        self.assertGreater(wind_from, 0.2)
        self.assertEqual(self.by_key(page.evaluate(PETS, wind_from - 0.1))["wind"]["state"], "idle")
        at_mid = self.by_key(page.evaluate(PETS, mid))
        self.assertEqual(at_mid["tilt"]["state"], "working")
        self.assertEqual(self.by_key(page.evaluate(PETS, tilt["to"] + 1.0))["tilt"]["state"], "idle")
        # back to mid after other times: the same picture
        again = self.by_key(page.evaluate(PETS, mid))
        page.close()
        fresh_page, errors2 = self.open(base, mid)
        fresh = self.by_key(fresh_page.evaluate(PETS, None))
        fresh_page.close()
        self.assertEqual(errors + errors2, [])
        for k in ("tilt", "wind"):
            self.assertEqual(again[k]["state"], fresh[k]["state"], k)
            # the pose drawn (state | pet3d time | canvas width): exactly the same
            self.assertEqual(again[k]["pose"], fresh[k]["pose"], k)
            # the picture: the same but for see-through edge pixels Chrome reads back a little
            # differently from a canvas read before it was drawn (test_cut_pets_browser)
            self.assertLess(differing(again[k]["pixels"], fresh[k]["pixels"]), 0.01, k)
        # the working pet moves: another time in the turn gives another pose and picture
        other_page, errors3 = self.open(base, mid + 0.4)
        other = self.by_key(other_page.evaluate(PETS, None))
        other_page.close()
        self.assertEqual(errors3, [])
        self.assertNotEqual(other["tilt"]["pose"], fresh["tilt"]["pose"])
        self.assertGreater(differing(other["tilt"]["pixels"], fresh["tilt"]["pixels"]), 0.02)


if __name__ == "__main__":
    unittest.main()

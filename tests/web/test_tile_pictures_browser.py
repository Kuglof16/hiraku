"""④ 解析結果: each domain's tile shows a picture of its own (web/js/tile_pictures.js, tile3d.js).

On the saved scripted run (showcase/20261105-150416-offroad-4w-scripted, every domain picked):

- each tile's picture names the record's material it is drawn from (data-used) and what it looked
  for and did not find (data-missing): crash the wall and the stress colours (no node_motion in
  this record), nvh the judged mode, thermal the parts' temperatures, handling the scene and the
  pose, powertrain the step and the car on it, aero the wind and the forces, assembly as built;
- the eight pictures differ from each other (no two tiles show the same picture);
- the moments are the record's: nvh draws the mode whose frequency is the verdict's value, the
  tilt table the moment a wheel lifts (series.js liftT), powertrain the moment of the largest
  pitch, thermal the hottest recorded temperature;
- the real look gives a seat's back and head restraint (kinds seatback, head_restraint) the
  seat's cover, and the diagram look the seat's colour.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server

try:
    from tests.web.pages import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

ENABLED = os.environ.get("HIRAKU_WEB_BROWSER") == "1" and sync_playwright is not None

ROOT = Path(__file__).resolve().parents[2]
RUN = "20261105-150416-offroad-4w-scripted"

DRAWN = "() => { const c = [...document.querySelectorAll('canvas.tile3d')]; return c.length > 0 && c.every((x) => x.dataset.key); }"
# (null until every picture is drawn: the grid is drawn again as the record loads, with new canvases)
TILES = """() => (""" + DRAWN + """)() && Object.fromEntries([...document.querySelectorAll('canvas.tile3d')].map((c) => {
  const g = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  let h = 0;
  for (let i = 0; i < g.length; i += 97) h = (h * 31 + g[i]) >>> 0;
  return [c.dataset.domain, { used: c.dataset.used.split(' ').filter(Boolean), missing: c.dataset.missing.split(' ').filter(Boolean), ana: c.dataset.ana, hash: h, w: c.width }];
}))"""
# what pictureOf chose for a domain, read through the module the page uses
PICTURE = """async (domain) => {
  const m = await import('./js/tile_pictures.js');
  const s = window.hiraku.player.state;
  const v = s.currentVersion;
  const p = m.pictureOf(s, domain, v, s.designs[v]);
  const ch = p.motion?.channels;
  const at = (name) => { const x = ch?.get(name); if (!x) return null; let k = 0; x.t_s.forEach((t, i) => { if (Math.abs(t - p.tau) < Math.abs(x.t_s[k] - p.tau)) k = i; }); return x.values[k]; };
  const maxAbs = (name) => { const x = ch?.get(name); return x ? Math.max(...x.values.map(Math.abs)) : null; };
  return { ana: p.ana?.analysis_id ?? null, lc: p.loadCase?.load_case_id ?? null, frequency: p.ct?.frequency ?? null, modePhase: p.modePhase,
           tau: p.tau, liftT: p.motion?.liftT ?? null, pitchAt: at('pitch'), pitchMax: maxAbs('pitch'),
           heatAt: p.heatAt, heatArrived: p.heat?.arrived ?? null, version: v };
}"""
LOOK = """async () => {
  const m = await import('./js/look.js');
  const real = new m.Look('real');
  const zu = new m.Look('zu');
  const fin = (k) => real.partMaterial(k).userData.finish ?? null;
  const col = (k) => '#' + zu.partMaterial(k).color.getHexString();
  return { finish: { seat: fin('seat'), seatback: fin('seatback'), head_restraint: fin('head_restraint') },
           colour: { seat: col('seat'), seatback: col('seatback'), head_restraint: col('head_restraint') } };
}"""


def run_events():
    path = ROOT / "showcase" / RUN / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class TilePicturesBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.server = make_server("127.0.0.1", 0, cls.tmp / "runs", quiet=True, saved_dir=ROOT / "showcase", launcher=False)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        cls.page = cls.browser.new_page(viewport={"width": 1440, "height": 900})
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.goto(f"{cls.base}/?run={RUN}&tab=grid&pace=0&pets=0&t=1e6", wait_until="domcontentloaded")
        cls.page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        cls.page.evaluate("window.hiraku.ready")
        cls.tiles = cls.page.wait_for_function(TILES, timeout=240000).json_value()

    @classmethod
    def tearDownClass(cls):
        cls.page.close()
        cls.browser.close()
        cls.pw.stop()
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_each_domain_names_its_material(self):
        got = {d: (t["used"], t["missing"]) for d, t in self.tiles.items()}
        self.assertEqual(got, {
            # no field node_motion in this record: the wall and the stress at its largest instead
            "crash": (["stress", "wall"], ["node_motion"]),
            "nvh": (["mode_shape"], []),
            "strength": (["stress"], []),
            "thermal": (["temperature"], []),
            "handling": (["scene", "pose"], []),
            "powertrain": (["scene", "pitch"], []),
            "aero": (["wind", "forces"], []),
            "assembly": (["built"], []),
        })
        self.assertEqual(self.errors, [])

    def test_the_pictures_differ(self):
        hashes = {d: t["hash"] for d, t in self.tiles.items()}
        self.assertEqual(len(set(hashes.values())), len(hashes), hashes)
        self.assertTrue(all(t["w"] > 50 for t in self.tiles.values()))

    def test_nvh_draws_the_judged_mode_swung_out(self):
        got = self.page.evaluate(PICTURE, "nvh")
        verdicts = [e["data"] for e in run_events() if e["kind"] == "verdict.recorded" and e["data"].get("load_case_id") == got["lc"]
                    and e.get("design_version") == got["version"]]
        hz = [v["value"]["value"] for v in verdicts if v["value"].get("unit") == "Hz"]
        self.assertTrue(hz)
        self.assertAlmostEqual(got["frequency"], hz[-1], places=6)
        self.assertEqual(got["modePhase"], 0.25)  # sin(2π 0.25) = 1: the mode at its full swing

    def test_the_moments_are_the_record_s(self):
        handling = self.page.evaluate(PICTURE, "handling")
        if handling["liftT"] is not None:
            self.assertEqual(handling["tau"], handling["liftT"])  # the tilt table: a wheel lifts
        power = self.page.evaluate(PICTURE, "powertrain")
        if power["pitchMax"] is not None:
            self.assertAlmostEqual(abs(power["pitchAt"]), power["pitchMax"], places=6)  # on the step
        thermal = self.page.evaluate(PICTURE, "thermal")
        self.assertIsNotNone(thermal["heatAt"])
        self.assertGreaterEqual(thermal["heatAt"], thermal["heatArrived"])

    def test_seat_back_and_head_restraint_look_as_the_seat(self):
        got = self.page.evaluate(LOOK)
        self.assertEqual(got["finish"], {"seat": "vinyl", "seatback": "vinyl", "head_restraint": "vinyl"})
        self.assertEqual(len(set(got["colour"].values())), 1, got["colour"])


if __name__ == "__main__":
    unittest.main()

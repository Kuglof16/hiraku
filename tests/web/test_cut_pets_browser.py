"""The 3D pets in the corners of the N04 / N15 tiles (cut.html?cut=n04 / n15).

Skipped unless HIRAKU_WEB_BROWSER=1 and Playwright is installed (as tests.web.test_browser).
The pets are the promo department's promo/pets/pet3d.js, not in this branch: the tests take
it from the promo branch's commit PETS_COMMIT (git show) into a temporary file and serve
it with make_server(pets_file=...); without that commit in the repository they are skipped.
The runs are the A6 fixture (tests.web.domains_fixture), the layout the grid test's
(layout.json 279fd76's tile.pet box, tile.pet.feature and n00's camera and motion).
"""

import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import REPO_DIR, make_server
from tests.web.domains_fixture import V1_RED, V2_RED, domain_events
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_cut_grid_browser import TESTS, box_of, crash_drawn, grid_layout, list_rects, switch_times, three_versions, with_state

PETS_COMMIT = "073e7be"  # pet3d.js v8 (the promo department's branch)


def pets_source() -> bytes | None:
    """promo/pets/pet3d.js at PETS_COMMIT, or None when the commit is not in the repository."""
    try:
        out = subprocess.run(["git", "show", f"{PETS_COMMIT}:promo/pets/pet3d.js"], cwd=REPO_DIR, capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 and out.stdout else None


PETS = """async (t) => {
  if (t !== null) await window.hiraku.seek(t);
  const rect = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
  return {
    loaded: document.documentElement.dataset.pets ?? null,
    err: document.querySelector(".err")?.textContent ?? null,
    tiles: [...document.querySelectorAll(".grid-tile")].map((el) => {
      const box = el.querySelector(".pet");
      const c = box.querySelector("canvas.pet3d");
      let ink = 0;
      if (c) { const d = c.getContext("2d").getImageData(0, 0, c.width, c.height).data; for (let i = 3; i < d.length; i += 4) ink += d[i] > 0 ? 1 : 0; }
      return { key: el.dataset.domain, featured: el.dataset.featured === "1", tile: rect(el), box: rect(box), canvas: c ? rect(c) : null,
               kind: c?.dataset.kind ?? null, role: box.dataset.role ?? null, state: box.dataset.petState ?? null, ink,
               draws: Number(box.dataset.petDraws ?? 0),
               pixels: c ? c.toDataURL() : null };
    }),
  };
}"""


@unittest.skipUnless(ENABLED, "set HIRAKU_WEB_BROWSER=1 with Playwright installed")
class CutPetsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = pets_source()
        cls.tmp = Path(tempfile.mkdtemp())
        cls.pets_file = cls.tmp / "pet3d.js"
        if cls.src:
            cls.pets_file.write_bytes(cls.src)
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

    def serve(self, events, pets_file):
        n = len(self.servers)
        runs = self.tmp / f"runs-{n}"
        for run_id, evs in events.items():
            (runs / run_id).mkdir(parents=True)
            (runs / run_id / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs), encoding="utf-8")
        lp = self.tmp / f"layout-{n}.json"
        lp.write_text(json.dumps(grid_layout()), encoding="utf-8")
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, layout_file=lp, pets_file=pets_file)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def open(self, url):
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        return page, errors

    def need_pets(self):
        if not self.src:
            self.skipTest(f"needs the promo department's commit {PETS_COMMIT} (promo/pets/pet3d.js) in the repository")

    def test_pets_stand_in_their_boxes_as_their_domains(self):
        """Each tile's pet canvas fills its box: tile.pet (96 x 96) at the bottom right, 12 px
        in, and tile.pet.feature (160 x 160) in the large tile; the role is cae.<domain>,
        packager for assembly; the pet is drawn; it is not counted as moving."""
        self.need_pets()
        layout = grid_layout()
        P = layout["n04"]["tile"]["pet"]
        B = layout["n04"]["tile"]["border_px"]
        base = self.serve({"20261103-130000-pets": crash_drawn(domain_events())}, self.pets_file)
        page, errors = self.open(f"{base}/cut.html?cut=n04&run=20261103-130000-pets&version=1&t=0")
        got = page.evaluate(PETS, 0.4)
        self.assertEqual(errors, [])
        self.assertIsNone(got["err"])
        self.assertEqual(got["loaded"], "on")
        self.assertEqual([t["key"] for t in got["tiles"]], TESTS)
        feature, slots = box_of(layout["n04"]["feature"]), list_rects(layout["n04"]["list"])
        rest = iter(slots)
        for t in got["tiles"]:
            r = feature if t["featured"] else next(rest)
            w, h = (P["feature"]["w"], P["feature"]["h"]) if t["featured"] else (P["w"], P["h"])
            # the box's right and bottom edges are P.right / P.bottom inside the tile's border
            want = [r[0] + r[2] - B - P["right"] - w, r[1] + r[3] - B - P["bottom"] - h, w, h]
            self.assertEqual([round(v) for v in t["box"]], want, t["key"])
            self.assertEqual([round(v) for v in t["canvas"]], want, t["key"])
            self.assertEqual(t["role"], "packager" if t["key"] == "assembly" else f"cae.{t['key']}")
            self.assertGreater(t["ink"], 0, t["key"])
            self.assertIsNone(t["kind"], t["key"])
        page.close()

    def test_pets_react_only_when_their_tile_changes_colour(self):
        """N04, version 1 (crash judged green here, NVH red): every pet idle and still before
        the voice names a tile; crash's pet does pass from crash_s for pass_dur_s, NVH's
        mismatch from nvh_s (the two overlap: crash 0.5-1.4 s, NVH from 1.2 s), then idle
        again; the others stay idle and their pictures do not change."""
        self.need_pets()
        layout = grid_layout()
        T = layout["n04"]["times"]
        dur = layout["n00"]["motion"]["pass_dur_s"]
        self.assertLess(T["nvh_s"] + 0.1, T["crash_s"] + dur)  # the times below assume the overlap
        events = with_state(crash_drawn(domain_events()), 1, "crash", "green")
        base = self.serve({"20261103-130000-pets": events}, self.pets_file)
        page, errors = self.open(f"{base}/cut.html?cut=n04&run=20261103-130000-pets&version=1&t=0")
        times = [0.35, T["crash_s"] + 0.1, T["nvh_s"] + 0.1, T["crash_s"] + dur + 0.05, T["assembly_s"] - 0.05]
        # one read first: Chrome moves a 2D canvas off the GPU at its first getImageData, and
        # the see-through edge pixels read back differently once (the picture is not redrawn)
        page.evaluate(PETS, 0.3)
        shots = {t: {x["key"]: x for x in page.evaluate(PETS, t)["tiles"]} for t in times}
        self.assertEqual(errors, [])
        states = [{k: x["state"] for k, x in shots[t].items() if x["state"] != "idle"} for t in times]
        # the last time is before the grid gives way: NVH's mismatch (from 1.2 s) lasts to 2.1 s
        self.assertEqual(states, [{}, {"crash": "pass"}, {"crash": "pass", "nvh": "mismatch"}, {"nvh": "mismatch"}, {"nvh": "mismatch"}])
        # idle is one fixed pose: a pet that does not react is drawn the same all along
        for k in ("handling", "thermal", "assembly"):
            self.assertEqual([shots[t][k]["draws"] for t in times], [1] * len(times), k)  # drawn once
            self.assertEqual(len({shots[t][k]["pixels"] for t in times}), 1, k)
        # a reaction moves the pet (crash is the large tile from the start to nvh_s, so both
        # pictures are in the same box), and afterwards it is idle again (states above; its
        # box is then a list slot, as NVH has become the large tile)
        self.assertNotEqual(shots[times[0]]["crash"]["pixels"], shots[times[1]]["crash"]["pixels"])
        self.assertEqual(shots[times[0]]["crash"]["box"][2:], [160, 160])
        self.assertEqual(shots[times[3]]["crash"]["box"][2:], [84, 84])
        page.close()

    def test_n15_pets_react_when_the_version_changes_a_tile(self):
        """N15 over three versions (fixture: red in V1_RED, then V2_RED, then all green): a
        pet reacts at its tile's own time in the round (the record's order, switch_times)
        when its judgement changes from the version before (pass when it turns green), and
        not when it stays the same."""
        self.need_pets()
        layout = grid_layout()
        T = layout["n15"]["times"]
        dur = layout["n00"]["motion"]["pass_dur_s"]
        events = three_versions()
        sw = switch_times(events, T)
        base = self.serve({"20261103-130000-pets3": events}, self.pets_file)
        page, errors = self.open(f"{base}/cut.html?cut=n15&run=20261103-130000-pets3&t=0")
        state = lambda t: {x["key"]: x["state"] for x in page.evaluate(PETS, t)["tiles"]}  # noqa: E731
        self.assertEqual(set(state(T["rounds_from_s"] - 0.1).values()), {"idle"})
        # each tile that turns green: idle just before its time, pass just after
        for k, v in [(k, 2) for k in V1_RED - V2_RED] + [(k, 3) for k in V2_RED]:
            at = sw[k][v]
            if at > 0.05:
                self.assertEqual(state(at - 0.05)[k], "idle", (k, v))
            self.assertEqual(state(at + 0.05)[k], "pass", (k, v))
        # a tile green all along never reacts
        steady = [k for k in TESTS if k not in V1_RED]
        for t in (1.0, 2.5, T["all_green_s"] + 0.1):
            self.assertTrue(all(state(t)[k] == "idle" for k in steady), t)
        self.assertEqual(set(state(T["all_green_s"] + dur + 0.1).values()), {"idle"})
        self.assertEqual(errors, [])
        page.close()

    def test_page_works_without_the_pets_file(self):
        """No pet3d.js (the server answers 404): no pet is drawn, the tiles and marks are as before."""
        base = self.serve({"20261103-130000-pets": domain_events()}, self.tmp / "no-such-pet3d.js")
        page, errors = self.open(f"{base}/cut.html?cut=n04&run=20261103-130000-pets&version=1&t=0")
        got = page.evaluate(PETS, 1.5)
        self.assertEqual(errors, [])
        self.assertIsNone(got["err"])
        self.assertEqual(got["loaded"], "none")
        self.assertEqual(len(got["tiles"]), len(TESTS))
        self.assertTrue(all(t["canvas"] is None and t["role"] is None for t in got["tiles"]))
        marks = page.evaluate("() => [...document.querySelectorAll('.grid-tile .mark')].map((m) => m.textContent)")
        self.assertEqual(sorted(marks), sorted("✕" if k in V1_RED else "✓" for k in TESTS))
        page.close()


if __name__ == "__main__":
    unittest.main()

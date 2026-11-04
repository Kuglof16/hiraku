"""The kinetic type samples of N08b and N17b (promo/kinetic_data.py and the
pages promo/screens/n08b_kt.html, n17b_kt.html).

The page tests that open a browser run only where playwright is installed."""

import importlib.util
import re
import subprocess
import unittest
from pathlib import Path

from promo import kinetic_data as kd
from promo import layout_build
from promo import n17b_data as nd

SCREENS = Path(kd.__file__).resolve().parent / "screens"
PAGES = {"n08b_kt": SCREENS / "n08b_kt.html", "n17b_kt": SCREENS / "n17b_kt.html"}
DATA = {"n08b_kt": kd.OUT_N08B, "n17b_kt": kd.OUT_N17B}
HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


def has_commit(sha: str) -> bool:
    try:
        subprocess.run(["git", "-C", str(kd.ROOT), "cat-file", "-e", f"{sha}^{{commit}}"],
                       check=True, capture_output=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


class TextTools(unittest.TestCase):
    def test_shorten(self):
        self.assertEqual(kd.shorten("Add the tool", 20), "Add the tool")
        s = kd.shorten("Add the fatigue tool: unit-force stresses and the S-N curve", 30)
        self.assertLessEqual(len(s), 30)
        self.assertTrue(s.endswith("\u2026"))
        self.assertTrue("Add the fatigue tool: unit-force stresses".startswith(s[:-1]))
        self.assertEqual(kd.shorten("a  b\tc", 10), "a b c")

    def test_pick(self):
        self.assertEqual(kd.pick(list(range(5)), 10), [0, 1, 2, 3, 4])
        self.assertEqual(kd.pick(list(range(11)), 3), [0, 5, 10])
        self.assertEqual(kd.pick(list(range(7)), 1), [6])

    def test_private_words_are_left_out(self):
        for s in ("copy C:/x/y", "mail a@b.c", "D:\\work"):
            self.assertTrue(kd.PRIVATE.search(s), s)
        self.assertIsNone(kd.PRIVATE.search("K2: Magic Formula 6.1 tyre for every wheel"))


class Data(unittest.TestCase):
    def test_files_are_generated_and_carry_the_rules(self):
        for cut, path in DATA.items():
            text = path.read_text(encoding="utf-8")
            self.assertTrue(nd.is_generated(text.encode("utf-8")), cut)  # left out of N17b's line count
            data = kd.read_js(path)
            self.assertEqual(data["cut"], cut)
            self.assertEqual(data["rules"], kd.RULES[cut], f"{cut}: run py promo/kinetic_data.py")

    def test_n08b_figures_are_n08b_html(self):
        data = kd.read_js(kd.OUT_N08B)["values"]
        self.assertEqual(data, kd.n08b())
        m, c = data["manufacturing"], data["cae_adoption"]
        self.assertAlmostEqual(m["sme_pct"], 100 * m["sme"] / m["all"])
        self.assertEqual(m["shown"], round(m["sme_pct"]))
        self.assertEqual(c["small"]["shown"], round(c["small"]["pct"]))
        # the figures the voice says
        self.assertEqual((m["shown"], c["small"]["shown"]), (99, 16))

    def test_n17b_counts_are_git(self):
        data = kd.read_js(kd.OUT_N17B)
        v = data["values"]
        if not has_commit(v["head"]):
            self.skipTest(f"commit {v['head']} is not in this clone")
        again = kd.n17b(kd.ROOT, v["head"])
        self.assertEqual({k: x for k, x in again.items() if k not in ("ref", "head")},
                         {k: x for k, x in v.items() if k not in ("ref", "head")})

    def test_n17b_depth_is_the_runs(self):
        d = kd.read_js(kd.OUT_N17B)["values"]["depth"]
        self.assertEqual(tuple(d), kd.DEPTH_KEYS)
        # the same four cards as N17b: the depth N17b's data file holds
        self.assertEqual(d, {k: nd.read_data(nd.OUT)["depth"][k] for k in kd.DEPTH_KEYS})
        # and, where the run's record is in this clone, the depth counted from it again
        run = kd.ROOT / "runs" / d["run_id"]
        if (run / "events.jsonl").exists():
            self.assertEqual(d, kd.n17b_depth(run))
        self.assertEqual(d["areas"], d["domains"] + (1 if d["assembly"] else 0))
        self.assertLessEqual(d["passed"], d["areas"])
        self.assertLessEqual(d["explained"], d["mismatches"])
        self.assertLessEqual(d["mismatches"], d["comparisons"])

    def test_n17b_depth_is_counted_by_n17b_data(self):
        events = [
            {"kind": "domains.picked", "run_id": "r", "data": {"picks": [{"domain": "a", "shown_name": "A"},
                                                                         {"domain": "b", "shown_name": "B"}]}},
            {"kind": "design.created", "run_id": "r", "data": {}},
            {"kind": "tool.checked", "run_id": "r", "data": {"name": "t1", "usable": True,
                                                             "checks": [{"pass": True}, {"pass": True}]}},
            {"kind": "comparison.recorded", "run_id": "r", "data": {"comparison_id": "c1", "status": "match"}},
            {"kind": "comparison.recorded", "run_id": "r", "data": {"comparison_id": "c2", "status": "mismatch",
                                                                    "cause_id": "k1"}},
            {"kind": "cause.recorded", "run_id": "r", "data": {"cause_id": "k1", "resolved": True}},
            {"kind": "judgement.updated", "run_id": "r", "data": {"domain": "a", "state": "green", "design_version": 1}},
            {"kind": "judgement.updated", "run_id": "r", "data": {"domain": "b", "state": "red", "design_version": 1}},
        ]
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
            d = kd.n17b_depth(run)
        self.assertEqual(d, {"run_id": "r", "domains": 2, "assembly": False, "areas": 2, "passed": 1, "versions": 1,
                             "tools": 1, "known_cases": 2, "comparisons": 2, "mismatches": 1, "explained": 1})

    def test_n17b_subjects(self):
        r, v = kd.RULES["n17b_kt"], kd.read_js(kd.OUT_N17B)["values"]
        self.assertEqual(len(v["messages"]), min(r["messages"], v["subjects_from"]))
        for m in v["messages"]:
            self.assertLessEqual(len(m["text"]), r["message_chars"])
            self.assertIsNone(kd.PRIVATE.search(m["text"]), m)
            self.assertRegex(m["sha"], r"^[0-9a-f]{7}$")


class Rules(unittest.TestCase):
    def setUp(self):
        self.layout = layout_build.load()
        self.band = self.layout["subtitle_band"]["y"]
        self.min_px = self.layout["min_text_px"]

    def test_duration_matches_the_page_and_the_cut(self):
        for cut, page in PAGES.items():
            m = re.search(r'data-duration="([\d.]+)"', page.read_text(encoding="utf-8"))
            self.assertEqual(float(m.group(1)), kd.RULES[cut]["duration_s"], cut)
            # the length of the cut it samples stays the same
            self.assertEqual(kd.RULES[cut]["duration_s"], self.layout[cut.replace("_kt", "")]["duration_s"], cut)

    def test_flat_boxes_stay_above_the_band(self):
        r8, r17 = kd.RULES["n08b_kt"], kd.RULES["n17b_kt"]
        for name, b in (("n08b src", r8["src"]), ("n08b cap", r8["cap"]), ("n17b cap", r17["cap"]),
                        ("n17b sub", r17["sub"]), ("n17b stamp", r17["stamp"])):
            self.assertLessEqual(b["y"] + b["h"], self.band, name)
        # the extruded figure's line box (line height 1.5) is above the caption
        n = r8["num"]
        self.assertLess(n["cy"] + 0.75 * n["size_px"], r8["cap"]["y"])
        # N17b's depth: the heading over the cards, the cards over the stamp
        h, c = r17["depth"]["head"], r17["depth"]["cards"]
        self.assertLessEqual(h["y"] + h["h"], c["y"])
        self.assertLessEqual(c["y"] + c["h"], r17["stamp"]["y"])
        self.assertLessEqual(c["x"] + c["w"], self.layout["stage"]["w"] - c["x"])
        # the commits line, moved up, stays under the figure and over the heading
        lf, sub = r17["lift"], r17["sub"]
        self.assertLessEqual(sub["y"] + lf["sub_dy_px"] + sub["h"], h["y"])

    def test_text_sizes(self):
        r8, r17 = kd.RULES["n08b_kt"], kd.RULES["n17b_kt"]
        for size in (r8["cap"]["size_px"], r8["src"]["size_px"], r17["cap"]["size_px"], r17["sub"]["size_px"],
                     r17["stamp"]["size_px"], r17["corridor"]["size_px"]):
            self.assertGreaterEqual(size, self.min_px)
        c = r17["depth"]["cards"]
        body = self.layout["story"]["defaults"]["body_text_px"]
        for size in (r17["depth"]["head"]["size_px"], c["lab_px"], c["sub_px"], c["num_px"] * c["unit_em"]):
            self.assertGreaterEqual(size, body)

    def test_times_follow_the_voice(self):
        r = kd.RULES["n08b_kt"]
        (s1a, s1b), (s2a, s2b) = r["voice"]
        b1, b2 = r["beats"]
        self.assertTrue(s1a <= b1["count_s"][0] and b1["count_s"][1] < s1b)  # 99 counts during sentence 1
        self.assertTrue(all(s1a <= t < s1b for t in b1["words_s"]))
        self.assertLessEqual(b1["out_s"][1], s2a + 0.1)                      # gone as sentence 2 starts
        self.assertTrue(all(s2a <= t < s2b for t in b2["words_s"]))
        self.assertTrue(s2a < b2["count_s"][0] and b2["count_s"][1] < s2b)  # 16 lands before "16 %" ends
        self.assertGreater(b2["num_in_s"], b1["out_s"][1])                   # never two figures at once
        r = kd.RULES["n17b_kt"]
        (s1a, s1b), (s2a, s2b) = r["voice"]
        t = r["times"]
        self.assertTrue(s1a <= t["count_s"][1] <= s1b)
        self.assertTrue(s2a <= t["open_s"][0] and s2a <= t["sub_s"] < s2b)
        self.assertLessEqual(t["cam_s"][1], r["duration_s"])
        # the depth comes with sentence 2: the heading and every card land, and
        # every card's figure has counted up, before the sentence ends
        self.assertTrue(s2a <= t["lift_s"][0] and t["lift_s"][0] <= t["head_s"] < t["cards_s"])
        last = t["cards_s"] + 3 * t["card_step_s"]
        self.assertLess(last + t["card_in_s"], s2b)
        self.assertLess(last + t["card_count_s"][1], s2b)

    def test_n17b_tilt_is_small(self):
        # the subjects face the camera (the depth shows in the rows, not in the turn of the faces),
        # the corridor hardly rolls, and the figure and the cards turn only a little
        r = kd.RULES["n17b_kt"]
        self.assertLessEqual(r["corridor"]["face_deg"], 20)
        cam, num, cards = r["camera"], r["num"], r["depth"]["cards"]
        for k in ("roll_from_deg", "roll_to_deg"):
            self.assertLessEqual(abs(cam[k]), 1.0, k)
        for k in ("yaw_from_deg", "yaw_to_deg"):
            self.assertLessEqual(abs(cam[k]), 3.0, k)
        for k in ("ry_from_deg", "ry_to_deg", "rx_from_deg", "rx_to_deg"):
            self.assertLessEqual(abs(num[k]), 12, k)
        for k in ("from_rx_deg", "from_ry_deg", "ry_deg"):
            self.assertLessEqual(abs(cards[k]), 10, k)

    def test_n17b_eases_start_and_end_at_rest(self):
        # cubic-bezier (x1, y1, x2, y2): the slope is 0 at the start when y1 = 0 < x1, at the end when y2 = 1 > x2
        for name, e in kd.RULES["n17b_kt"]["eases"].items():
            if name.startswith("_"):
                continue
            x1, y1, x2, y2 = e
            self.assertTrue(y1 == 0 < x1 <= 1 and 0 <= x2 < 1 == y2, name)

    def test_pages_are_driven_by_t_only(self):
        for cut, page in PAGES.items():
            text = page.read_text(encoding="utf-8")
            for word in ("Math.random", "Date.now", "performance.now", "requestAnimationFrame", "new Date"):
                self.assertNotIn(word, text, cut)
            self.assertIn("promo.onSeek", text, cut)
            self.assertIn(f"{cut}.data.js", text, cut)
            self.assertNotRegex(text, r"https?://", cut)  # nothing fetched: no web fonts, no libraries

    def test_pages_use_the_installed_ofl_faces_only(self):
        for cut, page in PAGES.items():
            text = page.read_text(encoding="utf-8")
            self.assertNotIn("@font-face", text, cut)
            faces = set(re.findall(r'"([^"]+)",\s*(?:sans-serif|serif)', text))
            self.assertEqual(faces, {"Noto Sans JP", "Noto Serif JP"}, cut)
            # every text style picks a face of its own (not common.css's Bahnschrift or Yu Gothic)
            self.assertNotIn("--font-latin", text, cut)
            self.assertNotIn("--font-ja", text, cut)


@unittest.skipUnless(HAS_PLAYWRIGHT, "playwright is not installed")
class Pages(unittest.TestCase):
    def shoot(self, page, times):
        from promo import export
        cap = export.Capture(page, scale=1.0)
        try:
            out = []
            for t in times:
                cap.page.evaluate("t => window.__export.seek(t)", t)
                out.append(cap.page.screenshot(type="png"))
            return out, cap
        except Exception:
            cap.close()
            raise

    def test_n08b_ends_on_the_figures(self):
        from promo import export
        v = kd.read_js(kd.OUT_N08B)["values"]
        cap = export.Capture(PAGES["n08b_kt"], scale=1.0)
        try:
            cap.page.evaluate("t => window.__export.seek(t)", 4.0)
            self.assertEqual(cap.page.evaluate("() => document.querySelector('[data-box=\"n08b_kt.num.sme\"] .d').textContent"),
                             str(v["manufacturing"]["shown"]))
            cap.page.evaluate("t => window.__export.seek(t)", 9.9)
            self.assertEqual(cap.page.evaluate("() => document.querySelector('[data-box=\"n08b_kt.num.small\"] .d').textContent"),
                             str(v["cae_adoption"]["small"]["shown"]))
            self.assertEqual(list(cap.errors), [])
        finally:
            cap.close()

    def test_n17b_counts_to_the_lines(self):
        from promo import export
        v = kd.read_js(kd.OUT_N17B)["values"]
        cap = export.Capture(PAGES["n17b_kt"], scale=1.0)
        try:
            cap.page.evaluate("t => window.__export.seek(t)", 0.0)
            self.assertEqual(cap.page.evaluate("() => document.querySelector('.num3d .d').textContent"), "0")
            cap.page.evaluate("t => window.__export.seek(t)", 5.0)
            self.assertEqual(cap.page.evaluate("() => document.querySelector('.num3d .d').textContent"),
                             f"{v['lines_total']:,}")
            shown = cap.page.evaluate("() => window.N17B_KT.MSGS.filter(m => m.el.style.visibility === 'visible').length")
            self.assertGreater(shown, 0)
            self.assertEqual(list(cap.errors), [])
        finally:
            cap.close()

    def test_n17b_cards_count_to_the_depth(self):
        from promo import export
        d = kd.read_js(kd.OUT_N17B)["values"]["depth"]
        want = {"areas": d["domains"], "tools": d["tools"], "comparisons": d["comparisons"], "passed": d["passed"]}
        cap = export.Capture(PAGES["n17b_kt"], scale=1.0)
        try:
            read = ("() => Object.fromEntries([...document.querySelectorAll('[data-box^=\"n17b_kt.depth.\"].card')]"
                    ".map(c => [c.dataset.box.split('.').pop(), c.querySelector('.fl:last-child .d').textContent]))")
            cap.page.evaluate("t => window.__export.seek(t)", 2.0)
            self.assertEqual(cap.page.evaluate("() => [...document.querySelectorAll('.card')]"
                                               ".map(c => getComputedStyle(c).visibility)"), ["hidden"] * 4)
            cap.page.evaluate("t => window.__export.seek(t)", 5.7)
            self.assertEqual(cap.page.evaluate(read), {k: f"{v:,}" for k, v in want.items()})
            self.assertEqual(dict(cap.page.evaluate("() => window.N17B_KT.cards")), want)
            self.assertEqual(list(cap.errors), [])
        finally:
            cap.close()

    def test_n17b_camera_moves_smoothly(self):
        # sampled at 30 frames a second: no frame moves far, and the speed changes little from frame to frame
        from promo import export
        cap = export.Capture(PAGES["n17b_kt"], scale=1.0)
        try:
            dt = 1 / 30
            rows = cap.page.evaluate(f"() => Array.from({{length: {int(5.8 / dt) + 1}}}, "
                                     f"(_, i) => window.N17B_KT.camera(i * {dt}))")
            self.assertEqual(list(cap.errors), [])
        finally:
            cap.close()
        limits = {"z_px": (80, 2.0), "roll_deg": (0.02, 0.001), "yaw_deg": (0.05, 0.002),
                  "fig_rx_deg": (0.05, 0.002), "fig_ry_deg": (0.1, 0.004)}
        for k, (d1_max, d2_max) in limits.items():
            x = [r[k] for r in rows]
            d1 = [b - a for a, b in zip(x, x[1:])]
            d2 = [b - a for a, b in zip(d1, d1[1:])]
            self.assertLessEqual(max(map(abs, d1)), d1_max, k)
            self.assertLessEqual(max(map(abs, d2)), d2_max, k)
            self.assertAlmostEqual(d1[0], 0, delta=d1_max / 20, msg=k)   # starts at rest
            self.assertAlmostEqual(d1[-1], 0, delta=d1_max / 20, msg=k)  # ends at rest

    def test_same_t_same_pixels(self):
        for cut, t in (("n08b_kt", 7.8), ("n17b_kt", 1.2), ("n17b_kt", 3.6), ("n17b_kt", 5.7)):
            (a, b), cap = self.shoot(PAGES[cut], [t, t])
            cap.close()
            self.assertEqual(a, b, cut)


if __name__ == "__main__":
    unittest.main()

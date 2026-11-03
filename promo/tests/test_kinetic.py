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

    def test_text_sizes(self):
        r8, r17 = kd.RULES["n08b_kt"], kd.RULES["n17b_kt"]
        for size in (r8["cap"]["size_px"], r8["src"]["size_px"], r17["cap"]["size_px"], r17["sub"]["size_px"],
                     r17["stamp"]["size_px"], r17["corridor"]["size_px"]):
            self.assertGreaterEqual(size, self.min_px)

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

    def test_same_t_same_pixels(self):
        for cut, t in (("n08b_kt", 7.8), ("n17b_kt", 1.2)):
            (a, b), cap = self.shoot(PAGES[cut], [t, t])
            cap.close()
            self.assertEqual(a, b, cut)


if __name__ == "__main__":
    unittest.main()

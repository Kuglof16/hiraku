import unittest

import numpy as np

from promo import bgm, voice

SCRIPT = voice.parse_script(
    "N00\t0:00-0:07\ta。\tA.\n"
    "N01\t0:07-0:11\tb。\tB.\n"
    "N05\t0:11-0:16\tc。\tC.\n"
    "N06\t0:16-0:20\td。\tD.\n"
)


class Harmony(unittest.TestCase):
    def test_midi_hz(self):
        self.assertAlmostEqual(bgm.midi_hz(69), 440.0)
        self.assertAlmostEqual(bgm.midi_hz(81), 880.0)

    def test_chord_notes(self):
        upper, bass = bgm.chord_notes("Bm")
        self.assertEqual({m % 12 for m in upper}, {11, 2, 6})
        self.assertTrue(all(57 <= m < 69 for m in upper))
        self.assertEqual(bass, 47)
        self.assertEqual(bgm.chord_notes("F#m")[1] % 12, 6)
        self.assertEqual(len(bgm.chord_notes("Dadd9")[0]), 4)

    def test_screen_times(self):
        self.assertEqual(bgm.css_time(bgm.SCREENS / "n05.html", "--t-burst"), 0.95)
        with self.assertRaises(ValueError):
            bgm.css_time(bgm.SCREENS / "n05.html", "--t-nothing")


class Form(unittest.TestCase):
    def test_sections_follow_the_cuts(self):
        secs = bgm.plan_sections(SCRIPT, burst_s=0.95)
        self.assertEqual([s.style for s in secs], ["intro", "question", "title", "problem"])
        self.assertEqual(secs[0].start, 0.0)
        self.assertAlmostEqual(secs[1].start, 7.0, delta=bgm.BEAT / 2)
        self.assertAlmostEqual(secs[1].start / bgm.BEAT, round(secs[1].start / bgm.BEAT))
        self.assertAlmostEqual(secs[2].start, 11.95)  # exactly at the burst
        self.assertEqual(secs[-1].end, 20.0)
        for a, b in zip(secs, secs[1:]):
            self.assertEqual(a.end, b.start)

    def test_segments_merge_a_held_chord(self):
        sec = bgm.Section("intro", 0.0, 3 * bgm.BAR + 1.0, ["D"])
        self.assertEqual(len(list(bgm.bars(sec))), 4)
        self.assertEqual(bgm.chord_segments(sec), [[0.0, sec.end, "D"]])


class Render(unittest.TestCase):
    def test_render_is_repeatable_and_in_range(self):
        a, secs = bgm.render(SCRIPT, rate=8000, seed=3)
        b, _ = bgm.render(SCRIPT, rate=8000, seed=3)
        self.assertEqual(a.shape, (20 * 8000, 2))
        self.assertTrue(np.array_equal(a, b))
        self.assertTrue(np.isfinite(a).all())
        self.assertAlmostEqual(np.abs(a).max(), 10 ** (-1 / 20))
        self.assertLess(np.abs(a[-10:]).max(), 1e-3)  # faded out


class Mix(unittest.TestCase):
    RATE = 1000

    def voice(self):
        v = np.zeros(6 * self.RATE)
        t = np.arange(self.RATE) / self.RATE
        v[3 * self.RATE: 4 * self.RATE] = 0.5 * np.sin(2 * np.pi * 50 * t)  # speaks 3-4 s
        return v

    def test_duck_gain(self):
        g = bgm.duck_gain(self.voice(), self.RATE, depth_db=9.0)
        db = 20 * np.log10(g)
        self.assertAlmostEqual(db[int(0.5 * self.RATE)], 0.0, places=3)
        self.assertAlmostEqual(db[int(2.95 * self.RATE)], -9.0, places=3)  # down before the voice
        self.assertAlmostEqual(db[int(3.5 * self.RATE)], -9.0, places=3)
        self.assertAlmostEqual(db[int(4.4 * self.RATE)], -9.0, places=3)  # held over a short pause
        self.assertAlmostEqual(db[int(5.8 * self.RATE)], 0.0, places=3)

    def test_mix_level_and_peak(self):
        rng = np.random.default_rng(0)
        music = 0.3 * rng.standard_normal((6 * self.RATE, 2))
        out = bgm.mix(music, self.voice(), self.RATE, music_db=-24.0, duck_db=9.0)
        self.assertEqual(out.shape, (6 * self.RATE, 2))
        self.assertLessEqual(np.abs(out).max(), 10 ** (-1 / 20) + 1e-9)
        quiet = out[: 2 * self.RATE]
        self.assertAlmostEqual(10 * np.log10((quiet.mean(1) ** 2).mean()), -24.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()

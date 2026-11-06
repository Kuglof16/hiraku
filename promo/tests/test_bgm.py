import os
import shutil
import unittest

import numpy as np

from promo import bgm, voice

CUTS = [("N00", 0, 4), ("N01", 4, 7), ("N05", 7, 11), ("N06", 11, 14), ("N09", 14, 17), ("N15", 17, 20),
        ("N18", 20, 24)]
SCRIPT = voice.parse_script("".join(f"{c}\t0:{a:02d}-0:{b:02d}\t{c}。\t{c}.\n" for c, a, b in CUTS))
RATE = 8000

SUMMARY = """[Parsed_ebur128_0 @ 0000] Summary:

  Integrated loudness:
    I:         -15.2 LUFS
    Threshold: -26.1 LUFS

  Loudness range:
    LRA:         5.1 LU

  True peak:
    Peak:        0.1 dBFS
"""


def has_ffmpeg() -> bool:
    return bool(os.environ.get("FFMPEG") or shutil.which("ffmpeg"))


class Story(unittest.TestCase):
    def test_midi_hz(self):
        self.assertAlmostEqual(bgm.midi_hz(69), 440.0)
        self.assertAlmostEqual(bgm.midi_hz(81), 880.0)

    def test_stretches_follow_the_cuts(self):
        sts = bgm.stretches(SCRIPT)
        self.assertEqual([s.start for s in sts], [0, 4, 7, 11, 14, 17, 20])
        self.assertEqual(sts[-1].end, 24)

    def test_more_grains_where_the_story_opens(self):
        sts = bgm.stretches(SCRIPT)
        d = lambda t: float(bgm.density_at(t, sts))  # noqa: E731
        self.assertGreater(d(9.0), d(12.5))   # title N05 over the problem N06
        self.assertGreater(d(15.5), d(12.5))  # the mechanism from N09
        self.assertGreater(d(18.5), d(15.5))  # N15-N17
        self.assertAlmostEqual(d(7.0), (d(5.5) + d(9.0)) / 2)  # ramps across a boundary

    def test_pitches_stay_in_the_pentatonic_scale(self):
        for s in bgm.stretches(SCRIPT):
            self.assertTrue(all(m % 12 in bgm.PENTATONIC for m in s.tones))
            pitches = bgm.grain_pitches(s)
            self.assertTrue(all(m % 12 in bgm.PENTATONIC for m in pitches))
            self.assertTrue(all(bgm.GRAIN_RANGE[0] <= m <= bgm.GRAIN_RANGE[1] for m in pitches))
            self.assertTrue({2, 9} <= {m % 12 for m in pitches})  # the drone's D and A

    def test_screen_times(self):
        self.assertEqual(bgm.screen_time(bgm.SCREENS, "n05", "burst"), 1.8)  # the door opens slower since 2026-11-06
        self.assertEqual(bgm.screen_time(bgm.SCREENS, "n00", "step"), 0.42)
        with self.assertRaises(ValueError):
            bgm.screen_time(bgm.SCREENS, "n05", "nothing")
        with self.assertRaises(ValueError):
            bgm.screen_time(bgm.SCREENS, "n99", "burst")

    def test_screen_times_follow_layout_json(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "layout.json").write_text(json.dumps({"n18": {"times": {"open_s": 1.25}}}), encoding="utf-8")
            self.assertEqual(bgm.screen_time(Path(d), "n18", "open"), 1.25)


class Render(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.whole, _, cls.count = bgm.render(SCRIPT, rate=RATE, seed=3)

    def test_whole(self):
        x = self.whole
        self.assertEqual(x.shape, (24 * RATE, 2))
        self.assertTrue(np.isfinite(x).all())
        self.assertLess(np.abs(x).max(), 1.0)
        self.assertLess(np.abs(x[:10]).max(), 1e-3)   # fades in
        self.assertLess(np.abs(x[-10:]).max(), 1e-3)  # and out
        self.assertGreater(self.count, 20)

    def test_same_seed_same_music(self):
        again, _, _ = bgm.render(SCRIPT, rate=RATE, seed=3)
        self.assertTrue(np.array_equal(self.whole, again))
        other, _, _ = bgm.render(SCRIPT, rate=RATE, seed=4)
        self.assertFalse(np.allclose(self.whole, other))

    def test_a_part_is_the_same_as_the_whole(self):
        part, _, _ = bgm.render(SCRIPT, rate=RATE, seed=3, start=9.3, end=15.1)
        self.assertEqual(len(part), round(15.1 * RATE) - round(9.3 * RATE))
        np.testing.assert_allclose(part, self.whole[round(9.3 * RATE): round(15.1 * RATE)], atol=1e-9)


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

    def test_limit(self):
        rng = np.random.default_rng(0)
        x = 0.1 * rng.standard_normal((4000, 2))
        x[2000] = [0.9, -0.8]
        y, reduction = bgm.limit(x, 1000, -6.0)
        self.assertLessEqual(np.abs(y).max(), bgm.db(-6.0) + 1e-12)
        self.assertLess(reduction, -5.0)
        np.testing.assert_array_equal(y[:1900], x[:1900])  # far from the peak nothing changes

    def test_parse_ebur128(self):
        self.assertEqual(bgm.parse_ebur128("noise\n" + SUMMARY), (-15.2, 0.1))
        with self.assertRaises(ValueError):
            bgm.parse_ebur128("no summary")

    @unittest.skipUnless(has_ffmpeg(), "needs ffmpeg")
    def test_mix_reaches_the_targets(self):
        rate = 44100
        t = np.arange(8 * rate) / rate
        speech = 0.6 * np.sin(2 * np.pi * 180 * t) * (np.sin(2 * np.pi * 3 * t) > 0) * ((t > 2) & (t < 6))
        music = 0.2 * np.stack([np.sin(2 * np.pi * 220 * t), np.sin(2 * np.pi * 330 * t)], 1)
        out, rep = bgm.mix(music, speech, rate, under_voice_lu=-15, duck_db=9, lufs=-16, true_peak=-3)
        self.assertEqual(out.shape, (8 * rate, 2))
        self.assertAlmostEqual(rep["lufs"], -16.0, delta=0.15)
        self.assertLessEqual(rep["true_peak"], -3.0 + 0.15)
        louder, _ = bgm.ebur128(out * bgm.db(6.0), rate)
        self.assertAlmostEqual(louder, rep["lufs"] + 6.0, delta=0.15)
        # by default the music stays 15 LU under the voice, at one level
        _, level = bgm.mix(music, speech, rate)
        self.assertAlmostEqual(level["music_lufs"], level["voice_lufs"] - 15.0, delta=0.2)


if __name__ == "__main__":
    unittest.main()

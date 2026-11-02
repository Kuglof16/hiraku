import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from promo import export


class FrameTimes(unittest.TestCase):
    def test_count_and_spacing(self):
        times = export.frame_times(0.0, 9.0, 30)
        self.assertEqual(len(times), 270)
        self.assertEqual(times[0], 0.0)
        self.assertAlmostEqual(times[-1], 269 / 30)
        self.assertAlmostEqual(times[1] - times[0], 1 / 30)

    def test_times_are_not_accumulated(self):
        # frame i is start + i / fps, so late frames carry no rounding drift
        times = export.frame_times(1.5, 181.5, 30)
        self.assertEqual(len(times), 5400)
        self.assertEqual(times[4500], 1.5 + 4500 / 30)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            export.frame_times(0, 1, 0)
        with self.assertRaises(ValueError):
            export.frame_times(2, 1, 30)


class ScreenUrl(unittest.TestCase):
    def test_query(self):
        url = export.screen_url(Path("promo/screens/n18.html"), t=2.5, layer="fg", guides=True)
        parts = urlparse(url)
        self.assertEqual(parts.scheme, "file")
        self.assertTrue(parts.path.endswith("/promo/screens/n18.html"))
        q = parse_qs(parts.query)
        self.assertEqual(float(q["t"][0]), 2.5)
        self.assertEqual(q["layer"], ["fg"])
        self.assertEqual(q["guides"], ["1"])

    def test_plain(self):
        self.assertNotIn("?", export.screen_url(Path("promo/screens/n00.html")))


class EncodeCommand(unittest.TestCase):
    def test_1080p_from_frames(self):
        cmd = export.encode_command(Path("frames"), Path("out.mp4"), 30, 1080)
        self.assertEqual(cmd[cmd.index("-framerate") + 1], "30")
        self.assertEqual(Path(cmd[cmd.index("-i") + 1]), Path("frames") / "%05d.png")
        vf = cmd[cmd.index("-vf") + 1]
        self.assertTrue(vf.startswith("scale=1920:1080:"))
        self.assertIn("format=yuv420p", vf)
        self.assertEqual(cmd[-1], "out.mp4")

    def test_4k(self):
        cmd = export.encode_command(Path("frames"), Path("out.mp4"), 30, 2160)
        self.assertTrue(cmd[cmd.index("-vf") + 1].startswith("scale=3840:2160:"))

    def test_frame_name(self):
        self.assertEqual(export.frame_name(7), "00007.png")


if __name__ == "__main__":
    unittest.main()

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock
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

    def test_app_url_keeps_its_query(self):
        url = export.screen_url("http://127.0.0.1:8765/?run=sample&video=1&t=3", t=0.5)
        parts = urlparse(url)
        self.assertEqual((parts.scheme, parts.netloc, parts.path), ("http", "127.0.0.1:8765", "/"))
        q = parse_qs(parts.query)
        self.assertEqual(q["run"], ["sample"])
        self.assertEqual(q["video"], ["1"])
        self.assertEqual(float(q["t"][0]), 0.5)  # the export time replaces the one in the URL

    def test_app_url_keeps_pace_off(self):
        q = parse_qs(urlparse(export.screen_url("http://127.0.0.1:8765/?run=sample&pace=0", t=1.0)).query)
        self.assertEqual(q["pace"], ["0"])

    def test_screen_name(self):
        self.assertEqual(export.screen_name(Path("promo/screens/n18.html")), "n18")
        self.assertEqual(export.screen_name("http://127.0.0.1:8765/?run=sample"), "app")
        self.assertEqual(export.screen_name("http://127.0.0.1:8765/grid.html?run=x"), "grid")


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

    def test_x264_by_default(self):
        cmd = export.encode_command(Path("frames"), Path("out.mp4"), 30, 1080, crf=14)
        i = cmd.index("-c:v")
        self.assertEqual(cmd[i:i + 8], ["-c:v", "libx264", "-preset", "slow", "-crf", "14", "-tune", "animation"])
        self.assertNotIn("h264_nvenc", cmd)

    def test_nvenc(self):
        cmd = export.encode_command(Path("frames"), Path("out.mp4"), 30, 1080, crf=14, encoder="nvenc")
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_nvenc")
        self.assertEqual(cmd[cmd.index("-cq") + 1], f"{14 + export.NVENC_CQ_OFFSET:g}")
        self.assertEqual(cmd[cmd.index("-rc") + 1], "vbr")
        self.assertEqual(cmd[cmd.index("-b:v") + 1], "0")
        self.assertEqual(cmd[cmd.index("-maxrate") + 1], export.NVENC_MAXRATE)
        self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "yuv420p")
        self.assertEqual(cmd[cmd.index("-profile:v") + 1], "high")
        self.assertNotIn("libx264", cmd)
        self.assertEqual(cmd[-1], "out.mp4")


class PickEncoder(unittest.TestCase):
    def test_x264_never_probes(self):
        def boom(ff):
            raise AssertionError("probed")
        self.assertEqual(export.pick_encoder("x264", "ffmpeg", available=boom), "x264")

    def test_auto(self):
        self.assertEqual(export.pick_encoder("auto", "ffmpeg", available=lambda ff: True), "nvenc")
        self.assertEqual(export.pick_encoder("auto", "ffmpeg", available=lambda ff: False), "x264")

    def test_nvenc_asked_but_missing(self):
        self.assertEqual(export.pick_encoder("nvenc", "ffmpeg", available=lambda ff: True), "nvenc")
        with self.assertRaises(SystemExit):
            export.pick_encoder("nvenc", "ffmpeg", available=lambda ff: False)

    def test_unknown(self):
        with self.assertRaises(ValueError):
            export.pick_encoder("vaapi", "ffmpeg", available=lambda ff: True)

    def test_no_ffmpeg_means_no_nvenc(self):
        self.assertFalse(export.nvenc_available(str(Path(tempfile.gettempdir()) / "no_such_ffmpeg.exe")))

    def test_cli_default_is_auto(self):
        for cmd in ("encode", "clip"):
            seen = {}
            with mock.patch.object(export, "encode", lambda *a: seen.setdefault("enc", a[-1])), \
                    mock.patch.object(export, "export_frames", lambda *a: 0):
                args = [cmd, "frames", "out.mp4"] if cmd == "encode" else [cmd, "s.html", "out.mp4", "--frames-dir", "f"]
                export.main(args)
                self.assertEqual(seen["enc"], "auto")
                export.main(args + ["--encoder", "x264"])


def _ffmpeg():
    try:
        return export.ffmpeg_path()
    except SystemExit:
        return None


FFMPEG = _ffmpeg()


@unittest.skipUnless(FFMPEG and shutil.which("ffprobe"), "needs ffmpeg and ffprobe")
class EncodeRun(unittest.TestCase):
    """Encode 15 PNG frames with each usable encoder and probe the file."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="export_enc_"))
        (cls.dir / "f").mkdir()
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                        "-i", "testsrc2=s=512x288:r=30", "-frames:v", "15", "-start_number", "0",
                        str(cls.dir / "f" / "%05d.png")], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def encode_and_probe(self, encoder):
        out = self.dir / f"{encoder}.mp4"
        subprocess.run(export.encode_command(self.dir / "f", out, 30, 144, ffmpeg=FFMPEG, encoder=encoder),
                       check=True)
        r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name,pix_fmt,width,height,nb_read_frames:format=duration",
                            "-of", "json", str(out)], capture_output=True, text=True, check=True)
        info = json.loads(r.stdout)
        s = info["streams"][0]
        self.assertEqual((s["codec_name"], s["pix_fmt"], s["width"], s["height"]), ("h264", "yuv420p", 256, 144))
        self.assertEqual(s["nb_read_frames"], "15")
        self.assertAlmostEqual(float(info["format"]["duration"]), 0.5, places=2)

    def test_x264(self):
        self.encode_and_probe("x264")

    def test_nvenc(self):
        if not export.nvenc_available(FFMPEG):
            self.skipTest("h264_nvenc is not usable here")
        self.encode_and_probe("nvenc")


if __name__ == "__main__":
    unittest.main()

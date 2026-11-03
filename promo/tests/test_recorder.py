"""The narration recorder (promo/recorder/) in Chromium with a fake microphone.

Chromium's fake device plays a beep; the test records one sentence, exports
it with and without the filter and reads the WAVs back.
"""

import base64
import importlib.util
import io
import math
import re
import shutil
import struct
import subprocess
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORDER = ROOT / "promo" / "recorder" / "index.html"

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None

SCRIPT = (
    "# test\n"
    "N00\t0:00-0:06\tテストの一文目です。\tThe first test sentence.\n"
    "N01\t0:06-0:14\t二つ目の文は、少し長めに書いてあります。\tThe second one is a little longer.\n"
    "N01\t0:06-0:14\t三つ目。\tThe third.\n"
)

ARGS = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
        "--autoplay-policy=no-user-gesture-required"]


def read_wav(data: bytes):
    with wave.open(io.BytesIO(data), "rb") as w:
        fmt = (w.getframerate(), w.getnchannels(), w.getsampwidth())
        frames = w.readframes(w.getnframes())
    samples = struct.unpack(f"<{len(frames) // 2}h", frames)
    return fmt, samples


def ffmpeg():
    try:
        from promo.export import ffmpeg_path
        return ffmpeg_path()
    except Exception:
        return shutil.which("ffmpeg")


@unittest.skipUnless(HAS_PLAYWRIGHT, "needs playwright (the video Python, not the HIRAKU venv)")
class RecorderInBrowser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=ARGS)
        cls.page = cls.browser.new_page(accept_downloads=True)
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.goto(RECORDER.as_uri())
        cls.page.evaluate("async () => { localStorage.clear(); await window.hirakuRec.ready; }")
        cls.page.evaluate("(t) => window.hirakuRec.loadScriptText(t, 'test.tsv')", SCRIPT)
        cls.page.evaluate("() => window.hirakuRec.openMic()")
        cls.page.evaluate("() => window.hirakuRec.startRecording({countdown: false})")
        cls.page.wait_for_timeout(2500)
        cls.take = cls.page.evaluate(
            "async () => { const t = await window.hirakuRec.stopRecording(); return {seconds: t.seconds, rate: t.rate, peak: t.peak}; }")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def export(self, bypass: bool):
        r = self.page.evaluate("(b) => window.hirakuRec.exportBase64('N00_1', {bypass: b})", bypass)
        return r, base64.b64decode(r["b64"])

    def test_script_is_listed(self):
        self.assertEqual(self.page.locator("#list .row").count(), 3)
        self.assertEqual(self.page.locator("#pKey").inner_text(), "N00_1")
        self.assertIn("テストの一文目", self.page.locator("#pJa").inner_text())

    def test_take_recorded(self):
        self.assertGreater(self.take["seconds"], 1.5)
        self.assertGreater(self.take["peak"], 0.01)  # the fake device's beep reached the tap
        self.assertEqual(self.page.locator("#takes .take").count(), 1)

    def test_export_format(self):
        r, data = self.export(False)
        (rate, ch, width), samples = read_wav(data)
        self.assertEqual((rate, ch, width), (44100, 1, 2))
        self.assertGreater(len(samples), 0.2 * 44100)
        # loudness was set: -16 unless the peak ceiling stopped it lower
        if r["limited"]:
            self.assertLess(r["loudnessAfter"], -16 + 0.01)
        else:
            self.assertAlmostEqual(r["loudnessAfter"], -16, places=3)
        peak = max(abs(s) for s in samples) / 32768
        self.assertLessEqual(20 * math.log10(peak), -1 + 0.05)

    def test_filter_changes_the_waveform(self):
        _, on = self.export(False)
        _, off = self.export(True)
        _, a = read_wav(on)
        _, b = read_wav(off)
        n = min(len(a), len(b))
        self.assertGreater(n, 1000)
        dot = sum(x * y for x, y in zip(a[:n], b[:n]))
        na = math.sqrt(sum(x * x for x in a[:n]))
        nb = math.sqrt(sum(y * y for y in b[:n]))
        corr = dot / (na * nb)
        self.assertLess(corr, 0.98, "filter on and off gave nearly the same waveform")

    def test_raw_preset_matches_bypass(self):
        self.page.evaluate("() => window.hirakuRec.applyPreset('raw')")
        try:
            _, raw = self.export(False)
            _, off = self.export(True)
        finally:
            self.page.evaluate("() => window.hirakuRec.applyPreset('clear')")
        _, a = read_wav(raw)
        _, b = read_wav(off)
        # every stage is an identity, so the trimmed lengths agree to within
        # the compressor's look-ahead delay
        self.assertGreater(min(len(a), len(b)), 1000)
        self.assertLess(abs(len(a) - len(b)), 0.02 * 44100)

    def test_download_button(self):
        with self.page.expect_download() as dl:
            self.page.click("#exportOne")
        self.assertEqual(dl.value.suggested_filename, "N00_1.wav")
        (rate, ch, width), _ = read_wav(Path(dl.value.path()).read_bytes())
        self.assertEqual((rate, ch, width), (44100, 1, 2))

    def test_keys_move_between_sentences(self):
        self.page.keyboard.press("ArrowRight")
        self.assertEqual(self.page.locator("#pKey").inner_text(), "N01_1")
        self.page.keyboard.press("ArrowLeft")
        self.assertEqual(self.page.locator("#pKey").inner_text(), "N00_1")

    @unittest.skipUnless(ffmpeg(), "needs ffmpeg")
    def test_loudness_agrees_with_ffmpeg(self):
        r, data = self.export(False)
        proc = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-i", "pipe:0", "-af", "ebur128",
                               "-f", "null", "-"], input=data, capture_output=True, check=True)
        text = proc.stderr.decode("utf-8", "replace")
        m = re.search(r"I:\s+(-?[0-9.]+) LUFS", text[text.rindex("Summary:"):])
        self.assertIsNotNone(m)
        self.assertAlmostEqual(float(m.group(1)), r["loudnessAfter"], delta=0.5)

    def test_no_page_errors(self):
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

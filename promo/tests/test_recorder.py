"""The narration recorder (promo/recorder/) in Chromium with a fake microphone,
and its local server (promo/recorder/serve.py).

Chromium's fake device plays a beep; the test records one sentence, exports
it with and without the filter and reads the WAVs back. The server tests
need only the standard library; the served-page test needs playwright.
"""

import base64
import http.client
import importlib.util
import io
import json
import math
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORDER = ROOT / "promo" / "recorder" / "index.html"
SERVE = ROOT / "promo" / "recorder" / "serve.py"


def load_serve():
    spec = importlib.util.spec_from_file_location("hiraku_recorder_serve", SERVE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def tone_wav(seconds: float = 0.5, rate: int = 44100, freq: float = 440.0) -> bytes:
    n = int(seconds * rate)
    frames = struct.pack(f"<{n}h", *(int(8000 * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)))
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return out.getvalue()

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

    def test_low_cut_stage(self):
        # sines through the chain with only the low cut on: the level change in dB
        r = self.page.evaluate("""async () => {
          const D = window.HirakuDSP;
          const s = D.cloneSettings(D.PRESETS.raw);
          s.hum.on = true;
          const gain = async (f) => {
            const n = 44100, x = new Float32Array(n);
            for (let i = 0; i < n; i++) x[i] = 0.1 * Math.sin(2 * Math.PI * f * i / n);
            const y = await D.renderChain(x, n, s, false);
            let a = 0;
            for (let i = n / 2; i < n; i++) a += y[i] * y[i];  // the second half, once the filters settle
            return 20 * Math.log10(Math.sqrt(a / (n / 2)) / (0.1 / Math.SQRT2));
          };
          const out = {};
          for (const f of [30, 50, 100, 150, 175, 325, 550, 575, 1000, 3000]) out[f] = await gain(f);
          return out;
        }""")
        self.assertLess(r["30"], -40)
        self.assertLess(r["50"], -40)    # mains: the highpass and the notch
        self.assertLess(r["100"], -30)   # 2nd harmonic: the notch on the cutoff
        self.assertLess(r["150"], -30)   # 3rd harmonic: the notch alone
        for f in ("175", "325"):  # halfway between two notches: their skirts take about 0.5 dB
            self.assertGreater(r[f], -0.6, f)
        self.assertGreater(r["550"], -1)  # the 11th harmonic is past the default ten notches
        for f in ("575", "1000", "3000"):
            self.assertAlmostEqual(r[f], 0, delta=0.3, msg=f)

    HALF_LSB = 0.5 / 32768  # below this a sample is 0 in the 16-bit export

    EDGE_TAKE_JS = """async (cutWhileSounding) => {
      // raw preset (every stage an identity) so the export is the take itself:
      // 0.3 s of room tone, 1 s of tone, then room tone to the stop, or the
      // tone to the stop
      const D = window.HirakuDSP;
      const s = D.cloneSettings(D.PRESETS.raw);
      s.out.normalize = false;
      const fs = 44100, n = Math.round(1.8 * fs), x = new Float32Array(n);
      let seed = 1;
      const noise = () => ((seed = (seed * 16807) % 2147483647) / 2147483647 - 0.5) * 0.002;
      const toneEnd = cutWhileSounding ? n : Math.round(1.3 * fs);
      for (let i = 0; i < n; i++) {
        x[i] = noise();
        if (i >= 0.3 * fs && i < toneEnd) x[i] += 0.3 * Math.sin(2 * Math.PI * 440 * i / fs);
      }
      const r = await D.processTake(x, fs, s);
      const y = r.samples, ms = (t) => Math.round(t * fs / 1000);
      const maxAbs = (a, b) => { let m = 0; for (let i = a; i < Math.min(b, y.length); i++) m = Math.max(m, Math.abs(y[i])); return m; };
      const quarters = [0, 1, 2, 3].map((k) => maxAbs(y.length - ms(150) + k * ms(37.5), y.length - ms(150) + (k + 1) * ms(37.5)));
      return { len: y.length, first: y[0], last: y[y.length - 1], start: r.trim.start, end: r.trim.end,
               head: maxAbs(0, ms(1)), quarters, fade: D.EDGE, mid: maxAbs(ms(500), ms(600)),
               srcEnd: n / fs };
    }"""

    def test_edges_fade_after_the_last_word(self):
        r = self.page.evaluate(self.EDGE_TAKE_JS, False)
        self.assertEqual(r["first"], 0)
        self.assertLess(abs(r["last"]), self.HALF_LSB)
        self.assertLess(r["head"], 0.002 * 0.05)  # the first ms is still near the start of the fade-in
        # the export runs to the last word + hold + fade-out (1.3 + 0.02 + 0.15 s)
        self.assertAlmostEqual(r["end"], 1.3 + r["fade"]["hold"] + r["fade"]["fadeOut"], delta=0.011)
        # the fade-out falls through its four quarters
        q = r["quarters"]
        self.assertTrue(all(a >= b for a, b in zip(q, q[1:])), q)
        self.assertGreater(r["mid"], 0.29)  # the voice itself is untouched

    def test_edges_stop_before_the_take_stops(self):
        r = self.page.evaluate(self.EDGE_TAKE_JS, True)
        self.assertLess(abs(r["last"]), self.HALF_LSB)
        # nothing of the render's tail or the stop: the export ends stopGuard before the take does
        self.assertAlmostEqual(r["end"], r["srcEnd"] - r["fade"]["stopGuard"], delta=0.001)
        q = r["quarters"]
        self.assertGreater(q[0], 0.2)  # the fade-out moved onto the still-sounding tone
        self.assertTrue(all(a >= b for a, b in zip(q, q[1:])), q)

    def test_old_settings_get_the_low_cut(self):
        s = self.page.evaluate("() => window.HirakuDSP.completeSettings({hp: {on: true, freq: 80}})")
        self.assertEqual(s["hum"], {"on": True, "cut": 100, "mains": 50, "harmonics": 10})
        self.assertEqual(self.page.locator('#knobs .stage[data-stage="hum"] input[type=range]').count(), 3)

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

    def test_file_mode_keeps_the_manual_buttons(self):
        self.assertTrue(self.page.locator("#scriptPick").is_visible())
        self.assertTrue(self.page.locator("#synthPick").is_visible())
        self.assertFalse(self.page.locator("#autoSaveBox").is_visible())
        self.assertIn("ダウンロード", self.page.locator("#stOut").inner_text())
        self.assertEqual(self.page.locator("#exportOne").inner_text(), "この文を書き出す")


def make_work(root: Path) -> Path:
    """A work folder: the test script and synthesized voices for two of its sentences."""
    (root / "script").mkdir(parents=True)
    (root / "script" / "narration.tsv").write_bytes(SCRIPT.encode("utf-8"))
    synth = root / "voice" / "aida_calm"
    synth.mkdir(parents=True)
    for name in ("N00_1.wav", "N01_1.wav"):
        (synth / name).write_bytes(tone_wav(1.0))
    (synth / "voice.json").write_text('{"lines": []}', encoding="utf-8")
    return root


class ServedWork:
    """serve.py on a free port in a thread, over a temporary work folder."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_work(Path(self.tmp.name) / "work")
        self.serve = load_serve()
        self.work = self.serve.Work(self.root)
        self.srv = self.serve.make_server(self.work, port=0, quiet=True)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            return r.status, r.getheader("Content-Type") or "", r.read()
        finally:
            c.close()


class ServeApi(unittest.TestCase):
    def setUp(self):
        self.s = ServedWork()
        self.addCleanup(self.s.close)
        self.out = self.s.root / "voice" / "recorded"

    def json(self, method, path, body=None, headers=None):
        code, _, data = self.s.request(method, path, body, headers)
        return code, json.loads(data)

    def test_status(self):
        code, st = self.json("GET", "/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(st["app"], "hiraku-recorder")
        self.assertEqual(st["script"]["lines"], 3)
        self.assertEqual(st["script"]["label"], "script/narration.tsv")
        self.assertEqual(st["synth"]["files"], ["N00_1.wav", "N01_1.wav"])
        self.assertEqual(st["out"], {"label": "voice/recorded", "have": [], "missing": 3})

    def test_script_and_synth(self):
        code, ctype, data = self.s.request("GET", "/api/script")
        self.assertEqual((code, data.decode("utf-8")), (200, SCRIPT))
        code, ctype, data = self.s.request("GET", "/api/synth/N00_1.wav")
        self.assertEqual((code, ctype), (200, "audio/wav"))
        self.assertEqual(data[:4], b"RIFF")
        self.assertEqual(self.s.request("GET", "/api/synth/voice.json")[0], 200)
        for bad in ("/api/synth/../script/narration.tsv", "/api/synth/..%2Fscript%2Fnarration.tsv",
                    "/api/synth/N09_1.wav", "/api/synth/notes.txt"):
            self.assertEqual(self.s.request("GET", bad)[0], 404, bad)

    def test_static_files(self):
        code, ctype, data = self.s.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertTrue(ctype.startswith("text/html"))
        self.assertIn(b"recorder.js", data)
        code, ctype, _ = self.s.request("GET", "/recorder.js")
        self.assertEqual((code, ctype.split(";")[0]), (200, "text/javascript"))
        for bad in ("/serve.py", "/../voice.py", "/..%2Fvoice.py", "/README.md"):
            self.assertEqual(self.s.request("GET", bad)[0], 404, bad)

    def test_put_take_writes_wav_and_voice_json(self):
        code, res = self.json("PUT", "/api/take/N00_1.wav", tone_wav(1.5), {"Content-Type": "audio/wav"})
        self.assertEqual(code, 200, res)
        self.assertEqual(res["saved"], "N00_1.wav")
        self.assertEqual((res["have"], res["total"], res["missing"]), (["N00_1.wav"], 3, 2))
        self.assertEqual(res["folder"], "voice/recorded")
        self.assertTrue((self.out / "N00_1.wav").is_file())
        meta = json.loads((self.out / "voice.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["source"], "recorded")
        self.assertEqual([r["file"] for r in meta["lines"]], ["N00_1.wav"])
        self.assertAlmostEqual(meta["lines"][0]["seconds"], 1.5, places=2)
        self.assertEqual(meta["missing"], ["N01_1.wav", "N01_2.wav"])
        self.assertEqual(list(self.out.glob("*.part")), [])
        # saving again replaces the file
        code, res = self.json("PUT", "/api/take/N00_1.wav", tone_wav(0.8))
        self.assertEqual(code, 200)
        meta = json.loads((self.out / "voice.json").read_text(encoding="utf-8"))
        self.assertAlmostEqual(meta["lines"][0]["seconds"], 0.8, places=2)
        _, st = self.json("GET", "/api/status")
        self.assertEqual(st["out"]["have"], ["N00_1.wav"])

    def test_put_outside_the_folder_is_refused(self):
        wav = tone_wav(0.3)
        for path in ("/api/take/../N00_1.wav", "/api/take/..%2FN00_1.wav", "/api/take/..%5CN00_1.wav",
                     "/api/take/../../script/narration.tsv", "/api/take/%2E%2E%2Fevil_1.wav",
                     "/api/take/sub/N00_1.wav", "/api/take/C:%5Cevil_1.wav"):
            code, _, _ = self.s.request("PUT", path, wav)
            self.assertIn(code, (400, 404), path)
        self.assertFalse(self.out.exists())
        self.assertFalse((self.s.root / "voice" / "N00_1.wav").exists())
        self.assertEqual(SCRIPT.encode("utf-8"), (self.s.root / "script" / "narration.tsv").read_bytes())

    def test_put_name_not_in_script_is_refused(self):
        for name in ("N09_1.wav", "N00_2.wav", "N01_3.wav", "voice.json", "N00_1.WAV"):
            code, res = self.json("PUT", f"/api/take/{name}", tone_wav(0.3))
            self.assertEqual(code, 400, name)
        self.assertFalse(self.out.exists())

    def test_put_bad_body_is_refused(self):
        code, res = self.json("PUT", "/api/take/N00_1.wav", b"not a wav at all")
        self.assertEqual(code, 400)
        self.assertIn("WAV", res["error"])
        self.assertFalse(self.out.exists())

    def refused(self, method, path, body=None, headers=None):
        """True when the server answers 403 or drops the connection first. On Windows the server
        closing before the client has sent the whole body shows up as WinError 10053/10054."""
        try:
            return self.s.request(method, path, body, headers)[0] == 403
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            return True

    def test_other_hosts_and_origins_are_refused(self):
        self.assertTrue(self.refused("GET", "/api/status", headers={"Host": "evil.example:80"}))
        self.assertTrue(self.refused("PUT", "/api/take/N00_1.wav", tone_wav(0.3),
                                     {"Origin": "http://evil.example"}))
        self.assertFalse(self.out.exists())
        code, _, _ = self.s.request("PUT", "/api/take/N00_1.wav", tone_wav(0.3),
                                    {"Origin": f"http://localhost:{self.s.port}"})
        self.assertEqual(code, 200)

    def test_next_port_when_taken(self):
        srv2 = self.s.serve.make_server(self.s.work, port=self.s.port, quiet=True)
        try:
            self.assertNotEqual(srv2.server_address[1], self.s.port)
            self.assertEqual(srv2.server_address[0], "127.0.0.1")
        finally:
            srv2.server_close()

    def test_missing_script(self):
        (self.s.root / "script" / "narration.tsv").rename(self.s.root / "script" / "gone.tsv")
        _, st = self.json("GET", "/api/status")
        self.assertEqual(st["script"]["error"], "not found")
        self.assertEqual(self.s.request("GET", "/api/script")[0], 404)
        self.assertEqual(self.s.request("PUT", "/api/take/N00_1.wav", tone_wav(0.3))[0], 409)


@unittest.skipUnless(HAS_PLAYWRIGHT, "needs playwright (the video Python, not the HIRAKU venv)")
class ServedPageInBrowser(unittest.TestCase):
    """The page opened from serve.py reads the work folder and saves takes by itself."""

    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.s = ServedWork()
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=ARGS)
        cls.context = cls.browser.new_context()
        cls.page = cls.context.new_page()
        cls.errors = []
        cls.page.on("pageerror", lambda e: cls.errors.append(str(e)))
        cls.page.goto(f"http://127.0.0.1:{cls.s.port}/")
        cls.page.evaluate("async () => { localStorage.clear(); await window.hirakuRec.ready; }")
        cls.page.reload()
        cls.page.evaluate("async () => { await window.hirakuRec.ready; }")
        cls.loaded = {k: cls.page.locator(f"#{k}").inner_text() for k in ("stScript", "stSynth", "stOut")}
        cls.page.evaluate("() => window.hirakuRec.openMic()")
        cls.page.evaluate("() => window.hirakuRec.startRecording({countdown: false})")
        cls.page.wait_for_timeout(2000)
        cls.page.evaluate("async () => { await window.hirakuRec.stopRecording(); await window.hirakuRec.idle(); }")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.s.close()

    def test_script_and_synth_load_by_themselves(self):
        self.assertIn("narration.tsv", self.loaded["stScript"])
        self.assertIn("3 文", self.loaded["stScript"])
        self.assertIn("2 本 / 3", self.loaded["stSynth"])
        self.assertIn("voice/recorded", self.loaded["stOut"])
        self.assertIn("0/3", self.loaded["stOut"])
        self.assertEqual(self.page.locator("#list .row").count(), 3)
        self.assertIn("合成", self.page.locator("#list .row").first.inner_text())

    def test_manual_pickers_hidden(self):
        self.assertFalse(self.page.locator("#scriptPick").is_visible())
        self.assertFalse(self.page.locator("#synthPick").is_visible())
        self.assertFalse(self.page.locator("#exportDir").is_visible())
        self.assertTrue(self.page.locator("#autoSave").is_checked())
        self.assertEqual(self.page.locator("#exportOne").inner_text(), "この文を保存")

    def test_take_saved_by_itself(self):
        out = self.s.root / "voice" / "recorded"
        with wave.open(str(out / "N00_1.wav"), "rb") as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (44100, 1, 2))
        meta = json.loads((out / "voice.json").read_text(encoding="utf-8"))
        self.assertEqual([r["file"] for r in meta["lines"]], ["N00_1.wav"])
        self.assertIn("1/3", self.page.locator("#stOut").inner_text())
        self.assertIn("保存 ✓", self.page.locator("#list .row").first.inner_text())
        self.assertIn("voice/recorded に保存", self.page.locator("#exportInfo").inner_text())

    def test_takes_come_back_after_a_reload(self):
        # the raw take is kept in IndexedDB, so "save all" works again after the page reopens
        page = self.context.new_page()  # the same storage as the page that recorded
        try:
            page.goto(f"http://127.0.0.1:{self.s.port}/")
            page.evaluate("async () => { await window.hirakuRec.ready; }")
            n = page.evaluate("() => (window.hirakuRec.state.takes['N00_1'] || []).length")
            self.assertEqual(n, 1)
        finally:
            page.close()

    def test_no_page_errors(self):
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

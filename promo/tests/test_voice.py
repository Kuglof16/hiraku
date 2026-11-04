import contextlib
import io
import json
import tempfile
import unittest
import wave
from pathlib import Path

from promo import voice

SCRIPT = (
    "# test script\n"
    "N00\t0:00-0:05\t一つ目の文。\tFirst.\n"
    "N01\t0:05-0:12\t二つ目の文。\tSecond.\n"
    "N01\t0:05-0:12\t三つ目の文。\tThird.\n"
)


def write_wav(path: Path, seconds: float, rate: int = 44100, channels: int = 1) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(2 * channels * int(round(seconds * rate))))


class Adopt(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.script = self.dir / "narration.tsv"
        self.script.write_text(SCRIPT, encoding="utf-8")
        self.wavs = self.dir / "rec"
        self.wavs.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_all_present(self):
        write_wav(self.wavs / "N00_1.wav", 2.0)
        write_wav(self.wavs / "N01_1.wav", 1.5)
        write_wav(self.wavs / "N01_2.wav", 3.25)
        res = voice.adopt(self.script, self.wavs)
        self.assertEqual(res["missing"], [])
        self.assertEqual(res["extra"], [])
        self.assertEqual(res["odd_format"], [])
        meta = json.loads((self.wavs / "voice.json").read_text(encoding="utf-8"))
        self.assertEqual([r["file"] for r in meta["lines"]], ["N00_1.wav", "N01_1.wav", "N01_2.wav"])
        self.assertEqual([r["seconds"] for r in meta["lines"]], [2.0, 1.5, 3.25])
        first = meta["lines"][0]
        self.assertEqual((first["cut"], first["start"], first["end"]), ("N00", 0.0, 5.0))
        self.assertEqual((first["ja"], first["en"]), ("一つ目の文。", "First."))
        # N01: 1.5 + 3.25 + one gap, against a 7 s slot
        n01 = meta["fit"][1]
        self.assertEqual(n01["cut"], "N01")
        self.assertAlmostEqual(n01["voice_s"], round(1.5 + 3.25 + voice.SENTENCE_GAP_S, 2))
        self.assertAlmostEqual(n01["spare_s"], round(7 - n01["voice_s"], 2))

    def test_missing_and_extra(self):
        write_wav(self.wavs / "N00_1.wav", 1.0)
        write_wav(self.wavs / "N01_2.wav", 1.0)
        write_wav(self.wavs / "N09_1.wav", 1.0)
        res = voice.adopt(self.script, self.wavs)
        self.assertEqual(res["missing"], ["N01_1.wav"])
        self.assertEqual(res["extra"], ["N09_1.wav"])
        meta = json.loads((self.wavs / "voice.json").read_text(encoding="utf-8"))
        self.assertEqual([r["file"] for r in meta["lines"]], ["N00_1.wav", "N01_2.wav"])
        self.assertEqual(meta["missing"], ["N01_1.wav"])

    def test_command_reports_and_exit_status(self):
        write_wav(self.wavs / "N00_1.wav", 1.0)
        write_wav(self.wavs / "N01_1.wav", 1.0, rate=48000)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = voice.cmd_adopt(self.script, self.wavs)
        self.assertEqual(status, 1)
        text = out.getvalue()
        self.assertIn("missing: N01_2.wav", text)
        self.assertIn("N01_1.wav: 48000 Hz", text)

    def test_complete_set_exits_zero(self):
        for name in ("N00_1.wav", "N01_1.wav", "N01_2.wav"):
            write_wav(self.wavs / name, 0.5)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(voice.cmd_adopt(self.script, self.wavs), 0)


def take(rate: int = 44100, tone_end: float = 1.3, sound_end: float = 1.7, length: float = 1.8) -> list[float]:
    """A recorded sentence as the recorder leaves it: room tone (white noise,
    about -60 dBFS) from the first sample, a 440 Hz tone from 0.3 s to
    tone_end, the take stopped at sound_end (after it a 25 Hz decay only)."""
    import math
    import random
    rnd = random.Random(1)
    x = []
    for i in range(int(length * rate)):
        t = i / rate
        if t < sound_end:
            v = rnd.uniform(-0.0017, 0.0017)
            if 0.3 <= t < tone_end:
                v += 0.3 * math.sin(2 * math.pi * 440 * t)
        else:
            v = 0.003 * math.exp(-(t - sound_end) / 0.03) * math.sin(2 * math.pi * 25 * (t - sound_end))
        x.append(v)
    return x


class Edges(unittest.TestCase):
    RATE = 44100

    def at(self, t: float) -> int:
        return int(round(t * self.RATE))

    def test_finds_onset_voice_end_and_sound_end(self):
        e = voice.find_edges(take(), self.RATE)
        self.assertAlmostEqual(e["onset"] / self.RATE, 0.3, delta=0.006)
        self.assertAlmostEqual(e["voice_end"] / self.RATE, 1.3, delta=0.006)
        self.assertAlmostEqual(e["sound_end"] / self.RATE, 1.7, delta=0.006)

    def test_room_tone_kept_only_around_the_voice(self):
        x = take()
        y, spans = voice.fade_edges(x, self.RATE)
        self.assertEqual(len(y), len(x))  # the timeline laid out from it does not move
        a, b = spans["fade_in_s"]
        self.assertAlmostEqual(b - a, voice.EDGE_FADE_IN_S, delta=voice.EDGE_SNAP_S + 0.001)
        self.assertTrue(all(v == 0 for v in y[: spans["fade_in_n"][0]]))
        a, b = spans["fade_out_s"]
        self.assertAlmostEqual(a, 1.3 + voice.EDGE_HOLD_S, delta=0.006)
        self.assertAlmostEqual(b - a, voice.EDGE_FADE_OUT_S, delta=voice.EDGE_SNAP_S + 0.001)
        i, j = spans["fade_out_n"]
        self.assertTrue(all(v == 0 for v in y[j:]))  # the click and the stop are gone
        # the voice is untouched, the fade-out only falls
        self.assertEqual(y[self.at(0.5):self.at(1.2)], x[self.at(0.5):self.at(1.2)])
        n = j - i
        quarters = [max(abs(v) for v in y[i + k * n // 4:i + (k + 1) * n // 4]) for k in range(4)]
        self.assertEqual(quarters, sorted(quarters, reverse=True))

    def test_take_stopped_while_sounding(self):
        x = take(tone_end=1.7)
        y, spans = voice.fade_edges(x, self.RATE)
        a, b = spans["fade_out_s"]
        self.assertLessEqual(b, 1.7 + voice.EDGE_FRAME_S + 0.001)  # at the stop, to the 5 ms frame
        self.assertAlmostEqual(b - a, voice.EDGE_FADE_OUT_S, delta=voice.EDGE_SNAP_S + 0.001)
        self.assertGreater(max(abs(v) for v in y[self.at(a):self.at(a) + 100]), 0.2)  # the fade lies on the tone
        self.assertEqual(y[-1], 0)

    def test_command_writes_another_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "rec", Path(tmp) / "rec_v2"
            src.mkdir()
            voice.write_pcm16(src / "N00_1.wav", take(), self.RATE)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(voice.cmd_edges(src, dst), 0)
            y, rate = voice.read_pcm16(dst / "N00_1.wav")
            x, _ = voice.read_pcm16(src / "N00_1.wav")
            self.assertEqual((rate, len(y)), (self.RATE, len(x)))
            self.assertEqual(y[0], 0)
            self.assertNotEqual(x[0], 0)  # the source is left as it was
            with self.assertRaises(SystemExit):
                voice.cmd_edges(src, src)


class SentenceFiles(unittest.TestCase):
    def test_names_follow_cut_order(self):
        lines = voice.parse_script(SCRIPT)
        self.assertEqual(voice.sentence_files(lines), ["N00_1.wav", "N01_1.wav", "N01_2.wav"])


if __name__ == "__main__":
    unittest.main()

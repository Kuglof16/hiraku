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


class SentenceFiles(unittest.TestCase):
    def test_names_follow_cut_order(self):
        lines = voice.parse_script(SCRIPT)
        self.assertEqual(voice.sentence_files(lines), ["N00_1.wav", "N01_1.wav", "N01_2.wav"])


if __name__ == "__main__":
    unittest.main()

import array
import math
import unittest

from promo import subs, voice


class Script(unittest.TestCase):
    TEXT = (
        "# comment\n"
        "\n"
        "N00\t0:00-0:07\tHIRAKU は、作り手へ。\tHIRAKU, for makers.\n"
        "N08b\t1:04-1:13\t一文目。\tFirst.\n"
        "N08b\t1:04-1:13\t二文目。\tSecond.\n"
    )

    def test_parse(self):
        lines = voice.parse_script(self.TEXT)
        self.assertEqual([ln.cut for ln in lines], ["N00", "N08b", "N08b"])
        self.assertEqual((lines[1].start, lines[1].end), (64.0, 73.0))
        self.assertEqual(lines[0].en, "HIRAKU, for makers.")

    def test_bad_rows(self):
        with self.assertRaises(ValueError):
            voice.parse_script("N00\t0:00-0:07\tonly three")
        with self.assertRaises(ValueError):
            voice.parse_slot("0:07-0:07")

    def test_sentence_files(self):
        lines = voice.parse_script(self.TEXT)
        self.assertEqual(voice.sentence_files(lines), ["N00_1.wav", "N08b_1.wav", "N08b_2.wav"])

    def test_readings_longest_first(self):
        r = voice.parse_readings("作り\tつくり\n作り手\tつくりて\n")
        self.assertEqual(voice.apply_readings("すべての作り手へ", r), "すべてのつくりてへ")

    def test_fit_report(self):
        lines = voice.parse_script(self.TEXT)
        rep = voice.fit_report(lines, [5.0, 4.0, 4.5], gap=0.5)
        self.assertEqual(rep[0], {"cut": "N00", "voice_s": 5.0, "slot_s": 7.0, "spare_s": 2.0})
        self.assertEqual(rep[1]["voice_s"], 9.0)
        self.assertEqual(rep[1]["spare_s"], 0.0)


class Chunks(unittest.TestCase):
    def test_width(self):
        self.assertEqual(subs.em_width("CAE"), 1.5)
        self.assertEqual(subs.em_width("解析"), 2.0)

    def test_short_sentence_is_one_chunk(self):
        self.assertEqual(subs.chunk_sentence("段差を越えられるか。"), ["段差を越えられるか。"])

    def test_long_sentence_splits_at_commas(self):
        s = "でこぼこ道でも、傾いても、風を受けても、急にハンドルを切っても、倒れないか。"
        chunks = subs.chunk_sentence(s, max_em=20)
        self.assertEqual("".join(chunks), s)
        self.assertTrue(all(subs.em_width(c) <= 20 for c in chunks))
        self.assertTrue(all(c.endswith(("、", "。")) for c in chunks))
        self.assertGreater(len(chunks), 1)

    def test_chunks_are_balanced(self):
        s = "でこぼこ道でも、傾いても、風を受けても、急にハンドルを切っても、倒れないか。"
        self.assertEqual(subs.chunk_sentence(s, max_em=34),
                         ["でこぼこ道でも、傾いても、風を受けても、", "急にハンドルを切っても、倒れないか。"])

    def test_a_part_wider_than_a_line_stays_whole(self):
        s = "あ" * 40 + "、" + "い" * 5 + "。"
        self.assertEqual(subs.chunk_sentence(s, max_em=34), ["あ" * 40 + "、", "い" * 5 + "。"])

    def test_boundaries_snap_to_pauses(self):
        chunks = ["あいうえお、", "かきくけこ。"]
        # text alone puts the boundary at 2.5 s; the audio has a pause at 2.8 s
        self.assertEqual(subs.chunk_times(chunks, 5.0, [2.8]), [0.0, 2.8])
        # no pause near enough: keep the text-length guess
        self.assertEqual(subs.chunk_times(chunks, 5.0, [4.5]), [0.0, 2.5])

    def test_find_pauses(self):
        rate = 1000
        tone = [int(8000 * math.sin(i / 3)) for i in range(400)]
        sig = array.array("h", [0] * 100 + tone + [0] * 200 + tone + [0] * 100)
        pauses = subs.find_pauses(sig, rate)
        self.assertEqual(len(pauses), 1)  # leading and trailing silence do not count
        self.assertAlmostEqual(pauses[0], 0.6, delta=0.02)


class Timeline(unittest.TestCase):
    def test_place_sentences(self):
        lines = [
            {"cut": "N00", "start": 0.0, "end": 7.0, "seconds": 5.0, "ja": "a", "en": "a"},
            {"cut": "N01", "start": 7.0, "end": 11.0, "seconds": 1.5, "ja": "b", "en": "b"},
            {"cut": "N01", "start": 7.0, "end": 11.0, "seconds": 1.0, "ja": "c", "en": "c"},
        ]
        placed = subs.place_sentences(lines, lead=0.3, gap=0.4)
        self.assertEqual([p["at"] for p in placed], [0.3, 7.3, 9.2])
        shifted = subs.place_sentences(lines[1:], lead=0.3, gap=0.4, origin=7.0)
        self.assertEqual([p["at"] for p in shifted], [0.3, 2.2])

    def test_a_late_cut_pushes_the_next(self):
        lines = [
            {"cut": "N00", "start": 0.0, "end": 4.0, "seconds": 4.5, "ja": "a", "en": "a"},
            {"cut": "N01", "start": 4.0, "end": 8.0, "seconds": 2.0, "ja": "b", "en": "b"},
        ]
        placed = subs.place_sentences(lines, lead=0.3, gap=0.4)
        # N00 ends at 4.8; N01 would start at 4.3, so it waits until 4.8 + gap
        self.assertEqual([p["at"] for p in placed], [0.3, 5.2])
        self.assertEqual(subs.late_cuts(placed), [("N00", 0.8)])

    def test_script_overrides_slots_and_text(self):
        voiced = [
            {"cut": "N00", "start": 0.0, "end": 7.0, "seconds": 5.0, "ja": "一。", "en": "One.", "file": "N00_1.wav"},
            {"cut": "N01", "start": 7.0, "end": 9.0, "seconds": 1.0, "ja": "二。", "en": "Two.", "file": "N01_1.wav"},
            {"cut": "N01", "start": 7.0, "end": 9.0, "seconds": 1.0, "ja": "三。", "en": "Three.", "file": "N01_2.wav"},
        ]
        script = voice.parse_script(
            "N00\t0:00-0:08\t一。\tOne.\n"
            "N01\t0:08-0:11\t二。\tTwo.\n"
            "N01\t0:08-0:11\t三つ。\tThree!\n")
        lines, stale = subs.apply_script(voiced, script)
        self.assertEqual([(ln["start"], ln["end"]) for ln in lines], [(0.0, 8.0), (8.0, 11.0), (8.0, 11.0)])
        self.assertEqual(lines[2]["ja"], "三つ。")
        self.assertEqual(lines[2]["seconds"], 1.0)  # audio as measured
        self.assertEqual(stale, ["N01_2.wav"])
        with self.assertRaises(ValueError):
            subs.apply_script(voiced[:2], script)

    def test_cues_and_english(self):
        placed = [{"at": 1.0, "seconds": 4.0, "ja": "短い。", "en": "Short."}]
        cues = subs.build_cues(placed, [[0.0]])
        self.assertEqual((cues[0].start, cues[0].end), (1.0, 5.0))
        two = [subs.Cue(0, 2, "a、", "E"), subs.Cue(2, 4, "b。", "E"), subs.Cue(5, 6, "c。", "F")]
        self.assertEqual(subs.merge_english(two), [("E", 0, 4), ("F", 5, 6)])


class Ass(unittest.TestCase):
    def test_time(self):
        self.assertEqual(subs.ass_time(0), "0:00:00.00")
        self.assertEqual(subs.ass_time(64.256), "0:01:04.26")

    def test_colours(self):
        self.assertEqual(subs.ass_colour("F4F1EA"), "&H00EAF1F4")
        self.assertEqual(subs.ass_inline_colour("E7B36A"), "&H6AB3E7&")

    def test_highlight_and_escape(self):
        out = subs.highlight("製造業の 99 % は{x}", ["99 %"])
        self.assertIn("{\\c&H6AB3E7&}99 %{\\c&HEAF1F4&}", out)
        self.assertNotIn("{x}", out)

    def test_document(self):
        doc = subs.write_ass([subs.Cue(0.3, 2.0, "一行。", "One line.")])
        self.assertIn("PlayResX: 1920", doc)
        self.assertIn("\\an2\\pos(960,995)", doc)
        self.assertIn("\\an2\\pos(960,1040)", doc)

    def test_burn_command(self):
        from pathlib import Path
        cmd = subs.burn_command(Path("v.mp4"), Path("x/subs.ass"), Path("o.mp4"), Path("n.wav"))
        vf = cmd[cmd.index("-vf") + 1]
        self.assertTrue(vf.startswith("drawbox=x=0:y=ih*910/1080:w=iw:h=ih*170/1080"))
        self.assertTrue(vf.endswith(",ass=subs.ass"))
        self.assertIn("-shortest", cmd)


if __name__ == "__main__":
    unittest.main()

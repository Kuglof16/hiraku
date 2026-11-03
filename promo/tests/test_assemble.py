import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from promo import assemble
from promo.assemble import Join
from promo.export import NVENC_CQ_OFFSET, nvenc_available
from promo.subs import burn_command

REPO_TABLE = Path(__file__).resolve().parents[1] / "assembly.json"


def script_text(slots):
    """A narration script with one row per (cut, start, end)."""
    def clock(t):
        return f"{int(t // 60)}:{t % 60:04.1f}"
    return "".join(f"{c}\t{clock(a)}-{clock(b)}\tja\ten\n" for c, a, b in slots)


def table_data(segments, **extra):
    return {"script": "s.tsv", "default_join": {"kind": "dissolve", "seconds": 0.25},
            "segments": segments, **extra}


def planned(segments, slots, fps=30):
    data = table_data(segments, fps=fps)
    table = assemble.load_table(data, {"WORK": "/w"})
    return assemble.plan(table, assemble.slot_table(script_text(slots)))


THREE = [("A", 0, 4), ("B", 4, 9.5), ("C", 9.5, 12)]


def three_segments(j1="dissolve", s1=0.4, j2="grain", s2=0.5):
    return [
        {"cuts": ["A"], "still": "${WORK}/a.png", "join": {"kind": j1, "seconds": s1}},
        {"cuts": ["B"], "video": "${WORK}/b.mp4", "join": {"kind": j2, "seconds": s2}},
        {"cuts": ["C"], "still": "${WORK}/c.png", "look": {"grain": True, "bloom": 0.3}},
    ]


class JoinLength(unittest.TestCase):
    def test_even_frames(self):
        self.assertEqual(Join("dissolve", 0.25).frames(30), 8)
        self.assertEqual(Join("dissolve", 0.3).frames(30), 10)
        self.assertEqual(Join("grain", 0.5).frames(30), 16)
        self.assertEqual(Join("light", 0.4).frames(30), 12)
        self.assertEqual(Join("cut", 0.0).frames(30), 0)
        self.assertEqual(Join("dissolve", 0.01).frames(30), 2)   # never shorter than one frame a side


class Table(unittest.TestCase):
    def test_substitutes_names(self):
        t = assemble.load_table(table_data(three_segments()), {"WORK": "/w"})
        self.assertEqual(t.segments[0].source, "/w/a.png")
        self.assertEqual(t.segments[1].kind, "video")

    def test_missing_name(self):
        with self.assertRaises(ValueError):
            assemble.load_table(table_data(three_segments()), {})

    def test_default_join_and_last(self):
        segs = three_segments()
        del segs[0]["join"]
        t = assemble.load_table(table_data(segs), {"WORK": "/w"})
        self.assertEqual((t.segments[0].join.kind, t.segments[0].join.seconds), ("dissolve", 0.25))
        self.assertEqual(t.segments[-1].join.kind, "cut")   # nothing follows the last segment

    def test_rejects_bad_joins_and_sources(self):
        segs = three_segments(j1="wipe")
        with self.assertRaises(ValueError):
            assemble.load_table(table_data(segs), {"WORK": "/w"})
        segs = three_segments()
        segs[0]["video"] = "x.mp4"
        with self.assertRaises(ValueError):
            assemble.load_table(table_data(segs), {"WORK": "/w"})

    def test_cuts_must_match_script(self):
        with self.assertRaises(ValueError):
            planned(three_segments(), [("A", 0, 4), ("B", 4, 9.5), ("D", 9.5, 12)])

    def test_slot_spans_all_rows_of_a_cut(self):
        text = "A\t0:00-0:03\tx\tx\nA\t0:00-0:03\ty\ty\nB\t0:03-0:05\tz\tz\n"
        self.assertEqual(assemble.slot_table(text), [("A", 0.0, 3.0), ("B", 3.0, 5.0)])

    def test_join_longer_than_a_slot_is_refused(self):
        with self.assertRaises(ValueError):
            planned(three_segments(s1=5.0), THREE)

    def test_repo_table(self):
        """The sample table covers the 21 cuts in order with the planned joins and looks."""
        data = json.loads(REPO_TABLE.read_text(encoding="utf-8"))
        for seg in data["segments"]:   # inputs come through ${WORK}, never a fixed folder
            src = seg.get("video") or seg.get("still")
            for p in (src.values() if isinstance(src, dict) else [src] if src else []):
                self.assertTrue(p.startswith("${WORK}/"), p)
        table = assemble.load_table(data, {"WORK": "/w"})
        cuts = [c for s in table.segments for c in s.cuts]
        self.assertEqual(len(cuts), 21)
        joins = {s.cuts[-1]: (s.join.kind, s.join.seconds) for s in table.segments}
        self.assertEqual(joins["N04"], ("grain", 0.5))
        self.assertEqual(joins["N05"], ("light", 0.5))
        self.assertEqual(joins["N08"], ("dissolve", 0.4))
        self.assertEqual(joins["N08b"], ("light", 0.4))
        self.assertEqual(joins["N17"], ("grain", 0.5))
        self.assertEqual(joins["N17b"], ("light", 0.6))
        self.assertEqual(joins["N10"], ("dissolve", 0.25))
        looked = sorted(s.cuts[0] for s in table.segments if s.grain and s.bloom > 0)
        self.assertEqual(looked, ["N05", "N08", "N08b", "N17b", "N18"])  # the dark cuts (N08: the plain CAE tool on night)
        # the numbered labels lie over N01-N04, each from the start of its own cut
        for seg in data["segments"]:
            for ov in seg.get("overlay", []):
                self.assertTrue(ov["frames"].startswith("${WORK}/"), ov)
        laid = {ov.cut or s.cuts[0]: Path(ov.frames).name for s in table.segments for ov in s.overlays}
        self.assertEqual(laid, {"N01": "labels_n01", "N02": "labels_n02", "N03": "labels_n03", "N04": "labels_n04"})
        # tiles a script with these cuts, whatever the slot lengths
        slots, t = [], 0.0
        for c in cuts:
            slots.append((c, t, t + 5.0))
            t += 5.0
        assemble.plan(table, assemble.slot_table(script_text(slots)))


class Overlays(unittest.TestCase):
    """Transparent frames laid over a segment's picture, from the start of one of its cuts."""

    def segments(self, overlay):
        return [
            {"cuts": ["A", "B"], "still": "${WORK}/a.png", "overlay": overlay,
             "join": {"kind": "dissolve", "seconds": 0.4}},
            {"cuts": ["C"], "video": "${WORK}/c.mp4"},
        ]

    def test_parse(self):
        t = assemble.load_table(table_data(self.segments(
            [{"frames": "${WORK}/la"}, {"frames": "${WORK}/lb", "cut": "B", "at": 0.2}])), {"WORK": "/w"})
        ovs = t.segments[0].overlays
        self.assertEqual([(o.frames, o.cut, o.at) for o in ovs], [("/w/la", None, 0.0), ("/w/lb", "B", 0.2)])
        self.assertTrue(ovs[0].is_folder())
        self.assertFalse(assemble.Overlay("/w/l.mov").is_folder())
        one = assemble.load_table(table_data(self.segments({"frames": "${WORK}/la"})), {"WORK": "/w"})
        self.assertEqual(len(one.segments[0].overlays), 1)
        self.assertEqual(assemble.load_table(table_data(three_segments()), {"WORK": "/w"}).segments[0].overlays, [])

    def test_rejects_bad_overlays(self):
        for bad in ({"frames": "${WORK}/la", "cut": "C"}, {"cut": "A"}, {"frames": "${WORK}/la", "at": -1}):
            with self.assertRaises(ValueError, msg=bad):
                assemble.load_table(table_data(self.segments(bad)), {"WORK": "/w"})

    def test_start_frame_follows_the_cut_and_the_padding(self):
        table = planned(self.segments([{"frames": "${WORK}/la"}, {"frames": "${WORK}/lb", "cut": "B", "at": 0.2}]),
                        THREE)
        seg, span = table.segments[0], assemble.spans(table)[0]
        self.assertEqual(seg.cut_starts, {"A": 0.0, "B": 4.0})
        self.assertEqual(span.pre, 0)
        self.assertEqual(assemble.overlay_start(seg, span, seg.overlays[0], 30), 0)
        self.assertEqual(assemble.overlay_start(seg, span, seg.overlays[1], 30), 4 * 30 + 6)
        # a segment with a join in starts its file half a join before its slot
        table2 = planned([{"cuts": ["A"], "still": "${WORK}/a.png", "join": {"kind": "dissolve", "seconds": 0.4}},
                          {"cuts": ["B", "C"], "video": "${WORK}/b.mp4", "overlay": {"frames": "${WORK}/lc", "cut": "C"}}],
                         THREE)
        seg2, span2 = table2.segments[1], assemble.spans(table2)[1]
        self.assertEqual(assemble.overlay_start(seg2, span2, seg2.overlays[0], 30), 6 + round(5.5 * 30))

    def test_graph_and_command(self):
        table = planned(self.segments([{"frames": "${WORK}/la"}, {"frames": "${WORK}/lb", "cut": "B"}]), THREE)
        seg, span = table.segments[0], assemble.spans(table)[0]
        g = assemble.segment_graph(seg, span, 30, 1920, 1080, 910, "1b1a17")
        self.assertIn("[1:v]scale=1920:1080:flags=lanczos,format=rgba,setpts=PTS-STARTPTS+0.000000/TB[ov0]", g)
        self.assertIn("[2:v]scale=1920:1080:flags=lanczos,format=rgba,setpts=PTS-STARTPTS+4.000000/TB[ov1]", g)
        self.assertIn("[p0][ov0]overlay=x=0:y=0:eof_action=pass", g)
        self.assertIn("[po0][ov1]overlay=", g)
        cmd = assemble.segment_command(seg, span, 30, 1080, Path("o.mp4"), 910, "1b1a17")
        inputs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
        self.assertEqual(Path(inputs[0]).as_posix(), "/w/a.png")
        self.assertEqual([Path(p).as_posix() for p in inputs[1:]], ["/w/la/%05d.png", "/w/lb/%05d.png"])
        # a card has no picture input: its overlays start at input 0
        card = assemble.Segment(["A"], "card", card={"lines": []}, overlays=[assemble.Overlay("/w/la")])
        card.cut_starts = {"A": 0.0}
        self.assertIn("[0:v]scale=", assemble.segment_graph(card, assemble.Span("A", 0, 30, 0, Join(), Join(), 0),
                                                            30, 1920, 1080, 910, "1b1a17"))


class Timing(unittest.TestCase):
    def setUp(self):
        self.table = planned(three_segments(), THREE)
        self.sp = assemble.spans(self.table)
        self.jp = assemble.join_points(self.sp, 30)

    def test_spans_pad_half_a_join_each_side(self):
        a, b, c = self.sp
        self.assertEqual((a.pre, a.slot, a.post), (0, 120, 6))
        self.assertEqual((b.pre, b.slot, b.post), (6, 165, 8))
        self.assertEqual((c.pre, c.slot, c.post), (8, 75, 0))

    def test_boundary_is_the_middle_of_each_join(self):
        for jp, boundary in zip(self.jp, (120, 285)):
            self.assertEqual(jp["boundary"], boundary)
            self.assertEqual(jp["start"] + jp["end"], 2 * boundary)
        self.assertEqual((self.jp[0]["start"], self.jp[0]["end"]), (114, 126))
        self.assertEqual((self.jp[1]["start"], self.jp[1]["end"]), (277, 293))

    def test_offset_is_where_the_join_starts(self):
        for jp in self.jp:
            self.assertEqual(jp["offset"], jp["start"])

    def test_total_length_unchanged(self):
        self.assertEqual(assemble.film_frames(self.sp, 30), 360)

    def test_cuts_add_no_padding(self):
        t = planned(three_segments(j1="cut", j2="cut"), THREE)
        sp = assemble.spans(t)
        self.assertEqual([(s.pre, s.post) for s in sp], [(0, 0), (0, 0), (0, 0)])
        self.assertEqual(assemble.film_frames(sp, 30), 360)

    def test_slot_off_the_frame_grid(self):
        with self.assertRaises(ValueError):
            assemble.frames(1.01, 30)


class Commands(unittest.TestCase):
    def setUp(self):
        self.table = planned(three_segments(j1="light"), THREE)
        self.sp = assemble.spans(self.table)

    def graph(self, i, height=1080):
        seg, span = self.table.segments[i], self.sp[i]
        return assemble.segment_graph(seg, span, 30, height * 16 // 9, height, 910, "1b1a17")

    def test_light_join_on_both_sides(self):
        self.assertIn("0xf6ead0", self.graph(0))
        self.assertIn("0xf6ead0", self.graph(1))
        self.assertNotIn("0xf6ead0", self.graph(2))

    def test_grain_join_and_look(self):
        g1, g2 = self.graph(1), self.graph(2)
        self.assertIn("noise=", g1)          # grain join out of B
        self.assertIn("noise=", g2)          # grain join into C, and C's grain look
        self.assertIn(f"all_seed={self.sp[2].first_frame + 1},", g2)   # seeded by the cut's first frame
        self.assertIn("all_mode=screen", g2)  # bloom
        self.assertNotIn("all_mode=screen", g1)

    def test_still_pads_and_band(self):
        self.table.segments[0].paint_band = True
        g = self.graph(0)
        self.assertIn("drawbox=x=0:y=910:w=iw:h=170", g)
        self.assertIn("tpad=stop=126:stop_mode=clone,trim=end_frame=126", g)
        self.assertIn("drawbox=x=0:y=1820:w=iw:h=340", self.graph(0, 2160))

    def test_video_freezes_on_the_first_frame_before_its_slot(self):
        self.assertIn("tpad=start=6:start_mode=clone", self.graph(1))

    def test_join_graph(self):
        g = assemble.join_graph(self.sp, 30)
        self.assertIn("xfade=transition=fade:duration=0.400000:offset=3.800000", g)
        # grain is a smooth fade under the swelling grain layer (no patchy break-up)
        self.assertIn("xfade=transition=fade:duration=0.533333:offset=9.233333", g)
        self.assertNotIn("transition=custom", g)
        self.assertNotIn("st(", g)   # xfade evaluates on several threads: no shared registers
        cut = planned(three_segments(j1="cut"), THREE)
        self.assertIn("concat=n=2", assemble.join_graph(assemble.spans(cut), 30))

    def test_segment_command(self):
        cmd = assemble.segment_command(self.table.segments[0], self.sp[0], 30, 1080, Path("o.mp4"),
                                       910, "1b1a17")
        self.assertEqual(cmd[cmd.index("-loop") + 1], "1")
        self.assertEqual(cmd[cmd.index("-frames:v") + 1], str(self.sp[0].total))

    def test_per_height_source(self):
        seg = assemble.Segment(["A"], "video", {"1080": "a1.mp4", "2160": "a4.mp4"})
        self.assertEqual(assemble.resolve_source(seg, 2160), Path("a4.mp4"))
        seg.source = {"1080": "a1.mp4"}
        self.assertEqual(assemble.resolve_source(seg, 2160), Path("a1.mp4"))

    def test_escape_path(self):
        self.assertEqual(assemble.escape_path("C:\\Windows\\Fonts\\a.ttc"), "'C\\:/Windows/Fonts/a.ttc'")


class Encoders(unittest.TestCase):
    """--encoder: libx264 as before, or h264_nvenc at the matching -cq."""

    def setUp(self):
        self.table = planned(three_segments(j1="light"), THREE)
        self.sp = assemble.spans(self.table)

    def test_x264_arguments_unchanged(self):
        cmd = assemble.segment_command(self.table.segments[1], self.sp[1], 30, 1080, Path("o.mp4"), 910, "1b1a17")
        i = cmd.index("-c:v")
        self.assertEqual(cmd[i:i + 8], ["-c:v", "libx264", "-preset", "medium", "-crf", str(assemble.SEGMENT_CRF),
                                        "-pix_fmt", "yuv420p"])
        j = assemble.join_command([Path("a.mp4")] * 3, self.sp, 30, Path("f.mp4"))
        self.assertEqual(j[j.index("-c:v") + 1], "libx264")

    def test_nvenc_arguments(self):
        for cmd in (assemble.segment_command(self.table.segments[1], self.sp[1], 30, 1080, Path("o.mp4"), 910,
                                             "1b1a17", encoder="nvenc"),
                    assemble.join_command([Path("a.mp4")] * 3, self.sp, 30, Path("f.mp4"), encoder="nvenc")):
            self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_nvenc")
            self.assertEqual(cmd[cmd.index("-cq") + 1], f"{assemble.SEGMENT_CRF + NVENC_CQ_OFFSET:g}")
            self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "yuv420p")
            self.assertNotIn("libx264", cmd)

    def test_burn_moves_to_nvenc(self):
        burn = burn_command(Path("film.mp4"), Path("subs.ass"), Path("out.mp4"), Path("mix.wav"))
        self.assertIs(assemble.swap_encoder(burn, "x264"), burn)
        cmd = assemble.swap_encoder(burn, "nvenc")
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "h264_nvenc")
        self.assertEqual(cmd[cmd.index("-cq") + 1], f"{16 + NVENC_CQ_OFFSET:g}")   # burn's crf 16
        self.assertNotIn("libx264", cmd)
        self.assertNotIn("-crf", cmd)
        # everything around the codec stays: filters, colour tags, audio, output
        k = burn.index("-c:v")
        self.assertEqual(cmd[:k], burn[:k])
        self.assertEqual(cmd[-14:], burn[-14:])
        self.assertIn("-c:a", cmd)


class FastGraph(unittest.TestCase):
    """The join expressions run only on the join's frames; the grain look uses the built-in overlay."""

    def setUp(self):
        self.table = planned(three_segments(j1="light"), THREE)
        self.sp = assemble.spans(self.table)

    def test_ramp_window_covers_the_ramp(self):
        fps = 30
        for i, kind in ((0, "light"), (1, "light"), (1, "grain"), (2, "grain")):
            span = self.sp[i]
            expr, window = assemble.ramps(span, fps, kind), assemble.ramp_window(span, fps, kind)
            for n in range(span.total):
                t = n / fps
                strength = eval_ramp(expr, t)
                inside = eval_window(window, t)
                if strength > 0:
                    self.assertTrue(inside, (i, kind, n, strength))
        self.assertIsNone(assemble.ramp_window(self.sp[0], fps, "grain"))

    def test_graph(self):
        fast = assemble.segment_graph(self.table.segments[1], self.sp[1], 30, 1920, 1080, 910, "1b1a17")
        self.assertIn("enable='", fast)
        exact = assemble.segment_graph(self.table.segments[1], self.sp[1], 30, 1920, 1080, 910, "1b1a17",
                                       fast=False)
        self.assertNotIn("enable=", exact)
        c = assemble.segment_graph(self.table.segments[2], self.sp[2], 30, 1920, 1080, 910, "1b1a17")
        self.assertIn("c0_mode=overlay:c0_opacity=0.300", c)
        self.assertIn("c0_expr='A+(if(lt(A,128)", assemble.segment_graph(
            self.table.segments[2], self.sp[2], 30, 1920, 1080, 910, "1b1a17", fast=False))


def eval_ramp(expr, t):
    """Evaluate a ramps() expression (smoothstep of clip, max, 1-) at T=t in Python."""
    py = expr.replace("T", repr(t))
    py = re.sub(r"clip\(", "_clip(", py)
    return eval(py, {"_clip": lambda x, a, b: min(max(x, a), b), "max": max})


def eval_window(expr, t):
    py = expr.replace("lt(", "_LT(").replace("gt(", "_GT(").replace("t", repr(t))
    return bool(eval(py, {"_LT": lambda a, b: a < b, "_GT": lambda a, b: a > b}))


def ffmpeg_or_none():
    try:
        from promo.export import ffmpeg_path
        return ffmpeg_path()
    except SystemExit:
        return None


FFMPEG = ffmpeg_or_none()


@unittest.skipUnless(FFMPEG and shutil.which("ffprobe"), "needs ffmpeg and ffprobe")
class Build(unittest.TestCase):
    """A 3 s film from tiny inputs at 144 px: length, frame count, same pixels twice."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="assemble_test_"))
        d = cls.dir
        lavfi = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi"]
        subprocess.run(lavfi + ["-i", "color=c=0xe8e2d0:s=320x180", "-frames:v", "1", str(d / "a.png")], check=True)
        subprocess.run(lavfi + ["-i", "testsrc2=s=320x180:r=30:d=0.8", "-pix_fmt", "yuv420p",
                                str(d / "b.mp4")], check=True)   # shorter than its slot
        subprocess.run(lavfi + ["-i", "color=c=0x151513:s=320x180", "-frames:v", "1", str(d / "c.png")], check=True)
        (d / "s.tsv").write_text(script_text([("A", 0, 1), ("B", 1, 2), ("C", 2, 3)]), encoding="utf-8")
        data = table_data([
            {"cuts": ["A"], "still": "${WORK}/a.png", "paint_band": True, "join": {"kind": "light", "seconds": 0.2}},
            {"cuts": ["B"], "video": "${WORK}/b.mp4", "join": {"kind": "grain", "seconds": 0.2}},
            {"cuts": ["C"], "still": "${WORK}/c.png", "look": {"grain": True, "bloom": 0.3}},
        ])
        (d / "t.json").write_text(json.dumps(data), encoding="utf-8")
        cls.outs = []
        for n in (1, 2):
            table = assemble.read_table(d / "t.json", str(d), [])
            cls.outs.append(assemble.build(table, d / f"out{n}.mp4", 144, None, None, ffmpeg=FFMPEG,
                                           log=lambda *a: None))
        cls.exact = assemble.build(assemble.read_table(d / "t.json", str(d), []), d / "exact.mp4", 144, None,
                                   None, ffmpeg=FFMPEG, log=lambda *a: None, fast=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def probe(self, path, entries):
        r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                            "-show_entries", entries, "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, check=True)
        return r.stdout.strip()

    def test_length(self):
        self.assertEqual(self.probe(self.outs[0], "stream=nb_read_frames"), "90")
        self.assertEqual(self.probe(self.outs[0], "stream=width,height"), "256,144")

    def test_same_input_same_pixels(self):
        def md5s(p):
            r = subprocess.run([FFMPEG, "-v", "error", "-i", str(p), "-f", "framemd5", "-"],
                               capture_output=True, text=True, check=True)
            return [ln.split(",")[-1].strip() for ln in r.stdout.splitlines() if not ln.startswith("#")]
        a, b = md5s(self.outs[0]), md5s(self.outs[1])
        self.assertEqual(len(a), 90)
        self.assertEqual(a, b)
        self.assertGreater(len(set(a[66:])), 1)   # the grain moves from frame to frame

    def test_fast_graph_looks_the_same(self):
        """Each segment through the fast graph and the earlier one (every expression on every frame),
        written losslessly (FFV1) so only the filters differ: SSIM frame by frame."""
        table = assemble.read_table(self.dir / "t.json", str(self.dir), [])
        for seg, span in zip(table.segments, assemble.spans(table)):
            files = []
            for fast in (True, False):
                f = self.dir / f"seg_{span.name}_{fast}.mkv"
                cmd = assemble.segment_command(seg, span, 30, 144, f, 910, "1b1a17", ffmpeg=FFMPEG, fast=fast)
                cmd = cmd[:cmd.index("-c:v")] + ["-c:v", "ffv1", "-pix_fmt", "yuv420p", str(f)]
                subprocess.run(cmd, check=True, cwd=self.dir)
                files.append(f)
            self.assertGreaterEqual(min(ssim_per_frame(*files)), 0.99, span.name)


def ssim_per_frame(a, b):
    r = subprocess.run([FFMPEG, "-hide_banner", "-i", str(a), "-i", str(b), "-lavfi", "ssim=stats_file=-",
                        "-f", "null", "-"], capture_output=True, text=True, check=True)
    vals = [float(m) for m in re.findall(r"All:([\d.]+)", r.stdout)]
    if not vals:
        raise AssertionError(r.stderr[-500:])
    return vals


@unittest.skipUnless(FFMPEG and shutil.which("ffprobe") and nvenc_available(FFMPEG or "ffmpeg"),
                     "needs ffmpeg, ffprobe and a usable h264_nvenc")
class NvencBuild(unittest.TestCase):
    """The same 3 s film with h264_nvenc: H.264, yuv420p, 90 frames, 3 s, and close to the libx264 one."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="assemble_nvenc_"))
        d = cls.dir
        lavfi = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi"]
        subprocess.run(lavfi + ["-i", "color=c=0xe8e2d0:s=320x180", "-frames:v", "1", str(d / "a.png")], check=True)
        subprocess.run(lavfi + ["-i", "testsrc2=s=320x180:r=30:d=0.8", "-pix_fmt", "yuv420p",
                                str(d / "b.mp4")], check=True)
        subprocess.run(lavfi + ["-i", "color=c=0x151513:s=320x180", "-frames:v", "1", str(d / "c.png")], check=True)
        (d / "s.tsv").write_text(script_text([("A", 0, 1), ("B", 1, 2), ("C", 2, 3)]), encoding="utf-8")
        data = table_data([
            {"cuts": ["A"], "still": "${WORK}/a.png", "join": {"kind": "light", "seconds": 0.2}},
            {"cuts": ["B"], "video": "${WORK}/b.mp4", "join": {"kind": "grain", "seconds": 0.2}},
            {"cuts": ["C"], "still": "${WORK}/c.png", "look": {"grain": True, "bloom": 0.3}},
        ])
        (d / "t.json").write_text(json.dumps(data), encoding="utf-8")
        cls.out = {}
        for enc in ("nvenc", "x264"):
            table = assemble.read_table(d / "t.json", str(d), [])
            cls.out[enc] = assemble.build(table, d / f"{enc}.mp4", 144, None, None, ffmpeg=FFMPEG,
                                          log=lambda *a: None, encoder=enc)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_shape(self):
        r = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name,pix_fmt,width,height,nb_read_frames:format=duration",
                            "-of", "json", str(self.out["nvenc"])], capture_output=True, text=True, check=True)
        info = json.loads(r.stdout)
        s = info["streams"][0]
        self.assertEqual((s["codec_name"], s["pix_fmt"], s["width"], s["height"]), ("h264", "yuv420p", 256, 144))
        self.assertEqual(s["nb_read_frames"], "90")
        self.assertAlmostEqual(float(info["format"]["duration"]), 3.0, places=2)

    def test_close_to_x264(self):
        # frames 0-20: cut A before its light join; the grain frames are random noise, which two
        # lossy encoders keep differently, so they are not compared here
        ssim = ssim_per_frame(self.out["nvenc"], self.out["x264"])
        self.assertEqual(len(ssim), 90)
        self.assertGreaterEqual(min(ssim[:21]), 0.99)


@unittest.skipUnless(FFMPEG and shutil.which("ffprobe"), "needs ffmpeg and ffprobe")
class OverlayBuild(unittest.TestCase):
    """A white box (transparent around it) laid over a dark still from 0.2 s into cut B."""

    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix="assemble_overlay_"))
        d = cls.dir
        lavfi = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi"]
        subprocess.run(lavfi + ["-i", "color=c=0x303030:s=320x180", "-frames:v", "1", str(d / "a.png")], check=True)
        (d / "ov").mkdir()
        # (drawbox would leave the alpha at 0: geq writes it)
        subprocess.run(lavfi + ["-i", "color=c=white:s=320x180:r=30,format=rgba,"
                                      "geq=r=255:g=255:b=255:a='255*between(X,120,199)*between(Y,60,119)'",
                                "-frames:v", "12", "-start_number", "0", str(d / "ov" / "%05d.png")], check=True)
        (d / "s.tsv").write_text(script_text([("A", 0, 1), ("B", 1, 2)]), encoding="utf-8")
        data = table_data([{"cuts": ["A", "B"], "still": "${WORK}/a.png",
                            "overlay": [{"frames": "${WORK}/ov", "cut": "B", "at": 0.2}]}])
        (d / "t.json").write_text(json.dumps(data), encoding="utf-8")
        table = assemble.read_table(d / "t.json", str(d), [])
        cls.out = assemble.build(table, d / "out.mp4", 144, None, None, ffmpeg=FFMPEG, log=lambda *a: None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def centre(self, n):
        """Grey level (0-255) of the middle of frame n."""
        r = subprocess.run([FFMPEG, "-v", "error", "-i", str(self.out),
                            "-vf", f"select=eq(n\\,{n}),crop=8:8:(iw-8)/2:(ih-8)/2,scale=1:1,format=gray",
                            "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True, check=True)
        return r.stdout[0]

    def test_box_shows_only_while_its_frames_play(self):
        # the box's 12 frames go at frames 36-47 (B starts at 30, +0.2 s = 6 frames)
        for n in (0, 30, 35, 48, 59):
            self.assertLess(self.centre(n), 90, n)
        for n in (36, 41, 47):
            self.assertGreater(self.centre(n), 200, n)


if __name__ == "__main__":
    unittest.main()

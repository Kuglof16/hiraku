import copy
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path

from promo import bgm, layout_build, layout_check as lc

HAS_PLAYWRIGHT = importlib.util.find_spec("playwright") is not None


def box(x, y, w, h):
    return {"x": x, "y": y, "w": w, "h": h}


def cut_rules(rules: dict, cut: str) -> dict:
    """The rules a checked page reads: its cut's, or for an entry page of the
    labels (labels_n01 ...) the labels' with the length of the cut it lies over."""
    if cut.startswith("labels_"):
        return {**rules["labels"], "duration_s": rules[cut.split("_", 1)[1]]["duration_s"]}
    return rules[cut]


def box_prefix(cut: str) -> str:
    return "labels." if cut.startswith("labels_") else cut + "."


def hop_times(j: dict) -> tuple[float, float]:
    """(landing, end) of one pet3d anim.hop with the values j, in s from its
    start: crouch_s + air_s, then the landing (land_s), each small bounce
    [up_px, dur_s] with its landing (land_s x (0.5 + 0.5 sqrt(up / over))),
    and the settle (settle_s)."""
    land = j.get("crouch_s", 0) + j.get("air_s", 0)
    end = land + j.get("land_s", 0)
    over = max(j.get("over_px", 0), 1e-6)
    for up, dur in j.get("bounces", []):
        k = min(1.0, up / over) ** 0.5
        end += dur + j.get("land_s", 0) * (0.5 + 0.5 * k)
    return land, end + j.get("settle_s", 0)


ROUTE_KINDS = ("walk", "hop", "hops", "turn", "sit", "act", "fade", "tick")   # pet3d.js anim.BEAT_KINDS


def route_end(beats: list, hop: dict) -> float:
    """When every beat of a pet3d anim.route is over (s): a hop's end from
    hop_times (with the cut's hop defaults under the beat's values), n hops
    every_s apart, the rest their dur_s."""
    end = 0.0
    for b in beats:
        if b["do"] in ("hop", "hops"):
            d = hop_times({**hop, **b})[1] + ((b["n"] - 1) * b["every_s"] if b["do"] == "hops" else 0)
        else:
            d = b.get("dur_s", 0)
        end = max(end, b["t"] + d)
    return end


def blinks_clear(stop: dict, shift: float, count: int) -> list:
    """Pets (index, clock at a blink) whose blink (pet3d: clock % 4.3 < 0.16)
    falls while the clocks t + shift x index slow to a halt (stop.from_s to
    stop.from_s + (still_s - from_s) / 2 on the clock): empty when none does."""
    lo, hi = stop["from_s"], stop["from_s"] + (stop["still_s"] - stop["from_s"]) / 2
    bad = []
    for i in range(count):
        for k in range(0, 40):
            b0 = 4.3 * k - shift * i
            if b0 < hi and b0 + 0.16 > lo:
                bad.append((i, b0))
    return bad


def hero_mask(png: Path, box_w: float, box_h: float, cell: float, threshold: int = 128,
              cover: float = 0.5) -> list[str]:
    """Where a picture is, cell by cell, as layout.json n05.hero_points.mask.rows_hex.

    The picture is fitted into a box_w x box_h box (contain, centred) as the page
    draws it; a cell of cell x cell box px is set when at least cover of the
    picture's pixels in it have alpha above threshold. One hex string per row,
    the first column in the high bit of the first digit, padded to whole digits.
    """
    from PIL import Image

    alpha = Image.open(png).getchannel("A")
    iw, ih = alpha.size
    k = min(box_w / iw, box_h / ih)
    ox, oy = (box_w - iw * k) / 2, (box_h - ih * k) / 2
    cols, rows = round(box_w / cell), round(box_h / cell)
    px = alpha.load()
    out = []
    for r in range(rows):
        bits = ""
        for c in range(cols):
            xs = range(max(0, int((c * cell - ox) / k)), min(iw, int(((c + 1) * cell - ox) / k)))
            ys = range(max(0, int((r * cell - oy) / k)), min(ih, int(((r + 1) * cell - oy) / k)))
            n = sum(1 for y in ys for x in xs if px[x, y] > threshold)
            bits += "1" if n / max(1, len(xs) * len(ys)) >= cover else "0"
        bits += "0" * (-len(bits) % 4)
        out.append("".join(f"{int(bits[i:i + 4], 2):x}" for i in range(0, len(bits), 4)))
    return out


class GeneratedData(unittest.TestCase):
    def test_data_js_matches_layout_json(self):
        rules = layout_build.load()
        text = layout_build.TARGET.read_text(encoding="utf-8")
        self.assertEqual(layout_build.parse(text), rules,
                         "layout.data.js differs from layout.json: run py promo/layout_build.py")
        self.assertEqual(text, layout_build.render(rules))

    def test_data_js_says_generated(self):
        head = layout_build.TARGET.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn("Generated file: do not edit", head)

    def test_render_parse_round_trip(self):
        rules = {"stage": {"w": 1920, "h": 1080}, "note": "日本語"}
        self.assertEqual(layout_build.parse(layout_build.render(rules)), rules)

    def test_check_times_name_every_cut(self):
        rules = layout_build.load()
        for cut in lc.CUTS:
            self.assertTrue(cut_rules(rules, cut))
            self.assertTrue(rules["check"]["times"][cut])

    def test_every_allowance_gives_a_reason(self):
        chk = layout_build.load()["check"]
        for rule in chk["band_exempt"] + chk["allow_overlap"] + chk["allow_overflow"] + chk["allow_escape"]:
            self.assertTrue(rule.get("why"), rule)


class SharedShape(unittest.TestCase):
    """The names the screen department reads from layout.json."""

    def setUp(self):
        self.rules = layout_build.load()

    def test_top_level(self):
        keys = {k for k in self.rules if not k.startswith("_")}
        self.assertEqual(keys, {"stage", "subtitle_band", "safe", "min_text_px", "colors",
                                "n00", "n01", "n02", "n03", "n04", "n05", "n06", "n07", "n08", "n08b", "n09", "n10", "n11", "n12", "n13", "n14", "n15",
                                "n16", "n17", "n17b", "n18", "labels", "story", "check"})
        r = self.rules
        self.assertEqual(r["stage"], {"w": 1920, "h": 1080})
        self.assertEqual(r["subtitle_band"]["y"] + r["subtitle_band"]["h"], r["stage"]["h"])
        self.assertEqual(r["safe"]["x"] * 2 + r["safe"]["w"], r["stage"]["w"])
        self.assertEqual(r["safe"]["y"] + r["safe"]["h"], r["subtitle_band"]["y"])
        self.assertEqual(r["min_text_px"], 18)
        for k in ("night", "ink", "tile_bg", "tile_border"):
            self.assertRegex(r["colors"][k], r"^#[0-9a-f]{6}$")

    def test_n01_boxes_stay_above_the_band(self):
        # N01 is drawn by the screens department from these values
        n, r = self.rules["n01"], self.rules
        for name in ("view", "pane"):
            b = n[name]
            self.assertLessEqual(b["y"] + b["h"], r["subtitle_band"]["y"], name)
            self.assertLessEqual(b["x"] + b["w"], r["stage"]["w"], name)
            self.assertGreaterEqual(b["x"], r["safe"]["x"], name)
        self.assertLessEqual(n["view"]["x"] + n["view"]["w"], n["pane"]["x"])  # side by side
        self.assertGreaterEqual(min(n["card"]["title_px"], n["card"]["text_px"]), r["min_text_px"])

    def test_every_cut_has_one_message(self):
        # the maker: the screens were too busy; each cut gets one message and limits on what it shows
        st = self.rules["story"]
        cuts = [k for k in st["cuts"]]
        self.assertEqual(len(cuts), 21)
        for k, c in st["cuts"].items():
            self.assertTrue(c["message"].strip(), k)
            if "limits" in c:
                self.assertTrue(c["limits"].get("why"), k)  # every exception says why
                for name in c["limits"]:
                    self.assertTrue(name == "why" or name in st["defaults"], (k, name))
        d = st["defaults"]
        self.assertGreaterEqual(d["body_text_px"], self.rules["min_text_px"])

    def test_n03_n06_n07_n17_for_the_screens_department(self):
        # drawn by the screens department from these values (narration draft 6)
        r = self.rules
        band, safe = r["subtitle_band"]["y"], r["safe"]
        right_edge = safe["x"] + safe["w"]
        boxes = [r["n03"]["view"], r["n03"]["popup"], r["n07"]["view"], r["n07"]["legend"], r["n17"]["page"]]
        for b in boxes:
            self.assertLessEqual(b["y"] + b["h"], band)
            self.assertLessEqual(b["x"] + b["w"], right_edge)
            self.assertGreaterEqual(b["x"], safe["x"])
        self.assertLessEqual(r["n03"]["view"]["x"] + r["n03"]["view"]["w"], r["n03"]["popup"]["x"])
        self.assertLessEqual(r["n07"]["view"]["x"] + r["n07"]["view"]["w"], r["n07"]["legend"]["x"])
        g = r["n06"]["grid"]
        self.assertEqual(g["x"] + g["cols"] * g["tile_w"] + (g["cols"] - 1) * g["gap_x"], right_edge)
        self.assertLessEqual(g["y"] + g["rows"] * g["tile_h"] + (g["rows"] - 1) * g["gap_y"], band)
        self.assertEqual(len(r["n06"]["tests"]), g["cols"] * g["rows"])
        t = r["n06"]["times"]
        self.assertLess(t["tiles_s"] + 5 * t["tile_stagger_s"] + t["tile_in_s"], 5.93)  # within sentences 1-2
        t7 = r["n07"]["times"]
        self.assertLess(t7["zoom_to_s"], t7["swap_s"])
        self.assertLess(t7["swap_to_s"], r["n07"]["duration_s"])
        self.assertEqual((r["n00"]["duration_s"], r["n03"]["duration_s"], r["n06"]["duration_s"],
                          r["n07"]["duration_s"], r["n17"]["duration_s"]), (8.6, 5.0, 11.7, 15.3, 8.8))
        sizes = [r["n03"]["popup"]["title_px"], r["n03"]["popup"]["text_px"], r["n07"]["legend"]["text_px"],
                 r["n07"]["label"]["size_px"], r["n17"]["page"]["text_px"], r["n06"]["tile"]["label"]["size_px"]]
        self.assertGreaterEqual(min(sizes), r["min_text_px"])

    def test_n04_n15_n16_for_the_screens_department(self):
        # drawn by the screens department from these values; N04 and N15 share one grid
        r = self.rules
        band, safe = r["subtitle_band"]["y"], r["safe"]
        for cut in ("n04", "n15"):
            n = r[cut]
            g = n["grid"]
            right = g["x"] + g["cols"] * g["tile_w"] + (g["cols"] - 1) * g["gap_x"]
            bottom = g["y"] + g["rows"] * g["tile_h"] + (g["rows"] - 1) * g["gap_y"]
            self.assertLessEqual(bottom, band, cut)
            self.assertEqual(right, safe["x"] + safe["w"], cut)
            self.assertLessEqual(n["header"]["y"] + n["header"]["h"], g["y"], cut)
            self.assertEqual(len(n["tests"]), g["cols"] * g["rows"], cut)
            self.assertGreaterEqual(min(n["tile"]["label"]["size_px"], n["header"]["size_px"]), r["min_text_px"])
        for k in ("header", "grid", "tests", "tile"):
            self.assertEqual(r["n04"][k], r["n15"][k], k)
        self.assertEqual((r["n04"]["duration_s"], r["n15"]["duration_s"], r["n16"]["duration_s"]), (4.0, 5.1, 6.3))
        n = r["n16"]
        v, s, st = n["view"], n["seek"], n["steps"]
        self.assertLessEqual(v["y"] + v["h"], band)
        self.assertLessEqual(st["y"] + st["h"], band)
        self.assertLessEqual(v["x"] + v["w"], st["x"])  # side by side
        self.assertGreaterEqual(s["x"], v["x"])
        self.assertLessEqual(s["x"] + s["w"], v["x"] + v["w"])
        self.assertLessEqual(s["y"] + s["h"], v["y"] + v["h"])  # over the view's lower edge
        self.assertGreaterEqual(min(st["title_px"], st["text_px"]), r["min_text_px"])
        t = n["times"]
        self.assertLess(t["steps_to_s"], t["scrub_s"])
        self.assertLess(t["scrub_to_s"], n["duration_s"])

    def test_n02_boxes_stay_above_the_band(self):
        # N02 is drawn by the screens department from these values
        n, r = self.rules["n02"], self.rules
        g = n["grid"]
        right = g["x"] + g["cols"] * g["tile_w"] + (g["cols"] - 1) * g["gap_x"]
        bottom = g["y"] + g["rows"] * g["tile_h"] + (g["rows"] - 1) * g["gap_y"]
        self.assertLessEqual(bottom, r["subtitle_band"]["y"])
        self.assertEqual(right, r["safe"]["x"] + r["safe"]["w"])  # full width, no result column
        self.assertNotIn("result", n)
        self.assertEqual(len(n["panes"]), g["cols"] * g["rows"])
        self.assertGreaterEqual(n["pane"]["rate"]["size_px"], r["min_text_px"])
        self.assertGreater(n["pane"]["verdict"]["of_label"], 1)

    def test_feature_and_list_boxes_fill_the_grid(self):
        # N04 / N15 / N06: one large box and a small list, side by side in the grid's box
        r = self.rules
        for cut, count in (("n04", 7), ("n15", 7), ("n06", 6)):
            n = r[cut]
            g, f, ls = n["grid"], n["feature"], n["list"]
            right = g["x"] + g["cols"] * g["tile_w"] + (g["cols"] - 1) * g["gap_x"]
            bottom = g["y"] + g["rows"] * g["tile_h"] + (g["rows"] - 1) * g["gap_y"]
            self.assertEqual((f["x"], f["y"], ls["y"]), (g["x"], g["y"], g["y"]), cut)
            self.assertEqual(f["x"] + f["w"] + g["gap_x"], ls["x"], cut)
            self.assertEqual((ls["x"] + ls["w"], f["y"] + f["h"], ls["y"] + ls["h"]), (right, bottom, bottom), cut)
            self.assertGreaterEqual(ls["cols"] * ls["rows"], count, cut)
            slot_w = (ls["w"] - (ls["cols"] - 1) * ls["gap_x"]) / ls["cols"]
            slot_h = (ls["h"] - (ls["rows"] - 1) * ls["gap_y"]) / ls["rows"]
            self.assertLessEqual(slot_w / slot_h, 2.6, cut)  # not a thin strip (was 560 x 111 in N06)
        self.assertEqual((r["n04"]["feature"], r["n04"]["list"]), (r["n15"]["feature"], r["n15"]["list"]))

    def test_style_keys_for_the_screens_department(self):
        r = self.rules
        self.assertEqual(r["n07"]["label"]["of_view_h"], 0.08)
        self.assertEqual(r["n03"]["popup"]["reading_of_title"], 2)
        self.assertEqual(r["n17"]["page"]["change_of_title"], 1.5)
        self.assertTrue(0 < r["n17"]["dim_opacity"] < 1)
        for cut, col in (("n01", "pane"), ("n16", "steps")):
            v, c = r[cut]["view"], r[cut][col]
            self.assertEqual((v["w"], c["x"], c["x"] + c["w"]), (1400, v["x"] + v["w"] + 24, r["safe"]["x"] + r["safe"]["w"]), cut)
        s, v = r["n16"]["seek"], r["n16"]["view"]
        self.assertEqual((s["x"] - v["x"], v["x"] + v["w"] - (s["x"] + s["w"])), (24, 24))

    def test_n05(self):
        n = self.rules["n05"]
        self.assertEqual(set(n["grid"]) - {"_note"},
                         {"x", "y", "cols", "rows", "tile_w", "tile_h", "gap_x", "gap_y"})
        self.assertEqual(set(n["tile"]), {"border_px", "radius_px", "label", "rate"})
        self.assertEqual(set(n["tile"]["label"]), {"x", "y", "size_px"})
        self.assertEqual(n["hero"]["x"], (self.rules["stage"]["w"] - n["hero"]["w"]) / 2)
        self.assertEqual(set(n["title"]), {"name", "tag", "gloss"})
        # the gloss sits between the tag line and the grid, and comes with or after the tag
        tag, gl = n["title"]["tag"], n["title"]["gloss"]
        self.assertGreaterEqual(gl["y"], tag["y"] + 1.2 * tag["size_px"])
        self.assertLessEqual(gl["y"] + 1.2 * gl["size_px"], n["grid"]["y"])
        self.assertGreaterEqual(gl["size_px"], self.rules["story"]["defaults"]["body_text_px"])
        self.assertEqual(gl["text"], "CAE = コンピュータで設計を試す技術")
        self.assertGreaterEqual(n["times"]["gloss_s"], n["times"]["tag_s"])
        self.assertEqual(len(n["tests"]), n["grid"]["cols"] * n["grid"]["rows"])
        self.assertEqual(set(n["fly_from"]), {"x", "y"})
        for k in ("burst_s", "tiles_s", "tile_in_s", "tile_stagger_s", "name_s", "tag_s"):
            self.assertIn(k, n["times"])

    def test_n05_door(self):
        n = self.rules["n05"]
        d = n["door"]
        # a double door behind the vehicle: it cracks, holds, then swings wide
        self.assertEqual({k for k in d if not k.startswith("_")}, {
            "crack_dur_s", "crack_deg", "crack_ease", "hold_dur_s", "open_dur_s", "angle_deg",
            "perspective_px", "ease", "seam_w_px", "seam_glow_w_px", "spill_dur_s",
            "hero_fade_dur_s", "hero_fade_scale", "panel", "handle",
            "crack_overshoot_deg", "settle_dur_s", "latch_dur_s", "latch_deg", "thickness_px",
            "dolly_px", "dolly_ease", "bolt"})
        self.assertEqual(len(d["ease"]), 4)
        self.assertEqual(len(d["crack_ease"]), 4)
        self.assertTrue(0 < d["crack_deg"] < d["angle_deg"] <= 180)
        self.assertGreater(d["hold_dur_s"], 0)
        # the door stays shut a while first (later than the first two versions' 0.3 s)
        t = n["times"]
        self.assertGreaterEqual(t["door_open_s"], 0.5)
        # fully open (and hidden) when the flash comes, before the tiles fly out
        swing = t["door_open_s"] + d["crack_dur_s"] + d["hold_dur_s"]
        self.assertAlmostEqual(swing + d["open_dur_s"], t["burst_s"], places=6)
        self.assertLess(t["burst_s"], t["tiles_s"])
        # the vehicle is gone (faded) by the flash, and grows on from push_scale
        self.assertLessEqual(swing + d["hero_fade_dur_s"], t["burst_s"])
        self.assertGreater(d["hero_fade_scale"], n["hero_motion"]["push_scale"])
        # the panels fit inside a leaf (half the stage wide)
        p, stage = d["panel"], self.rules["stage"]
        self.assertLess(2 * p["inset_px"], stage["w"] / 2)
        self.assertLess(2 * p["inset_px"] + p["upper_h_px"] + p["gap_px"], stage["h"])
        # the door parts on the stage's middle, where the tiles fly out of
        self.assertEqual(n["hero"]["x"] + n["hero"]["w"] / 2, stage["w"] / 2)
        self.assertEqual(n["fly_from"]["x"], stage["w"] / 2)
        # the door covers the subtitle band, and the vehicle stands over it
        chk = self.rules["check"]
        self.assertIn(".door", [e["selector"] for e in chk["band_exempt"]])
        self.assertTrue([r for r in chk["allow_overlap"]
                         if r["cut"] == "n05" and {r["a"], r["b"]} == {"n05.door", "n05.hero"}])
        # the check looks at the door shut, opening, and open
        at = chk["times"]["n05"]
        self.assertTrue([x for x in at if x < t["door_open_s"]])
        self.assertTrue([x for x in at if t["door_open_s"] < x < t["burst_s"]])
        self.assertTrue([x for x in at if x > t["tag_s"]])

    def test_n05_door_weight_and_light(self):
        n = self.rules["n05"]
        d, t = n["door"], n["times"]
        # the bolt goes and the leaves jolt while the door is still shut
        self.assertLess(t["latch_s"] + d["latch_dur_s"], t["door_open_s"] + 1e-9)
        self.assertLess(d["latch_deg"], d["crack_deg"])
        # the crack runs past and settles back inside the pause
        self.assertLess(d["crack_overshoot_deg"], d["crack_deg"])
        self.assertLessEqual(d["settle_dur_s"], d["hold_dur_s"])
        # the view moves in, but stays in front of the eye
        self.assertLess(d["dolly_px"], d["perspective_px"])
        self.assertEqual(len(d["dolly_ease"]), 4)
        self.assertEqual(set(d["bolt"]), {"w_px", "h_px", "reach_px", "travel_px", "gap_px"})
        self.assertGreater(d["bolt"]["travel_px"], d["bolt"]["reach_px"])   # it clears the seam
        # the flood of light does not burn the screen white
        self.assertLess(n["bloom"]["peak_alpha"], 0.5)
        self.assertLess(n["rays"]["peak_alpha"], 0.5)
        # shafts, motes and the flood are gone by 2.0 s: after that ?layer=fg
        # holds only the name and the tiles' glows
        for k in ("rays", "dust"):
            self.assertLessEqual(t["burst_s"] + n[k]["out_dur_s"], 2.0, k)
        self.assertLessEqual(t["burst_s"] + n["bloom"]["fall_dur_s"], 2.0)
        self.assertGreater(n["rays"]["count"], 0)
        self.assertGreater(n["dust"]["count"], 0)

    def test_n05_mesh_and_tile_fx(self):
        n = self.rules["n05"]
        d, t, m = n["door"], n["times"], n["hero_mesh"]
        # the mesh shows while the door is shut and the vehicle has come apart
        # (its last element gone) by the time the leaves are out
        self.assertLess(t["mesh_in_s"] + m["in_dur_s"], t["mesh_out_s"])
        swing = t["door_open_s"] + d["crack_dur_s"] + d["hold_dur_s"]
        last = t["mesh_out_s"] + m["spread_dur_s"] + m["jitter_dur_s"] + m["el_dur_s"]
        self.assertLessEqual(last, swing + d["hero_fade_dur_s"] + 1e-9)
        self.assertLessEqual(swing + d["hero_fade_dur_s"], t["burst_s"])
        self.assertTrue(0 < m["end_scale"] < 1)
        self.assertTrue(0 <= m["jitter"] < 0.5)          # nodes do not cross
        self.assertGreater(m["size_px"], 10)
        # trails stay within one flight; the wipe and its afterglow end inside the cut
        fx = n["tile_fx"]
        self.assertLess(fx["ghosts"] * fx["ghost_lag_s"], t["tile_in_s"])
        w = n["name_wipe"]
        self.assertLess(t["name_s"] + w["wipe_dur_s"] + w["afterglow_dur_s"], n["duration_s"])

    def test_n05_page_door_and_vehicle(self):
        html = (lc.SCREENS / "n05.html").read_text(encoding="utf-8")
        self.assertIn('class="door"', html)
        self.assertIn('class="leaf leaf-l"', html)
        self.assertIn('class="leaf leaf-r"', html)
        # the vehicle does not split; it is drawn in ?layer=fg too
        for gone in ('class="half', "--hinge-l", "door-slide"):
            self.assertNotIn(gone, html)
        self.assertIn('<div class="hero" data-box="n05.hero">', html)
        self.assertIn('class="grid bg-only"', html)
        # its picture comes from ?hero= or n05.hero.src, else the line drawing
        self.assertIn('get("hero")', html)
        self.assertIn("cutRules.hero.src", html)
        self.assertIn("src", self.rules["n05"]["hero"])
        # the door is behind the vehicle (earlier in the page)
        self.assertLess(html.index('class="door"'), html.index('class="hero"'))
        # v4: thick leaves, a bolt, the view moving in, shafts and motes, the
        # mesh over the picture, the tiles' trails (not .bg-only) and the wipe
        for part in ('class="door-cam"', 'class="edge"', 'class="bolt"', 'svg class="rays"',
                     'svg class="dust"', "meshedPicture", 'operator="in"', 'class="tile-fx"',
                     'class="bloom"', 'class="name-sweep"'):
            self.assertIn(part, html)
        # nothing moves by the wall clock or unseeded randomness
        for banned in ("Math.random", "Date.now", "requestAnimationFrame", "performance.now"):
            self.assertNotIn(banned, html)

    def test_units_in_names(self):
        def walk(obj, path):
            for k, v in obj.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, dict):
                    walk(v, f"{path}.{k}")
                elif path.endswith(".times"):
                    self.assertTrue(k.endswith("_s"), f"{path}.{k}")
        for cut in lc.CUTS:
            walk(cut_rules(self.rules, cut), cut)
            self.assertIn("duration_s", cut_rules(self.rules, cut))

    def test_duration_matches_the_page(self):
        for cut in lc.CUTS:
            html = (lc.SCREENS / f"{cut}.html").read_text(encoding="utf-8")
            m = re.search(r'data-duration="([0-9.]+)"', html)
            self.assertEqual(float(m.group(1)), cut_rules(self.rules, cut)["duration_s"], cut)

    def test_pages_hold_no_times_or_old_names(self):
        for cut in lc.CUTS:
            html = (lc.SCREENS / f"{cut}.html").read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"--t-[a-z-]+\s*:", html), f"{cut}: a time set in the page")
            self.assertNotIn("data-check", html)
            for name in re.findall(r'data-box="([^"$]+)"', html):
                self.assertTrue(name.startswith(box_prefix(cut)), name)

    def test_bgm_reads_the_same_times(self):
        self.assertEqual(bgm.screen_time(bgm.SCREENS, "n05", "burst"), self.rules["n05"]["times"]["burst_s"])
        self.assertEqual(bgm.screen_time(bgm.SCREENS, "n18", "open"), self.rules["n18"]["times"]["open_s"])


BANNED = ("Math.random", "Date.now", "requestAnimationFrame", "performance.now")


class ReferenceLook(unittest.TestCase):
    """N05's point cloud, N18's closing and the labels of N01-N04 (after the reference video)."""

    def setUp(self):
        self.rules = layout_build.load()

    def test_n05_points_before_the_picture(self):
        n = self.rules["n05"]
        t, p = n["times"], n["hero_points"]
        # the door's and the title's times stay as they were
        for k, v in (("latch_s", 0.38), ("door_open_s", 0.55), ("burst_s", 1.25), ("tiles_s", 1.35),
                     ("name_s", 1.75), ("tag_s", 2.3), ("mesh_in_s", 0.2), ("mesh_out_s", 0.78)):
            self.assertEqual(t[k], v, k)
        # every point is in before the picture fades in; it is in by about 0.45 s;
        # the points are gone before the mesh comes apart
        self.assertEqual(t["points_s"], 0.0)
        self.assertLessEqual(t["points_s"] + p["stagger_s"] + p["gather_dur_s"], t["hero_in_s"] + 1e-9)
        self.assertLessEqual(t["hero_in_s"] + p["hero_in_dur_s"], 0.5)
        self.assertGreaterEqual(t["points_out_s"], t["hero_in_s"])
        self.assertLess(t["points_out_s"] + p["fade_dur_s"], t["mesh_out_s"])
        self.assertTrue(0 < p["outline_keep"] <= 1)
        self.assertTrue(0 <= p["jitter"] < 0.5)            # an outline point stays in its cell
        self.assertLess(p["scatter_min_px"], p["scatter_max_px"])
        self.assertEqual(len(p["ease"]), 4)

    def test_n05_mask_shape(self):
        n = self.rules["n05"]
        k = n["hero_points"]["mask"]
        self.assertEqual(k["cols"] * k["cell_px"], n["hero"]["w"])
        self.assertEqual(k["rows"] * k["cell_px"], n["hero"]["h"])
        self.assertEqual(k["src"], n["hero"]["src"])
        self.assertEqual(len(k["rows_hex"]), k["rows"])
        for row in k["rows_hex"]:
            self.assertRegex(row, rf"^[0-9a-f]{{{-(-k['cols'] // 4)}}}$")
        self.assertGreater(sum(bin(int(r, 16)).count("1") for r in k["rows_hex"]), 500)

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "needs Pillow")
    def test_n05_mask_is_the_picture(self):
        n = self.rules["n05"]
        k = n["hero_points"]["mask"]
        got = hero_mask(lc.SCREENS / k["src"], n["hero"]["w"], n["hero"]["h"], k["cell_px"],
                        k["threshold"], k["cover"])
        self.assertEqual(got, k["rows_hex"], "the picture changed: put hero_mask()'s rows in layout.json")

    def test_n05_page_points(self):
        html = (lc.SCREENS / "n05.html").read_text(encoding="utf-8")
        for part in ("heroPoints", "meshNodes(seeded(R.hero_mesh.seed))", 'class="pic"', 'svg class="pts"'):
            self.assertIn(part, html)

    def test_n18_closing(self):
        r, n = self.rules, self.rules["n18"]
        t = n["times"]
        order = ["gather_s", "line_s", "open_s", "draw_s", "name_s", "tag_s", "url_s", "credit_s"]
        self.assertEqual([k for k in t if not k.startswith("_")], order)
        self.assertEqual([t[k] for k in order], sorted(t[k] for k in order))
        self.assertEqual(n["duration_s"], 9.0)
        self.assertEqual(t["open_s"], 0.8)                 # bgm.py starts its swell here
        # the motes are in when the line starts, and the line is grown when it opens
        g = n["gather"]
        self.assertLessEqual(t["gather_s"] + g["stagger_s"] + g["gather_dur_s"], t["line_s"] + 1e-9)
        self.assertGreaterEqual(t["line_s"] - t["gather_s"], 0.3)
        self.assertAlmostEqual(t["line_s"] + n["light"]["grow_dur_s"], t["open_s"])
        # the dark is only the short wait before the door opens: it is gone by 1.5 s,
        # and the seam light with it, before the vehicle is drawn on the paper
        d = n["door"]
        swing = t["open_s"] + d["crack_dur_s"] + d["hold_dur_s"]
        self.assertLessEqual(swing + d["open_dur_s"], 1.5 + 1e-9)
        self.assertLessEqual(swing + n["light"]["out_dur_s"], t["draw_s"])
        self.assertLessEqual(swing + d["open_dur_s"], t["draw_s"])
        self.assertTrue(0 < d["crack_deg"] < d["angle_deg"] <= 120)
        self.assertEqual(len(d["ease"]), 4)
        self.assertEqual(len(d["crack_ease"]), 4)
        # the seam runs inside the safe area, so the motes land above the band
        self.assertGreaterEqual(n["light"]["y"], r["safe"]["y"])
        self.assertLessEqual(n["light"]["y"] + n["light"]["h"], r["subtitle_band"]["y"])
        # with the voice (the maker's recording: sentence 2, HIRAKU は、CAE を、…, from 3.61 s):
        # the name starts just before it, and everything is in before it ends (7.07 s)
        self.assertTrue(3.0 <= t["name_s"] <= 3.61, t["name_s"])
        self.assertLess(t["name_s"], t["tag_s"])
        c, u = n["credit"], n["url"]
        self.assertLessEqual(t["credit_s"] + c["fade_dur_s"], 7.07)
        # top to bottom: vehicle, name, rule, tag, url, credit
        v, name, tag = n["vehicle"], n["title"]["name"], n["title"]["tag"]
        self.assertLessEqual(v["y"] + v["w"] / 2, name["y"])
        self.assertLessEqual(name["y"] + name["size_px"], n["rule"]["y"])
        self.assertLessEqual(n["rule"]["y"] + n["rule"]["h"], tag["y"])
        self.assertLessEqual(tag["y"] + 1.2 * tag["size_px"], u["y"])
        # the credit: under the URL, small but not below the minimum, above the band
        self.assertGreaterEqual(c["size_px"], r["min_text_px"])
        self.assertLess(c["size_px"], u["size_px"])
        self.assertLessEqual(u["y"] + u["size_px"], c["y"])
        self.assertLess(c["y"] + 1.25 * c["size_px"], r["subtitle_band"]["y"])
        self.assertTrue(0 < c["alpha"] < 1)
        self.assertLess(t["credit_s"] + c["fade_dur_s"], n["duration_s"])

    def test_n18_page(self):
        html = (lc.SCREENS / "n18.html").read_text(encoding="utf-8")
        self.assertIn('<p class="credit" data-box="n18.credit" data-kind="note">HIRAKU Powered by Claude</p>', html)
        self.assertNotIn("Built with Claude", html)
        self.assertNotIn("powered", html)                  # the ?powered=1 variant is gone
        for part in ('svg class="gather"', 'class="seed-line"', 'class="leaf leaf-l"', 'class="leaf leaf-r"'):
            self.assertIn(part, html)
        # ends on paper, the same ground as N00; the night is the door, dropped in ?layer=fg
        self.assertIn('<div class="stage paper-grid">', html)
        self.assertIn('<div class="stage paper-grid">', (lc.SCREENS / "n00.html").read_text(encoding="utf-8"))
        self.assertIn('<div class="door bg-only" data-kind="effect"', html)
        self.assertNotIn("--page-bg: var(--night)", html)
        self.assertNotIn("fade-out", html)                 # no fade to black at the end
        self.assertNotIn("<img", html)                     # no logo
        for banned in BANNED:
            self.assertNotIn(banned, html)

    def check_cast(self, cut: str, cast: dict, hop: dict, still_s: float):
        """The routes of one cut (n00.cast, n18.pets.cast): each of the ten once,
        beats of known kinds in time order, walks in whole steps at a walking
        pace, hops with a crouch and a landing squash, small acts, and all over
        by still_s."""
        pet3d = (lc.SCREENS.parent / "pets" / "pet3d.js").read_text(encoding="utf-8")
        roles = re.findall(r'\{ id: "([a-z.]+)", name:', pet3d)
        self.assertEqual(sorted(cast), sorted(roles), cut)
        places, moves = set(), set()
        for role, c in cast.items():
            beats = c["beats"]
            self.assertTrue(c["_who"], role)
            self.assertIn(c.get("layer", "front"), ("front", "behind"), role)
            ts = [b["t"] for b in beats]
            self.assertEqual(ts, sorted(ts), role)                           # in time order
            self.assertLessEqual(route_end(beats, hop), still_s + 1e-9, role)  # all over before the clocks stop
            for b in beats:
                self.assertIn(b["do"], ROUTE_KINDS, (role, b))
                moves.add(b["do"] if b["do"] != "act" else b["kind"])
                if b["do"] == "walk":
                    # the steps and the pace are checked on the page (test_walks_do_not_slide)
                    self.assertGreaterEqual(b["dur_s"], 0.6, role)
                    self.assertLessEqual(b.get("swing", 0.55), 0.75, role)       # pet3d LEG.swing
                if b["do"] in ("hop", "hops"):
                    j = {**hop, **b}
                    self.assertGreater(j["crouch_s"], 0, role)              # anticipation
                    self.assertGreater(j["land_sq"], j["stretch"] * 0.5, role)  # squash on landing
                    self.assertGreater(j["air_s"], 0, role)
                    if b["do"] == "hops":
                        self.assertGreaterEqual(b["every_s"], hop_times(j)[0] + j["land_s"], role)  # one after the other
                if b["do"] == "act":
                    self.assertIn(b["kind"], ("look", "nod", "sway", "fiddle", "bow", "peek"), role)
                    self.assertLessEqual(b["amp_deg"], 30, role)
                    if b["kind"] == "peek":
                        self.assertEqual(c.get("layer"), "behind", role)       # peeks from behind a letter
                        self.assertLessEqual(abs(b["roll_deg"]), 30, role)
            end = c["beats"][-1]
            places.add(json.dumps(end.get("to", c["start"]["at"])))
        # not ten of one: many kinds of move, and the pets start at different times
        self.assertGreaterEqual(len(moves), 8, (cut, moves))
        firsts = sorted(min(b["t"] for b in c["beats"] if b["do"] not in ("tick",)) for c in cast.values())
        self.assertGreaterEqual(len(set(firsts)), 8, cut)
        return moves

    def test_n00_cast(self):
        r, n = self.rules, self.rules["n00"]
        cast, pl, t = n["cast"], n["play"], n["times"]
        moves = self.check_cast("n00", cast, pl["hop"], pl["stop"]["still_s"])
        for kind in ("walk", "hop", "sit", "bow", "peek", "turn"):
            self.assertIn(kind, moves)                                      # walks in, sits, bows, peeks, ...
        self.assertLessEqual(pl["stop"]["still_s"], 7.4)
        self.assertEqual(blinks_clear(pl["stop"], n["motion"]["idle_shift_s"], 10), [])
        # the seven domain pets stand in the row over their chips until their chip lights,
        # and show the tick when it does
        domains = ["cae.handling", "cae.powertrain", "cae.aero", "cae.crash", "cae.strength", "cae.nvh", "cae.thermal"]
        for k, role in enumerate(domains):
            c = cast[role]
            self.assertEqual(c["start"]["at"], {"row": k}, role)
            lit = t["light_s"] + k * t["step_s"]
            tick = [b for b in c["beats"] if b["do"] == "tick"]
            self.assertEqual(len(tick), 1, role)
            self.assertAlmostEqual(tick[0]["t"], lit, places=6)
            moving = [b for b in c["beats"] if b["do"] not in ("tick",)]
            self.assertGreaterEqual(moving[0]["t"], lit, role)               # nobody leaves before its chip lights
            self.assertLessEqual(moving[0]["t"], lit + 0.25, role)           # and it reacts at once
        # nobody is on the name, or comes in, while the voice says it (0.3-1.3 s)
        for role, c in cast.items():
            first = min(b["t"] for b in c["beats"])
            on_name = [b for b in c["beats"] if isinstance(b.get("to"), dict) and "seat" in b["to"]]
            for b in on_name:
                self.assertGreaterEqual(b["t"], 1.3, role)
            if c.get("layer") == "behind":
                self.assertGreaterEqual(first, 1.3, role)
                self.assertEqual(c["start"]["alpha"], 0, role)
        # every letter gets a pet, the rest stand at the name's foot or behind a letter
        seats = {role: [b["to"] for b in c["beats"] if isinstance(b.get("to"), dict) and "seat" in b["to"]]
                 for role, c in cast.items()}
        on = [role for role, s in seats.items() if s]
        self.assertEqual(sorted(seats[role][-1]["seat"] for role in on), list(range(6)), on)
        self.assertGreaterEqual(len([c for c in cast.values() if c.get("layer") == "behind"]), 1)
        # two pass each other and bow: their bows overlap in time, facing each other
        bows = sorted((b["t"], role) for role, c in cast.items() for b in c["beats"] if b.get("kind") == "bow")
        self.assertGreaterEqual(len(bows), 2)
        self.assertLess(bows[1][0] - bows[0][0], 0.5)
        # the shaken letter is the NVH pet's; the pusher sits on the next letter
        sh, pu = pl["shake"], pl["push"]
        self.assertEqual(seats["cae.nvh"][-1]["seat"], sh["letter"])
        for role in pu["roles"]:
            self.assertEqual(seats[role][-1]["seat"], sh["letter"] + 1)
            land = [b for b in cast[role]["beats"] if b["do"] == "hop"][-1]
            self.assertGreaterEqual(pu["start_s"] - pu["wind_s"], land["t"] + hop_times({**pl["hop"], **land})[1] - 0.1)
        nvh = [b for b in cast["cae.nvh"]["beats"] if b["do"] == "hop"][-1]
        self.assertGreaterEqual(sh["start_s"], nvh["t"] + hop_times({**pl["hop"], **nvh})[0])
        self.assertLessEqual(sh["start_s"] + sh["dur_s"], pu["start_s"] - pu["wind_s"])
        self.assertLessEqual(pu["start_s"] + max(pu["lag_s"]) + pu["dur_s"], pl["still_s"])
        self.assertEqual(len(pu["roles"]), len(pu["step_px"]))
        self.assertEqual(len(pu["roles"]), len(pu["lag_s"]))
        # small moves, so the letters stay readable
        size = n["name"]["size_px"]
        for part in (pl["land"], sh):
            for k in ("sink_px", "dx_px"):
                if k in part:
                    self.assertLess(part[k], 0.1 * size + 1e-9)
        self.assertLessEqual(sh["tilt_deg"] + sh["rot_deg"], 8)
        self.assertLessEqual(max(max(pu["step_px"]), pu["wind_px"]), 0.1 * size)
        self.assertLessEqual(pu["lean_deg"], 20)
        for role, c in cast.items():
            for b in c["beats"]:
                if b["do"] in ("hop", "hops"):
                    self.assertLessEqual({**pl["hop"], **b}["over_px"], 0.35 * size, role)
        # the play starts after the name has been said
        self.assertGreaterEqual(pl["from_s"], 1.3)
        self.assertLessEqual(pl["still_s"], 7.4)

    def test_n18_cast(self):
        r, n = self.rules, self.rules["n18"]
        g, t = n["pets"], n["times"]
        moves = self.check_cast("n18", g["cast"], g["hop"], g["stop"]["still_s"])
        for kind in ("walk", "hops", "hop", "sit", "peek", "turn"):
            self.assertIn(kind, moves)
        st = g["stop"]
        self.assertLess(st["from_s"], st["still_s"])
        self.assertLess(st["still_s"], 8.5)
        self.assertEqual(blinks_clear(st, r["n00"]["motion"]["idle_shift_s"], 10), [])
        self.assertNotIn("seats", g)
        self.assertNotIn("title_pets", n)
        d = n["door"]
        gone = t["open_s"] + d["crack_dur_s"] + d["hold_dur_s"] + d["open_dur_s"]
        name_in = t["name_s"] + 5 * 0.07 + 0.7                              # title.css letter-in
        for role, c in g["cast"].items():
            self.assertGreaterEqual(min(b["t"] for b in c["beats"]), gone, role)  # on the paper, after the door
            self.assertEqual(c["start"].get("alpha"), 0, role)
            for b in c["beats"]:
                if isinstance(b.get("to"), dict) and "seat" in b["to"]:
                    self.assertGreaterEqual(b["t"], name_in, role)               # onto a letter once it is in
        on = [role for role, c in g["cast"].items()
              if any(isinstance(b.get("to"), dict) and "seat" in b["to"] for b in c["beats"])]
        self.assertTrue(2 <= len(on) <= 5, on)
        self.assertGreaterEqual(len([c for c in g["cast"].values() if c.get("layer") == "behind"]), 1)
        self.assertGreaterEqual(n["vehicle"]["y"], r["safe"]["y"])

    def test_n18_pets_page(self):
        html = (lc.SCREENS / "n18.html").read_text(encoding="utf-8")
        n00 = (lc.SCREENS / "n00.html").read_text(encoding="utf-8")
        for part in ('"three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js"',
                     '<script src="../pets/pet3d.js"></script>', "promo.onSeek(draw)", "<b>${c}</b>"):
            self.assertIn(part, html)
            self.assertIn(part, n00)
        # the size, camera and pet box come from layout.json n00; the routes from n18.pets.cast
        for part in ("N0.title_pets", "N0.camera", "N0.pets", "N0.motion", "measureText",
                     "actualBoundingBoxAscent", "R.pets.cast", "A.route(", "G.hop", "b.pose"):
            self.assertIn(part, html)
        self.assertNotIn('"at":', html)
        ids = re.findall(r'\{ id: "([a-z.]+)", tag:', html)
        pet3d = (lc.SCREENS.parent / "pets" / "pet3d.js").read_text(encoding="utf-8")
        self.assertEqual(ids, re.findall(r'\{ id: "([a-z.]+)", name:', pet3d))
        self.assertIn('data-kind="moving"', html)
        # a layer behind the words and one in front (both pages)
        for page in (html, n00):
            self.assertIn('<div class="pets behind"></div>', page)
            self.assertIn('<div class="pets front"></div>', page)
            self.assertLess(page.index('<div class="pets behind"></div>'), page.index('<h1 class="name"'))
        # the letters still come in by title.css's letter-in
        title = (lc.SCREENS / "title.css").read_text(encoding="utf-8")
        self.assertIn("animation: letter-in 0.7s cubic-bezier(.2, .7, .2, 1) both;", title)
        self.assertIn("animation-delay: calc(var(--t-name) + var(--i) * 0.07s);", title)
        for banned in ("Math.random", "Date.now", "performance.now", "requestAnimationFrame", "setAnimationLoop"):
            self.assertNotIn(banned, html.split('<script type="module">')[1])

    def test_n00_rules(self):
        r, n = self.rules, self.rules["n00"]
        self.assertEqual(n["tagline"]["text"], "作る前に、CAE で確かめる AI エージェント")
        self.assertGreaterEqual(n["tagline"]["size_px"], r["story"]["defaults"]["body_text_px"])
        self.assertGreater(n["tagline"]["y"], n["rule"]["y"] + n["rule"]["h"])
        self.assertLessEqual(n["tagline"]["y"] + 1.3 * n["tagline"]["size_px"], n["pets"]["y"])
        p = n["pets"]
        span = 6 * p["pitch_px"] + p["w"]                               # the seven domain pets
        x0 = (r["stage"]["w"] - span) / 2
        self.assertGreaterEqual(x0, r["safe"]["x"])                    # the row is inside safe
        self.assertLess(p["w"], p["pitch_px"])                          # boxes do not touch
        self.assertNotIn("tag", n)                                      # no name tags
        self.assertLess(p["y"] + p["h"] + n["chip"]["gap_above_px"] + n["chip"]["h"], r["subtitle_band"]["y"])
        self.assertGreaterEqual(n["chip"]["size_px"], r["story"]["defaults"]["body_text_px"])
        t = n["times"]
        self.assertEqual((t["name_s"], t["tagline_s"], t["row_s"], t["light_s"], t["step_s"]),
                         (0.25, 1.0, 1.4, 2.0, 0.42))                   # the chips light when they did
        self.assertLess(t["name_s"], t["tagline_s"])
        self.assertLess(t["row_s"] + 0.5, t["light_s"] + 1e-9)          # the row is in before the first chip
        self.assertLess(t["light_s"] + 6 * t["step_s"] + n["motion"]["pass_dur_s"], n["duration_s"])
        self.assertEqual(n["motion"]["pass_dur_s"], n["motion"]["hop_period_s"])  # one hop per jump
        self.assertNotIn("jump_over_px", n["motion"])                   # the jumps are n00.play.hop / cast
        lim = r["story"]["cuts"]["n00"]["limits"]
        self.assertGreaterEqual(lim["label"], 7)                        # the 7 chips
        self.assertGreaterEqual(lim["moving"], 10)                      # the ten pets
        self.assertTrue(lim["why"])

    def test_n00_page(self):
        html = (lc.SCREENS / "n00.html").read_text(encoding="utf-8")
        # three.js r170 by the importmap of promo/pets/viewer.html; pet3d.js as a plain script
        viewer = (lc.SCREENS.parent / "pets" / "viewer.html").read_text(encoding="utf-8")
        for part in ('"three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js"',
                     '"three/addons/": "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"'):
            self.assertIn(part, html)
            self.assertIn(part, viewer)
        self.assertIn('<script src="../pets/pet3d.js"></script>', html)
        self.assertNotIn("pets.js\"", html.replace("pet3d.js\"", ""))   # not the flat drawing
        self.assertIn("promo.onSeek(draw)", html)
        # the pose depends on timeline.js's t only
        for banned in ("Math.random", "Date.now", "performance.now", "requestAnimationFrame", "setAnimationLoop"):
            self.assertNotIn(banned, html)
        ids = re.findall(r'\{ id: "([a-z.]+)", (?:tag|chip):', html)
        pet3d = (lc.SCREENS.parent / "pets" / "pet3d.js").read_text(encoding="utf-8")
        self.assertEqual(ids, re.findall(r'\{ id: "([a-z.]+)", name:', pet3d))
        self.assertEqual(len(ids), 10)
        self.assertIn('data-kind="moving"', html)
        # the seats are measured on the name's letters (n00.title_pets)
        for part in ("measureText", "actualBoundingBoxAscent", "TP.h_em", "TP.lift_px"):
            self.assertIn(part, html)
        self.assertNotIn("jump_over_px", html)
        self.assertNotIn('class="tag"', html)                           # no name tags any more
        # the play: every letter is a <b> moved at each t from n00.play
        for part in ("R.play", "<b>${c}</b>", "letterMove", "onLetter", "var(--name-in-dur)", "var(--name-in-step)"):
            self.assertIn(part, html)
        # each pet's route is pet3d's anim.route over its beats in n00.cast (hops on
        # n00.play.hop), passed to update as pose terms
        for part in ("HP.anim", "A.route(", "A.hop(", "R.cast", "PL.hop", "PL.stop", "b.pose", "yawTo"):
            self.assertIn(part, html)
        self.assertNotIn('"pass"', html)                                 # no pet3d pass hop on top
        for banned in BANNED:
            self.assertNotIn(banned, html)

    def test_n10_wait_line(self):
        html = (lc.SCREENS / "n10.html").read_text(encoding="utf-8")
        w = self.rules["n10"]["wait"]
        self.assertEqual(w["text"], "これから確かめる")
        self.assertGreaterEqual(w["size_px"], self.rules["min_text_px"])
        self.assertIn('<div class="wait" data-kind="note">${R.wait.text}</div>', html)
        self.assertNotIn('<div class="wait">?</div>', html)

    def test_labels(self):
        r, lab = self.rules, self.rules["labels"]
        self.assertEqual(set(lab["cuts"]), {"n01", "n02", "n03", "n04"})
        for i, (cut, w) in enumerate(sorted(lab["cuts"].items()), 1):
            self.assertEqual(w["num"], f"{i:02d}")
            self.assertTrue(w["text"])
            # it is in for a while: in, then out before the end
            d = r[cut]["duration_s"]
            self.assertLess(lab["times"]["in_s"] + lab["in_dur_s"], d - lab["times"]["out_before_end_s"] - lab["out_dur_s"])
        self.assertEqual((r["n01"]["duration_s"], r["n02"]["duration_s"], r["n03"]["duration_s"],
                          r["n04"]["duration_s"]), (4.0, 7.0, 5.0, 4.0))
        self.assertEqual(lab["times"], {"in_s": 0.2, "out_before_end_s": 0.3})
        b = lab["box"]
        self.assertLessEqual(b["y"] + b["h"], r["subtitle_band"]["y"])
        self.assertGreaterEqual(b["x"], r["safe"]["x"])
        self.assertGreaterEqual(min(lab["num_size_px"], lab["text_size_px"]), r["min_text_px"])
        self.assertLess(b["h"], 2.5 * lab["text_size_px"])  # small: one line

    def test_label_pages(self):
        js = (lc.SCREENS / "labels.js").read_text(encoding="utf-8")
        for banned in BANNED:
            self.assertNotIn(banned, js)
        for name in ["labels"] + [f"labels_n0{i}" for i in range(1, 5)]:
            html = (lc.SCREENS / f"{name}.html").read_text(encoding="utf-8")
            self.assertIn('data-cut="labels"', html)
            self.assertLess(html.index('src="labels.js"'), html.index('src="timeline.js"'))
            self.assertIn('class="label-ground bg-only"', html)
            if name != "labels":
                self.assertIn(f'data-label-cut="{name[7:]}"', html)
                self.assertIn(name, lc.CUTS)


def n10_data() -> dict:
    """The DATA block of n10.html (plain JSON between 'const DATA = ' and '};')."""
    html = (lc.SCREENS / "n10.html").read_text(encoding="utf-8")
    m = re.search(r"const DATA = (\{.*?\n  \});", html, re.S)
    return json.loads(m.group(1))


def n10_diffs(row: dict) -> list[float]:
    """(analysis - answer) / answer in %, as HIRAKU compares a prediction with an analysis."""
    return [100 * (row["cae"]["v"] - c) / c for c in row["claude"]]


class N10(unittest.TestCase):
    """N10: Claude's answers without tools beside HIRAKU's values."""

    def setUp(self):
        self.rules = layout_build.load()
        self.n = self.rules["n10"]
        self.html = (lc.SCREENS / "n10.html").read_text(encoding="utf-8")

    def test_duration_matches_the_page(self):
        m = re.search(r'data-duration="([0-9.]+)"', self.html)
        self.assertEqual(float(m.group(1)), self.n["duration_s"])
        self.assertEqual(self.n["duration_s"], 8.3)

    def test_boxes_stay_above_the_band(self):
        band, safe, stage = self.rules["subtitle_band"]["y"], self.rules["safe"], self.rules["stage"]
        t, n = self.n["table"], self.n
        bottom = t["y"] + t["head_h"] + t["head_gap_px"] + 3 * t["row_h"] + 2 * t["row_gap_px"]
        self.assertLessEqual(bottom, n["src"]["y"])
        self.assertLessEqual(n["src"]["y"] + 2 * 1.5 * n["src"]["size_px"], band)  # two lines
        self.assertLessEqual(n["title"]["y"] + 1.4 * n["title"]["size_px"], t["y"])
        for b in (t, n["src"]):
            self.assertGreaterEqual(b["x"], safe["x"])
            self.assertLessEqual(b["x"] + b["w"], stage["w"] - safe["x"])
        cols = {k: v for k, v in n["cols"].items() if not k.startswith("_")}
        self.assertEqual(sum(cols.values()), t["w"])
        sizes = [v for k, v in n["text"].items() if not k.startswith("_")]
        self.assertGreaterEqual(min(sizes + [n["src"]["size_px"], n["title"]["size_px"]]), self.rules["min_text_px"])

    def test_times_follow_the_voice(self):
        t = self.n["times"]
        for k, v in t.items():
            if not k.startswith("_"):
                self.assertTrue(k.endswith("_s"), k)
        # all three answers are in during sentence 1 (0.30-3.18 s)
        last_answer = t["rows_s"] + 2 * t["row_step_s"] + t["answer_lag_s"]
        self.assertLess(last_answer, 3.18)
        # HIRAKU's values and the ticks come during sentence 2 (3.53-7.62 s)
        self.assertGreaterEqual(t["check_s"], 3.53)
        last_tick = t["check_s"] + 2 * t["check_step_s"] + t["tick_lag_s"]
        self.assertLess(last_tick, 7.62)
        # the full picture holds still for at least the last second
        self.assertLessEqual(max(last_tick, t["src_s"]) + 0.5 + 1.0, self.n["duration_s"])
        self.assertTrue(self.rules["check"]["times"]["n10"])

    def test_page_names_and_words(self):
        self.assertIsNone(re.search(r"--t-[a-z-]+\s*:", self.html))
        for name in re.findall(r'data-box="([^"$]+)"', self.html):
            self.assertTrue(name.startswith("n10."), name)
        self.assertIn("CAE ツールで確かめる", self.html)
        self.assertIn("ツールなしの Claude", self.html)
        self.assertNotIn("ハルシネーション", self.html)
        self.assertNotIn("道具", self.html)
        for banned in ("requestAnimationFrame", "Date.now", "Math.random"):
            self.assertNotIn(banned, self.html)

    def test_data_is_the_record(self):
        d = n10_data()
        self.assertEqual(d["asked"], 3)
        rows = {r["key"]: r for r in d["rows"]}
        self.assertEqual(list(rows), ["step", "tilt", "stress"])
        self.assertEqual(rows["step"]["claude"], [1090, 1090, 1090])
        self.assertEqual([h["v"] for h in rows["step"]["hand"]], [1092.9])
        self.assertEqual((rows["step"]["cae"]["v"], rows["step"]["cae"]["by"]), (1103.7, "K1"))
        self.assertEqual(rows["tilt"]["claude"], [39, 38, 39])
        self.assertEqual([h["v"] for h in rows["tilt"]["hand"]], [39.85])
        self.assertEqual((rows["tilt"]["cae"]["v"], rows["tilt"]["cae"]["by"]), (38.26, "K2"))
        # only one of the three runs gave a figure for the stress
        self.assertEqual(rows["stress"]["claude"], [60])
        self.assertEqual([h["v"] for h in rows["stress"]["hand"]], [40.0, 77.09])
        self.assertEqual((rows["stress"]["cae"]["v"], rows["stress"]["cae"]["by"]), (61.15, "K1"))

    def test_differences(self):
        rows = {r["key"]: r for r in n10_data()["rows"]}
        self.assertAlmostEqual(n10_diffs(rows["step"])[0], 1.2569, places=3)
        self.assertEqual([round(x, 2) for x in n10_diffs(rows["tilt"])], [-1.9, 0.68, -1.9])
        self.assertAlmostEqual(n10_diffs(rows["stress"])[0], 1.9167, places=3)
        # the widths HIRAKU allows for these quantities in the video run's comparisons
        self.assertEqual([r["tol_pct"] for r in rows.values()], [10, 10, 15])
        for r in rows.values():
            self.assertTrue(all(abs(x) <= r["tol_pct"] for x in n10_diffs(r)), r["key"])


class N08(unittest.TestCase):
    """N08: an ordinary CAE tool (not HIRAKU), hard to buy and hard to learn."""

    # sentences of the voice (s from the start of the cut), measured from its pauses
    BUY = (0.9, 3.5)      # CAE のツールは気軽に買えるものではなく、
    LEARN = (3.6, 4.8)    # 使いこなすのも
    HARD = (4.9, 6.0)     # 簡単ではありません。

    def setUp(self):
        self.rules = layout_build.load()
        self.n = self.rules["n08"]
        self.html = (lc.SCREENS / "n08.html").read_text(encoding="utf-8")

    def test_duration_matches_the_page(self):
        m = re.search(r'data-duration="([0-9.]+)"', self.html)
        self.assertEqual(float(m.group(1)), self.n["duration_s"])
        self.assertEqual(self.n["duration_s"], 6.4)
        self.assertEqual(self.n["camera"]["move_dur_s"], self.n["duration_s"])

    def test_boxes_stay_above_the_band(self):
        band, stage = self.rules["subtitle_band"]["y"], self.rules["stage"]
        boxes = {k: v for k, v in self.n["win"].items() if not k.startswith("_")}
        boxes["main"], boxes["books"] = self.n["main"], self.n["books"]
        for k, b in boxes.items():
            self.assertGreaterEqual(b["x"], 0, k)
            self.assertGreaterEqual(b["y"], 0, k)
            self.assertLessEqual(b["x"] + b["w"], stage["w"], k)
            self.assertLessEqual(b["y"] + b["h"], band, k)
        bk = self.n["books"]
        self.assertLessEqual(sum(bk["heights_px"]), bk["h"])
        self.assertLessEqual(bk["book_w"] + 2 * bk["shift_px"] + 14, bk["w"])  # 14: the page edges

    def test_text_sizes(self):
        sizes = [v for k, v in self.n["text"].items() if not k.startswith("_")]
        self.assertGreaterEqual(min(sizes), self.rules["min_text_px"])
        # the application is drawn at camera.from_scale at first: still 18 px or more
        app = [v for k, v in self.n["text"].items() if not k.startswith("_") and k != "book_px"]
        self.assertGreaterEqual(min(app) * self.n["camera"]["from_scale"], self.rules["min_text_px"])

    def test_times_follow_the_voice(self):
        t = self.n["times"]
        for k in t:
            if not k.startswith("_"):
                self.assertTrue(k.endswith("_s"), k)
        # licence windows and the ten module rows while the voice says it is not easy to buy
        for k in ("license_s", "modules_s", "select_s", "nolicense_s"):
            self.assertTrue(self.BUY[0] <= t[k] < self.BUY[1], k)
        last_row = t["module_rows_s"] + 9 * t["module_step_s"] + self.n["anim"]["row_dur_s"]
        self.assertLess(last_row, self.BUY[1])
        # settings windows while it says 使いこなすのも
        settings = [t[k] for k in ("mesh_s", "material_s", "bc_s", "contact_s", "solver_s")]
        self.assertEqual(settings, sorted(settings))
        for x in settings:
            self.assertTrue(self.LEARN[0] <= x < self.LEARN[1], x)
        # warnings while it says 簡単ではありません
        for k in ("warn_shape_s", "warn_conv_s", "warn_rigid_s"):
            self.assertTrue(self.HARD[0] <= t[k] < self.HARD[1], k)
        # the pointer reaches the fatigue row before it clicks it, and clicks
        # 計算を始める after the solver window is open and before the warnings
        path = self.n["pointer"]["path"]
        ts = [p["t_s"] for p in path]
        self.assertEqual(ts, sorted(ts))
        self.assertEqual((ts[0], ts[-1]), (0.0, self.n["duration_s"]))
        at = lambda x: next(p for p in reversed(path) if p["t_s"] <= x)  # noqa: E731
        self.assertLess(at(t["select_s"])["t_s"], t["select_s"])
        self.assertTrue(t["solver_s"] < at(t["run_s"])["t_s"] < t["run_s"] < t["warn_shape_s"])
        books_in = t["books_s"] + 5 * t["book_step_s"] + self.n["anim"]["book_dur_s"]
        self.assertLess(books_in, self.HARD[1])
        # the darkening takes the last half second or so and ends with the cut
        self.assertGreaterEqual(t["dim_s"], self.HARD[1] - 0.2)
        self.assertAlmostEqual(t["dim_s"] + self.n["dim"]["fade_dur_s"], self.n["duration_s"], places=6)
        self.assertTrue(0 < self.n["dim"]["peak_alpha"] < 1)
        # each dialog has its own time, in the order of the stacking
        wins = [k for k in self.n["win"] if not k.startswith("_")]
        self.assertEqual([self.n["win"][k]["z"] for k in wins], sorted(self.n["win"][k]["z"] for k in wins))
        self.assertEqual([t[k + "_s"] for k in wins], sorted(t[k + "_s"] for k in wins))
        self.assertTrue(self.rules["check"]["times"]["n08"])

    def test_page_names_and_words(self):
        self.assertIsNone(re.search(r"--t-[a-z-]+\s*:", self.html))
        for name in re.findall(r'data-box="([^"$]+)"', self.html):
            self.assertTrue(name.startswith("n08."), name)
        for banned in ("requestAnimationFrame", "Date.now", "Math.random", "performance.now"):
            self.assertNotIn(banned, self.html)
        # not HIRAKU, and no prices (the price line asks for a quote)
        self.assertNotIn("HIRAKU", self.html.split("<script>")[1])
        self.assertIn("お見積もりはお問い合わせください", self.html)
        self.assertIn("ノード数の上限", self.html)
        self.assertIn("ライセンスの有効期限", self.html)
        self.assertIsNone(re.search(r"[¥€￥]|円|ドル", self.html))
        # the warnings carry words only, no counts
        for m1, m2 in re.findall(r'msg\((?:WARN|ERR), "([^"]+)", "([^"]+)"\)', self.html):
            self.assertIsNone(re.search(r"[0-9０-９]", m1 + m2), m1)
        self.assertEqual(len(re.findall(r'msg\((?:WARN|ERR), ', self.html)), 4)
        self.assertIn("要素の形が悪い", self.html)
        self.assertIn("収束しませんでした", self.html)


class Judging(unittest.TestCase):
    def test_band(self):
        self.assertFalse(lc.in_band(box(0, 800, 100, 110), 910))      # ends on the band's top
        self.assertTrue(lc.in_band(box(0, 800, 100, 111), 910))
        self.assertTrue(lc.in_band(box(0, 950, 100, 20), 910))

    def test_outside(self):
        self.assertFalse(lc.outside(box(0, 0, 1920, 1080), 1920, 1080))
        self.assertTrue(lc.outside(box(-2, 10, 50, 50), 1920, 1080))
        self.assertTrue(lc.outside(box(1900, 10, 30, 50), 1920, 1080))
        self.assertTrue(lc.outside(box(10, 1070, 30, 20), 1920, 1080))
        self.assertFalse(lc.outside(box(-0.3, 0, 10, 10), 1920, 1080))  # rounding is not a problem

    def test_intersection(self):
        self.assertEqual(lc.intersection(box(0, 0, 100, 100), box(50, 60, 100, 100)), box(50, 60, 50, 40))
        self.assertIsNone(lc.intersection(box(0, 0, 100, 100), box(100, 0, 100, 100)))  # touching
        self.assertIsNone(lc.intersection(box(0, 0, 100, 100), box(0, 200, 100, 100)))

    def test_overflow(self):
        fits = {"sw": 100, "cw": 100, "sh": 31, "ch": 30}
        self.assertEqual(lc.overflows(fits), {})
        wide = {"sw": 140, "cw": 100, "sh": 30, "ch": 30}
        self.assertEqual(lc.overflows(wide), {"x": 40})
        tall = {"sw": 100, "cw": 100, "sh": 36, "ch": 30}
        self.assertEqual(lc.overflows(tall), {"y": 6})
        self.assertEqual(lc.overflows(None), {})

    def test_overflow_allowance_scales_with_font(self):
        tall = {"sw": 100, "cw": 100, "sh": 36, "ch": 30}
        allow = [{"axis": "y", "max_em": 0.25}]
        self.assertEqual(lc.overflows(tall, 30, allow), {})            # 6 px <= 7.5 px
        self.assertEqual(lc.overflows(tall, 16, allow), {"y": 6})      # 6 px > 4 px + 1
        self.assertEqual(lc.overflows({"sw": 140, "cw": 100, "sh": 30, "ch": 30}, 30, allow), {"x": 40})

    def test_small(self):
        self.assertTrue(lc.too_small(17.5, 18))
        self.assertFalse(lc.too_small(18, 18))
        self.assertFalse(lc.too_small(None, 18))

    def test_escapes(self):
        outer = box(100, 100, 200, 100)
        self.assertEqual(lc.escapes(box(110, 110, 50, 50), outer), {})
        self.assertEqual(lc.escapes(box(280, 110, 50, 50), outer), {"right": 30})
        self.assertEqual(lc.escapes(box(90, 180, 50, 30), outer), {"left": 10, "bottom": 10})
        self.assertEqual(lc.escapes(box(100.3, 100, 200, 100), outer), {})  # rounding

    def test_escape_room(self):
        allow = [{"cut": "n08b", "box": "n08b.fig.*.cap", "max_px": 14}]
        self.assertEqual(lc.escape_room("n08b", "n08b.fig.left.cap", allow), 14)
        self.assertEqual(lc.escape_room("n08b", "n08b.bars.large.value", allow), 0)
        self.assertEqual(lc.escape_room("n05", "n08b.fig.left.cap", allow), 0)

    def test_overlap_allowed(self):
        allow = [{"cut": "n18", "a": "light", "b": "vehicle"}, {"cut": "*", "a": "tile:*", "b": "hero"}]
        self.assertTrue(lc.overlap_allowed("n18", "vehicle", "light", allow))
        self.assertFalse(lc.overlap_allowed("n05", "light", "vehicle", allow))
        self.assertTrue(lc.overlap_allowed("n05", "hero", "tile:dlc", allow))

    def _layout(self):
        return {"stage": {"w": 1920, "h": 1080}, "subtitle_band": {"y": 910, "h": 170},
                "min_text_px": 18,
                "check": {"allow_overlap": [{"cut": "x", "a": "p", "b": "q"}],
                          "allow_escape": [{"cut": "x", "box": "p.*", "max_px": 5}]}}

    def item(self, name, b, **kw):
        return {"name": name, "label": name or "div", "text": kw.pop("text", ""), "box": b,
                "inside": kw.pop("inside", []), **kw}

    def test_cut_of_reads_the_cut_parameter(self):
        # the app's cut.html serves every cut, so its rules are looked up under ?cut=
        self.assertEqual(lc.cut_of("http://127.0.0.1:8765/cut.html?cut=n07&run=r&before=1"), "n07")
        self.assertEqual(lc.cut_of("http://127.0.0.1:8765/cut.html?run=r"), "cut")
        self.assertEqual(lc.cut_of(Path("promo/screens/n18.html")), "n18")

    def test_judge_lists_each_kind(self):
        items = [
            self.item("a", box(100, 100, 200, 100)),
            self.item("b", box(250, 150, 200, 100)),                             # overlaps a
            self.item("p", box(1000, 100, 100, 100)),
            self.item("q", box(1050, 100, 100, 100)),                            # allowed with p
            self.item("in", box(120, 120, 20, 20), inside=["a"]),                # inside a: not an overlap
            self.item("out", box(280, 105, 40, 20), inside=["a"]),               # sticks out of a (right +20)
            self.item("p.k", box(995, 150, 10, 10), inside=["p"]),               # 5 px out of p: allowed
            self.item(None, box(10, 950, 100, 30), text="字幕の帯", font=30),
            self.item(None, box(10, 950, 100, 30), text="guide", font=30, band_exempt=True),
            self.item(None, box(1900, 10, 100, 30), text="edge", font=30),
            self.item(None, box(10, 10, 50, 30), text="long", font=30,
                      scroll={"sw": 90, "cw": 50, "sh": 30, "ch": 30}),
            self.item(None, box(10, 300, 50, 12), text="小さい", font=12),
        ]
        probs = lc.judge(items, self._layout(), "x")
        kinds = sorted((p["kind"], p["element"]) for p in probs)
        self.assertEqual(kinds, [("band", "div"), ("escape", "out"), ("outside", "div"), ("overflow", "div"),
                                 ("overlap", "a × b"), ("small", "div")])
        overlap = next(p for p in probs if p["kind"] == "overlap")
        self.assertEqual(overlap["box"], box(250, 150, 50, 50))

    def test_judge_clean(self):
        items = [self.item("a", box(0, 0, 100, 100)), self.item("b", box(200, 0, 100, 100))]
        self.assertEqual(lc.judge(items, self._layout(), "x"), [])

    STORY = {"defaults": {"label": 3, "graph": 1, "legend": 1, "number": 1, "moving": 3, "body_text_px": 24, "_note": "x"},
             "cuts": {"a": {"message": "m"},
                      "b": {"message": "m", "limits": {"label": 5, "body_text_px": 20, "why": "w"}}}}

    def census(self, labels=0, numbers=0, moving=0, fonts=()):
        e = lambda i: {"label": f"e{i}", "text": "", "box": box(10 * i, 0, 5, 5)}  # noqa: E731
        return {"cut": None, "kinds": {"label": [e(i) for i in range(labels)], "number": [e(i) for i in range(numbers)]},
                "moving": [e(i) for i in range(moving)],
                "body": [{**e(i), "font": f} for i, f in enumerate(fonts)]}

    def test_story_cut(self):
        self.assertEqual(lc.story_cut(None, "a", self.STORY), "a")          # the file name
        self.assertEqual(lc.story_cut("b", "cut", self.STORY), "b")         # ?cut= or <body data-cut> first
        self.assertIsNone(lc.story_cut(None, "fixture", self.STORY))        # not in the story: not counted

    def test_story_limits(self):
        self.assertEqual(lc.story_limits(self.STORY, "a"),
                         {"label": 3, "graph": 1, "legend": 1, "number": 1, "moving": 3, "body_text_px": 24})
        b = lc.story_limits(self.STORY, "b")
        self.assertEqual((b["label"], b["body_text_px"], b["number"]), (5, 20, 1))
        self.assertNotIn("why", b)

    def test_census_counts(self):
        c = lc.census_counts(self.census(labels=2, moving=4, fonts=(30, 22.5)))
        self.assertEqual(c, {"label": 2, "graph": 0, "legend": 0, "number": 0, "moving": 4, "body_text_px": 22.5})
        self.assertIsNone(lc.census_counts(self.census())["body_text_px"])

    def test_busy_over_the_limits(self):
        probs = lc.judge_busy(self.census(labels=4, numbers=2, moving=4, fonts=(30, 20)),
                              lc.story_limits(self.STORY, "a"), "a")
        self.assertEqual(sorted(p["element"] for p in probs), ["body_text_px", "label", "moving", "number"])
        self.assertTrue(all(p["kind"] == "busy" for p in probs))
        label = next(p for p in probs if p["element"] == "label")
        self.assertIn("label 4 > 3", label["detail"])
        self.assertEqual(label["box"], box(0, 0, 35, 5))                      # around the four counted
        self.assertIn("20.0 px < 24", next(p for p in probs if p["element"] == "body_text_px")["detail"])

    def test_busy_at_the_limits(self):
        at = self.census(labels=3, numbers=1, moving=3, fonts=(24,))
        self.assertEqual(lc.judge_busy(at, lc.story_limits(self.STORY, "a"), "a"), [])

    def test_busy_limits_of_the_cut(self):
        c = self.census(labels=5, fonts=(20,))
        self.assertEqual(len(lc.judge_busy(c, lc.story_limits(self.STORY, "a"), "a")), 2)
        self.assertEqual(lc.judge_busy(c, lc.story_limits(self.STORY, "b"), "b"), [])

    def test_real_story_limits_give_reasons(self):
        story = lc.load_layout()["story"]
        for cut, s in story["cuts"].items():
            self.assertTrue(s.get("message"), cut)
            if "limits" in s:
                self.assertTrue(s["limits"].get("why"), cut)
                self.assertTrue(set(s["limits"]) - {"why"} <= set(lc.COUNTED) | {"moving", "body_text_px"}, cut)

    def test_markdown_lists_problems(self):
        res = {"problems": 1, "screens": [{"cut": "n05", "errors": [], "frames": [
            {"t": 2.0, "items": 3, "problems": [{"kind": "band", "element": "tag", "text": "a|b",
                                                 "box": box(0, 900, 10, 20), "detail": "d"}]}]}]}
        md = lc.markdown(res)
        self.assertIn("| n05 | 2 | band | `tag` | a\\|b |", md)


FIXTURE = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; overflow: hidden; } .f { position: absolute; font: 30px/2 sans-serif; margin: 0; }
</style></head><body>
<div class="f" data-box="a" style="left:100px;top:100px;width:200px;height:100px;background:#000;color:#fff">a</div>
<div class="f" data-box="b" style="left:250px;top:150px;width:200px;height:100px;background:#888"></div>
<div class="f" data-box="empty" style="left:250px;top:150px;width:200px;height:100px"></div>
<div class="f" style="left:100px;top:950px">字幕の帯</div>
<div class="f guide-band" style="left:300px;top:950px">guide</div>
<div class="f" style="left:100px;top:960px;opacity:0.02">ghost</div>
<div class="f" style="left:100px;top:980px;visibility:hidden">hidden</div>
<div class="f" style="left:1850px;top:400px;white-space:nowrap">edge text</div>
<div class="f" style="left:600px;top:400px;width:50px;white-space:nowrap">overflowing</div>
<div class="f" style="left:600px;top:600px;font-size:12px">tiny</div>
<div class="f" style="left:900px;top:600px;width:60px;height:60px;overflow:hidden">
  <span style="position:absolute;left:0;top:200px">clipped</span></div>
<script>window.promo = { ready: async () => {}, seek: async (t) => t, duration: 1 };</script>
</body></html>"""


# A small busy screen for the story check: 4 labels (one with a label inside
# it, counted once; one transparent, not counted), 1 graph, 2 numbers, 4
# things moving (a name whose 3 letters move is one; 2 boxes moving on their
# own; one marked data-kind="moving"; a box whose animation has ended is not
# moving), body text 22 px, and a 20 px note that is not body text.
BUSY = """<!doctype html><html><head><meta charset="utf-8"><style>
html, body { margin: 0; overflow: hidden; } .f { position: absolute; font: 30px/1.2 sans-serif; margin: 0; }
.spin { animation: turn 2s linear infinite; } @keyframes turn { to { transform: rotate(1turn); } }
.done { animation: turn 0.001s linear both; }
</style></head><body data-cut="tcut">
<div class="f" data-kind="label" style="left:100px;top:100px">札 1 <span data-kind="label">内</span></div>
<div class="f" data-kind="label" style="left:300px;top:100px">札 2</div>
<div class="f" data-kind="label" style="left:500px;top:100px">札 3</div>
<div class="f" data-kind="label" style="left:700px;top:100px">札 4</div>
<div class="f" data-kind="label" style="left:900px;top:100px;opacity:0">札 5</div>
<div class="f" data-kind="graph" style="left:100px;top:200px;width:300px;height:100px;background:#888"></div>
<div class="f" data-kind="number" style="left:500px;top:200px">99 %</div>
<div class="f" data-kind="number" style="left:700px;top:200px">16 %</div>
<div class="f" data-box="t.name" style="left:100px;top:400px"><span class="spin" style="display:inline-block">A</span><span
  class="spin" style="display:inline-block">B</span><span class="spin" style="display:inline-block">C</span></div>
<div class="f spin" style="left:400px;top:400px;width:40px;height:40px;background:#000"></div>
<div class="f spin" style="left:500px;top:400px;width:40px;height:40px;background:#000"></div>
<div class="f done" style="left:600px;top:400px;width:40px;height:40px;background:#000"></div>
<div class="f" data-kind="moving" style="left:700px;top:400px;width:40px;height:40px;background:#000"></div>
<p class="f" data-box="t.body" style="left:100px;top:600px;font-size:22px">本文 22 px</p>
<p class="f" data-box="t.src" data-kind="note" style="left:100px;top:700px;font-size:20px">出典 20 px</p>
<script>window.promo = { ready: async () => {}, duration: 1, seek: async (t) => {
  for (const a of document.getAnimations()) { a.pause(); a.currentTime = t * 1000; } return t; } };</script>
</body></html>"""


@unittest.skipUnless(HAS_PLAYWRIGHT, "needs playwright (the video Python, not the HIRAKU venv)")
class Busy(unittest.TestCase):
    def check(self, story: dict, body_cut: str = "tcut") -> dict:
        layout = copy.deepcopy(lc.load_layout())
        layout["story"] = story
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / "busy.html"
            page.write_text(BUSY.replace('data-cut="tcut"', f'data-cut="{body_cut}"'), encoding="utf-8")
            res = lc.check_screen(page, Path(d), layout, [0.5])
        return res

    def story(self, limits: dict | None = None) -> dict:
        cut = {"message": "m"}
        if limits:
            cut["limits"] = {**limits, "why": "test"}
        return {"defaults": {"label": 3, "graph": 1, "legend": 1, "number": 1, "moving": 3, "body_text_px": 24},
                "cuts": {"tcut": cut}}

    def busy(self, res: dict) -> dict:
        return {p["element"]: p["detail"] for p in res["frames"][0]["problems"] if p["kind"] == "busy"}

    def test_counts(self):
        res = self.check(self.story())
        self.assertEqual(res["story_cut"], "tcut")                      # from <body data-cut>
        self.assertEqual(res["frames"][0]["counts"],
                         {"label": 4, "graph": 1, "legend": 0, "number": 2, "moving": 4, "body_text_px": 22})

    def test_over_the_limits_is_busy(self):
        got = self.busy(self.check(self.story()))
        self.assertEqual(sorted(got), ["body_text_px", "label", "moving", "number"])
        self.assertIn("label 4 > 3 (tcut)", got["label"])
        self.assertIn("number 2 > 1", got["number"])
        self.assertIn("moving 4 > 3", got["moving"])
        self.assertIn("22.0 px < 24 px", got["body_text_px"])

    def test_within_the_limits_is_not_busy(self):
        roomy = {"defaults": {"label": 9, "graph": 9, "legend": 9, "number": 9, "moving": 9, "body_text_px": 18},
                 "cuts": {"tcut": {"message": "m"}}}
        res = self.check(roomy)
        self.assertEqual(self.busy(res), {})
        self.assertEqual(res["frames"][0]["problems"], [])            # and the fixture is clean otherwise

    def test_limits_of_the_cut_override_the_defaults(self):
        got = self.busy(self.check(self.story({"label": 4, "number": 2, "moving": 4, "body_text_px": 22})))
        self.assertEqual(got, {})
        got = self.busy(self.check(self.story({"label": 4})))
        self.assertEqual(sorted(got), ["body_text_px", "moving", "number"])

    def test_cut_not_in_the_story_is_not_counted(self):
        res = self.check(self.story(), body_cut="other")
        self.assertIsNone(res["story_cut"])
        self.assertIsNone(res["frames"][0]["counts"])
        self.assertEqual(self.busy(res), {})


@unittest.skipUnless(HAS_PLAYWRIGHT, "needs playwright (the video Python, not the HIRAKU venv)")
class InBrowser(unittest.TestCase):
    def test_fixture_finds_each_kind(self):
        layout = copy.deepcopy(lc.load_layout())
        with tempfile.TemporaryDirectory() as d:
            page = Path(d) / "fixture.html"
            page.write_text(FIXTURE, encoding="utf-8")
            res = lc.check_screen(page, Path(d), layout, [0])
        probs = res["frames"][0]["problems"]
        found = sorted((p["kind"], p["text"] or p["element"]) for p in probs)
        self.assertEqual(found, [
            ("band", "字幕の帯"),
            ("outside", "edge text"),
            ("overflow", "overflowing"),
            ("overlap", "a × b"),
            ("small", "tiny"),
        ])

    def test_n05_tiles_sit_on_grid_cells(self):
        from promo import export

        cap = export.Capture(lc.SCREENS / "n05.html", scale=1.0)
        try:
            cap.page.evaluate("t => window.__export.seek(t)", 4.5)
            got = cap.page.evaluate("""() => {
              const L = window.promoLayout;
              const cells = L.gridCells(L.cutRules.grid);
              const tiles = [...document.querySelectorAll('.tile')].map((el) => {
                const r = el.getBoundingClientRect();
                return { x: r.left, y: r.top, w: r.width, h: r.height };
              });
              const keys = [...document.querySelectorAll('.tile')].map((el) => el.dataset.box.split('.')[2]);
              return { cells, tiles, keys };
            }""")
        finally:
            cap.close()
        rules = lc.load_layout()
        grid = rules["n05"]["grid"]
        cells = got["cells"]
        self.assertEqual(len(cells), grid["cols"] * grid["rows"])
        self.assertEqual(len(got["tiles"]), len(cells))
        for cell, tile in zip(cells, got["tiles"]):
            for k in "xywh":
                self.assertAlmostEqual(cell[k], tile[k], places=3)
        for c in cells:
            self.assertEqual(c["x"], grid["x"] + c["col"] * (grid["tile_w"] + grid["gap_x"]))
            self.assertEqual(c["y"], grid["y"] + c["row"] * (grid["tile_h"] + grid["gap_y"]))
        self.assertEqual(cells[-1]["x"] + cells[-1]["w"], rules["stage"]["w"] - rules["safe"]["x"])
        self.assertEqual(got["keys"], rules["n05"]["tests"])
        for i, a in enumerate(cells):
            self.assertFalse(lc.in_band(a, rules["subtitle_band"]["y"]))
            for b in cells[i + 1:]:
                self.assertIsNone(lc.intersection(a, b))

    def test_n05_effects_follow_the_rules(self):
        # the landing glows sit on the tiles' cells, the mesh's last element is
        # gone in time, and the seeded parts come out the same on every load
        from promo import export

        script = """() => {
          const u = document.querySelector('.stage').getBoundingClientRect().width / 1920;
          const box = (el) => ({ x: el.offsetLeft / u, y: el.offsetTop / u, w: el.offsetWidth / u, h: el.offsetHeight / u });
          const ks = [...document.querySelectorAll('.cells polygon')]
            .map((p) => parseFloat(p.style.getPropertyValue('--k')));
          return {
            land: [...document.querySelectorAll('.land')].map(box),
            ghosts: document.querySelectorAll('.ghost').length,
            cells: window.promoLayout.gridCells(window.promoLayout.cutRules.grid),
            ks: [Math.min(...ks), Math.max(...ks)], tris: ks.length,
            wire: document.querySelectorAll('.wire polygon').length,
            rays: document.querySelector('svg.rays').innerHTML,
            dust: document.querySelector('svg.dust').innerHTML,
            meshed: document.querySelector('.hero').classList.contains('meshed'),
          };
        }"""
        got = []
        for _ in range(2):
            cap = export.Capture(lc.SCREENS / "n05.html", scale=0.25, layer="fg")
            try:
                cap.page.evaluate("t => window.__export.seek(t)", 4.5)
                got.append(cap.page.evaluate(script))
            finally:
                cap.close()
        a, b = got
        n = lc.load_layout()["n05"]
        d, t, m = n["door"], n["times"], n["hero_mesh"]
        self.assertTrue(a["meshed"])
        self.assertEqual(len(a["land"]), len(a["cells"]))
        self.assertEqual(a["ghosts"], len(a["cells"]) * n["tile_fx"]["ghosts"])
        for land, cell in zip(a["land"], a["cells"]):
            for k in "xywh":
                self.assertAlmostEqual(land[k], cell[k], delta=0.6)
        self.assertGreater(a["tris"], 100)
        self.assertEqual(a["wire"], a["tris"])
        self.assertGreaterEqual(a["ks"][0], t["mesh_out_s"] - 1e-3)
        swing = t["door_open_s"] + d["crack_dur_s"] + d["hold_dur_s"]
        self.assertLessEqual(a["ks"][1] + m["el_dur_s"], swing + d["hero_fade_dur_s"] + 1e-3)
        self.assertEqual(a["rays"].count("<polygon"), n["rays"]["count"])
        self.assertEqual(a["dust"].count("<circle"), n["dust"]["count"])
        self.assertEqual(a["rays"], b["rays"])
        self.assertEqual(a["dust"], b["dust"])
        self.assertEqual(a["ks"], b["ks"])

    def test_variable_forms(self):
        # layout.js turns each kind of value into its CSS form
        from promo import export

        cap = export.Capture(lc.SCREENS / "n05.html", scale=0.25)
        try:
            got = cap.page.evaluate("""() => {
              const L = window.promoLayout, css = getComputedStyle(document.documentElement);
              const v = (k) => css.getPropertyValue(k).trim();
              return { motion: L.motion(L.cutRules, "", {}), vars: L.vars("n05"),
                       angle: v("--door-angle"), persp: v("--door-perspective-px"),
                       open: v("--t-door-open") };
            }""")
        finally:
            cap.close()
        d = lc.load_layout()["n05"]["door"]
        m = got["motion"]
        self.assertEqual(m["door-angle"], f"{d['angle_deg']}deg")
        self.assertEqual(m["door-open-dur"], f"{d['open_dur_s']}s")
        self.assertEqual(m["door-ease"], "cubic-bezier(" + ", ".join(str(x) for x in d["ease"]) + ")")
        self.assertEqual(m["hero-motion-push-scale"], "1.04")
        self.assertEqual(m["door-crack"], f"{d['crack_deg']}deg")
        self.assertEqual(m["door-hold-dur"], f"{d['hold_dur_s']}s")
        self.assertEqual(m["door-crack-ease"], "cubic-bezier(" + ", ".join(str(x) for x in d["crack_ease"]) + ")")
        self.assertEqual(m["door-hero-fade-scale"], str(d["hero_fade_scale"]))
        self.assertNotIn("door-angle-deg", got["vars"])           # an angle is not a length
        self.assertEqual(got["vars"]["door-perspective-px"], d["perspective_px"])
        self.assertEqual(got["angle"], f"{d['angle_deg']}deg")
        self.assertIn(str(d["perspective_px"]), got["persp"])
        self.assertEqual(got["open"], f"{lc.load_layout()['n05']['times']['door_open_s']}s")

    def test_n08b_bars_to_scale_and_inside(self):
        from promo import export

        cap = export.Capture(lc.SCREENS / "n08b.html", scale=1.0)
        try:
            cap.page.evaluate("t => window.__export.seek(t)", 9.8)
            got = cap.page.evaluate("""() => {
              const r = (s) => { const b = document.querySelector(`[data-box="${s}"]`).getBoundingClientRect();
                                 return { x: b.left, w: b.width, right: b.right, h: b.height }; };
              return { small: r("n08b.bars.small.bar"), large: r("n08b.bars.large.bar"),
                       value: r("n08b.bars.large.value"), bars: r("n08b.bars"),
                       times: window.promoLayout.times("n08b") };
            }""")
        finally:
            cap.close()
        rules = lc.load_layout()["n08b"]
        k = rules["bars"]["px_per_pct"]
        self.assertAlmostEqual(got["small"]["w"], 16.0 * k, places=2)
        self.assertAlmostEqual(got["large"]["w"], 67.9 * k, places=2)
        self.assertLessEqual(got["value"]["right"], got["bars"]["right"])
        self.assertLess(got["value"]["h"], 1.5 * rules["bars"]["value_size_px"])  # one line
        self.assertEqual(got["times"]["t-right"], 4.8)

    def test_n10_figures_on_screen(self):
        from promo import export

        cap = export.Capture(lc.SCREENS / "n10.html", scale=0.5)
        try:
            cap.page.evaluate("t => window.__export.seek(t)", 8.3)
            got = cap.page.evaluate("""() => {
              const text = (s) => {
                const el = document.querySelector(`[data-box="${s}"] .in`);
                const parts = [...el.querySelectorAll(".v, .note")].map((e) => e.textContent.trim());
                return parts.length ? parts.join(" | ") : el.textContent.trim();
              };
              const out = {};
              for (const k of ["step", "tilt", "stress"])
                for (const c of ["claude", "hand", "cae", "diff", "tick"]) out[`${k}.${c}`] = text(`n10.row.${k}.${c}`);
              return out;
            }""")
            early = cap.page.evaluate("""async () => { await window.__export.seek(3.3);
              return [...document.querySelectorAll('.tick .in')].map((e) => getComputedStyle(e).opacity); }""")
        finally:
            cap.close()
        self.assertEqual(got["step.claude"], "約 1,090N | 3 回とも")
        self.assertEqual(got["tilt.claude"], "約 39°・38°・39° | 3 回の答え")
        self.assertEqual(got["stress.claude"], "約 60MPa | 数字を出したのは 3 回のうち 1 回")
        self.assertEqual(got["stress.hand"], "40.0MPa | 単純はり | 77.09MPa | つなぎ目、安全側")
        self.assertEqual(got["step.cae"], "1,103.7N | K1 の解析")
        # the differences shown are the ones computed from DATA
        rows = {r["key"]: r for r in n10_data()["rows"]}
        for k, want in (("step", "+1.3%"), ("tilt", "−1.9% | 3 回で最大"), ("stress", "+1.9%")):
            self.assertEqual(got[f"{k}.diff"], want)
            worst = max(n10_diffs(rows[k]), key=abs)
            self.assertIn(f"{abs(worst):.1f}", want)
            self.assertEqual(got[f"{k}.tick"], "✓")
        # no tick before sentence 2
        self.assertEqual(early, ["0", "0", "0"])

    def test_n08_pile_and_seeded_parts(self):
        # every dialog is drawn where layout.json puts it, in its order, and the
        # seeded mesh and book stack come out the same on every load
        from promo import export

        script = """() => {
          const u = document.querySelector('.stage').getBoundingClientRect().width / 1920;
          const wins = [...document.querySelectorAll('.win')].map((el) => ({
            key: el.dataset.box.split('.')[2], x: el.offsetLeft / u, y: el.offsetTop / u,
            w: el.offsetWidth / u, h: el.offsetHeight / u, z: Number(getComputedStyle(el).zIndex) }));
          return { wins, mesh: document.querySelector('svg.mesh').innerHTML,
                   bad: document.querySelectorAll('svg.mesh .bad').length,
                   books: document.querySelector('.books').innerHTML,
                   dim: getComputedStyle(document.querySelector('.dim')).opacity };
        }"""
        got = []
        for _ in range(2):
            cap = export.Capture(lc.SCREENS / "n08.html", scale=0.25)
            try:
                cap.page.evaluate("t => window.__export.seek(t)", 6.4)
                got.append(cap.page.evaluate(script))
            finally:
                cap.close()
        a, b = got
        n = lc.load_layout()["n08"]
        keys = [k for k in n["win"] if not k.startswith("_")]
        self.assertEqual([w["key"] for w in a["wins"]], [k.replace("_", "-") for k in keys])
        for w, k in zip(a["wins"], keys):
            for c in "xywhz":
                self.assertAlmostEqual(w[c], n["win"][k][c], delta=0.6)
        self.assertGreater(a["bad"], 0)
        self.assertAlmostEqual(float(a["dim"]), n["dim"]["peak_alpha"], places=3)
        self.assertEqual(a["mesh"], b["mesh"])
        self.assertEqual(a["books"], b["books"])

    def test_n10_has_no_problems(self):
        layout = lc.load_layout()
        with tempfile.TemporaryDirectory() as d:
            res = lc.check_screen(lc.SCREENS / "n10.html", Path(d), layout, None)
        probs = [p for f in res["frames"] for p in f["problems"]]
        self.assertEqual(probs, [], json.dumps(probs, ensure_ascii=False))
        self.assertEqual(res["errors"], [])

    def test_cuts_have_no_problems(self):
        layout = lc.load_layout()
        with tempfile.TemporaryDirectory() as d:
            for cut in lc.CUTS:
                res = lc.check_screen(lc.SCREENS / f"{cut}.html", Path(d), layout, None)
                probs = [p for f in res["frames"] for p in f["problems"]]
                self.assertEqual(probs, [], f"{cut}: {json.dumps(probs, ensure_ascii=False)}")
                self.assertEqual(res["errors"], [])

    def test_n05_points_land_on_the_vehicle(self):
        # every point lands on a set cell of the mask, starts on the stage
        # above the band, and the cloud comes out the same on every load
        from promo import export

        script = """() => {
          const pts = [...document.querySelectorAll('svg.pts circle')].map((c) => ({
            x: +c.getAttribute('cx'), y: +c.getAttribute('cy'), r: +c.getAttribute('r'),
            px: parseFloat(c.style.getPropertyValue('--px')), py: parseFloat(c.style.getPropertyValue('--py')),
            pd: parseFloat(c.style.getPropertyValue('--pd')) }));
          return { pts, html: document.querySelector('svg.pts').innerHTML,
                   pic: getComputedStyle(document.querySelector('.hero .pic')).opacity };
        }"""
        got = []
        for t in (0.0, 0.5):
            cap = export.Capture(lc.SCREENS / "n05.html", scale=0.25, layer="fg")
            try:
                cap.page.evaluate("t => window.__export.seek(t)", t)
                got.append(cap.page.evaluate(script))
            finally:
                cap.close()
        a, b = got
        r = lc.load_layout()
        n = r["n05"]
        p, k = n["hero_points"], n["hero_points"]["mask"]
        bits = [bin(int(h, 16))[2:].zfill(4 * len(h)) for h in k["rows_hex"]]
        self.assertGreater(len(a["pts"]), 200)
        self.assertEqual(a["html"], b["html"])
        self.assertEqual(float(a["pic"]), 0.0)          # the picture is not there at 0 s
        self.assertEqual(float(b["pic"]), 1.0)          # and in by 0.5 s
        m, band = p["margin_px"], r["subtitle_band"]["y"]
        for q in a["pts"]:
            self.assertEqual(bits[int(q["y"] // k["cell_px"])][int(q["x"] // k["cell_px"])], "1", q)
            sx, sy = n["hero"]["x"] + q["x"] + q["px"], n["hero"]["y"] + q["y"] + q["py"]
            self.assertTrue(m - 0.01 <= sx <= r["stage"]["w"] - m + 0.01 and m - 0.01 <= sy <= band - m + 0.01, q)
            self.assertTrue(0 <= q["pd"] <= p["stagger_s"] + 1e-3)
        self.assertEqual({q["r"] for q in a["pts"]}, {p["dot_px"], p["node_dot_px"]})

    def test_n18_motes_and_credit(self):
        from promo import export

        script = """() => ({
          motes: [...document.querySelectorAll('svg.gather circle')].map((c) => ({
            x: +c.getAttribute('cx'), y: +c.getAttribute('cy'),
            gx: parseFloat(c.style.getPropertyValue('--gx')), gy: parseFloat(c.style.getPropertyValue('--gy')),
            o: getComputedStyle(c).opacity })),
          credit: [document.querySelector('.credit').textContent, getComputedStyle(document.querySelector('.credit')).opacity],
          door: getComputedStyle(document.querySelector('.door')).visibility,
          ground: getComputedStyle(document.querySelector('.stage')).backgroundColor,
          paper: getComputedStyle(document.documentElement).getPropertyValue('--paper').trim(),
        })"""
        got = {}
        cap = export.Capture(lc.SCREENS / "n18.html", scale=0.25)
        try:
            for t in (0.0, 0.5, 1.5, 7.0, 8.9):
                cap.page.evaluate("t => window.__export.seek(t)", t)
                got[t] = cap.page.evaluate(script)
        finally:
            cap.close()
        r = lc.load_layout()
        n = r["n18"]
        g = n["gather"]
        self.assertEqual(len(got[0.0]["motes"]), g["count"])
        for q in got[0.0]["motes"]:
            self.assertAlmostEqual(q["x"], r["stage"]["w"] / 2, delta=2.01)      # they land on the line
            self.assertTrue(n["light"]["y"] <= q["y"] <= n["light"]["y"] + n["light"]["h"])
            sy = q["y"] + q["gy"]
            self.assertTrue(g["margin_px"] - 0.01 <= sy <= r["subtitle_band"]["y"] - g["margin_px"] + 0.01)
            self.assertAlmostEqual(float(q["o"]), g["start_alpha"], places=3)    # seen from the first frame
        self.assertTrue(all(float(q["o"]) == 0 for q in got[0.5]["motes"]))       # all gone into the line
        self.assertEqual(got[7.0]["credit"][0], "HIRAKU Powered by Claude")
        self.assertAlmostEqual(float(got[7.0]["credit"][1]), n["credit"]["alpha"], places=3)
        self.assertEqual(float(got[0.5]["credit"][1]), 0.0)
        # dark (the shut door) at the start, gone by 1.5 s; the ground is N00's paper
        self.assertEqual(got[0.5]["door"], "visible")
        self.assertEqual(got[1.5]["door"], "hidden")
        self.assertEqual(got[8.9]["door"], "hidden")
        self.assertEqual(got[8.9]["paper"].lower(), "#f4f1ea")
        self.assertEqual(got[8.9]["ground"], "rgb(244, 241, 234)")
        self.assertAlmostEqual(float(got[8.9]["credit"][1]), n["credit"]["alpha"], places=3)

    def pets_over_words(self, cut: str, words: list[str], t0: float, t1: float, fps: int = 15):
        """Seeks t0..t1 at fps and, at each frame, finds the pets in front whose
        drawn pixels (alpha > 40) lie over the words: the letters' ink (from
        12 px under the ink top, where the feet stand: 3 canvas px at scale
        0.25, the size of one canvas pixel being 4 design px) and the text boxes of the
        given selectors. Also each pet's drawn box (design px) and canvas box.
        Returns [(t, {pet: [what, ...]}, {pet: drawn box}, {pet: canvas box})]."""
        from promo import export

        seats = "N00_SEATS" if cut == "n00" else "N18_SEATS"
        js = """([seats, sels]) => {
          const stage = document.querySelector('.stage').getBoundingClientRect(), u = stage.width / 1920;
          const d = (x0, y0, x1, y1) => ({ x0: (x0 - stage.left) / u, y0: (y0 - stage.top) / u,
                                          x1: (x1 - stage.left) / u, y1: (y1 - stage.top) / u });
          // a letter of N00 sinks and squashes a little when a pet lands on it (n00.play.land) and the
          // pet rides it: the ink top where it is now (the page's place of a seat on it)
          const S = window[seats], t = window.promo.t;
          const mv = (i) => S.place({ seat: i, at: 0.5 }, t).y - S.place({ seat: i, at: 0.5 }, -1).y;
          const words = window[seats].INK.map((o, i) => ({ what: 'letter ' + i, x0: o.x0, y0: o.top + 12 + mv(i), x1: o.x1, y1: o.bottom + mv(i) }));
          for (const s of sels) for (const e of document.querySelectorAll(s)) {
            if (parseFloat(getComputedStyle(e).opacity) < 0.05) continue;
            const rg = document.createRange(); rg.selectNodeContents(e);
            const b = rg.getBoundingClientRect();
            if (b.width > 0) words.push({ what: s + ' ' + e.textContent.trim().slice(0, 8), ...d(b.left, b.top, b.right, b.bottom) });
          }
          const over = {}, drawn = {}, boxes = {};
          for (const c of document.querySelectorAll('canvas[data-pet]')) {
            if (parseFloat(c.style.opacity || '1') < 0.05) continue;
            const r = c.getBoundingClientRect();
            boxes[c.dataset.pet] = d(r.left, r.top, r.right, r.bottom);
            const px = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let x0 = 1e9, y0 = 1e9, x1 = -1, y1 = -1;
            for (let y = 0; y < c.height; y++) for (let x = 0; x < c.width; x++)
              if (px[(y * c.width + x) * 4 + 3] > 40) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
            if (x1 < 0) continue;
            const sx = r.width / c.width, sy = r.height / c.height;
            const b = d(r.left + x0 * sx, r.top + y0 * sy, r.left + (x1 + 1) * sx, r.top + (y1 + 1) * sy);
            drawn[c.dataset.pet] = b;
            if (c.dataset.layer === 'behind') continue;          // under the words: never covers them
            // over a word: at least 4 drawn canvas px (64 design px at scale 0.25) inside its box
            const hit = words.filter((w) => {
              if (!(b.x0 < w.x1 && b.x1 > w.x0 && b.y0 < w.y1 && b.y1 > w.y0)) return false;
              const cx0 = Math.max(0, Math.floor((w.x0 * u + stage.left - r.left) / sx)), cx1 = Math.min(c.width, Math.ceil((w.x1 * u + stage.left - r.left) / sx));
              const cy0 = Math.max(0, Math.floor((w.y0 * u + stage.top - r.top) / sy)), cy1 = Math.min(c.height, Math.ceil((w.y1 * u + stage.top - r.top) / sy));
              let k = 0;
              for (let y = cy0; y < cy1; y++) for (let x = cx0; x < cx1; x++) if (px[(y * c.width + x) * 4 + 3] > 40) k++;
              return k >= 4;
            }).map((w) => w.what);
            if (hit.length) over[c.dataset.pet] = hit;
          }
          return [over, drawn, boxes];
        }"""
        out = []
        cap = export.Capture(lc.SCREENS / f"{cut}.html", scale=0.25)
        try:
            for i in range(int(round(t0 * fps)), int(round(t1 * fps)) + 1):
                cap.page.evaluate("t => window.__export.seek(t)", i / fps)
                over, drawn, boxes = cap.page.evaluate(js, [seats, words])
                out.append((i / fps, over, drawn, boxes))
        finally:
            cap.close()
        return out

    def check_pets_on_screen(self, frames, fps: int):
        """Every drawn pet inside the safe area, and no pet box in the subtitle band."""
        r = lc.load_layout()
        s, band = r["safe"], r["subtitle_band"]["y"]
        for t, over, drawn, boxes in frames:
            for pet, b in drawn.items():
                self.assertGreaterEqual(b["x0"], s["x"] - 0.5, (t, pet, b))
                self.assertLessEqual(b["x1"], s["x"] + s["w"] + 0.5, (t, pet, b))
                self.assertGreaterEqual(b["y0"], s["y"] - 0.5, (t, pet, b))
                self.assertLessEqual(b["y1"], band + 0.5, (t, pet, b))
            for pet, b in boxes.items():
                self.assertLessEqual(b["y1"], band + 0.5, (t, pet, b))
                self.assertGreaterEqual(b["x0"], s["x"] - 0.5, (t, pet, b))
                self.assertLessEqual(b["x1"], s["x"] + s["w"] + 0.5, (t, pet, b))

    def over_time(self, frames, fps, skip=()):
        """Seconds each pet spends over the words (frames / fps)."""
        k = {}
        for t, over, _, _ in frames:
            for pet, what in over.items():
                if all(w.split(" ")[0] in skip for w in what):
                    continue
                k[pet] = k.get(pet, 0) + 1
        return {pet: n / fps for pet, n in k.items()}

    def test_n00_pets_about_the_screen(self):
        # the name and the tagline: at most 0.2 s under any pet (a pet jumping onto a
        # letter passes over them); the chips' words never; nobody over the name or
        # the tagline while the voice says the name; all inside the safe area, above the band
        fps = 15
        frames = self.pets_over_words("n00", [".tagline", ".chip"], 0.0, 7.6, fps)
        self.check_pets_on_screen(frames, fps)
        for t, over, _, _ in frames:
            for pet, what in over.items():
                self.assertFalse([w for w in what if w.startswith(".chip")], (t, pet, what))
                if 0.3 <= t <= 1.3:
                    self.fail((t, pet, what))
        for pet, s in self.over_time(frames, fps).items():
            self.assertLessEqual(s, 0.2 + 1e-9, pet)
        # where they are at the end: about the name (on its letters, behind the H, at its
        # foot left and right), none down by the chips: every pet's middle above the
        # line under the name, and spread across the name's width
        n = lc.load_layout()["n00"]
        _, _, drawn, _ = frames[-1]
        self.assertEqual(len(drawn), 10)
        xs = sorted((b["x0"] + b["x1"]) / 2 for b in drawn.values())
        for pet, b in drawn.items():
            self.assertLess((b["y0"] + b["y1"]) / 2, n["rule"]["y"], pet)
            self.assertGreater((b["y0"] + b["y1"]) / 2, n["name"]["y"] - 120, pet)
        self.assertGreater(xs[-1] - xs[0], 1000)

    def test_n18_pets_about_the_words(self):
        # the name, the tag, the URL and the credit: at most 0.2 s under any pet; all
        # inside the safe area, above the band; at the end gathered about the words
        fps = 15
        frames = self.pets_over_words("n18", [".tag", ".url", ".credit"], 1.4, 8.6, fps)
        self.check_pets_on_screen(frames, fps)
        for pet, s in self.over_time(frames, fps).items():
            self.assertLessEqual(s, 0.2 + 1e-9, pet)
        r = lc.load_layout()
        n = r["n18"]
        _, _, drawn, _ = frames[-1]
        self.assertEqual(len(drawn), 10)
        # every pet within 300 px of the block of words (name to credit, the vehicle above)
        x0, x1 = 545 - 300, 1377 + 300
        for pet, b in drawn.items():
            cx, cy = (b["x0"] + b["x1"]) / 2, (b["y0"] + b["y1"]) / 2
            self.assertTrue(x0 <= cx <= x1, (pet, cx))
            self.assertTrue(n["vehicle"]["y"] <= cy <= r["subtitle_band"]["y"], (pet, cy))

    def test_n18_pets_hold_still_at_the_end(self):
        from promo import export

        cap = export.Capture(lc.SCREENS / "n18.html", scale=0.5)
        try:
            seen, pix = {}, {}
            for t in (1.2, 6.0, 8.2, 8.5, 8.9, 8.2):
                cap.page.evaluate("t => window.__export.seek(t)", t)
                seen[t] = cap.page.evaluate("""() => ({
                  kinds: [...document.querySelectorAll('canvas.pet')].map((c) => c.dataset.kind || ''),
                  op: [...document.querySelectorAll('canvas.pet')].map((c) => c.style.opacity) })""")
                pix.setdefault(t, []).append(cap.page.evaluate(
                    "() => [...document.querySelectorAll('canvas.pet')].map((c) => c.toDataURL() + c.style.left + c.style.top)"))
        finally:
            cap.close()
        self.assertEqual(seen[1.2]["op"], ["0"] * 10)                         # behind the door: no pets yet
        self.assertEqual(seen[6.0]["kinds"], ["moving"] * 10)
        for t in (8.2, 8.5, 8.9):
            self.assertEqual(seen[t]["kinds"], [""] * 10, t)
            self.assertEqual(seen[t]["op"], [""] * 10, t)
        self.assertEqual(pix[8.2][0], pix[8.2][1])                            # the same t, the same picture
        self.assertEqual(pix[8.2][0], pix[8.9][0])                            # all still before 8.5 s

    def test_walks_do_not_slide(self):
        # the foot on the ground keeps its place on the screen while the body walks
        # over it (pet3d anim.route walk: r.plant), on every walk of N00 and N18
        from promo import export

        js = """([seats, list]) => {
          const S = window[seats], A = window.HirakuPets.anim, out = [];
          for (const p of eval(list)) for (const b of p.beats) {
            if (b.do !== 'walk') continue;
            let last = null, worst = 0, steps = 0;
            for (let i = 1; i < 240; i++) {
              const t = b.t + (b.dur_s * i) / 240;
              const r = A.route(t, p.start, p.beats, { place: S.place, yawTo: S.yawTo });
              if (!r.plant) continue;
              if (last && last.k === r.plant.k) worst = Math.max(worst, Math.hypot(r.plant.x - last.x0, r.plant.y - last.y0));
              if (!last || last.k !== r.plant.k) { last = { k: r.plant.k, x0: r.plant.x, y0: r.plant.y }; steps++; }
            }
            out.push([p.id, b.t, worst, steps, b.dur_s]);
          }
          return out;
        }"""
        got = []
        for cut, seats, lst in (("n00", "N00_SEATS", "window.N00_PETS.list"), ("n18", "N18_SEATS", "window.N18_PETS")):
            cap = export.Capture(lc.SCREENS / f"{cut}.html", scale=0.25)
            try:
                got += cap.page.evaluate(js, [seats, lst])
            finally:
                cap.close()
        self.assertGreaterEqual(len(got), 8)
        for pet, t, worst, steps, dur in got:
            self.assertLess(worst, 0.05, (pet, t))                            # design px
            self.assertGreaterEqual(steps, 3, (pet, t))
            self.assertTrue(1.5 <= steps / dur <= 7.0, (pet, t, steps / dur))   # a walking pace

    def test_n00_letters_play_then_stand_still(self):
        from promo import export

        n = lc.load_layout()["n00"]
        sh = n["play"]["shake"]
        aero = [b for b in n["cast"]["cae.aero"]["beats"] if b["do"] == "hop"][-1]
        land = aero["t"] + hop_times({**n["play"]["hop"], **aero})[0]
        script = """() => [...document.querySelectorAll('.name b')].map((b) => b.style.transform)"""
        got = {}
        cap = export.Capture(lc.SCREENS / "n00.html", scale=0.25)
        try:
            for t in (0.3, 0.6, 1.2, land + 0.1, 5.65, 7.5, 8.5):
                cap.page.evaluate("t => window.__export.seek(t)", t)
                got[t] = cap.page.evaluate(script)
            cap.page.evaluate("t => window.__export.seek(t)", 5.65)
            k = cap.page.evaluate(f"() => window.N00_SEATS.letterMove({sh['letter']}, 5.65)")
        finally:
            cap.close()
        for t in (0.3, 0.6, 1.2, 7.5, 8.5):                       # in line while the name is said, and at the end
            self.assertEqual(got[t], [""] * 6, t)
        self.assertNotEqual(got[land + 0.1][aero["to"]["seat"]], "")   # aero lands on its letter
        self.assertNotEqual(got[5.65][sh["letter"]], "")          # K out of line, before the push
        self.assertAlmostEqual(k["dx"], sh["dx_px"], places=3)
        self.assertAlmostEqual(k["rot"], sh["tilt_deg"] * 3.141592653589793 / 180, places=4)

    def test_n00_pets_still_by_7_4(self):
        from promo import export

        n = lc.load_layout()["n00"]
        pix = """() => [...document.querySelectorAll('canvas[data-pet]')].map((c) => c.toDataURL() + c.style.left + c.style.top + c.style.opacity)"""
        cap = export.Capture(lc.SCREENS / "n00.html", scale=0.25)
        try:
            got = {}
            for t in (2.45, 7.4, 8.5, 2.45):
                cap.page.evaluate("t => window.__export.seek(t)", t)
                got.setdefault(t, []).append(cap.page.evaluate(pix))
            cap.page.evaluate("t => window.__export.seek(t)", 7.4)
            kinds = cap.page.evaluate("() => [...document.querySelectorAll('canvas[data-pet]')].map((c) => c.dataset.kind || '')")
        finally:
            cap.close()
        self.assertEqual(got[2.45][0], got[2.45][1])                          # the same t, the same picture
        self.assertEqual(got[7.4][0], got[8.5][0])                            # all still by 7.4 s
        self.assertEqual(kinds, [""] * 10)
        self.assertLessEqual(n["play"]["stop"]["still_s"], 7.4)

    def test_labels_come_and_go(self):
        from promo import export

        r = lc.load_layout()
        lab = r["labels"]
        t_in = lab["times"]["in_s"]
        for cut in ("n01", "n02", "n03", "n04"):
            d = r[cut]["duration_s"]
            gone = d - lab["times"]["out_before_end_s"]
            cap = export.Capture(lc.SCREENS / f"labels_{cut}.html", scale=0.25, layer="fg")
            try:
                self.assertEqual(cap.duration, d)
                seen = {}
                for t in (0.0, t_in + lab["in_dur_s"] + 0.05, gone - lab["out_dur_s"] - 0.05, gone + 0.01, d - 0.01):
                    cap.page.evaluate("t => window.__export.seek(t)", t)
                    seen[t] = cap.page.evaluate("""() => [getComputedStyle(document.querySelector('.label')).opacity,
                        document.querySelector('.label-num').textContent, document.querySelector('.label-text').textContent]""")
            finally:
                cap.close()
            ops = [float(v[0]) for v in seen.values()]
            self.assertEqual(ops, [0.0, 1.0, 1.0, 0.0, 0.0], cut)
            first = next(iter(seen.values()))
            self.assertEqual(first[1:], [lab["cuts"][cut]["num"], lab["cuts"][cut]["text"]])

    def test_labels_page_takes_the_cut_from_the_url(self):
        from playwright.sync_api import sync_playwright

        r = lc.load_layout()
        url = (lc.SCREENS / "labels.html").resolve().as_uri() + "?cut=n03&t=1&layer=fg"
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 480, "height": 270})
                page.goto(url)
                page.wait_for_function("window.promo !== undefined")
                got = page.evaluate("""() => [window.promo.duration, document.querySelector('.label-text').textContent,
                    document.documentElement.classList.contains('layer-fg')]""")
            finally:
                browser.close()
        self.assertEqual(got, [r["n03"]["duration_s"], r["labels"]["cuts"]["n03"]["text"], True])


if __name__ == "__main__":
    unittest.main()

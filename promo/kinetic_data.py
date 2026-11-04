"""Data and rules for the kinetic type samples of N08b and N17b.

Writes two generated files the sample pages load with <script src> (they are
opened from file://):

- promo/screens/n08b_kt.data.js (``window.KT_DATA``): the two figures of N08b,
  read from the DATA block of promo/screens/n08b.html (the sourced values and
  their sources; nothing is typed in here), and the shares worked out from them.
- promo/screens/n17b_kt.data.js (``window.KT_DATA``): from the git record of one
  ref (main by default), the lines of code written (counted as N17b counts them,
  promo/n17b_data.py), the commits, the days, and the subjects of a spread of
  commits for the corridor (English, cut short); and the depth of one run for
  the four cards (n17b_data.depth: from the run's events.jsonl with --run, else
  the depth held by promo/screens/n17b.data.js, which n17b_data.py counted from
  its run).

Both files also carry the page's rules (RULES below: placement in 1920x1080
design px, times in seconds from the cut's start, camera). These two pages are
samples; their rules live here and not in layout.json.

    py promo/kinetic_data.py                 # both, git counted on main
    py promo/kinetic_data.py --ref HEAD      # count another ref
    py promo/kinetic_data.py --run <runs/<run_id> folder>   # count the depth from a run

Run it again after a merge into main to bring N17b's figures up to date.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promo import n17b_data  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCREENS = Path(__file__).resolve().parent / "screens"
N08B_PAGE = SCREENS / "n08b.html"
OUT_N08B = SCREENS / "n08b_kt.data.js"
OUT_N17B = SCREENS / "n17b_kt.data.js"

# The voice of each cut (recorded cues, seconds from the cut's start):
# N08b sentence 1 0.30-4.53 s, sentence 2 4.88-9.53 s; N17b sentence 1
# 0.30-1.93 s, sentence 2 2.28-5.01 s. The times below follow them.
RULES = {
    "n08b_kt": {
        "duration_s": 10.0,
        "voice": [[0.30, 4.53], [4.88, 9.53]],
        "view": {"perspective_px": 1500},
        "floor": {"w": 2400, "d": 3200, "near_px": 500, "cell_px": 80, "alpha": 0.18,
                  "_note": "a faint grid on the floor the figures stand on (the paper grid of N00, light on the night): w wide, d deep, its near edge near_px in front of the figures (kept in front of the camera at every angle)"},
        "num": {
            "cy": 380, "size_px": 360, "unit_em": 0.46, "layers": 40, "depth_px": 150,
            "_note": "the figure, extruded: layers copies of the text one behind another over depth_px; "
                     "centred on x 960, its middle at cy (before the camera)",
        },
        "cap": {"y": 676, "h": 84, "size_px": 56, "gap_px": 22, "mid_scale": 1.3},
        "src": {"x": 96, "y": 836, "w": 1728, "h": 64, "size_px": 20,
                "_note": "two lines at most (line height 1.6); bottom 900, above the subtitle band"},
        "beats": [
            {
                "key": "sme",
                "num_in_s": 0.25, "count_s": [0.45, 1.75], "out_s": [4.45, 4.95],
                "cam_from": {"ry_deg": -40, "rx_deg": 16, "z_px": -900},
                "cam_to": {"ry_deg": -16, "rx_deg": 8, "z_px": 0},
                "cam_s": [0.0, 4.45],
                "words_s": [1.85, 2.15, 2.85],
                "src_s": 3.2,
            },
            {
                "key": "small",
                "num_in_s": 7.35, "count_s": [7.55, 8.45], "out_s": None,
                "cam_from": {"ry_deg": 36, "rx_deg": 22, "z_px": -600},
                "cam_to": {"ry_deg": 14, "rx_deg": 8, "z_px": 0},
                "cam_s": [7.35, 9.6],
                "words_s": [5.15, 6.2],
                "src_s": 8.6,
            },
        ],
        "word_in_s": 0.4,
        "count_steps": 40,
    },
    "n17b_kt": {
        "duration_s": 5.8,
        "voice": [[0.30, 1.93], [2.28, 5.01]],
        "view": {"perspective_px": 900, "eye_y": 420},
        "corridor": {
            "half_w_px": 520, "open_w_px": 700, "rows_y_px": [-360, 300, -280, 360], "z0_px": -600,
            "pitch_px": 165, "size_px": 40, "face_deg": 14, "travel_px": 6200, "fade_px": 70, "fig_inset": 0.22, "min_seen_px": 13, "min_seen_fade_px": 5, "edge_px": 6,
            "far_px": 3600, "near_px": 120, "alpha": 0.9,
            "rails_y_px": [-400, 380], "rail_seg_px": 600, "rail_len_px": 11400, "rail_px": 2, "rail_alpha": 0.22,
            "rail_far_px": 9500,
            "_note": "commit subjects along two walls (the inner end of each at x = -half_w_px and +half_w_px "
                     "from the middle, rows at rows_y_px from the eye line eye_y), oldest at z0_px, one more "
                     "every pitch_px into the distance, alternating left and right. Each subject faces the "
                     "camera, turned only face_deg about its inner end (its outer end nearer the camera); the "
                     "depth shows in the row going into the distance. The camera goes travel_px forward over "
                     "times.cam_s. With sentence 2 the walls move apart to open_w_px (times.open_s). A subject "
                     "fades out as its box comes within fade_px of the screen's edge, the subtitle band or a "
                     "line, the figure or a card (the figure less fig_inset of its line box at the top and the bottom), and is gone edge_px before it; it fades in from far_px and is gone once its near end is within "
                     "near_px of the camera, and a farther subject fades by the same fade_px from a nearer one shown. A subject drawn smaller than min_seen_px (on the screen, in design px) is not shown; it fades in over the next min_seen_fade_px. Thin rails run along each wall at rails_y_px (segments of "
                     "rail_seg_px, rail_len_px in all), fading in from rail_far_px",
        },
        "camera": {"roll_from_deg": 0.8, "roll_to_deg": 0.0, "yaw_from_deg": -2.5, "yaw_to_deg": 0.0,
                   "_note": "the corridor (not the figure, the lines or the cards) rolls (about the line of sight) "
                            "and turns (about the vertical) from *_from_deg to *_to_deg over times.cam_s, on "
                            "eases.cam"},
        "num": {"size_px": 190, "unit_em": 0.34, "layers": 30, "depth_px": 90,
                "ry_from_deg": -12, "ry_to_deg": -6, "rx_from_deg": 5, "rx_to_deg": 3,
                "_note": "the lines written, extruded, held in front of the camera, centred on the eye line; "
                         "turns from *_from_deg to *_to_deg over times.cam_s on eases.cam"},
        "cap": {"y": 178, "h": 52, "size_px": 34},
        "sub": {"y": 640, "h": 60, "size_px": 40},
        "stamp": {"x": 96, "y": 858, "w": 1728, "h": 32, "size_px": 19},
        "lift": {"fig_dy_px": -175, "fig_scale": 0.6, "cap_dy_px": -86, "sub_dy_px": -290,
                 "_note": "after the walls start to open, the figure (scaled by fig_scale), the caption and the "
                          "commits line move up by these amounts over times.lift_s, to leave room for the depth"},
        "depth": {
            "head": {"y": 446, "h": 64, "size_px": 42},
            "cards": {"x": 96, "y": 560, "w": 1728, "h": 270, "gap_px": 36, "rule_px": 2,
                      "lab_px": 30, "num_px": 116, "unit_em": 0.34, "sub_px": 24,
                      "layers": 14, "depth_px": 30, "from_z_px": -1400, "from_rx_deg": 8, "rise_px": 40,
                      "from_ry_deg": -8, "ry_deg": -3, "rail_dim": 0.8, "perspective_px": 1200},
            "_note": "sentence 2: the heading (one run) and four cards in a row, in the order of a run: what is "
                     "judged, with what, checked how, and the end. Each card comes forward from from_z_px "
                     "(tilted back by from_rx_deg) over times.card_in_s and its figure (extruded: layers copies "
                     "over depth_px, the unit on the face only) counts up over times.card_count_s (from the card's "
                     "start), turning from "
                     "from_ry_deg to ry_deg. The corridor's rails dim by rail_dim as the cards come in (they "
                     "would cross the cards' text)",
        },
        "times": {
            "cam_s": [0.0, 5.8], "count_s": [0.35, 1.9], "num_in_s": 0.2, "cap_s": 0.3,
            "open_s": [2.3, 4.6], "sub_s": 2.4, "lift_s": [2.75, 3.3], "head_s": 3.0,
            "cards_s": 3.25, "card_step_s": 0.25, "card_in_s": 0.45, "card_count_s": [0.1, 0.65], "stamp_s": 4.5,
        },
        "eases": {
            "cam": [0.45, 0.0, 0.4, 1.0], "count": [0.35, 0.0, 0.25, 1.0], "enter": [0.3, 0.0, 0.2, 1.0],
            "move": [0.45, 0.0, 0.35, 1.0],
            "_note": "cubic-bezier control points (as CSS): cam for the camera's travel, roll and turn and the "
                     "figure's turn; count for the figures counting up; enter for things coming in (fades, "
                     "cards); move for the walls opening and the lift. Each starts and ends at rest",
        },
        "count_steps": 40,
        "messages": 60,
        "message_chars": 36,
    },
}

# Subjects that would put a local path (a drive letter) or an address on the screen are left out.
PRIVATE = re.compile(r"\b[a-z]:[/\\]|@", re.I)


# --- N08b: the two figures, from n08b.html ------------------------------------------

def _one(pattern: str, text: str) -> str:
    m = re.search(pattern, text, re.S)
    if not m:
        raise SystemExit(f"n08b.html: no match for {pattern!r}")
    return m.group(1)


def n08b(page: Path = N08B_PAGE) -> dict:
    """The figures of N08b as n08b.html holds them, and the shares on the screen."""
    text = page.read_text(encoding="utf-8")
    block = text[text.index("const DATA = {"):]
    block = block[:block.index("\n  };") + 4]
    mfg = block[block.index("manufacturing:"):block.index("cae_adoption:")]
    cae = block[block.index("cae_adoption:"):]
    all_, sme = int(_one(r"\ball:\s*(\d+)", mfg)), int(_one(r"\bsme:\s*(\d+)", mfg))
    small_label, small = _one(r"small:\s*\{\s*label:\s*\"([^\"]+)\"", cae), float(_one(r"small:\s*\{[^}]*pct:\s*([\d.]+)", cae))
    large_label, large = _one(r"large:\s*\{\s*label:\s*\"([^\"]+)\"", cae), float(_one(r"large:\s*\{[^}]*pct:\s*([\d.]+)", cae))
    sme_pct = 100 * sme / all_
    return {
        "from": "promo/screens/n08b.html (DATA)",
        "manufacturing": {"all": all_, "sme": sme, "sme_pct": sme_pct, "shown": round(sme_pct),
                          "source": _one(r"source:\s*\"([^\"]+)\"", mfg)},
        "cae_adoption": {"small": {"label": small_label, "pct": small, "shown": round(small)},
                         "large": {"label": large_label, "pct": large},
                         "source_short": _one(r"source_short:\s*\"([^\"]+)\"", cae)},
    }


# --- N17b: the amount and the commit subjects, from git -----------------------------

def shorten(subject: str, limit: int) -> str:
    """The subject cut to at most limit characters, at a space, with an ellipsis."""
    s = " ".join(subject.split())
    if len(s) <= limit:
        return s
    cut = s[:limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut[:cut.rindex(" ")]
    return cut.rstrip(" ,;:-") + "…"


def pick(items: list, k: int) -> list:
    """k items spread evenly over the list, first and last included, in order."""
    if len(items) <= k:
        return list(items)
    if k == 1:
        return [items[-1]]
    return [items[round(i * (len(items) - 1) / (k - 1))] for i in range(k)]


DEPTH_KEYS = ("run_id", "domains", "assembly", "areas", "passed", "versions", "tools", "known_cases",
              "comparisons", "mismatches", "explained")


def n17b_depth(run: Path | None = None) -> dict:
    """The depth of one run as N17b counts it (n17b_data.depth): from the run's
    events.jsonl when its folder is given, else the depth N17b's data file holds
    (promo/screens/n17b.data.js, counted by n17b_data.py from its run)."""
    d = n17b_data.depth(n17b_data.read_events(run)) if run is not None else n17b_data.read_data(n17b_data.OUT)["depth"]
    return {k: d[k] for k in DEPTH_KEYS}


def n17b(repo: Path = ROOT, ref: str = "main", rules: dict | None = None, depth: dict | None = None) -> dict:
    """Lines, commits and days of ref (as N17b counts them), the corridor's
    subjects, and the depth of one run (n17b_depth() when depth is None)."""
    r = rules or RULES["n17b_kt"]
    am = n17b_data.amount(repo, ref)
    log = n17b_data.read_log(repo, ref)  # parents before children
    plain = [c for c in log if len(c["parents"]) < 2 and not PRIVATE.search(c["subject"])]
    chosen = pick(plain, r["messages"])
    return {
        "ref": ref,
        "head": am["head"],
        "lines_total": am["lines_total"],
        "commits": am["commits"],
        "days": am["days"],
        "subjects_from": len(plain),
        "messages": [{"sha": c["sha"][:7], "text": shorten(c["subject"], r["message_chars"])} for c in chosen],
        "depth": depth if depth is not None else n17b_depth(),
    }


# --- writing ------------------------------------------------------------------------

def render_js(cut: str, values: dict, counted_at: str | None = None) -> str:
    data = {"cut": cut, "rules": RULES[cut], "values": values}
    if counted_at:
        data["counted_at"] = counted_at
    return ("// Generated file: do not edit. Written by promo/kinetic_data.py "
            f"(rules and values of {cut}).\n"
            "window.KT_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n")


def read_js(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", type=Path, default=ROOT)
    ap.add_argument("--ref", default="main")
    ap.add_argument("--run", type=Path, default=None,
                    help="a run's folder (holds events.jsonl) to count the depth from; "
                         "by default the depth of promo/screens/n17b.data.js")
    a = ap.parse_args(argv)
    v8 = n08b()
    OUT_N08B.write_text(render_js("n08b_kt", v8), encoding="utf-8", newline="\n")
    v17 = n17b(a.repo, a.ref, depth=n17b_depth(a.run))
    now = datetime.now(n17b_data.JST).isoformat(timespec="minutes")
    OUT_N17B.write_text(render_js("n17b_kt", v17, now), encoding="utf-8", newline="\n")
    m = v8["manufacturing"]
    print(f"n08b_kt: {m['sme']:,} / {m['all']:,} = {m['sme_pct']:.2f} % -> {m['shown']} %; "
          f"CAE {v8['cae_adoption']['small']['pct']} % -> {v8['cae_adoption']['small']['shown']} %")
    print(f"n17b_kt: {v17['ref']} {v17['head']}: {v17['lines_total']:,} lines, {v17['commits']} commits, "
          f"day {v17['days']}, {len(v17['messages'])} subjects of {v17['subjects_from']}")
    d = v17["depth"]
    print(f"  depth ({d['run_id']}): {d['domains']} domains" + (" + assembly" if d["assembly"] else "")
          + f", {d['tools']} tools on {d['known_cases']} known-answer cases, {d['comparisons']} comparisons "
          f"({d['mismatches']} mismatches, {d['explained']} explained), {d['passed']}/{d['areas']} green "
          f"in version {d['versions']}")
    print(f"wrote {OUT_N08B}\nwrote {OUT_N17B}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

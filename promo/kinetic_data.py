"""Data and rules for the kinetic type samples of N08b and N17b.

Writes two generated files the sample pages load with <script src> (they are
opened from file://):

- promo/screens/n08b_kt.data.js (``window.KT_DATA``): the two figures of N08b,
  read from the DATA block of promo/screens/n08b.html (the sourced values and
  their sources; nothing is typed in here), and the shares worked out from them.
- promo/screens/n17b_kt.data.js (``window.KT_DATA``): from the git record of one
  ref (main by default), the lines of code written (counted as N17b counts them,
  promo/n17b_data.py), the commits, the days, and the subjects of a spread of
  commits for the corridor (English, cut short).

Both files also carry the page's rules (RULES below: placement in 1920x1080
design px, times in seconds from the cut's start, camera). These two pages are
samples; their rules live here and not in layout.json.

    py promo/kinetic_data.py                 # both, git counted on main
    py promo/kinetic_data.py --ref HEAD      # count another ref

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
            "half_w_px": 560, "open_w_px": 760, "rows_y_px": [-230, -120, 110, 190], "z0_px": -600,
            "pitch_px": 165, "size_px": 40, "travel_px": 7600, "fade_px": 140, "edge_px": 6,
            "far_px": 9500, "near_px": 120, "alpha": 0.9,
            "rails_y_px": [-300, 270], "rail_seg_px": 600, "rail_len_px": 11400, "rail_px": 2, "rail_alpha": 0.22,
            "_note": "commit subjects on two walls (x = -half_w_px and +half_w_px from the middle, rows at "
                     "rows_y_px from the eye line eye_y), oldest at z0_px, one more every pitch_px into the "
                     "distance, alternating left and right. The camera goes travel_px forward over times.cam_s. "
                     "With sentence 2 the walls move apart to open_w_px (times.open_s). A subject fades out as "
                     "its box comes within fade_px of the screen's edge or the subtitle band and is gone edge_px "
                     "before it; it fades in from far_px and is gone once its near end is within near_px of the camera. "
                     "Thin rails run along each wall at rails_y_px (segments of rail_seg_px, rail_len_px in all)",
        },
        "num": {"size_px": 190, "unit_em": 0.34, "layers": 30, "depth_px": 90,
                "ry_from_deg": -24, "ry_to_deg": -8, "rx_from_deg": 10, "rx_to_deg": 4,
                "_note": "the lines written, extruded, held in front of the camera, centred on the eye line"},
        "cap": {"y": 178, "h": 52, "size_px": 34},
        "sub": {"y": 640, "h": 60, "size_px": 40},
        "stamp": {"x": 96, "y": 858, "w": 1728, "h": 32, "size_px": 19},
        "times": {
            "cam_s": [0.0, 4.9], "count_s": [0.35, 1.9], "num_in_s": 0.2, "cap_s": 0.3,
            "open_s": [2.3, 4.6], "sub_s": 2.4, "stamp_s": 4.5,
        },
        "cam_ease": [0.4, 0.0, 0.2, 1.0],
        "count_steps": 40,
        "messages": 60,
        "message_chars": 46,
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


def n17b(repo: Path = ROOT, ref: str = "main", rules: dict | None = None) -> dict:
    """Lines, commits and days of ref (as N17b counts them) and the corridor's subjects."""
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
    a = ap.parse_args(argv)
    v8 = n08b()
    OUT_N08B.write_text(render_js("n08b_kt", v8), encoding="utf-8", newline="\n")
    v17 = n17b(a.repo, a.ref)
    now = datetime.now(n17b_data.JST).isoformat(timespec="minutes")
    OUT_N17B.write_text(render_js("n17b_kt", v17, now), encoding="utf-8", newline="\n")
    m = v8["manufacturing"]
    print(f"n08b_kt: {m['sme']:,} / {m['all']:,} = {m['sme_pct']:.2f} % -> {m['shown']} %; "
          f"CAE {v8['cae_adoption']['small']['pct']} % -> {v8['cae_adoption']['small']['shown']} %")
    print(f"n17b_kt: {v17['ref']} {v17['head']}: {v17['lines_total']:,} lines, {v17['commits']} commits, "
          f"day {v17['days']}, {len(v17['messages'])} subjects of {v17['subjects_from']}")
    print(f"wrote {OUT_N08B}\nwrote {OUT_N17B}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

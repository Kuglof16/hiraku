// Generated file: do not edit. Made by `py promo/layout_build.py` from layout.json;
// edit layout.json and run it again (promo/tests/test_layout.py checks the two match).
window.PROMO_LAYOUT = {
  "_about": "Layout rules of the video-only screens, in 1920x1080 design px. The one source of positions, sizes and times: py promo/layout_build.py writes layout.data.js from this file, layout.js turns it into CSS variables and coordinates, bgm.py reads the times. Units: x, y, w, h are design px (top-left origin); other lengths end in _px, times (from the start of the cut) end in _s. Keys starting with _ are notes. Values were read from the pages as they were on 2026-11-03.",
  "stage": {
    "w": 1920,
    "h": 1080
  },
  "subtitle_band": {
    "y": 910,
    "h": 170,
    "_note": "the bottom 170 px hold nothing but the subtitles"
  },
  "safe": {
    "x": 96,
    "y": 60,
    "w": 1728,
    "h": 850,
    "_note": "side margin 96, top margin 60, down to the subtitle band"
  },
  "min_text_px": 18,
  "_min_text_note": "smallest font size at 1080p, Japanese and Latin alike. A rule of this project, not taken from an outside standard.",
  "colors": {
    "night": "#131311",
    "ink": "#1b1a17",
    "tile_bg": "#f7f4ec",
    "tile_border": "#3b3934",
    "_note": "night: ground of the title screens; ink: black ink on paper; tile_*: the N05 test tiles"
  },
  "n00": {
    "duration_s": 7.0,
    "name": {
      "y": 172,
      "size_px": 210
    },
    "rule": {
      "y": 430,
      "w": 400,
      "h": 2,
      "_note": "centred"
    },
    "row": {
      "y": 500,
      "gap_px": 34
    },
    "agent": {
      "w": 196
    },
    "pet": {
      "size_px": 184,
      "shift_px": 18
    },
    "chip": {
      "gap_above_px": 14,
      "h": 52,
      "size_px": 27
    },
    "times": {
      "name_s": 0.25,
      "row_s": 1.1,
      "light_s": 2.0,
      "step_s": 0.42,
      "_note": "name: first letter; row: agents and chips come in, unlit; light: first chip lights; step: gap between chips"
    }
  },
  "n05": {
    "duration_s": 5.0,
    "grid": {
      "x": 96,
      "y": 280,
      "cols": 4,
      "rows": 3,
      "tile_w": 408,
      "tile_h": 180,
      "gap_x": 32,
      "gap_y": 26,
      "_note": "cell (c, r) at x + c * (tile_w + gap_x), y + r * (tile_h + gap_y); spans safe.x to 1920 - safe.x"
    },
    "tile": {
      "border_px": 2,
      "radius_px": 10,
      "label": {
        "x": 16,
        "y": 12,
        "size_px": 26
      },
      "rate": {
        "size_px": 18,
        "_note": "not drawn on the promo screens"
      }
    },
    "hero": {
      "x": 210,
      "y": 60,
      "w": 1500,
      "h": 750,
      "_note": "centred; h follows the drawing (viewBox 600 x 300)"
    },
    "hero_motion": {
      "push_scale": 1.04,
      "burst_scale": 1.7,
      "burst_dur_s": 0.4,
      "ease": [
        0.5,
        0,
        0.9,
        0.4
      ],
      "_note": "the vehicle grows to push_scale until times.burst_s, then to burst_scale and fades out over burst_dur_s (ease: cubic-bezier)"
    },
    "ease": {
      "tile": [
        0.2,
        0.9,
        0.3,
        1.15
      ],
      "_note": "cubic-bezier of a tile's flight"
    },
    "title": {
      "name": {
        "y": 62,
        "size_px": 124
      },
      "tag": {
        "y": 200,
        "size_px": 42
      }
    },
    "times": {
      "burst_s": 0.95,
      "tiles_s": 1.05,
      "tile_in_s": 0.6,
      "tile_stagger_s": 0.05,
      "name_s": 1.45,
      "tag_s": 2.0,
      "_note": "burst: the vehicle bursts open; tiles: first tile starts to fly in; tile_in: flight of one tile; tile_stagger: between tiles, nearest to fly_from first"
    },
    "fly_from": {
      "x": 960,
      "y": 470
    },
    "tests": [
      "step",
      "tilt",
      "wind",
      "rough",
      "dlc",
      "brake",
      "long",
      "launch",
      "crash",
      "nvh",
      "fatigue",
      "heat"
    ]
  },
  "n08b": {
    "duration_s": 10.0,
    "fig": {
      "y": 210,
      "w": 860,
      "gap_px": 60,
      "num_size_px": 200,
      "cap_gap_px": 26,
      "cap_size_px": 44,
      "_note": "two figures, centred on the screen's middle with gap between them"
    },
    "divider": {
      "y": 230,
      "w": 2,
      "h": 260,
      "_note": "centred"
    },
    "bars": {
      "x": 930,
      "y": 560,
      "w": 930,
      "label_w": 290,
      "col_gap_px": 22,
      "row_h": 64,
      "bar_h": 36,
      "value_gap_px": 16,
      "label_size_px": 30,
      "value_size_px": 32,
      "px_per_pct": 7,
      "_note": "both bars share one scale: px_per_pct design px per percent. Was 8 until 2026-11-03; at 8 the 67.9 % bar (543 px) pushed its value 28 px past the bar area, at 7 (475 px) it has 40 px to spare"
    },
    "sources": {
      "side_px": 90,
      "y": 770,
      "size_px": 21
    },
    "times": {
      "left_s": 0.3,
      "right_s": 4.8,
      "bars_s": 6.1,
      "src_s": 7.1,
      "_note": "left: first figure, with the first sentence; right: second figure, just before the second sentence (voice at 5.0 s)"
    }
  },
  "n18": {
    "duration_s": 9.0,
    "light": {
      "y": 40,
      "w": 130,
      "h": 470,
      "_note": "centred"
    },
    "vehicle": {
      "y": 130,
      "w": 420,
      "_note": "centred"
    },
    "title": {
      "name": {
        "y": 545,
        "size_px": 170
      },
      "tag": {
        "y": 745,
        "size_px": 48
      }
    },
    "url": {
      "y": 856,
      "size_px": 30
    },
    "times": {
      "line_s": 0.15,
      "open_s": 0.8,
      "draw_s": 1.1,
      "name_s": 2.3,
      "tag_s": 3.0,
      "url_s": 3.6,
      "out_s": 8.2,
      "_note": "line: thin vertical line grows; open: it opens into a band; draw: vehicle strokes start; out: fade to black"
    }
  },
  "check": {
    "times": {
      "n00": [
        0.6,
        1.8,
        3.5,
        6.5
      ],
      "n05": [
        0.5,
        2.6,
        4.5
      ],
      "n08b": [
        1.5,
        5.2,
        5.8,
        7.5,
        9.8
      ],
      "n18": [
        1.0,
        3.2,
        5.0,
        8.5
      ],
      "app": [
        2,
        8,
        15
      ]
    },
    "band_exempt": [
      {
        "selector": ".guide-band",
        "why": "the band's own guide (?guides=1)"
      },
      {
        "selector": ".clock",
        "why": "authoring clock, drawn in the band with ?guides=1"
      }
    ],
    "allow_overflow": [
      {
        "cut": "*",
        "selector": ".tag, .tile b, .bar-row span, .fig .cap, .url",
        "axis": "y",
        "max_em": 0.25,
        "why": "line-height (1 to 1.3) is shorter than the font's content box (ascent + descent), so scrollHeight runs a few px past the line box. Measured 2-7 px (0.06-0.23 em). The glyphs stay inside and no ancestor clips them; a taller line-height would move the text"
      }
    ],
    "allow_escape": [
      {
        "cut": "n08b",
        "box": "n08b.fig.*.cap",
        "max_px": 14,
        "why": "the caption fades up from 14 px below its place (0.6 s), so while it moves it reaches up to 14 px under the figure's box; at rest it is inside"
      }
    ],
    "allow_overlap": [
      {
        "cut": "n18",
        "a": "n18.light",
        "b": "n18.vehicle",
        "why": "the vehicle is drawn over the vertical light on purpose"
      }
    ]
  }
};

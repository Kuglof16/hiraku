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
    "duration_s": 8.6,
    "_duration_note": "8.6 s is N00's slot in narration draft 6 (0:00-0:08.6): the voice is the longer line that says what HIRAKU is (7.86 s from 0.3 s)",
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
  "n01": {
    "duration_s": 4.0,
    "_note": "drawn by the screens department (web cut.html?cut=n01). 4.0 s is N01's slot in the narration script (0:07-0:11)",
    "view": {
      "x": 96,
      "y": 96,
      "w": 1200,
      "h": 790,
      "_note": "the 3D view; bottom 886, above the subtitle band"
    },
    "pane": {
      "x": 1328,
      "y": 96,
      "w": 496,
      "h": 790,
      "_note": "result cards on the right"
    },
    "card": {
      "gap": 20,
      "pad": 20,
      "title_px": 30,
      "text_px": 22,
      "radius_px": 12,
      "border_px": 2
    },
    "times": {
      "lead_s": 0.4,
      "zoom_s": 1.6,
      "_note": "lead: still before the motion starts; zoom: the camera has come in"
    },
    "zoom": {
      "from": 1.6,
      "to": 1.06,
      "_note": "margin factor around the vehicle, from -> to"
    },
    "overlay": {
      "color": "#c8402a",
      "opacity": 0.22,
      "_note": "the condition past the limit, translucent red"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
    }
  },
  "n02": {
    "duration_s": 7.0,
    "_note": "drawn by the screens department (web cut.html?cut=n02). 7.0 s is N02's slot in the narration script (0:11-0:18)",
    "grid": {
      "x": 96,
      "y": 96,
      "cols": 3,
      "rows": 2,
      "tile_w": 400,
      "tile_h": 383,
      "gap_x": 24,
      "gap_y": 24,
      "_note": "five tests and the legend; bottom 886, above the subtitle band"
    },
    "panes": [
      "tilt",
      "wind",
      "rough",
      "dlc",
      "brake",
      "legend"
    ],
    "pane": {
      "border_px": 2,
      "radius_px": 12,
      "label": {
        "x": 16,
        "y": 12,
        "size_px": 26
      },
      "rate": {
        "size_px": 18
      }
    },
    "result": {
      "x": 1392,
      "y": 96,
      "w": 432,
      "h": 790,
      "title_px": 30,
      "text_px": 20,
      "row_gap": 16,
      "rows": 2,
      "_note": "limit values on the right; rows: result lines per test"
    },
    "times": {
      "lead_s": 0.3
    },
    "overlay": {
      "color": "#c8402a",
      "opacity": 0.22,
      "_note": "the condition past the limit, translucent red"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
    }
  },
  "n04": {
    "duration_s": 4.0,
    "_note": "drawn by the screens department (web cut.html?cut=n04). 4.0 s is N04's slot in narration draft 5 (0:22.5-0:26.5). The same grid of eight tiles as N15",
    "header": {
      "x": 96,
      "y": 96,
      "w": 1728,
      "h": 48,
      "size_px": 30,
      "_note": "one line above the grid: version, rounds"
    },
    "grid": {
      "x": 96,
      "y": 160,
      "cols": 4,
      "rows": 2,
      "tile_w": 414,
      "tile_h": 351,
      "gap_x": 24,
      "gap_y": 24,
      "_note": "seven domains and assembly; bottom 886, above the subtitle band"
    },
    "tests": [
      "handling",
      "powertrain",
      "aero",
      "crash",
      "strength",
      "nvh",
      "thermal",
      "assembly"
    ],
    "tile": {
      "border_px": 2,
      "radius_px": 12,
      "label": {
        "x": 16,
        "y": 12,
        "size_px": 26
      },
      "mark": {
        "size_px": 44,
        "right": 16,
        "top": 10,
        "_note": "the tick or cross of the tile"
      },
      "pet": {
        "w": 96,
        "h": 96,
        "right": 12,
        "bottom": 12,
        "_note": "the corner of the tile where the role's pet stands"
      },
      "rate": {
        "size_px": 18
      }
    },
    "times": {
      "lead_s": 0.3,
      "crash_s": 0.5,
      "nvh_s": 1.2,
      "assembly_s": 2.0,
      "_note": "lead: still before anything moves. crash / nvh / assembly: when the voice says 衝突 (0.56 s), 振動 (about 1.2 s) and 組み立て (2.06 s), measured from the voice's pauses (the voice starts 0.3 s into the cut). The crash and NVH tiles are red mid iteration; from assembly_s to the end the grid gives way to the assembly fast-forward in the n16.view box"
    },
    "overlay": {
      "color": "#c8402a",
      "opacity": 0.22,
      "_note": "the condition past the limit, translucent red"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
    }
  },
  "n15": {
    "duration_s": 5.1,
    "_note": "drawn by the screens department (web cut.html?cut=n15) from a run of its own that ends with all eight tiles green. 5.1 s is N15's slot in narration draft 5 (2:24.7-2:29.8). header, grid, tests and tile are the same as n04",
    "header": {
      "x": 96,
      "y": 96,
      "w": 1728,
      "h": 48,
      "size_px": 30,
      "_note": "one line above the grid: version, rounds"
    },
    "grid": {
      "x": 96,
      "y": 160,
      "cols": 4,
      "rows": 2,
      "tile_w": 414,
      "tile_h": 351,
      "gap_x": 24,
      "gap_y": 24,
      "_note": "seven domains and assembly; bottom 886, above the subtitle band"
    },
    "tests": [
      "handling",
      "powertrain",
      "aero",
      "crash",
      "strength",
      "nvh",
      "thermal",
      "assembly"
    ],
    "tile": {
      "border_px": 2,
      "radius_px": 12,
      "label": {
        "x": 16,
        "y": 12,
        "size_px": 26
      },
      "mark": {
        "size_px": 44,
        "right": 16,
        "top": 10,
        "_note": "the tick or cross of the tile"
      },
      "pet": {
        "w": 96,
        "h": 96,
        "right": 12,
        "bottom": 12,
        "_note": "the corner of the tile where the role's pet stands"
      },
      "rate": {
        "size_px": 18
      }
    },
    "times": {
      "lead_s": 0.3,
      "rounds_from_s": 0.4,
      "all_green_s": 4.0,
      "_note": "rounds_from: the rounds of design changes start to play; all_green: every tile is green (tick) and the grid holds still to the end. The voice runs 0.54-4.32 s"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
    }
  },
  "n16": {
    "duration_s": 6.3,
    "_note": "drawn by the screens department (web cut.html?cut=n16). 6.3 s is N16's slot in narration draft 5 (2:29.8-2:36.1)",
    "view": {
      "x": 96,
      "y": 96,
      "w": 1200,
      "h": 790,
      "_note": "the 3D assembly, the same box as n01.view; bottom 886"
    },
    "seek": {
      "x": 120,
      "y": 814,
      "w": 1152,
      "h": 48,
      "_note": "the seek bar over the lower edge of the view, 24 px inside its sides and bottom"
    },
    "steps": {
      "x": 1328,
      "y": 96,
      "w": 496,
      "h": 790,
      "title_px": 30,
      "text_px": 22,
      "row_gap": 12,
      "_note": "the list of assembly steps on the right"
    },
    "times": {
      "lead_s": 0.3,
      "steps_from_s": 0.5,
      "steps_to_s": 4.2,
      "scrub_s": 4.4,
      "scrub_to_s": 5.8,
      "_note": "steps advance one at a time from steps_from to steps_to (how many comes from the record); from scrub to scrub_to the seek bar is dragged back and forward by hand, as the voice says 確認できます (from about 4.5 s); then still to the end. The voice runs 0.6-5.7 s; 組み立て手順を starts at 2.93 s"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
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
      "src": "media/n05_hero_2x.png",
      "_note": "centred; h follows the drawing (viewBox 600 x 300). src: picture of the vehicle alone on a transparent ground (a PNG, path relative to the page), fitted into the box (contain); empty: the line drawing of vehicle.js. ?hero=<path> in the page's URL wins over src. media/n05_hero_2x.png: the app's own drawing of the vehicle (web/cut.html?cut=n05&only=hero&t=0, device scale 2, 3000 x 1500) from the video run 20261103-041110-offroad-4w-scripted"
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
      "_note": "the vehicle grows to push_scale until times.burst_s, then to burst_scale and fades out over burst_dur_s (ease: cubic-bezier). The app's render (web/js/cut.js) uses all of these; promo n05.html uses push_scale only (the vehicle grows to it until the door swings wide, then fades out by door.hero_fade_*); burst_scale, burst_dur_s and ease are not used there"
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
      "burst_s": 1.25,
      "tiles_s": 1.35,
      "tile_in_s": 0.6,
      "tile_stagger_s": 0.05,
      "name_s": 1.75,
      "tag_s": 2.3,
      "door_open_s": 0.55,
      "latch_s": 0.38,
      "mesh_in_s": 0.2,
      "mesh_out_s": 0.78,
      "_note": "burst: flash of light as the door has swung out; tiles: first tile starts to fly in; tile_in: flight of one tile; tile_stagger: between tiles, nearest to fly_from first; door_open: the closed door starts to part (door.crack_*). burst, tiles, name and tag were 0.3 s earlier until 2026-11-03; they moved to leave the door closed a while first. latch: the bolt slides back and the leaves jolt (door.latch_*), the seam light flickers; mesh_in: the triangle mesh starts to show over the vehicle (hero_mesh.in_dur_s); mesh_out: its first elements come apart (hero_mesh.*)"
    },
    "door": {
      "crack_dur_s": 0.2,
      "crack_deg": 12,
      "crack_ease": [
        0.25,
        0,
        0.3,
        1
      ],
      "hold_dur_s": 0.12,
      "open_dur_s": 0.38,
      "angle_deg": 105,
      "perspective_px": 2400,
      "ease": [
        0.45,
        0,
        0.8,
        0.55
      ],
      "seam_w_px": 4,
      "seam_glow_w_px": 220,
      "spill_dur_s": 0.9,
      "hero_fade_dur_s": 0.35,
      "hero_fade_scale": 1.1,
      "panel": {
        "inset_px": 72,
        "upper_h_px": 300,
        "gap_px": 40,
        "line_px": 2,
        "bevel_px": 16
      },
      "handle": {
        "w_px": 12,
        "h_px": 200,
        "seam_gap_px": 48
      },
      "crack_overshoot_deg": 3,
      "settle_dur_s": 0.1,
      "latch_dur_s": 0.17,
      "latch_deg": 0.8,
      "thickness_px": 24,
      "dolly_px": 260,
      "dolly_ease": [
        0.55,
        0,
        0.85,
        0.5
      ],
      "bolt": {
        "w_px": 120,
        "h_px": 18,
        "reach_px": 40,
        "travel_px": 72,
        "gap_px": 64
      },
      "_v4_note": "added 2026-11-03 (v4). crack_overshoot_deg: the crack runs past crack_deg by this much and settles back over settle_dur_s (inside hold_dur_s), so the leaves stop like heavy doors. latch_*: at times.latch_s the bolt (bolt: on the left leaf across the seam, w x h, reaching reach_px into the right leaf, sliding back travel_px, gap_px above the handle) slides back over latch_dur_s while both leaves jolt open by up to latch_deg and come to rest at 0.4 of it. thickness_px: the leaves' free edges, seen as they turn. dolly_px: from times.door_open_s to times.burst_s the door comes towards the viewer by this much (translateZ under perspective_px, dolly_ease), and the light in front of it grows by the same factor perspective / (perspective - dolly), so the view seems to pass through the door. The swing's own overshoot is not drawn: past about 60 deg the leaves are out of the frame",
      "_note": "double door over the whole stage behind the vehicle, two leaves of 960 x 1080 hinged on the outer edges, opening towards the viewer (kannon-biraki; rotateY, perspective_px from the stage's centre). Closed until times.door_open_s while the light in the seam grows; then the leaves part by crack_deg over crack_dur_s (crack_ease), stay there for hold_dur_s, and swing on to angle_deg over open_dur_s (ease: slow at first, then fast as the leaves pass out of the frame, near 68 deg). Fully open (and hidden) at door_open_s + crack_dur_s + hold_dur_s + open_dur_s = times.burst_s. The vehicle in front does not open: from the start of the wide swing it grows from hero_motion.push_scale to hero_fade_scale and fades out over hero_fade_dur_s. seam_w_px / seam_glow_w_px: the warm light in the seam and its halo; spill_dur_s: the light behind the door, seen through the opening, and the light that spills towards the viewer, both fade out over it from the start of the wide swing. panel: the two raised panels on each leaf (inset from the leaf's edges, upper panel height, gap between the panels, outline width, width of the raised border); handle: the pull next to the seam"
    },
    "rays": {
      "count": 26,
      "seed": 7,
      "width_min_deg": 0.7,
      "width_max_deg": 3.6,
      "spread_deg": 62,
      "length_px": 1500,
      "blur_px": 6,
      "peak_alpha": 0.32,
      "crack_scale": 0.12,
      "shimmer_min_dur_s": 0.45,
      "shimmer_max_dur_s": 1.1,
      "out_dur_s": 0.5,
      "_note": "shafts of light fanning out of the opening, from fly_from: count wedges, half to each side, each at a seeded angle within spread_deg of the horizontal and a seeded width (width_*_deg), length_px long, blurred by blur_px. From times.door_open_s they grow from crack_scale (squeezed towards the seam) and nothing to full width and peak_alpha at times.burst_s, then fade over out_dur_s. Each shimmers on its own seeded period (shimmer_*_dur_s). Seeded: the same seed gives the same shafts"
    },
    "dust": {
      "count": 90,
      "seed": 11,
      "size_min_px": 2,
      "size_max_px": 7,
      "w": 1600,
      "h": 760,
      "drift_px": 70,
      "in_dur_s": 0.3,
      "out_dur_s": 0.7,
      "_note": "motes floating in the light: count soft dots at seeded places in a w x h ellipse around fly_from (seeded size_*_px), brighter near fly_from, each drifting up to drift_px and twinkling on its own seeded period. They come in over in_dur_s once the door has cracked (times.door_open_s + door.crack_dur_s) and fade over out_dur_s from times.burst_s"
    },
    "bloom": {
      "peak_alpha": 0.3,
      "rise_dur_s": 0.12,
      "fall_dur_s": 0.55,
      "_note": "the light floods out as the door is fully open: a warm radial wash around fly_from rises over rise_dur_s to peak_alpha at times.burst_s and falls over fall_dur_s. 0.42 in the first stills washed the screen out with the shafts and the light behind the door; 0.3 keeps it from burning white"
    },
    "hero_mesh": {
      "size_px": 50,
      "jitter": 0.28,
      "seed": 5,
      "line_px": 1.3,
      "in_dur_s": 0.4,
      "reach_px": 520,
      "spread_dur_s": 0.27,
      "jitter_dur_s": 0.04,
      "el_dur_s": 0.13,
      "end_scale": 0.15,
      "_note": "the vehicle comes apart as a finite-element mesh. Triangles of side size_px (rows of nodes, every other row shifted half a side, each node moved by up to jitter x size_px, seeded) cover the hero box; their lines show only where the vehicle's picture is (the PNG's alpha as an SVG mask), line_px wide, fading in over in_dur_s from times.mesh_in_s. From times.mesh_out_s each element flares, shrinks to end_scale about its centre and vanishes over el_dur_s, taking its piece of the picture with it; an element starts spread_dur_s x min(1, distance from fly_from / reach_px) later, plus up to jitter_dur_s (seeded). The last ends by times.mesh_out_s + spread_dur_s + jitter_dur_s + el_dur_s, inside the swing's start + door.hero_fade_dur_s. Only with a picture (hero.src or ?hero=); the line drawing fades as before. size_px was 34 at first (about 2400 triangles, each drawn twice with its own animation): the same t shot twice then differed in 1 to 4 of 6 tries (headless Chromium had not finished painting); at 50 (about 1100) 24 of 24 tries were identical"
    },
    "tile_fx": {
      "ghosts": 2,
      "ghost_lag_s": 0.035,
      "ghost_alpha": 0.32,
      "land_dur_s": 0.5,
      "land_glow_px": 26,
      "_note": "drawn in ?layer=fg too, so it moves exactly as the app's tiles (web/js/compose.js flyIn: same start, tile_in_s, ease.tile, fly_from and cells). ghosts: outlines that trail each tile by ghost_lag_s each, up to ghost_alpha, gone before the tile lands. Landing: a warm glow around the cell from the first moment the eased flight reaches its place (computed from ease.tile), over land_dur_s, land_glow_px wide"
    },
    "name_wipe": {
      "wipe_dur_s": 0.65,
      "feather_px": 180,
      "sweep_w_px": 300,
      "afterglow_dur_s": 1.3,
      "_note": "the name appears under a light that strokes it from left to right: from times.name_s a mask edge (feather_px soft) runs over the letters (their measured extent) in wipe_dur_s, with a warm band sweep_w_px wide on the edge; each letter is lit as the edge passes and cools to the ink of the night screen over afterglow_dur_s"
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
  "n03": {
    "duration_s": 5.0,
    "_note": "drawn by the screens department (web cut.html?cut=n03). 5.0 s is N03's slot in narration draft 6 (0:19.6-0:24.6)",
    "view": {
      "x": 96,
      "y": 96,
      "w": 1200,
      "h": 790,
      "_note": "the 3D drive (loop and straight), the same box as n01.view"
    },
    "popup": {
      "x": 1328,
      "y": 96,
      "w": 496,
      "h": 790,
      "title_px": 30,
      "text_px": 22,
      "_note": "battery charge and temperature, then speed, as graphs in the right column"
    },
    "times": {
      "lead_s": 0.3,
      "loop_to_s": 2.8,
      "accel_s": 2.9,
      "_note": "0.3-2.8 s the long loop (battery and heat), from 2.9 s the acceleration (speed); the voice says どこまで速く from about 2.9 s (measured from its pauses)"
    },
    "overlay": {
      "color": "#c8402a",
      "opacity": 0.22,
      "_note": "the condition past the limit, translucent red"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
    }
  },
  "n06": {
    "duration_s": 11.7,
    "_note": "drawn by the screens department (web cut.html?cut=n06): six designs that do not work. 11.7 s is N06's slot in narration draft 6 (0:33.6-0:45.3)",
    "grid": {
      "x": 96,
      "y": 96,
      "cols": 3,
      "rows": 2,
      "tile_w": 560,
      "tile_h": 383,
      "gap_x": 24,
      "gap_y": 24,
      "_note": "bottom 886, above the subtitle band"
    },
    "tests": [
      "step_break",
      "tilt_over",
      "step_stuck",
      "battery_hot",
      "parts_clash",
      "rollover"
    ],
    "tile": {
      "border_px": 2,
      "radius_px": 12,
      "label": {
        "x": 16,
        "y": 12,
        "size_px": 26
      }
    },
    "times": {
      "tiles_s": 0.6,
      "tile_in_s": 0.5,
      "tile_stagger_s": 0.55,
      "_note": "the sixth tile starts at 3.35 s and lands at 3.85 s, all within the first two sentences (0.30-2.79, 3.14-5.93 s)"
    },
    "overlay": {
      "color": "#c8402a",
      "opacity": 0.22,
      "_note": "the condition past the limit, translucent red"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
    }
  },
  "n07": {
    "duration_s": 15.3,
    "_note": "drawn by the screens department (web cut.html?cut=n07): what CAE is, shown as a weak joint found and reinforced. 15.3 s is N07's slot in narration draft 6 (0:45.3-1:00.6). Labels say only before / after the reinforcement (no beam or shell words: viewers do not know them)",
    "view": {
      "x": 96,
      "y": 96,
      "w": 1500,
      "h": 790,
      "_note": "the 3D view; bottom 886"
    },
    "legend": {
      "x": 1620,
      "y": 96,
      "w": 204,
      "h": 790,
      "title_px": 22,
      "text_px": 20,
      "_note": "the colour bar in a column of its own, not over the view"
    },
    "label": {
      "x": 120,
      "y": 120,
      "size_px": 30,
      "_note": "inside the view, top left: 補強の前 / 補強の後"
    },
    "times": {
      "lead_s": 0.3,
      "turn_to_s": 6.5,
      "zoom_s": 6.5,
      "zoom_to_s": 8.5,
      "swap_s": 11.0,
      "swap_to_s": 13.0,
      "_note": "the vehicle turns with the stress colours during sentences 1-2 (0.30-4.22, 4.57-8.89 s); it moves in on the highest element as sentence 2 says 突き止める; the reinforced version's colours come in as sentence 3 says 確かめ、分析する (9.24-15.28 s) and stay while it says それが CAE です"
    },
    "turn_deg": 90,
    "zoom": {
      "fill": 0.45,
      "_note": "when zoomed in, the area around the highest element fills 45 % of the view's height"
    },
    "overlay": {
      "color": "#c8402a",
      "opacity": 0.22,
      "_note": "the condition past the limit, translucent red"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
    }
  },
  "n08": {
    "duration_s": 6.4,
    "_duration_note": "6.4 s is N08's slot. Voice from 0.3 s to 6.29 s; parts measured from its pauses: ただ、 about 0.3-0.9 s; CAE のツールは気軽に買えるものではなく、 about 0.9-3.5 s; 使いこなすのも about 3.6-4.8 s; 簡単ではありません。 about 4.9-6.0 s",
    "_note": "an ordinary CAE tool, not HIRAKU: a cool grey business application on a dark ground, tilted a little. The licence windows pile up while the voice says it is not easy to buy, the settings windows while it says 使いこなすのも, the warnings and the stack of manuals while it says 簡単ではありません. No product names, logos or figures (no prices, no counts in the warnings). Every box below is in stage px before the camera's rotation and scale",
    "camera": {
      "rotate_from_deg": -1.4,
      "rotate_to_deg": -2.4,
      "from_scale": 0.96,
      "to_scale": 1.0,
      "move_dur_s": 6.4,
      "ease": [
        0.3,
        0,
        0.6,
        1
      ],
      "_note": "the application (main window and its dialogs) turns and grows slowly about the centre of the area above the subtitle band (960, 455) over the whole cut. At from_scale 0.96 the smallest text (19 px) is drawn at 18.2 px"
    },
    "main": {
      "x": 70,
      "y": 40,
      "w": 1780,
      "h": 800,
      "bar_h": 38,
      "menu_h": 38,
      "tool_h": 50,
      "status_h": 34,
      "tree_w": 330,
      "props_w": 340,
      "_note": "the main window: title bar, menu bar, tool bar, model tree on the left, the 3D view, properties on the right, status bar"
    },
    "view": {
      "seed": 3,
      "unit_px": 44,
      "depth_x_px": 150,
      "depth_y_px": 90,
      "origin_x": 170,
      "origin_y": 590,
      "jitter": 0.22,
      "_note": "the mesh of an L-shaped bracket in the 3D view (an oblique drawing, in the view's own px): grid cells of half a unit, interior nodes moved by up to jitter x cell (seeded); the faces seen from the upper right are drawn as quads"
    },
    "chrome": {
      "bar_h": 40,
      "pad_px": 16,
      "row_h": 40,
      "label_w": 220,
      "field_h": 30,
      "tab_h": 38,
      "btn_h": 34,
      "radius_px": 4
    },
    "text": {
      "title_px": 20,
      "menu_px": 20,
      "tree_px": 20,
      "label_px": 20,
      "field_px": 19,
      "tab_px": 19,
      "btn_px": 19,
      "status_px": 19,
      "warn_px": 24,
      "warn_sub_px": 20,
      "book_px": 21,
      "view_px": 19,
      "_note": "smaller than the HIRAKU screens on purpose (dense business software); the smallest is 19 px so that at camera.from_scale it stays at 18 px or more"
    },
    "win": {
      "license": {
        "x": 300,
        "y": 150,
        "w": 660,
        "h": 440,
        "z": 10
      },
      "modules": {
        "x": 1010,
        "y": 96,
        "w": 640,
        "h": 600,
        "z": 11
      },
      "nolicense": {
        "x": 600,
        "y": 580,
        "w": 700,
        "h": 220,
        "z": 12
      },
      "mesh": {
        "x": 150,
        "y": 110,
        "w": 580,
        "h": 520,
        "z": 20
      },
      "material": {
        "x": 400,
        "y": 160,
        "w": 580,
        "h": 590,
        "z": 21
      },
      "bc": {
        "x": 680,
        "y": 120,
        "w": 580,
        "h": 470,
        "z": 22
      },
      "contact": {
        "x": 960,
        "y": 200,
        "w": 580,
        "h": 510,
        "z": 23
      },
      "solver": {
        "x": 1240,
        "y": 130,
        "w": 600,
        "h": 560,
        "z": 24
      },
      "warn_shape": {
        "x": 330,
        "y": 330,
        "w": 720,
        "h": 210,
        "z": 30
      },
      "warn_conv": {
        "x": 620,
        "y": 455,
        "w": 740,
        "h": 220,
        "z": 31
      },
      "warn_rigid": {
        "x": 240,
        "y": 600,
        "w": 700,
        "h": 200,
        "z": 32
      },
      "_note": "the dialogs, in the order they open; z: stacking order (higher in front). nolicense was at y 470 in the first stills, where it hid the licence window's price line (お見積もりはお問い合わせください); at 580 the line stays in sight"
    },
    "books": {
      "x": 1440,
      "y": 470,
      "w": 430,
      "h": 410,
      "seed": 9,
      "shift_px": 14,
      "tilt_deg": 1.6,
      "drop_px": 160,
      "heights_px": [
        74,
        62,
        70,
        56,
        66,
        60
      ],
      "book_w": 360,
      "_note": "the stack of thick manuals at the right, in front of the application (not turned with it); bottom book first, each shifted by up to shift_px and turned by up to tilt_deg (seeded). Bottom 880, above the band"
    },
    "pointer": {
      "size_px": 30,
      "ring_px": 44,
      "click_dur_s": 0.3,
      "path": [
        {
          "t_s": 0.0,
          "x": 1480,
          "y": 760
        },
        {
          "t_s": 1.8,
          "x": 1480,
          "y": 760
        },
        {
          "t_s": 2.45,
          "x": 1100,
          "y": 375
        },
        {
          "t_s": 3.95,
          "x": 1100,
          "y": 375
        },
        {
          "t_s": 4.75,
          "x": 1752,
          "y": 660
        },
        {
          "t_s": 6.4,
          "x": 1752,
          "y": 660
        }
      ],
      "_note": "the mouse pointer, turned with the application: it moves (eased) from one point to the next of path (t_s, tip x, y) and clicks (a ring) at times.select_s on the fatigue row of the module list and at times.run_s on the solver window's 計算を始める button; the warnings follow the second click"
    },
    "anim": {
      "pop_dur_s": 0.16,
      "pop_from_scale": 0.94,
      "row_dur_s": 0.14,
      "book_dur_s": 0.28,
      "shake_dur_s": 0.3,
      "pop_ease": [
        0.2,
        0.8,
        0.3,
        1
      ],
      "book_ease": [
        0.5,
        0,
        0.75,
        0.4
      ]
    },
    "dim": {
      "peak_alpha": 0.55,
      "fade_dur_s": 0.5,
      "_note": "from times.dim_s the whole picture darkens towards colors.night (to peak_alpha) over fade_dur_s, ending with the cut, to hand over to N08b's dark screen"
    },
    "times": {
      "license_s": 1.0,
      "modules_s": 1.6,
      "module_rows_s": 1.75,
      "module_step_s": 0.11,
      "select_s": 2.55,
      "nolicense_s": 2.75,
      "mesh_s": 3.6,
      "material_s": 3.8,
      "bc_s": 4.0,
      "contact_s": 4.2,
      "solver_s": 4.4,
      "run_s": 4.85,
      "books_s": 4.9,
      "book_step_s": 0.08,
      "warn_shape_s": 4.95,
      "warn_conv_s": 5.25,
      "warn_rigid_s": 5.55,
      "dim_s": 5.9,
      "_note": "license ... nolicense: during CAE のツールは気軽に買えるものではなく (0.9-3.5 s); the ten module rows come in one per module_step (the last at 2.74 s); select: the pointer clicks the fatigue row, and the no-licence dialog answers. mesh ... solver: one settings window every 0.2 s during 使いこなすのも (3.6-4.8 s). run: the pointer clicks 計算を始める, and the warnings follow. books: the manuals drop one per book_step from 4.9 s (they were at 4.5 s in the first stills, where they hid the pointer clicking 計算を始める). warn_*: the warnings during 簡単ではありません (4.9-6.0 s). dim: the darkening, to the end"
    }
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
  "n10": {
    "duration_s": 8.3,
    "_duration_note": "8.3 s is N10's slot; voice: sentence 1 at 0.30-3.18 s, sentence 2 at 3.53-7.62 s",
    "title": {
      "x": 96,
      "y": 64,
      "size_px": 60
    },
    "table": {
      "x": 96,
      "y": 180,
      "w": 1728,
      "head_h": 80,
      "head_gap_px": 12,
      "row_h": 176,
      "row_gap_px": 12,
      "pad_px": 18,
      "radius_px": 10,
      "border_px": 2,
      "_note": "header row, then three rows (step force, tilt angle, frame stress); bottom 180 + 80 + 12 + 3 x 176 + 2 x 12 = 824"
    },
    "cols": {
      "q": 420,
      "claude": 430,
      "hand": 320,
      "cae": 300,
      "diff": 170,
      "tick": 88,
      "_note": "column widths, left to right; they add up to table.w"
    },
    "text": {
      "head_px": 24,
      "head_small_px": 20,
      "q_px": 36,
      "q_sub_px": 24,
      "value_px": 44,
      "value_small_px": 32,
      "note_px": 22,
      "_note": "note_px was 20 in the first stills; raised to 22 so the stress row's 'one of three runs' reads at a glance (value_small_px 34 -> 32 to keep the two hand values with their notes in the row)",
      "diff_px": 36,
      "tick_px": 64
    },
    "src": {
      "x": 96,
      "y": 836,
      "w": 1728,
      "size_px": 20,
      "_note": "two lines (1.5 line height) end at 896, above the band"
    },
    "times": {
      "title_s": 0.15,
      "head_s": 0.3,
      "rows_s": 0.5,
      "row_step_s": 0.75,
      "answer_lag_s": 0.3,
      "check_s": 4.3,
      "check_step_s": 0.45,
      "cae_lag_s": 0.2,
      "diff_lag_s": 0.4,
      "tick_lag_s": 0.55,
      "src_s": 6.0,
      "_note": "rows: first question appears, then one per row_step; answer_lag: Claude's answer after its question (all three by 2.3 s, inside sentence 1). check: HIRAKU's hand value of row 1 (sentence 2 starts at 3.53 s), one row per check_step; the analysis, the difference and the tick follow by cae_lag, diff_lag, tick_lag (last tick at 5.75 s). src: the source lines. From 6.0 s to 8.3 s the screen holds still"
    }
  },
  "n17": {
    "duration_s": 8.8,
    "_note": "drawn by the screens department (web cut.html?cut=n17): the one-page report. 8.8 s is N17's slot in narration draft 6 (2:36.1-2:44.9)",
    "page": {
      "x": 360,
      "y": 80,
      "w": 1200,
      "h": 806,
      "title_px": 34,
      "text_px": 22,
      "_note": "centred; bottom 886"
    },
    "times": {
      "lead_s": 0.3,
      "light_s": 5.8,
      "_note": "light: 次に変えるなら lights up as the voice says 次に何を (about 5.8 s; sentence 2 runs 3.83-8.52 s)"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
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
  "story": {
    "_note": "What each cut must get across in about 3 seconds (one message), and how much it may show. The maker on 2026-11-03: the screens are too busy to come across in the demo. Counts are of things on screen at one time t; elements say what they are with data-kind (label, graph, legend, number, moving) so layout_check can count them. A cut's limits are the defaults unless it lists its own, each with a why.",
    "defaults": {
      "label": 3,
      "graph": 1,
      "legend": 1,
      "number": 1,
      "moving": 3,
      "body_text_px": 24,
      "_note": "label: name tags and ticks; graph: plots; legend: colour bars; number: emphasised figures; moving: things in motion at once; body_text_px: smallest size of text the viewer must read (the global min_text_px 18 stays for notes and sources)"
    },
    "cuts": {
      "n00": {
        "message": "HIRAKU は、役ごとのエージェントが並んで動く (エージェントです)",
        "limits": {
          "label": 7,
          "why": "the seven domain chips under the pets are the point of the cut"
        }
      },
      "n01": {
        "message": "越えられる段差 (100 mm) と、越えられない段差 (250 mm)",
        "limits": {
          "number": 2,
          "why": "the two step heights are the comparison"
        }
      },
      "n02": {
        "message": "傾き・風・悪路・急ハンドル・急ブレーキの、どれで倒れるかが分かる",
        "limits": {
          "label": 5,
          "why": "one tag per test; no figures in the panes"
        }
      },
      "n03": {
        "message": "長く走っても熱くならず、どこまで速いかが分かる"
      },
      "n04": {
        "message": "衝突・振動・組み立てまで、全部を 1 度に見る (まだ赤が 2 枚)",
        "limits": {
          "label": 8,
          "moving": 8,
          "why": "the eight tiles of the grid; each tile shows a picture and a tick or cross only"
        }
      },
      "n05": {
        "message": "HIRAKU — CAE を、すべての作り手へ拓く",
        "limits": {
          "label": 12,
          "moving": 12,
          "why": "the title over the twelve preset tests; the tiles carry pictures, no figures"
        }
      },
      "n06": {
        "message": "作ってみると、うまくいかない",
        "limits": {
          "label": 6,
          "why": "six failures, one tag each, no figures"
        }
      },
      "n07": {
        "message": "つなぎ目が弱く、補強で直る (CAE が作る前に見つける)"
      },
      "n08": {
        "message": "CAE のツールは高く、使いこなすのが難しい",
        "limits": {
          "label": 40,
          "moving": 12,
          "body_text_px": 18,
          "why": "the clutter is the message: a plain CAE tool buried in windows; nothing on it needs to be read"
        }
      },
      "n08b": {
        "message": "小さな会社の CAE の導入率は 16 %",
        "limits": {
          "number": 2,
          "graph": 1,
          "why": "99 % and 16 % side by side, then the two bars"
        }
      },
      "n09": {
        "message": "作るものを伝えると、Claude が必要な解析を選ぶ (7 つの札が灯る)",
        "limits": {
          "label": 7,
          "why": "the seven domains light up"
        }
      },
      "n10": {
        "message": "AI の答えも、CAE ツールで確かめる (3 問とも ✓)",
        "limits": {
          "number": 3,
          "label": 3,
          "why": "one emphasised figure per question (the difference); the rest is small"
        }
      },
      "n11": {
        "message": "予測を書くまで、解析は動かない (hooks が止める)"
      },
      "n12": {
        "message": "手計算と解析が食い違ったら、原因 (単位の間違い) を突き止める",
        "limits": {
          "number": 2,
          "why": "the predicted and the analysed value"
        }
      },
      "n13": {
        "message": "ツールが無ければ、Claude がその場で作り、照合してから使う"
      },
      "n14": {
        "message": "渡したノウハウが、設計のチェックの規則になる"
      },
      "n15": {
        "message": "全部の分野が緑になるまで、設計を直す",
        "limits": {
          "label": 8,
          "moving": 8,
          "why": "the same eight tiles as N04, all turning green"
        }
      },
      "n16": {
        "message": "組み立ての順番まで、3D で確かめる"
      },
      "n17": {
        "message": "結果だけでなく、次に何を変えればよいかを示す"
      },
      "n17b": {
        "message": "部署ごとの Claude Code のセッションが、並んで作った",
        "limits": {
          "label": 8,
          "number": 4,
          "moving": 8,
          "why": "one lane per session; the four counts at the end"
        }
      },
      "n18": {
        "message": "解析の前に予測し、理由を説明する — HIRAKU"
      }
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
      "n03": [
        0.5,
        2.0,
        4.5
      ],
      "n04": [
        0.5,
        1.5,
        3.5
      ],
      "n06": [
        1.0,
        4.0,
        11.0
      ],
      "n07": [
        0.5,
        7.5,
        12.0,
        15.0
      ],
      "n17": [
        0.5,
        6.0,
        8.5
      ],
      "n15": [
        0.5,
        2.5,
        4.8
      ],
      "n16": [
        0.5,
        3.0,
        5.0,
        6.2
      ],
      "n05": [
        0,
        0.5,
        0.8,
        1.0,
        1.15,
        1.4,
        2.9,
        4.5
      ],
      "n08": [
        0.5,
        2.0,
        2.9,
        4.6,
        5.0,
        5.8,
        6.4
      ],
      "n08b": [
        1.5,
        5.2,
        5.8,
        7.5,
        9.8
      ],
      "n10": [
        0.6,
        2.4,
        4.6,
        5.6,
        8.3
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
      },
      {
        "selector": ".door",
        "why": "N05's closed door covers the whole stage as the ground behind the vehicle until it opens (by n05.times.burst_s); it holds no text, and the subtitles are drawn over it"
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
      },
      {
        "cut": "n05",
        "a": "n05.door",
        "b": "n05.hero",
        "why": "the vehicle stands in front of the closed door on purpose; it fades out as the door swings open"
      },
      {
        "cut": "n08",
        "a": "n08.*",
        "b": "n08.*",
        "why": "N08's dialogs, warnings and the stack of manuals pile up over one another and over the main window on purpose: the pile is the picture (too much to learn)"
      }
    ]
  }
};

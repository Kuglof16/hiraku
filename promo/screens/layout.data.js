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
      "size_px": 210,
      "in_dur_s": 0.5,
      "in_step_s": 0.05,
      "_note": "the letters come in one after another from times.name_s, in_step_s apart, each in in_dur_s (0.7 s and 0.07 s until 2026-11-03), so all six stand by 0.25 + 5 x 0.05 + 0.5 = 1.0 s, while the voice says the name (from 0.3 s)"
    },
    "rule": {
      "y": 430,
      "w": 400,
      "h": 2,
      "_note": "centred"
    },
    "tagline": {
      "y": 458,
      "size_px": 36,
      "text": "作る前に、CAE で確かめる AI エージェント",
      "_note": "added 2026-11-03: one line under the rule that says what HIRAKU is (the logo and the pets alone did not say it). Centred, ink-soft; bottom about 458 + 1.3 x 36 = 505, above pets.y"
    },
    "pets": {
      "y": 544,
      "w": 160,
      "h": 180,
      "pitch_px": 210,
      "_note": "the seven CAE domain pets of promo/pets/pet3d.js (v8) stand in one row over their chips until their chip lights, in the order the chips light. Box k is w x h at y, centred on cx_k = 960 - span / 2 + w / 2 + k x pitch_px, span = 6 x pitch_px + w = 1420 (x 250 to 1670, inside safe). Each pet is a 2D canvas: one WebGL renderer off the page (the size of one row box) draws each pet alone and the frame is copied into that pet's canvas, so no pet reaches past its box. Until the title pets (2026-11-03) the row held all ten pets (pitch 168, a 36 px gap after the third) and lead, designer and packager wore name tags under them"
    },
    "title_pets": {
      "h_em": 0.6,
      "lift_px": 0,
      "_note": "the size of a pet on (and around) the name, and where the feet stand on a letter. A pet box on the name, on the floor or behind a letter is h_em x the name's font size high and as wide as pets.w / pets.h of that (at 0.6 em the body is about 0.4 of the letter height, so the letters stay readable under it). A seat {seat: letter, at} in a route (n00.cast) is on the top edge of that letter of HIRAKU (0 = H ... 5 = U): at is where the feet stand across the letter's ink, 0 = its left edge, 1 = its right edge (the ink box is measured with canvas measureText (actualBoundingBoxLeft / Right) in the name's font and size, from the left edge of the letter's own span); the feet stand on the letter's ink top (baseline - actualBoundingBoxAscent; baseline = the name's top + (font size - (fontBoundingBoxAscent + fontBoundingBoxDescent)) / 2 + fontBoundingBoxAscent, as line-height is 1) plus lift_px up, and ride the letter's play (n00.play). Until 2026-11-04 (pets everywhere) all ten sat here in seats; now four end on the name and the rest live about the screen (n00.cast)"
    },
    "camera": {
      "fov_deg": 24,
      "azimuth_deg": 24,
      "elevation_deg": 12,
      "_note": "every pet is seen the same way: a camera fitted to pet3d's FRAME box as promo/pets/viewer.html does (distance = max(FRAME.h / 2, FRAME.w / 2 / aspect) / tan(fov / 2) x 1.04 + 0.6), turned azimuth_deg about the vertical and raised elevation_deg"
    },
    "chip": {
      "gap_above_px": 12,
      "h": 46,
      "size_px": 24,
      "_note": "the seven domain chips, centred under their pets. 27 px until 2026-11-03; 24 px (story body_text_px) so 強度信頼性 (about 178 px wide) clears its neighbours at pitch 168"
    },
    "motion": {
      "pass_dur_s": 0.9,
      "hop_period_s": 0.9,
      "_note": "a domain pet stands in the row over its chip until its chip lights at light_s + k x step_s (it shows the tick then), and goes off by its route (n00.cast). pass_dur_s and hop_period_s (pet3d.js's pass period, 0.9 s) are also read by the app's cut view (web/js/cut_pets.js). Every pet's clock is shifted by idle_shift_s x index so they do not blink together (also N18's). The pose is pet.update(clock + shift, state, camera, pose terms) and the place a function of t, with t from timeline.js only",
      "idle_shift_s": 1.3
    },
    "play": {
      "from_s": 1.4,
      "still_s": 7.4,
      "hop": {
        "crouch_s": 0.18,
        "crouch": 0.22,
        "air_s": 0.52,
        "over_px": 40,
        "stretch": 0.16,
        "land_sq": 0.3,
        "land_s": 0.12,
        "bounces": [
          [
            10,
            0.16
          ]
        ],
        "settle_s": 0.3,
        "lean_k": 0.8,
        "antenna_k": 2.0,
        "antenna_lag_s": 0.06,
        "look_up": 0.03,
        "shadow_air": 0.45,
        "weight": 1.0
      },
      "land": {
        "dur_s": 0.45,
        "sink_px": 8,
        "squash": 0.05
      },
      "shake": {
        "letter": 4,
        "start_s": 5.05,
        "dur_s": 0.55,
        "hz": 8,
        "rot_deg": 3,
        "dx_px": 20,
        "tilt_deg": 5,
        "over": 0.15
      },
      "push": {
        "roles": [
          "cae.thermal"
        ],
        "start_s": 5.75,
        "dur_s": 0.6,
        "lean_deg": 16,
        "step_px": [
          16
        ],
        "lag_s": [
          0
        ],
        "wind_s": 0.15,
        "wind_deg": 7,
        "wind_px": 5,
        "push_in": 0.4,
        "hold_to": 0.6,
        "over": 0.25,
        "letter_from": 0.15,
        "letter_over": 0.12
      },
      "stop": {
        "from_s": 6.99,
        "still_s": 7.37,
        "_note": "every pet's clock is t + motion.idle_shift_s x index, slowing to a halt from from_s to still_s (the speed falls linearly to 0), so the breathing, the report sway and the blinks stop. pet3d blinks while clock % 4.3 < 0.16; the slowing clocks run from from_s to from_s + (still_s - from_s) / 2 = 7.18, which for all ten (shifts 0, 1.3, ... 11.7) lies between two blinks (index 8 ends one at about 6.96 s, index 1 starts one at 7.3 s), so no pet holds still with its eyes shut"
      },
      "_note": "the play with the letters, after the name has been said (the voice says HIRAKU from 0.3 s; the letters stand still and in line until from_s, and everything is back in line and still at still_s). hop: the defaults of every hop in n00.cast (pet3d.js anim.hop: a crouch of crouch_s squashing to crouch, a flight of air_s on a parabola in time topping out over_px above the higher end, stretched by stretch x its speed, a landing squash land_sq over land_s, the small bounces [up_px, dur_s] each landing softer, a settle of settle_s; the antenna follows antenna_lag_s behind; look_up: the eyes go up while crouching; shadow_air: the contact shadow in the air; weight: how hard it lands on a letter). land: when a pet lands on a letter the letter sinks sink_px x weight and comes back (d(u) = sin(3 pi u) (1 - u)^2 scaled to a peak of 1, u = time since landing / dur_s), squashing by squash x weight x d. shake: after the NVH pet lands, the letter (index into HIRAKU, 4 = K) shakes (rot_deg at hz, dying out) and slides dx_px right and tilts tilt_deg in dur_s, easing out past it by over and back; it stays so until push. push: the pet on the next letter (roles) winds up for wind_s (lean back wind_deg, step back wind_px), shoves (lean_deg, step_px left) in push_in of dur_s, holds to hold_to and comes back with an overshoot of over; the letter starts back at letter_from of dur_s and goes back in line, overshooting by letter_over. stop: the clocks slow to a halt (see its note)"
    },
    "times": {
      "name_s": 0.25,
      "tagline_s": 1.0,
      "row_s": 1.4,
      "light_s": 2.0,
      "step_s": 0.42,
      "_note": "name: first letter; tagline: the line under the rule fades in (0.6 s); row: pets, chips and tags come in, unlit (0.5 s; 1.1 s until 2026-11-03, moved for the tagline); light: first chip lights; step: gap between chips (light_s and step_s are read by bgm.py)"
    },
    "cast": {
      "lead": {
        "_who": "walks in from the left along the foot of the name, meets handling and both bow, walks on to the H, turns to the viewer",
        "start": {
          "at": [
            160,
            346
          ],
          "yaw_deg": 110,
          "alpha": 0
        },
        "beats": [
          {
            "do": "fade",
            "t": 1.3,
            "to": 1,
            "dur_s": 0.45
          },
          {
            "do": "walk",
            "t": 1.3,
            "to": [
              215,
              346
            ],
            "dur_s": 2.45,
            "swing": 0.4
          },
          {
            "do": "act",
            "t": 3.95,
            "kind": "bow",
            "dur_s": 0.7,
            "amp_deg": 30
          },
          {
            "do": "walk",
            "t": 4.7,
            "to": [
              364,
              346
            ],
            "dur_s": 2.1,
            "swing": 0.7
          },
          {
            "do": "turn",
            "t": 6.85,
            "yaw_deg": 12,
            "dur_s": 0.45
          }
        ]
      },
      "designer": {
        "_who": "lives behind the H: peeks out from behind its left stem, goes back, and leans out again to stay",
        "layer": "behind",
        "start": {
          "at": [
            502,
            339
          ],
          "yaw_deg": 0,
          "alpha": 0
        },
        "beats": [
          {
            "do": "fade",
            "t": 1.5,
            "to": 1,
            "dur_s": 0.3
          },
          {
            "do": "act",
            "t": 1.8,
            "kind": "peek",
            "dur_s": 1.5,
            "roll_deg": 24,
            "dx_px": -44,
            "amp_deg": 20
          },
          {
            "do": "act",
            "t": 4.6,
            "kind": "peek",
            "dur_s": 1.2,
            "roll_deg": 20,
            "dx_px": -40,
            "amp_deg": 18,
            "stay": true
          }
        ]
      },
      "packager": {
        "_who": "carries its box in from the right along the foot of the name, stops by the U, turns to the viewer and turns the box about",
        "start": {
          "at": [
            1760,
            346
          ],
          "yaw_deg": -66,
          "alpha": 0
        },
        "beats": [
          {
            "do": "fade",
            "t": 1.6,
            "to": 1,
            "dur_s": 0.45
          },
          {
            "do": "walk",
            "t": 1.6,
            "to": [
              1590,
              346
            ],
            "dur_s": 3.9,
            "swing": 0.55,
            "carry_deg": 35
          },
          {
            "do": "turn",
            "t": 5.6,
            "yaw_deg": -12,
            "dur_s": 0.45
          },
          {
            "do": "act",
            "t": 6.1,
            "kind": "fiddle",
            "dur_s": 0.7,
            "amp_deg": 25
          }
        ]
      },
      "cae.handling": {
        "_who": "steady: hops up off the left end of the row to the foot of the name, meets lead and bows, walks to the left and sits",
        "start": {
          "at": {
            "row": 0
          },
          "h": "row"
        },
        "beats": [
          {
            "do": "tick",
            "t": 2.0,
            "dur_s": 0.9
          },
          {
            "do": "hop",
            "t": 2.08,
            "to": [
              350,
              346
            ],
            "h": "seat",
            "yaw_deg": 60,
            "crouch_s": 0.18,
            "air_s": 0.55,
            "over_px": 60,
            "bounces": [
              [
                8,
                0.14
              ]
            ],
            "settle_s": 0.24
          },
          {
            "do": "walk",
            "t": 3.1,
            "to": [
              300,
              346
            ],
            "dur_s": 0.9,
            "swing": 0.7
          },
          {
            "do": "act",
            "t": 4.05,
            "kind": "bow",
            "dur_s": 0.7,
            "amp_deg": 26
          },
          {
            "do": "walk",
            "t": 4.75,
            "to": [
              165,
              346
            ],
            "dur_s": 1.4,
            "swing": 0.7
          },
          {
            "do": "turn",
            "t": 6.2,
            "yaw_deg": 10,
            "dur_s": 0.4
          },
          {
            "do": "sit",
            "t": 6.5,
            "to": 1,
            "dur_s": 0.5,
            "thump": 0.1
          }
        ]
      },
      "cae.powertrain": {
        "_who": "eager: short crouch, the highest jump, two bounces, onto the H; nods, looks about",
        "start": {
          "at": {
            "row": 1
          },
          "h": "row"
        },
        "beats": [
          {
            "do": "tick",
            "t": 2.42,
            "dur_s": 0.2
          },
          {
            "do": "hop",
            "t": 2.45,
            "to": {
              "seat": 0,
              "at": 0.6
            },
            "h": "seat",
            "yaw_deg": 8,
            "crouch_s": 0.13,
            "crouch": 0.25,
            "air_s": 0.4,
            "over_px": 26,
            "stretch": 0.22,
            "bounces": [
              [
                12,
                0.16
              ],
              [
                4,
                0.1
              ]
            ],
            "weight": 1.1
          },
          {
            "do": "act",
            "t": 4.4,
            "kind": "nod",
            "dur_s": 0.6,
            "amp_deg": 8
          },
          {
            "do": "act",
            "t": 6.0,
            "kind": "look",
            "dur_s": 0.7,
            "amp_deg": 16
          }
        ]
      },
      "cae.aero": {
        "_who": "light: slow crouch, a floating jump onto the I, lands soft; looks about",
        "start": {
          "at": {
            "row": 2
          },
          "h": "row"
        },
        "beats": [
          {
            "do": "tick",
            "t": 2.84,
            "dur_s": 0.3
          },
          {
            "do": "hop",
            "t": 2.9,
            "to": {
              "seat": 1,
              "at": 0.5
            },
            "h": "seat",
            "yaw_deg": -8,
            "crouch_s": 0.2,
            "crouch": 0.16,
            "air_s": 0.4,
            "over_px": 24,
            "stretch": 0.14,
            "land_sq": 0.2,
            "bounces": [],
            "weight": 0.6
          },
          {
            "do": "act",
            "t": 5.0,
            "kind": "look",
            "dur_s": 0.8,
            "amp_deg": 20
          }
        ]
      },
      "cae.crash": {
        "_who": "heavy: a low-arched jump onto the R that lands hard, then turns its prop about",
        "start": {
          "at": {
            "row": 3
          },
          "h": "row"
        },
        "beats": [
          {
            "do": "tick",
            "t": 3.26,
            "dur_s": 0.9
          },
          {
            "do": "hop",
            "t": 3.3,
            "to": {
              "seat": 2,
              "at": 0.45
            },
            "h": "seat",
            "yaw_deg": 4,
            "crouch_s": 0.24,
            "crouch": 0.28,
            "air_s": 0.36,
            "over_px": 16,
            "land_sq": 0.4,
            "bounces": [
              [
                6,
                0.12
              ]
            ],
            "weight": 1.4
          },
          {
            "do": "act",
            "t": 5.2,
            "kind": "fiddle",
            "dur_s": 0.7,
            "amp_deg": 25
          }
        ]
      },
      "cae.strength": {
        "_who": "careful: a long crouch, then a measured jump onto the A; turns and looks about",
        "start": {
          "at": {
            "row": 4
          },
          "h": "row"
        },
        "beats": [
          {
            "do": "tick",
            "t": 3.68,
            "dur_s": 0.9
          },
          {
            "do": "hop",
            "t": 3.85,
            "to": {
              "seat": 3,
              "at": 0.5
            },
            "h": "seat",
            "yaw_deg": 14,
            "crouch_s": 0.26,
            "crouch": 0.2,
            "air_s": 0.36,
            "over_px": 34,
            "bounces": [
              [
                6,
                0.12
              ]
            ],
            "settle_s": 0.3
          },
          {
            "do": "turn",
            "t": 5.25,
            "yaw_deg": -15,
            "dur_s": 0.45
          },
          {
            "do": "act",
            "t": 6.0,
            "kind": "look",
            "dur_s": 0.7,
            "amp_deg": 16
          }
        ]
      },
      "cae.nvh": {
        "_who": "springy: quick crouch, a bounce, onto the K, which then shakes out of line",
        "start": {
          "at": {
            "row": 5
          },
          "h": "row"
        },
        "beats": [
          {
            "do": "tick",
            "t": 4.1,
            "dur_s": 0.2
          },
          {
            "do": "hop",
            "t": 4.12,
            "to": {
              "seat": 4,
              "at": 0.85
            },
            "h": "seat",
            "yaw_deg": 0,
            "crouch_s": 0.12,
            "crouch": 0.2,
            "air_s": 0.38,
            "over_px": 22,
            "stretch": 0.2,
            "bounces": [
              [
                10,
                0.14
              ]
            ],
            "settle_s": 0.2,
            "weight": 0.9
          },
          {
            "do": "act",
            "t": 6.5,
            "kind": "sway",
            "dur_s": 0.5,
            "amp_deg": 5
          }
        ]
      },
      "cae.thermal": {
        "_who": "quick, the last to go: onto the U, then pushes the K back in line",
        "start": {
          "at": {
            "row": 6
          },
          "h": "row"
        },
        "beats": [
          {
            "do": "tick",
            "t": 4.52,
            "dur_s": 0.2
          },
          {
            "do": "hop",
            "t": 4.52,
            "to": {
              "seat": 5,
              "at": 0.3
            },
            "h": "seat",
            "yaw_deg": -10,
            "crouch_s": 0.15,
            "crouch": 0.2,
            "air_s": 0.4,
            "over_px": 20,
            "bounces": [
              [
                8,
                0.14
              ]
            ],
            "settle_s": 0.14,
            "weight": 0.9
          }
        ]
      }
    },
    "_cast_note": "where each of the ten pets lives and what it does (2026-11-04: pets everywhere, not only on the name). start: the first place (at), the box height (h: seat = title_pets.h_em x the name size, row = pets.h; default seat), yaw_deg (0 faces the viewer, + turns the front to screen right) and alpha; layer: front (default) or behind (under the name: it peeks out from behind a letter and never covers it). beats: pet3d.js anim.route, each {do, t (start, s), ...} in time order (walk: on foot in whole steps that match the ground covered, turned along the way, the speed easing in and out, swing the legs' swing, carry_deg the prop held up in front; hop: one pet3d anim.hop there, n00.play.hop with the beat's values on top, the box going to h; turn; sit (to 0..1, thump: a squash as it lands); act: look, nod, sway, fiddle, bow, peek (leans out roll_deg and dx_px, back unless stay); fade; tick: the tick over the head when its chip lights). Places: [x, y] the feet in design px (the floor under the chips at y 893 in front and 884 behind: a seat box 126 high then ends at 907.6 and 898.6, above the subtitle band), {row: k} the feet in row spot k (the domain pets' places over their chips at the start), {seat: letter, at} on a letter (title_pets). Who and where (2026-11-05: all ten end about the name): lead and packager walk in from the left and right along the foot of the name (y 346; packager carrying its box); handling hops up to the foot of the name, bows to lead as they pass, and sits at the left; powertrain, aero, crash, strength, nvh and thermal jump onto H, I, R, A, K and U (each onto the letter nearest its chip); designer peeks from behind the H. No pet is on or in front of the name while the voice says it (0.3-1.3 s)"
  },
  "n01": {
    "duration_s": 4.0,
    "_note": "drawn by the screens department (web cut.html?cut=n01). 4.0 s is N01's slot in the narration script (0:07-0:11)",
    "view": {
      "x": 96,
      "y": 96,
      "w": 1400,
      "h": 790,
      "_note": "the 3D view; bottom 886, above the subtitle band"
    },
    "pane": {
      "x": 1520,
      "y": 96,
      "w": 304,
      "h": 790,
      "_note": "result cards in a narrow column on the right (the 3D is what is read)"
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
      "tile_w": 560,
      "tile_h": 383,
      "gap_x": 24,
      "gap_y": 24,
      "_note": "five tests and the legend over the full width (right edge 1824); bottom 886, above the subtitle band"
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
      },
      "verdict": {
        "of_label": 2,
        "_note": "問題なし / 直しが要る: at most this many times the pane name's size, and no wider than the pane"
      }
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
    "_note": "drawn by the screens department (web cut.html?cut=n04). 4.0 s is N04's slot in narration draft 6 (0:24.6-0:28.6). The same grid of eight tiles as N15",
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
    "feature": {
      "x": 96,
      "y": 160,
      "w": 852,
      "h": 726,
      "_note": "the one domain shown large"
    },
    "list": {
      "x": 972,
      "y": 160,
      "w": 852,
      "h": 726,
      "cols": 2,
      "rows": 4,
      "gap_x": 24,
      "gap_y": 24,
      "_note": "the other seven as a small list, row by row: each slot 414 x 163 (the same as the grid gave before)"
    },
    "assembly": {
      "x": 96,
      "y": 160,
      "w": 1728,
      "h": 726,
      "_note": "the box of the assembly fast-forward from times.assembly_s: the grid's own box, so nothing jumps up or leaves the right side empty (it borrowed n16.view before)"
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
        "w": 84,
        "h": 84,
        "right": 12,
        "bottom": 12,
        "feature": {
          "w": 160,
          "h": 160
        },
        "_note": "the corner of the tile where the role's 3D pet stands (promo/pets/pet3d.js: cae.<domain>, packager for assembly); feature: its size in the large tile. A pet holds still (idle) and moves only for a short reaction when its tile changes: pass when it turns green, mismatch when it turns red, working while its tile is mid iteration. Posed from the cut time t only"
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
      "_note": "lead: still before anything moves. crash / nvh / assembly: when the voice says 衝突 (0.56 s), 振動 (about 1.2 s) and 組み立て (2.06 s), set from the synthesized voice's pauses; the maker's recording runs 0.41-3.52 s with 組み立て from about 2.08 s, so the times stand. Each named tile lights up in its own colour from the record (the video run 20261103-153914, version 1: crash and NVH green, assembly red); from assembly_s to the end the grid gives way to the assembly fast-forward in the n16.view box"
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
    "feature": {
      "x": 96,
      "y": 160,
      "w": 852,
      "h": 726,
      "_note": "the one domain shown large"
    },
    "list": {
      "x": 972,
      "y": 160,
      "w": 852,
      "h": 726,
      "cols": 2,
      "rows": 4,
      "gap_x": 24,
      "gap_y": 24,
      "_note": "the other seven as a small list, row by row: each slot 414 x 163 (the same as the grid gave before)"
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
        "w": 84,
        "h": 84,
        "right": 12,
        "bottom": 12,
        "feature": {
          "w": 160,
          "h": 160
        },
        "_note": "the corner of the tile where the role's 3D pet stands (promo/pets/pet3d.js: cae.<domain>, packager for assembly); feature: its size in the large tile. A pet holds still (idle) and moves only for a short reaction when its tile changes: pass when it turns green, mismatch when it turns red, working while its tile is mid iteration. Posed from the cut time t only"
      },
      "rate": {
        "size_px": 18
      }
    },
    "times": {
      "lead_s": 0.3,
      "rounds_from_s": 0.4,
      "all_green_s": 4.0,
      "_note": "rounds_from: the rounds of design changes start to play; all_green: every tile is green (tick) and the grid holds still to the end. The voice (the maker's recording) runs 0.42-4.11 s"
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
      "w": 1400,
      "h": 790,
      "_note": "the 3D assembly, the same box as n01.view; bottom 886"
    },
    "seek": {
      "x": 120,
      "y": 814,
      "w": 1352,
      "h": 48,
      "_note": "the seek bar over the lower edge of the view, 24 px inside its sides and bottom"
    },
    "steps": {
      "x": 1520,
      "y": 96,
      "w": 304,
      "h": 790,
      "title_px": 30,
      "text_px": 22,
      "row_gap": 12,
      "_note": "the list of assembly steps in a narrow column on the right"
    },
    "times": {
      "lead_s": 0.3,
      "steps_from_s": 0.5,
      "steps_to_s": 4.2,
      "scrub_s": 4.4,
      "scrub_to_s": 5.8,
      "_note": "steps advance one at a time from steps_from to steps_to (how many comes from the record); from scrub to scrub_to the seek bar is dragged back and forward by hand, as the voice says 確認できます (from about 4.5 s); then still to the end. The voice (the maker's recording) runs 0.42-5.53 s; 組み立て手順を starts at about 2.72 s"
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
        "y": 58,
        "size_px": 124
      },
      "tag": {
        "y": 186,
        "size_px": 42
      },
      "gloss": {
        "y": 244,
        "size_px": 24,
        "text": "CAE = コンピュータで設計を試す技術",
        "_note": "added 2026-11-03: says in small type what CAE is, under the tag line, fading in at times.gloss_s. Bottom 244 + 1.2 x 24 = 272.8, above grid.y (280); the tag (bottom 186 + 1.2 x 42 = 236.4) and the name (58 to 182) moved up 14 and 4 px to make room (they were at 200 and 62)"
      }
    },
    "times": {
      "burst_s": 1.25,
      "tiles_s": 1.35,
      "tile_in_s": 0.6,
      "tile_stagger_s": 0.05,
      "name_s": 1.75,
      "tag_s": 2.3,
      "gloss_s": 2.4,
      "_gloss_note": "added 2026-11-03: the small line under the tag (title.gloss) fades up over 0.6 s, 0.1 s after the tag starts",
      "door_open_s": 0.55,
      "latch_s": 0.38,
      "mesh_in_s": 0.2,
      "mesh_out_s": 0.78,
      "points_s": 0.0,
      "hero_in_s": 0.3,
      "points_out_s": 0.36,
      "_points_note": "added 2026-11-03 (v5). points: the cloud of nodes starts to gather onto the vehicle (hero_points); hero_in: the vehicle's picture fades in over hero_points.hero_in_dur_s once the points have gathered (in by 0.46 s); points_out: the points fade over hero_points.fade_dur_s, handing over to the mesh lines (mesh_in_s). Before v5 the picture was there from 0 s",
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
    "hero_points": {
      "seed": 13,
      "outline_keep": 0.8,
      "jitter": 0.35,
      "scatter_min_px": 160,
      "scatter_max_px": 760,
      "margin_px": 24,
      "gather_dur_s": 0.22,
      "stagger_s": 0.08,
      "ease": [
        0.12,
        0.75,
        0.3,
        1
      ],
      "dot_px": 4.6,
      "node_dot_px": 6.4,
      "start_alpha": 0.35,
      "fade_dur_s": 0.22,
      "hero_in_dur_s": 0.16,
      "mask": {
        "cell_px": 10,
        "cols": 150,
        "rows": 75,
        "src": "media/n05_hero_2x.png",
        "threshold": 128,
        "cover": 0.5,
        "rows_hex": [
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000003000000000000000000000",
          "00000000000000007800000000000000000000",
          "0000000000000000fc00000000000000000000",
          "00000000000000007801e00000000000000000",
          "00000000000000007803e00000000000000000",
          "00000000000000003003f00000000000000000",
          "00000000000000007c03e60000000000000000",
          "0000000000000000fc01fff000000000000000",
          "0000000000000000fc01ffffc0000000000000",
          "0000000000000000fc1ffffffe000000000000",
          "0000000000000038feffffffff000000000000",
          "0000000000000040fcffffffff000000000000",
          "0000000000000044fcffffffff000000000000",
          "00000000000000007cffffffff000000000000",
          "00000000000000687fffffffff000000000000",
          "00000000000000707fffffffff000000000000",
          "00000000000000007fffffffff800000000000",
          "0000000000000080ffffffffffe00000000000",
          "0000000000000007fffffffffff00000000000",
          "000000000000013ffffffffffff00000000000",
          "000000000000027ffff7ffffffb00000000000",
          "00000000003ff8ffffffffffff100000000000",
          "00000000007fffffffffffff7e500000000000",
          "0000000001fffffffffffffc7e900000000000",
          "0000000001fffffffffffff1fd900000000000",
          "0000000003ffffffffffffe0ff900000000000",
          "0000000007fffffffffffff0fd300000000000",
          "0000000007ffffffffffffc0fd200000000000",
          "0000000007ffffffffffff007d600000000000",
          "000000000ffffffffffffc007f400000000000",
          "000000000ffffffffffff0007fc00000000000",
          "000000000fffffffffff00003f800000000000",
          "000000000ffffffffffe000006000000000000",
          "0000000007ffffffffff000000000000000000",
          "0000000007ffffffffff000000000000000000",
          "0000000003fffff3ffe7000000000000000000",
          "0000000003ffdfcfff87000000000000000000",
          "0000000000fff63fff89800000000000000000",
          "00000000003c7cfeff51800000000000000000",
          "0000000000001ff8ff71000000000000000000",
          "00000000000003e1ffe1000000000000000000",
          "0000000000000081fef3000000000000000000",
          "0000000000000001ffeb000000000000000000",
          "0000000000000001fe43000000000000000000",
          "0000000000000001fc46000000000000000000",
          "0000000000000001fc46000000000000000000",
          "0000000000000000fe0c000000000000000000",
          "0000000000000000ffb8000000000000000000",
          "00000000000000007ff0000000000000000000",
          "00000000000000003fe0000000000000000000",
          "00000000000000000fc0000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000",
          "00000000000000000000000000000000000000"
        ],
        "_note": "where the vehicle's picture is, cell by cell in the hero box (cols x rows cells of cell_px design px; the picture fitted in the box as the page draws it, contain). A cell is set when at least cover of its pixels have alpha above threshold. One hex string per row, the first column in the high bit of the first digit, padded to whole digits. Computed from src by hero_mask() in promo/tests/test_layout.py, which checks it against the PNG (the page cannot read the PNG's pixels: a file:// picture taints the canvas)"
      },
      "_note": "the vehicle gathers from a cloud of nodes (v5): the points are the nodes of hero_mesh (same seed and rows, so they land where the mesh lines will run) that fall on a set cell of mask, plus points on the outline (set cells next to an unset one, each kept with outline_keep and moved by up to jitter x cell_px about the cell's centre, seeded). Each starts at a seeded place scatter_*_px from where it lands (margin_px inside the stage and above the subtitle band; a place outside is drawn again, so no points pile up on the edges), at start_alpha, and comes in over gather_dur_s (ease) after a seeded delay of up to stagger_s from times.points_s, growing to full brightness. dot_px / node_dot_px: radius of an outline point / a node. All are in by points_s + stagger_s + gather_dur_s = times.hero_in_s; then the picture fades in (times.hero_in_s, hero_in_dur_s) and the points fade from times.points_out_s over fade_dur_s. Only with a picture; the line drawing has no points"
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
      "reading_of_title": 2,
      "ok_of_reading": 0.55,
      "_note": "battery charge and temperature, then speed, as graphs in the right column. reading_of_title: the largest size of the reading, a multiple of title_px (it is also held to the popup's width); ok_of_reading: the size of the tick line under it (✓ 上限より下), a share of the reading's size"
    },
    "loop": {
      "ease": "in-out",
      "_note": "the long drive's time runs through the record with this ease (in-out or linear)"
    },
    "times": {
      "lead_s": 0.3,
      "loop_to_s": 2.6,
      "ok_s": 2.3,
      "accel_s": 2.9,
      "_note": "0.3-2.6 s the long loop (eased in and out), the tick (below the limit) from ok_s 2.3 s as the loop slows, and it stays through the acceleration; the voice says オーバーヒートしないか at 1.44-2.56 s in the maker's recording (battery and heat), from 2.9 s the acceleration (speed); the voice says どこまで速く from about 2.9 s (measured from its pauses)"
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
    "feature": {
      "x": 96,
      "y": 96,
      "w": 1104,
      "h": 790,
      "_note": "the one design shown large"
    },
    "list": {
      "x": 1224,
      "y": 96,
      "w": 600,
      "h": 790,
      "cols": 2,
      "rows": 3,
      "gap_x": 24,
      "gap_y": 24,
      "_note": "the six designs as a small list, row by row: each slot (w - (cols-1) gap_x) / cols x (h - (rows-1) gap_y) / rows = 288 x 247"
    },
    "tests": [
      "tilt_over",
      "step_stuck",
      "step_break",
      "battery_hot",
      "parts_clash",
      "wind"
    ],
    "_tests_note": "wind (F1-gust58-beyond) in place of rollover: the video run 20261103-153914 has no S3 case past the limit, and the voice does not name the tile. Order: the tile in the large box while the voice says 動くか is stuck at the step, while it says 壊れないか the frame breaks (recorded voice)",
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
      "_note": "the sixth tile starts at 3.35 s and lands at 3.85 s, all within the first two sentences (0.41-2.79, 3.35-5.61 s in the maker's recording)"
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
      "of_view_h": 0.08,
      "_note": "inside the view, top left: 補強の前 / 補強の後. Text size: view.h x of_view_h (63 px), at least size_px"
    },
    "ring": {
      "of_view_h": 0.08,
      "_note": "the ring around the highest element: its size while the camera is far, as a share of view.h; it grows as the camera moves in and turns green on the reinforced version"
    },
    "times": {
      "lead_s": 0.3,
      "turn_to_s": 5.5,
      "zoom_s": 5.5,
      "zoom_to_s": 7.5,
      "swap_s": 11.0,
      "swap_to_s": 13.0,
      "_note": "the vehicle turns with the stress colours during sentences 1-2 (the maker's recorded voice, from the cut's start: 0.30-3.76, 4.11-8.05 s); it moves in on the highest element during sentence 2 (突き止める) and is in by 7.5 s, before sentence 2 ends; the reinforced version's colours come in as sentence 3 says 確かめ、分析する (8.40-14.42 s) and stay while it says それが CAE です"
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
    "wait": {
      "text": "これから確かめる",
      "size_px": 22,
      "_note": "added 2026-11-03: replaces the '?' placeholders. One line in the HIRAKU hand cell of each row until its value comes in (data-kind note: a placeholder, not body text); the analysis and difference cells stay empty until theirs (the difference column, 170 px, is too narrow for the line)"
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
  "n09": {
    "duration_s": 5.6,
    "_note": "drawn by the screens department (web cut.html?cut=n09) from the intake department's real run (Claude reads the request). 5.6 s is N09's slot in narration draft 6. Only the off-road vehicle, no other products",
    "request": {
      "x": 96,
      "y": 96,
      "w": 1728,
      "h": 120,
      "title_px": 22,
      "text_px": 30,
      "_note": "the maker's request in their own words, one or two lines"
    },
    "view": {
      "x": 96,
      "y": 240,
      "w": 1100,
      "h": 646,
      "_note": "the vehicle in 3D; bottom 886"
    },
    "chips": {
      "x": 1220,
      "y": 240,
      "w": 604,
      "h": 646,
      "text_px": 30,
      "row_gap": 16,
      "_note": "the seven domains and assembly, one chip a row; a chip lights when Claude picks it, with its reason in one short line (text_px x 0.7, at least min_text_px)"
    },
    "times": {
      "lead_s": 0.3,
      "request_s": 0.3,
      "chips_from_s": 2.2,
      "chip_step_s": 0.35,
      "_note": "the request is there from the start; the chips light one at a time from chips_from_s, in the order of the record, as the voice says 必要な解析を選びます (the maker's recording runs 0.30-5.33 s); with eight chips the last lights at 4.65 s"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
    }
  },
  "n11": {
    "duration_s": 14.6,
    "_note": "drawn by the screens department (web cut.html?cut=n11) from a scripted run: how HIRAKU works, not Claude making a mistake. 14.6 s is N11's slot in narration draft 6",
    "memo": {
      "x": 96,
      "y": 96,
      "w": 560,
      "h": 790,
      "title_px": 30,
      "text_px": 24,
      "_note": "the hand calculation memo (the prediction), written line by line"
    },
    "log": {
      "x": 680,
      "y": 96,
      "w": 1144,
      "h": 360,
      "title_px": 30,
      "text_px": 24,
      "_note": "the run's log: the analysis called without a prediction is stopped (hooks), then runs once the prediction is written"
    },
    "result": {
      "x": 680,
      "y": 480,
      "w": 1144,
      "h": 406,
      "title_px": 30,
      "text_px": 24,
      "_note": "the analysis result in 3D or a value. In the record (scripted run 20261103-223035) the unit mistake (62 mm meant as 62 cm) went into the hand calculation (the prediction, 40.6 deg); the analysis used the design's load height (31.7 deg) and is right. The screen shows the 62 mm in the settings and never calls the analysis wrong"
    },
    "times": {
      "lead_s": 0.3,
      "plausible_s": 1.5,
      "memo_s": 4.9,
      "stop_s": 9.6,
      "run_s": 11.6,
      "_note": "the maker's recording: sentence 1 0.30-4.56 s (the settings card with the mistaken 62 mm shows from plausible_s), sentence 2 4.91-9.20 s (the memo is written from memo_s), sentence 3 9.55-12.81 s (the call without a prediction is stopped at stop_s; after the prediction it runs from run_s)"
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
  "n12": {
    "duration_s": 9.2,
    "_note": "drawn by the screens department (web cut.html?cut=n12) from the same scripted run as N11: the comparison catches the unit mistake in the settings. 9.2 s is N12's slot in narration draft 6. Numbers come from the record",
    "table": {
      "x": 96,
      "y": 96,
      "w": 1728,
      "h": 560,
      "title_px": 30,
      "text_px": 28,
      "value_px": 56,
      "_note": "the comparison: the prediction (hand calculation) and the analysis side by side, their difference; red while they disagree (40.6 / 31.7 deg, -22.0 %), green once fixed (33.6 / 31.7 deg, -5.6 %). Numbers from the record"
    },
    "cause": {
      "x": 96,
      "y": 680,
      "w": 1728,
      "h": 206,
      "title_px": 30,
      "text_px": 28,
      "_note": "the recorded cause in one line: the prediction took the load height 62 mm where 62 cm was meant; fixed, the comparison again"
    },
    "times": {
      "lead_s": 0.3,
      "mismatch_s": 1.0,
      "cause_s": 5.2,
      "fixed_s": 7.6,
      "_note": "the maker's recording: sentence 1 0.30-4.82 s (the mismatch is red from mismatch_s), sentence 2 5.17-8.62 s (the cause from cause_s, the fixed comparison green from fixed_s)"
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
  "n13": {
    "duration_s": 12.0,
    "_note": "drawn by the screens department (web cut.html?cut=n13) from the experiment department's real Claude run (Claude writes a tool and checks it against known answers). 12.0 s is N13's slot in narration draft 6",
    "want": {
      "x": 96,
      "y": 96,
      "w": 1728,
      "h": 96,
      "text_px": 30,
      "_note": "what the maker wants, one line"
    },
    "shelf": {
      "x": 96,
      "y": 216,
      "w": 1100,
      "h": 670,
      "title_px": 26,
      "text_px": 22,
      "_note": "the shelf of tool cards, or the analysis that uses the new tool; the missing tool is a dotted card until Claude writes it"
    },
    "code": {
      "x": 1220,
      "y": 216,
      "w": 604,
      "h": 300,
      "text_px": 18,
      "_note": "a glimpse of the code Claude writes (small, a few lines; not to be read)"
    },
    "check": {
      "x": 1220,
      "y": 540,
      "w": 604,
      "h": 346,
      "title_px": 26,
      "text_px": 24,
      "_note": "the known-answer checks of the new tool (with their sources), ticked one by one"
    },
    "times": {
      "lead_s": 0.3,
      "gap_s": 4.0,
      "write_s": 4.6,
      "check_s": 7.6,
      "shelf_s": 10.4,
      "_note": "the maker's recording: sentence 1 0.30-3.63 s (what the maker wants), sentence 2 3.98-7.18 s (the missing tool from gap_s, Claude writes from write_s), sentence 3 7.53-11.00 s (the checks from check_s, the tool is used from shelf_s)"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
    }
  },
  "n14": {
    "duration_s": 9.7,
    "_note": "drawn by the screens department (web cut.html?cut=n14) from the intake department's real run (knowledge imported, a rule check, a hearing question). 9.7 s is N14's slot in narration draft 6",
    "knowledge": {
      "x": 96,
      "y": 96,
      "w": 700,
      "h": 790,
      "title_px": 30,
      "text_px": 24,
      "_note": "the list of rules: the basic constraints, then the dropped file's rules added as rows"
    },
    "view": {
      "x": 820,
      "y": 96,
      "w": 1004,
      "h": 560,
      "fill": 0.8,
      "_note": "the part the new rule catches, in 3D; fill: the vehicle fills this share of the view along the side it meets first (width or height, whichever fits the box), centred"
    },
    "ask": {
      "x": 820,
      "y": 680,
      "w": 1004,
      "h": 206,
      "text_px": 28,
      "_note": "HIRAKU's question back to the maker, as a speech bubble"
    },
    "times": {
      "lead_s": 0.3,
      "drop_s": 2.2,
      "rule_s": 3.6,
      "hit_s": 5.0,
      "ask_s": 6.8,
      "_note": "the maker's recording: sentence 1 0.30-6.34 s (the file is dropped at drop_s, its rules join the list at rule_s, one catches a part at hit_s), sentence 2 6.69-8.88 s (the question from ask_s)"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
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
      "change_of_title": 1.5,
      "domain_of_title": 1.4,
      "_note": "centred; bottom 886. change_of_title: the largest size of the change's sentence, a multiple of title_px (one line across the page); domain_of_title: the size of the domain cards (pass / fail), a multiple of title_px"
    },
    "dim_opacity": 0.3,
    "_dim_note": "dim_opacity: everything but the next change is drawn at this opacity once it lights (the story: one place to read)",
    "times": {
      "lead_s": 0.3,
      "light_s": 5.8,
      "_note": "light: 次に変えるなら lights up as the voice says 次に何を (about 5.8 s; sentence 2 of the maker's recording runs 4.10-8.21 s)"
    },
    "colors": {
      "bg": "#f4f1ea",
      "panel": "#fbf9f4",
      "rule": "#c9c3b6"
    }
  },
  "n17b": {
    "duration_s": 5.8,
    "_duration_note": "5.8 s is N17b's slot. Voice (sixth version, the recorded cues of N17b: at and seconds from the cut's start): 構想は 10 万行。 0.30-1.93 s, その道を、Claude Code が拓きました。 2.28-5.01 s. The subtitles are burnt in later; the voice's words are not on the screen",
    "_note": "how much and how deep: with sentence 1 the amount of HIRAKU (lines of code written, with the commits, the days and the tests under it, all counted from git main); with sentence 2, under 1 回の運転で, four cards in the order of one run (the areas judged, the analysis tools and the known-answer cases they were checked on, hand calculation set against analysis, fixed and passed). Every figure comes from promo/screens/n17b.data.js (promo/n17b_data.py, from git and one run's events.jsonl); the 100,000 of the voice is the plan and is not drawn",
    "amount": {
      "x": 96,
      "y": 110,
      "w": 1728,
      "h": 300,
      "cap_h": 48,
      "cap_size_px": 30,
      "num_h": 196,
      "num_size_px": 172,
      "unit_size_px": 56,
      "sub_h": 52,
      "sub_size_px": 34,
      "dim_alpha": 0.6,
      "dim_dur_s": 0.45,
      "_note": "centred in this box, top to bottom: the caption (cap_h), lines written (num_h, counting up), the commits, the days and the tests (sub_h). Once the cards come in it dims to dim_alpha over dim_dur_s from times.dim_s, so the cards are the place to read"
    },
    "head": {
      "x": 96,
      "y": 458,
      "w": 1728,
      "h": 46,
      "size_px": 28,
      "_note": "the heading over the cards (1 回の運転で), left-aligned"
    },
    "depth": {
      "x": 96,
      "y": 516,
      "w": 1728,
      "h": 300,
      "card_w": 402,
      "gap_px": 40,
      "rule_px": 2,
      "pad_top": 20,
      "label_size_px": 30,
      "num_size_px": 112,
      "unit_size_px": 36,
      "sub_size_px": 25,
      "arrow_px": 12,
      "_note": "four cards in a row (4 x card_w + 3 x gap_px = w) in the order of a run: the areas, the tools, hand calculation set against analysis, fixed and passed. Each is a rule on top, a label, one figure with its unit and a line or two that says what it means; a small arrow (arrow_px) sits on the rule in each gap. Bottom 516 + 300 = 816"
    },
    "stamp": {
      "x": 96,
      "y": 858,
      "w": 1728,
      "h": 32,
      "size_px": 19,
      "_note": "when the figures were counted, the git ref and the run, right-aligned, small; bottom 890, above the subtitle band"
    },
    "lanes": {
      "x": 96,
      "y": 118,
      "w": 1728,
      "h": 690,
      "top": 1,
      "pitch_px": 86,
      "dot_r_px": 4.5,
      "line_px": 2,
      "main_line_px": 3,
      "curve_dx_px": 48,
      "halo_px": 14,
      "alpha": 0.3,
      "dim_alpha": 0.2,
      "dim_dur_s": 0.45,
      "_note": "the commit graph from git, faint behind the figures: one lane per Claude Code session (the lead on top, then the departments; graph.lanes of n17b.data.js), lane i's centre at top + (i + 0.5) x pitch_px inside this box (8 lanes, 1-689). A commit is a dot at its author time on a linear axis from the first commit (x 0) to the last (x w); a merge into main is a curve from department lane i, curve_dx_px x sqrt(i) before the merge, up to the lead's lane. No lane names or dates. The text in front has a halo of the night colour (halo_px) so no line runs through a letter. The whole graph is drawn at alpha and dims to dim_alpha over dim_dur_s from times.dim_s, with the amount, so the cards stay the place to read"
    },
    "flow": {
      "ease": [
        0.3,
        0,
        0.35,
        1
      ],
      "step_s": 0.05,
      "dot_dur_s": 0.3,
      "merge_dur_s": 0.35,
      "in_dur_s": 0.4,
      "_note": "the graph grows from the left with the amount's count: a clip over the graph opens from x 0 at times.lanes_s to x w at times.lanes_to_s (ease), sampled every step_s into keyframes; the graph comes in over in_dur_s from times.lanes_s; a dot fades in over dot_dur_s and a merge curve draws over merge_dur_s when the clip reaches them. The page holds no lane motion after times.lanes_to_s + merge_dur_s"
    },
    "count": {
      "amount_dur_s": 1.3,
      "card_dur_s": 0.9,
      "in_dur_s": 0.4,
      "ease": [
        0.12,
        0.6,
        0.2,
        1
      ],
      "steps": 32,
      "_note": "each figure counts up over its dur_s and stops on the counted value: steps whole numbers (value x ease(j / steps), rounded down; fewer when the value is smaller), one after another at even times, so every frame shows a whole number. in_dur_s: how long a card or caption takes to come in"
    },
    "times": {
      "amount_s": 0.3,
      "lanes_s": 0.3,
      "lanes_to_s": 1.9,
      "amount_count_s": 0.35,
      "amount_sub_s": 1.0,
      "dim_s": 2.2,
      "cards_s": 2.35,
      "card_step_s": 0.4,
      "stamp_s": 4.5,
      "_note": "amount: comes in with 構想は 10 万行 (0.30-1.93 s); the lines count up from amount_count_s and stop at amount_count_s + count.amount_dur_s = 1.65 s; the commits, days and tests come in at amount_sub_s. lanes: the faint commit graph behind comes in at lanes_s and grows from the left to lanes_to_s, about as the lines count up, and is still by lanes_to_s + flow.merge_dur_s = 2.25 s; from dim_s it dims with the amount. cards: with その道を、Claude Code が拓きました (2.28-5.01 s) the heading and the four cards come in card_step_s apart from cards_s (2.35, 2.75, 3.15, 3.55 s), each counting up over count.card_dur_s, the last stopping at 4.45 s. stamp: the counted-at line at 4.5 s. From 4.9 s to the end the picture holds still"
    }
  },
  "n18": {
    "duration_s": 9.0,
    "_ground_note": "v3 (2026-11-03): ends on paper, the same ground as N00 (common.css --paper #f4f1ea with the faint 40 px grid, class paper-grid; layout.json colors has no paper entry). The night of N17b is the closed door (door.*) in front of the paper; it opens at times.open_s and is gone by open_s + door.crack_dur_s + door.hold_dur_s + door.open_dur_s (1.45 s)",
    "light": {
      "y": 60,
      "w": 130,
      "h": 850,
      "grow_dur_s": 0.4,
      "out_dur_s": 0.35,
      "_note": "centred, safe.y to the subtitle band. grow_dur_s: the thin line grows from times.line_s to its full height, ending at times.open_s; it is the light in the door's seam, opens with the crack (title.css light-open) and fades over out_dur_s from the swing (v3: was y 40, h 470, a band the vehicle stood on)"
    },
    "door": {
      "crack_dur_s": 0.15,
      "crack_deg": 14,
      "crack_ease": [
        0.25,
        0,
        0.3,
        1
      ],
      "hold_dur_s": 0.05,
      "open_dur_s": 0.45,
      "angle_deg": 100,
      "perspective_px": 3600,
      "ease": [
        0.4,
        0,
        0.6,
        1
      ],
      "line_px": 2,
      "thickness_px": 44,
      "spill_dur_s": 1.4,
      "shade_alpha": 0.55,
      "_note": "two night leaves (the N17b ground split down the middle), hinged on their outer edges, as N05's door: from times.open_s they part by crack_deg over crack_dur_s (crack_ease), hold hold_dur_s, then swing out towards the viewer to angle_deg over open_dur_s (ease), darkening to shade_alpha as they turn; each leaf's free edge has a lit thickness (thickness_px) and its other three sides a faint outline (line_px), unseen while the door is shut (it lies on the stage's edges). The first stills (crack 10 deg over 0.2 s, hold 0.1 s, swing 0.5 s with N05's ease) were still mostly dark at 1.4 s; the second (perspective 2400 as N05) threw the leaves out of the frame by about 1.28 s, so the swing was hard to see. Behind them warm light on the paper (the spill) fades over spill_dur_s from the swing, so from about 2.6 s the ground is bare paper. Hidden once open"
    },
    "vehicle": {
      "y": 70,
      "w": 460,
      "_note": "centred; ink lines on the paper (no halo). Height w / 2 (viewBox 600 x 300). Was at y 110 (bottom 340) until the pets sat on the name (2026-11-04): their boxes reach up to about 324, 310 while they hop (n18.pets); at 70 the bottom is 300"
    },
    "title": {
      "name": {
        "y": 400,
        "size_px": 170
      },
      "tag": {
        "y": 618,
        "size_px": 48
      }
    },
    "url": {
      "y": 724,
      "size_px": 30,
      "_note": "ink-soft. Was at y 826 (night ground) until v3"
    },
    "credit": {
      "y": 776,
      "size_px": 20,
      "alpha": 0.62,
      "fade_dur_s": 0.8,
      "_note": "one small line under the URL: HIRAKU Powered by Claude (no Claude logo, no Anthropic colours; the Agent SDK overview's branding guidelines list Claude Agent, Claude and {YourAgentName} Powered by Claude as allowed; this is the third form. It replaced Built with Claude on 2026-11-03: see promo/README.md). Ink at alpha, fading in over fade_dur_s from times.credit_s. Bottom about 801, above the subtitle band"
    },
    "gather": {
      "count": 150,
      "seed": 21,
      "w": 1640,
      "h": 760,
      "cy": 440,
      "margin_px": 24,
      "dot_min_px": 2.0,
      "dot_max_px": 5.0,
      "gather_dur_s": 0.3,
      "stagger_s": 0.1,
      "ease": [
        0.55,
        0,
        0.85,
        0.45
      ],
      "start_alpha": 0.55,
      "seed_glow_alpha": 0.5,
      "_note": "before the line (v2, after the reference's closing): count motes at seeded places in a w x h ellipse about (stage centre, cy), kept margin_px inside the stage and above the subtitle band, each of a seeded radius dot_*_px, are drawn in to seeded points on the line (x = stage centre, y within the light box, more towards its middle) over gather_dur_s (ease: slow, then fast) after a seeded delay of up to stagger_s from times.gather_s; they brighten from start_alpha and go out as they reach the line. A faint glow on the line's place rises to seed_glow_alpha meanwhile and hands over to the light at times.line_s. All motes are in by gather_s + stagger_s + gather_dur_s = times.line_s"
    },
    "rule": {
      "y": 596,
      "w": 400,
      "h": 2,
      "_note": "centred, ink, under the name as on N00; grows from the centre with the name (0.6 s from times.name_s + 0.55 s)"
    },
    "pets": {
      "_note": "the ten 3D pets of promo/pets/pet3d.js (v8) on the paper once the door has opened (2026-11-04: pets everywhere, as N00's, instead of all ten on the name): N00's size (n00.title_pets.h_em x this name's font size), camera (n00.camera), box aspect (n00.pets) and idle clocks (n00.motion.idle_shift_s x index). cast: where each lives and what it does, as n00.cast (pet3d.js anim.route; hop: the defaults of every hop here; places: [x, y] the feet in design px, {seat: letter, at} on the top edge of a letter of this name). The lines the pets stand on: the foot of the name (y 535, the baseline; left of the H and right of the U), beside the line under the name (y 681, the tag's bottom; left and right of it), the vehicle's ground line (y 300; left and right of the vehicle) and the floor under the words (y 892: a box 102 high ends at 903.8, above the subtitle band). Nobody comes in before the door has gone (1.45 s); a pet goes onto a letter only once the name is in (times.name_s + 5 x 0.07 + 0.7 = 4.45 s). At the end they stand about the words: lead on the A, handling on the H, thermal on the U, nvh behind the U, designer and strength at the foot of the name, crash and powertrain beside the tag, aero by the vehicle, packager on the floor. stop: from from_s the pets' clocks slow to a halt at still_s (the clock's speed falls linearly to 0), so from still_s nothing on the screen moves. Every pose is a function of t only",
      "hop": {
        "crouch_s": 0.12,
        "crouch": 0.2,
        "air_s": 0.34,
        "over_px": 22,
        "stretch": 0.16,
        "land_sq": 0.24,
        "land_s": 0.1,
        "bounces": [],
        "settle_s": 0.2,
        "lean_k": 0.8,
        "antenna_k": 2.0,
        "antenna_lag_s": 0.06,
        "look_up": 0.03,
        "shadow_air": 0.45
      },
      "cast": {
        "lead": {
          "_who": "hops in from the left along the vehicle's ground line, watches it being drawn, then hops down onto the I and on over R to the A",
          "start": {
            "at": [
              150,
              300
            ],
            "yaw_deg": 60,
            "alpha": 0
          },
          "beats": [
            {
              "do": "fade",
              "t": 2.3,
              "to": 1,
              "dur_s": 0.3
            },
            {
              "do": "hops",
              "t": 2.3,
              "to": [
                520,
                300
              ],
              "n": 4,
              "every_s": 0.58,
              "yaw_deg": 60,
              "over_px": 26
            },
            {
              "do": "turn",
              "t": 4.82,
              "yaw_deg": 20,
              "dur_s": 0.4
            },
            {
              "do": "hop",
              "t": 5.45,
              "to": {
                "seat": 1,
                "at": 0.5
              },
              "yaw_deg": 50,
              "air_s": 0.42,
              "over_px": 30
            },
            {
              "do": "hop",
              "t": 6.0,
              "to": {
                "seat": 2,
                "at": 0.5
              },
              "yaw_deg": 50,
              "air_s": 0.34,
              "over_px": 26
            },
            {
              "do": "hop",
              "t": 6.5,
              "to": {
                "seat": 3,
                "at": 0.5
              },
              "yaw_deg": 40,
              "air_s": 0.36,
              "over_px": 30,
              "bounces": [
                [
                  8,
                  0.12
                ]
              ]
            },
            {
              "do": "turn",
              "t": 7.5,
              "yaw_deg": 0,
              "dur_s": 0.45
            }
          ]
        },
        "designer": {
          "_who": "walks in from the left at the foot of the name and stops by the H, turns to the viewer",
          "start": {
            "at": [
              140,
              535
            ],
            "yaw_deg": 110,
            "alpha": 0
          },
          "beats": [
            {
              "do": "fade",
              "t": 3.6,
              "to": 1,
              "dur_s": 0.4
            },
            {
              "do": "walk",
              "t": 3.6,
              "to": [
                380,
                535
              ],
              "dur_s": 3.4,
              "swing": 0.6
            },
            {
              "do": "turn",
              "t": 7.1,
              "yaw_deg": 20,
              "dur_s": 0.45
            }
          ]
        },
        "packager": {
          "_who": "carries its box in from the left along the floor under the words, turns and turns the box about",
          "start": {
            "at": [
              140,
              892
            ],
            "yaw_deg": 110,
            "alpha": 0
          },
          "beats": [
            {
              "do": "fade",
              "t": 1.6,
              "to": 1,
              "dur_s": 0.4
            },
            {
              "do": "walk",
              "t": 1.6,
              "to": [
                560,
                892
              ],
              "dur_s": 5.0,
              "swing": 0.7,
              "carry_deg": 35
            },
            {
              "do": "turn",
              "t": 6.7,
              "yaw_deg": 15,
              "dur_s": 0.45
            },
            {
              "do": "act",
              "t": 7.2,
              "kind": "fiddle",
              "dur_s": 0.65,
              "amp_deg": 25
            }
          ]
        },
        "cae.handling": {
          "_who": "steady: hops in from the left at the foot of the name, waits, then jumps up onto the H",
          "start": {
            "at": [
              140,
              535
            ],
            "yaw_deg": 60,
            "alpha": 0
          },
          "beats": [
            {
              "do": "fade",
              "t": 1.8,
              "to": 1,
              "dur_s": 0.3
            },
            {
              "do": "hops",
              "t": 1.8,
              "to": [
                440,
                535
              ],
              "n": 3,
              "every_s": 0.56,
              "yaw_deg": 60,
              "over_px": 24
            },
            {
              "do": "turn",
              "t": 3.7,
              "yaw_deg": 25,
              "dur_s": 0.4
            },
            {
              "do": "hop",
              "t": 4.65,
              "to": {
                "seat": 0,
                "at": 0.3
              },
              "yaw_deg": 30,
              "crouch_s": 0.18,
              "air_s": 0.4,
              "over_px": 28,
              "bounces": [
                [
                  8,
                  0.12
                ]
              ]
            },
            {
              "do": "act",
              "t": 6.2,
              "kind": "sway",
              "dur_s": 0.7,
              "amp_deg": 6
            }
          ]
        },
        "cae.powertrain": {
          "_who": "eager: hops in high from the right beside the line under the name",
          "start": {
            "at": [
              1770,
              681
            ],
            "yaw_deg": -60,
            "alpha": 0
          },
          "beats": [
            {
              "do": "fade",
              "t": 2.3,
              "to": 1,
              "dur_s": 0.3
            },
            {
              "do": "hops",
              "t": 2.3,
              "to": [
                1405,
                681
              ],
              "n": 4,
              "every_s": 0.48,
              "yaw_deg": -60,
              "over_px": 34,
              "air_s": 0.3,
              "crouch_s": 0.08
            },
            {
              "do": "turn",
              "t": 4.55,
              "yaw_deg": -15,
              "dur_s": 0.4
            },
            {
              "do": "act",
              "t": 5.9,
              "kind": "nod",
              "dur_s": 0.6,
              "amp_deg": 8
            }
          ]
        },
        "cae.aero": {
          "_who": "light: floats in from the right in three long hops and stands by the vehicle",
          "start": {
            "at": [
              1770,
              300
            ],
            "yaw_deg": -60,
            "alpha": 0
          },
          "beats": [
            {
              "do": "fade",
              "t": 2.7,
              "to": 1,
              "dur_s": 0.3
            },
            {
              "do": "hops",
              "t": 2.7,
              "to": [
                1275,
                300
              ],
              "n": 3,
              "every_s": 0.74,
              "yaw_deg": -60,
              "over_px": 40,
              "air_s": 0.5,
              "crouch_s": 0.12,
              "land_sq": 0.16
            },
            {
              "do": "turn",
              "t": 5.12,
              "yaw_deg": -30,
              "dur_s": 0.45
            },
            {
              "do": "act",
              "t": 6.7,
              "kind": "look",
              "dur_s": 0.8,
              "amp_deg": 18
            }
          ]
        },
        "cae.crash": {
          "_who": "heavy: plods in from the left beside the line under the name and sits down with a thump",
          "start": {
            "at": [
              140,
              681
            ],
            "yaw_deg": 110,
            "alpha": 0
          },
          "beats": [
            {
              "do": "fade",
              "t": 1.6,
              "to": 1,
              "dur_s": 0.4
            },
            {
              "do": "walk",
              "t": 1.6,
              "to": [
                470,
                681
              ],
              "dur_s": 4.6,
              "swing": 0.7
            },
            {
              "do": "turn",
              "t": 6.25,
              "yaw_deg": 20,
              "dur_s": 0.45
            },
            {
              "do": "sit",
              "t": 6.75,
              "to": 1,
              "dur_s": 0.55,
              "over": 0.12,
              "thump": 0.16
            }
          ]
        },
        "cae.strength": {
          "_who": "careful: walks in from the right at the foot of the name and stops by the U",
          "start": {
            "at": [
              1770,
              535
            ],
            "yaw_deg": -66,
            "alpha": 0
          },
          "beats": [
            {
              "do": "fade",
              "t": 3.4,
              "to": 1,
              "dur_s": 0.4
            },
            {
              "do": "walk",
              "t": 3.4,
              "to": [
                1530,
                535
              ],
              "dur_s": 3.6,
              "swing": 0.55
            },
            {
              "do": "turn",
              "t": 7.1,
              "yaw_deg": -20,
              "dur_s": 0.45
            }
          ]
        },
        "cae.nvh": {
          "_who": "lives behind the U: peeks out from behind its right side, goes back, and leans out again to stay",
          "layer": "behind",
          "start": {
            "at": [
              1338,
              535
            ],
            "yaw_deg": 0,
            "alpha": 0
          },
          "beats": [
            {
              "do": "fade",
              "t": 4.5,
              "to": 1,
              "dur_s": 0.3
            },
            {
              "do": "act",
              "t": 4.8,
              "kind": "peek",
              "dur_s": 1.3,
              "roll_deg": -22,
              "dx_px": 40,
              "amp_deg": 18
            },
            {
              "do": "act",
              "t": 6.4,
              "kind": "peek",
              "dur_s": 1.2,
              "roll_deg": -20,
              "dx_px": 36,
              "amp_deg": 16,
              "stay": true
            }
          ]
        },
        "cae.thermal": {
          "_who": "quick: hops in from the right at the foot of the name, then jumps up onto the U",
          "start": {
            "at": [
              1770,
              535
            ],
            "yaw_deg": -60,
            "alpha": 0
          },
          "beats": [
            {
              "do": "fade",
              "t": 2.1,
              "to": 1,
              "dur_s": 0.3
            },
            {
              "do": "hops",
              "t": 2.1,
              "to": [
                1480,
                535
              ],
              "n": 3,
              "every_s": 0.56,
              "yaw_deg": -60,
              "over_px": 22
            },
            {
              "do": "turn",
              "t": 4.0,
              "yaw_deg": -25,
              "dur_s": 0.4
            },
            {
              "do": "hop",
              "t": 5.0,
              "to": {
                "seat": 5,
                "at": 0.75
              },
              "yaw_deg": -30,
              "crouch_s": 0.15,
              "air_s": 0.42,
              "over_px": 30,
              "bounces": [
                [
                  9,
                  0.12
                ]
              ]
            },
            {
              "do": "act",
              "t": 6.9,
              "kind": "look",
              "dur_s": 0.7,
              "amp_deg": 16
            }
          ]
        }
      },
      "stop": {
        "from_s": 7.92,
        "still_s": 8.18,
        "_note": "the clocks are t + idle_shift_s x index (N00's), and pet3d blinks while clock % 4.3 < 0.16: lead's clock + 5.2 (index 4) blinks at about 7.70-7.86 s and index 7's at 8.10-8.26 s; the slowing clock runs from from_s to from_s + (still_s - from_s) / 2 = 8.05, between the two, so no pet holds still with its eyes shut (the first stills, from 7.9 to 8.4, froze index 7 mid-blink)"
      }
    },
    "times": {
      "gather_s": 0.0,
      "line_s": 0.4,
      "open_s": 0.8,
      "draw_s": 1.7,
      "name_s": 3.4,
      "tag_s": 4.4,
      "url_s": 5.5,
      "credit_s": 6.0,
      "_note": "gather: motes gather onto the line's place (gather); line: thin vertical line grows (light.grow_dur_s); open: the door parts at the line (door.*; swings from 1.0 s, gone by 1.45 s); draw: the vehicle's ink strokes start on the paper; name: first letter, just before the voice says HIRAKU (sentence 2 of the maker's recording, 3.61-7.07 s; sentence 1 runs 0.30-3.26 s); tag: about a second into sentence 2, while it says the tag's words; url, credit: after the tag is in, done by 6.8 s; then still to the end on paper. v3 (2026-11-03): name, tag, url, credit were 2.3, 3.0, 3.6, 4.2 and out_s 8.2 faded to black; open_s (read by bgm.py) did not change"
    }
  },
  "labels": {
    "_note": "small numbered labels over the opening quick looks N01-N04 (labels.html?cut=n01, or the entry pages labels_n01.html ... labels_n04.html), on a transparent ground over the app's cut. The cut's length is n01..n04.duration_s. A label comes in at times.in_s from the start of the cut over in_dur_s and is gone times.out_before_end_s before its end (fading over out_dur_s, ending then). Words follow the voice (narration draft 6)",
    "box": {
      "x": 112,
      "y": 822,
      "w": 620,
      "h": 46,
      "_note": "bottom left, inside the lower-left corner of the cut's view or tile (they end at y 886 and start at x 96 in n01-n04), 16 px in from the left and 18 px above their bottom edge; bottom 868, above the subtitle band. The label is as wide as its words, up to w. At y 842 (first stills) it straddled the boxes' bottom border"
    },
    "num_size_px": 20,
    "text_size_px": 24,
    "line_px": 1.5,
    "pad_x_px": 16,
    "gap_px": 14,
    "rule_w": 28,
    "in_dur_s": 0.35,
    "out_dur_s": 0.25,
    "times": {
      "in_s": 0.2,
      "out_before_end_s": 0.3
    },
    "cuts": {
      "n01": {
        "num": "01",
        "text": "段差を越える",
        "_voice": "段差を越えられるか。"
      },
      "n02": {
        "num": "02",
        "text": "倒れない",
        "_voice": "でこぼこ道でも、傾いても、風を受けても、急にハンドルを切っても、倒れないか。"
      },
      "n03": {
        "num": "03",
        "text": "長く・速く",
        "_voice": "長く走っても、オーバーヒートしないか、どこまで速く走れるか。"
      },
      "n04": {
        "num": "04",
        "text": "衝突・振動・組み立て",
        "_voice": "衝突も、振動も、組み立ての順番も。"
      }
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
          "moving": 12,
          "why": "the seven domain chips are the point of the cut (7); lead, designer and packager wear no tag since they sit on the name (2026-11-03; 10 with their tags before). The ten pets are 3D (data-kind moving) and all breathe, so they count 10 wherever they are (the row of seven counts as one while it rises, being one moving thing with its pets inside), plus a chip as it lights or the name and its line while they come in (12)"
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
        "message": "衝突・振動・組み立てまで、全部を 1 度に見る (呼ばれた札が記録の色のまま灯る)",
        "limits": {
          "label": 8,
          "moving": 8,
          "why": "the eight tiles of the grid; each tile shows a picture and a tick or cross only. The tiles' 3D pets are not counted as moving: they hold still and react only for a moment when their tile changes"
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
          "label": 8,
          "why": "the seven domains and assembly light up (the same eight as the N04 / N15 grid)"
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
        "message": "ツールが無ければ、Claude がその場で作り、照合してから使う",
        "limits": {
          "label": 4,
          "why": "the three known-answer checks and the new tool's name; the shelf's other cards go without names"
        }
      },
      "n14": {
        "message": "渡したノウハウが、設計のチェックの規則になる"
      },
      "n15": {
        "message": "全部の分野が緑になるまで、設計を直す",
        "limits": {
          "label": 8,
          "moving": 8,
          "why": "the same eight tiles as N04, all turning green. The pets react only when their tile changes, as in N04"
        }
      },
      "n16": {
        "message": "組み立ての順番まで、3D で確かめる"
      },
      "n17": {
        "message": "結果だけでなく、次に何を変えればよいかを示す"
      },
      "n17b": {
        "message": "HIRAKU は量があり、1 回の運転が 7 分野をまたいで予測と解析を照らし合わせて直すほど深い",
        "limits": {
          "label": 7,
          "number": 5,
          "moving": 4,
          "why": "two beats, one per sentence of the voice: the amount (one figure, with the commits, the days and the tests on one line under it) and, under one heading, four cards of depth in the order of a run, each a label and one figure; the cards come in one after another, so up to four things move at once. The commit graph behind (data-kind note: not a counted label, graph or number; its dots and curves are parts of it) is one moving thing; it grows during sentence 1, when only the amount moves, and its dim overlaps only the heading and the first card, so at most four move at once (2.4 s: the amount and the graph dimming, the heading, the first card)"
        }
      },
      "n18": {
        "message": "解析の前に予測し、理由を説明する — HIRAKU",
        "limits": {
          "moving": 12,
          "why": "the ten 3D pets on the name (n18.pets) are data-kind moving while they breathe and play (they stop by n18.pets.stop.still_s), the same ten as N00's end; with them the name and then the tag come in (11 measured at 3.8 and 4.6 s)"
        }
      }
    }
  },
  "check": {
    "times": {
      "n00": [
        0.6,
        1.2,
        1.8,
        2.5,
        3.5,
        4.3,
        4.5,
        5.65,
        6.05,
        6.65,
        8.0
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
      "n09": [
        0.3,
        2.2,
        3.6,
        4.65,
        5.5
      ],
      "n11": [
        0.3,
        1.5,
        4.9,
        9.6,
        11.6,
        14.4
      ],
      "n12": [
        0.3,
        1.0,
        5.2,
        7.6,
        9.0
      ],
      "n13": [
        0.3,
        4.0,
        4.6,
        7.6,
        10.0,
        10.4,
        11.8
      ],
      "n14": [
        0.3,
        2.2,
        3.6,
        5.0,
        6.8,
        9.6
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
        0.15,
        0.3,
        0.45,
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
      "n17b": [
        0.5,
        1.5,
        2.5,
        3.5,
        4.5,
        5.8
      ],
      "n17b_3d": [
        0.5,
        1.5,
        2.4,
        2.8,
        3.6,
        4.5,
        4.9,
        5.8
      ],
      "n18": [
        0,
        0.3,
        0.6,
        1.0,
        1.2,
        1.4,
        1.8,
        2.6,
        3.8,
        4.6,
        5.1,
        5.5,
        5.8,
        6.1,
        6.7,
        7.0,
        7.5,
        8.5
      ],
      "labels_n01": [
        0,
        0.25,
        2.0,
        3.8
      ],
      "labels_n02": [
        0.25,
        3.5,
        6.8
      ],
      "labels_n03": [
        0.25,
        2.5,
        4.8
      ],
      "labels_n04": [
        0.25,
        2.0,
        3.8
      ],
      "app": [
        2,
        8,
        15
      ]
    },
    "n17b_3d": {
      "_note": "The 3D values of n17b_3d.html (N17b shown in depth). Placement, sizes, times, colours and content are n17b's (the page loads layout.js with data-cut n17b and the same n17b.data.js); only the depth, the camera and the extrusion are here. Kept under check because promo/tests/test_layout.py fixes the set of top-level keys and the number of story cuts; the page takes n17b's story limits through <body data-cut=\"n17b\">",
      "duration_s": 5.8,
      "view": {
        "perspective_px": 1800,
        "origin_x": 960,
        "origin_y": 500,
        "_note": "CSS perspective of the stage and the point the camera looks at (design px). origin_y is the middle of the content (amount top 110, stamp bottom 890)"
      },
      "planes": {
        "lanes_z_px": -360,
        "amount_z_px": 0,
        "cards_z_px": 40,
        "stamp_z_px": 0,
        "_note": "depth of each layer (+ toward the camera). Each layer is scaled by (perspective - z) / perspective about the origin, so with the camera at rest every box falls where n17b puts it; the depth shows as the camera moves (the graph behind moves less than the figures in front)"
      },
      "camera": {
        "from_s": 0.0,
        "to_s": 4.9,
        "ease": [
          0.45,
          0,
          0.55,
          1
        ],
        "yaw_from_deg": -4.0,
        "yaw_to_deg": 0.0,
        "pitch_from_deg": -1.6,
        "pitch_to_deg": -0.6,
        "roll_deg": 0,
        "dolly_from_px": 0,
        "dolly_to_px": 40,
        "_note": "one slow move over the cut: the camera comes round from the right to the middle (yaw, so the still end is framed as n17b), looks up a little less (pitch, negative = from below) and comes 40 px nearer (dolly), eased in and out, and holds still from to_s as n17b does from 4.9 s. World transform: translateZ(dolly) rotateX(pitch) rotateY(yaw) about the origin; no roll"
      },
      "extrude": {
        "amount_px": 48,
        "card_px": 18,
        "rule_px": 20,
        "step_px": 0.5,
        "max_layers": 48,
        "near_mix": 0.42,
        "far_mix": 0.16,
        "rule_mix": 0.24,
        "_note": "depth of the extruded figures (the lines written, each card's figure; the units, Japanese, are not extruded) and of each card's rule, in px behind the face; the side is drawn as layers every step_px on the screen (at most max_layers), coloured from near_mix to far_mix of the glow over the night ground (rule_mix of the light for the rule). The screen offset of the back is the projection, through the camera at t, of the point the depth behind the anchor of the figure"
      },
      "limits": {
        "face_deg": 8,
        "swing_deg": 6,
        "_note": "the faces turn at most face_deg from the screen (|yaw| and |pitch| of the camera), and the camera's swing (yaw range + pitch range) stays within swing_deg: N17b shown in depth without tilting"
      }
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
        "selector": ".tag, .tile b, .bar-row span, .fig .cap, .url, .credit, .label-text, .tagline, .gloss, .wait",
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
        "cut": "n17b_3d",
        "a": "n17b_3d.amount.num",
        "b": "n17b_3d.amount.sub",
        "why": "the two boxes touch in n17b (the figure's box ends where the line under it starts); seen in depth by the turned camera each is a trapezoid and their bounding boxes overlap by up to 3 px, while the letters stay far apart"
      },
      {
        "cut": "n00",
        "a": "n00.pet.*",
        "b": "n00.*",
        "why": "a pet's box is a transparent canvas bigger than the pet in it. On a letter, the box's lower edge reaches past the letter's ink top into the name's line box (title_pets puts the feet on the ink top); the pet behind the H is under the name and never covers it; pets near one another have boxes that touch; the floor pets' boxes reach up to the chips' lower edge, and while a pet jumps from the row onto its letter its box passes over the line, the tagline and the chips. promo/tests/test_layout.py checks the drawn pets against the letters' ink, the tagline's and the chips' text (at most 0.2 s over the name or the tagline for each pet, never over a chip's words)"
      },
      {
        "cut": "n07",
        "a": "n07.view",
        "b": "n07.label",
        "why": "the before / after label sits inside the view's top left on purpose (n07.label)"
      },
      {
        "cut": "n16",
        "a": "n16.view",
        "b": "n16.seek",
        "why": "the seek bar sits over the view's lower edge on purpose (n16.seek)"
      },
      {
        "cut": "n18",
        "a": "n18.light",
        "b": "n18.vehicle",
        "why": "the vehicle is drawn over the vertical light on purpose"
      },
      {
        "cut": "n18",
        "a": "n18.pet.*",
        "b": "n18.pet.*",
        "why": "as on N00: a pet's box is a transparent canvas bigger than the pet; pets near one another (on neighbouring letters, nvh behind the U under thermal on it) have boxes that touch"
      },
      {
        "cut": "n18",
        "a": "n18.pet.*",
        "b": "n18.title.name",
        "why": "as on N00: on a letter, the box's lower edge reaches past the letter's ink top into the name's line box (the feet stand on the ink top), and the pets at the foot of the name stand beside the H and the U in its line box; the pet behind the U is under the name and never covers it. promo/tests/test_layout.py checks the drawn pets against the letters' ink, not these boxes"
      },
      {
        "cut": "n18",
        "a": "n18.pet.*",
        "b": "n18.title.tag",
        "why": "the tag's box is the whole width of the stage (centred text); crash and powertrain stand beside its words, left and right of them. promo/tests/test_layout.py checks the drawn pets against the words' own text box"
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
      },
      {
        "cut": "n17b",
        "a": "n17b.vision*",
        "b": "n17b.lanes*",
        "why": "the planned 100,000 is drawn faint behind the lanes on purpose while they start to grow; it is gone (2.3 s) before the commits stream in"
      }
    ]
  }
};

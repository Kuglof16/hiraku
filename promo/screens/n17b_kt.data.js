// Generated file: do not edit. Written by promo/kinetic_data.py (rules and values of n17b_kt).
window.KT_DATA = {
 "cut": "n17b_kt",
 "rules": {
  "duration_s": 5.8,
  "voice": [
   [
    0.3,
    1.93
   ],
   [
    2.28,
    5.01
   ]
  ],
  "view": {
   "perspective_px": 900,
   "eye_y": 420
  },
  "corridor": {
   "half_w_px": 560,
   "open_w_px": 760,
   "rows_y_px": [
    -230,
    -120,
    110,
    190
   ],
   "z0_px": -600,
   "pitch_px": 165,
   "size_px": 40,
   "travel_px": 7600,
   "fade_px": 140,
   "edge_px": 6,
   "far_px": 9500,
   "near_px": 120,
   "alpha": 0.9,
   "rails_y_px": [
    -300,
    270
   ],
   "rail_seg_px": 600,
   "rail_len_px": 11400,
   "rail_px": 2,
   "rail_alpha": 0.22,
   "_note": "commit subjects on two walls (x = -half_w_px and +half_w_px from the middle, rows at rows_y_px from the eye line eye_y), oldest at z0_px, one more every pitch_px into the distance, alternating left and right. The camera goes travel_px forward over times.cam_s. With sentence 2 the walls move apart to open_w_px (times.open_s). A subject fades out as its box comes within fade_px of the screen's edge or the subtitle band and is gone edge_px before it; it fades in from far_px and is gone once its near end is within near_px of the camera. Thin rails run along each wall at rails_y_px (segments of rail_seg_px, rail_len_px in all)"
  },
  "num": {
   "size_px": 190,
   "unit_em": 0.34,
   "layers": 30,
   "depth_px": 90,
   "ry_from_deg": -24,
   "ry_to_deg": -8,
   "rx_from_deg": 10,
   "rx_to_deg": 4,
   "_note": "the lines written, extruded, held in front of the camera, centred on the eye line"
  },
  "cap": {
   "y": 178,
   "h": 52,
   "size_px": 34
  },
  "sub": {
   "y": 640,
   "h": 60,
   "size_px": 40
  },
  "stamp": {
   "x": 96,
   "y": 858,
   "w": 1728,
   "h": 32,
   "size_px": 19
  },
  "lift": {
   "fig_dy_px": -175,
   "fig_scale": 0.6,
   "cap_dy_px": -86,
   "sub_dy_px": -290,
   "_note": "after the walls start to open, the figure (scaled by fig_scale), the caption and the commits line move up by these amounts over times.lift_s, to leave room for the depth"
  },
  "depth": {
   "head": {
    "y": 446,
    "h": 64,
    "size_px": 42
   },
   "cards": {
    "x": 96,
    "y": 560,
    "w": 1728,
    "h": 270,
    "gap_px": 36,
    "rule_px": 2,
    "lab_px": 30,
    "num_px": 116,
    "unit_em": 0.34,
    "sub_px": 24,
    "layers": 14,
    "depth_px": 30,
    "from_z_px": -1400,
    "from_rx_deg": 24,
    "rise_px": 40,
    "from_ry_deg": -20,
    "ry_deg": -6,
    "rail_dim": 0.8,
    "perspective_px": 1200
   },
   "_note": "sentence 2: the heading (one run) and four cards in a row, in the order of a run: what is judged, with what, checked how, and the end. Each card comes forward from from_z_px (tilted back by from_rx_deg) over times.card_in_s and its figure (extruded: layers copies over depth_px, the unit on the face only) counts up over times.card_count_s (from the card's start), turning from from_ry_deg to ry_deg. The corridor's rails dim by rail_dim as the cards come in (they would cross the cards' text)"
  },
  "times": {
   "cam_s": [
    0.0,
    4.9
   ],
   "count_s": [
    0.35,
    1.9
   ],
   "num_in_s": 0.2,
   "cap_s": 0.3,
   "open_s": [
    2.3,
    4.6
   ],
   "sub_s": 2.4,
   "lift_s": [
    2.75,
    3.3
   ],
   "head_s": 3.0,
   "cards_s": 3.25,
   "card_step_s": 0.25,
   "card_in_s": 0.45,
   "card_count_s": [
    0.1,
    0.65
   ],
   "stamp_s": 4.5
  },
  "cam_ease": [
   0.4,
   0.0,
   0.2,
   1.0
  ],
  "count_steps": 40,
  "messages": 60,
  "message_chars": 46
 },
 "values": {
  "ref": "main",
  "head": "033ef52",
  "lines_total": 71001,
  "commits": 524,
  "days": 2,
  "subjects_from": 321,
  "messages": [
   {
    "sha": "68b816f",
    "text": "Start the repository: license, README…"
   },
   {
    "sha": "66b6f23",
    "text": "Add hiraku.webserve: serve web/ and read…"
   },
   {
    "sha": "bdd07fd",
    "text": "Add K0 formulas for tilt, crosswind, lane…"
   },
   {
    "sha": "611e11b",
    "text": "Use the new contract units: air density and…"
   },
   {
    "sha": "9cbb03e",
    "text": "Add the fatigue tool: unit-force stresses…"
   },
   {
    "sha": "5e33924",
    "text": "K2: vehicle model, K&C bench, time…"
   },
   {
    "sha": "3b54a1b",
    "text": "Add the run store: run folder, project.json…"
   },
   {
    "sha": "42255ad",
    "text": "Carry occupants and cargo as design parts…"
   },
   {
    "sha": "a34ef3d",
    "text": "Add closed-form known answers and solver…"
   },
   {
    "sha": "2d8a073",
    "text": "Follow the updated contract: solver entry…"
   },
   {
    "sha": "3db34a1",
    "text": "check_run: blocked_by goes with…"
   },
   {
    "sha": "4363363",
    "text": "Balance subtitle splits; take slots from the…"
   },
   {
    "sha": "e594350",
    "text": "Background music synthesized in…"
   },
   {
    "sha": "4acc796",
    "text": "Hook rule ids HK1-HK4 on the screen"
   },
   {
    "sha": "fa42a6d",
    "text": "CLAUDE.md: run the whole test suite before…"
   },
   {
    "sha": "189feaf",
    "text": "Speed up the box overlap test and rainflow…"
   },
   {
    "sha": "eb6d31f",
    "text": "A failed Claude run gives the error text as…"
   },
   {
    "sha": "4455875",
    "text": "check_run --effects: where the hooks acted…"
   },
   {
    "sha": "5faeb27",
    "text": "K2: declared load-case params and K0 formula…"
   },
   {
    "sha": "4a79eec",
    "text": "3D: test scenes from the records, part…"
   },
   {
    "sha": "2081ff9",
    "text": "run.finished stop_code; roles stopped with a…"
   },
   {
    "sha": "f32568e",
    "text": "Contract: stop_code goes with status…"
   },
   {
    "sha": "d58b998",
    "text": "check_run: stop_code and analysis.scene…"
   },
   {
    "sha": "02cc7b5",
    "text": "designgen: tyre and wheel values of the 3D…"
   },
   {
    "sha": "7c37dfc",
    "text": "K2: the projection note says what the part…"
   },
   {
    "sha": "dcf63e6",
    "text": "Contract: assembly step details…"
   },
   {
    "sha": "f2ca769",
    "text": "Contract: tools/ in the run folder…"
   },
   {
    "sha": "a54d6fa",
    "text": "N05 cut page: the vehicle bursts open into…"
   },
   {
    "sha": "a88254b",
    "text": "Video requirements file; scripted steps for…"
   },
   {
    "sha": "9da1eaa",
    "text": "N02 cut (five tests and their limits); N01…"
   },
   {
    "sha": "5151724",
    "text": "Contract: requirements and project may list…"
   },
   {
    "sha": "ef72aee",
    "text": "R3 gets a plain hand prediction; on the demo…"
   },
   {
    "sha": "1150f56",
    "text": "check_run: tile state follows its load…"
   },
   {
    "sha": "3d66470",
    "text": "Intake: pick_domains (N09) from the…"
   },
   {
    "sha": "0984f05",
    "text": "N05 opens on a double door; layout.js reads…"
   },
   {
    "sha": "f968ebe",
    "text": "WIP check_run: record kinds from…"
   },
   {
    "sha": "044e459",
    "text": "K1 beam stress painted on the elements of a…"
   },
   {
    "sha": "7a38d7d",
    "text": "Intake: use the contract's layout constants…"
   },
   {
    "sha": "ad4e391",
    "text": "Contract: shell mesh record, mesh.created…"
   },
   {
    "sha": "b6ee08e",
    "text": "Who is on board comes from…"
   },
   {
    "sha": "2838d55",
    "text": "Shell meshes drawn from mesh fields; series…"
   },
   {
    "sha": "4b6264f",
    "text": "Contract: input shapes of the intake MCP…"
   },
   {
    "sha": "8b4c3db",
    "text": "Intake screens in their own tabs; the…"
   },
   {
    "sha": "9144676",
    "text": "One loading rule, the contract's…"
   },
   {
    "sha": "72b0bea",
    "text": "frame_static draws the beam stress on the…"
   },
   {
    "sha": "8295909",
    "text": "K2: K&C compliance from the bushings, the…"
   },
   {
    "sha": "d2af457",
    "text": "Intake: a requirements file given to a run…"
   },
   {
    "sha": "90fadb7",
    "text": "A4 tool shelf screen: tools as a tree by…"
   },
   {
    "sha": "4c4398f",
    "text": "analyze: make the version's mesh before…"
   },
   {
    "sha": "9b73182",
    "text": "Tests run before every merge: one scripted…"
   },
   {
    "sha": "90afc19",
    "text": "Prompts: look at the joints with the shell…"
   },
   {
    "sha": "87a24c0",
    "text": "Narration draft 6: N00 8.6 s; n03, n06, n07…"
   },
   {
    "sha": "15ec005",
    "text": "Shell views: fields of the beam and shell…"
   },
   {
    "sha": "3dd3b94",
    "text": "Demo vehicle on double wishbones (builtin…"
   },
   {
    "sha": "52a2c76",
    "text": "K2: shorter check scenes, long K2 tests…"
   },
   {
    "sha": "420e1fe",
    "text": "Series against x for the screens; assembly…"
   },
   {
    "sha": "c688ed6",
    "text": "T1 past its limit; K0 step_climb.climb_limit"
   },
   {
    "sha": "eed881e",
    "text": "K2: mount forces as joint vectors; K&C…"
   },
   {
    "sha": "7edb3a7",
    "text": "run --intake-events: emit the events of…"
   },
   {
    "sha": "27cccf7",
    "text": "Layout: hiraku/export/ belongs to the design…"
   }
  ],
  "depth": {
   "run_id": "20261103-182809-offroad-4w-scripted",
   "domains": 7,
   "assembly": true,
   "areas": 8,
   "passed": 8,
   "versions": 2,
   "tools": 17,
   "known_cases": 295,
   "comparisons": 73,
   "mismatches": 8,
   "explained": 8
  }
 },
 "counted_at": "2026-11-04T01:23+09:00"
};

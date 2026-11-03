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
  "head": "2249748",
  "lines_total": 138308,
  "commits": 1150,
  "days": 3,
  "subjects_from": 753,
  "messages": [
   {
    "sha": "68b816f",
    "text": "Start the repository: license, README…"
   },
   {
    "sha": "1a14e98",
    "text": "Follow the contract update: rewrite a…"
   },
   {
    "sha": "f4d551b",
    "text": "Contract: signed comparison diffs, assembly…"
   },
   {
    "sha": "42255ad",
    "text": "Carry occupants and cargo as design parts…"
   },
   {
    "sha": "b995349",
    "text": "Add unit factors for m^2, kg/m^3, kg*m^2…"
   },
   {
    "sha": "8e47731",
    "text": "Remake only the changed sentences; N08b 9.7…"
   },
   {
    "sha": "fa42a6d",
    "text": "CLAUDE.md: run the whole test suite before…"
   },
   {
    "sha": "e79250f",
    "text": "Background music as a breathing drone with…"
   },
   {
    "sha": "226f054",
    "text": "Open a saved run by default; ready the…"
   },
   {
    "sha": "8861330",
    "text": "Contract: analysis.assumptions…"
   },
   {
    "sha": "ab72022",
    "text": "verdict.margin; round_limit ends stopped…"
   },
   {
    "sha": "177b2af",
    "text": "Layout from one rules file; automatic layout…"
   },
   {
    "sha": "69f499a",
    "text": "Stop button for runs started from the…"
   },
   {
    "sha": "70673cd",
    "text": "K2 builders: name the prediction-only…"
   },
   {
    "sha": "0464122",
    "text": "Contract: intake events, rule and rule_check…"
   },
   {
    "sha": "ae722d0",
    "text": "Pets v2: faceted body and a load-driven…"
   },
   {
    "sha": "5a7f653",
    "text": "N05 cut: ?hero=0 leaves the big vehicle out…"
   },
   {
    "sha": "2838d55",
    "text": "Shell meshes drawn from mesh fields; series…"
   },
   {
    "sha": "4e3a54e",
    "text": "A5's dropped knowledge file goes to the run…"
   },
   {
    "sha": "e0d37eb",
    "text": "N1: hand twist mode of the ladder and a…"
   },
   {
    "sha": "90fadb7",
    "text": "A4 tool shelf screen: tools as a tree by…"
   },
   {
    "sha": "d95a221",
    "text": "Contract: ToolError lives in protocols so…"
   },
   {
    "sha": "97ed1cb",
    "text": "README: the catalog's test list without a…"
   },
   {
    "sha": "52a2c76",
    "text": "K2: shorter check scenes, long K2 tests…"
   },
   {
    "sha": "cd40a5f",
    "text": "Scripted ask on a rule hit names the dropped…"
   },
   {
    "sha": "491936a",
    "text": "Contract: field.frames for a field at chosen…"
   },
   {
    "sha": "ba24637",
    "text": "loading: keep the earlier too-few-occupants…"
   },
   {
    "sha": "cc156c0",
    "text": "README: values checked against their sources…"
   },
   {
    "sha": "771837b",
    "text": "Intake: system rule_checks name every part…"
   },
   {
    "sha": "95cfa93",
    "text": "WIP: parts kits as left by the previous run"
   },
   {
    "sha": "842637e",
    "text": "Catalog R1s: R1 on the frame as shells…"
   },
   {
    "sha": "2b20a77",
    "text": "fe: mesh quality for shells and bricks…"
   },
   {
    "sha": "353444e",
    "text": "frame3d: a 3D beam stiffness solver sharing…"
   },
   {
    "sha": "f8677da",
    "text": "Cut pages within the story limits: one…"
   },
   {
    "sha": "3532465",
    "text": "N17b, the showcase-style opening and ending…"
   },
   {
    "sha": "7cfeea3",
    "text": "Demo convergence test: the 5 mm level is…"
   },
   {
    "sha": "b3d732b",
    "text": "fe.tubes: IIW hot spots on the tie lines by…"
   },
   {
    "sha": "165d4c3",
    "text": "T2 judges the motor and the controller…"
   },
   {
    "sha": "2a5b465",
    "text": "Report: numbers from the current design's…"
   },
   {
    "sha": "659173d",
    "text": "Requirements: Q60's source is the CPSC staff…"
   },
   {
    "sha": "b88c4bd",
    "text": "Runs that cannot make a first version end…"
   },
   {
    "sha": "962706d",
    "text": "N06: tag lines break between phrases, not…"
   },
   {
    "sha": "9a11c4b",
    "text": "export / assemble: NVENC on the GPU by…"
   },
   {
    "sha": "fda510f",
    "text": "run_tests: one full suite on the PC at a time"
   },
   {
    "sha": "f24aa5f",
    "text": "solvers/timing.py: where a run's time goes…"
   },
   {
    "sha": "8599d11",
    "text": "frame_static and frame_shell report each…"
   },
   {
    "sha": "69afd72",
    "text": "analyze refuses a tool whose applies_to does…"
   },
   {
    "sha": "72b0888",
    "text": "fe.tubes: round-tube hot spots settle with…"
   },
   {
    "sha": "677b4a3",
    "text": "N17: the next step lit is the named one with…"
   },
   {
    "sha": "8d02b77",
    "text": "check_run: the green rule's (b) by…"
   },
   {
    "sha": "056ce44",
    "text": "formula_inputs and compose_plan take the…"
   },
   {
    "sha": "a241736",
    "text": "K2 launch: the radius K0 takes is…"
   },
   {
    "sha": "1ba5267",
    "text": "Test layout copies: name the promo commit…"
   },
   {
    "sha": "2acab8b",
    "text": "Real look: materials for body panels, split…"
   },
   {
    "sha": "0acf35e",
    "text": "check_run: a report's next item from a…"
   },
   {
    "sha": "19a53ec",
    "text": "Care bed S1-side-rail-push is judged on the…"
   },
   {
    "sha": "30ee97b",
    "text": "space_frame_utv: the rear lower links get…"
   },
   {
    "sha": "e720a26",
    "text": "Screen text: ツール, not 道具 (HK2 label…"
   },
   {
    "sha": "4c700b7",
    "text": "Screen-facing words: ツール instead of 道具 in…"
   },
   {
    "sha": "45803f5",
    "text": "Intake: the summary line test reads the…"
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
 "counted_at": "2026-11-04T04:41+09:00"
};

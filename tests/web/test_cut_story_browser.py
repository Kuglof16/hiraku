"""Every cut page against the promo department's story limits (layout.json story).

Each cut is opened at its check times and the page is read: how many elements of each
data-kind (label, graph, legend, number, moving) are shown at once, against the cut's
limits (story.defaults, overridden by story.cuts.<cut>.limits); text inside an element
with a kind is at least limits.body_text_px, every other text at least min_text_px; and
the page's own corner note ("上限を越えた: ...") is not shown.

Skipped unless HIRAKU_WEB_BROWSER=1 (python -m tests.web.browser sets it) and Playwright is installed (as tests.web.test_browser).
The layout is a copy of the promo department's layout.json (the sections the cut pages
read, without the notes); every value in it is as in their commit 8e295cf. The check
times are its check.times,
and for N01 / N02, which have none there, the lead time, the middle and the end. The
records are the other cut tests' (shape examples); N01 and N02 need a run made by the
real tools (HIRAKU_WEB_DEV_RUNS, a folder made by tests.web.dev_k2_run) and are skipped
without one, N05 then plays the contract sample (its tiles say they have no record).

With HIRAKU_STORY_SHOTS=<folder> a screenshot of each cut at each check time is written
there (1920 x 1080; resize before reading).
"""

import copy
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from hiraku.webserve.server import make_server
from tests.web.domains_fixture import domain_events
from tests.web.shell_fixture import shell_events
from tests.web.test_assembly_browser import assembly_events, many_steps_events
from tests.web.test_browser import ENABLED, sync_playwright
from tests.web.test_cut_grid_browser import box_of, list_rects, three_versions
from tests.web.test_cut_n03_n06_browser import n03_n06_events
from tests.web.test_cut_n17_browser import REPORT, long_report, report_events

KINDS = ["label", "graph", "legend", "number", "moving"]
# Cuts whose text in a named box (data-box, not inside data-kind "note") is held to the
# story's body text size, as promo/layout_check.py counts it
BOXED_BODY = {"n03", "n04", "n06", "n07", "n15", "n16", "n17"}

# The promo department's layout.json (as of their commit 52d12e9: 11e1cc7's values, N03's
# shorter drive (loop_to_s 2.0), N04's own assembly box, n03.popup.ok_of_reading and
# n17.page.domain_of_title), the sections the cut pages read, without the notes.
# n11, n12 and n13 are as of their commit cce1262, the story's limits and check.times as of
# their commit 8e295cf (n09 label 8, n13 label 4, n17b, the check times of n09 and n11-n14);
# every value here was checked against 8e295cf, but for N03's loop: n03.times.loop_to_s 2.6,
# n03.times.ok_s 2.3 and n03.loop.ease "in-out" are the screens department's proposal of
# 2026-11-03, not in the promo department's layout.json yet. check.times.n13 is as of their
# commit 8b569cf (10.0 s added).
LAYOUT_JSON = r"""{
  "stage": {"w": 1920, "h": 1080},
  "subtitle_band": {"y": 910, "h": 170},
  "safe": {"x": 96, "y": 60, "w": 1728, "h": 850},
  "min_text_px": 18,
  "colors": {"night": "#131311", "ink": "#1b1a17", "tile_bg": "#f7f4ec", "tile_border": "#3b3934"},
  "n00": {"camera": {"fov_deg": 24, "azimuth_deg": 24, "elevation_deg": 12}, "motion": {"pass_dur_s": 0.9, "hop_period_s": 0.9, "idle_shift_s": 1.3}},
  "n01": {"duration_s": 4.0, "view": {"x": 96, "y": 96, "w": 1400, "h": 790}, "pane": {"x": 1520, "y": 96, "w": 304, "h": 790}, "card": {"gap": 20, "pad": 20, "title_px": 30, "text_px": 22, "radius_px": 12, "border_px": 2}, "times": {"lead_s": 0.4, "zoom_s": 1.6}, "zoom": {"from": 1.6, "to": 1.06}, "overlay": {"color": "#c8402a", "opacity": 0.22}, "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
  "n02": {"duration_s": 7.0, "grid": {"x": 96, "y": 96, "cols": 3, "rows": 2, "tile_w": 560, "tile_h": 383, "gap_x": 24, "gap_y": 24}, "panes": ["tilt", "wind", "rough", "dlc", "brake", "legend"], "pane": {"border_px": 2, "radius_px": 12, "label": {"x": 16, "y": 12, "size_px": 26}, "rate": {"size_px": 18}, "verdict": {"of_label": 2}}, "times": {"lead_s": 0.3}, "overlay": {"color": "#c8402a", "opacity": 0.22}, "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
  "n03": {"duration_s": 5.0, "view": {"x": 96, "y": 96, "w": 1200, "h": 790}, "popup": {"x": 1328, "y": 96, "w": 496, "h": 790, "title_px": 30, "text_px": 22, "reading_of_title": 2, "ok_of_reading": 0.55}, "times": {"lead_s": 0.3, "loop_to_s": 2.6, "accel_s": 2.9, "ok_s": 2.3}, "loop": {"ease": "in-out"}, "overlay": {"color": "#c8402a", "opacity": 0.22}, "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
  "n04": {"duration_s": 4.0, "header": {"x": 96, "y": 96, "w": 1728, "h": 48, "size_px": 30}, "grid": {"x": 96, "y": 160, "cols": 4, "rows": 2, "tile_w": 414, "tile_h": 351, "gap_x": 24, "gap_y": 24}, "feature": {"x": 96, "y": 160, "w": 852, "h": 726}, "list": {"x": 972, "y": 160, "w": 852, "h": 726, "cols": 2, "rows": 4, "gap_x": 24, "gap_y": 24}, "assembly": {"x": 96, "y": 160, "w": 1728, "h": 726}, "tests": ["handling", "powertrain", "aero", "crash", "strength", "nvh", "thermal", "assembly"], "tile": {"border_px": 2, "radius_px": 12, "label": {"x": 16, "y": 12, "size_px": 26}, "mark": {"size_px": 44, "right": 16, "top": 10}, "pet": {"w": 84, "h": 84, "right": 12, "bottom": 12, "feature": {"w": 160, "h": 160}}, "rate": {"size_px": 18}}, "times": {"lead_s": 0.3, "crash_s": 0.5, "nvh_s": 1.2, "assembly_s": 2.0}, "overlay": {"color": "#c8402a", "opacity": 0.22}, "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
  "n05": {"duration_s": 5.0, "grid": {"x": 96, "y": 280, "cols": 4, "rows": 3, "tile_w": 408, "tile_h": 180, "gap_x": 32, "gap_y": 26}, "tile": {"border_px": 2, "radius_px": 10, "label": {"x": 16, "y": 12, "size_px": 26}, "rate": {"size_px": 18}}, "hero": {"x": 210, "y": 60, "w": 1500, "h": 750, "src": "media/n05_hero_2x.png"}, "hero_motion": {"push_scale": 1.04, "burst_scale": 1.7, "burst_dur_s": 0.4, "ease": [0.5, 0, 0.9, 0.4]}, "ease": {"tile": [0.2, 0.9, 0.3, 1.15]}, "title": {"name": {"y": 58, "size_px": 124}, "tag": {"y": 186, "size_px": 42}, "gloss": {"y": 244, "size_px": 24, "text": "CAE = コンピュータで設計を試す技術"}}, "times": {"burst_s": 1.25, "tiles_s": 1.35, "tile_in_s": 0.6, "tile_stagger_s": 0.05, "name_s": 1.75, "tag_s": 2.3, "gloss_s": 2.4, "door_open_s": 0.55, "latch_s": 0.38, "mesh_in_s": 0.2, "mesh_out_s": 0.78, "points_s": 0.0, "hero_in_s": 0.3, "points_out_s": 0.36}, "door": {"crack_dur_s": 0.2, "crack_deg": 12, "crack_ease": [0.25, 0, 0.3, 1], "hold_dur_s": 0.12, "open_dur_s": 0.38, "angle_deg": 105, "perspective_px": 2400, "ease": [0.45, 0, 0.8, 0.55], "seam_w_px": 4, "seam_glow_w_px": 220, "spill_dur_s": 0.9, "hero_fade_dur_s": 0.35, "hero_fade_scale": 1.1, "panel": {"inset_px": 72, "upper_h_px": 300, "gap_px": 40, "line_px": 2, "bevel_px": 16}, "handle": {"w_px": 12, "h_px": 200, "seam_gap_px": 48}, "crack_overshoot_deg": 3, "settle_dur_s": 0.1, "latch_dur_s": 0.17, "latch_deg": 0.8, "thickness_px": 24, "dolly_px": 260, "dolly_ease": [0.55, 0, 0.85, 0.5], "bolt": {"w_px": 120, "h_px": 18, "reach_px": 40, "travel_px": 72, "gap_px": 64}}, "rays": {"count": 26, "seed": 7, "width_min_deg": 0.7, "width_max_deg": 3.6, "spread_deg": 62, "length_px": 1500, "blur_px": 6, "peak_alpha": 0.32, "crack_scale": 0.12, "shimmer_min_dur_s": 0.45, "shimmer_max_dur_s": 1.1, "out_dur_s": 0.5}, "dust": {"count": 90, "seed": 11, "size_min_px": 2, "size_max_px": 7, "w": 1600, "h": 760, "drift_px": 70, "in_dur_s": 0.3, "out_dur_s": 0.7}, "bloom": {"peak_alpha": 0.3, "rise_dur_s": 0.12, "fall_dur_s": 0.55}, "hero_mesh": {"size_px": 50, "jitter": 0.28, "seed": 5, "line_px": 1.3, "in_dur_s": 0.4, "reach_px": 520, "spread_dur_s": 0.27, "jitter_dur_s": 0.04, "el_dur_s": 0.13, "end_scale": 0.15}, "hero_points": {"seed": 13, "outline_keep": 0.8, "jitter": 0.35, "scatter_min_px": 160, "scatter_max_px": 760, "margin_px": 24, "gather_dur_s": 0.22, "stagger_s": 0.08, "ease": [0.12, 0.75, 0.3, 1], "dot_px": 4.6, "node_dot_px": 6.4, "start_alpha": 0.35, "fade_dur_s": 0.22, "hero_in_dur_s": 0.16, "mask": {"cell_px": 10, "cols": 150, "rows": 75, "src": "media/n05_hero_2x.png", "threshold": 128, "cover": 0.5, "rows_hex": ["00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000003000000000000000000000", "00000000000000007800000000000000000000", "0000000000000000fc00000000000000000000", "00000000000000007801e00000000000000000", "00000000000000007803e00000000000000000", "00000000000000003003f00000000000000000", "00000000000000007c03e60000000000000000", "0000000000000000fc01fff000000000000000", "0000000000000000fc01ffffc0000000000000", "0000000000000000fc1ffffffe000000000000", "0000000000000038feffffffff000000000000", "0000000000000040fcffffffff000000000000", "0000000000000044fcffffffff000000000000", "00000000000000007cffffffff000000000000", "00000000000000687fffffffff000000000000", "00000000000000707fffffffff000000000000", "00000000000000007fffffffff800000000000", "0000000000000080ffffffffffe00000000000", "0000000000000007fffffffffff00000000000", "000000000000013ffffffffffff00000000000", "000000000000027ffff7ffffffb00000000000", "00000000003ff8ffffffffffff100000000000", "00000000007fffffffffffff7e500000000000", "0000000001fffffffffffffc7e900000000000", "0000000001fffffffffffff1fd900000000000", "0000000003ffffffffffffe0ff900000000000", "0000000007fffffffffffff0fd300000000000", "0000000007ffffffffffffc0fd200000000000", "0000000007ffffffffffff007d600000000000", "000000000ffffffffffffc007f400000000000", "000000000ffffffffffff0007fc00000000000", "000000000fffffffffff00003f800000000000", "000000000ffffffffffe000006000000000000", "0000000007ffffffffff000000000000000000", "0000000007ffffffffff000000000000000000", "0000000003fffff3ffe7000000000000000000", "0000000003ffdfcfff87000000000000000000", "0000000000fff63fff89800000000000000000", "00000000003c7cfeff51800000000000000000", "0000000000001ff8ff71000000000000000000", "00000000000003e1ffe1000000000000000000", "0000000000000081fef3000000000000000000", "0000000000000001ffeb000000000000000000", "0000000000000001fe43000000000000000000", "0000000000000001fc46000000000000000000", "0000000000000001fc46000000000000000000", "0000000000000000fe0c000000000000000000", "0000000000000000ffb8000000000000000000", "00000000000000007ff0000000000000000000", "00000000000000003fe0000000000000000000", "00000000000000000fc0000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000", "00000000000000000000000000000000000000"]}}, "tile_fx": {"ghosts": 2, "ghost_lag_s": 0.035, "ghost_alpha": 0.32, "land_dur_s": 0.5, "land_glow_px": 26}, "name_wipe": {"wipe_dur_s": 0.65, "feather_px": 180, "sweep_w_px": 300, "afterglow_dur_s": 1.3}, "fly_from": {"x": 960, "y": 470}, "tests": ["step", "tilt", "wind", "rough", "dlc", "brake", "long", "launch", "crash", "nvh", "fatigue", "heat"]},
  "n06": {"duration_s": 11.7, "grid": {"x": 96, "y": 96, "cols": 3, "rows": 2, "tile_w": 560, "tile_h": 383, "gap_x": 24, "gap_y": 24}, "feature": {"x": 96, "y": 96, "w": 1104, "h": 790}, "list": {"x": 1224, "y": 96, "w": 600, "h": 790, "cols": 2, "rows": 3, "gap_x": 24, "gap_y": 24}, "tests": ["tilt_over", "step_stuck", "step_break", "battery_hot", "parts_clash", "wind"], "tile": {"border_px": 2, "radius_px": 12, "label": {"x": 16, "y": 12, "size_px": 26}}, "times": {"tiles_s": 0.6, "tile_in_s": 0.5, "tile_stagger_s": 0.55}, "overlay": {"color": "#c8402a", "opacity": 0.22}, "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
  "n07": {"duration_s": 15.3, "view": {"x": 96, "y": 96, "w": 1500, "h": 790}, "legend": {"x": 1620, "y": 96, "w": 204, "h": 790, "title_px": 22, "text_px": 20}, "label": {"x": 120, "y": 120, "size_px": 30, "of_view_h": 0.08}, "ring": {"of_view_h": 0.08}, "times": {"lead_s": 0.3, "turn_to_s": 5.5, "zoom_s": 5.5, "zoom_to_s": 7.5, "swap_s": 11.0, "swap_to_s": 13.0}, "turn_deg": 90, "zoom": {"fill": 0.45}, "overlay": {"color": "#c8402a", "opacity": 0.22}, "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
  "n15": {"duration_s": 5.1, "header": {"x": 96, "y": 96, "w": 1728, "h": 48, "size_px": 30}, "grid": {"x": 96, "y": 160, "cols": 4, "rows": 2, "tile_w": 414, "tile_h": 351, "gap_x": 24, "gap_y": 24}, "feature": {"x": 96, "y": 160, "w": 852, "h": 726}, "list": {"x": 972, "y": 160, "w": 852, "h": 726, "cols": 2, "rows": 4, "gap_x": 24, "gap_y": 24}, "tests": ["handling", "powertrain", "aero", "crash", "strength", "nvh", "thermal", "assembly"], "tile": {"border_px": 2, "radius_px": 12, "label": {"x": 16, "y": 12, "size_px": 26}, "mark": {"size_px": 44, "right": 16, "top": 10}, "pet": {"w": 84, "h": 84, "right": 12, "bottom": 12, "feature": {"w": 160, "h": 160}}, "rate": {"size_px": 18}}, "times": {"lead_s": 0.3, "rounds_from_s": 0.4, "all_green_s": 4.0}, "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
  "n16": {"duration_s": 6.3, "view": {"x": 96, "y": 96, "w": 1400, "h": 790}, "seek": {"x": 120, "y": 814, "w": 1352, "h": 48}, "steps": {"x": 1520, "y": 96, "w": 304, "h": 790, "title_px": 30, "text_px": 22, "row_gap": 12}, "times": {"lead_s": 0.3, "steps_from_s": 0.5, "steps_to_s": 4.2, "scrub_s": 4.4, "scrub_to_s": 5.8}, "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
  "n17": {"duration_s": 8.8, "page": {"x": 360, "y": 80, "w": 1200, "h": 806, "title_px": 34, "text_px": 22, "change_of_title": 1.5, "domain_of_title": 1.4}, "dim_opacity": 0.3, "times": {"lead_s": 0.3, "light_s": 5.8}, "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
  "n11": {"duration_s": 14.6, "memo": {"x": 96, "y": 96, "w": 560, "h": 790, "title_px": 30, "text_px": 24}, "log": {"x": 680, "y": 96, "w": 1144, "h": 360, "title_px": 30, "text_px": 24}, "result": {"x": 680, "y": 480, "w": 1144, "h": 406, "title_px": 30, "text_px": 24}, "times": {"lead_s": 0.3, "plausible_s": 1.5, "memo_s": 4.9, "stop_s": 9.6, "run_s": 11.6}, "overlay": {"color": "#c8402a", "opacity": 0.22}, "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
  "n12": {"duration_s": 9.2, "table": {"x": 96, "y": 96, "w": 1728, "h": 560, "title_px": 30, "text_px": 28, "value_px": 56}, "cause": {"x": 96, "y": 680, "w": 1728, "h": 206, "title_px": 30, "text_px": 28}, "times": {"lead_s": 0.3, "mismatch_s": 1.0, "cause_s": 5.2, "fixed_s": 7.6}, "overlay": {"color": "#c8402a", "opacity": 0.22}, "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
  "n13": {"duration_s": 12.0, "want": {"x": 96, "y": 96, "w": 1728, "h": 96, "text_px": 30}, "shelf": {"x": 96, "y": 216, "w": 1100, "h": 670, "title_px": 26, "text_px": 22}, "code": {"x": 1220, "y": 216, "w": 604, "h": 300, "text_px": 18}, "check": {"x": 1220, "y": 540, "w": 604, "h": 346, "title_px": 26, "text_px": 24}, "times": {"lead_s": 0.3, "gap_s": 4.0, "write_s": 4.6, "check_s": 7.6, "shelf_s": 10.4}, "colors": {"bg": "#f4f1ea", "panel": "#fbf9f4", "rule": "#c9c3b6"}},
  "story": {
    "defaults": {"label": 3, "graph": 1, "legend": 1, "number": 1, "moving": 3, "body_text_px": 24},
    "cuts": {
      "n00": {"message": "HIRAKU は、役ごとのエージェントが並んで動く (エージェントです)", "limits": {"label": 10, "moving": 12}},
      "n01": {"message": "越えられる段差 (100 mm) と、越えられない段差 (250 mm)", "limits": {"number": 2}},
      "n02": {"message": "傾き・風・悪路・急ハンドル・急ブレーキの、どれで倒れるかが分かる", "limits": {"label": 5}},
      "n03": {"message": "長く走っても熱くならず、どこまで速いかが分かる"},
      "n04": {"message": "衝突・振動・組み立てまで、全部を 1 度に見る (呼ばれた札が記録の色のまま灯る)", "limits": {"label": 8, "moving": 8}},
      "n05": {"message": "HIRAKU — CAE を、すべての作り手へ拓く", "limits": {"label": 12, "moving": 12}},
      "n06": {"message": "作ってみると、うまくいかない", "limits": {"label": 6}},
      "n07": {"message": "つなぎ目が弱く、補強で直る (CAE が作る前に見つける)"},
      "n08": {"message": "CAE のツールは高く、使いこなすのが難しい", "limits": {"label": 40, "moving": 12, "body_text_px": 18}},
      "n08b": {"message": "小さな会社の CAE の導入率は 16 %", "limits": {"number": 2, "graph": 1}},
      "n09": {"message": "作るものを伝えると、Claude が必要な解析を選ぶ (7 つの札が灯る)", "limits": {"label": 8}},
      "n10": {"message": "AI の答えも、CAE ツールで確かめる (3 問とも ✓)", "limits": {"number": 3, "label": 3}},
      "n11": {"message": "予測を書くまで、解析は動かない (hooks が止める)"},
      "n12": {"message": "手計算と解析が食い違ったら、原因 (単位の間違い) を突き止める", "limits": {"number": 2}},
      "n13": {"message": "ツールが無ければ、Claude がその場で作り、照合してから使う", "limits": {"label": 4}},
      "n14": {"message": "渡したノウハウが、設計のチェックの規則になる"},
      "n15": {"message": "全部の分野が緑になるまで、設計を直す", "limits": {"label": 8, "moving": 8}},
      "n16": {"message": "組み立ての順番まで、3D で確かめる"},
      "n17": {"message": "結果だけでなく、次に何を変えればよいかを示す"},
      "n17b": {"message": "HIRAKU は量があり、1 回の運転が 7 分野をまたいで予測と解析を照らし合わせて直すほど深い", "limits": {"label": 7, "number": 5, "moving": 4}},
      "n18": {"message": "解析の前に予測し、理由を説明する — HIRAKU"}
    }
  },
  "check": {"times": {"n00": [0.6, 1.2, 1.8, 2.5, 3.5, 4.5, 6.5, 8.0], "n03": [0.5, 2.0, 4.5], "n04": [0.5, 1.5, 3.5], "n06": [1.0, 4.0, 11.0], "n07": [0.5, 7.5, 12.0, 15.0], "n17": [0.5, 6.0, 8.5], "n15": [0.5, 2.5, 4.8], "n16": [0.5, 3.0, 5.0, 6.2], "n05": [0, 0.15, 0.3, 0.45, 0.5, 0.8, 1.0, 1.15, 1.4, 2.9, 4.5], "n08": [0.5, 2.0, 2.9, 4.6, 5.0, 5.8, 6.4], "n08b": [1.5, 5.2, 5.8, 7.5, 9.8], "n10": [0.6, 2.4, 4.6, 5.6, 8.3], "n17b": [0.5, 1.5, 2.5, 3.5, 4.5, 5.8], "n18": [0, 0.3, 0.6, 1.0, 1.2, 2.5, 3.2, 3.8, 5.0, 7.0, 8.5], "labels_n01": [0, 0.25, 2.0, 3.8], "labels_n02": [0.25, 3.5, 6.8], "labels_n03": [0.25, 2.5, 4.8], "labels_n04": [0.25, 2.0, 3.8], "app": [2, 8, 15], "n09": [0.3, 2.2, 3.6, 4.65, 5.5], "n11": [0.3, 1.5, 4.9, 9.6, 11.6, 14.4], "n12": [0.3, 1.0, 5.2, 7.6, 9.0], "n13": [0.3, 4.0, 4.6, 7.6, 10.0, 10.4, 11.8], "n14": [0.3, 2.2, 3.6, 5.0, 6.8, 9.6]}}
}"""


def story_layout():
    return json.loads(LAYOUT_JSON)


def limits_of(layout, cut):
    st = layout["story"]
    own = st["cuts"].get(cut, {}).get("limits", {})
    return {k: own.get(k, st["defaults"][k]) for k in [*KINDS, "body_text_px"]}


def check_times(layout, cut):
    """The promo department's check times; for a cut without any, the lead time, the middle and the end."""
    times = layout["check"]["times"].get(cut)
    if times:
        return times
    sp = layout[cut]
    lead = sp.get("times", {}).get("lead_s", 0.0)
    return [lead, round(sp["duration_s"] / 2, 3), round(sp["duration_s"] - 0.05, 3)]


# What is shown at stage time t, read in the page (its own counting, apart from the page's check).
READ = """async (t) => {
  await window.hiraku.seek(t);
  const stage = document.getElementById("stage");
  const shown = (el) => {
    if (!el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const counts = {};
  const items = {};
  for (const el of stage.querySelectorAll("[data-kind]")) {
    if (!shown(el)) continue;
    const k = el.dataset.kind;
    if (k === "note") continue; // notes and sources are not counted (promo/layout_check.py)
    counts[k] = (counts[k] ?? 0) + 1;
    (items[k] ??= []).push(el.dataset.box ?? el.className?.baseVal ?? el.className ?? el.tagName);
  }
  const texts = [];
  for (const el of stage.querySelectorAll("*")) {
    if (el.classList.contains("story-over") || el.classList.contains("err")) continue;
    if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue;
    if (!shown(el)) continue;
    texts.push({ text: el.textContent.trim().slice(0, 40), px: parseFloat(getComputedStyle(el).fontSize),
                 kind: el.closest('[data-kind~="note"]') ? null : el.closest("[data-kind]")?.dataset.kind ?? null,
                 boxed: !!el.closest("[data-box]") && !el.closest('[data-kind~="note"]') });
  }
  const note = document.querySelector(".story-over");
  return { err: document.querySelector(".err")?.textContent ?? null, counts, items, texts,
           note: note && shown(note) ? note.textContent : null, over: document.documentElement.dataset.storyOver ?? null,
           page: window.hiraku.story() };
}"""


# The sizes of what is shown large (the representative) and of everything else.
SIZES = """() => {
  const shown = (el) => {
    if (!el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const px = (el) => parseFloat(getComputedStyle(el).fontSize);
  const area = (el) => { const b = el.getBoundingClientRect(); return b.width * b.height; };
  const q = (sel) => [...document.querySelectorAll(sel)].filter(shown);
  const big = ".n03-reading, .verdict, .n17-change, .n07-label";
  const own = (el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
  return {
    large: q(big).map(px),
    others: [...document.querySelectorAll("#stage *")].filter((el) => own(el) && shown(el) && !el.closest(big) && !el.closest(".n06-big") && !el.classList.contains("story-over")).map(px),
    grid: q(".grid-tile").map((el) => ({ featured: el.dataset.featured === "1", area: area(el) })),
    n06: { big: q(".n06-big").map(area), small: q(".n06-tile").map(area) },
    label: document.querySelector(".n07-label")?.textContent ?? null,
    fixed: q(".n07-label .fixed").map((el) => [el.textContent, getComputedStyle(el).color]),
  };
}"""


# Where the boxes are, how low anything shown reaches, and text that runs past its box or
# is cut short (wider than its own width) inside the given boxes.
FIT = """(roots) => {
  const shown = (el) => {
    if (!el.checkVisibility({ opacityProperty: true, visibilityProperty: true })) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const rect = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
  const own = (el) => [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
  const boxes = {};
  for (const el of document.querySelectorAll("[data-box]")) if (shown(el)) boxes[el.dataset.box] = rect(el);
  const all = [...document.querySelectorAll("#stage *")].filter(shown);
  const out = [];
  for (const sel of roots) {
    for (const root of document.querySelectorAll(sel)) {
      if (!shown(root)) continue;
      const R = root.getBoundingClientRect();
      for (const el of root.querySelectorAll("*")) {
        if (!own(el) || !shown(el)) continue;
        const r = el.getBoundingClientRect();
        const block = getComputedStyle(el).display !== "inline";
        const cut = block && el.scrollWidth > el.clientWidth + 1;
        if (r.left < R.left - 0.5 || r.right > R.right + 0.5 || r.bottom > R.bottom + 0.5 || cut)
          out.push({ root: root.dataset.box, text: el.textContent.trim().slice(0, 30), px: parseFloat(getComputedStyle(el).fontSize), cut, rect: [r.left, r.top, r.right, r.bottom], box: [R.left, R.top, R.right, R.bottom] });
      }
    }
  }
  return { boxes, lowest: Math.max(...all.map((el) => el.getBoundingClientRect().bottom)), right: Math.max(...all.map((el) => el.getBoundingClientRect().right)), over: out };
}"""

# Where the one shown large and the list's tiles are (N04 / N15: the grid's tiles, the large
# one marked; N06: the large box shown now and the list's tiles that have landed).
PLACE = """() => {
  const rect = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; };
  const shown = (el) => el.checkVisibility({ visibilityProperty: true }) && el.getBoundingClientRect().width > 0;
  return {
    grid: [...document.querySelectorAll(".grid-tile")].filter(shown).map((el) => ({ featured: el.dataset.featured === "1", rect: rect(el) })),
    big: [...document.querySelectorAll(".n06-big")].filter(shown).map(rect),
    list: [...document.querySelectorAll(".n06-tile")].map((el) => ({ landed: Number(el.dataset.p) >= 1, rect: rect(el) })),
  };
}"""

# The boxes checked against the layout, and the boxes whose text must fit, by cut.
FIT_BOXES = {
    "n01": (["n01.view", "n01.pane"], ['[data-box="n01.pane"]']),
    "n02": ([], ['.n02-pane']),
    "n16": (["n16.view", "n16.steps"], ['[data-box="n16.steps"]', '[data-box="n16.view"]']),
    "n17": (["n17.page"], ['[data-box="n17.page"]']),
}


def with_verdict(events, run_id, passed):
    """N07's run with a comparison and a verdict on version 2's shell analysis (shape
    example: the values are the analysis's own, the verdict passes or fails as asked)."""
    out = copy.deepcopy(events)
    for e in out:
        e["run_id"] = run_id
    lc = out[0]["data"]["load_cases"][0]
    ana = next(e["data"] for e in out if e["kind"] == "analysis.finished" and e["data"]["analysis_id"] == "ana-0004")
    r = ana["results"][0]
    lim = lc["criterion"]["limit"]
    cmp = {"comparison_id": "cmp-0002", "prediction_id": "pred-0002", "analysis_id": "ana-0004", "quantity": r["quantity"],
           "predicted": {"value": r["value"], "unit": r["unit"]}, "analyzed": {"value": r["value"], "unit": r["unit"]},
           "diff_abs": 0.0, "diff_rel": 0.0, "tolerance_rel": lc.get("tolerance_rel", 0.1), "status": "match", "cause_id": None}
    ver = {"verdict_id": "ver-0002", "comparison_id": "cmp-0002", "load_case_id": lc["load_case_id"], "test_id": lc["test_id"],
           "domain": lc["domain"], "quantity": r["quantity"], "value": {"value": r["value"], "unit": r["unit"]}, "op": lc["criterion"]["op"],
           "limit": lim, "pass": passed, "margin_pct": round((lim["value"] - r["value"]) / lim["value"] * 100, 3)}
    end = out.pop()
    t = end["t"]
    base = {"run_id": run_id, "ts": end["ts"], "agent": "cae.strength", "domain": lc["domain"], "design_version": 2}
    out += [{**base, "t": t, "kind": "comparison.recorded", "data": cmp}, {**base, "t": t + 0.1, "kind": "verdict.recorded", "data": ver}]
    end["t"] = t + 0.2
    out.append(end)
    for i, e in enumerate(out):
        e["seq"] = i
    return out


@unittest.skipUnless(ENABLED, "browser test: run with python -m tests.web.browser (needs Playwright)")
class CutStoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        runs = cls.tmp / "runs"
        shell, _meshes, _shells = shell_events()
        cls.runs = {
            "n03": "20261103-120000-story-n03n06",
            "n04": "20261103-120000-story-grid",
            "n15": "20261103-120000-story-grid3",
            "n07": shell[0]["run_id"],
            "n16": "20261103-120000-story-asm",
            "n16_19": "20261103-120000-story-asm19",
            "n17": "20261103-120000-story-report",
            "n17_long": "20261103-120000-story-reportlong",
            "n07_pass": "20261103-120000-story-n07pass",
            "n07_fail": "20261103-120000-story-n07fail",
        }
        cls.runs["n06"] = cls.runs["n03"]
        for key, events in (("n03", n03_n06_events()), ("n04", domain_events()), ("n15", three_versions()), ("n07", shell),
                            ("n16", assembly_events()), ("n16_19", many_steps_events(19)),
                            ("n17", report_events(REPORT)), ("n17_long", report_events(long_report())),
                            ("n07_pass", with_verdict(shell, "20261103-120000-story-n07pass", True)),
                            ("n07_fail", with_verdict(shell, "20261103-120000-story-n07fail", False))):
            (runs / cls.runs[key]).mkdir(parents=True)
            (runs / cls.runs[key] / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        cls.layout = story_layout()
        layout_file = cls.tmp / "layout.json"
        layout_file.write_text(json.dumps(cls.layout, ensure_ascii=False), encoding="utf-8")
        cls.servers = []
        cls.base = cls.serve(runs, layout_file)
        given = os.environ.get("HIRAKU_WEB_DEV_RUNS")
        cls.dev = None
        if given:
            dev = Path(given)
            run = sorted(p.name for p in dev.iterdir() if (p / "events.jsonl").is_file() and p.name[:1].isdigit())[-1]
            cls.dev = (cls.serve(dev, layout_file), run)
        cls.shots = Path(os.environ["HIRAKU_STORY_SHOTS"]) if os.environ.get("HIRAKU_STORY_SHOTS") else None
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

    @classmethod
    def serve(cls, runs, layout_file):
        server = make_server("127.0.0.1", 0, runs, quiet=True, saved_dir=None, launcher=False, layout_file=layout_file)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        for s in cls.servers:
            s.shutdown()
            s.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def check_cut(self, cut, base, run, query="", tag=None):
        """Open the cut and read it at each check time: no error, the counts within the
        limits, the text sizes, no note in the corner."""
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/cut.html?cut={cut}&run={run}&t=0{query}")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        page.evaluate("window.hiraku.ready")
        lim = limits_of(self.layout, cut)
        seen = {}
        for t in check_times(self.layout, cut):
            got = page.evaluate(READ, t)
            where = f"{cut} t={t}"
            self.assertIsNone(got["err"], where)
            for k, n in got["counts"].items():
                self.assertIn(k, KINDS, (where, k))
                self.assertLessEqual(n, lim[k], (where, k, got["items"][k]))
            for x in got["texts"]:
                least = lim["body_text_px"] if x["kind"] else self.layout["min_text_px"]
                # as promo/layout_check.py's busy check: text inside a named box (less notes)
                # is body text too, on the cuts the review of the first stills went through
                if cut in BOXED_BODY and x["boxed"]:
                    least = lim["body_text_px"]
                self.assertGreaterEqual(x["px"], least - 0.01, (where, x))
            self.assertIsNone(got["note"], where)
            self.assertEqual(got["over"], "0", where)
            self.assertEqual(got["page"]["over"], [], where)
            self.assertEqual(got["page"]["counts"], {k: got["counts"].get(k, 0) for k in KINDS}, where)  # the page counts as this test does
            got["sizes"] = page.evaluate(SIZES)
            if cut in FIT_BOXES:
                self.check_fit(cut, page.evaluate(FIT, FIT_BOXES[cut][1]), where)
            if cut in ("n04", "n15", "n06"):
                self.check_place(cut, page.evaluate(PLACE), where)
            seen[t] = got
            if self.shots:
                self.shots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(self.shots / f"{tag or cut}_t{t:05.2f}.png"))
        self.assertEqual(errors, [], cut)
        page.close()
        return seen

    def check_place(self, cut, got, where):
        """The one shown large in the cut's feature box, the others in its list's slots, row
        by row in the layout's order, all above the subtitle band."""
        sp = self.layout[cut]
        feature, slots = box_of(sp["feature"]), list_rects(sp["list"])
        r4 = lambda r: [round(v) for v in r]  # noqa: E731
        band = self.layout["subtitle_band"]["y"]
        if cut == "n06":
            self.assertEqual([r4(r) for r in got["big"]], [feature] if got["big"] else [], where)
            for i, x in enumerate(got["list"]):
                if x["landed"]:
                    self.assertEqual(r4(x["rect"]), slots[i], (where, i))
                    self.assertLessEqual(x["rect"][1] + x["rect"][3], band + 0.5, (where, i))
            return
        if not got["grid"]:
            return  # N04 after assembly_s: the assembly's box instead
        big = [x for x in got["grid"] if x["featured"]]
        self.assertEqual([r4(x["rect"]) for x in big], [feature], where)
        self.assertEqual([r4(x["rect"]) for x in got["grid"] if not x["featured"]], slots[: len(got["grid"]) - 1], where)
        for x in got["grid"]:
            self.assertLessEqual(x["rect"][1] + x["rect"][3], band + 0.5, where)

    def test_missing_keys_are_named(self):
        """Each key the cut pages read from the promo department's boxes and sizes: without it
        the page says "layout.json の <cut> に無い: <key>" and draws nothing."""
        cases = [("n02", "pane.verdict.of_label"), ("n03", "popup.reading_of_title"), ("n03", "popup.ok_of_reading"),
                 ("n07", "label.of_view_h"), ("n07", "ring.of_view_h"),
                 ("n17", "page.change_of_title"), ("n17", "page.domain_of_title"), ("n17", "dim_opacity"), ("n06", "feature.w"), ("n06", "list.cols"),
                 ("n04", "list.gap_y"), ("n04", "feature.x"), ("n15", "list.rows"), ("n15", "feature.h")]
        for i, (cut, key) in enumerate(cases):
            layout = copy.deepcopy(self.layout)
            *up, last = key.split(".")
            node = layout[cut]
            for k in up:
                node = node[k]
            del node[last]
            layout_file = self.tmp / f"layout-missing-{i}.json"
            layout_file.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
            base = self.serve(self.tmp / "runs", layout_file)
            run = {"n02": "sample", "n07": self.runs["n07"], "n17": self.runs["n17"]}.get(cut, self.runs.get(cut, "sample"))
            page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
            page.goto(f"{base}/cut.html?cut={cut}&run={run}&t=0")
            page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
            err = page.evaluate("document.querySelector('.err')?.textContent ?? ''")
            self.assertIn(f"layout.json の {cut} に無い: ", err, (cut, key))
            self.assertIn(key, err.split("に無い: ")[1], (cut, key))
            page.close()

    def check_fit(self, cut, fit, where):
        """The boxes where the layout puts them, nothing in the subtitle band or past the
        stage, and no text running past its box or cut short."""
        L = self.layout
        rect = lambda b: [b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]]  # noqa: E731
        for key in FIT_BOXES[cut][0]:
            sec, name = key.split(".")
            self.assertEqual([round(v) for v in fit["boxes"][key]], rect(L[sec][name]), (where, key))
        if cut == "n02":
            g = L["n02"]["grid"]
            for i, k in enumerate(L["n02"]["panes"]):
                x, y = g["x"] + (i % g["cols"]) * (g["tile_w"] + g["gap_x"]), g["y"] + (i // g["cols"]) * (g["tile_h"] + g["gap_y"])
                self.assertEqual([round(v) for v in fit["boxes"][f"n02.pane.{k}"]], [x, y, x + g["tile_w"], y + g["tile_h"]], (where, k))
        self.assertLessEqual(fit["lowest"], L["subtitle_band"]["y"] + 0.5, where)
        self.assertLessEqual(fit["right"], L["stage"]["w"] + 0.5, where)
        self.assertEqual(fit["over"], [], where)

    def assert_larger(self, got, n):
        """n large words (the representative; None: one or more) shown, each larger than every other text."""
        sz = got["sizes"]
        if n is None:
            self.assertTrue(sz["large"], sz)
        else:
            self.assertEqual(len(sz["large"]), n, sz)
        self.assertGreater(min(sz["large"]), max(sz["others"]), sz)

    def assert_featured(self, got):
        """One tile large, larger than every other tile."""
        tiles = got["sizes"]["grid"]
        big = [x["area"] for x in tiles if x["featured"]]
        self.assertEqual(len(big), 1)
        self.assertGreater(big[0], 2 * max(x["area"] for x in tiles if not x["featured"]))

    def test_n01(self):
        if not self.dev:
            self.skipTest("needs HIRAKU_WEB_DEV_RUNS (a folder made by tests.web.dev_k2_run)")
        seen = self.check_cut("n01", *self.dev)
        got = next(iter(seen.values()))
        # the two step heights are the two numbers, and the colour bar the one legend
        self.assertEqual(got["counts"].get("number"), 2)
        self.assertEqual(got["counts"].get("legend"), 1)
        nums = [x["text"] for x in got["texts"] if x["kind"] == "number"]
        self.assertEqual(nums, ["100 mm", "250 mm"])
        # one number a card; the verdict words larger than any other text
        for g in seen.values():
            self.assert_larger(g, 2)

    def test_n02(self):
        if not self.dev:
            self.skipTest("needs HIRAKU_WEB_DEV_RUNS (a folder made by tests.web.dev_k2_run)")
        seen = self.check_cut("n02", *self.dev)
        for got in seen.values():
            self.assert_larger(got, None)  # each pane whose record says it: its verdict, large
            self.assertEqual(got["counts"].get("label"), 5)  # one tag per test
            self.assertNotIn("number", got["counts"])
            self.assertFalse(any(c.isdigit() for x in got["texts"] for c in x["text"]), got["texts"])

    def test_n03(self):
        seen = self.check_cut("n03", self.base, self.runs["n03"])
        for t, got in seen.items():
            self.assertEqual(got["counts"].get("graph"), 1)
            self.assertEqual(got["counts"].get("number"), 1)  # the reading
            self.assert_larger(got, 1)

    def test_n04(self):
        seen = self.check_cut("n04", self.base, self.runs["n04"], "&version=1")
        grid = [g for t, g in seen.items() if t < self.layout["n04"]["times"]["assembly_s"]]
        for got in grid:
            self.assert_featured(got)
            # pictures and marks only: the one number is the header's
            self.assertLessEqual(got["counts"].get("number", 0), 1)
            tile_text = [x for x in got["texts"] if x["kind"] == "label"]
            self.assertTrue(all(x["text"] in ("✓", "✕") for x in tile_text), tile_text)

    def test_n05(self):
        base, run = self.dev if self.dev else (self.base, "sample")
        seen = self.check_cut("n05", base, run)
        late = seen[max(seen)]
        self.assertEqual(late["counts"].get("label"), 12)
        self.assertNotIn("graph", late["counts"])

    def test_n05_without_the_hero(self):
        base, run = self.dev if self.dev else (self.base, "sample")
        self.check_cut("n05", base, run, "&hero=0", tag="n05_hero0")

    def test_n06(self):
        seen = self.check_cut("n06", self.base, self.runs["n06"])
        last = seen[max(seen)]
        self.assertEqual(last["counts"].get("label"), 6)  # five in the list, one in the large box
        self.assertNotIn("number", last["counts"])
        for t, got in seen.items():
            n = got["sizes"]["n06"]
            self.assertEqual(len(n["big"]), 1, t)  # one at a time, large
            self.assertGreater(n["big"][0], 4 * max(n["small"]), t)

    def test_n07(self):
        seen = self.check_cut("n07", self.base, self.runs["n07"])
        for got in seen.values():
            self.assertEqual(got["counts"].get("legend"), 1)
            self.assertNotIn("number", got["counts"])  # the legend is the stress and the allowable only
            self.assertEqual(got["counts"].get("label"), 1)
            self.assert_larger(got, 1)
            self.assertEqual(got["sizes"]["fixed"], [])  # no verdict in this run: nothing added

    def test_n02_legend_at_one_and_a_half_times_the_small_text(self):
        """The legend's words (重心, the red of the case past the limit) and their marks are at
        least 1.5 x the small text (max(min_text_px, pane.rate.size_px)) and stay in their pane."""
        L = self.layout
        small = max(L["min_text_px"], L["n02"]["pane"]["rate"]["size_px"])
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{self.base}/cut.html?cut=n02&run=sample&t=0")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        got = page.evaluate("""() => {
          const ul = document.querySelector(".n02-legend");
          const pane = ul.closest(".n02-pane").getBoundingClientRect();
          return { px: parseFloat(getComputedStyle(ul).fontSize), words: [...ul.querySelectorAll("li span")].map((s) => s.textContent),
                   marks: [...ul.querySelectorAll("li > svg, li > i")].map((m) => m.getBoundingClientRect().height),
                   right: Math.max(...[...ul.querySelectorAll("li span")].map((s) => s.getBoundingClientRect().right)), paneRight: pane.right };
        }""")
        page.close()
        self.assertEqual(got["words"], ["重心", "半透明の赤: 限界の外の条件の動き"])
        self.assertGreaterEqual(got["px"], 1.5 * small)
        self.assertGreaterEqual(min(got["marks"]), 1.5 * 16)  # the marks grow with the words (16 px the smallest before)
        self.assertLessEqual(got["right"], got["paneRight"])

    def test_n07_fixed_only_when_the_verdict_passes(self):
        """補強の後 says "✓ OK" in green only when the record judges version 2 a pass."""
        swap_to = self.layout["n07"]["times"]["swap_to_s"]
        for key, passed in (("n07_pass", True), ("n07_fail", False)):
            seen = self.check_cut("n07", self.base, self.runs[key], tag=key)
            for t, got in seen.items():
                sz = got["sizes"]
                if t >= swap_to and passed:
                    self.assertEqual(sz["fixed"], [["✓ OK", "rgb(47, 125, 79)"]], (key, t))
                    self.assertIn("補強の後 ✓ OK", sz["label"])
                else:
                    self.assertEqual(sz["fixed"], [], (key, t))
                    self.assertNotIn("OK", sz["label"], (key, t))

    def test_n15(self):
        seen = self.check_cut("n15", self.base, self.runs["n15"])
        for got in seen.values():
            self.assertEqual(got["counts"].get("number"), 1)  # the rounds
            self.assert_featured(got)

    def test_n16(self):
        for key in ("n16", "n16_19"):
            seen = self.check_cut("n16", self.base, self.runs[key], tag=key)
            for got in seen.values():
                rows = [x for x in got["texts"] if x["text"][:1].isdigit() and x["kind"] is None]
                self.assertLessEqual(len({x["text"][:2] for x in rows}), 3 * 2)  # the step numbers of three rows (and their names)

    def test_n17(self):
        light = self.layout["n17"]["times"]["light_s"]
        for key in ("n17", "n17_long"):
            seen = self.check_cut("n17", self.base, self.runs[key], tag=key)
            for t, got in seen.items():
                self.assertEqual(got["counts"].get("label"), 1, t)  # the next step
                self.assert_larger(got, 1)
            lit = [g for t, g in seen.items() if t >= light]
            self.assertTrue(lit)

    def test_the_corner_note_says_what_is_over(self):
        """With limits lower than what a cut shows, the page says so in its corner."""
        layout = copy.deepcopy(self.layout)
        layout["story"]["cuts"]["n06"]["limits"] = {"label": 2, "why": "test"}
        layout_file = self.tmp / "layout-low.json"
        layout_file.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
        base = self.serve(self.tmp / "runs", layout_file)
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{base}/cut.html?cut=n06&run={self.runs['n06']}&t=0")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        got = page.evaluate(READ, self.layout["n06"]["duration_s"] - 0.1)
        self.assertEqual(got["over"], "1")
        self.assertIn("上限を越えた: label 6 > 2", got["note"])
        page.close()
        # a story without its defaults is named
        del layout["story"]["defaults"]["moving"]
        layout_file.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
        page = self.browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(f"{base}/cut.html?cut=n06&run={self.runs['n06']}&t=0")
        page.wait_for_function("window.hiraku && window.hiraku.ready", timeout=120000)
        self.assertIn("layout.json の story.defaults に無い: moving", page.evaluate("document.querySelector('.err')?.textContent ?? ''"))
        page.close()


if __name__ == "__main__":
    unittest.main()

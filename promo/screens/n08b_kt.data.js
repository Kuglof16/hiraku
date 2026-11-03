// Generated file: do not edit. Written by promo/kinetic_data.py (rules and values of n08b_kt).
window.KT_DATA = {
 "cut": "n08b_kt",
 "rules": {
  "duration_s": 10.0,
  "voice": [
   [
    0.3,
    4.53
   ],
   [
    4.88,
    9.53
   ]
  ],
  "view": {
   "perspective_px": 1500
  },
  "floor": {
   "w": 2400,
   "d": 3200,
   "near_px": 500,
   "cell_px": 80,
   "alpha": 0.18,
   "_note": "a faint grid on the floor the figures stand on (the paper grid of N00, light on the night): w wide, d deep, its near edge near_px in front of the figures (kept in front of the camera at every angle)"
  },
  "num": {
   "cy": 380,
   "size_px": 360,
   "unit_em": 0.46,
   "layers": 40,
   "depth_px": 150,
   "_note": "the figure, extruded: layers copies of the text one behind another over depth_px; centred on x 960, its middle at cy (before the camera)"
  },
  "cap": {
   "y": 676,
   "h": 84,
   "size_px": 56,
   "gap_px": 22,
   "mid_scale": 1.3
  },
  "src": {
   "x": 96,
   "y": 836,
   "w": 1728,
   "h": 64,
   "size_px": 20,
   "_note": "two lines at most (line height 1.6); bottom 900, above the subtitle band"
  },
  "beats": [
   {
    "key": "sme",
    "num_in_s": 0.25,
    "count_s": [
     0.45,
     1.75
    ],
    "out_s": [
     4.45,
     4.95
    ],
    "cam_from": {
     "ry_deg": -40,
     "rx_deg": 16,
     "z_px": -900
    },
    "cam_to": {
     "ry_deg": -16,
     "rx_deg": 8,
     "z_px": 0
    },
    "cam_s": [
     0.0,
     4.45
    ],
    "words_s": [
     1.85,
     2.15,
     2.85
    ],
    "src_s": 3.2
   },
   {
    "key": "small",
    "num_in_s": 7.35,
    "count_s": [
     7.55,
     8.45
    ],
    "out_s": null,
    "cam_from": {
     "ry_deg": 36,
     "rx_deg": 22,
     "z_px": -600
    },
    "cam_to": {
     "ry_deg": 14,
     "rx_deg": 8,
     "z_px": 0
    },
    "cam_s": [
     7.35,
     9.6
    ],
    "words_s": [
     5.15,
     6.2
    ],
    "src_s": 8.6
   }
  ],
  "word_in_s": 0.4,
  "count_steps": 40
 },
 "values": {
  "from": "promo/screens/n08b.html (DATA)",
  "manufacturing": {
   "all": 337490,
   "sme": 335552,
   "sme_pct": 99.42576076328187,
   "shown": 99,
   "source": "中小企業白書 2025 付属統計資料 1 表、2021 年 6 月"
  },
  "cae_adoption": {
   "small": {
    "label": "従業員 100 人以内",
    "pct": 16.0,
    "shown": 16
   },
   "large": {
    "label": "1 万人超",
    "pct": 67.9
   },
   "source_short": "「fabcross for エンジニア」調べ (メイテック、2017 年)"
  }
 }
};

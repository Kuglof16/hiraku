"""A record shaped like the real ones a first-time viewer opened (20261104-201501-n15-v5-round2m,
not copied: its shapes only), for the layout lint and the screen's tests.

On top of tests.web.domains_fixture (every domain, two versions):
- many load cases (more than app.js MANY_TESTS), several sharing a test id (R8 x 3, T2 x 2, H3 x 2),
  with titles in the real record's way: the chips' row scrolls and the shared ids get short names
- a long change reason (the grid's 版 1 → 2 で変えたこと line)
- log lines with long ids (mass_props.front_wheel_load) and asides in brackets, in the narrow log

Shape examples: the words and numbers are not results.
"""

from __future__ import annotations

import copy

from tests.web.domains_fixture import domain_events

RUN_ID = "20261105-200000-firsttime"

# (test id, load case id, domain, title): the ids repeat as in the real record
EXTRA_CASES = [
    ("R8", "R8-free", "strength", "どの部品も、枠まで繋がっている (横 0.7 g・前後 0.9 g・下 1 g を全部の部品と乗員に、2 人 + 荷)"),
    ("R8", "R8-stress", "strength", "部品と乗員の重さと加速度 (横 0.7 g・前後 0.9 g・下 1 g) で、各部材の応力をその材料の許しと比べる (2 人 + 荷)"),
    ("R8", "R8-push", "strength", "乗員の肩・腰 (点 R) を 725 N で外へ押す (2 人 + 荷)"),
    ("R7", "R7-hand", "strength", "手の操作部の握る点を 300 N で前後・上下・回す向きに押す (2 人 + 荷)"),
    ("R7", "R7-foot", "strength", "足の操作部の踏む面を 500 N で踏む (2 人 + 荷)"),
    ("T2", "T2-motor", "thermal", "気温 40 ℃ で林道の周回を 20 km 走ったときのモーターの温度 (2 人 + 荷)"),
    ("T2", "T2-controller", "thermal", "気温 40 ℃ で林道の周回を 20 km 走ったときの制御器の温度 (2 人 + 荷)"),
    ("H3", "H3-body", "assembly", "体格の範囲の両端で座れるか・膝と頭が当たらないか (2 人 + 荷)"),
    ("H3", "H3-knee", "assembly", "運転者のペダルを踏む膝の角度が、体格の両端で楽な範囲にあるか (2 人 + 荷)"),
    ("H6", "H6-spray", "assembly", "車輪が巻き上げた物 (踏面の、車軸の真上から前 30 度・後ろ 50 度) が乗員に届かないか (2 人 + 荷)"),
    ("H5", "H5-order", "assembly", "部品を付ける順番があるか (先に付けないと入らない部品)"),
    ("P4", "P4-20km", "powertrain", "林道の周回を 20 km 走って電池が残るか (2 人 + 荷)"),
]
CHANGE_REASON = (
    "R1s: 横の部材の端がレールの側の壁を板として曲げ、つなぎ目のホットスポットが限界を超えるので、レールの肉厚を 2.3 mm から 4.5 mm にし、"
    "つなぎ目に脚 80 mm・厚さ 3.2 mm のガセットを足し、横の部材の高さを 40 mm から 50 mm にする (形の見本)"
)
SAID = [
    ("cae.powertrain", "予測を記録した: 前輪の荷重 = 1,231 N (mass_props.front_wheel_load)。台本に書いてある原因を記録します (形の見本)"),
    ("lead", "要件のファイルのまま進みます (既定値で足した 4 件: Q57・Q63・Q93・Q94)。この実行の試験 (P1・R1・R1s・R3・S1・F1・S3・S4・S5・P2・P3・P4・T1・T2・C1・N1・R8)"),
    ("cae.strength", "ツール「荷重の通り道 (部品を剛体か板の帯に、触れている所をばねに)」の照合: 使える。record_prediction(load_case_id=\"H1-interference\", ...) で記録"),
]


def firsttime_events() -> list[dict]:
    events = copy.deepcopy(domain_events())
    run = events[0]
    run["run_id"] = RUN_ID
    cases = run["data"]["load_cases"]
    for tid, lc, domain, title in EXTRA_CASES:
        cases.append({"load_case_id": lc, "test_id": tid, "domain": domain, "title": title, "params": {}, "tolerance_rel": 0.15})
    for ev in events:
        ev["run_id"] = RUN_ID
        if ev["kind"] == "design.created" and ev["data"].get("parent_version") is not None:
            ev["data"]["change_reason"] = CHANGE_REASON
    # the agents' lines just after the start
    t0 = events[1]["t"]
    said = [{"run_id": RUN_ID, "ts": run["ts"], "t": t0 + 0.1 * (k + 1), "kind": "agent.said", "agent": who, "text": text}
            for k, (who, text) in enumerate(SAID)]
    out = events[:2] + said + events[2:]
    for k, ev in enumerate(out):
        ev["seq"] = k
    return out

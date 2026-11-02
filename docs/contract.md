# 契約 (部署のあいだの決まり)

形の正本は [`hiraku/contract/hiraku.schema.json`](../hiraku/contract/hiraku.schema.json)。この文書は読み方の案内で、食い違ったら schema が正しい。schema を変えたいときは統括部に頼む (部署の中で勝手に足さない)。

```python
from hiraku import contract
contract.validate("prediction", obj)   # 合わなければ ValueError (全部の食い違いを列挙)
contract.errors("event", line)         # 食い違いの一覧 (空なら合っている)
contract.EVENT_KINDS, contract.DOMAINS, contract.UNITS
```

形の見本は [`hiraku/contract/examples/`](../hiraku/contract/examples/)。**見本の数値は形を見せるためのもので、計算の結果ではない**。画面部は `events.sample.jsonl` を再生の練習に使ってよい。

## フォルダと持ち場

| 部署 | 書くところ |
|---|---|
| 統括部 | `hiraku/contract/`、`tests/contract/`、`docs/`、`README.md`、`CLAUDE.md`、`requirements.txt`、`.gitignore` |
| 設計部 | `hiraku/agent/` (ループ・hooks・役の定義)、`hiraku/tools/` (MCP の道具)、`hiraku/store/` (runs の読み書き)、`hiraku/catalog/`、`hiraku/__main__.py`、`data/catalog/`、`data/requirements/`、`plugins/` (skills)、`tests/agent/`・`tests/tools/`・`tests/store/` |
| 解析部 | `hiraku/solvers/` (`k0_handcalc`・`k1_frame`・`k2_vehicle`・`k3_power_thermal`・`k4_geometry`・`k5_sensitivity`)、`data/designs/`、`data/materials/`、`tests/solvers/` |
| 画面部 | `web/`、`hiraku/webserve/`、`tests/web/` |
| 品質保証部 | `tests/verification/`、`qa/` (Rapier の照合は `qa/rapier/`、Node はここだけ) |
| 広報部 | `promo/` |

機械で引く表は `hiraku/contract/layout.py` の `OWNERS` (`owner_of(path)`)。

## 座標と単位

- 設計 (design) の座標は mm。軸は ISO 8855 (x 前、y 左、z 上)。原点は前の車軸の真ん中の真下の地面。だから前の車軸より後ろは x が負で、後ろの車軸は x = −ホイールベース。前後は x の大小で決め (大きいほど前)、「x が最小 = 前」としない
- design の数値は欄の名前に単位を付ける (`position_mm`、`mass_kg`、`E_MPa`)
- それ以外の数値は `{"value": 数, "unit": "単位"}`。単位は schema の `unit` の一覧だけ。照合は同じ単位の文字どうしでしかしない (変換は K0 の道具でしてから)

## 1 つの数字を信用して合否を出すまで

1. `record_prediction` — Claude は式 (`formula_id`) と入力と前提を選ぶだけ。値は K0 が計算して `prediction` にする
2. `analyze` — 道具の名前・荷重条件・予測の ID を渡す。全部の解析の道具をこの 1 つの入口から呼ぶ (実行の途中で Claude が作った道具も、MCP の道具を足し直さずに呼べるように)
3. `compare` — 予測と解析の結果から `comparison`。差は符号つき: `diff_abs` = 解析 − 予測、`diff_rel` = (解析 − 予測) / |予測|。|`diff_rel`| ≤ `tolerance_rel` なら一致。許す幅 `tolerance_rel` は荷重条件に入っていて、モデルは決めない。一致して基準があれば、同じ道具の中で `verdict` も作る
4. `record_cause` — 食い違いの原因と直し方。書くまでその分野は先に進めない。原因が説明でき、CAE 役が「安全側で見られている」と判断したときは `safe_side: true` と理由 `safe_side_reason` を付ける。道具は数字の向きを確かめ (基準が `<=` なら予測 ≥ 解析、`>=` なら予測 ≤ 解析。不利な方が予測の側にあること)、合っていれば不利な方の値で `verdict` を作る (`basis: "explained_safe_side"`、`value_from: "prediction"`、`cause_id`)。向きが逆なら断る
5. 判定 `judgement` — 分野 (7 分野 + 組み立て) ごとに合否をまとめたもの。A6 のタイル

合否 (`verdict`) は道具の中でだけ作る: 一致した照合から (`compare`、`basis: "match"`、値は解析) か、説明された安全側の食い違いから (`record_cause`、上の 4)。`basis` が無いものは `match`。

hooks が守らせること (`hiraku/contract/protocols.py` の `HOOK_RULES`。止めたら `hook.denied` の event に規則の ID を入れる):

| ID | 中身 |
|---|---|
| H1 | 同じ版・同じ荷重条件の予測が無い `analyze` は止める |
| H2 | 照合 (答えの分かっている例) に通っていない道具の `analyze` は止める |
| H3 | 原因の無い食い違いがある分野では `analyze` と `compare` を止める |
| H4 | 選んだ分野にまだ判定の無いものがある間は終われない (上限に達したときだけ止まる。止めた回数に上限を付ける) |

## 解析部と設計部のあいだ (Python)

`hiraku/contract/protocols.py`:

- 入口は `hiraku/solvers/__init__.py` の 2 つだけ: `formula_registry()` (`FormulaRegistry` を返す) と `builtin_tools()` (`AnalysisTool` の list を返す)。`hiraku.solvers` を import しただけで何かが起きないようにする
- `FormulaRegistry` — `list_formulas()` と `evaluate(formula_id, inputs)`。K0 の式の台帳
- `AnalysisTool` — `spec()`・`check()`・`run(design, load_case, predictions, params)`。`run` は `analysis_id` の無い `analysis` を返し、ID は store が付ける。設計が悪くても例外にせず `status: "failed"` と、何が悪く何を変えればよいかを `error` に書く
- 上流の荷重条件の結果 (P1 の輪の力 → R1 など) は `params["upstream_analyses"] = {荷重条件の ID: 照合で一致した analysis}` で `run` に渡る。上流の `analysis_id` を `inputs_from` に足すのは呼ぶ側 (設計部)
- 組み立ての順番と干渉は `analysis` の `assembly` (手順ごとの `assembly_step`) と `interferences` に入れる (`effects` に混ぜない)
- 道具の名前は棚の中で 1 つに 1 つ。段差越えは `step_climb` (MVP の形: 手計算の釣り合いの輪の力 → K1) と `step_climb_dynamic` (K2 で段差を走らせる) の 2 つ
- 乗員と荷は設計の部品 (`kind` が `occupant` / `cargo`) として持つ。どれを載せるかは荷重条件の `params["loading"]`: `empty`・`driver`・`two`・`two_cargo` (カタログの Q30)。無ければ `two_cargo`

MCP の道具 (サーバーの名前は `hiraku`、道具の名前は `mcp__hiraku__<名前>`): MVP は `get_context`・`list_formulas`・`list_tools`・`record_prediction`・`analyze`・`compare`・`record_cause`。入力の形は schema の `tool_input.*`。結果は記録そのものを JSON の文字列で返す。

## runs/&lt;run_id&gt;/

`hiraku/contract/layout.py` の冒頭に木がある。要点:

- `project.json` — 実行の初めに書く。`status` と要件だけはあとで書き換える
- `events.jsonl` — 1 行 1 event、足すだけ。画面は実行中も再生もこれだけで描く
- `v001/` — 版ごとのフォルダ。`design.json` と、記録の種類ごとのフォルダ (`predictions/pred-0001.json` など)。前の版は書き換えない
- 記録のファイルに書いたものは、同じ中身を 1 つの event の `data` にも入れる
- 記録を後から直すとき (原因が「直った」になる、照合に `cause_id` が付く): 同じ種類の event を、同じ ID と直したあとの全部の中身でもう一度出し、ファイルもそれに合わせる。ID ごとに最後の event が正で、ファイルはいつも最後の event の `data` と同じ。`design.json` だけは直さず、変えるときは次の版にする
- ID は実行の中で通し番号 (`pred-0001`・`ana-0001`・`cmp-0001`・`cause-0001`・`ver-0001`)
- `runs/` はコミットしない

## events.jsonl の 1 行

```json
{"seq": 4, "ts": "2026-11-02T20:30:02.000+09:00", "t": 2.0, "run_id": "20261102-203000-offroad-4w",
 "kind": "hook.denied", "agent": "cae.strength", "domain": "strength",
 "text": "予測が無いので解析は止めました。…",
 "data": {"hook": "PreToolUse", "tool": "mcp__hiraku__analyze", "rule": "H1_prediction_first"}}
```

- `seq` は 0 から抜けなし。`t` は `run.started` からの秒で、再生の間合いはこれで取る
- `kind` は schema の `event_kind` の一覧だけ。`*.recorded`・`design.created`・`analysis.finished`・`judgement.updated`・`tool.checked`・`run.started` の `data` は記録そのもの
- `agent` は `lead` (まとめ役)・`designer` (設計役)・`packager` (パッケージ役)・`cae.<分野>`・`system`・`user`。ペットは `agent` 1 つに 1 匹

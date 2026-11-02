---
name: predict-then-analyze
description: 荷重条件 1 つを、手計算の予測 → 解析 → 照合 → 合否まで進める手順。CAE 役が担当の荷重条件を始めるときに使う
---

# 予測してから解析する (荷重条件 1 つ分)

数値は全部、道具が出す。ここで選ぶのは式・入力・道具だけ。自分で計算した数を入力や文に書かない。

## 1. 見る

- `get_context` の `load_cases` から担当の荷重条件を探す。見るところ:
  - `params`: 段差の高さ・速さ・乗員と荷の質量など。予測の入力にそのまま写せる
  - `criterion`: 合否の量 (`quantity`)・向き (`op`)・限界 (`limit`)。この量の予測が無いと合否は付かない
  - `tolerance_rel`: 予測と解析の差をどこまで許すか。決まっているので変えられない
  - `feeds`: この荷重条件の結果を入力に使う、下流の荷重条件
- 上流 (自分を `feeds` で指している荷重条件) があれば、そちらを先に照合で一致させる。一致していない上流があると `analyze` は止まる
- `design` と `design_summary`: 質量の和・重心・ホイールベース・部材の長さ・断面の寸法。入力はここから写す

## 2. 道具と式を選ぶ

- `list_tools`: `tests` に試験の番号 (`test_id`) があり、`usable` が true の道具を選ぶ
- `list_formulas(tool=...)`: その道具の量を予測する K0 の式。入力の名前と単位、出力、前提を読む

## 3. 予測を記録する

- `record_prediction(load_case_id, quantity, formula_id, inputs, assumptions, output?)`
  - `inputs` は `{"名前": {"value": 数, "unit": "単位"}}`。値と単位は get_context か前の記録から写す。単位は式の一覧の単位に合わせて選ぶ (換算は K0 がする。次元が違えば K0 が理由つきで断る)
  - 出力が 2 つ以上ある式では `output` でどれを予測にするか選ぶ
  - `quantity` は解析の結果と同じ名前にする (照合は同じ名前どうし)
  - 中間の量 (前輪の荷重など) も予測として記録し、その値を次の予測の入力に写してよい
  - `assumptions` には自分が置いた前提を短く書く (例: 「レールを単純支持の梁とみる」)

## 4. 解析する

- `analyze(tool, load_case_id, prediction_ids)`。`prediction_ids` は同じ版・同じ荷重条件の予測だけ
- 失敗 (`status: failed`) したら `error` を読む。設計が悪いなら、その文をまとめ役に伝える

## 5. 照合する

- `compare(prediction_id, analysis_id)`。結果の名前が予測と違うときだけ `result_quantity` を付ける
- 一致して `criterion` があれば、合否 (`verdict`) と分野の判定が付く
- 食い違ったら、その分野は `record_cause` を書くまで止まる。手順は skill `hiraku:find-the-cause`

## 6. 報告する

荷重条件ごとに 1 行: 予測の値 (pred-…)・解析の値 (ana-…)・差 (cmp-…)・合否 (ver-…)。値は記録からそのまま引く。

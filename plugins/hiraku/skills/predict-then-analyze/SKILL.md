---
name: predict-then-analyze
description: 荷重条件 1 つを、手計算の予測 → 解析 → 照合 → 合否まで進める手順。CAE 役が担当の荷重条件を始めるときに使う
---

# 予測してから解析する (荷重条件 1 つ分)

数値は全部、道具が出す。ここで選ぶのは式・入力・道具だけ。自分で計算した数を入力や文に書かない。

## 1. 見る

- `get_context(sections=["load_cases", "design_summary"])` の `load_cases` から担当の荷重条件を探す。見るところ:
  - `params`: 段差の高さ・速さなどの量 (予測の入力にそのまま写せる) と、`loading` (載せる組: empty・driver・two・two_cargo)。乗員と荷は設計の部品 (kind occupant / cargo) で、`loading` が誰と何を載せるかを決める
  - `criterion`: 合否の量 (`quantity`)・向き (`op`)・限界 (`limit`)。この量の予測が無いと合否は付かない
  - `tolerance_rel`: 予測と解析の差をどこまで許すか。決まっているので変えられない
  - `feeds`: この荷重条件の結果を入力に使う、下流の荷重条件
- 上流 (自分を `feeds` で指している荷重条件) があれば、そちらを先に照合で一致させる。一致していない上流があると `analyze` は止まる
- `design_summary` (と、そこに無い値が要るときだけ `sections=["design"]` で設計の全体): 入力はここから写す
  - `design_summary.loadings.<loading>`: 車全体の質量 (`mass`、フレームの部材の重さと、その組で載っている部品の和) と重心 (`cg_x`・`cg_y`・`cg_z`)。荷重条件の `loading` と同じ組を使う。`frame_mass` はこの中に入っているので、足さない
  - `design_summary.loadings.<loading>.rail_joint`: 式 `frame_static.rail_joint_stress` の入力のうち F と W 以外 (設計から読んだもの)。`support` は W にどの車輪の荷重を使うか (上流の解析の `wheel_load_<support>`)
  - ホイールベース・前輪の数・トレッド・部材の長さ (`member_length`)。断面の寸法は `design.sections`

## 2. 道具と式を選ぶ

- `list_tools(test_id=...)`: その試験を解析できる道具が 1 行ずつ返る。`usable` が true の道具を選ぶ (`tool=...` でその道具の全体)
- 梁のモデル (frame_static など) で通っても、同じ試験を解けるシェルの道具 (frame_shell など、管の壁を板で解くもの) が棚にあれば、それも回す
  - 梁は、管と管のつなぎ目の壁の曲げ (応力の集中) を見ない
  - 合否は、荷重条件ごとに新しい方の判定で決まる。シェルは梁のあとに回す
- `list_formulas(tool=...)`: その道具の量を予測する K0 の式 (`formulas`) と、道具の対 (`predicts`)。式の入力の名前と単位、出力、前提を読む
  - `predicts` は、道具のどの結果 (`quantity`) を、どの式 (`formula_id`) のどの出力 (`output`) で予測するかの対。判定の量 (`criterion.quantity`) を予測するときは、その量と対になった式を選ぶ
  - 対の `note` を読む。ばねのある道具 (傾斜台の tilt など) の横に並ぶ剛体の式 (`tilt.rigid_angle` など) は初めの見積もりで、解析とは食い違う。ばねを入れた式 (`tilt.spring_roll_angle` など) があれば、そちらを判定の量の予測に使う

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
- 一致して `criterion` があれば、合否 (`verdict`、値は解析) と分野の判定が付く
- 食い違ったら、その分野は `record_cause` を書くまで止まる。手順は skill `hiraku:find-the-cause`。原因で差が説明でき、予測が安全側から押さえていると判断できるときだけ、`safe_side` を付けて予測の値で合否を出せる (同じ skill の 5)

## 6. 報告する

荷重条件ごとに 1 行: 予測の値 (pred-…)・解析の値 (ana-…)・差 (cmp-…)・合否 (ver-…)。値は記録からそのまま引く。

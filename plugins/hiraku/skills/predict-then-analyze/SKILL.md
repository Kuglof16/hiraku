---
name: predict-then-analyze
description: 荷重条件 1 つを、手計算の予測 → 解析 → 照合 → 合否まで進める手順。CAE 役が担当の荷重条件を始めるときに使う
---

# 予測してから解析する (荷重条件 1 つ分)

数値は全部、ツールが出す。ここで選ぶのは式・入力・ツールだけ。自分で計算した数を入力や文に書かない。

## 1. 見る

- `get_context(sections=["load_cases", "design_summary"])` の `load_cases` から担当の荷重条件を探す。見るところ:
  - `params`: 段差の高さ・速さなどの量 (予測の入力にそのまま写せる) と、`loading` (載せる組: empty・driver・two・two_cargo)。乗員と荷は設計の部品 (kind occupant / cargo) で、`loading` が誰と何を載せるかを決める
  - `criterion`: 合否の量 (`quantity`)・向き (`op`)・限界 (`limit`)。この量の予測が無いと合否は付かない
  - `tolerance_rel`: 予測と解析の差をどこまで許すか。決まっているので変えられない
  - `feeds`: この荷重条件の結果を入力に使う、下流の荷重条件
- 上流 (自分を `feeds` で指している荷重条件) があれば、そちらを先に照合で一致させる。一致していない上流があると `analyze` は止まる
- `design_summary` (と、そこに無い値が要るときだけ `sections=["design"]`。返るのは設計の要約 (数・ID・変数の値・断面・材料) で、部品・部材・節・支持の中身は `design_part`・`offset`・`limit` で少しずつ読む): 入力はここから写す
- 設計を直した版 (版 2 から) では、get_context の最初に `design_changes` (前の版から変わった変数の前と後、理由) が付く。前の版の値を使い回さない
  - `design_summary.loadings.<loading>`: 車全体の質量 (`mass`、フレームの部材の重さと、その組で載っている部品の和) と重心 (`cg_x`・`cg_y`・`cg_z`)。荷重条件の `loading` と同じ組を使う。`frame_mass` はこの中に入っているので、足さない
  - `design_summary.loadings.<loading>.rail_joint`: 式 `frame_static.rail_joint_stress` の入力のうち F と W 以外 (設計から読んだもの)。`support` は W にどの車輪の荷重を使うか (上流の解析の `wheel_load_<support>`)
  - ホイールベース・前輪の数・トレッド・部材の長さ (`member_length`)。断面の寸法は `design.sections`

## 1b. 式の入力を読む (record_prediction の前に必ず)

- `get_context(sections=["formula_inputs"], load_case_id=...)`: 荷重条件ごとに、計画の式 (`formulas`) と、その入力を、台本の運転と同じコードがこの版の設計・荷重条件・記録から出した値と単位で返す。入力ごとの `from` は値の出どころ (読み手の関数・design_summary・荷重条件の params・上流の照合で一致した解析など)
  - `role`: `criterion` は判定の量の式、`intermediate` は途中の量 (前輪の荷重など) の式
  - `refused`: その式はこの設計に当てはまらない (読み手の理由つき)。その式は使わない
  - `missing`: 上流の記録 (上流の解析・同じ荷重条件の前の予測) が揃ってから値が出る入力。揃えたあとにもう一度読む
  - `no_reader`: 入力をコードで読む手順が無い式。list_formulas で入力を見て自分で入れ、出どころを `assumptions` に書く
- 入力はここから写す。別の荷重条件・別の製品 (介護ベッドなど) の式や値を借りない
- 設計を直した版では、値は新しい版のもの。前の版で読んだ値を使い回さない

## 2. ツールと式を選ぶ

- `list_tools(test_id=...)`: その試験を解析できるツールが 1 行ずつ返る。`usable` が true のツールを選ぶ (`tool=...` でそのツールの全体)
- ツールの `stage` と荷重条件の `stage` (get_context の load_cases) は重さの段 (0 レイアウトと規則・1 軽い計算・2 車の動き・3 重い計算)。軽い段から回す。その版で軽い段の荷重条件に赤か未判定が残る間は、重い段のツールの analyze は止められる (HK5)。止められたら、理由に出た荷重条件を先に済ませるか、赤ならまとめ役に返して設計役に直してもらう。同じ段の荷重条件は並べて回してよい
- 梁のモデル (frame_static など) で通っても、同じ試験を解けるシェルのツール (frame_shell など、管の壁を板で解くもの) が棚にあれば、それも回す
  - 梁は、管と管のつなぎ目の壁の曲げ (応力の集中) を見ない
  - 合否は、荷重条件ごとに新しい方の判定で決まる。シェルは梁のあとに回す
- `list_formulas(tool=...)`: そのツールの量を予測する K0 の式 (`formulas`) と、ツールの対 (`predicts`)。式の入力の名前と単位、出力、前提を読む
  - `predicts` は、ツールのどの結果 (`quantity`) を、どの式 (`formula_id`) のどの出力 (`output`) で予測するかの対。判定の量 (`criterion.quantity`) を予測するときは、その量と対になった式を選ぶ
  - 対の `note` を読む。ばねのあるツール (傾斜台の tilt など) の横に並ぶ剛体の式 (`tilt.rigid_angle` など) は初めの見積もりで、解析とは食い違う。ばねを入れた式 (`tilt.spring_roll_angle` など) があれば、そちらを判定の量の予測に使う
- 試験に合うツールが棚に無いときは、ツールを書く: `get_tool_template` で見本 (通して動くコードと、クラス・analyse の引数・結果の欄・答えの分かっている例 known_answers と check_record の決まり) を見て、`check_tool(code)` で試し、通ったら `make_tool(code, reason)`

## 2b. シェルのつなぎ目のホットスポット

シェルのツール (`frame_shell`) の `max_hot_spot_stress` が、梁の式 (`frame_static.rail_joint_stress` など) の予測を大きく超え、結果の `where` がつなぎ目のときは、梁の式では予測できない。横の部材の端がレールの側の壁を板として曲げている。

- `get_context(sections=["joint_wall_inputs"], load_case_id=...)`: 式 `frame_static.joint_wall_bending` の入力 (横の部材の端のモーメント `M`、断面、板の寸法など) を、コードが設計と荷重条件から出して返す
- `record_prediction(quantity="max_hot_spot_stress", formula_id="frame_static.joint_wall_bending", output="sigma", inputs=返った inputs)` で予測し、その ID でシェルを `analyze` → `compare`
- はしご形でないフレームでは入力を出せず、理由つきで断られる。そのときは原因にそう書く

## 3. 予測を記録する

- `record_prediction(load_case_id, quantity, formula_id, inputs, assumptions, output?)`
  - `inputs` は `{"名前": {"value": 数, "unit": "単位"}}`。値と単位は get_context の `formula_inputs` (無ければ design_summary か前の記録) から写す。単位は式の一覧の単位に合わせて選ぶ (換算は K0 がする。次元が違えば K0 が理由つきで断る)
  - 出力が 2 つ以上ある式では `output` でどれを予測にするか選ぶ
  - `quantity` は解析の結果と同じ名前にする (照合は同じ名前どうし)
  - 中間の量 (前輪の荷重など) も予測として記録し、その値を次の予測の入力に写してよい
  - `assumptions` には自分が置いた前提を短く書く (例: 「レールを単純支持の梁とみる」)

## 4. 解析する

- `analyze(tool, load_case_id, prediction_ids)`。`prediction_ids` は同じ版・同じ荷重条件の予測だけ
- 失敗 (`status: failed`) したら `error` を読む。設計が悪いなら、その文をまとめ役に伝える
- 失敗した荷重条件は確かめていない。軽い段のツールの合格で代えない (前の版で重い段のツールが基準を超えていれば、この版で同じか重い段のツールの結果が出るまで緑にならない)

## 5. 照合する

- `compare(prediction_id, analysis_id)`。結果の名前が予測と違うときだけ `result_quantity` を付ける
- 結果が場所ごとに出ている (results の `where` が部材・部品の ID) とき、予測が 1 つの場所の式 (レールの曲げの式ならレールの部材) なら `where` にその ID を付ける。その場所の結果と比べる。付けないと全部の場所のうち不利な方 (別の部材、たとえば横の部材の最大) と比べてしまい、式の食い違いに見える
  - 合否は、全部の場所のうち不合格に近い方 (判定の量の最大か最小) の結果でしか作られない。選んだ場所がそこでなければ、照合は記録されるが合否は付かない (答えの `note`)。合否には、判定の量の予測を `where` なしでも照合する
- 一致して `criterion` があれば、合否 (`verdict`、値は解析) と分野の判定が付く
- 食い違ったら、その分野は `record_cause` を書くまで止まる。手順は skill `hiraku:find-the-cause`。原因で差が説明でき、予測が安全側から押さえていると判断できるときだけ、`safe_side` を付けて予測の値で合否を出せる (同じ skill の 5)。手計算が値に届かない (式が無い、上から押さえる式がゆるい) ときは、`load_path.free_parts` の予測で力の道がつながることを確かめ、`tool_decides` を付けてツールの値で合否を出す (同じ skill)

## 6. 報告する

荷重条件ごとに 1 行: 予測の値 (pred-…)・解析の値 (ana-…)・差 (cmp-…)・合否 (ver-…)。値は記録からそのまま引く。

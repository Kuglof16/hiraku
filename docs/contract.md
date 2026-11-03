# 契約 (部署のあいだの決まり)

HIRAKU は、Claude Code のセッションを「部署」に見立てて並べて作った。統括部 (契約を書き、各部署の成果を確かめて main に取り込む) と、設計部・解析部 (中に K2 班)・画面部・品質保証部・広報部。各部署は自分の git worktree と branch で、下の表の持ち場のファイルだけを書く。この文書は、その部署どうしが同じ形でつながるための決まり。

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
| 統括部 | `hiraku/contract/`、`tests/contract/`、`docs/`、`showcase/` (見本の実行)、`README.md`、`CLAUDE.md`、`LICENSE`、`requirements.txt`、`.gitignore`、`hiraku/__init__.py`、`tests/__init__.py` |
| 設計部 | `hiraku/agent/` (ループ・hooks・役の定義)、`hiraku/tools/` (MCP の道具)、`hiraku/store/` (runs の読み書き)、`hiraku/catalog/`、`hiraku/__main__.py`、`data/catalog/`、`data/requirements/`、`plugins/` (skills)、`tests/agent/`・`tests/tools/`・`tests/store/` |
| 解析部 | `hiraku/solvers/` (`k0_handcalc`・`k1_frame`・`k2_vehicle`・`k3_power_thermal`・`k4_geometry`・`k5_sensitivity`)、`hiraku/catalog/builders/` (試験ごとの荷重条件の作り方。K2 班は `k2_*.py`)、`hiraku/designgen/` (設計の変数から設計を組む)、`data/designs/`、`data/materials/`、`tests/solvers/` |
| 画面部 | `web/`、`hiraku/webserve/`、`tests/web/` |
| 品質保証部 | `tests/verification/`、`qa/` (Rapier の照合は `qa/rapier/`、Node はここだけ) |
| 広報部 | `promo/` |
| 受付部 | `hiraku/intake/` (分野を選ぶ・ヒアリング・ナレッジの規則)、`data/requirements/products/` (デモ以外の製品の要件)、`data/knowledge/` (最初から入っている規則)、`web/js/intake/` (A1・A2・A5 の画面)、`tests/intake/` |

機械で引く表は `hiraku/contract/layout.py` の `OWNERS` (`owner_of(path)`)。

パッケージの層 (下の層は上の層を import しない。`tests/contract/test_layers.py` が見る): `contract` < `solvers` < `designgen` < `store` < `catalog` < `intake` < `tools` < `agent` < `webserve`・`__main__`。上の層の関数が要るときは、上の層から渡してもらう (引数か登録)

## 座標と単位

- 設計 (design) の座標は mm。軸は ISO 8855 (x 前、y 左、z 上)。原点は前の車軸の真ん中の真下の地面。だから前の車軸より後ろは x が負で、後ろの車軸は x = −ホイールベース。前後は x の大小で決め (大きいほど前)、「x が最小 = 前」としない
- design の数値は欄の名前に単位を付ける (`position_mm`、`mass_kg`、`E_MPa`)。ただし `design.variables` (設計の変数) は `{"value": 数, "unit": "単位"}` の形
- それ以外の数値は `{"value": 数, "unit": "単位"}`。単位は schema の `unit` の一覧だけ。照合は同じ単位の文字どうしでしかしない (変換は K0 の道具でしてから)

## 1 つの数字を信用して合否を出すまで

1. `record_prediction` — Claude は式 (`formula_id`) と入力と前提を選ぶだけ。値は K0 が計算して `prediction` にする
2. `analyze` — 道具の名前・荷重条件・予測の ID を渡す。全部の解析の道具をこの 1 つの入口から呼ぶ (実行の途中で Claude が作った道具も、MCP の道具を足し直さずに呼べるように)
3. `compare` — 予測と解析の結果から `comparison`。差は符号つき: `diff_abs` = 解析 − 予測、`diff_rel` = (解析 − 予測) / |予測|。|`diff_rel`| ≤ `tolerance_rel` なら一致。許す幅 `tolerance_rel` は荷重条件に入っていて、モデルは決めない。一致して基準があれば、同じ道具の中で `verdict` も作る
4. `record_cause` — 食い違いの原因と直し方。書くまでその分野は先に進めない。原因が説明でき、CAE 役が「安全側で見られている」と判断したときは `safe_side: true` と理由 `safe_side_reason` を付ける。道具は数字の向きを確かめ (基準が `<=` なら予測 ≥ 解析、`>=` なら予測 ≤ 解析。不利な方が予測の側にあること)、合っていれば不利な方の値で `verdict` を作る (`basis: "explained_safe_side"`、`value_from: "prediction"`、`cause_id`)。向きが逆なら断る
5. 判定 `judgement` — 分野 (7 分野 + 組み立て) ごとに合否をまとめたもの。A6 のタイル

合否の余裕は `margin_pct` (基準に対する %、合格なら正) と `margin` (基準との差を単位つきで、合格なら正)。基準が 0 のときは `margin_pct` を null にして `margin` で言う。道具が設計にも荷重条件にも無い値を仮定して使ったときは、analysis の `assumptions` に書く。

合否 (`verdict`) は道具の中でだけ作る: 一致した照合から (`compare`、`basis: "match"`、値は解析) か、説明された安全側の食い違いから (`record_cause`、上の 4)。`basis` が無いものは `match`。

hooks が守らせること (`hiraku/contract/protocols.py` の `HOOK_RULES`。止めたら `hook.denied` の event に規則の ID を入れる):

| ID | 中身 |
|---|---|
| HK1 | 同じ版・同じ荷重条件の予測が無い `analyze` は止める |
| HK2 | 照合 (答えの分かっている例) に通っていない道具の `analyze` は止める |
| HK3 | 原因の無い食い違いがある分野では `analyze` と `compare` を止める |
| HK4 | 選んだ分野にまだ判定の無いものがある間は終われない (上限に達したときだけ止まる。止めた回数に上限を付ける) |

## 解析部と設計部のあいだ (Python)

`hiraku/contract/protocols.py`:

- 入口は `hiraku/solvers/__init__.py` の 2 つだけ: `formula_registry()` (`FormulaRegistry` を返す) と `builtin_tools()` (`AnalysisTool` の list を返す)。`hiraku.solvers` を import しただけで何かが起きないようにする
- `FormulaRegistry` — `list_formulas()` と `evaluate(formula_id, inputs)`。K0 の式の台帳
- `AnalysisTool` — `spec()`・`check()`・`run(design, load_case, predictions, params)`。`run` は `analysis_id` の無い `analysis` を返し、ID は store が付ける。設計が悪くても例外にせず `status: "failed"` と、何が悪く何を変えればよいかを `error` に書く
- 上流の荷重条件の結果 (P1 の輪の力 → R1 など) は `params["upstream_analyses"] = {荷重条件の ID: 照合で一致した analysis}` で `run` に渡る。上流の `analysis_id` を `inputs_from` に足すのは呼ぶ側 (設計部)
- 組み立ての順番と干渉は `analysis` の `assembly` (手順ごとの `assembly_step`) と `interferences` に入れる (`effects` に混ぜない)。手順には任意で、留め方 `fastenings` (何に・ボルト/溶接/載せる/ピン)、入れる道筋 `path_mm` (折れ線、最後が付いた位置)、手順の 1 文 `text` (コードで組む) を付ける。先に組んだ小さな組 (サスの角など) を付ける手順は、`part` に組の名前 (例 `corner:FL`) と `group_parts` (組の中の部品)。手と工具の邪魔は `tool_blocked_by` (入れられるが手が入りにくい)、`blocked_by` は入れられない理由
- 板 (シェル) のメッシュ: 版ごとに、コードが設計からフレームのメッシュ (管の壁と板の中面) を作り、`mesh` の記録 (`v<NNN>/meshes/mesh-NNNN.json`、event `mesh.created`) にする。節は `nodes_mm`、要素は節の番号 3 つか 4 つ (`elements`)、要素ごとにどの部材か (`element_member`)、部材の厚さ (`member_thickness_mm`)。解析の結果は field に `mesh_id` と、要素ごとの値 `per_element` か節ごとの値 `per_mesh_node` (変位とモードの形は vec3) を入れ、シェルの応力は `surface` (`top`・`mid`・`bottom`・`max`) を付ける。梁 (K1) の `per_member` と同じ analysis に並べてよい
- メッシュの品質 (pre 処理の点検): mesh の任意の欄 `quality` に、解く前にコードが測った値を入れる。`metrics` (縦横比・反り・ゆがみ・角度・ヤコビアン比など。名前 → `worst`・`p95`・`limit`・`over_limit`・`worst_element`)、任意で `per_element` (画面で色に塗る)、`topology` (自由縁・重なった節・つながっていない組・裏返った法線・負の体積)、`verdict` (`ok`・`warn`・`fail`。fail ならソルバーは解かない)。限度はソルバーの目安で、出典が無ければ仮定と書く
- メッシュを作るのは解析部の `hiraku.solvers.frame_mesh(design)` (入口は任意の 1 つ、`OPTIONAL_SOLVER_ENTRY_POINTS`。mesh_id の無い mesh を返す)。記録するのは `analyze`: 道具が `needs_mesh` (無ければ False) なら、その版の mesh が無いときだけ 1 回作って `mesh.created` を出し (mesh は解析の入力なので、その解析の `analysis.started` より前に出す)、道具の `run` に `params["mesh"]` で渡す。道具は同じ設計から作った mesh かを確かめ、field の `mesh_id` に書く。渡されないとき (照合・単体の試験) は mesh の field を出さず results だけ返す
- サスの取付点は、設計の `support.suspension.hardpoints_mm` (取付点の名前 → vec3、mm) に、設計を作るコードが書く。K2 の関数・梁とシェル・画面は、ここから読む (出どころを 1 つにする)。取付点ごとの力は、analysis の `vectors` に `kind: "joint"` (`at` は `<支持の id>.<取付点の名前>`、`origin_mm` は取付点、`vector_N` は車体が受ける力) で出す。サスのタワーはフレームの部材、金具の板は部品 (kind `bracket`) で持つ
- 接地する所 (`support`) は任意で `tire` (`coefficients`: `data/tyres/` のタイヤの係数のファイル、`relaxation_length_mm`) を持ち、サスペンションは任意で `bushings` (腕の内側のブッシュの半径方向・軸方向とタイロッドの剛性 N/mm、K&C の C) を持つ。無い値は解く側が仮定して analysis の `assumptions` に書く
- `series` は時間 (`t_s`) か、ほかの量 (`x`: `{quantity, unit, values}`。K&C の台のストロークや横力) のどちらか 1 つを横軸に持つ
- 道具の名前は棚の中で 1 つに 1 つ。段差越えは `step_climb` (MVP の形: 手計算の釣り合いの輪の力 → K1) と `step_climb_dynamic` (K2 で段差を走らせる) の 2 つ
- 乗員と荷は設計の部品 (`kind` が `occupant` / `cargo`) として持つ。どれを載せるかは荷重条件の `params["loading"]`: `empty`・`driver`・`two`・`two_cargo` (カタログの Q30)。無ければ `two_cargo`。`driver` は design.parts の並びで最初の occupant、`two` は最初の 2 つ、cargo の部品は `two_cargo` のときだけ載る。occupant の部品が 1 つも無い設計 (座席の無い製品) は誰も載せず、あるのに足りない設計 (1 人分しか無いのに `two`) はその荷重条件に合わない (ValueError)。この決まりは `hiraku/contract/loading.py` の `parts_on_board`・`on_board`・`loading_of` だけに書き、どの部署もそれを呼ぶ (自分で書き直さない)
- 記録の見せ方の約束: vectors の `origin_mm` は力が作用する点。モードの形 (field の kind が `mode_shape`) の `per_node` は、いちばん大きい所を 1 とした相対の変位 (vec3)。試験の場面 (段差・凸凹の路面・坂・傾斜台・パイロン・壁) は analysis の `scene` に道具が書き、画面が手で置かない。`scene` の `position_mm` は箱の中心、`size_mm` は辺の長さ、`rpy_deg` は中心のまわりの回転 (パイロンは点、門は [0, 幅, 0] の線)
- 実行の終わり (`run.finished` の data): `status` と、画面が言い換えるための `stop_code` (`done`・`not_judged`・`round_limit`・`cost_limit`・`turn_limit`・`user_stop`・`error`)。`stop_reason` は人向けの文。対応: `done` ⇔ status `done`、`error` ⇔ `failed`、ほか (`not_judged`・`round_limit`・`cost_limit`・`turn_limit`・`user_stop`) は `stopped`
- 実行を外から止めるとき (画面の止めるボタン) は、実行のフォルダに `stop.request` (`layout.STOP_REQUEST_FILE`) を置く。実行の側はそれを見て、記録を整えて `stopped`・`stop_code` `user_stop` で終わる
- `events.jsonl` に書くのは実行のプロセスだけ (書き手が 1 つなので `seq` が抜けない)。画面のサーバーから走っている実行に伝えるもの (Claude に伝える文、ナレッジのファイルを落とした、など) は、実行のフォルダの `inbox/` (`layout.INBOX_DIR`) に 1 件 1 ファイルの JSON (`<番号>-<種類>.json`、中身は `{"kind": ..., "text": ..., "file": ...}`) で置く。落としたファイルそのものは `knowledge/<filename>` に置く。実行の側がそれを読み、`user.said` や `knowledge.imported` などの event を自分で出して、読んだファイルを消す。ヒアリングの答えは今までどおり `hearing.answer.json`
  - 置き方: 1 件 1 ファイルの `inbox/<n>-<kind>.json`。`n` は整数で、画面のサーバーは inbox にある一番大きい `n` + 1 を付け、実行は `n` の順に読む (読んだファイルは消すので、空になれば同じ `n` がまた使われる)。書きかけを読まないよう `<n>-<kind>.json.tmp` に書いてから名前を変え、実行は `.json` で終わるものだけを読む。中身は `{"kind", "text", "file"}` で、使わない欄は省き、`kind` はファイル名の `<kind>` と同じ
  - `message` (`text`): 実行は `user.said` (agent `user`) を出す。Claude の実行ではまとめ役に渡し (次の道具のあと・番を終えようとしたとき (HK4 の回数に数えない)・役が動いている間に番が終わっていれば新しい番)、台本の運転では渡さないと `agent.said` (system) で言う
  - `knowledge` (`file`、任意で `text`): ファイルは先に `knowledge/<file>` に置く (名前は画面のサーバーが整え、`knowledge/` の直下だけ)。`text` があればまず `user.said`。実行がコードで `import_knowledge` (path) を呼んで `knowledge.imported` を出し、式が要る文があれば Claude の実行ではまとめ役に渡して、同じ path に式を付けてもう一度呼ぶ (`doc_id` は同じ)
  - 読めないもの (JSON でない・知らない kind・欄が無い・ファイルが無い) は `agent.said` (system) で理由を言って消す。読むのは `stop.request` と同じ所 (Claude の実行は 1 秒ごと、台本の運転は手順の切れ目と `analyze` の前)。実行が終わったあとに置かれたものは読まれずに残る
- 設計役が最初の版を作る実行 (要件に `design_file` が無い) では、版 1 ができたあと `project.updated` (data は project 全体) で荷重条件・選んだ分野・役を出し直す
- 報告は `report` の記録と `report.written` の event。数はコードが記録から入れ、Claude は `summary` と `next` だけを書く (文の中の数が記録と合わなければ道具が断る)
- 実行の中で Claude が作った道具 (N13) は、実行のフォルダの `tools/` に試みごとのコード (`<name>-<n>.py`) と照合の記録 (`.check.json`) を残す。試みは通らなくても `tool.checked` (usable false) で記録に出す。道具は別のプロセスで、使える import を絞り、空のフォルダ・API キー無し・時間の上限つきで走る。悪意のあるコードを完全に閉じ込める仕組みではなく、間違いへの守り
- 見本として残す実行は `showcase/<run_id>/` (`layout.SHOWCASE_DIR`。統括部の持ち場。runs/ はコミットしない)。画面は URL に `?run=` が無いとき、ここのいちばん新しい実行を再生する

MCP の道具 (サーバーの名前は `hiraku`、道具の名前は `mcp__hiraku__<名前>`): MVP は `get_context`・`list_formulas`・`list_tools`・`record_prediction`・`analyze`・`compare`・`record_cause`。設計役だけが `revise_design` (変数の変更と理由。`hiraku.designgen` が次の版を組む)。入力の形は schema の `tool_input.*`。結果は JSON の文字列で、`record_prediction`・`analyze` は記録そのもの、`compare` と `record_cause` は記録と、作ったときは `verdict`、`revise_design` は新しい版のまとめ。

## 分野を選ぶ・ヒアリング・ナレッジ (受付部)

event が 5 つ。どれも `run.started` のあと (seq 0 はいつも `run.started`) に出る。実行の前に済ませた分 (要件のファイルと答えのファイルで済む形) も、実行の初めにこの順で出し直し、`project.json` にはもう結果が入っている。

| kind | いつ | data | agent |
|---|---|---|---|
| `domains.picked` | まとめ役が分野を選んだ (A1) | `domains_picked`: `want`・`flags`・`picks` (`domain_pick` の配列)・`off` (選ばなかった分野と理由。`assembly` は入れない)。任意で `speed_class`・`flag_reasons` (フラグごとの理由)・`assembly_test_ids` (組み立てと人の試験 H・G。分野には数えない) | `lead` |
| `hearing.asked` | 足りない要件をまとめて 1 回聞いた (A2) | `hearing_asked`: `questions` (`question` の配列。`ask` は `must` (値がまだ無い) か `confirm` (既定値を確かめる)、答えが無いときの `default` は値と `trust`・`basis` つき) | `lead` |
| `hearing.answered` | 答え (か既定値) がそろった | `hearing_answered`: `answers` (`requirement` と同じ形で、`source` は `hearing` か `default`)、任意で `note` | `lead` |
| `knowledge.imported` | 規則の束が入った (最初から入っている束、渡されたファイル) | `knowledge_imported`: `doc` (`doc_id`・`filename`・`origin`、規則にしなかった行は `skipped` に理由つき) と `rules` (`rule` の配列) | `lead` |
| `rule_check.recorded` | 規則を 1 つ、版の部品に当てた (A5) | `rule_check` の記録そのもの (`v<NNN>/rule_checks/rchk-NNNN.json`) | `packager` |

- `domain_pick` には任意で `test_ids` (その分野で製品のフラグに当てはまる試験) が付く。画面はその数を「当てはまる試験の数」に出す
- ヒアリングで決まった要件は `project.requirements` にも入る。実行の途中で要件が変わったときは `project.updated` で出し直す
- 規則 (`rule`) の `check` は部品の値に当てる式 (Python の bool の部分集合) で、当てるのはコード。モデルは当てた結果を書かない。文から式を作ったのが誰かは `check_by`: `code` (文の型からコードが作った)・`claude` (Claude が書き、コードが検査した)。式が規則と一緒に書いてあったもの (最初から入っている規則、式つきの表) には付けない。出典は `basis`、確かめていないものは `not checked`
- `rule_check` の `pass` は `hits` が空のときだけ true (schema で縛る)。式に要る値が設計に無い部品は `not_evaluable` に、足りない値の名前つきで入れ、当てていない扱い
- 規則の束は event にだけ持ち、ファイルにはしない。`doc_id` ごとに最後の `knowledge.imported` が正 (同じファイルをもう一度渡して式が足されたときは、束を丸ごと出し直す)。渡されたファイルそのものを残すなら `knowledge/<filename>` (`layout.KNOWLEDGE_DIR`)
- 実行の途中で人の答えを待つ形は後で: 実行のフォルダに `hearing.request.json` (質問) を置き、画面が `hearing.answer.json` (答え) を置く (`layout.HEARING_REQUEST_FILE`・`HEARING_ANSWER_FILE`。`stop.request` と同じ作り)
- 規則の分類 `rule_category` の日本語の名前は `contract.RULE_CATEGORY_NAMES_JA`。MCP の道具 `pick_domains`・`ask_user`・`import_knowledge`・`rule_check` の入力の形は schema の `tool_input.<名前>` (受付部の `hiraku/intake/tools.py` はそこから引く)

## runs/&lt;run_id&gt;/

`hiraku/contract/layout.py` の冒頭に木がある。要点:

- `project.json` — 実行の初めに書く。あとで書き換えるのは `status`・`stop_reason`・要件・`cost_usd`・`design_versions` だけ
- `events.jsonl` — 1 行 1 event、足すだけ。画面は実行中も再生もこれだけで描く
- `v001/` — 版ごとのフォルダ。`design.json` と、その版の記録の種類ごとのフォルダ (`predictions/pred-0001.json` など)。設計を直すと次の版のフォルダ (`v002/`) ができ、前の版の `design.json` は書き換えない。前の版の記録は、下の「記録を後から直すとき」の場合だけ書き換わる
- 記録のファイルに書いたものは、同じ中身を 1 つの event の `data` にも入れる。記録の種類と event の種類の対応は `layout.RECORD_EVENT` (逆向きは `layout.RECORD_OF_EVENT`、ID の欄は `<種類>_id`) だけに書き、store と点検はそれを引く
- 記録を後から直すとき (原因が「直った」になる、照合に `cause_id` が付く): 同じ種類の event を、同じ ID と直したあとの全部の中身でもう一度出し、ファイルもそれに合わせる。ID ごとに最後の event が正で、ファイルはいつも最後の event の `data` と同じ。`design.json` だけは直さず、変えるときは次の版にする
- ID は実行の中で通し番号 (`pred-0001`・`ana-0001`・`cmp-0001`・`cause-0001`・`ver-0001`・`rchk-0001`・`mesh-0001`)
- `runs/` はコミットしない

## events.jsonl の 1 行

```json
{"seq": 4, "ts": "2026-11-02T20:30:02.000+09:00", "t": 2.0, "run_id": "20261102-203000-offroad-4w",
 "kind": "hook.denied", "agent": "cae.strength", "domain": "strength",
 "text": "予測が無いので解析は止めました。…",
 "data": {"hook": "PreToolUse", "tool": "mcp__hiraku__analyze", "rule": "HK1_prediction_first"}}
```

- `seq` は 0 から抜けなし。`t` は `run.started` からの秒で、再生の間合いはこれで取る
- `kind` は schema の `event_kind` の一覧だけ。`*.recorded`・`design.created`・`analysis.finished`・`judgement.updated`・`tool.checked`・`run.started` の `data` は記録そのもの (`domains.picked`・`hearing.*`・`knowledge.imported` の data は上の表の形)
- `agent` は `lead` (まとめ役)・`designer` (設計役)・`packager` (パッケージ役)・`cae.<分野>`・`system`・`user`。ペットは `agent` 1 つに 1 匹

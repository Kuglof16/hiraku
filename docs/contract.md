# 契約 (部署のあいだの決まり)

HIRAKU は、Claude Code のセッションを「部署」に見立てて並べて作った。統括部 (契約を書き、各部署の成果を確かめて main に取り込む) と、設計部・解析部 (中に K2 班)・画面部・品質保証部・広報部・受付部、それに HIRAKU をプラグインの道で回して困った所を集める実験部 (コードは書かない)。各部署は自分の git worktree と branch で、下の表の持ち場のファイルだけを書く。この文書は、その部署どうしが同じ形でつながるための決まり。

形の正本は [`hiraku/contract/hiraku.schema.json`](../hiraku/contract/hiraku.schema.json)。この文書は読み方の案内で、食い違ったら schema が正しい。schema を変えたいときは統括部に頼む (部署の中で勝手に足さない)。

```python
from hiraku import contract
contract.validate("prediction", obj)   # 合わなければ ValueError (合わない所を全部並べる)
contract.errors("event", line)         # 合わない所の一覧 (空なら合っている)
contract.EVENT_KINDS, contract.DOMAINS, contract.UNITS
```

形の見本は [`hiraku/contract/examples/`](../hiraku/contract/examples/)。**見本の数値は形を見せるためのもので、計算の結果ではない**。画面部は `events.sample.jsonl` を再生の練習に使ってよい。

## フォルダと持ち場

| 部署 | 書くところ |
|---|---|
| 統括部 | `hiraku/contract/`、`tests/contract/`、`docs/`、`showcase/` (見本の実行)、`README.md`、`CLAUDE.md`、`LICENSE`、`requirements.txt`、`.gitignore`、`hiraku/__init__.py`、`tests/__init__.py` |
| 設計部 | `hiraku/agent/` (ループ・hooks・役の定義)、`hiraku/tools/` (MCP のツール)、`hiraku/store/` (runs の読み書き)、`hiraku/export/` (結果の書き出し: CSV・VTK・報告)、`hiraku/catalog/`、`hiraku/__main__.py`、`data/catalog/`、`data/requirements/`、`plugins/` (skills)、`tests/agent/`・`tests/tools/`・`tests/store/` |
| 解析部 | `hiraku/solvers/` (`k0_handcalc`・`k1_frame`・`k2_vehicle`・`k3_power_thermal`・`k4_geometry`・`k5_sensitivity`)、`hiraku/catalog/builders/` (試験ごとの荷重条件の作り方。K2 班は `k2_*.py`)、`hiraku/designgen/` (設計の変数から設計を組む)、`data/designs/`、`data/materials/`、`viewer/` (結果のビューア。分野ごとのデータのスクリプトと 1 つのページ)、`tests/solvers/`、`tests/viewer/` |
| 画面部 | `web/`、`hiraku/webserve/`、`tests/web/` |
| 品質保証部 | `tests/verification/`、`qa/` (Rapier との比べは `qa/rapier/`、Node はここだけ) |
| 広報部 | `promo/` |
| 流れの解析 (CAD と同じく統括部の受け持ち、作るのは流体解析部) | `hiraku/cfd/`、`hiraku/tools/cfd_tools.py`、`tests/cfd/` |
| 受付部 | `hiraku/intake/` (分野を選ぶ・ヒアリング・ナレッジの規則)、`data/requirements/products/` (デモ以外の製品の要件)、`data/knowledge/` (最初から入っている規則)、`web/js/intake/` (A1・A2・A5 の画面)、`tests/intake/` |

機械で引く表は `hiraku/contract/layout.py` の `OWNERS` (`owner_of(path)`)。

パッケージの層 (下の層は上の層を import しない。`tests/contract/test_layers.py` が見る): `contract` < `solvers` < `designgen` < `store` < `catalog` < `intake` < `tools` < `agent` < `export` < `webserve`・`__main__`。上の層の関数が要るときは、上の層から渡してもらう (引数か登録)

機能ごとの枝と取り込み (2026-11-06 の方針): 1 つの機能は 1 本の枝 (`claude/<部署>-<機能>`、main から切る) で作り、その機能のファイルだけを触る。品質保証部は、通った機能の枝から 1 本ずつ main の上に重ねて試し (CLAUDE.md の試験)、通れば main を進める。1 本が落ちても、ほかの枝は待たない。多くの機能が同じ大きなファイルを触って並べられなかったので、`web/js/app.js`・`web/js/workbench3d.js` (画面部) と `hiraku/designgen/ladder_4w.py` (解析部) は、動きを変えずに、機能ごとのファイルに分ける (試験はそのまま通り、デモの設計のファイルはバイト単位で同じ)

## 座標と単位

- 設計 (design) の座標は mm。軸は ISO 8855 (x 前、y 左、z 上)。原点は前の車軸の真ん中の真下の地面。だから前の車軸より後ろは x が負で、後ろの車軸は x = −ホイールベース。前後は x の大小で決め (大きいほど前)、「x が最小 = 前」としない
- design の数値は欄の名前に単位を付ける (`position_mm`、`mass_kg`、`E_MPa`)。ただし `design.variables` (設計の変数) は `{"value": 数, "unit": "単位"}` の形。設計が選んだのではなく外から持ってきた値や仮定の値 (タイヤの縦の硬さなど) は、任意の欄 `basis` に出どころを書く (規格やカタログの版と表。確かめていなければ `not checked`。仮定なら `assumption`)
- それ以外の数値は `{"value": 数, "unit": "単位"}`。単位は schema の `unit` の一覧だけ。比べは同じ単位の文字どうしでしかしない (変換は K0 のツールでしてから)

## 1 つの数字を信用して判定を出すまで

言葉: 予測と解析の結果を比べた答えは「予想通り」(`match`) か「不一致」(`mismatch`)。基準に対する判定は OK か NG。答えの分かっている例でツールを試すのは「確かめ」(`tool_check`)。コードの名前 (`match`、`red_left` など) はそのまま。

1. `record_prediction` — Claude は式 (`formula_id`) と入力と前提を選ぶだけ。値は K0 が計算して `prediction` にする
2. `analyze` — ツールの名前・荷重条件・予測の ID を渡す。全部の解析のツールをこの 1 つの入口から呼ぶ (実行の途中で Claude が作ったツールも、MCP のツールを足し直さずに呼べるように)
3. `compare` — 予測と解析の結果から `comparison`。差は符号つき: `diff_abs` = 解析 − 予測、`diff_rel` = (解析 − 予測) / |予測|。|`diff_rel`| ≤ `tolerance_rel` なら予想通り。許す幅 `tolerance_rel` は荷重条件に入っていて、モデルは決めない。予想通りで基準があれば、同じツールの中で `verdict` も作る
4. `record_cause` — 不一致の原因と直し方。書くまでその分野は先に進めない。原因が説明でき、CAE 役が「安全側で見られている」と判断したときは `safe_side: true` と理由 `safe_side_reason` を付ける。ツールは数字の向きを確かめ (基準が `<=` なら予測 ≥ 解析、`>=` なら予測 ≤ 解析。不利な方が予測の側にあること)、合っていれば不利な方の値で `verdict` を作る (`basis: "explained_safe_side"`、`value_from: "prediction"`、`cause_id`)。向きが逆なら断る。原因が説明できても、この実行で確かめられる予測がその量に届かない (手計算で解けない、など) ときは `ends_case: true` を付ける。その荷重条件は判定なしで終わり、HK4 は待たず、HK5 は重い段を止めない (タイルは判定なしのまま、OK にならない)。基準 (`criterion`) の無い荷重条件 (下流に値を渡すだけのもの。例: P1-step100 は R1 に段差の力を渡す) は、もともと判定が無いので、`ends_case` で終えても判定の抜けに数えず、実行の終わりの `stop_code` を `not_judged` にしない。`safe_side` とは一緒に付けない。手計算が値に届かなくても、力の道を手計算で確かめてあるときは、`ends_case` ではなく `tool_decides: true` と理由 `tool_decides_reason` を付ける。ツールはその版の道の確かめ (下の「手計算の役目」) を見て、通っていればツールの値で `verdict` を作る (`basis: "path_checked"`、`value_from: "analysis"`、`cause_id`。OK も NG もある)。道の確かめが無いか通っていなければ断る。`ends_case` とも `safe_side` とも一緒に付けない (判定は 1 つ: `safe_side` は予測の値、`tool_decides` は解析の値で出す)。`record_cause` のツールの入力にも同じ 2 つの欄がある

手計算の役目 (2026-11-05 の方針: 考えずに CAE を回さないための手計算): 手計算は、CAE を回す前に考えるためのもの。値まで届く手計算はこれまでどおり予測との比べで判定を出す。値まで届かない (式が無い、上から押さえるだけでゆるい、原因が「手計算では届かない」) 荷重条件は、手計算で力の道がつながっていることを確かめたうえで、ツールの値で判定を出す。道の確かめ: その版で、その荷重条件の力がかかる部品について (版の全部の部品についての予測でもよい。全部がつながれば、力のかかる部品もつながる)、手計算 (load_path の式、例 `load_path.free_parts`) が、部品から部材まで触れている所の道がつながる (つながらない部品 0) と予測して記録し、ツールの解析も支えのない荷重 (`unsupported_load`) 0 を出していること。予測は今までどおり解析の前 (HK1)、不一致の原因も今までどおり書く (HK3)。道がつながらない (ツールが `unsupported_load` を出した) ときは、その荷重条件は NG (`basis: "unsupported_load"`)

CAE の役目 (2026-11-05 の方針: 手計算が見ていない所を見つけるのも CAE の役目で、見たい所だけを決め打ちで見ない): ツールは、手計算が見ていた場所だけでなく、モデルの全体で基準を超える所を探す。
- モデル: 力を受ける物は、形としてモデルに入れる (金具・溶接・ボルトやピンの穴・腕の付け根など)。力を入れる点で応力が尖る所は、要素を外して避けるのではなく、金具やボルトの穴の形で力を広げて入れて避ける。どうしても外す所が残るときは、その範囲と理由を解析の記録の `assumptions` に書き、画面に出す (タイルは、外した所を見ていないことを言う)。
- 判定の量: 荷重条件の基準の量は、モデルの全体 (外した所を除く) の最大で読む。溶接は全部 (管どうしの継ぎ手・ガセットの脚・金具の根元・斜めの継ぎ手) にホットスポットを読む線を引き、その最大で読む。溶接の無い所は要素の応力で読む。ただし、入りの角のように細かくするほど上がり続ける尖りの点は、`hot_spots` に note (尖り) を付けて出すが、NG の元にはしない (メッシュで決まる値なので)。溶接の線がまだ引けない継ぎ手は「判定していない継ぎ手」として、`assumptions` と画面に出す。
- 見つけた所: ツールは、解析の結果に、利用率の高い順の場所の一覧 (`hot_spots`。各行に場所の id・量・値・許し・手計算がその場所を予測していたか。任意で、値を読んだ点の設計の座標 `at_mm`: 板なら溶接の線の上の点、梁なら部材の上の点) を出す。利用率は、基準の向きで、1 を超えるとちょうど NG になる比: 「以下」「未満」(<=、<) の基準は 値 / 許し、「以上」「より大きい」(>=、>) の基準は 許し / 値。許しか値が 0 以下などで比が決まらないときや、比が 0〜1000 の外に出るときは、NG なら 1000、OK なら 0 と書く。手計算が予測していない場所で、荷重条件の基準の量が基準を外れたら (`predicted: false`、`utilisation` > 1)、その荷重条件は NG。この判定は、ツールの解析 (`analyze`) のときにコードが作る (`basis: "hot_spot"`、`analysis_id`、`where`、`value_from: "analysis"`、値と許しはその行、`pass: false`)。比べが無いので、道の確かめの有無にかかわらず NG で、道の確かめが無ければ、タイルと原因を求める文で道の確かめ (`load_path.free_parts`) も求める。HK4 は、この荷重条件を「判定済みの NG」として扱う (設計役へ渡す)。CAE 役はその場所の原因を `record_cause` で書く。原因は比べの代わりにこの判定を指す (`verdict_id`、`where`。`comparison_id` か `verdict_id` のどちらかが要る)。直した版で、同じ場所が基準の内になれば NG は消える

5. 判定 `judgement` — 分野 (7 分野 + 組み立て) ごとに判定をまとめたもの。A6 のタイル

判定の余裕は `margin_pct` (基準に対する %、OK なら正) と `margin` (基準との差を単位つきで、OK なら正)。基準が 0 のときは `margin_pct` を null にして `margin` で言う。ツールが設計にも荷重条件にも無い値を仮定して使ったときは、analysis の `assumptions` に書く。

判定 (`verdict`) はツールの中でだけ作る: 予想通りの比べから (`compare`、`basis: "match"`、値は解析) か、説明された安全側の不一致から (`record_cause`、上の 4)。`basis` が無いものは `match`。

分野が OK になる条件 (2026-11-03、実験部の 2 本目の実行で、シェルが 472 MPa を出したのに強度が OK で終わったことから): その版の、その分野の荷重条件のどれについても、(a) OK の `verdict` がある (同じ荷重条件に、量か場所 (`<量>.<場所の id>`) の違う判定がいくつかあるときは、どれも OK であること。同じ量・同じ場所のあとの判定は、前の判定を置き換える)、(b) その荷重条件の判定に使う量の、いちばん新しい比べが、予想通りか、安全側と説明された不一致 (`safe_side` の原因と `explained_safe_side` の判定がある) か、手計算が届かないと説明され道の確かめが通った不一致 (`tool_decides` の原因と `path_checked` の判定がある) である。それより前の不一致は、あとの比べ (同じ荷重条件・同じ量) で置き換わったものとして、原因が解けていなくても止めない (原因の記録は書き換えず、タイルの上だけで片づける)。いちばん新しい比べが不一致で、原因が無いか、原因が解けていなくて安全側でもないときだけ止める、(c) 判定に使う量で基準を超えた解析の結果が無い。どれかが欠ければ、その分野は OK にならない (NG か、まだ判定の無い扱い)。あとの解析が基準を超えたら、前の安全側の `verdict` (`explained_safe_side`) は効かなくなる。HK4 は (b)・(c) の残る分野を「まだ判定の無い分野」と同じに扱う (設計役へ渡すか、原因を書いて解くまで終われない)。ただし (c) の荷重条件を予測する手計算 (K0) の式が無いときは、HK4 はその荷重条件で止めを断らない (予測も比べもできず、役には解けないため)。その荷重条件は、タイルと report に「基準を超えた結果があるが、手計算の式が無いので判定していない」と残る (OK にはならない)。
(d) (2026-11-05、N13 の本番 094333 で、v3 のシェルの 643 MPa の NG のあと、v4 では R1-step100 が梁の段差のモデルの OK (ver-0042) で OK の判定になり、v4 のシェルは失敗していたことから。強度のタイルは R7-hand が判定なしで残ったので、どの版でも OK にはなっていない) この実行のそれより前の版で、その荷重条件の判定に使う量が基準を超えた解析があるとき (見るのは、それぞれの版の、ツールごとのいちばん新しい解析。同じ版で同じツールを回し直して基準の内に入ったら、前の基準超えは置き換わる)、そのうちいちばん重い段のツールと同じか重い段のツールの解析が、この版でその量を出していること。その解析が失敗したか回っていなければ、その荷重条件は OK にならない (まだ判定の無い扱い。HK4 は設計役へ渡すか、原因を書いて解くまで終われない)。軽い段のツールの OK は、重い段のツールが出した基準超えを消さない。

判定に使う量: 荷重条件の基準の量 (`criterion.quantity`) が `max_stress` のとき、板 (シェル) のツール `frame_shell` で判定に使うのは `max_hot_spot_stress` (溶接の止端の構造応力)。要素の最大 `max_stress` は鋭い角で細かくするほど上がる値なので、記録には出すが判定には使わない。`comparison.quantity` には、実際に比べた結果の量の名前を書く (呼ぶときの `result_quantity` と同じ)。

代わりに比べる量: 基準の任意の欄 `stand_in_quantity` (例: 疲労の損傷 `fatigue_damage` に対する応力の幅 `stress_range`)。判定に使う量の予測と解析の結果が、どちらも基準の限度の 1e-6 倍より小さいとき (損傷がほぼ 0 で、比の差に意味が無いとき) は、上の (b) を `stand_in_quantity` のいちばん新しい比べで見る。判定 (a)・(c) は判定に使う量のまま。比べはどちらの量も記録に残す。欄は荷重条件を作るコードが書く

場所ごとの予測: 予測は解析の前なので、手計算は判定の量がどこで最大になるかを前もって知らない。そこで、判定の量を場所ごとにも予測してよい (`<量>.<場所の id>`、例 `max_hot_spot_stress.LE`。部材ごとの結果と同じ名前の形)。`compare` は判定の量を比べるとき、解析の結果の `where` に当たる場所ごとの予測があればそれと比べ、`comparison.where` にその場所を書く。無ければ全体の予測と比べる。手計算が自分で選んだ最大の所どうしの比べは記録に残してよいが、OK の決まりの (b) には使わない。手計算がその場所を表せないときは、場所ごとの予測にその理由を書く

荷重の通り道が無いとき: 荷重を掛ける点がどこにもつながっていない (浮いた部品)、または押される相手が無い (乗員を外へ押す力を受ける物が無い) ために判定の量が出せないとき、ツールは `status` を `ok` にし、結果に `unsupported_load` (単位 `1`、通り道の無い荷重の点の数、`where` にその id) を出す。judge はそれを、その荷重条件の NG の `verdict` にする (`quantity` `unsupported_load`、`op` `<=`、`limit` 0、`basis` は `unsupported_load` (意味は「荷重の通り道が無い」)、予測も比べも無いので `comparison_id` の代わりに `analysis_id`、`value_from` `analysis`、`margin_pct` は null)。これは設計の側の欠けなので、まだ判定の無い荷重条件としては扱わない (HK4 は待たない)。浮いた部品がある版の応力の結果は、その部品の質量が載っていないことを `assumptions` に書く

hooks が守らせること (`hiraku/contract/protocols.py` の `HOOK_RULES`。止めたら `hook.denied` の event に規則の ID を入れる):

| ID | 中身 |
|---|---|
| HK1 | 同じ版・同じ荷重条件の予測が無い `analyze` は止める |
| HK2 | 確かめ (答えの分かっている例) に通っていないツールの `analyze` は止める |
| HK3 | 原因の無い不一致がある分野では `analyze` と `compare` を止める |
| HK4 | 選んだ分野にまだ判定の無いものがある間は終われない (上限に達したときだけ止まる。止めた回数に上限を付ける)。直す回数を使い切り、待ちの分野が低い段の NG (HK5) の後ろにあるだけなら、終わりを通す。K0 の式が無い荷重条件の、基準を超えた結果では止めを断らない (判定なしとして残す) |
| HK5 | 軽い計算から確かめる。ツールは重さの段 `stage` を持つ (0 レイアウトと規則: 幾何・干渉・乗員の体・ハンドルの切れ・取付点の隙間・系のつながり / 1 軽い: 手計算の大きさのモデル・梁・熱の集中のモデル / 2 車の動き: 時間の積分 / 3 重い: 板とソリッドの有限要素・収束・路面の疲労・時刻ごとの場)。荷重条件の段は、それを読むツールのいちばん軽い段と、`feeds` でそれに流れ込む荷重条件の段の、重い方 (上流の結果が無いと判定できないため)。その版で、低い段のツールで確かめるほかの荷重条件に NG か未判定が残る間、または解析しようとしている荷重条件そのものに低い段のツールの NG の判定がある間は、高い段のツールの `analyze` を止める (同じ荷重条件の低い段がまだ判定待ちなだけなら止めない。限界の外で落ちるための荷重条件 (`-beyond`) は数えない。判定できない荷重条件 (予測する K0 の式が無いもの、不一致の原因が `ends_case: true` で記録されたもの。台本の計画では `expected_mismatch` の `end`) も数えない。それはタイルに判定なしとして残り、OK にはならない)。NG が出たら、重い計算に進む前に設計役が直す (`revise_design`)。同じ段のツールは並べて回してよい。レイアウトが変われば強度や車の動きの数が全部動くので、段 0 から決める。直す回数を使い切った版で低い段に NG が残るときは、高い段は回さずに終わる (stop_code `round_limit`。待ちの分野は「低い段の NG が残るので判定していない」と stop_reason と報告に書く) |
| HK6 | モジュールの持ち場 (モジュールに分けた設計だけ)。モジュールの設計役の `revise_design` は、自分のモジュールの変数と選択だけを変える提案 (`design.change_proposed`) で、版は作らない。レイアウトの変数・空間 (envelope)・取付点 (interfaces) を変えるのはパッケージ役だけで、次の版を作るのはパッケージ役の `build_version` だけ (その回の提案を全部まとめて 1 つの版にする)。NG は、その荷重条件の `modules` の設計役に行き、設計役どうしは並べて動く。同じ変数を 2 つの提案が変えるとき、部品がモジュールの空間からはみ出すときは、`build_version` を断る |

確かめの例の日本語 (2026-11-05、画面で英語の例が読めなかったため): `tool_check` は任意で、例の日本語 `case_ja` と、答えの出どころの日本語 `source_ja` を持つ。英語の `case`・`source` と同じコードで組み、文の中の数も同じ値から出す。画面は日本語があればそれを出し、英語はくわしくの中に置く

確かめの使い回し (HK2 の補い): 棚のツールの確かめは、hiraku のソース (`.py` を全部) のハッシュが同じ間だけ、前の実行の結果を使ってよい。そのときの `tool_check` に `code_hash` と `cached_from` (使い回した実行の run_id) を書く。コードが 1 行でも変われば確かめをやり直す。実行の中で Claude が作ったツールの確かめは使い回さない

## 解析部と設計部のあいだ (Python)

`hiraku/contract/protocols.py`:

- 入口は `hiraku/solvers/__init__.py` の 2 つだけ: `formula_registry()` (`FormulaRegistry` を返す) と `builtin_tools()` (`AnalysisTool` の list を返す)。`hiraku.solvers` を import しただけで何かが起きないようにする
- `FormulaRegistry` — `list_formulas()` と `evaluate(formula_id, inputs)`。K0 の式の台帳
- `AnalysisTool` — `spec()`・`check()`・`run(design, load_case, predictions, params)`。`run` は `analysis_id` の無い `analysis` を返し、ID は store が付ける。設計が悪くても例外にせず `status: "failed"` と、何が悪く何を変えればよいかを `error` に書く
- 上流の荷重条件の結果 (P1 の輪の力 → R1 など) は `params["upstream_analyses"] = {荷重条件の ID: 比べて予想通りだった analysis}` で `run` に渡る。上流の `analysis_id` を `inputs_from` に足すのは呼ぶ側 (設計部)
- 組み立ての順番と干渉は `analysis` の `assembly` (手順ごとの `assembly_step`) と `interferences` に入れる (`effects` に混ぜない)。手順には任意で、留め方 `fastenings` (何に・ボルト/溶接/載せる/ピン)、入れる道筋 `path_mm` (折れ線、最後が付いた位置)、手順の 1 文 `text` (コードで組む) を付ける。先に組んだ小さな組 (サスの角など) を付ける手順は、`part` に組の名前 (例 `corner:FL`) と `group_parts` (組の中の部品)。手と工具の邪魔は `tool_blocked_by` (入れられるが手が入りにくい)、`blocked_by` は入れられない理由。留め方ごとに任意で、手と工具が来る向き `tool_side` と、手と工具に要る場所とほかの物とのいちばん狭い隙間 `tool_gap_mm` (組み立てのツールが計算する。負は食い込みの深さ)。手と工具の通り道の決まりには出典がまだ無いので、`tool_gap_mm` は見せるだけで判定しない
- 時計で測った時間 (解析にかかった秒) は、実行のたびに変わる。書くのは analysis の `wall_time_s` (全体) と任意の `timings_s` (部分ごと: 名前 → 秒。組み立て・分解・収束の段ごと など) だけで、`results`・`effects`・`assumptions`・文 (報告の `next` の理由、`agent.said` など) には入れない。同じ台本の 2 回を比べる試験は、この 2 つの欄だけを伏せる。モデルの大きさから計算した見込みの時間は時計ではないので、文に書いてよい
- 板 (シェル) のメッシュ: 版ごとに、コードが設計からフレームのメッシュ (管の壁と板の中面) を作り、`mesh` の記録 (`v<NNN>/meshes/mesh-NNNN.json`、event `mesh.created`) にする。節は `nodes_mm`、要素は節の番号 3 つか 4 つ (`elements`)、要素ごとにどの部材か (`element_member`)、部材の厚さ (`member_thickness_mm`)。解析の結果は field に `mesh_id` と、要素ごとの値 `per_element` か節ごとの値 `per_mesh_node` (変位とモードの形は vec3) を入れ、シェルの応力は `surface` (`top`・`mid`・`bottom`・`max`) を付ける。梁 (K1) の `per_member` と同じ analysis に並べてよい
- ソリッドと混ざったメッシュ (共通の核 `hiraku/solvers/fe/`): mesh の `element_type` は `shell`・`solid`・`mixed`。`member_thickness_mm` は shell のときだけ必須。要素は 3〜20 節点で、mixed のときは要素ごとの `element_kind` (`quad4`・`tri3`・`hex8`・`hex20`) が必須。節の並びは、quad4・tri3 が外から見て反時計回り、hex8 が 0〜3 を +ζ から見て反時計回り (下の面) で 4〜7 をその上、hex20 は角の 8 つのあとに辺の中の節を 0-1・1-2・2-3・3-0・4-5・5-6・6-7・7-4・0-4・1-5・2-6・3-7 の順。要素でない結びは `connections` (`id`・`kind`: `rbe2` (主の節 `master` から `nodes` へ剛、`components` は "123456" の形)・`rbe3` (基準の節 `master` が `nodes` の重み `weights` の平均で動く。固くしない)・`spring` (2 節のあいだの 6 自由度のばねとブッシュ、`stiffness` は N/mm と N*mm/rad、軸は `frame`)・`shell_solid` (シェルの縁の節と `solid_nodes`、`thickness_mm`)・`tie` (節を共有しない面を `faces` で貼る))
- 時間とともに変わる field: field の任意の欄 `frames` (`t_s` と、時刻ごとの `per_element` (mesh_id と一緒に) か `per_member`、時刻ごとの最大 `max`、荷重の出どころの `source_analysis_id`)。例: K2 の段差の動きの各瞬間の取付点の力で、フレームを解き直した応力。全部の歩を残さず、選んだ時刻だけ (数十) を残す。画面は再生の時刻にいちばん近い時刻の値で塗る
- pre 処理の中身: analysis の任意の欄 `setup` に、その荷重条件でモデルに入れたものを書く。`constraints` (メッシュの節の番号と止めた自由度)、`loads` (力の点・向き・大きさ、分けたときは `spread_to` に節と重み)、`inertia_relief`、`checks` (解く前の点検の値: 釣り合いの残り・モデルの質量・設計の質量など、quantity。名前は `equilibrium_residual`・`load_balance_residual` (単位 1)、`model_mass` (kg)、`mass_difference` (%)、`free_rigid_body_modes` (1) にそろえる)。接続要素 (剛な結び・ばね・tie) は mesh の側に持つ (形は共通の核と一緒に決める)
- メッシュの品質 (pre 処理の点検): mesh の任意の欄 `quality` に、解く前にコードが測った値を入れる。`metrics` (縦横比・反り・ゆがみ・角度・ヤコビアン比など。名前 → `worst`・`p95`・`limit`・`over_limit`・`worst_element`)、任意で `per_element` (画面で色に塗る)、`topology` (自由縁・重なった節・つながっていない組・裏返った法線・負の体積)、`verdict` (`ok`・`warn`・`fail`。fail ならソルバーは解かない)。限度はソルバーの目安で、出典が無ければ仮定と書く
- メッシュの大きさの確かめ (post 処理): 誤差の目安は field の `kind` `error_estimate` (要素ごと `per_element`、単位 `%`。例: Zienkiewicz–Zhu。なめらかに直した応力と要素の応力の差のエネルギーの大きさを、直した応力の大きさで割る)。画面で色に塗り、細かくすべき所を見せる。収束は analysis の任意の欄 `mesh_convergence`: 判定に使う値 (`quantity`・`unit`) を同じ設計の細かいメッシュで解き直した `levels` (粗い順、`element_size_mm`・`value`、任意で `elements`、記録したメッシュなら `mesh_id`。細かい方のメッシュは記録しない)、最も細かい 2 つの変化 `change_pct` と限度 `limit_pct`、3 つあれば Richardson の `observed_order`・`extrapolated`・`error_pct`、`verdict` (`converged`・`not_converged`・`singular`。singular は小さくするほど値が上がり続ける所 (鋭い角・点の荷重) で、そのままでは判定に使えない)
- 1 つの版に mesh が 2 つ以上あってよい (例: 版の板のメッシュ `frame_mesh` と、管ごとに切って tie で結ぶ `frame_tubes` のメッシュ)。mesh の任意の欄 `mesher` に作ったものの名前を書き、field は自分の載る mesh を `mesh_id` で名指す。ツールが自分でメッシュを作るときも、`mesh.created` を `analysis.started` の前に出す
- メッシュを作るのは解析部の `hiraku.solvers.frame_mesh(design)` (入口は任意の 1 つ、`OPTIONAL_SOLVER_ENTRY_POINTS`。mesh_id の無い mesh を返す)。記録するのは `analyze`: ツールが `needs_mesh` (無ければ False) なら、その版の mesh が無いときだけ 1 回作って `mesh.created` を出し (mesh は解析の入力なので、その解析の `analysis.started` より前に出す)、ツールの `run` に `params["mesh"]` で渡す。ツールは同じ設計から作った mesh かを確かめ、field の `mesh_id` に書く。渡されないとき (確かめ・単体の試験) は mesh の field を出さず results だけ返す
  - 自分のメッシュを作るツール (例: `frame_tubes`): ツールは `needs_mesh = True` と `make_mesh(design)` (mesh_id の無い mesh を返す。`mesher` はそのツールの名前) を持つ。`analyze` は frame_mesh の代わりにそれを呼び、版ごと・mesher ごとに 1 回だけ作って `mesh.created` を `analysis.started` より前に出し、`params["mesh"]` で渡す。同じ版のほかの荷重条件では同じ mesh を使い回す (作り直さない)。`run` の戻りで mesh を返す形 (`out["mesh"]`) は使わない。mesh の field を出さず部材ごとの値だけを返すツールは `make_mesh` を持たなくてよい
- サスの取付点は、設計の `support.suspension.hardpoints_mm` (取付点の名前 → vec3、mm) に、設計を作るコードが書く。K2 の関数・梁とシェル・画面は、ここから読む (出どころを 1 つにする)。取付点ごとの力は、analysis の `vectors` に `kind: "joint"` (`at` は `<支持の id>.<取付点の名前>`、`origin_mm` は取付点、`vector_N` は車体が受ける力) で出す。サスのタワーはフレームの部材、金具の板は部品 (kind `bracket`) で持つ
- 接地する所 (`support`) は任意で `tire` (`coefficients`: `data/tyres/` のタイヤの係数のファイル、`relaxation_length_mm`) を持ち、サスペンションは任意で `bushings` (腕の内側のブッシュの半径方向・軸方向とタイロッドの剛性 N/mm、K&C の C) を持つ。無い値は解く側が仮定して analysis の `assumptions` に書く
- `series` は時間 (`t_s`) か、ほかの量 (`x`: `{quantity, unit, values}`。K&C の台のストロークや横力) のどちらか 1 つを横軸に持つ
- 部品ごとの series: 1 つの部品の値 (電池ごとの温度・部品ごとの電力・損失 W など) の series は、任意の欄 `part_id` に設計の部品の id を書く。`channel` は今までどおり (`temp.battery`・`loss.motor` など)。同じ種類の部品が 2 つある設計では、画面は `part_id` でその部品に色を付ける。`part_id` の無い series は、部品に結び付けない
- 値と field を結ぶ: result の任意の欄 `field_index` は、その値を読んだ field の、同じ analysis の `fields` の中の番号 (0 から)。例: `first_frame_mode` のモード形の field。画面は判定の値からその field を開く。番号が `fields` の外なら、画面は結び付けない
- ツールの名前は棚の中で 1 つに 1 つ。段差越えは `step_climb` (MVP の形: 手計算の釣り合いの輪の力 → K1) と `step_climb_dynamic` (K2 で段差を走らせる) の 2 つ
- ツールは任意の欄 `applies_to` に、使える設計の型 (`hiraku.designgen` の `ARCHETYPES` の名前。例: `frame_tubes` は `["space_frame_utv"]`) を並べてよい。無ければどの設計にも使える。設計の型は、その設計を作った generator の型。`analyze` は、版の設計の型が `applies_to` に無いツールを理由の文つきで断り、型の分からない設計 (手で作った設計) にも `applies_to` のあるツールは使わない。`list_tools` はこの欄も見せる
- 材質カード (`material_card`、`data/materials/cards/<id>.json`、1 材質 1 ファイル): 1 つの材質の特性を、値ごとの出どころつきで持つ。`properties` の名前は決まった一覧で、単位は名前に付いている。値ごとに `value`・`kind` (`standard_minimum` 規格の最小・`typical` 代表値・`computed` ほかの値からの計算・`assumption` 仮定)・`source` (文書と版と表やページ。二次の出所はそう書く。仮定なら理由)・`checked` (ここに書いた出所で値を読んだときだけ true、そのときは `checked_on` に日付)。分からない特性は書かない。ソルバーが読むのは今までどおり `design.materials` で、カードは値の出どころを示す。同じ `id` の材質が `data/materials/materials.json` にあるときは、値が同じであること (試験で確かめる)。データベースは使わない (ファイルで足りる数で、Python だけで読める)
- 部品の任意の欄 `material` (部材と同じく `design.materials` の鍵) と `finish` (`painted`・`powder_coat`・`bare`・`anodized`・`galvanized`・`molded`)。設計を作るコードが書き、画面の実物の見た目はここから材質を決める (無ければ部品の種類から)。質量と強さに効く値は `design.materials` の側に出典つきで
- 金具の継ぎ手 (2026-11-05、サスペンションの取付点を判定していなかったため): 枠に着く金具の部品 (サスペンションの腕の付け根・ダンパーの上と下) は、任意の欄 `joint` に、設計役が直せる大きさを持つ: `bolt_d_mm` (ボルトかピンの径)、`bolts` (力を受ける本数)、`plate_t_mm` (金具の板厚)、`weld_leg_mm` (金具と枠のすみ肉溶接の脚長。のど厚は 0.7 倍)。金具の質量は、この大きさから設計を作るコードが計算する。荷重条件 R9「サスペンション取付」は、K2 の取付点ごとの力の最大で、これらを確かめる。判定の量の名前は `bolt_shear_utilisation`・`bolt_bearing_utilisation`・`plate_bending_utilisation`・`weld_utilisation` (どれも単位 1、基準は ≤ 1)。判定は「手計算の役目」のとおり
- 規則に渡す大きさ (2026-11-05): 部品は任意の欄 `declared` に、ナレッジの規則 (`rule_check`) が見る大きさを持つ。`{名前: {value, unit, basis}}` の形で、名前は受付部の語彙 (`hiraku.intake.facts.FACTS` の scope declared: 曲げの半径・穴の縁・穴の径・ボルトの径・ねじのかみ合い・溶接の長さ・のど厚・隙間・公差の積み上げ・隙間穴・位置の公差・閉じる隙間。2026-11-06 に、ハンドルの継ぎ手の数・大きい方の折れ角・2 つの角の差・位相のずれ、頭の後ろの高さ・幅・後ろへの距離・隙間、荷のベルトの本数・上からのベルトの本数・張る力・角度・前に止めがあるか、を足した。同じ日に、配線の部品 (kind `electrical`) の流れる電流・許容電流・束ねた本数・ヒューズの定格・被覆の耐熱・置く所の最高温度・耐電圧・曲げ半径・外径・支持の間隔・貫通部の隙間・操作系との隙間、電池の部品のヒューズまでの長さ、を足した)。単位は名前ごとに 1 つで、mm・deg・N・N*m・1・%・A・V・degC のどれか (語彙の表と同じ。規則が照らす)。名前は契約が先に足してよく、語彙は後から同じ名前を足す (語彙にあって契約に無い名前は作らない)。部品が受ける力 (背もたれのモーメント・頭の後ろの力など) は解析の結果なので `declared` に書かず、荷重条件で判定する。買う部品の強さは `rating` に書き、ベルトの定格の最小や和は、規則がベルトの部品の `rating` から出す (同じ値を 2 か所に書かない)。`basis` は値の出どころで、設計の変数 (例 `variable rollbar_t`)、規格 (版と表。確かめていなければ `not checked`)、仮定 (`assumption`) のどれかを書く。書くのは設計を作るコード、読むのは受付部の規則。`joint` のある部品は、ボルトの径とのど厚を `joint` から出す (同じ値を 2 か所に書かない) ので、`declared` には書かない。規則の記録 `rule_check` は任意の欄 `checked` に、評価できた部品ごと (OK も NG も) の使った値 `values` と、そのうち出どころを確かめていない名前 `unchecked` (`basis` が `not checked` か `assumption`) を残す
- 買う部品の定格 (2026-11-06、荷の押さえ R10): ベルトや荷台のフックのように、使うときに受けてよい力が決まっている部品は、任意の欄 `rating` に `{working_load_N, basis}` (定格の力、N。出典は規格やメーカーの表の版と表、確かめていなければ `not checked`、または `assumption`) を持つ。書くのは設計を作るコード、読むのは R10 の判定 (部品が受ける力 ≤ 定格)。部品の `kind` は決まった一覧ではないので、ベルトは `strap`、フックは `lashing_point` のように書いてよい
- 乗員と荷は設計の部品 (`kind` が `occupant` / `cargo`) として持つ。どれを載せるかは荷重条件の `params["loading"]`: `empty`・`driver`・`two`・`two_cargo` (カタログの Q30)。無ければ、要件のファイルの `default_loading` (製品ごとの既定。1 人で使うベッドなら `driver`)、それも無ければ `two_cargo`。荷重条件を作るコードが、要件のファイルの既定を `params["loading"]` に入れる。`driver` は design.parts の並びで最初の occupant、`two` は最初の 2 つ、cargo の部品は `two_cargo` のときだけ載る。occupant の部品が 1 つも無い設計 (座席の無い製品) は誰も載せず、あるのに足りない設計 (1 人分しか無いのに `two`) はその荷重条件に合わない (ValueError)。1 人を体の部分 (胴・もも・すね など) に分けてよい: id の最初の `.` より前が同じ occupant の部品 (`driver.torso`・`driver.thigh_l`) は 1 人 (`person_of`) で、まとめて載るか降りる。この決まりは `hiraku/contract/loading.py` の `parts_on_board`・`on_board`・`loading_of` だけに書き、どの部署もそれを呼ぶ (自分で書き直さない)
- 記録の見せ方の約束: vectors の `origin_mm` は力が作用する点。モードの形 (field の kind が `mode_shape`) の `per_node` は、いちばん大きい所を 1 とした相対の変位 (vec3)。試験の場面 (段差・凸凹の路面・坂・傾斜台・パイロン・壁) は analysis の `scene` にツールが書き、画面が手で置かない。`scene` の `position_mm` は箱の中心、`size_mm` は辺の長さ、`rpy_deg` は中心のまわりの回転 (パイロンは点、門は [0, 幅, 0] の線。地面に描く円 `circle` は、`position_mm` が中心で `size_mm` が [直径, 直径, 0]。例: 小回りの試験の、外側の前輪の円と車体のいちばん外の円)。形が変わる模型の動き (柔らかい体の衝突など) は、field の kind `node_motion` に、節の id の並び `nodes` (300 まで)、線で描く梁 `members` ([節, 節, 部材の id])、コマごとの `frames.t_s` と `frames.positions_mm` (節の並びどおりの位置、mm) と `frames.broken` (そのコマまでに折れた梁) を入れる。コマは 150 まで。等しい間隔のコマに、最初と最後・減速が最大・初めて降伏・初めて折れたコマを足して間引く。減速の波形は series の `deceleration` (A と同じ名前)
- 実行の終わり (`run.finished` の data): `status` と、画面が言い換えるための `stop_code` (`done`・`not_judged`・`round_limit`・`red_left`・`cost_limit`・`turn_limit`・`user_stop`・`error`。`not_judged` は、選んだ分野に判定の付いていないもの (基準のある荷重条件の判定の無いもの) を残して終わったとき。`red_left` は、直す回数が残っているのに NG の分野を残したまま、終わりを止める hooks の上限に達して終わったとき)。`stop_reason` は人向けの文。対応: `done` ⇔ status `done`、`error` ⇔ `failed`、ほか (`not_judged`・`round_limit`・`red_left`・`cost_limit`・`turn_limit`・`user_stop`) は `stopped`
- 役が手を止めたとき (`agent.stopped` の data): `agent_id` と、任意の `stop_code` (`done` 自分の仕事を終えた・`run_ended` 役がまだ動いている間に実行が終わった (実行の理由は `run.finished` の `stop_code`)・`user_stop` 止めるボタン・`error` 役がエラーで終わった) と `stop_reason` (人向けの文)。書くのは役を閉じるコード (`hiraku/agent/`)、画面はペットの欄で `stop_code` を言い換える。無ければ画面は理由を出さない
- 走って向かう記録: 要件のファイルの任意の欄 `drive` (試験 id の並び)。その試験に、記録だけの荷重条件を足す (id の終わりが `-drive`、判定なし、どこにも feeds しない)。P1 は step_climb_dynamic を `record_run = speed` で (段差を 1 倍で走って越える)、S3 は車線変更を速さ 1.25 倍で (タイヤの限界に近い)、S1 は rollover_tilt で傾斜台を限界の 1° 先まで (計算で横転する)。画面と動画で見せるため。実行を始めるとき、project の同じ名前の欄 `drive` に写す
- 早めに終わる運転: 要件のファイルの任意の欄 `stop_after` (`picks`)。`picks` は分野を選んでヒアリングを終えたら終わる運転 (分野選びだけの記録。設計は作らない)。HK4 はそのあとの分野を待たずに止めを通し、`run.finished` は status `done`・stop_code `done`、stop_reason に「分野選びまでの運転」と書く。実行を始めるとき、project の同じ名前の欄 `stop_after` に写す (hook のプロセスなど、実行を開き直すものもそこから読む)
- 聞き返すのは誰か: 要件のファイルの任意の欄 `hearing` (`code` か `claude`、無ければ `code`)。`code` は今までどおり、実行を始めるとき足りない項目をファイルと既定値で埋める。`claude` は埋めずに残し、まとめ役が `ask_user` (`wait_seconds` つき) で人に聞く (画面の A2 か、それに代わる答えのファイル)。実行を始めるとき project の同じ名前の欄に写す
- 規則の当たりを人に聞く: 人の判断が要る当たり (設計を直すか、この実行ではその規則を外すか) は、まとめ役が `ask_user` の `rule_hits` (rule_id・当たった部品・人に向けた文) で聞く。`hearing.asked` の `rule_questions` に出し、答えは `hearing.answered` の `rule_answers` (`revise_design` か `drop_rule`)。要件の質問 (`questions`) と同じ 1 回の聞き返しに入れてよい
- トレードオフを人に聞く: 1 つの設計の数を動かすと、1 つの結果は良くなり別の結果は悪くなる組は、人に優先を聞いてよい。組を探すのは MCP のツール `find_tradeoffs` (受付部)。感度の記録 (K5) から探し、数 (今の値・動かした後の値) はコードが記録から入れる。まとめ役は返った `tradeoff_id` を `ask_user` の `tradeoff_ids` に渡すだけで、数を書く道は無い。`hearing.asked` の `tradeoff_questions` (`tradeoff_question`: `a` は良くなる結果、`b` は悪くなる結果、それぞれ `name`・`unit`・`now`・`after`・`record_id`) に出し、答えは `hearing.answered` の `tradeoff_answers` (`favor_a`・`favor_b`・`keep_both`)。答えは設計役への指示に入り、実行の記録に残る
- 実行を外から止めるとき (画面の止めるボタン) は、実行のフォルダに `stop.request` (`layout.STOP_REQUEST_FILE`) を置く。実行の側はそれを見て、記録を整えて `stopped`・`stop_code` `user_stop` で終わる
- ツールが例外を外に出したときは、実行のフォルダの `tool.errors.log` (`layout.TOOL_ERRORS_FILE`) にトレースバックを 1 件ずつ足す (例外が無ければファイルも無い)。同じことを `agent.said` (system、`data.tool_error`) でも言い、ツールの答えはエラーの答えにする。記録 (record) ではないので、画面はこのファイルを読まない
- `events.jsonl` に書くのは実行のプロセスだけ (書き手が 1 つなので `seq` が抜けない)。画面のサーバーから走っている実行に伝えるもの (Claude に伝える文、ナレッジのファイルを落とした、など) は、実行のフォルダの `inbox/` (`layout.INBOX_DIR`) に 1 件 1 ファイルの JSON (`<番号>-<種類>.json`、中身は `{"kind": ..., "text": ..., "file": ...}`) で置く。落としたファイルそのものは `knowledge/<filename>` に置く。実行の側がそれを読み、`user.said` や `knowledge.imported` などの event を自分で出して、読んだファイルを消す。ヒアリングの答えは今までどおり `hearing.answer.json`
  - 置き方: 1 件 1 ファイルの `inbox/<n>-<kind>.json`。`n` は整数で、画面のサーバーは inbox にある一番大きい `n` + 1 を付け、実行は `n` の順に読む (読んだファイルは消すので、空になれば同じ `n` がまた使われる)。書きかけを読まないよう `<n>-<kind>.json.tmp` に書いてから名前を変え、実行は `.json` で終わるものだけを読む。中身は `{"kind", "text", "file"}` で、使わない欄は省き、`kind` はファイル名の `<kind>` と同じ
  - `message` (`text`): 実行は `user.said` (agent `user`) を出す。Claude の実行ではまとめ役に渡し (次のツールのあと・番を終えようとしたとき (HK4 の回数に数えない)・役が動いている間に番が終わっていれば新しい番)、台本の運転では渡さないと `agent.said` (system) で言う
  - `knowledge` (`file`、任意で `text`): ファイルは先に `knowledge/<file>` に置く (名前は画面のサーバーが整え、`knowledge/` の直下だけ)。`text` があればまず `user.said`。実行がコードで `import_knowledge` (path) を呼んで `knowledge.imported` を出し、式が要る文があれば Claude の実行ではまとめ役に渡して、同じ path に式を付けてもう一度呼ぶ (`doc_id` は同じ)
  - 読めないもの (JSON でない・知らない kind・欄が無い・ファイルが無い) は `agent.said` (system) で理由を言って消す。読むのは `stop.request` と同じ所 (Claude の実行は 1 秒ごと、台本の運転は手順の切れ目と `analyze` の前)。実行が終わったあとに置かれたものは読まれずに残る
- 設計役が最初の版を作る実行 (要件に `design_file` が無い) では、v1 ができたあと `project.updated` (data は project 全体) で荷重条件・選んだ分野・役を出し直す
- 報告は `report` の記録と `report.written` の event。数はコードが記録から入れ、Claude は `summary` と `next` だけを書く (文の中の数が記録と合わなければツールが断る)
- 実行の中で Claude が作ったツール (N13) は、実行のフォルダの `tools/` に試みごとのコード (`<name>-<n>.py`) と確かめの記録 (`.check.json`) を残す。試みは通らなくても `tool.checked` (usable false) で記録に出す。ツールは別のプロセスで、使える import を絞り、空のフォルダ・API キー無し・時間の上限つきで走る。悪意のあるコードを完全に閉じ込める仕組みではなく、間違いへの守り
- 見本として残す実行は `showcase/<run_id>/` (`layout.SHOWCASE_DIR`。統括部の持ち場。runs/ はコミットしない)。画面は URL に `?run=` が無いとき、ここのいちばん新しい実行を再生する

MCP のツール (サーバーの名前は `hiraku`、ツールの名前は `mcp__hiraku__<名前>`): MVP は `get_context`・`list_formulas`・`list_tools`・`record_prediction`・`analyze`・`compare`・`record_cause`。設計役だけが `revise_design` (変数の変更 `changes`、generator の選択 `choices` (例 `part_mounts: brackets`、選べる値は `get_context` の `design_choices`)、少なくとも一方と理由。`hiraku.designgen` が次の版を組む)。入力の形は schema の `tool_input.*`。結果は JSON の文字列で、`record_prediction`・`analyze` は記録そのもの、`compare` と `record_cause` は記録と、作ったときは `verdict`、`revise_design` は新しい版のまとめ。

## 流れの解析 (2026-11-06)

`cfd_aero` (実行の中で使うツール `RUN_TOOLS` にあり、役では空力の CAE 役だけが使う。`analyze` を通らないので HK1〜HK5 の外で、値は判定の根拠にしない) は、設計の v の流れを GPU で解き、`<実行のフォルダ>/cfd/v<N>/` に各ケースの記録と `aero_record.json` を書く。GPU が無ければ回さないと答え、CPU では回さない。GPU のメモリが足りなければ待つように答える。`replay` は保存した記録を GPU なしで読む。解析の記録の `physically_validated` が false の値は、実験と比べて確かめていない (`assumptions` にも 確かめていない と書く)。

## Claude が書く設計 (2026-11-06)

作るものを型で限定しない。設計は 2 つの道から作れる。

- 型から作る: `propose_design` と `revise_design`。型 (`design.archetype`) があれば、座標はキットが計算する。
- Claude が書く: `write_design` (設計そのもの。2 回目からは次の版) と `edit_design` (部材の追加など)。型は必須ではなく、下書きに使ったら `based_on` に書く。ツールが `written_by: claude` を付け、`revise_design` はこの設計を断る (変数から組み直すと書いた所が消えるため)。`check_design` が、参照・つながり・桁・干渉・質量をコードで確かめ、通らない設計は版にしない。

どちらの道でも、数値はツールが計算する。解析の前に手計算で予測する決まりも同じ。カタログに builder が無い試験の荷重条件は `write_load_case` で書く。限度はモデルが数を書かず、`limit_from` の式からコードが計算する。

型の判定は `design.archetype` を先に見る。人の部品が 1 つも無い設計は人を載せない (`hiraku/contract/loading.py` の決まり。catalog もこれに合わせる)。型の前提があるツール (ラダーの部材名、ベッドなど) は、当てはまらない設計を「当てはまらない」と言って断り、黙って外したり誤った値を出したりしない。

コードが選んだ分野と試験を Claude が見直し、必要なら `add_tests` で足す。足すだけで外せず、理由を記録する (`domains.picked` の `added_tests`、足さないときは `no_addition_reason`)。荷重条件の作り方が無い試験は `write_load_case` かツールを作る道へ、値が足りなければ `ask_user` へ。

## モジュールに分けた設計 (2026-11-04 の方針)

設計役は 構造・機構・電気 の 3 つ (`designer.structure`・`designer.mechanism`・`designer.electric`。`contract.MODULE_DESIGNERS`、日本語の名前は `DESIGNER_ROLES_JA`、2026-11-06 から)。何を作るかによらない分け方で、モジュールは設計の型 (designgen) が決める内部の分け方。各モジュールの `owner` が 3 つのどれか (`package` はパッケージ役)。車の型では、枠・車体・乗員まわりが構造、足まわり・操作系が機構、動力が電気。モジュールに分けない型は設計役 `designer` 1 つのまま。モジュールごとの古い役 (`designer.frame` など、`LEGACY_MODULE_DESIGNERS`) は、前に記録した実行を読むためだけに残す。`revise_design` は、`design.modules` で自分が `owner` のモジュールの中だけを変える (HK6)。

設計を 1 人の設計役が一度に直すのではなく、モジュールに分けて、モジュールごとの設計役が並べて直し、パッケージ役が空間を配ってまとめる。実際の車の開発と同じ分け方。v5 で、強度のために足したガセットが電池に 34.4 mm めり込んだのに誰も気づかなかったことから。

- モジュール (`module_id`): `package` レイアウト (軸距・トレッド・レールの間隔と高さ・各モジュールの空間。部品は持たない、持ち主はパッケージ役)、`frame` 枠 (レール・横の部材・塔・柱・ガセット・枠の金具)、`suspension` 足まわり (車輪・ナックル・腕・ダンパーと、その枠への取付)、`steering` 操作系 (ハンドル・コラム・シャフト・ラック・タイロッド・ペダル・ペダルの柱・マスターシリンダー)、`occupant` 乗員まわり (座席・ベルト・横の守り・扉か開口・手がかり・ロールバー。人の置き場もここ)、`powertrain` 動力 (モーター・減速機・ドライブシャフト・電池・制御器・補機・スイッチ・配線)、`body` 車体 (床・荷台・隔壁・前の囲い・フェンダー・荷)
- 設計のデータ: 部品と部材に `module`。設計に `modules` (モジュールごとの持ち主 `owner`、空間 `envelope` (箱の並び)、取付点 `interfaces` (どの 2 つのモジュールが、どの節か点で、何で (`via`) 付くか)、変えてよい変数 `variables` と選択 `choices`)。節に接合の種類 `joint` (`weld`・`bolt`・`pin`。無ければ書いていない。ladder_4w の枠の節は溶接)。モジュールに分けるのは設計の型 (generator) で、分けない型 (介護ベッドなど) は今までどおり 1 人の設計役
- 役: `designer.<module>` (枠・足まわり・操作系・乗員まわり・動力・車体の設計役) と `packager` (パッケージ役)。ペットは 1 役に 1 匹
- 1 回の直し (round): (1) まとめ役が、NG の荷重条件の `modules` から、どのモジュールの設計役に渡すかを決める (レイアウトでしか直らない NG はパッケージ役)。(2) 設計役は並べて動き、`revise_design` に `module` を付けて提案する (`design.change_proposed`、data は `module_change`)。自分のモジュールの変数と選択だけを変えられる。(3) パッケージ役が `build_version` で、その回の提案を全部まとめて次の版を作る (`design.created` の `module_changes` に提案を並べる)。パッケージ役は、必要ならレイアウトの変数・空間・取付点も変える。(4) 次の版で、まず段 0 の確かめ (干渉: 止まっているときと動いたときの重なり、部品がモジュールの空間に収まるか、組み立てで入らない部品) をする。モジュールどうしのぶつかりは、パッケージ役に行く
- 出し直した提案: 同じ回で、同じモジュール (同じ設計役、パッケージ役なら package) がもう一度 `revise_design` で提案したら、そのモジュールの前の提案は置き換わり、`build_version` が使うのは後の提案だけ。`build_version` に断られた設計役は、断りの理由を直して出し直す (取り下げのツールは無い)。ほかのモジュールの提案はそのまま残る。HK6 で、1 つの変数を変えられるのは 1 つのモジュールだけなので、「同じ変数を 2 つの提案が変える」断りは、置き換えのあとには、パッケージ役の変更とモジュールの提案が同じレイアウトの変数を変えたときだけ起きる
- 荷重条件に `modules` (その NG を直せるモジュール。builder が付ける)
- 荷重条件は版ごとに作り直す (基準が版の設計を読むものがあるため。例: 組む部品の数)。2 版目からは `load_cases.created` (data は `version_load_cases`: `design_version` と `load_cases`) を出し、`v00N/load_cases.json` (`layout.VERSION_LOAD_CASES_FILE`) に書く。`project.load_cases` はv1 のまま
- 台本の運転: 計画の `revise` の 1 回分を、モジュールごとの提案の並び (`- module: body, choices: …, reason: …`) とパッケージ役の分にする。並べて出た提案を、パッケージ役が 1 つの版にまとめる
- 画面: 部品をモジュールの色で分けて見せ、1 回の直しで、どのモジュールの設計役が何を変えたかを並べて見せる。ペットはモジュールの設計役ごと

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
- `rule_check` の `pass` は `hits` が空のときだけ true (schema で縛る)。式に要る値が設計に無い部品は `not_evaluable` に、足りない値の名前つきで入れ、当てていない扱い。`hits` と `not_evaluable` の `part_id` はどれも `targets` に入れる (品質保証部の記録の点検が確かめる)。設計の全体に当たる規則 (系の点検で部品が欠けている など) は `part_id` を `design` にし、`targets` にも `design` を入れる
- 規則の強さ: 規則の任意の欄 `level` (`rule`・`advice`、無ければ `rule`)。`advice` の規則の当たりは、助言として記録して画面に出すが、判定に数えず、人に聞かず (`rule_hits` にしない)、設計を止めない。`advice` の規則で評価できない部品 (`not_evaluable`) は、画面の指摘に出さない。`rule_check` にも同じ `level` を写す。`pass` の意味は変わらない (`hits` が空のときだけ true) ので、読む側が `level` を見て分ける
- 規則の束は event にだけ持ち、ファイルにはしない。`doc_id` ごとに最後の `knowledge.imported` が正 (同じファイルをもう一度渡して式が足されたときは、束を丸ごと出し直す)。渡されたファイルそのものを残すなら `knowledge/<filename>` (`layout.KNOWLEDGE_DIR`)
- 実行の途中で人の答えを待つ形は後で: 実行のフォルダに `hearing.request.json` (質問) を置き、画面が `hearing.answer.json` (答え) を置く (`layout.HEARING_REQUEST_FILE`・`HEARING_ANSWER_FILE`。`stop.request` と同じ作り)
- 系の点検: 部品は任意で `system` (駆動・操舵・制動・サス・座席・拘束・ROPS・灯火など) と `connects_to` ({`to`, `via`, `carries`}。`to` は部品・部材・フレームの節 (`nodes` の id、部品がフレームのつなぎ目に付くとき)・支持・`<支持>.<取付点>` の id) を持ち、設計を作るコードが書く。製品のフラグから要る系を決める規則 (分類 `system`) を、コードがつながりの鎖で当てる (例: 駆動輪のある製品は、モーターから駆動輪まで torque の鎖が切れずにつながる)。結果は `rule_check` の記録で、欠けた所が `hits`
- 規則の分類 `rule_category` の日本語の名前は `contract.RULE_CATEGORY_NAMES_JA`。MCP のツール `pick_domains`・`ask_user`・`import_knowledge`・`rule_check` の入力の形は schema の `tool_input.<名前>` (受付部の `hiraku/intake/tools.py` はそこから引く)

## runs/&lt;run_id&gt;/

`hiraku/contract/layout.py` の冒頭に木がある。要点:

- `project.json` — 実行の初めに書く。あとで書き換えるのは `status`・`stop_reason`・要件・`cost_usd`・`design_versions` だけ
- `events.jsonl` — 1 行 1 event、足すだけ。画面は実行中も再生もこれだけで描く
- `v001/` — 版ごとのフォルダ。`design.json` と、その版の記録の種類ごとのフォルダ (`predictions/pred-0001.json` など)。設計を直すと次の版のフォルダ (`v002/`) ができ、前の版の `design.json` は書き換えない。前の版の記録は、下の「記録を後から直すとき」の場合だけ書き換わる
- 記録のファイルに書いたものは、同じ中身を 1 つの event の `data` にも入れる。記録の種類と event の種類の対応は `layout.RECORD_EVENT` (逆向きは `layout.RECORD_OF_EVENT`、ID の欄は `<種類>_id`) だけに書き、store と点検はそれを引く
- 記録を後から直すとき (原因が「直った」になる、比べの記録に `cause_id` が付く): 同じ種類の event を、同じ ID と直したあとの全部の中身でもう一度出し、ファイルもそれに合わせる。ID ごとに最後の event が正で、ファイルはいつも最後の event の `data` と同じ。`design.json` だけは直さず、変えるときは次の版にする
- ID は実行の中で通し番号 (`pred-0001`・`ana-0001`・`cmp-0001`・`cause-0001`・`ver-0001`・`rchk-0001`・`mesh-0001`)
- `runs/` はコミットしない

- 実行を動かしたもの: project の任意の欄 `driver` (`scripted` 台本・`agent_sdk` HIRAKU のエージェントのループを本人の `ANTHROPIC_API_KEY` で・`claude_code_plugin` 本人の Claude Code のセッションに HIRAKU のプラグインをつないで、本人のサインインで。このとき HIRAKU は Claude を呼ばず、`events.jsonl` を書くのは MCP サーバーのプロセス)
  - プラグインの実行のフォルダの印: `plugin.lock` (`layout.PLUGIN_LOCK_FILE`) は、状態と outbox を読み書きする間だけ取る短い排他で、セッションが実行を持っている印ではない。持っている印は `plugin.alive` (`layout.PLUGIN_ALIVE_FILE`): MCP サーバーが実行を受け持つ間、見回りのたびに触る。画面は、`plugin.alive` の更新が `layout.PLUGIN_ALIVE_S` 秒より新しければ、その実行に Claude Code のセッションが付いていると見る (ヒアリングの答え・ナレッジ・止めるボタンを受ける)
  - 持ち主のいない実行: `driver` が `claude_code_plugin` で status が running のまま、`plugin.alive` が無いか `PLUGIN_ALIVE_S` より古い実行は、次の `start_run` (どのセッションのものでも) が閉じる。status `stopped`・stop_code `user_stop`・stop_reason「Claude Code のセッションが終わり、実行が閉じられないまま残っていた」で `run.finished` を出す

- 困った所: report の任意の欄 `issues` (`write_report` の入力にも同じ欄)。設計の結果ではなく、HIRAKU そのものが邪魔をした所を書く: `kind` (`missing_tool`・`missing_formula`・`assumed_value`・`unexplained_mismatch`・`tool_error`・`unclear_instruction`・`other`)、`what`、任意で `where`・`record_ids`・`workaround`。実験部が集めて、統括部が部署に振る

## events.jsonl の 1 行

```json
{"seq": 4, "ts": "2026-11-02T20:30:02.000+09:00", "t": 2.0, "run_id": "20261102-203000-offroad-4w",
 "kind": "hook.denied", "agent": "cae.strength", "domain": "strength",
 "text": "予測が無いので解析は止めました。…",
 "data": {"hook": "PreToolUse", "tool": "mcp__hiraku__analyze", "rule": "HK1_prediction_first"}}
```

- `seq` は 0 から抜けなし。`t` は `run.started` からの秒で、再生の間合いはこれで取る
- `kind` は schema の `event_kind` の一覧だけ。`*.recorded`・`design.created`・`analysis.finished`・`judgement.updated`・`tool.checked`・`run.started` の `data` は記録そのもの (`domains.picked`・`hearing.*`・`knowledge.imported` の data は上の表の形)
- `agent` は `lead` (まとめ役)・`designer` (設計役)・`designer.<module>` (モジュールごとの設計役、モジュールに分けた設計)・`packager` (パッケージ役)・`cae.<分野>`・`system`・`user`。ペットは `agent` 1 つに 1 匹

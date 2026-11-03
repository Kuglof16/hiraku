# qa — 品質保証の道具

HIRAKU 本体は `qa/` を読み込まない。試験 (`tests/verification/`) と点検のときだけ使う。

| ファイル | すること |
|---|---|
| `known_answers.py` | 解析の道具が合わせるべき閉じた式の答え (段差越え・輪の荷重・梁・門形・固有振動数・傾斜台・制動・単位の換算、シェルの穴あき板の Kt・片持ちの角パイプ・Bredt のねじり)。値はすべて式から計算し、式の出どころを関数ごとに書いた |
| `frame2d.py` | 平面の骨組みの剛性法。閉じた式の無い骨組みで K1 と比べる二つ目の意見。`hiraku.solvers` とはコードを共有しない |
| `check_run.py` | `runs/<run_id>/` の記録が契約 (`hiraku/contract/`) と hooks の決まり (HK1〜HK4) に合っているかを点検する |
| `judge_scan.py` | 公開する前のブランチを読み、公開してはいけないものと、人が見るべき所を並べる |
| `compare_runs.py` | 2 つの実行のフォルダを、記録のファイルごと・`events.jsonl` の行ごとに比べる (直す前と後の台本の運転が同じかを見る、リファクタリングの守り) |

## 走らせ方

venv の Python で、リポジトリの一番上から。

```
python -m unittest                                   # 全部の試験 (skip は理由つきで出る: -v)
python -m qa.check_run runs/<run_id>                 # 1 回の実行の記録を点検
python -m qa.check_run --all --recompute             # runs/ の全部。予測を K0 で計算し直す
python -m qa.check_run runs/<run_id> --effects       # hooks が止めた所とその次の記録、食い違いと原因、合否の出どころ
python -m qa.judge_scan --ref main                   # 公開する前に main を点検
```

`check_run` は誤りがあると終了コード 1、`--strict` で注意も 1 にする。`judge_scan` は公開してはいけないものがあると 1。

直す前と後で台本の運転の記録が変わらないことを見るとき (API キーは要らない):

```
python -m qa.compare_runs --make-baseline data/requirements/offroad-4w-video.yaml   # 直す前に 1 回
python -m qa.compare_runs --check data/requirements/offroad-4w-video.yaml --rel 1e-6  # 直したあと
python -m qa.compare_runs --twice data/requirements/offroad-4w-video.yaml --rel 1e-6  # 2 回回して比べる (決定的か)
python -m qa.compare_runs runs/<run_a> runs/<run_b>                                  # 2 つのフォルダを比べる
```

- 比べないもの: 時刻 (event の `ts`・`t`、`created_at`・`wall_time_s`)、`run_id` (文の中の run id と実行のフォルダも)、費用 `cost_usd`
- 違えば、ファイル (記録は ID の名前)・`events.jsonl` の行・欄の道を出して終了コード 1。比べられないとき (基準の運転が無い、など) は 2
- `--rel` が無ければ 1 バイトでも違えば違い (キーの順・空白・数の書き方も)。`--rel 1e-6` は数 (文の中の数も) の相対差 1e-6 までと、向きが逆なだけのモードの形 (`mode_shape`) を同じとみて、その数を最後の行に出す
- `--rel` が要る理由: modal の道具は PyNite の固有値計算 (scipy の `eigsh`) を使い、始めのベクトルが毎回 OS の乱数で決まる。そのため、振動数は 1e-10 ほど違い、モードの形は向きが変わることがある。ほかの記録は 2 回の運転でバイトまで同じ
- 基準の運転は `runs/baseline/<要件の名前>/`、`--check` と `--twice` の運転は `runs/compare/` に残る (`--baseline`・`--out` で変えられる)

## 照合の考え方

- 正解は閉じた式と手計算。`known_answers.py` の式は `tests/verification/test_known_answers.py` で別の道筋 (sympy で梁の微分方程式を解く、Kirsch の応力関数から応力を出して穴の縁と遠方の条件を確かめる、剛性法、差分、数値積分、Bredt のせん断流のモーメントと仕事) からもう一度出して確かめている
- 有限の幅の穴あき板の Kt は、Pilkey の近似式 (amesweb.info が Pilkey, Formulas for Stress, Strain, and Structural Matrices, 2nd ed., 2005 から引くもの) を使う。本そのものは確かめていない。d/W = 0 で Kirsch の 3、小さな穴の総断面の Kt が 3 に近いことは試験で確かめている
- 解析部の道具がまだ無い間、その試験は「何が無いか」を書いた理由で skip する。道具があって約束 (`hiraku/contract/protocols.py`) を満たさないときは skip ではなく失敗にする
- 道具の `check()` の `pass` は、`expected`・`got`・`tolerance_rel` から計算し直して確かめる (書いてある合否を信じない)

## check_run が見ること

- 形: `project.json`・`events.jsonl` の各行・記録のファイルが schema に合う。`seq` が抜けなく続き、時刻が戻らない
- ファイルと event: 記録のファイル 1 つに同じ中身の event が 1 つある。記録を直すときは同じ ID の event を出し直し、変えてよい欄は契約で決まったものだけ
- 実行の設定: `project.json` は `run.started` のまま (変えてよい欄を除く)。設計役が版 1 を作る実行では、版 1 のあとの `project.updated` が荷重条件・選んだ分野・役を出し直す。`stop.request` は実行が終われば残らない。`tools/` には、実行の中で作った道具の試みごとに `<名前>-<n>.py` と `.check.json` がそろう
- 報告: `report.json` は最後の `report.written` と同じ。報告は選んだ分野をすべて持ち、状態と合否の ID はその時のタイルと同じ、食い違いはその版の食い違いの照合で、差と原因は記録と同じ (判定のある荷重条件の食い違いを落としていれば注意)
- HK1〜HK4: 解析の前に同じ版・同じ荷重条件の予測がある / 照合に通った道具だけを使う / 食い違いに原因が書かれるまで、その分野の解析と照合は進まない / 選んだ分野が判定されるまで done で終わらない (判定されたとは、合否の基準のある荷重条件のすべてに最後の版の合否があること。赤のタイルでも、ほかの試験に合否が無ければ終わっていない)
- 実行の終わり: `stop_code` は status と合う (`done` ⇔ `done`、`error` ⇔ `failed`)。`not_judged` は判定の無い荷重条件が残っているときだけ、`round_limit` は設計の直しを上限まで使ったときだけ
- 照合の数字: 差は符号つき (解析 − 予測)。一致・食い違いを計算し直し、許す幅は荷重条件から取る
- 合否の出どころ: 一致した照合からなら値は解析。説明された安全側の食い違いからなら、原因が `safe_side` と理由を持ち、予測が基準に対して不利な側 (`<=` なら解析以上、`>=` なら解析以下) にあって、値は予測。合否と余裕は計算し直し、基準は荷重条件から取る
- 分野のタイル: 落ちた合否があれば赤 (判定の無い荷重条件が残っていても)。無くて判定の無い荷重条件があれば pending、全部合格なら緑。赤の数は並べた合否と合う
- 組み立てと干渉: 手順の番号が 1 から抜けなく、先に入るべき部品が先にある。干渉の数と最も深い重なりが一覧と合う
- 時系列: 横軸 (`t_s` か `x`) の点の数が値の数と同じ
- 規則の照合 (`rule_check`): その版の設計のあと。`pass` は当たり (`hits`) が無いときだけ真。当たりと当てられなかった部品は対象 (`targets`) の中にあり、両方には入らない。規則はその前の `knowledge.imported` で入っている (実行に 1 つでもあれば)。設計に無い対象は注意
- メッシュ (`mesh`): 版ごとに 1 つ、その版の設計のあと。要素の節の番号は節の数の中、1 つの要素に同じ節が 2 度無い、`element_member` は要素の数だけあり、その部材の厚さがある (どの要素にも使われない節と、設計に無い部材は注意)。メッシュを名指しする field は、解析が始まる前に作られた同じ版のメッシュを指し、`per_element`・`per_mesh_node` の数が要素・節の数と同じ
- 記録の種類と event の対応は契約の `layout.RECORD_EVENT` を引く (check_run に写し書きしない)
- `--recompute`: 予測の値を K0 の式と記録された入力からもう一度計算する

## judge_scan が見ること

- 誤り (公開の前に直す): 鍵に見えるもの、GitHub の noreply 以外のメールアドレス、ホームフォルダへの道、手元に置くべきファイル (`.env`・`runs/` など) が追跡されていること、期間の始まり (2026-11-02 12:00 JST) より前の日付のコミット、公開してはいけない言葉と道の型 (一覧はリポジトリの外のファイルから `--words-from` で読み、このリポジトリには入れない)
- 人が見るもの: 人の言葉をそのまま写したように見える書き方や作業の記録のような書き方、TODO などの未完成の印 (下の組で中身を書く土台の組のメソッドは数えない)、出典の書かれていない 3 桁以上の数値 (外から持ってきた値でないもの、つまり説明の文・道具の照合の例・定義で決まる定数・閉じた式の整数の係数・範囲つきの設計の変数・`hiraku/designgen/` が書いた設計の JSON は数えない)、`verified: false` の値があるのに README の「確かめていない」の表に名前の無いデータ (設計の JSON は、それを書く `hiraku/designgen/` の名前でよい)、出典の無いデータ、README の「確かめていない」の表そのもの、画像などの権利
- 誤りは全部直し、人が見るものは 1 行ずつ確かめる。外から持ってきた数値の出典は、使った所と README の両方に書く

# qa — 品質保証の道具

HIRAKU 本体は `qa/` を読み込まない。試験 (`tests/verification/`) と点検のときだけ使う。

| ファイル | すること |
|---|---|
| `known_answers.py` | 解析の道具が合わせるべき閉じた式の答え (段差越え・輪の荷重・梁・門形・固有振動数・傾斜台・制動・単位の換算、シェルの穴あき板の Kt・片持ちの角パイプ・Bredt のねじり)。値はすべて式から計算し、式の出どころを関数ごとに書いた |
| `frame2d.py` | 平面の骨組みの剛性法。閉じた式の無い骨組みで K1 と比べる二つ目の意見。`hiraku.solvers` とはコードを共有しない |
| `frame3d.py` | 3D の骨組みの剛性法 (6 自由度、numpy だけ、ずれ変形なし)。K1 (PyNite) とコードを共有しない二つ目の意見。デモの設計の版 1・版 2 を、止まっているときと段差 100 mm で K1 と比べる (`tests/verification/test_frame3d.py`: 変位・反力・部材の応力が 1e-6 の内) |
| `mesh_quality.py` | シェルのメッシュの品質をソルバーとは別に測る (縦横比・角・反り・ヤコビアン比・自由縁・重なった節・向きの食い違い、六面体の角のヤコビアンと体積) |
| `first_look.py` | 動画のカットとアプリの画面を、初めて見る人の目で点検する。絵を 8 枚ずつのシートにし、画像の判定器 (Codex CLI) に、何を言っている絵か・3 秒で分かるか・目が行く所・読めない札と重なり・直し方を絵ごとに聞き、直す所の一覧にする (判定器とモデルの名前も書く)。Pillow と ffmpeg が要る |
| `check_run.py` | `runs/<run_id>/` の記録が契約 (`hiraku/contract/`) と hooks の決まり (HK1〜HK4) に合っているかを点検する |
| `judge_scan.py` | 公開する前のブランチを読み、公開してはいけないものと、人が見るべき所を並べる |
| `run_tests.py` | 全部の試験を、試験のクラスごとに別のプロセスで並べて回す (`python -m unittest` と同じ試験。数で確かめる)。メモリが足りずに落ちたクラス (MemoryError、WinError 1455) だけは、あとで 1 つずつ回し直し、「rerun alone after a memory error」と結果を出して、その結果だけを数える。全部の試験は PC で 1 つずつ回す: 始める前に PC 全体の鍵 (システムの一時フォルダの `hiraku-run-tests/suite.lock`。プロセスが終われば外れる) を取り、ほかが持っている間は「waiting for <pid> (<worktree>, since hh:mm)」と出して待つ。鍵を持っている間は CPU の数 − 4 (8 以上) のプロセスで回す。急ぐときだけ `--no-wait` (待たずに 8 プロセスで回し、重なりを「warning: another qa.run_tests is running on this PC」で知らせる) |
| `procs.py` | 時間切れのあるコマンドの実行。時間切れのときは、そのコマンドが始めたプロセスごと止める (subprocess の時間切れは、そのコマンドのプロセスしか止めない。試験のプロセスを止めても、それが始めた台本の運転は回り続ける)。`run_tests` のクラスの時間切れと、台本の運転の時間切れで使う。`run_tests` の試験のプロセスと台本の運転には、BLAS のスレッドを 1 本にして渡す (`OPENBLAS_NUM_THREADS=1`・`OMP_NUM_THREADS=1`、もう決めてあればそのまま)。numpy と scipy の OpenBLAS は、使うかもしれないスレッドの数だけメモリを先に取るので、たくさん同時に回すと PC のメモリが尽きる |
| `compare_runs.py` | 2 つの実行のフォルダを、記録のファイルごと・`events.jsonl` の行ごとに比べる (直す前と後の台本の運転が同じかを見る、リファクタリングの守り) |

## 走らせ方

venv の Python で、リポジトリの一番上から。

```
python -m qa.run_tests                               # 全部の試験を並べて回す (main に入れる前はこれ)
python -m unittest                                   # 全部の試験を 1 つのプロセスで (skip は理由つきで出る: -v)
python -m qa.check_run runs/<run_id>                 # 1 回の実行の記録を点検
python -m qa.check_run --all --recompute             # runs/ の全部。予測を K0 で計算し直す
python -m qa.check_run runs/<run_id> --effects       # hooks が止めた所とその次の記録、食い違いと原因、合否の出どころ
python -m qa.judge_scan --ref main                   # 公開する前に main を点検
```

`check_run` は誤りがあると終了コード 1、`--strict` で注意も 1 にする。`judge_scan` は公開してはいけないものがあると 1。

GitHub Actions (`.github/workflows/light-tests.yml`) は、main に push されたときだけ、Linux (ubuntu-latest、Python 3.12) で軽い試験を回す。回すのは `python -m qa.ci_light` で、中身は `qa/ci_light.py` の一覧 (契約・受付・store・ツールの棚と MCP・hooks・check_run など)。解析を回す試験、ブラウザ、台本の運転、Rapier、遅い試験は外している。この PC で、LF で取り出した新しい clone (道具の照合の覚え無し) に 1 プロセスで回すと、648 本で 57 秒だった。これは main で軽い試験が通っている印で、関門ではない。main に入れる前の全部の試験は今のまま。`ci-trial` の枝に push すれば、main の前に同じものを 1 回回せる。

直す前と後で台本の運転の記録が変わらないことを見るとき (API キーは要らない):

```
python -m qa.compare_runs --make-baseline data/requirements/offroad-4w-video.yaml   # 直す前に 1 回
python -m qa.compare_runs --check data/requirements/offroad-4w-video.yaml            # 直したあと
python -m qa.compare_runs --twice data/requirements/offroad-4w-video.yaml            # 2 回回して比べる (決定的か)
python -m qa.compare_runs runs/<run_a> runs/<run_b>                                  # 2 つのフォルダを比べる
```

- 比べないもの: 時刻 (event の `ts`・`t`、`created_at`・`wall_time_s`・解析の `timings_s`。契約で、時計の時間はこの 2 つの欄にだけ書く)、`run_id` (文の中の run id と実行のフォルダも)、費用 `cost_usd`
- 比べるための台本の運転は、どれも道具の照合をその場で全部回す: `HIRAKU_TOOL_CHECK_REUSE=0` (前の実行から使い回すと `tool.checked` に `cached_from` が入る) で、試験のための覚え (`HIRAKU_CHECK_CACHE`。使うと `agent.said` が 1 行増える) も外す
- 違えば、ファイル (記録は ID の名前)・`events.jsonl` の行・欄の道を出して終了コード 1。比べられないとき (基準の運転が無い、など) は 2
- `--rel` が無ければ 1 バイトでも違えば違い (キーの順・空白・数の書き方も)。`--rel 1e-6` は数 (文の中の数も) の相対差 1e-6 までと、向きが逆なだけのモードの形 (`mode_shape`) を同じとみて、その数を最後の行に出す
- 動画用の要件の台本の運転は、2 回回すとバイトまで同じ (`--twice` で確かめた)。前は modal の道具の固有値計算 (scipy の `eigsh`) が乱数の始めのベクトルから解き、振動数が 1e-10 ほど違い、モードの形の向きが変わっていた。いまは始めのベクトルを固定し、モードの形の向きをそろえている。`--rel` は、乱数を使う道具がまた入ったときのためのもの
- 基準の運転は `runs/baseline/<要件の名前>/`、`--check` と `--twice` の運転は `runs/compare/` に残る (`--baseline`・`--out` で変えられる)

重い試験は、main に入れる前に毎回回す `python -m unittest` には入れない。毎回回す `test_real_runs` は、動画用の要件の台本の運転 1 本だけ (記録が契約と hooks に合うか、終わり方)。`HIRAKU_SLOW_TESTS=1` のときだけ、次も回す:

- ほかの要件ファイル (`offroad-4w.yaml` など) の台本の運転。台本の運転は要件ファイルごとに 1 つのクラス (`ScriptedRun_<ファイル名>`、例 `ScriptedRun_offroad_4w_video`) なので、`python -m qa.run_tests` は並べて回す。1 本の運転の時間切れは 1200 秒で、かかった秒数を標準エラーに 1 行出す
- 動画用の要件をもう 1 回回して、バイトで比べる。2 回目は 1 回目と同時に回す (`ScriptedRun_offroad_4w_video` の中で 2 つのスレッド)。なので、そのクラスも運転 1 本ぶんの時間で終わる
- `needs_mesh` のツールを、デモのフレームのメッシュ全体で解く。使える設計の型を持つツール (`applies_to`) はその型の設計で解き、自分でメッシュを作るツール (`make_mesh`) にはそのメッシュ (`mesher` はツールの名前) を渡し、field はそのメッシュを名指す。`make_mesh` の無いツールは部材ごとの値だけ。ツールごとに 1 つのクラス (`MeshFieldRecord_<ツール名>`、例 `MeshFieldRecord_frame_shell`) なので、これも並べて回す。`HIRAKU_SLOW_TESTS` が無いときは `MeshFieldRecord` が 1 つだけあって、skip の理由を出す

`HIRAKU_SLOW_TESTS=1` で回すのは次のとき:

- 台本の運転の通り道 (`hiraku/solvers/`・`hiraku/agent/`・`hiraku/store/`・`hiraku/tools/`・`hiraku/catalog/`・`data/catalog/`・`data/requirements/`) を変える取り込みのあと、取り込んだ main で 1 回。同じ回にいくつかの部署を取り込むなら、最後の 1 回だけでよい
- 動画や `showcase/` に使う運転を記録する前に 1 回
- コマンドはどちらか: `python -m qa.compare_runs --twice data/requirements/offroad-4w-video.yaml`、または `HIRAKU_SLOW_TESTS=1 python -m unittest tests.verification.test_real_runs`

## 照合の考え方

- 正解は閉じた式と手計算。`known_answers.py` の式は `tests/verification/test_known_answers.py` で別の道筋 (sympy で梁の微分方程式を解く、Kirsch の応力関数から応力を出して穴の縁と遠方の条件を確かめる、剛性法、差分、数値積分、Bredt のせん断流のモーメントと仕事) からもう一度出して確かめている
- 有限の幅の穴あき板の Kt は、Pilkey の近似式 (amesweb.info が Pilkey, Formulas for Stress, Strain, and Structural Matrices, 2nd ed., 2005 から引くもの) を使う。本そのものは確かめていない。d/W = 0 で Kirsch の 3、小さな穴の総断面の Kt が 3 に近いことは試験で確かめている
- 解析部の道具がまだ無い間、その試験は「何が無いか」を書いた理由で skip する。道具があって約束 (`hiraku/contract/protocols.py`) を満たさないときは skip ではなく失敗にする
- 道具の `check()` の `pass` は、`expected`・`got`・`tolerance_rel` から計算し直して確かめる (書いてある合否を信じない)

## check_run が見ること

- 形: `project.json`・`events.jsonl` の各行・記録のファイルが schema に合う。`seq` が抜けなく続き、時刻が戻らない
- ファイルと event: 記録のファイル 1 つに同じ中身の event が 1 つある。記録を直すときは同じ ID の event を出し直し、変えてよい欄は契約で決まったものだけ
- 実行の設定: `project.json` は `run.started` のまま (変えてよい欄を除く)。設計役が版 1 を作る実行では、版 1 のあとの `project.updated` が荷重条件・選んだ分野・役を出し直す。途中で変わった要件は、いつでも `project.updated` で出し直してよい。`stop.request`・ヒアリングのファイル・`inbox/` の頼みは実行が終われば残らない (残れば注意)。`knowledge/` には `knowledge.imported` が名指すファイルだけがある。`tools/` には、実行の中で作った道具の試みごとに `<名前>-<n>.py` と `.check.json` がそろう
- 報告: `report.json` は最後の `report.written` と同じ。報告は選んだ分野をすべて持ち、状態と合否の ID はその時のタイルと同じ、食い違いはその版の食い違いの照合で、差と原因は記録と同じ (判定のある荷重条件の食い違いを落としていれば注意)
- HK1〜HK5: 解析の前に同じ版・同じ荷重条件の予測がある / 照合に通った道具だけを使う / 食い違いに原因が書かれるまで、その分野の解析と照合は進まない / 選んだ分野が判定されるまで done で終わらない (直す回数の上限 `round_limit` で止まるときも、最後の版は判定まで進める。判定されたとは、合否の基準のある荷重条件のすべてに最後の版の合否があること。赤のタイルでも、ほかの試験に合否が無ければ終わっていない) / 軽い計算から: その版で、低い段 (`stage`) の道具ですでに確かめた荷重条件に合格が揃うまで、高い段の道具の解析は始まらない (段は `tool.checked` の道具の仕様から。無ければ 1。フックと同じく、限界の外の荷重条件 (`-beyond`、落ちるための条件) は数えない。解析する荷重条件そのものは、低い段の道具の判定が赤のときだけ数える)
- 実行の終わり: `stop_code` は status と合う (`done` ⇔ `done`、`error` ⇔ `failed`)。`not_judged` は判定の無い荷重条件が残っているときだけ、`round_limit` は設計の直しを上限まで使ったときだけ
- 照合の数字: 差は符号つき (解析 − 予測)。一致・食い違いを計算し直し、許す幅は荷重条件から取る。照合の量 (`quantity`) は、実際に比べた解析の結果の名前 (予測の量と違ってよい。例: 予測 `f_wheel` と結果 `wheel_rotation_frequency`)。`where` (部材や部品の id) があれば、その場所の結果と合う
- 合否の出どころ: 一致した照合からなら値は解析。説明された安全側の食い違いからなら、原因が `safe_side` と理由を持ち、予測が基準に対して不利な側 (`<=` なら解析以上、`>=` なら解析以下) にあって、値は予測。合否と余裕は計算し直し、基準は荷重条件から取る
- 分野のタイル: 落ちた合否があれば赤 (判定の無い荷重条件が残っていても)。無くて判定の無い荷重条件か、止められた荷重条件 (判定に使う量のいちばん新しい照合が、原因が書かれていないか、解けてもいず安全側でもない食い違い。それより前の食い違いは、あとの照合で置き換わったものとして止めない。判定の量の予測と結果がどちらも限度の 1e-6 倍より小さいときは、基準の `stand_in_quantity` のいちばん新しい照合で見る。道具のいちばん新しい結果が基準を超えている。`frame_shell` は `max_hot_spot_stress` で見る) があれば pending、全部合格なら緑。各分野・各版の最後のタイルは、実行の終わりでもこの決まりに合う (あとの解析が基準を超えたら、前の安全側の合否は効かない)。赤の数は並べた合否と合う
- 組み立てと干渉: 手順の番号が 1 から抜けなく、先に入るべき部品が先にある。干渉の数と最も深い重なりが一覧と合う
- 時系列: 横軸 (`t_s` か `x`) の点の数が値の数と同じ
- 規則の照合 (`rule_check`): その版の設計のあと。`pass` は当たり (`hits`) が無いときだけ真。当たりと当てられなかった部品は対象 (`targets`) の中にあり、両方には入らない。規則はその前の `knowledge.imported` で入っている (実行に 1 つでもあれば)。設計に無い対象は注意 (`design` は設計の全体を指すので注意にしない)
- メッシュ (`mesh`): 版ごとに 1 つ、その版の設計のあと。要素の節の番号は節の数の中、1 つの要素に同じ節が 2 度無い、`element_member` は要素の数だけあり、その部材の厚さがある (どの要素にも使われない節と、設計に無い部材は注意)。メッシュを名指しする field は、解析が始まる前に作られた同じ版のメッシュを指し、`per_element`・`per_mesh_node` の数が要素・節の数と同じ
- メッシュの品質: ソルバーとは別のコード (`qa/mesh_quality.py`) で、要素ごとに縦横比 (いちばん長い辺 / 短い辺)・角の最小と最大・反り (対角線で分けた 2 つの三角の面の角)・ヤコビアン比 (角ごとの外積の、最小 / 最大)・面積を測り、自由縁・重なった節・つながりの組・向きの食い違い (同じ部材の 2 要素だけが持つ辺を同じ向きに回る。管ごとに外を向くので、別の部材どうしの継ぎ目と、3 枚以上が会う継ぎ目の辺は数えない) を数える。誤りにするのは、面積や辺が 0 の要素と折れ返った要素 (ヤコビアン比 0 以下)、`quality` の中の食い違い (`worst`・`over_limit` が `per_element` と合わない、限度を越えた要素があるのに verdict が ok、数の合わない `per_element`)、verdict が fail のメッシュに描いた ok の解析。ソルバーの `worst` や topology の数が別に測った値と違うとき、重なった節、向きの食い違いは注意 (定義が少し違ってよいため)
- 記録の種類と event の対応は契約の `layout.RECORD_EVENT` を引く (check_run に写し書きしない)
- `--recompute`: 予測の値を K0 の式と記録された入力からもう一度計算する

## judge_scan が見ること

- 誤り (公開の前に直す): 鍵に見えるもの、GitHub の noreply 以外のメールアドレス、ホームフォルダへの道、手元に置くべきファイル (`.env`・`runs/` など) が追跡されていること、期間の始まり (2026-11-02 12:00 JST) より前の日付のコミット、公開してはいけない言葉と道の型 (一覧はリポジトリの外のファイルから `--words-from` で読み、このリポジトリには入れない)
- 人が見るもの: 人の言葉をそのまま写したように見える書き方や作業の記録のような書き方、TODO などの未完成の印 (下の組で中身を書く土台の組のメソッドは数えない)、出典の書かれていない 3 桁以上の数値 (外から持ってきた値でないもの、つまり説明の文・道具の照合の例・定義で決まる定数・閉じた式の整数の係数・範囲つきの設計の変数・`hiraku/designgen/` が書いた設計の JSON は数えない)、`verified: false` の値があるのに README の「確かめていない」の表に名前の無いデータ (設計の JSON は、それを書く `hiraku/designgen/` の名前でよい)、出典の無いデータ、README の「確かめていない」の表そのもの、画像などの権利
- 誤りは全部直し、人が見るものは 1 行ずつ確かめる。外から持ってきた数値の出典は、使った所と README の両方に書く

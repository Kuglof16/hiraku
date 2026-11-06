# HIRAKU

作りたいもの (たとえば、林道を走れる小さな車) の条件を渡すと、Claude のエージェントたちが、分野ごとに「手計算で結果を予測する → CAE (コンピューターでの解析) で解析する → 予測と比べる → 判定する」を進め、NG の所の設計を直していくアプリです。
計算の様子は、3D の画面で見られます。使うのは Windows の PC と Python 3.12 で、Claude を呼ばない「台本の運転」なら API キーは要りません。

HIRAKU: Claude agents predict each result by hand calculation before running the CAE analysis, check one against the other, and revise the design.

Built with Claude: Japan 2026 (2026-11-02 〜 11-08) の期間中に作っています。

## 動かし方

用意するもの: Windows の PC、git、Python 3.12、インターネット (部品を入れるとき・画面を開くとき)。

1. Python 3.12 が入っているか確かめます。コマンドの窓 (コマンドプロンプトか PowerShell) で次を打ちます。

   ```
   py -3.12 --version
   ```

   `Python 3.12.…` と出れば大丈夫です。出ないときは、https://www.python.org/downloads/ から 3.12 を入れ、窓を開き直してもう一度確かめます。

2. HIRAKU を取ってきます。

   ```
   git clone https://github.com/Kuglof16/hiraku.git
   cd hiraku
   ```

3. HIRAKU だけの Python の環境 (`.venv`) を作り、部品を入れます。Python は 3.12 を使います (`py -3.12`)。3.13 以降だと、部品の numba に合う版が無いことがあります (確かめていません)。

   ```
   py -3.12 -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   ```

4. `HIRAKU.cmd` をダブルクリックします (窓から `HIRAKU.cmd` と打ってもかまいません)。黒い窓が開き、窓に API キーが無いときは、始めに 1 回だけキーを聞かれます (打った字は見えません)。Claude で新しく実行しないなら、そのまま Enter を押します。そのあと画面のサーバーが起きて、ブラウザで画面が開きます (アドレスは http://127.0.0.1:8765/)。HIRAKU を使っている間は、黒い窓を閉じません。終わるときは、黒い窓を閉じます。

うまくいかないとき:

- `HIRAKU.cmd` の窓に「.venv\Scripts\python.exe was not found」と出て止まる: 手順 3 がまだです。
- 「the screen's server did not start」と出る: ほかの窓で HIRAKU が開いたままです。その窓を使うか、閉じてからもう一度ダブルクリックします。
- ブラウザが開かない、または別のものが 8765 番を使っている: 別の番号で起こします。

  ```
  .venv\Scripts\python -m hiraku.webserve --port 8766 --open
  ```

- 3D が出ない: 画面は 3D の部品 (three.js 0.170.0、MIT) を jsDelivr から読み込むので、インターネットにつながっている必要があります。
- 字は BIZ UDPGothic があればそれを使い、無ければ游ゴシックかメイリオで出ます。字は同梱していません。

## 保存した実行を見る

画面を開くと、まず一覧「HIRAKU でできること」が出ます (`?list=1` も同じ一覧)。「まずはこれ」の「見る」を押すと、リポジトリの `showcase/` に保存した実行のうちいちばん新しいもの (Claude Code のプラグインで回し、v7 で終わった `20261105-160725-offroad-4w`) を再生します。何も計算せず、無料です。

- 再生は長いので、下の時間の棒を右端まで送ると、終わったときの判定 (④ 解析結果) と組み立て (⑤ 組み立て) が見られます。棒の横の「1×」を押すと、速さが 0.5× から 8× まで変わります。
- `showcase/` には 4 つの実行と、動画の N10 の場面の記録 (`n10/`) があります。ほかの実行は、一覧の「記録を見る」か、URL の `?run=<実行の名前>` で開きます。実行ごとの中身と見る順のおすすめは [showcase/README.md](showcase/README.md)。
- 終わった実行で、合わないように見える所:
  - 記録の最後の「まだ確かめていない 45」は、要件の値のうち出典を確かめていない (仮定を含む) ものの数です。
  - この実行で選んだ分野は、強度信頼性と組み立てだけです。P1 (段差越え) は R1 に力を渡すために回した「参考 (判定しない)」なので、④ の動力は「選んでいない」と出ます。
  - ⑤ の手順ごとの「工具」「手」の × は、見せるだけで判定に入りません (手と工具の通り道の決まりに出典が無いため。`hiraku/catalog/builders/analysis.py` の `H5_HANDS_NOTE`)。
  - 上の「費用 不明」は、プラグインの実行では使った額が HIRAKU に届かないためです (下の「Claude Code のプラグインで回す」)。

## 台本の運転

「台本の運転」は、Claude を呼ばずに、決まった順にツールを呼んで、設計を試験して直すところまでを再現する運転です。無料で、API キーは要りません。

1. 画面の最初のページ「HIRAKU でできること」を開きます。
2. まず短いもの: 「デモ」の「要件の書き間違いを見つける」の行の「再現する」を押します (cm と mm の書き間違いを、手計算と解析の不一致から見つけて直す流れです)。本格的なものは、「小さなオフロード車の 11 の試験」の行の「再現する」です。
3. 押すと、ボタンが押せなくなり、画面が「③ 解析」に変わるまで **十数秒から 1 分ほど** 何も変わりません。使うツールの確認をしている時間で、止まっているわけではありません。その間にもう一度押さないでください。
4. 「③ 解析」に変わると、実行が進みます。かかった時間 (この PC で測った値。PC やほかの仕事で変わります):
   - 「要件の書き間違いを見つける」: 約 50 秒 (押してから 1 分ほど)
   - 「小さなオフロード車の 11 の試験」: 約 9 分 (526 秒。試験を同時に回していたときの値)

   途中で止めたいときは、上の「■ 止める」を押します。
5. 見る所:
   - 画面の上の「vN・繰り返し a / b 回」: 設計の v (v1、v2 …) と、直して確かめ直した回数 (b は上限) です。
   - 「③ 解析」の右のカード: いま確かめている試験の、手計算の予測・CAE の結果・その差と判定です。
   - 左の「記録」: 実行の中で起きたことが、順に並びます。
   - 画面の下の「hooks」の並び: 実行の途中で、順番を守らせるために止めた回数が出ます (たとえば「HK1 予測が先」)。
   - ④「解析結果」のタブ: 分野ごとの判定のタイルが並びます。タイルを押すと、その分野の解析をタブの中で再生します。
   - 3D の右上の「大きく見る」のボタン: 3D を画面いっぱいにします。もう一度押すか Esc で戻ります (URL の `?present=1` でも同じ)。

   「小さなオフロード車の 11 の試験」は、NG の所を 4 回直して、v5 で全部 OK になります (画面の行の説明)。
6. 終わった実行は、一覧の「記録を見る」に並び、「見る」でいつでも再生できます (古いものは「この PC の記録」を開くと出ます)。

記録は `runs\<実行の名前>\events.jsonl` に残ります。点検するには、次を打ちます。

```
.venv\Scripts\python -m hiraku check runs\<実行の名前>
```

画面を使わずに台本の運転を回すこともできます。

```
.venv\Scripts\python -m hiraku run data/requirements/offroad-4w.yaml --scripted
```

OK と NG をどう出しているかは、[docs/judging.md](docs/judging.md) に 1 枚でまとめてあります。

## Claude で回す

Claude で新しく実行するには、Anthropic の API キーが要ります。キーはこの README にも、コミットにも、チャットにも貼りません。入れ方は 2 つあります。

- `HIRAKU.cmd` をダブルクリックし、始めに聞かれたときにキーを貼って Enter を押す。キーはその窓の中だけに置き、どこにも書き出しません。
- 先に窓にキーを入れてから `HIRAKU.cmd` を起こす (このときは聞かれません)。キーは HIRAKU を起こす窓だけに入れます (PC 全体には入れません)。

  ```
  set ANTHROPIC_API_KEY=（あなたのキー）
  HIRAKU.cmd
  ```

  PowerShell なら `$env:ANTHROPIC_API_KEY = "（あなたのキー）"` のあと `.\HIRAKU.cmd` です。

画面では:

- キーがあると、行の「実行する」が押せます。キーが無いと「実行する」は押せず、横に「Claude のキーが要ります」と出ます。
- 行の「くわしく」に、費用の「見込み」と「上限」が出ます。「実行する」を押すと、見込みと上限を見せて確かめてから始まります。上限は要件のファイルの `cost_limit_usd` で、上限に着くと実行は止まります。
- 費用の上限が効くのは、API キーで動かす実行だけです。下のプラグインの実行では、払うのは自分の Claude Code で、HIRAKU には使った額が届かないので、上限を守らせられません (記録の費用は null のまま。[plugins/hiraku/README.md](plugins/hiraku/README.md))。
- 見込みは請求の額ではありません。Claude Code のプラグインで回した 1 本の実行 (`showcase/20261105-160725-offroad-4w`) の使ったトークン (usage) を値段表 (https://claude.com/pricing、2026-11-03 に読み、11-05 に読み直した) に掛けた 25.38 USD を、その実行の記録の数 434 で割り、台本の運転の記録の数に掛けたものです (`hiraku/catalog/cost.py` の冒頭)。

画面を使わずに回すときは、キーを入れた窓で `--scripted` を外します。

```
.venv\Scripts\python -m hiraku run data/requirements/offroad-4w.yaml
```

### Claude Code のプラグインで回す

自分の Claude Code (自分のサインインの枠) で回す道です。API キーは要りません。コマンドはリポジトリのフォルダで打ちます (Claude Code だけは、どのフォルダから起こしてもかまいません)。

1. 「動かし方」の手順 3 まで済ませます (`.venv` が要ります)。そのうえで、窓で `python --version` が動くことを確かめます。プラグインの MCP サーバーと hooks は PATH の `python` で起き、そこから HIRAKU の Python に切り替えます。切り替え先は、環境変数 `HIRAKU_PYTHON` があればそれ、無ければリポジトリの `.venv` です (`plugins/hiraku/scripts/hiraku_cli.py` の冒頭)。PATH に `python` が無いと、`/mcp` でつながりません。
2. 先にキー無しで確かめます (40 秒ほど。Claude を呼ばない台本の運転です)。

   ```
   .venv\Scripts\python -m hiraku run data/requirements/offroad-4w-n12.yaml --scripted
   ```

3. Claude Code を、プラグインを指定して起こします。`--allowedTools` を付けないと、役がツールを呼ぶたびに許可を聞かれます。

   ```
   claude --plugin-dir <リポジトリ>/plugins/hiraku --allowedTools "mcp__plugin_hiraku_hiraku"
   ```

4. Claude Code の中で `/mcp` を打ち、hiraku のサーバーがつながっているか見ます。つながっていたら、`/hiraku:run offroad-4w-n12.yaml` を打ちます (ファイル名だけなら `data/requirements/` から探します。最初の `start_run` は 30〜60 秒かかります)。
5. 実行の様子は、同じリポジトリのフォルダで `.venv\Scripts\python -m hiraku.webserve` を起こして、画面で見ます (画面は `./runs` を見るので、リポジトリのフォルダから起こします)。

プラグインはコピーせず、リポジトリの中から読ませます。くわしくは [plugins/hiraku/README.md](plugins/hiraku/README.md)。この道で回して v7 で終わった実行が `showcase/20261105-160725-offroad-4w` です。

## この README の言葉

| 言葉 | 意味 |
|---|---|
| 分野 | 何を確かめるかの区分: 操安・動力・空力・衝突・強度信頼性・音振・熱 の 7 つ (これとは別に、組み立てと人への当たりを見る) |
| 試験 | 分野ごとの 1 つの確かめ。P1 (段差越え)・R1 (骨組みの強度)・N1 (振動)・H1 (ハンドルを切ったときの干渉)・H3 (乗る人の体格)・P3 (発進と加速) のように、頭の字は分野 (S 操安・P 動力・F 空力・C 衝突・R 強度信頼性・N 音振・T 熱・H 組み立てと人) |
| 荷重条件 | 試験 1 つぶんの条件: 何をかけ、何を基準にし、予測と解析の差をどこまで予想通りとするか |
| 手計算 (予測) | 式で先に結果を出すこと。式は K0 の中にある |
| CAE | コンピューターで物の動きや強さを計算する解析。HIRAKU の中のツールが計算する |
| 判定 | 手計算の予測と CAE の結果を比べ、基準があれば OK か NG かを出すこと。不一致なら原因を書くまで先へ進めない |
| v・繰り返し | 設計の v (v1、v2 …)。NG のとき、設計を直して次の設計を作って確かめ直す。その回数が繰り返し |
| 予想通り・不一致 | 手計算の予測と CAE の結果を比べた答え。差が許す幅の中なら予想通り、外なら不一致 |
| OK・NG | 基準に対する判定 (画面では緑と赤) |
| ツール | CAE のツール (解析のプログラム)。答えの分かっている例と合ってから使う |
| hooks | 実行の途中で、順番を守らせるために止める決まり (HK1〜HK6)。たとえば「予測が先」「食い違いは原因が先」 |
| 台本の運転 / Claude の運転 | 決まった順にツールを呼ぶ運転 (Claude を呼ばない) / Claude が自分で考えて進める運転 (API キーが要る) |
| 役 | エージェントの役目: まとめ役・設計役 (設計がモジュールに分かれているときは、モジュールごとの設計役)・パッケージ役・分野ごとの CAE 役 |
| K0〜K5 | 解析の土台: K0 手計算の式・K1 梁と板の有限要素・K2 3D の車両の物理・K3 電力と熱・K4 幾何と干渉・K5 感度 |
| 記録 (events.jsonl) | 実行の中で起きたことを 1 行ずつ書いたファイル。画面はこれだけから描く |
| 要件 | 作りたいものの条件 (使う場所・荷・速さ・基準など)。`data/requirements/` にある |

## 試験 (任意)

HIRAKU を使うだけなら要りません。コードを直したり確かめたりするときに回します。

```
.venv\Scripts\python -m unittest
```

試験の数は、下の「試験の数」にあります。全部にかかる時間は測っていません。飛ばされるものもあります (ブラウザの試験・Rapier との照らし合わせ・重い試験)。

板とソリッドの大きなモデルを速く解く道 (任意。Windows と Linux だけ。無ければ SciPy の SuperLU で解く):

```
.venv\Scripts\python -m pip install -r requirements-fast.txt
```

- ブラウザの試験は Playwright を使い、`python -m tests.web.browser` で回します (中で `HIRAKU_WEB_BROWSER=1` を入れる。試験の名前を 1 つ渡すと、それだけを回す)。ふつうの全部の試験では飛ばします (Playwright は requirements.txt に入れていません)
- Rapier と照らし合わせる試験は、`qa/rapier` で `npm install` をしておく必要があります。していなければ、その試験は飛ばします
- 動画の書き出しのツールは `promo/` にあり、要るものは `promo/requirements-video.txt` です
- 画面の `?run=sample` は形の見本 (数値は計算結果ではない)、`?look=real` は実物の見た目の確認用です

ここから後ろは、HIRAKU の中身と、作り方・出典の説明です。

## いま動くもの

- デモの車両 (林道を走る 2 人乗りの 4 輪) で、要件のファイルに並べた 11 の試験 (段差越え P1・強度 R1・レールの継ぎ目の局所の応力 R1s・振動 N1・荷重の通り道 R8・操作部の荷重 R7・干渉とハンドルの切れ角 H1・乗員の体格 H3・泥のはね H6・乗り降り H7・発進 P3) を「予測 → 解析 → 比べる → 判定」で回す流れ。基準のある試験は OK か NG かまで出す (P1 は基準が無く、予測と比べるまで)。予測の無い解析は hooks が止め、不一致は原因を書くまで先へ進めない。説明できて安全側と判断できる不一致は、不利な方の値で OK か NG かを出す
- 設計役が設計の変数を変えて次の設計を作る (`revise_design`)。v ごとに何が NG で、次の v で何を変えたかは、保存した実行ごとに [showcase/README.md](showcase/README.md) にある
- 解析のツール 27 個 (梁と板 (シェル) の有限要素・3D の車両の物理・電力と熱・幾何と干渉。棚の 17 (`hiraku/solvers/shelf/__init__.py` の `BUILTIN_TOOLS`) と K2 の 10 (`hiraku/solvers/k2_vehicle/shelf/__init__.py` の `TOOLS`)。数は `hiraku.solvers.builtin_tools()` の長さ、2026-11-06)。どれも答えの分かっている例と照らし合わせてから使う。手計算の式 (`k0_handcalc.library.all_formulas()`) は単体の試験で確かめている (実行の中での照らし合わせは無い)。感度 (K5) は棚の 17 のツールのどれにも `params.sensitivity` で付く (`hiraku/solvers/shelf/base.py` の `BuiltinTool.run`。K2 の 10 には付かない)。感度の試験があるのは frame_static だけ
- 試験ごとの荷重条件の作り方は、P1・R1 を含めて 20 試験ぶんある (衝突・疲労・電力・熱・組み立て・傾斜台・横風・レーンチェンジ・制動・発進・凸凹の横の坂 など)。要件のファイルに並べれば実行に乗る
- 実行の記録 (`runs/<id>/events.jsonl`) を再生する画面「③ 解析」(3D の応力の色・矢印・試験の場面・走行の再生) と、分野ごとの判定のタイル
- 画面から実行を始める口。一覧「HIRAKU でできること」の行ごとに、走らせ方を選ぶ: 「見る」(記録を再生する。無料)、「再現する」(決まった手順の運転。無料、Claude を呼ばない)、「実行する」(Claude で新しく実行する。Anthropic の API キーが要り、行ごとの費用の上限で止まる)。「試す」は、その依頼の文を入れた依頼の画面を開く。「自分で頼む」で書いた依頼は、ヒアリングで要件がそろえば、そこから始められる。「実行する」は、HIRAKU を起こした窓に `ANTHROPIC_API_KEY` があるときだけ押せて、費用の上限を見せて確かめてから始まる。画面から始めた実行は画面から止められる (`web/js/app.js`、`hiraku/webserve/launch.py`)

まだのもの: 20 試験ぶんを全部まとめて回すこと、実行の最後の報告 (`write_report`) を読む画面。

## 主張と確かめる場所

`showcase/` の 4 つの実行のうち、Claude が回したのは `20261105-160725-offroad-4w`・`20261105-094333-offroad-4w`・`20261105-091217-offroad-4w` (どれも Claude Code のプラグイン。`project.json` の `driver` が `claude_code_plugin`)。`20261105-150416-offroad-4w-scripted` は台本の運転です (`driver` が `scripted`。Claude を呼ばない)。下の表では実行を名前の真ん中 (160725 など) で書き、seq は `events.jsonl` の event の番号です。

| 主張 | 記録 (`showcase/`) | コード | 試験 |
|---|---|---|---|
| 解析の前に、手計算で予測する | 160725 (Claude): R4-rops の予測 pred-0029〜0032 (seq 261〜264) のあとに、解析 ana-0022 (seq 266) | `hiraku/tools/rules.py` の `check_analyze` (HK1)、`hiraku/tools/functions.py` の `_gate` | `tests/tools/test_functions.py` の `test_hk1_analyze_needs_a_prediction_of_this_load_case` |
| hooks が実行を止める | Claude の 3 つの実行で止めたのは HK3・HK5・HK6 (`hook.denied`) と HK4 (終わりを断る `hook.blocked_stop`)。160725 では HK5 が seq 260・436、HK4 が seq 276・277、HK3 が seq 953。「予測が無い」の HK1 で止めた例は、Claude の実行の記録には無い。台本の 150416 は、わざと予測の前に `analyze` を呼んで HK1 を見せる (seq 50。`data/catalog/scripted.yaml` の `show_hk1`) | `hiraku/tools/rules.py`、`hiraku/agent/hooks.py`、`plugins/hiraku/hooks/hooks.json` | `tests/agent/test_hooks.py` の `test_hk1_denial_is_recorded_and_explained`、`tests/agent/test_plugin_path.py` の `test_hk1_deny_as_the_sdk_hook_and_recorded_by_the_server` |
| Claude が実行の中でツールを書き、確かめてから使った | 160725: `tools/rops_pushover-1.py` と `tools/rops_pushover-1.check.json`。`tool.checked` (seq 265、cae.strength、使える) のあと、ana-0022 (seq 266) で使った。094333 の `tools/` にも 2 つ | `hiraku/tools/functions.py` の `check_tool`・`make_tool`、HK2 (`rules.py`) | `tests/tools/test_tool_template.py` の `test_the_example_passes_check_tool` |
| 設計を、解析の結果から v1 → v7 と直した | 160725: `design.created` が 7 つ (v7 は seq 966)。v7 の `module_changes` は、v6 の R1-step100 の NG (ver-0077、シェルの解析 ana-0083) に答えて `rail_t` を 2.3 → 3.2 mm にした。v ごとの NG は [showcase/README.md](showcase/README.md) | `hiraku/tools/functions.py` の `revise_design`・`build_version`、`hiraku/designgen/` | `tests/tools/test_functions.py` の `test_revise_design_builds_the_load_cases_again_for_the_new_version` |
| CAE の役は、作るものに合わせて決まる | 160725: 選んだ分野は強度信頼性と組み立て (`domains.picked`、seq 1)、CAE 役は `cae.strength` だけ (`project.json` の `agents`)。保存した実行はどれもオフロードの車で、ほかの製品の記録は `showcase/` に無い | `hiraku/intake/domains.py` の `pick_domains`、`hiraku/catalog/project.py` の `agents_for` | `tests/intake/test_domains.py` の `test_which_domains` (ギターは強度信頼性と音振、介護ベッドは 5 分野) |
| 数はツールが出す | 160725 の `report.json`: 分野ごとの数はコードが記録から入れ、Claude が書いたのは `summary` と `next` だけ | `hiraku/agent/prompts.py` の `COMMON_RULES`、`hiraku/tools/report.py` の `unknown_numbers` (記録に無い数のある報告を断る) | `tests/tools/test_report.py` の `test_numbers_with_units_must_be_recorded` |

## 中身

- `hiraku/agent/` エージェントのループ・hooks・役 (まとめ役・設計役・パッケージ役・分野ごとの CAE 役)、`plugins/hiraku/` skills
- `hiraku/tools/` エージェントが呼ぶツール (MCP)。数値はツールが計算し、モデルには書かせない
- `hiraku/solvers/` 手計算の式 (K0) と CAE の土台 (K1 梁の有限要素・K2 3D の車両の物理・K3 電力と熱・K4 幾何と干渉・K5 感度)
- `hiraku/intake/` 受付 (分野を選ぶ・ヒアリング・ナレッジの取り込み・規則と系の点検)
- `hiraku/catalog/` 要件から試験の荷重条件を作る、`hiraku/designgen/` 設計の変数から設計を組む
- `hiraku/store/` 実行の記録、`hiraku/contract/` 記録の形 (JSON Schema)、`web/` 画面、`data/` 要件・試験・デモの車両
- `tests/`、`qa/` 照らし合わせの試験と記録の点検、`promo/` 動画だけの画面と書き出し

部署ごとに分けて作った方法と、記録の形は [docs/contract.md](docs/contract.md)。

## Claude の使い方

### エージェントのループ (Claude Agent SDK)

- 実行は `python -m hiraku run <要件のファイル>` で始まる (`hiraku/__main__.py`)。要件のファイルから荷重条件と実行のフォルダを作り、設計の v1 とツールの棚の確かめまでを済ませる (`hiraku/agent/session.py` の `start_run`)。要件に `design_file` が無いときはv0 から始め、設計役が `propose_design` で v1 を作る (同)。
- 役は 5 種類ある (設計がモジュールに分かれているときは、設計役がモジュールごとに 1 人になる) (`hiraku/agent/roles.py`、`hiraku/agent/prompts.py`)。
  - まとめ役 (lead): メインのエージェント。仕事を役に渡し、判定を確かめ、最後に `write_report` を呼ぶ (`prompts.py` の `LEAD_PROMPT`)。
  - 分野ごとの CAE 役 (`cae-<分野>`): SDK のサブエージェント。effort は medium (`roles.py` の `CAE_EFFORT`)。
  - パッケージ役 (`packager`): 組み立ての荷重条件を受け持つ。ナレッジの規則を当てる `rule_check` も使う (`roles.py` の `PACKAGER_TOOLS`)。 設計がモジュールに分かれているときは、`build_version` で、その回の提案を 1 つの v にまとめる (`roles.py`)。
  - 設計役 (`designer`): `propose_design` と `revise_design` で設計の v を作る。effort は high (`roles.py`)。
  - モジュールの設計役 (`designer.<モジュール>`): 設計がモジュールに分かれているときの設計役で、モジュールごとに 1 人。自分のモジュールの変数か選択を `revise_design` (module つき) で直す提案を出し、v は作らない。直せないものはパッケージ役に渡す (`roles.py`、skill は `revise-a-module`)。
- サブエージェントはどれも前景で動く (`background=False`)。まとめ役はその報告を待つ (`roles.py`、`runner.py` の `FOREGROUND_ROLES_ENV`)。
- 荷重条件 1 つの進め方は CAE 役の指示に書いてある (`prompts.py` の `cae_prompt`)。
  1. `get_context` で荷重条件と設計の値を読む。
  2. `list_tools` と `list_formulas` でツールと式を選ぶ。
  3. `record_prediction` で手計算の予測を記録する。
  4. `analyze` で解析する。
  5. `compare` で予測と比べて判定する。
  6. 不一致なら `record_cause` に原因と直し方を書き、予測を記録し直してやり直す。
- NG の分野が残り、直す回数 (`round_limit`) も残っていれば、まとめ役が設計役に渡す。新しい v では、全部の荷重条件をやり直す (`LEAD_PROMPT` の 4、`hiraku/agent/hooks.py` の `next_step`)。
- 使える組み込みのツールは `Agent` と `Skill` だけ。`permission_mode="dontAsk"` で、名前を並べたツールのほかは止まる (`hiraku/agent/runner.py` の `build_options`)。
- この PC の CLAUDE.md・設定・hooks は読まない (`setting_sources=[]`)。MCP サーバーは `hiraku` だけ (`strict_mcp_config=True`) (同)。

### hooks

hooks の規則は HK1〜HK6 の 6 つ (`hiraku/contract/protocols.py` の `HOOK_RULES`、`docs/contract.md`)。中身は `hiraku/tools/rules.py`、SDK への組み込みは `hiraku/agent/hooks.py`、Claude Code のプラグインでのつなぎは [plugins/hiraku/hooks/hooks.json](plugins/hiraku/hooks/hooks.json)。

なぜ要ったか:
- 予測を解析のあとに書けば、結果に合わせた予測になる。手計算で先に予測し、それと比べるのが確かめの芯なので、順番をプロンプトに頼まずに止める (HK1)。
- 判定がそろう前に終わらせない (HK4)。ただし数え方も要った: 2026-11-03 01:31 の本物の運転 (API キー) で、まとめ役が役を後ろで走らせたまま「報告を待ちます」と番を終えるたびに HK4 が数え、3 回で上限に達して、役がまだ動いている 71 秒で打ち切られた。役が走っている間は数えないようにした。
- レイアウトや軽い計算で直しが要る v に重い有限要素を回すと、時間が無駄になり、数も動く。軽い段から確かめ、NG が出たらそこで設計を直す順番にした (HK5、作者の決まり)。

| ID | hook | 止めるもの・守らせるもの |
|---|---|---|
| HK1 | PreToolUse | 同じ v・同じ荷重条件の予測が無い `analyze` を止める (`rules.py` の `check_analyze`) |
| HK2 | PreToolUse | 答えの分かっている例に通っていないツールの `analyze` を止める (同) |
| HK3 | PreToolUse | 原因の書かれていない不一致がある分野では、`analyze` と `compare` を止める (`rules.py` の `_h3`) |
| HK4 | Stop / SubagentStop | 選んだ分野に判定の無いものが残っている間は、終わらせない。止めた回数には上限がある (まとめ役 3 回、役ごとに 2 回。`hooks.py` の `MAX_STOP_BLOCKS`・`MAX_SUBAGENT_STOP_BLOCKS`) |
| HK5 | PreToolUse | 軽い計算から確かめる。その v で、低い段 (0 レイアウトと規則・1 軽い計算・2 車の動き・3 重い有限要素) のツールで確かめる荷重条件に NG か未判定が残る間は、高い段の `analyze` を止める。直す回数を使い切ったときは、高い段を回さずに終わる |
| HK6 | PreToolUse | 設計がモジュールに分かれているとき、`revise_design` は module を付けて、そのモジュールの設計役だけが、自分のモジュールの中だけを変える提案として出す。レイアウトの変数・空間・取付点を変えるのはパッケージ役の `build_version` だけ (`rules.py` の HK6) |

- 止めたときは `hook.denied` か `hook.blocked_stop` の event を出す。理由の文には「足りないもの」と「次に呼ぶもの」を書き、それが Claude に返る (`hooks.py`、`rules.py` の冒頭)。
- ツールの関数も、同じ確かめをもう一度する。だから hook を通らずに呼んでも、規則は抜けない (`rules.py` の冒頭、`hiraku/tools/functions.py` の `_gate`)。
- PostToolUse では、次にすることを `additionalContext` で渡す。`analyze` のあとは `compare` を、不一致のあとは `record_cause` を促す (`hooks.py` の `next_step`)。
- 止めるボタンを押すと、実行のフォルダに `stop.request` ができる。そのあとはツールの呼び出しを全部止め、終わる (`hooks.py` の `pre_tool_use`・`stop`)。
- SubagentStart と SubagentStop では `agent.started` と `agent.stopped` を出す (`hooks.py`)。

### MCP のツール (`hiraku/tools/`)

サーバーの名前は `hiraku`。ツールの名前は、API キーの道では `mcp__hiraku__<名前>`、プラグインの道では `mcp__plugin_hiraku_hiraku__<名前>` (`hiraku/agent/hooks.py` の `PREFIX`・`PLUGIN_PREFIX`)。並びは `hiraku/tools/functions.py` の `RUN_TOOLS`、説明は `mcp.py` の `DESCRIPTIONS` と `hiraku/intake/tools.py` の `TOOLS` にある。プラグインの道には、ほかに `start_run` (実行を始める)・`open_run` (走っている実行を開き直す)・`wait_for_roles` (役が終わるのを待つ) がある (`hiraku/agent/plugin.py` の `OWN_TOOLS`)。

- 読むツール
  - `get_context`: 実行の今の様子。要る部分だけを `sections` で選ぶ。
  - `list_formulas`: 手計算 (K0) の式の一覧。
  - `list_tools`: 解析のツールの棚。`usable` が true のツールだけを使える。
- 予測・解析・判定
  - `record_prediction`: 式と入力と前提を選ぶ。値は K0 が計算する。
  - `analyze`: 全部の解析のツールをこの 1 つから呼ぶ。
  - `compare`: 予測と解析を比べる。予想通りで基準があれば、OK か NG かも出す。
  - `record_cause`: 不一致の原因と直し方を書く。分野の止まりがこれで解ける。
- 設計 (設計役だけ)
  - `propose_design`: v0 から最初の v (v1) を作る。
  - `revise_design`: 設計の変数を変えて、次の設計を作る。モジュールに分かれた設計では、module をつけて提案として記録する (v は作らない)。
  - `build_version`: パッケージ役だけが使う (モジュールに分かれた設計)。その回の提案を全部まとめ、次の v を組み直して記録する。
- ツールを作る
  - `get_tool_template`: ツールを書くときの見本。強度のツール 1 つ分の通して動くコードと、決まりの説明。
  - `check_tool`: Claude が書いたツールのコードを、別のプロセスで、答えの分かっている例と比べて確かめる。
  - `make_tool`: 確かめに通ったツールを棚に入れる。
- 報告
  - `write_report`: まとめ役が最後に 1 回呼ぶ。文の中の「数 + 単位」が記録と合わなければ断る。
- 受付 (`hiraku/intake/tools.py`)
  - `pick_domains`: 分野を選ぶ。分野と試験はカタログからコードが決める。
  - `ask_user`: 足りない要件をまとめて 1 回聞く。
  - `import_knowledge`: 渡されたファイルを規則にする。
  - `rule_check`: 規則を設計の部品に当てる。
  - `find_tradeoffs`: 設計の変数を動かすと、1 つの結果は良くなり別の結果は悪くなる組を、感度の記録 (K5) から探す。

### Skills と plugin (`plugins/hiraku/`)

- ローカルの plugin `hiraku` が 1 つある (`plugins/hiraku/.claude-plugin/plugin.json`)。実行で読む skill はこの plugin のものだけ (`runner.py` の `plugins`・`skills`、`roles.py` の `plugin_skills`)。
- skill は 4 つ。
  - `take-the-request`: まとめ役が依頼を受ける手順。分野を選び、要件を聞き、ナレッジを規則にし、規則を当てる。途中で届く伝言の受け方も書いてある (`plugins/hiraku/skills/take-the-request/SKILL.md`)。
  - `predict-then-analyze`: 荷重条件 1 つを、予測 → 解析 → 比べる → 判定 (OK か NG) まで進める手順 (`.../predict-then-analyze/SKILL.md`)。
  - `find-the-cause`: 不一致のときに原因を探し、`record_cause` に書く手順 (`.../find-the-cause/SKILL.md`)。
  - `revise-a-module`: 設計がモジュールに分かれているとき、モジュールの設計役が、NG の荷重条件を読んで自分のモジュールの直しを提案し、直せないものをパッケージ役に渡す手順 (`.../revise-a-module/SKILL.md`)。

### 台本の運転と Claude の運転

- 台本の運転 (`--scripted`) は Claude を呼ばない。同じツールの関数と hooks の規則を、決まった順に呼ぶ。順は `data/catalog/scripted.yaml` と試験ごとの作り方にある (`hiraku/agent/scripted.py` の冒頭、`hiraku/__main__.py`)。
  - 台本は原因を調べられない。原因を書くのは、台本が名指しした不一致のときだけ。ほかの不一致が出たら実行を止める (`scripted.py` の冒頭)。
  - 費用は 0 と出す (`__main__.py` の `_print_end`)。
- Claude の運転は、その窓の環境変数 `ANTHROPIC_API_KEY` を要る。無ければ、実行のフォルダを作る前に止まる (`runner.py` の `require_api_key`、`__main__.py`)。
  - 費用の上限は、要件のファイルの `cost_limit_usd` から取る。`--budget` で変えられる (`runner.py` の `build_options`、`__main__.py`)。
  - 既定の値は、モデル `claude-opus-5-5`、effort `high`、ターンの上限 200 (`runner.py`)。
  - 上限で止まったら、`stop_code` を `cost_limit` か `turn_limit` にする (`runner.py` の `final_status`)。
- 記録はどちらの運転でも同じ形になる。`runs/<run_id>/events.jsonl` に 1 行 1 event を足していく。書くのは実行のプロセスだけ (`docs/contract.md`、`hiraku/contract/layout.py` の冒頭)。
- 画面は実行中も再生も、`events.jsonl` だけから描く (`docs/contract.md`)。
  - `hiraku.webserve` が `events.jsonl` をバイトの位置で読む (コミット 66b6f23)。
  - 画面から実行への伝言は、実行のフォルダの `inbox/` を通す (`docs/contract.md`、`hiraku/agent/inbox.py`)。
- `python -m hiraku check <run>` で、event の形・`seq` の抜け・時刻の戻りを確かめられる (`__main__.py` の `_cmd_check`)。

### 数がどこから出るか

- 予測の値は K0 が計算し、Claude は式と入力を選ぶだけ (`docs/contract.md` の、1 つの数字を信用して判定するまでの節、`mcp.py` の `record_prediction` の説明)。
- 予測と解析の差を許す幅は荷重条件に入っていて、モデルは決めない。OK と NG はツールの中でだけ作る (`docs/contract.md`)。
- Claude が実行の中で作ったツールは、答えの分かっている例に全部通ったときだけ棚に入る (`mcp.py` の `make_tool` の説明、`docs/contract.md`)。

## どう作ったか

### Claude Code のセッションを部署に分けた

作者は CAE の実務者です。作者が決め、Claude Code のセッションが書きました。

- Claude Code のセッションを「部署」として並べて作った。統括部 (契約を書き、成果を確かめて main に取り込む)・設計部・解析部 (中に K2 班)・画面部・品質保証部・広報部・受付部と、プラグインの道で HIRAKU を回して困った所を集める実験部 (コードは書かない)。部署ごとに git worktree と branch を持つ (`docs/contract.md` の冒頭)。
- 始めは部署ごとの branch (`claude/dept-*`、`origin` にも push してある) で作り、部署の branch から main に入れた。
- 2026-11-06 からは、1 つの機能を 1 本の branch (`claude/<部署>-<機能>`、main から切る) で作る。品質保証部が、通った機能の branch を 1 本ずつ main の上に重ねて試し、通れば main を進める。1 本が落ちても、ほかは待たない (`docs/contract.md`)。merge のコミットは「Merge <branch> <コミット> into the trial」の形で残る (例: bc58c4b「Merge intake-material-cards 5e0a8379 into the trial」)。

### 契約と持ち場

- 部署どうしの決まりは `docs/contract.md`。形の正本は `hiraku/contract/hiraku.schema.json` で、食い違ったら schema が正しい (`docs/contract.md`)。
- 契約の中身: 記録の形、`runs/<run_id>/` の木、event の種類、MCP のツールの入力の形、hooks の規則 (`docs/contract.md`、`hiraku/contract/layout.py`、`hiraku/contract/protocols.py`)。
- 持ち場は `hiraku/contract/layout.py` の `OWNERS` で、`owner_of(path)` で引ける。部署は自分の持ち場だけを書く。ほかは統括部を通す (同ファイルのコメント)。
  - lead (統括部): `hiraku/contract/`、`docs/`、`README.md`、`CLAUDE.md` など
  - design (設計部): `hiraku/agent/`・`hiraku/tools/`・`hiraku/store/`・`plugins/` など
  - analysis (解析部): `hiraku/solvers/`・`hiraku/designgen/` など
  - screen (画面部): `web/`・`hiraku/webserve/`
  - qa (品質保証部): `tests/verification/`・`qa/`
  - promo (広報部): `promo/`
  - intake (受付部): `hiraku/intake/` など
- パッケージの層の順も決めてあり、下の層は上の層を import しない。`tests/contract/test_layers.py` がそれを確かめる (`docs/contract.md`)。
- 契約は 2 つ目のコミットで入った (98aafe2「Add the contract: record schemas, run folder layout, event kinds, folder owners」)。

### main に入れる前に全部の試験を通す

- `CLAUDE.md` に決まりがある。main に入れる前に、通常の試験と遅い試験 (`HIRAKU_SLOW_TESTS=1`) を全部通す。契約だけの変更でも、部署の成果の取り込みでも同じ。回すのは `python -m unittest` か `python -m qa.run_tests`。ブラウザの試験 (`python -m tests.web.browser`) は、その変更が触れたものを使う試験を通す。提出の前の最後の取り込みは、ブラウザの試験も全部を通す。通らないものは main に入れない。
- `qa/run_tests.py` は、`python -m unittest` と同じ試験を、試験のクラスごとに別のプロセスで並べて回す。見つけた試験が全部、回ったか飛ばされたかを照らし合わせる。合わなければ終了コードを 1 にする (`qa/run_tests.py` の冒頭)。
  - 入ったコミット: c099143「Merge claude/dept-qa-ba0596: qa.run_tests runs the suite in parallel processes」、a25d42e「qa.run_tests (from QA), CLAUDE.md allows it」。
- 重い試験は `HIRAKU_SLOW_TESTS` を付けたときだけ回る。merge の前には、台本の運転を 1 回回す (550b5e8「one scripted run before every merge, the rest behind HIRAKU_SLOW_TESTS」)。
- 試験の数
  - `git grep "def test_" -- "tests/**/*.py"` は 2,969 行、`git grep -c` で 255 ファイル (2026-11-06。`tests/verification/test_run_tests.py` が一時の試験を書くための見本の行も入る)。
  - この数には、条件を付けたときだけ回るものも入っている。例: ブラウザの試験は `python -m tests.web.browser` のときだけ回る。
  - 実際に回る数は、`python -m unittest` か `python -m qa.run_tests` が出す `Ran N tests` で数える。

### Codex について

- `git log --all --grep="Drafted with Codex"` に当たるコミットは 0 件。
- `codex` という字を含むコミット (大文字小文字を区別しない) は 2 件ある。35975dd「first_look: cuts and screens seen as a first-time viewer, judged by an image model」と c22c311「first_look: say plainly when the Codex CLI is not installed」。
  - 中身は `qa/first_look.py`。画面と動画の静止画を、見る人の目で Codex CLI に評価させる品質保証部のツール。
  - このコミットにも `Co-Authored-By: Claude` の行がある。
- 遅い試験は 2026-11-05 から EC2 の上で回している。EC2 の用意と試験の起動は Codex のセッションが行い、結果を統括部と品質保証部に返す。
- 全部の branch のコミット 2,177 件のうち、1,652 件に `Co-Authored-By: Claude` の行がある (2026-11-06。`git rev-list --branches --remotes --count`、同じく `--grep="Co-Authored-By: Claude"` を付けて)。

## 期間中に作ったもの

このリポジトリのコードは、すべて 2026-11-02 12:00 (JST) 以降に書いたものです。

確かめ方 (どれも GitHub とこのリポジトリの記録から見られます):

- リポジトリを作ったのは 2026-11-02 18:51 (JST)。最初のコミットは同じ日の 18:45 で、それより前のコミットはありません
- GitHub の Activity (リポジトリの Insights の Activity、または `gh api repos/Kuglof16/hiraku/activity`) に、サーバーが記録した push の時刻が並びます。コミットの日付と違い、手元では書き換えられません。force push はしていません
- 開発は Claude Code のセッションを部署に分けて並べて進めました (統括部・設計部・解析部・解析部 K2 班・画面部・品質保証部・広報部・受付部)。部署ごとの枝 `claude/dept-*` も push してあり、並行して進んだことと、統括部が試験を通してから main に入れたことが、枝と merge の履歴で見えます
- 最初の夜 (2026-11-02 の 19 時台から) は、統括部が契約を書いたあと、部署のセッションが並んで、それぞれの持ち場をまとまった単位で書きました。そのため、始めの数時間のコミットにも 1 本で 2,000〜3,000 行のものがあります。どれも `git log` で部署の枝と merge の順に追えます
- 1 つのコミットが大きいことがあります (例: 受付の画面 A1・A2・A5 で約 7,000 行)。部署のセッションが、1 つのまとまりを書いて試験を通してからコミットしたためです
- 開始前に、リポジトリの外 (本人のメモ) で計画を作りました: 作るものの仕様、要件と試験のカタログの分け方 (製品の性質のフラグと、それで決まる試験の一覧)、動画の絵コンテ。期間中のコードはこの計画に沿っていますが、開始前に書いたコードは写していません。計画の文書もリポジトリには入れていません
- 行数とコミットの数は、`git log` と `git ls-files` で誰でも数え直せます

## 前から持っていた知識と、外から持ってきたデータ

動画の N08 で言う 3 つのこと (CAE を扱えるのは専門家だけ、ライセンスに何百万円、計算の準備に数週間) は、CAE の実務者である作者の実務の経験に基づきます。調べた数ではありません。

外から持ってきた値 (規格・統計・材料・人体の寸法など) は、使った所 (データのファイルとコードの中) に出典を書いています。出典の本文をこのリポジトリで読み直していない値には "not checked" と書いています。

### 単純にした所

- 骨組みの強度 (R1・R1s) と介護ベッドの床と柵 (R5) の限度は、骨組みの部材 (デモでは全部 STKR400、降伏 245 MPa) の降伏 ÷ 安全率 (Q61) の 1 つの数で見る。ロールバーの部材のうち 3 本は STKM13A (215 MPa) で、R1 の中ではこの限度で見られる (ロールバーは R4 と荷重の通り道 (R8-stress) で、それぞれの材料で見る)。N15 の台本の運転 (20261104-015442) の記録では、その 3 本の応力はいちばん大きくて 69.45 MPa (v3、シェルの要素の応力) で、STKM13A の限度 215 ÷ 1.5 = 143.3 MPa に対して 52 % の余裕がある。部材ごとの材料で見るのは、今は R8-stress だけ (`max_utilisation`)
- サスペンションの取付点 (腕の付け根とダンパーの金具、金具とレールの溶接、ピンとボルト) は、いま判定していない。全体のシェルでは、取付の範囲 (管の壁の前後の一定の範囲。大きさは金具の大きさの仮定で、確かめていない) の要素を、金具が補強する仮定で判定から外している。取付点の判定は足しているところ。使った所: `hiraku/solvers/k1_shell/frame.py`、`hiraku/designgen/kit/mounts.py`

### 確かめていない値

次の値は、出典の本文をこのリポジトリではまだ確かめていません (確かめていない)。どれも使った所に出典を書いています。

| 値 | 出典として書いたもの | 使った所 |
|---|---|---|
| 材料のカード 8 枚 (STKR400・STKM13A・SS400・A6061-T6・HDPE・銅・8.8 のボルト・塩ビの電線) の値。座席のフォームとタイヤのゴムはカードが無い | 値ごとに出典と `checked` を書いた。読んだのは (どれも 2026-11-06): 鋼管と鋼板の降伏点・引張強さ・伸びは鉄鋼メーカーのカタログや東京都の資材の仕様書 (二次の出典。JIS の本文は読んでいない)、A6061-T6 は ASM Aerospace Specification Metals のデータシート (Aluminum Association の代表値、二次)、銅は NBS Handbook 100 の標準の銅 (抵抗率・密度・熱膨張)、8.8 のボルトは ISO 898-1:2013 の表 3 (出版元の無料の見本)、HDPE は Goodfellow の製品のページ (二次、密度と比熱だけ)、塩ビの電線は AVSS の電線の販売会社 3 社のページ (二次、80 ℃ の等級)。確かめていないのは、鋼のヤング率・ポアソン比・密度 (出典を開けていない) と、HDPE のヤング率・ポアソン比・降伏 (仮定) | `data/materials/cards/`、`data/materials/materials.json` |
| 部品の温度の上限 (電池 55 ℃・制御器 85 ℃・モーター 155 ℃) | EVE LF50K の仕様書、Curtis 1236 のマニュアル、IEC 60085 の F 種。確かめたのは 2 つ: 電池 55 ℃ は LF50K の仕様書の 3 節、制御器 85 ℃ は Curtis 1234/36/38 のマニュアルの付録 D の表 D-1 (放熱板の温度で出力を絞り始める温度)。モーター 155 ℃ は IEC 60085 を読めず、Wikipedia だけ | `hiraku/designgen/ladder_4w.py` |
| 空気の密度 1.225 kg/m³ | ICAO Doc 7488 / ISO 2533 の海面の値 | `hiraku/designgen/ladder_4w.py`、`hiraku/solvers/k0_handcalc/library_dynamics.py`、`hiraku/solvers/k2_vehicle/` |
| 疲労の強さ (FAT 71・傾き 3・折れ点 1e7 回) | 傾き 3 と折れ点 1e7 は IIW の疲労設計の推奨 (Hobbacher 2016) の形 (本文は確かめていない)。FAT 71 は仮定。折れ点の後の傾き 2·m − 1 は下の「確かめた値」の表 | `hiraku/designgen/ladder_4w.py`、`hiraku/solvers/k0_handcalc/library_fatigue.py`、`hiraku/solvers/shelf/fatigue.py` |
| 路面の粗さ (ISO 8608 の C 級、G_d(n0) = 256×10⁻⁶ m³) | ISO 8608 の表 | `hiraku/solvers/k2_vehicle/road.py`・`shelf/offroad.py`、`hiraku/solvers/shelf/fatigue.py` |
| 溶接のホットスポット応力 (溶接の線から板厚 t の 0.5 倍と 1.5 倍の表面の応力を、σ = 1.5σ(0.5t) − 0.5σ(1.5t) で線まで外挿) | IIW の疲労設計の推奨 (Hobbacher 2016) | `hiraku/solvers/k1_shell/stress.py`、`hiraku/solvers/shelf/frame_shell.py` |
| タイヤの MF 6.1 の係数 (205/60R15 の乗用車の例のタイヤ、2.2 bar、98 個)。この車がはく 25x8-12 の UTV のタイヤではない。K2 は曲線の形だけを使い、荷重・半径・摩擦・剛さは設計の値に合わせ、空気圧の項は読まない | Pacejka, Tire and Vehicle Dynamics 3 版 Appendix 3 (公開の .tir から写した。.tir はリポジトリに無く、写しが元と一致するかは確かめていない。本そのものも確かめていない) | `data/tyres/pacejka_tvd3_205_60r15.json` |
| サスのアームのブッシュの剛さ (径方向 3 kN/mm・軸方向 0.5 kN/mm)、タイロッド 10 kN/mm | 乗用車のアームのブッシュの論文 2 本 (表と頁は `model.py` のコメント。径方向 1.7〜5.0 kN/mm、軸方向 0.23〜0.46 kN/mm)。UTV の値は無い。タイロッドは出典なし | `hiraku/solvers/k2_vehicle/model.py` |
| K2 の既定の値: タイヤの縦の硬さ 150 N/mm・縦の減衰比 0.05・コーナリング剛さ 8 × 輪の静荷重/rad・前後の滑りの剛さ 10 × 輪の静荷重・緩和長 = 車輪の半径・摩擦 0.8・転がり抵抗 0.015・サスの減衰比 0.3・ストローク 100 mm・バンプストップ 10 倍・ばね下 5 %・最大の切れ角 35°。駆動の効率 0.9 | 出典なし (仮定)。設計か荷重条件に値が無いときだけ使い、解析の記録の前提に書く | `hiraku/solvers/k2_vehicle/model.py` の `DEFAULTS`、`k2_vehicle/shelf/base.py` |
| 試験の速さ: S4 の制動の初めの速さ 35 km/h、S5 の 20 km/h | 細目告示 第 171 条 第 5 項の制動の表 (この車の種類の表ではなく借用)、林道規程 (3 級の林道の設計速度)。どちらも確かめていない | `hiraku/catalog/builders/k2_tests.py` |
| 限界の外の試験: 限界の 10 % 先、砂利の摩擦 0.3、坂を 12 s かけて傾ける、タイヤを摩擦 2 で止める | 仮定 (HIRAKU の選び) | `hiraku/catalog/builders/k2_tests.py` |
| 標準の重力 9.80665 m/s² | 定義値 (出典の本文は確かめていない) | `hiraku/solvers/k2_vehicle/units.py` |
| 衝突の手計算: 管がつぶれる平均の力 (角パイプ Wierzbicki・Abramowicz 1983、丸パイプ Alexander 1960)、つぶれる所が使える長さ (その長さの 70 %、残りは畳まれた折り目の厚み) | 管の式の論文は本文を確かめていない。70 % は出典なし (HIRAKU の仮定) | `hiraku/solvers/k0_handcalc/library_crash.py` |
| 要件の値 (摩擦係数・坂・風・停止距離・倒れない角度・航続 など) | 各項目の `basis` (林道規程・気象庁・細目告示からの借用・CPSC の職員の説明資料 (staff briefing、2016-11-22)・仮定)。確かめたのは、坂 14 % (林道規程 第 20 条 第 4 項)、風 30 m/s (気象庁「風の強さと吹き方」の表と注 2)、14 m と 20 % (細目告示 第 171 条 第 5 項 第 3 号・第 5 号。14 m は最高速度 35〜80 km/h の行を借りた)、倒れない角度 24° とその元の 33° (CPSC の 2016 年の説明資料の PDF 11 ページ)、体重 76.6 kg (AIST 1991-92 の N1 体重、若年男性の 95 パーセンタイル。表を 2026-11-03 と 2026-11-06 に読んだ)。確かめていないのは、路面の C 級の幅 (二次の出典だけ)。「空車で 35°」は出典が見つからない (前に「CPSC (2020)」と書いたが、その資料は見つからない) | `data/requirements/offroad-4w.yaml` |
| 工具の開口 200×125 mm | MIL-STD-1472H の図 57 | `hiraku/solvers/k4_geometry/assembly.py` |
| 製品ごとの要件の値 (ギター・介護ベッド・配膳ロボ) | 各項目の `basis` (JIS T 9254 の写し・アクチュエーターの仕様書・FDA の指針・配膳ロボの製品の仕様・ISO/TS 15066 を引いた論文・弦の張力の表・仮定。URL つき)。確かめたのは、介護ベッドの JIS T 9254 の項目 (IPX4、1350 N、70°、安全率 4、繰り返しの回数、答えの見本の 1700 N)・LINAK LA31 の 40 ℃・FDA の 2006 年の指針の隙間の区域、ギターの 456 N (EXL110 の表の和をコードで計算)、配膳ロボの階 (JARI)・こう配 1/12 (バリアフリー法施行令 第 19 条)・1/2 インチ・稼働 9〜18 時間と 4.5 時間・130/220/140/130 N (arXiv 2311.13814 の表 1)・指 25 mm (ISO 13854 の表 1)・UN 38.3・答えの見本の 40 kg と 1.2 m/s、オフロードの答えの見本の ROPS (ROHVA)。配膳ロボの通る幅 0.7 m は、引いた Keenon のページの通路の幅 (T8 0.55 m・T10 0.59 m・T9 0.70 m) の上の端。確かめていないのは、配膳ロボの ISO 13857 の年齢と ISO 13482 (検索の要約だけ)、介護ベッドの AIST の値と出典の URL が無い項目、仮定の項目 | `data/requirements/products/*.yaml`、答えの見本 `*.answers.yaml` |
| ヒアリングの既定値 (風 20 m/s・最低地上高 180 mm・体重 76.6 kg と 55 kg・身長の幅・指の隙間 25 mm など) | 気象庁「風の強さと吹き方」・EU 規則 168/2013 付属書 I・AIST 1991-92・細目告示 第 2 節・ISO 13854 (URL つき)。確かめたのは、風 20 m/s (気象庁の用語の「非常に強い風」)、最低地上高 180 mm (規則 168/2013 付属書 I の L7e-B の基準 (8))、55 kg (細目告示 第 2 条 第九号)、指の隙間 25 mm (ISO 13854 の表 1)、体重 76.6 kg (AIST の N1 の表)、身長の幅 1511.2〜1819.6 mm (AIST の B1 の表の若年女性 5 % 〜 若年男性 95 %。AIST の表はどちらも 2026-11-06 に読んだ) | `hiraku/intake/requirement_items.yaml` |
| 門形の梁の式 (ロールバーの横・後ろ・上からの力、たわみ角法と塑性ヒンジ) | 構造力学の教科書 (Heyman『Plastic Design of Frames』・Neal『The Plastic Methods of Structural Analysis』・McGuire ほか『Matrix Structural Analysis』) の方法。式はコードで組み、片持ち・両端固定などの答えの分かる形で試験した。本は開いていない | `hiraku/solvers/k0_handcalc/library_portal.py` |
| 人の体の部分の質量の割合 (頭と首 0.081・胴 0.497・上腕 0.028・前腕 0.016・手 0.006・腿 0.100・すね 0.0465・足 0.0145) | D. A. Winter『Biomechanics and Motor Control of Human Movement』4 版 表 4.1 (Dempster 1955 から) | `hiraku/designgen/kit/body.py` |
| 介護ベッドの側端の荷重の面 475×250 mm、力は面の真ん中 (縁から 125 mm)、膝のちょうつがいの位置 | JIS T 9254 (要件 Q60 が挙げる)。位置は仮定 | `hiraku/catalog/builders/product_care_bed.py` (S1) |
| 介護ベッドのサイドレールを押す力 = Q33 の半分を外向き | 仮定 (JIS T 9254 の柵の試験の力は確かめていない) | `hiraku/catalog/builders/product_care_bed.py` (R5) |
| 介護ベッドのアクチュエーターの定格 4000 N、動かす割合 10 %、連続 120 s、6 mm/s、3000 rpm | カタログの種類の値 | `hiraku/designgen/care_bed.py` (P5・T3) |
| 介護ベッドの疲労 (FAT 71、傾き 3、折れ点 1e7。使用の回数の損傷は折れ点のあとの傾きを 2·m − 1 にする)、1 回の使用で 2 ストローク、1 日の使用を 1 時間に集める | 傾き 3 と折れ点は Hobbacher 2016 (IIW) の形 (確かめていない)。2·m − 1 は下の「確かめた値」の表。FAT 71 と使い方は仮定 | `hiraku/solvers/k0_handcalc/library_bed.py` (R3・T3) |
| 介護ベッドの寸法 (床・キャスター・板)、質量 (アクチュエーター 2.5 kg・制御器 1.5 kg・ヘッドボード 5 kg・キャスター 0.8 kg)、材料 (STKR400・STKM13A・SS400) | 仮定、出典なし | `hiraku/designgen/care_bed.py` |
| 介護ベッドの手計算とツールを比べるときの許す幅 (S1 2 %、R5 20 %、P5 20 %、T3 10 %、R3 50 %) | HIRAKU の選び | `hiraku/catalog/builders/product_care_bed.py` |
| ナレッジの規則の値 (ボルトの縁端距離 1.2 d0、すみ肉溶接の長さ 30 mm か 6 倍、中空断面の b/t・h/t ≤ 35、固定の締結具の公差の式、1 人で持てる 23 kg、腰幅と座面の奥行き、指の隙間 25 mm、60 V、電池 55 ℃、角パイプの厚さ 1.6 mm から) | EN 1993-1-8・ASME Y14.5・NIOSH の持ち上げの式 (Waters ほか 1993)・AIST 1991-92・ISO 13854・UN R100・EVE LF50K の仕様書・JIS G 3466。出典のある規則には、読んだ日か「確かめていない」を書いた (試験で確かめる)。確かめたのは、縁端距離 1.2 d0 (EN 1993-1-8 の表 3.3)、溶接の長さ 30 mm か 6 倍 (同 4.5.1(2)、有効長さの規定)、角形の中空断面の ≤ 35 (同 表 7.8)、丸パイプの 10 ≤ d0/t0 ≤ 50 (同 表 7.1)、23 kg (NIOSH Pub. 94-110 に再録の Waters ほか 1993 の 6.2)、指の隙間 25 mm (ISO 13854 の表 1)、電池 55 ℃ (LF50K)、60 V (UN R100 の 2.17。R100 の言葉は "High Voltage" (直流 60 V を超える))、腰幅 391 mm と座面の奥行き 406 mm (AIST 1991-92 の J2・J6 の表、2026-11-03 に読んだ。`human.yaml`)。確かめていないのは、公差の式 (ASME を読めず二次の出典だけ)、厚さ 1.6 mm (JIS の改正案だけ読めた) | `data/knowledge/*.yaml` |
| 板金の曲げの内側の半径 ≥ 板厚 (SM-01)、穴の中心から縁 ≥ 板厚の 2 倍 (SM-02)、鋼どうしのねじのかみ合い ≥ 呼び径 (FS-02) | 設計の手引きの目安。出典を 1 つに決めて読んでいない (仮定) | `data/knowledge/sheet-metal.yaml`・`fasteners.yaml` |
| 部品が骨組みに止まっているとみなす距離 500 mm (SY-10) | HIRAKU の選び (仮の値、出典なし) | `data/knowledge/systems.yaml`、`hiraku/intake/systems.py` |
| K2 の荷重と運転の仮定: 空気抵抗の係数 0.8・横の力の係数 1.0 (荷重条件にも設計にも無いとき)、ブレーキの力の立ち上がり 0.2 s、横風の突風の立ち上がり 0.2 s、運転者の先読み 0.6 s (3 m 以上)・ハンドルを切る速さ 400 deg/s、車体が 60° 傾いたら倒れたとみなす | 0.8・1.0・ブレーキの 0.2 s は仮定 (出典なし)。突風の 0.2 s・先読みと切る速さ・60° は HIRAKU の選び (出典なし) | `hiraku/solvers/k2_vehicle/shelf/base.py`・`crosswind.py`・`braking.py`、`hiraku/solvers/k2_vehicle/drive.py` |
| サスのアームと部品の大きさ: アームの脚の丸パイプ 外径 25.4 mm・肉厚 2.0 mm、ダンパー (コイルばね) の半径 25 mm、ボールジョイントの箱 40 mm 角・0.2 kg。骨組みとの隙間を探す形では、アームの脚 25 mm の管・ダンパー 50 mm の円柱 | 仮定 (確かめていない) | `hiraku/designgen/kit/arms.py`・`kit/mounts.py`、`hiraku/designgen/space_frame_utv.py` |
| ツールの雛形の例の鋼 (ヤング率 205,000 MPa・ポアソン比 0.3・密度 7,850 kg/m³・降伏 235 MPa)。設計の材料ではない | 降伏 235 MPa は JIS G 3101 の SS400 の薄い板の最小、ほかは一般的な設計の値として書いた。どれも確かめていない (雛形の答えはこの値から計算するので、値によらず成り立つ) | `hiraku/tools/tool_template.py` |
| ISO 2867:1994 の値: 手すりと柱の間の手の空き 75 mm (表 2 C)、床が 1 m より高いときの 3 点の支え (4.7)、表 2 D | 75 mm と 4.7 は受付部が読んだと書いてあるが、解析部は確かめていない。表 2 D は確かめていない。今の版の ISO 4252:2007 と ISO 2867:2011 も確かめていない | `hiraku/designgen/ladder_4w.py`、`hiraku/catalog/builders/entry.py`、`data/requirements/offroad-4w.yaml` |

外から持ってきた画像の素材 (Poly Haven、CC0。https://polyhaven.com/license を読んで確かめた):

- 空の HDRI「Kloofendal 48d Partly Cloudy (Pure Sky)」1k (https://polyhaven.com/a/kloofendal_48d_partly_cloudy_puresky)
- 地面の質感「Dirt Floor」と「Brushed Concrete」の 1k の JPG (diff・nor_gl・arm) (https://polyhaven.com/a/dirt_floor、https://polyhaven.com/a/brushed_concrete)
- 地平の見える空の HDRI「Kloofendal 48d Partly Cloudy」1k (https://polyhaven.com/a/kloofendal_48d_partly_cloudy)
- 砂利道の質感「Gravel Road」の 1k の JPG 3 枚 (https://polyhaven.com/a/gravel_road)
- 置き場所は `web/assets/polyhaven/` (合計 10.3 MB、1 ファイルの最大 1.64 MB)。一覧はそのフォルダの `NOTES.md`

### 出典の本文を確かめた値

| 値 | 出典 | 使った所 |
|---|---|---|
| 角パイプ・丸パイプの規格の寸法 (角 112 種・丸 128 種) | JIS G 3466 の表 5・6、JIS G 3444 の表 6 (2015 年版と改正)。行の数と寸法を表の本文で確かめた (品質保証部)。2021 年と 2025 年の版は読んでいない | `data/sections/standard_tubes.json` |
| 人体の寸法 (座高・肩幅・腰幅・膝・届く距離 など) | AIST 人体寸法データベース 1991-92 の項目の表 (https://www.airc.aist.go.jp/dhrt/91-92/data/list.html、2026-11-03 と 11-04 に表で確かめた。項目の番号と読んだ日は `people.py` の `SOURCE`・`BODY_SOURCE`) | `hiraku/solvers/k4_geometry/people.py`・`assembly.py`、`data/knowledge/human.yaml` (J2・J6)、`data/requirements/offroad-4w.yaml` (Q95 の座高 985.2 mm) |
| 変動振幅の荷重で Miner の和を取るときの折れ点の後の傾き 2·m − 1 (m = 3 で 5)。22 は一定振幅のときだけ | IIW-1823-07 (2008) の 4.3.1 (品質保証部が本文を読んだ) | `hiraku/solvers/k0_handcalc/library_fatigue.py`・`library_bed.py`、`hiraku/solvers/shelf/fatigue.py` |
| 電池の電圧 51.2 V (LiFePO4 16 直列 × 3.2 V) | EVE LF50K の仕様書 (LF50K-73103 Ver. D) の 3 節の表の 2 行目、セルの公称電圧 3.2 V (https://www.rcscomponents.kiev.ua/datasheets/LF50K32V50AhProduct%20Specification.pdf、2026-11-03 に本文を確認)。16 直列はこの車の設計 | `hiraku/designgen/ladder_4w.py` |
| ダブルレーンチェンジのコース (区間 15・30・25・25・15・15 m、横 3.5 m、車線の幅 1.1 W + 0.25 m・1.2 W + 0.25 m・1.3 W + 0.25 m。W はミラーを除く車幅) | ISO 3888-1:2018 の表 1 (iTeh の公開見本 https://cdn.standards.iteh.ai/samples/67973/daa839ed4aca420788d9b4a51cd6cc95/ISO-3888-1-2018.pdf、2026-11-03 に本文を確認) | `hiraku/solvers/k2_vehicle/shelf/lane_change.py` |
| 製造業の企業のうち中小企業の割合 99 % (動画の N08b) | 中小企業庁「2025 年版 中小企業白書」付属統計資料 1 表 (産業別規模別企業数、2021 年 6 月時点) の製造業の行。企業 337,490 のうち中小企業 335,552 (99.4 %)。https://www.chusho.meti.go.jp/pamflet/hakusyo/2025/chusho/excel/f1.xlsx (2026-11-02 に表で確かめた) | `promo/screens/n08b.html` |
| 自社に CAE ツールを導入している技術者の割合: 従業員 100 人以内 16.0 %・1 万人超 67.9 % (動画の N08b) | 株式会社メイテックの発表「CAEツールの導入率は43.4％。1万人超の企業では67.9％が導入済み」(2017-09-26、「fabcross for エンジニア」調べ、製造系エンジニア 500 人のインターネットリサーチ、2017-08-25)。日刊工業新聞の企業リリース https://www.nikkan.co.jp/releases/view/1612 (2026-11-03 に原文で確かめた。日付は `promo/README.md` の出典の行)。会社の数の割合ではなく、答えた技術者の割合 | `promo/screens/n08b.html` |
| 静的な安定の基準 T/2h ≥ 1.0 (空車) | 79 FR 68981「The value of Kst for a vehicle at curb weight (without occupants) is required to be no less than 1.0.」。68982 で ROHVA 1-2011・B71.9-2012 の値とし、68968 で SSF = T/2H と定める (https://www.govinfo.gov/content/pkg/FR-2014-11-19/html/2014-26500.htm、2026-11-03 に本文を確認) | `hiraku/catalog/builders/k2_tests.py` |
| 横転の保護枠 (R4) の荷重: 横 1.75 M J・後ろ 1.4 M J・上から 20 M N、後ろの力は上の梁の幅の 1/6 の所 (M = 500 kg を入れて 875 J・700 J・10 kN)。トラクターの規格を借りたもので、この車の認証ではない | OECD Code 4 (トラクターの保護枠の静的試験、2026 年 2 月版) の 3.5.2・3.5.3・3.6 (https://www.oecd.org/content/dam/oecd/en/topics/policy-sub-issues/tractors/04-Code4-EN.pdf、2026-11-03 に本文を確認)。M = 500 kg はカタログの値から逆に出した仮定 | `hiraku/catalog/builders/analysis.py` |
| 乗員と部品に掛かる横 0.70 g・前後 0.90 g (Q75・Q76)、乗員の点 R を外へ押す力 725 N (10 s) と、そのあとの永久の横の変形 25 mm 未満 (Q77・Q78) | CPSC の規則案 79 FR 68964 の § 1422.3(e)・(b)(1)、§ 1422.5(c) (受付部が 2026-11-03 に読んだ) | `data/requirements/offroad-4w.yaml` |
| 足の操作部の力 500 N (Q68) | UN 規則 13-H 号 付属書 3 の 2.1.1・2.3.4 (50 daN、受付部が 2026-11-03 に読んだ) | `data/requirements/offroad-4w.yaml` |
| 手の操作部の力 300 N (Q66) | UN 規則 79 号 (1988 年の文) の操舵力の表 (M1、故障時 300 N)。表は読んだ (読んだ日は書いていない)。設計の荷重に使うのは仮定 | `data/requirements/offroad-4w.yaml` |
| 泥や石を放す踏面の範囲 前 30°・後ろ 50° (Q87・Q88) | 細目告示 (2023-09-22 版) 第 178 条 第 2 項 第 1 号 (読んだ。読んだ日は書いていない)。ほかの交通のための公道の決まりを借りた。乗員に当てるのは仮定 | `data/requirements/offroad-4w.yaml` |
| 乗り降りの開口・段・手すり (Q93 450・Q94 300・Q96 1250・Q97 550・Q44 300・Q45 150・Q46 150・Q48 1600・Q49 16・Q89 38、どれも mm) | ISO 4252:1983 の図 1・表 1、ISO 2867:1980 の 7.3・7.8・10.1、ISO 2867:1994 の表 1 A・1 C・2 A (受付部が 2026-11-04 に読んだ)。農業トラクターと土工機械の古い版を借りた仮定 | `data/requirements/offroad-4w.yaml`、`hiraku/catalog/builders/entry.py` |

- 動画のナレーション: 作者が自分の声で録った。下書きの段階では AivisSpeech (音声合成モデル「阿井田 茂」、制作 古山キリヲ、Aivis Common Model License (ACML) 1.0) を使ったが、提出の動画には使っていない

## ライセンス

MIT ([LICENSE](LICENSE))

使っているライブラリと素材のライセンス (Python の部品は、入れた版の中の情報から読んだ):

| 名前 | 版 | ライセンス |
|---|---|---|
| numpy | 2.4.4 | BSD-3-Clause ほか (同梱の部品の 0BSD・MIT・Zlib など) |
| scipy | 1.18.0 | BSD |
| PyNiteFEA | 3.2.0 | MIT |
| sympy | 1.14.0 | BSD |
| PyYAML | 6.0.3 | MIT |
| jsonschema | 4.26.0 | MIT |
| claude-agent-sdk | 0.2.173 | MIT |
| mcp | 2.3.0 | MIT |
| pillow | 13.0.0 | MIT-CMU |
| numba (重い繰り返しの計算を機械語に直す) | 0.68.1 | BSD |
| llvmlite (numba が使う) | 0.50.0 | BSD-2-Clause AND Apache-2.0 WITH LLVM-exception |
| pypardiso (任意) | 0.4.7 | BSD-3-Clause (中で使う Intel MKL は Intel のライセンス、確かめていない) |
| three.js (画面、jsDelivr から読む) | 0.170.0 | MIT |
| Playwright (ブラウザの試験だけ、requirements に入れていない) | — | Apache-2.0 (確かめていない) |
| Poly Haven の素材 | — | CC0 |
| BIZ UDPGothic (画面の字。同梱せず、PC にあるものを使う) | — | SIL Open Font License 1.1 (確かめていない) |

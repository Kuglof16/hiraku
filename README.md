# HIRAKU

作りたいもの (たとえば、林道を走れる小さな車) の条件を渡すと、Claude のエージェントたちが、分野ごとに「手計算で結果を予測する → CAE (コンピューターでの解析) で計算する → 予測と照らし合わせる」を進め、合わない所の設計を直していくアプリです。
計算の様子は、3D の画面で見られます。使うのは Windows の PC と Python 3.12 で、Claude を呼ばない「台本の運転」なら API キーは要りません。

HIRAKU: Claude agents predict each result by hand calculation before running the CAE analysis, check one against the other, and revise the design.

Built with Claude: Japan 2026 (2026-11-02 〜 11-08) の期間中に作っています。

## はじめに動かす

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

画面は、開くとまず、リポジトリの `showcase/` に保存した実行を再生します。何も計算せず、無料です。

- `showcase/` には 4 つの実行と、動画の N10 の場面の記録 (`n10/`) があります。何も選ばないと、いちばん新しい実行 (Claude Code のプラグインで回し、v7 で終わった `20261105-160725-offroad-4w`) が開きます。
- ほかの実行は、URL に `?run=<実行の名前>` を付けて開きます。一覧は `?list=1` です。最初のページ「HIRAKU でできること」の「まずはこれ」の「見る」でも開けます。
- それぞれの実行の中身と、見る順のおすすめは [showcase/README.md](showcase/README.md) にあります。

## 台本の運転

「台本の運転」は、Claude を呼ばずに、決まった順にツールを呼んで、設計を試験して直すところまでを再現する運転です。無料で、API キーは要りません。

1. 画面の最初のページ「HIRAKU でできること」を開きます。
2. まず短いもの: 「デモ」の「要件の書き間違いを見つける」の行の「再現する」を押します (cm と mm の書き間違いを、手計算と解析の食い違いから見つけて直す流れです)。本格的なものは、「小さなオフロード車の 11 の試験」の行の「再現する」です。
3. 押すと、ボタンが押せなくなり、画面が作業台に変わるまで **数十秒 (長くて 1 分ほど)** 何も変わりません。使うツールの確認をしている時間で、止まっているわけではありません。その間にもう一度押さないでください。
4. 作業台に変わると、実行が進みます。かかった時間 (この PC で測った値。PC やほかの仕事で変わります):
   - 「要件の書き間違いを見つける」: 約 40 秒
   - 「小さなオフロード車の 11 の試験」: 約 9 分 (526 秒。試験を同時に回していたときの値)

   途中で止めたいときは、上の「■ 止める」を押します。
5. 見る所:
   - 画面の上の「vN・繰り返し a / b 回」: 設計の版 (v1、v2 …) と、直して確かめ直した回数 (b は上限) です。
   - 作業台の右のカード: いま確かめている試験の、手計算の予測・CAE の結果・その差と判定です。
   - 左の「記録」: 実行の中で起きたことが、順に並びます。
   - 画面の下の「hooks」の並び: 実行の途中で、順番を守らせるために止めた回数が出ます (たとえば「HK1 予測が先」)。
   - ④「解析結果」のタブ: 分野ごとの判定のタイルが並びます。タイルを押すと、その分野の解析をタブの中で再生します。
   - 右上の「見せる / 作業台」: 「見せる」は 3D を大きく見せる表示、「作業台」は作業の様子を見る表示です。

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

自分の Claude Code (自分のサインインの枠) で回す道です。API キーは要りません。リポジトリのフォルダで、次のようにします。

1. 「はじめに動かす」の手順 3 まで済ませます (`.venv` が要ります)。
2. 先にキー無しで確かめます (40 秒ほど。Claude を呼ばない台本の運転です)。

   ```
   .venv\Scripts\python -m hiraku run data/requirements/offroad-4w-n12.yaml --scripted
   ```

3. Claude Code を、プラグインを指定して起こします。`--allowedTools` を付けないと、役がツールを呼ぶたびに許可を聞かれます。

   ```
   claude --plugin-dir <リポジトリ>/plugins/hiraku --allowedTools "mcp__plugin_hiraku_hiraku"
   ```

4. Claude Code の中で `/mcp` を打ち、hiraku のサーバーがつながっているか見ます。つながっていたら、`/hiraku:run offroad-4w-n12.yaml` を打ちます (最初の `start_run` は 30〜60 秒かかります)。
5. 実行の様子は、同じリポジトリのフォルダで `.venv\Scripts\python -m hiraku.webserve` を起こして、画面で見ます (画面は `./runs` を見るので、リポジトリのフォルダから起こします)。

プラグインはコピーせず、リポジトリの中から読ませます。くわしくは [plugins/hiraku/README.md](plugins/hiraku/README.md)。この道で回して v7 で終わった実行が `showcase/20261105-160725-offroad-4w` です。

## この README の言葉

| 言葉 | 意味 |
|---|---|
| 分野 | 何を確かめるかの区分: 操安・動力・空力・衝突・強度信頼性・音振・熱 の 7 つ (これとは別に、組み立てと人への当たりを見る) |
| 試験 | 分野ごとの 1 つの確かめ。P1 (段差越え)・R1 (骨組みの強度)・N1 (振動)・H1 (ハンドルを切ったときの干渉)・H3 (乗る人の体格)・P3 (発進と加速) のように、頭の字は分野 (S 操安・P 動力・F 空力・C 衝突・R 強度信頼性・N 音振・T 熱・H 組み立てと人) |
| 荷重条件 | 試験 1 つぶんの条件: 何をかけ、何を基準にし、予測と解析の差をどこまで許すか |
| 手計算 (予測) | 式で先に結果を出すこと。式は K0 の中にある |
| CAE | コンピューターで物の動きや強さを計算する解析。HIRAKU の中のツールが計算する |
| 判定 | 手計算の予測と CAE の結果を、許す幅の中で比べ、基準があれば OK か NG かを出すこと。食い違ったら原因を書くまで先へ進めない |
| 版・繰り返し | 設計の版 (v1、v2 …)。NG のとき、設計を直して次の版を作って確かめ直す。その回数が繰り返し |
| 赤・緑 | 判定が NG か OK か (画面の色) |
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

全部で 1,900 本ほどあります。全部にかかる時間は測っていません。飛ばされるものもあります (ブラウザの試験・Rapier との照合・重い試験)。

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

- デモの車両 (林道を走る 2 人乗りの 4 輪) で、要件のファイルに並べた 10 の試験 (段差越え P1・強度 R1・振動 N1・荷重の通り道 R8・操作部の荷重 R7・干渉とハンドルの切れ角 H1・乗員の体格 H3・泥のはね H6・乗り降り H7・発進 P3) を「手計算で予測 → 解析 → 照合」で回す流れ。基準のある試験は合否まで出す (P1 は基準が無く照合まで)。予測の無い解析は hooks が止め、食い違いは原因を書くまで先へ進めない。説明できて安全側と判断できる食い違いは、不利な方の値で合否を出す
- 設計役が設計の変数を変えて次の版を作る (`revise_design`)。デモの車両では、版 1 と 2 で組み立ての試験が赤、版 3 で P3 が赤になり、版 4 で全部緑になる (数は統括部が提出前に確かめる)
- 解析のツール 25 個 (梁と板 (シェル) の有限要素・3D の車両の物理・電力と熱・幾何と干渉。`hiraku.solvers.builtin_tools()` の数で、棚の 17 と K2 の 8)。どれも答えの分かっている例と照合してから使う。手計算の式 61 個 (`k0_handcalc.library.all_formulas()`) は単体の試験で確かめている (実行の中での照合は無い)。感度 (K5) は 9 つのツール (frame_static・frame_shell・step_climb・drive_energy・thermal・crash・modal・fatigue・assembly) に `params.sensitivity` で付く。感度の試験があるのは frame_static だけ
- 試験ごとの荷重条件の作り方は、P1・R1 を含めて 20 試験ぶんある (衝突・疲労・電力・熱・組み立て・傾斜台・横風・レーンチェンジ・制動・発進・凸凹の横の坂 など)。要件のファイルに並べれば実行に乗る
- 実行の記録 (`runs/<id>/events.jsonl`) を再生する作業台の画面 (3D の応力の色・矢印・試験の場面・走行の再生) と、分野ごとの判定のタイル
- 画面から実行を始める口。一覧「HIRAKU でできること」の行ごとに、走らせ方を選ぶ: 「見る」(記録を再生する。無料)、「再現する」(決まった手順の運転。無料、Claude を呼ばない)、「実行する」(Claude で新しく実行する。Anthropic の API キーが要り、行ごとの費用の上限で止まる)。「試す」は、その依頼の文を入れた依頼の画面を開く。「自分で頼む」で書いた依頼は、ヒアリングで要件がそろえば、そこから始められる。「実行する」は、HIRAKU を起こした窓に `ANTHROPIC_API_KEY` があるときだけ押せて、費用の上限を見せて確かめてから始まる。画面から始めた実行は画面から止められる (`web/js/app.js`、`hiraku/webserve/launch.py`)

まだのもの: 20 試験ぶんを全部まとめて回すこと、実行の最後の報告 (`write_report`) を読む画面。

## 中身

- `hiraku/agent/` エージェントのループ・hooks・役 (まとめ役・設計役・パッケージ役・分野ごとの CAE 役)、`plugins/hiraku/` skills
- `hiraku/tools/` エージェントが呼ぶツール (MCP)。数値はツールが計算し、モデルには書かせない
- `hiraku/solvers/` 手計算の式 (K0) と CAE の土台 (K1 梁の有限要素・K2 3D の車両の物理・K3 電力と熱・K4 幾何と干渉・K5 感度)
- `hiraku/intake/` 受付 (分野を選ぶ・ヒアリング・ナレッジの取り込み・規則と系の点検)
- `hiraku/catalog/` 要件から試験の荷重条件を作る、`hiraku/designgen/` 設計の変数から設計を組む
- `hiraku/store/` 実行の記録、`hiraku/contract/` 記録の形 (JSON Schema)、`web/` 画面、`data/` 要件・試験・デモの車両
- `tests/`、`qa/` 照合の試験と記録の点検、`promo/` 動画だけの画面と書き出し

部署ごとに分けて作った方法と、記録の形は [docs/contract.md](docs/contract.md)。

## Claude の使い方

### エージェントのループ (Claude Agent SDK)

- 実行は `python -m hiraku run <要件のファイル>` で始まる (`hiraku/__main__.py`)。要件のファイルから荷重条件と実行のフォルダを作り、設計の版 1 とツールの棚の確かめまでを済ませる (`hiraku/agent/session.py` の `start_run`)。要件に `design_file` が無いときは版 0 から始め、設計役が `propose_design` で版 1 を作る (同)。
- 役は 5 種類ある (設計がモジュールに分かれているときは、設計役がモジュールごとに 1 人になる) (`hiraku/agent/roles.py`、`hiraku/agent/prompts.py`)。
  - まとめ役 (lead): メインのエージェント。仕事を役に渡し、判定を確かめ、最後に `write_report` を呼ぶ (`prompts.py` の `LEAD_PROMPT`)。
  - 分野ごとの CAE 役 (`cae-<分野>`): SDK のサブエージェント。effort は medium (`roles.py` の `CAE_EFFORT`)。
  - パッケージ役 (`packager`): 組み立ての荷重条件を受け持つ。ナレッジの規則を当てる `rule_check` も使う (`roles.py` の `PACKAGER_TOOLS`)。 設計がモジュールに分かれているときは、`build_version` で、その回の提案を 1 つの版にまとめる (`roles.py`)。
  - 設計役 (`designer`): `propose_design` と `revise_design` で設計の版を作る。effort は high (`roles.py`)。
  - モジュールの設計役 (`designer.<モジュール>`): 設計がモジュールに分かれているときの設計役で、モジュールごとに 1 人。自分のモジュールの変数か選択を `revise_design` (module つき) で直す提案を出し、版は作らない。直せないものはパッケージ役に渡す (`roles.py`、skill は `revise-a-module`)。
- サブエージェントはどれも前景で動く (`background=False`)。まとめ役はその報告を待つ (`roles.py`、`runner.py` の `FOREGROUND_ROLES_ENV`)。
- 荷重条件 1 つの進め方は CAE 役の指示に書いてある (`prompts.py` の `cae_prompt`)。
  1. `get_context` で荷重条件と設計の値を読む。
  2. `list_tools` と `list_formulas` でツールと式を選ぶ。
  3. `record_prediction` で手計算の予測を記録する。
  4. `analyze` で解析する。
  5. `compare` で予測と照合する。
  6. 食い違ったら `record_cause` に原因と直し方を書き、予測を記録し直してやり直す。
- 赤の分野が残り、直す回数 (`round_limit`) も残っていれば、まとめ役が設計役に渡す。新しい版では、全部の荷重条件をやり直す (`LEAD_PROMPT` の 4、`hiraku/agent/hooks.py` の `next_step`)。
- 使える組み込みのツールは `Agent` と `Skill` だけ。`permission_mode="dontAsk"` で、名前を並べたツールのほかは止まる (`hiraku/agent/runner.py` の `build_options`)。
- この PC の CLAUDE.md・設定・hooks は読まない (`setting_sources=[]`)。MCP サーバーは `hiraku` だけ (`strict_mcp_config=True`) (同)。

### hooks

hooks の規則は HK1〜HK5 の 5 つ (`hiraku/contract/protocols.py` の `HOOK_RULES`、`docs/contract.md`)。中身は `hiraku/tools/rules.py`、SDK への組み込みは `hiraku/agent/hooks.py`、Claude Code のプラグインでのつなぎは [plugins/hiraku/hooks/hooks.json](plugins/hiraku/hooks/hooks.json)。

なぜ要ったか:
- 予測を解析のあとに書けば、結果に合わせた予測になる。手計算で先に予測し、それと照らし合わせるのが確かめの芯なので、順番をプロンプトに頼まずに止める (HK1)。
- 判定がそろう前に終わらせない (HK4)。ただし数え方も要った: 2026-11-03 01:31 の本物の運転 (API キー) で、まとめ役が役を後ろで走らせたまま「報告を待ちます」と番を終えるたびに HK4 が数え、3 回で上限に達して、役がまだ動いている 71 秒で打ち切られた。役が走っている間は数えないようにした。
- レイアウトや軽い計算で直しが要る版に重い有限要素を回すと、時間が無駄になり、数も動く。軽い段から確かめ、赤が出たらそこで設計を直す順番にした (HK5、作者の決まり)。

| ID | hook | 止めるもの・守らせるもの |
|---|---|---|
| HK1 | PreToolUse | 同じ版・同じ荷重条件の予測が無い `analyze` を止める (`rules.py` の `check_analyze`) |
| HK2 | PreToolUse | 答えの分かっている例に通っていないツールの `analyze` を止める (同) |
| HK3 | PreToolUse | 原因の書かれていない食い違いがある分野では、`analyze` と `compare` を止める (`rules.py` の `_h3`) |
| HK4 | Stop / SubagentStop | 選んだ分野に判定の無いものが残っている間は、終わらせない。止めた回数には上限がある (まとめ役 3 回、役ごとに 2 回。`hooks.py` の `MAX_STOP_BLOCKS`・`MAX_SUBAGENT_STOP_BLOCKS`) |
| HK5 | PreToolUse | 軽い計算から確かめる。その版で、低い段 (0 レイアウトと規則・1 軽い計算・2 車の動き・3 重い有限要素) のツールで確かめる荷重条件に赤か未判定が残る間は、高い段の `analyze` を止める。直す回数を使い切ったときは、高い段を回さずに終わる |

- 止めたときは `hook.denied` か `hook.blocked_stop` の event を出す。理由の文には「足りないもの」と「次に呼ぶもの」を書き、それが Claude に返る (`hooks.py`、`rules.py` の冒頭)。
- ツールの関数も、同じ確かめをもう一度する。だから hook を通らずに呼んでも、規則は抜けない (`rules.py` の冒頭、`hiraku/tools/functions.py` の `_gate`)。
- PostToolUse では、次にすることを `additionalContext` で渡す。`analyze` のあとは `compare` を、食い違ったあとは `record_cause` を促す (`hooks.py` の `next_step`)。
- 止めるボタンを押すと、実行のフォルダに `stop.request` ができる。そのあとはツールの呼び出しを全部止め、終わる (`hooks.py` の `pre_tool_use`・`stop`)。
- SubagentStart と SubagentStop では `agent.started` と `agent.stopped` を出す (`hooks.py`)。

### MCP のツール (`hiraku/tools/`)

サーバーの名前は `hiraku`、ツールの名前は `mcp__hiraku__<名前>` (`hiraku/tools/mcp.py`、`docs/contract.md`)。並びは `hiraku/tools/functions.py` の `RUN_TOOLS`、説明は `mcp.py` の `DESCRIPTIONS` と `hiraku/intake/tools.py` の `TOOLS` にある。

- 読むツール
  - `get_context`: 実行の今の様子。要る部分だけを `sections` で選ぶ。
  - `list_formulas`: 手計算 (K0) の式の一覧。
  - `list_tools`: 解析のツールの棚。`usable` が true のツールだけを使える。
- 予測・解析・照合
  - `record_prediction`: 式と入力と前提を選ぶ。値は K0 が計算する。
  - `analyze`: 全部の解析のツールをこの 1 つから呼ぶ。
  - `compare`: 予測と解析を照合する。一致して基準があれば、合否も作る。
  - `record_cause`: 食い違いの原因と直し方を書く。分野の止まりがこれで解ける。
- 設計 (設計役だけ)
  - `propose_design`: 版 0 から最初の版を作る。
  - `revise_design`: 設計の変数を変えて、次の版を作る。モジュールに分かれた設計では、module をつけて提案として記録する (版は作らない)。
  - `build_version`: パッケージ役だけが使う (モジュールに分かれた設計)。その回の提案を全部まとめ、次の版を組み直して記録する。
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

### Skills と plugin (`plugins/hiraku/`)

- ローカルの plugin `hiraku` が 1 つある (`plugins/hiraku/.claude-plugin/plugin.json`)。実行で読む skill はこの plugin のものだけ (`runner.py` の `plugins`・`skills`、`roles.py` の `plugin_skills`)。
- skill は 4 つ。
  - `take-the-request`: まとめ役が依頼を受ける手順。分野を選び、要件を聞き、ナレッジを規則にし、規則を当てる。途中で届く伝言の受け方も書いてある (`plugins/hiraku/skills/take-the-request/SKILL.md`)。
  - `predict-then-analyze`: 荷重条件 1 つを、予測 → 解析 → 照合 → 合否まで進める手順 (`.../predict-then-analyze/SKILL.md`)。
  - `find-the-cause`: 食い違ったときに原因を探し、`record_cause` に書く手順 (`.../find-the-cause/SKILL.md`)。
  - `revise-a-module`: 設計がモジュールに分かれているとき、モジュールの設計役が、赤の荷重条件を読んで自分のモジュールの直しを提案し、直せないものをパッケージ役に渡す手順 (`.../revise-a-module/SKILL.md`)。

### 台本の運転と Claude の運転

- 台本の運転 (`--scripted`) は Claude を呼ばない。同じツールの関数と hooks の規則を、決まった順に呼ぶ。順は `data/catalog/scripted.yaml` と試験ごとの作り方にある (`hiraku/agent/scripted.py` の冒頭、`hiraku/__main__.py`)。
  - 台本は原因を調べられない。原因を書くのは、台本が名指しした食い違いのときだけ。ほかの食い違いが出たら実行を止める (`scripted.py` の冒頭)。
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

- 役のプロンプトには「数値は全部、ツールが出す。自分で計算した数を入力にも文にも書かない。単位の換算もしない」とある (`prompts.py` の `COMMON_RULES`)。
- 予測の値は K0 が計算し、Claude は式と入力を選ぶだけ (`docs/contract.md` の「1 つの数字を信用して合否を出すまで」、`mcp.py` の `record_prediction` の説明)。
- 照合の許す幅は荷重条件に入っていて、モデルは決めない。合否はツールの中でだけ作る (`docs/contract.md`)。
- 報告の数はコードが記録から入れる。Claude が書くのは `summary` と `next` だけ (`docs/contract.md`、`mcp.py` の `write_report` の説明)。
- Claude が実行の中で作ったツールは、答えの分かっている例に全部通ったときだけ棚に入る (`mcp.py` の `make_tool` の説明、`docs/contract.md`)。

## どう作ったか

### Claude Code のセッションを部署に分けた

- Claude Code のセッションを「部署」として並べて作った。統括部・設計部・解析部 (中に K2 班)・画面部・品質保証部・広報部と、受付部がある。部署ごとに git worktree と branch を持つ (`docs/contract.md` の冒頭と持ち場の表)。
- 部署の branch (`git branch -a`)
  - `claude/dept-design-90c266`、`claude/dept-analysis-cf6cf8`、`claude/dept-k2-0c4e6b`、`claude/dept-screen-812e37`、`claude/dept-qa-ba0596`、`claude/dept-promo-f2ae3c`、`claude/dept-intake-932455`
  - この 7 つは `origin` にも push してある (`git branch -a` の `remotes/origin/...`)。
- 部署の中の小さな branch
  - 解析部: `claude/dept-analysis-assembly`・`-conv`・`-hotspot`・`-mesh`・`-parts`・`-plana`・`-rollbar`
  - 設計部: `claude/dept-design-plana`
  - 画面部: `claude/screen-a4`・`-assembly`・`-demo-runs`・`-grid`・`-n03n06`・`-n17`・`-present`・`-quality`・`-real`・`-shell`・`-story`
  - ほか: `claude/fe-core`・`claude/fe-more`・`claude/k2-spaceframe`
- 統括部の branch は `claude/lead-intake-contract` と `claude/lead-refactor` (`origin` にもある)。
- 部署の中の branch は、まず部署の branch に merge した。例: 3ba64c0「Merge branch 'claude/screen-story' into claude/dept-screen-812e37」、2e0b547「Merge branch 'claude/dept-analysis-conv' into claude/dept-analysis-cf6cf8」。そのあと main に入れた。例: ab6bdd2「Merge claude/dept-screen-812e37: ...」 (`git log --merges`)。

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

- `CLAUDE.md` に決まりがある。main に入れる前に、全部の試験を通す。契約だけの変更でも、部署の成果の取り込みでも同じ。回すのは `python -m unittest` か `python -m qa.run_tests` で、通らないものは main に入れない。台本の運転の通り道を変えたときは、`HIRAKU_SLOW_TESTS=1` を付けて 1 回回す。
- `qa/run_tests.py` は、`python -m unittest` と同じ試験を、試験のクラスごとに別のプロセスで並べて回す。見つけた試験が全部、回ったか飛ばされたかを照らし合わせる。合わなければ終了コードを 1 にする (`qa/run_tests.py` の冒頭)。
  - 入ったコミット: c099143「Merge claude/dept-qa-ba0596: qa.run_tests runs the suite in parallel processes」、a25d42e「qa.run_tests (from QA), CLAUDE.md allows it」。
- 重い試験は `HIRAKU_SLOW_TESTS` を付けたときだけ回る。merge の前には、台本の運転を 1 回回す (550b5e8「one scripted run before every merge, the rest behind HIRAKU_SLOW_TESTS」)。
- 試験の数
  - `tests/` の下の試験の関数 (`def test_`) は、149 ファイルに 1,909 個ある (全部の部署を合わせた試しの取り込み fdc7948 の時点。文字列の中にある 1 つ、`tests/verification/test_run_tests.py` が一時の試験を書くための見本は数えない)。
  - この数には、条件を付けたときだけ回るものも入っている。例: ブラウザの試験は `python -m tests.web.browser` のときだけ回る。
  - 実際に回る数は、`python -m unittest` か `python -m qa.run_tests` が出す `Ran N tests` で数える。

### Codex について

- `git log --all --grep="Drafted with Codex"` に当たるコミットは 0 件。
- `codex` という字を含むコミット (大文字小文字を区別しない) は 1 件ある。35975dd「first_look: cuts and screens seen as a first-time viewer, judged by an image model」。
  - 中身は `qa/first_look.py`。画面と動画の静止画を、見る人の目で Codex CLI に評価させる品質保証部のツール。
  - このコミットにも `Co-Authored-By: Claude` の行がある。
- 全部の branch のコミット 680 件のうち、487 件に `Co-Authored-By: Claude` の行がある (`git log --all --grep="Co-Authored-By: Claude"`)。

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

外から持ってきた値 (規格・統計・材料・人体の寸法など) は、使った所 (データのファイルとコードの中) に出典を書いています。出典の本文をこのリポジトリで読み直していない値には "not checked" と書いています。

### 単純にした所

- 骨組みの強度 (R1・R1s) と介護ベッドの床と柵 (R5) の限度は、骨組みの部材 (デモでは全部 STKR400、降伏 245 MPa) の降伏 ÷ 安全率 (Q61) の 1 つの数で見る。ロールバーの部材のうち 3 本は STKM13A (215 MPa) で、R1 の中ではこの限度で見られる (ロールバーは R4 と荷重の通り道 (R8-stress) で、それぞれの材料で見る)。N15 の台本の運転 (20261104-015442) の記録では、その 3 本の応力はいちばん大きくて 69.45 MPa (版 3、シェルの要素の応力) で、STKM13A の限度 215 ÷ 1.5 = 143.3 MPa に対して 52 % の余裕がある。部材ごとの材料で見るのは、今は R8-stress だけ (`max_utilisation`)
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
| 要件の値 (摩擦係数・坂・風・停止距離・倒れない角度・航続 など) | 各項目の `basis` (林道規程・気象庁・細目告示からの借用・CPSC の職員の説明資料 (staff briefing、2016-11-22)・仮定)。確かめたのは、坂 14 % (林道規程 第 20 条 第 4 項)、風 30 m/s (気象庁「風の強さと吹き方」の表と注 2)、14 m と 20 % (細目告示 第 171 条 第 5 項 第 3 号・第 5 号。14 m は最高速度 35〜80 km/h の行を借りた)、倒れない角度 24° とその元の 33° (CPSC の 2016 年の説明資料の PDF 11 ページ)、体重 76.6 kg (AIST 1991-92 の N1 体重、若い男性の 95 パーセンタイル、2026-11-03 に表で確かめた)。確かめていないのは、路面の C 級の幅 (二次の出典だけ)。「空車で 35°」は出典が見つからない (前に「CPSC (2020)」と書いたが、その資料は見つからない) | `data/requirements/offroad-4w.yaml` |
| 工具の開口 200×125 mm | MIL-STD-1472H の図 57 | `hiraku/solvers/k4_geometry/assembly.py` |
| 製品ごとの要件の値 (ギター・介護ベッド・配膳ロボ) | 各項目の `basis` (JIS T 9254 の写し・アクチュエーターの仕様書・FDA の指針・配膳ロボの製品の仕様・ISO/TS 15066 を引いた論文・弦の張力の表・仮定。URL つき)。確かめたのは、介護ベッドの JIS T 9254 の項目 (IPX4、1350 N、70°、安全率 4、繰り返しの回数、答えの見本の 1700 N)・LINAK LA31 の 40 ℃・FDA の 2006 年の指針の隙間の区域、ギターの 456 N (EXL110 の表の和をコードで計算)、配膳ロボの階 (JARI)・こう配 1/12 (バリアフリー法施行令 第 19 条)・1/2 インチ・稼働 9〜18 時間と 4.5 時間・130/220/140/130 N (arXiv 2311.13814 の表 1)・指 25 mm (ISO 13854 の表 1)・UN 38.3・答えの見本の 40 kg と 1.2 m/s、オフロードの答えの見本の ROPS (ROHVA)。配膳ロボの通る幅 0.7 m は、引いた Keenon のページの通路の幅 (T8 0.55 m・T10 0.59 m・T9 0.70 m) の上の端。確かめていないのは、配膳ロボの ISO 13857 の年齢と ISO 13482 (検索の要約だけ)、介護ベッドの AIST の値と出典の URL が無い項目、仮定の項目 | `data/requirements/products/*.yaml`、答えの見本 `*.answers.yaml` |
| ヒアリングの既定値 (風 20 m/s・最低地上高 180 mm・体重 76.6 kg と 55 kg・身長の幅・指の隙間 25 mm など) | 気象庁「風の強さと吹き方」・EU 規則 168/2013 付属書 I・AIST 1991-92・細目告示 第 2 節・ISO 13854 (URL つき)。確かめたのは、風 20 m/s (気象庁の用語の「非常に強い風」)、最低地上高 180 mm (規則 168/2013 付属書 I の L7e-B の基準 (8))、55 kg (細目告示 第 2 条 第九号)、指の隙間 25 mm (ISO 13854 の表 1)。体重 76.6 kg と身長の幅は、AIST の統計を読めず確かめていない | `hiraku/intake/requirement_items.yaml` |
| 門形の梁の式 (ロールバーの横・後ろ・上からの力、たわみ角法と塑性ヒンジ) | 構造力学の教科書 (Heyman『Plastic Design of Frames』・Neal『The Plastic Methods of Structural Analysis』・McGuire ほか『Matrix Structural Analysis』) の方法。式はコードで組み、片持ち・両端固定などの答えの分かる形で試験した。本は開いていない | `hiraku/solvers/k0_handcalc/library_portal.py` |
| 人の体の部分の質量の割合 (頭と首 0.081・胴 0.497・上腕 0.028・前腕 0.016・手 0.006・腿 0.100・すね 0.0465・足 0.0145) | D. A. Winter『Biomechanics and Motor Control of Human Movement』4 版 表 4.1 (Dempster 1955 から) | `hiraku/designgen/kit/body.py` |
| 介護ベッドの側端の荷重の面 475×250 mm、力は面の真ん中 (縁から 125 mm)、膝のちょうつがいの位置 | JIS T 9254 (要件 Q60 が挙げる)。位置は仮定 | `hiraku/catalog/builders/product_care_bed.py` (S1) |
| 介護ベッドのサイドレールを押す力 = Q33 の半分を外向き | 仮定 (JIS T 9254 の柵の試験の力は確かめていない) | `hiraku/catalog/builders/product_care_bed.py` (R5) |
| 介護ベッドのアクチュエーターの定格 4000 N、動かす割合 10 %、連続 120 s、6 mm/s、3000 rpm | カタログの種類の値 | `hiraku/designgen/care_bed.py` (P5・T3) |
| 介護ベッドの疲労 (FAT 71、傾き 3、折れ点 1e7。使用の回数の損傷は折れ点のあとの傾きを 2·m − 1 にする)、1 回の使用で 2 ストローク、1 日の使用を 1 時間に集める | 傾き 3 と折れ点は Hobbacher 2016 (IIW) の形 (確かめていない)。2·m − 1 は下の「確かめた値」の表。FAT 71 と使い方は仮定 | `hiraku/solvers/k0_handcalc/library_bed.py` (R3・T3) |
| 介護ベッドの寸法 (床・キャスター・板)、質量 (アクチュエーター 2.5 kg・制御器 1.5 kg・ヘッドボード 5 kg・キャスター 0.8 kg)、材料 (STKR400・STKM13A・SS400) | 仮定、出典なし | `hiraku/designgen/care_bed.py` |
| 介護ベッドの手計算とツールの照合の許す幅 (S1 2 %、R5 20 %、P5 20 %、T3 10 %、R3 50 %) | HIRAKU の選び | `hiraku/catalog/builders/product_care_bed.py` |
| ナレッジの規則の値 (ボルトの縁端距離 1.2 d0、すみ肉溶接の長さ 30 mm か 6 倍、中空断面の b/t・h/t ≤ 35、固定の締結具の公差の式、1 人で持てる 23 kg、腰幅と座面の奥行き、指の隙間 25 mm、60 V、電池 55 ℃、角パイプの厚さ 1.6 mm から) | EN 1993-1-8・ASME Y14.5・NIOSH の持ち上げの式 (Waters ほか 1993)・AIST 1991-92・ISO 13854・UN R100・EVE LF50K の仕様書・JIS G 3466。出典のある規則には、読んだ日か「確かめていない」を書いた (試験で確かめる)。確かめたのは、縁端距離 1.2 d0 (EN 1993-1-8 の表 3.3)、溶接の長さ 30 mm か 6 倍 (同 4.5.1(2)、有効長さの規定)、角形の中空断面の ≤ 35 (同 表 7.8)、丸パイプの 10 ≤ d0/t0 ≤ 50 (同 表 7.1)、23 kg (NIOSH Pub. 94-110 に再録の Waters ほか 1993 の 6.2)、指の隙間 25 mm (ISO 13854 の表 1)、電池 55 ℃ (LF50K)、60 V (UN R100 の 2.17。R100 の言葉は "High Voltage" (直流 60 V を超える))。確かめていないのは、公差の式 (ASME を読めず二次の出典だけ)、腰幅と座面の奥行き (AIST を読めない)、厚さ 1.6 mm (JIS の改正案だけ読めた) | `data/knowledge/*.yaml` |

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
| 人体の寸法 (座高・肩幅・腰幅・膝・届く距離 など) | AIST 人体寸法データベース 1991-92 の項目の表 (https://www.airc.aist.go.jp/dhrt/91-92/data/list.html、2026-11-03 と 11-04 に表で確かめた) | `hiraku/solvers/k4_geometry/people.py`・`assembly.py` |
| 変動振幅の荷重で Miner の和を取るときの折れ点の後の傾き 2·m − 1 (m = 3 で 5)。22 は一定振幅のときだけ | IIW-1823-07 (2008) の 4.3.1 (品質保証部が本文を読んだ) | `hiraku/solvers/k0_handcalc/library_fatigue.py`・`library_bed.py`、`hiraku/solvers/shelf/fatigue.py` |
| 電池の電圧 51.2 V (LiFePO4 16 直列 × 3.2 V) | EVE LF50K の仕様書 (LF50K-73103 Ver. D) の 3 節の表の 2 行目、セルの公称電圧 3.2 V (https://www.rcscomponents.kiev.ua/datasheets/LF50K32V50AhProduct%20Specification.pdf、2026-11-03 に本文を確認)。16 直列はこの車の設計 | `hiraku/designgen/ladder_4w.py` |
| ダブルレーンチェンジのコース (区間 15・30・25・25・15・15 m、横 3.5 m、車線の幅 1.1 W + 0.25 m・1.2 W + 0.25 m・1.3 W + 0.25 m。W はミラーを除く車幅) | ISO 3888-1:2018 の表 1 (iTeh の公開見本 https://cdn.standards.iteh.ai/samples/67973/daa839ed4aca420788d9b4a51cd6cc95/ISO-3888-1-2018.pdf、2026-11-03 に本文を確認) | `hiraku/solvers/k2_vehicle/shelf/lane_change.py` |
| 製造業の企業のうち中小企業の割合 99 % (動画の N08b) | 中小企業庁「2025 年版 中小企業白書」付属統計資料 1 表 (産業別規模別企業数、2021 年 6 月時点) の製造業の行。企業 337,490 のうち中小企業 335,552 (99.4 %)。https://www.chusho.meti.go.jp/pamflet/hakusyo/2025/chusho/excel/f1.xlsx (2026-11-02 に表で確かめた) | `promo/screens/n08b.html` |
| 自社に CAE ツールを導入している技術者の割合: 従業員 100 人以内 16.0 %・1 万人超 67.9 % (動画の N08b) | 株式会社メイテックの発表「CAEツールの導入率は43.4％。1万人超の企業では67.9％が導入済み」(2017-09-26、「fabcross for エンジニア」調べ、製造系エンジニア 500 人のインターネットリサーチ、2017-08-25)。日刊工業新聞の企業リリース https://www.nikkan.co.jp/releases/view/1612 (2026-11-03 に原文で確かめた)。会社の数の割合ではなく、答えた技術者の割合 | `promo/screens/n08b.html` |
| 静的な安定の基準 T/2h ≥ 1.0 (空車) | 79 FR 68981「The value of Kst for a vehicle at curb weight (without occupants) is required to be no less than 1.0.」。68982 で ROHVA 1-2011・B71.9-2012 の値とし、68968 で SSF = T/2H と定める (https://www.govinfo.gov/content/pkg/FR-2014-11-19/html/2014-26500.htm、2026-11-03 に本文を確認) | `hiraku/catalog/builders/k2_tests.py` |
| 横転の保護枠 (R4) の荷重: 横 1.75 M J・後ろ 1.4 M J・上から 20 M N、後ろの力は上の梁の幅の 1/6 の所 (M = 500 kg を入れて 875 J・700 J・10 kN)。トラクターの規格を借りたもので、この車の認証ではない | OECD Code 4 (トラクターの保護枠の静的試験、2026 年 2 月版) の 3.5.2・3.5.3・3.6 (https://www.oecd.org/content/dam/oecd/en/topics/policy-sub-issues/tractors/04-Code4-EN.pdf、2026-11-03 に本文を確認)。M = 500 kg はカタログの値から逆に出した仮定 | `hiraku/catalog/builders/analysis.py` |

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

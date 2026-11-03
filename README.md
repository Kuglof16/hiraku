# HIRAKU

作りたいものの要件を渡すと、Claude のエージェントたちが分野ごとに結果を手計算で予測してから CAE で解析し、予測と照らし合わせながら設計を直していくアプリです。(作成中)

HIRAKU: Claude agents predict each result by hand calculation before running the CAE analysis, check one against the other, and revise the design. Work in progress.

Built with Claude: Japan 2026 (2026-11-02 〜 11-08) の期間中に作っています。

## いま動くもの

- デモの車両 (林道を走る 2 人乗りの 4 輪) で、要件のファイルに並べた試験 (段差越え P1・強度 R1・振動 N1・ハンドルの切れ角 H1・乗員の体格 H3) を「手計算で予測 → 解析 → 照合」で回す流れ。基準のある試験は合否まで出す (P1 は基準が無く照合まで)。予測の無い解析は hooks が止め、食い違いは原因を書くまで先へ進めない。説明できて安全側と判断できる食い違いは、不利な方の値で合否を出す
- 設計役が設計の変数を変えて次の版を作る (`revise_design`)。デモの車両では、v1 で不合格の H1・H3 を直して v2 で合格する
- 解析の道具 15 個 (梁の有限要素・3D の車両の物理・電力と熱・幾何と干渉)。どれも答えの分かっている例と照合してから使う。手計算の式 40 個は単体の試験で確かめている (実行の中での照合は無い)。感度 (K5) は梁・電力と熱・幾何の 8 つの道具に `params.sensitivity` で付く
- 試験ごとの荷重条件の作り方は、P1・R1 を含めて 20 試験ぶんある (衝突・疲労・電力・熱・組み立て・傾斜台・横風・レーンチェンジ・制動・発進・凸凹の横の坂 など)。要件のファイルに並べれば実行に乗る
- 実行の記録 (`runs/<id>/events.jsonl`) を再生する作業台の画面 (3D の応力の色・矢印・試験の場面・走行の再生) と、分野ごとの判定のタイル

まだのもの: 20 試験ぶんを全部まとめて回すこと、新しい設計・報告・組み立てなどの画面、画面から実行を始める口。

## 動かし方

Python 3.12 で、リポジトリの中に venv を作って依存を入れます。

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

板とソリッドの大きなモデルを速く解く道 (任意。Windows と Linux だけ。無ければ SciPy の SuperLU で解く):

```
.venv\Scripts\python -m pip install -r requirements-fast.txt
```

試験:

```
.venv\Scripts\python -m unittest
```

API キー無しで、Claude の代わりに決まった順に道具を呼ぶ「台本の運転」で 1 回まわし、記録を確かめる:

```
.venv\Scripts\python -m hiraku run data/requirements/offroad-4w.yaml --scripted
.venv\Scripts\python -m hiraku check runs/<run_id>
```

Claude で回すときは、その窓だけに `ANTHROPIC_API_KEY` を入れて `--scripted` を外します。費用の上限は要件のファイルの `cost_limit_usd` です。

記録を画面で再生する (ブラウザで http://127.0.0.1:8765/ を開き、一覧から選ぶ。`?run=sample` は形の見本、`?look=real` は実物の見た目):

```
.venv\Scripts\python -m hiraku.webserve
```

- 画面は three.js 0.170.0 (MIT) を jsDelivr から読み込むので、ネットにつながっている必要があります。字は PC にある BIZ UDPGothic を使い、同梱していません
- ブラウザの試験は Playwright を使い、`HIRAKU_WEB_BROWSER=1` のときだけ回ります (Playwright は requirements.txt に入れていません)
- 動画の書き出しの道具は `promo/` にあり、要るものは `promo/requirements-video.txt` です

## 中身

- `hiraku/agent/` エージェントのループ・hooks・役 (まとめ役・設計役・パッケージ役・分野ごとの CAE 役)、`plugins/hiraku/` skills
- `hiraku/tools/` エージェントが呼ぶ道具 (MCP)。数値は道具が計算し、モデルには書かせない
- `hiraku/solvers/` 手計算の式 (K0) と CAE の土台 (K1 梁の有限要素・K2 3D の車両の物理・K3 電力と熱・K4 幾何と干渉・K5 感度)
- `hiraku/intake/` 受付 (分野を選ぶ・ヒアリング・ナレッジの取り込み・規則と系の点検)
- `hiraku/catalog/` 要件から試験の荷重条件を作る、`hiraku/designgen/` 設計の変数から設計を組む
- `hiraku/store/` 実行の記録、`hiraku/contract/` 記録の形 (JSON Schema)、`web/` 画面、`data/` 要件・試験・デモの車両
- `tests/`、`qa/` 照合の試験と記録の点検、`promo/` 動画だけの画面と書き出し

部署ごとに分けて作った方法と、記録の形は [docs/contract.md](docs/contract.md)。

## Claude の使い方

### エージェントのループ (Claude Agent SDK)

- 実行は `python -m hiraku run <要件のファイル>` で始まる (`hiraku/__main__.py`)。要件のファイルから荷重条件と実行のフォルダを作り、設計の版 1 と道具の棚の確かめまでを済ませる (`hiraku/agent/session.py` の `start_run`)。要件に `design_file` が無いときは版 0 から始め、設計役が `propose_design` で版 1 を作る (同)。
- 役は 4 種類ある (`hiraku/agent/roles.py`、`hiraku/agent/prompts.py`)。
  - まとめ役 (lead): メインのエージェント。仕事を役に渡し、判定を確かめ、最後に `write_report` を呼ぶ (`prompts.py` の `LEAD_PROMPT`)。
  - 分野ごとの CAE 役 (`cae-<分野>`): SDK のサブエージェント。effort は medium (`roles.py` の `CAE_EFFORT`)。
  - パッケージ役 (`packager`): 組み立ての荷重条件を受け持つ。ナレッジの規則を当てる `rule_check` も使う (`roles.py` の `PACKAGER_TOOLS`)。
  - 設計役 (`designer`): `propose_design` と `revise_design` で設計の版を作る。effort は high (`roles.py`)。
- サブエージェントはどれも前景で動く (`background=False`)。まとめ役はその報告を待つ (`roles.py`、`runner.py` の `FOREGROUND_ROLES_ENV`)。
- 荷重条件 1 つの進め方は CAE 役の指示に書いてある (`prompts.py` の `cae_prompt`)。
  1. `get_context` で荷重条件と設計の値を読む。
  2. `list_tools` と `list_formulas` で道具と式を選ぶ。
  3. `record_prediction` で手計算の予測を記録する。
  4. `analyze` で解析する。
  5. `compare` で予測と照合する。
  6. 食い違ったら `record_cause` に原因と直し方を書き、予測を記録し直してやり直す。
- 赤の分野が残り、直す回数 (`round_limit`) も残っていれば、まとめ役が設計役に渡す。新しい版では、全部の荷重条件をやり直す (`LEAD_PROMPT` の 4、`hiraku/agent/hooks.py` の `next_step`)。
- 使える組み込みの道具は `Agent` と `Skill` だけ。`permission_mode="dontAsk"` で、名前を並べた道具のほかは止まる (`hiraku/agent/runner.py` の `build_options`)。
- この PC の CLAUDE.md・設定・hooks は読まない (`setting_sources=[]`)。MCP サーバーは `hiraku` だけ (`strict_mcp_config=True`) (同)。

### hooks

hooks の規則は HK1〜HK4 の 4 つ (`hiraku/contract/protocols.py` の `HOOK_RULES`、`docs/contract.md`)。中身は `hiraku/tools/rules.py`、SDK への組み込みは `hiraku/agent/hooks.py`。

| ID | hook | 止めるもの・守らせるもの |
|---|---|---|
| HK1 | PreToolUse | 同じ版・同じ荷重条件の予測が無い `analyze` を止める (`rules.py` の `check_analyze`) |
| HK2 | PreToolUse | 答えの分かっている例に通っていない道具の `analyze` を止める (同) |
| HK3 | PreToolUse | 原因の書かれていない食い違いがある分野では、`analyze` と `compare` を止める (`rules.py` の `_h3`) |
| HK4 | Stop / SubagentStop | 選んだ分野に判定の無いものが残っている間は、終わらせない。止めた回数には上限がある (まとめ役 3 回、役ごとに 2 回。`hooks.py` の `MAX_STOP_BLOCKS`・`MAX_SUBAGENT_STOP_BLOCKS`) |

- 止めたときは `hook.denied` か `hook.blocked_stop` の event を出す。理由の文には「足りないもの」と「次に呼ぶもの」を書き、それが Claude に返る (`hooks.py`、`rules.py` の冒頭)。
- 道具の関数も、同じ確かめをもう一度する。だから hook を通らずに呼んでも、規則は抜けない (`rules.py` の冒頭、`hiraku/tools/functions.py` の `_gate`)。
- PostToolUse では、次にすることを `additionalContext` で渡す。`analyze` のあとは `compare` を、食い違ったあとは `record_cause` を促す (`hooks.py` の `next_step`)。
- 止めるボタンを押すと、実行のフォルダに `stop.request` ができる。そのあとは道具の呼び出しを全部止め、終わる (`hooks.py` の `pre_tool_use`・`stop`)。
- SubagentStart と SubagentStop では `agent.started` と `agent.stopped` を出す (`hooks.py`)。

### MCP の道具 (`hiraku/tools/`)

サーバーの名前は `hiraku`、道具の名前は `mcp__hiraku__<名前>` (`hiraku/tools/mcp.py`、`docs/contract.md`)。並びは `hiraku/tools/functions.py` の `RUN_TOOLS`、説明は `mcp.py` の `DESCRIPTIONS` と `hiraku/intake/tools.py` の `TOOLS` にある。

- 読む道具
  - `get_context`: 実行の今の様子。要る部分だけを `sections` で選ぶ。
  - `list_formulas`: 手計算 (K0) の式の一覧。
  - `list_tools`: 解析の道具の棚。`usable` が true の道具だけを使える。
- 予測・解析・照合
  - `record_prediction`: 式と入力と前提を選ぶ。値は K0 が計算する。
  - `analyze`: 全部の解析の道具をこの 1 つから呼ぶ。
  - `compare`: 予測と解析を照合する。一致して基準があれば、合否も作る。
  - `record_cause`: 食い違いの原因と直し方を書く。分野の止まりがこれで解ける。
- 設計 (設計役だけ)
  - `propose_design`: 版 0 から最初の版を作る。
  - `revise_design`: 設計の変数を変えて、次の版を作る。
- 道具を作る
  - `check_tool`: Claude が書いた道具のコードを、別のプロセスで、答えの分かっている例と比べて確かめる。
  - `make_tool`: 確かめに通った道具を棚に入れる。
- 報告
  - `write_report`: まとめ役が最後に 1 回呼ぶ。文の中の「数 + 単位」が記録と合わなければ断る。
- 受付 (`hiraku/intake/tools.py`)
  - `pick_domains`: 分野を選ぶ。分野と試験はカタログからコードが決める。
  - `ask_user`: 足りない要件をまとめて 1 回聞く。
  - `import_knowledge`: 渡されたファイルを規則にする。
  - `rule_check`: 規則を設計の部品に当てる。

### Skills と plugin (`plugins/hiraku/`)

- ローカルの plugin `hiraku` が 1 つある (`plugins/hiraku/.claude-plugin/plugin.json`)。実行で読む skill はこの plugin のものだけ (`runner.py` の `plugins`・`skills`、`roles.py` の `plugin_skills`)。
- skill は 3 つ。
  - `take-the-request`: まとめ役が依頼を受ける手順。分野を選び、要件を聞き、ナレッジを規則にし、規則を当てる。途中で届く伝言の受け方も書いてある (`plugins/hiraku/skills/take-the-request/SKILL.md`)。
  - `predict-then-analyze`: 荷重条件 1 つを、予測 → 解析 → 照合 → 合否まで進める手順 (`.../predict-then-analyze/SKILL.md`)。
  - `find-the-cause`: 食い違ったときに原因を探し、`record_cause` に書く手順 (`.../find-the-cause/SKILL.md`)。

### 台本の運転と Claude の運転

- 台本の運転 (`--scripted`) は Claude を呼ばない。同じ道具の関数と hooks の規則を、決まった順に呼ぶ。順は `data/catalog/scripted.yaml` と試験ごとの作り方にある (`hiraku/agent/scripted.py` の冒頭、`hiraku/__main__.py`)。
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

- 役のプロンプトには「数値は全部、道具が出す。自分で計算した数を入力にも文にも書かない。単位の換算もしない」とある (`prompts.py` の `COMMON_RULES`)。
- 予測の値は K0 が計算し、Claude は式と入力を選ぶだけ (`docs/contract.md` の「1 つの数字を信用して合否を出すまで」、`mcp.py` の `record_prediction` の説明)。
- 照合の許す幅は荷重条件に入っていて、モデルは決めない。合否は道具の中でだけ作る (`docs/contract.md`)。
- 報告の数はコードが記録から入れる。Claude が書くのは `summary` と `next` だけ (`docs/contract.md`、`mcp.py` の `write_report` の説明)。
- Claude が実行の中で作った道具は、答えの分かっている例に全部通ったときだけ棚に入る (`mcp.py` の `make_tool` の説明、`docs/contract.md`)。

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
- 契約の中身: 記録の形、`runs/<run_id>/` の木、event の種類、MCP の道具の入力の形、hooks の規則 (`docs/contract.md`、`hiraku/contract/layout.py`、`hiraku/contract/protocols.py`)。
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
  - `tests/` の下で `def test_` を数えると、103 ファイルに 1,315 個ある (このブランチ lead-merge-trial、2fb6f9b の時点)。
  - この数には、条件を付けたときだけ回るものも入っている。例: ブラウザの試験は `HIRAKU_WEB_BROWSER=1` のときだけ回る (`README.md`)。
  - 実際に回る数は、`python -m unittest` か `python -m qa.run_tests` が出す `Ran N tests` で数える。

### Codex について

- `git log --all --grep="Drafted with Codex"` に当たるコミットは 0 件。
- `codex` という字を含むコミット (大文字小文字を区別しない) は 1 件ある。35975dd「first_look: cuts and screens seen as a first-time viewer, judged by an image model」。
  - 中身は `qa/first_look.py`。画面と動画の静止画を、見る人の目で Codex CLI に評価させる品質保証部の道具。
  - このコミットにも `Co-Authored-By: Claude` の行がある。
- 全部の branch のコミット 680 件のうち、487 件に `Co-Authored-By: Claude` の行がある (`git log --all --grep="Co-Authored-By: Claude"`)。

## 期間中に作ったもの

このリポジトリのコードは、すべて 2026-11-02 12:00 (JST) 以降に書いたものです。

確かめ方 (どれも GitHub とこのリポジトリの記録から見られます):

- リポジトリを作ったのは 2026-11-02 18:51 (JST)。最初のコミットは同じ日の 18:45 で、それより前のコミットはありません
- GitHub の Activity (リポジトリの Insights の Activity、または `gh api repos/Kuglof16/hiraku/activity`) に、サーバーが記録した push の時刻が並びます。コミットの日付と違い、手元では書き換えられません。force push はしていません
- 開発は Claude Code のセッションを部署に分けて並べて進めました (統括部・設計部・解析部・解析部 K2 班・画面部・品質保証部・広報部・受付部)。部署ごとの枝 `claude/dept-*` も push してあり、並行して進んだことと、統括部が試験を通してから main に入れたことが、枝と merge の履歴で見えます
- 1 つのコミットが大きいことがあります (例: 受付の画面 A1・A2・A5 で約 7,000 行)。部署のセッションが、1 つのまとまりを書いて試験を通してからコミットしたためです
- 開始前に、リポジトリの外 (本人のメモ) で計画を作りました: 作るものの仕様、要件と試験のカタログの分け方 (製品の性質のフラグと、それで決まる試験の一覧)、動画の絵コンテ。期間中のコードはこの計画に沿っていますが、開始前に書いたコードは写していません。計画の文書もリポジトリには入れていません
- 行数とコミットの数は、`git log` と `git ls-files` で誰でも数え直せます

## 前から持っていた知識と、外から持ってきたデータ

外から持ってきた値 (規格・統計・材料・人体の寸法など) は、使った所 (データのファイルとコードの中) に出典を書いています。出典の本文をこのリポジトリで読み直していない値には "not checked" と書いています。

### 確かめていない値

次の値は、出典の本文をこのリポジトリではまだ確かめていません (確かめていない)。どれも使った所に出典を書いています。

| 値 | 出典として書いたもの | 使った所 |
|---|---|---|
| 材料の降伏点・ヤング率・密度 (STKR400・STKM13A・SS400・6061-T6) | JIS G 3466・G 3445・G 3101 の最小の降伏点、一般の設計値、ハンドブックの値 | `data/materials/materials.json` |
| 角パイプ・丸パイプの規格の寸法 (角 112 種・丸 128 種。単位質量を規格の式で計算し直して全行一致。行の数と 29 の寸法は 2015 年版 (改正込み) の表で確かめた。2021 年と 2025 年の版は確かめていない) | JIS G 3466 の表 5・6、JIS G 3444 の表 6 (2015 年版と改正) | `data/sections/standard_tubes.json` |
| 電池の電圧 51.2 V (LiFePO4 16 直列 × 3.2 V) | セルの公称電圧 | `hiraku/designgen/ladder_4w.py` |
| 部品の温度の上限 (電池 55 ℃・制御器 85 ℃・モーター 155 ℃) | EVE LF50K の仕様書、Curtis 1236 のマニュアル、IEC 60085 の F 種 | `hiraku/designgen/ladder_4w.py` |
| 空気の密度 1.225 kg/m³ | ICAO Doc 7488 / ISO 2533 の海面の値 | `hiraku/designgen/ladder_4w.py`、`hiraku/solvers/k0_handcalc/library_dynamics.py`、`hiraku/solvers/k2_vehicle/` |
| 疲労の強さ (FAT 71・傾き 3・折れ点 1e7 回) | IIW の疲労設計の推奨 (2016) | `hiraku/designgen/ladder_4w.py`、`hiraku/solvers/k0_handcalc/library_fatigue.py` |
| 路面の粗さ (ISO 8608 の C 級、G_d(n0) = 256×10⁻⁶ m³) | ISO 8608 の表 | `hiraku/solvers/k2_vehicle/road.py`・`shelf/offroad.py`、`hiraku/solvers/shelf/fatigue.py` |
| ダブルレーンチェンジのコース (15・30・25・25・15・15 m、横 3.5 m) | ISO 3888-1 | `hiraku/solvers/k2_vehicle/shelf/lane_change.py` |
| 溶接のホットスポット応力 (溶接の線から板厚 t の 0.5 倍と 1.5 倍の表面の応力を、σ = 1.5σ(0.5t) − 0.5σ(1.5t) で線まで外挿) | IIW の疲労設計の推奨 (Hobbacher 2016) | `hiraku/solvers/k1_shell/stress.py`、`hiraku/solvers/shelf/frame_shell.py` |
| タイヤの MF 6.1 の係数 (205/60R15 の例のタイヤ、98 個) | Pacejka, Tire and Vehicle Dynamics 3 版 Appendix 3 (公開の .tir から写し、写しが元と一致することは機械で確かめた。本そのものは確かめていない) | `data/tyres/pacejka_tvd3_205_60r15.json` |
| 静的な安定の基準 T/2h ≥ 1.0 | ROHVA 1-2011・ANSI B71.9-2012 を要約した 79 FR 68964 | `hiraku/catalog/builders/k2_tests.py` |
| 要件の値 (摩擦係数・坂・風・停止距離・倒れない角度・航続 など) | 各項目の `basis` (林道規程・気象庁・細目告示からの借用・CPSC の要約・仮定) | `data/requirements/offroad-4w.yaml` |
| 人体の寸法 (座高・肩幅・腰幅・膝) と工具の開口 | AIST 人体寸法データベース 1991-92、MIL-STD-1472H | `hiraku/solvers/k4_geometry/people.py`・`assembly.py` |
| 製品ごとの要件の値 (ギター・介護ベッド・配膳ロボ) | 各項目の `basis` (JIS T 9254 の写し・アクチュエーターの仕様書・FDA の指針・配膳ロボの製品の仕様・ISO/TS 15066 を引いた論文・弦の張力の表・仮定。URL つき) | `data/requirements/products/*.yaml`、答えの見本 `*.answers.yaml` |
| ヒアリングの既定値 (風 20 m/s・最低地上高 180 mm・体重 76.6 kg と 55 kg・身長の幅・指の隙間 25 mm など) | 気象庁「風の強さと吹き方」・EU 規則 168/2013 付属書 I・AIST 1991-92・細目告示 第 2 節・ISO 13854 (URL つき) | `hiraku/intake/requirement_items.yaml` |
| 門形の梁の式 (ロールバーの横・後ろ・上からの力、たわみ角法と塑性ヒンジ) | 構造力学の教科書 (Heyman『Plastic Design of Frames』・Neal『The Plastic Methods of Structural Analysis』) の方法。式はコードで組み、片持ち・両端固定などの答えの分かる形で試験した。本は開いていない | `hiraku/solvers/k0_handcalc/library_portal.py` |
| ナレッジの規則の値 (ボルトの縁端距離 1.2 d0、すみ肉溶接の長さ 30 mm か 6 倍、中空断面の b/t・h/t ≤ 35、固定の締結具の公差の式、1 人で持てる 23 kg、腰幅と座面の奥行き、指の隙間 25 mm、60 V、電池 55 ℃、角パイプの厚さ 1.6 mm から) | EN 1993-1-8・ASME Y14.5・NIOSH の持ち上げの式 (Waters ほか 1993)・AIST 1991-92・ISO 13854・UN R100・EVE LF50K の仕様書・JIS G 3466。出典を開かずに記憶から書いたものは `recalled` と印を付けた | `data/knowledge/*.yaml` |

### 出典の本文を確かめた値

| 値 | 出典 | 使った所 |
|---|---|---|
| 横転の保護枠 (R4) の荷重: 横 1.75 M J・後ろ 1.4 M J・上から 20 M N、後ろの力は上の梁の幅の 1/6 の所 (M = 500 kg を入れて 875 J・700 J・10 kN)。トラクターの規格を借りたもので、この車の認証ではない | OECD Code 4 (トラクターの保護枠の静的試験、2026 年 2 月版) の 3.5.2・3.5.3・3.6 (https://www.oecd.org/content/dam/oecd/en/topics/policy-sub-issues/tractors/04-Code4-EN.pdf、2026-11-03 に本文を確認)。M = 500 kg はカタログの値から逆に出した仮定 | `hiraku/catalog/builders/analysis.py` |

- 動画のナレーション: 作者が自分の声で録った。下書きの段階では AivisSpeech (音声合成モデル「阿井田 茂」、制作 古山キリヲ、Aivis Common Model License (ACML) 1.0) を使ったが、提出の動画には使っていない

## ライセンス

MIT ([LICENSE](LICENSE))

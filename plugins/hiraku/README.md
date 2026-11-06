# HIRAKU のプラグイン (Claude Code)

自分の Claude Code から HIRAKU を動かすためのプラグインです。要件のファイルから実行を始め、まとめ役・設計役・CAE 役が、手計算で予測してから解析し、予測と比べ (予測との判定)、食い違いの原因を書いて基準の判定 (OK / NG) を出します。中身は HIRAKU のツール (MCP のサーバー `hiraku`)・hooks (HK1〜HK6)・役 (agents)・手順 (skills)・コマンド `/hiraku:run` です。

- この道筋では、HIRAKU は Claude を呼ばず、Claude Code も起動しません。考えるのは自分の Claude Code のセッション (自分のサインイン) で、HIRAKU はツールと hooks として働くだけです。`ANTHROPIC_API_KEY` は要りません。費用は HIRAKU からは分からないので、記録の `cost_usd` は null です
- 費用の上限 (要件の `cost_limit_usd`) は、この道筋では HIRAKU が守らせられません。払うのは自分の Claude Code (自分のサインインの枠) で、HIRAKU には使った額が届かないからです (`project.cost_usd` は null のまま)。止まるための守りは、設計を直す回数の上限 `round_limit` (使い切ると HK4 は終わりを止めません) と、ターンの上限 (Claude Code の側の設定) です。HK4 が終わりを止める回数にも上限があります (まとめ役 3 回、役ごと版ごとに 2 回)
- 実行の記録は、リポジトリの `runs/<run_id>/` に書かれます

## 用意: リポジトリの venv

HIRAKU は、リポジトリの `.venv` に `requirements.txt` を入れた Python で動きます。

```
cd <リポジトリ>
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt     (Windows)
.venv/bin/python -m pip install -r requirements.txt         (ほか)
```

プラグインは PATH の `python` から `scripts/hiraku_cli.py` を呼び、そこで HIRAKU 用の Python に切り替えます。選ぶ順は、環境変数 `HIRAKU_PYTHON` → `<リポジトリ>/.venv` → (リポジトリが git の worktree なら) 元のチェックアウトの `.venv` です。どれも無く、PATH の `python` に `mcp` も無ければ、何をすればよいかを 1 行出して止まります。

## 始め方

1. どのフォルダからでも、プラグインをその場で読ませて Claude Code を始めます。

   ```
   claude --plugin-dir <リポジトリ>/plugins/hiraku --allowedTools "mcp__plugin_hiraku_hiraku"
   ```

   - プラグインはリポジトリの中から読ませます。別の場所に写したプラグインは、リポジトリを見つけられません (理由は `scripts/hiraku_cli.py` の冒頭の説明)。
   - `--allowedTools "mcp__plugin_hiraku_hiraku"` は、HIRAKU のツールを毎回聞かずに使わせる設定です (サーバー `hiraku` のツール全部)。付けないと、役のサブエージェントが予測・解析・予測との判定を呼ぶたびに、Claude Code が許可を聞きます (1 回の実行で数十回)。
   - 始めたら `/mcp` で、プラグインの `hiraku` のサーバーがつながっているかを見られます。つながっていなければ、上の「用意」の venv ができているかを確かめます。

2. 要件のファイルを渡して実行を始めます (ファイル名だけなら `data/requirements/` から探します)。

   ```
   /hiraku:run offroad-4w-n12.yaml
   ```

   - 最初の `start_run` は、ツールの棚を確かめるので 30〜60 秒ほどかかります。
   - `offroad-4w-n12.yaml` は、分野 1 つ (操縦安定)・設計を直す回数 1 回の小さい要件です。考えるのは自分の Claude Code なので、自分のサインインの枠を使います。`offroad-4w.yaml` は分野が多く、何倍も長くかかります。

3. 画面で見ます。別の窓で、リポジトリのフォルダから venv の Python で画面のサーバーを始め、`start_run` の答えの `screen` にある URL を開きます。

   ```
   cd <リポジトリ>
   .venv\Scripts\python -m hiraku.webserve       (Windows)
   .venv/bin/python -m hiraku.webserve           (ほか)
   ```

   開く URL は `http://127.0.0.1:8765/?run=<run_id>` です。画面は `runs/` を見るので、リポジトリのフォルダから始めます。

## キーもお金も使わずに確かめる

Claude を呼ばない台本の運転 (同じツールと hooks の決まりを、決まった順に回す) で、記録と画面を確かめられます。リポジトリのフォルダで:

```
.venv\Scripts\python -m hiraku run data/requirements/offroad-4w-n12.yaml --scripted     (Windows)
```

40 秒ほどで終わり、`runs/<run_id>/` (run_id は `-scripted` で終わる) ができて、上の画面で見られます (画面の `?list=1` から始めることもできます)。

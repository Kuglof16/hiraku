# HIRAKU のプラグイン (Claude Code)

自分の Claude Code から HIRAKU を動かすためのプラグインです。要件のファイルから実行を始め、まとめ役・設計役・CAE 役が、手計算で予測してから解析し、照合し、食い違いの原因を書いて合否を出します。中身は HIRAKU の道具 (MCP のサーバー `hiraku`)・hooks (HK1〜HK4)・役 (agents)・手順 (skills)・コマンド `/hiraku:run` です。

- この道筋では、HIRAKU は Claude を呼ばず、Claude Code も起動しません。考えるのは自分の Claude Code のセッション (自分のサインイン) で、HIRAKU は道具と hooks として働くだけです。`ANTHROPIC_API_KEY` は要りません。費用は HIRAKU からは分からないので、記録の `cost_usd` は null です
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
   claude --plugin-dir <リポジトリ>/plugins/hiraku
   ```

   プラグインはリポジトリの中から読ませます。別の場所に写したプラグインは、リポジトリを見つけられません (理由は `scripts/hiraku_cli.py` の冒頭の説明)。

2. 要件のファイルを渡して実行を始めます (ファイル名だけなら `data/requirements/` から探します)。

   ```
   /hiraku:run offroad-4w-n12.yaml
   ```

3. 画面で見ます。別の窓でリポジトリから `python -m hiraku.webserve` を始め、`start_run` の答えにある `?run=<run_id>` を開きます。

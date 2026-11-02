# HIRAKU

作りたいものの要件を渡すと、Claude のエージェントたちが分野ごとに結果を手計算で予測してから CAE で解析し、予測と照らし合わせながら設計を直していくアプリです。(作成中)

HIRAKU: Claude agents predict each result by hand calculation before running the CAE analysis, check one against the other, and revise the design. Work in progress.

Built with Claude: Japan 2026 (2026-11-02 〜 11-08) の期間中に作っています。

## いま動くもの

- デモの車両 (林道を走る 2 人乗りの 4 輪) で、段差越え (P1) とフレームの強度 (R1) を「手計算で予測 → 解析 → 照合 → 合否」まで回す流れ。予測の無い解析は hooks が止め、食い違いは原因を書くまで先へ進めない
- 解析の道具 15 個 (梁の有限要素・3D の車両の物理・電力と熱・幾何と干渉・感度) と手計算の式 40 個。どれも答えの分かっている例と照合してから使う
- 実行の記録 (`runs/<id>/events.jsonl`) を再生する作業台の画面

まだのもの: 段差越えと強度のほかの試験をエージェントの流れにつなぐこと、設計を直して次の版を作ること、ほかの画面。

## 動かし方

Python 3.12 で、リポジトリの中に venv を作って依存を入れます。

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
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

Claude で回すときは、その窓だけに `ANTHROPIC_API_KEY` を入れて `--scripted` を外します。

記録を画面で再生する (ブラウザで http://127.0.0.1:8765/ を開き、一覧から選ぶ。`?run=sample` は形の見本):

```
.venv\Scripts\python -m hiraku.webserve
```

## 中身

- `hiraku/agent/` エージェントのループ・hooks・役 (サブエージェント)、`plugins/hiraku/` skills
- `hiraku/tools/` エージェントが呼ぶ道具 (MCP)。数値は道具が計算し、モデルには書かせない
- `hiraku/solvers/` 手計算の式 (K0) と CAE の土台 (K1 梁の有限要素・K2 3D の車両の物理・K3 電力と熱・K4 幾何と干渉・K5 感度)
- `hiraku/store/` 実行の記録、`hiraku/contract/` 記録の形 (JSON Schema)、`web/` 画面、`data/` 要件・試験・デモの車両
- `tests/`、`qa/` 照合の試験と記録の点検、`promo/` 動画だけの画面と書き出し

部署ごとに分けて作った方法と、記録の形は [docs/contract.md](docs/contract.md)。

## 期間中に作ったもの

このリポジトリのコードは、すべて 2026-11-02 12:00 (JST) 以降に書いたものです。

## 前から持っていた知識と、外から持ってきたデータ

外から持ってきた値 (規格・統計・材料・人体の寸法など) は、使った所 (データのファイルとコードの中) に出典を書いています。出典の本文をこのリポジトリで読み直していない値には "not checked" と書いています。まとめの表は提出までにここに書きます。

- 動画のナレーション: AivisSpeech (音声合成モデル「阿井田 茂」、制作 古山キリヲ、Aivis Common Model License (ACML) 1.0)

## ライセンス

MIT ([LICENSE](LICENSE))

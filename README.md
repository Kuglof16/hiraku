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
- `hiraku/catalog/` 要件から試験の荷重条件を作る、`hiraku/designgen/` 設計の変数から設計を組む
- `hiraku/store/` 実行の記録、`hiraku/contract/` 記録の形 (JSON Schema)、`web/` 画面、`data/` 要件・試験・デモの車両
- `tests/`、`qa/` 照合の試験と記録の点検、`promo/` 動画だけの画面と書き出し

部署ごとに分けて作った方法と、記録の形は [docs/contract.md](docs/contract.md)。

## 期間中に作ったもの

このリポジトリのコードは、すべて 2026-11-02 12:00 (JST) 以降に書いたものです。

確かめ方 (どれも GitHub とこのリポジトリの記録から見られます):

- リポジトリを作ったのは 2026-11-02 18:51 (JST)。最初のコミットは同じ日の 18:45 で、それより前のコミットはありません
- GitHub の Activity (リポジトリの Insights の Activity、または `gh api repos/Kuglof16/hiraku/activity`) に、サーバーが記録した push の時刻が並びます。コミットの日付と違い、手元では書き換えられません。force push はしていません
- 開発は Claude Code のセッションを部署に分けて並べて進めました (統括部・設計部・解析部・解析部 K2 班・画面部・品質保証部・広報部・受付部)。部署ごとの枝 `claude/dept-*` も push してあり、並行して進んだことと、統括部が試験を通してから main に入れたことが、枝と merge の履歴で見えます
- 1 つのコミットが大きいことがあります (例: 受付の画面 A1・A2・A5 で約 7,000 行)。部署のセッションが、1 つのまとまりを書いて試験を通してからコミットしたためです
- 開始前に、リポジトリの外 (本人のメモ) で計画を作りました: 作るものの仕様、要件と試験のカタログの分け方 (製品の性質のフラグと 18 の試験)、動画の絵コンテ。期間中のコードはこの計画に沿っていますが、開始前に書いたコードは写していません。計画の文書もリポジトリには入れていません
- 行数とコミットの数は、`git log` と `git ls-files` で誰でも数え直せます

## 前から持っていた知識と、外から持ってきたデータ

外から持ってきた値 (規格・統計・材料・人体の寸法など) は、使った所 (データのファイルとコードの中) に出典を書いています。出典の本文をこのリポジトリで読み直していない値には "not checked" と書いています。

### 確かめていない値

次の値は、出典の本文をこのリポジトリではまだ確かめていません (確かめていない)。どれも使った所に出典を書いています。

| 値 | 出典として書いたもの | 使った所 |
|---|---|---|
| 材料の降伏点・ヤング率・密度 (STKR400・STKM13A・SS400・6061-T6) | JIS G 3466・G 3445・G 3101 の最小の降伏点、一般の設計値、ハンドブックの値 | `data/materials/materials.json` |
| 電池の電圧 51.2 V (LiFePO4 16 直列 × 3.2 V) | セルの公称電圧 | `hiraku/designgen/ladder_4w.py` |
| 部品の温度の上限 (電池 55 ℃・制御器 85 ℃・モーター 155 ℃) | EVE LF50K の仕様書、Curtis 1236 のマニュアル、IEC 60085 の F 種 | `hiraku/designgen/ladder_4w.py` |
| 空気の密度 1.225 kg/m³ | ICAO Doc 7488 / ISO 2533 の海面の値 | `hiraku/designgen/ladder_4w.py`、`hiraku/solvers/k0_handcalc/library_dynamics.py`、`hiraku/solvers/k2_vehicle/` |
| 疲労の強さ (FAT 71・傾き 3・折れ点 1e7 回) | IIW の疲労設計の推奨 (2016) | `hiraku/designgen/ladder_4w.py`、`hiraku/solvers/k0_handcalc/library_fatigue.py` |
| 路面の粗さ (ISO 8608 の C 級、G_d(n0) = 256×10⁻⁶ m³) | ISO 8608 の表 | `hiraku/solvers/k2_vehicle/road.py`・`shelf/offroad.py`、`hiraku/solvers/shelf/fatigue.py` |
| ダブルレーンチェンジのコース (15・30・25・25・15・15 m、横 3.5 m) | ISO 3888-1 | `hiraku/solvers/k2_vehicle/shelf/lane_change.py` |
| タイヤの MF 6.1 の係数 (205/60R15 の例のタイヤ、98 個) | Pacejka, Tire and Vehicle Dynamics 3 版 Appendix 3 (公開の .tir から写し、写しが元と一致することは機械で確かめた。本そのものは確かめていない) | `data/tyres/pacejka_tvd3_205_60r15.json` |
| 静的な安定の基準 T/2h ≥ 1.0 | ROHVA 1-2011・ANSI B71.9-2012 を要約した 79 FR 68964 | `hiraku/catalog/builders/k2_tests.py` |
| 要件の値 (摩擦係数・坂・風・停止距離・倒れない角度・航続 など) | 各項目の `basis` (林道規程・気象庁・細目告示からの借用・CPSC の要約・仮定) | `data/requirements/offroad-4w.yaml` |
| 人体の寸法 (座高・肩幅・腰幅・膝) と工具の開口 | AIST 人体寸法データベース 1991-92、MIL-STD-1472H | `hiraku/solvers/k4_geometry/people.py`・`assembly.py` |
| 製品ごとの要件の値 (ギター・介護ベッド・配膳ロボ) | 各項目の `basis` (JIS T 9254 の写し・アクチュエーターの仕様書・FDA の指針・配膳ロボの製品の仕様・ISO/TS 15066 を引いた論文・弦の張力の表・仮定。URL つき) | `data/requirements/products/*.yaml`、答えの見本 `*.answers.yaml` |
| ヒアリングの既定値 (風 20 m/s・最低地上高 180 mm・体重 76.6 kg と 55 kg・身長の幅・指の隙間 25 mm など) | 気象庁「風の強さと吹き方」・EU 規則 168/2013 付属書 I・AIST 1991-92・細目告示 第 2 節・ISO 13854 (URL つき) | `hiraku/intake/requirement_items.yaml` |
| ナレッジの規則の値 (ボルトの縁端距離 1.2 d0、すみ肉溶接の長さ 30 mm か 6 倍、中空断面の b/t・h/t ≤ 35、固定の締結具の公差の式、1 人で持てる 23 kg、腰幅と座面の奥行き、指の隙間 25 mm、60 V、電池 55 ℃、角パイプの厚さ 1.6 mm から) | EN 1993-1-8・ASME Y14.5・NIOSH の持ち上げの式 (Waters ほか 1993)・AIST 1991-92・ISO 13854・UN R100・EVE LF50K の仕様書・JIS G 3466。出典を開かずに記憶から書いたものは `recalled` と印を付けた | `data/knowledge/*.yaml` |

- 動画のナレーション: AivisSpeech (音声合成モデル「阿井田 茂」、制作 古山キリヲ、Aivis Common Model License (ACML) 1.0)

## ライセンス

MIT ([LICENSE](LICENSE))

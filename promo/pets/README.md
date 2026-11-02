# HIRAKU のペット (3D)

HIRAKU のエージェントの役ごとのキャラ (ペット) を three.js で 3D にしたものと、見ながら直すための専用ビューアです。版は v1。

| ファイル | 中身 |
|---|---|
| `pet3d.js` | キャラの組み立て。ふつうのスクリプト (ES module ではない) で `window.HirakuPets = { ROLES, STATES, FRAME, create(THREE, opts) }` を出す。three は呼ぶ側が渡す |
| `viewer.html` | 専用のビューア。three r170 と OrbitControls・RoomEnvironment を jsDelivr から読み、`pet3d.js` に渡す |

平面の絵 (`promo/screens/pets.js`、動画 N00 で使用) はそのまま残してあり、ここでは参考にしただけです。

## ビューアの開き方

`viewer.html` をブラウザで開きます (`file://` のままでよい)。three を jsDelivr から読むので、ネットにつながっている必要があります。

- 左の欄で 表示 (並び・1 体・大きさ)、役、状態、地 (生成り・夜)、要素の辺の線・節点のオンオフ、メッシュの細かさ (粗い・細かい)、カメラ (正面・斜め・横)、自動回転、時刻を止める、を切り替えます
- 並びの表示でキャラを押すと、その 1 体の表示に移ります。1 体の表示はドラッグで回せます
- 「大きさ」の表示は、動画 N00 の 1 体の大きさ (184 px) とアプリの一覧の大きさ (48 px) で 10 体を並べた帯です
- 右下の「v1」が版の印です

### URL の引数

いまの表示はいつも URL に写っています。同じ URL を開けば同じ絵になります。

| 引数 | 値 | 既定 |
|---|---|---|
| `view` | `grid` (並び)・`one` (1 体)・`sizes` (大きさの帯) | `grid` |
| `role` | `lead`・`designer`・`packager`・`cae.handling` など (下の表) | `lead` |
| `state` | `idle`・`report`・`pass`・`mismatch`・`working` | `idle` |
| `bg` | `paper` (生成り)・`night` (夜) | `paper` |
| `edges` / `nodes` | `0` で要素の辺の線 / 節点を消す | `1` |
| `mesh` | `coarse`・`fine` | `coarse` |
| `cam` | `front`・`three` (斜め)・`side` | `three` |
| `spin` | `1` で自動回転 (向きは時刻だけで決まる) | なし |
| `orbit` | 1 体の表示でドラッグしたときのカメラ (方位,仰角,距離。ラジアンと単位長) | なし |
| `t` | 時刻 (秒)。あれば時刻を止めてその時刻の姿を描く | なし (動く) |
| `ui` | `0` で左の欄を隠す (書き出し用) | なし |

例: `viewer.html?view=one&role=cae.crash&state=pass&bg=night&t=1.2`

書き出し用に `window.petViewer = { ready, seek(t) }` を出しています。`ready` は three を読み終えて最初の絵を描いたら解ける Promise、`seek(t)` は時刻を止めて t の姿を描きます。

## 役・小道具・色

| id | 名前 | 小道具 | アンテナの玉の色 | 色の出どころ |
|---|---|---|---|---|
| `lead` | まとめ役 | バインダー (クリップボード) | `#e9b872` | `web/js/app.js` の `PET_COLORS` |
| `designer` | 設計役 | 鉛筆 | `#2fa7d6` | 同上 |
| `packager` | パッケージ役 | スパナ | `#8e6bbf` | 同上 |
| `cae.handling` | 操安 | ハンドル | `#3461c8` | `promo/screens/common.css` の `--d-handling` |
| `cae.powertrain` | 動力 | 電池 (稲妻の印) | `#e2b13d` | `--d-powertrain` |
| `cae.aero` | 空力 | 吹き流し (ポールつき) | `#2fa2c3` | `--d-aero` |
| `cae.crash` | 衝突 | 衝突試験の的 (4 分割の黄と黒) | `#d6492f` | `--d-crash` |
| `cae.strength` | 強度信頼性 | I 形の梁 | `#55524b` | `--d-strength` |
| `cae.nvh` | 音振 | 音叉 | `#8a5cc2` | `--d-nvh` |
| `cae.thermal` | 熱 | 温度計 (赤い液) | `#e0682a` | `--d-thermal` |

ほかの色もすべてリポジトリの中の値です: 体の応力の色の 5 段は `common.css` の `--s1`〜`--s5`、地は `--paper` #f4f1ea と `--night` #131311、線はインク #1b1a17、✓ は `--pass`、? は `--miss`、温度計の液は `--s5`、的の黄は `--s4`、梁と金物は `--ink-faint`。外から持ってきた値はありません。

## キャラの作り方

- 体: 正二十面体を分割した球 (three の `IcosahedronGeometry`、粗い = 分割 2 で節点 92・要素 180、細かい = 分割 4 で節点 252・要素 500) を節点と三角の要素に組み直し、てっぺんの節点 (アンテナの根もと) 以外を少しずつずらしています。ずらし方は役の id から作った種の乱数で、毎回同じです。形は少し下ぶくれの楕円体です
- 応力の色: 節点ごとに値を計算し (脚・腕・アンテナの付け根が高く、てっぺんと背中が低い)、要素の中で補間してから 5 段の帯に分けて塗ります。解析中 (`working`) は値に時刻を足して帯を流します
- 線: 要素の辺はシェーダーで重心座標から描き、画面の上で決まった太さ (キャラの大きさに合わせて 0.8〜2.4 px) になります。小さく出るときは辺を薄くし、節点の点を消して、色の帯が読めるようにしています。輪郭は裏返した殻 (法線の向きに少し太らせた黒) で描きます
- 光: 部屋の環境光 (RoomEnvironment)、上からの主光 (柔らかい影)、後ろからの縁の光、体のシェーダーのフレネルの縁の光、足もとの接地の影。色は sRGB で管理し、トーンマップは色を崩しにくい Neutral を使っています
- 動き (`update(t, state)` は時刻と状態だけで姿が決まる): idle = 上下・呼吸・まばたき・アンテナの揺れ、report = 前に乗り出す、pass = 小さく跳ねて頭の上に ✓、mismatch = 体ごと首をかしげて頭の上に ?、working = 帯が流れる・目が小道具を見る

## 試験

`promo/tests/test_pets.py`: `pet3d.js` の役が 10 体でアプリの役の id (`hiraku/contract/hiraku.schema.json`) とそろっていること、色がリポジトリの値と同じこと、姿を決めるところで時計や乱数を使っていないこと。playwright とネットがあるときだけ、ビューアを開いて `window.petViewer.ready` が解けることも見ます (無ければ飛ばします)。

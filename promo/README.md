# promo — 動画のための画面と書き出し

動画だけに出る画面 (アプリの外) と、画面をコマごとに書き出して声・字幕・背景の音楽を付ける道具です。
声の原稿、合成した声、書き出したコマや動画は、リポジトリの外の作業フォルダに置きます。

## 画面 (`screens/`)

| ファイル | カット | 長さ |
|---|---|---|
| `n00.html` | はじめの一言: HIRAKU の名前と、7 つの分野のエージェントの札が順に灯る | 7 s |
| `n05.html` | 題字 (冒頭): 閉じた両開きの扉の合わせ目から暖かい光が漏れ、扉が手前へ開くと光があふれて奥の車両が見える。車両が開いて試験の格子になり、題字が乗る。12 枚のタイルは着地したあと、それぞれの試験のやり方を線画でくり返す (`tests.js`・`tests.css`) | 5 s |
| `n08b.html` | 数字: 99 % と 16 %、棒、出典 | 10.0 s |
| `n18.html` | 題字 (締め): 縦の光、車両の線画、題字、リポジトリ | 9 s |

- 1920×1080 の配置で作り、長さはすべて `--u` (= min(100vw/1920, 100vh/1080)) の倍数で書いています。1920×1080 の窓でも、3840×2160 の窓でも同じ配置になります
- 下の 170 px は字幕の帯なので、何も置きません (`?guides=1` で帯が見えます)
- 絵はすべて CSS と SVG で、このリポジトリの中で描いたものです。外の画像・素材は使っていません
- N05 の大きな車両は仮の絵です。動画ではアプリの描画を下に敷き、この画面を `?layer=fg` (地を透明にする) で重ねます (`?layer=fg` では大きな車両もタイルも消えます)
- N05 の扉は、画面いっぱいの 2 枚の扉板 (各 960×1080、外側の端が蝶番) です。`n05.times.door_open_s` から `n05.door.open_dur_s` かけて、CSS の 3D (`perspective_px`、`rotateY` で `angle_deg`) で手前に開き、開ききった時刻 (車両が弾ける `burst_s` より前) で消えます。扉は `?layer=fg` でも描くので、開くまでは下に敷いたアプリの絵を隠し、開いたあとは地が透明に戻ります。`?door=slide` で引き戸 (左右に滑って開く) にもなります (比べる用。URL は読み込みのときに 1 回だけ見て、時刻には関わりません)
- N05 のタイルは、試験ごとの線画のループ (1 周 1.6〜2.4 秒) です。段差越え・傾斜台・横風・オフロード走行・DLC (上から見た絵)・急ブレーキ・長時間・発進・衝突・音振・疲労・熱の、試験のやり方だけを見せ、数字・目盛り・合否は出しません。色は黒インクと応力の色 (`--s1`〜`--s5`) だけです。ループはタイルが着地してから始まり (`--t0`)、すべて CSS のアニメーションなので時刻で止まります
- 字体は Windows に入っている Bahnschrift (英字と数字) と Noto Sans JP (日本語) を使い、無ければ順に代わりの字体になります

### N08b の数字の出典

割合は `n08b.html` の中で元の値から計算します (値と出典は `DATA` にも書いています)。

- 99 %: 中小企業庁「2025 年版 中小企業白書」付属統計資料 1 表 (産業別規模別企業数、2021 年 6 月時点)、製造業の行。企業 337,490 のうち中小企業 335,552 (99.4 %)。https://www.chusho.meti.go.jp/pamflet/hakusyo/2025/chusho/excel/f1.xlsx (2026-11-02 に表で確かめた)
- 16 %・67.9 %: 株式会社メイテックの発表「CAEツールの導入率は43.4％。1万人超の企業では67.9％が導入済み」(2017-09-26、PR TIMES の配信を載せた日刊工業新聞の企業リリース https://www.nikkan.co.jp/releases/view/1612)。製造系エンジニア 500 人へのインターネットリサーチ (2017-08-25)。自社に CAE ツールを「導入している」と答えた技術者の割合を勤め先の従業員数別に出したもので、会社の数の割合ではない。従業員 100 人以内 16.0 %、1 万人超 67.9 %。画面では発表の許す形で「fabcross for エンジニア」調べと書く (2026-11-03 に原文で確かめた)

### 時刻で止める入口

画面のすべての動きは、1 つの時刻 t (秒) で決まります (`screens/timeline.js`)。

| 使い方 | 中身 |
|---|---|
| `n18.html` | 実時間で再生して繰り返す (下見)。スペースで一時停止、←→ で 1/30 秒ずつ |
| `n18.html?t=5` | t = 5 s で止めて描く |
| `await window.promo.seek(t)` | t へ動かし、その絵が描かれてから返る |
| `window.promo.duration` | 画面の長さ (秒) |
| `?layer=fg` | 地を透明にする (ほかの映像に重ねる) |

CSS のアニメーションは Web Animations API で止めて `currentTime` を t に合わせるので、同じ t からは毎回同じ絵が出ます (書き出したコマと撮り直したコマが 1 画素も違わないことを確かめました)。

アプリの画面 (`web/`) も同じ形の口を持っています (`?t=`、`?video=1`、描き終えてから返る `window.hiraku.seek(t)`)。`export.py` はどちらの画面も同じに扱います。

### 配置の決まり (`screens/layout.json`)

画面の配置と時刻の数値は `screens/layout.json` の 1 か所にだけ書きます。各カットの HTML と CSS には座標も時刻 (`--t-*`) も書きません。画面部 (アプリの画面) も N05 などの組み立てでこのファイルを読みます。

| ファイル | 中身 |
|---|---|
| `screens/layout.json` | 決まりの元。最上位のキーは `stage` (1920×1080)、`subtitle_band` (字幕の帯 y 910・高さ 170)、`safe` (余白の内側 x 96・y 60・1728×850)、`min_text_px` (文字の最小 18 px、日本語・英数字とも)、`colors` (`night`・`ink`・`tile_bg`・`tile_border`)、カットごとの `n00`・`n05`・`n08b`・`n18` (要素の位置と大きさ、長さ `duration_s`、時刻 `times`)、`check` (点検する時刻と、点検で許すもの。理由つき)。単位は名前で示す: `x`・`y`・`w`・`h` は設計の px、ほかの長さは `_px`、秒は `_s`、角度 (度) は `_deg` |
| `layout_build.py` | `layout.json` から `screens/layout.data.js` (`window.PROMO_LAYOUT`) を書く。画面は `file://` で開くので JSON を読み込めず、スクリプトにして渡す |
| `screens/layout.data.js` | 生成物。手で直さない。`layout.json` と違うと試験が落ちる |
| `screens/layout.js` | 決まりから座標を計算する。`<script src="layout.js" data-cut="n05">` で、そのカットの値を CSS 変数にして入れる (長さ `n05.title.name.y` → `--title-name-y: calc(62 * var(--u))`、`n05.tile.label.size_px` → `--tile-label-size-px`、時刻 `n05.times.burst_s` → `--t-burst: 0.95s`、色 `colors.tile_bg` → `--tile-bg`、動きの値は倍率 `n05.hero_motion.push_scale` → `--hero-motion-push-scale: 1.04` (単位なし)・長さ `burst_dur_s` → `--hero-motion-burst-dur: 0.4s`・緩急 `n05.ease.tile` → `--ease-tile: cubic-bezier(0.2, 0.9, 0.3, 1.15)`・角度 `n05.door.angle_deg` → `--door-angle: 105deg`)。`gridCells(n05.grid)` は格子の各マス (`x + 列 × (tile_w + gap_x)`、`y + 行 × (tile_h + gap_y)`) を返し、N05 の 12 枚のタイルは `n05.tests` の順にこれで置く |

```
py promo/layout_build.py
```

`layout.json` を直したら、この 1 行を回してから画面を開きます。書き換えの前後で 4 カットを 4 時刻ずつ撮って比べ、16 枚すべて 1 画素も違わないことを確かめました。いまの形 (最上位にカットを置き、時刻も持つ形) に替えたときも、N00・N05・N18 を 4 時刻ずつ撮って 12 枚すべて 1 画素も違わず、`bgm.py` の WAV も 1 サンプルも違わないことを確かめました。各カットの HTML の `data-duration` は `duration_s` と同じ値にします (試験で見ます)。

### 配置の点検 (`layout_check.py`)

画面を決まった時刻で開き、名前つきの要素 (`data-box="名前"`。名前は点で区切る: `n05.title.name`・`n05.tile.step`・`n05.tile.step.label`・`n08b.fig.left.num`・`n08b.bars.large.value`・`n18.url`) と、文字を持つ見える要素 (透明度 0.05 未満・`visibility: hidden`・大きさ 0・親に切られて見えないものは除く) の箱を集めて、次を一覧にします。

- `band`: 字幕の帯 (y ≥ 910) に入ったもの。帯の飾り (`?guides=1`) は `layout.json` の `band_exempt` で除く
- `outside`: 画面 (0〜1920 × 0〜1080) の外にはみ出したもの
- `escape`: 名前つきの要素が、いちばん近い名前つきの親の箱からはみ出したもの (N08b の棒の値が棒の欄から出る、など)。動きの途中だけ出るものは `allow_escape` で px まで許す (N08b の説明の字が下から 14 px 上がってくる)
- `overlap`: 名前つきの要素どうしの重なり。意図した重なりは `allow_overlap` で許す (N18 の光と車両)
- `overflow`: 文字のあふれ (scrollWidth > clientWidth + 1、高さも同じ)。`allow_overflow` で、字の大きさに対する割合まで許せる (行の高さが字体の高さより低く、箱の下に数 px 出るもの)
- `small`: 1080p で最小 (`min_text_px` = 18 px、日本語・英数字とも) を下回る文字。この値はこのリポジトリの決まりで、外の規格ではありません

```
py promo/layout_check.py promo/screens/n05.html <出力>
py promo/layout_check.py all <出力>
py promo/layout_check.py "http://127.0.0.1:8781/?run=sample&video=1" <出力> --at 2 8 15
```

時刻は `layout.json` の `check.times` (アプリは `app`) から取り、`--at` で変えられます。出力のフォルダはリポジトリの外を渡します。結果の `result.json`、表の `report.md`、時刻ごとの絵 (長辺 1280 px、問題の箱を赤い枠で描く)、全部を並べた `sheet.jpg` を書きます。問題が 1 件でもあれば終了コードは 1 です。canvas (WebGL) に描かれた文字は見えません。4 カットは 0 件です。

## 書き出し (`export.py`)

ブラウザ (headless Chromium) を 1920×1080 の配置のまま 2 倍の解像度で開き、コマごとに PNG で撮って ffmpeg でつなぎます。

`playwright` は動画の書き出しにだけ要るので、HIRAKU 本体の `requirements.txt` には入れず `promo/requirements-video.txt` に書いています。HIRAKU の venv ではなく、PC の Python (Windows では `py`) に入れて、その Python で `export.py` を回します。ffmpeg は PATH に置くか、環境変数 `FFMPEG` にその場所を書きます。

```
py -m pip install -r promo/requirements-video.txt
py -m playwright install chromium
```

```
py promo/export.py stills promo/screens/n18.html <作業>/stills --at 0.5 3 8
py promo/export.py frames promo/screens/n18.html <作業>/frames/n18 --fps 30
py promo/export.py encode <作業>/frames/n18 <作業>/clips/n18_1080p.mp4 --height 1080
py promo/export.py encode <作業>/frames/n18 <作業>/clips/n18_2160p.mp4 --height 2160
```

3840×2160 の PNG のコマから、1080p (Lanczos で縮小) と 4K の H.264 (BT.709) を作ります。

アプリの再生は、サーバー (`python -m hiraku.webserve`) を立ててから URL で渡します。`--gpu` を付けると WebGL をグラフィックカードで描きます (付けないとソフトウェア描画)。

```
py promo/export.py frames "http://127.0.0.1:8765/?run=sample&video=1" <作業>/frames/app --gpu
```

書き出す長さは、画面が持つ長さ (`window.promo.duration`、アプリでは `window.hiraku.player.duration`) です。アプリの再生は、ふつうは間合いを整えた時間の軸で進みます (event どうしの短い間を延ばし、長すぎる間は切る)。記録の時刻のまま撮りたいときは、URL に `&pace=0` を足します。見本の記録では、間合いを整えると 19.73 秒、`pace=0` では 9.00 秒でした。`frames` は、はじめに読んだ長さを表示します。

この PC で測った 4K の 1 コマの時間:

| 画面 | 時間 |
|---|---|
| N18 (CSS) | 0.39 s |
| N00・N08b (CSS) | 0.13〜0.16 s |
| アプリの見本の再生 (three.js、`--gpu`) | 0.22 s |
| 同じ (ソフトウェア描画) | 0.45〜0.57 s |

3 分 (5400 コマ) は、GPU ならおよそ 20〜35 分です。1 コマの PNG は約 1.2 MB です。

## 声と字幕 (`voice.py`, `subs.py`)

声はこの PC で動かす AivisSpeech Engine (VOICEVOX と同じ形の HTTP の口) で合成します。原稿はタブ区切りで、1 行が 1 文です。

```
カット<TAB>枠 (0:00-0:07)<TAB>日本語 (字幕に出す形)<TAB>英語
```

読み違える語は、読みの表 (`書いた形<TAB>読ませる形`) で直します。字幕は書いた形のまま出します。

```
py promo/voice.py speakers
py promo/voice.py reading <原稿> --speaker <ID> --readings <読みの表>
py promo/voice.py synth   <原稿> <作業>/voice --speaker <ID> --readings <読みの表>
py promo/subs.py layout <作業>/voice <作業>/subs/n00 --cuts N00 --hot "99 %"
py promo/subs.py burn <作業>/clips/n00_1080p.mp4 <作業>/subs/n00/subs.ass <作業>/clips/n00_final.mp4 --audio <作業>/subs/n00/narration.wav
```

- `synth` は文ごとの WAV と `voice.json` を書き、カットごとに声が枠に入るかを表で出します
- `synth --only N07_2.wav N08b_2.wav` は、名前を挙げた文だけ合成し直し、ほかの文は前の声をそのまま使います (同じ文でも合成のたびに長さが 0.03〜0.05 秒ほど変わるので、直していない文の長さを動かさないため)
- `layout` は各カットの文を枠の頭から並べ、字幕 (ASS) と声をつないだ WAV を書きます。1 行に入らない日本語の文は「、」で分け、分け目は声の間 (無音) に合わせます
- 前のカットの声が終わっていなければ、次のカットの声はそのあとまで待ちます (声は重ならない)。枠を超えたカットと、何秒超えたかを表示します
- 「、」で分けるときは、行の数をいちばん少なくし、そのなかで行の幅がいちばんそろう分け方を選びます
- `layout --script <原稿>` は、枠と文を原稿から取り、声は合成したものをそのまま使います (枠を変えるたびに合成し直さなくてよい)。合成したあとに文が変わった文は、合成し直すように表示します
- `burn` は下の 170 px に黒インクの帯を敷き、字幕を焼き込みます。日本語は 48 px の太字で y = 995、英語は 30 px で y = 1040 に固定 (どちらもいつも 1 行)。座標は 1920×1080 で書き、4K では libass が拡大します

声のモデル: 「阿井田 茂」(制作 古山キリヲ)、Aivis Common Model License (ACML) 1.0。ライセンスの本文はエンジンの `GET /aivm_models` の `manifest.license` で読めます (営利の利用も可、クレジットの表記は任意)。

## 背景の音楽 (`bgm.py`)

音はすべて `bgm.py` の中で numpy と scipy で合成しています。外の曲・音源・サンプルは使っていません。

拍やリズムの無い、持続音と粒の曲です。

- ドローン: D と A (D2・A2・D3) の持続音。声部ごとに長さの違う周期 (11.3・14.7・19.1 秒) で、音量がゆっくり膨らんで引きます
- 和音の色: 話の区切りごとに、ニ長調の 5 音音階 (D E F# A B) から 1〜3 音を薄く足し、区切りの前後 2 秒で次の色へ移ります
- 粒: 色の音とドローンの D・A を 2〜3 オクターブ上 (MIDI 84〜104) で短く鳴らし、時刻をばらばらに散らします。1 秒あたりの粒の数は、N00〜N04 が 0.7〜0.9、題字の N05 が 6、課題の N06〜N08b が 0.5、仕組みの N09〜N14 が 2.5、N15〜N17 が 4.5、N18 が 1.5 (区切りの前後 1 秒でなめらかに変わる)
- 画面の時刻に合わせる粒は、画面と同じ `screens/layout.json` の `times` を読みます: N00 で札が灯るたびに 1 粒ずつ上がる (`n00.times.light_s`・`step_s`)、N05 で車両が開く瞬間に粒がはじける (`n05.times.burst_s`)、N18 で光が開く瞬間に 5 粒 (`n18.times.open_s`)
- 残響は、減衰するノイズ (残響時間 4.5 秒) を畳み込みます。はじめの 4 秒で現れ、最後の 3 秒で消えます
- 乱数は 0.5 秒ごとの区間に (種, 区間の番号) で作る生成器を使うので、同じ種なら、どこから作っても同じ音になります (`render --start --end` で一部だけ作れ、全体から切り出したものと一致します)

```
python promo/bgm.py render <原稿> <作業>/bgm/bgm.wav
python promo/bgm.py mix <作業>/bgm/bgm.wav <作業>/subs/all/narration.wav <作業>/bgm/mix.wav
py promo/subs.py burn <映像> <作業>/subs/all/subs.ass <出力>.mp4 --audio <作業>/bgm/mix.wav
```

`mix` は、音楽をはじめから終わりまで声より 15 LU 下 (`--under-voice-lu`) の一定の大きさにします (声の下で下げない)。`--duck-db 9` のように 0 より大きくすると、声の切れ目で音楽をその分だけ上げる作りになります (下げ始めは声の 0.3 秒前、声が止んでも 0.6 秒は下げたまま)。足したものは全体で -16 LUFS (`--lufs`) にし、真のピークを -3 dBFS (`--true-peak`) に抑えます (先読みの短いリミッター)。大きさと真のピークは ffmpeg の ebur128 (ITU-R BS.1770) で測ります。numpy と scipy (HIRAKU の `requirements.txt` にあるもの) と ffmpeg で動きます。

## 試験

```
python -m unittest promo.tests.test_export promo.tests.test_subs promo.tests.test_bgm promo.tests.test_layout
```

`test_layout` のうちブラウザを使う試験 (点検の見本の画面、N05 のタイルと格子、N08b の棒の比率と値の字、4 カットが 0 件) は、playwright の無い環境 (HIRAKU の venv) では飛ばします。

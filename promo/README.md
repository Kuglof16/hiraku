# promo — 動画のための画面と書き出し

動画だけに出る画面 (アプリの外) と、画面をコマごとに書き出して声・字幕・背景の音楽を付ける道具です。
声の原稿、合成した声、書き出したコマや動画は、リポジトリの外の作業フォルダに置きます。

## 画面 (`screens/`)

| ファイル | カット | 長さ |
|---|---|---|
| `n00.html` | はじめの一言: HIRAKU の名前と、7 つの分野のエージェントの札が順に灯る | 7 s |
| `n05.html` | 題字 (冒頭): 車両が開いて試験の格子になり、題字が乗る | 5 s |
| `n08b.html` | 数字: 99 % と 5 %、棒、出典 | 9.7 s |
| `n18.html` | 題字 (締め): 縦の光、車両の線画、題字、リポジトリ | 9 s |

- 1920×1080 の配置で作り、長さはすべて `--u` (= min(100vw/1920, 100vh/1080)) の倍数で書いています。1920×1080 の窓でも、3840×2160 の窓でも同じ配置になります
- 下の 170 px は字幕の帯なので、何も置きません (`?guides=1` で帯が見えます)
- 絵はすべて CSS と SVG で、このリポジトリの中で描いたものです。外の画像・素材は使っていません
- N05 の大きな車両と格子のタイルは仮の絵です。動画ではアプリの描画を下に敷き、この画面を `?layer=fg` (地を透明にする) で重ねます
- 字体は Windows に入っている Bahnschrift (英字と数字) と Noto Sans JP (日本語) を使い、無ければ順に代わりの字体になります

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

- 音色: ずらした 2 つの発振器を低域だけ通したパッド、減衰する倍音を重ねた撥弦の音 (アルペジオと鐘)、サイン波のベース、短いノイズのハイハット、ノイズのライザーと低い一撃、減衰するノイズを畳み込む残響
- 構成は原稿のカットに沿って 8 つの節に分け (はじめ・問い・題字・課題・HIRAKU の仕事・積み上げ・締め・終わり)、節の頭は最寄りの拍に合わせます (92 BPM)。ニ長調
- 画面の時刻に合わせる所は、画面の HTML の値を読みます: N00 で札が灯るたびに鐘 (`--t-light`・`--t-step`)、N05 で車両が開く瞬間に一撃 (`--t-burst`、その前の 2.4 秒にライザー)、N18 で光が開く瞬間に鐘 (`--t-open`)
- 最後の 2 秒で消えます

```
python promo/bgm.py render <原稿> <作業>/bgm/bgm.wav
python promo/bgm.py mix <作業>/bgm/bgm.wav <作業>/subs/all/narration.wav <作業>/bgm/mix.wav
py promo/subs.py burn <映像> <作業>/subs/all/subs.ass <出力>.mp4 --audio <作業>/bgm/mix.wav
```

`mix` は、声の無い所で音楽を -24 dBFS (`--music-db`) にし、声の 0.3 秒前から 9 dB (`--duck-db`) 下げ、声が止んでも 0.6 秒は下げたままにします (声の切れ目が 0.9 秒までなら上げ戻さない)。numpy と scipy (HIRAKU の `requirements.txt` にあるもの) で動きます。足したものの山が -1 dBFS を超えるときは全体を下げます。

## 試験

```
python -m unittest promo.tests.test_export promo.tests.test_subs promo.tests.test_bgm
```

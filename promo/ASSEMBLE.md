# assemble.py — カットを 1 本の動画につなぐ

原稿 (narration.tsv) の枠を時間の軸にして、カットごとの映像 (できた動画・静止画・文字の札) を並べ、カットの境目を「つなぎ」で、暗い画面を「見た目の層」で仕上げる。最後に字幕と音を `subs.py` の焼き込みと同じ形で入れる。

```
py promo/assemble.py plan  promo/assembly.json --work <作業フォルダ>
py promo/assemble.py build promo/assembly.json <出力.mp4> --work <作業フォルダ> \
    [--height 1080|2160] [--subs <subs.ass>] [--audio <mix.wav>]
```

- `plan` は時刻の表 (各カットの始まり、つなぎの始まり | 境目 | 終わり、全体の長さ) を出すだけ
- `--work` は表の中の `${WORK}` に入るフォルダ。ほかの名前は `--set NAME=VALUE`
- `--height 2160` で 4K。同じ表のまま作れる (下の「映像」の高さごとの指定を参照)
- ffmpeg は PATH か環境変数 `FFMPEG`。Python は標準ライブラリだけ
- 途中のファイルは `<出力名>.parts/` に残る (カットごとの mp4 と、字幕の前の `film.mp4`)

## 時刻の扱い

- 原稿の枠が正。カットの映像は枠の長さに合わせる: 動画が長ければ後ろを切り、短ければ最後のコマで止める
- つなぎ (長さ d) は **境目をまたいで前後に d/2 ずつ**。枠の切れ目 = つなぎの真ん中なので、全体の長さと声・字幕の時刻は変わらない
- そのため各カットは枠の前に d/2 (動画は最初のコマで止めた絵)、後ろに d/2 (動画は続きのコマ、無ければ最後のコマ) を足して作り、`xfade` の offset = 境目 − d/2 で重ねる
- d は 30 fps で偶数コマに丸める (前後に整数コマずつ割るため)。0.25 秒 → 8 コマ (0.267 秒)、0.5 秒 → 16 コマ (0.533 秒)
- 枠の時刻はコマの格子 (1/30 秒) に乗っていること。乗っていなければ止まる

## つなぎ (カットの境目ごと)

| 種類 | 見え方 | ffmpeg での作り方 |
|---|---|---|
| `cut` | 硬く切る | `concat` |
| `dissolve` | 重ねて溶ける | `xfade=transition=fade` |
| `light` | 暖かい光 (#f6ead0) が強まり、次のカットが光の中から出る | 両側のカットで: 自分の絵を `gblur` した光を `blend` (screen) で強め、続いて #f6ead0 の `color` へ `blend` で寄せる。強さは境目で 1 になる時刻の式 (smoothstep)。その上で `xfade=fade` |
| `grain` | 粒が増えて砂のように崩れ、次のカットに組み上がる | 両側のカットで: 粗い粒 (`noise` を `gblur` した灰色の層) を `blend` (overlay) で境目に向けて強める。境目は `xfade=transition=custom`: 画素ごとの切り替わりの時刻を「なめらかな値ノイズ 2 段 (斑) + 2 px の砂粒ごとの固定の乱数」で決め、斑ごとに砂のふちでほどける |

`xfade` の custom 式は `st()`/`ld()` を使わずに展開して書いている (xfade は式を複数のスレッドで同時に評価し、変数が混ざるため)。

## 見た目の層 (カットごと)

| 層 | 作り方 |
|---|---|
| `grain` (フィルムの粒) | 灰色の `color` に `noise` (明るさだけ、毎コマ変わる) をかけ、`gblur` で 1 px より少し大きい粒にし、`blend` (overlay、不透明度 0.30) で明るさに重ねる。乱数の種はそのカットの始まりのコマ番号 + 1 で、同じ入力なら同じ画素になる |
| `bloom` (光のにじみ) | `lutrgb` で明るい所 (150/255 より上) だけを残し、`gblur` (σ 18 px) でぼかして `blend` (screen) で重ねる。強さ = 表の `bloom` (不透明度) |

ぼかしの大きさや粒の大きさは 1080p の画素で決め、出力の高さに合わせて拡大する。

## 字幕と音

`--subs` があれば `promo/subs.py` の `burn_command` で、つないだ映像の上に帯と字幕を焼き、`--audio` の音を入れる (字幕はつなぎの上に乗る)。`--subs` が無ければ映像はそのままで、`--audio` があれば音だけ入れる。

## 組み立ての表 (JSON)

見本は `promo/assembly.json` (いまの 21 カット)。

```json
{
  "script": "${WORK}/script/narration.tsv",
  "fps": 30,
  "band": {"top": 910, "color": "#1b1a17"},
  "default_join": {"kind": "dissolve", "seconds": 0.25},
  "segments": [
    {"cuts": ["N00"], "video": {"1080": "${WORK}/clips/n00_1080p.mp4", "2160": "${WORK}/clips/n00_2160p.mp4"},
     "join": {"kind": "dissolve", "seconds": 0.3}},
    {"cuts": ["N01", "N02"], "video": "${WORK}/clips/n01_n02_video_1080p.mp4"},
    {"cuts": ["N03"], "still": "${WORK}/storyboard/layouts/_tools/png/N03.png", "paint_band": true},
    {"cuts": ["N17b"], "paint_band": true,
     "card": {"background": "#131311", "lines": [
       {"text": "…", "x": 160, "y": 300, "size": 64, "font": "C:/Windows/Fonts/YuGothB.ttc", "color": "#f1ece0"}]},
     "look": {"grain": true, "bloom": 0.35},
     "join": {"kind": "light", "seconds": 0.6}}
  ]
}
```

| 項目 | 意味 |
|---|---|
| `script` | 原稿。相対パスなら表のファイルからの相対 |
| `fps` | 既定 30 |
| `band` | `paint_band` で塗る下の帯 (1080p での y と色)。絵コンテの下絵に描かれた字幕を消すため |
| `default_join` | `join` を書かないカットの、次へのつなぎ |
| `segments[].cuts` | この映像が受け持つカット (原稿の順に、抜けなく)。複数なら枠を続けて 1 本 |
| `video` / `still` / `card` | どれか 1 つ。`video` は文字列か、高さごとの `{"1080": …, "2160": …}` (無い高さは一番大きいものを拡大) |
| `card` | 文字の札: 背景色と行 (`text`・1080p での `x` `y` `size`・`font` のファイル・`color`) |
| `paint_band` | 下の帯を塗る |
| `look` | `{"grain": true/false, "bloom": 強さ 0〜1}` |
| `join` | 次のカットへのつなぎ: `{"kind": "cut|dissolve|light|grain", "seconds": 秒}`。最後のカットのものは使わない |

表の検査 (作る前に止まる): カットが原稿と同じ順で抜けなく並ぶこと、枠が続いていること、つなぎの半分が前後の枠の半分より短いこと、置き換えの名前に値があること。

## 試験

```
python -m unittest promo.tests.test_assemble
```

表の読み込み、時刻 (境目がつなぎの真ん中・全体の長さが変わらない)、フィルタの組み立てを ffmpeg 無しで確かめる。ffmpeg と ffprobe があるときは、小さな入力から 3 秒の動画を 2 回作り、コマ数と、2 回のコマの画素が同じこと (粒の乱数が固定) を確かめる。

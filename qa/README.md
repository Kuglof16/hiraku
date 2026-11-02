# qa — 品質保証の道具

HIRAKU 本体は `qa/` を読み込まない。試験 (`tests/verification/`) と点検のときだけ使う。

| ファイル | すること |
|---|---|
| `known_answers.py` | 解析の道具が合わせるべき閉じた式の答え (段差越え・輪の荷重・梁・門形・固有振動数・傾斜台・制動・単位の換算)。値はすべて式から計算し、式の出どころを関数ごとに書いた |
| `frame2d.py` | 平面の骨組みの剛性法。閉じた式の無い骨組みで K1 と比べる二つ目の意見。`hiraku.solvers` とはコードを共有しない |
| `check_run.py` | `runs/<run_id>/` が契約と hooks の決まり (H1〜H4) に合っているかを点検する |
| `judge_scan.py` | ブランチを審査員の目で読み、公開してはいけないものと、人が見るべき所を並べる |

## 走らせ方

venv の Python で、リポジトリの一番上から。

```
python -m unittest                                   # 全部の試験 (skip は理由つきで出る: -v)
python -m qa.check_run runs/<run_id>                 # 1 回の実行の記録を点検
python -m qa.check_run --all --recompute             # runs/ の全部。予測を K0 で計算し直す
python -m qa.judge_scan --ref main                   # main を審査員の目で
```

`check_run` は誤りがあると終了コード 1、`--strict` で注意も 1 にする。`judge_scan` は公開してはいけないものがあると 1。

## 照合の考え方

- 正解は閉じた式と手計算。`known_answers.py` の式は `tests/verification/test_known_answers.py` で別の道筋 (sympy で梁の微分方程式を解く、剛性法、差分、数値積分) からもう一度出して確かめている
- 解析部の道具がまだ無い間、その試験は「何が無いか」を書いた理由で skip する。道具があって約束 (`hiraku/contract/protocols.py`) を満たさないときは skip ではなく失敗にする
- 道具の `check()` の `pass` は、`expected`・`got`・`tolerance_rel` から計算し直して確かめる (書いてある合否を信じない)

## 審査員の目の点検 (毎日の終わり)

1. `python -m qa.judge_scan --ref main` を回す。言葉と道の型は、リポジトリの外にある pre-commit (`.private/pre-commit` の `WORDS=`・`PATHS=`) から読むので、このリポジトリには書いていない。勤務先の名前を見つけたいときは、そこに足す
2. `error` は全部、`review` は 1 行ずつ人が見る。数字の出典は、使った所と README の両方にあるか
3. 動画に映る機能が既定で動くか: 保存した実行を `check_run` に通し、画面で再生する
4. 結果は統括部に知らせる。他の部署のファイルは直さない

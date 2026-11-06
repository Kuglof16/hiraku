---
name: write-a-design
description: 合う設計の型が無い製品で、設計役 (designer) が設計そのもの (節点・部材・断面・材料・部品・支え) を書き、check_design で確かめ、write_design で版にし、通らなかった試験に答えて次の版を edit_design (変える所だけ) で作る手順。型の版 1 を下書きにする方法と、check_design の断りの直し方
---

# 設計そのものを書く

この手順は、型 (`get_context` の `archetypes`) が無いか、要件に合う型が無いときだけのもの。合う型があれば、今までどおり `propose_design` で版 1 を作り、`revise_design` で直す。

どちらの道でも、数値はツールが計算する。あなたが選ぶのは形と入力 (座標・断面・材料・部品の質量) で、結果の数 (質量の和・干渉の数・応力) は書かずに、ツールの答えと記録から読む。

## 1. 決まり

- 形は契約の `design`。`nodes`・`members`・`sections`・`materials`・`parts`・`supports`・`variables` (無ければ `{}`) と `name`。`design_version`・`parent_version`・`change_reason` はツールが付ける。`archetype` は型が作った設計の印なので書かない (書いてもツールが外す)。`modules` は書かない (モジュールに分けた設計は型が作る)。
- 座標系: x は前、y は左、z は上。原点は前の車軸の下の地面。前の車輪の中心が x 0 で、z はその車輪の半径。後ろの車軸は x が負の側 (ladder_4w の設計と同じ)。
- 単位: 長さと座標は mm、重さは kg (欄の名前 `xyz_mm`・`size_mm`・`mass_kg` のとおり)。m や cm で書かない。
- 部材 (`members`): 両端の節点 (`node_a`・`node_b`)、断面のキー (`section`)、材料のキー (`material`)。人に見せる名前は `name` (日本語)。
- 断面 (`sections`): `shape` (`rect_tube`・`round_tube` など) と `dims_mm` (角の管なら `b`・`h`・`t`、丸い管なら `d`・`t`)。値の出どころは `source` に。
- 材料 (`materials`): `name`・`E_MPa`・`nu`・`density_kg_m3`・`yield_MPa`・`source`・`verified`。出典を確かめていない値は `verified: false` にし、`source` にそう書く。
- 支え (`supports`): 地面に触れるもの。`kind` は `wheel`・`caster`・`leg`。`position_mm` は車輪の中心、`radius_mm`、`suspension` の `type` (`rigid` など)、`actuation` の `drive`・`brake`・`steer`。どの支えにも `frame_node` (その支えの荷重が入る骨組みの節点) を書く。
- 部品 (`parts`): `id`・`name`・`kind`・`box` (`center_mm` と `size_mm`、質量の同じ箱)・`mass_kg`。どの部品にも `carried_by` (その重さを受ける骨組みの節点) を書く。書かないと、ツールはその部品を車輪の側 (ばね下) の部品として扱う。
- 部品の `kind`:
  - `cargo`: 荷。質量の和は要件 Q40 (積載) にする
  - `occupant`: 人。1 人の質量は要件 Q33。人が乗らない製品には置かない (人の部品が無い設計は人を載せない)
  - `wheel`: 車輪。支えごとに 1 つ、`carried_by` はその支えの `frame_node`
  - `battery` 電池・`motor` モーター・`controller` 制御器・`seat` 座席・`body` 板や囲い
- 骨組みは部材で書く。骨組みを囲む大きな箱の部品 (`kind: frame` など) は置かない。箱が部材と部品の全部に重なり、干渉の数が増える (試しでは箱 1 つで 26 件)。
- 全部の部材が、部材をたどって支えの `frame_node` に届くようにする。ばらばらの部材や、支えにつながらない棚があると、骨組みの解析が解けない。

## 2. 型の版 1 を下書きにする

型が製品に合わず、それでも形の近い型があるとき (例: 介護ベッドの骨組みを台車に):

1. 下書きに使う型で `propose_design` を呼び、版 1 を作る (要件から座標が出る)。
2. `get_context(sections=["design"])` で要約を見て、`design_part` (`nodes`・`members`・`parts`・`supports`・`sections`・`materials`・`variables`) と `offset`・`limit` で中身を少しずつ読む。
3. 要る所を変えた設計の全体を、`write_design` (`based_on` に型の名前) で版 2 として出す。版 2 は直しの 1 回 (round) に数える。

- モジュールに分かれた型 (ladder_4w) の版の上では `write_design` は使えない (断られる)。その型を変えるときは、モジュールの設計役が `revise_design` で提案する。
- 型が 1 つも無い製品 (`archetypes` が空) では、この下書きは使えない。要件から書く。
- 下書きの設計の `variables` を残したときも、`write_design` で書いた設計は `revise_design` では直さない (変数から組み直すと、あなたが変えた所が消える)。

## 3. 確かめる (check_design)

`check_design(design)` は版を作らずに確かめる。`write_design` も中で同じ確かめをして、`problems` が 1 つでもあれば全部を挙げて断る。

| 断りの文 | 直し方 |
|---|---|
| 契約の design の形に合いません | 文の欄 (足りない欄・余計な欄・型の違い) を直す。`variables` が無ければ `{}` |
| … id が重なっています | id を 1 つずつ別にする |
| … は節点 (nodes) にありません / sections にありません / materials にありません | その節点・断面・材料を足すか、名前を合わせる |
| connects_to の to … どれにもありません | `to` を部品・部材・節点・支え (`<支え>.<取付点>`) の id にする |
| どの支えにも frame_node が無い | 支えごとに、その荷重を受ける節点を `frame_node` に書く |
| 支えまで部材でつながっていない部材 / 部品を載せる節点 | 部材を足してつなぐか、つながらない部材を外す。部品の `carried_by` をつながった節点にする |
| 長さが 1 mm より短い部材 | 両端の節点を別の所にする。座標を m で書いていたら mm に直す |
| 20 m を超える部材 / 原点から遠い節点 | 座標を mm で書き直す |

`notes` は断りではないが、見る:

- 干渉 (`interferences` の `count` と `first`): 止まっているときに重なる組。組み立ての試験 (H1) で赤になる。骨組みを囲む箱や、部材に入り込んだ部品の箱を直す
- `carried_by` の無い部品、どこにも使われていない節点、`frame_node` の無い支え
- 荷 (kind `cargo`) の質量の和 (`cargo_mass_kg`) と要件 Q40 (`Q40_kg`)、人の数 (`occupants`) と要件の載せ方

## 4. 版にする (write_design)

`write_design(design, reason)` を 1 回呼ぶ。`reason` には、どの要件からその形・寸法にしたかを書く。下書きに型を使ったら `based_on`。

- 版 1: 試験の荷重条件と担当の役が決まる。答えの `load_cases` を、まとめ役に 1 行で返す。
- 断られたら、文の問題を全部直して呼び直す。

## 5. 直す (次の版)

まとめ役から通らなかった試験 (合否の記録 ver-…) を渡されたら:

1. `get_context(sections=["judgements", "records"], record_kinds=["verdict", "analysis"])` で、判定と解析の結果 (値・限界・場所 `where`) を読む。
2. 通らなかった理由を記録の値で確かめ、変える所 (部材を足す・断面を太く・部品を動かす など) を決める。変える量は、記録にある不足の量を根拠に選ぶ。
3. 今の版を `get_context` の `design` と `design_part` で読み、id を確かめる。直すときは `edit_design(edits, reason)` で、変える所だけを `edits` に、当てる順に書く。全体を書き直すとき (形を大きく変えるとき) だけ `write_design` で設計の全体を出す。`reason` には、変えた所と理由、答える試験の記録 (ver-…) の ID を書く。ほかの試験を悪くしそうな変更 (重さ・重心・強さ) は、その心配も書く。
4. 新しい版の番号と変えた所を、まとめ役に 1 行で返す。新しい版では全部の荷重条件がやり直しになる。回数は round_limit まで。

### edit_design の edits

`edits` は今の版の写しに上から順に当たる。1 つでも当てられないと、何番目の edit かを言って全部を断る (何も当てず、版も作らない)。当て終わると、`write_design` と同じに `check_design` で確かめて次の版にする (`written_by` と `based_on` は引き継ぐ)。

| op | 欄 | すること |
|---|---|---|
| `add_node` | `id`・`xyz_mm`・(`joint`) | 節点を足す |
| `move_node` | `id`・`xyz_mm` | 節点を動かす |
| `remove_node` | `id` | 節点を外す。部材・部品の `carried_by`・`connects_to`・支えの `frame_node` が使っていれば断る |
| `add_member` | `member` (契約の member の形) | 部材を足す。端の節点・断面・材料は先にあること |
| `remove_member` | `id` | 部材を外す。部品の `connects_to` が使っていれば断る |
| `split_member` | `id`・(`at_node`) | 部材を `at_node` で 2 本 (`<id>_a` と `<id>_b`) に分ける。`at_node` が無い節点なら部材の中点に作り、書かなければ `<id>_mid` を作る。ガセットや横の部材を途中につなぐ前に使う |
| `set_section` | `key`・`section` (断面の record) | 断面を置き換えるか足す |
| `assign_section` | `members`・`section` (断面のキー) | 部材に断面を当てる (一部の部材だけ太くする) |
| `add_part` / `remove_part` | `part` / `id` | 部品を足す / 外す (部材の `part` や `connects_to` が使っていれば断る) |
| `set_part` / `set_support` | `id`・`fields` | 欄を変える。値を `null` にするとその欄を外す。`id` は変えない |
| `set_material` | `key`・`material` | 材料を置き換えるか足す |
| `set_variable` | `name`・`value`・(`basis`) | 変数を置き換えるか足す |

例: 下の棚の前の横の部材を中点で分けて、そこから上へ柱を 1 本足し、柱と分けた 2 本を太い断面にする: `split_member` (id: その部材) → `add_node` (柱の上の節点) → `set_section` (key: 太い断面) → `add_member` (柱) → `assign_section`。

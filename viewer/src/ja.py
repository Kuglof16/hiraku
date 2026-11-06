"""Japanese for the page: the English texts the viewer's data scripts and the tools' records put in a data object.

translate(obj) walks the object (dicts and lists; packed arrays, the dicts with "b64", are left as they are) and
replaces the strings under the display keys (DISPLAY: how, source, value, note, notes, label, where, basis,
assumptions, ...) by Japanese, from exact tables and from templates whose {n} parts (numbers, ids) are put back as
they are: a number is never changed. Keys the page uses as ids (id, kind, cat, quantity, channel, tool, field, name,
source.run, source.design_name, ...) are not touched. A ledger entry's "fn" (a function's dotted name) becomes
Japanese words, and the function's short name goes to the end of its "how" (the 詳しく text). A string no rule
matches stays as it is.

    python -m viewer.src.ja viewer/data      # rewrite every viewer/data/*.js through translate()
"""

from __future__ import annotations

import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

DISPLAY = {"how", "why", "source", "value", "note", "notes", "label", "where", "basis", "assumptions",
           "loading_source", "strain_method", "error"}

LOADING = {"empty": "空車", "driver": "運転者だけ", "two": "2 人", "two_cargo": "2 人 + 荷"}
DIRS = {"above": "上から", "below": "下から", "the left": "左から", "the right": "右から", "the front": "前から",
        "the back": "後ろから"}
MODE_KIND = {"body on springs": "ばねの上の車体", "frame": "フレーム", "roll bar": "ロールバー"}
STATUS = {"ok": "OK", "blocked": "途中で干渉", "no_tool": "工具が入らない", "no_reach": "手が届かない"}

# ---------------------------------------------------------------- function names (ledger "fn")
FNS = [   # dotted name -> (Japanese words, short name)
    ("hiraku.contract.loading.parts_on_board", "乗員・荷物の載せ方", "parts_on_board"),
    ("hiraku.contract.loading.loading_of", "乗員・荷物の決め方", "loading_of"),
    ("hiraku.solvers.k0_handcalc.vehicle.mass_items", "K0 の質量の項目", "mass_items"),
    ("hiraku.solvers.k0_handcalc.library_crash.crush_zone", "K0 のつぶれる区間", "crush_zone"),
    ("hiraku.solvers.k1_frame.modal.add_masses", "K1 の固有振動の質量", "add_masses"),
    ("hiraku.solvers.k1_frame.shaft.driveshaft_modes", "K1 の駆動軸のモード", "driveshaft_modes"),
    ("hiraku.solvers.k1_frame.model.FrameModel.add_body_loads", "K1 の部品の荷重", "add_body_loads"),
    ("hiraku.solvers.k1_frame.model.wheel_node", "K1 の車輪の中心の節点", "wheel_node"),
    ("hiraku.solvers.k1_frame.model.build", "K1 の梁のモデル", "build"),
    ("hiraku.solvers.k1_frame.fatigue.influence", "K1 の疲労の影響係数", "influence"),
    ("shelf.fatigue.road_wheel_forces", "路面からの輪の力", "road_wheel_forces"),
    ("hiraku.solvers.k1_frame.analyze.analyze", "K1 の静的な計算", "analyze"),
    ("hiraku.solvers.k1_frame.analyze.sprung_items", "K1 のばね上の項目", "sprung_items"),
    ("hiraku.solvers.k1_shell.frame.analyze_shell", "K1 のシェルの計算", "analyze_shell"),
    ("hiraku.solvers.shelf.crash.CrashTool.analyse", "衝突 A (1 次元) の計算", "CrashTool.analyse"),
    ("hiraku.solvers.k2_vehicle.crash_beam.mesh._lump_parts", "衝突 B (梁) の質量の割り振り", "_lump_parts"),
    ("hiraku.solvers.k2_vehicle.crash_beam.mesh.build_mesh", "衝突 B (梁) の要素の分け方", "build_mesh"),
    ("hiraku.solvers.k2_vehicle.model.build_vehicle", "K2 の車両モデル", "build_vehicle"),
    ("hiraku.solvers.k3_power_thermal.simulate.network_from_design", "K3 の熱回路", "network_from_design"),
    ("hiraku.solvers.k3_power_thermal.powertrain.from_design", "K3 の走行の質量", "from_design"),
    ("hiraku.solvers.k3_power_thermal.from_design", "K3 の車両 (一つの質量)", "from_design"),
    ("hiraku.solvers.shelf.k3_tools.chain_series", "K3 の電力の段", "chain_series"),
    ("hiraku.solvers.k4_geometry.model.bodies", "K4 の形と組み立ての手順", "bodies, assembly.steps"),
    ("hiraku.designgen.regenerate", "設計の作り直し", "regenerate"),
]


def fn_ja(s: str) -> tuple[str, str] | None:
    """(Japanese words, short names) of a ledger's fn text ('a.b.c (...)', several joined by '; '); None if a
    piece is not a known function."""
    if not isinstance(s, str) or not s.startswith(("hiraku.", "shelf.")):
        return None
    words, shorts = [], []
    for piece in s.split("; "):
        hit = None
        for name, ja, short in sorted(FNS, key=lambda f: -len(f[0])):
            if piece.startswith(name) and (len(piece) == len(name) or not (piece[len(name)].isalnum() or piece[len(name)] in "_.")):
                hit = (ja, short)
                break
        if hit is None:
            return None
        if hit[0] not in words:
            words.append(hit[0])
        if hit[1] not in shorts:
            shorts.append(hit[1])
    return "、".join(words), ", ".join(shorts)


# ---------------------------------------------------------------- exact texts

EXACT = {
    # loading, sides, small words
    **LOADING, "left": "左", "right": "右", "both": "両方", "none": "なし", "no change": "変更なし",
    "False": "いいえ", "True": "はい", "footprint": "取付の面 (footprint)",
    "not in the record": "記録にない", "none in the record": "記録にない",
    "no wall in the record's scene": "記録の場面に壁がない",
    "given in the case (see params)": "試験条件で指定 (条件の値を参照)",
    # ledger: how (common.parts_ledger, ledgers.py)
    "a body in the interference checks; no step of its own in this record; masses are not used by the assembly tool":
        "干渉を調べる物体。この記録には自分の手順がない。組み立てのツールは質量を使わない",
    "lumped translational mass, equal shares on its carried_by frame nodes (node loads in the mass case); no stiffness, "
    "no rotational inertia":
        "carried_by のフレームの節点に等分した、並進だけの集中質量 (質量のケースの節点荷重)。剛性も回転の慣性もない",
    "in the one rigid mass of the 1-D model (mass_summary), no stiffness, does not crush; in K1 at the peak its "
    "d'Alembert force (and moment) is shared equally by its carried_by nodes (hiraku.solvers.k1_frame.model inertia loads)":
        "1 次元モデルの一つの剛な質量に入る (mass_summary)。剛性はなく、つぶれない。ピークの K1 では、その慣性力 "
        "(とモーメント) を carried_by の節点で等分する (K1 の慣性の荷重)",
    "point masses in equal shares on its carried_by nodes; the part's own rotational inertia and its stiffness are not "
    "in the model, it has no contact with the wall (only frame nodes touch it)":
        "carried_by の節点に等分した点の質量。部品自身の回転の慣性と剛性はモデルにない。壁には当たらない "
        "(壁に触れるのはフレームの節点だけ)",
    "in the vehicle mass of the drive only (rolling and climbing resistance, acceleration); no heat":
        "走行の計算の車両の質量にだけ入る (転がりと登りの抵抗、加速)。熱はない",
    "in the vehicle mass of the drive only": "走行の計算の車両の質量にだけ入る",
    "meshed as shell elements (its plate is in the mesh: stiffness, and its mass on those elements)":
        "シェル要素に分けてある (板がメッシュに入る: 剛性があり、質量もその要素に)",
    "unsprung (a wheel kind): left out; the mount forces from K2 carry it":
        "ばね下 (車輪の種類): 入れない。K2 の取付の力がこれを受け持つ",
    "a point mass at its cg: its gravity and d'Alembert force (with the moment of its offset) spread onto the joint "
    "nodes of its carried_by frame nodes; no stiffness":
        "重心に点の質量: 重力と慣性力 (ずれによるモーメントも) を、carried_by のフレームの節点のつなぎ目の節点に配る。剛性はない",
    "a point mass at its cg: its gravity and d'Alembert force (with the moment of its offset) spread onto the joint "
    "nodes of its carried_by frame nodes; no stiffness (half its mass: a suspension kind, K2's split)":
        "重心に点の質量: 重力と慣性力 (ずれによるモーメントも) を、carried_by のフレームの節点のつなぎ目の節点に配る。"
        "剛性はない (質量は半分: サスペンションの種類、K2 の分け方)",
    # ledger: members
    "each member is a body (its tube as an OBB) for the interference checks; no mass":
        "各部材は干渉を調べる物体 (管を OBB として)。質量はない",
    "beams with stiffness (PyNite, subdivided); consistent mass from density x A x L (add_member_self_weight in the "
    "mass case)":
        "剛性のある梁 (PyNite、細かく分ける)。密度 × A × L の整合質量 (質量のケースの add_member_self_weight)",
    "in the one rigid mass; the crush zone's rails fold at their mean crushing force (the only stiffness of the 1-D "
    "model besides the bumper spring from design.variables); K1 at the peak uses every member's stiffness":
        "一つの剛な質量に入る。つぶれる区間のレールは平均のつぶれる力で折れる (設計の変数のバンパーのばねのほかでは、"
        "これが 1 次元モデルのただ一つの剛性)。ピークの K1 はすべての部材の剛性を使う",
    "co-rotational beam elements with plastic hinges (stiffness, yield, break); each element's own mass half to each "
    "of its two nodes":
        "塑性ヒンジのある共回転の梁要素 (剛性、降伏、破断)。要素の質量は両端の節点に半分ずつ",
    "beams with stiffness; their own weight / inertia as distributed loads": "剛性のある梁。自分の重さ / 慣性は分布荷重",
    "the tubes meshed as shell elements (MITC4)": "管をシェル要素 (MITC4) に分けてある",
    # ledger: notes, loading_source, stated
    "the assembly tool uses no masses; every part on board is a body":
        "組み立てのツールは質量を使わない。載る部品はすべて干渉を調べる物体",
    "the crash record states no vehicle_mass": "衝突の記録に vehicle_mass がない",
    "the crash_beam record states no mass assumption": "衝突 (梁) の記録に質量の前提がない",
    "the record states no vehicle_mass": "記録に vehicle_mass がない",
    "the through parts at joints the mesh made through are shell elements too (shell_parts); this layer cannot see "
    "which joints those are and draws them as point masses":
        "メッシュが通す形にしたつなぎ目の部品もシェル要素 (shell_parts)。この表示ではどのつなぎ目かが分からないので、"
        "点の質量として描く",
    "load case params.loading": "試験条件の乗員・荷物",
    "mass (total)": "質量 (合計)", "frame": "フレーム", "parts": "部品",
    "record assumption 'mass ... kg' (1 decimal)": "記録の前提「mass ... kg」(小数 1 桁)",
    "same assumption": "同じ前提", "same assumption, per support": "同じ前提 (支持点ごと)",
    "the design.json read here is not the design the tool weighed, or the loading differs":
        "ここで読んだ設計がツールの量った設計と違うか、乗員・荷物が違う",
    # inputs: source
    "load case params": "試験条件の値", "record assumptions": "記録の前提", "record effects": "記録の結果の説明 (effects)",
    "record scene (the tool's scene.wall)": "記録の場面 (ツールの壁)",
    "record node_motion field": "記録の節点の動き (node_motion)",
    "record fields (mode_shape, per_node vec3 at the frame nodes)": "記録のモードの形 (フレームの節点ごとの 3 成分)",
    "record fields (mode_shape per_mesh_node, stress per_element)": "記録のモードの形 (メッシュの節点ごと) と応力 (要素ごと)",
    "the record's results (wheel_rotation_frequency, motor_rotation_frequency) at the case's speed":
        "試験条件の速さでの記録の結果 (wheel_rotation_frequency、motor_rotation_frequency)",
    "the record's results at the case's speed": "試験条件の速さでの記録の結果",
    "record assembly list (order, direction, path_mm, fastenings, flags)": "記録の組み立ての手順 (順番、向き、道、締結、印)",
    "design.json parts (box centre, size, rpy_deg)": "設計の部品 (箱の中心、大きさ、向き)",
    "record interferences (a, b, depth_mm, state, condition)": "記録の干渉 (組、深さ、状態、条件)",
    "record assumptions (frame on its element nodes, parts on their carried_by nodes)":
        "記録の前提 (フレームは要素の節点に、部品は carried_by の節点に)",
    "record result vehicle_mass (design as loaded, the case's loading)":
        "記録の結果 vehicle_mass (試験条件の乗員・荷物を載せた設計)",
    "the series' largest value; the frame was solved at this instant": "系列の最大の値。フレームはこの瞬間で解いた",
    "record results wheel_load_<support>": "記録の結果 wheel_load_<支持点>",
    "frames.npz": "保存した計算のこま", "load_paths.json": "保存した荷重の経路",
    "load_paths.json mount_path": "保存した荷重の経路 (mount_path)",
    "load_paths.json design (the version's design with these variables)": "保存した荷重の経路の設計 (この版の設計にこの変数)",
    "hiraku.designgen.regenerate; the mesh itself is the saved one": "設計の作り直し (regenerate)。メッシュは保存したもの",
    "C1: no structure reaches into the occupants' space (the least room to their part boxes over the crash)":
        "C1: 乗員の空間に構造が入り込まない (衝突のあいだの、乗員の箱までの最小のすき間)",
    "H1: no two bodies overlap at rest, the driver's seat at either end of its slide too, beyond what the rule allows "
    "(welded joints, bolted mounts up to MOUNT_MM, the rest TOUCH_MM: k4_geometry.model.allowance)":
        "H1: 止まっているとき (運転席を滑りの両端に動かしたときも)、決まりの許す量より深く重なる物体の組がない "
        "(溶接のつなぎ目、ボルトの取付は MOUNT_MM まで、ほかは TOUCH_MM: allowance)",
    "both wheels of one side lift when their loads together reach 0": "片側の 2 輪の荷重の合計が 0 になると、その 2 輪が浮く",
    # where
    "wind from the left": "左からの風", "wind from the right": "右からの風",
    "tipping to the left": "左へ倒れる", "tipping to the right": "右へ倒れる",
    "front-most nodes to seat mounts": "いちばん前の節点から座席の取付まで",
    # labels (old data and the scripts' English labels)
    "load case": "試験条件", "loading": "乗員・荷物", "mesh": "メッシュ", "modes": "モード", "excitation": "加振",
    "interferences": "干渉", "sequence": "手順", "design": "設計", "frames": "保存したこま", "load path": "荷重の経路",
    "loads": "荷重", "loads shown": "表示した荷重", "parts layer": "部品の層", "sample": "計算した区間",
    "shell mesh": "シェルのメッシュ", "wheel loads": "輪の荷重", "mass": "質量", "offset": "ずれ",
    "ambient_temperature": "気温", "slope_up": "登りの坂", "battery": "電池", "motor": "モーター",
    "controller": "制御器", "wheel rotation": "車輪の回転", "motor rotation": "モーターの回転",
    "stress": "応力", "displacement": "変位", "cone": "コーン", "vehicle": "車両", "wheel": "車輪",
    # notes, strain_method
    "Motion: x(t) = node + scale x shape / |shape|max x sin(phase); colour = member strain / the mode's largest (or, "
    "switched, |shape| / |shape|max at each node).":
        "動き: x(t) = 節点 + 倍率 × 形 / |形|max × sin(位相)。色 = 部材のひずみ / そのモードの最大 (切り替えると、"
        "節点ごとの |形| / |形|max)。",
    "Strain per member from the mode shape, which has node translations only (no rotations in the record). Axial: "
    "(u_b - u_a) . e / L. Bending: each node's rotation is estimated as the mean of the chord rotations e x (u_b - u_a) / "
    "L of the members meeting there (rigid joints); a member's end rotation is that node rotation less its own chord "
    "rotation (twist dropped), and the curvature at an end is (4 phi_near + 2 phi_far) / L (Euler-Bernoulli, a cubic "
    "between the ends), times the half depth of the section in that direction. Total = |axial| + the larger end's "
    "bending, normalised to the mode's largest member (relative: the mode has no physical size). Strain energy: EA d^2 "
    "/ 2L + (EI/L)(2 phi_a^2 + 2 phi_a phi_b + 2 phi_b^2) in each bending direction, h taken as vertical as in K1 "
    "(members within 25 deg of vertical: h along x, the viewer's choice); torsion is not included (no rotations in "
    "the record).":
        "部材のひずみはモードの形から求める。記録の形は節点の並進だけで、回転はない。軸: (u_b − u_a)・e / L。"
        "曲げ: 節点の回転を、そこに集まる部材の弦の回転 e × (u_b − u_a) / L の平均とみる (剛なつなぎ目)。部材の端の回転は、"
        "その節点の回転から部材自身の弦の回転を引いたもの (ねじりは落とす)。端の曲率は (4 φ_近 + 2 φ_遠) / L "
        "(オイラー・ベルヌーイ、端のあいだは 3 次式) で、その向きの断面の高さの半分をかける。合計 = |軸| + 大きい方の端の曲げ。"
        "モードの中で最大の部材を 1 とする (相対値: モードの形は大きさを持たない)。ひずみエネルギー: EA d² / 2L + "
        "(EI/L)(2 φ_a² + 2 φ_a φ_b + 2 φ_b²) を曲げの向きごとに。h は K1 と同じく上下 (上下から 25° 以内の部材は x、"
        "ビューアの決め方)。ねじりは入れない (記録に回転がない)。",
    # assumptions (records)
    "K&C tables from the design's suspension hardpoints_mm, with their compliance from the bushings and the tie rod; "
    "longitudinal forces taken at the contact point":
        "K&C の表は設計のサスペンションの取付点 (hardpoints_mm) から作り、ブッシュとタイロッドのたわみを入れる。"
        "前後の力は接地点にかける",
    "centres of pressure at the centroids of the part boxes and wheels projected on the side and front planes (along "
    "x always; the areas and the side centre's height only where the design does not give them) (assumed)":
        "圧力の中心は、部品の箱と車輪を横と前の面に映した形の図心 (x の向きはいつも。面積と横の中心の高さは、"
        "設計が与えないときだけ) (仮定)",
    "frame members weighed as thin rods: density x area x length at the member centre":
        "フレームの部材は細い棒として重さを数える: 密度 × 断面積 × 長さを部材のまん中に",
    "brakes strong enough to lock every braked wheel (no ABS)": "ブレーキはどの制動輪もロックできる強さ (ABS なし)",
    "the cones and gates are drawn around the centre line; the driver model follows the line and the gates are not "
    "judged":
        "コーンと門は中心の線のまわりに描く。運転のモデルは線をたどり、門は判定しない",
    "no gravity and no ground (the crash forces are many times the weight over the run)":
        "重力と地面はない (衝突の力は重さの何倍も大きい)",
    "crush_margin: crumple_length (the front-most node to the front face of the front-most occupant part box, along x, "
    "at the start) less max_crush":
        "crush_margin: crumple_length (始めの、いちばん前の節点から、いちばん前の乗員の箱の前の面までの x の長さ) − max_crush",
    "hot_spots: each frame member's least room (over the run) to an occupant part box, read as occupant_space_left (the "
    "place's occupant_space_margin)":
        "hot_spots: フレームの部材ごとの、乗員の箱までの最小のすき間 (計算のあいだ)。occupant_space_left として読む "
        "(その場所の occupant_space_margin)",
    "occupant_space_margin: the least room over the run from the structure ahead of an occupant part box to the box's "
    "front face (occupant_space_left; negative: the structure is inside the box)":
        "occupant_space_margin: 乗員の箱の前にある構造から箱の前の面までの、計算のあいだの最小のすき間 "
        "(occupant_space_left。負: 構造が箱の中に入った)",
    "occupant_space_left: the occupant part boxes move with their carried_by nodes; the least x distance over the run "
    "from the structure ahead of a box (mesh nodes and element middles inside the box's y-z extent, ahead of its rear "
    "face) back to the box's front face; negative: the structure is inside the box":
        "occupant_space_left: 乗員の箱は carried_by の節点と一緒に動く。箱の前にある構造 (箱の y-z の範囲の中で、"
        "後ろの面より前にある節点と要素のまん中) から箱の前の面までの、計算のあいだの最小の x の距離。負: 構造が箱の中に入った",
    "elastic-perfectly plastic: axial yield at A f_y, plastic hinges at the element ends at M_p = Z_p f_y about each "
    "local axis on its own, reduced by the axial force as M_p (1 - (N / N_p)^2), torsion capped at the plastic torque; "
    "the plastic deformation stays (no spring back); no strain hardening, no strain-rate effect, no local buckling of "
    "the tube walls (a tube squashes at A f_y instead of folding progressively)":
        "弾完全塑性: 軸は A f_y で降伏。要素の端の塑性ヒンジは、局所の軸ごとに M_p = Z_p f_y で、軸力で "
        "M_p (1 − (N / N_p)²) に下げる。ねじりは塑性のトルクで頭打ち。塑性の変形は残る (戻らない)。ひずみ硬化、"
        "ひずみ速度の効果、管の壁の局部座屈は入れない (管は順に折れたたまずに A f_y でつぶれる)",
}
EXACT_BY_KEY = {"label": {}}   # filled from common.PARAM_JA below (param names as labels)

# ---------------------------------------------------------------- templates
# English with {n} parts, Japanese with {n} (as it is) or {n:C} where C converts it:
#   L loading, D direction ('from <D>'), M mode kind, J this module's translation, F function words (short name),
#   X sizes ('a x b' -> 'a × b'), S assembly step counts, W the excitation lines.

TEMPLATES = [
    # ---- ledger how
    ("left off by the loading {0} (occupants board in design order; cargo only with two_cargo): not in the design "
     "the tool is given",
     "乗員・荷物「{0:L}」では載らない (乗員は設計の順に乗り、荷は 2 人 + 荷 のときだけ)。ツールに渡す設計に入っていない"),
    ("left off by the loading {0}: not in the design the tool is given",
     "乗員・荷物「{0:L}」では載らない。ツールに渡す設計に入っていない"),
    ("a body (its box as an OBB / capsule) with its own step {0} in the sequence; masses are not used by the assembly tool",
     "干渉を調べる物体 (箱を OBB / カプセルとして)。組み立ての手順の {0} 手目で付ける。組み立てのツールは質量を使わない"),
    ("a body in the corner group put on at step of {0}; masses are not used by the assembly tool",
     "干渉を調べる物体。{0} の手順で、角の組と一緒に付ける。組み立てのツールは質量を使わない"),
    ("not reproduced: {0}", "再現できなかった: {0:J}"),
    ("point masses in equal shares on its carried_by nodes; the part's own rotational inertia and its stiffness are not "
     "in the model, it has no contact with the wall (only frame nodes touch it); carried_by nodes no member uses moved "
     "to the nearest frame node: {0}",
     "carried_by の節点に等分した点の質量。部品自身の回転の慣性と剛性はモデルにない。壁には当たらない "
     "(壁に触れるのはフレームの節点だけ)。部材のない carried_by の節点は、いちばん近いフレームの節点へ移した: {0}"),
    ("no carried_by: a point mass at support {0}'s frame node {1} (the nearest support); no stiffness, no wheel or tyre "
     "model, no contact with the wall",
     "carried_by なし: いちばん近い支持点 {0} のフレームの節点 {1} に点の質量。剛性も車輪・タイヤのモデルもなく、壁には当たらない"),
    ("the wheel centre node {0} (hiraku.solvers.k1_frame.model.wheel_node), joined to frame node {1} by a stiff link member",
     "車輪の中心の節点 {0} (フレームの節点 {1} と硬いつなぎの部材で結ぶ、wheel_node)"),
    ("frame node {0} (the support is there)", "フレームの節点 {0} (支持点がそこにある)"),
    ("no carried_by (unsprung, nearest support {0}): in the one rigid mass of the 1-D model, no stiffness; in K1 at the "
     "peak its force is at {1}",
     "carried_by なし (ばね下、いちばん近い支持点 {0}): 1 次元モデルの一つの剛な質量に入る。剛性はない。ピークの K1 では、"
     "その力を {1:J} にかける"),
    ("no carried_by (nearest support {0}): lumped mass at {1}; the support's spring (if any) runs from that node to "
     "the ground; no stiffness of its own",
     "carried_by なし (いちばん近い支持点 {0}): {1:J} に集中質量。支持点のばね (あれば) はその節点から地面へ。自分の剛性はない"),
    ("a mass item: {0} in equal shares on its carried_by frame nodes, with the moment of its offset; no stiffness",
     "質量の項目: {0:J} を carried_by のフレームの節点に等分 (ずれによるモーメントも)。剛性はない"),
    ("no carried_by (nearest support {0}): its d'Alembert force in each unit wheel load case (inertia relief; gravity "
     "is inside the wheel forces) at the wheel centre node {1}, joined to frame node {2} "
     "(hiraku.solvers.k1_frame.model.wheel_node); in the fatigue road model also the quarter car's unsprung mass at "
     "that support (shelf.fatigue.road_wheel_forces)",
     "carried_by なし (いちばん近い支持点 {0}): 単位の輪荷重のケースごとの慣性力 (慣性リリーフ。重力は輪の力に入っている) "
     "を車輪の中心の節点 {1} に (フレームの節点 {2} とつなぐ、wheel_node)。疲労の路面のモデルでは、その支持点の 1/4 車の"
     "ばね下の質量にもなる (road_wheel_forces)"),
    ("no carried_by (nearest support {0}): its d'Alembert force in each unit wheel load case (inertia relief; gravity "
     "is inside the wheel forces) at the wheel centre node {1}, joined to frame node {2} "
     "(hiraku.solvers.k1_frame.model.wheel_node)",
     "carried_by なし (いちばん近い支持点 {0}): 単位の輪荷重のケースごとの慣性力 (慣性リリーフ。重力は輪の力に入っている) "
     "を車輪の中心の節点 {1} に (フレームの節点 {2} とつなぐ、wheel_node)"),
    ("no carried_by (nearest support {0}): {1} at the wheel centre node {2}, joined to frame node {3} "
     "(hiraku.solvers.k1_frame.model.wheel_node)",
     "carried_by なし (いちばん近い支持点 {0}): {1:J} を車輪の中心の節点 {2} に (フレームの節点 {3} とつなぐ、wheel_node)"),
    ("its weight (gravity x the case's factor)", "重さ (重力 × 試験条件の倍率)"),
    ("its d'Alembert force in each unit wheel load case (inertia relief; gravity is inside the wheel forces)",
     "単位の輪荷重のケースごとの慣性力 (慣性リリーフ。重力は輪の力に入っている)"),
    ("no carried_by (support {0}): its force spread onto that support's mount nodes (half its mass: a suspension kind)",
     "carried_by なし (支持点 {0}): その力を支持点の取付の節点に配る (質量は半分: サスペンションの種類)"),
    ("no carried_by (support {0}): its force spread onto that support's mount nodes",
     "carried_by なし (支持点 {0}): その力を支持点の取付の節点に配る"),
    ("the RC network's node {0}: one heat capacity {1} and one resistance to the air {2} from design.variables; its loss "
     "heats it (shared by the {3} parts of kind {4}: one node for all); its mass is also in the vehicle mass of the drive",
     "熱回路の節点 {0}: 設計の変数の熱容量 {1} と空気への熱抵抗 {2} を一つずつ。損失で温まる (種類 {4} の部品 {3} 個で"
     "一つの節点)。質量は走行の計算の車両の質量にも入る"),
    ("the RC network's node {0}: one heat capacity {1} and one resistance to the air {2} from design.variables; its loss "
     "heats it; its mass is also in the vehicle mass of the drive",
     "熱回路の節点 {0}: 設計の変数の熱容量 {1} と空気への熱抵抗 {2} を一つずつ。損失で温まる。質量は走行の計算の車両の質量にも入る"),
    ("a stage of the power chain ({0}): {1} and {2} series with its part_id, shared equally by the {3} parts of the "
     "kind; no temperature (not a node of the RC network); its mass is in the vehicle mass of the drive",
     "電力の段 ({0}): part_id のある {1} と {2} の系列を、この種類の部品 {3} 個で等分。温度はない (熱回路の節点ではない)。"
     "質量は走行の計算の車両の質量に入る"),
    ("a stage of the power chain ({0}): {1} series with its part_id, shared equally by the {2} parts of the kind; no "
     "temperature (not a node of the RC network); its mass is in the vehicle mass of the drive",
     "電力の段 ({0}): part_id のある {1} の系列を、この種類の部品 {2} 個で等分。温度はない (熱回路の節点ではない)。"
     "質量は走行の計算の車両の質量に入る"),
    ("in the wheel node {0} ({1}, {2} kg), joined to the frame nodes {3} by pin-ended links (axial springs only); the "
     "wheel touches the wall at its centre's x + {4} mm and stops there (record assumptions)",
     "車輪の節点 {0} に入る ({1}、{2} kg)。フレームの節点 {3} とピンでつないだ棒 (軸のばねだけ) で結ぶ。車輪は中心の "
     "x + {4} mm で壁に当たり、そこで止まる (記録の前提)"),
    ("in the wheel node {0} ({1}), joined to the frame nodes {2} by pin-ended links (axial springs only); the wheel "
     "touches the wall at its centre's x + {3} mm and stops there (record assumptions)",
     "車輪の節点 {0} に入る ({1})。フレームの節点 {2} とピンでつないだ棒 (軸のばねだけ) で結ぶ。車輪は中心の "
     "x + {3} mm で壁に当たり、そこで止まる (記録の前提)"),
    ("{0} could not read this design: {1}", "{0:F} はこの設計を読めなかった: {1:J}"),
    ("No module named '{0}'", "モジュール {0} がない"),
    # ---- ledger notes, loading_source, stated
    ("{0}: not reproduced ({1})", "{0:F} は再現できなかった ({1:J})"),
    ("the modal record states no mass; the parts on board follow the load case's loading ({0}, {1})",
     "固有振動の記録には質量の値がない。載る部品は試験条件の乗員・荷物に従う ({0:L}、{1:J})"),
    ("default {0} (hiraku.contract.loading.loading_of: the load case gives none; the run does not hold this load case)",
     "既定の {0:L} (試験条件に指定がない。実行の記録にこの試験条件がない)"),
    ("default {0} (hiraku.contract.loading.loading_of: the load case gives none)", "既定の {0:L} (試験条件に指定がない)"),
    ("default {0} (the load case gives none)", "既定の {0:L} (試験条件に指定がない)"),
    ("unsprung at {0} ({1})", "ばね下 {0} ({1})"),
    ("wheel node {0}", "車輪の節点 {0}"),
    ("record result {0}", "記録の結果 {0}"),
    ("record assumption (the wheels as nodes), parts {0}", "記録の前提 (車輪を節点に)、部品 {0}"),
    ("not computed: {0}", "計算できなかった: {0:J}"),
    ("the design.json read here is not the design the tool weighed (a trimmed or edited copy), or the loading differs; "
     "computed from this version's design.json with loading {0}",
     "ここで読んだ設計がツールの量った設計と違う (削った・直した写し) か、乗員・荷物が違う。この版の設計から、"
     "乗員・荷物「{0:L}」で計算した"),
    # ---- inputs
    ("test {0}, catalog builder", "試験 {0}、カタログで作成"),
    ("record assumptions (the tool's default when the case does not give {0})", "記録の前提 (試験条件が {0} を与えないときのツールの既定)"),
    ("the record's mesh ({0}, {1})", "記録のメッシュ ({0}、{1})"),
    ("saved frames of a frame_shell run with the mount forces of {0} (K2 {1})",
     "{0} の取付の力をかけた frame_shell の計算の、保存したこま (K2 {1})"),
    ("design variables {0}, {1} (network_from_design)", "設計の変数 {0}、{1} (network_from_design)"),
    ("design variable {0}", "設計の変数 {0}"),
    ("load case criterion: T1: the design variable {0} (the part's limit; its source is in the design generator), "
     "against the {1}",
     "試験条件の判定基準: T1: 設計の変数 {0} (部品の上限。出典は設計を作るコードにある) と、部品を一つにまとめた温度を比べる"),
    ("load case criterion: {0}", "試験条件の判定基準: {0:J}"),
    ("T1: the design variable {0} (the part's limit; its source is in the design generator), against the lumped "
     "temperature of the part",
     "T1: 設計の変数 {0} (部品の上限。出典は設計を作るコードにある) と、部品を一つにまとめた温度を比べる"),
    ("Miner's rule: the welds last {0} {1} km while the damage stays below {2}",
     "マイナー則: 損傷が {2} より下なら、溶接は {0} の {1} km もつ"),
    ("P4: charge is left at the end of {0} {1} km", "P4: {0} の {1} km を走り終えても電池が残る"),
    ("{0} {1} deg (for the loading of {2})", "{0} の {1}° ({2} の乗員・荷物のとき)"),
    ("{0} gust {1} m/s", "{0} の突風 {1} m/s"),
    ("{0} {1} m from {2} km/h ({3}, borrowed)", "{0} の {2} km/h から {1} m ({3} を借りた)"),
    ("{0} pairs in the record", "記録に {0} 組"),
    ("{0} mode shapes stored of {1} frequencies", "周波数 {1} 個のうち、モードの形 {0} 個を保存"),
    ("{0} nodes, {1} beam elements of {2} members; {3} frames kept of the run",
     "節点 {0}、梁要素 {1} (部材 {2})。計算のこま {3} を保存"),
    ("{0} part boxes, {1} of them in the sequence", "部品の箱 {0}、そのうち手順にあるもの {1}"),
    ("{0} m simulated, scaled to the distance", "{0} m を計算し、距離まで伸ばした"),
    ("{0} instants of the step hit ({1} to {2} s)", "段差に当たるときの {0} 時刻 ({1}〜{2} s)"),
    ("C {0} J/K, R {1} K/W to the air (tau {2} s)", "C {0} J/K、空気への R {1} K/W (τ {2} s)"),
    ("mount forces at t = {0} s", "t = {0} s の取付の力"),
    ("quarter cars on an ISO 8608 class {0} road at {1} km/h", "ISO 8608 クラス {0} の路面を {1} km/h で走る 1/4 車"),
    ("rigid wall, face at x = {0} mm, box centre ({1}) mm, size {2} mm",
     "剛な壁、面は x = {0} mm、箱の中心 ({1}) mm、大きさ {2:X} mm"),
    ("the version's design regenerated with {0}; not applied (this generator has no such variable): {1}",
     "この版の設計を {0} で作り直した。使わない変数 (この設計の作り方にない): {1}"),
    ("the version's design regenerated with {0}", "この版の設計を {0:J} で作り直した"),
    ("{0} steps: {1}", "{0} 手: {1:S}"),
    ("{0} nodes, {1} quads, edge {2} mm", "節点 {0}、四角の要素 {1}、要素の辺 {2} mm"),
    ("{0} nodes, {1} quads, element {2} mm", "節点 {0}、四角の要素 {1}、要素の辺 {2} mm"),
    ("mass {0} kg", "質量 {0} kg"),
    ("wheel rotation {0} Hz, motor rotation {1} Hz", "車輪の回転 {0} Hz、モーターの回転 {1} Hz"),
    ("wheel rotation {0} Hz", "車輪の回転 {0} Hz"),
    ("yield {0}", "降伏 {0}"),
    ("{0} at t {1} s", "{0} (t {1} s)"),
    # ---- the field's own texts
    ("footprint of {0}", "{0} の取付の面"), ("joint nodes of {0}", "{0} のつなぎ目の節点"), ("pin holes of {0}", "{0} のピンの穴"),
    ("mode {0} ({1})", "モード {0} ({1:M})"), ("mode {0} ({1}): {2}", "モード {0} ({1:M}): {2}"),
    ("stress ranges {0}-up MPa", "応力範囲 {0} MPa 以上"), ("stress ranges {0}-{1} MPa", "応力範囲 {0}〜{1} MPa"),
    ("Miner damage over {0} km (largest weld point)", "{0} km でのマイナー損傷 (溶接点の最大)"),
    ("frequency response: vertical motion at {0} per vertical force at wheel {1}, over its value at 0 Hz; damping ratio "
     "{2} for the body on its springs (from the dampers), {3} for the frame's modes (assumed, not checked)",
     "周波数応答: 輪 {1} の上下の力あたりの {0} の上下の動き (0 Hz の値で割る)。減衰比は、ばねの上の車体 {2} "
     "(ダンパーから)、フレームのモード {3} (仮定、確かめていない)"),
    ("Box centre minus the last path point: largest {0} mm over {1} steps (the page adds it to the path).",
     "箱の中心 − 道の最後の点: {1} 手で最大 {0} mm (このページが道に足す)。"),
    ("{0} frame nodes have no mode vector in the record (drawn still): {1}",
     "フレームの節点 {0} 個は記録にモードのベクトルがない (止めて描く): {1}"),
    # ---- assembly step notes (one clause each; the clauses are joined by '; ')
    ("hands to node {0} from {1}, {2} mm in", "節点 {0} へ{1:D}手を入れる ({2} mm)"),
    ("hands to part {0} from {1}, {2} mm in", "部品 {0} へ{1:D}手を入れる ({2} mm)"),
    ("hands to its face from {0}, {1} mm in", "その面へ{0:D}手を入れる ({1} mm)"),
    ("the hands' way to node {0} is taken from every side", "節点 {0} への手の道は、どの向きからもふさがれる"),
    ("the hands' way to part {0} is taken from every side", "部品 {0} への手の道は、どの向きからもふさがれる"),
    ("from {0} by {1}", "{0:D}は {1} がふさぐ"),
    ("the hands' way is taken by {0}", "手の道をふさぐもの: {0}"),
    ("sub-assembly {0}, built off the vehicle ({1} of {2})", "サブアセンブリ {0} (車の外で組む、{2} 手のうち {1} 手目)"),
    ("welded on when it was built (no fastening by hand here)", "作るときに溶接してある (ここで手で留める所はない)"),
    ("puts sub-assembly {0} ({1}) on the frame", "サブアセンブリ {0} ({1}) をフレームに付ける"),
    # ---- record assumptions
    ("friction coefficient {0} (assumed; the load case usually gives mu)", "摩擦係数 {0} (仮定。ふつうは試験条件が mu を与える)"),
    ("suspension arm bushings {0} kN/mm along the pivot axis (assumed: a little above passenger-car arm bushings, {1} "
     "to {2} kN/mm, no UTV value; the design has no bushing_axial_stiffness)",
     "サスペンションのアームのブッシュ: 回転軸の向きに {0} kN/mm (仮定: 乗用車のアームのブッシュ {1}〜{2} kN/mm より少し上。"
     "UTV の値はない。設計に bushing_axial_stiffness がない)"),
    ("suspension arm bushings {0} kN/mm across the pivot axis (assumed: within passenger-car arm bushings, {1} to {2} "
     "kN/mm, no UTV value; the design has no bushing_radial_stiffness)",
     "サスペンションのアームのブッシュ: 回転軸に直角に {0} kN/mm (仮定: 乗用車のアームのブッシュ {1}〜{2} kN/mm の内。"
     "UTV の値はない。設計に bushing_radial_stiffness がない)"),
    ("tie rod {0} kN/mm along its length, rack held (assumed, no source; the design has no tie_rod_stiffness)",
     "タイロッド: 長さの向きに {0} kN/mm、ラックは動かない (仮定、出典なし。設計に tie_rod_stiffness がない)"),
    ("tyre forces: Magic Formula 6.1 with the coefficients of the 205/60R15 91 V, 2.2 bar (passenger car) tyre (H. B. "
     "Pacejka, Tire and Vehicle Dynamics, 3rd edition (2012), Appendix 3: the reference tyre's MF-Tyre parameters; not "
     "checked against the book). Not a measured UTV tyre: only the shapes of the passenger car tyre's curves are used, "
     "scaled to this vehicle: nominal load {0} N (the mean static wheel load), R0 = the wheel radius, and at each "
     "wheel's static load the peak friction = mu and the slopes at zero slip = the stiffnesses K2 took (design or "
     "defaults above); held at the coefficients' nominal inflation pressure (no pressure terms, so nothing is "
     "extrapolated to a low-pressure tyre)",
     "タイヤの力: Magic Formula 6.1、乗用車のタイヤ 205/60R15 91 V、2.2 bar の係数 (H. B. Pacejka, Tire and Vehicle "
     "Dynamics, 3rd edition (2012), Appendix 3 の基準のタイヤの MF-Tyre の値。本では確かめていない)。測った UTV の"
     "タイヤではない: 乗用車のタイヤの曲線の形だけを使い、この車に合わせて伸ばす: 基準の荷重 {0} N (静止時の輪の荷重の平均)、"
     "R0 = 車輪の半径。各輪の静止時の荷重で、最大の摩擦 = mu、すべり 0 での傾き = K2 の剛さ (設計か上の既定)。"
     "係数の基準の空気圧のまま (空気圧の項はなく、低い空気圧のタイヤへは延ばさない)"),
    ("mount forces up to the tip ({0} deg) stand for a steady turn at a_y = tan(tip) g = {1} g just before rolling "
     "over: on the table gravity is g cos(theta) down and g sin(theta) across, so they are cos(theta) = {2} times "
     "those of that turn (quasi-static)",
     "倒れる角度 ({0}°) までの取付の力は、倒れる直前の a_y = tan(角度) g = {1} g の定常旋回にあたる: 台の上では重力が "
     "下に g cos(θ)、横に g sin(θ) なので、その旋回の力の cos(θ) = {2} 倍 (準静的)"),
    ("table surface friction {0} so the tyres do not slide", "台の面の摩擦 {0} (タイヤが滑らないように)"),
    ("ISO 8608 class {0}: G_d(n0) = {1} m^3, w = {2} (not checked against the ISO text); left and right tracks "
     "uncorrelated",
     "ISO 8608 クラス {0}: G_d(n0) = {1} m^3、w = {2} (ISO の本文では確かめていない)。左右の通り道は無相関"),
    ("brake_rise_time = {0} s (assumed; neither the load case nor the design gives brake_rise_time)",
     "brake_rise_time = {0} s (仮定。試験条件にも設計にも brake_rise_time がない)"),
    ("double lane change course of ISO 3888-1:2018 Table 1 ({0} m, offset {1} m; checked against the public sample on "
     "{2}); a centre line, not cone gates",
     "ISO 3888-1:2018 の表 1 のダブルレーンチェンジの道 ({0} m、ずれ {1} m。{2} に公開の見本で確かめた)。"
     "コーンの門ではなく中心の線"),
    ("peak_tractive_force {0} N = motor_torque {1} N m x gear_ratio {2} x efficiency {3} / rolling_radius {4} mm (K2's "
     "effective rolling radius: the loaded radius at the static load, the free radius {5} mm less the static load / tyre "
     "stiffness); K0, with no rolling_radius given, computes the same loaded radius: {6} mm gives {7} N ({8})",
     "peak_tractive_force {0} N = motor_torque {1} N m × gear_ratio {2} × efficiency {3} / rolling_radius {4} mm "
     "(K2 の転がりの半径: 静止時の荷重での負荷時の半径 = 自由な半径 {5} mm − 静止時の荷重 / タイヤの剛さ)。"
     "rolling_radius がないとき K0 も同じ負荷時の半径を出す: {6} mm で {7} N ({8})"),
    ("the run stopped {0} s after target_speed {1} km/h (until = target): speed_at_stop {2} km/h is not the top speed, "
     "and there is no top_speed result; give until = top_speed for it",
     "計算は target_speed {1} km/h の {0} s 後に止めた (until = target): speed_at_stop {2} km/h は最高速度ではなく、"
     "top_speed の結果はない。最高速度には until = top_speed を与える"),
    ("after the lift-off the crawl at {0} m/s stopped before every wheel the step covers had crossed it (the record "
     "ends there)",
     "浮いた後、{0} m/s のゆっくりの走りは、段差にかかる輪がすべて越える前に止まった (記録はそこで終わる)"),
    ("a beam element breaks (is removed) when the plastic hinge rotation at one of its ends passes {0} deg (assumed; no "
     "source in the repository, 確かめていない)",
     "梁の要素は、どちらかの端の塑性ヒンジ回転が {0}° を超えると折れる (取り除く) (仮定。リポジトリに出典なし、確かめていない)"),
    ("wall friction mu = {0} (assumed; no source in the repository, 確かめていない)",
     "壁の摩擦 mu = {0} (仮定。リポジトリに出典なし、確かめていない)"),
    ("battery_mount_force: the battery's mass share on each of its carried_by nodes x the node's acceleration averaged "
     "over {0} ms (largest at {1} ms)",
     "battery_mount_force: carried_by の節点ごとに分けた電池の質量 × その節点の加速度を {0} ms でならした値 (最大は {1} ms)"),
    ("first break at {0} ms ({1})", "最初の破断 {0} ms ({1})"),
    ("first yield at {0} ms", "最初の降伏 {0} ms"),
    ("the largest crush at {0} ms", "最大のつぶれ {0} ms"),
    ("occupant_space_left at the start: {0} mm (the least over the run at {1} ms)",
     "始めの occupant_space_left: {0} mm (計算のあいだの最小は {1} ms)"),
    ("run: the vehicle stopped (its speed toward the wall, its momentum / its mass, reached 0) at {0} ms ({1} steps); "
     "speed at the end {2} m/s",
     "計算: 車は {0} ms ({1} ステップ) で止まった (壁へ向かう速さ = 運動量 / 質量が 0 になった)。終わりの速さ {2} m/s"),
    ("smaller pieces broken off the seats' side, touching the rest by the element contact: at {0} ms, {1} nodes ({2} % "
     "of the mass: {3})",
     "座席側から離れた小さい塊 (要素の接触で残りに触れる): {0} ms に {1} 節点 (質量の {2} %: {3})"),
    ("tyre radial stiffness for the wall contact: the vertical stiffness tyre_stiffness, taken as the same in the "
     "radial direction (not checked): {0} N/mm, the design's tyre_stiffness",
     "壁との接触でのタイヤの半径の向きの剛さ: 上下の剛さ tyre_stiffness を半径の向きにも同じとみる (確かめていない): "
     "{0} N/mm、設計の tyre_stiffness"),
    ("the tyre's section height (the deflection at which the rim reaches the wall): {0} % of the wheel radius "
     "(HIRAKU's assumption; the design gives no rim)",
     "タイヤの断面の高さ (リムが壁に届くたわみ): 車輪の半径の {0} % (HIRAKU の仮定。設計にリムがない)"),
    ("max_crush and the crush series: how far the occupants' seat mount nodes ({0}) moved toward the wall after the "
     "frame's front-most node reached it (the shortening of the structure ahead of them while the front is held at the "
     "wall; a broken-off front piece moving back from the wall is not counted)",
     "max_crush とつぶれの系列: フレームのいちばん前の節点が壁に着いてから、乗員の座席の取付の節点 ({0}) が壁へ動いた"
     "距離 (前が壁に押さえられているあいだの、その前の構造の縮み。折れて壁から戻る前の塊は数えない)"),
    ("peak_decel and the deceleration series: the whole mass's deceleration, the wall's impulse over each {0} ms / (the "
     "total mass x {1} ms), recorded every {2} ms; peak_decel is the input to the occupants (the deceleration of the "
     "whole vehicle they ride in, not of the seat mounts on their own); mean_decel: the speed lost / the time to the stop",
     "peak_decel と減速度の系列: 全体の質量の減速度 = {0} ms ごとの壁の力積 / (全体の質量 × {1} ms)、{2} ms ごとに記録。"
     "peak_decel は乗員への入力 (乗っている車全体の減速度で、座席の取付だけのものではない)。mean_decel: 失った速さ / "
     "止まるまでの時間"),
    ("frame_split_fraction: the largest share of the whole mass in a piece broken off the seats' side with a node in an "
     "occupant part box's y-z extent, ahead of its rear face, at the start (a piece that can come into their space); {0} "
     "other piece(s) cannot and are not counted",
     "frame_split_fraction: 座席側から離れた塊のうち、始めに乗員の箱の y-z の範囲の中で後ろの面より前に節点のあるもの "
     "(乗員の空間に来うる塊) の、全体の質量に対する最大の割合。ほかの {0} 個の塊は来ないので数えない"),
    ("part boxes past the wall face, taken as crushed against it (the boxes have no contact; they move with their "
     "carried_by nodes): {0}",
     "壁面の先に出た部品の箱 (壁でつぶれたとみる。箱は接触しない、carried_by の節点と一緒に動く): {0}"),
    ("unsupported_load: the parts with mass put on no mesh node (each part's mass goes on its carried_by nodes, or the "
     "nearest support's node; whether a part's shape touches the frame is the hand calculation's path check, "
     "load_path.free_parts): {0}",
     "unsupported_load: どの節点にも載らない質量のある部品 (部品の質量は carried_by の節点か、いちばん近い支持点の節点に"
     "載る。部品の形がフレームに触れるかは手計算の経路の確かめ、load_path.free_parts): {0:J}"),
    ("member_axial_force.<member> (N, + tension): of the member's elements not broken then, the axial force E A / L0 "
     "(length - L0 - plastic elongation) of the largest size, 0 when all are broken; member_hinge.<member>.<end node> "
     "(deg): the plastic hinge rotation sqrt(theta_y^2 + theta_z^2) at that end of the member; both at the node_motion "
     "field's frames",
     "member_axial_force.<部材> (N、+ 引張): その時に折れていない部材の要素のうち、大きさが最大の軸力 E A / L0 "
     "(長さ − L0 − 塑性の伸び)。全部折れたら 0。member_hinge.<部材>.<端の節点> (°): 部材のその端の塑性ヒンジ回転 "
     "sqrt(θ_y² + θ_z²)。どちらも node_motion のこまの時刻で"),
    ("the frame as {0} co-rotational 3-D beam elements on {1} nodes (each member in up to {2} elements, none shorter "
     "than {3} radii of gyration of its section), explicit central differences, time step {4} us = {5} (Courant factor) "
     "x the elements' stable step 2 / omega_max; the rotational inertia of the nodes scaled up to at least (element "
     "mass / 2) x length^2 / 12 per element end (rotational mass scaling: no translational mass is added)",
     "フレームは節点 {1} 個の上の共回転の 3 次元の梁要素 {0} 個 (部材ごとに最大 {2} 要素、断面の回転半径の {3} 倍より"
     "短いものはない)。陽解法の中心差分、時間の刻み {4} us = {5} (クーラン数) × 要素の安定な刻み 2 / omega_max。"
     "節点の回転の慣性は、要素の端ごとに少なくとも (要素の質量 / 2) × 長さ² / 12 まで大きくする (回転の質量の調整: "
     "並進の質量は足さない)"),
    ("mass {0} kg: frame {1} kg on its element nodes, parts {2} kg; the other parts without carried_by (suspension) as "
     "point masses at their nearest support's frame node: {3}; each part's mass in equal shares on its carried_by nodes, "
     "as point masses (the part's own rotational inertia and its stiffness are not in the model; it moves with those "
     "nodes); the wheels as nodes at their centres touching the wall at their radius (x + r > wall) through the tyre "
     "(the wall note says how), joined to the frame by pin-ended links standing in for the suspension arms (axial "
     "springs only, no bending, no yield, never broken; stiffness set from the time step: each link's frequency {4} x "
     "the frame elements' highest, far stiffer than the arms): {5}; section capacities: plastic moduli (K0 "
     "frame_inputs.plastic_modulus), plastic torque 2 A_m t fy / sqrt 3",
     "質量 {0} kg: フレーム {1} kg を要素の節点に、部品 {2} kg。carried_by のないほかの部品 (サスペンション) は、いちばん"
     "近い支持点のフレームの節点に点の質量: {3:W}。部品の質量は carried_by の節点に等分した点の質量 (部品自身の回転の慣性と"
     "剛性はモデルにない。その節点と一緒に動く)。車輪は中心の節点で、タイヤを通して半径のところで壁に当たる (x + r > 壁、"
     "やり方は壁の前提)。フレームとは、サスペンションのアームの代わりのピンでつないだ棒で結ぶ (軸のばねだけ、曲げ・降伏・"
     "破断なし。剛さは時間の刻みから: 棒の振動数はフレームの要素の最高の {4} 倍で、アームよりずっと硬い): {5:W}。断面の"
     "強さ: 塑性断面係数 (K0 frame_inputs.plastic_modulus)、塑性のトルク 2 A_m t fy / sqrt 3"),
    ("the wall: a rigid plane x = {0} mm, at the front-most of the frame's nodes ({1}) and the part boxes they carry; it "
     "touches the frame's nodes by a penalty spring {2} x the node's mass / dt^2, and the wheel nodes through the tyre: "
     "when a wheel centre's x + its radius passes the wall, a spring of the tyre's radial stiffness on the deflection "
     "(compressive only, no damping: the tyre gives back what it holds); when the deflection reaches the tyre's section "
     "height the rim is at the wall and stops there, perfectly plastic (no spring-back from the rim; HIRAKU's choice, "
     "the rim's own crushing is not modelled). {3}; the tyre spring on the wheel node's mass (omega dt)^2 = k_t dt^2 / "
     "m = {4} at most (the kernel allows 1); the part boxes and the bumper do not touch the wall",
     "壁: 剛な平面 x = {0} mm、フレームのいちばん前の節点 ({1}) とそれが載せる部品の箱のところ。フレームの節点にはペナルティの"
     "ばね {2} × 節点の質量 / dt² で触れ、車輪の節点にはタイヤを通して触れる: 車輪の中心の x + 半径が壁を越えると、たわみに"
     "タイヤの半径の向きの剛さのばね (押すときだけ、減衰なし: タイヤはためた力を返す)。たわみがタイヤの断面の高さに届くと、"
     "リムが壁に着いてそこで止まる、完全塑性 (リムからの戻りなし。HIRAKU の決め方で、リム自身のつぶれはモデルにない)。"
     "{3:W}。車輪の節点の質量に対するタイヤのばね (omega dt)² = k_t dt² / m = 最大 {4} (計算の核は 1 まで許す)。"
     "部品の箱とバンパーは壁に当たらない"),
    ("contact between the frame's elements that share no node (pieces broken apart, a member folding onto another), "
     "frictionless: each element a capsule of radius half its section's larger outer dimension (pairs closer than that "
     "at the start, near the joints: from their starting distance); a penalty spring 0.05 x the contact point's mass / "
     "dt^2 between the closest points of the two axes, the force shared to the four nodes (momentum kept); the pairs "
     "looked at are listed again whenever a node has moved 5 mm relative to the rest (the list keeps the pairs within "
     "10 mm of touching). In this run: {0} pairs can touch, the list made {1} times, at most {2} pairs on it, {3} "
     "pair-steps in contact. The wheels and the part boxes do not touch the elements",
     "節点を共有しないフレームの要素どうしの接触 (折れて離れた塊、ほかの部材に折れ重なる部材)、摩擦なし: 各要素は断面の"
     "大きい方の外の寸法の半分を半径とするカプセル (始めにそれより近い組、つなぎ目の近く: 始めの距離から)。二つの軸の"
     "いちばん近い点のあいだに、ペナルティのばね 0.05 × 接触点の質量 / dt²、力は 4 つの節点に分ける (運動量は保つ)。"
     "調べる組は、節点がほかに対して 5 mm 動くたびに作り直す (触れるまで 10 mm 以内の組を残す)。この計算では: 触れうる組 "
     "{0}、組の表を {1} 回作り、表の組は最大 {2}、接触した組とステップの数 {3}。車輪と部品の箱は要素に触れない"),
    # ---- short ones last (they would also take the start of the longer texts above)
    ("ISO 8608 class {0}", "ISO 8608 クラス {0}"),
]

CONV: dict[str, Callable[[str], str | None]] = {
    "L": lambda v: LOADING.get(v, v),
    "D": lambda v: DIRS.get(v),
    "M": lambda v: MODE_KIND.get(v),
    "J": lambda v: ja(v),
    "F": lambda v: (lambda f: f"{f[0]} ({f[1]})" if f else None)(fn_ja(v)),
    "X": lambda v: v.replace(" x ", " × "),
    "S": lambda v: _steps(v),
    "W": lambda v: _words(v),
}

WORDS = [   # phrases inside the record's lists (crash_beam's mass and wall assumptions)
    ("(the arms' brackets (found through connects_to), with the support's frame node and the design nodes nearest the "
     "wheel)", "(アームの取付金具 (connects_to でたどる)、支持点のフレームの節点、車輪にいちばん近い設計の節点)"),
    ("(the arms' brackets (found through connects_to))", "(アームの取付金具、connects_to でたどる)"),
    (", radius ", "、半径 "), (") to ", ") → "), ("section height", "断面の高さ"), ("rim radius", "リムの半径"),
    (" kg at ", " kg → "), ("; ", "。"),
]


def _words(v: str) -> str:
    for en, jp in WORDS:
        v = v.replace(en, jp)
    return v


def _steps(v: str) -> str | None:
    out = []
    for piece in v.split(", "):
        m = re.fullmatch(r"(\d+) (\w+)", piece)
        if not m or m.group(2) not in STATUS:
            return None
        out.append(f"{STATUS[m.group(2)]} {m.group(1)}")
    return "、".join(out)


SUFFIXES = [
    ("; also its own shaft beam with stiffness for driveshaft_bending/torsion_mode "
     "(hiraku.solvers.k1_frame.shaft.driveshaft_modes)",
     "。駆動軸の曲げ・ねじりのモードのために、剛性のある自分の軸の梁も持つ (driveshaft_modes)"),
]

_PH = re.compile(r"\{(\d+)(?::(\w+))?\}")


def _compile(en: str):
    """The template as a regex. Where the template's own text has no '; ', a part does not run over one (texts
    joined by '; ' are translated clause by clause)."""
    part = ".+?" if "; " in _PH.sub("", en) else "(?:(?!; ).)+?"
    parts, pos = [], 0
    for m in _PH.finditer(en):
        parts.append(re.escape(en[pos:m.start()]))
        parts.append(f"(?P<g{m.group(1)}>{part})")
        pos = m.end()
    parts.append(re.escape(en[pos:]))
    head = en[:_PH.search(en).start()] if _PH.search(en) else en
    return re.compile("".join(parts), re.S), head


RULES = [(*_compile(en), jp) for en, jp in TEMPLATES]


def _fill(jp: str, m: re.Match) -> str | None:
    out, pos = [], 0
    for p in _PH.finditer(jp):
        out.append(jp[pos:p.start()])
        v = m.group(f"g{p.group(1)}")
        if p.group(2):
            v = CONV[p.group(2)](v)
            if v is None:
                return None
        out.append(v)
        pos = p.end()
    out.append(jp[pos:])
    return "".join(out)


@lru_cache(maxsize=None)
def ja(s: str, key: str = "") -> str:
    """The Japanese of one string (the string itself if no rule matches)."""
    if not s or not re.search(r"[A-Za-z]{2,}", s):
        return s
    by_key = EXACT_BY_KEY.get(key, {})
    if s in by_key:
        return by_key[s]
    if s in EXACT:
        return EXACT[s]
    for rx, head, jp in RULES:
        if head and not s.startswith(head):
            continue
        m = rx.fullmatch(s)
        if m:
            out = _fill(jp, m)
            if out is not None:
                return out
    for en, jp in SUFFIXES:
        if s.endswith(en) and len(s) > len(en):
            base = ja(s[: -len(en)], key)
            if base != s[: -len(en)]:
                return base + jp
    if "; " in s:
        pieces = s.split("; ")
        tr = [ja(p, key) for p in pieces]
        if all(t != p for t, p in zip(tr, pieces)):
            return "。".join(tr)
    return s


def translate(obj: Any, key: str = "", parent: str = "") -> Any:
    """The data object with its display texts in Japanese (a new object; packed arrays shared)."""
    if isinstance(obj, dict):
        if "b64" in obj:
            return obj
        out = {k: translate(v, k, key) for k, v in obj.items()}
        f = fn_ja(obj.get("fn")) if isinstance(obj.get("fn"), str) else None
        if f:
            out["fn"] = f[0]
            if isinstance(out.get("how"), str) and out["how"]:
                out["how"] = out["how"] + f" (計算の関数: {f[1]})"
        return out
    if isinstance(obj, list):
        return [translate(v, key, parent) for v in obj]
    if isinstance(obj, str) and (key in DISPLAY or (key == "name" and parent == "bands")):
        return ja(obj, key)
    return obj


def _init_labels() -> None:
    from viewer.src import common
    EXACT_BY_KEY["label"].update({k: v for k, v in common.PARAM_JA.items()})


_init_labels()


if __name__ == "__main__":
    from viewer.src import common
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else common.DEFAULT_OUT)
    for p in sorted(folder.glob("*.js")):
        field, obj = common.read_data_js(p)
        p.write_text(common.data_js(field, translate(obj)), encoding="ascii")
        print(f"translated {p.name}")

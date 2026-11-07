"""NVH: forced response to the tyres' ground displacement (studies.lere.frf, k1_shell.frf), before and after a fix.

    python -m viewer.src.nvh_frf <frf dir> [--sets A REC] [--labels 対策前 対策後] [--out viewer/data]

<frf dir>: the driver's output folder (studies/lere/frf.py --out), one folder per set with frf.json and
frf_shapes.npz. Writes viewer/data/nvh_frf.js (VIEW_DATA["nvh_frf"]); the page shows it in the NVH field as the
variant 「接地面の加振」.

Field keys added to the common object (see common.py):
    model "frf"
    input_mm: the input amplitude (1 mm)
    patterns {key: label}, outputs {key: label}
    sets [{name, label, doc, offset_y (mm, where the page draws it beside the others),
           shell {nodes (f32 n x 3), quads (i32 ne x 4)},
           x (f32 Hz), curves {pattern: {output: {acc (f32 m/s^2 per mm), disp (f32 mm per mm)}}},
           peaks {pattern: {output: [{f_Hz, acc_mps2, disp_mm, mode_f_Hz, kind}]}},
           modes [{f_Hz, kind}], rollbar_Hz, frame_Hz, hop_Hz [lo, hi],
           shapes {key "pattern@f": {re, im (i16 n x 3, mm per mm of input), vm (i16 per element, MPa per mm), u_max,
                   vm_max}}, static_check {pattern: largest relative error at 0 Hz}}]
    keys {pattern: [f_Hz]}: the frequencies with a stored shape (the same for every set)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

from viewer.src import common

PATTERN_JA = {"in_phase": "4 輪とも同じ向き (上下)", "roll": "左右で逆 (ロール)", "pitch": "前後で逆 (ピッチ)"}
OUTPUT_JA = {"seat": "座席の取付 (左の座席、上下)", "column": "ステアリングコラムの取付 (上下)",
             "rollbar_top": "ロールバーの上 (中央、上下)", "rollbar_top_lat": "ロールバーの上 (中央、横)",
             "frame_mid": "フレームの中ほど (左のレール、上下)"}
KIND_JA = [("rigid (free in x, y, yaw)", "剛体 (前後・横・ヨーに自由)"), ("body bounce", "車体の上下 (バウンス)"),
           ("body pitch", "車体のピッチ"), ("body roll", "車体のロール"), ("body fore-aft", "車体の前後"),
           ("body sideways", "車体の横"), ("body yaw", "車体のヨー"), ("roll bar", "ロールバー"), ("frame", "フレーム"),
           ("wheel hop", "ばね下の上下 (ホップ)"), ("wheel camber rotation", "車輪のキャンバー回り"),
           ("wheel spin / wind-up rotation", "車輪の回転方向 (ワインドアップ)"), ("wheel steer rotation", "車輪の操舵回り"),
           ("wheel fore-aft", "車輪の前後"), ("wheel sideways", "車輪の横"), ("arm local", "アームだけ")]
QUAL_JA = [("(coupled with the wheels)", "(車輪と連成)"), ("(coupled with the arms)", "(アームと連成)"),
           ("(coupled with the frame)", "(フレームと連成)"), ("front", "前"), ("rear", "後"), ("both axles", "前後とも"),
           ("in phase", "同相"), ("anti-phase (tramp)", "逆相 (トランプ)")]


def kind_ja(k: str) -> str:
    out = k
    for en, ja in KIND_JA:
        if out.startswith(en):
            out = ja + out[len(en):]
            break
    for en, ja in QUAL_JA:
        out = out.replace(en, ja)
    return re.sub(r"\s+", " ", out).strip()


def shell_of(npz) -> dict:
    nodes, quads = npz["nodes"], npz["elements"]
    return {"n_nodes": int(len(nodes)), "n_elements": int(len(quads)),
            "nodes": common.pack_f32(nodes.ravel().tolist(), [len(nodes), 3]),
            "quads": common.pack_i32(quads.ravel().tolist(), [len(quads), 4])}


def pack_scaled(a: np.ndarray, shape) -> tuple[dict, float]:
    big = float(np.max(np.abs(a))) if a.size else 0.0
    return common.pack_i16(a.ravel().tolist(), (big / 32767.0) or 1.0, list(shape)), big


SHAPE_OUTPUTS = ("seat", "rollbar_top", "rollbar_top_lat", "frame_mid")


def chosen_keys(raws: list[dict], per_pattern: int) -> set[str]:
    """At most per_pattern stored frequencies per pattern (the same for every set): every set's roll bar and first
    frame modes' nearest, then the highest peaks (acceleration over that output's largest) at the seat, the roll bar
    top and the frame middle of any set."""
    stored: dict[str, list[float]] = {}
    for k in raws[0]["shape_keys"]:
        p, f = k.split("@")
        stored.setdefault(p, []).append(float(f))
    out = set()
    for p, fl in stored.items():
        near = lambda x: min(fl, key=lambda v: abs(v - x))  # noqa: E731
        pick = []

        def add(f):
            if len(pick) < per_pattern and all(abs(f - v) > 0.3 for v in pick):  # one of near-equal ones
                pick.append(f)

        cand = []
        for r in raws:
            for j, o in enumerate(SHAPE_OUTPUTS):
                pk = r["peaks"][p].get(o, [])
                big = max([x["acc_mps2"] for x in pk] or [1.0])
                cand += [(-round(x["acc_mps2"] / big, 2), j, near(x["f_Hz"])) for x in pk]
        top = sorted(cand)
        for c in top:  # each output's highest peak first
            if c[0] == -1.0:
                add(c[2])
        for r in raws:
            for x in (r.get("rollbar_mode_Hz"), r.get("first_frame_mode_Hz")):
                if x is not None:
                    add(near(x))
        for c in top:
            add(c[2])
        for f in fl:  # room left: the other stored ones
            add(f)
        out |= {f"{p}@{f:g}" for f in pick}
    return out


def part_setup(design: dict, npz) -> tuple[list[dict], dict, dict, dict]:
    """The set's part boxes, their ledger (as the modal tool carries them: mass on the carried_by nodes), the
    display mesh as nvh_shell reads a mesh, and the design nodes' joint mesh nodes on it (nvh_shell.joint_nodes)."""
    from viewer.src import nvh_shell

    parts = common.parts_of(design)
    ledger = common.parts_ledger("modal", design, {"params": {"loading": PART_LOADING}}, {}, parts)
    mesh = {"nodes_mm": npz["nodes"].tolist(), "elements": npz["elements"].tolist(),
            "element_member": [str(m) for m in npz["member"]], "element_size_mm": DISPLAY_ELEMENT_MM}
    return parts, ledger, mesh, nvh_shell.joint_nodes(design, mesh)


# the loading the FRF runs put on board (studies/lere/frf.py: the N1 load case, Q30's two_cargo)
PART_LOADING = "two_cargo"
DISPLAY_ELEMENT_MM = 30.0  # frf.json display_mesh element_size_mm
PART_NOTE = ("部品は、載っている節 (carried_by) の動きと一緒に動く: 解いた網の表示の節のうち、その設計の節のまわりの節の"
             "平均 (実部と虚部を別に。viewer/src/nvh_shell.py の joint_nodes と同じ選び方) に、部品ごとの剛体の動きを"
             "合わせる。ばね下の部品 (車輪・ナックル・腕など) は表示の網に無いので止めて描く")


def part_shape(design: dict, parts: list[dict], ledger: dict, mesh: dict, joints: dict, d: np.ndarray) -> dict:
    """One stored response's part motions: the rigid fit (nvh.part_motion) of the real and of the imaginary part of
    the displacement, packed; the unsprung parts stay (their wheels and arms are not in the display mesh)."""
    from viewer.src import nvh as beam
    from viewer.src import nvh_shell

    out = {}
    for tag, u in (("re", d.real), ("im", d.imag)):
        per = nvh_shell.node_motion(design, mesh, joints, u.tolist())
        per = {k: v for k, v in per.items() if not k.startswith("wc:")}
        pm = beam.part_motion(parts, ledger, design, per)
        for j, e in enumerate(ledger["parts"]):
            if e.get("cat") == "unsprung":
                pm["t"][j], pm["w"][j] = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        out[f"t_{tag}"] = common.pack_f32([c for v in pm["t"] for c in v], [len(pm["t"]), 3])
        out[f"w_{tag}"] = common.pack_f32([c for v in pm["w"] for c in v], [len(pm["w"]), 3])
        if tag == "re":
            out["c"] = common.pack_f32([c for v in pm["c"] for c in v], [len(pm["c"]), 3])
    return out


def set_object(folder: Path, label: str, keep: set[str] | None = None, design: dict | None = None) -> dict:
    r = common.read_json(folder / "frf.json")
    npz = np.load(folder / "frf_shapes.npz")
    keys = [str(k) for k in npz["keys"]]
    setup = part_setup(design, npz) if design is not None else None
    x = r["frequencies_Hz"]
    curves = {p: {o: {"acc": common.pack_f32(c["acc_mps2"]), "disp": common.pack_f32(c["disp_mm"])}
                  for o, c in r["curves"][p].items()} for p in r["curves"]}
    peaks = {p: {o: [{"f_Hz": k["f_Hz"], "acc_mps2": k["acc_mps2"], "disp_mm": k["disp_mm"], "mode_f_Hz": k["mode_f_Hz"],
                      "kind": kind_ja(k["mode_kind"])} for k in pk] for o, pk in r["peaks"][p].items()}
             for p in r["peaks"]}
    shapes = {}
    for i, k in enumerate(keys):
        if keep is not None and k not in keep:
            continue
        d = npz["disp"][i]
        re_, u_re = pack_scaled(d.real, d.shape)
        im_, u_im = pack_scaled(d.imag, d.shape)
        vm, vm_max = pack_scaled(npz["vm"][i], npz["vm"][i].shape)
        shapes[k] = {"re": re_, "im": im_, "vm": vm, "u_max": float(np.max(np.abs(d))), "vm_max": vm_max}
        if setup is not None:
            shapes[k]["parts"] = part_shape(design, setup[0], setup[1], setup[2], setup[3], d)
    hop = r.get("hop_band_Hz") or []
    return {"name": r["name"], "label": label, "doc": r.get("doc", ""), "tyre_N_per_mm": r["tyre_N_per_mm"],
            "parts": setup[0] if setup is not None else [],
            "shell": shell_of(npz), "x": common.pack_f32(x), "curves": curves, "peaks": peaks,
            "modes": [{"f_Hz": m["f_Hz"], "kind": kind_ja(m["kind"])} for m in r["modes"]],
            "rollbar_Hz": r.get("rollbar_mode_Hz"), "frame_Hz": r.get("first_frame_mode_Hz"),
            "hop_Hz": [min(hop), max(hop)] if hop else None, "shapes": shapes,
            "static_check": {p: v["largest_rel_err_0Hz"] for p, v in r["static_check"].items()},
            "_raw": r}


def build(args) -> dict:
    base = Path(args.dir)
    names = args.sets
    labels = args.labels or [f"対策前 {names[0]}"] + [f"対策後 {n}" for n in names[1:]]
    keep = chosen_keys([common.read_json(base / n / "frf.json") for n in names], getattr(args, "per_pattern", 8) or 8)
    designs = [common.read_json(Path(p)) for p in args.designs] if getattr(args, "designs", None) else [None] * len(names)
    if len(designs) != len(names):
        raise SystemExit("--designs: one design JSON per set, in the order of --sets")
    sets = [set_object(base / n, lab, keep, dz) for n, lab, dz in zip(names, labels, designs)]
    r0 = sets[0].pop("_raw")
    raws = [r0] + [s.pop("_raw") for s in sets[1:]]
    # side by side across y: each set beside the one before with a gap
    off, offs = 0.0, []
    for s in sets:
        offs.append(off)
        off += 2200.0
    for s, o in zip(sets, offs):
        s["offset_y"] = o
    keys = {}
    for k in sets[0]["shapes"]:
        p, f = k.split("@")
        keys.setdefault(p, []).append(float(f))
    for p in keys:
        keys[p].sort()
    damp = r0["damping"]
    c0 = next(iter(damp["corners"].values()))
    obj = {
        "field": "nvh", "model": "frf", "title": "NVH: 接地面の上下の加振への応答 (対策前と後)",
        "source": {"run": base.resolve().name, "version": "", "analysis_id": "k1_shell.frf", "tool": "k1_shell.frf",
                   "load_case_id": r0.get("load_case") or "", "design_name": " / ".join(names), "design_version": None,
                   "design_key": "frf:" + "+".join(names)},
        "frame": None, "parts": [], "scene": [], "ground": None, "vectors": [], "results": [], "assumptions": [],
        "tyre": None, "input_mm": 1.0,
        "patterns": {p: PATTERN_JA.get(p, p) for p in r0["curves"]},
        "outputs": {o: OUTPUT_JA.get(o, o) for o in r0["outputs"]},
        "sets": sets, "keys": keys,
    }
    obj["inputs"] = [
        {"label": "入力", "value": "タイヤのばねの地面側の端を上下に 1 mm (振幅) 動かす (3 通り: 4 輪同相、左右逆、前後逆)",
         "source": "k1_shell.frf (基礎加振): 地面の変位 × (タイヤのばね + タイヤの減衰 × iω) が接地点を押す"},
        {"label": "周波数", "value": "0.1 〜 30 Hz、0.1 Hz おき (山の近く ±0.2 Hz は 0.01 Hz おき)", "source": "studies/lere/frf.py"},
        {"label": "モデル", "value": f"シェル {r0['element_size_mm']:g} mm の網、サスペンションのリンク (腕・ナックル+車輪の剛体・ダンパー軸のばね・タイヤの上下のばね)、"
                                   f"積載 {r0.get('load_case') or ''}、タイヤ {r0['tyre_N_per_mm']:g} N/mm",
         "source": "k1_shell.modal suspension=\"linkage\""},
        {"label": "モードの重ね合わせ", "value": f"剛体 {r0['n_zero']} 個 + 弾性 {r0['n_modes'] - r0['n_zero']} 個 (フル減衰行列を周波数ごとに解く)",
         "source": "k1_shell.frf.modal_response"},
        {"label": "ダンパー", "value": f"車輪で {c0['c_wheel']:g} N·s/mm → ダンパー軸で {c0['c_axis']:.2f} N·s/mm (レバー比 {c0['motion_ratio']:.3f} の 2 乗で割る)",
         "source": "設計の suspension.damping_Ns_per_mm (K2 の値)。ダンパー軸へはリンクを剛として移す"},
        {"label": "タイヤの減衰", "value": f"{c0['c_tyre']:.3f} N·s/mm (2 ζ √(k m_u)、ζ = 設計の tyre_damping_ratio)",
         "source": "設計の変数 tyre_damping_ratio (設計の仮定)、K2 の式"},
        {"label": "構造の減衰", "value": f"モード減衰比 {100 * damp['struct_zeta']:g} % (すべての弾性モード)",
         "source": "確かめていない (HIRAKU の仮定)"},
    ]
    zs = max(max(r["zero_modes_input_share"].values()) for r in raws)
    stat = max(max(v["largest_rel_err_0Hz"] for v in r["static_check"].values()) for r in raws)
    stat01 = max(max(v["largest_rel_err_0.1Hz"] for v in r["static_check"].values()) for r in raws)
    obj["notes"] = [
        "入力の振幅は 1 mm。グラフの値は入力 1 mm あたりの上下の加速度 [m/s²] (または変位 [mm])。",
        "形は複素数の応答の実部を 1 周期まわして動かす (位相)。変形は表示の倍率で、色は 1 周期の中の von Mises の最大 (入力 1 mm あたり MPa)。",
        f"剛体モード 3 個 (前後・横・ヨーに自由) も重ね合わせに入れた。上下の入力はこれをほとんど動かさない (入力の力の分け前は最大 {zs:.1e})。",
        f"打ち切りの確かめ: 静的な応答 (0 Hz) を K u = f の直接の解と比べて、出力の点で最大 {100 * stat:.2f} % (0.1 Hz の応答で {100 * stat01:.2f} %)。",
        "構造の減衰 (モード減衰比) は確かめていない仮定。ダンパーとタイヤの減衰は設計の値 (タイヤの減衰比は設計の仮定)。",
        "左右逆 (ロール) のとき、ロールバーの上の中央とコラム (左右の取付の平均) は対称の中心なので上下はほぼ 0。ロールバーの横の動きを見る。",
        "表示の網は解いた網より粗い (各表示の節は、解いた網のいちばん近い節の動き。要素の色は、近い解いた要素の最大)。",
        "ばね下のホップの帯 (灰色) と、ロールバー・最初のフレームのモード (破線) をグラフに印した。",
    ]
    if any(s["parts"] for s in sets):
        obj["notes"].append(PART_NOTE)
    return obj


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="python -m viewer.src.nvh_frf", description="Write viewer/data/nvh_frf.js")
    ap.add_argument("dir", type=Path, help="studies/lere/frf.py's output folder")
    ap.add_argument("--sets", nargs="+", default=["A", "REC"], help="sets in order (the first is before the fix)")
    ap.add_argument("--labels", nargs="+")
    ap.add_argument("--designs", nargs="+", help="each set's design JSON (same order as --sets): the part boxes and "
                    "their motion with the nodes they sit on; without it no parts are drawn")
    ap.add_argument("--per-pattern", type=int, default=8, help="stored shapes kept per input pattern (data size)")
    ap.add_argument("--out", type=Path, default=common.DEFAULT_OUT)
    args = ap.parse_args(argv)
    obj = build(args)
    path = common.write_data("nvh_frf", obj, args.out)
    print(f"wrote {path} ({path.stat().st_size} bytes)")
    return path


if __name__ == "__main__":
    main(sys.argv[1:])

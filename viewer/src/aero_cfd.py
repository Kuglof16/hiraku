"""Aerodynamics, the CFD variant of the aero field: python -m viewer.src.aero_cfd <case folder or sweep folder> --design <design.json>

Reads what hiraku.cfd saves for a run: <case>/fields.npz (the mean flow) and <case>/case.json (VehicleRun.summary). The
folder is one case, or a folder with one case per sub-folder (one per yaw angle). The design comes from --design, else
design.json in the folder or the one above it. Streamlines come from hiraku.cfd.streamlines; nothing here is solved.

The data (viewer/data/aero_cfd.js, window.VIEW_DATA["aero_cfd"]), on top of what every field has (common.py):
    model 'cfd', status {text, source}: how far the accuracy has been checked, always shown on the page
    on_board [bool per part of parts]: the parts in the calculation
    wheels [{c, r, width}] mm, ground {z, from}, nets (packed f32, n x 2 x 3, mm: the side nets' wires)
    table [{yaw_deg, Fx, Fy, Fz, Mx, My, Mz, Cd, CdA_m2, Cy, Cl, q_Pa, reference_area_m2, spread_pct}]: N, N m, vehicle axes about the moment centre
    yaws [{yaw_deg, speed_ms, air_speed_ms, speed_top (the colour scale's top, m/s: the cases' air speeds differ a lot), lines {points (i16, n x 3, mm, design axes), speed (i16, m/s), cp (i16), time (f32, s from the line's
           first point, from the local speed), offsets (i32), near (per line, mm: the least distance to the vehicle's box),
           side (per line: line_sides)}, count, ends, side_axis (the grid's y axis in design axes)}]
    centre_mm: the moment centre in design mm
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

for _name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_name, "4")  # numpy's matrix products would otherwise take every core

import numpy as np

from viewer.src import common

Record = dict[str, Any]

STATUS_DEFAULT = "精度は確かめていない"
TOOL = "cfd_aero"
MM = 1e-3


def extra(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--design", type=Path, help="the design's JSON (default: design.json in the folder or the one above it)")
    ap.add_argument("--status", help="how far the accuracy has been checked, as it is to be shown (default: the case's own, else 確かめていない)")
    ap.add_argument("--loading", default="two_cargo", help="the loading the run used (default two_cargo, run_vehicle's)")
    ap.add_argument("--seeds", type=int, nargs=2, default=(22, 14), help="seeds across and up the upstream plane")
    ap.add_argument("--spacing", type=float, default=0.08, help="distance between the stored points of a line, m")
    ap.add_argument("--downstream", type=float, default=7.0, help="lines end this far behind the moment centre, m")
    ap.add_argument("--yaws", type=float, nargs="+", help="only the cases of these yaw angles, deg (default: every case in the folder)")


def case_folders(folder: Path) -> list[Path]:
    """The case folder itself, or its sub-folders that hold a case."""
    folder = Path(folder)
    if (folder / "case.json").is_file() and (folder / "fields.npz").is_file():
        return [folder]
    found = sorted(p for p in folder.iterdir() if p.is_dir() and (p / "case.json").is_file() and (p / "fields.npz").is_file())
    if not found:
        raise FileNotFoundError(f"{folder.name}: no case.json with fields.npz here or in its sub-folders")
    return found


def design_of(args: argparse.Namespace) -> Record:
    run = Path(args.run)
    for p in [args.design, run / "design.json", run.parent / "design.json"]:
        if p and Path(p).is_file():
            return common.read_json(p)
    raise FileNotFoundError("no design: give --design")


def finest_per_case(folders: list[Path], summaries: list[Record]) -> tuple[list[Path], list[Record]]:
    """One case per speed and yaw: the finest grid. A sweep's folder also holds its grid study's coarser cases."""
    best: dict[tuple[float, float], int] = {}
    for i, s in enumerate(summaries):
        key = (round(float(s["speed_ms"]), 6), round(float(s["yaw_deg"]), 6))
        if key not in best or float(s["dx"]) < float(summaries[best[key]]["dx"]):
            best[key] = i
    kept = sorted(best.values())
    return [folders[i] for i in kept], [summaries[i] for i in kept]


def status_of(args: argparse.Namespace, summaries: list[Record]) -> Record:
    """The status as received: --status, else the sweep record's physically_validated, else the cases' own (a 'validation'
    text or {status, source}), else not checked."""
    if getattr(args, "status", None):
        return {"text": args.status, "source": "指定された文"}
    rec = Path(args.run) / "aero_record.json"
    if rec.is_file():
        checked = common.read_json(rec).get("physically_validated")
        if checked is not None:
            return {"text": "実験と比べて確かめた" if checked else "実験と比べて確かめていない", "source": "記録の physically_validated"}
    for s in summaries:
        v = s.get("validation")
        if isinstance(v, str) and v:
            return {"text": v, "source": "計算の記録"}
        if isinstance(v, dict) and v.get("status"):
            return {"text": str(v["status"]), "source": str(v.get("source") or "計算の記録")}
    return {"text": STATUS_DEFAULT, "source": "記録に検証の結果がない"}


def net_wires(part: Record, pitch_mm: float) -> list[list[float]]:
    """A side net's wires as segments (mm): two sets in the plane of its box, at the design's pitch."""
    from hiraku.cfd.vehicle import rpy_matrix

    box = part["box"]
    size = np.asarray(box["size_mm"], float)
    R = rpy_matrix(box.get("rpy_deg", (0.0, 0.0, 0.0)))
    c = np.asarray(box["center_mm"], float)
    thin = int(np.argmin(size))
    a, b = [k for k in range(3) if k != thin]
    out: list[list[float]] = []
    for along, across in ((a, b), (b, a)):
        n = int(size[across] / pitch_mm)
        offs = (np.arange(n + 1) - n / 2.0) * pitch_mm
        for s in offs:
            p, q = np.zeros(3), np.zeros(3)
            p[across] = q[across] = s
            p[along], q[along] = -size[along] / 2.0, size[along] / 2.0
            out.append((c + R @ p).tolist() + (c + R @ q).tolist())
    return out


def ledger_of(design: Record, parts: list[Record], on_board: list[bool], loading: str) -> Record:
    """How each part enters this calculation, in the shape of common.parts_ledger: a shape in the air, or left off."""
    FN = "hiraku.cfd.vehicle.design_shapes"
    rows, not_in = [], []
    for p, on in zip(parts, on_board):
        if on:
            how = {"side_net": "多孔の面: 空気は損失係数を受けて通り抜ける", "wheel": "支持点の円柱: 回る壁 (部品の箱は使わない)"}.get(p.get("kind"), "形だけ: 空気の流れを止める箱 (質量も剛性も使わない)")
            rows.append({"id": p["id"], "kind": p.get("kind", ""), "mass_kg": None, "cat": "body", "nodes": [], "fn": FN, "how": how, "stiffness": False})
        else:
            how = f"積み方 {loading} で載っていない"
            rows.append({"id": p["id"], "kind": p.get("kind", ""), "mass_kg": None, "cat": "not_in", "nodes": [], "fn": FN, "how": how, "stiffness": False})
            not_in.append({"id": p["id"], "why": how})
    table = []
    for cat in ("body", "not_in"):
        n = sum(1 for r in rows if r["cat"] == cat)
        if n:
            table.append({"cat": cat, "label": common.CATEGORIES[cat], "count": n, "mass_kg": None})
    return {"tool": TOOL, "loading": loading, "loading_source": "計算の条件", "categories": common.CATEGORIES, "parts": rows,
            "members": {"count": len(design.get("members", [])), "mass_kg": None, "fn": FN, "how": "部材の管も形だけ"},
            "table": table, "stated": [], "error": None, "notes": [], "no_stiffness": [], "not_in": not_in, "parts_in_kg": None, "total_kg": None}


def lines_of(folder: Path, summary: Record, design: Record, args: argparse.Namespace) -> Record:
    """The streamlines of one case in design axes (mm) and packed."""
    from hiraku.cfd.streamlines import MeanField, ShapeStop, named_seeds, trace
    from hiraku.cfd.vehicle import design_shapes, moment_centre, to_grid

    yaw, speed = float(summary["yaw_deg"]), float(summary["speed_ms"])
    dx = float(summary["dx"])
    shapes, _ = design_shapes(design, yaw, speed, args.loading, float(summary["numerics"]["u_lattice"]), dx=dx)
    field = MeanField.from_case(folder)
    stop = ShapeStop(shapes)
    seeds = named_seeds("upstream", stop.box_lo, stop.box_hi, tuple(args.seeds), margin=0.5, upstream=1.6, floor=max(field.lo[2], 0.06))
    region = (field.lo, (args.downstream, field.hi[1], field.hi[2]))
    lines = trace(field, seeds, stop=stop, region=region, max_length_m=25.0)
    gaps = np.maximum(np.maximum(stop.box_lo - lines.points, lines.points - stop.box_hi), 0.0)
    near = np.minimum.reduceat(np.linalg.norm(gaps, axis=1), lines.offsets[:-1]) if len(lines) else np.zeros(0)
    side = line_sides(lines, stop.box_lo, stop.box_hi)
    lines = lines.thinned(max(1, int(round(args.spacing / (field.dx / 4.0)))))
    ref, _ = moment_centre(design)
    R = to_grid(yaw)
    mm = lines.points @ R / MM + ref  # grid = R vehicle, so vehicle = R^T grid; each row: p @ R
    n = len(lines.points)
    packed = {"points": common.pack_i16(mm.ravel().tolist(), 1.0, [n, 3]), "speed": common.pack_i16(lines.speed.tolist(), 0.01), "cp": common.pack_i16(lines.cp.tolist(), 0.001),
              "time": common.pack_f32(lines.time.tolist()), "offsets": common.pack_i32(lines.offsets.tolist()), "near": [round(float(v) / MM) for v in near], "side": side.tolist()}
    return {"yaw_deg": yaw, "speed_ms": speed, "air_speed_ms": float(summary["air_speed_ms"]), "speed_top": speed_top(lines.speed), "lines": packed, "count": len(lines), "ends": lines.stop_counts(),
            "side_axis": [float(v) for v in R[1]]}


def line_sides(lines: Any, lo: np.ndarray, hi: np.ndarray, gap: float = 0.15, seed_gap: float = 0.3) -> np.ndarray:
    """For the page's 手前の線を隠す, per line: bit 1 when the line passes between a camera on the grid's y- side and the vehicle,
    bit 2 the same for a camera on the y+ side. Such a line starts more than seed_gap off the centre on that side, or runs
    beside the vehicle, below its top, off that flank. Whole lines are hidden on the page, none is cut or moved."""
    if not len(lines):
        return np.zeros(0, np.int64)
    p, off = lines.points, lines.offsets[:-1]
    abreast = (p[:, 0] > lo[0] - 0.8) & (p[:, 0] < hi[0] + 0.8) & (p[:, 2] < hi[2])
    any_of = lambda m: np.add.reduceat(m.astype(np.int64), off) > 0
    minus = any_of(abreast & (p[:, 1] < lo[1] - gap)) | (p[off, 1] < -seed_gap)
    plus = any_of(abreast & (p[:, 1] > hi[1] + gap)) | (p[off, 1] > seed_gap)
    return minus.astype(np.int64) + 2 * plus.astype(np.int64)


def table_row(s: Record) -> Record:
    f, c = s["forces_N"], s["coefficients"]
    return {"yaw_deg": float(s["yaw_deg"]), "speed_ms": float(s["speed_ms"]), **{k: float(f[k]) for k in ("Fx", "Fy", "Fz", "Mx", "My", "Mz")}, "Cd": float(c["Cd"]), "CdA_m2": float(c["CdA_m2"]), "Cy": float(c["Cy"]), "Cl": float(c["Cl"]),
            "q_Pa": float(c["q_Pa"]), "reference_area_m2": float(c["reference_area_m2"]), "spread_pct": s.get("convergence", {}).get("spread_pct")}


def yaw_text(yaw: float) -> str:
    """As the page's buttons say it: 0°, +5°, -5°."""
    return f"{float(yaw):+g}°" if yaw else "0°"


def results_of(summaries: list[Record]) -> list[Record]:
    """The table again as the rows the page's 記録の結果 panel lists."""
    units = (("Fx", "N"), ("Fy", "N"), ("Fz", "N"), ("Mx", "N m"), ("My", "N m"), ("Mz", "N m"), ("Cd", "1"), ("CdA_m2", "m2"), ("Cy", "1"), ("Cl", "1"))
    return [{"q": k, "v": table_row(s)[k], "u": u, "where": f"横風の角度 {yaw_text(s['yaw_deg'])}"} for s in summaries for k, u in units]


def speed_top(speeds: np.ndarray) -> float:
    """The colour scale's top from the speeds of all the lines: a round number just above nearly all of them."""
    raw = float(np.percentile(speeds, 99.8)) / 4.0
    power = 10.0 ** math.floor(math.log10(raw))
    step = next(k * power for k in (1.0, 2.0, 2.5, 4.0, 5.0, 10.0) if k * power >= raw * 0.999)
    return math.ceil(4.0 * raw / step - 1e-9) * step


def build(args: argparse.Namespace) -> Record:
    from hiraku.cfd.vehicle import moment_centre
    from hiraku.contract.loading import parts_on_board

    folders = case_folders(args.run)
    design = design_of(args)
    summaries = [json.loads((f / "case.json").read_text(encoding="utf-8")) for f in folders]
    wanted = getattr(args, "yaws", None)
    if wanted:
        kept = [i for i, s in enumerate(summaries) if any(abs(float(s["yaw_deg"]) - w) < 1e-6 for w in wanted)]
        if not kept:
            raise LookupError(f"no case of yaw {wanted} in {Path(args.run).name}")
        folders, summaries = [folders[i] for i in kept], [summaries[i] for i in kept]
    folders, summaries = finest_per_case(folders, summaries)
    order = sorted(range(len(folders)), key=lambda i: abs(float(summaries[i]["yaw_deg"])) + (0.5 if float(summaries[i]["yaw_deg"]) < 0 else 0.0))
    folders, summaries = [folders[i] for i in order], [summaries[i] for i in order]
    for s in summaries:
        if s.get("design_version") != design.get("design_version"):
            raise ValueError(f"the case is of design v{s.get('design_version')}, the design given is v{design.get('design_version')}")
    yaws = [lines_of(f, s, design, args) for f, s in zip(folders, summaries)]
    parts = common.parts_of(design)
    ids = {p["id"] for p in parts_on_board(design, args.loading)}
    on_board = [p["id"] in ids for p in parts]
    variables = design.get("variables") or {}
    pitch = float((variables.get("side_net_pitch") or {}).get("value", 50.0))
    wires = [v for p in design.get("parts", []) if p.get("kind") == "side_net" and p.get("box") and p["id"] in ids for w in net_wires(p, pitch) for v in w]
    wheels = [{"c": [float(v) for v in s["position_mm"]], "r": float(s["radius_mm"]), "width": float(s.get("width_mm", 200.0))} for s in design.get("supports", []) if "radius_mm" in s]
    ref, ground = moment_centre(design)
    s0 = summaries[0]
    dxs = sorted({float(s["dx"]) for s in summaries})
    name = Path(args.run).resolve().name
    inputs = [{"label": "車速", "value": "、".join(f"{v:.0f} km/h" for v in sorted({round(float(s["speed_ms"]) * 3.6, 3) for s in summaries})), "source": "計算の条件"},
              {"label": "横風の角度", "value": "、".join(yaw_text(s["yaw_deg"]) for s in summaries), "source": "計算の条件 (+ は車の左からの風)"},
              {"label": "格子の大きさ", "value": "、".join(f"{d * 1000:.0f} mm" for d in dxs), "source": "計算の条件"},
              {"label": "基準の面積", "value": f"{float(s0['coefficients']['reference_area_m2']):.3f} m²", "source": "部品の投影面積 (計算で求めた値)"},
              {"label": "積み方", "value": args.loading, "source": "計算の条件"}]
    notes = ["流線は、計算で保存した平均の速度場を 4 次のルンゲ・クッタ法でたどった線。固体の節点や部品の形、計算の領域の端、空気がほぼ止まった所で終わる。",
             "光は流線の上だけを動く。各点の速さから求めた到達時間のとおりに進む (実時間に倍率をかけて見せる)。",
             "線がきれいでも精度の証拠にはならない。",
             "力は車の座標 (x 前、y 左、z 上)、回転の中心は前後の軸の中央・左右の中央・地面。"]
    return {"field": "aero_cfd", "model": "cfd", "title": "車のまわりの空気の流れ (平均)",
            "source": {"run": name, "version": f"v{design.get('design_version')}", "analysis_id": "", "tool": TOOL, "load_case_id": "", "design_name": design.get("name", ""),
                       "design_version": design.get("design_version"), "design_key": common.design_key(design)},
            "status": status_of(args, summaries), "frame": common.frame_of(design), "parts": parts, "on_board": on_board, "scene": [], "vectors": [], "results": results_of(summaries),
            "centre_mm": [float(v) for v in ref],
            "assumptions": list(s0.get("assumptions") or []), "inputs": inputs, "notes": notes, "ledger": ledger_of(design, parts, on_board, args.loading), "wheels": wheels,
            "ground": {"z": float(ground), "from": "設計の支持点: 車輪の中心の高さ − 半径"}, "nets": common.pack_f32(wires, [len(wires) // 6, 2, 3]), "table": [table_row(s) for s in summaries],
            "yaws": yaws}


def for_record(run: Path, vdir: Path, rec: Record) -> Record | str:
    """viewer.src.build's entry: the data of a cfd_aero analysis, or the reason in one plain line. The sweep's folder is
    <run>/cfd/v<N>/<speed>_<grid>/, the newest of that version whose record holds this load case."""
    version = rec.get("design_version")
    found = sorted((Path(run) / "cfd" / f"v{version}").glob("*/aero_record.json"), key=lambda p: p.stat().st_mtime_ns)
    wanted = rec.get("load_case_id")
    for path in reversed(found):
        if any(a.get("load_case_id") == wanted for a in common.read_json(path).get("analyses", [])):
            design = Path(vdir) / "design.json"
            if not design.is_file():
                return "流れの画面には設計 (design.json) がいる。記録のフォルダにない"
            ap = argparse.ArgumentParser()
            ap.add_argument("run", type=Path)
            extra(ap)
            args = ap.parse_args([str(path.parent), "--design", str(design)])
            loading = (common.read_json(path).get("aero_map") or {}).get("loading")
            if loading:
                args.loading = loading
            if not any((p / "fields.npz").is_file() for p in path.parent.iterdir() if p.is_dir()):
                return "流れの場 (fields.npz) が記録のフォルダにない"
            return build(args)
    return f"この解析の流れの記録 (cfd/v{version} の aero_record.json) が run のフォルダにない"


if __name__ == "__main__":
    common.main("aero_cfd", build, extra=extra, argv=sys.argv[1:])

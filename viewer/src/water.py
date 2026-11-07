"""The flooded road's place in the viewer: the stored water record (hiraku.cfd.water's case.json and replay.npz) as data for the
page. Nothing is computed here and no GPU is needed.

    python -m viewer.src.water <case folder> --design <design.json>   # -> viewer/fixtures/water.js

The stored file is kept in the repository (viewer/fixtures/, not the ignored viewer/data/), so the place opens with no run and no GPU.

The data (window.VIEW_DATA["water"]), on top of what every field has (common.py):
    statement                the sentence the page always shows: for showing, not a judgement of what can be crossed
    frames, frame_s, poses   the vehicle's x, z (m) and pitch (rad) in the road's frame, per frame
    columns {nx, ny, x0_mm, y0_mm, step_mm}, surface (i16 mm, frames x nx x ny), road_mm (the road's height per x column)
    spray (i16 mm, points x 3), spray_start (i32): a sample of the flying particles per frame, in the road's frame
    records [{t, depth_mean_mm, depth_max_mm, reached_pct, top_m}], wheels, on_board, centre_mm, checks
"""

from __future__ import annotations

import argparse
import base64
from pathlib import Path
from typing import Any

import numpy as np

from hiraku.contract.loading import parts_on_board
from viewer.src import common

Record = dict[str, Any]
TOOL = "flooded_road"
SPRAY_PER_FRAME = 1200  # the sample the page draws: enough to see the spray, small enough to keep in the repository
SEED = 5
STORED = common.VIEWER / "fixtures"
CHECK_NAMES = {"still_water": "静止した水の水位と残留速度", "moving_box": "移動する箱の運動量の釣り合い", "standing_vehicle": "計算の形と描く形が同じ (水の粒子が形の中に残らない)",
               "standing_vehicle_grid_only": "同じ確かめの、粒子を形の外へ戻す段を外した参考", "standing_vehicle_25mm": "同じ確かめの 25 mm 格子"}


def _hex(packed: Record) -> Record:
    """A packed array with its bytes as hex, not base64. The stored file is scanned for private words in any letter case, and
    1.5 MB of base64 holds such letter runs by chance; hex has only 0-9 and a-f. The page turns it back (js/water.js)."""
    out = dict(packed)
    out["hex"] = base64.b64decode(out.pop("b64")).hex()
    return out


def extra(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--design", type=Path, help="the design the case was run on (default: <case>/design.json)")
    ap.set_defaults(out=STORED)


def _road_height(depth_level_m: float, half_length_m: float, slope: float, blend_m: float, x: np.ndarray) -> np.ndarray:
    """The road's height over x, the same shape as hiraku.cfd.water.Road (a dip with ramps at both ends)."""
    foot = half_length_m - depth_level_m / slope
    d = np.abs(x) - foot
    b = blend_m
    s = np.where(d <= -b, 0.0, np.where(d >= b, d, 0.5 * (d + b) - b / np.pi * np.cos(0.5 * np.pi * (d + b) / b) * np.sin(0.5 * np.pi * (d + b) / b)))
    return slope * s


def sample_spray(spray: np.ndarray, start: np.ndarray, lo: np.ndarray, scale: np.ndarray, per_frame: int = SPRAY_PER_FRAME, seed: int = SEED) -> tuple[np.ndarray, np.ndarray]:
    """At most per_frame particles of each frame, in mm, the same choice on every build."""
    rng = np.random.default_rng(seed)
    points: list[np.ndarray] = []
    offsets = [0]
    for f in range(len(start) - 1):
        a, b = int(start[f]), int(start[f + 1])
        idx = np.arange(a, b) if b - a <= per_frame else np.sort(rng.choice(np.arange(a, b), per_frame, replace=False))
        mm = (spray[idx].astype(np.float64) * scale + lo) * 1000.0
        points.append(mm)
        offsets.append(offsets[-1] + len(idx))
    return np.vstack(points) if points else np.zeros((0, 3)), np.array(offsets)


def ledger_of(design: Record, parts: list[Record], on_board: list[bool], loading: str) -> Record:
    """How each part enters this calculation, in the shape of common.parts_ledger: a shape the water goes around, or left off."""
    fn = "hiraku.cfd.vehicle.design_shapes"
    rows, not_in = [], []
    for p, on in zip(parts, on_board):
        if on:
            how = {"side_net": "網は水を通す", "wheel": "支持点の円柱: 回る壁 (部品の箱は使わない)"}.get(p.get("kind"), "形だけ: 水はこの形をよける")
            rows.append({"id": p["id"], "kind": p.get("kind", ""), "mass_kg": None, "cat": "body", "nodes": [], "fn": fn, "how": how, "stiffness": False})
        else:
            how = f"積み方 {loading} で載っていない"
            rows.append({"id": p["id"], "kind": p.get("kind", ""), "mass_kg": None, "cat": "not_in", "nodes": [], "fn": fn, "how": how, "stiffness": False})
            not_in.append({"id": p["id"], "why": how})
    table = [{"cat": cat, "label": common.CATEGORIES[cat], "count": sum(1 for r in rows if r["cat"] == cat), "mass_kg": None}
             for cat in ("body", "not_in") if any(r["cat"] == cat for r in rows)]
    return {"tool": TOOL, "loading": loading, "loading_source": "計算の条件", "categories": common.CATEGORIES, "parts": rows,
            "members": {"count": len(design.get("members", [])), "mass_kg": None, "fn": fn, "how": "部材の管も形だけ"},
            "table": table, "stated": [], "error": None, "notes": [], "no_stiffness": [], "not_in": not_in, "parts_in_kg": None, "total_kg": None}


def moment_centre_mm(design: Record) -> list[float]:
    """Mid wheelbase, mid track, on the ground (the same point as hiraku.cfd.vehicle.moment_centre)."""
    sup = design["supports"]
    pos = np.array([s["position_mm"] for s in sup], float)
    c = 0.5 * (pos.min(axis=0) + pos.max(axis=0))
    return [float(c[0]), float(c[1]), float(min(s["position_mm"][2] - s.get("radius_mm", 0.0) for s in sup))]


def build(args: argparse.Namespace) -> Record:
    folder = Path(args.run)
    case = common.read_json(folder / "case.json")
    design_path = args.design or folder / "design.json"
    design = common.read_json(design_path)
    inputs_case = case["inputs"]
    if inputs_case.get("design_version") != design.get("design_version"):
        raise ValueError(f"the case is of design v{inputs_case.get('design_version')}, the design given is v{design.get('design_version')}")
    with np.load(folder / "replay.npz") as d:
        statement = str(d["statement"])
        surface = d["surface_mm"]
        column_m, x0_m, y0_m = float(d["column_m"]), float(d["x0_m"]), float(d["y0_m"])
        poses = d["poses"]
        frame_s = float(d["frame_s"])
        spray_mm, spray_start = sample_spray(d["spray"], d["spray_start"], d["spray_lo"], d["spray_scale"])
    frames, nx, ny = surface.shape
    road = case["road"]
    xs = x0_m + (np.arange(nx) + 0.5) * column_m
    road_mm = 1000.0 * _road_height(float(road["level_m"]), 0.5 * float(road["water_length_m"]), float(road["slope"]), float(road.get("blend_m", 0.3)), xs)
    loading = inputs_case.get("loading") or "two_cargo"
    parts = common.parts_of(design)
    ids = {p["id"] for p in parts_on_board(design, loading)}
    on_board = [p["id"] in ids for p in parts]
    wheels = [{"c": [float(v) for v in s["position_mm"]], "r": float(s["radius_mm"]), "width": float(s.get("width_mm", 200.0))} for s in design.get("supports", []) if "radius_mm" in s]
    records = [{"t": float(r["t"]), "depth_mean_mm": float(r["depth_mean_mm"]), "depth_max_mm": float(r["depth_max_mm"]), "reached_pct": 100.0 * float(r["reached_share"]), "top_m": float(r["top_m"])}
               for r in case["records"]]
    checks = [{"name": CHECK_NAMES.get(k, k), "key": k} for k in case.get("tests_passed", {}) if k in ("still_water", "moving_box", "standing_vehicle")]
    under = case["underfloor"]
    inputs = [{"label": "水深", "value": f"{float(road['level_m']) * 1000.0:.0f} mm", "source": "計算の条件 (粒子の層の数で決まる)"},
              {"label": "車速", "value": f"{float(inputs_case['speed_kmh']):g} km/h", "source": "計算の条件 (一定の速さ)"},
              {"label": "車輪", "value": "回す" if inputs_case.get("rotate_wheels") else "回さない", "source": "計算の条件"},
              {"label": "格子の大きさ", "value": f"{float(inputs_case['dx_m']) * 1000.0:.0f} mm", "source": "計算の条件"},
              {"label": "水の粒子", "value": f"{int(case['particles']):,} 個", "source": "計算の条件"},
              {"label": "いちばん低い部品", "value": f"{float(under['lowest_part_m']) * 1000.0:.0f} mm", "source": "部品の形から計算"},
              {"label": "積み方", "value": loading, "source": "計算の条件"}]
    notes = ["水面は、柱ごとの粒子の体積から求めた高さ。飛ぶ水は計算の粒子から選んだ一部で、点の大きさは見せるための大きさ。",
             "床下の水の深さ: " + str(under.get("depth_definition", "")),
             "水が届いた床下の列: " + str(under.get("reach_definition", ""))]
    return {"field": "water", "title": "冠水路を渡る水", "statement": statement,
            "source": {"run": "", "version": f"v{design.get('design_version')}", "analysis_id": "", "tool": TOOL, "load_case_id": "", "design_name": design.get("name", ""),
                       "design_version": design.get("design_version"), "design_key": common.design_key(design)},
            "frame": common.frame_of(design), "parts": parts, "on_board": on_board, "scene": [], "vectors": [], "results": [], "wheels": wheels,
            "centre_mm": moment_centre_mm(design), "assumptions": list(case.get("assumptions") or []), "inputs": inputs, "notes": notes,
            "ledger": ledger_of(design, parts, on_board, loading), "ground": None,
            "frames": int(frames), "frame_s": frame_s, "poses": [[round(float(v), 6) for v in p] for p in poses],
            "columns": {"nx": int(nx), "ny": int(ny), "x0_mm": x0_m * 1000.0, "y0_mm": y0_m * 1000.0, "step_mm": column_m * 1000.0},
            "surface": _hex(common.pack_i16(surface.ravel().tolist(), 1.0, [int(frames), int(nx), int(ny)])), "road_mm": [round(float(v), 1) for v in road_mm],
            "spray": _hex(common.pack_i16(spray_mm.ravel().tolist(), 1.0, [len(spray_mm), 3])), "spray_start": _hex(common.pack_i32(spray_start.tolist())),
            "records": records, "checks": checks, "top_of_water_m": float(case.get("top_of_water_m", 0.0)),
            "underfloor": {"max_depth_mean_mm": float(under["max_depth_mean_mm"]), "max_reached_pct": 100.0 * float(under["max_reached_share"]), "lowest_part_mm": float(under["lowest_part_m"]) * 1000.0}}


if __name__ == "__main__":
    common.main("water", build, extra=extra)

"""Handling and stability data for the viewer (K2 rigid 6-DOF replays):

    python -m viewer.src.handling <run dir> [--also <run dir> ...] [--out viewer/data]

Each run dir gives its newest record of tilt (S1), braking (S4), lane_change (S3) or offroad_roll (S5); the
first is shown first. The data object is the first case with every case in `cases` (each a full object):
    motion  (common.k2_motion): t, pose (nT x 6), wheels (nT x nw x channels), corners, part_mode, part_corner
    plots   {channel: {unit, t, v}}: the record's series the page plots
    scene   (common.k2_scene): every scene item (tilt table / slope with angle_channel, gates, cones, road profile)
    judge   the load case's criterion against the record's result (OK / NG); hot_spots; tool
    loads_max: the largest wheel load of the run (the tyre colour range)
The vehicle is K2's one rigid sprung body plus its wheels: the frame is drawn moving rigidly with the body.
"""

from __future__ import annotations

import sys

from viewer.src import common

TOOLS = ("tilt", "braking", "lane_change", "offroad_roll")
TOOL_JA = {"tilt": "傾斜台", "braking": "制動", "lane_change": "レーンチェンジ", "offroad_roll": "横斜面の悪路",
           "launch": "発進加速", "step_climb_dynamic": "段差乗り越え", "crosswind": "横風", "drive_energy": "周回の電力"}
PLOT_CH = ("roll", "pitch", "yaw", "speed", "accel_x", "accel_y", "body_x", "body_y", "cg_y", "table_angle", "slope_angle",
           "steer", "path_y", "wind_speed", "wheel_torque")
PER_WHEEL = ("wheel_load", "friction_use", "wheel_stroke", "wheel_spin")
SCENE_JA = {"tilt_table": "傾斜台", "slope": "斜面", "ground": "路面", "gate": "門", "cone": "コーン",
            "road_profile": "路面の凹凸", "step": "段差"}


def find_any(run, tools, version=None, analysis=None):
    last = None
    for tool in tools:
        try:
            got = common.find_analysis(run, tool, version=version, analysis_id=analysis)
        except LookupError:
            continue
        if last is None or got[1].get("analysis_id", "") > last[1].get("analysis_id", ""):
            last = got
    if last is None:
        raise LookupError(f"{run}: no ok record of {', '.join(tools)}")
    return last


def k2_case(field: str, run, vdir, rec: dict, design: dict) -> dict:
    """One K2 record as a viewer case (the shared part of handling, powertrain and aero)."""
    lc = common.load_case_of(vdir, rec["load_case_id"])
    title = f"{TOOL_JA.get(rec['tool'], rec['tool'])} {rec['load_case_id'].split('-')[0]}: {(lc or {}).get('title', '')}"
    obj = common.base_object(field, title, run, vdir, rec, design)
    obj["tool"] = rec["tool"]
    obj["scene"] = common.k2_scene(rec)
    obj["ledger"] = common.parts_ledger(rec["tool"], design, lc, rec, obj["parts"])
    veh = common.k2_vehicle(design, obj["ledger"]["loading"])
    obj["motion"] = common.k2_motion(rec, veh, obj["ledger"])
    ids = [c["id"] for c in obj["motion"]["corners"]]
    obj["plots"] = common.k2_plot_series(rec, list(PLOT_CH) + [f"{c}.{i}" for c in PER_WHEEL for i in ids])
    loads = [v for i in ids for v in (common.series(rec, f"wheel_load.{i}") or {"values": [0.0]})["values"]]
    obj["loads_max"] = max(loads) if loads else 1.0
    obj["judge"] = common.judge(rec, lc)
    obj["hot_spots"] = common.hot_spots_k2(rec)
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"])
    for s in obj["scene"]:
        if s["kind"] in ("cone",):
            continue
        obj["inputs"].append({"label": SCENE_JA.get(s["kind"], s["kind"]), "value": s["label"], "source": "記録の場面"})
    n_cone = sum(1 for s in obj["scene"] if s["kind"] == "cone")
    if n_cone:
        obj["inputs"].append({"label": "コーン", "value": f"{n_cone} 本", "source": "記録の場面"})
    if not any(r["label"] == "乗員・荷物" for r in obj["inputs"]):
        obj["inputs"].append({"label": "乗員・荷物", "value": common.LOADING_VALUE_JA.get(obj["ledger"]["loading"], obj["ledger"]["loading"]),
                              "source": "既定 (試験条件にない)"})
    m = obj["motion"]
    obj["notes"] = [
        "K2 は剛体 6 自由度の車両モデル: 車体は一つの剛体、輪はサスペンションの通り道に沿って上下する。フレームは車体と一緒に剛体として動かす (たわみは計算に入っていない)。",
        f"動きは記録の時系列そのもの ({m['nT']} 時刻、{m['t_end']:.2f} s)。姿勢は R = Rz(yaw) Ry(pitch) Rx(roll)、位置は設計の原点の位置。",
        "部品の箱: ばね上は車体と一緒、ばね下は輪の中心と一緒、半分ばね下は両者の中ほど (箱の形は変えない)。",
        "矢印: 緑 = 路面の垂直力、赤 = タイヤの摩擦力 (接地点から)。",
    ]
    return obj


def build_cases(args, tools, field):
    runs = [args.run] + list(getattr(args, "also", None) or [])
    cases = []
    for run in runs:
        vdir, rec = find_any(run, tools, args.version if run == args.run else None,
                             args.analysis if run == args.run else None)
        design = common.read_json(vdir / "design.json")
        cases.append(k2_case(field, run, vdir, rec, design))
    return cases


def build(args) -> dict:
    cases = build_cases(args, TOOLS, "handling")
    return {**cases[0], "cases": cases}


def extra(ap):
    ap.add_argument("--also", nargs="*", default=[], help="more run folders, one case each")


if __name__ == "__main__":
    common.main("handling", build, extra=extra, argv=sys.argv[1:])

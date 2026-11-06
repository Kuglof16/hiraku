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

import math
import re
import sys
from pathlib import Path

from viewer.src import common

TOOLS = ("tilt", "braking", "lane_change", "offroad_roll")
TOOL_JA = {"tilt": "傾斜台", "braking": "制動", "lane_change": "レーンチェンジ", "offroad_roll": "横斜面の悪路",
           "rollover_tilt": "横転",
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


def k2_case(field: str, run, vdir, rec: dict, design: dict, title: str | None = None) -> dict:
    """One K2 record as a viewer case (the shared part of handling, powertrain and aero)."""
    lc = common.load_case_of(vdir, rec["load_case_id"])
    title = title or f"{TOOL_JA.get(rec['tool'], rec['tool'])} {rec['load_case_id'].split('-')[0]}: {(lc or {}).get('title', '')}"
    obj = common.base_object(field, title, run, vdir, rec, design)
    obj["tool"] = rec["tool"]
    obj["rollover"] = None   # K2's rollover record (rollover_case) only
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


# ---------------------------------------------------------------- K2's rollover (rollover_tilt), a record file
# K2's rollover_tilt record (hiraku.solvers.k2_vehicle.shelf.rollover, not a HIRAKU run's analysis): the body's
# pose, the wheels and the occupant boxes' centres are in the tilt table's frame (the record's roll is to the
# table); the table turns about x by table_angle about its low edge; the level floor is the scene's ground item,
# given in the table's frame at the held angle. Contact points: ground_force / ground_point / ground_touch.<id>.
ROLL_WINDOW_S = (17.0, 23.0)
CONTACT_JA = {"rollbar": "ロールバー", "hull": "車体の角", "wheel": "輪の外側の面"}


def _rot_x(a_deg: float) -> list[list[float]]:
    c, s = math.cos(math.radians(a_deg)), math.sin(math.radians(a_deg))
    return [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]


def _mv(R, v):
    return [sum(R[i][j] * v[j] for j in range(3)) for i in range(3)]


def _mm(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def contact_name_ja(cid: str) -> str:
    """rollbar_post_R.7 -> ロールバー rollbar_post_R の点 7 (the record's point name: group or member, then the index)."""
    base, _, idx = cid.rpartition(".")
    head = base.split("_", 1)[0]
    kind = CONTACT_JA.get(head, base)
    if head == "wheel":
        return f"輪 {base.split('_', 1)[1]} の外側の面 (点 {idx})"
    return f"{kind} {base} の点 {idx}" if head == "rollbar" else f"{kind} (点 {idx})"


def rollover_floor(scene: list[dict]) -> dict:
    """The table, the hinge (its low edge, table frame mm) and the floor in the world from the record's scene:
    the floor item's edge nearest the table's low edge is the hinge; the world is the table frame turned by the
    held angle (-floor rpy roll) about the hinge."""
    table = next(s for s in scene if s["kind"] == "tilt_table")
    floor = next(s for s in scene if s["kind"] == "ground" and abs(s["rpy"][0]) > 0)
    hold = -floor["rpy"][0]
    R = _rot_x(floor["rpy"][0])
    ey = _mv(R, [0.0, 1.0, 0.0])
    low = [table["c"][1] - table["s"][1] / 2, table["c"][2] + table["s"][2] / 2]   # the table's low edge, its top
    edges = [[floor["c"][a] + sg * floor["s"][1] / 2 * ey[a] for a in range(3)] for sg in (1, -1)]
    hinge = min(edges, key=lambda e: math.hypot(e[1] - low[0], e[2] - low[1]))
    W = _rot_x(hold)
    corners = []
    for sx in (1, -1):
        for sy in (1, -1):
            p = [floor["c"][0] + sx * floor["s"][0] / 2, *(floor["c"][a] + sy * floor["s"][1] / 2 * ey[a] for a in (1, 2))]
            d = _mv(W, [0.0, p[1] - hinge[1], p[2] - hinge[2]])
            corners.append([p[0], hinge[1] + d[1], hinge[2] + d[2]])
    z = [c[2] for c in corners]
    return {"hold_deg": hold, "hinge_mm": [hinge[1], hinge[2]], "table_low_edge_mm": low,
            "world": {"x": [min(c[0] for c in corners), max(c[0] for c in corners)],
                      "y": [min(c[1] for c in corners), max(c[1] for c in corners)], "z": sum(z) / len(z),
                      "z_spread": max(z) - min(z)}}


def rollover_case(path, run, field: str = "handling") -> dict:
    """K2's rollover record file `path` on the design of `run` (a run folder: the version folder of the record's
    design_version, or a version folder itself) as a handling case."""
    rec = dict(common.read_json(Path(path)))
    run = Path(run)
    vdir = run if (run / "design.json").is_file() else run / f"v{int(rec['design_version']):03d}"
    design = common.read_json(vdir / "design.json")
    rec.setdefault("analysis_id", "")
    tip, hold = common.result(rec, "tip_angle"), common.result(rec, "hold_angle")
    title = f"横転 (v{rec['design_version']}) {rec['load_case_id'].split('-')[0]}: 傾斜台を倒れる角度より {hold - tip:.0f}° 先まで回す"
    obj = k2_case(field, run, vdir, rec, design, title=title)
    obj["inputs"] = [r for r in obj["inputs"] if r["label"] != "判定基準"]   # S1's criterion is not this tool's result
    obj["source"]["analysis_id"] = "K2 の横転の計算 (2026-11-06)"
    ser = {s["channel"]: s for s in rec["series"]}
    t = [float(v) for v in ser["body_x"]["t_s"]]
    nT = len(t)

    def at(ch):
        s = ser[ch]
        if len(s["t_s"]) != nT:
            raise ValueError(f"{ch}: {len(s['t_s'])} samples, the body has {nT}")
        return [float(v) for v in s["values"]]

    ang = at("table_angle")
    fl = rollover_floor(obj["scene"])
    hy, hz = fl["hinge_mm"]
    # contacts: force (N) and point (mm) in the table frame, touch 0/1, |F| as a plot
    contacts = []
    for cid in sorted(c.split(".", 1)[1] for c in ser if c.startswith("ground_touch.")):
        F = [at(f"ground_force_{a}.{cid}") for a in "xyz"]
        P = [at(f"ground_point_{a}.{cid}") for a in "xyz"]
        mag = [math.sqrt(F[0][k] ** 2 + F[1][k] ** 2 + F[2][k] ** 2) for k in range(nT)]
        kmax = max(range(nT), key=mag.__getitem__)
        head = cid.split("_", 1)[0].split(".", 1)[0]
        short = f"輪 {cid.split('_', 1)[1].split('.')[0]}" if head == "wheel" else CONTACT_JA.get(head, cid)
        contacts.append({"id": cid, "name": contact_name_ja(cid), "short": short,
                         "force": common.pack_f32([F[a][k] for k in range(nT) for a in range(3)], [nT, 3]),
                         "point": common.pack_f32([P[a][k] * 1000 for k in range(nT) for a in range(3)], [nT, 3]),
                         "touch": common.pack_f32(at(f"ground_touch.{cid}")),
                         "series_peak_N": mag[kmax], "series_peak_t": t[kmax]})
        obj["plots"][f"contact_force.{cid}"] = {"unit": "kN", "t": common.pack_f32(t), "v": common.pack_f32([m / 1000 for m in mag])}
    # the record's peak (its own result; the series are every 40 ms and need not hold that instant) and the
    # vectors the record gives at it
    peak = {"N": common.result(rec, "body_contact_force_peak"), "rollbar_N": common.result(rec, "rollbar_contact_force_peak")}
    where = next((r.get("where", "") for r in rec["results"] if r["quantity"] == "body_contact_force_peak"), "")
    m = re.search(r"at t ([\d.]+) s", where)
    peak["t"] = float(m.group(1)) if m else None
    peak["at"] = where.split(" ", 1)[0] if where else ""
    peak["at_name"] = contact_name_ja(peak["at"]) if peak["at"] else ""
    ids = {c["id"] for c in contacts}
    peak["vectors"] = [{"at": v["at"], "name": contact_name_ja(v["at"]), "o": v["origin_mm"], "f": v["vector_N"]}
                       for v in rec.get("vectors", []) if v.get("at") in ids and any(abs(x) > 0 for x in v["vector_N"])]
    # the body's attitude to the world (the floor): R_world = Rx(table_angle) R_body
    roll, pitch, yaw = at("roll"), at("pitch"), at("yaw")
    Rb = [common.rot_zyx(roll[k], pitch[k], yaw[k]) for k in range(nT)]
    Rw = [_mm(_rot_x(ang[k]), Rb[k]) for k in range(nT)]
    roll_world = [math.degrees(math.atan2(R[2][1], R[2][2])) for R in Rw]
    tilt_world = [math.degrees(math.acos(max(-1.0, min(1.0, R[2][2])))) for R in Rw]
    obj["plots"]["roll_world"] = {"unit": "deg", "t": common.pack_f32(t), "v": common.pack_f32(roll_world)}
    # the occupant boxes (the design's boxes with the loading on board) carried by the body, their centres the
    # record's occupant_x/y/z: the least signed distance of a corner to the floor plane (in the table frame the
    # floor's normal is Rx(-table_angle) z through the hinge)
    from hiraku.contract import loading as contract_loading
    board = {p["id"]: p for p in contract_loading.on_board(design, obj["ledger"]["loading"]).get("parts", [])}
    occ = sorted(c.split(".", 1)[1] for c in ser if c.startswith("occupant_x.") and c.split(".", 1)[1].startswith("occupant"))
    gap, gap_who = [math.inf] * nT, [""] * nT
    for oid in occ:
        box = board[oid]["box"]
        Rp = common.rot_zyx(*box["rpy_deg"]) if box.get("rpy_deg") else [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]
        hs = [v / 2 for v in box["size_mm"]]
        offs = [_mv(Rp, [sx * hs[0], sy * hs[1], sz * hs[2]]) for sx in (1, -1) for sy in (1, -1) for sz in (1, -1)]
        C = [at(f"occupant_{a}.{oid}") for a in "xyz"]
        for k in range(nT):
            a = math.radians(ang[k])
            n = (math.sin(a), math.cos(a))
            for o in offs:
                d = _mv(Rb[k], o)
                y, z = C[1][k] * 1000 + d[1], C[2][k] * 1000 + d[2]
                g = n[0] * (y - hy) + n[1] * (z - hz)
                if g < gap[k]:
                    gap[k], gap_who[k] = g, oid
    kmin = min(range(nT), key=gap.__getitem__)
    obj["plots"]["occupant_floor_gap"] = {"unit": "mm", "t": common.pack_f32(t), "v": common.pack_f32(gap)}
    k_end = nT - 1
    obj["rollover"] = {
        "window": list(ROLL_WINDOW_S), "hinge_mm": fl["hinge_mm"], "hold_deg": fl["hold_deg"], "floor": fl["world"],
        "contacts": contacts, "peak": peak, "occupant_gap": common.pack_f32(gap), "occupant_gap_min": gap[kmin],
        "occupant_gap_min_t": t[kmin], "occupant_gap_min_box": gap_who[kmin], "occupant_boxes": occ,
        "tilt_world": common.pack_f32(tilt_world), "roll_world": common.pack_f32(roll_world),
        "times": {q: common.result(rec, q) for q in ("wheels_off_time", "two_wheel_lift_time", "tip_time",
                                                      "rollbar_first_contact_time")},
        "tip_deg": tip, "end": {"t": t[k_end], "roll_world": roll_world[k_end], "tilt_world": tilt_world[k_end],
                                "roll_table": roll[k_end]},
        "k2_people_touch": common.result(rec, "occupant_box_ground_contact"),
    }
    obj["judge"] = None   # the S1 criterion (tilt_angle) is not this tool's result; the header says what happens
    obj["inputs"].insert(0, {"label": "計算の出どころ", "value": f"K2 の横転の計算 (2026-11-06)。{rec['load_case_id'].split('-')[0]} を v{rec['design_version']} で、台を倒れる角度 {tip:.1f}° より {hold - tip:.0f}° 先の {hold:.1f}° まで回して止めた",
                             "source": "HIRAKU の run の外の K2 の計算 (記録のファイル)"})
    obj["notes"] = [
        f"この試験は HIRAKU の run の解析ではなく、K2 の横転の計算 (2026-11-06) の記録: S1 (傾斜台、2 人 + 荷) を設計 v{rec['design_version']} (台本どおりのデモの run の v{int(rec['design_version']):03d} の設計) で、台を倒れる角度 {tip:.2f}° より {hold - tip:.0f}° 先の {hold:.2f}° まで回して止め、倒れて止まるまで。",
        "記録の位置と姿勢は傾斜台の座標 (記録のロールは台に対して)。画面は台の座標ごと台の角度 (table_angle) だけ、台の低い縁のちょうつがいのまわりに回す。床は記録の scene の床 (台を止めた角度で台の座標で書かれたもの) を、同じ回し方で世界に戻して描いた。",
        f"ちょうつがい: 台の座標で y = {fl['hinge_mm'][0]:.0f} mm、z = {fl['hinge_mm'][1]:.0f} mm (床の縁のうち台の低い縁にいちばん近い方、計算で出した)。",
        "矢印: 橙 = 接触点の力、紫 = 記録の最大の瞬間の力、緑 = 輪の垂直力、青 = 輪の摩擦力。光る点 = 力を持つ接触点。動きは ×1 (実寸)。",
        "接触点の矢印: 記録の ground_force (点の力、台の座標) を ground_point から。矢印の長さは輪の矢印の 1/10 (kN で表示)。点が力を持つ時刻 (ground_touch) だけ点を光らせる。",
        f"時系列は 40 ms おき (動き始めの前はもっと粗い)。接触力の最大 {peak['N'] / 1000:.1f} kN (記録の結果、{peak['t']} s) はその間の瞬間で、時系列の最大は {max(c['series_peak_N'] for c in contacts) / 1000:.1f} kN。最大の瞬間の力 (記録の vectors) は、その瞬間をはさむ 2 つの時刻にだけ紫の矢印で重ねる (車体の姿勢はその時刻のもの)。",
        "乗員の箱と床の距離: 設計の乗員の箱 (乗せた 2 人の体の箱と全体の箱) の 8 つの角を、記録の箱の中心と車体の向きで置き、床の面までのいちばん近い距離 (負なら床の中)。",
        f"記録の前提 (閉じた枠) にあるとおり、接触はばね 1000 N/mm とダンパ (HIRAKU の選んだ値、出典なし): 最大の力はこの硬さで変わる (2 倍の硬さで {common.result(rec, 'body_contact_force_peak_k2x') / 1000:.1f} kN)。",
    ] + obj["notes"]
    return obj


def build(args) -> dict:
    cases = build_cases(args, TOOLS, "handling") if args.run else []
    for path in getattr(args, "rollover", None) or []:
        cases.append(rollover_case(path, args.rollover_design))
    return {**cases[0], "cases": cases}


def extra(ap):
    ap.add_argument("--also", nargs="*", default=[], help="more run folders, one case each")
    ap.add_argument("--rollover", nargs="*", default=[], help="K2 rollover_tilt record files (JSON), one case each")
    ap.add_argument("--rollover-design", type=Path, help="the run folder (or version folder) of the rollover record's design")


if __name__ == "__main__":
    common.main("handling", build, extra=extra, argv=sys.argv[1:])

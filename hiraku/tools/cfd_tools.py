"""The flow tools (cfd_aero, cfd_water): a thin adapter between the MCP tools and hiraku.cfd.

The only module outside hiraku/cfd that imports it. hiraku/tools/mcp.py imports this module in one line, and on
import it adds its tools to mcp's tables. The names are in RUN_TOOLS and in the aero CAE role's tools (roles.AERO_TOOLS);
removing those, that line, this file and hiraku/cfd leaves the rest working.

A sweep runs on the GPU only: with no GPU the tool says so and does not run; it never falls back to the CPU.
Its record goes to <run folder>/cfd/v<N>/<speed>_<grid>/ (each case in its own folder). A replay reads a stored
record and touches no GPU. The hand defaults come from the design and the run's load cases here, as plain
numbers, so hiraku.cfd stays free of the catalog and the solvers.

cfd_water is a pass through a flooded road, on the GPU in the same way. By default the vehicle answers the water through K2
(hiraku.cfd.water_coupled gets K2 from here, k2_parts).

Called with judge, each goes the run's own way to a verdict: the load case (F4, F5), the hand calculation, analyze, compare. The solvers
do not import hiraku.cfd and a run does not start with these tests, so the analysis tool joins the run's shelf at that call (_FlowTool).
Every value stays marked as not compared with an experiment (physically_validated: false).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from hiraku import contract
from hiraku.catalog.build import CatalogError
from hiraku.cfd import aero_map, cuda, record, solver, uncertainty, vehicle, water, water_coupled, water_record
from hiraku.contract.loading import loading_of
from hiraku.contract.protocols import DEFAULT_LOADING, ToolError
from hiraku.store import StoreError

Record = dict[str, Any]

NAME = "cfd_aero"
CFD_DIR = "cfd"
GPU_WAIT_S = 600.0  # how long a call waits for another flow run to finish before it says to come back
MIN_DX_MM, MAX_DX_MM = 20.0, 320.0
WAIT = "今は回していません。ほかの計算が終わるのを少し待ってから、もう一度呼んでください"
NO_GPU = "GPU が無いので流れの計算は回しません ({reason})。CPU では回しません。保存した記録があれば replay で読めます"

INPUT_SCHEMA: Record = {
    "description": "Agent-facing MCP tool (hiraku/tools/cfd_tools.py). 設計の版の空気の流れを GPU で解き、ヨーごとの力と係数の表を実行のフォルダに書く。GPU が無ければ回さない。",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "design_version": {"type": "integer", "minimum": 1, "description": "Missing: the latest version."},
        "yaws_deg": {"type": "array", "items": {"type": "number", "minimum": -aero_map.REST_YAW_DEG, "maximum": aero_map.REST_YAW_DEG},
                     "minItems": 1, "maxItems": 21,
                     "description": "Yaw angles; + is wind from the left. A moving vehicle takes up to +-85, at rest up to +-90. Missing: 0, +-5, +-10, +-15, +-20, and F1's wind on both sides: at rest at yaw 90, and at the load case's speed."},
        "speed_kmh": {"anyOf": [{"type": "number", "minimum": 0},
                                {"type": "array", "items": {"type": "number", "minimum": 0}, "minItems": 1, "maxItems": 4}],
                      "description": "Vehicle speed, or several to compute the speed effect. 0: at rest in the wind wind_ms. Missing: the design variable max_speed."},
        "wind_ms": {"type": "number", "exclusiveMinimum": 0, "description": "The wind on a vehicle at rest. Missing: the F1 load case's wind speed."},
        "dx_mm": {"type": "number", "minimum": MIN_DX_MM, "maximum": MAX_DX_MM, "description": "Grid size. Missing: 80."},
        "judge": {"type": "boolean", "description": "true: go to a verdict on test F4 (the wind speed at which the upwind wheels lift, at rest in a wind from each side, against F1's limit): the load case, the hand calculation, the analysis and the comparison. yaws_deg and speed_kmh do not apply. Missing: false."},
        "replay": {"anyOf": [{"type": "boolean"}, {"type": "string", "minLength": 1, "pattern": "\\.json$"}],
                   "description": "Read a stored record instead of running: its path relative to the run's folder, or true for the newest of the version. Needs no GPU."},
    },
}

DESCRIPTION = (
    "設計の版のまわりの空気の流れを GPU で解き、ヨーごとの力 (Fx Fy Fz、N) とモーメント (Mx My Mz、N*m。中心は軸距の中央・輪距の中央の地面の上) "
    "と Cd・CdA・Cy・Cl の表を実行のフォルダの cfd/ に書く。yaws_deg を付けなければ、ヨー 20° までと、F1 の風を止まった車に真横から当てた点、"
    "同じ風を走っている車に当てた点を回す。横風の試験 F1 に渡す形 (C_y・side_area・side_cp_height と、読んだヨー) と、"
    "基準が読む値 (F1 の転倒の風速、F2 の抗力、P4 の電力量と航続) を設計の既定値・流れの値・流れの値の幅で並べ、OK / NG が動くかも返す。"
    "judge を true にすると、試験 F4 (風上の輪が浮く風速が F1 の限度以上か) の荷重条件を書き、手計算の予測、解析、比較まで進めて判定を返す。"
    "値は実験と比べて確かめていない (physically_validated: false)。GPU が無いときは回さず、そう答える。GPU のメモリが足りないときは待つように答える。"
    "replay を付けると保存した記録を GPU なしで読む (true か、実行のフォルダからの path)"
)


def _run_dir(ctx: Any) -> Path:
    path = getattr(getattr(ctx, "store", None), "path", None)
    if path is None:
        raise ToolError("実行のフォルダがありません。実行を始めてから呼んでください")
    return Path(path).resolve()


def _version(ctx: Any, args: Record) -> int:
    v = args.get("design_version") or ctx.store.design_version
    if not v:
        raise ToolError("設計の版がまだありません。版ができてから呼んでください")
    return int(v)


def _replay_path(ctx: Any, args: Record, record_file: str = record.RECORD_FILE) -> Path:
    run = _run_dir(ctx)
    root = (run / CFD_DIR).resolve()
    replay = args["replay"]
    if replay is True:
        v = _version(ctx, args)
        found = sorted(root.glob(f"v{v}/*/{record_file}"), key=lambda p: p.stat().st_mtime_ns)
        if not found:
            raise ToolError(f"v{v} の流れの記録はまだありません。replay を外して回してください")
        return found[-1]
    if not isinstance(replay, str):
        raise ToolError("replay は true か、実行のフォルダからの記録の path です")
    p = (run / replay).resolve()
    if not p.is_relative_to(root) or p.suffix.lower() != ".json":
        raise ToolError(f"replay は実行のフォルダの {CFD_DIR}/ の中の .json だけです ({replay!r})")
    if not p.is_file():
        raise ToolError(f"{replay!r} は実行のフォルダにありません")
    return p


def _replay(ctx: Any, args: Record) -> Record:
    p = _replay_path(ctx, args)
    try:
        rec = record.load_record(p)
    except ValueError as e:
        raise ToolError(f"流れの記録を読めませんでした: {e}") from None
    if args.get("design_version") and rec["design_version"] != args["design_version"]:
        raise ToolError(f"この記録は v{rec['design_version']} のものです (頼まれたのは v{args['design_version']})")
    return record.answer(rec, p.relative_to(_run_dir(ctx)).as_posix(), replayed=True)


def _speeds_ms(args: Record, design: Record) -> list[float]:
    given = args.get("speed_kmh")
    if given is not None:
        speeds = [given] if isinstance(given, (int, float)) else list(given)
        if not speeds or not all(isinstance(s, (int, float)) and s >= 0 for s in speeds):
            raise ToolError("speed_kmh は 0 以上の数か、その並びです (0 は止まった車)")
        return [float(s) / 3.6 for s in dict.fromkeys(speeds)]
    try:
        v = aero_map.speed_ms_of(design)
    except ValueError as e:
        raise ToolError(str(e)) from None
    if v is None:
        raise ToolError("設計の変数に max_speed が無いので speed_kmh を渡してください")
    return [v]


def _case_of(store: Any, version: int, test_id: str) -> Record | None:
    return next((c for c in store.version_load_cases(version) if c.get("test_id") == test_id and not c["load_case_id"].endswith("-beyond")), None)


def _cases(ctx: Any, args: Record, design: Record, version: int) -> list[aero_map.Case]:
    """The cases of the call. Without yaws_deg the default sweep and F1's own wind (when the run has an F1 load case or
    wind_ms says it): where K2 reads its number and where the moving vehicle meets that wind."""
    from hiraku.solvers.k0_handcalc.units import to_unit

    speeds = _speeds_ms(args, design)
    f1 = _case_of(ctx.store, version, "F1")
    params = (f1 or {}).get("params", {})
    yaws = args.get("yaws_deg")
    try:
        wind = args.get("wind_ms") or (to_unit(params["wind_speed"], "m/s", "wind_speed") if "wind_speed" in params else None)
        if yaws is not None:
            return aero_map.plan_cases(design, yaws, speeds, wind)
        moving = [v for v in speeds if v > 0.0]
        if not moving:
            return aero_map.plan_cases(design, (aero_map.REST_YAW_DEG, -aero_map.REST_YAW_DEG), speeds, wind)
        along = to_unit(params["speed"], "m/s", "speed") if "speed" in params else moving[0]
        return aero_map.plan_cases(design, None, moving, None, aero_map.f1_cases(wind, along) if wind else ())
    except (TypeError, ValueError) as e:
        raise ToolError(f"yaws_deg か wind_ms を使えません: {e}") from None


def _hand_inputs(store: Any, design: Record, version: int) -> tuple[Record, str, str | None, list[str]]:
    """The hand defaults as plain numbers, the loading and the F1 wind side. A block that cannot be worked out stays
    out with its reason: the flow values still stand."""
    from hiraku.catalog.builders.analysis import p4_calc
    from hiraku.catalog.builders.common import design_var
    from hiraku.catalog.builders.k2_tests import wind_inputs
    from hiraku.catalog.summary import design_summary
    from hiraku.solvers.k0_handcalc.units import to_unit

    def criterion(case: Record, unit: str) -> Record | None:
        c = case.get("criterion")
        return c and {"quantity": c["quantity"], "op": c["op"], "limit": to_unit(c["limit"], unit, "limit"), "unit": unit, "load_case_id": case["load_case_id"]}

    f1, p4 = _case_of(store, version, "F1"), _case_of(store, version, "P4")
    loading = loading_of(f1) if f1 else loading_of(p4) if p4 else DEFAULT_LOADING
    wind_from = (f1 or {}).get("params", {}).get("wind_from")
    hand: Record = {}
    notes: list[str] = []
    try:
        s = design_summary(design)
        w = wind_inputs(design, {"params": {"loading": loading}})
        hand["f1"] = {"m_kg": to_unit(s["loadings"][loading]["mass"], "kg", "m"), "t_mm": to_unit(s["track_front"], "mm", "t"),
                      "rho": to_unit(w["rho"], "kg/m^3", "rho"), "C_y": to_unit(w["C_y"], "1", "C_y"), "A_m2": to_unit(w["A"], "m^2", "A"),
                      "h_cp_mm": to_unit(w["h_cp"], "mm", "h_cp"),
                      "speed_ms": to_unit(f1["params"]["speed"], "m/s", "speed") if f1 and "speed" in f1["params"] else None,
                      "criterion": criterion(f1, "m/s") if f1 else None}
    except (CatalogError, KeyError, TypeError, ValueError) as e:
        notes.append(f"F1 の手計算の既定値を出せませんでした: {e}")
    try:
        hand["f2"] = {"rho": design_var(design, "air_density", "kg/m^3"), "C_d": design_var(design, "drag_coefficient", "1"),
                      "A_m2": design_var(design, "frontal_area", "m^2")}
    except ValueError as e:
        hand["f2"] = {}
        notes.append(f"F2 の手計算の既定値を出せませんでした: {e}")
    f2 = _case_of(store, version, "F2")
    if f2:
        if f2.get("criterion"):
            hand["f2"]["criterion"] = criterion(f2, "N")
        if "speed" in f2.get("params", {}):
            hand["f2"]["speed_ms"] = to_unit(f2["params"]["speed"], "m/s", "speed")
    if p4:
        try:
            p, calc = p4["params"], p4_calc(design, p4)
            block = {"m_kg": to_unit(design_summary(design)["loadings"][loading_of(p4)]["mass"], "kg", "m"), "d_km": to_unit(p["distance"], "km", "distance"),
                     "v_kmh": to_unit(calc["loop_rms_speed"], "km/h", "v"), "H_m": to_unit(calc["loop_climb"], "m", "H"),
                     "eta": to_unit(calc["drive_efficiency"], "1", "eta"), "E_batt_kWh": to_unit(calc["battery_energy"], "kWh", "E_batt"),
                     "C_rr": design_var(design, "rolling_resistance", "1"), "eta_regen": design_var(design, "regen_efficiency", "1"),
                     "rho": design_var(design, "air_density", "kg/m^3"), "soc_start_pct": to_unit(p["soc_start"], "%", "soc_start")}
            missing = [k for k, v in block.items() if v is None]
            if missing:
                raise ValueError(f"設計の変数が足りません ({', '.join(missing)})")
            hand["p4"] = {**block, "C_d": hand["f2"].get("C_d"), "A_m2": hand["f2"].get("A_m2"), "criterion": criterion(p4, "%")}
        except (CatalogError, KeyError, TypeError, ValueError) as e:
            notes.append(f"P4 の手計算の既定値を出せませんでした: {e}")
    return hand, loading, wind_from, notes


def _new_folder(run: Path, version: int, speed_ms: float, dx_mm: float) -> Path:
    base = run / CFD_DIR / f"v{version}" / f"{speed_ms * 3.6:g}kmh_dx{dx_mm:g}"
    folder, n = base, 1
    while folder.exists():  # never over an earlier sweep
        n += 1
        folder = base.with_name(f"{base.name}-{n}")
    return folder


def _sweep(ctx: Any, args: Record) -> Record:
    run = _run_dir(ctx)
    version = _version(ctx, args)
    try:
        design = ctx.store.design(version)
    except StoreError:
        raise ToolError(f"設計の版 v{version} はありません (今は v{ctx.store.design_version} まで)") from None
    cases = _cases(ctx, args, design, version)
    dx_mm = float(args.get("dx_mm") or aero_map.DEFAULT_DX_M * 1000.0)
    if not MIN_DX_MM <= dx_mm <= MAX_DX_MM:
        raise ToolError(f"dx_mm は {MIN_DX_MM:g} から {MAX_DX_MM:g} までです ({dx_mm:g})")
    try:
        gpu, reason = cuda.gpu_or_reason()
    except cuda.CudaError as e:  # the GPU is there but refuses now
        raise ToolError(f"GPU を使えませんでした ({e})。{WAIT}") from None
    if gpu is None:
        raise ToolError(NO_GPU.format(reason=reason))
    gpu.close()
    hand, loading, wind_from, notes = _hand_inputs(ctx.store, design, version)
    try:
        validation = uncertainty.validation_error_pct()
    except (OSError, ValueError) as e:
        raise ToolError(f"実験と比べた誤差のファイルを読めません ({e})") from None
    folder = _new_folder(run, version, cases[0][0], dx_mm)
    t0 = time.perf_counter()
    try:
        with cuda.gpu_turn(GPU_WAIT_S), cuda.Gpu() as live:
            solver.check_memory(solver.required_bytes(aero_map.largest_grid(cases, dx_mm / 1000.0), None, fields=True))
            aero = aero_map.run_sweep(live, design, cases=cases, dx=dx_mm / 1000.0, folder=folder, loading=loading, wind_from=wind_from)
    except TimeoutError:
        raise ToolError(f"GPU の順番が回ってきませんでした。{WAIT}") from None
    except solver.MemoryBudgetError as e:
        raise ToolError(f"{e}。{WAIT}") from None
    except ValueError as e:  # a design the shapes cannot be read from, or a loading it does not fit
        raise ToolError(f"この設計では流れの計算を回せません: {e}") from None
    except cuda.GpuUnavailable as e:
        raise ToolError(NO_GPU.format(reason=e)) from None
    except cuda.CudaError as e:  # e.g. the memory went to another program after the check
        raise ToolError(f"GPU の呼び出しが失敗しました ({e})。{WAIT}") from None
    except RuntimeError as e:  # the flow itself went bad: a non-finite value at some step
        raise ToolError(f"流れの計算が途中で壊れました ({e})。格子を変えて回し直してください") from None
    table = uncertainty.table(aero, hand, validation)
    if notes:
        table["hand_notes"] = notes
    rec = record.make_record(aero, table, design_version=version,
                             requested={"cases": [{"speed_kmh": c[0] * 3.6, "yaw_deg": c[1], "wind_ms": c[2]} for c in cases], "dx_mm": dx_mm, "loading": loading})
    rec["wall_time_s"] = time.perf_counter() - t0
    path = record.save_record(rec, folder)
    return record.answer(rec, path.relative_to(run).as_posix(), replayed=False)


def cfd_aero(ctx: Any, args: Record | None = None, agent: str | None = None) -> Record:
    """Run a yaw sweep on the GPU into the run's folder, read a stored record, or with judge go to a verdict on test F4."""
    args = dict(args or {})
    if args.get("replay"):
        return _replay(ctx, args)
    return _judge_aero(ctx, args, agent) if args.get("judge") else _sweep(ctx, args)


def k2_parts() -> Any:
    """What hiraku.cfd.water_coupled uses from K2. It comes through here because hiraku.cfd itself does not import K2."""
    from types import SimpleNamespace

    from hiraku.solvers.k2_vehicle import drive, model, scenario, sim

    return SimpleNamespace(Motor=drive.Motor, PathDriver=drive.PathDriver, SpeedController=drive.SpeedController, controls=drive.controls, on_board=drive.on_board, start_rolling=drive.start_rolling,
                           build_vehicle=model.build_vehicle, settle=scenario.settle, hold=scenario.hold, Sim=sim.Sim, quat_from_euler=sim.quat_from_euler)


WATER_NAME = "cfd_water"
WATER_DEFAULTS = {"depth_mm": 200.0, "speed_kmh": 18.0, "dx_mm": 30.0, "frame_hz": 30.0}
WATER_TIME_LIMIT_S = 6.0  # a vehicle the water slows to a stop ends the pass here
WATER_RANGE = {"depth_mm": (50.0, 350.0), "speed_kmh": (5.0, 36.0), "dx_mm": (25.0, 60.0), "frame_hz": (10.0, 90.0)}

WATER_INPUT_SCHEMA: Record = {
    "description": "Agent-facing MCP tool (hiraku/tools/cfd_tools.py). 設計の版が冠水路を通るときの水を GPU で解き、実行のフォルダに記録を書く。judge で試験 F5 (電池、モーター、制御器に水が届かない) の判定まで進む。実験と比べていない計算による。GPU が無ければ回さない。",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "design_version": {"type": "integer", "minimum": 1, "description": "Missing: the latest version."},
        "depth_mm": {"type": "number", "minimum": WATER_RANGE["depth_mm"][0], "maximum": WATER_RANGE["depth_mm"][1], "description": "The depth asked of the dip; the settled water line is a little lower (the record has it). Missing: 200."},
        "speed_kmh": {"type": "number", "minimum": WATER_RANGE["speed_kmh"][0], "maximum": WATER_RANGE["speed_kmh"][1], "description": "The vehicle's speed; with vehicle_moves the speed the driver aims for. Missing: 18."},
        "dx_mm": {"type": "number", "minimum": WATER_RANGE["dx_mm"][0], "maximum": WATER_RANGE["dx_mm"][1], "description": "Grid size. Missing: 30."},
        "vehicle_moves": {"type": "boolean", "description": "true: the vehicle answers the water through K2 (slows, pitches, rises). false: it keeps a fixed path at the speed. Missing: true."},
        "frame_hz": {"type": "number", "minimum": WATER_RANGE["frame_hz"][0], "maximum": WATER_RANGE["frame_hz"][1], "description": "Saved frames per second. Missing: 30."},
        "keep_particles": {"type": "boolean", "description": "Keep every frame's particles in the run's folder, for pictures made afterwards (about 8 MB a frame at 30 mm). Missing: false."},
        "judge": {"type": "boolean", "description": "true: go to a verdict on test F5 (water reaches none of the battery, the motor and the controller): the load case, the hand calculation, the analysis and the comparison. Missing: false."},
        "replay": {"anyOf": [{"type": "boolean"}, {"type": "string", "minLength": 1, "pattern": "\\.json$"}],
                   "description": "Read a stored record instead of running: its path relative to the run's folder, or true for the newest of the version. Needs no GPU."},
    },
}

WATER_DESCRIPTION = (
    "設計の版が冠水路 (水の入った窪み、両端は斜面) を通るときの水を GPU で解き (MLS-MPM)、実行のフォルダの cfd/ に記録を書く。"
    "judge を true にすると、試験 F5 (電池、モーター、制御器のどれにも水が届かない) の荷重条件を書き、手計算の予測、解析、比較まで進めて判定を返す。"
    "この判定は水が届くかだけを見る。値は実験と比べて確かめていない (physically_validated: false)。"
    "返すのは、部品ごとのすき間と水が届いた時刻、水の高さ、床下の水の深さ、水の運動量の収支。vehicle_moves が true (既定) なら K2 の車両と水を双方向につなぎ、速さの落ち方、路面に対するピッチと高さ、前輪が路面を離れた時間も返す。"
    "1 回に数分。GPU が無いときは回さず、そう答える。GPU のメモリが足りないときは待つように答える。"
    "replay を付けると保存した記録を GPU なしで読む (true か、実行のフォルダからの path)"
)


def _water_replay(ctx: Any, args: Record) -> Record:
    p = _replay_path(ctx, args, water_record.RECORD_FILE)
    try:
        rec = water_record.load_record(p)
    except ValueError as e:
        raise ToolError(f"冠水路の記録を読めませんでした: {e}") from None
    if args.get("design_version") and rec["design_version"] != args["design_version"]:
        raise ToolError(f"この記録は v{rec['design_version']} のものです (頼まれたのは v{args['design_version']})")
    return water_record.answer(rec, p.relative_to(_run_dir(ctx)).as_posix(), replayed=True)


def _water_folder(run: Path, version: int, depth_mm: float, speed_kmh: float, dx_mm: float, moves: bool) -> Path:
    base = run / CFD_DIR / f"v{version}" / f"water_{depth_mm:g}mm_{speed_kmh:g}kmh_dx{dx_mm:g}{'' if moves else '_fixed'}"
    folder, n = base, 1
    while folder.exists():  # never over an earlier pass
        n += 1
        folder = base.with_name(f"{base.name}-{n}")
    return folder


def _say_no_gpu(kernels: str = "kernels") -> None:
    """A ToolError with the reason when this machine cannot run the flow."""
    try:
        gpu, reason = cuda.gpu_or_reason(kernels)
    except cuda.CudaError as e:
        raise ToolError(f"GPU を使えませんでした ({e})。{WAIT}") from None
    if gpu is None:
        raise ToolError(NO_GPU.format(reason=reason))
    gpu.close()


def _water_pass(ctx: Any, args: Record) -> Record:
    run = _run_dir(ctx)
    version = _version(ctx, args)
    try:
        design = ctx.store.design(version)
    except StoreError:
        raise ToolError(f"設計の版 v{version} はありません (今は v{ctx.store.design_version} まで)") from None
    asked = {k: float(args.get(k) or d) for k, d in WATER_DEFAULTS.items()}
    for k, (lo, hi) in WATER_RANGE.items():
        if not lo <= asked[k] <= hi:
            raise ToolError(f"{k} は {lo:g} から {hi:g} までです ({asked[k]:g})")
    moves, keep = bool(args.get("vehicle_moves", True)), bool(args.get("keep_particles", False))
    _say_no_gpu("water")
    p4 = _case_of(ctx.store, version, "P4")
    loading = loading_of(p4) if p4 else DEFAULT_LOADING
    folder = _water_folder(run, version, asked["depth_mm"], asked["speed_kmh"], asked["dx_mm"], moves)
    depth, speed, dx, frame_dt = asked["depth_mm"] / 1000.0, asked["speed_kmh"] / 3.6, asked["dx_mm"] / 1000.0, 1.0 / asked["frame_hz"]
    t0 = time.perf_counter()
    try:
        with cuda.gpu_turn(GPU_WAIT_S), cuda.Gpu("water") as live:
            if moves:
                water_coupled.run_coupled(live, k2_parts(), design, water_coupled.LIFT_RATIO, depth_m=depth, speed_ms=speed, dx=dx, out=folder, loading=loading, frame_dt=frame_dt, t_max_s=WATER_TIME_LIMIT_S)
            else:
                water.run_flooded_road(live, design, depth_m=depth, speed_ms=speed, dx=dx, out=folder, loading=loading, analyse=False, frame_dt=frame_dt)
    except TimeoutError:
        raise ToolError(f"GPU の順番が回ってきませんでした。{WAIT}") from None
    except solver.MemoryBudgetError as e:
        raise ToolError(f"{e}。{WAIT}") from None
    except (ValueError, KeyError) as e:  # a design the shapes or K2 cannot read (no four wheels, no motor), or a loading it does not fit
        hint = "。vehicle_moves を false にすると、車を動かさずに回せます" if moves else ""
        raise ToolError(f"この設計では冠水路の計算を回せません: {e}{hint}") from None
    except cuda.GpuUnavailable as e:
        raise ToolError(NO_GPU.format(reason=e)) from None
    except cuda.CudaError as e:
        raise ToolError(f"GPU の呼び出しが失敗しました ({e})。{WAIT}") from None
    case = water.analyse_case(folder, design, keep_particles=keep)  # on the CPU, after the GPU's turn
    rec = water_record.make_record(case, design_version=version, requested={**asked, "vehicle_moves": moves, "keep_particles": keep, "loading": loading}, kept_particles=keep, wall_time_s=time.perf_counter() - t0)
    path = water_record.save_record(rec, folder)
    return water_record.answer(rec, path.relative_to(run).as_posix(), replayed=False)


def cfd_water(ctx: Any, args: Record | None = None, agent: str | None = None) -> Record:
    """A pass through the flooded road on the GPU into the run's folder, read a stored record, or with judge go to a verdict on test F5."""
    args = dict(args or {})
    if args.get("replay"):
        return _water_replay(ctx, args)
    return _judge_water(ctx, args, agent) if args.get("judge") else _water_pass(ctx, args)


# ---------------------------------------------------------------- the way to a verdict (tests F4 and F5)

FLOW_STAGE = 2  # HK5: after the light tools, as K2's time simulations; minutes on a GPU, not hours
NOT_COMPARED = "実験と比べていない計算の値 (physically_validated: false)。確かめていない"
ASSUMED_FLOOD = "水深 200 mm と 18 km/h は HIRAKU の仮定で、要件から来ていない"


class _FlowTool:
    """A flow calculation as analyze takes it, on the run's shelf from the call that needs it. check() is the known-answer cases the
    solver already holds: run again here they would take minutes each time a tool is listed."""

    def __init__(self, name: str, title: str, test_id: str, formula_id: str, checks: list[Record], run: Any):
        self.name, self.title, self.test_id, self.formula_id, self.checks, self._run = name, title, test_id, formula_id, checks, run
        self.tests = [test_id]

    def spec(self) -> Record:
        return {"name": self.name, "title": self.title, "domain": "aero", "base": record.BASE, "tests": [self.test_id], "origin": "builtin", "usable": all(c["pass"] for c in self.checks),
                "checks": self.checks, "formulas": [self.formula_id], "stage": FLOW_STAGE}

    def check(self) -> list[Record]:
        return self.checks

    def run(self, design: Record, load_case: Record, predictions: list[Record], params: Record) -> Record:
        return self._run(design, load_case)


def _on_shelf(ctx: Any, tool: _FlowTool, agent: str) -> None:
    spec = tool.spec()
    contract.validate("tool_spec", spec)
    if not spec["usable"]:
        raise ToolError(f"{tool.name} の確かめが通っていません: {[c['case'] for c in spec['checks'] if not c['pass']]}")
    first = tool.name not in ctx.specs
    ctx.tools[tool.name], ctx.specs[tool.name] = tool, spec
    if first:
        ctx.store.emit("tool.checked", agent=agent, domain="aero", text=f"ツール {tool.name} を棚に置きました (GPU の流れの計算)", data=spec)


def _flow_load_case(ctx: Any, test_id: str, write: Record, agent: str) -> Record:
    """The version's load case of the test: the one written earlier, else written now by the run's write_load_case."""
    from hiraku.tools.load_case_writer import write_load_case

    for lc in ctx.load_cases:
        if lc["test_id"] == test_id:
            return lc
    return ctx.load_case(write_load_case(ctx, {"test_id": test_id, "domain": "aero", **write}, agent)["load_case_id"])


def _to_verdict(ctx: Any, tool: _FlowTool, lc: Record, prediction: Record, agent: str, statement: str | None = None) -> Record:
    """The prediction, analyze and compare, as a role does them by hand, each through its own checks (HK1 to HK6)."""
    from hiraku.tools import functions

    _on_shelf(ctx, tool, agent)
    pred = functions.record_prediction(ctx, {"load_case_id": lc["load_case_id"], "formula_id": tool.formula_id, **prediction}, agent)
    analysis = functions.analyze(ctx, {"tool": tool.name, "load_case_id": lc["load_case_id"], "prediction_ids": [pred["prediction_id"]]}, agent)
    out: Record = {"load_case_id": lc["load_case_id"], "criterion": lc["criterion"], "prediction": {"value": pred["value"], "unit": pred["unit"], "formula_id": pred["formula_id"]},
                   "analysis_id": analysis["analysis_id"], "physically_validated": False, "not_compared": NOT_COMPARED, "results": analysis["results"]}
    if statement:
        out["statement"] = statement
    if analysis["status"] != "ok":
        raise ToolError(f"解析 {analysis['analysis_id']} は失敗しました: {analysis.get('error', '')}")
    compared = functions.compare(ctx, {"prediction_id": pred["prediction_id"], "analysis_id": analysis["analysis_id"]}, agent)
    out["comparison"] = {k: compared["comparison"][k] for k in ("comparison_id", "quantity", "predicted", "analyzed", "diff_rel", "status", "tolerance_rel")}
    out["verdict"] = compared["verdict"]
    if compared["verdict"] is None:
        out["next"] = (f"手計算と流れの計算が許容の外です (不一致)。判定はまだ出ていません。record_cause に {compared['comparison']['comparison_id']} の原因と直し方を書いてください。"
                       "手計算が不利な側にあるなら safe_side と safe_side_reason を付けると、手計算の値で判定します")
    return out


def _sharp_body_check() -> Record:
    """The known answers the wind tunnel holds: bodies with sharp edges against the published Cd (hiraku/cfd/data/validation.json)."""
    band = uncertainty.validation_by_class()["sharp_edges"]
    return {"case": "cube and square plate at Re 1e5, finest grid", "quantity": "Cd_error_pct", "expected": 0.0, "got": float(band), "unit": "%", "tolerance_rel": 0.0, "tolerance_abs": 10.0,
            "pass": abs(float(band)) <= 10.0, "source": "hiraku/cfd/data/validation.json (values redrawn from Hoerner by another source; the original not read)"}


def _judge_aero(ctx: Any, args: Record, agent: str | None) -> Record:
    """Test F4: the wind speed at which the upwind wheels lift, with the forces of the flow on the vehicle at rest."""
    agent = agent or "lead"
    run = _run_dir(ctx)
    version = _version(ctx, args)
    design = ctx.store.design(version)
    _say_no_gpu()
    hand, loading, _, notes = _hand_inputs(ctx.store, design, version)
    f1 = hand.get("f1")
    if not f1:
        raise ToolError(f"この設計では F1 の手計算の入力を作れません: {'; '.join(notes)}")
    dx_mm = float(args.get("dx_mm") or aero_map.DEFAULT_DX_M * 1000.0)
    if not MIN_DX_MM <= dx_mm <= MAX_DX_MM:
        raise ToolError(f"dx_mm は {MIN_DX_MM:g} から {MAX_DX_MM:g} までです ({dx_mm:g})")
    lc = _flow_load_case(ctx, "F4", {"title": "流れの計算で、止まった車の風上の輪が横風で浮かない", "params": {"loading": loading, "speed": {"value": 0.0, "unit": "km/h"}, "yaw": {"value": 90.0, "unit": "deg"}},
                                     "requirement_keys": ["Q18"], "criterion": {"quantity": "critical_wind_speed", "op": ">=", "limit_from": {"expr": "Q18", "unit": "m/s", "refs": {"Q18": "Q18"}}},
                                     "basis": "F1 と同じ限度 (Q18) を、F1 の K2 と同じ釣り合いで、力だけ GPU の流れの計算で求める。実験と比べていない"}, agent)
    wind = float(lc["criterion"]["limit"]["value"])

    def analysis(design: Record, load_case: Record) -> Record:
        t0 = time.perf_counter()
        cases = [(0.0, aero_map.REST_YAW_DEG, wind), (0.0, -aero_map.REST_YAW_DEG, wind)]
        folder = _new_folder(run, version, 0.0, dx_mm)
        with cuda.gpu_turn(GPU_WAIT_S), cuda.Gpu() as live:
            solver.check_memory(solver.required_bytes(aero_map.largest_grid(cases, dx_mm / 1000.0), None, fields=True))
            aero = aero_map.run_sweep(live, design, cases=cases, dx=dx_mm / 1000.0, folder=folder, loading=loading)
        rec = record.make_record(aero, uncertainty.table(aero, hand, uncertainty.validation_error_pct()), design_version=version, requested={"judge": "F4", "dx_mm": dx_mm, "wind_ms": wind, "loading": loading})
        record.save_record(rec, folder)
        results = []
        for form in aero["crosswind"]:
            turning = form["CyA_m2"] * form["side_cp_height_mm"] + form["lift_area_m2"] * f1["t_mm"] / 2.0  # Mx + Fz t / 2 = m g t / 2: the flow's lift is in, the hand formula has none
            where = f"wind from the {form['wind_from']}"
            results += [{"quantity": "critical_wind_speed", "value": uncertainty.critical_wind_speed(f1["m_kg"], f1["t_mm"], turning, f1["rho"]), "unit": "m/s", "where": where},
                        {"quantity": "side_force_area", "value": float(form["CyA_m2"]), "unit": "m^2", "where": where}, {"quantity": "side_cp_height", "value": float(form["side_cp_height_mm"]), "unit": "mm", "where": where}]
        return {"status": "ok", "base": record.BASE, "results": results, "physically_validated": False, "assumptions": [*aero["assumptions"], NOT_COMPARED], "wall_time_s": time.perf_counter() - t0, "timings_s": {"solve": time.perf_counter() - t0}}

    tool = _FlowTool("cfd_aero", "流れの計算で横風 (GPU)", "F4", "crosswind.critical_wind_speed", [_sharp_body_check()], analysis)
    prediction = {"quantity": "critical_wind_speed", "output": "v_crit", "assumptions": ["車体は剛体、止まっている", "横の力は風圧の中心にかかる", "設計の既定の C_y"],
                  "inputs": {"m": {"value": f1["m_kg"], "unit": "kg"}, "t": {"value": f1["t_mm"], "unit": "mm"}, "C_y": {"value": f1["C_y"], "unit": "1"}, "A": {"value": f1["A_m2"], "unit": "m^2"}, "h_cp": {"value": f1["h_cp_mm"], "unit": "mm"}, "rho": {"value": f1["rho"], "unit": "kg/m^3"}}}
    return _to_verdict(ctx, tool, lc, prediction, agent)


def _judge_water(ctx: Any, args: Record, agent: str | None) -> Record:
    """Test F5: the gap between the battery, the motor and the controller and the water, in a pass with the vehicle answering it."""
    agent = agent or "lead"
    version = _version(ctx, args)
    design = ctx.store.design(version)
    _say_no_gpu("water")
    p4 = _case_of(ctx.store, version, "P4")
    loading = loading_of(p4) if p4 else DEFAULT_LOADING
    shapes, _ = vehicle.design_shapes(design, 0.0, 5.0, loading, rotate_wheels=True, dx=0.0)
    parts = water.protected_shapes([s for s in shapes if s.reflect >= 1.0])
    if not parts:
        raise ToolError(f"この設計には、水を届かせない部品 ({', '.join(water.PROTECTED_PARTS)}) がありません")
    lowers = sorted(float(s.bounds()[0][2]) * 1000.0 for s in parts.values())
    lowers = (lowers + [float("inf")] * 3)[:3]  # the formula takes three parts; fewer: the rest out of the water's reach
    lowers = [h if h != float("inf") else 1e6 for h in lowers]
    asked = {k: float(args.get(k) or d) for k, d in WATER_DEFAULTS.items()}
    lc = _flow_load_case(ctx, "F5", {"title": "冠水路で電池・モーター・制御器に水が届かない", "params": {"depth": {"value": asked["depth_mm"], "unit": "mm"}, "speed": {"value": asked["speed_kmh"], "unit": "km/h"}, "loading": loading},
                                     "criterion": {"quantity": "water_reached_parts", "op": "<=", "limit_from": {"expr": "0", "unit": "1"}},
                                     "basis": f"限度は「水が届かない」そのもの。{ASSUMED_FLOOD}。実験と比べていない"}, agent)
    depth, speed = lc["params"]["depth"]["value"], lc["params"]["speed"]["value"]

    def analysis(design: Record, load_case: Record) -> Record:
        t0 = time.perf_counter()
        pass_args = {k: v for k, v in args.items() if k in ("design_version", "dx_mm", "vehicle_moves", "frame_hz", "keep_particles")}
        out = _water_pass(ctx, {**pass_args, "depth_mm": depth, "speed_kmh": speed})
        results = [{"quantity": "water_reached_parts", "value": float(sum(gap["reached_s"] is not None for gap in out["protected_parts"].values())), "unit": "1"}]
        for name, gap in out["protected_parts"].items():
            results.append({"quantity": "water_clearance", "value": float(gap["clearance_mm"]), "unit": "mm", "where": name})
            if gap["reached_s"] is not None:
                results.append({"quantity": "water_reached_time", "value": float(gap["reached_s"]), "unit": "s", "where": name})
        if out["vehicle"]:
            results += [{"quantity": "lowest_speed", "value": out["vehicle"]["speed_kmh"]["lowest"], "unit": "km/h"}, {"quantity": "pitch_nose_up_largest", "value": out["vehicle"]["pitch_deg_nose_up_against_road"]["max"], "unit": "deg"}]
        return {"status": "ok", "base": record.BASE, "results": results, "physically_validated": False, "assumptions": [water.STATEMENT, ASSUMED_FLOOD, *out["assumptions"], NOT_COMPARED], "wall_time_s": time.perf_counter() - t0, "timings_s": {"solve": time.perf_counter() - t0}}

    tool = _FlowTool("cfd_water", "冠水路の水 (GPU)", "F5", "flooded.parts_reached", [_moving_box_check()], analysis)
    prediction = {"quantity": "water_reached_parts", "output": "n_reached", "assumptions": ["水は車の前で速度水頭の高さまで上がるとみる。上限の見積もりで、部品の前後の位置は見ない", ASSUMED_FLOOD],
                  "inputs": {"h_1": {"value": lowers[0], "unit": "mm"}, "h_2": {"value": lowers[1], "unit": "mm"}, "h_3": {"value": lowers[2], "unit": "mm"}, "d_water": {"value": depth, "unit": "mm"}, "V": {"value": speed, "unit": "km/h"}}}
    return _to_verdict(ctx, tool, lc, prediction, agent, water.STATEMENT)


def _moving_box_check() -> Record:
    """The known answer the water solver can give in seconds: a box driven through water gives the water exactly the momentum summed from the boundary's changes (no factor)."""
    with cuda.gpu_turn(GPU_WAIT_S), cuda.Gpu("water") as live:
        got = water.check_moving_box(live, dx=0.05, seconds=0.2)
    return {"case": "a box driven through water, 0.2 s at 50 mm", "quantity": "momentum_error_over_box_impulse", "expected": 0.0, "got": float(got["error_x_over_box"]), "unit": "1", "tolerance_rel": 0.0, "tolerance_abs": 1e-3,
            "pass": float(got["error_x_over_box"]) <= 1e-3, "source": "the water's momentum against the impulses the kernels sum: it must balance with no factor"}


def _input_schema(name: str = NAME, here: Record = INPUT_SCHEMA) -> Record:
    """The contract's tool_input once it has it; until then the same shape from here (the lead adds it to the contract)."""
    schema = dict(contract.schema()["$defs"].get(f"tool_input.{name}") or here)
    schema.pop("description", None)
    return schema


TOOLS: dict[str, Record] = {NAME: {"description": DESCRIPTION, "input_schema": _input_schema(), "function": cfd_aero},
                            WATER_NAME: {"description": WATER_DESCRIPTION, "input_schema": _input_schema(WATER_NAME, WATER_INPUT_SCHEMA), "function": cfd_water}}


def _register() -> None:
    """Add the tool to hiraku.tools.mcp's tables (imported here; when mcp is the importer, its tables are ready)."""
    try:
        from hiraku.tools import mcp
    except ModuleNotFoundError as e:
        if e.name != "claude_agent_sdk":
            raise
        return  # a Python without the Agent SDK has no MCP server to add to; the function still works

    for name, t in TOOLS.items():
        mcp.INPUT_SCHEMAS[name] = t["input_schema"]
        mcp.DESCRIPTIONS[name] = t["description"]
        mcp.HANDLERS[name] = t["function"]


_register()

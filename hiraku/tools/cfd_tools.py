"""The flow tool (cfd_aero): a thin adapter between the MCP tools and hiraku.cfd.

The only module outside hiraku/cfd that imports it. hiraku/tools/mcp.py imports this module in one line, and on
import it adds its tool to mcp's tables. The name is in RUN_TOOLS and in the aero CAE role's tools (roles.AERO_TOOLS);
removing those, that line, this file and hiraku/cfd leaves the rest working.

A sweep runs on the GPU only: with no GPU the tool says so and does not run; it never falls back to the CPU.
Its record goes to <run folder>/cfd/v<N>/<speed>_<grid>/ (each case in its own folder). A replay reads a stored
record and touches no GPU. The hand defaults come from the design and the run's load cases here, as plain
numbers, so hiraku.cfd stays free of the catalog and the solvers.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from hiraku import contract
from hiraku.catalog.build import CatalogError
from hiraku.cfd import aero_map, cuda, record, solver, uncertainty
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
        "replay": {"anyOf": [{"type": "boolean"}, {"type": "string", "minLength": 1, "pattern": "\\.json$"}],
                   "description": "Read a stored record instead of running: its path relative to the run's folder, or true for the newest of the version. Needs no GPU."},
    },
}

DESCRIPTION = (
    "設計の版のまわりの空気の流れを GPU で解き、ヨーごとの力 (Fx Fy Fz、N) とモーメント (Mx My Mz、N*m。中心は軸距の中央・輪距の中央の地面の上) "
    "と Cd・CdA・Cy・Cl の表を実行のフォルダの cfd/ に書く。yaws_deg を付けなければ、ヨー 20° までと、F1 の風を止まった車に真横から当てた点、"
    "同じ風を走っている車に当てた点を回す。横風の試験 F1 に渡す形 (C_y・side_area・side_cp_height と、読んだヨー) と、"
    "基準が読む値 (F1 の転倒の風速、F2 の抗力、P4 の電力量と航続) を設計の既定値・流れの値・流れの値の幅で並べ、OK / NG が動くかも返す。"
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


def _replay_path(ctx: Any, args: Record) -> Path:
    run = _run_dir(ctx)
    root = (run / CFD_DIR).resolve()
    replay = args["replay"]
    if replay is True:
        v = _version(ctx, args)
        found = sorted(root.glob(f"v{v}/*/{record.RECORD_FILE}"), key=lambda p: p.stat().st_mtime_ns)
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
    """Run a yaw sweep on the GPU into the run's folder, or read a stored record."""
    args = dict(args or {})
    return _replay(ctx, args) if args.get("replay") else _sweep(ctx, args)


def _input_schema() -> Record:
    """The contract's tool_input once it has it; until then the same shape from here (the lead adds it to the contract)."""
    schema = dict(contract.schema()["$defs"].get(f"tool_input.{NAME}") or INPUT_SCHEMA)
    schema.pop("description", None)
    return schema


TOOLS: dict[str, Record] = {NAME: {"description": DESCRIPTION, "input_schema": _input_schema(), "function": cfd_aero}}


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

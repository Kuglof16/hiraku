"""A yaw sweep of one design on one grid, and the tables made from its forces.

run_sweep runs the cases (hiraku.cfd.vehicle.run_vehicle) and keeps each case's record; everything else
here is arithmetic on the forces a run gives, so the tests need no GPU.

Signs (hiraku/cfd/vehicle.py, links_compute in kernels.cu): vehicle axes x forward, y left, z up; forces on
the vehicle; moments r x F about the moment centre on the ground. A positive yaw is wind from the left, which
pushes the vehicle to the right: Fy < 0. A side force Fy at height h gives Mx = -h Fy, so the height is
-Mx / Fy, and the longitudinal place of the side force is Mz / Fy.

K2's crosswind takes F = 1/2 rho C_y A v^2 with v the wind across the vehicle and one number for C_y. Here
v = U sin(yaw), and C_y A = |Fy| / (1/2 rho v^2) depends on the yaw: every value says the yaw it was read at.

Two kinds of case. Moving: speed V > 0, a wind square to the path, U = V / cos(yaw), so v = V tan(yaw) and yaw
20 deg at 40 km/h is only 4 m/s across. At rest: speed 0, the wind given, ground and wheels still; this is
where K2's critical_wind_speed is read, at yaw 90. A case is (speed m/s, yaw deg, wind m/s or None when moving).
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Sequence

from hiraku.cfd import vehicle

Record = dict[str, Any]
Case = tuple[float, float, "float | None"]

DEFAULT_YAWS_DEG = (0.0, 5.0, -5.0, 10.0, -10.0, 15.0, -15.0, 20.0, -20.0)
DEFAULT_DX_M = 0.08  # run_vehicle's default grid
MAX_YAW_DEG = 85.0  # a moving vehicle: U = V / cos(yaw) has no value at 90
REST_YAW_DEG = 90.0  # a vehicle at rest may stand square to the wind
# From this yaw the vehicle stands broadside to the air, so the domain is wider: HIRAKU's choice, an assumption (no source).
WIDE_FROM_YAW_DEG = 45.0
WIDE_HALF_WIDTH_M = 9.0
STUDY_FACTOR = 1.5  # the grid study's second grid: this many times coarser than the sweep's
# Largest change between the two finest grids still called converged: HIRAKU's choice, an assumption (no source).
GRID_CHANGE_LIMIT_PCT = 5.0

UNITS = {"speed_kmh": "km/h", "yaw_deg": "deg", "air_speed_ms": "m/s", "wind_across_ms": "m/s",
         "Fx_N": "N", "Fy_N": "N", "Fz_N": "N", "drag_N": "N", "Mx_Nm": "N*m", "My_Nm": "N*m", "Mz_Nm": "N*m",
         "Cd": "1", "CdA_m2": "m^2", "Cy": "1", "Cl": "1", "reference_area_m2": "m^2", "q_Pa": "Pa",
         "C_y": "1", "CyA_m2": "m^2", "side_area_m2": "m^2", "side_cp_height_mm": "mm", "side_cp_x_mm": "mm", "lift_area_m2": "m^2",
         "dx_mm": "mm"}

FORCE_KEYS = ("Fx_N", "Fy_N", "Fz_N", "Mx_Nm", "My_Nm", "Mz_Nm")
SPEED_NOT_RUN = ("力は速さの二乗で換算できる形で出しています。速さそのものの効果 (レイノルズ数の違い) は計算していません。"
                 "速さを変えた結果が要るときは、その速さで回してください")
# What the map's numbers rest on, whatever the case
MAP_NOTES = ("ヨーの + は左からの風。走っている行は、横風が進む向きに直角: 空気の速さ U = V / cos(ヨー)、地面は V で動く",
             "K2 の形の C_y は F = 1/2 rho C_y A v^2、v = U sin(ヨー) から出す。ヨーごとに値が違う",
             "風圧の中心の高さは -Mx / Fy で、揚力が左右にずれて作るモーメントの分も含む")
REST_NOTE = "速さ 0 の行は、止まった車に風を当てた値 (地面も車輪も止まっている)。K2 の転倒の風速と同じ場面はヨー 90°"


def yaw_list(yaws: Iterable[float] | None = None) -> list[float]:
    """The yaw angles once each, in the order given (default DEFAULT_YAWS_DEG)."""
    out: list[float] = []
    for y in (DEFAULT_YAWS_DEG if yaws is None else yaws):
        y = float(y) + 0.0  # -0.0 becomes 0.0
        if abs(y) > REST_YAW_DEG:
            raise ValueError(f"yaw {y:g} deg: at most {REST_YAW_DEG:g} deg")
        if y not in out:
            out.append(y)
    if not out:
        raise ValueError("no yaw angle to run")
    return out


def speed_ms_of(design: Record) -> float | None:
    """The design variable max_speed in m/s, or None when the design has none."""
    v = (design.get("variables") or {}).get("max_speed")
    if not v:
        return None
    factor = {"km/h": 1.0 / 3.6, "m/s": 1.0}.get(v.get("unit"))
    if factor is None:
        raise ValueError(f"design variable max_speed has the unit {v.get('unit')!r}; expected km/h or m/s")
    return float(v["value"]) * factor


def sweep_cases(yaws_deg: Iterable[float], speeds_ms: Iterable[float], wind_ms: float | None = None) -> list[Case]:
    """Every yaw at every speed. A speed of 0 is a vehicle at rest in the wind wind_ms."""
    out: list[Case] = []
    for v in speeds_ms:
        for y in yaw_list(yaws_deg):
            if v > 0.0:
                if abs(y) > MAX_YAW_DEG:
                    raise ValueError(f"yaw {y:g} deg at {v * 3.6:g} km/h: a moving vehicle takes at most {MAX_YAW_DEG:g} deg "
                                     "(U = V / cos(yaw)); yaw 90 is speed 0 with a wind")
                out.append((float(v), y, None))
            else:
                if not wind_ms or wind_ms <= 0.0:
                    raise ValueError("a vehicle at rest (speed 0) needs the wind speed")
                out.append((0.0, y, float(wind_ms)))
    return out


def f1_cases(wind_ms: float, speed_ms: float | None = None) -> list[Case]:
    """F1's own wind on both sides: the vehicle at rest at yaw 90, where K2 reads critical_wind_speed; and at the
    load case's speed, at the yaw that wind makes there (rounded to a degree: 30 m/s at 40 km/h is 70)."""
    out: list[Case] = [(0.0, REST_YAW_DEG, float(wind_ms)), (0.0, -REST_YAW_DEG, float(wind_ms))]
    if speed_ms and speed_ms > 0.0:
        yaw = float(round(math.degrees(math.atan2(wind_ms, speed_ms))))
        if 0.0 < yaw <= MAX_YAW_DEG:
            out += [(float(speed_ms), yaw, None), (float(speed_ms), -yaw, None)]
    return out


def plan_cases(design: Record, yaws_deg: Iterable[float] | None = None, speeds_ms: Sequence[float] | None = None, wind_ms: float | None = None,
               extra: Iterable[Case] = ()) -> list[Case]:
    """The sweep's cases: speeds_ms defaults to the design's max_speed; extra after them, once each."""
    if speeds_ms is None or not len(speeds_ms):
        v = speed_ms_of(design)
        if v is None:
            raise ValueError("the design has no max_speed variable: give the speed")
        speeds_ms = [v]
    out = sweep_cases(yaws_deg if yaws_deg is not None else DEFAULT_YAWS_DEG, speeds_ms, wind_ms)
    return out + [c for c in dict.fromkeys(extra) if c not in out]


def domain_of(yaw_deg: float) -> vehicle.Domain:
    """The domain of a case: wider when the vehicle stands broadside to the air."""
    return vehicle.Domain(half_width=WIDE_HALF_WIDTH_M) if abs(yaw_deg) >= WIDE_FROM_YAW_DEG else vehicle.Domain()


def largest_grid(cases: Iterable[Case], dx: float) -> Any:
    """The grid of the case that takes the most memory, for the check before a sweep."""
    return vehicle.Domain.grid(domain_of(max((abs(c[1]) for c in cases), default=0.0)), dx)


def wind_side(yaw_deg: float) -> str | None:
    return "left" if yaw_deg > 0.0 else "right" if yaw_deg < 0.0 else None


def case_id(speed_ms: float, yaw_deg: float, dx: float, wind_ms: float | None = None) -> str:
    at = f"{speed_ms * 3.6:g}kmh" if speed_ms > 0.0 or wind_ms is None else f"rest_wind{wind_ms:g}"
    return f"{at}_yaw{yaw_deg:+g}_dx{dx * 1000.0:g}"


def case_row(run: Any, folder: str | None = None) -> Record:
    """One line of the table, from a VehicleRun: plain numbers, ready for JSON."""
    f, c, n = run.forces_N, run.coefficients, run.numerics
    yaw = float(run.yaw_deg)
    speed = float(run.speed_ms)
    row: Record = {
        "case_id": case_id(speed, yaw, run.dx, None if speed > 0.0 else float(run.air_speed_ms)), "design_version": run.design_version,
        "speed_ms": speed, "speed_kmh": speed * 3.6,
        "yaw_deg": yaw, "wind_from": wind_side(yaw), "air_speed_ms": float(run.air_speed_ms),
        "wind_across_ms": float(run.air_speed_ms) * abs(math.sin(math.radians(yaw))),
        "Fx_N": f["Fx"], "Fy_N": f["Fy"], "Fz_N": f["Fz"], "Mx_Nm": f["Mx"], "My_Nm": f["My"], "Mz_Nm": f["Mz"], "drag_N": -f["Fx"],
        "Cd": c["Cd"], "CdA_m2": c["CdA_m2"], "Cy": c["Cy"], "Cl": c["Cl"],
        "reference_area_m2": c["reference_area_m2"], "q_Pa": c["q_Pa"],
        "grid": {"dx_mm": float(run.dx) * 1000.0, "cells": list(n.get("cells", [])), "domain_m": n.get("domain_m")},
        "convergence": dict(run.convergence, max_mach=n.get("max_mach"), steps=n.get("steps"), warm_steps=n.get("warm_steps"),
                            average_steps=n.get("average_steps")),
        "numerics": {k: n[k] for k in ("u_lattice", "tau0", "dt_s", "smagorinsky", "interpolated_link_share", "device") if k in n},
        "groups_N": run.groups_N, "geometry": run.geometry, "assumptions": list(run.assumptions),
        "wall_time_s": float(getattr(run.result, "wall_time_s", 0.0)),
    }
    if folder is not None:
        row["folder"] = folder
    return row


def scale_to_speed(row: Record, speed_ms: float) -> Record:
    """The row's forces and moments at another speed by speed^2 at the same yaw; for a row at rest speed_ms is the
    wind. The speed effect itself is not in it."""
    k = (speed_ms / (row["speed_ms"] or row["air_speed_ms"])) ** 2
    return {"speed_ms": speed_ms, "speed_kmh": speed_ms * 3.6, "yaw_deg": row["yaw_deg"], "from_speed_ms": row["speed_ms"] or row["air_speed_ms"],
            **{key: row[key] * k for key in (*FORCE_KEYS, "drag_N")}, "statement": SPEED_NOT_RUN}


def crosswind_form(row: Record, side_area_m2: float) -> Record | None:
    """The row in the form K2's crosswind takes, or None at yaw 0 or without a side force."""
    s = math.sin(math.radians(row["yaw_deg"]))
    fy = row["Fy_N"]
    if s == 0.0 or fy == 0.0:
        return None
    q_across = row["q_Pa"] * s * s  # 1/2 rho v^2 with v = U sin(yaw)
    pushed = -math.copysign(1.0, s) * fy  # positive when the wind pushes the vehicle downwind
    cya = pushed / q_across
    return {"case_id": row["case_id"], "speed_kmh": row["speed_kmh"], "yaw_deg": row["yaw_deg"], "wind_from": row["wind_from"],
            "wind_across_ms": row["wind_across_ms"], "CyA_m2": cya, "side_area_m2": side_area_m2, "C_y": cya / side_area_m2,
            "side_cp_height_mm": -row["Mx_Nm"] / fy * 1000.0, "side_cp_x_mm": row["Mz_Nm"] / fy * 1000.0,
            "lift_area_m2": row["Fz_N"] / q_across}  # Fz = 1/2 rho (this) v^2: K0 and K2 leave the lift out


def _diff_pct(a: float, b: float) -> float | None:
    mean = 0.5 * (abs(a) + abs(b))
    return abs(a - b) / mean * 100.0 if mean else None


def plus_minus(rows: Sequence[Record], forms: Sequence[Record]) -> list[Record]:
    """+yaw against -yaw at the same speed: the design is not exactly symmetric."""
    out = []
    by = {(round(f["speed_kmh"], 6), f["yaw_deg"]): f for f in forms}
    drag = {(round(r["speed_kmh"], 6), r["yaw_deg"]): r["drag_N"] for r in rows}
    for (speed, yaw), plus in sorted(by.items()):
        minus = by.get((speed, -yaw))
        if yaw <= 0.0 or minus is None:
            continue
        out.append({"speed_kmh": plus["speed_kmh"], "yaw_deg": yaw,
                    "C_y_plus": plus["C_y"], "C_y_minus": minus["C_y"], "C_y_diff_pct": _diff_pct(plus["C_y"], minus["C_y"]),
                    "side_cp_height_plus_mm": plus["side_cp_height_mm"], "side_cp_height_minus_mm": minus["side_cp_height_mm"],
                    "side_cp_height_diff_pct": _diff_pct(plus["side_cp_height_mm"], minus["side_cp_height_mm"]),
                    # at rest in a side wind the force along x is about zero: no drag to compare
                    "drag_diff_pct": _diff_pct(drag[(speed, yaw)], drag[(speed, -yaw)]) if speed > 0.0 else None})
    return out


def usable_forms(forms: Iterable[Record], wind_from: str | None = None) -> list[Record]:
    """The forms K2 could take (pushed downwind, the height above the ground), of one side when given."""
    return [f for f in forms if f["C_y"] > 0.0 and f["side_cp_height_mm"] > 0.0 and (wind_from is None or f["wind_from"] == wind_from)]


def k2_point(forms: Sequence[Record], wind_from: str | None = None) -> Record | None:
    """The one C_y, side area and height K2's model can hold, and the yaw they come from.

    The largest yaw computed: K2 reads critical_wind_speed at rest in a wind square to the vehicle, so yaw 90 at
    rest when it is there. C_y A goes as 1 / yaw at small yaw (a force about linear in the yaw over v^2), so the
    small angles are not the unfavourable end of a range that applies at a strong crosswind. Without wind_from
    the side of the larger rolling moment."""
    fit = usable_forms(forms, wind_from)
    if not fit:
        return None
    top = max(abs(f["yaw_deg"]) for f in fit)
    best = max((f for f in fit if abs(f["yaw_deg"]) == top), key=lambda f: f["CyA_m2"] * f["side_cp_height_mm"])
    at = (f"止まった車に横風 {best['wind_across_ms']:g} m/s" if best["speed_kmh"] == 0.0
          else f"{best['speed_kmh']:g} km/h、横風 {best['wind_across_ms']:.1f} m/s 相当")
    return {"cy": best["C_y"], "side_area_m2": best["side_area_m2"], "side_cp_height_mm": best["side_cp_height_mm"],
            "CyA_m2": best["CyA_m2"], "side_cp_x_mm": best["side_cp_x_mm"], "from_yaw_deg": best["yaw_deg"], "wind_from": best["wind_from"],
            "from_speed_kmh": best["speed_kmh"], "from_wind_across_ms": best["wind_across_ms"], "case_id": best["case_id"],
            "note": f"K2 は C_y を 1 つの数で持つ。この値はヨー {best['yaw_deg']:+g}° ({at}) の 1 点で、当てはめではない"}


def grid_study(rows: Iterable[Record]) -> list[Record]:
    """Each case computed on more than one grid: its values coarse to fine, and the change between the two finest."""
    cases: dict[tuple[float, float], list[Record]] = {}
    for r in rows:
        cases.setdefault((round(r["speed_kmh"], 6), r["yaw_deg"]), []).append(r)
    out = []
    for (speed, yaw), same in cases.items():
        if len({r["grid"]["dx_mm"] for r in same}) < 2:
            continue
        levels = sorted(same, key=lambda r: -r["grid"]["dx_mm"])
        names = ("drag_N", "CdA_m2") if yaw == 0.0 else ("drag_N", "CdA_m2", "Fy_N", "Mx_Nm")
        fine, nextfine = levels[-1], levels[-2]
        change = {k: abs(fine[k] - nextfine[k]) / abs(fine[k]) * 100.0 if fine[k] else None for k in names}
        out.append({"speed_kmh": same[0]["speed_kmh"], "yaw_deg": yaw, "limit_pct": GRID_CHANGE_LIMIT_PCT, "change_pct": change,
                    "levels": [{"dx_mm": r["grid"]["dx_mm"], "cells": r["grid"]["cells"], **{k: r[k] for k in names}} for r in levels]})
    return out


def speed_effect(rows: Sequence[Record]) -> Record:
    """The speed^2 law and whether the speed effect was computed; when more than one speed ran, the exponents they give.
    Rows at rest are another scene, not another speed of the same one."""
    moving = [r for r in rows if r["speed_ms"] > 0.0]
    speeds = sorted({round(r["speed_kmh"], 6) for r in moving})
    out: Record = {"law": "力とモーメントは同じヨーで速さの二乗に比例するとして換算する", "computed": False, "speeds_kmh": speeds,
                   "statement": SPEED_NOT_RUN}
    if len(speeds) < 2:
        return out
    exponents = []
    for yaw in sorted({r["yaw_deg"] for r in moving}):
        same = sorted((r for r in moving if r["yaw_deg"] == yaw), key=lambda r: r["speed_ms"])
        if len({round(r["speed_kmh"], 6) for r in same}) < 2 or same[0]["grid"]["dx_mm"] != same[-1]["grid"]["dx_mm"]:
            continue
        low, high = same[0], same[-1]
        ratio = math.log(high["speed_ms"] / low["speed_ms"])
        row: Record = {"yaw_deg": yaw, "from_speed_kmh": low["speed_kmh"], "to_speed_kmh": high["speed_kmh"]}
        for key in ("Fx_N",) if yaw == 0.0 else ("Fx_N", "Fy_N", "Mx_Nm"):
            row[key.split("_")[0] + "_exponent"] = (math.log(abs(high[key] / low[key])) / ratio if low[key] and high[key] else None)
        exponents.append(row)
    if exponents:
        out.update(computed=True, exponents=exponents,
                   statement=f"速さを変えて計算した ({', '.join(f'{s:g}' for s in speeds)} km/h)。力が速さの何乗で変わったかは exponents の値")
    return out


def _unique(texts: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(texts))


def build_map(rows: Sequence[Record], *, moment_centre_mm: Sequence[float], side_area_m2: float, loading: str | None = None,
              study_rows: Sequence[Record] = (), wind_from: str | None = None) -> Record:
    """The aero map: rows of the sweep's grid, the study's rows on other grids, and what is read from them."""
    forms = [f for f in (crosswind_form(r, side_area_m2) for r in rows) if f is not None]
    study_forms = [f for f in (crosswind_form(r, side_area_m2) for r in study_rows) if f is not None]
    centre = [float(x) for x in moment_centre_mm]
    effect = speed_effect(rows)
    notes = [f"モーメントの中心は軸距の中央・輪距の中央の地面の上 (設計の座標 x {centre[0]:g}、y {centre[1]:g}、z {centre[2]:g} mm)。"
             "軸は x 前、y 左、z 上", *MAP_NOTES, *([REST_NOTE] if any(r["speed_ms"] == 0.0 for r in rows) else []), effect["statement"]]
    return {"units": UNITS,
            "moment_centre": {"design_mm": centre, "text": "軸距の中央・輪距の中央の地面の上", "axes": "x 前、y 左、z 上"},
            "loading": loading, "air": {"rho_kg_m3": vehicle.RHO_AIR, "nu_m2_s": vehicle.NU_AIR},
            "side_area_m2": side_area_m2,
            "table": list(rows), "study_table": list(study_rows),
            "drag_yaw0": [{"speed_kmh": r["speed_kmh"], "drag_N": r["drag_N"], "Cd": r["Cd"], "CdA_m2": r["CdA_m2"], "reference_area_m2": r["reference_area_m2"],
                           "dx_mm": r["grid"]["dx_mm"], "case_id": r["case_id"]}
                          for r in rows if r["yaw_deg"] == 0.0 and r["speed_ms"] > 0.0],
            "crosswind": forms, "study_crosswind": study_forms, "plus_minus": plus_minus(rows, forms),
            "k2_point": k2_point(forms, wind_from), "grid_study": grid_study([*rows, *study_rows]),
            "speed_effect": effect, "assumptions": _unique([*notes, *(a for r in rows for a in r["assumptions"])])}


def study_cases(cases: Sequence[Case], wind_from: str | None) -> list[Case]:
    """Where the grid study looks: the drag at yaw 0, and the case K2's numbers come from (the largest yaw of the side
    K2 reads; both sides without wind_from), once per yaw."""
    out = [c for c in cases if c[1] == 0.0 and c[0] > 0.0][:1]
    sides = [c for c in cases if c[1] != 0.0 and (wind_from is None or wind_side(c[1]) == wind_from)]
    if sides:
        top = max(abs(c[1]) for c in sides)
        out += list({c[1]: c for c in reversed([c for c in sides if abs(c[1]) == top])}.values())
    return out


def run_sweep(gpu: Any, design: Record, *, cases: Sequence[Case] | None = None, dx: float = DEFAULT_DX_M, folder: Path | None = None,
              loading: str | None = None, wind_from: str | None = None, study_factor: float | None = STUDY_FACTOR,
              runner: Callable[..., Any] | None = None, **run_kw: Any) -> Record:
    """Run every case on one grid, then the grid study; each case keeps its record in folder/<case id>/.

    cases defaults to plan_cases(design). A run's error (the memory budget included) goes up with the cases done so
    far saved. run_kw goes to run_vehicle (flow_throughs and the like)."""
    run_one = runner or vehicle.run_vehicle
    todo = list(cases) if cases is not None else plan_cases(design)
    centre, _ = vehicle.moment_centre(design)
    # the same reference and side area for every case: the shapes square to the axes, with the same parts on board
    area = vehicle.frontal_area_m2(vehicle.design_shapes(design, 0.0, todo[0][0], loading)[0])
    side = vehicle.frontal_area_m2(vehicle.design_shapes(design, REST_YAW_DEG, todo[0][0], loading)[0])

    def one(case: Case, grid: float) -> Record:
        speed, yaw, wind = case
        kw = dict(run_kw)
        kw.setdefault("domain", domain_of(yaw))
        if wind is not None:
            kw["air_speed_ms"] = wind
        run = run_one(gpu, design, yaw_deg=yaw, speed_ms=speed, dx=grid, loading=loading, reference_area_m2=area, **kw)
        name = case_id(speed, yaw, grid, wind)
        if folder is not None:
            run.save(Path(folder) / name)
        return case_row(run, name if folder is not None else None)

    rows = [one(c, dx) for c in todo]
    study = [one(c, dx * study_factor) for c in study_cases(todo, wind_from)] if study_factor else []
    return build_map(rows, moment_centre_mm=centre, side_area_m2=side, loading=loading, study_rows=study, wind_from=wind_from)


def rows_from_folder(folder: Path) -> list[Record]:
    """The rows of the cases VehicleRun.save kept under folder/<any name>/case.json; no GPU. The clock comes back from
    the record's own speed: the solver works out mlups from the cells, the steps and the wall time."""
    rows = []
    for p in sorted(Path(folder).glob("*/case.json")):
        s = json.loads(p.read_text(encoding="utf-8"))
        n = s["numerics"]
        wall = math.prod(n["cells"]) * n["steps"] / (n["mlups"] * 1e6) if n.get("mlups") else 0.0
        rows.append(case_row(SimpleNamespace(**s, result=SimpleNamespace(wall_time_s=wall)), p.parent.name))
    return rows


def map_from_folder(folder: Path, design: Record, *, loading: str | None = None, wind_from: str | None = None) -> Record:
    """The aero map of a sweep run by hand and saved under folder: the finest grid is the sweep, any other its study.
    Refuses cases of another version of the design, whose areas and hand values would not belong with them."""
    rows = rows_from_folder(folder)
    if not rows:
        raise ValueError(f"{folder}: no case.json under it")
    version = design.get("design_version")
    other = sorted({str(r["design_version"]) for r in rows if r["design_version"] != version})
    if other:
        raise ValueError(f"{folder}: cases of design version {', '.join(other)}, the design given is {version}")
    fine = min(r["grid"]["dx_mm"] for r in rows)
    main, study = [r for r in rows if r["grid"]["dx_mm"] == fine], [r for r in rows if r["grid"]["dx_mm"] != fine]
    centre, _ = vehicle.moment_centre(design)
    side = vehicle.frontal_area_m2(vehicle.design_shapes(design, REST_YAW_DEG, 0.0, loading)[0])
    return build_map(main, moment_centre_mm=centre, side_area_m2=side, loading=loading, study_rows=study, wind_from=wind_from)

"""What the flow numbers do to the results a criterion reads, and whether the verdict moves.

Each row shows one result with the hand default (the design's own Cd and Cy), with the flow value, and with the
flow value plus and minus its stated uncertainty: the change between grids, plus the validation error once
data/validation.json exists. The equations are the K0 library's (hiraku/solvers/k0_handcalc: crosswind.
critical_wind_speed, drive_energy.loop_energy, drive_energy.soc_end), written out in plain Python so this
package imports nothing but the contract; tests/cfd/test_aero_map.py checks them against the library.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable

Record = dict[str, Any]

G = 9.80665  # m/s^2: the K0 library's standard gravity (hiraku.solvers.k0_handcalc.units.STANDARD_GRAVITY)
VALIDATION_FILE = Path(__file__).with_name("data") / "validation.json"
AT_REST_YAW_DEG = 90.0  # where K2 reads critical_wind_speed: the vehicle at rest, the wind square to it
F1_ROWS_FROM_YAW_DEG = 20.0  # F1 shows a row per yaw from here up; below it C_y A only grows as 1 / yaw
OPS: dict[str, Callable[[float, float], bool]] = {"<=": lambda a, b: a <= b, "<": lambda a, b: a < b, ">=": lambda a, b: a >= b, ">": lambda a, b: a > b}


def critical_wind_speed(m_kg: float, t_mm: float, cy_a_h_m2mm: float, rho: float) -> float:
    """crosswind.critical_wind_speed: v_crit = sqrt(m g t / (rho C_y A h_cp)), t and h_cp both in mm."""
    return math.sqrt(m_kg * G * t_mm / (rho * cy_a_h_m2mm))


def drag_force(rho: float, cda_m2: float, v_ms: float) -> float:
    """F = 1/2 rho C_d A v^2."""
    return 0.5 * rho * cda_m2 * v_ms * v_ms


def loop_energy(m_kg: float, d_km: float, v_kmh: float, c_rr: float, cda_m2: float, h_m: float, eta: float, eta_regen: float, rho: float) -> float:
    """drive_energy.loop_energy, Wh: E = [(m g C_rr + 1/2 rho C_d A v^2) d + m g H] / eta - eta_regen m g H."""
    joules = (m_kg * G * c_rr + drag_force(rho, cda_m2, v_kmh / 3.6)) * d_km * 1000.0 + m_kg * G * h_m
    return (joules / eta - eta_regen * m_kg * G * h_m) / 3600.0


def drive_range(d_km: float, e_batt_kwh: float, e_wh: float) -> float:
    """drive_energy.loop_energy's range, km: d E_batt / E."""
    return d_km * e_batt_kwh * 1000.0 / e_wh


def soc_end(e_wh: float, e_batt_kwh: float, soc_start_pct: float) -> float:
    """drive_energy.soc_end, %: soc_start - E / E_batt."""
    return soc_start_pct - e_wh / (e_batt_kwh * 1000.0) * 100.0


# The reference bodies by kind. A design here is a set of boxes and tubes with sharp edges, so the band on its results comes from
# the sharp bodies, whose separation is pinned at the edges. The round bodies are reported separately (validation_by_class).
SHARP_BODIES = ("cube", "plate")


def _largest_error(node: Any) -> float | None:
    found: list[float] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for k, v in item.items():
                if str(k).endswith("error_pct") and isinstance(v, (int, float)) and not isinstance(v, bool):
                    found.append(abs(float(v)))
                else:
                    walk(v)
        elif isinstance(item, list):
            for v in item:
                walk(v)

    walk(node)
    return max(found) if found else None


def validation_by_class(path: Path = VALIDATION_FILE) -> dict[str, float | None]:
    """The largest |error| in % per kind of reference body: sharp edges, a round nose (Ahmed), spheres."""
    path = Path(path)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    bodies = data.get("bodies") if isinstance(data, dict) else None
    if not isinstance(bodies, dict):
        return {"all": _largest_error(data)}
    return {"sharp_edges": _largest_error({k: v for k, v in bodies.items() if k in SHARP_BODIES}),
            "round_nose": _largest_error({k: v for k, v in bodies.items() if k.startswith("ahmed")}),
            "sphere": _largest_error({k: v for k, v in bodies.items() if k.startswith("sphere")})}


def validation_error_pct(path: Path = VALIDATION_FILE) -> float | None:
    """The band for a design's results: the largest error on the sharp bodies at any grid level, or on all bodies when the file
    names none. The largest, so the band does not rest on the level the solver happens to do best on."""
    classes = validation_by_class(path)
    if "sharp_edges" in classes:
        return classes["sharp_edges"]
    return classes.get("all")


def _verdict(value: float | None, criterion: Record | None) -> str | None:
    if value is None or criterion is None:
        return None
    return "OK" if OPS[criterion["op"]](value, criterion["limit"]) else "NG"


def _margins(grid_pct: float | None, validation_pct: float | None) -> Record:
    parts = [p for p in (grid_pct, validation_pct) if p is not None]
    return {"grid_pct": grid_pct, "validation_pct": validation_pct, "total_pct": sum(parts) if parts else None}


def _row(test: str, quantity: str, unit: str, value_of: Callable[[float], float], hand_x: float | None, cfd_x: float | None,
         margins: Record, criterion: Record | None = None, judged_of: Callable[[float], float] | None = None, note: str | None = None,
         **extra: Any) -> Record:
    """One row. x is what the flow changes (C_d A, or C_y A h_cp); value_of(x) the row's result and judged_of(x)
    the value the criterion reads, when that is another result (P4 judges soc_end for all its rows)."""
    def value(x: float | None) -> float | None:
        return value_of(x) if x is not None and x > 0.0 else None

    def judged(x: float | None) -> float | None:
        if x is None or x <= 0.0:
            return None
        return (judged_of or value_of)(x)

    total = margins["total_pct"]
    band = (None, None) if total is None or cfd_x is None else (cfd_x * (1.0 - total / 100.0), cfd_x * (1.0 + total / 100.0))
    low_x, high_x = band
    if total is not None and cfd_x is not None and cfd_x > 0.0 and value(band[1]) < value(cfd_x):
        low_x, high_x = band[1], band[0]  # more of x lowers this result (the wind speed, the range)
    hand_v, cfd_v = value(hand_x), value(cfd_x)
    verdicts = {"verdict_hand": _verdict(judged(hand_x), criterion), "verdict_cfd": _verdict(judged(cfd_x), criterion),
                "verdict_low": _verdict(judged(low_x), criterion), "verdict_high": _verdict(judged(high_x), criterion)}
    seen = {v for v in verdicts.values() if v is not None}
    return {"test": test, "quantity": quantity, "unit": unit, "hand": hand_v, "cfd": cfd_v, "cfd_low": value(low_x), "cfd_high": value(high_x),
            "diff_pct": (cfd_v - hand_v) / abs(hand_v) * 100.0 if hand_v and cfd_v is not None else None,
            "uncertainty": margins, "criterion": criterion, **verdicts, "verdict_changes": len(seen) > 1 if criterion else None,
            **({"note": note} if note else {}), **extra}


def _grid_change(aero: Record, speed_kmh: float, yaw: float, key: str) -> float | None:
    return next((s["change_pct"].get(key) for s in aero.get("grid_study", [])
                 if s["yaw_deg"] == yaw and math.isclose(s["speed_kmh"], speed_kmh, abs_tol=1e-6)), None)


def _f1_rows(aero: Record, hand: Record | None, validation_pct: float | None) -> list[Record]:
    """One row per point of F1's side worth reading: K2's point first, then the other large yaws, to show how far the
    one number K2 holds depends on where it is read."""
    point = aero.get("k2_point")
    if not hand or not point:
        return []
    forms = [f for f in aero["crosswind"] if f["wind_from"] == point["wind_from"] and f["C_y"] > 0.0 and f["side_cp_height_mm"] > 0.0]
    shown = [f for f in forms if abs(f["yaw_deg"]) >= F1_ROWS_FROM_YAW_DEG or f["case_id"] == point["case_id"]]
    shown.sort(key=lambda f: (f["case_id"] != point["case_id"], -abs(f["yaw_deg"])))
    hand_x = None if None in (hand.get("C_y"), hand.get("A_m2"), hand.get("h_cp_mm")) else hand["C_y"] * hand["A_m2"] * hand["h_cp_mm"]
    criterion = hand.get("criterion")
    outside = abs(point["from_yaw_deg"]) < AT_REST_YAW_DEG or point["from_speed_kmh"] > 0.0
    rows = []
    for f in shown:
        yaw = f["yaw_deg"]
        reads = f["case_id"] == point["case_id"]
        extra: Record = {"from_yaw_deg": yaw, "from_speed_kmh": f["speed_kmh"], "from_wind_across_ms": f["wind_across_ms"], "reads_k2_point": reads,
                         "criterion_yaw_deg": AT_REST_YAW_DEG, "yaw_outside_sweep": outside}
        note = point["note"] if reads else (f"ヨー {yaw:+g}° ({f['speed_kmh']:g} km/h、横風 {f['wind_across_ms']:.1f} m/s 相当) で読んだ場合。"
                                            f"K2 に渡すのはヨー {point['from_yaw_deg']:+g}° の値")
        if criterion and hand.get("speed_ms"):
            extra["gust_yaw_deg"] = math.degrees(math.atan2(criterion["limit"], hand["speed_ms"]))  # the criterion's wind on the moving vehicle
        if reads and outside:
            note += ("。K2 の転倒の風速は止まった車 (ヨー 90°) の値で、計算したヨーの外。K2 の式は C_y を 1 つの数で持つので、この差は式に入らない。"
                     "この値で判定が動くときは、ヨー 90° を回してから決める")
        x = f["CyA_m2"] * f["side_cp_height_mm"]
        # Balance about the downwind contact line with the lift as well: Mx + Fz t / 2 = m g t / 2. The K0 formula has no lift.
        with_lift = x + f["lift_area_m2"] * hand["t_mm"] / 2.0
        if reads and with_lift > 0.0:
            extra["cfd_with_lift"] = critical_wind_speed(hand["m_kg"], hand["t_mm"], with_lift, hand["rho"])
            extra["verdict_with_lift"] = _verdict(extra["cfd_with_lift"], criterion)
            note += f"。式には揚力が無い。流れの揚力も入れた釣り合いでは {extra['cfd_with_lift']:.1f} m/s"
        rows.append(_row("F1", "critical_wind_speed", "m/s", lambda v: critical_wind_speed(hand["m_kg"], hand["t_mm"], v, hand["rho"]), hand_x,
                         x, _margins(_grid_change(aero, f["speed_kmh"], yaw, "Mx_Nm"), validation_pct), criterion, note=note, **extra))
    return rows


def _f2_rows(aero: Record, hand: Record | None, validation_pct: float | None) -> list[Record]:
    drag = (aero.get("drag_yaw0") or [None])[0]
    if not drag:
        return []
    hand = hand or {}
    v = drag["speed_kmh"] / 3.6
    rho = hand.get("rho") or aero["air"]["rho_kg_m3"]
    hand_x = None if None in (hand.get("C_d"), hand.get("A_m2")) else hand["C_d"] * hand["A_m2"]
    return [_row("F2", "drag_force", "N", lambda x: drag_force(rho, x, v), hand_x, drag["CdA_m2"],
                 _margins(_grid_change(aero, drag["speed_kmh"], 0.0, "CdA_m2"), validation_pct),
                 note=f"ヨー 0、{drag['speed_kmh']:g} km/h。F2 の基準はまだ無いので判定は無い", at_speed_kmh=drag["speed_kmh"])]


def _p4_rows(aero: Record, hand: Record | None, validation_pct: float | None) -> list[Record]:
    drag = (aero.get("drag_yaw0") or [None])[0]
    if not hand or not drag:
        return []
    e_batt, soc0 = hand["E_batt_kWh"], hand.get("soc_start_pct", 100.0)

    def energy(x: float) -> float:
        return loop_energy(hand["m_kg"], hand["d_km"], hand["v_kmh"], hand["C_rr"], x, hand["H_m"], hand["eta"], hand["eta_regen"], hand["rho"])

    def end(x: float) -> float:
        return soc_end(energy(x), e_batt, soc0)

    hand_x = None if None in (hand.get("C_d"), hand.get("A_m2")) else hand["C_d"] * hand["A_m2"]
    margins = _margins(_grid_change(aero, drag["speed_kmh"], 0.0, "CdA_m2"), validation_pct)
    note = (f"CdA はヨー 0、{drag['speed_kmh']:g} km/h の値で、周回の速さ {hand['v_kmh']:.1f} km/h でも同じとみる "
            "(速さの効果は計算していない)")
    crit = hand.get("criterion")
    return [_row("P4", "energy_used", "Wh", energy, hand_x, drag["CdA_m2"], margins, crit, end, note),
            _row("P4", "range", "km", lambda x: drive_range(hand["d_km"], e_batt, energy(x)), hand_x, drag["CdA_m2"], margins, crit, end, note),
            _row("P4", "soc_end", "%", end, hand_x, drag["CdA_m2"], margins, crit, end, note)]


def table(aero: Record, hand: Record, validation_pct: float | None = None) -> Record:
    """The uncertainty table of an aero map (hiraku.cfd.aero_map.build_map).

    hand: f1 {m_kg, t_mm, rho, C_y, A_m2, h_cp_mm, speed_ms, criterion?}, f2 {rho, C_d, A_m2}, p4 {m_kg, d_km,
    v_kmh, C_rr, C_d, A_m2, H_m, eta, eta_regen, rho, E_batt_kWh, soc_start_pct, criterion?}; each may be missing.
    criterion: {quantity, op, limit, unit}. validation_pct: validation_error_pct()."""
    rows = [*_f1_rows(aero, hand.get("f1"), validation_pct), *_f2_rows(aero, hand.get("f2"), validation_pct), *_p4_rows(aero, hand.get("p4"), validation_pct)]
    return {"rows": rows, "validation_error_pct": validation_pct,
            "statement": ("幅は、格子を変えたときの変化と、基準の物体で実験と比べた誤差の単純な和 (統計の幅ではない)。"
                          if validation_pct is not None else
                          "幅は、格子を変えたときの変化だけ。実験と比べた誤差はまだ入っていない (実験と比べて確かめていない)。")
            + "OK / NG は、それぞれの値を基準に当てた結果"}

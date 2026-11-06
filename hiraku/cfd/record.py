"""The record of a sweep: one analysis-shaped entry per case (docs/contract.md), the aero map and the uncertainty table.

A saved record is read back without a GPU: this module imports only the standard library, so a replay never
touches the solver. Fields the contract's analysis does not have yet are listed in contract_additions.

The analyses leave analysis_id out (the store assigns it) and hold no prediction ids (the sweep runs outside
analyze); as_contract_analysis fills both when an entry goes into a run's records.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

Record = dict[str, Any]

KIND = "cfd_aero_record"
VERSION = 1
TOOL = "cfd_aero"
BASE = "K6"  # the flow solver; contract `base` needs it (contract_additions)
RECORD_FILE = "aero_record.json"
NOT_CHECKED = "力と係数は実験と比べて確かめていない (physically_validated: false)。格子を変えた変化は数値の確かめで、実験との比べではない"

# table key -> (result quantity, contract unit)
RESULTS = (("speed_kmh", "speed", "km/h"), ("yaw_deg", "yaw", "deg"), ("air_speed_ms", "air_speed", "m/s"),
           ("Fx_N", "Fx", "N"), ("Fy_N", "Fy", "N"), ("Fz_N", "Fz", "N"),
           ("Mx_Nm", "Mx", "N*m"), ("My_Nm", "My", "N*m"), ("Mz_Nm", "Mz", "N*m"),
           ("drag_N", "drag_force", "N"), ("Cd", "Cd", "1"), ("CdA_m2", "CdA", "m^2"), ("Cy", "Cy", "1"), ("Cl", "Cl", "1"),
           ("reference_area_m2", "reference_area", "m^2"), ("q_Pa", "dynamic_pressure", "Pa"))
CROSSWIND_RESULTS = (("wind_across_ms", "wind_across", "m/s"), ("CyA_m2", "side_force_area", "m^2"), ("C_y", "side_force_coefficient", "1"),
                     ("side_area_m2", "side_area", "m^2"), ("side_cp_height_mm", "side_cp_height", "mm"), ("side_cp_x_mm", "side_cp_x", "mm"))
# the grid study's value in mesh_convergence: at yaw 0 the drag, else the rolling moment (F1 reads C_y A h_cp, which is Mx)
STUDY_QUANTITY = {True: ("drag_N", "drag_force", "N"), False: ("Mx_Nm", "Mx", "N*m")}


def load_case_id(row: Record) -> str:
    """The case's name in the characters a load case id may hold: L and R say the side the wind comes from."""
    yaw = row["yaw_deg"]
    side = "L" if yaw > 0.0 else "R" if yaw < 0.0 else ""
    at = f"{row['speed_kmh']:g}kmh" if row["speed_ms"] > 0.0 else f"rest-wind{row['air_speed_ms']:g}"
    return f"CFD-{at}-yaw{abs(yaw):g}{side}"


def mesh_convergence(study: Record) -> Record:
    """The contract's mesh_convergence block from one case of the grid study (levels coarse to fine)."""
    key, quantity, unit = STUDY_QUANTITY[study["yaw_deg"] == 0.0]
    limit, change = study["limit_pct"], study["change_pct"].get(key)
    out: Record = {"quantity": quantity, "unit": unit,
                   "levels": [{"element_size_mm": lv["dx_mm"], "value": lv[key],
                               **({"elements": lv["cells"][0] * lv["cells"][1] * lv["cells"][2]} if len(lv.get("cells") or ()) == 3 else {})}
                              for lv in study["levels"]],
                   "limit_pct": limit, "verdict": "converged" if change is not None and change <= limit else "not_converged"}
    if change is not None:
        out["change_pct"] = change
    return out


def case_analysis(row: Record, form: Record | None = None, study: Record | None = None, design_version: int = 1) -> Record:
    """One case as an analysis without analysis_id. Clock times go only in wall_time_s and timings_s."""
    results = [{"quantity": name, "value": float(row[key]), "unit": unit} for key, name, unit in RESULTS]
    if form:
        results += [{"quantity": name, "value": float(form[key]), "unit": unit, "where": f"wind from the {form['wind_from']}"}
                    for key, name, unit in CROSSWIND_RESULTS]
    out: Record = {"tool": TOOL, "base": BASE, "design_version": int(design_version), "load_case_id": load_case_id(row),
                   "prediction_ids": [], "status": "ok", "results": results, "assumptions": [*row["assumptions"], NOT_CHECKED],
                   "physically_validated": False, "wall_time_s": row["wall_time_s"], "timings_s": {"solve": row["wall_time_s"]}}
    if study:
        out["mesh_convergence"] = mesh_convergence(study)
    return out


def as_contract_analysis(analysis: Record, analysis_id: str, prediction_ids: Iterable[str]) -> Record:
    """The entry with the two ids a run's store and analyze give it."""
    return {**analysis, "analysis_id": analysis_id, "prediction_ids": list(prediction_ids)}


def make_record(aero: Record, uncertainty: Record, *, design_version: int, requested: Record | None = None) -> Record:
    """The record of one sweep. The vehicle's values are not compared with an experiment here, so physically_validated
    stays false whatever the validation file holds: a reference body's error goes into the band, not into this field."""
    forms = {f["case_id"]: f for f in aero["crosswind"]}
    studies = {(round(s["speed_kmh"], 6), s["yaw_deg"]): s for s in aero["grid_study"]}
    analyses = [case_analysis(r, forms.get(r["case_id"]), studies.get((round(r["speed_kmh"], 6), r["yaw_deg"])), design_version) for r in aero["table"]]
    times = {r["case_id"]: r["wall_time_s"] for r in (*aero["table"], *aero.get("study_table", []))}
    return {"kind": KIND, "version": VERSION, "tool": TOOL, "design_version": int(design_version), "physically_validated": False,
            "requested": requested or {}, "analyses": analyses, "aero_map": aero, "uncertainty": uncertainty,
            "assumptions": [*aero["assumptions"], NOT_CHECKED],
            "wall_time_s": sum(times.values()), "timings_s": times}


def save_record(record: Record, folder: Path) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / RECORD_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def load_record(path: Path) -> Record:
    """A saved record; ValueError when the file is not one."""
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"{path}: {e}") from None
    if not isinstance(record, dict) or record.get("kind") != KIND:
        raise ValueError(f"{path}: not a {KIND}")
    return record


def answer(record: Record, path: str, replayed: bool) -> Record:
    """What the tool says about a record: the same for the run and for its replay, apart from `replayed`."""
    aero = record["aero_map"]
    table = [{k: r[k] for k in ("speed_kmh", "yaw_deg", "wind_from", "air_speed_ms", "wind_across_ms", "Fx_N", "Fy_N", "Fz_N", "Mx_Nm", "My_Nm", "Mz_Nm", "Cd", "CdA_m2", "Cy", "Cl", "reference_area_m2", "q_Pa")}
             | {"dx_mm": r["grid"]["dx_mm"], "spread_pct": r["convergence"].get("spread_pct")} for r in aero["table"]]
    return {"path": path, "replayed": replayed, "design_version": record["design_version"], "physically_validated": record["physically_validated"],
            "units": aero["units"], "moment_centre": aero["moment_centre"], "loading": aero.get("loading"), "table": table,
            "drag_yaw0": aero["drag_yaw0"], "crosswind": aero["crosswind"], "plus_minus": aero["plus_minus"], "k2_point": aero["k2_point"],
            "grid_study": aero["grid_study"], "speed_effect": aero["speed_effect"], "uncertainty": record["uncertainty"],
            "assumptions": record["assumptions"], "wall_time_s": record["wall_time_s"]}


def load_case_values(record: Record, path: str | None = None) -> Record:
    """What a load case can take from this record: K2's params in their units, and a basis naming the source. The params
    go in a load case's params, which both the hand prediction (wind_inputs) and the tool read before the design
    variables, so the design's own defaults stay as they are."""
    aero = record["aero_map"]
    point, drag = aero.get("k2_point"), (aero.get("drag_yaw0") or [None])[0]
    params: Record = {}
    sources = [f"cfd_aero の記録{f' {path}' if path else ''} (v{record['design_version']})"]
    if point:
        params.update(cy={"value": point["cy"], "unit": "1"}, side_area={"value": point["side_area_m2"], "unit": "m^2"},
                      side_cp_height={"value": point["side_cp_height_mm"], "unit": "mm"})
        sources.append(f"横の力は {point['case_id']}。{point['note']}")
    if drag:
        params.update(cd={"value": drag["Cd"], "unit": "1"}, front_area={"value": drag["reference_area_m2"], "unit": "m^2"})
        sources.append(f"抗力は {drag['case_id']}")
    return {"params": params, "basis": "。".join([*sources, "実験と比べて確かめていない"]), "physically_validated": record["physically_validated"]}


def contract_additions() -> Record:
    """What the record needs from the contract and it does not have yet (the lead's to add; tests apply it locally)."""
    return {"base_enum": BASE,
            "analysis.physically_validated": {
                "type": "boolean",
                "description": "false: the values were not compared with an experiment on this kind of object (assumptions say 確かめていない)."}}

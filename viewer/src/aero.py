"""Aerodynamics (crosswind F1) data for the viewer: python -m viewer.src.aero <run dir> [--out viewer/data]

The K2 replay of the crosswind record (handling.k2_case) plus the wind:
    wind {cp_mm (the side centre of pressure in the design frame: K2's own Aero.of on its vehicle, CrosswindTool),
          dir (the record's wind vector's unit direction), side_force_N (the record's result at the full gust),
          gust (the load case's wind speed, m/s), from}
The page draws the side force at the centre of pressure as side_force x (wind_speed(t) / gust)^2: the tool's
1/2 rho C_y A v^2 with v the wind alone (the vehicle's own sideways speed is left out of the arrow, not of the run).
"""

from __future__ import annotations

import sys

from viewer.src import common
from viewer.src.handling import build_cases, extra


def wind_of(rec: dict, design: dict, lc: dict | None, loading: str) -> dict:
    from hiraku.contract import loading as contract_loading
    from hiraku.solvers.k2_vehicle.shelf import TOOLS
    tool = TOOLS["crosswind"]
    lp = dict((lc or {}).get("params") or {})
    tool.opts, tool._vars, tool._assumed = {}, design.get("variables") or {}, []
    veh = tool.vehicle(contract_loading.on_board(design, loading), lp)
    aero = tool.aero(veh, lp)
    cp = [1000.0 * (float(veh.cg_s[k]) + float(aero.side_cp[k])) for k in range(3)]
    v = next((x for x in rec.get("vectors", []) or [] if x.get("kind") == "wind"), None)
    f = [float(c) for c in v["vector_N"]] if v else [0.0, -1.0, 0.0]
    n = sum(c * c for c in f) ** 0.5 or 1.0
    gust = lp.get("wind_speed", {}).get("value") if isinstance(lp.get("wind_speed"), dict) else None
    return {"cp_mm": cp, "dir": [c / n for c in f], "side_force_N": common.result(rec, "side_force") or n,
            "gust": float(gust) if gust is not None else max((common.series(rec, "wind_speed") or {"values": [1.0]})["values"]),
            "from": lp.get("wind_from", "left")}


def build(args) -> dict:
    cases = build_cases(args, ("crosswind",), "aero")
    for c, run in zip(cases, [args.run] + list(args.also or [])):
        vdir, rec = common.find_analysis(run, "crosswind", version=args.version, analysis_id=args.analysis)
        design = common.read_json(vdir / "design.json")
        lc = common.load_case_of(vdir, rec["load_case_id"])
        c["wind"] = wind_of(rec, design, lc, c["ledger"]["loading"])
        w = c["wind"]
        c["inputs"] += [{"label": "横風の中心", "value": "(" + ", ".join(f"{v:.0f}" for v in w["cp_mm"]) + ") mm (車体の座標)",
                         "source": "K2 の部品の箱の側面の投影から"},
                        {"label": "横の力", "value": f"{w['side_force_N']:.0f} N (風速 {w['gust']:g} m/s)", "source": "記録の結果"}]
        c["notes"].append("空気の力は、部品の箱から求めた圧力の中心に、抗力と横の力だけをかける。流れの場、表面の圧力、揚力、ヨーのモーメントはない。")
        c["notes"].append("風の矢印 = 突風時の横の力 × (風速(t) / 突風の風速)²。車の横の速さは矢印には入れない (計算には入っている)。")
    return {**cases[0], "cases": cases}


if __name__ == "__main__":
    common.main("aero", build, extra=extra, argv=sys.argv[1:])

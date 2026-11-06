"""Thermal (T1-T3, K3's RC network) data for the viewer: python -m viewer.src.thermal <run dir> [--energy <run dir>]

Reads the newest thermal record (and a drive_energy record of the same run, or of --energy, for the battery):
    t (packed, s): the temperature series' times; nodes [{name, parts [part ids], C_J_per_K, R_K_per_W, tau_s,
        G_to [[other node, W/K]], temp (packed, degC), max, end, limit {value, source} | null, loss_mean_W, ok}]
        (the network as network_from_design builds it from this design; temp.<node> from the record)
    part_node [node name | null per part]: the node each part box takes its colour from (the parts of the node's kind,
        k3_tools._parts_of)
    chain [{channel, kind (power | loss | heat_to_air), stage, part_id, unit, t (packed), values (packed)}]: the record's
        power.* / loss.* / heat_to_air.* series (with part_id when the record has it)
    ambient_C, criterion {quantity, op, limit, unit, value, ok} | null
    energy {source, t (packed), speed, soc, power_battery (kW), current (A), results {...}} | null: drive_energy (P4)
Every number is the record's or computed here from the record and the design."""

from __future__ import annotations

import sys

from viewer.src import common, ledgers


def network(design: dict) -> dict:
    """The RC network as the tool builds it (network_from_design): names, C, resistance to the air, couplings."""
    from hiraku.solvers.k3_power_thermal import network_from_design

    net = network_from_design(design)
    out = []
    for i, n in enumerate(net.names):
        g = float(net.G_amb[i])
        out.append({"name": n, "C_J_per_K": float(net.C[i]), "R_K_per_W": 1.0 / g if g > 0 else None,
                    "tau_s": float(net.C[i]) / g if g > 0 else None,
                    "G_to": [[net.names[j], float(net.G[i, j])] for j in range(len(net.names)) if j != i and net.G[i, j] > 0]})
    return {"nodes": out}


def _limit(design: dict, node: str, lc: dict | None) -> dict | None:
    crit = (lc or {}).get("criterion") or {}
    if crit.get("quantity") == f"temp_{node}_max":
        return {"value": float(crit["limit"]["value"]), "source": "load case criterion: " + crit.get("basis", "")[:120]}
    v = (design.get("variables") or {}).get(f"{node}_max_temperature")
    if v is not None:
        return {"value": float(v["value"]), "source": f"design variable {node}_max_temperature"}
    return None


def _energy(run, version) -> tuple[dict | None, str | None]:
    try:
        vdir, rec = common.find_analysis(run, "drive_energy", version=version)
    except (LookupError, FileNotFoundError):
        return None, None
    ser = {s["channel"]: s for s in rec.get("series", []) or []}
    sp = ser.get("speed")
    if sp is None:
        return None, None
    pb = [s for s in rec.get("series", []) or [] if s["channel"] == "power.battery"]
    tot = [sum(v) for v in zip(*[s["values"] for s in pb])] if pb else None
    out = {"source": f"{common.run_name(run, vdir)} {rec.get('analysis_id', '')} {rec.get('load_case_id', '')}",
           "t": common.pack_f32(sp["t_s"]), "speed": common.pack_f32(sp["values"])}
    for key, ch in (("soc", "soc"), ("current", "current.battery"), ("grade", "grade")):
        if ch in ser:
            out[key] = common.pack_f32(ser[ch]["values"])
    if tot:
        out["power_battery"] = common.pack_f32(tot)
        out["power_parts"] = [s.get("part_id") for s in pb]
    out["results"] = {r["quantity"]: [r["value"], r.get("unit", "")] for r in rec.get("results", [])}
    return out, rec.get("analysis_id")


def build(args) -> dict:
    vdir, rec = common.find_analysis(args.run, "thermal", version=args.version, analysis_id=args.analysis)
    design = common.read_json(vdir / "design.json")
    lc = common.load_case_of(vdir, rec["load_case_id"])
    obj = common.base_object("thermal", "熱: 電池・モーター・制御器の温度 (熱回路)", args.run, vdir, rec, design)
    from hiraku.solvers.shelf.base import on_board
    from hiraku.solvers.shelf.k3_tools import _parts_of

    on = on_board(design, lc) if lc else design
    net = network(on)
    ser = {s["channel"]: s for s in rec.get("series", []) or []}
    first = ser.get(f"temp.{net['nodes'][0]['name']}")
    if first is None:
        raise LookupError(f"{rec['_path']}: no temp.<node> series")
    obj["t"] = common.pack_f32(first["t_s"])
    amb = float(((lc or {}).get("params") or {}).get("ambient_temperature", {}).get("value", first["values"][0]))
    obj["ambient_C"] = amb
    part_ids = [p["id"] for p in obj["parts"]]
    part_node = [None] * len(part_ids)
    for n in net["nodes"]:
        s = ser.get(f"temp.{n['name']}")
        n["parts"] = _parts_of(on, n["name"])
        for pid in n["parts"]:
            if pid in part_ids:
                part_node[part_ids.index(pid)] = n["name"]
        n["temp"] = common.pack_f32(s["values"]) if s else None
        n["max"] = max(s["values"]) if s else None
        n["end"] = s["values"][-1] if s else None
        n["limit"] = _limit(design, n["name"], lc)
        n["loss_mean_W"] = common.result(rec, f"loss_{n['name']}_mean")
        n["ok"] = (n["max"] <= n["limit"]["value"]) if (n["limit"] and n["max"] is not None) else None
    obj["nodes"] = net["nodes"]
    obj["part_node"] = part_node
    chain = []
    for s in rec.get("series", []) or []:
        kind, _, stage = s["channel"].partition(".")
        if kind in ("power", "loss", "heat_to_air"):
            chain.append({"channel": s["channel"], "kind": kind, "stage": stage, "part_id": s.get("part_id"),
                          "unit": s.get("unit", ""), "t": common.pack_f32(s["t_s"]), "values": common.pack_f32(s["values"])})
    obj["chain"] = chain
    crit = (lc or {}).get("criterion")
    if crit:
        v = common.result(rec, crit["quantity"])
        lim = float(crit["limit"]["value"])
        ok = None if v is None else (v <= lim if crit["op"] == "<=" else v < lim if crit["op"] == "<" else
                                     v >= lim if crit["op"] == ">=" else v > lim)
        obj["criterion"] = {"quantity": crit["quantity"], "op": crit["op"], "limit": lim, "unit": crit["limit"].get("unit", ""),
                            "value": v, "ok": ok}
    else:
        obj["criterion"] = None
    obj["energy"], _ = _energy(getattr(args, "energy", None) or args.run, None)
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"]) + [
        {"label": n["name"], "value": f"C {n['C_J_per_K']:g} J/K, R {n['R_K_per_W']:g} K/W to the air (tau {n['tau_s']:.0f} s)",
         "source": f"design variables {n['name']}_heat_capacity, {n['name']}_thermal_resistance (network_from_design)"}
        for n in net["nodes"]]
    obj["notes"] = [
        "熱回路は部品ごとに 1 つの節点 (熱容量 C と空気への熱抵抗 R)。部品の中の温度分布はない。",
        "箱の色はその種類の節点の温度 (同じ種類の部品は同じ色)。熱回路の節点でない部品は灰色。",
        "損失・電力・空気への放熱は記録の系列 (part_id のある系列は部品ごと: 同じ種類の部品で等分)。",
    ] + (["電池の電力と残量は同じ走行の drive_energy の記録から。"] if obj["energy"] else [])
    obj["ledger"] = ledgers.thermal_ledger(design, lc, rec, obj["parts"])
    return obj


def _extra(ap):
    ap.add_argument("--energy", help="run folder with the drive_energy record (default: the same run)")


if __name__ == "__main__":
    common.main("thermal", build, extra=_extra, argv=sys.argv[1:])

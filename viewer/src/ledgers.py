"""How each part enters the thermal, fatigue and strength calculations: the parts layer of those fields, in the
shape of common.parts_ledger (per part: category, mass, nodes with kg each, the deciding function, a sentence; the
members; the mass table; the record's stated mass compared).

The categories of common.CATEGORIES are used where they fit; the thermal field adds two of its own (CATEGORIES_K3),
coloured by viewer/js/thermal.js. Each rule follows the function named in the entry, called where it can be
(hiraku.contract.loading.parts_on_board, k0_handcalc.vehicle.mass_items, k1_frame.analyze.sprung_items)."""

from __future__ import annotations

from typing import Any

from viewer.src import common

Record = dict[str, Any]

CATEGORIES_K3 = {
    "heat_node": "熱回路の節点 (温度を計算)",      # a node of the RC network: its temperature is computed
    "power_stage": "電力の段 (電力・損失の系列)",  # a stage of the power chain: power / loss series only
}

FN_K3_NET = "hiraku.solvers.k3_power_thermal.simulate.network_from_design (THERMAL_PARTS)"
FN_K3_CHAIN = "hiraku.solvers.shelf.k3_tools.chain_series (_parts_of)"
FN_K3_MASS = "hiraku.solvers.k3_power_thermal.powertrain.from_design (mass = mass_summary(design).mass_kg)"
FN_K1_BODY = "hiraku.solvers.k1_frame.model.FrameModel.add_body_loads"
FN_FATIGUE = "hiraku.solvers.k1_frame.fatigue.influence (add_body_loads: inertia relief); shelf.fatigue.road_wheel_forces"
FN_STATIC = "hiraku.solvers.k1_frame.analyze.analyze (add_gravity / add_body_loads)"
FN_SHELL = "hiraku.solvers.k1_shell.frame.analyze_shell (sprung_items, shell_parts)"


def _base(tool: str, design: Record, load_case: Record | None, categories: dict) -> tuple[Record, dict, set]:
    from hiraku.contract import loading as contract_loading
    from hiraku.contract.protocols import DEFAULT_LOADING

    lc_loading = ((load_case or {}).get("params") or {}).get("loading")
    loading = lc_loading or DEFAULT_LOADING
    src = "load case params.loading" if lc_loading else f"default {DEFAULT_LOADING} (the load case gives none)"
    out: Record = {"tool": tool, "loading": loading, "loading_source": src, "categories": categories, "parts": [],
                   "members": None, "table": [], "stated": [], "error": None, "notes": []}
    try:
        on_ids = {p["id"] for p in contract_loading.parts_on_board(design, loading)}
    except ValueError as e:
        out["error"] = f"{common.FN_ON_BOARD}: {e}"
        on_ids = {p["id"] for p in design.get("parts", [])}
    return out, {p["id"]: p for p in design.get("parts", [])}, on_ids


def _items(out: Record, design: Record, on_ids: set) -> tuple[dict, float | None, list]:
    on = {**design, "parts": [p for p in design.get("parts", []) if p["id"] in on_ids]}
    try:
        from hiraku.solvers.k0_handcalc.vehicle import mass_items
        its = mass_items(on)
    except Exception as e:  # the design as stored cannot be read by the tool's own function
        out["error"] = f"{common.FN_MASS_ITEMS} could not read this design: {e}"
        return {}, None, []
    return ({it.id.split(":", 1)[1]: it for it in its if it.carrier != "member"},
            sum(it.mass_kg for it in its if it.carrier == "member"), its)


def _entry(p: Record, src: Record) -> Record:
    return {"id": p["id"], "kind": p.get("kind", ""), "mass_kg": float(src.get("mass_kg", 0.0) or 0.0), "cat": "not_in",
            "nodes": [], "fn": "", "how": "", "stiffness": False}


def _off(e: Record, loading: str) -> Record:
    e.update(fn=common.FN_ON_BOARD, how=f"left off by the loading {loading}: not in the design the tool is given")
    return e


def _finish(out: Record, frame_kg: float | None, labels: dict) -> Record:
    cats: dict[str, Record] = {}
    for e in out["parts"]:
        c = cats.setdefault(e["cat"], {"cat": e["cat"], "label": labels[e["cat"]], "count": 0, "mass_kg": 0.0})
        c["count"] += 1
        c["mass_kg"] += e["mass_kg"]
    out["table"] = [cats[k] for k in labels if k in cats]
    in_calc = sum(e["mass_kg"] for e in out["parts"] if e["cat"] not in ("not_in", "body"))
    out["parts_in_kg"] = in_calc
    out["total_kg"] = in_calc + frame_kg if frame_kg is not None else None
    for s in out["stated"]:
        s["diff_kg"] = None if s["computed_kg"] is None else s["computed_kg"] - s["stated_kg"]
        s["agrees"] = s["diff_kg"] is not None and abs(s["diff_kg"]) <= s["tol_kg"]
        if not s["agrees"]:
            s["why"] = ("not computed: " + (out["error"] or "") if s["diff_kg"] is None else
                        "the design.json read here is not the design the tool weighed (a trimmed or edited copy), or the "
                        "loading differs; computed from this version's design.json with loading " + out["loading"])
    out["no_stiffness"] = [e["id"] for e in out["parts"] if e["cat"] not in ("not_in", "body") and not e["stiffness"]]
    out["not_in"] = [{"id": e["id"], "why": e["how"]} for e in out["parts"] if e["cat"] == "not_in"]
    return out


def _stated(out: Record, record: Record, rows: list[tuple[str, float | None]]) -> None:
    for q, mine in rows:
        v = common.result(record, q)
        if v is not None:
            out["stated"].append({"label": q, "stated_kg": v, "computed_kg": mine, "tol_kg": 1e-6 * max(1.0, v),
                                  "source": f"record result {q}"})


# ---------------------------------------------------------------- thermal / drive_energy (K3)

def thermal_ledger(design: Record, load_case: Record | None, record: Record, parts: list[Record],
                   tool: str = "thermal") -> Record:
    """K3: the parts of the kinds in THERMAL_PARTS (battery, motor, controller) are the RC network's nodes; the reducer
    and the driven wheels are stages of the power chain (power and loss series with their part_id); every part on board
    is in the vehicle mass of the drive (rolling and climbing resistance) and nothing else."""
    from hiraku.solvers.k3_power_thermal.simulate import THERMAL_PARTS
    from hiraku.solvers.shelf.k3_tools import _parts_of

    labels = {**CATEGORIES_K3, "mass_only": common.CATEGORIES["mass_only"], "not_in": common.CATEGORIES["not_in"]}
    out, dparts, on_ids = _base(tool, design, load_case, labels)
    on = {**design, "parts": [p for p in design.get("parts", []) if p["id"] in on_ids]}
    heat = {k: _parts_of(on, k) for k in THERMAL_PARTS}
    stage = {k: _parts_of(on, k) for k in ("controller", "motor", "reducer", "wheel")}
    items, frame_kg, _ = _items(out, design, on_ids)
    for p in parts:
        e = _entry(p, dparts.get(p["id"], {}))
        if p["id"] not in on_ids:
            out["parts"].append(_off(e, out["loading"]))
            continue
        k = next((k for k, ids in heat.items() if p["id"] in ids), None)
        s = next((k for k, ids in stage.items() if p["id"] in ids), None)
        if k:
            n = len(heat[k])
            e.update(cat="heat_node", fn=FN_K3_NET,
                     how=(f"the RC network's node {k}: one heat capacity {k}_heat_capacity and one resistance to the air "
                          f"{k}_thermal_resistance from design.variables; its loss heats it"
                          + (f" (shared by the {n} parts of kind {k}: one node for all)" if n > 1 else "")
                          + "; its mass is also in the vehicle mass of the drive"))
        elif s:
            e.update(cat="power_stage", fn=FN_K3_CHAIN,
                     how=(f"a stage of the power chain ({s}): power.{s}" + (f" and loss.{s}" if s == "reducer" else "")
                          + f" series with its part_id, shared equally by the {len(stage[s])} parts of the kind; no "
                          "temperature (not a node of the RC network); its mass is in the vehicle mass of the drive"))
        elif items or not out["error"]:
            e.update(cat="mass_only", fn=FN_K3_MASS,
                     how="in the vehicle mass of the drive only (rolling and climbing resistance, acceleration); no heat")
        else:
            e.update(fn=common.FN_MASS_ITEMS, how="not reproduced: " + out["error"])
        out["parts"].append(e)
    out["members"] = {"count": len(design.get("members", [])), "mass_kg": frame_kg, "fn": FN_K3_MASS,
                      "label": "フレームの部材 (質量だけ)", "how": "in the vehicle mass of the drive only"}
    in_mass = sum(e["mass_kg"] for e in out["parts"] if e["cat"] != "not_in")
    _stated(out, record, [("vehicle_mass", in_mass + frame_kg if frame_kg is not None else None)])
    if not out["stated"]:
        out["notes"].append("the record states no vehicle_mass")
    return _finish(out, frame_kg, labels)


# ---------------------------------------------------------------- K1 beams: frame_static, fatigue

def k1_ledger(tool: str, design: Record, load_case: Record | None, record: Record, parts: list[Record]) -> Record:
    """K1 beam frame (frame_static at rest or on the step; fatigue's unit wheel loads with inertia relief): every mass
    item of a part with carried_by is loaded (gravity, or inertia relief) in equal shares on those frame nodes; a part
    without carried_by at its support's wheel centre node; the members are beams with stiffness."""
    out, dparts, on_ids = _base(tool, design, load_case, common.CATEGORIES)
    items, frame_kg, _ = _items(out, design, on_ids)
    fn = FN_STATIC if tool == "frame_static" else FN_FATIGUE
    what = ("its weight (gravity x the case's factor)" if tool == "frame_static" else
            "its d'Alembert force in each unit wheel load case (inertia relief; gravity is inside the wheel forces)")
    nodes_xyz = {n["id"] for n in design.get("nodes", [])}
    supports = {s["id"]: s for s in design.get("supports", [])}
    for p in parts:
        e = _entry(p, dparts.get(p["id"], {}))
        if p["id"] not in on_ids:
            out["parts"].append(_off(e, out["loading"]))
            continue
        it = items.get(p["id"])
        if it is None:
            e.update(fn=common.FN_MASS_ITEMS, how="not reproduced: " + (out["error"] or "no mass item"))
        elif it.carrier == "nodes":
            e.update(cat="frame_nodes", fn=f"{fn}; {FN_K1_BODY}",
                     nodes=[[n, it.mass_kg / len(it.carried_by)] for n in it.carried_by],
                     how=f"a mass item: {what} in equal shares on its carried_by frame nodes, with the moment of its "
                         "offset; no stiffness")
        else:
            sid = it.carried_by[0]
            fnode = supports.get(sid, {}).get("frame_node")
            node = f"wc:{sid}" if sid in supports else fnode
            e.update(cat="unsprung", fn=f"{fn}; {FN_K1_BODY}", nodes=[[node, it.mass_kg]],
                     how=f"no carried_by (nearest support {sid}): {what} at the wheel centre node {node}, joined to frame "
                         f"node {fnode} (hiraku.solvers.k1_frame.model.wheel_node)"
                         + ("; in the fatigue road model also the quarter car's unsprung mass at that support "
                            "(shelf.fatigue.road_wheel_forces)" if tool == "fatigue" else ""))
            if fnode not in nodes_xyz:
                e["nodes"] = [[node, it.mass_kg]]
        out["parts"].append(e)
    out["members"] = {"count": len(design.get("members", [])), "mass_kg": frame_kg, "fn": "hiraku.solvers.k1_frame.model.build",
                      "how": "beams with stiffness; their own weight / inertia as distributed loads"}
    if tool == "frame_static":
        _stated(out, record, [("vehicle_mass", None), ("frame_mass", frame_kg)])
        for s in out["stated"]:
            if s["label"] == "vehicle_mass":
                s["computed_kg"] = (sum(e["mass_kg"] for e in out["parts"] if e["cat"] != "not_in") + frame_kg
                                    if frame_kg is not None else None)
    return _finish(out, frame_kg, common.CATEGORIES)


# ---------------------------------------------------------------- K1 shell with mount loads (the step hit)

def shell_ledger(design: Record, load_case: Record | None, parts: list[Record], meshed: set[str]) -> Record:
    """frame_shell with mount loads (analyze_shell): only the sprung items are loaded (k1_frame.analyze.sprung_items:
    the wheel kinds left out, the suspension kinds at half mass); a part meshed as shell elements (gusset plates,
    meshed brackets: `meshed`, the saved mesh's element labels) is stiffness and carries its own mass; every other
    sprung part is a point mass at its cg, its gravity and d'Alembert force spread onto its carried_by joints (or a
    support's mount nodes)."""
    from hiraku.solvers.k1_frame.analyze import sprung_items

    out, dparts, on_ids = _base("frame_shell", design, load_case, common.CATEGORIES)
    items, frame_kg, its = _items(out, design, on_ids)
    on = {**design, "parts": [p for p in design.get("parts", []) if p["id"] in on_ids]}
    sprung = {it.id.split(":", 1)[1]: it for it in sprung_items(on, its) if it.carrier != "member"}
    for p in parts:
        e = _entry(p, dparts.get(p["id"], {}))
        pid = p["id"]
        if pid not in on_ids:
            out["parts"].append(_off(e, out["loading"]))
            continue
        it = items.get(pid)
        if pid in meshed:
            e.update(cat="stiffness", fn=FN_SHELL, stiffness=True,
                     nodes=[[n, None] for n in (dparts.get(pid, {}).get("carried_by") or [])],
                     how="meshed as shell elements (its plate is in the mesh: stiffness, and its mass on those elements)")
        elif it is None:
            e.update(fn=common.FN_MASS_ITEMS, how="not reproduced: " + (out["error"] or "no mass item"))
        elif pid not in sprung:
            e.update(cat="not_in", fn="hiraku.solvers.k1_frame.analyze.sprung_items",
                     how="unsprung (a wheel kind): left out; the mount forces from K2 carry it")
        else:
            s = sprung[pid]
            half = s.mass_kg < it.mass_kg - 1e-9
            e["mass_kg_in"] = s.mass_kg
            if s.carrier == "nodes":
                e.update(cat="frame_nodes", fn=FN_SHELL, nodes=[[n, s.mass_kg / len(s.carried_by)] for n in s.carried_by],
                         how="a point mass at its cg: its gravity and d'Alembert force (with the moment of its offset) "
                             "spread onto the joint nodes of its carried_by frame nodes; no stiffness"
                             + (" (half its mass: a suspension kind, K2's split)" if half else ""))
            else:
                sid = s.carried_by[0]
                e.update(cat="unsprung", fn=FN_SHELL, nodes=[[(design_support_node(design, sid)), s.mass_kg]],
                         how=f"no carried_by (support {sid}): its force spread onto that support's mount nodes"
                             + (" (half its mass: a suspension kind)" if half else ""))
        out["parts"].append(e)
    for e in out["parts"]:   # the mass table counts what the shell loads
        if "mass_kg_in" in e:
            e["mass_kg_design"], e["mass_kg"] = e["mass_kg"], e.pop("mass_kg_in")
    out["members"] = {"count": len(design.get("members", [])), "mass_kg": frame_kg, "fn": FN_SHELL,
                      "label": "フレームの部材 (シェル要素)", "how": "the tubes meshed as shell elements (MITC4)"}
    out["notes"].append("the through parts at joints the mesh made through are shell elements too (shell_parts); "
                        "this layer cannot see which joints those are and draws them as point masses")
    return _finish(out, frame_kg, common.CATEGORIES)


def design_support_node(design: Record, sid: str) -> str:
    return next((s.get("frame_node") for s in design.get("supports", []) if s["id"] == sid), sid)

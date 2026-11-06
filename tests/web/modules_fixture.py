"""A sample record of a design split into modules (docs/contract.md, モジュールに分けた設計), for the
screen's tests and pictures until the design and analysis departments write real ones.

It is data/designs/offroad-4w-v1.json with, by hand:
- `module` on every part (by its kind and system, as the contract's list of what each module holds)
  and member (part "frame": frame, part "rollbar": occupant);
- `modules`: the packager owns package (the layout variables, no space), designer.<module> the
  others; each module's space (`envelope`) is the box round its parts and members grown by
  ENVELOPE_MARGIN_MM (the suspension one box per corner), `interfaces` a few frame nodes;
- `joint` on the frame's nodes (mostly weld, some bolt and pin);
- two rounds: in each, two or three module designers propose (design.change_proposed, data
  module_change) and the packager builds the next version (design.created with module_changes).
  Version 3 grows the battery past the powertrain's space (one part outside).

Shape example only: the changes, their reasons and the boxes are made up for the screen; none of
it is a design or a calculation. The record passes the contract's schema (test_modules_browser).
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "data" / "designs" / "offroad-4w-v1.json"
RUN_ID = "20261104-210000-modules"
ENVELOPE_MARGIN_MM = 25.0  # as the design department's generator (21ad431): HIRAKU's choice, not from a source
BATTERY_GROW_MM = 160.0  # version 3's battery is this much longer along x (sticks out of its space)

# module by part kind (tie_rod by its system: steering's tie rods, the suspension's toe links)
KIND_MODULE = {
    "bracket": "frame",
    "wheel": "suspension", "suspension": "suspension", "knuckle": "suspension", "brake_caliper": "suspension",
    "link": "suspension", "damper": "suspension", "ball_joint": "suspension",  # the arms (designgen kit/arms.py)
    "pushrod": "steering", "bearing": "steering", "anchor": "frame",  # designgen kit/joints.py's parts
    "steering": "steering", "steering_column": "steering", "steering_shaft": "steering", "steering_rack": "steering",
    "accelerator_pedal": "steering", "brake_pedal": "steering", "brake_master_cylinder": "steering",
    "seat": "occupant", "seatback": "occupant", "head_restraint": "occupant",  # the seat's back and head restraint
    "seat_belt": "occupant", "occupant": "occupant", "rollbar": "occupant",
    "battery": "powertrain", "switch": "powertrain", "motor": "powertrain", "reducer": "powertrain", "driveshaft": "powertrain",
    "controller": "powertrain", "electrical": "powertrain",
    "body": "body", "cargo": "body",
}
MEMBER_MODULE = {"frame": "frame", "rollbar": "occupant"}
MODULE_VARIABLES = {
    "package": ["wheelbase", "track", "rail_spacing", "rail_height", "front_overhang", "rear_overhang"],
    "frame": ["rail_b", "rail_h", "rail_t", "cross_b", "cross_h", "cross_t", "tower_b", "tower_t", "lower_b", "lower_t"],
    "suspension": ["front_spring_rate", "rear_spring_rate", "suspension_travel", "wheel_radius", "wheel_width"],
    "steering": ["steering_x", "steering_column_angle", "steering_wheel_height", "pedal_x", "max_steer_angle"],
    "occupant": ["seat_x", "seat_y", "seat_height", "rollbar_height", "rollbar_width", "rollbar_offset"],
    "powertrain": ["battery_x", "battery_capacity", "gear_ratio", "motor_peak_power", "driveshaft_diameter"],
    "body": ["cargo_height", "cargo_mass"],
}
OWNER = {m: ("packager" if m == "package" else f"designer.{m}") for m in MODULE_VARIABLES}

# the rounds: (agent's module, variable, new value, reason, load cases), then what the packager says
ROUNDS = [
    [("suspension", "front_spring_rate", 30.0, "段差で前の輪の力が大きいので、前のばねを固くして沈み込みを減らす", ["R1-v"]),
     ("powertrain", "battery_x", None, "電池を 20 mm 前へ寄せて、前後の重さの釣り合いを取る", ["S1-v"]),
     ("body", "cargo_height", None, "荷を 15 mm 下げて、重心を下げる", ["S1-v"])],
    [("frame", "rail_t", 3.2, "段差でレールの応力が許容に近いので、板厚を上げる", ["R1-v"]),
     ("powertrain", "battery_capacity", 120.0, "長い上り坂で電池が足りないので、容量を上げる (電池が前後に長くなる)", ["T1-v"])],
]
SHIFT = {"battery_x": 20.0, "cargo_height": -15.0}  # the round's change of these variables (mm)


def _rot(rpy):
    r, p, y = (math.radians(a) for a in rpy)
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr]]


def part_extent(p):
    """The axis-aligned box round a part's (turned) box: (lo, hi)."""
    c, s = p["box"]["center_mm"], p["box"]["size_mm"]
    R = _rot(p["box"].get("rpy_deg") or [0, 0, 0])
    half = [sum(abs(R[i][j]) * s[j] / 2 for j in range(3)) for i in range(3)]
    return [c[i] - half[i] for i in range(3)], [c[i] + half[i] for i in range(3)]


def member_extent(design, m):
    nodes = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
    a, b = nodes[m["node_a"]], nodes[m["node_b"]]
    dims = design["sections"][m["section"]]["dims_mm"]
    r = max(dims.get("b", 0), dims.get("h", 0), dims.get("d", 0)) / 2
    return [min(a[i], b[i]) - r for i in range(3)], [max(a[i], b[i]) + r for i in range(3)]


def _box_round(extents, margin):
    lo = [min(e[0][i] for e in extents) - margin for i in range(3)]
    hi = [max(e[1][i] for e in extents) + margin for i in range(3)]
    return {"center_mm": [round((lo[i] + hi[i]) / 2, 1) for i in range(3)], "size_mm": [round(hi[i] - lo[i], 1) for i in range(3)]}


def _module_of_part(p):
    if p["kind"] == "tie_rod":
        return "suspension" if p.get("system") == "suspension" else "steering"
    return KIND_MODULE[p["kind"]]


def _envelopes(design):
    by = {m: [] for m in MODULE_VARIABLES if m != "package"}
    corner, pt = {}, {}
    wb = design["variables"]["wheelbase"]["value"]
    for p in design["parts"]:
        e = part_extent(p)
        by[p["module"]].append(e)
        if p["module"] == "suspension":
            c = p["box"]["center_mm"]
            corner.setdefault((c[0] > -wb / 2, c[1] > 0), []).append(e)
        if p["module"] == "powertrain":  # two spaces: the battery's, the drive's
            pt.setdefault(p["kind"] == "battery", []).append(e)
    for m in design["members"]:
        by[m["module"]].append(member_extent(design, m))
    out = {m: [_box_round(ex, ENVELOPE_MARGIN_MM)] for m, ex in by.items() if ex}
    out["suspension"] = [_box_round(ex, ENVELOPE_MARGIN_MM) for _, ex in sorted(corner.items())]
    out["powertrain"] = [_box_round(ex, ENVELOPE_MARGIN_MM) for _, ex in sorted(pt.items())]
    return out


def _interfaces(design):
    ids = [n["id"] for n in design["nodes"]]
    return {
        "frame": [{"id": "if-frame-body", "between": ["frame", "body"], "node": ids[0], "via": "bolt"}],
        "suspension": [{"id": "if-susp-frame", "between": ["suspension", "frame"], "node": ids[1], "via": "bolt"}],
        "powertrain": [{"id": "if-pt-frame", "between": ["powertrain", "frame"], "node": ids[2], "via": "bolt"}],
        "occupant": [{"id": "if-occ-frame", "between": ["occupant", "frame"], "node": ids[3], "via": "weld"}],
        "steering": [{"id": "if-steer-susp", "between": ["steering", "suspension"], "point_mm": [0.0, 0.0, 300.0], "via": "ball_joint"}],
    }


def module_design() -> dict:
    """Version 1: the base design with modules, envelopes, interfaces and node joints."""
    d = copy.deepcopy(json.loads(BASE.read_text(encoding="utf-8")))
    d["name"] = d["name"] + " (modules: shape example)"
    for p in d["parts"]:
        p["module"] = _module_of_part(p)
    for m in d["members"]:
        m["module"] = MEMBER_MODULE[m["part"]]
    for i, n in enumerate(d["nodes"]):
        n["joint"] = "bolt" if i % 9 == 4 else "pin" if i % 13 == 6 else "weld"
    env = _envelopes(d)
    intf = _interfaces(d)
    d["modules"] = [
        {"module_id": m, "owner": OWNER[m], "envelope": env.get(m, []), "interfaces": intf.get(m, []), "variables": list(vs),
         **({"choices": ["part_mounts"]} if m == "frame" else {})}
        for m, vs in MODULE_VARIABLES.items()
    ]
    return d


def _apply(design, var, value):
    """The next version's design after one change (the parts it moves, by hand)."""
    old = design["variables"][var]["value"]
    design["variables"][var] = {"value": value, "unit": design["variables"][var]["unit"]}
    if var == "battery_x":
        for p in design["parts"]:
            if p["id"] == "battery":
                p["box"]["center_mm"][0] += value - old
    elif var == "cargo_height":
        for p in design["parts"]:
            if p["id"] == "cargo":
                p["box"]["center_mm"][2] += value - old
    elif var == "battery_capacity":
        for p in design["parts"]:
            if p["id"] == "battery":
                p["box"]["size_mm"][0] += BATTERY_GROW_MM
                p["mass_kg"] = round(p["mass_kg"] * value / old, 1)


def module_events() -> list[dict]:
    """run.started, version 1, two rounds of proposals and the packager's versions 2 and 3, and the end."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    run = copy.deepcopy(src[0])
    run["run_id"] = RUN_ID
    proj = run["data"]
    proj["run_id"] = RUN_ID
    proj["round_limit"] = 2
    proj["agents"] = [{"agent_id": "lead", "role": "lead", "prop": "binder"}, {"agent_id": "packager", "role": "packager", "prop": "spanner"}] + [
        {"agent_id": f"designer.{m}", "role": "designer", "prop": "pencil", "module": m} for m in MODULE_VARIABLES if m != "package"
    ] + [a for a in proj["agents"] if a["agent_id"].startswith("cae.")]
    proj["design_versions"] = [1, 2, 3]
    proj.setdefault("load_cases", [])
    base = {"run_id": RUN_ID, "ts": run["ts"]}
    t = [0.0]

    def ev(kind, agent, data=None, version=None, text=None, dt=0.5):
        t[0] += dt
        e = {**base, "t": round(t[0], 2), "kind": kind, "agent": agent}
        if data is not None:
            e["data"] = data
        if version is not None:
            e["design_version"] = version
        if text:
            e["text"] = text
        return e

    v = module_design()
    v["design_version"], v["parent_version"] = 1, None
    events = [run, ev("design.created", "packager", copy.deepcopy(v), 1)]
    n = 0
    for r, round_ in enumerate(ROUNDS):
        t[0] += 0.5
        nxt = copy.deepcopy(v)
        changes = []
        for module, var, value, reason, lcs in round_:
            n += 1
            old = v["variables"][var]["value"]
            new = round(old + SHIFT[var], 1) if value is None else value
            chg = {"change_id": f"chg-{n:04d}", "module_id": module, "agent_id": f"designer.{module}", "reason": reason,
                   "load_case_ids": lcs, "changes": {var: {"value": new, "unit": v["variables"][var]["unit"]}}}
            changes.append(chg)
            events.append(ev("agent.started", f"designer.{module}", {"agent_id": f"designer.{module}"}, dt=0.3))
            events.append(ev("design.change_proposed", f"designer.{module}", copy.deepcopy(chg), v["design_version"], dt=0.7))
            _apply(nxt, var, new)
        nxt["design_version"] = v["design_version"] + 1
        nxt["parent_version"] = v["design_version"]
        nxt["module_changes"] = copy.deepcopy(changes)
        nxt["change_reason"] = f"{len(changes)} つのモジュールの提案をまとめた (形の見本)"
        events.append(ev("design.created", "packager", copy.deepcopy(nxt), nxt["design_version"], dt=1.5))
        v = nxt
    # an assembly order of the last version, for A7 (three steps; shape example)
    steps = [{"order": i + 1, "part": pid, "direction": "-z", "depends_on": [], "no_interference": True, "tool_clearance": True, "hand_reach": True}
             for i, pid in enumerate(("battery", "seat_R", "seat_L"))]
    ana = {"analysis_id": "ana-0801", "tool": "assembly", "base": "K4", "design_version": v["design_version"], "load_case_id": "H5-order",
           "prediction_ids": ["pred-0801"], "status": "ok", "results": [{"quantity": "assembly_steps", "value": 3.0, "unit": "1"}], "assembly": steps}
    pred = {"prediction_id": "pred-0801", "load_case_id": "H5-order", "design_version": v["design_version"], "quantity": "blocked_parts",
            "test_id": "H5", "domain": "assembly", "agent": "lead", "created_at": run["ts"],
            "formula_id": "assembly.blocked_count", "expression": "n = n_blocked", "value": 0.0, "unit": "1",
            "inputs": {"n_blocked": {"value": 0.0, "unit": "1"}}, "assumptions": []}
    events.append(ev("prediction.recorded", "lead", pred, v["design_version"], dt=0.3))
    events.append(ev("analysis.finished", "lead", ana, v["design_version"], dt=0.5))
    events.append(ev("run.finished", "system", {"status": "stopped", "stop_code": "round_limit", "stop_reason": "形の見本の終わり"}, dt=2.0))
    for i, e in enumerate(events):
        e["seq"] = i
    return events


def outside_parts(design) -> list[str]:
    """Parts and members not inside any one box of their module's space (0.5 mm), as the screen decides."""
    env = {m["module_id"]: m["envelope"] for m in design.get("modules", [])}
    out = []
    things = [(f"part:{p['id']}", p["module"], part_extent(p)) for p in design["parts"]]
    things += [(f"member:{m['id']}", m["module"], member_extent(design, m)) for m in design["members"]]
    for key, mod, (lo, hi) in things:
        if mod == "package":
            continue
        inside = any(all(lo[i] >= b["center_mm"][i] - b["size_mm"][i] / 2 - 0.5 and hi[i] <= b["center_mm"][i] + b["size_mm"][i] / 2 + 0.5 for i in range(3))
                     for b in env.get(mod, []))
        if not inside:
            out.append(key)
    return out

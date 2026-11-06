"""A step-climb run with one moving analysis, for the play=loop tests (web/js/anim_loop.js).

Shape example only: the contract sample's vehicle as a rigid body driving forward at a fixed
speed onto a step recorded in the analysis' scene; the body rises by the step's height while
its wheels go up. The numbers come from that simple picture, not from a solver. After the
analysis, a second prediction and the run's end come later, so a replay that moved on would
show something else.
"""

from __future__ import annotations

import copy
import json

from hiraku.webserve.server import SAMPLE_EVENTS

RUN_ID = "20261106-220000-animloop"
ANA = "ana-0001"
SPEED = 2.5  # m/s (shape example)
DT = 0.1  # s between samples
T_END = 2.0  # s recorded
STEP_X = 2000.0  # mm, the step's edge (shape example)
STEP_H = 100.0  # mm
RISE = (0.9, 1.5)  # s: the body goes up the step between these times (shape example)
T_ARRIVE = 7.0  # s: the analysis.finished event's time


def body_x(t: float) -> float:
    """The body's x (m) at sample time t."""
    return SPEED * t


def body_z(t: float) -> float:
    """The body's z (m) at sample time t: 0 before RISE, the step's height after, linear between."""
    a, b = RISE
    f = min(1.0, max(0.0, (t - a) / (b - a)))
    return STEP_H / 1000 * f


def loop_events():
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    run, design_ev = copy.deepcopy(src[0]), copy.deepcopy(src[1])
    run["run_id"] = RUN_ID
    design = design_ev["data"]
    wheels = {s["id"]: s["position_mm"] for s in design["supports"]}
    lc = {
        "load_case_id": "P1-step", "test_id": "P1", "domain": "offroad",
        "title": "段差越え・形の見本", "params": {"step_height": {"value": STEP_H, "unit": "mm"}},
        "tolerance_rel": 0.1,
    }
    run["data"]["load_cases"] = [lc]
    run["data"]["domain_picks"] = [{"domain": "offroad", "shown_name": "悪路", "reason": "段差を越える (形の見本)"}]
    run["data"]["agents"] = [{"agent_id": "lead", "role": "lead", "prop": "binder"},
                             {"agent_id": "cae.offroad", "role": "cae", "domain": "offroad", "prop": "steering_wheel"}]

    n = round(T_END / DT)
    t_s = [round(i * DT, 6) for i in range(n + 1)]
    ch: dict[str, tuple[str, list[float]]] = {}

    def put(name, unit, v):
        ch.setdefault(name, (unit, []))[1].append(round(float(v), 6))

    for t in t_s:
        x, z = body_x(t), body_z(t)
        put("body_x", "m", x)
        put("body_y", "m", 0.0)
        put("body_z", "m", z)
        put("roll", "deg", 0.0)
        put("pitch", "deg", 0.0)
        put("yaw", "deg", 0.0)
        for wid, p in wheels.items():
            put(f"wheel_load.{wid}", "N", 1000.0)
            for k, v in zip("xyz", (p[0] / 1000 + x, p[1] / 1000, p[2] / 1000 + z)):
                put(f"wheel_center_{k}.{wid}", "m", v)
            for k, v in zip("xyz", (p[0] / 1000 + x, p[1] / 1000, z)):
                put(f"contact_{k}.{wid}", "m", v)
    series = [{"channel": c, "unit": u, "t_s": t_s, "values": v} for c, (u, v) in ch.items()]
    scene = [{"kind": "step", "label": "段差 (形の見本)", "position_mm": [STEP_X + 2500.0, 0.0, STEP_H / 2],
              "size_mm": [5000.0, 2400.0, STEP_H]}]

    def pred(pid):
        return {"prediction_id": pid, "design_version": 1, "load_case_id": "P1-step", "test_id": "P1", "domain": "offroad",
                "agent": "cae.offroad", "quantity": "step_climb_ok", "value": 1, "unit": "1", "formula_id": "step.shape",
                "expression": "shape example", "inputs": {}, "assumptions": ["shape example"], "created_at": run["ts"]}

    ana = {"analysis_id": ANA, "tool": "step_climb_dynamic", "base": "shape example", "design_version": 1,
           "load_case_id": "P1-step", "prediction_ids": ["pred-0001"], "status": "ok",
           "results": [{"quantity": "step_climb_ok", "value": 1, "unit": "1", "where": "shape example"}],
           "series": series, "scene": scene, "wall_time_s": 1.0}
    base = {"run_id": RUN_ID, "ts": run["ts"]}
    timed = [
        (2.0, {"kind": "agent.started", "agent": "cae.offroad", "domain": "offroad", "data": {"agent_id": "cae.offroad"}}),
        (3.0, {"kind": "tool.checked", "agent": "system", "data": {"name": "step_climb_dynamic", "title": "段差を走って越える",
               "domain": "offroad", "base": "shape example", "tests": ["P1"], "origin": "builtin", "usable": True, "checks": []}}),
        (4.0, {"kind": "prediction.recorded", "agent": "cae.offroad", "domain": "offroad", "design_version": 1, "data": pred("pred-0001")}),
        (5.0, {"kind": "analysis.started", "agent": "cae.offroad", "domain": "offroad", "design_version": 1,
               "data": {"analysis_id": ANA, "tool": "step_climb_dynamic", "load_case_id": "P1-step", "prediction_ids": ["pred-0001"]}}),
        (T_ARRIVE, {"kind": "analysis.finished", "agent": "cae.offroad", "domain": "offroad", "design_version": 1, "data": ana}),
        (T_ARRIVE + 1.0, {"kind": "prediction.recorded", "agent": "cae.offroad", "domain": "offroad", "design_version": 1, "data": pred("pred-0002")}),
        (T_ARRIVE + 30.0, {"kind": "run.finished", "agent": "system", "data": {"status": "done"}}),
    ]
    run["t"] = 0.0
    design_ev["t"] = 1.0
    design_ev["run_id"] = RUN_ID
    out = [run, design_ev] + [{**base, **ev, "t": t} for t, ev in timed]
    for i, ev in enumerate(out):
        ev["seq"] = i
    return out

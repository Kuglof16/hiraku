"""A tilt-table run in the shape of the K2 vehicle tool's output, for the screen's tests.

Shape example only: the vehicle is a rigid box on the contract sample's
wheels and the numbers come from that simple picture, not from K2. They are
here so the driving animation, the wheel arrows, the wheel lift and the graph
can be drawn and checked before the K2 tool is merged.

Channel names follow hiraku/solvers/k2_vehicle/record.py on the K2 branch.
"""

from __future__ import annotations

import copy
import json
import math

from hiraku.webserve.server import SAMPLE_EVENTS

G = 9.80665
MASS = 500.0  # kg (shape example)
CG = (-900.0, 0.0, 600.0)  # mm, design frame (shape example)
RATE = 4.0  # deg/s the table turns
DT = 0.1  # s between samples
AFTER = 1.5  # s recorded after the upper wheels lift


def _rot_x(p, phi, pivot_y):
    """Turn point p (mm) about the x-axis line through (y = pivot_y, z = 0)."""
    x, y, z = p
    c, s = math.cos(phi), math.sin(phi)
    dy = y - pivot_y
    return (x, pivot_y + dy * c - z * s, dy * s + z * c)


def tilt_events():
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    run, design_ev = copy.deepcopy(src[0]), copy.deepcopy(src[1])
    design = design_ev["data"]
    wheels = {s["id"]: s["position_mm"] for s in design["supports"]}
    ys = [p[1] for p in wheels.values()]
    track = max(ys) - min(ys)
    pivot = min(ys)  # tipping to the right: the left side goes up
    upper = [i for i, p in wheels.items() if p[1] > 0]
    tip_deg = math.degrees(math.atan(track / 2 / CG[2]))

    lc = {
        "load_case_id": "S1-tilt",
        "test_id": "S1",
        "domain": "handling",
        "title": "傾斜台で倒れる角度 (形の見本)",
        "params": {"direction": "right"},
        "tolerance_rel": 0.1,
    }
    run["data"]["load_cases"] = [lc]
    run["data"]["domain_picks"] = [{"domain": "handling", "shown_name": "操安", "reason": "人が乗って斜面を走る (形の見本)"}]
    run["data"]["agents"] = [{"agent_id": "lead", "role": "lead", "prop": "binder"}, {"agent_id": "cae.handling", "role": "cae", "domain": "handling", "prop": "steering_wheel"}]

    t_s, ch = [], {}

    def put(name, unit, v):
        ch.setdefault(name, (unit, []))[1].append(round(float(v), 6))

    t = 0.0
    lift_t = None
    while True:
        theta_deg = RATE * t
        th = math.radians(theta_deg)
        past = 0.0 if theta_deg <= tip_deg else math.radians(theta_deg - tip_deg) * 3.0
        if past and lift_t is None:
            lift_t = t
        t_s.append(round(t, 6))
        o = _rot_x((0.0, 0.0, 0.0), past, pivot)
        cg = _rot_x(CG, past, pivot)
        put("body_x", "m", o[0] / 1000)
        put("body_y", "m", o[1] / 1000)
        put("body_z", "m", o[2] / 1000)
        put("roll", "deg", math.degrees(past))
        put("pitch", "deg", 0.0)
        put("yaw", "deg", 0.0)
        for k, v in zip("xyz", cg):
            put(f"cg_{k}", "m", v / 1000)
        put("table_angle", "deg", theta_deg)
        n_total = MASS * G * math.cos(th)
        n_up = max(0.0, MASS * G * (math.cos(th) / 2 - CG[2] * math.sin(th) / track)) if not past else 0.0
        for wid, p in wheels.items():
            n = (n_up if wid in upper else n_total - n_up) / 2
            centre = _rot_x(p, past, pivot)
            contact = (centre[0], centre[1], 0.0) if n > 0 else _rot_x((p[0], p[1], 0.0), past, pivot)
            put(f"wheel_load.{wid}", "N", n)
            put(f"wheel_fx.{wid}", "N", 0.0)
            put(f"wheel_fy.{wid}", "N", n * math.tan(th))
            put(f"wheel_steer.{wid}", "deg", 0.0)
            put(f"wheel_spin.{wid}", "deg", 0.0)
            for k, v in zip("xyz", centre):
                put(f"wheel_center_{k}.{wid}", "m", v / 1000)
            for k, v in zip("xyz", contact):
                put(f"contact_{k}.{wid}", "m", v / 1000)
            for k, v in zip("xyz", (0.0, 0.0, n)):
                put(f"reaction_{k}.{wid}", "N", v)
            for k, v in zip("xyz", (0.0, n * math.tan(th), 0.0)):
                put(f"grip_{k}.{wid}", "N", v)
        if lift_t is not None and t >= lift_t + AFTER:
            break
        t = round(t + DT, 6)

    series = [{"channel": n, "unit": u, "t_s": t_s, "values": v} for n, (u, v) in ch.items()]
    base = {"run_id": run["run_id"], "ts": run["ts"]}
    pred = {
        "prediction_id": "pred-0001", "design_version": 1, "load_case_id": "S1-tilt", "test_id": "S1", "domain": "handling",
        "agent": "cae.handling", "quantity": "tilt_angle", "value": tip_deg, "unit": "deg", "formula_id": "tilt.rigid_ssf",
        "expression": "theta = atan(t / 2h)", "inputs": {"t": {"value": track, "unit": "mm"}, "h": {"value": CG[2], "unit": "mm"}},
        "assumptions": ["rigid body (shape example)"], "created_at": run["ts"],
    }
    ana = {
        "analysis_id": "ana-0001", "tool": "tilt", "base": "K2", "design_version": 1, "load_case_id": "S1-tilt",
        "prediction_ids": ["pred-0001"], "status": "ok",
        "results": [{"quantity": "tilt_angle", "value": tip_deg, "unit": "deg", "where": "tipping to the right"}],
        "series": series, "wall_time_s": 1.0,
    }
    events = [
        run,
        design_ev,
        {"kind": "agent.started", "agent": "cae.handling", "domain": "handling", "data": {"agent_id": "cae.handling"}},
        {"kind": "tool.checked", "agent": "system", "data": {"name": "tilt", "title": "傾斜台 (K2 で台をゆっくり傾ける)", "domain": "handling", "base": "K2", "tests": ["S1"], "origin": "builtin", "usable": True, "checks": []}},
        {"kind": "prediction.recorded", "agent": "cae.handling", "domain": "handling", "design_version": 1, "data": pred},
        {"kind": "analysis.started", "agent": "cae.handling", "domain": "handling", "design_version": 1,
         "data": {"analysis_id": "ana-0001", "tool": "tilt", "load_case_id": "S1-tilt", "prediction_ids": ["pred-0001"]}},
        {"kind": "analysis.finished", "agent": "cae.handling", "domain": "handling", "design_version": 1, "data": ana},
        {"kind": "run.finished", "agent": "system", "data": {"status": "done"}},
    ]
    out = []
    for i, ev in enumerate(events):
        if i >= 2:
            ev = {**base, **ev, "t": 1.0 + i}
        ev["seq"] = i
        out.append(ev)
    return out, {"tip_deg": tip_deg, "lift_t": lift_t, "upper": upper, "t_end": t_s[-1]}

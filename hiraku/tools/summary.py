"""Plain sums and distances read off a design, so hand-calculation inputs can
be taken from a tool instead of being worked out by the model. No physics:
masses are added, the centre of mass is the mass-weighted box centre, lengths
are distances between points."""

from __future__ import annotations

import math
from typing import Any

Record = dict[str, Any]


def _q(value: float, unit: str) -> Record:
    return {"value": float(value), "unit": unit}


def design_summary(design: Record) -> Record:
    parts = design.get("parts", [])
    mass = sum(p["mass_kg"] for p in parts)
    out: Record = {"mass": _q(mass, "kg")}
    if mass > 0:
        for i, axis in enumerate("xyz"):
            c = sum(p["mass_kg"] * p["box"]["center_mm"][i] for p in parts) / mass
            out[f"cg_{axis}"] = _q(c, "mm")
    wheels = [s for s in design.get("supports", []) if s["kind"] == "wheel"]
    if wheels:
        xs = [s["position_mm"][0] for s in wheels]
        front = [s for s in wheels if s["position_mm"][0] == min(xs)]
        rear = [s for s in wheels if s["position_mm"][0] == max(xs)]
        out["wheelbase"] = _q(max(xs) - min(xs), "mm")
        out["front_wheels"] = _q(len(front), "1")
        out["rear_wheels"] = _q(len(rear), "1")
        if len(front) >= 2:
            ys = [s["position_mm"][1] for s in front]
            out["track_front"] = _q(max(ys) - min(ys), "mm")
        if len(rear) >= 2:
            ys = [s["position_mm"][1] for s in rear]
            out["track_rear"] = _q(max(ys) - min(ys), "mm")
    nodes = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    out["member_length"] = {
        m["id"]: _q(math.dist(nodes[m["node_a"]], nodes[m["node_b"]]), "mm")
        for m in design.get("members", []) if m["node_a"] in nodes and m["node_b"] in nodes
    }
    return out

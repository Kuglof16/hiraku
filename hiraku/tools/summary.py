"""Plain sums and distances read off a design, so hand-calculation inputs can
be taken from a tool instead of being worked out by the model. No physics:
masses are added (parts, and each frame member as density * area * length),
weight is mass times standard gravity, the centre of mass is the
mass-weighted centre, lengths are distances.

Axes are ISO 8855 with the origin under the front axle: x points forward,
so the front axle is the wheel row with the largest x and the rear axle sits
at x = -wheelbase.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from hiraku.catalog.loading import parts_on_board
from hiraku.contract.protocols import LOADINGS

Record = dict[str, Any]

G = 9.80665  # standard gravity in m/s^2 (exact by definition, 3rd CGPM 1901)


def _q(value: float, unit: str) -> Record:
    return {"value": float(value), "unit": unit}


def section_area(section: Record) -> float | None:
    """Cross-section area in mm^2 from the section's dims (or its A_mm2)."""
    if "A_mm2" in section:
        return float(section["A_mm2"])
    d = section["dims_mm"]
    shape = section["shape"]
    try:
        if shape == "rect_tube":
            return d["b"] * d["h"] - (d["b"] - 2 * d["t"]) * (d["h"] - 2 * d["t"])
        if shape == "round_tube":
            return math.pi / 4.0 * (d["d"] ** 2 - (d["d"] - 2 * d["t"]) ** 2)
        if shape == "rect_solid":
            return d["b"] * d["h"]
        if shape == "round_solid":
            return math.pi / 4.0 * d["d"] ** 2
    except KeyError:
        return None
    return None


def frame_masses(design: Record) -> list[tuple[str, float, list[float]]]:
    """(member id, mass kg, centre mm) of every member whose mass can be summed."""
    nodes = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    out = []
    for m in design.get("members", []):
        sec = design.get("sections", {}).get(m["section"])
        mat = design.get("materials", {}).get(m["material"])
        if sec is None or mat is None or m["node_a"] not in nodes or m["node_b"] not in nodes:
            continue
        area = section_area(sec)
        if area is None:
            continue
        a, b = nodes[m["node_a"]], nodes[m["node_b"]]
        mass = mat["density_kg_m3"] * area * math.dist(a, b) * 1e-9  # mm^3 -> m^3
        out.append((m["id"], mass, [(x + y) / 2.0 for x, y in zip(a, b)]))
    return out


def mass_summary(items: list[tuple[float, list[float]]], front_axle_x: float | None) -> Record:
    mass = sum(m for m, _ in items)
    out: Record = {"mass": _q(mass, "kg"), "weight": _q(mass * G, "N")}
    if mass > 0:
        for i, axis in enumerate("xyz"):
            out[f"cg_{axis}"] = _q(sum(m * c[i] for m, c in items) / mass, "mm")
        if front_axle_x is not None:
            out["cg_from_front_axle"] = _q(front_axle_x - out["cg_x"]["value"], "mm")
    return out


def rail_section(design: Record) -> Record | None:
    """The side rails: dims_mm of the section most used by members running along x,
    and z, the mean height of those members' end nodes (the rail centre line)."""
    nodes = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    along_x: Counter = Counter()
    ends: dict[str, list[float]] = {}
    for m in design.get("members", []):
        if m["node_a"] in nodes and m["node_b"] in nodes:
            a, b = nodes[m["node_a"]], nodes[m["node_b"]]
            dx, dy, dz = (abs(p - q) for p, q in zip(a, b))
            if dx > max(dy, dz):
                along_x[m["section"]] += 1
                ends.setdefault(m["section"], []).extend([a[2], b[2]])
    if not along_x:
        return None
    key = along_x.most_common(1)[0][0]
    sec = design["sections"].get(key)
    if sec is None:
        return None
    out = {name: _q(v, "mm") for name, v in sec["dims_mm"].items()}
    out["z"] = _q(sum(ends[key]) / len(ends[key]), "mm")
    out["section"] = key
    return out


def design_summary(design: Record) -> Record:
    """Geometry, member lengths and, per loading, the mass and centre of mass of
    the frame plus the parts on board."""
    out: Record = {}
    front_x = None
    wheels = [s for s in design.get("supports", []) if s["kind"] == "wheel"]
    if wheels:
        xs = [s["position_mm"][0] for s in wheels]
        front_x = max(xs)
        front = [s for s in wheels if s["position_mm"][0] == front_x]
        rear = [s for s in wheels if s["position_mm"][0] == min(xs)]
        out["wheelbase"] = _q(max(xs) - min(xs), "mm")
        out["front_axle_x"] = _q(front_x, "mm")
        out["front_wheels"] = _q(len(front), "1")
        out["rear_wheels"] = _q(len(rear), "1")
        out["front_wheel_radius"] = _q(min(s["radius_mm"] for s in front), "mm")
        out["rear_wheel_radius"] = _q(min(s["radius_mm"] for s in rear), "mm")
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
    rail = rail_section(design)
    if rail is not None:
        out["rail_section"] = rail
    frame = frame_masses(design)
    out["frame_mass"] = _q(sum(m for _, m, _ in frame), "kg")
    frame_items = [(m, c) for _, m, c in frame]
    out["loadings"] = {
        name: mass_summary(frame_items + [(p["mass_kg"], p["box"]["center_mm"]) for p in parts_on_board(design, name)],
                           front_x)
        for name in LOADINGS
    }
    return out

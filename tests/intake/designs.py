"""A small design written for these tests (not the demo vehicle, whose numbers belong to the analysis department)."""

from __future__ import annotations

import copy

from hiraku import contract


def box(center, size):
    return {"center_mm": list(center), "size_mm": list(size)}


def mini_design(version: int = 1):
    """Two supports with springs, a heavy battery, a 20 mm seat, a floor plate and a two-member frame."""
    d = {
        "design_version": version,
        "parent_version": None,
        "name": "mini",
        "variables": {"track": {"value": 1000.0, "unit": "mm"}, "battery_voltage": {"value": 51.2, "unit": "V"}},
        "supports": [
            {"id": "FL", "kind": "wheel", "position_mm": [0, 500, 300], "radius_mm": 300, "width_mm": 200,
             "suspension": {"type": "spring", "stiffness_N_per_mm": 20.0, "travel_mm": 150.0},
             "actuation": {"drive": False, "brake": True, "steer": True}},
            {"id": "FR", "kind": "wheel", "position_mm": [0, -500, 300], "radius_mm": 300,
             "suspension": {"type": "rigid"}, "actuation": {"drive": False, "brake": True, "steer": True}},
        ],
        "parts": [
            {"id": "battery", "name": "電池", "kind": "battery", "box": box((-700, 0, 330), (500, 600, 200)), "mass_kg": 50.0},
            {"id": "seat", "name": "座席", "kind": "seat", "box": box((-500, 0, 500), (500, 450, 260)), "mass_kg": 12.0},
            {"id": "floor", "name": "床", "kind": "body", "box": box((-500, 0, 440), (550, 1100, 20)), "mass_kg": 8.0},
            {"id": "cargo", "name": "荷", "kind": "cargo", "box": box((-900, 0, 700), (900, 880, 300)), "mass_kg": 150.0},
        ],
        "nodes": [{"id": "A", "xyz_mm": [0.0, 0.0, 0.0]}, {"id": "B", "xyz_mm": [0.0, 0.0, 1000.0]},
                  {"id": "C", "xyz_mm": [300.0, 0.0, 1000.0]}],
        "members": [
            {"id": "m1", "node_a": "A", "node_b": "B", "section": "rt", "material": "steel", "part": "frame"},
            {"id": "m2", "node_a": "B", "node_b": "C", "section": "rt2", "material": "steel", "part": "frame"},
        ],
        "sections": {"rt": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.3}},
                     "rt2": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 1.6}}},
        "materials": {"steel": {"name": "steel", "E_MPa": 205000, "nu": 0.3, "density_kg_m3": 7850, "yield_MPa": 235,
                                "source": "test", "verified": False}},
    }
    contract.validate("design", d)
    return d


def requirements(**values):
    """requirement entries for the numbers a rule may use: requirements(Q15=100, Q19=40)."""
    return [{"key": k, "name": k, "value": v, "source": "default", "trust": 3} for k, v in values.items()]


def changed(design, **parts_mass):
    d = copy.deepcopy(design)
    for p in d["parts"]:
        if p["id"] in parts_mass:
            p["mass_kg"] = parts_mass[p["id"]]
    return d

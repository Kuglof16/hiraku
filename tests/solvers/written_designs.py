"""A small design written by hand, as Claude writes one with write_design: no archetype, no variables, no
occupants. A two-shelf serving cart on four small wheels (front driven, rear casters), 20 x 20 x 1.6 mm square
tube. Its sizes and the steel's values are this test's assumptions, not checked against a source."""

from __future__ import annotations

import json

_X = {"F": 0.0, "R": -450.0}  # x forward, the origin under the front axle
_Y = {"L": 180.0, "R": -180.0}  # y to the left
_Z = {"base": 70.0, "shelf": 380.0, "top": 800.0}
WHEEL_R_MM = 45.0


def serving_cart() -> dict:
    nodes, members = [], []
    corners = [c + s for c in _X for s in _Y]
    for c, x in _X.items():
        for s, y in _Y.items():
            for lvl, z in _Z.items():
                nodes.append({"id": f"{lvl}_{c}{s}", "xyz_mm": [x, y, z], "joint": "weld"})
            members += [{"id": f"post_a_{c}{s}", "node_a": f"base_{c}{s}", "node_b": f"shelf_{c}{s}",
                         "section": "sq20", "material": "steel", "name": f"post {c}{s} lower"},
                        {"id": f"post_b_{c}{s}", "node_a": f"shelf_{c}{s}", "node_b": f"top_{c}{s}",
                         "section": "sq20", "material": "steel", "name": f"post {c}{s} upper"}]
    for lvl in _Z:
        for a, b in (("FL", "FR"), ("RL", "RR"), ("FL", "RL"), ("FR", "RR")):
            members.append({"id": f"ring_{lvl}_{a}{b}", "node_a": f"{lvl}_{a}", "node_b": f"{lvl}_{b}",
                            "section": "sq20", "material": "steel", "name": f"{lvl} ring {a}-{b}"})
    supports = [{"id": f"w_{c}{s}", "kind": "wheel" if c == "F" else "caster",
                 "position_mm": [x, y, WHEEL_R_MM], "radius_mm": WHEEL_R_MM, "width_mm": 22.0,
                 "suspension": {"type": "rigid"}, "actuation": {"drive": c == "F", "brake": c == "F", "steer": False},
                 "frame_node": f"base_{c}{s}"}
                for c, x in _X.items() for s, y in _Y.items()]
    mid = _X["R"] / 2.0

    def part(pid, kind, z, size, kg, lvl):
        return {"id": pid, "name": pid, "kind": kind, "box": {"center_mm": [mid, 0.0, z], "size_mm": size},
                "mass_kg": kg, "carried_by": [f"{lvl}_{k}" for k in corners]}

    parts = [part("tray", "body", _Z["top"] + 10, [470, 380, 2], 1.2, "top"),
             part("plates", "cargo", _Z["top"] + 60, [380, 300, 90], 8.0, "top"),
             part("battery", "battery", _Z["base"] + 35, [180, 120, 50], 2.5, "base")]
    return {"design_version": 1, "parent_version": None, "name": "serving cart (test)", "change_reason": "test",
            "written_by": "claude", "variables": {}, "supports": supports, "parts": parts, "nodes": nodes,
            "members": members,
            "sections": {"sq20": {"shape": "rect_tube", "dims_mm": {"b": 20.0, "h": 20.0, "t": 1.6}}},
            "materials": {"steel": {"name": "carbon steel tube (test)", "E_MPa": 205000.0, "nu": 0.3,
                                    "density_kg_m3": 7850.0, "yield_MPa": 245.0, "source": "test assumption",
                                    "verified": False}}}


def demo_design() -> dict:
    """The demo vehicle's design version 1 (data/designs)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    with (root / "data" / "designs" / "offroad-4w-v1.json").open(encoding="utf-8") as f:
        return json.load(f)

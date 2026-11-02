"""Small designs with closed-form answers, used by the tools' known-answer checks."""

from __future__ import annotations

STEEL = {"name": "steel for checks", "E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 7850.0,
         "yield_MPa": 245.0, "source": "round numbers for known-answer checks", "verified": True}


def beam_design(length: float, section: dict, material: dict = STEEL) -> dict:
    """One member from A (origin) to B along +x."""
    return {
        "design_version": 1, "name": "check beam", "variables": {}, "supports": [], "parts": [],
        "nodes": [{"id": "A", "xyz_mm": [0.0, 0.0, 0.0]}, {"id": "B", "xyz_mm": [length, 0.0, 0.0]}],
        "members": [{"id": "m", "node_a": "A", "node_b": "B", "section": "s", "material": "mat"}],
        "sections": {"s": section},
        "materials": {"mat": material},
    }


def ladder_design(wheelbase: float = 2000.0, rail_y: float = 400.0, wheel_y: float = 600.0,
                  rail_z: float = 400.0, radius: float = 300.0, payload_kg: float = 200.0,
                  payload_x: float | None = None) -> dict:
    """A symmetric ladder on four rigid wheels, rear drive, payload on the middle cross member."""
    L = wheelbase
    xm = -L / 2.0
    nodes = []
    for side, y in (("L", rail_y), ("R", -rail_y)):
        for st, x in (("0", 0.0), ("1", xm), ("2", -L)):
            nodes.append({"id": side + st, "xyz_mm": [x, y, rail_z]})
    sec = "rt"
    members = []
    for side in "LR":
        members.append({"id": f"rail{side}a", "node_a": side + "0", "node_b": side + "1", "section": sec, "material": "st"})
        members.append({"id": f"rail{side}b", "node_a": side + "1", "node_b": side + "2", "section": sec, "material": "st"})
    for st in "012":
        members.append({"id": f"cross{st}", "node_a": "L" + st, "node_b": "R" + st, "section": sec, "material": "st"})

    def wheel(sid, x, y, front):
        return {"id": sid, "kind": "wheel", "position_mm": [x, y, radius], "radius_mm": radius,
                "suspension": {"type": "rigid"},
                "actuation": {"drive": not front, "brake": True, "steer": front},
                "frame_node": ("L" if y > 0 else "R") + ("0" if front else "2")}

    px = xm if payload_x is None else payload_x
    return {
        "design_version": 1, "name": "check ladder", "variables": {},
        "supports": [wheel("FL", 0.0, wheel_y, True), wheel("FR", 0.0, -wheel_y, True),
                     wheel("RL", -L, wheel_y, False), wheel("RR", -L, -wheel_y, False)],
        "parts": [{"id": "payload", "name": "payload", "kind": "cargo",
                   "box": {"center_mm": [px, 0.0, rail_z], "size_mm": [100.0, 100.0, 100.0]},
                   "mass_kg": payload_kg, "carried_by": ["L1", "R1"]}],
        "nodes": nodes,
        "members": members,
        "sections": {sec: {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.0}}},
        "materials": {"st": STEEL},
    }

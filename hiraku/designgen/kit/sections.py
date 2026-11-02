"""Section records from their dimensions, and the table of their properties.

    key, sec = rect_tube(40, 80, 2.3)     # ("rt40x80x2.3", {"shape": "rect_tube", "dims_mm": {...}})
    table({key: sec})                     # {key: {"A_mm2", "Iy_mm4", "Iz_mm4", "J_mm4"}} (K0 section formulas)
"""

from __future__ import annotations

from typing import Any

Record = dict[str, Any]


def rect_tube(b: float, h: float, t: float) -> tuple[str, Record]:
    """b across (y for a member along x), h up, wall t (mm)."""
    return f"rt{b:g}x{h:g}x{t:g}", {"shape": "rect_tube", "dims_mm": {"b": float(b), "h": float(h), "t": float(t)}}


def round_tube(d: float, t: float) -> tuple[str, Record]:
    return f"ct{d:g}x{t:g}", {"shape": "round_tube", "dims_mm": {"d": float(d), "t": float(t)}}


def rect_solid(b: float, h: float) -> tuple[str, Record]:
    return f"rs{b:g}x{h:g}", {"shape": "rect_solid", "dims_mm": {"b": float(b), "h": float(h)}}


def table(sections: dict[str, Record]) -> dict[str, Record]:
    """Area, second moments and torsion constant of each section (mm^2, mm^4), from the K0 section formulas."""
    from hiraku.solvers.k0_handcalc.sections import properties

    out = {}
    for key, sec in sections.items():
        p = properties(sec)
        out[key] = {"A_mm2": p["A"], "Iy_mm4": p["Iy"], "Iz_mm4": p["Iz"], "J_mm4": p["J"]}
    return out

"""Section records from their dimensions, and the table of their properties.

    key, sec = rect_tube(40, 80, 2.3)     # ("rt40x80x2.3", {"shape": "rect_tube", "dims_mm": {...}})
    round_tube, rect_solid, i_beam, channel, angle   # the other shapes K0 computes
    table({key: sec})                     # {key: {"A_mm2", "Iy_mm4", "Iz_mm4", "J_mm4"}} (K0 section formulas)
    pick("rect_tube", b=40, h=80, t_min=2.0)          # the lightest JIS standard size that fits
    pick("round_tube", need={"Iz_mm4": 5e4})          # the lightest standard round tube with that stiffness
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

Record = dict[str, Any]


def rect_tube(b: float, h: float, t: float) -> tuple[str, Record]:
    """b across (y for a member along x), h up, wall t (mm)."""
    return f"rt{b:g}x{h:g}x{t:g}", {"shape": "rect_tube", "dims_mm": {"b": float(b), "h": float(h), "t": float(t)}}


def round_tube(d: float, t: float) -> tuple[str, Record]:
    return f"ct{d:g}x{t:g}", {"shape": "round_tube", "dims_mm": {"d": float(d), "t": float(t)}}


def rect_solid(b: float, h: float) -> tuple[str, Record]:
    return f"rs{b:g}x{h:g}", {"shape": "rect_solid", "dims_mm": {"b": float(b), "h": float(h)}}


def i_beam(h: float, b: float, tw: float, tf: float) -> tuple[str, Record]:
    """h up, flanges b across, web tw, flanges tf (mm)."""
    return f"ib{h:g}x{b:g}x{tw:g}x{tf:g}", {"shape": "i_beam", "dims_mm": {"h": float(h), "b": float(b),
                                                                             "tw": float(tw), "tf": float(tf)}}


def channel(h: float, b: float, tw: float, tf: float) -> tuple[str, Record]:
    """h up (the web), flanges b toward +z, web tw, flanges tf (mm)."""
    return f"ch{h:g}x{b:g}x{tw:g}x{tf:g}", {"shape": "channel", "dims_mm": {"h": float(h), "b": float(b),
                                                                              "tw": float(tw), "tf": float(tf)}}


def angle(h: float, b: float, t: float) -> tuple[str, Record]:
    """Legs h (up) and b (across), both t thick (mm)."""
    return f"an{h:g}x{b:g}x{t:g}", {"shape": "angle", "dims_mm": {"h": float(h), "b": float(b), "t": float(t)}}


STANDARD_SIZES = Path(__file__).resolve().parents[3] / "data" / "sections" / "standard_tubes.json"


def standard(shape: str) -> list[tuple[str, Record]]:
    """The standard sizes of a tube shape (data/sections/standard_tubes.json: rect_tube from
    JIS G 3466, round_tube from JIS G 3444; its source block says where each list was read; both
    lists checked against JIS G 3466 Tables 5 and 6 and JIS G 3444 Table 6 on 2026-11-03, README,
    the table of values checked),
    as section records with the list's source; a rect_tube size A x B is taken with b = B
    across and h = A up (b <= h)."""
    data = json.loads(STANDARD_SIZES.read_text(encoding="utf-8"))
    if shape not in data:
        raise KeyError(f"no standard sizes for {shape!r}; there are {', '.join(sorted(data))}")
    block = data[shape]
    out = []
    for size in block["sizes"]:
        if shape == "rect_tube":
            a, b_, t = size
            key, sec = rect_tube(min(a, b_), max(a, b_), t)
        else:
            key, sec = round_tube(*size)
        out.append((key, dict(sec, source=f"{block['standard']} standard size ({block['source']})")))
    return out


def pick(shape: str, *, b: float | None = None, h: float | None = None, d: float | None = None,
         t_min: float = 0.0, need: dict[str, float] | None = None) -> tuple[str, Record]:
    """The lightest standard size of a tube shape (by area) that has the outer size asked for
    (b across and h up, a standard A x B either way round, or d; any when not given), a wall of
    at least t_min (mm) and at least the properties in need ({"A_mm2" | "Iy_mm4" | "Iz_mm4" |
    "J_mm4": value}). A rect_tube without b and h may be turned (b > h) to meet Iy and Iz.
    Raises ValueError when no standard size fits."""
    need = need or {}

    def fits(p: dict) -> bool:
        return all(p[k] >= v for k, v in need.items())

    best = None
    for key, sec in standard(shape):
        dims = sec["dims_mm"]
        if dims["t"] < t_min:
            continue
        if shape == "round_tube" and d is not None and dims["d"] != float(d):
            continue
        cands = [(key, sec)]
        if shape == "rect_tube":
            turned = rect_tube(dims["h"], dims["b"], dims["t"])
            if b is not None and h is not None:
                cands = [(k, s) for k, s in (cands[0], turned)
                         if (s["dims_mm"]["b"], s["dims_mm"]["h"]) == (float(b), float(h))]
            elif dims["b"] != dims["h"]:
                cands.append(turned)
        for k, s in cands:
            s = dict(s, source=sec["source"])
            p = table({k: s})[k]
            if fits(p) and (best is None or p["A_mm2"] < best[2]):
                best = (k, s, p["A_mm2"])
    if best is None:
        raise ValueError(f"no standard {shape} with b={b}, h={h}, d={d}, t >= {t_min} mm and {need}")
    return best[0], best[1]


def table(sections: dict[str, Record]) -> dict[str, Record]:
    """Area, second moments and torsion constant of each section (mm^2, mm^4), from the K0 section formulas."""
    from hiraku.solvers.k0_handcalc.sections import properties

    out = {}
    for key, sec in sections.items():
        p = properties(sec)
        out[key] = {"A_mm2": p["A"], "Iy_mm4": p["Iy"], "Iz_mm4": p["Iz"], "J_mm4": p["J"]}
    return out

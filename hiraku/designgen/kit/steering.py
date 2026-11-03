"""Steering (steering wheel -> column -> intermediate shaft -> rack -> tie rods -> knuckles) and the links at each
corner (a knuckle on every wheel, a toe link on every wheel that does not steer).

    steering.place(lay, total_mass=12.0, wheel_x=-400.0, driver_y=-300.0, pinion_y=-200.0, knee_top=990.0, rho=7850.0)
    steering.place_corners(lay, rho=7850.0)

Where the parts go:
- the rack runs between the tie rods' inner joints of the steered wheels and each tie rod from
  its inner to its outer joint (K2 hard points tie_inner, tie_outer of the support);
- the pinion is on the rack's line at pinion_y (kept PINION_END_MM in from its ends); the steering
  wheel's centre is at wheel_x, on the driver's middle line; the column runs forward and down
  from it at COLUMN_ANGLE_DEG, the wheel square to it, high enough that the rim's lowest point is
  KNEE_GAP_MM above the top of the large driver's knee; the column ends above the pinion (at its
  x, at least COLUMN_MIN_MM long) and an intermediate shaft runs from there to the pinion;
- a knuckle is the box around the outer joints of the wheel's links (lca_outer, uca_outer,
  tie_outer) and KNUCKLE_PAD_MM beyond them; a wheel that does not steer has a toe link from
  tie_inner to tie_outer, like a tie rod.

Masses: tie rods and toe links are steel bars from joint to joint plus two ball joints of
ROD_END_KG; the rest of the steering's total is split WHEEL_SHARE to the wheel, COLUMN_SHARE to
the column, SHAFT_SHARE to the intermediate shaft and the remainder to the rack. A knuckle (KNUCKLE_KG) and a toe link come out of their
corner's suspension part.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from hiraku.designgen.kit.links import put_link
from hiraku.designgen.kit.package import Item, Layout

Record = dict[str, Any]

STEERING_WHEEL_DIAMETER_MM = 350.0  # assumption
STEERING_WHEEL_THICKNESS_MM = 40.0  # rim and hub along the column (assumption)
COLUMN_RADIUS_MM = 20.0  # column and its jacket (assumption)
RACK_RADIUS_MM = 25.0  # rack housing (assumption)
TIE_ROD_RADIUS_MM = 8.0  # tie rods and toe links, solid bars (assumption)
ROD_END_KG = 0.25  # one ball joint at the end of a tie rod or toe link (assumption)
WHEEL_SHARE = 0.25  # of the steering without the tie rods (assumption)
COLUMN_SHARE = 0.2  # of the steering without the tie rods (assumption)
SHAFT_SHARE = 0.1  # of the steering without the tie rods (assumption)
SHAFT_RADIUS_MM = 12.0  # intermediate shaft with its universal joints (assumption)
COLUMN_ANGLE_DEG = 35.0  # the column below the horizontal, forward and down from the wheel (assumption)
COLUMN_MIN_MM = 150.0  # the column at least this long (assumption)
KNEE_GAP_MM = 50.0  # the steering wheel's rim above the top of the large driver's knee (assumption)
KNUCKLE_KG = 2.5  # one knuckle with its hub bearing (assumption)
KNUCKLE_PAD_MM = 20.0  # the knuckle's box this far beyond its joints (assumption)
PINION_END_MM = 30.0  # the pinion at least this far in from the rack's ends (assumption)


def _hp(design: Record, sid: str) -> Record:
    from hiraku.solvers.k2_vehicle.model import hardpoints_of

    return hardpoints_of(design, sid)


def steered(design: Record) -> list[Record]:
    return [s for s in design.get("supports", []) if s["actuation"]["steer"]]


def rack_ends(design: Record) -> tuple[list[float], list[float]]:
    """The tie rods' inner joints of the steered wheels, right (-y) first."""
    pts = sorted((_hp(design, s["id"])["tie_inner"] for s in steered(design)), key=lambda p: p[1])
    if len(pts) != 2:
        raise ValueError(f"a rack needs two steered wheels, the design has {len(pts)}")
    return [float(v) for v in pts[0]], [float(v) for v in pts[-1]]


def pinion(design: Record, y: float) -> np.ndarray:
    """The point on the rack's line at y."""
    a, b = (np.asarray(p, float) for p in rack_ends(design))
    if not min(a[1], b[1]) <= y <= max(a[1], b[1]):
        raise ValueError(f"the pinion at y = {y:g} mm is beyond the rack's ends ({a[1]:g} to {b[1]:g} mm)")
    return a + (b - a) * (y - a[1]) / (b[1] - a[1])


def _axis(part: Record) -> tuple[np.ndarray, np.ndarray]:
    """(upper end, lower end) of a link part's axis, from its box."""
    from hiraku.solvers.k4_geometry.capsule import from_obb
    from hiraku.solvers.k4_geometry.obb import from_box

    cap = from_obb(from_box(part["box"]["center_mm"], part["box"]["size_mm"], part["box"].get("rpy_deg")))
    return (cap.a, cap.b) if cap.a[2] >= cap.b[2] else (cap.b, cap.a)


def column_ends(design: Record, part: Record) -> tuple[list[float], list[float]]:
    """(steering wheel centre, lower end) of the column: the centre of the wheel nearest to its upper end."""
    top, low = _axis(part)
    wheels = [p for p in design["parts"] if p["kind"] == "steering_wheel"]
    w = min(wheels, key=lambda p: float(np.linalg.norm(np.asarray(p["box"]["center_mm"], float) - top)))
    return [float(v) for v in w["box"]["center_mm"]], low.tolist()


def shaft_ends(design: Record, part: Record) -> tuple[list[float], list[float]]:
    """(the column's lower end, the pinion) of the intermediate shaft: the lower end of the column nearest
    to the shaft's upper end, and the point of the rack's line nearest to the shaft's lower end."""
    top, low = _axis(part)
    cols = [_axis(p)[1] for p in design["parts"] if p["kind"] == "steering_column"]
    joint = min(cols, key=lambda p: float(np.linalg.norm(p - top)))
    a, b = (np.asarray(p, float) for p in rack_ends(design))
    t = float(np.clip((low - a) @ (b - a) / ((b - a) @ (b - a)), 0.0, 1.0))
    return joint.tolist(), (a + t * (b - a)).tolist()


def bar_mass(length_mm: float, radius_mm: float, rho: float) -> float:
    return rho * math.pi * radius_mm ** 2 * length_mm * 1e-9


def column_direction() -> np.ndarray:
    """Unit vector down the column from the wheel: forward and down at COLUMN_ANGLE_DEG."""
    a = math.radians(COLUMN_ANGLE_DEG)
    return np.array([math.cos(a), 0.0, -math.sin(a)])


def wheel_centre(wheel_x: float, driver_y: float, knee_top: float) -> np.ndarray:
    """The steering wheel's centre: the wheel square to the column, its rim's lowest point KNEE_GAP_MM
    above knee_top."""
    u = column_direction()
    r = STEERING_WHEEL_DIAMETER_MM / 2.0
    return np.array([wheel_x, driver_y, knee_top + KNEE_GAP_MM + r * math.sqrt(1.0 - u[2] ** 2)])


def place(lay: Layout, total_mass: float, wheel_x: float, driver_y: float, pinion_y: float, knee_top: float,
          rho: float) -> Record:
    """Put the rack, tie rods, column and steering wheel in; returns their masses."""
    design = lay.design
    left_right = rack_ends(design)
    rods = {}
    for s in steered(design):
        hp = _hp(design, s["id"])
        rods[s["id"]] = (hp["tie_inner"], hp["tie_outer"])
    rod_mass = {sid: bar_mass(float(np.linalg.norm(np.subtract(b, a))), TIE_ROD_RADIUS_MM, rho) + 2 * ROD_END_KG
                for sid, (a, b) in rods.items()}
    rest = total_mass - sum(rod_mass.values())
    if rest <= 0:
        raise ValueError("the tie rods weigh more than the steering")
    m_wheel, m_col, m_shaft = WHEEL_SHARE * rest, COLUMN_SHARE * rest, SHAFT_SHARE * rest
    m_rack = rest - m_wheel - m_col - m_shaft
    ys = sorted(p[1] for p in left_right)
    pin = pinion(design, float(np.clip(pinion_y, ys[0] + PINION_END_MM, ys[1] - PINION_END_MM)))
    centre = wheel_centre(wheel_x, driver_y, knee_top)
    u = column_direction()
    length = max((pin[0] - centre[0]) / u[0], COLUMN_MIN_MM)
    low = centre + u * length
    put_link(lay, Item("steering_rack", "ステアリングのラック", "steering_rack", (0, 0, 0), m_rack), *left_right,
             RACK_RADIUS_MM)
    for s in steered(design):
        a, b = rods[s["id"]]
        corner = ("前" if s["position_mm"][0] >= 0 else "後") + ("左" if s["position_mm"][1] > 0 else "右")
        put_link(lay, Item(f"tie_rod_{s['id']}", f"{corner}のタイロッド", "tie_rod", (0, 0, 0), rod_mass[s["id"]]),
                 a, b, TIE_ROD_RADIUS_MM, carried_by=None)
    put_link(lay, Item("steering_column", "ステアリングのコラム", "steering_column", (0, 0, 0), m_col), centre, low,
             COLUMN_RADIUS_MM)
    put_link(lay, Item("steering_shaft", "ステアリングの中間軸", "steering_shaft", (0, 0, 0), m_shaft), low, pin,
             SHAFT_RADIUS_MM)
    # the wheel: a disc square to the column, its thickness along the column
    from hiraku.designgen.kit.links import link_box

    half = STEERING_WHEEL_THICKNESS_MM / 2.0
    _, _, rpy = link_box(centre - u * half, centre + u * half, 1.0)
    w = lay.put(Item("steering_wheel", "ハンドル", "steering_wheel",
                     (STEERING_WHEEL_THICKNESS_MM, STEERING_WHEEL_DIAMETER_MM, STEERING_WHEEL_DIAMETER_MM), m_wheel),
                *centre.tolist())
    if rpy is not None:
        w["box"]["rpy_deg"] = rpy
    return {"rack": m_rack, "column": m_col, "shaft": m_shaft, "wheel": m_wheel, "tie_rods": rod_mass,
            "wheel_centre": centre.tolist(), "column_end": low.tolist(), "pinion": pin.tolist()}


def place_corners(lay: Layout, rho: float, suspension_prefix: str = "susp_") -> Record:
    """A knuckle on every wheel and a toe link on every wheel that does not steer, their masses taken
    out of the corner's suspension part (suspension_prefix + support id); returns {part id: mass}."""
    design = lay.design
    out = {}
    for s in design.get("supports", []):
        hp = _hp(design, s["id"])
        corner = ("前" if s["position_mm"][0] >= 0 else "後") + ("左" if s["position_mm"][1] > 0 else "右")
        pts = np.array([hp[k] for k in ("lca_outer", "uca_outer", "tie_outer")], float)
        lo, hi = pts.min(axis=0) - KNUCKLE_PAD_MM, pts.max(axis=0) + KNUCKLE_PAD_MM
        susp = lay.part(f"{suspension_prefix}{s['id']}")
        taken = KNUCKLE_KG
        lay.put(Item(f"knuckle_{s['id']}", f"{corner}のナックル (ハブの軸受けを含む)", "knuckle", tuple((hi - lo).tolist()),
                     KNUCKLE_KG), *((lo + hi) / 2.0).tolist(), carried_by=None)
        out[f"knuckle_{s['id']}"] = KNUCKLE_KG
        if not s["actuation"]["steer"]:
            a, b = hp["tie_inner"], hp["tie_outer"]
            m = bar_mass(float(np.linalg.norm(np.subtract(b, a))), TIE_ROD_RADIUS_MM, rho) + 2 * ROD_END_KG
            put_link(lay, Item(f"toe_link_{s['id']}", f"{corner}のトーを決めるリンク", "tie_rod", (0, 0, 0), m), a, b,
                     TIE_ROD_RADIUS_MM, carried_by=None)
            out[f"toe_link_{s['id']}"] = m
            taken += m
        if susp["mass_kg"] <= taken:
            raise ValueError(f"{susp['id']} ({susp['mass_kg']:g} kg) is lighter than the knuckle and links taken out of it")
        susp["mass_kg"] = float(susp["mass_kg"]) - taken
    return out

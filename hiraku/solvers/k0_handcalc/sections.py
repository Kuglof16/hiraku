"""Section properties computed from the design's section dimensions (mm).

Local axes follow the beam convention used by K1: x along the member, y the
"height" direction (h), z the "width" direction (b). For a horizontal member
local y points up, so a vertical load bends the section about local z and uses
Iz (the h-direction stiffness).

Returned dict (all mm based):
    A, Iy, Iz, J          area, second moments about local y / z, torsion constant
    cy, cz                distance from the centroid to the extreme fibre in y / z
    tau_per_T             max torsional shear stress per unit torque (1/mm^3)
    round                 True for circular sections (bending is checked on the
                          resultant moment instead of at corners)
    Iyz                   product of inertia (open sections: i_beam, channel, angle)
Formulas are textbook strength-of-materials results (thin-walled tubes use
Bredt's formula for torsion; rectangular solids use Roark's approximation;
open sections, dims_mm i_beam and channel {h, b, tw, tf}, angle {h, b, t}, are
joined rectangles with sharp corners and no root fillets).
"""

from __future__ import annotations

import math

SUPPORTED_SHAPES = ("rect_tube", "round_tube", "rect_solid", "round_solid", "i_beam", "channel", "angle", "custom")


def _need(dims: dict, keys: tuple[str, ...], shape: str) -> list[float]:
    missing = [k for k in keys if k not in dims]
    if missing:
        raise ValueError(f"section {shape} needs dims_mm {', '.join(keys)}; missing {', '.join(missing)}")
    vals = [float(dims[k]) for k in keys]
    if any(not math.isfinite(v) or v <= 0 for v in vals):
        raise ValueError(f"section {shape} dims_mm must be positive numbers, got {dict(zip(keys, vals))}")
    return vals


def rect_tube(b: float, h: float, t: float) -> dict:
    if 2 * t >= min(b, h):
        raise ValueError(f"rect_tube wall t={t} mm is too thick for b={b} mm, h={h} mm (needs 2t < min(b, h))")
    bi, hi = b - 2 * t, h - 2 * t
    A = b * h - bi * hi
    Iz = (b * h**3 - bi * hi**3) / 12.0
    Iy = (h * b**3 - hi * bi**3) / 12.0
    Am = (b - t) * (h - t)  # area enclosed by the wall centre line
    pm = 2.0 * ((b - t) + (h - t))
    J = 4.0 * Am**2 * t / pm
    return {"A": A, "Iy": Iy, "Iz": Iz, "J": J, "cy": h / 2.0, "cz": b / 2.0,
            "tau_per_T": 1.0 / (2.0 * Am * t), "round": False}


def round_tube(d: float, t: float) -> dict:
    if 2 * t >= d:
        raise ValueError(f"round_tube wall t={t} mm is too thick for d={d} mm (needs 2t < d)")
    di = d - 2 * t
    A = math.pi / 4.0 * (d**2 - di**2)
    I = math.pi / 64.0 * (d**4 - di**4)
    J = 2.0 * I
    return {"A": A, "Iy": I, "Iz": I, "J": J, "cy": d / 2.0, "cz": d / 2.0,
            "tau_per_T": (d / 2.0) / J, "round": True}


def rect_solid(b: float, h: float) -> dict:
    A = b * h
    Iz = b * h**3 / 12.0
    Iy = h * b**3 / 12.0
    a, c = max(b, h), min(b, h)  # long and short side
    J = a * c**3 * (1.0 / 3.0 - 0.21 * (c / a) * (1.0 - c**4 / (12.0 * a**4)))
    tau_per_T = (3.0 * a + 1.8 * c) / (a**2 * c**2)
    return {"A": A, "Iy": Iy, "Iz": Iz, "J": J, "cy": h / 2.0, "cz": b / 2.0,
            "tau_per_T": tau_per_T, "round": False}


def round_solid(d: float) -> dict:
    A = math.pi * d**2 / 4.0
    I = math.pi * d**4 / 64.0
    J = 2.0 * I
    return {"A": A, "Iy": I, "Iz": I, "J": J, "cy": d / 2.0, "cz": d / 2.0,
            "tau_per_T": 16.0 / (math.pi * d**3), "round": True}


def _plate_J(a: float, c: float) -> float:
    """St Venant torsion constant of a rectangle a x c (Roark's approximation, as rect_solid)."""
    a, c = max(a, c), min(a, c)
    return a * c**3 * (1.0 / 3.0 - 0.21 * (c / a) * (1.0 - c**4 / (12.0 * a**4)))


def _open(plates: list[tuple[float, float, float, float]]) -> dict:
    """An open section made of rectangles (y0, y1, z0, z1) that touch but do not overlap.

    A, Iy, Iz and the product Iyz about the centroid, parallel to y and z; J as the sum of the
    plates' (thin-walled open section: Roark, Formulas for Stress and Strain, the open-section
    torsion of joined rectangles); the largest torsional shear T t_max / J (the same source).
    cy, cz are the larger distances from the centroid to the extreme fibres."""
    A = sum((y1 - y0) * (z1 - z0) for y0, y1, z0, z1 in plates)
    yc = sum((y1 - y0) * (z1 - z0) * (y0 + y1) / 2.0 for y0, y1, z0, z1 in plates) / A
    zc = sum((y1 - y0) * (z1 - z0) * (z0 + z1) / 2.0 for y0, y1, z0, z1 in plates) / A
    Iz = Iy = Iyz = 0.0
    for y0, y1, z0, z1 in plates:
        hy, bz = y1 - y0, z1 - z0
        dy, dz = (y0 + y1) / 2.0 - yc, (z0 + z1) / 2.0 - zc
        Iz += bz * hy**3 / 12.0 + hy * bz * dy**2  # about z: uses the y (h) extent
        Iy += hy * bz**3 / 12.0 + hy * bz * dz**2
        Iyz += hy * bz * dy * dz
    J = sum(_plate_J(y1 - y0, z1 - z0) for y0, y1, z0, z1 in plates)
    t_max = max(min(y1 - y0, z1 - z0) for y0, y1, z0, z1 in plates)
    ys = [v for p in plates for v in p[:2]]
    zs = [v for p in plates for v in p[2:]]
    return {"A": A, "Iy": Iy, "Iz": Iz, "J": J, "Iyz": Iyz, "cy": max(yc - min(ys), max(ys) - yc),
            "cz": max(zc - min(zs), max(zs) - zc), "tau_per_T": t_max / J, "round": False}


def i_beam(h: float, b: float, tw: float, tf: float) -> dict:
    """I (H) section: two flanges b x tf, a web tw between them, h overall (along y)."""
    if 2 * tf >= h or tw >= b:
        raise ValueError(f"i_beam needs 2 tf < h and tw < b, got h={h}, b={b}, tw={tw}, tf={tf} mm")
    return _open([(0.0, tf, -b / 2.0, b / 2.0), (h - tf, h, -b / 2.0, b / 2.0),
                  (tf, h - tf, -tw / 2.0, tw / 2.0)])


def channel(h: float, b: float, tw: float, tf: float) -> dict:
    """Channel (C) section: a web tw along h (y) and two flanges b x tf toward +z. The shear centre
    lies off the web, so a load through the centroid also twists it; that coupling is not here."""
    if 2 * tf >= h or tw >= b:
        raise ValueError(f"channel needs 2 tf < h and tw < b, got h={h}, b={b}, tw={tw}, tf={tf} mm")
    return _open([(0.0, tf, 0.0, b), (h - tf, h, 0.0, b), (tf, h - tf, 0.0, tw)])


def angle(h: float, b: float, t: float) -> dict:
    """Angle (L) section: a leg h along y and a leg b along z, both t thick, meeting at a corner.
    Iy and Iz are about the centroidal axes parallel to the legs and Iyz is their product; the
    principal axes are turned from them, and the beam model's Iy and Iz leave that coupling out."""
    if t >= min(h, b):
        raise ValueError(f"angle needs t < min(h, b), got h={h}, b={b}, t={t} mm")
    return _open([(0.0, h, 0.0, t), (0.0, t, t, b)])


def properties(section: dict) -> dict:
    """Properties of a contract `section` record. Raises ValueError with what to fix."""
    shape = section.get("shape")
    dims = section.get("dims_mm", {})
    if shape == "i_beam":
        return i_beam(*_need(dims, ("h", "b", "tw", "tf"), shape))
    if shape == "channel":
        return channel(*_need(dims, ("h", "b", "tw", "tf"), shape))
    if shape == "angle":
        return angle(*_need(dims, ("h", "b", "t"), shape))
    if shape == "rect_tube":
        return rect_tube(*_need(dims, ("b", "h", "t"), shape))
    if shape == "round_tube":
        return round_tube(*_need(dims, ("d", "t"), shape))
    if shape == "rect_solid":
        return rect_solid(*_need(dims, ("b", "h"), shape))
    if shape == "round_solid":
        return round_solid(*_need(dims, ("d",), shape))
    if shape == "custom":
        keys = ("A_mm2", "Iy_mm4", "Iz_mm4", "J_mm4")
        missing = [k for k in keys if k not in section]
        if missing:
            raise ValueError(f"custom section needs {', '.join(keys)}; missing {', '.join(missing)}")
        A, Iy, Iz, J = (float(section[k]) for k in keys)
        if min(A, Iy, Iz, J) <= 0:
            raise ValueError("custom section properties must be positive")
        out = {"A": A, "Iy": Iy, "Iz": Iz, "J": J, "round": False,
               "cy": float(dims.get("cy", 0.0)), "cz": float(dims.get("cz", 0.0)),
               "tau_per_T": float(dims.get("tau_per_T", 0.0))}
        return out
    raise ValueError(f"section shape {shape!r} is not supported yet; use one of {', '.join(SUPPORTED_SHAPES)}")


def area(section: dict) -> float:
    """Cross-section area (mm^2) of a contract `section`: its A_mm2 when given, else from dims_mm
    (sharp corners). Raises ValueError with what to fix."""
    if "A_mm2" in section:
        return float(section["A_mm2"])
    return properties(section)["A"]


def corner_von_mises(props: dict, N: float, My: float, Mz: float, T: float) -> float:
    """Largest von Mises stress (MPa) on the section for internal forces in N and N*mm.

    Normal stress is taken at the extreme fibres: for non-round sections the
    worst corner gives |N|/A + |My| cz/Iy + |Mz| cy/Iz; for round sections the
    resultant moment is used. Torsional shear is taken at its maximum and
    combined with the worst normal stress (conservative). Shear from
    transverse forces is not included.
    """
    A = props["A"]
    if props["round"]:
        sigma = abs(N) / A + math.hypot(My, Mz) * props["cy"] / props["Iz"]
    else:
        sigma = abs(N) / A + abs(My) * props["cz"] / props["Iy"] + abs(Mz) * props["cy"] / props["Iz"]
    tau = abs(T) * props["tau_per_T"]
    return math.sqrt(sigma**2 + 3.0 * tau**2)

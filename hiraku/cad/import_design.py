"""Bring solids read from STEP into the contract's design record.

recognise(solid)                 -> what the solid is: a straight prism (tube or bar) with its section, or not
step_to_parts(solids, density)   -> contract parts (box + mass), each with a `basis` saying where it came from
step_to_frame(solids, join_mm)   -> nodes, members, sections for the straight prismatic tubes, and a report
design_from_step(solids or text) -> a design record, its contract errors and what the file could not say

A straight prism: every planar face is either perpendicular to one direction (exactly two such faces,
the ends, at the two extremes) or parallel to it, and every cylindrical face has its axis along it.
Its cross-section is then constant along that direction. The section is read from an end face:
an outer rectangle with a concentric inner rectangle of equal wall -> rect_tube {b, h, t}; outer and
inner concentric circles -> round_tube {d, t}; one rectangle -> rect_solid; one circle -> round_solid.
Only tubes become members; a solid bar or any other solid stays a part (a box). Nothing is guessed:
masses need a density, members need a material, and a design needs supports; what is missing is
listed in `needed`.
"""

from __future__ import annotations

import itertools
import math
import re
from typing import Any

import numpy as np

from hiraku import contract
from hiraku.cad import CadError
from hiraku.cad.geom import member_axes, unit
from hiraku.cad.step_read import Solid, read_step

ANG_TOL = 1e-4  # |cos| below this is perpendicular, above 1 - this is parallel
LEN_TOL = 0.01  # mm: walls equal, centres coincide


def _area_vec(L: np.ndarray) -> np.ndarray:
    return 0.5 * np.cross(L, np.roll(L, -1, axis=0)).sum(axis=0)


def _poly2d(L: np.ndarray, o, e1, e2) -> np.ndarray:
    d = L - o
    return np.column_stack([d @ e1, d @ e2])


def _centroid2d(P: np.ndarray) -> tuple[np.ndarray, float]:
    x, y = P[:, 0], P[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cr = x * yn - xn * y
    A = cr.sum() / 2
    if abs(A) < 1e-12:
        return P.mean(axis=0), 0.0
    return np.array([((x + xn) * cr).sum(), ((y + yn) * cr).sum()]) / (6 * A), A


def _simplify(P: np.ndarray) -> np.ndarray:
    keep = []
    n = len(P)
    for i in range(n):
        a, b, c = P[i - 1], P[i], P[(i + 1) % n]
        u, v = b - a, c - b
        if np.linalg.norm(u) < 1e-9:
            continue
        if abs(u[0] * v[1] - u[1] * v[0]) > 1e-9 * max(1.0, np.linalg.norm(u) * np.linalg.norm(v)):
            keep.append(b)
    return np.array(keep)


def _shape_of_loop(P: np.ndarray) -> dict | None:
    """{'circle', c, r} or {'rect', c, u (unit side), w (side along u), h} or None."""
    c, A = _centroid2d(P)
    r = np.linalg.norm(P - c, axis=1)
    if len(P) >= 12 and r.max() - r.min() <= 1e-3 * r.mean() + 1e-6:
        return {"kind": "circle", "c": c, "r": float(r.mean())}
    Q = _simplify(P)
    if len(Q) != 4:
        return None
    sides = [Q[(i + 1) % 4] - Q[i] for i in range(4)]
    lens = [float(np.linalg.norm(s)) for s in sides]
    for i in range(4):
        if abs(sides[i] @ sides[(i + 1) % 4]) > 1e-6 * lens[i] * lens[(i + 1) % 4]:
            return None
    return {"kind": "rect", "c": c, "u": sides[0] / lens[0], "w": lens[0], "h": lens[1]}


def recognise(s: Solid) -> dict:
    """{'prism': bool, 'reason': str, and for a prism: shape, dims_mm, a, b (end centres), ey, ez, roll_deg}."""
    planes, cyls, other = [], [], []
    for fc in s.faces:
        if fc.surface == "PLANE" and fc.loops and len(fc.loops[0]) >= 3:
            av = sum((_area_vec(L) for L in fc.loops), np.zeros(3))
            if np.linalg.norm(av) < 1e-12:
                return {"prism": False, "reason": "a planar face with no area"}
            planes.append((fc, av / np.linalg.norm(av)))
        elif fc.surface == "CYLINDRICAL_SURFACE":
            cyls.append(fc)
        else:
            other.append(fc.surface)
    if other:
        return {"prism": False, "reason": f"has faces on {', '.join(sorted(set(other)))} (not a straight prism)"}
    if s.voids:
        return {"prism": False, "reason": "has inner voids"}
    cands = [c.params["z"] for c in cyls[:1]] or [n for _, n in planes]
    best = None
    for d in cands:
        d = unit(d)
        ends = [(fc, n) for fc, n in planes if abs(n @ d) > 1 - ANG_TOL]
        sides = [(fc, n) for fc, n in planes if abs(n @ d) < ANG_TOL]
        if len(ends) != 2 or len(ends) + len(sides) != len(planes) or ends[0][1] @ ends[1][1] > 0:
            continue
        if any(abs(abs(c.params["z"] @ d) - 1) > ANG_TOL for c in cyls):
            continue
        e0, e1 = ends if ends[0][1] @ d < 0 else ends[::-1]  # e0 faces -d (start), e1 faces +d (end)
        p0, p1 = e0[0].loops[0][0], e1[0].loops[0][0]
        L = float((p1 - p0) @ d)
        allp = np.vstack([Lp for fc in s.faces for Lp in fc.loops])
        proj = allp @ d
        if L <= 0 or proj.min() < p0 @ d - LEN_TOL or proj.max() > p1 @ d + LEN_TOL:
            continue
        if best is None or L > best[0]:
            best = (L, d, e0, e1)
    if best is None:
        return {"prism": False, "reason": "no direction along which the cross-section is constant "
                                          "(not a straight prism with flat square-cut ends)"}
    L, d, e0, e1 = best
    ey, ez = member_axes(d)
    o = e0[0].loops[0][0]
    shapes = []
    for loop in e0[0].loops:
        sh = _shape_of_loop(_poly2d(loop, o, ey, ez))
        if sh is None:
            return {"prism": False, "reason": "a straight prism whose cross-section is neither a rectangle nor a circle"}
        shapes.append(sh)
    shapes.sort(key=lambda sh: -(sh["r"] if sh["kind"] == "circle" else sh["w"] * sh["h"]))
    if len(shapes) > 2:
        return {"prism": False, "reason": f"a straight prism with {len(shapes) - 1} holes"}
    out = shapes[0]
    inner = shapes[1] if len(shapes) == 2 else None
    centre2 = out["c"]
    a = o + centre2[0] * ey + centre2[1] * ez
    res: dict[str, Any] = {"prism": True, "a": a, "b": a + L * d, "length_mm": L, "ey": ey, "ez": ez, "roll_deg": 0.0}
    if out["kind"] == "circle":
        if inner is None:
            res.update(shape="round_solid", dims_mm={"d": 2 * out["r"]})
        elif inner["kind"] == "circle" and np.linalg.norm(inner["c"] - out["c"]) <= LEN_TOL:
            res.update(shape="round_tube", dims_mm={"d": 2 * out["r"], "t": out["r"] - inner["r"]})
        else:
            return {"prism": False, "reason": "a round prism whose hole is not a concentric circle"}
    else:
        ang = math.degrees(math.atan2(out["u"][1], out["u"][0]))
        k = round(ang / 90.0)
        roll = ang - 90.0 * k
        side_on_ey_is_w = k % 2 == 0
        h, b = (out["w"], out["h"]) if side_on_ey_is_w else (out["h"], out["w"])
        res["roll_deg"] = roll
        if abs(roll) > 1e-3:
            R = np.cos(math.radians(roll)), np.sin(math.radians(roll))
            res["ey"], res["ez"] = R[0] * ey + R[1] * ez, -R[1] * ey + R[0] * ez
        if inner is None:
            res.update(shape="rect_solid", dims_mm={"b": b, "h": h})
        else:
            if inner["kind"] != "rect" or np.linalg.norm(inner["c"] - out["c"]) > LEN_TOL \
                    or min(abs(inner["u"] @ out["u"]), 1 - abs(inner["u"] @ out["u"])) > 1e-6:
                return {"prism": False, "reason": "a rectangular prism whose hole is not a concentric rectangle"}
            same = abs(inner["u"] @ out["u"]) > 0.5
            wi, hi = (inner["w"], inner["h"]) if same else (inner["h"], inner["w"])
            t1, t2 = (out["w"] - wi) / 2, (out["h"] - hi) / 2
            if abs(t1 - t2) > LEN_TOL:
                return {"prism": False, "reason": f"a rectangular tube with unequal walls ({t1:.3f} and {t2:.3f} mm)"}
            res.update(shape="rect_tube", dims_mm={"b": b, "h": h, "t": (t1 + t2) / 2})
    res["dims_mm"] = {k: round(float(v), 3) for k, v in res["dims_mm"].items()}
    res["reason"] = f"straight {res['shape']} {_dims_text(res['dims_mm'])}, {L:.1f} mm long"
    if abs(res["roll_deg"]) > 0.01:
        res["reason"] += f", section turned {res['roll_deg']:.2f} deg about its axis (the design keeps b and h only)"
    return res


def _dims_text(dims: dict) -> str:
    return "x".join(f"{v:g}" for v in dims.values())


def section_key(shape: str, dims: dict) -> str:
    return f"{shape}_{_dims_text(dims)}"


def _ids(names: list[str]) -> list[str]:
    out, seen = [], {}
    for n in names:
        base = re.sub(r"\s+", "_", n.strip()) or "solid"
        k = seen.get(base, 0) + 1
        seen[base] = k
        out.append(base if k == 1 else f"{base}_{k}")
    return out


def _rpy(R: np.ndarray) -> list[float]:
    p = math.asin(max(-1.0, min(1.0, -R[2, 0])))
    r = math.atan2(R[2, 1], R[2, 2])
    y = math.atan2(R[1, 0], R[0, 0])
    return [math.degrees(r), math.degrees(p), math.degrees(y)]


def _box_frame(rec: dict, s: Solid) -> tuple[np.ndarray, list[float], list[float] | None]:
    """(centre, size, rpy or None) of an oriented box from a rect_solid prism, with the axes chosen
    closest to the design axes."""
    d = unit(rec["b"] - rec["a"])
    cols = [d, rec["ey"], rec["ez"]]
    sizes = [rec["length_mm"], rec["dims_mm"]["h"], rec["dims_mm"]["b"]]
    best = None
    for perm in itertools.permutations(range(3)):
        for signs in itertools.product((1, -1), repeat=3):
            R = np.column_stack([signs[i] * cols[perm[i]] for i in range(3)])
            if np.linalg.det(R) < 0:
                continue
            tr = float(np.trace(R))
            if best is None or tr > best[0] + 1e-12:
                best = (tr, R, [sizes[perm[i]] for i in range(3)])
    _, R, size = best
    centre = (rec["a"] + rec["b"]) / 2
    rpy = _rpy(R)
    if max(abs(a) for a in rpy) < 1e-6:
        rpy = None
    return centre, [round(float(x), 4) for x in size], None if rpy is None else [round(a, 6) for a in rpy]


def step_to_parts(solids: list[Solid], density_by_name: dict | None = None, kind: str = "imported") -> list[dict]:
    """A contract part per solid: box (oriented when the solid is a rectangular block, else the
    axis-aligned bounding box), mass = volume x density (None when no density is given for its
    name or under '*'), and `basis` (where each number came from)."""
    density_by_name = density_by_name or {}
    parts = []
    for pid, s in zip(_ids([s.name for s in solids]), solids):
        rec = recognise(s)
        if rec.get("prism") and rec["shape"] == "rect_solid":
            c, size, rpy = _box_frame(rec, s)
            how = "oriented box of a rectangular block"
        else:
            lo, hi = np.array(s.bbox_min), np.array(s.bbox_max)
            c, size, rpy = (lo + hi) / 2, [round(float(x), 4) for x in hi - lo], None
            how = "axis-aligned bounding box (the solid is not a rectangular block)"
        box: dict[str, Any] = {"center_mm": [round(float(x), 4) for x in c], "size_mm": size}
        if rpy is not None:
            box["rpy_deg"] = rpy
        rho = density_by_name.get(s.name, density_by_name.get("*"))
        mass = None if rho is None else s.volume_mm3 * 1e-9 * float(rho)
        basis = {"source": f"STEP solid {s.name!r} (#{s.entity_id})", "box": how,
                 "volume_mm3": s.volume_mm3, "volume_method": s.method, "centroid_mm": s.center_mm,
                 "density_kg_m3": rho,
                 "mass": "volume x density" if rho is not None else "not known: no density given for this solid"}
        if s.notes:
            basis["notes"] = list(s.notes)
        parts.append({"id": pid, "name": s.name, "kind": kind, "box": box, "mass_kg": mass, "basis": basis})
    return parts


def step_to_frame(solids: list[Solid], join_mm: float = 1.0, material: str = "unspecified") -> dict:
    """Members, nodes and sections from the straight prismatic tubes; every solid is in `report`
    with what it became and why. `rest` holds the solids that stay parts."""
    report, tubes, rest = [], [], []
    for mid, s in zip(_ids([s.name for s in solids]), solids):
        rec = recognise(s)
        if rec.get("prism") and rec["shape"] in ("rect_tube", "round_tube"):
            tubes.append((mid, s, rec))
        else:
            why = rec["reason"] if not rec.get("prism") else rec["reason"] + "; a solid bar, not a tube"
            report.append({"solid": s.name, "became": "part", "reason": why})
            rest.append(s)
    ends: list[np.ndarray] = []
    node_of = []
    for _, _, rec in tubes:
        pair = []
        for p in (rec["a"], rec["b"]):
            hit = next((i for i, q in enumerate(ends) if np.linalg.norm(q - p) <= join_mm), None)
            if hit is None:
                ends.append(np.array(p, float))
                hit = len(ends) - 1
            pair.append(hit)
        node_of.append(pair)
    groups: dict[int, list] = {}
    for (_, _, rec), pair in zip(tubes, node_of):
        for i, p in zip(pair, (rec["a"], rec["b"])):
            groups.setdefault(i, []).append(p)
    nodes = [{"id": f"N{i + 1}", "xyz_mm": [round(float(x), 4) for x in np.mean(groups[i], axis=0)]} for i in range(len(ends))]
    sections, members = {}, []
    for (mid, s, rec), (ia, ib) in zip(tubes, node_of):
        key = section_key(rec["shape"], rec["dims_mm"])
        sections.setdefault(key, {"shape": rec["shape"], "dims_mm": rec["dims_mm"],
                                  "source": f"measured from STEP solid {s.name!r}"})
        if ia == ib:
            report.append({"solid": s.name, "became": "part", "reason": f"a tube shorter than join_mm {join_mm}"})
            rest.append(s)
            continue
        members.append({"id": mid, "node_a": f"N{ia + 1}", "node_b": f"N{ib + 1}", "section": key, "material": material})
        report.append({"solid": s.name, "became": "member", "member": mid, "reason": rec["reason"],
                       "nodes": [f"N{ia + 1}", f"N{ib + 1}"]})
    lone = [n["id"] for n in nodes if len(groups[int(n["id"][1:]) - 1]) == 1]
    return {"nodes": nodes, "members": members, "sections": sections, "rest": rest, "report": report,
            "free_ends": lone}


def design_from_step(solids: list[Solid] | str, name: str = "imported", density_by_name: dict | None = None,
                     materials: dict | None = None, member_material: str | None = None,
                     supports: list | None = None, join_mm: float = 1.0) -> dict:
    """{design, errors (contract), needed (what the file cannot say), report, part_basis}."""
    if isinstance(solids, str):
        solids = read_step(solids)
    frame = step_to_frame(solids, join_mm, member_material or "unspecified")
    parts = step_to_parts(frame["rest"], density_by_name)
    basis = {p["id"]: p.pop("basis") for p in parts}
    design = {"design_version": 1, "parent_version": None, "name": name, "variables": {},
              "supports": list(supports or []), "parts": parts, "nodes": frame["nodes"],
              "members": frame["members"], "sections": frame["sections"], "materials": dict(materials or {})}
    needed = []
    if not supports:
        needed.append("supports: where the product touches the ground (wheels, casters or legs); a STEP file has none")
    no_mass = [p["id"] for p in parts if p["mass_kg"] is None]
    if no_mass:
        needed.append(f"density_kg_m3 for {len(no_mass)} solids without one (masses unknown): {', '.join(no_mass[:10])}"
                      + (" ..." if len(no_mass) > 10 else ""))
    if frame["members"] and (member_material is None or member_material not in design["materials"]):
        needed.append("the members' material (E, nu, density, yield with sources) in materials, named by member_material;"
                      " a STEP file gives shapes only")
    needed.append("load cases: a STEP file has no loads (they are built from the requirements and the catalog)")
    errors = contract.errors("design", design)
    return {"design": design, "errors": errors, "needed": needed, "report": frame["report"],
            "free_ends": frame["free_ends"], "part_basis": basis}


__all__ = ["recognise", "step_to_parts", "step_to_frame", "design_from_step", "CadError"]

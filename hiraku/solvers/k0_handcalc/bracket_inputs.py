"""The inputs of the mount bracket formulas (library_bracket, load case R9) for one corner of a design.

    pts = mount_points(design, support_id)                    # the bracket, pin axis, base line, b, e at each mount
    got = bracket_inputs(design, support_id, upstream)       # forces from K2, the worst utilisations

Forces: K2's series at the suspension mounts (mount_x/y/z.<support>.<mount>, N, world axes, the force the body
receives), read with hiraku.solvers.shelf.frame_static.upstream_mount_series one upstream analysis at a time (so
in the body's axes, as that function turns them). For each mount point and each upstream analysis the instant
where |F| is largest is taken; the four utilisations are computed at each of those, and for each quantity the
worst over the upstream analyses is the result (not the largest |F| alone: the direction matters). With no
upstream analysis carrying the series, bracket_inputs raises NoForces (a ValueError): no force is invented.

Geometry (from the bracket part's box and the hard point, no field of its own):
- the pin along the body's x: the plates are the box's two faces across the car, as kit/mounts._bracket draws
  their mass (an assumption: the arms' pivot axes and the damper's eye are taken along x);
- the plates' base: the box face (of the four along x) toward the frame node the bracket is carried by; the base
  line runs along the other of y and z; b = the box's size along the base line;
- e = the distance from the base to the hard point (the pin centre) normal to the base; where the hard point is on
  the base (a clevis on a member's end, whose node is the hard point), the pin is taken a hole edge distance (1.2
  d0) inside the plates' free end: e = the box's depth - 1.2 d0.
A mount point with no bracket part (a member's end in another design): the pin in shear and bearing only (with
the plate thickness of the other mounts' joint, or the defaults), the plate and the weld not checked.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from . import library_bracket as lb

Record = dict[str, Any]
MOUNTS = ("lca_front", "lca_rear", "uca_front", "uca_rear", "damper_upper", "tie_inner")  # k2_vehicle.kinematics.MOUNTS
NODE_TOL_MM = 2.0  # a bracket's box holds its hard point within this
MATERIALS = Path(__file__).resolve().parents[3] / "data" / "materials" / "materials.json"
PLATE_MATERIAL = "stkr400"  # the brackets' plates taken as the frame's steel (assumption)
PIN_AXIS = np.array([1.0, 0.0, 0.0])

ASSUMPTIONS = [
    "forces: K2's at the suspension mounts (the force the body receives), the largest |F| of each upstream "
    "analysis, the worst utilisation over those analyses",
    "the A-arm's force along the arm is split half and half between its two inner pivots by K2",
    "the pin along the body's x (the plates across the car, as the bracket's mass is drawn)",
    "the plates' base on the box face toward the frame node that carries the bracket; e from the base to the hard point "
    "(on the base: a hole edge distance 1.2 d0 inside the plates' free end)",
    "the plates' steel taken as STKR400 (f_y 245, f_u 400 MPa); the pin grade 8.8",
]


class NoForces(ValueError):
    """No upstream analysis gives K2's mount series for the design."""


def plate_material() -> Record:
    m = json.loads(MATERIALS.read_text(encoding="utf-8"))[PLATE_MATERIAL]
    return {"f_y": float(m["yield_MPa"]), "f_u": float(m["tensile_MPa"])}


def _corner(design: Record, support_id: str) -> Record:
    s = next((x for x in design.get("supports", []) if x["id"] == support_id), None)
    hp = ((s or {}).get("suspension") or {}).get("hardpoints_mm")
    if not hp:
        raise ValueError(f"support {support_id} has no suspension.hardpoints_mm")
    return hp


def _holds(part: Record, p: np.ndarray) -> bool:
    c, size = (np.asarray(part["box"][k], float) for k in ("center_mm", "size_mm"))
    return bool(np.all(np.abs(p - c) <= size / 2.0 + NODE_TOL_MM))


def find_bracket(design: Record, support_id: str, mount: str) -> Record | None:
    """The bracket part (kind bracket with part.joint) that holds the hard point: by position (its box holds the
    point), the one named bracket_<lca|uca|damper|tie>_<support>.. first when several do."""
    p = np.asarray(_corner(design, support_id)[mount], float)
    cands = [x for x in design.get("parts", []) if x.get("kind") == "bracket" and x.get("box") and _holds(x, p)]
    if not cands:
        return None
    stem = {"lca_front": "bracket_lca_", "lca_rear": "bracket_lca_", "uca_front": "bracket_uca_",
            "uca_rear": "bracket_uca_", "damper_upper": "bracket_damper_", "tie_inner": "bracket_tie_"}[mount]
    cands.sort(key=lambda x: (not x["id"].startswith(stem + support_id), not x.get("joint"),
                              float(np.linalg.norm(np.asarray(x["box"]["center_mm"], float) - p))))
    return cands[0]


def plate_geometry(part: Record, hp, carrier_xyz, d0: float) -> Record:
    """{u, base, b, e, base_axis} of a clevis bracket (see the module doc)."""
    c, size = (np.asarray(part["box"][k], float) for k in ("center_mm", "size_mm"))
    half = size / 2.0
    p = np.asarray(hp, float)
    d = np.asarray(carrier_xyz, float) - c
    ratio = [abs(d[i]) / half[i] if half[i] > 0 else 0.0 for i in (1, 2)]
    i = 1 if ratio[0] > ratio[1] else 2
    j = 3 - i
    side = 1.0 if d[i] >= 0 else -1.0
    base_at = c[i] + side * half[i]
    edge = lb.EDGE_FACTOR * d0
    e = abs(p[i] - base_at)
    if e < edge:
        e = max(size[i] - edge, 0.0)
    base = np.zeros(3)
    base[j] = 1.0
    return {"u": PIN_AXIS.copy(), "base": base, "b": float(size[j]), "e": float(e), "base_axis": "yz"[i - 1]}


def mount_points(design: Record, support_id: str) -> dict[str, Record]:
    """For each mount of the support: {"bracket" (part id or None), "joint", "u", "base", "b", "e" (None without a
    bracket)}."""
    hp = _corner(design, support_id)
    nodes = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    out: dict[str, Record] = {}
    joints = [x["joint"] for x in design.get("parts", []) if x.get("kind") == "bracket" and x.get("joint")]
    fallback = joints[0] if joints else {"bolt_d_mm": 10.0, "bolts": 1, "plate_t_mm": 3.2, "weld_leg_mm": 3.0}
    for m in MOUNTS:
        if m not in hp:
            continue
        part = find_bracket(design, support_id, m)
        if part is None or not part.get("joint"):
            out[m] = {"bracket": part["id"] if part else None, "joint": dict(fallback), "u": PIN_AXIS.copy(),
                      "base": np.array([0.0, 1.0, 0.0]), "b": None, "e": None}
            continue
        j = part["joint"]
        carrier = nodes.get((part.get("carried_by") or [None])[0], hp[m])
        g = plate_geometry(part, hp[m], carrier, float(j["bolt_d_mm"]) + lb.HOLE_CLEARANCE_MM)
        out[m] = {"bracket": part["id"], "joint": dict(j), **g}
    return out


def _peaks(design: Record, support_id: str, lc_id: str, ana: Record) -> dict[str, Record] | None:
    from hiraku.solvers.shelf.frame_static import upstream_mount_series

    got = upstream_mount_series({lc_id: ana}, design)
    if got is None:
        return None
    _, ana_id, instants = got
    best: dict[str, Record] = {}
    for inst in instants:
        for m, _, f in inst["mount_loads"][support_id]["vectors"]:
            mag = float(np.linalg.norm(f))
            if m not in best or mag > best[m]["F_abs"]:
                best[m] = {"F_N": np.asarray(f, float), "F_abs": mag, "t_s": inst["t_s"], "load_case_id": lc_id,
                           "analysis_id": ana_id}
    return best


def bracket_inputs(design: Record, support_id: str, upstream: dict | None) -> Record:
    """{"support", "points": {mount: {"bracket", "worst": {quantity: {"utilisation", "inputs" (formula inputs,
    plain numbers in the formula's units), "load_case_id", "analysis_id", "t_s", "F_N" (body axes)}}, "geometry"}},
    "worst": {quantity: (mount, utilisation)}, "used": [load case ids], "assumptions"}. upstream: {load case id:
    analysis record} (the K2 analyses that feed R9)."""
    pts = mount_points(design, support_id)
    mat = plate_material()
    used, per = [], []
    for lc_id, ana in (upstream or {}).items():
        if not isinstance(ana, dict):
            continue
        pk = _peaks(design, support_id, lc_id, ana)
        if pk is not None:
            used.append(lc_id)
            per.append(pk)
    if not per:
        raise NoForces(f"R9 {support_id}: no upstream analysis gives K2's mount series (mount_x/y/z.{support_id}.<mount>, "
                       f"sprung_roll/pitch/yaw); the mount forces are not invented")
    assumptions = list(ASSUMPTIONS)
    points: dict[str, Record] = {}
    for m, g in pts.items():
        j = g["joint"]
        d, n, t, s = float(j["bolt_d_mm"]), int(j["bolts"]), float(j["plate_t_mm"]), float(j["weld_leg_mm"])
        has = g["b"] is not None
        if not has:
            assumptions.append(f"{support_id}.{m}: no bracket part; the pin's shear and bearing only, the plate and the "
                               f"weld not checked")
        worst: dict[str, Record] = {}
        for pk in per:
            if m not in pk:
                continue
            f = lb.split_force(pk[m]["F_N"], g["u"], g["base"])
            us = lb.clevis_utilisations(pk[m]["F_N"], g["u"], g["base"], d, n, t, s if has else None,
                                        g["b"] if has else None, g["e"] if has else None, mat["f_y"], mat["f_u"])
            vals = {"F_n": abs(f["F_n"]), "F_t": abs(f["F_t"]), "F_v": abs(f["F_v"]), "d": d, "n": float(n), "t": t,
                    "s": s, "b": g["b"], "e": g["e"], "f_y": mat["f_y"], "f_u": mat["f_u"], "f_ub": lb.F_UB_8_8_MPA}
            for qty, u in us.items():
                if qty not in worst or u > worst[qty]["utilisation"]:
                    worst[qty] = {"utilisation": u, "inputs": {k: vals[k] for k in lb.INPUTS_OF[qty]},
                                  "load_case_id": pk[m]["load_case_id"], "analysis_id": pk[m]["analysis_id"],
                                  "t_s": pk[m]["t_s"], "F_N": [float(x) for x in pk[m]["F_N"]]}
        if worst:
            points[m] = {"bracket": g["bracket"], "worst": worst,
                         "geometry": {"b": g["b"], "e": g["e"], "base_axis": g.get("base_axis"), "joint": j}}
    overall: dict[str, tuple[str, float]] = {}
    for m, p in points.items():
        for qty, w in p["worst"].items():
            if qty not in overall or w["utilisation"] > overall[qty][1]:
                overall[qty] = (m, w["utilisation"])
    return {"support": support_id, "points": points, "worst": overall, "used": used, "assumptions": assumptions}


__all__ = ["ASSUMPTIONS", "MOUNTS", "NoForces", "bracket_inputs", "find_bracket", "mount_points", "plate_geometry",
           "plate_material"]

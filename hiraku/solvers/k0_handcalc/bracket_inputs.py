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
  line runs along the other of y and z; b = the welded length: the part of the base on the tube wall under it
  (weld_span, which the shell's mesh reads too), not the box's size along the base line (box_b);
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
BASE_TOL_MM = 1.0  # the box's base face on a tube wall's outer face within this (k1_shell.brackets.BASE_TOL_MM)
END_CLEVIS_NOTE = ("its base is not on a tube wall running along the pin (a clevis on a member's end); the box's width "
                   "taken as the welded length (not checked against the member's section)")
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
    "the welded length b of the plate and weld formulas: the part of the plate's base that lies on the tube wall "
    "(weld_span: the box's extent along the base line within the wall's mid-line width), the length the shell ties; "
    "the rest of the box's width overhangs the wall and is not welded (before stage 3 the box's whole width was taken)",
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
    """{u, base, b, e, base_axis, base_at, base_side} of a clevis bracket (see the module doc): base_at the base
    face's coordinate along base_axis, base_side +1 / -1 the direction from the box's centre to that face."""
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
    return {"u": PIN_AXIS.copy(), "base": base, "b": float(size[j]), "e": float(e), "base_axis": "yz"[i - 1],
            "base_at": float(base_at), "base_side": side}


def weld_span(design: Record, part: Record, geom: Record) -> Record | None:
    """The part of a bracket's base that lies on a tube wall (the welded length; the hand calculation and the shell
    mesh, k1_shell.brackets.plan_brackets, both read this): a rect tube member running along the pin axis whose wall's
    outer face is the box's base face (within BASE_TOL_MM) and whose segment holds the box's centre along the pin; the
    wall's extent across is its mid-line width (the outer size less one wall thickness, as the shell's walls end at the
    neighbouring walls' mid-surfaces). {"span": (w0, w1) from the box's low face along the base line, "L": w1 - w0,
    "member", "box_b": the box's size along the base line}; None when no such wall is under the base."""
    from hiraku.solvers.k1_shell.mesh import k1_axes  # the shell's member axes (the same wall layout)

    c, size = (np.asarray(part["box"][k], float) for k in ("center_mm", "size_mm"))
    i = "xyz".index(geom["base_axis"])
    j = 3 - i
    e_i = np.eye(3)[i]
    up = -float(geom["base_side"]) * e_i  # from the wall into the plate
    lo_j = float(c[j] - size[j] / 2.0)
    u = np.asarray(geom["u"], float)
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])}
    best = None
    for m in design.get("members", []):
        sec = (design.get("sections") or {}).get(m.get("section"), {})
        dims = sec.get("dims_mm") or {}
        if sec.get("shape") != "rect_tube" or not {"b", "h", "t"} <= set(dims):
            continue
        a, b = nodes.get(m["node_a"]), nodes.get(m["node_b"])
        if a is None or b is None:
            continue
        length = float(np.linalg.norm(b - a))
        if length <= 0.0:
            continue
        d = (b - a) / length
        if abs(abs(float(d @ u)) - 1.0) > 1e-6:
            continue
        s = float((c - a) @ d)
        if not 0.0 <= s <= length:
            continue
        ey, ez = k1_axes(d)
        bt, ht, t = float(dims["b"]), float(dims["h"]), float(dims["t"])
        for nrm, out_half, across in ((ey, ht / 2.0, ez), (-ey, ht / 2.0, ez), (ez, bt / 2.0, ey), (-ez, bt / 2.0, ey)):
            if float(nrm @ up) < 1.0 - 1e-6:
                continue
            if abs(float((a + out_half * nrm) @ e_i) - float(geom["base_at"])) > BASE_TOL_MM:
                continue
            mid_half = ((bt if across is ez else ht) - t) / 2.0
            ends = sorted([float((a - mid_half * across) @ np.eye(3)[j]), float((a + mid_half * across) @ np.eye(3)[j])])
            w0, w1 = max(ends[0], lo_j) - lo_j, min(ends[1], lo_j + float(size[j])) - lo_j
            if w1 - w0 > 0.0 and (best is None or w1 - w0 > best["L"]):
                best = {"span": (float(w0), float(w1)), "L": float(w1 - w0), "member": m["id"], "box_b": float(size[j])}
    return best


def mount_points(design: Record, support_id: str) -> dict[str, Record]:
    """For each mount of the support: {"bracket" (part id or None), "joint", "u", "base", "b" (the welded length,
    weld_span's L), "e", "box_b", "weld_span" (None without a bracket or without a tube wall under its base: then
    a clevis on a member's end keeps b = box_b, END_CLEVIS_NOTE)}."""
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
                      "base": np.array([0.0, 1.0, 0.0]), "b": None, "e": None, "box_b": None, "weld_span": None}
            continue
        j = part["joint"]
        carrier = nodes.get((part.get("carried_by") or [None])[0], hp[m])
        g = plate_geometry(part, hp[m], carrier, float(j["bolt_d_mm"]) + lb.HOLE_CLEARANCE_MM)
        ws = weld_span(design, part, g)
        g["box_b"] = g["b"]
        g["weld_span"] = ws
        # the formulas' b: the welded length; a clevis on a member's end (no tube wall along the pin under its base)
        # keeps the box's width (its base is the member's end, within the tube's section: END_CLEVIS_NOTE)
        g["b"] = ws["L"] if ws else g["box_b"]
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
        elif g.get("weld_span") is None:
            assumptions.append(f"{support_id}.{m}: {g['bracket']}: {END_CLEVIS_NOTE}")
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
            ws = g.get("weld_span")
            points[m] = {"bracket": g["bracket"], "worst": worst,
                         "geometry": {"b": g["b"], "e": g["e"], "base_axis": g.get("base_axis"), "joint": j,
                                      "box_b": g.get("box_b"), "weld_member": ws["member"] if ws else None}}
    overall: dict[str, tuple[str, float]] = {}
    for m, p in points.items():
        for qty, w in p["worst"].items():
            if qty not in overall or w["utilisation"] > overall[qty][1]:
                overall[qty] = (m, w["utilisation"])
    return {"support": support_id, "points": points, "worst": overall, "used": used, "assumptions": assumptions}


__all__ = ["ASSUMPTIONS", "MOUNTS", "NoForces", "bracket_inputs", "find_bracket", "mount_points", "plate_geometry",
           "plate_material", "weld_span"]

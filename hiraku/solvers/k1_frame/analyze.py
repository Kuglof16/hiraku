"""K1 static analysis of a design's frame for one load situation.

Two ways to hold the frame:
- at rest ("wheels"): the frame stands on its supports (rigid, or the
  suspension spring to the ground); the FE model decides the wheel loads
- with given ground forces ("free"): the forces come from K0 rigid-body
  statics (e.g. a wheel climbing a step) or from an upstream analysis (K2),
  and are applied as loads. Whatever they leave unbalanced against the weight
  is taken by the masses as d'Alembert inertia (inertia relief: the rigid-body
  linear and angular acceleration that the imbalance gives). Six restraints
  only stop rigid-body motion and should carry nothing (their residual is
  reported as a check).

Ground forces are reported as vectors of kind "reaction" (normal) and "grip"
(along the ground); the two together are the whole force at a support.

Or mount loads (free): the forces the body receives at the suspension mounts
(K2's mount series, in the body's axes) take the place of the ground forces,
each at its hard point, carried into the frame where the design takes it
(mount_carriers: a frame node at the point, such as a tower's top; the nodes
a bracket part holding the point is welded to; else the support's wheel node
as the ground forces), with a moment per support (a drive or brake reacting
on the body) at the wheel node. The unsprung parts are inside those
forces, so only the sprung masses are loaded (sprung_items: K2's split of
the parts by kind), and gravity may come tilted.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np

from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import (DesignError, inertia_tensor, mass_summary, resultant, support_contact,
                                   support_loads)
from .model import FrameModel, build, stress_dims

BALANCE_TOL = 0.01  # given ground forces must balance the weight (and given inertia) to this fraction; another
#                     loading or design version is far outside it, small bookkeeping differences go to inertia relief


MOUNT_TOL_MM = 1.0  # a hard point this close to a design node, or to a bracket's box, is on it


def mount_carriers(design: dict) -> dict[str, dict[str, list[str]]]:
    """{support: {mount: frame nodes}} for each support's suspension.hardpoints_mm: the design node at the point
    (within MOUNT_TOL_MM; a tower's top), else the carried_by nodes of the part of kind bracket whose box holds
    the point, else [] (the force goes in at the support's wheel mount)."""
    from ..k0_handcalc.rigid import rpy_matrix

    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])}
    used = {m[k] for m in design.get("members", []) for k in ("node_a", "node_b")}
    brackets = []
    for p in design.get("parts", []):
        if str(p.get("kind", "")).lower() == "bracket" and p.get("carried_by"):
            b = p["box"]
            R = rpy_matrix(b["rpy_deg"]) if b.get("rpy_deg") else np.eye(3)
            brackets.append((p, np.asarray(b["center_mm"], float), R, np.asarray(b["size_mm"], float) / 2.0))
    out: dict[str, dict[str, list[str]]] = {}
    for s in design.get("supports", []):
        got = {}
        for mount, xyz in ((s.get("suspension") or {}).get("hardpoints_mm") or {}).items():
            q = np.asarray(xyz, float)
            at = [n for n in used if n in nodes and float(np.linalg.norm(nodes[n] - q)) <= MOUNT_TOL_MM]
            if at:
                got[mount] = at[:1]
                continue
            held = [p for p, c, R, h in brackets if np.all(np.abs(R.T @ (q - c)) <= h + MOUNT_TOL_MM)]
            got[mount] = list(held[0]["carried_by"]) if held else []
        out[s["id"]] = got
    return out


def mount_share(force, point, nodes_xyz: list) -> list[tuple[np.ndarray, np.ndarray]]:
    """(force, moment about the node) for each of the nodes a mount's force (at point) is shared over: equal
    forces and the moment of the point's offset from their centroid shared equally (as the parts)."""
    f, p = np.asarray(force, float), np.asarray(point, float)
    k = len(nodes_xyz)
    c = sum(np.asarray(x, float) for x in nodes_xyz) / k
    m = np.cross(p - c, f)
    return [(f / k, m / k)] * k


def sprung_items(design: dict, items: list) -> list:
    """The mass items the body carries as K2 splits them (k2_vehicle.model): parts of a wheel kind are
    unsprung, parts of a suspension kind half sprung (half their mass and inertia, where they are)."""
    from ..k2_vehicle.model import HALF_UNSPRUNG_KINDS, UNSPRUNG_KINDS

    kind = {f"part:{p['id']}": str(p.get("kind", "")).lower() for p in design.get("parts", [])}
    out = []
    for it in items:
        k = kind.get(it.id, "")
        if k in UNSPRUNG_KINDS:
            continue
        if k in HALF_UNSPRUNG_KINDS:
            it = dataclasses.replace(it, mass_kg=0.5 * it.mass_kg,
                                     inertia_kg_mm2=None if it.inertia_kg_mm2 is None else 0.5 * it.inertia_kg_mm2)
        out.append(it)
    return out


def _split(point, force, contact: str) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """Vectors for one ground force: on flat ground the vertical part is the reaction and the
    horizontal part the grip; at a step corner the rigid wheel's push is all reaction."""
    p, f = np.asarray(point, float), np.asarray(force, float)
    if contact == "step_corner":
        return [("reaction", p, f)]
    out = [("reaction", p, np.array([0.0, 0.0, f[2]]))]
    if abs(f[0]) + abs(f[1]) > 1e-9:
        out.append(("grip", p, np.array([f[0], f[1], 0.0])))
    return out


def analyze(design: dict, g_factor: float = 1.0, step_mm: float | None = None,
            step_supports: list[str] | None = None, ground_forces: dict | None = None,
            inertia_N: np.ndarray | None = None, node_loads: list | None = None,
            mount_loads: dict | None = None, gravity_mps2=None) -> dict:
    """Solve the frame.

    ground_forces: {support id: [(kind, point_mm, force_N), ...]} from an
    upstream analysis. With inertia_N (the upstream's d'Alembert force -m a at
    the centre of gravity) the imbalance must agree with it; without it the
    forces must balance the weight (quasi-static). Otherwise step_mm makes K0
    compute the step forces, and without either the frame stands at rest.
    node_loads: [(frame node, point_mm, force_N), ...] other external forces
    (e.g. a wall); they are part of the balance, so give inertia_N with them.
    mount_loads {support: {"vectors": [(mount, point_mm, force_N)], "moment_Nmm": (3,)}}: the forces
    the body receives at its suspension mounts (named as in hardpoints_mm, carried in by mount_carriers),
    in place of the ground forces (only the sprung masses are loaded, sprung_items); gravity_mps2: gravity in the body's axes (default straight down, times g_factor).
    """
    summ = mass_summary(design)
    items = summ["items"]
    fm = build(design)
    gvec = (np.array([0.0, 0.0, -STANDARD_GRAVITY * g_factor]) if gravity_mps2 is None
            else np.asarray(gravity_mps2, float))
    if mount_loads is not None:
        items = sprung_items(design, items)
        m_body = sum(it.mass_kg for it in items)
        cg = sum(it.mass_kg * it.cg_mm for it in items) / m_body
    else:
        m_body = summ["mass_kg"]
        cg = summ["cg_mm"]
    weight = (summ["mass_kg"] * STANDARD_GRAVITY * g_factor if mount_loads is None and gravity_mps2 is None
              else m_body * float(np.linalg.norm(gvec)))
    ground: dict[str, dict] = {}
    statics = None
    accel = np.zeros(3)
    alpha = np.zeros(3)
    moments: dict[str, np.ndarray] = {}
    if mount_loads is not None:
        missing = [s["id"] for s in design["supports"] if s["id"] not in mount_loads]
        if missing:
            raise DesignError(f"the given mount loads have no entry for support(s) {', '.join(missing)}")
        for sid, ml in mount_loads.items():
            vs = [(k, np.asarray(p, float), np.asarray(f, float)) for k, p, f in ml["vectors"]]
            ground[sid] = {"point_mm": vs[0][1] if vs else np.zeros(3),
                           "force_N": sum((f for _, _, f in vs), np.zeros(3)), "vectors": vs}
            moments[sid] = np.asarray(ml.get("moment_Nmm", np.zeros(3)), float)
    elif ground_forces is not None:
        missing = [s["id"] for s in design["supports"] if s["id"] not in ground_forces]
        if missing:
            raise DesignError(f"the given ground forces have no entry for support(s) {', '.join(missing)}")
        ground = {sid: {"point_mm": np.asarray(vs[0][1], float), "force_N": sum(np.asarray(v[2], float) for v in vs),
                        "vectors": [(k, np.asarray(p, float), np.asarray(f, float)) for k, p, f in vs]}
                  for sid, vs in ground_forces.items()}
    elif step_mm is not None:
        statics = support_loads(design, g_factor, step_mm, step_supports, summary=summ)
        for sid, sl in statics["supports"].items():
            ground[sid] = dict(sl, vectors=_split(sl.get("contact_point", sl["point_mm"]), sl["force_N"], sl["contact"]))

    if not ground:
        fm.add_gravity(items, g_factor)
        fm.restrain_on_wheels()
        fm.solve()
        for s in design["supports"]:
            node = fm.wheel_nodes[s["id"]]
            f, _ = fm.reaction(node)
            f = f + fm.spring_force(s["id"])
            p = support_contact(s)
            ground[s["id"]] = {"point_mm": p, "force_N": f, "contact": "ground", "vectors": _split(p, f, "ground")}
        residual = 0.0
    else:
        extra = [(n, np.asarray(p, float), np.asarray(f, float)) for n, p, f in (node_loads or [])]
        for n, _, _ in extra:
            if n not in fm.model.nodes:
                raise DesignError(f"a load is put on node {n!r}, which is not in the frame")
        pairs = [(p, f) for g in ground.values() for _, p, f in g["vectors"]] + [(p, f) for _, p, f in extra]
        weights = [(it.cg_mm, np.array([0.0, 0.0, -it.mass_kg * STANDARD_GRAVITY * g_factor]) if gravity_mps2 is None
                    else it.mass_kg * gvec) for it in items]
        F, M = resultant(pairs + weights)
        M = M + sum(moments.values(), np.zeros(3))
        M_cg = M - np.cross(cg, F)
        span = max(float(np.ptp([s["position_mm"][0] for s in design["supports"]])), 1.0)
        if inertia_N is None:
            off = (float(np.linalg.norm(F)) + float(np.linalg.norm(M_cg)) / span) / weight
            what = "the weight"
        else:
            off = float(np.linalg.norm(F + np.asarray(inertia_N, float))) / weight
            what = "the weight and the inertia force passed with them"
        if off > BALANCE_TOL:
            raise DesignError(
                f"the ground forces passed in do not balance {what} of this design (off by {off:.1%} of the weight); "
                f"they were probably computed for another loading or design version, so analyse them with the same loading")
        accel = F / m_body  # m/s^2
        alpha = np.linalg.solve(inertia_tensor(items, cg), M_cg) * 1e3  # (N*mm)/(kg*mm^2) -> rad/s^2
        if gravity_mps2 is None:
            fm.add_gravity(items, g_factor)
        else:
            fm.add_body_loads(items, gvec)
        carriers = mount_carriers(design) if mount_loads is not None else {}
        for sid, g in ground.items():
            for kind, p, f in g["vectors"]:
                at = carriers.get(sid, {}).get(kind) or []
                if len(at) == 1:
                    fm.add_force_at(at[0], p, f)
                elif at:
                    for n, (fn, mn) in zip(at, mount_share(f, p, [fm.node_xyz(n) for n in at])):
                        fm.add_force(n, fn, mn)
                else:
                    fm.add_force_at(fm.wheel_nodes[sid], p, f)
            if np.any(moments.get(sid, 0.0)):
                fm.add_force(fm.wheel_nodes[sid], np.zeros(3), moments[sid])
        for n, p, f in extra:
            fm.add_force_at(n, p, f)
        if np.any(accel != 0) or np.any(alpha != 0):
            fm.add_body_loads(items, -accel, -alpha, cg)
        held = fm.restrain_free_body()
        fm.solve()
        residual = max(float(np.linalg.norm(fm.reaction(fm.wheel_nodes[s])[0])) for s in held) / weight

    stresses = {mid: fm.member_stress(mid) for mid in fm.props}
    worst = max(stresses, key=lambda k: stresses[k]["max"])
    wheel_pts = [fm.node_xyz(n) for n in fm.wheel_nodes.values()]
    wheel_u = [fm.displacement(n) for n in fm.wheel_nodes.values()]
    t, theta = rigid_fit(wheel_pts, wheel_u)
    disp = {n: fm.displacement(n) - (t + np.cross(theta, fm.node_xyz(n))) for n in fm.frame_nodes}
    worst_node = max(disp, key=lambda k: float(np.linalg.norm(disp[k])))
    safety = {}
    for mid, st in stresses.items():
        fy = float(fm.props[mid]["material"]["yield_MPa"])
        safety[mid] = fy / st["max"] if st["max"] > 0 else math.inf

    total_ground = sum(g["force_N"] for g in ground.values())
    return {
        "model": fm,
        "mass": summ,
        "weight_N": weight,
        "statics": statics,
        "ground": ground,
        "ground_total_N": total_ground,
        "residual_ratio": residual,
        "inertia_accel": accel,
        "inertia_alpha": alpha,
        "stresses": stresses,
        "worst_member": worst,
        "displacements": disp,
        "worst_node": worst_node,
        "safety": safety,
    }


def rigid_fit(points: list[np.ndarray], disps: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares rigid motion u = t + theta x p through the given points.

    Frame displacements are reported relative to this motion of the wheel
    centres, so spring sag and the free-body hold do not show as deflection.
    """
    rows, rhs = [], []
    for p, u in zip(points, disps):
        px = np.array([[0.0, -p[2], p[1]], [p[2], 0.0, -p[0]], [-p[1], p[0], 0.0]])
        rows.append(np.hstack([np.eye(3), -px]))
        rhs.append(u)
    sol, *_ = np.linalg.lstsq(np.vstack(rows), np.concatenate(rhs), rcond=None)
    return sol[:3], sol[3:]


def stress_terms(result: dict) -> list[dict]:
    """Split the worst stress into axial, the two bendings and torsion (share of their linear sum)."""
    fm: FrameModel = result["model"]
    mid = result["worst_member"]
    st = result["stresses"][mid]
    p = fm.props[mid]["section"]
    stress_dims(p, mid)
    parts = {"axial": abs(st["N"]) / p["A"]}
    if p["round"]:
        parts["bending"] = math.hypot(st["My"], st["Mz"]) * p["cy"] / p["Iz"]
    else:
        parts["bending_vertical"] = abs(st["Mz"]) * p["cy"] / p["Iz"]
        parts["bending_sideways"] = abs(st["My"]) * p["cz"] / p["Iy"]
    parts["torsion"] = math.sqrt(3.0) * abs(st["T"]) * p["tau_per_T"]
    total = sum(parts.values())
    return [{"name": f"{k} ({mid})", "value": v, "unit": "MPa", "share": (v / total if total > 0 else 0.0)}
            for k, v in parts.items()]

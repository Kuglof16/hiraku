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
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import (DesignError, inertia_tensor, mass_summary, resultant, support_contact,
                                   support_loads)
from .model import FrameModel, build

BALANCE_TOL = 0.01  # given ground forces must balance the weight (and given inertia) to this fraction; another
#                     loading or design version is far outside it, small bookkeeping differences go to inertia relief


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
            inertia_N: np.ndarray | None = None) -> dict:
    """Solve the frame.

    ground_forces: {support id: [(kind, point_mm, force_N), ...]} from an
    upstream analysis. With inertia_N (the upstream's d'Alembert force -m a at
    the centre of gravity) the imbalance must agree with it; without it the
    forces must balance the weight (quasi-static). Otherwise step_mm makes K0
    compute the step forces, and without either the frame stands at rest.
    """
    summ = mass_summary(design)
    items = summ["items"]
    fm = build(design)
    weight = summ["mass_kg"] * STANDARD_GRAVITY * g_factor
    cg = summ["cg_mm"]
    ground: dict[str, dict] = {}
    statics = None
    accel = np.zeros(3)
    alpha = np.zeros(3)
    if ground_forces is not None:
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
        pairs = [(p, f) for g in ground.values() for _, p, f in g["vectors"]]
        weights = [(it.cg_mm, np.array([0.0, 0.0, -it.mass_kg * STANDARD_GRAVITY * g_factor])) for it in items]
        F, M = resultant(pairs + weights)
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
        accel = F / summ["mass_kg"]  # m/s^2
        alpha = np.linalg.solve(inertia_tensor(items, cg), M_cg) * 1e3  # (N*mm)/(kg*mm^2) -> rad/s^2
        fm.add_gravity(items, g_factor)
        for sid, g in ground.items():
            for _, p, f in g["vectors"]:
                fm.add_force_at(fm.wheel_nodes[sid], p, f)
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

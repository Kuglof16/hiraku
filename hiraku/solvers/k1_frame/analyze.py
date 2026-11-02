"""K1 static analysis of a design's frame for one load situation.

Two ways to hold the frame:
- at rest ("wheels"): the frame stands on its supports (rigid, or the
  suspension spring to the ground); the FE model decides the wheel loads
- with self-balanced loads ("free"): the ground forces come from K0 rigid-body
  statics (e.g. a wheel climbing a step) and are applied as loads; six
  restraints only stop rigid-body motion and should carry nothing (their
  residual is reported as a check)
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import mass_summary, support_contact, support_loads
from .model import FrameModel, build


def analyze(design: dict, g_factor: float = 1.0, step_mm: float | None = None,
            step_supports: list[str] | None = None) -> dict:
    summ = mass_summary(design)
    items = summ["items"]
    fm = build(design)
    weight = summ["mass_kg"] * STANDARD_GRAVITY * g_factor
    ground: dict[str, dict] = {}
    statics = None
    if step_mm is None:
        fm.add_gravity(items, g_factor)
        fm.restrain_on_wheels()
        fm.solve()
        for s in design["supports"]:
            node = fm.wheel_nodes[s["id"]]
            f, _ = fm.reaction(node)
            f = f + fm.spring_force(s["id"])
            ground[s["id"]] = {"point_mm": support_contact(s), "force_N": f, "contact": "ground"}
        restrained = [fm.wheel_nodes[s["id"]] for s in design["supports"]]
        residual = 0.0
    else:
        statics = support_loads(design, g_factor, step_mm, step_supports, summary=summ)
        fm.add_gravity(items, g_factor)
        for sid, sl in statics["supports"].items():
            fm.add_force_at(fm.wheel_nodes[sid], sl["point_mm"], sl["force_N"])
            ground[sid] = sl
        held = fm.restrain_free_body()
        fm.solve()
        restrained = [fm.wheel_nodes[s] for s in held]
        residual = max(float(np.linalg.norm(fm.reaction(n)[0])) for n in restrained) / weight

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

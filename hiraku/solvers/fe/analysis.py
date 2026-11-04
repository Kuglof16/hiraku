"""One static analysis with the checks around it: mesh quality and rigid-body freedom before the solve,
load balance and model mass after it.

run_static returns a plain dict:
  status   "solved" or "failed"
  reasons  why it failed (empty when solved)
  quality  the contract mesh.quality record
  rigid    the rigid-body check (free motions before factorising)
  results  contract `result` items: equilibrium_residual, load_balance_residual (with `intended`),
           model_mass, mass_difference (with `design_mass_kg`), free_rigid_body_modes
  solution the fe Solution (None when failed)
A mesh whose quality verdict is fail, or a model that can move as a rigid body, is not solved.
"""

from __future__ import annotations

import numpy as np

from . import precheck, quality
from .model import FEModel, ModelError, StaticSolver


def loaded_and_held_nodes(model: FEModel, F_nodes: np.ndarray) -> np.ndarray:
    lay = model.layout()
    fixed = model.fixed_dofs(lay)
    held = set()
    for d in fixed:
        held.add(int(np.searchsorted(lay.offsets, d, side="right") - 1))
    loaded = set(np.nonzero(np.any(F_nodes != 0.0, axis=1))[0].tolist())
    return np.array(sorted(held | loaded), int)


def run_static(model: FEModel, F, design_mass_kg: float | None = None, intended=None,
               expected_free_loops: int | None = None, method: str = "eliminate") -> dict:
    """F: per-node loads (N, 6). intended: the resultant (6,) the load case should have (about the origin)."""
    F = np.asarray(F, float)
    lay = model.layout()
    Fn = F if F.ndim == 2 else lay.node_array(F)
    q = quality.model_quality(model, loaded_and_held_nodes(model, Fn), expected_free_loops)
    out = {"status": "failed", "reasons": [], "quality": q["quality"], "rigid": None, "results": [],
           "solution": None}
    mass = precheck.mass_check(model, design_mass_kg)
    if q["fail"]:
        out["reasons"] = [f"mesh check: {r}" for r in q["fail"]]
        out["results"] = precheck.result_items(mass=mass)
        return out
    rb = precheck.rigid_body_check(model, lay)
    out["rigid"] = rb
    if rb["free_rigid_modes"]:
        out["reasons"] = [f"the model can move as a rigid body ({', '.join(rb['free_motions'])}); add supports"]
        out["results"] = precheck.result_items(mass=mass, rigid=rb)
        return out
    try:
        sol = StaticSolver(model, method=method, check_rigid=False).solve(Fn)
    except ModelError as e:
        out["reasons"] = [str(e)]
        out["results"] = precheck.result_items(mass=mass, rigid=rb)
        return out
    eq = precheck.equilibrium(sol, intended)
    out.update(status="solved", solution=sol, equilibrium=eq,
               results=precheck.result_items(eq, mass, rb))
    return out


__all__ = ["loaded_and_held_nodes", "run_static"]

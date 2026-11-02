"""K1 natural frequencies and mode shapes of the frame on its suspension.

Mass, by the solver notes' rule, only as density (members, consistent mass)
and node loads (parts, lumped translational mass). PyNite turns the
self-weight load of the mass combination into rho L A / gravity and node loads
into F / gravity, so a separate "mass" case is used with gravity 1 and node
loads equal to the part masses in tonnes (the model's mass unit with N, mm).
Part rotational inertia is not included. Frequencies are read from
model.frequencies (analyze_modal returns nothing).
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc.vehicle import DesignError, MassItem, mass_summary
from .analyze import rigid_fit
from .model import FrameModel, build, to_design

MASS_CASE = "mass"


def add_masses(fm: FrameModel, items: list[MassItem]) -> None:
    fm.model.add_member_self_weight("FY", -1.0, MASS_CASE)  # members: consistent mass from density
    for it in items:
        if it.carrier == "member":
            continue
        nodes = [fm.wheel_nodes[it.carried_by[0]]] if it.carrier == "support" else it.carried_by
        for n in nodes:
            fm.model.add_node_load(n, "FY", -it.mass_kg / 1000.0 / len(nodes), MASS_CASE)


def modes(design: dict, n_modes: int = 10) -> dict:
    """Frequencies (Hz) and mode shapes (design-axis displacement per frame node, largest = 1)."""
    summ = mass_summary(design)
    fm = build(design)
    add_masses(fm, summ["items"])
    fm.restrain_on_wheels()
    fm.model.add_load_combo(MASS_CASE, {MASS_CASE: 1.0})
    n_free = 6 * len(fm.model.nodes)
    k = max(1, min(n_modes, n_free - 2))
    try:
        fm.model.analyze_modal(num_modes=k, mass_combo_name=MASS_CASE, mass_direction="Y", gravity=1.0)
    except Exception as e:
        raise DesignError(f"the modal analysis could not be solved ({e}); check that every node is connected and the frame is supported") from None
    f = np.asarray(fm.model.frequencies, float)
    if not np.all(np.isfinite(f)):
        raise DesignError("the modal analysis returned NaN frequencies; the mass or stiffness is not valid (check member sizes and part masses)")
    order = np.argsort(f)
    shapes, flex = [], []
    pts = [fm.node_xyz(n) for n in fm.frame_nodes]
    for i in order:
        combo = f"Mode {i + 1}"
        u = {}
        for n in fm.frame_nodes:
            nd = fm.model.nodes[n]
            u[n] = to_design((nd.DX[combo], nd.DY[combo], nd.DZ[combo]))
        scale = max(float(np.linalg.norm(v)) for v in u.values()) or 1.0
        u = {n: v / scale for n, v in u.items()}
        t, th = rigid_fit(pts, [u[n] for n in fm.frame_nodes])
        resid = [u[n] - (t + np.cross(th, p)) for n, p in zip(fm.frame_nodes, pts)]
        total = math.sqrt(sum(float(v @ v) for v in u.values())) or 1.0
        flex.append(math.sqrt(sum(float(r @ r) for r in resid)) / total)
        shapes.append(u)
    return {"frequencies": f[order], "shapes": shapes, "flexibility": np.array(flex), "model": fm}

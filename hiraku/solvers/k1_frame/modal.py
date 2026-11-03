"""K1 natural frequencies and mode shapes of the frame on its suspension.

Mass, by the solver notes' rule, only as density (members, consistent mass)
and node loads (parts, lumped translational mass). PyNite turns the
self-weight load of the mass combination into rho L A / gravity and node loads
into F / gravity, so a separate "mass" case is used with gravity 1 and node
loads equal to the part masses in tonnes (the model's mass unit with N, mm).
Part rotational inertia is not included. Frequencies are read from
model.frequencies (analyze_modal returns nothing).

The eigenproblem is solved by solve_modal, PyNite's analyze_modal steps with
a fixed start vector, and each mode shape is signed so that its largest
component is positive: a run gives the same modes every time.
"""

from __future__ import annotations

import math

import numpy as np
from Pynite import Analysis
from scipy.sparse.linalg import eigsh

from ..k0_handcalc.vehicle import DesignError, MassItem, mass_summary
from .analyze import rigid_fit
from .model import FrameModel, build, to_design

MASS_CASE = "mass"
V0_SEED = 0  # seed of the eigen solver's start vector


def solve_modal(model, num_modes: int) -> None:
    """PyNite 3.2's analyze_modal (mass from the MASS_CASE combination along PyNite Y, gravity 1)
    with a fixed start vector for ARPACK. Without one scipy starts from a random vector, and the
    frequencies and mode shapes differed in their last digits and in sign from run to run."""
    Analysis._prepare_model(model, num_modes)
    d1, d2, D2 = Analysis._partition_D(model)
    K11 = Analysis._partition(model, model.Ke(MASS_CASE, False, True, sparse=True).tocsr(), d1, d2)[0]
    M11 = Analysis._partition(model, model.M(MASS_CASE, "Y", 1.0, False, sparse=True).tocsr(), d1, d2)[0]
    if M11.nnz == 0:
        raise ValueError("no mass terms found")
    v0 = np.random.default_rng(V0_SEED).random(K11.shape[0])
    values, vectors = eigsh(A=K11, k=num_modes, M=M11, sigma=0.0, which="LM", v0=v0)
    for i in range(len(values)):
        Analysis._store_displacements(model, vectors[:, i].reshape(-1, 1), D2, d1, d2, model.load_combos[f"Mode {i + 1}"])
    model.frequencies = np.sqrt(values) / (2 * np.pi)
    model.solution = "Modal"


def signed(u: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """The shape with its largest component positive (the first one, in node and axis order, of
    those within 1e-9 of the largest: mirror nodes of a symmetric frame tie)."""
    comps = np.concatenate([u[n] for n in u])
    big = float(np.max(np.abs(comps)))
    first = comps[int(np.argmax(np.abs(comps) >= big * (1.0 - 1e-9)))]
    return {n: -v for n, v in u.items()} if first < 0 else u


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
    fm.restrain_on_wheels(in_plane_at_frame=True)  # holds below the frame would sway the twist modes
    fm.model.add_load_combo(MASS_CASE, {MASS_CASE: 1.0})
    n_free = 6 * len(fm.model.nodes)
    k = max(1, min(n_modes, n_free - 2))
    try:
        solve_modal(fm.model, k)
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
        u = signed({n: v / scale for n, v in u.items()})
        t, th = rigid_fit(pts, [u[n] for n in fm.frame_nodes])
        resid = [u[n] - (t + np.cross(th, p)) for n, p in zip(fm.frame_nodes, pts)]
        total = math.sqrt(sum(float(v @ v) for v in u.values())) or 1.0
        flex.append(math.sqrt(sum(float(r @ r) for r in resid)) / total)
        shapes.append(u)
    return {"frequencies": f[order], "shapes": shapes, "flexibility": np.array(flex), "model": fm}

"""The known-answer cases of the beam frame (the tool checks them before it is used).

1. cantilever, small tip load, held still by damping (dynamic relaxation): tip deflection P L^3 / (3 E I);
2. the same cantilever's tip pushed slowly sideways past yield: the tip force levels off at the
   collapse load M_p / L (the plastic hinge at the root);
3. a short elastic tube carrying a large mass hits the wall: peak wall force v sqrt(k m) and contact
   time pi sqrt(m / k), k the tube's E A / L in series with the wall's penalty spring on the tube's
   front node, m the mass plus a third of the tube's (Rayleigh's spring-mass correction);
4. the same run: the change of the total momentum equals the wall's impulse.
All cases are built here from section and material values chosen for the test (not a design's).
"""

from __future__ import annotations

import math

import numpy as np

from . import sim
from .mesh import build_mesh, stable_dt

STEEL = {"name": "steel (test)", "E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 7850.0, "yield_MPa": 245.0,
         "source": "test value", "verified": False}
TUBE = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.3}}
SQUARE = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 2.3}}


def beam_design(length_mm: float, section: dict, material: dict, parts: list | None = None) -> dict:
    """One member from node A at the origin to node B on +x."""
    return {"design_version": 1, "name": "beam check", "variables": {}, "supports": [], "parts": parts or [],
            "nodes": [{"id": "A", "xyz_mm": [0.0, 0.0, 0.0]}, {"id": "B", "xyz_mm": [length_mm, 0.0, 0.0]}],
            "members": [{"id": "beam", "node_a": "A", "node_b": "B", "section": "s", "material": "m"}],
            "sections": {"s": section}, "materials": {"m": material}}


def _first_mode(mesh, L: float, EI: float) -> float:
    """rad/s, the cantilever's first bending frequency 1.875^2 sqrt(E I / (m L^4)) with its mass per length."""
    m = float(mesh.mass.sum()) / L
    return 1.8751 ** 2 * math.sqrt(EI / (m * L ** 4))


def cantilever_deflection(load_N: float = 50.0, length_mm: float = 1000.0, use_jit: bool = True) -> dict:
    """Tip deflection (m) of the clamped tube under a tip load down (-z), and the closed form."""
    d = beam_design(length_mm, TUBE, STEEL)
    mesh = build_mesh(d)
    dt = sim.time_step(mesh)
    st = sim.new_state(mesh)
    st.fix[0, :] = 1
    tip = mesh.design_nodes["B"]
    st.fext[tip, 2] = -load_N
    L = length_mm / 1000.0
    EI = mesh.prop[0, 3]  # about local z: the section's h (80 mm) is vertical
    w1 = _first_mode(mesh, L, EI)
    alpha = 2.0 * w1  # mass-proportional damping: every mode decays at alpha / 2 = w1
    steps = int(math.ceil(14.0 / w1 / dt))
    sim.advance(mesh, st, steps, dt, alpha=alpha, use_jit=use_jit)
    got = -(st.x[tip, 2] - mesh.x0[tip, 2])
    return {"got": float(got), "expected": load_N * L ** 3 / (3.0 * EI), "steps": steps, "EI": EI, "L": L,
            "elements": mesh.ne}


def cantilever_collapse(length_mm: float = 1000.0, push_mm: float = 45.0, speed: float = 0.05,
                        use_jit: bool = True) -> dict:
    """The clamped tube's tip pushed down (-z) at `speed` (m/s, ramped up over 0.1 s) to push_mm: the mean
    tip force over the last quarter of the push, and M_p / L."""
    d = beam_design(length_mm, TUBE, STEEL)
    mesh = build_mesh(d)
    dt = sim.time_step(mesh)
    st = sim.new_state(mesh)
    st.fix[0, :] = 1
    tip = mesh.design_nodes["B"]
    st.fix[tip, 2] = 1
    L = length_mm / 1000.0
    EI = mesh.prop[0, 3]
    w1 = _first_mode(mesh, L, EI)
    alpha = 0.05 * w1
    ramp = 0.1
    t_end = ramp / 2.0 + push_mm / 1000.0 / speed
    forces, pushes = [], []
    per = max(1, int(round(1e-3 / dt)))
    while st.t < t_end:
        v = speed * min(1.0, st.t / ramp)
        st.vpre[tip, 2] = -v
        sim.advance(mesh, st, per, dt, alpha=alpha, use_jit=use_jit)
        forces.append(-float(st.fint[tip, 2]))  # the push holding the tip on its path (internal force at the tip)
        pushes.append(-(st.x[tip, 2] - mesh.x0[tip, 2]))
    forces, pushes = np.array(forces), np.array(pushes)
    late = pushes >= 0.75 * pushes[-1]
    mp = mesh.prop[0, 6]
    return {"got": float(np.mean(forces[late])), "expected": mp / L, "Mp": mp, "push": float(pushes[-1]),
            "yield_push": mp / L * L ** 3 / (3.0 * EI), "max_hinge_deg": float(np.degrees(sim.hinge_rotations(st).max()))}


def mass_on_spring(mass_kg: float = 2000.0, length_mm: float = 500.0, speed: float = 1.0,
                   use_jit: bool = True) -> dict:
    """An elastic tube (yield set out of reach) with mass_kg at its rear node A hits the wall with its front
    node B at `speed`: peak wall force and contact time against v sqrt(k m), pi sqrt(m / k); the momentum
    change against the wall's impulse."""
    elastic = dict(STEEL, yield_MPa=1e9)
    part = {"id": "mass", "name": "mass", "kind": "cargo", "mass_kg": mass_kg, "carried_by": ["A"],
            "box": {"center_mm": [0.0, 0.0, 0.0], "size_mm": [10.0, 10.0, 10.0]}}
    mesh = build_mesh(beam_design(length_mm, SQUARE, elastic, [part]))
    dt = sim.time_step(mesh)
    m_tube = mesh.frame_mass
    front = mesh.design_nodes["B"]
    k_tube = mesh.prop[0, 0] / (length_mm / 1000.0)
    k_wall = sim.PENALTY * mesh.mass[front] / dt ** 2
    k = k_tube * k_wall / (k_tube + k_wall)
    m = mass_kg + m_tube / 3.0
    t_c = math.pi * math.sqrt(m / k)
    run = sim.run_crash(mesh, speed, time_limit=3.0 * t_c, use_jit=use_jit)
    f = -run.impulse[:, 0] / run.chunk
    on = np.nonzero(f > 0.0)[0]
    p0, p1 = run.momentum[0], run.momentum[-1]
    # keep running to the release: run_crash stops when the momentum turns; the contact time needs the rest
    st = run.final
    t_rel = None
    imp_after = np.zeros(3)
    while st.t < 3.0 * t_c:
        imp = sim.advance(mesh, st, run.per, dt, wall=run.wall_x, use_jit=use_jit)
        imp_after += imp
        if imp[0] < 0.0:
            t_rel = st.t
        elif t_rel is not None and st.t > t_rel + 0.2 * t_c:
            break
    p2 = mesh.mass @ st.v
    total_imp = run.impulse.sum(axis=0) + imp_after
    return {"peak_force": float(f.max()), "expected_force": speed * math.sqrt(k * m),
            "contact_time": (t_rel if t_rel is not None else float(run.t[on[-1]])) - 0.0, "expected_time": t_c,
            "k": k, "k_tube": k_tube, "k_wall": k_wall, "m": m, "dt": dt, "total_mass": float(mesh.mass.sum()),
            "momentum_change": float(p2[0] - p0[0]), "wall_impulse": float(total_imp[0]),
            "momentum_change_vec": (p2 - p0).tolist(), "wall_impulse_vec": total_imp.tolist(),
            "momentum_at_turn": float(p1[0])}


__all__ = ["beam_design", "cantilever_deflection", "cantilever_collapse", "mass_on_spring", "stable_dt"]

"""The known-answer cases of the beam frame (the tool checks them before it is used).

1. cantilever, small tip load, held still by damping (dynamic relaxation): tip deflection P L^3 / (3 E I);
2. the same cantilever's tip pushed slowly sideways past yield: the tip force levels off at the
   collapse load M_p / L (the plastic hinge at the root);
3. a short elastic tube carrying a large mass hits the wall: peak wall force v sqrt(k m) and contact
   time pi sqrt(m / k), k the tube's E A / L in series with the wall's penalty spring on the tube's
   front node, m the mass plus a third of the tube's (Rayleigh's spring-mass correction);
4. the same run: the change of the total momentum equals the wall's impulse;
5. two elastic tubes, not joined, on one axis, each with a large mass at its far end, one moving into the
   other (no wall): the contact force and time of the masses on the springs in series (the two tubes'
   E A / L and the contact's penalty spring between the two touching end nodes), the speeds after from
   momentum and energy (an elastic impact), the momentum kept;
6. a tube moving crosswise onto another resting tube (no gravity): it rebounds instead of passing through,
   the momentum is kept, and the least distance between the axes stays above the sum of the contact
   radii less the penetration bound from the penalty stiffness (all the relative kinetic energy in the
   contact spring);
7. a wheel node of radius r moving into the wall: the wall acts from x = wall - r on and stops it there
   (the wheel's wall contact is perfectly plastic).
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


def two_tube_design(ends_mm: list, section: dict, material: dict, parts: list | None = None) -> dict:
    """Two members not joined: t1 from A1 to B1, t2 from A2 to B2 (ends_mm: the four points in that order)."""
    names = ("A1", "B1", "A2", "B2")
    return {"design_version": 1, "name": "two tubes", "variables": {}, "supports": [], "parts": parts or [],
            "nodes": [{"id": k, "xyz_mm": list(map(float, p))} for k, p in zip(names, ends_mm)],
            "members": [{"id": "t1", "node_a": "A1", "node_b": "B1", "section": "s", "material": "m"},
                        {"id": "t2", "node_a": "A2", "node_b": "B2", "section": "s", "material": "m"}],
            "sections": {"s": section}, "materials": {"m": material}}


def _tube_nodes(mesh, member: str) -> list[int]:
    return sorted({int(k) for e in range(mesh.ne) if mesh.member_of[e] == member for k in mesh.con[e]})


def _point(mass_kg: float, node: str, at_mm) -> dict:
    return {"id": f"mass_{node}", "name": "mass", "kind": "cargo", "mass_kg": mass_kg, "carried_by": [node],
            "box": {"center_mm": list(map(float, at_mm)), "size_mm": [10.0, 10.0, 10.0]}}


def tubes_head_on(m1: float = 2000.0, m2: float = 1000.0, length_mm: float = 500.0, gap_mm: float = 100.0,
                  speed: float = 1.0, use_jit: bool = True) -> dict:
    """Tube t1 (A1 -> B1 on +x, m1 at A1) moving at `speed` into tube t2 (A2 -> B2 further on +x, m2 at B2) at
    rest; the front ends B1, A2 start gap_mm apart. The contact force on t2 (the sum of the contact forces on its
    nodes) every step, the time it is not zero, and each tube's speed (its momentum / its mass) at the end."""
    elastic = dict(STEEL, yield_MPa=1e9)
    h = gap_mm / 2.0
    ends = [(-h - length_mm, 0, 0), (-h, 0, 0), (h, 0, 0), (h + length_mm, 0, 0)]
    mesh = build_mesh(two_tube_design(ends, SQUARE, elastic, [_point(m1, "A1", ends[0]), _point(m2, "B2", ends[3])]))
    dt = sim.time_step(mesh)
    st = sim.new_state(mesh, (speed, 0.0, 0.0), dt)
    n1, n2 = _tube_nodes(mesh, "t1"), _tube_nodes(mesh, "t2")
    st.v[n2] = 0.0
    p0 = mesh.mass @ st.v
    b1, a2 = mesh.design_nodes["B1"], mesh.design_nodes["A2"]
    L = length_mm / 1000.0
    k_tube = mesh.prop[0, 0] / L
    k_c = sim.CONTACT_PENALTY / (dt * dt * (1.0 / mesh.mass[b1] + 1.0 / mesh.mass[a2]))
    k = 1.0 / (2.0 / k_tube + 1.0 / k_c)
    m_t = mesh.frame_mass / 2.0
    me1, me2 = m1 + m_t / 3.0, m2 + m_t / 3.0
    mu = me1 * me2 / (me1 + me2)
    t_c = math.pi * math.sqrt(mu / k)
    forces, on, t_first, t_last = [], 0, None, None
    t_end = (gap_mm / 1000.0 - 2.0 * float(mesh.crad[0])) / speed + 2.5 * t_c
    while st.t < t_end:
        sim.advance(mesh, st, 1, dt, use_jit=use_jit)
        f = float(st.fcon[n2, 0].sum())
        forces.append(f)
        if f != 0.0:
            on += 1
            t_last = st.t
            if t_first is None:
                t_first = st.t
    M1, M2 = float(mesh.mass[n1].sum()), float(mesh.mass[n2].sum())
    v1, v2 = float(mesh.mass[n1] @ st.v[n1, 0]) / M1, float(mesh.mass[n2] @ st.v[n2, 0]) / M2
    p1 = mesh.mass @ st.v
    return {"peak_force": max(forces), "expected_force": speed * math.sqrt(k * mu), "contact_time": on * dt,
            "expected_time": t_c, "k": k, "k_tube": k_tube, "k_contact": k_c, "mu": mu, "dt": dt,
            "v1": v1, "v2": v2, "expected_v1": (M1 - M2) / (M1 + M2) * speed, "expected_v2": 2.0 * M1 / (M1 + M2) * speed,
            "M1": M1, "M2": M2, "momentum_before": p0.tolist(), "momentum_after": p1.tolist(),
            "first_touch_gap": None if t_first is None else float(gap_mm / 1000.0 - speed * (t_first - dt)),
            "contact_distance": float(mesh.crad[0] * 2.0), "separated": forces[-1] == 0.0}


def tube_crosswise(length_mm: float = 600.0, gap_mm: float = 20.0, speed: float = 2.0, steps: int | None = None,
                   use_jit: bool = True) -> dict:
    """Tube t2 (along y, above) moving down (-z) at `speed` onto tube t1 (along x, at z = 0, at rest), each in 3
    elements so that the axes cross at the middles of the middle elements; the axes start (sum of the contact radii
    + gap_mm) apart. The least distance between the two tubes' axes over the run, the bound, the speeds after."""
    elastic = dict(STEEL, yield_MPa=1e9)
    r = SQUARE["dims_mm"]["b"] / 2.0
    h = 2.0 * r + gap_mm
    half = length_mm / 2.0
    ends = [(-half, 0, 0), (half, 0, 0), (0, -half, h), (0, half, h)]
    mesh = build_mesh(two_tube_design(ends, SQUARE, elastic), n_sub=3)
    dt = sim.time_step(mesh)
    st = sim.new_state(mesh, (0.0, 0.0, -speed), dt)
    n1, n2 = _tube_nodes(mesh, "t1"), _tube_nodes(mesh, "t2")
    st.v[n1] = 0.0
    p0 = mesh.mass @ st.v
    M1, M2 = float(mesh.mass[n1].sum()), float(mesh.mass[n2].sum())
    e1 = [e for e in range(mesh.ne) if mesh.member_of[e] == "t1"]
    e2 = [e for e in range(mesh.ne) if mesh.member_of[e] == "t2"]
    mid1 = [int(k) for k in mesh.con[e1[1]]]
    mid2 = [int(k) for k in mesh.con[e2[1]]]
    # contact point at the middles (s = t = 1/2): 1 / m_contact = 4 x (1/2)^2 / m_node, m_node = each middle node's mass
    minv = sum(0.25 / float(mesh.mass[k]) for k in mid1 + mid2)
    k_c = sim.CONTACT_PENALTY / (dt * dt * minv)
    mu = M1 * M2 / (M1 + M2)
    bound = speed * math.sqrt(mu / k_c)
    from .kernel import closest

    least, t_touch, t_free = math.inf, None, None
    n = int(steps) if steps is not None else int(math.ceil((gap_mm / 1000.0 / speed) * 3.0 / dt))
    for _ in range(n):
        sim.advance(mesh, st, 1, dt, use_jit=use_jit)
        for a in e1:
            for b in e2:
                i, j = mesh.con[a]
                k, l = mesh.con[b]
                s, t = closest(st.x, int(i), int(j), int(k), int(l))
                d = float(np.linalg.norm(st.x[i] + s * (st.x[j] - st.x[i]) - st.x[k] - t * (st.x[l] - st.x[k])))
                least = min(least, d)
        touching = bool(np.any(st.fcon != 0.0))
        if touching and t_touch is None:
            t_touch = st.t
        if t_touch is not None and not touching and t_free is None:
            t_free = st.t
    v1, v2 = float(mesh.mass[n1] @ st.v[n1, 2]) / M1, float(mesh.mass[n2] @ st.v[n2, 2]) / M2
    return {"least": least, "contact_distance": 2.0 * r / 1000.0, "bound": bound, "k_contact": k_c, "mu": mu,
            "dt": dt, "v1": v1, "v2": v2, "speed": speed, "momentum_before": p0.tolist(),
            "momentum_after": (mesh.mass @ st.v).tolist(), "touched": t_touch, "released": t_free,
            "M1": M1, "M2": M2, "mesh": mesh, "state": st}


def wheel_into_wall(radius: float = 0.3, wall: float = 1.0, speed: float = 1.0, mass: float = 18.0,
                    use_jit: bool = True) -> dict:
    """One node of radius `radius` (a wheel) moving at `speed` (+x) into the wall at x = wall, from 2 mm short of
    touching: the node's x at the start of the first step the wall pushes, the last x before it with no push, the
    deepest x over the run and the speed after (the wheel's wall contact is perfectly plastic: kernel step 7)."""
    from .mesh import Mesh

    mesh = Mesh(["wheel~centre"], np.array([[wall - radius - 0.002, 0.0, 0.0]]), np.array([mass]), np.array([1.0]),
                np.zeros((0, 2), dtype=np.int64), np.zeros(0), np.zeros((0, 3, 3)), np.zeros((0, 8)), [], {},
                nrad=np.array([radius]), cdist=np.zeros((0, 0)))
    dt = 1e-5
    st = sim.new_state(mesh, (speed, 0.0, 0.0), dt)
    first, before, deepest = None, None, -math.inf
    for _ in range(1000):
        x = float(st.x[0, 0])
        imp = sim.advance(mesh, st, 1, dt, wall=wall, use_jit=use_jit)
        deepest = max(deepest, x, float(st.x[0, 0]))
        if imp[0] != 0.0 and first is None:
            first = x
        if first is None:
            before = x
    return {"first_touch_x": first, "last_free_x": before, "expected": wall - radius, "deepest": deepest,
            "speed_after": float(st.v[0, 0]), "dt": dt, "speed": speed}


__all__ = ["beam_design", "cantilever_deflection", "cantilever_collapse", "mass_on_spring", "stable_dt",
           "tubes_head_on", "tube_crosswise", "two_tube_design", "wheel_into_wall"]

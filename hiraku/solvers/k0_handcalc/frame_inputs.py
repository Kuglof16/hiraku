"""Inputs for the frame hand calculations read off a design by code.

rail_joint_inputs() looks at the rear axle joint of one rail: the rail
member ahead of it, the cross member there, the wheel's sideways offset from
the rail, the rail height, and the hogging moment at the joint from what the
rail carries behind the axle (part weights shared equally over their
carried_by nodes, members' own weight). Moments are taken about the joint
from the x positions; this is plain statics, not the frame model.

end_cross_member_inputs() looks at the rear overhang of a ladder frame (the
rails behind the rear axle joints and the cross member at their ends) for
frame_static.end_cross_member_stress, with the overhang's twist misfit from
the ladder's warping torsion (ladder_twist_rate).

joint_wall_inputs() shares the wheels' twisting moments among the cross
members along a torsion chain of the ladder's symmetric half, with the
joints' wall rotation from frame_static.joint_wall_bending, for that
formula at the joint where the wall's bending is largest.

portal_frame_inputs() reads a roll bar (members rollbar_post_L/R and
rollbar_top, optionally rollbar_foot_L/R outriggers) as a portal frame for
frame_static.portal_frame_side / _rear / _top: post and beam lengths, second
moments, plastic moments, and the feet's rotation from the outriggers and the
rail they stand on.
"""

from __future__ import annotations

import numpy as np
from scipy.linalg import solve_banded

from ...contract.loading import on_board
from . import sections
from .units import STANDARD_GRAVITY, quantity
from .vehicle import DesignError, mass_summary, member_props, node_table


def _rect_dims(section: dict) -> tuple[float, float, float]:
    if section.get("shape") != "rect_tube":
        raise DesignError("the rail-joint formula is for rectangular tubes (shape rect_tube)")
    d = section["dims_mm"]
    return float(d["b"]), float(d["h"]), float(d["t"])


def overhang_moment(design: dict, node: str) -> float:
    """Hogging moment (N*mm) at a rail node from the loads on that rail behind it (smaller x)."""
    nodes = node_table(design)
    props = member_props(design)
    x0, y0 = nodes[node][0], nodes[node][1]
    on_rail = {n for n, p in nodes.items() if abs(p[1] - y0) < 1e-6}
    M = 0.0
    for part in design.get("parts", []):
        carried = part.get("carried_by") or []
        if not carried:
            continue
        P = float(part["mass_kg"]) * STANDARD_GRAVITY / len(carried)
        for n in carried:
            if n in on_rail and nodes[n][0] < x0 - 1e-9:
                M += P * (x0 - nodes[n][0])
    for mid, mp in props.items():
        w = float(mp["material"]["density_kg_m3"]) * mp["section"]["A"] * 1e-9 * STANDARD_GRAVITY  # N/mm
        a, b = mp["a"], mp["b"]
        ends = [p for p in (a, b) if abs(p[1] - y0) < 1e-6]
        if len(ends) == 2:  # a rail piece on this side: its own weight where it lies behind the node
            lo, hi = sorted((a[0], b[0]))
            hi = min(hi, x0)
            if hi > lo:
                M += w * (hi - lo) * (x0 - (lo + hi) / 2.0)
        elif len(ends) == 1 and ends[0][0] < x0 - 1e-9:  # a cross member: half its weight at this end
            M += w * mp["length"] / 2.0 * (x0 - ends[0][0])
    return M


def rail_joint_inputs(design: dict, side: str | None = None, loading: str | None = None) -> dict:
    """Quantities for frame_static.rail_joint_stress at the rear axle joint (all but F and W).

    side: "L" or "R" (the sign of y of the rail); default the side with the larger overhang moment.
    loading: who and what is on board (default: the parts as listed).
    """
    if loading is not None:
        design = on_board(design, loading)
    nodes = node_table(design)
    props = member_props(design)
    sup = design.get("supports", [])
    if not sup:
        raise DesignError("the design has no supports")
    x_rear = min(s["position_mm"][0] for s in sup)
    rear = [s for s in sup if abs(s["position_mm"][0] - x_rear) < 1e-6 and s.get("frame_node")]
    if not rear:
        raise DesignError("the rear supports have no frame_node, so the rail joint is unknown")
    if side is not None:
        rear = [s for s in rear if (s["position_mm"][1] > 0) == (side == "L")] or rear
    best = None
    for s in rear:
        n = s["frame_node"]
        p = nodes[n]
        ahead = cross = None
        for mid, mp in props.items():
            m = mp["member"]
            if n not in (m["node_a"], m["node_b"]):
                continue
            other = mp["b"] if m["node_a"] == n else mp["a"]
            if abs(other[1] - p[1]) < 1e-6 and other[0] > p[0]:
                ahead = mp
            elif abs(other[0] - p[0]) < 1e-6 and abs(other[1] - p[1]) > 1e-6:
                cross = mp
        if ahead is None or cross is None:
            continue
        M = overhang_moment(design, n)
        if best is None or M > best[0]:
            best = (M, s, n, ahead, cross)
    if best is None:
        raise DesignError("no rear axle joint with a rail ahead and a cross member was found")
    M, s, n, ahead, cross = best
    b, h, t = _rect_dims(design["sections"][ahead["member"]["section"]])
    bc, hc, tc = _rect_dims(design["sections"][cross["member"]["section"]])
    mat = ahead["material"]
    contact_z = float(s["position_mm"][2]) - float(s.get("radius_mm", 0.0))
    return {
        "inputs": {
            "M_oh": quantity(M, "N*mm"),
            "z": quantity(float(nodes[n][2]) - contact_z, "mm"),
            "e": quantity(abs(float(s["position_mm"][1]) - float(nodes[n][1])), "mm"),
            "L1": quantity(ahead["length"], "mm"),
            "L_c": quantity(cross["length"], "mm"),
            "E": quantity(float(mat["E_MPa"]), "MPa"),
            "nu": quantity(float(mat["nu"]), "1"),
            "b": quantity(b, "mm"), "h": quantity(h, "mm"), "t": quantity(t, "mm"),
            "b_c": quantity(bc, "mm"), "h_c": quantity(hc, "mm"), "t_c": quantity(tc, "mm"),
        },
        "support": s["id"],
        "node": n,
        "rail_member": ahead["member"]["id"],
        "cross_member": cross["member"]["id"],
    }


TWIST_POINTS = 2001  # stations along the frame for the warping-torsion equation


def roll_inertia_along(design: dict) -> list[tuple[float, float]]:
    """(x mm, roll inertia kg*mm^2 about the x axis through the centre of gravity) pieces: parts shared
    equally over their carried_by nodes (as the beam model carries them), members at their middle,
    unsprung parts at their support."""
    nodes = node_table(design)
    summ = mass_summary(design)
    cg = summ["cg_mm"]
    sup_x = {s["id"]: float(s["position_mm"][0]) for s in design.get("supports", [])}
    out = []
    for it in summ["items"]:
        own = float(it.inertia_kg_mm2[0, 0]) if it.inertia_kg_mm2 is not None else 0.0
        I = it.mass_kg * ((it.cg_mm[1] - cg[1]) ** 2 + (it.cg_mm[2] - cg[2]) ** 2) + own
        if it.carrier == "nodes":
            out += [(float(nodes[n][0]), I / len(it.carried_by)) for n in it.carried_by]
        elif it.carrier == "support":
            out.append((sup_x[it.carried_by[0]], I))
        else:
            out.append((float(it.cg_mm[0]), I))
    return out


def ladder_twist_rate(pieces: list[tuple[float, float]], x_rear_end: float, x_front_end: float, GJ: float, EIw: float,
                      x_in: float, M_in: float, x_at: float) -> float:
    """Twist rate psi' (rad/mm) of a ladder frame at x_at for a roll moment M_in (N*mm) put in at x_in and taken
    by the roll inertia of the pieces (inertia relief).

    Warping torsion of the ladder as a thin-walled beam: EIw psi''' - GJ psi' = -T(x), T the torque carried
    across x (the moments behind it), with no bimoment (psi'' = 0) at the free ends. GJ: St Venant stiffness
    (rails' torsion, cross members' twist smeared); EIw: the rails bending opposite ways. Finite differences.
    """
    total = sum(I for _, I in pieces)
    alpha = M_in / total
    X = np.linspace(x_rear_end, x_front_end, TWIST_POINTS)
    T = np.where(X > x_in, M_in, 0.0)
    for x, I in pieces:
        T = T - np.where(X > x, alpha * I, 0.0)
    h = X[1] - X[0]
    n = len(X)
    ab = np.zeros((3, n))  # banded: upper, diagonal, lower
    ab[0, 2:] = EIw / h**2
    ab[1, 1:-1] = -2.0 * EIw / h**2 - GJ
    ab[2, :-2] = EIw / h**2
    rhs = -T
    ab[1, 0], ab[0, 1], rhs[0] = -1.0, 1.0, 0.0  # psi'' = 0 at the rear end
    ab[1, -1], ab[2, -2], rhs[-1] = 1.0, -1.0, 0.0  # and at the front end
    phi = solve_banded((1, 1), ab, rhs)
    return float(np.interp(x_at, X, phi))


def ladder_twist_inputs(design: dict, loading: str | None = None) -> dict:
    """Quantities for modal.ladder_twist_mode: the ladder's rails (members along x) and cross members (members
    joining the two rail lines), the roll inertia along it (roll_inertia_along: its total, centroid x_I and
    spread r_x), and the front and rear axles' wheel rates, tracks and distances from x_I."""
    import math

    if loading is not None:
        design = on_board(design, loading)
    nodes = node_table(design)
    props = member_props(design)
    rails = [mp for mp in props.values() if abs(mp["a"][1] - mp["b"][1]) < 1e-6 and abs(mp["a"][2] - mp["b"][2]) < 1e-6]
    z_rail = float(rails[0]["a"][2]) if rails else 0.0
    # cross members join the two rails at the rails' height (a roll bar's top beam is not one)
    cross = [mp for mp in props.values() if abs(mp["a"][1] + mp["b"][1]) < 1e-6 and abs(mp["a"][1]) > 1e-6
             and abs(mp["a"][2] - z_rail) < 1e-6 and abs(mp["b"][2] - z_rail) < 1e-6]
    if not rails or not cross:
        raise DesignError("the twist hand calculation needs a ladder frame: rails along x and cross members joining them")
    sup = design.get("supports", [])
    x_front = max(float(s["position_mm"][0]) for s in sup)
    front = sorted((s for s in sup if abs(s["position_mm"][0] - x_front) < 1e-6), key=lambda s: -s["position_mm"][1])
    rear = sorted((s for s in sup if abs(s["position_mm"][0] - x_front) > 1e-6), key=lambda s: -s["position_mm"][1])
    if len(front) < 2 or len(rear) < 2:
        raise DesignError("the twist hand calculation needs a left and a right wheel at the front and at the rear")
    pieces = roll_inertia_along(design)
    I0 = sum(I for _, I in pieces)
    x_I = sum(I * x for x, I in pieces) / I0
    r_x = math.sqrt(sum(I * (x - x_I) ** 2 for x, I in pieces) / I0)
    mat = rails[0]["material"]
    xs = [float(nodes[n][0]) for mp in rails for n in (mp["member"]["node_a"], mp["member"]["node_b"])]
    rate = lambda s: float(s.get("suspension", {}).get("stiffness_N_per_mm") or 0.0)  # noqa: E731
    return {"inputs": {
        "E": quantity(float(mat["E_MPa"]), "MPa"), "nu": quantity(float(mat["nu"]), "1"),
        "J_r": quantity(rails[0]["section"]["J"], "mm^4"),
        "J_c": quantity(sum(mp["section"]["J"] for mp in cross) / len(cross), "mm^4"),
        "n_c": quantity(float(len(cross)), "1"),
        "w": quantity(sum(mp["length"] for mp in cross) / len(cross), "mm"), "L": quantity(max(xs) - min(xs), "mm"),
        "I0": quantity(I0 * 1e-6, "kg*m^2"), "r_x": quantity(r_x, "mm"),
        "k_f": quantity(rate(front[0]), "N/mm"), "k_r": quantity(rate(rear[0]), "N/mm"),
        "t_f": quantity(float(front[0]["position_mm"][1] - front[-1]["position_mm"][1]), "mm"),
        "t_r": quantity(float(rear[0]["position_mm"][1] - rear[-1]["position_mm"][1]), "mm"),
        "d_f": quantity(x_front - x_I, "mm"), "d_r": quantity(float(rear[0]["position_mm"][0]) - x_I, "mm"),
    }}


def _rail_torques(design: dict, summ: dict, ground: dict, rail_y: float, rail_z: float, g_factor: float) -> dict[str, float]:
    """Moment about the rail's axis (the line y = rail_y, z = rail_z along x; N*mm, right-handed about +x) put
    into each frame node on that rail: the ground forces at the supports whose frame node is on it, the
    unsprung weights riding with them, and the moments of the offsets of parts carried by frame nodes
    (as K1 shares them: the moment about the centroid of the carried_by nodes, equally per node)."""
    G0 = STANDARD_GRAVITY
    nodes = node_table(design)
    on = {n for n, p in nodes.items() if abs(p[1] - rail_y) < 1e-6 and abs(p[2] - rail_z) < 1e-6}
    sup_node = {s["id"]: s.get("frame_node") for s in design.get("supports", [])}
    out: dict[str, float] = {}

    def add(node, point, force):
        r = np.asarray(point, float) - np.array([0.0, rail_y, rail_z])
        out[node] = out.get(node, 0.0) + float(np.cross(r, np.asarray(force, float))[0])

    for sid, g in ground.items():
        n = sup_node.get(sid)
        if n in on:
            add(n, g["point_mm"], g["force_N"])
    for it in summ["items"]:
        f = np.array([0.0, 0.0, -it.mass_kg * G0 * g_factor])
        if it.carrier == "support":
            n = sup_node.get(it.carried_by[0])
            if n in on:
                add(n, it.cg_mm, f)
        elif it.carrier == "nodes":
            c = np.mean([nodes[n] for n in it.carried_by], axis=0)
            mx = float(np.cross(it.cg_mm - c, f)[0]) / len(it.carried_by)
            for n in it.carried_by:
                if n in on:
                    out[n] = out.get(n, 0.0) + mx
    return out


def joint_wall_inputs(design: dict, loading: str | None = None, step_mm: float | None = None,
                      step_supports: list[str] | None = None, g_factor: float = 1.0) -> dict:
    """Quantities for frame_static.joint_wall_bending at the joint where it is largest, and every joint's numbers.

    The ladder's symmetric half: each rail twists in St Venant torsion (G J / length between stations); at
    every station the cross member resists the twist in single curvature (2 E I / w) in series with the
    rotation of the rail's side wall at its end (theta_per_kNm of joint_wall_bending: 0 for a through joint,
    the gussets' spread where the design has gusset parts at the node). The moments about the rail's axis
    from the wheels (ground forces at rest or on a step from K0 statics, support_loads, and the unsprung
    weights at their offsets) are put in at the supports' frame nodes; the left and right rails' moments are
    averaged as a mirror pair (the antisymmetric rest, the ladder's twist, is left out). Each cross member's
    end moment is its spring times the rail's twist there; joint_wall_bending then gives each joint's stress.
    The joints at the supports' frame nodes where the rail runs on (the wheel mounts) are in the chain but not
    compared: frame_shell leaves the elements inside the mounts out of its peak (the bracket covers the wall).
    """
    from .library_joint import joint_wall
    from .vehicle import support_loads

    if loading is not None:
        design = on_board(design, loading)
    nodes = node_table(design)
    props = member_props(design)
    summ = mass_summary(design)
    rails = [mp for mp in props.values() if abs(mp["a"][1] - mp["b"][1]) < 1e-6 and abs(mp["a"][2] - mp["b"][2]) < 1e-6
             and abs(mp["a"][0] - mp["b"][0]) > 1e-6 and mp["a"][1] > 0]
    if not rails:
        raise DesignError("the joint wall calculation needs a ladder frame: rails along x")
    y_r, z_r = float(rails[0]["a"][1]), float(rails[0]["a"][2])
    left = sorted({n for n, p in nodes.items() if abs(p[1] - y_r) < 1e-6 and abs(p[2] - z_r) < 1e-6},
                  key=lambda n: -nodes[n][0])
    stations = []
    for n in left:
        p = nodes[n]
        mirror = next((m for m, q in nodes.items() if abs(q[0] - p[0]) < 1e-6 and abs(q[1] + p[1]) < 1e-6
                       and abs(q[2] - p[2]) < 1e-6), None)
        cross = _member_between(props, n, mirror) if mirror else None
        if cross is not None:
            stations.append((n, mirror, cross))
    if len(stations) < 2:
        raise DesignError("the joint wall calculation needs cross members joining the two rails at two stations or more")
    rail = rails[0]
    b, h, t = _rect_dims(design["sections"][rail["member"]["section"]])
    mat = rail["material"]
    E, nu = float(mat["E_MPa"]), float(mat["nu"])
    G = E / (2.0 * (1.0 + nu))
    GJ = G * sections.properties(design["sections"][rail["member"]["section"]])["J"]
    through_all = float(((design.get("variables") or {}).get("cross_members_through") or {}).get("value", 0.0)) == 1.0
    ends = {left[0], left[-1]}
    legs = {}
    for p in design.get("parts", []):
        if p.get("kind") == "gusset" and len(p.get("carried_by") or []) == 1:
            legs[p["carried_by"][0]] = float(p["box"]["size_mm"][0])

    rows = []
    for n, m, cross in stations:
        bc, hc, tc = _rect_dims(design["sections"][cross["member"]["section"]])
        open_end = n in ends
        through = through_all and not open_end and (hc - tc) < (h - t)
        v = {"b": b, "h": h, "t": t, "b_c": bc, "h_c": hc, "t_c": tc, "E": E, "nu": nu,
             "leg": 0.0 if through else legs.get(n, 0.0), "through": 1.0 if through else 0.0,
             "open_end": 1.0 if open_end else 0.0}
        theta = joint_wall(dict(v, M=1.0e6))["theta_per_kNm"] if not through else 0.0
        EI = E * sections.properties(design["sections"][cross["member"]["section"]])["Iz"]
        k = 1.0 / (cross["length"] / (2.0 * EI) + theta / 1.0e6)
        rows.append({"node": n, "mirror": m, "cross_member": cross["member"]["id"], "x": float(nodes[n][0]), "k": k,
                     "values": v, "theta_per_kNm": theta})

    # ground forces and the twisting moments on each rail
    if step_mm is not None:
        st = support_loads(design, g_factor, step_mm, step_supports, summary=summ)
    else:
        st = support_loads(design, g_factor, summary=summ)
    T_left = _rail_torques(design, summ, st["supports"], y_r, z_r, g_factor)
    T_right = _rail_torques(design, summ, st["supports"], -y_r, z_r, g_factor)
    by_x = {round(float(nodes[n][0]), 6): n for n in left}
    T_sym = {n: 0.0 for n in left}
    for n, v in T_left.items():
        T_sym[n] += v / 2.0
    for n, v in T_right.items():  # the mirror of a right-rail moment about +x is minus it on the left rail
        T_sym[by_x[round(float(nodes[n][0]), 6)]] -= v / 2.0

    # torsion chain along the left rail: nodes in x order, rail pieces between, springs at the stations
    idx = {n: i for i, n in enumerate(left)}
    K = np.zeros((len(left), len(left)))
    for a_, b_ in zip(left, left[1:]):
        k = GJ / abs(float(nodes[a_][0]) - float(nodes[b_][0]))
        i, j = idx[a_], idx[b_]
        K[i, i] += k
        K[j, j] += k
        K[i, j] -= k
        K[j, i] -= k
    for r in rows:
        K[idx[r["node"]], idx[r["node"]]] += r["k"]
    T = np.array([T_sym[n] for n in left])
    phi = np.linalg.solve(K, T)
    for r in rows:
        r["M"] = abs(r["k"] * phi[idx[r["node"]]])
        out = joint_wall(dict(r["values"], M=r["M"]))
        r["sigma"], r["sigma_open_end"] = out["sigma"], out["sigma_open_end"]
    mounts = {s.get("frame_node") for s in design.get("supports", [])} - ends
    candidates = [r for r in rows if r["node"] not in mounts]
    if not candidates:
        raise DesignError("every cross member joint is at a wheel mount, where the wall is covered; nothing to compare")
    worst = max(candidates, key=lambda r: r["sigma"])
    v = worst["values"]
    q = lambda x, u: quantity(float(x), u)  # noqa: E731
    return {
        "inputs": {"M": q(worst["M"], "N*mm"), "b": q(v["b"], "mm"), "h": q(v["h"], "mm"), "t": q(v["t"], "mm"),
                   "b_c": q(v["b_c"], "mm"), "h_c": q(v["h_c"], "mm"), "t_c": q(v["t_c"], "mm"), "E": q(v["E"], "MPa"),
                   "nu": q(v["nu"], "1"), "leg": q(v["leg"], "mm"), "through": q(v["through"], "1"),
                   "open_end": q(v["open_end"], "1")},
        "joint": worst["node"],
        "cross_member": worst["cross_member"],
        "joints": [{"node": r["node"], "cross_member": r["cross_member"], "M_Nmm": r["M"], "sigma_MPa": r["sigma"],
                    "sigma_open_end_MPa": r["sigma_open_end"], "theta_per_kNm": r["theta_per_kNm"],
                    "at_wheel_mount": r["node"] in mounts,
                    "kind": ("through" if r["values"]["through"] else "gusset" if r["values"]["leg"] else "butt")
                            + (" (rail end)" if r["values"]["open_end"] else "")} for r in rows],
        "twisting_moments_Nmm": {n: float(T_sym[n]) for n in left},
        "mode": st["mode"],
    }


def _member_between(props: dict, n1: str, n2: str):
    return next((mp for mp in props.values() if {mp["member"]["node_a"], mp["member"]["node_b"]} == {n1, n2}), None)


def end_cross_member_inputs(design: dict, loading: str | None = None) -> dict:
    """Quantities for frame_static.end_cross_member_stress: the rear overhang of a ladder frame.

    The rear supports' frame nodes are the rail joints; behind each a rail member ends at a node, and a
    cross member joins those two end nodes. misfit_rear / misfit_front: the twist the overhang carries on
    over its length (rad) for 1 kN up at the left and down at the right rear / front wheel, from the
    ladder's warping torsion with the roll inertia along the frame (ladder_twist_rate).
    """
    if loading is not None:
        design = on_board(design, loading)
    nodes = node_table(design)
    props = member_props(design)
    sup = design.get("supports", [])
    if not sup:
        raise DesignError("the design has no supports")
    x_rear = min(s["position_mm"][0] for s in sup)
    rear = sorted((s for s in sup if abs(s["position_mm"][0] - x_rear) < 1e-6 and s.get("frame_node")),
                  key=lambda s: -s["position_mm"][1])
    front = sorted((s for s in sup if abs(s["position_mm"][0] - x_rear) > 1e-6), key=lambda s: -s["position_mm"][1])
    if len(rear) != 2 or len(front) < 2:
        raise DesignError("the end cross member calculation needs two rear supports on frame nodes and two front ones")

    def along(n, behind):  # the rail member from node n backwards (behind) or forwards
        p = nodes[n]
        for mp in props.values():
            m = mp["member"]
            if n in (m["node_a"], m["node_b"]):
                other = m["node_b"] if m["node_a"] == n else m["node_a"]
                q = nodes[other]
                if abs(q[1] - p[1]) < 1e-6 and abs(q[2] - p[2]) < 1e-6 and (q[0] < p[0]) == behind:
                    return mp, other
        return None, None

    joints = [s["frame_node"] for s in rear]
    back = [along(n, True) for n in joints]
    ahead = [along(n, False) for n in joints]
    if any(mp is None for mp, _ in back + ahead):
        raise DesignError("each rear rail joint needs a rail member ahead of it and one behind it (an overhang)")
    end = _member_between(props, back[0][1], back[1][1])
    joint = _member_between(props, joints[0], joints[1])
    if end is None or joint is None:
        raise DesignError("the overhang needs a cross member joining the rail ends and one joining the rear rail joints")
    rail, fwd = back[0][0], ahead[0][0]
    b, h, t = _rect_dims(design["sections"][rail["member"]["section"]])
    bc, hc, tc = _rect_dims(design["sections"][end["member"]["section"]])
    bj, hj, tj = _rect_dims(design["sections"][joint["member"]["section"]])
    mat = rail["material"]
    E, nu = float(mat["E_MPa"]), float(mat["nu"])
    G = E / (2.0 * (1.0 + nu))
    w = abs(float(nodes[joints[0]][1]) - float(nodes[joints[1]][1]))
    e = abs(float(rear[0]["position_mm"][1])) - abs(float(nodes[joints[0]][1]))

    # warping torsion of the whole ladder (rails at +-w/2; cross members are members joining the two rails)
    rail_nodes = [n for n, p in nodes.items() if abs(abs(p[1]) - w / 2.0) < 1e-6]
    xr = [float(nodes[n][0]) for n in rail_nodes]
    x_rear_end, x_front_end = min(xr), max(xr)
    rail_J = sections.properties(design["sections"][rail["member"]["section"]])["J"]
    cross_GJw = sum(G * mp["section"]["J"] * mp["length"] for mp in props.values()
                    if abs(abs(mp["a"][1]) - w / 2.0) < 1e-6 and abs(abs(mp["b"][1]) - w / 2.0) < 1e-6
                    and abs(mp["a"][1] - mp["b"][1]) > 1e-6)
    GJ = 2.0 * G * rail_J + cross_GJw / (x_front_end - x_rear_end)
    EIw = E * sections.properties(design["sections"][rail["member"]["section"]])["Iz"] * w**2 / 2.0
    pieces = roll_inertia_along(design)
    x_joint = float(nodes[joints[0]][0])
    a = rail["length"]

    def misfit(pair):
        track = float(pair[0]["position_mm"][1]) - float(pair[-1]["position_mm"][1])
        x_in = float(pair[0]["position_mm"][0])
        return abs(ladder_twist_rate(pieces, x_rear_end, x_front_end, GJ, EIw, x_in, 1000.0 * track, x_joint)) * a

    return {
        "inputs": {
            "e": quantity(e, "mm"), "a": quantity(a, "mm"), "w": quantity(w, "mm"), "L1": quantity(fwd["length"], "mm"),
            "E": quantity(E, "MPa"), "nu": quantity(nu, "1"),
            "b": quantity(b, "mm"), "h": quantity(h, "mm"), "t": quantity(t, "mm"),
            "b_c": quantity(bc, "mm"), "h_c": quantity(hc, "mm"), "t_c": quantity(tc, "mm"),
            "b_j": quantity(bj, "mm"), "h_j": quantity(hj, "mm"), "t_j": quantity(tj, "mm"),
            "misfit_rear": quantity(misfit(rear), "rad"), "misfit_front": quantity(misfit(front), "rad"),
        },
        "supports": [s["id"] for s in rear] + [s["id"] for s in front],
        "end_cross_member": end["member"]["id"],
        "joint_cross_member": joint["member"]["id"],
    }


def _left_rail(design: dict) -> tuple[dict, dict, list[str], float, float]:
    """(nodes, member props, the left rail's nodes front to back, rail y, rail z) of a ladder frame."""
    nodes = node_table(design)
    props = member_props(design)
    rails = [mp for mp in props.values() if abs(mp["a"][1] - mp["b"][1]) < 1e-6 and abs(mp["a"][2] - mp["b"][2]) < 1e-6
             and abs(mp["a"][0] - mp["b"][0]) > 1e-6 and mp["a"][1] > 0]
    if not rails:
        raise DesignError("the calculation needs a ladder frame: rails along x")
    y_r, z_r = float(rails[0]["a"][1]), float(rails[0]["a"][2])
    left = sorted({n for n, p in nodes.items() if abs(p[1] - y_r) < 1e-6 and abs(p[2] - z_r) < 1e-6},
                  key=lambda n: -nodes[n][0])
    return nodes, props, left, y_r, z_r


def _mirror(nodes: dict, n: str) -> str | None:
    p = nodes[n]
    return next((m for m, q in nodes.items() if abs(q[0] - p[0]) < 1e-6 and abs(q[1] + p[1]) < 1e-6
                 and abs(q[2] - p[2]) < 1e-6), None)


def _rail_piece(props: dict, a: str, b: str):
    return _member_between(props, a, b)


def rail_springs(design: dict, node: str) -> dict:
    """Rotational springs (N*mm/rad) of the left rail at one of its nodes against the rest of the ladder, for
    moments that come in at the node and its mirror on the right rail as an antisymmetric pair (left-right
    opposite pitch, alike roll and yaw). Each is a chain along the rail (nodes front to back, the ends free):

    - k_x (roll): the rail's St Venant torsion G J / L between nodes; at each station (a cross member joining the
      node and its mirror at the rails' height) the cross member in double curvature, 6 E I / w (vertical bending);
    - k_z (yaw): the rail bent sideways, E I / L between nodes (the rail's moment E I times the change of its yaw);
      the cross member in double curvature sideways, 6 E I / w;
    - k_y (the opposite pitch, the ladder's warping): the rail bent vertically, E I / L between nodes; the cross
      member twisted by the two rails' opposite pitch, 2 G J / w; and the rails' own torsion, which the warping
      twists, 4 G J / w^2 per unit length (from E I_w psi''' - G J psi' with I_w = I w^2 / 2, psi' = 2 theta / w).
    """
    nodes, props, left, y_r, z_r = _left_rail(design)
    if node not in left:
        raise DesignError(f"node {node} is not on the left rail")
    w = 2.0 * y_r
    idx = {n: i for i, n in enumerate(left)}
    K = {k: np.zeros((len(left), len(left))) for k in ("x", "y", "z")}

    def add(Km, i, j, k):
        Km[i, i] += k
        Km[j, j] += k
        Km[i, j] -= k
        Km[j, i] -= k

    for a_, b_ in zip(left, left[1:]):
        mp = _rail_piece(props, a_, b_)
        if mp is None:
            raise DesignError(f"no rail member between {a_} and {b_}")
        mat, sec = mp["material"], mp["section"]
        E = float(mat["E_MPa"])
        G = E / (2.0 * (1.0 + float(mat["nu"])))
        L = mp["length"]
        i, j = idx[a_], idx[b_]
        add(K["x"], i, j, G * sec["J"] / L)
        add(K["z"], i, j, E * sec["Iy"] / L)
        add(K["y"], i, j, E * sec["Iz"] / L)
        f = 4.0 * G * sec["J"] / w**2 * L / 2.0
        K["y"][i, i] += f
        K["y"][j, j] += f
    for n in left:
        m = _mirror(nodes, n)
        cross = _member_between(props, n, m) if m else None
        if cross is None:
            continue
        E = float(cross["material"]["E_MPa"])
        G = E / (2.0 * (1.0 + float(cross["material"]["nu"])))
        s, L = cross["section"], cross["length"]
        i = idx[n]
        K["x"][i, i] += 6.0 * E * s["Iz"] / L
        K["z"][i, i] += 6.0 * E * s["Iy"] / L
        K["y"][i, i] += 2.0 * G * s["J"] / L
    out = {}
    for k, Km in K.items():
        f = np.zeros(len(left))
        f[idx[node]] = 1.0
        out[f"k_{k}"] = 1.0 / float(np.linalg.solve(Km, f)[idx[node]])
    return out


def mount_frames(design: dict) -> list[dict]:
    """The U-frames under (or over) the rails: a member from a left rail node to a node off the rails (a leg), a
    member from there to its mirror (a cross member off the rails' height) and the mirror leg back to the mirror
    rail node. [{"leg", "cross", "leg_mirror", "base", "tip"}], front to back."""
    nodes, props, left, y_r, z_r = _left_rail(design)
    on_left = set(left)
    on_rails = {n for n, p in nodes.items() if abs(abs(p[1]) - y_r) < 1e-6 and abs(p[2] - z_r) < 1e-6}
    out = []
    for mp in props.values():
        m = mp["member"]
        for base, tip in ((m["node_a"], m["node_b"]), (m["node_b"], m["node_a"])):
            if base not in on_left or tip in on_rails:
                continue
            t_m, b_m = _mirror(nodes, tip), _mirror(nodes, base)
            if t_m is None or b_m is None or abs(nodes[tip][1]) < 1e-6:
                continue
            cross = _member_between(props, tip, t_m)
            leg_m = _member_between(props, t_m, b_m)
            if cross is None or leg_m is None:
                continue
            out.append({"leg": m["id"], "cross": cross["member"]["id"], "leg_mirror": leg_m["member"]["id"],
                        "base": base, "tip": tip})
    return sorted(out, key=lambda u: -nodes[u["base"]][0])


def ladder_twist_setup(design: dict) -> dict:
    """The ladder as a thin-walled beam for ladder_twist_rate (as end_cross_member_inputs takes it): its ends, GJ
    (the rails' St Venant torsion and the cross members' twist smeared over the length), EIw (the rails bending
    opposite ways) and the roll inertia along it."""
    nodes, props, left, y_r, z_r = _left_rail(design)
    w = 2.0 * y_r
    rail = _rail_piece(props, left[0], left[1])
    mat = rail["material"]
    E, nu = float(mat["E_MPa"]), float(mat["nu"])
    G = E / (2.0 * (1.0 + nu))
    rail_nodes = [n for n, p in nodes.items() if abs(abs(p[1]) - w / 2.0) < 1e-6]
    xr = [float(nodes[n][0]) for n in rail_nodes]
    x_rear_end, x_front_end = min(xr), max(xr)
    cross_GJw = sum(G * mp["section"]["J"] * mp["length"] for mp in props.values()
                    if abs(abs(mp["a"][1]) - w / 2.0) < 1e-6 and abs(abs(mp["b"][1]) - w / 2.0) < 1e-6
                    and abs(mp["a"][1] - mp["b"][1]) > 1e-6)
    sec = rail["section"]
    return {"x_rear_end": x_rear_end, "x_front_end": x_front_end, "w": w,
            "GJ": 2.0 * G * sec["J"] + cross_GJw / (x_front_end - x_rear_end), "EIw": E * sec["Iz"] * w**2 / 2.0,
            "pieces": roll_inertia_along(design)}


def mount_leg_inputs(design: dict, loading: str | None = None) -> list[dict]:
    """Quantities for frame_static.mount_leg_stress for each U-frame's left leg (mount_frames), front to back:
    the leg and the half cross member's geometry and tubes, the rail's springs at the leg's node (rail_springs),
    and the free ladder's pitch of the left rail there for 1 kN up at the left and down at the right front / rear
    wheel (theta = -(w/2) psi', psi' from ladder_twist_rate as end_cross_member_inputs takes the ladder).
    [{"leg", "leg_mirror", "cross", "node", "inputs"}]."""
    if loading is not None:
        design = on_board(design, loading)
    frames = mount_frames(design)
    if not frames:
        return []
    nodes = node_table(design)
    props = member_props(design)
    sup = design.get("supports", [])
    x_rear = min(s["position_mm"][0] for s in sup)
    rear = sorted((s for s in sup if abs(s["position_mm"][0] - x_rear) < 1e-6), key=lambda s: -s["position_mm"][1])
    front = sorted((s for s in sup if abs(s["position_mm"][0] - x_rear) > 1e-6), key=lambda s: -s["position_mm"][1])
    if len(rear) < 2 or len(front) < 2:
        raise DesignError("the mount leg calculation needs a left and a right wheel at the front and at the rear")
    tw = ladder_twist_setup(design)

    def pitch(pair, x_at):
        track = float(pair[0]["position_mm"][1]) - float(pair[-1]["position_mm"][1])
        rate = ladder_twist_rate(tw["pieces"], tw["x_rear_end"], tw["x_front_end"], tw["GJ"], tw["EIw"],
                                 float(pair[0]["position_mm"][0]), 1000.0 * track, x_at)
        return -tw["w"] / 2.0 * rate

    out = []
    for u in frames:
        leg, crs = props[u["leg"]], props[u["cross"]]
        sl, sx = (design["sections"][x["member"]["section"]] for x in (leg, crs))
        for s in (sl, sx):
            d = s.get("dims_mm", {})
            if s.get("shape") != "rect_tube" or abs(float(d["b"]) - float(d["h"])) > 1e-9:
                raise DesignError("the mount leg calculation is for square tubes (rect_tube with b = h)")
        B, T = nodes[u["base"]], nodes[u["tip"]]
        mat = leg["material"]
        x_at = float(B[0])
        q = quantity
        out.append({
            "leg": u["leg"], "leg_mirror": u["leg_mirror"], "cross": u["cross"], "node": u["base"],
            "inputs": {
                "dx": q(float(T[0] - B[0]), "mm"), "dy": q(float(T[1] - B[1]), "mm"), "dz": q(float(T[2] - B[2]), "mm"),
                "y_p": q(abs(float(T[1])), "mm"),
                "b_l": q(float(sl["dims_mm"]["b"]), "mm"), "t_l": q(float(sl["dims_mm"]["t"]), "mm"),
                "b_x": q(float(sx["dims_mm"]["b"]), "mm"), "t_x": q(float(sx["dims_mm"]["t"]), "mm"),
                "E": q(float(mat["E_MPa"]), "MPa"), "nu": q(float(mat["nu"]), "1"),
                **{k: q(v, "N*mm") for k, v in rail_springs(design, u["base"]).items()},
                "theta_front": q(pitch(front, x_at), "rad"), "theta_rear": q(pitch(rear, x_at), "rad"),
            },
        })
    return out


ROLLBAR_POSTS = ("rollbar_post_L", "rollbar_post_R")
ROLLBAR_TOP = "rollbar_top"


def plastic_modulus(section: dict) -> tuple[float, float]:
    """Plastic section moduli (mm^3) for bending about the section's local z axis (depth h, as Iz) and local y
    axis (depth b, as Iy): round tube (d^3 - (d - 2t)^3) / 6 both ways; rect tube (b h^2 - (b - 2t)(h - 2t)^2) / 4
    and (h b^2 - (h - 2t)(b - 2t)^2) / 4 (sharp corners)."""
    d = section.get("dims_mm", {})
    if section.get("shape") == "round_tube":
        D, t = float(d["d"]), float(d["t"])
        z = (D**3 - (D - 2.0 * t) ** 3) / 6.0
        return z, z
    if section.get("shape") == "rect_tube":
        b, h, t = float(d["b"]), float(d["h"]), float(d["t"])
        return (b * h**2 - (b - 2 * t) * (h - 2 * t) ** 2) / 4.0, (h * b**2 - (h - 2 * t) * (b - 2 * t) ** 2) / 4.0
    raise DesignError(f"the portal frame inputs need round_tube or rect_tube sections, got {section.get('shape')!r}")


def plastic_torque(section: dict, tau_y: float) -> float:
    """Plastic torque (N*mm) of a thin closed tube, 2 A_m t tau_y (Bredt with the shear at yield all round)."""
    d = section.get("dims_mm", {})
    if section.get("shape") == "round_tube":
        D, t = float(d["d"]), float(d["t"])
        return 2.0 * np.pi * (D - t) ** 2 / 4.0 * t * tau_y
    if section.get("shape") == "rect_tube":
        b, h, t = float(d["b"]), float(d["h"]), float(d["t"])
        return 2.0 * (b - t) * (h - t) * t * tau_y
    raise DesignError(f"the portal frame inputs need round_tube or rect_tube sections, got {section.get('shape')!r}")


def portal_frame_inputs(design: dict) -> dict:
    """Quantities for frame_static.portal_frame_side, _rear and _top from a roll bar (all but E_in, f and F,
    which the load case gives).

    The posts rollbar_post_L/R stand from a foot node up to the top corners, joined by rollbar_top. A post's
    foot is either a rail node or the outboard end of an outrigger rollbar_foot_<side> from a rail node. Section
    axes as K1: a vertical post's local y is along x, so the portal's sideways (in-plane) bending uses Iy and
    the bending along the vehicle Iz; the top beam's in-plane (vertical) bending uses Iz; the outrigger (along
    y) bends vertically on Iz and twists on J; the rail (along x) twists on J and bends vertically on Iz.

    Feet (assumptions): the outrigger a cantilever from the rail node (rotation M L_o / (E I) for the side
    moment, M L_o / (G J) for the moment along the vehicle); the rail held against twist and vertical
    rotation at its next nodes either side (L_a, L_b), so the side moment twists it with G J (1/L_a + 1/L_b)
    and the moment along the vehicle bends it with 4 E I (1/L_a + 1/L_b). theta_f = 1e6 x the two
    flexibilities in series. Plastic moment at a foot: the smallest of the post's, the outrigger's (bending
    or torsion) and the rail's (2 x, the rail on both sides of the node). The posts are taken alike; the
    left one is read."""
    nodes = node_table(design)
    props = member_props(design)
    missing = [m for m in (*ROLLBAR_POSTS, ROLLBAR_TOP) if m not in props]
    if missing:
        raise DesignError(f"no roll bar in the design: members {', '.join(missing)} are missing")
    post, top = props[ROLLBAR_POSTS[0]], props[ROLLBAR_TOP]
    pm = post["member"]
    corners = {top["member"]["node_a"], top["member"]["node_b"]}
    foot = pm["node_a"] if pm["node_b"] in corners else pm["node_b"]
    mat = post["material"]
    E, fy = float(mat["E_MPa"]), float(mat["yield_MPa"])
    sp, sb = (design["sections"][x["member"]["section"]] for x in (post, top))
    zp_post_z, zp_post_y = plastic_modulus(sp)
    Mp_post_side, Mp_post_rear = zp_post_y * fy, zp_post_z * fy
    Mp_top = plastic_modulus(sb)[0] * float(top["material"]["yield_MPa"])

    out_mp = next((mp for mid, mp in props.items() if mid.startswith("rollbar_foot_")
                   and foot in (mp["member"]["node_a"], mp["member"]["node_b"])), None)
    c_side = c_rear = 0.0
    caps_side, caps_rear = [Mp_post_side], [Mp_post_rear]
    breakdown: dict = {}
    rail_node = foot
    if out_mp is not None:
        om = out_mp["member"]
        rail_node = om["node_a"] if om["node_b"] == foot else om["node_b"]
        omat = out_mp["material"]
        Eo, fyo = float(omat["E_MPa"]), float(omat["yield_MPa"])
        Go = Eo / (2.0 * (1.0 + float(omat["nu"])))
        osec = design["sections"][om["section"]]
        Lo = out_mp["length"]
        c_o_side = Lo / (Eo * out_mp["section"]["Iz"])
        c_o_rear = Lo / (Go * out_mp["section"]["J"])
        c_side += c_o_side
        c_rear += c_o_rear
        caps_side.append(plastic_modulus(osec)[0] * fyo)
        caps_rear.append(plastic_torque(osec, fyo / np.sqrt(3.0)))
        breakdown.update(outrigger=om["id"], outrigger_length_mm=Lo, theta_outrigger_side_per_kNm=1e6 * c_o_side,
                         theta_outrigger_rear_per_kNm=1e6 * c_o_rear)
    p = nodes[rail_node]
    rails = [mp for mp in props.values() if rail_node in (mp["member"]["node_a"], mp["member"]["node_b"])
             and abs(mp["a"][1] - mp["b"][1]) < 1e-6 and abs(mp["a"][2] - mp["b"][2]) < 1e-6
             and abs(mp["a"][0] - mp["b"][0]) > 1e-6 and abs(mp["a"][1] - p[1]) < 1e-6]
    if len(rails) == 2:
        rmat = rails[0]["material"]
        Er, fyr = float(rmat["E_MPa"]), float(rmat["yield_MPa"])
        Gr = Er / (2.0 * (1.0 + float(rmat["nu"])))
        rsec = design["sections"][rails[0]["member"]["section"]]
        inv = sum(1.0 / r["length"] for r in rails)
        c_r_side = 1.0 / (Gr * rails[0]["section"]["J"] * inv)
        c_r_rear = 1.0 / (4.0 * Er * rails[0]["section"]["Iz"] * inv)
        c_side += c_r_side
        c_rear += c_r_rear
        caps_side.append(2.0 * plastic_torque(rsec, fyr / np.sqrt(3.0)))
        caps_rear.append(2.0 * plastic_modulus(rsec)[0] * fyr)
        breakdown.update(rail_node=rail_node, rail_members=[r["member"]["id"] for r in rails],
                         rail_lengths_mm=[r["length"] for r in rails], theta_rail_side_per_kNm=1e6 * c_r_side,
                         theta_rail_rear_per_kNm=1e6 * c_r_rear)
    else:  # the foot is not on a rail running on both sides: taken as fixed there
        breakdown.update(rail_node=None, note="the foot's node has no rail on both sides; taken as fixed there")

    pp = post["section"]
    q = quantity
    common = {"E": q(E, "MPa"), "h": q(post["length"], "mm")}
    M_pt = min(Mp_post_side, Mp_top)
    return {
        "side": {**common, "w": q(top["length"], "mm"), "I_c": q(pp["Iy"], "mm^4"), "I_b": q(top["section"]["Iz"], "mm^4"),
                 "theta_f": q(1e6 * c_side, "rad"), "M_pf": q(min(caps_side), "N*mm"), "M_pt": q(M_pt, "N*mm")},
        "rear": {**common, "I_c": q(pp["Iz"], "mm^4"), "theta_f": q(1e6 * c_rear, "rad"),
                 "M_pf": q(min(caps_rear), "N*mm")},
        "top": {**common, "w": q(top["length"], "mm"), "I_c": q(pp["Iy"], "mm^4"), "I_b": q(top["section"]["Iz"], "mm^4"),
                "I_min": q(min(pp["Iy"], pp["Iz"]), "mm^4"), "A_c": q(pp["A"], "mm^2"),
                "theta_f": q(1e6 * c_side, "rad"), "M_pb": q(Mp_top, "N*mm"), "M_pt": q(M_pt, "N*mm"),
                "f_y": q(fy, "MPa")},
        "members": {"posts": list(ROLLBAR_POSTS), "top": ROLLBAR_TOP, "foot_node": foot},
        "foot": breakdown,
        "plastic_moments_Nmm": {"post_side": Mp_post_side, "post_rear": Mp_post_rear, "top": Mp_top,
                                "foot_side": min(caps_side), "foot_rear": min(caps_rear)},
    }

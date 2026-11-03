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
    cross = [mp for mp in props.values() if abs(mp["a"][1] + mp["b"][1]) < 1e-6 and abs(mp["a"][1]) > 1e-6]
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

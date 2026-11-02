"""Inputs for the rail-joint hand calculation read off a design by code.

rail_joint_inputs() looks at the rear axle joint of one rail: the rail
member ahead of it, the cross member there, the wheel's sideways offset from
the rail, the rail height, and the hogging moment at the joint from what the
rail carries behind the axle (part weights shared equally over their
carried_by nodes, members' own weight). Moments are taken about the joint
from the x positions; this is plain statics, not the frame model.
"""

from __future__ import annotations

from .units import STANDARD_GRAVITY, quantity
from .vehicle import DesignError, member_props, node_table, parts_on_board


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
        design = parts_on_board(design, loading)
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

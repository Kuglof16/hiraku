"""Whether the parts' connections touch, and whether the bodies stay in their modules' spaces (H1-joints,
H1-envelope).

A part's connects_to says what it is joined to (a node, a part, a member, a support) and how (via). A joint that
carries something mechanically has to touch: the gap between the part's shape and the other (a node's point, the
other body's shape, a support's wheel centre) at most JOINT_GAP_MM. Wires and hoses (NOT_TOUCHING_VIA) run between
the two and are not counted. A part with no mechanical connection at all is joined to nothing.

The spaces: a design split into modules gives each module an envelope (boxes); a body counts as outside when the
axis-aligned box round its shape is not inside any one box of its own module's envelope (within ENVELOPE_TOL_MM)."""

from __future__ import annotations

import numpy as np

from .model import Body, bodies, shape_overlap

JOINT_GAP_MM = 2.0  # a joint touches when the gap is no more than this (the program management's figure)
NOT_TOUCHING_VIA = ("wire", "hose")
ENVELOPE_TOL_MM = 0.5


def point_gap(p: np.ndarray, b: Body) -> float:
    """Distance from a point to a body's shape (0 inside)."""
    if b.capsule is not None:
        c = b.capsule
        d = c.b - c.a
        t = float(np.clip((p - c.a) @ d / max(float(d @ d), 1e-12), 0.0, 1.0))
        return max(0.0, float(np.linalg.norm(p - (c.a + t * d))) - c.r)
    local = b.obb.axes @ (p - b.obb.center)
    return float(np.linalg.norm(np.maximum(np.abs(local) - b.obb.half, 0.0)))


def joint_gaps(design: dict, bs: dict[str, Body] | None = None) -> dict:
    """{"gaps": [(part id, to, via, gap mm)] over JOINT_GAP_MM, widest first; "unjoined": [part ids with no
    mechanical connects_to]; "joints": how many mechanical connections were measured}."""
    bs = bs or bodies(design)
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])}
    supports = {s["id"]: np.asarray(s["position_mm"], float) for s in design.get("supports", [])}
    gaps, unjoined, n = [], [], 0
    for p in design.get("parts", []):
        me = bs.get(f"part:{p['id']}")
        links = [c for c in p.get("connects_to") or [] if c.get("via") not in NOT_TOUCHING_VIA]
        if not links:
            unjoined.append(p["id"])
            continue
        if me is None:
            continue
        for c in links:
            to = c["to"]
            if to in nodes:
                g = point_gap(nodes[to], me)
            elif f"part:{to}" in bs or f"member:{to}" in bs:
                other = bs.get(f"part:{to}") or bs[f"member:{to}"]
                g = max(0.0, -shape_overlap(me, other))
            elif to in supports:
                g = point_gap(supports[to], me)
            else:
                continue
            n += 1
            if g > JOINT_GAP_MM:
                gaps.append((p["id"], to, c.get("via", ""), g))
    return {"gaps": sorted(gaps, key=lambda t: -t[3]), "unjoined": unjoined, "joints": n}


def outside_envelopes(design: dict, bs: dict[str, Body] | None = None,
                      envelopes: dict[str, list[dict]] | None = None) -> list[tuple[str, str, float]]:
    """Bodies not inside any box of their module's envelope: (body id, module, mm out), farthest first. Empty for a
    design without modules. `envelopes` ({module: boxes}) instead of the design's own."""
    if envelopes is None:
        envelopes = {m["module_id"]: m.get("envelope", []) for m in design.get("modules") or []}
    if not envelopes:
        return []
    mods = {f"part:{p['id']}": p.get("module") for p in design.get("parts", [])}
    mods.update({f"member:{m['id']}": m.get("module") for m in design.get("members", [])})
    bs = bs or bodies(design)
    out = []
    for k, b in bs.items():
        m = mods.get(k)
        if m is None or m == "package":
            continue
        c = b.obb.corners()
        lo, hi = c.min(axis=0), c.max(axis=0)
        best = np.inf
        for e in envelopes.get(m, []):
            ec, eh = np.asarray(e["center_mm"], float), np.asarray(e["size_mm"], float) / 2.0
            best = min(best, float(max(np.max((ec - eh) - lo), np.max(hi - (ec + eh)), 0.0)))
        if best > ENVELOPE_TOL_MM:
            out.append((k, m, best))
    return sorted(out, key=lambda t: -t[2])

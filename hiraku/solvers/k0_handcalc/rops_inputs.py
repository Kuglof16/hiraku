"""Inputs of frame_static.rops_occupant_clearance read off a design by code: the gap between the roll bar and
the occupants' space, measured along each loading of the roll-over (ROPS) test, R4.

    from hiraku.solvers.k0_handcalc.rops_inputs import rops_gaps, rops_inputs
    rops_gaps(on_board(design, "two_cargo"))      # {"g_side", "g_rear", "g_top": quantity (mm), "motions": [...]}
    rops_inputs(design, loading="two_cargo")      # gaps and portal_frame_inputs, flat for from.<name> refs

How a gap is measured (before any deflection; code, no table values):

- The roll bar's members are those whose id starts with rollbar_ (k4_geometry.rollbar.ROLLBAR_PREFIX), each a
  capsule between its end nodes with k4_geometry.rollbar.member_radius. The occupants' space is
  k4_geometry.rollbar.occupant_spaces(design) (the occupant box widened to AIST D2 shoulder breadth and raised to
  AIST I1 sitting height, young male 95th percentile). So the clearance at any moved shape is
  k4_geometry.occupant_clearance(design, displacements=...) with the default members (the tests check this).
- Only the two top corners (the ends of rollbar_top) move; every other node stays. The posts (the members from a
  corner down to a foot) must be upright.
- A loading moves the corners with its deflection delta, the deflection the portal formula gives at its load
  point:
    side: both corners travel delta across the vehicle (+y or -y; the sway of the portal);
    rear: along the vehicle (+x or -x); the load at f of the span from the near corner and the top beam rigid out
          of the portal's plane turning about the vertical (as frame_static.portal_frame_rear): the near corner
          travels delta (1 - f) / ((1 - f)^2 + f^2), the far one delta f / ((1 - f)^2 + f^2), so the load point
          travels delta; either corner may be the near one;
    side and rear: each post turns about its foot as a rigid straight line (the plastic hinges of the
          mechanisms are at the feet), so a corner that travels u sideways also drops h - sqrt(h^2 - u^2), h the
          post's length; the travel ends when a post lies flat (u = h);
    top:  both corners go down delta (the top beam's mid-span deflection put on its ends, which moves the ends
          more than they move: on the safe side), up to the posts' length.
  The top beam stays straight between the corners: its bending between them is not seen. Both senses (and both
  near corners) are tried; the smallest gap is the direction's.
- The gap of a motion is the delta at which the smallest clearance first reaches 0: a march in steps that move no
  corner further than the clearance at the step's start, or half the smallest member radius when the clearance
  is smaller (no point of a member moves further than its end nodes, so no contact is stepped over, but a graze
  shallower than about that half radius can be), then bisection to 1e-6 mm. A bar already in a
  space as built gives that (negative) clearance as the gap. A motion that does not touch a space before its
  travel ends gives the delta at its end and contact False (the bar passes beside the space that way).

What the gap is not: it is the travel left before contact along that motion, not the shortest distance. Where
the bar closes on the space straight along the motion and the nearest point travels delta, the two are the same;
where it closes at a slant or passes beside the space, the travel is longer than the distance; both reach 0 at the
same delta. The criterion's quantity is the shortest distance: clearances_at measures it with the bar moved by each
motion at a given deflection (rops_inputs: at the portal formulas' own deflections, portal_deflections), and
frame_static.rops_occupant_clearance takes the smallest.
"""

from __future__ import annotations

import math

import numpy as np

from ...contract.loading import on_board
from .frame_inputs import ROLLBAR_TOP, portal_frame_inputs
from .units import quantity
from .vehicle import DesignError, node_table

DIRECTIONS = ("side", "rear", "top")
TOL_MM = 1.0e-6
UPRIGHT = 1.0e-6  # largest horizontal offset of a post's top over its foot, per mm of its length


def _rollbar(design: dict) -> dict:
    """Corners by side ({"L", "R"}, L the larger y), each corner's foot and post length, the nodes, the roll bar's
    members (id, node_a, node_b, radius) and the occupants' spaces as k4_geometry boxes."""
    from ..k4_geometry.obb import OBB
    from ..k4_geometry.rollbar import ROLLBAR_PREFIX, member_radius, occupant_spaces

    members = {m["id"]: m for m in design.get("members", [])}
    if ROLLBAR_TOP not in members:
        raise DesignError(f"no roll bar in the design: member {ROLLBAR_TOP} is missing")
    nodes = node_table(design)
    top = members[ROLLBAR_TOP]
    a, b = top["node_a"], top["node_b"]
    corners = {"L": a, "R": b} if nodes[a][1] >= nodes[b][1] else {"L": b, "R": a}
    bar = [(mid, m["node_a"], m["node_b"], member_radius(design["sections"][m["section"]]))
           for mid, m in members.items() if mid.startswith(ROLLBAR_PREFIX)]
    posts = {}
    for c in corners.values():
        under = [(nb if na == c else na) for mid, na, nb, _ in bar if mid != ROLLBAR_TOP and c in (na, nb)]
        if len(under) != 1:
            raise DesignError(f"the roll bar's corner {c} needs one post under it, found {len(under)}")
        p = nodes[c] - nodes[under[0]]
        h = float(np.linalg.norm(p))
        if p[2] <= 0 or math.hypot(p[0], p[1]) > UPRIGHT * h:
            raise DesignError(f"the post under corner {c} is not upright; the gaps are measured for upright posts")
        posts[c] = {"foot": under[0], "h": h}
    spaces = occupant_spaces(design)
    if not spaces:
        raise DesignError("the design has no occupant parts on board, so there is no occupant space to keep")
    boxes = [OBB((np.asarray(s["lo_mm"]) + np.asarray(s["hi_mm"])) / 2.0, np.eye(3),
                 (np.asarray(s["hi_mm"]) - np.asarray(s["lo_mm"])) / 2.0, s["occupant"]) for s in spaces]
    return {"corners": corners, "posts": posts, "nodes": nodes, "bar": bar, "boxes": boxes, "spaces": spaces}


def motions(corners: dict, rear_fraction: float, rear_shares: tuple[float, float] | None = None) -> list[dict]:
    """Every motion tried: {"direction", "name", "kind" ("turn" or "down"), "share": {corner: [ex, ey] times the
    corner's travel per mm of delta}} (for "down" the share is the drop per mm of delta). rear_shares: the near and
    far corners' travel per mm of the rear load point's (default: the lever rule of a top beam rigid out of the
    portal's plane on posts with no fixity at their tops, (1 - f) / s and f / s, s = (1 - f)^2 + f^2)."""
    f = float(rear_fraction)
    if not 0.0 <= f <= 0.5:
        raise DesignError(f"rear_fraction {f} is outside 0..0.5")
    if rear_shares is None:
        s = (1.0 - f) ** 2 + f**2
        near, far = (1.0 - f) / s, f / s
    else:
        near, far = (float(x) for x in rear_shares)
    L, R = corners["L"], corners["R"]
    out = []
    for sign, word in ((1.0, "+y"), (-1.0, "-y")):
        out.append({"direction": "side", "name": f"side {word}", "kind": "turn",
                    "share": {L: [0.0, sign], R: [0.0, sign]}})
    for sign, word in ((1.0, "+x"), (-1.0, "-x")):
        for n_near, n_far, side in ((L, R, "L"), (R, L, "R")):
            out.append({"direction": "rear", "name": f"rear {word}, near corner {side}", "kind": "turn",
                        "share": {n_near: [sign * near, 0.0], n_far: [sign * far, 0.0]}})
    out.append({"direction": "top", "name": "top -z", "kind": "down", "share": {L: 1.0, R: 1.0}})
    return out


def displacements(geo: dict, motion: dict, delta: float) -> dict:
    """{corner node: [dx, dy, dz] mm} of a motion at deflection delta (k4_geometry.occupant_clearance's
    displacements)."""
    out = {}
    for c, share in motion["share"].items():
        if motion["kind"] == "down":
            out[c] = [0.0, 0.0, -share * delta]
            continue
        h = geo["posts"][c]["h"]
        ux, uy = share[0] * delta, share[1] * delta
        u = math.hypot(ux, uy)
        if u > h * (1.0 + 1e-12):
            raise DesignError(f"corner {c} would travel {u:.1f} mm, past its post lying flat ({h:.1f} mm)")
        out[c] = [ux, uy, -(h - math.sqrt(max(h * h - u * u, 0.0)))]
    return out


def _clearance(geo: dict, moves: dict) -> float:
    """Smallest clearance (mm) between the moved roll bar and the spaces (as k4_geometry.occupant_clearance)."""
    from ..k4_geometry.capsule import Capsule, capsule_box_overlap

    nodes = geo["nodes"]
    at = {k: nodes[k] + np.asarray(v, float) for k, v in moves.items()}
    return float(min(-capsule_box_overlap(Capsule(at.get(na, nodes[na]), at.get(nb, nodes[nb]), r, mid), box)
                     for mid, na, nb, r in geo["bar"] for box in geo["boxes"]))


def _end_of_travel(geo: dict, motion: dict) -> float:
    """The largest delta of a motion: a post lies flat (turn), or the corners are down a post's length (down)."""
    if motion["kind"] == "down":
        return min(geo["posts"][c]["h"] / s for c, s in motion["share"].items())
    return min(geo["posts"][c]["h"] / math.hypot(*s) for c, s in motion["share"].items() if math.hypot(*s) > 0)


def _gap(geo: dict, motion: dict) -> tuple[float, bool]:
    """The delta at which the motion first brings the bar to the space, and whether it does before its end."""
    clear = lambda d: _clearance(geo, displacements(geo, motion, d))  # noqa: E731
    c_lo = clear(0.0)
    if c_lo <= 0.0:
        return c_lo, True
    end = _end_of_travel(geo, motion)
    half_r = 0.5 * min(m[3] for m in geo["bar"])
    lo = 0.0
    at_lo = displacements(geo, motion, lo)
    while lo < end:
        # Every point of a member moves at most as far as its end nodes do, so the clearance cannot fall by more
        # than the corners' largest move: a step moving no corner more than max(clearance, half_r) skips no contact
        # (but a graze shallower than about half_r, once the clearance is below half_r).
        allowed = max(c_lo, half_r)
        hi = min(lo + allowed, end)
        while True:
            at_hi = displacements(geo, motion, hi)
            moved = max(math.dist(at_hi[c], at_lo[c]) for c in at_hi)
            if moved <= allowed or hi - lo <= TOL_MM:
                break
            hi = lo + 0.5 * (hi - lo)
        c_hi = _clearance(geo, at_hi)
        if c_hi <= 0.0:
            while hi - lo > TOL_MM:
                mid = 0.5 * (lo + hi)
                lo, hi = (lo, mid) if clear(mid) <= 0.0 else (mid, hi)
            return hi, True
        lo, c_lo, at_lo = hi, c_hi, at_hi
    return end, False


def rops_gaps(design: dict, rear_fraction: float = 1.0 / 6.0, rear_shares: tuple[float, float] | None = None) -> dict:
    """Gaps (mm) for frame_static.rops_occupant_clearance: g_side, g_rear and g_top, each the smallest over its
    motions (the module's text says how they are measured; rear_shares as motions()). The design's parts are taken
    as they are: pass on_board(design, loading).

    Returns {"g_side", "g_rear", "g_top": quantity, "motions": [{"direction", "name", "kind", "share", "gap_mm",
    "contact"}], "corners": {"L", "R"}, "posts": {corner: {"foot", "h"}}, "as_built_clearance_mm", "spaces"}."""
    geo = _rollbar(design)
    tried = []
    for m in motions(geo["corners"], rear_fraction, rear_shares):
        gap, contact = _gap(geo, m)
        tried.append({**m, "gap_mm": gap, "contact": contact})
    out = {f"g_{d}": quantity(min(t["gap_mm"] for t in tried if t["direction"] == d), "mm") for d in DIRECTIONS}
    out.update(motions=tried, corners=geo["corners"], posts=geo["posts"],
               as_built_clearance_mm=_clearance(geo, {}), spaces=geo["spaces"])
    return out


def _values(portal: dict, direction: str) -> dict:
    from .units import to_unit

    return {k: to_unit(q, q["unit"], k) for k, q in portal[direction].items()}


def rear_shares(portal: dict, rear_fraction: float, rear_energy_J: float | None = None) -> tuple[float, float]:
    """The near and far corners' travel along the vehicle per mm of the rear load point's, from
    frame_static.portal_frame_rear on the portal's inputs: at the energy (share_near, share_far) when it is given,
    else on the mechanism (mech_near, mech_far)."""
    from .library_portal import rear

    v = _values(portal, "rear")
    v.update(f=float(rear_fraction), E_in=float(rear_energy_J) if rear_energy_J else 1.0)
    r = rear(v)
    return (r["share_near"], r["share_far"]) if rear_energy_J else (r["mech_near"], r["mech_far"])


def portal_deflections(portal: dict, side_energy_J: float, rear_energy_J: float, crush_force_N: float,
                       rear_fraction: float) -> dict:
    """{"side", "rear", "top": mm}: the deflections the R4 predictions give (frame_static.portal_frame_side delta_E,
    portal_frame_rear delta_E, portal_frame_top delta) on the portal's inputs, by the same functions."""
    from .library_portal import rear, side, top

    return {"side": side({**_values(portal, "side"), "E_in": float(side_energy_J)})["delta_E"],
            "rear": rear({**_values(portal, "rear"), "E_in": float(rear_energy_J), "f": float(rear_fraction)})["delta_E"],
            "top": top({**_values(portal, "top"), "F": float(crush_force_N)})["delta"]}


def clearances_at(design: dict, deflections: dict, rear_fraction: float = 1.0 / 6.0,
                  rear_shares: tuple[float, float] | None = None) -> dict:
    """The shortest distance (mm, k4_geometry.occupant_clearance's) between the roll bar and the occupants' space
    with the bar moved by each loading's motion at its deflection (deflections {"side", "rear", "top": mm}), the
    smallest over the direction's motions (both senses, either near corner). A deflection past the end of a
    motion's travel (a post lying flat, the corners down a post's length) is taken at the end. Pass
    on_board(design, loading).

    Returns {"s_side", "s_rear", "s_top": quantity, "motions": [{"direction", "name", "delta_mm",
    "clearance_mm"}]}."""
    geo = _rollbar(design)
    tried = []
    for m in motions(geo["corners"], rear_fraction, rear_shares):
        delta = min(float(deflections[m["direction"]]), _end_of_travel(geo, m))
        tried.append({"direction": m["direction"], "name": m["name"], "delta_mm": delta,
                      "clearance_mm": _clearance(geo, displacements(geo, m, delta))})
    out = {f"s_{d}": quantity(min(t["clearance_mm"] for t in tried if t["direction"] == d), "mm")
           for d in DIRECTIONS}
    out["motions"] = tried
    return out


def rops_inputs(design: dict, loading: str | None = None, rear_fraction: float = 1.0 / 6.0,
                rear_energy_J: float | None = None, side_energy_J: float | None = None,
                crush_force_N: float | None = None) -> dict:
    """Everything the R4 prediction reads off a design, flat for from.<name> refs: side_<k>, rear_<k> and top_<k>
    for frame_inputs.portal_frame_inputs(design)[side / rear / top][k], and g_side, g_rear and g_top from
    rops_gaps(on_board(design, loading), rear_fraction, shares), the rear motion's corner shares from
    frame_static.portal_frame_rear (rear_shares: at rear_energy_J when given, else on the mechanism) so that the
    rear motion is the one the formula's top beam makes (the posts share the load; not the lever rule).

    With all three of side_energy_J, rear_energy_J and crush_force_N, also d0_side, d0_rear, d0_top (the
    deflections the portal formulas give, portal_deflections) and s_side, s_rear, s_top (clearances_at those
    deflections): frame_static.rops_occupant_clearance's shortest distances.

    Returns {"inputs": {name: quantity}, "portal": portal_frame_inputs(design), "gaps": rops_gaps(...),
    "rear_shares": (near, far), "clearances": clearances_at(...) or None}."""
    portal = portal_frame_inputs(design)
    shares = rear_shares(portal, rear_fraction, rear_energy_J)
    on = on_board(design, loading)
    gaps = rops_gaps(on, rear_fraction, shares)
    flat = {f"{d}_{k}": v for d in DIRECTIONS for k, v in portal[d].items()}
    flat.update({f"g_{d}": gaps[f"g_{d}"] for d in DIRECTIONS})
    clear = None
    if None not in (side_energy_J, rear_energy_J, crush_force_N):
        d0 = portal_deflections(portal, side_energy_J, rear_energy_J, crush_force_N, rear_fraction)
        clear = clearances_at(on, d0, rear_fraction, shares)
        flat.update({f"d0_{d}": quantity(d0[d], "mm") for d in DIRECTIONS})
        flat.update({f"s_{d}": clear[f"s_{d}"] for d in DIRECTIONS})
    return {"inputs": flat, "portal": portal, "gaps": gaps, "rear_shares": shares, "clearances": clear}


__all__ = ["DIRECTIONS", "clearances_at", "displacements", "motions", "portal_deflections", "rops_gaps",
           "rops_inputs"]

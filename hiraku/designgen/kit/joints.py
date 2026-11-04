"""Joining the parts in shape: a part's connects_to names only what it touches (within JOINT_GAP_MM), directly or
through a fitting added as a part. The requirement is the joint itself ("this part is held by that one"): where the
two do not touch, the joint needs something between them.

join(design), for the steering, the pedals and the master cylinder, and the fenders (the corners' arms, damper and
ball joints are kit/arms.py's, the other parts kit/fittings.py's):
1. steering: a bearing (a BEARING_MM cube, kind bearing) where the intermediate shaft passes the front bulkhead.
2. every mechanical connects_to (not wire or hose) of these parts whose two ends are apart: when the part touches a
   frame member, that member is named instead (the joint is where the part sits); a far joint to a node is dropped
   when the part has a joint that touches (its load goes that way); else, within BRIDGE_MAX_MM, a bracket (kind
   bracket, a STRUT_D_MM strut between the nearest points of the two shapes) is added and the joint goes through
   it; farther ones are left as they are (counted by H1-joints). The master cylinder is held by the bulkhead
   (HOLDER).
3. a fender joined to nothing is joined to its corner's knuckle (it turns with the wheel).

Sizes are HIRAKU's choices (no source)."""

from __future__ import annotations

import math

import numpy as np

BEARING_MM = 40.0  # the steering shaft's bearing in the bulkhead (HIRAKU's choice, no source)
BRIDGE_MAX_MM = 250.0  # a gap longer than this is not bridged by a bracket (left for the layout)
STEEL_KG_M3 = 7850.0
JOINT_GAP_MM = 2.0
NOT_TOUCHING_VIA = ("wire", "hose")


def _cube(c, s: float) -> dict:
    return {"center_mm": [float(x) for x in c], "size_mm": [s, s, s]}


def _bodies(design: dict):
    from hiraku.solvers.k4_geometry import model as k4

    return k4.bodies(design)


def _gap(design: dict, bs, pid: str, to: str) -> float | None:
    from hiraku.solvers.k4_geometry.joints import point_gap
    from hiraku.solvers.k4_geometry.model import shape_overlap

    me = bs.get(f"part:{pid}")
    if me is None:
        return None
    nodes = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    sups = {s["id"]: s["position_mm"] for s in design.get("supports", [])}
    if to in nodes:
        return point_gap(np.asarray(nodes[to], float), me)
    other = bs.get(f"part:{to}") or bs.get(f"member:{to}")
    if other is not None:
        return max(0.0, -shape_overlap(me, other))
    if to in sups:
        return point_gap(np.asarray(sups[to], float), me)
    return None


def _touching_member(bs, pid: str, near) -> str | None:
    """The frame member the part touches nearest to `near` (a point), or None."""
    from hiraku.solvers.k4_geometry.model import shape_overlap

    me = bs[f"part:{pid}"]
    best = None
    for k, b in bs.items():
        if b.kind != "member":
            continue
        if -shape_overlap(me, b) <= JOINT_GAP_MM:
            d = float(np.linalg.norm(b.obb.center - near))
            if best is None or d < best[0]:
                best = (d, k.split(":", 1)[1])
    return best[1] if best else None


def _add(design: dict, part: dict) -> dict:
    design["parts"].append(part)
    return part


def bearing(design: dict) -> str | None:
    """Step 2: the intermediate shaft's bearing in the front bulkhead."""
    parts = {p["id"]: p for p in design["parts"]}
    shaft, wall = parts.get("steering_shaft"), parts.get("bulkhead")
    if shaft is None or wall is None:
        return None
    from hiraku.solvers.k0_handcalc.rigid import rpy_matrix

    c = np.asarray(shaft["box"]["center_mm"], float)
    ax = rpy_matrix(shaft["box"].get("rpy_deg") or [0, 0, 0])[:, 0]
    x_wall = float(wall["box"]["center_mm"][0])
    if abs(ax[0]) < 1e-6:
        return None
    t = (x_wall - c[0]) / ax[0]
    if abs(t) > shaft["box"]["size_mm"][0] / 2.0:
        return None  # the shaft does not reach the bulkhead
    at = c + t * ax
    b = _add(design, {"id": "bearing_steering_shaft", "name": "中間軸の軸受け (隔壁)", "kind": "bearing",
                      "box": _cube(at, BEARING_MM), "mass_kg": 0.3,
                      "connects_to": [{"to": "bulkhead", "via": "bolt", "carries": "load"}]})
    shaft["connects_to"] = [c2 for c2 in shaft.get("connects_to") or [] if c2["to"] not in ("LDT", "RDT")]
    shaft["connects_to"].append({"to": b["id"], "via": "pin", "carries": "load"})
    return b["id"]


STRUT_D_MM, STRUT_T_MM = 20.0, 2.0  # a bracket strut: a round steel tube (HIRAKU's choice, no source)
# the parts held by one part named here (the requirement that holds them): the master cylinder on the bulkhead
HOLDER = {"brake_master_cylinder": "bulkhead"}
# by part id: the column on its bracket on the dash cross member, each pedal on its own stand (pedal_mount)
HOLDER_ID = {"steering_column": "bracket_column", "pedal_brake": "stand_pedal_brake", "pedal_accel": "stand_pedal_accel",
             # the rack on the frame's brackets at the tie rods' inner pivots, one each end
             "steering_rack": ("bracket_tie_FL", "bracket_tie_FR")}
# links that move (a pin or a spline between two parts) are not bridged by a fixed strut: a gap there is the layout's
MOVING_VIA = ("pin", "spline", "ball_joint")


def closest(body, x: np.ndarray) -> np.ndarray:
    """The point of a body's shape (its capsule, else its box) nearest to x."""
    if body.capsule is not None:
        c = body.capsule
        d = c.b - c.a
        t = float(np.clip((x - c.a) @ d / max(float(d @ d), 1e-12), 0.0, 1.0))
        on = c.a + t * d
        v = x - on
        n = float(np.linalg.norm(v))
        return on + (v / n * min(n, c.r) if n > 1e-9 else 0.0)
    local = body.obb.axes @ (x - body.obb.center)
    return body.obb.center + body.obb.axes.T @ np.clip(local, -body.obb.half, body.obb.half)


def nearest_points(a, b, rounds: int = 40) -> tuple[np.ndarray, np.ndarray]:
    """The nearest points of two convex shapes, by projecting back and forth from b's centre."""
    q = b.obb.center.copy()
    p = closest(a, q)
    for _ in range(rounds):
        q = closest(b, p)
        p = closest(a, q)
    return p, q


def strut_box(p: np.ndarray, q: np.ndarray, d: float = STRUT_D_MM) -> dict:
    """A box from p to q (its long side along p-q, d square), turned by rpy_deg as the design defines it."""
    v = q - p
    L = float(np.linalg.norm(v))
    yaw = math.degrees(math.atan2(v[1], v[0]))
    pitch = math.degrees(math.atan2(-v[2], math.hypot(v[0], v[1])))
    return {"center_mm": [float(x) for x in (p + q) / 2.0], "size_mm": [max(L, 1.0), d, d], "rpy_deg": [0.0, pitch, yaw]}


def _fit(design: dict, parts: dict, p: dict, me, other) -> str:
    to_id = other.id.split(":", 1)[1]
    fid = f"fit_{p['id']}_{to_id}"
    if fid not in parts:
        a, b = nearest_points(me, other)
        box = strut_box(a, b)
        parts[fid] = _add(design, {"id": fid, "name": f"{p.get('name', p['id'])} の金具", "kind": "bracket",
                                   "box": box, "mass_kg": STEEL_KG_M3 * math.pi * (STRUT_D_MM - STRUT_T_MM) * STRUT_T_MM
                                   * box["size_mm"][0] * 1e-9,
                                   "connects_to": [{"to": to_id, "via": "weld" if other.kind == "member" else "bolt",
                                                    "carries": "load"}]})
    return fid


def retarget_and_bridge(design: dict, only=None) -> dict:
    """Step 2 (see the module docstring). `only`: part ids to work on (None: all). A far joint to a node is dropped
    when the part already has a joint that touches (its load goes that way); the parts in HOLDER are held by that
    part. Returns counts."""
    bs = _bodies(design)
    parts = {p["id"]: p for p in design["parts"]}
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])}
    n_re = n_br = n_drop = 0
    for p in list(design["parts"]):
        if only is not None and p["id"] not in only:
            continue
        me = bs.get(f"part:{p['id']}")
        if me is None:
            continue
        links = list(p.get("connects_to") or [])
        holders = HOLDER_ID.get(p["id"], HOLDER.get(p["kind"]))
        holders = [h for h in ((holders,) if isinstance(holders, str) else holders or ()) if h in parts]
        if holders:
            links = [c for c in links if c["to"] not in nodes and c["to"] not in holders] + [
                {"to": h, "via": "bolt", "carries": "load"} for h in holders]
        kept, far = [], []
        for c in links:
            if c.get("via") in NOT_TOUCHING_VIA:
                kept.append(c)
                continue
            g = _gap(design, bs, p["id"], c["to"])
            if g is None or g <= JOINT_GAP_MM:
                kept.append(c)
                continue
            near = nodes.get(c["to"], me.obb.center)
            mem = _touching_member(bs, p["id"], near)
            if mem is not None:
                if all(x["to"] != mem for x in kept):
                    kept.append(dict(c, to=mem))
                n_re += 1
                continue
            far.append((g, c))
        # held: a fixed joint, or a shaft's pin or spline at its ends; a ball joint to a moving link does not hold
        touching = any(x.get("via") not in NOT_TOUCHING_VIA + ("ball_joint",) for x in kept)
        held = bool(holders) and any(x["to"] in holders for x in kept)
        for g, c in far:
            # a far fixed joint is dropped when the part touches its holder, or (to a node) anything else
            if touching and ((c["to"] in nodes) or (held and c.get("via") not in MOVING_VIA)):
                n_drop += 1
                continue
            if c["to"] in nodes and g > BRIDGE_MAX_MM:
                kept.append(c)
                continue
            other = bs.get(f"part:{c['to']}") or bs.get(f"member:{c['to']}")
            if other is None and c["to"] in nodes:
                cands = [b for b in bs.values() if b.kind == "member" and c["to"] in b.nodes]
                other = min(cands, key=lambda b: float(np.linalg.norm(b.obb.center - me.obb.center)), default=None)
            if other is None or g > BRIDGE_MAX_MM or (other.kind != "member" and c.get("via") in MOVING_VIA):
                kept.append(c)
                continue
            fid = _fit(design, parts, p, me, other)
            n_br += 1
            if all(x["to"] != fid for x in kept):
                kept.append(dict(c, to=fid))
            touching = True
        p["connects_to"] = kept
    return {"retargeted": n_re, "brackets": n_br, "dropped": n_drop}


def fenders(design: dict) -> int:
    """Step 4's fenders: joined to their corner's knuckle through a bracket."""
    parts = {p["id"]: p for p in design["parts"]}
    n = 0
    for p in list(design["parts"]):
        if p["kind"] != "fender" or p.get("connects_to"):
            continue
        sid = p["id"].split("_")[1] if p["id"].count("_") >= 2 else None
        knuckle = parts.get(f"knuckle_{sid}")
        if knuckle is None:
            continue
        p["connects_to"] = [{"to": knuckle["id"], "via": "bolt", "carries": "load"}]
        n += 1
    return n


# the parts this file joins (the arms, the damper and the ball joints are kit/arms.py's, the other parts
# kit/fittings.py's)
MINE_KINDS = ("steering", "steering_column", "steering_shaft", "steering_rack", "brake_master_cylinder",
              "brake_pedal", "accelerator_pedal", "fender")
MINE_IDS = ("bracket_column", "stand_pedal", "bearing_steering_shaft")


def mine(part: dict) -> bool:
    return part["kind"] in MINE_KINDS or part["id"].startswith(MINE_IDS)


def join(design: dict) -> dict:
    """Steps 2 to 4 for the steering, the pedals and the master cylinder, and the fenders (MINE_KINDS, MINE_IDS);
    step 1 (the corners' arms) is kit/arms.py's. Returns what was done."""
    brg = bearing(design)
    fen = fenders(design)
    done = retarget_and_bridge(design, {p["id"] for p in design["parts"] if mine(p)})
    return {"added": [brg] if brg else [], "fenders": fen, **done}

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
BRIDGE_MAX_MM = 600.0  # a gap longer than this is not bridged by a bracket (left for the layout)
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


# parts fixed to the frame that others may be fastened to (a bracket: a mount or a fitting welded to the frame)
LAMP_KINDS = ("headlamp", "stoplamp", "taillamp", "turn_signal")  # a cluster of lamps: each may hold the next
FIXED_KINDS = ("body", "frame", "gusset", "rollbar", "bracket") + LAMP_KINDS


def _anchored(design: dict) -> set[str]:
    """Parts fastened to the frame by fixed joints (not moving ones), directly to a member or through parts that
    are: they can hold others. A joint to a node does not count (it may still be one of the far ones)."""
    frame = {m["id"] for m in design.get("members", [])}
    fixed = {p["id"]: {c["to"] for c in p.get("connects_to") or [] if c.get("via") not in MOVING_VIA + NOT_TOUCHING_VIA}
             for p in design["parts"]}
    out = {pid for pid, to in fixed.items() if to & frame}
    grew = True
    while grew:
        grew = False
        for pid, to in fixed.items():
            if pid not in out and to & out:
                out.add(pid)
                grew = True
    return out


def owns_members(design: dict) -> set[str]:
    """Parts that are pieces of the frame made of members (the roll bar: members with part = its id): their joints
    are the members' welds at the nodes, not their box's."""
    return {m.get("part") for m in design.get("members", [])} & {p["id"] for p in design["parts"]}


def _touching_fixed_part(bs, pid: str, near, anchored: set[str] | None = None) -> str | None:
    """A fixed part (FIXED_KINDS: a sheet of the body, a piece of the frame, a bracket) the part touches, nearest
    `near`, that is itself fastened to the frame (`anchored`): two sheets resting on each other hold nothing."""
    from hiraku.solvers.k4_geometry.model import shape_overlap

    me = bs[f"part:{pid}"]
    r_me = float(np.linalg.norm(me.obb.half))
    best = None
    for k, b in bs.items():
        if b.kind not in FIXED_KINDS or k == f"part:{pid}":
            continue
        if anchored is not None and k.split(":", 1)[1] not in anchored:
            continue
        if float(np.linalg.norm(b.obb.center - me.obb.center)) > r_me + float(np.linalg.norm(b.obb.half)) + JOINT_GAP_MM:
            continue
        if -shape_overlap(me, b) <= JOINT_GAP_MM:
            d = float(np.linalg.norm(b.obb.center - near))
            if best is None or d < best[0]:
                best = (d, k.split(":", 1)[1])
    return best[1] if best else None


def _touching_member(bs, pid: str, near) -> str | None:
    """The frame member the part touches nearest to `near` (a point), or None."""
    from hiraku.solvers.k4_geometry.model import shape_overlap

    me = bs[f"part:{pid}"]
    r_me = float(np.linalg.norm(me.obb.half))
    best = None
    for k, b in bs.items():
        if b.kind != "member":
            continue
        # spheres round the two boxes apart: they cannot touch (skips the separating-axis test)
        if float(np.linalg.norm(b.obb.center - me.obb.center)) > r_me + float(np.linalg.norm(b.obb.half)) + JOINT_GAP_MM:
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
# parts carried at their two ends by moving joints (SY-10's shaft rule): the rest need a fixed joint to the frame
ENDS_HELD_KINDS = ("steering", "steering_shaft", "driveshaft", "pushrod", "tie_rod", "link",
                   "door")  # a door hangs on its hinges (pins) and its latch


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


CLEAR_TRIES = 8  # frame members tried, nearest first, for a strut that passes through nothing


def _clear_member(bs, me, first, pid: str):
    """The frame member to strut to: `first` if the strut from the part to it passes through no other body (a sheet
    of the body excepted: an anchor goes through it; the members welded at the target's nodes excepted), else the
    nearest member whose strut does; `first` when none is clear (the overlap is then H1-interference's)."""
    from hiraku.solvers.k4_geometry.obb import from_box, overlap

    # candidates by the distance of their centres (cheap); the strut itself by the nearest points of the shapes
    members = sorted((b for b in bs.values() if b.kind == "member"),
                     key=lambda b: float(np.linalg.norm(b.obb.center - me.obb.center)))
    for cand in [first] + [m for m in members if m is not first][:CLEAR_TRIES]:
        a, b = nearest_points(me, cand)
        box = strut_box(a, b)
        sb = from_box(box["center_mm"], box["size_mm"], box["rpy_deg"])
        r_sb = float(np.linalg.norm(sb.half))
        hit = False
        for k, o in bs.items():
            if o is me or o is cand or o.kind == "body" or k == f"part:{pid}":
                continue
            if o.kind == "member" and o.nodes & cand.nodes:
                continue
            if float(np.linalg.norm(o.obb.center - sb.center)) > r_sb + float(np.linalg.norm(o.obb.half)):
                continue  # their spheres apart: no overlap
            if overlap(sb, o.obb) > 1.0:
                hit = True
                break
        if not hit:
            return cand
    return first


def _fit(design: dict, parts: dict, p: dict, me, other) -> str:
    to_id = other.id.split(":", 1)[1]
    fid = f"fit_{p['id']}_{to_id}"
    if fid not in parts:
        a, b = nearest_points(me, other)
        box = strut_box(a, b)
        # carried as what it is fixed to: a member's nodes (a frame piece), a part's own carried_by; on an unsprung
        # part (the knuckle) none, so it rides with that corner
        if other.kind == "member":
            carried = sorted(other.nodes)
        else:
            carried = list(parts.get(to_id, {}).get("carried_by") or [])
        extra = {"carried_by": carried} if carried else {}
        # a strut that crosses a sheet of the body is an anchor: a bolt through the sheet into the frame
        # (k4_geometry.model.THROUGH_PANEL_KINDS lets it pass)
        from hiraku.solvers.k4_geometry.obb import from_box, overlap

        sb = from_box(box["center_mm"], box["size_mm"], box["rpy_deg"])
        through = any(b.kind == "body" and overlap(sb, b.obb) > 1.0 for b in _bodies(design).values())
        parts[fid] = _add(design, {"id": fid, "name": f"{p.get('name', p['id'])} の" + ("止め (板を通す)" if through else "金具"),
                                   "kind": "anchor" if through else "bracket", **extra,
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
    # the fixed parts first (brackets, sheets, frame pieces), so that the parts resting on them find them anchored
    # (the lamps after the other fixed parts, in a fixed order, so that one fastened to the frame holds the next)
    for p in sorted(list(design["parts"]), key=lambda x: (x["kind"] not in FIXED_KINDS, x["kind"] in LAMP_KINDS,
                                                           x["id"] if x["kind"] in LAMP_KINDS else "")):
        if only is not None and p["id"] not in only:
            continue
        me = bs.get(f"part:{p['id']}")
        if me is None:
            continue
        links = list(p.get("connects_to") or [])
        holders = HOLDER_ID.get(p["id"], HOLDER.get(p["kind"]))
        if p["kind"] == "door" and holders is None:
            # a door hangs on its hinges and its latch (designgen's side_opening: side_door_hinge_<side><n>, _latch_)
            side = p["id"].rsplit("_", 1)[-1]
            holders = [q for q in parts if q.startswith((f"side_door_hinge_{side}", f"side_door_latch_{side}"))]
        if p["kind"] == "seat_belt" and holders is None:
            # a belt goes with its seat (the driver's slides with the seat): held by the seat of its side
            holders = f"seat_{p['id'].rsplit('_', 1)[-1]}"
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
            # the frame member it touches, else a fixed part it rests on (a seat on the floor sheet)
            mem = _touching_member(bs, p["id"], near) or _touching_fixed_part(bs, p["id"], near, _anchored(design))
            if mem is not None:
                if all(x["to"] != mem for x in kept):
                    kept.append(dict(c, to=mem))
                n_re += 1
                continue
            far.append((g, c))
        # held: a fixed joint; a pin or a spline holds only a part carried at its ends (a shaft, a rod, the steering
        # wheel on its column); a ball joint to a moving link holds nothing
        ends_held = p["kind"] in ENDS_HELD_KINDS
        touching = any(x.get("via") not in NOT_TOUCHING_VIA + ("ball_joint",)
                       and (ends_held or x.get("via") not in MOVING_VIA) for x in kept)
        held = bool(holders) and any(x["to"] in holders for x in kept)
        dropped_here = False
        far.sort(key=lambda gc: gc[1]["to"] not in holders)  # the holders' joints first: the others may then drop
        for g, c in far:
            # a far fixed joint is dropped when the part touches its holder, or (to a node) anything else
            # a sheet of the body is held at each of its fastenings (one strut alone would let it swing about it)
            if touching and c.get("via") not in MOVING_VIA and not (p["kind"] == "body" and not held):
                n_drop += 1
                dropped_here = True
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
            if other.kind == "member":
                other = _clear_member(bs, me, other, p["id"])
            fid = _fit(design, parts, p, me, other)
            n_br += 1
            if all(x["to"] != fid for x in kept):
                kept.append(dict(c, to=fid))
            touching = True
        if dropped_here and not any(x.get("carries") in (None, "load") for x in kept):
            # its weight went to the joints dropped: it now goes through the joint that holds it (a wheel on its
            # column's spline carries the wheel, as a shaft's ends carry the shaft)
            hold = next((x for x in kept if x.get("via") not in NOT_TOUCHING_VIA + ("ball_joint",)), None)
            if hold is not None:  # the same joint also carries the load (it still passes the steer or the torque on)
                kept.append(dict(hold, carries="load"))
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


# an occupant's segments joined each to the next toward the trunk (kit/body: they overlap at the joints)
OCCUPANT_PARENT = {"foot": "shank", "shank": "thigh", "thigh": None, "hand": "forearm", "forearm": "arm", "arm": None,
                   "head": "neck", "neck": None}


def occupants(design: dict) -> int:
    """Each segment of a person joined to its neighbour toward the trunk (a pin), not to the trunk across the body."""
    ids = {p["id"] for p in design["parts"]}
    n = 0
    for p in design["parts"]:
        if p["kind"] != "occupant" or "." not in p["id"]:
            continue
        person, seg = p["id"].split(".", 1)
        name, _, side = seg.partition("_")
        if name not in OCCUPANT_PARENT:
            continue
        up = OCCUPANT_PARENT[name]
        to = person if up is None else f"{person}.{up}" + (f"_{side}" if side else "")
        if to not in ids:
            continue
        p["connects_to"] = [{"to": to, "via": "pin", "carries": "load"}]
        n += 1
    return n


def mounts(design: dict) -> int:
    """A mount (part_mounts: kind bracket, id mount_*) that touches no frame member is made again between the part it
    holds and the frame member at the node it names, nearest that part: a box from the part's nearest point to the
    member's, as thick as the mount's thinnest side, carried by the member's nodes and welded to it."""
    bs = _bodies(design)
    parts = {p["id"]: p for p in design["parts"]}
    nodes = {n["id"] for n in design.get("nodes", [])}
    held_by = {}
    for p in design["parts"]:
        for c in p.get("connects_to") or []:
            if c["to"].startswith("mount_"):
                held_by.setdefault(c["to"], p["id"])
    n = 0
    for p in design["parts"]:
        if not p["id"].startswith("mount_") or p["kind"] != "bracket" or f"part:{p['id']}" not in bs:
            continue
        if _touching_member(bs, p["id"], bs[f"part:{p['id']}"].obb.center) is not None:
            continue
        held = bs.get(f"part:{held_by.get(p['id'], '')}")
        named = [c["to"] for c in p.get("connects_to") or [] if c["to"] in nodes]
        cands = [b for b in bs.values() if b.kind == "member" and (b.nodes & set(named) or not named)]
        if held is None or not cands:
            continue
        member = min(cands, key=lambda b: float(np.linalg.norm(nearest_points(held, b)[0] - nearest_points(held, b)[1])))
        a, b = nearest_points(held, member)
        d = max(10.0, float(min(p["box"]["size_mm"])))
        p["box"] = strut_box(a, b, d)
        p["carried_by"] = sorted(member.nodes)
        mid = member.id.split(":", 1)[1]
        p["connects_to"] = [{"to": mid, "via": "weld", "carries": "load"}]
        n += 1
    return n


AXIS_VIA = ("pin", "bushing", "ball_joint", "hinge")


def _axis(body, via: str) -> list[float]:
    """The joint's axis (HIRAKU's reading, no source): a ball joint's along its link (a slender part's long side),
    a pin's, a bushing's and a hinge's across the vehicle (y: the knees, elbows, pedals and pushrods turn about it)
    unless the part is a slender link, whose pin is across it in the horizontal plane."""
    if via == "ball_joint" and body is not None:
        v = body.capsule.b - body.capsule.a if body.capsule is not None else body.obb.axes[0]
        n = float(np.linalg.norm(v))
        return [float(x) for x in v / n] if n > 1e-9 else [1.0, 0.0, 0.0]
    return [0.0, 1.0, 0.0]


def annotate(design: dict) -> int:
    """at_mm on every mechanical joint (not wire or hose) that has none: the middle of the nearest points of the two
    shapes (a node's or a support's own point), which lies on both within the joint's gap; axis on the pins,
    bushings, ball joints and hinges (_axis). Returns the joints written."""
    bs = _bodies(design)
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])}
    sups = {s["id"]: np.asarray(s["position_mm"], float) for s in design.get("supports", [])}
    n = 0
    own = owns_members(design)
    for p in design["parts"]:
        me = bs.get(f"part:{p['id']}")
        for c in p.get("connects_to") or []:
            if c.get("via") in NOT_TOUCHING_VIA or me is None:
                continue
            if "at_mm" not in c:
                to = c["to"]
                pt = nodes.get(to, sups.get(to))
                if pt is not None and p["id"] in own:
                    at = pt  # a frame piece of members (the roll bar): its tubes are welded at the node
                elif pt is not None:
                    a = closest(me, pt)
                    at = (a + pt) / 2.0
                else:
                    other = bs.get(f"part:{to}") or bs.get(f"member:{to}")
                    if other is None:
                        continue
                    a, b = nearest_points(me, other)
                    at = (a + b) / 2.0
                c["at_mm"] = [round(float(x), 3) for x in at]
                n += 1
            if c.get("via") in AXIS_VIA and "axis" not in c:
                c["axis"] = _axis(me, c["via"])
    return n


PUSHROD_D_MM = 10.0  # the brake pedal's pushrod (HIRAKU's choice, no source)


def brake_master(design: dict) -> str | None:
    """The master cylinder on the front face of the bulkhead in line with the brake pedal (its y and z), and a
    pushrod (kind pushrod) from the pedal's nearest point to the cylinder's rear face, through the bulkhead: the
    pedal pushes the cylinder only where the two meet (the requirement), so the cylinder sits ahead of the pedal."""
    parts = {p["id"]: p for p in design["parts"]}
    m, pedal, wall = parts.get("brake_master"), parts.get("pedal_brake"), parts.get("bulkhead")
    if m is None or pedal is None or wall is None:
        return None
    x_face = float(wall["box"]["center_mm"][0]) + float(wall["box"]["size_mm"][0]) / 2.0
    L = float(m["box"]["size_mm"][0])
    py, pz = float(pedal["box"]["center_mm"][1]), float(pedal["box"]["center_mm"][2])
    m["box"] = {"center_mm": [x_face + L / 2.0, py, pz], "size_mm": list(m["box"]["size_mm"])}
    bs = _bodies(design)
    rear = np.array([x_face, py, pz])
    a = closest(bs["part:pedal_brake"], rear)
    rod = _add(design, {"id": "pushrod_brake", "name": "ブレーキの押し棒", "kind": "pushrod",
                        "box": strut_box(a, rear, PUSHROD_D_MM), "mass_kg": 0.1,
                        "connects_to": [{"to": "brake_master", "via": "pin", "carries": "brake"}],
                        # carried as the pedal is (it moves with the pedal, not with a wheel)
                        **({"carried_by": list(pedal["carried_by"])} if pedal.get("carried_by") else {})})
    pedal["connects_to"] = [c for c in pedal.get("connects_to") or [] if c["to"] != "brake_master"] + [
        {"to": rod["id"], "via": "pin", "carries": "brake"}]
    m["connects_to"] = [c for c in m.get("connects_to") or [] if c["to"] not in {n["id"] for n in design["nodes"]}]
    if all(c["to"] != "bulkhead" for c in m["connects_to"]):
        m["connects_to"].append({"to": "bulkhead", "via": "bolt", "carries": "load"})
    return rod["id"]


def join(design: dict) -> dict:
    """Every part's joints made to touch: the occupants' segments to their neighbours, the mounts between their part
    and the frame, the shaft's bearing, the fenders on their knuckles, then step 2 for every other part (the corners'
    arms, damper and ball joints are kit/arms.py's and already touch). Returns what was done."""
    occ = occupants(design)
    rod = brake_master(design)
    mnt = mounts(design)
    brg = bearing(design)
    fen = fenders(design)
    own = owns_members(design)
    done = retarget_and_bridge(design, {p["id"] for p in design["parts"] if p["kind"] not in ("occupant",)
                                        and p["id"] not in own})
    done["at_mm"] = annotate(design)
    return {"added": [x for x in (brg, rod) if x], "fenders": fen, "occupants": occ, "mounts": mnt, **done}

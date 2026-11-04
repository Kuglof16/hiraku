"""The system each part belongs to and what it is joined to (part.system, part.connects_to), so
code can follow a chain end to end: the motor to the driven wheels, the steering wheel to the
knuckles, the brake pedal to the braked wheels, the battery to the motor.

    links = systems.links(design)        # {part id: {"system": ..., "connects_to": [{to, via, carries?}]}}
    systems.write(design)                # into design.parts, when the contract has the fields

The links follow from the parts the kits place (their kinds and ids) and the frame:
- mounting: a part with carried_by is bolted (frame pieces and the roll bar welded) to those
  frame nodes, carrying load; an occupant rests on its seat, the cargo on the deck;
- drive (torque): motor -> reducer -> driveshaft_<s> -> wheel_<s> -> support <s>, for every
  driven support (no driveshafts: the reducer reaches no wheel);
- steering (steer): steering wheel (kind steering) -> column -> intermediate shaft -> rack ->
  tie_rod_<s> -> knuckle_<s> -> wheel_<s> -> support <s>, for every steered support;
- brake (brake): brake pedal -> master cylinder -> caliper_<s> -> wheel_<s> -> support <s>, for
  every braked support;
- power: battery -> main switch -> controller -> motor (a second battery pack wired to the first, in series:
  ladder_4w battery_split); the wiring box (12 V) from the battery,
  the lamps from the wiring box; the accelerator pedal signals the controller;
- the corners (load): suspension part to what holds its body-side hard points (body_mounts: the
  tower's and legs' end nodes, the brackets; else its support's frame node) and to the support,
  knuckle to the suspension part, wheel to the knuckle and to the support (it stands there),
  caliper and toe link to the knuckle, toe link to the bracket at its inner end (else the frame node);
- the steering column to the dash cross member that holds it (bolt, steer; ladder_4w.build_dash);
- brackets (kind bracket) are welded to their carried_by nodes;
- restraint: each seat belt to its seat and to the cross member behind the seat;
- rops: the roll bar to the rail nodes it stands on (welded).

The links go one way (each is written on one of its two parts); a reader takes them both ways.
"""

from __future__ import annotations

from typing import Any

Record = dict[str, Any]

SYSTEM_OF_KIND = {
    "battery": "battery", "switch": "battery", "fuse": "battery", "contactor": "battery", "breaker": "battery",
    "controller": "control", "accelerator_pedal": "control", "electrical": "electrical",
    "motor": "drive", "reducer": "drive", "driveshaft": "drive",
    "steering": "steering", "steering_column": "steering", "steering_shaft": "steering", "steering_rack": "steering",
    "tie_rod": "steering", "brake_pedal": "brake", "brake_master_cylinder": "brake", "brake_caliper": "brake",
    "wheel": "suspension", "suspension": "suspension", "knuckle": "suspension",
    "link": "suspension", "damper": "suspension", "ball_joint": "suspension",  # kit/arms.py
    "seat": "seat", "seat_belt": "restraint", "rollbar": "rops", "occupant": "occupant", "cargo": "cargo",
    "body": "body", "frame": "frame", "gusset": "frame", "bracket": "frame",
    "headlamp": "lighting", "taillamp": "lighting", "stoplamp": "lighting", "turn_signal": "lighting",
    "side_net": "restraint", "net_clip": "restraint", "fender": "body",
}
WELDED_KINDS = ("frame", "gusset", "rollbar", "bracket")  # joined to their nodes by welds
# the suspension's hard points on the body (K2's mounts): the arms and the damper are joined there
BODY_MOUNTS = ("lca_front", "lca_rear", "uca_front", "uca_rear", "damper_upper")
MOUNT_TOL_MM = 1.0
DASH_MEMBER = "dash_cross"  # the frame member across the dash that holds the steering column (ladder_4w.build_dash)
SWITCH_KINDS = ("fuse", "switch", "contactor", "breaker")
LAMP_KINDS = ("headlamp", "taillamp", "stoplamp", "turn_signal")


def contract_has_fields() -> bool:
    """Whether the contract's part has system and connects_to (only then are they written)."""
    from hiraku import contract

    props = contract.schema()["$defs"]["part"]["properties"]
    return "system" in props and "connects_to" in props


def system_of(part: Record) -> str:
    if part["id"].startswith("toe_link_"):
        return "suspension"
    if "." in part["id"] and part["kind"] == "occupant":  # a body part of an occupant (kit/body.py)
        return "occupant"
    return SYSTEM_OF_KIND.get(part["kind"], "other")


def _c(to: str, via: str, carries: str | None = None) -> Record:
    out = {"to": to, "via": via}
    if carries:
        out["carries"] = carries
    return out


def _cross_member_at(design: Record, x: float) -> str | None:
    """The member running across the frame at x (a cross member), or None."""
    nodes = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    best = None
    for m in design.get("members", []):
        a, b = nodes[m["node_a"]], nodes[m["node_b"]]
        if abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) > 1e-6 and abs(a[2] - b[2]) < 1e-6 and m["id"].startswith("cross_"):
            if best is None or abs(a[0] - x) < best[0]:
                best = (abs(a[0] - x), m["id"])
    return best[1] if best else None


def links(design: Record) -> dict[str, Record]:
    """{part id: {"system", "connects_to"}} for every part of the design."""
    parts = design.get("parts", [])
    ids = {p["id"] for p in parts}
    kinds: dict[str, list[str]] = {}
    for p in parts:
        kinds.setdefault(p["kind"], []).append(p["id"])
    out = {p["id"]: {"system": system_of(p), "connects_to": []} for p in parts}

    def add(pid, *conns):
        if pid in out:
            out[pid]["connects_to"].extend(conns)

    def first(kind):
        return (kinds.get(kind) or [None])[0]

    for p in parts:  # mounting to the frame
        pid = p["id"]
        if p["kind"] == "occupant":
            seat = _seat_of(p, parts)
            if seat and "." not in pid:
                add(pid, _c(seat, "rest", "load"))
            elif "." in pid:
                add(pid, _c(pid.split(".", 1)[0], "pin", "load"))
            continue
        if p["kind"] == "cargo":
            deck = next((q["id"] for q in parts if q["id"] == "deck"), None)
            if deck:
                add(pid, _c(deck, "rest", "load"))
            continue
        via = "weld" if p["kind"] in WELDED_KINDS else "bolt"
        for c in p.get("connects_to") or []:  # what the part was built with (a through tube's own member)
            if c.get("to") not in (p.get("carried_by") or []):
                add(pid, dict(c))
        nodes = p.get("carried_by") or []
        if p["kind"] == "rollbar":
            from hiraku.designgen.kit.safety import rollbar_feet

            nodes = rollbar_feet(design) or nodes
        for n in nodes:
            add(pid, _c(n, via, "load"))
    sups = design.get("supports", [])
    for s in sups:  # the corners
        sid = s["id"]
        susp, knuckle, wheel = f"susp_{sid}", f"knuckle_{sid}", f"wheel_{sid}"
        held = body_mounts(design, s)
        arm = arm_parts(sid, ids)  # kit/arms.py: the arms' legs, damper, ball joints (in place of the box)
        if len(arm) == len(ARM_ROLES):
            # each pivot and the damper's top: a bushing at the bracket (or node) that holds it
            owner = {"lca_front": arm["lca_f"], "lca_rear": arm["lca_r"], "uca_front": arm["uca_f"],
                     "uca_rear": arm["uca_r"], "damper_upper": arm["damper"]}
            for mount in BODY_MOUNTS:
                if mount in held:
                    add(owner[mount], _c(held[mount], "bushing", "load"))
            add(arm["lca_x"], _c(arm["lca_f"], "weld", "load"), _c(arm["lca_r"], "weld", "load"))
            add(arm["damper"], _c(arm["lca_x"], "bushing", "load"))  # damper_lower on the lower arm's cross tube
            add(arm["bj_lower"], _c(arm["lca_f"], "weld", "load"), _c(arm["lca_r"], "weld", "load"))
            add(arm["bj_upper"], _c(arm["uca_f"], "weld", "load"), _c(arm["uca_r"], "weld", "load"))
            add(knuckle, _c(arm["bj_lower"], "ball_joint", "load"), _c(arm["bj_upper"], "ball_joint", "load"))
            hub = knuckle
        else:
            if held:  # the arms' pivots and the damper's top: frame nodes (towers, legs) or brackets
                for mount in BODY_MOUNTS:
                    if mount in held:
                        add(susp, _c(held[mount], "bushing", "load"))
            elif s.get("frame_node"):
                add(susp, _c(s["frame_node"], "bushing", "load"))
            add(susp, _c(sid, "bushing", "load"))
            add(knuckle, _c(susp, "ball_joint", "load"))
            hub = knuckle if knuckle in ids else susp
        add(wheel, _c(hub, "bolt", "load"), _c(sid, "rest", "load"))
        add(f"caliper_{sid}", _c(hub, "bolt", "load"))
        toe = f"toe_link_{sid}"
        if toe in ids:
            add(toe, _c(knuckle, "ball_joint", "load"))
            inner = held.get("tie_inner") if held else None
            if inner or s.get("frame_node"):
                add(toe, _c(inner or s["frame_node"], "ball_joint", "load"))
    # drive
    motor, reducer = first("motor"), first("reducer")
    if motor and reducer:
        add(motor, _c(reducer, "spline", "torque"))
    for s in sups:
        if not s["actuation"]["drive"]:
            continue
        shaft, wheel = f"driveshaft_{s['id']}", f"wheel_{s['id']}"
        if shaft in ids and reducer:
            add(reducer, _c(shaft, "spline", "torque"))
            add(shaft, _c(wheel, "spline", "torque"))
            add(wheel, _c(s["id"], "rest", "torque"))
    # steering
    chain = [first("steering"), first("steering_column"), first("steering_shaft"), first("steering_rack")]
    chain = [c for c in chain if c]
    vias = {"steering_column": "spline", "steering_shaft": "pin", "steering_rack": "spline"}
    for a, b in zip(chain, chain[1:]):
        add(a, _c(b, vias.get(_kind(parts, b), "spline"), "steer"))
    column = first("steering_column")
    if column and any(m["id"] == DASH_MEMBER for m in design.get("members", [])):
        add(column, _c(DASH_MEMBER, "bolt", "steer"))  # the column is held by the dash cross member (its bracket)
    rack = first("steering_rack")
    for s in sups:
        if not s["actuation"]["steer"]:
            continue
        rod, knuckle, wheel = f"tie_rod_{s['id']}", f"knuckle_{s['id']}", f"wheel_{s['id']}"
        if rack and rod in ids:
            add(rack, _c(rod, "ball_joint", "steer"))
            add(rod, _c(knuckle, "ball_joint", "steer"))
            add(knuckle, _c(wheel, "bolt", "steer"))
            add(wheel, _c(s["id"], "rest", "steer"))
    # brake
    pedal, master = first("brake_pedal"), first("brake_master_cylinder")
    if pedal and master:
        add(pedal, _c(master, "pin", "brake"))
    for s in sups:
        cal, wheel = f"caliper_{s['id']}", f"wheel_{s['id']}"
        if s["actuation"]["brake"] and master and cal in ids:
            add(master, _c(cal, "hose", "brake"))
            add(cal, _c(wheel, "friction", "brake"))
            add(wheel, _c(s["id"], "rest", "brake"))
    # power and signals
    battery, controller = first("battery"), first("controller")
    switch = next((k for kind in SWITCH_KINDS for k in kinds.get(kind, [])), None)
    if battery and controller:
        if switch:
            add(battery, _c(switch, "wire", "power"))
            add(switch, _c(controller, "wire", "power"))
        else:
            add(battery, _c(controller, "wire", "power"))
    packs = kinds.get("battery", [])
    for later, before in zip(packs[1:], packs):  # more packs: in series, each to the one before (the first's chain)
        add(later, _c(before, "wire", "power"))
    if controller and motor:
        add(controller, _c(motor, "wire", "power"))
    aux = first("electrical")
    if aux and battery:
        add(aux, _c(battery, "wire", "power"))
    for kind in LAMP_KINDS:
        for lamp in kinds.get(kind, []):
            if aux:
                add(lamp, _c(aux, "wire", "power"))
    accel = first("accelerator_pedal")
    if accel and controller:
        add(accel, _c(controller, "wire", "signal"))
    for k in ("accelerator_pedal", "brake_pedal"):  # pedals stand on the footwell floor
        for pid in kinds.get(k, []):
            floor = _floor_under(design, pid)
            if floor:
                add(pid, _c(floor, "bolt", "load"))
    if master:
        add(master, _c(_nearest_body(design, master), "bolt", "load"))
    # restraint
    for belt in kinds.get("seat_belt", []):
        seat = _seat_of(_part(parts, belt), parts)
        if seat:
            add(belt, _c(seat, "bolt", "load"))
        cross = _cross_member_at(design, float(_part(parts, belt)["box"]["center_mm"][0]))
        if cross:
            add(belt, _c(cross, "bolt", "load"))
    return out


ARM_ROLES = ("lca_f", "lca_r", "lca_x", "uca_f", "uca_r", "damper", "bj_lower", "bj_upper")  # kit/arms.part_ids


def arm_parts(sid: str, ids) -> dict[str, str]:
    """{role: part id} of the arms, damper and ball joints kit/arms.py placed for support sid."""
    from hiraku.designgen.kit.arms import part_ids

    return {role: pid for role, pid in part_ids(sid).items() if pid in ids}


def body_mounts(design: Record, support: Record) -> dict[str, str]:
    """{hard point: what holds it} for a support's body-side hard points (BODY_MOUNTS and tie_inner): the
    design node at the point (a tower's top, a leg's end), else the bracket part whose box holds it.
    Empty without hardpoints_mm or when nothing holds them (a design without mounts)."""
    import numpy as np

    hp = (support.get("suspension") or {}).get("hardpoints_mm") or {}
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])}
    brackets = [p for p in design.get("parts", []) if p["kind"] == "bracket"]
    out = {}
    for k in (*BODY_MOUNTS, "tie_inner"):
        if k not in hp:
            continue
        q = np.asarray(hp[k], float)
        # the bracket whose box holds the point (a clevis welded on, kit/mounts, kit/arms), else the node at it
        at = next((p["id"] for p in brackets
                   if np.all(np.abs(q - np.asarray(p["box"]["center_mm"], float))
                             <= np.asarray(p["box"]["size_mm"], float) / 2.0 + MOUNT_TOL_MM)), None)
        if at is None:
            at = next((n for n, x in nodes.items() if float(np.linalg.norm(x - q)) <= MOUNT_TOL_MM), None)
        if at is not None:
            out[k] = at
    return out


def _part(parts, pid):
    return next(p for p in parts if p["id"] == pid)


def _kind(parts, pid):
    return _part(parts, pid)["kind"]


def _seat_of(p: Record, parts: list[Record]) -> str | None:
    seats = [q for q in parts if q["kind"] == "seat"]
    if not seats:
        return None
    y = float(p["box"]["center_mm"][1])
    return min(seats, key=lambda s: abs(float(s["box"]["center_mm"][1]) - y))["id"]


def _floor_under(design: Record, pid: str) -> str | None:
    from hiraku.solvers.k4_geometry.people import floor_under

    c = _part(design["parts"], pid)["box"]["center_mm"]
    f = floor_under(design, float(c[0]), float(c[1]), float(c[2]))
    return f["id"] if f else None


def _nearest_body(design: Record, pid: str) -> str:
    import numpy as np

    c = np.asarray(_part(design["parts"], pid)["box"]["center_mm"], float)
    bodies = [p for p in design["parts"] if p["kind"] == "body"]

    def gap(p):
        lo = np.asarray(p["box"]["center_mm"], float) - np.asarray(p["box"]["size_mm"], float) / 2.0
        hi = lo + np.asarray(p["box"]["size_mm"], float)
        return float(np.linalg.norm(np.maximum(0.0, np.maximum(lo - c, c - hi))))
    return min(bodies, key=gap)["id"]


def write(design: Record, force: bool = False) -> bool:
    """Write system and connects_to into every part (when the contract has the fields, or force);
    returns whether it wrote them."""
    if not (force or contract_has_fields()):
        return False
    for pid, rec in links(design).items():
        p = _part(design["parts"], pid)
        p["system"] = rec["system"]
        if rec["connects_to"]:
            p["connects_to"] = rec["connects_to"]
    return True


def reach(design_links: dict[str, Record], start: str, carries: str) -> set[str]:
    """Everything reached from `start` along links that carry `carries`, either way along a link."""
    adj: dict[str, set[str]] = {}
    for pid, rec in design_links.items():
        for c in rec["connects_to"]:
            if c.get("carries") == carries:
                adj.setdefault(pid, set()).add(c["to"])
                adj.setdefault(c["to"], set()).add(pid)
    seen, todo = {start}, [start]
    while todo:
        for n in adj.get(todo.pop(), ()):
            if n not in seen:
                seen.add(n)
                todo.append(n)
    return seen

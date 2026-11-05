"""The parts a vehicle needs to be allowed to run: brakes, seat belts, the battery's main switch,
lamps, and the roll bar as a part (its tubes are frame members).

    safety.place_brakes(lay, pedal="pedal_brake", panel="front_body", floor="floor_front", pose=driver_pose)
    safety.place_belts(lay, seats=("seat_R", "seat_L"))
    safety.place_main_switch(lay, beside="aux")
    safety.place_lamps(lay, front="front_body", rear_x=-2100.0, rear_z=380.0, rear_body="deck")  # public roads only
    safety.add_rollbar_part(design)                    # on the finished design, after the roll bar's members

Why each is there (the rule that asks for it):
- brakes on every wheel with actuation.brake, worked from a brake pedal through a master cylinder
  and a caliper at each wheel: HIRAKU's systems check (a brake chain from a brake pedal to every
  braked support). On public roads in Japan: Safety Regulations for Road Vehicles (道路運送車両の
  保安基準) article 12, braking devices (not checked in this repository). For off-road vehicles
  ANSI/ROHVA 1 (recreational off-highway vehicles) has brake requirements (not checked).
- a seat belt at every seat, on the seat and on the frame: HIRAKU's systems check (restraint at
  every seat). Public roads: Safety Regulations for Road Vehicles article 22-3, seat belts (not
  checked). Off-road: ANSI/ROHVA 1 occupant retention (not checked).
- the battery's main switch between the battery and the controller: HIRAKU's systems check (a
  fuse, switch, contactor or breaker in the chain from the battery to the motor). UN Regulation
  No. 100 (electric power trains) covers working voltages above 60 V DC; the 51.2 V pack is below
  that, so it does not ask for the switch here. The 60 V: R100 para. 2.17 "High Voltage", read on
  2026-11-03 for data/knowledge/battery.yaml (BT-02; README, the knowledge rules' row). R100 para. 2.38
  takes the highest working voltage (at full charge), not the nominal 51.2 V; whether the pack's
  full-charge voltage stays at or below 60 V, and the rest of this reading, are not checked here.
  The regulations' article numbers above are as cited; their texts were not read in this repository.
- lamps (headlamps, tail lamps, stop lamps, turn signals) only on public roads: Safety Regulations
  for Road Vehicles articles 32 (headlamps), 37 (tail lamps), 39 (stop lamps) and 41 (direction
  indicators) (not checked). Off public roads none are placed.
- the roll bar (ROPS): HIRAKU's systems check (a part of system rops on the frame). Off-road:
  ANSI/ROHVA 1 asks for a ROPS (it refers to ISO 3471 for the test; not checked).

Masses (assumptions, no source) come out of parts already there, so the vehicle's mass does not
change: the master cylinder out of the footwell floor, each caliper out of its corner's
suspension part, each belt out of its seat, the main switch out of the wiring box, the front lamps
out of the front body and the rear lamps out of the deck. The roll bar part weighs 0 kg: its
members carry the mass.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from hiraku.designgen.kit.package import Item, Layout, bottom_at, front_at, rear_at, right_at

Record = dict[str, Any]

# Masses, sizes and offsets below: HIRAKU's choices (assumptions, no source)
# brakes (assumptions)
MASTER_KG = 0.8  # master cylinder with its reservoir
MASTER_SIZE_MM = (120.0, 70.0, 70.0)  # along x (its bore), across, high
MASTER_FOOT_GAP_MM = 50.0  # the master cylinder's bottom this far above the top of the large driver's foot
CALIPER_KG = 1.5  # one caliper with its pads
CALIPER_SIZE_MM = (60.0, 50.0, 90.0)
DISC_RADIUS_MM = 110.0  # the caliper's middle this far behind the wheel centre (on the disc's edge)
CALIPER_INSET_MM = 40.0  # the caliper's middle this far in from the wheel's inner face
# seat belts (assumptions): the belt's anchor and retractor as a strip behind the seat back, at its
# outboard edge and inside the seat's width
BELT_KG = 1.0
BELT_SIZE_MM = (50.0, 20.0, 300.0)  # along x, across, high
# main switch (assumptions)
SWITCH_KG = 0.3
SWITCH_SIZE_MM = (80.0, 80.0, 80.0)
# lamps (assumptions): kind, mass, size along x y z
LAMPS_FRONT = (("headlamp", "前照灯", 0.6, (40.0, 150.0, 100.0)), ("turn_signal", "前の方向指示器", 0.1, (30.0, 60.0, 40.0)))
LAMPS_REAR = (("taillamp", "尾灯", 0.2, (30.0, 100.0, 60.0)), ("stoplamp", "制動灯", 0.2, (30.0, 100.0, 60.0)),
              ("turn_signal", "後ろの方向指示器", 0.1, (30.0, 60.0, 40.0)))
LAMP_INSET_MM = 20.0  # front lamps this far in from the front body's side faces; rear lamps from the deck's
LAMP_GAP_MM = 10.0  # between lamps stacked on one side
ROLLBAR_PART = "rollbar"


def _corner(s: Record) -> str:
    return ("前" if s["position_mm"][0] >= 0 else "後") + ("左" if s["position_mm"][1] > 0 else "右")


def _take(lay: Layout, pid: str, mass: float) -> None:
    p = lay.part(pid)
    if p["mass_kg"] <= mass:
        raise ValueError(f"{pid} ({p['mass_kg']:g} kg) is lighter than the {mass:g} kg taken out of it")
    p["mass_kg"] = float(p["mass_kg"]) - mass


def place_brakes(lay: Layout, pedal: str, panel: str, floor: str, pose, suspension_prefix: str = "susp_") -> Record:
    """The master cylinder (on the panel's rear face, at the pedal's y, MASTER_FOOT_GAP_MM above the
    large driver's foot `pose`) and a caliper at every braked wheel (on the disc's rear edge, inside
    the wheel); returns {part id: mass}."""
    from hiraku.solvers.k4_geometry import people

    s = people.SIZES[pose.size]
    foot_top = max(float(max(g.toe[2], g.ankle[2])) + s["malleolus_height"] for g in pose.legs.values())
    py = float(lay.part(pedal)["box"]["center_mm"][1])
    lay.put(Item("brake_master", "ブレーキのマスターシリンダー", "brake_master_cylinder", MASTER_SIZE_MM, MASTER_KG),
            x=front_at(lay.rear_of(panel)), y=py, z=bottom_at(foot_top + MASTER_FOOT_GAP_MM))
    _take(lay, floor, MASTER_KG)
    out = {"brake_master": MASTER_KG}
    for sup in lay.design.get("supports", []):
        if not sup["actuation"]["brake"]:
            continue
        x, y, z = (float(v) for v in sup["position_mm"])
        inner = abs(y) - float(sup.get("width_mm") or 0.0) / 2.0 + CALIPER_INSET_MM
        lay.put(Item(f"caliper_{sup['id']}", f"{_corner(sup)}のブレーキのキャリパー", "brake_caliper", CALIPER_SIZE_MM,
                     CALIPER_KG), x=x - DISC_RADIUS_MM, y=math.copysign(inner, y), z=z, carried_by=None)
        _take(lay, f"{suspension_prefix}{sup['id']}", CALIPER_KG)
        out[f"caliper_{sup['id']}"] = CALIPER_KG
    return out


def belt_of(seat_id: str) -> str:
    return f"seat_belt_{seat_id.split('_', 1)[-1]}"


def place_belts(lay: Layout, seats) -> Record:
    """A seat belt behind every seat back at its outboard edge; returns {part id: mass}."""
    out = {}
    for sid in seats:
        lo, hi = lay.box(sid)
        y = float(lay.part(sid)["box"]["center_mm"][1])
        edge = float(hi[1]) - BELT_SIZE_MM[1] / 2.0 if y > 0 else float(lo[1]) + BELT_SIZE_MM[1] / 2.0
        name = "助手席" if y > 0 else "運転席"
        bid = belt_of(sid)
        lay.put(Item(bid, f"{name}のシートベルト (取付金具と巻き取り)", "seat_belt", BELT_SIZE_MM, BELT_KG),
                x=front_at(float(lo[0])), y=edge, z=bottom_at(float(hi[2]) - BELT_SIZE_MM[2] / 2.0))
        _take(lay, sid, BELT_KG)
        out[bid] = BELT_KG
    return out


def place_main_switch(lay: Layout, beside: str) -> Record:
    """The battery's main switch on the +y side of `beside`, at its height and x; returns {id: mass}."""
    lo, hi = lay.box(beside)
    c = lay.part(beside)["box"]["center_mm"]
    lay.put(Item("main_switch", "電池の主スイッチ (遮断)", "switch", SWITCH_SIZE_MM, SWITCH_KG),
            x=float(c[0]), y=right_at(float(hi[1])), z=float(c[2]))
    _take(lay, beside, SWITCH_KG)
    return {"main_switch": SWITCH_KG}


def place_lamps(lay: Layout, front: str, rear_x: float, rear_z: float, rear_body: str) -> Record:
    """Front lamps on the front face of `front` (each side, LAMP_INSET_MM in from its side faces,
    stacked down from the middle of its height) and rear lamps behind rear_x (the frame's rear end)
    at the height rear_z, each side LAMP_INSET_MM in from the side faces of `rear_body`, stacked up;
    returns {part id: mass}."""
    out = {}
    lo, hi = lay.box(front)
    zc = float(lay.part(front)["box"]["center_mm"][2])
    for side, sign in (("L", 1.0), ("R", -1.0)):
        top = zc + LAMPS_FRONT[0][3][2] / 2.0
        for kind, label, m, size in LAMPS_FRONT:
            pid = f"{kind}_F{side}"
            y = sign * (float(hi[1]) - LAMP_INSET_MM - size[1] / 2.0)
            lay.put(Item(pid, f"{'左' if sign > 0 else '右'}の{label}", kind, size, m),
                    x=rear_at(float(hi[0])), y=y, z=top - size[2] / 2.0)
            top -= size[2] + LAMP_GAP_MM
            out[pid] = m
    _take(lay, front, sum(v for k, v in out.items() if k.endswith(("_FL", "_FR"))))
    rlo, rhi = lay.box(rear_body)
    rear = {}
    for side, sign in (("L", 1.0), ("R", -1.0)):
        bottom = rear_z
        for kind, label, m, size in LAMPS_REAR:
            pid = f"{kind}_R{side}"
            y = sign * (float(rhi[1]) - LAMP_INSET_MM - size[1] / 2.0)
            lay.put(Item(pid, f"{'左' if sign > 0 else '右'}の{label}", kind, size, m),
                    x=front_at(rear_x), y=y, z=bottom + size[2] / 2.0)
            bottom += size[2] + LAMP_GAP_MM
            rear[pid] = m
    _take(lay, rear_body, sum(rear.values()))
    return {**out, **rear}


def add_rollbar_part(design: Record) -> Record | None:
    """The roll bar as a part of 0 kg (kind rollbar; its members' `part` set to it) added to the design:
    its box is the top beam's (rollbar_top), carried_by every node of the roll bar's members. None
    when the design has no roll bar members."""
    from hiraku.solvers.k4_geometry.obb import from_member

    mine = [m for m in design.get("members", []) if m["id"].startswith("rollbar_")]
    if not mine:
        return None
    for m in mine:
        m["part"] = ROLLBAR_PART
    nodes = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
    top = next(m for m in mine if m["id"] == "rollbar_top")
    dims = design["sections"][top["section"]]["dims_mm"]
    w = float(dims.get("d", dims.get("b", 0.0)))
    pts = from_member(nodes[top["node_a"]], nodes[top["node_b"]], w, float(dims.get("d", dims.get("h", w)))).corners()
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    part = {"id": ROLLBAR_PART, "name": "ロールバー (横転のときに乗る人を守る枠。管は骨組みの部材)", "kind": "rollbar",
            "box": {"center_mm": [float(v) for v in (lo + hi) / 2.0], "size_mm": [float(v) for v in hi - lo]},
            "mass_kg": 0.0, "carried_by": sorted({n for m in mine for n in (m["node_a"], m["node_b"])})}
    # before the joint pieces (gussets, through pieces), so that the parts of a design without them come first
    at = next((i for i, p in enumerate(design["parts"]) if p["kind"] in ("gusset", "frame")), len(design["parts"]))
    design["parts"].insert(at, part)
    return part


def rollbar_feet(design: Record) -> list[str]:
    """The nodes the roll bar stands on: where its feet (outriggers) start, else where its posts start."""
    mine = {m["id"]: m for m in design.get("members", []) if m["id"].startswith("rollbar_")}
    feet = [m["node_a"] for k, m in sorted(mine.items()) if k.startswith("rollbar_foot_")]
    return feet or [m["node_a"] for k, m in sorted(mine.items()) if k.startswith("rollbar_post_")]

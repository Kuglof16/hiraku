"""How each part is held, read off the design.

- a part with carried_by: bolted to those frame nodes
- a suspension part: bolted to its support's frame node (the mount on the frame)
- a wheel part: bolted to the suspension part of the same support (the hub)
- frame members: welded to each other where they share a node
- occupants and cargo: not fastened (they ride in)

Each fastening: {"to": "node LS1" | "part susp_FL" | "member rail_L_FA", "kind": "bolt" | "weld" | "rest",
"at_mm": [x, y, z]}.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from .model import Body

Record = dict[str, Any]
NOT_FASTENED = ("occupant", "cargo")
# A corner built of its arms (designgen kit/arms.py): its parts are held as their connects_to say (the legs in
# the frame's brackets, the ball joints on the legs, the knuckle on the ball joints, the wheel on the knuckle)
ARM_KINDS = ("link", "ball_joint")
FASTENING_VIAS = ("bolt", "weld", "bushing", "pin", "ball_joint")


def _linked(design: Record, p: Record, bs: dict[str, Body], nodes: dict) -> list[Record]:
    """A corner part's fastenings from its connects_to: to the parts and nodes it names (a support id, the
    ground, is left out), at the link's at_mm or else the other's centre."""
    out = []
    for c in p.get("connects_to") or []:
        to = c.get("to")
        if c.get("via") not in FASTENING_VIAS:  # friction, spline, rest, wire: not what holds the part
            continue
        if f"part:{to}" in bs:
            at = c.get("at_mm") or [float(v) for v in bs[f"part:{to}"].obb.center]
            out.append({"to": f"part {to}", "kind": c.get("via", "bolt"), "at_mm": [float(v) for v in at]})
        elif to in nodes:
            out.append({"to": f"node {to}", "kind": c.get("via", "bolt"), "at_mm": c.get("at_mm") or nodes[to]})
    if p["kind"] == "wheel":  # onto the hub after the brake on it: the wheel goes on last
        sid = bs[f"part:{p['id']}"].support
        for k, b in sorted(bs.items()):
            if b.support == sid and b.kind == "brake_caliper" and f"part {k.split(':', 1)[1]}" not in {f['to'] for f in out}:
                out.append({"to": f"part {k.split(':', 1)[1]}", "kind": "rest", "at_mm": [float(v) for v in b.obb.center]})
    return out


def _support_of(design: Record, sid: str) -> Record | None:
    return next((s for s in design.get("supports", []) if s["id"] == sid), None)


def part_fastenings(design: Record, part_id: str, bs: dict[str, Body]) -> list[Record]:
    """The fastenings of one part (empty for occupants, cargo and anything not held)."""
    nodes = {n["id"]: [float(v) for v in n["xyz_mm"]] for n in design.get("nodes", [])}
    p = next(x for x in design.get("parts", []) if x["id"] == part_id)
    if p["kind"] in NOT_FASTENED:
        return []
    if p.get("carried_by"):
        return [{"to": f"node {n}", "kind": "bolt", "at_mm": nodes[n]} for n in p["carried_by"] if n in nodes]
    body = bs.get(f"part:{part_id}")
    sid = body.support if body is not None else None
    s = _support_of(design, sid) if sid else None
    if s is None:
        return []
    if any(b.support == sid and b.kind in ARM_KINDS for b in bs.values()):
        return _linked(design, p, bs, nodes)
    if p["kind"] == "suspension":
        fn = s.get("frame_node")
        return [{"to": f"node {fn}", "kind": "bolt", "at_mm": nodes[fn]}] if fn in nodes else []
    if p["kind"] == "wheel":  # onto the hub of the arms of the same support
        arms = [k.split(":", 1)[1] for k, b in bs.items() if b.support == sid and b.kind == "suspension"]
        return [{"to": f"part {a}", "kind": "bolt", "at_mm": [float(v) for v in s["position_mm"]]} for a in arms]
    return [{"to": f"node {s['frame_node']}", "kind": "bolt", "at_mm": nodes[s["frame_node"]]}] if s.get("frame_node") in nodes else []


def welds(design: Record) -> list[Record]:
    """Frame joints: every node shared by two or more members, with the members welded there."""
    by_node: dict[str, list[str]] = {}
    for m in design.get("members", []):
        for k in ("node_a", "node_b"):
            by_node.setdefault(m[k], []).append(m["id"])
    nodes = {n["id"]: [float(v) for v in n["xyz_mm"]] for n in design.get("nodes", [])}
    return [{"node": n, "members": sorted(ms), "kind": "weld", "at_mm": nodes[n]}
            for n, ms in sorted(by_node.items()) if len(ms) > 1 and n in nodes]


def text_of(fastenings: list[Record], names: dict[str, str]) -> str:
    """The fastenings in Japanese, e.g. 'LS1・RS1 の節点にボルトで留める'."""
    if not fastenings:
        return "載せる"
    nodes = [f["to"].split(" ", 1)[1] for f in fastenings if f["to"].startswith("node ")]
    parts = [names.get(f["to"].split(" ", 1)[1], f["to"].split(" ", 1)[1]) for f in fastenings if f["to"].startswith("part ")]
    where = []
    if nodes:
        where.append("・".join(nodes) + " の節点")
    if parts:
        where.append("・".join(parts))
    kinds = {f["kind"] for f in fastenings}
    how = "ボルトで留める" if kinds == {"bolt"} else ("溶接する" if kinds == {"weld"} else "留める")
    return "と".join(where) + "に" + how


def centre(fastenings: list[Record]) -> np.ndarray | None:
    pts = [f["at_mm"] for f in fastenings if "at_mm" in f]
    return np.mean(np.asarray(pts, float), axis=0) if pts else None

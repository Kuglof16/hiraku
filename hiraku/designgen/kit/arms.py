"""The suspension's arms, dampers and ball joints as parts, from a support's K2 hard points.

    _kit(arms.place, lay, rho)            # after steering.place_corners and safety.place_brakes
    arms.pivot_brackets(design, rho)      # once the design has the frame's nodes, before systems.write

place(), for every support with suspension.hardpoints_mm (a double wishbone), in place of the
corner's one suspension box (suspension_prefix + id):
- the lower arm, an A of two legs (kind "link"): lca_<id>_f from lca_front and lca_<id>_r from
  lca_rear, each to lca_outer, and a cross tube lca_<id>_x between the legs through damper_lower
  (the damper's lower mount);
- the upper arm, an A of two legs (kind "link"): uca_<id>_f from uca_front and uca_<id>_r from
  uca_rear, each to uca_outer;
- the damper with its coil spring (kind "damper"), damper_<id>, from damper_lower to damper_upper;
- the ball joints (kind "ball_joint"), bj_lower_<id> at lca_outer and bj_upper_<id> at uca_outer.
The arms are round tubes ARM_D_MM x ARM_T_MM (kit/links.put_link boxes) of the radius the frame's
mounts keep clear of the members (kit/mounts.ARM_RADIUS_MM), the damper DAMPER_RADIUS_MM
(kit/mounts.DAMPER_RADIUS_MM). The suspension box's mass (what is left after the knuckle, toe link
and caliper came out of it) is shared out: the tubes their own mass, the ball joints BALL_JOINT_KG,
the damper the rest; so a corner's mass is what it was, and K2 takes half of each of these kinds
as unsprung as it did of the box (k2_vehicle.model.HALF_UNSPRUNG_KINDS). The hard points, springs
and dampers that K2 runs are not changed.

pivot_brackets(): a clevis bracket (two plates and a pin, welded on) at each lower arm pivot and
damper top that sits on a frame node with no bracket round it (the front lower arm's pivots at
the ends of the lower legs, the dampers' tops at the ends of the tower arms), sized as
kit/mounts' brackets. The parts' links (bushings at the brackets, the knuckle on the ball joints)
are made by kit/systems.py.

Sizes are assumptions (not checked against a source): the arms of 25.4 x 2.0 mm round tube, a
coil-over of 50 mm across, a ball joint of 0.2 kg.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from hiraku.designgen.kit import mounts
from hiraku.designgen.kit.links import put_link
from hiraku.designgen.kit.package import Item, Layout

Record = dict[str, Any]

ARM_D_MM = 2.0 * mounts.ARM_RADIUS_MM  # arm legs, round tube outside diameter (25.4; assumption, not checked)
ARM_T_MM = 2.0  # its wall (assumption, not checked)
DAMPER_RADIUS_MM = mounts.DAMPER_RADIUS_MM  # the coil spring's outside radius (25; assumption, not checked)
BALL_JOINT_MM = 40.0  # a ball joint's housing, cube side (assumption, not checked)
BALL_JOINT_KG = 0.2  # one ball joint (assumption, not checked)
KINDS = ("link", "damper", "ball_joint")  # the kinds of the parts placed here
HARDPOINTS = ("lca_front", "lca_rear", "lca_outer", "uca_front", "uca_rear", "uca_outer", "damper_lower",
              "damper_upper")
NODE_TOL_MM = 1.0  # a hard point on a frame node


def tube_mass(length_mm: float, d_mm: float, t_mm: float, rho: float) -> float:
    """kg of a round tube; rho in kg/m^3."""
    return rho * math.pi * (d_mm - t_mm) * t_mm * length_mm * 1e-9


def part_ids(sid: str) -> dict[str, str]:
    """The parts place() makes for support sid, by role."""
    return {"lca_f": f"lca_{sid}_f", "lca_r": f"lca_{sid}_r", "lca_x": f"lca_{sid}_x", "uca_f": f"uca_{sid}_f",
            "uca_r": f"uca_{sid}_r", "damper": f"damper_{sid}", "bj_lower": f"bj_lower_{sid}",
            "bj_upper": f"bj_upper_{sid}"}


def _corner(s: Record) -> str:
    return ("前" if s["position_mm"][0] >= 0 else "後") + ("左" if s["position_mm"][1] > 0 else "右")


def _on_leg(a: np.ndarray, b: np.ndarray, y: float) -> np.ndarray:
    """The point of the leg a-b at side position y (clamped to the leg)."""
    t = 0.5 if abs(b[1] - a[1]) < 1e-9 else float(np.clip((y - a[1]) / (b[1] - a[1]), 0.0, 1.0))
    return a + t * (b - a)


def cross_ends(hp: Record) -> tuple[np.ndarray, np.ndarray]:
    """The lower arm's cross tube ends, on its front and rear legs at the damper's lower mount's side
    position (hard points in mm)."""
    p = {k: np.asarray(hp[k], float) for k in ("lca_front", "lca_rear", "lca_outer", "damper_lower")}
    return (_on_leg(p["lca_front"], p["lca_outer"], p["damper_lower"][1]),
            _on_leg(p["lca_rear"], p["lca_outer"], p["damper_lower"][1]))


def place(lay: Layout, rho: float, suspension_prefix: str = "susp_") -> Record:
    """Replace every corner's suspension box by its arms, damper and ball joints. Returns {part id: kg}.
    Raises ValueError when a hard point is missing or the box is lighter than the tubes and ball joints."""
    out = {}
    for s in lay.design.get("supports", []):
        hp = (s.get("suspension") or {}).get("hardpoints_mm")
        if not hp:
            continue
        missing = [k for k in HARDPOINTS if k not in hp]
        if missing:
            raise ValueError(f"support {s['id']}: hardpoints_mm has no {', '.join(missing)}")
        p = {k: np.asarray(hp[k], float) for k in HARDPOINTS}
        sid, corner = s["id"], _corner(s)
        ids = part_ids(sid)
        box = lay.part(f"{suspension_prefix}{sid}")
        r = ARM_D_MM / 2.0
        mine = {}
        cross = cross_ends(p)
        for role, a, b, label in (("lca_f", p["lca_front"], p["lca_outer"], "下の腕 (前の脚)"),
                                  ("lca_r", p["lca_rear"], p["lca_outer"], "下の腕 (後の脚)"),
                                  ("lca_x", cross[0], cross[1], "下の腕の横の管 (ダンパーの下の取付)"),
                                  ("uca_f", p["uca_front"], p["uca_outer"], "上の腕 (前の脚)"),
                                  ("uca_r", p["uca_rear"], p["uca_outer"], "上の腕 (後の脚)")):
            m = tube_mass(float(np.linalg.norm(b - a)), ARM_D_MM, ARM_T_MM, rho)
            put_link(lay, Item(ids[role], f"{corner}の{label}", "link", (0, 0, 0), m), a, b, r, carried_by=None)
            mine[ids[role]] = m
        for role, at, label in (("bj_lower", "lca_outer", "下"), ("bj_upper", "uca_outer", "上")):
            lay.put(Item(ids[role], f"{corner}の{label}のボールジョイント", "ball_joint", (BALL_JOINT_MM,) * 3,
                         BALL_JOINT_KG), *p[at].tolist(), carried_by=None)
            mine[ids[role]] = BALL_JOINT_KG
        damper_kg = float(box["mass_kg"]) - sum(mine.values())
        if damper_kg <= 0.0:
            raise ValueError(f"{box['id']} ({box['mass_kg']:g} kg) is lighter than the arms and ball joints "
                             f"({sum(mine.values()):.3f} kg) that replace it")
        put_link(lay, Item(ids["damper"], f"{corner}のダンパー (コイルばねつき)", "damper", (0, 0, 0), damper_kg),
                 p["damper_lower"], p["damper_upper"], DAMPER_RADIUS_MM, carried_by=None)
        mine[ids["damper"]] = damper_kg
        lay.parts.remove(box)
        out.update(mine)
    return out


def _member_away(design: Record, node: str, nodes: dict) -> np.ndarray | None:
    """The direction from a node along the (first) member that ends there, or None."""
    for m in design.get("members", []):
        if node in (m["node_a"], m["node_b"]):
            other = m["node_b"] if m["node_a"] == node else m["node_a"]
            return nodes[other] - nodes[node]
    return None


REACH_STEP_MM = 0.5  # the damper bracket's inboard reach is cut back by this much at a time


def _clear_of_members(design: Record, pid: str, node: str, lo: np.ndarray, hi: np.ndarray, sy: float):
    """The damper-top bracket's box with its inboard reach cut back until K4 allows it against every frame
    member (attach.fits): on a short tower arm the box reached into the tower's post (track 1200 mm). The
    reach keeps at least NODE_TOL_MM past the eye; the box is returned as it is when nothing is in the way."""
    from hiraku.solvers.k4_geometry import model as k4

    from . import attach

    bs = {k: v for k, v in k4.bodies(design).items() if v.kind == "member"}
    lo, hi = np.array(lo, float), np.array(hi, float)
    eye = (hi[1] if sy > 0 else lo[1]) - sy * NODE_TOL_MM  # the hard point's y
    reach = abs(eye - (lo[1] if sy > 0 else hi[1]))
    while True:
        if sy > 0:
            lo[1] = eye - reach
        else:
            hi[1] = eye + reach
        if reach <= NODE_TOL_MM or attach.fits(design, attach._body(pid, lo, hi, [node]), set(), bs) is None:
            return lo, hi
        reach = max(NODE_TOL_MM, reach - REACH_STEP_MM)


def pivot_brackets(design: Record, rho: float) -> list[str]:
    """A clevis bracket at each lower arm pivot and damper top that sits on a frame node with no bracket
    round it, added to design["parts"]; returns the new brackets' ids."""
    parts = design["parts"]
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])}
    brackets = [p for p in parts if p["kind"] == "bracket"]

    def held(q):
        return any(np.all(np.abs(q - np.asarray(b["box"]["center_mm"], float))
                          <= np.asarray(b["box"]["size_mm"], float) / 2.0 + NODE_TOL_MM) for b in brackets)

    out = []
    for s in design.get("supports", []):
        hp = (s.get("suspension") or {}).get("hardpoints_mm")
        if not hp:
            continue
        sid, corner = s["id"], _corner(s)
        for key, pid, label in (("lca_front", f"bracket_lca_{sid}1", "下の腕の付け根の金具 (前)"),
                                ("lca_rear", f"bracket_lca_{sid}2", "下の腕の付け根の金具 (後)"),
                                ("damper_upper", f"bracket_damper_{sid}", "ダンパーの上の金具")):
            q = np.asarray(hp[key], float)
            at = [n for n, x in nodes.items() if float(np.linalg.norm(x - q)) <= NODE_TOL_MM]
            if not at or held(q) or any(p["id"] == pid for p in parts):
                continue
            pad = np.array([mounts.BRACKET_HALF_X_MM, mounts.BRACKET_PAD_MM, mounts.BRACKET_PAD_MM])
            lo, hi = q - pad, q + pad
            leg = _member_away(design, at[0], nodes)
            if key == "damper_upper":  # under the tower arm's tip, reaching inboard: nothing past the eye toward the wheel
                sy = 1.0 if s["position_mm"][1] > 0 else -1.0
                lo[1], hi[1] = sorted((float(q[1]) - sy * 2.0 * mounts.BRACKET_PAD_MM, float(q[1]) + sy * NODE_TOL_MM))
                lo[2], hi[2] = q[2] - mounts.BRACKET_PAD_MM, q[2] + NODE_TOL_MM  # clear of the knuckle at full bump
            elif leg is not None:
                # on the leg's end, reaching away from the leg along x and z: the leg stops at its faces
                for i in (0, 2):
                    reach = 2.0 * pad[i]
                    lo[i], hi[i] = ((q[i] - reach, q[i] + NODE_TOL_MM) if leg[i] > 0 else (q[i] - NODE_TOL_MM, q[i] + reach))
            if key == "damper_upper":
                lo, hi = _clear_of_members(design, pid, at[0], lo, hi, sy)
            b = mounts._bracket(pid, f"{corner}の{label} (クレビス、溶接)", lo, hi, at[:1], rho)
            # before the joints' reinforcement (gussets, through pieces), which a design adds last
            at_end = next((i for i, x in enumerate(parts) if x["kind"] == "gusset" or x["id"].startswith("through_")),
                          len(parts))
            parts.insert(at_end, b)
            brackets.append(b)
            out.append(pid)
    return out


__all__ = ["ARM_D_MM", "ARM_T_MM", "BALL_JOINT_KG", "BALL_JOINT_MM", "DAMPER_RADIUS_MM", "KINDS", "part_ids",
           "pivot_brackets", "place", "tube_mass"]

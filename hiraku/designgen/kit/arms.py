"""The suspension's arms, dampers and ball joints as parts, from a support's K2 hard points.

    _kit(arms.place, lay, rho)   # before steering.place_corners

For every support with suspension.hardpoints_mm (a double wishbone):
- the upper arm's two legs (kind "link"), uca_<id>_f from uca_front and uca_<id>_r from uca_rear,
  each to uca_outer: round tubes UPPER_ARM_D_MM x UPPER_ARM_T_MM (kit/links.put_link boxes);
- the damper with its coil spring (kind "damper"), damper_<id>, from damper_lower to damper_upper,
  a cylinder of DAMPER_RADIUS_MM and DAMPER_KG;
- the ball joints (kind "ball_joint"), bj_lower_<id> at lca_outer and bj_upper_<id> at uca_outer,
  cubes of BALL_JOINT_MM and BALL_JOINT_KG.
The radii are the ones the frame's mounts keep clear of the members (kit/mounts.ARM_RADIUS_MM,
DAMPER_RADIUS_MM). The corner's suspension part (suspension_prefix + id, the box around the lower
arm) stays as the lower arm and gives up these parts' mass, so a corner's suspension mass is what it
was; K2 takes half of each of these kinds as unsprung, as of the suspension part (k2_vehicle.model.
HALF_UNSPRUNG_KINDS). The hard points, springs and dampers that K2 runs are not changed. The parts'
links (what holds each mount, the knuckle on the ball joints) are made by kit/systems.py.

Sizes are assumptions (not checked against a source): the arms of 25.4 x 2.0 mm round tube, a
coil-over of 50 mm across and 1.2 kg, a ball joint of 0.2 kg.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from hiraku.designgen.kit import mounts
from hiraku.designgen.kit.links import put_link
from hiraku.designgen.kit.package import Item, Layout

Record = dict[str, Any]

UPPER_ARM_D_MM = 2.0 * mounts.ARM_RADIUS_MM  # upper arm legs, round tube outside diameter (25.4; assumption, not checked)
UPPER_ARM_T_MM = 2.0  # its wall (assumption, not checked)
DAMPER_RADIUS_MM = mounts.DAMPER_RADIUS_MM  # the coil spring's outside radius (25; assumption, not checked)
DAMPER_KG = 1.2  # damper with its coil spring and end eyes (assumption, not checked)
BALL_JOINT_MM = 40.0  # a ball joint's housing, cube side (assumption, not checked)
BALL_JOINT_KG = 0.2  # one ball joint (assumption, not checked)
KINDS = ("link", "damper", "ball_joint")  # the kinds of the parts placed here
HARDPOINTS = ("uca_front", "uca_rear", "uca_outer", "lca_outer", "damper_lower", "damper_upper")


def tube_mass(length_mm: float, d_mm: float, t_mm: float, rho: float) -> float:
    """kg of a round tube; rho in kg/m^3."""
    return rho * math.pi * (d_mm - t_mm) * t_mm * length_mm * 1e-9


def part_ids(sid: str) -> dict[str, str]:
    """The parts this kit places for support sid, by role."""
    return {"uca_f": f"uca_{sid}_f", "uca_r": f"uca_{sid}_r", "damper": f"damper_{sid}",
            "bj_lower": f"bj_lower_{sid}", "bj_upper": f"bj_upper_{sid}"}


def place(lay: Layout, rho: float, suspension_prefix: str = "susp_") -> Record:
    """Place the arms, dampers and ball joints of every support with hard points; their mass comes out
    of the corner's suspension part. Returns {part id: mass kg}. Raises ValueError when a hard point is
    missing or the suspension part is lighter than what is taken out of it."""
    out = {}
    for s in lay.design.get("supports", []):
        hp = (s.get("suspension") or {}).get("hardpoints_mm")
        if not hp:
            continue
        missing = [k for k in HARDPOINTS if k not in hp]
        if missing:
            raise ValueError(f"support {s['id']}: hardpoints_mm has no {', '.join(missing)}")
        p = {k: np.asarray(hp[k], float) for k in HARDPOINTS}
        sid = s["id"]
        ids = part_ids(sid)
        corner = ("前" if s["position_mm"][0] >= 0 else "後") + ("左" if s["position_mm"][1] > 0 else "右")
        susp = lay.part(f"{suspension_prefix}{sid}")
        r_arm = UPPER_ARM_D_MM / 2.0
        for role, inner, label in (("uca_f", "uca_front", "前"), ("uca_r", "uca_rear", "後")):
            L = float(np.linalg.norm(p["uca_outer"] - p[inner]))
            m = tube_mass(L, UPPER_ARM_D_MM, UPPER_ARM_T_MM, rho)
            put_link(lay, Item(ids[role], f"{corner}の上の腕 ({label}の脚)", "link", (0, 0, 0), m),
                     p[inner], p["uca_outer"], r_arm, carried_by=None)
            out[ids[role]] = m
        put_link(lay, Item(ids["damper"], f"{corner}のダンパー (コイルばねつき)", "damper", (0, 0, 0), DAMPER_KG),
                 p["damper_lower"], p["damper_upper"], DAMPER_RADIUS_MM, carried_by=None)
        out[ids["damper"]] = DAMPER_KG
        for role, at, label in (("bj_lower", "lca_outer", "下"), ("bj_upper", "uca_outer", "上")):
            lay.put(Item(ids[role], f"{corner}の{label}のボールジョイント", "ball_joint", (BALL_JOINT_MM,) * 3,
                         BALL_JOINT_KG), *p[at].tolist(), carried_by=None)
            out[ids[role]] = BALL_JOINT_KG
        taken = sum(out[i] for i in ids.values())
        if float(susp["mass_kg"]) <= taken:
            raise ValueError(f"{susp['id']} ({susp['mass_kg']:g} kg) is lighter than the arms, damper and ball joints "
                             f"taken out of it ({taken:.3f} kg)")
        susp["mass_kg"] = float(susp["mass_kg"]) - taken
        susp["name"] = f"{corner}の下の腕 (A 形。箱は腕のまわり)"
    return out


__all__ = ["BALL_JOINT_KG", "BALL_JOINT_MM", "DAMPER_KG", "DAMPER_RADIUS_MM", "UPPER_ARM_D_MM", "UPPER_ARM_T_MM",
           "part_ids", "place", "tube_mass"]

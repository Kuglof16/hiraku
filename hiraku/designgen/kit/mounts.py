"""Where the double wishbones meet the frame: the hard points moved clear of the rails, the towers
that carry the dampers' tops, the legs and cross members that carry the lower arms' pivots, and
the brackets (parts of kind bracket) for the points no member reaches.

    hp, clear = mounts.fit_hardpoints(support, rail, travel_mm)        # before the parts are placed
    mounts.place(frame, lay, supports, sections, rho, post_dx)         # after: members and brackets

The hard points (fit_hardpoints) come from K2's builtin double wishbone (hiraku.solvers.k2_vehicle.
model.hardpoints_for): the whole upper arm is raised (upper_joint_z_mm) and the damper's top moved
outboard (damper_upper_y_mm) in small steps until, through the whole wheel travel (half the
suspension travel each way, as K4 sweeps the wheels), each upper arm's line is ARM_RADIUS_MM +
GAP_MM from the rail and the damper's axis DAMPER_RADIUS_MM + GAP_MM; then the damper's top is raised
above the upper ball joint at full bump (K2 puts it 0.9 wheel radius over the wheel centre, which the
raised arm would reach) so that the arm passes under the tower. The arm and damper sizes and the gap
are K2's assumptions (no source). The arms move as K2's bench solves them
(kinematics.solve_at_travel); the damper's lower end turns with the lower arm.

What place() adds at each corner (a support on the builtin K&C table), every node on a member:
- the rail split at the tower's posts (post_dx ahead of and behind the wheel centre) and below the
  upper arm's two pivots;
- the tower: two posts straight up from the rail to the damper's top height, a top beam along x
  between their tops through a node above the damper, and a short arm across from there out to
  the damper's top (hard point damper_upper is that arm's end node: mount_carriers finds it);
  where the rail must stay whole (`whole`) on one side the top beam reaches over from the other
  post, and with both sides whole the tower is one post on the wheel's station node;
- the lower arm's pivots (lca_front, lca_rear) as nodes, each at the end of a leg down from the
  post's node on the rail; across the car a lower cross member joins the left and right pivots
  where nothing is in the way (the parts given in `avoid`, e.g. the motor);
- brackets (kind bracket, carried_by the rail node below): one at each upper arm pivot, standing
  on the rail; one at the inner end of each tie rod or toe link (tie_inner: the rack's clamp at the
  front), hanging from the rail node below the nearer upper arm pivot (the leg down to the lower
  arm's pivot arrives beside the pivot, where a clamp on the lower cross member would stand).
Bracket masses: two plates (BRACKET_T_MM thick, steel of the frame's density) the size of the
bracket's box face across the car (assumption, the plates' shape not drawn). The suspension brackets carry
part.joint (joint_sizes: the pin, one per bracket, the plates and the weld; the design variables mount_bolt_d,
mount_plate_t, mount_weld_leg) and part.declared (joint_declared: the pin hole and its edge distance); their
plates are joint["plate_t_mm"] thick (the default BRACKET_T_MM, so the default design's mass is as before).
"""

from __future__ import annotations

import functools
import json
import math
from typing import Any

import numpy as np

from hiraku.designgen.kit.package import Item, Layout

Record = dict[str, Any]

# K2's assumptions (no source): the upper arm a tube of radius 12.7 mm, the damper's body radius 25 mm,
# each kept 10 mm off the frame
ARM_RADIUS_MM = 12.7
DAMPER_RADIUS_MM = 25.0
GAP_MM = 10.0
TRAVEL_STEPS = 9  # positions through the travel the clearances are checked at
STEP_LIMIT = 40  # iterations of the hard point search
UPPER_PIVOT_DROP_MM = 15.0  # K2: the upper arm's inner pivots 15 mm below its ball joint
# brackets (assumptions)
BRACKET_T_MM = 3.2  # plate thickness, two plates per bracket
BRACKET_HALF_X_MM = 25.0  # the bracket's half length along x around its point
BRACKET_PAD_MM = 15.0  # the bracket's box this far beyond its point
SINGLE_POST_LEAN_X_MM = 2.0  # a tower of one post leans back this much at its top (assumption: not square to the rail;
# any lean will do; more brings it to the upper arm's legs). It does not lean across the rail: its foot is the station
# node on the rail's centre line, so a lean across would move its footprint on the rail's top face off centre
RACK_RADIUS_MM = 25.0  # the rack housing (kit/steering.RACK_RADIUS_MM): the clamp reaches over it
# the suspension brackets' joint (part.joint; R9): defaults of the design variables mount_plate_t (BRACKET_T_MM),
# mount_bolt_d and mount_weld_leg (ladder_4w.VARIABLES)
BOLT_D_MM = 10.0  # the pin's diameter: HIRAKU's assumption (not sized; R9 sizes it)
WELD_LEG_MM = 3.0  # the fillet weld's leg: HIRAKU's assumption (not sized; R9 sizes it)
PINS_PER_BRACKET = 1  # one pin through the clevis (the bracket's shape, not a variable)
HOLE_CLEARANCE_MM = 2.0  # d0 = d + 2 mm: EN 1090-2 Table 11 normal clearance (not checked)
HOLE_EDGE_FACTOR = 1.2  # the hole's edge distance 1.2 d0: the minimum R9 assumes (EN 1993-1-8 Table 3.3, not checked)


class MountError(ValueError):
    pass


# ---------------------------------------------------------------- hard points
def _rail_box(rail: Record) -> tuple[float, float, float, float]:
    """(y0, y1, z0, z1) of the left rail's cross-section: rail = {"y", "z", "b", "h"} (centre line and tube)."""
    return rail["y"] - rail["b"] / 2.0, rail["y"] + rail["b"] / 2.0, rail["z"] - rail["h"] / 2.0, rail["z"] + rail["h"] / 2.0


def _seg_rect(a, b, rect) -> float:
    """Shortest distance (mm) in the y-z plane from the segment a-b to the rectangle (negative: inside by)."""
    y0, y1, z0, z1 = rect
    best = math.inf
    for t in np.linspace(0.0, 1.0, 121):
        p = np.asarray(a, float) + t * (np.asarray(b, float) - np.asarray(a, float))
        dy = max(y0 - p[1], 0.0, p[1] - y1)
        dz = max(z0 - p[2], 0.0, p[2] - z1)
        d = math.hypot(dy, dz) if (dy or dz) else -min(p[1] - y0, y1 - p[1], p[2] - z0, z1 - p[2])
        best = min(best, d)
    return best


def poses(hp_left: Record, radius_mm: float, travel_mm: float, steps: int = TRAVEL_STEPS) -> list[Record]:
    """The moving points of a left corner through the travel (half each way), mm: for each position
    {"travel_mm", "ubj", "lbj", "damper_lower"} (K2's bench, kinematics.solve_at_travel)."""
    from hiraku.solvers.k2_vehicle import kinematics

    H = kinematics.Hardpoints.from_json(dict(hp_left, axle=[0.0, 1.0, 0.0], radius_mm=radius_mm))
    a, b = H.lca_front, H.lca_rear
    e = (b - a) / float(np.linalg.norm(b - a))

    def perp(p):
        v = p - a
        return v - float(v @ e) * e

    out = []
    for t in np.linspace(-travel_mm / 2.0, travel_mm / 2.0, steps):
        sol = kinematics.solve_at_travel(H, float(t) / 1000.0)
        u0, u1 = perp(H.lca_outer), perp(sol["lbj"])
        ang = math.atan2(float(np.cross(u0, u1) @ e), float(u0 @ u1))
        dl = kinematics.rotate_about(H.damper_lower, a, e, ang)
        out.append({"travel_mm": float(t), "ubj": sol["ubj"] * 1000.0, "lbj": sol["lbj"] * 1000.0,
                    "damper_lower": np.asarray(dl, float) * 1000.0})
    return out


def _left(hp: Record) -> Record:
    return {k: [float(v[0]), abs(float(v[1])), float(v[2])] for k, v in hp.items()}


def rail_clearances(hp: Record, radius_mm: float, travel_mm: float, rail: Record) -> Record:
    """Through the travel, the smallest distance (mm) from each upper arm's line (inner pivot to ball
    joint) and from the damper's axis to the rail's cross-section: {"upper_arm_mm", "damper_mm"} and
    where in the travel each is smallest."""
    left = _left(hp)
    rect = _rail_box(rail)
    arm, dmp = (math.inf, 0.0), (math.inf, 0.0)
    for p in poses(left, radius_mm, travel_mm):
        a = min(_seg_rect(left["uca_front"], p["ubj"], rect), _seg_rect(left["uca_rear"], p["ubj"], rect))
        d = _seg_rect(p["damper_lower"], left["damper_upper"], rect)
        arm = min(arm, (a, p["travel_mm"]))
        dmp = min(dmp, (d, p["travel_mm"]))
    return {"upper_arm_mm": arm[0], "upper_arm_at_travel_mm": arm[1], "damper_mm": dmp[0], "damper_at_travel_mm": dmp[1]}


def fit_hardpoints(support: Record, rail: Record, travel_mm: float, top_half_mm: float = 0.0) -> tuple[Record, Record]:
    """See _fit (the left wheel's layout, worked out once per size and mirrored for a right wheel)."""
    x, y, z = (float(v) for v in support["position_mm"])
    # the layout is the same for every wheel of this size and side distance: worked out at x = 0, then moved
    left = {"id": "wheel", "position_mm": [0.0, abs(y), z], "radius_mm": float(support["radius_mm"]),
            "suspension": support["suspension"]}
    key = (abs(y), z, float(support["radius_mm"]), json.dumps(support["suspension"], sort_keys=True),
           tuple(sorted(rail.items())), float(travel_mm), float(top_half_mm))
    hp, c = _fit_cached(key, json.dumps(left, sort_keys=True))
    hp = {k: [v[0] + x, (-v[1] if y < 0.0 else v[1]), v[2]] for k, v in json.loads(hp).items()}
    return hp, dict(c)


@functools.lru_cache(maxsize=64)
def _fit_cached(key: tuple, left_json: str) -> tuple[str, tuple]:
    rail = dict(key[4])
    hp, c = _fit(json.loads(left_json), rail, key[5], key[6])
    return json.dumps(hp), tuple(c.items())


def _fit(support: Record, rail: Record, travel_mm: float, top_half_mm: float = 0.0) -> tuple[Record, Record]:
    """Hard points for a support (design frame, mm, as suspension.hardpoints_mm) whose upper arms and
    damper clear the rail through the travel (see the module doc), and their clearances. The upper
    ball joint and the damper's top move in whole millimetres, up and outboard only. The damper's top
    is also raised (K2 puts it at a fixed height over the wheel centre) to clear the upper ball joint
    at full bump by ARM_RADIUS_MM + GAP_MM + top_half_mm (half the size of the member that ends there),
    so that the arm passes under the tower; the damper is not in K2's linkage, so the arms move as
    before. Raises MountError when K2's linkage does not close on the way."""
    from hiraku.solvers.k2_vehicle.model import DesignError, hardpoints_for

    need_arm, need_dmp = ARM_RADIUS_MM + GAP_MM, DAMPER_RADIUS_MM + GAP_MM
    uz = dy = None
    radius = float(support["radius_mm"])
    sy = -1.0 if float(support["position_mm"][1]) < 0.0 else 1.0
    for _ in range(STEP_LIMIT):
        try:
            hp = hardpoints_for(support, upper_joint_z_mm=uz, damper_upper_y_mm=dy)
        except DesignError as e:
            raise MountError(f"the suspension of {support['id']} cannot clear the rails ({e})") from None
        top = max(float(p["ubj"][2]) for p in poses(_left(hp), radius, travel_mm, steps=3))
        z_du = max(float(hp["damper_upper"][2]), math.ceil(top + ARM_RADIUS_MM + GAP_MM + top_half_mm))
        hp["damper_upper"] = [float(hp["damper_upper"][0]), sy * abs(float(hp["damper_upper"][1])), float(z_du)]
        c = rail_clearances(hp, radius, travel_mm, rail)
        short_arm, short_dmp = need_arm - c["upper_arm_mm"], need_dmp - c["damper_mm"]
        if short_arm <= 0.0 and short_dmp <= 0.0:
            c.update(upper_joint_z_mm=float(hp["uca_outer"][2]), damper_upper_y_mm=abs(float(hp["damper_upper"][1])),
                     need_upper_arm_mm=need_arm, need_damper_mm=need_dmp)
            return hp, c
        uz = float(hp["uca_outer"][2]) + (math.ceil(short_arm) if short_arm > 0.0 else 0.0)
        dy = abs(float(hp["damper_upper"][1])) + (math.ceil(short_dmp) if short_dmp > 0.0 else 0.0)
    raise MountError(f"the suspension of {support['id']} does not clear the rails in {STEP_LIMIT} steps")


# ---------------------------------------------------------------- frame pieces
def member_through(frame, xyz, along_x: bool = True) -> str | None:
    """The member whose segment passes through xyz strictly between its ends (a rail, when along_x)."""
    p = np.asarray(xyz, float)
    for m in frame.members:
        a, b = (np.asarray(frame.nodes[m[k]], float) for k in ("node_a", "node_b"))
        d = b - a
        if along_x and abs(d[0]) <= max(abs(d[1]), abs(d[2])):
            continue
        s = float((p - a) @ d / (d @ d))
        if 0.0 < s < 1.0 and float(np.linalg.norm(a + s * d - p)) <= frame.merge_mm:
            return m["id"]
    return None


def split_rail(frame, xyz, nid: str, station_nodes: set[str], corner: str) -> str:
    """A node on the rail at xyz (the existing node when one is there). Of the two pieces, the one that
    ends at a station node (station_nodes) other than `corner` (the wheel's own station) keeps the rail's
    id, so that the rail pieces between the stations keep their names away from the wheels; the other
    pieces are named by their end nodes, rail_<side>_<node>_<node>."""
    for k, q in frame.nodes.items():
        if float(np.linalg.norm(np.asarray(q) - np.asarray(xyz, float))) <= frame.merge_mm:
            return k
    mid = member_through(frame, xyz)
    if mid is None:
        raise MountError(f"no rail passes through {list(np.round(xyz, 1))} to put node {nid} on")
    m = next(x for x in frame.members if x["id"] == mid)
    side = mid.split("_")[1]
    a, b = m["node_a"], m["node_b"]
    by_ends = (f"rail_{side}_{a}_{nid}", f"rail_{side}_{nid}_{b}")
    far = {n for n in (a, b) if n in station_nodes and n != corner}
    if mid == f"rail_{side}_{a}_{b}" or not far:
        ids = by_ends
    elif a in far:
        ids = (mid, by_ends[1])
    else:
        ids = (by_ends[0], mid)
    return frame.split(mid, nid, xyz, ids)


def joint_sizes(plate_t_mm: float = BRACKET_T_MM, bolt_d_mm: float = BOLT_D_MM,
                weld_leg_mm: float = WELD_LEG_MM) -> Record:
    """A suspension bracket's part.joint (docs/contract.md 金具の継ぎ手): one pin per bracket (the clevis's shape)."""
    return {"bolt_d_mm": float(bolt_d_mm), "bolts": PINS_PER_BRACKET, "plate_t_mm": float(plate_t_mm),
            "weld_leg_mm": float(weld_leg_mm)}


def joint_declared(joint: Record) -> Record:
    """part.declared of a suspension bracket from its joint (shape only): the pin hole and its distance to the edge."""
    d0 = float(joint["bolt_d_mm"]) + HOLE_CLEARANCE_MM
    return {"hole_diameter_mm": {"value": d0, "unit": "mm",
                                 "basis": f"variable mount_bolt_d + {HOLE_CLEARANCE_MM:g} mm, the normal clearance of "
                                          "EN 1090-2 Table 11 (not checked)"},
            "hole_edge_mm": {"value": round(HOLE_EDGE_FACTOR * d0, 3), "unit": "mm",
                             "basis": f"assumption: {HOLE_EDGE_FACTOR:g} d0, the smallest end and edge distance R9's "
                                      "bearing check takes (EN 1993-1-8 Table 3.3, not checked)"}}


def _bracket(pid: str, name: str, lo, hi, carried_by: list[str], rho: float, joint: Record | None = None) -> Record:
    """A bracket part: two plates the size of its box's face across the car. With `joint` (a suspension mount's,
    joint_sizes) the plates are joint["plate_t_mm"] thick and the part carries joint and declared; else BRACKET_T_MM."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    size = hi - lo
    t = float(joint["plate_t_mm"]) if joint else BRACKET_T_MM
    mass = rho * 1e-9 * 2.0 * t * float(size[1]) * float(size[2])
    out = {"id": pid, "name": name, "kind": "bracket",
           "box": {"center_mm": [float(v) for v in (lo + hi) / 2.0], "size_mm": [float(v) for v in size]},
           "mass_kg": float(mass), "carried_by": list(carried_by)}
    if joint:
        out["joint"] = dict(joint)
        out["declared"] = joint_declared(joint)
    return out


def _corner_name(s: Record) -> str:
    return ("前" if s["actuation"]["steer"] or s["position_mm"][0] >= 0 else "後") + ("左" if s["position_mm"][1] > 0 else "右")


def place(frame, lay: Layout, supports: list[Record], sections: Record, rho: float, post_dx: float,
          rail: Record, avoid: tuple[str, ...] = (), lower_cross: bool = True, whole: tuple[str, ...] = (),
          rear_post_dx: dict[str, float] | None = None, joint: Record | None = None) -> Record:
    """Add each corner's tower, lower arm legs and cross members to the frame and its brackets to the
    parts (see the module doc). sections: {"tower", "leg", "lower"} section keys of the frame; rail:
    {"y", "z", "b", "h"}; whole: rail members not to split (a post there stands on the wheel's station
    node, a bracket there is carried by it); rear_post_dx: {axle station: dx} puts that axle's rear posts
    dx behind the wheel centre instead of post_dx (the lower arm's rear leg still starts from the rail
    node post_dx behind, a node of its own); joint: the suspension brackets' part.joint (joint_sizes(), the
    default; it sets their plates' thickness and so their mass). Returns {"members": [ids], "brackets": [ids], "skipped_cross": [why]}."""
    from hiraku.solvers.k4_geometry.obb import from_box, from_member, overlap

    joint = joint_sizes() if joint is None else joint
    stations = {n for n in frame.nodes}  # nodes before this: the ladder's stations (and any others)
    rail_top = rail["z"] + rail["h"] / 2.0
    out = {"members": [], "brackets": [], "skipped_cross": []}
    pivots: dict[tuple[str, str], dict[str, str]] = {}  # (axle station, which pivot) -> {side: node}
    corners = [s for s in supports if (s.get("suspension") or {}).get("hardpoints_mm") and s.get("frame_node")]
    for s in corners:
        hp = {k: np.asarray(v, float) for k, v in s["suspension"]["hardpoints_mm"].items()}
        side, st = s["frame_node"][0], s["frame_node"][1:]
        sy = 1.0 if side == "L" else -1.0
        x0 = float(hp["wheel_center"][0])
        ry = sy * rail["y"]
        tag = f"{side}{st}"

        def on_rail(x: float, nid: str) -> str | None:
            """A rail node at x, or None where the rail piece must stay whole (`whole`) or the rail does not
            reach (beyond the frame's end)."""
            on = member_through(frame, [x, ry, rail["z"]])
            known = any(float(np.linalg.norm(np.asarray(q) - np.array([x, ry, rail["z"]]))) <= frame.merge_mm
                        for q in frame.nodes.values())
            if on in whole or (on is None and not known):
                return None
            return split_rail(frame, [x, ry, rail["z"]], nid, stations, s["frame_node"])

        # rail nodes: the posts and below the upper arm pivots; on a piece kept whole there is no post (the top
        # beam reaches over from the other post) and the bracket is carried by the wheel's own station node
        behind = float((rear_post_dx or {}).get(st, post_dx))
        dxs = {1: post_dx, 2: -behind}
        posts = {i: on_rail(x0 + dx, f"{tag}p{i}") for i, dx in dxs.items()}
        leg_feet = dict(posts)  # the rail nodes the lower arm's legs start from
        if behind != post_dx:
            leg_feet[2] = on_rail(x0 - post_dx, f"{tag}q2")
        under_u = {i: on_rail(float(hp[k][0]), f"{tag}u{i}") or s["frame_node"]
                   for i, k in ((1, "uca_front"), (2, "uca_rear"))}
        # the tower
        du = hp["damper_upper"]
        z_top = float(du[2])
        over = frame.node(f"{tag}m", [float(du[0]), ry, z_top])
        tip = frame.node(f"{tag}d", du.tolist())
        if posts[1] is None and posts[2] is None:  # the rails either side kept whole: one post on the station node,
            # leaning back (so that the station's T joint, where gussets go, stays the cross member's alone, and the
            # hand calculations, which take a member at the node along x as the rail and across y as the cross
            # member, see neither in it). Its top stays over the rail's centre line (no lean across y): its section
            # is then square to y (K1's axes) and its footprint on the rail's top face centred on the rail
            frame.nodes.pop(over)
            over = frame.node(f"{tag}m", [float(du[0]) - SINGLE_POST_LEAN_X_MM, ry, z_top])
            out["members"].append(frame.member(f"tower_post_{tag}", s["frame_node"], over, sections["tower"]))
        for i, dx in dxs.items():
            if posts[i] is None:
                continue
            top = frame.node(f"{tag}t{i}", [x0 + dx, ry, z_top])
            out["members"].append(frame.member(f"tower_post_{tag}{i}", posts[i], top, sections["tower"]))
            a_, b_ = (top, over) if i == 1 else (over, top)
            out["members"].append(frame.member(f"tower_top_{tag}{i}", a_, b_, sections["tower"]))
        out["members"].append(frame.member(f"tower_arm_{tag}", over, tip, sections["tower"]))
        # lower arm pivots on legs from the posts' rail nodes; where the post stands on the wheel's own station
        # node (a rail piece kept whole) a leg from there would cross the driveshaft's way, so the pivot is
        # held by a bracket hanging from that node instead
        corner = _corner_name(s)
        for i, k in ((1, "lca_front"), (2, "lca_rear")):
            if leg_feet[i] is None:
                p = hp[k]
                # MEASUREMENT ONLY (scratch): HIRAKU_LCA_FULL_WALL=1 takes the box to the rail's outer face
                import os as _os
                y_out = ry + sy * rail["b"] / 2.0 if _os.environ.get("HIRAKU_LCA_FULL_WALL") == "1" else ry
                ys = sorted((float(p[1]) - sy * BRACKET_PAD_MM, y_out))
                zs = sorted((float(p[2]) - BRACKET_PAD_MM, max(rail["z"] - rail["h"] / 2.0, float(p[2]) + BRACKET_PAD_MM)))
                lo = [float(p[0]) - BRACKET_HALF_X_MM, ys[0], zs[0]]
                hi = [float(p[0]) + BRACKET_HALF_X_MM, ys[1], zs[1]]
                pid = f"bracket_lca_{s['id']}{i}"
                lay.parts.append(_bracket(pid, f"{corner}の下の腕の付け根の金具 ({'前' if i == 1 else '後'})", lo, hi,
                                          [s["frame_node"]], rho, joint))
                out["brackets"].append(pid)
                continue
            n = frame.node(f"{tag}l{i}", hp[k].tolist())
            out["members"].append(frame.member(f"lower_leg_{tag}{i}", leg_feet[i], n, sections["leg"]))
            pivots.setdefault((st, k), {})[side] = n
        # brackets at the upper arm pivots, standing on the rail
        for i, k in ((1, "uca_front"), (2, "uca_rear")):
            p = hp[k]
            y_in = float(p[1]) - sy * BRACKET_PAD_MM
            ys = sorted((y_in, ry))
            lo = [float(p[0]) - BRACKET_HALF_X_MM, ys[0], min(rail_top, float(p[2]) - BRACKET_PAD_MM)]
            hi = [float(p[0]) + BRACKET_HALF_X_MM, ys[1], max(rail_top, float(p[2]) + BRACKET_PAD_MM)]
            pid = f"bracket_uca_{s['id']}{i}"
            lay.parts.append(_bracket(pid, f"{corner}の上の腕の付け根の金具 ({'前' if i == 1 else '後'})", lo, hi,
                                      [under_u[i]], rho, joint))
            out["brackets"].append(pid)
    # lower cross members joining the pivots left and right, where nothing in `avoid` is in the way
    avoid_boxes = [from_box(p["box"]["center_mm"], p["box"]["size_mm"], p["box"].get("rpy_deg"), p["id"])
                   for p in lay.parts if p["id"] in avoid]
    dims = sections["dims"]["lower"]
    crosses: dict[tuple[str, str], str] = {}
    for (st, k), by_side in sorted(pivots.items()) if lower_cross else ():
        if set(by_side) != {"L", "R"}:
            continue
        a, b = (np.asarray(frame.nodes[by_side[s]], float) for s in ("L", "R"))
        box = from_member(a, b, dims["b"], dims["h"])
        hit = [o.name for o in avoid_boxes if overlap(box, o) > -GAP_MM]
        if hit:
            out["skipped_cross"].append(f"{st} {k}: {', '.join(hit)} in the way")
            continue
        mid = f"lower_cross_{st}{'1' if k == 'lca_front' else '2'}"
        out["members"].append(frame.member(mid, by_side["L"], by_side["R"], sections["lower"]))
        crosses[(st, k)] = mid
    # brackets at the tie rods' and toe links' inner ends, hanging from the rail node below the nearer
    # upper arm pivot (the leg down to the lower arm's pivot arrives beside it, so not on the cross member)
    for s in corners:
        hp = {k: np.asarray(v, float) for k, v in s["suspension"]["hardpoints_mm"].items()}
        side, st = s["frame_node"][0], s["frame_node"][1:]
        sy = 1.0 if side == "L" else -1.0
        p = hp["tie_inner"]
        near = min(("uca_front", "uca_rear"), key=lambda k: abs(float(hp[k][0]) - float(p[0])))
        node = f"{side}{st}u{1 if near == 'uca_front' else 2}"
        node = node if node in frame.nodes else s["frame_node"]  # on a rail piece kept whole
        q = np.asarray(frame.nodes[node], float)
        ys = sorted((float(p[1]) - sy * BRACKET_PAD_MM, float(q[1])))
        zs = sorted((float(p[2]) - RACK_RADIUS_MM, rail["z"] - rail["h"] / 2.0))
        zs[1] = max(zs[1], float(p[2]) + RACK_RADIUS_MM) if zs[0] == rail["z"] - rail["h"] / 2.0 else zs[1]
        lo = [float(p[0]) - BRACKET_HALF_X_MM, ys[0], zs[0]]
        hi = [float(p[0]) + BRACKET_HALF_X_MM, ys[1], zs[1]]
        what = "ラックの留め金具 (タイロッドの内の端)" if s["actuation"]["steer"] else "トーのリンクの内の端の金具"
        pid = f"bracket_tie_{s['id']}"
        lay.parts.append(_bracket(pid, f"{_corner_name(s)}の{what}", lo, hi, [node], rho, joint))
        out["brackets"].append(pid)
    return out


def link_clearances(design: Record, travel_mm: float) -> dict[str, Record]:
    """Through the travel, for every corner with hard points: the smallest gap (mm) from the axis of
    each upper arm, lower arm and the damper to the frame's members (boxes), less the radius kept
    for it (ARM_RADIUS_MM, DAMPER_RADIUS_MM), leaving out the members that end at a node the link is
    joined to (its pivots, the damper's top). {support: {"upper_arm", "lower_arm", "damper": (gap, member)}}."""
    from hiraku.solvers.k4_geometry.capsule import capsule_box_overlap, from_points
    from hiraku.solvers.k4_geometry.obb import from_member

    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    boxes = []
    for m in design["members"]:
        dims = design["sections"][m["section"]]["dims_mm"]
        w, h = float(dims.get("b", dims.get("d", 0.0))), float(dims.get("h", dims.get("d", 0.0)))
        boxes.append((m, from_member(nodes[m["node_a"]], nodes[m["node_b"]], w, h, m["id"])))
    out = {}
    for s in design["supports"]:
        hp = (s.get("suspension") or {}).get("hardpoints_mm")
        if not hp:
            continue
        sy = 1.0 if s["position_mm"][1] > 0 else -1.0
        flip = np.array([1.0, sy, 1.0])
        at = {k: {n for n, q in nodes.items() if float(np.linalg.norm(q - np.asarray(v, float))) <= 1.0}
              for k, v in hp.items()}
        res = {"upper_arm": (math.inf, ""), "lower_arm": (math.inf, ""), "damper": (math.inf, "")}
        for p in poses(_left(hp), float(s["radius_mm"]), travel_mm):
            links = {"upper_arm": [(hp["uca_front"], p["ubj"] * flip, ARM_RADIUS_MM, at["uca_front"]),
                                   (hp["uca_rear"], p["ubj"] * flip, ARM_RADIUS_MM, at["uca_rear"])],
                     "lower_arm": [(hp["lca_front"], p["lbj"] * flip, ARM_RADIUS_MM, at["lca_front"]),
                                   (hp["lca_rear"], p["lbj"] * flip, ARM_RADIUS_MM, at["lca_rear"])],
                     "damper": [(p["damper_lower"] * flip, hp["damper_upper"], DAMPER_RADIUS_MM, at["damper_upper"])]}
            for name, segs in links.items():
                for a, b, r, joined in segs:
                    cap = from_points(np.asarray(a, float), np.asarray(b, float), 0.0)
                    for m, o in boxes:
                        if {m["node_a"], m["node_b"]} & joined:
                            continue
                        gap = -capsule_box_overlap(cap, o) - r
                        if gap < res[name][0]:
                            res[name] = (float(gap), m["id"])
        out[s["id"]] = res
    return out

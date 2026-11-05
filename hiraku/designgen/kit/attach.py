"""Mounts for parts that touch nothing: a spacer or a bracket from the part to the nearest frame member,
and stands that hold the pedals on the footwell floor or the front bulkhead.

    rows = attach.mount_parts(design, ["floor", "battery"], rho)        # on the finished design
    rows = attach.pedal_stands(design, ["pedal_accel", "pedal_brake"], "bulkhead", rho)

What "touch" means is the load_path tool's (hiraku.solvers.shelf.load_path: boxes at most its TOUCH_MM
apart); a part is "free" when no chain of touching boxes reaches a member (load_path.contact_groups). The
mounts only add parts of kind bracket; the parts they hold do not move.

How a mount is shaped (code, from the gap and the boxes; the sizes below are assumptions, no source):
- the part's box and the member's box, both square to the axes, are compared axis by axis. Along an axis
  where they overlap by at least MIN_OVERLAP_MM, the mount's footprint is the middle of the overlap, at most
  PAD_MM long; along an axis where they are apart, the mount fills the gap from one face to the other.
  The mount's box therefore touches both boxes (gap 0) and goes into neither;
- overlapping on two axes (apart on one): a spacer or a hanger straight across the gap; overlapping on one
  axis only: an angle bracket across the corner between them (it touches both along an edge);
- of the members whose mount would fit, the ones overlapping on more axes first, then the smaller gap,
  then the larger footprint; a second mount to another member when its gap is within SECOND_GAP_MM of the
  first's and it is at least SECOND_APART_MM from the first (a part held at two places, as a floor on both
  rails or a battery between two cross members);
- a mount must not reach into any other body further than K4 allows (k4_geometry.model.allowance: touching
  TOUCH_MM, MOUNT_MM into a member that shares its carried_by nodes), and the gap at most MAX_GAP_MM;
- mass: a spacer (gap at most SPACER_MAX_MM) is solid steel; a longer bracket is two plates BRACKET_T_MM
  thick, each the size of the box's largest face (never more than the solid box). carried_by: the member's
  end nodes. material ss400 (design.materials), finish painted.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

Record = dict[str, Any]

PAD_MM = 50.0  # the mount's footprint along an axis where the boxes overlap, at most this long
MIN_OVERLAP_MM = 10.0  # an overlap shorter than this is no footprint
MAX_GAP_MM = 100.0  # a part farther than this from every member gets no mount here
SPACER_MAX_MM = 20.0  # up to this gap a mount is a solid spacer; beyond, a bracket of two plates
BRACKET_T_MM = 3.2  # the plates' thickness (as kit/mounts.BRACKET_T_MM)
SECOND_GAP_MM = 1.0
SECOND_APART_MM = 100.0
NEAR_MM = 1500.0  # members whose centre is farther than this from the part's are not looked at
STAND_PAD_MM = 40.0  # a pedal stand's footprint across the pedal and along its edge
MOUNT_MATERIAL = "ss400"
MOUNT_FINISH = "painted"


class AttachError(ValueError):
    pass


def _aabb(obb) -> tuple[np.ndarray, np.ndarray] | None:
    """(lo, hi) of a box whose axes are the design's axes (in any order), else None."""
    ax = np.abs(np.asarray(obb.axes, float))
    if not np.allclose(np.sort(ax, axis=1)[:, -1], 1.0, atol=1e-9):
        return None
    half = ax.T @ np.asarray(obb.half, float)
    c = np.asarray(obb.center, float)
    return c - half, c + half


def span(a: tuple[np.ndarray, np.ndarray], b: tuple[np.ndarray, np.ndarray], pad: float = PAD_MM,
         min_overlap: float = MIN_OVERLAP_MM) -> Record | None:
    """The mount between two axis-square boxes (see the module doc): {"lo", "hi", "overlap_axes", "gap",
    "footprint"}, or None when they overlap on no axis, are apart on none, or overlap by less than min_overlap."""
    lo, hi = np.zeros(3), np.zeros(3)
    n_over, gaps, foot = 0, [], 1.0
    for i in range(3):
        o0, o1 = max(a[0][i], b[0][i]), min(a[1][i], b[1][i])
        if o1 - o0 >= min_overlap:
            c, h = (o0 + o1) / 2.0, min(pad, o1 - o0) / 2.0
            lo[i], hi[i] = c - h, c + h
            n_over += 1
            foot *= 2.0 * h
        elif o1 - o0 < -1e-9:
            lo[i], hi[i] = o1, o0  # the gap: from the nearer box's face to the other's
            gaps.append(o0 - o1)
        else:
            return None
    if not n_over or not gaps:
        return None
    return {"lo": lo, "hi": hi, "overlap_axes": n_over, "gap": float(math.hypot(*gaps)), "footprint": foot}


def mount_mass(lo, hi, rho: float, gap: float) -> tuple[float, str]:
    """(kg, "spacer" or "bracket") of a mount box across a gap of `gap` mm (see the module doc)."""
    size = np.sort(np.asarray(hi, float) - np.asarray(lo, float))
    solid = rho * 1e-9 * float(np.prod(size))
    if gap <= SPACER_MAX_MM:
        return solid, "spacer"
    return min(solid, rho * 1e-9 * 2.0 * BRACKET_T_MM * float(size[1] * size[2])), "bracket"


def _body(pid: str, lo, hi, nodes, kind: str = "bracket"):
    from hiraku.solvers.k4_geometry.model import Body
    from hiraku.solvers.k4_geometry.obb import from_box

    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    return Body(f"part:{pid}", from_box((lo + hi) / 2.0, hi - lo, None, pid), kind, None, set(nodes))


def fits(design: Record, body, skip: set[str], bs: dict | None = None) -> str | None:
    """None when the new body reaches into no other body further than K4 allows; else the first one it does."""
    from hiraku.solvers.k4_geometry import model as k4

    bs = bs if bs is not None else k4.bodies(design)
    r = float(np.linalg.norm(body.obb.half))
    for k, other in bs.items():
        if k in skip:
            continue
        if np.linalg.norm(other.obb.center - body.obb.center) > r + float(np.linalg.norm(other.obb.half)) + 1.0:
            continue
        if k4.shape_overlap(body, other) > k4.allowance(body, other, design):
            return k
    return None


def _record(pid: str, name: str, lo, hi, nodes, rho: float, gap: float) -> Record:
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    mass, _ = mount_mass(lo, hi, rho, gap)
    return {"id": pid, "name": name, "kind": "bracket",
            "box": {"center_mm": [float(v) for v in (lo + hi) / 2.0], "size_mm": [float(v) for v in hi - lo]},
            "mass_kg": float(mass), "carried_by": list(nodes), "material": MOUNT_MATERIAL, "finish": MOUNT_FINISH}


def free_parts(design: Record) -> tuple[set[str], set[str]]:
    """(held, free) by the load_path tool's contacts."""
    from hiraku.solvers.shelf.load_path import contact_groups, find_contacts

    return contact_groups(design, find_contacts(design))


def candidates(design: Record, pid: str, bs: dict | None = None) -> list[Record]:
    """Every mount that would fit from part `pid` to a member, best first (see the module doc)."""
    from hiraku.solvers.k4_geometry import model as k4

    bs = bs if bs is not None else k4.bodies(design)
    P = bs[f"part:{pid}"]
    pa = _aabb(P.obb)
    if pa is None:
        return []
    out = []
    for k, M in bs.items():
        if M.kind != "member" or np.linalg.norm(M.obb.center - P.obb.center) > NEAR_MM:
            continue
        ma = _aabb(M.obb)
        if ma is None:
            continue
        s = span(pa, ma)
        if s is None or s["gap"] > MAX_GAP_MM:
            continue
        body = _body(f"mount_{pid}", s["lo"], s["hi"], M.nodes)
        hit = fits(design, body, {P.id, k}, bs)
        if hit is not None:
            continue
        out.append({**s, "member": k.split(":", 1)[1], "nodes": sorted(M.nodes)})
    out.sort(key=lambda c: (-c["overlap_axes"], round(c["gap"], 6), -c["footprint"]))
    return out


def mount_parts(design: Record, ids, rho: float, always=(), labels: dict | None = None) -> list[Record]:
    """Add mounts (parts of kind bracket, id mount_<part>_<n>) to design.parts for the parts in `ids`, in order:
    each part the load_path tool finds free (after the mounts added before it) gets one or two, and each part in
    `always` gets them even when it is held through what it touches (a seat belt's anchor goes to the frame).
    Returns a row per part: {"part", "mounts": [{"id", "member", "gap_mm", "size_mm", "mass_kg", "type"}],
    "held_by": "touch" | "mount" | None, "why"}."""
    from hiraku.solvers.k4_geometry import model as k4

    parts = {p["id"]: p for p in design["parts"]}
    rows = []
    # the loop changes the design only by adding mounts to design.parts: while their count is the same, the contacts
    # (and so the held parts) are the ones found last
    held, held_at = set(), None
    for pid in ids:
        if pid not in parts:
            continue
        if held_at != len(design["parts"]):
            held, _ = free_parts(design)
            held_at = len(design["parts"])
        row = {"part": pid, "mounts": [], "held_by": "touch" if pid in held else None, "why": None}
        if pid in held and pid not in always:
            rows.append(row)
            continue
        bs = k4.bodies(design)
        cands = candidates(design, pid, bs)
        if not cands:
            row["why"] = (f"no member within {MAX_GAP_MM:g} mm that a mount square to the axes reaches clear of the "
                          f"other parts")
            rows.append(row)
            continue
        chosen = [cands[0]]
        for c in cands[1:]:
            far = np.linalg.norm((c["lo"] + c["hi"]) / 2.0 - (cands[0]["lo"] + cands[0]["hi"]) / 2.0)
            if (c["overlap_axes"] == cands[0]["overlap_axes"] and c["gap"] <= cands[0]["gap"] + SECOND_GAP_MM
                    and far >= SECOND_APART_MM and c["member"] != cands[0]["member"]):
                chosen.append(c)
                break
        label = (labels or {}).get(pid, parts[pid]["name"])
        for n, c in enumerate(chosen, 1):
            mid = f"mount_{pid}_{n}"
            mass, kind = mount_mass(c["lo"], c["hi"], rho, c["gap"])
            word = "スペーサー" if kind == "spacer" else "金具"
            rec = _record(mid, f"{label}の取付の{word} ({c['member']} へ)", c["lo"], c["hi"], c["nodes"], rho, c["gap"])
            if fits(design, _body(mid, c["lo"], c["hi"], c["nodes"]), {f"part:{pid}", f"member:{c['member']}"}) is not None:
                continue  # a mount added just before (the first of the two) is in the way
            design["parts"].append(rec)
            row["mounts"].append({"id": mid, "member": c["member"], "gap_mm": c["gap"], "size_mm": rec["box"]["size_mm"],
                                  "mass_kg": rec["mass_kg"], "type": kind})
        if row["mounts"]:
            row["held_by"] = row["held_by"] or "mount"
        rows.append(row)
    return rows


def _corners(part: Record) -> np.ndarray:
    from hiraku.solvers.k4_geometry.obb import from_box

    b = part["box"]
    return from_box(b["center_mm"], b["size_mm"], b.get("rpy_deg")).corners()


def pedal_stands(design: Record, pedals, target: str, rho: float) -> list[Record]:
    """A stand (kind bracket, id stand_<pedal>) under or ahead of each pedal: target "floor_front" (or any part
    below it): from the part's top face up to the pedal's lowest edge; target "bulkhead" (or any part ahead):
    from its rear face back to the pedal's front-most edge. Across the pedal at its middle, STAND_PAD_MM wide; along
    the edge STAND_PAD_MM centred on the edge. carried_by: the target's. A target "member:<id>" (a frame member ahead
    of the pedals, square to the axes) takes the member's box (k4_geometry) and its end nodes, and the stands go
    from its rear face as from the bulkhead's. Raises AttachError when a stand would reach into another body.
    Returns a row per pedal like mount_parts'."""
    parts = {p["id"]: p for p in design["parts"]}
    if target.startswith("member:"):
        from hiraku.solvers.k4_geometry import model as k4

        body = k4.bodies(design).get(target)
        box = _aabb(body.obb) if body is not None else None
        if box is None:
            raise AttachError(f"no member {target.split(':', 1)[1]!r} square to the axes to stand the pedals on")
        tc, ts = (box[0] + box[1]) / 2.0, box[1] - box[0]
        t_nodes, t_key, t_name = sorted(body.nodes), target, target.split(":", 1)[1]
    elif target in parts:
        t = parts[target]
        tc, ts = np.asarray(t["box"]["center_mm"], float), np.asarray(t["box"]["size_mm"], float)
        t_nodes, t_key, t_name = list(t.get("carried_by") or []), f"part:{target}", target
    else:
        raise AttachError(f"no part {target!r} to stand the pedals on")
    rows = []
    for pid in pedals:
        p = parts[pid]
        cs = _corners(p)
        y = float(p["box"]["center_mm"][1])
        if target == "bulkhead" or t_key.startswith("member:") or tc[0] > float(np.max(cs[:, 0])):
            x0 = float(np.max(cs[:, 0]))
            edge = cs[np.abs(cs[:, 0] - x0) < 1e-6]
            z = float(np.mean(edge[:, 2]))
            lo = [x0, y - STAND_PAD_MM / 2.0, z - STAND_PAD_MM / 2.0]
            hi = [float(tc[0] - ts[0] / 2.0), y + STAND_PAD_MM / 2.0, z + STAND_PAD_MM / 2.0]
            where = "ペダルの梁から" if t_key.startswith("member:") else "前の隔壁から"
        else:
            z0 = float(np.min(cs[:, 2]))
            edge = cs[np.abs(cs[:, 2] - z0) < 1e-6]
            x = float(np.mean(edge[:, 0]))
            # the half of the pad on the side the pedal rises from its lowest edge stays clear of it
            lo = [x - STAND_PAD_MM / 2.0, y - STAND_PAD_MM / 2.0, float(tc[2] + ts[2] / 2.0)]
            hi = [x + STAND_PAD_MM / 2.0, y + STAND_PAD_MM / 2.0, z0]
            where = "床から"
        if hi[0] - lo[0] <= 0 or hi[2] - lo[2] <= 0:
            raise AttachError(f"{pid} is not clear of {t_name}: no room for a stand")
        sid = f"stand_{pid}"
        nodes = list(t_nodes)
        body = _body(sid, lo, hi, nodes)
        hit = fits(design, body, {f"part:{pid}", t_key})
        if hit is not None:
            raise AttachError(f"the stand of {pid} {where} would reach into {hit.split(':', 1)[1]}")
        gap = float(hi[0] - lo[0]) if where != "床から" else float(hi[2] - lo[2])
        rec = _record(sid, f"{p['name']}の台 ({where})", lo, hi, nodes, rho, gap)
        design["parts"].append(rec)
        rows.append({"part": pid, "mounts": [{"id": sid, "member": t_name, "gap_mm": gap, "size_mm": rec["box"]["size_mm"],
                                              "mass_kg": rec["mass_kg"], "type": "stand"}],
                     "held_by": "mount", "why": None})
    return rows

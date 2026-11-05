"""Wheels through their suspension travel and steering range against everything else.

The wheels (unsprung parts other than kind suspension) move up and down by
half the suspension travel each way and, on steered supports, turn about the
vertical axis through the wheel centre by up to the design variable
max_steer_angle. Suspension arms pivot at the frame, so a rigid box would
overstate their motion; their boxes are not swept. Links (model.LINK_KINDS: driveshafts, tie
rods) keep their inner end in place and move their outer end with the wheel.
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc import units
from .model import LINK_KINDS, TOUCH_MM, Body, bodies, mounted, shape_overlap

STEPS = 7


def _rz(angle_deg: float) -> np.ndarray:
    a = math.radians(angle_deg)
    return np.array([[math.cos(a), -math.sin(a), 0.0], [math.sin(a), math.cos(a), 0.0], [0.0, 0.0, 1.0]])


def max_steer(design: dict) -> float:
    q = design.get("variables", {}).get("max_steer_angle")
    return units.to_unit(q, "deg", "max_steer_angle") if q is not None else 0.0


def _spheres(bs: dict[str, Body]) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Every body's sphere (_sphere), in the order of bs: (keys, centres (n, 3), radii (n,))."""
    keys = list(bs)
    got = [_sphere(bs[k]) for k in keys]
    return (keys, np.array([c for c, _ in got], float).reshape(-1, 3), np.array([r for _, r in got], float))


_KIN: dict[tuple[int, str], object] = {}  # (id of the design, support) -> CornerKinematics, the last few
_MOUNTED: dict[tuple[int, str, str], bool] = {}  # (id of the design, body, body) -> mounted(), asked again at every pose


def _mounted(a: Body, b: Body, design: dict) -> bool:
    """model.mounted, kept per pair of bodies of one design (it reads only the design and the bodies' ids at rest)."""
    key = (id(design), a.id, b.id)
    got = _MOUNTED.get(key)
    if got is None:
        if len(_MOUNTED) > 200000:
            _MOUNTED.clear()
        got = _MOUNTED[key] = mounted(a, b, design)
    return got


def corner_kinematics(design: dict, sid: str):
    """corner_motion.CornerKinematics of a support with double-wishbone hard points (damper points too), else None."""
    from .corner_motion import CornerKinematics, has_kinematics

    key = (id(design), sid)
    if key not in _KIN:
        if len(_KIN) > 32:
            _KIN.clear()
        _KIN[key] = CornerKinematics(design, sid) if has_kinematics(design, sid) else None
    return _KIN[key]


def _pose(design: dict, bs: dict[str, Body], sid: str, k: str, dz: float, steer: float, maps=None) -> Body:
    """Body k of corner sid at a wheel travel (mm) and a steer angle (deg). A corner with hard points moves
    as its suspension does (corner_motion; maps: CornerKinematics.maps at that pose, when made); another
    one as described at the top."""
    from .corner_motion import moved as kin_moved

    b = bs[k]
    kin = corner_kinematics(design, sid)
    if kin is not None:
        return kin_moved(b, sid, maps if maps is not None else kin.maps(dz, steer))
    s = next(x for x in design["supports"] if x["id"] == sid)
    c = np.asarray(s["position_mm"], float)
    R = _rz(steer)
    if b.kind in LINK_KINDS and b.capsule is not None:
        return _link_moved(b, R, c, dz, steer)
    return (b.turned(R, c) if steer else b).moved([0.0, 0.0, dz])


def _overlap(o: Body, other: Body, slabs=None) -> float:
    """shape_overlap, a wheel taken as round (corner_motion.wheel_overlap: its box's corners would meet what
    the tyre does not)."""
    from .corner_motion import wheel_overlap

    if o.kind == "wheel" and o.capsule is None:
        return wheel_overlap(o, other, slabs)
    if other.kind == "wheel" and other.capsule is None:
        return wheel_overlap(other, o)
    return shape_overlap(o, other)


def _connected(design: dict) -> set[frozenset]:
    """The pairs of part ids one part's connects_to names (either way)."""
    out = set()
    for p in design.get("parts", []):
        for c in p.get("connects_to") or []:
            if c.get("to"):
                out.add(frozenset((p["id"], c["to"])))
    return out


def _rest_own(design: dict, bs: dict[str, Body], sid: str) -> dict[tuple[str, str], float]:
    """The overlaps of a corner's own parts with each other at the design pose, for the pairs not joined to
    each other (connects_to either way, or model.joined: a link and what it is jointed to at an end).
    model.allowance lets a corner's parts overlap each other any amount (they ride together); in motion
    they do not all move together, so they are compared, and only what grows past the overlap at rest
    counts (an arm's end inside the wheel's rim is there by design)."""
    from .model import joined

    conn = _connected(design)
    group = sorted(k for k, b in bs.items() if b.support == sid and b.kind != "suspension")
    out = {}
    for i, k in enumerate(group):
        for k2 in group[i + 1:]:
            a, b = bs[k], bs[k2]
            if frozenset((k.split(":", 1)[1], k2.split(":", 1)[1])) in conn or joined(a, b):
                continue
            out[(k, k2)] = _overlap(a, b)
    return out


def _hits(design: dict, bs: dict[str, Body], sid: str, dz: float, steer: float,
          spheres: tuple[list[str], np.ndarray, np.ndarray] | None = None, threshold: float = TOUCH_MM,
          own: dict[tuple[str, str], float] | None = None) -> list[tuple[str, str, float]]:
    """The pairs (a moved body of corner sid, another body) overlapping by more than `threshold` at the pose
    (negative: within that gap). spheres: _spheres(bs), when the caller asks for many positions of the same
    bodies. own: _rest_own of the corner, to check its own parts against each other too; for those pairs the
    overlap given is what grows past the overlap at rest (at least 0)."""
    from .corner_motion import wheel_slabs

    group = {k: b for k, b in bs.items() if b.support == sid and b.kind != "suspension"}
    keys, C, Rad = spheres or _spheres(bs)
    kin = corner_kinematics(design, sid)
    maps = kin.maps(dz, steer) if kin is not None else None
    moved = {k: _pose(design, bs, sid, k, dz, steer, maps) for k in group}
    out = []
    reach = max(TOUCH_MM, -threshold)
    for k, o in moved.items():
        b = bs[k]
        c_o, r_o = _sphere(o)
        # spheres round the two shapes apart by more than the reach: they cannot come that close (a quick test)
        near = np.sqrt(((C - c_o) ** 2).sum(axis=1)) <= r_o + Rad + reach
        slabs = wheel_slabs(o) if o.kind == "wheel" and o.capsule is None else None
        for i in np.flatnonzero(near):
            k2 = keys[i]
            if k2 in group:
                continue
            other = bs[k2]
            if _mounted(b, other, design):
                continue
            ov = _overlap(o, other, slabs)
            if ov > threshold:
                out.append((k, k2, ov))
    for (k, k2), rest in (own or {}).items():
        if k in moved and k2 in moved:
            ov = _overlap(moved[k], moved[k2]) - max(rest, 0.0)
            if ov > threshold:
                out.append((k, k2, ov))
    return out


FINE_TRAVEL_MM = 5.0  # the fine search's steps around a pair's closest coarse pose
FINE_STEER_DEG = 1.0
NEAR_MM = 20.0  # pairs this close at a coarse pose are searched finely around it


def _fine(design: dict, bs: dict[str, Body], sid: str, k: str, k2: str, rest: float | None, dz0: float, st0: float,
          d_dz: float, d_st: float, lo_hi_dz: tuple[float, float], lo_hi_st: tuple[float, float]) -> tuple[float, float, float]:
    """The pair's largest overlap (past `rest` for a corner's own pair) on the fine grid around (dz0, st0), one
    coarse step each way: (overlap, dz, steer)."""
    best = (-np.inf, dz0, st0)
    dzs = np.arange(max(lo_hi_dz[0], dz0 - d_dz), min(lo_hi_dz[1], dz0 + d_dz) + 1e-9, FINE_TRAVEL_MM) if d_dz else [dz0]
    sts = np.arange(max(lo_hi_st[0], st0 - d_st), min(lo_hi_st[1], st0 + d_st) + 1e-9, FINE_STEER_DEG) if d_st else [st0]
    for dz in dzs:
        for st in sts:
            o = _pose(design, bs, sid, k, float(dz), float(st))
            o2 = _pose(design, bs, sid, k2, float(dz), float(st)) if bs[k2].support == sid else bs[k2]
            ov = _overlap(o, o2) - (max(rest, 0.0) if rest is not None else 0.0)
            if ov > best[0]:
                best = (ov, float(dz), float(st))
    return best


def _sphere(b: Body) -> tuple[np.ndarray, float]:
    """A sphere round the shape the overlap test uses: the capsule's (a moved link's box is not moved), else the
    box's."""
    if b.capsule is not None:
        c = b.capsule
        return (c.a + c.b) / 2.0, float(np.linalg.norm(c.b - c.a)) / 2.0 + c.r
    return b.obb.center, float(np.linalg.norm(b.obb.half))


def _link_moved(b: Body, R: np.ndarray, c: np.ndarray, dz: float, steer: float) -> Body:
    """A link with the inner end (nearer the centre line) in place and the outer end moved with the wheel."""
    from dataclasses import replace

    from .capsule import Capsule

    a, e = b.capsule.a, b.capsule.b
    inner, outer = (a, e) if abs(a[1]) <= abs(e[1]) else (e, a)
    moved = (c + R @ (outer - c) if steer else outer) + np.array([0.0, 0.0, dz])
    return replace(b, capsule=Capsule(inner, moved, b.capsule.r, b.capsule.name))


def sweep(design: dict, bs: dict[str, Body] | None = None) -> dict:
    """Worst overlap over the travel and steering grid for every support (with where it happens),
    and the free steer angle: {"hits": [(a, b, overlap, condition)], "free_steer": {support: deg}}."""
    bs = bs or bodies(design)
    steer_max = max_steer(design)
    hits: dict[tuple[str, str], tuple[float, str]] = {}
    free = {}
    sph = _spheres(bs)
    for s in design["supports"]:
        sid = s["id"]
        travel = float(s.get("suspension", {}).get("travel_mm") or 0.0)
        dzs = np.linspace(-travel / 2.0, travel / 2.0, STEPS) if travel > 0 else [0.0]
        steers = np.linspace(-steer_max, steer_max, STEPS) if s["actuation"]["steer"] and steer_max > 0 else [0.0]
        own = _rest_own(design, bs, sid) if corner_kinematics(design, sid) is not None else None
        # coarse: every pair within NEAR_MM at some pose, and its closest pose
        close: dict[tuple[str, str], tuple[float, float, float]] = {}
        for dz in dzs:
            for st in steers:
                for a, b, ov in _hits(design, bs, sid, float(dz), float(st), sph, threshold=-NEAR_MM, own=own):
                    if ov > close.get((a, b), (-np.inf,))[0]:
                        close[(a, b)] = (ov, float(dz), float(st))
        # fine: around each close pair's closest coarse pose, one coarse step each way
        d_dz = float(dzs[1] - dzs[0]) if len(dzs) > 1 else 0.0
        d_st = float(steers[1] - steers[0]) if len(steers) > 1 else 0.0
        lims = ((float(dzs[0]), float(dzs[-1])), (float(steers[0]), float(steers[-1])))
        for (a, b), (ov, dz, st) in close.items():
            rest = (own or {}).get((a, b))
            fine = _fine(design, bs, sid, a, b, rest, dz, st, d_dz, d_st, *lims)
            ov, dz, st = max((ov, dz, st), fine)
            if ov > TOUCH_MM and ov > hits.get((a, b), (0.0, ""))[0]:
                what = " (past its overlap at rest)" if rest is not None else ""
                hits[(a, b)] = (ov, f"steer {st:+.0f} deg, wheel travel {dz:+.0f} mm{what}")
        if s["actuation"]["steer"] and steer_max > 0:
            free[sid] = free_steer(design, bs, sid, steer_max, dzs, sph, own)
    return {"hits": sorted(((a, b, o, c) for (a, b), (o, c) in hits.items()), key=lambda t: -t[2]), "free_steer": free}


def steer_room(design: dict, bs: dict[str, Body] | None = None) -> dict[str, dict]:
    """Inputs of the hand calculation geometry.free_steer_angle for each steered support:
    half width w and half length l of its wheel (kind wheel) and the room c between the
    wheel's inner face and the nearest frame member beside it running along x (a rail; within
    the wheel's length and height), plus TOUCH_MM (touching is not contact). Supports with no
    member beside them are left out."""
    bs = bs or bodies(design)
    out = {}
    for s in design["supports"]:
        if not s["actuation"]["steer"]:
            continue
        wheel = next((b for b in bs.values() if b.support == s["id"] and b.kind == "wheel"), None)
        if wheel is None:
            continue
        c = np.asarray(s["position_mm"], float)
        corners = wheel.obb.corners()
        w = float(np.ptp(corners[:, 1])) / 2.0
        l = float(np.ptp(corners[:, 0])) / 2.0
        h = float(np.ptp(corners[:, 2])) / 2.0
        side = 1.0 if c[1] >= 0 else -1.0
        inner = side * c[1] - w  # distance of the inner face from the centre line
        best = None
        for b in bs.values():
            if b.kind != "member":
                continue
            m = b.obb.corners()
            if np.ptp(m[:, 0]) <= max(np.ptp(m[:, 1]), np.ptp(m[:, 2])):
                continue  # the formula's member runs along the wheel (a rail), not across or up beside it
            if m[:, 0].max() < c[0] - l or m[:, 0].min() > c[0] + l or m[:, 2].max() < c[2] - h or m[:, 2].min() > c[2] + h:
                continue
            face = float((side * m[:, 1]).max())  # the member's face toward the wheel
            if face <= inner and (best is None or face > best[0]):
                best = (face, b.id.split(":", 1)[1])
        if best is not None:
            out[s["id"]] = {"w": w, "l": l, "c": float(inner - best[0] + TOUCH_MM), "member": best[1]}
    return out


def free_steer(design: dict, bs: dict[str, Body], sid: str, limit: float, dzs, spheres=None, own=None) -> float:
    """Largest steer angle (deg, both ways, whole travel) that adds no contact to what the
    straight-ahead wheel already has, by bisection. spheres: _spheres(bs), when already made. own:
    _rest_own of the corner (its own parts against each other too; default: made here for a corner with
    hard points)."""
    sph = spheres or _spheres(bs)
    if own is None and corner_kinematics(design, sid) is not None:
        own = _rest_own(design, bs, sid)
    base = {}
    for dz in dzs:
        for a, b, o in _hits(design, bs, sid, float(dz), 0.0, sph, own=own):
            base[(a, b)] = max(base.get((a, b), 0.0), o)

    def clear(ang: float) -> bool:
        for dz in dzs:
            for sgn in (1, -1):
                for a, b, o in _hits(design, bs, sid, float(dz), sgn * ang, sph, own=own):
                    if o > base.get((a, b), 0.0) + TOUCH_MM:
                        return False
        return True
    if clear(limit):
        return limit
    lo, hi = 0.0, limit
    for _ in range(20):
        mid = (lo + hi) / 2.0
        lo, hi = (mid, hi) if clear(mid) else (lo, mid)
    return lo

"""A double-wishbone corner's parts at a wheel travel and a steer angle, moved as the suspension moves
them (written by the K2 team for H1's sweep, motion.py).

The corner's hard points (k2_vehicle.model.hardpoints_of: the same points K2's K&C bench solves) give,
at a travel, the lower and upper arms' turns about their inner axes and the knuckle's pose
(k2_vehicle.kinematics.TravelSolver: the lower ball joint on the lower arm's circle, the upper on the
upper arm's, the tie rod's length held). Steering turns the knuckle about the kingpin axis (the line
through the two ball joints) by the steer angle; the rack moves the tie rod's inner end sideways so
the rod keeps its length. The parts follow:

- the knuckle and what it carries (the wheel, the hub, the brake): the knuckle's motion;
- the lower arm's legs (part ids lca_<support>_*) turn with the lower arm, the upper arm's (uca_*) with
  the upper arm; the lower ball joint moves with the lower ball joint, the upper with the upper;
- the damper spans from its upper mount (fixed) to its lower mount on the lower arm (it stretches);
- the tie rod spans from the rack end to the knuckle's steering arm;
- another link (a driveshaft) keeps its inner end and moves its outer end with the knuckle.

A corner without hard points with damper points (no K&C table) keeps the old motion (motion.py).
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from .capsule import Capsule
from .model import LINK_KINDS, Body

FLIP = np.array([1.0, -1.0, 1.0])


class CornerKinematics:
    """The poses of one support's corner (hard points in mm, as the design gives them)."""

    def __init__(self, design: dict, sid: str):
        from hiraku.solvers.k2_vehicle import kinematics
        from hiraku.solvers.k2_vehicle.model import hardpoints_of

        s = next(x for x in design["supports"] if x["id"] == sid)
        self.sid = sid
        hp = hardpoints_of(design, sid)
        self.right = float(np.asarray(s["position_mm"], float)[1]) < 0.0
        # solved on the left-hand corner (the bench's convention); a right corner is its mirror
        left = {k: (list(np.asarray(v, float) * FLIP) if self.right and isinstance(v, (list, tuple)) and len(v) == 3
                    else v) for k, v in hp.items()}
        self.hp = kinematics.Hardpoints.from_json(left)
        self.solver = kinematics.TravelSolver(self.hp)
        self.k = kinematics
        self.tie_len = float(np.linalg.norm(self.hp.tie_outer - self.hp.tie_inner))
        self._cache: dict[float, dict] = {}

    def _bench(self, dz_mm: float) -> dict:
        key = round(float(dz_mm), 6)
        got = self._cache.get(key)
        if got is None:
            k, hp = self.k, self.hp
            sol = self.solver.solve(dz_mm / 1000.0)
            lbj, ubj = sol["lbj"], sol["ubj"]
            ea, eu = k._unit(hp.lca_rear - hp.lca_front), k._unit(hp.uca_rear - hp.uca_front)

            def arm_turn(front, outer, e, moved):
                return k.rotation_between(outer - front - e * float((outer - front) @ e),
                                          moved - front - e * float((moved - front) @ e))
            got = {"lbj": lbj, "ubj": ubj, "rk": sol["rotation"], "rl": arm_turn(hp.lca_front, hp.lca_outer, ea, lbj),
                   "ru": arm_turn(hp.uca_front, hp.uca_outer, eu, ubj)}
            self._cache[key] = got
        return got

    def maps(self, dz_mm: float, steer_deg: float) -> dict:
        """Point maps (mm in, mm out, design frame) at a travel and a steer angle (+ turns the wheel's front
        to the left): knuckle, lower, upper, lbj, ubj (translations of the ball joints), rack (the tie
        rod's inner end)."""
        k, hp = self.k, self.hp
        b = self._bench(dz_mm)
        lbj, ubj = b["lbj"], b["ubj"]
        # the steer angle about the kingpin axis; a right corner mirrored turns the other way
        rs = k.axis_rotation(ubj - lbj, np.radians(steer_deg) * (-1.0 if self.right else 1.0))

        def knuckle(p):  # metres, left-hand frame
            return lbj + rs @ (b["rk"] @ (p - hp.lca_outer))

        def lower(p):
            return hp.lca_front + b["rl"] @ (p - hp.lca_front)

        def upper(p):
            return hp.uca_front + b["ru"] @ (p - hp.uca_front)

        tro = knuckle(hp.tie_outer)
        # the rack slides the inner end along y so the rod keeps its length (the root nearer its design place)
        dx, dzz = tro[0] - hp.tie_inner[0], tro[2] - hp.tie_inner[2]
        reach = max(self.tie_len ** 2 - dx * dx - dzz * dzz, 0.0) ** 0.5
        y_in = min((tro[1] - reach, tro[1] + reach), key=lambda y: abs(y - hp.tie_inner[1]))
        rack = np.array([hp.tie_inner[0], y_in, hp.tie_inner[2]])

        def wrap(f):
            def g(p_mm):
                p = np.asarray(p_mm, float) / 1000.0
                if self.right:
                    p = p * FLIP
                q = f(p)
                return (q * FLIP if self.right else q) * 1000.0
            return g
        return {"knuckle": wrap(knuckle), "lower": wrap(lower), "upper": wrap(upper),
                "lbj": wrap(lambda p: p + lbj - hp.lca_outer), "ubj": wrap(lambda p: p + ubj - hp.uca_outer),
                "rack": (rack * FLIP if self.right else rack) * 1000.0,
                "tie_inner": (hp.tie_inner * FLIP if self.right else hp.tie_inner) * 1000.0,
                "damper_upper": None if hp.damper_upper is None else
                (hp.damper_upper * FLIP if self.right else hp.damper_upper) * 1000.0}


def role(b: Body, sid: str) -> str:
    """How a body of corner sid moves: lower, upper, lbj, ubj, damper, tie_rod, link or knuckle."""
    pid = b.id.split(":", 1)[1]
    if b.kind == "damper":
        return "damper"
    if b.kind == "tie_rod":
        return "tie_rod"
    if pid.startswith(f"lca_{sid}"):
        return "lower"
    if pid.startswith(f"uca_{sid}"):
        return "upper"
    if b.kind == "ball_joint":
        return "lbj" if "lower" in pid else "ubj"
    if b.kind in LINK_KINDS:
        return "link"
    return "knuckle"


def _rigid(b: Body, f) -> Body:
    """The body under the point map f (a rigid motion): its box's centre and axes, its capsule's ends."""
    o = b.obb
    c2 = f(o.center)
    axes = np.array([f(o.center + a) - c2 for a in o.axes])
    axes = axes / np.linalg.norm(axes, axis=1)[:, None]
    obb = replace(o, center=c2, axes=axes)
    cap = None if b.capsule is None else Capsule(f(b.capsule.a), f(b.capsule.b), b.capsule.r, b.capsule.name)
    return replace(b, obb=obb, capsule=cap)


def _span(b: Body, a: np.ndarray, e: np.ndarray) -> Body:
    """A capsule part (a damper, a tie rod) spanning from a to e; its box follows the capsule's middle."""
    if b.capsule is None:
        return b.moved((a + e) / 2.0 - b.obb.center)
    return replace(b, capsule=Capsule(np.asarray(a, float), np.asarray(e, float), b.capsule.r, b.capsule.name),
                   obb=b.obb.moved((a + e) / 2.0 - (b.capsule.a + b.capsule.b) / 2.0))


def moved(b: Body, sid: str, m: dict) -> Body:
    """Body b of corner sid at the pose whose point maps are m (CornerKinematics.maps)."""
    r = role(b, sid)
    if r in ("knuckle", "lower", "upper", "lbj", "ubj"):
        return _rigid(b, m[r])
    if b.capsule is None:
        return _rigid(b, m["knuckle"])
    a, e = b.capsule.a, b.capsule.b
    if r == "damper" and m["damper_upper"] is not None:
        top = m["damper_upper"]
        fixed, low = (a, e) if np.linalg.norm(a - top) <= np.linalg.norm(e - top) else (e, a)
        return _span(b, fixed, m["lower"](low))
    if r == "tie_rod":
        ti = m["tie_inner"]
        inner, outer = (a, e) if np.linalg.norm(a - ti) <= np.linalg.norm(e - ti) else (e, a)
        return _span(b, inner + (m["rack"] - ti), m["knuckle"](outer))
    inner, outer = (a, e) if abs(a[1]) <= abs(e[1]) else (e, a)  # a driveshaft: the inner end stays
    return _span(b, inner, m["knuckle"](outer))


WHEEL_SLABS = 8  # the tyre's disc as this many boxes turned about the axle (22.5 deg apart): covers it, at most 2 % over


def wheel_slabs(b: Body) -> list:
    """A wheel's box (its shortest side along the axle) as WHEEL_SLABS boxes turned about the axle, each the
    disc's full diameter long and R tan(90 deg / WHEEL_SLABS) wide, so together they cover the round tyre (a
    point of the disc is within half the spacing of one slab's long axis) and reach at most
    R / cos(90 deg / WHEEL_SLABS) out (2 % with 8), where the box's corners reach 41 % out."""
    from .obb import OBB

    o = b.obb
    i_ax = int(np.argmin(o.half))
    ax = o.axes[i_ax]
    u, v = [o.axes[i] for i in range(3) if i != i_ax]
    r = float(min(o.half[i] for i in range(3) if i != i_ax))
    w = r * np.tan(np.pi / (2 * WHEEL_SLABS))
    out = []
    for k in range(WHEEL_SLABS):
        a = np.pi * k / WHEEL_SLABS
        d1 = np.cos(a) * u + np.sin(a) * v
        d2 = -np.sin(a) * u + np.cos(a) * v
        out.append(OBB(o.center, np.array([ax, d1, d2]), np.array([o.half[i_ax], r, w]), o.name))
    return out


def wheel_overlap(b: Body, other: Body, slabs: list | None = None) -> float:
    """shape_overlap of a wheel (its round tyre, wheel_slabs) with another body: the largest of the slabs'.
    The box holds the tyre, so a body clear of the box is clear of the tyre (by at least as much): the box's
    overlap is returned then. slabs: wheel_slabs(b), when the caller has them."""
    from dataclasses import replace as _r

    from .model import TOUCH_MM, shape_overlap
    box = shape_overlap(b, other)
    if box <= TOUCH_MM:
        return box
    return max(shape_overlap(_r(b, obb=s, capsule=None), other) for s in (slabs or wheel_slabs(b)))


def has_kinematics(design: dict, sid: str) -> bool:
    s = next(x for x in design["supports"] if x["id"] == sid)
    hp = (s.get("suspension") or {}).get("hardpoints_mm") or {}
    return "damper_lower" in hp and "damper_upper" in hp

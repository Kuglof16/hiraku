"""Contact of the sprung body with the ground (for a rollover).

K2's wheels touch the road through their tyres (sim.Sim._normal). This module lets the sprung body
itself touch the ground: a set of points fixed in the body, each a penalty spring and damper along
the road normal with Coulomb friction across it, compressive only:

    d   = depth of the point below the ground, along the normal   (penetration, m)
    F_n = max(0, k d - c min(d / D_RAMP, 1) v_n)                 (v_n: the point's speed along the normal)
    F_t = -mu F_n v_t / max(|v_t|, v_eps)                         (v_t: its speed across the normal)

v_eps turns Coulomb friction into a steep viscous law below that slip speed (a regularised Coulomb
friction), so a body at rest is held by a force that grows with its creep. On a road.Planes ground
(a tilt table with the floor at its hinge) each plane acts along its own normal, and a point in the
valley between two planes touches both. On any other road the ground is taken as flat under each
point: the normal is vertical, the height is Road.height at the point (exact on the flat road of the
lane change; on a step it ignores the step's face).

The forces are computed from the state at the start of the step, as the tyres' are, and enter the
body's equations through Sim.step(ext=...) (a force in body axes at a point relative to the sprung CG),
so Sim itself is unchanged and a run without contact points is the same run as before.

The points: the roll bar's members (design members whose id starts with rollbar_ or rops_, the
names of hiraku.designgen.ladder_4w) sampled every POINT_SPACING along their length, and the corners of
the convex hull of the body (the frame's nodes and the corners of every sprung part box). A hull corner
that comes from an occupant's or the cargo's box is in group "occupant" / "cargo", so a run says when
a person's box would touch the ground. A wheel lying on its side does not touch through its tyre (the
tyre is a disc on a radial spring), so the outer rim of each wheel's outer face is a ring of body points
too (group "wheel", at the wheel's design position: wheel_face_points); wheel_rim_points gives the
wheels' true rims for the check of how far they went below the ground.

Stiffness and damping per point are HIRAKU's choices (assumptions, no source): they set how far a
point sinks and how hard and short an impact is, so the largest contact force depends on them
(the tool reports the run at two stiffnesses).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from hiraku.solvers.k0_handcalc.rigid import rpy_matrix

from .sim import _r_tuple, _to_body, _to_world

ROLLBAR_PREFIXES = ("rollbar_", "rops_")
POINT_SPACING = 0.2  # m between the points along a roll bar member: HIRAKU's choice
MERGE_RADIUS = 0.05  # m: a hull corner this close to a contact point already taken is left out (HIRAKU's choice)
# per point: 1000 N/mm, 10000 N s/m, regularising slip speed 0.05 m/s (HIRAKU's choices, assumptions, no
# source). At 1000 N/mm a point carrying 10 kN sinks 10 mm; the run reports the deepest sink.
K_CONTACT = 1.0e6
C_CONTACT = 1.0e4
V_EPS = 0.05
# the damping grows from 0 at first touch to c at this depth, so the force starts from 0 instead of jumping
# to c v at touchdown (the form of Hunt and Crossley's contact damping, proportional to the depth; not
# checked against their paper). HIRAKU's choice
D_RAMP = 0.005
WHEEL_FACE_POINTS = 12  # points on each wheel's outer face, on a ring ...
# ... of 0.8 x the wheel radius: the sidewall, inside the tread, so the ring stays off the road while the
# tyre carries the wheel (at the full radius its lowest point sits in the tyre's own deflection). HIRAKU's choice
WHEEL_FACE_RADIUS = 0.8
UNSPRUNG = {"wheel", "tire", "tyre", "hub", "upright", "knuckle", "brake"}


@dataclass(frozen=True)
class ContactPoint:
    name: str
    group: str               # rollbar | frame | part | occupant | cargo | (a test's own)
    design: tuple            # design frame (m)
    part_id: str | None = None
    wheel: str | None = None  # a support id: the point moves with that wheel's centre (its travel)


def rollbar_points(design: dict, spacing: float = POINT_SPACING) -> list[ContactPoint]:
    """Points along the roll bar members (ends included, shared nodes once)."""
    nodes = {n["id"]: np.asarray(n["xyz_mm"], dtype=float) / 1000.0 for n in design.get("nodes") or []}
    out, seen = [], set()
    for mb in design.get("members") or []:
        mid = mb.get("id", "")
        if not mid.startswith(ROLLBAR_PREFIXES) or mb.get("node_a") not in nodes or mb.get("node_b") not in nodes:
            continue
        a, b = nodes[mb["node_a"]], nodes[mb["node_b"]]
        n = max(1, int(math.ceil(float(np.linalg.norm(b - a)) / spacing)))
        for k in range(n + 1):
            p = a + (b - a) * (k / n)
            key = tuple(round(float(x), 4) for x in p)
            if key in seen:
                continue
            seen.add(key)
            out.append(ContactPoint(f"{mid}.{k}", "rollbar", tuple(float(x) for x in p), mid))
    return out


def _box_corners(part: dict) -> np.ndarray:
    box = part["box"]
    c = np.asarray(box["center_mm"], dtype=float) / 1000.0
    h = np.asarray(box["size_mm"], dtype=float) / 2000.0
    rpy = box.get("rpy_deg")
    r = rpy_matrix(rpy) if rpy else np.eye(3)
    signs = np.array([[sx, sy, sz] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)], dtype=float)
    return c + (signs * h) @ r.T


def hull_points(design: dict, exclude: list[ContactPoint] = ()) -> list[ContactPoint]:
    """The corners of the convex hull of the frame nodes and the sprung part boxes' corners (not the
    wheels' parts), without the ones within MERGE_RADIUS of an `exclude` point (one contact, not two
    springs side by side). A part box named like a roll bar member (rollbar..., rops...) is group
    "rollbar"."""
    from scipy.spatial import ConvexHull

    pts, src = [], []
    for n in design.get("nodes") or []:
        pts.append(np.asarray(n["xyz_mm"], dtype=float) / 1000.0)
        src.append(("frame", n["id"]))
    for p in design.get("parts") or []:
        if p.get("kind") in UNSPRUNG or "box" not in p:
            continue
        group = p["kind"] if p.get("kind") in ("occupant", "cargo") else (
            "rollbar" if p["id"].startswith(("rollbar", "rops")) else "part")
        for c in _box_corners(p):
            pts.append(c)
            src.append((group, p["id"]))
    if len(pts) < 4:
        return []
    arr = np.array(pts)
    hull = ConvexHull(arr)
    ex = np.array([e.design for e in exclude]) if exclude else np.zeros((0, 3))
    out = []
    for k in sorted(hull.vertices):
        if len(ex) and float(np.min(np.linalg.norm(ex - arr[k], axis=1))) < MERGE_RADIUS:
            continue
        if any(float(np.linalg.norm(np.array(q.design) - arr[k])) < MERGE_RADIUS for q in out):
            continue
        group, sid = src[k]
        out.append(ContactPoint(f"hull.{len(out)}", group, tuple(float(x) for x in arr[k]),
                                sid if group != "frame" else None))
    return out


def wheel_face_points(design: dict, n: int = WHEEL_FACE_POINTS) -> list[ContactPoint]:
    """Points on a ring (WHEEL_FACE_RADIUS x the radius) on each wheel support's outer face (group
    "wheel"): a wheel lying on its side touches there. In a run the ring follows the wheel centre along
    its suspension travel (not its steer, camber or spin: the ring keeps the body's axes), and its
    force goes into the body at that point, as if the suspension carried it rigidly (an assumption: on
    its side the ground pushes the wheel along its axle, across the travel)."""
    out = []
    for s in design.get("supports") or []:
        if s.get("kind") != "wheel":
            continue
        c = np.asarray(s["position_mm"], dtype=float) / 1000.0
        r = WHEEL_FACE_RADIUS * float(s["radius_mm"]) / 1000.0
        side = 1.0 if c[1] >= 0.0 else -1.0
        y = c[1] + side * float(s.get("width_mm", 0.0)) / 2000.0
        for k in range(n):
            a = 2.0 * math.pi * k / n
            out.append(ContactPoint(f"wheel_{s['id']}.{k}", "wheel",
                                    (float(c[0] + r * math.cos(a)), float(y), float(c[2] + r * math.sin(a))), s["id"],
                                    s["id"]))
    return out


def design_points(design: dict, wheels: bool = True) -> list[ContactPoint]:
    """The roll bar points, the body's hull corners and (wheels) the wheels' outer rims of a design
    (with its occupants on board)."""
    rb = rollbar_points(design)
    return rb + hull_points(design, rb) + (wheel_face_points(design) if wheels else [])


class BodyContact:
    """The ground contact of a set of body points on a Sim's road.

    ext(sim): the contact forces of the sim's present state as Sim.step's ext list; after it,
    self.force[i] (N, world) and self.touch[i] (bool) per point, self.pos[i] (m, world), self.depth[i]
    (m below the road, negative above it)."""

    def __init__(self, sim, points: list[ContactPoint], mu: float, k: float = K_CONTACT, c: float = C_CONTACT,
                 v_eps: float = V_EPS):
        self.sim = sim
        self.points = list(points)
        cg = [float(x) for x in sim.veh.cg_s]
        self.b = [(p.design[0] - cg[0], p.design[1] - cg[1], p.design[2] - cg[2]) for p in self.points]
        # a wheel point: (corner index, its offset from that wheel's design centre)
        ids = {c.id: i for i, c in enumerate(sim.veh.corners)}
        self.wheel = []
        for p in self.points:
            i = ids.get(p.wheel) if p.wheel else None
            if i is None:
                self.wheel.append(None)
                continue
            wc = sim.veh.corners[i].pos
            self.wheel.append((i, p.design[0] - float(wc[0]), p.design[1] - float(wc[1]), p.design[2] - float(wc[2])))
        self.mu, self.k, self.c, self.v_eps = float(mu), float(k), float(c), float(v_eps)
        n = len(self.points)
        self.force = [(0.0, 0.0, 0.0)] * n
        self.touch = [False] * n
        self.pos = [(0.0, 0.0, 0.0)] * n
        self.depth = [0.0] * n

    def ext(self, sim=None) -> list:
        sim = sim or self.sim
        rot = _r_tuple(sim.q)
        px, py, pz = sim.p
        vx, vy, vz = sim.v
        wx, wy, wz = _to_world(rot, *sim.w)
        road = sim.road
        planes_road = hasattr(road, "planes")
        out = []
        k, c, mu, ve = self.k, self.c, self.mu, self.v_eps
        centres = {}
        for i, (bx, by, bz) in enumerate(self.b):
            wh = self.wheel[i]
            if wh is not None:
                j = wh[0]
                if j not in centres:
                    centres[j] = sim._path(j, sim.s[j])[:3]  # wheel centre, body axes from the sprung CG
                cx, cy, cz = centres[j]
                bx, by, bz = cx + wh[1], cy + wh[2], cz + wh[3]
            rx, ry, rz = _to_world(rot, bx, by, bz)
            x, y, z = px + rx, py + ry, pz + rz
            self.pos[i] = (x, y, z)
            # the planes under the point: a Planes road's own, else the road's height with a vertical normal
            planes = road.planes if planes_road else (((x, y, road.height(x, y)), (0.0, 0.0, 1.0)),)
            ux = vx + wy * rz - wz * ry
            uy = vy + wz * rx - wx * rz
            uz = vz + wx * ry - wy * rx
            fx = fy = fz = 0.0
            dmax = -math.inf
            touch = False
            for (qx, qy, qz), (nx, ny, nz) in planes:
                d = (qx - x) * nx + (qy - y) * ny + (qz - z) * nz  # depth below the plane, along its normal
                dmax = d if d > dmax else dmax
                if d <= 0.0:
                    continue
                touch = True
                un = ux * nx + uy * ny + uz * nz
                fn = k * d - c * (d / D_RAMP if d < D_RAMP else 1.0) * un
                if fn <= 0.0:
                    continue
                tx, ty, tz = ux - un * nx, uy - un * ny, uz - un * nz
                vt = math.sqrt(tx * tx + ty * ty + tz * tz)
                s = mu * fn / (vt if vt > ve else ve)
                fx += fn * nx - s * tx
                fy += fn * ny - s * ty
                fz += fn * nz - s * tz
            self.depth[i] = dmax
            self.touch[i] = touch
            self.force[i] = (fx, fy, fz)
            if fx != 0.0 or fy != 0.0 or fz != 0.0:
                out.append((_to_body(rot, fx, fy, fz), (bx, by, bz)))
        return out

    def total(self) -> tuple:
        return tuple(sum(f[a] for f in self.force) for a in range(3))


def wheel_rim_points(sim, n: int = 12) -> list:
    """World points on the outer rim of every wheel (n per wheel, at the tyre's outer face): for the
    check that no wheel went through the ground while the body lay on its side."""
    r = sim.rotation()
    out = []
    centres = sim.wheel_centres()
    for i, c in enumerate(sim.veh.corners):
        side = 1.0 if c.side >= 0 else -1.0
        ay = r[:, 1] * side
        ax, az = r[:, 0], r[:, 2]
        face = centres[i] + ay * (c.width / 2.0)
        for k in range(n):
            a = 2.0 * math.pi * k / n
            out.append((c.id, face + c.radius * (math.cos(a) * ax + math.sin(a) * az)))
    return out

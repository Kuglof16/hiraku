"""Capsules (a segment with a radius) for long thin parts, in numpy.

The overlaps follow obb.overlap: positive is how far they go into each other,
negative is minus the gap.

- capsule and capsule: the sum of the radii minus the shortest distance between the segments
- capsule and box: the radius minus the signed distance from the segment to the box (the
  distance when the segment stays outside, minus the depth of its deepest point inside when it
  goes in)
- a capsule slid along a direction (swept_overlap): the segment sweeps a parallelogram; the
  radius (plus the other capsule's) minus the distance from that parallelogram to the other
  body. When they cross, the distance is 0 and the overlap is the radius, not a depth.

All distances are exact for segments, boxes and parallelograms (closest features, no sampling).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .obb import OBB

EPS = 1e-12


@dataclass
class Capsule:
    a: np.ndarray  # one end of the axis, mm
    b: np.ndarray  # the other end, mm
    r: float  # radius, mm
    name: str = ""

    def moved(self, offset) -> "Capsule":
        o = np.asarray(offset, float)
        return Capsule(self.a + o, self.b + o, self.r, self.name)

    def turned(self, R: np.ndarray, about) -> "Capsule":
        p = np.asarray(about, float)
        return Capsule(p + R @ (self.a - p), p + R @ (self.b - p), self.r, self.name)


def from_obb(obb: OBB) -> Capsule:
    """The capsule of a part's box: its axis along the box's longest side, its radius half the
    larger of the other two sides, as long overall as the box (the axis ends are a radius in from
    the box's end faces; a box no longer than it is wide gives a ball)."""
    k = int(np.argmax(obb.half))
    r = float(max(obb.half[i] for i in range(3) if i != k))
    h = max(float(obb.half[k]) - r, 0.0)
    return Capsule(obb.center - obb.axes[k] * h, obb.center + obb.axes[k] * h, r, obb.name)


def from_points(p, q, radius: float, name: str = "") -> Capsule:
    """A capsule whose axis runs between two known points (e.g. a link's two joints)."""
    return Capsule(np.asarray(p, float), np.asarray(q, float), float(radius), name)


def seg_seg(p1, q1, p2, q2) -> np.ndarray:
    """Shortest distance between segments p1-q1 and p2-q2 (arrays of points broadcast along the
    leading axes): the closest parameters s, t in [0, 1] of the two lines, clamped one after
    the other."""
    p1, q1, p2, q2 = (np.asarray(v, float) for v in (p1, q1, p2, q2))
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a = np.sum(d1 * d1, axis=-1)
    e = np.sum(d2 * d2, axis=-1)
    b = np.sum(d1 * d2, axis=-1)
    c = np.sum(d1 * r, axis=-1)
    f = np.sum(d2 * r, axis=-1)
    a_ok, e_ok = a > EPS, e > EPS
    denom = a * e - b * b
    with np.errstate(divide="ignore", invalid="ignore"):
        s = np.where(a_ok & e_ok & (denom > EPS), np.clip((b * f - c * e) / np.where(denom > EPS, denom, 1.0), 0, 1), 0.0)
        t = np.where(e_ok, (b * s + f) / np.where(e_ok, e, 1.0), 0.0)
        # t outside [0, 1]: clamp it and take the closest s for that t
        s = np.where(t < 0, np.where(a_ok, np.clip(-c / np.where(a_ok, a, 1.0), 0, 1), 0.0), s)
        s = np.where(t > 1, np.where(a_ok, np.clip((b - c) / np.where(a_ok, a, 1.0), 0, 1), 0.0), s)
        t = np.clip(t, 0, 1)
        # p1 a point (a = 0): only t matters; p2 a point (e = 0): only s matters
        t = np.where(~a_ok & e_ok, np.clip(f / np.where(e_ok, e, 1.0), 0, 1), t)
        s = np.where(a_ok & ~e_ok, np.clip(-c / np.where(a_ok, a, 1.0), 0, 1), s)
    gap = (p1 + d1 * s[..., None]) - (p2 + d2 * t[..., None])
    return np.sqrt(np.sum(gap * gap, axis=-1))


def _local(obb: OBB, pts) -> np.ndarray:
    return (np.asarray(pts, float) - obb.center) @ obb.axes.T


def _box_sdf(x: np.ndarray, half: np.ndarray) -> np.ndarray:
    """Signed distance of points (box frame) to the box: + outside, - inside."""
    q = np.abs(x) - half
    out = np.sqrt(np.sum(np.maximum(q, 0.0) ** 2, axis=-1))
    return out + np.minimum(np.max(q, axis=-1), 0.0)


def _box_edges(obb: OBB) -> tuple[np.ndarray, np.ndarray]:
    """The 12 edges of a box as (starts, ends), world frame."""
    c = obb.corners()  # index = 4 i + 2 j + k for signs (i, j, k) of the three axes
    pairs = [(n, n | bit) for bit in (4, 2, 1) for n in range(8) if not n & bit]
    return c[[p for p, _ in pairs]], c[[q for _, q in pairs]]


def _seg_in_box(p: np.ndarray, q: np.ndarray, half: np.ndarray) -> tuple[float, float] | None:
    """Parameter interval [t0, t1] of segment p-q (box frame) inside the box, or None (slabs)."""
    d = q - p
    t0, t1 = 0.0, 1.0
    for i in range(3):
        if abs(d[i]) < EPS:
            if abs(p[i]) > half[i]:
                return None
            continue
        u, v = (-half[i] - p[i]) / d[i], (half[i] - p[i]) / d[i]
        t0, t1 = max(t0, min(u, v)), min(t1, max(u, v))
        if t0 > t1:
            return None
    return t0, t1


def seg_box(p, q, obb: OBB) -> float:
    """Signed distance from segment p-q to the box: the gap when it stays outside, minus the
    depth of its deepest point when it goes in."""
    lp, lq = _local(obb, p), _local(obb, q)
    inside = _seg_in_box(lp, lq, obb.half)
    if inside is not None:
        # inside, the signed distance is max_i(|x_i| - h_i), piecewise linear in t: its smallest
        # value is at an end of the inside part, where an x_i is 0, or where two pieces meet
        t0, t1 = inside
        d = lq - lp
        ts = [t0, t1]
        for i in range(3):
            if abs(d[i]) > EPS:
                ts.append(-lp[i] / d[i])
            for j in range(i + 1, 3):
                for si in (-1, 1):
                    for sj in (-1, 1):
                        den = si * d[i] - sj * d[j]
                        if abs(den) > EPS:
                            ts.append((sj * lp[j] - obb.half[j] - si * lp[i] + obb.half[i]) / den)
        t = np.clip(np.array(ts), t0, t1)
        return float(_box_sdf(lp + t[:, None] * d, obb.half).min())
    ends = _box_sdf(np.vstack([lp, lq]), obb.half).min()
    e0, e1 = _box_edges(obb)
    return float(min(ends, seg_seg(np.asarray(p, float), np.asarray(q, float), e0, e1).min()))


def capsule_overlap(a: Capsule, b: Capsule) -> float:
    return a.r + b.r - float(seg_seg(a.a, a.b, b.a, b.b))


def capsule_box_overlap(c: Capsule, obb: OBB) -> float:
    return c.r - seg_box(c.a, c.b, obb)


def _point_in_face(pts: np.ndarray, o: np.ndarray, e1: np.ndarray, e2: np.ndarray) -> np.ndarray:
    """Distance from points to the parallelogram o + s e1 + t e2 (s, t in [0, 1]) where the foot
    of the perpendicular falls inside it, inf elsewhere (the edges cover the rest)."""
    n = np.cross(e1, e2)
    nn = float(n @ n)
    if nn < EPS:
        return np.full(len(pts), np.inf)
    w = pts - o
    s = (np.cross(w, e2) @ n) / nn
    t = (np.cross(e1, w) @ n) / nn
    inside = (s >= 0) & (s <= 1) & (t >= 0) & (t <= 1)
    return np.where(inside, np.abs(w @ n) / np.sqrt(nn), np.inf)


def _crosses_face(p: np.ndarray, q: np.ndarray, o: np.ndarray, e1: np.ndarray, e2: np.ndarray) -> bool:
    """Whether any of the segments p-q pass through the parallelogram's inside."""
    n = np.cross(e1, e2)
    nn = float(n @ n)
    if nn < EPS:
        return False
    sp, sq = (p - o) @ n, (q - o) @ n
    cut = sp * sq < 0
    if not cut.any():
        return False
    k = sp[cut] / (sp[cut] - sq[cut])
    x = p[cut] + (q[cut] - p[cut]) * k[:, None] - o
    s = (np.cross(x, e2) @ n) / nn
    t = (np.cross(e1, x) @ n) / nn
    return bool(np.any((s >= 0) & (s <= 1) & (t >= 0) & (t <= 1)))


def swept_distance(c: Capsule, direction, travel: float, other: OBB | Capsule) -> float:
    """Distance (>= 0) from the parallelogram the axis of c sweeps sliding `travel` along
    `direction` to the other body's box, or to the other capsule's axis."""
    d = np.asarray(direction, float) * travel
    o, e1 = c.a, c.b - c.a
    n = np.cross(e1, d)
    if float(n @ n) < EPS * max(float(e1 @ e1), 1.0) * max(float(d @ d), 1.0):  # axis along the slide (or a ball)
        pts = np.vstack([c.a, c.b, c.a + d, c.b + d])
        span = pts @ (d / np.linalg.norm(d))
        lo, hi = pts[int(np.argmin(span))], pts[int(np.argmax(span))]
        if isinstance(other, Capsule):
            return float(seg_seg(lo, hi, other.a, other.b))
        return max(seg_box(lo, hi, other), 0.0)
    starts = np.vstack([o, o + e1, o + e1 + d, o + d])
    ends = np.vstack([o + e1, o + e1 + d, o + d, o])
    if isinstance(other, Capsule):
        if _crosses_face(other.a[None], other.b[None], o, e1, d):
            return 0.0
        rim = float(seg_seg(starts, ends, other.a, other.b).min())
        return float(min(rim, _point_in_face(np.vstack([other.a, other.b]), o, e1, d).min()))
    e0, e1b = _box_edges(other)
    if _crosses_face(e0, e1b, o, e1, d):
        return 0.0
    rim = min(seg_box(s, e, other) for s, e in zip(starts, ends))
    if rim <= 0.0:
        return 0.0
    return float(min(rim, _point_in_face(other.corners(), o, e1, d).min()))


def swept_overlap(c: Capsule, direction, travel: float, other: OBB | Capsule) -> float:
    r = c.r + (other.r if isinstance(other, Capsule) else 0.0)
    return r - swept_distance(c, direction, travel, other)

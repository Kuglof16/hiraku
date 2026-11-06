"""Streamlines of a saved mean flow field.

Input: a case folder's fields.npz (vehicle.VehicleRun.save): dr, ux, uy, uz as float32 in lattice units, C-order
[z, y, x], NaN in solid nodes; dx and origin in metres, node i at origin + (i + 1/2) dx. Physical velocity =
u * air_speed_ms / u_lattice, Cp = dr / 3 / (0.5 u_lattice^2). Everything here is in metres and m/s on the grid axes
(air along +x, z up).

Interpolation is trilinear on the nodes. Next to a solid node the weights are shared among the fluid corners, and a
point with less than half of its weight on fluid nodes counts as being in the solid. The integration is classical RK4
with a step of fixed length: dt = h / |u| at the start of each step, the same for all four stages of that step.

A line ends where a stage or the next point falls in a solid node or outside the nodes, where the air is slower than
min_speed_ms, or at max_length_m. The solver also stops air at shapes thinner than the grid, where no node is solid:
ShapeStop ends a line at those too. Screens (reflect < 1) let air through and do not stop a line.

    python -m hiraku.cfd.streamlines <case folder> --seeds upstream --n 24 16 --out lines.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

STOP_LENGTH, STOP_SOLID, STOP_DOMAIN, STOP_SLOW = 0, 1, 2, 3
STOP_NAMES = {STOP_LENGTH: "length", STOP_SOLID: "solid", STOP_DOMAIN: "domain", STOP_SLOW: "slow"}
SEED_KINDS = ("upstream", "front", "beside", "above", "wake")
JSON_FORMAT = "hiraku-streamlines-1"
JSON_SCALE = {"xyz_m": 1e-3, "speed_ms": 1e-2, "cp": 1e-3, "time_s": 1e-3}  # stored value = round(value / scale)


class MeanField:
    """A mean flow field on the grid's nodes."""

    def __init__(self, ux: np.ndarray, uy: np.ndarray, uz: np.ndarray, dr: np.ndarray, dx: float, origin: Any, u_lattice: float, air_speed_ms: float,
                 rho: float = 1.225, dt: float | None = None):
        self.shape = tuple(ux.shape)  # (nz, ny, nx)
        self.dx = float(dx)
        self.origin = np.asarray(origin, float)
        self.u_lattice = float(u_lattice)
        self.air_speed_ms = float(air_speed_ms)
        self.rho = float(rho)
        self.dt = None if dt is None else float(dt)
        self.fluid = ~(np.isnan(ux) | np.isnan(uy) | np.isnan(uz) | np.isnan(dr))
        # flat copies, 0 in solid nodes: the weights of solid corners are 0, so a NaN can never reach a result
        self._u = [np.nan_to_num(a, nan=0.0).ravel() for a in (ux, uy, uz)]
        self._dr = np.nan_to_num(dr, nan=0.0).ravel()
        self._fluid = self.fluid.ravel()
        nz, ny, nx = self.shape
        self._last = np.array([nx - 1, ny - 1, nz - 1])
        self._stride = np.array([1, nx, nx * ny])
        self.to_ms = self.air_speed_ms / self.u_lattice
        self.to_cp = 1.0 / 3.0 / (0.5 * self.u_lattice * self.u_lattice)
        self.lo = self.origin + 0.5 * self.dx
        self.hi = self.origin + (self._last + 0.5) * self.dx

    @classmethod
    def from_case(cls, folder: Path) -> "MeanField":
        with np.load(Path(folder) / "fields.npz") as d:
            return cls(d["ux"], d["uy"], d["uz"], d["dr"], float(d["dx"]), d["origin"], float(d["u_lattice"]), float(d["air_speed_ms"]), float(d["rho"]), float(d["dt"]))

    def node(self, i: int, j: int, k: int) -> np.ndarray:
        """Where node (i along x, j along y, k along z) is, metres."""
        return self.origin + (np.array([i, j, k]) + 0.5) * self.dx

    def solid_box(self) -> tuple[np.ndarray, np.ndarray]:
        """Bounds (metres) of the solid nodes, to their cells' faces."""
        if self.fluid.all():
            raise ValueError("the field has no solid nodes")
        k, j, i = np.nonzero(~self.fluid)
        ijk = np.column_stack([i, j, k])
        return self.origin + ijk.min(axis=0) * self.dx, self.origin + (ijk.max(axis=0) + 1) * self.dx

    def sample(self, p: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """At points (n, 3): velocity (n, 3) in m/s, Cp (n), whether the point is in the air (n), whether it is outside
        the nodes (n). Velocity and Cp of a point outside or with no fluid corner are 0."""
        q = (p - self.origin) / self.dx - 0.5
        out = ((q < -1e-9) | (q > self._last + 1e-9)).any(axis=1)  # a point on the last node stays in, whatever the rounding
        q = np.clip(q, 0.0, self._last)
        i0 = np.minimum(q.astype(np.int64), np.maximum(self._last - 1, 0))
        f = q - i0
        base = i0 @ self._stride
        sx, sy, sz = self._stride
        fx, fy, fz = f[:, 0], f[:, 1], f[:, 2]
        gx, gy, gz = 1.0 - fx, 1.0 - fy, 1.0 - fz
        corners = np.stack([base, base + sx, base + sy, base + sx + sy, base + sz, base + sx + sz, base + sy + sz, base + sx + sy + sz])
        weights = np.stack([gx * gy * gz, fx * gy * gz, gx * fy * gz, fx * fy * gz, gx * gy * fz, fx * gy * fz, gx * fy * fz, fx * fy * fz])
        weights = weights * self._fluid[corners]
        share = weights.sum(axis=0)
        ok = (share >= 0.5) & ~out
        scale = np.where(share > 0.0, 1.0 / np.where(share > 0.0, share, 1.0), 0.0)
        scale = np.where(out, 0.0, scale)
        u = np.column_stack([(weights * a[corners]).sum(axis=0) * scale for a in self._u]) * self.to_ms
        cp = (weights * self._dr[corners]).sum(axis=0) * scale * self.to_cp
        return u, cp, ok, out


def _meets_polytope(s: Any, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Whether each segment a[i]-b[i] meets the convex solid {x: normals x <= offsets}."""
    da = a @ s.normals.T - s.offsets
    db = b @ s.normals.T - s.offsets
    outside = ((da > 0.0) & (db > 0.0)).any(axis=1)
    den = da - db
    safe = np.where(np.abs(den) > 0.0, den, 1.0)
    t = da / safe
    enter = np.where((da > 0.0) & (db <= 0.0), t, 0.0).max(axis=1)
    leave = np.where((da <= 0.0) & (db > 0.0), t, 1.0).min(axis=1)
    return ~outside & (enter <= leave)


def _meets_cylinder(c: Any, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Whether each segment a[i]-b[i] meets the solid cylinder c (center, axis, radius, half)."""
    d = b - a
    r = a - c.center
    s0, ds = r @ c.axis, d @ c.axis
    parallel = np.abs(ds) < 1e-14
    safe = np.where(parallel, 1.0, ds)
    t1, t2 = (-c.half - s0) / safe, (c.half - s0) / safe
    within = np.abs(s0) <= c.half
    lo = np.where(parallel, np.where(within, -np.inf, np.inf), np.minimum(t1, t2))
    hi = np.where(parallel, np.where(within, np.inf, -np.inf), np.maximum(t1, t2))
    rp = r - s0[:, None] * c.axis
    dp = d - ds[:, None] * c.axis
    qa, qb, qc = (dp * dp).sum(axis=1), (rp * dp).sum(axis=1), (rp * rp).sum(axis=1) - c.radius ** 2
    along = qa < 1e-20  # the segment runs along the axis: in or out of the round side as a whole
    disc = qb * qb - qa * qc
    root = np.sqrt(np.maximum(disc, 0.0))
    sa = np.where(along, 1.0, qa)
    side_lo = np.where(along, np.where(qc <= 0.0, -np.inf, np.inf), np.where(disc >= 0.0, (-qb - root) / sa, np.inf))
    side_hi = np.where(along, np.where(qc <= 0.0, np.inf, -np.inf), np.where(disc >= 0.0, (-qb + root) / sa, -np.inf))
    lo, hi = np.maximum(lo, side_lo), np.minimum(hi, side_hi)
    return (lo <= hi) & (hi >= 0.0) & (lo <= 1.0)


class ShapeStop:
    """stop(a, b): whether each segment meets a solid shape (reflect >= 1): boxes, prisms and cylinders exactly, any other
    shape at five points along the segment."""

    def __init__(self, shapes: Sequence[Any]):
        self.shapes = [s for s in shapes if s.reflect >= 1.0]
        bounds = [s.bounds() for s in self.shapes]
        self.lo = np.array([b[0] for b in bounds])
        self.hi = np.array([b[1] for b in bounds])
        self.box_lo, self.box_hi = self.lo.min(axis=0), self.hi.max(axis=0)

    def __call__(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        hit = np.zeros(len(a), bool)
        seg_lo, seg_hi = np.minimum(a, b), np.maximum(a, b)
        near = np.nonzero(((seg_hi >= self.box_lo) & (seg_lo <= self.box_hi)).all(axis=1))[0]
        if not near.size:
            return hit
        over = ((seg_lo[near, None, :] <= self.hi[None]) & (seg_hi[near, None, :] >= self.lo[None])).all(axis=2)
        for n in np.nonzero(over.any(axis=0))[0]:
            s = self.shapes[n]
            at = near[over[:, n]]
            if hasattr(s, "normals"):
                hit[at] |= _meets_polytope(s, a[at], b[at])
            elif hasattr(s, "axis"):
                hit[at] |= _meets_cylinder(s, a[at], b[at])
            else:
                for w in (0.0, 0.25, 0.5, 0.75, 1.0):
                    hit[at] |= s.inside(a[at] + w * (b[at] - a[at]))
        return hit


@dataclass
class Lines:
    """Polylines stored one after another: line n is points[offsets[n]:offsets[n + 1]]. time is the travel time from the
    line's first point (s), growing with the air's direction. seed is the seed each line came from, stop why it ended
    downstream (STOP_NAMES)."""

    points: np.ndarray
    speed: np.ndarray
    cp: np.ndarray
    time: np.ndarray
    offsets: np.ndarray
    seed: np.ndarray
    stop: np.ndarray

    def __len__(self) -> int:
        return len(self.offsets) - 1

    def line(self, n: int) -> dict[str, np.ndarray]:
        a, b = self.offsets[n], self.offsets[n + 1]
        return {"points": self.points[a:b], "speed": self.speed[a:b], "cp": self.cp[a:b], "time": self.time[a:b]}

    def select(self, keep: np.ndarray) -> "Lines":
        """The lines with keep[n] true."""
        keep = np.asarray(keep, bool)
        counts = np.diff(self.offsets)
        mask = np.repeat(keep, counts)
        return Lines(self.points[mask], self.speed[mask], self.cp[mask], self.time[mask], np.concatenate([[0], np.cumsum(counts[keep])]), self.seed[keep], self.stop[keep])

    def thinned(self, every: int) -> "Lines":
        """Every n-th point of each line, and always its last."""
        if every <= 1:
            return self
        counts = np.diff(self.offsets)
        at = np.arange(len(self.points)) - np.repeat(self.offsets[:-1], counts)
        last = np.repeat(counts - 1, counts)
        mask = (at % every == 0) | (at == last)
        kept = np.add.reduceat(mask.astype(np.int64), self.offsets[:-1]) if len(self) else np.zeros(0, np.int64)
        return Lines(self.points[mask], self.speed[mask], self.cp[mask], self.time[mask], np.concatenate([[0], np.cumsum(kept)]), self.seed, self.stop)

    def stop_counts(self) -> dict[str, int]:
        return {name: int((self.stop == code).sum()) for code, name in STOP_NAMES.items()}


def _march(field: MeanField, seeds: np.ndarray, h: float, steps: int, sign: float, slowest: float, stop: Callable[[np.ndarray, np.ndarray], np.ndarray] | None,
           region: tuple[np.ndarray, np.ndarray] | None) -> tuple[list[tuple[np.ndarray, ...]], np.ndarray]:
    """RK4 from each seed. Returns the records of each step (seed numbers, points, speed, Cp, time) and why each ended."""
    n = len(seeds)
    reason = np.full(n, STOP_LENGTH, np.int8)

    def in_region(p: np.ndarray) -> np.ndarray:
        return np.ones(len(p), bool) if region is None else ((p >= region[0]) & (p <= region[1])).all(axis=1)

    x = seeds.astype(float).copy()
    u, cp, ok, out = field.sample(x)
    inside = in_region(x)
    reason[out | ~inside] = STOP_DOMAIN
    reason[~out & inside & ~ok] = STOP_SOLID
    keep = ok & inside
    idx = np.nonzero(keep)[0]
    x, u, cp = x[keep], u[keep], cp[keep]
    t = np.zeros(len(idx))
    records: list[tuple[np.ndarray, ...]] = []
    for step in range(steps + 1):
        if not idx.size:
            break
        speed = np.linalg.norm(u, axis=1)
        records.append((idx, x, speed, cp, t))
        if step == steps:
            break
        moving = speed >= slowest
        reason[idx[~moving]] = STOP_SLOW
        idx, x, u, cp, t, speed = idx[moving], x[moving], u[moving], cp[moving], t[moving], speed[moving]
        if not idx.size:
            break
        dt = sign * h / speed
        d = dt[:, None]
        k2, _, ok2, out2 = field.sample(x + 0.5 * d * u)
        k3, _, ok3, out3 = field.sample(x + 0.5 * d * k2)
        k4, _, ok4, out4 = field.sample(x + d * k3)
        xn = x + d / 6.0 * (u + 2.0 * k2 + 2.0 * k3 + k4)
        un, cpn, okn, outn = field.sample(xn)
        left = out2 | out3 | out4 | outn | ~in_region(xn)
        good = ok2 & ok3 & ok4 & okn & ~left
        if stop is not None and good.any():
            good[good] = ~stop(x[good], xn[good])
        reason[idx[~good]] = np.where(left[~good], STOP_DOMAIN, STOP_SOLID)
        idx, x, u, cp, t = idx[good], xn[good], un[good], cpn[good], (t + dt)[good]
    return records, reason


def _per_seed(records: list[tuple[np.ndarray, ...]], n: int) -> tuple[np.ndarray, ...]:
    """The records of the steps as (points, speed, Cp, time, offsets), in seed order and step order within a seed."""
    if not records:
        return np.zeros((0, 3)), np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(n + 1, np.int64)
    ids = np.concatenate([r[0] for r in records])
    order = np.argsort(ids, kind="stable")
    arrays = [np.concatenate([r[k] for r in records])[order] for k in (1, 2, 3, 4)]
    offsets = np.concatenate([[0], np.cumsum(np.bincount(ids, minlength=n))])
    return (*arrays, offsets)


def trace(field: MeanField, seeds: np.ndarray, step_m: float | None = None, max_length_m: float = 20.0, direction: str = "forward", min_speed_ms: float | None = None,
          stop: Callable[[np.ndarray, np.ndarray], np.ndarray] | None = None, region: tuple[Any, Any] | None = None) -> Lines:
    """Streamlines from seeds (n, 3), step_m long steps (default a quarter of the grid). direction: forward (with the
    air), backward, or both (one line through the seed). min_speed_ms: default 1 % of the air speed. stop: ShapeStop or
    the like. region: (lo, hi), lines also end at this box. A seed in a solid or outside gives no line."""
    if direction not in ("forward", "backward", "both"):
        raise ValueError(f"direction: {direction}")
    seeds = np.asarray(seeds, float).reshape(-1, 3)
    h = field.dx / 4.0 if step_m is None else float(step_m)
    if h <= 0.0:
        raise ValueError("step_m must be positive")
    steps = int(np.ceil(max_length_m / h))
    slowest = 0.01 * field.air_speed_ms if min_speed_ms is None else float(min_speed_ms)
    box = None if region is None else (np.asarray(region[0], float), np.asarray(region[1], float))
    n = len(seeds)
    points, speed, cp, time, offsets = [], [], [], [], [0]
    seed_kept, stop_kept = [], []
    fwd = bwd = None
    if direction in ("forward", "both"):
        rec, why_f = _march(field, seeds, h, steps, 1.0, slowest, stop, box)
        fwd = _per_seed(rec, n)
    if direction in ("backward", "both"):
        rec, why_b = _march(field, seeds, h, steps, -1.0, slowest, stop, box)
        bwd = _per_seed(rec, n)
    for s in range(n):
        parts = []
        if bwd is not None and bwd[4][s + 1] > bwd[4][s]:
            a, b = bwd[4][s], bwd[4][s + 1]
            end = a if fwd is not None else a - 1  # the seed is a's point; with a forward part it comes from there
            parts.append(tuple(arr[b - 1:(end if end >= 0 else None):-1] for arr in bwd[:4]))
        if fwd is not None and fwd[4][s + 1] > fwd[4][s]:
            a, b = fwd[4][s], fwd[4][s + 1]
            parts.append(tuple(arr[a:b] for arr in fwd[:4]))
        count = sum(len(p[0]) for p in parts)
        if count < 2:
            continue
        pts, spd, c, t = (np.concatenate([p[k] for p in parts]) for k in range(4))
        points.append(pts)
        speed.append(spd)
        cp.append(c)
        time.append(t - t[0])
        offsets.append(offsets[-1] + count)
        seed_kept.append(s)
        # the downstream end of a backward line is its seed
        stop_kept.append(why_f[s] if fwd is not None else STOP_LENGTH)
    if not points:
        return Lines(np.zeros((0, 3)), np.zeros(0), np.zeros(0), np.zeros(0), np.zeros(1, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int8))
    return Lines(np.concatenate(points), np.concatenate(speed), np.concatenate(cp), np.concatenate(time), np.array(offsets, np.int64), np.array(seed_kept, np.int64), np.array(stop_kept, np.int8))


def plane_seeds(corner: Any, e1: Any, e2: Any, n1: int, n2: int) -> np.ndarray:
    """n1 x n2 seeds in the middles of a grid on the parallelogram corner + a e1 + b e2 (0 <= a, b <= 1)."""
    corner, e1, e2 = (np.asarray(v, float) for v in (corner, e1, e2))
    a, b = np.meshgrid((np.arange(n1) + 0.5) / n1, (np.arange(n2) + 0.5) / n2, indexing="ij")
    return corner + a.ravel()[:, None] * e1 + b.ravel()[:, None] * e2


def named_seeds(kind: str, lo: Any, hi: Any, n: tuple[int, int] = (24, 16), margin: float = 0.3, upstream: float = 2.0, floor: float = 0.0) -> np.ndarray:
    """Seeds on a named plane around the vehicle's box (lo, hi), metres on the grid axes.
    upstream: square to the air, `upstream` metres ahead of the vehicle, covering its width and height with `margin`.
    front / wake: the same plane just ahead of / behind the vehicle. beside: along the vehicle, `margin` off its y+ side.
    above: horizontal, `margin` over it. The planes do not reach below z = floor."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    if kind not in SEED_KINDS:
        raise ValueError(f"seed plane {kind}: not one of {SEED_KINDS}")
    y0, y1 = lo[1] - margin, hi[1] + margin
    z0, z1 = max(lo[2] - margin, floor), hi[2] + margin
    x0, x1 = lo[0] - margin, hi[0] + margin
    if kind in ("upstream", "front", "wake"):
        x = {"upstream": lo[0] - upstream, "front": lo[0] - 0.5 * margin, "wake": hi[0] + 0.5 * margin}[kind]
        return plane_seeds((x, y0, z0), (0.0, y1 - y0, 0.0), (0.0, 0.0, z1 - z0), n[0], n[1])
    if kind == "beside":
        return plane_seeds((x0, hi[1] + margin, z0), (x1 - x0, 0.0, 0.0), (0.0, 0.0, z1 - z0), n[0], n[1])
    return plane_seeds((x0, y0, hi[2] + margin), (x1 - x0, 0.0, 0.0), (0.0, y1 - y0, 0.0), n[0], n[1])


def seed_direction(kind: str) -> str:
    """The upstream plane's lines start there; those of a plane near the vehicle run both ways through it."""
    return "forward" if kind == "upstream" else "both"


def to_compact(lines: Lines, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Integers in the steps of JSON_SCALE; x, y, z as differences along each line after its first point."""
    pts = np.rint(lines.points / JSON_SCALE["xyz_m"]).astype(np.int64)
    diff = pts.copy()
    first = np.zeros(len(pts), bool)
    first[lines.offsets[:-1][np.diff(lines.offsets) > 0]] = True
    diff[1:][~first[1:]] = pts[1:][~first[1:]] - pts[:-1][~first[1:]]
    return {"format": JSON_FORMAT, "scale": JSON_SCALE, "meta": meta or {}, "offsets": lines.offsets.tolist(), "seed": lines.seed.tolist(), "stop": [STOP_NAMES[int(c)] for c in lines.stop],
            "xyz": diff.ravel().tolist(), "speed": np.rint(lines.speed / JSON_SCALE["speed_ms"]).astype(np.int64).tolist(),
            "cp": np.rint(lines.cp / JSON_SCALE["cp"]).astype(np.int64).tolist(), "time": np.rint(lines.time / JSON_SCALE["time_s"]).astype(np.int64).tolist()}


def from_compact(obj: dict[str, Any]) -> Lines:
    if obj.get("format") != JSON_FORMAT:
        raise ValueError(f"not a streamlines file: {obj.get('format')}")
    s = obj["scale"]
    offsets = np.array(obj["offsets"], np.int64)
    pts = np.array(obj["xyz"], np.int64).reshape(-1, 3)
    for a, b in zip(offsets[:-1], offsets[1:]):
        pts[a:b] = np.cumsum(pts[a:b], axis=0)
    names = {v: k for k, v in STOP_NAMES.items()}
    return Lines(pts * s["xyz_m"], np.array(obj["speed"], float) * s["speed_ms"], np.array(obj["cp"], float) * s["cp"], np.array(obj["time"], float) * s["time_s"], offsets,
                 np.array(obj["seed"], np.int64), np.array([names[c] for c in obj["stop"]], np.int8))


def write_json(path: Path, lines: Lines, meta: dict[str, Any] | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_compact(lines, meta), ensure_ascii=True, separators=(",", ":")), encoding="ascii")
    return path


def read_json(path: Path) -> Lines:
    return from_compact(json.loads(Path(path).read_text(encoding="ascii")))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m hiraku.cfd.streamlines", description="Streamlines of a case's fields.npz as compact JSON")
    ap.add_argument("case", type=Path)
    ap.add_argument("--seeds", choices=SEED_KINDS, default="upstream")
    ap.add_argument("--n", type=int, nargs=2, default=(24, 16))
    ap.add_argument("--step", type=float, help="step length, m (default a quarter of the grid)")
    ap.add_argument("--length", type=float, default=20.0, help="longest line, m")
    ap.add_argument("--every", type=int, default=1, help="keep every n-th point")
    ap.add_argument("--design", type=Path, help="the design's JSON: lines also end at its solid shapes, and the planes follow its box")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    field = MeanField.from_case(a.case)
    summary = json.loads((a.case / "case.json").read_text(encoding="utf-8"))
    stop = None
    if a.design:
        from hiraku.cfd.vehicle import design_shapes

        shapes, _ = design_shapes(json.loads(a.design.read_text(encoding="utf-8")), summary["yaw_deg"], summary["speed_ms"], dx=summary["dx"])
        stop = ShapeStop(shapes)
        lo, hi = stop.box_lo, stop.box_hi
    else:
        lo, hi = field.solid_box()
    seeds = named_seeds(a.seeds, lo, hi, tuple(a.n), floor=field.lo[2])
    lines = trace(field, seeds, a.step, a.length, seed_direction(a.seeds), stop=stop).thinned(a.every)
    path = write_json(a.out, lines, {"yaw_deg": summary["yaw_deg"], "speed_ms": summary["speed_ms"], "air_speed_ms": summary["air_speed_ms"], "dx": summary["dx"], "seeds": a.seeds})
    print(f"{len(lines)} lines, {len(lines.points)} points, ends {lines.stop_counts()}, {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()

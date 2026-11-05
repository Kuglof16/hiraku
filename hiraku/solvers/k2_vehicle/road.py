"""Road surfaces and the tyre contact query.

Each contact is the closest point of the road solid to the wheel centre,
with the normal pointing from that point to the centre and depth = radius -
distance (a sphere). The simulator then takes the wheel as a disc: the
contact moves to the disc's lowest point toward that plane, so a cambered
wheel touches at its rim as in the K&C bench.
A step gives a second contact at its corner, so a wheel touches a step when
the centre is d = sqrt(2 r h - h^2) before it (the hand-calculation
geometry). Slopes and tilt tables are made by turning gravity, not the road.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod

import numpy as np


class Road(ABC):
    """Base of the road surfaces (BlockRoad, RandomRoad): each gives contact() and height()."""

    name = "road"

    @abstractmethod
    def contact(self, c: np.ndarray, r: np.ndarray):
        """c (n,3) wheel centres, r (n,) radii -> P (n,K,3), N (n,K,3), depth (n,K)."""

    def contacts(self, centres: list, radii: list) -> list:
        """Per wheel, a list of (px, py, pz, nx, ny, nz, depth) (plain floats)."""
        p, nrm, dep = self.contact(np.asarray(centres, dtype=float), np.asarray(radii, dtype=float))
        out = []
        for i in range(len(centres)):
            out.append([tuple(p[i, k].tolist()) + tuple(nrm[i, k].tolist()) + (float(dep[i, k]),)
                        for k in range(dep.shape[1])])
        return out

    @abstractmethod
    def height(self, x: float, y: float) -> float:
        """Road height (m) at (x, y)."""

    def describe(self) -> dict:
        return {"kind": self.name}


class BlockRoad(Road):
    """Piecewise-flat road along x (the same for every y): flat ground, steps, platforms.

    breaks: x positions where the level changes; levels: len(breaks) + 1 heights (m).
    """

    name = "blocks"

    def __init__(self, breaks=(), levels=(0.0,), y_range=None):
        self.y_range = y_range  # the blocks lie only for y in [lo, hi]; flat ground elsewhere
        self.breaks = np.asarray(breaks, dtype=float)
        self.levels = np.asarray(levels, dtype=float)
        if len(self.levels) != len(self.breaks) + 1:
            raise ValueError("levels must have one more entry than breaks")
        if len(self.breaks) and np.any(np.diff(self.breaks) <= 0):
            raise ValueError("breaks must increase")
        self.lo = np.concatenate([[-np.inf], self.breaks])
        self.hi = np.concatenate([self.breaks, [np.inf]])

    def height(self, x: float, y: float) -> float:
        return float(self.levels[int(np.searchsorted(self.breaks, x, side="right"))])

    def contacts(self, centres, radii):
        breaks = self.breaks.tolist()
        levels = self.levels.tolist()
        lo = [-math.inf] + breaks
        hi = breaks + [math.inf]
        out = []
        yr = self.y_range
        for (cx, cy, cz), r in zip(centres, radii):
            if yr is not None and not (yr[0] <= cy <= yr[1]):
                out.append([(cx, cy, 0.0, 0.0, 0.0, 1.0, r - cz)])
                continue
            k = 0
            while k < len(breaks) and cx >= breaks[k]:
                k += 1
            cl = []
            for j, z in enumerate(levels):
                if j == k:
                    cl.append((cx, cy, z, 0.0, 0.0, 1.0, r - (cz - z)))
                    continue
                qx = lo[j] if cx < lo[j] else hi[j]
                if abs(cx - qx) >= r:
                    continue
                qz = cz if cz < z else z
                dx, dz = cx - qx, cz - qz
                dist = math.sqrt(dx * dx + dz * dz)
                if dist >= r or dist < 1e-12:
                    continue
                cl.append((qx, cy, qz, dx / dist, 0.0, dz / dist, r - dist))
            out.append(cl)
        return out

    def contact(self, c, r):
        n = c.shape[0]
        b = len(self.levels)
        cx, cy, cz = c[:, 0], c[:, 1], c[:, 2]
        k = np.searchsorted(self.breaks, cx, side="right")
        qx = np.clip(cx[:, None], self.lo[None, :], self.hi[None, :])
        qz = np.minimum(cz[:, None], self.levels[None, :])
        own = np.arange(b)[None, :] == k[:, None]
        qz = np.where(own, self.levels[None, :], qz)
        p = np.empty((n, b, 3))
        p[:, :, 0] = qx
        p[:, :, 1] = cy[:, None]
        p[:, :, 2] = qz
        dx = cx[:, None] - qx
        dz = cz[:, None] - qz
        dist = np.sqrt(dx * dx + dz * dz)
        nrm = np.zeros((n, b, 3))
        safe = dist > 1e-12
        inv = np.where(safe, 1.0 / np.where(safe, dist, 1.0), 0.0)
        nrm[:, :, 0] = dx * inv
        nrm[:, :, 2] = np.where(safe, dz * inv, 1.0)
        # own interval: the plane below (or above, if the centre sank below the level)
        nrm[:, :, 0] = np.where(own, 0.0, nrm[:, :, 0])
        nrm[:, :, 2] = np.where(own, 1.0, nrm[:, :, 2])
        dist = np.where(own, dz, dist)
        depth = r[:, None] - dist
        return p, nrm, depth

    def describe(self) -> dict:
        return {"kind": self.name, "breaks_m": self.breaks.tolist(), "levels_m": self.levels.tolist()}


def flat() -> BlockRoad:
    return BlockRoad()


def step(x_step: float, height: float, y_range=None) -> BlockRoad:
    return BlockRoad([x_step], [0.0, height], y_range)


# Source: ISO 8608 (road surface profiles), its table of road classes A-H:
# displacement PSD G_d(n0) at n0 = 0.1 cycle/m (the geometric mean of each class
# band; class C band 128-512 x 1e-6 m^3) and waviness w = 2. Not checked in this
# repository: the ISO 8608 text was not read here, so these values are not
# verified against it.
ISO8608_GD_N0 = {"A": 16e-6, "B": 64e-6, "C": 256e-6, "D": 1024e-6, "E": 4096e-6,
                 "F": 16384e-6, "G": 65536e-6, "H": 262144e-6}
ISO8608_N0 = 0.1
ISO8608_W = 2.0


def iso8608_psd(n, road_class: str = "C", gd_n0: float | None = None):
    g0 = ISO8608_GD_N0[road_class] if gd_n0 is None else gd_n0
    return g0 * (np.asarray(n, dtype=float) / ISO8608_N0) ** (-ISO8608_W)


class RandomRoad(Road):
    """Two independent ISO 8608 tracks (left y = +half_track, right y = -half_track).

    z(x) = sum_k sqrt(2 G_d(n_k) dn) cos(2 pi n_k x + phi_k), phases from a seeded
    generator; linear between the tracks across y. The tracks are uncorrelated
    (no coherence), the unfavourable side for roll. A smooth ramp from 0 to 1
    over [fade_start, fade_end] lets the vehicle start on flat ground.
    """

    name = "iso8608"

    # n_min, n_max: the band of spatial frequencies summed (cycle/m). HIRAKU's choice, meant as ISO 8608's band;
    # whether ISO 8608 gives this band was not checked in this repository
    def __init__(self, road_class: str = "C", half_track: float = 0.6, seed: int = 1,
                 n_min: float = 0.011, n_max: float = 2.83, dn: float = 0.01,
                 fade_start: float = 2.0, fade_end: float = 6.0, gd_n0: float | None = None):
        rng = np.random.default_rng(seed)
        self.road_class = road_class
        self.seed = seed
        self.n = np.arange(n_min, n_max + 0.5 * dn, dn)
        self.amp = np.sqrt(2.0 * iso8608_psd(self.n, road_class, gd_n0) * dn)
        self.phase = rng.uniform(0.0, 2.0 * math.pi, size=(2, self.n.size))
        self.k = 2.0 * math.pi * self.n
        self.half_track = half_track
        self.fade_start, self.fade_end = fade_start, fade_end

    def _fade(self, x):
        u = np.clip((x - self.fade_start) / (self.fade_end - self.fade_start), 0.0, 1.0)
        w = u * u * (3.0 - 2.0 * u)
        dw = np.where((u > 0) & (u < 1), 6.0 * u * (1.0 - u) / (self.fade_end - self.fade_start), 0.0)
        return w, dw

    def track(self, x, side: int):
        """Height and slope of one track (side 0 left, 1 right) at x (array)."""
        x = np.asarray(x, dtype=float)
        arg = np.multiply.outer(x, self.k) + self.phase[side]
        z = np.cos(arg) @ self.amp
        dz = -(np.sin(arg) * self.k) @ self.amp
        w, dw = self._fade(x)
        return z * w, dz * w + z * dw

    def _hgrad(self, x, y):
        zl, sl = self.track(x, 0)
        zr, sr = self.track(x, 1)
        u = np.clip((y + self.half_track) / (2.0 * self.half_track), 0.0, 1.0)
        inside = (y > -self.half_track) & (y < self.half_track)
        h = zr + u * (zl - zr)
        hx = sr + u * (sl - sr)
        hy = np.where(inside, (zl - zr) / (2.0 * self.half_track), 0.0)
        return h, hx, hy

    def height(self, x: float, y: float) -> float:
        h, _, _ = self._hgrad(np.array([x]), np.array([y]))
        return float(h[0])

    def contacts(self, centres, radii):
        c = np.asarray(centres, dtype=float)
        h, hx, hy = self._hgrad(c[:, 0], c[:, 1])
        out = []
        for i, ((cx, cy, cz), r) in enumerate(zip(centres, radii)):
            hi_, gx, gy = float(h[i]), float(hx[i]), float(hy[i])
            inv = 1.0 / math.sqrt(1.0 + gx * gx + gy * gy)
            nx, ny, nz = -gx * inv, -gy * inv, inv
            d = (cz - hi_) * inv
            out.append([(cx - d * nx, cy - d * ny, cz - d * nz, nx, ny, nz, r - d)])
        return out

    def contact(self, c, r):
        h, hx, hy = self._hgrad(c[:, 0], c[:, 1])
        inv = 1.0 / np.sqrt(1.0 + hx * hx + hy * hy)
        nrm = np.stack([-hx * inv, -hy * inv, inv], axis=1)
        d = (c[:, 2] - h) * inv
        p = c - d[:, None] * nrm
        return p[:, None, :], nrm[:, None, :], (r - d)[:, None]

    def describe(self) -> dict:
        return {"kind": self.name, "class": self.road_class, "seed": self.seed,
                "n_cycles_per_m": [float(self.n[0]), float(self.n[-1])], "dn": float(self.n[1] - self.n[0]),
                "gd_n0_m3": float(self.amp[0] ** 2 / (2 * (self.n[1] - self.n[0])) * (self.n[0] / ISO8608_N0) ** ISO8608_W),
                "tracks": "left and right independent"}

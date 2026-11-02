"""Placing parts in a vehicle (packaging) without writing their coordinates by hand.

A Layout holds the frame (nodes, members, sections), the supports and the parts
placed so far. A part's position comes from references to what is already
there, plus clearances; each of x, y, z is a centre coordinate or a face placed
at a value (top_at, bottom_at, front_at, rear_at, left_at, right_at):

    lay = Layout(frame_and_supports)
    lay.put(Item("battery", "電池", "battery", (500, 600, 200), 50.0),
            x=lay.between("S1", "S2"), y=0.0, z=top_at(lay.bottom_of("floor")))

or from a search of the free space, nearest to a target point (K4's
separating-axis test against the frame members, the wheels through their
travel and the parts already placed, with a clearance):

    lay.search(item, region=((-1300, -350, 150), (-400, 350, 450)), target=(-725, 0, 330), clearance=10)

Fixing nodes (carried_by) default to the frame nodes of the stations on either
side of the part, on the side(s) of the centre line the part covers.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

Record = dict[str, Any]
Place = float | Callable[[float], float]  # a centre, or a function of the part's half size

TOL_MM = 1e-6
SEARCH_STEP_MM = 25.0  # grid of candidate centres (assumption: finer than the parts' clearances matter)


class PackageError(ValueError):
    """A part cannot be placed: the message says where it was looked for and what was in the way."""


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    kind: str
    size: tuple[float, float, float]  # mm along x, y, z
    mass: float  # kg


def top_at(v: float) -> Callable[[float], float]:
    return lambda half: v - half


def bottom_at(v: float) -> Callable[[float], float]:
    return lambda half: v + half


def front_at(v: float) -> Callable[[float], float]:
    return lambda half: v - half


def rear_at(v: float) -> Callable[[float], float]:
    return lambda half: v + half


left_at, right_at = front_at, rear_at  # +y side at v, -y side at v


class Layout:
    def __init__(self, design: Record):
        self.design = {**design, "parts": [dict(p) for p in design.get("parts", [])]}
        self._nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in self.design.get("nodes", [])}

    # ---------------------------------------------------------------- what is there
    @property
    def parts(self) -> list[Record]:
        return self.design["parts"]

    def part(self, pid: str) -> Record:
        try:
            return next(p for p in self.parts if p["id"] == pid)
        except StopIteration:
            raise PackageError(f"no part {pid!r} has been placed yet") from None

    def box(self, pid: str) -> tuple[np.ndarray, np.ndarray]:
        p = self.part(pid)
        c = np.asarray(p["box"]["center_mm"], float)
        h = np.asarray(p["box"]["size_mm"], float) / 2.0
        return c - h, c + h

    def top_of(self, pid: str) -> float:
        return float(self.box(pid)[1][2])

    def bottom_of(self, pid: str) -> float:
        return float(self.box(pid)[0][2])

    def front_of(self, pid: str) -> float:
        return float(self.box(pid)[1][0])

    def rear_of(self, pid: str) -> float:
        return float(self.box(pid)[0][0])

    def stations(self) -> dict[str, float]:
        """x of each frame station (a member running across), by its node name without the side letter."""
        out: dict[str, float] = {}
        for m in self.design.get("members", []):
            a, b = self._nodes[m["node_a"]], self._nodes[m["node_b"]]
            if abs(a[0] - b[0]) < TOL_MM and abs(a[1] - b[1]) > TOL_MM:
                out[m["node_a"][1:]] = float(a[0])
        return out

    def station_x(self, name: str) -> float:
        st = self.stations()
        if name not in st:
            raise PackageError(f"no frame station {name!r}; stations: {', '.join(st)}")
        return st[name]

    def between(self, a: str, b: str) -> float:
        """x midway between two stations."""
        return (self.station_x(a) + self.station_x(b)) / 2.0

    def _along_x(self) -> list[tuple[np.ndarray, np.ndarray]]:
        """(lower, upper corner) of the members that run along x (the rails)."""
        out = []
        for m in self.design.get("members", []):
            a, b = self._nodes[m["node_a"]], self._nodes[m["node_b"]]
            if abs(a[0] - b[0]) > max(abs(a[1] - b[1]), abs(a[2] - b[2])):
                dims = self.design["sections"][m["section"]]["dims_mm"]
                half = np.array([0.0, dims.get("b", dims.get("d", 0.0)) / 2.0, dims.get("h", dims.get("d", 0.0)) / 2.0])
                out.append((np.minimum(a, b) - half, np.maximum(a, b) + half))
        if not out:
            raise PackageError("the frame has no members along x (rails) to place against")
        return out

    def rail_top(self) -> float:
        return float(max(hi[2] for _, hi in self._along_x()))

    def rail_bottom(self) -> float:
        return float(min(lo[2] for lo, _ in self._along_x()))

    def rail_inner_gap(self) -> float:
        """Clear width between the left and right rails (mm)."""
        left = [lo[1] for lo, _ in self._along_x() if lo[1] > 0]
        right = [hi[1] for _, hi in self._along_x() if hi[1] < 0]
        if not left or not right:
            raise PackageError("the frame has no left and right rails")
        return float(min(left) - max(right))

    # ---------------------------------------------------------------- placing
    def put(self, item: Item, x: Place, y: Place, z: Place, carried_by: list[str] | str | None = "auto") -> Record:
        """Place `item` at a centre or face per axis. carried_by: node ids, "auto" (the stations
        either side, on the side(s) it covers) or None (rides with the nearest wheel)."""
        c = [w(item.size[i] / 2.0) if callable(w) else float(w) for i, w in enumerate((x, y, z))]
        p: Record = {"id": item.id, "name": item.name, "kind": item.kind,
                     "box": {"center_mm": [float(v) for v in c], "size_mm": [float(v) for v in item.size]},
                     "mass_kg": float(item.mass)}
        nodes = self.mounts(c, item.size) if carried_by == "auto" else carried_by
        if nodes:
            p["carried_by"] = list(nodes)
        self.parts.append(p)
        return p

    def search(self, item: Item, region, target, clearance: float = 10.0, step: float = SEARCH_STEP_MM,
               carried_by: list[str] | str | None = "auto") -> Record:
        """Place `item` at the free centre in `region` ((lo xyz), (hi xyz) of the centre) nearest
        to `target`, with at least `clearance` mm to the frame, the wheels through their travel and
        steering, and the parts already placed (K4 boxes). Raises PackageError when nothing fits."""
        from hiraku.solvers.k4_geometry import bodies
        from hiraku.solvers.k4_geometry.obb import from_box, overlap

        lo, hi, tgt = (np.asarray(v, float) for v in (*region, target))
        grids = [np.arange(lo[i], hi[i] + TOL_MM, step) if hi[i] > lo[i] else np.array([lo[i]]) for i in range(3)]
        cands = sorted(itertools.product(*grids), key=lambda c: float(np.linalg.norm(np.asarray(c) - tgt)))
        obstacles = [b.obb for b in bodies(self.design).values()] + self._wheel_envelopes()
        worst = None
        for c in cands:
            box = from_box(c, item.size)
            hits = [(o.name, overlap(box, o)) for o in obstacles if overlap(box, o) > -clearance]
            if not hits:
                return self.put(item, c[0], c[1], c[2], carried_by)
            deepest = max(hits, key=lambda h: h[1])
            if worst is None or deepest[1] < worst[1]:
                worst = deepest
        raise PackageError(f"{item.id} ({item.size[0]:g} x {item.size[1]:g} x {item.size[2]:g} mm) does not fit in "
                           f"{lo.tolist()}..{hi.tolist()} with {clearance:g} mm clearance; closest try met {worst[0]}")

    def _wheel_envelopes(self) -> list:
        """Boxes around each wheel through its suspension travel and steering lock."""
        from hiraku.solvers.k4_geometry.obb import from_box

        out = []
        steer = float(self.design.get("variables", {}).get("max_steer_angle", {}).get("value", 0.0))
        for s in self.design.get("supports", []):
            r, w = float(s["radius_mm"]), float(s.get("width_mm") or 0.0)
            travel = float(s.get("suspension", {}).get("travel_mm") or 0.0)
            a = np.radians(steer) if s["actuation"]["steer"] else 0.0
            dx = r * np.cos(a) + w / 2 * np.sin(a)
            dy = r * np.sin(a) + w / 2 * np.cos(a)
            x, y, z = (float(v) for v in s["position_mm"])
            out.append(from_box([x, y, z], [2 * dx, 2 * dy, 2 * r + travel], name=f"wheel {s['id']}"))
        return out

    def reorder(self, ids: list[str]) -> None:
        """Put the parts in this order (the rest after them, as they are)."""
        rank = {pid: i for i, pid in enumerate(ids)}
        self.parts.sort(key=lambda p: rank.get(p["id"], len(rank)))

    # ---------------------------------------------------------------- fixing nodes
    def at(self, *stations: str, sides: str = "LR") -> list[str]:
        """The frame nodes of these stations, left and right (or `sides`), station by station."""
        out = [f"{s}{st}" for st in stations for s in sides]
        missing = [n for n in out if n not in self._nodes]
        if missing:
            raise PackageError(f"no frame nodes {', '.join(missing)}")
        return out

    def mounts(self, center, size) -> list[str]:
        """Frame nodes of the stations on either side of the centre x (or the one it sits on), on the
        side(s) of the centre line the part covers: left and right ahead, then left and right behind."""
        st = self.stations()
        if not st:
            return []
        x = float(center[0])
        ahead = min((k for k, v in st.items() if v >= x - TOL_MM), key=lambda k: st[k], default=max(st, key=st.get))
        behind = max((k for k, v in st.items() if v < x - TOL_MM), key=lambda k: st[k], default=None)
        y0, y1 = float(center[1]) - size[1] / 2.0, float(center[1]) + size[1] / 2.0
        sides = [s for s, covers in (("L", y1 > 0), ("R", y0 < 0)) if covers]
        names = [ahead] + ([behind] if behind is not None and abs(st[ahead] - x) > TOL_MM else [])
        return [f"{s}{n}" for n in names for s in sides if f"{s}{n}" in self._nodes]

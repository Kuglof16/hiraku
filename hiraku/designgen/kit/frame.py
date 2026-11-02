"""Frames from lines, curves and attachment points.

    f = Frame(material="stkr400")
    rail = f.section(*rect_tube(40, 80, 2.3))
    f.ladder({"F": 400, "A": 0, "B": -1700, "E": -2100}, half_width=350, z=380, rail=rail, cross=rail)
    f.polyline("hoop", [("H1", p1), ("H2", p2), ("H3", p3)], rail)   # a bent tube as straight pieces
    f.mirror_y(["hoop_H1H2"])                                        # its left-right copy
    f.part()                                                         # {"nodes", "members", "sections"}

Nodes closer than `merge_mm` to an existing node are that node, so members
drawn from different lines meet at shared joints. Left nodes are named
L<name>, right ones R<name>; a station is a name with a pair of nodes.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

Record = dict[str, Any]


class FrameError(ValueError):
    pass


class Frame:
    def __init__(self, material: str, merge_mm: float = 1.0):
        self.material = material
        self.merge_mm = merge_mm
        self.nodes: dict[str, list[float]] = {}
        self.members: list[Record] = []
        self.sections: dict[str, Record] = {}

    # ---------------------------------------------------------------- pieces
    def section(self, key: str, sec: Record) -> str:
        if key in self.sections and self.sections[key] != sec:
            raise FrameError(f"section {key!r} is already defined differently")
        self.sections[key] = sec
        return key

    def node(self, nid: str, xyz) -> str:
        """Add a node, or return the existing one within merge_mm of xyz."""
        p = np.asarray(xyz, float)
        for k, q in self.nodes.items():
            if float(np.linalg.norm(p - np.asarray(q))) <= self.merge_mm:
                return k
        if nid in self.nodes:
            raise FrameError(f"node {nid!r} already exists at {self.nodes[nid]}, not at {list(p)}")
        self.nodes[nid] = [float(v) for v in p]
        return nid

    def member(self, mid: str, a: str, b: str, section: str) -> str:
        if section not in self.sections:
            raise FrameError(f"section {section!r} is not defined; add it with section()")
        if a == b:
            raise FrameError(f"member {mid!r} would join node {a!r} to itself")
        if any(m["id"] == mid for m in self.members):
            raise FrameError(f"member {mid!r} already exists")
        self.members.append({"id": mid, "node_a": a, "node_b": b, "section": section, "material": self.material,
                             "part": "frame"})
        return mid

    # ---------------------------------------------------------------- shapes
    def polyline(self, prefix: str, points: list[tuple[str, Any]], section: str) -> list[str]:
        """Straight members through the named points in order: <prefix>_<a><b>."""
        ids = [self.node(name, xyz) for name, xyz in points]
        return [self.member(f"{prefix}_{a}{b}", a, b, section) for a, b in zip(ids, ids[1:])]

    @staticmethod
    def arc(center, radius: float, start_deg: float, end_deg: float, pieces: int, plane: str = "xz") -> list[list[float]]:
        """Points along a circular arc in a plane through `center` (for a bent tube as `pieces` straight members)."""
        c = np.asarray(center, float)
        i, j = {"xz": (0, 2), "xy": (0, 1), "yz": (1, 2)}[plane]
        out = []
        for a in np.linspace(math.radians(start_deg), math.radians(end_deg), pieces + 1):
            p = c.copy()
            p[i] += radius * math.cos(a)
            p[j] += radius * math.sin(a)
            out.append([float(v) for v in p])
        return out

    def stations(self, stations: dict[str, float], half_width: float, z: float) -> None:
        """A pair of nodes (L, R) at each station: x from the table, y = +-half_width, height z."""
        for side, y in (("L", half_width), ("R", -half_width)):
            for name, x in stations.items():
                self.node(f"{side}{name}", [x, y, z])

    def rails(self, stations: list[str], section: str) -> list[str]:
        """Members along each side through the stations in order: rail_<side>_<a><b>."""
        out = []
        for side in "LR":
            for a, b in zip(stations, stations[1:]):
                out.append(self.member(f"rail_{side}_{a}{b}", f"{side}{a}", f"{side}{b}", section))
        return out

    def cross(self, station: str, section: str) -> str:
        """A member across at a station: cross_<station>."""
        return self.member(f"cross_{station}", f"L{station}", f"R{station}", section)

    def ladder(self, stations: dict[str, float], half_width: float, z: float, rail: str, cross: str) -> None:
        """Two rails through the stations and a cross member at every station."""
        self.stations(stations, half_width, z)
        order = list(stations)
        self.rails(order, rail)
        for st in order:
            self.cross(st, cross)

    def mirror_y(self, member_ids: list[str]) -> list[str]:
        """Copies of these members on the other side (y -> -y; every L and R in the node and member
        names swapped, so name pieces with L or R in them for their side only)."""
        swap = str.maketrans({"L": "R", "R": "L"})
        out = []
        for mid in member_ids:
            m = next(x for x in self.members if x["id"] == mid)
            ends = []
            for k in ("node_a", "node_b"):
                x, y, z = self.nodes[m[k]]
                ends.append(self.node(m[k].translate(swap), [x, -y, z]))
            out.append(self.member(mid.translate(swap) if mid.translate(swap) != mid else f"{mid}_m", ends[0], ends[1],
                                   m["section"]))
        return out

    # ---------------------------------------------------------------- out
    def part(self) -> Record:
        """The frame's share of a design record: nodes, members, sections."""
        return {"nodes": [{"id": k, "xyz_mm": list(v)} for k, v in self.nodes.items()],
                "members": [dict(m) for m in self.members], "sections": dict(self.sections)}

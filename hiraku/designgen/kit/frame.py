"""Frames from lines, curves and attachment points.

    f = Frame(material="stkr400")
    rail = f.section(*rect_tube(40, 80, 2.3))
    f.ladder({"F": 400, "A": 0, "B": -1700, "E": -2100}, half_width=350, z=380, rail=rail, cross=rail)
    f.polyline("hoop", [("H1", p1), ("H2", p2), ("H3", p3)], rail)   # a bent tube as straight pieces
    f.mirror_y(["hoop_H1H2"])                                        # its left-right copy
    f.split("rail_L_S2B", "LH", [x, y, z], ("rail_L_S2H", "rail_L_HB"))  # a node on a member, the member in two
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


def gusset_spans(design: Record) -> None:
    """Every gusset (Frame.gussets) is welded along its through tube: add to its carried_by the other nodes
    of that tube its leg reaches over (where the tube goes on in another member, e.g. a node a later
    part added on the rail). Run once the design has all its nodes."""
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design.get("nodes", [])}
    for g in design.get("parts", []):
        if g.get("kind") != "gusset" or not g.get("carried_by") or g["carried_by"][0] not in nodes:
            continue
        p = nodes[g["carried_by"][0]]
        c, s = np.asarray(g["box"]["center_mm"], float), np.asarray(g["box"]["size_mm"], float)
        x0, x1 = c[0] - s[0] / 2.0, c[0] + s[0] / 2.0
        over = sorted(k for k, q in nodes.items() if k not in g["carried_by"] and abs(q[1] - p[1]) <= 1e-9
                      and abs(q[2] - p[2]) <= 1e-9 and x0 <= q[0] <= x1)
        g["carried_by"] = [*g["carried_by"], *over]


def _overlapping(part: Record, others: list[Record]) -> str | None:
    """The id of the first of `others` whose box overlaps the part's box by more than a touch (K4's
    TOUCH_MM), else None."""
    from hiraku.solvers.k4_geometry.model import TOUCH_MM
    from hiraku.solvers.k4_geometry.obb import from_box, overlap

    a = from_box(part["box"]["center_mm"], part["box"]["size_mm"], part["box"].get("rpy_deg"), part["id"])
    for o in others:
        b = from_box(o["box"]["center_mm"], o["box"]["size_mm"], o["box"].get("rpy_deg"), o["id"])
        if overlap(a, b) > TOUCH_MM:
            return o["id"]
    return None


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

    def member(self, mid: str, a: str, b: str, section: str, material: str | None = None) -> str:
        """A member from node a to node b; material: the frame's unless given."""
        if section not in self.sections:
            raise FrameError(f"section {section!r} is not defined; add it with section()")
        if a == b:
            raise FrameError(f"member {mid!r} would join node {a!r} to itself")
        if any(m["id"] == mid for m in self.members):
            raise FrameError(f"member {mid!r} already exists")
        self.members.append({"id": mid, "node_a": a, "node_b": b, "section": section,
                             "material": material or self.material, "part": "frame"})
        return mid

    # ---------------------------------------------------------------- shapes
    def polyline(self, prefix: str, points: list[tuple[str, Any]], section: str, material: str | None = None) -> list[str]:
        """Straight members through the named points in order: <prefix>_<a><b>."""
        ids = [self.node(name, xyz) for name, xyz in points]
        return [self.member(f"{prefix}_{a}{b}", a, b, section, material) for a, b in zip(ids, ids[1:])]

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

    def split(self, member_id: str, nid: str, xyz, ids: tuple[str, str]) -> str:
        """Put a node on a member and replace the member with its two pieces (ids: the piece from node_a,
        the piece to node_b), in its place in the member list. xyz must lie on the member, strictly
        between its ends."""
        i = next((k for k, m in enumerate(self.members) if m["id"] == member_id), None)
        if i is None:
            raise FrameError(f"no member {member_id!r} to split")
        m = self.members[i]
        a, b = (np.asarray(self.nodes[m[k]]) for k in ("node_a", "node_b"))
        p = np.asarray(xyz, float)
        d = b - a
        s = float((p - a) @ d / (d @ d))
        if not 0.0 < s < 1.0 or float(np.linalg.norm(a + s * d - p)) > self.merge_mm:
            raise FrameError(f"point {list(p)} is not on member {member_id!r} between its ends")
        n = self.node(nid, p)
        first = dict(m, id=ids[0], node_b=n)
        second = dict(m, id=ids[1], node_a=n)
        if any(x["id"] in ids for x in self.members if x is not m):
            raise FrameError(f"member {ids[0]!r} or {ids[1]!r} already exists")
        self.members[i:i + 1] = [first, second]
        return n

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

    # ---------------------------------------------------------------- joint reinforcement
    def _along_x_at(self, node: str, branch: str) -> list[Record]:
        """The members other than `branch` that end at `node` and run along x (the tube passing through a T
        joint; other members there, e.g. a post standing on the rail, are not part of the T)."""
        out = []
        for x in self.members:
            if x["id"] == branch or node not in (x["node_a"], x["node_b"]):
                continue
            d = np.asarray(self.nodes[x["node_b"]]) - np.asarray(self.nodes[x["node_a"]])
            if abs(d[1]) <= 1e-9 and abs(d[2]) <= 1e-9:
                out.append(x)
        return out

    def gussets(self, node: str, branch: str, leg: float, thickness: float, density_kg_m3: float,
                avoid: list[Record] | None = None) -> list[Record]:
        """Four triangular gusset parts at the T joint where member `branch` ends on the side of the
        rect tube that passes through `node` (ahead of and behind the branch, on its upper and lower
        wall): each a right triangle with legs `leg` along the through tube and along the branch,
        its right angle where the through tube's side wall meets the branch's side wall, lying in the
        plane of the branch's upper (lower) wall mid-surface. box.center_mm is the triangle's
        centroid, box.size_mm [leg along the through tube, leg along the branch, thickness] (the
        shell mesher's convention), mass = density x leg^2 / 2 x thickness, carried_by [node].
        The through tube must run along x and the branch along y (a ladder's rail and cross member).
        Each gusset lies on the side wall of the through tube's piece on its own side (front or rear),
        so a joint between two rail pieces of different widths puts each on its own piece's wall.
        avoid: parts (contract records with a box) the gussets keep out of; a gusset whose box would
        overlap one of them is left off, and the joint's other gussets say which side was left off
        and why in their name (the parts of other systems are placed first, the gussets go where
        there is room)."""
        m = next((x for x in self.members if x["id"] == branch), None)
        if m is None or node not in (m["node_a"], m["node_b"]):
            raise FrameError(f"member {branch!r} does not end at node {node!r}")
        other = m["node_b"] if m["node_a"] == node else m["node_a"]
        p = np.asarray(self.nodes[node])
        u_in = np.asarray(self.nodes[other]) - p
        if abs(u_in[0]) > 1e-9 or abs(u_in[2]) > 1e-9:
            raise FrameError(f"gussets need the branch {branch!r} to run along y from node {node!r}")
        u_in /= np.linalg.norm(u_in)
        hosts = self._along_x_at(node, branch)
        if len(hosts) != 2:
            raise FrameError(f"node {node!r} is not a T joint: {len(hosts)} other members end there, not 2")
        br = self.sections[m["section"]]["dims_mm"]

        def piece(sx: float) -> dict:  # the through tube's piece on the front (sx 1) or rear (sx -1) side
            ahead = [x for x in hosts if sx * (self.nodes[x["node_b"] if x["node_a"] == node else x["node_a"]][0] - p[0]) > 0]
            return self.sections[(ahead or hosts)[0]["section"]]["dims_mm"]

        mass = density_kg_m3 * 1e-9 * leg * leg / 2.0 * thickness
        out, off = [], []
        for sx, fb in ((1.0, "front"), (-1.0, "rear")):
            host = piece(sx)
            y_wall = p[1] + u_in[1] * (host["b"] - host["t"]) / 2.0
            for sz, ul in ((1.0, "upper"), (-1.0, "lower")):
                ox = p[0] + sx * (br["b"] - br["t"]) / 2.0
                oz = p[2] + sz * (br["h"] - br["t"]) / 2.0
                c = [ox + sx * leg / 3.0, y_wall + u_in[1] * leg / 3.0, oz]
                where = f"{'前' if sx > 0 else '後'}・{'上' if sz > 0 else '下'}"
                g = {"id": f"gusset_{node}_{fb}_{ul}", "name": f"{node} のつなぎ目のガセット ({where})", "kind": "gusset",
                     "box": {"center_mm": [float(v) for v in c], "size_mm": [float(leg), float(leg), float(thickness)]},
                     "mass_kg": float(mass), "carried_by": [node]}
                hit = _overlapping(g, avoid or [])
                if hit:
                    off.append(f"{where}は {hit} があるので付けない")
                else:
                    out.append(g)
        if off:
            for g in out:
                g["name"] += f" ({'、'.join(off)})"
        return out

    def through_part(self, node: str, branch: str, density_kg_m3: float) -> Record:
        """The part for member `branch` running on through the rect tube at `node` (a T joint), from the
        hole in its near wall to its far wall: id through_<branch>_<node>, kind frame, carried_by [node].
        The beam member still ends at the node, so this is the tube the beam model does not have: box
        centred at the node, [branch width, through tube width - its wall, branch height] (the run from
        the near wall's mid-surface to the far wall's); mass = density x (branch area x that run - the
        near wall cut out for the hole, wall t x (branch b - t_b) x (branch h - t_b)), mid-surface
        sizes as the shell mesh has them. The through tube must run along x and the branch along y."""
        m = next((x for x in self.members if x["id"] == branch), None)
        if m is None or node not in (m["node_a"], m["node_b"]):
            raise FrameError(f"member {branch!r} does not end at node {node!r}")
        other = m["node_b"] if m["node_a"] == node else m["node_a"]
        p = np.asarray(self.nodes[node])
        d = np.asarray(self.nodes[other]) - p
        if abs(d[0]) > 1e-9 or abs(d[2]) > 1e-9:
            raise FrameError(f"a through joint needs the branch {branch!r} to run along y from node {node!r}")
        hosts = self._along_x_at(node, branch)
        if len(hosts) != 2:
            raise FrameError(f"node {node!r} is not a T joint: {len(hosts)} other members end there, not 2")
        host, br = (self.sections[x["section"]]["dims_mm"] for x in (hosts[0], m))
        run = host["b"] - host["t"]
        area = 2.0 * br["t"] * (br["b"] + br["h"] - 2.0 * br["t"])
        hole = host["t"] * (br["b"] - br["t"]) * (br["h"] - br["t"])
        mass = density_kg_m3 * 1e-9 * (area * run - hole)
        # the member it continues (welded) and that member's material: the tube's section is the branch's, so a
        # rule about walls and section ratios can read it there
        return {"id": f"through_{branch}_{node}",
                "name": f"{node} で {branch} がレールの中を通る分 (手前の壁の穴の分を引いた正味)",
                "kind": "frame",
                "box": {"center_mm": [float(x) for x in p], "size_mm": [float(br["b"]), float(run), float(br["h"])]},
                "mass_kg": float(mass), "carried_by": [node],
                "connects_to": [{"to": node, "via": "weld", "carries": "load"},
                                {"to": branch, "via": "weld", "carries": "load"}],
                **({"material": m["material"]} if m.get("material") else {})}

    # ---------------------------------------------------------------- out
    def part(self) -> Record:
        """The frame's share of a design record: nodes, members, sections."""
        return {"nodes": [{"id": k, "xyz_mm": list(v)} for k, v in self.nodes.items()],
                "members": [dict(m) for m in self.members], "sections": dict(self.sections)}

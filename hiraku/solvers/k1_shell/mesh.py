"""Shell mesh of a design's frame: the mid-surfaces of the tube walls.

Coordinates are the design's (mm, ISO 8855: x forward, y left, z up).
Section axes follow K1 (k1_frame.model): local x along the member, local y
the h direction (up for a horizontal member), local z the b direction.
For a non-vertical member: ez = unit(d x z_up), ey = ez x d; for a vertical
one ey = -+x, ez = -y (PyNite's rule, rotated into design axes; see
tests/solvers/test_k1_shell.py).

What is meshed:
- rect_tube {b, h, t}: the (b - t) x (h - t) mid-line rectangle;
  round_tube {d, t}: the circle of diameter d - t as a regular polygon (a
  multiple of 4 facets, at least 16) whose corners sit slightly outside the
  circle so that the faceted wall has the circle's second moment of area
  (facet_radius_factor; 1.011 for 16 facets, which makes the wall area
  0.4 % larger). Other shapes raise DesignError.
- Members that continue in a straight line with the same section are one
  tube ("run"); every element keeps the id of the member it lies in.
- Structured grid of 4-node quadrilaterals per run (axial stations x
  points around the section), counter-clockwise seen from outside.
  Between fixed lines (wall corners, joint lines, member ends) every
  interval is cut into ceil(length / element_size) equal parts.

Joints (at each design node where tube ends meet):
- T joint: a tube end meets the side of a tube that passes through the
  node, at right angles, square to the wall, with section axes aligned and
  the footprint inside the wall. The branch stops at the wall's
  mid-surface; the through tube's grid gets lines through every node of the
  branch's end ring, so the ring nodes are nodes of the wall (shared).
- L joint: exactly two tube ends meet at right angles with aligned sections
  and nothing passes through. The larger section (by area; on a tie the
  member listed first) is extended to the mid-surface of the other's outer
  wall; the other ends on its side as in a T joint.
- Anything else (oblique joints, round tubes in a joint, more than two
  ends, collinear ends of different sections, joints whose grid lines
  would clash): the end ring is not shared. The tube stops at the other
  tube's mid-surface envelope along its axis and each ring node is tied by
  a rigid link (RBE2 type, solve.reduction_matrix) to the nearest node of
  the other tube. Listed in `notes`.
- Open tube ends are not closed, corners are sharp (no corner radius) and
  welds are not modelled (shared nodes are fully continuous).

Joint reinforcement (the beam model K1 keeps its members and nodes either way):
- design variable cross_members_through = 1: at every T joint the branch's
  four walls go on through a hole in the near wall of the tube it meets, to
  the mid-surface of that tube's far wall. The branch gets a grid line at
  the near wall, its ring there is shared with the near wall (the hole's
  edge; the near wall inside the footprint is left out) and its end ring
  with the far wall. L joints stay as they are. A branch whose footprint
  reaches the edge of the wall (its walls would lie in the other tube's
  walls) stays a plain T joint (listed in `notes`).
- parts of kind "gusset": a flat right-triangle plate at a T joint, carried
  by the joint's node. box.center_mm is the triangle's centroid, box.size_mm
  [leg along the tube that passes through, leg along the branch, plate
  thickness]. The plate lies in the plane of the branch's upper (or lower)
  wall mid-surface; its right-angle corner is where the through tube's side
  wall meets the branch's side wall, one leg runs along the through tube's
  wall and the other along the branch's corner line, and both legs share
  nodes with the tube walls (welded). It is meshed with quadrilaterals (the
  triangle split into three quads at its centroid and edge mid-points, each
  cut n x n); its elements carry the part id in element_member and take the
  branch member's material.

Wheel mounts: for each support whose frame node lies inside a straight
tube, `mounts[frame node]` are that tube's wall nodes (all four walls)
within +- the tube's height h of the node along the tube (an assumed
bracket size), and `mount_elements` the elements whose nodes are all in
that set (the bracket's footprint).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from scipy.spatial import cKDTree

from ..k0_handcalc.vehicle import DesignError, member_props

ELEMENT_SIZE_MM = 10.0
FORMULATION = ("MITC4 (Dvorkin & Bathe 1984) on flat 4-node facets; membrane with incompatible modes; "
               "6 DOF per node with a small drilling stiffness")
TOL = 1e-6  # mm, for coincident lines and nodes
ANGLE_TOL = 1e-6  # |cos| for parallel / perpendicular tests
SHAPES = ("rect_tube", "round_tube")
UP = np.array([0.0, 0.0, 1.0])


def k1_axes(d: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(ey, ez) of a member with unit direction d, as K1 (PyNite) orients it, in design axes."""
    c = np.cross(d, UP)
    if np.linalg.norm(c) < 1e-9:
        ey = np.array([-1.0, 0.0, 0.0]) if d[2] > 0 else np.array([1.0, 0.0, 0.0])
        return ey, np.array([0.0, -1.0, 0.0])
    ez = c / np.linalg.norm(c)
    return np.cross(ez, d), ez


@dataclass
class _Member:
    id: str
    order: int
    a: np.ndarray
    b: np.ndarray
    d: np.ndarray
    L: float
    ey: np.ndarray
    ez: np.ndarray
    section_id: str
    shape: str
    dims: dict
    t: float
    area: float


@dataclass
class _Run:
    id: int
    members: list[str]  # in run order
    origin: np.ndarray  # design node at s = 0
    ex: np.ndarray
    ey: np.ndarray
    ez: np.ndarray
    length: float
    member_s: dict[str, tuple[float, float]]
    nodes_at: dict[str, float]  # design node id -> s
    start_node: str
    end_node: str
    shape: str
    H: float = 0.0  # mid-line size along ey (rect)
    B: float = 0.0  # mid-line size along ez (rect)
    R: float = 0.0  # mid-line radius (round)
    t: float = 0.0
    area: float = 0.0
    order: int = 0
    height: float = 0.0  # outer section size along ey (h of a rect tube, d of a round one)
    extra_s: set = field(default_factory=set)  # grid lines from through joints and gussets
    ext: list[float] = field(default_factory=lambda: [0.0, 0.0])  # added length at start / end (negative: cut back)
    forced_s: set = field(default_factory=set)
    forced_c: list = field(default_factory=lambda: [set(), set(), set(), set()])

    # ---- section geometry
    def wall_lengths(self) -> list[float]:
        return [self.B, self.H, self.B, self.H]

    def wall_point(self, wall: int, w: float) -> tuple[float, float]:
        """(y, z) of the point w along wall 0 (+y), 1 (+z), 2 (-y), 3 (-z), walking counter-clockwise about ex."""
        H2, B2 = self.H / 2.0, self.B / 2.0
        if wall == 0:
            return H2, -B2 + w
        if wall == 1:
            return H2 - w, B2
        if wall == 2:
            return -H2, B2 - w
        return -H2 + w, -B2

    def wall_of(self, y: float, z: float) -> tuple[int, float]:
        """Wall and position along it of a point (y, z) on the mid-line rectangle."""
        H2, B2 = self.H / 2.0, self.B / 2.0
        cands = [(abs(y - H2), 0, z + B2), (abs(z - B2), 1, H2 - y), (abs(y + H2), 2, B2 - z), (abs(z + B2), 3, y + H2)]
        err, wall, w = min(cands)
        return wall, w

    def half_extent(self, u: np.ndarray) -> float:
        """Distance from the axis along direction u to the mid-surface (u need not be perpendicular)."""
        up = u - (u @ self.ex) * self.ex
        if np.linalg.norm(up) < 1e-9:
            return 0.0
        if self.shape == "round_tube":
            return self.R / float(np.linalg.norm(up))
        k = max(abs(up @ self.ey) / (self.H / 2.0), abs(up @ self.ez) / (self.B / 2.0))
        return 1.0 / k

    def s_range(self) -> tuple[float, float]:
        return -self.ext[0], self.length + self.ext[1]

    def local(self, p: np.ndarray) -> tuple[float, float, float]:
        r = p - self.origin
        return float(r @ self.ex), float(r @ self.ey), float(r @ self.ez)


@dataclass
class _Joint:
    node: str
    kind: str  # "T", "L", "tied"
    branch: int  # run id of the tube that ends
    branch_end: int  # 0 start, 1 end of that run
    host: int  # run id of the tube it ends on
    wall: int = -1
    s_set: tuple = ()
    c_set: tuple = ()
    footprint: tuple = ()  # (s0, s1, c0, c1)
    reason: str = ""
    through: bool = False  # the branch goes on through the near wall to the far wall
    dist: float = 0.0  # node to the near wall's mid-surface
    far_wall: int = -1
    far_c_set: tuple = ()
    far_footprint: tuple = ()
    note: str = ""


@dataclass
class _Gusset:
    id: str
    joint: _Joint
    O: np.ndarray  # right-angle corner
    u_s: np.ndarray  # along the through tube
    u_in: np.ndarray  # along the branch, away from the through tube
    normal: np.ndarray  # out of the branch (the plate's top side)
    g_s: float
    g_in: float
    t: float
    n: int  # each of the three quads is cut n x n


@dataclass
class ShellMesh:
    """A shell mesh of the frame and the bookkeeping to load it and read it back.

    nodes (N, 3) mm; elements (E, 4) node indices, counter-clockwise seen from
    outside; element_member (E) member ids; member_thickness_mm {member: t}.
    station_s_mm (E): position of the element centre along its member from
    node_a; station_yz_mm (E, 2): the centre in the member's section axes
    (K1 local y, z), for painting beam results onto the elements.
    end_rings {(member, "a" | "b"): node indices around the section at that
    end, in order (counter-clockwise about the run axis); for a member end
    inside a straight run, the ring at the nearest axial station};
    joints {design node: node indices of the joint (rings of the member ends
    there, the wall patch they sit on, tied nodes)}; joint_kinds {design
    node: "T", "L", "tied", "through" or "open end"}; shared_rings {design
    node: [(branch member, ring nodes, wall patch nodes)]} for T and L joints.
    links [(slave, master)] rigid links of the tied joints.
    """

    nodes: np.ndarray
    elements: np.ndarray
    element_member: list[str]
    member_thickness_mm: dict[str, float]
    element_size_mm: float
    station_s_mm: np.ndarray
    station_yz_mm: np.ndarray
    end_rings: dict[tuple[str, str], np.ndarray]
    joints: dict[str, np.ndarray]
    joint_kinds: dict[str, str]
    links: list[tuple[int, int]]
    notes: list[str]
    shared_rings: dict[str, list[tuple[str, np.ndarray, np.ndarray]]] = field(default_factory=dict)
    member_axes: dict[str, np.ndarray] = field(default_factory=dict)  # member -> (3, 3) rows ex, ey, ez
    mounts: dict[str, np.ndarray] = field(default_factory=dict)  # support frame node -> wall nodes of its mount
    mount_elements: dict[str, np.ndarray] = field(default_factory=dict)  # frame node -> elements inside the mount
    mount_half_mm: dict[str, float] = field(default_factory=dict)  # frame node -> half length of the mount
    part_material: dict[str, str] = field(default_factory=dict)  # gusset part -> member whose material it takes
    through_joints: list[str] = field(default_factory=list)  # design nodes of the through joints

    @property
    def n_nodes(self) -> int:
        return len(self.nodes)

    @property
    def n_elements(self) -> int:
        return len(self.elements)

    def edge_lengths(self) -> np.ndarray:
        x = self.nodes[self.elements]
        return np.linalg.norm(x - np.roll(x, -1, axis=1), axis=2)

    def record(self, mesh_id: str, design_version: int) -> dict:
        """The contract `mesh` record (node coordinates rounded to 0.0001 mm to keep the record small)."""
        return {
            "mesh_id": mesh_id,
            "design_version": int(design_version),
            "element_type": "shell",
            "formulation": FORMULATION,
            "element_size_mm": float(self.element_size_mm),
            "nodes_mm": [[round(float(v), 4) for v in p] for p in self.nodes],
            "elements": [[int(i) for i in e] for e in self.elements],
            "element_member": list(self.element_member),
            "member_thickness_mm": {k: float(v) for k, v in self.member_thickness_mm.items()},
            "notes": " ".join(self.notes),
        }


# ------------------------------------------------------------------ helpers


def _divide(points, size: float) -> np.ndarray:
    """Sorted unique fixed points, each interval cut into ceil(length / size) equal parts."""
    p = np.unique(np.round(np.asarray(sorted(points), float), 9))
    keep = [p[0]]
    for v in p[1:]:
        if v - keep[-1] > TOL:
            keep.append(v)
    out = [keep[0]]
    for a, b in zip(keep[:-1], keep[1:]):
        n = max(1, math.ceil((b - a) / size - 1e-9))
        out.extend(a + (b - a) * np.arange(1, n + 1) / n)
    return np.array(out)


def _key(values) -> tuple:
    return tuple(sorted({round(float(v), 6) for v in values}))


def _members(design: dict) -> dict[str, _Member]:
    props = member_props(design)
    if not props:
        raise DesignError("the design has no members, so there is no frame to mesh")
    out = {}
    for i, (mid, mp) in enumerate(props.items()):
        sec = design["sections"][mp["member"]["section"]]
        shape = sec.get("shape")
        if shape not in SHAPES:
            raise DesignError(
                f"member {mid!r} uses section {mp['member']['section']!r} of shape {shape!r}; the shell mesh "
                f"needs tube walls: give it a rect_tube {{b, h, t}} or round_tube {{d, t}} section "
                f"(or leave it to the beam model K1)")
        d = (mp["b"] - mp["a"]) / mp["length"]
        ey, ez = k1_axes(d)
        out[mid] = _Member(mid, i, mp["a"], mp["b"], d, mp["length"], ey, ez, mp["member"]["section"], shape,
                           {k: float(v) for k, v in sec["dims_mm"].items()}, float(sec["dims_mm"]["t"]),
                           float(mp["section"]["A"]))
    return out


def _runs(design: dict, mem: dict[str, _Member]) -> tuple[list[_Run], dict]:
    """Join members that continue straight with the same section into runs."""
    ends = defaultdict(list)  # node -> [(member, end)]
    for m in design["members"]:
        ends[m["node_a"]].append((m["id"], "a"))
        ends[m["node_b"]].append((m["id"], "b"))

    def outward(mid, end):  # direction from the node into the member
        return mem[mid].d if end == "a" else -mem[mid].d

    partner = {}
    for node, es in ends.items():
        es = sorted(es, key=lambda e: (-mem[e[0]].area, mem[e[0]].order))
        for i, e1 in enumerate(es):
            if e1 in partner:
                continue
            for e2 in es[i + 1:]:
                if e2 in partner:
                    continue
                m1, m2 = mem[e1[0]], mem[e2[0]]
                if m1.section_id != m2.section_id:
                    continue
                if outward(*e1) @ outward(*e2) > -1.0 + ANGLE_TOL:
                    continue
                if m1.shape == "rect_tube" and abs(m1.ey @ m2.ey) < 1.0 - ANGLE_TOL:
                    continue
                partner[e1] = e2
                partner[e2] = e1
                break

    runs, seen = [], set()
    for m in design["members"]:  # start runs at member ends without a partner, in design order
        for start_end in ("a", "b"):
            if m["id"] in seen or (m["id"], start_end) in partner:
                continue
            chain, cur, end = [], m["id"], start_end
            while True:
                chain.append((cur, end))
                seen.add(cur)
                other = "b" if end == "a" else "a"
                nxt = partner.get((cur, other))
                if nxt is None:
                    break
                cur, end = nxt
            runs.append(chain)
    if len(seen) != len(mem):
        raise DesignError("some members form a closed straight loop; split the loop at a node with a corner")

    out = []
    for rid, chain in enumerate(runs):
        first = mem[chain[0][0]]
        node_of = {m["id"]: m for m in design["members"]}
        start_node = node_of[chain[0][0]]["node_a" if chain[0][1] == "a" else "node_b"]
        origin = first.a if chain[0][1] == "a" else first.b
        ex = first.d if chain[0][1] == "a" else -first.d
        ey = first.ey - (first.ey @ ex) * ex
        ey /= np.linalg.norm(ey)
        ez = np.cross(ex, ey)
        member_s, nodes_at, s = {}, {start_node: 0.0}, 0.0
        for mid, end in chain:
            member_s[mid] = (s, s + mem[mid].L)
            s += mem[mid].L
            far = node_of[mid]["node_b" if end == "a" else "node_a"]
            nodes_at[far] = s
        r = _Run(rid, [c[0] for c in chain], origin.copy(), ex, ey, ez, s, member_s, nodes_at, start_node, far,
                 first.shape, t=first.t, area=first.area, order=first.order)
        if first.shape == "rect_tube":
            r.H = first.dims["h"] - first.t
            r.B = first.dims["b"] - first.t
            r.height = first.dims["h"]
        else:
            r.R = (first.dims["d"] - first.t) / 2.0
            r.height = first.dims["d"]
        out.append(r)
    return out, ends


def round_facets(R: float, size: float) -> int:
    """Facets around a round tube of mid-line radius R: a multiple of 4, at least 16, edges <= size."""
    return max(16, 4 * math.ceil(2.0 * math.pi * R / size / 4.0 - 1e-9))


def facet_radius_factor(n: int) -> float:
    """Corner radius / R of a regular n-gon whose thin wall has the second moment of area of the circle.

    Thin n-gon, corner radius rho: I = t rho^3 n sin(pi/n) (cos^2(pi/n) + sin^2(pi/n) / 3); circle: pi R^3 t.
    """
    s, c = math.sin(math.pi / n), math.cos(math.pi / n)
    return (math.pi / (n * s * (c * c + s * s / 3.0))) ** (1.0 / 3.0)


def _ring_yz(run: _Run, size: float) -> tuple[np.ndarray, list[tuple[int, float]]]:
    """Points (y, z) around the section, counter-clockwise about ex, and (wall, w) of each (rect)."""
    if run.shape == "round_tube":
        n = round_facets(run.R, size)
        ph = 2.0 * math.pi * np.arange(n) / n
        rho = run.R * facet_radius_factor(n)
        return np.stack([rho * np.cos(ph), rho * np.sin(ph)], axis=1), [(-1, float(p)) for p in ph]
    pts, tags = [], []
    for wall, Lw in enumerate(run.wall_lengths()):
        ws = _divide([0.0, Lw, *[c for c in run.forced_c[wall] if TOL < c < Lw - TOL]], size)
        for w in ws[:-1]:
            pts.append(run.wall_point(wall, w))
            tags.append((wall, float(w)))
    return np.array(pts), tags


def _aligned_rect(branch: _Run, host: _Run, u: np.ndarray):
    """For a rect branch leaving `host` along u: (wall, distance) if square to a wall, else None."""
    if branch.shape != "rect_tube" or host.shape != "rect_tube":
        return None
    if abs(u @ host.ex) > ANGLE_TOL:
        return None
    if abs(abs(branch.ey @ host.ex) - 1.0) > ANGLE_TOL and abs(abs(branch.ez @ host.ex) - 1.0) > ANGLE_TOL:
        return None
    if abs(abs(u @ host.ey) - 1.0) < ANGLE_TOL:
        return (0 if u @ host.ey > 0 else 2), host.H / 2.0
    if abs(abs(u @ host.ez) - 1.0) < ANGLE_TOL:
        return (1 if u @ host.ez > 0 else 3), host.B / 2.0
    return None


def _end_dir(run: _Run, end: int) -> np.ndarray:
    """Direction from the end node into the run."""
    return run.ex if end == 0 else -run.ex


def _end_s(run: _Run, end: int) -> float:
    lo, hi = run.s_range()
    return lo if end == 0 else hi


def _classify(runs: list[_Run], ends: dict, through_mode: bool = False) -> list[_Joint]:
    """Joint kinds at every node, with the run ends extended or cut back to suit (through_mode:
    T branches go on to the far wall)."""
    joints = []
    for node in ends:
        through = [r for r in runs if node in r.nodes_at and r.start_node != node and r.end_node != node]
        tube_ends = [(r, 0) for r in runs if r.start_node == node] + [(r, 1) for r in runs if r.end_node == node]
        if len(through) > 1:
            names = ", ".join(r.members[0] for r in through)
            raise DesignError(f"tubes ({names}) pass through each other at node {node!r}; the shell mesh needs one of "
                              f"them to end there: give its two sides different sections, or move one of them so "
                              f"they meet at separate nodes")
        if not tube_ends:
            continue
        if through:
            host = through[0]
            for r, e in tube_ends:
                u = _end_dir(r, e)
                al = _aligned_rect(r, host, u)
                if al is not None:
                    wall, dist = al
                    j = _Joint(node, "T", r.id, e, host.id, wall, dist=dist)
                    if through_mode:
                        j.through, j.far_wall = True, (wall + 2) % 4
                    r.ext[e] = dist if j.through else -dist
                    joints.append(j)
                else:
                    r.ext[e] = -host.half_extent(u)
                    joints.append(_Joint(node, "tied", r.id, e, host.id, reason="not square to the wall of a rect tube"))
            continue
        if len(tube_ends) == 1:
            continue  # open end
        if len(tube_ends) == 2:
            (rb, eb), (rs, es_) = sorted(tube_ends, key=lambda re: (-re[0].area, re[0].order))
            ub, us = _end_dir(rb, eb), _end_dir(rs, es_)
            if abs(ub @ us) < ANGLE_TOL:
                al = _aligned_rect(rs, rb, us)
                back = _aligned_rect(rb, rs, ub)
                if al is not None and back is not None:
                    wall, dist = al
                    rb.ext[eb] = rs.half_extent(-ub)  # to the mid-surface of the other's outer wall
                    rs.ext[es_] = -dist
                    joints.append(_Joint(node, "L", rs.id, es_, rb.id, wall))
                    continue
        # general: the largest tube ends at the node, the others stop at its envelope and are tied
        (rb, eb) = min(tube_ends, key=lambda re: (-re[0].area, re[0].order))
        for r, e in tube_ends:
            if r is rb and e == eb:
                continue
            r.ext[e] = -rb.half_extent(_end_dir(r, e))
            joints.append(_Joint(node, "tied", r.id, e, rb.id, reason="tube ends that are not a square T or L"))
    return joints


def _ring_points(run: _Run, end: int, size: float, s: float | None = None) -> np.ndarray:
    """Ring points of a run at its end (or at station s)."""
    yz, _ = _ring_yz(run, size)
    s = _end_s(run, end) if s is None else s
    return run.origin + s * run.ex + yz[:, :1] * run.ey + yz[:, 1:] * run.ez


def _near_s(run: _Run, j: _Joint) -> float:
    """Station of a through joint's branch at the near wall's mid-surface."""
    return j.dist if j.branch_end == 0 else run.length - j.dist


def _set_forced(runs: list[_Run], joints: list[_Joint], size: float) -> None:
    """Grid lines on the host tubes through the ring nodes of shared joints (iterated to a fixed point)."""
    for _ in range(10):
        new_s = {r.id: set() for r in runs}
        new_c = {r.id: [set(), set(), set(), set()] for r in runs}
        for j in joints:
            if j.kind == "tied":
                continue
            host, br = runs[j.host], runs[j.branch]
            near = _near_s(br, j) if j.through else None
            pts = _ring_points(br, j.branch_end, size, near)  # from the branch's current lines
            ss, cs = [], []
            for p in pts:
                s, y, z = host.local(p)
                ss.append(s)
                cs.append(host.wall_of(y, z)[1])
            j.s_set, j.c_set = _key(ss), _key(cs)
            j.footprint = (min(ss), max(ss), min(cs), max(cs))
            new_s[host.id].update(j.s_set)
            new_c[host.id][j.wall].update(j.c_set)
            if j.through:
                cs = [host.wall_of(*host.local(p)[1:])[1] for p in _ring_points(br, j.branch_end, size)]
                j.far_c_set = _key(cs)
                j.far_footprint = (min(ss), max(ss), min(cs), max(cs))
                new_c[host.id][j.far_wall].update(j.far_c_set)
        changed = any(new_s[r.id] != r.forced_s or new_c[r.id] != r.forced_c for r in runs)
        for r in runs:
            r.forced_s, r.forced_c = new_s[r.id], new_c[r.id]
        if not changed:
            return
    raise DesignError("the joint grid lines do not settle (tubes branch into each other in a loop); "
                      "simplify the joints or report the design")


def _clashes(runs: list[_Run], joints: list[_Joint]) -> list[_Joint]:
    """Shared joints whose footprint is crossed by grid lines that are not its own."""
    bad = []
    for j in joints:
        if j.kind == "tied":
            continue
        host = runs[j.host]
        s0, s1, c0, c1 = j.footprint
        own_s, own_c = set(j.s_set), set(j.c_set)
        if any(s0 + TOL < s < s1 - TOL and s not in own_s for s in _key(host.forced_s | host.extra_s)):
            bad.append(j)
            continue
        if any(c0 + TOL < c < c1 - TOL and c not in own_c for c in _key(host.forced_c[j.wall])):
            bad.append(j)
            continue
        if j.through:
            f0, f1 = j.far_footprint[2:]
            if any(f0 + TOL < c < f1 - TOL and c not in set(j.far_c_set) for c in _key(host.forced_c[j.far_wall])):
                bad.append(j)
                continue
        lo, hi = host.s_range()
        if s0 < lo - TOL or s1 > hi + TOL or c0 < -TOL or c1 > host.wall_lengths()[j.wall] + TOL:
            bad.append(j)
    return bad


def _fit_through(runs: list[_Run], joints: list[_Joint]) -> bool:
    """Through joints whose footprint reaches an edge of the wall go back to plain T joints; True if any did."""
    changed = False
    for j in joints:
        if not j.through or j.kind == "tied":
            continue
        host = runs[j.host]
        Lw = host.wall_lengths()
        c0, c1 = j.footprint[2:]
        f0, f1 = j.far_footprint[2:]
        if c0 <= TOL or c1 >= Lw[j.wall] - TOL or f0 <= TOL or f1 >= Lw[j.far_wall] - TOL:
            j.through = False
            runs[j.branch].ext[j.branch_end] = -j.dist
            j.note = "the branch is as wide as the wall it meets, so its walls would lie in that tube's walls"
            changed = True
    return changed


def _untie_through(runs: list[_Run], j: _Joint) -> None:
    """A through joint that becomes tied: the branch stops at the near wall again."""
    if j.through:
        j.through = False
        runs[j.branch].ext[j.branch_end] = -j.dist


def _gusset_parts(design: dict) -> list[dict]:
    return [p for p in design.get("parts", []) if p.get("kind") == "gusset"]


def _place_gussets(runs: list[_Run], joints: list[_Joint], parts: list[dict], size: float) -> list[_Gusset]:
    """Gusset geometry from the parts, and the grid lines they need (run.extra_s, with the through joints' lines)."""
    for r in runs:
        r.extra_s = set()
    for j in joints:
        if j.through and j.kind != "tied":
            runs[j.branch].extra_s.add(round(_near_s(runs[j.branch], j), 9))
    out = []
    for p in parts:
        carried = p.get("carried_by") or []
        if len(carried) != 1:
            raise DesignError(f"gusset {p['id']!r} must be carried by exactly one frame node (its joint), "
                              f"it names {len(carried)}")
        node = carried[0]
        c = np.asarray(p["box"]["center_mm"], float)
        cands = []
        for j in joints:
            if j.node != node or j.kind != "T":
                continue
            host, br = runs[j.host], runs[j.branch]
            n0 = host.origin + host.nodes_at[node] * host.ex
            u_in = _end_dir(br, j.branch_end)
            if (c - n0) @ u_in > 0:
                cands.append((j, host, br, n0, u_in))
        if not cands:
            raise DesignError(f"gusset {p['id']!r}: node {node!r} has no T joint (a tube ending square on the side "
                              f"of a rect tube, sharing its ring) on the side of the gusset's centre; put gussets "
                              f"at T joints")
        j, host, br, n0, u_in = cands[0]
        u_s = host.ex if (c - n0) @ host.ex >= 0 else -host.ex
        u_n = np.cross(host.ex, u_in)
        u_n /= np.linalg.norm(u_n)
        if (c - n0) @ u_n < 0:
            u_n = -u_n
        g_s, g_in, t = (float(v) for v in p["box"]["size_mm"])
        if min(g_s, g_in, t) <= 0:
            raise DesignError(f"gusset {p['id']!r} needs positive legs and thickness, got size_mm {p['box']['size_mm']}")
        O = n0 + u_in * j.dist + u_s * br.half_extent(u_s) + u_n * br.half_extent(u_n)
        want = O + u_s * g_s / 3.0 + u_in * g_in / 3.0
        if np.linalg.norm(want - c) > 1.0:
            raise DesignError(f"gusset {p['id']!r}: its centre {np.round(c, 2).tolist()} is not the centroid of a "
                              f"plate at the corner of joint {node!r} ({np.round(want, 2).tolist()} for these legs)")
        n = max(1, math.ceil(math.hypot(g_s, g_in) / 2.0 / size - 1e-9))  # half the hypotenuse is the longest side
        k = np.arange(1, 2 * n + 1) / (2 * n)
        hs = host.local(O)[0] + (u_s @ host.ex) * g_s * k
        bs = br.local(O)[0] + (u_in @ br.ex) * g_in * k
        for run, ss, leg in ((host, hs, "along the tube it sits on"), (br, bs, "along the branch")):
            lo, hi = run.s_range()
            if ss.min() < lo - TOL or ss.max() > hi + TOL:
                raise DesignError(f"gusset {p['id']!r}: its leg {leg} runs past the end of the tube; make it shorter")
            run.extra_s.update(round(float(s), 9) for s in ss)
        out.append(_Gusset(p["id"], j, O, u_s, u_in, u_n, g_s, g_in, t, n))
    return out


# the three quads of a gusset in triangle coordinates (a along u_s, b along u_in): corner, mid-points, centroid
_TRI_QUADS = [[(0.0, 0.0), (0.5, 0.0), (1 / 3, 1 / 3), (0.0, 0.5)],
              [(0.5, 0.0), (1.0, 0.0), (0.5, 0.5), (1 / 3, 1 / 3)],
              [(0.0, 0.5), (1 / 3, 1 / 3), (0.5, 0.5), (0.0, 1.0)]]


def _gusset_grid(g: _Gusset) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Points (P, 3), quads (Q, 4) into them (counter-clockwise seen from the top side) and which points lie on a leg."""
    pts, quads, on_leg = [], [], []
    n = g.n
    flip = np.cross(g.u_s, g.u_in) @ g.normal < 0
    for corners in _TRI_QUADS:
        cab = np.array(corners)
        u = np.arange(n + 1) / n
        uu, vv = np.meshgrid(u, u, indexing="ij")  # uu along corner 0 -> 1, vv along corner 0 -> 3
        ab = ((1 - uu)[..., None] * (1 - vv)[..., None] * cab[0] + uu[..., None] * (1 - vv)[..., None] * cab[1]
              + uu[..., None] * vv[..., None] * cab[2] + (1 - uu)[..., None] * vv[..., None] * cab[3])
        base = sum(len(p) for p in pts)
        idx = base + np.arange((n + 1) ** 2).reshape(n + 1, n + 1)
        i, k = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        q = np.stack([idx[i, k], idx[i + 1, k], idx[i + 1, k + 1], idx[i, k + 1]], axis=-1).reshape(-1, 4)
        if flip:
            q = q[:, ::-1]
        ab = ab.reshape(-1, 2)
        pts.append(g.O + ab[:, :1] * g.g_s * g.u_s + ab[:, 1:] * g.g_in * g.u_in)
        on_leg.append((np.abs(ab[:, 0]) < 1e-12) | (np.abs(ab[:, 1]) < 1e-12))
        quads.append(q)
    return np.vstack(pts), np.vstack(quads), np.concatenate(on_leg)


def build_mesh(design: dict, element_size_mm: float = ELEMENT_SIZE_MM) -> ShellMesh:
    """Mesh the mid-surfaces of the design's tube members (see the module notes)."""
    size = float(element_size_mm)
    if not size > 0:
        raise ValueError("element_size_mm must be positive")
    mem = _members(design)
    runs, ends = _runs(design, mem)
    joints = _classify(runs, ends, through_mode(design))
    gparts = _gusset_parts(design)
    run_through = {n: r for r in runs for n in r.nodes_at if n not in (r.start_node, r.end_node)}
    mount_nodes = [s.get("frame_node") for s in design.get("supports", []) if s.get("frame_node") in run_through]
    gussets: list[_Gusset] = []
    for _ in range(2 * len(joints) + 2):
        _set_forced(runs, joints, size)
        if _fit_through(runs, joints):
            continue
        gussets = _place_gussets(runs, joints, gparts, size)
        _mount_lines(run_through, mount_nodes, joints)
        bad = _clashes(runs, joints)
        if not bad:
            break
        for j in bad:
            _untie_through(runs, j)
            j.kind = "tied"
            j.reason = "its grid lines would clash with another joint's"

    # ---- grids per run
    all_nodes, grids, run_elems = [], [], []
    offset = 0
    for r in runs:
        lo, hi = r.s_range()
        foot = [(j.footprint[0], j.footprint[1]) for j in joints if j.kind != "tied" and j.host == r.id]
        member_lines = [s for s in r.nodes_at.values()
                        if lo < s < hi and not any(a - TOL <= s <= b + TOL for a, b in foot)]
        ss = _divide([lo, hi, *[s for s in r.forced_s | r.extra_s if lo < s < hi], *member_lines], size)
        yz, _ = _ring_yz(r, size)
        P = (r.origin[None, None, :] + ss[:, None, None] * r.ex + yz[None, :, :1] * r.ey + yz[None, :, 1:] * r.ez)
        ns, nc = len(ss), len(yz)
        idx = offset + np.arange(ns * nc).reshape(ns, nc)
        i, jj = np.meshgrid(np.arange(ns - 1), np.arange(nc), indexing="ij")
        j1 = (jj + 1) % nc
        el = np.stack([idx[i, jj], idx[i, j1], idx[i + 1, j1], idx[i + 1, jj]], axis=-1).reshape(-1, 4)
        all_nodes.append(P.reshape(-1, 3))
        grids.append((ss, idx))
        run_elems.append(el)
        offset += ns * nc
    X = np.vstack(all_nodes)

    # ---- holes in the near walls of through joints
    hole_count: dict[str, int] = defaultdict(int)
    for j in joints:
        if not j.through or j.kind == "tied":
            continue
        host = runs[j.host]
        el = run_elems[host.id]
        loc = (X[el].mean(axis=1) - host.origin) @ np.stack([host.ex, host.ey, host.ez]).T
        w = np.array([host.wall_of(y, z) for y, z in loc[:, 1:]])
        s0, s1, c0, c1 = j.footprint
        hole = ((w[:, 0] == j.wall) & (loc[:, 0] > s0 + TOL) & (loc[:, 0] < s1 - TOL)
                & (w[:, 1] > c0 + TOL) & (w[:, 1] < c1 - TOL))
        run_elems[host.id] = el[~hole]
        hole_count[j.node] += int(hole.sum())

    # ---- gusset plates (their leg nodes must fall on tube nodes)
    n_tube = len(X)
    gus_elems, gus_legs = [], []
    for g in gussets:
        P, Q, leg = _gusset_grid(g)
        gus_elems.append(Q + len(X))
        gus_legs.append(len(X) + np.nonzero(leg)[0])
        X = np.vstack([X, P])

    # ---- merge coincident nodes (shared joint rings)
    parent = np.arange(len(X))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in cKDTree(X).query_pairs(r=1e-4):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    roots = np.array([find(i) for i in range(len(X))])
    for g, legs in zip(gussets, gus_legs):
        loose = int(np.sum(roots[legs] >= n_tube))
        if loose:
            raise DesignError(f"gusset {g.id!r}: {loose} nodes of its legs do not fall on the tube walls' grid, so it "
                              f"would not be welded on; report the design")
    uniq, new_of = np.unique(roots, return_inverse=True)
    nodes = X[uniq]
    elements_per_run = [new_of[el] for el in run_elems]
    gus_el = [new_of[q] for q in gus_elems]
    # drop nodes left without elements (inside the holes of through joints)
    used = np.zeros(len(nodes), bool)
    used[np.concatenate([e.ravel() for e in elements_per_run + gus_el])] = True
    remap = -np.ones(len(nodes), int)
    remap[used] = np.arange(int(used.sum()))
    nodes = nodes[used]
    grids = [(ss, remap[new_of[idx]]) for ss, idx in grids]
    elements_per_run = [remap[el] for el in elements_per_run]
    gus_el = [remap[q] for q in gus_el]

    # ---- element member, stations
    elements = np.vstack(elements_per_run + gus_el)
    centres = nodes[elements].mean(axis=1)
    element_member: list[str] = []
    run_first = []
    for r, el in zip(runs, elements_per_run):
        run_first.append(len(element_member))
        c = nodes[el].mean(axis=1)
        s = (c - r.origin) @ r.ex
        bounds = np.array([r.member_s[m][1] for m in r.members[:-1]])
        k = np.searchsorted(bounds, s)
        element_member.extend(r.members[i] for i in k)
    for g, q in zip(gussets, gus_el):
        element_member.extend([g.id] * len(q))
    st_s = np.empty(len(elements))
    st_yz = np.empty((len(elements), 2))
    for mid, m in mem.items():
        sel = np.array([em == mid for em in element_member])
        rel = centres[sel] - m.a
        st_s[sel] = rel @ m.d
        st_yz[sel] = np.stack([rel @ m.ey, rel @ m.ez], axis=1)
    for g in gussets:
        sel = np.array([em == g.id for em in element_member])
        rel = centres[sel] - g.O
        st_s[sel] = rel @ g.u_s
        st_yz[sel] = np.stack([rel @ g.u_in, rel @ g.normal], axis=1)

    # ---- rings, joints, links
    run_of = {mid: r for r in runs for mid in r.members}
    node_of = {m["id"]: m for m in design["members"]}
    end_rings: dict[tuple[str, str], np.ndarray] = {}
    for mid in mem:
        r = run_of[mid]
        ss, idx = grids[r.id]
        for end in ("a", "b"):
            n = node_of[mid]["node_" + end]
            if n == r.start_node:
                ring = idx[0]
            elif n == r.end_node:
                ring = idx[-1]
            else:
                ring = idx[int(np.argmin(np.abs(ss - r.nodes_at[n])))]
            end_rings[(mid, end)] = ring.copy()

    links: list[tuple[int, int]] = []
    slaves: set[int] = set()
    joint_nodes: dict[str, set] = defaultdict(set)
    joint_kinds: dict[str, str] = {}
    shared_rings: dict[str, list] = defaultdict(list)
    notes_T, notes_L, notes_tied, notes_through = [], [], [], []
    for node, es in ends.items():
        for mid, end in es:
            joint_nodes[node].update(int(i) for i in end_rings[(mid, end)])
        joint_kinds[node] = "open end" if len(es) == 1 else "through"
    for j in joints:
        host, br = runs[j.host], runs[j.branch]
        ring = grids[br.id][1][0 if j.branch_end == 0 else -1]
        h_ss, h_idx = grids[host.id]
        if j.kind == "tied":
            cand = np.unique(h_idx.ravel())
            cand = np.array([c for c in cand if c >= 0 and c not in slaves])
            tree = cKDTree(nodes[cand])
            _, k = tree.query(nodes[ring])
            for s_node, m_node in zip(ring, cand[k]):
                if s_node != m_node and s_node not in slaves:
                    links.append((int(s_node), int(m_node)))
                    slaves.add(int(s_node))
                    joint_nodes[j.node].add(int(m_node))
            joint_kinds[j.node] = "tied"
            notes_tied.append(f"{j.node} ({br.members[0 if j.branch_end == 0 else -1]} to "
                              f"{host.members[0]}: {j.reason})")
            continue
        s0, s1, c0, c1 = j.footprint
        walls = np.array(_ring_yz(host, size)[1])
        in_s = (h_ss >= s0 - TOL) & (h_ss <= s1 + TOL)
        bm = br.members[0 if j.branch_end == 0 else -1]

        def patch_on(wall, ca, cb):
            on = (walls[:, 0] == wall) & (walls[:, 1] >= ca - TOL) & (walls[:, 1] <= cb + TOL)
            p = h_idx[np.ix_(in_s, on)].ravel()
            return p[p >= 0]

        patch = patch_on(j.wall, c0, c1)
        if j.through:
            b_ss, b_idx = grids[br.id]
            near_ring = b_idx[int(np.argmin(np.abs(b_ss - _near_s(br, j))))]
            far_patch = patch_on(j.far_wall, *j.far_footprint[2:])
            joint_nodes[j.node].update(int(i) for i in near_ring)
            joint_nodes[j.node].update(int(i) for i in far_patch)
            shared_rings[j.node].append((bm, near_ring.copy(), patch.copy()))
            shared_rings[j.node].append((bm, ring.copy(), far_patch.copy()))
        else:
            shared_rings[j.node].append((bm, ring.copy(), patch.copy()))
        joint_nodes[j.node].update(int(i) for i in patch)
        joint_kinds[j.node] = j.kind if joint_kinds.get(j.node) != "tied" else "tied"
        wall_name = ("+y", "+z", "-y", "-z")[j.wall]
        if j.through:
            notes_through.append(f"{j.node} ({bm} through the {wall_name} wall of {host.members[0]}..{host.members[-1]}, "
                                 f"{hole_count[j.node]} elements of that wall left out for the hole)")
        elif j.kind == "T":
            notes_T.append(f"{j.node} ({bm} on the {wall_name} wall of {host.members[0]}..{host.members[-1]}"
                           + (f"; not through: {j.note}" if j.note else "") + ")")
        else:
            other = host.members[0 if (host.start_node == j.node) else -1]
            notes_L.append(f"{j.node} ({other} extended to the outer wall of {bm}; {bm} ends on its {wall_name} wall)")

    notes = [f"Mid-surface shell mesh, target element edge {size:g} mm; each interval between fixed lines "
             f"(corners, joint lines, member ends) cut into ceil(length / {size:g}) parts.",
             "Straight members with the same section are meshed as one tube through their nodes.",
             "Wall directions +y/+z/-y/-z are the K1 section axes of the tube (y = h direction, z = b direction)."]
    if notes_T:
        notes.append("T joints, ring nodes shared with the wall: " + "; ".join(notes_T) + ".")
    if notes_through:
        notes.append("Through joints (design variable cross_members_through = 1): the branch's four walls go on through "
                     "a hole in the near wall to the mid-surface of the far wall, sharing its ring nodes with both "
                     "walls: " + "; ".join(notes_through) + ". The beam model (K1) is unchanged: its members still "
                     "meet at the node.")
    if notes_L:
        notes.append("L joints (larger section extended), ring nodes shared: " + "; ".join(notes_L) + ".")
    if notes_tied:
        notes.append("Not shared; each ring node tied by a rigid link (RBE2 type) to the nearest node of the other tube: "
                     + "; ".join(notes_tied) + ".")
    if any(r.shape == "round_tube" for r in runs):
        notes.append("Round tubes are regular polygons with the corners placed so the wall keeps the circle's "
                     "second moment of area.")
    part_material = {}
    for g in gussets:
        br = runs[g.joint.branch]
        part_material[g.id] = br.members[0 if g.joint.branch_end == 0 else -1]
    if gussets:
        notes.append(f"Gussets (parts of kind gusset, {len(gussets)}): flat right-triangle plates in the plane of the "
                     f"branch's upper or lower wall at T joints, legs welded (nodes shared) along the through tube's wall "
                     f"and the branch's corner line, meshed as three quads cut n x n; material of the branch member. "
                     f"The beam model (K1) carries them as mass only.")

    # ---- wheel mounts: the through tube's walls within +- its height of the support's frame node
    mounts, mount_elements, mount_half = {}, {}, {}
    for fn in mount_nodes:
        r = run_through[fn]
        ss, idx = grids[r.id]
        rows = np.abs(ss - r.nodes_at[fn]) <= r.height + TOL
        nset = idx[rows].ravel()
        nset = np.unique(nset[nset >= 0])
        el = elements_per_run[r.id]
        inside = np.all(np.isin(el, nset), axis=1)
        mounts[fn] = nset
        mount_elements[fn] = run_first[r.id] + np.nonzero(inside)[0]
        mount_half[fn] = r.height
    if mounts:
        notes.append("Wheel mounts: " + "; ".join(f"{fn} (walls of {run_through[fn].members[0]}..{run_through[fn].members[-1]} "
                                                    f"within +- {mount_half[fn]:g} mm, its height)" for fn in mounts) + ".")

    return ShellMesh(
        nodes=nodes, elements=elements, element_member=element_member,
        member_thickness_mm={**{mid: m.t for mid, m in mem.items()}, **{g.id: g.t for g in gussets}},
        element_size_mm=size,
        station_s_mm=st_s, station_yz_mm=st_yz, end_rings=end_rings,
        joints={k: np.array(sorted(v)) for k, v in joint_nodes.items()}, joint_kinds=joint_kinds,
        links=links, notes=notes, shared_rings=dict(shared_rings),
        member_axes={**{mid: np.stack([m.d, m.ey, m.ez]) for mid, m in mem.items()},
                     **{g.id: np.stack([g.u_s, g.u_in, g.normal]) for g in gussets}},
        mounts=mounts, mount_elements=mount_elements, mount_half_mm=mount_half, part_material=part_material,
        through_joints=sorted({j.node for j in joints if j.through and j.kind != "tied"}),
    )


def through_mode(design: dict) -> bool:
    """The design variable cross_members_through (0 or 1; absent: 0)."""
    v = (design.get("variables") or {}).get("cross_members_through")
    if v is None:
        return False
    x = v.get("value") if isinstance(v, dict) else v
    if x not in (0, 1):
        raise DesignError(f"design variable cross_members_through must be 0 or 1, got {x!r}")
    return x == 1


def _mount_lines(run_through: dict, mount_nodes: list[str], joints: list[_Joint]) -> None:
    """Grid lines at +- the tube height from each wheel mount node, where they cross no joint footprint."""
    for fn in mount_nodes:
        r = run_through[fn]
        lo, hi = r.s_range()
        foot = [j.footprint for j in joints if j.kind != "tied" and j.host == r.id and j.footprint]
        for s in (r.nodes_at[fn] - r.height, r.nodes_at[fn] + r.height):
            if lo + TOL < s < hi - TOL and not any(f[0] - TOL <= s <= f[1] + TOL for f in foot):
                r.extra_s.add(round(float(s), 9))

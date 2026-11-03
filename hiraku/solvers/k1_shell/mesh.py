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
        """The contract `mesh` record."""
        return {
            "mesh_id": mesh_id,
            "design_version": int(design_version),
            "element_type": "shell",
            "formulation": FORMULATION,
            "element_size_mm": float(self.element_size_mm),
            "nodes_mm": [[float(v) for v in p] for p in self.nodes],
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
        else:
            r.R = (first.dims["d"] - first.t) / 2.0
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


def _classify(runs: list[_Run], ends: dict) -> list[_Joint]:
    """Joint kinds at every node, with the run ends extended or cut back to suit."""
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
                    r.ext[e] = -dist
                    joints.append(_Joint(node, "T", r.id, e, host.id, wall))
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


def _ring_points(run: _Run, end: int, size: float) -> np.ndarray:
    yz, _ = _ring_yz(run, size)
    s = _end_s(run, end)
    return run.origin + s * run.ex + yz[:, :1] * run.ey + yz[:, 1:] * run.ez


def _set_forced(runs: list[_Run], joints: list[_Joint], size: float) -> None:
    """Grid lines on the host tubes through the ring nodes of shared joints (iterated to a fixed point)."""
    for _ in range(10):
        new_s = {r.id: set() for r in runs}
        new_c = {r.id: [set(), set(), set(), set()] for r in runs}
        for j in joints:
            if j.kind == "tied":
                continue
            host, br = runs[j.host], runs[j.branch]
            pts = _ring_points(br, j.branch_end, size)  # from the branch's current lines
            ss, cs = [], []
            for p in pts:
                s, y, z = host.local(p)
                ss.append(s)
                cs.append(host.wall_of(y, z)[1])
            j.s_set, j.c_set = _key(ss), _key(cs)
            j.footprint = (min(ss), max(ss), min(cs), max(cs))
            new_s[host.id].update(j.s_set)
            new_c[host.id][j.wall].update(j.c_set)
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
        if any(s0 + TOL < s < s1 - TOL and s not in own_s for s in _key(host.forced_s)):
            bad.append(j)
            continue
        if any(c0 + TOL < c < c1 - TOL and c not in own_c for c in _key(host.forced_c[j.wall])):
            bad.append(j)
            continue
        lo, hi = host.s_range()
        if s0 < lo - TOL or s1 > hi + TOL or c0 < -TOL or c1 > host.wall_lengths()[j.wall] + TOL:
            bad.append(j)
    return bad


def build_mesh(design: dict, element_size_mm: float = ELEMENT_SIZE_MM) -> ShellMesh:
    """Mesh the mid-surfaces of the design's tube members (see the module notes)."""
    size = float(element_size_mm)
    if not size > 0:
        raise ValueError("element_size_mm must be positive")
    mem = _members(design)
    runs, ends = _runs(design, mem)
    joints = _classify(runs, ends)
    for _ in range(len(joints) + 1):
        _set_forced(runs, joints, size)
        bad = _clashes(runs, joints)
        if not bad:
            break
        for j in bad:
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
        ss = _divide([lo, hi, *[s for s in r.forced_s if lo < s < hi], *member_lines], size)
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
    uniq, new_of = np.unique(roots, return_inverse=True)
    nodes = X[uniq]
    grids = [(ss, new_of[idx]) for ss, idx in grids]
    elements_per_run = [new_of[el] for el in run_elems]

    # ---- element member, stations
    elements = np.vstack(elements_per_run)
    centres = nodes[elements].mean(axis=1)
    element_member: list[str] = []
    for r, el in zip(runs, elements_per_run):
        c = nodes[el].mean(axis=1)
        s = (c - r.origin) @ r.ex
        bounds = np.array([r.member_s[m][1] for m in r.members[:-1]])
        k = np.searchsorted(bounds, s)
        element_member.extend(r.members[i] for i in k)
    st_s = np.empty(len(elements))
    st_yz = np.empty((len(elements), 2))
    for mid, m in mem.items():
        sel = np.array([em == mid for em in element_member])
        rel = centres[sel] - m.a
        st_s[sel] = rel @ m.d
        st_yz[sel] = np.stack([rel @ m.ey, rel @ m.ez], axis=1)

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
    notes_T, notes_L, notes_tied = [], [], []
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
            cand = np.array([c for c in cand if c not in slaves])
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
        hy = np.array([host.local(p)[1:] for p in nodes[h_idx[0]]])
        walls = np.array([host.wall_of(y, z) for y, z in hy])
        on_wall = (walls[:, 0] == j.wall) & (walls[:, 1] >= c0 - TOL) & (walls[:, 1] <= c1 + TOL)
        in_s = (h_ss >= s0 - TOL) & (h_ss <= s1 + TOL)
        patch = h_idx[np.ix_(in_s, on_wall)].ravel()
        joint_nodes[j.node].update(int(i) for i in patch)
        joint_kinds[j.node] = j.kind if joint_kinds.get(j.node) != "tied" else "tied"
        shared_rings[j.node].append((br.members[0 if j.branch_end == 0 else -1], ring.copy(), patch.copy()))
        bm = br.members[0 if j.branch_end == 0 else -1]
        wall_name = ("+y", "+z", "-y", "-z")[j.wall]
        if j.kind == "T":
            notes_T.append(f"{j.node} ({bm} on the {wall_name} wall of {host.members[0]}..{host.members[-1]})")
        else:
            other = host.members[0 if (host.start_node == j.node) else -1]
            notes_L.append(f"{j.node} ({other} extended to the outer wall of {bm}; {bm} ends on its {wall_name} wall)")

    notes = [f"Mid-surface shell mesh, target element edge {size:g} mm; each interval between fixed lines "
             f"(corners, joint lines, member ends) cut into ceil(length / {size:g}) parts.",
             "Straight members with the same section are meshed as one tube through their nodes.",
             "Wall directions +y/+z/-y/-z are the K1 section axes of the tube (y = h direction, z = b direction)."]
    if notes_T:
        notes.append("T joints, ring nodes shared with the wall: " + "; ".join(notes_T) + ".")
    if notes_L:
        notes.append("L joints (larger section extended), ring nodes shared: " + "; ".join(notes_L) + ".")
    if notes_tied:
        notes.append("Not shared; each ring node tied by a rigid link (RBE2 type) to the nearest node of the other tube: "
                     + "; ".join(notes_tied) + ".")
    if any(r.shape == "round_tube" for r in runs):
        notes.append("Round tubes are regular polygons with the corners placed so the wall keeps the circle's "
                     "second moment of area.")
    notes.append("Open tube ends are not closed; corners are sharp (no corner radius); welds are not modelled "
                 "(shared nodes are continuous).")

    return ShellMesh(
        nodes=nodes, elements=elements, element_member=element_member,
        member_thickness_mm={mid: m.t for mid, m in mem.items()}, element_size_mm=size,
        station_s_mm=st_s, station_yz_mm=st_yz, end_rings=end_rings,
        joints={k: np.array(sorted(v)) for k, v in joint_nodes.items()}, joint_kinds=joint_kinds,
        links=links, notes=notes, shared_rings=dict(shared_rings),
        member_axes={mid: np.stack([m.d, m.ey, m.ez]) for mid, m in mem.items()},
    )

"""A design's frame as shells, one tube at a time, joined by ties; loads from K2 at the suspension mounts.

Meshing (frame_shells):
- Members that continue in a straight line (within COLLINEAR_DEG) with the same section through a node
  are one tube. At a node where two such lines cross (the middle of an X brace) only one stays a
  through tube (the larger section, then the one listed first); the other is two tubes that end there.
  A through tube smaller than a tube that ends at the node is split too, so the host of a joint is
  never smaller than its branches.
- Each tube is a structured grid of MITC4 quads on its wall's mid-surface: round tubes as a regular
  polygon (a multiple of 4 facets, at least 8, edges near the element size) whose corners sit at
  k1_shell.mesh.facet_radius_factor times the mid-surface radius (the faceted wall has the circle's
  second moment of area, as k1_shell meshes them; facet=False puts the corners on the circle, as
  fe.checks' check 9 does); rect tubes as the (b - t) x (h - t) mid-line rectangle, with K1's
  section axes (k1_shell.mesh.k1_axes: h along ey, b along ez). Quads counter-clockwise seen from
  outside. Along the tube every generator line (a corner of the section polygon) is cut into the
  same number of equal pieces between its two end cuts, so a cut that is not square (a saddle)
  skews the first rows.
- Joints, at each design node:
  * a node inside a through tube: that tube is the host; every tube ending there is a branch;
  * no through tube, one end: an open end, cut square;
  * no through tube, two ends of one round section at a kink under MITRE_MAX_DEG: a mitre, both cut on
    the bisector plane, the second's end ring tied to the first;
  * otherwise the end of the largest section (then the one whose axis makes the largest least angle
    with the others) is the host: it runs on past the node by the length the branches' footprints
    need plus one element (a stub of tube that the beam model does not have; its mass is reported);
    the others are branches.
  A branch is cut where each generator line meets the host wall's mid-surface (the true circle of
  radius (d - t) / 2, or the mid-line rectangle): a saddle for an oblique round tube. Its end ring is
  tied (fe.connect.tie, translations and rotations) to the host's wall faces near the node. Branches
  that meet the host side by side may overlap each other near the wall; they are tied to the host,
  not to each other (assumption: the weld cluster is not modelled). A branch under MIN_BRANCH_DEG to
  its host is refused.
- Weld strips (weld_strips=True, the default; for hot_spots, the IIW structural hot-spot stress by
  k1_shell.stress.hot_spots' rule): a tied end's first two rows lie one and two wall thicknesses from the
  cut, square to it (strip_steps: t / cos of the cut's slant on each generator line); a host's grid has
  lines at each footprint's four extremes (along the host and around it) and t and 2t beyond them; for a
  rect branch also k1_shell's corner lines at t, 2t and 3t from the footprint's corners (on the host's and
  the branch's wall). Grid lines closer than MERGE_T t are merged, and a ring node that falls on a host
  node shares it. A round host's facets are then uneven (their corners stay at facet_radius_factor of the
  total count, an approximation of the circle's second moment of area).
- Round branches (weld strips): the crown and saddle are the true extremes of the intersection
  (true_extremes); the host has lines at them and t/2 either side of each, the branch's ring has generator
  lines t/2 either side, so each is read on strip elements t long centred on the true point. A round tube has
  at least HOT_SPOT_MIN_FACETS facets, and round each joint the rows are a facet apart for a radius (on the
  host beyond the footprint's strips, on the branch beyond its strips; joint zone, zone_step), so the mesh at
  the joint does not follow the element size. The facet count sets the read: on a round T at 40 facets it is
  up to 7 % below the 96-facet value (not on the safe side; RoundConvergenceTest).
- Mounts: a hardpoint's node is joined by a distributing link (RBE3, fe.connect.distributing_link) to
  the host wall's nodes within PATCH_FACTOR x the host's radius of the hardpoint along the host and
  around it (assumption: a bracket about as long as the tube is wide).

Loads (body_loads, case_loads): what the sprung body of K2 (hiraku.solvers.k2_vehicle) receives in one
load state (Sim.loads, via shelf.step.load_state): at each double wishbone mount the force of
shelf.step.corner_mounts, each corner's drive and brake reaction (applied as a couple on that corner's
lower arm mounts), and for every sprung mass item of K2 (part boxes, frame members; wheel kinds left
out, suspension kinds at half, as K2's model._unsprung) its weight and d'Alembert force
m (g - a - alpha x r - omega x (omega x r)) and moment -(I alpha + omega x I omega), with r from the
sprung centre of mass. World vectors are turned back into the design's axes with the state's rot and
origin. These add up to zero to the accuracy of K2's own balance; the frame shell is held 3-2-1 at
three mounts (no other support), so the support reactions are the part of the loads that does not
balance (load_balance_residual). The frame members' own items are spread over their tube's nodes, a
part's over the patches of its carried_by nodes (the nearest corner's mounts when it has none), all
with the RBE3 weights (fe.connect.rbe3_forces), which keep each item's force and moment.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass, field

import numpy as np

from ..k1_shell.mesh import CORNER_LINES, facet_radius_factor, k1_axes
from ..k1_shell.stress import CORNER_SKIP_T, HOT_SPOT_READ_T, axis_name, extrapolate_hot_spot
from .connect import distributing_link, rbe3_forces, rigid_link, tie
from .model import FEModel, StaticSolver
from .shell import MITC4

COLLINEAR_DEG = 0.5
MITRE_MAX_DEG = 35.0
MIN_BRANCH_DEG = 12.0
PATCH_FACTOR = 1.0
ELEMENT_SIZE_MM = 20.0
MIN_ROUND_FACETS = 8
MERGE_T = 0.4  # weld strips: grid lines closer than this many wall thicknesses are merged
# weld strips: a round tube has at least this many facets whatever the element size. The saddle's hot spot is the
# host wall's ring bending, set by the facet count more than by the element length: round T 42.7 x 2.3 / 34 x 2.3
# at 90 deg (14 mm), host side 225.65 / 228.90 / 229.81 / 231.88 / 236.43 MPa at 32 / 40 / 48 / 64 / 96 facets,
# branch side 179.24 / 181.19 / 184.44 / 184.55 / 194.06 MPa (the cost of the frame's shell grows with it)
HOT_SPOT_MIN_FACETS = 40


class TubeMeshError(ValueError):
    pass


@dataclass
class Tube:
    id: str
    members: list
    nodes: list  # design node ids along the tube, start to end
    p0: np.ndarray
    p1: np.ndarray
    shape: str
    dims: dict
    section: str
    material: str
    ends: dict = field(default_factory=dict)  # "a" | "b" -> {"kind", "node", "host", "ext", "mate"}
    ids: np.ndarray | None = None  # (rows + 1, n_c) global node ids
    quads: np.ndarray | None = None
    group: object = None
    zone_facets: int = HOT_SPOT_MIN_FACETS  # weld strips: a joint zone's row spacing is a facet at this count
    # (frame_shells sets it from hot_spot_facets)

    @property
    def d(self) -> np.ndarray:
        v = self.p1 - self.p0
        return v / np.linalg.norm(v)

    @property
    def L(self) -> float:
        return float(np.linalg.norm(self.p1 - self.p0))

    @property
    def t(self) -> float:
        return float(self.dims["t"])

    @property
    def R(self) -> float:
        """Mid-surface radius (round) or half the mid-line diagonal (rect)."""
        if self.shape == "round_tube":
            return (self.dims["d"] - self.dims["t"]) / 2.0
        return 0.5 * math.hypot(self.dims["b"] - self.t, self.dims["h"] - self.t)

    @property
    def size(self) -> float:
        return float(self.dims["d"]) if self.shape == "round_tube" else float(max(self.dims["b"], self.dims["h"]))

    def axes(self) -> tuple[np.ndarray, np.ndarray]:
        return k1_axes(self.d)

    def end_point(self, end: str) -> np.ndarray:
        return self.p0 if end == "a" else self.p1


# ---------------------------------------------------------------- tubes from members


def _unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def tubes_of(design: dict, members: list | None = None) -> tuple[dict, dict]:
    """The tubes of a design's frame (see the module docstring) and, per design node, the tubes passing
    through it and the tube ends there: {node: {"through": [tube id], "ends": [(tube id, "a"|"b")]}}."""
    xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    mems = [m for m in design["members"] if members is None or m["id"] in members]
    secs = design["sections"]
    for m in mems:
        if secs[m["section"]]["shape"] not in ("round_tube", "rect_tube"):
            raise TubeMeshError(f"member {m['id']}: only round and rect tubes are meshed, not {secs[m['section']]['shape']}")

    def size(m):
        dm = secs[m["section"]]["dims_mm"]
        return float(dm["d"]) if "d" in dm else float(max(dm["b"], dm["h"]))

    at: dict[str, list] = {}
    for k, m in enumerate(mems):
        for end, other in (("node_a", "node_b"), ("node_b", "node_a")):
            u = _unit(xyz[m[other]] - xyz[m[end]])
            at.setdefault(m[end], []).append((k, u))
    cos_tol = math.cos(math.radians(COLLINEAR_DEG))
    pairs: dict[str, tuple[int, int]] = {}
    for n, inc in at.items():
        cands = []
        for i in range(len(inc)):
            for j in range(i + 1, len(inc)):
                (a, ua), (b, ub) = inc[i], inc[j]
                if mems[a]["section"] == mems[b]["section"] and ua @ ub < -cos_tol:
                    cands.append((-size(mems[a]), min(a, b), a, b))
        if not cands:
            continue
        _, _, a, b = min(cands)
        others = [size(mems[k]) for k, _ in inc if k not in (a, b)]
        if others and max(others) > size(mems[a]) + 1e-9:
            continue  # a branch is bigger: the through tube is split here
        pairs[n] = (a, b)
    # chains: walk from each member along the pairs
    nxt: dict[tuple[int, str], int] = {}
    for n, (a, b) in pairs.items():
        nxt[(a, n)] = b
        nxt[(b, n)] = a
    seen: set[int] = set()
    tubes: dict[str, Tube] = {}

    def far(k: int, n: str) -> str:
        return mems[k]["node_b"] if mems[k]["node_a"] == n else mems[k]["node_a"]

    for k in range(len(mems)):
        if k in seen:
            continue
        # go back to the chain's start through node_a's side
        start_k, start_n = k, mems[k]["node_a"]
        guard = 0
        while (start_k, start_n) in nxt and guard <= len(mems):
            pk = nxt[(start_k, start_n)]
            start_n = far(pk, start_n)
            start_k = pk
            guard += 1
        chain, nodes = [], [start_n]
        cur_k, cur_n = start_k, start_n
        while True:
            chain.append(cur_k)
            seen.add(cur_k)
            cur_n = far(cur_k, cur_n)
            nodes.append(cur_n)
            if (cur_k, cur_n) not in nxt:
                break
            cur_k = nxt[(cur_k, cur_n)]
        m0 = mems[chain[0]]
        sec = secs[m0["section"]]
        tid = mems[chain[0]]["id"] if len(chain) == 1 else "+".join(mems[c]["id"] for c in chain)
        tubes[tid] = Tube(tid, [mems[c]["id"] for c in chain], nodes, xyz[nodes[0]].copy(), xyz[nodes[-1]].copy(),
                          sec["shape"], {k2: float(v) for k2, v in sec["dims_mm"].items()}, m0["section"],
                          m0["material"])
    joints: dict[str, dict] = {}
    for tid, tb in tubes.items():
        for n in tb.nodes[1:-1]:
            joints.setdefault(n, {"through": [], "ends": []})["through"].append(tid)
        joints.setdefault(tb.nodes[0], {"through": [], "ends": []})["ends"].append((tid, "a"))
        joints.setdefault(tb.nodes[-1], {"through": [], "ends": []})["ends"].append((tid, "b"))
    return tubes, joints


def _out_dir(tb: Tube, end: str) -> np.ndarray:
    """Direction from the end's node into the tube."""
    return tb.d if end == "a" else -tb.d


def _angle(u, v) -> float:
    """Angle (deg) between the lines of two directions (0 to 90)."""
    c = abs(float(np.clip(u @ v, -1.0, 1.0)))
    return math.degrees(math.acos(c))


def resolve_joints(tubes: dict, joints: dict) -> dict:
    """Sets every tube end's kind (free, branch, mitre, host) and returns per node its host tube."""
    hosts = {}
    for n, j in joints.items():
        ends = j["ends"]
        if j["through"]:
            host = max(j["through"], key=lambda t: tubes[t].size)
            hosts[n] = host
            for tid, end in ends:
                tubes[tid].ends[end] = {"kind": "branch", "node": n, "host": host}
            continue
        if len(ends) == 1:
            tid, end = ends[0]
            tubes[tid].ends[end] = {"kind": "free", "node": n}
            hosts[n] = tid
            continue
        if len(ends) == 2:
            (t1, e1), (t2, e2) = ends
            a, b = tubes[t1], tubes[t2]
            kink = 180.0 - math.degrees(math.acos(float(np.clip(_out_dir(a, e1) @ _out_dir(b, e2), -1.0, 1.0))))
            if a.section == b.section and a.shape == "round_tube" and kink < MITRE_MAX_DEG:
                a.ends[e1] = {"kind": "mitre", "node": n, "mate": (t2, e2), "master": True}
                b.ends[e2] = {"kind": "mitre", "node": n, "mate": (t1, e1), "master": False}
                hosts[n] = t1
                continue

        def key(te):
            tid, end = te
            u = _out_dir(tubes[tid], end)
            least = min(_angle(u, _out_dir(tubes[o], oe)) for o, oe in ends if (o, oe) != te)
            return (tubes[tid].size, least)

        host, hend = max(ends, key=key)
        hosts[n] = host
        tubes[host].ends[hend] = {"kind": "host", "node": n, "ext": 0.0}
        for tid, end in ends:
            if (tid, end) != (host, hend):
                tubes[tid].ends[end] = {"kind": "branch", "node": n, "host": host}
    for tb in tubes.values():
        for end in ("a", "b"):
            e = tb.ends[end]
            if e["kind"] == "branch":
                ang = _angle(tb.d, tubes[e["host"]].d)
                if ang < MIN_BRANCH_DEG:
                    raise TubeMeshError(f"tube {tb.id} meets {e['host']} at node {e['node']} at {ang:.1f} deg, under "
                                        f"{MIN_BRANCH_DEG:g} deg: no saddle cut")
    return hosts


# ---------------------------------------------------------------- sections and cuts


def perimeter(tb: Tube) -> float:
    """Length of the mid-surface's section line: the circle 2 pi R, or the mid-line rectangle."""
    if tb.shape == "round_tube":
        return 2.0 * math.pi * tb.R
    return 2.0 * (tb.dims["h"] - tb.t) + 2.0 * (tb.dims["b"] - tb.t)


def _rect_half(tb: Tube) -> tuple[float, float]:
    return (tb.dims["h"] - tb.t) / 2.0, (tb.dims["b"] - tb.t) / 2.0


def _rect_corners(tb: Tube) -> np.ndarray:
    """Perimeter coordinates of the mid-line rectangle's corners, from (+hy, -hz) counter-clockwise."""
    hy, hz = _rect_half(tb)
    return np.array([0.0, 2 * hz, 2 * hz + 2 * hy, 4 * hz + 2 * hy])


def perimeter_point(tb: Tube, c) -> np.ndarray:
    """Offsets (n, 3) from the axis of the mid-surface points at perimeter coordinates c (counter-clockwise
    about d from ey; a rect tube's from its corner (+hy, -hz))."""
    ey, ez = tb.axes()
    c = np.mod(np.atleast_1d(np.asarray(c, float)), perimeter(tb))
    if tb.shape == "round_tube":
        a = c / tb.R
        return tb.R * (np.cos(a)[:, None] * ey + np.sin(a)[:, None] * ez)
    hy, hz = _rect_half(tb)
    k = _rect_corners(tb)
    y = np.where(c < k[1], hy, np.where(c < k[2], hy - (c - k[1]), np.where(c < k[3], -hy, -hy + (c - k[3]))))
    z = np.where(c < k[1], -hz + c, np.where(c < k[2], hz, np.where(c < k[3], hz - (c - k[2]), -hz)))
    return y[:, None] * ey + z[:, None] * ez


def perimeter_coord(tb: Tube, w: np.ndarray) -> np.ndarray:
    """Perimeter coordinate (as perimeter_point) of points offset w (n, 3) from the axis (on or near the wall)."""
    ey, ez = tb.axes()
    y, z = w @ ey, w @ ez
    if tb.shape == "round_tube":
        return np.mod(np.arctan2(z, y), 2.0 * math.pi) * tb.R
    hy, hz = _rect_half(tb)
    k = _rect_corners(tb)
    face = np.argmax(np.stack([y / hy, z / hz, -y / hy, -z / hz]), axis=0)
    c = np.choose(face, [z + hz, k[1] + (hy - y), k[2] + (hz - z), k[3] + (y + hy)])
    return np.mod(c, perimeter(tb))


def section_points(tb: Tube, size: float, facet: bool = True, forced=None,
                   min_facets: int = MIN_ROUND_FACETS) -> tuple[np.ndarray, int]:
    """Offsets (n_c, 3) of the generator lines from the axis, counter-clockwise about d (from ey to ez).

    forced: [(perimeter coordinate, half width)]: generator lines put there (weld strips). Lines closer than
    MERGE_T t are one (a rect tube's corners first, then the forced lines in order); between these fixed lines
    the gaps are cut evenly into pieces no wider than the even spacing, except a gap of at most 1.05 t (a
    strip), which stays whole. min_facets: the least facet count of a round tube. A round tube's corners stay at
    facet_radius_factor(n) x R for the total count n (exact for even facets only)."""
    forced = list(forced or [])
    P = perimeter(tb)
    if tb.shape == "round_tube":
        n = max(min_facets, 4 * math.ceil(2.0 * math.pi * tb.R / size / 4.0 - 1e-9))
        base = P * np.arange(n) / n
        keep_always = np.zeros(0)
        spacing = P / n
    else:
        hy, hz = _rect_half(tb)
        ny, nz = max(2, math.ceil(2 * hy / size - 1e-9)), max(2, math.ceil(2 * hz / size - 1e-9))
        k = _rect_corners(tb)
        # counter-clockwise about d seen with ey right and ez up: start at (+hy, -hz)
        base = np.concatenate([k[0] + 2 * hz * np.arange(nz) / nz, k[1] + 2 * hy * np.arange(ny) / ny,
                               k[2] + 2 * hz * np.arange(nz) / nz, k[3] + 2 * hy * np.arange(ny) / ny])
        keep_always = k
        spacing = min(2 * hy / ny, 2 * hz / nz)
    cs = base
    if forced:
        gap = MERGE_T * tb.t  # lines closer than this are one line (no sliver elements)
        fc = np.mod(np.array([f for f, _ in forced], float), P)
        if len(keep_always):
            for i, c in enumerate(fc):
                dk = np.abs((keep_always - c + P / 2) % P - P / 2)
                if dk.min() < gap:
                    fc[i] = keep_always[int(np.argmin(dk))]
        # corners first, then the forced lines in order, then the even lines left: each kept unless within gap
        # of one already kept
        kept: list[float] = []
        for c in list(keep_always) + list(fc):
            if all(abs((c - k + P / 2) % P - P / 2) >= gap for k in kept):
                kept.append(float(c))
        fixed = np.sort(np.mod(np.array(kept), P))
        # between the fixed lines (corners and forced lines), even lines no further apart than the even
        # spacing (no wide facet beside a strip)
        cs = []
        for i, c0 in enumerate(fixed):
            c1 = fixed[(i + 1) % len(fixed)] + (P if i + 1 == len(fixed) else 0.0)
            k = 1 if c1 - c0 <= 1.05 * tb.t else max(1, math.ceil((c1 - c0) / spacing - 1e-9))  # a strip stays whole
            cs += [c0 + (c1 - c0) * j / k for j in range(k)]
        cs = np.mod(np.array(cs), P)
    off = perimeter_point(tb, cs)
    if tb.shape == "round_tube" and facet:
        off = off * facet_radius_factor(len(cs))
    return off, len(cs)


def exit_param(host: Tube, node: np.ndarray, q: np.ndarray, d: np.ndarray) -> np.ndarray:
    """For lines q_k + s d (q (n, 3), d (3,)) starting inside the host's wall mid-surface near `node`, the s
    at which each leaves it (the true circle of radius R, or the mid-line rectangle)."""
    e = host.d
    w = q - node
    if host.shape == "round_tube":
        R = host.R
        wp = w - np.outer(w @ e, e)
        dp = d - (d @ e) * e
        A = float(dp @ dp)
        B = wp @ dp
        C = np.einsum("ij,ij->i", wp, wp) - R * R
        disc = np.maximum(B * B - A * C, 0.0)
        return (-B + np.sqrt(disc)) / A
    ey, ez = host.axes()
    out = np.full(len(q), np.inf)
    for ax, half in ((ey, (host.dims["h"] - host.t) / 2.0), (ez, (host.dims["b"] - host.t) / 2.0)):
        dd = float(d @ ax)
        if abs(dd) < 1e-12:
            continue
        ww = w @ ax
        out = np.minimum(out, (math.copysign(half, dd) - ww) / dd)
    if not np.all(np.isfinite(out)):
        raise TubeMeshError(f"a branch runs along the host {host.id}'s wall")
    return out


def _cuts(tubes: dict, tb: Tube, offs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Axial positions (from p0) of the start and end cut on every generator line."""
    s = {}
    for end, sign in (("a", 1.0), ("b", -1.0)):
        e = tb.ends[end]
        node = tb.end_point(end)
        u = sign * tb.d  # into the tube
        if e["kind"] in ("free",):
            c = np.zeros(len(offs))
        elif e["kind"] == "host":
            c = np.full(len(offs), -e["ext"])
        elif e["kind"] == "branch":
            c = exit_param(tubes[e["host"]], node, node + offs, u)
        else:  # mitre: the bisector plane of the two axes, through the node
            mt, me = e["mate"]
            # the plane's normal: u (into this tube) less the mate's direction into its own tube, so the
            # plane bisects the two (square to the axis for a straight run)
            m = _unit(u - _out_dir(tubes[mt], me))
            c = -(offs @ m) / float(u @ m)
        s[end] = c
    return s["a"], tb.L - s["b"]


@dataclass
class Footprint:
    """Where a branch's tied ring lies on its host's wall: the ring's points (on the host's mid-surface) and
    their host coordinates s (along the host from p0) and c (perimeter coordinate, unwrapped about the first
    point so the footprint is one interval)."""

    host: str
    branch: str
    end: str
    node: str
    pts: np.ndarray
    s: np.ndarray
    c: np.ndarray
    # a round branch's crown and saddle points on the true intersection (true_extremes): {"smin" | "smax" |
    # "cmin" | "cmax": (the extreme value, the other coordinate there, the branch's angle there)}
    extremes: dict | None = None

    def limits(self) -> tuple[float, float, float, float]:
        if self.extremes:
            e = self.extremes
            return e["smin"][0], e["smax"][0], e["cmin"][0], e["cmax"][0]
        return float(self.s.min()), float(self.s.max()), float(self.c.min()), float(self.c.max())


EXTREME_SAMPLES = 3600  # angles round a branch at which its intersection with the host is sampled


def true_extremes(host: Tube, br: Tube, end: str, node: np.ndarray, rho: float, ref: float) -> dict:
    """The crown and saddle points of a round branch on its host: where the branch's generator circle (radius rho,
    its corners' radius) cut on the host's true mid-surface (exit_param) is furthest along the host (smin, smax)
    and round it (cmin, cmax, c unwrapped about ref). Each: (the extreme value, the other host coordinate there,
    the branch's angle there from ey, rad); sampled at EXTREME_SAMPLES angles, then refined on a parabola
    through the best sample and its neighbours."""
    ey, ez = br.axes()
    u = br.d if end == "a" else -br.d
    a = 2.0 * math.pi * np.arange(EXTREME_SAMPLES) / EXTREME_SAMPLES

    def curve(ang):
        ang = np.atleast_1d(ang)
        offs = rho * (np.cos(ang)[:, None] * ey + np.sin(ang)[:, None] * ez)
        q = node + offs
        s = exit_param(host, node, q, u)
        return host_coords(host, q + s[:, None] * u, ref=ref)

    hs, hc = curve(a)
    out = {}
    step = a[1] - a[0]
    for key, vals, other, sign in (("smin", hs, 1, -1.0), ("smax", hs, 1, 1.0), ("cmin", hc, 0, -1.0),
                                   ("cmax", hc, 0, 1.0)):
        k = int(np.argmax(sign * vals))
        y0, y1, y2 = vals[k - 1], vals[k], vals[(k + 1) % len(vals)]
        den = y0 - 2 * y1 + y2
        off = 0.5 * (y0 - y2) / den if abs(den) > 1e-12 else 0.0
        ang = a[k] + max(-1.0, min(1.0, off)) * step
        s_, c_ = curve(ang)
        val = float((s_ if key[0] == "s" else c_)[0])
        out[key] = (val, float((c_ if key[0] == "s" else s_)[0]), float(ang))
    return out


def _ring_at(tb: Tube, offs: np.ndarray, s0: np.ndarray, s1: np.ndarray, end: str) -> np.ndarray:
    return tb.p0 + offs + (s0 if end == "a" else s1)[:, None] * tb.d


def host_coords(host: Tube, X: np.ndarray, ref: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(s, c) of points X on the host's wall: along its axis from p0, and the perimeter coordinate (unwrapped to
    within half the perimeter of ref when given)."""
    w = X - host.p0
    s = w @ host.d
    c = perimeter_coord(host, w - np.outer(s, host.d))
    if ref is not None:
        P = perimeter(host)
        c = ref + ((c - ref + P / 2) % P - P / 2)
    return s, c


def footprints(tubes: dict, offsets: dict, cuts: dict) -> list:
    out = []
    for tb in tubes.values():
        for end in ("a", "b"):
            e = tb.ends[end]
            if e["kind"] != "branch":
                continue
            host = tubes[e["host"]]
            ring = _ring_at(tb, offsets[tb.id], *cuts[tb.id], end)
            s, c = host_coords(host, ring)
            P = perimeter(host)
            c = c[0] + ((c - c[0] + P / 2) % P - P / 2)
            ext = None
            if tb.shape == "round_tube":
                rho = float(np.linalg.norm(offsets[tb.id][0]))
                ext = true_extremes(host, tb, end, tb.end_point(end), rho, float(c[0]))
            out.append(Footprint(host.id, tb.id, end, e["node"], ring, s, c, ext))
    return out


def _strip_lines(fps: list, tubes: dict) -> tuple[dict, dict]:
    """Per tube: the forced generator lines [(c, keep-clear half width)] and forced rows [s] of the weld strips:
    on a host at each footprint's four extremes and t and 2t beyond (t the host's wall); for a rect branch,
    k1_shell's corner lines at t, 2t and 3t (t the larger wall) from the footprint's corners along its edges,
    on the host's wall and on the branch's, so the first pair read past a corner square (CORNER_SKIP_T) lies
    2.5 t from the corner whatever the element size."""
    cols: dict[str, list] = {}
    rows: dict[str, list] = {}
    for fp in fps:
        host, br = tubes[fp.host], tubes[fp.branch]
        t = host.t
        smin, smax, cmin, cmax = fp.limits()
        cols.setdefault(fp.host, []).extend([(cmin, 0.5 * t), (cmin - t, t), (cmin - 2 * t, 0.5 * t),
                                             (cmax, 0.5 * t), (cmax + t, t), (cmax + 2 * t, 0.5 * t)])
        rows.setdefault(fp.host, []).extend([smin - 2 * t, smin - t, smin, smax, smax + t, smax + 2 * t])
        if fp.extremes:
            # a round branch: the crown and saddle are read on strip elements t long along the weld line and
            # centred on the true point (host lines t/2 either side of it), and the branch's ring has generator
            # lines t/2 either side of it too, so the read points do not move with the facet count
            e = fp.extremes
            for key in ("smin", "smax"):
                cols[fp.host].extend([(e[key][1] - 0.5 * t, 0.25 * t), (e[key][1] + 0.5 * t, 0.25 * t)])
            for key in ("cmin", "cmax"):
                rows[fp.host].extend([e[key][1] - 0.5 * t, e[key][1] + 0.5 * t])
            half = 0.5 * br.t
            cols.setdefault(fp.branch, []).extend((br.R * e[key][2] + sg * half, 0.25 * br.t)
                                                  for key in ("smin", "smax", "cmin", "cmax") for sg in (-1.0, 1.0))
            # the joint zone on the host: rows no further apart than a facet from a radius before the
            # footprint's strips to a radius after them, so the elements round the joint do not follow the
            # element size
            zs = zone_step(host)
            lo, hi = smin - 2 * t - ZONE_HOST_R * host.R, smax + 2 * t + ZONE_HOST_R * host.R
            rows.setdefault(fp.host + ZONE, []).extend(np.linspace(lo, hi, max(2, math.ceil((hi - lo) / zs) + 1)).tolist())
        if br.shape == "rect_tube":
            tc = max(t, br.t)
            ks = [k * tc for k in CORNER_LINES]
            rows[fp.host].extend([smin + k for k in ks] + [smax - k for k in ks])
            cols[fp.host].extend([(cmin + k, 0.25 * tc) for k in ks] + [(cmax - k, 0.25 * tc) for k in ks])
            cols.setdefault(fp.branch, []).extend((float(c) + sg * k, 0.25 * tc) for c in _rect_corners(br)
                                                  for k in ks for sg in (-1.0, 1.0))
    return cols, rows


ZONE = "#zone"  # key suffix in the forced rows: a host's joint-zone rows, kept only clear of its strip rows
ZONE_HOST_R = 0.25  # joint zone on a host: the footprint and its strips, and this many host radii beyond
ZONE_BRANCH_R = 0.5  # joint zone on a round branch: this many of its radii beyond its strips


def zone_step(tb: Tube) -> float:
    """Row spacing in a joint zone (weld strips): a round tube's facet width at its zone_facets (frame_shells'
    hot_spot_facets, HOT_SPOT_MIN_FACETS by default), a rect tube's side over 8 (assumption: about square elements
    round the joint)."""
    if tb.shape == "round_tube":
        return perimeter(tb) / tb.zone_facets
    return min(tb.dims["b"], tb.dims["h"]) / 8.0


def strip_steps(tb: Tube, ring: np.ndarray) -> np.ndarray:
    """Per generator line, the distance along it that is one wall thickness square to the cut line (the cut's
    tangent from the two neighbouring generator lines' cut points): t / sqrt(1 - (d . T)^2), at most 4 t."""
    T = np.roll(ring, -1, axis=0) - np.roll(ring, 1, axis=0)
    T = T / np.linalg.norm(T, axis=1, keepdims=True)
    cos = np.sqrt(np.clip(1.0 - (T @ tb.d) ** 2, 0.0, 1.0))
    return tb.t / np.maximum(cos, 0.25)


def _row_positions(tb: Tube, s0: np.ndarray, s1: np.ndarray, size: float, step_a, step_b, forced: list,
                   zone: list | None = None) -> np.ndarray:
    """Axial positions (rows + 1, n_c) of the grid rows: at the end cuts, at one and two strip steps from a tied
    end (step_a / step_b; None: none), at the forced rows (absolute, the same on every generator line) between
    those, and evenly in between (pieces no longer than size; at least two rows of elements)."""
    zs = zone_step(tb)
    # the joint zone on a round tube's tied end: a radius of rows a facet apart beyond the strips
    nz = max(1, math.ceil(ZONE_BRANCH_R * tb.R / zs)) if tb.shape == "round_tube" else 0
    for zn in (nz, 0):
        head = [s0] if step_a is None else [s0, s0 + step_a, s0 + 2 * step_a] + [s0 + 2 * step_a + k * zs
                                                                             for k in range(1, zn + 1)]
        tail = [s1] if step_b is None else [s1 - 2 * step_b - k * zs for k in range(zn, 0, -1)] + [
            s1 - 2 * step_b, s1 - step_b, s1]
        if float((tail[0] - head[-1]).min()) > 1e-6:
            break
    else:  # no room for the strips either: plain rows
        head, tail = [s0], [s1]
    lo, hi = float(head[-1].max()), float(tail[0].min())
    mids: list[float] = []
    gap = MERGE_T * tb.t
    for f in forced:  # the strip rows first
        if lo + gap < f < hi - gap and all(abs(f - m) >= gap for m in mids):
            mids.append(f)
    strip = list(mids)
    for f in zone or []:  # then the zone's rows, a strip element's length (1.05 t) clear of every strip row
        if (lo + gap < f < hi - gap and all(abs(f - m) >= 1.05 * tb.t for m in strip)
                and all(abs(f - m) >= gap for m in mids)):
            mids.append(f)
    mids.sort()
    knots = head + [np.full(len(s0), f) for f in mids] + tail
    rows = [knots[0]]
    for k0, k1 in zip(knots, knots[1:]):
        n = max(1, math.ceil(float((k1 - k0).max()) / size - 1e-9))
        rows += [k0 + (k1 - k0) * i / n for i in range(1, n + 1)]
    if len(rows) < 3:
        rows = [s0, 0.5 * (s0 + s1), s1]
    return np.array(rows)


def _host_extensions(tubes: dict, size: float, offsets: dict) -> None:
    """Length each host end runs on past its node: the furthest point of its branches' cuts beyond the node
    along its axis, plus one element (and room for the weld strips' rows at t and 2t)."""
    for tb in tubes.values():
        for end in ("a", "b"):
            e = tb.ends[end]
            if e["kind"] != "branch":
                continue
            host = tubes[e["host"]]
            for hend in ("a", "b"):
                he = host.ends.get(hend)
                if he and he["kind"] == "host" and he["node"] == e["node"]:
                    node = tb.end_point(end)
                    u = tb.d if end == "a" else -tb.d
                    s = exit_param(host, node, node + offsets[tb.id], u)
                    pts = node + offsets[tb.id] + s[:, None] * u
                    beyond = (pts - node) @ (-_out_dir(host, hend))
                    he["ext"] = max(he["ext"], float(beyond.max()) + max(size, 3.0 * host.t))


# ---------------------------------------------------------------- the mesh


@dataclass
class FrameShells:
    model: FEModel
    tubes: dict
    joints: dict
    hosts: dict
    ties: list
    xyz: dict  # design node -> (3,)
    size: float
    stub_mass_kg: float = 0.0
    refs: dict = field(default_factory=dict)  # hardpoint node -> ref node
    footprints: list = field(default_factory=list)
    weld_strips: bool = False
    tie_sets: list = field(default_factory=list)  # (ring nodes, faces) of each tie

    def tube_of_element(self) -> list:
        out = []
        for tb in self.tubes.values():
            out += [tb.id] * len(tb.quads)
        return out

    def patch(self, node: str) -> np.ndarray:
        """The wall nodes of the joint's host within PATCH_FACTOR x its radius of the node along the host
        and the host's radius plus a facet around it."""
        tb = self.tubes[self.hosts[node]]
        p = self.xyz[node]
        X = self.model.nodes[tb.ids.ravel()]
        w = X - p
        ax = w @ tb.d
        radial = np.linalg.norm(w - np.outer(ax, tb.d), axis=1)
        keep = (np.abs(ax) <= PATCH_FACTOR * tb.R + 1e-9) & (radial <= 1.2 * tb.R + 1e-9)
        ids = tb.ids.ravel()[keep]
        if len(ids) < 3:  # a coarse mesh: the nearest ring
            k = np.argsort(np.abs(ax))[: max(3, tb.ids.shape[1])]
            ids = tb.ids.ravel()[k]
        return np.unique(ids)


def frame_shells(design: dict, size: float = ELEMENT_SIZE_MM, facet: bool = True, members: list | None = None,
                 max_gap: float | None = None, weld_strips: bool = True,
                 hot_spot_facets: int | None = None) -> FrameShells:
    """The shell model of the frame's tubes (see the module docstring): MITC4 group per tube, ties at the
    joints. No supports, loads or mounts yet. weld_strips: grid lines for the hot-spot read-out (hot_spots).
    hot_spot_facets: with weld strips, the least facet count of a round tube and the one its joint zones' rows
    are spaced at (None: HOT_SPOT_MIN_FACETS; fewer only to make a quick model, e.g. a test that needs the
    results' names and not their values)."""
    hot_spot_facets = HOT_SPOT_MIN_FACETS if hot_spot_facets is None else int(hot_spot_facets)
    if hot_spot_facets < MIN_ROUND_FACETS or hot_spot_facets % 4:
        raise ValueError(f"hot_spot_facets must be a multiple of 4, at least {MIN_ROUND_FACETS}; got {hot_spot_facets}")
    tubes, joints = tubes_of(design, members)
    for tb in tubes.values():
        tb.zone_facets = hot_spot_facets
    hosts = resolve_joints(tubes, joints)
    forced_cols: dict[str, list] = {}
    forced_rows: dict[str, list] = {}
    for _ in range(4):  # a host's own cut moves with its forced lines when it is also a branch: settle them
        offsets, cuts = {}, {}
        for tb in tubes.values():
            offsets[tb.id], _n = section_points(tb, size, facet, forced_cols.get(tb.id),
                                                hot_spot_facets if weld_strips else MIN_ROUND_FACETS)
            for end in ("a", "b"):
                if tb.ends[end]["kind"] == "host":
                    tb.ends[end]["ext"] = 0.0
        _host_extensions(tubes, size, offsets)
        for tb in tubes.values():
            cuts[tb.id] = _cuts(tubes, tb, offsets[tb.id])
        fps = footprints(tubes, offsets, cuts)
        if not weld_strips:
            break
        cols, forced_rows = _strip_lines(fps, tubes)
        key = lambda cs: {k: [round(c, 6) for c, _ in v] for k, v in cs.items()}  # noqa: E731
        if key(cols) == key(forced_cols):
            break
        forced_cols = cols
    mats = design["materials"]
    model = FEModel(np.zeros((0, 3)))
    stub = 0.0
    for tb in tubes.values():
        offs = offsets[tb.id]
        s0, s1 = cuts[tb.id]
        if np.any(s1 - s0 <= 1e-6):
            raise TubeMeshError(f"tube {tb.id}: its end cuts overlap (too short for its joints)")
        steps = {}
        for end in ("a", "b"):
            tied = tb.ends[end]["kind"] in ("branch", "mitre")
            steps[end] = strip_steps(tb, _ring_at(tb, offs, s0, s1, end)) if weld_strips and tied else None
        S = _row_positions(tb, s0, s1, size, steps["a"], steps["b"], forced_rows.get(tb.id, []),
                           forced_rows.get(tb.id + ZONE))
        P = tb.p0[None, None, :] + offs[None, :, :] + S[:, :, None] * tb.d[None, None, :]
        tb.ids = model.add_nodes(P.reshape(-1, 3)).reshape(S.shape[0], len(offs))
    # a mitre's second ring: nodes that fall on the first ring's nodes become those nodes; the rest are tied
    for tb in tubes.values():
        for end in ("a", "b"):
            e = tb.ends[end]
            if e["kind"] == "mitre" and not e["master"]:
                mt, me = e["mate"]
                mring = tubes[mt].ids[0] if me == "a" else tubes[mt].ids[-1]
                row = 0 if end == "a" else -1
                for j, n in enumerate(tb.ids[row]):
                    dist = np.linalg.norm(model.nodes[mring] - model.nodes[n], axis=1)
                    if dist.min() < 1e-6 * size:
                        tb.ids[row, j] = mring[int(np.argmin(dist))]
                e["merged"] = int(np.sum(np.isin(tb.ids[row], mring)))
    # a branch's ring node that falls on a node of its host's grid (the weld strips' lines pass through the
    # footprint's extremes) is that node: shared, not tied
    for tb in tubes.values():
        for end in ("a", "b"):
            e = tb.ends[end]
            if e["kind"] != "branch":
                continue
            hids = tubes[e["host"]].ids.ravel()
            row = 0 if end == "a" else -1
            for j, n in enumerate(tb.ids[row]):
                dist = np.linalg.norm(model.nodes[hids] - model.nodes[n], axis=1)
                if dist.min() < 1e-6 * size:
                    tb.ids[row, j] = hids[int(np.argmin(dist))]
    # branch rings at one joint that meet on a line (the two halves of a split X brace) share those nodes
    rings_at: dict[str, list] = {}
    for tb in tubes.values():
        for end in ("a", "b"):
            if tb.ends[end]["kind"] == "branch":
                rings_at.setdefault(tb.ends[end]["node"], []).append((tb, 0 if end == "a" else -1))
    for lst in rings_at.values():
        for k, (tb, row) in enumerate(lst):
            prev = np.concatenate([t.ids[r] for t, r in lst[:k]]) if k else np.zeros(0, int)
            if not len(prev):
                continue
            for j, n in enumerate(tb.ids[row]):
                dist = np.linalg.norm(model.nodes[prev] - model.nodes[n], axis=1)
                if dist.min() < 1e-6 * size:
                    tb.ids[row, j] = prev[int(np.argmin(dist))]
    for tb in tubes.values():
        ids, n_c = tb.ids, tb.ids.shape[1]
        q = []
        for i in range(ids.shape[0] - 1):
            for j in range(n_c):
                k = (j + 1) % n_c
                q.append([ids[i, j], ids[i, k], ids[i + 1, k], ids[i + 1, j]])
        tb.quads = np.array(q, int)
        mat = mats[tb.material]
        tb.group = model.add(MITC4(tb.quads, tb.t, float(mat["E_MPa"]), float(mat["nu"]),
                                   float(mat["density_kg_m3"]) * 1e-12, name=tb.id))
        for end in ("a", "b"):
            if tb.ends[end]["kind"] == "host":
                stub += float(mat["density_kg_m3"]) * 1e-9 * tb.ends[end]["ext"] * _wall_area(tb)
    used = np.unique(np.concatenate([tb.quads.ravel() for tb in tubes.values()]))
    model.rotation_nodes.update(used.tolist())
    xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    ties = []
    tie_sets = []  # (ring nodes, faces (k, 4)) of each tie, for the mesh record's connections
    tied: set[int] = set()
    for tb in tubes.values():
        for end in ("a", "b"):
            e = tb.ends[end]
            if e["kind"] not in ("branch", "mitre") or (e["kind"] == "mitre" and e["master"]):
                continue
            master = tubes[e["host"]] if e["kind"] == "branch" else tubes[e["mate"][0]]
            ring = tb.ids[0] if end == "a" else tb.ids[-1]
            ring = np.array([n for n in ring if int(n) not in tied], int)  # a shared node is tied once
            tied.update(int(n) for n in ring)
            if e["kind"] == "mitre":
                mring = tubes[e["mate"][0]].ids[0] if e["mate"][1] == "a" else tubes[e["mate"][0]].ids[-1]
                ring = ring[~np.isin(ring, mring)]
            else:
                ring = ring[~np.isin(ring, master.ids.ravel())]  # nodes shared with the host are not tied
            if not len(ring):
                continue
            node = xyz[e["node"]]
            # faces whose centre can be within a face's size of the ring (a face is at most size long and a
            # facet wide)
            reach = float(np.linalg.norm(model.nodes[ring] - node, axis=1).max()) + 1.5 * size + 0.5 * master.R
            cen = model.nodes[master.quads].mean(axis=1)
            near = master.quads[np.linalg.norm(cen - node, axis=1) <= reach]
            gap = max_gap if max_gap is not None else max(0.5 * size, 0.25 * master.R)
            try:
                c = model.add(tie(ring, near, model.nodes, name=f"tie {tb.id}.{end} on {master.id}", max_gap=gap))
            except ValueError as err:
                raise TubeMeshError(f"tube {tb.id} at node {e['node']}: {err}") from None
            ties.append(c)
            tie_sets.append((np.asarray(ring, int), np.asarray(near, int)))
    return FrameShells(model, tubes, joints, hosts, ties, xyz, size, stub, footprints=fps, weld_strips=weld_strips,
                       tie_sets=tie_sets)


def _wall_area(tb: Tube) -> float:
    if tb.shape == "round_tube":
        return math.pi * (tb.dims["d"] - tb.t) * tb.t
    return 2.0 * tb.t * (tb.dims["b"] + tb.dims["h"] - 2.0 * tb.t)


def add_mount(fs: FrameShells, node: str) -> int:
    """A reference node at a design node, joined by a distributing link to the joint host's patch."""
    if node in fs.refs:
        return fs.refs[node]
    ref = fs.model.add_node(fs.xyz[node])
    fs.model.add(distributing_link(ref, fs.patch(node), fs.model.nodes, name=f"mount {node}"))
    fs.refs[node] = ref
    return ref


def spread(fs: FrameShells, ids, point, force, moment=(0.0, 0.0, 0.0), F=None) -> np.ndarray:
    """Add to F (N, 6) a force (N) at `point` and a moment (N mm), spread over the nodes `ids` with the
    RBE3 weights (equal), keeping its resultant."""
    if F is None:
        F = np.zeros((len(fs.model.nodes), 6))
    ids = np.asarray(ids, int)
    f = rbe3_forces(point, fs.model.nodes[ids], force, moment)
    np.add.at(F[:, :3], ids, f)
    return F


# ---------------------------------------------------------------- loads from K2


HALF_KINDS = ("suspension",)


def _k2_kinds():
    from ..k2_vehicle.model import HALF_UNSPRUNG_KINDS, UNSPRUNG_KINDS

    return UNSPRUNG_KINDS, HALF_UNSPRUNG_KINDS


def sprung_items(design: dict) -> list:
    """K2's sprung mass items: (kg, centre m, inertia about own centre kg m^2, record) of every part box and
    frame member, wheel kinds left out and suspension kinds at half (model._unsprung's rule)."""
    from ..k2_vehicle.mass import member_items, part_items

    unsprung, half = _k2_kinds()
    out = []
    for m, c, inertia, rec in part_items(design.get("parts") or []) + member_items(design):
        kind = str(rec.get("kind", "")).lower()
        if kind in unsprung:
            continue
        k = 0.5 if kind in half else 1.0
        out.append((m * k, np.asarray(c, float), inertia * k, rec))
    return out


@dataclass
class BodyLoad:
    kind: str  # mount, body_torque, item
    where: str
    point_mm: np.ndarray
    force_N: np.ndarray
    moment_Nmm: np.ndarray
    carriers: list  # design nodes whose patches take it, or ["tube:<member id>"]


def body_loads(design: dict, veh, st: dict) -> list:
    """The loads on the frame in one K2 load state (see the module docstring), design axes, mm, N, N mm."""
    from ..k2_vehicle.shelf.step import corner_mounts

    rot, origin = np.asarray(st["rot"], float), np.asarray(st["origin"], float)
    body = rot.T
    m_s = float(veh.m_s)
    g = body @ (np.asarray(st["weight"], float) / m_s)
    acc = body @ np.asarray(st["acc"], float)
    alpha = body @ np.asarray(st["alpha"], float)
    om = body @ np.asarray(st["omega"], float)
    cg = np.asarray(veh.cg_s, float)
    sup = {s["id"]: s for s in design["supports"]}
    out = []
    for i, c in enumerate(veh.corners):
        got = corner_mounts(veh, st, i)
        if got is None:
            raise TubeMeshError(f"corner {c.id} is not on a K&C table with damper points: no mount forces")
        s = sup[c.id]
        side = "L" if s["position_mm"][1] > 0 else "R"
        nodes = _mount_nodes(design, s)
        for mount, (p, f) in got.items():
            pd = body @ (np.asarray(p) - origin) * 1000.0
            out.append(BodyLoad("mount", f"{c.id}.{mount}", pd, body @ np.asarray(f), np.zeros(3), [nodes[mount]]))
        tq = body @ np.asarray(st["corners"][i]["body_torque"], float) * 1000.0
        if np.any(tq != 0.0):
            pts = [nodes["lca_front"], nodes["lca_rear"]]
            mid = np.mean([np.asarray(sup[c.id]["suspension"]["hardpoints_mm"][k]) for k in ("lca_front", "lca_rear")],
                          axis=0)
            out.append(BodyLoad("body_torque", c.id, mid, np.zeros(3), tq, pts))
        del side
    for m, c, inertia, rec in sprung_items(design):
        r = c - cg
        a_p = acc + np.cross(alpha, r) + np.cross(om, np.cross(om, r))
        f = m * (g - a_p)
        mo = -(inertia @ alpha + np.cross(om, inertia @ om)) * 1000.0
        if rec.get("kind") == "frame_member":
            carriers = [f"tube:{rec['id']}"]
        elif rec.get("carried_by"):
            carriers = list(rec["carried_by"])
        else:
            carriers = _nearest_corner_mounts(design, c * 1000.0)
        out.append(BodyLoad("item", rec["id"], c * 1000.0, f, mo, carriers))
    return out


def _mount_nodes(design: dict, s: dict) -> dict:
    """The design node at each of a support's mount hardpoints (the one within 1e-6 mm)."""
    xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    out = {}
    for k in ("lca_front", "lca_rear", "uca_front", "uca_rear", "damper_upper", "tie_inner"):
        p = np.asarray(s["suspension"]["hardpoints_mm"][k], float)
        hit = [n for n, q in xyz.items() if np.linalg.norm(q - p) < 1e-6]
        if not hit:
            raise TubeMeshError(f"support {s['id']}: no frame node at its {k} {p.tolist()}")
        out[k] = hit[0]
    return out


def _nearest_corner_mounts(design: dict, p: np.ndarray) -> list:
    s = min(design["supports"], key=lambda s: float(np.linalg.norm(np.asarray(s["position_mm"]) - p)))
    n = _mount_nodes(design, s)
    return [n[k] for k in ("lca_front", "lca_rear", "uca_front", "uca_rear")]


def resultant_of(loads: list, about=(0.0, 0.0, 0.0)) -> np.ndarray:
    about = np.asarray(about, float)
    F = sum(l.force_N for l in loads)
    M = sum(np.cross(l.point_mm - about, l.force_N) + l.moment_Nmm for l in loads)
    return np.concatenate([F, M])


def shell_load_vector(fs: FrameShells, loads: list, member_tube: dict) -> np.ndarray:
    """Per-node loads (N, 6) of the body loads on the shell model: mounts on their RBE3 reference node,
    items spread (rbe3 weights) over their carriers' patches or their tube's nodes."""
    F = np.zeros((len(fs.model.nodes), 6))
    for l in loads:
        if l.kind == "mount":
            ref = fs.refs[l.carriers[0]]  # the reference node sits at the hardpoint
            F[ref, :3] += l.force_N
            # K2's mount point is the hardpoint; any difference is carried as its moment
            F[ref, 3:] += np.cross(l.point_mm - fs.xyz[l.carriers[0]], l.force_N) + l.moment_Nmm
            continue
        if l.carriers[0].startswith("tube:"):
            ids = fs.tubes[member_tube[l.carriers[0][5:]]].ids.ravel()
        else:
            ids = np.unique(np.concatenate([fs.patch(n) for n in l.carriers]))
        spread(fs, ids, l.point_mm, l.force_N, l.moment_Nmm, F)
    return F


def member_tube_map(fs: FrameShells) -> dict:
    return {m: tb.id for tb in fs.tubes.values() for m in tb.members}


def k2_states(design: dict, step_height_mm: float = 100.0, rec_hz: float = 25.0) -> dict:
    """K2's vehicle, its load state standing still on flat ground (1 g) and at the step's lift-off (the
    state of step_push's records nearest the snapshot time). Returns {"veh", "static", "lift", "push"}.
    The result is kept in this process by the design's content and the arguments (K2_STATES_KEPT), so a
    version's load cases and the tests that ask again do not run K2 again; treat it as read-only."""
    key = _content_key(design, step_height_mm, rec_hz)
    if key in _K2_STATES:
        _K2_STATES.move_to_end(key)
        return dict(_K2_STATES[key])
    out = _k2_states(design, step_height_mm, rec_hz)
    _K2_STATES[key] = out
    while len(_K2_STATES) > K2_STATES_KEPT:
        _K2_STATES.popitem(last=False)
    return dict(out)


K2_STATES_KEPT = 4
_K2_STATES: "OrderedDict[str, dict]" = OrderedDict()


def _content_key(design: dict, *args) -> str:
    """A digest of a design's content and the other arguments of a call."""
    import hashlib
    import json

    h = hashlib.sha1(json.dumps(design, sort_keys=True, default=str).encode())
    h.update(repr(args).encode())
    return h.hexdigest()


def _k2_states(design: dict, step_height_mm: float, rec_hz: float) -> dict:
    from ..k2_vehicle.drive import controls
    from ..k2_vehicle.model import build_vehicle
    from ..k2_vehicle.scenario import new_sim
    from ..k2_vehicle.shelf.step import load_state, step_push

    veh = build_vehicle(design)
    sim = new_sim(veh, dt=1e-3, settle_s=1.0)
    sim.step(controls(sim, 0.0, [0.0] * sim.n, [0.0] * sim.n))
    static = load_state(sim)
    xmax = max(float(c.pos[0]) for c in veh.corners)
    stepping = [i for i, c in enumerate(veh.corners) if abs(float(c.pos[0]) - xmax) < 1e-9]
    push = step_push(veh, step_height_mm / 1000.0, stepping, rec_hz=rec_hz)
    if push["snap"] is None or not push["states"]:
        raise TubeMeshError("the step push gave no lift-off state")
    lift = min(push["states"], key=lambda s: abs(s["t"] - push["snap"]["t"]))
    return {"veh": veh, "static": static, "lift": lift, "push": push}


# ---------------------------------------------------------------- the analysis


def hold_321(fs: FrameShells, design: dict) -> list:
    """Hold the shell 3-2-1 at wall nodes of three mounts: FL lca_front (x, y, z), RL lca_front (y, z), FR
    lca_front (z): the node of each patch nearest the hardpoint. Returns the held node ids."""
    sup = {s["id"]: s for s in design["supports"]}
    held = []
    for sid, comps in (("FL", [0, 1, 2]), ("RL", [1, 2]), ("FR", [2])):
        n = _mount_nodes(design, sup[sid])["lca_front"]
        ids = fs.patch(n)
        k = int(ids[np.argmin(np.linalg.norm(fs.model.nodes[ids] - fs.xyz[n], axis=1))])
        fs.model.fix(k, comps)
        held.append(k)
    return held


def joint_of_point(fs: FrameShells, p: np.ndarray) -> str:
    """The design node nearest a point."""
    return min(fs.xyz, key=lambda n: float(np.linalg.norm(fs.xyz[n] - p)))


def analyse(design: dict, size: float = ELEMENT_SIZE_MM, states: dict | None = None, step_height_mm: float = 100.0,
            facet: bool = True) -> dict:
    """The frame shell under the two K2 load cases (static 1 g, step lift-off). Per case: run_static's
    record (quality, rigid, results), the largest von Mises (and where: tube and nearest design node; and
    the largest away from the mount patches and the holds), each mount's displacement (its RBE3 reference
    node, relative to the 3-2-1 holds), and the balance: load_balance_residual (the holds' reaction force
    over the sum of the load magnitudes, and its moment over that times the model size: what of the K2
    loads does not balance) and equilibrium_residual (the model's load vector plus the reactions, the same
    scales: what was lost on the way into the model)."""
    states = states or k2_states(design, step_height_mm)
    veh = states["veh"]
    r = analyse_loads(design, {name: body_loads(design, veh, states[name]) for name in ("static", "lift")}, size,
                      facet)
    for name in ("static", "lift"):
        r["cases"][name]["t_s"] = float(states[name]["t"])
    return r


def _static_checks(model: FEModel, Fs: dict) -> dict:
    """analysis.run_static's steps for several load vectors on one model: the mesh quality (with every case's
    loaded nodes) and the rigid-body check once, one factorisation, then per load vector the solution and its
    equilibrium. Returns {"solve": F -> run_static's record}."""
    from . import precheck, quality
    from .analysis import loaded_and_held_nodes
    from .model import ModelError

    lay = model.layout()
    loaded = set()
    for F in Fs.values():
        loaded |= {int(n) for n in loaded_and_held_nodes(model, np.asarray(F, float))}
    q = quality.model_quality(model, np.array(sorted(loaded), int))
    mass = precheck.mass_check(model, None)
    base = {"status": "failed", "reasons": [], "quality": q["quality"], "rigid": None, "results": [], "solution": None}
    if q["fail"]:
        rec = dict(base, reasons=[f"mesh check: {r}" for r in q["fail"]], results=precheck.result_items(mass=mass))
        return {"solve": lambda F: dict(rec)}
    rb = precheck.rigid_body_check(model, lay)
    base["rigid"] = rb
    if rb["free_rigid_modes"]:
        rec = dict(base, reasons=[f"the model can move as a rigid body ({', '.join(rb['free_motions'])}); add supports"],
                   results=precheck.result_items(mass=mass, rigid=rb))
        return {"solve": lambda F: dict(rec)}
    try:
        solver = StaticSolver(model, method="eliminate", check_rigid=False)
    except ModelError as e:
        rec = dict(base, reasons=[str(e)], results=precheck.result_items(mass=mass, rigid=rb))
        return {"solve": lambda F: dict(rec)}

    def solve(F):
        sol = solver.solve(np.asarray(F, float))
        eq = precheck.equilibrium(sol)
        return dict(base, status="solved", solution=sol, equilibrium=eq, results=precheck.result_items(eq, mass, rb))

    return {"solve": solve}


def mesh_record(fs: FrameShells, design: dict, mesher: str) -> dict:
    """The contract `mesh` record (no mesh_id) of a FrameShells: shell elements tube by tube in fs.tubes' order
    (per_element values follow it: element_values), the nodes they use (renumbered from 0), element_member
    (member_at of each element's centre), member_thickness_mm, the ties as connections (kind tie: nodes the
    ring, faces the host's faces) and quality (fe.quality.record_quality, the ties as links)."""
    from .quality import record_quality

    quads = np.concatenate([tb.quads for tb in fs.tubes.values()])
    used = np.unique(np.concatenate([quads.ravel()] + [r for r, _ in fs.tie_sets] +
                                    [f.ravel() for _, f in fs.tie_sets]))
    index = np.full(len(fs.model.nodes), -1, int)
    index[used] = np.arange(len(used))
    members, thickness = [], {}
    for tb in fs.tubes.values():
        cen = fs.model.nodes[tb.quads].mean(axis=1)
        for c in cen:
            m = member_at(tb, fs, c)
            members.append(m)
            thickness[m] = float(tb.t)
    conns, links = [], []
    for k, (ring, faces) in enumerate(fs.tie_sets):
        conns.append({"id": f"tie-{k + 1:03d}", "kind": "tie", "nodes": index[ring].tolist(),
                      "faces": index[faces].tolist()})
        links.append(np.concatenate([index[ring], index[faces].ravel()]).tolist())
    rec = {"design_version": int(design.get("design_version", 1)), "mesher": mesher, "element_type": "shell",
           "formulation": "MITC4", "element_size_mm": float(fs.size), "nodes_mm": np.round(fs.model.nodes[used], 6).tolist(),
           "elements": index[quads].tolist(), "element_member": members, "member_thickness_mm": thickness,
           "connections": conns,
           "notes": (f"fe.tubes: each straight run of one section a tube of MITC4 shells, branches cut on their host's "
                     f"wall and tied to it ({len(conns)} ties); weld strips for the IIW hot spot; at least "
                     f"{HOT_SPOT_MIN_FACETS} facets per round tube")}
    rec["quality"] = record_quality(rec, links=links)
    return rec


def element_values(fs: FrameShells, per_tube: dict) -> list:
    """Values per tube ({tube id: (n_quads,)}) as one list in mesh_record's element order."""
    return [float(v) for tb in fs.tubes.values() for v in per_tube[tb.id]]


def prepare(design: dict, size: float = ELEMENT_SIZE_MM, facet: bool = True, weld_strips: bool = True,
            hot_spot_facets: int | None = None) -> dict:
    """What every load case of a design shares: the shell (frame_shells), a reference node with its distributing
    link at each suspension pickup (add_mount), the 3-2-1 holds (hold_321) and, after the first solve, the
    factorisation. {"fs", "mounts", "held", "mt", "excluded", "size", "solver"}."""
    fs = frame_shells(design, size, facet, weld_strips=weld_strips, hot_spot_facets=hot_spot_facets)
    mounts = sorted({n for s in design["supports"] for n in _mount_nodes(design, s).values()})
    for n in mounts:
        add_mount(fs, n)
    held = hold_321(fs, design)
    patch_nodes = set(np.concatenate([fs.patch(n) for n in mounts]).tolist())
    return {"fs": fs, "mounts": mounts, "held": held, "mt": member_tube_map(fs),
            "excluded": patch_nodes | {int(k) for k in held}, "size": float(size), "solver": None}


def analyse_loads(design: dict, cases_loads: dict, size: float = ELEMENT_SIZE_MM, facet: bool = True,
                  weld_strips: bool = True, prepared: dict | None = None) -> dict:
    """The frame shell under load cases {name: [BodyLoad]} (as analyse, which gives K2's two), held 3-2-1. Per
    case also the structural hot spots (hot_spots, outside the mount patches and the holds) and the largest.
    prepared: prepare()'s record of this design (its shell, mounts, holds and factorisation are reused)."""
    from .shell import shell_stresses

    p = prepared if prepared is not None else prepare(design, size, facet, weld_strips)
    fs, mounts, held, mt, excluded = p["fs"], p["mounts"], p["held"], p["mt"], p["excluded"]
    cases, solutions = {}, {}
    size_mm = float(np.ptp(fs.model.nodes, axis=0).max())
    Fs = {name: shell_load_vector(fs, loads, mt) for name, loads in cases_loads.items()}
    if p["solver"] is None:  # analysis.run_static's checks once, one factorisation for every case
        p["solver"] = _static_checks(fs.model, Fs)
    shared = p["solver"]
    for name, loads in cases_loads.items():
        F = Fs[name]
        res = shared["solve"](F)
        # run_static's equilibrium_residual divides by the larger of the applied and reacted resultants,
        # both near zero for a balanced free body: left out, this record's own residuals below replace it
        out = {"status": res["status"], "reasons": res["reasons"], "quality": res["quality"],
               "results": [x for x in res["results"] if x["quantity"] != "equilibrium_residual"]}
        applied = resultant_of(loads)
        scale_f = sum(float(np.linalg.norm(l.force_N)) for l in loads)
        out["applied_resultant"] = applied
        out["applied_force_sum_N"] = scale_f
        if res["status"] == "solved":
            sol = res["solution"]
            solutions[name] = sol
            from .model import resultant

            Rr = resultant(fs.model.nodes, sol.reactions())
            out["reaction_N"] = Rr[:3]
            out["reaction_moment_Nmm"] = Rr[3:]
            # the loads balance when the 3-2-1 holds take (almost) nothing: reactions over the loads' size
            out["load_balance_residual"] = float(max(np.linalg.norm(Rr[:3]) / max(scale_f, 1e-300),
                                                     np.linalg.norm(Rr[3:]) / max(scale_f * size_mm, 1e-300)))
            Fr = resultant(fs.model.nodes, F)
            out["model_load_resultant"] = Fr
            out["equilibrium_residual"] = float(max(np.linalg.norm(Fr[:3] + Rr[:3]) / max(scale_f, 1e-300),
                                                    np.linalg.norm(Fr[3:] + Rr[3:]) / max(scale_f * size_mm, 1e-300)))
            best, best_away = (-1.0, None), (-1.0, None)
            for tb in fs.tubes.values():
                ss = shell_stresses(sol, tb.group)
                vm = ss.von_mises("max")
                k = int(np.argmax(vm))
                if vm[k] > best[0]:
                    best = (float(vm[k]), (tb.id, ss.centres[k]))
                away = np.array([not any(int(i) in excluded for i in q) for q in tb.quads])
                if away.any():
                    ka = int(np.argmax(np.where(away, vm, -1.0)))
                    if vm[ka] > best_away[0]:
                        best_away = (float(vm[ka]), (tb.id, ss.centres[ka]))
            U = sol.displacements
            for key, (val, (tid, c)) in (("max", best), ("max_away_from_mounts", best_away)):
                out[f"von_mises_{key}"] = {"MPa": val, "tube": tid, "joint": joint_of_point(fs, c),
                                           "distance_to_joint_mm": float(np.linalg.norm(fs.xyz[joint_of_point(fs, c)] - c)),
                                           "point_mm": c.tolist()}
            out["mount_displacement_mm"] = {n: U[fs.refs[n], :3].tolist() for n in mounts}
            out["hot_spots"] = hot_spots(fs, sol, excluded)
            out["hot_spot_max"] = hot_spot_max(out["hot_spots"])
        cases[name] = out
    return {"cases": cases, "n_nodes": int(len(fs.model.nodes)), "n_elements": int(sum(len(t.quads) for t in fs.tubes.values())),
            "n_ties": len(fs.ties), "stub_mass_kg": fs.stub_mass_kg,
            "shell_mass_kg": float(sum(t.group.mass_total(fs.model.nodes) for t in fs.tubes.values()) * 1000.0),
            "held": held, "shells": fs, "solutions": solutions, "excluded_nodes": excluded}


# ---------------------------------------------------------------- the beam model under the same loads


def beam_model(design: dict, n_per_member: int = 4) -> dict:
    """The design's members as fe.beam 3D beams (n_per_member elements each), E, G = E / (2 (1 + nu)) and
    the section's A, Iy, Iz, J from the K0 section formulas (k0_handcalc.sections.properties), joints
    rigid at the design nodes. Returns {"model", "node" {design node: index}}."""
    from ..k0_handcalc.sections import properties
    from .beam import Beam

    xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    names = list(xyz)
    index = {n: i for i, n in enumerate(names)}
    pts = [xyz[n] for n in names]
    props = {}
    for key, sec in design["sections"].items():
        p = properties(sec)
        props[key] = {"A_mm2": p["A"], "Iy_mm4": p["Iy"], "Iz_mm4": p["Iz"], "J_mm4": p["J"]}
    conn, E, G, A, Iy, Iz, J, rho = [], [], [], [], [], [], [], []
    for m in design["members"]:
        a, b = xyz[m["node_a"]], xyz[m["node_b"]]
        chain = [index[m["node_a"]]]
        for k in range(1, n_per_member):
            pts.append(a + (b - a) * k / n_per_member)
            chain.append(len(pts) - 1)
        chain.append(index[m["node_b"]])
        mat = design["materials"][m["material"]]
        p = props[m["section"]]
        for i, j in zip(chain, chain[1:]):
            conn.append([i, j])
            E.append(float(mat["E_MPa"]))
            G.append(float(mat["E_MPa"]) / (2.0 * (1.0 + float(mat["nu"]))))
            A.append(p["A_mm2"])
            Iy.append(p["Iy_mm4"])
            Iz.append(p["Iz_mm4"])
            J.append(p["J_mm4"])
            rho.append(float(mat["density_kg_m3"]) * 1e-12)
    model = FEModel(np.array(pts))
    model.add(Beam(np.array(conn), np.array(E), np.array(G), np.array(A), np.array(Iy), np.array(Iz), np.array(J),
                   np.array(rho)))
    return {"model": model, "node": index}


def _to_nodes(F: np.ndarray, X: np.ndarray, ids, point, force, moment) -> None:
    """A force at `point` and a moment, shared equally by nodes `ids` (6 DOFs each) with the moment of the
    offsets put back on them as couples, so the resultant is kept."""
    ids = list(ids)
    n = len(ids)
    f = np.asarray(force, float) / n
    m = np.asarray(moment, float) + sum(np.cross(np.asarray(point) - X[i], f) for i in ids)
    for i in ids:
        F[i, :3] += f
        F[i, 3:] += m / n


def beam_loads(design: dict, bm: dict, loads: list) -> np.ndarray:
    """The body loads on the beam model: mounts at their nodes, a member's items at its two end nodes, a
    part's at its carriers (each share with the couple that keeps the resultant)."""
    model, idx = bm["model"], bm["node"]
    F = np.zeros((len(model.nodes), 6))
    ends = {m["id"]: (m["node_a"], m["node_b"]) for m in design["members"]}
    for l in loads:
        if l.carriers[0].startswith("tube:"):
            nodes = ends[l.carriers[0][5:]]
        else:
            nodes = l.carriers
        _to_nodes(F, model.nodes, [idx[n] for n in nodes], l.point_mm, l.force_N, l.moment_Nmm)
    return F


def rigid_fit(P: np.ndarray, U: np.ndarray) -> np.ndarray:
    """The displacements U (n, 3) at points P less their least-squares rigid motion (small rotations)."""
    rows = []
    for p in P:
        rows.append(np.hstack([np.eye(3), -_skew(p)]))
    A = np.vstack(rows)
    x, *_ = np.linalg.lstsq(A, U.ravel(), rcond=None)
    return U - (A @ x).reshape(-1, 3)


def _skew(r) -> np.ndarray:
    x, y, z = r
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def compare_beam(design: dict, result: dict, case: str, states: dict, n_per_member: int = 4) -> dict:
    """The same loads on the beam model held 3-2-1 at the same three mounts' nodes; the mounts'
    displacements of both less their own rigid motion (so the holds' places do not matter), and their
    ratio (shell / beam) by the root mean square over the mounts."""
    bm = beam_model(design, n_per_member)
    model, idx = bm["model"], bm["node"]
    loads = body_loads(design, states["veh"], states[case])
    F = beam_loads(design, bm, loads)
    sup = {s["id"]: s for s in design["supports"]}
    for sid, comps in (("FL", [0, 1, 2]), ("RL", [1, 2]), ("FR", [2])):
        model.fix(idx[_mount_nodes(design, sup[sid])["lca_front"]], comps)
    sol = StaticSolver(model).solve(F)
    mounts = sorted(result["cases"][case]["mount_displacement_mm"])
    P = np.array([design_xyz(design)[n] for n in mounts])
    Ub = np.array([sol.displacements[idx[n], :3] for n in mounts])
    Us = np.array([result["cases"][case]["mount_displacement_mm"][n] for n in mounts])
    db, ds = rigid_fit(P, Ub), rigid_fit(P, Us)
    rms = lambda a: float(np.sqrt(np.mean(np.sum(a * a, axis=1))))  # noqa: E731
    k = int(np.argmax(np.linalg.norm(ds, axis=1)))
    return {"mounts": mounts, "shell_mm": ds, "beam_mm": db, "rms_shell_mm": rms(ds), "rms_beam_mm": rms(db),
            "ratio": rms(ds) / rms(db), "largest": mounts[k], "largest_shell_mm": float(np.linalg.norm(ds[k])),
            "largest_beam_mm": float(np.linalg.norm(db[k])),
            "reaction_N": sol.reactions().sum(axis=0)[:3]}


def design_xyz(design: dict) -> dict:
    return {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}


# ---------------------------------------------------------------- structural hot spots on the tie lines

STRIP_TOL_T = 0.15  # a strip element's centre within this many t of its read distance (curved cut lines)
CONTACT_TOL_T = 0.05  # ring points within this many t of a footprint's extreme line touch it


def member_at(tb: Tube, fs: FrameShells, p: np.ndarray) -> str:
    """The design member of a tube at a point (by its position along the tube)."""
    s = float((np.asarray(p) - tb.p0) @ tb.d)
    for m, nb in zip(tb.members, tb.nodes[1:]):
        if s <= float((fs.xyz[nb] - tb.p0) @ tb.d) + 1e-9:
            return m
    return tb.members[-1]


def _along(st, idx: np.ndarray, dirs: np.ndarray, surface: str) -> np.ndarray:
    """Surface stress of elements idx along per-element directions dirs (k, 3), projected on each plane."""
    dl = np.einsum("kij,kj->ki", st.frames[idx], dirs)
    nrm = np.linalg.norm(dl[:, :2], axis=1)
    c, s = dl[:, 0] / nrm, dl[:, 1] / nrm
    sx, sy, txy = st.components(surface)[idx].T
    return c * c * sx + s * s * sy + 2 * c * s * txy


def _ring_corners(ring: np.ndarray, deg: float = 30.0) -> np.ndarray:
    """Indices of the points where the closed polyline turns by more than deg (a rect section's corners)."""
    a = ring - np.roll(ring, 1, axis=0)
    b = np.roll(ring, -1, axis=0) - ring
    cos = np.einsum("ij,ij->i", a, b) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))
    return np.nonzero(cos < math.cos(math.radians(deg)))[0]


def hot_spots(fs: FrameShells, sol, excluded=None) -> list:
    """IIW structural hot-spot stresses along the tie lines of a frame meshed with weld strips, by the rule of
    k1_shell.stress.hot_spots (HOT_SPOT_SOURCE there): the surface stress square to the weld line in the
    strips one wall thickness t wide beside it, at HOT_SPOT_READ_T (0.5 t and 1.5 t), outer (top) and inner
    (bottom) surface, extrapolated to the line (1.5 sigma(0.5 t) - 0.5 sigma(1.5 t)).

    - branch side (and each side of a mitre): every pair of strip elements along the cut ring (the tube's
      first two rows, graded t square to the cut by strip_steps); for a rect branch not within CORNER_SKIP_T t
      of a corner of its section (k1_shell's corner squares);
    - host side: beyond the four extremes of each footprint on the host's wall (along the host: before and
      after; around it: one side and the other), where the grid has lines at the extreme and t and 2t beyond.
      Where the ring touches the extreme line over 2 CORNER_SKIP_T t or more (a rect footprint's edge) the
      pairs along it outside the corner squares are read; where it touches at a point (a round branch's
      crown or saddle) the pair over that point.
    Pairs with an element on a node in `excluded` (the mount patches, the holds) are not read, nor pairs whose
    centres are not within STRIP_TOL_T t of 0.5 t and 1.5 t (a strip cut by another joint's lines, elements
    smaller than t).

    Returns dicts like k1_shell's: value (MPa, signed), surface, joint (design node), kind ("tie", "mitre"),
    side ("host", "branch"), place, tube, member, position_mm (the pair's foot on the weld line), sigma_05t,
    sigma_15t, elements ((tube, index) at 0.5 t, at 1.5 t), t_mm, direction (into the strip), towards."""
    from .shell import shell_stresses

    if not fs.weld_strips:
        return []
    a_t, b_t = HOT_SPOT_READ_T
    st = {tid: shell_stresses(sol, tb.group) for tid, tb in fs.tubes.items()}
    ex = None
    if excluded is not None and len(excluded):
        ex = np.zeros(len(fs.model.nodes), bool)
        ex[np.asarray(sorted(excluded), int)] = True
    out = []

    def emit(tb, e1, e2, dirs, feet, joint, kind, side, place):
        if ex is not None:
            keep = ~(ex[tb.quads[e1]].any(axis=1) | ex[tb.quads[e2]].any(axis=1))
            e1, e2, dirs, feet = e1[keep], e2[keep], dirs[keep], feet[keep]
        if len(e1) == 0:
            return
        s = st[tb.id]
        for surface in ("top", "bottom"):
            s1, s2 = _along(s, e1, dirs, surface), _along(s, e2, dirs, surface)
            hs = extrapolate_hot_spot(s1, s2)
            for i in range(len(e1)):
                out.append({"value": float(hs[i]), "surface": surface, "joint": joint, "kind": kind, "side": side,
                            "place": place, "tube": tb.id, "member": member_at(tb, fs, feet[i]),
                            "position_mm": feet[i], "sigma_05t": float(s1[i]), "sigma_15t": float(s2[i]),
                            "elements": ((tb.id, int(e1[i])), (tb.id, int(e2[i]))), "t_mm": tb.t,
                            "direction": dirs[i], "towards": axis_name(dirs[i])})

    for tb in fs.tubes.values():
        n_c, rows = tb.ids.shape[1], tb.ids.shape[0] - 1
        for end in ("a", "b"):
            e = tb.ends[end]
            if e["kind"] not in ("branch", "mitre"):
                continue
            i1, i2 = (0, 1) if end == "a" else (rows - 1, rows - 2)
            ring = fs.model.nodes[tb.ids[0] if end == "a" else tb.ids[-1]]
            j = np.arange(n_c)
            e1, e2 = i1 * n_c + j, i2 * n_c + j
            A, B = ring, np.roll(ring, -1, axis=0)
            seg = np.linalg.norm(B - A, axis=1)
            T = (B - A) / seg[:, None]
            din = tb.d if end == "a" else -tb.d
            m = din[None, :] - (T @ din)[:, None] * T
            m /= np.linalg.norm(m, axis=1, keepdims=True)
            mid = 0.5 * (A + B)
            c = st[tb.id].centres
            d1 = np.einsum("ij,ij->i", c[e1] - mid, m)
            d2 = np.einsum("ij,ij->i", c[e2] - mid, m)
            ok = (np.abs(d1 - a_t * tb.t) < STRIP_TOL_T * tb.t) & (np.abs(d2 - b_t * tb.t) < STRIP_TOL_T * tb.t)
            if tb.shape == "rect_tube":
                arc = np.concatenate([[0.0], np.cumsum(seg)])
                L = arc[-1]
                corners = arc[_ring_corners(ring)]
                if len(corners):
                    at = arc[:-1] + 0.5 * seg
                    dc = np.abs((at[:, None] - corners[None, :] + L / 2) % L - L / 2).min(axis=1)
                    ok &= dc > CORNER_SKIP_T * tb.t - 1e-9
            kind = "mitre" if e["kind"] == "mitre" else "tie"
            place = f"{tb.id} end {end}" + (f" on {e['host']}" if e["kind"] == "branch" else f" (mitre with {e['mate'][0]})")
            emit(tb, e1[ok], e2[ok], m[ok], mid[ok], e["node"], kind, "branch", place)

    for fp in fs.footprints:
        host = fs.tubes[fp.host]
        t = host.t
        c_el = st[host.id].centres
        s_el, cc_el = host_coords(host, c_el, ref=float(fp.c[0]))
        smin, smax, cmin, cmax = fp.limits()
        for coord, lim, sign, label, key in (("s", smin, -1.0, "before", "smin"), ("s", smax, 1.0, "after", "smax"),
                                             ("c", cmin, -1.0, "one side", "cmin"),
                                             ("c", cmax, 1.0, "other side", "cmax")):
            x, y = (s_el, cc_el) if coord == "s" else (cc_el, s_el)
            if fp.extremes:  # a round branch: the true crown or saddle point
                ya = yb = fp.extremes[key][1]
            else:
                xr, yr = (fp.s, fp.c) if coord == "s" else (fp.c, fp.s)
                touch = np.abs(xr - lim) < CONTACT_TOL_T * t
                ya, yb = float(yr[touch].min()), float(yr[touch].max())
            dist = sign * (x - lim)
            one = np.nonzero(np.abs(dist - a_t * t) < STRIP_TOL_T * t)[0]
            two = np.nonzero(np.abs(dist - b_t * t) < STRIP_TOL_T * t)[0]
            if not len(one) or not len(two):
                continue
            if yb - ya >= 2.0 * CORNER_SKIP_T * t - 1e-9:
                one = one[(y[one] > ya + CORNER_SKIP_T * t - 1e-9) & (y[one] < yb - CORNER_SKIP_T * t + 1e-9)]
            else:  # a point of contact: the strip element over it
                ym = 0.5 * (ya + yb)
                one = one[[int(np.argmin(np.abs(y[one] - ym)))]]
                qs, qc = host_coords(host, fs.model.nodes[host.quads[one[0]]], ref=float(fp.c[0]))
                span = qc if coord == "s" else qs
                if not span.min() - 1e-9 <= ym <= span.max() + 1e-9:
                    continue  # no strip element over the point
            pairs = []
            for k in one:
                cand = two[np.abs(y[two] - y[k]) < CONTACT_TOL_T * t]
                if len(cand):
                    pairs.append((int(k), int(cand[np.argmin(np.abs(y[cand] - y[k]))])))
            if not pairs:
                continue
            e1 = np.array([p for p, _ in pairs])
            e2 = np.array([q for _, q in pairs])
            if coord == "s":
                dirs = np.tile(sign * host.d, (len(e1), 1))
            else:
                dirs = c_el[e2] - c_el[e1]
                dirs /= np.linalg.norm(dirs, axis=1, keepdims=True)
            feet = c_el[e1] - a_t * t * dirs
            emit(host, e1, e2, dirs, feet, fp.node, "tie", "host",
                 f"{host.id}, {label} of the footprint of {fp.branch} ({'along' if coord == 's' else 'around'} the host)")
    return out


def hot_spot_max(hs: list) -> dict | None:
    return max(hs, key=lambda h: abs(h["value"])) if hs else None


# ---------------------------------------------------------------- loads from given mount forces

MOUNT_KEYS = ("lca_front", "lca_rear", "uca_front", "uca_rear", "damper_upper", "tie_inner")


def unsprung_items(design: dict) -> list:
    """(kg, centre mm, support id) of what K2 leaves off the sprung body (wheel kinds, half of suspension kinds),
    each given to the support nearest its centre."""
    from ..k2_vehicle.mass import member_items, part_items

    unsprung, half = _k2_kinds()
    pos = {s["id"]: np.asarray(s["position_mm"], float) for s in design["supports"]}
    out = []
    for m, c, _, rec in part_items(design.get("parts") or []) + member_items(design):
        kind = str(rec.get("kind", "")).lower()
        k = 1.0 if kind in unsprung else 0.5 if kind in half else 0.0
        if k:
            cm = np.asarray(c, float) * 1000.0
            out.append((m * k, cm, min(pos, key=lambda sid: float(np.linalg.norm(pos[sid] - cm)))))
    return out


def relieved_loads(design: dict, mounts: dict, gravity_mps2) -> list:
    """BodyLoads of given mount forces ({support: {mount: force N}}, design axes, at the hardpoints) and of every
    sprung item (sprung_items) under gravity (m/s^2, design axes), with the inertia relief that balances them:
    the sprung body's rigid acceleration a and angular acceleration alpha from the forces' resultant about its
    centre of mass; each item gets m (g - a - alpha x r) and -I alpha."""
    gvec = np.asarray(gravity_mps2, float)
    sup = {s["id"]: s for s in design["supports"]}
    out = []
    for sid, fm in mounts.items():
        nodes = _mount_nodes(design, sup[sid])
        hp = sup[sid]["suspension"]["hardpoints_mm"]
        for mount, f in fm.items():
            if mount not in nodes:
                continue
            out.append(BodyLoad("mount", f"{sid}.{mount}", np.asarray(hp[mount], float), np.asarray(f, float),
                                np.zeros(3), [nodes[mount]]))
    items = sprung_items(design)
    m_s = sum(m for m, *_ in items)
    cg = sum(m * c for m, c, *_ in items) / m_s * 1000.0
    J = np.zeros((3, 3))
    for m, c, inertia, _ in items:
        r = c * 1000.0 - cg
        J += np.asarray(inertia, float) * 1e6 + m * (float(r @ r) * np.eye(3) - np.outer(r, r))
    F = sum((l.force_N for l in out), np.zeros(3)) + m_s * gvec
    M = sum((np.cross(l.point_mm - cg, l.force_N) for l in out), np.zeros(3))
    a = F / m_s  # m/s^2
    alpha = np.linalg.solve(J, M) * 1e3  # (N mm) / (kg mm^2) = 1e3 rad/s^2
    for m, c, inertia, rec in items:
        p = c * 1000.0
        f = m * (gvec - a - np.cross(alpha, p - cg) * 1e-3)
        mo = -(np.asarray(inertia, float) @ alpha) * 1000.0
        carriers = ([f"tube:{rec['id']}"] if rec.get("kind") == "frame_member" else
                    list(rec["carried_by"]) if rec.get("carried_by") else _nearest_corner_mounts(design, p))
        out.append(BodyLoad("item", rec["id"], p, f, mo, carriers))
    return out


# ---------------------------------------------------------------- one joint's stress


def joint_stress(fs: FrameShells, sol, node: str, radius_factor: float = 3.0, off_mm: float = 0.0) -> dict:
    """The largest von Mises (either surface) of the elements of the tubes meeting at a design node whose
    centres lie within radius_factor x the host's radius of it; with off_mm > 0 only elements whose centre
    is at least off_mm from every tied ring node at that joint (a read-out distance from the intersection
    line that does not shrink with the mesh)."""
    from .shell import shell_stresses

    j = fs.joints[node]
    tids = set(j["through"]) | {t for t, _ in j["ends"]}
    p = fs.xyz[node]
    rad = radius_factor * fs.tubes[fs.hosts[node]].R
    rings = []
    for t, end in j["ends"]:
        tb = fs.tubes[t]
        if tb.ends[end]["kind"] in ("branch", "mitre"):
            rings.append(fs.model.nodes[tb.ids[0] if end == "a" else tb.ids[-1]])
    R = np.vstack(rings) if rings else np.zeros((0, 3))
    best = (0.0, None)
    for t in tids:
        tb = fs.tubes[t]
        ss = shell_stresses(sol, tb.group)
        vm = ss.von_mises("max")
        keep = np.linalg.norm(ss.centres - p, axis=1) <= rad
        if off_mm > 0 and len(R):
            dmin = np.min(np.linalg.norm(ss.centres[:, None, :] - R[None, :, :], axis=2), axis=1)
            keep &= dmin >= off_mm
        if keep.any():
            k = int(np.argmax(np.where(keep, vm, -1.0)))
            if vm[k] > best[0]:
                best = (float(vm[k]), (t, ss.centres[k].tolist()))
    return {"MPa": best[0], "where": best[1]}


__all__ = ["element_values", "mesh_record", "prepare",
           "BodyLoad", "Footprint", "FrameShells", "MOUNT_KEYS", "Tube", "TubeMeshError", "add_mount", "analyse",
           "analyse_loads", "body_loads", "exit_param", "footprints", "frame_shells", "hold_321", "host_coords",
           "hot_spot_max", "hot_spots", "k2_states", "member_at", "member_tube_map", "perimeter", "perimeter_coord",
           "perimeter_point", "relieved_loads", "resolve_joints", "resultant_of", "section_points",
           "shell_load_vector", "spread", "sprung_items", "strip_steps", "tubes_of", "unsprung_items"]

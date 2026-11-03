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
from dataclasses import dataclass, field

import numpy as np

from ..k1_shell.mesh import facet_radius_factor, k1_axes
from .connect import distributing_link, rbe3_forces, rigid_link, tie
from .model import FEModel, StaticSolver
from .shell import MITC4

COLLINEAR_DEG = 0.5
MITRE_MAX_DEG = 35.0
MIN_BRANCH_DEG = 12.0
PATCH_FACTOR = 1.0
ELEMENT_SIZE_MM = 20.0
MIN_ROUND_FACETS = 8


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


def section_points(tb: Tube, size: float, facet: bool = True) -> tuple[np.ndarray, int]:
    """Offsets (n_c, 3) of the generator lines from the axis, counter-clockwise about d (from ey to ez)."""
    ey, ez = tb.axes()
    if tb.shape == "round_tube":
        R = tb.R
        n = max(MIN_ROUND_FACETS, 4 * math.ceil(2.0 * math.pi * R / size / 4.0 - 1e-9))
        rho = R * (facet_radius_factor(n) if facet else 1.0)
        a = 2.0 * math.pi * np.arange(n) / n
        return rho * (np.cos(a)[:, None] * ey + np.sin(a)[:, None] * ez), n
    hy, hz = (tb.dims["h"] - tb.t) / 2.0, (tb.dims["b"] - tb.t) / 2.0
    ny, nz = max(2, math.ceil(2 * hy / size - 1e-9)), max(2, math.ceil(2 * hz / size - 1e-9))
    pts = []
    # counter-clockwise about d seen with ey right and ez up: start at (+hy, -hz)
    for k in range(nz):
        pts.append((hy, -hz + 2 * hz * k / nz))
    for k in range(ny):
        pts.append((hy - 2 * hy * k / ny, hz))
    for k in range(nz):
        pts.append((-hy, hz - 2 * hz * k / nz))
    for k in range(ny):
        pts.append((-hy + 2 * hy * k / ny, -hz))
    P = np.array(pts)
    return P[:, 0:1] * ey + P[:, 1:2] * ez, len(P)


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


def _host_extensions(tubes: dict, size: float, offsets: dict) -> None:
    """Length each host end runs on past its node: the furthest point of its branches' cuts beyond the node
    along its axis, plus one element."""
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
                    he["ext"] = max(he["ext"], float(beyond.max()) + size)


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
                 max_gap: float | None = None) -> FrameShells:
    """The shell model of the frame's tubes (see the module docstring): MITC4 group per tube, ties at the
    joints. No supports, loads or mounts yet."""
    tubes, joints = tubes_of(design, members)
    hosts = resolve_joints(tubes, joints)
    offsets, counts = {}, {}
    for tb in tubes.values():
        offsets[tb.id], counts[tb.id] = section_points(tb, size, facet)
    _host_extensions(tubes, size, offsets)
    mats = design["materials"]
    model = FEModel(np.zeros((0, 3)))
    stub = 0.0
    for tb in tubes.values():
        offs = offsets[tb.id]
        n_c = counts[tb.id]
        s0, s1 = _cuts(tubes, tb, offs)
        if np.any(s1 - s0 <= 1e-6):
            raise TubeMeshError(f"tube {tb.id}: its end cuts overlap (too short for its joints)")
        rows = max(2, math.ceil(float((s1 - s0).max()) / size - 1e-9))
        f = np.linspace(0.0, 1.0, rows + 1)
        S = s0[None, :] + f[:, None] * (s1 - s0)[None, :]
        P = tb.p0[None, None, :] + offs[None, :, :] + S[:, :, None] * tb.d[None, None, :]
        tb.ids = model.add_nodes(P.reshape(-1, 3)).reshape(rows + 1, n_c)
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
            if not len(ring):
                continue
            node = xyz[e["node"]]
            reach = float(np.linalg.norm(model.nodes[ring] - node, axis=1).max()) + 2.0 * size + master.R
            cen = model.nodes[master.quads].mean(axis=1)
            near = master.quads[np.linalg.norm(cen - node, axis=1) <= reach]
            gap = max_gap if max_gap is not None else max(0.5 * size, 0.25 * master.R)
            try:
                c = model.add(tie(ring, near, model.nodes, name=f"tie {tb.id}.{end} on {master.id}", max_gap=gap))
            except ValueError as err:
                raise TubeMeshError(f"tube {tb.id} at node {e['node']}: {err}") from None
            ties.append(c)
    return FrameShells(model, tubes, joints, hosts, ties, xyz, size, stub)


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
    state of step_push's records nearest the snapshot time). Returns {"veh", "static", "lift", "push"}."""
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
    from . import analysis
    from .shell import shell_stresses

    states = states or k2_states(design, step_height_mm)
    veh = states["veh"]
    fs = frame_shells(design, size, facet)
    mounts = sorted({n for s in design["supports"] for n in _mount_nodes(design, s).values()})
    for n in mounts:
        add_mount(fs, n)
    held = hold_321(fs, design)
    mt = member_tube_map(fs)
    patch_nodes = set(np.concatenate([fs.patch(n) for n in mounts]).tolist())
    cases, solutions = {}, {}
    size_mm = float(np.ptp(fs.model.nodes, axis=0).max())
    for name in ("static", "lift"):
        loads = body_loads(design, veh, states[name])
        F = shell_load_vector(fs, loads, mt)
        res = analysis.run_static(fs.model, F)
        # run_static's equilibrium_residual divides by the larger of the applied and reacted resultants,
        # both near zero for a balanced free body: left out, this record's own residuals below replace it
        out = {"status": res["status"], "reasons": res["reasons"], "quality": res["quality"],
               "results": [x for x in res["results"] if x["quantity"] != "equilibrium_residual"],
               "t_s": float(states[name]["t"])}
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
                away = np.array([not any(int(i) in patch_nodes or int(i) in held for i in q) for q in tb.quads])
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
        cases[name] = out
    return {"cases": cases, "n_nodes": int(len(fs.model.nodes)), "n_elements": int(sum(len(t.quads) for t in fs.tubes.values())),
            "n_ties": len(fs.ties), "stub_mass_kg": fs.stub_mass_kg,
            "shell_mass_kg": float(sum(t.group.mass_total(fs.model.nodes) for t in fs.tubes.values()) * 1000.0),
            "held": held, "shells": fs, "solutions": solutions}


# ---------------------------------------------------------------- the beam model under the same loads


def beam_model(design: dict, n_per_member: int = 4) -> dict:
    """The design's members as fe.beam 3D beams (n_per_member elements each), E, G = E / (2 (1 + nu)) and
    the section's A, Iy, Iz, J from the K0 section formulas (kit.sections.table), joints rigid at the
    design nodes. Returns {"model", "node" {design node: index}}."""
    from ...designgen.kit.sections import table
    from .beam import Beam

    xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    names = list(xyz)
    index = {n: i for i, n in enumerate(names)}
    pts = [xyz[n] for n in names]
    props = table(design["sections"])
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


__all__ = ["BodyLoad", "FrameShells", "Tube", "TubeMeshError", "add_mount", "analyse", "body_loads", "exit_param",
           "frame_shells", "hold_321", "k2_states", "member_tube_map", "resolve_joints", "resultant_of",
           "section_points", "shell_load_vector", "spread", "sprung_items", "tubes_of"]

"""Mesh quality before the solve (pre-processing check), for shells (3- and 4-node) and solids (HEX8, HEX20).

Per element:
- aspect_ratio: longest edge / shortest edge (corner nodes).
- warp_deg (quads, and the faces of bricks): the larger of the two angles between the normals of the
  triangle pairs on the two diagonals; 0 for a flat quad.
- skew_deg: 90 deg minus the angle between the two lines joining the mid-points of opposite edges
  (0 for a rectangle); for bricks the worst face.
- min_angle_deg, max_angle_deg: interior corner angles (bricks: of their faces).
- jacobian_ratio: min det J / max det J over the integration points and the corner nodes
  (shells: 2 x 2 points in the element's mean plane; HEX8: 2 x 2 x 2; HEX20: 3 x 3 x 3; corner nodes are
  added so a collapsed corner shows as 0). <= 0 means an inverted or collapsed element.
- taper: max over the four corner triangles of |A_i - mean| / mean (A_i the triangle at corner i);
  0 for a parallelogram.

Topology: free edges of the shells (an edge used by one shell element; the edge of an open tube end is
one, a hole in a wall that should be closed adds a loop), coincident nodes (used nodes closer than
1e-4 of the median edge), connected groups (elements and links join nodes), shell normals that disagree
with their neighbours across a shared edge, solids with negative volume.

Limits (LIMITS) are rules of thumb for this solver, assumptions, not taken from a source: typical values
quoted for pre-processors' element checks, not checked against any program's documentation here.

Verdict: fail (the solver does not solve) for a negative volume, a jacobian ratio <= 0, or a group that
is joined to nothing and carries no load; warn when a metric is past its limit, nodes coincide, normals
disagree, free-edge loops exceed the expected number or groups are disconnected (load unknown);
ok otherwise. The record follows the contract's mesh.quality (metrics, per_element, topology, verdict,
notes).
"""

from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree

from .solid import CORNER_SIGNS, FACES8, gauss_3d, shape_hex8, shape_hex20

# metric -> (limit, "max" if larger is worse else "min")
LIMITS = {
    "aspect_ratio": (5.0, "max"),
    "warp_deg": (10.0, "max"),
    "skew_deg": (45.0, "max"),
    "min_angle_deg": (30.0, "min"),
    "max_angle_deg": (150.0, "max"),
    "jacobian_ratio": (0.6, "min"),
    "taper": (0.5, "max"),
    "tri_min_angle_deg": (20.0, "min"),
    "tri_max_angle_deg": (120.0, "max"),
}
LIMIT_NOTE = ("limits are assumed rules of thumb for this solver (no source checked): aspect <= 5, warp <= 10 deg, "
              "skew <= 45 deg, quad angles 30..150 deg, triangle angles 20..120 deg, jacobian ratio >= 0.6, "
              "taper <= 0.5; fail at jacobian ratio <= 0 or negative volume")


def _angle(u, v):
    c = np.einsum("...i,...i->...", u, v) / np.maximum(np.linalg.norm(u, axis=-1) * np.linalg.norm(v, axis=-1), 1e-300)
    return np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))


def quad_metrics(X: np.ndarray) -> dict:
    """Metrics of quadrilaterals X (E, 4, 3)."""
    e = np.roll(X, -1, axis=1) - X  # edge i: node i -> i+1
    L = np.linalg.norm(e, axis=2)
    aspect = L.max(axis=1) / np.maximum(L.min(axis=1), 1e-300)
    ang = _angle(np.roll(X, 1, axis=1) - X, np.roll(X, -1, axis=1) - X)  # angle at each corner
    # warp: normals of the two triangles on each diagonal
    def tri_n(a, b, c):
        return np.cross(X[:, b] - X[:, a], X[:, c] - X[:, a])
    w1 = _angle(tri_n(0, 1, 2), tri_n(0, 2, 3))
    w2 = _angle(tri_n(0, 1, 3), tri_n(1, 2, 3))
    warp = np.maximum(w1, w2)
    mid = 0.5 * (X + np.roll(X, -1, axis=1))
    skew = np.abs(90.0 - _angle(mid[:, 2] - mid[:, 0], mid[:, 1] - mid[:, 3]))
    # corner triangle areas (taper)
    A = 0.5 * np.linalg.norm(np.cross(np.roll(X, -1, axis=1) - X, np.roll(X, 1, axis=1) - X), axis=2)
    Am = A.mean(axis=1)
    taper = np.abs(A - Am[:, None]).max(axis=1) / np.maximum(Am, 1e-300)
    # jacobian in the mean plane (Newell normal)
    n = np.cross(X[:, 2] - X[:, 0], X[:, 3] - X[:, 1])
    nn = np.linalg.norm(n, axis=1)
    n = n / np.maximum(nn, 1e-300)[:, None]
    g1 = 0.5 * ((X[:, 1] + X[:, 2]) - (X[:, 0] + X[:, 3]))
    e1 = g1 - np.einsum("ei,ei->e", g1, n)[:, None] * n
    e1 /= np.maximum(np.linalg.norm(e1, axis=1), 1e-300)[:, None]
    e2 = np.cross(n, e1)
    c = X.mean(axis=1)
    xl = np.stack([np.einsum("eni,ei->en", X - c[:, None], e1), np.einsum("eni,ei->en", X - c[:, None], e2)], axis=2)
    xi_n = np.array([-1.0, 1.0, 1.0, -1.0])
    eta_n = np.array([-1.0, -1.0, 1.0, 1.0])
    g = 1.0 / np.sqrt(3.0)
    pts = [(-g, -g), (g, -g), (g, g), (-g, g)] + list(zip(xi_n, eta_n))
    dets = []
    for xi, eta in pts:
        dN = np.array([0.25 * xi_n * (1 + eta_n * eta), 0.25 * eta_n * (1 + xi_n * xi)])
        J = np.einsum("ak,ekb->eab", dN, xl)
        dets.append(J[:, 0, 0] * J[:, 1, 1] - J[:, 0, 1] * J[:, 1, 0])
    dets = np.stack(dets, axis=1)
    dmax = dets.max(axis=1)
    jr = np.where(dmax > 0, dets.min(axis=1) / np.where(dmax > 0, dmax, 1.0), -1.0)
    jr = np.where(nn > 0, jr, 0.0)
    return {"aspect_ratio": aspect, "warp_deg": warp, "skew_deg": skew, "min_angle_deg": ang.min(axis=1),
            "max_angle_deg": ang.max(axis=1), "jacobian_ratio": jr, "taper": taper, "normal": n}


def tri_metrics(X: np.ndarray) -> dict:
    e = np.roll(X, -1, axis=1) - X
    L = np.linalg.norm(e, axis=2)
    ang = _angle(np.roll(X, 1, axis=1) - X, np.roll(X, -1, axis=1) - X)
    n = np.cross(X[:, 1] - X[:, 0], X[:, 2] - X[:, 0])
    nn = np.linalg.norm(n, axis=1)
    return {"aspect_ratio": L.max(axis=1) / np.maximum(L.min(axis=1), 1e-300),
            "tri_min_angle_deg": ang.min(axis=1), "tri_max_angle_deg": ang.max(axis=1),
            "jacobian_ratio": np.where(nn > 1e-12 * L.max(axis=1) ** 2, 1.0, 0.0),
            "normal": n / np.maximum(nn, 1e-300)[:, None]}


def hex_metrics(X: np.ndarray) -> dict:
    """Metrics of bricks X (E, 8 or 20, 3) in the solid.py node order."""
    C = X[:, :8]
    from .solid import EDGES
    L = np.linalg.norm(C[:, EDGES[:, 1]] - C[:, EDGES[:, 0]], axis=2)
    aspect = L.max(axis=1) / np.maximum(L.min(axis=1), 1e-300)
    faces = [quad_metrics(C[:, f]) for f in FACES8]
    def worst(key, fn):
        return fn(np.stack([f[key] for f in faces], axis=1), axis=1)
    shp = shape_hex20 if X.shape[1] == 20 else shape_hex8
    P, W = gauss_3d(3 if X.shape[1] == 20 else 2)
    pts = np.vstack([P, CORNER_SIGNS])
    dets = []
    for p in pts:
        _, dN = shp(*p)
        dets.append(np.linalg.det(np.einsum("an,enb->eab", dN, X)))
    dets = np.stack(dets, axis=1)
    vol = dets[:, :len(P)] @ W
    dmax = dets.max(axis=1)
    jr = np.where(dmax > 0, dets.min(axis=1) / np.where(dmax > 0, dmax, 1.0), -1.0)
    return {"aspect_ratio": aspect, "warp_deg": worst("warp_deg", np.max), "skew_deg": worst("skew_deg", np.max),
            "min_angle_deg": worst("min_angle_deg", np.min), "max_angle_deg": worst("max_angle_deg", np.max),
            "jacobian_ratio": jr, "taper": worst("taper", np.max), "volume": vol}


def _union_groups(n_nodes: int, cells, links=()) -> np.ndarray:
    parent = np.arange(n_nodes)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for row in list(cells) + [list(l) for l in links]:
        row = [int(i) for i in row]
        r0 = find(row[0])
        for i in row[1:]:
            ri = find(i)
            if ri != r0:
                parent[ri] = r0
    return np.array([find(i) for i in range(n_nodes)])


def _free_edges(shells: list) -> tuple[np.ndarray, dict]:
    """Free edges (k, 2) and the map edge -> [(element, a, b) in element order] of shell elements."""
    use: dict = {}
    for ei, el in enumerate(shells):
        k = len(el)
        for i in range(k):
            a, b = int(el[i]), int(el[(i + 1) % k])
            use.setdefault((min(a, b), max(a, b)), []).append((ei, a, b))
    free = np.array([e for e, v in use.items() if len(v) == 1], int).reshape(-1, 2)
    return free, use


def _loops(edges: np.ndarray) -> int:
    if len(edges) == 0:
        return 0
    ids = np.unique(edges)
    loc = {int(v): i for i, v in enumerate(ids)}
    lab = _union_groups(len(ids), [[loc[int(a)], loc[int(b)]] for a, b in edges])
    return int(len(np.unique(lab)))


def _flipped(n_el: int, use: dict, labels=None) -> np.ndarray:
    """Shell elements whose orientation disagrees with their neighbours (per connected patch of manifold
    edges, the smaller side). With labels (member or part per element) only edges between elements of the
    same label count: where two tubes meet along a line (an L joint's corner) both are outward and their
    orientations disagree by geometry, not by mistake."""
    adj = [[] for _ in range(n_el)]
    for v in use.values():
        if len(v) == 2:
            (e1, a1, b1), (e2, a2, b2) = v
            if labels is not None and labels[e1] != labels[e2]:
                continue
            same = (a1 == a2)  # both traverse the edge in the same direction: orientations disagree
            adj[e1].append((e2, same))
            adj[e2].append((e1, same))
    sign = np.zeros(n_el, int)
    out = []
    for s in range(n_el):
        if sign[s]:
            continue
        sign[s] = 1
        stack, comp = [s], [s]
        conflicts = 0
        while stack:
            i = stack.pop()
            for j, same in adj[i]:
                want = -sign[i] if same else sign[i]
                if sign[j] == 0:
                    sign[j] = want
                    stack.append(j)
                    comp.append(j)
                elif sign[j] != want:
                    conflicts += 1
        comp = np.array(comp)
        neg = comp[sign[comp] < 0]
        pos = comp[sign[comp] > 0]
        out += (neg if len(neg) <= len(pos) else pos).tolist()
    return np.array(sorted(out), int)


def _summary(values: np.ndarray, name: str, offset_index: np.ndarray) -> dict:
    lim, kind = LIMITS[name]
    v = np.asarray(values, float)
    if kind == "max":
        i = int(np.argmax(v))
        over = int(np.sum(v > lim))
        p95 = float(np.percentile(v, 95))
    else:
        i = int(np.argmin(v))
        over = int(np.sum(v < lim))
        p95 = float(np.percentile(v, 5))
    return {"worst": float(v[i]), "p95": p95, "limit": float(lim), "over_limit": over,
            "worst_element": int(offset_index[i])}


def check_mesh(nodes, shells=None, solids=None, links=(), loaded_nodes=None, expected_free_loops: int | None = None,
               per_element: bool = False, shell_labels=None) -> dict:
    """Quality of a mesh: nodes (N, 3); shells: list of 3/4-node rows; solids: list of 8/20-node rows (element
    indices count shells first, then solids, in the order given); links: node groups joined by connections;
    loaded_nodes: nodes with loads or supports (when known, a group without any is a fail); shell_labels:
    member or part of each shell element (normals are compared within one label).

    Returns {"quality": contract mesh.quality, "fail": [reasons], "warn": [reasons], "groups": labels, ...}."""
    nodes = np.asarray(nodes, float)
    shells = [list(map(int, e)) for e in ([] if shells is None else shells)]
    solids = [list(map(int, e)) for e in ([] if solids is None else solids)]
    n_sh = len(shells)
    vals: dict[str, list] = {}
    idx: dict[str, list] = {}
    pe: dict[str, np.ndarray] = {}
    n_total = n_sh + len(solids)

    def add(name, v, where):
        vals.setdefault(name, []).append(np.asarray(v, float))
        idx.setdefault(name, []).append(np.asarray(where, int))

    quads = [i for i, e in enumerate(shells) if len(e) == 4]
    tris = [i for i, e in enumerate(shells) if len(e) == 3]
    normals = np.zeros((n_sh, 3))
    if quads:
        qm = quad_metrics(nodes[np.array([shells[i] for i in quads])])
        normals[quads] = qm.pop("normal")
        for k, v in qm.items():
            add(k, v, quads)
    if tris:
        tm = tri_metrics(nodes[np.array([shells[i] for i in tris])])
        normals[tris] = tm.pop("normal")
        for k, v in tm.items():
            add(k, v, tris)
    neg_vol = 0
    for npe in (8, 20):
        sel = [i for i, e in enumerate(solids) if len(e) == npe]
        if sel:
            hm = hex_metrics(nodes[np.array([solids[i] for i in sel])])
            vol = hm.pop("volume")
            neg_vol += int(np.sum(vol <= 0))
            where = n_sh + np.array(sel)
            for k, v in hm.items():
                add(k, v, where)
    metrics = {}
    for k in vals:
        v = np.concatenate(vals[k])
        w = np.concatenate(idx[k])
        metrics[k] = _summary(v, k, w)
        if per_element:
            full = np.full(n_total, np.nan)
            full[w] = v
            pe[k] = full
    # topology
    free, use = _free_edges(shells)
    loops = _loops(free)
    used = np.zeros(len(nodes), bool)
    for row in shells + solids:
        used[row] = True
    for l in links:
        used[list(l)] = True
    ids = np.nonzero(used)[0]
    edge_len = []
    for row in shells:
        x = nodes[row]
        edge_len.append(np.linalg.norm(np.roll(x, -1, axis=0) - x, axis=1))
    for row in solids:
        from .solid import EDGES
        x = nodes[row[:8]]
        edge_len.append(np.linalg.norm(x[EDGES[:, 1]] - x[EDGES[:, 0]], axis=1))
    med = float(np.median(np.concatenate(edge_len))) if edge_len else 1.0
    pairs = cKDTree(nodes[ids]).query_pairs(1e-4 * med, output_type="ndarray") if len(ids) else np.zeros((0, 2))
    coincident = int(len(pairs))
    labels = _union_groups(len(nodes), shells + solids, links)
    groups = np.unique(labels[ids]) if len(ids) else np.array([])
    flipped_ids = _flipped(n_sh, use, shell_labels) if n_sh else np.zeros(0, int)
    flipped = len(flipped_ids)
    topo = {"free_edges": int(len(free)), "coincident_nodes": coincident,
            "unconnected_groups": int(max(len(groups) - 1, 0)), "flipped_normals": int(flipped),
            "negative_volume": int(neg_vol)}
    fail, warn = [], []
    if neg_vol:
        fail.append(f"{neg_vol} solid element(s) with negative volume")
    if "jacobian_ratio" in metrics and metrics["jacobian_ratio"]["worst"] <= 0.0:
        n_bad = int(sum(np.sum(v <= 0) for v in vals["jacobian_ratio"]))
        fail.append(f"{n_bad} element(s) with jacobian ratio <= 0 (inverted or collapsed; worst element "
                    f"{metrics['jacobian_ratio']['worst_element']})")
    if len(groups) > 1:
        if loaded_nodes is not None:
            loaded = set(int(n) for n in np.atleast_1d(loaded_nodes))
            for gr in groups:
                members = ids[labels[ids] == gr]
                if not loaded.intersection(members.tolist()):
                    fail.append(f"a group of {len(members)} node(s) (first node {int(members[0])}) is joined to "
                                f"nothing and has no load or support")
        else:
            warn.append(f"{len(groups)} unconnected groups (loads not given, so not judged)")
    for k, m in metrics.items():
        if m["over_limit"] and not (k == "jacobian_ratio" and m["worst"] <= 0):
            warn.append(f"{k}: {m['over_limit']} element(s) past {m['limit']:g} (worst {m['worst']:.3g} at element "
                        f"{m['worst_element']})")
    if coincident:
        warn.append(f"{coincident} pair(s) of coincident nodes (not joined: a crack in the mesh)")
    if flipped:
        warn.append(f"{flipped} shell element(s) with normals opposite to their neighbours")
    if expected_free_loops is not None and loops > expected_free_loops:
        warn.append(f"{loops} loops of free shell edges, {expected_free_loops} expected (a hole in a wall that "
                    f"should be closed)")
    verdict = "fail" if fail else ("warn" if warn else "ok")
    notes = [LIMIT_NOTE, f"free-edge loops: {loops}"] + [f"FAIL: {r}" for r in fail] + [f"WARN: {r}" for r in warn]
    q = {"verdict": verdict, "metrics": metrics, "topology": topo, "notes": "; ".join(notes)}
    if per_element:  # only metrics that every element has (JSON has no NaN)
        q["per_element"] = {k: [float(x) for x in v] for k, v in pe.items() if not np.any(np.isnan(v))}
    return {"quality": q, "fail": fail, "warn": warn, "groups": labels, "free_edge_loops": loops,
            "flipped_elements": flipped_ids, "free_edges": free}


def record_quality(rec: dict, links=(), loaded_nodes=None, expected_free_loops=None, per_element: bool = False) -> dict:
    """mesh.quality of a contract shell `mesh` record (e.g. hiraku.solvers.frame_mesh(design)).
    links: node groups joined by connections that the record does not list (rigid links of tied joints)."""
    els = rec["elements"]
    kind = rec.get("element_type", "shell")
    shells = els if kind == "shell" else [e for e in els if len(e) <= 4]
    solids = [] if kind == "shell" else [e for e in els if len(e) >= 8]
    labels = rec.get("element_member") if kind == "shell" else None
    return check_mesh(rec["nodes_mm"], shells, solids, links, loaded_nodes, expected_free_loops, per_element,
                      labels)["quality"]


def model_quality(model, loaded_nodes=None, expected_free_loops=None, per_element: bool = False) -> dict:
    """check_mesh of an FEModel: shells are the 4-node MITC4 groups and the 3-node shell triangles (tri.Tri3),
    solids the 8/20-node groups, links every constraint's nodes and every other element group's rows
    (springs). Each group is its own label, so normals are compared within a group only."""
    shells, solids, links, labels = [], [], [], []
    for gi, g in enumerate(model.groups):
        n = g.conn.shape[1]
        if g.n_comp == 6 and n in (3, 4) and (g.kind == "mitc4" or getattr(g, "shell", False)):
            shells += g.conn.tolist()
            labels += [gi] * len(g.conn)
        elif n in (8, 20):
            solids += g.conn.tolist()
        else:
            links += [[int(i) for i in r if i >= 0] for r in g.conn.tolist()]
    for c in model.constraints:
        nodes = set(c.nodes)
        for eq in c.equations:
            nodes.update(t[0] for t in eq.terms)
        if nodes:
            links.append(sorted(nodes))
    links = [l for l in links if l]
    return check_mesh(model.nodes, shells, solids, links, loaded_nodes, expected_free_loops, per_element, labels)

"""Mesh quality measured apart from the solver (check_run reads a mesh with it).

Shell elements have 3 or 4 nodes in order. Per element:
- aspect_ratio: longest edge / shortest edge
- min_angle_deg, max_angle_deg: the corner angles between each node's two edges
- area: |A| with the vector area A = (p2 - p0) x (p3 - p1) / 2 (a quad) or (p1 - p0) x (p2 - p0) / 2;
  n = A / |A| is the element's normal
- jacobian_ratio: min / max over the corners of J_i = ((p_next - p_i) x (p_prev - p_i)) . n. 1 for a
  parallelogram, 0 or less when a corner folds back (a bow-tie, a re-entrant or collapsed corner)
- warp_deg (quads): the larger, over the two diagonals, of the angle between the normals of the two
  triangles the diagonal cuts the quad into
- degenerate: an edge or the area (against the longest edge squared) below the tolerance

Topology of the mesh:
- free_edges: edges of exactly one element (a tube's open ends have them)
- coincident_nodes: nodes with another node within tol_mm (not merged: a crack)
- unconnected_groups: groups of elements joined through shared nodes
- orientation_conflicts: edges of exactly two elements of the same member that both run the same way
  (one of them faces the other side); seam_conflicts: the same between two members, where it is
  expected (each tube's elements face out of their own tube); joint_edges: edges of three or more
  elements (walls meeting at a joint), where facing has no one meaning

Solid hexahedra (8 nodes: 0-3 the bottom face, counter-clockwise seen from above; node i + 4 above
node i): hex_corner_jacobians and hex_volume.
"""

from __future__ import annotations

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

TOL_MM = 1e-3  # nodes closer than this are the same point (the mesher rounds to 1e-4 mm)
REL_AREA = 1e-9  # area below this times the longest edge squared is no area


def _unit(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return np.divide(v, n, out=np.zeros_like(v), where=n > 0)


def _angle(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.degrees(np.arccos(np.clip(np.sum(_unit(a) * _unit(b), axis=-1), -1.0, 1.0)))


def _elements(P: np.ndarray, conn: np.ndarray) -> dict[str, np.ndarray]:
    """Measures of same-size elements: P (N, 3), conn (E, k) with k 3 or 4."""
    X = P[conn]  # (E, k, 3)
    nxt, prv = np.roll(X, -1, axis=1) - X, np.roll(X, 1, axis=1) - X
    edges = np.linalg.norm(nxt, axis=2)
    if conn.shape[1] == 4:
        A = 0.5 * np.cross(X[:, 2] - X[:, 0], X[:, 3] - X[:, 1])
        warp = np.maximum(
            _angle(np.cross(X[:, 1] - X[:, 0], X[:, 2] - X[:, 0]), np.cross(X[:, 2] - X[:, 0], X[:, 3] - X[:, 0])),
            _angle(np.cross(X[:, 2] - X[:, 1], X[:, 3] - X[:, 1]), np.cross(X[:, 3] - X[:, 1], X[:, 0] - X[:, 1])))
    else:
        A = 0.5 * np.cross(X[:, 1] - X[:, 0], X[:, 2] - X[:, 0])
        warp = np.zeros(len(conn))
    area = np.linalg.norm(A, axis=1)
    n = _unit(A)
    J = np.einsum("ekj,ej->ek", np.cross(nxt, prv), n)
    longest, shortest = edges.max(axis=1), edges.min(axis=1)
    jmax = J.max(axis=1)
    ratio = np.divide(J.min(axis=1), jmax, out=np.full(len(conn), -np.inf), where=jmax > 0)
    angles = _angle(nxt, prv)
    degenerate = (shortest <= TOL_MM) | (area <= REL_AREA * longest**2)
    return {"aspect_ratio": np.divide(longest, shortest, out=np.full(len(conn), np.inf), where=shortest > 0),
            "min_angle_deg": angles.min(axis=1), "max_angle_deg": angles.max(axis=1), "area": area,
            "jacobian_ratio": np.where(degenerate, -np.inf, ratio), "warp_deg": warp, "degenerate": degenerate}


def measure(nodes_mm, elements, element_member=None) -> dict:
    """Per-element measures (arrays in element order) and the topology of a shell mesh.

    With element_member, facing is compared only between elements of the same member: each tube's
    elements face out of that tube, so where two tubes meet there is no one side to face (those
    edges are counted as seam_conflicts)."""
    P = np.asarray(nodes_mm, float).reshape(-1, 3)
    n_el = len(elements)
    out = {k: np.zeros(n_el) for k in ("aspect_ratio", "min_angle_deg", "max_angle_deg", "area",
                                      "jacobian_ratio", "warp_deg")}
    out["degenerate"] = np.zeros(n_el, bool)
    for k in (3, 4):
        idx = [i for i, e in enumerate(elements) if len(e) == k]
        if idx:
            part = _elements(P, np.asarray([elements[i] for i in idx], int))
            for name, values in part.items():
                out[name][idx] = values
    # directed edges of every element
    tails = np.concatenate([np.asarray(e, int) for e in elements]) if n_el else np.zeros(0, int)
    heads = np.concatenate([np.roll(np.asarray(e, int), -1) for e in elements]) if n_el else np.zeros(0, int)
    owner = np.repeat(np.arange(n_el), [len(e) for e in elements])
    lo, hi = np.minimum(tails, heads), np.maximum(tails, heads)
    keys = lo.astype(np.int64) * (len(P) + 1) + hi
    _, inverse, counts = np.unique(keys, return_inverse=True, return_counts=True)
    free_edges = int(np.sum(counts == 1))
    # an edge of exactly two elements is run once each way when they face the same side; where three or
    # more walls meet (a joint) there is no one way to face, so those edges are left out
    pair = counts[inverse] == 2
    order = np.argsort(inverse[pair], kind="stable")
    t2 = tails[pair][order].reshape(-1, 2)
    same_way = t2[:, 0] == t2[:, 1]
    if element_member is not None and len(element_member) == n_el:
        members = np.asarray(element_member, dtype=object)
        o2 = owner[pair][order].reshape(-1, 2)
        one_member = members[o2[:, 0]] == members[o2[:, 1]]
    else:
        one_member = np.ones(len(t2), bool)
    conflicts = int(np.sum(same_way & one_member))
    seam_conflicts = int(np.sum(same_way & ~one_member))
    non_manifold = int(np.sum(counts > 2))
    pairs = cKDTree(P).query_pairs(TOL_MM) if len(P) else set()
    coincident = len({i for pair in pairs for i in pair})
    if n_el:
        # elements joined when they share a node: element -> node incidence, then components
        inc = coo_matrix((np.ones(len(tails)), (owner, tails)), shape=(n_el, len(P))).tocsr()
        groups = connected_components(inc @ inc.T, directed=False)[0]
    else:
        groups = 0
    out["topology"] = {"free_edges": free_edges, "coincident_nodes": coincident, "unconnected_groups": int(groups),
                       "orientation_conflicts": conflicts, "seam_conflicts": seam_conflicts,
                       "joint_edges": non_manifold,
                       "degenerate": int(out["degenerate"].sum()),
                       "inverted": int(np.sum(~out["degenerate"] & (out["jacobian_ratio"] <= 0)))}
    return out


# --- solid hexahedra --------------------------------------------------------------------
_HEX_CORNERS = [(1, 3, 4), (2, 0, 5), (3, 1, 6), (0, 2, 7), (7, 5, 0), (4, 6, 1), (5, 7, 2), (6, 4, 3)]


def hex_corner_jacobians(points) -> np.ndarray:
    """det[e1, e2, e3] at each corner, e the edges to its three neighbours in a right-handed order:
    positive at every corner of a proper hexahedron, negative where it is turned inside out."""
    X = np.asarray(points, float).reshape(8, 3)
    return np.array([np.dot(X[a] - X[i], np.cross(X[b] - X[i], X[c] - X[i])) for i, (a, b, c) in enumerate(_HEX_CORNERS)])


def hex_volume(points) -> float:
    """Signed volume: the six tetrahedra around the diagonal 0-6 (negative when inside out)."""
    X = np.asarray(points, float).reshape(8, 3)
    tets = [(0, 1, 2, 6), (0, 2, 3, 6), (0, 3, 7, 6), (0, 7, 4, 6), (0, 4, 5, 6), (0, 5, 1, 6)]
    return float(sum(np.dot(X[b] - X[a], np.cross(X[c] - X[a], X[d] - X[a])) / 6.0 for a, b, c, d in tets))

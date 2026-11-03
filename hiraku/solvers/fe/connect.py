"""Connections: rigid links (RBE2), distributing links (RBE3), springs and bushings, shell-to-solid coupling,
and ties between meshes whose nodes do not match.

All links are constraint equations on the fe core (model.Constraint), eliminated with the rest; springs
are an element group. Formulas are written here; sources are named at each one.
"""

from __future__ import annotations

import numpy as np

from .model import Constraint, ElementGroup, Equation


def skew(r: np.ndarray) -> np.ndarray:
    """[r]x: [r]x a = r x a."""
    x, y, z = r
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


# ------------------------------------------------------------------ RBE2


def rigid_link(master: int, slaves, nodes: np.ndarray, name: str = "rbe2", comps: str = "all") -> Constraint:
    """Rigid link (RBE2 type): every slave follows the master rigidly,
        u_s = u_m + theta_m x (x_s - x_m),  theta_s = theta_m  (the latter when the slave has rotations).
    Small-rotation rigid-body kinematics (Cook et al., Concepts and Applications of FEA, 4th ed., section on
    rigid links; not checked here). The master carries 6 DOFs; comps "xyz" ties translations only."""
    master = int(master)
    slaves = [int(s) for s in np.atleast_1d(slaves) if int(s) != master]
    xm = np.asarray(nodes[master], float).copy()
    xs = {s: np.asarray(nodes[s], float).copy() for s in slaves}

    def make(counts):
        eqs = []
        for s in slaves:
            S = -skew(xs[s] - xm)  # theta x r = -r x theta = -[r]x theta
            for i in range(3):
                terms = [(s, i, 1.0), (master, i, -1.0)]
                terms += [(master, 3 + j, -S[i, j]) for j in range(3) if S[i, j] != 0.0]
                eqs.append(Equation(terms, prefer=(s, i), source=name))
            if comps == "all" and counts[s] == 6:
                for j in range(3):
                    eqs.append(Equation([(s, 3 + j, 1.0), (master, 3 + j, -1.0)], prefer=(s, 3 + j), source=name))
        return eqs

    return Constraint(name, needs_rotation=(master,), nodes=tuple([master] + slaves), maker=make)


# ------------------------------------------------------------------ springs and bushings


class Spring(ElementGroup):
    """Springs and bushings between two nodes (or a node and the ground: b = -1).

    k (E, 6) or (6,): stiffness along the spring's local axes, (kx, ky, kz) in N/mm and (krx, kry, krz) in
    N*mm/rad. frames (E, 3, 3) or (3, 3): rows are the local axes in global coordinates (identity when not
    given). The relative motion is taken at the point p (the mid-point of the two nodes unless given),
    each node carried to p rigidly:
        du = (u_b + theta_b x (p - x_b)) - (u_a + theta_a x (p - x_a)),  dtheta = theta_b - theta_a,
    K = B^T R6^T diag(k) R6 B with R6 = diag(R, R). So a rigid motion of both nodes stretches nothing, also
    when the nodes are apart (a bushing with offsets, as Nastran's CBUSH is commonly described; the program's
    documentation not checked here). With only translational stiffness (rotations=False) the group uses
    3 DOFs per node and no offsets: then the nodes should coincide, or the stiffness lie along the line
    between them, for a rigid rotation to stay free of energy.
    """

    kind = "spring"

    def __init__(self, conn, k, frames=None, points=None, rotations: bool | None = None, name: str = "spring"):
        self.conn = np.asarray(conn, int).reshape(-1, 2)
        ne = len(self.conn)
        self.k = np.broadcast_to(np.asarray(k, float), (ne, 6)).copy()
        self.frames = np.broadcast_to(np.eye(3) if frames is None else np.asarray(frames, float), (ne, 3, 3)).copy()
        self.points = None if points is None else np.broadcast_to(np.asarray(points, float), (ne, 3)).copy()
        if rotations is None:
            rotations = bool(np.any(self.k[:, 3:] != 0.0))
        if not rotations and np.any(self.k[:, 3:] != 0.0):
            raise ValueError("rotational stiffness needs rotations=True (6 DOFs per node)")
        self.n_comp = 6 if rotations else 3
        self.name = name

    def relative_motion(self, nodes: np.ndarray) -> np.ndarray:
        """L = R6 B (E, nc, 2 nc): the relative motion at p in the spring's local axes, L ue."""
        ne = len(self.conn)
        a, b = self.conn[:, 0], self.conn[:, 1]
        xa = nodes[a]
        xb = np.where((b >= 0)[:, None], nodes[np.where(b >= 0, b, 0)], xa)
        p = 0.5 * (xa + xb) if self.points is None else self.points
        nc = self.n_comp
        L = np.zeros((ne, nc, 2 * nc))
        for e in range(ne):
            R = self.frames[e]
            if nc == 6:
                Bm = np.zeros((6, 12))
                Bm[:3, 0:3] = -np.eye(3)
                Bm[:3, 3:6] = skew(p[e] - xa[e])  # -(-[p - xa]x)
                Bm[3:, 3:6] = -np.eye(3)
                Bm[:3, 6:9] = np.eye(3)
                Bm[:3, 9:12] = -skew(p[e] - xb[e])
                Bm[3:, 9:12] = np.eye(3)
                R6 = np.zeros((6, 6))
                R6[:3, :3] = R
                R6[3:, 3:] = R
                L[e] = R6 @ Bm
            else:
                L[e] = R @ np.hstack([-np.eye(3), np.eye(3)])
        return L

    def stiffness(self, nodes: np.ndarray) -> np.ndarray:
        L = self.relative_motion(nodes)
        k = self.k[:, : self.n_comp]
        return np.einsum("eai,ea,eaj->eij", L, k, L)

    def forces(self, nodes: np.ndarray, ue: np.ndarray) -> np.ndarray:
        """Spring forces and moments in local axes (E, 6) (or (E, 3)): diag(k) R6 B ue, k times the local
        relative motion at p. (Node b's nodal moment is not used: when the nodes are apart it also holds
        (p - x_b) x F.)"""
        L = self.relative_motion(nodes)
        return self.k[:, : self.n_comp] * np.einsum("eij,ej->ei", L, ue)


# ------------------------------------------------------------------ shell to solid


def shell_node_normals(conn: np.ndarray, nodes: np.ndarray) -> np.ndarray:
    """Unit normal at every node (N, 3): the mean of the normals of the shell elements around it
    (zero for nodes without shells). conn (E, 4) quads or (E, 3) triangles."""
    X = nodes[conn]
    n = np.cross(X[:, 2] - X[:, 0], X[:, 3] - X[:, 1]) if conn.shape[1] == 4 else np.cross(X[:, 1] - X[:, 0], X[:, 2] - X[:, 0])
    n /= np.linalg.norm(n, axis=1)[:, None]
    acc = np.zeros((len(nodes), 3))
    for k in range(conn.shape[1]):
        np.add.at(acc, conn[:, k], n)
    nrm = np.linalg.norm(acc, axis=1)
    return np.where(nrm[:, None] > 0, acc / np.where(nrm > 0, nrm, 1.0)[:, None], 0.0)


def shell_to_solid(shell_nodes, normals: np.ndarray, nodes: np.ndarray, solid_nodes, thickness,
                   tol: float = 1e-6, name: str = "shell-solid") -> Constraint:
    """Couple shell nodes on a solid face to the solid nodes through the shell's thickness.

    For a shell node s with unit normal n, the solid nodes j on the line x_s + z n with |z| <= t/2 (+ tol)
    are found. The shell node is made dependent on them by the least-squares fit of a straight, rigid
    normal line to their displacements, u_j ~ u_s + theta x (z_j n):
        v = P sum_j (z_j - zbar) u_j / sum_j (z_j - zbar)^2   (P = I - n n^T),
        u_s = mean_j u_j - zbar v,   theta_perp = n x v,
    i.e. the shell's rotation is the difference of the solid's displacements across the thickness. The
    rotation about n (drilling) is left to the shell. The solid nodes are not tied to each other, so the
    face is not stiffened (a distributing coupling, as the shell-to-solid couplings of general FE codes are
    commonly described; derived here, no program's documentation checked). Needs at least two solid nodes
    at different z on each line.
    """
    shell_nodes = [int(s) for s in np.atleast_1d(shell_nodes)]
    solid_nodes = np.asarray(solid_nodes, int)
    ts = np.broadcast_to(np.asarray(thickness, float), (len(shell_nodes),))
    eqs = []
    touched = set(shell_nodes)
    size = float(np.ptp(nodes[solid_nodes], axis=0).max()) or 1.0
    for s, t in zip(shell_nodes, ts):
        n = normals[s] / np.linalg.norm(normals[s])
        d = nodes[solid_nodes] - nodes[s]
        z = d @ n
        off = np.linalg.norm(d - z[:, None] * n[None, :], axis=1)
        sel = np.nonzero((off <= tol * size) & (np.abs(z) <= t / 2 + tol * size))[0]
        if len(sel) < 2 or np.ptp(z[sel]) <= tol * size:
            raise ValueError(f"shell node {s}: fewer than two solid nodes across its thickness on the solid face")
        js = solid_nodes[sel]
        zj = z[sel]
        zbar = zj.mean()
        Szz = np.sum((zj - zbar) ** 2)
        P = np.eye(3) - np.outer(n, n)
        # v = sum_j a_j P u_j with a_j = (z_j - zbar) / Szz;  u_s = sum_j (1/m) u_j - zbar v
        a = (zj - zbar) / Szz
        m = len(js)
        for k in range(3):
            terms = [(s, k, 1.0)]
            for j, aj in zip(js, a):
                for l in range(3):
                    c = (1.0 / m if l == k else 0.0) - zbar * aj * P[k, l]
                    if c != 0.0:
                        terms.append((int(j), l, -c))
            eqs.append(Equation(terms, prefer=(s, k), source=name))
        # theta . e = (n x v) . e = v . (e x n) for the two directions e across n
        e1 = np.cross(n, [1.0, 0.0, 0.0])
        if np.linalg.norm(e1) < 0.5:
            e1 = np.cross(n, [0.0, 1.0, 0.0])
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(n, e1)
        for e in (e1, e2):
            w = P @ np.cross(e, n)  # v . (e x n) = sum_j a_j (P (e x n)) . u_j
            terms = [(s, 3 + k, e[k]) for k in range(3) if e[k] != 0.0]
            for j, aj in zip(js, a):
                terms += [(int(j), l, -aj * w[l]) for l in range(3) if w[l] != 0.0]
            big = int(np.argmax(np.abs(e)))
            eqs.append(Equation(terms, prefer=(s, 3 + big), source=name))
        touched.update(int(j) for j in js)
    return Constraint(name, eqs, needs_rotation=tuple(shell_nodes), nodes=tuple(sorted(touched)))


# ------------------------------------------------------------------ tie (non-matching meshes)


def project_to_faces(points: np.ndarray, faces: np.ndarray, nodes: np.ndarray, n_candidates: int = 8,
                     tol: float = 1e-6) -> list:
    """Closest point of each point on a set of faces (F, 4) or (F, 8) (quad4 / quad8 shape functions).

    For each point the nearest faces (by centroid) are tried; on each, (xi, eta) are found by Gauss-Newton on
    |x(xi, eta) - p|^2. A face whose solution lies inside (|xi|, |eta| <= 1 + tol) and is closest wins; if
    none is inside, the closest clamped point. Returns [(face index, xi, eta, distance, inside)]."""
    from scipy.spatial import cKDTree

    from .solid import shape_quad4, shape_quad8

    faces = np.asarray(faces, int)
    shp = shape_quad8 if faces.shape[1] == 8 else shape_quad4
    X = nodes[faces]
    tree = cKDTree(X[:, :4].mean(axis=1))
    k = min(n_candidates, len(faces))
    out = []
    for p in np.atleast_2d(points):
        _, cand = tree.query(p, k=k)
        best = None
        for f in np.atleast_1d(cand):
            xi = np.zeros(2)
            for _ in range(25):
                N, dN = shp(*xi)
                x = N @ X[f]
                J = dN @ X[f]  # (2, 3)
                r = x - p
                step = np.linalg.solve(J @ J.T, -(J @ r))
                xi = np.clip(xi + step, -1.5, 1.5)
                if np.abs(step).max() < 1e-12:
                    break
            inside = bool(np.all(np.abs(xi) <= 1 + tol))
            xc = np.clip(xi, -1.0, 1.0)
            N, _ = shp(*xc)
            dist = float(np.linalg.norm(N @ X[f] - p))
            key = (not inside, dist)
            if best is None or key < best[0]:
                best = (key, int(f), float(xc[0]), float(xc[1]), dist, inside)
        out.append(best[1:])
    return out


def tie_weights(slave_nodes, master_faces: np.ndarray, nodes: np.ndarray, max_gap: float | None = None) -> list:
    """Interpolation weights of a tie: each slave node projected on a surface of another mesh (shell faces
    (F, 4) or solid faces (F, 8) / (F, 4)) by project_to_faces.

    Returns one dict per slave node, in the order given:
        slave    the slave node
        face     index of the face in master_faces
        masters  the face's nodes (list of int)
        weights  shape functions N_i(xi, eta) at the projected point, one per master node (they sum to 1)
        xi, eta  the projected point's coordinates on the face (clamped to [-1, 1])
        offset   x_s - x_p, the slave's position minus the projected point x_p = sum_i N_i x_i (list of 3)
        gap      |offset|
        inside   whether the projection fell inside the face
    The tie built from them (as tie does) is
        u_s = sum_i N_i u_i  (+ theta_p x offset when the master nodes have rotations, theta_p = sum_i N_i theta_i,
                              which keeps a rigid motion exact across a small gap such as a faceted wall),
        theta_s = sum_i N_i theta_i   (when both sides have rotations).
    max_gap: refuse slave nodes farther than this from the surface (default: no limit) with ValueError.
    """
    from .solid import shape_quad4, shape_quad8

    slaves = [int(s) for s in np.atleast_1d(slave_nodes)]
    faces = np.asarray(master_faces, int)
    proj = project_to_faces(nodes[slaves], faces, nodes)
    shp = shape_quad8 if faces.shape[1] == 8 else shape_quad4
    out = []
    for s, (f, xi, eta, dist, inside) in zip(slaves, proj):
        if max_gap is not None and dist > max_gap:
            raise ValueError(f"tie: slave node {s} is {dist:.3g} from the surface (more than {max_gap:g})")
        N, _ = shp(xi, eta)
        ids = faces[f]
        xp = N @ nodes[ids]
        out.append({"slave": s, "face": int(f), "masters": [int(i) for i in ids], "weights": [float(n) for n in N],
                    "xi": xi, "eta": eta, "offset": [float(v) for v in nodes[s] - xp], "gap": dist,
                    "inside": inside})
    return out


def tie(slave_nodes, master_faces: np.ndarray, nodes: np.ndarray, name: str = "tie", max_gap: float | None = None,
        rotations: bool = True) -> Constraint:
    """Tie slave nodes to a surface of another mesh whose nodes do not match (shell faces (F, 4) or solid faces
    (F, 8) / (F, 4)).

    Each slave node is projected on the surface (tie_weights): face, (xi, eta), the point x_p. Then
        u_s = sum_i N_i(xi, eta) u_i  (+ theta_p x (x_s - x_p) when the master nodes have rotations,
                                          theta_p = sum_i N_i theta_i, which keeps a rigid motion exact
                                          across a small gap such as a faceted wall),
        theta_s = sum_i N_i theta_i   (when both sides have rotations and rotations=True).
    A shell edge tied to solid faces gets no rotation tie (solid nodes have none): use shell_to_solid there.
    max_gap: refuse slave nodes farther than this from the surface (default: no limit).
    """
    tw = tie_weights(slave_nodes, master_faces, nodes, max_gap=max_gap)
    slaves = [w["slave"] for w in tw]
    data = [(w["slave"], np.asarray(w["masters"], int), np.asarray(w["weights"], float),
             np.asarray(w["offset"], float)) for w in tw]
    masters = sorted({int(i) for _, ids, *_ in data for i in ids})

    def make(counts):
        eqs = []
        for s, ids, N, d in data:
            m_rot = all(counts[i] == 6 for i in ids)
            Sd = skew(d)
            for k in range(3):
                terms = [(s, k, 1.0)] + [(int(i), k, -float(n)) for i, n in zip(ids, N) if n != 0.0]
                if m_rot and np.any(d != 0.0):
                    terms += [(int(i), 3 + l, float(n) * Sd[k, l]) for i, n in zip(ids, N) for l in range(3)
                              if n != 0.0 and Sd[k, l] != 0.0]
                eqs.append(Equation(terms, prefer=(s, k), source=name))
            if rotations and m_rot and counts[s] == 6:
                for k in range(3):
                    terms = [(s, 3 + k, 1.0)] + [(int(i), 3 + k, -float(n)) for i, n in zip(ids, N) if n != 0.0]
                    eqs.append(Equation(terms, prefer=(s, 3 + k), source=name))
        return eqs

    c = Constraint(name, nodes=tuple(slaves + masters), maker=make)
    c.projection = [{k: w[k] for k in ("slave", "face", "xi", "eta", "gap", "inside")} for w in tw]
    return c


# ------------------------------------------------------------------ RBE3


def is_collinear(xyz: np.ndarray, weights=None, tol: float = 1e-9) -> bool:
    """True when the points (with positive weights) lie on one line or coincide, so their translations do not
    fix a rotation about that line."""
    x = np.asarray(xyz, float)
    w = np.ones(len(x)) if weights is None else np.asarray(weights, float)
    c = (w[:, None] * x).sum(axis=0) / w.sum()
    rho = x - c
    A = np.einsum("i,ij,ik->jk", w, rho, rho)
    A = np.trace(A) * np.eye(3) - A
    return bool(np.linalg.matrix_rank(A, tol=tol * max(np.trace(A), 1e-300)) < 3)


def rbe3_full_matrices(ref_xyz, xyz: np.ndarray, weights=None, rotation_weights=None):
    """Coefficients of the distributing link with the connected nodes' rotations:
        u_ref = sum_i (H_i u_i + Hr_i theta_i),  theta_ref = sum_i (G_i u_i + Gr_i theta_i)   (3 x 3 each).

    The reference motion is the weighted least-squares rigid motion (u_c, theta) of the connected nodes,
    now with their rotations as well:
        minimise sum_i w_i |u_i - u_c - theta x rho_i|^2 + sum_i v_i |theta_i - theta|^2,
    rho_i = x_i - c about the weighted centroid c of the translations. Setting the derivatives to zero
    (derived here): u_c = sum w_i u_i / sum w_i, and
        A theta = sum_i w_i rho_i x u_i + sum_i v_i theta_i,   A = sum_i w_i (|rho_i|^2 I - rho_i rho_i^T) + (sum v_i) I,
    then u_ref = u_c + theta x (x_ref - c). The rotation weights v_i (N mm^2 per N, i.e. length^2 times the
    translation weight's unit) make A invertible when the nodes lie on one line: the twist about the line
    then comes from the nodes' own rotations. A rigid motion of the connected nodes (u_i = a + omega x x_i,
    theta_i = omega) is fitted exactly, so the transpose spreads a force and moment at the reference with
    the same resultant, and the link adds no stiffness (the reference is the dependent node). Nastran's
    RBE3 also lets the connected nodes' rotational components carry weights (as commonly described; the
    program's documentation not checked here).

    rotation_weights: per node or one value; None = 0 (translations only, as rbe3_matrices)."""
    x = np.asarray(xyz, float)
    w = np.ones(len(x)) if weights is None else np.asarray(weights, float)
    v = np.zeros(len(x)) if rotation_weights is None else np.broadcast_to(np.asarray(rotation_weights, float),
                                                                         (len(x),)).copy()
    W = w.sum()
    c = (w[:, None] * x).sum(axis=0) / W
    rho = x - c
    A = np.einsum("i,ij,ik->jk", w, rho, rho)
    A = np.trace(A) * np.eye(3) - A + v.sum() * np.eye(3)
    scale = max(np.trace(A), 1e-300)
    if np.linalg.matrix_rank(A, tol=1e-9 * scale) < 3:
        raise ValueError("a distributing link needs at least three connected nodes not on one line, or rotation "
                         "weights on the connected nodes")
    Ainv = np.linalg.inv(A)
    G = np.array([wi * Ainv @ skew(r) for wi, r in zip(w, rho)])  # theta = sum G_i u_i + Gr_i theta_i
    Gr = np.array([vi * Ainv for vi in v])
    d = np.asarray(ref_xyz, float) - c
    Sd = skew(d)
    H = np.array([(wi / W) * np.eye(3) - Sd @ Gi for wi, Gi in zip(w, G)])  # theta x d = -[d]x theta
    Hr = np.array([-Sd @ Gi for Gi in Gr])
    return H, Hr, G, Gr


def default_rotation_weights(xyz: np.ndarray, weights=None, factor: float = 1e-3) -> np.ndarray:
    """Rotation weights for a line of nodes: factor * w_i * l^2, l the RMS distance of the nodes from their
    centroid (or 1 mm when they coincide). The perpendicular rotations then come almost wholly from the
    translations (factor small); the twist about the line comes only from the nodes' rotations, shared in
    proportion to the weights whatever the factor."""
    x = np.asarray(xyz, float)
    w = np.ones(len(x)) if weights is None else np.asarray(weights, float)
    c = (w[:, None] * x).sum(axis=0) / w.sum()
    l2 = float(np.sum(w * np.sum((x - c) ** 2, axis=1)) / w.sum()) or 1.0
    return factor * w * l2


def rbe3_matrices(ref_xyz, xyz: np.ndarray, weights=None):
    """Coefficients of the distributing link: u_ref = sum_i H_i u_i, theta_ref = sum_i G_i u_i (3 x 3 each).

    The reference motion is the weighted least-squares rigid motion of the connected nodes' translations:
    minimise sum_i w_i |u_i - u_c - theta x rho_i|^2 with rho_i = x_i - c about the weighted centroid c,
    giving u_c = sum w_i u_i / sum w_i and theta = A^-1 sum_i w_i rho_i x u_i, A = sum_i w_i (|rho_i|^2 I -
    rho_i rho_i^T); then u_ref = u_c + theta x (x_ref - c). Its transpose spreads a force F and moment M at
    the reference as f_i = H_i^T F + G_i^T M, which has the same resultant and adds no stiffness (the
    interpolation constraint of Nastran's RBE3 as commonly described; derived here, the program's own
    documentation not checked). Needs three nodes not on one line for the rotations."""
    x = np.asarray(xyz, float)
    w = np.ones(len(x)) if weights is None else np.asarray(weights, float)
    W = w.sum()
    c = (w[:, None] * x).sum(axis=0) / W
    rho = x - c
    A = np.einsum("i,ij,ik->jk", w, rho, rho)
    A = np.trace(A) * np.eye(3) - A
    if np.linalg.matrix_rank(A, tol=1e-9 * max(np.trace(A), 1e-300)) < 3:
        raise ValueError("a distributing link needs at least three connected nodes not on one line")
    Ainv = np.linalg.inv(A)
    G = np.array([wi * Ainv @ skew(r) for wi, r in zip(w, rho)])  # theta = sum G_i u_i
    d = np.asarray(ref_xyz, float) - c
    H = np.array([(wi / W) * np.eye(3) - skew(d) @ Gi for wi, Gi in zip(w, G)])  # theta x d = -[d]x theta
    return H, G


def _rotation_weights(xyz, weights, rotation_weights):
    """None: zero when the translations fix the rotations, default_rotation_weights when the nodes are on one
    line; otherwise the values given."""
    if rotation_weights is None:
        return default_rotation_weights(xyz, weights) if is_collinear(xyz, weights) else None
    return rotation_weights


def distributing_link(ref: int, nodes_ids, nodes: np.ndarray, weights=None, name: str = "rbe3",
                      rotation_weights=None) -> Constraint:
    """Distributing link (RBE3 type): the reference node (6 DOFs, dependent) moves with the weighted average
    rigid motion of the connected nodes (rbe3_full_matrices). A load on the reference reaches the connected
    nodes without making them stiffer.

    rotation_weights: None = translations only when the connected nodes span a plane or a volume, and the
    nodes' rotations with default_rotation_weights when they lie on one line (then they must carry
    rotations: shell or beam nodes); or values per node / one value (0 = translations only)."""
    ref = int(ref)
    ids = [int(i) for i in np.atleast_1d(nodes_ids)]
    v = _rotation_weights(nodes[ids], weights, rotation_weights)
    H, Hr, G, Gr = rbe3_full_matrices(nodes[ref], nodes[ids], weights, v)
    use_rot = [bool(np.any(Hr[n] != 0.0) or np.any(Gr[n] != 0.0)) for n in range(len(ids))]

    def make(counts):
        bad = [i for i, u in zip(ids, use_rot) if u and counts[i] < 6]
        if bad:
            raise ValueError(f"{name}: connected node(s) {bad[:5]} have no rotations, which a link to nodes on one "
                             f"line needs (connect shell or beam nodes, or give three nodes off one line)")
        eqs = []
        for k in range(3):
            terms = [(ref, k, 1.0)]
            terms += [(i, l, -H[n, k, l]) for n, i in enumerate(ids) for l in range(3) if H[n, k, l] != 0.0]
            terms += [(i, 3 + l, -Hr[n, k, l]) for n, i in enumerate(ids) if use_rot[n] for l in range(3)
                      if Hr[n, k, l] != 0.0]
            eqs.append(Equation(terms, prefer=(ref, k), source=name))
        for k in range(3):
            terms = [(ref, 3 + k, 1.0)]
            terms += [(i, l, -G[n, k, l]) for n, i in enumerate(ids) for l in range(3) if G[n, k, l] != 0.0]
            terms += [(i, 3 + l, -Gr[n, k, l]) for n, i in enumerate(ids) if use_rot[n] for l in range(3)
                      if Gr[n, k, l] != 0.0]
            eqs.append(Equation(terms, prefer=(ref, 3 + k), source=name))
        return eqs

    c = Constraint(name, needs_rotation=(ref,), nodes=tuple([ref] + ids), maker=make)
    c.rotation_weights = None if v is None else np.broadcast_to(np.asarray(v, float), (len(ids),)).copy()
    return c


def rbe3_forces(ref_xyz, xyz: np.ndarray, force, moment=(0.0, 0.0, 0.0), weights=None) -> np.ndarray:
    """Nodal forces (n, 3) that a distributing link gives the connected nodes for a force and moment at the
    reference: f_i = H_i^T F + G_i^T M."""
    H, G = rbe3_matrices(ref_xyz, xyz, weights)
    return np.einsum("nkl,k->nl", H, np.asarray(force, float)) + np.einsum("nkl,k->nl", G, np.asarray(moment, float))


def rbe3_loads(ref_xyz, xyz: np.ndarray, force, moment=(0.0, 0.0, 0.0), weights=None, rotation_weights=None
               ) -> np.ndarray:
    """Nodal forces and moments (n, 6) that a distributing link (with the same rotation_weights rule as
    distributing_link) gives the connected nodes: f_i = H_i^T F + G_i^T M, m_i = Hr_i^T F + Gr_i^T M."""
    v = _rotation_weights(xyz, weights, rotation_weights)
    H, Hr, G, Gr = rbe3_full_matrices(ref_xyz, xyz, weights, v)
    F, M = np.asarray(force, float), np.asarray(moment, float)
    f = np.einsum("nkl,k->nl", H, F) + np.einsum("nkl,k->nl", G, M)
    m = np.einsum("nkl,k->nl", Hr, F) + np.einsum("nkl,k->nl", Gr, M)
    return np.concatenate([f, m], axis=1)

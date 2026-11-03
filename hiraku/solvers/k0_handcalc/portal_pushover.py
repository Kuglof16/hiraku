"""A roll bar on its feet as a small 3D frame, pushed with plastic hinges forming one by one (event to event).

Used by the K0 formulas frame_static.portal_frame_side, _rear and _top (library_portal). The model is the roll bar
alone, with what holds its feet written out as members:

    rail (held) --L_a-- root --L_b-- rail (held)        the rail along x, fixed at the far ends of both pieces
                         |
                    outrigger (length e, along y)       none when e = 0 (the post stands on the rail)
                         |
                       foot
                         |  post (length h, along z)
                       corner ------ top beam (span w, along y) ------ corner (the right side mirrored)

Every member is a slender 3D beam (axial E A / L, torsion G J / L, bending E I_y and E I_z, no shear deformation:
the 12 x 12 element of textbook matrix structural analysis, local axes as K1 and frame_inputs.member_axes). The
top beam has nodes at the rear load point (a fraction f of the span from the left corner) and at mid-span.

Plastic hinges (the event-to-event, or step-by-step elastic-plastic, method of plastic analysis): the load grows
in steps; in each step the frame with the hinges so far is solved for a unit load, and the step ends when the next
member end reaches its plastic capacity, about one local bending axis (M_p = Z_p f_y about that axis) or in torsion
(T_p, the member's twist is then free). A hinge holds its moment and turns freely after; the capacities are
independent (no interaction of bending about both axes, torsion and axial force), the hinges do not unload, no
strain hardening, small displacements. The frame is a mechanism when its stiffness matrix with the hinges is
singular and the load does work on the null shape; the load then stays at the collapse load while the frame moves
along that shape.

Source: the slope-deflection / matrix stiffness method and step-by-step plastic analysis of frames, textbook
structural analysis (e.g. W. McGuire, R. H. Gallagher, R. D. Ziemian, Matrix Structural Analysis; B. G. Neal, The
Plastic Methods of Structural Analysis; J. Heyman, Plastic Design of Frames). Written out here; no table values.
"""

from __future__ import annotations

import math

import numpy as np

P0 = 1000.0  # N, the unit load of a step
_NULL = 1e-12  # eigenvalues of the diagonally scaled stiffness matrix (unit diagonal) below this are zero
_SAME = 1e-9  # hinges reached within this relative step of the first one form together
MAX_STEPS = 200

# member groups and the keys of their properties in the formulas' inputs
GROUPS = {"c": "post", "b": "top beam", "o": "outrigger", "r": "rail"}
PROPS = ("A", "Iy", "Iz", "J", "Mpy", "Mpz", "Tp")


def member_axes(e: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Local y and z as K1 orients them (frame_inputs.member_axes): a vertical member's local y along x; otherwise
    local z horizontal (e x z_up), local y = z x e (up for a horizontal member)."""
    if abs(e[0]) < 1e-9 and abs(e[1]) < 1e-9:
        y = np.array([1.0, 0.0, 0.0])
        return y, np.cross(e, y)
    z = np.cross(e, [0.0, 0.0, 1.0])
    z /= np.linalg.norm(z)
    return np.cross(z, e), z


def local_stiffness(L: float, E: float, G: float, p: dict) -> np.ndarray:
    """12 x 12 local stiffness: DOFs u, v, w, rx, ry, rz at each end; bending E Iz in the local x-y plane (v, rz),
    E Iy in the local x-z plane (w, ry)."""
    k = np.zeros((12, 12))
    a, t = E * p["A"] / L, G * p["J"] / L
    for i, j, v in ((0, 0, a), (6, 6, a), (0, 6, -a), (6, 0, -a), (3, 3, t), (9, 9, t), (3, 9, -t), (9, 3, -t)):
        k[i, j] += v
    for dofs, I, s in (((1, 5, 7, 11), p["Iz"], 1.0), ((2, 4, 8, 10), p["Iy"], -1.0)):
        c = E * I / L**3
        m = c * np.array([[12, 6 * L * s, -12, 6 * L * s], [6 * L * s, 4 * L * L, -6 * L * s, 2 * L * L],
                          [-12, -6 * L * s, 12, -6 * L * s], [6 * L * s, 2 * L * L, -6 * L * s, 4 * L * L]])
        k[np.ix_(dofs, dofs)] += m
    return k


# a hinge: (member index, end 0 or 1, axis "x" torsion, "y" or "z" bending); its local DOF
_LOCAL = {("y", 0): 4, ("z", 0): 5, ("y", 1): 10, ("z", 1): 11, ("x", 0): 3, ("x", 1): 9}


class Frame:
    """Nodes {name: xyz}, members [(name, node_a, node_b, props)], fixed nodes; solve() with hinges."""

    def __init__(self, E: float, G: float):
        self.E, self.G = E, G
        self.nodes: dict[str, np.ndarray] = {}
        self.members: list[tuple[str, str, str, dict]] = []
        self.fixed: set[str] = set()

    def node(self, name: str, xyz) -> str:
        self.nodes[name] = np.asarray(xyz, float)
        return name

    def member(self, name: str, a: str, b: str, props: dict) -> None:
        if np.linalg.norm(self.nodes[b] - self.nodes[a]) > 1e-9:
            self.members.append((name, a, b, props))

    def _geometry(self, a: str, b: str):
        d = self.nodes[b] - self.nodes[a]
        L = float(np.linalg.norm(d))
        e = d / L
        y, z = member_axes(e)
        return L, np.kron(np.eye(4), np.vstack([e, y, z]))

    def element(self, i: int, hinges: set) -> tuple[np.ndarray, np.ndarray]:
        """Local stiffness of member i with its hinges condensed out, and its transformation."""
        name, a, b, p = self.members[i]
        L, T = self._geometry(a, b)
        p = dict(p)
        if (i, 0, "x") in hinges or (i, 1, "x") in hinges:
            p["J"] = 0.0
        k = local_stiffness(L, self.E, self.G, p)
        rel = [_LOCAL[(ax, end)] for (m, end, ax) in hinges if m == i and ax != "x"]
        if rel:
            keep = [j for j in range(12) if j not in rel]
            kbb = k[np.ix_(rel, rel)]
            kc = k[np.ix_(keep, keep)] - k[np.ix_(keep, rel)] @ np.linalg.solve(kbb, k[np.ix_(rel, keep)])
            k = np.zeros((12, 12))
            k[np.ix_(keep, keep)] = kc
        return k, T

    def dof_map(self) -> dict[str, int]:
        free = [n for n in self.nodes if n not in self.fixed]
        return {n: 6 * i for i, n in enumerate(free)}

    def solve(self, hinges: set, load_node: str, direction: np.ndarray) -> dict:
        """Displacements for P0 along direction at load_node; {"u": {node: 6-vector}, "forces": [12-vector local end
        forces per member], "mechanism": bool, "shape": {node: 6-vector} (the null shape, load point 1 along
        the load) when the load does work on a null shape of the frame."""
        dm = self.dof_map()
        n = 6 * len(dm)
        K = np.zeros((n, n))
        els = []
        for i, (_, a, b, _) in enumerate(self.members):
            k, T = self.element(i, hinges)
            kg = T.T @ k @ T
            els.append((k, T))
            idx = [dm[a] + j if a in dm else -1 for j in range(6)] + [dm[b] + j if b in dm else -1 for j in range(6)]
            for r, gr in enumerate(idx):
                if gr < 0:
                    continue
                for c, gc in enumerate(idx):
                    if gc >= 0:
                        K[gr, gc] += kg[r, c]
        f = np.zeros(n)
        f[dm[load_node]:dm[load_node] + 3] = P0 * direction
        diag = np.diag(K).copy()
        trans = np.array([j % 6 < 3 for j in range(n)])
        live = np.zeros(n, bool)
        for sel in (trans, ~trans):
            top = diag[sel].max() if sel.any() else 0.0
            live |= sel & (diag > 1e-12 * top)
        idx = np.flatnonzero(live)
        Ks = K[np.ix_(idx, idx)]
        D = 1.0 / np.sqrt(np.diag(Ks))
        S = D[:, None] * Ks * D[None, :]
        w, V = np.linalg.eigh(S)
        null = V[:, w < _NULL]
        fs = D * f[idx]
        u = np.zeros(n)
        mechanism = False
        shape = None
        if null.shape[1]:
            work = null.T @ fs
            if np.linalg.norm(work) > 1e-6 * np.linalg.norm(fs):
                mechanism = True
                y = null @ work
                s = np.zeros(n)
                s[idx] = D * y
                at = float(s[dm[load_node]:dm[load_node] + 3] @ direction)
                shape = s / at
            keep = w >= _NULL
            Vk, wk = V[:, keep], w[keep]
            y = Vk @ ((Vk.T @ fs) / wk)
        else:
            y = np.linalg.solve(S, fs)
        u[idx] = D * y

        def per_node(vec):
            return {nd: (vec[dm[nd]:dm[nd] + 6] if nd in dm else np.zeros(6)) for nd in self.nodes}

        U = per_node(u)
        forces = []
        for i, (_, a, b, _) in enumerate(self.members):
            k, T = els[i]
            forces.append(k @ (T @ np.concatenate([U[a], U[b]])))
        return {"u": U, "forces": forces, "mechanism": mechanism, "shape": per_node(shape) if shape is not None else None}


def push(frame: Frame, caps: list[dict], load_node: str, direction, watch: list[str]) -> dict:
    """Event-to-event push to the mechanism. caps[i] = {"x": T_p, "y": M_py, "z": M_pz} of member i (0 or less:
    no hinge there). Returns {"path": [(F, d)], "U": [{node: xyz displacement}] per path point, "hinges": [(member
    name, end, axis, F)], "mechanism": bool, "shape": {node: xyz per unit load-point travel} or None}."""
    direction = np.asarray(direction, float)
    hinges: set = set()
    acc = {(i, end, ax): 0.0 for i in range(len(frame.members)) for end in (0, 1) for ax in "xyz"}
    F = d = 0.0
    path, Us, formed = [(0.0, 0.0)], [{n: np.zeros(3) for n in watch}], []
    for _ in range(MAX_STEPS):
        s = frame.solve(hinges, load_node, direction)
        if s["mechanism"]:
            shape = {n: s["shape"][n][:3] for n in watch}
            return {"path": path, "U": Us, "hinges": formed, "mechanism": True, "shape": shape}
        du = float(s["u"][load_node][:3] @ direction)
        if not du > 0.0:
            raise ValueError("the frame does not move along the load under it (no stiffness or a negative one)")
        lam, inc = math.inf, {}
        for (i, end, ax) in acc:
            if (i, end, ax) in hinges or ((i, 1 - end, "x") in hinges and ax == "x"):
                continue
            cap = caps[i].get(ax, 0.0)
            if not cap > 0.0:
                continue
            m = float(s["forces"][i][_LOCAL[(ax, end)]])
            inc[(i, end, ax)] = m
            M = acc[(i, end, ax)]
            if abs(m) < 1e-12 * cap:
                continue
            reach = (cap - abs(M)) / abs(m) if M * m >= 0.0 else (cap + abs(M)) / abs(m)
            lam = min(lam, max(reach, 0.0))
        if not math.isfinite(lam):
            raise ValueError("no hinge can form and the frame is not a mechanism: give the members plastic capacities")
        for key, m in inc.items():
            acc[key] += lam * m
        F += lam * P0
        d += lam * du
        path.append((F, d))
        Us.append({n: Us[-1][n] + lam * s["u"][n][:3] for n in watch})
        for key, m in inc.items():
            i, end, ax = key
            cap = caps[i].get(ax, 0.0)
            if abs(acc[key]) >= cap * (1.0 - _SAME) and key not in hinges:
                hinges.add(key)
                formed.append((frame.members[i][0], end, ax, F))
    raise ValueError(f"no mechanism after {MAX_STEPS} hinges")


def energy_at(path: list[tuple[float, float]]) -> list[float]:
    E = [0.0]
    for (f0, d0), (f1, d1) in zip(path, path[1:]):
        E.append(E[-1] + 0.5 * (f0 + f1) * (d1 - d0))
    return E


def follow(res: dict, kind: str, target: float) -> dict:
    """Where the pushed frame is when it has absorbed the energy target (kind "energy", N*mm) or carries the force
    target (kind "force", N): {"d", "F", "U": {node: xyz}, "beyond": bool (on the collapse plateau / force above
    the collapse load), "stage": index of the path segment}."""
    path, Us = res["path"], res["U"]
    En = energy_at(path)
    F_c, d_c = path[-1]
    for k in range(1, len(path)):
        (f0, d0), (f1, d1) = path[k - 1], path[k]
        if kind == "energy" and En[k] >= target:
            kseg = (f1 - f0) / (d1 - d0)
            a, b, c = 0.5 * kseg, f0, En[k - 1] - target
            x = (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a) if a > 0 else -c / b
            t = x / (d1 - d0)
        elif kind == "force" and f1 >= target:
            t = (target - f0) / (f1 - f0)
        else:
            continue
        U = {n: Us[k - 1][n] + t * (Us[k][n] - Us[k - 1][n]) for n in Us[k]}
        return {"d": d0 + t * (d1 - d0), "F": f0 + t * (f1 - f0), "U": U, "beyond": False, "stage": k}
    if kind == "force":
        return {"d": d_c, "F": F_c, "U": dict(Us[-1]), "beyond": True, "stage": len(path) - 1}
    extra = (target - En[-1]) / F_c
    shape = res["shape"] or {n: np.zeros(3) for n in Us[-1]}
    U = {n: Us[-1][n] + extra * shape[n] for n in Us[-1]}
    return {"d": d_c + extra, "F": F_c, "U": U, "beyond": True, "stage": len(path) - 1}


def build(v: dict, f: float = 1.0 / 6.0) -> tuple[Frame, list[dict], dict]:
    """The roll bar frame from the formulas' inputs (library_portal: E, nu, h, w, e, L_a, L_b and the members'
    A_g, Iy_g, Iz_g, J_g, Mpy_g, Mpz_g, Tp_g for g in c (posts), b (top beam), o (outriggers), r (rail)).
    Returns the frame, the members' capacities and the named nodes {"corner_L", "corner_R", "q", "mid", ...}."""
    E = v["E"]
    G = E / (2.0 * (1.0 + v["nu"]))
    fr = Frame(E, G)
    h, w, e = v["h"], v["w"], v["e"]
    props = {g: {k: v[f"{k}_{g}"] for k in PROPS} for g in GROUPS}
    caps: list[dict] = []
    names = {}

    def add(name, a, b, g):
        before = len(fr.members)
        fr.member(name, a, b, props[g])
        if len(fr.members) > before:
            p = props[g]
            caps.append({"x": p["Tp"], "y": p["Mpy"], "z": p["Mpz"]})

    for side, s in (("L", 1.0), ("R", -1.0)):
        foot = fr.node(f"foot_{side}", [0.0, s * w / 2.0, 0.0])
        corner = fr.node(f"corner_{side}", [0.0, s * w / 2.0, h])
        names[f"corner_{side}"] = corner
        names[f"foot_{side}"] = foot
        root = foot
        if abs(e) > 1e-9:
            root = fr.node(f"root_{side}", [0.0, s * (w / 2.0 - e), 0.0])
            add(f"outrigger_{side}", root, foot, "o")
        names[f"root_{side}"] = root
        held = 0
        for tag, x in (("a", v["L_a"]), ("b", -v["L_b"])):
            if abs(x) > 1e-9:
                end = fr.node(f"rail_{tag}_{side}", [x, s * (w / 2.0 - e), 0.0])
                fr.fixed.add(end)
                add(f"rail_{tag}_{side}", end, root, "r")
                held += 1
        if not held:
            fr.fixed.add(root)
        add(f"post_{side}", foot, corner, "c")
    yq = w / 2.0 - f * w
    q = fr.node("q", [0.0, yq, h]) if f > 1e-9 else names["corner_L"]
    mid = fr.node("mid", [0.0, 0.0, h]) if abs(yq) > 1e-9 else q
    names.update(q=q, mid=mid)
    chain = [names["corner_L"], q] + ([mid] if mid != q else []) + [names["corner_R"]]
    for k, (a, b) in enumerate(zip(chain, chain[1:])):
        add(f"top_{k}", a, b, "b")
    return fr, caps, names


__all__ = ["Frame", "GROUPS", "P0", "PROPS", "build", "energy_at", "follow", "push"]

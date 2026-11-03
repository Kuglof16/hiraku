"""K1 shell static analysis of a design's frame for one load situation.

The loads are those of the beam model (k1_frame.analyze), put on the shell
mesh of the tube walls:

- ground forces: given (an upstream analysis), or from K0 rigid-body
  statics (vehicle.support_loads: a step, or at rest). At rest the wheel
  loads are K0's rigid-body values; the FE model does not stand on the
  suspension springs as K1 does.
- each ground force acts at its contact point; with the moment of its
  offset it is shared over the nodes of the support's frame-node joint
  (mesh.joints[frame_node]: the member end rings and the wall patch they
  sit on) by solve.spread_load (RBE3 type). Unsprung parts (carrier
  "support") load the same joint.
- parts carried by frame nodes: the force at the part's centre of gravity
  and the moment of its offset from the centroid of its carried_by nodes,
  shared equally over those nodes (as K1); each node's share is spread over
  that node's joint nodes.
- tube weight: the shell's lumped element masses (rho t area) times the
  body acceleration; the beam bookkeeping (density A L) is not used.
- inertia relief: what the forces leave unbalanced gives a rigid-body
  linear and angular acceleration of all the masses (shell nodes as point
  masses, parts with their own inertia); -m (a + alpha x (r - cg)) is put
  on every mass, so the loads are self-balanced. Six restraints (3-2-1 on
  single nodes of three support joints) only stop rigid-body motion; their
  reactions over the weight are reported as residual_ratio.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np

from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import DesignError, mass_summary, node_table, resultant, support_loads
from ..k1_frame.analyze import BALANCE_TOL, _split, rigid_fit
from .mesh import ELEMENT_SIZE_MM, ShellMesh, build_mesh
from .solve import ShellModel, StaticSolver, dofs_of, spread_load
from .stress import ShellStress, element_stresses

WALLS = {(0, 0, 1): "upper", (0, 0, -1): "lower", (0, 1, 0): "left (+y)", (0, -1, 0): "right (-y)",
         (1, 0, 0): "front (+x)", (-1, 0, 0): "rear (-x)"}


@dataclass
class ShellFrame:
    """The shell model of a design's frame, held against rigid-body motion, factorised once.

    masses_kg (N,): lumped translational mass of each mesh node. held: the
    support ids whose joints carry the 3-2-1 restraints, hold_nodes the mesh
    node of each (nearest the design node).
    """

    design: dict
    mesh: ShellMesh
    model: ShellModel
    masses_kg: np.ndarray
    held: list[str]
    hold_nodes: list[int]
    fixed: np.ndarray
    support_node: dict[str, str]
    node_xyz: dict[str, np.ndarray]
    timings: dict = field(default_factory=dict)
    _solver: StaticSolver | None = None

    @classmethod
    def build(cls, design: dict, element_size_mm: float = ELEMENT_SIZE_MM) -> "ShellFrame":
        t0 = time.perf_counter()
        nodes = node_table(design)
        sup = design.get("supports", [])
        support_node = {}
        mesh = build_mesh(design, element_size_mm)
        for s in sup:
            fn = s.get("frame_node")
            if not fn:
                raise DesignError(f"support {s['id']!r} has no frame_node, so its load cannot reach the frame; "
                                  f"name the frame node that carries it")
            if fn not in mesh.joints:
                raise DesignError(f"support {s['id']!r} names frame_node {fn!r}, which no member uses; attach it to the frame")
            support_node[s["id"]] = fn
        model = ShellModel.from_mesh(mesh, design)
        masses = model.mass_diagonal()[0::6] * 1000.0  # tonne -> kg
        held = _hold_supports(sup)
        hold_nodes = []
        for sid in held:
            j = mesh.joints[support_node[sid]]
            x0 = nodes[support_node[sid]]
            hold_nodes.append(int(j[np.argmin(np.linalg.norm(mesh.nodes[j] - x0, axis=1))]))
        a, b, c = hold_nodes
        fixed = np.concatenate([dofs_of(a, "xyz"), dofs_of(b, "yz"), dofs_of(c, "z")])
        out = cls(design, mesh, model, masses, held, hold_nodes, fixed, support_node, nodes)
        out.timings["mesh_s"] = time.perf_counter() - t0
        return out

    def solver(self) -> StaticSolver:
        if self._solver is None:
            try:
                self._solver = StaticSolver(self.model, self.fixed)
            except ValueError as e:
                raise DesignError(str(e)) from None
        return self._solver


def _hold_supports(sup: list[dict]) -> list[str]:
    """Three supports for the 3-2-1 hold, chosen as k1_frame.model.restrain_free_body does."""
    if len(sup) < 3:
        raise DesignError(f"need at least 3 supports to hold the frame, the design has {len(sup)}")
    pts = {s["id"]: np.asarray(s["position_mm"], float) for s in sup}
    a = sup[0]["id"]
    b = max(pts, key=lambda k: abs(pts[k][0] - pts[a][0]))
    rest = [k for k in pts if k not in (a, b)]
    if abs(pts[b][0] - pts[a][0]) < 1e-6 or not rest:
        raise DesignError("supports must be spread in x and y to hold the frame")
    ab = pts[b] - pts[a]
    c = max(rest, key=lambda k: np.linalg.norm(np.cross(ab, pts[k] - pts[a])))
    if np.linalg.norm(np.cross(ab, pts[c] - pts[a])) < 1e-6:
        raise DesignError("the supports are in a line, so the frame cannot be held; spread them in x and y")
    return [a, b, c]


def _ground(design: dict, summ: dict, g_factor: float, step_mm, step_supports, ground_forces):
    """{support: {"point_mm", "force_N", "vectors"}}, the K0 statics (or None) and where they came from."""
    if ground_forces is not None:
        missing = [s["id"] for s in design["supports"] if s["id"] not in ground_forces]
        if missing:
            raise DesignError(f"the given ground forces have no entry for support(s) {', '.join(missing)}")
        ground = {sid: {"point_mm": np.asarray(vs[0][1], float), "force_N": sum(np.asarray(v[2], float) for v in vs),
                        "vectors": [(k, np.asarray(p, float), np.asarray(f, float)) for k, p, f in vs]}
                  for sid, vs in ground_forces.items()}
        return ground, None, "given"
    statics = support_loads(design, g_factor, step_mm, step_supports if step_mm is not None else None, summary=summ)
    ground = {}
    for sid, sl in statics["supports"].items():
        ground[sid] = dict(sl, vectors=_split(sl.get("contact_point", sl["point_mm"]), sl["force_N"], sl["contact"]))
    return ground, statics, ("K0 step" if step_mm is not None else "K0 rest")


def check_balance(design: dict, summ: dict, ground: dict, g_factor: float, inertia_N) -> float:
    """k1_frame.analyze's balance test (same masses, same tolerance); raises DesignError when it fails."""
    weight = summ["mass_kg"] * STANDARD_GRAVITY * g_factor
    pairs = [(p, f) for g in ground.values() for _, p, f in g["vectors"]]
    pairs += [(it.cg_mm, np.array([0.0, 0.0, -it.mass_kg * STANDARD_GRAVITY * g_factor])) for it in summ["items"]]
    F, M = resultant(pairs)
    M_cg = M - np.cross(summ["cg_mm"], F)
    span = max(float(np.ptp([s["position_mm"][0] for s in design["supports"]])), 1.0)
    if inertia_N is None:
        off = (float(np.linalg.norm(F)) + float(np.linalg.norm(M_cg)) / span) / weight
        if off > BALANCE_TOL:
            raise DesignError(
                f"the ground forces do not balance the weight of this design: they leave {np.round(F, 1).tolist()} N "
                f"and {np.round(M_cg / 1000.0, 1).tolist()} N*m about the centre of gravity unbalanced ({off:.1%} of "
                f"the weight, more than {BALANCE_TOL:.0%}) and no inertia force came with them; they were probably "
                f"computed for another loading or design version, so analyse them with the same loading")
    else:
        rest = F + np.asarray(inertia_N, float)
        off = float(np.linalg.norm(rest)) / weight
        if off > BALANCE_TOL:
            raise DesignError(
                f"the ground forces and the inertia force passed with them do not balance the weight of this design: "
                f"{np.round(rest, 1).tolist()} N is left over ({off:.1%} of the weight, more than {BALANCE_TOL:.0%}); "
                f"they were probably computed for another loading or design version, so analyse them with the same loading")
    return off


def analyze_shell(design: dict, g_factor: float = 1.0, step_mm: float | None = None,
                  step_supports: list[str] | None = None, ground_forces: dict | None = None,
                  inertia_N: np.ndarray | None = None, frame: ShellFrame | None = None,
                  element_size_mm: float = ELEMENT_SIZE_MM) -> dict:
    """Solve the shell frame (see the module notes). Arguments as k1_frame.analyze; `frame`
    reuses a built and factorised ShellFrame of the same design."""
    t0 = time.perf_counter()
    summ = mass_summary(design)
    items = summ["items"]
    fr = frame or ShellFrame.build(design, element_size_mm)
    ms, model = fr.mesh, fr.model
    ground, statics, source = _ground(design, summ, g_factor, step_mm, step_supports, ground_forces)
    off = check_balance(design, summ, ground, g_factor, inertia_N)

    # ---- masses: shell nodes (point masses) and parts
    parts = [it for it in items if it.carrier != "member"]
    m_shell = float(fr.masses_kg.sum())
    m_tot = m_shell + sum(it.mass_kg for it in parts)
    cg = (fr.masses_kg @ ms.nodes + sum(it.mass_kg * it.cg_mm for it in parts)) / m_tot
    d = ms.nodes - cg
    J = (np.sum(fr.masses_kg * np.einsum("ij,ij->i", d, d)) * np.eye(3)
         - np.einsum("i,ij,ik->jk", fr.masses_kg, d, d))
    for it in parts:
        r = it.cg_mm - cg
        J += it.mass_kg * (float(r @ r) * np.eye(3) - np.outer(r, r))
        if it.inertia_kg_mm2 is not None:
            J += it.inertia_kg_mm2

    # ---- imbalance of ground forces and weight -> rigid-body acceleration
    gvec = np.array([0.0, 0.0, -STANDARD_GRAVITY * g_factor])
    pairs = [(p, f) for g in ground.values() for _, p, f in g["vectors"]]
    pairs += [(it.cg_mm, it.mass_kg * gvec) for it in parts]
    F, M = resultant(pairs)
    F = F + m_shell * gvec
    M = M + np.cross(fr.masses_kg @ ms.nodes, gvec)
    M_cg = M - np.cross(cg, F)
    accel = F / m_tot  # m/s^2
    alpha = np.linalg.solve(J, M_cg) * 1e3  # (N*mm)/(kg*mm^2) -> rad/s^2

    def body(r):  # body force per kg (m/s^2) at points r (mm): gravity and d'Alembert inertia
        return gvec - accel - np.cross(alpha, np.asarray(r, float) - cg) * 1e-3

    n_dof = model.n_dof
    Fv = np.zeros(n_dof)
    bf = fr.masses_kg[:, None] * body(ms.nodes)
    for k in range(3):
        Fv[k::6] += bf[:, k]
    X = ms.nodes
    joint_loads: dict[str, list[np.ndarray]] = {}  # design node -> [force N, moment N*mm about the node]

    def put(node: str, force, moment, about) -> None:
        nonlocal Fv
        Fv += spread_load(X, ms.joints[node], force=force, moment=moment, about=about, n_dof=n_dof)
        acc = joint_loads.setdefault(node, [np.zeros(3), np.zeros(3)])
        acc[0] += force
        acc[1] += np.asarray(moment, float) + np.cross(np.asarray(about, float) - fr.node_xyz[node], force)

    for sid, g in ground.items():
        for _, p, f in g["vectors"]:
            put(fr.support_node[sid], f, np.zeros(3), p)
    for it in parts:
        f = it.mass_kg * body(it.cg_mm)
        m_own = -(it.inertia_kg_mm2 @ alpha) * 1e-3 if it.inertia_kg_mm2 is not None else np.zeros(3)  # N*mm
        if it.carrier == "support":
            put(fr.support_node[it.carried_by[0]], f, m_own, it.cg_mm)
            continue
        missing = [n for n in it.carried_by if n not in ms.joints]
        if missing:
            raise DesignError(f"{it.id} is carried by node(s) {', '.join(missing)}, which no member uses; "
                              f"carry it on frame nodes")
        pts = np.array([fr.node_xyz[n] for n in it.carried_by])
        k = len(pts)
        moment = np.cross(it.cg_mm - pts.mean(axis=0), f) + m_own
        for n, p in zip(it.carried_by, pts):
            put(n, f / k, moment / k, p)

    # ---- solve
    solver = fr.solver()
    U = solver.solve(Fv)
    react = solver.reactions(U, Fv)
    weight = m_tot * STANDARD_GRAVITY * g_factor
    residual = max(float(np.linalg.norm(react[dofs_of(n, "xyz")])) for n in fr.hold_nodes) / weight
    st = element_stresses(model, U)
    vm = {s: st.von_mises(s) for s in ("top", "bottom", "max")}

    # ---- read back
    members = np.asarray(ms.element_member)
    member_max: dict[str, tuple[float, int]] = {}
    for mid in dict.fromkeys(ms.element_member):
        idx = np.nonzero(members == mid)[0]
        e = int(idx[np.argmax(vm["max"][idx])])
        member_max[mid] = (float(vm["max"][e]), e)
    worst = max(member_max, key=lambda m: member_max[m][0])
    mat_of = {m["id"]: design["materials"][m["material"]] for m in design["members"]}
    safety = {m: (float(mat_of[m]["yield_MPa"]) / v if v > 0 else math.inf) for m, (v, _) in member_max.items()}

    u = U.reshape(-1, 6)[:, :3]
    pts, disps = [], []
    for sid in fr.support_node:
        j = ms.joints[fr.support_node[sid]]
        pts.append(X[j].mean(axis=0))
        disps.append(u[j].mean(axis=0))
    t, theta = rigid_fit(pts, disps)
    disp = u - (t[None, :] + np.cross(theta[None, :], X))
    worst_node = int(np.argmax(np.linalg.norm(disp, axis=1)))
    node_member = {}
    for e, el in enumerate(ms.elements):
        for n in el:
            node_member.setdefault(int(n), ms.element_member[e])

    timings = dict(fr.timings, **{k: v for k, v in model.timings.items() if k.endswith("_s")})
    timings["total_s"] = time.perf_counter() - t0
    return {
        "frame": fr,
        "mesh": ms,
        "mass": summ,
        "shell_mass_kg": m_shell,
        "total_mass_kg": m_tot,
        "cg_mm": cg,
        "weight_N": weight,
        "statics": statics,
        "ground_source": source,
        "ground": ground,
        "ground_total_N": sum(g["force_N"] for g in ground.values()),
        "balance_off": off,
        "joint_loads": joint_loads,
        "residual_ratio": residual,
        "inertia_accel": accel,
        "inertia_alpha": alpha,
        "U": U,
        "stress": st,
        "von_mises": vm,
        "member_max": member_max,
        "worst_member": worst,
        "worst_element": member_max[worst][1],
        "safety": safety,
        "displacements": disp,
        "worst_node": worst_node,
        "worst_node_member": node_member.get(worst_node, worst),
        "timings": timings,
    }


def element_place(res: dict, e: int) -> dict:
    """Where element e is: member, distance along it, the wall (by its outward normal), the nearest design node."""
    ms: ShellMesh = res["mesh"]
    st: ShellStress = res["stress"]
    mid = ms.element_member[e]
    n = st.frames[e][2]
    k = int(np.argmax(np.abs(n)))
    key = tuple(int(np.sign(n[i])) if i == k else 0 for i in range(3))
    nodes = res["frame"].node_xyz
    c = st.centres[e]
    near = min(nodes, key=lambda nid: float(np.linalg.norm(nodes[nid] - c)))
    return {"member": mid, "s_mm": float(ms.station_s_mm[e]), "wall": WALLS[key], "node": near,
            "node_distance_mm": float(np.linalg.norm(nodes[near] - c)), "centre_mm": c}


def membrane_bending(res: dict, e: int) -> dict:
    """Split element e's surface stresses into membrane (outer + inner) / 2 and bending (outer - inner) / 2.

    Values are the von Mises of each part's in-plane stress tensor, and the
    normal stress along the member axis on the outer and inner surface.
    """
    st: ShellStress = res["stress"]
    ms: ShellMesh = res["mesh"]
    top, bot = st.components("top")[e], st.components("bottom")[e]
    memb, bend = (top + bot) / 2.0, (top - bot) / 2.0
    vm = lambda s: float(np.sqrt(max(s[0] ** 2 - s[0] * s[1] + s[1] ** 2 + 3.0 * s[2] ** 2, 0.0)))  # noqa: E731
    axis = ms.member_axes[ms.element_member[e]][0]
    ax_top = float(st.in_plane(axis, "top")[e, 0])
    ax_bot = float(st.in_plane(axis, "bottom")[e, 0])
    return {"membrane_vm": vm(memb), "bending_vm": vm(bend), "outer_vm": float(res["von_mises"]["top"][e]),
            "inner_vm": float(res["von_mises"]["bottom"][e]), "axial_outer": ax_top, "axial_inner": ax_bot,
            "axial_membrane": (ax_top + ax_bot) / 2.0, "axial_bending": (ax_top - ax_bot) / 2.0}

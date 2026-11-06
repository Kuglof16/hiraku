"""K1 shell natural frequencies and mode shapes of the frame on its suspension springs.

The model is the k1_shell mesh of the tube walls (MITC4, solve.ShellModel), with:

- mass, lumped (diagonal) per element: a quarter of rho t area on each node's three translations
  and that quarter times t^2 / 12 on each of its three rotations (element.lumped_mass: the wall's
  rotary inertia about its mid-surface, used for the drilling rotation too);
- the parts as the beam modal model carries them (k1_frame.modal.add_masses): every mass item but
  the tubes' own (carrier "member", the shell elements carry it) and the parts the shell elements
  already are (frame.shell_parts: gussets, through parts). A part carried by frame nodes puts
  mass / len(carried_by) as a point mass at each carried_by design node; a part carried by a support
  (wheel, suspension) its mass at the wheel centre (position_mm). Translational mass only; the parts'
  own rotary inertia is not included (nor in the beam model);
- a design node's point mass moves with the joint's mesh nodes (mesh.joints) by the least-squares rigid
  fit of their translations (RBE3 type: no stiffness is added): u_p = P u_nodes, P (3 x 3n), and adds
  m P^T P to M (its translational mass is exactly m);
- each support's wheel centre is a node of its own, the master of a rigid link (RBE2) to the wall
  nodes of its wheel mount (mesh.mounts, or the joint nodes where no tube passes through; nodes that
  are already the slaves of a tied joint keep their master): the bracket and stub axle rigid over the
  mount, as the beam model's stiff link from frame node to wheel centre and fe.frame.frame_model's
  RBE2 (an assumption, not checked). An RBE3 tie there was tried first and gave a first frame mode of
  4.90 Hz on offroad-4w-v1 at 20 mm (the wheel's mass and spring swinging on the bare wall) against
  6.32 Hz with the fe core's RBE2 and 8.95 Hz on the beams, and filled the factorisation with the
  dense mount blocks;
- the suspension spring of each support (stiffness_N_per_mm, type spring or kc_table) on the wheel
  centre's vertical translation to the ground (the beam model's vertical spring at the wheel node,
  k1_frame.model.restrain_on_wheels); a rigid support holds it vertically by the stiff spring
  HOLD_STIFFNESS instead;
- the in-plane holds of the beam modal model (x and y at the first support, y at the one farthest from
  it in x, at their frame nodes): stiff springs HOLD_STIFFNESS on the x / y translation of the frame
  node's point, rigid with the wheel centre;
- every rigid link (the tied joints' and the mounts') reduces K and M alike: K_r = T^T K T,
  M_r = T^T M T (solve.reduction_matrix, no fixed DOF).

Eigenproblem: scipy.sparse.linalg.eigsh(K_r, k, M=M_r, sigma, which="LM") in shift-invert mode,
with the factorisation of K_r - sigma M_r by hiraku.solvers.sparse_lu.superlu passed as OPinv and a
fixed start vector (a run gives the same modes every time). sigma = (2 pi SIGMA_HZ)^2, a small positive
shift below the body's bounce on its springs. Checks per mode: M-orthonormality (phi^T M_r phi = I)
and the relative residual |K_r phi - lambda M_r phi| / |K_r phi|.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import LinearOperator, eigsh

from ..k0_handcalc.vehicle import DesignError, mass_summary, node_table
from ..k1_frame.analyze import rigid_fit
from ..sparse_lu import superlu
from .frame import shell_parts
from .mesh import ELEMENT_SIZE_MM, ShellMesh, build_mesh
from .solve import ShellModel, reduction_matrix

SIGMA_HZ = 0.1  # the shift of the eigen solver (Hz): below any body mode on real springs (HIRAKU's choice)
# Stiff springs (N/mm) of the in-plane holds and of a rigid support's vertical hold: about 1000 times the
# suspension springs of the demo vehicle, and the size of the shell's own stiffness terms; a penalty, HIRAKU's
# choice, no source. Their effect on the frame modes is checked by the residual and the fe-core cross-check.
HOLD_STIFFNESS = 1.0e5
V0_SEED = 0  # seed of the eigen solver's start vector
ORTHO_TOL = 1e-6  # largest |phi^T M phi - I| accepted
RESIDUAL_TOL = 1e-6  # largest relative eigen residual accepted


def rigid_point_rows(X: np.ndarray, ids, point) -> np.ndarray:
    """P (3, 3n): the translation at `point` of the least-squares rigid motion through the translations
    of the nodes ids (X their coordinates), u_p = P [u_1; ...; u_n]. With r_i = x_i - c (c the centroid)
    the fit is t = mean(u_i), theta = A^+ sum r_i x u_i, A = sum (|r_i|^2 I - r_i r_i^T); u_p = t + theta x
    (point - c). A^+ is the pseudo-inverse, so nodes on a line (or one node) give the rotation about that
    line no part."""
    ids = np.asarray(ids, int)
    x = np.asarray(X, float)[ids]
    n = len(ids)
    c = x.mean(axis=0)
    r = x - c
    A = np.einsum("ij,ij->", r, r) * np.eye(3) - r.T @ r
    Ap = np.linalg.pinv(A, rcond=1e-10)
    d = np.asarray(point, float) - c
    D = _cross(d)
    P = np.zeros((3, 3 * n))
    for i in range(n):
        P[:, 3 * i:3 * i + 3] = np.eye(3) / n - D @ Ap @ _cross(r[i])
    return P


def _cross(v) -> np.ndarray:
    """[v]x with [v]x w = v x w."""
    x, y, z = v
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


@dataclass
class ShellModalModel:
    """K (springs and holds included) and M of the frame shell, the reduction T and what was put where."""

    design: dict
    mesh: ShellMesh
    model: ShellModel
    K: sp.csr_matrix
    M: sp.csr_matrix
    shell_mass_kg: float
    part_mass_kg: float
    parts: list  # [(item id, mass kg, where)]
    springs: dict  # support -> N/mm (the stiff hold for a rigid support)
    wheel: dict  # support -> its wheel-centre node (the RBE2 master of its mount); linkage: its rigid body node
    timings: dict = field(default_factory=dict)
    suspension: str = "spring"
    K_parts: dict = field(default_factory=dict)  # linkage: "shell" and linkage.CATEGORIES -> their K
    linkage: dict | None = None  # linkage: corners (CornerInfo), asm (linkage.Assembly), carriers, notes

    @property
    def total_mass_kg(self) -> float:
        return self.shell_mass_kg + self.part_mass_kg

    def translational_mass_kg(self) -> np.ndarray:
        """r^T M r (kg) for unit rigid translations along x, y and z (all nodes moving alike)."""
        out = []
        for k in range(3):
            r = np.zeros(self.model.n_dof)
            r[k::6] = 1.0
            out.append(float(r @ (self.M @ r)) * 1000.0)
        return np.array(out)


def hold_pair(sup: list[dict]) -> tuple[str, str]:
    """The supports of the in-plane holds, as k1_frame.model.restrain_on_wheels chooses them."""
    pts = {s["id"]: np.asarray(s["position_mm"], float) for s in sup}
    a = sup[0]["id"]
    b = max(pts, key=lambda k: abs(pts[k][0] - pts[a][0]))
    return a, b


def mass_items(design: dict, ms: ShellMesh) -> list:
    """The mass items the shell modal model adds as point masses (k1_frame.modal.add_masses' items less
    the ones the shell elements carry)."""
    skip = shell_parts(design, ms)
    return [it for it in mass_summary(design)["items"]
            if it.carrier != "member" and it.id.split(":", 1)[-1] not in skip]


def build_modal(design: dict, element_size_mm: float = ELEMENT_SIZE_MM, weld_strips: bool = True,
                mesh: ShellMesh | None = None, suspension: str = "spring",
                tyre_stiffness_N_per_mm: float | None = None) -> ShellModalModel:
    """The shell modal model of the design's frame (module notes). suspension "spring": the wheel centres
    RBE2 on their mounts on the beam model's springs; "linkage": the arms, knuckle and wheel, spring and tyre
    of k1_shell.linkage (tyre_stiffness_N_per_mm: the tyre's vertical rate)."""
    if suspension == "linkage":
        return build_linkage_modal(design, element_size_mm, weld_strips, mesh, tyre_stiffness_N_per_mm)
    if suspension != "spring":
        raise DesignError(f"suspension must be spring or linkage, got {suspension!r}")
    t0 = time.perf_counter()
    ms = mesh if mesh is not None else build_mesh(design, element_size_mm, weld_strips)
    model = ShellModel.from_mesh(ms, design)
    n_dof = model.n_dof
    X = ms.nodes
    xyz = node_table(design)
    sup = design.get("supports", [])
    if len(sup) < 3:
        raise DesignError(f"need at least 3 supports to stand the frame on its springs, the design has {len(sup)}")
    for s in sup:
        if s.get("frame_node") not in ms.joints:
            raise DesignError(f"support {s['id']!r} names frame_node {s.get('frame_node')!r}, which no member uses")
    # wheel centres: a node each, the master of its mount's wall nodes (RBE2: the bracket and stub axle rigid)
    slaves_before = {s for s, _ in model.links}
    wheel = {}
    for s in sup:
        w = model.add_node(np.asarray(s["position_mm"], float))
        ids = np.asarray(ms.mounts.get(s["frame_node"], ms.joints[s["frame_node"]]), int)
        model.links += [(int(i), w) for i in ids if int(i) not in slaves_before]
        slaves_before.update(int(i) for i in ids)
        wheel[s["id"]] = w
    n_dof = model.n_dof
    t1 = time.perf_counter()
    K = model.stiffness()
    t2 = time.perf_counter()
    shell_mass = model.total_mass() * 1000.0
    Mdiag = model.mass_diagonal()
    Kdiag = np.zeros(n_dof)
    rows_M, cols_M, vals_M = [], [], []
    rows_K, cols_K, vals_K = [], [], []

    def add(rows, cols, vals, d, B):
        rows.append(np.repeat(d, len(d)))
        cols.append(np.tile(d, len(d)))
        vals.append(B.ravel())

    def tdofs(ids):
        return (6 * np.asarray(ids, int)[:, None] + np.arange(3)[None, :]).ravel()

    parts, part_mass = [], 0.0
    node_mass: dict[str, float] = {}
    by_id = {s["id"]: s for s in sup}
    for it in mass_items(design, ms):
        if it.carrier == "support":
            sid = it.carried_by[0]
            Mdiag[6 * wheel[sid]:6 * wheel[sid] + 3] += it.mass_kg / 1000.0
            parts.append((it.id, it.mass_kg, f"wheel centre of {sid}"))
        else:
            missing = [n for n in it.carried_by if n not in ms.joints]
            if missing:
                raise DesignError(f"{it.id} is carried by node(s) {', '.join(missing)}, which no member uses")
            for n in it.carried_by:
                node_mass[n] = node_mass.get(n, 0.0) + it.mass_kg / len(it.carried_by)
            parts.append((it.id, it.mass_kg, ", ".join(it.carried_by)))
        part_mass += it.mass_kg
    for n, m in node_mass.items():
        ids = np.asarray(ms.joints[n], int)
        P = rigid_point_rows(X, ids, xyz[n])
        add(rows_M, cols_M, vals_M, tdofs(ids), (m / 1000.0) * (P.T @ P))
    springs = {}
    for s in sup:
        susp = s.get("suspension", {})
        k = susp.get("stiffness_N_per_mm")
        rigid = not (susp.get("type") in ("spring", "kc_table") and k)
        k = HOLD_STIFFNESS if rigid else float(k)
        springs[s["id"]] = k
        Kdiag[6 * wheel[s["id"]] + 2] += k  # vertical spring at the wheel centre to the ground
    a_id, b_id = hold_pair(sup)
    for sid, axes in ((a_id, (0, 1)), (b_id, (1,))):
        w = wheel[sid]
        d = xyz[by_id[sid]["frame_node"]] - model.nodes[w]  # the frame-node point, rigid with the wheel centre
        R = np.hstack([np.eye(3), -_cross(d)])  # its translation from the centre's (u, theta)
        for ax in axes:
            add(rows_K, cols_K, vals_K, 6 * w + np.arange(6), HOLD_STIFFNESS * np.outer(R[ax], R[ax]))
    Kt = K + sp.diags(Kdiag)
    if rows_K:
        Kt = Kt + sp.coo_matrix((np.concatenate(vals_K), (np.concatenate(rows_K), np.concatenate(cols_K))),
                                shape=(n_dof, n_dof))
    Mt = sp.diags(Mdiag).tocsr()
    if rows_M:
        Mt = Mt + sp.coo_matrix((np.concatenate(vals_M), (np.concatenate(rows_M), np.concatenate(cols_M))),
                                shape=(n_dof, n_dof))
    Kt = ((Kt + Kt.T) * 0.5).tocsr()
    Mt = ((Mt + Mt.T) * 0.5).tocsr()
    out = ShellModalModel(design, ms, model, Kt, Mt, shell_mass, part_mass, parts, springs, wheel)
    out.timings.update(mesh_s=t1 - t0, stiffness_s=t2 - t1, masses_s=time.perf_counter() - t2)
    return out


DEFAULT_TYRE_N_PER_MM = 60.0  # the tyre's vertical rate when neither the run nor the design gives one (HIRAKU's choice)


def tyre_rate_of(design: dict, given: float | None) -> tuple[float, str]:
    """(N/mm, where from): the run's value, else the design variable tyre_stiffness, else DEFAULT_TYRE_N_PER_MM."""
    from ..k0_handcalc import units

    if given is not None:
        return float(given), "the run parameter tyre_stiffness"
    q = (design.get("variables") or {}).get("tyre_stiffness")
    if q is not None:
        return float(units.to_unit(q, "N/mm", "tyre_stiffness")), "the design variable tyre_stiffness"
    return DEFAULT_TYRE_N_PER_MM, f"HIRAKU's default {DEFAULT_TYRE_N_PER_MM:g} N/mm (the design has no tyre_stiffness)"


def arm_material(design: dict) -> tuple[dict, str]:
    """The arms' steel: the design's machine-structural tube (a material id starting stkm), else the material of
    the first frame member."""
    from .linkage import ARM_FALLBACK

    mats = design.get("materials") or {}
    mid = next((k for k in mats if k.lower().startswith("stkm")), None)
    if mid is None and design.get("members"):
        mid = design["members"][0].get("material")
    if mid in mats:
        return {k: float(mats[mid][k]) for k in ("E_MPa", "nu", "density_kg_m3")}, mid
    return dict(ARM_FALLBACK), "steel E 205000 MPa, nu 0.3, 7850 kg/m^3 (the design has no material for the arms)"


def corner_parts(design: dict, sid: str, items: list, hp: dict, mat: dict):
    """linkage.CornerParts of the support sid from its mass items (carrier support)."""
    from hiraku.designgen.kit.arms import ARM_D_MM, ARM_T_MM, cross_ends, part_ids, tube_mass

    from .linkage import RIGID_KINDS, CornerParts

    kind = {p["id"]: str(p.get("kind", "")).lower() for p in design.get("parts", [])}
    role_of = {v: k for k, v in part_ids(sid).items()}
    p = {k: np.asarray(v, float) for k, v in hp.items() if isinstance(v, list)}
    cf, cr = cross_ends({k: p[k] for k in ("lca_front", "lca_rear", "lca_outer", "damper_lower")})
    lengths = {"lca_f": np.linalg.norm(p["lca_outer"] - p["lca_front"]),
               "lca_r": np.linalg.norm(p["lca_outer"] - p["lca_rear"]), "lca_x": np.linalg.norm(cr - cf),
               "uca_f": np.linalg.norm(p["uca_outer"] - p["uca_front"]),
               "uca_r": np.linalg.norm(p["uca_outer"] - p["uca_rear"])}
    tubes, rigid, other, ids = {}, [], [], {}
    tie, bj_l, bj_u, damper = 0.0, 0.0, 0.0, 0.0
    for it in items:
        pid = it.id.split(":", 1)[-1]
        role, k = role_of.get(pid), kind.get(pid, "")
        entry = (it.mass_kg, np.asarray(it.cg_mm, float), it.inertia_kg_mm2)
        if role in lengths:
            tubes[role] = tubes.get(role, 0.0) + it.mass_kg
        elif role == "bj_lower":
            bj_l += it.mass_kg
        elif role == "bj_upper":
            bj_u += it.mass_kg
        elif role == "damper" or k == "damper":
            role, damper = "damper", damper + it.mass_kg
        elif k in ("tie_rod", "toe_link"):
            role, tie = "tie_rod", tie + it.mass_kg
        elif k in RIGID_KINDS:
            role = "rigid"
            rigid.append(entry)
        else:
            role = "other"
            other.append(entry)
        ids.setdefault(role, []).append(pid)
    rho = mat["density_kg_m3"]
    for role, L in lengths.items():  # an arm with no part: its tube's own mass
        if role not in tubes:
            tubes[role] = tube_mass(float(L), ARM_D_MM, ARM_T_MM, rho)
            ids.setdefault(f"{role} (no part: rho A L)", [])
    if tie <= 0.0:
        tie = tube_mass(float(np.linalg.norm(p["tie_outer"] - p["tie_inner"])), ARM_D_MM, ARM_T_MM, rho)
    return CornerParts(rigid, tubes, tie, bj_l, bj_u, damper, other, ids)


def build_linkage_modal(design: dict, element_size_mm: float = ELEMENT_SIZE_MM, weld_strips: bool = True,
                        mesh: ShellMesh | None = None, tyre_stiffness_N_per_mm: float | None = None) -> ShellModalModel:
    """The shell on the suspension linkage (k1_shell.linkage): the arms' inner pivots, the damper tops and the
    tie rods' inner ends on rigid bracket nodes (RBE2 over the wall nodes of the frame nodes that hold them,
    k1_frame.analyze.mount_carriers; the wheel mount's wall nodes for a support's frame node), the vehicle on
    its four tyres only (no penalty holds)."""
    from hiraku.designgen.kit.arms import ARM_D_MM, ARM_T_MM

    from ..k1_frame.analyze import mount_carriers
    from ..k2_vehicle.model import _bushings, hardpoints_of
    from . import linkage as lk

    t0 = time.perf_counter()
    ms = mesh if mesh is not None else build_mesh(design, element_size_mm, weld_strips)
    model = ShellModel.from_mesh(ms, design)
    X = ms.nodes
    xyz = node_table(design)
    sup = design.get("supports", [])
    if len(sup) < 3:
        raise DesignError(f"need at least 3 supports to stand the frame on its tyres, the design has {len(sup)}")
    tyre_k, tyre_from = tyre_rate_of(design, tyre_stiffness_N_per_mm)
    mat, mat_from = arm_material(design)
    carriers_of = mount_carriers(design)
    notes = [f"tyre {tyre_k:g} N/mm from {tyre_from}", f"arms of {mat_from}"]
    master_of: dict[int, int] = {s: m for s, m in model.links}
    bracket: dict[tuple, int] = {}

    def bracket_node(nodes: tuple) -> tuple[int, np.ndarray]:
        if nodes in bracket:
            return bracket[nodes], model.nodes[bracket[nodes]]
        for nd in nodes:
            if nd not in ms.joints:
                raise DesignError(f"frame node {nd} holds a suspension mount but no member uses it")
        c = np.mean([xyz[nd] for nd in nodes], axis=0)
        b = asm.node(c, "bracket:" + "+".join(nodes))
        ids = np.unique(np.concatenate([np.asarray(ms.mounts.get(nd, ms.joints[nd]), int) for nd in nodes]))
        new = [int(i) for i in ids if int(i) not in master_of]
        if new:
            model.links += [(i, b) for i in new]
            master_of.update({i: b for i in new})
        else:  # every wall node there is already held by another bracket: ride on that one
            model.links.append((b, master_of[int(ids[0])]))
            master_of[b] = master_of[int(ids[0])]
        bracket[nodes] = b
        return b, c

    asm = lk.Assembly(model.add_node)
    corners, by_id = {}, {s["id"]: s for s in sup}
    support_items: dict[str, list] = {s["id"]: [] for s in sup}
    frame_items = []
    for it in mass_items(design, ms):
        if it.carrier == "support":
            support_items[it.carried_by[0]].append(it)
        else:
            frame_items.append(it)
    for s in sup:
        sid = s["id"]
        susp = s.get("suspension") or {}
        if not (susp.get("type") in ("spring", "kc_table") and susp.get("stiffness_N_per_mm")):
            raise DesignError(f"support {sid} has no suspension spring (stiffness_N_per_mm): the linkage model needs one")
        hp = hardpoints_of(design, sid)
        bush = _bushings(design, susp, notes)
        bush = {"radial": bush.radial / 1000.0, "axial": bush.axial / 1000.0, "tie_rod": bush.tie_rod / 1000.0}
        parts = corner_parts(design, sid, support_items[sid], hp, mat)
        k_w = float(susp["stiffness_N_per_mm"])
        k_s, ratio, comp = lk.fit_spring_rate(sid, hp, float(s["radius_mm"]), parts, mat, ARM_D_MM, ARM_T_MM, bush, k_w)
        held = carriers_of.get(sid, {})

        def carrier(mount, sid=sid, held=held, fn=s["frame_node"]):
            nodes = tuple(held.get(mount) or [fn]) if mount != "frame_node" else (fn,)
            return bracket_node(nodes)

        info = lk.build_corner(asm, sid, hp, float(s["radius_mm"]), carrier, parts, mat, ARM_D_MM, ARM_T_MM, bush,
                               tyre_k, k_s, k_w)
        info.motion_ratio = ratio
        corners[sid] = dict(info=info, parts=parts, bushings=bush, linkage_compliance=comp,
                            carriers={m: list(held.get(m) or [s["frame_node"]]) for m in
                                      ("lca_front", "lca_rear", "uca_front", "uca_rear", "tie_inner", "damper_upper")})
    n_dof = model.n_dof
    t1 = time.perf_counter()
    K = model.stiffness()
    t2 = time.perf_counter()
    shell_mass = model.total_mass() * 1000.0
    Mdiag = model.mass_diagonal()
    rows_M, cols_M, vals_M = [], [], []
    parts_log, part_mass = [], 0.0
    node_mass: dict[str, float] = {}
    for it in frame_items:
        missing = [n for n in it.carried_by if n not in ms.joints]
        if missing:
            raise DesignError(f"{it.id} is carried by node(s) {', '.join(missing)}, which no member uses")
        for n in it.carried_by:
            node_mass[n] = node_mass.get(n, 0.0) + it.mass_kg / len(it.carried_by)
        parts_log.append((it.id, it.mass_kg, ", ".join(it.carried_by)))
        part_mass += it.mass_kg
    for sid, its in support_items.items():
        for it in its:
            parts_log.append((it.id, it.mass_kg, f"linkage of {sid}"))
            part_mass += it.mass_kg
    for n, m in node_mass.items():
        ids = np.asarray(ms.joints[n], int)
        P = rigid_point_rows(X, ids, xyz[n])
        d = (6 * ids[:, None] + np.arange(3)[None, :]).ravel()
        rows_M.append(np.repeat(d, len(d)))
        cols_M.append(np.tile(d, len(d)))
        vals_M.append(((m / 1000.0) * (P.T @ P)).ravel())
    Kl, Ml = asm.matrices(n_dof)
    Kparts = {"shell": K, **Kl}
    Kt = K
    for c in lk.CATEGORIES:
        Kt = Kt + Kl[c]
    Mt = sp.diags(Mdiag).tocsr() + Ml
    if rows_M:
        Mt = Mt + sp.coo_matrix((np.concatenate(vals_M), (np.concatenate(rows_M), np.concatenate(cols_M))),
                                shape=(n_dof, n_dof))
    Kt = ((Kt + Kt.T) * 0.5).tocsr()
    Mt = ((Mt + Mt.T) * 0.5).tocsr()
    springs = {sid: c["info"].spring_rate for sid, c in corners.items()}
    wheel = {sid: c["info"].body for sid, c in corners.items()}
    out = ShellModalModel(design, ms, model, Kt, Mt, shell_mass, part_mass, parts_log, springs, wheel,
                          suspension="linkage", K_parts=Kparts,
                          linkage={"corners": corners, "asm": asm, "brackets": dict(bracket), "notes": notes,
                                   "tyre_N_per_mm": tyre_k, "material": mat})
    out.timings.update(mesh_s=t1 - t0, stiffness_s=t2 - t1, masses_s=time.perf_counter() - t2)
    return out


def linkage_layout(design: dict) -> dict:
    """The linkage's drawn points at rest (mm) and its lines, as build_linkage_modal places them, without the
    shell: {"points": {name: xyz}, "lines": [[name, name, element]], "bodies": {support: {"centre", "radius"}}}."""
    from hiraku.designgen.kit.arms import ARM_D_MM, ARM_T_MM

    from ..k1_frame.analyze import mount_carriers
    from ..k2_vehicle.model import hardpoints_of
    from . import linkage as lk

    xyz_n = node_table(design)
    pts: list = []

    def add(x):
        pts.append(np.asarray(x, float))
        return len(pts) - 1

    asm = lk.Assembly(add)
    mat, _ = arm_material(design)
    held_all = mount_carriers(design)
    brackets: dict = {}
    bodies = {}
    for s in design.get("supports", []):
        sid = s["id"]
        hp = hardpoints_of(design, sid)
        held = held_all.get(sid, {})

        def carrier(mount, held=held, fn=s["frame_node"]):
            nodes = tuple(held.get(mount) or [fn]) if mount != "frame_node" else (fn,)
            if nodes not in brackets:
                brackets[nodes] = asm.node(np.mean([xyz_n[n] for n in nodes], axis=0), "bracket:" + "+".join(nodes))
            return brackets[nodes], asm.xyz[brackets[nodes]]

        parts = lk.CornerParts([(1.0, np.asarray(hp["wheel_center"], float), None)],
                               {k: 1.0 for k in ("lca_f", "lca_r", "lca_x", "uca_f", "uca_r")}, 1.0, 0.0, 0.0, 0.0)
        lk.build_corner(asm, sid, hp, float(s["radius_mm"]), carrier, parts, mat, ARM_D_MM, ARM_T_MM,
                        {"radial": 1.0, "axial": 1.0, "tie_rod": 1.0}, 1.0, 1.0)
        bodies[sid] = {"centre_mm": [float(v) for v in hp["wheel_center"]], "radius_mm": float(s["radius_mm"]),
                       "width_mm": float(s.get("width_mm", 0.0))}
    points = {k: [round(float(v), 3) for v in asm.xyz[n] + d] for k, (n, d) in asm.points.items() if drawn(k)}
    lines = [list(x) for x in asm.lines if drawn(x[0]) and drawn(x[1])]
    return {"points": points, "lines": lines, "bodies": bodies}


def drawn(name: str) -> bool:
    """The linkage points a mode shape gives (not the bracket nodes, not the rigid body's centre of mass: its
    wheel_center and contact points draw it)."""
    return not (name.startswith("bracket:") or name.endswith(".wheel_body"))


ZERO_HZ = 0.02  # a mode below this is a rigid motion (free on the tyres sideways, fore-aft and in yaw)


def linkage_groups(mm: ShellModalModel) -> dict:
    """DOF masks of the frame (mesh and bracket nodes), each corner's rigid body and each corner's arms."""
    n = mm.model.n_dof // 6
    owner = np.full(n, "frame", dtype=object)
    for sid, c in mm.linkage["corners"].items():
        owner[c["info"].nodes] = f"arms:{sid}"
        owner[c["info"].body] = f"wheel:{sid}"
    return {"owner": np.repeat(owner, 6)}


def linkage_energies(mm: ShellModalModel, phi: np.ndarray, omega2: np.ndarray) -> list[dict]:
    """Per mode: kinetic energy shares (frame, wheel:<sid>, arms:<sid>) and strain energy shares (shell and the
    linkage's categories)."""
    owner = linkage_groups(mm)["owner"]
    keys = sorted(set(owner))
    MP = mm.M @ phi
    out = []
    for i in range(phi.shape[1]):
        e = phi[:, i] * MP[:, i]
        tot = float(e.sum()) or 1.0
        ke = {k: float(e[owner == k].sum()) / tot for k in keys}
        se = {c: float(phi[:, i] @ (Kc @ phi[:, i])) for c, Kc in mm.K_parts.items()}
        st = sum(se.values()) or 1.0
        out.append({"kinetic": ke, "strain": {c: v / st for c, v in se.items()}, "omega2": float(omega2[i])})
    return out


@dataclass
class ShellModes:
    """frequencies (k,) Hz ascending; shapes (n_dof, k) mass-normalised in the full DOF vector (phi = T phi_r);
    the checks and the clock times."""

    frequencies: np.ndarray
    omega2: np.ndarray
    shapes: np.ndarray
    orthonormality: float  # largest |Phi_r^T M_r Phi_r - I|
    residuals: np.ndarray  # (k,) relative residual per mode
    n_free: int
    timings: dict = field(default_factory=dict)


def solve_modes(K: sp.spmatrix, M: sp.spmatrix, nodes: np.ndarray, links, n_modes: int, fixed=(),
                sigma_hz: float = SIGMA_HZ, refine: int = 0) -> ShellModes:
    """The lowest n_modes of K phi = omega^2 M phi (N, mm, tonne: omega^2 in 1/s^2) after the reduction for
    rigid links and fixed DOFs (solve.reduction_matrix), by eigsh shift-invert about (2 pi sigma_hz)^2;
    refine: subspace steps with the same factorisation after eigsh (the linkage model takes one)."""
    t0 = time.perf_counter()
    T, _ = reduction_matrix(np.asarray(nodes, float), links, np.asarray(fixed, int))
    Kr = (T.T @ K @ T).tocsc()
    Mr = (T.T @ M @ T).tocsc()
    n = Kr.shape[0]
    k = max(1, min(int(n_modes), n - 2))
    sigma = (2.0 * math.pi * sigma_hz) ** 2
    t1 = time.perf_counter()
    try:
        lu = superlu((Kr - sigma * Mr).tocsc())
    except RuntimeError as e:
        raise DesignError(f"the shell modal model cannot be factorised ({e}): a part is not connected or the frame "
                          f"is not held") from None
    t2 = time.perf_counter()
    op = LinearOperator((n, n), matvec=lu.solve, dtype=float)
    v0 = np.random.default_rng(V0_SEED).random(n)
    w2, vec = eigsh(Kr, k=k, M=Mr, sigma=sigma, which="LM", OPinv=op, v0=v0)
    for _ in range(int(refine)):
        # one shift-invert subspace step on eigsh's vectors and Rayleigh-Ritz in that subspace: the soft modes
        # next to rigid ones come out of eigsh with residuals about 1e-5 (linkage model); this takes them down
        from scipy.linalg import eigh, qr

        MV = Mr @ vec
        W = np.column_stack([lu.solve(np.ascontiguousarray(MV[:, j])) for j in range(vec.shape[1])])
        Q, _ = qr(W, mode="economic")
        w2, y = eigh(Q.T @ (Kr @ Q), Q.T @ (Mr @ Q))
        vec = Q @ y
    t3 = time.perf_counter()
    del lu, op
    order = np.argsort(w2)
    w2, vec = w2[order], vec[:, order]
    if not np.all(np.isfinite(w2)):
        raise DesignError("the shell modal analysis returned NaN frequencies; check member sizes and part masses")
    # mass-normalise again (eigsh's vectors are M-orthonormal to its tolerance) and check
    nrm = np.sqrt(np.einsum("ik,ik->k", vec, Mr @ vec))
    vec = vec / nrm
    G = vec.T @ (Mr @ vec)
    ortho = float(np.max(np.abs(G - np.eye(k))))
    KV = Kr @ vec
    res = np.linalg.norm(KV - (Mr @ vec) * w2[None, :], axis=0) / np.maximum(np.linalg.norm(KV, axis=0), 1e-300)
    phi = T @ vec
    f = np.sqrt(np.maximum(w2, 0.0)) / (2.0 * math.pi)
    return ShellModes(f, w2, phi, ortho, res, int(n),
                      {"reduce_s": t1 - t0, "factor_s": t2 - t1, "eigsh_s": t3 - t2, "checks_s": time.perf_counter() - t3})


def signed(u: np.ndarray) -> np.ndarray:
    """The shape (n, 3) with its largest component positive (the first one of those within 1e-9 of the largest)."""
    comps = u.ravel()
    big = float(np.max(np.abs(comps)))
    first = comps[int(np.argmax(np.abs(comps) >= big * (1.0 - 1e-9)))]
    return -u if first < 0 else u


def flexibility(X: np.ndarray, u: np.ndarray, ids: np.ndarray | None = None, total: float | None = None) -> float:
    """How far the translations u (n, 3) of the nodes (X) are from a rigid motion: the residual of the
    least-squares rigid fit over the size of the motion (k1_frame.modal's measure, on the mesh nodes; ids: only
    those nodes fitted, over `total`, the size of the whole motion)."""
    if ids is not None:
        X, u = X[ids], u[ids]
    t, th = rigid_fit(list(X), list(u))
    r = u - (t[None, :] + np.cross(th[None, :], X))
    tot = total if total is not None else math.sqrt(float(np.sum(u * u)))
    return math.sqrt(float(np.sum(r * r))) / (tot or 1.0)


def frame_nodes_of(design: dict, ms: ShellMesh) -> np.ndarray:
    """Mesh nodes of the elements of the frame itself (members of part frame, and the gussets): those only on
    members of another part (member.part, e.g. a roll bar) are left out (k1_frame.modal.ladder_nodes)."""
    part = {m["id"]: m.get("part", "frame") for m in design.get("members", [])}
    keep = np.array([part.get(m, "frame") == "frame" for m in ms.element_member])
    ids = np.unique(np.asarray(ms.elements)[keep].ravel()) if keep.any() else np.arange(ms.n_nodes)
    return ids


def node_mesh_shape(ms: ShellMesh, u: np.ndarray, frame_nodes: list[str]) -> np.ndarray:
    """(n_frame_nodes, 3) mean translation of each design node's joint nodes."""
    return np.array([u[np.asarray(ms.joints[n], int)].mean(axis=0) for n in frame_nodes])


def mac(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Modal assurance criterion of the columns of a and b."""
    num = (a.T @ b) ** 2
    return num / np.outer(np.einsum("ik,ik->k", a, a), np.einsum("ik,ik->k", b, b))


def point_shapes(mm: ShellModalModel, phi_i: np.ndarray) -> dict:
    """linkage: the translation of every drawn point of the linkage (its nodes and the frame-side points)."""
    u6 = phi_i.reshape(-1, 6)
    from .linkage import offset_rows

    return {name: offset_rows(d) @ u6[node] for name, (node, d) in mm.linkage["asm"].points.items() if drawn(name)}


def modes(design: dict, n_modes: int = 20, element_size_mm: float = ELEMENT_SIZE_MM, weld_strips: bool = True,
          mesh: ShellMesh | None = None, suspension: str = "spring",
          tyre_stiffness_N_per_mm: float | None = None) -> dict:
    """Frequencies, mode shapes per mesh node (translations, largest = 1, signed), the flexibility of each mode
    on all mesh nodes and on the frame's own, the solve's checks and timings, and the model.

    linkage: the rigid modes (below ZERO_HZ: the vehicle free on its tyres in x, y and yaw) are solved for too
    and set apart (zero_modes); n_modes are the elastic ones after them. Per mode also the energy shares
    (linkage_energies) and the linkage's point shapes (point_shapes), all scaled with the mesh nodes by one
    factor: point_scale makes the largest of mesh and linkage points 1."""
    mm = build_modal(design, element_size_mm, weld_strips, mesh, suspension, tyre_stiffness_N_per_mm)
    ms = mm.mesh
    extra = 3 if suspension == "linkage" else 0
    r = solve_modes(mm.K, mm.M, mm.model.nodes, mm.model.links, n_modes + extra, refine=1 if extra else 0)
    keep = np.arange(len(r.frequencies))
    zero = np.array([], int)
    if suspension == "linkage":
        zero = np.nonzero(r.frequencies < ZERO_HZ)[0]
        keep = np.nonzero(r.frequencies >= ZERO_HZ)[0][:n_modes]
    X = ms.nodes
    ladder = frame_nodes_of(design, ms)
    shapes, flex, lflex, scale, pshapes = [], [], [], [], []
    for i in keep:
        u = r.shapes[:, i].reshape(-1, 6)[:ms.n_nodes, :3]  # the mesh nodes (the wheel centres follow them)
        s = float(np.max(np.linalg.norm(u, axis=1))) or 1.0
        un = u / s
        if suspension == "linkage":
            pts = point_shapes(mm, r.shapes[:, i])
            s2 = max(s, max(float(np.linalg.norm(v)) for v in pts.values()))
            sign = 1.0 if signed(un) is un else -1.0  # the mesh shape's sign (signed returns u itself unflipped)
            pshapes.append({k: sign * v / s2 for k, v in pts.items()})
            scale.append(s2)
        else:
            scale.append(s)
        u = signed(un)
        tot = math.sqrt(float(np.sum(u * u)))
        flex.append(flexibility(X, u, total=tot))
        lflex.append(flex[-1] if len(ladder) == ms.n_nodes else flexibility(X, u, ladder, tot))
        shapes.append(u)
    timings = dict(mm.timings, **r.timings)
    out = {"frequencies": r.frequencies[keep], "omega2": r.omega2[keep], "shapes": shapes,
           "flexibility": np.array(flex), "ladder_flexibility": np.array(lflex), "phi": r.shapes[:, keep],
           "scale": np.array(scale), "solve": r, "model": mm, "timings": timings, "keep": keep}
    if suspension == "linkage":
        out["zero_modes"] = {"frequencies": r.frequencies[zero], "energies": linkage_energies(
            mm, r.shapes[:, zero], r.omega2[zero])}
        out["energies"] = linkage_energies(mm, r.shapes[:, keep], r.omega2[keep])
        out["point_shapes"] = pshapes
        out["mesh_scale"] = np.array([float(np.max(np.linalg.norm(r.shapes[:, i].reshape(-1, 6)[:ms.n_nodes, :3],
                                                                    axis=1))) for i in keep])
    return out


def mode_strain(mm: ShellModalModel, phi: np.ndarray) -> np.ndarray:
    """Element von Mises of the mode's stress (E times its strain; the larger of the outer and inner surface,
    at the element centre) over the largest in the mode: (E,) in 0..1."""
    from .stress import element_stresses

    vm = element_stresses(mm.model, phi).von_mises("max")
    big = float(np.max(vm))
    return vm / big if big > 0 else vm


__all__ = ["HOLD_STIFFNESS", "SIGMA_HZ", "ShellModalModel", "ShellModes", "build_modal", "flexibility",
           "frame_nodes_of", "mac", "mass_items", "mode_strain", "modes", "node_mesh_shape", "rigid_point_rows",
           "solve_modes"]

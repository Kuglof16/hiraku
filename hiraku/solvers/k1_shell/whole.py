"""The whole-vehicle step-hit model: the frame shell of frame.analyze_shell with additions switched on one at a
time (analyze_shell(..., model="whole", adds=[...]); adds empty: today's frame solve, unchanged). Units N, mm,
tonne, s, as the shell.

Additions (ADDS):

- "arms": the suspension of k1_shell.linkage (build_corner, the modal model's elements): the control arms and
  the tie rod as beams on K2's bushings, the knuckle + wheel as one rigid body, the coil spring along the damper
  axis to the damper's upper mount (its rate fitted to K2's wheel rate, linkage.fit_spring_rate), the tyre's
  vertical spring from the contact point to a ground node. Each mount of the frame side is a node at the hard
  point joined to the frame by a distributing tie (RBE3 type: the point's translation is the least-squares rigid
  motion of the nodes the load went on in today's solve, modal.rigid_point_rows; the tie adds no stiffness to
  the frame, only couples the rigid-fit part of those nodes to the point, by the penalty RBE3_STIFFNESS; the
  point's rotations are held at zero: nothing loads them): a meshed bracket's pin hole rings (both cheeks), a
  bracket not meshed: the carrier's wall nodes under its box (frame.mount_footprints), else the joint nodes of
  the carried_by nodes (k1_frame.analyze.mount_carriers), else the support's wheel mount.
  The load: K2's knuckle load of each corner (k2_vehicle.sim.loads: the road force, the unsprung mass's weight
  and inertia m_u (g - a_u), and the road forces' moment about the wheel centre without its part about the spin
  axis), rebuilt as the resultant of K2's mount forces of the corner (its force, and its moment about the wheel
  centre; K2's mount forces are that load split by K2's linkage, so their resultant is it). Its vertical part
  goes on the ground node (through the tyre spring, at the contact point), the rest on the rigid body. The
  record's tyre force series (reaction / grip) are at the record's 25 Hz and not at the mount instants, so they
  are not used. The drive torque's reaction (K2's body_torque) stays on the frame's wheel mount, as today. The
  linkage's masses are not added: they are inside the knuckle load (m_u (g - a_u)). The tyre spring is in series
  with the load put at its free end, so it carries the vertical force and changes no stress (inertia relief:
  the model is held 3-2-1 as today, the loads self-balanced); the tyre rate changes the result only through K2.
- "damper_mounts": the damper's upper mount rigid on the nodes it was tied to (RBE2): the damper bracket's own
  stiffness as rigid (v005's damper brackets are not meshed: their base face is not on a tube wall along the
  pin, mesh.bracket_skipped; a rigid footprint is an upper bound on their stiffness).
- "brackets": the arms' and tie rods' mounts rigid the same way: a meshed bracket (its plates are in the shell
  already in today's model) gets a rigid pin through both cheeks' holes (the bolt and sleeve), a bracket not
  meshed a rigid footprint (upper bound), a mount on joint nodes a rigid joint.
- "seats": the seats (kind seat, with the occupants carried by the same nodes) and the battery as rigid bodies:
  their masses on a node at their centre of mass, RBE2 to the top wall of each carried_by joint (the joint
  nodes within 1 mm of the joint's highest), so the frame between their mount points is rigid: an upper bound.
- "panels": the parts with a thickness (a box whose smallest side is at most PANEL_MAX_T_MM and that names a
  material) as shell plates in the box's mid-plane, their mass kept (density scaled to the part's mass), tied
  rigidly to the nearest frame node where they lie on the frame: under the design's fixings (parts fit_<panel>_*)
  when it has them, else everywhere they touch (bonded: an upper bound). A panel tied at fewer than 3 nodes is
  left a point mass (said in the notes).

With "arms" the result has linkage_mounts: the force the body receives at each mount out of the linkage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np
import scipy.sparse as sp

from ..k0_handcalc.vehicle import DesignError, node_table
from .frame import ShellFrame, _hold_supports, hold_node, mount_footprints, shell_parts
from .mesh import ELEMENT_SIZE_MM, build_mesh
from .solve import ShellModel, StaticSolver, dofs_of

ADDS = ("arms", "damper_mounts", "brackets", "seats", "panels")
FRAME_MOUNTS = ("lca_front", "lca_rear", "uca_front", "uca_rear", "tie_inner", "damper_upper")
RBE3_STIFFNESS = 1.0e6  # N/mm, the penalty of a distributing tie (as linkage.JOINT_STIFFNESS; HIRAKU's choice)
PANEL_MAX_T_MM = 10.0  # a box this thin or thinner is a plate (HIRAKU's choice)
SEAT_KINDS = ("seat", "battery")
PANEL_KINDS = ("body",)  # the body panels (a net or a fender is not a plate of the body)


def panel_fits(design: dict, pid: str) -> list[dict]:
    """The design's fixings of panel pid: parts fit_<pid>_<...> with a box, not those of a panel whose id starts with
    pid (fit_floor_front_... is floor_front's, not floor's)."""
    ids = sorted((p["id"] for p in design.get("parts", [])), key=len, reverse=True)
    out = []
    for q in design.get("parts", []):
        if not q["id"].startswith("fit_") or not q.get("box"):
            continue
        owner = next((i for i in ids if q["id"].startswith(f"fit_{i}_")), None)
        if owner == pid:
            out.append(q)
    return out


@dataclass
class Attach:
    sid: str
    mount: str
    node: int  # the mount point's node (at the hard point)
    kind: str  # "rbe3" or "rbe2"
    nodes: np.ndarray  # the frame's mesh nodes it is tied to
    where: str


@dataclass
class WholeFrame(ShellFrame):
    adds: tuple = ()
    attach: dict = field(default_factory=dict)  # (sid, mount) -> Attach
    corners: dict = field(default_factory=dict)  # sid -> {"info", "ground", "wc", "contact", "motion_ratio", ...}
    frame_springs: list = field(default_factory=list)  # (sid, mount, a, da, b, db, Ks)
    part_master: dict = field(default_factory=dict)  # part id -> master node (seats)
    extra_shell_parts: set = field(default_factory=set)  # panels meshed as plates
    panels: dict = field(default_factory=dict)  # part id -> {"elements": (lo, hi), "ties", "t_mm"}
    n_mesh_nodes: int = 0
    notes: list = field(default_factory=list)


def _root(mo: dict, i: int) -> int:
    while i in mo:
        i = mo[i]
    return i


def tie_rigid(model: ShellModel, mo: dict, master: int, nodes) -> int:
    """RBE2: every node of `nodes` (through its own master, when it already follows one) rigid with master."""
    n_add = 0
    for n in np.unique(np.asarray(nodes, int)):
        r = _root(mo, int(n))
        if r == master:
            continue
        model.links.append((r, master))
        mo[r] = master
        n_add += 1
    return n_add


class _RecAsm:
    """linkage.Assembly that also records the springs whose second node is a mount point (the frame side)."""

    def __new__(cls, add_node, frame_side: dict, rec: list):
        from . import linkage as lk

        class A(lk.Assembly):
            def spring(self, cat, a, da, b, db, Ks):
                if b is not None and b in frame_side:
                    sid, mount = frame_side[b]
                    rec.append((sid, mount, int(a), np.asarray(da, float), int(b), np.asarray(db, float),
                                np.asarray(Ks, float)))
                super().spring(cat, a, da, b, db, Ks)

        return A(add_node)


def mount_node_sets(design: dict, fr: SimpleNamespace) -> dict:
    """{(sid, mount): (mesh nodes, where)} the mount forces go on in today's solve (mount_path footprint)."""
    from ..k1_frame.analyze import mount_carriers

    ms = fr.mesh
    carriers = mount_carriers(design)
    feet = mount_footprints(design, fr, carriers)
    out = {}
    for s in design.get("supports", []):
        sid = s["id"]
        hp = (s.get("suspension") or {}).get("hardpoints_mm") or {}
        for m in FRAME_MOUNTS:
            if m not in hp:
                continue
            bk = ms.bracket_of(sid, m)
            if bk is not None:
                out[(sid, m)] = (np.unique(np.concatenate([np.asarray(h, int) for h in bk.hole_nodes])),
                                 f"pin holes of {bk.id}")
                continue
            if (sid, m) in feet:
                bid, nodes = feet[(sid, m)]
                out[(sid, m)] = (np.asarray(nodes, int), f"footprint of {bid}")
                continue
            at = carriers.get(sid, {}).get(m) or []
            if at:
                out[(sid, m)] = (np.unique(np.concatenate([np.asarray(ms.joints[n], int) for n in at])),
                                 f"joint nodes of {', '.join(at)}")
            else:
                fn = s["frame_node"]
                out[(sid, m)] = (np.asarray(ms.mounts.get(fn, ms.joints[fn]), int), f"wheel mount of {fn}")
    return out


def _panel_parts(design: dict) -> list[dict]:
    out = []
    for p in design.get("parts", []):
        b = p.get("box") or {}
        if not b.get("size_mm") or not p.get("material") or b.get("rpy_deg"):
            continue
        if str(p.get("kind", "")).lower() not in PANEL_KINDS:
            continue
        if min(float(v) for v in b["size_mm"]) <= PANEL_MAX_T_MM and float(p.get("mass_kg", 0)) > 0:
            out.append(p)
    return out


def _add_panel(model: ShellModel, ms, design: dict, p: dict, h: float, mo: dict, n_mesh: int, notes: list):
    """One panel as a plate grid in its box's mid-plane, tied to the frame; returns its record or None."""
    from scipy.spatial import cKDTree

    b = p["box"]
    c, size = np.asarray(b["center_mm"], float), np.asarray(b["size_mm"], float)
    k = int(np.argmin(size))
    t = float(size[k])
    i1, i2 = [j for j in range(3) if j != k]
    n1, n2 = max(1, int(np.ceil(size[i1] / h))), max(1, int(np.ceil(size[i2] / h)))
    a1 = np.linspace(-size[i1] / 2, size[i1] / 2, n1 + 1)
    a2 = np.linspace(-size[i2] / 2, size[i2] / 2, n2 + 1)
    G1, G2 = np.meshgrid(a1, a2, indexing="ij")
    P = np.tile(c, (G1.size, 1))
    P[:, i1] += G1.ravel()
    P[:, i2] += G2.ravel()
    # ties: plate nodes near a frame node (mid-surfaces: half the plate, half the thickest wall, and the grid offset)
    frame_xyz = model.nodes[:n_mesh]
    tree = cKDTree(frame_xyz)
    t_wall = max(float(v) for v in ms.member_thickness_mm.values())
    tol = t / 2 + t_wall / 2 + 0.75 * h
    fits = panel_fits(design, p["id"])
    spacers = []  # (fixing id, plate grid indices over it, frame nodes inside it)
    if fits:
        for q in fits:
            qc = np.asarray(q["box"]["center_mm"], float)
            qs = np.asarray(q["box"]["size_mm"], float) / 2 + h / 2
            dP = np.abs(P - qc)
            over = np.nonzero((dP[:, i1] <= qs[i1]) & (dP[:, i2] <= qs[i2]))[0]
            dF = np.abs(frame_xyz - qc)
            inside = np.nonzero(np.all(dF <= qs + t_wall, axis=1))[0]
            if len(over) and len(inside):
                spacers.append((q["id"], over, inside))
        n_tied = sum(len(s[1]) for s in spacers)
        how = (f"at {len(spacers)} of its {len(fits)} fixings (each a rigid spacer: RBE2 of the plate nodes over it "
               f"and the frame nodes inside it: {', '.join(s[0] for s in spacers) or 'none'})")
        ok = None
    else:
        dist, near = tree.query(P, distance_upper_bound=tol)
        ok = np.isfinite(dist)
        n_tied = int(ok.sum())
        how = "wherever it lies on the frame (no fixings in the design: bonded, an upper bound)"
    if n_tied < 3 or (fits and len(spacers) < 2):
        notes.append(f"panel {p['id']}: tied at {n_tied} nodes only ({how}); left a point mass")
        return None
    mat = design["materials"][p["material"]]
    area = float(size[i1] * size[i2])
    rho = float(p["mass_kg"]) / 1000.0 / (area * t)  # t/mm^3: the part's mass
    base = model.n_nodes
    model.nodes = np.vstack([model.nodes, P])
    model._K = None
    idx = base + np.arange(G1.size).reshape(n1 + 1, n2 + 1)
    quads = np.stack([idx[:-1, :-1].ravel(), idx[1:, :-1].ravel(), idx[1:, 1:].ravel(), idx[:-1, 1:].ravel()], 1)
    lo = len(model.elements)
    model.elements = np.vstack([model.elements, quads])
    ne = len(quads)
    model.thickness = np.concatenate([model.thickness, np.full(ne, t)])
    model.E = np.concatenate([model.E, np.full(ne, float(mat["E_MPa"]))])
    model.nu = np.concatenate([model.nu, np.full(ne, float(mat["nu"]))])
    model.rho = np.concatenate([model.rho, np.full(ne, rho)])
    ties = 0
    if ok is not None:
        for i in np.nonzero(ok)[0]:
            model.links.append((int(base + i), int(near[i])))  # the plate node follows the frame node
            mo[int(base + i)] = int(near[i])
            ties += 1
    for _, over, inside in spacers:
        m = model.add_node(P[over].mean(axis=0))
        ties += tie_rigid(model, mo, m, np.concatenate([base + over, inside]))
    notes.append(f"panel {p['id']}: {t:g} mm {p['material']} plate, {ne} elements, tied at {ties} nodes {how}; "
                 f"density scaled to its {float(p['mass_kg']):.2f} kg (steel would give "
                 f"{float(mat['density_kg_m3']) * area * t * 1e-9:.2f} kg)")
    return {"elements": (lo, lo + ne), "ties": ties, "t_mm": t, "how": how}


def build_whole(design: dict, adds, element_size_mm: float = ELEMENT_SIZE_MM, weld_strips: bool = True,
                tyre_stiffness_N_per_mm: float | None = None) -> WholeFrame:
    """The whole-vehicle shell model with the given additions (module notes), held 3-2-1 and factorised."""
    from hiraku.designgen.kit.arms import ARM_D_MM, ARM_T_MM

    from ..k0_handcalc.vehicle import mass_summary
    from ..k2_vehicle.model import _bushings, hardpoints_of
    from . import linkage as lk
    from .modal import arm_material, corner_parts, rigid_point_rows, tyre_rate_of

    bad = set(adds) - set(ADDS)
    if bad:
        raise ValueError(f"unknown additions {sorted(bad)}; known: {', '.join(ADDS)}")
    adds = tuple(a for a in ADDS if a in set(adds))
    t0 = time.perf_counter()
    ms = build_mesh(design, element_size_mm, weld_strips)
    model = ShellModel.from_mesh(ms, design)
    n_mesh = model.n_nodes
    X0 = np.array(model.nodes)
    mo = {int(s): int(m) for s, m in model.links}
    nodes_tab = node_table(design)
    sup = design.get("supports", [])
    support_node = {s["id"]: s["frame_node"] for s in sup}
    notes: list[str] = []
    probe = SimpleNamespace(mesh=ms)
    sets = mount_node_sets(design, probe)
    rigid_of = {m: ("damper_mounts" if m == "damper_upper" else "brackets") for m in FRAME_MOUNTS}
    attach: dict = {}
    extra = []  # (rows, cols, vals) of the added stiffness
    fixed_extra = []

    def add_K(dofs, B):
        dofs = np.asarray(dofs, int)
        extra.append((np.repeat(dofs, len(dofs)), np.tile(dofs, len(dofs)), np.asarray(B, float).ravel()))

    # ---- panels first (their nodes are mesh-like: they carry mass), then the mount points
    panels, extra_parts = {}, set()
    if "panels" in adds:
        for p in _panel_parts(design):
            rec = _add_panel(model, ms, design, p, element_size_mm, mo, n_mesh, notes)
            if rec is not None:
                panels[p["id"]] = rec
                extra_parts.add(p["id"])
        if not _panel_parts(design):
            notes.append("panels: the design has no part with a thickness")
    for (sid, m), (nodes, where) in sets.items():
        rigid = rigid_of[m] in adds
        if not rigid and "arms" not in adds:
            continue  # today's path (the force on those nodes)
        x = np.asarray(design_hp(design, sid)[m], float)
        node = model.add_node(x)
        if rigid:
            tie_rigid(model, mo, node, nodes)
            attach[(sid, m)] = Attach(sid, m, node, "rbe2", nodes, where)
        else:
            P = rigid_point_rows(X0, nodes, x)  # (3, 3n)
            C = np.hstack([np.eye(3), -P])
            dofs = np.concatenate([dofs_of(node, "xyz"), (6 * nodes[:, None] + np.arange(3)[None, :]).ravel()])
            add_K(dofs, RBE3_STIFFNESS * C.T @ C)
            fixed_extra.append(dofs_of(node, "r"))
            attach[(sid, m)] = Attach(sid, m, node, "rbe3", nodes, where)

    # ---- the suspension
    corners, frame_springs = {}, []
    if "arms" in adds:
        tyre_k, tyre_from = tyre_rate_of(design, tyre_stiffness_N_per_mm)
        mat, mat_from = arm_material(design)
        notes += [f"tyre {tyre_k:g} N/mm from {tyre_from}", f"arms of {mat_from}"]
        frame_side = {a.node: (a.sid, a.mount) for a in attach.values()}
        asm = _RecAsm(model.add_node, frame_side, frame_springs)
        for a in attach.values():
            asm.xyz[a.node] = np.asarray(model.nodes[a.node], float)
            asm.names[a.node] = f"mount:{a.sid}.{a.mount}"
        items = mass_summary(design)["items"]
        skip = shell_parts(design, ms)
        for s in sup:
            sid = s["id"]
            susp = s.get("suspension") or {}
            if not (susp.get("type") in ("spring", "kc_table") and susp.get("stiffness_N_per_mm")):
                raise DesignError(f"support {sid} has no suspension spring: the linkage needs one")
            hp = hardpoints_of(design, sid)
            bush = _bushings(design, susp, notes)
            bush = {"radial": bush.radial / 1000.0, "axial": bush.axial / 1000.0, "tie_rod": bush.tie_rod / 1000.0}
            its = [it for it in items if it.carrier == "support" and it.carried_by[0] == sid
                   and it.id.split(":", 1)[-1] not in skip]
            parts = corner_parts(design, sid, its, hp, mat)
            k_w = float(susp["stiffness_N_per_mm"])
            k_s, ratio, comp = lk.fit_spring_rate(sid, hp, float(s["radius_mm"]), parts, mat, ARM_D_MM, ARM_T_MM,
                                                  bush, k_w)
            fn = s["frame_node"]
            any_mesh = int(np.asarray(ms.mounts.get(fn, ms.joints[fn]), int)[0])

            def carrier(mount, sid=sid, any_mesh=any_mesh):
                if mount == "frame_node":  # only the half masses of other parts (not used: no masses here)
                    asm.xyz.setdefault(any_mesh, np.asarray(model.nodes[any_mesh], float))
                    return any_mesh, asm.xyz[any_mesh]
                a = attach[(sid, mount)]
                return a.node, asm.xyz[a.node]

            info = lk.build_corner(asm, sid, hp, float(s["radius_mm"]), carrier, parts, mat, ARM_D_MM, ARM_T_MM,
                                   bush, 0.0, k_s, k_w)
            info.motion_ratio = ratio
            wc = np.asarray(hp["wheel_center"], float)
            contact = wc - np.array([0.0, 0.0, float(s["radius_mm"])])
            g = model.add_node(contact)
            asm.xyz[g] = contact
            asm.names[g] = f"{sid}.ground"
            # tyre: vertical spring from the contact point (rigid with the body) to the ground node
            asm.spring("tyre", info.body, contact - asm.xyz[info.body], g, np.zeros(3),
                       tyre_k * np.outer([0, 0, 1.0], [0, 0, 1.0]))
            fixed_extra.append(np.concatenate([dofs_of(g, "xy"), dofs_of(g, "r")]))  # only its z is loaded
            corners[sid] = {"info": info, "ground": g, "wc": wc, "contact": contact, "motion_ratio": ratio,
                            "spring_rate": k_s, "wheel_rate": k_w, "linkage_compliance": comp, "bushings": bush,
                            "tyre_N_per_mm": tyre_k}
        Kl, _ = asm.matrices(model.n_dof)
        for c in lk.CATEGORIES:
            Kc = Kl[c].tocoo()
            if Kc.nnz:
                extra.append((Kc.row, Kc.col, Kc.data))

    # ---- seats and battery as rigid bodies
    part_master = {}
    if "seats" in adds:
        mass_items = {it.id.split(":", 1)[-1]: it for it in mass_summary(design)["items"]}
        groups = []
        parts = design.get("parts", [])
        for p in parts:
            if str(p.get("kind", "")).lower() in SEAT_KINDS and p.get("carried_by"):
                cb = tuple(p["carried_by"])
                ids = [p["id"]] + [q["id"] for q in parts if str(q.get("kind", "")).lower() == "occupant"
                                   and tuple(q.get("carried_by") or ()) == cb and p["kind"] == "seat"]
                groups.append((p["id"], cb, ids))
        for pid, cb, ids in groups:
            its = [mass_items[i] for i in ids if i in mass_items]
            m = sum(it.mass_kg for it in its)
            cg = sum(it.mass_kg * np.asarray(it.cg_mm, float) for it in its) / m
            node = model.add_node(cg)
            top = []
            for n in cb:
                j = np.asarray(ms.joints[n], int)
                z = X0[j, 2]
                top.append(j[z >= z.max() - 1.0])
            tie_rigid(model, mo, node, np.concatenate(top))
            for i in ids:
                part_master[i] = node
            notes.append(f"{pid} ({', '.join(ids[1:]) + ' on it, ' if len(ids) > 1 else ''}{m:.1f} kg) rigid on the "
                         f"top walls of {', '.join(cb)} ({sum(len(t) for t in top)} nodes)")
    out_timing = time.perf_counter() - t0

    # ---- holds (as ShellFrame.build, on nodes that follow no other node)
    held = _hold_supports(sup)
    probe_links = SimpleNamespace(joints=ms.joints, links=list(model.links), nodes=model.nodes)
    hold_nodes = [hold_node(probe_links, support_node[h], nodes_tab[support_node[h]]) for h in held]
    a, b, c = hold_nodes
    fixed = np.concatenate([dofs_of(a, "xyz"), dofs_of(b, "yz"), dofs_of(c, "z")] + fixed_extra)
    K = model.stiffness()
    if extra:
        A = sp.coo_matrix((np.concatenate([e[2] for e in extra]),
                           (np.concatenate([e[0] for e in extra]), np.concatenate([e[1] for e in extra]))),
                          shape=(model.n_dof, model.n_dof)).tocsr()
        K = ((K + A + (K + A).T) * 0.5).tocsr()
    model._K = K
    masses = model.mass_diagonal()[0::6] * 1000.0
    excluded = np.zeros(ms.n_elements, bool)
    for fn in set(support_node.values()):
        excluded[ms.mount_elements.get(fn, [])] = True
    fr = WholeFrame(design, ms, model, masses, held, hold_nodes, fixed, support_node, nodes_tab, excluded=excluded,
                    adds=adds, attach=attach, corners=corners, frame_springs=frame_springs, part_master=part_master,
                    extra_shell_parts=extra_parts, panels=panels, n_mesh_nodes=n_mesh, notes=notes)
    fr.timings["mesh_s"] = out_timing
    t1 = time.perf_counter()
    fr.solver()
    fr.timings["factor_s"] = time.perf_counter() - t1
    return fr


def design_hp(design: dict, sid: str) -> dict:
    s = next(s for s in design["supports"] if s["id"] == sid)
    return (s.get("suspension") or {}).get("hardpoints_mm") or {}


def corner_load(ml: dict, wc) -> tuple[np.ndarray, np.ndarray]:
    """K2's knuckle load of a corner from its mount forces (the force the body receives at each mount):
    (force N, moment N mm about the wheel centre wc)."""
    wc = np.asarray(wc, float)
    F, M = np.zeros(3), np.zeros(3)
    for _, p, f in ml["vectors"]:
        f = np.asarray(f, float)
        F += f
        M += np.cross(np.asarray(p, float) - wc, f)
    return F, M


def corner_forces(fr: WholeFrame, mount_loads: dict, n_dof: int) -> np.ndarray:
    """The load vector of the corners: each corner's knuckle load (corner_load), its vertical part on the ground
    node (through the tyre spring), the rest on the rigid body."""
    Fv = np.zeros(n_dof)
    X = fr.model.nodes
    for sid, c in fr.corners.items():
        F, M = corner_load(mount_loads[sid], c["wc"])
        b = c["info"].body
        xb = X[b]
        Fz = np.array([0.0, 0.0, F[2]])
        Fv[6 * c["ground"] + 2] += F[2]
        rest = F - Fz
        Mb = M + np.cross(c["wc"] - xb, F) - np.cross(c["contact"] - xb, Fz)
        Fv[6 * b:6 * b + 3] += rest
        Fv[6 * b + 3:6 * b + 6] += Mb
    return Fv


def linkage_mounts(fr: WholeFrame, U: np.ndarray) -> dict:
    """{sid: {mount: force (3,)}}: the force the body receives at each mount out of the linkage's springs."""
    from .linkage import offset_rows

    u6 = np.asarray(U).reshape(-1, 6)
    out: dict = {}
    for sid, mount, a, da, b, db, Ks in fr.frame_springs:
        d = offset_rows(da) @ u6[a] - offset_rows(db) @ u6[b]
        out.setdefault(sid, {}).setdefault(mount, np.zeros(3))
        out[sid][mount] = out[sid][mount] + Ks @ d
    return out


__all__ = ["ADDS", "Attach", "WholeFrame", "build_whole", "corner_forces", "corner_load", "linkage_mounts",
           "mount_node_sets", "tie_rigid"]

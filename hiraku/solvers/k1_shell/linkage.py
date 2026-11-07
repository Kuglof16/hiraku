"""The double-wishbone suspension of a corner as a small FE model, for the shell modal model's
suspension = "linkage" (k1_shell.modal.build_modal). Units N, mm, tonne, s.

Per support with double-wishbone hard points (k2_vehicle.model.hardpoints_of: the design's
suspension.hardpoints_mm, or K2's builtin layout):

- the lower arm (LCA): two legs, lca_front -> lca_outer and lca_rear -> lca_outer, and the cross
  tube between them at the side position of damper_lower (arm_parts.cross_ends), with a node at
  damper_lower; the upper arm (UCA): uca_front -> uca_outer and uca_rear -> uca_outer; the tie rod
  (toe link at the rear) tie_inner -> tie_outer. All are 3D Euler-Bernoulli beams of round tube
  d x t read from the design's arm parts (arm_parts.arm_section: the box across, the wall from the
  mass; else the generator's default, an assumption, not checked), of the design's steel
  (arm_material), N_SEG elements per leg. Their mass is the design part's mass (kit/arms gives
  the tubes rho A L, the tie rod part its own box mass), lumped on the element nodes in
  proportion to length; a node's rotary inertia is (m/2) L^2 / 12 about the two bending axes
  and (m/2) r_g^2 about the tube's axis (lumped, no source needed: it only has to be positive);
- each arm's inner pivot is joined to the frame at the bracket's frame node (see carriers) by its
  bushing: translation springs, radial across the pivot axis and axial along it (K2's bushing
  stiffnesses, k2_vehicle.model._bushings: suspension.bushings, the design variables, else
  K2's defaults 3 / 0.5 kN/mm), rotation free: a pin about the pivot axis with an elastic pin.
  The bushings' own torsional rate is left out (K2 has none);
- the knuckle, wheel, tyre, hub and brake (the parts of k2_vehicle.model.UNSPRUNG_KINDS and
  brake calipers carried by the support) are one rigid body: a node at their combined centre of
  mass with their total mass and inertia tensor (each part's own inertia plus the parallel axis
  term); it is joined to the arms' outer ends and the tie rod's outer end by ball joints: stiff
  translation springs JOINT_STIFFNESS (a penalty, rotations free);
- the tie rod's inner end is a ball joint on the frame: across the rod stiff, along it the spring
  that puts the rod in series to K2's tie rod rate (tie_rod_N_per_mm); a rod between two ball
  joints spins freely about its own axis, so that spin is held by a torsional spring
  SPIN_STIFFNESS on its inner end (the rack: no steering freedom is left);
- the coil spring as one axial spring from damper_lower (on the lower arm) to damper_upper (the
  frame): its rate k_s is set so the corner's wheel rate (vertical force over vertical motion at
  the wheel centre, frame held, tyre off) is K2's wheel rate stiffness_N_per_mm (wheel_rate). The
  damper adds no stiffness: an undamped eigen analysis has no damping;
- the tyre: a vertical spring tyre_stiffness from the contact point (wheel centre - radius in z)
  to the ground. K2 has no static lateral or fore-aft tyre stiffness (its cornering and slip
  stiffnesses act on slip, not on a displacement), so the tyre is free sideways and fore-aft and
  the vehicle has three rigid modes at 0 Hz (fore-aft, sideways, yaw);
- the ball joints' masses on the arms' outer nodes, the damper's half on damper_lower and half on
  damper_upper (a two-force member), any other part carried by the support (a driveshaft) half on
  the rigid body and half on the frame at the support's frame node.

The frame side (carriers): each mount point names the frame's design nodes that hold it
(k1_frame.analyze.mount_carriers: a node at the point, else the bracket part's carried_by; else
the support's frame_node). In the shell those are rigid bracket nodes (RBE2 over the joint's
wall nodes, or the wheel mount's for a support's frame node: k1_shell.modal); the pivot is a
point rigidly offset from that bracket node.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import spsolve

from ..k0_handcalc.vehicle import DesignError

N_SEG = 4  # beam elements per arm leg and per tie rod (HIRAKU's choice)
JOINT_STIFFNESS = 1.0e6  # N/mm: ball joints (a penalty, about 10 x an arm leg's axial EA/L; HIRAKU's choice)
SPIN_STIFFNESS = 1.0e5  # N mm/rad: holds a tie rod's spin about its own axis (penalty, HIRAKU's choice)
ARM_FALLBACK = {"E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 7850.0}
RIGID_KINDS = {"wheel", "tire", "tyre", "hub", "upright", "knuckle", "brake", "brake_caliper", "brake_disc"}
CATEGORIES = ("beam", "bushing", "joint", "spring", "tyre")


def cross(v) -> np.ndarray:
    x, y, z = v
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def offset_rows(d) -> np.ndarray:
    """R (3, 6): the translation of a point at offset d from a node, rigid with it: u + theta x d."""
    return np.hstack([np.eye(3), -cross(d)])


def unit(v) -> np.ndarray:
    v = np.asarray(v, float)
    n = float(np.linalg.norm(v))
    if n <= 0.0:
        raise DesignError("two suspension hard points coincide")
    return v / n


def tube_props(d_mm: float, t_mm: float) -> dict:
    di = d_mm - 2.0 * t_mm
    A = math.pi / 4.0 * (d_mm ** 2 - di ** 2)
    I = math.pi / 64.0 * (d_mm ** 4 - di ** 4)
    return {"A": A, "I": I, "J": 2.0 * I, "rg2": (d_mm ** 2 + di ** 2) / 8.0}


def beam_stiffness(xa, xb, E, G, A, I, J) -> np.ndarray:
    """12 x 12 global stiffness of a 3D Euler-Bernoulli beam (equal I about both bending axes)."""
    xa, xb = np.asarray(xa, float), np.asarray(xb, float)
    L = float(np.linalg.norm(xb - xa))
    ex = (xb - xa) / L
    ref = np.array([0.0, 0.0, 1.0]) if abs(ex[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    ey = unit(np.cross(ref, ex))
    ez = np.cross(ex, ey)
    lam = np.vstack([ex, ey, ez])
    k = np.zeros((12, 12))
    a, t = E * A / L, G * J / L
    for i, j, v in ((0, 0, a), (0, 6, -a), (6, 6, a), (3, 3, t), (3, 9, -t), (9, 9, t)):
        k[i, j] = k[j, i] = v
    b1, b2, b3, b4 = 12 * E * I / L ** 3, 6 * E * I / L ** 2, 4 * E * I / L, 2 * E * I / L
    # bending in the local x-y plane (v, theta_z) and x-z plane (w, theta_y)
    for (v1, r1, v2, r2, s) in ((1, 5, 7, 11, 1.0), (2, 4, 8, 10, -1.0)):
        ent = {(v1, v1): b1, (v1, r1): s * b2, (v1, v2): -b1, (v1, r2): s * b2, (r1, r1): b3, (r1, v2): -s * b2,
               (r1, r2): b4, (v2, v2): b1, (v2, r2): -s * b2, (r2, r2): b3}
        for (i, j), v in ent.items():
            k[i, j] = k[j, i] = v
    T = np.kron(np.eye(4), lam)
    return T.T @ k @ T


@dataclass
class Assembly:
    """Nodes and the K / M triplets of the linkage, on global node numbers given by add_node."""

    add_node: object  # callable xyz -> node index
    names: dict = field(default_factory=dict)  # node index -> name
    xyz: dict = field(default_factory=dict)  # node index -> coordinates
    K: dict = field(default_factory=lambda: {c: ([], [], []) for c in CATEGORIES})
    M: tuple = field(default_factory=lambda: ([], [], []))
    lines: list = field(default_factory=list)  # (point name, point name, element name)
    points: dict = field(default_factory=dict)  # point name -> (node, offset): drawn points rigid with a node

    def node(self, xyz, name: str) -> int:
        i = int(self.add_node(np.asarray(xyz, float)))
        self.names[i] = name
        self.xyz[i] = np.asarray(xyz, float)
        self.points[name] = (i, np.zeros(3))
        return i

    def point(self, name: str, node: int, at) -> str:
        self.points[name] = (int(node), np.asarray(at, float) - self.xyz[node])
        return name

    @staticmethod
    def _put(store, dofs, B):
        dofs = np.asarray(dofs, int)
        store[0].append(np.repeat(dofs, len(dofs)))
        store[1].append(np.tile(dofs, len(dofs)))
        store[2].append(np.asarray(B, float).ravel())

    def stiff(self, cat: str, dofs, B):
        self._put(self.K[cat], dofs, B)

    def mass(self, dofs, B):
        self._put(self.M, dofs, B)

    def point_mass(self, node: int, m: float, d=(0.0, 0.0, 0.0)):
        """m (tonne) at offset d from the node, rigid with it."""
        R = offset_rows(d)
        self.mass(dof6(node), m * R.T @ R)

    def spring(self, cat: str, a: int, da, b: int | None, db, Ks):
        """Translation spring Ks (3 x 3) between the point at offset da of node a and the point at offset db of
        node b (None: the ground)."""
        Ra = offset_rows(da)
        if b is None:
            self.stiff(cat, dof6(a), Ra.T @ Ks @ Ra)
            return
        C = np.hstack([Ra, -offset_rows(db)])
        self.stiff(cat, np.concatenate([dof6(a), dof6(b)]), C.T @ Ks @ C)

    def rot_spring(self, cat: str, a: int, b: int | None, axis, k: float):
        e = unit(axis)
        C = np.concatenate([np.zeros(3), e]) if b is None else np.concatenate([np.zeros(3), e, np.zeros(3), -e])
        dofs = dof6(a) if b is None else np.concatenate([dof6(a), dof6(b)])
        self.stiff(cat, dofs, k * np.outer(C, C))

    def matrices(self, n_dof: int) -> tuple[dict, sp.csr_matrix]:
        def build(store):
            if not store[0]:
                return sp.csr_matrix((n_dof, n_dof))
            return sp.coo_matrix((np.concatenate(store[2]), (np.concatenate(store[0]), np.concatenate(store[1]))),
                                 shape=(n_dof, n_dof)).tocsr()

        return {c: build(s) for c, s in self.K.items()}, build(self.M)


def dof6(i: int) -> np.ndarray:
    return 6 * int(i) + np.arange(6)


@dataclass
class CornerParts:
    """The masses of a corner (kg, mm, kg mm^2): the rigid body's items [(m, cg, I)], the arms' tubes by role
    (lca_f, lca_r, lca_x, uca_f, uca_r), the tie rod, the ball joints, the damper, and the rest [(m, cg, I)]
    (half on the rigid body, half on the frame)."""

    rigid: list
    tubes: dict
    tie_rod_kg: float
    bj_lower_kg: float
    bj_upper_kg: float
    damper_kg: float
    other: list = field(default_factory=list)
    ids: dict = field(default_factory=dict)  # role -> part ids, for the record


@dataclass
class CornerInfo:
    sid: str
    body: int  # the rigid body's node (knuckle + wheel)
    body_mass_kg: float
    body_inertia_kg_mm2: np.ndarray
    spring_rate: float  # N/mm along the damper axis
    wheel_rate: float  # N/mm K2's, which spring_rate gives
    motion_ratio: float  # d(spring length) / d(wheel travel), from the fitted rates
    tyre_rate: float
    nodes: list  # the corner's own nodes (beams, damper_lower)
    contact: np.ndarray
    radius: float


def rigid_body(items: list) -> tuple[float, np.ndarray, np.ndarray]:
    """(mass kg, cg mm, inertia about cg kg mm^2) of rigid items [(m, cg, I or None)]."""
    m = sum(float(it[0]) for it in items)
    if m <= 0.0:
        raise DesignError("a suspended corner has no wheel or knuckle parts to make its rigid body from")
    cg = sum(float(it[0]) * np.asarray(it[1], float) for it in items) / m
    J = np.zeros((3, 3))
    for mi, ci, Ii in items:
        r = np.asarray(ci, float) - cg
        J += (np.zeros((3, 3)) if Ii is None else np.asarray(Ii, float)) + float(mi) * (float(r @ r) * np.eye(3)
                                                                                         - np.outer(r, r))
    return m, cg, J


def add_beam_chain(asm: Assembly, a: int, b: int, n_seg: int, mass_kg: float, mat: dict, props: dict,
                   name: str) -> list[int]:
    """Beam elements from node a to node b (n_seg of them, inner nodes added); the mass lumped on the nodes."""
    xa, xb = asm.xyz[a], asm.xyz[b]
    ids = [a] + [asm.node(xa + (xb - xa) * k / n_seg, f"{name}.{k}") for k in range(1, n_seg)] + [b]
    L = float(np.linalg.norm(xb - xa))
    E, G = mat["E_MPa"], mat["E_MPa"] / (2.0 * (1.0 + mat["nu"]))
    m_seg = mass_kg / 1000.0 / n_seg
    ex = (xb - xa) / L
    for i, j in zip(ids[:-1], ids[1:]):
        asm.stiff("beam", np.concatenate([dof6(i), dof6(j)]),
                  beam_stiffness(asm.xyz[i], asm.xyz[j], E, G, props["A"], props["I"], props["J"]))
        le = L / n_seg
        J_bend = (m_seg / 2.0) * le ** 2 / 12.0
        J_ax = (m_seg / 2.0) * props["rg2"]
        Jr = J_bend * (np.eye(3) - np.outer(ex, ex)) + J_ax * np.outer(ex, ex)
        for nd in (i, j):
            B = np.zeros((6, 6))
            B[:3, :3] = (m_seg / 2.0) * np.eye(3)
            B[3:, 3:] = Jr
            asm.mass(dof6(nd), B)
        asm.lines.append((asm.names[i], asm.names[j], name))
    return ids


def build_corner(asm: Assembly, sid: str, hp: dict, radius: float, carrier, parts: CornerParts, mat: dict,
                 arm_d: float, arm_t: float, bushings: dict, tyre_rate: float, spring_rate: float,
                 wheel_rate: float = 0.0, other_carrier=None) -> CornerInfo:
    """One corner into asm. carrier(mount) -> (frame node index, its coordinates): the rigid bracket node that
    holds the mount point. bushings: radial / axial / tie rod N/mm. spring_rate along the damper axis (N/mm)."""
    p = {k: np.asarray(v, float) for k, v in hp.items() if isinstance(v, (list, tuple, np.ndarray))}
    props = tube_props(arm_d, arm_t)
    n = {}
    for k in ("lca_front", "lca_rear", "lca_outer", "uca_front", "uca_rear", "uca_outer", "tie_inner", "tie_outer",
              "damper_lower"):
        n[k] = asm.node(p[k], f"{sid}.{k}")
    # lower arm's cross tube ends on the legs at damper_lower's side position (the generator's rule, arm_parts)
    from .arm_parts import cross_ends

    cf, cr = cross_ends({k: p[k] for k in ("lca_front", "lca_rear", "lca_outer", "damper_lower")})
    n["cross_f"], n["cross_r"] = asm.node(cf, f"{sid}.lca_cross_f"), asm.node(cr, f"{sid}.lca_cross_r")
    lf_len = float(np.linalg.norm(p["lca_outer"] - p["lca_front"]))
    lr_len = float(np.linalg.norm(p["lca_outer"] - p["lca_rear"]))
    t = parts.tubes
    own = []
    half = max(1, N_SEG // 2)
    for a, b, m, nm in (
            ("lca_front", "cross_f", t["lca_f"] * np.linalg.norm(cf - p["lca_front"]) / lf_len, "lca_f.in"),
            ("cross_f", "lca_outer", t["lca_f"] * np.linalg.norm(p["lca_outer"] - cf) / lf_len, "lca_f.out"),
            ("lca_rear", "cross_r", t["lca_r"] * np.linalg.norm(cr - p["lca_rear"]) / lr_len, "lca_r.in"),
            ("cross_r", "lca_outer", t["lca_r"] * np.linalg.norm(p["lca_outer"] - cr) / lr_len, "lca_r.out")):
        own += add_beam_chain(asm, n[a], n[b], half, float(m), mat, props, f"{sid}.{nm}")
    xl = float(np.linalg.norm(cr - cf))
    dl_f = float(np.linalg.norm(p["damper_lower"] - cf))
    if abs(dl_f + float(np.linalg.norm(cr - p["damper_lower"])) - xl) > 1e-3 * max(xl, 1.0):
        raise DesignError(f"support {sid}: damper_lower is not on the lower arm's cross tube")
    own += add_beam_chain(asm, n["cross_f"], n["damper_lower"], 1, t["lca_x"] * dl_f / xl, mat, props,
                          f"{sid}.lca_x.f")
    own += add_beam_chain(asm, n["damper_lower"], n["cross_r"], 1, t["lca_x"] * (1 - dl_f / xl), mat, props,
                          f"{sid}.lca_x.r")
    own += add_beam_chain(asm, n["uca_front"], n["uca_outer"], N_SEG, t["uca_f"], mat, props, f"{sid}.uca_f")
    own += add_beam_chain(asm, n["uca_rear"], n["uca_outer"], N_SEG, t["uca_r"], mat, props, f"{sid}.uca_r")
    own += add_beam_chain(asm, n["tie_inner"], n["tie_outer"], N_SEG, parts.tie_rod_kg, mat, props, f"{sid}.tie_rod")
    # bushings at the arms' inner pivots: radial and axial translation springs, rotation free
    kr, ka, kt_rod = bushings["radial"], bushings["axial"], bushings["tie_rod"]
    for arm, f, r in (("lca", "lca_front", "lca_rear"), ("uca", "uca_front", "uca_rear")):
        e = unit(p[f] - p[r])
        Kb = kr * (np.eye(3) - np.outer(e, e)) + ka * np.outer(e, e)
        for k in (f, r):
            m_i, x_i = carrier(k)
            asm.spring("bushing", n[k], np.zeros(3), m_i, p[k] - x_i, Kb)
            asm.lines.append((asm.names[n[k]], asm.point(f"{sid}.frame.{k}", m_i, p[k]), f"{sid}.bushing"))
    # tie rod: inner ball joint stiff across, along the rod the joint in series with the rod to K2's rate
    e_t = unit(p["tie_outer"] - p["tie_inner"])
    L_t = float(np.linalg.norm(p["tie_outer"] - p["tie_inner"]))
    rod = mat["E_MPa"] * props["A"] / L_t
    if kt_rod >= rod:
        raise DesignError(f"support {sid}: K2's tie rod rate {kt_rod:g} N/mm is stiffer than the rod itself "
                          f"({rod:.0f} N/mm)")
    k_join = 1.0 / (1.0 / kt_rod - 1.0 / rod)
    m_t, x_t = carrier("tie_inner")
    asm.spring("joint", n["tie_inner"], np.zeros(3), m_t, p["tie_inner"] - x_t,
               JOINT_STIFFNESS * (np.eye(3) - np.outer(e_t, e_t)) + k_join * np.outer(e_t, e_t))
    asm.rot_spring("joint", n["tie_inner"], m_t, e_t, SPIN_STIFFNESS)
    asm.lines.append((asm.names[n["tie_inner"]], asm.point(f"{sid}.frame.tie_inner", m_t, p["tie_inner"]),
                      f"{sid}.bushing"))
    # the rigid body (knuckle, wheel, brake ...) and the half of the other parts
    rigid = list(parts.rigid) + [(0.5 * m, c, None if I is None else 0.5 * np.asarray(I)) for m, c, I in parts.other]
    mb, cg, Jb = rigid_body(rigid)
    body = asm.node(cg, f"{sid}.wheel_body")
    B = np.zeros((6, 6))
    B[:3, :3] = mb / 1000.0 * np.eye(3)
    B[3:, 3:] = Jb / 1000.0
    asm.mass(dof6(body), B)
    wc = asm.point(f"{sid}.wheel_center", body, p["wheel_center"])
    for k in ("lca_outer", "uca_outer", "tie_outer"):
        asm.spring("joint", n[k], np.zeros(3), body, p[k] - cg, JOINT_STIFFNESS * np.eye(3))
        asm.lines.append((asm.names[n[k]], wc, f"{sid}.knuckle"))
    # spring (coil over the damper) along the damper axis
    e_d = unit(p["damper_upper"] - p["damper_lower"])
    m_d, x_d = carrier("damper_upper")
    asm.spring("spring", n["damper_lower"], np.zeros(3), m_d, p["damper_upper"] - x_d, spring_rate * np.outer(e_d, e_d))
    asm.lines.append((asm.names[n["damper_lower"]], asm.point(f"{sid}.frame.damper_upper", m_d, p["damper_upper"]),
                      f"{sid}.damper"))
    # tyre: vertical spring at the contact point
    contact = p["wheel_center"] - np.array([0.0, 0.0, radius])
    asm.point(f"{sid}.contact", body, contact)
    asm.lines.append((f"{sid}.wheel_center", f"{sid}.contact", f"{sid}.wheel"))
    if tyre_rate > 0.0:
        asm.spring("tyre", body, contact - cg, None, None, tyre_rate * np.outer([0, 0, 1.0], [0, 0, 1.0]))
    # point masses
    asm.point_mass(n["lca_outer"], parts.bj_lower_kg / 1000.0)
    asm.point_mass(n["uca_outer"], parts.bj_upper_kg / 1000.0)
    asm.point_mass(n["damper_lower"], 0.5 * parts.damper_kg / 1000.0)
    asm.point_mass(m_d, 0.5 * parts.damper_kg / 1000.0, p["damper_upper"] - x_d)
    if parts.other:
        m_o, x_o = (other_carrier or carrier)("frame_node")
        for m, c, _ in parts.other:
            asm.point_mass(m_o, 0.5 * m / 1000.0, np.asarray(c, float) - x_o)
    nodes = sorted(set(own) | {n[k] for k in n})
    return CornerInfo(sid, body, mb, Jb, spring_rate, wheel_rate, 0.0, tyre_rate, nodes, contact, radius)


def corner_rate(sid: str, hp: dict, radius: float, parts: CornerParts, mat: dict, arm_d: float, arm_t: float,
                bushings: dict, spring_rate: float) -> float:
    """Vertical rate (N/mm) at the wheel centre of the corner alone: frame held (one fixed frame node holding
    every mount), tyre off, spring_rate along the damper axis."""
    xyz = []

    def add(x):
        xyz.append(np.asarray(x, float))
        return len(xyz) - 1

    asm = Assembly(add)
    frame = asm.node(np.asarray(hp["wheel_center"], float), "frame")

    def carrier(_mount):
        return frame, asm.xyz[frame]

    info = build_corner(asm, sid, hp, radius, carrier, parts, mat, arm_d, arm_t, bushings, 0.0, spring_rate)
    n_dof = 6 * len(xyz)
    Kc, _ = asm.matrices(n_dof)
    K = sum(Kc.values()).tocsr()
    free = np.setdiff1d(np.arange(n_dof), dof6(frame))
    f = np.zeros(n_dof)
    wc = np.asarray(hp["wheel_center"], float)
    R = offset_rows(wc - asm.xyz[info.body])
    f[dof6(info.body)] = R.T @ np.array([0.0, 0.0, 1.0])
    Kf = K[free][:, free].tocsc()
    u = np.zeros(n_dof)
    u[free] = spsolve(Kf, f[free])
    w = float((R @ u[dof6(info.body)])[2])
    if not w > 0.0:
        raise DesignError(f"support {sid}: the linkage does not hold the wheel (vertical compliance {w:g})")
    return 1.0 / w


def fit_spring_rate(sid: str, hp: dict, radius: float, parts: CornerParts, mat: dict, arm_d: float, arm_t: float,
                    bushings: dict, wheel_rate: float) -> tuple[float, float, float]:
    """The spring rate along the damper axis that gives the wheel rate (N/mm), from two corner rates:
    1 / k_w = 1 / (k_s r^2) + c (r the motion ratio, c the linkage's own compliance). Returns (k_s, r, c)."""
    a1, a2 = 10.0, 100.0
    c1 = 1.0 / corner_rate(sid, hp, radius, parts, mat, arm_d, arm_t, bushings, a1)
    c2 = 1.0 / corner_rate(sid, hp, radius, parts, mat, arm_d, arm_t, bushings, a2)
    inv_r2 = (c1 - c2) / (1.0 / a1 - 1.0 / a2)
    c = c1 - inv_r2 / a1
    target = 1.0 / wheel_rate - c
    if not (inv_r2 > 0.0 and target > 0.0):
        raise DesignError(f"support {sid}: the linkage cannot give the wheel rate {wheel_rate:g} N/mm")
    k_s = inv_r2 / target
    return k_s, 1.0 / math.sqrt(inv_r2), c


__all__ = ["Assembly", "CATEGORIES", "CornerInfo", "CornerParts", "JOINT_STIFFNESS", "N_SEG", "SPIN_STIFFNESS",
           "beam_stiffness", "build_corner", "corner_rate", "fit_spring_rate", "offset_rows", "rigid_body",
           "tube_props"]

"""K1: a 3D beam model of the design's frame, solved with PyNite 3.2.

Units through PyNite: N, mm, MPa, tonne (density in t/mm^3, g in mm/s^2).

Axes: PyNite treats global Y as "up" when it orients member local axes
(a horizontal member gets local y = global Y). The design uses ISO 8855
(z up), so coordinates are rotated on the way in and out:
    PyNite (X, Y, Z) = (x, z, -y)        design (x, y, z) = (X, -Z, Y)
With this, a horizontal member's local y points up and local z sideways, so
section `h` is the vertical size and a vertical load bends about local z (Iz).
tests/solvers/test_k1_frame.py pins this down.

Rules (from the solver notes): mass only as density * self-weight and node
loads; at least 4 elements per member; frequencies read from
model.frequencies; stop on NaN; shear deformation is not modelled; stresses
are computed here at the section corners, not taken from PyNite.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from Pynite import FEModel3D

from ..k0_handcalc import sections
from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import DesignError, MassItem, member_props

G_MM_S2 = STANDARD_GRAVITY * 1000.0
R_DP = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])  # design -> PyNite
CASE = "load"
COMBO = "combo"
N_SUB = 4  # elements per member (rule: at least 4 per span)
LINK_STIFFNESS_FACTOR = 100.0  # 1000 made soft suspension springs fail PyNite's residual check

_P_DOF = {"x": "X", "y": "Z", "z": "Y"}  # design axis -> PyNite axis


def to_pynite(v) -> np.ndarray:
    return R_DP @ np.asarray(v, dtype=float)


def to_design(v) -> np.ndarray:
    return R_DP.T @ np.asarray(v, dtype=float)


def wheel_node(support_id: str) -> str:
    return f"wc:{support_id}"


@dataclass
class FrameModel:
    """A PyNite model built from a design, plus the bookkeeping to read it back."""

    design: dict
    model: FEModel3D
    props: dict[str, dict]  # member id -> section/material/length
    frame_nodes: list[str]
    wheel_nodes: dict[str, str] = field(default_factory=dict)  # support id -> node name
    links: list[str] = field(default_factory=list)
    applied: list[tuple[np.ndarray, np.ndarray, str]] = field(default_factory=list)  # (point, force, kind) design coords

    # ------------------------------------------------------------ loads

    def add_force(self, node: str, force, moment=(0.0, 0.0, 0.0)) -> None:
        f = to_pynite(force)
        m = to_pynite(moment)
        for comp, val in zip(("FX", "FY", "FZ"), f):
            if val != 0.0:
                self.model.add_node_load(node, comp, float(val), CASE)
        for comp, val in zip(("MX", "MY", "MZ"), m):
            if val != 0.0:
                self.model.add_node_load(node, comp, float(val), CASE)

    def node_xyz(self, node: str) -> np.ndarray:
        n = self.model.nodes[node]
        return to_design((n.X, n.Y, n.Z))

    def add_force_at(self, node: str, point, force) -> None:
        """Apply a force acting at `point` (design mm) to `node`, with the moment of the offset."""
        p = np.asarray(point, float)
        f = np.asarray(force, float)
        self.add_force(node, f, np.cross(p - self.node_xyz(node), f))
        self.applied.append((p, f, "load"))

    def add_gravity(self, items: list[MassItem], g_factor: float = 1.0) -> None:
        """Weight of members (PyNite self-weight) and parts (node loads with transfer moments)."""
        self.add_body_loads(items, np.array([0.0, 0.0, -STANDARD_GRAVITY * g_factor]))

    def add_body_loads(self, items: list[MassItem], accel_field, alpha=(0.0, 0.0, 0.0), center=(0.0, 0.0, 0.0)) -> None:
        """Body forces m * b on every mass, with b = accel_field + alpha x (r - center).

        accel_field (m/s^2) is uniform: gravity is (0, 0, -g), a d'Alembert
        inertia load of a vehicle accelerating at a is -a. alpha (rad/s^2) adds
        the d'Alembert load of an angular acceleration: pass -alpha_vehicle;
        parts also get the moment of their own inertia. Members take the
        uniform part as PyNite self-weight (distributed) and the rotational
        part lumped at their ends.
        """
        b0 = np.asarray(accel_field, float)
        al = np.asarray(alpha, float)
        c = np.asarray(center, float)
        for axis, comp in zip("xyz", b0):
            if comp != 0.0:
                p = to_pynite(np.eye(3)["xyz".index(axis)])  # design axis in PyNite axes
                k = int(np.argmax(np.abs(p)))
                self.model.add_member_self_weight(f"F{'XYZ'[k]}", float(np.sign(p[k]) * comp * 1000.0), CASE)
        rotating = bool(np.any(al != 0.0))
        for it in items:
            b_rot = np.cross(al, it.cg_mm - c) * 1e-3 if rotating else np.zeros(3)  # m/s^2
            f = it.mass_kg * (b0 + b_rot)
            m_own = (it.inertia_kg_mm2 @ al) * 1e-6 if rotating and it.inertia_kg_mm2 is not None else np.zeros(3)  # N*m
            m_own = m_own * 1000.0  # N*mm
            if it.carrier == "member":
                if rotating:  # only the rotational part; the uniform part is the self-weight above
                    fr = it.mass_kg * b_rot
                    mp = self.props[it.carried_by[0]]["member"]
                    for n in (mp["node_a"], mp["node_b"]):
                        self.add_force(n, fr / 2.0, m_own / 2.0)
            elif it.carrier == "support":
                node = self.wheel_nodes[it.carried_by[0]]
                self.add_force(node, f, np.cross(it.cg_mm - self.node_xyz(node), f) + m_own)
            else:
                nodes = it.carried_by
                centroid = sum(self.node_xyz(n) for n in nodes) / len(nodes)
                moment = np.cross(it.cg_mm - centroid, f) + m_own
                for n in nodes:
                    self.add_force(n, f / len(nodes), moment / len(nodes))
            self.applied.append((it.cg_mm, f, "body"))

    # ------------------------------------------------------------ supports

    def restrain(self, node: str, axes: str) -> None:
        """Fix translations of `node` along design axes, e.g. 'xyz' or 'z'."""
        n = self.model.nodes[node]
        flags = dict(DX=n.support_DX, DY=n.support_DY, DZ=n.support_DZ)
        for a in axes:
            flags["D" + _P_DOF[a]] = True
        self.model.def_support(node, flags["DX"], flags["DY"], flags["DZ"], False, False, False)

    def restrain_free_body(self) -> list[str]:
        """Six restraints that only stop rigid-body motion (for self-balanced loads)."""
        sup = self.design["supports"]
        pts = {s["id"]: np.asarray(s["position_mm"], float) for s in sup}
        a = sup[0]["id"]
        b = max(pts, key=lambda k: abs(pts[k][0] - pts[a][0]))
        rest = [k for k in pts if k not in (a, b)]
        if abs(pts[b][0] - pts[a][0]) < 1e-6 or not rest:
            raise DesignError("supports must be spread in x and y to hold the frame")
        ab = pts[b] - pts[a]
        c = max(rest, key=lambda k: np.linalg.norm(np.cross(ab, pts[k] - pts[a])))
        self.restrain(self.wheel_nodes[a], "xyz")
        self.restrain(self.wheel_nodes[b], "yz")
        self.restrain(self.wheel_nodes[c], "z")
        return [a, b, c]

    def restrain_on_wheels(self, in_plane_at_frame: bool = False) -> None:
        """Stand the frame on its supports: vertical rigid or spring, plus in-plane holds (x and y at the
        first support, y at the one farthest from it in x) at the wheel centres, or with in_plane_at_frame
        at their frame nodes. The modal analysis holds at the frame nodes: a hold below the frame turns the
        frame's twist into a fore-and-aft and sideways sway through its lever (on the demo vehicle the first
        frame mode fell from 9.7 to 8.1 Hz, with 69 % of its kinetic energy fore-and-aft)."""
        sup = self.design["supports"]
        for s in sup:
            node = self.wheel_nodes[s["id"]]
            susp = s.get("suspension", {})
            k = susp.get("stiffness_N_per_mm")
            if susp.get("type") in ("spring", "kc_table") and k:
                ground = f"gnd:{s['id']}"
                p = np.asarray(s["position_mm"], float) - np.array([0.0, 0.0, float(s.get("radius_mm", 0.0)) or 100.0])
                self.model.add_node(ground, *to_pynite(p))
                self.model.def_support(ground, True, True, True, True, True, True)
                self.model.add_spring(f"spr:{s['id']}", node, ground, float(k))
            else:
                self.restrain(node, "z")
        pts = {s["id"]: np.asarray(s["position_mm"], float) for s in sup}
        a = sup[0]["id"]
        b = max(pts, key=lambda k: abs(pts[k][0] - pts[a][0]))
        at = {s["id"]: (s["frame_node"] if in_plane_at_frame else self.wheel_nodes[s["id"]]) for s in sup}
        self.restrain(at[a], "xy")
        self.restrain(at[b], "y")

    # ------------------------------------------------------------ solve and read

    def solve(self) -> None:
        self.model.add_load_combo(COMBO, {CASE: 1.0})
        try:
            self.model.analyze_linear(check_stability=True)
        except Exception as e:  # PyNite raises bare Exception for unstable models
            raise DesignError(
                f"the frame cannot be solved ({e}). Usually a node is not connected by members, "
                f"or a member is only pinned at one end; connect every node with members"
            ) from None
        for n in self.model.nodes.values():
            vals = (n.DX[COMBO], n.DY[COMBO], n.DZ[COMBO], n.RX[COMBO], n.RY[COMBO], n.RZ[COMBO])
            if not all(math.isfinite(v) for v in vals):
                raise DesignError(f"the solver returned NaN at node {n.name}; the model is ill-conditioned (check member sizes and supports)")

    def displacement(self, node: str) -> np.ndarray:
        n = self.model.nodes[node]
        return to_design((n.DX[COMBO], n.DY[COMBO], n.DZ[COMBO]))

    def reaction(self, node: str) -> tuple[np.ndarray, np.ndarray]:
        n = self.model.nodes[node]
        f = to_design((n.RxnFX[COMBO], n.RxnFY[COMBO], n.RxnFZ[COMBO]))
        m = to_design((n.RxnMX[COMBO], n.RxnMY[COMBO], n.RxnMZ[COMBO]))
        return f, m

    def spring_force(self, support_id: str) -> np.ndarray:
        """Force from a support spring on the vehicle (design coords)."""
        ground = self.model.nodes.get(f"gnd:{support_id}")
        if ground is None:
            return np.zeros(3)
        f, _ = self.reaction(ground.name)
        return f

    def member_stress(self, member_id: str, n_points: int = 41) -> dict:
        """Largest von Mises stress along a member and where it occurs."""
        pm = self.model.members[member_id]
        props = self.props[member_id]["section"]
        N = pm.axial_array(n_points, COMBO)[1]
        My = pm.moment_array("My", n_points, COMBO)[1]
        Mz = pm.moment_array("Mz", n_points, COMBO)[1]
        T = pm.torque_array(n_points, COMBO)[1]
        x = pm.axial_array(n_points, COMBO)[0]
        vm = np.array([sections.corner_von_mises(props, N[i], My[i], Mz[i], T[i]) for i in range(len(x))])
        if not np.all(np.isfinite(vm)):
            raise DesignError(f"stress in member {member_id} is not finite")
        i = int(np.argmax(vm))
        return {"max": float(vm[i]), "x_mm": float(x[i]), "N": float(N[i]), "My": float(My[i]),
                "Mz": float(Mz[i]), "T": float(T[i])}


def stress_dims(section: dict, member_id: str, keys=("cy", "cz", "tau_per_T")) -> None:
    """Raise DesignError when a member's section (a custom one) lacks the dims its stresses need."""
    missing = [k for k in keys if section.get(k) is None]
    if missing:
        raise DesignError(f"member {member_id}'s section gives no {', '.join(missing)} (a custom section needs them in "
                          f"dims_mm for its stresses); add them")


def build(design: dict, n_sub: int = N_SUB) -> FrameModel:
    """Build the PyNite model of the frame (members subdivided, wheel links added)."""
    if n_sub < 4:
        raise ValueError("use at least 4 elements per member")
    props = member_props(design)
    if not props:
        raise DesignError("the design has no members, so there is no frame to analyse")
    model = FEModel3D()
    used = set()
    for mp in props.values():
        used.update((mp["member"]["node_a"], mp["member"]["node_b"]))
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    for nid, xyz in nodes.items():
        model.add_node(nid, *to_pynite(xyz))

    max_E = 0.0
    for name, mat in design["materials"].items():
        E = float(mat["E_MPa"])
        nu = float(mat["nu"])
        G = float(mat.get("G_MPa") or E / (2.0 * (1.0 + nu)))
        model.add_material(name, E, G, nu, float(mat["density_kg_m3"]) * 1e-12)
        max_E = max(max_E, E)

    sec_names = {}
    for mid, mp in props.items():
        s = mp["section"]
        key = f"sec:{mp['member']['section']}"
        if key not in sec_names:
            model.add_section(key, s["A"], s["Iy"], s["Iz"], s["J"])
            sec_names[key] = s
        a, b = mp["a"], mp["b"]
        for k in range(1, n_sub):
            model.add_node(f"{mid}~{k}", *to_pynite(a + (b - a) * k / n_sub))
        model.add_member(mid, mp["member"]["node_a"], mp["member"]["node_b"], mp["member"]["material"], key)

    fm = FrameModel(design, model, props, sorted(used))

    # Wheel centres joined to their frame node by stiff massless links.
    A = max(s["A"] for s in sec_names.values()) * LINK_STIFFNESS_FACTOR
    I = max(max(s["Iy"], s["Iz"]) for s in sec_names.values()) * LINK_STIFFNESS_FACTOR
    J = max(s["J"] for s in sec_names.values()) * LINK_STIFFNESS_FACTOR
    model.add_material("_link", max_E, max_E / 2.6, 0.3, 0.0)
    model.add_section("_link", A, I, I, J)
    for s in design.get("supports", []):
        fn = s.get("frame_node")
        if not fn:
            raise DesignError(f"support {s['id']!r} has no frame_node, so its load cannot reach the frame; name the frame node that carries it")
        if fn not in nodes:
            raise DesignError(f"support {s['id']!r} names frame_node {fn!r}, which is not in design.nodes")
        if fn not in used:
            raise DesignError(f"support {s['id']!r} names frame_node {fn!r}, which no member uses; attach it to the frame")
        c = np.asarray(s["position_mm"], float)
        if np.linalg.norm(c - nodes[fn]) < 1e-6:
            fm.wheel_nodes[s["id"]] = fn
            continue
        wn = wheel_node(s["id"])
        model.add_node(wn, *to_pynite(c))
        model.add_member(f"link:{s['id']}", fn, wn, "_link", "_link")
        fm.wheel_nodes[s["id"]] = wn
        fm.links.append(f"link:{s['id']}")
    return fm

"""The design's frame as nodes and beam elements, with the masses lumped to the nodes.

SI units inside (m, kg, N, s, Pa). Each design member is split into up to N_SUB elements of equal
length, fewer when the member is short (no element shorter than MIN_ELEMENT_RADII radii of gyration
of its section): an Euler-Bernoulli element much shorter than its section is deep bends faster than it
stretches, and that would set a much smaller stable time step without being a better model. The
interior nodes are named "<member id>~k" (k = 1..n-1) as K1 names them.

Section properties are K0's (hiraku.solvers.k0_handcalc.sections: A, Iy, Iz, J; frame_inputs:
the plastic moduli and the plastic torque of the tubes). Local axes follow K1 (PyNite's rule
read in the design frame): x along the member, y the section's h direction, which is the part of
global z across the member (up); a vertical member going up has y = -x_design, going down y = +x_design;
z = x cross y.

Masses: each element's own mass (density x A x length) half to each of its two nodes; every part
with carried_by nodes in equal shares to those nodes (a carried_by node no member uses: the nearest
mesh node instead); every part without carried_by (wheels, suspension) to the frame node of the
support nearest to it (K0's rule in hiraku.solvers.k0_handcalc.vehicle.mass_items), and a support
without a frame node to the mesh node nearest the support. The total is the design's mass.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from hiraku.solvers.k0_handcalc import sections as k0_sections
from hiraku.solvers.k0_handcalc.frame_inputs import plastic_modulus, plastic_torque
from hiraku.solvers.k0_handcalc.vehicle import DesignError, mass_items, member_props

N_SUB = 4                 # elements per member at most
MIN_ELEMENT_RADII = 3.5   # no element shorter than this many radii of gyration (the larger of the two)
ROT_INERTIA_FLOOR = 1.0 / 12.0  # rotational inertia of an element end >= (its mass / 2) x this x length^2


def section_capacity(section: dict, props: dict, fy: float) -> dict:
    """Plastic capacities (N, N*mm) of a section with yield stress fy (MPa): Np = A fy, Mpy / Mpz =
    Zp fy about local y / z, Tp the plastic torque. Tubes: K0's plastic_modulus and plastic_torque.
    Solid rectangle: b h^2 / 4 and h b^2 / 4, solid round: d^3 / 6 (textbook plastic moduli); any other
    shape: the elastic moduli I / c (no plastic reserve; `basis` says so) and the torque at first
    shear yield. Torque of the solids at first yield: (fy / sqrt 3) / tau_per_T."""
    shape = section.get("shape")
    tau_y = fy / math.sqrt(3.0)
    if shape in ("rect_tube", "round_tube"):
        zz, zy = plastic_modulus(section)
        tp = plastic_torque(section, tau_y)
        basis = "plastic moduli (K0 frame_inputs.plastic_modulus), plastic torque 2 A_m t fy / sqrt 3"
    elif shape == "rect_solid":
        b, h = (float(section["dims_mm"][k]) for k in ("b", "h"))
        zz, zy = b * h * h / 4.0, h * b * b / 4.0
        tp = tau_y / props["tau_per_T"]
        basis = "plastic moduli b h^2 / 4, h b^2 / 4; torque at first shear yield"
    elif shape == "round_solid":
        d = float(section["dims_mm"]["d"])
        zz = zy = d ** 3 / 6.0
        tp = tau_y / props["tau_per_T"]
        basis = "plastic modulus d^3 / 6; torque at first shear yield"
    else:
        cy, cz, tpt = props.get("cy"), props.get("cz"), props.get("tau_per_T")
        if not cy or not cz or not tpt:
            raise DesignError(f"section shape {shape!r} gives no cy, cz and tau_per_T, so its yield moments are not "
                              "known; give them in dims_mm")
        zz, zy = props["Iz"] / cy, props["Iy"] / cz
        tp = tau_y / tpt
        basis = f"{shape}: elastic moduli I / c (first yield, no plastic reserve); torque at first shear yield"
    return {"Np": props["A"] * fy, "Mpy": zy * fy, "Mpz": zz * fy, "Tp": tp, "basis": basis}


def local_frame(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """3 x 3, columns the member's local x, y, z in the design frame (K1's axes, see the module text)."""
    ex = (b - a) / np.linalg.norm(b - a)
    up = np.array([0.0, 0.0, 1.0])
    if abs(float(ex @ up)) > 1.0 - 1e-9:
        ey = np.array([-1.0, 0.0, 0.0]) if ex[2] > 0.0 else np.array([1.0, 0.0, 0.0])
    else:
        ey = up - float(up @ ex) * ex
        ey /= np.linalg.norm(ey)
    ez = np.cross(ex, ey)
    return np.column_stack([ex, ey, ez])


@dataclass
class Mesh:
    """Nodes and elements of the frame (SI), the lumped masses, and the bookkeeping to read them back."""

    node_ids: list[str]
    x0: np.ndarray              # (n, 3) m
    mass: np.ndarray            # (n,) kg, translational
    inertia: np.ndarray         # (n,) kg m^2, rotational (isotropic, see element_inertia)
    con: np.ndarray             # (ne, 2) int
    l0: np.ndarray              # (ne,) m
    e0: np.ndarray              # (ne, 3, 3) local axes (columns) at the start
    prop: np.ndarray            # (ne, 8): EA, GJ, EIy, EIz, Np, Mpy, Mpz, Tp (N, N m)
    member_of: list[str]        # (ne,) design member id of each element
    design_nodes: dict[str, int]  # design node id -> mesh index
    node_parts: dict[str, dict[int, float]] = field(default_factory=dict)  # part id -> {node index: kg}
    frame_mass: float = 0.0
    notes: list[str] = field(default_factory=list)
    density: np.ndarray | None = None  # (ne,) kg/m^3
    area: np.ndarray | None = None     # (ne,) m^2

    @property
    def n(self) -> int:
        return len(self.node_ids)

    @property
    def ne(self) -> int:
        return len(self.con)

    def members(self) -> list[str]:
        return list(dict.fromkeys(self.member_of))

    def nearest(self, p_m: np.ndarray, among: list[int] | None = None) -> int:
        idx = list(range(self.n)) if among is None else among
        d = np.linalg.norm(self.x0[idx] - p_m, axis=1)
        return idx[int(np.argmin(d))]


def n_elements(length_mm: float, props: dict) -> int:
    """Elements for a member: N_SUB, fewer so none is shorter than MIN_ELEMENT_RADII radii of gyration."""
    r = math.sqrt(max(props["Iy"], props["Iz"]) / props["A"])
    return max(1, min(N_SUB, int(length_mm // (MIN_ELEMENT_RADII * r))))


def element_inertia(m_e: float, l_e: float, a: float, iy: float, iz: float) -> float:
    """Rotational inertia (kg m^2) one end of an element gives its node: (m_e / 2) times the larger of the
    section's polar radius of gyration squared ((Iy + Iz) / A, the physical value) and
    ROT_INERTIA_FLOOR l_e^2 (rotational mass scaling, so the rotations do not set the time step; it
    adds no translational mass)."""
    return 0.5 * m_e * max((iy + iz) / a, ROT_INERTIA_FLOOR * l_e * l_e)


def build_mesh(design: dict, n_sub: int | None = None) -> Mesh:
    """The mesh of a design (members, sections, materials, nodes, parts, supports)."""
    props = member_props(design)
    if not props:
        raise DesignError("the design has no members, so there is no frame to crash")
    nodes_mm = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    used = []
    for mp in props.values():
        for end in ("node_a", "node_b"):
            if mp["member"][end] not in used:
                used.append(mp["member"][end])
    ids = list(used)
    xyz = [nodes_mm[i] / 1000.0 for i in ids]
    index = {nid: k for k, nid in enumerate(ids)}
    con, l0, e0, prop, member_of, dens, area = [], [], [], [], [], [], []
    notes = []
    bases = set()
    for mid, mp in props.items():
        mem, sec, mat = mp["member"], mp["section"], mp["material"]
        E = float(mat["E_MPa"])
        nu = float(mat["nu"])
        G = float(mat.get("G_MPa") or E / (2.0 * (1.0 + nu)))
        if "yield_MPa" not in mat:
            raise DesignError(f"member {mid!r}: material {mem['material']!r} gives no yield_MPa; the crash needs it")
        fy = float(mat["yield_MPa"])
        cap = section_capacity(design["sections"][mem["section"]], sec, fy)
        bases.add(cap["basis"])
        k = n_sub if n_sub is not None else n_elements(mp["length"], sec)
        a, b = mp["a"] / 1000.0, mp["b"] / 1000.0
        chain = [index[mem["node_a"]]]
        for s in range(1, k):
            ids.append(f"{mid}~{s}")
            xyz.append(a + (b - a) * s / k)
            chain.append(len(ids) - 1)
        chain.append(index[mem["node_b"]])
        frame = local_frame(a, b)
        # N, N m from MPa and mm: E A [MPa mm^2 = N]; E I [MPa mm^4 = N mm^2 = 1e-6 N m^2]; moments N mm = 1e-3 N m
        p = [E * sec["A"], G * sec["J"] * 1e-6, E * sec["Iy"] * 1e-6, E * sec["Iz"] * 1e-6,
             cap["Np"], cap["Mpy"] * 1e-3, cap["Mpz"] * 1e-3, cap["Tp"] * 1e-3]
        for s in range(k):
            con.append((chain[s], chain[s + 1]))
            l0.append(mp["length"] / 1000.0 / k)
            e0.append(frame)
            prop.append(p)
            member_of.append(mid)
            dens.append(float(mat["density_kg_m3"]))
            area.append(sec["A"] * 1e-6)
    x0 = np.array(xyz, float)
    con = np.array(con, dtype=np.int64)
    l0 = np.array(l0, float)
    prop = np.array(prop, float)
    dens = np.array(dens, float)
    area = np.array(area, float)
    n = len(ids)
    mass = np.zeros(n)
    inertia = np.zeros(n)
    for e in range(len(con)):
        m_e = dens[e] * area[e] * l0[e]
        iy, iz = prop[e, 2] / (prop[e, 0] / area[e]), prop[e, 3] / (prop[e, 0] / area[e])  # m^4 from E I / E
        j_e = element_inertia(m_e, l0[e], area[e], iy, iz)
        for k in con[e]:
            mass[k] += 0.5 * m_e
            inertia[k] += j_e
    frame_mass = float(mass.sum())
    mesh = Mesh(ids, x0, mass, inertia, con, l0, np.array(e0), prop, member_of,
                {nid: index[nid] for nid in used}, frame_mass=frame_mass, notes=notes, density=dens, area=area)
    _lump_parts(mesh, design, nodes_mm)
    notes.append("section capacities: " + "; ".join(sorted(bases)))
    return mesh


def _lump_parts(mesh: Mesh, design: dict, nodes_mm: dict) -> None:
    """Every part's mass to the mesh nodes (the module text says how)."""
    supports = {s["id"]: s for s in design.get("supports", [])}
    design_idx = list(mesh.design_nodes.values())
    moved, unsprung, nearest_support = [], {}, []
    for it in mass_items(design):
        if it.carrier == "member":
            continue  # the elements carry the members' own mass
        pid = it.id.split(":", 1)[1]
        share = {}
        if it.carrier == "nodes":
            for nid in it.carried_by:
                if nid in mesh.design_nodes:
                    k = mesh.design_nodes[nid]
                else:
                    k = mesh.nearest(nodes_mm[nid] / 1000.0, design_idx)
                    moved.append(f"{pid}: {nid} -> {mesh.node_ids[k]}")
                share[k] = share.get(k, 0.0) + it.mass_kg / len(it.carried_by)
        else:  # a part without carried_by rests on the nearest support (K0's rule)
            s = supports[it.carried_by[0]]
            fn = s.get("frame_node")
            if fn in mesh.design_nodes:
                k = mesh.design_nodes[fn]
            else:
                k = mesh.nearest(np.asarray(s["position_mm"], float) / 1000.0, design_idx)
                nearest_support.append(f"{s['id']} -> {mesh.node_ids[k]}")
            share[k] = it.mass_kg
            unsprung.setdefault(s["id"], [mesh.node_ids[k], 0.0])[1] += it.mass_kg
        for k, m in share.items():
            mesh.mass[k] += m
        prev = mesh.node_parts.setdefault(pid, {})
        for k, m in share.items():
            prev[k] = prev.get(k, 0.0) + m
    if unsprung:
        mesh.notes.append("the parts without carried_by (wheels, suspension) as point masses at their nearest support's "
                          "frame node: " + ", ".join(f"{sid} {v[1]:.1f} kg at {v[0]}" for sid, v in unsprung.items()))
    if moved:
        mesh.notes.append("carried_by nodes no member uses, their share put on the nearest frame node: " + ", ".join(moved))
    if nearest_support:
        mesh.notes.append("supports without a frame node in the mesh, their parts on the nearest frame node: "
                          + ", ".join(nearest_support))
    mesh.notes.append("each part's mass in equal shares on its carried_by nodes, as point masses (the part's own "
                      "rotational inertia and its stiffness are not in the model; it moves with those nodes)")


def element_matrices(mesh: Mesh, e: int) -> tuple[np.ndarray, np.ndarray]:
    """The element's linear 12 x 12 local stiffness and its own lumped mass diagonal (translational m_e / 2,
    rotational element_inertia), for the stable time step."""
    ea, gj, eiy, eiz = mesh.prop[e, :4]
    L = mesh.l0[e]
    k = np.zeros((12, 12))
    for d, s in ((0, ea / L), (3, gj / L)):  # axial (dofs 0, 6) and torsion (dofs 3, 9)
        k[d, d] = k[d + 6, d + 6] = s
        k[d, d + 6] = k[d + 6, d] = -s
    # bending in x-y (v, theta_z): dofs 1, 5, 7, 11; in x-z (w, theta_y): dofs 2, 4, 8, 10
    for (dv, dt, dv2, dt2), ei, s in (((1, 5, 7, 11), eiz, 1.0), ((2, 4, 8, 10), eiy, -1.0)):
        kb = ei / L ** 3 * np.array([[12.0, 6 * L * s, -12.0, 6 * L * s],
                                     [6 * L * s, 4 * L * L, -6 * L * s, 2 * L * L],
                                     [-12.0, -6 * L * s, 12.0, -6 * L * s],
                                     [6 * L * s, 2 * L * L, -6 * L * s, 4 * L * L]])
        idx = (dv, dt, dv2, dt2)
        for a in range(4):
            for b in range(4):
                k[idx[a], idx[b]] = kb[a, b]
    m_e = mesh.density[e] * mesh.area[e] * L
    iy, iz = eiy / (ea / mesh.area[e]), eiz / (ea / mesh.area[e])
    j_e = element_inertia(m_e, L, mesh.area[e], iy, iz)
    md = np.array([m_e / 2] * 3 + [j_e] * 3 + [m_e / 2] * 3 + [j_e] * 3)
    return k, md


def stable_dt(mesh: Mesh) -> tuple[float, int]:
    """The largest stable central-difference step of the elements, 2 / omega_max, the smallest over the
    elements (each element's own lumped masses: assembling, and the part masses, only lower the highest
    frequency), and the element that sets it."""
    best, which = math.inf, -1
    for e in range(mesh.ne):
        k, md = element_matrices(mesh, e)
        s = 1.0 / np.sqrt(md)
        w2 = float(np.max(np.linalg.eigvalsh(k * s[:, None] * s[None, :])))
        dt = 2.0 / math.sqrt(w2)
        if dt < best:
            best, which = dt, e
    return best, which

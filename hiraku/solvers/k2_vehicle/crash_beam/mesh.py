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

Wheels: each part of kind "wheel" becomes a node of its own at the wheel's centre (its box centre)
carrying the wheel's mass and the masses of the knuckles and brake calipers (kinds "knuckle",
"brake_caliper") that rest on the same support; its radius for the wall is the support's radius_mm
(else half the box's x size). It is joined to the frame by LINKS: pin-ended axial springs (no bending,
no torsion, no yield, they never break) standing in for the suspension arms. The frame nodes they go
to (the mounts): the wheel's carried_by nodes when it has them; else the carried_by nodes of the
parts reached from the wheel through connects_to by load-carrying connections over parts without
carried_by (knuckle, ball joints, arms, tie rods; not through dampers or springs), i.e. the arm
brackets; else (or when those do not hold the wheel node) the support's frame node and the design
nodes nearest the wheel, until they hold it in all three directions or at least fore and aft (the
wall's direction; a direction left free is free motion of the wheel node, there is no gravity), at
most MAX_MOUNTS. A link's axial stiffness is set
from the time step: its frequency on the wheel's share and the mount node's mass is LINK_SHARE of
the frame elements' highest one (so it does not lower the step; it is far stiffer than the arms).

Contact between elements: each element has a contact radius (half its section's larger outer
dimension, see contact_radius), and two elements that share no node touch when the distance between
their axes is less than the sum of the radii (capsules). A pair that is closer than that at the start
(near a joint) touches only when it comes closer than it started. cdist holds that distance for each
pair (-1: the pair never touches: they share a node, or one is a link).
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
LINK_SHARE = 0.2          # a wheel link's frequency is this share of the frame elements' highest (HIRAKU's choice)
WHEEL_PART_KINDS = ("wheel", "knuckle", "brake_caliper")  # their masses go to the wheel node
NO_LINK_KINDS = ("damper", "spring")  # the search for a wheel's mounts does not go through these (they give way)


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


def contact_radius(section: dict, props: dict) -> float:
    """mm, an element's contact radius: half the section's larger outer dimension (the larger of dims_mm's b, h,
    d); a section without those (custom): the larger of the stress distances cy, cz, else the larger radius of
    gyration sqrt(I / A)."""
    dims = section.get("dims_mm") or {}
    outer = [float(dims[k]) for k in ("b", "h", "d") if k in dims]
    if outer:
        return max(outer) / 2.0
    c = [float(props[k]) for k in ("cy", "cz") if props.get(k)]
    if c:
        return max(c)
    return math.sqrt(max(props["Iy"], props["Iz"]) / props["A"])


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
    crad: np.ndarray | None = None     # (ne,) m, contact radius (0 for a link)
    link: np.ndarray | None = None     # (ne,) int8, 1: a wheel link (pin-ended axial spring)
    nrad: np.ndarray | None = None     # (n,) m, a node's radius for the wall (a wheel's radius, else 0)
    cdist: np.ndarray | None = None    # (ne, ne) m, the distance between axes at which a pair touches; -1: never
    n_frame: int = 0                   # the frame's nodes are 0 .. n_frame - 1, the wheel nodes after them
    wheels: dict = field(default_factory=dict)  # wheel part id -> {"node", "radius", "mounts", "parts", "kg"}

    def __post_init__(self):
        ne, n = len(self.con), len(self.node_ids)
        if self.link is None:
            self.link = np.zeros(ne, dtype=np.int8)
        if self.crad is None:
            self.crad = np.zeros(ne)
        if self.nrad is None:
            self.nrad = np.zeros(n)
        if self.cdist is None:
            self.cdist = -np.ones((ne, ne))
        if not self.n_frame:
            self.n_frame = n

    @property
    def n(self) -> int:
        return len(self.node_ids)

    @property
    def ne(self) -> int:
        return len(self.con)

    def members(self) -> list[str]:
        """The design's members (the wheel links left out)."""
        return list(dict.fromkeys(m for e, m in enumerate(self.member_of) if not self.link[e]))

    def frame_elements(self) -> list[int]:
        return [e for e in range(self.ne) if not self.link[e]]

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


def build_mesh(design: dict, n_sub: int | None = None, wheel_nodes: bool = True) -> Mesh:
    """The mesh of a design (members, sections, materials, nodes, parts, supports). wheel_nodes: the wheels as
    nodes of their own on links (else their masses on the frame nodes, as the other parts without carried_by)."""
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
    con, l0, e0, prop, member_of, dens, area, crad = [], [], [], [], [], [], [], []
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
        r_c = contact_radius(design["sections"][mem["section"]], sec) / 1000.0
        for s in range(k):
            crad.append(r_c)
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
                {nid: index[nid] for nid in used}, frame_mass=frame_mass, notes=notes, density=dens, area=area,
                crad=np.array(crad, float))
    wheel_parts = _wheel_parts(design) if wheel_nodes else {}
    _lump_parts(mesh, design, nodes_mm, skip={q for wid, w in wheel_parts.items() for q in [wid] + w["with"]})
    if wheel_parts:
        _add_wheels(mesh, design, nodes_mm, wheel_parts)
    mesh.cdist = contact_distances(mesh)
    notes.append("section capacities: " + "; ".join(sorted(bases)))
    return mesh


def contact_distances(mesh: Mesh) -> np.ndarray:
    """(ne, ne) m: for two elements that share no node and are not links, the sum of their contact radii, or the
    distance between their axes at the start when that is less (they touch only when they come closer than they
    started); -1 for every other pair."""
    from .kernel import closest  # the kernel's own segment-to-segment closest points

    ne = mesh.ne
    out = -np.ones((ne, ne))
    x = mesh.x0
    for e in range(ne):
        if mesh.link[e]:
            continue
        a, b = int(mesh.con[e, 0]), int(mesh.con[e, 1])
        for f in range(e + 1, ne):
            if mesh.link[f]:
                continue
            c, d = int(mesh.con[f, 0]), int(mesh.con[f, 1])
            if a == c or a == d or b == c or b == d:
                continue
            s, t = closest(x, a, b, c, d)
            dist = float(np.linalg.norm(x[a] + s * (x[b] - x[a]) - x[c] - t * (x[d] - x[c])))
            out[e, f] = out[f, e] = min(float(mesh.crad[e] + mesh.crad[f]), dist)
    return out


def _wheel_parts(design: dict) -> dict[str, dict]:
    """{wheel part id: {"support": support or None, "with": [knuckle / caliper part ids resting on that support]}}
    for the parts of kind "wheel" that have a box."""
    items = {it.id.split(":", 1)[1]: it for it in mass_items(design) if it.carrier != "member"}
    supports = {s["id"]: s for s in design.get("supports", [])}
    out = {}
    for p in design.get("parts", []):
        if p.get("kind") != "wheel" or not p.get("box"):
            continue
        it = items[p["id"]]
        if it.carrier == "support":
            sup = supports[it.carried_by[0]]
        elif supports:
            c = np.asarray(p["box"]["center_mm"], float)
            sup = min(supports.values(), key=lambda s: float(np.linalg.norm(np.asarray(s["position_mm"], float) - c)))
        else:
            sup = None
        out[p["id"]] = {"support": sup, "with": []}
    for p in design.get("parts", []):
        if p.get("kind") in WHEEL_PART_KINDS[1:]:
            it = items[p["id"]]
            if it.carrier != "support":
                continue
            for wid, w in out.items():
                if w["support"] is not None and w["support"]["id"] == it.carried_by[0]:
                    w["with"].append(p["id"])
                    break
    return out


def _mount_search(design: dict, wheel_id: str) -> list[str]:
    """The design nodes the wheel's arms are fixed to: the carried_by nodes of the parts reached from the wheel through
    load-carrying connects_to links over parts without carried_by (not through dampers and springs)."""
    parts = {p["id"]: p for p in design.get("parts", [])}
    adj: dict[str, set] = {}
    for p in design.get("parts", []):
        for c in p.get("connects_to", []) or []:
            if c.get("carries") != "load" or c.get("to") not in parts:
                continue
            adj.setdefault(p["id"], set()).add(c["to"])
            adj.setdefault(c["to"], set()).add(p["id"])
    seen, todo, mounts = {wheel_id}, [wheel_id], []
    while todo:
        pid = todo.pop(0)
        for q in sorted(adj.get(pid, ())):
            if q in seen:
                continue
            seen.add(q)
            qp = parts[q]
            if qp.get("kind") in NO_LINK_KINDS or qp.get("kind") == "wheel":
                continue
            if qp.get("carried_by"):
                mounts += [n for n in qp["carried_by"] if n not in mounts]
            else:
                todo.append(q)
    return mounts


def _holds(dirs: list[np.ndarray]) -> bool:
    """The links along these unit vectors hold the wheel node in all three directions (the smallest singular value
    at least 0.1 of the largest), or at least fore and aft (x is within the span of two or more of them, to 0.1)."""
    if len(dirs) < 2:
        return False
    A = np.array(dirs)
    sv = np.linalg.svd(A, compute_uv=False)
    if len(dirs) >= 3 and sv[-1] >= 0.1 * sv[0]:
        return True
    big = sv >= 0.1 * sv[0]
    if big.sum() < 2:
        return False
    _, _, vt = np.linalg.svd(A)
    span = vt[:int(big.sum())]
    ex = np.array([1.0, 0.0, 0.0])
    return bool(np.linalg.norm(ex - span.T @ (span @ ex)) <= 0.1)


MAX_MOUNTS = 6  # the fallback takes at most this many nodes for a wheel


def _add_wheels(mesh: Mesh, design: dict, nodes_mm: dict, wheel_parts: dict) -> None:
    """A node per wheel part, its links to the mounts (the module text says how)."""
    parts = {p["id"]: p for p in design["parts"]}
    design_idx = list(mesh.design_nodes.values())
    dt_frame = stable_dt(mesh)[0]
    w_max = 2.0 / dt_frame
    texts = []
    for wid, w in wheel_parts.items():
        p = parts[wid]
        c = np.asarray(p["box"]["center_mm"], float) / 1000.0
        sup = w["support"]
        radius = (float(sup["radius_mm"]) if sup is not None and sup.get("radius_mm") else
                  float(p["box"]["size_mm"][0]) / 2.0) / 1000.0
        kg = {q: float(parts[q]["mass_kg"]) for q in [wid] + w["with"]}
        m_w = sum(kg.values())
        # the mounts
        how = "its carried_by nodes"
        names = list(p.get("carried_by") or [])
        if not names:
            names, how = _mount_search(design, wid), "the arms' brackets (found through connects_to)"
        mounts = []
        for nid in names:
            k = mesh.design_nodes[nid] if nid in mesh.design_nodes else mesh.nearest(nodes_mm[nid] / 1000.0, design_idx)
            if k not in mounts:
                mounts.append(k)
        dirs = [(mesh.x0[k] - c) / np.linalg.norm(mesh.x0[k] - c) for k in mounts]
        if not _holds(dirs):
            how = (how + ", with " if mounts else "") + "the support's frame node and the design nodes nearest the wheel"
            extra = []
            if sup is not None and sup.get("frame_node") in mesh.design_nodes:
                extra.append(mesh.design_nodes[sup["frame_node"]])
            extra += sorted(design_idx, key=lambda k: float(np.linalg.norm(mesh.x0[k] - c)))
            for k in extra:
                if _holds(dirs) or len(mounts) >= MAX_MOUNTS:
                    break
                if k in mounts:
                    continue
                mounts.append(k)
                dirs.append((mesh.x0[k] - c) / np.linalg.norm(mesh.x0[k] - c))
        # the node
        i = mesh.n
        mesh.node_ids.append(f"{wid}~centre")
        mesh.x0 = np.vstack([mesh.x0, c])
        mesh.mass = np.append(mesh.mass, m_w)
        mesh.inertia = np.append(mesh.inertia, 0.5 * m_w * radius * radius)  # a disc's; no moment acts on the node
        mesh.nrad = np.append(mesh.nrad, radius)
        for q, m in kg.items():
            mesh.node_parts[q] = {i: m}
        share = m_w / len(mounts)
        for k in mounts:
            L = float(np.linalg.norm(mesh.x0[k] - c))
            kl = (LINK_SHARE * w_max) ** 2 / (1.0 / share + 1.0 / float(mesh.mass[k]))
            mesh.con = np.vstack([mesh.con, np.array([[k, i]], dtype=np.int64)])
            mesh.l0 = np.append(mesh.l0, L)
            mesh.e0 = np.concatenate([mesh.e0, local_frame(mesh.x0[k], c)[None]])
            mesh.prop = np.vstack([mesh.prop, [[kl * L, 0.0, 0.0, 0.0, 1e300, 1e300, 1e300, 1e300]]])
            mesh.member_of.append(f"link:{wid}:{mesh.node_ids[k]}")
            mesh.density = np.append(mesh.density, 0.0)
            mesh.area = np.append(mesh.area, 0.0)
            mesh.crad = np.append(mesh.crad, 0.0)
            mesh.link = np.append(mesh.link, np.int8(1))
        mesh.wheels[wid] = {"node": i, "radius": radius, "mounts": [mesh.node_ids[k] for k in mounts],
                            "parts": list(kg), "kg": m_w, "how": how}
        texts.append(f"{wid} ({' + '.join(kg)}, {m_w:.1f} kg, radius {radius * 1000:.0f} mm) to "
                     f"{', '.join(mesh.node_ids[k] for k in mounts)} ({how})")
    mesh.notes.append("the wheels as nodes at their centres touching the wall at their radius (x + r > wall), joined to "
                      "the frame by pin-ended links standing in for the suspension arms (axial springs only, no bending, "
                      "no yield, never broken; stiffness set from the time step: each link's frequency "
                      f"{LINK_SHARE:g} x the frame elements' highest, far stiffer than the arms): " + "; ".join(texts))


def _lump_parts(mesh: Mesh, design: dict, nodes_mm: dict, skip: set | None = None) -> None:
    """Every part's mass to the mesh nodes (the module text says how); the wheels (skip) and the knuckles and calipers
    resting on their supports go to the wheel nodes instead (_add_wheels)."""
    supports = {s["id"]: s for s in design.get("supports", [])}
    design_idx = list(mesh.design_nodes.values())
    moved, unsprung, nearest_support = [], {}, []
    skip = set(skip or ())
    for it in mass_items(design):
        if it.carrier == "member":
            continue  # the elements carry the members' own mass
        pid = it.id.split(":", 1)[1]
        if pid in skip:
            continue
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
        mesh.notes.append("the other parts without carried_by (suspension) as point masses at their nearest support's "
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
        if mesh.link[e]:
            continue  # the wheel links are made to fit (LINK_SHARE)
        k, md = element_matrices(mesh, e)
        s = 1.0 / np.sqrt(md)
        w2 = float(np.max(np.linalg.eigvalsh(k * s[:, None] * s[None, :])))
        dt = 2.0 / math.sqrt(w2)
        if dt < best:
            best, which = dt, e
    return best, which

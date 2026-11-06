"""The suspension mount brackets (parts of kind bracket with part.joint, load case R9) as shell plates.

Each bracket is a clevis: two plates (cheeks) normal to the pin axis, standing on a tube wall
(k0_handcalc.bracket_inputs.mount_points gives the geometry stage 1 uses: pin axis u along the body's x,
base face of the bracket's box toward its carrier node, base line along the other of y and z, b, e).

Meshed when (else the bracket keeps the old path: its force on the joint nodes of its carried_by nodes,
mesh.ShellMesh.bracket_skipped says why):
- a rect tube runs along the pin axis and the outer surface of one of its walls is the box's base face
  (within BASE_TOL_MM), and the base line overlaps that wall (the tied span);
- the plates' grid lines on the tube (an s line at each cheek's mid-plane; c lines at the ends of the
  tied span and at the pin hole block's lines inside it) cross no shared joint's footprint on the tube
  (a crossing would cut the joint's ring);
- there is room for the pin hole block (half size at least HOLE_ROOM r) inside the plate.

The cheek: a plate of plate_t_mm whose mid-plane is plate_t / 2 inside the box's face across the car
(the box's size along x is the clevis's outside width), from the wall's mid-surface (h = 0) to the box's
far face (the plate is t_wall / 2 taller than the box, as a branch tube stops at the wall's mid-surface),
b wide along the base line. Its nodes on the base inside the tied span are the wall's nodes (welded, shared):
the wall gets an s line at the cheek and c lines at the span's ends; the cheek's base takes every wall node
in the span. The plate's base row is graded (lines at t and 2t above the base). The pin hole, diameter
d0 = d + 2 mm (library_bracket.HOLE_CLEARANCE_MM), centred at e above the base face and at the hard point
along the base line (stage 1's e), sits in an O-grid block of half size a = min(2 r, the room to the
plate's edges): 4 m nodes around the hole, layers graded toward it, the block's sides on the plate's grid.

The pin load (pin_load): the force F at the mount (the force the body receives) is split by
library_bracket.split_force; each cheek takes half of every force (as stage 1). The in-plane part F_p / 2
goes on the hole's nodes on the half facing F_p with weights cos(theta - theta_F) times the node's arc,
each node's force parallel to F_p (so the resultant is F_p / 2 exactly; a radial bearing pressure would
need the same resultant and differs only in the tangential parts, which cancel over the half); the part
along the pin F_n / 2 goes evenly on all the hole's nodes. The pin itself is not modelled.

The read-back (bracket_values): per cheek, the plate elements' nodal forces on the tied base nodes
(K_e u_e of the cheek's elements; the force the wall puts on the cheek) give the base's resultant R and
its moment M about the middle of the tied base (base_resultant). Over the welded length L (the tied span:
bracket_inputs.weld_span, the same length the hand calculation takes as b; a span end moved by snap_end makes
it shorter, said in the record) and with the formulas of library_bracket (mount_bracket.weld and .plate_bending, the
line forces and the three stresses added, safe side), each cheek welded on both sides:
    w = |R| / (2 L) + |M . base| / (L t_p) + 3 |M . u| / L^2           (N/mm, one fillet line)
    sigma = 6 |M . base| / (L t_p^2) + 6 |M . u| / (t_p L^2) + |R . up| / (L t_p)
tau_w = w / (THROAT s), weld_utilisation = tau_w / f_vw and plate_bending_utilisation = sigma / f_plate
(library_bracket.resistances), the worse cheek. The shell gives the forces' share between the two cheeks
and the moments the wall takes (the hand calculation halves the force and takes the moments from e).
The local values are singular at the ends of the tied base, where the stiff cheek meets the thin wall
(the hand case of tests/solvers/test_shell_brackets.py: the base's end line force 166 N/mm at 10 mm
elements and 234 N/mm at 5 mm, the base row's von Mises 25 and 36 MPa, against 29 N/mm and 16 MPa by
the nominal formulas), so they are reported and not judged: weld_line_peak_N_per_mm (the line force q
and moment m along the base by the base's consistent line mass matrix, base_line_forces: |q| / 2 +
|m . base| / t_p), plate_vm_peak (the base row's largest von Mises), and hole_vm (the first layer round
the hole: the pin load on a few nodes).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..k0_handcalc import library_bracket as lb
from ..k0_handcalc.bracket_inputs import mount_points, plate_material

BASE_TOL_MM = 1.0  # the box's base face on the wall's outer surface within this
SPAN_TOL_MM = 1e-6  # the tied span and bracket_inputs.weld_span (the hand calculation's welded length) agree within this
HOLE_ROOM = 1.5  # the hole block's half size a must be at least this many hole radii
HOLE_EXTRA_LAYERS = 3  # O-grid: m // 2 + this layers from the hole to the block's sides (log-polar radii)
TOL = 1e-6


@dataclass
class BracketPlan:
    """A bracket to mesh: its geometry in the tube's terms (made before the tube grids)."""

    id: str
    support: str
    mount: str
    run: int
    wall: int
    s: tuple  # the cheeks' mid-planes as stations of the run
    x: tuple  # ... and as points along the pin axis (world)
    u: np.ndarray  # pin axis (unit)
    e_w: np.ndarray  # along the base line (unit)
    e_h: np.ndarray  # the wall's outward normal (up the plate)
    origin: np.ndarray  # w = 0, h = 0 at the plane through the box's centre normal to u
    b: float
    H: float  # plate height from the wall's mid-surface
    e: float  # stage 1's e (from the base face)
    t: float
    t_wall: float
    weld_leg: float
    span: tuple  # (w0, w1): the base on the wall
    hole: tuple  # (w, h) of the hole's centre
    r: float
    a: float
    m: int  # hole block: m segments per side
    c_of_w: tuple  # (c at w = 0, dc/dw): the wall's c coordinate of a base point
    member: str = ""  # the tube's member at the base (the weld's place)


@dataclass
class MountBracket:
    """A meshed bracket (ShellMesh.brackets)."""

    id: str
    support: str
    mount: str
    member: str
    t: float
    weld_leg: float
    u: np.ndarray
    e_w: np.ndarray
    e_h: np.ndarray
    b: float
    e: float
    span_mm: float
    hole_centres: list = field(default_factory=list)  # per cheek (3,)
    elements: list = field(default_factory=list)  # per cheek: element indices
    hole_nodes: list = field(default_factory=list)  # per cheek: the hole's ring nodes (angle order)
    hole_angles: list = field(default_factory=list)  # per cheek: angle of each in the (e_w, e_h) plane
    base_nodes: list = field(default_factory=list)  # per cheek: tied base nodes, by w
    base_w: list = field(default_factory=list)
    base_elements: list = field(default_factory=list)  # per cheek: the base row on the tied span
    hole_elements: list = field(default_factory=list)  # per cheek: the first layer round the hole


def bracket_parts(design: dict) -> list[dict]:
    return [p for p in design.get("parts", []) if p.get("kind") == "bracket" and p.get("joint") and p.get("box")]


def _mounts_of(design: dict) -> dict:
    """{bracket id: (support, mount, geometry)} for the brackets stage 1 finds at the hard points."""
    out = {}
    for s in design.get("supports", []):
        if not (s.get("suspension") or {}).get("hardpoints_mm"):
            continue
        try:
            pts = mount_points(design, s["id"])
        except (KeyError, ValueError):
            continue
        for m, g in pts.items():
            if g.get("bracket") and g.get("box_b") is not None and g["bracket"] not in out:
                out[g["bracket"]] = (s["id"], m, dict(g, hp=s["suspension"]["hardpoints_mm"][m]))
    return out


def _wall_frame(run, wall: int):
    """(outward normal, unit c direction, point at c = 0 for s = 0) of a rect run's wall."""
    (y0, z0), (y1, z1) = run.wall_point(wall, 0.0), run.wall_point(wall, 1.0)
    nrm = (run.ey, run.ez, -run.ey, -run.ez)[wall]
    dc = (y1 - y0) * run.ey + (z1 - z0) * run.ez
    return nrm, dc, run.origin + y0 * run.ey + z0 * run.ez


def plan_brackets(design: dict, runs, size: float) -> tuple[list[BracketPlan], dict[str, str]]:
    """The brackets that can be meshed and, for the others, why not (module notes)."""
    parts = {p["id"]: p for p in bracket_parts(design)}
    where = _mounts_of(design)
    plans, skipped = [], {}
    for pid, p in parts.items():
        if pid not in where:
            skipped[pid] = "no suspension hard point inside its box (stage 1 does not use it)"
            continue
        sid, mount, g = where[pid]
        c, size_b = (np.asarray(p["box"][k], float) for k in ("center_mm", "size_mm"))
        u = np.asarray(g["u"], float)
        i = "xyz".index(g["base_axis"])
        j = 3 - i if i in (1, 2) else None
        e_i = np.eye(3)[i]
        e_w = np.eye(3)[j]
        up = -float(g["base_side"]) * e_i  # from the wall into the plate
        t = float(p["joint"]["plate_t_mm"])
        found = None
        for r in runs:
            if r.shape != "rect_tube" or abs(abs(float(r.ex @ u)) - 1.0) > 1e-6:
                continue
            for w in range(4):
                nrm, dc, p0 = _wall_frame(r, w)
                if float(nrm @ up) < 1.0 - 1e-6:
                    continue
                outer = float((p0 + 0.5 * r.t * nrm) @ e_i)
                if abs(outer - float(g["base_at"])) > BASE_TOL_MM:
                    continue
                Lw = r.wall_lengths()[w]
                ends = sorted([float(p0 @ e_w), float((p0 + Lw * dc) @ e_w)])
                lo_w = float(c[j] - size_b[j] / 2.0)
                w0, w1 = max(ends[0], lo_w) - lo_w, min(ends[1], lo_w + size_b[j]) - lo_w
                # the welded length the hand calculation takes (bracket_inputs.weld_span) must be this wall's span;
                # the run's own numbers are kept (the wall's grid is made from them)
                ws = g.get("weld_span")
                if ws is None or max(abs(ws["span"][0] - w0), abs(ws["span"][1] - w1)) > SPAN_TOL_MM:
                    continue
                if w1 - w0 < 2.0 * t:
                    continue
                # the plate in the box's other directions must lie along the tube (its cross-section offset)
                off = c - r.origin
                axial = float(off @ r.ex)
                if not (r.s_range()[0] + size_b[0] / 2.0 <= axial <= r.s_range()[1] - size_b[0] / 2.0):
                    continue
                found = (r, w, nrm, dc, p0, w0, w1, lo_w)
                break
            if found:
                break
        if not found:
            skipped[pid] = ("its base face is not on the outer surface of a rect tube's wall running along the pin "
                            "(the bracket's force goes on the joint nodes of its carried_by nodes, as before)")
            continue
        r, w, nrm, dc, p0, w0, w1, lo_w = found
        mid = float((p0 @ e_i))  # the wall's mid-surface along e_i
        H = float(size_b[i]) + 0.5 * r.t
        half_x = size_b[0] / 2.0 - t / 2.0
        xs = (float(c @ u) - half_x, float(c @ u) + half_x)
        s_of = lambda xv: float((xv - float(r.origin @ u)) * float(r.ex @ u))  # noqa: E731
        hp_w = float(np.asarray(g.get("hp", c), float)[j]) if g.get("hp") is not None else None
        e = float(g["e"])
        d0 = float(p["joint"]["bolt_d_mm"]) + lb.HOLE_CLEARANCE_MM
        rr = d0 / 2.0
        hole_w = (hp_w - lo_w) if hp_w is not None else size_b[j] / 2.0
        hole_h = e + 0.5 * r.t
        a = min(2.0 * rr, hole_w, size_b[j] - hole_w, hole_h - 2.0 * t, H - hole_h)
        if a < HOLE_ROOM * rr:
            skipped[pid] = f"no room for the pin hole (d0 {d0:g} mm) in the plate"
            continue
        m = 2 * max(2, math.ceil(a / size - 1e-9))
        origin = np.zeros(3)  # its component along u is 0: each cheek adds x * u
        origin[i] = mid
        origin[j] = lo_w
        # c of a base point at w: (point - p0) . dc with point_j = lo_w + w
        c0 = float((lo_w - float(p0 @ e_w)) * float(dc @ e_w))
        member = _member_at(r, s_of(float(c @ u)))
        plans.append(BracketPlan(pid, sid, mount, r.id, w, tuple(s_of(x) for x in xs), xs, u, e_w, up, origin, float(size_b[j]),
                                 H, e, t, r.t, float(p["joint"]["weld_leg_mm"]), (w0, w1), (hole_w, hole_h), rr, a, m,
                                 (c0, float(dc @ e_w)), member))
    return plans, skipped


def _member_at(run, s: float) -> str:
    for mid in run.members:
        a, b = run.member_s[mid]
        if a - TOL <= s <= b + TOL:
            return mid
    return run.members[0]


def hole_lines(pl: BracketPlan) -> tuple[np.ndarray, np.ndarray]:
    """The hole block's lines along w and h."""
    k = np.arange(pl.m + 1) / pl.m
    return pl.hole[0] - pl.a + 2.0 * pl.a * k, pl.hole[1] - pl.a + 2.0 * pl.a * k


def wall_c_lines(pl: BracketPlan, Lw: float) -> set:
    """The c lines the bracket needs on its wall: the tied span's ends and the hole block's lines inside it."""
    hw, _ = hole_lines(pl)
    ws = [pl.span[0], pl.span[1]] + [v for v in hw if pl.span[0] + TOL < v < pl.span[1] - TOL]
    out = set()
    for v in ws:
        cv = pl.c_of_w[0] + pl.c_of_w[1] * v
        if TOL < cv < Lw - TOL:
            out.add(round(float(cv), 9))
    return out


def conflicts(pl: BracketPlan, runs, joints) -> str | None:
    """Why the bracket's lines would cut a shared joint's ring (None: they do not)."""
    r = runs[pl.run]
    Lw = r.wall_lengths()[pl.wall]
    cs = wall_c_lines(pl, Lw)
    for j in joints:
        if j.kind == "tied" or j.host != r.id or not j.s_set:
            continue
        s0, s1, c0, c1 = j.footprint
        for s in pl.s:
            if s0 - TOL <= s <= s1 + TOL:
                return f"its cheek at s {s:.1f} mm crosses the joint of {runs[j.branch].members[0]} at {j.node}"
        walls = [(j.wall, c0, c1, set(j.c_set))]
        if j.through and j.far_footprint:
            walls.append((j.far_wall, *j.far_footprint[2:], set(j.far_c_set)))
        for w, a, b, own in walls:
            if w != pl.wall:
                continue
            for cv in cs:
                if a + TOL < cv < b - TOL and not any(abs(cv - o) < TOL for o in own):
                    return snap_end(pl, cv, own) or (f"its base line's end crosses the joint of "
                                                     f"{runs[j.branch].members[0]} at {j.node}")
    lo, hi = r.s_range()
    if min(pl.s) < lo + TOL or max(pl.s) > hi - TOL:
        return "a cheek is beyond the tube's end"
    return None


def snap_end(pl: BracketPlan, cv: float, own) -> tuple | None:
    """A tied span end at wall line cv that would cut a joint's ring: ("snap", end, w) moving that end to the
    nearest of the joint's own lines `own` inside the span (the wall has those lines already), or None when cv is
    not a span end (a hole line) or no such line is left."""
    ws = [(cv - pl.c_of_w[0]) / pl.c_of_w[1]]
    end = next((i for i in (0, 1) if abs(pl.span[i] - ws[0]) < 1e-6), None)
    if end is None:
        return None
    cands = [(o - pl.c_of_w[0]) / pl.c_of_w[1] for o in own]
    keep = pl.span[1 - end]  # the other end stays; the span stays inside and at least 2 t long
    inside = [w for w in cands if pl.span[0] < w < pl.span[1] and abs(w - keep) >= 2.0 * pl.t]
    if not inside:
        return None
    return ("snap", end, min(inside, key=lambda w: abs(w - ws[0])))


def _divide(points, size: float) -> np.ndarray:
    pts = np.unique(np.round(np.asarray(points, float), 9))
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        n = max(1, math.ceil((b - a) / size - 1e-9))
        out.extend(a + (b - a) * np.arange(1, n + 1) / n)
    return np.array(out)


def plate_grid(pl: BracketPlan, ring_w: np.ndarray, size: float):
    """One cheek in (w, h): points (P, 2), quads (Q, 4) counter-clockwise in (w, h), and index sets
    {"base": points on h = 0 inside the tied span (by w), "hole": the ring (by angle), "hole_angles",
    "base_quads", "hole_quads"}. ring_w: the wall's node positions (as w) inside the tied span."""
    hw, hh = hole_lines(pl)
    w0, w1 = pl.span
    fixed = sorted({0.0, pl.b, w0, w1, *hw})
    ws = [0.0]
    for a, b in zip(fixed[:-1], fixed[1:]):
        if b - a < TOL:
            continue
        if w0 - TOL <= a and b <= w1 + TOL:
            inner = [v for v in ring_w if a + TOL < v < b - TOL]
            ws.extend(sorted(inner) + [b])
        elif hw[0] - TOL <= a and b <= hw[-1] + TOL:
            ws.append(b)
        else:
            ws.extend(_divide([a, b], size)[1:])
    ws = np.unique(np.round(np.array(ws), 9))
    hfix = [0.0, pl.H, *hh] + [k * pl.t for k in (1, 2) if k * pl.t < hh[0] - TOL]
    hfix = sorted(set(round(v, 9) for v in hfix))
    hs = [0.0]
    for a, b in zip(hfix[:-1], hfix[1:]):
        if hh[0] - TOL <= a and b <= hh[-1] + TOL:
            hs.append(b)
        else:
            hs.extend(_divide([a, b], size)[1:])
    hs = np.unique(np.round(np.array(hs), 9))
    nw, nh = len(ws), len(hs)
    gid = np.arange(nw * nh).reshape(nw, nh)
    pts = [np.stack(np.meshgrid(ws, hs, indexing="ij"), axis=-1).reshape(-1, 2)]
    iw0, iw1 = int(np.argmin(np.abs(ws - hw[0]))), int(np.argmin(np.abs(ws - hw[-1])))
    ih0, ih1 = int(np.argmin(np.abs(hs - hh[0]))), int(np.argmin(np.abs(hs - hh[-1])))
    quads, base_q = [], []
    for a in range(nw - 1):
        for b in range(nh - 1):
            if iw0 <= a < iw1 and ih0 <= b < ih1:
                continue  # inside the hole block
            quads.append([gid[a, b], gid[a + 1, b], gid[a + 1, b + 1], gid[a, b + 1]])
            if b == 0 and ws[a] >= w0 - TOL and ws[a + 1] <= w1 + TOL:
                base_q.append(len(quads) - 1)
    # the O-grid: the block's sides counter-clockwise
    per = ([gid[a, ih0] for a in range(iw0, iw1)] + [gid[iw1, b] for b in range(ih0, ih1)]
           + [gid[a, ih1] for a in range(iw1, iw0, -1)] + [gid[iw0, b] for b in range(ih1, ih0, -1)])
    P = pts[0]
    C = np.array(pl.hole)
    B = P[per]
    d = B - C
    ang = np.arctan2(d[:, 1], d[:, 0])
    R = np.linalg.norm(d, axis=1)
    u = d / R[:, None]
    # log-polar layers: on each node's ray the radii grow geometrically from r to the block's side, so every
    # layer has the same dr / r all round and blends smoothly from the circle to the square (no kink where a
    # straight circle-to-square interpolation turns at the corner rays)
    L = pl.m // 2 + HOLE_EXTRA_LAYERS
    s = np.arange(L + 1) / L
    n = len(per)
    layer_ids = []
    extra = []
    base = len(P)
    for li in range(L):
        layer_ids.append(base + li * n + np.arange(n))
        extra.append(C + (pl.r * (R / pl.r) ** s[li])[:, None] * u)
    layer_ids.append(np.asarray(per))
    P = np.vstack([P] + extra)
    hole_q = []
    for li in range(L):
        inner, outer = layer_ids[li], layer_ids[li + 1]
        for k in range(n):
            k1 = (k + 1) % n
            quads.append([inner[k], outer[k], outer[k1], inner[k1]])
            if li == 0:
                hole_q.append(len(quads) - 1)
    Q = np.array(quads, int)
    # drop the grid points inside the block that no quad uses
    used = np.zeros(len(P), bool)
    used[Q.ravel()] = True
    remap = -np.ones(len(P), int)
    remap[used] = np.arange(int(used.sum()))
    base_pts = [gid[a, 0] for a in range(nw) if w0 - TOL <= ws[a] <= w1 + TOL]
    return (P[used], remap[Q], {"base": remap[np.array(base_pts)], "base_w": ws[[a for a in range(nw)
                                                                                 if w0 - TOL <= ws[a] <= w1 + TOL]],
                                "hole": remap[layer_ids[0]], "hole_angles": ang,
                                "base_quads": np.array(base_q, int), "hole_quads": np.array(hole_q, int)})


def to_xyz(pl: BracketPlan, k: int, wh: np.ndarray) -> np.ndarray:
    """Points (w, h) of cheek k in design coordinates."""
    o = pl.origin + pl.x[k] * pl.u
    return o[None, :] + wh[:, :1] * pl.e_w[None, :] + wh[:, 1:] * pl.e_h[None, :]


# ------------------------------------------------------------------ loads and read-back
def pin_load(nodes_xyz: np.ndarray, br: MountBracket, force, n_dof: int) -> np.ndarray:
    """The force vector (n_dof,) of the mount force `force` (N, 3) on the bracket's hole edges (module notes)."""
    F = np.asarray(force, float)
    Fn = float(F @ br.u)
    Fp = F - Fn * br.u
    out = np.zeros(n_dof)
    fp = float(np.linalg.norm(Fp))
    for k in range(len(br.hole_nodes)):
        nodes = np.asarray(br.hole_nodes[k], int)
        f = np.zeros((len(nodes), 3))
        f += (0.5 * Fn / len(nodes)) * br.u[None, :]
        if fp > 0:
            dirn = Fp / fp
            th_f = math.atan2(float(dirn @ br.e_h), float(dirn @ br.e_w))
            ang = np.asarray(br.hole_angles[k], float)
            nxt, prv = np.roll(ang, -1), np.roll(ang, 1)
            arc = 0.5 * (np.abs(np.angle(np.exp(1j * (nxt - ang)))) + np.abs(np.angle(np.exp(1j * (ang - prv)))))
            wgt = np.maximum(np.cos(ang - th_f), 0.0) * arc
            f += (0.5 * fp) * (wgt / wgt.sum())[:, None] * dirn[None, :]
        for c in range(3):
            np.add.at(out, 6 * nodes + c, f[:, c])
    return out


def _line_mass(w: np.ndarray) -> np.ndarray:
    n = len(w)
    M = np.zeros((n, n))
    for i in range(n - 1):
        h = w[i + 1] - w[i]
        M[i:i + 2, i:i + 2] += h / 6.0 * np.array([[2.0, 1.0], [1.0, 2.0]])
    return M


def base_line_forces(model, br: MountBracket, k: int, U: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(q (n, 3) N/mm, m (n, 3) N mm/mm) along cheek k's tied base: the plate's nodal forces on the base nodes
    turned into line values by the base's consistent line mass matrix."""
    from . import element

    el = np.asarray(br.elements[k], int)
    xyz = model.nodes[model.elements[el]]
    Ke = element.global_stiffness_blocks(xyz, model.thickness[el], model.E[el], model.nu[el], model.drill_factor,
                                         model.incompatible)
    dofs = (6 * model.elements[el][:, :, None] + np.arange(6)[None, None, :]).reshape(len(el), 24)
    fe = np.einsum("eij,ej->ei", Ke, U[dofs])
    nodal = np.zeros((model.n_nodes, 6))
    np.add.at(nodal, model.elements[el].ravel(), fe.reshape(-1, 6))
    base = np.asarray(br.base_nodes[k], int)
    M = _line_mass(np.asarray(br.base_w[k], float))
    q = np.linalg.solve(M, nodal[base, :3])
    m = np.linalg.solve(M, nodal[base, 3:])
    return q, m


def bracket_values(model, mesh, U: np.ndarray, vm_max: np.ndarray) -> dict:
    """{bracket id: {"support", "mount", "member", "weld_line_N_per_mm", "tau_w", "weld_utilisation",
    "weld_at_mm", "plate_vm", "plate_bending_utilisation", "plate_at_mm", "hole_vm", "hole_at_mm",
    "hole_element", "plate_element"}} of the meshed brackets (module notes)."""
    mat = plate_material()
    res = lb.resistances(mat["f_y"], mat["f_u"])
    centres = model.nodes[model.elements].mean(axis=1) if mesh.brackets else None
    out = {}
    for br in mesh.brackets:
        L, t = br.span_mm, br.t
        w_best = s_best = w_peak = -1.0
        w_at = s_at = None
        for k in range(len(br.base_nodes)):
            q, m = base_line_forces(model, br, k, U)
            wl = 0.5 * np.linalg.norm(q, axis=1) + np.abs(m @ br.e_w) / t
            i = int(np.argmax(wl))
            w_peak = max(w_peak, float(wl[i]))
            R, M, c0 = base_resultant(model, br, k, U)
            Mb, Mu, Rv = abs(float(M @ br.e_w)), abs(float(M @ br.u)), abs(float(R @ br.e_h))
            w = float(np.linalg.norm(R)) / (2.0 * L) + Mb / (L * t) + 3.0 * Mu / (L * L)
            s = 6.0 * Mb / (L * t * t) + 6.0 * Mu / (t * L * L) + Rv / (L * t)
            if w > w_best:
                w_best, w_at = w, c0
            if s > s_best:
                s_best, s_at = s, c0
        base_el = np.concatenate([np.asarray(x, int) for x in br.base_elements])
        hole_el = np.concatenate([np.asarray(x, int) for x in br.hole_elements])
        pe = int(base_el[np.argmax(vm_max[base_el])])
        he = int(hole_el[np.argmax(vm_max[hole_el])])
        tau = w_best / (lb.THROAT * br.weld_leg)
        out[br.id] = {"support": br.support, "mount": br.mount, "member": br.member, "weld_length_mm": L,
                      "box_width_mm": br.b,
                      "weld_line_N_per_mm": w_best, "tau_w": tau, "weld_utilisation": tau / res["f_vw"],
                      "weld_at_mm": w_at, "plate_sigma": s_best,
                      "plate_bending_utilisation": s_best / res["f_plate"], "plate_at_mm": s_at,
                      "weld_line_peak_N_per_mm": w_peak, "plate_vm_peak": float(vm_max[pe]),
                      "plate_peak_at_mm": centres[pe], "plate_element": pe,
                      "hole_vm": float(vm_max[he]), "hole_at_mm": centres[he], "hole_element": he,
                      "f_vw": res["f_vw"], "f_plate": res["f_plate"]}
    return out


def base_resultant(model, br: MountBracket, k: int, U: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(R (3) N, M (3) N mm, c0 (3)): the resultant of cheek k's nodal forces on its tied base nodes and its moment
    about the tied base's middle c0 (the force the wall puts on the cheek)."""
    from . import element

    el = np.asarray(br.elements[k], int)
    xyz = model.nodes[model.elements[el]]
    Ke = element.global_stiffness_blocks(xyz, model.thickness[el], model.E[el], model.nu[el], model.drill_factor,
                                         model.incompatible)
    dofs = (6 * model.elements[el][:, :, None] + np.arange(6)[None, None, :]).reshape(len(el), 24)
    fe = np.einsum("eij,ej->ei", Ke, U[dofs]).reshape(-1, 6)
    flat = model.elements[el].ravel()
    base = np.asarray(br.base_nodes[k], int)
    on = np.isin(flat, base)
    x = model.nodes[flat[on]]
    c0 = 0.5 * (model.nodes[base[0]] + model.nodes[base[-1]])
    R = fe[on, :3].sum(axis=0)
    M = np.cross(x - c0, fe[on, :3]).sum(axis=0) + fe[on, 3:].sum(axis=0)
    return R, M, c0


__all__ = ["BracketPlan", "MountBracket", "bracket_parts", "bracket_values", "base_line_forces", "conflicts",
           "pin_load", "plan_brackets", "plate_grid", "to_xyz", "wall_c_lines"]

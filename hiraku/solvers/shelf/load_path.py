"""load_path: does every part pass its loads to the frame? (K1 static, parts as rigid bodies on contacts)

K1 (frame_static) puts each part's mass on its carried_by nodes rigidly, so a part whose box is far from
those nodes still loads the frame and is never found. Here carried_by is not trusted for the path:

- members are 3D beams (fe.beam.Beam, the sections and materials of the design, as K1), cut at least every
  ELEMENT_MM and at every contact;
- each part is a rigid body: one node with 6 DOFs at its box centre, its mass there;
- a part is joined to what its box touches - a member's tube (the member's box, k4_geometry) or another part's
  box (capsules for long thin parts) - when the gap is at most `touch` (run param, default TOUCH_MM), by a
  spring element (fe.connect.Spring, 6 DOFs) at the contact point. Its stiffness comes from the contact's
  size and an assumed bolted joint (contact_stiffness);
- a part joined to nothing, or only to parts that are joined to nothing, is a free rigid body; it is found
  by fe.precheck.rigid_body_check, reported as free_parts (count, where = the part ids) and left out of the
  solve, with the loads it would have taken named in effects. A deflection whose load point is on a free
  part is not reported (it is unbounded; JSON has no infinity): free_parts carries the failure.

Load case params.load_kind picks the loads:
- hand: hand_force at the grip of the hand control (the steering wheel's rim top, a handlebar's end) in
  three directions in turn: fore-and-aft, up-down, turning (along the rim). grip_deflection = the largest
  movement of the grip point.
- foot: foot_force on each pedal's face along its normal, pushing forward. pedal_deflection = the largest.
- inertia: every modelled part's and member's m a, plus the occupants' (occupant_mass and the
  occupant_force_* params) and the cargo's on what they rest on; four combinations: down (accel_vertical),
  down + forward (accel_longitudinal, braking), down + left and down + right (accel_lateral).
  max_part_deflection (a part box corner), max_stress (members, von Mises at the section corners).
- push: push_force outward (away from the vehicle's middle) at each on-board occupant's hip and shoulder
  (point R), on the first thing outboard of that point (a member or a part); occupant_push_deflection
  along the push. A point with nothing outboard, or only a free part, counts in occupant_push_open.

Units: N, mm, MPa, kg.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from ..fe.beam import Beam
from ..fe.connect import Spring
from ..fe.model import FEModel, ModelError, StaticSolver
from ..fe.precheck import rigid_body_check
from ..k0_handcalc import sections
from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import DesignError, member_props
from ..k4_geometry import model as k4
from ..k4_geometry import people
from ..k4_geometry.obb import OBB, from_member
from .base import LOADING, BuiltinTool, Param, Predicts, Record, check_record, on_board, param_text, param_value, \
    result, run_param

TOUCH_MM = 2.0  # a part touches what is at most this far (run param `touch`)
SLICE_MM = 20.0  # members are cut into slices this long to find where along them a part touches
ELEMENT_MM = 100.0  # beam elements at most this long
# The assumed bolted joint at every contact (HIRAKU's assumption, no source): one M8 steel bolt per
# AREA_PER_BOLT of contact, each a bar of its nominal area over the clamped length.
BOLT_D_MM = 8.0
BOLT_GRIP_MM = 10.0
BOLT_E_MPA = 205000.0  # steel; the usual round value for steel, not checked here
AREA_PER_BOLT_MM2 = 50.0 * 50.0
HAND_KINDS = ("steering", "steering_wheel", "handlebar", "hand_lever")
FOOT_KINDS = ("brake_pedal", "accelerator_pedal", "clutch_pedal", "pedal")
RIDER_KINDS = ("occupant", "cargo")  # loads, not structure
LOAD_KINDS = ("hand", "foot", "inertia", "push")
MAX_WHERE = 8  # part ids named in a result's where

ASSUMPTIONS = [
    "部品は剛体 (6 自由度、質量は箱の中心)。部材は K1 と同じ断面・材料の梁 (せん断の変形は入れない)",
    f"触れる = 部品の箱 (細長い部品はカプセル) と、部材の管の箱・隣の部品の箱のすき間が許し (既定 {TOUCH_MM:g} mm) 以下。"
    "すき間は分離軸の下限 (k4_geometry) で、部材は長さ {SLICE_MM:g} mm の輪切りごとに見る".replace("{SLICE_MM:g}", f"{SLICE_MM:g}"),
    f"触れた所はボルト留めとみる (仮定、出典なし): 触れた面 {AREA_PER_BOLT_MM2:g} mm² ごとに M{BOLT_D_MM:g} の鋼のボルト 1 本 "
    f"(締める長さ {BOLT_GRIP_MM:g} mm、E {BOLT_E_MPA:g} MPa の棒の硬さ E A / L)。3 方向の硬さは同じ。回る向きの硬さは、"
    "その硬さを触れた面に一様にならべた床のばね (正方形の面: 硬さ × 面積 / 12)",
    "carried_by の無い部品 (車輪・サスなど) は、K1 と同じく車輪の側 (ばね下) として入れない。乗員と荷は荷重だけで、"
    "体の当たる物 (座席・荷台) に掛ける",
    "フレームは各支持の frame_node で上下を止め、前後・横は 2 点で止める (タイヤは硬い)",
    "線形の静的解析。弾性の変形を、永久の変形の上限の見積もりとして使う (降伏しない範囲)",
]


# ------------------------------------------------------------------ contacts


@dataclass
class Contact:
    """A spring between two bodies ("part:<id>" and "part:<id>" or "member:<id>")."""

    a: str
    b: str
    point: np.ndarray  # mm; for a member, on its axis
    area: float  # mm^2
    k_t: float  # N/mm, each direction
    k_r: float  # N*mm/rad, each axis
    t: float | None = None  # where along the member (0 at node_a, 1 at node_b)


def contact_stiffness(area: float) -> tuple[float, float, int]:
    """(k_t N/mm, k_r N*mm/rad, bolts) of a contact of `area` mm^2 under the assumed bolted joint."""
    n = max(1, int(math.floor(area / AREA_PER_BOLT_MM2)))
    k_bolt = BOLT_E_MPA * math.pi * BOLT_D_MM ** 2 / 4.0 / BOLT_GRIP_MM
    k_t = n * k_bolt
    return k_t, k_t * max(area, AREA_PER_BOLT_MM2) / 12.0, n


def modelled_parts(design: Record) -> list[Record]:
    """The parts that are rigid bodies here: those with carried_by (the sprung ones), not occupants or
    cargo, not parts that stand for members (k4_geometry FRAME_PIECES)."""
    return [p for p in design.get("parts", []) if p.get("carried_by") and p["kind"] not in RIDER_KINDS
            and p["kind"] not in k4.FRAME_PIECES]


def _radius(obb: OBB) -> float:
    return float(np.linalg.norm(obb.half))


def _face_area(body: k4.Body) -> float:
    """The largest face of a body (a capsule: its length times its diameter)."""
    if body.capsule is not None:
        c = body.capsule
        return float(np.linalg.norm(c.b - c.a) + 2.0 * c.r) * 2.0 * c.r
    h = np.sort(body.obb.half)
    return float(4.0 * h[1] * h[2])


def _closest_in(obb: OBB, x: np.ndarray) -> np.ndarray:
    loc = obb.axes @ (x - obb.center)
    return obb.center + np.clip(loc, -obb.half, obb.half) @ obb.axes


def member_slices(design: Record) -> dict[str, list[tuple[float, k4.Body]]]:
    """Each member cut into slices of at most SLICE_MM: [(t of the slice's middle, its body)]."""
    bs = k4.bodies(design)
    out = {}
    for mid, mp in member_props(design).items():
        whole = bs[f"member:{mid}"].obb
        w, h = 2.0 * whole.half[2], 2.0 * whole.half[1]
        n = max(1, int(math.ceil(mp["length"] / SLICE_MM)))
        a, b = mp["a"], mp["b"]
        out[mid] = [((k + 0.5) / n, k4.Body(f"member:{mid}", from_member(a + (b - a) * k / n, a + (b - a) * (k + 1) / n,
                                                                        w, h, mid), "member"))
                    for k in range(n)]
    return out


def find_contacts(design: Record, touch: float = TOUCH_MM) -> list[Contact]:
    """Every contact between a modelled part and a member or another modelled part."""
    bs = k4.bodies(design)
    parts = [f"part:{p['id']}" for p in modelled_parts(design)]
    slices = member_slices(design)
    props = member_props(design)
    out: list[Contact] = []
    for i, pk in enumerate(parts):
        P = bs[pk]
        rp = _radius(P.obb)
        for mid, sl in slices.items():
            M = bs[f"member:{mid}"]
            if np.linalg.norm(P.obb.center - M.obb.center) > rp + _radius(M.obb) + touch:
                continue
            if k4.shape_overlap(P, M) < -touch:
                continue
            hit = [(t, s) for t, s in sl if np.linalg.norm(P.obb.center - s.obb.center) <= rp + _radius(s.obb) + touch
                   and k4.shape_overlap(P, s) >= -touch]
            if not hit:
                continue
            t = float(np.mean([t for t, _ in hit]))
            mp = props[mid]
            point = mp["a"] + (mp["b"] - mp["a"]) * t
            width = 2.0 * float(min(M.obb.half[1], M.obb.half[2]))
            area = len(hit) * mp["length"] / len(sl) * width
            k_t, k_r, _ = contact_stiffness(area)
            out.append(Contact(pk, f"member:{mid}", point, area, k_t, k_r, t))
        for qk in parts[i + 1:]:
            Q = bs[qk]
            if np.linalg.norm(P.obb.center - Q.obb.center) > rp + _radius(Q.obb) + touch:
                continue
            if k4.shape_overlap(P, Q) < -touch:
                continue
            point = 0.5 * (_closest_in(P.obb, Q.obb.center) + _closest_in(Q.obb, P.obb.center))
            area = min(_face_area(P), _face_area(Q))
            k_t, k_r, _ = contact_stiffness(area)
            out.append(Contact(pk, qk, point, area, k_t, k_r))
    return out


def contact_groups(design: Record, contacts: list[Contact]) -> tuple[set[str], set[str]]:
    """(held, free) part ids by following contacts from the members (a graph search, no stiffness)."""
    parts = {p["id"] for p in modelled_parts(design)}
    nbr: dict[str, set[str]] = {f"part:{p}": set() for p in parts}
    for c in contacts:
        nbr.setdefault(c.a, set()).add(c.b)
        nbr.setdefault(c.b, set()).add(c.a)
    seen = {c.a for c in contacts if c.b.startswith("member:")}
    todo = list(seen)
    while todo:
        x = todo.pop()
        for y in nbr.get(x, ()):
            if y.startswith("part:") and y not in seen:
                seen.add(y)
                todo.append(y)
    held = {k.split(":", 1)[1] for k in seen}
    return held, parts - held


# ------------------------------------------------------------------ the model


@dataclass
class LoadPathModel:
    design: Record
    model: FEModel
    beams: Beam
    elem_member: list[str]
    node_of: dict[str, int]  # design node id -> model node
    member_nodes: dict[str, list[tuple[float, int]]]  # member -> [(t, node)] along it
    part_node: dict[str, int]
    contacts: list[Contact]
    springs: Spring | None
    node_mass: np.ndarray  # kg lumped at each model node from the members
    props: dict = field(default_factory=dict)


def default_holds(design: Record) -> dict[str, str]:
    """Each support's frame node held up-down; x and y at the first, y at the one farthest from it in x."""
    sup = design.get("supports", [])
    if not sup:
        raise DesignError("the design has no supports, so nothing holds the frame")
    holds = {s["frame_node"]: "z" for s in sup}
    a = sup[0]
    b = max(sup, key=lambda s: abs(s["position_mm"][0] - a["position_mm"][0]))
    holds[a["frame_node"]] += "xy"
    if b is not a:
        holds[b["frame_node"]] = "".join(sorted(set(holds[b["frame_node"]] + "y")))
    return holds


def build(design: Record, contacts: list[Contact], exclude: frozenset = frozenset(),
          holds: dict[str, str] | None = None) -> LoadPathModel:
    """The beam-and-rigid-body model; parts in `exclude` (and their contacts) left out."""
    props = member_props(design)
    if not props:
        raise DesignError("the design has no members, so there is no frame to carry the parts")
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    model = FEModel(np.zeros((0, 3)))
    node_of: dict[str, int] = {}

    def design_node(nid: str) -> int:
        if nid not in node_of:
            node_of[nid] = model.add_node(nodes[nid])
        return node_of[nid]

    cuts: dict[str, set[float]] = {mid: set() for mid in props}
    live = [c for c in contacts if c.a.split(":", 1)[1] not in exclude and c.b.split(":", 1)[1] not in exclude]
    for c in live:
        if c.t is not None:
            cuts[c.b.split(":", 1)[1]].add(round(c.t, 9))
    conn, E, G, A, Iy, Iz, J, elem_member = [], [], [], [], [], [], [], []
    member_nodes: dict[str, list[tuple[float, int]]] = {}
    node_mass: list[float] = []
    lumps: list[tuple[int, float]] = []
    for mid, mp in props.items():
        n = max(4, int(math.ceil(mp["length"] / ELEMENT_MM)))
        ts = sorted({round(k / n, 9) for k in range(n + 1)} | cuts[mid])
        ids = []
        for t in ts:
            if t == 0.0:
                ids.append(design_node(mp["member"]["node_a"]))
            elif t == 1.0:
                ids.append(design_node(mp["member"]["node_b"]))
            else:
                ids.append(model.add_node(mp["a"] + (mp["b"] - mp["a"]) * t))
        member_nodes[mid] = list(zip(ts, ids))
        mat = mp["material"]
        e = float(mat["E_MPa"])
        g = float(mat.get("G_MPa") or e / (2.0 * (1.0 + float(mat["nu"]))))
        s = mp["section"]
        rho = float(mat["density_kg_m3"])
        for (t0, i0), (t1, i1) in zip(member_nodes[mid][:-1], member_nodes[mid][1:]):
            conn.append((i0, i1))
            E.append(e), G.append(g), A.append(s["A"]), Iy.append(s["Iy"]), Iz.append(s["Iz"]), J.append(s["J"])
            elem_member.append(mid)
            m = rho * s["A"] * (t1 - t0) * mp["length"] * 1e-9
            lumps += [(i0, m / 2.0), (i1, m / 2.0)]
    beams = Beam(np.array(conn), E, G, A, Iy, Iz, J, name="members")
    model.add(beams)
    part_node = {}
    for p in modelled_parts(design):
        if p["id"] in exclude:
            continue
        i = model.add_node(p["box"]["center_mm"])
        part_node[p["id"]] = i
        model.rotation_nodes.add(i)
    springs = None
    if live:
        sconn, k, pts = [], [], []
        for c in live:
            a = part_node[c.a.split(":", 1)[1]]
            if c.t is None:
                b = part_node[c.b.split(":", 1)[1]]
                p = c.point
            else:
                b = dict((round(t, 9), i) for t, i in member_nodes[c.b.split(":", 1)[1]])[round(c.t, 9)]
                p = model.nodes[b]
            sconn.append((a, b))
            k.append([c.k_t] * 3 + [c.k_r] * 3)
            pts.append(p)
        springs = Spring(np.array(sconn), np.array(k), points=np.array(pts), rotations=True, name="contacts")
        model.add(springs)
    for nid, comps in (holds if holds is not None else default_holds(design)).items():
        if nid not in node_of:
            raise DesignError(f"support node {nid!r} is not on any member, so it cannot hold the frame")
        model.fix(node_of[nid], comps if comps == "all" else "".join(sorted(set(comps))))
    mass = np.zeros(len(model.nodes))
    for i, m in lumps:
        mass[i] += m
    return LoadPathModel(design, model, beams, elem_member, node_of, member_nodes, part_node, live, springs, mass, props)


def free_parts(lp: LoadPathModel) -> list[str]:
    """Part ids the supports and contacts leave free to move (fe.precheck.rigid_body_check, by connected group).
    A free group that holds members is an error of the design (the frame itself is not held)."""
    rb = rigid_body_check(lp.model)
    if rb["free_rigid_modes"] == 0:
        return []
    labels = lp.model.connectivity_groups()
    if rb["groups"] == 1:
        raise DesignError(f"the frame is free to move ({', '.join(rb['free_motions'])}); its supports do not hold it")
    groups = {int(m.group(1)) for m in (re.match(r"group (\d+) ", s) for s in rb["free_motions"]) if m}
    frame = {int(labels[i]) for i in lp.node_of.values()}
    if groups & frame:
        loose = sorted({lp.elem_member[e] for e, (i, _) in enumerate(lp.beams.conn) if int(labels[i]) in groups})
        raise DesignError(f"members {', '.join(loose[:6])} are not joined to the held frame; join them")
    return sorted(pid for pid, i in lp.part_node.items() if int(labels[i]) in groups)


# ------------------------------------------------------------------ loads and reading back


def _load(F: np.ndarray, lp: LoadPathModel, node: int, point, force) -> None:
    f = np.asarray(force, float)
    F[node, :3] += f
    F[node, 3:] += np.cross(np.asarray(point, float) - lp.model.nodes[node], f)


def motion_at(U: np.ndarray, lp: LoadPathModel, node: int, point) -> np.ndarray:
    return U[node, :3] + np.cross(U[node, 3:], np.asarray(point, float) - lp.model.nodes[node])


def member_point_node(lp: LoadPathModel, mid: str, point) -> int:
    """The model node on member `mid` nearest to `point`."""
    ids = [i for _, i in lp.member_nodes[mid]]
    return ids[int(np.argmin(np.linalg.norm(lp.model.nodes[ids] - np.asarray(point, float), axis=1)))]


def member_stresses(lp: LoadPathModel, U: np.ndarray, lay) -> dict[str, float]:
    """Largest von Mises stress (MPa) at the element ends of each member (section corners, K1's rule)."""
    dofs = lay.element_dofs(lp.beams.conn, 6)
    flat = lay.from_node_array(U)
    ue = flat[dofs]
    f = lp.beams.end_forces(lp.model.nodes, ue)
    My, Mz = lp.beams.end_moments(lp.model.nodes, ue)
    out: dict[str, float] = {}
    for e, mid in enumerate(lp.elem_member):
        s = lp.props[mid]["section"]
        for end in (0, 1):
            vm = sections.corner_von_mises(s, f[e, 6], My[e, end], Mz[e, end], f[e, 9])
            out[mid] = max(out.get(mid, 0.0), float(vm))
    return out


def solve_cases(lp: LoadPathModel, loads: list[np.ndarray]) -> tuple[list[np.ndarray], object]:
    """Displacements (N, 6) for each load array (N, 6), one factorisation."""
    try:
        solver = StaticSolver(lp.model)
    except ModelError as e:
        raise DesignError(f"the load path model cannot be solved: {e}") from None
    out = []
    for F in loads:
        try:
            out.append(solver.solve(F).displacements)
        except ModelError as e:
            raise DesignError(f"the load path model cannot be solved: {e}") from None
    return out, solver.layout


# ------------------------------------------------------------------ control and occupant geometry


def _axes(part: Record) -> tuple[np.ndarray, np.ndarray]:
    """(rows = the box's unit axes in the design's axes, half sizes)."""
    from ..k4_geometry.obb import from_box

    o = from_box(part["box"]["center_mm"], part["box"]["size_mm"], part["box"].get("rpy_deg"))
    return o.axes, o.half


def grip(part: Record) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """The grip point of a hand control and the three hand directions (fore_aft, vertical, turning).
    A wheel (its thinnest side is its axis): the rim's top; a bar: the end of its long side toward +y."""
    ax, half = _axes(part)
    c = np.asarray(part["box"]["center_mm"], float)
    k = int(np.argmin(half))
    if part["kind"] in ("steering", "steering_wheel"):
        others = [i for i in range(3) if i != k]
        up = max(others, key=lambda i: abs(ax[i][2]))
        radial = ax[up] * (1.0 if ax[up][2] >= 0 else -1.0)
        point = c + radial * float(max(half[i] for i in others))
        turn = np.cross(ax[k], radial)
    else:
        j = int(np.argmax(half))
        d = ax[j] * (1.0 if ax[j][1] >= 0 else -1.0)
        point = c + d * float(half[j])
        turn = np.cross(np.array([0.0, 0.0, 1.0]), d)
    turn = turn / float(np.linalg.norm(turn)) if np.linalg.norm(turn) > 1e-9 else np.array([0.0, 1.0, 0.0])
    return point, {"fore_aft": np.array([1.0, 0.0, 0.0]), "vertical": np.array([0.0, 0.0, 1.0]), "turning": turn}


def pedal_face(part: Record) -> tuple[np.ndarray, np.ndarray]:
    """(the middle of the face the foot pushes, the push direction): the box's thinnest axis, pointing forward."""
    ax, half = _axes(part)
    k = int(np.argmin(half))
    n = ax[k] * (1.0 if ax[k][0] >= 0 else -1.0)
    return np.asarray(part["box"]["center_mm"], float) - n * float(half[k]), n


def hand_controls(design: Record) -> list[Record]:
    return [p for p in design.get("parts", []) if p["kind"] in HAND_KINDS]


def foot_controls(design: Record) -> list[Record]:
    return [p for p in design.get("parts", []) if p["kind"] in FOOT_KINDS]


def occupant_trunks(design: Record) -> list[Record]:
    """The occupants' trunk parts (kind occupant, an id without "."): the body box on the seat."""
    return [p for p in design.get("parts", []) if p["kind"] == "occupant" and "." not in p["id"]]


def point_r(trunk: Record) -> list[tuple[str, np.ndarray, np.ndarray]]:
    """(name, point, outward unit) of the hip and the shoulder on the outer side of an occupant's trunk box: heights
    over the box's bottom (the seat top) from k4_geometry.people (young male 95th percentile, AIST: trochanter
    height I11, shoulder height I3), the hip at the box's middle in x, the shoulder back_shoulder (C16) from its
    back. Outward is away from the vehicle's middle (both sides for an occupant on the middle)."""
    s = people.SIZES[people.BIG]
    c = np.asarray(trunk["box"]["center_mm"], float)
    size = np.asarray(trunk["box"]["size_mm"], float)
    bottom, back = c[2] - size[2] / 2.0, c[0] - size[0] / 2.0
    sides = [1.0, -1.0] if abs(c[1]) < 1.0 else [float(np.sign(c[1]))]
    out = []
    for sgn in sides:
        y = c[1] + sgn * size[1] / 2.0
        out.append(("hip", np.array([c[0], y, bottom + s["trochanter_height"]]), np.array([0.0, sgn, 0.0])))
        out.append(("shoulder", np.array([back + s["back_shoulder"], y, bottom + s["shoulder_height"]]),
                    np.array([0.0, sgn, 0.0])))
    return out


def _ray_box(p: np.ndarray, d: np.ndarray, obb: OBB) -> float | None:
    """Distance along the ray p + s d (s >= 0) to an oriented box (slab test), None when it misses."""
    lp_, ld = obb.axes @ (p - obb.center), obb.axes @ d
    lo, hi = 0.0, math.inf
    for i in range(3):
        if abs(ld[i]) < 1e-12:
            if abs(lp_[i]) > obb.half[i]:
                return None
            continue
        a, b = (-obb.half[i] - lp_[i]) / ld[i], (obb.half[i] - lp_[i]) / ld[i]
        lo, hi = max(lo, min(a, b)), min(hi, max(a, b))
        if lo > hi:
            return None
    return lo


def first_outboard(design: Record, point: np.ndarray, out: np.ndarray, skip: set[str]) -> tuple[str, float] | None:
    """The first member or modelled part a ray from `point` along `out` meets: (body id, distance)."""
    bs = k4.bodies(design)
    allowed = {f"part:{p['id']}" for p in modelled_parts(design)} | {k for k in bs if k.startswith("member:")}
    best = None
    for k in allowed - skip:
        s = _ray_box(point, out, bs[k].obb)
        if s is not None and (best is None or s < best[1]):
            best = (k, s)
    return best


# ------------------------------------------------------------------ the analysis


def analyse_load_path(design: Record, kind: str, values: dict, touch: float = TOUCH_MM,
                      holds: dict[str, str] | None = None) -> Record:
    """Results, effects and vectors of one load kind (see the module docstring). `values`: the forces (N),
    accelerations (g) and occupant mass (kg) of the load case, and the part ids picked (hand_part, foot_parts)."""
    contacts = find_contacts(design, touch)
    full = build(design, contacts, holds=holds)
    free = free_parts(full)
    lp = build(design, contacts, exclude=frozenset(free), holds=holds) if free else full
    parts = {p["id"]: p for p in design.get("parts", [])}
    effects: list[Record] = []
    results = [result("contacts", len(lp.contacts), "1")]
    results += [result(f"free_part.{pid}", 1.0, "1", pid) for pid in free]
    if free:
        effects.append({"name": f"どこにも繋がらない部品 {len(free)} 個 (自由な剛体、解析から外した): " + ", ".join(free),
                        "value": float(len(free)), "unit": "1"})
    vectors: list[Record] = []
    N = len(lp.model.nodes)
    lost: list[str] = []

    def on_part(pid: str) -> int | None:
        if pid in lp.part_node:
            return lp.part_node[pid]
        lost.append(pid)
        return None

    if kind == "hand":
        hid = values.get("hand_part") or next((p["id"] for p in hand_controls(design)), None)
        if hid is None or hid not in parts:
            raise DesignError("the design has no hand control (a part of kind " + ", ".join(HAND_KINDS) + ")")
        point, dirs = grip(parts[hid])
        node = on_part(hid)
        F_hand = float(values["hand_force"])
        if node is not None:
            loads = []
            for d in dirs.values():
                F = np.zeros((N, 6))
                _load(F, lp, node, point, F_hand * d)
                loads.append(F)
            Us, lay = solve_cases(lp, loads)
            defl = {name: float(np.linalg.norm(motion_at(U, lp, node, point))) for name, U in zip(dirs, Us)}
            top = max(defl, key=defl.get)
            results.append(result("grip_deflection", defl[top], "mm", hid))
            results += [result(f"grip_deflection.{n}", v, "mm", hid) for n, v in defl.items()]
            results += _stress_results(lp, Us, lay)
            effects.append({"name": f"握る点のいちばん大きな動きは {top} の向き", "value": defl[top], "unit": "mm"})
        vectors += [{"kind": "load", "at": hid, "origin_mm": [float(v) for v in point],
                     "vector_N": [float(v) for v in F_hand * d]} for d in dirs.values()]
    elif kind == "foot":
        wanted = values.get("foot_parts") or [p["id"] for p in foot_controls(design)]
        if not wanted:
            raise DesignError("the design has no foot control (a part of kind " + ", ".join(FOOT_KINDS) + ")")
        F_foot = float(values["foot_force"])
        held, loads, faces = [], [], []
        for pid in wanted:
            if pid not in parts:
                raise DesignError(f"foot_parts names {pid!r}, which is not a part of the design")
            face, n = pedal_face(parts[pid])
            vectors.append({"kind": "load", "at": pid, "origin_mm": [float(v) for v in face],
                            "vector_N": [float(v) for v in F_foot * n]})
            node = on_part(pid)
            if node is None:
                continue
            F = np.zeros((N, 6))
            _load(F, lp, node, face, F_foot * n)
            held.append((pid, node, face))
            loads.append(F)
        if loads:
            Us, lay = solve_cases(lp, loads)
            defl = {pid: float(np.linalg.norm(motion_at(U, lp, node, face))) for (pid, node, face), U in zip(held, Us)}
            top = max(defl, key=defl.get)
            results.append(result("pedal_deflection", defl[top], "mm", top))
            results += [result(f"pedal_deflection.{pid}", v, "mm", pid) for pid, v in defl.items()]
            results += _stress_results(lp, Us, lay)
    elif kind == "inertia":
        loads, names = _inertia_loads(design, lp, values, on_part, vectors)
        Us, lay = solve_cases(lp, loads)
        worst, where, combo = 0.0, None, None
        for name, U in zip(names, Us):
            for pid, node in lp.part_node.items():
                ax, half = _axes(parts[pid])
                c = np.asarray(parts[pid]["box"]["center_mm"], float)
                for s in ((i, j, k) for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)):
                    x = c + (np.array(s) * half) @ ax
                    v = float(np.linalg.norm(motion_at(U, lp, node, x)))
                    if v > worst:
                        worst, where, combo = v, pid, name
        if where is not None:
            results.append(result("max_part_deflection", worst, "mm", where))
            effects.append({"name": f"部品のいちばん大きな動きは {where} ({combo})", "value": worst, "unit": "mm"})
        results += _stress_results(lp, Us, lay, names)
    elif kind == "push":
        results += _push(design, lp, values, on_part, vectors, effects)
    else:
        raise DesignError(f"load_kind must be one of {', '.join(LOAD_KINDS)}, got {kind!r}")
    # the contract's unsupported_load: the load points with no path to the frame (a floating control, or a point R
    # with nothing outboard to take the push); the judge makes it a failing verdict
    if kind in ("hand", "foot"):
        unsupported = list(dict.fromkeys(lost))
    elif kind == "push":
        opened = next(r for r in results if r["quantity"] == "occupant_push_open")
        unsupported = [w.strip() for w in (opened.get("where") or "").split(",") if w.strip()] if opened["value"] else []
        unsupported = unsupported if len(unsupported) == int(opened["value"]) else [opened.get("where")] * int(opened["value"])
    else:
        unsupported = []
    if unsupported:
        results.append(result("unsupported_load", len(unsupported), "1", _where(unsupported)))
    loaded = [pid for pid in dict.fromkeys(lost) if pid in free]  # the free parts this load case loads, named first
    results.insert(0, result("free_parts", len(free), "1", _where(loaded + [p for p in free if p not in loaded])))
    for pid in dict.fromkeys(lost):
        effects.append({"name": f"{pid} はどこにも繋がらないので、{pid} に掛かる荷重は枠まで届かない (この荷重は解析に入れていない)"})
    return {"results": results, "effects": effects, "vectors": vectors, "contacts": lp.contacts, "free": free}


def _where(ids: list[str]) -> str | None:
    if not ids:
        return None
    return ", ".join(ids[:MAX_WHERE]) + (f" (ほか {len(ids) - MAX_WHERE})" if len(ids) > MAX_WHERE else "")


def _stress_results(lp: LoadPathModel, Us: list[np.ndarray], lay, names: list[str] | None = None) -> list[Record]:
    worst: dict[str, float] = {}
    for U in Us:
        for mid, v in member_stresses(lp, U, lay).items():
            worst[mid] = max(worst.get(mid, 0.0), v)
    top = max(worst, key=worst.get)
    return [result("max_stress", worst[top], "MPa", top)]


def _inertia_loads(design: Record, lp: LoadPathModel, values: dict, on_part, vectors: list) -> tuple[list, list[str]]:
    """The four combinations of body forces: down, down + forward, down + left, down + right (N, 6 each)."""
    g = STANDARD_GRAVITY
    av, al, at = (float(values[k]) for k in ("accel_vertical", "accel_longitudinal", "accel_lateral"))
    combos = {"down": (0.0, 0.0, -av), "down + forward": (al, 0.0, -av), "down + left": (0.0, at, -av),
              "down + right": (0.0, -at, -av)}
    parts = {p["id"]: p for p in design.get("parts", [])}
    N = len(lp.model.nodes)
    occ_m = float(values.get("occupant_mass") or 0.0)
    f_lat, f_lon = float(values.get("occupant_force_lateral") or 0.0), float(values.get("occupant_force_longitudinal") or 0.0)
    rests: list[tuple[str, np.ndarray, float, np.ndarray | None]] = []  # (part it rests on, point, mass, given force)
    for t in occupant_trunks(design):
        seat = _rests_on(design, t, ("seat",))
        if seat:
            rests.append((seat, np.asarray(t["box"]["center_mm"], float), occ_m, np.array([f_lon, f_lat, 0.0])))
    for c in (p for p in design.get("parts", []) if p["kind"] == "cargo"):
        on = _rests_on(design, c, None)
        if on:
            rests.append((on, np.asarray(c["box"]["center_mm"], float), float(c["mass_kg"]), None))
    loads, names = [], []
    for name, b in combos.items():
        b = np.asarray(b) * g
        F = np.zeros((N, 6))
        F[:, :3] += lp.node_mass[:, None] * b[None, :]
        for pid, node in lp.part_node.items():
            m = float(parts[pid]["mass_kg"])
            _load(F, lp, node, parts[pid]["box"]["center_mm"], m * b)
        for on, x, m, given in rests:
            node = on_part(on)
            if node is None:
                continue
            f = m * b if given is None else np.array([given[0] * np.sign(b[0]), given[1] * np.sign(b[1]), m * b[2]])
            _load(F, lp, node, x, f)
            if name == "down":
                vectors.append({"kind": "load", "at": on, "origin_mm": [float(v) for v in x], "vector_N": [float(v) for v in f]})
        loads.append(F)
        names.append(name)
    return loads, names


def _rests_on(design: Record, rider: Record, kinds: tuple[str, ...] | None) -> str | None:
    """The modelled part a rider (occupant trunk, cargo) rests on: its connects_to targets of those kinds
    (any kind when None), else the nearest modelled part of those kinds."""
    modelled = {p["id"]: p for p in modelled_parts(design)}
    for c in rider.get("connects_to", []):
        p = modelled.get(c.get("to"))
        if p is not None and (kinds is None or p["kind"] in kinds):
            return p["id"]
    cand = [p for p in modelled.values() if kinds is None or p["kind"] in kinds]
    if not cand:
        return None
    x = np.asarray(rider["box"]["center_mm"], float)
    return min(cand, key=lambda p: float(np.linalg.norm(np.asarray(p["box"]["center_mm"], float) - x)))["id"]


def _push(design: Record, lp: LoadPathModel, values: dict, on_part, vectors: list, effects: list) -> list[Record]:
    F_push = float(values["push_force"])
    trunks = occupant_trunks(design)
    if not trunks:
        raise DesignError("no occupant is on board, so there is no point R to push")
    N = len(lp.model.nodes)
    cases, open_points = [], []
    occupants = {p["id"] for p in design.get("parts", []) if p["kind"] in RIDER_KINDS}
    for t in trunks:
        for name, x, out in point_r(t):
            label = f"{t['id']}.{name}"
            hit = first_outboard(design, x, out, {f"part:{o}" for o in occupants})
            if hit is None:
                open_points.append(label)
                effects.append({"name": f"{label}: 外側に何も無い (点 R から外へ、部材にも部品にも当たらない)"})
                continue
            body, s = hit
            at = x + s * out
            vectors.append({"kind": "load", "at": label, "origin_mm": [float(v) for v in at],
                            "vector_N": [float(v) for v in F_push * out]})
            if body.startswith("member:"):
                node = member_point_node(lp, body.split(":", 1)[1], at)
            else:
                node = on_part(body.split(":", 1)[1])
                if node is None:
                    open_points.append(label)
                    continue
            F = np.zeros((N, 6))
            _load(F, lp, node, at, F_push * out)
            cases.append((label, body, node, at, out, F))
            effects.append({"name": f"{label}: {s:.0f} mm 外の {body} を押す", "value": float(s), "unit": "mm"})
    out_results = [result("occupant_push_open", len(open_points), "1", _where(open_points))]
    if cases:
        Us, lay = solve_cases(lp, [c[-1] for c in cases])
        defl = {label: float(motion_at(U, lp, node, at) @ out) for (label, _, node, at, out, _), U in zip(cases, Us)}
        top = max(defl, key=defl.get)
        out_results.append(result("occupant_push_deflection", defl[top], "mm", top))
        out_results += [result(f"occupant_push_deflection.{k}", v, "mm", k) for k, v in defl.items()]
        out_results += _stress_results(lp, Us, lay)
    return out_results


# ------------------------------------------------------------------ known answers


def _steel() -> Record:
    return {"E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 0.0, "yield_MPa": 245.0}


def _bare(nodes: dict, members: list, parts: list, section: Record) -> Record:
    return {"design_version": 1, "nodes": [{"id": k, "xyz_mm": list(v)} for k, v in nodes.items()],
            "members": [{"id": m, "node_a": a, "node_b": b, "section": "s", "material": "steel"} for m, a, b in members],
            "sections": {"s": section}, "materials": {"steel": _steel()}, "supports": [], "parts": parts}


def _box(pid: str, center, size, mass: float = 0.0, carried=("A",), kind: str = "bracket") -> Record:
    return {"id": pid, "name": pid, "kind": kind, "box": {"center_mm": list(center), "size_mm": list(size)},
            "mass_kg": mass, "carried_by": list(carried)}


def load_path_known_answers() -> list[Record]:
    out = []
    rect = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.0}}
    sec = sections.properties(rect)
    E = 205000.0
    tol = 1e-6

    # 1. a cantilever (clamped at A) with a rigid box on its free end, pushed down at the box's centre
    L, P = 1000.0, 1000.0
    d = _bare({"A": (0.0, 0.0, 0.0), "B": (L, 0.0, 0.0)}, [("m", "A", "B")],
              [_box("tip", (L - 50.0, 0.0, 40.0 + 30.0), (100.0, 40.0, 60.0))], rect)
    cs = find_contacts(d)
    lp = build(d, cs, holds={"A": "all"})
    F = np.zeros((len(lp.model.nodes), 6))
    c = np.array([L - 50.0, 0.0, 70.0])
    _load(F, lp, lp.part_node["tip"], c, (0.0, 0.0, -P))
    (U,), _ = solve_cases(lp, [F])
    got = -float(motion_at(U, lp, lp.part_node["tip"], c)[2])
    (k,) = cs
    a = float(k.point[0])  # where the spring meets the member
    e = c[0] - a  # the load's lever from there along x
    I = sec["Iz"]
    M = P * e
    v = P * a ** 3 / (3 * E * I) + M * a ** 2 / (2 * E * I)
    th = P * a ** 2 / (2 * E * I) + M * a / (E * I)
    out.append(check_record("cantilever 40x80x2, 1 m; a rigid box on its end pushed down 1 kN at the box's centre",
                            "box_deflection", v + th * e + P / k.k_t + P * e * e / k.k_r, got, "mm", tol,
                            "cantilever with a force P and a moment P e at the contact (a from the root): "
                            "v = P a^3/(3EI) + M a^2/(2EI), theta = P a^2/(2EI) + M a/(EI); the box adds theta e and the "
                            "contact spring P/k_t + P e^2/k_r"))

    # 2. a chain of three boxes stacked on the middle of a beam clamped at both ends, pushed down through the chain
    L = 1000.0
    boxes = [_box("b1", (500.0, 0.0, 40.0 + 25.0), (100.0, 40.0, 50.0)),
             _box("b2", (500.0, 0.0, 40.0 + 50.0 + 20.0), (80.0, 40.0, 40.0)),
             _box("b3", (500.0, 0.0, 40.0 + 90.0 + 15.0), (60.0, 40.0, 30.0))]
    d = _bare({"A": (0.0, 0.0, 0.0), "B": (L, 0.0, 0.0)}, [("m", "A", "B")], boxes, rect)
    cs = find_contacts(d)
    lp = build(d, cs, holds={"A": "all", "B": "all"})
    F = np.zeros((len(lp.model.nodes), 6))
    top = np.array([500.0, 0.0, 145.0])
    _load(F, lp, lp.part_node["b3"], top, (0.0, 0.0, -P))
    (U,), _ = solve_cases(lp, [F])
    got = -float(motion_at(U, lp, lp.part_node["b3"], top)[2])
    springs = sum(P / c.k_t for c in cs)
    out.append(check_record("three boxes stacked on the middle of a 1 m beam clamped at both ends, 1 kN down the chain",
                            "top_deflection", P * L ** 3 / (192 * E * sec["Iz"]) + springs, got, "mm", tol,
                            f"springs in series and the beam: P L^3/(192 E I) + sum P/k_t over the {len(cs)} contacts "
                            "(the load passes through every contact point: no moment)"))

    # 3. floating bodies are found: one box alone, and two boxes touching each other but nothing else
    d = _bare({"A": (0.0, 0.0, 0.0), "B": (L, 0.0, 0.0)}, [("m", "A", "B")],
              [_box("held", (500.0, 0.0, 65.0), (100.0, 40.0, 50.0)), _box("alone", (500.0, 0.0, 400.0), (50.0, 50.0, 50.0)),
               _box("pair1", (200.0, 300.0, 300.0), (50.0, 50.0, 50.0)),
               _box("pair2", (250.0, 300.0, 300.0), (50.0, 50.0, 50.0))], rect)
    lp = build(d, find_contacts(d), holds={"A": "all"})
    free = free_parts(lp)
    out.append(check_record("a box on a beam, a box alone and two boxes touching only each other", "free_parts",
                            3.0, float(len(free)), "1", 0.0, "the box alone and the touching pair are free rigid bodies "
                            "(fe.precheck.rigid_body_check); the box on the beam is held"))
    out.append(check_record("a box on a beam, a box alone and two boxes touching only each other", "held_box_free",
                            0.0, float("held" in free), "1", 0.0, "the box on the beam is not free"))

    # 4. a rigid box on two posts: the weight splits by the lever rule
    H, span, xc, m = 500.0, 2000.0, 600.0, 50.0
    sq = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 2.0}}
    d = _bare({"P1b": (0.0, 0.0, 0.0), "P1t": (0.0, 0.0, H), "P2b": (span, 0.0, 0.0), "P2t": (span, 0.0, H)},
              [("post1", "P1b", "P1t"), ("post2", "P2b", "P2t")],
              [_box("slab", (xc, 0.0, H + 10.0), (2 * (span - xc) + 40.0, 100.0, 20.0), mass=m, carried=("P1t",))], sq)
    lp = build(d, find_contacts(d), holds={"P1b": "all", "P2b": "all"})
    F = np.zeros((len(lp.model.nodes), 6))
    _load(F, lp, lp.part_node["slab"], (xc, 0.0, H + 10.0), (0.0, 0.0, -m * STANDARD_GRAVITY))
    (U,), lay = solve_cases(lp, [F])
    dofs = lay.element_dofs(lp.beams.conn, 6)
    N_ax = lp.beams.axial_force(lp.model.nodes, lay.from_node_array(U)[dofs])
    W = m * STANDARD_GRAVITY
    for post, share in (("post1", (span - xc) / span), ("post2", xc / span)):
        e = min(i for i, mid in enumerate(lp.elem_member) if mid == post)  # the bottom element (the posts weigh 0)
        out.append(check_record(f"a rigid 50 kg slab on two posts {span:g} mm apart, its centre {xc:g} mm from post1",
                                f"{post}_load", W * share, -float(N_ax[e]), "N", 1e-3,
                                "lever rule: post1 carries W (span - x)/span, post2 W x/span. The slab tilts by the "
                                "posts' different shortening and bends them; that share is about the ratio of a post's "
                                "end rotation stiffness to its axial stiffness times span^2, 4 r^2/span^2 = 0.02 % "
                                "(r the tube's radius of gyration, 15.5 mm)"))
    return out


# ------------------------------------------------------------------ the tool

LOAD_KIND = Param("load_kind", None, "which loads: hand (Q66 at the grip), foot (Q68 on the pedals), inertia (m a of "
                  "every part and the occupants), push (Q77 outward at point R)", default="inertia", choices=LOAD_KINDS)
HAND_FORCE = Param("hand_force", "N", "hand force at the grip of the hand control, in each of three directions (Q66)",
                   default=300.0)
FOOT_FORCE = Param("foot_force", "N", "foot force on each pedal's face, along its normal (Q68)", default=500.0)
ACCEL_V = Param("accel_vertical", "g", "vertical acceleration on every mass, gravity included", default=1.0)
ACCEL_LON = Param("accel_longitudinal", "g", "fore-and-aft acceleration (braking: the masses pushed forward) (Q76)",
                  default=0.0)
ACCEL_LAT = Param("accel_lateral", "g", "sideways acceleration, to the left and to the right in turn (Q75)", default=0.0)
OCC_MASS = Param("occupant_mass", "kg", "each on-board occupant's mass for the vertical load on the seat (Q33)",
                 default=0.0)
OCC_LAT = Param("occupant_force_lateral", "N", "each occupant's sideways inertia force on the seat (Q33 Q75 g)",
                default=0.0)
OCC_LON = Param("occupant_force_longitudinal", "N", "each occupant's fore-and-aft inertia force on the seat (Q33 Q76 g)",
                default=0.0)
PUSH_FORCE = Param("push_force", "N", "outward push at each occupant's hip and shoulder (point R) (Q77)", default=725.0)
HAND_PART = Param("hand_part", None, "the hand control's part id (without it: the first part of a hand control kind)")
FOOT_PARTS = Param("foot_parts", None, "comma-separated pedal part ids (without it: every part of a pedal kind)")
TOUCH = Param("touch", "mm", "a part touches what is at most this far from its box", default=TOUCH_MM)


class LoadPath(BuiltinTool):
    name = "load_path"
    title = "荷重の通り道 (部品を剛体に、触れている所をばねに)"
    domain = "strength"
    base = "K1"
    tests = ["R7", "R8"]
    formulas = ["load_path.control_deflection", "load_path.free_parts"]
    load_case_params = (LOAD_KIND, HAND_FORCE, FOOT_FORCE, ACCEL_V, ACCEL_LON, ACCEL_LAT, OCC_MASS, OCC_LAT, OCC_LON,
                        PUSH_FORCE, HAND_PART, FOOT_PARTS, LOADING)
    run_params = (TOUCH,)
    # Every load kind gives free_parts. grip_deflection (load_kind hand) and pedal_deflection (foot) are predicted
    # by load_path.control_deflection (output delta) with the inputs of hiraku.catalog.builders.load_path.
    # control_inputs; they are not listed here because a load case of the default kind (inertia) has neither.
    predicts = (
        Predicts("free_parts", "load_path.free_parts", "n", "the contacts followed from the members (a graph search)"),
    )

    def known_answers(self) -> list[Record]:
        return load_path_known_answers()

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        design = on_board(design, load_case)
        kind = param_text(load_case, "load_kind", "inertia")
        values = {p.name: param_value(load_case, p.name, p.unit) for p in self.load_case_params
                  if p.unit is not None}
        values["hand_part"] = param_text(load_case, "hand_part")
        fp = param_text(load_case, "foot_parts")
        values["foot_parts"] = [s.strip() for s in fp.split(",") if s.strip()] if fp else None
        touch = float(run_param(params, TOUCH))
        out = analyse_load_path(design, kind, values, touch)
        n_springs = len(out.pop("contacts"))
        free = out.pop("free")
        out["assumptions"] = list(ASSUMPTIONS) + ([f"触れる許し {touch:g} mm (run の params.touch)"] if touch != TOUCH_MM else [])
        if free:
            out["assumptions"].append(f"どこにも繋がらない部品 {len(free)} 個の質量は枠に載っていない (応力とたわみは、"
                                      "それらが繋がったときより小さく出る)")
        out["effects"].append({"name": f"触れている所 (ばね) {n_springs} か所", "value": float(n_springs), "unit": "1"})
        return out

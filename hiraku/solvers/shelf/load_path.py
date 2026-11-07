"""load_path: does every part pass its loads to the frame? (K1 static, parts as rigid bodies or plates on contacts)

K1 (frame_static) puts each part's mass on its carried_by nodes rigidly, so a part whose box is far from
those nodes still loads the frame and is never found. Here carried_by is not trusted for the path:

- members are 3D beams (fe.beam.Beam, the sections and materials of the design, as K1), cut at least every
  ELEMENT_MM and at every contact;
- each part is a rigid body: one node with 6 DOFs at its box centre, its mass there; except a plate (a part
  whose box's thinnest side is at most PLATE_RATIO of its middle side: a floor, a deck, a bulkhead, a net),
  which is a star of plate strips (plate_parts): its centre node (the mass there) joined to a node on its
  mid-plane under each contact (and under each point a push loads) by beam elements of the plate's thickness
  (the box's thinnest side: the design gives a plate part a box as thick as the sheet) and material
  (part.material; a plate without it is taken as steel and named in effects), as wide as the plate across the strip but no wider than the contact's width spread at
  PLATE_SPREAD (45 degrees each side) from the contact. A rigid floor joined both rails as one stiff body;
  the strips give them the plate's own bending (b t^3 / 12) and in-plane stiffness (t b^3 / 12, b t);
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
  max_part_deflection (a rigid part's box corner, a plate's nodes), max_stress (members, von Mises at the
  section corners).
- push: push_force outward (away from the vehicle's middle) at each on-board occupant's hip and shoulder
  (point R), on the first thing outboard of that point (a member or a part); occupant_push_deflection
  along the push. A point with nothing outboard, or only a free part, counts in occupant_push_open. Nets on
  quick-release clips and doors (k4_geometry.entry.openable_parts) are taken as open: not pushed on, and left
  out of the model, so they do not stiffen the frame either.

Every load kind that solves gives, next to max_stress (the largest von Mises stress, whatever the member's
material), max_utilisation: the largest over the members of stress / (that member's own material yield /
params.safety_factor), where = that member, and allowable_stress (MPa) of that member. At most 1 is within.

Units: N, mm, MPa, kg.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from ..fe.beam import Beam
from ..fe.connect import Spring
from ..fe.model import ElementGroup, FEModel, ModelError, StaticSolver
from ..fe.precheck import rigid_body_check
from ..k0_handcalc import sections
from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import DesignError, member_props
from ..k4_geometry import model as k4
from ..k4_geometry import people
from ..k4_geometry.entry import openable_parts
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
# Plates (HIRAKU's model, no source): a part whose box's thinnest side is at most PLATE_RATIO of its middle side
# is a plate of that thickness, solid, of part.material (else PLATE_MATERIAL), modelled as strips (beams) from
# its centre to each point it is joined or loaded at. A strip is as wide as the plate across it, but near a
# contact no wider than the contact's width (the square root of its area) plus 2 s tan(PLATE_SPREAD_DEG), s the
# distance from the contact: the usual 45-degree spread of a concentrated load, not checked against a text.
PLATE_RATIO = 0.1
PLATE_SPREAD_DEG = 45.0
PLATE_ARM_ELEMENTS = 4  # elements along each strip (the width is taken at each one's middle)
# without part.material: steel, the round values used for steel elsewhere here (not checked)
PLATE_MATERIAL = {"name": "steel (assumed)", "E_MPa": 205000.0, "nu": 0.3}
# Nets (HIRAKU's model, no source): a part of a kind in NET_KINDS is a square mesh of wires that take tension only.
# Its box's thinnest side is the wire's diameter d, part.material the wire's; the wire area per unit width in each
# of the two directions is read from the part's mass: a = mass / (2 density area). Lines of wires (cables) run
# along the box's two in-plane axes NET_CELL_MM apart (each stands for the wires of its width: area a w, pretension
# N0 w with N0 = NET_PRESTRESS_MPA a), through every load point, and end on the net's border: the convex hull of the
# points where the net is fastened (a net is taut only between its fastenings). The border is a cable of one line
# (area a NET_CELL_MM). Each fastening is a spring of one assumed bolt (contact_stiffness) to the member or the part.
# The cables are solved geometrically non-linear (Newton, the frame stays linear): tension T = max(0, T0 + EA
# (L - L0) / L0) along the cable's present direction, so a pushed net takes the push by its sag.
NET_KINDS = ("side_net",)
# A rod pinned at its ends (a brake pushrod) passes through holes and by brackets without bearing on them: it touches
# only what its connects_to pins it to (either way round).
PIN_ENDED_KINDS = ("pushrod",)
NET_CELL_MM = 50.0
NET_PRESTRESS_MPA = 20.0  # the wires' stress as fitted (assumption, no source; run param net_prestress)
# mm per mm of the largest movement. 1e-6 until 2026-11-07: on the scripted v5 (side_bar_reach 222, the nets on clips)
# R8-free's steps stalled at 1e-5 to 1e-4 mm (cables close to slack flipping taut / slack) and never settled; with
# 1e-4 it settles in 10 s, and on v4 of the same run R8-free gives the same results to 9 digits at 1e-6 and 1e-4
NET_NEWTON_TOL = 1e-4
NET_NEWTON_ITER = 100
NET_STEP_MM = 20.0  # a Newton step moves no node more than this (the pretension alone gives far too large a first step)
SLACK_FLOOR = 1e-6

ASSUMPTIONS = [
    "部品は剛体 (6 自由度、質量は箱の中心)。部材は K1 と同じ断面・材料の梁 (せん断の変形は入れない)",
    "運転席が前後にスライドする設計 (seat_slide) では、運転者の体の部分の箱は、H3 が大きい人 (AIST の若年男性 95 パーセンタイル) を座らせたスライドの中の位置にある。その位置で数が決まる",
    f"板の部品 (箱のいちばん薄い向きが、中の向きの {PLATE_RATIO:g} 倍以下: 床・荷台・隔壁・網) は剛体にしない。"
    "厚さ = 箱の薄い向きの寸法の中身の詰まった板 (設計の側で、板の部品の箱を実物の板の厚さにする)、"
    "材料は part.material (無ければ鋼 E 205000 MPa・ν 0.3 とみて、その板を effects に出す)。"
    "板の中心の節から、触れた所 (と押す点) の下の中面の節へ、板の帯の梁でつなぐ (曲げ b t³/12、面内 t b³/12、伸び b t)。"
    f"帯の幅は板の幅まで、ただし触れた所の近くは、触れた面の幅 (面積の平方根) から {PLATE_SPREAD_DEG:g}° で広がる幅まで"
    "(集中荷重の 45° の広がり、出典は確かめていない)。帯は中心を通るので、中心から外れた 2 点のあいだは実際より柔らかめ。"
    "板の質量と、板に載る乗員・荷の重さは中心の節に掛ける。板そのものの応力は出さない",
    f"触れる = 部品の箱 (細長い部品はカプセル) と、部材の管の箱・隣の部品の箱のすき間が許し (既定 {TOUCH_MM:g} mm) 以下。"
    "すき間は分離軸の下限 (k4_geometry) で、部材は長さ {SLICE_MM:g} mm の輪切りごとに見る".replace("{SLICE_MM:g}", f"{SLICE_MM:g}"),
    f"触れた所はボルト留めとみる (仮定、出典なし): 触れた面 {AREA_PER_BOLT_MM2:g} mm² ごとに M{BOLT_D_MM:g} の鋼のボルト 1 本 "
    f"(締める長さ {BOLT_GRIP_MM:g} mm、E {BOLT_E_MPA:g} MPa の棒の硬さ E A / L)。3 方向の硬さは同じ。回る向きの硬さは、"
    "その硬さを触れた面に一様にならべた床のばね (正方形の面: 硬さ × 面積 / 12)",
    "carried_by の無い部品 (車輪・サスなど) は、K1 と同じく車輪の側 (ばね下) として入れない。乗員と荷は荷重だけで、"
    "体の当たる物 (座席・荷台) に掛ける",
    "フレームは各支持の frame_node で上下を止め、前後・横は 2 点で止める (タイヤは硬い)",
    "線形の静的解析。弾性の変形を、永久の変形の上限の見積もりとして使う (降伏しない範囲)。ただし網は次の通り",
    f"網の部品 (kind {', '.join(NET_KINDS)}) は板にしない。引っぱりだけを受ける線 (索) の目: 箱のいちばん薄い向き = 線径、"
    "線の断面 (幅あたり、二つの向きそれぞれ) = 質量 / (2 × 密度 × 張れる範囲の面積)。"
    f"張れる範囲 = 網が部材・部品に留まる点 (触れる部材は長さ {NET_CELL_MM:g} mm ごと) の凸包。"
    f"索は {NET_CELL_MM:g} mm ごとと押す点を通り、自分の幅の線をまとめた断面と張りを持つ。縁は 1 本分の索。"
    f"初めの張り = 線の応力 {NET_PRESTRESS_MPA:g} MPa (仮定、出典なし。run の params.net_prestress)。"
    "留め点はボルト 1 本分のばね。索は幾何の非線形 (今の向きの張り T = max(0, T0 + EA ΔL / L0)、ニュートン法で解く) で、"
    "押されると伸びてたわみ (サグ) で受ける。初めの張りは留め点とつり合っているとして荷重に入れない。網の質量は網の節に等分",
    "max_utilisation = 各部材の応力 / (その部材の材料の降伏 / params.safety_factor) の最大。部材ごとに自分の材料で見る",
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
    ts: tuple = ()  # the middles of the member's slices that touch (a net is fastened along them)


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


@dataclass
class Plate:
    """A plate part: its mid-plane (centre, normal, the two in-plane axes and half sizes), thickness, material."""

    pid: str
    center: np.ndarray
    normal: np.ndarray
    inplane: np.ndarray  # (2, 3) rows: the in-plane unit axes
    half: np.ndarray  # (2,) half sizes along them
    t: float  # mm
    E: float  # MPa
    G: float  # MPa
    material: str
    assumed: bool = False  # no part.material (or not in design.materials): PLATE_MATERIAL taken


def plate_parts(design: Record) -> dict[str, Plate]:
    """The modelled parts taken as plates (see PLATE_RATIO); controls (hand, foot) stay rigid bodies, nets
    (NET_KINDS) are cable nets (net_parts)."""
    from ..k4_geometry.obb import from_box

    mats = design.get("materials", {})
    out = {}
    for p in modelled_parts(design):
        if p["kind"] in HAND_KINDS or p["kind"] in FOOT_KINDS or p["kind"] in NET_KINDS:
            continue
        o = from_box(p["box"]["center_mm"], p["box"]["size_mm"], p["box"].get("rpy_deg"))
        k = np.argsort(o.half, kind="stable")
        if o.half[k[0]] > PLATE_RATIO * o.half[k[1]]:
            continue
        key = p.get("material")
        mat = mats.get(key) if key else None
        mat_found = mat is not None
        name = key if mat else PLATE_MATERIAL["name"]
        mat = mat or PLATE_MATERIAL
        E = float(mat["E_MPa"])
        G = float(mat.get("G_MPa") or E / (2.0 * (1.0 + float(mat.get("nu", PLATE_MATERIAL["nu"])))))
        out[p["id"]] = Plate(p["id"], np.asarray(o.center, float), np.asarray(o.axes[k[0]], float),
                             np.asarray(o.axes[k[1:]], float), np.asarray(o.half[k[1:]], float),
                             2.0 * float(o.half[k[0]]), E, G, name, assumed=not mat_found)
    return out


def assumed_material_effect(plates: dict[str, Plate]) -> Record | None:
    """The effect naming the plates whose material was assumed (PLATE_MATERIAL), or None."""
    ids = [pid for pid, pl in plates.items() if pl.assumed]
    if not ids:
        return None
    m = PLATE_MATERIAL
    return {"name": f"part.material の無い板 {len(ids)} 枚は仮に鋼 (E {m['E_MPa']:g} MPa・ν {m['nu']:g}) とみた: "
                    + ", ".join(ids), "value": float(len(ids)), "unit": "1"}


def plate_point(pl: Plate, x) -> np.ndarray:
    """The point of the plate's mid-plane under x (inside the plate's outline)."""
    loc = np.clip(pl.inplane @ (np.asarray(x, float) - pl.center), -pl.half, pl.half)
    return pl.center + loc @ pl.inplane


def strip_section(b: float, t: float) -> dict[str, float]:
    """A solid strip b wide, t thick: A, the out-of-plane I (b t^3/12), the in-plane I (t b^3/12) and J
    (Roark's approximation for a solid rectangle, a c^3 (1/3 - 0.21 c/a (1 - c^4/(12 a^4))), a >= c; not checked)."""
    a, c = max(b, t), min(b, t)
    return {"A": b * t, "I_out": b * t ** 3 / 12.0, "I_in": t * b ** 3 / 12.0,
            "J": a * c ** 3 * (1.0 / 3.0 - 0.21 * c / a * (1.0 - c ** 4 / (12.0 * a ** 4)))}


def strip_widths(pl: Plate, end: np.ndarray, w0: float, n: int = PLATE_ARM_ELEMENTS) -> list[float]:
    """The widths of the n elements of the strip from the plate's centre to `end` (a contact of width w0 there):
    the plate's width across the strip, but at most w0 + 2 s tan(PLATE_SPREAD_DEG), s from `end`."""
    d = end - pl.center
    L = float(np.linalg.norm(d))
    across = np.cross(pl.normal, d / L)
    b_plate = 2.0 * float(np.sum(pl.half * np.abs(pl.inplane @ across)))
    tan = math.tan(math.radians(PLATE_SPREAD_DEG))
    return [min(b_plate, w0 + 2.0 * L * (1.0 - (k + 0.5) / n) * tan) for k in range(n)]


# ------------------------------------------------------------------ nets


@dataclass
class Net:
    """A net part: its plane (centre, normal, the two in-plane axes = the wires' directions, half sizes), the wire's
    diameter d and material."""

    pid: str
    center: np.ndarray
    normal: np.ndarray
    inplane: np.ndarray  # (2, 3)
    half: np.ndarray  # (2,)
    d: float
    E: float
    density: float  # kg/m^3
    mass: float  # kg
    material: str
    assumed: bool = False

    def local(self, x) -> np.ndarray:
        return self.inplane @ (np.asarray(x, float) - self.center)

    def world(self, uv) -> np.ndarray:
        return self.center + np.asarray(uv, float) @ self.inplane


def net_parts(design: Record) -> dict[str, Net]:
    """The modelled parts of a net kind (NET_KINDS)."""
    from ..k4_geometry.obb import from_box

    mats = design.get("materials", {})
    out = {}
    for p in modelled_parts(design):
        if p["kind"] not in NET_KINDS:
            continue
        o = from_box(p["box"]["center_mm"], p["box"]["size_mm"], p["box"].get("rpy_deg"))
        k = np.argsort(o.half, kind="stable")
        key = p.get("material")
        mat = mats.get(key) if key else None
        found = mat is not None
        mat = mat or dict(PLATE_MATERIAL, density_kg_m3=7850.0)
        out[p["id"]] = Net(p["id"], np.asarray(o.center, float), np.asarray(o.axes[k[0]], float),
                           np.asarray(o.axes[[1, 2] if k[0] == 0 else [0, 2] if k[0] == 1 else [0, 1]], float),
                           np.asarray(o.half[[1, 2] if k[0] == 0 else [0, 2] if k[0] == 1 else [0, 1]], float),
                           2.0 * float(o.half[k[0]]), float(mat["E_MPa"]), float(mat.get("density_kg_m3", 7850.0)),
                           float(p.get("mass_kg") or 0.0), key if found else PLATE_MATERIAL["name"], assumed=not found)
    return out


def convex_hull(pts) -> np.ndarray:
    """The convex hull (counter-clockwise, no collinear points) of 2D points (Andrew's monotone chain)."""
    P = sorted({(round(float(x), 6), round(float(y), 6)) for x, y in pts})
    if len(P) < 3:
        return np.array(P, float).reshape(-1, 2)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in P:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 1e-9:
            lower.pop()
        lower.append(p)
    for p in reversed(P):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 1e-9:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1], float)


def polygon_area(poly: np.ndarray) -> float:
    if len(poly) < 3:
        return 0.0
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def inside_polygon(poly: np.ndarray, p, tol: float = 1e-6) -> bool:
    """p inside or on a counter-clockwise convex polygon."""
    if len(poly) < 3:
        return False
    for a, b in zip(poly, np.roll(poly, -1, axis=0)):
        e = b - a
        if e[0] * (p[1] - a[1]) - e[1] * (p[0] - a[0]) < -tol * max(1.0, float(np.linalg.norm(e))):
            return False
    return True


def _chord(poly: np.ndarray, axis: int, c: float) -> tuple[float, float] | None:
    """Where the line {coordinate `axis` = c} crosses a convex polygon: (lo, hi) along the other coordinate."""
    o = 1 - axis
    hits = []
    for a, b in zip(poly, np.roll(poly, -1, axis=0)):
        if (a[axis] - c) * (b[axis] - c) > 0.0 or a[axis] == b[axis]:
            if a[axis] == b[axis] == c:
                hits += [a[o], b[o]]
            continue
        s = (c - a[axis]) / (b[axis] - a[axis])
        hits.append(a[o] + s * (b[o] - a[o]))
    if len(hits) < 2 or max(hits) - min(hits) < 1e-6:
        return None
    return min(hits), max(hits)


def _levels(lo: float, hi: float, h: float, extra) -> list[float]:
    """Line positions strictly between lo and hi: every h, and the extra ones (regular ones within h/4 of an extra
    dropped)."""
    ex = sorted({round(float(e), 6) for e in extra if lo + 1e-6 < e < hi - 1e-6})
    n = int(math.floor((hi - lo) / h))
    reg = [lo + h * k for k in range(1, n + 1) if lo + h * k < hi - h / 4.0]
    reg = [r for r in reg if all(abs(r - e) > h / 4.0 for e in ex)]
    return sorted(reg + ex)


def _widths(levels: list[float], lo: float, hi: float) -> list[float]:
    """The width each line stands for: halfway to its neighbours, the first and the last out to lo and hi."""
    out = []
    for i, x in enumerate(levels):
        a = lo if i == 0 else (levels[i - 1] + x) / 2.0
        b = hi if i == len(levels) - 1 else (x + levels[i + 1]) / 2.0
        out.append(b - a)
    return out


@dataclass
class NetMesh:
    """A net as built: its hull (local u, v), nodes {(u, v) rounded: model node}, its cables (indices into the
    model's net cable group) and the wire area per width a (mm^2/mm) and pretension per width N0 (N/mm)."""

    net: Net
    hull: np.ndarray
    nodes: dict
    a: float
    N0: float
    cables: list = field(default_factory=list)
    border: set = field(default_factory=set)  # the keys of the nodes on the hull's sides


def net_mesh(net: Net, anchors: list, points: list, prestress: float, h: float = NET_CELL_MM):
    """The lines of a net between its fastenings. anchors: local (u, v) points; points: local load points.
    Returns (NetMesh, [(u, v)] node points, cable list [(key a, key b, A, T0)], [(anchor index, key)]) or None when
    the fastenings do not span an area (fewer than three not on a line)."""
    hull = convex_hull(anchors)
    area = polygon_area(hull)
    if len(hull) < 3 or area <= h * h * 1e-3:
        return None
    a = net.mass / (2.0 * net.density * 1e-9 * area) if net.mass > 0 else 0.0
    if a <= 0.0:
        return None
    N0 = prestress * a
    lo, hi = hull.min(axis=0), hull.max(axis=0)
    pts = [p for p in points if inside_polygon(hull, p)]
    lv = [_levels(lo[k], hi[k], h, [p[k] for p in pts]) for k in (0, 1)]
    wd = [_widths(lv[k], lo[k], hi[k]) for k in (0, 1)]
    keys: dict = {}

    def key(u, v):
        k = (round(float(u), 6), round(float(v), 6))
        keys.setdefault(k, len(keys))
        return k

    cables = []
    for axis in (0, 1):  # lines of constant coordinate `axis`, running along the other one
        for c, w in zip(lv[axis], wd[axis]):
            ch = _chord(hull, axis, c)
            if ch is None:
                continue
            run = [ch[0]] + [x for x in lv[1 - axis] if ch[0] + 1e-6 < x < ch[1] - 1e-6] + [ch[1]]
            ks = [key(*((c, x) if axis == 0 else (x, c))) for x in run]
            cables += [(p, q, a * w, N0 * w) for p, q in zip(ks[:-1], ks[1:])]
    # the border: along each side of the hull through every node on it (the hull's corners are fastenings)
    for i in range(len(hull)):
        key(*hull[i])
    allk = list(keys)
    border = set()
    for i in range(len(hull)):
        p, q = hull[i], hull[(i + 1) % len(hull)]
        e = q - p
        L = float(np.linalg.norm(e))
        on = []
        for k in allk:
            r = np.array(k) - p
            s = float(r @ e) / L
            if -1e-6 <= s <= L + 1e-6 and abs(e[0] * r[1] - e[1] * r[0]) / L <= 1e-5:
                on.append((s, k))
        on.sort()
        border.update(k for _, k in on)
        cables += [(k1, k2, a * h, N0 * h) for (_, k1), (_, k2) in zip(on[:-1], on[1:])]
    anchor_keys = []
    for j, p in enumerate(anchors):
        k = (round(float(p[0]), 6), round(float(p[1]), 6))
        if k not in keys:  # inside the hull or between nodes of a side: the nearest node
            k = min(keys, key=lambda q: (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2)
        anchor_keys.append((j, k))
    return NetMesh(net, hull, {}, a, N0, border=border), list(keys), cables, anchor_keys


class Cable(ElementGroup):
    """Tension-only cables (2 nodes, 3 DOFs each) of axial stiffness EA / L0 and pretension T0, about the present
    shape (disp, the nodes' movements): T = max(0, T0 + EA (L - L0) / L0) along the present direction e; the
    tangent stiffness EA / L0 e e^T (taut) + T / L (I - e e^T). Internal force T e - T0 e0 (the pretension as
    fitted is in balance with the fastenings and loads nothing)."""

    kind = "cable"
    n_comp = 3

    def __init__(self, conn, EA, T0, nodes: np.ndarray, A=None, name: str = "nets"):
        self.conn = np.asarray(conn, int).reshape(-1, 2)
        self.EA = np.asarray(EA, float)
        self.A = np.asarray(A, float) if A is not None else None  # mm^2, for the stress T / A
        self.T0 = np.asarray(T0, float)
        d0 = nodes[self.conn[:, 1]] - nodes[self.conn[:, 0]]
        self.L0 = np.linalg.norm(d0, axis=1)
        self.e0 = d0 / self.L0[:, None]
        self.disp = np.zeros((len(self.conn), 2, 3))
        self.name = name

    def state(self, nodes: np.ndarray):
        """(L, e, T, taut): T the force along e; a slack cable (T0 + EA (L - L0) / L0 <= 0) pushes SLACK_FLOOR of
        that (a millionth: tension-only, kept continuous so that Newton's method settles)."""
        d = nodes[self.conn[:, 1]] + self.disp[:, 1] - nodes[self.conn[:, 0]] - self.disp[:, 0]
        L = np.linalg.norm(d, axis=1)
        e = d / L[:, None]
        T = self.T0 + self.EA * (L - self.L0) / self.L0
        taut = T > 0.0
        return L, e, np.where(taut, T, SLACK_FLOOR * T), taut

    def stiffness(self, nodes: np.ndarray) -> np.ndarray:
        L, e, T, taut = self.state(nodes)
        ee = e[:, :, None] * e[:, None, :]
        # slack: SLACK_FLOOR of the axial stiffness, and SLACK_FLOOR EA across so that the step stays defined
        Tg = np.where(taut, T, SLACK_FLOOR * self.EA)
        Kb = (np.where(taut, 1.0, SLACK_FLOOR)[:, None, None] * (self.EA / self.L0)[:, None, None] * ee
              + (Tg / L)[:, None, None] * (np.eye(3)[None] - ee))
        return np.block([[Kb, -Kb], [-Kb, Kb]])

    def forces(self, nodes: np.ndarray) -> np.ndarray:
        """(E, 6): the internal forces on the two ends, T e - T0 e0 at b and its opposite at a."""
        _, e, T, _ = self.state(nodes)
        f = T[:, None] * e - self.T0[:, None] * self.e0
        return np.hstack([-f, f])

    def tensions(self, nodes: np.ndarray) -> np.ndarray:
        return np.maximum(self.state(nodes)[2], 0.0)


def net_effect(lp, prestress: float) -> Record:
    """The effect describing the nets as built: wire, the wire area per width and the pitch it means, the
    pretension, the fastenings, the taut area; and the largest wire stress of the loads solved."""
    parts = []
    for pid, m in lp.nets.items():
        n = m.net
        pitch = math.pi * n.d ** 2 / 4.0 / m.a
        parts.append(f"{pid}: 線径 {n.d:g} mm ({n.material})、線の断面 {m.a:.4f} mm²/mm (目 {pitch:.0f} mm 相当)、"
                     f"初めの張り {m.N0:.2f} N/mm (線の応力 {prestress:g} MPa)、張れる範囲 {polygon_area(m.hull) / 1e6:.3f} m²、"
                     f"索 {len(m.cables)} 本")
    top = max(lp.net_stress) if lp.net_stress else 0.0
    return {"name": "網は引っぱりだけの索 (幾何の非線形、ニュートン法) — " + "; ".join(parts)
                    + (f"。網の線のいちばん大きな応力 {top:.0f} MPa" if lp.net_stress else ""),
            "value": top, "unit": "MPa"}


def newton_solve(model: FEModel, cables: Cable, F: np.ndarray, tol: float = NET_NEWTON_TOL,
                 iterations: int = NET_NEWTON_ITER) -> tuple[np.ndarray, object]:
    """Displacements (N, 6) under F (N, 6) of a model whose only non-linear group is `cables` (Newton's method on
    the cables' present shape, load in steps when a step does not converge). Returns (U, layout)."""
    first = True
    for steps in (1, 4, 16):
        U = np.zeros((len(model.nodes), 6))
        ok = True
        for s in range(1, steps + 1):
            Fs = F * (s / steps)
            for it in range(iterations):
                cables.disp = np.stack([U[cables.conn[:, 0], :3], U[cables.conn[:, 1], :3]], axis=1)
                model._k_cache = None  # the cables' stiffness changed with their shape
                try:
                    solver = StaticSolver(model, check_rigid=first)
                except ModelError as e:
                    if first:  # the model as built cannot be solved
                        raise DesignError(f"the load path model cannot be solved: {e}") from None
                    ok = False
                    break
                first = False
                lay = solver.layout
                dofs = lay.element_dofs(cables.conn, 3)
                u = lay.from_node_array(U)
                Kc = cables.stiffness(model.nodes)
                fc = cables.forces(model.nodes)
                ue = u[dofs]
                lin = solver.K @ u  # every group's tangent times u
                np.add.at(lin, dofs, -np.einsum("eij,ej->ei", Kc, ue))  # less the cables' tangent part
                np.add.at(lin, dofs, fc)  # plus the cables' own forces
                r = lay.from_node_array(Fs) - lin
                try:
                    dU = solver.solve(lay.node_array(r)).displacements
                except ModelError:
                    ok = False
                    break
                step = float(np.abs(dU[:, :3]).max())
                U = U + dU * min(1.0, NET_STEP_MM / step) if step > 0.0 else U
                if step <= tol * max(1.0, float(np.abs(U[:, :3]).max())):
                    break
            else:
                ok = False
            if not ok:
                break
        if ok:
            cables.disp = np.stack([U[cables.conn[:, 0], :3], U[cables.conn[:, 1], :3]], axis=1)
            model._k_cache = None
            return U, lay
    raise DesignError("the nets' cables did not settle (Newton's method did not converge in 16 load steps)")


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
    return {mid: _slices_of(bs, mid, mp) for mid, mp in member_props(design).items()}


def _slices_of(bs: dict, mid: str, mp: Record) -> list[tuple[float, k4.Body]]:
    """One member's slices (member_slices)."""
    whole = bs[f"member:{mid}"].obb
    w, h = 2.0 * whole.half[2], 2.0 * whole.half[1]
    n = max(1, int(math.ceil(mp["length"] / SLICE_MM)))
    a, b = mp["a"], mp["b"]
    return [((k + 0.5) / n, k4.Body(f"member:{mid}", from_member(a + (b - a) * k / n, a + (b - a) * (k + 1) / n,
                                                                 w, h, mid), "member"))
            for k in range(n)]


def find_contacts(design: Record, touch: float = TOUCH_MM) -> list[Contact]:
    """Every contact between a modelled part and a member or another modelled part."""
    bs = k4.bodies(design)
    parts = [f"part:{p['id']}" for p in modelled_parts(design)]
    props = member_props(design)
    # a member's slices are made when a part first comes near the whole member (the same slices member_slices gives)
    slices: dict[str, list[tuple[float, k4.Body]] | None] = dict.fromkeys(props)
    pinned: dict[str, set[str]] = {}
    for p in design.get("parts", []):
        for c in p.get("connects_to", []):
            pinned.setdefault(f"part:{p['id']}", set()).add(c.get("to", ""))
            pinned.setdefault(f"part:{c.get('to', '')}", set()).add(p["id"])
    pin_ended = {f"part:{p['id']}" for p in design.get("parts", []) if p.get("kind") in PIN_ENDED_KINDS}

    def bears(a: str, b: str) -> bool:
        for x, y in ((a, b), (b, a)):
            if x in pin_ended and y.split(":", 1)[1] not in pinned.get(x, set()):
                return False
        return True

    out: list[Contact] = []
    rad = {k: _radius(b.obb) for k, b in bs.items()}  # each body's radius once (the tests below compare many pairs)
    slice_rad: dict[str, list[float]] = {}
    for i, pk in enumerate(parts):
        P = bs[pk]
        rp = rad[pk]
        for mid, sl in slices.items():
            if not bears(pk, f"member:{mid}"):
                continue
            M = bs[f"member:{mid}"]
            if np.linalg.norm(P.obb.center - M.obb.center) > rp + rad[f"member:{mid}"] + touch:
                continue
            if k4.shape_overlap(P, M) < -touch:
                continue
            if sl is None:
                sl = slices[mid] = _slices_of(bs, mid, props[mid])
                slice_rad[mid] = [_radius(s.obb) for _, s in sl]
            hit = [(t, s) for (t, s), rs in zip(sl, slice_rad[mid])
                   if np.linalg.norm(P.obb.center - s.obb.center) <= rp + rs + touch and k4.shape_overlap(P, s) >= -touch]
            if not hit:
                continue
            t = float(np.mean([t for t, _ in hit]))
            mp = props[mid]
            point = mp["a"] + (mp["b"] - mp["a"]) * t
            width = 2.0 * float(min(M.obb.half[1], M.obb.half[2]))
            area = len(hit) * mp["length"] / len(sl) * width
            k_t, k_r, _ = contact_stiffness(area)
            out.append(Contact(pk, f"member:{mid}", point, area, k_t, k_r, t, tuple(sorted(t for t, _ in hit))))
        for qk in parts[i + 1:]:
            if not bears(pk, qk):
                continue
            Q = bs[qk]
            if np.linalg.norm(P.obb.center - Q.obb.center) > rp + rad[qk] + touch:
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
    plates: dict[str, Plate] = field(default_factory=dict)
    plate_nodes: dict[str, dict[tuple, int]] = field(default_factory=dict)  # plate -> {mid-plane point: node}
    plate_beams: Beam | None = None
    nets: dict[str, NetMesh] = field(default_factory=dict)
    net_cables: Cable | None = None
    slack_nets: list[str] = field(default_factory=list)  # nets whose fastenings span no area (not built)
    net_stress: list = field(default_factory=list)  # the nets' largest wire stress (MPa) of each load solved

    def net_node(self, pid: str, x) -> int | None:
        """The node of net pid at the point of its plane under x, None when x is outside where the net is taut
        (the hull of its fastenings) or no node was made there (build's plate_points)."""
        m = self.nets[pid]
        uv = m.net.local(x)
        if not inside_polygon(m.hull, uv):
            return None
        return m.nodes.get((round(float(uv[0]), 6), round(float(uv[1]), 6)))

    def net_node_ids(self, pid: str) -> list[int]:
        return list(self.nets[pid].nodes.values())

    def node_on(self, pid: str, x) -> int:
        """The node a load at x on part pid goes to: a rigid part's node, or the plate's node under x (its centre
        when x is within a thickness of it; else the nearest of its nodes)."""
        if pid not in self.plates:
            return self.part_node[pid]
        pl = self.plates[pid]
        y = plate_point(pl, x)
        if np.linalg.norm(y - pl.center) <= pl.t:
            return self.part_node[pid]
        nodes = self.plate_nodes.get(pid, {})
        key = tuple(np.round(y, 6))
        if key in nodes:
            return nodes[key]
        ids = [self.part_node[pid], *nodes.values()]
        return ids[int(np.argmin(np.linalg.norm(self.model.nodes[ids] - y, axis=1)))]


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
          holds: dict[str, str] | None = None, plate_points: dict[str, list] | None = None,
          net_prestress: float = NET_PRESTRESS_MPA) -> LoadPathModel:
    """The beam-and-rigid-body model; parts in `exclude` (and their contacts) left out. plate_points: points
    loads will be put on, per plate part (each gets its own node and strip; a point load's width there is
    taken as the plate's thickness) or net part (its lines pass through them). net_prestress: the nets' wire
    stress as fitted (MPa)."""
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
    nets = {pid: n for pid, n in net_parts(design).items() if pid not in exclude}
    # each net's fastenings: (local point, the other body, t along a member or None); a member the net touches
    # holds it at points about NET_CELL_MM apart along the touching length
    fast: dict[str, list] = {pid: [] for pid in nets}
    net_contacts = []
    for c in live:
        ida, idb = c.a.split(":", 1)[1], c.b.split(":", 1)[1]
        mine = [(ida, c.b)] if ida in nets else []
        mine += [(idb, c.a)] if c.b.startswith("part:") and idb in nets else []
        if not mine:
            continue
        net_contacts.append(c)
        for pid, other in mine:
            n = nets[pid]
            if other.startswith("member:") and c.ts:
                mp = props[other.split(":", 1)[1]]
                keep, last = [], None
                for t in c.ts:
                    if last is None or (t - last) * mp["length"] >= NET_CELL_MM:
                        keep.append(t)
                        last = t
                if keep[-1] != c.ts[-1]:
                    keep.append(c.ts[-1])
                for t in keep:  # for the hull only (build fastens each border node beside the member)
                    uv = np.clip(n.local(mp["a"] + (mp["b"] - mp["a"]) * t), -n.half, n.half)
                    fast[pid].append((uv, other, round(t, 9)))
            elif other.startswith("part:") and other.split(":", 1)[1] not in nets:
                fast[pid].append((np.clip(n.local(c.point), -n.half, n.half), other, None))
    netc = {id(c) for c in net_contacts}
    pending: dict[str, tuple] = {}
    slack: list[str] = []
    for pid, n in nets.items():
        loads = [n.local(x) for x in (plate_points or {}).get(pid, [])]
        built = net_mesh(n, [f[0] for f in fast[pid]], loads, net_prestress)
        if built is None:
            slack.append(pid)
            continue
        mesh, keys, cables, anchor_keys = built
        # the fastenings: a part's where it touches; a member's at every border node beside the length it touches
        # (a laced edge holds each wire's end)
        held = [(q, fast[pid][j][1], None) for j, q in anchor_keys if fast[pid][j][2] is None]
        for c in net_contacts:
            other = c.b if c.a == f"part:{pid}" else c.a if c.b == f"part:{pid}" else None
            if other is None or not other.startswith("member:") or not c.ts:
                continue
            mp = props[other.split(":", 1)[1]]
            ab = mp["b"] - mp["a"]
            L2 = float(ab @ ab)
            pad = SLICE_MM / mp["length"]
            # the member's width (find_contacts: area = slices x slice length x width)
            reach = c.area / (len(c.ts) * mp["length"] / max(1, int(math.ceil(mp["length"] / SLICE_MM))))
            for q in sorted(mesh.border):
                x = n.world(q)
                t = float((x - mp["a"]) @ ab) / L2
                if not min(c.ts) - pad <= t <= max(c.ts) + pad:
                    continue
                near = n.local(mp["a"] + ab * min(1.0, max(0.0, t)))
                if float(np.linalg.norm(np.clip(near, -n.half, n.half) - np.asarray(q))) <= reach + n.d + 1e-6:
                    t = round(min(1.0, max(0.0, t)), 9)
                    held.append((q, other, t))
                    cuts[other.split(":", 1)[1]].add(t)
        pending[pid] = (mesh, keys, cables, held)
    for c in live:
        if c.t is not None and id(c) not in netc:
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
        if p["id"] in exclude or p["id"] in nets:
            continue
        i = model.add_node(p["box"]["center_mm"])
        part_node[p["id"]] = i
        model.rotation_nodes.add(i)
    plates = {pid: pl for pid, pl in plate_parts(design).items() if pid not in exclude}
    plate_nodes: dict[str, dict[tuple, int]] = {pid: {} for pid in plates}
    strips: dict[str, list] = {k: [] for k in ("conn", "E", "G", "A", "Iy", "Iz", "J", "ref")}

    def body_node(pid: str, x, w0: float) -> int:
        """A rigid part's node, or the plate's node under x (a new strip from the centre when it is new)."""
        if pid not in plates:
            return part_node[pid]
        pl = plates[pid]
        y = plate_point(pl, x)
        if np.linalg.norm(y - pl.center) <= pl.t:
            return part_node[pid]
        key = tuple(np.round(y, 6))
        if key not in plate_nodes[pid]:
            n = PLATE_ARM_ELEMENTS
            ids = [part_node[pid]] + [model.add_node(pl.center + (y - pl.center) * (j + 1) / n) for j in range(n)]
            for j, b in enumerate(strip_widths(pl, y, w0, n)):
                s = strip_section(b, pl.t)
                strips["conn"].append((ids[j], ids[j + 1]))
                # local y along the plate's normal: Iz (bending in local x-y) is the out-of-plane bending
                for name, v in (("E", pl.E), ("G", pl.G), ("A", s["A"]), ("Iy", s["I_in"]), ("Iz", s["I_out"]),
                                ("J", s["J"]), ("ref", pl.normal)):
                    strips[name].append(v)
            plate_nodes[pid][key] = ids[-1]
        return plate_nodes[pid][key]

    for pid, pts in (plate_points or {}).items():
        if pid in plates:
            for x in pts:
                body_node(pid, x, plates[pid].t)
    # the nets: their lines and border as cables, each fastening a spring of one bolt to its body
    sconn, k, pts = [], [], []
    meshes: dict[str, NetMesh] = {}
    cab = {"conn": [], "EA": [], "T0": [], "A": []}
    k_fast, kr_fast, _ = contact_stiffness(AREA_PER_BOLT_MM2)
    for pid, (mesh, keys, cables, held) in pending.items():
        n = nets[pid]
        ids = model.add_nodes([n.world(q) for q in keys])
        mesh.nodes = {q: int(i) for q, i in zip(keys, ids)}
        for q, i in mesh.nodes.items():
            lumps.append((i, n.mass / len(mesh.nodes)))
        for p_, q_, A_, T_ in cables:
            mesh.cables.append(len(cab["conn"]))
            cab["conn"].append((mesh.nodes[p_], mesh.nodes[q_]))
            cab["EA"].append(n.E * A_)
            cab["T0"].append(T_)
            cab["A"].append(A_)
        for q, other, t in held:
            i = mesh.nodes[q]
            if t is not None:
                b = dict((round(tt, 9), ii) for tt, ii in member_nodes[other.split(":", 1)[1]])[t]
            else:
                b = body_node(other.split(":", 1)[1], model.nodes[i], math.sqrt(AREA_PER_BOLT_MM2))
            sconn.append((i, b))
            k.append([k_fast] * 3 + [kr_fast] * 3)
            pts.append(model.nodes[i].copy())
        meshes[pid] = mesh
    net_cables = None
    if cab["conn"]:
        net_cables = Cable(np.array(cab["conn"]), cab["EA"], cab["T0"], model.nodes, A=cab["A"])
        model.add(net_cables)
    springs = None
    live_rest = [c for c in live if id(c) not in netc]
    if live_rest or sconn:
        for c in live_rest:
            w0 = math.sqrt(c.area)
            a = body_node(c.a.split(":", 1)[1], c.point, w0)
            if c.t is None:
                b = body_node(c.b.split(":", 1)[1], c.point, w0)
                p = c.point
            else:
                b = dict((round(t, 9), i) for t, i in member_nodes[c.b.split(":", 1)[1]])[round(c.t, 9)]
                p = model.nodes[b]
            sconn.append((a, b))
            k.append([c.k_t] * 3 + [c.k_r] * 3)
            pts.append(p)
        springs = Spring(np.array(sconn), np.array(k), points=np.array(pts), rotations=True, name="contacts")
        model.add(springs)
    plate_beams = None
    if strips["conn"]:
        plate_beams = Beam(np.array(strips["conn"]), strips["E"], strips["G"], strips["A"], strips["Iy"], strips["Iz"],
                           strips["J"], ref=np.array(strips["ref"]), name="plates")
        model.add(plate_beams)
    for nid, comps in (holds if holds is not None else default_holds(design)).items():
        if nid not in node_of:
            raise DesignError(f"support node {nid!r} is not on any member, so it cannot hold the frame")
        model.fix(node_of[nid], comps if comps == "all" else "".join(sorted(set(comps))))
    mass = np.zeros(len(model.nodes))
    for i, m in lumps:
        mass[i] += m
    return LoadPathModel(design, model, beams, elem_member, node_of, member_nodes, part_node, live, springs, mass, props,
                         plates, plate_nodes, plate_beams, meshes, net_cables, slack)


def free_parts(lp: LoadPathModel) -> list[str]:
    """Part ids the supports and contacts leave free to move (fe.precheck.rigid_body_check, by connected group).
    A free group that holds members is an error of the design (the frame itself is not held)."""
    rb = rigid_body_check(lp.model)
    if rb["free_rigid_modes"] == 0:
        return sorted(lp.slack_nets)
    labels = lp.model.connectivity_groups()
    if rb["groups"] == 1:
        raise DesignError(f"the frame is free to move ({', '.join(rb['free_motions'])}); its supports do not hold it")
    groups = {int(m.group(1)) for m in (re.match(r"group (\d+) ", s) for s in rb["free_motions"]) if m}
    frame = {int(labels[i]) for i in lp.node_of.values()}
    if groups & frame:
        loose = sorted({lp.elem_member[e] for e, (i, _) in enumerate(lp.beams.conn) if int(labels[i]) in groups})
        raise DesignError(f"members {', '.join(loose[:6])} are not joined to the held frame; join them")
    return sorted([pid for pid, i in lp.part_node.items() if int(labels[i]) in groups]
                  + [pid for pid, m in lp.nets.items() if int(labels[next(iter(m.nodes.values()))]) in groups]
                  + list(lp.slack_nets))


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
    """Displacements (N, 6) for each load array (N, 6), one factorisation; with nets, each load by
    newton_solve (the nets' largest wire stress of each load in lp.net_stress)."""
    if lp.net_cables is not None:
        out, lay = [], None
        lp.net_stress = []
        A = lp.net_cables.A
        for F in loads:
            U, lay = newton_solve(lp.model, lp.net_cables, F)
            out.append(U)
            lp.net_stress.append(float(np.max(lp.net_cables.tensions(lp.model.nodes) / A)))
        lp.net_cables.disp[:] = 0.0
        lp.model._k_cache = None
        return out, lay
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


def occupant_cg(design: Record, trunk: Record) -> np.ndarray:
    """The occupant's centre of gravity: the mass-weighted centre of its trunk and its segments (parts
    <trunk id>.<segment>); the trunk's centre when the parts carry no mass."""
    own = [p for p in design.get("parts", []) if p["id"] == trunk["id"] or p["id"].startswith(trunk["id"] + ".")]
    m = sum(float(p.get("mass_kg") or 0.0) for p in own)
    if m <= 0.0:
        return np.asarray(trunk["box"]["center_mm"], float)
    return sum(float(p.get("mass_kg") or 0.0) * np.asarray(p["box"]["center_mm"], float) for p in own) / m


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
    """The first member or modelled part a ray from `point` along `out` meets: (body id, distance). Nets held by
    quick-release clips and doors with a hinge and a latch (k4_geometry.entry.openable_parts) and their fittings are
    passed over: a guard the occupant has to put in place does not count as the side guard (the US CPSC's proposed
    rule for ROVs, 79 FR 68964, sec. 1422.5(c)(4), read by the intake department: such barriers are taken off
    before the shoulder and hip tests)."""
    bs = k4.bodies(design)
    allowed = {f"part:{p['id']}" for p in modelled_parts(design)} | {k for k in bs if k.startswith("member:")}
    allowed -= {f"part:{pid}" for pid in openable_parts(design)}
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
    points = _push_plate_points(design) if kind == "push" else {}
    pre = float(values.get("net_prestress") or NET_PRESTRESS_MPA)
    # The push takes openable nets and doors as open (CPSC proposed rule sec. 1422.5(c)(4), see first_outboard):
    # they are pushed on by nobody and do not stiffen the frame either (the unfavourable side).
    opened = frozenset(openable_parts(design)) if kind == "push" else frozenset()
    full = build(design, contacts, exclude=opened, holds=holds, plate_points=points, net_prestress=pre)
    free = free_parts(full)
    lp = (build(design, contacts, exclude=opened | frozenset(free), holds=holds, plate_points=points,
                net_prestress=pre) if free else full)
    parts = {p["id"]: p for p in design.get("parts", [])}
    effects: list[Record] = []
    assumed = assumed_material_effect(full.plates)  # the free plates too
    if assumed is not None:
        effects.append(assumed)
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
            results += _stress_results(lp, Us, lay, safety_factor=float(values.get("safety_factor") or 1.0))
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
            results += _stress_results(lp, Us, lay, safety_factor=float(values.get("safety_factor") or 1.0))
    elif kind == "inertia":
        loads, names = _inertia_loads(design, lp, values, on_part, vectors)
        Us, lay = solve_cases(lp, loads)
        worst, where, combo = 0.0, None, None
        for name, U in zip(names, Us):
            for pid in lp.nets:
                for i in lp.net_node_ids(pid):
                    v = float(np.linalg.norm(U[i, :3]))
                    if v > worst:
                        worst, where, combo = v, pid, name
            for pid, node in lp.part_node.items():
                if pid in lp.plates:  # a plate is not rigid: its nodes' movements
                    for i in [node, *lp.plate_nodes[pid].values()]:
                        v = float(np.linalg.norm(U[i, :3]))
                        if v > worst:
                            worst, where, combo = v, pid, name
                    continue
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
        results += _stress_results(lp, Us, lay, names, float(values.get("safety_factor") or 1.0), effects)
    elif kind == "push":
        results += _push(design, lp, values, on_part, vectors, effects)
    else:
        raise DesignError(f"load_kind must be one of {', '.join(LOAD_KINDS)}, got {kind!r}")
    if lp.nets:
        effects.append(net_effect(lp, pre))
    # the contract's unsupported_load: the load points with no path to the frame (a floating control, a point R
    # with nothing outboard to take the push, or a free part's own weight under the inertia loads); the judge makes
    # it a failing verdict
    if kind in ("hand", "foot"):
        unsupported = list(dict.fromkeys(lost))
    elif kind == "push":
        opened = next(r for r in results if r["quantity"] == "occupant_push_open")
        unsupported = [w.strip() for w in (opened.get("where") or "").split(",") if w.strip()] if opened["value"] else []
        unsupported = unsupported if len(unsupported) == int(opened["value"]) else [opened.get("where")] * int(opened["value"])
    elif kind == "inertia":
        # the weight and the accelerations of a free part with mass reach no member: a load with no path
        unsupported = [pid for pid in free if float((parts.get(pid) or {}).get("mass_kg") or 0.0) > 0.0]
        if unsupported:
            effects.append({"name": "浮いた部品の荷重は載っていない (応力は、枠まで繋がった部品の荷重だけで出した): "
                                    + ", ".join(unsupported), "value": float(len(unsupported)), "unit": "1"})
    else:
        unsupported = []
    # given at 0 too: the analysis then counts as one that looked for the path (docs/contract.md 手計算の役目)
    results.append(result("unsupported_load", len(unsupported), "1", _where(unsupported) if unsupported else None))
    loaded = [pid for pid in dict.fromkeys(lost) if pid in free]  # the free parts this load case loads, named first
    results.insert(0, result("free_parts", len(free), "1", _where(loaded + [p for p in free if p not in loaded])))
    for pid in dict.fromkeys(lost):
        effects.append({"name": f"{pid} はどこにも繋がらないので、{pid} に掛かる荷重は枠まで届かない (この荷重は解析に入れていない)"})
    return {"results": results, "effects": effects, "vectors": vectors, "contacts": lp.contacts, "free": free}


def _where(ids: list[str]) -> str | None:
    if not ids:
        return None
    return ", ".join(ids[:MAX_WHERE]) + (f" (ほか {len(ids) - MAX_WHERE})" if len(ids) > MAX_WHERE else "")


def utilisations(lp: LoadPathModel, stress: dict[str, float], safety_factor: float) -> dict[str, tuple[float, float]]:
    """{member: (utilisation, allowable MPa)}: von Mises stress / (the member's own material yield / safety_factor).
    Members whose material has no yield_MPa are left out."""
    out = {}
    for mid, s in stress.items():
        y = lp.props[mid]["material"].get("yield_MPa")
        if y is None or float(y) <= 0.0:
            continue
        allow = float(y) / safety_factor
        out[mid] = (s / allow, allow)
    return out


def _stress_results(lp: LoadPathModel, Us: list[np.ndarray], lay, names: list[str] | None = None,
                    safety_factor: float = 1.0, effects: list | None = None) -> list[Record]:
    worst: dict[str, float] = {}
    for U in Us:
        for mid, v in member_stresses(lp, U, lay).items():
            worst[mid] = max(worst.get(mid, 0.0), v)
    top = max(worst, key=worst.get)
    out = [result("max_stress", worst[top], "MPa", top)]
    util = utilisations(lp, worst, safety_factor)
    if util:
        u = max(util, key=lambda m: util[m][0])
        out += [result("max_utilisation", util[u][0], "1", u), result("allowable_stress", util[u][1], "MPa", u)]
        if effects is not None:
            mat = lp.props[u]["material"]
            effects.append({"name": f"許しにいちばん近い部材は {u}: 応力 {worst[u]:.1f} MPa / 許し {util[u][1]:.1f} MPa "
                                    f"({mat.get('name', '?')} の降伏 {float(mat['yield_MPa']):g} MPa / 安全率 "
                                    f"{safety_factor:g}{'' if mat.get('verified') else '、降伏は確かめていない'})",
                            "value": util[u][0], "unit": "1"})
    return out


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
            rests.append((seat, occupant_cg(design, t), occ_m, np.array([f_lon, f_lat, 0.0])))
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


def _push_plate_points(design: Record) -> dict[str, list[np.ndarray]]:
    """The points the push puts its loads on that are on plate or net parts (they get their own nodes)."""
    plates = {**plate_parts(design), **net_parts(design)}
    occupants = {f"part:{p['id']}" for p in design.get("parts", []) if p["kind"] in RIDER_KINDS}
    out: dict[str, list[np.ndarray]] = {}
    for t in occupant_trunks(design):
        for _, x, outward in point_r(t):
            hit = first_outboard(design, x, outward, occupants)
            if hit is not None and hit[0].startswith("part:") and hit[0].split(":", 1)[1] in plates:
                out.setdefault(hit[0].split(":", 1)[1], []).append(x + hit[1] * outward)
    return out


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
            elif body.split(":", 1)[1] in lp.nets:
                node = lp.net_node(body.split(":", 1)[1], at)
                if node is None:
                    open_points.append(label)
                    effects.append({"name": f"{label}: {body} の箱には当たるが、網の張れる範囲 (留め点の凸包) の外"})
                    continue
                at = lp.model.nodes[node].copy()  # on the net's plane (a net node has no rotations)
            else:
                node = on_part(body.split(":", 1)[1])
                if node is None:
                    open_points.append(label)
                    continue
                node = lp.node_on(body.split(":", 1)[1], at)
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
        out_results += _stress_results(lp, Us, lay, safety_factor=float(values.get("safety_factor") or 1.0))
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
                            "contact spring P/k_t + P e^2/k_r",
                            case_ja=(f"片持ちの角パイプ {rect['dims_mm']['b']:g}x{rect['dims_mm']['h']:g}x"
                                     f"{rect['dims_mm']['t']:g}、長さ {L / 1e3:g} m。先に載せた変形しない箱を、箱の中心で "
                                     f"{P / 1e3:g} kN 下へ押す"),
                            source_ja=("片持ち梁の接点 (根元から a) に力 P と回す力 P e: v = P a^3/(3EI) + M a^2/(2EI)、"
                                       "theta = P a^2/(2EI) + M a/(EI)。箱の分 theta e と、接点のばねの分 P/k_t + P e^2/k_r "
                                       "を足す")))

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
                            "(the load passes through every contact point: no moment)",
                            case_ja=(f"両端を固定した長さ {L / 1e3:g} m の梁の真ん中に箱を {len(boxes)} つ積み、その列を通して "
                                     f"{P / 1e3:g} kN を下へ"),
                            source_ja=(f"直列につないだばねと梁: P L^3/(192 E I) + {len(cs)} か所の接点の P/k_t の和 "
                                       "(荷重はどの接点も通るので、回す力は無い)")))

    # 3. floating bodies are found: one box alone, and two boxes touching each other but nothing else
    d = _bare({"A": (0.0, 0.0, 0.0), "B": (L, 0.0, 0.0)}, [("m", "A", "B")],
              [_box("held", (500.0, 0.0, 65.0), (100.0, 40.0, 50.0)), _box("alone", (500.0, 0.0, 400.0), (50.0, 50.0, 50.0)),
               _box("pair1", (200.0, 300.0, 300.0), (50.0, 50.0, 50.0)),
               _box("pair2", (250.0, 300.0, 300.0), (50.0, 50.0, 50.0))], rect)
    lp = build(d, find_contacts(d), holds={"A": "all"})
    free = free_parts(lp)
    free_ja = "梁に載った箱、どこにも触れない箱、互いにだけ触れる箱 2 つ"
    out.append(check_record("a box on a beam, a box alone and two boxes touching only each other", "free_parts",
                            3.0, float(len(free)), "1", 0.0, "the box alone and the touching pair are free rigid bodies "
                            "(fe.precheck.rigid_body_check); the box on the beam is held",
                            case_ja=free_ja,
                            source_ja=("どこにも触れない箱と、触れ合う 2 つの箱は、支えの無い剛体 (fe.precheck.rigid_body_check)。"
                                       "梁に載った箱は支えられている")))
    out.append(check_record("a box on a beam, a box alone and two boxes touching only each other", "held_box_free",
                            0.0, float("held" in free), "1", 0.0, "the box on the beam is not free",
                            case_ja=free_ja, source_ja="梁に載った箱は浮いていない"))

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
    sq_sec = sections.properties(sq)
    r_g = math.sqrt(sq_sec["Iz"] / sq_sec["A"])  # the tube's radius of gyration
    tilt_ja = (f"てこの法則: post1 が W (span - x)/span、post2 が W x/span を受ける。柱の縮み方の違いで板が傾き、柱を曲げる。"
               f"その分は、柱の端の回る硬さと、軸の硬さ × span^2 の比ほどで、4 r^2/span^2 = "
               f"{100.0 * 4.0 * r_g ** 2 / span ** 2:.2f} % (r は管の断面二次半径 {r_g:.1f} mm)")
    for post, share in (("post1", (span - xc) / span), ("post2", xc / span)):
        e = min(i for i, mid in enumerate(lp.elem_member) if mid == post)  # the bottom element (the posts weigh 0)
        out.append(check_record(f"a rigid 50 kg slab on two posts {span:g} mm apart, its centre {xc:g} mm from post1",
                                f"{post}_load", W * share, -float(N_ax[e]), "N", 1e-3,
                                "lever rule: post1 carries W (span - x)/span, post2 W x/span. The slab tilts by the "
                                "posts' different shortening and bends them; that share is about the ratio of a post's "
                                "end rotation stiffness to its axial stiffness times span^2, 4 r^2/span^2 = 0.02 % "
                                "(r the tube's radius of gyration, 15.5 mm)",
                                case_ja=(f"{m:g} kg の変形しない板が、{span:g} mm 離れた 2 本の柱に載る。板の中心は post1 から "
                                         f"{xc:g} mm"),
                                source_ja=tilt_ja))

    out.append(plate_strip_known_answer())
    out += cable_known_answers()
    out += utilisation_known_answers()
    return out


def utilisation_known_answers() -> list[Record]:
    """A cantilever of two members of different materials: the stronger root has the larger stress, the weaker tip
    the larger utilisation."""
    L, P, sf, y_root, y_tip = 1000.0, 100.0, 1.5, 400.0, 100.0
    sq = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 2.0}}
    sec = sections.properties(sq)
    d = _bare({"A": (0.0, 0.0, 0.0), "M": (L / 2, 0.0, 0.0), "B": (L, 0.0, 0.0)}, [("root", "A", "M"), ("tip", "M", "B")],
              [], sq)
    d["materials"] = {"strong": dict(_steel(), yield_MPa=y_root, name="strong"),
                      "weak": dict(_steel(), yield_MPa=y_tip, name="weak")}
    d["members"][0]["material"], d["members"][1]["material"] = "strong", "weak"
    lp = build(d, [], holds={"A": "all"})
    F = np.zeros((len(lp.model.nodes), 6))
    F[lp.node_of["B"], 2] = -P
    (U,), lay = solve_cases(lp, [F])
    r = {x["quantity"]: x for x in _stress_results(lp, [U], lay, safety_factor=sf)}
    Z = sec["Iz"] / 20.0  # the square tube's section modulus (corner 20 mm from the axis)
    s_root, s_tip = P * L / Z, P * (L / 2) / Z  # the bending moment at each member's root, over Z
    case = (f"a 1 m cantilever of a square tube 40x40x2, root half yield {y_root:g} MPa, tip half {y_tip:g} MPa, "
            f"{P:g} N at the tip, safety factor {sf:g}")
    src = ("utilisation = M / Z / (yield / sf) at each member's root (M = P x from the tip); the root has twice the "
           "stress, the tip a quarter of the yield")
    dims = sq["dims_mm"]
    case_ja = (f"長さ {L / 1e3:g} m の片持ちの角パイプ {dims['b']:g}x{dims['h']:g}x{dims['t']:g}。根元の半分は降伏 "
               f"{y_root:g} MPa、先の半分は {y_tip:g} MPa、先に {P:g} N、安全率 {sf:g}")
    src_ja = ("使用率 = M / Z / (降伏 / 安全率) を部材ごとの根元で (M = P × 先からの距離)。根元は応力が 2 倍、先は降伏が "
              "4 分の 1")
    return [check_record(case, "max_stress", s_root, r["max_stress"]["value"], "MPa", 1e-6, "sigma = P L / Z at the root",
                         case_ja=case_ja, source_ja="根元で sigma = P L / Z"),
            check_record(case, "max_utilisation", s_tip * sf / y_tip, r["max_utilisation"]["value"], "1", 1e-6, src,
                         case_ja=case_ja, source_ja=src_ja),
            check_record(case, "max_utilisation_is_the_tip", 1.0, float(r["max_utilisation"]["where"] == "tip"), "1",
                         0.0, "the tip member, not the root (where the stress is largest), governs",
                         case_ja=case_ja, source_ja="決めるのは先の部材で、(応力がいちばん大きい) 根元ではない"),
            check_record(case, "allowable_stress", y_tip / sf, r["allowable_stress"]["value"], "MPa", 1e-9,
                         "the tip's own yield / sf",
                         case_ja=case_ja, source_ja="先の部材の降伏 / 安全率")]


def taut_cable_deflection(P: float, L: float, EA: float, T0: float) -> float:
    """The deflection w of the middle of a cable L long, held at both ends with pretension T0, pushed across at its
    middle by P: the root of P = 2 T w / sqrt(a^2 + w^2), T = T0 + EA (sqrt(a^2 + w^2) - a) / a, a = L / 2 (the
    taut string with stretch; bisection)."""
    a = L / 2.0
    lo, hi = 0.0, P * L / (4.0 * T0) if T0 > 0 else L

    def f(w):
        s = math.hypot(a, w)
        return 2.0 * (T0 + EA * (s - a) / a) * w / s - P

    while f(hi) < 0.0:
        hi *= 2.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if f(mid) < 0.0 else (lo, mid)
    return 0.5 * (lo + hi)


def cable_known_answers() -> list[Record]:
    """A cable held at both ends, pushed across at its middle: the first (linear) step P L / (4 T0) and the
    converged sag (taut_cable_deflection)."""
    L, d, sigma0, P, E = 600.0, 3.0, 20.0, 200.0, 205000.0
    A = math.pi * d * d / 4.0
    T0 = sigma0 * A
    n = 6
    model = FEModel(np.array([[L * k / n, 0.0, 0.0] for k in range(n + 1)]))
    cab = Cable([(k, k + 1) for k in range(n)], [E * A] * n, [T0] * n, model.nodes, A=[A] * n)
    model.add(cab)
    model.fix([0, n], "xyz")
    F = np.zeros((n + 1, 6))
    F[n // 2, 1] = P
    lin = StaticSolver(model).solve(F).displacements[n // 2, 1]
    U, _ = newton_solve(model, cab, F)
    case = (f"a steel wire {d:g} mm, {L:g} mm between fixed ends, pretension {sigma0:g} MPa ({T0:.1f} N), {P:g} N across "
            f"at its middle ({n} cables)")
    case_ja = (f"径 {d:g} mm の鋼の線、両端を止めた間が {L:g} mm、初めの引張り {sigma0:g} MPa ({T0:.1f} N)、真ん中に横から "
               f"{P:g} N ({n} 本のケーブル要素)")
    return [check_record(case, "linear_deflection", P * L / (4.0 * T0), float(lin), "mm", 1e-9,
                         "the first Newton step (the pretension's geometric stiffness alone): w = P L / (4 T0)",
                         case_ja=case_ja,
                         source_ja="ニュートン法の 1 歩目 (初めの引張りによる形の硬さだけ): w = P L / (4 T0)"),
            check_record(case, "sag", taut_cable_deflection(P, L, E * A, T0), float(U[n // 2, 1]), "mm", 1e-6,
                         "the taut string with stretch: P = 2 T w / sqrt(a^2 + w^2), T = T0 + EA (sqrt(a^2 + w^2) - a) / a, "
                         "a = L / 2 (statics of the two halves; solved by bisection)",
                         case_ja=case_ja,
                         source_ja=("伸びる張った糸: P = 2 T w / sqrt(a^2 + w^2)、T = T0 + EA (sqrt(a^2 + w^2) - a) / a、"
                                    "a = L / 2 (半分ずつの力のつり合い。二分法で解く)"))]


def plate_strip_known_answer() -> Record:
    """A plate strip across two rails, pushed down at its middle: a beam on two supports with rotation springs."""
    Lr, L, b, t, P = 1000.0, 600.0, 40.0, 3.0, 100.0
    rect = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.0}}
    sec = sections.properties(rect)
    E = 205000.0
    G = E / 2.6
    # the plate's mid-plane on the rails' axes, so no lever joins the contact's rotation to the rails' sideways bending
    d = _bare({"R1a": (0.0, -L / 2, 0.0), "R1b": (Lr, -L / 2, 0.0), "R2a": (0.0, L / 2, 0.0), "R2b": (Lr, L / 2, 0.0)},
              [("rail1", "R1a", "R1b"), ("rail2", "R2a", "R2b")],
              [_box("plate", (Lr / 2, 0.0, 0.0), (b, L + 40.0, t), kind="body")], rect)
    cs = find_contacts(d)
    lp = build(d, cs, holds={k: "all" for k in ("R1a", "R1b", "R2a", "R2b")})
    node = lp.part_node["plate"]
    F = np.zeros((len(lp.model.nodes), 6))
    F[node, 2] = -P
    (U,), _ = solve_cases(lp, [F])
    got = -float(U[node, 2])
    c = cs[0]
    Ip = b * t ** 3 / 12.0
    # end moment M from compatibility: the simply supported strip's end rotation P L^2/(16 E I) less the end moments'
    # M L/(2 E I) equals the rotation of the contact spring M/k_r and of the rail's twist M Lr/(4 G J)
    M = (P * L ** 2 / (16 * E * Ip)) / (1.0 / c.k_r + Lr / (4 * G * sec["J"]) + L / (2 * E * Ip))
    want = (P * L ** 3 / (48 * E * Ip) - M * L ** 2 / (8 * E * Ip) + P / (2 * c.k_t)
            + (P / 2) * Lr ** 3 / (192 * E * sec["Iz"]))
    return check_record(f"a steel plate strip {b:g} x {t:g} mm across two rails {L:g} mm apart (each clamped at both "
                        f"ends, {Lr:g} mm), {P:g} N down at its middle", "plate_middle_deflection", want, got, "mm", 1e-6,
                        "the strip is a beam of I = b t^3/12 between the contacts, with end moments M from "
                        "compatibility (P L^2/(16EI) - M L/(2EI) = M (1/k_r + Lr/(4GJ))): "
                        "P L^3/(48EI) - M L^2/(8EI); plus the contact springs P/(2 k_t) and each rail clamped at both "
                        "ends under P/2 at its middle, (P/2) Lr^3/(192 E I_rail)",
                        case_ja=(f"幅 {b:g} x 厚さ {t:g} mm の鋼の板の帯を、{L:g} mm 離れた 2 本のレール (どちらも長さ "
                                 f"{Lr:g} mm で両端を固定) に渡し、真ん中に {P:g} N を下へ"),
                        source_ja=("帯は接点の間の I = b t^3/12 の梁で、両端の回す力 M はたわみ角が合う条件 "
                                   "(P L^2/(16EI) - M L/(2EI) = M (1/k_r + Lr/(4GJ))) から: P L^3/(48EI) - M L^2/(8EI)。"
                                   "これに接点のばね P/(2 k_t) と、両端を固定したレールの真ん中に P/2 がかかる分 "
                                   "(P/2) Lr^3/(192 E I_rail) を足す"))


# ------------------------------------------------------------------ the tool

# Defaults when a load case has none: hand 300 N, foot 500 N and push 725 N are the values of Q66, Q68 and Q77 in
# data/requirements/offroad-4w.yaml, whose basis gives the sources: Q66 UN R79 (1988 text) steering effort table, an
# assumption to use it as a load; Q68 UN R13-H Annex 3, 2.1.1 and 2.3.4; Q77 CPSC NPR 79 FR 68964, § 1422.5(c).
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
NET_PRESTRESS = Param("net_prestress", "MPa", "the nets' wire stress as fitted (their pretension; an assumption)",
                      default=NET_PRESTRESS_MPA)
SAFETY_FACTOR = Param("safety_factor", "1", "each member's allowable stress is its own material's yield over this "
                      "(max_utilisation) (Q61)", default=1.0)


class LoadPath(BuiltinTool):
    name = "load_path"
    title = "荷重の通り道 (部品を剛体か板の帯に、触れている所をばねに)"
    domain = "strength"
    base = "K1"
    tests = ["R7", "R8"]
    formulas = ["load_path.control_deflection", "load_path.plate_strip", "load_path.grip_bound", "load_path.free_parts"]
    load_case_params = (LOAD_KIND, HAND_FORCE, FOOT_FORCE, ACCEL_V, ACCEL_LON, ACCEL_LAT, OCC_MASS, OCC_LAT, OCC_LON,
                        PUSH_FORCE, SAFETY_FACTOR, HAND_PART, FOOT_PARTS, LOADING)
    run_params = (TOUCH, NET_PRESTRESS)
    # Every load kind gives free_parts. grip_deflection (load_kind hand) and pedal_deflection (foot) are predicted
    # by load_path.grip_bound (output delta) with the inputs of hiraku.catalog.builders.load_path (grip_bound_inputs,
    # pedal_bound_inputs); they are not listed here because a load case of the default kind (inertia) has neither.
    predicts = (
        Predicts("free_parts", "load_path.free_parts", "n", "the contacts followed from the members (a graph search)"),
        Predicts("unsupported_load", "load_path.free_parts", "n",
                 "the path check (hiraku.catalog.builders.load_path.PATH_PREDICTION): the same search over the parts "
                 "the load case loads"),
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
        values["net_prestress"] = float(run_param(params, NET_PRESTRESS))
        out = analyse_load_path(design, kind, values, touch)
        n_springs = len(out.pop("contacts"))
        free = out.pop("free")
        out["assumptions"] = list(ASSUMPTIONS) + ([f"触れる許し {touch:g} mm (run の params.touch)"] if touch != TOUCH_MM else [])
        if values["net_prestress"] != NET_PRESTRESS_MPA:
            out["assumptions"].append(f"網の線の初めの張り {values['net_prestress']:g} MPa (run の params.net_prestress)")
        if free:
            out["assumptions"].append(f"どこにも繋がらない部品 {len(free)} 個の質量は枠に載っていない (応力とたわみは、"
                                      "それらが繋がったときより小さく出る)")
        out["effects"].append({"name": f"触れている所 (ばね) {n_springs} か所", "value": float(n_springs), "unit": "1"})
        return out

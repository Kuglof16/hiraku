"""The vehicle K2 simulates, built from a contract `design`.

Supports (wheels, casters, legs) come from design.supports; masses from the
part boxes in design.parts. Parts of a wheel kind are the unsprung mass of
the nearest sprung corner; everything else is the sprung body. Every number
the builder had to assume is listed in Vehicle.assumptions.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from . import kinematics
from .magic_formula import DEFAULT_TYRE, MF61, load_coefficients
from .mass import combine, items_symmetric, member_items, part_items
from .units import G0, si

UNSPRUNG_KINDS = {"wheel", "tire", "tyre", "hub", "upright", "knuckle", "brake"}
HALF_UNSPRUNG_KINDS = {"suspension"}

# Defaults used when the design does not say. They are modelling choices,
# not values from a source; each one used is reported in Vehicle.assumptions
# (and the tools write those into the analysis record's assumptions). The keys
# are the design variable names of hiraku.designgen where it has one.
DEFAULTS = {
    "tyre_stiffness": (150e3, "N/m", "tyre vertical stiffness 150 N/mm (assumed; the design has no tyre_stiffness)"),
    "tyre_damping_ratio": (0.05, "1", "tyre vertical damping ratio 0.05 (assumed; the design has no tyre_damping_ratio)"),
    "tire_cornering_coeff": (8.0, "1/rad", "cornering stiffness 8 x static wheel load per rad (assumed; the design "
                                           "has no front_cornering_stiffness / rear_cornering_stiffness)"),
    "tyre_slip_stiffness_coeff": (10.0, "1", "longitudinal slip stiffness 10 x static wheel load (assumed; the "
                                             "design has no tyre_slip_stiffness_coeff)"),
    "tyre_relaxation_length": (None, "m", "relaxation length = wheel radius (assumed; the design has no "
                                          "tyre_relaxation_length)"),
    "wheel_inertia": (None, "kg*m^2", "wheel spin inertia as a solid disc of the unsprung mass, J = m r^2 / 2 "
                                      "(assumed; the design has no wheel_inertia)"),
    "tire_mu": (0.8, "1", "friction coefficient 0.8 (assumed; the load case usually gives mu)"),
    "rolling_resistance": (0.015, "1", "rolling resistance coefficient 0.015 (assumed; the design has no "
                                       "rolling_resistance)"),
    "damping_ratio": (0.3, "1", "suspension damping ratio 0.3 of the corner (assumed)"),
    "travel": (0.10, "m", "suspension travel 100 mm, half bump half rebound (assumed)"),
    "bump_stop_factor": (10.0, "1", "bump and rebound stops 10 x wheel rate (assumed; the design has no "
                                    "bump_stop_factor)"),
    "unsprung_fraction": (0.05, "1", "unsprung mass 5 % of the vehicle mass per sprung corner when no wheel parts are listed (assumed)"),
    "max_steer_angle": (math.radians(35.0), "rad", "maximum steer angle 35 deg (assumed; the design has no "
                                                   "max_steer_angle)"),
    "bushing_radial_stiffness": (3e6, "N/m", "suspension arm bushings 3 kN/mm across the pivot axis (assumed; the "
                                             "design has no bushing_radial_stiffness)"),
    "bushing_axial_stiffness": (0.5e6, "N/m", "suspension arm bushings 0.5 kN/mm along the pivot axis (assumed; the "
                                              "design has no bushing_axial_stiffness)"),
    "tie_rod_stiffness": (10e6, "N/m", "tie rod 10 kN/mm along its length, rack held (assumed; the design has no "
                                       "tie_rod_stiffness)"),
}


class DesignError(ValueError):
    """The design cannot be simulated; the message says what to change."""


@dataclass(eq=False)
class Corner:
    id: str
    kind: str            # wheel | caster | leg
    side: float          # +1 left, -1 right, 0 on the centreline
    pos: np.ndarray      # wheel centre at design (design frame, m)
    radius: float
    width: float
    susp: str            # rigid | spring | kc_table
    k_w: float = 0.0     # wheel rate N/m
    c_w: float = 0.0     # damper at the wheel N s/m
    preload: float = 0.0 # spring force at design travel (N)
    s_min: float = -0.05
    s_max: float = 0.05
    k_stop: float = 0.0
    m_u: float = 0.0
    j_w: float = 0.0
    drive: bool = False
    brake: bool = False
    steer: bool = False
    path: kinematics.PathPoly | None = None
    k_z: float = 150e3
    c_z: float = 0.0
    c_long: float = 0.0  # N per unit slip
    c_lat: float = 0.0   # N per rad
    sigma: float = 0.3
    mu: float = 0.8
    c_rr: float = 0.015
    static_load: float = 0.0
    mirror: int = -1
    mf: MF61 | None = None  # the wheel's Magic Formula tyre (None: legs and casters)
    table: dict | None = None  # the K&C table the path was fitted to (kc_table supports)

    @property
    def dof(self) -> bool:
        return self.susp != "rigid"


@dataclass(eq=False)
class Vehicle:
    name: str
    corners: list[Corner]
    m_total: float
    cg_total: np.ndarray
    m_s: float
    cg_s: np.ndarray         # sprung CG, design frame (m)
    i_s: np.ndarray          # sprung inertia about cg_s
    symmetric: bool
    side_area: float
    side_cp: np.ndarray      # design frame (m)
    front_area: float
    front_cp: np.ndarray
    x_ref: float             # x of the non-steered axle (Ackermann reference)
    l_steer: float           # x distance steered axle - reference
    max_steer: float
    assumptions: list[str] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.corners)

    def ids(self) -> list[str]:
        return [c.id for c in self.corners]

    def wheel_steer(self, delta: float) -> np.ndarray:
        """Per-corner steer angles (rad) for a bicycle-model angle delta (Ackermann)."""
        out = np.zeros(self.n)
        if delta == 0.0:
            return out
        t = math.tan(delta)
        for i, c in enumerate(self.corners):
            if c.steer and c.kind == "wheel":
                lx = float(c.pos[0]) - self.x_ref
                out[i] = math.atan(lx * t / (self.l_steer - float(c.pos[1]) * t))
        return out


def _mirror_pairs(sups: list[dict]) -> list[int]:
    """Index of the mirror partner of each support (-1 if none, itself if on the centreline)."""
    def key(s):
        a = s["actuation"]
        return (s["kind"], round(float(s["radius_mm"]), 9), s["suspension"]["type"],
                round(float(s["suspension"].get("stiffness_N_per_mm", 0.0)), 9), a["drive"], a["brake"], a["steer"])

    out = [-1] * len(sups)
    for i, s in enumerate(sups):
        x, y, z = (float(v) for v in s["position_mm"])
        if abs(y) < 1e-9:
            out[i] = i
            continue
        for j, t in enumerate(sups):
            if j == i:
                continue
            xj, yj, zj = (float(v) for v in t["position_mm"])
            if abs(x - xj) < 1e-9 and abs(y + yj) < 1e-9 and abs(z - zj) < 1e-9 and key(s) == key(t):
                out[i] = j
                break
    return out


def _projected(boxes: list[tuple[np.ndarray, np.ndarray]], discs: list[tuple[np.ndarray, float]],
               axes: tuple[int, int], disc_box_width: float | None, cell: float = 0.01) -> tuple[float, np.ndarray]:
    """Area and centroid of the union of boxes (AABBs) and discs projected on two axes."""
    if not boxes and not discs:
        return 0.0, np.zeros(2)
    a0, a1 = axes
    lo = np.array([np.inf, np.inf])
    hi = -lo
    for c, h in boxes:
        lo = np.minimum(lo, [c[a0] - h[a0], c[a1] - h[a1]])
        hi = np.maximum(hi, [c[a0] + h[a0], c[a1] + h[a1]])
    for c, r in discs:
        lo = np.minimum(lo, [c[a0] - r, c[a1] - r])
        hi = np.maximum(hi, [c[a0] + r, c[a1] + r])
    u = np.arange(lo[0] + cell / 2, hi[0], cell)
    v = np.arange(lo[1] + cell / 2, hi[1], cell)
    uu, vv = np.meshgrid(u, v, indexing="ij")
    mask = np.zeros(uu.shape, dtype=bool)
    for c, h in boxes:
        mask |= (np.abs(uu - c[a0]) <= h[a0]) & (np.abs(vv - c[a1]) <= h[a1])
    for c, r in discs:
        if disc_box_width is None:
            mask |= (uu - c[a0]) ** 2 + (vv - c[a1]) ** 2 <= r * r
        else:
            mask |= (np.abs(uu - c[a0]) <= disc_box_width / 2) & (np.abs(vv - c[a1]) <= r)
    n = int(mask.sum())
    if n == 0:
        return 0.0, np.zeros(2)
    return n * cell * cell, np.array([uu[mask].mean(), vv[mask].mean()])


# Older names of the same design variables, read when the designgen name is absent.
VARIABLE_ALIASES = {
    "tyre_stiffness": ("tire_vertical_stiffness", "tire_stiffness"),
    "rolling_resistance": ("tire_rolling_resistance",),
    "tire_mu": ("tyre_mu",),
    "max_steer_angle": ("max_steer",),
    "tyre_damping_ratio": ("tire_vertical_damping_ratio",),
    "tyre_slip_stiffness_coeff": ("tire_long_slip_coeff",),
    "tyre_relaxation_length": ("tire_relaxation_length",),
}


def _given(design: dict, name: str):
    """The design variable `name` (or an older name of it), or None."""
    var = design.get("variables", {}) or {}
    return next((var[n] for n in (name,) + VARIABLE_ALIASES.get(name, ()) if n in var), None)


def _var(design: dict, name: str, want: str, notes: list[str], default_key: str | None = None):
    var = design.get("variables", {}) or {}
    for n in (name,) + VARIABLE_ALIASES.get(name, ()):
        if n in var:
            return si(var[n], want)
    key = default_key or name
    value, _, text = DEFAULTS[key]
    if text not in notes:
        notes.append(text)
    return value


def _note(notes: list[str], text: str):
    if text not in notes:
        notes.append(text)


@dataclass
class _Settings:
    """The design-wide tyre, suspension and steering numbers (design variables or DEFAULTS)."""

    k_z: float
    zeta_z: float
    c_kappa: float
    mu: float
    c_rr: float
    zeta_s: float
    max_steer: float


def build_vehicle(design: dict, overrides: dict | None = None) -> Vehicle:
    """Build the simulation model. Raises DesignError with what to change."""
    overrides = overrides or {}
    notes: list[str] = []
    sups = design.get("supports") or []
    if not sups:
        raise DesignError("the design has no supports (wheels, casters or legs); add design.supports")
    items = _weighed_items(design, notes)
    m_all = sum(it[0] for it in items)
    mirror = _mirror_pairs(sups)
    symmetric = all(j >= 0 for j in mirror) and items_symmetric(items)
    sups, mirror_new = _in_mirror_pairs(sups, mirror)
    st = _settings(design, overrides, notes)
    corners = [_corner(s, mirror_new[k], st, design, notes) for k, s in enumerate(sups)]
    sprung_items = _unsprung(items, corners, m_all, design, notes)
    m_total, cg_total, m_s, cg_s, i_s = _body(items, sprung_items, symmetric)
    loads = _static_loads(corners, m_total, cg_total, symmetric)
    _settle(corners, loads, sups, design, st, notes)
    _tyres(corners, notes)
    side_area, side_cp, front_area, front_cp = _aero(items, corners, symmetric, notes)
    x_ref, l_steer = _steering(corners, cg_total)
    return Vehicle(
        name=design.get("name", "design"), corners=corners, m_total=m_total, cg_total=cg_total,
        m_s=m_s, cg_s=cg_s, i_s=i_s, symmetric=symmetric, side_area=side_area, side_cp=side_cp,
        front_area=front_area, front_cp=front_cp, x_ref=x_ref, l_steer=l_steer, max_steer=st.max_steer,
        assumptions=notes,
    )


def _weighed_items(design: dict, notes: list[str]) -> list:
    """(mass, centre, inertia, part) of every part box and frame member."""
    items = part_items(design.get("parts") or [])
    try:
        members = member_items(design)
    except ValueError as e:
        raise DesignError(str(e))
    if members:
        items += members
        notes.append("frame members weighed as thin rods: density x area x length at the member centre")
    if not items:
        raise DesignError("the design has no parts with mass; add design.parts with mass_kg")
    return items


def _in_mirror_pairs(sups: list[dict], mirror: list[int]) -> tuple[list[dict], list[int]]:
    """The supports ordered in mirror pairs (left, right), then centreline and unpaired ones,
    and the mirror partner of each in that order."""
    order: list[int] = []
    for i, s in enumerate(sups):
        if i in order:
            continue
        j = mirror[i]
        if j >= 0 and j != i and j not in order:
            left, right = (i, j) if float(s["position_mm"][1]) > 0 else (j, i)
            order += [left, right]
        elif j == i or j < 0:
            pass
    order += [i for i in range(len(sups)) if i not in order]
    sups = [sups[i] for i in order]
    mirror_new = [-1] * len(sups)
    for k in range(0, len(sups) - 1):
        a, b = sups[k], sups[k + 1]
        if (k % 2 == 0 and float(a["position_mm"][1]) > 0 and abs(float(a["position_mm"][1]) + float(b["position_mm"][1])) < 1e-9
                and abs(float(a["position_mm"][0]) - float(b["position_mm"][0])) < 1e-9):
            mirror_new[k], mirror_new[k + 1] = k + 1, k
    return sups, mirror_new


def _settings(design: dict, overrides: dict, notes: list[str]) -> _Settings:
    k_z = overrides.get("tire_vertical_stiffness")
    if k_z is None:
        k_z = _var(design, "tyre_stiffness", "N/m", notes)
    zeta_z = _var(design, "tyre_damping_ratio", "1", notes)
    c_kappa = _var(design, "tyre_slip_stiffness_coeff", "1", notes)
    mu = overrides.get("mu")
    if mu is None:
        mu = _var(design, "tire_mu", "1", notes)
    c_rr = overrides.get("c_rr")
    if c_rr is None:
        c_rr = _var(design, "rolling_resistance", "1", notes)
    zeta_s = _var(design, "damping_ratio", "1", [])
    max_steer = _var(design, "max_steer_angle", "rad", notes)
    return _Settings(k_z, zeta_z, c_kappa, mu, c_rr, zeta_s, max_steer)


def _corner(s: dict, mirror: int, st: _Settings, design: dict, notes: list[str]) -> Corner:
    """One support as a corner: geometry, actuation, suspension rate, travel and wheel path."""
    pos = np.asarray(s["position_mm"], dtype=float) / 1000.0
    kind = s["kind"]
    radius = float(s["radius_mm"]) / 1000.0
    if kind == "leg" and radius <= 0.0:
        radius = 0.01
    if radius <= 0.0:
        raise DesignError(f"support {s['id']}: radius_mm must be > 0 for a {kind}")
    width = float(s.get("width_mm", 0.0)) / 1000.0
    sus = s["suspension"]
    typ = sus["type"]
    if typ == "kc_table" and not sus.get("kc_table"):
        raise DesignError(f"support {s['id']}: suspension type kc_table needs suspension.kc_table (a table path or 'builtin:double_wishbone')")
    if typ in ("spring", "kc_table") and not sus.get("stiffness_N_per_mm"):
        raise DesignError(f"support {s['id']}: suspension.stiffness_N_per_mm (wheel rate) is missing or zero; give the wheel rate or use type 'rigid'")
    side = 1.0 if pos[1] > 1e-12 else (-1.0 if pos[1] < -1e-12 else 0.0)
    act = s["actuation"]
    c = Corner(id=s["id"], kind=kind, side=side, pos=pos, radius=radius, width=width, susp=typ,
               drive=bool(act["drive"]) and kind == "wheel", brake=bool(act["brake"]) and kind == "wheel",
               steer=bool(act["steer"]) and kind == "wheel", mirror=mirror)
    c.k_z = st.k_z
    c.mu = st.mu
    c.c_rr = st.c_rr
    if typ == "rigid":
        return c
    c.k_w = float(sus["stiffness_N_per_mm"]) * 1000.0
    if "travel_mm" in sus:
        travel = float(sus["travel_mm"]) / 1000.0
    else:
        travel = DEFAULTS["travel"][0]
        _note(notes, DEFAULTS["travel"][2])
    c.s_min, c.s_max = -travel / 2.0, travel / 2.0
    c.k_stop = _var(design, "bump_stop_factor", "1", notes) * c.k_w
    if typ == "kc_table":
        ref = sus["kc_table"]
        if ref.startswith("builtin:"):
            hp_pos = pos.copy()
            hp_pos[1] = abs(hp_pos[1])
            bushings = kinematics.Bushings(_var(design, "bushing_radial_stiffness", "N/m", notes),
                                           _var(design, "bushing_axial_stiffness", "N/m", notes),
                                           _var(design, "tie_rod_stiffness", "N/m", notes))
            table = _builtin_table(hp_pos, radius, _table_travel(travel), bushings)
            _note(notes, "K&C table from the generic short-long-arm layout (builtin, not a real vehicle), its tie rod's "
                         "inner joint placed where the toe follows the travel least (no bump steer: K2's choice, "
                         "assumed), with its compliance from the bushings and the tie rod; longitudinal forces taken "
                         "at the contact point")
        else:
            table = kinematics.load_table(ref)
        c.path = kinematics.PathPoly.from_table(table)
        c.table = table
    else:
        c.path = kinematics.PathPoly.vertical()
    return c


def _table_travel(travel: float) -> float:
    """Half range (m) of a builtin K&C table for a suspension travel (m, bump to rebound)."""
    return max(travel, 0.02) * 0.6


def hardpoints_of(design: dict, support_id: str) -> dict:
    """The double-wishbone hardpoints (mm, design frame) of a support on the builtin K&C table,
    as K2 builds and runs them: the left-wheel layout, mirrored in y for a right wheel
    (Hardpoints.to_json keys). Raises DesignError when the support has no builtin table."""
    sup = next((s for s in design.get("supports") or [] if s["id"] == support_id), None)
    if sup is None:
        raise DesignError(f"no support {support_id!r} in the design")
    sus = sup["suspension"]
    if sus.get("type") != "kc_table" or not str(sus.get("kc_table", "")).startswith("builtin:"):
        raise DesignError(f"support {support_id} is not on the builtin K&C table (suspension.type kc_table, "
                          "kc_table 'builtin:double_wishbone')")
    pos = np.asarray(sup["position_mm"], dtype=float) / 1000.0
    travel = float(sus["travel_mm"]) / 1000.0 if "travel_mm" in sus else DEFAULTS["travel"][0]
    left = pos.copy()
    left[1] = abs(left[1])
    out = kinematics.builtin_hardpoints(left, float(sup["radius_mm"]) / 1000.0, _table_travel(travel)).to_json()
    if pos[1] < 0.0:
        out = {k: ([v[0], -v[1], v[2]] if isinstance(v, list) else v) for k, v in out.items()}
    return out


@lru_cache(maxsize=32)
def _table_of(key: tuple) -> str:
    wc, radius, travel, bushings = key
    return json.dumps(kinematics.build_table(kinematics.builtin_hardpoints(np.array(wc), radius, travel), travel,
                                             bushings=bushings))


def _builtin_table(wc: np.ndarray, radius: float, travel: float, bushings: kinematics.Bushings) -> dict:
    """The builtin K&C table of a corner (computed once per geometry and bushings in a process)."""
    return json.loads(_table_of((tuple(float(x) for x in wc), float(radius), float(travel), bushings)))


def _unsprung(items: list, corners: list[Corner], m_all: float, design: dict, notes: list[str]) -> list:
    """Give each corner its unsprung mass and spin inertia; return the sprung items.

    Wheel parts go to the nearest corner (suspension parts half); a sprung corner without
    wheel parts gets a default share, taken out of the body."""
    sprung_items = []
    wheel_mass = np.zeros(len(corners))
    for m, centre, inertia, p in items:
        kind = str(p.get("kind", "")).lower()
        if kind in UNSPRUNG_KINDS or kind in HALF_UNSPRUNG_KINDS:
            d = [float(np.linalg.norm(centre - c.pos)) for c in corners]
            i = int(np.argmin(d))
            share = m if kind in UNSPRUNG_KINDS else 0.5 * m
            wheel_mass[i] += share
            if corners[i].dof:
                if share < m:
                    sprung_items.append((m - share, centre, inertia * ((m - share) / m)))
                continue
        sprung_items.append((m, centre, inertia))
    frac = DEFAULTS["unsprung_fraction"][0]
    for i, c in enumerate(corners):
        mw = wheel_mass[i]
        if mw <= 0.0:
            mw = max(1.0, frac * m_all / len(corners))
            if c.dof or c.kind == "wheel":
                _note(notes, DEFAULTS["unsprung_fraction"][2])
            if c.dof:
                sprung_items.append((-mw, c.pos, np.zeros((3, 3))))
        if c.dof:
            c.m_u = mw
        c.j_w = 0.5 * mw * c.radius ** 2
    j_given = _given(design, "wheel_inertia")
    if j_given is not None:
        for c in corners:
            if c.kind == "wheel":
                c.j_w = si(j_given, "kg*m^2")
    elif any(c.kind == "wheel" for c in corners):
        notes.append(DEFAULTS["wheel_inertia"][2])
    if any(c.kind == "caster" for c in corners):
        notes.append("casters swivel freely: no side force, rolling resistance only")
    return sprung_items


def _body(items: list, sprung_items: list, symmetric: bool):
    """Whole-vehicle mass and CG; sprung mass, CG and inertia about it."""
    m_total, cg_total, _ = combine([(m, c, i) for m, c, i, _ in items])
    try:
        m_s, cg_s, i_s = combine(sprung_items)
    except ValueError:
        raise DesignError("all the mass is unsprung; the body needs mass")
    if m_s <= 0.0:
        raise DesignError("the unsprung mass is larger than the vehicle mass; list the wheel parts with their mass")
    if symmetric:
        cg_s[1] = 0.0
        cg_total[1] = 0.0
        i_s[0, 1] = i_s[1, 0] = i_s[1, 2] = i_s[2, 1] = 0.0
    if np.any(np.linalg.eigvalsh(i_s) <= 0.0):
        raise DesignError("the body inertia is not positive; check the part boxes and masses")
    return m_total, cg_total, m_s, cg_s, i_s


def _axle_cornering(design: dict, corners: list[Corner]) -> dict[int, float]:
    """Cornering stiffness per wheel from the design's per-axle values (both tyres), for the wheels
    of the front-most and the rear-most axle; other wheels are not in the result."""
    axle_cornering: dict[int, float] = {}
    var = design.get("variables") or {}
    wheels = [i for i, c in enumerate(corners) if c.kind == "wheel"]
    if wheels:
        for name, x in (("front_cornering_stiffness", max(float(corners[i].pos[0]) for i in wheels)),
                        ("rear_cornering_stiffness", min(float(corners[i].pos[0]) for i in wheels))):
            if name in var:
                on = [i for i in wheels if abs(float(corners[i].pos[0]) - x) < 1e-9]
                for i in on:
                    axle_cornering[i] = si(var[name], "N/rad") / len(on)
    return axle_cornering


def _static_loads(corners: list[Corner], m_total: float, cg_total: np.ndarray, symmetric: bool) -> np.ndarray:
    """Static wheel loads: rigid body on elastic supports (series spring + tyre)."""
    xs = np.array([c.pos[0] for c in corners])
    ys = np.array([c.pos[1] for c in corners])
    keff = np.array([(c.k_w * c.k_z / (c.k_w + c.k_z)) if c.dof else c.k_z for c in corners])
    a = np.array([
        [keff.sum(), (keff * xs).sum(), (keff * ys).sum()],
        [(keff * xs).sum(), (keff * xs * xs).sum(), (keff * xs * ys).sum()],
        [(keff * ys).sum(), (keff * xs * ys).sum(), (keff * ys * ys).sum()],
    ])
    if symmetric:
        a[0, 2] = a[2, 0] = a[1, 2] = a[2, 1] = 0.0
    w = m_total * G0
    rhs = np.array([w, w * cg_total[0], w * cg_total[1]])
    if abs(np.linalg.det(a)) < 1e-12 * max(1.0, abs(a).max()) ** 3:
        raise DesignError("the supports are in a line, so the vehicle cannot stand; spread the wheels to make a support area")
    sol = np.linalg.solve(a, rhs)
    loads = keff * (sol[0] + sol[1] * xs + sol[2] * ys)
    if np.any(loads <= 0.0):
        bad = [c.id for c, f in zip(corners, loads) if f <= 0]
        raise DesignError(f"the centre of gravity is outside the support area (support {', '.join(bad)} would lift at rest); move the mass or the supports")
    return loads


def _settle(corners: list[Corner], loads: np.ndarray, sups: list[dict], design: dict, st: _Settings,
            notes: list[str]):
    """Per corner from its static load: spring preload and damper, tyre damping, relaxation
    length and the tyre's slip and cornering stiffness."""
    axle_cornering = _axle_cornering(design, corners)
    for i, (c, f) in enumerate(zip(corners, loads)):
        c.static_load = float(f)
        if c.dof:
            c.preload = float(f) - c.m_u * G0
            m_corner = max(c.preload / G0, 1.0)
            if c.preload <= 0.0:
                raise DesignError(f"support {c.id}: the unsprung mass is heavier than the wheel load; the spring would have no load")
            sus_i = sups[i]["suspension"]
            if "damping_Ns_per_mm" in sus_i:
                c.c_w = float(sus_i["damping_Ns_per_mm"]) * 1000.0
            else:
                c.c_w = 2.0 * st.zeta_s * math.sqrt(c.k_w * m_corner)
                if "damping_ratio" not in (design.get("variables") or {}):
                    _note(notes, DEFAULTS["damping_ratio"][2])
        m_vert = c.m_u if c.dof else f / G0
        c.c_z = 2.0 * st.zeta_z * math.sqrt(c.k_z * m_vert)
        relax = _given(design, "tyre_relaxation_length")
        c.sigma = si(relax, "m") if relax is not None else c.radius
        if c.kind == "caster":
            c.c_long = c.c_lat = 0.0
        else:
            c.c_long = st.c_kappa * f
            c.c_lat = axle_cornering.get(i)
            if c.c_lat is None:
                c.c_lat = _var(design, "tire_cornering_coeff", "1", notes) * f
    if _given(design, "tyre_relaxation_length") is None:
        notes.append(DEFAULTS["tyre_relaxation_length"][2])


def _tyres(corners: list[Corner], notes: list[str], name: str = DEFAULT_TYRE):
    """The Magic Formula tyre of every wheel: one coefficient file scaled to the vehicle.

    Nominal load = the mean static load of the wheels (one tyre size for the vehicle),
    R0 = the wheel's radius; the scaling factors make, at each wheel's static load, the
    peak friction mu (both directions), the slip stiffness c_long and the cornering
    stiffness c_lat, so that the hand calculations (K0), which take those numbers, see the
    same tyre at small slip."""
    wheels = [c for c in corners if c.kind == "wheel"]
    if not wheels:
        return
    coeffs = load_coefficients(name)
    fz0 = sum(c.static_load for c in wheels) / len(wheels)
    for c in wheels:
        base = MF61(coeffs, fz0=fz0, r0=c.radius)
        fz = c.static_load
        c.mf = MF61(coeffs, fz0=fz0, r0=c.radius, lmux=c.mu / base.mu_x(fz), lmuy=c.mu / base.mu_y(fz),
                    lkx=c.c_long / base.kxk(fz), lky=c.c_lat / abs(base.kya(fz)))
    notes.append(f"tyre forces: Magic Formula 6.1 with the coefficients of the {coeffs['tyre']} tyre "
                 f"({coeffs['source']['title']}; not checked against the book), scaled to this vehicle: nominal "
                 f"load {fz0:.0f} N (the mean static wheel load), R0 = the wheel radius, and at each wheel's static "
                 "load the peak friction = mu and the slopes at zero slip = the stiffnesses K2 took (design or "
                 "defaults above)")


def _aero(items: list, corners: list[Corner], symmetric: bool, notes: list[str]):
    """Side and frontal areas and their centres of pressure from the part boxes and wheels."""
    boxes = []
    for m, centre, inertia, p in items:
        if "box" not in p:
            continue  # frame members: thin, left out of the projected areas
        size = np.asarray(p["box"]["size_mm"], dtype=float) / 1000.0
        boxes.append((centre, size / 2.0))
    discs = [(c.pos, c.radius) for c in corners if c.kind != "leg"]
    side_area, side_c = _projected(boxes, discs, (0, 2), None)
    front_area, front_c = _projected(boxes, [(c.pos, c.radius) for c in corners if c.kind != "leg"], (1, 2),
                                     max([c.width for c in corners] + [0.0]) or 0.1)
    notes.append("centres of pressure at the centroids of the part boxes and wheels projected on the side and front "
                 "planes (along x always; the areas and the side centre's height only where the design does not give "
                 "them) (assumed)")
    side_cp = np.array([side_c[0], 0.0, side_c[1]])
    front_cp = np.array([0.0, front_c[0], front_c[1]])
    if symmetric:
        front_cp[1] = 0.0
    return side_area, side_cp, front_area, front_cp


def _steering(corners: list[Corner], cg_total: np.ndarray) -> tuple[float, float]:
    """x of the non-steered axle (Ackermann reference) and the steered axle's distance from it."""
    steered = [c for c in corners if c.steer]
    fixed = [c for c in corners if c.kind == "wheel" and not c.steer]
    x_ref = float(np.mean([c.pos[0] for c in fixed])) if fixed else float(cg_total[0])
    l_steer = float(np.mean([c.pos[0] for c in steered])) - x_ref if steered else 1.0
    if steered and abs(l_steer) < 1e-6:
        l_steer = 1.0
    return x_ref, l_steer


def box_design(*, mass: float = 500.0, cg_x: float = -0.9, cg_z: float = 0.5, track: float = 1.2,
               wheelbase: float = 1.8, radius: float = 0.3, susp: str = "spring", k_w: float = 20.0,
               c_w: float | None = None, travel: float = 150.0, front_drive: bool = False, rear_drive: bool = True,
               box_size=(2.0, 1.0, 0.3), wheel_mass: float = 0.0, variables: dict | None = None,
               kc_table: str | None = None, name: str = "check vehicle") -> dict:
    """A plain 4-wheel design (one body box) for the known-answer checks.

    Front axle at x = 0, rear axle at x = -wheelbase (x forward). Units: kg, m, N/mm, mm.
    """
    t2 = track / 2.0 * 1000.0
    sups = []
    for sid, x, y, steer, drive in (
        ("FL", 0.0, t2, True, front_drive), ("FR", 0.0, -t2, True, front_drive),
        ("RL", -wheelbase * 1000.0, t2, False, rear_drive), ("RR", -wheelbase * 1000.0, -t2, False, rear_drive),
    ):
        sus = {"type": susp}
        if susp != "rigid":
            sus["stiffness_N_per_mm"] = k_w
            sus["travel_mm"] = travel
            if c_w is not None:
                sus["damping_Ns_per_mm"] = c_w
            if susp == "kc_table":
                sus["kc_table"] = kc_table or "builtin:double_wishbone"
        sups.append({"id": sid, "kind": "wheel", "position_mm": [x, y, radius * 1000.0], "radius_mm": radius * 1000.0,
                     "width_mm": 200.0, "suspension": sus, "actuation": {"drive": drive, "brake": True, "steer": steer}})
    parts = [{"id": "body", "name": "body", "kind": "body",
              "box": {"center_mm": [cg_x * 1000.0, 0.0, cg_z * 1000.0], "size_mm": [s * 1000.0 for s in box_size]},
              "mass_kg": mass}]
    if wheel_mass > 0.0:
        for s in sups:
            parts.append({"id": f"wheel_{s['id']}", "name": "wheel", "kind": "wheel",
                          "box": {"center_mm": list(s["position_mm"]), "size_mm": [2 * radius * 1000.0, 200.0, 2 * radius * 1000.0]},
                          "mass_kg": wheel_mass})
    return {"design_version": 1, "parent_version": None, "name": name, "variables": variables or {},
            "supports": sups, "parts": parts, "nodes": [], "members": [], "sections": {}, "materials": {}}

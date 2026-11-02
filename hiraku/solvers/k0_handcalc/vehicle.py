"""Rigid-body statics of a contract `design`: masses, centre of gravity and
the forces at the supports (wheels) at rest or while a wheel climbs a step.

Coordinates are the design's: mm, ISO 8855 (x forward, y left, z up).
Forces are N on the vehicle.

Mass bookkeeping (shared with K1 so both see the same vehicle):
- every member carries its own weight: density * A * length
- a part with `carried_by` frame nodes loads those nodes
- a part without `carried_by` is unsprung: it rests on the nearest support
  and does not load the frame (wheels, hubs, brakes)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import sections
from .units import STANDARD_GRAVITY


class DesignError(ValueError):
    """The design cannot be analysed as given; the message says what to change."""


@dataclass
class MassItem:
    id: str
    mass_kg: float
    cg_mm: np.ndarray
    carrier: str  # "member", "nodes" or "support"
    carried_by: list[str]  # member id, frame node ids, or [support id]
    inertia_kg_mm2: np.ndarray | None = None  # own inertia about its centre of gravity (3 x 3)


def box_inertia(mass: float, size_mm, rpy_deg=None) -> np.ndarray:
    """Inertia (kg*mm^2) of a solid box about its centre, rotated by roll-pitch-yaw (fixed axes x, y, z)."""
    a, b, c = (float(s) for s in size_mm)
    local = mass / 12.0 * np.diag([b * b + c * c, a * a + c * c, a * a + b * b])
    if not rpy_deg or not any(rpy_deg):
        return local
    r, p, y = np.radians(rpy_deg)
    Rx = np.array([[1, 0, 0], [0, np.cos(r), -np.sin(r)], [0, np.sin(r), np.cos(r)]])
    Ry = np.array([[np.cos(p), 0, np.sin(p)], [0, 1, 0], [-np.sin(p), 0, np.cos(p)]])
    Rz = np.array([[np.cos(y), -np.sin(y), 0], [np.sin(y), np.cos(y), 0], [0, 0, 1]])
    R = Rz @ Ry @ Rx
    return R @ local @ R.T


def rod_inertia(mass: float, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Inertia (kg*mm^2) of a slender rod from a to b about its middle."""
    d = b - a
    L = float(np.linalg.norm(d))
    u = d / L
    return mass * L * L / 12.0 * (np.eye(3) - np.outer(u, u))


def inertia_tensor(items: list[MassItem], about: np.ndarray) -> np.ndarray:
    """Inertia (kg*mm^2) of all items about a point (parallel-axis theorem)."""
    J = np.zeros((3, 3))
    for it in items:
        d = it.cg_mm - about
        J += it.mass_kg * (float(d @ d) * np.eye(3) - np.outer(d, d))
        if it.inertia_kg_mm2 is not None:
            J += it.inertia_kg_mm2
    return J


OCCUPANTS = {"empty": 0, "driver": 1, "two": 2, "two_cargo": 2}
CARGO = {"empty": False, "driver": False, "two": False, "two_cargo": True}


def parts_on_board(design: dict, loading: str) -> dict:
    """A copy of the design with only the occupants and cargo of a loading (catalog Q30).

    Occupants board in the order they are listed (the first is the driver);
    cargo parts ride only with two_cargo. A design with fewer occupant parts
    than the loading names carries the ones it has (products without seats).
    """
    if loading not in OCCUPANTS:
        raise ValueError(f"unknown loading {loading!r}; use one of {', '.join(OCCUPANTS)}")
    parts, seated = [], 0
    for p in design.get("parts", []):
        if p["kind"] == "occupant":
            if seated >= OCCUPANTS[loading]:
                continue
            seated += 1
        elif p["kind"] == "cargo" and not CARGO[loading]:
            continue
        parts.append(p)
    out = dict(design)
    out["parts"] = parts
    return out


def node_table(design: dict) -> dict[str, np.ndarray]:
    nodes = {}
    for n in design.get("nodes", []):
        if n["id"] in nodes:
            raise DesignError(f"node id {n['id']!r} is used twice; give each node its own id")
        nodes[n["id"]] = np.asarray(n["xyz_mm"], dtype=float)
    return nodes


def member_props(design: dict) -> dict[str, dict]:
    """Section properties, material and length for every member."""
    nodes = node_table(design)
    out = {}
    for m in design.get("members", []):
        mid = m["id"]
        for end in ("node_a", "node_b"):
            if m[end] not in nodes:
                raise DesignError(f"member {mid!r} uses node {m[end]!r}, which is not in design.nodes")
        if m["section"] not in design.get("sections", {}):
            raise DesignError(f"member {mid!r} uses section {m['section']!r}, which is not in design.sections")
        if m["material"] not in design.get("materials", {}):
            raise DesignError(f"member {mid!r} uses material {m['material']!r}, which is not in design.materials")
        try:
            props = sections.properties(design["sections"][m["section"]])
        except ValueError as e:
            raise DesignError(f"member {mid!r}, section {m['section']!r}: {e}") from None
        a, b = nodes[m["node_a"]], nodes[m["node_b"]]
        L = float(np.linalg.norm(b - a))
        if L <= 0:
            raise DesignError(f"member {mid!r} has zero length (node_a and node_b are at the same place)")
        out[mid] = {"section": props, "material": design["materials"][m["material"]], "length": L,
                    "a": a, "b": b, "member": m}
    return out


def support_contact(support: dict) -> np.ndarray:
    """Ground contact point below the wheel centre (wheels and casters), or the foot (legs)."""
    p = np.asarray(support["position_mm"], dtype=float)
    if support["kind"] == "leg":
        return p.copy()
    return p - np.array([0.0, 0.0, float(support["radius_mm"])])


def mass_items(design: dict) -> list[MassItem]:
    nodes = node_table(design)
    supports = design.get("supports", [])
    items = []
    for mid, mp in member_props(design).items():
        rho = float(mp["material"]["density_kg_m3"])
        mass = rho * mp["section"]["A"] * mp["length"] * 1e-9
        items.append(MassItem(f"member:{mid}", mass, (mp["a"] + mp["b"]) / 2.0, "member", [mid],
                              rod_inertia(mass, mp["a"], mp["b"])))
    for part in design.get("parts", []):
        pid = part["id"]
        mass = float(part["mass_kg"])
        cg = np.asarray(part["box"]["center_mm"], dtype=float)
        J = box_inertia(mass, part["box"]["size_mm"], part["box"].get("rpy_deg"))
        carried = list(part.get("carried_by") or [])
        if carried:
            bad = [n for n in carried if n not in nodes]
            if bad:
                raise DesignError(f"part {pid!r} is carried by node(s) {', '.join(bad)} that are not in design.nodes")
            items.append(MassItem(f"part:{pid}", mass, cg, "nodes", carried, J))
        else:
            if not supports:
                raise DesignError(f"part {pid!r} has no carried_by nodes and the design has no supports")
            dist = [np.linalg.norm(np.asarray(s["position_mm"], float) - cg) for s in supports]
            sid = supports[int(np.argmin(dist))]["id"]
            items.append(MassItem(f"part:{pid}", mass, cg, "support", [sid], J))
    return items


def mass_summary(design: dict) -> dict:
    items = mass_items(design)
    m = sum(i.mass_kg for i in items)
    if m <= 0:
        raise DesignError("the design has no mass (no members with density and no parts with mass)")
    cg = sum(i.mass_kg * i.cg_mm for i in items) / m
    frame = sum(i.mass_kg for i in items if i.carrier == "member")
    unsprung = sum(i.mass_kg for i in items if i.carrier == "support")
    return {"mass_kg": m, "cg_mm": cg, "frame_mass_kg": frame, "unsprung_mass_kg": unsprung, "items": items}


def _support_weight(s: dict) -> float:
    """Weight for distributing load among supports: the vertical spring rate (rigid = 1)."""
    susp = s.get("suspension", {})
    k = susp.get("stiffness_N_per_mm")
    if susp.get("type") in ("spring", "kc_table") and k:
        return float(k)
    return 1.0


def step_geometry(radius_mm: float, step_mm: float) -> tuple[float, float]:
    """(corner distance d, force ratio d/(r - h)) for a rigid wheel on a step corner."""
    r, h = float(radius_mm), float(step_mm)
    if h >= r:
        raise DesignError(f"step height {h:g} mm is not below the wheel radius {r:g} mm: a rigid wheel cannot climb it (use a larger wheel or a lower step)")
    d = math.sqrt(2.0 * r * h - h * h)
    return d, d / (r - h)


def support_loads(design: dict, g_factor: float = 1.0, step_mm: float | None = None,
                  step_supports: list[str] | None = None, summary: dict | None = None) -> dict:
    """Forces from the ground on the vehicle at every support.

    At rest (step_mm None): vertical loads only. With a step: the listed
    supports rest on the step corner at the moment they lift off; the
    corner pushes back and up along the line to the wheel centre
    (F = k W, k = d / (r - h)), and the driven supports that are not on the
    step push the vehicle forward with equal traction. When the pushes are
    not symmetric, tyre side forces hold the yaw.

    Vertical and side forces come from rigid-body equilibrium (Fz, Mx, My,
    Fy, Mz); with more unknowns than equations the spread is the one with
    least spring energy (rigid body on the suspension springs,
    sum (W_i^2 + Fy_i^2) / k_i minimal). At rest this is the rigid body on
    springs and the side forces are zero.

    Returns {"supports": {id: {"point_mm", "force_N", "contact", "k"}}, "weight_N",
             "cg_mm", "mode", "traction_N", "required_mu_push"}.
    """
    supports = design.get("supports", [])
    if len(supports) < 3:
        raise DesignError(f"need at least 3 supports for static balance, the design has {len(supports)}")
    summ = summary or mass_summary(design)
    wt = summ["mass_kg"] * STANDARD_GRAVITY * float(g_factor)
    cg = summ["cg_mm"]
    ids = [s["id"] for s in supports]
    step_set = list(step_supports or [])
    if step_mm is not None:
        unknown = [s for s in step_set if s not in ids]
        if unknown:
            raise DesignError(f"step supports {', '.join(unknown)} are not in design.supports ({', '.join(ids)})")
        if not step_set:
            raise DesignError("a step load needs at least one support on the step")

    n = len(supports)
    centers = np.array([np.asarray(s["position_mm"], float) for s in supports])
    contacts = np.array([support_contact(s) for s in supports])
    ks = np.zeros(n)
    on_step = np.zeros(n, dtype=bool)
    pushers: list[int] = []
    if step_mm is not None:
        for i, s in enumerate(supports):
            if s["id"] in step_set:
                if s["kind"] == "leg":
                    raise DesignError(f"support {s['id']!r} is a leg; only wheels and casters can climb a step")
                on_step[i] = True
                ks[i] = step_geometry(s["radius_mm"], step_mm)[1]
            elif s["actuation"]["drive"]:
                pushers.append(i)
        if not pushers:
            stepping = ", ".join(step_set)
            kmax = float(ks.max())
            raise DesignError(
                f"no driven support off the step to push {stepping} over it. This model handles a pushed climb; "
                f"a wheel climbing by its own torque needs a friction coefficient of {kmax:.2f} at the step corner. "
                f"Drive a support that stays on the ground, or lower the step")

    # Unknowns u = [W_1..W_n, Fy_1..Fy_n] (vertical and side force at each
    # support's point of action: the step corner or the ground contact). x
    # forces follow from W: -k W on the step, and their sum shared equally by
    # the pushers. The corner push points at the wheel centre.
    pts = contacts.copy()
    for i, s in enumerate(supports):
        if on_step[i]:
            d, _ = step_geometry(s["radius_mm"], step_mm)
            pts[i] = centers[i] + np.array([d, 0.0, -(float(s["radius_mm"]) - step_mm)])
    ybar_push = float(np.mean(pts[pushers, 1])) if pushers else 0.0
    zbar_push = float(np.mean(pts[pushers, 2])) if pushers else 0.0
    A = np.zeros((5, 2 * n))
    A[0, :n] = 1.0                                   # sum Fz = weight
    A[1, :n] = pts[:, 1]                             # sum Mx = y Fz - z Fy
    A[1, n:] = -pts[:, 2]
    A[2, :n] = -pts[:, 0] + ks * (zbar_push - pts[:, 2])  # sum My = z Fx - x Fz
    A[3, n:] = 1.0                                   # sum Fy = 0
    A[4, n:] = pts[:, 0]                             # sum Mz = x Fy - y Fx
    A[4, :n] = -ks * (ybar_push - pts[:, 1])
    b = np.array([wt, cg[1] * wt, -cg[0] * wt, 0.0, 0.0])
    c = np.array([_support_weight(s) for s in supports])
    C = np.diag(np.concatenate([c, c]))
    M = A @ C @ A.T
    if np.linalg.cond(M) > 1e12:
        raise DesignError("the supports are in a line, so the vehicle cannot stand; spread the supports in x and y")
    u = C @ A.T @ np.linalg.solve(M, b)
    W, fy = u[:n], u[n:]

    traction_total = float(np.sum(ks * W))
    fx = np.where(on_step, -ks * W, 0.0)
    for p in pushers:
        fx[p] = traction_total / len(pushers)
    out = {}
    for i, s in enumerate(supports):
        if on_step[i]:
            out[s["id"]] = {"point_mm": pts[i], "force_N": np.array([fx[i], fy[i], W[i]]),
                            "contact": "step_corner", "k": float(ks[i])}
        else:
            out[s["id"]] = {"point_mm": contacts[i], "force_N": np.array([fx[i], fy[i], W[i]]),
                            "contact": "ground", "k": 0.0}
    push_w = [W[p] for p in pushers]
    return {
        "supports": out,
        "weight_N": wt,
        "cg_mm": cg,
        "mode": "rest" if step_mm is None else "pushed_step",
        "traction_N": traction_total,
        "required_mu_push": (traction_total / sum(push_w)) if push_w and sum(push_w) > 0 else 0.0,
    }


def resultant(forces: list[tuple[np.ndarray, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    """Sum of forces and of their moments about the origin, for (point, force) pairs."""
    F = np.zeros(3)
    M = np.zeros(3)
    for p, f in forces:
        F += f
        M += np.cross(p, f)
    return F, M

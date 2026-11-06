"""Natural frequencies of the driveshafts: a small finite element model of each shaft alone.

    from hiraku.solvers.k1_frame.shaft import driveshaft_modes
    r = driveshaft_modes(design)   # {"bending": {part id: Hz}, "torsion": {part id: Hz}, "inputs": {part id: {...}}}

Bending: the tube between its two joints as Euler-Bernoulli beam elements (cubic shape functions,
consistent mass), held at both joints against moving across and free to turn (pinned at both
ends: assumption, the joints carry no bending moment). The first frequency is reported.

Torsion: the tube as torsion elements (linear shape functions, consistent polar inertia) between
two discs: at the inner joint the motor and the reducer's turning inertia as seen at the shafts,
(J_rotor i^2 + J_gear) shared equally by the driveshafts (the mode in which both driven wheels
turn together against the motor, the differential not moving), and at the outer joint the wheel
(design variable wheel_inertia), turning free (the tyre's grip on the road is left out:
assumption). The first frequency above the free turning (0 Hz) is reported.

Inputs read off the design: the joints from the driveshaft part's box (its axis, kit/links.py
link_box), the tube's outer diameter and wall from the design variables driveshaft_diameter and
driveshaft_wall, the steel of the frame (the material most members use: assumption, the generator
gives the shafts the frame's density), and the inertias from the design variables
motor_rotor_inertia, reducer_output_inertia, gear_ratio and wheel_inertia.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
from scipy.linalg import eigh

N_ELEMENTS = 20


def tube(D: float, t: float) -> dict[str, float]:
    """Section of a round tube (mm): A, I (bending), J (polar)."""
    d = D - 2.0 * t
    return {"A": math.pi / 4.0 * (D ** 2 - d ** 2), "I": math.pi / 64.0 * (D ** 4 - d ** 4), "J": math.pi / 32.0 * (D ** 4 - d ** 4)}


def _bending_system(L_mm: float, D_mm: float, t_mm: float, E_MPa: float, rho: float, n: int):
    """(K, M, keep): beam elements of a tube pinned at both ends; keep = the free degrees of freedom."""
    sec = tube(D_mm, t_mm)
    EI = E_MPa * 1e6 * sec["I"] * 1e-12  # N m^2
    mu = rho * sec["A"] * 1e-6  # kg/m
    le = L_mm / 1000.0 / n
    k = EI / le ** 3 * np.array([[12, 6 * le, -12, 6 * le], [6 * le, 4 * le ** 2, -6 * le, 2 * le ** 2],
                                 [-12, -6 * le, 12, -6 * le], [6 * le, 2 * le ** 2, -6 * le, 4 * le ** 2]])
    m = mu * le / 420.0 * np.array([[156, 22 * le, 54, -13 * le], [22 * le, 4 * le ** 2, 13 * le, -3 * le ** 2],
                                    [54, 13 * le, 156, -22 * le], [-13 * le, -3 * le ** 2, -22 * le, 4 * le ** 2]])
    size = 2 * (n + 1)
    K, M = np.zeros((size, size)), np.zeros((size, size))
    for e in range(n):
        i = slice(2 * e, 2 * e + 4)
        K[i, i] += k
        M[i, i] += m
    keep = [i for i in range(size) if i not in (0, 2 * n)]  # no sideways motion at the two joints
    return K, M, keep


def bending_frequencies(L_mm: float, D_mm: float, t_mm: float, E_MPa: float, rho: float, n: int = N_ELEMENTS) -> np.ndarray:
    """Bending frequencies (Hz) of a tube pinned at both ends, from n beam elements."""
    K, M, keep = _bending_system(L_mm, D_mm, t_mm, E_MPa, rho, n)
    w2 = eigh(K[np.ix_(keep, keep)], M[np.ix_(keep, keep)], eigvals_only=True)
    return np.sqrt(np.clip(w2, 0.0, None)) / (2.0 * math.pi)


def _torsion_system(L_mm: float, D_mm: float, t_mm: float, G_MPa: float, rho: float, J_in: float, J_out: float,
                    n: int):
    """(K, M): torsion elements of a tube between two discs (kg*m^2), node 0 at J_in."""
    sec = tube(D_mm, t_mm)
    GJ = G_MPa * 1e6 * sec["J"] * 1e-12  # N m^2
    rJ = rho * sec["J"] * 1e-12  # kg m (polar inertia per length)
    le = L_mm / 1000.0 / n
    K, M = np.zeros((n + 1, n + 1)), np.zeros((n + 1, n + 1))
    for e in range(n):
        i = slice(e, e + 2)
        K[i, i] += GJ / le * np.array([[1, -1], [-1, 1]])
        M[i, i] += rJ * le / 6.0 * np.array([[2, 1], [1, 2]])
    M[0, 0] += J_in
    M[n, n] += J_out
    return K, M


def torsion_frequencies(L_mm: float, D_mm: float, t_mm: float, G_MPa: float, rho: float, J_in: float, J_out: float,
                        n: int = N_ELEMENTS) -> np.ndarray:
    """Torsion frequencies (Hz) of a tube between two discs (kg*m^2), free to turn; the first is the free turning (0)."""
    K, M = _torsion_system(L_mm, D_mm, t_mm, G_MPa, rho, J_in, J_out, n)
    w2 = eigh(K, M, eigvals_only=True)
    return np.sqrt(np.clip(w2, 0.0, None)) / (2.0 * math.pi)


def _var(design: dict, name: str, unit: str) -> float | None:
    from ..k0_handcalc import units

    q = design.get("variables", {}).get(name)
    return None if q is None else units.to_unit(q, unit, name)


def _steel(design: dict) -> dict:
    used = Counter(m["material"] for m in design.get("members", []))
    if not used:
        raise ValueError("no frame members to take the shafts' steel from")
    return design["materials"][used.most_common(1)[0][0]]


def shaft_inputs(design: dict) -> dict[str, dict[str, float]]:
    """For each driveshaft part: {L (mm), D, t (mm), E (MPa), nu, rho (kg/m^3), J_in, J_out (kg*m^2)}.
    Empty when the design has no driveshafts or lacks the variables."""
    from ..k4_geometry.capsule import from_obb
    from ..k4_geometry.obb import from_box

    shafts = [p for p in design.get("parts", []) if p["kind"] == "driveshaft"]
    names = ("driveshaft_diameter", "driveshaft_wall", "motor_rotor_inertia", "reducer_output_inertia", "gear_ratio",
             "wheel_inertia")
    units_ = ("mm", "mm", "kg*m^2", "kg*m^2", "1", "kg*m^2")
    vals = {n: _var(design, n, u) for n, u in zip(names, units_)}
    if not shafts or any(v is None for v in vals.values()):
        return {}
    mat = _steel(design)
    j_in = (vals["motor_rotor_inertia"] * vals["gear_ratio"] ** 2 + vals["reducer_output_inertia"]) / len(shafts)
    out = {}
    for p in shafts:
        c = from_obb(from_box(p["box"]["center_mm"], p["box"]["size_mm"], p["box"].get("rpy_deg")))
        out[p["id"]] = {"L": float(np.linalg.norm(c.b - c.a)), "D": vals["driveshaft_diameter"], "t": vals["driveshaft_wall"],
                        "E": float(mat["E_MPa"]), "nu": float(mat["nu"]), "rho": float(mat["density_kg_m3"]),
                        "J_in": j_in, "J_out": vals["wheel_inertia"]}
    return out


def _unit_shape(u: np.ndarray) -> np.ndarray:
    """A shape over its largest value, the largest positive."""
    k = int(np.argmax(np.abs(u)))
    return u / u[k] if u[k] != 0 else u


def bending_shape(L_mm: float, D_mm: float, t_mm: float, E_MPa: float, rho: float, n: int = N_ELEMENTS) -> np.ndarray:
    """Sideways displacement at the n + 1 element nodes (joint to joint) of the first bending mode of
    bending_frequencies' model, over its largest."""
    K, M, keep = _bending_system(L_mm, D_mm, t_mm, E_MPa, rho, n)
    _, vec = eigh(K[np.ix_(keep, keep)], M[np.ix_(keep, keep)], subset_by_index=[0, 0])
    full = np.zeros(K.shape[0])
    full[keep] = vec[:, 0]
    return _unit_shape(full[0::2])


def torsion_shape(L_mm: float, D_mm: float, t_mm: float, G_MPa: float, rho: float, J_in: float, J_out: float,
                  n: int = N_ELEMENTS) -> np.ndarray:
    """Twist at the n + 1 element nodes (node 0 at the inner disc J_in) of the first torsion mode above the free
    turning of torsion_frequencies' model, over its largest."""
    K, M = _torsion_system(L_mm, D_mm, t_mm, G_MPa, rho, J_in, J_out, n)
    _, vec = eigh(K, M, subset_by_index=[1, 1])
    return _unit_shape(vec[:, 0])


def shaft_ends(design: dict, pid: str) -> tuple[np.ndarray, np.ndarray]:
    """(inner joint, outer joint) in mm: the ends of the axis of the part's box; the outer is the end nearer the
    point (at_mm) where the part's first connects_to link with a point meets the wheel, or without one the end
    farther from the centre plane y = 0."""
    from ..k4_geometry.capsule import from_obb
    from ..k4_geometry.obb import from_box

    p = next(x for x in design["parts"] if x["id"] == pid)
    c = from_obb(from_box(p["box"]["center_mm"], p["box"]["size_mm"], p["box"].get("rpy_deg")))
    a, b = np.asarray(c.a, float), np.asarray(c.b, float)
    at = next((np.asarray(l["at_mm"], float) for l in p.get("connects_to", []) if "at_mm" in l), None)
    outer_is_b = (np.linalg.norm(b - at) < np.linalg.norm(a - at)) if at is not None else abs(b[1]) > abs(a[1])
    return (a, b) if outer_is_b else (b, a)


def driveshaft_shapes(design: dict) -> dict[str, dict]:
    """For every driveshaft of driveshaft_modes: {"inner_mm", "outer_mm", "bending", "torsion"}, the two shapes at
    the N_ELEMENTS + 1 element nodes evenly from the inner joint to the outer one."""
    out = {}
    for pid, i in shaft_inputs(design).items():
        G = i["E"] / (2.0 * (1.0 + i["nu"]))
        inner, outer = shaft_ends(design, pid)
        out[pid] = {"inner_mm": inner, "outer_mm": outer,
                    "bending": bending_shape(i["L"], i["D"], i["t"], i["E"], i["rho"]),
                    "torsion": torsion_shape(i["L"], i["D"], i["t"], G, i["rho"], i["J_in"], i["J_out"])}
    return out


def driveshaft_modes(design: dict) -> dict:
    """The first bending and torsion frequency (Hz) of every driveshaft and the inputs used."""
    ins = shaft_inputs(design)
    bend, tors = {}, {}
    for pid, i in ins.items():
        bend[pid] = float(bending_frequencies(i["L"], i["D"], i["t"], i["E"], i["rho"])[0])
        G = i["E"] / (2.0 * (1.0 + i["nu"]))
        tors[pid] = float(torsion_frequencies(i["L"], i["D"], i["t"], G, i["rho"], i["J_in"], i["J_out"])[1])
    return {"bending": bend, "torsion": tors, "inputs": ins}

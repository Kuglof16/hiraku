"""Fatigue on the K1 frame: stress per unit wheel force, rainflow counting,
S-N curve and Miner's rule.

Stress history: with inertia relief, a unit force at a wheel is balanced by
the masses' d'Alembert loads, so the frame stress is linear in the wheel
forces and the weight is already inside them: sigma_p(t) = sum_k S_pk F_k(t).
Points: the four corners (eight points round a circular tube) at both ends
of every member, where the welds are. Normal stress only (axial and the two
bendings); torsional shear is not counted for fatigue.
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc.vehicle import mass_summary, inertia_tensor, support_contact
from .model import COMBO, build


# ---------------------------------------------------------------- rainflow (ASTM E1049-85, 5.4.4)

def reversals(series) -> list[float]:
    """Peaks and valleys of a series (first and last points kept)."""
    x = np.asarray(series, float).ravel()
    if x.size == 0:
        return []
    x = x[np.concatenate([[True], x[1:] != x[:-1]])]  # repeated values are one point
    if x.size < 3:
        return x.tolist()
    d = np.diff(x)
    turn = d[:-1] * d[1:] < 0  # the direction changes at x[1:-1]
    return x[np.concatenate([[True], turn, [True]])].tolist()


def rainflow(series) -> list[tuple[float, float, float]]:
    """Cycles as (range, mean, count) with count 1.0 or 0.5 (ASTM E1049-85 rainflow counting)."""
    stack: list[float] = []
    out: list[tuple[float, float, float]] = []
    for v in reversals(series):
        stack.append(v)
        while len(stack) >= 3:
            X = abs(stack[-1] - stack[-2])
            Y = abs(stack[-2] - stack[-3])
            if X < Y:
                break
            if len(stack) == 3:
                out.append((Y, (stack[0] + stack[1]) / 2.0, 0.5))
                stack.pop(0)
            else:
                out.append((Y, (stack[-2] + stack[-3]) / 2.0, 1.0))
                last = stack.pop()
                stack.pop()
                stack.pop()
                stack.append(last)
    for a, b in zip(stack, stack[1:]):
        out.append((abs(b - a), (a + b) / 2.0, 0.5))
    return out


# ---------------------------------------------------------------- S-N and Miner

def sn_cycles(stress_range: float, fat: float, m: float, n_knee: float, m2: float) -> float:
    """Cycles to failure at a stress range (MPa) on an IIW-type curve: FAT at 2e6 cycles, slope m,
    then slope m2 beyond the knee (n_knee cycles)."""
    if stress_range <= 0:
        return math.inf
    s_knee = fat * (2e6 / n_knee) ** (1.0 / m)
    if stress_range >= s_knee:
        log_n = math.log(2e6) + m * math.log(fat / stress_range)
    else:
        log_n = math.log(n_knee) + m2 * math.log(s_knee / stress_range)
    return math.exp(log_n) if log_n < 700.0 else math.inf


def miner(cycles: list[tuple[float, float, float]], fat: float, m: float, n_knee: float, m2: float) -> float:
    return sum(c / sn_cycles(r, fat, m, n_knee, m2) for r, _, c in cycles if r > 0)


def equivalent_range(cycles: list[tuple[float, float, float]], m: float, n_ref: float = 2e6) -> float:
    """Constant stress range that does the same damage in n_ref cycles on a single-slope curve."""
    s = sum(c * r**m for r, _, c in cycles)
    return (s / n_ref) ** (1.0 / m) if s > 0 else 0.0


# ---------------------------------------------------------------- influence of unit wheel forces

def stress_points(fm) -> list[tuple[str, str, int]]:
    """(member, end 'a'/'b', corner index) for every evaluated point."""
    out = []
    for mid, mp in fm.props.items():
        n = 8 if mp["section"]["round"] else 4
        for end in ("a", "b"):
            for c in range(n):
                out.append((mid, end, c))
    return out


def _point_stresses(fm) -> np.ndarray:
    vals = []
    for mid, mp in fm.props.items():
        s = mp["section"]
        pm = fm.model.members[mid]
        L = pm.L()
        for x in (0.0, L):
            N = pm.axial(x, COMBO)
            My = pm.moment("My", x, COMBO)
            Mz = pm.moment("Mz", x, COMBO)
            if s["round"]:
                for k in range(8):
                    th = 2 * math.pi * k / 8
                    vals.append(N / s["A"] + (My * math.sin(th) + Mz * math.cos(th)) * s["cy"] / s["Iz"])
            else:
                for s1, s2 in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                    vals.append(N / s["A"] + s1 * My * s["cz"] / s["Iy"] + s2 * Mz * s["cy"] / s["Iz"])
    return np.array(vals)


def influence(design: dict, directions: str = "xyz") -> tuple[np.ndarray, list[tuple[str, str]], list[tuple[str, str, int]]]:
    """S (points x columns): stress (MPa) per 1 N at each support's ground contact, per direction.
    Each unit case is balanced by inertia relief (no gravity: it is inside the wheel forces)."""
    summ = mass_summary(design)
    items, cg, m = summ["items"], summ["cg_mm"], summ["mass_kg"]
    Jinv = np.linalg.inv(inertia_tensor(items, cg))
    cols, columns = [], []
    points = None
    for s in design["supports"]:
        p = support_contact(s)
        for d in directions:
            f = np.zeros(3)
            f["xyz".index(d)] = 1.0
            fm = build(design)
            fm.add_force_at(fm.wheel_nodes[s["id"]], p, f)
            a = f / m
            alpha = Jinv @ np.cross(p - cg, f) * 1e3
            fm.add_body_loads(items, -a, -alpha, cg)
            fm.restrain_free_body()
            fm.solve()
            cols.append(_point_stresses(fm))
            columns.append((s["id"], d))
            if points is None:
                points = stress_points(fm)
    return np.column_stack(cols), columns, points

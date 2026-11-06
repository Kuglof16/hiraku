"""K0 formulas for a frontal impact into a rigid wall, and the crush zone read from the design.

The crush zone (crush_zone(design)): the front frame's rails from the front of
the frame back to the first cross member behind the front wheels' mounts. Each
rail's tubes fold progressively at a mean crushing force from the tube's section
and material (tube_mean_crush_force); the rails crush side by side, so the crush
force is the sum over the rails (each rail at its weakest tube). The crushing
members (crushing_members(design)) are the rails' members ahead of the station
where the crush zone ends.

Sources (not checked: the texts were not read in this repository):
- square / rectangular tube: Wierzbicki T., Abramowicz W., "On the crushing
  mechanics of thin-walled structures", J. Appl. Mech. 50 (1983) 727-734:
  P_m = 9.56 sigma_0 t^(5/3) C^(1/3), C = (b + h) / 2.
- circular tube: Alexander J.M., "An approximate analysis of the collapse of thin
  cylindrical shells under axial loading", Q. J. Mech. Appl. Math. 13 (1960)
  10-15: P_m = 6 sigma_0 t^1.5 sqrt(D), D the mean diameter (d - t).
- flow stress sigma_0 = (yield + tensile) / 2 when the material gives its tensile
  strength, else the yield stress (the usual choice in the crush literature, e.g.
  Jones N., Structural Impact; not checked).
- usable crush length: 70 % of the crush zone's length, the rest is the folds'
  solid length (HIRAKU's assumption, not checked).
"""

from __future__ import annotations

import math

import numpy as np
import sympy as sp

from .library_dynamics import G, G_NOTE
from .registry import Formula
from .vehicle import DesignError

WA_COEF = 9.56  # Wierzbicki & Abramowicz (1983), square tube, not checked
ALEXANDER_COEF = 6.0  # Alexander (1960), circular tube, not checked
USABLE_FRACTION = 0.70  # HIRAKU's assumption: the folds' solid length takes the other 30 %
LONGITUDINAL = 0.9  # a rail member runs within about 25 deg of the vehicle's x axis
LATERAL = 0.9  # a cross member runs within about 25 deg of the y axis

SOURCE_RECT = ("Wierzbicki & Abramowicz (1983), J. Appl. Mech. 50: P_m = 9.56 sigma_0 t^(5/3) C^(1/3), "
               "C = (b + h) / 2 (not checked)")
SOURCE_ROUND = ("Alexander (1960), Q. J. Mech. Appl. Math. 13: P_m = 6 sigma_0 t^1.5 sqrt(D), D the mean "
                "diameter (not checked)")


def flow_stress(material: dict) -> float:
    """sigma_0 (MPa): (yield + tensile) / 2 when the material has tensile_MPa, else the yield stress."""
    fy = float(material["yield_MPa"])
    fu = material.get("tensile_MPa")
    return (fy + float(fu)) / 2.0 if fu else fy


def tube_mean_crush_force(section: dict, material: dict) -> float:
    """Mean crushing force (N) of a thin-walled tube folding progressively under an axial load.

    section: a design's sections entry, shape rect_tube (dims_mm b, h, t) or round_tube (d, t);
    material: a design's materials entry (yield_MPa, tensile_MPa optional)."""
    s0 = flow_stress(material)
    d = section["dims_mm"]
    t = float(d["t"])
    if section["shape"] == "rect_tube":
        c = (float(d["b"]) + float(d["h"])) / 2.0
        return WA_COEF * s0 * t ** (5.0 / 3.0) * c ** (1.0 / 3.0)
    if section["shape"] == "round_tube":
        return ALEXANDER_COEF * s0 * t ** 1.5 * math.sqrt(float(d["d"]) - t)
    raise DesignError(f"no crushing force for a section of shape {section['shape']!r} (rect_tube, round_tube only)")


def tube_area(section: dict) -> float:
    """Wall area (mm^2), sharp corners."""
    d = section["dims_mm"]
    t = float(d["t"])
    if section["shape"] == "rect_tube":
        return 2.0 * t * (float(d["b"]) + float(d["h"])) - 4.0 * t * t
    return math.pi * (float(d["d"]) - t) * t


def _rails(design: dict) -> tuple[dict, list[list[dict]]]:
    """Node positions and the rail chains: from each front-most frame node, the most nearly longitudinal
    member going rearward, then the next, until none is left."""
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    members = design["members"]
    used = {m[k] for m in members for k in ("node_a", "node_b")}
    xmax = max(nodes[n][0] for n in used)
    chains, taken = [], set()
    for start in sorted(n for n in used if abs(nodes[n][0] - xmax) < 1.0):
        chain, at = [], start
        while True:
            best = None
            for m in members:
                if m["id"] in taken or at not in (m["node_a"], m["node_b"]):
                    continue
                other = m["node_b"] if m["node_a"] == at else m["node_a"]
                d = nodes[other] - nodes[at]
                length = float(np.linalg.norm(d))
                if length <= 0 or d[0] >= 0 or -d[0] / length < LONGITUDINAL:
                    continue
                key = (-d[0] / length, m["id"])
                if best is None or key[0] > best[0] + 1e-12:
                    best = (key[0], m, other)
            if best is None:
                break
            chain.append(best[1])
            taken.add(best[1]["id"])
            at = best[2]
        if chain:
            chains.append(chain)
    return nodes, chains


def _front_mounts_x(design: dict) -> tuple[float, str]:
    """Station (x, mm) of the front wheels' rearmost mount on the frame, and how it was found."""
    sups = design.get("supports", [])
    if not sups:
        raise DesignError("the design has no supports, so there are no front wheels to find the crush zone by")
    xa = max(s["position_mm"][0] for s in sups)
    r = max(float(s.get("radius_mm", 0.0)) for s in sups if abs(s["position_mm"][0] - xa) < 1.0)
    br = [p["box"]["center_mm"][0] for p in design.get("parts", [])
          if p.get("kind") == "bracket" and abs(p["box"]["center_mm"][0] - xa) <= r]
    if br:
        return min(br), "the rearmost bracket within the front wheels' span (x_axle +- wheel radius)"
    return xa, "the front axle (no brackets within the front wheels' span)"


def crush_zone(design: dict) -> dict:
    """The crush zone read from the design: rails, crushing members, end station, forces and lengths (N, mm)."""
    nodes, chains = _rails(design)
    if not chains:
        raise DesignError("no longitudinal rail runs back from the front of the frame, so there is no crush zone")
    x_front = max(float(nodes[m[k]][0]) for c in chains for m in c for k in ("node_a", "node_b"))
    x_mount, how = _front_mounts_x(design)
    chain_nodes = [{m[k] for m in c for k in ("node_a", "node_b")} for c in chains]
    in_chain = {m["id"] for c in chains for m in c}
    cross = []
    for m in design["members"]:
        if m["id"] in in_chain:
            continue
        a, b = m["node_a"], m["node_b"]
        ia = [i for i, s in enumerate(chain_nodes) if a in s]
        ib = [i for i, s in enumerate(chain_nodes) if b in s]
        if not ia or not ib or set(ia) == set(ib):
            continue
        d = nodes[b] - nodes[a]
        if abs(d[1]) / float(np.linalg.norm(d)) < LATERAL:
            continue
        x = float((nodes[a][0] + nodes[b][0]) / 2.0)
        if x < x_mount - 1e-6:
            cross.append((x, m["id"]))
    if not cross:
        raise DesignError("no cross member joins the rails behind the front wheels' mounts, so the crush zone has no end")
    x_end, end_member = max(cross)
    sections, materials = design["sections"], design["materials"]
    rails = []
    for c in chains:
        crushing = [m for m in c if max(nodes[m["node_a"]][0], nodes[m["node_b"]][0]) > x_end + 1e-6]
        if not crushing:
            continue
        forces = {m["id"]: tube_mean_crush_force(sections[m["section"]], materials[m["material"]]) for m in crushing}
        weakest = min(crushing, key=lambda m: (forces[m["id"]], m["id"]))
        rails.append({"members": [m["id"] for m in crushing], "forces_N": forces, "weakest": weakest["id"],
                      "force_N": forces[weakest["id"]],
                      "EA_N": float(materials[weakest["material"]]["E_MPa"]) * tube_area(sections[weakest["section"]])})
    length = x_front - x_end
    usable = USABLE_FRACTION * length
    solid = length - usable
    return {"rails": rails, "members": [i for r in rails for i in r["members"]],
            "force_N": sum(r["force_N"] for r in rails),
            "x_front_mm": x_front, "x_end_mm": x_end, "end_member": end_member, "x_mounts_mm": x_mount,
            "mounts_how": how, "length_mm": length, "usable_mm": usable,
            "hard_stiffness_N_per_mm": sum(r["EA_N"] for r in rails) / solid}


def crushing_members(design: dict) -> list[str]:
    """Member ids of the front frame's tubes that crush (the rails ahead of the crush zone's end)."""
    return crush_zone(design)["members"]


def _sym(*names: str) -> dict[str, sp.Symbol]:
    return {n: sp.Symbol(n, real=True) for n in names}


def _in(unit: str, description: str, **kw) -> dict:
    return {"unit": unit, "description": description, **kw}


def _plastic(m, vm, F, kb, st):
    """(E, E_b, s, x_max, a_mean, t_stop) in SI from SI symbols: bumper to its stroke, then crushing at F."""
    E = m * vm**2 / 2
    E_b = kb * st**2 / 2
    s = (E - E_b) / F
    w = sp.sqrt(kb / m)
    t1 = sp.asin(w * st / vm) / w
    v1 = sp.sqrt(vm**2 - kb * st**2 / m)
    return E, E_b, s, st + s, F / m, t1 + m * v1 / F


def all_formulas() -> list[Formula]:
    out = []
    s = _sym("m", "v", "k")
    m, v, k = s["m"], s["v"], s["k"]
    vm = v / 3.6
    km = k * 1000  # N/mm -> N/m
    out.append(Formula(
        formula_id="crash.linear_spring",
        title="線形のばねで壁に当たったときの最大の減速度とつぶれ",
        domain="crash",
        expression="a_max = v sqrt(k/m); x_max = v sqrt(m/k); t_stop = (pi/2) sqrt(m/k); E = m v^2 / 2",
        inputs={"m": _in("kg", "mass of the vehicle with its load", min=0),
                "v": _in("km/h", "impact speed", min=0),
                "k": _in("N/mm", "stiffness of the front against the wall", min=0)},
        outputs={"a_max": ("g", "largest deceleration", vm * sp.sqrt(km / m) / G),
                 "x_max": ("mm", "largest crush", vm * sp.sqrt(m / km) * 1000),
                 "t_stop": ("s", "time from contact to standstill", sp.pi / 2 * sp.sqrt(m / km)),
                 "E": ("J", "kinetic energy taken by the front", m * vm**2 / 2)},
        assumptions=["one mass on one linear spring", "rigid wall", "no damping, no rebound counted", G_NOTE],
        source="undamped mass-spring impact, a quarter period of free vibration (textbook dynamics)",
        requires=[(sp.And(m > 0, k > 0), "m and k must be positive")],
        symbols=s,
    ))
    s = _sym("m", "v", "k", "s")
    m, v, k, sd = s["m"], s["v"], s["k"], s["s"]
    out.append(Formula(
        formula_id="crash.occupant_space_margin",
        title="壁に当たってつぶれたあと、乗員の前に残る長さ",
        domain="crash",
        expression="margin = s - x_max; x_max = v sqrt(m/k)",
        inputs={"m": _in("kg", "mass of the vehicle with its load", min=0),
                "v": _in("km/h", "impact speed", min=0),
                "k": _in("N/mm", "stiffness of the front against the wall", min=0),
                "s": _in("mm", "length from the front of the vehicle back to the occupants' front", min=0)},
        outputs={"margin": ("mm", "length left in front of the occupants (negative: the crush reaches them)",
                            sd - v / 3.6 * sp.sqrt(m / (k * 1000)) * 1000)},
        assumptions=["one mass on one linear spring", "rigid wall", "the crush takes the length from the front back"],
        source="undamped mass-spring impact (textbook dynamics) and a difference of lengths",
        requires=[(sp.And(m > 0, k > 0), "m and k must be positive")],
        symbols=s,
    ))
    s = _sym("m", "v", "s")
    m, v, sd = s["m"], s["v"], s["s"]
    vm = v / 3.6
    out.append(Formula(
        formula_id="crash.mean_deceleration",
        title="つぶれる距離から出す平均の減速度",
        domain="crash",
        expression="a_mean = v^2 / (2 s); F_mean = m a_mean; E = m v^2 / 2",
        inputs={"m": _in("kg", "mass of the vehicle with its load", min=0),
                "v": _in("km/h", "impact speed", min=0),
                "s": _in("mm", "crush distance", min=0)},
        outputs={"a_mean": ("g", "mean deceleration over the crush", vm**2 / (2 * sd / 1000) / G),
                 "F_mean": ("N", "mean wall force", m * vm**2 / (2 * sd / 1000)),
                 "E": ("J", "kinetic energy taken by the front", m * vm**2 / 2)},
        assumptions=["constant deceleration over the crush distance", "rigid wall", G_NOTE],
        source="work-energy: F s = m v^2 / 2 (textbook dynamics)",
        requires=[(sd > 0, "the crush distance s must be positive")],
        symbols=s,
    ))
    # -- the crush zone as plastic folding tubes
    s = _sym("sigma_0", "b", "h", "t", "n")
    s0, b, h, t, n = s["sigma_0"], s["b"], s["h"], s["t"], s["n"]
    out.append(Formula(
        formula_id="crash.rect_tube_crush",
        title="角パイプが折れ畳まれてつぶれるときの平均の力",
        domain="crash",
        expression="F_m = n 9.56 sigma_0 t^(5/3) ((b + h)/2)^(1/3)",
        inputs={"sigma_0": _in("MPa", "flow stress: (yield + tensile)/2, or the yield stress without a tensile strength",
                               min=0),
                "b": _in("mm", "tube width", min=0), "h": _in("mm", "tube height", min=0),
                "t": _in("mm", "wall thickness", min=0),
                "n": _in("1", "number of such tubes crushing side by side", min=1)},
        outputs={"F_m": ("N", "mean crushing force of the n tubes",
                         n * WA_COEF * s0 * t ** sp.Rational(5, 3) * ((b + h) / 2) ** sp.Rational(1, 3))},
        assumptions=["progressive folding of a thin-walled tube under an axial load (no global bending)",
                     "rigid-perfectly plastic material at the flow stress", "the tubes crush side by side: forces add"],
        source=SOURCE_RECT,
        requires=[(sp.And(t > 0, b > 0, h > 0), "b, h and t must be positive")],
        symbols=s,
    ))
    s = _sym("sigma_0", "d", "t", "n")
    s0, dd, t, n = s["sigma_0"], s["d"], s["t"], s["n"]
    out.append(Formula(
        formula_id="crash.round_tube_crush",
        title="丸パイプが折れ畳まれてつぶれるときの平均の力",
        domain="crash",
        expression="F_m = n 6 sigma_0 t^1.5 sqrt(d - t)",
        inputs={"sigma_0": _in("MPa", "flow stress: (yield + tensile)/2, or the yield stress without a tensile strength",
                               min=0),
                "d": _in("mm", "outer diameter", min=0), "t": _in("mm", "wall thickness", min=0),
                "n": _in("1", "number of such tubes crushing side by side", min=1)},
        outputs={"F_m": ("N", "mean crushing force of the n tubes",
                         n * ALEXANDER_COEF * s0 * t ** sp.Rational(3, 2) * sp.sqrt(dd - t))},
        assumptions=["axisymmetric progressive folding of a thin-walled tube (no global bending)",
                     "rigid-perfectly plastic material at the flow stress", "D in the source is the mean diameter d - t"],
        source=SOURCE_ROUND,
        requires=[(dd > t, "the diameter must be larger than the wall")],
        symbols=s,
    ))
    s = _sym("m", "v", "F_m", "k_b", "stroke")
    m, v, F, kb, st = s["m"], s["v"], s["F_m"], s["k_b"], s["stroke"]
    E, E_b, sc, x_max, a_mean, t_stop = _plastic(m, v / 3.6, F, kb * 1000, st / 1000)
    plastic_assume = ["the body is rigid; the bumper is a linear spring up to its stroke, then the crush zone folds "
                      "at its mean force F_m (rigid-plastic, no spring back)",
                      "the bumper bottoms out before the crush zone folds (k_b stroke <= F_m)",
                      "the crush stays within the usable crush length (no hard stop)", "rigid wall", G_NOTE]
    out.append(Formula(
        formula_id="crash.plastic_crush",
        title="バンパーのばねのあと、つぶれる所が平均の力で塑性でつぶれるときのつぶれと減速度 (エネルギーの釣り合い)",
        domain="crash",
        expression=("E = m v^2/2; E_b = k_b stroke^2/2; s = (E - E_b)/F_m; x_max = stroke + s; "
                    "a_max = a_mean = F_m/m; t_stop = asin(w stroke/v)/w + m sqrt(v^2 - k_b stroke^2/m)/F_m, "
                    "w = sqrt(k_b/m)"),
        inputs={"m": _in("kg", "mass of the vehicle with its load", min=0),
                "v": _in("km/h", "impact speed", min=0),
                "F_m": _in("N", "mean crushing force of the crush zone (sum over the rails)", min=0),
                "k_b": _in("N/mm", "bumper stiffness", min=0),
                "stroke": _in("mm", "bumper stroke", min=0)},
        outputs={"s": ("mm", "crush of the crush zone", sc * 1000),
                 "x_max": ("mm", "largest crush of the front (bumper stroke + crush)", x_max * 1000),
                 "a_mean": ("g", "deceleration while the crush zone folds", a_mean / G),
                 "a_max": ("g", "largest deceleration (the folding force; no first peak counted)", a_mean / G),
                 "t_stop": ("s", "time from contact to standstill", t_stop),
                 "E": ("J", "kinetic energy taken by the front", E),
                 "E_b": ("J", "elastic energy left in the bumper", E_b)},
        assumptions=plastic_assume,
        source="work-energy F_m s = m v^2/2 - E_b and the motion in two pieces (a quarter of a harmonic arc, "
               "then constant deceleration) (textbook dynamics)",
        requires=[(sp.And(m > 0, F > 0, kb > 0, st > 0), "m, F_m, k_b and stroke must be positive"),
                  (kb * 1000 * st / 1000 <= F, "the bumper must bottom out before the crush zone folds (k_b stroke <= F_m)"),
                  (m * (v / 3.6) ** 2 / 2 > kb * 1000 * (st / 1000) ** 2 / 2, "the bumper alone stops the vehicle")],
        symbols=s,
    ))
    s = _sym("m", "v", "F_m", "k_b", "stroke", "s")
    m, v, F, kb, st, sd = s["m"], s["v"], s["F_m"], s["k_b"], s["stroke"], s["s"]
    x_max = _plastic(m, v / 3.6, F, kb * 1000, st / 1000)[3]
    out.append(Formula(
        formula_id="crash.plastic_occupant_space_margin",
        title="塑性でつぶれたあと、乗員の前に残る長さ",
        domain="crash",
        expression="margin = s - x_max; x_max = stroke + (m v^2/2 - k_b stroke^2/2) / F_m",
        inputs={"m": _in("kg", "mass of the vehicle with its load", min=0),
                "v": _in("km/h", "impact speed", min=0),
                "F_m": _in("N", "mean crushing force of the crush zone (sum over the rails)", min=0),
                "k_b": _in("N/mm", "bumper stiffness", min=0),
                "stroke": _in("mm", "bumper stroke", min=0),
                "s": _in("mm", "length from the front of the vehicle back to the occupants' front", min=0)},
        outputs={"margin": ("mm", "length left in front of the occupants (negative: the crush reaches them)",
                            sd - x_max * 1000)},
        assumptions=plastic_assume + ["the crush takes the length from the front back"],
        source="work-energy F_m s = m v^2/2 - E_b (textbook dynamics) and a difference of lengths",
        requires=[(sp.And(m > 0, F > 0, kb > 0, st > 0), "m, F_m, k_b and stroke must be positive")],
        symbols=s,
    ))
    return out

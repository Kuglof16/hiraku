"""frame_static: stresses, deflection and wheel loads of the frame (K1 static)."""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc import units
from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k1_frame import analyze, build, stress_terms
from ..k1_frame.model import COMBO
from . import fixtures
from .base import BuiltinTool, Record, check_record, on_board, param_list, param_value, result

CLOSED_FORM_TOL = 1e-3


def frame_outputs(res: dict) -> Record:
    """results / fields / vectors / effects shared by the K1-based tools."""
    fm = res["model"]
    worst = res["worst_member"]
    node = res["worst_node"]
    safe_member = min(res["safety"], key=res["safety"].get)
    results = [
        result("max_stress", res["stresses"][worst]["max"], "MPa", worst),
        result("min_safety_factor", res["safety"][safe_member], "1", safe_member),
        result("max_displacement", float(np.linalg.norm(res["displacements"][node])), "mm", node),
        result("vehicle_mass", res["mass"]["mass_kg"], "kg"),
        result("frame_mass", res["mass"]["frame_mass_kg"], "kg"),
    ]
    for sid, g in res["ground"].items():
        results.append(result(f"wheel_load_{sid}", g["force_N"][2], "N", sid))
    fields = [
        {"kind": "stress", "label": "von Mises (max along each member)", "unit": "MPa",
         "per_member": {m: s["max"] for m, s in res["stresses"].items()}},
        {"kind": "displacement", "label": "frame deflection (wheel-centre rigid motion removed)", "unit": "mm",
         "per_node": {n: [float(v) for v in d] for n, d in res["displacements"].items()}},
    ]
    vectors = [{"kind": kind, "at": sid, "origin_mm": [float(v) for v in p], "vector_N": [float(v) for v in f]}
               for sid, g in res["ground"].items() for kind, p, f in g["vectors"]]
    cg = [float(v) for v in res["mass"]["cg_mm"]]
    vectors.append({"kind": "load", "at": "cg", "origin_mm": cg, "vector_N": [0.0, 0.0, -float(res["weight_N"])]})
    a = res["inertia_accel"]
    if float(np.linalg.norm(a)) * res["mass"]["mass_kg"] > 1e-6 * res["weight_N"]:
        vectors.append({"kind": "inertia", "at": "cg", "origin_mm": cg,
                        "vector_N": [float(v) for v in -res["mass"]["mass_kg"] * a]})
        results.append(result("inertia_acceleration", float(np.linalg.norm(a)), "m/s^2", "cg"))
    return {"results": results, "fields": fields, "vectors": vectors, "effects": stress_terms(res)}


def _beam(length, section, material=fixtures.STEEL):
    return build(fixtures.beam_design(length, section, material))


def frame_known_answers() -> list[Record]:
    """K1 against closed-form beam results (also used by step_climb)."""
    out = []
    E = fixtures.STEEL["E_MPa"]
    nu = fixtures.STEEL["nu"]
    G = E / (2 * (1 + nu))
    b, h, t = 40.0, 80.0, 2.0
    Iz = (b * h**3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12.0
    Iy = (h * b**3 - (h - 2 * t) * (b - 2 * t) ** 3) / 12.0
    A = b * h - (b - 2 * t) * (h - 2 * t)
    rect = {"shape": "rect_tube", "dims_mm": {"b": b, "h": h, "t": t}}
    L, P = 1000.0, 1000.0

    fm = _beam(L, rect)
    fm.model.def_support("A", True, True, True, True, True, True)
    fm.add_force("B", (0.0, 0.0, -P))
    fm.solve()
    src = "cantilever with an end load: deflection P L^3 / (3 E I), root stress P L (h/2) / I"
    out.append(check_record("cantilever 40x80x2 tube, 1 kN down at the tip", "tip_deflection",
                            P * L**3 / (3 * E * Iz), -fm.displacement("B")[2], "mm", CLOSED_FORM_TOL,
                            src + "; vertical load uses the h (strong) axis"))
    out.append(check_record("cantilever 40x80x2 tube, 1 kN down at the tip", "root_stress",
                            P * L * (h / 2) / Iz, fm.member_stress("m")["max"], "MPa", CLOSED_FORM_TOL, src))

    fm = _beam(L, rect)
    fm.model.def_support("A", True, True, True, True, True, True)
    fm.add_force("B", (0.0, P, 0.0))
    fm.solve()
    out.append(check_record("cantilever 40x80x2 tube, 1 kN sideways at the tip", "tip_deflection",
                            P * L**3 / (3 * E * Iy), fm.displacement("B")[1], "mm", CLOSED_FORM_TOL,
                            src + "; sideways load uses the b (weak) axis"))

    span = 2000.0
    fm = _beam(span, rect)
    # PyNite axes here: X along the beam, Y = design z (up), Z = -design y
    fm.model.def_support("A", True, True, True, True, False, False)
    fm.model.def_support("B", False, True, True, False, False, False)
    fm.add_force("m~2", (0.0, 0.0, -P))
    fm.solve()
    out.append(check_record("simply supported 2 m span, 1 kN at mid-span", "mid_deflection",
                            P * span**3 / (48 * E * Iz), -fm.displacement("m~2")[2], "mm", CLOSED_FORM_TOL,
                            "simply supported beam, centre load: P L^3 / (48 E I)"))

    fm = _beam(span, rect)
    fm.model.def_support("A", True, True, True, True, False, False)
    fm.model.def_support("B", False, True, True, False, False, False)
    fm.model.add_member_self_weight("FY", -STANDARD_GRAVITY * 1000.0, "load")
    fm.solve()
    w = fixtures.STEEL["density_kg_m3"] * 1e-12 * A * STANDARD_GRAVITY * 1000.0
    out.append(check_record("simply supported 2 m span under its own weight", "mid_deflection",
                            5 * w * span**4 / (384 * E * Iz), -fm.displacement("m~2")[2], "mm", CLOSED_FORM_TOL,
                            "simply supported beam, uniform load: 5 w L^4 / (384 E I), w = density * A * g"))

    d, tt = 50.0, 3.0
    J = math.pi / 32.0 * (d**4 - (d - 2 * tt) ** 4)
    T = 500_000.0
    fm = _beam(L, {"shape": "round_tube", "dims_mm": {"d": d, "t": tt}})
    fm.model.def_support("A", True, True, True, True, True, True)
    fm.add_force("B", (0.0, 0.0, 0.0), (T, 0.0, 0.0))
    fm.solve()
    twist = fm.model.nodes["B"].RX[COMBO]
    out.append(check_record("cantilever 50x3 round tube, 500 N*m torque at the tip", "tip_twist",
                            T * L / (G * J), twist, "rad", CLOSED_FORM_TOL,
                            "torsion of a round tube: twist T L / (G J)"))
    out.append(check_record("cantilever 50x3 round tube, 500 N*m torque at the tip", "von_mises",
                            math.sqrt(3.0) * T * (d / 2) / J, fm.member_stress("m")["max"], "MPa", CLOSED_FORM_TOL,
                            "shear T r / J, von Mises sqrt(3) tau"))

    lad = fixtures.ladder_design()
    res = analyze(lad)
    m = res["mass"]["mass_kg"]
    a = -float(res["mass"]["cg_mm"][0])
    Lw = 2000.0
    front = sum(res["ground"][s]["force_N"][2] for s in ("FL", "FR"))
    out.append(check_record("symmetric ladder at rest on four rigid wheels", "front_axle_load",
                            m * STANDARD_GRAVITY * (Lw - a) / Lw, front, "N", CLOSED_FORM_TOL,
                            "lever rule m g (L - a) / L (exact for a frame symmetric left to right)"))
    total = sum(g["force_N"][2] for g in res["ground"].values())
    out.append(check_record("symmetric ladder at rest on four rigid wheels", "total_wheel_load",
                            m * STANDARD_GRAVITY, total, "N", 1e-9, "sum of reactions equals the weight"))
    return out


class FrameStatic(BuiltinTool):
    name = "frame_static"
    title = "フレームの静的 (3D の梁)"
    domain = "strength"
    base = "K1"
    tests = ["R1"]
    formulas = [
        "frame_static.rail_step_bending",
        "frame_static.rail_bending",
        "frame_static.simple_beam_point_load",
        "frame_static.simple_beam_center_load",
        "frame_static.simple_beam_udl",
        "frame_static.cantilever_tip_load",
        "frame_static.bending_stress",
        "mass_props.static_wheel_loads",
    ]

    def known_answers(self) -> list[Record]:
        return frame_known_answers()

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params (all optional):
        loading (empty / driver / two / two_cargo, default two_cargo): who and what is on board
        g_factor (unit 1 or g, default 1): vertical load factor on every weight
        step_height (length): add the forces of the step_supports climbing a step
        step_supports (string, e.g. "FL,FR"; default: the front-most supports)
        Run params: upstream_analyses {load case id: analysis}; when one of them
        carries ground forces (reaction and grip vectors) at every support, those
        forces load the frame instead of being worked out here, and an
        "inertia" vector at "cg" in it is checked against what the forces leave
        unbalanced (inertia relief takes that part).
        """
        design = on_board(design, load_case)
        g = g_factor(load_case)
        up = upstream_ground_forces(params.get("upstream_analyses"), design)
        step = param_value(load_case, "step_height", "mm")
        sup = None
        if up is None and step is not None:
            sup = param_list(load_case, "step_supports") or front_supports(design)
        res = analyze(design, g_factor=g, step_mm=None if up else step, step_supports=sup,
                      ground_forces=up[1] if up else None, inertia_N=up[2] if up else None)
        return frame_outputs(res)


def upstream_ground_forces(upstream: dict | None, design: Record):
    """(load case id, {support id: [(kind, point, force), ...]}, inertia force or None) from the first
    upstream analysis that has a reaction vector at every support of the design."""
    ids = [s["id"] for s in design.get("supports", [])]
    for lc_id, ana in (upstream or {}).items():
        if not isinstance(ana, dict) or ana.get("status") != "ok":
            continue
        got: dict[str, list] = {}
        inertia = None
        for v in ana.get("vectors", []):
            if v.get("kind") in ("reaction", "grip") and v.get("at") in ids:
                got.setdefault(v["at"], []).append((v["kind"], v["origin_mm"], v["vector_N"]))
            elif v.get("kind") == "inertia" and v.get("at") == "cg":
                inertia = np.asarray(v["vector_N"], float)
        if set(got) == set(ids) and all(any(k == "reaction" for k, _, _ in vs) for vs in got.values()):
            return lc_id, got, inertia
    return None


def g_factor(load_case: Record) -> float:
    """Vertical load factor: a plain number (unit "1") or an acceleration (e.g. 2 g)."""
    p = load_case.get("params", {}).get("g_factor")
    if p is None:
        return 1.0
    if isinstance(p, dict) and units.UNIT_TABLE.get(p.get("unit"), (0, 0, None))[2] == units.dimension("g"):
        return param_value(load_case, "g_factor", "g")
    return param_value(load_case, "g_factor", "1")


def front_supports(design: Record) -> list[str]:
    sup = design.get("supports", [])
    xmax = max(s["position_mm"][0] for s in sup)
    return [s["id"] for s in sup if abs(s["position_mm"][0] - xmax) < 1e-6]

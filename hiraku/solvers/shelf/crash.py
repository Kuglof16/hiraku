"""crash (C1): frontal impact into a rigid wall.

A 1-D mass and spring gives the deceleration (bumper rubber, then the crush
zone once the bumper bottoms out). At the largest deceleration the wall force
is put on the front frame nodes and inertia relief loads every mass with its
d'Alembert force, so K1 shows the frame stress and what the battery mounts
carry.

Design variables: bumper_stiffness (N/mm), bumper_stroke (mm),
crush_stiffness (N/mm).
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc import units
from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import DesignError, mass_summary, support_loads
from ..k1_frame import analyze
from ..k1_frame.impact import Front, impact
from . import fixtures
from .base import BuiltinTool, LoadCaseError, Record, check_record, on_board, param_value, result
from .frame_static import frame_outputs

FRONT_VARS = {"bumper_stiffness": "N/mm", "bumper_stroke": "mm", "crush_stiffness": "N/mm"}


def front_from_design(design: Record) -> Front:
    vals, missing = {}, []
    for k, u in FRONT_VARS.items():
        q = design.get("variables", {}).get(k)
        if q is None:
            missing.append(f"{k} [{u}]")
            continue
        try:
            vals[k] = units.to_unit(q, u, k)
        except ValueError as e:
            raise DesignError(f"design variable {e}") from None
    if missing:
        raise DesignError("the design has no crash data for: " + ", ".join(missing) + "; add them to design.variables")
    return Front(vals["bumper_stiffness"] * 1000.0, vals["bumper_stroke"] / 1000.0, vals["crush_stiffness"] * 1000.0)


def wall_loads(design: Record, force_N: float) -> list:
    """The wall force (backward) shared by the front-most frame nodes."""
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    used = {m[k] for m in design["members"] for k in ("node_a", "node_b")}
    xmax = max(nodes[n][0] for n in used)
    front = [n for n in sorted(used) if abs(nodes[n][0] - xmax) < 1e-6]
    return [(n, nodes[n], np.array([-force_N / len(front), 0.0, 0.0])) for n in front]


def crash_frame(design: Record, a_wall: float) -> dict:
    """K1 at a deceleration a_wall (m/s^2): wheels carry the weight, the wall stops the mass."""
    summ = mass_summary(design)
    m = summ["mass_kg"]
    rest = support_loads(design, summary=summ)
    ground = {sid: [("reaction", sl["point_mm"], sl["force_N"])] for sid, sl in rest["supports"].items()}
    F_wall = m * a_wall
    return analyze(design, ground_forces=ground, node_loads=wall_loads(design, F_wall),
                   inertia_N=np.array([F_wall, 0.0, 0.0]))


class Crash(BuiltinTool):
    name = "crash"
    title = "壁への正面衝突 (1 次元の質量とばね → フレーム)"
    domain = "crash"
    base = "K1"
    tests = ["C1"]
    formulas = ["crash.linear_spring", "crash.mean_deceleration"]

    def known_answers(self) -> list[Record]:
        out = []
        m, v, k = 500.0, 40.0 / 3.6, 200e3
        r = impact(m, v, Front(k, 10.0, 0.0))
        src = "undamped mass-spring impact: a = v sqrt(k/m), x = v sqrt(m/k), t = (pi/2) sqrt(m/k); 500 kg, 200 kN/m, 40 km/h"
        out.append(check_record("500 kg at 40 km/h on a 200 kN/m spring", "peak_deceleration",
                                v * math.sqrt(k / m), r["a_max"], "m/s^2", 1e-6, src))
        out.append(check_record("500 kg at 40 km/h on a 200 kN/m spring", "max_crush",
                                v * math.sqrt(m / k), r["x_max"], "m", 1e-6, src))
        out.append(check_record("500 kg at 40 km/h on a 200 kN/m spring", "stop_time",
                                math.pi / 2 * math.sqrt(m / k), r["t_stop"], "s", 1e-6, src))
        r = impact(600.0, 10.0, Front(100e3, 0.08, 600e3))
        out.append(check_record("bumper then crush zone, 600 kg at 10 m/s", "energy_absorbed",
                                r["E_kinetic"], r["E_absorbed"], "J", 1e-6,
                                "work-energy: the springs take m v^2 / 2 at the stop"))
        lad = fixtures.ladder_design()
        res = crash_frame(lad, 20.0 * STANDARD_GRAVITY)
        out.append(check_record("symmetric ladder stopped by a wall at 20 g", "hold_reaction",
                                0.0, res["residual_ratio"], "1", 1e-6,
                                "inertia relief: wall force and d'Alembert loads balance, the holding restraints carry nothing"))
        out.append(check_record("symmetric ladder stopped by a wall at 20 g", "deceleration",
                                20.0 * STANDARD_GRAVITY, -float(res["inertia_accel"][0]), "m/s^2", 1e-9,
                                "rigid-body acceleration from the wall force: a = F / m"))
        return out

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: speed (km/h, required), loading (default two_cargo)."""
        design = on_board(design, load_case)
        v = param_value(load_case, "speed", "m/s")
        if v is None or v <= 0:
            raise LoadCaseError("the crash needs the load case parameter speed (positive)")
        front = front_from_design(design)
        summ = mass_summary(design)
        m = summ["mass_kg"]
        imp = impact(m, v, front)
        res = crash_frame(design, imp["a_max"])
        out = frame_outputs(res)
        crash_results = [
            result("peak_deceleration", imp["a_max"] / STANDARD_GRAVITY, "g"),
            result("peak_force", imp["F_max"] / 1000.0, "kN"),
            result("max_crush", imp["x_max"] * 1000.0, "mm"),
            result("crash_energy", imp["E_kinetic"], "J"),
            result("stop_time", imp["t_stop"], "s"),
            result("mean_deceleration", v / imp["t_stop"] / STANDARD_GRAVITY, "g"),
        ]
        margin = occupant_space(design)
        if margin is not None:
            crash_results.append(result("occupant_space_margin", margin - imp["x_max"] * 1000.0, "mm"))
        for p in design.get("parts", []):
            if p["kind"] == "battery":
                crash_results.append(result("battery_inertia_force", float(p["mass_kg"]) * imp["a_max"], "N", p["id"]))
        out["results"] = crash_results + out["results"]
        for n, p, f in wall_loads(design, m * imp["a_max"]):
            out["vectors"].append({"kind": "load", "at": n, "origin_mm": [float(x) for x in p], "vector_N": [float(x) for x in f]})
        out["series"] = [
            {"channel": "deceleration", "unit": "g", "t_s": [float(x) for x in imp["t"]],
             "values": [float(x) for x in imp["a"] / STANDARD_GRAVITY]},
            {"channel": "crush", "unit": "mm", "t_s": [float(x) for x in imp["t"]], "values": [float(x) for x in imp["x"] * 1000.0]},
            {"channel": "wall_force", "unit": "kN", "t_s": [float(x) for x in imp["t"]], "values": [float(x) for x in imp["F"] / 1000.0]},
        ]
        e_b = front.energy(min(imp["x_max"], front.stroke)) if front.k_bumper > 0 else 0.0
        out["effects"] = [
            {"name": "energy taken by the bumper", "value": e_b, "unit": "J", "share": e_b / imp["E_kinetic"]},
            {"name": "energy taken by the crush zone", "value": imp["E_absorbed"] - e_b, "unit": "J",
             "share": (imp["E_absorbed"] - e_b) / imp["E_kinetic"]},
        ] + out["effects"]
        return out


def occupant_space(design: Record) -> float | None:
    """Room in front of the occupants: front-most point of the vehicle minus the occupants' front face (mm)."""
    occ = [p for p in design.get("parts", []) if p["kind"] == "occupant"]
    if not occ:
        return None
    xs = [n["xyz_mm"][0] for n in design["nodes"]]
    xs += [p["box"]["center_mm"][0] + p["box"]["size_mm"][0] / 2.0 for p in design.get("parts", [])]
    occ_front = max(p["box"]["center_mm"][0] + p["box"]["size_mm"][0] / 2.0 for p in occ)
    return max(xs) - occ_front

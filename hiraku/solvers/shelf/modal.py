"""modal (N1): natural frequencies and mode shapes of the frame on its springs,
next to the frequencies the wheels and the motor shake it with."""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc import units
from ..k0_handcalc.vehicle import DesignError
from ..k1_frame import build
from ..k1_frame.modal import MASS_CASE, modes
from . import fixtures
from .base import BuiltinTool, LoadCaseError, Record, check_record, on_board, param_value, result

FLEX_LIMIT = 0.3  # a mode whose shape is more than this far from a rigid motion bends the frame
FIELD_MODES = 6


def _beam_modes(length, section, material, supports, tip_mass_kg=0.0, n=3):
    fm = build(fixtures.beam_design(length, section, material))
    fm.model.add_member_self_weight("FY", -1.0, MASS_CASE)
    if tip_mass_kg:
        fm.model.add_node_load("B", "FY", -tip_mass_kg / 1000.0, MASS_CASE)
    for node, flags in supports.items():
        fm.model.def_support(node, *flags)
    fm.model.add_load_combo(MASS_CASE, {MASS_CASE: 1.0})
    fm.model.analyze_modal(num_modes=n, mass_combo_name=MASS_CASE, mass_direction="Y", gravity=1.0)
    return sorted(float(f) for f in fm.model.frequencies)


class Modal(BuiltinTool):
    name = "modal"
    title = "固有振動数とモードの形 (3D の梁)"
    domain = "nvh"
    base = "K1"
    tests = ["N1"]
    formulas = ["modal.spring_mass", "modal.quarter_car", "modal.simple_beam_first_mode", "modal.rotation_excitation"]

    def known_answers(self) -> list[Record]:
        out = []
        mat = dict(fixtures.STEEL, E_MPa=69000.0, density_kg_m3=2700.0, nu=0.33)
        sec = {"shape": "custom", "dims_mm": {"cy": 10.0, "cz": 10.0}, "A_mm2": 400.0, "Iy_mm4": 1e5, "Iz_mm4": 1e5, "J_mm4": 2e5}
        f = _beam_modes(1000.0, sec, mat, {"A": (True, True, True, True, False, False), "B": (False, True, True, False, False, False)})
        out.append(check_record("simply supported beam, A 400 mm^2, I 1e5 mm^4, E 69 GPa, 2700 kg/m^3, 1 m, 4 elements",
                                "first_frequency", math.pi / 2 / 1000.0**2 * math.sqrt(69000.0 * 1e5 / (2.7e-9 * 400.0)),
                                f[0], "Hz", 1e-3, "f1 = (pi / 2 L^2) sqrt(E I / rho A) = 125.555 Hz"))
        f = _beam_modes(1000.0, sec, dict(mat, density_kg_m3=1e-6), {"A": (True,) * 6}, tip_mass_kg=10.0)
        out.append(check_record("cantilever without mass, 10 kg at the tip, same beam", "first_frequency",
                                math.sqrt(3 * 69000.0 * 1e5 / (0.010 * 1000.0**3)) / (2 * math.pi), f[0], "Hz", 1e-6,
                                "f = (1/2 pi) sqrt(3 E I / (M L^3)) = 7.2411 Hz"))
        lad = fixtures.ladder_design(payload_kg=400.0)
        lad["materials"]["st"] = dict(lad["materials"]["st"], E_MPa=205000.0 * 1e3, density_kg_m3=1e-3)
        for s in lad["supports"]:
            s["suspension"] = {"type": "spring", "stiffness_N_per_mm": 20.0}
        r = modes(lad, 4)  # the stiff frame's own mass (1e-3 kg/m^3) is negligible next to the 400 kg
        bounce = min(r["frequencies"], key=lambda fi: abs(fi - math.sqrt(4 * 20.0e3 / 400.0) / (2 * math.pi)))
        out.append(check_record("400 kg on a nearly rigid ladder, four 20 N/mm springs", "bounce_frequency",
                                math.sqrt(4 * 20.0e3 / 400.0) / (2 * math.pi), float(bounce), "Hz", 1e-3,
                                "rigid body bounce: f = (1/2 pi) sqrt(4 k / m)"))
        return out

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: speed (km/h; default the design variable max_speed) for the
        rotation excitation, n_modes (1, default 10), loading (default two_cargo)."""
        design = on_board(design, load_case)
        n = int(param_value(load_case, "n_modes", "1", 10.0))
        if not 1 <= n <= 40:
            raise LoadCaseError("n_modes must be between 1 and 40")
        v = param_value(load_case, "speed", "km/h")
        if v is None:
            q = design.get("variables", {}).get("max_speed")
            if q is None:
                raise LoadCaseError("give the load case parameter speed (km/h) or the design variable max_speed")
            v = units.to_unit(q, "km/h", "max_speed")
        r = modes(design, n)
        f, flex = r["frequencies"], r["flexibility"]
        results = [result(f"mode_{i + 1}", float(fi), "Hz") for i, fi in enumerate(f)]
        body = [float(fi) for fi, x in zip(f, flex) if x < FLEX_LIMIT]
        frame = [float(fi) for fi, x in zip(f, flex) if x >= FLEX_LIMIT]
        if body:
            results.append(result("first_body_mode", body[0], "Hz"))
        if not frame:
            raise DesignError(f"no frame bending mode among the first {n} modes; ask for more modes (n_modes)")
        results.append(result("first_frame_mode", frame[0], "Hz"))
        driven = [s for s in design["supports"] if s["actuation"]["drive"]] or design["supports"]
        radius = float(np.mean([s["radius_mm"] for s in driven]))
        f_wheel = (v / 3.6) / (2 * math.pi * radius / 1000.0)
        results.append(result("wheel_rotation_frequency", f_wheel, "Hz"))
        exc = {"wheel": f_wheel}
        gear = design.get("variables", {}).get("gear_ratio")
        if gear is not None:
            exc["motor"] = f_wheel * units.to_unit(gear, "1", "gear_ratio")
            results.append(result("motor_rotation_frequency", exc["motor"], "Hz"))
        gaps = {k: min(abs(fr - fe) / fe for fr in frame) * 100.0 for k, fe in exc.items() if fe > 0}
        nearest = min(gaps, key=gaps.get)
        results.append(result("excitation_gap", gaps[nearest], "%", nearest))
        fields = []
        for i in range(min(FIELD_MODES, len(f))):
            kind = "frame" if flex[i] >= FLEX_LIMIT else "body on springs"
            fields.append({"kind": "mode_shape", "label": f"mode {i + 1} ({kind})", "unit": "1",
                           "frequency_Hz": float(f[i]),
                           "per_node": {nid: [float(x) for x in u] for nid, u in r["shapes"][i].items()}})
        effects = [{"name": f"mode {i + 1}: share of the shape that bends the frame", "value": float(flex[i]), "unit": "1"}
                   for i in range(min(FIELD_MODES, len(f)))]
        return {"results": results, "fields": fields, "effects": effects}

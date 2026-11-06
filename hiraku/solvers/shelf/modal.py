"""modal (N1): natural frequencies and mode shapes of the frame on its springs,
next to the frequencies the wheels and the motor shake it with, and the first
bending and torsion frequencies of the driveshafts (k1_frame.shaft)."""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc import units
from ..k0_handcalc.vehicle import DesignError
from ..k1_frame import build
from ..k1_frame.modal import MASS_CASE, modes, solve_modal
from ..k1_frame.shaft import driveshaft_modes, driveshaft_shapes
from . import fixtures
from .base import LOADING, BuiltinTool, LoadCaseError, Param, Predicts, Record, check_record, on_board, param_value, result

FLEX_LIMIT = 0.3  # a mode whose shape is more than this far from a rigid motion bends the frame
FIELD_MODES = 10  # modes with a mode_shape field (the first FIELD_MODES of n_modes)


def _beam_modes(length, section, material, supports, tip_mass_kg=0.0, n=3):
    fm = build(fixtures.beam_design(length, section, material))
    fm.model.add_member_self_weight("FY", -1.0, MASS_CASE)
    if tip_mass_kg:
        fm.model.add_node_load("B", "FY", -tip_mass_kg / 1000.0, MASS_CASE)
    for node, flags in supports.items():
        fm.model.def_support(node, *flags)
    fm.model.add_load_combo(MASS_CASE, {MASS_CASE: 1.0})
    solve_modal(fm.model, n)
    return sorted(float(f) for f in fm.model.frequencies)


class Modal(BuiltinTool):
    name = "modal"
    title = "固有振動数とモードの形 (3D の梁)"
    domain = "nvh"
    base = "K1"
    tests = ["N1"]
    formulas = ["modal.spring_mass", "modal.quarter_car", "modal.ladder_twist_mode", "modal.simple_beam_first_mode",
                "modal.rotation_excitation", "modal.tube_shaft_bending", "modal.two_inertia_torsion"]
    load_case_params = (
        Param("speed", "km/h", "driving speed for the wheel and motor rotation frequencies "
              "(without it: the design variable max_speed)"),
        Param("n_modes", "1", "number of modes to compute (1 to 40)", default=10.0),
        LOADING,
    )
    predicts = (
        Predicts("first_body_mode", "modal.quarter_car", "f", "the body bouncing on suspension and tyre springs"),
        Predicts("first_body_mode", "modal.spring_mass", "f", "k = all suspension springs together, m = the sprung mass"),
        Predicts("first_frame_mode", "modal.ladder_twist_mode", "f",
                 "a ladder frame's twist; inputs from hiraku.solvers.k0_handcalc.frame_inputs.ladder_twist_inputs("
                 "design, loading=...)"),
        Predicts("first_frame_mode", "modal.simple_beam_first_mode", "f1",
                 "one rail as a simply supported beam on the axles, without the masses it carries"),
        Predicts("wheel_rotation_frequency", "modal.rotation_excitation", "f_wheel"),
        Predicts("motor_rotation_frequency", "modal.rotation_excitation", "f_motor", "i = the design variable gear_ratio"),
        Predicts("driveshaft_bending_mode", "modal.tube_shaft_bending", "f1",
                 "inputs from hiraku.solvers.k1_frame.shaft.shaft_inputs(design) (the lowest of the shafts)"),
        Predicts("driveshaft_torsion_mode", "modal.two_inertia_torsion", "f",
                 "inputs from hiraku.solvers.k1_frame.shaft.shaft_inputs(design): J_1 = J_in, J_2 = J_out"),
    )

    def known_answers(self) -> list[Record]:
        out = []
        # round values for aluminium (test values: the closed form uses the same numbers; no source)
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
        """Load case params: load_case_params."""
        design = on_board(design, load_case)
        n = int(param_value(load_case, "n_modes", "1"))
        if not 1 <= n <= 40:
            raise LoadCaseError("n_modes must be between 1 and 40")
        v = param_value(load_case, "speed", "km/h")
        if v is None:
            q = design.get("variables", {}).get("max_speed")
            if q is None:
                raise LoadCaseError("give the load case parameter speed (km/h) or the design variable max_speed")
            v = units.to_unit(q, "km/h", "max_speed")
        r = modes(design, n)
        f, flex, lflex = r["frequencies"], r["flexibility"], r["ladder_flexibility"]
        n_fields = min(FIELD_MODES, len(f))

        def mode_row(quantity: str, i: int) -> Record:
            """The row of mode i's frequency, with field_index when mode i has a mode_shape field (fields[i])."""
            row = result(quantity, float(f[i]), "Hz")
            if i < n_fields:
                row["field_index"] = i
            return row

        results = [mode_row(f"mode_{i + 1}", i) for i in range(len(f))]
        body = [i for i, x in enumerate(flex) if x < FLEX_LIMIT]
        ladder = [i for i, (x, lx) in enumerate(zip(flex, lflex)) if x >= FLEX_LIMIT and lx >= FLEX_LIMIT]
        other = [i for i, (x, lx) in enumerate(zip(flex, lflex)) if x >= FLEX_LIMIT and lx < FLEX_LIMIT]
        frame = [float(fi) for fi, x in zip(f, flex) if x >= FLEX_LIMIT]  # everything that bends (the excitation gap)
        if body:
            results.append(mode_row("first_body_mode", body[0]))
        if not ladder:
            raise DesignError(f"no frame bending mode among the first {n} modes; ask for more modes (n_modes)")
        results.append(mode_row("first_frame_mode", ladder[0]))
        if other:  # members of another part (the roll bar) bend while the ladder stays as it is
            results.append(mode_row("first_rollbar_mode", other[0]))
        driven = [s for s in design["supports"] if s["actuation"]["drive"]] or design["supports"]
        radius = float(np.mean([s["radius_mm"] for s in driven]))
        f_wheel = (v / 3.6) / (2 * math.pi * radius / 1000.0)
        results.append(result("wheel_rotation_frequency", f_wheel, "Hz"))
        exc = {"wheel": f_wheel}
        gear = design.get("variables", {}).get("gear_ratio")
        if gear is not None:
            exc["motor"] = f_wheel * units.to_unit(gear, "1", "gear_ratio")
            results.append(result("motor_rotation_frequency", exc["motor"], "Hz"))
        shafts = driveshaft_modes(design)
        shaft_rows = []
        for key, quantity in (("bending", "driveshaft_bending_mode"), ("torsion", "driveshaft_torsion_mode")):
            if shafts[key]:
                pid = min(shafts[key], key=shafts[key].get)
                shaft_rows.append((key, pid, result(quantity, shafts[key][pid], "Hz", pid)))
                results.append(shaft_rows[-1][2])
        gaps = {k: min(abs(fr - fe) / fe for fr in frame) * 100.0 for k, fe in exc.items() if fe > 0}
        nearest = min(gaps, key=gaps.get)
        results.append(result("excitation_gap", gaps[nearest], "%", nearest))
        fields = []
        for i in range(n_fields):
            kind = ("body on springs" if flex[i] < FLEX_LIMIT else "frame" if lflex[i] >= FLEX_LIMIT else "roll bar")
            fields.append({"kind": "mode_shape", "label": f"mode {i + 1} ({kind})", "unit": "1",
                           "frequency_Hz": float(f[i]),
                           "per_node": {nid: [float(x) for x in u] for nid, u in r["shapes"][i].items()}})
        effects = [{"name": f"mode {i + 1}: share of the shape that bends the frame", "value": float(flex[i]), "unit": "1"}
                   for i in range(n_fields)]
        if shaft_rows:
            shapes = driveshaft_shapes(design)
            for key, pid, row in shaft_rows:
                row["field_index"] = len(fields)
                fields.append(shaft_field(pid, key, row["value"], shapes[pid]))
        series, note = response_series(design, r, f, flex)
        effects.append({"name": note})
        return {"results": results, "fields": fields, "effects": effects, "series": series}


def shaft_field(pid: str, key: str, frequency: float, shape: Record) -> Record:
    """The mode_shape field of a driveshaft's first bending or torsion mode (k1_frame.shaft.driveshaft_shapes) on
    points <pid>.p00 to .pNN evenly along the shaft from its inner joint to its outer one (the shaft model's
    element nodes; the shaft has no design nodes). Bending: a vec3 across the shaft in the vertical plane through
    its axis (the tube is round; that plane is a choice); torsion: the twist (a number)."""
    inner, outer = shape["inner_mm"], shape["outer_mm"]
    u = shape[key]
    pts = len(u) - 1
    a = ", ".join(f"{x:.1f}" for x in inner)
    b = ", ".join(f"{x:.1f}" for x in outer)
    where = f"points {pid}.p00 to {pid}.p{pts:02d} evenly from the inner joint ({a}) mm to the outer joint ({b}) mm"
    axis = (outer - inner) / np.linalg.norm(outer - inner)
    side = np.array([0.0, 0.0, 1.0]) - axis[2] * axis
    side = side / np.linalg.norm(side)
    if key == "bending":
        per = {f"{pid}.p{k:02d}": [float(x) + 0.0 for x in uk * side] for k, uk in enumerate(u)}
        label = (f"{pid}: driveshaft first bending mode (pinned at both joints, the shaft's beam model), sideways "
                 f"displacement in the vertical plane through the axis, at {where}")
    else:
        # a mode_shape's per_node is a displacement (vec3): the twist is shown as the displacement of the tube's
        # top line, twist x radius along the tangent there (axis x up), over its largest
        tangent = np.cross(axis, side)
        per = {f"{pid}.p{k:02d}": [float(x) + 0.0 for x in uk * tangent] for k, uk in enumerate(u)}
        label = (f"{pid}: driveshaft first torsion mode (motor and reducer inertia at the inner joint, the wheel at "
                 f"the outer, the shaft's torsion model), twist shown as the displacement of the tube's top line "
                 f"along its tangent, at {where}")
    return {"kind": "mode_shape", "label": label, "unit": "1", "frequency_Hz": float(frequency), "per_node": per}


FRF_POINTS = 400


def body_damping(design: Record) -> float:
    """Damping ratio of the body on its suspension: all dampers over twice the root of all springs times the
    sprung mass."""
    from ..k0_handcalc.vehicle import mass_summary

    summ = mass_summary(design)
    sus = [s.get("suspension", {}) for s in design["supports"]]
    k = sum(float(s.get("stiffness_N_per_mm") or 0.0) for s in sus) * 1e3
    c = sum(float(s.get("damping_Ns_per_mm") or 0.0) for s in sus) * 1e3
    m = summ["mass_kg"] - summ["unsprung_mass_kg"]
    return c / (2.0 * math.sqrt(k * m)) if k > 0 and m > 0 else 0.0


def response_series(design: Record, r: Record, f, flex) -> tuple[list[Record], str]:
    """The frequency response of the driver's seat (the first occupant's carried_by nodes, or the frame nodes)
    to a vertical force at the first front wheel, as a series against frequency."""
    from ..k1_frame.modal import FRAME_DAMPING, frequency_response

    fm = r["model"]
    sup = max(design["supports"], key=lambda s: s["position_mm"][0])
    seat = next((p.get("carried_by") for p in design.get("parts", []) if p["kind"] == "occupant" and p.get("carried_by")),
                None) or fm.frame_nodes
    zb = body_damping(design)
    zetas = [FRAME_DAMPING if x >= FLEX_LIMIT else zb for x in flex]
    freqs = np.linspace(0.0, 1.1 * float(max(f)), FRF_POINTS)
    amp = frequency_response(fm, fm.wheel_nodes[sup["id"]], list(seat), zetas, freqs)
    note = (f"frequency response: vertical motion at {', '.join(seat)} per vertical force at wheel {sup['id']}, over its "
            f"value at 0 Hz; damping ratio {zb:.2f} for the body on its springs (from the dampers), {FRAME_DAMPING:g} for "
            f"the frame's modes (assumed, not checked)")
    return [{"channel": f"frequency_response.{sup['id']}", "unit": "1", "values": [round(float(a), 5) for a in amp],
             "x": {"quantity": "frequency", "unit": "Hz", "values": [round(float(x), 4) for x in freqs]}}], note

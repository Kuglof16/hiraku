"""step_climb: can the vehicle climb a step, what force does the wheel take, and
what does that do to the frame.

MVP form: the wheel forces come from K0 rigid-body statics at the moment the
stepping wheels lift off (pushed by the driven wheels on the ground), and
those forces are put on the K1 frame. When K2 (3D vehicle physics) is ready,
the wheel forces will come from driving over the step instead.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import mass_summary, resultant, step_geometry, support_loads
from ..k1_frame import analyze
from . import fixtures, scene
from .base import (G_FACTOR, LOADING, BuiltinTool, LoadCaseError, Predicts, Record, check_record, on_board, param_list,
                   param_value, result)
from .frame_static import (JOINT_INPUTS, STEP_BENDING, STEP_HEIGHT, STEP_SUPPORTS, frame_known_answers, frame_outputs,
                           front_supports, g_factor)


class StepClimb(BuiltinTool):
    name = "step_climb"
    title = "段差越え (輪の力の釣り合い → フレーム)"
    domain = "powertrain"
    base = "K1"
    tests = ["P1", "R1"]
    formulas = [
        "step_climb.wheel_force_rigid",
        "step_climb.pushed_wheel_load",
        "step_climb.self_climb",
        "mass_props.front_wheel_load",
        "mass_props.static_wheel_loads",
        "frame_static.rail_joint_stress",
        "frame_static.rail_step_bending",
        "frame_static.rail_bending",
        "frame_static.simple_beam_point_load",
        "frame_static.cantilever_tip_load",
        "frame_static.bending_stress",
    ]
    load_case_params = (replace(STEP_HEIGHT, required=True, meaning="height of the step the step_supports climb"),
                        STEP_SUPPORTS, LOADING, G_FACTOR)
    predicts = (
        Predicts("front_wheel_load", "step_climb.pushed_wheel_load", "W",
                 "the stepping wheel as it lifts off: the push couple moves load to the rear "
                 "(results are named step_* instead of front_* when the step_supports are not the front ones)"),
        Predicts("front_wheel_force", "step_climb.pushed_wheel_load", "F"),
        Predicts("front_wheel_force", "step_climb.wheel_force_rigid", "F",
                 "with W = the climbing load (pushed_wheel_load W); the static load overpredicts it"),
        Predicts("front_wheel_load_static", "mass_props.front_wheel_load", "W"),
        Predicts("corner_distance", "step_climb.wheel_force_rigid", "d"),
        Predicts("self_climb_torque", "step_climb.self_climb", "tau", "with W = front_wheel_load"),
        Predicts("max_stress", "frame_static.rail_joint_stress", "sigma", JOINT_INPUTS),
        Predicts("max_stress", "frame_static.rail_step_bending", "sigma", STEP_BENDING),
    )

    def known_answers(self) -> list[Record]:
        out = []
        src = "rigid wheel on a step corner: d = sqrt(2 r h - h^2), F/W = d / (r - h)"
        src_ja = "変形しない車輪が段の角に当たる: d = √(2 r h - h²)、F/W = d / (r - h)"
        for h, expected in ((100.0, 1.118), (150.0, 1.732), (250.0, 5.92)):
            out.append(check_record(f"wheel r = 300 mm on a {h:g} mm step", "force_ratio",
                                    expected, step_geometry(300.0, h)[1], "1", 1e-3, src,
                                    case_ja=f"半径 300 mm の車輪が高さ {h:g} mm の段に当たる", source_ja=src_ja))

        lad = fixtures.ladder_design()
        summ = mass_summary(lad)
        wt = summ["mass_kg"] * STANDARD_GRAVITY
        a = -float(summ["cg_mm"][0])
        L, r, h = 2000.0, 300.0, 100.0
        k = step_geometry(r, h)[1]
        front_expected = wt * (L - a) / L / (1.0 + k * r / L)
        sl = support_loads(lad, 1.0, h, ["FL", "FR"], summary=summ)
        got = sum(sl["supports"][s]["force_N"][2] for s in ("FL", "FR"))
        out.append(check_record("symmetric ladder, both front wheels on a 100 mm step, pushed by the rear",
                                "front_axle_load", front_expected, got, "N", 1e-9,
                                "moment balance with the push at axle height and traction at the ground: "
                                "W_f = m g (L - a) / L / (1 + k r / L)",
                                case_ja=f"左右対称のはしご形のフレーム、前の左右の車輪とも高さ {h:g} mm の段に当たり、後ろの車輪が押す",
                                source_ja="押す力は車軸の高さ、駆動の力は地面にかかるとして、力のモーメントのつり合いから: "
                                          "W_f = m g (L - a) / L / (1 + k r / L)"))
        forces = [(v["point_mm"], v["force_N"]) for v in sl["supports"].values()]
        forces += [(it.cg_mm, np.array([0.0, 0.0, -it.mass_kg * STANDARD_GRAVITY])) for it in summ["items"]]
        sl1 = support_loads(lad, 1.0, h, ["FL"], summary=summ)
        forces1 = [(v["point_mm"], v["force_N"]) for v in sl1["supports"].values()]
        forces1 += forces[len(sl["supports"]):]
        F, M = resultant(forces1)
        out.append(check_record("symmetric ladder, front left wheel alone on a 100 mm step", "force_imbalance",
                                0.0, float(np.linalg.norm(F) + np.linalg.norm(M) / L) / wt, "1", 1e-9,
                                "rigid-body equilibrium: forces and moments of ground and weight sum to zero",
                                case_ja=f"左右対称のはしご形のフレーム、左前の車輪だけが高さ {h:g} mm の段に当たる",
                                source_ja="剛体のつり合い: 地面の力と重さの、力とモーメントを足すと 0"))
        res = analyze(lad, step_mm=h, step_supports=["FL"])
        out.append(check_record("symmetric ladder, front left wheel alone on a 100 mm step", "hold_reaction",
                                0.0, res["residual_ratio"], "1", 1e-6,
                                "self-balanced loads on the K1 frame: the six holding restraints carry nothing",
                                case_ja=f"左右対称のはしご形のフレーム、左前の車輪だけが高さ {h:g} mm の段に当たる",
                                source_ja="K1 のフレームにかけた荷重はそれだけでつり合う: 6 つの押さえ (拘束) は何も受けない"))
        return out + frame_known_answers()

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: load_case_params.

        Results for the stepping wheel that takes the largest push (named
        front_* when the stepping wheels are the front ones, step_* otherwise):
        *_wheel_load is its vertical load at the moment it lifts off (the push
        at axle height and the traction at the ground move load rearward),
        *_wheel_load_static the same wheel at rest, *_wheel_force the push.
        """
        design = on_board(design, load_case)
        h = param_value(load_case, "step_height", "mm")
        if h is None:
            raise LoadCaseError("step_climb needs the load case parameter step_height (a length)")
        if h <= 0:
            raise LoadCaseError(f"step_height must be positive, got {h:g} mm")
        front = front_supports(design)
        sup_ids = param_list(load_case, "step_supports") or front
        gf = g_factor(load_case)
        res = analyze(design, g_factor=gf, step_mm=h, step_supports=sup_ids)
        rest = support_loads(design, gf, summary=res["mass"])
        out = frame_outputs(res)

        by_id = {s["id"]: s for s in design["supports"]}
        st = res["statics"]
        lead = max(sup_ids, key=lambda s: abs(st["supports"][s]["force_N"][0]))
        f = st["supports"][lead]["force_N"]
        d, k = step_geometry(by_id[lead]["radius_mm"], h)
        r = float(by_id[lead]["radius_mm"])
        tag = "front" if set(sup_ids) <= set(front) else "step"
        climb = [
            result(f"{tag}_wheel_force", abs(f[0]), "N", lead),
            result(f"{tag}_wheel_load", f[2], "N", lead),
            result(f"{tag}_wheel_load_static", rest["supports"][lead]["force_N"][2], "N", lead),
            result("force_ratio", k, "1", lead),
            result("corner_distance", d, "mm", lead),
            result("self_climb_torque", f[2] * d / 1000.0, "N*m", lead),
            result("step_margin", r - h, "mm", lead),
            result("push_traction", st["traction_N"], "N"),
            result("required_mu_push", st["required_mu_push"], "1"),
        ]
        out["results"] = climb + out["results"]
        out["scene"] = scene.steps(design, sup_ids, h)
        return out

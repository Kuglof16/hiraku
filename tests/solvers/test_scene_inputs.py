"""scene_inputs (the inputs of the H1, H3 and P3 formulas read off a design) and the K0 formula
launch.peak_tractive_force: the builders' readers give the same values as before they called them."""

import json
import math
import unittest
from pathlib import Path

from hiraku import solvers
from hiraku.catalog.builders import analysis as ab
from hiraku.catalog.builders import k2_tests
from hiraku.contract.loading import on_board
from hiraku.solvers.k0_handcalc import scene_inputs
from hiraku.solvers.k0_handcalc.vehicle import DesignError

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
REG = solvers.formula_registry()
FORMULAS = {f["formula_id"]: f for f in REG.list_formulas()}
LAUNCH_LC = {"load_case_id": "P3-accel30", "test_id": "P3", "domain": "powertrain",
             "params": {"loading": "two_cargo", "target_speed": {"value": 30.0, "unit": "km/h"},
                        "grade": {"value": 0.0, "unit": "%"}}}


# ---- the builders' code before it called scene_inputs (kept here as the reference)
def old_h1(design):
    from hiraku.solvers.k0_handcalc.library_geometry import _free_steer
    from hiraku.solvers.k4_geometry import steer_room

    rooms = steer_room(design)
    sid = min(rooms, key=lambda k: _free_steer(rooms[k])["theta"])
    r = rooms[sid]
    return {"steer_w": r["w"], "steer_l": r["l"], "steer_c": r["c"]}


def old_h3(design, loading):
    from hiraku.solvers.k4_geometry import AIST, knee_space

    space = knee_space(on_board(design, loading))
    return {"knee_length": min(length for length, _ in space.values()), "buttock_knee": AIST["buttock_knee_m95"]}


def old_launch(design, load_case):
    from hiraku.solvers.k2_vehicle.shelf import LaunchTool

    veh, _ = k2_tests._vehicle(design, load_case)
    tool = LaunchTool()
    tool._vars = design.get("variables") or {}
    lp = load_case.get("params", {})
    motor = tool.motor(lp)
    aero = tool.aero(veh, lp)
    driven = [c for c in veh.corners if c.drive]
    r = sum(c.radius for c in driven) / len(driven)
    return {"F_max": motor.torque * motor.gear * motor.efficiency / r, "P": motor.power / 1000.0,
            "eta": motor.efficiency, "C_rr": veh.corners[0].c_rr, "C_d": aero.cd, "A": aero.front_area,
            "rho": aero.rho}


def shape_ok(test, got, names):
    test.assertEqual(set(got["inputs"]), set(names))
    for k, v in got["inputs"].items():
        test.assertEqual(set(v), {"value", "unit"}, k)
        test.assertTrue(math.isfinite(v["value"]), k)
    json.dumps(got, allow_nan=False)


class SteerRoomTest(unittest.TestCase):
    def test_inputs_and_builder_as_before(self):
        got = scene_inputs.steer_room_inputs(DESIGN)
        shape_ok(self, got, FORMULAS["geometry.free_steer_angle"]["inputs"])
        self.assertTrue(all(v["unit"] == "mm" for v in got["inputs"].values()))
        self.assertIn(got["support"], got["supports"])
        before = old_h1(DESIGN)
        now = ab.h1_calc(DESIGN, {"params": {"loading": "two_cargo"}})
        self.assertEqual({k: v["value"] for k, v in now.items()}, before)
        self.assertEqual({k: v["unit"] for k, v in now.items()}, {k: "mm" for k in before})
        self.assertEqual([got["inputs"][k]["value"] for k in "wlc"], [before[f"steer_{k}"] for k in "wlc"])

    def test_no_steered_wheel_beside_a_member(self):
        d = json.loads(json.dumps(DESIGN))
        for s in d["supports"]:
            s["actuation"]["steer"] = False
        with self.assertRaises(DesignError):
            scene_inputs.steer_room_inputs(d)


class KneeSpaceTest(unittest.TestCase):
    def test_inputs_and_builder_as_before(self):
        for loading in ("driver", "two", "two_cargo"):
            with self.subTest(loading=loading):
                got = scene_inputs.knee_space_inputs(DESIGN, loading)
                shape_ok(self, got, FORMULAS["occupant.knee_room"]["inputs"])
                before = old_h3(DESIGN, loading)
                now = ab.h3_calc(DESIGN, {"params": {"loading": loading}})
                self.assertEqual({k: v["value"] for k, v in now.items()}, before)
                self.assertEqual(got["inputs"]["L"]["value"], before["knee_length"])
                self.assertEqual(got["occupants"][got["occupant"]]["L_mm"], before["knee_length"])


class LaunchTest(unittest.TestCase):
    def test_formula_on_the_shelf(self):
        f = FORMULAS["launch.peak_tractive_force"]
        self.assertEqual(f["domain"], "powertrain")
        self.assertEqual({k: v["unit"] for k, v in f["inputs"].items()}, {"T": "N*m", "i": "1", "eta": "1", "r": "mm"})
        self.assertEqual(f["outputs"]["F_max"]["unit"], "N")
        self.assertTrue(f["assumptions"])
        self.assertIn("definition", f["source"])
        out = REG.evaluate("launch.peak_tractive_force", {"T": {"value": 50.0, "unit": "N*m"},
                                                          "i": {"value": 8.0, "unit": "1"},
                                                          "eta": {"value": 0.9, "unit": "1"},
                                                          "r": {"value": 0.3, "unit": "m"}})
        self.assertAlmostEqual(out["outputs"]["F_max"]["value"], 50.0 * 8.0 * 0.9 / 0.3, places=9)
        with self.assertRaises(ValueError):
            REG.evaluate("launch.peak_tractive_force", {"T": {"value": 50.0, "unit": "N*m"},
                                                        "i": {"value": 8.0, "unit": "1"},
                                                        "eta": {"value": 0.9, "unit": "1"},
                                                        "r": {"value": 0.0, "unit": "mm"}})

    def test_inputs_and_f_max_as_before(self):
        got = scene_inputs.launch_inputs(DESIGN, "two_cargo", LAUNCH_LC["params"])
        shape_ok(self, got, ("T", "i", "eta", "r", "P", "C_rr", "C_d", "A", "rho"))
        self.assertTrue(got["driven_wheels"])
        self.assertTrue({"m", "grade", "v", "lambda", "F_max"} <= set(got["other_inputs"]))
        before = old_launch(DESIGN, LAUNCH_LC)
        f_max = REG.evaluate("launch.peak_tractive_force",
                             {k: got["inputs"][k] for k in ("T", "i", "eta", "r")})["outputs"]["F_max"]
        self.assertEqual(f_max["unit"], "N")
        self.assertAlmostEqual(f_max["value"], before["F_max"], delta=1e-9 * before["F_max"])
        now = k2_tests.launch_inputs(DESIGN, LAUNCH_LC)
        for k in ("P", "eta", "C_rr", "C_d", "A", "rho"):
            self.assertEqual(now[k]["value"], before[k], k)
        self.assertEqual(now, got["inputs"])

    def test_p3_step_takes_f_max_from_the_prediction(self):
        step = k2_tests.PLANS["P3"]
        first, then = step["predictions"]
        self.assertEqual((first["quantity"], first["formula_id"], first["output"]),
                         ("peak_tractive_force", "launch.peak_tractive_force", "F_max"))
        self.assertEqual(then["inputs"]["F_max"], "pred.peak_tractive_force")
        self.assertNotIn("F_max", k2_tests.PLANS["P2"]["predictions"][0]["inputs"])
        frm = first["inputs_from"](DESIGN, LAUNCH_LC)
        ins = {k: frm[ref.split(".", 1)[1]] for k, ref in first["inputs"].items()}
        f_max = REG.evaluate(first["formula_id"], ins)["outputs"]["F_max"]
        before = old_launch(DESIGN, LAUNCH_LC)
        self.assertAlmostEqual(f_max["value"], before["F_max"], delta=1e-9 * before["F_max"])
        # time_to_speed with the predicted F_max and with the F_max the builder computed before
        from hiraku.catalog.summary import design_summary

        mass = design_summary(DESIGN)["loadings"]["two_cargo"]["mass"]
        common = {"m": mass, "P": frm["P"], "eta": frm["eta"], "C_rr": frm["C_rr"], "C_d": frm["C_d"], "A": frm["A"],
                  "rho": frm["rho"], "grade": LAUNCH_LC["params"]["grade"], "lambda": DESIGN["variables"]["mass_factor"],
                  "v": LAUNCH_LC["params"]["target_speed"]}
        t_new = REG.evaluate("launch.time_to_speed", dict(common, F_max=f_max))["outputs"]["t"]["value"]
        t_old = REG.evaluate("launch.time_to_speed",
                             dict(common, F_max={"value": before["F_max"], "unit": "N"}))["outputs"]["t"]["value"]
        self.assertAlmostEqual(t_new, t_old, delta=1e-9 * t_old)


if __name__ == "__main__":
    unittest.main()

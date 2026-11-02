"""Every K2 scenario as a contract AnalysisTool: own known answers, record shape, sanity."""

import json
import time
import unittest

from hiraku import contract, solvers
from hiraku.contract.protocols import LOADINGS, AnalysisTool
from hiraku.solvers.k2_vehicle import tools
from hiraku.solvers.k2_vehicle.drive import on_board
from hiraku.solvers.k2_vehicle.model import box_design, build_vehicle
from hiraku.solvers.k2_vehicle.shelf import TOOLS
from hiraku.solvers.k2_vehicle.units import SI_OF


def q(v, u):
    return {"value": v, "unit": u}


def demo_design():
    """A 4-wheel vehicle with two occupants (the first one drives) and cargo."""
    d = box_design(mass=300.0, cg_x=-0.85, cg_z=0.45, track=1.2, wheelbase=1.8, k_w=20.0, wheel_mass=15.0,
                   box_size=(2.2, 1.1, 0.4))
    for pid, y in (("occupant_R", -0.3), ("occupant_L", 0.3)):
        d["parts"].append({"id": pid, "name": pid, "kind": "occupant", "mass_kg": 76.6,
                           "box": {"center_mm": [-800.0, y * 1000.0, 950.0], "size_mm": [450.0, 450.0, 520.0]}})
    d["parts"].append({"id": "cargo", "name": "cargo", "kind": "cargo", "mass_kg": 150.0,
                       "box": {"center_mm": [-1600.0, 0.0, 600.0], "size_mm": [900.0, 1000.0, 300.0]}})
    d["variables"].update({"motor_torque": q(60.0, "N*m"), "motor_power": q(10.0, "kW"), "gear_ratio": q(8.0, "1")})
    # a ladder frame: two rails and two cross members (steel tubes)
    d["nodes"] = [{"id": n, "xyz_mm": xyz} for n, xyz in (
        ("LF", [300.0, 350.0, 350.0]), ("RF", [300.0, -350.0, 350.0]), ("LR", [-2100.0, 350.0, 350.0]),
        ("RR", [-2100.0, -350.0, 350.0]), ("LA", [0.0, 350.0, 350.0]), ("RA", [0.0, -350.0, 350.0]),
        ("LB", [-1800.0, 350.0, 350.0]), ("RB", [-1800.0, -350.0, 350.0]))]
    d["members"] = [
        {"id": "rail_L", "node_a": "LF", "node_b": "LR", "section": "rt40x80x2", "material": "steel"},
        {"id": "rail_R", "node_a": "RF", "node_b": "RR", "section": "rt40x80x2", "material": "steel"},
        {"id": "cross_A", "node_a": "LA", "node_b": "RA", "section": "rt40x40x2", "material": "steel"},
        {"id": "cross_B", "node_a": "LB", "node_b": "RB", "section": "rt40x40x2", "material": "steel"},
    ]
    d["sections"] = {"rt40x80x2": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.0}},
                     "rt40x40x2": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 2.0}}}
    d["materials"] = {"steel": {"name": "steel (test)", "E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 7850.0,
                                "yield_MPa": 235.0, "source": "test value", "verified": False}}
    return d


def frame_mass():
    """Hand calculation of the test frame: density x (b h - (b - 2t)(h - 2t)) x length."""
    rail = 7850.0 * (40 * 80 - 36 * 76) * 1e-6 * 2.4
    cross = 7850.0 * (40 * 40 - 36 * 36) * 1e-6 * 0.7
    return 2 * rail + 2 * cross


CASES = {
    "tilt": {"loading": "two_cargo"},
    "crosswind": {"loading": "empty", "wind_speed": q(30.0, "m/s"), "speed": q(40.0, "km/h")},
    "braking": {"loading": "two_cargo", "speed": q(35.0, "km/h"), "mu": q(0.6, "1"), "parking_slope": q(20.0, "%")},
    "launch": {"loading": "two_cargo", "target_speed": q(40.0, "km/h")},
    "lane_change": {"loading": "two", "speed": q(40.0, "km/h"), "mu": q(0.8, "1"), "find_limit": False},
    "offroad_roll": {"loading": "two_cargo", "speed": q(20.0, "km/h"), "side_slope": q(15.0, "deg"), "length": q(40.0, "m")},
    "step_climb_dynamic": {"loading": "two_cargo", "step_height": q(100.0, "mm"), "speed": q(10.0, "km/h"),
                           "mu": q(0.8, "1")},
}


class EntryPoint(unittest.TestCase):
    def test_tools_lists_every_scenario_without_computing(self):
        t0 = time.perf_counter()
        names = [t.name for t in tools()]
        self.assertLess(time.perf_counter() - t0, 2.0)
        self.assertEqual(names, ["tilt", "crosswind", "braking", "launch", "lane_change", "offroad_roll",
                                 "step_climb_dynamic"])
        self.assertNotIn("step_climb", names)  # the analysis department's hand-calculation tool keeps that name
        for t in tools():
            self.assertIsInstance(t, AnalysisTool)

    def test_loading_chooses_occupants_and_cargo(self):
        d = demo_design()
        masses = {ld: build_vehicle(on_board(d, ld)).m_total for ld in ("empty", "driver", "two", "two_cargo")}
        base = 300.0 + 4 * 15.0 + frame_mass()
        self.assertAlmostEqual(masses["empty"], base)
        self.assertAlmostEqual(masses["driver"], base + 76.6)
        self.assertAlmostEqual(masses["two"], base + 2 * 76.6)
        self.assertAlmostEqual(masses["two_cargo"], base + 2 * 76.6 + 150.0)
        drv = on_board(d, "driver")
        self.assertEqual([p["id"] for p in drv["parts"] if p["kind"] == "occupant"], ["occupant_R"])


def k0_formulas():
    return {f["formula_id"]: f for f in solvers.formula_registry().list_formulas()}


class Declarations(unittest.TestCase):
    """The load-case params and the K0 pairs that the load-case builders and the agents use."""

    def test_describe_params(self):
        for name, tool in TOOLS.items():
            with self.subTest(tool=name):
                desc = tool.describe()
                self.assertEqual(desc["name"], name)
                self.assertEqual(desc["tests"], tool.tests)
                recs = desc["params"]
                names = [p["name"] for p in recs]
                self.assertEqual(len(names), len(set(names)))
                self.assertEqual(recs[0]["name"], "loading")
                self.assertEqual(recs[0]["choices"], list(LOADINGS))
                for p in recs:
                    self.assertIn(p["kind"], ("quantity", "string", "boolean"))
                    self.assertTrue(p["meaning"])
                    if p["kind"] == "quantity":
                        self.assertIn(p["unit"], contract.UNITS)
                        if "default" in p:
                            self.assertEqual(contract.errors("quantity", p["default"]), [])
                            self.assertEqual(p["default"]["unit"], p["unit"])
                json.dumps(desc)

    def test_predicts_name_k0_outputs_and_declared_params(self):
        formulas = k0_formulas()
        for name, tool in TOOLS.items():
            desc = tool.describe()
            declared = {p["name"]: p for p in desc["params"]}
            self.assertTrue(desc["predicts"], name)
            self.assertEqual(tool.spec()["formulas"], desc["formulas"])
            for pair in desc["predicts"]:
                with self.subTest(tool=name, quantity=pair["quantity"], formula=pair["formula_id"]):
                    f = formulas[pair["formula_id"]]
                    self.assertIn(pair["output"], f["outputs"])
                    for inp, lc_name in pair.get("inputs_from_load_case", {}).items():
                        self.assertIn(inp, f["inputs"])
                        self.assertIn(lc_name, declared)
                        # the registry converts units of the same dimension
                        self.assertEqual(SI_OF[declared[lc_name]["unit"]], SI_OF[f["inputs"][inp]["unit"]])

    def test_design_variables_fill_absent_params(self):
        from hiraku.solvers.k2_vehicle.shelf import CrosswindTool
        d = demo_design()
        d["variables"].update({"side_area": q(2.4, "m^2"), "centre_of_pressure_height": q(800.0, "mm"),
                               "side_force_coefficient": q(1.1, "1"), "rolling_resistance": q(0.03, "1"),
                               "tyre_stiffness": q(120.0, "N/mm")})
        tool = CrosswindTool()
        tool._vars = d["variables"]
        self.assertAlmostEqual(tool.q({}, "side_area", "m^2"), 2.4)
        self.assertAlmostEqual(tool.q({"side_area": q(2.0, "m^2")}, "side_area", "m^2"), 2.0)  # the load case first
        self.assertAlmostEqual(tool.q({}, "cy", "1"), 1.1)
        self.assertTrue(tool.given({}, "cy"))
        self.assertFalse(tool.given({}, "drift_time"))
        veh = build_vehicle(on_board(d, "two_cargo"))
        aero = tool.aero(veh, {})
        ground = min(float(c.pos[2]) - c.radius for c in veh.corners)
        self.assertAlmostEqual(aero.side_area, 2.4)
        self.assertAlmostEqual(aero.side_cp[2] + float(veh.cg_s[2]) - ground, 0.8)
        self.assertEqual({c.c_rr for c in veh.corners}, {0.03})
        self.assertEqual({c.k_z for c in veh.corners}, {120e3})

    def test_designgen_names_are_read(self):
        import math
        from hiraku.solvers.k2_vehicle.shelf import LaunchTool
        d = demo_design()
        d["variables"].update({"max_steer_angle": q(30.0, "deg"), "front_cornering_stiffness": q(25000.0, "N/rad"),
                               "rear_cornering_stiffness": q(30000.0, "N/rad"), "drivetrain_efficiency": q(0.95, "1")})
        veh = build_vehicle(on_board(d, "two_cargo"))
        self.assertAlmostEqual(veh.max_steer, math.radians(30.0))
        front = max(float(c.pos[0]) for c in veh.corners)
        self.assertEqual({c.c_lat for c in veh.corners if float(c.pos[0]) == front}, {12500.0})
        self.assertEqual({c.c_lat for c in veh.corners if float(c.pos[0]) != front}, {15000.0})
        self.assertFalse(any("cornering" in a for a in veh.assumptions))
        tool = LaunchTool()
        tool._vars = d["variables"]
        self.assertAlmostEqual(tool.q({}, "efficiency", "1"), 0.95)
        tool._vars = {}
        tool._assumed = []
        self.assertAlmostEqual(tool.q({}, "efficiency", "1"), 0.9)
        self.assertIn("drivetrain_efficiency", tool._assumed[0])

    def test_unread_params_are_named_and_bad_choices_fail(self):
        d = demo_design()
        lc = {"load_case_id": "x", "params": {"stepheight": q(100.0, "mm")}}
        rec = TOOLS["step_climb_dynamic"].run(d, lc, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("stepheight", rec["effects"][0]["name"])
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        lc = {"load_case_id": "x", "params": {"direction": "up"}}
        rec = TOOLS["lane_change"].run(d, lc, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("direction", rec["error"])


class KcChannels(unittest.TestCase):
    def test_kc_vehicle_records_camber_and_toe(self):
        from hiraku.solvers.k2_vehicle.record import Recorder
        from hiraku.solvers.k2_vehicle.scenario import new_sim
        veh = build_vehicle(box_design(susp="kc_table", wheel_mass=15.0))
        sim = new_sim(veh, settle_s=0.2)
        rec = Recorder(sim, 25.0)
        rec.sample()
        names = {s["channel"] for s in rec.series()}
        self.assertIn("wheel_camber.FL", names)
        self.assertIn("wheel_toe.RR", names)
        fl = [s for s in rec.series() if s["channel"] == "wheel_camber.FL"][0]["values"][0]
        fr = [s for s in rec.series() if s["channel"] == "wheel_camber.FR"][0]["values"][0]
        self.assertEqual(fl, fr)


class OwnChecks(unittest.TestCase):
    def test_every_tool_passes_its_known_answers(self):
        for name, tool in TOOLS.items():
            for c in tool.own_checks():
                with self.subTest(tool=name, case=c["case"]):
                    self.assertTrue(c["pass"], f"{name}: {c['case']}: expected {c['expected']} got {c['got']}")


class Runs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = demo_design()
        cls.recs = {}
        for name, params in CASES.items():
            lc = {"load_case_id": f"{name}-t", "test_id": "S1", "domain": "handling", "title": name,
                  "params": params, "tolerance_rel": 0.1}
            cls.recs[name] = TOOLS[name].run(cls.d, lc, [{"prediction_id": "pred-0001"}], {})

    def res(self, name):
        return {r["quantity"]: r["value"] for r in self.recs[name]["results"]}

    def test_records_fit_the_contract(self):
        for name, rec in self.recs.items():
            with self.subTest(tool=name):
                self.assertEqual(rec["status"], "ok", rec.get("error"))
                self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
                self.assertTrue(rec["series"])
                json.dumps(rec)

    def test_paired_results_carry_the_formula_unit(self):
        formulas = k0_formulas()
        only_past_the_limit = {"tip_slope_angle"}  # in test_a_vehicle_that_tips_stops_the_run
        for name, rec in self.recs.items():
            units = {r["quantity"]: r["unit"] for r in rec["results"]}
            for pair in TOOLS[name].describe()["predicts"]:
                if pair["quantity"] in only_past_the_limit:
                    continue
                with self.subTest(tool=name, quantity=pair["quantity"], formula=pair["formula_id"]):
                    self.assertIn(pair["quantity"], units)
                    self.assertEqual(units[pair["quantity"]],
                                     formulas[pair["formula_id"]]["outputs"][pair["output"]]["unit"])

    def test_scenes(self):
        kinds = {name: [o["kind"] for o in rec.get("scene", [])] for name, rec in self.recs.items()}
        self.assertEqual(kinds["tilt"], ["tilt_table"])
        self.assertEqual(self.recs["tilt"]["scene"][0]["angle_channel"], "table_angle")
        self.assertIn("table_angle", {s["channel"] for s in self.recs["tilt"]["series"]})
        self.assertEqual(kinds["lane_change"].count("gate"), 6)
        self.assertEqual(kinds["lane_change"].count("cone"), 12)
        self.assertEqual(kinds["offroad_roll"], ["road_profile", "slope"])
        prof = self.recs["offroad_roll"]["scene"][0]["profile"]
        self.assertEqual(len(prof["z_mm"]["left"]), len(prof["x_mm"]))
        self.assertEqual(self.recs["offroad_roll"]["scene"][1]["rpy_deg"], [15.0, 0.0, 0.0])
        self.assertEqual(kinds["step_climb_dynamic"], ["step"])
        self.assertEqual(kinds["crosswind"], [])

    def test_step_scene_series_and_vectors_from_one_run(self):
        rec = self.recs["step_climb_dynamic"]
        step = rec["scene"][0]
        edge = step["position_mm"][0] - step["size_mm"][0] / 2.0
        self.assertAlmostEqual(step["size_mm"][2], 100.0)
        on_corner = [v["origin_mm"][0] for v in rec["vectors"]
                     if v["kind"] == "reaction" and abs(v["origin_mm"][2] - 100.0) < 5.0]
        self.assertTrue(on_corner)
        for x in on_corner:  # the reactions at lift-off push on the step's corner
            self.assertLess(abs(x - edge), 5.0)
        # the animated wheel meets this step: when its centre has risen 20 mm it is within a
        # wheel radius behind the corner (the faster run 2 has its step 2 m further on)
        series = {s["channel"]: s for s in rec["series"]}
        wx, wz = series["wheel_center_x.FL"], series["wheel_center_z.FL"]
        up = next(i for i, z in enumerate(wz["values"]) if z > wz["values"][0] + 0.02)
        r = next(s["radius_mm"] for s in self.d["supports"] if s["id"] == "FL")
        self.assertGreater(wx["values"][up] * 1000.0, edge - r)
        self.assertLess(wx["values"][up] * 1000.0, edge)

    def test_a_vehicle_that_tips_stops_the_run(self):
        params = {"loading": "two_cargo", "side_slope": q(55.0, "deg"), "length": q(20.0, "m"),
                  "slope_ramp_time": q(8.0, "s"), "mu": q(2.0, "1")}  # held tyres: it tips, not slides
        rec = TOOLS["offroad_roll"].run(self.d, {"load_case_id": "x", "params": params},
                                        [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        res = {r["quantity"]: r for r in rec["results"]}
        self.assertEqual(res["tipped"]["value"], 1.0)
        self.assertEqual(res["tip_slope_angle"]["unit"], "deg")
        self.assertLess(res["tip_slope_angle"]["value"], 55.0)
        series = {s["channel"]: s for s in rec["series"]}
        self.assertEqual(len({len(s["values"]) for s in rec["series"]}), 1)
        self.assertEqual(series["slope_angle"]["values"][0] < 5.0, True)  # the record starts level
        self.assertLess(max(abs(v) for v in series["roll"]["values"]), 61.0)  # and stops once tipped
        self.assertEqual(rec["scene"][1]["angle_channel"], "slope_angle")
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])

    def test_assumptions_are_in_the_record(self):
        notes = self.recs["crosswind"]["assumptions"]
        self.assertTrue(any(n.startswith("cy = 1") for n in notes), notes)
        self.assertTrue(any("side_area" in n and "projection" in n for n in notes), notes)
        self.assertFalse(any(e["name"].startswith("assumption") for e in self.recs["crosswind"].get("effects", [])))

    def test_braking_close_to_the_friction_limit(self):
        r = self.res("braking")
        v = 35.0 / 3.6
        self.assertAlmostEqual(r["braking_distance"], v * v / (2 * 0.6 * 9.80665), delta=0.05 * r["braking_distance"])
        self.assertEqual(r["min_rear_wheel_load"], r["min_wheel_load"])  # load moves forward: a rear wheel is lightest
        self.assertGreater(r["max_pitch"], 0.0)  # nose down

    def test_launch_reaches_40(self):
        r = self.res("launch")
        self.assertEqual(r["reached_target"], 1.0)
        self.assertGreater(r["top_speed"], 40.0)

    def test_crosswind_tips_above_the_gust(self):
        r = self.res("crosswind")
        self.assertGreater(r["critical_wind_speed"], 30.0)
        self.assertGreater(r["min_upwind_wheel_load"], 0.0)

    def test_step_force_ratio_near_rigid_wheel(self):
        r = self.res("step_climb_dynamic")
        self.assertAlmostEqual(r["force_ratio"], 1.118, delta=0.08)
        self.assertEqual(r["climbed"], 1.0)
        kinds = {v["kind"] for v in self.recs["step_climb_dynamic"]["vectors"]}
        self.assertEqual(kinds, {"reaction", "grip", "inertia"})

    def test_step_vectors_balance_the_weight_with_the_frame(self):
        from hiraku.solvers.k2_vehicle.units import G0
        m = 300.0 + 4 * 15.0 + 2 * 76.6 + 150.0 + frame_mass()
        tot = [sum(v["vector_N"][k] for v in self.recs["step_climb_dynamic"]["vectors"]) for k in range(3)]
        self.assertAlmostEqual(tot[0], 0.0, delta=1e-6)
        self.assertAlmostEqual(tot[1], 0.0, delta=1e-6)
        self.assertAlmostEqual(tot[2], m * G0, delta=1e-6 * m * G0)

    def test_lane_change_and_offroad_keep_wheels_down(self):
        self.assertEqual(self.res("lane_change")["two_wheel_lift"], 0.0)
        self.assertLess(self.res("lane_change")["max_path_error"], 1.0)
        self.assertGreaterEqual(self.res("offroad_roll")["two_wheel_lift_time"], 0.0)


if __name__ == "__main__":
    unittest.main()

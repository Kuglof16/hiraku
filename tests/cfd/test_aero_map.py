"""The aero map and the uncertainty table on forces made up here: no GPU. The fake forces come from known
coefficients, a known height and a known place, so the tables must give those back."""

import json
import math
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hiraku.cfd import aero_map, uncertainty

RHO = 1.225
V = 40.0 / 3.6
AREA = 1.7
SIDE = 1.8
DESIGN_FILE = Path(__file__).resolve().parents[2] / "data" / "designs" / "offroad-4w-v1.json"


@dataclass
class FakeResult:
    wall_time_s: float = 2.5


@dataclass
class FakeRun:
    design_version: Any
    yaw_deg: float
    speed_ms: float
    air_speed_ms: float
    dx: float
    forces_N: dict
    groups_N: dict
    coefficients: dict
    geometry: dict
    convergence: dict
    numerics: dict
    assumptions: list
    result: FakeResult = field(default_factory=FakeResult)

    def save(self, folder: Path) -> None:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "case.json").write_text(json.dumps({"yaw_deg": self.yaw_deg, "forces_N": self.forces_N}), encoding="utf-8")


def fake_run(yaw=0.0, speed=V, dx=0.08, cda=1.4, cya=7.0, height=0.7, place=-0.2, lift=0.1, minus_gain=1.0, exponent=2.0, grid_gain=1.0, version=5, air=None):
    """A VehicleRun in vehicle.py's axes and signs. cya: C_y A in the form F = 1/2 rho C_y A v^2 with v = U sin(yaw).
    exponent: forces go as speed^exponent from the speed V (2: the law). grid_gain: forces times this on this grid.
    air: the wind on a vehicle at rest (speed 0)."""
    b = math.radians(yaw)
    air = air if air is not None else speed / math.cos(b)
    q = 0.5 * RHO * air * air
    scale = grid_gain * (speed / V) ** (exponent - 2.0)
    side = -math.copysign(1.0, yaw) * q * math.sin(b) ** 2 * cya * scale if yaw else 0.0  # wind from the left pushes to -y
    if yaw < 0.0:
        side *= minus_gain
    fx, fz = -q * cda * scale, q * AREA * lift * scale
    forces = {"Fx": fx, "Fy": side, "Fz": fz, "Mx": -height * side, "My": 0.5 * fx, "Mz": place * side}
    coeff = {"q_Pa": q, "reference_area_m2": AREA, "Cd": -fx / (q * AREA), "CdA_m2": -fx / q, "Cy": side / (q * AREA), "Cl": fz / (q * AREA)}
    numerics = {"cells": [188, 112, 69], "domain_m": {"upstream": 5.0}, "u_lattice": 0.05, "max_mach": 0.1, "steps": 100, "warm_steps": 40, "average_steps": 60,
                "mlups": 900.0, "tau0": 0.5001, "dt_s": 1e-4, "smagorinsky": 0.15}
    return FakeRun(version, yaw, speed, air, dx, forces, {"wheels": [fx / 4.0, side / 4.0, 0.0]}, coeff, {"solid_nodes": 10},
                   {"quarter_means_Fx_N": [fx] * 4, "spread_pct": 0.5}, numerics, ["空気は高さによらず一様 (大気の境界層なし)"])


def rows(**kw):
    return [aero_map.case_row(fake_run(y, **kw)) for y in aero_map.DEFAULT_YAWS_DEG]


def make_map(sweep=None, study=(), wind_from="left"):
    return aero_map.build_map(sweep if sweep is not None else rows(), moment_centre_mm=[-1010.0, 0.0, 0.0], side_area_m2=SIDE, loading="two_cargo",
                              study_rows=study, wind_from=wind_from)


def row_at(table, yaw):
    return next(r for r in table if r["yaw_deg"] == yaw)


class TableTest(unittest.TestCase):
    def test_one_row_has_the_forces_the_coefficients_and_the_records(self):
        run = fake_run(10.0)
        r = aero_map.case_row(run)
        self.assertEqual(r["case_id"], "40kmh_yaw+10_dx80")
        self.assertEqual(r["wind_from"], "left")
        for key, name in (("Fx_N", "Fx"), ("Fy_N", "Fy"), ("Fz_N", "Fz"), ("Mx_Nm", "Mx"), ("My_Nm", "My"), ("Mz_Nm", "Mz")):
            self.assertEqual(r[key], run.forces_N[name])
        self.assertEqual(r["drag_N"], -run.forces_N["Fx"])
        self.assertAlmostEqual(r["q_Pa"], 0.5 * RHO * (V / math.cos(math.radians(10.0))) ** 2)
        self.assertAlmostEqual(r["CdA_m2"], 1.4)
        self.assertAlmostEqual(r["Cd"], 1.4 / AREA)
        self.assertEqual(r["grid"]["dx_mm"], 80.0)
        self.assertEqual(r["grid"]["cells"], [188, 112, 69])
        self.assertEqual(r["convergence"]["spread_pct"], 0.5)
        self.assertEqual(r["convergence"]["max_mach"], 0.1)
        self.assertIn("空気は高さによらず一様 (大気の境界層なし)", r["assumptions"])
        self.assertNotIn("mlups", r["numerics"])  # a clock number stays in the case's own file

    def test_the_map_states_the_moment_centre_and_the_units(self):
        m = make_map()
        self.assertEqual(m["moment_centre"]["design_mm"], [-1010.0, 0.0, 0.0])
        self.assertIn("地面", m["moment_centre"]["text"])
        self.assertTrue(any("モーメントの中心" in a and "-1010" in a for a in m["assumptions"]))
        self.assertEqual((m["units"]["Fx_N"], m["units"]["Mx_Nm"], m["units"]["CdA_m2"], m["units"]["q_Pa"]), ("N", "N*m", "m^2", "Pa"))
        self.assertEqual(len(m["table"]), 9)

    def test_drag_at_yaw_0(self):
        m = make_map()
        self.assertEqual(len(m["drag_yaw0"]), 1)
        d = m["drag_yaw0"][0]
        self.assertAlmostEqual(d["drag_N"], 0.5 * RHO * 1.4 * V * V)
        self.assertAlmostEqual(d["CdA_m2"], 1.4)
        self.assertAlmostEqual(d["speed_kmh"], 40.0)


class CrosswindFormTest(unittest.TestCase):
    def test_the_form_gives_back_the_force_the_height_and_the_place_on_both_sides(self):
        for yaw in (5.0, -5.0, 20.0, -20.0):
            r = aero_map.case_row(fake_run(yaw))
            f = aero_map.crosswind_form(r, SIDE)
            with self.subTest(yaw=yaw):
                v = r["air_speed_ms"] * abs(math.sin(math.radians(yaw)))
                self.assertAlmostEqual(f["wind_across_ms"], v)
                self.assertAlmostEqual(v, V * math.tan(math.radians(abs(yaw))))  # the crosswind is square to the path
                self.assertAlmostEqual(f["CyA_m2"], 7.0)
                self.assertAlmostEqual(f["C_y"], 7.0 / SIDE)
                self.assertAlmostEqual(0.5 * RHO * f["C_y"] * f["side_area_m2"] * v * v, abs(r["Fy_N"]))  # F = 1/2 rho C_y A v^2
                self.assertAlmostEqual(f["side_cp_height_mm"], 700.0)  # above the ground: positive for both signs
                self.assertAlmostEqual(f["side_cp_x_mm"], -200.0)

    def test_the_sign_of_the_rolling_moment_is_r_cross_f(self):
        # a force to -y at a height z: Mx = y Fz - z Fy = +z |Fy|
        r = aero_map.case_row(fake_run(10.0, height=0.7))
        self.assertLess(r["Fy_N"], 0.0)
        self.assertGreater(r["Mx_Nm"], 0.0)

    def test_no_form_at_yaw_0(self):
        self.assertIsNone(aero_map.crosswind_form(aero_map.case_row(fake_run(0.0)), SIDE))

    def test_plus_and_minus_are_both_there_with_their_difference(self):
        sweep = rows(minus_gain=1.1)
        m = make_map(sweep)
        self.assertEqual([p["yaw_deg"] for p in m["plus_minus"]], [5.0, 10.0, 15.0, 20.0])
        p = m["plus_minus"][-1]
        self.assertAlmostEqual(p["C_y_plus"], 7.0 / SIDE)
        self.assertAlmostEqual(p["C_y_minus"], 1.1 * 7.0 / SIDE)
        self.assertAlmostEqual(p["C_y_diff_pct"], 0.1 / 1.05 * 100.0)
        self.assertAlmostEqual(p["side_cp_height_diff_pct"], 0.0)
        self.assertEqual(len(m["crosswind"]), 8)
        self.assertEqual({f["wind_from"] for f in m["crosswind"]}, {"left", "right"})

    def test_the_k2_point_names_its_yaw_and_takes_the_side_asked_for(self):
        sweep = rows(minus_gain=1.1)
        left = aero_map.k2_point([aero_map.crosswind_form(r, SIDE) for r in sweep if r["yaw_deg"]], "left")
        self.assertEqual((left["from_yaw_deg"], left["wind_from"]), (20.0, "left"))
        self.assertAlmostEqual(left["cy"], 7.0 / SIDE)
        self.assertEqual(left["side_area_m2"], SIDE)
        self.assertAlmostEqual(left["side_cp_height_mm"], 700.0)
        self.assertIn("ヨー +20°", left["note"])
        self.assertIn("1 つの数", left["note"])
        right = aero_map.k2_point([aero_map.crosswind_form(r, SIDE) for r in sweep if r["yaw_deg"]], "right")
        self.assertEqual(right["from_yaw_deg"], -20.0)
        worst = aero_map.k2_point([aero_map.crosswind_form(r, SIDE) for r in sweep if r["yaw_deg"]])
        self.assertEqual(worst["from_yaw_deg"], -20.0)  # the larger rolling moment


class SpeedTest(unittest.TestCase):
    def test_scaling_goes_with_speed_squared_and_says_the_effect_was_not_computed(self):
        r = aero_map.case_row(fake_run(10.0))
        s = aero_map.scale_to_speed(r, 2.0 * V)
        for key in ("Fx_N", "Fy_N", "Mx_Nm"):
            self.assertAlmostEqual(s[key], 4.0 * r[key])
        self.assertIn("計算していません", s["statement"])
        m = make_map()
        self.assertFalse(m["speed_effect"]["computed"])
        self.assertIn("計算していません", m["speed_effect"]["statement"])
        self.assertTrue(any("計算していません" in a for a in m["assumptions"]))

    def test_two_speeds_give_the_exponent_they_ran_to(self):
        sweep = [aero_map.case_row(fake_run(y, speed=v, exponent=1.9)) for v in (V, 2.0 * V) for y in (0.0, 10.0)]
        m = make_map(sweep)
        e = m["speed_effect"]
        self.assertTrue(e["computed"])
        self.assertNotIn("計算していません", e["statement"])
        at10 = next(x for x in e["exponents"] if x["yaw_deg"] == 10.0)
        self.assertAlmostEqual(at10["Fx_exponent"], 1.9)
        self.assertAlmostEqual(at10["Fy_exponent"], 1.9)
        self.assertAlmostEqual(at10["Mx_exponent"], 1.9)
        self.assertEqual(len(m["table"]), 4)


class GridStudyTest(unittest.TestCase):
    def test_the_change_is_between_the_two_finest(self):
        study = [aero_map.case_row(fake_run(y, dx=0.12, grid_gain=1.1)) for y in (0.0, 20.0)]
        m = make_map(rows(), study)
        self.assertEqual(len(m["grid_study"]), 2)
        at0 = next(s for s in m["grid_study"] if s["yaw_deg"] == 0.0)
        self.assertEqual([lv["dx_mm"] for lv in at0["levels"]], [120.0, 80.0])  # coarse to fine
        self.assertAlmostEqual(at0["change_pct"]["CdA_m2"], 10.0)
        self.assertNotIn("Mx_Nm", at0["change_pct"])  # nothing to read at yaw 0
        at20 = next(s for s in m["grid_study"] if s["yaw_deg"] == 20.0)
        self.assertAlmostEqual(at20["change_pct"]["Mx_Nm"], 10.0)
        self.assertEqual(at20["limit_pct"], aero_map.GRID_CHANGE_LIMIT_PCT)

    def test_where_the_study_looks(self):
        cases = aero_map.sweep_cases(aero_map.DEFAULT_YAWS_DEG, [V])
        yaws = lambda got: [c[1] for c in got]
        self.assertEqual(yaws(aero_map.study_cases(cases, "left")), [0.0, 20.0])
        self.assertEqual(yaws(aero_map.study_cases(cases, "right")), [0.0, -20.0])
        self.assertEqual(sorted(yaws(aero_map.study_cases(cases, None))), [-20.0, 0.0, 20.0])
        with_f1 = cases + aero_map.f1_cases(30.0, V)
        self.assertEqual(aero_map.study_cases(with_f1, "left"), [(V, 0.0, None), (0.0, 90.0, 30.0)])
        two_speeds = aero_map.sweep_cases([0.0, 10.0], [V, 2.0 * V])
        self.assertEqual(aero_map.study_cases(two_speeds, "left"), [(V, 0.0, None), (V, 10.0, None)])  # once per yaw, the first speed


class SweepTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = json.loads(DESIGN_FILE.read_text(encoding="utf-8"))

    def fake_runner(self, calls):
        def runner(gpu, design, yaw_deg=0.0, speed_ms=V, dx=0.08, reference_area_m2=None, loading=None, air_speed_ms=None, domain=None, **kw):
            calls.append({"gpu": gpu, "yaw": yaw_deg, "speed": speed_ms, "dx": dx, "area": reference_area_m2, "loading": loading, "air": air_speed_ms,
                          "half_width": domain.half_width})
            return fake_run(yaw_deg, speed=speed_ms, dx=dx, version=design.get("design_version"), air=air_speed_ms)
        return runner

    def test_the_default_sweep_keeps_each_case_and_reads_the_design_speed(self):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            m = aero_map.run_sweep("gpu", self.design, folder=Path(d), loading="two_cargo", wind_from="left", runner=self.fake_runner(calls))
            self.assertEqual(sorted(p.name for p in Path(d).iterdir() if (p / "case.json").is_file()),
                             sorted({aero_map.case_id(c["speed"], c["yaw"], c["dx"]) for c in calls}))
        self.assertEqual([c["yaw"] for c in calls][:9], list(aero_map.DEFAULT_YAWS_DEG))
        self.assertEqual({c["half_width"] for c in calls}, {4.5})
        self.assertEqual([(c["yaw"], round(c["dx"], 6)) for c in calls][9:], [(0.0, 0.12), (20.0, 0.12)])  # the study on a second grid
        self.assertAlmostEqual(calls[0]["speed"], V)  # design variable max_speed, 40 km/h
        self.assertEqual({c["gpu"] for c in calls}, {"gpu"})
        self.assertEqual(len({c["area"] for c in calls}), 1)  # one reference area for all the cases
        self.assertEqual(len(m["table"]), 9)
        self.assertEqual(len(m["study_table"]), 2)
        self.assertTrue(all(r["folder"] == r["case_id"] for r in m["table"]))
        # the projections of the design's own shapes
        self.assertTrue(1.0 < m["side_area_m2"] < 3.5, m["side_area_m2"])  # the design changes; the size stays this order
        self.assertEqual(m["k2_point"]["from_yaw_deg"], 20.0)

    def test_an_argument_speed_wins_and_no_speed_is_refused(self):
        calls = []
        aero_map.run_sweep("gpu", self.design, cases=aero_map.plan_cases(self.design, [0.0], [5.0]), study_factor=None, runner=self.fake_runner(calls))
        self.assertEqual([c["speed"] for c in calls], [5.0])
        no_speed = {**self.design, "variables": {k: v for k, v in self.design["variables"].items() if k != "max_speed"}}
        with self.assertRaises(ValueError):
            aero_map.run_sweep("gpu", no_speed, runner=self.fake_runner([]))

    def test_the_f1_points_run_at_rest_and_moving_on_a_wider_domain(self):
        calls = []
        cases = aero_map.plan_cases(self.design, extra=aero_map.f1_cases(30.0, V))
        with tempfile.TemporaryDirectory() as d:
            m = aero_map.run_sweep("gpu", self.design, cases=cases, folder=Path(d), loading="two_cargo", wind_from="left", runner=self.fake_runner(calls))
            self.assertTrue((Path(d) / "rest_wind30_yaw+90_dx80" / "case.json").is_file())
            self.assertTrue((Path(d) / "40kmh_yaw-70_dx80" / "case.json").is_file())
        rest = [c for c in calls if c["yaw"] == 90.0 and c["dx"] == 0.08][0]
        self.assertEqual((rest["speed"], rest["air"], rest["half_width"]), (0.0, 30.0, 9.0))
        moving = [c for c in calls if c["yaw"] == 70.0][0]
        self.assertEqual((moving["air"], moving["half_width"]), (None, 9.0))
        self.assertAlmostEqual(moving["speed"], V)
        self.assertEqual(len(m["table"]), 13)
        self.assertEqual((m["k2_point"]["from_yaw_deg"], m["k2_point"]["from_speed_kmh"]), (90.0, 0.0))
        self.assertAlmostEqual(m["k2_point"]["from_wind_across_ms"], 30.0)
        self.assertIn("止まった車に横風 30 m/s", m["k2_point"]["note"])
        self.assertAlmostEqual(m["k2_point"]["cy"], 7.0 / m["side_area_m2"])
        self.assertTrue(any("止まった車" in a for a in m["assumptions"]))
        study = [(s["speed_kmh"], s["yaw_deg"]) for s in m["grid_study"]]
        self.assertEqual(study[1], (0.0, 90.0))  # the study looks at what K2 reads
        self.assertEqual([p["yaw_deg"] for p in m["plus_minus"]], [90.0, 5.0, 10.0, 15.0, 20.0, 70.0])
        self.assertEqual(len(m["drag_yaw0"]), 1)  # the drag is the moving vehicle's
        self.assertFalse(m["speed_effect"]["computed"])  # at rest is another scene, not another speed

    def test_a_sweep_saved_by_hand_makes_the_same_map(self):
        design = {**self.design, "design_version": 5}
        with tempfile.TemporaryDirectory() as d:
            for name, run in (("a", fake_run(0.0)), ("b", fake_run(20.0)), ("c", fake_run(90.0, speed=0.0, air=30.0)), ("coarse", fake_run(90.0, speed=0.0, air=30.0, dx=0.12, grid_gain=1.05))):
                (Path(d) / name).mkdir()
                summary = {k: v for k, v in vars(run).items() if k != "result"}
                (Path(d) / name / "case.json").write_text(json.dumps(summary), encoding="utf-8")
            m = aero_map.map_from_folder(Path(d), design, loading="two_cargo", wind_from="left")
            with self.assertRaises(ValueError):  # the areas and the hand values of another version would not belong with them
                aero_map.map_from_folder(Path(d), {**self.design, "design_version": 4})
        self.assertEqual([(r["yaw_deg"], r["folder"]) for r in m["table"]], [(0.0, "a"), (20.0, "b"), (90.0, "c")])
        self.assertEqual(m["k2_point"]["from_yaw_deg"], 90.0)
        self.assertAlmostEqual(m["grid_study"][0]["change_pct"]["Mx_Nm"], 5.0)
        self.assertTrue(1.0 < m["side_area_m2"] < 3.5, m["side_area_m2"])  # the design changes; the size stays this order
        self.assertAlmostEqual(m["table"][0]["wall_time_s"], 188 * 112 * 69 * 100 / (900.0 * 1e6))  # from the record's own speed
        with tempfile.TemporaryDirectory() as d, self.assertRaises(ValueError):
            aero_map.map_from_folder(Path(d), design)

    def test_yaw_list_and_cases(self):
        self.assertEqual(aero_map.yaw_list([0, -0.0, 5, 5]), [0.0, 5.0])
        self.assertEqual(str(aero_map.yaw_list([-0.0])[0]), "0.0")
        self.assertEqual(aero_map.yaw_list([90.0, -90.0]), [90.0, -90.0])
        for bad in ([91.0], []):
            with self.assertRaises(ValueError):
                aero_map.yaw_list(bad)
        with self.assertRaises(ValueError):  # yaw 90 only at rest
            aero_map.sweep_cases([90.0], [V])
        with self.assertRaises(ValueError):  # at rest needs a wind
            aero_map.sweep_cases([90.0], [0.0])
        self.assertEqual(aero_map.sweep_cases([85.0], [V]), [(V, 85.0, None)])
        self.assertEqual(aero_map.sweep_cases([90.0], [0.0], 30.0), [(0.0, 90.0, 30.0)])
        self.assertEqual(aero_map.f1_cases(30.0, V), [(0.0, 90.0, 30.0), (0.0, -90.0, 30.0), (V, 70.0, None), (V, -70.0, None)])
        self.assertEqual(aero_map.f1_cases(30.0), [(0.0, 90.0, 30.0), (0.0, -90.0, 30.0)])
        self.assertEqual(aero_map.case_id(0.0, 90.0, 0.08, 30.0), "rest_wind30_yaw+90_dx80")
        self.assertEqual(aero_map.case_id(V, -70.0, 0.08), "40kmh_yaw-70_dx80")
        self.assertEqual(aero_map.domain_of(20.0).half_width, 4.5)
        self.assertEqual(aero_map.domain_of(-70.0).half_width, 9.0)
        self.assertEqual(aero_map.largest_grid([(V, 0.0, None), (0.0, 90.0, 30.0)], 0.16).ny, round(18.0 / 0.16))
        self.assertEqual(aero_map.speed_ms_of({"variables": {"max_speed": {"value": 10.0, "unit": "m/s"}}}), 10.0)
        self.assertIsNone(aero_map.speed_ms_of({"variables": {}}))


# Inputs near the demo design (data/designs/offroad-4w-v1.json), to put the verdicts on either side of the limits
F1_HAND = {"m_kg": 646.16, "t_mm": 1100.0, "rho": 1.225, "C_y": 1.0, "A_m2": 2.4, "h_cp_mm": 800.0, "speed_ms": V}
P4_HAND = {"m_kg": 646.16, "d_km": 20.0, "v_kmh": 32.0, "C_rr": 0.03, "C_d": 0.8, "A_m2": 1.6, "H_m": 150.0, "eta": 0.75, "eta_regen": 0.5,
           "rho": 1.225, "E_batt_kWh": 6.0, "soc_start_pct": 100.0}


def f1_crit(limit):
    return {"quantity": "critical_wind_speed", "op": ">=", "limit": limit, "unit": "m/s"}


class EquationsTest(unittest.TestCase):
    """The plain Python is the K0 library's equation."""

    def test_critical_wind_speed_is_the_k0_formula(self):
        from hiraku import solvers

        h = F1_HAND
        got = solvers.formula_registry().evaluate("crosswind.critical_wind_speed", {
            "m": {"value": h["m_kg"], "unit": "kg"}, "t": {"value": h["t_mm"], "unit": "mm"}, "C_y": {"value": h["C_y"], "unit": "1"},
            "A": {"value": h["A_m2"], "unit": "m^2"}, "h_cp": {"value": h["h_cp_mm"], "unit": "mm"}, "rho": {"value": h["rho"], "unit": "kg/m^3"}})["outputs"]["v_crit"]
        self.assertEqual(got["unit"], "m/s")
        self.assertAlmostEqual(uncertainty.critical_wind_speed(h["m_kg"], h["t_mm"], h["C_y"] * h["A_m2"] * h["h_cp_mm"], h["rho"]), got["value"], places=9)

    def test_energy_and_range_are_the_k0_formula(self):
        from hiraku import solvers

        h = P4_HAND
        q = lambda v, u: {"value": v, "unit": u}
        reg = solvers.formula_registry()
        out = reg.evaluate("drive_energy.loop_energy", {
            "m": q(h["m_kg"], "kg"), "d": q(h["d_km"], "km"), "v": q(h["v_kmh"], "km/h"), "C_rr": q(h["C_rr"], "1"), "C_d": q(h["C_d"], "1"),
            "A": q(h["A_m2"], "m^2"), "H": q(h["H_m"], "m"), "eta": q(h["eta"], "1"), "eta_regen": q(h["eta_regen"], "1"),
            "E_batt": q(h["E_batt_kWh"], "kWh"), "rho": q(h["rho"], "kg/m^3")})["outputs"]
        e = uncertainty.loop_energy(h["m_kg"], h["d_km"], h["v_kmh"], h["C_rr"], h["C_d"] * h["A_m2"], h["H_m"], h["eta"], h["eta_regen"], h["rho"])
        self.assertAlmostEqual(e, out["E"]["value"], places=6)
        self.assertAlmostEqual(uncertainty.drive_range(h["d_km"], h["E_batt_kWh"], e), out["range"]["value"], places=6)
        end = reg.evaluate("drive_energy.soc_end", {"E": q(e, "Wh"), "E_batt": q(h["E_batt_kWh"], "kWh"), "soc_start": q(100.0, "%")})["outputs"]["soc_end"]
        self.assertAlmostEqual(uncertainty.soc_end(e, h["E_batt_kWh"], 100.0), end["value"], places=6)

    def test_gravity_is_the_k0_one(self):
        from hiraku.solvers.k0_handcalc.units import STANDARD_GRAVITY

        self.assertEqual(uncertainty.G, STANDARD_GRAVITY)


class UncertaintyTest(unittest.TestCase):
    def table(self, *, limit=30.0, study_gain=1.1, validation=None, p4_crit=True, cya=7.0, cda=1.4):
        sweep = rows(cya=cya, cda=cda)
        study = [aero_map.case_row(fake_run(y, dx=0.12, grid_gain=study_gain, cya=cya, cda=cda)) for y in (0.0, 20.0)] if study_gain else []
        hand = {"f1": {**F1_HAND, "criterion": f1_crit(limit)}, "f2": {"rho": 1.225, "C_d": 0.8, "A_m2": 1.6},
                "p4": {**P4_HAND, "criterion": {"quantity": "soc_end", "op": ">", "limit": 0.0, "unit": "%"} if p4_crit else None}}
        return uncertainty.table(make_map(sweep, study), hand, validation)

    def row(self, t, test, quantity):
        return next(r for r in t["rows"] if (r["test"], r["quantity"]) == (test, quantity))

    def test_f1_shows_the_hand_the_flow_and_the_band(self):
        r = self.row(self.table(), "F1", "critical_wind_speed")
        x_hand = 1.0 * 2.4 * 800.0
        x_cfd = 7.0 * 700.0
        self.assertAlmostEqual(r["hand"], uncertainty.critical_wind_speed(646.16, 1100.0, x_hand, 1.225))
        self.assertAlmostEqual(r["cfd"], uncertainty.critical_wind_speed(646.16, 1100.0, x_cfd, 1.225))
        self.assertAlmostEqual(r["uncertainty"]["grid_pct"], 10.0)
        self.assertIsNone(r["uncertainty"]["validation_pct"])
        self.assertAlmostEqual(r["cfd_low"], uncertainty.critical_wind_speed(646.16, 1100.0, x_cfd * 1.1, 1.225))  # more side force: less wind
        self.assertAlmostEqual(r["cfd_high"], uncertainty.critical_wind_speed(646.16, 1100.0, x_cfd * 0.9, 1.225))
        self.assertLess(r["cfd_low"], r["cfd"])
        self.assertLess(r["cfd"], r["cfd_high"])
        self.assertEqual(r["from_yaw_deg"], 20.0)

    def f2_row(self, **f2):
        hand = {"f2": {"rho": 1.225, "C_d": 0.8, "A_m2": 1.6, **f2}}
        return self.row(uncertainty.table(make_map(rows(cda=1.4), [aero_map.case_row(fake_run(y, dx=0.12, grid_gain=1.1, cda=1.4)) for y in (0.0, 20.0)]), hand, None), "F2", "drag_force")

    def test_f2_without_a_criterion_has_no_verdict(self):
        r = self.f2_row()
        self.assertIsNone(r["verdict_cfd"])
        self.assertIn("基準はまだ無い", r["note"])

    def test_f2_reads_the_load_cases_criterion_at_each_value(self):
        drag = uncertainty.drag_force(1.225, 1.4, 40.0 / 3.6)
        crit = {"quantity": "drag_force", "op": "<=", "unit": "N", "load_case_id": "F2-drag40kmh"}
        far = self.f2_row(criterion={**crit, "limit": 665.0})
        self.assertEqual((far["verdict_hand"], far["verdict_cfd"], far["verdict_low"], far["verdict_high"], far["verdict_changes"]), ("OK", "OK", "OK", "OK", False))
        self.assertIn("確かめていない", far["note"])
        near = self.f2_row(criterion={**crit, "limit": drag * 1.05})  # inside the 10 % band of the grid change: the band's top goes over
        self.assertEqual(near["verdict_cfd"], "OK")
        self.assertTrue(near["verdict_changes"])
        over = self.f2_row(criterion={**crit, "limit": drag * 0.5})
        self.assertEqual(over["verdict_cfd"], "NG")

    def test_f2_goes_as_speed_squared_at_the_load_cases_speed(self):
        same = self.f2_row()
        twice = self.f2_row(speed_ms=2.0 * 40.0 / 3.6)
        self.assertAlmostEqual(twice["cfd"], 4.0 * same["cfd"])
        self.assertAlmostEqual(twice["at_speed_kmh"], 80.0)

    def test_the_verdict_is_read_at_each_value_and_says_when_it_moves(self):
        base = self.table(limit=1.0)
        r = self.row(base, "F1", "critical_wind_speed")
        self.assertEqual((r["verdict_hand"], r["verdict_cfd"], r["verdict_low"], r["verdict_high"]), ("OK", "OK", "OK", "OK"))
        self.assertFalse(r["verdict_changes"])
        # the limit between the flow value and the hand value: hand OK, flow NG
        hand_v, cfd_v = r["hand"], r["cfd"]
        r = self.row(self.table(limit=0.5 * (hand_v + cfd_v)), "F1", "critical_wind_speed")
        self.assertEqual((r["verdict_hand"], r["verdict_cfd"]), ("OK", "NG"))
        self.assertTrue(r["verdict_changes"])
        # the limit inside the band only: flow OK, its low end NG
        r2 = self.row(self.table(limit=0.5 * (r["cfd_low"] + cfd_v)), "F1", "critical_wind_speed")
        self.assertEqual((r2["verdict_hand"], r2["verdict_cfd"], r2["verdict_low"], r2["verdict_high"]), ("OK", "OK", "NG", "OK"))
        self.assertTrue(r2["verdict_changes"])

    def test_f1_says_when_k2s_wind_is_outside_the_yaws_computed(self):
        r = self.row(self.table(limit=30.0), "F1", "critical_wind_speed")
        self.assertEqual(r["criterion_yaw_deg"], 90.0)  # K2's critical_wind_speed is at rest
        self.assertAlmostEqual(r["gust_yaw_deg"], math.degrees(math.atan2(30.0, V)))
        self.assertTrue(r["yaw_outside_sweep"])
        self.assertIn("計算したヨーの外", r["note"])
        self.assertIn("ヨー +20°", r["note"])  # which yaw the one number comes from

    def f1_rows(self, **kw):
        """F1's rows with the points at rest and at 70 deg, C_y A falling with the yaw as a real body's does."""
        sweep = [aero_map.case_row(fake_run(y, cya=7.0)) for y in aero_map.DEFAULT_YAWS_DEG]
        sweep += [aero_map.case_row(fake_run(y, cya=2.5)) for y in (70.0, -70.0)]
        sweep += [aero_map.case_row(fake_run(y, speed=0.0, air=30.0, cya=2.2)) for y in (90.0, -90.0)]
        study = [aero_map.case_row(fake_run(0.0, dx=0.12, grid_gain=1.04)), aero_map.case_row(fake_run(90.0, speed=0.0, air=30.0, cya=2.2, dx=0.12, grid_gain=1.06))]
        hand = {"f1": {**F1_HAND, "criterion": f1_crit(30.0)}}
        return [r for r in uncertainty.table(make_map(sweep, study), hand, **kw)["rows"] if r["test"] == "F1"]

    def test_f1_reads_yaw_90_at_rest_and_shows_the_other_large_yaws(self):
        at_rest, at_70, at_20 = self.f1_rows()
        self.assertEqual([(r["from_yaw_deg"], r["reads_k2_point"]) for r in (at_rest, at_70, at_20)], [(90.0, True), (70.0, False), (20.0, False)])
        self.assertEqual(at_rest["from_speed_kmh"], 0.0)
        self.assertAlmostEqual(at_70["from_speed_kmh"], 40.0)
        self.assertFalse(at_rest["yaw_outside_sweep"])
        self.assertNotIn("計算したヨーの外", at_rest["note"])
        self.assertIn("止まった車", at_rest["note"])
        self.assertIn("ヨー +90°", at_20["note"])  # says which row goes to K2
        self.assertAlmostEqual(at_rest["cfd"], uncertainty.critical_wind_speed(646.16, 1100.0, 2.2 * 700.0, 1.225))
        self.assertAlmostEqual(at_20["cfd"], uncertainty.critical_wind_speed(646.16, 1100.0, 7.0 * 700.0, 1.225))
        self.assertLess(at_20["cfd"], at_70["cfd"])
        self.assertLess(at_70["cfd"], at_rest["cfd"])  # the small yaw's C_y would make the wind look stronger than it is
        self.assertAlmostEqual(at_rest["uncertainty"]["grid_pct"], 6.0)  # the study of that very point
        self.assertIsNone(at_70["uncertainty"]["grid_pct"])  # no study there, so no band there
        self.assertIsNone(at_70["cfd_low"])

    def test_f1_also_says_what_the_lift_does_where_k2_reads(self):
        at_rest, at_70, _ = self.f1_rows()
        # fake_run: Fz = q A 0.1, at yaw 90 q is the q across, so the lift area is 0.17 m^2; moments about the downwind contact line
        want = uncertainty.critical_wind_speed(646.16, 1100.0, 2.2 * 700.0 + AREA * 0.1 * 1100.0 / 2.0, 1.225)
        self.assertAlmostEqual(at_rest["cfd_with_lift"], want)
        self.assertLess(at_rest["cfd_with_lift"], at_rest["cfd"])  # the lift helps the wind tip the vehicle
        self.assertEqual(at_rest["verdict_with_lift"], "OK")
        self.assertIn("揚力", at_rest["note"])
        self.assertNotIn("cfd_with_lift", at_70)

    def test_scaling_a_row_at_rest_goes_with_the_wind(self):
        r = aero_map.case_row(fake_run(90.0, speed=0.0, air=30.0))
        self.assertEqual((r["speed_kmh"], r["air_speed_ms"], r["wind_from"]), (0.0, 30.0, "left"))
        self.assertAlmostEqual(r["wind_across_ms"], 30.0)
        self.assertAlmostEqual(aero_map.scale_to_speed(r, 15.0)["Fy_N"], r["Fy_N"] / 4.0)
        f = aero_map.crosswind_form(r, SIDE)
        self.assertAlmostEqual(0.5 * RHO * f["C_y"] * SIDE * 30.0 ** 2, abs(r["Fy_N"]))
        self.assertAlmostEqual(f["side_cp_height_mm"], 700.0)

    def test_f2_is_the_drag_and_has_no_verdict_yet(self):
        r = self.row(self.table(), "F2", "drag_force")
        self.assertAlmostEqual(r["hand"], 0.5 * 1.225 * 0.8 * 1.6 * V * V)
        self.assertAlmostEqual(r["cfd"], 0.5 * RHO * 1.4 * V * V)
        self.assertAlmostEqual(r["cfd_low"], r["cfd"] * 0.9)
        self.assertAlmostEqual(r["cfd_high"], r["cfd"] * 1.1)
        self.assertIsNone(r["criterion"])
        self.assertIsNone(r["verdict_cfd"])
        self.assertIsNone(r["verdict_changes"])
        self.assertAlmostEqual(r["diff_pct"], (1.4 - 1.28) / 1.28 * 100.0)

    def test_p4_energy_and_range_share_the_criterion(self):
        t = self.table()
        e, rng, end = self.row(t, "P4", "energy_used"), self.row(t, "P4", "range"), self.row(t, "P4", "soc_end")
        h = P4_HAND
        want = uncertainty.loop_energy(h["m_kg"], h["d_km"], h["v_kmh"], h["C_rr"], 1.4, h["H_m"], h["eta"], h["eta_regen"], h["rho"])
        self.assertAlmostEqual(e["cfd"], want)
        self.assertAlmostEqual(rng["cfd"], uncertainty.drive_range(h["d_km"], h["E_batt_kWh"], want))
        self.assertAlmostEqual(end["cfd"], uncertainty.soc_end(want, h["E_batt_kWh"], 100.0))
        self.assertLess(e["cfd_low"], e["cfd_high"])  # sorted by the result, whichever way the drag moves it
        self.assertLess(rng["cfd_low"], rng["cfd_high"])
        self.assertLess(end["cfd_low"], end["cfd_high"])
        self.assertEqual({r["verdict_cfd"] for r in (e, rng, end)}, {end["verdict_cfd"]})
        self.assertEqual(end["criterion"]["quantity"], "soc_end")

    def test_a_band_past_100_percent_has_no_end_on_the_open_side(self):
        r = self.row(self.table(study_gain=2.5), "F1", "critical_wind_speed")  # a change of 150 % on the fine value
        self.assertAlmostEqual(r["uncertainty"]["grid_pct"], 150.0)
        self.assertIsNone(r["cfd_high"])  # x would go below zero: no wind speed to read
        self.assertLess(r["cfd_low"], r["cfd"])

    def test_without_a_grid_study_there_is_no_band(self):
        t = self.table(study_gain=None)
        r = self.row(t, "F1", "critical_wind_speed")
        self.assertIsNone(r["uncertainty"]["total_pct"])
        self.assertIsNone(r["cfd_low"])
        self.assertIsNone(r["verdict_low"])
        self.assertIsNotNone(r["verdict_cfd"])

    def test_the_validation_error_adds_to_the_band(self):
        t = self.table(validation=7.0)
        r = self.row(t, "F2", "drag_force")
        self.assertAlmostEqual(r["uncertainty"]["total_pct"], 17.0)
        self.assertAlmostEqual(r["cfd_high"], r["cfd"] * 1.17)
        self.assertEqual(t["validation_error_pct"], 7.0)
        self.assertIn("基準の物体", t["statement"])
        self.assertIn("確かめていない", self.table()["statement"])

    def test_the_validation_file_gives_the_largest_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "validation.json"
            self.assertIsNone(uncertainty.validation_error_pct(p))  # no file yet
            p.write_text(json.dumps({"cases": [{"name": "sphere", "cd_error_pct": -3.5}, {"name": "cube", "error_pct": 6.25}, {"note": "no number"}]}), encoding="utf-8")
            self.assertEqual(uncertainty.validation_error_pct(p), 6.25)
            p.write_text(json.dumps({"cases": []}), encoding="utf-8")
            self.assertIsNone(uncertainty.validation_error_pct(p))

    def test_a_missing_hand_block_leaves_the_flow_values(self):
        t = uncertainty.table(make_map(rows()), {})
        self.assertEqual([(r["test"], r["quantity"]) for r in t["rows"]], [("F2", "drag_force")])
        self.assertIsNone(t["rows"][0]["hand"])
        self.assertAlmostEqual(t["rows"][0]["cfd"], 0.5 * RHO * 1.4 * V * V)


if __name__ == "__main__":
    unittest.main()

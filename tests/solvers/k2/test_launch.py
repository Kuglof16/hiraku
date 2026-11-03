"""The launch tool's top speed: what holds it (motor power or the motor speed limit), against the K0
formula and the speed limit worked out by hand; and the until param from the analyze call."""

import math
import unittest

from hiraku import contract
from hiraku.solvers import formula_registry
from hiraku.solvers.k2_vehicle.drive import on_board
from hiraku.solvers.k2_vehicle.model import box_design, build_vehicle
from hiraku.solvers.k2_vehicle.shelf import TOOLS
from hiraku.solvers.k2_vehicle.shelf.base import K2Tool, P
from hiraku.solvers.k2_vehicle.shelf.launch import STEADY_GAIN, _loaded_radius
from tests.solvers.k2.slow import SLOW

G = 9.80665


def q(v, u):
    return {"value": v, "unit": u}


C_RR, CD, AREA, ETA = 0.03, 1.0, 2.0, 0.95


def design():
    """A light box on four sprung wheels, rear drive, 20 N m x 8 to the wheels."""
    return box_design(mass=250.0, wheel_mass=10.0, name="launch top speed",
                      variables={"tire_mu": q(1.0, "1"), "rolling_resistance": q(C_RR, "1"),
                                 "motor_torque": q(20.0, "N*m"), "gear_ratio": q(8.0, "1")})


def case(**params):
    base = {"target_speed": q(20.0, "km/h"), "until": "top_speed", "efficiency": q(ETA, "1"), "cd": q(CD, "1"),
            "front_area": q(AREA, "m^2"), "motor_power": q(2.0, "kW"), "max_time": q(60.0, "s")}
    return {"load_case_id": "P3-t", "params": dict(base, **params)}


def run(lc, params=None):
    rec = TOOLS["launch"].run(design(), lc, [{"prediction_id": "pred-0001"}], params or {})
    assert rec["status"] == "ok", rec.get("error")
    assert contract.errors("analysis", dict(rec, analysis_id="ana-0001")) == [], rec
    return rec, {r["quantity"]: r for r in rec["results"]}


def k0_top(power_kw: float) -> float:
    """K0 launch.top_speed (km/h) with the vehicle's mass, as the scripted steps give it."""
    m = build_vehicle(on_board(design(), None)).m_total
    out = formula_registry().evaluate("launch.top_speed", {
        "m": q(m, "kg"), "P": q(power_kw, "kW"), "eta": q(ETA, "1"), "C_rr": q(C_RR, "1"), "C_d": q(CD, "1"),
        "A": q(AREA, "m^2"), "grade": q(0.0, "%"), "rho": q(1.225, "kg/m^3")})
    return out["outputs"]["v_top"]["value"]


def stop_rule_floor(power_kw: float, slip: float) -> float:
    """The lowest speed (km/h) at which K2 may call the speed steady: the balance of eta P / (1 + slip)
    (the slip of the driven wheels takes its share of the power) with the road load plus the force
    of STEADY_GAIN m/s in 1 s on the mass with the wheels' spin inertia."""
    veh = build_vehicle(on_board(design(), None))
    m = veh.m_total
    m_eff = m + sum(c.j_w / _loaded_radius(c) ** 2 for c in veh.corners if c.kind == "wheel")
    pw = ETA * power_kw * 1000.0 / (1.0 + slip)

    def net(v):
        return pw / v - m * G * C_RR - 0.5 * 1.225 * CD * AREA * v * v - m_eff * STEADY_GAIN

    lo, hi = 0.1, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        lo, hi = (mid, hi) if net(mid) > 0 else (lo, mid)
    return lo * 3.6


class Declarations(unittest.TestCase):
    def test_until_says_which_results_it_gives(self):
        desc = {p["name"]: p for p in TOOLS["launch"].describe()["params"]}
        until = desc["until"]
        self.assertEqual(until["choices"], ["target", "top_speed"])
        self.assertTrue(until["also_from_analyze_params"])
        for word in ("speed_at_stop", "top_speed_power", "motor_max_speed", "not the top speed"):
            self.assertIn(word, until["meaning"])
        self.assertTrue(desc["max_time"]["also_from_analyze_params"])
        self.assertNotIn("also_from_analyze_params", desc["target_speed"])

    def test_the_k0_top_speed_pairs_say_which_one_to_compare(self):
        pairs = {p["quantity"]: p for p in TOOLS["launch"].describe()["predicts"]}
        self.assertEqual(pairs["top_speed_power"]["formula_id"], "launch.top_speed")
        self.assertIn("until = top_speed", pairs["top_speed_power"]["note"])
        self.assertIn("motor_max_speed", pairs["top_speed"]["note"])
        self.assertIn("compare top_speed_power", pairs["top_speed"]["note"])


class AnalyzeParams(unittest.TestCase):
    """K2Tool.run on a tool that only echoes the params it read (no simulation)."""

    class Echo(K2Tool):
        name = "echo"
        params = (P("target_speed", "km/h", 40.0, "a load-case param"),
                  P("until", None, "target", "a run option", choices=("target", "top_speed"), run_option=True))

        def _run(self, design, lp):
            return {"results": [{"quantity": "until_top", "value": float(self.text(lp, "until") == "top_speed"),
                                 "unit": "1"},
                                {"quantity": "target", "value": self.q(lp, "target_speed", "m/s") * 3.6,
                                 "unit": "km/h"}]}

    def test_a_run_option_comes_from_the_analyze_call_and_is_written_down(self):
        lc = {"load_case_id": "x", "params": {"target_speed": q(40.0, "km/h")}}
        rec = self.Echo().run(design(), lc, [], {"until": "top_speed"})
        res = {r["quantity"]: r["value"] for r in rec["results"]}
        self.assertEqual(res["until_top"], 1.0)
        self.assertIn("until = top_speed from the analyze call's params (the load case gives target)",
                      rec["assumptions"])
        self.assertNotIn("effects", rec)

    def test_a_load_case_param_in_the_analyze_call_is_named_not_used(self):
        lc = {"load_case_id": "x", "params": {"target_speed": q(40.0, "km/h")}}
        rec = self.Echo().run(design(), lc, [], {"target_speed": q(60.0, "km/h"), "dt": 1e-3})
        res = {r["quantity"]: r["value"] for r in rec["results"]}
        self.assertAlmostEqual(res["target"], 40.0, places=9)
        self.assertEqual(res["until_top"], 0.0)
        self.assertEqual(len(rec["effects"]), 1)
        self.assertIn("analyze-call params this tool does not read: target_speed", rec["effects"][0]["name"])

    def test_a_bad_run_option_fails_the_run(self):
        rec = self.Echo().run(design(), {"load_case_id": "x", "params": {}}, [], {"until": "top"})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("until", rec["error"])


@SLOW
class TopSpeed(unittest.TestCase):
    def test_power_sets_the_top_speed_as_the_k0_balance(self):
        # no motor speed limit: the power against the road load holds the speed
        rec, res = run(case())
        top, power = res["top_speed"], res["top_speed_power"]
        self.assertEqual(top["where"], "motor_power")
        self.assertEqual(power["where"], "motor_power")
        self.assertEqual(top["value"], power["value"])
        v0 = k0_top(2.0)
        self.assertLess(top["value"], v0)  # K2 stops while still gaining a little
        self.assertGreater(top["value"], stop_rule_floor(2.0, 0.02))
        self.assertAlmostEqual(top["value"], v0, delta=0.02 * v0)
        self.assertEqual(res["speed_at_stop"]["where"], "top_speed")
        # more power, a higher top speed, again as the formula
        _, res3 = run(case(motor_power=q(3.0, "kW")))
        v3 = k0_top(3.0)
        self.assertGreater(res3["top_speed"]["value"], top["value"] + 0.5 * (v3 - v0))
        self.assertAlmostEqual(res3["top_speed"]["value"], v3, delta=0.02 * v3)

    def test_the_motor_speed_limit_holds_the_top_speed_whatever_the_power(self):
        # 1800 rpm / 8 = 225 rpm at the wheels, under the power-limited speed
        rpm = 1800.0
        lim = q(rpm, "rpm")
        veh = build_vehicle(on_board(design(), None))
        r = [_loaded_radius(c) for c in veh.corners if c.drive]
        v_rim = rpm / 60.0 * 2.0 * math.pi / 8.0 * sum(r) / len(r) * 3.6
        self.assertLess(v_rim, 0.9 * k0_top(2.0))
        tops = {}
        for kw in (2.0, 3.0):
            rec, res = run(case(motor_power=q(kw, "kW"), motor_max_speed=lim))
            top = res["top_speed"]
            self.assertEqual(top["where"], "motor_max_speed")
            # the wheel rim at the speed limit, less the drive slip (under 2 %)
            self.assertLess(top["value"], v_rim)
            self.assertGreater(top["value"], 0.98 * v_rim)
            self.assertTrue(any("held by the motor speed limit" in a for a in rec["assumptions"]))
            # with the speed limit lifted, the power-limited speed of the case without one
            power = res["top_speed_power"]
            self.assertEqual(power["where"], "motor_power")
            self.assertAlmostEqual(power["value"], k0_top(kw), delta=0.02 * k0_top(kw))
            tops[kw] = top["value"]
        self.assertAlmostEqual(tops[3.0], tops[2.0], delta=0.005 * tops[2.0])  # power does not move it

    def test_until_target_gives_the_speed_at_stop_not_a_top_speed(self):
        for lc, params in ((case(until="target"), {}), (case(until="target"), {"until": "target"})):
            rec, res = run(lc, params)
            self.assertNotIn("top_speed", res)
            self.assertNotIn("top_speed_power", res)
            self.assertEqual(res["speed_at_stop"]["where"], "target_speed")
            self.assertGreater(res["speed_at_stop"]["value"], 20.0)
            self.assertLess(res["speed_at_stop"]["value"], 0.9 * k0_top(2.0))
        # the analyze call's until = top_speed runs on to the top speed (the experiment's call)
        rec, res = run(case(until="target"), {"until": "top_speed"})
        self.assertEqual(res["top_speed"]["where"], "motor_power")
        self.assertIn("until = top_speed from the analyze call's params (the load case gives target)",
                      rec["assumptions"])


if __name__ == "__main__":
    unittest.main()

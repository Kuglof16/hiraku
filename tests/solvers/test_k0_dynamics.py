import math
import unittest

from hiraku import contract
from hiraku.solvers import formula_registry

G = 9.80665


def q(v, u):
    return {"value": v, "unit": u}


def ev(fid, **inputs):
    return formula_registry().evaluate(fid, inputs)


def out(fid, **inputs):
    return {k: v["value"] for k, v in ev(fid, **inputs)["outputs"].items()}


VEH = dict(t=q(1100, "mm"), h=q(593, "mm"), m=q(591, "kg"), m_s=q(503, "kg"), h_s=q(300, "mm"), k=q(20, "N/mm"), n=q(2, "1"))


class TiltTest(unittest.TestCase):
    def test_rigid_known_value(self):
        # t = 1.2 m, h = 0.5 m: atan(0.6/0.5) = 50.19 deg
        o = out("tilt.rigid_angle", t=q(1.2, "m"), h=q(0.5, "m"), e=q(0, "mm"))
        self.assertAlmostEqual(o["theta"], math.degrees(math.atan(1.2)), places=9)
        self.assertAlmostEqual(o["theta"], 50.19, delta=0.01)
        self.assertAlmostEqual(o["ssf"], 1.2, places=12)

    def test_offset_toward_low_side_lowers_the_angle(self):
        a = out("tilt.rigid_angle", t=q(1100, "mm"), h=q(593, "mm"), e=q(0, "mm"))["theta"]
        b = out("tilt.rigid_angle", t=q(1100, "mm"), h=q(593, "mm"), e=q(50, "mm"))["theta"]
        self.assertLess(b, a)

    def test_spring_roll_tends_to_rigid_when_stiff(self):
        rigid = out("tilt.rigid_angle", t=q(1100, "mm"), h=q(593, "mm"), e=q(0, "mm"))["theta"]
        stiff = out("tilt.spring_roll_angle", **dict(VEH, k=q(1e7, "N/mm")), e=q(0, "mm"))["theta"]
        soft = out("tilt.spring_roll_angle", **VEH, e=q(0, "mm"))
        self.assertAlmostEqual(stiff, rigid, delta=1e-4)
        self.assertLess(soft["theta"], rigid)
        self.assertGreater(soft["roll"], 0)
        # roll equilibrium: K phi = m_s g h_s sin(theta + phi), K = n k t^2 / 2 (N*mm)
        K = 2 * 20 * 1100**2 / 2
        th, ph = math.radians(soft["theta"]), math.radians(soft["roll"])
        self.assertAlmostEqual(K * ph, 503 * G * 300 * math.sin(th + ph), delta=K * ph * 1e-9)

    def test_spring_roll_too_soft_is_refused(self):
        with self.assertRaisesRegex(ValueError, "too soft"):
            ev("tilt.spring_roll_angle", **dict(VEH, k=q(0.1, "N/mm")), e=q(0, "mm"))

    def test_slide(self):
        self.assertAlmostEqual(out("tilt.slide_angle", mu=q(1, "1"))["theta"], 45.0, places=12)

    def test_side_slope_uphill_load(self):
        args = dict(m=q(600, "kg"), t=q(1100, "mm"), h=q(500, "mm"), e=q(0, "mm"))
        flat = out("tilt.side_slope_uphill_load", **args, beta=q(0, "deg"))["W_up"]
        self.assertAlmostEqual(flat, 600 * G / 2, places=9)
        # at the rigid tip angle the uphill wheels carry nothing
        tip = out("tilt.rigid_angle", t=q(1100, "mm"), h=q(500, "mm"), e=q(0, "mm"))["theta"]
        self.assertAlmostEqual(out("tilt.side_slope_uphill_load", **args, beta=q(tip, "deg"))["W_up"], 0.0, delta=1e-9)
        # lateral acceleration tan(beta) on level ground carries the slope's normal load too: 1/cos(beta) more
        up = out("tilt.side_slope_uphill_load", **args, beta=q(15, "deg"))["W_up"]
        inner = out("lane_change.inner_wheel_load", m=q(600, "kg"), a_y=q(math.tan(math.radians(15)), "g"), h=q(500, "mm"),
                    t=q(1100, "mm"))["W_inner"]
        self.assertAlmostEqual(inner / up, 1 / math.cos(math.radians(15)), places=9)


class CrosswindTest(unittest.TestCase):
    def test_side_force(self):
        # 1/2 * 1.225 * 1 * 1.5 m^2 * (20 m/s)^2 = 367.5 N
        self.assertAlmostEqual(out("crosswind.side_force", C_y=q(1, "1"), A=q(1.5, "m^2"), v=q(20, "m/s"), rho=q(1.225, "kg/m^3"))["F"], 367.5, places=9)

    def test_critical_speed_empties_the_windward_wheels(self):
        v = out("crosswind.critical_wind_speed", m=q(591, "kg"), t=q(1100, "mm"), C_y=q(1, "1"), A=q(1.5, "m^2"), h_cp=q(900, "mm"), rho=q(1.225, "kg/m^3"))["v_crit"]
        F = out("crosswind.side_force", C_y=q(1, "1"), A=q(1.5, "m^2"), v=q(v, "m/s"), rho=q(1.225, "kg/m^3"))["F"]
        W = out("crosswind.windward_wheel_load", m=q(591, "kg"), t=q(1100, "mm"), F=q(F, "N"), h_cp=q(900, "mm"))["W_windward"]
        self.assertAlmostEqual(W, 0.0, delta=1e-6)


class LaneChangeTest(unittest.TestCase):
    def test_inner_wheel_load_reaches_zero_at_the_threshold(self):
        o = out("lane_change.inner_wheel_load", m=q(591, "kg"), a_y=q(0.5, "g"), h=q(593, "mm"), t=q(1100, "mm"))
        self.assertAlmostEqual(o["a_y_lift"], 1100 / (2 * 593), places=12)
        o2 = out("lane_change.inner_wheel_load", m=q(591, "kg"), a_y=q(o["a_y_lift"], "g"), h=q(593, "mm"), t=q(1100, "mm"))
        self.assertAlmostEqual(o2["W_inner"], 0.0, delta=1e-9)
        o3 = out("lane_change.inner_wheel_load", m=q(591, "kg"), a_y=q(0.5 * G, "m/s^2"), h=q(593, "mm"), t=q(1100, "mm"))
        self.assertAlmostEqual(o3["W_inner"], o["W_inner"], places=9)

    def test_rollover_threshold(self):
        stiff = out("lane_change.rollover_threshold", **dict(VEH, k=q(1e9, "N/mm")))["a_y_lift"]
        self.assertAlmostEqual(stiff, 1100 / (2 * 593), delta=1e-6)
        soft = out("lane_change.rollover_threshold", **VEH)
        self.assertLess(soft["a_y_lift"], stiff)

    def test_peak_lateral_acceleration(self):
        # v^2 * Y/2 * (pi/X)^2 with v = 10 m/s, Y = 2 m, X = 20 m: 100 * 1 * 0.02467 = 2.467 m/s^2
        o = out("lane_change.peak_lateral_acceleration", v=q(36, "km/h"), Y=q(2000, "mm"), X=q(20000, "mm"))
        self.assertAlmostEqual(o["a_y_peak"] * G, 100 * 1 * (math.pi / 20) ** 2, places=9)

    def test_neutral_steer_is_ackermann(self):
        o = out("lane_change.steady_steer", L=q(1700, "mm"), R=q(15, "m"), W_f=q(2000, "N"), W_r=q(3000, "N"),
                C_f=q(40000, "N/rad"), C_r=q(60000, "N/rad"), v=q(20, "km/h"))
        self.assertAlmostEqual(o["K"], 0.0, places=12)
        self.assertAlmostEqual(o["delta"], math.degrees(1.7 / 15), places=9)
        o = out("lane_change.steady_steer", L=q(1700, "mm"), R=q(15, "m"), W_f=q(2400, "N"), W_r=q(3000, "N"),
                C_f=q(40000, "N/rad"), C_r=q(60000, "N/rad"), v=q(20, "km/h"))
        self.assertGreater(o["K"], 0)  # more load on the front for the same tyres: understeer


class BrakingTest(unittest.TestCase):
    def test_stopping_distance(self):
        # 40 km/h, mu 0.7: (11.11)^2 / (2 * 0.7 * 9.80665) = 8.99 m
        r = ev("braking.stopping_distance", v=q(40, "km/h"), mu=q(0.7, "1"), t_r=q(0, "s"))
        self.assertAlmostEqual(r["outputs"]["s"]["value"], 8.99, delta=0.005)
        r = ev("braking.stopping_distance", v=q(36, "km/h"), mu=q(0.5, "1"), t_r=q(1, "s"))
        self.assertAlmostEqual(r["outputs"]["s"]["value"], 10 + 100 / (2 * 0.5 * G), places=9)
        self.assertAlmostEqual(sum(t["value"] for t in r["terms"]), r["outputs"]["s"]["value"], places=9)

    def test_load_transfer(self):
        o = out("braking.load_transfer", m=q(591, "kg"), a=q(1014, "mm"), L=q(1700, "mm"), h=q(593, "mm"), a_x=q(0.7, "g"))
        self.assertAlmostEqual(o["W_front"] + o["W_rear"], 591 * G, places=6)
        self.assertAlmostEqual(o["a_x_tip"], 1014 / 593, places=12)
        o = out("braking.load_transfer", m=q(591, "kg"), a=q(1014, "mm"), L=q(1700, "mm"), h=q(593, "mm"), a_x=q(1014 / 593, "g"))
        self.assertAlmostEqual(o["W_rear"], 0.0, delta=1e-6)

    def test_parking_friction(self):
        a, L, h, grade = 900.0, 1700.0, 500.0, 20.0
        o = out("braking.parking_friction", a=q(a, "mm"), L=q(L, "mm"), h=q(h, "mm"), grade=q(grade, "%"))
        self.assertAlmostEqual(o["mu_all"], 0.2, places=12)
        # independent check: axle loads from the moments about each contact, per unit weight
        th = math.atan(grade / 100)
        n_rear = (a * math.cos(th) + h * math.sin(th)) / L
        n_front = math.cos(th) - n_rear
        self.assertAlmostEqual(o["mu_rear"], math.sin(th) / n_rear, places=12)
        self.assertAlmostEqual(o["mu_front"], math.sin(th) / n_front, places=12)
        self.assertGreater(o["mu_front"], o["mu_rear"])  # facing uphill the load moves to the rear
        flat = out("braking.parking_friction", a=q(a, "mm"), L=q(L, "mm"), h=q(h, "mm"), grade=q(0, "%"))
        self.assertEqual((flat["mu_all"], flat["mu_rear"], flat["mu_front"]), (0.0, 0.0, 0.0))
        with self.assertRaisesRegex(ValueError, "tips backward"):
            ev("braking.parking_friction", a=q(1600, "mm"), L=q(L, "mm"), h=q(h, "mm"), grade=q(100, "%"))


class LaunchTest(unittest.TestCase):
    BASE = dict(m=q(600, "kg"), F_max=q(1200, "N"), P=q(1000, "kW"), eta=q(1, "1"), C_rr=q(0, "1"), C_d=q(0, "1"),
                A=q(1, "m^2"), grade=q(0, "%"), **{"lambda": q(1, "1")}, v=q(36, "km/h"), rho=q(1.225, "kg/m^3"))

    def test_constant_force_without_resistance(self):
        # v = F t / m: 10 m/s * 600 kg / 1200 N = 5 s, s = v t / 2 = 25 m
        r = ev("launch.time_to_speed", **self.BASE)
        self.assertAlmostEqual(r["outputs"]["t"]["value"], 5.0, places=6)
        self.assertAlmostEqual(r["outputs"]["s"]["value"], 25.0, places=6)
        e = {t["name"]: t.get("share") for t in r["terms"]}
        self.assertAlmostEqual(e["m"], 1.0, places=5)
        self.assertAlmostEqual(e["F_max"], -1.0, places=5)

    def test_power_limit_and_top_speed(self):
        o = out("launch.time_to_speed", **dict(self.BASE, P=q(6, "kW"), C_rr=q(0.03, "1"), C_d=q(0.8, "1"), A=q(1.6, "m^2")))
        self.assertGreater(o["t"], 5.0)
        top = out("launch.top_speed", m=q(600, "kg"), P=q(6, "kW"), eta=q(1, "1"), C_rr=q(0.03, "1"), C_d=q(0.8, "1"),
                  A=q(1.6, "m^2"), grade=q(0, "%"), rho=q(1.225, "kg/m^3"))
        # at top speed eta P / v equals the road load
        v = top["v_top"] / 3.6
        self.assertAlmostEqual(6000 / v, 600 * G * 0.03 + 0.5 * 1.225 * 0.8 * 1.6 * v * v, delta=1e-6)
        with self.assertRaisesRegex(ValueError, "drag area"):
            ev("launch.top_speed", m=q(600, "kg"), P=q(6, "kW"), eta=q(1, "1"), C_rr=q(0.03, "1"), C_d=q(0, "1"),
               A=q(1.6, "m^2"), grade=q(0, "%"), rho=q(1.225, "kg/m^3"))

    def test_unreachable_speed(self):
        with self.assertRaisesRegex(ValueError, "top speed"):
            ev("launch.time_to_speed", **dict(self.BASE, P=q(1, "kW"), C_rr=q(0.03, "1")))


class EnergyAndHeatTest(unittest.TestCase):
    def test_loop_energy(self):
        r = ev("drive_energy.loop_energy", m=q(600, "kg"), d=q(10, "km"), v=q(30, "km/h"), C_rr=q(0.02, "1"),
               C_d=q(0, "1"), A=q(1, "m^2"), H=q(0, "m"), eta=q(1, "1"), eta_regen=q(0, "1"), E_batt=q(5, "kWh"), rho=q(1.225, "kg/m^3"))
        self.assertAlmostEqual(r["outputs"]["E"]["value"], 600 * G * 0.02 * 10000 / 3600, places=9)
        r = ev("drive_energy.loop_energy", m=q(600, "kg"), d=q(10, "km"), v=q(30, "km/h"), C_rr=q(0.02, "1"),
               C_d=q(0.8, "1"), A=q(1.6, "m^2"), H=q(300, "m"), eta=q(0.85, "1"), eta_regen=q(0.3, "1"), E_batt=q(5, "kWh"), rho=q(1.225, "kg/m^3"))
        self.assertAlmostEqual(sum(t["value"] for t in r["terms"]), r["outputs"]["E"]["value"], places=9)

    def test_steady_power(self):
        o = out("drive_energy.steady_power", m=q(600, "kg"), v=q(36, "km/h"), grade=q(0, "%"), C_rr=q(0.02, "1"),
                C_d=q(0, "1"), A=q(1, "m^2"), eta=q(0.5, "1"), rho=q(1.225, "kg/m^3"))
        self.assertAlmostEqual(o["P"], 600 * G * 0.02 * 10 / 0.5 / 1000, places=9)

    def test_rc_step(self):
        # P 100 W, R 0.5 K/W, tau 1000 s: dT(tau) = 50 (1 - 1/e) = 31.6 K, dT(inf) = 50 K
        o = out("thermal.rc_step", P=q(100, "W"), R=q(0.5, "K/W"), tau=q(1000, "s"), t=q(1000, "s"), T_amb=q(40, "degC"))
        self.assertAlmostEqual(o["dT"], 50 * (1 - math.exp(-1)), places=9)
        self.assertAlmostEqual(o["T"], 40 + o["dT"], places=9)
        o = out("thermal.steady_temperature", P=q(100, "W"), R=q(0.5, "K/W"), T_amb=q(313.15, "K"))
        self.assertAlmostEqual(o["T"], 90.0, places=9)
        self.assertAlmostEqual(out("thermal.loss_from_efficiency", P_out=q(4, "kW"), eta=q(0.8, "1"))["P_loss"], 1000.0, places=9)


class AllFormulasTest(unittest.TestCase):
    def test_every_formula_fits_the_contract(self):
        for f in formula_registry().list_formulas():
            contract.validate("formula", f)
            self.assertTrue(f["assumptions"] and f["source"], f["formula_id"])


if __name__ == "__main__":
    unittest.main()

import math
import unittest

from hiraku import contract
from hiraku.contract.protocols import FormulaRegistry
from hiraku.solvers import formula_registry


def q(v, u):
    return {"value": v, "unit": u}


class FormulaLedgerTest(unittest.TestCase):
    def setUp(self):
        self.R = formula_registry()

    def test_protocol_and_contract_shape(self):
        for name in ("list_formulas", "evaluate"):  # FormulaRegistry is not runtime_checkable
            self.assertTrue(callable(getattr(self.R, name)), name)
            self.assertTrue(hasattr(FormulaRegistry, name))
        ids = set()
        for f in self.R.list_formulas():
            contract.validate("formula", f)
            self.assertTrue(f["source"].strip(), f["formula_id"])
            self.assertTrue(f["assumptions"], f["formula_id"])
            ids.add(f["formula_id"])
        for needed in ("step_climb.wheel_force_rigid", "step_climb.self_climb", "mass_props.front_wheel_load",
                       "mass_props.static_wheel_loads", "frame_static.rail_bending",
                       "frame_static.simple_beam_center_load", "frame_static.cantilever_tip_load",
                       "frame_static.bending_stress"):
            self.assertIn(needed, ids)

    def test_names_other_departments_call(self):
        f = {x["formula_id"]: x for x in self.R.list_formulas()}
        self.assertEqual(set(f["step_climb.wheel_force_rigid"]["inputs"]), {"r", "h", "W"})
        self.assertEqual(set(f["step_climb.wheel_force_rigid"]["outputs"]), {"F", "d"})
        self.assertEqual(set(f["mass_props.front_wheel_load"]["inputs"]), {"m", "a", "L", "n"})
        self.assertEqual(set(f["mass_props.front_wheel_load"]["outputs"]), {"W"})
        self.assertEqual(set(f["frame_static.rail_bending"]["inputs"]), {"F", "L", "b", "h", "t"})
        self.assertIn("sigma", f["frame_static.rail_bending"]["outputs"])

    def test_evaluate_returns_contract_quantities_and_terms(self):
        out = self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(100, "mm"), "W": q(1000, "N")})
        self.assertEqual(set(out), {"outputs", "terms", "expression"})
        for v in out["outputs"].values():
            contract.validate("quantity", v)
        for t in out["terms"]:
            contract.validate("term", t)

    def test_step_climb_known_values(self):
        # F/W = sqrt(2 r h - h^2) / (r - h): r = 0.3 m gives 1.118 (h 0.10), 1.732 (h 0.15), 5.92 (h 0.25)
        for h, ratio in ((100, 1.1180), (150, 1.7321), (250, 5.9161)):
            a = self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(0.3, "m"), "h": q(h / 1000, "m"), "W": q(1, "kN")})
            b = self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(h, "mm"), "W": q(1000, "N")})
            self.assertAlmostEqual(a["outputs"]["F"]["value"] / 1000, ratio, delta=1e-4)
            self.assertAlmostEqual(a["outputs"]["F"]["value"], b["outputs"]["F"]["value"], places=9)
            self.assertAlmostEqual(a["outputs"]["d"]["value"], b["outputs"]["d"]["value"], places=9)
        out = self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(100, "mm"), "W": q(1000, "N")})
        self.assertAlmostEqual(out["outputs"]["d"]["value"], math.sqrt(50000.0), places=9)
        out = self.R.evaluate("step_climb.self_climb", {"r": q(300, "mm"), "h": q(100, "mm"), "W": q(1000, "N")})
        self.assertAlmostEqual(out["outputs"]["tau"]["value"], math.sqrt(50000.0), places=9)  # 1000 N * 0.2236 m
        self.assertAlmostEqual(out["outputs"]["mu"]["value"], math.sqrt(50000.0) / 200.0, places=12)

    def test_elasticities_from_sympy(self):
        out = self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(100, "mm"), "W": q(1000, "N")})
        e = {t["name"]: t["share"] for t in out["terms"]}
        # d ln F / d ln r = r h / (2 r h - h^2) - r / (r - h) = 0.6 - 1.5
        self.assertAlmostEqual(e["r"], -0.9, places=12)
        self.assertAlmostEqual(e["h"], 0.9, places=12)
        self.assertAlmostEqual(e["W"], 1.0, places=12)

    def test_step_as_high_as_the_wheel_is_refused(self):
        for h in (300, 350):
            with self.assertRaisesRegex(ValueError, "below the wheel radius"):
                self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(h, "mm"), "W": q(1000, "N")})

    def test_below_minimum_names_input(self):
        with self.assertRaisesRegex(ValueError, "input 'h'.*minimum"):
            self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(-1, "mm"), "W": q(1000, "N")})

    def test_wrong_dimension_names_input(self):
        with self.assertRaisesRegex(ValueError, "input 'W'"):
            self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(100, "mm"), "W": q(100, "kg")})

    def test_missing_and_unknown_inputs(self):
        with self.assertRaisesRegex(ValueError, "missing input"):
            self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(100, "mm")})
        with self.assertRaisesRegex(ValueError, "unknown input"):
            self.R.evaluate("step_climb.wheel_force_rigid", {"r": q(300, "mm"), "h": q(100, "mm"), "W": q(1, "N"), "x": q(1, "mm")})
        with self.assertRaisesRegex(ValueError, "unknown formula"):
            self.R.evaluate("no.such", {})

    def test_static_wheel_loads(self):
        m, a, L, t, e = 600.0, 1000.0, 1700.0, 1100.0, 50.0
        out = self.R.evaluate("mass_props.static_wheel_loads",
                              {"m": q(m, "kg"), "a": q(a, "mm"), "L": q(L, "mm"), "t": q(t, "mm"), "e": q(e, "mm")})["outputs"]
        g = 9.80665
        self.assertAlmostEqual(out["axle_load_front"]["value"], m * g * (L - a) / L, places=9)
        total = sum(out[f"wheel_load_{w}"]["value"] for w in ("FL", "FR", "RL", "RR"))
        self.assertAlmostEqual(total, m * g, places=9)
        self.assertGreater(out["wheel_load_FL"]["value"], out["wheel_load_FR"]["value"])  # CG to the left
        W = self.R.evaluate("mass_props.front_wheel_load",
                            {"m": q(m, "kg"), "a": q(a, "mm"), "L": q(L, "mm"), "n": q(2, "1")})["outputs"]["W"]["value"]
        self.assertAlmostEqual(W, m * g * (L - a) / L / 2, places=9)
        with self.assertRaisesRegex(ValueError, "between the axles"):
            self.R.evaluate("mass_props.static_wheel_loads",
                            {"m": q(m, "kg"), "a": q(2000, "mm"), "L": q(L, "mm"), "t": q(t, "mm"), "e": q(0, "mm")})

    def test_beam_formulas_against_hand_numbers(self):
        # 40 x 80 x 2 tube: I = (40*80^3 - 36*76^3) / 12 = (20,480,000 - 15,803,136) / 12 = 389,738.67 mm^4
        I = (40 * 80**3 - 36 * 76**3) / 12
        self.assertAlmostEqual(I, 4676864 / 12, places=6)
        sec = {"b": q(40, "mm"), "h": q(80, "mm"), "t": q(2, "mm")}
        out = self.R.evaluate("frame_static.cantilever_tip_load", {"P": q(1000, "N"), "L": q(1, "m"), "E": q(205000, "MPa"), **sec})["outputs"]
        self.assertAlmostEqual(out["max_displacement"]["value"], 1000 * 1000**3 / (3 * 205000 * I), places=9)
        self.assertAlmostEqual(out["max_stress"]["value"], 1000 * 1000 * 40 / I, places=9)
        out = self.R.evaluate("frame_static.simple_beam_center_load", {"P": q(1000, "N"), "L": q(2000, "mm"), "E": q(205000, "MPa"), **sec})["outputs"]
        self.assertAlmostEqual(out["max_displacement"]["value"], 1000 * 2000**3 / (48 * 205000 * I), places=9)
        # point load at mid-span equals the centre-load formula
        mid = self.R.evaluate("frame_static.simple_beam_point_load", {"P": q(1000, "N"), "L": q(2000, "mm"), "a": q(1000, "mm"), "E": q(205000, "MPa"), **sec})["outputs"]
        self.assertAlmostEqual(mid["max_displacement"]["value"], out["max_displacement"]["value"], places=9)
        self.assertAlmostEqual(mid["max_stress"]["value"], out["max_stress"]["value"], places=9)
        rb = self.R.evaluate("frame_static.rail_bending", {"F": q(1000, "N"), "L": q(2000, "mm"), **sec})["outputs"]
        self.assertAlmostEqual(rb["sigma"]["value"], out["max_stress"]["value"], places=9)
        self.assertAlmostEqual(rb["Z"]["value"], I / 40, places=6)
        self.assertEqual(rb["Z"]["unit"], "mm^3")
        udl = self.R.evaluate("frame_static.simple_beam_udl", {"w": q(1, "N/mm"), "L": q(2000, "mm"), "E": q(205000, "MPa"), **sec})["outputs"]
        self.assertAlmostEqual(udl["max_displacement"]["value"], 5 * 2000**4 / (384 * 205000 * I), places=9)
        with self.assertRaisesRegex(ValueError, "wall thickness"):
            self.R.evaluate("frame_static.bending_stress", {"M": q(1, "N*mm"), "b": q(40, "mm"), "h": q(80, "mm"), "t": q(25, "mm")})

    def test_rail_joint_stress(self):
        import json
        from pathlib import Path

        from hiraku.solvers.k0_handcalc.frame_inputs import overhang_moment, rail_joint_inputs

        from hiraku.designgen import ladder_4w

        from hiraku.designgen.kit.joints import skipped

        # the ladder alone: the roll bar (on by default) splits the rail S2B at its node H; without the joints'
        # struts (kit/joints.py), whose few grams on the rear nodes the hand sum below does not count
        with skipped():
            design = ladder_4w.build({"rollbar": {"value": 0.0, "unit": "1"}})
        ji = rail_joint_inputs(design, loading="two_cargo")
        self.assertEqual((ji["rail_member"][-3:], ji["cross_member"]), ("S2B", "cross_B"))
        ins = {k: v["value"] for k, v in ji["inputs"].items()}
        self.assertEqual((ins["z"], ins["e"], ins["L1"], ins["L_c"]), (380.0, 200.0, 700.0, 700.0))
        # overhang by hand: the deck and cargo 150 kg over 6 bed nodes (one at the rear end, 400 mm behind),
        # half of cross_E at that end, and the 400 mm rail piece behind the axle at 200 mm
        g = 9.80665
        rail_w = 7850e-9 * (40 * 80 - 35.4 * 75.4) * g
        cross_m = 7850e-9 * (40 * 40 - 35.4 * 35.4) * 700
        deck = next(p["mass_kg"] for p in design["parts"] if p["id"] == "deck")  # between the rear towers: narrower
        by_hand = (deck / 6 + 150 / 6) * g * 400 + cross_m / 2 * g * 400 + rail_w * 400 * 200
        # the rear tower leans back 2 mm (its top LBm at x - 2: kit/mounts, a3699a13): overhang_moment counts the post
        # (both ends on the rail's line) over its 2 mm behind the axle at 1 mm, and half of the arm LBm-LBd (one end on
        # the line) at LBm, 2 mm behind
        tower_w = 7850e-9 * (30 * 30 - 25.4 * 25.4) * g  # 30 x 30 x 2.3, N/mm
        by_hand += tower_w * 2 * 1 + tower_w * math.hypot(2.0, 78.8) / 2 * 2
        self.assertAlmostEqual(ins["M_oh"], by_hand, places=6)
        self.assertEqual(overhang_moment(design, "LA") > ins["M_oh"], True)  # more lies behind the front axle
        # with no offset and no overhang the formula is the traction bending plus the push
        closed = ("a", "L_e", "b_e", "h_e", "t_e")  # rail_joint_stress_closed's: the rail behind to the end cross member
        self.assertEqual({k: ins[k] for k in closed}, {"a": 400.0, "L_e": 700.0, "b_e": 40.0, "h_e": 40.0, "t_e": 2.3})
        base = dict({k: v for k, v in ji["inputs"].items() if k not in closed}, F=q(1000, "N"), W=q(2000, "N"))
        o = self.R.evaluate("frame_static.rail_joint_stress", dict(base, M_oh=q(0, "N*mm"), e=q(0, "mm")))["outputs"]
        I = (40 * 80**3 - 35.4 * 75.4**3) / 12
        A = 40 * 80 - 35.4 * 75.4
        self.assertAlmostEqual(o["sigma"]["value"], 1000 * 380 * 40 / I + 1000 / A, places=9)
        self.assertEqual(o["tau_torsion"]["value"], 0.0)
        # the joint share: torsion G J / L1 against 2 E I_c / L_c
        o = self.R.evaluate("frame_static.rail_joint_stress", base)["outputs"]
        Am = 37.7 * 77.7
        J = 4 * Am**2 * 2.3 / (2 * (37.7 + 77.7))
        Ic = (40 * 40**3 - 35.4 * 35.4**3) / 12
        kt, kc = 205000 / 2.6 * J / 700, 2 * 205000 * Ic / 700
        self.assertAlmostEqual(o["T_rail"]["value"], 2000 * 200 * kt / (kt + kc), places=6)

    def test_rail_step_bending_limits(self):
        sec = {"b": q(40, "mm"), "h": q(80, "mm"), "t": q(2.3, "mm")}
        I = (40 * 80**3 - 35.4 * 75.4**3) / 12
        A = 40 * 80 - 35.4 * 75.4
        # no push: half the weight as a uniform load, largest at mid-span: (W/2) L / 8
        o = self.R.evaluate("frame_static.rail_step_bending", {"W": q(4000, "N"), "L": q(1700, "mm"), "F": q(0, "N"),
                                                                "z": q(380, "mm"), "r": q(300, "mm"), **sec})["outputs"]
        self.assertAlmostEqual(o["sigma"]["value"], 2000 * 1700 / 8 * 40 / I, places=9)
        self.assertAlmostEqual(o["x_max"]["value"], 850.0, places=6)
        # no weight: the traction moment F z at the rear axle plus the push as compression
        o = self.R.evaluate("frame_static.rail_step_bending", {"W": q(0, "N"), "L": q(1700, "mm"), "F": q(1000, "N"),
                                                                "z": q(380, "mm"), "r": q(300, "mm"), **sec})["outputs"]
        self.assertAlmostEqual(o["sigma"]["value"], 1000 * 380 * 40 / I + 1000 / A, places=9)
        self.assertAlmostEqual(o["M_rear"]["value"], 380000.0, places=6)
        self.assertEqual(o["x_max"]["value"], 1700.0)


if __name__ == "__main__":
    unittest.main()

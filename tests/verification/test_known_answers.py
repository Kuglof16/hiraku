"""The closed forms in qa.known_answers, checked by a second, independent route.

A typo in an expected value would make every solver test wrong, so each formula
is re-derived here: beams by solving E I w'''' = q symbolically, frames with the
qa.frame2d stiffness solver, frequencies as eigenvalues, the step by moving the
wheel numerically. The rounded values asserted below are what the closed forms
give for these cases, recomputed here, never copied in as results.
"""

import math
import unittest

import numpy as np
import sympy as sp

from hiraku import contract
from qa import known_answers as ka
from qa.frame2d import Frame2D

E, I, L = ka.BEAM["E"], ka.BEAM["I"], ka.BEAM["L"]


def _beam_ode(q, bcs):
    """Solve E I w'''' = q with four boundary conditions bc(w, x, EI) = 0; return w(x)."""
    x = sp.symbols("x")
    c = sp.symbols("c0:4")
    EI = sp.Symbol("EI", positive=True)
    w = sum(ci * x**i for i, ci in enumerate(c)) + q * x**4 / (24 * EI)
    eqs = [bc(w, x, EI) for bc in bcs]
    sol = sp.solve(eqs, c, dict=True)[0]
    return sp.simplify(w.subs(sol)), x, EI


class StepClimb(unittest.TestCase):
    def test_corner_distance_is_where_the_circle_meets_the_step(self):
        for c in ka.STEP_CASES:
            with self.subTest(case=c["case"]):
                r, h = c["r"], c["h"]
                # wheel centre at (0, r); step face x = d, corner height h on the circle
                d = ka.step_corner_distance(r, h)
                self.assertAlmostEqual(math.hypot(d, h - r), r, places=9)

    def test_force_ratio_by_virtual_work(self):
        # Rotate the wheel about the corner by a small angle: F dx = W dz.
        for c in ka.STEP_CASES:
            with self.subTest(case=c["case"]):
                r, h = c["r"], c["h"]
                d = ka.step_corner_distance(r, h)
                phi0 = math.atan2(r - h, -d)  # angle of the centre seen from the corner
                eps = 1e-7
                cx = lambda p: d + r * math.cos(p)
                cz = lambda p: h + r * math.sin(p)
                dx = cx(phi0 - eps) - cx(phi0 + eps)
                dz = cz(phi0 - eps) - cz(phi0 + eps)
                self.assertAlmostEqual(abs(dz / dx), ka.step_force_ratio(r, h), places=6)

    def test_rounded_force_ratios_of_the_step_cases(self):
        # r = 300 mm at three heights, then r = 75 mm: the closed form rounded to 3 decimals
        got = [round(ka.step_force_ratio(c["r"], c["h"]), 3) for c in ka.STEP_CASES]
        self.assertEqual(got, [1.118, 1.732, 5.916, 0.927])

    def test_step_not_below_radius_is_refused(self):
        with self.assertRaises(ValueError):
            ka.step_force_ratio(300.0, 300.0)


class WheelLoads(unittest.TestCase):
    def test_lever_rule(self):
        front, rear = ka.axle_loads(5000.0, 1800.0, 800.0)
        self.assertAlmostEqual(front + rear, 5000.0, places=9)
        self.assertAlmostEqual(rear * 1800.0, 5000.0 * 800.0, places=6)  # moments about the front axle

    def test_centred_load_is_left_right_identical(self):
        w = ka.wheel_loads_equal_rates(5000.0, 1800.0, 1300.0, 800.0)
        self.assertEqual(w["FL"], w["FR"])
        self.assertEqual(w["RL"], w["RR"])

    def test_equal_springs_by_stiffness_method(self):
        # Rigid body on four equal springs: solve for heave and two rotations.
        W, Lw, t, a, y = 5000.0, 1800.0, 1300.0, 800.0, 120.0
        corners = {"FL": (0.0, t / 2), "FR": (0.0, -t / 2), "RL": (-Lw, t / 2), "RR": (-Lw, -t / 2)}
        k = 30.0
        # load W at (x = -a, y); unknowns z0, ax, ay with z = z0 + ax*x + ay*y
        B = np.array([[1.0, x, yy] for x, yy in corners.values()])
        K = k * B.T @ B
        f = W * np.array([1.0, -a, y])
        z = B @ np.linalg.solve(K, f)
        expected = ka.wheel_loads_equal_rates(W, Lw, t, a, y)
        for (name, _), zi in zip(corners.items(), z):
            self.assertAlmostEqual(k * zi, expected[name], places=6)


class Beams(unittest.TestCase):
    def test_cantilever_tip_load_from_the_ode(self):
        P = sp.Symbol("P")
        w, x, EI = _beam_ode(0, [
            lambda w, x, EI: w.subs(x, 0),
            lambda w, x, EI: sp.diff(w, x).subs(x, 0),
            lambda w, x, EI: sp.diff(w, x, 2).subs(x, L),
            lambda w, x, EI: EI * sp.diff(w, x, 3).subs(x, L) + P,
        ])
        tip = float(abs(w.subs({x: L, P: 100.0, EI: E * I})))
        self.assertAlmostEqual(tip, ka.cantilever_tip_load(100.0, L, E, I)["deflection"], places=9)
        self.assertEqual(round(tip, 3), 4.831)  # mm, the closed form P L^3 / (3 E I) rounded

    def test_simply_supported_centre_load_from_the_ode(self):
        P = sp.Symbol("P")
        half = L / 2
        w, x, EI = _beam_ode(0, [
            lambda w, x, EI: w.subs(x, 0),
            lambda w, x, EI: sp.diff(w, x, 2).subs(x, 0),
            lambda w, x, EI: sp.diff(w, x).subs(x, half),
            lambda w, x, EI: EI * sp.diff(w, x, 3).subs(x, half) + P / 2,
        ])
        mid = float(abs(w.subs({x: half, P: 349.0, EI: E * I})))
        self.assertAlmostEqual(mid, ka.simply_supported_centre_load(349.0, L, E, I)["deflection"], places=9)

    def test_simply_supported_uniform_load_from_the_ode(self):
        q = 0.8
        w, x, EI = _beam_ode(q, [
            lambda w, x, EI: w.subs(x, 0),
            lambda w, x, EI: w.subs(x, L),
            lambda w, x, EI: sp.diff(w, x, 2).subs(x, 0),
            lambda w, x, EI: sp.diff(w, x, 2).subs(x, L),
        ])
        mid = float(abs(w.subs({x: L / 2, EI: E * I})))
        self.assertAlmostEqual(mid, ka.simply_supported_uniform_load(q, L, E, I)["deflection"], places=9)

    def test_beams_by_the_stiffness_solver(self):
        fr = Frame2D()
        fr.node("a", 0, 0)
        fr.node("b", L, 0)
        fr.member("a", "b", E, ka.BEAM["A"], I, parts=4)
        fr.support("a")
        fr.load("b", fz=-100.0)
        tip = fr.solve()["disp"]["b"][1]
        self.assertAlmostEqual(-tip, ka.cantilever_tip_load(100.0, L, E, I)["deflection"], places=9)

        fr = Frame2D()
        fr.node("a", 0, 0)
        fr.node("m", L / 2, 0)
        fr.node("b", L, 0)
        fr.member("a", "m", E, ka.BEAM["A"], I, parts=2)
        fr.member("m", "b", E, ka.BEAM["A"], I, parts=2)
        fr.support("a", u=True, w=True, r=False)
        fr.support("b", u=False, w=True, r=False)
        fr.load("m", fz=-349.0)
        out = fr.solve()
        cf = ka.simply_supported_centre_load(349.0, L, E, I)
        self.assertAlmostEqual(-out["disp"]["m"][1], cf["deflection"], places=9)
        self.assertAlmostEqual(out["reaction"]["a"][1], cf["reaction"], places=9)
        self.assertAlmostEqual(out["reaction"]["b"][1], cf["reaction"], places=9)

    def test_portal_sway_tends_to_the_rigid_beam_formula(self):
        h, span, H = 800.0, 1200.0, 500.0
        for bases, formula in (("fixed", ka.portal_sway_fixed_bases), ("pinned", ka.portal_sway_pinned_bases)):
            with self.subTest(bases=bases):
                fr = Frame2D()
                for n, x, z in (("a", 0, 0), ("b", 0, h), ("c", span, h), ("d", span, 0)):
                    fr.node(n, x, z)
                fr.member("a", "b", E, 1e9, I, parts=4)        # axially stiff columns
                fr.member("b", "c", E, 1e9, I * 1e6, parts=4)  # nearly rigid beam
                fr.member("c", "d", E, 1e9, I, parts=4)
                rot = bases == "fixed"
                fr.support("a", r=rot)
                fr.support("d", r=rot)
                fr.load("b", fx=H)
                sway = fr.solve()["disp"]["b"][0]
                self.assertAlmostEqual(sway / formula(H, h, E, I), 1.0, places=5)

    def test_section_properties(self):
        s = ka.rect_tube(40.0, 80.0, 2.0)
        # integrate over the walls on a grid of cell centres (walls fall on cell edges)
        step = 0.05
        y = np.arange(-20 + step / 2, 20, step)
        z = np.arange(-40 + step / 2, 40, step)
        Y, Z = np.meshgrid(y, z)
        inside = ~((np.abs(Y) < 18) & (np.abs(Z) < 38))
        dA = step * step
        self.assertAlmostEqual(inside.sum() * dA / s["A"], 1.0, places=6)
        self.assertAlmostEqual((Z**2 * inside).sum() * dA / s["Iy"], 1.0, places=4)
        self.assertAlmostEqual((Y**2 * inside).sum() * dA / s["Iz"], 1.0, places=4)
        rt = ka.round_tube(40.0, 2.0)
        self.assertAlmostEqual(rt["J"], rt["Iy"] + rt["Iz"], places=6)


class Frequencies(unittest.TestCase):
    def test_simply_supported_first_mode_by_finite_differences(self):
        n = 400
        dx = L / n
        # w'''' with w = w'' = 0 at both ends (ghost points mirror with a sign change)
        D2 = (np.diag(-2.0 * np.ones(n - 1)) + np.diag(np.ones(n - 2), 1) + np.diag(np.ones(n - 2), -1)) / dx**2
        D4 = D2 @ D2
        lam = np.linalg.eigvalsh(E * I * D4 / (ka.BEAM["rho_t_mm3"] * ka.BEAM["A"]))[0]
        f_fd = math.sqrt(lam) / (2 * math.pi)
        f_cf = ka.simply_supported_first_frequency(L, E, I, ka.BEAM["rho_t_mm3"], ka.BEAM["A"])
        self.assertAlmostEqual(f_fd / f_cf, 1.0, places=4)
        self.assertEqual(round(f_cf, 3), 125.555)  # Hz, the closed form rounded

    def test_tip_mass_uses_the_cantilever_stiffness(self):
        k = 1.0 / (ka.cantilever_tip_load(1.0, L, E, I)["deflection"])
        f = math.sqrt(k / 0.01) / (2 * math.pi)  # 10 kg = 0.01 t
        self.assertAlmostEqual(f, ka.cantilever_tip_mass_frequency(L, E, I, 0.01), places=9)
        self.assertEqual(round(f, 4), 7.2411)

    def test_quarter_car(self):
        f = ka.quarter_car_ride_frequency(20e3, 150e3, 125.0)
        self.assertEqual(round(f, 3), 1.891)
        body, hop = ka.quarter_car_two_dof_frequencies(20e3, 150e3, 125.0, 1e-6)
        self.assertAlmostEqual(body / f, 1.0, places=6)


class PlatesAndShells(unittest.TestCase):
    def test_kirsch_from_the_airy_function(self):
        r, th, a, s = sp.symbols("r theta a sigma", positive=True)
        phi = s / 4 * (r**2 - 2 * a**2 * sp.log(r) - (r**2 - 2 * a**2 + a**4 / r**2) * sp.cos(2 * th))
        rr = sp.diff(phi, r) / r + sp.diff(phi, th, 2) / r**2
        tt = sp.diff(phi, r, 2)
        rt = -sp.diff(sp.diff(phi, th) / r, r)
        lap = lambda f: sp.diff(f, r, 2) + sp.diff(f, r) / r + sp.diff(f, th, 2) / r**2
        self.assertEqual(sp.simplify(lap(lap(phi))), 0)  # compatible: biharmonic
        for f in (rr, rt):  # the hole is free of traction
            self.assertEqual(sp.simplify(f.subs(r, a)), 0)
        far = {"rr": s * sp.cos(th) ** 2, "tt": s * sp.sin(th) ** 2, "rt": -s * sp.sin(th) * sp.cos(th)}
        for name, f in (("rr", rr), ("tt", tt), ("rt", rt)):
            self.assertEqual(sp.simplify(sp.limit(f, r, sp.oo) - far[name]), 0, name)  # uniaxial far away
            fn = sp.lambdify((s, a, r, th), f)
            for args in ((100.0, 10.0, 10.0, 0.3), (100.0, 10.0, 25.0, 1.2), (-50.0, 4.0, 9.0, 2.0)):
                self.assertAlmostEqual(ka.kirsch_stresses(*args)[name], fn(*args), places=9)
        self.assertAlmostEqual(ka.kirsch_kt(), 3.0, places=12)
        self.assertAlmostEqual(ka.kirsch_stresses(1.0, 1.0, 1.0, 0.0)["tt"], -1.0, places=12)

    def test_kirsch_across_carries_the_load_cut_by_the_hole(self):
        # Along the line across the hole, the stress above sigma carries the sigma a
        # that the hole takes out of each side: the integral of (sigma_x - sigma) from a to infinity.
        y, a, s = sp.symbols("y a sigma", positive=True)
        sx = s * (1 + a**2 / (2 * y**2) + 3 * a**4 / (2 * y**4))
        self.assertEqual(sp.simplify(sp.integrate(sx - s, (y, a, sp.oo)) - s * a), 0)
        for yv in (10.0, 13.0, 50.0):
            self.assertAlmostEqual(ka.kirsch_across(100.0, 10.0, yv), float(sx.subs({s: 100.0, a: 10.0, y: yv})), places=9)
        self.assertEqual(round(ka.kirsch_across(1.0, 1.0, 5.0), 4), 1.0224)  # 2 % above sigma at 5 radii

    def test_finite_width_fit(self):
        self.assertAlmostEqual(ka.hole_kt_net(0.0), ka.kirsch_kt(), places=12)
        self.assertAlmostEqual(ka.hole_kt_net(1.0), 2.0, places=12)
        xs = np.linspace(0, 1, 101)
        net = [ka.hole_kt_net(x) for x in xs]
        self.assertTrue(all(b < a for a, b in zip(net, net[1:])))  # falls from 3 to 2
        # a small hole sees the Kirsch peak on the gross stress
        self.assertAlmostEqual(ka.hole_kt_gross(0.01), ka.kirsch_kt(), delta=0.002)
        got = [(round(ka.hole_kt_net(c["d"] / c["W"]), 3), round(ka.hole_kt_gross(c["d"] / c["W"]), 3))
               for c in ka.HOLE_CASES]
        self.assertEqual(got, [(2.852, 3.002), (2.156, 4.312)])
        with self.assertRaises(ValueError):
            ka.hole_kt_net(1.2)

    def test_box_section_on_the_mid_line(self):
        b, h, t = ka.BOX["b"], ka.BOX["h"], ka.BOX["t"]
        s = ka.box_midline(b, h, t)
        bm, hm = s["b_m"], s["h_m"]
        y, z = sp.symbols("y z")
        web = sp.integrate(z**2, (y, -t / 2, t / 2), (z, -hm / 2, hm / 2))  # each wall a strip of thickness t
        flange = sp.integrate(z**2, (y, -bm / 2, bm / 2), (z, hm / 2 - t / 2, hm / 2 + t / 2))
        self.assertAlmostEqual(float(2 * web + 2 * flange) / s["Iy"], 1.0, places=12)
        self.assertAlmostEqual(s["Iy"], s["Iz"], places=9)  # square
        solid = ka.rect_tube(b, h, t)
        self.assertAlmostEqual(s["A"], solid["A"], places=9)  # the corners: overlap = missing
        self.assertLess(abs(s["Iy"] / solid["Iy"] - 1), 0.003)
        thin = ka.box_midline(40.0, 60.0, 0.01)
        self.assertAlmostEqual(thin["Iy"] / ka.rect_tube(40.0, 60.0, 0.01)["Iy"], 1.0, places=5)

    def test_cantilever_box(self):
        E, nu = ka.SHELL["E"], ka.SHELL["nu"]
        c = ka.BOX
        out = ka.cantilever_box_tip_load(c["P"], c["L"], E, nu, c["b"], c["h"], c["t"])
        s = ka.box_midline(c["b"], c["h"], c["t"])
        self.assertAlmostEqual(out["deflection_bending"], ka.cantilever_tip_load(c["P"], c["L"], E, s["Iy"])["deflection"], places=12)
        self.assertAlmostEqual(out["deflection"], out["deflection_bending"] + out["deflection_shear"], places=12)
        self.assertLess(out["deflection_shear"] / out["deflection"], 0.01)  # slender: shear under 1 %
        at_h = ka.cantilever_box_tip_load(c["P"], c["L"], E, nu, c["b"], c["h"], c["t"], x=c["h"])
        self.assertAlmostEqual(at_h["flange_stress"], ka.bending_stress(c["P"] * (c["L"] - c["h"]), s["Iy"], s["c_z"]), places=9)
        self.assertEqual((round(out["deflection"], 3), round(out["flange_stress"], 2), round(at_h["flange_stress"], 2)),
                         (5.719, 103.81, 98.62))

    def test_bredt_by_the_moment_and_the_work_of_the_shear_flow(self):
        E, nu = ka.SHELL["E"], ka.SHELL["nu"]
        G = ka.shear_modulus(E, nu)
        for b, h, t in ((ka.BOX["b"], ka.BOX["h"], ka.BOX["t"]), (40.0, 80.0, 3.0)):
            with self.subTest(b=b, h=h, t=t):
                T = ka.BOX["T"]
                out = ka.bredt_torsion(T, ka.BOX["L"], G, b, h, t)
                bm, hm, q = b - t, h - t, out["shear_flow"]
                # walk the mid-line once (counter-clockwise) and add the moment of q ds about a point off the centre
                corners = [(-bm / 2, -hm / 2), (bm / 2, -hm / 2), (bm / 2, hm / 2), (-bm / 2, hm / 2)]
                px, pz, moment, n = 7.0, -3.0, 0.0, 1000
                for (x0, z0), (x1, z1) in zip(corners, corners[1:] + corners[:1]):
                    for k in range(n):
                        f = (k + 0.5) / n
                        x, z = x0 + f * (x1 - x0) - px, z0 + f * (z1 - z0) - pz
                        dx, dz = (x1 - x0) / n, (z1 - z0) / n
                        moment += q * (x * dz - z * dx)
                self.assertAlmostEqual(moment / T, 1.0, places=9)
                # T theta' / 2 = sum of q^2 / (2 G t) ds round the cell
                energy = q**2 / (2 * G * t) * 2 * (bm + hm)
                self.assertAlmostEqual(T * out["twist_rate"] / 2 / energy, 1.0, places=12)
                self.assertAlmostEqual(out["shear_stress"] * t, q, places=9)
        # a thin round cell by the same steps tends to the round tube's polar J
        R, t = 20.0, 0.02
        j_bredt = 4 * (math.pi * R**2) ** 2 * t / (2 * math.pi * R)
        self.assertAlmostEqual(j_bredt / ka.round_tube(2 * R + t, t)["J"], 1.0, places=6)
        sq = ka.bredt_torsion(ka.BOX["T"], ka.BOX["L"], G, ka.BOX["b"], ka.BOX["h"], ka.BOX["t"])
        self.assertEqual((round(sq["shear_stress"], 2), round(math.degrees(sq["twist"]), 4)), (34.63, 1.0595))

    def test_walls_twisting_as_thin_strips(self):
        y, t, s = sp.symbols("y t s", positive=True)
        phi = t**2 / 4 - y**2  # Prandtl stress function across a thin strip
        self.assertEqual(sp.diff(phi, y, 2), -2)
        self.assertEqual([phi.subs(y, v) for v in (-t / 2, t / 2)], [0, 0])
        self.assertEqual(sp.simplify(2 * s * sp.integrate(phi, (y, -t / 2, t / 2)) - s * t**3 / 3), 0)
        G = ka.shear_modulus(ka.SHELL["E"], ka.SHELL["nu"])
        b = ka.BOX
        out = ka.bredt_torsion(b["T"], b["L"], G, b["b"], b["h"], b["t"])
        self.assertAlmostEqual(out["J_open"], 2 * (b["b"] + b["h"] - 2 * b["t"]) * b["t"] ** 3 / 3, places=9)
        # the cell and the strips in parallel: their torques add up to T at one twist rate
        th = out["twist_rate_walls"]
        self.assertAlmostEqual(G * th * out["J"] + G * th * out["J_open"], b["T"], places=6)
        self.assertAlmostEqual(out["shear_stress_walls"], G * th * out["J"] / (2 * (b["b"] - b["t"]) * (b["h"] - b["t"]) * b["t"]), places=9)
        self.assertEqual(round((out["twist_rate_walls"] / out["twist_rate"] - 1) * 100, 3), -0.368)  # % for BOX


class VehicleStatics(unittest.TestCase):
    def test_tilt_by_moments(self):
        t, hcg = 1200.0, 500.0
        th = math.radians(ka.tilt_angle_rigid_deg(t, hcg))
        # at the limit the weight's line passes over the lower wheels: moment = 0
        self.assertAlmostEqual(math.cos(th) * t / 2 - math.sin(th) * hcg, 0.0, places=9)
        self.assertEqual(round(math.degrees(th), 2), 50.19)

    def test_braking_by_integration(self):
        v0, mu = 40 / 3.6, 0.7
        dt, v, s = 1e-5, v0, 0.0
        while v > 0:
            v_new = max(v - mu * ka.G_N * dt, 0.0)
            s += 0.5 * (v + v_new) * dt
            v = v_new
        self.assertAlmostEqual(s / ka.braking_distance_m(v0, mu), 1.0, places=5)

    def test_free_fall_loop_matches_the_sum(self):
        y, v, dt, g = 10.0, 0.0, 1 / 240, 9.81
        for _ in range(240):
            v -= g * dt
            y += v * dt
        self.assertAlmostEqual(y, ka.free_fall_semi_implicit_euler(10.0, g, dt, 240), places=12)

    def test_side_force(self):
        self.assertAlmostEqual(ka.side_force_N(1.2, 1.0, 1.5, 20.0), 360.0, places=9)


class Units(unittest.TestCase):
    def test_every_contract_unit_has_a_factor(self):
        self.assertEqual(set(ka.UNIT_TO_SI), set(contract.UNITS))

    def test_known_conversions(self):
        self.assertAlmostEqual(ka.convert(40.0, "km/h", "m/s"), 40 / 3.6, places=12)
        self.assertAlmostEqual(ka.convert(1.0, "g", "m/s^2"), 9.80665, places=12)
        self.assertAlmostEqual(ka.convert(25.0, "degC", "K"), 298.15, places=12)
        self.assertAlmostEqual(ka.convert(1.0, "kWh", "J"), 3.6e6, places=6)
        self.assertAlmostEqual(ka.convert(1.0, "MPa", "Pa"), 1e6, places=6)
        self.assertAlmostEqual(ka.convert(1.0, "N*m", "N*mm"), 1000.0, places=9)
        self.assertAlmostEqual(ka.convert(600.0, "rpm", "Hz"), 10.0, places=12)
        self.assertAlmostEqual(ka.convert(180.0, "deg", "rad"), math.pi, places=12)
        self.assertAlmostEqual(ka.convert(90.0, "deg/s", "rad/s"), math.pi / 2, places=12)

    def test_angular_rate_is_not_a_frequency(self):
        # 1 Hz is 2 pi rad/s; a shared dimension would silently drop the 2 pi
        with self.assertRaises(ValueError):
            ka.convert(1.0, "Hz", "rad/s")
        with self.assertRaises(ValueError):
            ka.convert(60.0, "rpm", "deg/s")

    def test_round_trip_and_wrong_dimension(self):
        for u, (dim, _, _) in ka.UNIT_TO_SI.items():
            for v, (dim2, _, _) in ka.UNIT_TO_SI.items():
                if dim == dim2:
                    self.assertAlmostEqual(ka.convert(ka.convert(3.7, u, v), v, u), 3.7, places=9)
        with self.assertRaises(ValueError):
            ka.convert(1.0, "mm", "N")


if __name__ == "__main__":
    unittest.main()

"""K2 crash_beam: the frame as co-rotational beams hitting a rigid wall (C1)."""

import copy
import json
import math
import time
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract
from hiraku.contract.loading import on_board
from hiraku.solvers.k0_handcalc.vehicle import mass_summary
from hiraku.solvers.k2_vehicle import jit
from hiraku.solvers.k2_vehicle.crash_beam import build_mesh, known, measure, sim
from hiraku.solvers.k2_vehicle.crash_beam.mesh import local_frame, n_elements
from hiraku.solvers.k2_vehicle.model import box_design
from hiraku.solvers.k2_vehicle.shelf import TOOLS
from hiraku.solvers.k2_vehicle.shelf import crash_beam as tool_module
from tests.solvers.k2.slow import SLOW
from tests.solvers.k2.test_tools import demo_design

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "data" / "designs" / "offroad-4w-v1.json"


def q(v, u):
    return {"value": v, "unit": u}


def small_frame():
    """A ladder frame (rails through every cross member) with a dash hoop ahead of two occupants, a battery
    and a body on the frame, wheels on the supports' frame nodes."""
    d = box_design(mass=200.0, wheel_mass=15.0, name="crash_beam small frame")
    for s, fn in zip(d["supports"], ("LA", "RA", "LB", "RB")):
        s["frame_node"] = fn
    d["parts"][0]["carried_by"] = ["LM", "RM", "LB", "RB"]
    for pid, y in (("occupant_R", -300.0), ("occupant_L", 300.0)):
        d["parts"].append({"id": pid, "name": pid, "kind": "occupant", "mass_kg": 75.0, "carried_by": ["LM", "RM"],
                           "box": {"center_mm": [-800.0, y, 950.0], "size_mm": [400.0, 450.0, 520.0]}})
    d["parts"].append({"id": "battery", "name": "battery", "kind": "battery", "mass_kg": 40.0,
                       "carried_by": ["LM", "RM", "LB", "RB"],
                       "box": {"center_mm": [-1300.0, 0.0, 300.0], "size_mm": [400.0, 500.0, 200.0]}})
    xs = {"F": 300.0, "A": 0.0, "M": -900.0, "B": -1800.0, "R": -2100.0}
    d["nodes"] = [{"id": f"{s}{k}", "xyz_mm": [x, y, 350.0]} for k, x in xs.items() for s, y in (("L", 350.0), ("R", -350.0))]
    d["nodes"] += [{"id": "LD", "xyz_mm": [-300.0, 350.0, 950.0]}, {"id": "RD", "xyz_mm": [-300.0, -350.0, 950.0]}]
    order = list(xs)
    d["members"] = [{"id": f"rail_{s}_{a}{b}", "node_a": f"{s}{a}", "node_b": f"{s}{b}", "section": "rail",
                     "material": "steel"} for s in "LR" for a, b in zip(order, order[1:])]
    d["members"] += [{"id": f"cross_{k}", "node_a": f"L{k}", "node_b": f"R{k}", "section": "tube", "material": "steel"}
                     for k in ("F", "A", "M", "B")]
    d["members"] += [{"id": "post_L", "node_a": "LA", "node_b": "LD", "section": "tube", "material": "steel"},
                     {"id": "post_R", "node_a": "RA", "node_b": "RD", "section": "tube", "material": "steel"},
                     {"id": "dash", "node_a": "LD", "node_b": "RD", "section": "tube", "material": "steel"}]
    d["sections"] = {"rail": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.0}},
                     "tube": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 2.0}}}
    d["materials"] = {"steel": {"name": "steel (test)", "E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 7850.0,
                                "yield_MPa": 235.0, "source": "test value", "verified": False}}
    return d


def load_case(speed=40.0, **params):
    return {"load_case_id": "C1-t", "test_id": "C1", "domain": "crash", "title": "crash_beam test",
            "params": dict({"speed": q(speed, "km/h"), "loading": "two_cargo"}, **params), "tolerance_rel": 0.15}


class KnownAnswers(unittest.TestCase):
    def test_every_own_check_passes(self):
        checks = TOOLS["crash_beam"].own_checks()
        self.assertEqual(len(checks), 13)
        for c in checks:
            with self.subTest(case=c["case"]):
                self.assertTrue(c["pass"], f"{c['case']}: expected {c['expected']} got {c['got']}")
                self.assertTrue(c["case_ja"] and c["source_ja"])
                self.assertEqual(contract.errors("tool_check", c), [])

    def test_cantilever_deflection_and_collapse(self):
        c = known.cantilever_deflection()
        self.assertAlmostEqual(c["got"] / c["expected"], 1.0, delta=2e-3)
        p = known.cantilever_collapse()
        self.assertAlmostEqual(p["got"] / p["expected"], 1.0, delta=3e-2)
        self.assertGreater(p["push"], 3.0 * p["yield_push"])  # well past the first yield
        self.assertGreater(p["max_hinge_deg"], 0.0)

    def test_mass_on_spring_and_momentum(self):
        s = known.mass_on_spring()
        self.assertAlmostEqual(s["peak_force"] / s["expected_force"], 1.0, delta=3e-2)
        self.assertAlmostEqual(s["contact_time"] / s["expected_time"], 1.0, delta=3e-2)
        np.testing.assert_allclose(s["momentum_change_vec"], s["wall_impulse_vec"], rtol=1e-9, atol=1e-9)


class ContactKnownAnswers(unittest.TestCase):
    """Contact between elements that share no node, and a wheel node at the wall (known.tubes_head_on,
    known.tube_crosswise, known.wheel_into_wall)."""

    @classmethod
    def setUpClass(cls):
        cls.h = known.tubes_head_on()
        cls.c = known.tube_crosswise()
        cls.w = known.wheel_into_wall()

    def test_head_on_tubes_spring_mass_answer(self):
        """Two elastic 40x40x2.3 steel tubes, L = 0.5 m, on the x axis, not joined: t1 carries M1 = 2000 kg at its
        rear end and moves at v = 1 m/s; t2 carries M2 = 1000 kg at its far end and rests; no wall.

        Hand calculation (the code computes the same numbers): the touching end nodes B1, A2 meet through the
        penalty spring k_c = pc / (dt^2 (1/m_B1 + 1/m_A2)) (the contact point at the two end nodes, weights 1; pc =
        CONTACT_PENALTY); each tube is a spring E A / L = 205000 MPa x 347.16 mm^2 / 500 mm = 142.3 kN/mm. In series:
        k = 1 / (2 L / (E A) + 1 / k_c). The masses with a third of their tube each (Rayleigh) m_i = M_i + m_t / 3,
        reduced mass mu = m1 m2 / (m1 + m2) (about 667 kg). An undamped impact through a spring: the force peaks at
        F = v sqrt(k mu), the contact lasts half a period pi sqrt(mu / k). Elastic (no losses): momentum and energy
        give v1' = (M1 - M2) / (M1 + M2) v = 1/3 m/s and v2' = 2 M1 / (M1 + M2) v = 4/3 m/s (M_i the tubes'
        whole masses). Tolerances: 3 % on the force and the time (the tubes' own masses are lumped at 5 nodes
        each), 1e-3 relative on the speeds after (the tubes keep a little vibration energy). The capsules touch
        when the end gap is the sum of the contact radii (2 x 20 mm, the ends are rounded): the first touch
        comes within one step's travel of a 40 mm gap."""
        h = self.h
        self.assertTrue(h["separated"])
        self.assertAlmostEqual(h["peak_force"] / h["expected_force"], 1.0, delta=3e-2)
        self.assertAlmostEqual(h["contact_time"] / h["expected_time"], 1.0, delta=3e-2)
        self.assertAlmostEqual(h["v1"] / h["expected_v1"], 1.0, delta=1e-3)
        self.assertAlmostEqual(h["v2"] / h["expected_v2"], 1.0, delta=1e-3)
        np.testing.assert_allclose(h["momentum_after"], h["momentum_before"], rtol=1e-12, atol=1e-9)
        self.assertLessEqual(h["first_touch_gap"], h["contact_distance"] + 1e-12)
        self.assertGreater(h["first_touch_gap"], h["contact_distance"] - 1.0 * h["dt"] - 1e-12)

    def test_crosswise_tube_rebounds_within_the_penetration_bound(self):
        """Tube t2 (40x40x2.3, 0.6 m, along y) moves down at v = 2 m/s onto tube t1 (the same, along x, at rest),
        no gravity; 3 elements each, so the axes cross at the middles of the two middle elements.

        Hand bound: by symmetry the closest points stay at the middles (s = t = 1/2), so the contact point's
        inverse mass is 4 x (1/2)^2 / m_node (the four middle nodes, each m_t / 3) and the penalty spring is
        k = pc / (dt^2 x that), constant: the contact force is conservative. All the relative kinetic energy
        (1/2) mu v^2, mu = m_t / 2 the reduced mass, could at most go into the spring (the tubes' bending takes the
        rest): (1/2) k d^2 <= (1/2) mu v^2, so the penetration d <= v sqrt(mu / k) (0.34 mm here). The axes must
        never come closer than 2 x 20 mm - d. The tube rebounds: after the contact t2 moves down slower than t1
        (they part), at a relative speed no larger than v (energy), and the momentum is kept."""
        c = self.c
        self.assertIsNotNone(c["touched"])
        self.assertIsNotNone(c["released"])
        self.assertLess(c["least"], c["contact_distance"])  # it did touch
        self.assertGreaterEqual(c["least"], c["contact_distance"] - c["bound"])
        self.assertGreater(c["v2"], c["v1"])  # parting: t2 above t1 moves up relative to it
        self.assertLessEqual(c["v2"] - c["v1"], c["speed"] + 1e-9)
        np.testing.assert_allclose(c["momentum_after"], c["momentum_before"], rtol=1e-12, atol=1e-12)

    def test_wheel_node_on_the_tyre_spring_below_the_rim(self):
        """A lone wheel node, radius r = 0.3 m, m = 18 kg, tyre radial stiffness k_t = 60 N/mm, section height
        h = 0.4 r = 0.12 m (rim radius 0.18 m), moves at v = 1 m/s toward the wall at x = 1 m from 2 mm short of
        touching; dt = 1e-5 s.

        Hand calculation: the tyre touches when x + r has passed the wall (x = 0.7 m): the spring acts on the
        deflection at the start of a step, so the first step it pushes starts within one step's travel past
        0.7 m (0.7 m < x <= 0.7 m + v dt) and the step before starts at most at 0.7 m. Then a mass on a linear spring that only pushes: the force peaks at
        F = v sqrt(k_t m) = 1 x sqrt(60000 x 18) = 1039.2 N, the deflection at d = v sqrt(m / k_t) = 17.3 mm (below
        h: the rim does not touch), the contact lasts half a period pi sqrt(m / k_t) = 54.4 ms, and the node leaves
        at -v (no damping in the tyre: elastic). Tolerances 1e-3 relative ((omega dt)^2 = k_t dt^2 / m = 3.3e-7:
        the central differences are near exact; the contact time is counted in whole steps, 1e-5 / 0.0544 = 2e-4)."""
        w = self.w
        m, k, v, r, h, wall = 18.0, 60e3, 1.0, 0.3, 0.12, 1.0
        self.assertEqual((w["mass"], w["k_t"], w["speed"], w["radius"], w["wall"]), (m, k, v, r, wall))
        self.assertAlmostEqual(w["section"], h, delta=1e-12)
        self.assertLessEqual(w["last_free_x"], wall - r + 1e-12)
        self.assertGreater(w["first_touch_x"], wall - r - 1e-12)
        self.assertLessEqual(w["first_touch_x"], wall - r + v * w["dt"] + 1e-12)
        self.assertAlmostEqual(w["peak_force"] / (v * math.sqrt(k * m)), 1.0, delta=1e-3)
        self.assertAlmostEqual(w["contact_time"] / (math.pi * math.sqrt(m / k)), 1.0, delta=1e-3)
        self.assertAlmostEqual((w["deepest"] - (wall - r)) / (v * math.sqrt(m / k)), 1.0, delta=1e-3)
        self.assertFalse(w["rim"])
        self.assertAlmostEqual(w["speed_after"] / -v, 1.0, delta=1e-3)

    def test_wheel_node_reaches_the_rim(self):
        """The same wheel node at v = 40 km/h = 11.11 m/s: the free spring would deflect v sqrt(m / k_t) = 192 mm,
        more than the section height h = 120 mm, so the rim reaches the wall: the node stops at x = wall - (r - h)
        = 0.82 m exactly (the rim's contact is perfectly plastic, as the wheel's was before the tyre) and never
        passes it. Hand calculation: the tyre takes (1/2) k_t h^2 = 432 J of the (1/2) m v^2 = 1111 J; the rest is
        lost at the rim. The tyre then pushes the node back with what it holds: it leaves at h sqrt(k_t / m) =
        6.93 m/s (1e-3 relative). The wall's largest force is the rim's (more than the tyre's k_t h = 7.2 kN)."""
        w = known.wheel_into_wall(speed=40.0 / 3.6)
        m, k, r, h, wall = 18.0, 60e3, 0.3, 0.12, 1.0
        self.assertTrue(w["rim"])
        self.assertAlmostEqual(w["deepest"], wall - (r - h), delta=1e-12)
        self.assertAlmostEqual(w["speed_after"] / -(h * math.sqrt(k / m)), 1.0, delta=1e-3)
        self.assertGreater(w["peak_force"], k * h)

    def test_tyre_spring_and_time_step(self):
        """The tyre's spring is held to the kernel's budget: (omega dt)^2 = k_t dt^2 / m of the wheel node at most
        sim.TYRE_PENALTY_MAX; a spring past it fails loudly (the run would go unstable)."""
        mesh = build_mesh(on_board(small_frame(), "two_cargo"))
        wheels = range(mesh.n_frame, mesh.n)
        self.assertTrue(all(mesh.ktyre[i] == 60e3 for i in wheels))  # small_frame has no tyre_stiffness: 60 N/mm
        self.assertTrue(all(abs(mesh.hsec[i] - 0.4 * mesh.nrad[i]) < 1e-12 for i in wheels))
        self.assertTrue(all(mesh.ktyre[i] == 0.0 and mesh.hsec[i] == 0.0 for i in range(mesh.n_frame)))
        d = small_frame()
        d["variables"]["tyre_stiffness"] = q(90.0, "N/mm")
        d["variables"]["rim_radius"] = q(150.0, "mm")
        mesh2 = build_mesh(on_board(d, "two_cargo"))
        i = mesh2.n_frame
        self.assertAlmostEqual(mesh2.ktyre[i], 90e3)
        self.assertAlmostEqual(mesh2.hsec[i], mesh2.nrad[i] - 0.15)
        mesh2.ktyre[i] = 10.0 * sim.TYRE_PENALTY_MAX * mesh2.mass[i] / sim.time_step(mesh2) ** 2
        with self.assertRaises(ValueError):
            sim.run_crash(mesh2, 1.0, 1e-3)

    def test_closest_points_of_two_segments(self):
        from hiraku.solvers.k2_vehicle.crash_beam.kernel import closest, closest_py

        for fn in (closest, closest_py):
            x = np.array([[0.0, 0, 0], [1.0, 0, 0], [0.25, -1, 1], [0.25, 1, 1]])
            self.assertEqual(tuple(round(v, 12) for v in fn(x, 0, 1, 2, 3)), (0.25, 0.5))  # crossing
            x = np.array([[0.0, 0, 0], [1.0, 0, 0], [1.5, 0, 0], [3.0, 0, 0]])
            self.assertEqual(fn(x, 0, 1, 2, 3), (1.0, 0.0))  # on one line, end to end
            x = np.array([[0.0, 0, 0], [1.0, 0, 0], [0.5, 0.1, 0], [2.0, 0.1, 0]])
            s, t = fn(x, 0, 1, 2, 3)  # parallel, overlapping on 0.5 .. 1: its middle
            self.assertAlmostEqual(s, 0.75)
            self.assertAlmostEqual(t, 0.25 / 1.5)
            x = np.array([[0.0, 0, 0], [1.0, 0, 0], [-2.0, 0.1, 0], [-1.0, 0.1, 0]])
            self.assertEqual(fn(x, 0, 1, 2, 3), (0.0, 1.0))  # parallel, apart


class Mesh(unittest.TestCase):
    def test_total_mass_is_the_designs(self):
        for name, d in (("ladder", demo_design()), ("small", small_frame()), ("demo", json.loads(DEMO.read_text(encoding="utf-8")))):
            for loading in ("empty", "two_cargo"):
                with self.subTest(design=name, loading=loading):
                    dd = on_board(d, loading)
                    mesh = build_mesh(dd)
                    self.assertAlmostEqual(float(mesh.mass.sum()), mass_summary(dd)["mass_kg"], delta=1e-9)
                    self.assertTrue(np.all(mesh.mass > 0.0) and np.all(mesh.inertia > 0.0))

    def test_frame_mass_and_part_shares(self):
        d = on_board(demo_design(), "two_cargo")
        mesh = build_mesh(d)
        rail = 7850.0 * (40 * 80 - 36 * 76) * 1e-6 * 2.4
        cross = 7850.0 * (40 * 40 - 36 * 36) * 1e-6 * 0.7
        self.assertAlmostEqual(mesh.frame_mass, 2 * rail + 2 * cross, places=9)
        d2 = copy.deepcopy(d)
        d2["parts"].append({"id": "box", "name": "box", "kind": "cargo", "mass_kg": 10.0, "carried_by": ["LA", "RB"],
                            "box": {"center_mm": [0.0, 0.0, 400.0], "size_mm": [10.0, 10.0, 10.0]}})
        sh = build_mesh(d2).node_parts["box"]
        self.assertEqual({mesh.node_ids[k]: v for k, v in sh.items()}, {"LA": 5.0, "RB": 5.0})

    def test_local_axes_follow_k1(self):
        # horizontal: y up; vertical going up: y = -x_design, z = -y_design (PyNite's rule in the design frame)
        f = local_frame(np.zeros(3), np.array([1.0, 0.0, 0.0]))
        np.testing.assert_allclose(f[:, 1], [0.0, 0.0, 1.0])
        f = local_frame(np.zeros(3), np.array([0.0, 0.0, 1.0]))
        np.testing.assert_allclose(f[:, 1], [-1.0, 0.0, 0.0])
        np.testing.assert_allclose(f[:, 2], [0.0, -1.0, 0.0])

    def test_short_members_get_fewer_elements(self):
        props = {"A": 531.0, "Iy": 1.5e5, "Iz": 4.4e5}  # r = 28.8 mm
        self.assertEqual(n_elements(2400.0, props), 4)
        self.assertEqual(n_elements(115.0, props), 1)
        self.assertEqual(n_elements(250.0, props), 2)


class Kernel(unittest.TestCase):
    @unittest.skipUnless(jit.ENABLED, "without numba advance is advance_py")
    def test_the_same_steps_as_python(self):
        mesh = build_mesh(on_board(small_frame(), "two_cargo"))
        dt = sim.time_step(mesh)
        a = sim.new_state(mesh, (40.0, 0.0, 0.0), dt)
        b = a.copy()
        wall = float(mesh.x0[:, 0].max())
        for st, use in ((a, True), (b, False)):
            imp = sim.advance(mesh, st, 600, dt, wall=wall, mu=0.3, hinge_lim=0.02, use_jit=use)
            st.imp = imp
        self.assertTrue(a.yielded.any())
        self.assertFalse(a.alive.all())  # some broke: every branch of the kernel has run
        self.assertGreater(int(a.cstat[2]), 0)  # elements touched each other
        self.assertGreater(int(a.cstat[0]), 1)  # the contact pair list was made again
        # the front wheels start at the wall (x + r = the rail ends' x): the wheel branch of the wall has run
        self.assertTrue(np.any(np.abs(mesh.x0[mesh.n_frame:, 0] + mesh.nrad[mesh.n_frame:] - wall) < 1e-12))
        np.testing.assert_array_equal(a.cstat, b.cstat)
        np.testing.assert_array_equal(a.cpairs[:a.cnp[0]], b.cpairs[:b.cnp[0]])
        for k in ("x", "v", "R", "w", "up", "tp", "thp", "fint", "fcon", "imp"):
            ga, gb = getattr(a, k), getattr(b, k)
            scale = max(1e-9, float(np.max(np.abs(gb))))
            self.assertLessEqual(float(np.max(np.abs(ga - gb))), 1e-12 * scale, k)
        np.testing.assert_array_equal(a.alive, b.alive)

    def test_free_flight_keeps_momentum_and_shape(self):
        mesh = build_mesh(on_board(small_frame(), "two_cargo"))
        dt = sim.time_step(mesh)
        st = sim.new_state(mesh, (3.0, 1.0, -2.0), dt)
        sim.advance(mesh, st, 500, dt)
        np.testing.assert_allclose(mesh.mass @ st.v, mesh.mass.sum() * np.array([3.0, 1.0, -2.0]), rtol=1e-12)
        np.testing.assert_allclose(st.x - mesh.x0, np.tile([3.0, 1.0, -2.0], (mesh.n, 1)) * st.t, atol=1e-9)
        self.assertFalse(st.yielded.any())


class NodeMotion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mesh = build_mesh(on_board(small_frame(), "two_cargo"))
        cls.crash = sim.run_crash(cls.mesh, 40.0 / 3.6, 0.2, mu=0.3, hinge_lim=math.radians(60.0))

    def test_frame_picks(self):
        p = sim.frame_picks(2000, [7, 1500, None])
        self.assertLessEqual(len(p), 150)
        for k in (0, 1999, 7, 1500):
            self.assertIn(k, p)
        self.assertEqual(p, sorted(set(p)))
        self.assertEqual(sim.frame_picks(5, [2]), [0, 1, 2, 3, 4])
        many = sim.frame_picks(2000, list(range(0, 2000, 13)), even=120, most=150)
        self.assertEqual(len(many), len(set(range(0, 2000, 13)) | {1999}))  # the specials and the ends stay

    def test_field_shape(self):
        f = sim.node_motion(self.crash, "test")
        json.dumps(f)
        self.assertEqual(f["kind"], "node_motion")
        self.assertEqual(f["unit"], "mm")
        fr = f["frames"]
        n = len(fr["t_s"])
        self.assertTrue(2 <= n <= 150)
        self.assertEqual(fr["t_s"], sorted(fr["t_s"]))
        self.assertEqual(fr["t_s"][0], 0.0)
        self.assertAlmostEqual(fr["t_s"][-1], float(self.crash.t_all[-1]))
        self.assertEqual(len(fr["positions_mm"]), n)
        self.assertEqual(len(fr["broken"]), n)
        self.assertNotIn("max_decel_g", fr)
        self.assertEqual({len(p) for p in fr["positions_mm"]}, {len(f["nodes"])})
        self.assertEqual(len(f["members"]), len(self.mesh.frame_elements()))  # the wheel links left out
        self.assertEqual(len(f["nodes"]), self.mesh.n)  # the wheel nodes among the nodes
        self.assertEqual(self.mesh.n - self.mesh.n_frame, 4)
        ids = set(f["nodes"])
        for a, b, mid in f["members"]:
            self.assertIn(a, ids)
            self.assertIn(b, ids)
            self.assertIn(mid, {m["id"] for m in small_frame()["members"]})
        self.assertEqual(fr["positions_mm"][0][0], [round(v * 1000.0, 1) for v in self.mesh.x0[0]])
        tt = self.crash.t_all
        self.assertIn(float(tt[int(np.argmax(self.crash.decel()))]), fr["t_s"])  # the peak deceleration's frame
        self.assertEqual(contract.errors("field", f), [])
        for a, b in zip(fr["broken"], fr["broken"][1:]):
            self.assertTrue(set(a) <= set(b))

    def test_crush_and_room(self):
        seat = sorted({k for pid, sh in self.mesh.node_parts.items() if pid.startswith("occupant") for k in sh})
        crush = measure.shortening(self.crash, seat)
        self.assertEqual(crush[0], 0.0)
        self.assertGreater(crush.max(), 0.0)


class Tool(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = small_frame()
        t0 = time.perf_counter()
        cls.rec = TOOLS["crash_beam"].run(cls.d, load_case(), [{"prediction_id": "pred-0001"}], {})
        cls.wall = time.perf_counter() - t0
        # no element breaks (hinge_break_angle out of reach): the frame stays one piece and the run goes to the stop
        cls.whole = TOOLS["crash_beam"].run(cls.d, load_case(hinge_break_angle=q(1000.0, "deg")),
                                            [{"prediction_id": "pred-0001"}], {})

    def res(self, rec=None):
        return {r["quantity"]: r for r in (rec or self.rec)["results"]}

    def test_a_large_piece_breaking_off_is_reported_and_can_stop_the_run(self):
        """With the hinges breaking at 25 deg and a rim of 295 mm in the 300 mm wheels (a 5 mm tyre section: the front
        wheels stop at the wall nearly at once and load the rails behind the front axle), at 40 km/h the side rails
        between the front axle and the seats break and a piece of 8.7 % of the mass comes apart from the seats' side
        at about 43 ms. (With the default 40 % section the tyres give way, the posts break instead and no piece of
        3 % comes off.) By default the run goes on (the pieces touch each other) and the results say when the split
        happened (C1: NG); with stop_at_split it stops there, and the results that need the stop are not given."""
        d = copy.deepcopy(self.d)
        d["variables"]["rim_radius"] = q(295.0, "mm")
        on = TOOLS["crash_beam"].run(d, load_case(hinge_break_angle=q(25.0, "deg")),
                                     [{"prediction_id": "pred-0001"}], {})
        stop = TOOLS["crash_beam"].run(d, load_case(hinge_break_angle=q(25.0, "deg"), stop_at_split=True),
                                       [{"prediction_id": "pred-0001"}], {})
        for rec in (on, stop):
            self.assertEqual(rec["status"], "ok", rec.get("error"))
            self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        r_on, r = self.res(on), self.res(stop)
        for rr in (r_on, r):
            self.assertEqual(rr["frame_split_time"]["unit"], "s")
            self.assertIn("rail_L_AM", rr["frame_split_time"]["where"])
            self.assertIn("crush_at_split", rr)
        self.assertAlmostEqual(r_on["frame_split_time"]["value"], r["frame_split_time"]["value"], delta=1e-9)
        for name in ("mean_decel", "max_crush", "crush_margin", "occupant_space_left"):
            self.assertNotIn(name, r)
            self.assertIn(name, r_on)
        # C1's two quantities in both: the least room (up to the split when the run stops there: an upper bound) and
        # the broken-off piece's share
        for rr in (r_on, r):
            self.assertIn("occupant_space_margin", rr)
            self.assertGreater(rr["frame_split_fraction"]["value"], 0.03)
        self.assertGreaterEqual(r_on["max_crush"]["value"], r_on["crush_at_split"]["value"])
        self.assertTrue(any("split" in n and "not computed" in n for n in stop["assumptions"]))
        self.assertTrue(any("split" in n and "goes on past the split" in n for n in on["assumptions"]))
        t_split = r["frame_split_time"]["value"]
        self.assertLessEqual(stop["series"][0]["t_s"][-1], t_split + 1e-6)  # the series' t_s are to 1 us
        self.assertGreater(on["series"][0]["t_s"][-1], t_split + 1e-3)

    def test_member_series_at_the_node_motion_frames(self):
        series = {s["channel"]: s for s in self.whole["series"]}
        force = [c for c in series if c.startswith("member_axial_force.")]
        hinge = [c for c in series if c.startswith("member_hinge.")]
        self.assertEqual(len(force), len(self.d["members"]))  # the design's members (no wheel links)
        self.assertEqual(len(hinge), 2 * len(force))  # each member's two ends
        self.assertEqual({series[c]["unit"] for c in hinge}, {"deg"})
        if "fields" in self.whole:
            frames = self.whole["fields"][0]["frames"]["t_s"]
            self.assertEqual(len(series[force[0]]["t_s"]), len(frames))
            for a, b in zip(series[force[0]]["t_s"], frames):
                self.assertAlmostEqual(a, b, delta=1e-6)
        # the front rail is pushed first: in its first 3 ms in compression up to its squash load A f_y (no more:
        # elastic-perfectly plastic)
        mesh = build_mesh(self.d)
        n_p = min(mesh.prop[e, 4] for e in range(mesh.ne) if mesh.member_of[e] == "rail_L_FA")
        f = series["member_axial_force.rail_L_FA"]
        early = [v for t, v in zip(f["t_s"], f["values"]) if 0.0 < t <= 3e-3]
        self.assertTrue(early)
        self.assertLess(min(early), -0.9 * n_p)
        self.assertGreaterEqual(min(early), -1.001 * n_p)

    def test_hot_spots_give_each_members_room_to_the_occupants(self):
        lc = load_case(hinge_break_angle=q(1000.0, "deg"))
        lc["criterion"] = {"quantity": "occupant_space_margin", "op": ">=", "limit": q(0.0, "mm")}
        rec = TOOLS["crash_beam"].run(self.d, lc, [{"prediction_id": "pred-0001"}], {})
        hs = [h for h in rec.get("hot_spots", []) if h["quantity"] == "occupant_space_margin"]
        self.assertTrue(hs)
        self.assertTrue(all(not h["predicted"] for h in hs))  # places the load case does not judge
        least = min(h["value"]["value"] for h in hs)
        self.assertAlmostEqual(least, self.res(rec)["occupant_space_left"]["value"], places=6)

    def test_record_fits_the_contract(self):
        self.assertEqual(self.rec["status"], "ok", self.rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(self.rec, analysis_id="ana-0001")), [])
        json.dumps(self.rec)
        self.assertLess(self.wall, 30.0)

    def test_results_and_series(self):
        r = self.res(self.whole)
        for name, unit in (("peak_decel", "g"), ("mean_decel", "g"), ("max_crush", "mm"), ("broken_members", "1"),
                           ("max_plastic_rotation", "deg"), ("occupant_space_left", "mm")):
            self.assertEqual(r[name]["unit"], unit, name)
        self.assertNotIn("frame_split_time", r)
        self.assertIn(r["battery_mount_force"]["where"], {"LM", "RM", "LB", "RB"})
        self.assertGreater(r["battery_mount_force"]["value"], 0.0)
        self.assertGreater(r["occupant_space_left"]["value"], -1000.0)
        self.assertIn(r["occupant_space_left"]["where"], {"occupant_L", "occupant_R"})
        self.assertGreater(r["peak_decel"]["value"], r["mean_decel"]["value"])
        self.assertGreater(r["mean_decel"]["value"], 0.0)
        self.assertGreater(r["max_crush"]["value"], 0.0)
        series = {s["channel"]: s for s in self.rec["series"]}
        self.assertEqual({c for c in series if not c.startswith("member_")}, {"deceleration", "crush"})
        t = series["deceleration"]["t_s"]
        self.assertEqual(t[0], 0.0)
        chunk = 1.1e-4  # a record row is one kernel chunk of about 0.1 ms
        self.assertTrue(all(abs(tk - k * 1e-3) <= chunk for k, tk in enumerate(t[:-1])), t[:5])
        self.assertEqual(self.rec["scene"][0]["kind"], "wall")

    def test_assumptions_name_the_unsourced_constants(self):
        notes = self.rec["assumptions"]
        self.assertTrue(any("確かめていない" in n and "friction" in n for n in notes), notes)
        self.assertTrue(any("確かめていない" in n and "hinge" in n for n in notes), notes)
        self.assertTrue(any("Courant" in n for n in notes), notes)
        # the tyre: small_frame has no tyre_stiffness, so 60 N/mm, taken as the radial stiffness (not checked)
        self.assertTrue(any(n.startswith("tyre radial stiffness for the wall contact: the vertical stiffness "
                                         "tyre_stiffness, taken as the same in the radial direction (not checked)")
                            and "60 N/mm" in n for n in notes), notes)
        self.assertTrue(any("40 % of the wheel radius" in n and "HIRAKU's assumption" in n for n in notes), notes)
        self.assertTrue(any("peak_decel" in n and "input to the occupants" in n for n in notes), notes)

    def test_fields_only_when_the_contract_has_node_motion(self):
        self.assertEqual(sim.contract_has_node_motion(),
                         "node_motion" in contract.schema()["$defs"]["field"]["properties"]["kind"]["enum"])
        if not sim.contract_has_node_motion():
            self.assertNotIn("fields", self.rec)
        else:
            self.assertEqual([f["kind"] for f in self.rec["fields"]], ["node_motion"])
        was = tool_module.contract_has_node_motion
        try:
            tool_module.contract_has_node_motion = lambda: True
            rec = TOOLS["crash_beam"].run(self.d, load_case(), [{"prediction_id": "pred-0001"}], {})
        finally:
            tool_module.contract_has_node_motion = was
        self.assertEqual([f["kind"] for f in rec["fields"]], ["node_motion"])

    def test_given_params_are_used_and_not_assumed(self):
        d = copy.deepcopy(self.d)
        d["variables"]["tyre_stiffness"] = q(60.0, "N/mm")  # the tyre's default is an unchecked value too
        rec = TOOLS["crash_beam"].run(d, load_case(20.0, wall_mu=q(0.0, "1"), hinge_break_angle=q(1000.0, "deg")),
                                      [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        res = {r["quantity"]: r["value"] for r in rec["results"]}
        self.assertEqual(res["broken_members"], 0.0)
        self.assertFalse(any("確かめていない" in n for n in rec["assumptions"]))
        self.assertLess(res["max_crush"], self.res(self.whole)["max_crush"]["value"])  # half the speed

    def test_bad_speed_fails_in_the_record(self):
        rec = TOOLS["crash_beam"].run(self.d, load_case(-5.0), [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])


def _unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def _angular_momentum(mesh, st):
    """kg m^2/s about the origin: sum m x cross v (the positions at the step, the velocities at the half step before:
    the pair the central differences keep) plus the nodes' spins J w."""
    return (mesh.mass[:, None] * np.cross(st.x, st.v)).sum(axis=0) + (mesh.inertia[:, None] * st.w).sum(axis=0)


class WallOfAnyNormal(unittest.TestCase):
    """run_crash's wall_dir: the rigid plane p . d = wall with any unit direction d the frame moves into it (the
    plane's normal is -d); the kernel's wall is x only, so the frame is turned to put d on x and back."""

    def test_along_x_it_is_the_default_wall(self):
        mesh = build_mesh(on_board(small_frame(), "two_cargo"))
        a = sim.run_crash(mesh, 40.0 / 3.6, 5e-3, mu=0.3, hinge_lim=math.radians(60.0))
        b = sim.run_crash(mesh, 0.0, 5e-3, mu=0.3, hinge_lim=math.radians(60.0), wall_dir=(1.0, 0.0, 0.0),
                          velocity=(40.0 / 3.6, 0.0, 0.0))
        self.assertEqual(b.speed, a.speed)
        self.assertEqual(b.wall_x, a.wall_x)
        np.testing.assert_array_equal(a.positions, b.positions)
        np.testing.assert_array_equal(a.impulse, b.impulse)
        np.testing.assert_array_equal(a.decel(), b.decel())
        np.testing.assert_array_equal(a.final.R, b.final.R)

    def test_tube_and_mass_into_an_inclined_plane(self):
        """An elastic 40x40x2.3 steel tube 0.5 m long along d = (0.6, -0.48, 0.64) (|d| = 1), M = 2000 kg on its rear
        node A, its front node B touching the plane p . d = B . d (normal -d); the frame moves at v_n = 1 m/s along d
        and v_t = 0.7 m/s along t = d x z / |d x z| (in the plane), no friction.

        Hand calculation (known.mass_on_spring's, along d): the front node meets the plane through the penalty spring
        k_w = PENALTY m_B / dt^2 in series with the tube's E A / L; m = M + m_tube / 3. The normal force peaks at
        v_n sqrt(k m) and lasts pi sqrt(m / k) (3 %, as along x). Without friction the plane's impulse is along -d only
        (its parts along t and d x t: 1e-9 of it); the momentum change is the impulse (1e-9); the motion along t is
        not touched (1e-9 of the momentum); an elastic impact: the whole mass leaves at -v_n along d (3 %: the tube
        keeps some vibration)."""
        d = np.array([0.6, -0.48, 0.64])
        self.assertAlmostEqual(float(np.linalg.norm(d)), 1.0, places=12)
        t = _unit(np.cross(d, [0.0, 0.0, 1.0]))
        L_mm, M, vn, vt = 500.0, 2000.0, 1.0, 0.7
        elastic = dict(known.STEEL, yield_MPa=1e9)
        design = known.beam_design(L_mm, known.SQUARE, elastic,
                                   [{"id": "mass", "name": "mass", "kind": "cargo", "mass_kg": M, "carried_by": ["A"],
                                     "box": {"center_mm": [0.0, 0.0, 0.0], "size_mm": [10.0, 10.0, 10.0]}}])
        design["nodes"][1]["xyz_mm"] = list(d * L_mm)
        mesh = build_mesh(design)
        dt = sim.time_step(mesh)
        front = mesh.design_nodes["B"]
        k_tube = mesh.prop[0, 0] / (L_mm / 1000.0)
        k_wall = sim.PENALTY * mesh.mass[front] / dt ** 2
        k = k_tube * k_wall / (k_tube + k_wall)
        m = M + mesh.frame_mass / 3.0
        t_c = math.pi * math.sqrt(m / k)
        v0 = vn * d + vt * t
        run = sim.run_crash(mesh, 0.0, 2.5 * t_c, wall_dir=d, velocity=v0, until_limit=True)
        self.assertAlmostEqual(run.wall_x, float(mesh.x0[front] @ d), places=12)
        self.assertAlmostEqual(run.speed, vn, places=12)
        np.testing.assert_allclose(run.velocities[0], np.tile(v0, (mesh.n, 1)), rtol=0, atol=1e-15)
        fn = -(run.impulse @ d) / run.chunk
        self.assertAlmostEqual(fn.max() / (vn * math.sqrt(k * m)), 1.0, delta=3e-2)
        on = np.nonzero(fn > 0.0)[0]
        self.assertAlmostEqual((run.t[on[-1]] - run.t[on[0]] + run.chunk) / t_c, 1.0, delta=3e-2)
        imp = run.impulse.sum(axis=0)
        self.assertLess(abs(float(imp @ t)), 1e-9 * np.linalg.norm(imp))
        self.assertLess(abs(float(imp @ np.cross(d, t))), 1e-9 * np.linalg.norm(imp))
        dp = run.momentum[-1] - run.momentum[0]
        np.testing.assert_allclose(dp, imp, rtol=0, atol=1e-9 * np.linalg.norm(imp))
        Mt = run.total_mass
        self.assertLess(abs(float(dp @ t)), 1e-9 * Mt * vt)
        self.assertAlmostEqual(float(run.momentum[-1] @ d) / Mt / -vn, 1.0, delta=3e-2)
        # the deceleration is along d: the plane's impulse along -d over the window / (M x window)
        w = run.window_rows()
        cum = np.concatenate([[0.0], np.cumsum(run.impulse @ d)])
        kk = np.arange(len(cum))
        np.testing.assert_allclose(run.decel(), -(cum - cum[np.maximum(kk - w, 0)]) / (Mt * w * run.chunk), rtol=1e-12)

    def test_free_fall_with_gravity(self):
        """No contact (the plane far below), gravity g: the momentum grows by M g t exactly (1e-9) and every node falls
        g t^2 / 2 from rest (to the central differences' error, dt^2 terms: 1e-6 m)."""
        mesh = build_mesh(on_board(small_frame(), "two_cargo"))
        g = np.array([0.0, 0.0, -9.81])
        run = sim.run_crash(mesh, 0.0, 5e-3, wall_dir=(0.0, 0.0, -1.0), wall=10.0, velocity=(0.0, 0.0, 0.0),
                            gravity=g, until_limit=True)
        T = float(run.t[-1])
        self.assertAlmostEqual(T, 5e-3, delta=run.chunk)
        np.testing.assert_allclose(run.momentum[-1] - run.momentum[0], run.total_mass * g * T, rtol=0,
                                   atol=1e-9 * run.total_mass * 9.81 * T)
        np.testing.assert_allclose(run.positions[-1] - mesh.x0, np.tile(0.5 * g * T * T, (mesh.n, 1)), atol=1e-6)
        self.assertFalse(run.final.yielded.any())


class Spin(unittest.TestCase):
    """new_state's and run_crash's spin: the frame starts as a rigid body turning at omega (rad/s, design frame)
    about a point (default its mass centre): every node's velocity v + omega x (x - c), every node's spin omega."""

    def test_rigid_velocity_field_and_its_angular_momentum(self):
        """Hand calculation: about the origin, L = M c x v + I_c omega + sum J omega, with I_c = sum m (|r|^2 1 - r r^T),
        r = x - c (the point masses' inertia about the mass centre) and J the nodes' rotational inertias (1e-12)."""
        mesh = build_mesh(on_board(small_frame(), "two_cargo"))
        om, v0 = np.array([0.8, -1.5, 2.5]), np.array([1.0, 0.5, 0.0])
        st = sim.new_state(mesh, v0, sim.time_step(mesh), spin=om)
        M = float(mesh.mass.sum())
        c = mesh.mass @ mesh.x0 / M
        r = mesh.x0 - c
        np.testing.assert_allclose(st.v, v0 + np.cross(om, r), rtol=0, atol=1e-14)
        np.testing.assert_array_equal(st.w, np.tile(om, (mesh.n, 1)))
        Ic = sum(mi * (float(ri @ ri) * np.eye(3) - np.outer(ri, ri)) for mi, ri in zip(mesh.mass, r))
        L = M * np.cross(c, v0) + Ic @ om + float(mesh.inertia.sum()) * om
        np.testing.assert_allclose(_angular_momentum(mesh, st), L, rtol=1e-12)
        np.testing.assert_allclose(mesh.mass @ st.v, M * v0, rtol=0, atol=1e-12 * M * np.linalg.norm(v0))
        # about a given point: that point moves at v0
        k = mesh.design_nodes["LD"]
        st2 = sim.new_state(mesh, v0, None, spin=om, about=mesh.x0[k])
        np.testing.assert_allclose(st2.v[k], v0, rtol=0, atol=1e-15)

    def test_a_spinning_free_frame_keeps_its_angular_momentum(self):
        """The small frame (with its wheel nodes on links) turning at |omega| = 3.0 rad/s about a skew axis and moving,
        free (no wall, no gravity), 4000 steps (about 35 ms): the internal forces and moments of the beams and links
        balance, so the momentum (1e-12) and the angular momentum (sum m x cross v + J w, about the origin; 1e-9)
        are kept. The spin is slow enough that nothing yields."""
        mesh = build_mesh(on_board(small_frame(), "two_cargo"))
        dt = sim.time_step(mesh)
        om, v0 = np.array([0.8, -1.5, 2.5]), np.array([1.0, 0.5, 0.0])
        st = sim.new_state(mesh, v0, dt, spin=om)
        p0, L0 = mesh.mass @ st.v, _angular_momentum(mesh, st)
        sim.advance(mesh, st, 4000, dt)
        np.testing.assert_allclose(mesh.mass @ st.v, p0, rtol=0, atol=1e-12 * np.linalg.norm(p0))
        np.testing.assert_allclose(_angular_momentum(mesh, st), L0, rtol=0, atol=1e-9 * np.linalg.norm(L0))
        self.assertFalse(st.yielded.any())

    def test_run_crash_takes_the_spin(self):
        mesh = build_mesh(on_board(small_frame(), "two_cargo"))
        om = np.array([0.0, 2.0, 0.0])
        run = sim.run_crash(mesh, 0.0, 1e-3, wall_dir=(0.0, 0.0, -1.0), wall=10.0, velocity=(0.0, 0.0, 0.0), spin=om,
                            until_limit=True)
        c = mesh.mass @ mesh.x0 / mesh.mass.sum()
        np.testing.assert_allclose(run.velocities[0], np.cross(om, mesh.x0 - c), rtol=0, atol=1e-14)
        np.testing.assert_allclose(run.final.w.mean(axis=0), om, rtol=0, atol=2e-3)


class BodyFrameMeasures(unittest.TestCase):
    def test_rigid_fit_recovers_a_rigid_motion(self):
        rng = np.random.default_rng(3)
        a = rng.normal(size=(12, 3))
        R = known_rotation(np.array([0.3, -1.1, 0.7]), 2.1)
        t = np.array([0.4, -2.0, 1.5])
        Rf, tf = measure.rigid_fit(a, a @ R.T + t)
        np.testing.assert_allclose(Rf, R, atol=1e-12)
        np.testing.assert_allclose(tf, t, atol=1e-12)

    def test_segment_to_box_distance(self):
        """A box 0.2 x 0.4 x 0.6 m at the origin: a segment along y at x = 0.3 (0.2 m off the +x face) is 0.2 m
        away; one through the middle along z is inside, 0.1 m from the nearest face (-0.1); turned 90 deg about z
        the box's x half size is 0.2, so the first segment is 0.1 m away."""
        c, h = np.zeros(3), np.array([0.1, 0.2, 0.3])
        self.assertAlmostEqual(measure.segment_box_distance(np.array([0.3, -1, 0.0]), np.array([0.3, 1, 0.0]), c, h),
                               0.2, places=12)
        self.assertAlmostEqual(measure.segment_box_distance(np.array([0.0, 0, -1.0]), np.array([0.0, 0, 1.0]), c, h),
                               -0.1, places=12)
        rz = np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
        self.assertAlmostEqual(measure.segment_box_distance(np.array([0.3, -1, 0.0]), np.array([0.3, 1, 0.0]), c, h,
                                                            rz), 0.1, places=12)


def known_rotation(axis, angle):
    k = _unit(axis)
    K = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + math.sin(angle) * K + (1.0 - math.cos(angle)) * K @ K


class BandNote(unittest.TestCase):
    """break_band_note on hand-made runs: which members are at the edge, and whether a C1 verdict turns."""

    R = math.radians

    def test_a_member_broken_by_a_hair_and_the_split_turns(self):
        # as on the EC2 trial: d breaks at 60 deg (a piece splits off) and not at 61; b stays a hair under 60
        hinges = {"a": self.R(60.0), "d": self.R(60.06), "b": self.R(59.987), "c": self.R(30.0)}
        note = tool_module.break_band_note(self.R(60.0), self.R(1.0), ["a", "d"], -546.0, 0.157, hinges,
                                           [(self.R(61.0), ["a"], -191.0, 0.0)])
        self.assertTrue(note.startswith(tool_module.EDGE_NOTE + " (the run made again with the break angle +1 deg)"))
        self.assertIn("60 deg: 2 broken (a, d), occupant_space_margin -546 mm, frame_split_fraction 0.157; "
                      "61 deg: 1 broken (a), occupant_space_margin -191 mm, frame_split_fraction 0.000", note)
        self.assertIn("Members that break in one run and not in another: d.", note)
        self.assertIn("Unbroken at 60 deg with a hinge within 1 deg under it: b 59.987 deg.", note)
        self.assertIn("C1's verdict changes inside the band (frame_split_fraction <= 0.03)", note)
        self.assertNotIn("occupant_space_margin >= 0", note)  # NG in both

    def test_a_member_a_hair_under_names_the_edge_without_a_lower_run(self):
        # as on this PC: b ends at 59.987 deg, unbroken at 60 and at 61; the verdicts hold in the two runs
        hinges = {"a": self.R(60.0), "b": self.R(59.987)}
        note = tool_module.break_band_note(self.R(60.0), self.R(1.0), ["a"], -191.0, 0.0, hinges,
                                           [(self.R(61.0), ["a"], -190.0, 0.0)])
        self.assertIn("The same members break in every run.", note)
        self.assertIn("b 59.987 deg", note)
        self.assertTrue(note.endswith("a member a hair under the break angle could still turn them on another computer"))

    def test_the_same_members_and_nothing_near(self):
        note = tool_module.break_band_note(self.R(60.0), self.R(1.0), ["a"], 20.0, 0.0,
                                           {"a": self.R(60.0), "c": self.R(50.0)}, [(self.R(61.0), ["a"], 21.0, 0.0)])
        self.assertTrue(note.endswith("C1's verdicts are the same in these runs"))

    def test_the_margin_turns(self):
        note = tool_module.break_band_note(self.R(60.0), self.R(1.0), ["a"], -3.0, 0.0, {"a": self.R(60.2)},
                                           [(self.R(61.0), [], 6.0, 0.0)])
        self.assertIn("C1's verdict changes inside the band (occupant_space_margin >= 0)", note)


@SLOW
class DemoDesign(unittest.TestCase):
    def test_the_demo_design_runs_and_its_record_says_the_break_angle_edge(self):
        d = json.loads(DEMO.read_text(encoding="utf-8"))
        self.assertEqual((len(d["nodes"]), len(d["members"])), (44, 54))
        t0 = time.perf_counter()
        rec = TOOLS["crash_beam"].run(d, load_case(break_band=q(1.0, "deg")), [{"prediction_id": "pred-0001"}], {})
        wall = time.perf_counter() - t0
        self.assertLess(wall, 2 * 120.0)  # two runs: the break angle and 1 deg above it
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        res = {r["quantity"]: r for r in rec["results"]}
        for name in ("peak_decel", "battery_mount_force", "broken_members", "max_plastic_rotation"):
            self.assertIn(name, res)
        # at 40 km/h, with the front wheels on their tyres, the front rails break; on 1afeaedd's demo v1 (tyre 150
        # N/mm) rail_R_AS1 ended at the break angle's edge (59.987 deg on one computer, past 60 on another, where a
        # piece split off). Which member sits at the edge moves with the design (its tyre, brackets, seats), so the
        # count, the split, the margin's size and the members are not fixed here: the record must give the band's
        # two runs and say what changes between them
        self.assertGreaterEqual(res["broken_members"]["value"], 1.0)
        edge = next(a for a in rec["assumptions"] if a.startswith(tool_module.EDGE_NOTE))
        lim = float(tool_module.HINGE_BREAK_DEG)
        self.assertIn(f"; {lim + 1.0:g} deg: ", edge)  # the run made again 1 deg higher
        self.assertTrue("Members that break in one run and not in another" in edge
                        or "The same members break in every run" in edge, edge)
        self.assertTrue("C1's verdict changes inside the band" in edge or "C1's verdicts are the same" in edge, edge)
        for name in ("mean_decel", "max_crush", "crush_margin", "occupant_space_margin", "occupant_space_left"):
            self.assertIn(name, res)
        # C1-40kmh is NG whichever way the edge goes: the tower posts come into the foot boxes
        self.assertLess(res["occupant_space_margin"]["value"], 0.0)
        self.assertNotIn("occupant_space_margin >= 0", edge)  # the band's note names no flip of C1-40kmh's verdict
        # the vehicle stops (about 70 ms), or, when the edge member breaks and a piece splits off, the piece still moves
        # at the time limit (0.2 s): both are the edge's two sides
        run_note = next(a for a in rec["assumptions"] if a.startswith("run: "))
        self.assertTrue(run_note.startswith(("run: the vehicle stopped", "run: time limit")), run_note)
        if run_note.startswith("run: time limit"):
            self.assertIn("frame_split_time", res)
        k_t = d["variables"]["tyre_stiffness"]["value"]  # the design's own (150 N/mm on 1afeaedd, 60 with tyre60)
        self.assertTrue(any(a.startswith("tyre radial stiffness for the wall contact") and f"{k_t:g} N/mm, the design's "
                            "tyre_stiffness" in a for a in rec["assumptions"]))
        contact_note = next(a for a in rec["assumptions"] if a.startswith("contact between the frame's elements"))
        self.assertIn("pair-steps in contact", contact_note)
        self.assertNotIn(" 0 pair-steps", contact_note)
        # no part box starts past the wall (it is at the front-most of the frame and the boxes on it)
        wall = next(a for a in rec["assumptions"] if a.startswith("the wall: a rigid plane"))
        self.assertIn("x = 415 mm", wall)
        self.assertGreater(res["peak_decel"]["value"], 0.0)
        self.assertIn(res["battery_mount_force"]["where"], {"LS1", "RS1", "LS2", "RS2"})
        self.assertTrue(any(a.startswith("mass 646.2 kg") or "kg: frame" in a for a in rec["assumptions"]))
        self.assertTrue(any("radius 300 mm" in a and "wheel_FL" in a for a in rec["assumptions"]))  # the wheel nodes


if __name__ == "__main__":
    unittest.main()

"""Body-to-ground contact (k2_vehicle.contact): known answers on a rigid box, and the design's points."""

import json
import math
import pathlib
import unittest

import numpy as np

from hiraku.solvers.k2_vehicle.contact import BodyContact, ContactPoint, design_points, rollbar_points
from hiraku.solvers.k2_vehicle.model import box_design, build_vehicle
from hiraku.solvers.k2_vehicle.road import Planes, flat, table_with_floor
from hiraku.solvers.k2_vehicle.sim import Sim, quat_from_euler
from hiraku.solvers.k2_vehicle.shelf import TOOLS
from hiraku.solvers.k2_vehicle.shelf.rollover import RolloverLaneChangeTool, RolloverTiltTool
from hiraku.solvers.k2_vehicle.units import G0
from tests.solvers.k2.slow import SLOW

ROOT = pathlib.Path(__file__).resolve().parents[3]
SIZE = (2.0, 1.0, 0.3)  # the box (m)


def box_sim(dt=1e-3):
    """A rigid 500 kg box whose wheels never touch (a negative radius: the road depth r - z is always
    below 0): a free rigid body for the contact."""
    veh = build_vehicle(box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, susp="rigid", box_size=SIZE))
    sim = Sim(veh, flat(), dt=dt)
    sim.radius = [-100.0] * sim.n
    sim.v = [0.0, 0.0, 0.0]
    return sim


def box_points(sim):
    c = [float(x) for x in sim.veh.cg_s]
    h = [s / 2.0 for s in SIZE]
    return [ContactPoint(f"c{k}", "box", (c[0] + sx * h[0], c[1] + sy * h[1], c[2] + sz * h[2]))
            for k, (sx, sy, sz) in enumerate((sx, sy, sz) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1))]


def place(sim, con, roll, pitch, gap):
    """Attitude roll / pitch, the lowest contact point `gap` above the ground, at rest."""
    sim.q = quat_from_euler(roll, pitch, 0.0)
    sim.p = [0.0, 0.0, 10.0]
    sim.w = [0.0, 0.0, 0.0]
    sim.v = [0.0, 0.0, 0.0]
    con.ext()
    low = min(p[2] for p in con.pos)
    sim.p[2] += gap - low


def ctl(sim):
    return sim.no_controls()


class RigidBoxTest(unittest.TestCase):
    def test_box_is_a_free_rigid_body(self):
        sim = box_sim()
        self.assertEqual(sim.m, 0)
        self.assertAlmostEqual(sim.m_dof, 500.0)

    def test_corner_impact_impulse(self):
        """Elastic (no damping), frictionless, no gravity: one corner hits at 2 m/s. The impulse is
        J = (1 + e) m_eff v with e = 1 and 1 / m_eff = 1 / m + (r x n) . I^-1 (r x n) (rigid-body
        impact, I in world axes at the hit); the kinetic energy comes back."""
        sim = box_sim(dt=1e-5)
        sim.set_gravity([0.0, 0.0, 0.0])
        con = BodyContact(sim, box_points(sim), mu=0.0, k=2e6, c=0.0)
        place(sim, con, 0.3, 0.2, 1e-4)
        v0 = 2.0
        sim.v = [0.0, 0.0, -v0]
        con.ext()
        low = int(np.argmin([p[2] for p in con.pos]))
        r = np.array(con.pos[low]) - np.array(sim.p)
        rot = sim.rotation()
        iw = rot @ np.array(sim.i_s) @ rot.T
        n = np.array([0.0, 0.0, 1.0])
        rn = np.cross(r, n)
        m = sim.m_dof
        m_eff = 1.0 / (1.0 / m + rn @ np.linalg.solve(iw, rn))
        ke0 = 0.5 * m * v0 * v0
        j, touched = 0.0, False
        for _ in range(200000):
            ext = con.ext()
            fz = con.total()[2]
            j += fz * sim.dt
            if ext:
                touched = True
            elif touched:
                break
            sim.step(ctl(sim), ext)
        self.assertTrue(touched)
        self.assertEqual(sum(con.touch), 0)
        self.assertLess(abs(j - 2.0 * m_eff * v0) / (2.0 * m_eff * v0), 0.02)
        w = np.array(sim.w)
        ke1 = 0.5 * m * float(np.dot(sim.v, sim.v)) + 0.5 * float(w @ np.array(sim.i_s) @ w)
        self.assertLess(abs(ke1 - ke0) / ke0, 0.01)
        self.assertLess(abs(sim.v[0]) + abs(sim.v[1]), 1e-9)  # no friction: no sideways momentum
        self.assertAlmostEqual(sim.v[2] + v0, j / m, places=9)  # the contact force is all that moved it

    def test_dropped_on_a_corner_comes_to_rest_on_its_face(self):
        """Dropped 50 mm onto a corner (damping, friction 0.8, gravity): it ends flat on its bottom face,
        the centre at h / 2 - m g / (4 k) (four corners share the weight), the contact carrying m g."""
        sim = box_sim()
        con = BodyContact(sim, box_points(sim), mu=0.8)
        place(sim, con, 0.15, 0.1, 0.05)
        for _ in range(4000):
            sim.step(ctl(sim), con.ext())
        m = sim.m_dof
        roll, pitch, _ = sim.attitude()
        self.assertLess(abs(math.degrees(roll)), 0.01)
        self.assertLess(abs(math.degrees(pitch)), 0.01)
        z_exp = SIZE[2] / 2.0 - m * G0 / (4.0 * con.k)
        self.assertLess(abs(sim.p[2] - z_exp), 1e-4)
        con.ext()
        self.assertLess(abs(con.total()[2] - m * G0) / (m * G0), 1e-3)
        self.assertEqual(sum(con.touch), 4)
        self.assertLess(math.sqrt(sum(x * x for x in sim.v)), 1e-3)

    def test_sliding_on_its_points_decelerates_at_mu_g(self):
        """Flat on the ground, pushed to 5 m/s along x: it slides at a = mu g and stops after
        v^2 / (2 mu g)."""
        mu, v0 = 0.8, 5.0
        sim = box_sim()
        con = BodyContact(sim, box_points(sim), mu=mu)
        place(sim, con, 0.0, 0.0, 0.0)
        for _ in range(1000):
            sim.step(ctl(sim), con.ext())
        x0 = sim.p[0]
        sim.v = [v0, 0.0, sim.v[2]]
        vs = []
        for _ in range(1500):
            sim.step(ctl(sim), con.ext())
            vs.append(sim.v[0])
        a = (vs[99] - vs[499]) / 0.4
        self.assertLess(abs(a - mu * G0) / (mu * G0), 5e-3)
        d_exp = v0 * v0 / (2.0 * mu * G0)
        self.assertLess(abs((sim.p[0] - x0) - d_exp) / d_exp, 0.01)
        self.assertLess(abs(sim.v[0]), 0.01)


class PlanesTest(unittest.TestCase):
    def test_box_slides_down_an_incline_at_g_sin_minus_mu_cos(self):
        """A Planes road inclined 20 deg about x (gravity vertical): the box on its four bottom corners
        slides down at a = g (sin 20 - mu cos 20) with mu 0.2, each plane force along its own normal."""
        th, mu = math.radians(20.0), 0.2
        sim = box_sim()
        sim.road = Planes([((0.0, 0.0, 0.0), (0.0, -math.sin(th), math.cos(th)))])  # rises toward +y
        con = BodyContact(sim, box_points(sim), mu=mu)
        place(sim, con, th, 0.0, 0.0)  # the box rolled 20 deg: its bottom parallel to the plane
        con.ext()
        sim.p[2] -= min(-d for d in con.depth) / math.cos(th)  # the lowest corner onto the plane
        vs = []
        for _ in range(800):
            sim.step(ctl(sim), con.ext())
            vs.append(sim.v[1] * math.cos(th) + sim.v[2] * math.sin(th))  # along the plane, up the slope
        a = -(vs[699] - vs[299]) / 0.4
        a_exp = G0 * (math.sin(th) - mu * math.cos(th))
        self.assertLess(abs(a - a_exp) / a_exp, 5e-3)

    def test_table_with_floor(self):
        road = table_with_floor(math.radians(40.0), -1.0)
        self.assertAlmostEqual(road.height(0.0, 0.5), 0.0)
        self.assertAlmostEqual(road.height(0.0, -2.0), math.tan(math.radians(40.0)))  # the floor rises past the hinge
        c = road.contacts([(0.0, -1.0, 0.3)], [0.3])[0]
        self.assertEqual(len(c), 2)
        self.assertAlmostEqual(c[0][6], 0.0)  # a wheel just touching the table at the hinge


class RolloverToolsTest(unittest.TestCase):
    def test_own_checks_pass(self):
        for tool in (RolloverTiltTool(), RolloverLaneChangeTool()):
            checks = tool.own_checks()
            self.assertEqual(len(checks), 2)
            for c in checks:
                self.assertTrue(c["pass"], c)

    def test_params_are_declared_once(self):
        for tool in (RolloverTiltTool(), RolloverLaneChangeTool()):
            names = [p["name"] for p in tool.describe()["params"]]
            self.assertEqual(len(names), len(set(names)))

    def test_not_in_tools(self):
        """The rollover tools are not on the catalog's shelf: TOOLS (and so every existing tool) is unchanged."""
        self.assertNotIn("rollover_tilt", TOOLS)
        self.assertNotIn("rollover_lane_change", TOOLS)

    @SLOW
    def test_tilt_rollover_comes_to_rest_on_its_side(self):
        """The tilt check vehicle (a 500 kg box on springs, no roll bar: its hull and wheel rings touch) turned
        past its tip angle falls off the table and comes to rest on the floor, turned over (on its roof: on
        its side the CG is outside the wheel face and the box's top corner)."""
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, k_w=20.0, wheel_mass=20.0, travel=200.0,
                       box_size=(2.0, 1.0, 0.6), name="tilt rollover check")
        rec = RolloverTiltTool().run(d, {"load_case_id": "S1-test", "params": {"direction": "right"}}, [],
                                     {"stiffness_study": False, "record_hz": 10.0})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        res = {r["quantity"]: r["value"] for r in rec["results"]}
        self.assertEqual(res["rollover"], 1.0)
        self.assertEqual(res["came_to_rest"], 1.0)
        self.assertGreater(res["rest_tilt"], 70.0)
        self.assertLess(res["max_contact_depth"], 0.05)
        n = {len(s["values"]) for s in rec["series"] if "t_s" in s}
        self.assertEqual(len(n), 1)  # every time channel has a value at every sample


class DesignPointsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = json.loads((ROOT / "data/designs/offroad-4w-v1.json").read_text(encoding="utf-8"))

    def test_rollbar_points_lie_on_the_members(self):
        pts = rollbar_points(self.design)
        self.assertTrue(pts)
        nodes = {n["id"]: np.asarray(n["xyz_mm"], dtype=float) / 1000.0 for n in self.design["nodes"]}
        mbs = {m["id"]: m for m in self.design["members"]}
        for p in pts:
            m = mbs[p.part_id]
            a, b = nodes[m["node_a"]], nodes[m["node_b"]]
            u = (np.array(p.design) - a)
            self.assertLess(float(np.linalg.norm(np.cross(u, b - a))) / float(np.linalg.norm(b - a)), 1e-9)
        top = max(p.design[2] for p in pts)
        self.assertAlmostEqual(top, max(float(n["xyz_mm"][2]) for n in self.design["nodes"]) / 1000.0)

    def test_hull_points_enclose_the_body(self):
        pts = design_points(self.design)
        hull = [p for p in pts if p.group != "rollbar"]
        self.assertTrue(hull)
        names = [p.name for p in pts]
        self.assertEqual(len(names), len(set(names)))
        allz = [float(n["xyz_mm"][2]) / 1000.0 for n in self.design["nodes"]]
        self.assertLessEqual(min(p.design[2] for p in pts), min(allz) + 1e-9)


if __name__ == "__main__":
    unittest.main()

"""rollover_beam (R4 judged by a rollover): the hand-off of K2's rigid state to the beam mesh, the room measure, the
run's stop hook, and (slow) the tool on the demo design."""

import json
import math
import types
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract
from hiraku.contract.loading import on_board
from hiraku.solvers.k2_vehicle.crash_beam import build_mesh, measure, sim
from hiraku.solvers.k2_vehicle.model import build_vehicle
from hiraku.solvers.k2_vehicle.scenario import new_sim
from hiraku.solvers.k2_vehicle.shelf import TOOLS
from hiraku.solvers.k2_vehicle.shelf import rollover_beam as rb
from hiraku.solvers.k2_vehicle.sim import quat_from_euler
from tests.solvers.k2.slow import SLOW
from tests.solvers.k2.test_crash_beam import known_rotation, small_frame

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "data" / "designs" / "offroad-4w-v1.json"


def demo():
    return on_board(json.loads(DEMO.read_text(encoding="utf-8")), "two_cargo")


def rolling_sim(design):
    """K2's vehicle of the design turned 58 deg in roll, moving and turning, the wheels moving on their travel."""
    veh = build_vehicle(design)
    s = new_sim(veh, dt=1e-3, settle_s=0.0)
    s.q = quat_from_euler(math.radians(58.0), math.radians(3.0), math.radians(-2.0))
    s.v = [0.04, -2.5, -0.3]
    s.w = [1.6, -0.11, 0.06]
    s.s = [0.01 * (i - 1) for i in range(s.n)]
    s.sd = [0.1 * (i + 1) for i in range(s.n)]
    return s


class K2Momentum(unittest.TestCase):
    def test_against_finite_differences_of_the_mass_points(self):
        """Hand calculation: the sprung centre and the unsprung point masses at the wheel centres, their positions at
        t - h and t + h (p + v t, R exp(omega t), s + s' t), central differences for their velocities (h = 1e-6 s):
        P = sum m v, L = R I_s omega + sum (x - c) x m v, E = sum m v^2 / 2 + omega I_s omega / 2 (1e-6 relative)."""
        s = rolling_sim(demo())
        got = rb.k2_momentum(s)
        R0, p0, v, w = s.rotation(), np.array(s.p), np.array(s.v), np.array(s.w)
        m = [float(s.veh.m_s)] + [float(x) for x in s.mu_ if x > 0.0]
        ids = [i for i in range(s.n) if s.mu_[i] > 0.0]

        def points(t):
            R = R0 @ known_rotation(w, float(np.linalg.norm(w)) * t)
            p = p0 + v * t
            return np.array([p] + [p + R @ np.array(s._path(i, s.s[i] + s.sd[i] * t)[:3]) for i in ids])

        h = 1e-6
        x, vel = points(0.0), (points(h) - points(-h)) / (2 * h)
        m = np.array(m)
        c = m @ x / m.sum()
        P = m @ vel
        I_s = np.array(s.i_s)
        L = R0 @ (I_s @ w) + np.cross(x - c, m[:, None] * vel).sum(axis=0)
        E = 0.5 * float(m @ (vel * vel).sum(axis=1) + w @ I_s @ w)
        self.assertAlmostEqual(got["M"], float(m.sum()), places=9)
        np.testing.assert_allclose(got["c"], c, atol=1e-9)
        np.testing.assert_allclose(got["P"], P, rtol=0, atol=1e-6 * np.linalg.norm(P))
        np.testing.assert_allclose(got["L"], L, rtol=0, atol=1e-6 * np.linalg.norm(L))
        self.assertAlmostEqual(got["E"] / E, 1.0, delta=1e-6)


class Handoff(unittest.TestCase):
    def test_mesh_inertia_of_four_points(self):
        """1 kg at (+-1, 0, 0), 2 kg at (0, +-0.5, 0), 0.1 kg m^2 of own inertia each: about the centre (the origin)
        I = diag(2 x 0.25 x 2, 2, 2 + 1) + 0.4 = diag(1.4, 2.4, 3.4)."""
        mesh = types.SimpleNamespace(mass=np.array([1.0, 1.0, 2.0, 2.0]), inertia=np.full(4, 0.1),
                                     x0=np.array([[1.0, 0, 0], [-1.0, 0, 0], [0, 0.5, 0], [0, -0.5, 0]]))
        np.testing.assert_allclose(rb.mesh_inertia(mesh), np.diag([1.4, 2.4, 3.4]), atol=1e-15)
        # handed P and L in the road frame with the body turned by R: u = R^T P / M, omega = I^-1 R^T L
        R = known_rotation(np.array([0.3, -1.0, 0.4]), 1.1)
        mom = {"P": np.array([6.0, -3.0, 1.5]), "L": np.array([0.7, 2.0, -1.2]), "c": np.array([5.0, 1.0, 0.2])}
        ho = rb.handoff(mesh, R, np.zeros(3), np.zeros(3), mom)
        np.testing.assert_allclose(ho["velocity"], R.T @ mom["P"] / 6.0, atol=1e-15)
        np.testing.assert_allclose(ho["spin"], (R.T @ mom["L"]) / np.array([1.4, 2.4, 3.4]), atol=1e-14)
        np.testing.assert_allclose(ho["c_k2"], R.T @ mom["c"], atol=1e-14)

    def test_the_demo_mesh_takes_k2s_momentum_and_angular_momentum(self):
        """K2's demo vehicle rolling at 58 deg handed to the demo design's beam mesh: the mesh's starting state has
        K2's linear momentum and K2's angular momentum about the mass centre (each about its own), in the road frame
        (1e-9 relative); its mass is K2's whole mass (sprung + unsprung)."""
        d = demo()
        s = rolling_sim(d)
        mom = rb.k2_momentum(s)
        mesh = build_mesh(d)
        R = s.rotation()
        ho = rb.handoff(mesh, R, s.p, s.veh.cg_s, mom)
        st = sim.new_state(mesh, ho["velocity"], None, spin=ho["spin"], about=ho["about"])
        P, L = rb.state_momentum(mesh, st, ho["about"])
        np.testing.assert_allclose(R @ P, mom["P"], rtol=0, atol=1e-9 * np.linalg.norm(mom["P"]))
        np.testing.assert_allclose(R @ L, mom["L"], rtol=0, atol=1e-9 * np.linalg.norm(mom["L"]))
        self.assertAlmostEqual(float(mesh.mass.sum()) / mom["M"], 1.0, delta=1e-9)

    def test_the_landing_starts_from_the_handed_state_and_falls_freely_before_the_ground(self):
        """The landing's first record row is the handed rigid motion; with the plane far away (10 m ahead along the
        direction the frame moves into it) the momentum grows by M g t (1e-9) and the angular momentum about the moving mass centre stays (1e-6)
        over 2 ms."""
        d = demo()
        s = rolling_sim(d)
        mesh = build_mesh(d)
        R = s.rotation()
        tch = {"R": R, "p": np.array(s.p), "cg_s": np.array(s.veh.cg_s), "mom": rb.k2_momentum(s),
               "gravity": np.array([0.0, -6.7, -7.16]), "point_design": np.array(s.veh.cg_s),
               "plane": (np.zeros(3), np.array([0.0, 0.0, 1.0]))}
        ho = rb.handoff(mesh, R, tch["p"], tch["cg_s"], tch["mom"])
        g_b = R.T @ tch["gravity"]
        run = sim.run_crash(mesh, 0.0, 2e-3, wall_dir=-(R.T @ np.array([0.0, 0.0, 1.0])), wall=10.0,
                            velocity=ho["velocity"], spin=ho["spin"], about=ho["about"], gravity=g_b, until_limit=True)
        np.testing.assert_allclose(run.velocities[0], ho["velocity"] + np.cross(ho["spin"], mesh.x0 - ho["about"]),
                                   atol=1e-13)
        T = float(run.t[-1])
        M = run.total_mass
        np.testing.assert_allclose(run.momentum[-1] - run.momentum[0], M * g_b * T, rtol=0,
                                   atol=1e-9 * M * 9.81 * T)
        x, v = run.positions[-1], run.velocities[-1]
        c = mesh.mass @ x / M
        L_end = np.cross(x - c, mesh.mass[:, None] * v).sum(axis=0) + mesh.inertia @ run.final.w
        P0, L0 = rb.state_momentum(mesh, sim.new_state(mesh, ho["velocity"], None, spin=ho["spin"],
                                                       about=ho["about"]), ho["about"])
        np.testing.assert_allclose(L_end, L0, rtol=0, atol=1e-6 * np.linalg.norm(L0))


class RoomToBoxes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = on_board(small_frame(), "two_cargo")
        cls.mesh = build_mesh(cls.design)
        cls.crash = sim.run_crash(cls.mesh, 0.0, 3e-4, wall_dir=(0.0, 0.0, -1.0), wall=10.0,
                                  velocity=(0.0, 0.0, 0.0), until_limit=True)

    def test_a_box_behind_the_dash(self):
        """The small frame's dash (LD-RD along y at x = -0.3 m, z = 0.95 m) and a box 0.2 m cube centred at x = -0.6,
        y = 0, z = 0.95: the dash's axis is 0.2 m from the box's front face (x = -0.5); with a radius of 0.02 m the
        room is 0.18 m (every other member is further), at the start and over the rows."""
        k = self.mesh.design_nodes["LM"]
        box = {"id": "b", "center_mm": [-600.0, 0.0, 950.0], "size_mm": [200.0, 200.0, 200.0], "shares": {k: 1.0}}
        rad = np.full(self.mesh.ne, 0.02)
        got = measure.room_to_boxes(self.crash, self.crash.positions, [box], self.mesh.frame_elements(), rad)["b"]
        self.assertAlmostEqual(got["room"], 0.18, places=9)
        self.assertAlmostEqual(got["start"], 0.18, places=9)
        self.assertEqual(self.mesh.member_of[got["element"]], "dash")
        self.assertAlmostEqual(got["by_member"]["dash"], 0.18, places=9)
        self.assertEqual(len(got["series"]), len(self.crash.t_all))
        self.assertEqual(got["size_mm"], [200.0, 200.0, 200.0])

    def test_depth_past_the_face_it_came_in_through(self):
        """A bar 100 mm long along x (radius 10 mm) moving down y through a 200 mm cube at the origin, its axis at
        y = 300, 100, 0, -50, -150 mm. Room (minus the depth to the nearest face inside): 190, -10, -110, -60, +40 mm:
        it bottoms out at the middle. The depth past the +y face it came in through (100 + 10 - y): it entered at the
        second row (10 mm), then 110, 160, 260 mm (the last row past the far face, still counted)."""
        ys = [0.3, 0.1, 0.0, -0.05, -0.15]
        x0 = np.array([[-0.05, 0.3, 0.0], [0.05, 0.3, 0.0], [0.0, 0.0, 0.0]])
        body = np.array([[[-0.05, y, 0.0], [0.05, y, 0.0], [0.0, 0.0, 0.0]] for y in ys])
        mesh = types.SimpleNamespace(con=np.array([[0, 1]]), l0=np.array([0.1]), member_of=["bar"], x0=x0)
        run = types.SimpleNamespace(mesh=mesh, broken_at={}, t_all=np.arange(len(ys)) * 1e-3)
        box = {"id": "b", "center_mm": [0.0, 0.0, 0.0], "size_mm": [200.0, 200.0, 200.0], "shares": {2: 1.0}}
        got = measure.room_to_boxes(run, body, [box], [0], np.array([0.01]))["b"]
        np.testing.assert_allclose(got["series"], [0.19, -0.01, -0.11, -0.06, 0.04], atol=1e-12)
        self.assertAlmostEqual(got["room"], -0.11, places=12)
        self.assertEqual(got["row"], 2)
        dp = got["depth"]["bar"]
        self.assertEqual((dp["face"], dp["entered_row"], dp["row"], dp["element"]), ("+y", 1, 4, 0))
        self.assertAlmostEqual(dp["depth"], 0.26, places=12)

    def test_k4_radius_is_half_the_diagonal(self):
        """k4_geometry.rollbar.member_radius of the 40 x 40 tube: sqrt(40^2 + 40^2) / 2 = 28.28 mm."""
        rad = rb.k4_radius(self.design, self.mesh)
        e = next(e for e in self.mesh.frame_elements() if self.mesh.member_of[e] == "dash")
        self.assertAlmostEqual(rad[e], math.hypot(0.04, 0.04) / 2.0, places=12)


class StopHook(unittest.TestCase):
    def test_stop_when_ends_the_run_with_its_reason(self):
        mesh = build_mesh(on_board(small_frame(), "two_cargo"))

        def stop(m, st, imp, chunk):
            return "enough" if st.t >= 1e-3 else None

        run = sim.run_crash(mesh, 0.0, 5e-3, wall_dir=(0.0, 0.0, -1.0), wall=10.0, velocity=(0.0, 0.0, 0.0),
                            until_limit=True, stop_when=stop)
        self.assertEqual(run.stopped, "enough")
        self.assertAlmostEqual(float(run.t[-1]), 1e-3, delta=run.chunk)


class ToolShape(unittest.TestCase):
    def test_on_the_shelf_for_r4_at_stage_3_with_its_pairs(self):
        tool = TOOLS["rollover_beam"]
        self.assertEqual((tool.tests, tool.stage, tool.planned_only), (["R4"], 3, False))
        pairs = {(p["quantity"], p["formula_id"], p["output"]) for p in tool.describe()["predicts"]}
        self.assertIn(("rops_occupant_clearance", "frame_static.rops_occupant_clearance", "rops_occupant_clearance"),
                      pairs)
        self.assertIn(("unsupported_load", "load_path.free_parts", "n"), pairs)
        # no lighter tool on the shelf covers R4 (it would take R4's stage)
        self.assertEqual([n for n, t in TOOLS.items() if "R4" in t.covers()], ["rollover_beam"])

    def test_every_own_check_passes(self):
        checks = TOOLS["rollover_beam"].own_checks()
        self.assertEqual([c["quantity"] for c in checks][-2:], ["handoff_P", "handoff_L"])
        for c in checks:
            with self.subTest(case=c["case"]):
                self.assertTrue(c["pass"], f"{c['case']}: expected {c['expected']} got {c['got']}")
                self.assertEqual(contract.errors("tool_check", c), [])


@SLOW
class DemoDesign(unittest.TestCase):
    def test_the_demo_design_rolls_onto_its_roll_bar(self):
        """The demo design through the tool on R4's builder's load case (the judged run): the record fits the contract,
        the criterion's quantity and the head and trunk rooms are there, the roll bar touched (no fallback), the
        hand-off and the unchecked constants are written down; the plan's static hand value of the criterion misses
        the tool's by more than the tolerance, as its expected_mismatch says (K2's run 2026-11-06: -16 mm against
        +31 mm)."""
        from hiraku import solvers
        from hiraku.catalog import load_catalog, load_requirements
        from hiraku.catalog.builders import BUILDERS, PLANS

        design = json.loads(DEMO.read_text(encoding="utf-8"))
        req = load_requirements(ROOT / "data" / "requirements" / "offroad-4w.yaml")
        lc = BUILDERS["R4"](load_catalog().test("R4"), req, design)[0]  # R4-rollover (the soil)
        rec = TOOLS["rollover_beam"].run(design, lc, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        plan = PLANS[lc["load_case_id"]]
        formulas, preds = solvers.formula_registry(), {}
        for p in plan["predictions"]:
            extra = p["inputs_from"](design, lc)
            refs = {"from": extra, "lc": lc["params"], "pred": preds}
            ins = {k: refs[r.partition(".")[0]][r.partition(".")[2]] for k, r in p["inputs"].items()}
            preds[p["quantity"]] = formulas.evaluate(p["formula_id"], ins)["outputs"][p["output"]]
        self.assertEqual(preds["unsupported_load"]["value"], 0.0)
        hand = preds["rops_occupant_clearance"]["value"]
        got = next(r["value"] for r in rec["results"] if r["quantity"] == "rops_occupant_clearance")
        self.assertGreater(abs(got - hand) / abs(hand), lc["tolerance_rel"], (got, hand))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        res = {r["quantity"]: r for r in rec["results"]}
        for q in ("rops_occupant_clearance", "head_room", "trunk_room", "rollbar_top_displacement",
                  "ground_force_peak", "broken_members", "frame_split_fraction", "unsupported_load"):
            self.assertIn(q, res)
        self.assertEqual(res["rops_occupant_clearance"]["unit"], "mm")
        self.assertIn("space", res["rops_occupant_clearance"]["where"])
        self.assertTrue(res["rollbar_touch_time"]["where"].startswith("rollbar_"))
        text = " ".join(rec["assumptions"])
        for s in ("the hand-off", "確かめていない", "1000 N/mm", "60 deg", "friction 2",
                  "HIRAKU 独自の計算の確かめ。規格の試験ではない", "ISO 3471", "ROHVA", "「HIRAKU の仮定」",
                  "the deepest intrusion into"):
            self.assertIn(s, text)
        self.assertNotIn("規格に基づく", text)
        self.assertTrue(any(h["quantity"] == "rops_occupant_clearance" for h in rec.get("hot_spots", [])))


if __name__ == "__main__":
    unittest.main()

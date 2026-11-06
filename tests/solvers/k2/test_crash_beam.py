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
        self.assertEqual(len(checks), 5)
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
        for k in ("x", "v", "R", "w", "up", "tp", "thp", "fint", "imp"):
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
        self.assertEqual(len(f["members"]), self.mesh.ne)
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

    def test_a_large_piece_breaking_off_stops_the_run(self):
        """At 40 km/h the side rails break behind the occupants' feet and a piece of 7.9 % of the mass comes apart
        from the seats' side: with no contact between pieces the rest would pass through it, so the run stops."""
        r = self.res()
        self.assertIn("frame_split_time", r)
        self.assertEqual(r["frame_split_time"]["unit"], "s")
        self.assertTrue(r["frame_split_time"]["where"])
        self.assertIn("crush_at_split", r)
        for name in ("mean_decel", "max_crush", "crush_margin", "occupant_space_left"):
            self.assertNotIn(name, r)
        # C1's two quantities: the least room up to the split (an upper bound) and the broken-off piece's share
        self.assertIn("occupant_space_margin", r)
        self.assertGreater(r["frame_split_fraction"]["value"], 0.03)
        notes = self.rec["assumptions"]
        self.assertTrue(any("split" in n and "not computed" in n for n in notes), notes)
        self.assertTrue(any("smaller pieces" in n for n in notes), notes)  # the front cross member, 0.5 %
        t_split = r["frame_split_time"]["value"]
        self.assertLessEqual(self.rec["series"][0]["t_s"][-1], t_split + 1e-6)  # the series' t_s are to 1 us

    def test_member_series_at_the_node_motion_frames(self):
        series = {s["channel"]: s for s in self.whole["series"]}
        force = [c for c in series if c.startswith("member_axial_force.")]
        hinge = [c for c in series if c.startswith("member_hinge.")]
        self.assertEqual(len(force), len(set(build_mesh(self.d).member_of)))
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
        rec = TOOLS["crash_beam"].run(self.d, load_case(20.0, wall_mu=q(0.0, "1"), hinge_break_angle=q(1000.0, "deg")),
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


@SLOW
class DemoDesign(unittest.TestCase):
    def test_the_demo_design_runs_in_under_two_minutes(self):
        d = json.loads(DEMO.read_text(encoding="utf-8"))
        self.assertEqual((len(d["nodes"]), len(d["members"])), (44, 54))
        t0 = time.perf_counter()
        rec = TOOLS["crash_beam"].run(d, load_case(), [{"prediction_id": "pred-0001"}], {})
        wall = time.perf_counter() - t0
        self.assertLess(wall, 120.0)
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        res = {r["quantity"]: r for r in rec["results"]}
        for name in ("peak_decel", "battery_mount_force", "broken_members", "max_plastic_rotation"):
            self.assertIn(name, res)
        # at 40 km/h the tower posts break and the towers and the dash (4.6 % of the mass) come apart from the
        # seats' side at 66 ms: the run stops there (the pieces have no contact with each other)
        self.assertAlmostEqual(res["frame_split_time"]["value"], 0.066, delta=0.005)
        self.assertIn("tower_post", res["frame_split_time"]["where"])
        # C1 by this tool: the dash goes into the foot box before the split, and the towers and dash break off
        self.assertLess(res["occupant_space_margin"]["value"], 0.0)
        self.assertAlmostEqual(res["frame_split_fraction"]["value"], 0.046, delta=0.005)
        self.assertGreater(res["crush_at_split"]["value"], 0.0)
        # no part box starts past the wall (it is at the front-most of the frame and the boxes on it)
        wall = next(a for a in rec["assumptions"] if a.startswith("the wall: a rigid plane"))
        self.assertIn("x = 415 mm", wall)
        self.assertGreater(res["peak_decel"]["value"], 0.0)
        self.assertIn(res["battery_mount_force"]["where"], {"LS1", "RS1", "LS2", "RS2"})
        self.assertTrue(any(a.startswith("mass 646.2 kg") or "kg: frame" in a for a in rec["assumptions"]))


if __name__ == "__main__":
    unittest.main()

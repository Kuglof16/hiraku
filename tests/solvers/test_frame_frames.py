"""Frames over time: K2's mount force series into frame_static / frame_shell (field.frames)."""

import copy
import math
import unittest

import numpy as np

from hiraku import contract, solvers
from hiraku.solvers.k0_handcalc.rigid import rpy_matrix
from hiraku.solvers.k0_handcalc.units import STANDARD_GRAVITY
from hiraku.solvers.k0_handcalc.vehicle import DesignError, support_loads
from hiraku.solvers.k1_frame import analyze
from hiraku.solvers.k1_frame.analyze import _split, mount_carriers, sprung_items
from hiraku.solvers.k1_shell import build_mesh
from hiraku.solvers.k1_shell.frame import ShellFrame, analyze_shell
from hiraku.solvers.k0_handcalc.vehicle import mass_summary
from hiraku.solvers.shelf import fixtures
from hiraku.solvers.shelf.frame_static import upstream_mount_series

TIMES = [0.0, 0.1, 0.2]
PITCH = [0.0, 5.0, 10.0]  # deg, the body nose-down in world (rpy_matrix's pitch about +y)
SIZE = {"element_size": {"value": 20.0, "unit": "mm"}}


def tool(name):
    return {t.name: t for t in solvers.builtin_tools()}[name]


def ladder():
    """The fixture ladder with one 'contact' hard point per support, at its contact point."""
    d = fixtures.ladder_design()
    st = support_loads(d)
    for s in d["supports"]:
        p = st["supports"][s["id"]].get("contact_point", st["supports"][s["id"]]["point_mm"])
        s["suspension"]["hardpoints_mm"] = {"contact": [float(x) for x in p]}
    return d, st


def series_analysis(design, st, scale=(1.0, 1.0, 1.0), torque_Nm=0.0):
    """An upstream analysis as K2 writes it: each support's ground force at rest (world axes, vertical) at
    its 'contact' mount, the body pitched by PITCH, the inertia force balancing them."""
    ch = []
    w = mass_summary(design)["mass_kg"] * STANDARD_GRAVITY
    for s in design["supports"]:
        f = np.asarray(st["supports"][s["id"]]["force_N"], float)
        for a, i in zip("xyz", range(3)):
            ch.append({"channel": f"mount_{a}.{s['id']}.contact", "unit": "N", "t_s": TIMES,
                       "values": [float(f[i]) * k for k in scale]})
        ch.append({"channel": f"body_torque_y.{s['id']}", "unit": "N*m", "t_s": TIMES, "values": [torque_Nm] * 3})
    total = [sum(float(st["supports"][s["id"]]["force_N"][2]) * k for s in design["supports"]) for k in scale]
    ch += [{"channel": "sprung_roll", "unit": "deg", "t_s": TIMES, "values": [0.0] * 3},
           {"channel": "sprung_pitch", "unit": "deg", "t_s": TIMES, "values": PITCH},
           {"channel": "sprung_yaw", "unit": "deg", "t_s": TIMES, "values": [0.0] * 3},
           {"channel": "inertia_force_x", "unit": "N", "t_s": TIMES, "values": [0.0] * 3},
           {"channel": "inertia_force_y", "unit": "N", "t_s": TIMES, "values": [0.0] * 3},
           {"channel": "inertia_force_z", "unit": "N", "t_s": TIMES, "values": [w - f for f in total]}]
    return {"status": "ok", "analysis_id": "ana-0003", "series": ch, "results": []}


class SeriesTest(unittest.TestCase):
    def test_body_axes_units_and_balance(self):
        d, st = ladder()
        lc, ana, inst = upstream_mount_series({"P1-x": series_analysis(d, st, torque_Nm=40.0)}, d)
        self.assertEqual((lc, ana, [i["t_s"] for i in inst]), ("P1-x", "ana-0003", TIMES))
        for i, pitch in zip(inst, PITCH):
            Rt = rpy_matrix([0.0, pitch, 0.0]).T
            np.testing.assert_allclose(i["gravity_mps2"], Rt @ [0.0, 0.0, -STANDARD_GRAVITY], atol=1e-12)
            f = np.asarray(st["supports"]["FL"]["force_N"], float)
            (kind, p, fb), = i["mount_loads"]["FL"]["vectors"]
            self.assertEqual(kind, "contact")
            np.testing.assert_allclose(p, d["supports"][0]["suspension"]["hardpoints_mm"]["contact"])
            np.testing.assert_allclose(fb, Rt @ f, atol=1e-9)
            np.testing.assert_allclose(i["mount_loads"]["FL"]["moment_Nmm"], Rt @ [0.0, 40e3, 0.0], atol=1e-9)
        self.assertAlmostEqual(abs(inst[2]["gravity_mps2"][0]), STANDARD_GRAVITY * math.sin(math.radians(10.0)),
                               places=12)

    def test_none_and_errors(self):
        d, st = ladder()
        ana = series_analysis(d, st)
        self.assertIsNone(upstream_mount_series(None, d))
        no_att = dict(ana, series=[c for c in ana["series"] if c["channel"] != "sprung_yaw"])
        self.assertIsNone(upstream_mount_series({"P1-x": no_att}, d))
        self.assertIsNone(upstream_mount_series({"P1-x": dict(ana, status="failed")}, d))
        bare = copy.deepcopy(d)
        del bare["supports"][1]["suspension"]["hardpoints_mm"]
        with self.assertRaisesRegex(DesignError, "FR.contact"):
            upstream_mount_series({"P1-x": ana}, bare)
        shifted = copy.deepcopy(ana)
        next(c for c in shifted["series"] if c["channel"] == "mount_z.RR.contact")["t_s"] = [0.0, 0.1, 0.3]
        with self.assertRaisesRegex(DesignError, "mount_z.RR.contact"):
            upstream_mount_series({"P1-x": shifted}, d)

    def test_sprung_items_follow_k2(self):
        d = fixtures.ladder_design()
        d["parts"] += [
            {"id": "wheel_X", "name": "車輪", "kind": "wheel", "mass_kg": 10.0, "carried_by": ["L0"],
             "box": {"center_mm": [0.0, 400.0, 300.0], "size_mm": [100.0, 100.0, 100.0]}},
            {"id": "arm_X", "name": "腕", "kind": "suspension", "mass_kg": 6.0, "carried_by": ["L0"],
             "box": {"center_mm": [0.0, 400.0, 300.0], "size_mm": [100.0, 100.0, 100.0]}}]
        items = mass_summary(d)["items"]
        got = {it.id: it.mass_kg for it in sprung_items(d, items)}
        self.assertNotIn("part:wheel_X", got)
        self.assertEqual(got["part:arm_X"], 3.0)
        self.assertEqual(got["part:payload"], next(it.mass_kg for it in items if it.id == "part:payload"))


class SameAsGroundForcesTest(unittest.TestCase):
    """At level attitude the mount path with the ground forces at the contact points is the ground path."""

    def setUp(self):
        self.d, st = ladder()
        self.ground = {sid: _split(s.get("contact_point", s["point_mm"]), s["force_N"], s["contact"])
                       for sid, s in st["supports"].items()}
        _, _, inst = upstream_mount_series({"P1-x": series_analysis(self.d, st)}, self.d)
        self.inst = inst[0]

    def test_beam(self):
        a = analyze(self.d, ground_forces=self.ground)
        b = analyze(self.d, mount_loads=self.inst["mount_loads"], gravity_mps2=self.inst["gravity_mps2"],
                    inertia_N=self.inst["inertia_N"])
        for m, s in a["stresses"].items():
            self.assertAlmostEqual(b["stresses"][m]["max"], s["max"], delta=1e-6 * max(s["max"], 1.0), msg=m)

    def test_shell(self):
        fr = ShellFrame.build(self.d, 25.0)
        a = analyze_shell(self.d, ground_forces=self.ground, frame=fr)
        b = analyze_shell(self.d, frame=fr, mount_loads=self.inst["mount_loads"],
                          gravity_mps2=self.inst["gravity_mps2"], inertia_N=self.inst["inertia_N"])
        np.testing.assert_allclose(b["von_mises"]["max"], a["von_mises"]["max"], rtol=1e-6, atol=1e-6)
        self.assertAlmostEqual(abs(b["hot_spot_max"]["value"]), abs(a["hot_spot_max"]["value"]), places=6)

    def test_unbalanced_mounts_fail(self):
        bad = copy.deepcopy(self.inst)
        bad["inertia_N"] = np.array([0.0, 0.0, 500.0])
        with self.assertRaisesRegex(DesignError, "mount forces"):
            analyze_shell(self.d, mount_loads=bad["mount_loads"], gravity_mps2=bad["gravity_mps2"],
                          inertia_N=bad["inertia_N"], element_size_mm=25.0)


class ToolFramesTest(unittest.TestCase):
    LC = {"load_case_id": "R1-x", "test_id": "R1", "domain": "strength", "title": "R1", "params": {},
          "tolerance_rel": 0.15}

    @classmethod
    def setUpClass(cls):
        cls.d, st = ladder()
        # the front wheels carry more as time goes (the rear less), so the stresses change over the instants
        cls.up = {"P1-x": series_analysis(cls.d, st)}
        ana = cls.up["P1-x"]
        for c in ana["series"]:
            if c["channel"] == "mount_z.FL.contact" or c["channel"] == "mount_z.FR.contact":
                c["values"] = [v * k for v, k in zip(c["values"], (1.0, 1.1, 1.2))]
            if c["channel"] == "mount_z.RL.contact" or c["channel"] == "mount_z.RR.contact":
                c["values"] = [v * k for v, k in zip(c["values"], (1.0, 0.9, 0.8))]

    def check_common(self, rec, channels):
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        contract.validate("analysis", dict(rec, analysis_id="ana-0009"))
        frames = [f for f in rec["fields"] if "frames" in f]
        for f in frames:
            self.assertEqual(f["frames"]["t_s"], TIMES)
            self.assertEqual(f["frames"]["source_analysis_id"], "ana-0003")
            self.assertEqual(len(f["frames"]["max"]), 3)
        self.assertEqual([s["channel"] for s in rec["series"]], channels)
        self.assertTrue(any("instants of P1-x's mount forces" in e["name"] for e in rec["effects"]))
        self.assertTrue(any("field.frames" in a for a in rec["assumptions"]))
        return frames

    def test_frame_static(self):
        rec = tool("frame_static").run(self.d, self.LC, [{"prediction_id": "pred-0001"}], {"upstream_analyses": self.up})
        frames = self.check_common(rec, ["max_stress"])
        (f,) = frames
        members = {m["id"] for m in self.d["members"]}
        self.assertTrue(all(set(pm) == members for pm in f["frames"]["per_member"]))
        peak = rec["series"][0]["values"]
        self.assertNotAlmostEqual(peak[0], peak[2], places=3)
        top = next(r for r in rec["results"] if r["quantity"] == "max_stress_over_time")
        self.assertAlmostEqual(top["value"], max(peak), places=3)
        self.assertIn(f"t {TIMES[int(np.argmax(peak))]:.3f} s", top["where"])

    def test_frame_shell_on_the_mesh(self):
        mesh = dict(build_mesh(self.d, 20.0).record("mesh-0001", 1))
        rec = tool("frame_shell").run(self.d, self.LC, [{"prediction_id": "pred-0001"}],
                                      dict(SIZE, upstream_analyses=self.up, mesh=mesh))
        frames = self.check_common(rec, ["max_hot_spot_stress", "max_stress"])
        on_mesh = [f for f in frames if f.get("mesh_id") == "mesh-0001"]
        self.assertEqual(len(on_mesh), 1)
        pe = on_mesh[0]["frames"]["per_element"]
        self.assertEqual([len(v) for v in pe], [len(mesh["elements"])] * 3)
        self.assertEqual(on_mesh[0]["frames"]["max"], [max(v) for v in pe])
        hs = rec["series"][0]["values"]
        top = next(r for r in rec["results"] if r["quantity"] == "max_hot_spot_stress_over_time")
        self.assertAlmostEqual(top["value"], max(hs), places=3)

    def test_frame_shell_without_the_mesh_keeps_per_member(self):
        rec = tool("frame_shell").run(self.d, self.LC, [{"prediction_id": "pred-0001"}],
                                      dict(SIZE, upstream_analyses=self.up))
        frames = self.check_common(rec, ["max_hot_spot_stress", "max_stress"])
        self.assertEqual([("mesh_id" in f, "per_member" in f["frames"]) for f in frames], [(False, True)])



def towered():
    """The fixture ladder with a 200 mm post (30 x 30 x 2) standing on the left rail at x -500: FL's
    damper_upper at its top (node LT), a bracket on the right rail holding FR's uca_front, the other
    hard points nowhere on the frame."""
    d, st = ladder()
    d["nodes"] += [{"id": "LM", "xyz_mm": [-500.0, 400.0, 400.0]}, {"id": "LT", "xyz_mm": [-500.0, 400.0, 600.0]},
                   {"id": "RM", "xyz_mm": [-500.0, -400.0, 400.0]}]
    d["sections"]["post"] = {"shape": "rect_tube", "dims_mm": {"b": 30.0, "h": 30.0, "t": 2.0}}
    ms = [m for m in d["members"] if m["id"] not in ("railLa", "railRa")]
    base = next(m for m in d["members"] if m["id"] == "railLa")
    ms += [dict(base, id="railLa1", node_a="L0", node_b="LM"), dict(base, id="railLa2", node_a="LM", node_b="L1"),
           dict(base, id="railRa1", node_a="R0", node_b="RM"), dict(base, id="railRa2", node_a="RM", node_b="R1"),
           dict(base, id="post", node_a="LM", node_b="LT", section="post")]
    d["members"] = ms
    d["supports"][0]["suspension"]["hardpoints_mm"]["damper_upper"] = [-500.0, 400.0, 600.0]
    d["supports"][1]["suspension"]["hardpoints_mm"]["uca_front"] = [-480.0, -360.0, 450.0]
    d["parts"].append({"id": "bracket_FR", "name": "金具", "kind": "bracket", "mass_kg": 0.5, "carried_by": ["RM", "R1"],
                       "box": {"center_mm": [-500.0, -370.0, 450.0], "size_mm": [60.0, 30.0, 40.0]}})
    return d, st


class MountCarriersTest(unittest.TestCase):
    """Mount forces go into the frame where the design takes them (案 A: towers and brackets)."""

    def setUp(self):
        self.d, st = towered()
        self.loads = {sid: {"vectors": [("contact", s.get("contact_point", s["point_mm"]), s["force_N"])]}
                      for sid, s in st["supports"].items()}

    def test_carriers(self):
        c = mount_carriers(self.d)
        self.assertEqual(c["FL"], {"contact": [], "damper_upper": ["LT"]})
        self.assertEqual(c["FR"], {"contact": [], "uca_front": ["RM", "R1"]})
        self.assertEqual(c["RL"], {"contact": []})

    def with_side_force(self, mount, point, force=(0.0, 2000.0, 0.0)):
        """FL's ground force, and a side force at `mount` taken back out at the contact (a couple)."""
        out = copy.deepcopy(self.loads)
        f = np.asarray(force, float)
        (k, p, g), = out["FL"]["vectors"]
        out["FL"]["vectors"] = [(k, p, np.asarray(g, float) - f), (mount, np.asarray(point, float), f)]
        return out

    def test_beam_post_takes_the_force_at_its_top(self):
        top = [-500.0, 400.0, 600.0]
        on = analyze(self.d, mount_loads=self.with_side_force("damper_upper", top), inertia_N=np.zeros(3))
        off = analyze(self.d, mount_loads=self.with_side_force("elsewhere", top), inertia_N=np.zeros(3))
        I = (30.0**4 - 26.0**4) / 12.0
        want = 2000.0 * 200.0 * 15.0 / I  # the post as a cantilever, the side force at its top
        self.assertAlmostEqual(on["stresses"]["post"]["max"], want, delta=0.05 * want)
        self.assertLess(off["stresses"]["post"]["max"], 0.05 * want)

    def test_shell_post_and_bracket(self):
        fr = ShellFrame.build(self.d, 20.0)
        kw = dict(frame=fr, inertia_N=np.zeros(3))
        on = analyze_shell(self.d, mount_loads=self.with_side_force("damper_upper", [-500.0, 400.0, 600.0]), **kw)
        off = analyze_shell(self.d, mount_loads=self.with_side_force("elsewhere", [-500.0, 400.0, 600.0]), **kw)
        self.assertGreater(on["member_max"]["post"][0], 10.0 * off["member_max"]["post"][0])
        self.assertIn("LT", on["joint_loads"])
        br = self.with_side_force("elsewhere", [-500.0, 400.0, 600.0])
        br["FR"]["vectors"].append(("uca_front", np.array([-480.0, -360.0, 450.0]), np.array([0.0, 0.0, 300.0])))
        br["FR"]["vectors"][0] = ("contact", br["FR"]["vectors"][0][1], np.asarray(br["FR"]["vectors"][0][2]) -
                                  np.array([0.0, 0.0, 300.0]))
        r = analyze_shell(self.d, mount_loads=br, **kw)
        # half of the bracket's 300 N goes into RM, which carries nothing else but half the 0.5 kg bracket,
        # whose d'Alembert force changes with the couple the extra force makes
        c = np.array([-500.0, -370.0, 450.0])

        def body(res):  # analyze_shell's body force per kg at the bracket's centre
            return -res["inertia_accel"] - np.cross(res["inertia_alpha"], c - res["cg_mm"]) * 1e-3

        want = np.array([0.0, 0.0, 150.0]) + 0.25 * (body(r) - body(off))
        np.testing.assert_allclose(r["joint_loads"]["RM"][0] - off["joint_loads"]["RM"][0], want, atol=1e-6)


if __name__ == "__main__":
    unittest.main()

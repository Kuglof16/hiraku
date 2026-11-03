"""The space-frame UTV design (hiraku.designgen.space_frame_utv): contract, frame, hardpoints, cage, K2."""

import math
import os
import unittest

import numpy as np

from hiraku import contract
from hiraku.designgen import ladder_4w
from hiraku.designgen import space_frame_utv as sf
from hiraku.designgen.kit.frame import Frame
from hiraku.designgen.kit.sections import standard

SLOW = unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "a long K2 scene: set HIRAKU_SLOW_TESTS=1")


class SpaceFrameDesignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = sf.build()

    def test_contract(self):
        self.assertEqual(contract.errors("design", self.d), [])

    def test_variables_have_ladder_4w_shape(self):
        keys = {"name", "unit", "default", "revisable", "meaning"}
        for r in sf.spec():
            self.assertTrue(keys <= set(r) or r.get("derived"), r)
            if r["revisable"]:
                self.assertLessEqual(r["min"], r["default"])
                self.assertLessEqual(r["default"], r["max"])
        for k in sf.SHARED:  # the shared variables are ladder_4w's own
            self.assertIs(sf.VARIABLES[k], ladder_4w.VARIABLES[k])
        self.assertFalse(set(ladder_4w.SIGNATURE) <= set(self.d["variables"]))  # not taken for a ladder design
        self.assertTrue(set(sf.SIGNATURE) <= set(self.d["variables"]))
        self.assertEqual(sf.values_of(self.d)["wheelbase"], {"value": 1900.0, "unit": "mm"})

    def test_frame_is_one_piece(self):
        self.assertTrue(sf.connected(self.d))
        broken = dict(self.d, members=[m for m in self.d["members"] if not m["id"].startswith(("stay", "deck_rail",
                                                                                               "rear_"))])
        self.assertFalse(sf.connected(broken))

    def test_subframe_nodes_are_the_hardpoints(self):
        nodes = {n["id"]: np.asarray(n["xyz_mm"]) for n in self.d["nodes"]}
        for s in self.d["supports"]:
            sus = s["suspension"]
            self.assertEqual((sus["type"], sus["kc_table"]), ("kc_table", "builtin:double_wishbone"))
            hp = sus["hardpoints_mm"]
            side = "L" if s["position_mm"][1] > 0 else "R"
            ax = "A" if s["id"].startswith("F") else "B"
            for k in sf.ARM_KEYS:
                np.testing.assert_allclose(nodes[f"{side}{ax}_{k}"], hp[k], atol=1e-9, err_msg=f"{s['id']} {k}")
            self.assertIn(s["frame_node"], nodes)
            np.testing.assert_allclose(hp["wheel_center"], s["position_mm"], atol=1e-9)

    def test_tubes_are_standard_sizes(self):
        std = {k for k, _ in standard("round_tube")}
        for key, sec in self.d["sections"].items():
            self.assertIn(key, std)
            self.assertIn("JIS G 3444", sec["source"])
        self.assertEqual(sf.tube(40.0, 2.1)[0], "ct42.7x2.3")  # next standard diameter, lightest wall >= 2.1
        self.assertEqual(sf.tube(42.7, 2.4)[0], "ct42.7x2.5")

    def test_cage_clears_the_occupants(self):
        gap, where = sf.occupant_clearance(self.d)
        v = {k: q["value"] for k, q in self.d["variables"].items()}
        self.assertGreaterEqual(gap, min(v["cage_clearance"], v["head_clearance"]) - 1e-6, where)
        occ = [p for p in self.d["parts"] if p["kind"] == "occupant"]
        self.assertEqual([p["id"] for p in occ], ["occupant_R", "occupant_L"])  # the driver first (right-hand drive)

    def test_suspension_clears_the_frame(self):
        frame = Frame(sf.MATERIAL)
        frame.nodes = {n["id"]: list(n["xyz_mm"]) for n in self.d["nodes"]}
        frame.members = [dict(m) for m in self.d["members"]]
        frame.sections = dict(self.d["sections"])
        sup = {s["id"]: s for s in self.d["supports"]}
        for corner, sid in (("A", "FL"), ("B", "RL")):
            s = sup[sid]
            gap, where = sf.arm_clearance(frame, corner, s["suspension"]["hardpoints_mm"], s["radius_mm"],
                                          s["suspension"]["travel_mm"])
            self.assertGreaterEqual(gap, sf.ARM_CLEARANCE_MM, where)
        # a tube put across the upper arm's path is found
        hp = sup["FL"]["suspension"]["hardpoints_mm"]
        mid = (np.asarray(hp["uca_front"]) + np.asarray(hp["uca_outer"])) / 2
        frame.node("Xa", mid + [0.0, 0.0, 30.0])
        frame.node("Xb", mid + [-300.0, 0.0, 30.0])
        frame.member("in_the_way", "Xa", "Xb", next(iter(frame.sections)))
        gap, where = sf.arm_clearance(frame, "A", hp, sup["FL"]["radius_mm"], sup["FL"]["suspension"]["travel_mm"])
        self.assertLess(gap, 0.0)
        self.assertIn("in_the_way", where)

    def test_tyres_clear_the_frame(self):
        gap, where = sf.tyre_clearance(self.d)
        self.assertGreaterEqual(gap, sf.ARM_CLEARANCE_MM, where)
        # a tube put through the front left tyre's place is found
        fl = next(s for s in self.d["supports"] if s["id"] == "FL")
        c = np.asarray(fl["position_mm"], float)
        d2 = dict(self.d, nodes=self.d["nodes"] + [{"id": "Xa", "xyz_mm": (c + [0.0, -400.0, 0.0]).tolist()},
                                                   {"id": "Xb", "xyz_mm": (c + [0.0, 0.0, 0.0]).tolist()}],
                  members=self.d["members"] + [dict(self.d["members"][0], id="in_the_way", node_a="Xa", node_b="Xb")])
        gap, where = sf.tyre_clearance(d2)
        self.assertLess(gap, 0.0)
        self.assertIn("in_the_way", where)
        # a tube across, 40 mm behind the straight tyre's tread: steered, the tyre's corner comes nearer
        # (r cos a + w/2 sin a behind the centre, largest at the sampled lock nearest atan(w / 2r))
        r = fl["radius_mm"]
        d3 = dict(self.d, nodes=self.d["nodes"] + [{"id": "Ya", "xyz_mm": (c + [-r - 40.0, -300.0, 0.0]).tolist()},
                                                   {"id": "Yb", "xyz_mm": (c + [-r - 40.0, 300.0, 0.0]).tolist()}],
                  members=self.d["members"] + [dict(self.d["members"][0], id="behind", node_a="Ya", node_b="Yb")])
        gap, where = sf.tyre_clearance(d3)
        self.assertIn("behind", where)
        rt = self.d["sections"][self.d["members"][0]["section"]]["dims_mm"]["d"] / 2.0
        lock = self.d["variables"]["max_steer_angle"]["value"]
        reach = max(r * math.cos(a) + fl["width_mm"] / 2 * abs(math.sin(a))
                    for a in np.radians(np.linspace(-lock, lock, sf.TYRE_STEER_SAMPLES)))
        self.assertGreater(reach, r + 10.0)
        self.assertAlmostEqual(gap, r + 40.0 - reach - rt, delta=0.1)

    def test_dash_bar_has_its_own_tube(self):
        sec = {m["id"]: m["section"] for m in self.d["members"]}
        self.assertEqual(sec["dash_bar_L"], "ct48.6x3.2")
        self.assertEqual(sec["dash_bar_R"], "ct48.6x3.2")
        self.assertEqual(sec["front_upper_link_L"], "ct34x2.3")
        ends = {m["id"]: (m["node_a"], m["node_b"]) for m in self.d["members"]}
        self.assertEqual(ends["front_upper_link_L"], ("LA_uca_rear", "D0"))
        steering = next(p for p in self.d["parts"] if p["id"] == "steering")
        top = steering["box"]["center_mm"][2] + steering["box"]["size_mm"][2] / 2
        dash_z = next(n["xyz_mm"][2] for n in self.d["nodes"] if n["id"] == "D0")
        self.assertAlmostEqual(dash_z - top, 48.6 / 2 + sf.ON_TUBES_MM)  # hangs below the heavier dash bar

    def test_the_layout_follows_the_variables(self):
        d2 = sf.build({"wheelbase": {"value": 2100.0, "unit": "mm"}, "seat_x": -950.0, "head_clearance": 150.0,
                       "cage_tube_d": 48.6})
        self.assertEqual(contract.errors("design", d2), [])
        self.assertTrue(sf.connected(d2))
        n1 = {n["id"]: n["xyz_mm"] for n in self.d["nodes"]}
        n2 = {n["id"]: n["xyz_mm"] for n in d2["nodes"]}
        self.assertAlmostEqual(n2["LB_lca_front"][0] - n1["LB_lca_front"][0], -200.0)
        self.assertAlmostEqual(n2["LH2"][2] - n1["LH2"][2], 50.0 + (48.6 - 42.7) / 2)  # roof up by the clearance
        self.assertIn("ct48.6x2.3", d2["sections"])
        gap, _ = sf.occupant_clearance(d2)
        self.assertGreaterEqual(gap, 60.0 - 1e-6)

    def test_refusals(self):
        with self.assertRaisesRegex(ladder_4w.DesignGenError, "outside"):
            sf.build({"wheelbase": 900.0})
        with self.assertRaisesRegex(ladder_4w.DesignGenError, "main hoop"):
            sf.build({"seat_x": -1200.0, "wheelbase": 1600.0})
        with self.assertRaisesRegex(ladder_4w.DesignGenError, "unknown"):
            sf.build({"rail_spacing": 700.0})

    def test_mass_and_parts(self):
        from hiraku.solvers.k0_handcalc.vehicle import mass_summary
        from hiraku.solvers.k4_geometry import interferences

        ms = mass_summary(self.d)
        frame_kg = sum(it.mass_kg for it in ms["items"] if it.carrier == "member")
        self.assertGreater(frame_kg, 40.0)
        self.assertLess(frame_kg, 120.0)
        self.assertEqual(interferences(self.d), [])
        for p in self.d["parts"]:
            for n in p.get("carried_by", []):
                self.assertIn(n, {x["id"] for x in self.d["nodes"]})


class SpaceFrameK2Test(unittest.TestCase):
    def test_k2_builds_and_the_push_lifts_the_front(self):
        from hiraku.solvers.k2_vehicle.model import build_vehicle
        from hiraku.solvers.k2_vehicle.shelf.step import corner_mounts, step_push

        d = sf.build()
        veh = build_vehicle(d)
        self.assertEqual([c.id for c in veh.corners], ["FL", "FR", "RL", "RR"])
        self.assertTrue(all(c.table is not None for c in veh.corners))
        front = [i for i, c in enumerate(veh.corners) if c.steer]
        r = step_push(veh, 0.10, front, rec_hz=25.0)
        self.assertTrue(r["lifted"])
        st = min(r["states"], key=lambda s: abs(s["t"] - r["snap"]["t"]))
        self.assertLess(abs(st["t"] - r["snap"]["t"]), 0.011)
        total = sum(f for i in range(len(veh.corners)) for _, f in corner_mounts(veh, st, i).values())
        np.testing.assert_allclose(total + st["weight"] + st["inertia_force"], 0.0, atol=1e-6 * veh.m_s * 9.8)

    @SLOW
    def test_step_climb_dynamic_runs(self):
        from hiraku.solvers.k2_vehicle.shelf import TOOLS

        lc = {"load_case_id": "p1", "params": {"step_height": {"value": 100.0, "unit": "mm"}}}
        rec = TOOLS["step_climb_dynamic"].run(sf.build(), lc, [{"prediction_id": "pred-0001"}], {})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        res = {r["quantity"]: r["value"] for r in rec["results"]}
        self.assertEqual(res["lifted_by_push"], 1.0)
        self.assertTrue(math.isfinite(res["front_wheel_force"]))


if __name__ == "__main__":
    unittest.main()

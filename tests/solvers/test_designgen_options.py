"""ladder_4w's options for the requirements that fail on version 1 (kit/attach.py, the side guard, the ROPS shape,
the fenders): off by default (version 1 unchanged), each one a variable or a choice, and what each does to the
load_path tests (R7 foot, R8 free parts and push) and to R4's K0 inputs."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, designgen
from hiraku.designgen import ladder_4w as GEN
from hiraku.designgen.kit import attach
from hiraku.solvers.k0_handcalc.frame_inputs import portal_frame_inputs
from hiraku.solvers.k4_geometry import model as k4

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
MOUNTS = {k: 1 for k in GEN.MOUNT_GROUPS}
# the set the design department's version 2 may use (the report's table): every part held, the pedals on a
# beam out from the front tower's rear post (pedal_mount 3; on the bulkhead, a 2.3 mm sheet, the brake pedal moved
# 49.5 mm), the side frame outboard of point R
VERSION_2 = {**MOUNTS, "pedal_mount": 3, "side_bars": 1}


def _touch(design, a: str, b: str) -> float:
    bs = k4.bodies(design)
    return k4.shape_overlap(bs[a], bs[b])


class SpanTest(unittest.TestCase):
    def test_spacer_across_one_gap(self):
        # a panel 10 mm over a rail: footprint the middle of the overlap, at most PAD_MM, height the gap
        s = attach.span((np.array([0.0, -50, 430]), np.array([550.0, 50, 450])),
                        (np.array([-100.0, 30, 340]), np.array([700.0, 70, 420])))
        self.assertEqual(s["overlap_axes"], 2)
        self.assertAlmostEqual(s["gap"], 10.0)
        np.testing.assert_allclose(s["lo"], [250.0, 30, 420])
        np.testing.assert_allclose(s["hi"], [300.0, 50, 430])

    def test_angle_bracket_across_a_corner(self):
        s = attach.span((np.array([0.0, 0, 0]), np.array([50.0, 20, 300])),
                        (np.array([-90.0, 50, -100]), np.array([-40.0, 90, 900])))
        self.assertEqual(s["overlap_axes"], 1)
        np.testing.assert_allclose(s["lo"][:2], [-40.0, 20])
        np.testing.assert_allclose(s["hi"][:2], [0.0, 50])
        self.assertAlmostEqual(s["gap"], math.hypot(40.0, 30.0))

    def test_no_mount(self):
        far = (np.array([100.0, 100, 100]), np.array([200.0, 200, 200]))
        self.assertIsNone(attach.span((np.zeros(3), np.full(3, 50.0)), far))  # apart on every axis
        self.assertIsNone(attach.span((np.zeros(3), np.full(3, 50.0)), (np.full(3, 10.0), np.full(3, 40.0))))  # inside
        self.assertIsNone(attach.span((np.zeros(3), np.full(3, 50.0)),  # overlap shorter than MIN_OVERLAP_MM
                                      (np.array([45.0, 0, 60]), np.array([90.0, 50, 70]))))

    def test_mass(self):
        self.assertAlmostEqual(attach.mount_mass([0, 0, 0], [50, 40, 10], 7850.0, 10.0)[0], 7850e-9 * 50 * 40 * 10)
        m, kind = attach.mount_mass([0, 0, 0], [50, 40, 30], 7850.0, 30.0)
        self.assertEqual(kind, "bracket")
        self.assertAlmostEqual(m, 7850e-9 * 2 * attach.BRACKET_T_MM * 40 * 50)


class DefaultsTest(unittest.TestCase):
    def test_off_by_default(self):
        self.assertEqual(GEN.build(), V1)
        self.assertEqual(GEN.choices_of(V1), {"joints": "butt", "part_mounts": "none", "pedal_mount": "none",
                                              "side_guard": "none", "rops": "hoop", "fenders": "none",
                                              "side_opening": "none", "grab_bar": "none"})
        for k in ("mount_floor", "pedal_mount", "side_bars", "rollbar_stays", "rollbar_front_posts", "fenders",
                  "rollbar_foot_b"):
            self.assertTrue(GEN.VARIABLES[k].revisable, k)
            self.assertNotIn(k, V1["variables"])

    def test_choices_stand_for_the_variables(self):
        self.assertEqual(designgen.regenerate(V1, {"part_mounts": "brackets"}), GEN.build(MOUNTS))
        d = designgen.regenerate(V1, {"rops": "four_post_stays", "side_guard": "bars"})
        self.assertEqual(GEN.choices_of(d)["rops"], "four_post_stays")
        self.assertEqual(GEN.choices_of(d)["side_guard"], "bars")
        self.assertEqual(GEN.choices_of(GEN.build({"mount_floor": 1}))["part_mounts"], "some")
        with self.assertRaises(designgen.DesignGenError):
            GEN.apply_choices({"side_guard": "bars"}, {"side_bars": 0})
        with self.assertRaises(designgen.DesignGenError):
            GEN.build({"side_bar_count": 2.5})
        with self.assertRaises(designgen.DesignGenError):
            GEN.build({"rollbar_foot_b": 10})  # 0 (as the cross members) or at least 30 mm


class MountsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = GEN.build(MOUNTS)
        cls.mounts = [p for p in cls.d["parts"] if p["id"].startswith("mount_")]

    def test_fits_the_contract(self):
        contract.validate("design", self.d)
        self.assertIn("ss400", self.d["materials"])
        for p in self.mounts:
            self.assertEqual((p["kind"], p["material"], p["finish"]), ("bracket", "ss400", "painted"))
            self.assertGreater(p["mass_kg"], 0.0)

    def test_every_part_but_the_pedals_is_held(self):
        held, free = attach.free_parts(self.d)
        self.assertEqual(free, {"pedal_accel", "pedal_brake"})
        _, free_v1 = attach.free_parts(V1)
        self.assertEqual(len(free_v1), 16)

    def test_each_mount_touches_its_part_and_member(self):
        for p in self.mounts:
            part = p["id"][len("mount_"):].rsplit("_", 1)[0]
            member = p["name"].rsplit("(", 1)[1].split(" ")[0]
            self.assertAlmostEqual(_touch(self.d, f"part:{p['id']}", f"part:{part}"), 0.0, places=6, msg=p["id"])
            self.assertAlmostEqual(_touch(self.d, f"part:{p['id']}", f"member:{member}"), 0.0, places=6, msg=p["id"])

    def test_sizes_come_from_the_gaps(self):
        floor = [p for p in self.mounts if p["id"] in ("mount_floor_1", "mount_floor_2")]
        self.assertEqual(len(floor), 2)  # on both rails
        for p in floor:
            self.assertAlmostEqual(p["box"]["size_mm"][2], GEN.ON_RAILS_MM)
        self.assertEqual(len([p for p in self.mounts if p["id"].startswith("mount_battery_")]), 2)  # both cross members


class PedalsTest(unittest.TestCase):
    def test_bulkhead_and_floor(self):
        for vals, target in (({"pedal_mount": 2}, "bulkhead"), ({"pedal_mount": 1, "mount_floor": 1}, "floor_front")):
            d = GEN.build(vals)
            stands = [p for p in d["parts"] if p["id"].startswith("stand_")]
            self.assertEqual(sorted(p["id"] for p in stands), ["stand_pedal_accel", "stand_pedal_brake"])
            _, free = attach.free_parts(d)
            self.assertFalse({"pedal_accel", "pedal_brake"} & free, target)
            for p in stands:
                self.assertAlmostEqual(_touch(d, f"part:{p['id']}", f"part:{target}"), 0.0, places=6)
        with self.assertRaises(designgen.DesignGenError):
            GEN.build({"pedal_mount": 1})  # the footwell floor floats without mount_floor


class GuardsTest(unittest.TestCase):
    def test_side_bars_at_point_r(self):
        d = GEN.build({"side_bars": 1, "side_net": 1})
        contract.validate("design", d)
        nodes = {n["id"]: n["xyz_mm"] for n in d["nodes"]}
        seat_top = max(p["box"]["center_mm"][2] + p["box"]["size_mm"][2] / 2 for p in d["parts"] if p["kind"] == "seat")
        bars = [m for m in d["members"] if m["id"].startswith("side_bar_")]
        self.assertEqual(len(bars), 4)
        heights = sorted({round(nodes[m["node_a"]][2] - seat_top, 6) for m in bars})
        self.assertEqual(heights, [82.0, 637.5])  # AIST I11 and I3 of the young male 95th percentile
        self.assertTrue(all(m["part"] == "side_guard" for m in d["members"] if m["id"].startswith("side_")))
        nets = [p for p in d["parts"] if p["kind"] == "side_net"]
        self.assertEqual(len(nets), 2)
        held, _ = attach.free_parts(d)
        self.assertTrue({p["id"] for p in nets} <= held)

    def test_refuses_a_side_frame_in_the_floor(self):
        with self.assertRaises(designgen.DesignGenError) as e:
            GEN.build({"track": 1300, "side_bars": 1})  # the floor under the seats is the track wide
        self.assertIn("rollbar_width", str(e.exception))

    def test_fenders(self):
        d = GEN.build({"fenders": 1})
        f = [p for p in d["parts"] if p["kind"] == "fender"]
        self.assertEqual(len(f), 4 * GEN.FENDER_PIECES)
        self.assertTrue(all("carried_by" not in p and p["material"] == "hdpe" for p in f))
        self.assertIn("hdpe", d["materials"])
        s = d["supports"][0]
        R = s["radius_mm"] + GEN.VARIABLES["fender_gap"].default
        for p in f[:GEN.FENDER_PIECES]:  # each piece's inner face tangent to the circle at its middle
            c = np.asarray(p["box"]["center_mm"]) - np.asarray(s["position_mm"])
            self.assertAlmostEqual(math.hypot(c[0], c[2]), R + GEN.FENDER_T_MM / 2, places=6)
            th = math.atan2(-c[0], c[2])
            self.assertAlmostEqual(p["box"]["rpy_deg"][1], -math.degrees(th), places=6)
        first = math.atan2(-(f[0]["box"]["center_mm"][0] - s["position_mm"][0]), f[0]["box"]["center_mm"][2] - s["position_mm"][2])
        step = math.radians(80.0) / GEN.FENDER_PIECES
        self.assertAlmostEqual(first - step / 2, -math.radians(30.0), places=9)


class RopsTest(unittest.TestCase):
    def test_outrigger_section_reaches_k0(self):
        base = portal_frame_inputs(V1)["side"]
        d = GEN.build({"rollbar_foot_b": 50, "rollbar_foot_h": 60, "rollbar_foot_t": 3.2})
        got = portal_frame_inputs(d)["side"]
        self.assertGreater(got["A_o"]["value"], base["A_o"]["value"])
        b, h, t = 50.0, 60.0, 3.2
        self.assertAlmostEqual(got["A_o"]["value"], b * h - (b - 2 * t) * (h - 2 * t), places=6)
        self.assertEqual(got["h"], base["h"])

    def test_stays_and_front_posts_leave_the_hoop_as_k0_reads_it(self):
        from hiraku.solvers.k0_handcalc.rops_inputs import rops_gaps

        d = GEN.build({"rollbar_stays": 1, "rollbar_front_posts": 1, "side_bars": 1})
        ids = {m["id"] for m in d["members"]}
        self.assertTrue({"rops_stay_L", "rops_stay_R", "rops_front_top", "rops_roof_L", "rops_front_foot_R"} <= ids)
        self.assertTrue(all(m["part"] == "rollbar" for m in d["members"] if m["id"].startswith("rops_")))
        self.assertEqual(portal_frame_inputs(d)["side"], portal_frame_inputs(V1)["side"])
        from hiraku.contract.loading import on_board
        self.assertEqual(rops_gaps(on_board(d, "two_cargo"))["g_side"], rops_gaps(on_board(V1, "two_cargo"))["g_side"])
        with self.assertRaises(designgen.DesignGenError):
            GEN.build({"rollbar": 0, "rollbar_stays": 1})


class LoadPathTest(unittest.TestCase):
    """The load_path tool on version 1 and on VERSION_2 (R8-free, R7-foot and R8-push as the builders make them)."""

    @classmethod
    def setUpClass(cls):
        from hiraku.catalog.builders import BUILDERS
        from hiraku.solvers.shelf import LoadPath
        from tests.solvers.test_builders_load_path import REQ, SPECS

        cls.d = GEN.build(VERSION_2)
        cases = {lc["load_case_id"]: lc for tid in ("R7", "R8") for lc in BUILDERS[tid](SPECS[tid], REQ, cls.d)}
        cls.cases = {k: cases[k] for k in ("R8-free", "R7-foot-500N", "R8-push-725N")}
        tool = LoadPath()
        cls.runs = {k: tool.run(cls.d, lc, [{"prediction_id": "pred-0001"}], {}) for k, lc in cls.cases.items()}

    def test_version_2_judged(self):
        """R8-free, R7-foot and R8-push pass, with the pedals' stands on the beam from the post (pedal_mount 3)."""
        from tests.solvers.test_builders_load_path import judged, results

        for k, lc in self.cases.items():
            rec = self.runs[k]
            self.assertEqual(rec["status"], "ok", rec.get("error"))
            got = results(rec).get(lc["criterion"]["quantity"])
            self.assertTrue(judged(lc, rec), f"{k}: {got}")
            self.assertEqual(results(rec)["free_parts"]["value"], 0.0)
            self.assertNotIn("unsupported_load", results(rec))
        contract.validate("design", self.d)

    def test_bulkhead_stands_move_more_than_q69(self):
        """With the stands on the bulkhead (pedal_mount 2), a 2.3 mm sheet (ladder_4w.PLATE_T_MM) and not a 20 mm
        solid box, the brake pedal moves more than Q69; the accelerator's, near the post, passes."""
        from hiraku.catalog.builders import BUILDERS
        from hiraku.solvers.shelf import LoadPath
        from tests.solvers.test_builders_load_path import REQ, SPECS, judged, results

        d = GEN.build({**VERSION_2, "pedal_mount": 2})
        lc = next(c for c in BUILDERS["R7"](SPECS["R7"], REQ, d) if c["load_case_id"] == "R7-foot-500N")
        rec = LoadPath().run(d, lc, [{"prediction_id": "pred-0001"}], {})
        foot = results(rec)
        self.assertFalse(judged(lc, rec))
        self.assertEqual(foot["pedal_deflection"]["where"], "pedal_brake")
        self.assertLessEqual(foot["pedal_deflection.pedal_accel"]["value"], lc["criterion"]["limit"]["value"])


if __name__ == "__main__":
    unittest.main()

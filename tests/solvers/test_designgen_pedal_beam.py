"""ladder_4w pedal_mount = 3 ("post"): a beam out from the front tower's rear post holds the pedals' stands. Its
place and length come from the pedals and the post; on the version 2 option set (every part mounted, the side
frame with its net) the load_path tool's R7 and R8 cases all pass with it."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract
from hiraku.designgen import DesignGenError
from hiraku.designgen import ladder_4w as GEN
from hiraku.designgen.kit import attach
from hiraku.solvers.k4_geometry import model as k4

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
CHOICES_V2 = {"part_mounts": "brackets", "pedal_mount": "post", "side_guard": "bars_net"}


def _nodes(d):
    return {n["id"]: np.asarray(n["xyz_mm"], float) for n in d["nodes"]}


def _members(d):
    return {m["id"]: m for m in d["members"]}


def _parts(d):
    return {p["id"]: p for p in d["parts"]}


def _front_edge(p):
    cs = attach._corners(p)
    x0 = float(np.max(cs[:, 0]))
    return x0, float(np.mean(cs[np.abs(cs[:, 0] - x0) < 1e-6][:, 2]))


class PedalBeamTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = GEN.build({"pedal_mount": 3})

    def test_choice(self):
        self.assertEqual(GEN.choice_values({"pedal_mount": "post"}), {"pedal_mount": {"value": 3.0, "unit": "1"}})
        self.assertEqual(GEN.choices_of(self.d)["pedal_mount"], "post")
        self.assertEqual(GEN.build(), V1)  # off by default
        contract.validate("design", self.d)

    def test_beam_from_the_post_at_the_pedals(self):
        d, N, M, P = self.d, _nodes(self.d), _members(self.d), _parts(self.d)
        beam = M["pedal_beam"]
        root, tip = N[beam["node_a"]], N[beam["node_b"]]
        # the root splits the driver's side front tower rear post
        self.assertEqual(M["tower_post_RA2"]["node_b"], beam["node_a"])
        self.assertEqual(M["tower_post_RA2_up"]["node_a"], beam["node_a"])
        self.assertEqual(M["tower_post_RA2_up"]["node_b"], "RAt2")
        self.assertEqual(beam["section"], M["tower_post_RA2"]["section"])
        rail_y = GEN.VARIABLES["rail_spacing"].default / 2.0
        np.testing.assert_allclose(root[:2], [N["RAp2"][0], -rail_y])
        # height: the pedals' front edges; length: to the inner side of the innermost stand
        z = np.mean([_front_edge(P[k])[1] for k in ("pedal_accel", "pedal_brake")])
        self.assertAlmostEqual(root[2], z, places=6)
        self.assertAlmostEqual(tip[2], z, places=6)
        inner = max(P[k]["box"]["center_mm"][1] for k in ("pedal_accel", "pedal_brake"))
        self.assertAlmostEqual(tip[1], inner + attach.STAND_PAD_MM / 2.0 + GEN.PEDAL_BEAM_PAST_MM, places=6)
        self.assertEqual(tip[0], root[0])

    def test_stands_between_pedal_and_beam(self):
        d, P = self.d, _parts(self.d)
        bs = k4.bodies(d)
        lo, hi = attach._aabb(bs["member:pedal_beam"].obb)
        for k in ("pedal_accel", "pedal_brake"):
            s = P[f"stand_{k}"]
            x0, _ = _front_edge(P[k])
            c, size = np.asarray(s["box"]["center_mm"]), np.asarray(s["box"]["size_mm"])
            self.assertAlmostEqual(c[0] - size[0] / 2.0, x0, places=6)
            self.assertAlmostEqual(c[0] + size[0] / 2.0, lo[0], places=6)  # the beam's rear face
            self.assertEqual(sorted(s["carried_by"]), sorted(bs["member:pedal_beam"].nodes))
            self.assertAlmostEqual(k4.shape_overlap(bs[f"part:stand_{k}"], bs["member:pedal_beam"]), 0.0, places=6)
            self.assertAlmostEqual(k4.shape_overlap(bs[f"part:stand_{k}"], bs[f"part:{k}"]), 0.0, places=6)
        _, free = attach.free_parts(d)
        self.assertFalse({"pedal_accel", "pedal_brake"} & free)
        # the bulkhead lies on the beam as on the posts
        self.assertIn(_members(d)["pedal_beam"]["node_a"], P["bulkhead"]["carried_by"])

    def test_follows_pedal_x(self):
        x1 = _parts(self.d)["stand_pedal_brake"]["box"]["size_mm"][0]
        d = GEN.build({"pedal_mount": 3, "pedal_x": GEN.VARIABLES["pedal_x"].default - 40.0})
        self.assertAlmostEqual(_parts(d)["stand_pedal_brake"]["box"]["size_mm"][0], x1 + 40.0, places=6)

    def test_refuses_pedals_off_the_post(self):
        with self.assertRaises(DesignGenError):
            GEN.build({"pedal_mount": 3, "pedal_x": -280.0})  # the pedals at the post: no room for a stand


class Version2Test(unittest.TestCase):
    """The version 2 option set with the pedals on the post's beam: every R7 and R8 case passes."""

    @classmethod
    def setUpClass(cls):
        from hiraku.catalog.builders import BUILDERS
        from hiraku.solvers.shelf import LoadPath
        from tests.solvers.test_builders_load_path import REQ, SPECS

        cls.d = GEN.build(GEN.apply_choices(CHOICES_V2, {}))
        cls.cases = {lc["load_case_id"]: lc for tid in ("R7", "R8") for lc in BUILDERS[tid](SPECS[tid], REQ, cls.d)}
        tool = LoadPath()
        cls.runs = {k: tool.run(cls.d, lc, [{"prediction_id": "pred-0001"}], {}) for k, lc in cls.cases.items()}

    def test_all_pass(self):
        from tests.solvers.test_builders_load_path import judged, results

        self.assertEqual(sorted(self.cases), ["R7-foot-500N", "R7-hand-300N", "R8-free", "R8-push-725N", "R8-stress"])
        for k, lc in self.cases.items():
            rec = self.runs[k]
            self.assertEqual(rec["status"], "ok", rec.get("error"))
            self.assertTrue(judged(lc, rec), f"{k}: {results(rec).get(lc['criterion']['quantity'])}")
            self.assertEqual(results(rec)["free_parts"]["value"], 0.0)
        contract.validate("design", self.d)


if __name__ == "__main__":
    unittest.main()

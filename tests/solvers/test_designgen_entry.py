"""designgen.ladder_4w side_opening and grab_bar: the side frame as a fixed guard round point R only, an opening ahead
of it closed by a door (hinges and a latch) or a net on quick-release clips, a grab bar on the front post. Off by
default (version 1 unchanged); on the version 2 option set the load_path tool's R7 and R8 cases pass, and R8's push
lands on the fixed bars (also with the door taken off)."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from hiraku import contract, designgen
from hiraku.designgen import ladder_4w as GEN
from hiraku.solvers.shelf import load_path as LP

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
BASE = {"part_mounts": "brackets", "pedal_mount": "post"}
DOOR = {**BASE, "side_guard": "bars", "side_opening": "door", "grab_bar": "front_post"}
CLIP_NET = {**BASE, "side_guard": "bars_net", "side_opening": "clip_net", "grab_bar": "front_post"}


def nodes(d):
    return {n["id"]: n["xyz_mm"] for n in d["nodes"]}


def parts(d, kind):
    return [p for p in d["parts"] if p["kind"] == kind]


class DefaultsTest(unittest.TestCase):
    def test_off_by_default(self):
        from tests.solvers.same_design import assert_same_design

        assert_same_design(self, GEN.build(), V1)
        self.assertEqual(GEN.choices_of(V1)["side_opening"], "none")
        self.assertEqual(GEN.choices_of(V1)["grab_bar"], "none")
        for k in ("side_opening", "side_bar_reach", "grab_bar", "grab_bar_d"):
            self.assertNotIn(k, V1["variables"])
        self.assertEqual(GEN.choice_values({"side_opening": "door"}), {"side_opening": {"value": 2.0, "unit": "1"}})

    def test_needs_the_side_frame(self):
        for choices in ({"side_opening": "door"}, {"grab_bar": "front_post"}):
            with self.assertRaises(designgen.DesignGenError):
                GEN.build(GEN.apply_choices(choices, {}))
        with self.assertRaises(designgen.DesignGenError):  # the four posts' front post would stand in the opening
            GEN.build(GEN.apply_choices({"side_guard": "bars", "side_opening": "door", "rops": "four_post"}, {}))


class DoorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = designgen.regenerate(V1, DOOR)

    def test_choices_and_contract(self):
        got = GEN.choices_of(self.d)
        self.assertEqual((got["side_guard"], got["side_opening"], got["grab_bar"]), ("bars", "door", "front_post"))
        contract.validate("design", self.d)

    def test_front_post_just_ahead_of_point_r(self):
        n = nodes(self.d)
        x_r = max(float(x[0]) for t in LP.occupant_trunks(self.d) for _, x, _ in LP.point_r(t))
        for side in "LR":
            post = next(m for m in self.d["members"] if m["id"] == f"side_post_{side}SG_1")
            self.assertAlmostEqual(n[post["node_a"]][0], x_r + GEN.SIDE_OPENING_REACH_MM)
            bar = next(m for m in self.d["members"] if m["id"] == f"side_bar_{side}1")
            xs = sorted(n[bar[k]][0] for k in ("node_a", "node_b"))
            self.assertLess(xs[0], x_r)
            self.assertAlmostEqual(xs[1], x_r + GEN.SIDE_OPENING_REACH_MM)
        ids = {m["id"] for m in self.d["members"]}
        self.assertFalse([i for i in ids if i.startswith("side_post_") and "S1" in i])  # no post at S1

    def test_door_hinges_latch(self):
        doors = parts(self.d, "door")
        self.assertEqual({p["id"] for p in doors}, {"side_door_L", "side_door_R"})
        self.assertEqual(len(parts(self.d, "door_hinge")), 4)
        self.assertEqual(len(parts(self.d, "door_latch")), 2)
        door = next(p for p in doors if p["id"] == "side_door_L")
        self.assertAlmostEqual(door["box"]["size_mm"][1], GEN.DOOR_T_MM)
        self.assertAlmostEqual(door["mass_kg"], GEN.plate_mass(door["box"]["size_mm"]))
        front_x = door["box"]["center_mm"][0] + door["box"]["size_mm"][0] / 2.0
        self.assertAlmostEqual(front_x, -float(self.d["variables"]["wheel_radius"]["value"]) - GEN.SIDE_NET_FRONT_GAP_MM)

    def test_grab_bars(self):
        n = nodes(self.d)
        grips = [m for m in self.d["members"] if m["id"].startswith("grab_")]
        self.assertEqual(len(grips), 6)
        self.assertEqual({m["section"] for m in grips}, {"ct27.2x2.3"})
        g = next(m for m in grips if m["id"] == "grab_L_2")
        half = self.d["variables"]["rollbar_width"]["value"] / 2.0
        self.assertAlmostEqual(n[g["node_a"]][1], half + 42.7 / 2.0 + GEN.GRAB_BAR_GAP_MM + 27.2 / 2.0)


class ClipNetTest(unittest.TestCase):
    def test_net_on_clips_and_the_bolted_net_round_point_r(self):
        d = designgen.regenerate(V1, CLIP_NET)
        contract.validate("design", d)
        self.assertEqual({p["id"] for p in parts(d, "side_net")},
                         {"side_net_L", "side_net_R", "side_open_net_L", "side_open_net_R"})
        bolted = next(p for p in d["parts"] if p["id"] == "side_net_L")
        opening = next(p for p in d["parts"] if p["id"] == "side_open_net_L")
        self.assertLess(bolted["box"]["center_mm"][0] + bolted["box"]["size_mm"][0] / 2.0,
                        opening["box"]["center_mm"][0] - opening["box"]["size_mm"][0] / 2.0)
        clips = [p for p in parts(d, "net_clip") if p["id"].startswith("side_open_clip_L")]
        held = {n for c in clips for n in c["carried_by"]}
        self.assertLessEqual(set(opening["carried_by"]), held)


class LoadPathTest(unittest.TestCase):
    """R7 and R8 on the door set; R8's push on the fixed bars, the door taken as open (not pushed on, not stiffening)."""

    @classmethod
    def setUpClass(cls):
        from hiraku.catalog.builders import BUILDERS
        from hiraku.solvers.shelf import LoadPath
        from tests.solvers.test_builders_load_path import REQ, SPECS

        cls.d = GEN.build(GEN.apply_choices(DOOR, {}))
        cls.cases = {lc["load_case_id"]: lc for tid in ("R7", "R8") for lc in BUILDERS[tid](SPECS[tid], REQ, cls.d)}
        tool = LoadPath()
        cls.runs = {k: tool.run(cls.d, lc, [{"prediction_id": "pred-0001"}], {}) for k, lc in cls.cases.items()}
        bare = copy.deepcopy(cls.d)
        bare["parts"] = [p for p in bare["parts"] if p["kind"] not in ("door", "door_hinge", "door_latch")]
        cls.bare = tool.run(bare, cls.cases["R8-push-725N"], [{"prediction_id": "pred-0001"}], {})

    def test_all_pass(self):
        from tests.solvers.test_builders_load_path import judged, results

        self.assertEqual(sorted(self.cases), ["R7-foot-500N", "R7-hand-300N", "R8-free", "R8-push-725N", "R8-stress"])
        for k, lc in self.cases.items():
            rec = self.runs[k]
            self.assertEqual(rec["status"], "ok", rec.get("error"))
            self.assertTrue(judged(lc, rec), f"{k}: {results(rec).get(lc['criterion']['quantity'])}")
            self.assertEqual(results(rec)["free_parts"]["value"], 0.0)

    def test_push_on_the_fixed_bars(self):
        from tests.solvers.test_builders_load_path import judged

        for rec in (self.runs["R8-push-725N"], self.bare):
            pushed = [e["name"] for e in rec["effects"] if "を押す" in e["name"]]
            self.assertEqual(len(pushed), 4)
            self.assertTrue(all("member:side_bar_" in n for n in pushed), pushed)
        self.assertTrue(judged(self.cases["R8-push-725N"], self.bare))

    def test_door_does_not_stiffen_the_push(self):
        """The door is taken as open for the push: left out of the model, the same movement as without it."""
        from tests.solvers.test_builders_load_path import results

        q = "occupant_push_deflection"
        self.assertAlmostEqual(results(self.runs["R8-push-725N"])[q]["value"], results(self.bare)[q]["value"], places=6)


if __name__ == "__main__":
    unittest.main()

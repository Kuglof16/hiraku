"""designgen.ladder_4w: side_guard = net, a net on the roll bar's posts without side bars (build_side_net), and the
R8 push on it through load_path."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from hiraku import contract, designgen
from hiraku.designgen import ladder_4w
from hiraku.designgen.ladder_4w import _inside_by
from hiraku.solvers.shelf import load_path as LP

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
SET2 = {"part_mounts": "brackets", "pedal_mount": "bulkhead"}
GEN = ladder_4w


def parts(d, kind):
    return [p for p in d["parts"] if p["kind"] == kind]


def node(d, nid):
    return next(n["xyz_mm"] for n in d["nodes"] if n["id"] == nid)


class NetOptionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = designgen.regenerate(V1, {**SET2, "side_guard": "net"})

    def test_choice(self):
        self.assertEqual(GEN.choices_of(self.d)["side_guard"], "net")
        self.assertEqual(GEN.choice_values({"side_guard": "net"}),
                         {"side_bars": {"value": 0.0, "unit": "1"}, "side_net": {"value": 1.0, "unit": "1"}})
        contract.validate("design", self.d)

    def test_no_bars_and_an_outrigger_each_side(self):
        ids = {m["id"] for m in self.d["members"]}
        self.assertFalse([i for i in ids if i.startswith(("side_bar_", "side_post_"))])
        self.assertTrue({"side_net_foot_L", "side_net_foot_R"} <= ids)
        half = self.d["variables"]["rollbar_width"]["value"] / 2.0
        for side, sy in (("L", 1.0), ("R", -1.0)):
            self.assertAlmostEqual(node(self.d, f"{side}NO")[1], sy * half)  # out to the posts' line

    def test_net_and_clips(self):
        nets = parts(self.d, "side_net")
        clips = parts(self.d, "net_clip")
        self.assertEqual({p["id"] for p in nets}, {"side_net_L", "side_net_R"})
        self.assertEqual(len(clips) % 2, 0)
        for p in nets + clips:
            self.assertEqual(p["material"], "ss400", p["id"])
            self.assertEqual(p["finish"], "galvanized", p["id"])
            self.assertEqual(p["system"], "restraint", p["id"])
        n = next(p for p in nets if p["id"] == "side_net_L")
        self.assertAlmostEqual(n["box"]["size_mm"][1], ladder_4w.SIDE_NET_WIRE_MM)
        self.assertAlmostEqual(n["box"]["center_mm"][1], self.d["variables"]["rollbar_width"]["value"] / 2.0)
        # clips at most SIDE_NET_CLIP_PITCH_MM apart along the post
        zs = sorted(c["box"]["center_mm"][2] for c in clips if c["id"].startswith("side_net_clip_L")
                    and c["box"]["center_mm"][0] < n["box"]["center_mm"][0])
        self.assertLessEqual(max(b - a for a, b in zip(zs, zs[1:])), ladder_4w.SIDE_NET_CLIP_PITCH_MM + 1e-9)

    def test_point_r_inside_the_taut_net(self):
        # load_path's hull of the net's fastenings covers every point R with SIDE_NET_COVER_MM to spare, less the
        # clips' own size
        lp = LP.build(self.d, LP.find_contacts(self.d))
        self.assertEqual(lp.slack_nets, [])
        for t in LP.occupant_trunks(self.d):
            for name, x, out in LP.point_r(t):
                pid = "side_net_L" if out[1] > 0 else "side_net_R"
                m = lp.nets[pid]
                hull = [tuple(q) for q in m.hull]
                self.assertGreater(_inside_by(hull, m.net.local(x)), ladder_4w.SIDE_NET_COVER_MM - ladder_4w.SIDE_NET_CLIP_MM,
                                   f"{t['id']} {name}")

    def test_push_meets_the_net(self):
        out = LP.analyse_load_path(self.d, "push", {"push_force": 725.0, "safety_factor": 1.0})
        self.assertEqual(out["free"], [])
        r = {x["quantity"]: x for x in out["results"]}
        self.assertEqual(r["occupant_push_open"]["value"], 0.0)
        names = [e["name"] for e in out["effects"]]
        self.assertTrue(any("part:side_net_L を押す" in n for n in names))
        self.assertTrue(any("part:side_net_R を押す" in n for n in names))
        # the default net (3 mm wire, 50 mm pitch, 20 MPa pretension) sags more than R8's 25 mm
        self.assertGreater(r["occupant_push_deflection"]["value"], 25.0)

    def test_v1_unchanged(self):
        self.assertEqual(GEN.choices_of(V1)["side_guard"], "none")
        self.assertNotIn("side_net_wire_d", V1["variables"])


class NetOptionVariantsTest(unittest.TestCase):
    def test_needs_the_roll_bar(self):
        with self.assertRaises(designgen.DesignGenError):
            GEN.build({"rollbar": 0, "side_net": 1})

    def test_four_posts_make_a_rectangle(self):
        d = designgen.regenerate(V1, {**SET2, "side_guard": "net", "rops": "four_post"})
        self.assertFalse([m for m in d["members"] if m["id"].startswith("side_net_foot")])
        n = next(p for p in d["parts"] if p["id"] == "side_net_L")
        self.assertIn("柱のあいだ", n["name"])
        lp = LP.build(d, LP.find_contacts(d))
        hull = lp.nets["side_net_L"].hull
        self.assertEqual(len(hull), 4)

    def test_wire_and_pitch_are_variables(self):
        d = designgen.regenerate(V1, {**SET2, "side_guard": "net", "side_net_wire_d": {"value": 5.0, "unit": "mm"},
                                      "side_net_pitch": {"value": 25.0, "unit": "mm"}})
        n = next(p for p in d["parts"] if p["id"] == "side_net_L")
        self.assertAlmostEqual(n["box"]["size_mm"][1], 5.0)
        lp = LP.build(d, LP.find_contacts(d))
        m = lp.nets["side_net_L"]
        # the mass is the net's outline; load_path's hull of the clips is a little smaller, so its pitch reads finer
        pitch = 3.141592653589793 * 25.0 / 4.0 / m.a
        self.assertLess(pitch, 25.0)
        self.assertGreater(pitch, 22.0)

    def test_bars_net_still_on_the_bars(self):
        d = designgen.regenerate(V1, {**SET2, "side_guard": "bars_net"})
        self.assertFalse(parts(d, "net_clip"))
        self.assertTrue([m for m in d["members"] if m["id"].startswith("side_bar_")])


if __name__ == "__main__":
    unittest.main()

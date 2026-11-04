"""H7 (proposed) getting in and out: k4_geometry.entry and the entry_opening tool's known answers, on version 2
(side_guard bars_net: the bars close the side) and with side_opening (a door or a net on clips ahead of the side
frame round point R)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.designgen import ladder_4w as GEN
from hiraku.solvers.k4_geometry import entry as geo
from hiraku.solvers.shelf import EntryOpening

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
V2 = {"part_mounts": "brackets", "pedal_mount": "post", "side_guard": "bars_net"}
DOOR = {"part_mounts": "brackets", "pedal_mount": "post", "side_guard": "bars", "side_opening": "door",
        "grab_bar": "front_post"}
CLIP_NET = {"part_mounts": "brackets", "pedal_mount": "post", "side_guard": "bars_net", "side_opening": "clip_net",
            "grab_bar": "front_post"}


class GeometryTest(unittest.TestCase):
    def test_version_2_bars_close_the_side(self):
        d = GEN.build(GEN.apply_choices(V2, {}))
        boxes = geo.aabb(d)
        self.assertEqual(geo.openable_parts(d, boxes), {})  # the net is bolted to the bars: no clip
        for sp in geo.seat_spaces(d, boxes, {}):
            ids, _ = geo.doorway(sp, 450.0, 300.0, 985.2, 1250.0)
            s = "L" if sp.side > 0 else "R"
            self.assertEqual(ids, [f"member:side_bar_{s}1", f"member:side_bar_{s}2", f"part:side_net_{s}"])
            # the lowest bar's underside over the seat top: 82 - 42.7 / 2 (AIST trochanter height I11, as side_bar_low)
            h, _ = geo.clear_height(sp, sp.z_seat, 450.0)
            self.assertAlmostEqual(h, GEN.VARIABLES["side_bar_low"].default - 42.7 / 2.0, places=6)

    def test_door_and_clip_net_open(self):
        for choices, kind in ((DOOR, "door"), (CLIP_NET, "side_net")):
            d = GEN.build(GEN.apply_choices(choices, {}))
            opens = geo.openable_parts(d)
            mains = {p["id"] for p in d["parts"] if p["id"] in opens and p["kind"] == kind}
            self.assertEqual(len(mains), 2, choices)
            for sp in geo.seat_spaces(d, None, opens):
                self.assertEqual(geo.doorway(sp, 450.0, 300.0, 985.2, 1250.0)[0], [])
            # without counting what opens, the closure blocks
            for sp in geo.seat_spaces(d, None, {}):
                self.assertTrue(geo.doorway(sp, 450.0, 300.0, 985.2, 1250.0)[0])

    def test_window_is_the_persons_span(self):
        sp = next(s for s in geo.seat_spaces(V1) if s.seat == "seat_L")
        seat = next(p for p in V1["parts"] if p["id"] == "seat_L")
        self.assertAlmostEqual(sp.x_back, seat["box"]["center_mm"][0] - seat["box"]["size_mm"][0] / 2.0)
        boxes = geo.aabb(V1)
        feet = max(boxes[f"part:{p['id']}"].hi[0] for p in V1["parts"] if p["id"].startswith("occupant_L"))
        self.assertAlmostEqual(sp.x_front, float(feet))
        self.assertEqual(sp.side, 1.0)


class ToolTest(unittest.TestCase):
    def test_known_answers_and_spec(self):
        t = EntryOpening()
        checks = t.check()
        self.assertTrue(all(c["pass"] for c in checks), [c for c in checks if not c["pass"]])
        self.assertEqual(contract.errors("tool_spec", t.spec()), [])
        self.assertIn("entry_opening", [x.name for x in solvers.builtin_tools()])


if __name__ == "__main__":
    unittest.main()

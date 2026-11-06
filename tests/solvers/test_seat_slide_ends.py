"""H1 looks at the driver's seat at both ends of its slide (k4_geometry.people.slide_ends): the seat's box is at the
middle of the travel, so an obstacle the seat reaches only when slid forward or back shows as an interference with
the end it is at."""

import copy
import json
import unittest
from pathlib import Path

from hiraku import designgen, solvers
from hiraku.solvers.k4_geometry.people import slide_ends

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
LC = {"load_case_id": "H1-interference", "test_id": "H1", "domain": "assembly", "title": "H1", "params": {},
      "tolerance_rel": 0.05}


def slid(mm):
    return designgen.regenerate(V1, {"seat_slide": {"value": mm, "unit": "mm"}})


class SlideEndsTest(unittest.TestCase):
    def test_no_slide_no_ends(self):
        self.assertEqual(slide_ends(V1), [])

    def test_the_driver_seat_moves_half_the_travel_each_way(self):
        d = slid(300.0)
        mid = {p["id"]: p["box"]["center_mm"][0] for p in d["parts"]}
        ends = {label: (de, moved) for label, de, moved in slide_ends(d)}
        self.assertEqual(set(ends), {"front", "rear"})
        for label, dx in (("front", 150.0), ("rear", -150.0)):
            de, moved = ends[label]
            self.assertTrue(moved)
            for p in de["parts"]:
                if p["id"] in moved:
                    self.assertAlmostEqual(p["box"]["center_mm"][0], mid[p["id"]] + dx)

    def test_an_obstacle_reached_only_at_the_front_end(self):
        d = slid(300.0)
        moved = slide_ends(d)[0][2]  # the seat with its seatback and head restraint
        seat = next(p for p in d["parts"] if p["id"] in moved and p["kind"] == "seat")
        c, s = seat["box"]["center_mm"], seat["box"]["size_mm"]
        block = {"id": "test_block", "name": "block", "kind": "body", "mass_kg": 1.0, "carried_by": [d["nodes"][0]["id"]],
                 "box": {"center_mm": [c[0] + s[0] / 2 + 60.0, c[1], c[2]], "size_mm": [20.0, 50.0, 50.0]}}
        with_block = copy.deepcopy(d)
        with_block["parts"].append(block)
        tool = {t.name: t for t in solvers.builtin_tools()}["assembly"]
        rec = tool.run(with_block, LC, [{"prediction_id": "p"}], {})
        hits = [i for i in rec["interferences"] if "test_block" in (i["a"], i["b"])]
        self.assertEqual([i["condition"] for i in hits], ["seat at the slide's front end"])
        self.assertAlmostEqual(hits[0]["depth_mm"], 100.0, places=6)  # 150 mm forward, 50 mm past the block's face
        self.assertGreaterEqual({r["quantity"]: r["value"] for r in rec["results"]}["interference_count"], 1.0)


if __name__ == "__main__":
    unittest.main()

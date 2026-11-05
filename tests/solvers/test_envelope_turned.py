"""k4_geometry.joints.outside_envelopes with a turned envelope box (rpy_deg, as a part's box may have): the body's
corners are read in the box's own axes."""

import unittest

from hiraku.designgen import ladder_4w as GEN
from hiraku.solvers.k4_geometry.joints import outside_envelopes

D = GEN.build()
CONTROLLER = next(p for p in D["parts"] if p["id"] == "controller")["box"]  # 250 x 200 x 80, not turned


def _out(rpy):
    box = {"center_mm": list(CONTROLLER["center_mm"]), "size_mm": [260.0, 210.0, 130.0]}
    if rpy is not None:
        box["rpy_deg"] = rpy
    got = {k: mm for k, _, mm in outside_envelopes(D, envelopes={"powertrain": [box]})}
    return got.get("part:controller", 0.0)


class TurnedEnvelopeTest(unittest.TestCase):
    def test_turned_box_reads_its_own_axes(self):
        self.assertEqual(CONTROLLER["size_mm"][:2], [250.0, 200.0])
        self.assertEqual(_out(None), 0.0)
        self.assertEqual(_out([0.0, 0.0, 0.0]), 0.0)
        self.assertAlmostEqual(_out([0.0, 0.0, 90.0]), 20.0, places=6)  # 250 long in a box 210 wide along x


if __name__ == "__main__":
    unittest.main()

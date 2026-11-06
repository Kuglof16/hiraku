"""STEP files written by other CAD programs. They are not in the repository (their licence is not
known): the tests read them from HIRAKU_CAD_SAMPLES (default C:/tmp/hiraku-cad-samples) and skip
when the file is not there.

- as1-oc-214.stp: an assembly of 18 solids (nuts, bolts, a rod, plates); 53 faces, 25 planes and
  28 rational B-spline surfaces (Open CASCADE).
- Ventilator.stp: one solid with B-spline, revolution, extrusion, cone, cylinder and torus faces
  (CATIA V5).
"""

import math
import os
import time
import unittest
from pathlib import Path

from hiraku.cad.step_read import read_step

SAMPLES = Path(os.environ.get("HIRAKU_CAD_SAMPLES", "C:/tmp/hiraku-cad-samples"))
AS1 = SAMPLES / "as1-oc-214.stp"
VENT = SAMPLES / "Ventilator.stp"
SLOW = os.environ.get("HIRAKU_SLOW_TESTS") == "1"


def _read(path: Path, refine: int = 1):
    return read_step(path.read_text(encoding="latin-1"), refine=refine)


def _box(s) -> float:
    return math.prod(s.size_mm)


@unittest.skipUnless(AS1.exists(), f"{AS1.name} is not in {SAMPLES}")
class As1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t0 = time.perf_counter()
        cls.solids = _read(AS1)
        cls.seconds = time.perf_counter() - t0
        cls.fine = _read(AS1, refine=2)

    def test_every_solid_is_integrated(self):
        self.assertEqual(len(self.solids), 18)
        for s in self.solids:
            self.assertEqual(s.method, "approximate", s.name)
            self.assertEqual(s.notes, [], s.name)
            self.assertGreater(s.volume_mm3, 0.0)
            self.assertLess(s.volume_mm3, _box(s), s.name)
            for c, lo, hi in zip(s.center_mm, s.bbox_min, s.bbox_max):
                self.assertTrue(lo < c < hi, s.name)

    def test_rod_is_pi_r2_l_from_its_outer_size(self):
        (rod,) = [s for s in self.solids if s.name == "rod"]
        a, b, L = sorted(rod.size_mm)
        self.assertAlmostEqual(a, b, delta=1e-6)
        want = math.pi * (a / 2) ** 2 * L
        err = abs(rod.volume_mm3 - want) / want
        print(f"\n[cad] as1 rod: {rod.volume_mm3:.3f} mm3, pi r^2 L = {want:.3f} (r {a / 2}, L {L}), error {err:.2e}", end="")
        self.assertLess(err, 0.005)

    def test_nut_lies_within_its_box(self):
        nuts = [s for s in self.solids if s.name == "nut"]
        self.assertEqual(len(nuts), 8)
        for n in nuts:
            self.assertAlmostEqual(n.volume_mm3, nuts[0].volume_mm3, delta=1e-6 * nuts[0].volume_mm3)
            self.assertTrue(0.2 * _box(n) < n.volume_mm3 < _box(n))
        print(f"\n[cad] as1 nut: {nuts[0].volume_mm3:.3f} mm3 in a box of {_box(nuts[0]):.1f} mm3", end="")

    def test_closed_shells_hold_under_refinement(self):
        worst = 0.0
        for s, f in zip(self.solids, self.fine):
            self.assertEqual((s.name, s.entity_id), (f.name, f.entity_id))
            d = abs(f.volume_mm3 - s.volume_mm3) / f.volume_mm3
            worst = max(worst, d)
            self.assertLess(d, 0.01, s.name)
            for a, b in zip(s.center_mm, f.center_mm):
                self.assertAlmostEqual(a, b, delta=1e-3 * max(s.size_mm))
        print(f"\n[cad] as1: 18 solids, worst change with refine 2: {worst:.2e}", end="")

    def test_reads_in_under_ten_seconds(self):
        print(f"\n[cad] as1 read in {self.seconds:.2f} s", end="")
        self.assertLess(self.seconds, 10.0)


@unittest.skipUnless(VENT.exists(), f"{VENT.name} is not in {SAMPLES}")
class VentilatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t0 = time.perf_counter()
        (cls.s,) = _read(VENT)
        cls.seconds = time.perf_counter() - t0

    def test_volume_is_below_its_box(self):
        s = self.s
        print(f"\n[cad] Ventilator: {s.volume_mm3:.3f} mm3, box {[round(x, 3) for x in s.size_mm]} "
              f"= {_box(s):.1f} mm3, centre {[round(x, 4) for x in s.center_mm]}, read in {self.seconds:.1f} s", end="")
        self.assertEqual(s.method, "approximate")
        self.assertEqual(s.notes, [])
        self.assertTrue(0.0 < s.volume_mm3 < _box(s))

    @unittest.skipUnless(SLOW, "the Ventilator read at refine 2 takes about a minute (HIRAKU_SLOW_TESTS=1)")
    def test_stable_under_refinement(self):
        (f,) = _read(VENT, refine=2)
        d = abs(f.volume_mm3 - self.s.volume_mm3) / f.volume_mm3
        print(f"\n[cad] Ventilator refine 2: {f.volume_mm3:.3f} mm3, change {d:.2e}", end="")
        self.assertLess(d, 0.01)


if __name__ == "__main__":
    unittest.main()

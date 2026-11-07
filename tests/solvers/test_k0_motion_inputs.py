"""assembly.motion_count's inputs (k0_handcalc.assembly_inputs.motion_inputs) follow the assembly tool's sweep
(k4_geometry.motion.sweep): the same steer angles and wheel travels, and the same moving parts (the knuckle too)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import numpy as np

from hiraku.solvers.k0_handcalc.assembly_inputs import motion_inputs, moving_parts, sweep_grid
from hiraku.solvers.k4_geometry import motion, sweep

ROOT = Path(__file__).resolve().parents[2]
MATERIAL = {"name": "m", "E_MPa": 1.0, "nu": 0.3, "density_kg_m3": 1.0, "yield_MPa": 1.0, "source": "check",
            "verified": True}


def _part(pid, kind, center, size):
    return {"id": pid, "name": pid, "kind": kind, "box": {"center_mm": list(center), "size_mm": list(size)},
            "mass_kg": 1.0}


def knuckle_design() -> dict:
    """A steered corner (support at y 600, travel 100 mm, max_steer_angle 20 deg): a 600 x 200 x 600 wheel and a
    100 x 80 x 100 knuckle inboard of it (y 410..490, top at z 350), and a fixed 20 x 20 bar along x at y 450,
    z 375..395, x -50..50. The knuckle reaches the bar only near the top of the travel (by 20 mm at +50 mm); the
    wheel's inner face at 20 deg is inboard of y 500 by 100 (1/cos 20 - 1) + 50 tan 20 = 24.6 mm at the bar's end,
    short of the bar's face at y 460 (at 30 deg it would reach it by 3.8 mm)."""
    return {"design_version": 1, "name": "knuckle", "variables": {"max_steer_angle": {"value": 20.0, "unit": "deg"}},
            "parts": [_part("wheel_X", "wheel", [0, 600, 300], [600, 200, 600]),
                      _part("knuckle_X", "knuckle", [0, 450, 300], [100, 80, 100])],
            "nodes": [{"id": "b0", "xyz_mm": [-50, 450, 385]}, {"id": "b1", "xyz_mm": [50, 450, 385]}],
            "members": [{"id": "bar", "node_a": "b0", "node_b": "b1", "section": "bar", "material": "m"}],
            "sections": {"bar": {"shape": "rect_solid", "dims_mm": {"b": 20, "h": 20}}},
            "materials": {"m": MATERIAL},
            "supports": [{"id": "X", "kind": "wheel", "position_mm": [0, 600, 300], "radius_mm": 300,
                          "suspension": {"type": "independent", "travel_mm": 100.0},
                          "actuation": {"drive": False, "brake": True, "steer": True}}]}


class SweepGridTest(unittest.TestCase):
    def test_steer_range_equals_the_tools(self):
        d = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
        th = motion.max_steer(d)
        for s in d["supports"]:
            steers, dzs = sweep_grid(d, s)
            travel = float(s["suspension"]["travel_mm"])
            with self.subTest(support=s["id"]):
                if s["actuation"]["steer"]:
                    np.testing.assert_allclose(steers, np.linspace(-th, th, motion.STEPS))
                    self.assertEqual((steers[0], steers[-1]), (-th, th))
                else:
                    self.assertEqual(steers, [0.0])
                np.testing.assert_allclose(dzs, np.linspace(-travel / 2.0, travel / 2.0, motion.STEPS))

    def test_moving_parts_are_the_tools(self):
        d = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
        fl = moving_parts(d)["FL"]
        self.assertIn("part:knuckle_FL", fl["box"])
        self.assertIn("part:caliper_FL", fl["box"])
        self.assertIn("part:tie_rod_FL", fl["rod"])
        self.assertIn("part:damper_FL", fl["rod"])


class KnuckleOnlyTest(unittest.TestCase):
    def test_counted_by_both(self):
        d = knuckle_design()
        hand = {frozenset((a, b)) for a, b, _ in motion_inputs(d)["pairs"]}
        tool = {frozenset((a, b)) for a, b, _, _ in sweep(d)["hits"]}
        pair = frozenset(("part:knuckle_X", "member:bar"))
        self.assertEqual(hand, {pair})
        self.assertEqual(tool, {pair})
        # the bar's 20 mm (z 375..395, y 440..460) inside the knuckle's top at +50 mm (z up to 400, y 410..490)
        got = next(o for a, b, o in motion_inputs(d)["pairs"])
        self.assertAlmostEqual(got, 20.0, places=6)


if __name__ == "__main__":
    unittest.main()

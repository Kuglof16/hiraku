"""load_path: plate parts (floors, the deck, the bulkhead, nets) as strips of the plate's own stiffness, not rigid
bodies (hiraku.solvers.shelf.load_path.plate_parts)."""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from hiraku import designgen
from hiraku.solvers.shelf import load_path as LP
from tests.solvers.test_load_path import VALUES, quantity

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
# the version 2 set of the design options (part mounts, pedals on the bulkhead, side bars and nets)
SET2 = {"part_mounts": "brackets", "pedal_mount": "bulkhead", "side_guard": "bars_net"}


def rigid():
    """Every part a rigid body again (the model before plates)."""
    return mock.patch.object(LP, "PLATE_RATIO", -1.0)


class PlateKnownAnswerTest(unittest.TestCase):
    def test_strip_across_two_rails(self):
        r = LP.plate_strip_known_answer()
        self.assertTrue(r["pass"], r)
        self.assertIn(r, LP.load_path_known_answers())

    def test_strip_section(self):
        s = LP.strip_section(100.0, 2.0)
        self.assertAlmostEqual(s["A"], 200.0)
        self.assertAlmostEqual(s["I_out"], 100.0 * 8.0 / 12.0)
        self.assertAlmostEqual(s["I_in"], 2.0 * 1e6 / 12.0)
        # a thin strip's J tends to b t^3 / 3 (less the end correction 0.21 t / b of it)
        self.assertAlmostEqual(s["J"], 100.0 * 8.0 * (1.0 / 3.0 - 0.21 * 0.02 * (1.0 - 0.02 ** 4 / 12.0)))

    def test_strip_widths_spread_at_45_degrees(self):
        pl = LP.Plate("p", np.zeros(3), np.array([0.0, 0.0, 1.0]), np.eye(3)[:2], np.array([500.0, 200.0]), 5.0,
                      205000.0, 205000.0 / 2.6, "steel")
        # a strip along x to a contact 400 mm out, 40 mm wide: 40 + 2 s at each element's middle, at most 400 across
        w = LP.strip_widths(pl, np.array([400.0, 0.0, 0.0]), 40.0, 4)
        np.testing.assert_allclose(w, [min(400.0, 40.0 + 2.0 * s) for s in (350.0, 250.0, 150.0, 50.0)])
        self.assertEqual(w[0], 400.0)


class Version1Test(unittest.TestCase):
    """Plates do not change which parts float, nor which load points have no path."""

    def test_free_parts_and_unsupported_load_unchanged(self):
        for kind in ("foot", "push", "inertia"):
            got = LP.analyse_load_path(V1, kind, VALUES)
            with rigid():
                old = LP.analyse_load_path(V1, kind, VALUES)
            self.assertEqual(got["free"], old["free"], kind)
            self.assertEqual(len(got["free"]), 16, kind)
            self.assertEqual(quantity(got, "unsupported_load"), quantity(old, "unsupported_load"), kind)
        self.assertEqual(quantity(LP.analyse_load_path(V1, "foot", VALUES), "unsupported_load")["value"], 2.0)
        self.assertEqual(quantity(LP.analyse_load_path(V1, "push", VALUES), "unsupported_load")["value"], 4.0)


class Version2SetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = designgen.regenerate(V1, SET2)

    def test_which_parts_are_plates(self):
        plates = LP.plate_parts(self.d)
        self.assertEqual(set(plates), {"floor_front", "floor", "deck", "bulkhead", "side_net_L", "side_net_R"})
        self.assertAlmostEqual(plates["floor"].t, 3.2)  # the box is the sheet (designgen.ladder_4w.PLATE_T_MM)
        self.assertEqual(plates["floor"].material, "ss400")
        self.assertFalse(any(pl.assumed for pl in plates.values()))
        self.assertEqual(plates["side_net_L"].material, "ss400")
        self.assertAlmostEqual(plates["side_net_L"].t, 3.0)
        np.testing.assert_allclose(np.abs(plates["floor"].normal), [0.0, 0.0, 1.0])

    def test_plates_are_softer_than_rigid_floors(self):
        got = LP.analyse_load_path(self.d, "push", VALUES)
        with rigid():
            old = LP.analyse_load_path(self.d, "push", VALUES)
        a, b = (quantity(o, "occupant_push_deflection")["value"] for o in (got, old))
        self.assertGreater(a, b)
        self.assertEqual(got["free"], [])

    def test_plate_nodes_under_each_contact(self):
        lp = LP.build(self.d, LP.find_contacts(self.d))
        floor = lp.plates["floor"]
        nodes = lp.model.nodes[list(lp.plate_nodes["floor"].values())]
        # every node of the floor is on its mid-plane, inside its outline
        np.testing.assert_allclose((nodes - floor.center) @ floor.normal, 0.0, atol=1e-9)
        self.assertTrue(np.all(np.abs((nodes - floor.center) @ floor.inplane.T) <= floor.half + 1e-9))
        self.assertGreaterEqual(len(nodes), 4)  # the two mounts and the two seats (the battery is under the centre)
        self.assertEqual(len(lp.plate_beams.conn) % LP.PLATE_ARM_ELEMENTS, 0)
        self.assertTrue(math.isclose(min(lp.plate_beams.E), LP.PLATE_MATERIAL["E_MPa"]))


if __name__ == "__main__":
    unittest.main()

"""The plate parts of ladder_4w (the floors, the deck, the bulkhead) are boxes as thick as the sheet, of part.material,
weighing area x the catalogue's unit mass; load_path builds their strips from that box and that material, and names the
plates whose material it had to assume."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from hiraku import designgen
from hiraku.designgen import ladder_4w
from hiraku.solvers.shelf import load_path as LP
from tests.solvers.test_load_path import VALUES, quantity

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
SET2 = {"part_mounts": "brackets", "pedal_mount": "bulkhead", "side_guard": "bars_net"}


def part(design, pid):
    return next(p for p in design["parts"] if p["id"] == pid)


class PlatePartsTest(unittest.TestCase):
    def test_box_is_the_sheet(self):
        # the mass: the area x the catalogue's unit mass at the thickness (Nippon Steel A101, checkers included)
        kg_m2 = {2.3: 19.73, 3.2: 26.79}
        for pid, t in ladder_4w.PLATE_T_MM.items():
            p = part(V1, pid)
            s = sorted(p["box"]["size_mm"])
            self.assertAlmostEqual(s[0], t, msg=pid)
            self.assertEqual(p["material"], ladder_4w.PLATE_STEEL, pid)
            self.assertAlmostEqual(p["mass_kg"], s[1] * s[2] * 1e-6 * kg_m2[t], places=9, msg=pid)
        self.assertEqual(part(V1, "front_body")["material"], ladder_4w.PLATE_STEEL)
        self.assertEqual(part(V1, "front_body")["mass_kg"], ladder_4w.FRONT_BODY_KG)

    def test_load_path_reads_the_box_and_the_material(self):
        plates = LP.plate_parts(V1)
        self.assertEqual(set(plates), set(ladder_4w.PLATE_T_MM))
        for pid, pl in plates.items():
            self.assertAlmostEqual(pl.t, ladder_4w.PLATE_T_MM[pid], msg=pid)
            self.assertEqual(pl.material, "ss400", pid)
            self.assertFalse(pl.assumed, pid)

    def test_the_floors_stay_on_the_rails_and_under_the_seats(self):
        # the plate is thinner than the 20 mm box it was; its bottom stays on the rails, the seat on its top
        floor = part(V1, "floor")
        top = floor["box"]["center_mm"][2] + floor["box"]["size_mm"][2] / 2.0
        seat = part(V1, "seat_R")
        self.assertAlmostEqual(seat["box"]["center_mm"][2] - seat["box"]["size_mm"][2] / 2.0, top, places=9)
        for pid in ("floor", "floor_front"):
            p = part(V1, pid)
            self.assertAlmostEqual(p["box"]["center_mm"][2] - p["box"]["size_mm"][2] / 2.0,
                                   part(V1, "deck")["box"]["center_mm"][2] - part(V1, "deck")["box"]["size_mm"][2] / 2.0,
                                   places=9, msg=pid)


class AssumedMaterialTest(unittest.TestCase):
    def test_a_plate_without_material_is_named(self):
        d = copy.deepcopy(V1)
        del part(d, "deck")["material"]
        plates = LP.plate_parts(d)
        self.assertTrue(plates["deck"].assumed)
        self.assertEqual(plates["deck"].material, LP.PLATE_MATERIAL["name"])
        out = LP.analyse_load_path(d, "inertia", VALUES)
        named = [e for e in out["effects"] if "part.material" in e["name"]]
        self.assertEqual(len(named), 1)
        self.assertIn("deck", named[0]["name"])
        self.assertEqual(named[0]["value"], 1.0)

    def test_none_named_when_every_plate_has_one(self):
        out = LP.analyse_load_path(V1, "inertia", VALUES)
        self.assertFalse([e for e in out["effects"] if "part.material" in e["name"]])
        self.assertEqual(len(out["free"]), 0)  # every part joined in shape (kit/joints.py); 16 floated before


class Version2SetTest(unittest.TestCase):
    def test_the_sheet_bulkhead_is_softer_under_the_brake_pedal(self):
        d = designgen.regenerate(V1, SET2)
        thick = copy.deepcopy(d)
        bh = part(thick, "bulkhead")
        bh["box"]["size_mm"][0] = 20.0  # the 20 mm box it was (its front face where it was)
        bh["box"]["center_mm"][0] -= (20.0 - ladder_4w.PLATE_T_MM["bulkhead"]) / 2.0
        a, b = (quantity(LP.analyse_load_path(x, "foot", VALUES), "pedal_deflection.pedal_brake")["value"]
                for x in (d, thick))
        self.assertGreater(a, b)


if __name__ == "__main__":
    unittest.main()

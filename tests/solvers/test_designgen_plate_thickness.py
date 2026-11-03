"""ladder_4w's plate thickness variables (floor_t, deck_t, bulkhead_t): revisable among the catalogue's standard
thicknesses whose unit mass was read (PLATE_KG_M2), the defaults build version 1 as before, and each sets its plates'
boxes (and what stands on the floors)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.designgen import DesignGenError
from hiraku.designgen import ladder_4w as GEN

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))


def _parts(d):
    return {p["id"]: p for p in d["parts"]}


def _top(p):
    return p["box"]["center_mm"][2] + p["box"]["size_mm"][2] / 2.0


class PlateThicknessTest(unittest.TestCase):
    def test_variables(self):
        spec = {r["name"]: r for r in GEN.spec()}
        for k, t in (("floor_t", 3.2), ("deck_t", 3.2), ("bulkhead_t", 2.3)):
            self.assertTrue(spec[k]["revisable"], k)
            self.assertEqual((spec[k]["default"], spec[k]["min"], spec[k]["max"]), (t, 2.3, 3.2))
            self.assertNotIn(k, V1["variables"])
        self.assertEqual(GEN.PLATE_KG_M2, {2.3: 19.73, 3.2: 26.79})

    def test_defaults_build_version_1(self):
        self.assertEqual(GEN.build(), V1)
        self.assertEqual(GEN.build({"floor_t": 3.2, "deck_t": 3.2, "bulkhead_t": 2.3}), V1)

    def test_only_catalogue_thicknesses(self):
        for k, t in (("floor_t", 2.6), ("bulkhead_t", 3.0), ("deck_t", 4.5)):
            with self.assertRaises(DesignGenError, msg=f"{k} {t}"):
                GEN.build({k: t})

    def test_each_sets_its_plates(self):
        base = _parts(V1)
        d = GEN.build({"bulkhead_t": 3.2, "deck_t": 2.3})
        contract.validate("design", d)
        p = _parts(d)
        self.assertAlmostEqual(min(p["bulkhead"]["box"]["size_mm"]), 3.2)
        self.assertAlmostEqual(min(p["deck"]["box"]["size_mm"]), 2.3)
        self.assertEqual(d["variables"]["bulkhead_t"], {"value": 3.2, "unit": "mm"})
        self.assertAlmostEqual(_top(p["cargo"]) - _top(base["cargo"]), -0.9, places=6)  # the cargo lies on the deck
        self.assertEqual(p["seat_R"]["box"], base["seat_R"]["box"])

    def test_floor_moves_what_stands_on_it(self):
        d = GEN.build({"floor_t": 2.3})
        p, base = _parts(d), _parts(V1)
        for pid in ("floor_front", "floor"):
            self.assertAlmostEqual(min(p[pid]["box"]["size_mm"]), 2.3, msg=pid)
            self.assertAlmostEqual(p[pid]["box"]["center_mm"][2] - p[pid]["box"]["size_mm"][2] / 2.0,
                                   base[pid]["box"]["center_mm"][2] - base[pid]["box"]["size_mm"][2] / 2.0)  # on the rails
        for pid in ("seat_R", "occupant_R", "steering_wheel"):
            self.assertAlmostEqual(p[pid]["box"]["center_mm"][2] - base[pid]["box"]["center_mm"][2], -0.9, places=6,
                                   msg=pid)


if __name__ == "__main__":
    unittest.main()

"""ladder_4w's seat_depth: the seats' cushion depth, revisable within a range from the AIST body sizes in
k4_geometry.people; the default builds version 1; the seat back stays, so the people, the knee room and the belts
stay; rule HU-02 (cushion depth at most 406 mm) passes or hits as the variable moves."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.designgen import DesignGenError
from hiraku.designgen import ladder_4w as GEN
from hiraku.intake import knowledge, rulecheck
from hiraku.solvers.k4_geometry import people

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
SEATS = ("seat_R", "seat_L")
HU02 = next(r for r in knowledge.all_rules(knowledge.load_builtin()) if r["rule_id"] == "HU-02")


def _parts(d):
    return {p["id"]: p for p in d["parts"]}


def _rear(p):
    return p["box"]["center_mm"][0] - p["box"]["size_mm"][0] / 2.0


def _hu02_hits(d):
    [check] = rulecheck.check_all([HU02], d)
    return [h["part_id"] for h in check["hits"]]


class SeatDepthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.short = GEN.build({"seat_depth": 400.0})

    def test_variable_and_range_from_the_body_sizes(self):
        spec = {r["name"]: r for r in GEN.spec()}["seat_depth"]
        self.assertTrue(spec["revisable"])
        self.assertEqual(spec["unit"], "mm")
        big, small = people.SIZES[people.BIG], people.SIZES[people.SMALL]
        self.assertEqual(spec["default"], GEN.SEAT_LENGTH_MM)
        self.assertEqual(spec["min"], 2.0 * big["buttock_trochanter"])  # the large person's trunk box (kit/body.py)
        self.assertEqual(spec["max"], small["buttock_knee"])
        self.assertEqual((spec["min"], spec["max"]), (292.0, 511.0))
        self.assertNotIn("seat_depth", V1["variables"])
        for bad in (291.0, 512.0):
            with self.assertRaises(DesignGenError):
                GEN.build({"seat_depth": bad})

    def test_default_builds_version_1(self):
        from tests.solvers.same_design import assert_same_design

        assert_same_design(self, GEN.build({"seat_depth": GEN.SEAT_LENGTH_MM}), V1)

    def test_the_cushion_front_moves_the_back_stays(self):
        d, v1 = _parts(self.short), _parts(V1)
        for sid in SEATS:
            self.assertEqual(d[sid]["box"]["size_mm"][0], 400.0)
            self.assertAlmostEqual(_rear(d[sid]), _rear(v1[sid]), places=9)
            self.assertEqual(d[sid]["box"]["size_mm"][1:], v1[sid]["box"]["size_mm"][1:])
            self.assertEqual(d[sid]["mass_kg"], v1[sid]["mass_kg"])
        self.assertEqual(self.short["variables"]["seat_depth"], {"value": 400.0, "unit": "mm"})
        contract.validate("design", self.short)

    def test_people_belts_and_knee_room_read_the_seat_back(self):
        d, v1 = _parts(self.short), _parts(V1)
        moved = [pid for pid in v1 if pid not in SEATS and d[pid]["box"] != v1[pid]["box"]]
        self.assertEqual(moved, [])  # the occupants' bodies, the pedals, the wheel and the belts stay
        self.assertEqual(people.knee_space(self.short), people.knee_space(V1))
        fit_short, fit_v1 = people.fit(self.short), people.fit(V1)
        self.assertEqual(set(fit_short), set(fit_v1))
        for k in fit_v1:
            self.assertAlmostEqual(fit_short[k][0], fit_v1[k][0], places=6, msg=k)

    def test_hu02_hits_and_passes_with_the_depth(self):
        self.assertEqual(_hu02_hits(V1), list(SEATS))  # v1's 500 mm is over 406 mm
        self.assertEqual(_hu02_hits(self.short), [])
        self.assertEqual(_hu02_hits(GEN.build({"seat_depth": 406.0})), [])
        self.assertEqual(_hu02_hits(GEN.build({"seat_depth": 407.0})), list(SEATS))


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from pathlib import Path

from hiraku import contract, designgen, solvers

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
GEN = designgen.generator("offroad-4w")
PRED = [{"prediction_id": "pred-0001"}]


def mm(v):
    return {"value": v, "unit": "mm"}


def results(rec):
    return {r["quantity"]: r["value"] for r in rec["results"]}


class DesignGenTest(unittest.TestCase):
    def test_defaults_build_v1(self):
        self.assertEqual(GEN.build(), V1)

    def test_spec(self):
        names = set()
        for v in GEN.spec():
            names.add(v["name"])
            self.assertIn(v["unit"], contract.UNITS, v["name"])
            if v["revisable"]:
                self.assertLessEqual(v["min"], v["default"])
                self.assertLessEqual(v["default"], v["max"])
        self.assertTrue(names <= set(V1["variables"]))
        for needed in ("wheelbase", "track", "rail_spacing", "rail_b", "rail_h", "rail_t", "max_steer_angle", "seat_x",
                       "seat_y", "steering_x", "battery_x", "front_spring_rate", "rear_spring_rate"):
            self.assertIn(needed, names)

    def test_revise_round_trip(self):
        v2 = GEN.build(GEN.values_of(V1), version=2, parent=1, reason="same variables")
        self.assertEqual({k: v for k, v in v2.items() if k not in ("design_version", "parent_version", "name", "change_reason")},
                         {k: v for k, v in V1.items() if k not in ("design_version", "parent_version", "name", "change_reason")})
        self.assertEqual((v2["design_version"], v2["parent_version"]), (2, 1))

    def test_every_range_end_builds_or_says_why(self):
        for v in GEN.spec():
            if not v["revisable"]:
                continue
            for end in (v["min"], v["max"]):
                try:
                    d = GEN.build({v["name"]: {"value": end, "unit": v["unit"]}})
                except designgen.DesignGenError as e:
                    self.assertTrue(str(e))
                    continue
                contract.validate("design", d)
                self.assertEqual(d["variables"][v["name"]]["value"], end)

    def test_refusals(self):
        with self.assertRaisesRegex(designgen.DesignGenError, "outside"):
            GEN.build({"wheelbase": mm(3000)})
        with self.assertRaisesRegex(designgen.DesignGenError, "unknown variable"):
            GEN.build({"wheel_base": mm(1700)})
        with self.assertRaisesRegex(designgen.DesignGenError, "rails reach the wheels"):
            GEN.build({"rail_spacing": mm(900), "track": mm(900)})
        with self.assertRaisesRegex(designgen.DesignGenError, "stations"):
            GEN.build({"seat_x": mm(-1100), "wheelbase": mm(1400)})

    def test_variables_fix_the_known_defects(self):
        """v1: the front wheels touch the rails before 30 deg of steer, the driver's knees hit
        the steering by 41 mm. A wider track and the steering 50 mm forward clear both."""
        tool = {t.name: t for t in solvers.builtin_tools()}["assembly"]
        d = GEN.build({**GEN.values_of(V1), "track": mm(1220), "steering_x": mm(-200)}, version=2, parent=1, reason="fix")
        contract.validate("design", d)
        r = results(tool.run(d, {"load_case_id": "H", "test_id": "H1", "domain": "assembly", "title": "H", "params": {},
                                 "tolerance_rel": 0.05}, PRED, {}))
        self.assertGreaterEqual(r["free_steer_angle"], 30.0)
        self.assertGreaterEqual(r["knee_room"], 0.0)
        self.assertEqual(r["interference_count"], 0.0)

    def test_regenerate(self):
        d = designgen.regenerate(V1, {"track": mm(1220)})
        self.assertEqual(d["variables"]["track"]["value"], 1220.0)
        self.assertEqual([s["position_mm"][1] for s in d["supports"]], [610.0, -610.0, 610.0, -610.0])
        for k in ("design_version", "parent_version", "name", "change_reason"):
            self.assertEqual(d[k], V1[k])
        self.assertEqual({k: v for k, v in d["variables"].items() if k != "track"},
                         {k: v for k, v in V1["variables"].items() if k != "track"})
        with self.assertRaisesRegex(ValueError, "cannot be changed"):
            designgen.regenerate(V1, {"occupant_mass": {"value": 60, "unit": "kg"}})
        with self.assertRaisesRegex(ValueError, "outside 900 to 1400 mm"):
            designgen.regenerate(V1, {"track": mm(2000)})
        with self.assertRaisesRegex(ValueError, "unknown variable"):
            designgen.regenerate(V1, {"trak": mm(1200)})
        with self.assertRaisesRegex(ValueError, "match no design generator"):
            designgen.regenerate({"variables": {}}, {})

    def test_variables_listing(self):
        got = {v["name"]: v for v in designgen.variables(V1)}
        self.assertEqual(got["track"]["value"], 1100.0)
        self.assertEqual((got["track"]["min"], got["track"]["max"]), (900.0, 1400.0))
        self.assertFalse(got["occupant_mass"]["revisable"])
        json.dumps(list(got.values()), allow_nan=False)

    def test_battery_follows_its_capacity(self):
        d = GEN.build({"battery_capacity": {"value": 150, "unit": "Ah"}})
        bat = next(p for p in d["parts"] if p["id"] == "battery")
        self.assertAlmostEqual(bat["mass_kg"], 75.0)
        self.assertAlmostEqual(bat["box"]["size_mm"][0], 750.0)


if __name__ == "__main__":
    unittest.main()

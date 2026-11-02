import json
import runpy
import unittest
from pathlib import Path

from hiraku import contract
from hiraku.solvers.k0_handcalc.vehicle import mass_summary

ROOT = Path(__file__).resolve().parents[2]


class DemoDesignTest(unittest.TestCase):
    def setUp(self):
        self.design = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))

    def test_fits_the_contract(self):
        contract.validate("design", self.design)

    def test_file_matches_its_generator(self):
        mod = runpy.run_path(str(ROOT / "data" / "designs" / "make_offroad_4w_v1.py"))
        self.assertEqual(mod["build"](), self.design)

    def test_references_resolve(self):
        nodes = {n["id"] for n in self.design["nodes"]}
        for m in self.design["members"]:
            self.assertIn(m["node_a"], nodes)
            self.assertIn(m["node_b"], nodes)
            self.assertIn(m["section"], self.design["sections"])
            self.assertIn(m["material"], self.design["materials"])
        for s in self.design["supports"]:
            self.assertIn(s["frame_node"], nodes)
        for p in self.design["parts"]:
            for n in p.get("carried_by", []):
                self.assertIn(n, nodes, p["id"])

    def test_brief(self):
        """2 seats, 4 wheels, curb mass within 500 kg, origin at the front axle, x forward."""
        parts = {p["id"]: p for p in self.design["parts"]}
        self.assertEqual(sum(1 for p in parts.values() if p["kind"] == "occupant"), 2)
        self.assertEqual(len(self.design["supports"]), 4)
        curb = mass_summary(self.design)["mass_kg"] - sum(p["mass_kg"] for p in parts.values() if p["kind"] in ("occupant", "cargo"))
        self.assertLessEqual(curb, 500.0)
        front = [s for s in self.design["supports"] if s["actuation"]["steer"]]
        self.assertTrue(all(s["position_mm"][0] == 0.0 for s in front))
        rear = [s for s in self.design["supports"] if not s["actuation"]["steer"]]
        self.assertTrue(all(s["position_mm"][0] < 0.0 for s in rear))
        self.assertFalse(any(p["kind"] == "rollbar" for p in parts.values()))


class MaterialsTest(unittest.TestCase):
    def test_materials_fit_the_contract_and_say_if_checked(self):
        mats = json.loads((ROOT / "data" / "materials" / "materials.json").read_text(encoding="utf-8"))
        self.assertTrue(mats)
        for key, m in mats.items():
            contract.validate("material", m)
            if not m["verified"]:
                self.assertIn("Not checked", m["source"], key)


if __name__ == "__main__":
    unittest.main()

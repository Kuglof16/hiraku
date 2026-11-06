import json
import re
import unittest
from pathlib import Path

from hiraku import contract

ROOT = Path(__file__).resolve().parents[2] / "data" / "materials"
CARDS = sorted((ROOT / "cards").glob("*.json"))
# materials.json's own names -> the card's property names
SAME = {"E_MPa": "E_MPa", "nu": "nu", "density_kg_m3": "density_kg_m3", "yield_MPa": "yield_MPa", "tensile_MPa": "tensile_MPa",
        "elongation_pct": "elongation_pct"}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


class MaterialCardTest(unittest.TestCase):
    def test_there_are_cards(self):
        self.assertGreaterEqual(len(CARDS), 4)
        self.assertLessEqual(len(CARDS), 12)

    def test_every_card_fits_the_contract_and_is_named_by_its_id(self):
        for path in CARDS:
            with self.subTest(card=path.name):
                card = load(path)
                self.assertEqual(contract.errors("material_card", card), [])
                self.assertEqual(path.stem, card["id"])

    def test_a_value_is_called_checked_only_with_the_date_it_was_read(self):
        for path in CARDS:
            for name, prop in load(path)["properties"].items():
                with self.subTest(card=path.name, prop=name):
                    if prop["checked"]:
                        self.assertRegex(prop["checked_on"], r"^\d{4}-\d{2}-\d{2}$")
                        self.assertNotIn("not read", prop["source"])
                    else:
                        self.assertNotIn("checked_on", prop)

    def test_an_assumption_says_why(self):
        for path in CARDS:
            for name, prop in load(path)["properties"].items():
                if prop["kind"] == "assumption":
                    with self.subTest(card=path.name, prop=name):
                        self.assertGreater(len(prop["source"]), 20)

    def test_the_cards_hold_no_private_names_or_paths(self):
        for path in CARDS:
            text = path.read_text(encoding="utf-8")
            with self.subTest(card=path.name):
                self.assertIsNone(re.search("FU" + "MI|[A-Z]:[/\\\\]Cl" + "aude", text))

    def test_a_card_has_the_values_of_the_same_material_in_materials_json(self):
        materials = load(ROOT / "materials.json")
        same = [p for p in CARDS if p.stem in materials]
        self.assertGreaterEqual(len(same), 4)  # stkr400, stkm13a, ss400, a6061_t6
        for path in same:
            card, mat = load(path), materials[path.stem]
            for name, prop in card["properties"].items():
                if name in mat:
                    with self.subTest(card=path.name, prop=name):
                        self.assertEqual(prop["value"], mat[name])
            for name in SAME:  # a value materials.json has, the card has too
                if name in mat:
                    with self.subTest(card=path.name, missing=name):
                        self.assertIn(name, card["properties"])


if __name__ == "__main__":
    unittest.main()

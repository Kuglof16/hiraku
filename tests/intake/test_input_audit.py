"""What a product's file gives the builders: numbers where a builder reads a number, and every question the entry path asks
answered by the product's own answers file (so a real run is not stopped at v1 by a sentence or an empty item)."""

import copy
import re
import unittest
from pathlib import Path

from hiraku.intake import hearing, request
from hiraku.intake.products import answers_file, load_product
from hiraku.solvers.k0_handcalc import units

NUM = re.compile(r'number\((?:body_mass_in_kg\()?req\)?,\s*"(Q[0-9]+)",\s*"([^"]+)"')
REQ = re.compile(r'requirement\(req,\s*"(Q[0-9]+)"(?:,\s*"([^"]+)")?')
LOOP = re.compile(r'_loop\(spec,\s*req,\s*"(Q[0-9]+)"\)')  # the forest loop reads its distance key in km


def builder_numbers() -> dict[str, set[str]]:
    """The keys the catalog's builders read as numbers, with the units they ask for (read off the builders' source)."""
    out: dict[str, set[str]] = {}
    for f in sorted(Path("hiraku/catalog").rglob("*.py")):
        s = f.read_text(encoding="utf-8")
        for k, u in [*NUM.findall(s), *REQ.findall(s)]:
            out.setdefault(k, set()).add(u or "")
    return out


def entry_file(slug: str, label: str):
    base = load_product(slug)
    b = request.begin(base["want"], product=label, flags=base["flags"], speed_class=base.get("speed_class"),
                      requirements=copy.deepcopy(base["requirements"]))
    res = request._intake(b["draft"])
    return hearing.prepare(res.requirements_file)


class ProductFilesTest(unittest.TestCase):
    PRODUCTS = (("offroad-4w", "free-utv"), ("serving-robot", "serving-robot-claude"))

    def test_the_builder_scan_finds_the_numbers_it_should(self):
        nums = builder_numbers()
        for key in ("Q11", "Q50", "Q52", "Q53", "Q60", "Q90"):
            self.assertIn(key, nums)

    def test_no_entry_a_builder_reads_as_a_number_is_a_sentence_or_in_a_unit_it_cannot_convert(self):
        nums = builder_numbers()
        for slug, label in self.PRODUCTS:
            by = {r["key"]: r for r in entry_file(slug, label).requirements_file["requirements"]}
            for key, us in nums.items():
                r = by.get(key)
                if r is None:
                    continue
                with self.subTest(product=slug, key=key):
                    v = r.get("value")
                    self.assertTrue(isinstance(v, (int, float)) and not isinstance(v, bool), f"{key} is {v!r}")
                    ok = False
                    for u in us:
                        if not u:
                            ok = True
                            continue
                        try:
                            units.convert(float(v), r.get("unit", "1"), u, key)
                            ok = True
                        except Exception:  # noqa: BLE001
                            pass
                    self.assertTrue(ok, f"{key} {r.get('unit')!r} fits none of {sorted(us)}")

    def test_every_question_the_entry_path_asks_is_answered_by_the_products_answers_file(self):
        for slug, label in self.PRODUCTS:
            path = answers_file(slug)
            self.assertIsNotNone(path, slug)
            have = set(hearing.load_answers(path)[0])
            asked = [q["req_key"] for q in entry_file(slug, label).asked["questions"]]
            with self.subTest(product=slug):
                self.assertEqual([k for k in asked if k not in have], [])


if __name__ == "__main__":
    unittest.main()

"""A frame written by design_to_step comes back as the same members, sections and part boxes."""

import json
import unittest

import numpy as np

from hiraku import contract
from hiraku.cad.import_design import design_from_step, recognise, step_to_frame, step_to_parts
from hiraku.cad.step_read import read_step
from hiraku.cad.step_write import design_to_step
from tests.cad import handmade as hm
from tests.cad.test_step_write import DESIGN, _rot

TOL_MM = 1e-3


def corners(box):
    s = np.array(box["size_mm"], float)
    R = _rot(box.get("rpy_deg") or [0, 0, 0])
    c = np.array(box["center_mm"], float)
    return sorted(tuple(np.round(c + R @ (np.array([i, j, k]) * s / 2), 2)) for i in (-1, 1) for j in (-1, 1) for k in (-1, 1))


class ImportFrameTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = json.loads(DESIGN.read_text(encoding="utf-8"))
        cls.solids = read_step(design_to_step(cls.design))
        cls.frame = step_to_frame(cls.solids, join_mm=0.5)

    def test_members_and_sections_come_back(self):
        d = self.design
        nodes = {n["id"]: np.array(n["xyz_mm"]) for n in d["nodes"]}
        got_nodes = {n["id"]: np.array(n["xyz_mm"]) for n in self.frame["nodes"]}
        got = {m["id"]: m for m in self.frame["members"]}
        self.assertEqual(sorted(got), sorted(m["id"] for m in d["members"]))
        used = {n for m in d["members"] for n in (m["node_a"], m["node_b"])}
        self.assertEqual(len(self.frame["nodes"]), len(used))
        worst = 0.0
        for m in d["members"]:
            g = got[m["id"]]
            want = sorted([tuple(nodes[m["node_a"]]), tuple(nodes[m["node_b"]])])
            have = sorted([tuple(got_nodes[g["node_a"]]), tuple(got_nodes[g["node_b"]])])
            worst = max(worst, float(np.abs(np.array(want) - np.array(have)).max()))
            sec, gsec = d["sections"][m["section"]], self.frame["sections"][g["section"]]
            self.assertEqual(sec["shape"], gsec["shape"], m["id"])
            for k, v in sec["dims_mm"].items():
                self.assertAlmostEqual(gsec["dims_mm"][k], v, delta=TOL_MM, msg=f"{m['id']} {k}")
        self.assertLess(worst, TOL_MM)
        self.assertEqual(len(self.frame["sections"]), len(d["sections"]))

    def test_parts_come_back_as_their_boxes(self):
        d = self.design
        rho = {"*": 7850.0}
        parts = {p["id"]: p for p in step_to_parts(self.frame["rest"], rho)}
        self.assertEqual(sorted(parts), sorted(p["id"] for p in d["parts"]))
        for p in d["parts"]:
            g = parts[p["id"]]
            for a, b in zip(corners(p["box"]), corners(g["box"])):
                self.assertTrue(np.allclose(a, b, atol=0.02), f"{p['id']}: {a} vs {b}")
            vol = float(np.prod(p["box"]["size_mm"]))
            self.assertAlmostEqual(g["mass_kg"], vol * 1e-9 * 7850.0, delta=1e-6 * vol * 1e-9 * 7850)
            self.assertEqual(g["basis"]["volume_method"], "exact")

    def test_every_solid_is_reported(self):
        self.assertEqual(len(self.frame["report"]), len(self.solids))
        became = {r["solid"]: r["became"] for r in self.frame["report"]}
        self.assertEqual(sum(1 for v in became.values() if v == "member"), len(self.design["members"]))

    def test_design_passes_the_contract_when_the_missing_values_are_given(self):
        d = self.design
        mat = d["members"][0]["material"]
        out = design_from_step(self.solids, name="back", density_by_name={"*": 7850.0},
                               materials={mat: d["materials"][mat]}, member_material=mat,
                               supports=d["supports"], join_mm=0.5)
        self.assertEqual(out["errors"], [])
        self.assertEqual(len(out["needed"]), 1)  # loads only
        self.assertIn("load", out["needed"][0])

    def test_without_them_it_says_what_is_missing(self):
        out = design_from_step(self.solids, join_mm=0.5)
        text = " ".join(out["needed"])
        for word in ("supports", "density", "material", "load"):
            self.assertIn(word, text)
        self.assertTrue(out["errors"])  # supports minItems, mass_kg None: not guessed
        for p in out["design"]["parts"]:
            self.assertIsNone(p["mass_kg"])


class RecogniseTest(unittest.TestCase):
    def test_cube_is_a_solid_bar_and_cylinder_a_round_bar(self):
        (c,) = read_step(hm.cube())
        r = recognise(c)
        self.assertEqual(r["shape"], "rect_solid")
        (cyl,) = read_step(hm.CYLINDER)
        r = recognise(cyl)
        self.assertEqual(r["shape"], "round_solid")
        self.assertAlmostEqual(r["dims_mm"]["d"], 20.0, places=3)
        self.assertAlmostEqual(r["length_mm"], 20.0, places=6)

    def test_contract_kind_exists(self):
        self.assertIn("design", contract.kinds())


if __name__ == "__main__":
    unittest.main()

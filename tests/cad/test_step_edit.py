"""edit_step: after each edit, read back; the edited solid against a calculation here, the rest unchanged."""

import math
import unittest

import numpy as np

from hiraku.cad import CadError
from hiraku.cad.step_edit import edit_step, edit_step_report
from hiraku.cad.step_read import read_step
from hiraku.cad.step_write import design_to_step
from tests.cad import handmade as hm
from tests.cad.test_step_write import expected

TOL = 0.005

DESIGN = {
    "name": "edit",
    "nodes": [{"id": "a", "xyz_mm": [0, 0, 300]}, {"id": "b", "xyz_mm": [1000, 0, 300]},
              {"id": "c", "xyz_mm": [1000, 600, 300]}, {"id": "d", "xyz_mm": [0, 600, 300]}],
    "members": [
        {"id": "rail_L", "node_a": "a", "node_b": "b", "section": "rt", "material": "s"},
        {"id": "cross_F", "node_a": "b", "node_b": "c", "section": "rt", "material": "s"},
        {"id": "hoop", "node_a": "c", "node_b": "d", "section": "ct", "material": "s"},
    ],
    "sections": {"rt": {"shape": "rect_tube", "dims_mm": {"b": 40, "h": 80, "t": 2.3}},
                 "ct": {"shape": "round_tube", "dims_mm": {"d": 42.7, "t": 2.3}}},
    "parts": [{"id": "battery", "name": "battery", "kind": "battery", "mass_kg": 30,
               "box": {"center_mm": [500, 300, 450], "size_mm": [400, 200, 150]}},
              {"id": "seat", "name": "seat", "kind": "seat", "mass_kg": 8,
               "box": {"center_mm": [300, 300, 600], "size_mm": [450, 450, 100], "rpy_deg": [0, 10, 0]}}],
}


def snapshot(text):
    return {s.name: s for s in read_step(text)}


class EditTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = design_to_step(DESIGN)
        cls.before = snapshot(cls.text)
        cls.exp = expected(DESIGN)

    def check_untouched(self, after, touched):
        for name, s in self.before.items():
            if name in touched:
                continue
            t = after[name]
            self.assertAlmostEqual(t.volume_mm3, s.volume_mm3, delta=1e-9 * s.volume_mm3, msg=name)
            for k in range(3):
                self.assertAlmostEqual(t.bbox_min[k], s.bbox_min[k], delta=1e-9, msg=name)
                self.assertAlmostEqual(t.bbox_max[k], s.bbox_max[k], delta=1e-9, msg=name)
                self.assertAlmostEqual(t.center_mm[k], s.center_mm[k], delta=1e-7, msg=name)
            self.assertEqual(t.method, s.method)

    def test_move(self):
        after = snapshot(edit_step(self.text, [{"op": "move", "name": "battery", "by_mm": [10, -20, 30]}]))
        b0, b1 = self.before["battery"], after["battery"]
        self.assertAlmostEqual(b1.volume_mm3, 400 * 200 * 150, places=4)
        self.assertTrue(np.allclose(np.subtract(b1.bbox_min, b0.bbox_min), [10, -20, 30], atol=1e-9))
        self.check_untouched(after, {"battery"})

    def test_rotate(self):
        after = snapshot(edit_step(self.text, [{"op": "rotate", "name": "battery", "axis": [0, 0, 1], "angle_deg": 90}]))
        b = after["battery"]
        self.assertTrue(np.allclose(b.size_mm, [200, 400, 150], atol=1e-6))
        self.assertTrue(np.allclose(b.center_mm, [500, 300, 450], atol=1e-6))
        self.check_untouched(after, {"battery"})

    def test_delete_and_add_box(self):
        after = snapshot(edit_step(self.text, [{"op": "delete", "name": "seat"},
                                               {"op": "add_box", "name": "tank", "center_mm": [-200, 300, 400],
                                                "size_mm": [300, 250, 200], "rpy_deg": [0, 0, 30]}]))
        self.assertNotIn("seat", after)
        self.assertEqual(len(after), len(self.before))
        t = after["tank"]
        self.assertAlmostEqual(t.volume_mm3, 300 * 250 * 200, delta=1e-6 * 300 * 250 * 200)
        c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
        self.assertAlmostEqual(t.size_mm[0], 300 * c + 250 * s, places=6)
        self.assertAlmostEqual(t.size_mm[1], 300 * s + 250 * c, places=6)
        self.check_untouched(after, {"seat", "tank"})

    def test_set_section_and_wall(self):
        after = snapshot(edit_step(self.text, [
            {"op": "set_section", "name": "rail_L", "section": {"shape": "rect_tube", "dims_mm": {"b": 50, "h": 100, "t": 3.2}}},
            {"op": "set_wall", "name": "hoop", "t_mm": 3.2}]))
        r = after["rail_L"]
        want = (50 * 100 - (50 - 6.4) * (100 - 6.4)) * 1000
        self.assertLess(abs(r.volume_mm3 - want) / want, 1e-9)
        self.assertTrue(np.allclose(r.size_mm, [1000, 50, 100], atol=1e-6))
        h = after["hoop"]
        want = math.pi / 4 * (42.7 ** 2 - (42.7 - 6.4) ** 2) * 1000
        self.assertLess(abs(h.volume_mm3 - want) / want, TOL)
        self.assertTrue(np.allclose(h.size_mm, [1000, 42.7, 42.7], rtol=TOL))
        self.check_untouched(after, {"rail_L", "hoop"})

    def test_add_brace(self):
        sec = {"shape": "round_tube", "dims_mm": {"d": 27.2, "t": 2.0}}
        after = snapshot(edit_step(self.text, [{"op": "add_brace", "name": "diag", "from_mm": [0, 0, 300],
                                                "to_mm": [1000, 600, 300], "section": sec}]))
        mini = {"nodes": [{"id": "p", "xyz_mm": [0, 0, 300]}, {"id": "q", "xyz_mm": [1000, 600, 300]}],
                "members": [{"id": "diag", "node_a": "p", "node_b": "q", "section": "s", "material": "x"}],
                "sections": {"s": sec}, "parts": []}
        vol, lo, hi = expected(mini)["diag"]
        d = after["diag"]
        self.assertLess(abs(d.volume_mm3 - vol) / vol, TOL)
        self.assertTrue(np.allclose(d.size_mm, hi - lo, rtol=TOL))
        self.check_untouched(after, {"diag"})

    def test_edits_chain_and_report(self):
        once = edit_step(self.text, [{"op": "move", "name": "seat", "by_mm": [0, 0, 50]}])
        twice, log = edit_step_report(once, [{"op": "set_wall", "name": "cross_F", "t_mm": 4.0}])
        after = snapshot(twice)
        self.assertAlmostEqual(after["seat"].center_mm[2], self.before["seat"].center_mm[2] + 50, places=6)
        self.assertTrue(any(x.get("written") == "cross_F" for x in log))
        self.check_untouched(after, {"seat", "cross_F"})

    def test_bad_edits_are_named(self):
        for edits, word in (([{"op": "move", "name": "nope", "by_mm": [1, 2, 3]}], "nope"),
                            ([{"op": "explode", "name": "seat"}], "explode"),
                            ([{"op": "set_wall", "name": "battery", "t_mm": 2}], "battery"),
                            ([{"op": "add_box", "name": "seat", "center_mm": [0, 0, 0], "size_mm": [1, 1, 1]}], "already")):
            with self.assertRaises(CadError) as c:
                edit_step(self.text, edits)
            self.assertIn(word, str(c.exception))


class EditForeignFileTest(unittest.TestCase):
    def test_inch_file_comes_out_in_mm(self):
        out = read_step(edit_step(hm.cube(hm.INCH_CONTEXT, 1.0), []))
        self.assertAlmostEqual(out[0].volume_mm3, 25.4 ** 3, delta=1e-6)
        self.assertIn("SI_UNIT(.MILLI.,.METRE.)", edit_step(hm.cube(hm.INCH_CONTEXT, 1.0), []))

    def test_cylinder_and_assembly_are_copied_in_place(self):
        (c0,) = read_step(hm.CYLINDER)
        (c1,) = read_step(edit_step(hm.CYLINDER, [{"op": "move", "name": "cyl", "by_mm": [5, 0, 0]}]))
        self.assertAlmostEqual(c1.volume_mm3, c0.volume_mm3, delta=1e-9 * c0.volume_mm3)
        self.assertAlmostEqual(c1.center_mm[0] - c0.center_mm[0], 5.0, places=6)
        with self.assertRaises(CadError) as e:
            edit_step(hm.assembly(), [{"op": "delete", "name": "block"}])
        self.assertIn("unique", str(e.exception))
        flat = read_step(edit_step(hm.assembly(), []))
        self.assertEqual(sorted(round(s.center_mm[0], 6) for s in flat), [-10.0, 105.0])


if __name__ == "__main__":
    unittest.main()

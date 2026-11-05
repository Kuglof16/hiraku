"""tests.solvers.same_design: a design whose numbers differ in their last bits (as between operating systems) is the
same; one that differs in a number, a key, a key's order, a string or a list's length is not."""

import json
import math
import unittest
from pathlib import Path

from tests.solvers.same_design import differences

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))


def nudged(obj, ulps=4):
    """Every float moved by `ulps` units in its last place (what another maths library may give)."""
    if isinstance(obj, float):
        x = obj
        for _ in range(ulps):
            x = math.nextafter(x, math.inf)
        return x
    if isinstance(obj, dict):
        return {k: nudged(v, ulps) for k, v in obj.items()}
    if isinstance(obj, list):
        return [nudged(v, ulps) for v in obj]
    return obj


class SameDesignTest(unittest.TestCase):
    def test_last_bits_are_the_same_design(self):
        self.assertEqual(differences(nudged(V1), V1), [])

    def test_a_real_difference_is_found(self):
        d = json.loads(json.dumps(V1))
        d["nodes"][0]["xyz_mm"][0] += 1e-4
        self.assertEqual(len(differences(d, V1)), 1)
        self.assertIn("nodes[0].xyz_mm[0]", differences(d, V1)[0])

    def test_keys_order_strings_and_lengths(self):
        d = json.loads(json.dumps(V1))
        d["variables"] = dict(reversed(list(d["variables"].items())))
        self.assertTrue(differences(d, V1))
        d = json.loads(json.dumps(V1))
        d["parts"][0]["name"] += "x"
        self.assertTrue(differences(d, V1))
        d = json.loads(json.dumps(V1))
        d["parts"].pop()
        self.assertTrue(differences(d, V1))
        self.assertTrue(differences({"a": True}, {"a": 1}))  # a boolean is not a number here


if __name__ == "__main__":
    unittest.main()

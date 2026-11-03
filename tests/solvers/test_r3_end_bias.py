"""R3's plan says the known bias of the end cross member's hand calculation (26-31 % below the tool on the
revised version and with rail_t 4.5 mm) in that prediction's assumptions, once, and nowhere else."""

import unittest

from hiraku.catalog.builders import analysis as ab


class EndBiasTest(unittest.TestCase):
    def test_in_the_end_cross_member_damage_only(self):
        preds = {p["quantity"]: p for p in ab.R3_PLAN["predictions"]}
        end = preds["fatigue_damage_end_cross_member"]["assumptions"]
        self.assertEqual(end.count(ab._END_BIAS), 1)
        self.assertIn("26〜31 %", ab._END_BIAS)
        self.assertEqual(end[:len(ab._DAMAGE_ASSUME)], list(ab._DAMAGE_ASSUME))
        others = [q for q, p in preds.items() if ab._END_BIAS in p["assumptions"]]
        self.assertEqual(others, ["fatigue_damage_end_cross_member"])


if __name__ == "__main__":
    unittest.main()

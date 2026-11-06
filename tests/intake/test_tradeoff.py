import unittest

from hiraku.intake import IntakeError, tradeoff

# K5 terms in the contract's shape: share is the elasticity d ln y / d ln x (test numbers, not results of a run)
SENS = {
    "utilisation": [
        {"name": "utilisation to sections.tower.dims_mm.t", "value": 3.0, "unit": "mm", "share": -1.2},
        {"name": "utilisation to materials.steel.E_MPa", "value": 205000.0, "unit": "MPa", "share": -0.02},
        {"name": "utilisation to parts.cargo.mass_kg (lost in noise: the two one-sided slopes disagree)", "value": 80.0},
    ],
    "mass_kg": [
        {"name": "mass_kg to sections.tower.dims_mm.t", "value": 3.0, "unit": "mm", "share": 0.8},
        {"name": "mass_kg to materials.steel.E_MPa", "value": 205000.0, "unit": "MPa", "share": 0.0},
        {"name": "mass_kg to parts.cargo.mass_kg", "value": 80.0, "unit": "kg", "share": 0.3},
    ],
}
META = {"utilisation": {"label": "フレームの使用率"}, "mass_kg": {"label": "質量", "unit": "kg"}}
BASES = {"utilisation": {"value": 0.8, "record_id": "ver-0001"}, "mass_kg": {"value": 400.0, "record_id": "res-0002"}}


class FindTest(unittest.TestCase):
    def test_a_number_that_helps_one_result_and_hurts_the_other_is_a_trade_off(self):
        got = tradeoff.find(SENS, META, BASES)
        self.assertEqual([p["variable"] for p in got], ["sections.tower.dims_mm.t"])  # E: too small; cargo mass: noisy on one side
        pair = got[0]
        self.assertEqual(pair["tradeoff_id"], "sections.tower.dims_mm.t:utilisation:mass_kg")
        self.assertEqual(pair["a"]["name"], "フレームの使用率")  # the side that gets better
        self.assertEqual(pair["b"]["name"], "質量")
        self.assertEqual((pair["a"]["change_pct"], pair["b"]["change_pct"]), (-12.0, 8.0))  # elasticity x 10 %
        self.assertEqual((pair["a"]["now"], pair["a"]["after"]), (0.8, 0.704))  # computed from the base value
        self.assertEqual((pair["b"]["now"], pair["b"]["after"]), (400.0, 432.0))
        self.assertEqual(pair["a"]["record_id"], "ver-0001")
        self.assertEqual(pair["choices"], ["favor_a", "favor_b", "keep_both"])

    def test_the_text_has_the_numbers_of_the_pair_and_nothing_else(self):
        text = tradeoff.find(SENS, META, BASES)[0]["text"]
        for part in ("sections.tower.dims_mm.t", "いま 3 mm", "10 %", "フレームの使用率は約 12 % 良くなり (0.8 → 0.704)",
                     "質量は約 8 % 悪くなり (400 → 432 kg)", "どちらを優先しますか"):
            self.assertIn(part, text)

    def test_without_base_values_the_text_gives_the_percentages_only(self):
        text = tradeoff.find(SENS, META)[0]["text"]
        self.assertIn("質量は約 8 % 悪くなり", text)
        self.assertNotIn("→", text)

    def test_two_results_that_move_together_are_not_a_trade_off(self):
        sens = {"utilisation": [{"name": "utilisation to x", "value": 1.0, "share": 0.5}],
                "mass_kg": [{"name": "mass_kg to x", "value": 1.0, "share": 0.5}]}
        self.assertEqual(tradeoff.find(sens, META), [])

    def test_a_result_where_higher_is_better_is_read_the_right_way(self):
        sens = {"stiffness": [{"name": "stiffness to x", "value": 2.0, "share": 1.0}],
                "mass_kg": [{"name": "mass_kg to x", "value": 2.0, "share": 0.5}]}
        meta = {"stiffness": {"label": "剛性", "lower_is_better": False}, "mass_kg": {"label": "質量"}}
        got = tradeoff.find(sens, meta)  # x up: stiffness up (good), mass up (bad)
        self.assertEqual((len(got), got[0]["a"]["quantity"], got[0]["b"]["quantity"]), (1, "stiffness", "mass_kg"))
        sens["stiffness"][0]["share"] = -1.0  # x up: stiffness down (bad) and mass up (bad): no trade-off
        self.assertEqual(tradeoff.find(sens, meta), [])

    def test_weak_pairs_are_dropped_and_the_strongest_comes_first(self):
        sens = {"a": [{"name": "a to x", "value": 1.0, "share": -0.5}, {"name": "a to y", "value": 1.0, "share": -2.0}],
                "b": [{"name": "b to x", "value": 1.0, "share": 0.4}, {"name": "b to y", "value": 1.0, "share": 1.5},
                      {"name": "b to z", "value": 1.0, "share": 0.05}]}
        got = tradeoff.find(sens)
        self.assertEqual([p["variable"] for p in got], ["y", "x"])


class AnswersTest(unittest.TestCase):
    ASKED = [{"tradeoff_id": "x:a:b", "choices": ["favor_a", "favor_b", "keep_both"]}]

    def test_an_answer_to_a_question_that_was_asked(self):
        got = tradeoff.check_answers([{"tradeoff_id": "x:a:b", "choice": "favor_a", "note": 3}], self.ASKED)
        self.assertEqual(got, [{"tradeoff_id": "x:a:b", "choice": "favor_a", "note": "3"}])

    def test_refused(self):
        for raw in ({"x": 1}, [{"tradeoff_id": "x:a:b"}], [{"tradeoff_id": "x:a:b", "choice": "favor_c"}],
                    [{"tradeoff_id": "nope", "choice": "favor_a"}], [{"tradeoff_id": "x:a:b", "choice": "favor_a", "extra": 1}]):
            with self.subTest(raw=raw):
                with self.assertRaises(IntakeError):
                    tradeoff.check_answers(raw, self.ASKED)

    def test_nothing_is_nothing(self):
        self.assertEqual(tradeoff.check_answers(None, self.ASKED), [])


if __name__ == "__main__":
    unittest.main()

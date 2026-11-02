import unittest

from hiraku.intake import expression
from hiraku.intake.expression import ExprError, evaluate, names, parse


class GrammarTest(unittest.TestCase):
    def test_what_is_allowed(self):
        for ok in ("a >= b", "outer_mm <= 35 * wall_mm", "w >= max(30, 6 * t)", "a < b < c", "not (a > 1)",
                   "a > 1 and b < 2 or c == 3", "x ** 2 <= y", "-a < 0", "(a - b) / 2 >= c"):
            with self.subTest(ok=ok):
                parse(ok)

    def test_what_is_refused(self):
        for bad, word in [
            ("", "空"),
            ("a +", "読めません"),
            ("a + b", "はい / いいえ"),  # a number, not a question
            ("__import__('os').system('x') == 0", "呼べる"),
            ("a.b > 1", "Attribute"),
            ("open('f') > 1", "呼べる"),
            ("a > 'x'", "数字以外"),
            ("a == True", "数字以外"),
            ("x ** y > 1", "** の右"),
            ("x ** 9 > 1", "** の右"),
            ("[a] > 1", "List"),
            ("a if b else c > 1", "IfExp"),
            ("max(a, key=b) > 1", "呼べる"),
            ("a" * 301, "長すぎ"),
        ]:
            with self.subTest(bad=bad[:30]):
                with self.assertRaises(ExprError) as cm:
                    parse(bad)
                self.assertIn(word, str(cm.exception))

    def test_names_in_the_order_written(self):
        self.assertEqual(names("total_mass_kg >= mass_kg + max(a, mass_kg)"), ["total_mass_kg", "mass_kg", "a"])


class EvaluateTest(unittest.TestCase):
    def test_answers(self):
        self.assertEqual(evaluate("m <= 23", {"m": 50}), (False, []))
        self.assertEqual(evaluate("m <= 23", {"m": 23}), (True, []))
        self.assertEqual(evaluate("a >= 2 * b", {"a": 5, "b": 2.5}), (True, []))
        self.assertEqual(evaluate("w >= max(30, 6 * t)", {"w": 20, "t": 4}), (False, []))
        self.assertEqual(evaluate("1 < a < 3", {"a": 2}), (True, []))
        self.assertEqual(evaluate("1 < a < 3", {"a": 3}), (False, []))
        self.assertEqual(evaluate("not (a > 1)", {"a": 0}), (True, []))

    def test_a_missing_name_gives_no_answer_and_every_gap_is_listed(self):
        self.assertEqual(evaluate("a >= b", {"a": 1}), (None, ["b"]))
        self.assertEqual(evaluate("a >= b + c", {}), (None, ["a", "b", "c"]))
        self.assertEqual(evaluate("a > 1 or b > 1", {"a": 5}), (None, ["b"]))  # no short cut: the gap is still reported

    def test_dividing_by_zero_is_a_gap_not_a_crash(self):
        self.assertEqual(evaluate("a / b > 1", {"a": 1, "b": 0}), (None, ["0 で割る"]))

    def test_the_answer_is_a_plain_bool(self):
        self.assertIs(evaluate("a > 1", {"a": 2})[0], True)


if __name__ == "__main__":
    unittest.main()

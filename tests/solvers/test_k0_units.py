import unittest

from hiraku import contract
from hiraku.solvers.k0_handcalc import units


class UnitsTest(unittest.TestCase):
    def test_every_contract_unit_has_a_row(self):
        self.assertEqual(set(contract.UNITS), set(units.UNIT_TABLE))

    def test_conversions(self):
        cases = [
            (1.0, "m", "mm", 1000.0),
            (36.0, "km/h", "m/s", 10.0),
            (1.0, "g", "m/s^2", 9.80665),
            (25.0, "degC", "K", 298.15),
            (300.0, "K", "degC", 26.85),
            (60.0, "rpm", "Hz", 1.0),
            (50.0, "%", "1", 0.5),
            (1.0, "kWh", "J", 3.6e6),
            (1.0, "kWh", "Wh", 1000.0),
            (1.0, "N*m", "N*mm", 1000.0),
            (1.0, "MPa", "Pa", 1e6),
            (180.0, "deg", "rad", 3.141592653589793),
            (2.0, "kN", "N", 2000.0),
            (1.0, "mm^4", "mm^4", 1.0),
            (1.0, "mm^3", "mm", None),
            (1.0, "mm^2", "mm^3", None),
            (1.0, "N/mm", "N", None),
        ]
        for v, a, b, want in cases:
            if want is None:
                with self.assertRaises(ValueError):
                    units.convert(v, a, b)
            else:
                self.assertAlmostEqual(units.convert(v, a, b), want, places=9, msg=f"{v} {a} -> {b}")

    def test_wrong_dimension_names_the_input(self):
        with self.assertRaisesRegex(ValueError, "input 'r'.*'kg'.*'mm'"):
            units.convert(1.0, "kg", "mm", "r")

    def test_unknown_unit(self):
        with self.assertRaisesRegex(ValueError, "unknown unit"):
            units.convert(1.0, "inch", "mm")

    def test_angle_is_not_a_plain_number(self):
        with self.assertRaises(ValueError):
            units.convert(1.0, "1", "rad")

    def test_to_unit_rejects_non_quantity(self):
        with self.assertRaisesRegex(ValueError, "input 'h'"):
            units.to_unit(100.0, "mm", "h")


if __name__ == "__main__":
    unittest.main()

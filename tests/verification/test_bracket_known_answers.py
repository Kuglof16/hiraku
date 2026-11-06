"""R9 (suspension mounts): the product's clevis bracket hand calculation against qa.known_answers' own.

hiraku/solvers/k0_handcalc/library_bracket.py (analysis dept) and qa.known_answers [clevis] are written apart;
both take a force vector, the pin axis and the plates' base line. The allowables are the product's resistances()
(computed from the materials there); they are also checked here against the values agreed between the two
departments (EN 1993-1-8:2005 with its recommended partial factors: not checked in this repository).
"""

import math
import random
import unittest

from qa import known_answers as ka

try:
    from hiraku.solvers.k0_handcalc import library_bracket as lb
except ImportError:  # before analysis 3b06ab93
    lb = None

NAMES = ("bolt_shear_utilisation", "bolt_bearing_utilisation", "plate_bending_utilisation", "weld_utilisation")


def _normal_pair(rng):
    """A unit pin axis u and a base line normal to it."""
    u = [rng.uniform(-1, 1) for _ in range(3)]
    n = math.sqrt(sum(x * x for x in u))
    u = [x / n for x in u]
    w = [rng.uniform(-1, 1) for _ in range(3)]
    d = sum(a * b for a, b in zip(w, u))
    base = [a - d * b for a, b in zip(w, u)]
    return u, base


@unittest.skipIf(lb is None, "needs hiraku.solvers.k0_handcalc.library_bracket (analysis 3b06ab93)")
class BracketKnownAnswersTest(unittest.TestCase):
    def _bolt_is_the_larger(self, out):
        """bolt_utilisation (analysis, R9 stage 3), when given, is the larger of the bolt's shear and bearing;
        it is taken out of `out` so the four utilisations are compared as before."""
        if "bolt_utilisation" in out:
            u = out.pop("bolt_utilisation")
            self.assertEqual(u, max(out["bolt_shear_utilisation"], out["bolt_bearing_utilisation"]))

    def test_resistances_are_the_agreed_values(self):
        # f_v = 0.6 f_ub / 1.25; f_b = k1 alpha_b f_u / 1.25 with alpha_b = 1.2 / 3, k1 = 2.8 * 1.2 - 1.7;
        # f_plate = f_y / 1.0; f_vw = f_u / (sqrt 3 * 0.8 * 1.25)
        r = lb.resistances(245.0, 400.0, 800.0)
        self.assertAlmostEqual(r["f_v"], 0.6 * 800.0 / 1.25)
        self.assertAlmostEqual(r["f_b"], (2.8 * 1.2 - 1.7) * (1.2 / 3.0) * 400.0 / 1.25)
        self.assertAlmostEqual(r["f_plate"], 245.0)
        self.assertAlmostEqual(r["f_vw"], 400.0 / (math.sqrt(3.0) * 0.8 * 1.25))

    def test_the_force_split_agrees(self):
        rng = random.Random(9)
        for _ in range(50):
            F = [rng.uniform(-20000, 20000) for _ in range(3)]
            u, base = _normal_pair(rng)
            mine, theirs = ka.mount_force_split(F, u, base), lb.split_force(F, u, base)
            for k in ("F_n", "F_t", "F_v"):
                self.assertAlmostEqual(mine[k], abs(theirs[k]), delta=1e-9 * (1 + mine["F"]), msg=k)
            self.assertAlmostEqual(mine["F_p"], theirs["F_p"], delta=1e-9 * (1 + mine["F"]))

    def test_the_four_utilisations_agree(self):
        rng = random.Random(17)
        for case in range(200):
            F = [rng.uniform(-30000, 30000) for _ in range(3)]
            u, base = _normal_pair(rng)
            d, n = rng.choice([10.0, 12.0, 16.0, 20.0]), rng.choice([1, 2])
            t, s = rng.uniform(4, 12), rng.uniform(3, 8)
            b, e = rng.uniform(30, 120), rng.uniform(20, 80)
            f_y, f_u = rng.choice([(245.0, 400.0), (235.0, 360.0)])
            r = lb.resistances(f_y, f_u)
            mine = ka.clevis_utilisations(F, u, base, d, n, t, s, b, e, r["f_v"], r["f_b"], r["f_plate"], r["f_vw"])
            theirs = lb.clevis_utilisations(F, u, base, d, n, t, s, b, e, f_y=f_y, f_u=f_u)
            self._bolt_is_the_larger(theirs)
            self.assertEqual(sorted(theirs), sorted(NAMES))
            for k in NAMES:
                self.assertAlmostEqual(theirs[k], mine[k], delta=1e-9 * max(1.0, mine[k]), msg=f"case {case} {k}")

    def test_a_member_end_has_shear_and_bearing_only(self):
        u, base = (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)
        out = lb.clevis_utilisations((3000.0, 0.0, 4000.0), u, base, 12.0, 1, 6.0, None, None, None)
        self._bolt_is_the_larger(out)
        self.assertEqual(sorted(out), ["bolt_bearing_utilisation", "bolt_shear_utilisation"])
        r = lb.resistances(245.0, 400.0)
        # |F_p| = 5000 N in the plates' plane: tau = 5000 / (2 pi 144 / 4), sigma_b = 5000 / (2 * 12 * 6)
        self.assertAlmostEqual(out["bolt_shear_utilisation"], 5000.0 / (2 * math.pi * 144.0 / 4) / r["f_v"])
        self.assertAlmostEqual(out["bolt_bearing_utilisation"], 5000.0 / (2 * 12.0 * 6.0) / r["f_b"])


if __name__ == "__main__":
    unittest.main()

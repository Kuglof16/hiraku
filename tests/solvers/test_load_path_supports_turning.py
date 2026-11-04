"""The R7 hand calculation's contacts: each part of the chain a rigid body on all its contacts in parallel."""

import unittest

import numpy as np

from hiraku.catalog.builders.load_path import supports_turning
from hiraku.solvers.shelf.load_path import Contact


def c(a, b, x, k_t, k_r):
    return Contact(a=a, b=b, point=np.asarray(x, float), area=1.0, k_t=k_t, k_r=k_r)


class SupportsTurningTest(unittest.TestCase):
    def test_one_contact_turns_by_its_rotation_spring(self):
        """A part held at one point: the load F at lever r across turns it by F r / k_r, the point moves F r^2 / k_r."""
        F, r, k_t, k_r = 300.0, 400.0, 1.0e5, 2.0e7
        cs = [c("part:p", "member:m", [0.0, 0.0, 0.0], k_t, k_r)]
        got = supports_turning(cs, cs, "p", [0.0, 0.0, r], F, [[1.0, 0.0, 0.0]])
        self.assertAlmostEqual(got, F * r * r / k_r, delta=1e-9 * F * r * r / k_r)

    def test_two_contacts_hold_it_by_a_couple(self):
        """Held at two points s apart along z (no rotation springs): the moment F r is taken by the pair, the turn is
        F r / (k_t s^2 / 2) about the middle, the point r above the middle moves r times that."""
        F, r, s, k_t = 300.0, 400.0, 100.0, 1.0e5
        cs = [c("part:p", "member:m", [0.0, 0.0, -s / 2], k_t, 0.0), c("part:p", "member:n", [0.0, 0.0, s / 2], k_t, 0.0)]
        got = supports_turning(cs, cs[:1], "p", [0.0, 0.0, r], F, [[1.0, 0.0, 0.0]])
        expect = r * F * r / (k_t * s * s / 2.0)
        self.assertAlmostEqual(got, expect, delta=1e-9 * expect)

    def test_parts_in_series(self):
        """Two parts in a chain each turning on one contact: the movements add."""
        F, k_r = 300.0, 2.0e7
        load = [0.0, 0.0, 500.0]
        cs = [c("part:a", "part:b", [0.0, 0.0, 300.0], 1e9, k_r), c("part:b", "member:m", [0.0, 0.0, 0.0], 1e9, k_r)]
        got = supports_turning(cs, cs, "a", load, F, [[1.0, 0.0, 0.0]])
        expect = F * 200.0 ** 2 / k_r + F * 500.0 ** 2 / k_r
        self.assertAlmostEqual(got, expect, delta=1e-6 * expect)


if __name__ == "__main__":
    unittest.main()

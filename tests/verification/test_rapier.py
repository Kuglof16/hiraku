"""Rapier itself against closed forms, before it is used as a second opinion for K2.

These pin down how the installed Rapier integrates (sub-steps, force at a
point, resting contact) so a later K2-versus-Rapier difference can be traced
to one side. Skipped when Node or the package is missing.
"""

import math
import unittest

from qa import known_answers as ka
from qa import rapier

G = 9.81


class RapierBasics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reason = rapier.unavailable()
        if reason:
            raise unittest.SkipTest(f"Rapier check needs Node and the package: {reason}")

    def test_free_fall_is_semi_implicit_euler_with_sub_steps(self):
        dt, steps = 1 / 60, 60
        out = rapier.run({"dt": dt, "steps": steps, "gravity": [0, 0, -G], "solver_iterations": 4,
                          "bodies": [{"id": "box", "mass": 1.0, "inertia": [0.1, 0.1, 0.1],
                                      "position": [0, 0, 1.0], "box": None}]})
        k = out["solver_iterations"]
        z = out["bodies"]["box"]["position"][-1][2]
        expected = ka.free_fall_semi_implicit_euler(1.0, G, dt / k, steps * k)
        plain = ka.free_fall_semi_implicit_euler(1.0, G, dt, steps)
        self.assertAlmostEqual(z, expected, delta=1e-6,
                               msg=f"z={z}; with {k} sub-steps {expected}, without {plain} (Rapier {out['rapier_version']})")

    def test_force_at_a_point_gives_the_rigid_body_rates(self):
        m, inertia, F, dt, steps = 2.0, [0.5, 1.0, 1.5], 10.0, 1 / 100, 50
        out = rapier.run({"dt": dt, "steps": steps, "gravity": [0, 0, 0],
                          "bodies": [{"id": "b", "mass": m, "inertia": inertia, "position": [0, 0, 0], "box": None}],
                          "forces": [{"body": "b", "force": [0, F, 0], "point": [1.0, 0, 0]}]})
        t = steps * dt
        v = out["bodies"]["b"]["linvel"][-1]
        w = out["bodies"]["b"]["angvel"][-1]
        # torque r x F = (1, 0, 0) x (0, F, 0) = (0, 0, F) about the principal z axis
        self.assertAlmostEqual(v[1], F / m * t, delta=1e-9 + 1e-6 * F / m * t)
        self.assertAlmostEqual(w[2], F / inertia[2] * t, delta=1e-9 + 1e-6 * F / inertia[2] * t)
        self.assertAlmostEqual(math.hypot(v[0], v[2]), 0.0, delta=1e-9)

    def test_box_resting_on_the_ground_does_not_drift(self):
        half = [0.5, 0.3, 0.2]
        out = rapier.run({"dt": 1 / 60, "steps": 300, "gravity": [0, 0, -G], "record_every": 60,
                          "boxes": [{"center": [0, 0, -0.5], "half_extents": [20, 20, 0.5]}],
                          "bodies": [{"id": "box", "mass": 50.0, "inertia": [2.0, 4.0, 5.0],
                                      "position": [0, 0, half[2]], "box": {"half_extents": half}}]})
        x, y, z = out["bodies"]["box"]["position"][-1]
        q = out["bodies"]["box"]["rotation"][-1]
        self.assertLess(math.hypot(x, y), 1e-6)
        self.assertAlmostEqual(z, half[2], delta=5e-3)
        self.assertAlmostEqual(abs(q[3]), 1.0, delta=1e-6)

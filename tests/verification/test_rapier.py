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
        # Rapier computes in single precision (about 7 digits); the two schemes differ by 0.06 m here
        self.assertAlmostEqual(z, expected, delta=1e-5,
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
        self.assertAlmostEqual(v[1], F / m * t, delta=1e-5 * F / m * t)  # single precision
        # the body turns about 0.8 rad and its world inertia is rebuilt each step in single precision
        self.assertAlmostEqual(w[2], F / inertia[2] * t, delta=1e-4 * F / inertia[2] * t)
        self.assertAlmostEqual(math.hypot(v[0], v[2]), 0.0, delta=1e-6)

    def test_box_resting_on_the_ground_does_not_drift(self):
        half = [0.5, 0.3, 0.2]
        out = rapier.run({"dt": 1 / 60, "steps": 300, "gravity": [0, 0, -G], "record_every": 60,
                          "boxes": [{"center": [0, 0, -0.5], "half_extents": [20, 20, 0.5]}],
                          "bodies": [{"id": "box", "mass": 50.0, "inertia": [2.0, 4.0, 5.0],
                                      "position": [0, 0, half[2]], "box": {"half_extents": half}}]})
        x, y, z = out["bodies"]["box"]["position"][-1]
        q = out["bodies"]["box"]["rotation"][-1]
        self.assertLess(math.hypot(x, y), 1e-4)  # 0.1 mm in 5 s: single-precision contact noise
        self.assertAlmostEqual(z, half[2], delta=5e-3)
        self.assertAlmostEqual(abs(q[3]), 1.0, delta=1e-5)


def tips(theta_deg, mass, track, h, wheelbase, mu=2.0, seconds=3.0):
    """Does a rigid box (bottom = the wheel contact rectangle, centre of gravity at its middle,
    h above the ground) tip over on a table tilted by theta? The table is level and gravity is
    turned instead (left side up: gravity leans toward -y)."""
    th, dt = math.radians(theta_deg), 1 / 240
    steps = int(seconds / dt)
    a, b, c = wheelbase, track, 2 * h
    out = rapier.run({"dt": dt, "steps": steps, "record_every": steps,
                      "gravity": [0, -G * math.sin(th), -G * math.cos(th)],
                      "boxes": [{"center": [0, 0, -0.5], "half_extents": [20, 20, 0.5], "friction": mu}],
                      "bodies": [{"id": "v", "mass": mass, "position": [0, 0, h],
                                  "box": {"half_extents": [a / 2, b / 2, h], "friction": mu},
                                  "inertia": [mass * (b * b + c * c) / 12, mass * (a * a + c * c) / 12,
                                              mass * (a * a + b * b) / 12]}]})
    x, y, z, w = out["bodies"]["v"]["rotation"][-1]
    up = 1 - 2 * (x * x + y * y)  # z component of the body's z axis
    return math.degrees(math.acos(max(-1.0, min(1.0, up)))) > 2.0


def tip_angle(lo=30.0, hi=70.0, resolution=0.02, **box):
    while hi - lo > resolution:
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if tips(mid, **box) else (mid, hi)
    return (lo + hi) / 2


class RapierTilt(unittest.TestCase):
    """A rigid vehicle on a tilt table: the upper wheels lift when the resultant of the weight
    passes the lower wheels, tan(theta) = (track / 2) / h. K2's own rigid check is the same box
    (500 kg, track 1.2 m, h 0.5 m)."""

    BOX = {"mass": 500.0, "track": 1.2, "h": 0.5, "wheelbase": 1.8}

    @classmethod
    def setUpClass(cls):
        reason = rapier.unavailable()
        if reason:
            raise unittest.SkipTest(f"Rapier check needs Node and the package: {reason}")
        cls.rapier_deg = tip_angle(**cls.BOX)

    def test_rigid_box_tips_at_the_closed_form_angle(self):
        self.assertAlmostEqual(self.rapier_deg, ka.tilt_angle_rigid_deg(1.2, 0.5), delta=0.1)
        higher = dict(self.BOX, h=0.8)  # a higher centre of gravity tips earlier
        self.assertAlmostEqual(tip_angle(**higher), ka.tilt_angle_rigid_deg(1.2, 0.8), delta=0.1)

    def test_k2_rigid_tilt_agrees_with_rapier(self):
        import os
        if os.environ.get("HIRAKU_SLOW_TESTS") != "1":
            self.skipTest("K2's tilt sweep takes about a minute: set HIRAKU_SLOW_TESTS=1")
        try:
            from hiraku.solvers.k2_vehicle.checks import CHECK_TILT
            from hiraku.solvers.k2_vehicle.model import box_design, build_vehicle
            from hiraku.solvers.k2_vehicle.scenario import tilt_sweep
        except ImportError as e:
            self.skipTest(f"K2 is not in this checkout: {e}")
        veh = build_vehicle(box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, susp="rigid",
                                       variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                                  "tire_mu": {"value": 2.0, "unit": "1"}}))
        k2_deg = math.degrees(tilt_sweep(veh, left_up=True, **CHECK_TILT)["tip_angle"])
        self.assertAlmostEqual(k2_deg, self.rapier_deg, delta=0.15)


def quat_xyzw(R) -> list[float]:
    """Unit quaternion [x, y, z, w] of a rotation matrix."""
    import numpy as np
    m = np.asarray(R, float)
    w = math.sqrt(max(0.0, 1 + m[0, 0] + m[1, 1] + m[2, 2])) / 2
    x = math.copysign(math.sqrt(max(0.0, 1 + m[0, 0] - m[1, 1] - m[2, 2])) / 2, m[2, 1] - m[1, 2])
    y = math.copysign(math.sqrt(max(0.0, 1 - m[0, 0] + m[1, 1] - m[2, 2])) / 2, m[0, 2] - m[2, 0])
    z = math.copysign(math.sqrt(max(0.0, 1 - m[0, 0] - m[1, 1] + m[2, 2])) / 2, m[1, 0] - m[0, 1])
    return [x, y, z, w]


class RapierStepReplay(unittest.TestCase):
    """K2's step push (hiraku.solvers.k2_vehicle.shelf.step.step_body_log): the forces and moments
    K2 put on the sprung body at each step, replayed on a Rapier rigid body with K2's mass, inertia,
    gravity and start state, one sub-step per step (Rapier's semi-implicit Euler, as K2's). The
    loads are open-loop (computed from K2's states), so this checks the rigid-body integration and
    where the forces act, not the tyres or springs."""

    RECORD = 10

    @classmethod
    def setUpClass(cls):
        import numpy as np
        reason = rapier.unavailable()
        if reason:
            raise unittest.SkipTest(f"Rapier check needs Node and the package: {reason}")
        try:
            from hiraku.solvers.k2_vehicle.model import box_design
            from hiraku.solvers.k2_vehicle.shelf.step import step_body_log
        except ImportError as e:
            raise unittest.SkipTest(f"needs K2's step_body_log: {e}")
        cls.log = log = step_body_log(box_design(susp="kc_table", wheel_mass=15.0), 100.0, after_s=0.3)
        vals, vecs = np.linalg.eigh(np.array(log["inertia_kg_m2"]))
        if np.linalg.det(vecs) < 0:
            vecs[:, 2] *= -1
        s = log["start"]
        w, x, y, z = s["quat"]
        out = rapier.run({
            "dt": log["dt"], "steps": log["n_steps"], "gravity": log["gravity_m_s2"], "solver_iterations": 1,
            "record_every": cls.RECORD,
            "bodies": [{"id": "body", "mass": log["mass_kg"], "inertia": vals.tolist(), "inertia_frame": quat_xyzw(vecs),
                        "position": s["cg_m"], "rotation": [x, y, z, w], "linvel": s["v_m_s"],
                        "angvel": s["omega_rad_s"], "box": None}],
            "force_series": {"body": [st["forces"] for st in log["steps"]]},
            "torque_series": {"body": [np.sum(st["moments"], axis=0).tolist() for st in log["steps"]]},
        }, timeout=600)
        cls.rapier = out["bodies"]["body"]

    def pairs(self):
        """(Rapier record, K2 step) after the same number of steps."""
        for j in range(1, len(self.rapier["position"])):
            k = j * self.RECORD - 1
            if k < len(self.log["steps"]):
                yield j, self.log["steps"][k]

    def test_centre_of_gravity_and_attitude_follow_k2(self):
        import numpy as np
        dp = da = 0.0
        for j, st in self.pairs():
            dp = max(dp, float(np.linalg.norm(np.array(self.rapier["position"][j]) - st["cg_m"])))
            qx, qy, qz, qw = self.rapier["rotation"][j]
            dot = abs(qw * st["quat"][0] + qx * st["quat"][1] + qy * st["quat"][2] + qz * st["quat"][3])
            da = max(da, math.degrees(2 * math.acos(min(1.0, dot))))
        k2 = self.log["steps"]
        moved = float(np.linalg.norm(np.array(k2[-1]["cg_m"]) - self.log["start"]["cg_m"]))
        self.assertGreater(moved, 0.05)  # the body did climb
        self.assertLess(dp, 1e-3, f"centre of gravity off by up to {dp * 1000:.3f} mm over a {moved:.3f} m move")
        self.assertLess(da, 0.05, f"attitude off by up to {da:.4f} deg")

"""The body log of a step push: its loads, replayed on a rigid body, give back K2's motion."""

import json
import math
import unittest

import numpy as np

from hiraku.solvers.k2_vehicle.model import box_design
from hiraku.solvers.k2_vehicle.shelf.step import step_body_log
from hiraku.solvers.k2_vehicle.sim import quat_mul, quat_to_r


def replay(log: dict):
    """Semi-implicit Euler of a rigid body under the logged forces, moments and gravity (as K2)."""
    h, m = log["dt"], log["mass_kg"]
    inertia, g = np.array(log["inertia_kg_m2"]), np.array(log["gravity_m_s2"])
    s = log["start"]
    p, q, v = np.array(s["cg_m"]), list(s["quat"]), np.array(s["v_m_s"])
    r = quat_to_r(q)
    w = r.T @ np.array(s["omega_rad_s"])
    for st in log["steps"]:
        force = sum(np.array(f) for f, _ in st["forces"])
        moment = sum(np.cross(np.array(x) - p, np.array(f)) for f, x in st["forces"]) + sum(map(np.array, st["moments"]))
        alpha = np.linalg.solve(inertia, r.T @ moment - np.cross(w, inertia @ w))
        v = v + h * (force / m + g)
        w = w + h * alpha
        p = p + h * v
        ang = float(np.linalg.norm(w)) * h
        if ang > 0.0:
            k = math.sin(ang / 2.0) * h / ang
            q = quat_mul(q, (math.cos(ang / 2.0), w[0] * k, w[1] * k, w[2] * k))
            q = [x / math.sqrt(sum(y * y for y in q)) for x in q]
        r = quat_to_r(q)
        yield p, np.array(q), v, r @ w, st


class BodyLogReplay(unittest.TestCase):
    def test_a_rigid_body_under_the_logged_loads_moves_as_k2(self):
        log = step_body_log(box_design(susp="kc_table", wheel_mass=15.0), 100.0, after_s=0.3)
        self.assertGreater(log["n_steps"], 1000)
        self.assertEqual(len(log["steps"][0]["forces"]), 4)
        json.dumps(log)  # plain lists and numbers
        worst = 0.0
        for p, q, v, w, st in replay(log):
            worst = max(worst, float(np.max(np.abs(p - st["cg_m"]))), float(np.max(np.abs(q - st["quat"]))),
                        float(np.max(np.abs(v - st["v_m_s"]))), float(np.max(np.abs(w - st["omega_rad_s"]))))
        self.assertLess(worst, 1e-9)
        # the body did move: it pitched as the front wheels climbed
        pitch0 = quat_to_r(log["start"]["quat"])
        pitch1 = quat_to_r(log["steps"][-1]["quat"])
        self.assertGreater(float(np.max(np.abs(pitch1 - pitch0))), 1e-3)


if __name__ == "__main__":
    unittest.main()

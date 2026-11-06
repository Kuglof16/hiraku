"""The shell modal model (k1_shell.modal) and the modal_shell tool (N1)."""

import json
import math
import os
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract
from hiraku.solvers.k1_shell import element
from hiraku.solvers.k1_shell import modal as smodal
from hiraku.solvers.shelf.modal_shell import ModalShell, cantilever_strip_modes, make_mesh_record

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
SLOW = os.environ.get("HIRAKU_SLOW_TESTS") == "1"


class MassTest(unittest.TestCase):
    def test_total_mass_is_the_shell_and_the_parts(self):
        # assembly only (no factorisation): the 20 mm mesh of the demo vehicle
        mm = smodal.build_modal(DESIGN, element_size_mm=20.0)
        want = mm.shell_mass_kg + sum(it.mass_kg for it in smodal.mass_items(DESIGN, mm.mesh))
        self.assertGreater(mm.part_mass_kg, 0.0)
        np.testing.assert_allclose(mm.translational_mass_kg(), [want] * 3, rtol=1e-6)
        # the shell's own: rho t area summed over the elements
        _, xl, _, _ = element.local_frames(mm.model.element_xyz())
        self.assertAlmostEqual(mm.shell_mass_kg, 1000.0 * float(np.sum(mm.model.rho * mm.model.thickness
                                                                      * element.element_area(xl))),
                               delta=1e-9 * mm.shell_mass_kg)

    def test_rigid_point_rows_follow_a_rigid_motion(self):
        rng = np.random.default_rng(1)
        X = rng.random((7, 3)) * 100.0
        p = np.array([30.0, -50.0, 80.0])
        P = smodal.rigid_point_rows(X, np.arange(7), p)
        t, th = np.array([1.0, -2.0, 0.5]), np.array([0.01, 0.02, -0.03])
        u = t[None, :] + np.cross(th[None, :], X)
        np.testing.assert_allclose(P @ u.ravel(), t + np.cross(th, p), rtol=1e-12, atol=1e-12)


class KnownFrequencyTest(unittest.TestCase):
    def test_cantilever_strip_first_frequency_and_orthonormal_modes(self):
        L, b, t, E, rho = 500.0, 50.0, 2.0, 200000.0, 7.85e-9
        r = cantilever_strip_modes(L, b, t, E, 0.3, rho, 40, 4, 4)
        exact = 1.875104 ** 2 / (2 * math.pi * L * L) * math.sqrt(E * b * t ** 3 / 12.0 / (rho * b * t))
        self.assertLess(abs(r.frequencies[0] / exact - 1.0), 0.02, (r.frequencies[0], exact))
        # second bending (4.694091): the first four modes hold two bending modes within 3 %
        exact2 = exact * (4.694091 / 1.875104) ** 2
        self.assertLess(min(abs(f / exact2 - 1.0) for f in r.frequencies), 0.03)
        self.assertLess(r.orthonormality, 1e-8)
        self.assertLess(float(np.max(r.residuals)), smodal.RESIDUAL_TOL)

    def test_known_answers_pass(self):
        checks = ModalShell().known_answers()
        self.assertTrue(all(c["pass"] for c in checks), checks)


@unittest.skipUnless(SLOW, "a 20 mm shell factorisation of the demo vehicle (HIRAKU_SLOW_TESTS=1)")
class ToolRecordTest(unittest.TestCase):
    def test_record_fits_the_contract(self):
        lc = {"load_case_id": "N1-x", "test_id": "N1", "domain": "nvh", "title": "N1", "tolerance_rel": 0.15,
              "params": {"speed": {"value": 20.0, "unit": "km/h"}, "n_modes": {"value": 12, "unit": "1"}}}
        mesh = dict(make_mesh_record(DESIGN, 20.0), mesh_id="mesh-0001")
        rec = ModalShell().run(DESIGN, lc, [{"prediction_id": "pred-0001"}],
                               {"mesh": mesh, "element_size": {"value": 20.0, "unit": "mm"}})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        got = {r["quantity"]: r for r in rec["results"]}
        for q in ("first_frame_mode", "first_body_mode", "mode_1", "mode_12", "first_frame_mode_beam"):
            self.assertIn(q, got)
        shapes = [f for f in rec["fields"] if f["kind"] == "mode_shape"]
        self.assertEqual(len(shapes), 10)
        self.assertEqual(len(shapes[0]["per_mesh_node"]), len(mesh["nodes_mm"]))
        strains = [f for f in rec["fields"] if f["kind"] == "stress"]
        self.assertEqual(len(strains[0]["per_element"]), len(mesh["elements"]))
        self.assertAlmostEqual(max(strains[0]["per_element"]), 1.0)
        i = got["first_frame_mode"]["field_index"]
        self.assertEqual(rec["fields"][i]["frequency_Hz"], got["first_frame_mode"]["value"])


if __name__ == "__main__":
    unittest.main()

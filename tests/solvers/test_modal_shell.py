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
from hiraku.solvers.shelf.modal_shell import FRAME_SHARE, ModalShell, cantilever_strip_modes, first_frame_involving, \
    make_mesh_record

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
SLOW = os.environ.get("HIRAKU_SLOW_TESTS") == "1"
# v1's 20 mm linkage record (kinds and kinetic energy shares per mode, not a solve): see its "about"
V1_LINKAGE = json.loads((ROOT / "tests" / "solvers" / "data" / "modal_shell_v1_20mm_linkage_modes.json")
                        .read_text(encoding="utf-8"))


class FrameInvolvingModeTest(unittest.TestCase):
    """N1's first_frame_involving_mode: not a body mode, not a wheel mode, frame kinetic share >= 0.5 (roll bar too)."""

    @staticmethod
    def pick(modes):
        return first_frame_involving([m["kind"] for m in modes], [{"kinetic": m["kinetic"]} for m in modes])

    def test_v1_linkage_record_gives_the_roll_bar_mode(self):
        modes = V1_LINKAGE["modes"]
        i = self.pick(modes)
        # mode 4, the roll bar at 6.417 Hz (frame share 0.995), below the first frame mode 10.622 Hz (mode 10) and
        # below the wheel hop modes (mode 6 at 9.340 Hz is a hop coupled with the frame, share 0.425)
        self.assertEqual((i, modes[i]["kind"], modes[i]["frequency_Hz"]), (3, "roll bar", 6.417))
        self.assertEqual(modes[i]["frequency_Hz"], V1_LINKAGE["results"]["first_rollbar_mode"])
        self.assertLess(modes[i]["frequency_Hz"], V1_LINKAGE["results"]["first_frame_mode"])

    def test_without_the_roll_bar_modes_the_first_frame_mode(self):
        modes = [m for m in V1_LINKAGE["modes"] if not m["kind"].startswith("roll bar")]
        i = self.pick(modes)
        self.assertEqual((modes[i]["mode"], modes[i]["frequency_Hz"]), (10, V1_LINKAGE["results"]["first_frame_mode"]))

    def test_body_and_wheel_modes_never_count_and_the_share_decides(self):
        mk = lambda kind, fr: {"kind": kind, "kinetic": {"frame": fr}}  # noqa: E731
        self.assertIsNone(self.pick([mk("body bounce", 0.98), mk("wheel hop (front, in phase) (coupled with the frame)",
                                                                 0.9), mk("wheel steer rotation (rear)", 0.7)]))
        self.assertIsNone(self.pick([mk("arm local (FL)", FRAME_SHARE - 0.01)]))
        self.assertEqual(self.pick([mk("frame (coupled with the wheels)", FRAME_SHARE - 0.01),
                                    mk("frame (coupled with the arms)", FRAME_SHARE)]), 1)


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


def quarter_car(m_s=100.0, m_u=20.0, k_w=20.0, k_t=60.0):
    """One double-wishbone corner (k1_shell.linkage) under a sprung mass m_s (kg) that moves only vertically:
    equal parallel horizontal arms and tie rod, so the knuckle and wheel (one rigid point mass m_u) move straight
    up and down; the arms and tie rod nearly massless. Returns (solve_modes result, the corner info)."""
    from hiraku.solvers.k1_shell import linkage as lk

    hp = {"lca_front": [120, 200, 180], "lca_rear": [-120, 200, 180], "lca_outer": [0, 500, 180],
          "uca_front": [120, 200, 480], "uca_rear": [-120, 200, 480], "uca_outer": [0, 500, 480],
          "tie_inner": [-120, 200, 330], "tie_outer": [-120, 500, 330], "wheel_center": [0, 560, 330],
          "damper_lower": [0, 400, 180], "damper_upper": [0, 400, 700]}
    mat = {"E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 7850.0}
    bush = {"radial": 3000.0, "axial": 500.0, "tie_rod": 10000.0}
    tiny = 1e-6
    parts = lk.CornerParts([(m_u, np.array(hp["wheel_center"], float), None)],
                           {k: tiny for k in ("lca_f", "lca_r", "lca_x", "uca_f", "uca_r")}, tiny, 0.0, 0.0, 0.0)
    k_s, _, _ = lk.fit_spring_rate("Q", hp, 300.0, parts, mat, 25.4, 2.0, bush, k_w)
    xyz = []

    def add(x):
        xyz.append(np.asarray(x, float))
        return len(xyz) - 1

    asm = lk.Assembly(add)
    body = asm.node([0.0, 0.0, 400.0], "sprung")
    asm.point_mass(body, m_s / 1000.0)
    info = lk.build_corner(asm, "Q", hp, 300.0, lambda _m: (body, asm.xyz[body]), parts, mat, 25.4, 2.0, bush, k_t,
                           k_s, k_w)
    n_dof = 6 * len(xyz)
    Kc, M = asm.matrices(n_dof)
    K = sum(Kc.values()).tocsr()
    fixed = [6 * body + j for j in (0, 1, 3, 4, 5)]
    return smodal.solve_modes(K, M, np.array(xyz), [], 4, fixed, sigma_hz=0.1), info


class LinkageQuarterCarTest(unittest.TestCase):
    def test_quarter_car_two_frequencies_match_the_two_mass_formula(self):
        m_s, m_u, k_w, k_t = 100.0, 20.0, 20.0, 60.0
        r, info = quarter_car(m_s, m_u, k_w, k_t)
        a, b = m_s * m_u / 1e6, -(m_s * (k_w + k_t) + m_u * k_w) / 1e3
        disc = math.sqrt(b * b - 4 * a * k_w * k_t)
        want = sorted(math.sqrt(w2) / (2 * math.pi) for w2 in ((-b - disc) / (2 * a), (-b + disc) / (2 * a)))
        got = sorted(r.frequencies)[:2]
        for g, w in zip(got, want):
            self.assertLess(abs(g / w - 1.0), 0.02, (got, want))
        self.assertGreater(info.spring_rate, k_w)  # the spring sits inboard: motion ratio < 1

    def test_quarter_car_modes_are_m_orthonormal(self):
        r, _ = quarter_car()
        self.assertLess(r.orthonormality, smodal.ORTHO_TOL)
        self.assertLess(float(np.max(r.residuals)), smodal.RESIDUAL_TOL)

    def test_spring_rate_gives_the_wheel_rate(self):
        from hiraku.solvers.k1_shell import linkage as lk

        hp = smodal_hardpoints()
        mat = {"E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 7850.0}
        bush = {"radial": 3000.0, "axial": 500.0, "tie_rod": 10000.0}
        parts = lk.CornerParts([(18.0, np.array(hp["wheel_center"], float), None)],
                               {k: 0.3 for k in ("lca_f", "lca_r", "lca_x", "uca_f", "uca_r")}, 0.9, 0.2, 0.2, 2.0)
        k_s, ratio, _ = lk.fit_spring_rate("FL", hp, 300.0, parts, mat, 25.4, 2.0, bush, 20.0)
        self.assertAlmostEqual(lk.corner_rate("FL", hp, 300.0, parts, mat, 25.4, 2.0, bush, k_s), 20.0, delta=1e-6)
        self.assertTrue(0.3 < ratio < 1.0, ratio)


def smodal_hardpoints():
    from hiraku.solvers.k2_vehicle.model import hardpoints_of

    return hardpoints_of(DESIGN, "FL")


@unittest.skipUnless(SLOW, "a 20 mm shell factorisation of the demo vehicle on its linkage (HIRAKU_SLOW_TESTS=1)")
class LinkageToolRecordTest(unittest.TestCase):
    def test_linkage_record_fits_the_contract(self):
        lc = {"load_case_id": "N1-x", "test_id": "N1", "domain": "nvh", "title": "N1", "tolerance_rel": 0.15,
              "params": {"speed": {"value": 20.0, "unit": "km/h"}, "n_modes": {"value": 12, "unit": "1"}}}
        mesh = dict(make_mesh_record(DESIGN, 20.0), mesh_id="mesh-0001")
        rec = ModalShell().run(DESIGN, lc, [{"prediction_id": "pred-0001"}],
                               {"mesh": mesh, "element_size": {"value": 20.0, "unit": "mm"}, "suspension": "linkage",
                                "tyre_stiffness": {"value": 60.0, "unit": "N/mm"}})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        got = {r["quantity"]: r for r in rec["results"]}
        for q in ("first_frame_mode", "first_body_mode", "mode_1", "mode_12", "first_wheel_hop_mode",
                  "first_frame_involving_mode"):
            self.assertIn(q, got)
        self.assertEqual(got["unsupported_load"]["value"], 0.0)  # the path check reads it on the deciding analysis
        # the roll bar's mode (as in the saved record, there mode 4 at 6.417 Hz) and the effect that names it
        self.assertAlmostEqual(got["first_frame_involving_mode"]["value"], got["first_rollbar_mode"]["value"], places=9)
        self.assertTrue(any(e["name"].startswith("first frame-involving mode (N1): mode ") and "(roll bar)" in e["name"]
                            for e in rec["effects"]))
        shapes = [f for f in rec["fields"] if f["kind"] == "mode_shape"]
        self.assertEqual(len(shapes[0]["per_mesh_node"]), len(mesh["nodes_mm"]))
        layout = smodal.linkage_layout(DESIGN)
        self.assertEqual(set(shapes[0]["per_node"]), set(layout["points"]))
        big = max(max(abs(x) for v in s["per_node"].values() for x in v) for s in shapes[:1])
        self.assertLessEqual(big, 1.0 + 1e-9)
        # the body bounce lies near the quarter-car estimate on the 60 N/mm tyres (a check of the scale)
        self.assertTrue(1.0 < got["first_body_mode"]["value"] < 3.0, got["first_body_mode"])


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

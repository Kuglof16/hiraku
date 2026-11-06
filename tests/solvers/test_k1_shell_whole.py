"""The whole-vehicle step-hit model (k1_shell.whole): the linkage's lever, and no additions = today's solve."""

import json
import unittest
from pathlib import Path

import numpy as np
from scipy.sparse.linalg import spsolve

from hiraku.solvers.k1_shell.frame import ShellFrame, analyze_shell
from hiraku.solvers.k1_shell.whole import _RecAsm, build_whole, linkage_mounts
from hiraku.solvers.shelf.frame_static import upstream_mount_series
from tests.solvers.test_frame_frames import ladder, series_analysis

V005 = Path(__file__).resolve().parents[2] / "studies" / "lere" / "v005.json"


def corner_alone(design, sid):
    """v005's corner sid on one held frame node: (assembly, n_dof, recorded frame-side springs, info, motion ratio)."""
    from hiraku.designgen.kit.arms import ARM_D_MM, ARM_T_MM
    from hiraku.solvers.k0_handcalc.vehicle import mass_summary
    from hiraku.solvers.k1_shell import linkage as lk
    from hiraku.solvers.k1_shell.modal import arm_material, corner_parts
    from hiraku.solvers.k2_vehicle.model import _bushings, hardpoints_of

    s = next(s for s in design["supports"] if s["id"] == sid)
    hp = hardpoints_of(design, sid)
    mat, _ = arm_material(design)
    b = _bushings(design, s["suspension"], [])
    bush = {"radial": b.radial / 1000.0, "axial": b.axial / 1000.0, "tie_rod": b.tie_rod / 1000.0}
    items = [it for it in mass_summary(design)["items"] if it.carrier == "support" and it.carried_by[0] == sid]
    parts = corner_parts(design, sid, items, hp, mat)
    k_w = float(s["suspension"]["stiffness_N_per_mm"])
    k_s, ratio, _ = lk.fit_spring_rate(sid, hp, float(s["radius_mm"]), parts, mat, ARM_D_MM, ARM_T_MM, bush, k_w)
    xyz = []

    def add(x):
        xyz.append(np.asarray(x, float))
        return len(xyz) - 1

    rec = []
    frame = add(hp["wheel_center"])
    asm = _RecAsm(add, {frame: (sid, "frame")}, rec)
    asm.xyz[frame], asm.names[frame] = xyz[frame], "frame"

    def carrier(_mount):
        return frame, asm.xyz[frame]

    info = lk.build_corner(asm, sid, hp, float(s["radius_mm"]), carrier, parts, mat, ARM_D_MM, ARM_T_MM, bush,
                           0.0, k_s, k_w)
    return asm, 6 * len(xyz), rec, info, ratio, hp, frame


class LinkageLever(unittest.TestCase):
    """A vertical contact force alone on the linkage (frame held) reaches the frame in full, and the coil spring
    takes it over the motion ratio (the lever)."""

    def test_vertical_contact_force_and_motion_ratio(self):
        from hiraku.solvers.k1_shell.linkage import offset_rows

        with open(V005, encoding="utf-8") as f:
            d = json.load(f)
        for sid in ("FL", "RL"):
            asm, n_dof, rec, info, ratio, hp, frame = corner_alone(d, sid)
            Kc, _ = asm.matrices(n_dof)
            K = sum(Kc.values()).tocsr()
            free = np.setdiff1d(np.arange(n_dof), np.arange(6 * frame, 6 * frame + 6))
            F = 1000.0
            contact = info.contact
            xb = asm.xyz[info.body]
            Fv = np.zeros(n_dof)
            Fv[6 * info.body + 2] = F
            Fv[6 * info.body + 3:6 * info.body + 6] = np.cross(contact - xb, [0.0, 0.0, F])
            u = np.zeros(n_dof)
            u[free] = spsolve(K[free][:, free].tocsc(), Fv[free])
            fr = type("F", (), {"frame_springs": rec})()
            got = linkage_mounts(fr, u)[sid]["frame"]
            np.testing.assert_allclose(got, [0.0, 0.0, F], atol=1e-6 * F)  # all of it reaches the frame
            sp = next(r for r in rec if asm.names[r[2]].endswith("damper_lower"))
            u6 = u.reshape(-1, 6)
            f_spring = sp[6] @ (offset_rows(sp[3]) @ u6[sp[2]] - offset_rows(sp[5]) @ u6[sp[4]])
            e = np.asarray(hp["damper_upper"], float) - np.asarray(hp["damper_lower"], float)
            along = float(f_spring @ e / np.linalg.norm(e))
            print(f"{sid}: motion ratio {ratio:.4f}, spring force {along:.1f} N for {F:g} N at the contact, "
                  f"spring force x ratio / F = {along * ratio / F:.4f}")
            self.assertAlmostEqual(along * ratio / F, 1.0, delta=0.02)


class NoAdditionsIsToday(unittest.TestCase):
    """model "whole" with no additions is today's solve; with an addition the design has nothing for (panels on a
    design with no panels) the whole model's own build gives today's answer too."""

    @classmethod
    def setUpClass(cls):
        cls.d, st = ladder()
        _, _, inst = upstream_mount_series({"P1-x": series_analysis(cls.d, st, torque_Nm=40.0)}, cls.d)
        cls.inst = inst[1]
        cls.fr = ShellFrame.build(cls.d, 25.0)

    def solve(self, **kw):
        i = self.inst
        return analyze_shell(self.d, mount_loads=i["mount_loads"], gravity_mps2=i["gravity_mps2"],
                             inertia_N=i["inertia_N"], mount_path="footprint", **kw)

    def test_w0_equals_today(self):
        a = self.solve(frame=self.fr)
        b = self.solve(frame=self.fr, model="whole", adds=[])
        np.testing.assert_array_equal(b["von_mises"]["max"], a["von_mises"]["max"])
        self.assertEqual([h["value"] for h in b["hot_spots"]], [h["value"] for h in a["hot_spots"]])
        self.assertEqual(b["model"], "frame")

    def test_whole_build_without_effect_equals_today(self):
        a = self.solve(frame=self.fr)
        wf = build_whole(self.d, ["panels"], 25.0)
        self.assertIn("panels: the design has no part with a thickness", wf.notes)
        b = self.solve(frame=wf, model="whole", adds=["panels"])
        np.testing.assert_allclose(b["von_mises"]["max"], a["von_mises"]["max"], rtol=1e-9, atol=1e-9)
        self.assertAlmostEqual(b["hot_spot_max"]["value"], a["hot_spot_max"]["value"], places=6)
        self.assertEqual(b["adds"], ["panels"])

    def test_unknown_addition(self):
        with self.assertRaisesRegex(ValueError, "unknown additions"):
            build_whole(self.d, ["wings"], 25.0)


if __name__ == "__main__":
    unittest.main()

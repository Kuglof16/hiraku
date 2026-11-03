"""K0: the lower arm's leg in the ladder's twist (frame_static.mount_leg_stress) and its inputs."""

import json
import unittest
from pathlib import Path

import numpy as np

from hiraku import designgen
from hiraku.solvers.k0_handcalc import frame_inputs
from hiraku.solvers.k0_handcalc.library import all_formulas
from hiraku.solvers.k0_handcalc.library_mount import half_u
from hiraku.solvers.k0_handcalc.registry import Registry

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
LEG = {"dx": 165.0, "dy": -154.0, "dz": -200.0, "y_p": 196.0, "b_l": 40.0, "t_l": 2.3, "b_x": 40.0, "t_x": 2.3,
       "E": 205000.0, "nu": 0.3}
STIFF = {"k_x": 1e18, "k_y": 1e18, "k_z": 1e18}


def pynite_u(theta: float) -> tuple[float, float, float]:
    """The whole U in PyNite (K1's member axes), its bases turned +-theta about y: the left leg's base N, My, Mz."""
    from Pynite import FEModel3D

    from hiraku.solvers.k1_frame.model import to_pynite

    yr = LEG["y_p"] - LEG["dy"]  # the rail's distance from the centre line
    P = {"BL": np.array([0.0, yr, 0.0]), "TL": np.array([LEG["dx"], LEG["y_p"], LEG["dz"]]),
         "TR": np.array([LEG["dx"], -LEG["y_p"], LEG["dz"]]), "BR": np.array([0.0, -yr, 0.0])}
    b, t = LEG["b_l"], LEG["t_l"]
    bi = b - 2 * t
    A, I, J = b * b - bi * bi, (b**4 - bi**4) / 12.0, 4 * ((b - t) ** 2) ** 2 * t / (4 * (b - t))
    m = FEModel3D()
    m.add_material("s", LEG["E"], LEG["E"] / 2.6, 0.3, 0.0)
    m.add_section("t", A, I, I, J)
    for n, p in P.items():
        m.add_node(n, *to_pynite(p))
    m.add_member("legL", "BL", "TL", "s", "t")
    m.add_member("cross", "TL", "TR", "s", "t")
    m.add_member("legR", "BR", "TR", "s", "t")
    for n, sg in (("BL", 1.0), ("BR", -1.0)):
        m.def_support(n, True, True, True, True, True, True)
        r = to_pynite(np.array([0.0, sg * theta, 0.0]))
        for c, v in zip(("RX", "RY", "RZ"), r):
            m.def_node_disp(n, c, float(v))
    m.add_load_combo("c", {"L": 1.0})
    m.add_node_load("TL", "FX", 0.0, "L")
    m.analyze_linear()
    pm = m.members["legL"]
    return pm.axial(0, "c"), pm.moment("My", 0, "c"), pm.moment("Mz", 0, "c")


class HalfUTest(unittest.TestCase):
    def test_force_method_matches_the_whole_u(self):
        """The half U cut at the centre line (force method) against the whole U solved by PyNite."""
        theta = 2.5e-3
        u = half_u(dict(LEG, **STIFF), theta)
        N, My, Mz = pynite_u(theta)
        self.assertAlmostEqual(abs(u["M_y"]), abs(My), delta=0.01 * abs(My))
        self.assertAlmostEqual(abs(u["M_z"]), abs(Mz), delta=0.01 * abs(Mz))
        self.assertLess(abs(u["N"]), 1.0)
        self.assertAlmostEqual(u["theta_base"], theta, delta=1e-9)

    def test_softer_rail_takes_some_of_the_turn(self):
        stiff = half_u(dict(LEG, **STIFF))
        soft = half_u(dict(LEG, k_x=1e8, k_y=3e8, k_z=1e8))
        self.assertLess(abs(soft["sigma"]), abs(stiff["sigma"]))
        self.assertLess(soft["theta_base"], 1.0)
        self.assertGreater(soft["theta_base"], 0.5)

    def test_linear_in_theta(self):
        a, b = half_u(dict(LEG, **STIFF), 1e-3), half_u(dict(LEG, **STIFF), 2e-3)
        self.assertAlmostEqual(b["sigma"], 2.0 * a["sigma"], places=9)

    def test_record_and_wheels(self):
        reg = Registry(all_formulas())
        q = {k: {"value": v, "unit": "mm"} for k, v in LEG.items() if k not in ("E", "nu")}
        q.update(E={"value": 205000.0, "unit": "MPa"}, nu={"value": 0.3, "unit": "1"},
                 **{k: {"value": 2e8, "unit": "N*mm"} for k in ("k_x", "k_y", "k_z")},
                 theta_front={"value": 3e-3, "unit": "rad"}, theta_rear={"value": -1e-3, "unit": "rad"})
        out = {k: v["value"] for k, v in reg.evaluate("frame_static.mount_leg_stress", q)["outputs"].items()}
        self.assertAlmostEqual(out["sigma_fa"], -out["sigma_fb"])
        self.assertAlmostEqual(out["sigma_fa"] / out["sigma_ra"], -3.0)
        self.assertAlmostEqual(abs(out["sigma_fa"]), 0.5 * 3.0 * out["sigma_per_mrad"], places=6)
        worst = reg.evaluate("fatigue.worst_of_two_welds", {"D_a": {"value": 0.2, "unit": "1"}, "D_b": {"value": 0.3, "unit": "1"}})
        self.assertEqual((worst["outputs"]["D"]["value"], worst["outputs"]["which"]["value"]), (0.3, 2.0))


class MountInputsTest(unittest.TestCase):
    def test_u_frames_on_the_demo(self):
        frames = frame_inputs.mount_frames(V1)
        self.assertEqual([(u["leg"], u["cross"], u["leg_mirror"]) for u in frames],
                         [("lower_leg_LA1", "lower_cross_A1", "lower_leg_RA1"),
                          ("lower_leg_LA2", "lower_cross_A2", "lower_leg_RA2")])
        off = designgen.regenerate(V1, {"lower_cross": {"value": 0, "unit": "1"}})
        self.assertEqual(frame_inputs.mount_frames(off), [])

    def test_rail_springs_against_k1(self):
        """The chains' springs at the rear leg's node against K1 (the frame without the lower cross members, a pair
        of 1 kN*m moments at the node and its mirror, balanced): roll, opposite pitch and yaw within 10 %."""
        from hiraku.solvers.k1_frame.model import COMBO, build, to_design

        d = designgen.regenerate(V1, {"lower_cross": {"value": 0, "unit": "1"}})
        k = frame_inputs.rail_springs(d, "LAp2")
        w, M = 700.0, 1.0e6

        def k1(axis):
            fm = build(d)
            mL, mR, fL = np.zeros(3), np.zeros(3), np.zeros(3)
            mL["xyz".index(axis)] = M
            mR["xyz".index(axis)] = -M if axis == "y" else M
            if axis == "x":
                fL[2] = -2 * M / w
            if axis == "z":
                fL[0] = 2 * M / w
            fm.add_force("LAp2", fL, mL)
            fm.add_force("RAp2", -fL, mR)
            fm.restrain_free_body()
            fm.solve()
            rot = {n: to_design([getattr(fm.model.nodes[n], r)[COMBO] for r in ("RX", "RY", "RZ")]) for n in ("LAp2", "RAp2")}
            uL, uR = fm.displacement("LAp2"), fm.displacement("RAp2")
            rigid = {"x": (uL[2] - uR[2]) / w, "y": 0.0, "z": -(uL[0] - uR[0]) / w}[axis]
            i = "xyz".index(axis)
            turn = (rot["LAp2"][i] - rot["RAp2"][i]) / 2.0 if axis == "y" else rot["LAp2"][i] - rigid
            return M / turn

        for axis in "xyz":
            got = k1(axis)
            self.assertAlmostEqual(k[f"k_{axis}"] / got, 1.0, delta=0.10, msg=f"k_{axis}: chain {k[f'k_{axis}']:.3g}, K1 {got:.3g}")

    def test_leg_inputs_on_the_demo(self):
        legs = frame_inputs.mount_leg_inputs(V1, loading="two_cargo")
        self.assertEqual([x["leg"] for x in legs], ["lower_leg_LA1", "lower_leg_LA2"])
        rear = legs[1]["inputs"]
        self.assertEqual((rear["dx"]["value"], rear["dy"]["value"], rear["dz"]["value"], rear["y_p"]["value"]),
                         (165.0, -154.0, -200.0, 196.0))
        # the front wheels pitch the rails apart more there than the rear ones, and the other way round
        self.assertLess(rear["theta_rear"]["value"] / rear["theta_front"]["value"], 0.0)
        self.assertGreater(abs(rear["theta_front"]["value"]), abs(rear["theta_rear"]["value"]))


if __name__ == "__main__":
    unittest.main()

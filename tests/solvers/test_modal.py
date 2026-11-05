import copy
import json
import math
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.solvers.k1_frame.modal import modes

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
PRED = [{"prediction_id": "pred-0001"}]
TOOL = {t.name: t for t in solvers.builtin_tools()}["modal"]


def q(v, u):
    return {"value": v, "unit": u}


def lc(**params):
    return {"load_case_id": "N1", "test_id": "N1", "domain": "nvh", "title": "N1", "params": params, "tolerance_rel": 0.15}


def results(rec):
    return {r["quantity"]: r for r in rec["results"]}


class ModalTest(unittest.TestCase):
    def test_checks(self):
        self.assertEqual([c for c in TOOL.check() if not c["pass"]], [])

    def test_first_frame_mode_is_the_ladder_twist(self):
        """Held in plane at the frame nodes, the lowest frame mode is the twist (left and right rails opposite),
        with little fore-and-aft motion, and the hand twist frequency is within the tolerance of it."""
        from hiraku.contract.loading import on_board
        from hiraku.solvers.k0_handcalc.frame_inputs import ladder_twist_inputs

        d = on_board(DESIGN, "two_cargo")
        r = modes(d, 6)
        # the ladder's own bending (the roll bar's sway, on by default, bends the hoop and leaves the ladder as it is)
        i = next(k for k, x in enumerate(r["ladder_flexibility"]) if x >= 0.3)
        from hiraku.solvers.k1_frame.modal import ladder_nodes

        u = {n: r["shapes"][i][n] for n in ladder_nodes(d, list(r["shapes"][i]))}  # the ladder's nodes
        self.assertLess(max(abs(v[0]) for v in u.values()), 0.25 * max(abs(v[2]) for v in u.values()))
        self.assertLess(u["LE"][2] * u["RE"][2], 0.0)  # the rear corners move opposite ways
        hand = solvers.formula_registry().evaluate("modal.ladder_twist_mode", ladder_twist_inputs(d)["inputs"])
        f = hand["outputs"]["f"]["value"]
        self.assertLessEqual(abs(r["frequencies"][i] - f) / f, 0.15)

    def test_frequency_response_series(self):
        rec = TOOL.run(DESIGN, lc(speed=q(40, "km/h")), PRED, {})
        s = next(x for x in rec["series"] if x["channel"].startswith("frequency_response."))
        self.assertEqual((s["x"]["quantity"], s["x"]["unit"], len(s["x"]["values"])), ("frequency", "Hz", len(s["values"])))
        self.assertAlmostEqual(s["values"][0], 1.0, places=4)  # over its value at 0 Hz
        xs, vs = s["x"]["values"], s["values"]
        peak = xs[max(range(len(vs)), key=vs.__getitem__)]
        # the peak is at one of the modes. On the demo (wheelbase 2020 mm, suspension towers) the response has two
        # nearly equal peaks: the body bouncing on its springs (about 1.5 Hz) and the frame's first mode near 9 Hz.
        # Which one is higher has flipped with small changes (with the plates of their real thickness, 318ea65, the
        # seat 16.8 mm lower: 1.697 at 1.48 Hz against 1.687 at 8.93 Hz; before it, 1.651 against 1.749), so the
        # test asks for a local peak at the frame's first mode, not for the highest one
        r = results(rec)
        modes_hz = [r[k]["value"] for k in r if k.startswith("mode_")]
        self.assertLess(min(abs(peak - f) / f for f in modes_hz), 0.15)
        local = [xs[j] for j in range(1, len(vs) - 1) if vs[j] > vs[j - 1] and vs[j] >= vs[j + 1]]
        f1 = r["first_frame_mode"]["value"]
        self.assertLess(min(abs(x - f1) / f1 for x in local), 0.15)
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))

    def test_ladder_twist_formula(self):
        E, nu, Jr, Jc, n, w, L = 205000.0, 0.3, 3.4e5, 1.2e5, 6.0, 700.0, 2500.0
        I0, rx, k, t, d = 100.0, 640.0, 20.0, 1100.0, 900.0
        G = E / 2.6
        K = 2 * G * Jr * L + n * G * Jc * w + 2 * k * (t / 2) ** 2 * d**2 * 2
        expect = math.sqrt(1e3 * K / (I0 * 1e6 * rx**2)) / (2 * math.pi)
        ins = {"E": q(E, "MPa"), "nu": q(nu, "1"), "J_r": q(Jr, "mm^4"), "J_c": q(Jc, "mm^4"), "n_c": q(n, "1"),
               "w": q(w, "mm"), "L": q(L, "mm"), "I0": q(I0, "kg*m^2"), "r_x": q(rx, "mm"), "k_f": q(k, "N/mm"),
               "k_r": q(k, "N/mm"), "t_f": q(t, "mm"), "t_r": q(t, "mm"), "d_f": q(d, "mm"), "d_r": q(-d, "mm")}
        got = solvers.formula_registry().evaluate("modal.ladder_twist_mode", ins)["outputs"]["f"]["value"]
        self.assertAlmostEqual(got, expect, delta=1e-9 * expect)

    def test_the_same_modes_every_time(self):
        """A fixed start vector and signed shapes: the scripted runs are compared byte for byte."""
        a, b = modes(DESIGN, n_modes=6), modes(DESIGN, n_modes=6)
        self.assertEqual(a["frequencies"].tolist(), b["frequencies"].tolist())
        for sa, sb in zip(a["shapes"], b["shapes"]):
            self.assertEqual({n: v.tolist() for n, v in sa.items()}, {n: v.tolist() for n, v in sb.items()})
            comps = [c for v in sa.values() for c in v]
            big = max(abs(c) for c in comps)
            self.assertGreater(next(c for c in comps if abs(c) >= big * (1 - 1e-9)), 0.0)
        contract.validate("tool_spec", TOOL.spec())

    def test_demo(self):
        rec = TOOL.run(DESIGN, lc(), PRED, {})
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        r = results(rec)
        f = [r[f"mode_{i}"]["value"] for i in range(1, 11)]
        self.assertEqual(f, sorted(f))
        self.assertLess(r["first_body_mode"]["value"], 4.0)  # body on 20 N/mm springs
        self.assertGreater(r["first_frame_mode"]["value"], r["first_body_mode"]["value"])
        # 40 km/h on 300 mm wheels: 11.11 / (2 pi 0.3) = 5.89 Hz
        self.assertAlmostEqual(r["wheel_rotation_frequency"]["value"], (40 / 3.6) / (2 * math.pi * 0.3), places=9)
        self.assertEqual({fl["kind"] for fl in rec["fields"]}, {"mode_shape"})
        for fl in rec["fields"]:
            self.assertAlmostEqual(max(math.sqrt(sum(x * x for x in v)) for v in fl["per_node"].values()), 1.0, places=9)

    def test_lighter_vehicle_has_higher_modes(self):
        full = results(TOOL.run(DESIGN, lc(), PRED, {}))
        empty = results(TOOL.run(DESIGN, lc(loading="empty"), PRED, {}))
        self.assertGreater(empty["first_body_mode"]["value"], full["first_body_mode"]["value"])

    def test_stiffer_springs_raise_the_body_modes_only(self):
        d = copy.deepcopy(DESIGN)
        for s in d["supports"]:
            s["suspension"]["stiffness_N_per_mm"] = 80.0
        a, b = modes(DESIGN, 6), modes(d, 6)
        self.assertAlmostEqual(b["frequencies"][0] / a["frequencies"][0], 2.0, delta=0.05)  # sqrt(4)

    def test_hand_formulas(self):
        R = solvers.formula_registry()
        o = R.evaluate("modal.quarter_car", {"k_s": q(20, "N/mm"), "k_t": q(150, "N/mm"), "m_s": q(125, "kg")})["outputs"]
        self.assertAlmostEqual(o["f"]["value"], 1.891, delta=0.0005)
        o = R.evaluate("modal.spring_mass", {"k": q(1, "N/mm"), "m": q(1, "kg")})["outputs"]
        self.assertAlmostEqual(o["f"]["value"], math.sqrt(1000) / (2 * math.pi), places=12)
        o = R.evaluate("modal.rotation_excitation", {"v": q(40, "km/h"), "r": q(300, "mm"), "i": q(10, "1")})["outputs"]
        self.assertAlmostEqual(o["f_motor"]["value"], 10 * o["f_wheel"]["value"], places=12)
        # steel 40x80x2 tube, 1 m: (pi/2) sqrt(E I / rho A)
        I = (40 * 80**3 - 36 * 76**3) / 12 * 1e-12
        A = (40 * 80 - 36 * 76) * 1e-6
        o = R.evaluate("modal.simple_beam_first_mode", {"E": q(205000, "MPa"), "rho": q(7850, "kg/m^3"), "L": q(1000, "mm"),
                                                         "b": q(40, "mm"), "h": q(80, "mm"), "t": q(2, "mm")})["outputs"]
        self.assertAlmostEqual(o["f1"]["value"], math.pi / 2 * math.sqrt(205e9 * I / (7850 * A)), places=6)


if __name__ == "__main__":
    unittest.main()

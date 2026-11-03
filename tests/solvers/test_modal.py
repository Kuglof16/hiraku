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

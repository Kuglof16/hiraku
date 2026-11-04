"""The road (spectrum) damage continues the S-N curve below the knee with slope 2 m - 1
(IIW-1823-07 (2008) sec. 4.3.1, p.110), not with the design's constant-amplitude slope."""

import copy
import json
import math
import unittest
from pathlib import Path

import numpy as np

from hiraku import solvers
from hiraku.solvers.k0_handcalc.library_fatigue import spectrum_slope
from hiraku.solvers.shelf.fatigue import sn_series

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
TOOL = {t.name: t for t in solvers.builtin_tools()}["fatigue"]
PRED = [{"prediction_id": "pred-0001"}]


def q(v, u):
    return {"value": v, "unit": u}


def lc(**params):
    return {"load_case_id": "R3", "test_id": "R3", "domain": "strength", "title": "R3", "params": params, "tolerance_rel": 0.5}


ROAD = dict(distance=q(5000, "km"), road_class="C", speed=q(20, "km/h"), sample_length=q(200, "m"))
QC = dict(sigma_w=q(22.0, "MPa"), W=q(1700.0, "N"), m_u=q(22.0, "kg"), k_s=q(20.0, "N/mm"), zeta=q(0.3, "1"),
          k_t=q(150.0, "N/mm"), G_d=q(256e-6 * 1e9, "mm^3"), v=q(40.0, "km/h"), d=q(5000.0, "km"),
          FAT=q(71.0, "MPa"), m=q(3.0, "1"), N_knee=q(1e7, "1"))


def cycles_to_failure(dS, fat, m, n_knee, m2):
    """The IIW-type curve written out again here: FAT at 2e6 cycles, slope m down to the knee, m2 below."""
    s_knee = fat * (2e6 / n_knee) ** (1 / m)
    return 2e6 * (fat / dS) ** m if dS >= s_knee else n_knee * (s_knee / dS) ** m2


class SpectrumSlopeTest(unittest.TestCase):
    def test_two_m_minus_one(self):
        self.assertEqual(spectrum_slope(3.0), 5.0)
        self.assertEqual(spectrum_slope(5.0), 9.0)

    def test_road_formulas_take_no_m2(self):
        reg = solvers.formula_registry()
        for fid in ("fatigue.road_damage_quarter_car", "fatigue.road_damage_four_wheels"):
            self.assertNotIn("m2", reg.get(fid).inputs, fid)
        self.assertIn("m2", reg.get("fatigue.constant_amplitude_damage").inputs)
        with self.assertRaisesRegex(ValueError, "unknown input"):
            reg.evaluate("fatigue.road_damage_quarter_car", dict(QC, m2=q(22.0, "1")))

    def test_quarter_car_damage_is_rayleigh_on_slope_five(self):
        """The formula's damage against the Rayleigh integral done here with the curve written out (slope 5)."""
        out = {k: v["value"] for k, v in solvers.formula_registry().evaluate("fatigue.road_damage_quarter_car", QC)["outputs"].items()}
        s = out["sigma_s"]
        a = np.linspace(s * 1e-4, 8.0 * s, 20001)
        p = a / s**2 * np.exp(-a**2 / (2 * s**2))
        inv = np.array([1.0 / cycles_to_failure(2 * x, 71.0, 3.0, 1e7, 5.0) for x in a])
        self.assertAlmostEqual(out["D"], out["n"] * float(np.trapezoid(p * inv, a)), delta=1e-3 * out["D"])

    def test_sn_series_shows_the_slope_used(self):
        curve = sn_series(71.0, 3.0, 1e7, spectrum_slope(3.0), [(50.0, 0.0, 1.0)], "m")[0]
        N, S = np.array(curve["x"]["values"]), np.array(curve["values"])
        s_knee = 71.0 * 0.2 ** (1 / 3)
        below = N > 1.01e7
        self.assertTrue(np.allclose(S[below], s_knee * (1e7 / N[below]) ** (1 / 5), rtol=1e-4))


class ToolSpectrumTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = TOOL.run(DESIGN, lc(**ROAD), PRED, {})

    def damage(self, rec):
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        return next(r["value"] for r in rec["results"] if r["quantity"] == "fatigue_damage")

    def test_design_slope_after_knee_does_not_change_the_damage(self):
        d = copy.deepcopy(DESIGN)
        d["variables"]["fatigue_slope_after_knee_constant_amplitude"] = q(9.0, "1")
        self.assertEqual(self.damage(TOOL.run(d, lc(**ROAD), PRED, {})), self.damage(self.base))
        del d["variables"]["fatigue_slope_after_knee_constant_amplitude"]
        self.assertEqual(self.damage(TOOL.run(d, lc(**ROAD), PRED, {})), self.damage(self.base))

    def test_damage_below_the_knee_on_slope_five(self):
        """The stress-range bands below the knee (41.5 MPa for FAT 71) add up with slope 5, so they carry a share
        the steep constant-amplitude slope would not give them; the bands still sum to the damage."""
        D = self.damage(self.base)
        bands = {e["name"]: e["value"] for e in self.base["effects"] if "share" in e}
        self.assertAlmostEqual(sum(bands.values()), D, delta=1e-9 * D)
        self.assertGreater(bands["stress ranges 20-40 MPa"], 0.0)
        self.assertGreater(bands["stress ranges 10-20 MPa"], 0.0)

    def test_assumption_names_the_slope_and_the_source(self):
        text = " ".join(self.base["assumptions"])
        self.assertIn("5 (= 2 × 3 − 1)", text)
        self.assertIn("IIW-1823-07", text)
        self.assertIn("fatigue_slope_after_knee_constant_amplitude = 22", text)

    def test_known_answers_pass(self):
        failed = [c for c in TOOL.check() if not c["pass"]]
        self.assertEqual(failed, [])
        self.assertTrue(any(c.get("quantity") == "cycles_at_half_the_knee_range" or "half_the_knee" in json.dumps(c)
                            for c in TOOL.check()))
        self.assertTrue(math.isfinite(self.damage(self.base)))


if __name__ == "__main__":
    unittest.main()

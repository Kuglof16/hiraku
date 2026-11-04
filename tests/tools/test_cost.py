"""Cost estimates per model mix (hiraku.catalog.cost)."""

import json
import tempfile
import unittest
from pathlib import Path

from hiraku.catalog import CATALOG_DIR, read_data
from hiraku.catalog import cost
from hiraku.catalog.mixes import mix_names

PER_CALL = cost.CALIBRATION_USD / cost.CALIBRATION_CALLS


def run_folder(tmp, events):
    (Path(tmp) / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return tmp


EVENTS = [
    {"kind": "prediction.recorded", "agent": "cae.strength", "data": {"prediction_id": "pred-0001"}},
    {"kind": "analysis.started", "agent": "cae.strength", "data": {"analysis_id": "ana-0001"}},
    {"kind": "comparison.recorded", "agent": "cae.strength", "data": {"comparison_id": "cmp-0001"}},
    {"kind": "comparison.recorded", "agent": "system", "data": {"comparison_id": "cmp-0001"}},  # updated: once, the first agent's
    {"kind": "prediction.recorded", "agent": "packager", "data": {"prediction_id": "pred-0002"}},
    {"kind": "design.created", "agent": "designer", "data": {}},
]


class PriceTest(unittest.TestCase):
    def test_ratios_to_opus(self):
        for kind, r in cost.price_ratios("claude-sonnet-5-5").items():
            self.assertAlmostEqual(r, 0.5, msg=kind)
        haiku = cost.price_ratios("claude-haiku-5-5")
        for kind in ("input", "output", "cache_write"):
            self.assertAlmostEqual(haiku[kind], 0.025, msg=kind)
        self.assertAlmostEqual(haiku["cache_read"], 0.05)  # twice the others: noted in price_ratio
        above = cost.price_ratios("claude-haiku-5-5", above_100k=True)
        for kind in ("input", "output", "cache_write"):
            self.assertAlmostEqual(above[kind], 0.125, msg=kind)
        self.assertEqual(cost.ratio_range("claude-opus-5-5"), (1.0, 1.0))
        self.assertEqual(tuple(round(r, 6) for r in cost.ratio_range("claude-sonnet-5-5")), (0.5, 0.5))
        low, high = cost.ratio_range("claude-haiku-5-5")
        self.assertAlmostEqual(low, 0.025)  # input, output, cache write up to 100K
        self.assertAlmostEqual(high, 0.25)  # cache read above 100K: 0.05 / 0.20
        self.assertAlmostEqual(cost.price_ratio("claude-opus-5-5"), 1.0)
        self.assertAlmostEqual(cost.price_ratio("claude-sonnet-5-5", above_100k=True), 0.5)  # no such price: the same
        with self.assertRaises(KeyError):
            cost.price_ratio("claude-unknown")

    def test_every_mix_model_has_a_price(self):
        from hiraku.catalog.mixes import load_mixes

        for name, mix in load_mixes().items():
            for role, spec in mix.items():
                self.assertIn(spec["model"], cost.PRICES, (name, role))


class EstimateTest(unittest.TestCase):
    def test_calls_by_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_folder(tmp, EVENTS)
            self.assertEqual(dict(cost.record_calls_by_agent(tmp)), {"cae.strength": 3, "packager": 1})
            self.assertEqual(cost.record_calls(tmp), 4)

    def test_per_mix(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_folder(tmp, EVENTS)
            default = cost.estimate_mix(tmp, "default")
            self.assertAlmostEqual(default["usd"], cost.estimate_usd(tmp))
            self.assertAlmostEqual(default["upper_usd"], default["usd"])
            for name in ("cae-sonnet", "lead-sonnet"):  # the lead's own tokens are not separated
                e = cost.estimate_mix(tmp, name)
                self.assertAlmostEqual(e["usd"], 4 * PER_CALL * 0.5)
                self.assertAlmostEqual(e["upper_usd"], e["usd"])
            haiku = cost.estimate_mix(tmp, "cae-haiku")
            self.assertAlmostEqual(haiku["usd"], 4 * PER_CALL * 0.025)
            self.assertAlmostEqual(haiku["upper_usd"], 4 * PER_CALL * 0.25)
            self.assertEqual(list(cost.estimates(tmp)), mix_names())
            self.assertEqual(cost.estimates(tmp)["default"]["usd"], round(4 * PER_CALL, 2))

    def test_the_calibration_run_counts(self):
        """The calibration run's 5 calls were cae.strength's (its events), so its default estimate is its cost."""
        events = [{"kind": k, "agent": "cae.strength", "data": {f: f"{f}-{i}"}}
                  for i, (k, f) in enumerate([("prediction.recorded", "prediction_id")] * 2
                                             + [("analysis.started", "analysis_id")]
                                             + [("comparison.recorded", "comparison_id")] * 2)]
        with tempfile.TemporaryDirectory() as tmp:
            run_folder(tmp, events)
            self.assertAlmostEqual(cost.estimate_mix(tmp, "default")["usd"], cost.CALIBRATION_USD)


class DemoRunEstimatesTest(unittest.TestCase):
    def test_estimates_per_mix(self):
        for r in read_data(CATALOG_DIR / "demo_runs.yaml")["runs"]:
            with self.subTest(run=r["id"]):
                self.assertNotIn("estimate_usd", r)  # replaced by estimates_usd
                if "file" not in r:
                    continue
                est = r["estimates_usd"]
                if est is None:
                    continue
                self.assertEqual(list(est), mix_names())
                for name, e in est.items():
                    self.assertEqual(set(e), {"usd", "upper_usd"}, name)
                    self.assertGreaterEqual(e["upper_usd"], e["usd"], name)
                    self.assertLessEqual(e["usd"], est["default"]["usd"], name)


if __name__ == "__main__":
    unittest.main()

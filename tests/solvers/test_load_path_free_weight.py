"""R8 inertia: a free part's own weight has no path to the frame, so the tool names it in unsupported_load (the
judge makes that a failing verdict) and says the stresses leave it out."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
V1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))


class FreeWeightTest(unittest.TestCase):
    def test_free_parts_with_mass_are_unsupported_under_inertia(self):
        from hiraku.catalog.builders import BUILDERS
        from hiraku.solvers.shelf import LoadPath
        from tests.solvers.test_builders_load_path import REQ, SPECS, results

        lc = next(c for c in BUILDERS["R8"](SPECS["R8"], REQ, V1) if c["load_case_id"] == "R8-stress")
        rec = LoadPath().run(V1, lc, [{"prediction_id": "pred-0001"}], {})
        r = results(rec)
        free = int(r["free_parts"]["value"])
        self.assertGreater(free, 0)  # version 1 has no part mounts
        parts = {p["id"]: p for p in V1["parts"]}
        self.assertIn("unsupported_load", r)
        named = [w.strip() for w in (r["unsupported_load"]["where"] or "").split("(")[0].split(",") if w.strip()]
        self.assertTrue(all(parts[w]["mass_kg"] > 0 for w in named))
        self.assertLessEqual(r["unsupported_load"]["value"], free)
        self.assertTrue(any("浮いた部品の荷重は載っていない" in str(e.get("name")) for e in rec["effects"]))

    def test_no_free_parts_no_unsupported_load(self):
        from hiraku.catalog.builders import BUILDERS
        from hiraku.designgen import ladder_4w
        from hiraku.solvers.shelf import LoadPath
        from tests.solvers.test_builders_load_path import REQ, SPECS, results

        d = ladder_4w.build(ladder_4w.apply_choices({"part_mounts": "brackets", "pedal_mount": "post",
                                                     "side_guard": "bars_net"}, {}))
        lc = next(c for c in BUILDERS["R8"](SPECS["R8"], REQ, d) if c["load_case_id"] == "R8-stress")
        r = results(LoadPath().run(d, lc, [{"prediction_id": "pred-0001"}], {}))
        self.assertEqual(r["free_parts"]["value"], 0.0)
        self.assertNotIn("unsupported_load", r)


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from pathlib import Path

from hiraku import contract, solvers
from hiraku.solvers.k5_sensitivity import get_value, sensitivity, strongest, with_value
from hiraku.solvers.shelf import fixtures

ROOT = Path(__file__).resolve().parents[2]
DESIGN = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))


def _one_steel():
    """The demo vehicle without the roll bar (its tubes are another steel): one material, so scaling its E
    scales every stiffness alike."""
    from hiraku.designgen import ladder_4w
    return ladder_4w.build({"rollbar": {"value": 0.0, "unit": "1"}})
TOOLS = {t.name: t for t in solvers.builtin_tools()}
PRED = [{"prediction_id": "pred-0001"}]


def q(v, u):
    return {"value": v, "unit": u}


class PathTest(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(get_value(DESIGN, "sections.rt40x80x2.3.dims_mm.t"), (2.3, "mm"))
        self.assertEqual(get_value(DESIGN, "variables.motor_peak_power"), (10.0, "kW"))
        self.assertEqual(get_value(DESIGN, "parts.cargo.mass_kg"), (150.0, "kg"))
        # the cargo stands on the deck, a 3.2 mm sheet since 318ea65 (the floors' bottoms stay on the rails, so
        # what stands on them is 16.8 mm lower than with the thicker boxes before)
        self.assertEqual(get_value(DESIGN, "parts.cargo.box.center_mm.2")[0], 583.2)
        d2 = with_value(DESIGN, "parts.cargo.mass_kg", 100.0)
        self.assertEqual(get_value(d2, "parts.cargo.mass_kg")[0], 100.0)
        self.assertEqual(get_value(DESIGN, "parts.cargo.mass_kg")[0], 150.0)  # the original is untouched
        with self.assertRaises(KeyError):
            get_value(DESIGN, "parts.ghost.mass_kg")


class SensitivityTest(unittest.TestCase):
    def test_cantilever_known_elasticities(self):
        """Tip load P on a cantilever: deflection ~ P L^3 / (E I): elasticities 1 (P... through the load), -1 for E."""
        lad = fixtures.ladder_design(payload_kg=200.0)
        lc = {"load_case_id": "R1", "test_id": "R1", "domain": "strength", "title": "x", "params": {}, "tolerance_rel": 0.15}
        t = sensitivity(TOOLS["frame_static"], lad, lc, "max_displacement",
                        ["materials.st.E_MPa", "parts.payload.mass_kg"], rel_step=0.01)
        e = {x["name"].split(" to ")[1]: x.get("share") for x in t}
        self.assertAlmostEqual(e["materials.st.E_MPa"], -1.0, delta=1e-3)  # deflection ~ 1/E
        self.assertGreater(e["parts.payload.mass_kg"], 0.5)  # most of the load is the payload

    def test_through_run_params(self):
        lc = {"load_case_id": "R1", "test_id": "R1", "domain": "strength", "title": "x",
              "params": {"step_height": q(100, "mm")}, "tolerance_rel": 0.15}
        rec = TOOLS["frame_static"].run(_one_steel(), lc, PRED, {"sensitivity": {"quantity": "max_stress", "paths": [
            "sections.rt40x80x2.3.dims_mm.h", "materials.stkr400.E_MPa"]}})
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        sens = [x for x in rec["effects"] if x["name"].startswith("max_stress to")]
        self.assertEqual(len(sens), 2)
        self.assertLess(sens[0]["share"], -1.0)  # taller rail: less bending stress
        self.assertEqual(sens[1]["share"], 0.0)  # stiffness scale does not change these stresses
        self.assertIs(strongest(sens), sens[0])

    def test_bad_request_is_reported(self):
        lc = {"load_case_id": "R1", "test_id": "R1", "domain": "strength", "title": "x", "params": {}, "tolerance_rel": 0.15}
        rec = TOOLS["frame_static"].run(DESIGN, lc, PRED, {"sensitivity": {"quantity": "nothing", "paths": ["parts.cargo.mass_kg"]}})
        self.assertEqual(rec["status"], "ok")
        self.assertIn("not computed", rec["effects"][-1]["name"])


if __name__ == "__main__":
    unittest.main()

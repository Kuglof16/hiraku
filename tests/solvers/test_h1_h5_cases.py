"""H1 split into H1-interference, H1-motion, H1-joints (shown, not judged), H1-envelope (designs with modules) and
H1-steer; H5-order on blocked_parts. The K0 counts against the assembly tool
on generated designs, and load_case.modules."""

from __future__ import annotations

import copy
import unittest

from hiraku import contract
from hiraku.catalog.builders import BUILDERS, PLANS
from hiraku.designgen import ladder_4w as GEN
from hiraku.solvers import formula_registry
from hiraku.solvers.shelf.assembly import Assembly
from tests.solvers.test_builders_load_path import REQ

SPEC = {"domain": "assembly", "tolerance_rel": 0.05}
FORMULAS = formula_registry()


def predict(design, lc, quantity=None) -> float | None:
    plan = PLANS.get(lc["load_case_id"])
    if plan is None:
        return None
    out = {}
    for p in plan["predictions"]:
        given = p["inputs_from"](design, lc) if "inputs_from" in p else given
        ins = {}
        for k, ref in p["inputs"].items():
            src, _, name = ref.partition(".")
            ins[k] = given[name] if src == "from" else (
                {"value": out[name], "unit": "deg"} if src == "pred" else design["variables"][ref.rsplit(".", 1)[1]])
        out[p["quantity"]] = FORMULAS.evaluate(p["formula_id"], ins)["outputs"][p["output"]]["value"]
    return out[quantity or lc.get("criterion", {}).get("quantity") or plan["predictions"][-1]["quantity"]]


def cases(design):
    return {lc["load_case_id"]: lc for t in ("H1", "H5") for lc in BUILDERS[t](SPEC, REQ, design)}


class CasesTest(unittest.TestCase):
    def test_ids_and_modules(self):
        d = GEN.build(GEN.apply_choices({"part_mounts": "brackets", "pedal_mount": "post"}, {}))
        got = cases(d)
        self.assertEqual(sorted(got), ["H1-envelope", "H1-interference", "H1-joints", "H1-motion", "H1-steer",
                                       "H5-order"])
        for lc in got.values():
            self.assertEqual(contract.errors("load_case", lc), [], lc["load_case_id"])
        self.assertEqual(got["H1-interference"]["modules"], ["package"])
        self.assertEqual(got["H5-order"]["criterion"]["quantity"], "blocked_parts")
        self.assertNotIn("criterion", got["H1-joints"])  # shown, not judged, while the parts are not joined in shape
        self.assertIn("判定なし", got["H1-joints"]["title"])

    def test_no_envelope_case_without_modules(self):
        d = GEN.build()
        d.pop("modules")
        got = cases(d)
        self.assertNotIn("H1-envelope", got)
        self.assertTrue(all("modules" not in lc for lc in got.values()))


class K0AgainstToolTest(unittest.TestCase):
    """The hand counts equal the tool's on version 1 and with gussets (H1-joints within 5 %); a design with the
    battery moved onto the frame's gussets (as run 20261104-175910's version 5) is red."""

    @classmethod
    def setUpClass(cls):
        d1 = GEN.build()
        d2 = GEN.build(GEN.apply_choices({"part_mounts": "brackets", "pedal_mount": "post", "joints": "gusset"}, {}))
        bat = next(p for p in d2["parts"] if p["id"] == "battery")
        g = next(p for p in d2["parts"] if p["kind"] == "gusset")
        moved = copy.deepcopy(d2)
        b2 = next(p for p in moved["parts"] if p["id"] == "battery")
        b2["box"]["center_mm"] = [g["box"]["center_mm"][0], 0.0, bat["box"]["center_mm"][2]]
        cls.designs = {"v1": d1, "gussets": d2, "battery on a gusset": moved}
        tool = Assembly()
        cls.got = {}
        for name, d in cls.designs.items():
            cs = cases(d)
            rec = tool.run(d, cs["H1-interference"], [{"prediction_id": "pred-0001"}], {})
            res = {r["quantity"]: r["value"] for r in rec["results"]}
            cls.got[name] = (cs, res)

    def test_counts(self):
        for name, d in self.designs.items():
            if name == "battery on a gusset":
                continue  # it also meets turned parts and slanted members, which the hand count leaves out
            cs, res = self.got[name]
            for lc_id in ("H1-interference", "H1-motion", "H1-envelope", "H5-order"):
                with self.subTest(design=name, case=lc_id):
                    q = cs[lc_id]["criterion"]["quantity"]
                    self.assertEqual(predict(d, cs[lc_id]), res[q])
            with self.subTest(design=name, case="H1-joints"):
                q = "joint_gap_count"
                self.assertLessEqual(abs(predict(d, cs["H1-joints"]) - res[q]), 0.05 * res[q])

    def test_battery_on_the_gussets_is_red(self):
        cs, res = self.got["battery on a gusset"]
        self.assertGreater(res["interference_count"], 0.0)
        self.assertGreater(predict(self.designs["battery on a gusset"], cs["H1-interference"]), 0.0)
        self.assertEqual(self.got["v1"][1]["interference_count"], 0.0)


if __name__ == "__main__":
    unittest.main()

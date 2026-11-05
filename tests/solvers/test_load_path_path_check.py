"""The path check of a load case (docs/contract.md 手計算の役目, contract 2fc1006): load_path.free_parts over the
parts the load case loads (PATH_PREDICTION, quantity unsupported_load), and the tools' own unsupported_load,
given at 0 as well."""

import copy
import unittest

from hiraku import solvers
from hiraku.catalog.builders import BUILDERS
from hiraku.catalog.builders import load_path as B
from hiraku.designgen import ladder_4w
from hiraku.solvers.shelf import LoadPath
from tests.solvers.test_builders_load_path import REQ, SPECS, results


def predict(design, lc):
    ins = B.path_inputs(design, lc)
    return solvers.formula_registry().evaluate("load_path.free_parts", ins)["outputs"]["n"]["value"]


class PathCheckTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = ladder_4w.build(ladder_4w.apply_choices({"part_mounts": "brackets", "pedal_mount": "post",
                                                         "side_guard": "bars_net"}, {}))
        cls.cases = {c["load_case_id"]: c for tid in ("R7", "R8") for c in BUILDERS[tid](SPECS[tid], REQ, cls.d)}

    def test_loaded_parts(self):
        hand = next(c for k, c in self.cases.items() if c["params"].get("load_kind") == "hand")
        foot = next(c for k, c in self.cases.items() if c["params"].get("load_kind") == "foot")
        self.assertEqual(B.loaded_parts(self.d, hand), [hand["params"]["hand_part"]])
        self.assertEqual(B.loaded_parts(self.d, foot), foot["params"]["foot_parts"].split(","))
        every = B.loaded_parts(self.d, self.cases["R8-stress"])
        self.assertIn(hand["params"]["hand_part"], every)
        self.assertFalse(any(p["kind"] in ("occupant", "cargo") for p in self.d["parts"] if p["id"] in every))

    def test_held_design_predicts_and_gives_zero(self):
        for k, lc in self.cases.items():
            self.assertEqual(predict(self.d, lc), 0.0, k)
            r = results(LoadPath().run(self.d, lc, [{"prediction_id": "pred-0001"}], {}))
            self.assertEqual(r["unsupported_load"]["value"], 0.0, k)

    def test_a_floating_pedal_predicted_as_the_tool_counts(self):
        # a pedal lifted 2 m clear of everything: the foot case's prediction is the tool's count, 1
        d = copy.deepcopy(self.d)
        foot = next(c for c in self.cases.values() if c["params"].get("load_kind") == "foot")
        pid = foot["params"]["foot_parts"].split(",")[0]
        part = next(p for p in d["parts"] if p["id"] == pid)
        part["box"]["center_mm"][2] += 2000.0
        r = results(LoadPath().run(d, foot, [{"prediction_id": "pred-0001"}], {}))
        self.assertEqual(r["unsupported_load"]["value"], 1.0)
        self.assertEqual(predict(d, foot), 1.0)
        hand = next(c for c in self.cases.values() if c["params"].get("load_kind") == "hand")
        self.assertEqual(predict(d, hand), 0.0)  # the hand's path does not go through the pedal

    def test_frame_tools_declare_and_give_zero(self):
        from hiraku.solvers.shelf.frame_static import lost_load_outputs

        res, said = lost_load_outputs()
        self.assertEqual((res["quantity"], res["value"]), ("unsupported_load", 0.0))
        tools = {t.name: t for t in solvers.builtin_tools()}
        for name in ("frame_static", "frame_shell", "frame_tubes", "load_path"):
            t = tools[name]
            self.assertIn("load_path.free_parts", t.formulas, name)
            self.assertIn("unsupported_load", [p.quantity for p in t.predicts], name)


if __name__ == "__main__":
    unittest.main()

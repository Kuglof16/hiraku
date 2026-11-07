"""The CAD tools on a temporary run: export_step, import_step, edit_step and the path check."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract
from hiraku.cad.step_read import read_step
from hiraku.contract.protocols import ToolError
from hiraku.store import RunStore
from hiraku.tools.cad_tools import TOOLS
from hiraku.tools.context import RunContext
from tests.cad.test_step_write import DESIGN


def call(name, ctx, **args):
    return TOOLS[name]["function"](ctx, args, "lead")


class CadToolsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = json.loads(DESIGN.read_text(encoding="utf-8"))

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        fields = {"want": "四輪の車", "product": "offroad-4w", "flags": {}, "speed_class": "static", "round_limit": 1,
                  "cost_limit_usd": 1.0, "requirements": [], "domain_picks": [], "load_cases": [], "agents": []}
        self.store = RunStore.create(fields, root=self.root, slug="cad-test")
        self.store.write_design(self.design)
        self.ctx = RunContext(store=self.store, formulas=None, tools={})
        self.run = Path(self.store.path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_tools_are_registered_with_the_contract_schemas(self):
        from hiraku.tools import mcp

        for name in ("export_step", "import_step", "edit_step"):
            self.assertIn(name, contract.MCP_TOOLS_CAD)
            self.assertIn(name, mcp.RUN_TOOLS)  # in the run: the designers use them (roles.DESIGNER_TOOLS)
            self.assertIs(mcp.HANDLERS[name], TOOLS[name]["function"])
            self.assertEqual(mcp.INPUT_SCHEMAS[name], TOOLS[name]["input_schema"])
            self.assertFalse(TOOLS[name]["input_schema"]["additionalProperties"])

    def test_export_step(self):
        out = call("export_step", self.ctx)
        self.assertEqual(out["path"], "cad/design_v1.step")
        members, parts = len(self.design["members"]), len(self.design["parts"])
        self.assertEqual((out["members"], out["parts"], out["solids"]), (members, parts, members + parts))
        solids = read_step((self.run / out["path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(solids), members + parts)
        self.assertEqual(out["bytes"], (self.run / out["path"]).stat().st_size)
        named = call("export_step", self.ctx, design_version=1, path="cad/mine.stp")
        self.assertEqual(named["path"], "cad/mine.stp")
        with self.assertRaises(ToolError):  # never over a file that is there
            call("export_step", self.ctx, path="cad/mine.stp")
        with self.assertRaises(ToolError):
            call("export_step", self.ctx, design_version=2)

    def test_import_step(self):
        rel = call("export_step", self.ctx)["path"]
        out = call("import_step", self.ctx, path=rel, density_kg_m3=7850)
        d = out["design"]
        members, solids = len(self.design["members"]), len(self.design["members"]) + len(self.design["parts"])
        self.assertEqual(out["counts"]["solids"], solids)
        self.assertEqual(sorted(m["id"] for m in d["members"]), sorted(m["id"] for m in self.design["members"]))
        self.assertEqual(len(out["solids"]), solids)
        self.assertEqual(sum(r["became"] == "member" for r in out["solids"]), members)
        parts = {p["id"]: p for p in d["parts"]}
        for r in out["solids"]:
            if r["became"] == "part":
                self.assertEqual(parts[r["part"]]["name"], r["solid"])
        # mass = volume x density, from the code
        p = d["parts"][0]
        self.assertAlmostEqual(p["mass_kg"], out["part_basis"][p["id"]]["volume_mm3"] * 1e-9 * 7850, places=9)
        self.assertEqual(self.store.design_version, 1)  # no version made
        self.assertTrue(any("supports" in n for n in out["needed"]))
        no_rho = call("import_step", self.ctx, path=rel)
        self.assertTrue(all(q["mass_kg"] is None for q in no_rho["design"]["parts"]))

    def test_edit_step(self):
        rel = call("export_step", self.ctx)["path"]
        before = {s.name: s for s in read_step((self.run / rel).read_text(encoding="utf-8"))}
        out = call("edit_step", self.ctx, path=rel, edits=[
            {"op": "move", "name": "rail_L_FA", "by_mm": [10, 0, 0]},
            {"op": "delete", "name": "battery"},
            {"op": "add_box", "name": "new_box", "center_mm": [0, 0, 500], "size_mm": [100, 50, 20]}])
        self.assertEqual(out["out_path"], "cad/design_v1_edit1.step")
        self.assertEqual(out["solids"], len(before))  # one deleted, one added
        self.assertEqual([r["op"] for r in out["log"] if "edit" in r], ["move", "delete", "add_box"])
        after = {s.name: s for s in read_step((self.run / out["out_path"]).read_text(encoding="utf-8"))}
        moved = np.array(after["rail_L_FA"].center_mm) - np.array(before["rail_L_FA"].center_mm)
        self.assertTrue(np.allclose(moved, [10, 0, 0], atol=1e-6), moved)
        self.assertNotIn("battery", after)
        self.assertAlmostEqual(after["new_box"].volume_mm3, 100 * 50 * 20, delta=1e-6 * 100 * 50 * 20)
        self.assertIn("battery", {s.name for s in read_step((self.run / rel).read_text(encoding="utf-8"))})  # the source stays
        again = call("edit_step", self.ctx, path=rel, edits=[{"op": "delete", "name": "battery"}])
        self.assertEqual(again["out_path"], "cad/design_v1_edit2.step")
        with self.assertRaises(ToolError) as e:
            call("edit_step", self.ctx, path=rel, edits=[{"op": "move", "name": "nothing", "by_mm": [1, 0, 0]}])
        self.assertIn("nothing", str(e.exception))

    def test_paths_stay_inside_the_run(self):
        call("export_step", self.ctx)
        outside = self.root / "outside.step"
        outside.write_text("x", encoding="utf-8")
        for bad in ("../outside.step", str(outside), "cad/../../outside.step", "project.json", "events.jsonl",
                    "cad", "", "cad/design_v1.step/../../../outside.step"):
            with self.subTest(path=bad):
                with self.assertRaises(ToolError):
                    call("import_step", self.ctx, path=bad)
                with self.assertRaises(ToolError):
                    call("export_step", self.ctx, path=bad)
                with self.assertRaises(ToolError):
                    call("edit_step", self.ctx, path="cad/design_v1.step", out_path=bad,
                         edits=[{"op": "delete", "name": "battery"}])
        self.assertEqual(outside.read_text(encoding="utf-8"), "x")


if __name__ == "__main__":
    unittest.main()

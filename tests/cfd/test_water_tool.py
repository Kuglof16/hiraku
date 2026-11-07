"""The flooded-road tool on a temporary run: the no-GPU answer, the limits, a short coarse pass on the GPU, its record and its replay.
The statement stays on everything it writes. The way to a verdict is in tests/cfd/test_flow_verdict.py."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from hiraku.cfd import cuda, water, water_coupled, water_record
from hiraku.contract.protocols import ToolError
from hiraku.store import RunStore
from hiraku.tools import cfd_tools
from hiraku.tools.context import RunContext

DESIGN = Path(__file__).resolve().parents[1] / "verification" / "fixtures" / "n15-v005-design.json"


def fields() -> dict:
    return {"want": "test", "product": "offroad-4w", "flags": {}, "speed_class": "static", "round_limit": 1, "cost_limit_usd": 1.0, "requirements": [], "domain_picks": [], "load_cases": [], "agents": []}


class ToolBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        store = RunStore.create(fields(), root=Path(self.tmp.name), slug="water")
        design = json.loads(DESIGN.read_text(encoding="utf-8"))
        design["design_version"] = 1
        store.write_design(design)
        self.ctx = RunContext(store=store, formulas=None, tools={})


class WithoutGpu(ToolBase):
    def test_says_there_is_no_gpu_and_does_not_run(self) -> None:
        with mock.patch.object(cuda, "gpu_or_reason", return_value=(None, "CUDA の使える GPU がありません")):
            with self.assertRaises(ToolError) as e:
                cfd_tools.cfd_water(self.ctx, {})
        self.assertIn("GPU が無いので", str(e.exception))
        self.assertIn("replay", str(e.exception))
        self.assertFalse((Path(self.ctx.store.path) / "cfd").exists())

    def test_limits(self) -> None:
        for bad in ({"dx_mm": 10.0}, {"depth_mm": 900.0}, {"speed_kmh": 200.0}, {"frame_hz": 500.0}):
            with self.assertRaises(ToolError):
                cfd_tools.cfd_water(self.ctx, bad)

    def test_replay_with_nothing_stored(self) -> None:
        with self.assertRaises(ToolError) as e:
            cfd_tools.cfd_water(self.ctx, {"replay": True})
        self.assertIn("まだありません", str(e.exception))

    def test_replay_refuses_an_aero_record_and_a_path_outside(self) -> None:
        folder = Path(self.ctx.store.path) / "cfd" / "v1" / "x"
        folder.mkdir(parents=True)
        (folder / "aero_record.json").write_text(json.dumps({"kind": "cfd_aero_record"}), encoding="utf-8")
        for path in ("cfd/v1/x/aero_record.json", "../x.json"):
            with self.assertRaises(ToolError):
                cfd_tools.cfd_water(self.ctx, {"replay": path})

    def test_registered_with_a_schema(self) -> None:
        tool = cfd_tools.TOOLS["cfd_water"]
        self.assertIs(tool["function"], cfd_tools.cfd_water)
        self.assertIn("F5", tool["description"])
        self.assertIn("実験と比べて確かめていない", tool["description"])
        self.assertIn("vehicle_moves", tool["input_schema"]["properties"])


class Roles(unittest.TestCase):
    def test_in_the_run_and_only_the_aero_role_has_it(self) -> None:
        from hiraku import contract
        from hiraku.agent import roles
        from hiraku.tools.functions import RUN_TOOLS

        self.assertIn("cfd_water", RUN_TOOLS)
        self.assertIn("cfd_water", roles.cae_tools("aero"))
        for domain in contract.DOMAINS:
            if domain != "aero":
                self.assertNotIn("cfd_water", roles.cae_tools(domain), domain)
        self.assertNotIn("cfd_water", roles.PACKAGER_TOOLS)
        self.assertNotIn("cfd_water", roles.DESIGNER_TOOLS)

    def test_the_aero_role_is_told_it_is_not_a_judgement(self) -> None:
        from hiraku.agent import prompts

        text = prompts.cae_prompt("aero")
        for word in ("cfd_water", "judge", "F5", "電池、モーター、制御器に水が届くかだけを見る", "実験と比べていない", "record_cause", "GPU が無い"):
            self.assertIn(word, text)
        self.assertNotIn("cfd_water", prompts.cae_prompt("nvh"))

    def test_the_plugins_aero_agent_has_the_tool(self) -> None:
        agents = Path(__file__).resolve().parents[2] / "plugins" / "hiraku" / "agents"
        self.assertIn("cfd_water", (agents / "cae-aero.md").read_text(encoding="utf-8").split("---")[1])
        self.assertNotIn("cfd_water", (agents / "cae-nvh.md").read_text(encoding="utf-8"))

    def test_the_schema_here_is_the_contracts(self) -> None:
        from hiraku import contract

        here = dict(cfd_tools.WATER_INPUT_SCHEMA)
        self.assertEqual(json.loads(json.dumps(here)), contract.schema()["$defs"]["tool_input.cfd_water"])


class VehicleSummary(unittest.TestCase):
    def test_against_the_road(self) -> None:
        def record(t: float, z: float, pitch: float, front_ground: float, tyre: float) -> dict:
            return {"t": t, "pose": [0.0, z, pitch], "vehicle": {"speed_ms": 5.0 - t, "ground_m": [front_ground, front_ground, 0.0, 0.0], "wheel_centres": [[-1.0, -0.6, 0.3], [-1.0, 0.6, 0.3], [1.0, -0.6, 0.3], [1.0, 0.6, 0.3]],
                                                                  "tyre_n": [tyre, tyre, 2000.0, 2000.0], "travel_m": [-0.05, -0.05, 0.01, 0.01], "roll_deg": 0.1, "yaw_deg": -0.2}}
        s = water_coupled.vehicle_summary([record(0.0, 0.0, 0.0, 0.0, 1000.0), record(1.0, 0.04, 0.05, 0.0, 0.0), record(2.0, 0.1, 0.1, 0.2, 1000.0)])  # at 2 s a ramp: no pitch against the road
        self.assertAlmostEqual(s["pitch_deg_nose_up_against_road"]["max"], 2.8648, places=3)
        self.assertAlmostEqual(s["pitch_deg_nose_up_against_road"]["max_at_s"], 1.0)
        self.assertAlmostEqual(s["speed_kmh"]["lowest"], 10.8)
        self.assertEqual(s["front_wheels_off_road_s"], [1.0, 1.0])
        self.assertAlmostEqual(s["yaw_deg_largest"], 0.2)
        self.assertEqual(s["suspension_travel_mm"]["front"], [-50.0, -50.0])


class OnGpu(ToolBase):
    def test_short_coarse_pass_then_replay(self) -> None:
        with cuda.gpu_turn(1800.0):
            gpu, reason = cuda.gpu_or_reason("water")
        if gpu is None:
            self.skipTest(reason)
        gpu.close()
        with mock.patch.object(cfd_tools, "WATER_TIME_LIMIT_S", 0.5):
            out = cfd_tools.cfd_water(self.ctx, {"dx_mm": 60.0})
        self.assertEqual(out["statement"], water.STATEMENT)
        self.assertFalse(out["physically_validated"])
        self.assertTrue(out["vehicle_moves"])
        self.assertIsNone(out["faulted"])
        self.assertLess(out["vehicle"]["speed_kmh"]["end"], out["vehicle"]["speed_kmh"]["start"] + 1.0)
        self.assertLess(max(out["water"]["momentum_residual_over_gross"]), 1e-3)
        run = Path(self.ctx.store.path)
        self.assertEqual(set(out["protected_parts"]), {"battery", "motor", "controller"})
        folder = (run / out["path"]).parent
        self.assertTrue((folder / "replay.npz").is_file())
        self.assertFalse((folder / "frames").exists())  # the particles are not kept unless asked
        again = cfd_tools.cfd_water(self.ctx, {"replay": True})
        self.assertTrue(again["replayed"])
        self.assertEqual(again["vehicle"], out["vehicle"])
        with mock.patch.object(cuda, "gpu_or_reason", return_value=(None, "no GPU")):
            self.assertEqual(cfd_tools.cfd_water(self.ctx, {"replay": out["path"]})["path"], out["path"])
        self.assertEqual(water_record.load_record(run / out["path"])["tool"], "cfd_water")


if __name__ == "__main__":
    unittest.main()

"""The flow tool on a temporary run, with the GPU and the solver stood in for: the no-GPU answer, the memory refusal, a
sweep's files, the replay, the path check and the registration."""

import importlib.util
import json
import math
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import jsonschema

from hiraku import contract
from hiraku.cfd import cuda, record, solver, uncertainty, vehicle
from hiraku.contract.protocols import ToolError
from hiraku.store import RunStore
from hiraku.tools import cfd_tools
from hiraku.tools.context import RunContext
from tests.cfd.test_aero_map import DESIGN_FILE, V, fake_run

HAS_SDK = importlib.util.find_spec("claude_agent_sdk") is not None

F1_CASE = {"load_case_id": "F1-wind30", "test_id": "F1", "domain": "aero", "title": "横風",
           "params": {"loading": "two_cargo", "wind_speed": {"value": 30.0, "unit": "m/s"}, "speed": {"value": 40.0, "unit": "km/h"}, "wind_from": "left"},
           "requirement_keys": ["Q18", "Q50"], "tolerance_rel": 0.1,
           "criterion": {"quantity": "critical_wind_speed", "op": ">=", "limit": {"value": 30.0, "unit": "m/s"}, "basis": "Q18"}}
P4_CASE = {"load_case_id": "P4-20km", "test_id": "P4", "domain": "powertrain", "title": "周回",
           "params": {"distance": {"value": 20.0, "unit": "km"}, "grade_low": {"value": 10.0, "unit": "%"}, "grade_high": {"value": 20.0, "unit": "%"},
                      "speed_low": {"value": 20.0, "unit": "km/h"}, "speed_high": {"value": 40.0, "unit": "km/h"}, "soc_start": {"value": 100.0, "unit": "%"},
                      "loading": "two_cargo"},
           "requirement_keys": ["Q54", "Q50"], "tolerance_rel": 0.1,
           "criterion": {"quantity": "soc_end", "op": ">", "limit": {"value": 0.0, "unit": "%"}, "basis": "P4"}}


class FakeGpu:
    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@contextmanager
def turn(wait_s=0.0):
    yield


def runner(calls):
    def run(gpu, design, yaw_deg=0.0, speed_ms=V, dx=0.08, reference_area_m2=None, loading=None, air_speed_ms=None, domain=None, **kw):
        calls.append((yaw_deg, round(speed_ms, 6), round(dx, 6), loading, air_speed_ms, domain.half_width))
        return fake_run(yaw_deg, speed=speed_ms, dx=dx, air=air_speed_ms)
    return run


def call(ctx, **args):
    return cfd_tools.TOOLS["cfd_aero"]["function"](ctx, args, "lead")


class CfdToolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = json.loads(DESIGN_FILE.read_text(encoding="utf-8"))

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        fields = {"want": "四輪の車", "product": "offroad-4w", "flags": {}, "speed_class": "static", "round_limit": 1, "cost_limit_usd": 1.0,
                  "requirements": [], "domain_picks": [], "load_cases": [F1_CASE, P4_CASE], "agents": []}
        self.store = RunStore.create(fields, root=Path(self._tmp.name), slug="cfd-test")
        self.store.write_design(self.design)
        self.ctx = RunContext(store=self.store, formulas=None, tools={})
        self.run_dir = Path(self.store.path)
        self.calls = []

    def tearDown(self):
        self._tmp.cleanup()

    @contextmanager
    def gpu_available(self, free_mib=11000):
        """A GPU that is there, the lock free, this much memory free, and made-up flow runs."""
        with mock.patch.object(cuda, "gpu_or_reason", return_value=(FakeGpu(), "")), mock.patch.object(cuda, "Gpu", FakeGpu), \
                mock.patch.object(cuda, "gpu_turn", turn), \
                mock.patch.object(solver, "gpu_memory_mib", return_value={"total": 12000, "used": 1000, "free": free_mib}), \
                mock.patch.object(vehicle, "run_vehicle", runner(self.calls)):
            yield

    # ---------------------------------------------------------------- no GPU, no memory
    def test_no_gpu_says_so_and_runs_nothing(self):
        with mock.patch.object(cuda, "gpu_or_reason", return_value=(None, "CUDA の使える GPU がありません")), \
                mock.patch.object(vehicle, "run_vehicle", side_effect=AssertionError("must not run")):
            with self.assertRaises(ToolError) as e:
                call(self.ctx)
        text = str(e.exception)
        self.assertIn("GPU が無いので流れの計算は回しません", text)
        self.assertIn("CUDA の使える GPU がありません", text)
        self.assertIn("CPU では回しません", text)
        self.assertFalse((self.run_dir / "cfd").exists())

    def test_a_pc_without_the_driver_gets_the_same_answer(self):
        with mock.patch.object(cuda, "_load_driver", side_effect=cuda.GpuUnavailable("NVIDIA のドライバの CUDA がありません")):
            with self.assertRaises(ToolError) as e:
                call(self.ctx)
        self.assertIn("GPU が無いので", str(e.exception))
        self.assertIn("NVIDIA のドライバの CUDA がありません", str(e.exception))

    def test_too_little_memory_says_to_wait(self):
        with self.gpu_available(free_mib=100):
            with self.assertRaises(ToolError) as e:
                call(self.ctx)
        text = str(e.exception)
        self.assertIn("GPU の空きが足りません", text)
        self.assertIn("待ってから、もう一度", text)
        self.assertEqual(self.calls, [])

    def test_memory_taken_during_the_sweep_says_to_wait_too(self):
        def full(*a, **k):
            raise solver.MemoryBudgetError("GPU の空きが足りません: 要るのは 300 MiB")

        with self.gpu_available(), mock.patch.object(vehicle, "run_vehicle", full):
            with self.assertRaises(ToolError) as e:
                call(self.ctx)
        self.assertIn("待ってから、もう一度", str(e.exception))

    def test_no_turn_on_the_gpu_says_to_wait(self):
        @contextmanager
        def busy(wait_s=0.0):
            raise TimeoutError("GPU の順番が回ってきませんでした")
            yield

        with self.gpu_available(), mock.patch.object(cuda, "gpu_turn", busy):
            with self.assertRaises(ToolError) as e:
                call(self.ctx)
        self.assertIn("順番", str(e.exception))
        self.assertIn("待ってから", str(e.exception))

    def test_a_gpu_call_that_fails_says_to_wait_not_that_there_is_no_gpu(self):
        def fail(*a, **k):
            raise cuda.CudaError("cuMemAlloc 1000 bytes: CUDA error 2")

        with self.gpu_available(), mock.patch.object(vehicle, "run_vehicle", fail):
            with self.assertRaises(ToolError) as e:
                call(self.ctx)
        self.assertIn("待ってから", str(e.exception))
        self.assertNotIn("GPU が無い", str(e.exception))

    def test_a_flow_that_went_bad_says_what_to_do(self):
        def bad(*a, **k):
            raise RuntimeError("yaw 5.0 dx 0.08: step 1200: 3 nodes are not finite")

        with self.gpu_available(), mock.patch.object(vehicle, "run_vehicle", bad):
            with self.assertRaises(ToolError) as e:
                call(self.ctx)
        self.assertIn("not finite", str(e.exception))
        self.assertIn("格子を変えて", str(e.exception))

    # ---------------------------------------------------------------- inputs
    def test_inputs_it_cannot_use_are_refused_in_words(self):
        no_speed = {**self.design, "variables": {k: v for k, v in self.design["variables"].items() if k != "max_speed"}}
        for args, words in (({"yaws_deg": [90.0]}, "yaws_deg"), ({"dx_mm": 1.0}, "dx_mm"), ({"speed_kmh": -3.0}, "speed_kmh"), ({"design_version": 7}, "v7")):
            with self.subTest(args=args), self.gpu_available():
                with self.assertRaises(ToolError) as e:
                    call(self.ctx, **args)
                self.assertIn(words, str(e.exception))
        with mock.patch.object(self.store, "design", return_value=no_speed), self.gpu_available():
            with self.assertRaises(ToolError) as e:
                call(self.ctx)
        self.assertIn("max_speed", str(e.exception))
        self.assertEqual(self.calls, [])

    # ---------------------------------------------------------------- a sweep
    def test_a_sweep_writes_its_record_and_each_case(self):
        with self.gpu_available():
            out = call(self.ctx)
        self.assertEqual(out["path"], "cfd/v1/40kmh_dx80/aero_record.json")
        self.assertIs(out["replayed"], False)
        self.assertIs(out["physically_validated"], False)
        self.assertEqual(out["design_version"], 1)
        self.assertEqual(out["loading"], "two_cargo")
        self.assertEqual(len(out["table"]), 13)  # the 9 of the sweep and F1's wind on both sides, at rest and moving
        self.assertEqual({c[0] for c in self.calls[:9]}, {0.0, 5.0, -5.0, 10.0, -10.0, 15.0, -15.0, 20.0, -20.0})
        self.assertEqual([(c[0], c[1], c[4], c[5]) for c in self.calls[9:13]],
                         [(90.0, 0.0, 30.0, 9.0), (-90.0, 0.0, 30.0, 9.0), (70.0, round(V, 6), None, 9.0), (-70.0, round(V, 6), None, 9.0)])
        self.assertEqual({c[5] for c in self.calls[:9]}, {4.5})  # the narrower domain up to yaw 20
        self.assertEqual([(c[0], c[2]) for c in self.calls[13:]], [(0.0, 0.12), (90.0, 0.12)])  # F1's wind is from the left
        folder = self.run_dir / "cfd" / "v1" / "40kmh_dx80"
        self.assertTrue((folder / "aero_record.json").is_file())
        self.assertEqual(len([p for p in folder.iterdir() if (p / "case.json").is_file()]), 15)
        self.assertTrue((folder / "rest_wind30_yaw+90_dx80" / "case.json").is_file())
        self.assertEqual((out["k2_point"]["from_yaw_deg"], out["k2_point"]["from_speed_kmh"], out["k2_point"]["wind_from"]), (90.0, 0.0, "left"))
        self.assertIn("止まった車", out["k2_point"]["note"])
        self.assertTrue(any("確かめていない" in a for a in out["assumptions"]))
        self.assertTrue(any("止まった車" in a for a in out["assumptions"]))
        self.assertFalse(out["speed_effect"]["computed"])

    def test_the_hand_row_comes_from_the_design_and_the_load_cases(self):
        with self.gpu_available():
            out = call(self.ctx)
        rows = {(r["test"], r["quantity"]): r for r in reversed(out["uncertainty"]["rows"])}  # the first F1 row last: K2's point
        self.assertEqual(sorted(rows), [("F1", "critical_wind_speed"), ("F2", "drag_force"), ("P4", "energy_used"), ("P4", "range"), ("P4", "soc_end")])
        f1_rows = [r for r in out["uncertainty"]["rows"] if r["test"] == "F1"]
        self.assertEqual([(r["from_yaw_deg"], r["from_speed_kmh"] == 0.0, r["reads_k2_point"]) for r in f1_rows],
                         [(90.0, True, True), (70.0, False, False), (20.0, False, False)])
        f1 = rows[("F1", "critical_wind_speed")]
        v = self.design["variables"]
        from hiraku.catalog.summary import design_summary

        mass = design_summary(self.design)["loadings"]["two_cargo"]["mass"]["value"]
        cy, area, h = v["side_force_coefficient"]["value"], v["side_area"]["value"], v["centre_of_pressure_height"]["value"]
        self.assertAlmostEqual(f1["hand"], uncertainty.critical_wind_speed(mass, 1100.0, cy * area * h, v["air_density"]["value"]))
        self.assertEqual(f1["criterion"]["limit"], 30.0)
        self.assertEqual(f1["verdict_hand"], "OK")
        self.assertEqual(f1["criterion"]["load_case_id"], "F1-wind30")
        self.assertEqual(f1["criterion_yaw_deg"], 90.0)
        self.assertAlmostEqual(f1["gust_yaw_deg"], math.degrees(math.atan2(30.0, 40.0 / 3.6)))
        self.assertFalse(f1["yaw_outside_sweep"])
        self.assertIsNotNone(f1["uncertainty"]["grid_pct"])  # the study looked at the point K2 reads
        f2 = rows[("F2", "drag_force")]
        self.assertAlmostEqual(f2["hand"], 0.5 * v["air_density"]["value"] * v["drag_coefficient"]["value"] * v["frontal_area"]["value"] * (40.0 / 3.6) ** 2)
        self.assertEqual(rows[("P4", "soc_end")]["criterion"]["op"], ">")
        self.assertIn(rows[("P4", "soc_end")]["verdict_hand"], ("OK", "NG"))

    def test_a_design_without_powertrain_numbers_still_gets_its_flow_table(self):
        no_batt = {**self.design, "variables": {k: v for k, v in self.design["variables"].items() if k != "battery_capacity"}}
        with mock.patch.object(self.store, "design", return_value=no_batt), self.gpu_available():
            out = call(self.ctx)
        self.assertNotIn("P4", {r["test"] for r in out["uncertainty"]["rows"]})
        self.assertTrue(any("P4" in n for n in out["uncertainty"]["hand_notes"]))
        self.assertEqual(len(out["table"]), 13)

    def test_a_second_sweep_does_not_write_over_the_first(self):
        with self.gpu_available():
            a = call(self.ctx, yaws_deg=[0.0])
            b = call(self.ctx, yaws_deg=[0.0])
        self.assertEqual((a["path"], b["path"]), ("cfd/v1/40kmh_dx80/aero_record.json", "cfd/v1/40kmh_dx80-2/aero_record.json"))

    def test_arguments_reach_the_runs(self):
        with self.gpu_available():
            out = call(self.ctx, yaws_deg=[0.0, 70.0], speed_kmh=[20.0, 40.0], dx_mm=160.0)
        self.assertEqual(out["path"], "cfd/v1/20kmh_dx160/aero_record.json")
        main = [c for c in self.calls if c[2] == 0.16]
        self.assertEqual(sorted({c[1] for c in main}), [round(20.0 / 3.6, 6), round(40.0 / 3.6, 6)])
        self.assertEqual(len(out["table"]), 4)  # exactly the yaws asked for: no F1 points added
        self.assertTrue(out["speed_effect"]["computed"])
        self.assertEqual(out["k2_point"]["from_yaw_deg"], 70.0)
        self.assertTrue(next(r for r in out["uncertainty"]["rows"] if r["test"] == "F1")["yaw_outside_sweep"])

    def test_at_rest_it_takes_yaw_90_and_a_wind(self):
        with self.gpu_available():
            out = call(self.ctx, yaws_deg=[90.0], speed_kmh=0, wind_ms=25.0, dx_mm=160.0)
        self.assertEqual(out["path"], "cfd/v1/0kmh_dx160/aero_record.json")
        self.assertEqual(self.calls[0], (90.0, 0.0, 0.16, "two_cargo", 25.0, 9.0))
        self.assertEqual(out["table"][0]["air_speed_ms"], 25.0)
        self.assertEqual(out["table"][0]["speed_kmh"], 0.0)
        self.assertAlmostEqual(out["crosswind"][0]["wind_across_ms"], 25.0)
        self.assertEqual(out["drag_yaw0"], [])
        self.calls.clear()
        with self.gpu_available():
            rest = call(self.ctx, speed_kmh=0)  # both sides, the wind from the F1 load case
        self.assertEqual([(c[0], c[4]) for c in self.calls[:2]], [(90.0, 30.0), (-90.0, 30.0)])
        self.assertEqual(rest["k2_point"]["from_yaw_deg"], 90.0)

    def test_yaw_90_moving_or_at_rest_with_no_wind_is_refused(self):
        with self.gpu_available():
            with self.assertRaises(ToolError) as e:
                call(self.ctx, yaws_deg=[90.0])
            self.assertIn("speed 0", str(e.exception))
            without = {"load_cases": []}
            with mock.patch.object(self.store, "version_load_cases", return_value=without["load_cases"]):
                with self.assertRaises(ToolError) as e:
                    call(self.ctx, yaws_deg=[90.0], speed_kmh=0)
            self.assertIn("wind", str(e.exception))
        self.assertEqual(self.calls, [])

    def test_the_memory_check_sees_the_wide_domain(self):
        grids = []
        real = solver.required_bytes

        def spy(grid, vox=None, fields=False):
            grids.append(grid.ny)
            return real(grid, vox, fields)

        with self.gpu_available(), mock.patch.object(solver, "required_bytes", spy):
            call(self.ctx, yaws_deg=[0.0], dx_mm=160.0)
            call(self.ctx, yaws_deg=[90.0], speed_kmh=0, wind_ms=30.0, dx_mm=160.0)
        self.assertEqual(grids, [round(9.0 / 0.16), round(18.0 / 0.16)])

    def test_a_design_with_no_f1_case_runs_the_plain_sweep(self):
        with mock.patch.object(self.store, "version_load_cases", return_value=[]), self.gpu_available():
            out = call(self.ctx)
        self.assertEqual(len(out["table"]), 9)
        self.assertEqual(abs(out["k2_point"]["from_yaw_deg"]), 20.0)  # no side named: the larger rolling moment, here even

    # ---------------------------------------------------------------- replay
    def test_a_replay_needs_no_gpu_and_says_the_same(self):
        with self.gpu_available():
            first = call(self.ctx)
        with mock.patch.object(cuda, "gpu_or_reason", side_effect=AssertionError("no GPU on a replay")), \
                mock.patch.object(cuda, "Gpu", side_effect=AssertionError("no GPU on a replay")), \
                mock.patch.object(cuda, "gpu_turn", side_effect=AssertionError("no GPU on a replay")), \
                mock.patch.object(vehicle, "run_vehicle", side_effect=AssertionError("no run on a replay")):
            newest = call(self.ctx, replay=True)
            named = call(self.ctx, replay=first["path"])
        self.assertIs(newest["replayed"], True)
        self.assertEqual(newest, named)
        strip = lambda d: {k: v for k, v in d.items() if k != "replayed"}
        self.assertEqual(json.loads(json.dumps(strip(first))), strip(newest))

    def test_a_replay_takes_the_newest_of_the_version(self):
        with self.gpu_available():
            call(self.ctx, yaws_deg=[0.0])
            later = call(self.ctx, yaws_deg=[0.0, 5.0])
        self.assertEqual(call(self.ctx, replay=True)["path"], later["path"])

    def test_a_replay_of_nothing_or_of_something_else_is_refused(self):
        with self.assertRaises(ToolError) as e:
            call(self.ctx, replay=True)
        self.assertIn("まだありません", str(e.exception))
        (self.run_dir / "cfd").mkdir()
        (self.run_dir / "cfd" / "not_a_record.json").write_text("{}", encoding="utf-8")
        for bad, words in (("../project.json", "の中の"), ("project.json", "の中の"), ("cfd/missing.json", "ありません"), ("cfd/x.txt", "の中の"),
                           ("cfd/not_a_record.json", "読めませんでした")):
            with self.subTest(bad=bad):
                with self.assertRaises(ToolError) as e:
                    call(self.ctx, replay=bad)
                self.assertIn(words, str(e.exception))

    def test_a_replay_of_another_version_is_refused(self):
        with self.gpu_available():
            first = call(self.ctx, yaws_deg=[0.0])
        with self.assertRaises(ToolError) as e:
            call(self.ctx, replay=first["path"], design_version=4)
        self.assertIn("v1", str(e.exception))
        self.assertIn("v4", str(e.exception))

    def test_a_replayed_record_holds_each_analysis(self):
        with self.gpu_available():
            first = call(self.ctx, yaws_deg=[0.0, 10.0])
        rec = record.load_record(self.run_dir / first["path"])
        self.assertEqual([a["load_case_id"] for a in rec["analyses"]], ["CFD-40kmh-yaw0", "CFD-40kmh-yaw10L"])
        self.assertEqual(rec["requested"]["loading"], "two_cargo")
        self.assertEqual([c["yaw_deg"] for c in rec["requested"]["cases"]], [0.0, 10.0])


class RegistrationTest(unittest.TestCase):
    def test_the_input_schema(self):
        schema = cfd_tools.TOOLS["cfd_aero"]["input_schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("description", schema)
        v = jsonschema.Draft202012Validator(schema)
        for good in ({}, {"design_version": 2}, {"yaws_deg": [0, 5.5, -20]}, {"yaws_deg": [90, -90], "speed_kmh": 0, "wind_ms": 30}, {"speed_kmh": 40},
                     {"speed_kmh": [20, 40]}, {"dx_mm": 80}, {"replay": True}, {"replay": "cfd/v2/40kmh_dx80/aero_record.json"}):
            self.assertEqual([e.message for e in v.iter_errors(good)], [], good)
        for bad in ({"design_version": 0}, {"yaws_deg": []}, {"yaws_deg": [91]}, {"speed_kmh": -1}, {"wind_ms": 0}, {"dx_mm": 5}, {"replay": "x.txt"}, {"other": 1}):
            self.assertTrue(list(v.iter_errors(bad)), bad)

    def test_the_local_schema_gives_way_to_the_contracts(self):
        contract_schema = {"type": "object", "additionalProperties": False, "description": "x", "properties": {"only": {"type": "string"}}}
        root = {"$defs": {"tool_input.cfd_aero": contract_schema}}
        with mock.patch.object(contract, "schema", return_value=root):
            got = cfd_tools._input_schema()
        self.assertEqual(sorted(got["properties"]), ["only"])
        self.assertNotIn("description", got)
        with mock.patch.object(contract, "schema", return_value={"$defs": {}}):
            self.assertEqual(sorted(cfd_tools._input_schema()["properties"]), ["design_version", "dx_mm", "replay", "speed_kmh", "wind_ms", "yaws_deg"])

    @unittest.skipUnless(HAS_SDK, "this Python has no Agent SDK, so no MCP tables")
    def test_it_is_registered_but_not_in_the_production_run(self):
        from hiraku.tools import mcp
        from hiraku.tools.functions import RUN_TOOLS

        t = cfd_tools.TOOLS["cfd_aero"]
        self.assertIs(mcp.HANDLERS["cfd_aero"], t["function"])
        self.assertEqual(mcp.INPUT_SCHEMAS["cfd_aero"], t["input_schema"])
        self.assertEqual(mcp.DESCRIPTIONS["cfd_aero"], t["description"])
        self.assertNotIn("cfd_aero", RUN_TOOLS)


if __name__ == "__main__":
    unittest.main()

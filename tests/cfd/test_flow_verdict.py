"""cfd_aero and cfd_water with judge, on a real run's records and the real formula registry, with the GPU stood in for: the load case,
the hand calculation, analyze, compare and the verdict, each through the run's own functions. No GPU here (test_water_tool.py has one)."""

import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from hiraku import contract
from hiraku.agent.session import start_run
from hiraku.cfd import cuda, solver, vehicle, water
from hiraku.contract.protocols import ToolError
from hiraku.solvers import formula_registry
from hiraku.tools import cfd_tools, functions
from tests.cfd.test_cfd_tools import FakeGpu, runner, turn
from tests.tools import fakes
from tests.tools.helpers import requirements_with_tests

DESIGN = Path(__file__).resolve().parents[1] / "verification" / "fixtures" / "n15-v005-design.json"
REACHED = {"battery": -230.0, "motor": -241.0, "controller": -110.0}


def passed(clearance: dict[str, float]) -> dict:
    return {"protected_parts": {k: {"lower_face_mm": 200.0, "clearance_mm": v, "reached_s": 0.5 if v <= 15.0 else None} for k, v in clearance.items()},
            "vehicle": {"speed_kmh": {"lowest": 11.2}, "pitch_deg_nose_up_against_road": {"max": 3.8}}, "assumptions": ["test"]}


class Base(unittest.TestCase):
    tests_of_the_run = ["F1"]

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        design = json.loads(DESIGN.read_text(encoding="utf-8"))
        design["design_version"] = 1
        path = root / "design.json"
        path.write_text(json.dumps(design), encoding="utf-8")
        self.ctx = start_run(requirements_with_tests(root, self.tests_of_the_run), design_path=path, root=root, slug="flow-verdict", formulas=formula_registry(), tools=fakes.shelf(1.03, 1.03, True), designgen=fakes.FakeDesigngen())

    @contextmanager
    def gpu(self, parts: dict[str, float] | None = None):
        calls: list = []
        with mock.patch.object(cuda, "gpu_or_reason", return_value=(FakeGpu(), "")), mock.patch.object(cuda, "Gpu", lambda *a: FakeGpu()), mock.patch.object(cuda, "gpu_turn", turn), \
                mock.patch.object(solver, "gpu_memory_mib", return_value={"total": 12000, "used": 1000, "free": 11000}), mock.patch.object(vehicle, "run_vehicle", runner(calls)), \
                mock.patch.object(water, "check_moving_box", return_value={"error_x_over_box": 1e-5}), mock.patch.object(cfd_tools, "_water_pass", return_value=passed(parts or REACHED)):
            yield calls

    def records(self, kind: str) -> list[dict]:
        return self.ctx.store.records(kind, self.ctx.version)


class Water(Base):
    def test_water_on_all_three_parts_is_ng_and_as_the_hand_calculation_said(self) -> None:
        with self.gpu():
            out = cfd_tools.cfd_water(self.ctx, {"judge": True}, "cae.aero")
        lc = self.ctx.load_case(out["load_case_id"])
        self.assertEqual(lc["test_id"], "F5")
        self.assertEqual((lc["criterion"]["quantity"], lc["criterion"]["op"], lc["criterion"]["limit"]["value"], lc["criterion"]["limit"]["unit"]), ("water_reached_parts", "<=", 0.0, "1"))
        self.assertEqual(lc["params"]["depth"], {"value": 200.0, "unit": "mm"})
        self.assertIn("仮定", lc["basis"])
        self.assertEqual(out["prediction"]["value"], 3.0)  # the three lower faces are under 200 mm + V^2 / 2 g
        self.assertEqual(out["statement"], water.STATEMENT)
        self.assertFalse(out["physically_validated"])
        analysis = self.records("analysis")[-1]
        self.assertEqual((analysis["tool"], analysis["base"], analysis["status"], analysis["physically_validated"]), ("cfd_water", "K6", "ok", False))
        self.assertIn(water.STATEMENT, analysis["assumptions"])
        self.assertEqual(contract.errors("analysis", analysis), [])
        self.assertEqual(out["comparison"]["status"], "match")
        self.assertEqual(out["verdict"]["pass"], False)
        self.assertEqual({r["where"] for r in analysis["results"] if r["quantity"] == "water_clearance"}, set(REACHED))
        self.assertTrue([j for j in self.ctx.store.judgements(self.ctx.version) if j["domain"] == "aero"])
        self.assertTrue(self.ctx.specs["cfd_water"]["usable"])
        self.assertEqual(self.ctx.specs["cfd_water"]["base"], "K6")

    def test_fewer_parts_than_the_upper_end_of_the_hand_calculation(self) -> None:
        with self.gpu({"battery": 40.0, "motor": -20.0, "controller": 60.0}):
            out = cfd_tools.cfd_water(self.ctx, {"judge": True}, "cae.aero")
        self.assertEqual(out["comparison"]["status"], "mismatch")
        self.assertIsNone(out["verdict"])
        self.assertIn("safe_side", out["next"])
        cause = functions.record_cause(self.ctx, {"comparison_id": out["comparison"]["comparison_id"], "cause": "手計算は水が速度水頭まで上がるとみる上限で、部品の位置を見ない", "fix": "上限の見積もりのまま判定する",
                                                  "safe_side": True, "safe_side_reason": "手計算は水の届く高さの上限なので、届く部品の数を多く数える側にある"}, "cae.aero")
        self.assertEqual(cause["verdict"]["pass"], False)
        self.assertEqual(cause["verdict"]["value_from"], "prediction")

    def test_parts_above_the_water_pass(self) -> None:
        high = mock.Mock(bounds=lambda: ([0.0, 0.0, 2.0], [0.1, 0.1, 2.2]))
        with self.gpu({"battery": 900.0, "motor": 900.0, "controller": 900.0}), mock.patch.object(water, "protected_shapes", return_value={"battery": high, "motor": high, "controller": high}):
            out = cfd_tools.cfd_water(self.ctx, {"judge": True}, "cae.aero")
        self.assertEqual(out["prediction"]["value"], 0.0)
        self.assertEqual(out["comparison"]["status"], "match")
        self.assertEqual(out["verdict"]["pass"], True)

    def test_a_design_with_no_such_parts_is_told_so(self) -> None:
        with self.gpu(), mock.patch.object(water, "protected_shapes", return_value={}), self.assertRaises(ToolError) as e:
            cfd_tools.cfd_water(self.ctx, {"judge": True}, "cae.aero")
        self.assertIn("battery", str(e.exception))

    def test_no_gpu_says_so_and_writes_nothing(self) -> None:
        before = len(self.ctx.load_cases)
        with mock.patch.object(cuda, "gpu_or_reason", return_value=(None, "CUDA の使える GPU がありません")):
            with self.assertRaises(ToolError) as e:
                cfd_tools.cfd_water(self.ctx, {"judge": True}, "cae.aero")
        self.assertIn("GPU が無いので", str(e.exception))
        self.assertEqual(len(self.ctx.load_cases), before)
        self.assertEqual(self.records("prediction"), [])

    def test_a_second_call_uses_the_same_load_case(self) -> None:
        with self.gpu():
            one = cfd_tools.cfd_water(self.ctx, {"judge": True}, "cae.aero")
            two = cfd_tools.cfd_water(self.ctx, {"judge": True, "depth_mm": 100.0}, "cae.aero")
        self.assertEqual(one["load_case_id"], two["load_case_id"])
        self.assertEqual(self.ctx.load_case(one["load_case_id"])["params"]["depth"]["value"], 200.0)

    def test_only_the_aero_role_or_the_lead_writes_it(self) -> None:
        with self.gpu(), self.assertRaises(ToolError):
            cfd_tools.cfd_water(self.ctx, {"judge": True}, "cae.nvh")


class Air(Base):
    def test_the_way_to_a_verdict(self) -> None:
        with self.gpu() as calls:
            out = cfd_tools.cfd_aero(self.ctx, {"judge": True}, "cae.aero")
        self.assertEqual({round(c[0]) for c in calls}, {90, -90})  # both sides of a vehicle at rest
        lc = self.ctx.load_case(out["load_case_id"])
        self.assertEqual((lc["test_id"], lc["criterion"]["quantity"], lc["criterion"]["op"]), ("F4", "critical_wind_speed", ">="))
        f1 = next(c for c in self.ctx.load_cases if c["test_id"] == "F1")
        self.assertEqual(lc["criterion"]["limit"], f1["criterion"]["limit"])
        self.assertEqual(out["prediction"]["formula_id"], "crosswind.critical_wind_speed")
        analysis = self.records("analysis")[-1]
        self.assertEqual((analysis["tool"], analysis["base"], analysis["physically_validated"]), ("cfd_aero", "K6", False))
        self.assertEqual({r["where"] for r in analysis["results"] if r["quantity"] == "critical_wind_speed"}, {"wind from the left", "wind from the right"})
        self.assertIn("確かめていない", " ".join(analysis["assumptions"]))
        self.assertEqual(contract.errors("analysis", analysis), [])
        self.assertIn(out["comparison"]["status"], ("match", "mismatch"))
        self.assertEqual(out["verdict"] is None, out["comparison"]["status"] == "mismatch")
        self.assertTrue(list((Path(self.ctx.store.path) / "cfd").rglob("aero_record.json")))

    def test_no_gpu_says_so_and_writes_nothing(self) -> None:
        before = len(self.ctx.load_cases)
        with mock.patch.object(cuda, "gpu_or_reason", return_value=(None, "CUDA の使える GPU がありません")):
            with self.assertRaises(ToolError) as e:
                cfd_tools.cfd_aero(self.ctx, {"judge": True}, "cae.aero")
        self.assertIn("GPU が無いので", str(e.exception))
        self.assertEqual(len(self.ctx.load_cases), before)


class ARunWithoutAero(Base):
    tests_of_the_run = ["P1"]

    def test_says_the_domain_was_not_picked(self) -> None:
        with self.gpu(), self.assertRaises(ToolError) as e:
            cfd_tools.cfd_water(self.ctx, {"judge": True}, "lead")
        self.assertIn("選ばれていません", str(e.exception))


class Catalog(unittest.TestCase):
    def test_a_run_does_not_start_with_them(self) -> None:
        from hiraku.catalog import load_catalog
        from hiraku.catalog.expr import applies

        catalog = load_catalog()
        every = {"outdoor": True, "self_propelled": True, "liquid": True, "rough": True}
        for test_id in ("F4", "F5"):
            spec = catalog.test(test_id)
            self.assertEqual(spec["domain"], "aero")
            self.assertFalse(applies(spec["applies_if"], every, "speed_vehicle"))
        self.assertFalse({"F4", "F5"} & {t["id"] for t in catalog.tests if applies(t["applies_if"], every, "speed_vehicle")})

    def test_the_formula_of_the_water_test(self) -> None:
        q = lambda v, u: {"value": v, "unit": u}  # noqa: E731
        at = lambda h1, h2, h3: formula_registry().evaluate("flooded.parts_reached", {"h_1": q(h1, "mm"), "h_2": q(h2, "mm"), "h_3": q(h3, "mm"), "d_water": q(200.0, "mm"), "V": q(18.0, "km/h")})["outputs"]  # noqa: E731
        out = at(175.0, 230.0, 260.0)
        self.assertEqual(out["n_reached"]["value"], 3.0)
        self.assertAlmostEqual(out["water_line"]["value"], 200.0 + 25.0 / (2.0 * 9.80665) * 1000.0, places=6)  # 5 m/s: the head is 1275 mm
        self.assertEqual(at(175.0, 1400.0, 2000.0)["n_reached"]["value"], 2.0)
        self.assertEqual(at(1500.0, 1600.0, 2000.0)["n_reached"]["value"], 0.0)


if __name__ == "__main__":
    unittest.main()

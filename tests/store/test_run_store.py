import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hiraku import contract
from hiraku.contract import layout
from hiraku.store import RunStore, StoreError

JST = timezone(timedelta(hours=9))


def example(name):
    with (contract.EXAMPLES_DIR / name).open(encoding="utf-8") as f:
        return json.load(f)


def project_fields():
    p = example("project.json")
    for k in ("run_id", "created_at", "status", "cost_usd", "stop_reason", "design_versions"):
        p.pop(k, None)
    return p


class FakeClock:
    """Wall clock and monotonic clock that advance 0.5 s per reading of the wall clock."""

    def __init__(self):
        self.t = 0.0

    def wall(self):
        dt = datetime(2026, 11, 2, 20, 30, 0, tzinfo=JST) + timedelta(seconds=self.t)
        return dt

    def mono(self):
        self.t += 0.5
        return self.t


class RunStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.clock = FakeClock()
        self.store = RunStore.create(project_fields(), root=self.root, wall=self.clock.wall, mono=self.clock.mono)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_writes_project_and_run_started(self):
        self.assertTrue(self.store.run_id.endswith("-offroad-4w"))
        self.assertTrue((self.store.path / "project.json").exists())
        ev = self.store.events()
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["kind"], "run.started")
        self.assertEqual(ev[0]["seq"], 0)
        self.assertEqual(ev[0]["t"], 0.0)  # the clock moved while project.json was written
        nxt = self.store.emit("agent.said", agent="lead", text="次")
        self.assertGreater(nxt["t"], 0.0)
        self.assertEqual(ev[0]["data"]["status"], "running")
        self.assertEqual(contract.errors("event", ev[0]), [])

    def test_design_versions_are_append_only(self):
        d = example("design.json")
        self.store.write_design(d)
        self.assertEqual(self.store.design_version, 1)
        self.assertEqual(self.store.design(1)["name"], d["name"])
        with self.assertRaises(StoreError):
            self.store.write_design(d)
        d3 = dict(d, design_version=3)
        with self.assertRaises(StoreError):
            self.store.write_design(d3)

    def test_record_file_equals_event_data(self):
        self.store.write_design(example("design.json"))
        pred = example("prediction.json")
        pred["prediction_id"] = self.store.next_id("prediction")
        self.store.add_record("prediction", pred, design_version=1, agent="cae.strength", domain="strength")
        path = layout.record_path(self.store.path, 1, "prediction", pred["prediction_id"])
        with path.open(encoding="utf-8") as f:
            on_disk = json.load(f)
        last = self.store.events()[-1]
        self.assertEqual(last["kind"], "prediction.recorded")
        self.assertEqual(last["data"], on_disk)
        self.assertEqual(self.store.get("prediction", "pred-0001"), on_disk)

    def test_invalid_record_is_not_written(self):
        self.store.write_design(example("design.json"))
        bad = example("prediction.json")
        del bad["value"]
        n = len(self.store.events())
        with self.assertRaises(ValueError):
            self.store.add_record("prediction", bad, design_version=1, agent="cae.strength")
        self.assertEqual(len(self.store.events()), n)
        self.assertEqual(self.store.records("prediction"), [])

    def test_unknown_event_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.emit("made.up", agent="system")

    def test_seq_has_no_gaps_and_t_grows(self):
        self.store.write_design(example("design.json"))
        for _ in range(3):
            self.store.emit("agent.said", agent="lead", text="hello")
        ev = self.store.events()
        self.assertEqual([e["seq"] for e in ev], list(range(len(ev))))
        ts = [e["t"] for e in ev]
        self.assertEqual(ts, sorted(ts))
        for e in ev:
            self.assertEqual(contract.errors("event", e), [])

    def test_ids_are_run_wide(self):
        self.assertEqual(self.store.next_id("prediction"), "pred-0001")
        self.assertEqual(self.store.next_id("prediction"), "pred-0002")
        self.assertEqual(self.store.next_id("analysis"), "ana-0001")

    def test_judgements_replace_the_domain_tile(self):
        self.store.write_design(example("design.json"))
        j = example("judgement.json")
        self.store.set_judgement(dict(j, state="pending", verdict_ids=[], summary="まだ"))
        self.store.set_judgement(j)
        tiles = self.store.judgements(1)
        self.assertEqual(len(tiles), 1)
        self.assertEqual(tiles[0]["state"], "green")

    def test_finish_and_cost(self):
        self.store.set_cost(0.12)
        self.store.finish("stopped", stop_reason="limit")
        p = json.loads((self.store.path / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(p["status"], "stopped")
        self.assertEqual(p["cost_usd"], 0.12)
        last = self.store.events()[-1]
        self.assertEqual(last["kind"], "run.finished")
        self.assertEqual(last["data"]["status"], "stopped")

    def test_the_plugin_paths_cost_is_null(self):
        self.assertEqual(self.store.project["cost_usd"], 0.0)  # a run HIRAKU pays for (or a scripted one)
        for driver, cost in (("claude_code_plugin", None), ("scripted", 0.0), ("agent_sdk", 0.0)):
            with self.subTest(driver=driver):
                s = RunStore.create(dict(project_fields(), driver=driver), root=self.root, slug=driver.replace("_", "-"))
                self.assertEqual(s.project["cost_usd"], cost)
                on_disk = json.loads((s.path / "project.json").read_text(encoding="utf-8"))
                self.assertIn("cost_usd", on_disk)
                self.assertEqual(on_disk["cost_usd"], cost)
                self.assertEqual(contract.errors("event", s.events()[0]), [])

    def test_reopen_continues_seq_and_ids(self):
        self.store.write_design(example("design.json"))
        pred = example("prediction.json")
        pred["prediction_id"] = self.store.next_id("prediction")
        self.store.add_record("prediction", pred, design_version=1, agent="cae.strength")
        again = RunStore.open(self.store.path)
        self.assertEqual(again.design_version, 1)
        self.assertEqual(again.next_id("prediction"), "pred-0002")
        again.emit("agent.said", agent="lead", text="続き")
        ev = again.events()
        self.assertEqual([e["seq"] for e in ev], list(range(len(ev))))
        self.assertGreaterEqual(ev[-1]["t"], ev[-2]["t"])


if __name__ == "__main__":
    unittest.main()

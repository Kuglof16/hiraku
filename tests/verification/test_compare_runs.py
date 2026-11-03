"""qa.compare_runs on small run folders written here (the scripted runs are compared in test_real_runs)."""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from qa import compare_runs as cr

RUN_A = "20261103-100000-qa-compare"
RUN_B = "20261103-100002-qa-compare"


def _event(seq, run_id, kind, t, data, text=""):
    return {"seq": seq, "ts": f"2026-11-03T10:00:{seq:02d}.{int(t * 1000) % 1000:03d}+09:00", "t": t,
            "run_id": run_id, "kind": kind, "agent": "system", "text": text, "data": data}


def _analysis(wall):
    return {"analysis_id": "ana-0001", "tool": "modal", "design_version": 1, "results": [{"quantity": "mode_1",
            "value": {"value": 1.7771981498622405, "unit": "Hz"}}], "wall_time_s": wall,
            "fields": [{"kind": "mode_shape", "per_node": {"LA": [0.5, -0.25, 1.0], "LB": [0.0, 0.125, -0.75]}}],
            "series": [{"name": "z", "t": [0.0, 0.5], "values": [0.0, 1.0]}]}


def write_run(root: Path, run_id: str, offset: float = 0.0, cost: float = 0.0) -> Path:
    """A run whose times, run id and cost depend on the arguments and nothing else does."""
    run = root / run_id
    (run / "v001" / "analyses").mkdir(parents=True)
    project = {"run_id": run_id, "created_at": f"2026-11-03T10:00:0{int(offset)}+09:00", "status": "done",
               "cost_usd": cost, "stop_reason": f"finished (run {run_id})"}
    (run / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
    ana = _analysis(1.5 + offset)
    (run / "v001" / "analyses" / "ana-0001.json").write_text(json.dumps(ana, ensure_ascii=False, indent=2), encoding="utf-8")
    events = [_event(0, run_id, "run.started", 0.0, project),
              _event(1, run_id, "analysis.finished", 0.25 + offset, ana, text="1 次の固有振動数 1.777 Hz"),
              _event(2, run_id, "run.finished", 0.5 + offset, {"status": "done", "cost_usd": cost},
                     text=f"run {run_id} finished")]
    (run / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    return run


class CompareRuns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.a = write_run(self.root / "a", RUN_A)
        self.b = write_run(self.root / "b", RUN_B, offset=1.0, cost=0.25)

    def tearDown(self):
        self.tmp.cleanup()

    def reset(self):
        self.tearDown()
        self.setUp()

    def edit(self, rel: str, change, run=None):
        p = (run or self.b) / rel
        if rel == "events.jsonl":
            lines = [json.loads(s) for s in p.read_text(encoding="utf-8").splitlines()]
            change(lines)
            p.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in lines), encoding="utf-8")
        else:
            obj = json.loads(p.read_text(encoding="utf-8"))
            obj = change(obj) or obj
            p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def lines(self, **kw):
        r = cr.compare(self.a, self.b, **kw)
        return r, [s for s in r.lines if s.startswith(("DIFF", "ONLY"))]

    def test_times_run_id_and_cost_are_left_out(self):
        r, found = self.lines()
        self.assertEqual(found, [], r.lines)
        self.assertTrue(r.same)
        self.assertEqual(r.files_compared, 3)

    def test_a_changed_value_names_the_record_and_the_field(self):
        self.edit("v001/analyses/ana-0001.json", lambda a: a["results"][0]["value"].update(value=1.777198149870244))
        r, found = self.lines()
        self.assertFalse(r.same)
        self.assertEqual(len(found), 1)
        self.assertIn("v001/analyses/ana-0001.json  results[0].value.value: 1.7771981498622405 -> 1.777198149870244", found[0])

    def test_events_are_compared_line_by_line(self):
        self.edit("events.jsonl", lambda es: es[1]["data"]["results"][0]["value"].update(unit="kHz"))
        _, found = self.lines()
        self.assertEqual(found, ['DIFF  events.jsonl:2 seq=1 analysis.finished  data.results[0].value.unit: "Hz" -> "kHz"'])

    def test_a_t_inside_the_data_is_compared(self):
        self.edit("events.jsonl", lambda es: es[1]["data"]["series"][0].update(t=[0.0, 0.6]))
        _, found = self.lines()
        self.assertEqual(len(found), 1)
        self.assertIn("data.series[0].t[1]: 0.5 -> 0.6", found[0])

    def test_one_byte_counts(self):
        cases = {
            "key order": lambda p: {k: p[k] for k in reversed(list(p))},
            "type": lambda p: p.update(status=1) or p,
        }
        for name, change in cases.items():
            with self.subTest(name):
                self.reset()
                self.edit("project.json", change)
                r, found = self.lines()
                self.assertFalse(r.same, name)
        with self.subTest("int and float"):
            self.reset()
            self.edit("v001/analyses/ana-0001.json", lambda a: a["fields"][0]["per_node"]["LA"].__setitem__(2, 1))
            _, found = self.lines()
            self.assertEqual(len(found), 1)
            self.assertIn("(type)", found[0])
        with self.subTest("spacing"):
            self.reset()
            p = self.b / "project.json"
            p.write_text(json.dumps(json.loads(p.read_text(encoding="utf-8")), ensure_ascii=False), encoding="utf-8")
            _, found = self.lines()
            self.assertEqual(len(found), 1)
            self.assertIn("written differently", found[0])

    def test_files_and_lines_on_one_side_only(self):
        shutil.copy(self.b / "project.json", self.b / "v001" / "extra.json")
        self.edit("events.jsonl", lambda es: es.append(dict(es[-1], seq=3, kind="note")))
        _, found = self.lines()
        self.assertIn("ONLY  v001/extra.json: only in B", found)
        self.assertTrue(any("3 lines in A, 4 in B" in s and "'note'" in s for s in found), found)

    def test_rel_lets_small_number_differences_through(self):
        def change(a):
            a["results"][0]["value"]["value"] *= 1 + 1e-9
        self.edit("v001/analyses/ana-0001.json", change)
        self.edit("events.jsonl", lambda es: es[1].update(text="1 次の固有振動数 1.7770001 Hz")
                  or es[1]["data"]["results"][0]["value"].update(value=1.7771981498622405 * (1 + 1e-9)), run=self.b)
        self.edit("events.jsonl", lambda es: es[1].update(text="1 次の固有振動数 1.777 Hz"), run=self.a)
        r, found = self.lines(rel=1e-6)
        self.assertEqual(found, [], r.lines)
        self.assertEqual(r.near, 3)
        self.assertIn("within --rel 1e-06", r.summary())
        r, found = self.lines(rel=1e-10)
        self.assertEqual(len(found), 3, found)

    def test_a_mode_shape_with_the_other_sign(self):
        def flip(a):
            f = a["fields"][0]
            f["per_node"] = {n: [-x for x in v] for n, v in f["per_node"].items()}
        self.edit("v001/analyses/ana-0001.json", flip)
        r, found = self.lines()
        self.assertEqual(len(found), 5)  # the non-zero components without --rel
        r, found = self.lines(rel=1e-6)
        self.assertEqual(found, [])
        self.assertEqual(r.flipped, ["v001/analyses/ana-0001.json fields[0].per_node"])
        self.edit("v001/analyses/ana-0001.json", lambda a: a["fields"][0].update(kind="displacement"))
        self.edit("v001/analyses/ana-0001.json", lambda a: a["fields"][0].update(kind="displacement"), run=self.a)
        r, found = self.lines(rel=1e-6)
        self.assertEqual(len(found), 5)  # only a mode shape's sign is arbitrary

    def test_main_exit_codes(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cr.main([str(self.a), str(self.b)]), 0)
            self.edit("project.json", lambda p: p.update(status="stopped"))
            self.assertEqual(cr.main([str(self.a), str(self.b)]), 1)
        self.assertIn('DIFF  project.json  status: "done" -> "stopped"', out.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cr.main([str(self.a), str(self.root)]), 2)  # not a run folder
            self.assertEqual(cr.main(["--check", "data/requirements/x.yaml", "--baseline", str(self.root / "none")]), 2)

    def test_newest_baseline(self):
        base = self.root / "baseline"
        for rid in ("20261103-090000-x", "20261103-110000-x", "20261102-230000-x"):
            (base / "offroad-4w-video" / rid).mkdir(parents=True)
        self.assertEqual(cr.newest_baseline(base, Path("data/requirements/offroad-4w-video.yaml")).name,
                         "20261103-110000-x")
        self.assertIsNone(cr.newest_baseline(base, Path("data/requirements/offroad-4w.yaml")))


if __name__ == "__main__":
    unittest.main()

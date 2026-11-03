"""hiraku.export: a scripted run's records written out (CSV, VTK, report, zip) and read back."""

import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from hiraku.agent.scripted import run_scripted
from hiraku.store import RunStore
from hiraku.export import ExportError, export_run, export_zip, read_csv, read_vtk
from hiraku.export.vtk import VTK_LINE, VTK_QUAD, VTK_TRIANGLE, _mesh_files
from tests.tools.helpers import RunCase

MESH = {"element_type": "shell", "nodes_mm": [[0, 0, 0], [10, 0, 0], [10, 10, 0], [0, 10, 0], [20, 0, 0]],
        "elements": [[0, 1, 2, 3], [1, 4, 2]], "element_member": ["rail_L", "rail_L"],
        "member_thickness_mm": {"rail_L": 2.3}}


class ExportTest(RunCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        run_scripted(self.ctx)
        s, v = self.ctx.store, 1
        self.mesh = s.add_record("mesh", {**MESH, "mesh_id": s.next_id("mesh"), "design_version": v}, design_version=v,
                                 agent="cae.strength")
        design = s.design(v)
        self.member = design["members"][0]["id"]
        self.node = design["nodes"][0]["id"]
        r1 = next(p["prediction_id"] for p in s.records("prediction", v) if p["load_case_id"] == "R1-step100")
        self.shell = s.add_record("analysis", {
            "analysis_id": s.next_id("analysis"), "tool": "frame_shell", "base": "K1", "design_version": v,
            "load_case_id": "R1-step100", "prediction_ids": [r1], "status": "ok",
            "results": [{"quantity": "max_stress", "value": 123.25, "unit": "MPa", "where": "rail_L"}],
            "fields": [
                {"kind": "stress", "unit": "MPa", "label": "von Mises", "surface": "top", "mesh_id": self.mesh["mesh_id"],
                 "per_element": [12.5, 40.25]},
                {"kind": "displacement", "unit": "mm", "mesh_id": self.mesh["mesh_id"],
                 "per_mesh_node": [[0, 0, 0], [0, 0, -0.5], [0, 0, -0.25], [0, 0, 0], [0, 0, -1.0]]},
                {"kind": "stress", "unit": "MPa", "label": "beam", "per_member": {self.member: 7.75}},
                {"kind": "displacement", "unit": "mm", "per_node": {self.node: [0.5, 0.0, -0.125]}},
            ],
            "series": [
                {"channel": "speed", "unit": "km/h", "t_s": [0, 0.5, 1.0], "values": [0, 5, 10]},
                {"channel": "soc", "unit": "%", "t_s": [0, 0.5, 1.0], "values": [100, 99.5, 99]},
                {"channel": "response", "unit": "1", "values": [1.0, 2.5],
                 "x": {"quantity": "frequency", "unit": "Hz", "values": [1, 2]}},
            ],
        }, design_version=v, agent="cae.strength", domain="strength")
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "out"

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_comparisons_and_results_read_back_as_recorded(self):
        got = export_run(self.ctx.store.path, self.out, parts=["comparisons", "results"])
        self.assertEqual(got["files"], ["comparisons.csv", "results.csv"])
        rows = {r["comparison_id"]: r for r in read_csv(self.out / "comparisons.csv")}
        records = self.ctx.store.records("comparison")
        self.assertEqual(sorted(rows), sorted(c["comparison_id"] for c in records))
        verdicts = {x["comparison_id"]: x for x in self.ctx.store.records("verdict")}
        for c in records:
            row = rows[c["comparison_id"]]
            self.assertEqual((float(row["predicted"]), float(row["analyzed"]), float(row["diff_rel"]), row["status"]),
                             (c["predicted"]["value"], c["analyzed"]["value"], c["diff_rel"], c["status"]))
            if c["comparison_id"] in verdicts:
                ver = verdicts[c["comparison_id"]]
                self.assertEqual((row["verdict_id"], float(row["verdict_value"]), row["pass"]),
                                 (ver["verdict_id"], ver["value"]["value"], str(ver["pass"])))
        results = read_csv(self.out / "results.csv")
        self.assertEqual(len(results), sum(len(a["results"]) for a in self.ctx.store.records("analysis")))
        self.assertIn({"analysis_id": self.shell["analysis_id"], "quantity": "max_stress", "value": "123.25"},
                      [{k: r[k] for k in ("analysis_id", "quantity", "value")} for r in results])

    def test_series_share_a_file_per_axis(self):
        got = export_run(self.ctx.store.path, self.out, parts=["series"])
        mine = sorted(f for f in got["files"] if self.shell["analysis_id"] in f)
        self.assertEqual(len(mine), 2)  # t_s shared by speed and soc; the frequency axis apart
        rows = {}
        for f in mine:
            with (self.out / f).open(encoding="utf-8-sig") as fh:
                rows[f] = [line.strip().split(",") for line in fh]
        by_head = {r[0][0]: r for r in rows.values()}
        self.assertEqual(by_head["t [s]"][0], ["t [s]", "speed [km/h]", "soc [%]"])
        self.assertEqual([float(x) for x in by_head["t [s]"][3]], [1.0, 10.0, 99.0])
        self.assertEqual([[float(x) for x in r] for r in by_head["frequency [Hz]"][1:]], [[1, 1.0], [2, 2.5]])

    def test_vtk_of_the_shell_and_the_beams(self):
        got = export_run(self.ctx.store.path, self.out, parts=["vtk"])
        a, m = self.shell["analysis_id"], self.mesh["mesh_id"]
        shell = read_vtk(self.out / f"vtk/v001/{a}-{m}.vtk")
        self.assertEqual(shell["points"], [[float(x) for x in p] for p in MESH["nodes_mm"]])
        self.assertEqual(shell["cells"], MESH["elements"])
        self.assertEqual(shell["cell_types"], [VTK_QUAD, VTK_TRIANGLE])
        self.assertEqual(shell["cell_data"]["stress_top_von_Mises"], [12.5, 40.25])
        self.assertEqual(shell["point_data"]["displacement_2"][4], [0.0, 0.0, -1.0])
        alone = read_vtk(self.out / f"vtk/v001/{m}.vtk")
        self.assertEqual(alone["cell_data"]["thickness_mm"], [2.3, 2.3])
        beams = read_vtk(self.out / f"vtk/v001/{a}-frame.vtk")
        design = self.ctx.store.design(1)
        self.assertEqual(len(beams["points"]), len(design["nodes"]))
        self.assertEqual(set(beams["cell_types"]), {VTK_LINE})
        values = beams["cell_data"]["stress_beam"]
        self.assertEqual(values[0], 7.75)
        self.assertTrue(all(v != v for v in values[1:]))  # members without a value: NaN
        self.assertEqual(beams["point_data"]["displacement_4"][0], [0.5, 0.0, -0.125])
        self.assertIn(f"vtk/v001/{m}.vtk", got["files"])

    def test_frames_give_one_file_per_moment(self):
        analysis = {"analysis_id": "ana-0099", "tool": "frame_shell", "load_case_id": "P1-step100", "fields": [
            {"kind": "stress", "unit": "MPa", "mesh_id": "mesh-0001", "per_element": [1.0, 2.0],
             "frames": {"t_s": [0.0, 0.25], "per_element": [[1.0, 2.0], [3.0, 4.0]]}}]}
        files = _mesh_files(self.out, analysis, {"mesh-0001": {**MESH, "mesh_id": "mesh-0001"}})
        self.assertEqual([f.name for f in files], ["ana-0099-mesh-0001.vtk", "ana-0099-mesh-0001-t000.vtk",
                                                   "ana-0099-mesh-0001-t001.vtk"])
        self.assertEqual(read_vtk(files[2])["cell_data"]["stress_1"], [3.0, 4.0])

    def test_report_keeps_causes_assumptions_and_sources(self):
        export_run(self.ctx.store.path, self.out, parts=["report"])
        md = (self.out / "report.md").read_text(encoding="utf-8")
        page = (self.out / "report.html").read_text(encoding="utf-8")
        report = self.ctx.store.project  # the requirements and their sources
        for r in report["requirements"]:
            self.assertIn(r["key"], md)
        for c in self.ctx.store.records("cause"):
            self.assertIn(" ".join(c["cause"].split())[:20], " ".join(md.split()))
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertNotIn("<script", page)

    def test_zip_holds_the_export_and_the_records(self):
        got = export_zip(self.ctx.store.path, parts=["comparisons", "report"])
        with zipfile.ZipFile(got["zip"]) as z:
            names = set(z.namelist())
        self.assertTrue({"export/comparisons.csv", "export/report.md", "project.json", "events.jsonl"} <= names)
        self.assertFalse([n for n in names if n.endswith(".zip")])

    def test_refusals_name_what_the_run_has(self):
        cases = [({"parts": ["pictures"]}, "選べるのは"), ({"versions": [2]}, "ある版: 1"),
                 ({"domains": ["nvh"]}, "ある分野"), ({"diff_from": 1}, "同じです"), ({"diff_from": 5}, "ある版")]
        for kw, needle in cases:
            with self.subTest(kw=kw), self.assertRaises(ExportError) as cm:
                export_run(self.ctx.store.path, self.out, **kw)
            self.assertIn(needle, str(cm.exception))

    def test_only_the_chosen_domains(self):
        load_cases = {lc["load_case_id"]: lc["domain"] for lc in self.ctx.project["load_cases"]}
        domain = load_cases["R1-step100"]
        got = export_run(self.ctx.store.path, self.out, parts=["comparisons", "results", "series", "vtk"],
                         domains=[domain])
        rows = read_csv(self.out / "comparisons.csv")
        self.assertTrue(rows)
        self.assertEqual({r["domain"] for r in rows}, {domain})
        self.assertEqual({load_cases[r["load_case_id"]] for r in read_csv(self.out / "results.csv")}, {domain})
        self.assertIn(f"vtk/v001/{self.shell['analysis_id']}-frame.vtk", got["files"])
        other = sorted(set(load_cases.values()) - {domain})
        if other:  # another domain: nothing of R1's
            got = export_run(self.ctx.store.path, Path(self.tmp.name) / "other", parts=["vtk"], domains=other[:1])
            self.assertFalse([f for f in got["files"] if self.shell["analysis_id"] in f])

    def test_diff_from_sets_the_same_results_side_by_side(self):
        s = self.ctx.store
        s.write_design(dict(s.design(1), design_version=2, parent_version=1, change_reason="test"))
        r1 = next(p["prediction_id"] for p in s.records("prediction", 1) if p["load_case_id"] == "R1-step100")
        s.add_record("analysis", {**{k: self.shell[k] for k in ("tool", "base", "load_case_id", "status")},
                                  "analysis_id": s.next_id("analysis"), "design_version": 2, "prediction_ids": [r1],
                                  "results": [{"quantity": "max_stress", "value": 100.0, "unit": "MPa", "where": "rail_L"}]},
                     design_version=2, agent="cae.strength", domain="strength")
        got = export_run(s.path, self.out, parts=["results"], versions=[2], diff_from=1)
        self.assertIn("results_diff.csv", got["files"])
        (row,) = [r for r in read_csv(self.out / "results_diff.csv") if r["tool"] == "frame_shell"]
        self.assertEqual((float(row["value_from"]), float(row["value_to"]), float(row["change"])), (123.25, 100.0, -23.25))
        self.assertAlmostEqual(float(row["change_rel"]), -23.25 / 123.25)


@unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "the demo's scripted run takes a minute (HIRAKU_SLOW_TESTS=1)")
class DemoRunExportTest(unittest.TestCase):
    """The demo's scripted run with the real solvers, exported and read back."""

    def test_the_demo_run_reads_back(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, PYTHONUTF8="1")
            env.pop("ANTHROPIC_API_KEY", None)
            subprocess.run([sys.executable, "-m", "hiraku", "run", str(root / "data/requirements/offroad-4w.yaml"),
                            "--scripted", "--runs", tmp], cwd=root, env=env, capture_output=True, timeout=900)
            (run,) = [p for p in Path(tmp).iterdir() if p.is_dir()]
            got = export_run(run)
            store = RunStore.open(run)
            rows = read_csv(Path(got["out_dir"]) / "comparisons.csv")
            self.assertEqual([(r["comparison_id"], float(r["analyzed"])) for r in rows],
                             [(c["comparison_id"], c["analyzed"]["value"]) for c in store.records("comparison")])
            meshes = [f for f in got["files"] if f.endswith(".vtk") and "-mesh-" in f]
            self.assertTrue(meshes, "the demo's frame analyses draw on the shell mesh")
            for rel in meshes[:2]:
                a = store.get("analysis", Path(rel).name.split("-mesh-")[0])
                field = next(f for f in a["fields"] if f.get("per_element"))
                back = read_vtk(Path(got["out_dir"]) / rel)["cell_data"]
                self.assertIn(field["per_element"], list(back.values()))


if __name__ == "__main__":
    unittest.main()

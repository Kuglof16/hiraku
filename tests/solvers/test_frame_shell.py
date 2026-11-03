import copy
import json
import time
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, solvers
from hiraku.solvers.k1_shell import build_mesh
from hiraku.solvers.shelf import fixtures
from hiraku.solvers.shelf.frame_shell import LABELS, LABEL_DISP, same_mesh

ROOT = Path(__file__).resolve().parents[2]
STEP = {"step_height": {"value": 100.0, "unit": "mm"}}


def tool(name):
    return {t.name: t for t in solvers.builtin_tools()}[name]


def mesh_fields(rec):
    return [f for f in rec.get("fields", []) if "mesh_id" in f]


def value(rec, quantity):
    return next(r["value"] for r in rec["results"] if r["quantity"] == quantity)


class FrameShellToolTest(unittest.TestCase):
    def test_on_the_shelf(self):
        t = tool("frame_shell")
        self.assertTrue(t.needs_mesh)
        self.assertEqual((t.domain, t.base, t.tests), ("strength", "K1", ["R1"]))
        d = t.describe()
        self.assertEqual([p["name"] for p in d["params"]], ["loading", "g_factor", "step_height", "step_supports"])
        self.assertIn("frame_static.rail_joint_stress", [p["formula_id"] for p in d["predicts"]])
        self.assertTrue(all("stress concentration" in p["note"] for p in d["predicts"] if p["quantity"] == "max_stress"))

    def test_known_answers_pass(self):
        t0 = time.perf_counter()
        rows = tool("frame_shell").check()
        dt = time.perf_counter() - t0
        print(f"  frame_shell known answers: {len(rows)} checks in {dt:.2f} s")
        bad = [(r["case"], r["quantity"], r["expected"], r["got"]) for r in rows if not r["pass"]]
        self.assertEqual(bad, [])
        self.assertTrue(any("ladder" in r["case"] for r in rows))
        for r in rows:
            contract.validate("tool_check", r)
        self.assertLess(dt, 15.0)

    def test_other_mesh_is_named(self):
        lad = fixtures.ladder_design()
        ms = build_mesh(lad, 20.0)
        other = dict(build_mesh(lad, 25.0).record("mesh-0002", 1))
        why = same_mesh({"mesh": ms}, other)
        self.assertIn("mesh-0002", why)
        self.assertIn("not drawn", why)
        self.assertIsNone(same_mesh({"mesh": ms}, ms.record("mesh-0001", 1)))

    def test_forces_of_another_design_fail_with_the_reason(self):
        lad = fixtures.ladder_design()
        up = {"P1": {"status": "ok", "vectors": [
            {"kind": "reaction", "at": s["id"], "origin_mm": [s["position_mm"][0], s["position_mm"][1], 0.0],
             "vector_N": [0.0, 0.0, 100.0]} for s in lad["supports"]]}}
        lc = {"load_case_id": "R1-x", "test_id": "R1", "domain": "strength", "title": "R1", "params": {},
              "tolerance_rel": 0.15}
        rec = tool("frame_shell").run(lad, lc, [{"prediction_id": "pred-0001"}], {"upstream_analyses": up})
        self.assertEqual(rec["status"], "failed")
        self.assertIn("do not balance the weight", rec["error"])
        self.assertIn("unbalanced", rec["error"])


class DemoStepTest(unittest.TestCase):
    """offroad-4w-v1, R1 on a 100 mm step: three solves (about 6 s each)."""

    @classmethod
    def setUpClass(cls):
        cls.design = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
        cls.mesh = dict(solvers.frame_mesh(cls.design), mesh_id="mesh-0001")
        p1_lc = {"load_case_id": "P1-step100", "test_id": "P1", "domain": "powertrain", "title": "P1",
                 "params": dict(STEP), "tolerance_rel": 0.15}
        cls.p1 = tool("step_climb").run(cls.design, p1_lc, [{"prediction_id": "pred-0001"}], {})
        cls.lc = {"load_case_id": "R1-step100", "test_id": "R1", "domain": "strength", "title": "R1",
                  "params": dict(STEP), "tolerance_rel": 0.15}
        t = tool("frame_shell")
        up = {"P1-step100": cls.p1}
        cls.with_mesh = t.run(cls.design, cls.lc, [{"prediction_id": "pred-0002"}],
                              {"mesh": cls.mesh, "upstream_analyses": up})
        cls.no_mesh = t.run(cls.design, cls.lc, [{"prediction_id": "pred-0002"}], {})  # K0 step forces, no upstream
        cls.other = t.run(cls.design, cls.lc, [{"prediction_id": "pred-0002"}],
                          {"mesh": dict(cls.mesh, design_version=2), "upstream_analyses": up})

    def test_status_and_contract(self):
        self.assertEqual(self.p1["status"], "ok", self.p1.get("error"))
        for rec in (self.with_mesh, self.no_mesh, self.other):
            self.assertEqual(rec["status"], "ok", rec.get("error"))
            contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        self.assertIn("upstream analysis P1-step100", " ".join(e["name"] for e in self.with_mesh["effects"]))

    def test_fields_on_the_passed_mesh(self):
        fs = mesh_fields(self.with_mesh)
        stress = {f["surface"]: f for f in fs if f["kind"] == "stress"}
        self.assertEqual(set(stress), {"max", "top", "bottom"})
        for s, f in stress.items():
            self.assertEqual((f["mesh_id"], f["label"], len(f["per_element"])),
                             ("mesh-0001", LABELS[s], len(self.mesh["elements"])))
        mx, top, bot = (np.array(stress[s]["per_element"]) for s in ("max", "top", "bottom"))
        np.testing.assert_allclose(mx, np.maximum(top, bot), atol=0.006)
        self.assertAlmostEqual(float(mx.max()), value(self.with_mesh, "max_stress"), delta=0.006)
        disp = next(f for f in fs if f["kind"] == "displacement")
        self.assertEqual((disp["label"], len(disp["per_mesh_node"])), (LABEL_DISP, len(self.mesh["nodes_mm"])))
        self.assertTrue(all(len(v) == 3 for v in disp["per_mesh_node"]))
        size = len(json.dumps(self.with_mesh))
        print(f"  frame_shell record with the mesh fields: {size / 1e6:.2f} MB")
        self.assertLess(size, 1.5e6)

    def test_no_mesh_no_mesh_fields(self):
        self.assertEqual(mesh_fields(self.no_mesh), [])
        for rec in (self.no_mesh, self.with_mesh):
            pm = next(f for f in rec["fields"] if f["label"].startswith("シェル") and "per_member" in f)["per_member"]
            self.assertEqual(set(pm), {m["id"] for m in self.design["members"]})
        self.assertTrue(self.no_mesh["scene"])
        # P1 took its forces from the same K0 statics, so both ways give the same frame
        self.assertAlmostEqual(value(self.no_mesh, "max_stress"), value(self.with_mesh, "max_stress"),
                               delta=1e-6 * value(self.with_mesh, "max_stress"))

    def test_mesh_of_another_version_is_not_used(self):
        self.assertEqual(mesh_fields(self.other), [])
        self.assertTrue(any("not drawn" in e["name"] for e in self.other["effects"]))

    def test_shell_peak_above_the_beam(self):
        shell, beam = value(self.with_mesh, "max_stress"), value(self.with_mesh, "max_stress_beam")
        self.assertGreater(shell, beam)
        eff = {e["name"]: e for e in self.with_mesh["effects"]}
        scf = next(e for n, e in eff.items() if n.startswith("shell max / beam max"))
        self.assertGreater(scf["value"], 1.0)
        parts = [e for n, e in eff.items() if n.startswith(("membrane part", "wall bending part"))]
        self.assertEqual(len(parts), 2)
        self.assertAlmostEqual(sum(p["share"] for p in parts), 1.0, places=9)
        print(f"  R1 step 100 mm: shell {shell:.1f} MPa, beam {beam:.1f} MPa, shell/beam same member "
              f"{scf['value']:.2f}, wall time {self.with_mesh['wall_time_s']:.1f} s")

    def test_balance(self):
        rec = self.with_mesh
        self.assertLess(value(rec, "residual_ratio"), 1e-6)
        total = sum(np.array(v["vector_N"]) for v in rec["vectors"])
        weight = next(v for v in rec["vectors"] if v["kind"] == "load")["vector_N"][2]
        self.assertLess(float(np.linalg.norm(total)), 1e-6 * abs(weight))
        for sid in ("FL", "FR", "RL", "RR"):
            p1 = next(r["value"] for r in self.p1["results"] if r["quantity"] == f"wheel_load_{sid}")
            self.assertAlmostEqual(value(rec, f"wheel_load_{sid}"), p1, delta=1e-9 * abs(p1))

    def test_beam_side_is_frame_static(self):
        """max_stress_beam is K1 with the same ground forces: frame_static with the same upstream gives it too."""
        fs = tool("frame_static").run(self.design, self.lc, [{"prediction_id": "pred-0003"}],
                                      {"upstream_analyses": {"P1-step100": self.p1}})
        self.assertAlmostEqual(value(self.with_mesh, "max_stress_beam"), value(fs, "max_stress"), places=6)


if __name__ == "__main__":
    unittest.main()

import copy
import json
import os
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
SLOW = os.environ.get("HIRAKU_SLOW_TESTS") == "1"  # the long demo runs (as HIRAKU_WEB_BROWSER in tests/web)


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
        self.assertEqual([p["name"] for p in d["run_params"]], ["element_size", "mesh_check"])
        self.assertEqual(d["run_params"][0]["default"], {"value": 10.0, "unit": "mm"})
        ids = [p["formula_id"] for p in d["predicts"]]
        self.assertEqual(ids[0], "frame_static.joint_wall_bending")
        self.assertIn("frame_static.rail_joint_stress", ids)
        self.assertTrue(all("stress concentration" in p["note"] for p in d["predicts"]
                            if p["quantity"] == "max_stress" and p["formula_id"].startswith("frame_static.rail_")))
        self.assertIn("joint_wall_inputs", d["predicts"][0]["note"])

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


class ElementSizeTest(unittest.TestCase):
    LC = {"load_case_id": "R1-x", "test_id": "R1", "domain": "strength", "title": "R1", "params": {},
          "tolerance_rel": 0.15}

    def run_with(self, params):
        return tool("frame_shell").run(fixtures.ladder_design(), self.LC, [{"prediction_id": "pred-0001"}], params)

    def test_size_and_half_size_check(self):
        rec = self.run_with({"element_size": {"value": 0.02, "unit": "m"}, "mesh_check": "half"})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        self.assertTrue(any(e["name"].startswith("shell model") and "(edge 20 mm)" in e["name"] for e in rec["effects"]))
        hs, fine = value(rec, "max_hot_spot_stress"), value(rec, "max_hot_spot_stress_half_size")
        self.assertAlmostEqual(value(rec, "hot_spot_change_half_size"), fine / hs - 1.0, places=12)
        self.assertLess(abs(fine / hs - 1.0), 0.10)  # the hot spot holds as the elements halve
        self.assertGreater(value(rec, "max_stress_half_size"), value(rec, "max_stress"))  # the corner peak grows
        check = next(e for e in rec["effects"] if e["name"].startswith("mesh check 20 -> 10 mm"))
        self.assertEqual(check["unit"], "1")

    def test_bad_values_fail_with_the_reason(self):
        for params, why in [({"element_size": {"value": 3.0, "unit": "mm"}}, "must be 5 to 20 mm, got 3 mm"),
                            ({"element_size": {"value": 8.0, "unit": "mm"}, "mesh_check": "half"},
                             "needs element_size 10 mm or more"),
                            ({"element_size": {"value": 8.0, "unit": "kg"}}, "run parameter 'element_size' must be in mm"),
                            ({"mesh_check": "double"}, "run parameter 'mesh_check' must be one of half")]:
            rec = self.run_with(params)
            self.assertEqual(rec["status"], "failed", params)
            self.assertIn(why, rec["error"])


@unittest.skipUnless(SLOW, "set HIRAKU_SLOW_TESTS=1 for the demo's three shell solves (about 20 s)")
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
        # the peak leaves out the elements inside the wheel mounts; their stresses stay in the field
        ms = build_mesh(self.design)
        out = np.zeros(len(mx), bool)
        for el in ms.mount_elements.values():
            out[el] = True
        self.assertAlmostEqual(float(mx[~out].max()), value(self.with_mesh, "max_stress"), delta=0.006)
        self.assertGreater(float(mx[out].max()), 0.0)
        disp = next(f for f in fs if f["kind"] == "displacement")
        self.assertEqual((disp["label"], len(disp["per_mesh_node"])), (LABEL_DISP, len(self.mesh["nodes_mm"])))
        self.assertTrue(all(len(v) == 3 for v in disp["per_mesh_node"]))
        size = len(json.dumps(self.with_mesh))
        print(f"  frame_shell record with the mesh fields: {size / 1e6:.2f} MB")
        self.assertLess(size, 2.0e6)  # the 10 mm mesh with weld strips: about 35000 elements (1.6 MB)

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

    def test_mounts_are_said(self):
        rec = self.with_mesh
        ms = build_mesh(self.design)
        n = sum(len(v) for v in ms.mount_elements.values())
        eff = next(e for e in rec["effects"] if e["name"].startswith("wheel loads put in"))
        self.assertEqual(eff["value"], n)
        self.assertIn("LA +- 80 mm", eff["name"])
        self.assertTrue(any("left out" in e["name"] and e["unit"] == "MPa" for e in rec["effects"]))
        self.assertIn("確かめていない", " ".join(rec["assumptions"]))
        self.assertIn("外した", " ".join(rec["assumptions"]))

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


class ReinforcedJointsTest(unittest.TestCase):
    """The demo with gussets (frame_shell with its mesh, 10 mm) and with the cross members through (20 mm), at rest."""

    @classmethod
    def setUpClass(cls):
        from hiraku import designgen
        v1 = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
        cls.gusset = designgen.regenerate(v1, {"joints": "gusset"})
        cls.through = designgen.regenerate(v1, {"joints": "through"})
        cls.mesh = dict(solvers.frame_mesh(cls.gusset), mesh_id="mesh-0001")
        lc = {"load_case_id": "R1-rest", "test_id": "R1", "domain": "strength", "title": "R1", "params": {},
              "tolerance_rel": 0.15}
        cls.rec = tool("frame_shell").run(cls.gusset, lc, [{"prediction_id": "pred-0001"}], {"mesh": cls.mesh})

    def test_gusset_record(self):
        rec = self.rec
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        self.assertEqual(len(mesh_fields(rec)), 4)  # the mesh with the gusset plates is drawn
        pm = next(f for f in rec["fields"] if f.get("label", "").startswith("シェル") and "per_member" in f)["per_member"]
        self.assertEqual(set(pm), {m["id"] for m in self.gusset["members"]})
        self.assertIn("gussets meshed as plates", " ".join(e["name"] for e in rec["effects"]))
        self.assertIn("ガセット", " ".join(rec["assumptions"]))
        self.assertLess(value(rec, "residual_ratio"), 1e-6)

    def test_frame_static_draws_on_the_gusset_mesh(self):
        lc = {"load_case_id": "R1-rest", "test_id": "R1", "domain": "strength", "title": "R1", "params": {},
              "tolerance_rel": 0.15}
        rec = tool("frame_static").run(self.gusset, lc, [{"prediction_id": "pred-0001"}], {"mesh": self.mesh})
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        f = mesh_fields(rec)[0]["per_element"]
        on_plate = [v for v, m in zip(f, self.mesh["element_member"]) if m.startswith("gusset_")]
        self.assertTrue(on_plate)
        self.assertEqual(set(on_plate), {0.0})
        self.assertIn("drawn at 0", " ".join(e["name"] for e in rec["effects"]))

    def test_gusset_mass_counted_once(self):
        from hiraku.contract import loading
        from hiraku.solvers.k0_handcalc.vehicle import mass_summary
        from hiraku.solvers.k1_shell.frame import analyze_shell
        d = loading.on_board(self.through, "two_cargo")
        res = analyze_shell(d, element_size_mm=20.0)
        summ = mass_summary(d)
        through = sum(p["mass_kg"] for p in d["parts"] if p["id"].startswith("through_"))
        self.assertGreater(through, 0.0)
        self.assertEqual(len([p for p in res["shell_parts"] if p.startswith("through_")]), 8)
        self.assertAlmostEqual(res["total_mass_kg"] - res["shell_mass_kg"],
                               summ["mass_kg"] - summ["frame_mass_kg"] - through, places=9)
        self.assertLess(res["residual_ratio"], 1e-6)
        self.assertFalse(res["excluded"][res["worst_element"]])
        g = loading.on_board(self.gusset, "two_cargo")
        gres = analyze_shell(g, element_size_mm=20.0)
        gsum = mass_summary(g)
        plates = sum(p["mass_kg"] for p in g["parts"] if p["kind"] == "gusset")
        self.assertAlmostEqual(gres["total_mass_kg"] - gres["shell_mass_kg"],
                               gsum["mass_kg"] - gsum["frame_mass_kg"] - plates, places=9)


if __name__ == "__main__":
    unittest.main()

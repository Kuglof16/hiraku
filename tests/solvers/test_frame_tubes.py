"""frame_tubes (shelf): the tube-by-tube shell with ties, judged on the IIW hot spot at the tie lines."""

import json
import os
import time
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract
from hiraku.designgen import space_frame_utv as sf
from hiraku.solvers.fe import tubes as tubes_mod
from hiraku.solvers.shelf.frame_tubes import FrameTubes, upstream_joint_loads

ROOT = Path(__file__).resolve().parents[2]
SLOW = unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "K2 runs and the shell: set HIRAKU_SLOW_TESTS=1")
PRED = [{"prediction_id": "pred-0001"}]
COARSE = {"element_size": {"value": 40.0, "unit": "mm"}}


def results(rec):
    return {r["quantity"]: r["value"] for r in rec["results"]}


class KnownAnswersTest(unittest.TestCase):
    def test_checks_pass_and_spec(self):
        tool = FrameTubes()
        t0 = time.perf_counter()
        checks = tool.check()
        self.assertLess(time.perf_counter() - t0, 60.0)
        for c in checks:
            self.assertTrue(c["pass"], c)
            self.assertEqual(contract.errors("tool_check", c), [])
        names = {c["quantity"] for c in checks}
        self.assertIn("max_hot_spot_stress (host side)", names)
        self.assertIn("largest tie gap (saddle on the facets)", names)
        spec = tool.spec()
        self.assertEqual(contract.errors("tool_spec", spec), [])
        self.assertTrue(spec["usable"])
        self.assertTrue(tool.needs_mesh)
        # applies_to and stage: always in describe(); in spec() when the contract's tool_spec has the fields
        desc = tool.describe()
        self.assertEqual(desc["applies_to"], ["space_frame_utv"])
        self.assertEqual(desc["stage"], 3)
        fields = contract.schema()["$defs"]["tool_spec"]["properties"]
        self.assertEqual("applies_to" in spec, "applies_to" in fields)
        self.assertEqual("stage" in spec, "stage" in fields)
        if "applies_to" in spec:
            self.assertEqual(spec["applies_to"], ["space_frame_utv"])


class SpaceFrameK0Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = sf.build()
        cls.rec = FrameTubes().run(cls.d, {"load_case_id": "R1t", "params": {}}, PRED, COARSE)

    def test_record(self):
        rec = self.rec
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        self.assertEqual(contract.errors("analysis", dict(rec, analysis_id="ana-0001")), [])
        res = results(rec)
        yield_mpa = self.d["materials"][sf.MATERIAL]["yield_MPa"]
        self.assertAlmostEqual(res["min_safety_factor"], yield_mpa / res["max_hot_spot_stress"], places=6)
        self.assertLess(res["load_balance_residual"], 1e-8)
        self.assertLess(res["equilibrium_residual"], 1e-8)
        joints = [v for v in rec["vectors"] if v["kind"] == "joint"]
        self.assertEqual(len(joints), 24)  # 6 mounts x 4 corners
        # standing still the mounts carry the sprung weight up (inertia relief takes the rest)
        self.assertGreater(sum(v["vector_N"][2] for v in joints), 0.0)
        labels = [f["label"] for f in rec["fields"]]
        self.assertEqual(len(labels), 2)
        self.assertTrue(any("max_hot_spot_stress" in a for a in rec["assumptions"]))
        # the facet count's bias (40 against 96 facets, up to 6.6 % low) is in every run's assumptions
        facet = [a for a in rec["assumptions"] if "96 角" in a]
        self.assertEqual(len(facet), 1)
        self.assertIn("6.6 %", facet[0])
        self.assertIn(f"{tubes_mod.HOT_SPOT_MIN_FACETS} 角", facet[0])
        self.assertEqual(tubes_mod.HOT_SPOT_MIN_FACETS, 40)  # the measured bias is of 40 facets
        self.assertNotIn("mesh", rec)  # run() does not return a mesh

    def test_the_hot_spot_is_judged_and_the_peak_is_a_reference(self):
        eff = " ".join(e["name"] for e in self.rec["effects"])
        self.assertIn("the quantity to judge", eff)
        self.assertIn("singular", eff)
        self.assertIn("K0 rigid-body statics at rest", eff)

    def test_a_ladder_is_refused_with_a_reason(self):
        lad = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
        tool = FrameTubes()
        why = tool.applies(lad)
        self.assertIn("立体フレーム (space_frame_utv) の設計に使います", why)
        self.assertIn("frame_shell を使ってください", why)
        rec = tool.run(lad, {"load_case_id": "R1t", "params": {}}, PRED, COARSE)
        self.assertEqual(rec["status"], "failed")
        self.assertEqual(rec["error"], why)
        self.assertLess(rec["wall_time_s"], 10.0)  # refused before any meshing
        self.assertIsNone(tool.applies(self.d))


class MeshRecordTest(unittest.TestCase):
    """fe.tubes.mesh_record: the contract mesh of a tube-by-tube shell (here a round T, tied)."""

    def test_round_t_record(self):
        from hiraku.solvers.fe import tubes
        from tests.solvers.test_fe_tubes import design, round_sec

        d = design({"A": (-300, 0, 0), "O": (0, 0, 0), "B": (300, 0, 0), "C": (0, 250, 0)},
                   [("h1", "A", "O", "h"), ("h2", "O", "B", "h"), ("br", "O", "C", "b")],
                   {"h": round_sec(42.7, 2.3), "b": round_sec(34.0, 2.3)})
        fs = tubes.frame_shells(d, 20.0)
        rec = dict(tubes.mesh_record(fs, d, "frame_tubes"), mesh_id="mesh-0001")
        self.assertEqual(contract.errors("mesh", rec), [])
        self.assertEqual((rec["mesher"], rec["element_type"]), ("frame_tubes", "shell"))
        self.assertEqual(len(rec["elements"]), sum(len(t.quads) for t in fs.tubes.values()))
        self.assertEqual(len(rec["element_member"]), len(rec["elements"]))
        self.assertEqual(set(rec["element_member"]), {"h1", "h2", "br"})
        self.assertEqual(rec["member_thickness_mm"]["br"], 2.3)
        ties = [c for c in rec["connections"] if c["kind"] == "tie"]
        self.assertEqual(len(ties), len(fs.ties))
        n = len(rec["nodes_mm"])
        for c in ties:
            self.assertTrue(all(0 <= i < n for i in c["nodes"]))
            self.assertTrue(all(len(f) == 4 for f in c["faces"]))
        self.assertNotEqual(rec["quality"]["verdict"], "fail")
        self.assertEqual(rec["quality"]["topology"].get("unconnected_groups", 0), 0)  # the tie joins the tubes


@SLOW
class MeshFlowTest(unittest.TestCase):
    """make_mesh once per version, run on params["mesh"]: the fields name it, the model is reused."""

    def test_make_mesh_and_two_load_cases(self):
        from hiraku.solvers.shelf import frame_tubes as ft

        tool = FrameTubes()
        d = sf.build()
        mesh = dict(tool.make_mesh(d), mesh_id="mesh-0002")
        self.assertEqual(contract.errors("mesh", mesh), [])
        self.assertEqual(mesh["mesher"], "frame_tubes")
        self.assertNotEqual(mesh["quality"]["verdict"], "fail")
        times = []
        for lc in ({"load_case_id": "a", "params": {}},
                   {"load_case_id": "b", "params": {"step_height": {"value": 100.0, "unit": "mm"}}}):
            t0 = time.perf_counter()
            out = tool.run(d, lc, PRED, {"mesh": mesh})
            times.append(time.perf_counter() - t0)
            self.assertEqual(out["status"], "ok", out.get("error"))
            self.assertNotIn("mesh", out)  # the mesh is not returned with the analysis
            self.assertEqual(contract.errors("analysis", dict(out, analysis_id="ana-0001")), [])
            on = [f for f in out["fields"] if f.get("mesh_id")]
            self.assertEqual({f["mesh_id"] for f in on}, {"mesh-0002"})
            for f in on:
                self.assertEqual(len(f["per_element"]), len(mesh["elements"]))
            self.assertTrue(any("96" in a for a in out["assumptions"]))  # the facet count's bias, every run
        # measured: 127 s, then 7 s (the second load case reuses the model and its factorisation)
        self.assertLess(times[1], 0.5 * times[0])
        self.assertIn(ft._digest(mesh), ft._PREPARED)

    def test_another_mesh_is_not_drawn_on(self):
        tool = FrameTubes()
        d = sf.build()
        other = {"mesh_id": "mesh-0001", "design_version": 1, "mesher": "frame_mesh", "element_type": "shell",
                 "nodes_mm": [], "elements": [], "element_member": []}
        out = tool.run(d, {"load_case_id": "a", "params": {}}, PRED, dict(COARSE, mesh=other))
        self.assertEqual(out["status"], "ok", out.get("error"))
        self.assertFalse(any(f.get("mesh_id") for f in out["fields"]))
        self.assertTrue(any("not by frame_tubes" in e["name"] for e in out["effects"]))


@SLOW
class SpaceFrameK2Test(unittest.TestCase):
    """K2's own load state, and K2's mount forces passed as an upstream analysis's joint vectors."""

    def test_k2_rest_agrees_with_k0_rest(self):
        d = sf.build()
        k0 = results(FrameTubes().run(d, {"load_case_id": "a", "params": {}}, PRED, COARSE))
        k2 = results(FrameTubes().run(d, {"load_case_id": "b", "params": {"load_source": "k2"}}, PRED, COARSE))
        # measured at 40 mm: 144.2 and 144.3 MPa (both at LH2)
        self.assertAlmostEqual(k0["max_hot_spot_stress"], k2["max_hot_spot_stress"],
                               delta=0.02 * k2["max_hot_spot_stress"])

    def test_upstream_joint_vectors(self):
        from hiraku.solvers.fe import tubes
        from hiraku.solvers.k2_vehicle.shelf.step import corner_mounts

        d = sf.build()
        st = tubes.k2_states(d)
        veh, s = st["veh"], st["static"]
        vectors = []
        body = np.asarray(s["rot"], float).T
        for i, c in enumerate(veh.corners):
            for mount, (p, f) in corner_mounts(veh, s, i).items():
                vectors.append({"kind": "joint", "at": f"{c.id}.{mount}", "origin_mm": [0.0, 0.0, 0.0],
                                "vector_N": (body @ np.asarray(f)).tolist()})
        up = {"P2": {"status": "ok", "vectors": vectors}}
        got = upstream_joint_loads(up, d)
        self.assertEqual(got[0], "P2")
        rec = FrameTubes().run(d, {"load_case_id": "R1t", "params": {}}, PRED, dict(COARSE, upstream_analyses=up))
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        eff = " ".join(e["name"] for e in rec["effects"])
        self.assertIn("upstream analysis P2", eff)
        k2 = results(FrameTubes().run(d, {"load_case_id": "b", "params": {"load_source": "k2"}}, PRED, COARSE))
        self.assertAlmostEqual(results(rec)["max_hot_spot_stress"], k2["max_hot_spot_stress"],
                               delta=0.03 * k2["max_hot_spot_stress"])


if __name__ == "__main__":
    unittest.main()

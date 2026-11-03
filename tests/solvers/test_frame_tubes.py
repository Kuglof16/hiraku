"""frame_tubes (shelf): the tube-by-tube shell with ties, judged on the IIW hot spot at the tie lines."""

import json
import os
import time
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract
from hiraku.designgen import space_frame_utv as sf
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

    def test_the_hot_spot_is_judged_and_the_peak_is_a_reference(self):
        eff = " ".join(e["name"] for e in self.rec["effects"])
        self.assertIn("the quantity to judge", eff)
        self.assertIn("singular", eff)
        self.assertIn("K0 rigid-body statics at rest", eff)

    def test_a_ladder_is_refused_with_a_reason(self):
        lad = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
        rec = FrameTubes().run(lad, {"load_case_id": "R1t", "params": {}}, PRED, COARSE)
        self.assertEqual(rec["status"], "failed")
        self.assertIn("no frame node", rec["error"])


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

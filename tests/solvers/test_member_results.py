"""Per-member results of frame_static and frame_shell: max_stress.<member> (and, on the shell,
max_hot_spot_stress.<member>) beside the overall values, the same as the per-member fields."""

import json
import unittest
from pathlib import Path

import numpy as np

from hiraku import contract, solvers
from hiraku.solvers.k1_shell import build_mesh
from hiraku.solvers.shelf import fixtures
from hiraku.solvers.shelf.frame_shell import LABEL_HOT_SPOT, LABEL_MEMBER, LABELS

ROOT = Path(__file__).resolve().parents[2]
LC = {"load_case_id": "R1-x", "test_id": "R1", "domain": "strength", "title": "R1",
      "params": {"step_height": {"value": 100.0, "unit": "mm"}}, "tolerance_rel": 0.15,
      "criterion": {"quantity": "max_stress", "op": "<=", "limit": {"value": 235.0, "unit": "MPa"}, "basis": "x"}}
COARSE = {"element_size": {"value": 20.0, "unit": "mm"}, "mesh_convergence": "off"}


def tool(name):
    return {t.name: t for t in solvers.builtin_tools()}[name]


def per(rec, quantity):
    """{member: (value, where)} of the results named <quantity>.<member>."""
    pre = quantity + "."
    return {r["quantity"][len(pre):]: (r["value"], r.get("where")) for r in rec["results"]
            if r["quantity"].startswith(pre)}


def value(rec, quantity):
    hits = [r for r in rec["results"] if r["quantity"] == quantity]
    assert len(hits) == 1, (quantity, hits)
    return hits[0]


class FrameStaticMembersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
        cls.rec = tool("frame_static").run(cls.design, LC, [{"prediction_id": "pred-0001"}], {})

    def test_one_result_per_member_equal_to_the_field(self):
        rec = self.rec
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        got = per(rec, "max_stress")
        field = next(f for f in rec["fields"] if f["kind"] == "stress" and "per_member" in f)["per_member"]
        self.assertEqual(set(got), {m["id"] for m in self.design["members"]})
        self.assertEqual(set(got), set(field))
        for m, (v, where) in got.items():
            self.assertEqual(where, m)
            self.assertAlmostEqual(v, field[m], places=9)
        top = value(rec, "max_stress")
        self.assertAlmostEqual(top["value"], max(v for v, _ in got.values()), places=9)
        self.assertEqual(top["where"], max(got, key=lambda m: got[m][0]))
        self.assertTrue(any(e["name"].startswith("max_stress per member") and top["where"] in e["name"]
                            for e in rec["effects"]))
        print(f"  frame_static demo: {len(self.design['members'])} members, {len(got)} max_stress.<member> results, "
              f"{len(rec['results'])} results in all")


class FrameShellMembersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design = fixtures.ladder_design()
        cls.rec = tool("frame_shell").run(cls.design, LC, [{"prediction_id": "pred-0001"}], dict(COARSE))

    def test_max_stress_per_member_as_the_member_field(self):
        rec = self.rec
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        contract.validate("analysis", dict(rec, analysis_id="ana-0001"))
        got = per(rec, "max_stress")
        field = next(f for f in rec["fields"] if f.get("label") == LABEL_MEMBER)["per_member"]
        self.assertTrue(set(field) <= set(got))
        for m, v in field.items():
            self.assertAlmostEqual(got[m][0], v, delta=0.006)  # the field is rounded to 0.01 MPa
            self.assertEqual(got[m][1], m)
        top = value(rec, "max_stress")
        self.assertAlmostEqual(top["value"], max(v for v, _ in got.values()), places=9)
        self.assertEqual(top["where"], max(got, key=lambda m: got[m][0]))
        # not to be confused with the overall result the criterion is judged on
        self.assertNotIn("max_stress_beam", got)

    def test_hot_spot_per_member_as_the_hot_spot_field(self):
        rec = self.rec
        got = per(rec, "max_hot_spot_stress")
        field = next(f for f in rec["fields"] if f.get("label") == LABEL_HOT_SPOT)["per_member"]
        self.assertTrue(got)
        self.assertEqual(set(got), set(field))
        for m, v in field.items():
            self.assertAlmostEqual(got[m][0], v, delta=0.006)
        top = value(rec, "max_hot_spot_stress")
        self.assertAlmostEqual(top["value"], max(v for v, _ in got.values()), places=9)
        self.assertEqual(top["where"], max(got, key=lambda m: got[m][0]))

    def test_mount_elements_left_out_as_in_max_stress(self):
        """max_stress.<member> is the largest outside the wheel mounts: the mesh field (mounts included) is at
        least as large, and the effects give the largest outside and inside the mounts."""
        demo = json.loads((ROOT / "data" / "designs" / "offroad-4w-v1.json").read_text(encoding="utf-8"))
        ms = build_mesh(demo, 20.0)  # the demo's wheel mounts; 20 mm keeps the solve short
        mesh = dict(ms.record("mesh-0001", 1))
        rec = tool("frame_shell").run(demo, LC, [{"prediction_id": "pred-0001"}], dict(COARSE, mesh=mesh))
        self.assertEqual(rec["status"], "ok", rec.get("error"))
        mx = np.array(next(f for f in rec["fields"] if f.get("surface") == "max")["per_element"])
        self.assertIn("車輪の取付の当て板", LABELS["max"])
        n_res = len(per(rec, "max_stress")) + len(per(rec, "max_hot_spot_stress"))
        print(f"  frame_shell demo at 20 mm: {len(demo['members'])} members, {n_res} per-member results "
              f"(max_stress.<id> {len(per(rec, 'max_stress'))}, max_hot_spot_stress.<id> "
              f"{len(per(rec, 'max_hot_spot_stress'))}), {len(rec['results'])} results in all, record "
              f"{len(json.dumps(rec)) / 1e6:.2f} MB")
        member = np.asarray(ms.element_member)
        inside = np.zeros(len(mx), bool)
        for el in ms.mount_elements.values():
            inside[el] = True
        self.assertTrue(inside.any())
        for m, (v, _) in per(rec, "max_stress").items():
            sel = (member == m) & ~inside
            self.assertAlmostEqual(float(mx[sel].max()), v, delta=0.006, msg=m)
        out_eff = next(e for e in rec["effects"] if e["name"].startswith("largest element stress outside"))
        in_eff = next(e for e in rec["effects"] if e["name"].startswith("largest element stress inside"))
        self.assertAlmostEqual(out_eff["value"], value(rec, "max_stress")["value"], places=9)
        self.assertAlmostEqual(in_eff["value"], float(mx[inside].max()), delta=0.006)
        self.assertAlmostEqual(float(mx.max()), max(out_eff["value"], in_eff["value"]), delta=0.006)

    def test_criterion_still_judged_on_the_overall_value(self):
        from hiraku.tools.functions import _pick_result

        hit = _pick_result(dict(self.rec, analysis_id="ana-0001"), "max_hot_spot_stress", None)
        self.assertEqual(hit["quantity"], "max_hot_spot_stress")
        hit = _pick_result(dict(self.rec, analysis_id="ana-0001"), "max_stress", LC["criterion"])
        self.assertEqual(hit["quantity"], "max_stress")


if __name__ == "__main__":
    unittest.main()

"""load_path's unsupported_load (contract 046c13c): a load point with no path to the frame is counted and named,
so the judge can fail the load case instead of waiting for a value that cannot come."""

import json
import unittest
from pathlib import Path

from hiraku import solvers

ROOT = Path(__file__).resolve().parents[2]
# version 1 before designgen joined every part in shape (kit/joints.py): the free parts these tests are about
V1 = json.loads((ROOT / "tests" / "solvers" / "data" / "offroad-4w-v1-unjoined.json").read_text(encoding="utf-8"))
TOOL = {t.name: t for t in solvers.builtin_tools()}["load_path"]


def run(kind: str, **force):
    lc = {"load_case_id": f"x-{kind}", "test_id": "R7", "domain": "strength", "title": "x", "tolerance_rel": 0.3,
          "params": {"load_kind": kind, "loading": "driver", **{k: {"value": v, "unit": "N"} for k, v in force.items()}}}
    rec = TOOL.run(V1, lc, [{"prediction_id": "pred-0001"}], {})
    return rec, {r["quantity"]: r for r in rec["results"]}


class UnsupportedLoadTest(unittest.TestCase):
    def test_floating_pedals(self):
        rec, r = run("foot", foot_force=500)
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(r["unsupported_load"]["value"], 2.0)
        self.assertEqual(r["unsupported_load"]["unit"], "1")
        self.assertEqual(r["unsupported_load"]["where"], "pedal_accel, pedal_brake")
        self.assertNotIn("pedal_deflection", r)
        self.assertTrue(any("質量は枠に載っていない" in a for a in rec["assumptions"]))

    def test_nothing_outboard_of_point_r(self):
        rec, r = run("push", push_force=725)
        self.assertEqual(rec["status"], "ok")
        self.assertEqual(r["unsupported_load"]["value"], r["occupant_push_open"]["value"])
        self.assertGreater(r["unsupported_load"]["value"], 0)

    def test_a_held_control_has_none(self):
        rec, r = run("hand", hand_force=300)
        self.assertNotIn("unsupported_load", r)
        self.assertIn("grip_deflection", r)


if __name__ == "__main__":
    unittest.main()

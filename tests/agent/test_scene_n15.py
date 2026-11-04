"""Video scene N15: the scripted run of data/requirements/offroad-4w-n15.yaml revises the demo
vehicle round by round (data/catalog/scripted.yaml, plan offroad-4w-n15). It ends stopped,
not_judged: on the last version every tile is green but strength, which stays pending because
one load case, R7-hand-300N, has a mismatch whose cause ends it (ends_case: no hand calculation
of this run reaches it). The red domains of each version are written below; when a tool, a
builder or the demo design changes them, this test says so."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# design version -> the domains whose tile is red on it (the last version has none). HK5, light first:
# each version stops at its first red stage and the designer revises before the heavier stages.
EXPECTED_REDS = {
    1: {"assembly"},    # stage 0: H1, H3 and H6 (the layout)
    2: {"strength"},    # stage 1: R8 and R7-foot (the load path and the controls)
    3: {"powertrain"},  # stage 2: P3 (vehicle dynamics)
    4: {"strength"},    # stage 3: R1s (the joint wall's hot spot on the shells)
    5: set(),
}
# the load cases of the last version left without a verdict, each ended by its cause (ends_case)
ENDED = {"R7-hand-300N"}
NOT_JUDGED_DOMAIN = "strength"  # their tile: pending, never green


@unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "the N15 scripted run takes about half an hour (HIRAKU_SLOW_TESTS=1)")
class SceneN15Test(unittest.TestCase):
    """The N15 scripted run with the real solvers."""

    def test_the_run_ends_green_but_the_ended_load_case(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, PYTHONUTF8="1")
            env.pop("ANTHROPIC_API_KEY", None)
            subprocess.run([sys.executable, "-m", "hiraku", "run", str(root / "data/requirements/offroad-4w-n15.yaml"),
                            "--scripted", "--runs", tmp], cwd=root, env=env, capture_output=True, timeout=5400)
            (run,) = [p for p in Path(tmp).iterdir() if p.is_dir()]
            events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
                      if line.strip()]
            project = json.loads((run / "project.json").read_text(encoding="utf-8"))

        finished = [e for e in events if e["kind"] == "run.finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual((finished[0]["data"]["status"], finished[0]["data"]["stop_code"]), ("stopped", "not_judged"))

        tiles: dict[tuple[int, str], str] = {}  # the newest state of each tile
        for e in events:
            if e["kind"] == "judgement.updated":
                tiles[(e["data"]["design_version"], e["data"]["domain"])] = e["data"]["state"]
        versions = sorted({v for v, _ in tiles})
        reds = {v: {d for (w, d), state in tiles.items() if w == v and state == "red"} for v in versions}
        self.assertEqual(reds, EXPECTED_REDS)

        last = versions[-1]
        data = [e["data"] for e in events if e.get("design_version") == last]
        verdicts = {d["load_case_id"] for d in data if "verdict_id" in d}
        # load cases with a criterion (P1 has none: it feeds R1), not past the limit
        cases = {lc["load_case_id"] for lc in project["load_cases"]
                 if lc.get("criterion") and not lc["load_case_id"].endswith("-beyond")}
        self.assertEqual(cases - verdicts, ENDED)
        # each has a cause with ends_case on the last version (through its comparison and prediction)
        lc_of_pred = {d["prediction_id"]: d["load_case_id"] for d in data if "formula_id" in d and "prediction_id" in d}
        lc_of_cmp = {d["comparison_id"]: lc_of_pred.get(d.get("prediction_id"))
                     for d in data if "comparison_id" in d and "status" in d}
        ended = {lc_of_cmp.get(d["comparison_id"]) for d in data if "cause_id" in d and d.get("ends_case") is True}
        self.assertEqual(ended & ENDED, ENDED)

        # every picked domain has a green tile on the last version, and so has every other tile of it (assembly),
        # but the domain of the ended load case, which stays pending
        picked = {d["domain"] for e in events if e["kind"] == "domains.picked" for d in e["data"]["picks"]}
        self.assertTrue(picked)
        shown = picked | {d for v, d in tiles if v == last}
        want = {d: ("pending" if d == NOT_JUDGED_DOMAIN else "green") for d in shown}
        self.assertEqual({d: tiles.get((last, d)) for d in shown}, want)


if __name__ == "__main__":
    unittest.main()

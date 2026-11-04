"""Video scene N15: the scripted run of data/requirements/offroad-4w-n15.yaml revises the demo
vehicle round by round (data/catalog/scripted.yaml, plan offroad-4w-n15). The vehicle is split into
modules: each round the module designers propose and the packager builds the version (contract
d9631a5). With the load cases built per version (H5-order counts the parts shut in on that
version's design), the plan's revisions do not fix assembly: from version 3 two parts cannot go
in (H5-order), and on version 5 the joints' reinforcement overlaps other parts (H1-interference).
The run uses its four revisions and stops at round_limit with assembly red; the heavier stages
stay unchecked on the last version (HK5). The red domains of each version and the red load cases
of the last are written below; when a tool, a builder or the demo design changes them, this test
says so."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# design version -> the domains whose tile is red on it. HK5, light first: each version stops at its
# first red stage and the designers revise before the heavier stages.
EXPECTED_REDS = {
    1: {"assembly"},  # stage 0: H1-motion, H1-steer, H3 and H6 (the layout)
    2: {"strength"},  # stage 1: R8 and R7-foot (no load path)
    3: {"assembly"},  # stage 0: H5-order, two parts shut in
    4: {"assembly"},  # the same
    5: {"assembly"},  # H5-order, and H1-interference (the joints' reinforcement)
}
LAST_RED_CASES = {"H1-interference", "H5-order"}  # the failing verdicts of the last version


@unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "the N15 scripted run takes about half an hour (HIRAKU_SLOW_TESTS=1)")
class SceneN15Test(unittest.TestCase):
    """The N15 scripted run with the real solvers."""

    def test_the_run_ends_at_the_round_limit_with_assembly_red(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, PYTHONUTF8="1")
            env.pop("ANTHROPIC_API_KEY", None)
            subprocess.run([sys.executable, "-m", "hiraku", "run", str(root / "data/requirements/offroad-4w-n15.yaml"),
                            "--scripted", "--runs", tmp], cwd=root, env=env, capture_output=True, timeout=5400)
            (run,) = [p for p in Path(tmp).iterdir() if p.is_dir()]
            events = [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
                      if line.strip()]

        finished = [e for e in events if e["kind"] == "run.finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual((finished[0]["data"]["status"], finished[0]["data"]["stop_code"]), ("stopped", "round_limit"))
        self.assertIn("HK5", finished[0]["data"]["stop_reason"])

        tiles: dict[tuple[int, str], str] = {}  # the newest state of each tile
        for e in events:
            if e["kind"] == "judgement.updated":
                tiles[(e["data"]["design_version"], e["data"]["domain"])] = e["data"]["state"]
        versions = sorted({v for v, _ in tiles})
        reds = {v: {d for (w, d), state in tiles.items() if w == v and state == "red"} for v in versions}
        self.assertEqual(reds, EXPECTED_REDS)

        last = versions[-1]
        failing = {e["data"]["load_case_id"] for e in events if e["kind"] == "verdict.recorded"
                   and e.get("design_version") == last and not e["data"]["pass"]}
        self.assertEqual(failing, LAST_RED_CASES)
        # each round after the first: the module designers proposed and the packager built the version
        built = [e for e in events if e["kind"] == "design.created" and e["data"]["design_version"] > 1]
        self.assertEqual(len(built), 4)
        self.assertTrue(all(e["agent"] == "packager" and e["data"].get("module_changes") for e in built))
        proposed = {e["agent"] for e in events if e["kind"] == "design.change_proposed"}
        self.assertTrue(proposed and all(a.startswith("designer.") for a in proposed))


if __name__ == "__main__":
    unittest.main()

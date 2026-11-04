"""Video scene N15: the scripted run of data/requirements/offroad-4w-n15.yaml revises the demo
vehicle round by round (data/catalog/scripted.yaml, plan offroad-4w-n15). The vehicle is split into
modules: each round the module designers propose and the packager builds the version (contract
d9631a5). With the load cases built per version (H5-order counts the parts shut in on that
version's design), version 3 has two parts that cannot go in (H5-order, routed to the packager),
and the plan's next revision (the motor) answers no red of that version: a round uses only the
modules the version's reds go to, so the run stops there, not judged, with assembly red and the
heavier stages unchecked (HK5). The red domains of each version and the red load cases of the
last are written below; when a tool, a builder or the demo design changes them, this test says so."""

import json
import os
import unittest
from pathlib import Path

# design version -> the domains whose tile is red on it. HK5, light first: each version stops at its
# first red stage and the designers revise before the heavier stages.
EXPECTED_REDS = {
    1: {"assembly"},  # stage 0: H1-motion, H1-steer, H3 and H6 (the layout)
    2: {"strength"},  # stage 1: R8 and R7-foot (no load path)
    3: {"assembly"},  # stage 0: H5-order, two parts shut in; the plan has no revision for it
}
LAST_RED_CASES = {"H5-order"}  # the failing verdicts of the last version


@unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "the N15 scripted run takes about half an hour (HIRAKU_SLOW_TESTS=1)")
class SceneN15Test(unittest.TestCase):
    """The N15 scripted run with the real solvers."""

    def test_the_run_ends_not_judged_with_assembly_red(self):
        from qa import shared_runs  # the N15 run made once per code version, shared with QA's run checks

        root = Path(__file__).resolve().parents[2]
        shared = shared_runs.scripted_run(root / "data/requirements/offroad-4w-n15.yaml")
        self.assertIsNotNone(shared.run_dir, shared.stderr[-2000:])
        events = [json.loads(line) for line in (shared.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
                  if line.strip()]

        finished = [e for e in events if e["kind"] == "run.finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual((finished[0]["data"]["status"], finished[0]["data"]["stop_code"]), ("stopped", "not_judged"))
        self.assertIn("H5-order", finished[0]["data"]["stop_reason"])
        self.assertIn("低い段の赤が残るので判定していない", finished[0]["data"]["stop_reason"])  # the contract's HK5 words

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
        self.assertEqual(len(built), 2)
        self.assertTrue(all(e["agent"] == "packager" and e["data"].get("module_changes") for e in built))
        proposed = {e["agent"] for e in events if e["kind"] == "design.change_proposed"}
        self.assertTrue(proposed and all(a.startswith("designer.") for a in proposed))


if __name__ == "__main__":
    unittest.main()

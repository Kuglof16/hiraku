"""Video scene N15: the scripted run of data/requirements/offroad-4w-n15.yaml revises the demo
vehicle round by round (data/catalog/scripted.yaml, plan offroad-4w-n15). The vehicle is split into
modules: each round the module designers propose and the packager builds the version (contract
d9631a5). On the joined design (the parts' fittings and the struts are in version 1), one revision
answers each version's red stage: the layout and a hand hold to get in by (H7-handhold), then the
opening the bolted side net shuts (H7-opening), then the controls (R7-hand red on the hand calculation's
upper bound, load_path.grip_bound: the towers and dash members thicker), then the motor (P3), then the
rails and gussets (R1s). Version 6 has every tile green: R7-hand and R7-foot pass on their bounds (safe
side), so the run ends done. The red domains of each version are written below; when a tool, a
builder or the demo design changes them, this test says so."""

import json
import os
import unittest
from pathlib import Path

# design version -> the domains whose tile is red on it. HK5, light first: each version stops at its
# first red stage and the designers revise before the heavier stages.
EXPECTED_REDS = {
    1: {"assembly"},  # stage 0: H1-motion, H1-steer, H3, H6 and H7-handhold (nothing to hold to get in)
    2: {"assembly"},  # stage 0: H7-opening (the bolted side net shuts the way in)
    3: {"strength"},  # stage 1: R7-hand (the grip's bound past 5 mm); R7-foot ends without a verdict
    4: {"powertrain"},  # stage 2: P3 (time to 40 km/h)
    5: {"strength"},  # stage 3: R1s (the hot spot at the rail joints)
    6: set(),
}
JUDGED_ON_LAST = ("R7-hand-300N", "R7-foot-500N")  # both judged on the last version, and passing


@unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "the N15 scripted run takes about half an hour (HIRAKU_SLOW_TESTS=1)")
class SceneN15Test(unittest.TestCase):
    """The N15 scripted run with the real solvers."""

    def test_the_run_ends_with_every_tile_green(self):
        from qa import shared_runs  # the N15 run made once per code version, shared with QA's run checks

        root = Path(__file__).resolve().parents[2]
        shared = shared_runs.scripted_run(root / "data/requirements/offroad-4w-n15.yaml")
        self.assertIsNotNone(shared.run_dir, shared.stderr[-2000:])
        events = [json.loads(line) for line in (shared.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
                  if line.strip()]

        finished = [e for e in events if e["kind"] == "run.finished"]
        self.assertEqual(len(finished), 1)
        self.assertEqual((finished[0]["data"]["status"], finished[0]["data"]["stop_code"]), ("done", "done"),
                         finished[0]["data"].get("stop_reason"))

        tiles: dict[tuple[int, str], str] = {}  # the newest state of each tile
        for e in events:
            if e["kind"] == "judgement.updated":
                tiles[(e["data"]["design_version"], e["data"]["domain"])] = e["data"]["state"]
        versions = sorted({v for v, _ in tiles})
        reds = {v: {d for (w, d), state in tiles.items() if w == v and state == "red"} for v in versions}
        self.assertEqual(reds, EXPECTED_REDS)

        last = versions[-1]
        self.assertEqual({d: s for (w, d), s in tiles.items() if w == last and s != "green"}, {})
        verdicts = {e["data"]["load_case_id"]: e["data"]["pass"] for e in events if e["kind"] == "verdict.recorded"
                    and e.get("design_version") == last}
        self.assertTrue(verdicts and all(verdicts.values()), verdicts)
        self.assertTrue(set(JUDGED_ON_LAST) <= set(verdicts), verdicts)
        # each round after the first: the module designers proposed and the packager built the version
        built = [e for e in events if e["kind"] == "design.created" and e["data"]["design_version"] > 1]
        self.assertEqual(len(built), 5)
        self.assertTrue(all(e["agent"] == "packager" and e["data"].get("module_changes") for e in built))
        proposed = {e["agent"] for e in events if e["kind"] == "design.change_proposed"}
        self.assertTrue(proposed and all(a.startswith("designer.") for a in proposed))


if __name__ == "__main__":
    unittest.main()

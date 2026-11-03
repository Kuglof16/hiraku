"""Video scene N15: the scripted run of data/requirements/offroad-4w-n15.yaml revises the demo
vehicle round by round (data/catalog/scripted.yaml, plan offroad-4w-n15) and ends done with every
picked domain green on the last version. The red domains of each version are written below; when
a tool, a builder or the demo design changes them, this test says so."""

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
    1: {"assembly"},    # stage 0: H1 and H3 (the layout)
    2: {"powertrain"},  # stage 2: P3 (vehicle dynamics), stages 0 and 1 green
    3: {"strength"},    # stage 3: R1s (the joint wall's hot spot on the shells), stages 0 to 2 green
    4: set(),
}


@unittest.skipUnless(os.environ.get("HIRAKU_SLOW_TESTS") == "1", "the N15 scripted run takes about half an hour (HIRAKU_SLOW_TESTS=1)")
class SceneN15Test(unittest.TestCase):
    """The N15 scripted run with the real solvers."""

    def test_the_run_ends_green(self):
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
        self.assertEqual((finished[0]["data"]["status"], finished[0]["data"]["stop_code"]), ("done", "done"))

        tiles: dict[tuple[int, str], str] = {}  # the newest state of each tile
        for e in events:
            if e["kind"] == "judgement.updated":
                tiles[(e["data"]["design_version"], e["data"]["domain"])] = e["data"]["state"]
        versions = sorted({v for v, _ in tiles})
        reds = {v: {d for (w, d), state in tiles.items() if w == v and state == "red"} for v in versions}
        self.assertEqual(reds, EXPECTED_REDS)

        last = versions[-1]
        # every picked domain has a green tile on the last version, and so has every other tile of it (assembly)
        picked = {d["domain"] for e in events if e["kind"] == "domains.picked" for d in e["data"]["picks"]}
        self.assertTrue(picked)
        shown = picked | {d for v, d in tiles if v == last}
        self.assertEqual({d: tiles.get((last, d)) for d in shown}, {d: "green" for d in shown})


if __name__ == "__main__":
    unittest.main()

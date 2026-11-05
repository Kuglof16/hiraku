"""k0_handcalc.frame_inputs gives the same numbers in every process: where an off-rail node is as near to two rail
nodes (antisymmetric_twist's rail_node), the choice does not follow the order of a set of node names, which changes
with PYTHONHASHSEED. On version 1, seeds 0 and 3 gave misfits differing by about 1e-6 of their size before."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODE = """
import json, sys
sys.path.insert(0, sys.argv[1])
from hiraku.solvers.k0_handcalc import frame_inputs
d = json.load(open(sys.argv[2], encoding="utf-8"))
print(json.dumps(frame_inputs.end_cross_member_inputs(d)["inputs"], sort_keys=True))
"""


def _inputs(seed: str) -> dict:
    env = dict(os.environ, PYTHONHASHSEED=seed)
    out = subprocess.run([sys.executable, "-c", CODE, str(ROOT), str(ROOT / "data" / "designs" / "offroad-4w-v1.json")],
                         env=env, capture_output=True, text=True, check=True, cwd=str(ROOT))
    return json.loads(out.stdout.strip().splitlines()[-1])


class HashSeedTest(unittest.TestCase):
    def test_same_inputs_whatever_the_seed(self):
        a = _inputs("0")
        self.assertIn("misfit_rear", a)
        for seed in ("1", "3"):
            self.assertEqual(_inputs(seed), a, f"PYTHONHASHSEED={seed}")


if __name__ == "__main__":
    unittest.main()

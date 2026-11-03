"""Write data/designs/offroad-4w-nodriveshaft.json: the demo vehicle missing a link of its drive.

Run from the repository root:  python data/designs/make_offroad_4w_nodriveshaft.py

hiraku.designgen.ladder_4w with its default variables except driveshafts 0: the
motor and the reducer are in place, but no driveshaft carries the torque on to
the driven wheels (the chain of torque from the motor ends at the reducer). The
shafts' mass stays in the reducer, so the vehicle's mass is that of v1. Used as
version 1 of a run in which the designer finds the missing system and adds it
back with revise_design (driveshafts 1, which builds v1).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hiraku.designgen.ladder_4w import build  # noqa: E402


def main() -> None:
    out = HERE / "offroad-4w-nodriveshaft.json"
    design = build({"driveshafts": {"value": 0, "unit": "1"}}, name="offroad-4w v1 without driveshafts",
                   reason="the demo vehicle with the driveshafts left out (variable driveshafts 0): the drive does not "
                          "reach the wheels")
    out.write_text(json.dumps(design, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()

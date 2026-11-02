"""Write data/designs/offroad-4w-v1.json, the fixed demo vehicle.

Run from the repository root:  python data/designs/make_offroad_4w_v1.py

The vehicle is hiraku.designgen.ladder_4w with its default variables: a small
two-seat electric off-road vehicle for forest roads, ladder frame of
rectangular steel tube, four wheels (front steer, rear drive), up to 40 km/h.
The variables, their ranges, and the sources and assumptions behind the
values are written there.
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
    out = HERE / "offroad-4w-v1.json"
    out.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()

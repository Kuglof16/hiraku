"""Write data/designs/offroad-4w-tallcargo.json: the demo vehicle with a tall load.

Run from the repository root:  python data/designs/make_offroad_4w_tallcargo.py

hiraku.designgen.ladder_4w with its default variables except cargo_height
1240 mm: the load's centre of gravity sits 620 mm above the deck. Used by the
runs where a requirement gives the load's height in the wrong unit.
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

CARGO_HEIGHT_MM = 1240.0  # a test value for those runs: an assumption, HIRAKU's choice, no source


def main() -> None:
    out = HERE / "offroad-4w-tallcargo.json"
    design = build({"cargo_height": {"value": CARGO_HEIGHT_MM, "unit": "mm"}})
    out.write_text(json.dumps(design, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()

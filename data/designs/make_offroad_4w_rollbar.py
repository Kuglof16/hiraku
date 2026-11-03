"""Write data/designs/offroad-4w-rollbar.json: the demo vehicle with a roll bar.

Run from the repository root:  python data/designs/make_offroad_4w_rollbar.py

hiraku.designgen.ladder_4w with its default variables except rollbar 1: the
roll bar takes its own variables' defaults (rollbar_height, rollbar_width,
rollbar_offset, a round tube rollbar_d x rollbar_t), and build_rollbar places
it behind the seats from the frame and the seats. Used by the roll-over (R4)
runs.
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
    out = HERE / "offroad-4w-rollbar.json"
    design = build({"rollbar": {"value": 1, "unit": "1"}}, name="offroad-4w v1 with a roll bar",
                   reason="the demo vehicle with a roll bar behind the seats (variables rollbar_*, their defaults)")
    out.write_text(json.dumps(design, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()

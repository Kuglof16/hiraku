"""Write screens/layout.data.js from screens/layout.json.

The screens open from file://, where a page cannot fetch a JSON file, so the
layout rules reach them as a script that sets ``window.PROMO_LAYOUT``.
layout.json is the only source; layout.data.js is generated, never edited.

    py promo/layout_build.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCREENS = Path(__file__).resolve().parent / "screens"
SOURCE = SCREENS / "layout.json"
TARGET = SCREENS / "layout.data.js"

HEADER = ("// Generated file: do not edit. Made by `py promo/layout_build.py` from layout.json;\n"
          "// edit layout.json and run it again (promo/tests/test_layout.py checks the two match).\n")


def load(path: Path = SOURCE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def render(layout: dict) -> str:
    """The text of layout.data.js for the given rules."""
    body = json.dumps(layout, ensure_ascii=False, indent=2)
    return f"{HEADER}window.PROMO_LAYOUT = {body};\n"


def parse(text: str) -> dict:
    """The rules inside a layout.data.js text (inverse of render)."""
    start = text.index("window.PROMO_LAYOUT = ") + len("window.PROMO_LAYOUT = ")
    return json.loads(text[start:].rstrip().rstrip(";"))


def main() -> int:
    TARGET.write_text(render(load()), encoding="utf-8", newline="\n")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

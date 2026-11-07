"""The material cards for the shelf's 材質 view (GET /api/materials).

The cards are data/materials/cards/<id>.json (the contract's material_card, one file per
material). They are read as they are, on every request (a handful of small files): nothing is
computed from them and nothing is filled in. A file that is not a card of the contract, or whose
name is not its id, is left out and named in "skipped" with the reason, so the screen can say
that a card could not be read rather than show it half.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_DIR = Path(__file__).resolve().parents[2]
CARDS_DIR = REPO_DIR / "data" / "materials" / "cards"


def material_cards(cards_dir: Path | str = CARDS_DIR) -> dict[str, Any]:
    """{"cards": [card, ...] in the order of the file names, "skipped": [{"file", "error"}, ...]}."""
    from hiraku import contract

    cards: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    root = Path(cards_dir)
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            skipped.append({"file": path.name, "error": f"unreadable: {e}"})
            continue
        errors = contract.errors("material_card", card)
        if errors:
            skipped.append({"file": path.name, "error": "; ".join(str(x) for x in errors)})
            continue
        if card["id"] != path.stem:
            skipped.append({"file": path.name, "error": f"the file is not named by its id {card['id']!r}"})
            continue
        cards.append(card)
    return {"cards": cards, "skipped": skipped}

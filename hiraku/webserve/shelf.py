"""The analysis tool shelf for the A4 screen: every builtin tool's spec and the K0
formulas, from the solvers' two entry points, and the names of the kinds of design
(hiraku.designgen.ARCHETYPES -> the generator's TITLE) that a tool's applies_to lists.

A tool's spec runs its known-answer checks, which takes tens of seconds for the whole
shelf, so it is computed once, in the background, on the first request; until then the
answer is {"ready": false} and the page asks again. Tools a run wrote come from that
run's tool.checked events and its tools/ folder, not from here.
"""

from __future__ import annotations

import threading
from typing import Any, Callable


def load_shelf() -> dict[str, Any]:
    from hiraku import designgen, solvers

    return {
        "tools": [tool.spec() for tool in solvers.builtin_tools()],
        "formulas": solvers.formula_registry().list_formulas(),
        "archetypes": archetype_titles(designgen.ARCHETYPES),
    }


def archetype_titles(archetypes: dict[str, Any]) -> dict[str, str]:
    """{kind of design: its generator's TITLE}, for the generators that have one."""
    return {name: gen.TITLE for name, gen in archetypes.items() if isinstance(getattr(gen, "TITLE", None), str)}


class Shelf:
    def __init__(self, load: Callable[[], dict[str, Any]] = load_shelf):
        self._load = load
        self._lock = threading.Lock()
        self._result: dict[str, Any] | None = None
        self._started = False

    def get(self) -> dict[str, Any]:
        with self._lock:
            if self._result is not None:
                return self._result
            if not self._started:
                self._started = True
                threading.Thread(target=self._fill, daemon=True).start()
        return {"ready": False}

    def compute(self) -> dict[str, Any]:
        """What get() answers once ready, computed here and now (blocking; the static export's
        shelf). A shelf already computed is answered as it is."""
        with self._lock:
            if self._result is not None:
                return self._result
            self._started = True
        self._fill()
        return self._result

    def _fill(self) -> None:
        try:
            result = {"ready": True, **self._load()}
        except Exception as e:  # noqa: BLE001 - the page shows why the shelf is missing
            result = {"ready": True, "error": f"{type(e).__name__}: {e}", "tools": [], "formulas": [], "archetypes": {}}
        with self._lock:
            self._result = result

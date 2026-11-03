"""In-process caches of the shell meshes and the factorised shell frames, keyed by the design's content.

A run builds the same mesh several times (the version's mesh record by solvers.frame_mesh, then
frame_shell at the same element size) and factorises the same frame for several load cases (R1
and R1s of one version). Both depend only on the design's frame: everything in the design but
its parts, and of the parts only the gussets (meshed as plates). The occupants and cargo that
on_board puts in or takes out do not change them. design_key() hashes exactly that.

HIRAKU_SHELL_CACHE=0 turns both caches off (every call builds and factorises again).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import OrderedDict
from typing import Any, Callable

MESHES = 4  # meshes kept (the demo frame's 10 mm mesh is about 30 MB in memory)
FRAMES = 1  # factorised frames kept
# A factorised frame is kept only when its estimated memory is at most FRAME_MAX_BYTES: about
# FRAME_BYTES_PER_ELEMENT of committed memory per element while it is kept (the demo frame at 10 mm: 1.8 GB
# for 49,612 elements, 1.56 GB of it the factors; measured here with hiraku.solvers.sparse_lu, an estimate
# for other frames). Larger frames are factorised for each use.
FRAME_MAX_BYTES = 2_000_000_000
FRAME_BYTES_PER_ELEMENT = 37_000


def keep_frame(n_elements: int) -> bool:
    return n_elements * FRAME_BYTES_PER_ELEMENT <= FRAME_MAX_BYTES


def enabled() -> bool:
    return os.environ.get("HIRAKU_SHELL_CACHE", "1") != "0"


def design_key(design: dict, *extra: Any) -> str:
    """Hash of what the shell mesh and model are made from: the design without its parts, the gusset
    parts, and `extra` (element size, weld strips)."""
    frame = {k: v for k, v in design.items() if k != "parts"}
    frame["gussets"] = [p for p in design.get("parts", []) if p.get("kind") == "gusset"]
    text = json.dumps([frame, list(extra)], sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class LRU:
    """A small least-recently-used map, safe to share between threads (one build per key at a time)."""

    def __init__(self, size: int):
        self.size = size
        self._d: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get_or_make(self, key: str, make: Callable[[], Any],
                    keep: Callable[[Any], bool] | None = None) -> tuple[Any, bool]:
        """(value, whether it came from the cache); a new value is kept only when keep(value) (default: always)."""
        with self._lock:
            if key in self._d:
                self._d.move_to_end(key)
                self.hits += 1
                return self._d[key], True
            self.misses += 1
            # the oldest go before the new one is made, so the cache never holds more than `size`
            while self._d and len(self._d) >= self.size:
                self._d.popitem(last=False)
            value = make()
            if self.size > 0 and (keep is None or keep(value)):
                self._d[key] = value
            return value, False

    def __len__(self) -> int:
        return len(self._d)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

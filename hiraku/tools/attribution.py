"""Which agent made a tool call.

An in-process MCP tool handler gets only its arguments. The PreToolUse hook
knows the caller (agent_type inside a subagent), so it pushes the agent here
under the tool name and the arguments, and the handler takes it back.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict, deque
from typing import Any


def _key(tool_name: str, args: dict[str, Any]) -> str:
    return tool_name + "\x00" + json.dumps(args, sort_keys=True, ensure_ascii=False)


class Attribution:
    def __init__(self) -> None:
        self._q: dict[str, deque[str]] = defaultdict(deque)
        self._lock = threading.Lock()

    def push(self, tool_name: str, args: dict[str, Any], agent: str) -> None:
        with self._lock:
            self._q[_key(tool_name, args)].append(agent)

    def take(self, tool_name: str, args: dict[str, Any]) -> str | None:
        with self._lock:
            q = self._q.get(_key(tool_name, args))
            return q.popleft() if q else None

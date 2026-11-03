"""Requests the screen puts for a running run: runs/<run_id>/inbox/<n>-<kind>.json (layout.INBOX_DIR).

Only the run writes events.jsonl, so the screen's server leaves one JSON file per
request, {"kind", "text", "file"}, and the run reads it where it looks for
stop.request, emits the events itself and removes the file. The files are read
in the order of <n>; a name that does not end in .json (a file still being
written, <n>-<kind>.json.tmp) is left for the next look. The screen numbers a
request as the largest <n> in the folder + 1, so a number comes back once the
folder is empty: the run does not remember the numbers it has read.

    message    {"kind": "message", "text": ...}: user.said, and the text goes to the lead
    knowledge  {"kind": "knowledge", "file": ..., "text"?: ...}: the file is knowledge/<file>;
               the text (if any) as user.said, then the intake's import_knowledge turns the
               file into rules (knowledge.imported); sentences that need a formula go to the lead

A request the run cannot read is named in an agent.said of system, and its file is removed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from hiraku.contract import layout
from hiraku.tools.context import RunContext, ToolError

Record = dict[str, Any]

NAME_RE = re.compile(r"^([0-9]+)-([a-z_]+)\.json$")


class InboxError(ValueError):
    pass


def _pending(folder: Path) -> list[Path]:
    found = [(int(m.group(1)), p) for p in folder.iterdir() if (m := NAME_RE.match(p.name))]
    return [p for _, p in sorted(found, key=lambda x: (x[0], x[1].name))]


def _message(ctx: RunContext, body: Record) -> list[str]:
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise InboxError("message needs text")
    ctx.store.emit("user.said", agent="user", text=text.strip())
    return [f"人からの伝言です: {text.strip()}"]


def _knowledge(ctx: RunContext, body: Record) -> list[str]:
    from hiraku.intake.tools import TOOLS

    name = body.get("file")
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise InboxError("knowledge needs file, a file name in knowledge/")
    rel = f"{layout.KNOWLEDGE_DIR}/{name}"
    if not (ctx.store.path / rel).is_file():
        raise InboxError(f"{rel} is not in the run folder")
    text = body.get("text")
    if isinstance(text, str) and text.strip():
        ctx.store.emit("user.said", agent="user", text=text.strip())
    got = TOOLS["import_knowledge"]["function"](ctx, {"path": rel}, "lead")
    if not got["rules"] and not got["needs_formula"]:
        ctx.store.emit("agent.said", agent="system", text=f"{name} から規則を読み取れませんでした")
    if not got["needs_formula"]:
        return []
    # the lead gets what import_knowledge answers Claude: the sentences, the value names, the categories
    detail = {k: got[k] for k in ("doc_id", "needs_formula", "facts", "categories") if k in got}
    return [f"渡されたナレッジ {name} に、式が要る文が {len(got['needs_formula'])} 件あります。"
            f"import_knowledge(path=\"{rel}\", formulas=..., skip=...) で、同じ path をもう一度呼んでください。"
            f"{got.get('next', '')}\n" + json.dumps(detail, ensure_ascii=False)]


HANDLERS = {"message": _message, "knowledge": _knowledge}


def take(ctx: RunContext) -> list[str]:
    """Read and remove every request in inbox/ (in <n> order), emitting its events.
    Returns what is to be told to the lead, in order."""
    folder = ctx.store.path / layout.INBOX_DIR
    if not folder.is_dir():
        return []
    for_lead: list[str] = []
    for p in _pending(folder):
        try:
            body = json.loads(p.read_text(encoding="utf-8"))
            kind = body.get("kind") if isinstance(body, dict) else None
            if kind not in HANDLERS:
                raise InboxError(f"kind must be one of {', '.join(HANDLERS)}, got {kind!r}")
            for_lead += HANDLERS[kind](ctx, body)
        except (InboxError, ToolError, OSError, json.JSONDecodeError) as e:
            ctx.store.emit("agent.said", agent="system", text=f"inbox の {p.name} を読めませんでした: {e}")
        except Exception as e:  # a bug behind one request must not end the run
            ctx.store.emit("agent.said", agent="system",
                           text=f"inbox の {p.name} を扱う途中で失敗しました ({type(e).__name__}: {e})")
        p.unlink(missing_ok=True)
    return for_lead

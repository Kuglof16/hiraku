"""HIRAKU as a Claude Code plugin (plugins/hiraku): the MCP server over stdio and the hook commands.

A person installs plugins/hiraku in their own Claude Code and drives a run from their own
session, under their own sign-in. HIRAKU does not call Claude on this path and does not start
Claude Code. project.driver is "claude_code_plugin".

- `python -m hiraku mcp` (serve): the "hiraku" MCP server over stdio, with the same tools as
  the Agent SDK run (names, schemas, descriptions and handlers of hiraku.tools.mcp), plus
  start_run and open_run. It is the run process: it alone writes events.jsonl. The current
  run is named in <runs>/.plugin_current.json (CURRENT_FILE) for the hook commands.
- `python -m hiraku hook <Event>` (run_hook): one process per hook call. It reads the hook
  input on stdin, opens the current run for reading and answers with the decisions of
  hiraku.agent.hooks.Hooks (the same code as the SDK run). It never writes events.jsonl: what
  the SDK hooks emit (hook.denied, hook.blocked_stop, agent.started, agent.stopped), the
  caller's agent for the next tool call (Attribution) and "the lead's stop was let through"
  go as notes into the run folder's OUTBOX_FILE, and the server turns them into events (at
  the start of every tool call, and every POLL_S). What the hooks keep between calls (stop
  blocks, running roles, what the screen sent for the lead) is in STATE_FILE, with the load cases
  no K0 formula can predict (worked out by the server after each tool call, UNPREDICTABLE_KEY).

The run folder lock (LOCK_FILE) is taken by the hook command and by the server around the
state and outbox files.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from hiraku import contract
from hiraku.agent import inbox, prompts
from hiraku.agent.hooks import Hooks
from hiraku.catalog import REPO_ROOT
from hiraku.contract import layout
from hiraku.store import RunStore
from hiraku.tools import Attribution, RunContext, ToolError

Record = dict[str, Any]

DRIVER = "claude_code_plugin"
CURRENT_FILE = ".plugin_current.json"  # in the runs folder: {"run_dir": ...} of the server's current run
STATE_FILE = layout.PLUGIN_HOOKS_FILE  # in the run folder: Hooks.state() between hook calls
OUTBOX_FILE = layout.PLUGIN_OUTBOX_FILE  # in the run folder: notes from the hook commands for the server
LOCK_FILE = layout.PLUGIN_LOCK_FILE  # in the run folder, while the state or the outbox is read or written
LOCK_TIMEOUT_S = 10.0
LOCK_STALE_S = 30.0  # a lock older than this was left by a process that died
POLL_S = 1.0  # how often the server reads the outbox, the inbox and stop.request
# in STATE_FILE, written by the server: rules.unpredictable_cases of the run (the hook command has no registry)
UNPREDICTABLE_KEY = "unpredictable"

STOPPED_SESSION_CLOSED = "Claude Code のセッションが閉じたので、実行を止めました"
STOPPED_NEW_RUN = "同じセッションで新しい実行を始めたので、この実行は止めました"
NO_RUN = "実行がありません。start_run(requirements_file=...) で実行を始めるか、open_run(run_id=...) で走っている実行を開いてください"


def default_runs() -> Path:
    return REPO_ROOT / layout.RUNS_DIR


# ---------------------------------------------------------------------------- files


@contextmanager
def locked(run_dir: Path, timeout: float = LOCK_TIMEOUT_S) -> Iterator[None]:
    """Hold the run folder's plugin lock (a file made with O_EXCL; works across processes)."""
    path = Path(run_dir) / LOCK_FILE
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > LOCK_STALE_S:
                    path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"{path} is held by another process") from None
            time.sleep(0.01)
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    for _ in range(50):  # Windows: a reader may hold the target open for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02)
    os.replace(tmp, path)


def current_run(runs: Path) -> Path | None:
    """The run folder the server named as current, or None."""
    got = _read_json(Path(runs) / CURRENT_FILE, None)
    if not isinstance(got, dict) or not got.get("run_dir"):
        return None
    path = Path(got["run_dir"])
    return path if (path / layout.PROJECT_FILE).is_file() else None


def set_current(runs: Path, run_dir: Path) -> None:
    Path(runs).mkdir(parents=True, exist_ok=True)
    _write_json(Path(runs) / CURRENT_FILE, {"run_dir": str(Path(run_dir).resolve()), "server_pid": os.getpid()})


def read_state(run_dir: Path) -> Record:
    return _read_json(Path(run_dir) / STATE_FILE, {})


def write_state(run_dir: Path, state: Record) -> None:
    _write_json(Path(run_dir) / STATE_FILE, state)


def put_notes(run_dir: Path, notes: list[Record]) -> None:
    """Append notes for the server (hold the lock)."""
    if notes:
        with (Path(run_dir) / OUTBOX_FILE).open("a", encoding="utf-8") as f:
            for n in notes:
                f.write(json.dumps(n, ensure_ascii=False) + "\n")


def take_notes(run_dir: Path) -> list[Record]:
    """Read and remove the hook commands' notes (hold the lock)."""
    path = Path(run_dir) / OUTBOX_FILE
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    path.unlink()
    return [json.loads(line) for line in lines if line.strip()]


def specs_from_events(events: list[Record]) -> dict[str, Record]:
    """The tool shelf as checked in this run: the last tool.checked per tool."""
    return {e["data"]["name"]: e["data"] for e in events if e["kind"] == "tool.checked"}


def open_store(run_dir: Path, tries: int = 5) -> RunStore:
    """RunStore.open, again if the server was half-way through writing a line or a file."""
    for i in range(tries):
        try:
            return RunStore.open(run_dir)
        except (ValueError, OSError):
            if i == tries - 1:
                raise
            time.sleep(0.05)
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------- hook command


class _NoteAttribution:
    """The hooks' Attribution.push, as a note: the server pushes it into its own Attribution."""

    def __init__(self, notes: list[Record]) -> None:
        self.notes = notes

    def push(self, tool_name: str, args: Record, agent: str) -> None:
        self.notes.append({"op": "attribute", "tool": tool_name, "args": args, "agent": agent})


HOOK_EVENTS = ("PreToolUse", "PostToolUse", "Stop", "SubagentStart", "SubagentStop")


def run_hook(event: str, input_data: Record, runs: Path | None = None) -> Record:
    """The answer of one hook command: Hooks.decide on the current run, read only.
    {} (go ahead) when there is no current run or it has ended."""
    if event not in HOOK_EVENTS:
        raise ValueError(f"unknown hook event {event!r} (one of {', '.join(HOOK_EVENTS)})")
    run_dir = current_run(runs or default_runs())
    if run_dir is None:
        return {}
    with locked(run_dir):
        store = open_store(run_dir)
        if store.project["status"] != "running":
            return {}
        state = read_state(run_dir)
        # no registry here: the load cases no K0 formula can predict are the server's (UNPREDICTABLE_KEY)
        ctx = RunContext(store=store, formulas=None, tools={}, specs=specs_from_events(store.events()),
                         unpredictable=state.get(UNPREDICTABLE_KEY))
        notes: list[Record] = []

        def emit(kind: str, **kw: Any) -> None:
            notes.append({"op": "emit", "kind": kind, **kw})

        hooks = Hooks(ctx, _NoteAttribution(notes), emit=emit)  # type: ignore[arg-type]
        hooks.load_state(state)
        hooks.on_end = lambda: notes.append({"op": "end"})
        out = hooks.decide(event, input_data)
        write_state(run_dir, {**state, **hooks.state()})
        put_notes(run_dir, notes)
    return out


def hook_main(event: str, runs: Path | None = None, stdin: Any = None, stdout: Any = None) -> int:
    """`python -m hiraku hook <Event>`: hook input JSON on stdin, the answer as JSON on stdout.
    A failure lets the call through (exit 0, {}) and says why on stderr: the tools check
    HK1-HK3 again themselves (hiraku.tools.functions._gate). Claude Code sends UTF-8; the
    process's own stdin encoding (cp932 on a Japanese Windows) is not used, or a Japanese
    argument would not match the server's Attribution key."""
    stdout = stdout or sys.stdout
    try:
        raw = stdin.read() if stdin is not None else sys.stdin.buffer.read().decode("utf-8")
        out = run_hook(event, json.loads(raw) if raw.strip() else {}, runs)
    except Exception as e:  # never break the person's session over a hook
        print(f"hiraku hook {event}: {type(e).__name__}: {e}", file=sys.stderr)
        out = {}
    stdout.write(json.dumps(out, ensure_ascii=False))
    stdout.flush()
    return 0


# ---------------------------------------------------------------------------- the server


START_RUN_SCHEMA: Record = {
    "type": "object", "additionalProperties": False, "required": ["requirements_file"],
    "properties": {
        "requirements_file": {"type": "string", "description": "要件のファイル (例 data/requirements/offroad-4w.yaml、ファイル名だけでもよい)"},
        "design_file": {"type": "string", "description": "最初の設計のファイル (無ければ要件の design_file、それも無ければ設計役が版 1 を作る)"},
    },
}
OPEN_RUN_SCHEMA: Record = {
    "type": "object", "additionalProperties": False, "required": ["run_id"],
    "properties": {"run_id": {"type": "string", "description": "走っている (status running の) 実行の ID"}},
}
OWN_TOOLS: dict[str, tuple[str, Record]] = {
    "start_run": (
        "HIRAKU の実行を始める。要件のファイルから実行のフォルダを作り (分野選び・ヒアリング・ナレッジは CLI と同じ)、"
        "道具の棚を確かめ、この会話の実行にする。ほかの道具はこの実行に働く。返る task がまとめ役の最初の仕事",
        START_RUN_SCHEMA,
    ),
    "open_run": (
        "走っている実行 (サーバーが立ち直したときなど) を開き直して、この会話の実行にする。返る task がまとめ役の仕事",
        OPEN_RUN_SCHEMA,
    ),
}


def resolve_requirements(name: str) -> Path:
    """A requirements file as given, else under the repository or data/requirements/."""
    for p in (Path(name), REPO_ROOT / name, REPO_ROOT / "data" / "requirements" / name):
        if p.is_file():
            return p
    have = ", ".join(sorted(p.name for p in (REPO_ROOT / "data" / "requirements").glob("*.yaml")))
    raise ToolError(f"要件のファイル {name!r} がありません。data/requirements/ にあるのは: {have}")


class PluginServer:
    """The run process of the plugin path: the current run, the hook commands' notes, the tools."""

    def __init__(self, runs: Path | None = None, *, load: Any = None) -> None:
        self.runs = Path(runs or default_runs())
        self.ctx: RunContext | None = None
        self.attribution = Attribution()
        self._lock = threading.RLock()
        self._load = load  # tests: a function returning (formulas, tools) instead of hiraku.solvers
        self._poller: threading.Thread | None = None
        self._stop_polling = threading.Event()

    # ------------------------------------------------------------------ tools

    def tool_table(self) -> list[tuple[str, str, Record]]:
        """(name, description, input schema) of every tool, the run's from hiraku.tools.mcp."""
        from hiraku.tools.functions import RUN_TOOLS
        from hiraku.tools.mcp import DESCRIPTIONS, INPUT_SCHEMAS

        own = [(n, d, s) for n, (d, s) in OWN_TOOLS.items()]
        return own + [(n, DESCRIPTIONS[n], INPUT_SCHEMAS[n]) for n in RUN_TOOLS]

    async def call_tool(self, name: str, args: Record) -> Record:
        """{"content": [{"type": "text", "text": ...}], "is_error"?} as hiraku.tools.mcp answers."""
        from hiraku.tools.mcp import HANDLERS, make_handler

        await asyncio.to_thread(self.drain)
        if name in OWN_TOOLS:
            try:
                got = await asyncio.to_thread(self.start if name == "start_run" else self.open, args)
            except ToolError as e:
                return {"content": [{"type": "text", "text": str(e)}], "is_error": True}
            return {"content": [{"type": "text", "text": json.dumps(got, ensure_ascii=False)}]}
        if name not in HANDLERS:
            return {"content": [{"type": "text", "text": f"道具 {name!r} はありません"}], "is_error": True}
        ctx = self.ctx
        if ctx is None or ctx.project["status"] != "running":
            return {"content": [{"type": "text", "text": NO_RUN}], "is_error": True}
        out = await make_handler(ctx, self.attribution, name, HANDLERS[name])(args)
        await asyncio.to_thread(self.note_unpredictable, ctx)  # a version, a prediction or a verdict changes it
        return out

    # ------------------------------------------------------------------ runs

    def _solvers(self) -> tuple[Any, Any]:
        if self._load is not None:
            return self._load()
        from hiraku.tools import load_solvers

        return load_solvers()

    def _answer(self, ctx: RunContext) -> Record:
        return {"run_id": ctx.project["run_id"], "run_dir": str(ctx.store.path.resolve()),
                "design_version": ctx.version, "driver": ctx.project.get("driver"),
                "screen": f"画面 (python -m hiraku.webserve) の ?run={ctx.project['run_id']} で見られます",
                "task": prompts.lead_task(ctx)}

    def start(self, args: Record) -> Record:
        from hiraku.agent.session import start_run
        from hiraku.catalog import CatalogError, load_requirements
        from hiraku.tools import SolversMissing

        req_path = resolve_requirements(str(args.get("requirements_file") or ""))
        design = args.get("design_file")
        if design and not Path(design).is_file():
            design = str(REPO_ROOT / design)
        formulas, tools = self._solvers()
        try:
            slug = load_requirements(req_path)["product"]
            ctx = start_run(req_path, design_path=design, root=self.runs, slug=slug, formulas=formulas,
                            tools=tools, intake=True, driver=DRIVER)
        except (FileNotFoundError, SolversMissing, CatalogError, ValueError) as e:
            raise ToolError(f"実行を始められません: {e}") from None
        with self._lock:
            if self.ctx is not None and self.ctx.project["status"] == "running":
                self.close("stopped", STOPPED_NEW_RUN, "user_stop")
            self.let_go(self.ctx)
            self.ctx = ctx
            self.attribution = Attribution()
            ctx.store.emit("agent.started", agent="lead", data={"agent_id": "lead"})
            set_current(self.runs, ctx.store.path)
            self.touch_alive(ctx)
        self.note_unpredictable(ctx)
        return self._answer(ctx)

    def open(self, args: Record) -> Record:
        run_id = str(args.get("run_id") or "")
        try:
            path = layout.run_dir(run_id, self.runs)
        except ValueError as e:
            raise ToolError(str(e)) from None
        if not (path / layout.PROJECT_FILE).is_file():
            raise ToolError(f"実行 {run_id} は {self.runs} にありません")
        store = open_store(path)
        if store.project["status"] != "running":
            raise ToolError(f"実行 {run_id} は終わっています (status {store.project['status']})。start_run で新しく始めてください")
        formulas, tools = self._solvers()
        from hiraku.tools import load_designgen

        ctx = RunContext(store=store, formulas=formulas, tools=dict(tools), specs=specs_from_events(store.events()),
                         designgen=load_designgen())
        with self._lock:
            if self.ctx is not None and self.ctx is not ctx and self.ctx.project["status"] == "running" \
                    and self.ctx.store.path.resolve() != path.resolve():
                self.close("stopped", STOPPED_NEW_RUN, "user_stop")
            if self.ctx is not None and self.ctx.store.path.resolve() != path.resolve():
                self.let_go(self.ctx)
            self.ctx = ctx
            self.attribution = Attribution()
            set_current(self.runs, path)
            self.touch_alive(ctx)
        self.note_unpredictable(ctx)
        return self._answer(ctx)

    @staticmethod
    def touch_alive(ctx: RunContext | None) -> None:
        """layout.PLUGIN_ALIVE_FILE: this server serves the run (the screen holds a plugin run while the
        file is younger than layout.PLUGIN_ALIVE_S); touched at start and on every poll."""
        if ctx is not None and ctx.project["status"] == "running":
            (ctx.store.path / layout.PLUGIN_ALIVE_FILE).touch()

    @staticmethod
    def let_go(ctx: RunContext | None) -> None:
        """The server no longer serves the run: its alive file goes."""
        if ctx is not None:
            (ctx.store.path / layout.PLUGIN_ALIVE_FILE).unlink(missing_ok=True)

    def close(self, status: str, reason: str | None, code: str) -> None:
        """End the current run: roles still running stop, then the lead, then run.finished; the
        server lets go of it (its alive file goes)."""
        with self._lock:
            ctx = self.ctx
            self.let_go(ctx)
            if ctx is None or ctx.project["status"] != "running":
                return
            run_dir = ctx.store.path
            with locked(run_dir):
                state = read_state(run_dir)
            for agent in sorted(set((state.get("running") or {}).values())):
                domain = agent.split(".", 1)[1] if agent.startswith("cae.") else None
                ctx.store.emit("agent.stopped", agent=agent, domain=domain, data={"agent_id": agent})
            ctx.store.emit("agent.stopped", agent="lead", data={"agent_id": "lead"})
            ctx.store.finish(status, stop_reason=reason, stop_code=code)

    def end(self) -> None:
        """The Stop hook let the lead stop: the run ends as the SDK run does (runner.end_status)."""
        from hiraku.agent.runner import end_status
        from hiraku.agent.session import STOPPED_BY_BUTTON

        ctx = self.ctx
        if ctx is None or ctx.project["status"] != "running":
            return
        if ctx.store.stop_requested():
            self.close("stopped", STOPPED_BY_BUTTON, "user_stop")
            ctx.store.clear_stop_request()
            return
        with locked(ctx.store.path):
            blocks = int(read_state(ctx.store.path).get("stop_blocks") or 0)
        self.close(*end_status(ctx, blocks))

    def note_unpredictable(self, ctx: RunContext) -> None:
        """Leave rules.unpredictable_cases in the hooks' state for the hook command (which has no
        registry), when it changed."""
        from hiraku.tools import rules

        if ctx.formulas is None or ctx.project["status"] != "running":
            return
        try:
            cases = rules.unpredictable_cases(ctx)
        except Exception as e:  # the hooks then hold the stop as before (no load case set aside)
            print(f"hiraku mcp: unpredictable_cases: {type(e).__name__}: {e}", file=sys.stderr)
            return
        with locked(ctx.store.path):
            state = read_state(ctx.store.path)
            if state.get(UNPREDICTABLE_KEY) != cases:
                write_state(ctx.store.path, {**state, UNPREDICTABLE_KEY: cases})

    # ------------------------------------------------------------------ notes, inbox, stop button

    def drain(self) -> None:
        """Turn the hook commands' notes into events (and the callers' agents into Attribution)."""
        with self._lock:
            ctx = self.ctx
            if ctx is None or ctx.project["status"] != "running":
                return
            with locked(ctx.store.path):
                notes = take_notes(ctx.store.path)
            for n in notes:
                op = n.get("op")
                if op == "emit":
                    ctx.store.emit(n["kind"], agent=n.get("agent"), domain=n.get("domain"), text=n.get("text"),
                                   data=n.get("data"))
                elif op == "attribute":
                    self.attribution.push(n["tool"], n.get("args") or {}, n["agent"])
                elif op == "end":
                    self.end()

    def poll_once(self) -> None:
        """Notes, then the stop button (the run ends; the hooks let everything through after),
        then the inbox (its events, and what is for the lead to the hooks' state); the alive file
        is touched while the run goes on."""
        from hiraku.agent.session import STOPPED_BY_BUTTON

        self.drain()
        with self._lock:
            ctx = self.ctx
            if ctx is None or ctx.project["status"] != "running":
                return
            self.touch_alive(ctx)
            if ctx.store.stop_requested():
                ctx.store.emit("agent.said", agent="system", text="止めるボタンが押されたので、実行を止めます")
                self.close("stopped", STOPPED_BY_BUTTON, "user_stop")
                ctx.store.clear_stop_request()
                return
            told = inbox.take(ctx)
            if told:
                with locked(ctx.store.path):
                    state = read_state(ctx.store.path)
                    state["for_lead"] = list(state.get("for_lead") or []) + told
                    write_state(ctx.store.path, state)

    def start_polling(self, every_s: float = POLL_S) -> None:
        def loop() -> None:
            while not self._stop_polling.wait(every_s):
                try:
                    self.poll_once()
                except Exception as e:  # a bad note or file must not stop the server
                    print(f"hiraku mcp: poll: {type(e).__name__}: {e}", file=sys.stderr)

        self._poller = threading.Thread(target=loop, name="hiraku-plugin-poll", daemon=True)
        self._poller.start()

    def shutdown(self) -> None:
        """The session closed (stdin ended): read the last notes, then stop a run still running."""
        self._stop_polling.set()
        try:
            self.drain()
        finally:
            self.close("stopped", STOPPED_SESSION_CLOSED, "user_stop")


def serve(runs: Path | None = None) -> int:
    """`python -m hiraku mcp`: the MCP server over stdio (the mcp package's lowlevel Server)."""
    import anyio
    import mcp_types as types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    plugin = PluginServer(runs)

    async def list_tools(ctx: Any, params: Any) -> Any:
        return types.ListToolsResult(tools=[types.Tool(name=n, description=d, input_schema=s)
                                            for n, d, s in plugin.tool_table()])

    async def call_tool(ctx: Any, params: Any) -> Any:
        out = await plugin.call_tool(params.name, dict(params.arguments or {}))
        return types.CallToolResult(content=[types.TextContent(text=c["text"]) for c in out["content"]],
                                    is_error=bool(out.get("is_error")))

    server = Server(contract.MCP_SERVER, version="0.1.0", on_list_tools=list_tools, on_call_tool=call_tool)

    async def main() -> None:
        plugin.start_polling()
        try:
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())
        finally:
            plugin.shutdown()

    anyio.run(main)
    return 0

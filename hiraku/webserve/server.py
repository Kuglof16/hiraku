"""A small HTTP server for the screen: serves web/ and reads runs/<run_id>/events.jsonl.

Standard library only. Routes:

    GET /                               web/index.html
    GET /<path>                         a file under web/
    GET /api/runs                       list of runs: saved runs, then runs/ (newest first),
                                        then the contract sample; one may be marked default
    GET /api/runs/<run_id>/events?offset=N
                                        complete lines of events.jsonl from byte N on,
                                        parsed; next_offset tells where to read next
    GET /api/runs/<run_id>/events?offset=N&format=ndjson
                                        the same lines' bytes as they are (application/x-ndjson,
                                        not parsed); next offset, size, reset and the lines
                                        skipped for length in X-Hiraku-* headers
    GET /api/launch, POST /api/launch   start a run from the screen (launch.py); GET also lists
                                        the demo runs of data/catalog/demo_runs.yaml
    POST /api/launch/stop               ask that run to stop (stop.request in its folder)
    POST /api/launch {request, mode}    start a run from a request settled in A1/A2 (--intake-events)
    POST /api/request, /api/request/answer
                                        A1/A2 before a run (hiraku.intake.request): the domains, the
                                        hearing, then the request's files under runs/_requests/<id>/
    GET /layout.json                    the video's layout rules (promo/screens/layout.json,
                                        the promo department's), read by the cut pages
    GET /pets/pet3d.js                  the promo department's 3D pets (promo/pets/pet3d.js,
                                        read only, passed through as it is); 404 without the
                                        file, and the cut pages then draw no pets
    GET /api/shelf                      the tool shelf for A4: every builtin tool's spec and
                                        the K0 formulas ({"ready": false} while computed, shelf.py)
    GET /api/runs/<run_id>/tools/<file> a tool a run wrote (tools/<name>-<n>.py) or its checks
                                        (tools/<name>-<n>.check.json)
    GET /api/runs/<run_id>/tools        the tools a run wrote: {"tools": {name: [n, ...]}} from
                                        the names of its tools/<name>-<n>.py (empty without tools/)
    GET /api/formulas                  the K0 formulas' ids and Japanese titles (formula_titles)
    GET /api/modules                    the contract's module ids, MODULE_NAMES_JA and the
                                        module designers (web/js/modules.js)

Saved runs are run folders kept in the repository (saved_dir/<run_id>/, the
same layout as runs/) so a fresh checkout has something to replay. The screen
opens on the run list when the URL names no run; the list recommends the
default run (おすすめ): the run given as default_run, else the newest saved run.

The screen draws only from events, so the server never interprets them beyond
parsing JSON lines. A line still being written (no trailing newline) is left
for the next read. A line longer than MAX_LINE_BYTES is skipped (counted in bad_lines).
"""

from __future__ import annotations

import json
import mimetypes
import re
import socket
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from hiraku.contract import EXAMPLES_DIR
from hiraku.contract.layout import EVENTS_FILE, JUDGEMENTS_FILE, PROJECT_FILE, RUN_ID_RE, SHOWCASE_DIR, TOOLS_DIR
from hiraku.webserve.launch import DEMO_RUNS_FILE, Launcher, LaunchError
from hiraku.webserve.shelf import Shelf

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
REPO_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_DIR / "web"
SAVED_DIR = REPO_DIR / SHOWCASE_DIR  # saved runs kept in the repository
LAYOUT_FILE = REPO_DIR / "promo" / "screens" / "layout.json"  # the video's layout rules (promo's)
PETS_FILE = REPO_DIR / "promo" / "pets" / "pet3d.js"  # the 3D pets (promo's), served at PETS_PATH
PETS_PATH = "/pets/pet3d.js"
SAMPLE_ID = "sample"
# project.driver's values (hiraku.schema.json project.driver, from claude/lead-refactor 75f5484)
DRIVERS = ("scripted", "agent_sdk", "claude_code_plugin")
SAMPLE_EVENTS = EXAMPLES_DIR / "events.sample.jsonl"

# Windows can map .js to text/plain through the registry, which breaks ES modules.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".jsonl": "application/x-ndjson; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".hdr": "application/octet-stream",
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}

_EVENTS_PATH_RE = re.compile(r"^/api/runs/([^/]+)/events$")
_TOOL_FILE_RE = re.compile(r"^/api/runs/([^/]+)/tools/([a-z0-9_]+-[0-9]+(?:\.py|\.check\.json))$")
_TOOL_LIST_RE = re.compile(r"^/api/runs/([^/]+)/tools/?$")
_TOOL_NAME_RE = re.compile(r"^([a-z0-9_]+)-([0-9]+)\.py$")
MAX_READ_BYTES = 8 * 1024 * 1024  # one response; the client asks again for the rest
# One line longer than MAX_READ_BYTES is still read whole up to this size (a mesh.created or an
# analysis.finished with per-element values of a large mesh); a longer line is skipped as bad.
MAX_LINE_BYTES = 64 * 1024 * 1024
_SCAN_BYTES = 1024 * 1024  # block size when looking for the end of a long line
# ?format=ndjson: the response headers that carry what the JSON form has beside its events
EVENTS_NEXT_OFFSET = "X-Hiraku-Next-Offset"
EVENTS_SIZE = "X-Hiraku-Size"
EVENTS_RESET = "X-Hiraku-Reset"
EVENTS_BAD_LINES = "X-Hiraku-Bad-Lines"  # only the lines skipped for their length (not JSON-checked)
KNOWLEDGE_MAX_BYTES = 2_000_000  # the body of a dropped knowledge file (its text in JSON)
REQUEST_MAX_BYTES = 1_000_000  # an A1/A2 request carries its draft back


def events_path(runs_dir: Path, run_id: str, saved_dir: Path | None = None) -> Path | None:
    """Path of a run's events.jsonl (runs/ first, then the saved runs), or None when
    the id is not a run id."""
    if run_id == SAMPLE_ID:
        return SAMPLE_EVENTS
    if not RUN_ID_RE.match(run_id):
        return None
    path = Path(runs_dir) / run_id / EVENTS_FILE
    if not path.exists() and saved_dir is not None and (Path(saved_dir) / run_id / EVENTS_FILE).exists():
        return Path(saved_dir) / run_id / EVENTS_FILE
    return path


def tool_versions(tools_dir: Path) -> dict[str, list[int]]:
    """{tool name: its version numbers, ascending} from the files tools/<name>-<n>.py of a run
    ({} when the run has no tools/)."""
    out: dict[str, list[int]] = {}
    if not tools_dir.is_dir():
        return out
    for f in tools_dir.iterdir():
        m = _TOOL_NAME_RE.match(f.name)
        if m and f.is_file():
            out.setdefault(m.group(1), []).append(int(m.group(2)))
    return {k: sorted(v) for k, v in sorted(out.items())}


def read_event_lines(path: Path, offset: int) -> dict[str, Any]:
    """The bytes of the complete lines of `path` from byte `offset` on, not parsed.

    Returns data (the lines' bytes as they are in the file), next_offset (where the
    next read starts), size, reset (true when the file got shorter than offset, so
    reading restarted at 0) and bad_lines (lines skipped for being longer than
    MAX_LINE_BYTES; their bytes are not in data).

    At most MAX_READ_BYTES are read, except when the first line is longer: then that
    one line is read to its newline (up to MAX_LINE_BYTES; a longer one is counted
    bad and skipped), so next_offset always moves past a finished line.
    """
    reset = False
    size = path.stat().st_size if path.exists() else 0
    if offset < 0 or offset > size:
        offset, reset = 0, True
    bad = 0
    next_offset = offset
    complete = b""
    if size > offset:
        with path.open("rb") as f:
            f.seek(offset)
            chunk = f.read(min(size - offset, MAX_READ_BYTES))
            end = chunk.rfind(b"\n")
            if end >= 0:
                complete = chunk[: end + 1]
                next_offset = offset + len(complete)
            elif offset + len(chunk) < size:
                # one line longer than a read: find its newline (None while it is still being written)
                nl = _newline_after(f, offset + len(chunk), size)
                if nl is not None:
                    next_offset = nl + 1
                    if next_offset - offset <= MAX_LINE_BYTES:
                        f.seek(offset)
                        complete = f.read(next_offset - offset)
                    else:
                        bad += 1
    return {"data": complete, "next_offset": next_offset, "size": size, "reset": reset, "bad_lines": bad}


def read_events(path: Path, offset: int) -> dict[str, Any]:
    """Parse the complete lines of `path` from byte `offset` on (read_event_lines).

    Returns events, next_offset, size, reset and bad_lines (lines that were not
    JSON objects, or longer than MAX_LINE_BYTES).
    """
    out = read_event_lines(path, offset)
    events: list[dict[str, Any]] = []
    bad = out["bad_lines"]
    for raw in out.pop("data").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            bad += 1
            continue
        if isinstance(obj, dict):
            events.append(obj)
        else:
            bad += 1
    return {"events": events, **out, "bad_lines": bad}


def _newline_after(f, start: int, size: int) -> int | None:
    """Byte position of the first newline at or after `start` (before `size`), read in blocks; None if none."""
    pos = start
    f.seek(pos)
    while pos < size:
        block = f.read(min(_SCAN_BYTES, size - pos))
        if not block:
            break
        i = block.find(b"\n")
        if i >= 0:
            return pos + i
        pos += len(block)
    return None


def _first_event(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as f:
            line = f.readline()
        obj = json.loads(line.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


TAIL_BYTES = 65536  # how much of the end of events.jsonl the run list reads (a big record is not read whole)


def _tail_events(path: Path) -> list[dict[str, Any] | None]:
    """The lines in the last TAIL_BYTES of the file, parsed, oldest first (a line cut by the start
    of the tail is left out; a line that does not parse, the last one still being written, is None)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read()
    except OSError:
        return []
    lines = tail.splitlines()
    if size > TAIL_BYTES and lines:
        lines = lines[1:]
    out: list[dict[str, Any] | None] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            obj = None
        out.append(obj if isinstance(obj, dict) else None)
    return out


def _last_kind(path: Path) -> str | None:
    """Kind of the last complete line (reads only the tail of the file)."""
    tail = _tail_events(path)
    return tail[-1].get("kind") if tail and tail[-1] is not None else None


def _when(created_at: str, run_id: str) -> str:
    """A short date and time for the list, "11/4 08:40": the clock written in created_at (as it is
    written, no time zone turned), else the one in the run id (YYYYMMDD-HHMMSS-...); "" if neither."""
    m = re.match(r"^\d{4}-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", created_at or "")
    if m:
        mo, d, h, mi = m.groups()
    else:
        m = re.match(r"^\d{4}(\d{2})(\d{2})-(\d{2})(\d{2})\d{2}-", run_id)
        if not m:
            return ""
        mo, d, h, mi = m.groups()
    return f"{int(mo)}/{int(d)} {h}:{mi}"


def _judged(run_dir: Path, version: int | None, tail: list[dict[str, Any] | None]) -> dict[str, int] | None:
    """How the domains of the last version were judged: {"green", "red", "pending"} counts, from
    v<NNN>/judgements.json, else from the judgement.updated events of that version in the tail
    (the last one per domain); None when neither has any."""
    if version is None:
        return None
    judgements: list[dict[str, Any]] = []
    jf = run_dir / f"v{version:03d}" / JUDGEMENTS_FILE
    if jf.is_file():
        try:
            got = json.loads(jf.read_text(encoding="utf-8"))
            judgements = [j for j in got if isinstance(j, dict)] if isinstance(got, list) else []
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            judgements = []
    if not judgements:
        last: dict[str, dict[str, Any]] = {}
        for ev in tail:
            if ev and ev.get("kind") == "judgement.updated" and isinstance(ev.get("data"), dict):
                d = ev["data"]
                if d.get("design_version") == version and d.get("domain"):
                    last[d["domain"]] = d
        judgements = list(last.values())
    if not judgements:
        return None
    out = {"green": 0, "red": 0, "pending": 0}
    for j in judgements:
        state = j.get("state")
        out[state if state in out else "pending"] += 1
    return out


def _ending(run_dir: Path, project: dict[str, Any], tail: list[dict[str, Any] | None]) -> dict[str, Any]:
    """How the run ended, from the tail of events.jsonl and project.json: the run.finished event's
    status, stop_code and stop_reason (the last line; none while the run goes on), the last design
    version (project design_versions, else the highest design_version in the tail) and how its
    domains were judged."""
    last = tail[-1] if tail else None
    fin = last.get("data") if last and last.get("kind") == "run.finished" and isinstance(last.get("data"), dict) else None
    versions = [v for v in project.get("design_versions") or [] if isinstance(v, int)]
    if not versions:
        versions = [ev["design_version"] for ev in tail if ev and isinstance(ev.get("design_version"), int)]
    version = max(versions) if versions else None
    return {
        "end_status": fin.get("status") if fin else None,
        "stop_code": fin.get("stop_code") if fin else None,
        "stop_reason": (fin.get("stop_reason") or project.get("stop_reason") or "") if fin else "",
        "stop_after": project.get("stop_after"),
        "last_version": version,
        "judged": _judged(run_dir, version, tail),
    }


def _run_summary(run_id: str, events_file: Path, project_file: Path | None) -> dict[str, Any]:
    project: dict[str, Any] = {}
    if project_file is not None and project_file.exists():
        try:
            project = json.loads(project_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            project = {}
    if not project and events_file.exists():
        first = _first_event(events_file)
        if first and first.get("kind") == "run.started" and isinstance(first.get("data"), dict):
            project = first["data"]
    stat = events_file.stat() if events_file.exists() else None
    tail = _tail_events(events_file) if stat else []
    last = tail[-1] if tail else None
    return {
        "run_id": run_id,
        "want": project.get("want", ""),
        "product": project.get("product", ""),
        "status": project.get("status", "unknown"),
        "created_at": project.get("created_at", ""),
        "when": _when(project.get("created_at", ""), run_id),
        # what drove the run (contract project.driver); None for records older than the field
        "driver": project.get("driver") if project.get("driver") in DRIVERS else None,
        "finished": bool(last and last.get("kind") == "run.finished"),
        **_ending(events_file.parent, project, tail),
        "events_bytes": stat.st_size if stat else 0,
        "mtime": stat.st_mtime if stat else 0,
        "sample": run_id == SAMPLE_ID,
        "saved": False,
        "default": False,
    }


def _run_dirs(root: Path) -> list[Path]:
    """Run folders under root (newest id first)."""
    if not root.is_dir():
        return []
    return [d for d in sorted(root.iterdir(), key=lambda p: p.name, reverse=True)
            if d.is_dir() and RUN_ID_RE.match(d.name) and (d / EVENTS_FILE).exists()]


def list_runs(runs_dir: Path, saved_dir: Path | None = None, default_run: str | None = None) -> list[dict[str, Any]]:
    """Saved runs, then runs under runs_dir (both newest id first), then the contract sample.

    A run in both places is listed once, from runs_dir (the same id is the same run).
    The default (the run the run list recommends, おすすめ) is default_run when
    it is listed, else the newest saved run, else none.
    """
    local = _run_dirs(Path(runs_dir))
    local_ids = {d.name for d in local}
    saved = [d for d in _run_dirs(Path(saved_dir)) if d.name not in local_ids] if saved_dir is not None else []
    out = []
    for d in saved:
        out.append({**_run_summary(d.name, d / EVENTS_FILE, d / PROJECT_FILE), "saved": True})
    for d in local:
        out.append(_run_summary(d.name, d / EVENTS_FILE, d / PROJECT_FILE))
    if SAMPLE_EVENTS.exists():
        out.append(_run_summary(SAMPLE_ID, SAMPLE_EVENTS, None))
    pick = next((r for r in out if r["run_id"] == default_run), None) if default_run else None
    pick = pick or next((r for r in out if r["saved"]), None)
    if pick is not None:
        pick["default"] = True
    return out


class _NullWriter:
    """Passes headers through but drops the body (for HEAD)."""

    def __init__(self, raw):
        self._raw = raw
        self._headers_done = False

    def write(self, data: bytes) -> int:
        if not self._headers_done:
            self._headers_done = True
            return self._raw.write(data)
        return len(data)

    def flush(self) -> None:
        self._raw.flush()


def modules_info() -> dict:
    """GET /api/modules: the contract's module ids, their names and the module designers' agent ids."""
    from hiraku import contract

    return {"modules": list(contract.MODULES), "names": dict(contract.MODULE_NAMES_JA), "designers": list(contract.MODULE_DESIGNERS)}


_FORMULA_TITLES: dict | None = None


def formula_titles() -> dict:
    """GET /api/formulas: each K0 formula's id and Japanese title (the solvers' registry; listed
    once, without the shelf's known-answer checks). On an error: none, with the reason."""
    global _FORMULA_TITLES
    if _FORMULA_TITLES is None:
        try:
            from hiraku import solvers

            _FORMULA_TITLES = {"formulas": [{"formula_id": f["formula_id"], "title": f["title"]} for f in solvers.formula_registry().list_formulas()]}
        except Exception as e:  # noqa: BLE001 - the screen then shows the formula as written
            _FORMULA_TITLES = {"formulas": [], "error": f"{type(e).__name__}: {e}"}
    return _FORMULA_TITLES


ASSET_DIR = "assets/polyhaven"  # web/js/look.js ASSET_DIR: Poly Haven files the user put there
ASSET_SUFFIXES = {".hdr", ".exr", ".jpg", ".jpeg", ".png"}


def list_assets(web_dir: Path) -> list[str]:
    """The asset files under web/assets/polyhaven/, as '<id>/<file>' (sorted). They are
    added by hand (web/assets/polyhaven/NOTES.md); the real look draws its own textures
    when this is empty."""
    root = Path(web_dir) / ASSET_DIR
    if not root.is_dir():
        return []
    return sorted(
        f"{p.parent.name}/{p.name}"
        for p in root.glob("*/*")
        if p.is_file() and p.suffix.lower() in ASSET_SUFFIXES
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "HirakuWebserve/0.1"
    web_dir: Path = WEB_DIR
    runs_dir: Path = Path("runs")
    saved_dir: Path | None = SAVED_DIR
    default_run: str | None = None
    launcher: Launcher | None = None
    layout_file: Path = LAYOUT_FILE
    pets_file: Path = PETS_FILE
    shelf: Shelf = Shelf()
    quiet: bool = False
    assets: bool = True  # /api/assets lists the Poly Haven files (False: none, for the fall-back)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib name)
        if not self.quiet:
            super().log_message(format, *args)

    def do_GET(self) -> None:  # noqa: N802 (stdlib name)
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        query = parse_qs(parts.query)
        if path == "/api/runs":
            self._send_json(list_runs(self.runs_dir, self.saved_dir, self.default_run))
            return
        if path == "/api/launch":
            self._send_json(self.launcher.status() if self.launcher else {"available": False})
            return
        if path == "/layout.json":
            try:
                self._send_json(json.loads(Path(self.layout_file).read_text(encoding="utf-8")))
            except FileNotFoundError:
                self._send_json({"error": f"no layout file ({Path(self.layout_file).name})"}, HTTPStatus.NOT_FOUND)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
                self._send_json({"error": f"layout file unreadable: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if path == PETS_PATH:
            self._send_pets()
            return
        if path == "/api/shelf":
            self._send_json(self.shelf.get())
            return
        if path == "/api/assets":
            self._send_json({"files": list_assets(self.web_dir) if self.assets else []})
            return
        if path == "/api/modules":
            self._send_json(modules_info())
            return
        if path == "/api/formulas":
            self._send_json(formula_titles())
            return
        m = _EVENTS_PATH_RE.match(path)
        if m:
            self._send_events(m.group(1), query)
            return
        m = _TOOL_FILE_RE.match(path)
        if m:
            self._send_tool_file(m.group(1), m.group(2))
            return
        m = _TOOL_LIST_RE.match(path)
        if m:
            self._send_tool_list(m.group(1))
            return
        if path.startswith("/api/"):
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self._send_static(path)

    def do_POST(self) -> None:  # noqa: N802 (stdlib name)
        # The body is read before any answer: an answer to an unread body can reach the
        # client as a reset connection on Windows.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        route = urlsplit(self.path).path
        # a dropped knowledge file carries its text; the other bodies are small
        # a dropped knowledge file carries its text and a request its draft; the other bodies are small
        cap = KNOWLEDGE_MAX_BYTES if route == "/api/launch/knowledge" else REQUEST_MAX_BYTES if route.startswith("/api/request") else 10000
        raw = self.rfile.read(min(max(length, 0), cap))
        left = min(max(length, 0), 64 * 1024 * 1024) - len(raw)
        while left > 0:  # the rest of a body too big to take is read and dropped
            chunk = self.rfile.read(min(left, 65536))
            if not chunk:
                break
            left -= len(chunk)
        if route not in ("/api/launch", "/api/launch/stop", "/api/launch/hearing", "/api/launch/knowledge", "/api/request", "/api/request/answer") or not self.launcher:
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        # Only this server's own page may start a run: another web page could
        # otherwise make a browser start one (and spend money). A JSON body makes a
        # browser ask first (CORS preflight), which this server never allows, and
        # the Origin, when sent, must be this server.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        # and only under a loopback name (another name pointed at this machine is refused)
        if host.rsplit(":", 1)[0] not in ("127.0.0.1", "localhost", "[::1]"):
            self._send_json({"error": "a run can only be started from this machine (127.0.0.1 or localhost)"}, HTTPStatus.FORBIDDEN)
            return
        if origin and origin not in (f"http://{host}", f"https://{host}"):
            self._send_json({"error": "a run can only be started from this server's own page"}, HTTPStatus.FORBIDDEN)
            return
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            self._send_json({"error": "a run can only be started from this server's own page"}, HTTPStatus.FORBIDDEN)
            return
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            self._send_json({"error": "send JSON"}, HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        if len(raw) < length:
            self._send_json({"error": f"the body is over {len(raw) // 1000} kB"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError) as e:
            self._send_json({"error": f"bad JSON: {e}"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            if route == "/api/launch/stop":
                self._send_json(self.launcher.stop(body.get("run_id")))
            elif route == "/api/launch/hearing":
                self._send_json(self.launcher.answer(body.get("run_id"), body.get("answer")))
            elif route in ("/api/request", "/api/request/answer"):
                self._send_json(self._request(route, body))
            elif route == "/api/launch/knowledge":
                self._send_json(self.launcher.knowledge(body.get("run_id"), body.get("name"), body.get("text")))
            else:
                # the child is started and the answer comes at once (202, run_id still None); the
                # screen follows GET /api/launch's "launch" until the child prints its folder
                info = self.launcher.begin(body.get("requirements"), body.get("mode"), body.get("request"))
                self._send_json(self.launcher.shown(info), HTTPStatus.ACCEPTED)
        except LaunchError as e:
            self._send_json({"error": str(e), **e.extra}, HTTPStatus(e.status))

    def do_HEAD(self) -> None:  # noqa: N802 (stdlib name)
        # Answer like GET without a body (used by tools that probe the server).
        self.wfile = _NullWriter(self.wfile)
        self.do_GET()

    def _send_events(self, run_id: str, query: dict[str, list[str]]) -> None:
        path = events_path(self.runs_dir, run_id, self.saved_dir)
        if path is None:
            self._send_json({"error": f"bad run id {run_id!r}"}, HTTPStatus.BAD_REQUEST)
            return
        if not path.exists():
            self._send_json({"error": f"no events for {run_id}"}, HTTPStatus.NOT_FOUND)
            return
        try:
            offset = int(query.get("offset", ["0"])[0])
        except ValueError:
            self._send_json({"error": "offset must be an integer"}, HTTPStatus.BAD_REQUEST)
            return
        if query.get("format", [""])[0] == "ndjson":
            # the lines' bytes as they are (not parsed here); where to read next in the headers
            out = read_event_lines(path, offset)
            data = out["data"]
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", CONTENT_TYPES[".jsonl"])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header(EVENTS_NEXT_OFFSET, str(out["next_offset"]))
            self.send_header(EVENTS_SIZE, str(out["size"]))
            self.send_header(EVENTS_RESET, "1" if out["reset"] else "0")
            self.send_header(EVENTS_BAD_LINES, str(out["bad_lines"]))
            self.end_headers()
            self.wfile.write(data)
            return
        body = read_events(path, offset)
        body["run_id"] = run_id
        self._send_json(body)

    def _send_pets(self) -> None:
        """The pets' script, read from pets_file as it is (only this one file)."""
        try:
            data = Path(self.pets_file).read_bytes()
        except OSError:
            self._send_json({"error": f"no pets file ({Path(self.pets_file).name})"}, HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", CONTENT_TYPES[".js"])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_static(self, path: str) -> None:
        rel = path.lstrip("/") or "index.html"
        root = self.web_dir.resolve()
        target = (root / rel).resolve()
        if target.is_dir():
            target = target / "index.html"
        if not target.is_relative_to(root) or not target.is_file():
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        ctype = CONTENT_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _request(self, route: str, body: dict[str, Any]) -> dict[str, Any]:
        """A1/A2 before a run: hiraku.intake.request over the body. Claude picks the flags only when the
        body asks (claude: true) and the key is where this server runs; the key is never read here.
        A settled request also says whether its product has a scripted plan and a kind of design."""
        from hiraku.intake.domains import IntakeError
        from hiraku.intake.request import claude_picker, handle_answer, handle_request

        try:
            if route == "/api/request":
                ask = bool(body.pop("claude", False))
                return handle_request(body, picker=claude_picker if ask and self.launcher.claude_ready() else None)
            out = handle_answer(body, self.launcher.requests_dir)
        except IntakeError as e:
            raise LaunchError(400, str(e)) from e
        rf = out.get("requirements_file") or {}
        if out.get("ready") and rf:
            from hiraku.agent.scripted import has_plan
            from hiraku.designgen import archetypes

            out["scripted_plan"] = has_plan(rf.get("product", ""))
            out["designable"] = bool(archetypes(rf.get("flags") or {}))
        return out

    def _send_tool_file(self, run_id: str, name: str) -> None:
        path = events_path(self.runs_dir, run_id, self.saved_dir)
        target = path.parent / TOOLS_DIR / name if path is not None and path != SAMPLE_EVENTS else None
        if target is None or not target.is_file():
            self._send_json({"error": f"no {name} in {run_id}"}, HTTPStatus.NOT_FOUND)
            return
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8" if name.endswith(".json") else "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_tool_list(self, run_id: str) -> None:
        path = events_path(self.runs_dir, run_id, self.saved_dir)
        # (as the tool files: the run's folder may hold tools/ before its events)
        if path is None or path == SAMPLE_EVENTS:
            self._send_json({"error": f"no run {run_id}"}, HTTPStatus.NOT_FOUND)
            return
        self._send_json({"tools": tool_versions(path.parent / TOOLS_DIR)})

    def _send_json(self, obj: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR (HTTPServer's default) lets a second socket bind a port another
    # server already listens on, so a second HIRAKU would quietly share the port with an old one.
    # There the port is bound exclusively instead: a port in use fails with OSError.
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self) -> None:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address) -> None:
        # A page that closes while its request is answered is not an error of the server.
        if isinstance(sys.exc_info()[1], (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    runs_dir: Path | str = "runs",
    web_dir: Path | str = WEB_DIR,
    quiet: bool = False,
    saved_dir: Path | str | None = SAVED_DIR,
    default_run: str | None = None,
    launcher: Launcher | bool | None = None,
    layout_file: Path | str = LAYOUT_FILE,
    shelf: Shelf | None = None,
    pets_file: Path | str = PETS_FILE,
    demo_runs_file: Path | str = DEMO_RUNS_FILE,
    assets: bool = True,
) -> ThreadingHTTPServer:
    """Build (but do not start) the server. Port 0 picks a free port.

    launcher: None starts runs into runs_dir with this process' environment
    (with the demo runs of demo_runs_file); False turns starting runs off; or a
    Launcher to use. shelf: what /api/shelf answers (by default the solvers'
    shelf, computed on the first request).
    assets: False makes /api/assets answer none (the screen then draws its own textures,
    as without the files).
    """
    if launcher is None:
        launcher = Launcher(Path(runs_dir), demo_runs_file=Path(demo_runs_file))
    handler = type(
        "BoundHandler",
        (Handler,),
        {
            "runs_dir": Path(runs_dir),
            "web_dir": Path(web_dir),
            "saved_dir": Path(saved_dir) if saved_dir is not None else None,
            "default_run": default_run,
            "launcher": launcher or None,
            "layout_file": Path(layout_file),
            "pets_file": Path(pets_file),
            "shelf": shelf or Shelf(),
            "quiet": quiet,
            "assets": assets,
        },
    )
    return Server((host, port), handler)

"""A small HTTP server for the screen: serves web/ and reads runs/<run_id>/events.jsonl.

Standard library only. Routes:

    GET /                               web/index.html
    GET /<path>                         a file under web/
    GET /api/runs                       list of runs (newest first) plus the contract sample
    GET /api/runs/<run_id>/events?offset=N
                                        complete lines of events.jsonl from byte N on,
                                        parsed; next_offset tells where to read next

The screen draws only from events, so the server never interprets them beyond
parsing JSON lines. A line still being written (no trailing newline) is left
for the next read.
"""

from __future__ import annotations

import json
import mimetypes
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from hiraku.contract import EXAMPLES_DIR
from hiraku.contract.layout import EVENTS_FILE, PROJECT_FILE, RUN_ID_RE

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
SAMPLE_ID = "sample"
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
    ".ico": "image/x-icon",
    ".txt": "text/plain; charset=utf-8",
}

_EVENTS_PATH_RE = re.compile(r"^/api/runs/([^/]+)/events$")
MAX_READ_BYTES = 8 * 1024 * 1024  # one response; the client asks again for the rest


def events_path(runs_dir: Path, run_id: str) -> Path | None:
    """Path of a run's events.jsonl, or None when the id is not a run id."""
    if run_id == SAMPLE_ID:
        return SAMPLE_EVENTS
    if not RUN_ID_RE.match(run_id):
        return None
    return Path(runs_dir) / run_id / EVENTS_FILE


def read_events(path: Path, offset: int) -> dict[str, Any]:
    """Parse the complete lines of `path` from byte `offset` on.

    Returns events, next_offset (where the next read starts), size, reset
    (true when the file got shorter than offset, so reading restarted at 0)
    and bad_lines (lines that were not JSON objects).
    """
    reset = False
    size = path.stat().st_size if path.exists() else 0
    if offset < 0 or offset > size:
        offset, reset = 0, True
    events: list[dict[str, Any]] = []
    bad = 0
    next_offset = offset
    if size > offset:
        with path.open("rb") as f:
            f.seek(offset)
            chunk = f.read(min(size - offset, MAX_READ_BYTES))
        end = chunk.rfind(b"\n")
        if end >= 0:
            complete = chunk[: end + 1]
            next_offset = offset + len(complete)
            for raw in complete.splitlines():
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
    return {"events": events, "next_offset": next_offset, "size": size, "reset": reset, "bad_lines": bad}


def _first_event(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as f:
            line = f.readline()
        obj = json.loads(line.decode("utf-8"))
        return obj if isinstance(obj, dict) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _last_kind(path: Path) -> str | None:
    """Kind of the last complete line (reads only the tail of the file)."""
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - 65536))
            tail = f.read()
    except OSError:
        return None
    for raw in reversed(tail.splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return obj.get("kind") if isinstance(obj, dict) else None
    return None


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
    return {
        "run_id": run_id,
        "want": project.get("want", ""),
        "product": project.get("product", ""),
        "status": project.get("status", "unknown"),
        "created_at": project.get("created_at", ""),
        "finished": _last_kind(events_file) == "run.finished" if stat else False,
        "events_bytes": stat.st_size if stat else 0,
        "mtime": stat.st_mtime if stat else 0,
        "sample": run_id == SAMPLE_ID,
    }


def list_runs(runs_dir: Path) -> list[dict[str, Any]]:
    """Runs under runs_dir (newest id first), then the contract sample."""
    out = []
    root = Path(runs_dir)
    if root.is_dir():
        for d in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
            if d.is_dir() and RUN_ID_RE.match(d.name) and (d / EVENTS_FILE).exists():
                out.append(_run_summary(d.name, d / EVENTS_FILE, d / PROJECT_FILE))
    if SAMPLE_EVENTS.exists():
        out.append(_run_summary(SAMPLE_ID, SAMPLE_EVENTS, None))
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


class Handler(BaseHTTPRequestHandler):
    server_version = "HirakuWebserve/0.1"
    web_dir: Path = WEB_DIR
    runs_dir: Path = Path("runs")
    quiet: bool = False

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 (stdlib name)
        if not self.quiet:
            super().log_message(format, *args)

    def do_GET(self) -> None:  # noqa: N802 (stdlib name)
        parts = urlsplit(self.path)
        path = unquote(parts.path)
        query = parse_qs(parts.query)
        if path == "/api/runs":
            self._send_json(list_runs(self.runs_dir))
            return
        m = _EVENTS_PATH_RE.match(path)
        if m:
            self._send_events(m.group(1), query)
            return
        if path.startswith("/api/"):
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self._send_static(path)

    def do_HEAD(self) -> None:  # noqa: N802 (stdlib name)
        # Answer like GET without a body (used by tools that probe the server).
        self.wfile = _NullWriter(self.wfile)
        self.do_GET()

    def _send_events(self, run_id: str, query: dict[str, list[str]]) -> None:
        path = events_path(self.runs_dir, run_id)
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
        body = read_events(path, offset)
        body["run_id"] = run_id
        self._send_json(body)

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

    def _send_json(self, obj: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    runs_dir: Path | str = "runs",
    web_dir: Path | str = WEB_DIR,
    quiet: bool = False,
) -> ThreadingHTTPServer:
    """Build (but do not start) the server. Port 0 picks a free port."""
    handler = type(
        "BoundHandler",
        (Handler,),
        {"runs_dir": Path(runs_dir), "web_dir": Path(web_dir), "quiet": quiet},
    )
    return ThreadingHTTPServer((host, port), handler)

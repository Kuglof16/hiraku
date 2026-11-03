"""A small HTTP server for the screen: serves web/ and reads runs/<run_id>/events.jsonl.

Standard library only. Routes:

    GET /                               web/index.html
    GET /<path>                         a file under web/
    GET /api/runs                       list of runs: saved runs, then runs/ (newest first),
                                        then the contract sample; one may be marked default
    GET /api/runs/<run_id>/events?offset=N
                                        complete lines of events.jsonl from byte N on,
                                        parsed; next_offset tells where to read next
    GET /api/launch, POST /api/launch   start a run from the screen (launch.py)
    POST /api/launch/stop               ask that run to stop (stop.request in its folder)
    GET /layout.json                    the video's layout rules (promo/screens/layout.json,
                                        the promo department's), read by the cut pages

Saved runs are run folders kept in the repository (saved_dir/<run_id>/, the
same layout as runs/) so a fresh checkout has something to replay. The screen
opens the default run when the URL names none: the run given as default_run,
else the newest saved run.

The screen draws only from events, so the server never interprets them beyond
parsing JSON lines. A line still being written (no trailing newline) is left
for the next read.
"""

from __future__ import annotations

import json
import mimetypes
import re
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from hiraku.contract import EXAMPLES_DIR
from hiraku.contract.layout import EVENTS_FILE, PROJECT_FILE, RUN_ID_RE, SHOWCASE_DIR
from hiraku.webserve.launch import Launcher, LaunchError

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
REPO_DIR = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_DIR / "web"
SAVED_DIR = REPO_DIR / SHOWCASE_DIR  # saved runs kept in the repository
LAYOUT_FILE = REPO_DIR / "promo" / "screens" / "layout.json"  # the video's layout rules (promo's)
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
    The default (what the screen opens when the URL names no run) is default_run when
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


class Handler(BaseHTTPRequestHandler):
    server_version = "HirakuWebserve/0.1"
    web_dir: Path = WEB_DIR
    runs_dir: Path = Path("runs")
    saved_dir: Path | None = SAVED_DIR
    default_run: str | None = None
    launcher: Launcher | None = None
    layout_file: Path = LAYOUT_FILE
    quiet: bool = False

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
        m = _EVENTS_PATH_RE.match(path)
        if m:
            self._send_events(m.group(1), query)
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
        raw = self.rfile.read(min(max(length, 0), 10000))
        route = urlsplit(self.path).path
        if route not in ("/api/launch", "/api/launch/stop", "/api/launch/hearing") or not self.launcher:
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
            else:
                self._send_json(self.launcher.start(body.get("requirements"), body.get("mode")))
        except LaunchError as e:
            self._send_json({"error": str(e)}, HTTPStatus(e.status))

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


class Server(ThreadingHTTPServer):
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
) -> ThreadingHTTPServer:
    """Build (but do not start) the server. Port 0 picks a free port.

    launcher: None starts runs into runs_dir with this process' environment;
    False turns starting runs off; or a Launcher to use.
    """
    if launcher is None:
        launcher = Launcher(Path(runs_dir))
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
            "quiet": quiet,
        },
    )
    return Server((host, port), handler)

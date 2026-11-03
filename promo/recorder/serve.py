"""Serve the narration recorder on 127.0.0.1 with the work folder behind it.

    py promo/recorder/serve.py --work WORK_DIR [--port 8790] [--no-browser]
                               [--script FILE] [--synth DIR] [--out DIR]

The page (promo/recorder/) then reads the script and the synthesized voices
by itself and saves each chosen take into the output folder, where
``voice.json`` is rewritten after every save (the same as
``voice.py adopt``). Defaults, relative to WORK_DIR:

    script   script/narration.tsv
    synth    voice/aida_calm/        (read only)
    out      voice/recorded/

Only 127.0.0.1 is listened on. Stop with Ctrl+C.

API (all JSON unless noted):
    GET  /api/status              script name and size, synth files, saved files
    GET  /api/script              the script text (text/plain)
    GET  /api/synth/<name>        voice.json or a <cut>_<k>.wav of the synth folder
    PUT  /api/take/<cut>_<k>.wav  a 16-bit WAV for a sentence of the script;
                                  writes it to the output folder, then voice.json
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import threading
import urllib.request
import wave
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from promo import voice  # noqa: E402  (repository root added above)

APP = "hiraku-recorder"
DEFAULT_PORT = 8790
PORT_TRIES = 20
MAX_TAKE_BYTES = 64 * 1024 * 1024  # about 12 minutes of 44.1 kHz 16-bit mono
WAV_NAME = re.compile(r"^[A-Za-z0-9]+_[0-9]+\.wav$")

# Explicit types: Windows can map .js to text/plain through the registry.
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class Work:
    """The folders the server reads from and writes to."""

    def __init__(self, work: Path, script: Path | None = None, synth: Path | None = None,
                 out: Path | None = None):
        self.work = work.resolve()
        self.script = (script or self.work / "script" / "narration.tsv").resolve()
        self.synth = (synth or self.work / "voice" / "aida_calm").resolve()
        self.out = (out or self.work / "voice" / "recorded").resolve()
        self.lock = threading.Lock()

    def label(self, p: Path) -> str:
        """A path as shown on the page: relative to the work folder when inside it."""
        try:
            return p.relative_to(self.work).as_posix() or "."
        except ValueError:
            return str(p)

    def sentence_names(self) -> list[str]:
        return voice.sentence_files(voice.parse_script(self.script.read_text(encoding="utf-8")))

    def status(self) -> dict:
        st: dict = {"app": APP, "work": str(self.work),
                    "script": {"name": self.script.name, "label": self.label(self.script)},
                    "synth": {"label": self.label(self.synth), "files": []},
                    "out": {"label": self.label(self.out), "have": []}}
        try:
            names = self.sentence_names()
            st["script"]["lines"] = len(names)
        except FileNotFoundError:
            st["script"]["error"] = "not found"
            names = []
        except ValueError as e:
            st["script"]["error"] = str(e)
            names = []
        if self.synth.is_dir():
            st["synth"]["files"] = sorted(p.name for p in self.synth.glob("*.wav") if WAV_NAME.match(p.name))
            st["synth"]["voice_json"] = (self.synth / "voice.json").is_file()
        else:
            st["synth"]["error"] = "not found"
        if self.out.is_dir():
            on_disk = {p.name for p in self.out.glob("*.wav")}
            st["out"]["have"] = [n for n in names if n in on_disk]
        st["out"]["missing"] = len(names) - len(st["out"]["have"])
        return st

    def save_take(self, name: str, data: bytes) -> dict:
        """Write one take and rewrite voice.json. Raises ValueError for a bad request."""
        if not WAV_NAME.match(name):
            raise ValueError(f"bad file name: {name}")
        names = self.sentence_names()
        if name not in names:
            raise ValueError(f"not a sentence of the script: {name}")
        try:
            with wave.open(io.BytesIO(data), "rb") as w:
                fmt = (w.getframerate(), w.getnchannels(), w.getsampwidth())
                seconds = w.getnframes() / w.getframerate()
        except (wave.Error, EOFError) as e:
            raise ValueError(f"not a WAV file: {e}") from None
        if fmt[2] != 2:
            raise ValueError(f"expected 16-bit samples, got {8 * fmt[2]} bit")
        target = (self.out / name).resolve()
        if target.parent != self.out:  # the name pattern already forbids this
            raise ValueError("outside the output folder")
        with self.lock:
            self.out.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".wav.part")
            tmp.write_bytes(data)
            tmp.replace(target)
            res = voice.adopt(self.script, self.out)
        have = [r["file"] for r in res["meta"]["lines"]]
        return {"saved": name, "seconds": round(seconds, 3), "format": list(fmt),
                "folder": self.label(self.out), "have": have, "total": len(names),
                "missing": len(res["missing"]), "fit": res["meta"]["fit"]}


class Handler(BaseHTTPRequestHandler):
    server_version = "HirakuRecorder/1"
    work: Work  # set on the subclass made by make_server
    quiet = False

    # --- helpers -------------------------------------------------------------

    def log_message(self, fmt, *args):
        if not self.quiet:
            sys.stderr.write("  " + (fmt % args) + "\n")

    def send_body(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, code: int, obj) -> None:
        self.send_body(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def fail(self, code: int, msg: str) -> None:
        self.send_json(code, {"error": msg})

    def host_ok(self) -> bool:
        """Refuse requests whose Host is not this machine (DNS rebinding)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost")

    def origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        o = urlsplit(origin)
        return o.hostname in ("127.0.0.1", "localhost") and o.port == self.server.server_address[1]

    def path_only(self) -> str:
        return unquote(urlsplit(self.path).path)

    # --- methods -------------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.host_ok():
            return self.fail(HTTPStatus.FORBIDDEN, "host")
        p = self.path_only()
        if p == "/api/status":
            return self.send_json(HTTPStatus.OK, self.work.status())
        if p == "/api/script":
            try:
                text = self.work.script.read_bytes()
            except FileNotFoundError:
                return self.fail(HTTPStatus.NOT_FOUND, f"script not found: {self.work.label(self.work.script)}")
            return self.send_body(HTTPStatus.OK, text, "text/plain; charset=utf-8")
        if p.startswith("/api/synth/"):
            name = p[len("/api/synth/"):]
            if name != "voice.json" and not WAV_NAME.match(name):
                return self.fail(HTTPStatus.NOT_FOUND, "no such file")
            f = self.work.synth / name
            if not f.is_file():
                return self.fail(HTTPStatus.NOT_FOUND, "no such file")
            ctype = "application/json; charset=utf-8" if name.endswith(".json") else "audio/wav"
            return self.send_body(HTTPStatus.OK, f.read_bytes(), ctype)
        if p.startswith("/api/"):
            return self.fail(HTTPStatus.NOT_FOUND, "no such endpoint")
        return self.static(p)

    def static(self, p: str):
        name = "index.html" if p in ("", "/") else p.lstrip("/")
        # files directly in promo/recorder/ only
        if "/" in name or "\\" in name or name.startswith(".") or Path(name).suffix not in STATIC_TYPES:
            return self.fail(HTTPStatus.NOT_FOUND, "no such file")
        f = HERE / name
        if not f.is_file():
            return self.fail(HTTPStatus.NOT_FOUND, "no such file")
        return self.send_body(HTTPStatus.OK, f.read_bytes(), STATIC_TYPES[f.suffix])

    def do_PUT(self):
        if not self.host_ok() or not self.origin_ok():
            return self.fail(HTTPStatus.FORBIDDEN, "host or origin")
        p = self.path_only()
        if not p.startswith("/api/take/"):
            return self.fail(HTTPStatus.NOT_FOUND, "no such endpoint")
        name = p[len("/api/take/"):]
        try:
            length = int(self.headers.get("Content-Length") or "-1")
        except ValueError:
            length = -1
        if length < 0:
            return self.fail(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
        if length > MAX_TAKE_BYTES:
            return self.fail(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "take too large")
        data = self.rfile.read(length)
        try:
            res = self.work.save_take(name, data)
        except ValueError as e:
            return self.fail(HTTPStatus.BAD_REQUEST, str(e))
        except FileNotFoundError as e:
            return self.fail(HTTPStatus.CONFLICT, f"script not found: {e}")
        return self.send_json(HTTPStatus.OK, res)

    def do_POST(self):
        self.fail(HTTPStatus.METHOD_NOT_ALLOWED, "use PUT /api/take/<name>.wav")

    do_DELETE = do_POST


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second server bind a port in use; keep it off
    allow_reuse_address = False
    daemon_threads = True


def make_server(work: Work, port: int = DEFAULT_PORT, tries: int = PORT_TRIES, quiet: bool = False) -> Server:
    """Bind 127.0.0.1 on the first free port from ``port`` (port 0: any free port)."""
    handler = type("BoundHandler", (Handler,), {"work": work, "quiet": quiet})
    last: OSError | None = None
    for p in ([0] if port == 0 else range(port, port + tries)):
        try:
            return Server(("127.0.0.1", p), handler)
        except OSError as e:
            last = e
    raise OSError(f"no free port in {port}-{port + tries - 1}: {last}")


def running_here(port: int, work: Work) -> bool:
    """True when a recorder for the same work folder already answers on ``port``."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=0.5) as r:
            st = json.loads(r.read())
        return st.get("app") == APP and Path(st.get("work", "")).resolve() == work.work
    except (OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)  # the address shows at once, even piped
    ap =argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", type=Path, required=True, help="work folder")
    ap.add_argument("--script", type=Path, help="script TSV (default WORK/script/narration.tsv)")
    ap.add_argument("--synth", type=Path, help="synthesized voices (default WORK/voice/aida_calm)")
    ap.add_argument("--out", type=Path, help="where takes are saved (default WORK/voice/recorded)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--no-browser", action="store_true", help="do not open the page")
    a = ap.parse_args(argv)

    if not a.work.is_dir():
        print(f"作業フォルダがありません: {a.work}")
        return 2
    work = Work(a.work, a.script, a.synth, a.out)

    if a.port and running_here(a.port, work):
        url = f"http://127.0.0.1:{a.port}/"
        print(f"もう動いています。ページを開きます: {url}")
        if not a.no_browser:
            webbrowser.open(url)
        return 0

    st = work.status()
    print(f"作業フォルダ  {work.work}")
    print(f"原稿          {st['script']['label']}  " +
          (f"({st['script']['lines']} 文)" if "lines" in st["script"] else f"!! {st['script']['error']}"))
    print(f"合成の声      {st['synth']['label']}  ({len(st['synth']['files'])} 本)")
    print(f"保存先        {st['out']['label']}  ({len(st['out']['have'])} 文が保存済み)")

    srv = make_server(work, a.port)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"\n{url} で開いています。止めるときはこの窓で Ctrl+C (または窓を閉じる)。\n")
    if not a.no_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("止めました。")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

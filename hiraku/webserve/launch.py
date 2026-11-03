"""Starting a run from the screen: the second way in, next to replaying a saved run.

    GET  /api/launch   what can be started: the requirement files, whether a Claude run
                       can start (ANTHROPIC_API_KEY is set where this server runs), and
                       the run started here, if one is still going, and the demo runs
                       (data/catalog/demo_runs.yaml, see Launcher.demo_runs)
    POST /api/launch   {"requirements": "<file name>", "mode": "scripted" | "claude"}
                       -> {"run_id": ...} once the run folder exists; the name is a file
                       directly in the requirements folder, or a path under it listed as
                       `file` in the demo runs. A file in the demo runs only runs in the
                       modes listed for it there.
    POST /api/launch/stop  {"run_id": ...}: asks the run started here to stop, by
                       putting layout.STOP_REQUEST_FILE in its folder (the run then
                       ends as stopped, stop_code user_stop, within seconds)

A run is `python -m hiraku run data/requirements/<file> [--scripted] --runs <runs>`
in a child process with this server's environment. The key is never put into a
response or written anywhere, and the screen never asks for it: the child reads
it from the environment like any run. Only files in the requirements folder (and
the demo runs' listed files under it) can be named, one run at a time. The server
refuses a request from another web page (a run costs money); see Handler.do_POST.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

from hiraku.contract.layout import EVENTS_FILE, INBOX_DIR, KNOWLEDGE_DIR, RUN_ID_RE, STOP_REQUEST_FILE

REPO_DIR = Path(__file__).resolve().parents[2]
REQUIREMENTS_DIR = REPO_DIR / "data" / "requirements"
KEY_NAME = "ANTHROPIC_API_KEY"
MODES = ("scripted", "claude")
_FILE_RE = re.compile(r"^[A-Za-z0-9_.-]+\.ya?ml$")
REQUESTS_DIR = "_requests"  # under the runs folder: requests settled in A1/A2 (hiraku.intake.request)
_REQUEST_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_RUN_FOLDER = "run folder:"
# the design department's list of demo runs: kept out of data/requirements/ so that nothing
# running every requirements file there takes it for one
DEMO_RUNS_FILE = REPO_DIR / "data" / "catalog" / "demo_runs.yaml"
PRODUCTS_DIR = "products"
_PART_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_PRODUCT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class LaunchError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def knowledge_name(name: Any) -> str:
    """A dropped file's name as kept in knowledge/: its last part only (no folders, no
    drive or stream colon, not hidden, not . or ..)."""
    if not isinstance(name, str):
        raise LaunchError(400, "name must be the file's name")
    base = name.replace("\\", "/").split("/")[-1].strip()
    if not base or base in (".", "..") or base.startswith(".") or ":" in base or len(base) > 200:
        raise LaunchError(400, f"cannot keep a file named {name!r}")
    return base


def put_inbox(run_dir: Path, kind: str, fields: dict[str, Any]) -> str:
    """Put a request for a running run as inbox/<n>-<kind>.json (n: one more than the largest
    there; written to .tmp first so the run never reads half a file). Returns its path in the run."""
    inbox = run_dir / INBOX_DIR
    inbox.mkdir(exist_ok=True)
    taken = [int(m.group(1)) for p in inbox.iterdir() if (m := re.match(r"(\d+)-", p.name))]
    name = f"{max(taken, default=0) + 1}-{kind}.json"
    tmp = inbox / (name + ".tmp")
    tmp.write_text(json.dumps({"kind": kind, **fields}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(inbox / name)
    return f"{INBOX_DIR}/{name}"


def check_designable(requirements: Path) -> None:
    """Refuse a request whose product no kind of design suits (the run could not make version 1)."""
    import yaml

    from hiraku.designgen import archetypes

    flags = (yaml.safe_load(requirements.read_text(encoding="utf-8")) or {}).get("flags") or {}
    if not archetypes(flags):
        raise LaunchError(422, "この製品の設計の作り方がまだ無いので、走らせられません (製品の性質に合う設計の型が無い)")


def read_requirements(path: Path) -> dict[str, Any]:
    """What the screen shows about a requirements file (its words and limits)."""
    from hiraku.catalog import load_requirements

    r = load_requirements(path)
    return {"product": r.get("product", ""), "want": r.get("want", ""),
            "cost_limit_usd": r.get("cost_limit_usd"), "round_limit": r.get("round_limit")}


def relative_file(name: Any) -> str:
    """A demo run's `file`: a relative path of plain parts (no drive, no leading /, no . or ..),
    ending in a .yaml/.yml file name. Returns it with / between the parts."""
    if not isinstance(name, str) or not name or "\\" in name or name.startswith("/"):
        raise ValueError("file must be a path relative to data/requirements/")
    parts = name.split("/")
    if not all(_PART_RE.match(p) and p not in (".", "..") for p in parts) or not _FILE_RE.match(parts[-1]):
        raise ValueError(f"file must be a path relative to data/requirements/, not {name!r}")
    return name


def demo_run_modes(modes: Any) -> list[str]:
    """A demo run's `modes`: a non-empty list of scripted / claude (the buttons its row shows)."""
    if not isinstance(modes, list) or not modes or not all(m in MODES for m in modes):
        raise ValueError(f"modes must be a list of {' / '.join(MODES)}, not {modes!r}")
    return [m for m in MODES if m in modes]


def demo_run_estimate(value: Any) -> float | None:
    """A demo run's `estimate_usd` as written (null: no estimate)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"estimate_usd must be a number of USD or null, not {value!r}")
    return value


def run_command(requirements: Path, mode: str, runs_dir: Path, intake_events: Path | None = None) -> list[str]:
    cmd = [sys.executable, "-u", "-m", "hiraku", "run", str(requirements), "--runs", str(runs_dir)]
    if intake_events is not None:  # a request settled in A1/A2: the run emits these intake events as they are
        cmd += ["--intake-events", str(intake_events)]
    return cmd + (["--scripted"] if mode == "scripted" else [])


class Launcher:
    """One child run at a time; remembers the runs started from this server."""

    def __init__(self, runs_dir: Path, requirements_dir: Path = REQUIREMENTS_DIR, env: dict[str, str] | None = None,
                 command: Callable[[Path, str, Path], list[str]] = run_command,
                 reader: Callable[[Path], dict[str, Any]] = read_requirements, wait_s: float = 60.0,
                 demo_runs_file: Path = DEMO_RUNS_FILE):
        self.runs_dir = Path(runs_dir)
        self.requirements_dir = Path(requirements_dir)
        self.demo_runs_file = Path(demo_runs_file)
        self.env = dict(os.environ if env is None else env)
        self.command = command
        self.reader = reader
        self.wait_s = wait_s
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.current: dict[str, Any] | None = None
        self.started: list[dict[str, Any]] = []

    def claude_ready(self) -> bool:
        return bool(self.env.get(KEY_NAME, "").strip())

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> dict[str, Any]:
        demo, demo_error = self.demo_runs()
        modes = self.demo_modes()
        files = []
        if self.requirements_dir.is_dir():
            for p in sorted(self.requirements_dir.iterdir()):
                if not (p.is_file() and _FILE_RE.match(p.name)):
                    continue
                row = self._read_file(p.name, p)
                if p.name in modes:  # a file in the demo runs runs only in the modes listed there
                    row["modes"] = modes[p.name]
                files.append(row)
        return {
            "requirements": files,
            "demo_runs": demo,
            "demo_runs_error": demo_error,
            "claude_ready": self.claude_ready(),
            "key_name": KEY_NAME,
            "running": self.current if self.running() else None,
            "started": self.started[-10:],
        }

    def _read_file(self, name: str, path: Path) -> dict[str, Any]:
        try:
            return {"file": name, **self.reader(path)}
        except Exception as e:  # a file that does not load is shown, not hidden
            return {"file": name, "error": f"{type(e).__name__}: {e}"}

    def _demo_list(self) -> list[Any] | None:
        """The demo runs file's `runs` as written; None when there is no such file. Raises when it does not load."""
        import yaml

        if not self.demo_runs_file.is_file():
            return None
        doc = yaml.safe_load(self.demo_runs_file.read_text(encoding="utf-8"))
        runs = doc.get("runs") if isinstance(doc, dict) else None
        if not isinstance(runs, list):
            raise ValueError(f"{self.demo_runs_file.name} needs a list under runs:")
        return runs

    def demo_modes(self) -> dict[str, list[str]]:
        """The relative paths listed as `file` in the demo runs, with the modes listed for them
        (in MODES order; a file listed twice runs in the modes of both). Well-formed rows only."""
        try:
            runs = self._demo_list() or []
        except Exception:
            return {}
        out: dict[str, set[str]] = {}
        for r in runs:
            if not isinstance(r, dict):
                continue
            try:
                name, modes = relative_file(r.get("file")), demo_run_modes(r.get("modes"))
            except ValueError:
                continue
            out.setdefault(name, set()).update(modes)
        return {name: [m for m in MODES if m in modes] for name, modes in out.items()}

    def demo_runs(self) -> tuple[list[dict[str, Any]], str | None]:
        """The demo runs in the file's order, and why the file did not load (None if it did or is
        not there). A row that cannot be used keeps its id, title and shows with an error."""
        import yaml

        try:
            runs = self._demo_list()
        except Exception as e:
            return [], f"{self.demo_runs_file.name}: {type(e).__name__}: {e}"
        rows = []
        for r in runs or []:
            if not isinstance(r, dict):
                rows.append({"id": "", "title": "", "shows": "", "cuts": [], "error": "each run must be a mapping"})
                continue
            cuts = r.get("cuts") or []
            row: dict[str, Any] = {"id": str(r.get("id") or ""), "title": str(r.get("title") or ""),
                                   "shows": str(r.get("shows") or ""),
                                   "cuts": [str(c) for c in cuts] if isinstance(cuts, list) else [str(cuts)]}
            try:
                row["modes"] = demo_run_modes(r.get("modes"))
                row["estimate_usd"] = demo_run_estimate(r.get("estimate_usd"))
            except ValueError as e:
                rows.append({**row, "error": str(e)})
                continue
            file, request = r.get("file"), r.get("request")
            if (file is None) == (request is None):
                rows.append({**row, "error": "a run needs either file or request"})
            elif file is not None:
                try:
                    name = relative_file(file)
                except ValueError as e:
                    rows.append({**row, "file": str(file), "error": str(e)})
                    continue
                path = self.requirements_dir / name
                rows.append({**row, **(self._read_file(name, path) if path.is_file()
                                       else {"file": name, "error": f"no requirements file {name}"})})
            else:
                product = request.get("product") if isinstance(request, dict) else None
                if not isinstance(product, str) or not _PRODUCT_RE.match(product):
                    rows.append({**row, "error": "request must be {product: <name in products/>}"})
                    continue
                path = self.requirements_dir / PRODUCTS_DIR / f"{product}.yaml"
                try:
                    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                    want = doc.get("want") if isinstance(doc, dict) else None
                    if not isinstance(want, str) or not want.strip():
                        raise ValueError(f"no want in {PRODUCTS_DIR}/{product}.yaml")
                    rows.append({**row, "product": product, "want": want.strip()})
                except Exception as e:
                    rows.append({**row, "product": product, "error": f"{type(e).__name__}: {e}"})
        return rows, None

    def _path_of(self, name: Any) -> Path:
        if isinstance(name, str) and not _FILE_RE.match(name) and name in self.demo_modes():
            path = (self.requirements_dir / name).resolve()
            if not path.is_relative_to(self.requirements_dir.resolve()):  # (relative_file already keeps it inside)
                raise LaunchError(400, "requirements must be a file under data/requirements/")
            if not path.is_file():
                raise LaunchError(404, f"no requirements file {name}")
            return path
        if not isinstance(name, str) or not _FILE_RE.match(name):
            raise LaunchError(400, "requirements must be the name of a file in data/requirements/ or a file listed in the demo runs")
        path = self.requirements_dir / name
        if not path.is_file():
            raise LaunchError(404, f"no requirements file {name}")
        return path

    @property
    def requests_dir(self) -> Path:
        """Where A1/A2 requests are saved (hiraku.intake.request.handle_answer): <id>/requirements.yaml, intake.json."""
        return self.runs_dir / REQUESTS_DIR

    def _request_of(self, request_id: Any) -> tuple[Path, Path]:
        if not isinstance(request_id, str) or not _REQUEST_RE.match(request_id):
            raise LaunchError(400, "request must be the id of a request saved from the screen")
        folder = self.requests_dir / request_id
        req, events = folder / "requirements.yaml", folder / "intake.json"
        if not (req.is_file() and events.is_file()):
            raise LaunchError(404, f"no saved request {request_id}")
        return req, events

    def start(self, name: Any, mode: Any, request: Any = None) -> dict[str, Any]:
        if mode not in MODES:
            raise LaunchError(400, f"mode must be one of {', '.join(MODES)}")
        events = None
        if request is not None:
            path, events = self._request_of(request)
            check_designable(path)
        else:
            path = self._path_of(name)
            listed = self.demo_modes().get(name)
            if listed is not None and mode not in listed:
                raise LaunchError(400, f"{name} runs only as {' / '.join(listed)} in the demo runs")
        if mode == "claude" and not self.claude_ready():
            raise LaunchError(403, f"{KEY_NAME} is not set where this server runs; start the server in a window that has it")
        with self.lock:
            if self.running():
                raise LaunchError(409, f"a run is still going: {self.current.get('run_id')}")
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            before = {p.name for p in self.runs_dir.iterdir()}
            env = {**self.env, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
            cmd = self.command(path, mode, self.runs_dir, events) if events is not None else self.command(path, mode, self.runs_dir)
            proc = subprocess.Popen(cmd, cwd=str(REPO_DIR), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    text=True, encoding="utf-8", errors="replace")
            info: dict[str, Any] = {"run_id": None, "mode": mode, "requirements": name if request is None else f"{REQUESTS_DIR}/{request}",
                                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            tail: deque[str] = deque(maxlen=20)
            threading.Thread(target=self._read, args=(proc, info, tail), daemon=True).start()
            self.proc, self.current = proc, info
        # The run folder appears when the run starts; its id is also printed (run folder: ...).
        deadline = time.monotonic() + self.wait_s
        while time.monotonic() < deadline:
            if info["run_id"] is None:
                new = [p for p in self.runs_dir.iterdir()
                       if p.name not in before and RUN_ID_RE.match(p.name) and (p / EVENTS_FILE).exists()]
                if new:
                    info["run_id"] = sorted(p.name for p in new)[-1]
            if info["run_id"]:
                self.started.append(info)
                return info
            if proc.poll() is not None:
                raise LaunchError(500, "the run ended before it started: " + " / ".join(tail))
            time.sleep(0.1)
        raise LaunchError(504, "the run folder did not appear in time")

    def stop(self, run_id: Any) -> dict[str, Any]:
        """Ask the run started here to stop (only that one, and only while it runs)."""
        if not self.running() or not self.current or self.current.get("run_id") != run_id:
            raise LaunchError(409, "no run started from this screen is going under that id")
        (self.runs_dir / run_id / STOP_REQUEST_FILE).write_bytes(b"")
        self.current["stop_requested"] = True
        return {"run_id": run_id, "stop_requested": True}

    def answer(self, run_id: Any, body: Any) -> dict[str, Any]:
        """The hearing's answer (A2) for the run started here, while it runs: checked and
        written as hearing.answer.json in its folder by hiraku.intake.hearing.save_answer."""
        if not self.running() or not self.current or self.current.get("run_id") != run_id:
            raise LaunchError(409, "no run started from this screen is going under that id")
        if not isinstance(body, dict):
            raise LaunchError(400, "answer must be an object (answers, note, useDefaults)")
        from hiraku.intake.hearing import save_answer

        try:
            save_answer(self.runs_dir / run_id, body)
        except ValueError as e:  # IntakeError: an answer the hearing does not take
            raise LaunchError(400, str(e)) from e
        return {"run_id": run_id, "answered": True}

    def knowledge(self, run_id: Any, name: Any, text: Any) -> dict[str, Any]:
        """A knowledge file dropped on A5, for the run started here while it runs: kept as
        knowledge/<name> in its folder, and asked for through inbox/<n>-knowledge.json (the run
        reads it, imports the file and emits the events itself)."""
        if not self.running() or not self.current or self.current.get("run_id") != run_id:
            raise LaunchError(409, "no run started from this screen is going under that id")
        if not isinstance(text, str):
            raise LaunchError(400, "text must be the file's text")
        safe = knowledge_name(name)
        run_dir = self.runs_dir / run_id
        folder = run_dir / KNOWLEDGE_DIR
        folder.mkdir(exist_ok=True)
        (folder / safe).write_bytes(text.encode("utf-8"))  # as given (no newline translation)
        request = put_inbox(run_dir, "knowledge", {"file": safe})
        return {"run_id": run_id, "file": safe, "request": request}

    @staticmethod
    def _read(proc: subprocess.Popen, info: dict[str, Any], tail: deque[str]) -> None:
        for line in proc.stdout:
            line = line.rstrip()
            tail.append(line)
            if line.startswith(_RUN_FOLDER) and info["run_id"] is None:
                name = Path(line[len(_RUN_FOLDER):].strip()).name
                if RUN_ID_RE.match(name):
                    info["run_id"] = name
        proc.stdout.close()
        proc.wait()
        info["exit_code"] = proc.returncode

"""Starting a run from the screen: the second way in, next to replaying a saved run.

    GET  /api/launch   what can be started: the requirement files, whether a Claude run
                       can start (ANTHROPIC_API_KEY is set where this server runs), and
                       the run started here, if one is still going
    POST /api/launch   {"requirements": "<file name>", "mode": "scripted" | "claude"}
                       -> {"run_id": ...} once the run folder exists
    POST /api/launch/stop  {"run_id": ...}: asks the run started here to stop, by
                       putting layout.STOP_REQUEST_FILE in its folder (the run then
                       ends as stopped, stop_code user_stop, within seconds)

A run is `python -m hiraku run data/requirements/<file> [--scripted] --runs <runs>`
in a child process with this server's environment. The key is never put into a
response or written anywhere, and the screen never asks for it: the child reads
it from the environment like any run. Only files in the requirements folder can
be named, one run at a time. The server refuses a request from another web page
(a run costs money); see Handler.do_POST.
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
_RUN_FOLDER = "run folder:"


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


def read_requirements(path: Path) -> dict[str, Any]:
    """What the screen shows about a requirements file (its words and limits)."""
    from hiraku.catalog import load_requirements

    r = load_requirements(path)
    return {"product": r.get("product", ""), "want": r.get("want", ""),
            "cost_limit_usd": r.get("cost_limit_usd"), "round_limit": r.get("round_limit")}


def run_command(requirements: Path, mode: str, runs_dir: Path) -> list[str]:
    cmd = [sys.executable, "-u", "-m", "hiraku", "run", str(requirements), "--runs", str(runs_dir)]
    return cmd + (["--scripted"] if mode == "scripted" else [])


class Launcher:
    """One child run at a time; remembers the runs started from this server."""

    def __init__(self, runs_dir: Path, requirements_dir: Path = REQUIREMENTS_DIR, env: dict[str, str] | None = None,
                 command: Callable[[Path, str, Path], list[str]] = run_command,
                 reader: Callable[[Path], dict[str, Any]] = read_requirements, wait_s: float = 60.0):
        self.runs_dir = Path(runs_dir)
        self.requirements_dir = Path(requirements_dir)
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
        files = []
        if self.requirements_dir.is_dir():
            for p in sorted(self.requirements_dir.iterdir()):
                if not (p.is_file() and _FILE_RE.match(p.name)):
                    continue
                try:
                    files.append({"file": p.name, **self.reader(p)})
                except Exception as e:  # a file that does not load is shown, not hidden
                    files.append({"file": p.name, "error": f"{type(e).__name__}: {e}"})
        return {
            "requirements": files,
            "claude_ready": self.claude_ready(),
            "key_name": KEY_NAME,
            "running": self.current if self.running() else None,
            "started": self.started[-10:],
        }

    def _path_of(self, name: Any) -> Path:
        if not isinstance(name, str) or not _FILE_RE.match(name):
            raise LaunchError(400, "requirements must be the name of a file in data/requirements/")
        path = self.requirements_dir / name
        if not path.is_file():
            raise LaunchError(404, f"no requirements file {name}")
        return path

    def start(self, name: Any, mode: Any) -> dict[str, Any]:
        if mode not in MODES:
            raise LaunchError(400, f"mode must be one of {', '.join(MODES)}")
        path = self._path_of(name)
        if mode == "claude" and not self.claude_ready():
            raise LaunchError(403, f"{KEY_NAME} is not set where this server runs; start the server in a window that has it")
        with self.lock:
            if self.running():
                raise LaunchError(409, f"a run is still going: {self.current.get('run_id')}")
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            before = {p.name for p in self.runs_dir.iterdir()}
            env = {**self.env, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
            proc = subprocess.Popen(self.command(path, mode, self.runs_dir), cwd=str(REPO_DIR), env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                    text=True, encoding="utf-8", errors="replace")
            info: dict[str, Any] = {"run_id": None, "mode": mode, "requirements": name,
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

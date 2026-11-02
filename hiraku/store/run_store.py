"""Read and write one run folder: runs/<run_id>/.

Every write is validated against the contract first, so a bad record never
reaches the disk or events.jsonl. Every record written to a file is also the
`data` of one event (the same object).

Updating a record (a cause becomes resolved, a comparison gets its cause_id,
a judgement tile changes) rewrites its file and emits the same event kind
again with the full new record. Readers keep the latest record per id, so the
file always equals the data of the latest event that carries its id.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from hiraku import contract
from hiraku.contract import layout

Record = dict[str, Any]

# Event kind emitted when a record of this kind is written.
RECORD_EVENT = {
    "prediction": "prediction.recorded",
    "analysis": "analysis.finished",
    "comparison": "comparison.recorded",
    "cause": "cause.recorded",
    "verdict": "verdict.recorded",
}


class StoreError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


class RunStore:
    """One run folder. Create with RunStore.create(), reopen with RunStore.open()."""

    def __init__(self, path: Path, project: Record, *, wall: Callable[[], datetime] = _now,
                 mono: Callable[[], float] = time.monotonic) -> None:
        self.path = Path(path)
        self.run_id = project["run_id"]
        self._project = project
        self._wall = wall
        self._mono = mono
        self._lock = threading.RLock()
        self._seq = 0
        self._t0_mono = mono()
        self._t_offset = 0.0  # t of the last event when reopened
        self._counters = {kind: 0 for kind in layout.ID_PREFIX}

    # ------------------------------------------------------------------ create / open

    @classmethod
    def create(cls, project_fields: Record, *, root: Path = layout.RUNS_DIR, slug: str | None = None,
               wall: Callable[[], datetime] = _now, mono: Callable[[], float] = time.monotonic) -> "RunStore":
        """Make runs/<run_id>/, write project.json and emit run.started.

        project_fields: every project field except run_id, created_at, status and cost_usd.
        """
        now = wall()
        run_id = layout.new_run_id(now, slug or project_fields["product"])
        path = layout.run_dir(run_id, Path(root))
        if path.exists():
            raise StoreError(f"run folder already exists: {path}")
        project = {
            "run_id": run_id,
            "created_at": _iso(now),
            **project_fields,
            "status": "running",
            "cost_usd": 0.0,
            "design_versions": list(project_fields.get("design_versions", [])),
        }
        contract.validate("project", project)
        path.mkdir(parents=True)
        store = cls(path, project, wall=wall, mono=mono)
        _write_json(path / layout.PROJECT_FILE, project)
        store.emit("run.started", agent="system", data=project)
        return store

    @classmethod
    def open(cls, path: Path) -> "RunStore":
        """Reopen an existing run folder (for reading, or to append more events)."""
        path = Path(path)
        project = _read_json(path / layout.PROJECT_FILE)
        store = cls(path, project)
        events = store.events()
        if events:
            store._seq = events[-1]["seq"] + 1
            store._t_offset = events[-1]["t"]
        for kind in layout.ID_PREFIX:
            ids = [r[f"{kind}_id"] for r in store.records(kind)]
            store._counters[kind] = max((int(i.rsplit("-", 1)[1]) for i in ids), default=0)
        return store

    # ------------------------------------------------------------------ events

    @property
    def events_path(self) -> Path:
        return self.path / layout.EVENTS_FILE

    def _t(self) -> float:
        return round(self._t_offset + (self._mono() - self._t0_mono), 3)

    def emit(self, kind: str, *, agent: str | None = None, domain: str | None = None,
             design_version: int | None = None, text: str | None = None, data: Record | None = None) -> Record:
        """Append one event to events.jsonl (validated) and return it."""
        with self._lock:
            if kind == "run.started":
                # t counts from run.started, so its own t is exactly 0
                self._t0_mono, self._t_offset = self._mono(), 0.0
                t = 0.0
            else:
                t = self._t()
            event: Record = {
                "seq": self._seq,
                "ts": _iso(self._wall()),
                "t": t,
                "run_id": self.run_id,
                "kind": kind,
            }
            if agent is not None:
                event["agent"] = agent
            if design_version is not None:
                event["design_version"] = design_version
            if domain is not None:
                event["domain"] = domain
            if text is not None:
                event["text"] = text
            if data is not None:
                event["data"] = data
            contract.validate("event", event)
            line = json.dumps(event, ensure_ascii=False, separators=(", ", ": "))
            with self.events_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
                f.flush()
            self._seq += 1
            return event

    def events(self) -> list[Record]:
        if not self.events_path.exists():
            return []
        with self.events_path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    # ------------------------------------------------------------------ project

    @property
    def project(self) -> Record:
        return self._project

    def _save_project(self) -> None:
        contract.validate("project", self._project)
        _write_json(self.path / layout.PROJECT_FILE, self._project)

    @property
    def design_version(self) -> int:
        """The newest design version (0 before the first design)."""
        return max(self._project.get("design_versions") or [0])

    def set_cost(self, cost_usd: float) -> Record:
        with self._lock:
            self._project["cost_usd"] = float(cost_usd)
            self._save_project()
            return self.emit("cost.updated", agent="system", data={"cost_usd": float(cost_usd)})

    def stop_requested(self) -> bool:
        """Someone (the screen's stop button) has put stop.request in the run folder."""
        return (self.path / layout.STOP_REQUEST_FILE).exists()

    def clear_stop_request(self) -> None:
        """The request was carried out: stop.request is present only while someone asks."""
        (self.path / layout.STOP_REQUEST_FILE).unlink(missing_ok=True)

    def finish(self, status: str, stop_reason: str | None = None, text: str | None = None,
               stop_code: str | None = None) -> Record:
        """Set the final status in project.json and emit run.finished (stop_code: why it
        ended, one of the contract's codes, for the screen to word)."""
        with self._lock:
            self._project["status"] = status
            if stop_reason:
                self._project["stop_reason"] = stop_reason
            self._save_project()
            data: Record = {"status": status}
            if stop_reason:
                data["stop_reason"] = stop_reason
            if stop_code:
                data["stop_code"] = stop_code
            return self.emit("run.finished", agent="system", text=text, data=data)

    # ------------------------------------------------------------------ design

    def write_design(self, design: Record, *, agent: str = "designer") -> Record:
        """Write v<NNN>/design.json for a new version and emit design.created."""
        with self._lock:
            contract.validate("design", design)
            v = design["design_version"]
            if v in (self._project.get("design_versions") or []):
                raise StoreError(f"design version {v} already exists; versions are never rewritten")
            if v != self.design_version + 1:
                raise StoreError(f"next design version must be {self.design_version + 1}, got {v}")
            _write_json(layout.version_dir(self.path, v) / layout.DESIGN_FILE, design)
            self._project.setdefault("design_versions", []).append(v)
            self._save_project()
            self.emit("design.created", agent=agent, design_version=v, data=design, text=design.get("change_reason"))
            return design

    def design(self, version: int | None = None) -> Record:
        v = version or self.design_version
        path = layout.version_dir(self.path, v) / layout.DESIGN_FILE
        if not path.exists():
            raise StoreError(f"no design version {v}")
        return _read_json(path)

    # ------------------------------------------------------------------ records

    def next_id(self, kind: str) -> str:
        """Reserve the next run-wide id of a record kind (pred-0001, ana-0001, ...)."""
        with self._lock:
            self._counters[kind] += 1
            return layout.format_id(kind, self._counters[kind])

    def add_record(self, kind: str, record: Record, *, design_version: int, agent: str,
                   domain: str | None = None, text: str | None = None) -> Record:
        """Validate, write v<NNN>/<kind dir>/<id>.json and emit the record's event.

        Called again with the same id, it rewrites the file and emits the event
        again (the update rule in the module docstring).
        """
        with self._lock:
            contract.validate(kind, record)
            rid = record[f"{kind}_id"]
            _write_json(layout.record_path(self.path, design_version, kind, rid), record)
            self.emit(RECORD_EVENT[kind], agent=agent, domain=domain, design_version=design_version,
                      text=text, data=record)
            return record

    def records(self, kind: str, design_version: int | None = None) -> list[Record]:
        """Records of one kind, in id order, for one version or for the whole run."""
        versions = [design_version] if design_version else sorted(self._project.get("design_versions") or [])
        out = []
        for v in versions:
            folder = layout.version_dir(self.path, v) / layout.RECORD_DIRS[kind]
            if folder.is_dir():
                out.extend(_read_json(p) for p in sorted(folder.glob("*.json")))
        return out

    def find(self, kind: str, record_id: str) -> tuple[int, Record] | None:
        """(design_version, record) for an id, or None."""
        for v in sorted(self._project.get("design_versions") or []):
            p = layout.record_path(self.path, v, kind, record_id)
            if p.exists():
                return v, _read_json(p)
        return None

    def get(self, kind: str, record_id: str) -> Record | None:
        hit = self.find(kind, record_id)
        return hit[1] if hit else None

    # ------------------------------------------------------------------ judgements

    def judgements(self, design_version: int | None = None) -> list[Record]:
        path = layout.version_dir(self.path, design_version or self.design_version) / layout.JUDGEMENTS_FILE
        return _read_json(path) if path.exists() else []

    def set_judgement(self, judgement: Record, *, agent: str = "system", text: str | None = None) -> Record:
        """Replace this domain's tile in v<NNN>/judgements.json and emit judgement.updated."""
        with self._lock:
            contract.validate("judgement", judgement)
            v = judgement["design_version"]
            tiles = [j for j in self.judgements(v) if j["domain"] != judgement["domain"]]
            tiles.append(judgement)
            order = {d: i for i, d in enumerate(contract.DOMAINS)}
            tiles.sort(key=lambda j: order[j["domain"]])
            _write_json(layout.version_dir(self.path, v) / layout.JUDGEMENTS_FILE, tiles)
            self.emit("judgement.updated", agent=agent, domain=judgement["domain"], design_version=v,
                      text=text, data=judgement)
            return judgement

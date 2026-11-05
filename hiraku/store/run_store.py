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
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from hiraku import contract
from hiraku.contract import layout

Record = dict[str, Any]

# Event kind emitted when a record of this kind is written (the contract's one map).
RECORD_EVENT = layout.RECORD_EVENT
# project.driver whose cost HIRAKU cannot know (the person's own Claude Code pays): cost_usd null
UNPRICED_DRIVER = "claude_code_plugin"
# A module designer's proposal (design.change_proposed, data a module_change) has a run-wide id chg-0001, ...
# It has no file of its own: layout has no folder for it, so the events are its record (the event's
# design_version is the version it was based on) and a proposal is built once a later design lists it in
# module_changes (build_version). ID_PREFIX is the contract's with this one added.
MODULE_CHANGE = "module_change"
CHANGE_PROPOSED = "design.change_proposed"
ID_PREFIX = {**layout.ID_PREFIX, MODULE_CHANGE: "chg"}


# Keys of an analysis that RunStore.reading() leaves out: the tools' large outputs for the screen and the export
# (fields on the members or shells, time series, the animation's scene). The hooks' and the rules' checks read
# results, status, tool and ids only.
READING_DROPS = ("fields", "series", "scene")


class StoreError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now().astimezone()


def _change_number(change: Record) -> int:
    """chg-0012 -> 12: proposals are numbered run-wide in the order they were made."""
    return int(change["change_id"].rsplit("-", 1)[1])


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    _retry(lambda: os.replace(tmp, path))


REPLACE_TRIES, REPLACE_WAIT_S = 20, 0.05  # Windows refuses a replace or a read while another holds the file open


def _retry(fn: Callable[[], Any]) -> Any:
    """fn(), tried again for a moment on PermissionError: on Windows, os.replace onto a file another process
    (the plugin's hook command, the screen) has open for reading is refused, and so is opening one being replaced."""
    for i in range(REPLACE_TRIES):
        try:
            return fn()
        except PermissionError:
            if i == REPLACE_TRIES - 1:
                raise
            time.sleep(REPLACE_WAIT_S)


def _read_json(path: Path) -> Any:
    def read() -> Any:
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    return _retry(read)


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
        self._version_lcs: dict[int, list[Record]] = {}  # v -> load cases of v00N/load_cases.json
        self._seq = 0
        self._t0_mono = mono()
        self._t_offset = 0.0  # t of the last event when reopened
        self._counters = {kind: 0 for kind in ID_PREFIX}
        # inside reading(): record files already looked at in this stretch, by path (None outside). Emptied on every
        # event, since every write of this store emits one
        self._read_cache: dict[str, Any] | None = None
        self._ids: dict[str, dict[str, tuple[int, Path]]] = {}  # inside reading(): kind -> record id -> (version, path)
        self._reading_depth = 0
        # what reading() read, kept across stretches: path -> ((mtime_ns, size), record); a file whose stat changed
        # (written since, by this process or another) is read again. Analyses are kept without READING_DROPS
        self._kept: dict[str, tuple[tuple[int, int], Any]] = {}

    # ------------------------------------------------------------------ create / open

    @classmethod
    def create(cls, project_fields: Record, *, root: Path = layout.RUNS_DIR, slug: str | None = None,
               wall: Callable[[], datetime] = _now, mono: Callable[[], float] = time.monotonic) -> "RunStore":
        """Make runs/<run_id>/, write project.json and emit run.started.

        project_fields: every project field except run_id, created_at, status and cost_usd (0.0, or
        null for driver claude_code_plugin).
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
            # null where HIRAKU cannot know the cost: the person's own Claude Code pays (plugin path)
            "cost_usd": None if project_fields.get("driver") == UNPRICED_DRIVER else 0.0,
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
        chg = [c["change_id"] for _, c in store._proposals(events)]
        chg += [c["change_id"] for v in store._project.get("design_versions") or [] for c in store.design(v).get("module_changes") or []]
        store._counters[MODULE_CHANGE] = max((int(i.rsplit("-", 1)[1]) for i in chg), default=0)
        return store

    # ------------------------------------------------------------------ events

    @property
    def events_path(self) -> Path:
        return self.path / layout.EVENTS_FILE

    def _t(self) -> float:
        return round(self._t_offset + (self._mono() - self._t0_mono), 3)

    def emit(self, kind: str, *, agent: str | None = None, domain: str | None = None,
             design_version: int | None = None, text: str | None = None, data: Record | None = None,
             data_checked: bool = False) -> Record:
        """Append one event to events.jsonl (validated) and return it. data_checked: data is a record add_record
        has just validated whole, so the event is validated with READING_DROPS left out of its data (the large
        arrays of an analysis: a 12 MB shell analysis took 3.7 s as a record and 4.0 s again as the event)."""
        with self._lock:
            if self._read_cache:
                self._read_cache.clear()  # the event goes with a write (or a hook's answer): read the files again
            self._ids.clear()
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
            if data_checked and isinstance(data, dict) and any(k in data for k in READING_DROPS):
                contract.validate("event", {**event, "data": {k: v for k, v in data.items() if k not in READING_DROPS}})
            else:
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

    def set_cost(self, cost_usd: float, extra: Record | None = None) -> Record:
        """project.json cost_usd and a cost.updated event; extra (token counts, turns) goes into the
        event's data next to cost_usd (the contract requires only cost_usd there)."""
        with self._lock:
            self._project["cost_usd"] = float(cost_usd)
            self._save_project()
            return self.emit("cost.updated", agent="system", data={**(extra or {}), "cost_usd": float(cost_usd)})

    def update_project(self, fields: Record, *, agent: str = "system", text: str | None = None) -> Record:
        """Change project fields after the start (the load cases, picks and roles of a run whose
        first version the designer made) and emit project.updated with the whole project."""
        with self._lock:
            contract.validate("project", dict(self._project, **fields))
            self._project.update(fields)
            self._save_project()
            self.emit("project.updated", agent=agent, text=text, data=self._project)
            return self._project

    def write_report(self, report: Record, *, agent: str = "lead") -> Record:
        """Write report.json (validated) and emit report.written with the same object; a later
        report replaces it, as the file always equals the last event."""
        with self._lock:
            contract.validate("report", report)
            _write_json(self.path / layout.REPORT_FILE, report)
            # a report before the first version says design_version 0; an event's design_version is 1 or more
            self.emit("report.written", agent=agent, design_version=report["design_version"] or None, data=report)
            return report

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

    def write_version_load_cases(self, version: int, load_cases: list[Record], *, agent: str = "system",
                                 text: str | None = None) -> Record:
        """Write v<NNN>/load_cases.json, the load cases built for a version from 2 on, and emit
        load_cases.created (data: version_load_cases). project.load_cases stays the first version's."""
        with self._lock:
            if version < 2 or version not in (self._project.get("design_versions") or []):
                raise StoreError(f"load cases are written per version from version 2, for a version that exists (got {version})")
            data = {"design_version": version, "load_cases": load_cases}
            contract.validate("version_load_cases", data)
            _write_json(layout.version_dir(self.path, version) / layout.VERSION_LOAD_CASES_FILE, data)
            self._version_lcs[version] = load_cases
            self.emit("load_cases.created", agent=agent, design_version=version, data=data, text=text)
            return data

    def version_load_cases(self, version: int | None = None) -> list[Record]:
        """The load cases of a version: its own load_cases.json, else the nearest earlier version's,
        else project.load_cases (version 1, and runs made before load cases were built per version)."""
        with self._lock:
            v = version or self.design_version
            while v and v >= 2:
                if v not in self._version_lcs:  # a version's file is written once, never rewritten
                    path = layout.version_dir(self.path, v) / layout.VERSION_LOAD_CASES_FILE
                    if path.exists():
                        self._version_lcs[v] = _read_json(path)["load_cases"]
                if v in self._version_lcs:
                    return self._version_lcs[v]
                v -= 1
            return self._project.get("load_cases") or []

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
            return f"{ID_PREFIX[kind]}-{self._counters[kind]:04d}"

    def add_record(self, kind: str, record: Record, *, design_version: int, agent: str,
                   domain: str | None = None, text: str | None = None) -> Record:
        """Validate, write v<NNN>/<kind dir>/<id>.json and emit the record's event.

        Called again with the same id, it rewrites the file and emits the event
        again (the update rule in the module docstring).
        """
        with self._lock:
            contract.validate(kind, record)
            rid = record[f"{kind}_id"]
            path = layout.record_path(self.path, design_version, kind, rid)
            _write_json(path, record)
            self._kept.pop(str(path), None)  # rewritten: read again (also when the stat would not show it)
            self.emit(RECORD_EVENT[kind], agent=agent, domain=domain, design_version=design_version,
                      text=text, data=record, data_checked=True)
            return record

    @contextmanager
    def reading(self) -> Iterator[None]:
        """A stretch of reads that change nothing (the hooks' and the rules' checks): records(), find() and get()
        hand back the same objects for a file, so the caller must not change them. A file is read once and kept
        for later stretches while its stat (mtime, size) stays the same, so a check reads only the records
        written since the last one. Analyses come without READING_DROPS (the tools' fields, time series and
        scenes: large, and no check reads them). The store's lock is held throughout (tools run in threads); an
        event (every write emits one) makes the stretch look at the files again. Nested calls share the outer
        stretch."""
        with self._lock:
            self._reading_depth += 1
            if self._reading_depth == 1:
                self._read_cache = {}
            try:
                yield
            finally:
                self._reading_depth -= 1
                if self._reading_depth == 0:
                    self._read_cache = None
                    self._ids.clear()

    def _read_record(self, path: Path) -> Any:
        cache = self._read_cache
        if cache is None:
            return _read_json(path)
        key = str(path)
        if key not in cache:
            st = os.stat(key)
            stamp = (st.st_mtime_ns, st.st_size)
            kept = self._kept.get(key)
            if kept is None or kept[0] != stamp:
                rec = _read_json(path)
                if path.parent.name == layout.RECORD_DIRS["analysis"] and isinstance(rec, dict):
                    rec = {k: v for k, v in rec.items() if k not in READING_DROPS}
                kept = self._kept[key] = (stamp, rec)
            cache[key] = kept[1]
        return cache[key]

    def _id_index(self, kind: str) -> dict[str, tuple[int, Path]]:
        """Inside reading(): record id -> (version, path) of a kind, from one listing of each version's folder."""
        got = self._ids.get(kind)
        if got is None:
            got = {}
            for v in sorted(self._project.get("design_versions") or []):
                folder = layout.version_dir(self.path, v) / layout.RECORD_DIRS[kind]
                if folder.is_dir():
                    for p in folder.glob("*.json"):
                        got.setdefault(p.stem, (v, p))
            self._ids[kind] = got
        return got

    def records(self, kind: str, design_version: int | None = None) -> list[Record]:
        """Records of one kind, in id order, for one version or for the whole run."""
        with self._lock:  # tools run in threads (the MCP servers): no read while another thread replaces a file
            versions = [design_version] if design_version else sorted(self._project.get("design_versions") or [])
            out = []
            for v in versions:
                folder = layout.version_dir(self.path, v) / layout.RECORD_DIRS[kind]
                if folder.is_dir():
                    out.extend(self._read_record(p) for p in sorted(folder.glob("*.json")))
            return out

    def find(self, kind: str, record_id: str) -> tuple[int, Record] | None:
        """(design_version, record) for an id, or None."""
        with self._lock:
            if self._read_cache is not None:
                hit = self._id_index(kind).get(record_id)
                return (hit[0], self._read_record(hit[1])) if hit else None
            for v in sorted(self._project.get("design_versions") or []):
                p = layout.record_path(self.path, v, kind, record_id)
                if p.exists():
                    return v, self._read_record(p)
            return None

    def get(self, kind: str, record_id: str) -> Record | None:
        hit = self.find(kind, record_id)
        return hit[1] if hit else None

    # ------------------------------------------------------------------ module changes (designs with modules)

    def propose_change(self, change: Record, *, design_version: int, agent: str, text: str | None = None) -> Record:
        """Emit design.change_proposed: data is the module_change (validated), the event's design_version the
        version it was based on. No version is made; build_version builds the round's proposals into one."""
        with self._lock:
            contract.validate(MODULE_CHANGE, change)
            if design_version not in (self._project.get("design_versions") or []):
                raise StoreError(f"no design version {design_version} to base a proposal on")
            self.emit(CHANGE_PROPOSED, agent=agent, design_version=design_version, text=text, data=change)
            return change

    @staticmethod
    def _proposals(events: list[Record]) -> list[tuple[int, Record]]:
        """(based-on version, module_change) of every design.change_proposed, the last event per change_id."""
        latest: dict[str, tuple[int, Record]] = {}
        for e in events:
            if e["kind"] == CHANGE_PROPOSED and isinstance(e.get("data"), dict) and "change_id" in e["data"]:
                latest[e["data"]["change_id"]] = (e.get("design_version") or 0, e["data"])
        return list(latest.values())

    def built_change_ids(self) -> set[str]:
        """The change_ids some version was built from (its design's module_changes)."""
        with self._lock:
            return {c["change_id"] for v in self._project.get("design_versions") or []
                    for c in self.design(v).get("module_changes") or []}

    def proposals(self, design_version: int | None = None, *, pending: bool = False) -> list[Record]:
        """The module designers' proposals, in id order: those based on one version (or all); with pending,
        only those no version was built from yet, and of those only each module's latest on its version (a module
        has one proposal per round: a new one replaces the module's earlier proposal that was not built,
        superseded_change_ids)."""
        with self._lock:  # tools run in threads: no read while another thread appends or replaces
            got = self._proposals(self.events())
            built = self.built_change_ids() if pending else set()
            out = sorted(((v, c) for v, c in got if (design_version is None or v == design_version)
                          and c["change_id"] not in built), key=lambda vc: _change_number(vc[1]))
            if pending:
                last = {(v, c["module_id"]): c["change_id"] for v, c in out}
                out = [(v, c) for v, c in out if last[(v, c["module_id"])] == c["change_id"]]
            return [c for _, c in out]

    def superseded_change_ids(self, design_version: int | None = None) -> list[str]:
        """Proposals based on the version (default: the current) that no version was built from and that a later
        proposal of the same module replaced, in id order."""
        with self._lock:
            v = design_version or self.design_version
            built, kept = self.built_change_ids(), {c["change_id"] for c in self.pending_proposals(v)}
            return [c["change_id"] for c in self.proposals(v) if c["change_id"] not in built | kept]

    def pending_proposals(self, design_version: int | None = None) -> list[Record]:
        """The proposals based on the current version (or the one given) that no version was built from yet."""
        with self._lock:
            return self.proposals(design_version or self.design_version, pending=True)

    # ------------------------------------------------------------------ judgements

    def judgements(self, design_version: int | None = None) -> list[Record]:
        with self._lock:
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

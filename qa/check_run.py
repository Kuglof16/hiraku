"""Check one run folder (runs/<run_id>/) against the contract and the hook rules.

    python -m qa.check_run runs/<run_id> [runs/<other> ...] [--all] [--strict] [--recompute] [--effects]

Reads only. Lists every finding as `LEVEL rule where: message` and exits 1 when
there is an error (or a warning with --strict). --effects also lists where the
hooks acted (each denial or blocked stop, live or staged by the script, and the
record that followed), the mismatches with their causes and the verdicts with
their basis. What it looks at:

- shape: project.json, every line of events.jsonl and every record file fit
  hiraku.schema.json; seq runs 0, 1, 2, ... without gaps; t and ts never go back
- files and events: each record file has exactly one event whose data is the
  same object, and each record event has its file (layout.py)
- ids: unique, numbered 1..n within the run, file name = id, right v### folder
- HK1: every analysis names predictions of the same version and load case that
  were recorded before it
- HK2: a tool is used only after a tool.checked that says usable (warning when
  the run has no tool.checked for it at all)
- HK3: after a mismatch, no analysis or comparison in that domain until a cause
- assembly and interferences in an analysis: steps numbered 1..n, each part
  once, dependencies go in earlier, blocked_by only when no_interference is false;
  no part interferes with itself; the tool's interference counts and deepest
  overlap agree with its list
- comparisons: values copied from the prediction and the analysis, signed diffs
  (analyzed - predicted) and status recomputed, tolerance taken from the load case
- verdicts: from a matching comparison with the analysis value, or (basis
  explained_safe_side) from a mismatch whose own earlier cause says safe_side with a
  reason, the prediction on the unfavourable side of the criterion and taken as the
  value; pass and margin recomputed; limit and op from the load case criterion
- judgements: red_count and state follow the listed verdicts
- HK4: a run that finished as done has no picked domain still pending
- agents: each agent.started has its agent.stopped before run.finished (warning:
  an agent still working when the run ended was cut off)
- run.finished: stop_code done exactly when the status is done (error with
  failed); not_judged only with a picked domain still pending; round_limit only
  when the design revisions reached round_limit; stop_reason as in project.json
- scene in an analysis: a road profile has one height per x along an increasing
  x, sizes are not negative, angle_channel names a series of that analysis
- --recompute: each prediction value is reproduced by K0 from its formula and
  inputs (needs hiraku.solvers.formula_registry())
"""

from __future__ import annotations

import argparse
import json
import math
import operator
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from hiraku import contract
from hiraku.contract import layout
from hiraku.contract.protocols import HOOK_RULES

# event kind -> (record kind in the schema, id field). None id = keyed differently.
RECORD_EVENTS = {
    "prediction.recorded": ("prediction", "prediction_id"),
    "analysis.finished": ("analysis", "analysis_id"),
    "comparison.recorded": ("comparison", "comparison_id"),
    "cause.recorded": ("cause", "cause_id"),
    "verdict.recorded": ("verdict", "verdict_id"),
}
# A record is updated by emitting its event again with the same id and the whole
# new record; the last event wins and the file equals it (layout.py). Only these
# fields may change in an update; any other change rewrites a recorded result.
UPDATABLE_FIELDS = {"comparison": {"cause_id"}, "cause": {"resolved", "resolved_by"}}
# project.json fields the contract lets change after run.started.
PROJECT_MUTABLE = {"status", "stop_reason", "requirements", "cost_usd", "design_versions"}
OPS = {"<=": operator.le, ">=": operator.ge, "<": operator.lt, ">": operator.gt}
# results the assembly tool reports about its own interference list (the analysis
# dept's quantity names): count and deepest overlap per state. Checked when reported.
INTERFERENCE_COUNTS = {"interference_count": "rest", "motion_interference_count": "motion"}
INTERFERENCE_WORST = {"worst_interference": "rest", "worst_motion_interference": "motion"}
# the record a denied call should be followed by when the agent does what the hook asks
FOLLOW_UP = {"HK1_prediction_first": "prediction.recorded", "HK3_cause_before_continue": "cause.recorded"}
VERSION_DIR_RE = re.compile(r"^v([0-9]{3})$")
REL = 1e-9


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "warn"
    rule: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.level.upper():5} {self.rule:22} {self.where}: {self.message}"


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=REL, abs_tol=1e-12)


def _num(x: Any, scale: float = 1.0, spec: str = ".1f") -> str:
    return format(x * scale, spec) if isinstance(x, (int, float)) else "?"


def _fmt(q: Any) -> str:
    """A {value, unit} quantity for reading: 4 significant digits."""
    q = q if isinstance(q, dict) else {}
    return f"{_num(q.get('value'), spec='.4g')} {q.get('unit', '?')}"


class RunChecker:
    def __init__(self, run_path: Path, recompute: bool = False):
        self.path = Path(run_path)
        self.recompute = recompute
        self.findings: list[Finding] = []
        self.project: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []
        self.where_of: list[str] = []  # location of each event, same index as self.events
        self.load_cases: dict[str, dict] = {}
        # record kind -> id -> (event index, data)
        self.recs: dict[str, dict[str, tuple[int, dict]]] = {k: {} for k, _ in RECORD_EVENTS.values()}
        self.designs: dict[int, tuple[int, dict]] = {}
        self.judgements: list[tuple[int, dict]] = []
        self.tool_checks: dict[str, list[tuple[int, dict]]] = {}
        self.started: dict[str, int] = {}

    # --- reporting ---------------------------------------------------------------
    def error(self, rule: str, where: str, message: str) -> None:
        self.findings.append(Finding("error", rule, where, message))

    def warn(self, rule: str, where: str, message: str) -> None:
        self.findings.append(Finding("warn", rule, where, message))

    def rel(self, p: Path) -> str:
        try:
            return p.relative_to(self.path).as_posix()
        except ValueError:
            return str(p)

    # --- loading -----------------------------------------------------------------
    def _read_json(self, p: Path) -> Any:
        try:
            with p.open(encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            self.error("file.unreadable", self.rel(p), str(e))
            return None

    def _schema(self, kind: str, obj: Any, where: str) -> bool:
        errs = contract.errors(kind, obj)
        for e in errs[:10]:
            self.error("schema", where, e)
        if len(errs) > 10:
            self.error("schema", where, f"... and {len(errs) - 10} more")
        return not errs

    def load_project(self) -> None:
        p = self.path / layout.PROJECT_FILE
        if not p.exists():
            self.error("file.missing", layout.PROJECT_FILE, "no project.json")
            return
        self.project = self._read_json(p)
        if self.project is None:
            return
        self._schema("project", self.project, layout.PROJECT_FILE)
        rid = self.project.get("run_id")
        if rid != self.path.name:
            self.error("project.run_id", layout.PROJECT_FILE, f"run_id {rid!r} is not the folder name {self.path.name!r}")
        for lc in self.project.get("load_cases", []):
            if lc.get("load_case_id") in self.load_cases:
                self.error("project.load_case", layout.PROJECT_FILE, f"load case {lc['load_case_id']} listed twice")
            self.load_cases[lc.get("load_case_id")] = lc

    def load_events(self) -> None:
        p = self.path / layout.EVENTS_FILE
        if not p.exists():
            self.error("file.missing", layout.EVENTS_FILE, "no events.jsonl")
            return
        with p.open(encoding="utf-8") as f:
            for n, line in enumerate(f, start=1):
                where = f"{layout.EVENTS_FILE}:{n}"
                if not line.strip():
                    self.warn("events.blank", where, "blank line")
                    continue
                try:
                    e = json.loads(line)
                except json.JSONDecodeError as ex:
                    self.error("events.json", where, str(ex))
                    continue
                self._schema("event", e, where)
                if isinstance(e, dict):
                    self.events.append(e)
                    self.where_of.append(f"{where} seq={e.get('seq')}")

    # --- events ------------------------------------------------------------------
    def check_sequence(self) -> None:
        if not self.events:
            self.error("events.empty", layout.EVENTS_FILE, "no events")
            return
        rid = self.project.get("run_id") if self.project else self.path.name
        prev_t, prev_ts = None, None
        for i, e in enumerate(self.events):
            w = self.where_of[i]
            if e.get("seq") != i:
                self.error("events.seq", w, f"seq {e.get('seq')} where {i} was expected (gap, repeat or reorder)")
            if e.get("run_id") != rid:
                self.error("events.run_id", w, f"run_id {e.get('run_id')!r} is not {rid!r}")
            t = e.get("t")
            if isinstance(t, (int, float)):
                if prev_t is not None and t < prev_t:
                    self.error("events.t", w, f"t goes back from {prev_t} to {t}")
                prev_t = t
            try:
                ts = datetime.fromisoformat(e.get("ts", ""))
                if ts.tzinfo is None:
                    self.error("events.ts", w, "ts has no UTC offset")
                elif prev_ts is not None and ts < prev_ts:
                    self.error("events.ts", w, "ts goes back")
                else:
                    prev_ts = ts
            except (TypeError, ValueError):
                self.error("events.ts", w, f"ts {e.get('ts')!r} is not ISO 8601")
        first = self.events[0]
        if first.get("kind") != "run.started":
            self.error("events.first", self.where_of[0], f"first event is {first.get('kind')}, not run.started")
        if first.get("t") not in (0, 0.0):
            self.error("events.t", self.where_of[0], "t of run.started is not 0")
        kinds = [e.get("kind") for e in self.events]
        if kinds.count("run.started") > 1:
            self.error("events.run_started", layout.EVENTS_FILE, "more than one run.started")
        if "run.finished" in kinds:
            if kinds.count("run.finished") > 1:
                self.error("events.run_finished", layout.EVENTS_FILE, "more than one run.finished")
            if kinds[-1] != "run.finished":
                self.error("events.run_finished", self.where_of[kinds.index("run.finished")],
                           "events follow run.finished")

    def index_events(self) -> None:
        for i, e in enumerate(self.events):
            kind, data, w = e.get("kind"), e.get("data"), self.where_of[i]
            if not isinstance(data, dict):
                continue
            if kind in RECORD_EVENTS:
                rkind, idf = RECORD_EVENTS[kind]
                rid = data.get(idf)
                if rid in self.recs[rkind]:
                    first, old = self.recs[rkind][rid]
                    changed = sorted(k for k in set(old) | set(data) if old.get(k) != data.get(k))
                    bad = [k for k in changed if k not in UPDATABLE_FIELDS.get(rkind, set())]
                    if bad:
                        self.error("record.changed", w, f"{rid} re-sent with {', '.join(bad)} changed "
                                                        f"(an update may change only "
                                                        f"{', '.join(sorted(UPDATABLE_FIELDS.get(rkind, ()))) or 'nothing'})")
                    self.recs[rkind][rid] = (first, data)  # first appearance, latest content
                else:
                    self.recs[rkind][rid] = (i, data)
            elif kind == "design.created":
                v = data.get("design_version")
                if v in self.designs:
                    self.error("ids.duplicate", w, f"design version {v} created twice")
                self.designs[v] = (i, data)
            elif kind == "judgement.updated":
                self.judgements.append((i, data))
            elif kind == "tool.checked":
                self.tool_checks.setdefault(data.get("name"), []).append((i, data))
            elif kind == "analysis.started":
                self.started.setdefault(data.get("analysis_id"), i)
            self._event_fields_agree(i, e, data)

    def _event_fields_agree(self, i: int, e: dict, data: dict) -> None:
        w = self.where_of[i]
        for field in ("design_version", "domain"):
            if field in e and field in data and e[field] != data[field]:
                self.error("events.fields", w, f"event {field} {e[field]!r} but data says {data[field]!r}")
        if e.get("kind") == "prediction.recorded" and "agent" in e and e["agent"] != data.get("agent"):
            self.error("events.fields", w, f"event agent {e['agent']} but prediction agent {data.get('agent')}")

    def check_ids(self) -> None:
        for rkind, recs in self.recs.items():
            prefix = layout.ID_PREFIX[rkind]
            nums = []
            for rid, (i, _) in recs.items():
                m = re.fullmatch(rf"{prefix}-([0-9]+)", str(rid))
                if not m:
                    self.error("ids.format", self.where_of[i], f"{rid!r} is not {prefix}-NNNN")
                else:
                    nums.append((i, int(m.group(1))))
            got = sorted(n for _, n in nums)
            if got != list(range(1, len(got) + 1)):
                self.error("ids.numbering", layout.EVENTS_FILE, f"{prefix} ids are {got}, not 1..{len(got)}")
            order = [n for _, n in sorted(nums)]
            if order != sorted(order):
                self.warn("ids.order", layout.EVENTS_FILE, f"{prefix} ids not in event order: {order}")

    def check_project_vs_start(self) -> None:
        if not self.project or not self.events or self.events[0].get("kind") != "run.started":
            return
        start = self.events[0].get("data") or {}
        for key in sorted(set(start) | set(self.project)):
            if key in PROJECT_MUTABLE:
                continue
            if start.get(key) != self.project.get(key):
                self.error("project.changed", layout.PROJECT_FILE,
                           f"{key} differs from run.started data (only {', '.join(sorted(PROJECT_MUTABLE))} may change)")
        versions = self.project.get("design_versions")
        if versions is not None and sorted(versions) != sorted(self.designs):
            self.warn("project.versions", layout.PROJECT_FILE,
                      f"design_versions {versions} but design.created for {sorted(self.designs)}")

    # --- files -------------------------------------------------------------------
    def check_files(self) -> None:
        seen = {k: set() for k in self.recs}
        seen_designs = set()
        for p in sorted(self.path.iterdir()) if self.path.is_dir() else []:
            name = p.name
            if name in (layout.PROJECT_FILE, layout.EVENTS_FILE, layout.REPORT_FILE):
                continue
            m = VERSION_DIR_RE.match(name)
            if p.is_dir() and m:
                self._check_version_dir(p, int(m.group(1)), seen, seen_designs)
            else:
                self.warn("files.unknown", self.rel(p), "not part of the run layout")
        for rkind, recs in self.recs.items():
            for rid, (i, _) in recs.items():
                if rid not in seen[rkind]:
                    self.error("files.missing", self.where_of[i], f"{rid} has an event but no file")
        for v, (i, _) in self.designs.items():
            if v not in seen_designs:
                self.error("files.missing", self.where_of[i], f"design version {v} has an event but no design.json")

    def _check_version_dir(self, vdir: Path, version: int, seen, seen_designs) -> None:
        dirs = {d: k for k, d in layout.RECORD_DIRS.items()}
        for p in sorted(vdir.iterdir()):
            if p.name == layout.DESIGN_FILE:
                seen_designs.add(version)
                self._check_design_file(p, version)
            elif p.name == layout.JUDGEMENTS_FILE:
                self._check_judgements_file(p, version)
            elif p.is_dir() and p.name in dirs:
                rkind = dirs[p.name]
                for f in sorted(p.iterdir()):
                    self._check_record_file(f, rkind, version, seen)
            else:
                self.warn("files.unknown", self.rel(p), "not part of the run layout")

    def _check_design_file(self, p: Path, version: int) -> None:
        d = self._read_json(p)
        if d is None or not self._schema("design", d, self.rel(p)):
            return
        if d.get("design_version") != version:
            self.error("files.version", self.rel(p), f"design_version {d.get('design_version')} in folder v{version:03d}")
        ev = self.designs.get(version)
        if ev and ev[1] != d:
            self.error("files.event_data", self.rel(p), "design.json differs from its design.created event")

    def _check_record_file(self, p: Path, rkind: str, version: int, seen) -> None:
        where = self.rel(p)
        if p.suffix != ".json":
            self.warn("files.unknown", where, "not a .json record")
            return
        rec = self._read_json(p)
        if rec is None or not self._schema(rkind, rec, where):
            return
        rid = rec.get(f"{rkind}_id")
        seen[rkind].add(rid)
        if p.stem != rid:
            self.error("files.name", where, f"file name {p.stem} but id {rid}")
        ev = self.recs[rkind].get(rid)
        if ev is None:
            self.error("files.no_event", where, f"{rid} has a file but no {rkind} event")
        elif ev[1] != rec:
            self.error("files.event_data", where, f"{rid} differs from the data of its event ({self.where_of[ev[0]]})")
        rv = self.version_of(rkind, rec)
        if rv is not None and rv != version:
            self.error("files.version", where, f"{rid} belongs to design version {rv}, not v{version:03d}")

    def _check_judgements_file(self, p: Path, version: int) -> None:
        js = self._read_json(p)
        where = self.rel(p)
        if js is None:
            return
        if not isinstance(js, list):
            self.error("schema", where, "judgements.json is not a list")
            return
        latest = self.latest_judgements(version)
        in_file = {}
        for n, j in enumerate(js):
            if self._schema("judgement", j, f"{where}[{n}]"):
                in_file[j["domain"]] = j
                if j["design_version"] != version:
                    self.error("files.version", f"{where}[{n}]", f"design_version {j['design_version']}")
        for dom in set(latest) | set(in_file):
            if latest.get(dom) != in_file.get(dom):
                self.error("files.event_data", where, f"{dom}: judgements.json differs from the last judgement.updated")

    # --- links -------------------------------------------------------------------
    def version_of(self, rkind: str, rec: dict) -> int | None:
        if rkind in ("prediction", "analysis"):
            return rec.get("design_version")
        if rkind == "comparison":
            p = self.recs["prediction"].get(rec.get("prediction_id"))
            return p[1].get("design_version") if p else None
        if rkind in ("cause", "verdict"):
            c = self.recs["comparison"].get(rec.get("comparison_id"))
            return self.version_of("comparison", c[1]) if c else None
        return None

    def domain_of_analysis(self, a: dict) -> str | None:
        lc = self.load_cases.get(a.get("load_case_id"))
        if lc:
            return lc.get("domain")
        for pid in a.get("prediction_ids", []):
            p = self.recs["prediction"].get(pid)
            if p:
                return p[1].get("domain")
        return None

    def latest_judgements(self, version: int) -> dict[str, dict]:
        out = {}
        for _, j in self.judgements:
            if j.get("design_version") == version:
                out[j.get("domain")] = j
        return out

    def _before(self, rkind: str, rid: str, i: int, w: str, rule: str, what: str) -> dict | None:
        rec = self.recs[rkind].get(rid)
        if rec is None:
            self.error(rule, w, f"{what} {rid} was never recorded")
            return None
        if rec[0] > i:
            self.error(rule, w, f"{what} {rid} was recorded after this event ({self.where_of[rec[0]]})")
        return rec[1]

    # --- rules -------------------------------------------------------------------
    def check_predictions(self) -> None:
        for pid, (i, p) in self.recs["prediction"].items():
            w = self.where_of[i]
            v = p.get("design_version")
            if v not in self.designs or self.designs[v][0] > i:
                self.error("prediction.design", w, f"{pid} is for design version {v}, not created before it")
            lc = self.load_cases.get(p.get("load_case_id"))
            if lc is None:
                self.error("prediction.load_case", w, f"{pid}: load case {p.get('load_case_id')} not in project.json")
            else:
                for f in ("test_id", "domain"):
                    if lc.get(f) != p.get(f):
                        self.error("prediction.load_case", w, f"{pid}: {f} {p.get(f)} but the load case says {lc.get(f)}")

    def check_analyses(self) -> None:
        for i, e in enumerate(self.events):
            if e.get("kind") == "analysis.started" and isinstance(e.get("data"), dict):
                for pid in e["data"].get("prediction_ids", []):
                    self._before("prediction", pid, i, self.where_of[i], "HK1_prediction_first", "prediction")
        for aid, (i, a) in self.recs["analysis"].items():
            w = self.where_of[i]
            if aid not in self.started:
                self.warn("analysis.started", w, f"{aid} finished without an analysis.started event")
            elif self.started[aid] > i:
                self.error("analysis.started", w, f"{aid} started after it finished")
            if not a.get("prediction_ids"):
                self.error("HK1_prediction_first", w, f"{aid} has no prediction")
            for pid in a.get("prediction_ids", []):
                p = self._before("prediction", pid, self.started.get(aid, i), w, "HK1_prediction_first", "prediction")
                if p is None:
                    continue
                for f in ("design_version", "load_case_id"):
                    if p.get(f) != a.get(f):
                        self.error("HK1_prediction_first", w, f"{aid} {f} {a.get(f)} but {pid} has {p.get(f)}")
            self._check_tool_usable(a, self.started.get(aid, i), w)
            if a.get("status") == "failed" and not str(a.get("error", "")).strip():
                self.error("analysis.error", w, f"{aid} failed without saying why")

    def _check_tool_usable(self, a: dict, i: int, w: str) -> None:
        name = a.get("tool")
        checks = [(j, s) for j, s in self.tool_checks.get(name, []) if j < i]
        if not checks:
            if name in self.tool_checks:
                self.error("HK2_tool_usable", w, f"tool {name} was used before its tool.checked")
            else:
                self.warn("HK2_tool_usable", w, f"no tool.checked for {name} in this run")
            return
        if not checks[-1][1].get("usable"):
            self.error("HK2_tool_usable", w, f"tool {name} used while its last check says usable=false")

    def _design_ids(self, version: int) -> set[str] | None:
        d = self.designs.get(version)
        if d is None:
            return None
        return {x.get("id") for g in ("parts", "members", "supports", "nodes") for x in d[1].get(g, [])}

    @staticmethod
    def _names_a_thing(name: str, ids: set[str] | None) -> bool:
        """An id of the design, bare or after a kind (part:<id>, member:<id>, "member <id>")."""
        if ids is None:
            return True
        if not isinstance(name, str) or not name.strip():
            return False
        return name in ids or name.replace(":", " ").split()[-1] in ids

    def check_assembly(self) -> None:
        for aid, (i, a) in self.recs["analysis"].items():
            w = self.where_of[i]
            ids = self._design_ids(a.get("design_version"))
            # a step without an integer order is already a schema error
            steps = [s for s in a.get("assembly") or [] if isinstance(s, dict) and isinstance(s.get("order"), int)]
            orders = sorted(s["order"] for s in steps)
            if orders != list(range(1, len(steps) + 1)):
                self.error("analysis.assembly", w, f"{aid} assembly orders {orders} are not 1..{len(steps)} once each")
            step_of: dict[str, int] = {}
            for s in steps:
                part = s.get("part")
                if part in step_of:
                    self.error("analysis.assembly", w, f"{aid} puts {part} in twice (steps {step_of[part]} and {s.get('order')})")
                step_of.setdefault(part, s.get("order"))
            for s in steps:
                n, part = s.get("order"), s.get("part")
                if not self._names_a_thing(part, ids):
                    self.warn("analysis.assembly", w, f"{aid} step {n}: {part} is not in design v{a.get('design_version')}")
                for dep in s.get("depends_on", []):
                    if dep in step_of and step_of[dep] >= n:
                        self.error("analysis.assembly", w, f"{aid} step {n}: {part} depends on {dep}, which goes in at step {step_of[dep]}")
                    elif dep not in step_of and not self._names_a_thing(dep, ids):
                        self.warn("analysis.assembly", w, f"{aid} step {n}: depends on {dep}, which is neither a step nor in the design")
                # blocked_by says what stops a part going in, so it goes with no_interference
                # (a part can go in and still lack tool clearance or hand reach)
                if s.get("no_interference") and s.get("blocked_by"):
                    self.error("analysis.assembly", w, f"{aid} step {n}: {part} goes in without interference but is blocked by {s['blocked_by']}")
                elif s.get("no_interference") is False and not s.get("blocked_by"):
                    self.warn("analysis.assembly", w, f"{aid} step {n}: {part} cannot go in but names nothing in blocked_by")
            inters = [f for f in a.get("interferences") or [] if isinstance(f, dict)]
            seen = set()
            for f in inters:
                pair = (frozenset((str(f.get("a")), str(f.get("b")))), str(f.get("state")), str(f.get("condition")))
                if f.get("a") == f.get("b"):
                    self.error("analysis.interference", w, f"{aid} lists {f.get('a')} as interfering with itself")
                elif pair in seen:
                    self.warn("analysis.interference", w, f"{aid} lists {f.get('a')} x {f.get('b')} ({f.get('state')}) twice")
                seen.add(pair)
                for n in (f.get("a"), f.get("b")):
                    if not self._names_a_thing(n, ids):
                        self.warn("analysis.interference", w, f"{aid}: {n} is not in design v{a.get('design_version')}")
            results = {r.get("quantity"): r.get("value") for r in a.get("results", [])}
            for q, state in INTERFERENCE_COUNTS.items():
                if not isinstance(results.get(q), (int, float)):
                    continue
                n = sum(1 for f in inters if f.get("state") == state)
                if "interferences" not in a:
                    if results[q] > 0:
                        self.warn("analysis.interference", w, f"{aid} reports {q} {results[q]} but no interferences list")
                elif not _close(results[q], n):
                    self.error("analysis.interference", w, f"{aid} reports {q} {results[q]} but lists {n} ({state})")
            for q, state in INTERFERENCE_WORST.items():
                depths = [f.get("depth_mm", 0.0) for f in inters if f.get("state") == state]
                if "interferences" in a and isinstance(results.get(q), (int, float)):
                    worst = max(depths, default=0.0)
                    if not _close(results[q], worst):
                        self.error("analysis.interference", w, f"{aid} reports {q} {results[q]} but the deepest listed is {worst}")

    def check_scene(self) -> None:
        """The test scene an analysis draws: a road profile has one height per x along an
        increasing x, sizes are not negative, and a moving object names a series of its analysis."""
        for aid, (i, a) in self.recs["analysis"].items():
            w = self.where_of[i]
            channels = {s.get("channel") for s in a.get("series") or [] if isinstance(s, dict)}
            for k, obj in enumerate(a.get("scene") or []):
                if not isinstance(obj, dict):
                    continue
                tag = f"{aid} scene[{k}] {obj.get('kind')}"
                size = obj.get("size_mm")
                if isinstance(size, list) and any(isinstance(x, (int, float)) and x < 0 for x in size):
                    self.error("analysis.scene", w, f"{tag}: negative size {size}")
                prof = obj.get("profile")
                if obj.get("kind") == "road_profile" and not prof:
                    self.error("analysis.scene", w, f"{tag} without a profile")
                elif prof and obj.get("kind") != "road_profile":
                    self.warn("analysis.scene", w, f"{tag} has a profile (meant for road_profile)")
                if isinstance(prof, dict):
                    xs = [x for x in prof.get("x_mm") or [] if isinstance(x, (int, float))]
                    if any(b <= a_ for a_, b in zip(xs, xs[1:])):
                        self.error("analysis.scene", w, f"{tag}: x_mm does not increase")
                    zs = prof.get("z_mm")
                    for track, z in (zs.items() if isinstance(zs, dict) else [("", zs)]):
                        if isinstance(z, list) and len(z) != len(xs):
                            self.error("analysis.scene", w, f"{tag}{' ' + track if track else ''}: "
                                                            f"{len(z)} heights for {len(xs)} x values")
                ch = obj.get("angle_channel")
                if ch is not None and ch not in channels:
                    self.error("analysis.scene", w, f"{tag}: angle_channel {ch!r} is not a series of {aid} "
                                                    f"({', '.join(sorted(map(str, channels))) or 'none'})")

    def check_tool_specs(self) -> None:
        for name, specs in self.tool_checks.items():
            for i, s in specs:
                w = self.where_of[i]
                checks = s.get("checks", [])
                for c in checks:
                    got = c.get("got")
                    scale = abs(c["expected"]) if c["expected"] != 0 else 1.0  # absolute when expecting 0
                    ok = (isinstance(got, (int, float)) and math.isfinite(got)
                          and abs(got - c["expected"]) <= c["tolerance_rel"] * scale)
                    if c.get("pass") != ok:
                        self.error("tool.check", w, f"{name} case {c.get('case')!r}: pass={c.get('pass')} "
                                                     f"but expected {c.get('expected')} got {got} tol {c.get('tolerance_rel')}")
                usable = bool(checks) and all(c.get("pass") for c in checks)
                if s.get("usable") != usable:
                    self.error("tool.check", w, f"{name} usable={s.get('usable')} but its checks say {usable}")

    def check_comparisons(self) -> None:
        for cid, (i, c) in self.recs["comparison"].items():
            w = self.where_of[i]
            p = self._before("prediction", c.get("prediction_id"), i, w, "comparison.link", "prediction")
            a = self._before("analysis", c.get("analysis_id"), i, w, "comparison.link", "analysis")
            if p is None or a is None:
                continue
            if a.get("status") != "ok":
                self.error("comparison.link", w, f"{cid} compares a {a.get('status')} analysis")
            if (c.get("prediction_id") not in a.get("prediction_ids", [])
                    and not self._revised_after_cause(c.get("prediction_id"), c.get("analysis_id"))):
                self.warn("comparison.link", w, f"{cid}: {a.get('analysis_id')} was not run for {c.get('prediction_id')}, "
                                                f"and no cause of a mismatch on it came before that prediction")
            for f in ("design_version", "load_case_id"):
                if p.get(f) != a.get(f):
                    self.error("comparison.link", w, f"{cid}: prediction and analysis differ in {f}")
            if c.get("quantity") != p.get("quantity"):
                self.error("comparison.values", w, f"{cid} quantity {c.get('quantity')} but prediction {p.get('quantity')}")
            pq, aq = c.get("predicted", {}), c.get("analyzed", {})
            if pq != {"value": p.get("value"), "unit": p.get("unit")}:
                self.error("comparison.values", w, f"{cid} predicted {pq} is not the prediction's value and unit")
            if not any({"value": r.get("value"), "unit": r.get("unit")} == aq for r in a.get("results", [])):
                self.error("comparison.values", w, f"{cid} analyzed {aq} is not one of the analysis results")
            if pq.get("unit") != aq.get("unit"):
                self.error("comparison.units", w, f"{cid} compares {pq.get('unit')} with {aq.get('unit')}")
            self._check_comparison_math(cid, c, w)
            lc = self.load_cases.get(p.get("load_case_id"))
            if lc and c.get("tolerance_rel") != lc.get("tolerance_rel"):
                self.error("comparison.tolerance", w,
                           f"{cid} tolerance {c.get('tolerance_rel')} but the load case says {lc.get('tolerance_rel')}")
            if c.get("cause_id"):
                cause = self.recs["cause"].get(c["cause_id"])
                if cause is None or cause[1].get("comparison_id") != cid:
                    self.error("comparison.cause", w, f"{cid} points to cause {c['cause_id']} that is not its own")

    def _check_comparison_math(self, cid: str, c: dict, w: str) -> None:
        try:
            pv, av = float(c["predicted"]["value"]), float(c["analyzed"]["value"])
        except (KeyError, TypeError, ValueError):
            return
        # contract: both diffs are signed, analyzed - predicted; a match is |diff_rel| <= tolerance_rel
        diff = av - pv
        if not _close(c.get("diff_abs", math.nan), diff):
            self.error("comparison.math", w, f"{cid} diff_abs {c.get('diff_abs')} but {av} - {pv} = {diff}")
        if pv != 0:
            rel = diff / abs(pv)
            if not _close(c.get("diff_rel", math.nan), rel):
                self.error("comparison.math", w, f"{cid} diff_rel {c.get('diff_rel')} but ({av} - {pv}) / |{pv}| = {rel}")
            status = "match" if abs(rel) <= c.get("tolerance_rel", -1) else "mismatch"
            if c.get("status") != status:
                self.error("comparison.math", w, f"{cid} status {c.get('status')} but diff_rel {rel:.6g} "
                                                 f"vs tolerance {c.get('tolerance_rel')} gives {status}")

    def _load_case_of(self, comparison: dict) -> str | None:
        p = self.recs["prediction"].get(comparison.get("prediction_id"))
        return p[1].get("load_case_id") if p else None

    def _revised_after_cause(self, prediction_id: str, analysis_id: str) -> bool:
        """A prediction made after a cause was written for a mismatch on this analysis
        (the agreed way to fix a hand calculation: cause, new formula, compare again)."""
        p = self.recs["prediction"].get(prediction_id)
        if p is None:
            return False
        for i, k in self.recs["cause"].values():
            c = self.recs["comparison"].get(k.get("comparison_id"))
            if c and c[1].get("analysis_id") == analysis_id and c[1].get("status") == "mismatch" and i < p[0]:
                return True
        return False

    def check_causes(self) -> None:
        for kid, (i, k) in self.recs["cause"].items():
            w = self.where_of[i]
            c = self._before("comparison", k.get("comparison_id"), i, w, "cause.link", "comparison")
            if c is not None and c.get("status") != "mismatch":
                self.warn("cause.link", w, f"{kid} explains {k.get('comparison_id')}, which matched")
            rb = k.get("resolved_by")
            if rb:
                r = self.recs["comparison"].get(rb)
                if r is None:
                    self.error("cause.resolved", w, f"{kid} resolved_by {rb}, which was never recorded")
                elif r[1].get("status") != "match":
                    self.error("cause.resolved", w, f"{kid} resolved_by {rb}, which is a {r[1].get('status')}")
                elif c is not None:
                    first = self.recs["comparison"][k["comparison_id"]][0]
                    if r[0] < first:
                        self.error("cause.resolved", w, f"{kid} resolved_by {rb}, recorded before the mismatch")
                    if r[1].get("quantity") != c.get("quantity") or self._load_case_of(r[1]) != self._load_case_of(c):
                        self.error("cause.resolved", w, f"{kid} resolved_by {rb}, which compares another "
                                                        f"quantity or load case than {k.get('comparison_id')}")
            elif k.get("resolved"):
                self.warn("cause.resolved", w, f"{kid} says resolved but names no matching comparison")

    def check_verdicts(self) -> None:
        for vid, (i, v) in self.recs["verdict"].items():
            w = self.where_of[i]
            c = self._before("comparison", v.get("comparison_id"), i, w, "verdict.from_match", "comparison")
            if c is None:
                continue
            crit = (self.load_cases.get(v.get("load_case_id")) or {}).get("criterion")
            if v.get("basis", "match") == "explained_safe_side":
                self._check_safe_side_verdict(vid, v, c, crit, i, w)
            else:
                if c.get("status") != "match":
                    self.error("verdict.from_match", w, f"{vid} made from {v.get('comparison_id')}, a {c.get('status')}")
                if v.get("value_from", "analysis") != "analysis":
                    self.error("verdict.values", w, f"{vid} from a match has value_from {v.get('value_from')!r}, "
                                                    f"not 'analysis'")
                if v.get("value") != c.get("analyzed"):
                    self.error("verdict.values", w, f"{vid} value {v.get('value')} is not the compared analysis value")
            p = self.recs["prediction"].get(c.get("prediction_id"))
            if p:
                for f in ("load_case_id", "test_id", "domain"):
                    if v.get(f) != p[1].get(f):
                        self.error("verdict.values", w, f"{vid} {f} {v.get(f)} but the prediction says {p[1].get(f)}")
            if crit is None:
                self.error("verdict.criterion", w, f"{vid}: load case {v.get('load_case_id')} has no criterion")
            else:
                for f in ("op", "limit", "quantity"):
                    if v.get(f) != crit.get(f):
                        self.error("verdict.criterion", w, f"{vid} {f} {v.get(f)} but the criterion says {crit.get(f)}")
            self._check_verdict_math(vid, v, w)

    def _check_safe_side_verdict(self, vid: str, v: dict, c: dict, crit: dict | None, i: int, w: str) -> None:
        """basis explained_safe_side (contract, record_cause): a mismatch whose own cause,
        recorded before the verdict, says safe_side with a reason; the prediction is the
        unfavourable value under the criterion and the verdict takes it."""
        cid = v.get("comparison_id")
        if c.get("status") != "mismatch":
            self.error("verdict.safe_side", w, f"{vid} is explained_safe_side but {cid} is a {c.get('status')}")
        if not v.get("cause_id"):
            self.error("verdict.safe_side", w, f"{vid} is explained_safe_side but names no cause")
        else:
            k = self._before("cause", v["cause_id"], i, w, "verdict.safe_side", "cause")
            if k is not None:
                if k.get("comparison_id") != cid:
                    self.error("verdict.safe_side", w, f"{vid}: {v['cause_id']} explains {k.get('comparison_id')}, not {cid}")
                if k.get("safe_side") is not True:
                    self.error("verdict.safe_side", w, f"{vid}: {v['cause_id']} does not say safe_side true")
                elif not str(k.get("safe_side_reason") or "").strip():
                    self.error("verdict.safe_side", w, f"{vid}: {v['cause_id']} gives no safe_side_reason")
        if v.get("value_from") != "prediction":
            self.error("verdict.values", w, f"{vid} is explained_safe_side but value_from is {v.get('value_from')!r}, "
                                            f"not 'prediction'")
        if v.get("value") != c.get("predicted"):
            self.error("verdict.values", w, f"{vid} value {v.get('value')} is not the compared prediction value")
        try:
            pv, av = float(c["predicted"]["value"]), float(c["analyzed"]["value"])
        except (KeyError, TypeError, ValueError):
            return
        op = (crit or {}).get("op", v.get("op"))
        # an upper limit (<=, <) needs prediction >= analysis; a lower limit (>=, >) needs prediction <= analysis
        if (op in ("<=", "<") and pv < av) or (op in (">=", ">") and pv > av):
            self.error("verdict.safe_side", w, f"{vid}: criterion {op} but the prediction {pv} is on the favourable "
                                               f"side of the analysis {av}")

    def _check_verdict_math(self, vid: str, v: dict, w: str) -> None:
        try:
            val, lim, op = float(v["value"]["value"]), float(v["limit"]["value"]), v["op"]
        except (KeyError, TypeError, ValueError):
            return
        if v["value"].get("unit") != v["limit"].get("unit"):
            self.error("verdict.units", w, f"{vid} compares {v['value'].get('unit')} with {v['limit'].get('unit')}")
        if v.get("pass") != OPS[op](val, lim):
            self.error("verdict.math", w, f"{vid} pass={v.get('pass')} but {val} {op} {lim} is {OPS[op](val, lim)}")
        if lim != 0:
            room = (lim - val) if op in ("<=", "<") else (val - lim)
            margin = 100.0 * room / abs(lim)
            if not _close(v.get("margin_pct", math.nan), margin):
                self.error("verdict.math", w, f"{vid} margin_pct {v.get('margin_pct')} but {margin}")

    def check_judgements(self) -> None:
        for i, j in self.judgements:
            w = self.where_of[i]
            fails = 0
            for vid in j.get("verdict_ids", []):
                v = self._before("verdict", vid, i, w, "judgement.verdicts", "verdict")
                if v is None:
                    continue
                if v.get("domain") != j.get("domain"):
                    self.error("judgement.verdicts", w, f"{vid} is {v.get('domain')}, judgement is {j.get('domain')}")
                if self.version_of("verdict", v) not in (None, j.get("design_version")):
                    self.error("judgement.verdicts", w, f"{vid} is from another design version")
                fails += not v.get("pass")
            if j.get("red_count") != fails:
                self.error("judgement.state", w, f"red_count {j.get('red_count')} but {fails} listed verdicts fail")
            state = j.get("state")
            if (state == "red") != (fails > 0):
                self.error("judgement.state", w, f"state {state} with {fails} failing verdicts")
            if state == "green" and not j.get("verdict_ids"):
                self.error("judgement.state", w, "green without any verdict")

    def check_h3(self) -> None:
        open_mismatch: dict[str, set[str]] = {}
        for i, e in enumerate(self.events):
            kind, data, w = e.get("kind"), e.get("data") or {}, self.where_of[i]
            if kind == "analysis.started" or (kind == "analysis.finished" and data.get("analysis_id") not in self.started):
                dom = self.domain_of_analysis(data)
                if open_mismatch.get(dom):
                    self.error("HK3_cause_before_continue", w,
                               f"analysis in {dom} while {sorted(open_mismatch[dom])} have no cause")
            elif kind == "comparison.recorded":
                if self.recs["comparison"].get(data.get("comparison_id"), (i,))[0] != i:
                    continue  # an update of an earlier comparison, not a new one
                p = self.recs["prediction"].get(data.get("prediction_id"))
                dom = p[1].get("domain") if p else None
                if open_mismatch.get(dom):
                    self.error("HK3_cause_before_continue", w,
                               f"comparison in {dom} while {sorted(open_mismatch[dom])} have no cause")
                if data.get("status") == "mismatch":
                    open_mismatch.setdefault(dom, set()).add(data.get("comparison_id"))
            elif kind == "cause.recorded":
                for s in open_mismatch.values():
                    s.discard(data.get("comparison_id"))

    def check_hooks(self) -> None:
        for i, e in enumerate(self.events):
            if e.get("kind") in ("hook.denied", "hook.blocked_stop"):
                rule = (e.get("data") or {}).get("rule")
                if e["kind"] == "hook.denied" and rule not in HOOK_RULES:
                    self.error("hook.rule", self.where_of[i], f"unknown rule {rule!r}")
                tool = (e.get("data") or {}).get("tool")
                if tool is not None and not str(tool).startswith(f"mcp__{contract.MCP_SERVER}__"):
                    self.warn("hook.tool", self.where_of[i], f"tool {tool!r} is not one of HIRAKU's MCP tools")

    def _running_at(self, end: int) -> dict[str, int]:
        """Agents started and not stopped before event index `end` (agent id -> start index)."""
        running: dict[str, int] = {}
        for i, e in enumerate(self.events[:end]):
            agent = (e.get("data") or {}).get("agent_id") or e.get("agent")
            if e.get("kind") == "agent.started":
                running.setdefault(agent, i)
            elif e.get("kind") == "agent.stopped":
                running.pop(agent, None)
        return running

    def check_agents(self) -> None:
        fin = next((i for i, e in enumerate(self.events) if e.get("kind") == "run.finished"), None)
        if fin is None:
            return
        running = self._running_at(fin)
        if running:
            self.warn("run.agents", self.where_of[fin], f"finished while {', '.join(sorted(running))} "
                                                        f"had no agent.stopped (their work was cut off)")

    def _unjudged(self) -> list[str] | None:
        """Picked domains with no judgement or a pending one in the last design version."""
        if not self.project or not self.designs:
            return None
        latest = self.latest_judgements(max(self.designs))
        return [p.get("domain") for p in self.project.get("domain_picks", [])
                if (latest.get(p.get("domain")) or {}).get("state", "pending") == "pending"]

    def _check_stop_code(self, code: str | None, status: str | None, w: str) -> None:
        """run.finished's stop_code agrees with the status and with what the records show."""
        if code is None:
            if status in ("stopped", "failed"):
                self.warn("run.stop_code", w, f"{status} without stop_code (the screen cannot say why the run ended)")
            return
        if (code == "done") != (status == "done"):
            self.error("run.stop_code", w, f"stop_code {code} with status {status}")
        if (code == "error") != (status == "failed"):
            self.warn("run.stop_code", w, f"stop_code {code} with status {status} (error goes with failed)")
        unjudged = self._unjudged()
        if code == "not_judged" and unjudged == []:
            self.error("run.stop_code", w, "stop_code not_judged but every picked domain is judged")
        if code == "round_limit" and self.project and self.designs:
            used, limit = max(self.designs) - 1, self.project.get("round_limit")
            if isinstance(limit, int) and used < limit:
                self.error("run.stop_code", w, f"stop_code round_limit after {used} of {limit} design revisions")
        if code == "cost_limit" and self.project:
            costs = [(ev.get("data") or {}).get("cost_usd") for ev in self.events if ev.get("kind") == "cost.updated"]
            limit = self.project.get("cost_limit_usd")
            if costs and isinstance(costs[-1], (int, float)) and isinstance(limit, (int, float)) and costs[-1] < limit:
                self.warn("run.stop_code", w, f"stop_code cost_limit but the last cost is {costs[-1]} of {limit} USD")

    def check_finish(self) -> None:
        fin = [(i, e) for i, e in enumerate(self.events) if e.get("kind") == "run.finished"]
        status = self.project.get("status") if self.project else None
        if not fin:
            if status not in (None, "running"):
                self.warn("run.status", layout.PROJECT_FILE, f"status {status} but no run.finished event")
            return
        i, e = fin[-1]
        w = self.where_of[i]
        fstatus = (e.get("data") or {}).get("status")
        if self.project and fstatus != status:
            self.error("run.status", layout.PROJECT_FILE, f"status {status} but run.finished says {fstatus}")
        if fstatus == "stopped" and self.project and not self.project.get("stop_reason"):
            self.warn("run.status", layout.PROJECT_FILE, "stopped without stop_reason")
        d = e.get("data") or {}
        if self.project and d.get("stop_reason") and self.project.get("stop_reason") not in (None, d["stop_reason"]):
            self.error("run.status", layout.PROJECT_FILE, "stop_reason differs from the one in run.finished")
        self._check_stop_code(d.get("stop_code"), fstatus, w)
        if fstatus != "done" or not self.project:
            return
        if not self.designs:
            self.error("HK4_no_stop_before_judged", w, "done without any design version")
            return
        last = max(self.designs)
        latest = self.latest_judgements(last)
        for pick in self.project.get("domain_picks", []):
            dom = pick.get("domain")
            j = latest.get(dom)
            if j is None or j.get("state") == "pending":
                self.error("HK4_no_stop_before_judged", w, f"done while {dom} is not judged for version {last}")
            elif j.get("state") == "red":
                self.warn("HK4_no_stop_before_judged", w, f"done with {dom} red for version {last} (stopped expected)")

    def check_recompute(self) -> None:
        try:
            from hiraku import solvers
        except ModuleNotFoundError:
            self.warn("recompute", "-", "hiraku.solvers not available; predictions not recomputed")
            return
        if not callable(getattr(solvers, "formula_registry", None)):
            self.error("recompute", "-", "hiraku.solvers has no formula_registry() (protocols.py)")
            return
        reg = solvers.formula_registry()
        for pid, (i, p) in self.recs["prediction"].items():
            w = self.where_of[i]
            try:
                out = reg.evaluate(p["formula_id"], p["inputs"])
            except Exception as ex:  # noqa: BLE001 - any failure means not reproducible
                self.error("recompute", w, f"{pid}: K0 could not evaluate {p.get('formula_id')}: {ex}")
                continue
            if not any(o.get("unit") == p.get("unit") and _close(o.get("value", math.nan), p.get("value"))
                       for o in out.get("outputs", {}).values()):
                self.error("recompute", w, f"{pid} value {p.get('value')} {p.get('unit')} is not an output of "
                                           f"{p.get('formula_id')} for the recorded inputs ({out.get('outputs')})")

    # --- what the hooks did (a reading aid, not a check) -----------------------------
    def _next_record(self, i: int, agent: str | None) -> tuple[int, dict] | None:
        """The first record event after event i, by `agent` (any agent when None)."""
        for j in range(i + 1, len(self.events)):
            e = self.events[j]
            if e.get("kind") in RECORD_EVENTS and (agent is None or e.get("agent") == agent):
                return j, e
        return None

    def effects(self) -> list[str]:
        """Where the hooks acted and what came of it: each denial or blocked stop with the
        record that followed, each mismatch with its cause, each verdict with its basis,
        the last judgement per domain and how the run ended. Call after run()."""
        out = []
        hooks = [(i, e) for i, e in enumerate(self.events) if e.get("kind") in ("hook.denied", "hook.blocked_stop")]
        out.append(f"hooks: {len(hooks)} denial(s) or blocked stop(s)")
        for i, e in hooks:
            d = e.get("data") or {}
            rule = d.get("rule")
            tool = str(d.get("tool") or d.get("hook") or "").removeprefix(f"mcp__{contract.MCP_SERVER}__")
            how = "staged by the script" if d.get("scripted") else "live"
            line = f"  seq={e.get('seq')} {rule} {e.get('agent')} {e.get('domain') or '-'} {tool} ({how})"
            nxt = self._next_record(i, None if e["kind"] == "hook.blocked_stop" else e.get("agent"))
            if nxt is None:
                line += "; nothing recorded after it"
            else:
                kind, rkind = nxt[1]["kind"], RECORD_EVENTS[nxt[1]["kind"]]
                line += f"; then {kind} {(nxt[1].get('data') or {}).get(rkind[1])} (seq={nxt[1].get('seq')})"
                want = FOLLOW_UP.get(rule)
                if want and kind != want:
                    line += f", not the {want} the rule asks for"
            out.append(line)
            if e["kind"] == "hook.blocked_stop" and isinstance(d.get("count"), int) and d.get("count") == d.get("limit"):
                busy = sorted(a for a in self._running_at(i) if a != e.get("agent"))
                out.append(f"    block limit reached ({d['count']} of {d['limit']}): the next stop ends the run"
                           + (f", while {', '.join(busy)} were still working" if busy else ""))
        unusable =sorted({n for n, checks in self.tool_checks.items() if not checks[-1][1].get("usable")})
        if unusable:
            out.append(f"  tools left unusable by their checks (HK2): {', '.join(unusable)}")
        mism = [(cid, c) for cid, (_, c) in self.recs["comparison"].items() if c.get("status") == "mismatch"]
        out.append(f"comparisons: {len(self.recs['comparison'])} ({len(self.recs['comparison']) - len(mism)} match)")
        out.append(f"mismatches: {len(mism)}")
        for cid, c in mism:
            p = (self.recs["prediction"].get(c.get("prediction_id")) or (0, {}))[1]
            out.append(f"  {cid} {p.get('test_id')} {c.get('quantity')}: predicted {_fmt(c.get('predicted'))}, "
                       f"analyzed {_fmt(c.get('analyzed'))}, diff {_num(c.get('diff_rel'), 100, '+.1f')} % "
                       f"(tolerance {_num(c.get('tolerance_rel'), 100, '.0f')} %), formula {p.get('formula_id')}")
            causes = [(kid, k) for kid, (_, k) in self.recs["cause"].items() if k.get("comparison_id") == cid]
            if not causes:
                out.append("    no cause")
            for kid, k in causes:
                out.append(f"    {kid}: {k.get('cause', '')[:80]}")
                if k.get("safe_side"):
                    out.append(f"    safe side: {k.get('safe_side_reason', '')[:80]}")
                if k.get("resolved_by"):
                    out.append(f"    resolved by {k['resolved_by']}")
        out.append(f"verdicts: {len(self.recs['verdict'])}")
        for vid, (_, v) in self.recs["verdict"].items():
            src = f"value from the {v.get('value_from', 'analysis')}" + (f", {v['cause_id']}" if v.get("cause_id") else "")
            out.append(f"  {vid} {v.get('test_id')} {v.get('quantity')} {_fmt(v.get('value'))} {v.get('op')} "
                       f"{_fmt(v.get('limit'))}: {'pass' if v.get('pass') else 'fail'}, margin {_num(v.get('margin_pct'))} %; "
                       f"{v.get('basis', 'match')} ({src}, {v.get('comparison_id')})")
        if self.designs:
            last = max(self.designs)
            for dom, j in sorted(self.latest_judgements(last).items(), key=lambda x: str(x[0])):
                out.append(f"judgement v{last} {dom}: {j.get('state')} ({j.get('summary', '')[:80]})")
        fin = [i for i, e in enumerate(self.events) if e.get("kind") == "run.finished"]
        status = (self.events[fin[-1]].get("data") or {}).get("status") if fin else "not finished"
        reason = (self.project or {}).get("stop_reason")
        out.append(f"finished: {status}" + (f" ({reason})" if reason else ""))
        if fin and self._running_at(fin[-1]):
            out.append(f"  still working at the end: {', '.join(sorted(self._running_at(fin[-1])))}")
        return out

    # --- all ---------------------------------------------------------------------
    def run(self) -> list[Finding]:
        if not self.path.is_dir():
            self.error("file.missing", str(self.path), "not a folder")
            return self.findings
        self.load_project()
        self.load_events()
        self.check_sequence()
        self.index_events()
        self.check_ids()
        self.check_project_vs_start()
        self.check_files()
        self.check_predictions()
        self.check_analyses()
        self.check_tool_specs()
        self.check_assembly()
        self.check_scene()
        self.check_comparisons()
        self.check_causes()
        self.check_verdicts()
        self.check_judgements()
        self.check_h3()
        self.check_hooks()
        self.check_agents()
        self.check_finish()
        if self.recompute:
            self.check_recompute()
        return self.findings


def check_run(run_path: Path | str, recompute: bool = False) -> list[Finding]:
    return RunChecker(Path(run_path), recompute=recompute).run()


def run_effects(run_path: Path | str) -> list[str]:
    """RunChecker.effects() for one run folder."""
    checker = RunChecker(Path(run_path))
    checker.run()
    return checker.effects()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qa.check_run", description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="*", type=Path, help="run folders (runs/<run_id>)")
    ap.add_argument("--all", action="store_true", help="check every folder under runs/")
    ap.add_argument("--strict", action="store_true", help="fail on warnings too")
    ap.add_argument("--recompute", action="store_true", help="recompute predictions with K0")
    ap.add_argument("--effects", action="store_true",
                    help="also list where the hooks acted, the mismatches and causes, and the verdicts")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # causes and summaries are Japanese
    paths = list(args.runs)
    if args.all:
        paths += sorted(p for p in layout.RUNS_DIR.iterdir() if p.is_dir()) if layout.RUNS_DIR.is_dir() else []
    if not paths:
        ap.error("give a run folder or --all")
    failed = False
    for p in paths:
        checker = RunChecker(p, recompute=args.recompute)
        findings = checker.run()
        errors = sum(f.level == "error" for f in findings)
        warns = len(findings) - errors
        print(f"{p}: {errors} error(s), {warns} warning(s)")
        for f in findings:
            print(f"  {f}")
        if args.effects:
            for line in checker.effects():
                print(f"  {line}")
        failed |= errors > 0 or (args.strict and warns > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

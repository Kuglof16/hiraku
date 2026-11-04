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
  same object, and each record event has its file (layout.py; the record kinds
  and their events are layout.RECORD_EVENT)
- ids: unique, numbered 1..n within the run, file name = id, right v### folder
- project: project.json keeps the run.started data, except the fields that may
  change (requirements re-sent any time) and what project.updated re-sends after
  version 1 (load cases, picks, roles); stop.request, the hearing files and inbox/ requests are gone once the
  run finished (warnings); knowledge/ holds only files a knowledge.imported names;
  tools/ holds each attempt at a tool made in the run as <name>-<n>.py with its
  .check.json
- report: report.json is the last report.written; each report covers the picked
  domains with the state and verdicts of their tiles at that time, and its
  differences are mismatches of that version with their recorded diff_rel and
  cause (warning when a mismatch on a judged load case is left out)
- HK1: every analysis names predictions of the same version and load case that
  were recorded before it
- HK2: a tool is used only after a tool.checked that says usable (warning when
  the run has no tool.checked for it at all)
- HK3: after a mismatch, no analysis or comparison in that domain until a cause
- assembly and interferences in an analysis: steps numbered 1..n, each part
  once, dependencies go in earlier, blocked_by only when no_interference is false;
  no part interferes with itself; the tool's interference counts and deepest
  overlap agree with its list
- comparisons: values copied from the prediction and the analysis (the comparison's
  quantity is the analysis result compared, which may differ from the prediction's),
  signed diffs (analyzed - predicted) and status recomputed, tolerance taken from
  the load case
- verdicts: from a matching comparison with the analysis value, or (basis
  explained_safe_side) from a mismatch whose own earlier cause says safe_side with a
  reason, the prediction on the unfavourable side of the criterion and taken as the
  value; pass, margin_pct (null when the limit is 0) and margin (in the limit's
  unit; a warning when the limit is 0 and there is none) recomputed; limit and op
  from the load case criterion
- judgements: red_count and state follow the listed verdicts: red when one fails
  (even with load cases left without a verdict), else pending while a load case
  with a criterion has no verdict or is held back (the latest comparison of the
  judged quantity is a mismatch whose cause is not written, or neither resolved
  nor safe-side; earlier mismatches it replaced do not hold; a tool's newest result
  beyond the criterion, frame_shell judged on max_hot_spot_stress), else green;
  the last tile of each domain and version is still right at the end of the run
  (a later result beyond the criterion voids a safe-side verdict)
- HK5 (light first): an analysis with a tool of stage n starts only when every load
  case with a criterion already checked in that version with a lower-stage tool
  has a passing verdict (stage from the tool's tool.checked spec, 1 when missing)
- HK4: a run that finished as done, or stopped at the round limit (which only ends
  revisions), has no picked domain still pending: every load case with a
  criterion has a verdict in the last version (a red tile alone does not end a
  domain), and no tile is left pending
- HK6 (contract d9631a5, designs with modules only): each design.change_proposed comes from
  designer.<its module> (data.agent_id the same), not for package, and names only that module's
  variables and choices of the version it was made on; its module is named in the modules of a red
  load case of that version (and of each load case it says it answers); from version 2 a version is
  made by the packager, its module_changes are the round's proposals (since the previous version)
  in order and unchanged, plus at most one package change from the packager with package variables
  only; no variable is changed twice in a round, and every variable that moved is in that package
  change or a variable of a module proposed in the round (a proposal's code may set it, as fit: driver does);
  warning (module.envelope) for a part box or member end outside its module's envelope boxes; no
  proposal in a design without modules
- agents: each agent.started has its agent.stopped before run.finished (warning:
  an agent still working when the run ended was cut off)
- run.finished: stop_code done exactly when the status is done (error with
  failed); not_judged only with a picked domain still pending (as for HK4); round_limit only
  when the design revisions reached round_limit; stop_reason as in project.json
- scene in an analysis: a road profile has one height per x along an increasing
  x, sizes are not negative, angle_channel names a series of that analysis; a
  series has as many points on its axis (t_s or x) as values
- rule checks: made after their design version, pass exactly when nothing hits,
  hits and parts not evaluable are among the targets (and not both), the rule came
  in with a knowledge.imported before it (when the run has any); warning for a
  target not in the design ("design" stands for the whole design)
- meshes: one per design version, made after it; element node numbers within the
  nodes, no node twice in an element, one element_member per element and a
  thickness for each; warnings for nodes in no element and members not in the
  design. A field that names a mesh: the mesh was created before the analysis
  started, is of the analysis' version, and per_element / per_mesh_node have one
  value per element / node
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

# event kind -> (record kind in the schema, id field), from the contract's one map
RECORD_EVENTS = {event: (kind, f"{kind}_id") for event, kind in layout.RECORD_OF_EVENT.items()}
# A record is updated by emitting its event again with the same id and the whole
# new record; the last event wins and the file equals it (layout.py). Only these
# fields may change in an update; any other change rewrites a recorded result.
UPDATABLE_FIELDS = {"comparison": {"cause_id"}, "cause": {"resolved", "resolved_by"}}
# project.json fields the contract lets change after run.started.
PROJECT_MUTABLE = {"status", "stop_reason", "requirements", "cost_usd", "design_versions"}
# and what project.updated re-sends once the designer has made version 1 (docs/contract.md)
PROJECT_UPDATABLE = {"domain_picks", "load_cases", "agents"}
OPS = {"<=": operator.le, ">=": operator.ge, "<": operator.lt, ">": operator.gt}
# results the assembly tool reports about its own interference list (the analysis
# dept's quantity names): count and deepest overlap per state. Checked when reported.
INTERFERENCE_COUNTS = {"interference_count": "rest", "motion_interference_count": "motion"}
INTERFERENCE_WORST = {"worst_interference": "rest", "worst_motion_interference": "motion"}
# the record a denied call should be followed by when the agent does what the hook asks
FOLLOW_UP = {"HK1_prediction_first": "prediction.recorded", "HK3_cause_before_continue": "cause.recorded"}
VERSION_DIR_RE = re.compile(r"^v([0-9]{3})$")
# tools/<name>-<n>.py and tools/<name>-<n>.check.json: one attempt at a tool made in the run
TOOL_FILE_RE = re.compile(r"^(\w+)-([0-9]+)(\.py|\.check\.json)$")
# mesh quality metrics measured apart (qa.mesh_quality) and how; a worst value off by more than 1 %
# (0.5 deg for angles) is a warning, since a solver may define them a little differently
MESH_MEASURES = {"aspect_ratio": "longest / shortest edge", "min_angle_deg": "smallest corner angle",
                 "max_angle_deg": "largest corner angle", "warp_deg": "angle between the triangles of a diagonal",
                 "jacobian_ratio": "min / max corner jacobian"}
# the solver's topology counts and the measure each is held against (flipped_normals: only zero or not)
TOPOLOGY_OF = {"free_edges": "free_edges", "coincident_nodes": "coincident_nodes",
               "unconnected_groups": "unconnected_groups", "flipped_normals": "orientation_conflicts",
               "negative_volume": "inverted"}
# inbox/<n>-<kind>.json: one request from the screen to a running run
INBOX_FILE_RE = re.compile(r"^[0-9]+-[\w.]+\.json$")
# a rule_check target (and part_id) standing for the whole design, e.g. a system rule finding a part missing
WHOLE_DESIGN = "design"
PLUGIN_FILES = {getattr(layout, k) for k in ("PLUGIN_HOOKS_FILE", "PLUGIN_OUTBOX_FILE", "PLUGIN_LOCK_FILE",
                                              "PLUGIN_ALIVE_FILE") if hasattr(layout, k)}
UNSUPPORTED = "unsupported_load"  # a load with no path: a failing verdict from an analysis (contract)
BEYOND_SUFFIX = "-beyond"  # a load case past the limit, meant to fail (hiraku.catalog.build.BEYOND_SUFFIX)
# the result a tool is judged on where it is not the criterion's own quantity (docs/contract.md, 判定に使う量)
JUDGED_AS = {("frame_shell", "max_stress"): "max_hot_spot_stress"}
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
        # every appearance of a record (a cause re-sent with resolved true), to read its state at an event
        self.history: dict[str, dict[str, list[tuple[int, dict]]]] = {k: {} for k, _ in RECORD_EVENTS.values()}
        self.designs: dict[int, tuple[int, dict]] = {}
        self.judgements: list[tuple[int, dict]] = []
        self.proposals: list[tuple[int, dict, str | None]] = []  # design.change_proposed: (event, data, agent)
        self.tool_checks: dict[str, list[tuple[int, dict]]] = {}
        self.started: dict[str, int] = {}
        self.project_updates: list[tuple[int, dict]] = []
        self.reports: list[tuple[int, dict]] = []
        self.knowledge: list[tuple[int, dict]] = []

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
                self.history[rkind].setdefault(rid, []).append((i, data))
            elif kind == "design.created":
                v = data.get("design_version")
                if v in self.designs:
                    self.error("ids.duplicate", w, f"design version {v} created twice")
                self.designs[v] = (i, data)
            elif kind == "judgement.updated":
                self.judgements.append((i, data))
            elif kind == "design.change_proposed":
                self.proposals.append((i, data, e.get("agent")))
            elif kind == "tool.checked":
                self.tool_checks.setdefault(data.get("name"), []).append((i, data))
            elif kind == "analysis.started":
                self.started.setdefault(data.get("analysis_id"), i)
            elif kind == "project.updated":
                self.project_updates.append((i, data))
            elif kind == "report.written":
                self.reports.append((i, data))
            elif kind == "knowledge.imported":
                self.knowledge.append((i, data))
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
                    # an analysis takes its id at analysis.started; roles run at once, so they finish out of order
                    at = self.started.get(rid, i) if rkind == "analysis" else i
                    nums.append((at, int(m.group(1))))
            got = sorted(n for _, n in nums)
            if got != list(range(1, len(got) + 1)):
                self.error("ids.numbering", layout.EVENTS_FILE, f"{prefix} ids are {got}, not 1..{len(got)}")
            order = [n for _, n in sorted(nums)]
            if order != sorted(order):
                self.warn("ids.order", layout.EVENTS_FILE, f"{prefix} ids not in event order: {order}")

    def check_project_vs_start(self) -> None:
        if not self.project or not self.events or self.events[0].get("kind") != "run.started":
            return
        prev, prev_name = self.events[0].get("data") or {}, "run.started"
        first = self.designs.get(1)
        for i, data in self.project_updates:
            w = self.where_of[i]
            # contract: requirements changed mid-run are re-sent any time; load cases, picks and roles
            # are re-sent once the designer has made version 1
            before_v1 = first is None or first[0] > i
            for key in sorted(set(prev) | set(data)):
                if key in PROJECT_MUTABLE or prev.get(key) == data.get(key):
                    continue
                if key not in PROJECT_UPDATABLE:
                    self.error("project.changed", w, f"{key} changed in project.updated (only "
                                                     f"{', '.join(sorted(PROJECT_UPDATABLE))} are re-sent)")
                elif before_v1:
                    self.error("project.updated", w, f"{key} re-sent before design version 1 (load cases, picks "
                                                     f"and roles come again only after the first version)")
            prev, prev_name = data, f"project.updated ({w})"
        for key in sorted(set(prev) | set(self.project)):
            if key in PROJECT_MUTABLE:
                continue
            if prev.get(key) != self.project.get(key):
                self.error("project.changed", layout.PROJECT_FILE,
                           f"{key} differs from {prev_name} data (only {', '.join(sorted(PROJECT_MUTABLE))} may change)")
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
            if name in PLUGIN_FILES or name == layout.TOOL_ERRORS_FILE:  # plugin runs; a tool's exceptions (layout)
                continue
            finished = any(e.get("kind") == "run.finished" for e in self.events)
            if name == layout.STOP_REQUEST_FILE:
                if finished:
                    self.warn("files.stop_request", name, "left after run.finished (it is there only while "
                                                          "someone asks the run to stop)")
                continue
            if name in (layout.HEARING_REQUEST_FILE, layout.HEARING_ANSWER_FILE):
                if finished:
                    self.warn("files.request", name, "left after run.finished (the run reads and removes it)")
                continue
            if name == layout.INBOX_DIR and p.is_dir():
                for f in sorted(p.iterdir()):
                    if not INBOX_FILE_RE.match(f.name):
                        self.warn("files.unknown", self.rel(f), "not an inbox request (<n>-<kind>.json)")
                    elif finished:
                        self.warn("files.request", self.rel(f), "left after run.finished: a request the run never read")
                continue
            if name == layout.KNOWLEDGE_DIR and p.is_dir():
                given = {str((k.get("doc") or {}).get("filename")) for _, k in self.knowledge}
                for f in sorted(p.iterdir()):
                    if f.name not in given:
                        self.warn("files.unknown", self.rel(f), "a knowledge file no knowledge.imported names")
                continue
            if name == layout.TOOLS_DIR and p.is_dir():
                self._check_tools_dir(p)
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

    def _check_tools_dir(self, tdir: Path) -> None:
        """Each attempt at a tool made in the run has its code and its check file, numbered 1..n per name."""
        names = {f.name for f in tdir.iterdir()}
        attempts: dict[str, set[int]] = {}
        for f in sorted(tdir.iterdir()):
            m = TOOL_FILE_RE.match(f.name)
            if not m or not f.is_file():
                self.warn("files.unknown", self.rel(f), "not a tool attempt (<name>-<n>.py or .check.json)")
                continue
            attempts.setdefault(m.group(1), set()).add(int(m.group(2)))
            other = f"{m.group(1)}-{m.group(2)}" + (".check.json" if m.group(3) == ".py" else ".py")
            if other not in names:
                self.error("files.missing", self.rel(f), f"no {other} beside it")
        for tool, ns in sorted(attempts.items()):
            if sorted(ns) != list(range(1, len(ns) + 1)):
                self.error("ids.numbering", self.rel(tdir), f"attempts of {tool} are {sorted(ns)}, not 1..{len(ns)}")

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
        if rkind in ("prediction", "analysis", "rule_check", "mesh"):
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

    def check_series(self) -> None:
        """A series has as many points on its axis (t_s, or x.values) as values."""
        for aid, (i, a) in self.recs["analysis"].items():
            for k, s in enumerate(a.get("series") or []):
                if not isinstance(s, dict) or not isinstance(s.get("values"), list):
                    continue
                axis = s.get("t_s") if "t_s" in s else (s.get("x") or {}).get("values")
                if isinstance(axis, list) and len(axis) != len(s["values"]):
                    self.error("analysis.series", self.where_of[i], f"{aid} series[{k}] {s.get('channel')}: "
                                                                   f"{len(s['values'])} values on {len(axis)} axis points")

    def check_rule_checks(self) -> None:
        """A knowledge rule checked on one version's parts: the version was made before it,
        pass exactly when nothing hits, hits and parts it could not evaluate are among its
        targets, and the rule came in with a knowledge.imported before it."""
        for rid, (i, rc) in self.recs["rule_check"].items():
            w = self.where_of[i]
            v = rc.get("design_version")
            if v not in self.designs or self.designs[v][0] > i:
                self.error("rule_check.design", w, f"{rid} is for design version {v}, not created before it")
            hits = [h.get("part_id") for h in rc.get("hits") or [] if isinstance(h, dict)]
            if bool(rc.get("pass")) == bool(hits):
                self.error("rule_check.pass", w, f"{rid} pass={rc.get('pass')} with {len(hits)} hit(s) "
                                                 f"(pass exactly when nothing hits)")
            targets = set(rc.get("targets") or [])
            missing = [m.get("part_id") for m in rc.get("not_evaluable") or [] if isinstance(m, dict)]
            for what, ids in (("hit", hits), ("not evaluable", missing)):
                for p in ids:
                    if p not in targets:
                        self.error("rule_check.targets", w, f"{rid}: {what} part {p} is not among its targets")
            for p in sorted(set(hits) & set(missing)):
                self.error("rule_check.targets", w, f"{rid}: {p} both hits and could not be evaluated")
            ids = self._design_ids(v)
            for p in sorted(targets - {WHOLE_DESIGN}):
                if not self._names_a_thing(p, ids):
                    self.warn("rule_check.targets", w, f"{rid}: target {p} is not in design v{v}")
            if self.knowledge:
                bundles: dict[str, set] = {}
                for j, k in self.knowledge:
                    if j < i:  # per doc_id the last bundle so far
                        bundles[str((k.get("doc") or {}).get("doc_id"))] = {r.get("rule_id") for r in k.get("rules") or []
                                                                           if isinstance(r, dict)}
                if rc.get("rule_id") not in set().union(*bundles.values()):
                    self.error("rule_check.rule", w, f"{rid} checks rule {rc.get('rule_id')}, which no "
                                                     f"knowledge.imported before it brought in")

    def check_meshes(self) -> None:
        """A version's shell mesh (one per version): elements name nodes it has, each element
        has its member, each member its thickness; and the fields that name a mesh give one
        value per element or node of a mesh of their version, created before the analysis ran."""
        per_version: dict[int, str] = {}
        for mid, (i, m) in self.recs["mesh"].items():
            w = self.where_of[i]
            v = m.get("design_version")
            if v not in self.designs or self.designs[v][0] > i:
                self.error("mesh.design", w, f"{mid} is for design version {v}, not created before it")
            if v in per_version:
                self.error("mesh.version", w, f"{mid} is a second mesh of version {v} (after {per_version[v]}; "
                                              f"analyze makes one per version)")
            per_version.setdefault(v, mid)
            nodes, elements = m.get("nodes_mm") or [], m.get("elements") or []
            used = set()
            for k, el in enumerate(elements):
                if not isinstance(el, list):
                    continue
                bad = [n for n in el if not isinstance(n, int) or not 0 <= n < len(nodes)]
                if bad:
                    self.error("mesh.elements", w, f"{mid} element {k} names nodes {bad} of {len(nodes)} (0..{len(nodes) - 1})")
                elif len(set(el)) != len(el):
                    self.error("mesh.elements", w, f"{mid} element {k} uses a node twice: {el}")
                used.update(n for n in el if isinstance(n, int))
            unused = len(nodes) - len(used & set(range(len(nodes))))
            if unused:
                self.warn("mesh.nodes", w, f"{mid}: {unused} of {len(nodes)} nodes belong to no element")
            members = m.get("element_member") or []
            if len(members) != len(elements):
                self.error("mesh.members", w, f"{mid}: {len(members)} element_member for {len(elements)} elements")
            thick = m.get("member_thickness_mm") or {}
            no_t = sorted({str(x) for x in members if x not in thick})
            if no_t:
                self.error("mesh.thickness", w, f"{mid}: no member_thickness_mm for {', '.join(no_t[:5])}")
            ids = self._design_ids(v)
            strangers = sorted({str(x) for x in members if not self._names_a_thing(x, ids)})
            if strangers:
                self.warn("mesh.members", w, f"{mid}: {', '.join(strangers[:5])} not in design v{v}")
            if not any(f.rule == "mesh.elements" and f.where == w for f in self.findings):  # measurable
                self._check_mesh_quality(mid, w, m)
        for aid, (i, a) in self.recs["analysis"].items():
            w, ran = self.where_of[i], self.started.get(aid, i)
            for k, f in enumerate(a.get("fields") or []):
                if not isinstance(f, dict) or "mesh_id" not in f:
                    continue
                tag, mid = f"{aid} fields[{k}] {f.get('kind')}", f.get("mesh_id")
                rec = self.recs["mesh"].get(mid)
                if rec is None:
                    self.error("field.mesh", w, f"{tag} names {mid}, which was never created")
                    continue
                if rec[0] > ran:
                    self.error("field.mesh", w, f"{tag} names {mid}, created after the analysis started ({self.where_of[rec[0]]})")
                m = rec[1]
                if (m.get("quality") or {}).get("verdict") == "fail" and a.get("status") == "ok":
                    self.error("mesh.quality", w, f"{tag}: {aid} is ok on {mid}, whose quality verdict is fail "
                                                  f"(the solver must not solve it)")
                if m.get("design_version") != a.get("design_version"):
                    self.error("field.mesh", w, f"{tag} names {mid} of version {m.get('design_version')} "
                                                f"in an analysis of version {a.get('design_version')}")
                for key, n in (("per_element", len(m.get("elements") or [])), ("per_mesh_node", len(m.get("nodes_mm") or []))):
                    if isinstance(f.get(key), list) and len(f[key]) != n:
                        self.error("field.mesh", w, f"{tag}: {len(f[key])} {key} values for {n} in {mid}")

    def _check_mesh_quality(self, mid: str, w: str, m: dict) -> None:
        """Measure the mesh apart from the solver (qa.mesh_quality); its own quality must hold together
        and not miss what the measures find."""
        from qa import mesh_quality

        mine = mesh_quality.measure(m.get("nodes_mm") or [], m.get("elements") or [], m.get("element_member"))
        topo, n_el = mine["topology"], len(m.get("elements") or [])
        bad = [k for k in range(n_el) if mine["degenerate"][k] or mine["jacobian_ratio"][k] <= 0]
        if bad:
            self.error("mesh.quality", w, f"{mid}: {topo['degenerate']} element(s) with no area or a zero edge and "
                                          f"{topo['inverted']} folded back (jacobian ratio <= 0), e.g. element {bad[0]}")
        if topo["coincident_nodes"]:
            self.warn("mesh.quality", w, f"{mid}: {topo['coincident_nodes']} nodes lie on another node "
                                         f"(within {mesh_quality.TOL_MM} mm) without being merged")
        if topo["orientation_conflicts"]:
            self.warn("mesh.quality", w, f"{mid}: {topo['orientation_conflicts']} edges within one member run the "
                                         f"same way in both elements (an element faces the other side)")
        q = m.get("quality")
        if not isinstance(q, dict):
            return
        verdict = q.get("verdict")
        if bad and verdict == "ok":
            self.error("mesh.quality", w, f"{mid}: quality verdict ok with {len(bad)} collapsed or folded elements")
        for name, values in (q.get("per_element") or {}).items():
            if len(values) != n_el:
                self.error("mesh.quality", w, f"{mid}: quality.per_element.{name} has {len(values)} values for {n_el} elements")
        for name, met in (q.get("metrics") or {}).items():
            if not isinstance(met, dict):
                continue
            low_is_bad = name.startswith("min_") or name == "jacobian_ratio"
            we = met.get("worst_element")
            if we is not None and not 0 <= we < n_el:
                self.error("mesh.quality", w, f"{mid}: {name} worst_element {we} is not an element (0..{n_el - 1})")
            per = (q.get("per_element") or {}).get(name)
            if isinstance(per, list) and len(per) == n_el and per:
                worst = min(per) if low_is_bad else max(per)
                if not _close(worst, met["worst"]):
                    self.error("mesh.quality", w, f"{mid}: {name} worst {met['worst']} but its per_element gives {worst}")
                if isinstance(met.get("limit"), (int, float)) and "over_limit" in met:
                    over = sum(1 for x in per if (x < met["limit"] if low_is_bad else x > met["limit"]))
                    if over != met["over_limit"]:
                        self.error("mesh.quality", w, f"{mid}: {name} over_limit {met['over_limit']} but "
                                                      f"{over} per_element values are past {met['limit']}")
            if met.get("over_limit") and verdict == "ok":
                self.error("mesh.quality", w, f"{mid}: verdict ok with {met['over_limit']} elements past the "
                                              f"{name} limit (warn or fail)")
            if name in MESH_MEASURES and n_el:
                got = float(mine[name].min() if low_is_bad else mine[name].max())
                if not math.isclose(met["worst"], got, rel_tol=0.01, abs_tol=0.5 if name.endswith("_deg") else 1e-6):
                    self.warn("mesh.quality", w, f"{mid}: {name} worst {met['worst']:.6g} but measured apart "
                                                 f"{got:.6g} ({MESH_MEASURES[name]})")
        for name, theirs in (q.get("topology") or {}).items():
            ours = topo.get(TOPOLOGY_OF.get(name, name))
            if ours is not None and (ours != theirs if name != "flipped_normals" else bool(ours) != bool(theirs)):
                self.warn("mesh.quality", w, f"{mid}: topology {name} {theirs} but measured apart "
                                             f"{TOPOLOGY_OF.get(name, name)} {ours}")

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
            # contract: comparison.quantity is the result actually compared (the call's result_quantity),
            # which may differ from the prediction's quantity (f_wheel vs wheel_rotation_frequency); the
            # prediction side is its value, which check_recompute finds among its formula's outputs
            pq, aq = c.get("predicted", {}), c.get("analyzed", {})
            if pq != {"value": p.get("value"), "unit": p.get("unit")}:
                self.error("comparison.values", w, f"{cid} predicted {pq} is not the prediction's value and unit")
            # where (a member or part id) picks the result when the tool reports the quantity in more than one place
            named = [r for r in a.get("results", []) if r.get("quantity") == c.get("quantity")
                     and (c.get("where") is None or r.get("where") == c["where"])]
            if not named:
                at = f" at {c['where']}" if c.get("where") is not None else ""
                self.error("comparison.values", w, f"{cid} quantity {c.get('quantity')}{at} is not a result of "
                                                   f"{a.get('analysis_id')}")
            elif not any({"value": r.get("value"), "unit": r.get("unit")} == aq for r in named):
                self.error("comparison.values", w, f"{cid} analyzed {aq} is not {a.get('analysis_id')}'s "
                                                   f"{c.get('quantity')}")
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
            if v.get("basis") == UNSUPPORTED:
                self._check_unsupported_verdict(vid, v, i, w)
                continue
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

    def _check_unsupported_verdict(self, vid: str, v: dict, i: int, w: str) -> None:
        """basis unsupported_load (contract, 荷重の通り道が無いとき): a failing verdict with no prediction
        or comparison, made from an ok analysis (analysis_id, recorded before it) of the same load case
        whose results carry unsupported_load > 0; quantity unsupported_load, op <=, limit 0 (unit 1),
        value from the analysis, margin_pct null."""
        rule = "verdict.unsupported_load"
        if v.get("comparison_id") is not None:
            self.error(rule, w, f"{vid} is unsupported_load but names comparison {v.get('comparison_id')}")
        a = self._before("analysis", v.get("analysis_id"), i, w, rule, "analysis")
        if a is None:
            return
        if a.get("status") != "ok":
            self.error(rule, w, f"{vid} made from {v.get('analysis_id')}, a {a.get('status')} analysis")
        if a.get("load_case_id") != v.get("load_case_id"):
            self.error(rule, w, f"{vid} is for {v.get('load_case_id')} but {v.get('analysis_id')} "
                                f"analyzed {a.get('load_case_id')}")
        got = [r for r in a.get("results") or [] if r.get("quantity") == UNSUPPORTED]
        value = v.get("value")
        value = value.get("value") if isinstance(value, dict) else value
        if not got or not isinstance(got[0].get("value"), (int, float)) or got[0]["value"] <= 0:
            self.error(rule, w, f"{vid}: {v.get('analysis_id')} has no unsupported_load result above 0")
        elif value != got[0]["value"]:
            self.error(rule, w, f"{vid} value {value} is not the analysis' unsupported_load {got[0]['value']}")
        limit = v.get("limit")
        limit = limit.get("value") if isinstance(limit, dict) else limit
        if (v.get("quantity"), v.get("op"), limit, v.get("pass"), v.get("value_from", "analysis"), v.get("margin_pct")) \
                != (UNSUPPORTED, "<=", 0, False, "analysis", None):
            self.error(rule, w, f"{vid} must be quantity unsupported_load, op <=, limit 0, pass false, "
                                f"value_from analysis, margin_pct null")
        lc = self.load_cases.get(v.get("load_case_id")) or {}
        for f in ("test_id", "domain"):
            if lc and lc.get(f) is not None and v.get(f) != lc.get(f):
                self.error(rule, w, f"{vid} {f} {v.get(f)} but the load case says {lc.get(f)}")

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
        # contract: margin_pct = room as % of the limit (null when the limit is 0); margin = room in
        # the limit's unit; both positive when passing
        room, unit = (lim - val) if op in ("<=", "<") else (val - lim), v["limit"].get("unit")
        if lim != 0:
            pct = 100.0 * room / abs(lim)
            if not _close(v.get("margin_pct") if v.get("margin_pct") is not None else math.nan, pct):
                self.error("verdict.math", w, f"{vid} margin_pct {v.get('margin_pct')} but {pct}")
        elif v.get("margin_pct") is not None:
            self.error("verdict.math", w, f"{vid} margin_pct {v['margin_pct']} with a limit of 0 (null, and the margin in {unit})")
        m = v.get("margin")
        if not isinstance(m, dict):
            if lim == 0:
                self.warn("verdict.margin", w, f"{vid}: the limit is 0 and there is no margin, so nothing says how far it is")
        elif m.get("unit") != unit:
            self.error("verdict.units", w, f"{vid} margin in {m.get('unit')}, the limit in {unit}")
        elif not _close(m.get("value") if isinstance(m.get("value"), (int, float)) else math.nan, room):
            self.error("verdict.math", w, f"{vid} margin {m.get('value')} {unit} but {room} {unit}")

    def _state_at(self, rkind: str, rid: str, i: int) -> dict | None:
        """The record as last sent before event i (None when not sent yet)."""
        seen = [d for j, d in self.history[rkind].get(rid, []) if j < i]
        return seen[-1] if seen else None

    def _held(self, i: int, domain: str, v: int) -> dict[str, list[str]]:
        """Load cases of the domain (with a criterion, without a failing verdict) that may not count as
        passed at event i (docs/contract.md, 分野が緑になる条件): the latest comparison of the judged
        quantity is a mismatch whose cause is not written, or neither resolved nor safe-side (earlier
        mismatches a later comparison of the same load case and quantity replaced do not hold), or a
        tool's newest ok analysis has its judged result beyond the criterion, which also voids a
        safe-side verdict."""
        out: dict[str, list[str]] = {}
        for lc_id, lc in self.load_cases.items():
            crit = lc.get("criterion")
            if lc.get("domain") != domain or not isinstance(crit, dict):
                continue
            verdicts = [r for vid, (j, r) in self.recs["verdict"].items()
                        if j < i and r.get("load_case_id") == lc_id and self.version_of("verdict", r) == v]
            if verdicts and not verdicts[-1].get("pass"):
                continue
            why = []
            # the latest comparison of the judged quantity holds the case when it is a mismatch whose cause
            # is not written, or neither resolved nor safe-side; earlier ones it replaced do not
            latest: dict[str, tuple[str, dict]] = {}
            stand_in = crit.get("stand_in_quantity")
            latest_stand_in = None
            for cid, (j, c) in self.recs["comparison"].items():
                p = self.recs["prediction"].get(c.get("prediction_id"))
                a = self.recs["analysis"].get(c.get("analysis_id"))
                if j > i or p is None or p[1].get("load_case_id") != lc_id or p[1].get("design_version") != v:
                    continue
                judged = JUDGED_AS.get(((a[1] if a else {}).get("tool"), crit.get("quantity")), crit.get("quantity"))
                if c.get("quantity") == judged:
                    latest[judged] = (cid, c)
                elif stand_in and c.get("quantity") == stand_in:
                    latest_stand_in = (cid, c)
            # contract (stand_in_quantity): when the judged quantity's prediction and result are both below
            # 1e-6 of the limit, its latest stand-in comparison decides (b) instead (the verdict stays judged)
            lim = (crit.get("limit") or {}).get("value")
            for q, (cid, c) in list(latest.items()):
                pv, av = (c.get("predicted") or {}).get("value"), (c.get("analyzed") or {}).get("value")
                if (latest_stand_in and isinstance(lim, (int, float)) and isinstance(pv, (int, float))
                        and isinstance(av, (int, float)) and abs(pv) < 1e-6 * abs(lim) and abs(av) < 1e-6 * abs(lim)):
                    latest[q] = latest_stand_in
            for cid, c in latest.values():
                if c.get("status") != "mismatch":
                    continue
                causes = [kid for kid, (k, r) in self.recs["cause"].items() if k < i and r.get("comparison_id") == cid]
                cause = self._state_at("cause", causes[-1], i) if causes else None
                if cause is None or not (cause.get("resolved") or cause.get("safe_side")):
                    out_why = "no cause" if cause is None else f"{causes[-1]} not resolved, not safe-side"
                    why.append(f"latest comparison {cid} of {c.get('quantity')} is a mismatch ({out_why})")
            newest: dict[str, dict] = {}
            for aid, (j, a) in self.recs["analysis"].items():
                if j < i and a.get("load_case_id") == lc_id and a.get("design_version") == v and a.get("status") == "ok":
                    newest[a.get("tool")] = a
            limit = crit.get("limit") or {}
            for tool, a in newest.items():
                q = JUDGED_AS.get((tool, crit.get("quantity")), crit.get("quantity"))
                vals = [r.get("value") for r in a.get("results", [])
                        if r.get("quantity") == q and r.get("unit") == limit.get("unit")
                        and isinstance(r.get("value"), (int, float))]
                op = crit.get("op")
                if vals and op in OPS and isinstance(limit.get("value"), (int, float)):
                    worst = max(vals) if op in ("<=", "<") else min(vals)
                    if not OPS[op](worst, limit["value"]):
                        why.append(f"{a.get('analysis_id')} {q} {worst:.4g} {limit.get('unit')} beyond {op} {limit['value']}")
            if why:
                out[lc_id] = why
        return out

    def check_judgements(self) -> None:
        """A tile is red when a listed verdict fails (even with load cases left without one), else
        pending while a load case with a criterion has no verdict or is held back (_held: an open
        mismatch, or a result beyond the criterion), else green."""
        for i, j in self.judgements:
            w = self.where_of[i]
            fails = 0
            judged = set()
            for vid in j.get("verdict_ids", []):
                v = self._before("verdict", vid, i, w, "judgement.verdicts", "verdict")
                if v is None:
                    continue
                judged.add(v.get("load_case_id"))
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
            missing = [lc_id for lc_id, lc in self.load_cases.items()
                       if lc.get("domain") == j.get("domain") and "criterion" in lc and lc_id not in judged]
            if state == "green" and missing:
                self.error("judgement.state", w, f"green while {', '.join(missing)} have no verdict (pending)")
            held = self._held(i, j.get("domain"), j.get("design_version")) if state in ("green", "pending") else {}
            if state == "green" and held:
                self.error("judgement.state", w, "green while held back (pending): " +
                           "; ".join(f"{lc_id}: {', '.join(why)}" for lc_id, why in held.items()))
            if state == "pending" and not fails and judged and not missing and not held:
                self.error("judgement.state", w, "pending but every load case with a criterion has a passing "
                                                 "verdict and none is held back (green)")
        # a later record can hold back a green tile (a result beyond the criterion voids a safe-side
        # verdict): the last tile of each domain and version must still be right at the end of the run
        last = {(j.get("domain"), j.get("design_version")): (i, j) for i, j in self.judgements}
        for (domain, v), (i, j) in last.items():
            held = self._held(len(self.events), domain, v) if j.get("state") == "green" else {}
            if held:
                self.error("judgement.state", self.where_of[i], f"{domain} v{v} stays green to the end of the run, "
                           "but later records hold it back (pending): " +
                           "; ".join(f"{lc_id}: {', '.join(why)}" for lc_id, why in held.items()))

    def check_report(self) -> None:
        """report.json is the last report.written; what code fills in each report is the records'."""
        p = self.path / layout.REPORT_FILE
        if p.exists():
            rep = self._read_json(p)
            if rep is not None and self._schema("report", rep, layout.REPORT_FILE):
                if not self.reports:
                    self.error("files.no_event", layout.REPORT_FILE, "report.json but no report.written event")
                elif self.reports[-1][1] != rep:
                    self.error("files.event_data", layout.REPORT_FILE,
                               f"differs from the last report.written ({self.where_of[self.reports[-1][0]]})")
        elif self.reports:
            self.error("files.missing", self.where_of[self.reports[-1][0]], "report.written but no report.json")
        for i, rep in self.reports:
            self._check_report(i, rep)

    def _check_report(self, i: int, rep: dict) -> None:
        w, v = self.where_of[i], rep.get("design_version")
        if v not in self.designs or self.designs[v][0] > i:
            self.error("report.version", w, f"report on design version {v}, which was not created before it")
        picked = [p.get("domain") for p in (self.project or {}).get("domain_picks", [])]
        doms = [d.get("domain") for d in rep.get("domains", [])]
        if sorted(doms) != sorted(picked):
            self.error("report.domains", w, f"domains {doms} but the picked domains are {picked}")
        for n in rep.get("next", []):
            # an item from a knowledge rule's hit (rule_id) may name any domain: rules check the design
            # apart from the domains picked
            if n.get("rule_id") and not any(rc.get("rule_id") == n["rule_id"] and rc.get("hits")
                                            for _, rc in self.recs["rule_check"].values()):
                self.warn("report.domains", w, f"next answers rule {n['rule_id']}, which has no rule_check hit in the run")
            if n.get("domain") not in picked and not n.get("rule_id"):
                self.error("report.domains", w, f"next names {n.get('domain')}, not a picked domain")
        tiles = {j.get("domain"): j for k, j in self.judgements if k < i and j.get("design_version") == v}
        for d in rep.get("domains", []):
            dom, tile = d.get("domain"), tiles.get(d.get("domain"))
            if d.get("state") != (tile or {}).get("state", "pending"):
                self.error("report.state", w, f"{dom} {d.get('state')} but its tile is {(tile or {}).get('state', 'pending')}")
            vids = d.get("verdict_ids", [])
            if tile is not None and sorted(vids) != sorted(tile.get("verdict_ids", [])):
                self.error("report.verdicts", w, f"{dom} lists {vids} but its tile {tile.get('verdict_ids', [])}")
            for vid in vids:
                rec = self.recs["verdict"].get(vid)
                if rec is None or rec[0] > i:
                    self.error("report.verdicts", w, f"{vid} was not recorded before the report")
            self._check_report_differences(i, w, v, dom, d.get("differences", []))

    def _check_report_differences(self, i: int, w: str, v: int, dom: str, diffs: list[dict]) -> None:
        listed = set()
        for x in diffs:
            cid = x.get("comparison_id")
            listed.add(cid)
            rec = self.recs["comparison"].get(cid)
            if rec is None or rec[0] > i:
                self.error("report.differences", w, f"{cid} was not recorded before the report")
                continue
            c = rec[1]
            if c.get("status") != "mismatch":
                self.error("report.differences", w, f"{cid} is listed as a difference but is a {c.get('status')}")
            if isinstance(x.get("diff_rel"), (int, float)) and isinstance(c.get("diff_rel"), (int, float)) \
                    and not _close(x["diff_rel"], c["diff_rel"]):
                self.error("report.differences", w, f"{cid} diff_rel {x['diff_rel']} but the comparison says {c['diff_rel']}")
            cause = x.get("cause_id")
            if cause is not None:
                crec = self.recs["cause"].get(cause)
                if crec is None or crec[0] > i or crec[1].get("comparison_id") != cid:
                    self.error("report.differences", w, f"{cid} cause {cause} is not a cause of it recorded before the report")
        for cid, (k, c) in self.recs["comparison"].items():
            p = (self.recs["prediction"].get(c.get("prediction_id")) or (None, {}))[1]
            lc = self.load_cases.get(p.get("load_case_id")) or {}
            if (k < i and c.get("status") == "mismatch" and cid not in listed and lc.get("domain") == dom
                    and "criterion" in lc and self.version_of("comparison", c) == v):
                self.warn("report.differences", w, f"{dom} leaves out the mismatch {cid} ({lc.get('load_case_id')})")

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

    def _stage(self, tool: str | None) -> int:
        """The tool's weight class (tool_spec.stage, 0-3) from its last tool.checked; 1 when missing."""
        checks = self.tool_checks.get(tool or "")
        s = checks[-1][1].get("stage") if checks else None
        return s if isinstance(s, int) and not isinstance(s, bool) else 1

    def check_light_first(self) -> None:
        """HK5 (light first): in a version, an analysis with a tool of stage n starts only when every load
        case with a criterion already checked with a lower-stage tool has a passing verdict. (Lower-stage
        load cases not analysed yet are not seen in the records; the hook knows them from the tests.)
        As the hook counts them (hiraku.tools.rules.lower_stage_left): cases past the limit (load case id
        ending BEYOND_SUFFIX) are meant to fail and do not count; the load case being analyzed counts
        only when its latest verdict is red and came from a tool of a lower stage."""
        for aid, (fin, a) in self.recs["analysis"].items():
            at, v, n = self.started.get(aid, fin), a.get("design_version"), self._stage(a.get("tool"))
            lower: dict[str, tuple[str, str]] = {}
            for bid, (bfin, b) in self.recs["analysis"].items():
                if (b.get("design_version") == v and self.started.get(bid, bfin) < at
                        and self._stage(b.get("tool")) < n):
                    lower.setdefault(b.get("load_case_id"), (bid, b.get("tool")))
            for lc_id, (bid, tool) in lower.items():
                if (not isinstance((self.load_cases.get(lc_id) or {}).get("criterion"), dict)
                        or str(lc_id).endswith(BEYOND_SUFFIX)):
                    continue
                verdicts = [r for j, r in self.recs["verdict"].values()
                            if j < at and r.get("load_case_id") == lc_id and self.version_of("verdict", r) == v]
                state = "not judged" if not verdicts else ("red" if not verdicts[-1].get("pass") else None)
                if lc_id == a.get("load_case_id") and (state != "red" or self._stage(self._verdict_tool(verdicts[-1])) >= n):
                    continue
                if state == "not judged" and self._cannot_judge(lc_id, v, at):
                    continue
                if state:
                    self.error("HK5_light_first", self.where_of[at],
                               f"{aid} ({a.get('tool')}, stage {n}) started while {lc_id}, checked by {bid} "
                               f"({tool}, stage {self._stage(tool)}), is {state}")

    def _cannot_judge(self, lc_id: str, v: int, i: int) -> str | None:
        """Why a load case without a verdict cannot be judged at event i in version v, as the records show
        it (contract, HK5: it does not hold the heavier tools and stays not judged on its tile); else None.
        - no K0 formula predicts it: none of its predictions so far is of its criterion's quantity (the
          analysis was allowed on predictions of other quantities)
        - its mismatch was ended (contract 08cf4d6; a plan's expected_mismatch with end): the latest
          comparison of the judged quantity is a mismatch whose latest cause says ends_case true and is not
          resolved (a cause without it, waiting for a fix, still holds the heavier tools)"""
        crit = (self.load_cases.get(lc_id) or {}).get("criterion") or {}
        q = crit.get("quantity")
        preds = [p for j, p in self.recs["prediction"].values()
                 if j < i and p.get("load_case_id") == lc_id and p.get("design_version") == v]
        if preds and not any(p.get("quantity") == q for p in preds):
            return f"no prediction of {q}"
        latest = None
        for cid, (j, c) in self.recs["comparison"].items():
            p = self.recs["prediction"].get(c.get("prediction_id"))
            a = self.recs["analysis"].get(c.get("analysis_id"))
            if j > i or p is None or p[1].get("load_case_id") != lc_id or p[1].get("design_version") != v:
                continue
            if c.get("quantity") == JUDGED_AS.get(((a[1] if a else {}).get("tool"), q), q):
                latest = (cid, c)
        if latest is None or latest[1].get("status") != "mismatch":
            return None
        causes = [kid for kid, (k, r) in self.recs["cause"].items() if k < i and r.get("comparison_id") == latest[0]]
        cause = self._state_at("cause", causes[-1], i) if causes else None
        if cause is not None and cause.get("ends_case") is True and not cause.get("resolved"):
            return f"{latest[0]} ended with {causes[-1]}"
        return None

    def _verdict_tool(self, ver: dict) -> str | None:
        """The tool of the analysis a verdict came from (through its comparison, or its analysis_id)."""
        aid = ver.get("analysis_id")
        if aid is None and ver.get("comparison_id"):
            aid = (self.recs["comparison"].get(ver["comparison_id"], (None, {}))[1] or {}).get("analysis_id")
        return (self.recs["analysis"].get(aid, (None, {}))[1] or {}).get("tool") if aid else None

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

    @staticmethod
    def _not_looked_at(pick: dict) -> bool:
        """A picked domain this run does not judge: its test_ids are empty (none of its tests has a way to
        build its load cases, and the pick's reason says the run does not look at it)."""
        return "test_ids" in pick and not pick["test_ids"]

    def _unjudged(self) -> dict[str, list[str]] | None:
        """Picked domains still pending as HK4 counts them (hiraku.tools.rules.pending), with
        their load cases that have a criterion and no verdict in the last design version.
        A red tile does not end a domain while another of its load cases has no verdict."""
        if not self.project or not self.designs:
            return None
        last = max(self.designs)
        judged = {v.get("load_case_id") for _, v in self.recs["verdict"].values()
                  if self.version_of("verdict", v) == last}
        out = {}
        for p in self.project.get("domain_picks", []):
            if self._not_looked_at(p):
                continue
            dom = p.get("domain")
            missing = [lc_id for lc_id, lc in self.load_cases.items()
                       if lc.get("domain") == dom and "criterion" in lc and lc_id not in judged]
            if missing:
                out[dom] = missing
        return out

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
        if code == "not_judged" and self._unjudged() == {}:
            self.error("run.stop_code", w, "stop_code not_judged but every load case with a criterion in the "
                                           "picked domains has a verdict")
        if code == "round_limit" and self.project and self.designs:
            used, limit = max(self.designs) - 1, self.project.get("round_limit")
            if isinstance(limit, int) and used < limit:
                self.error("run.stop_code", w, f"stop_code round_limit after {used} of {limit} design revisions")
        if code == "cost_limit" and self.project:
            costs = [(ev.get("data") or {}).get("cost_usd") for ev in self.events if ev.get("kind") == "cost.updated"]
            limit = self.project.get("cost_limit_usd")
            if costs and isinstance(costs[-1], (int, float)) and isinstance(limit, (int, float)) and costs[-1] < limit:
                self.warn("run.stop_code", w, f"stop_code cost_limit but the last cost is {costs[-1]} of {limit} USD")

    def _design_before(self, i: int) -> tuple[int, dict] | None:
        """The latest design version created before event i: (version, design)."""
        made = [(j, v, d) for v, (j, d) in self.designs.items() if j < i and isinstance(v, int)]
        return max(made, key=lambda x: x[0])[1:] if made else None

    def _red_cases(self, v: int, i: int) -> set[str]:
        """Load cases whose latest verdict in version v before event i fails."""
        last: dict[str, dict] = {}
        for j, ver in sorted(self.recs["verdict"].values(), key=lambda x: x[0]):
            if j < i and self.version_of("verdict", ver) == v:
                last[ver.get("load_case_id")] = ver
        return {lc for lc, ver in last.items() if ver.get("pass") is False}

    def check_modules(self) -> None:
        """HK6_module_scope (contract d9631a5): the module designers propose, the packager builds."""
        rule = "HK6_module_scope"
        if not any(d.get("modules") for _, d in self.designs.values()):
            for i, p, _ in self.proposals:
                self.error(rule, self.where_of[i], f"{p.get('change_id')}: a module change for a design without modules")
            return
        seen: set = set()
        for i, p, agent in self.proposals:
            w, cid, m = self.where_of[i], p.get("change_id"), p.get("module_id")
            if cid in seen:
                self.error("ids.duplicate", w, f"{cid} proposed twice")
            seen.add(cid)
            cur = self._design_before(i)
            if cur is None:
                self.error(rule, w, f"{cid} proposed before any design version")
                continue
            v, d = cur
            mods = {x.get("module_id"): x for x in d.get("modules") or []}
            if m == "package":
                self.error(rule, w, f"{cid}: the package is the packager's (build_version input), not a proposal")
            elif m not in mods:
                self.error(rule, w, f"{cid}: v{v} has no module {m}")
            if agent != f"designer.{m}" or p.get("agent_id") != agent:
                self.error(rule, w, f"{cid} for {m} from {agent} (agent_id {p.get('agent_id')}); "
                                    f"only designer.{m} proposes for it")
            mod = mods.get(m) or {}
            for what, allowed in (("changes", "variables"), ("choices", "choices")):
                out = sorted(set(p.get(what) or {}) - set(mod.get(allowed) or []))
                if out:
                    self.error(rule, w, f"{cid} {what} {', '.join(out)}, not among {m}'s {allowed} in v{v}")
            reds = self._red_cases(v, i)
            named = {x for lc in reds for x in (self.load_cases.get(lc) or {}).get("modules") or []}
            for lc in p.get("load_case_ids") or []:
                if lc not in reds:
                    self.error(rule, w, f"{cid} answers {lc}, which is not red in v{v}")
                elif m not in ((self.load_cases.get(lc) or {}).get("modules") or []):
                    self.error(rule, w, f"{cid} from {m} answers {lc}, whose modules do not name {m}")
            if not reds:
                self.error(rule, w, f"{cid} proposed while nothing in v{v} is red")
            elif not named:
                self.warn(rule, w, f"{cid}: no red load case of v{v} names its modules")
            elif m not in named:
                self.error(rule, w, f"{cid} from {m}, which no red load case of v{v} names "
                                    f"({', '.join(sorted(named))})")
        versions = sorted(v for v in self.designs if isinstance(v, int))
        for k, v in enumerate(versions):
            i, d = self.designs[v]
            w = self.where_of[i]
            self._check_envelopes(v, d, w)
            if k == 0:
                if d.get("module_changes"):
                    self.error(rule, w, f"v{v} is the first version but lists module_changes")
                continue
            pi, prev = self.designs[versions[k - 1]]
            if not (prev.get("modules") or d.get("modules")):
                continue
            agent = self.events[i].get("agent")
            if agent != "packager":
                self.error(rule, w, f"v{v} made by {agent}; with modules only the packager's build_version "
                                    f"makes a version")
            mods = {x.get("module_id"): x for x in prev.get("modules") or []}
            pvars = set((mods.get("package") or {}).get("variables") or [])
            rnd = [p for j, p, _ in self.proposals if pi < j < i]
            listed = d.get("module_changes") or []
            pkg = [x for x in listed if x.get("module_id") == "package"]
            listed = [x for x in listed if x.get("module_id") != "package"]
            if len(pkg) > 1:
                self.error(rule, w, f"v{v} lists {len(pkg)} package changes (one at most)")
            for x in pkg:
                if x.get("agent_id") != "packager":
                    self.error(rule, w, f"v{v} package change {x.get('change_id')} by {x.get('agent_id')}, not the packager")
                out = sorted(set(x.get("changes") or {}) - pvars)
                if out or x.get("choices"):
                    self.error(rule, w, f"v{v} package change {x.get('change_id')} sets "
                                        f"{', '.join(out + sorted(x.get('choices') or {}))}, not package variables")
            if [x.get("change_id") for x in listed] != [p.get("change_id") for p in rnd]:
                self.error(rule, w, f"v{v} module_changes {[x.get('change_id') for x in listed]} but the round "
                                    f"proposed {[p.get('change_id') for p in rnd]}")
            else:
                for x, p in zip(listed, rnd):
                    if x != p:
                        self.error(rule, w, f"v{v} lists {x.get('change_id')} unlike its proposal")
            twice: dict[str, list] = {}
            for p in rnd + pkg:
                for var in p.get("changes") or {}:
                    twice.setdefault(var, []).append(p.get("change_id"))
            for var, cids in sorted(twice.items()):
                if len(cids) > 1:
                    self.error(rule, w, f"v{v} built while {', '.join(map(str, cids))} all change {var}")
            allowed = {x for c in pkg for x in c.get("changes") or {}}  # the packager's change, as listed
            for p in rnd:
                allowed |= set((mods.get(p.get("module_id")) or {}).get("variables") or [])
            a, b = prev.get("variables") or {}, d.get("variables") or {}
            moved = sorted(x for x in set(a) | set(b) if a.get(x) != b.get(x))
            bad = [x for x in moved if x not in allowed]
            if bad:
                self.error(rule, w, f"v{v} moved {', '.join(bad)}: neither in the packager's package change nor a "
                                    f"variable of a module proposed in the round")
            for p in rnd + pkg:
                for var, q in (p.get("changes") or {}).items():
                    if b.get(var) != q:
                        self.warn(rule, w, f"v{v} has {var} = {b.get(var)}, not {q} as {p.get('change_id')} set it")

    def _check_envelopes(self, v: int, d: dict, w: str, tol_mm: float = 0.5) -> None:
        """Parts (all 8 corners of the box) and members (both end nodes) inside one of their module's
        envelope boxes, within tol_mm (warning module.envelope; H1-envelope's own margin is the
        analysis dept's)."""
        mods = {x.get("module_id"): x for x in d.get("modules") or []}
        if not mods:
            return
        try:
            import numpy as np
            from hiraku.solvers.k4_geometry.obb import from_box
        except ModuleNotFoundError:
            self.warn("module.envelope", w, "numpy or hiraku.solvers not available; envelopes not checked")
            return

        def inside(points, boxes) -> bool:
            for bx in boxes:
                o = from_box(bx["center_mm"], bx["size_mm"], bx.get("rpy_deg"))
                local = (np.asarray(points, float) - o.center) @ o.axes.T
                if np.all(np.abs(local) <= o.half + tol_mm):
                    return True
            return False

        nodes = {n.get("id"): n.get("xyz_mm") for n in d.get("nodes") or []}
        things = [(f"part {x.get('id')}", x.get("module"),
                   from_box(x["box"]["center_mm"], x["box"]["size_mm"], x["box"].get("rpy_deg")).corners())
                  for x in d.get("parts") or [] if x.get("module") and x.get("box")]
        things += [(f"member {x.get('id')}", x.get("module"), [nodes[x["node_a"]], nodes[x["node_b"]]])
                   for x in d.get("members") or []
                   if x.get("module") and nodes.get(x.get("node_a")) and nodes.get(x.get("node_b"))]
        for name, m, pts in things:
            if m not in mods:
                self.error("HK6_module_scope", w, f"v{v} {name} is in module {m}, which the design does not have")
            elif (mods[m].get("envelope") or []) and not inside(pts, mods[m]["envelope"]):
                self.warn("module.envelope", w, f"v{v} {name} leaves the envelope of {m}")

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
        # HK4: done, or stopped at the round limit (it only ends revisions; the latest version is still judged)
        ending = "done" if fstatus == "done" else "round_limit" if d.get("stop_code") == "round_limit" else None
        if ending is None or not self.project:
            return
        if not self.designs:
            # a run that ends on purpose after the domain picks and the hearing (project.stop_after "picks",
            # copied from the requirements file) makes no design; any other done without a version is wrong
            picks_only = self.project.get("stop_after") == "picks"
            if ending == "done" and not picks_only:
                self.error("HK4_no_stop_before_judged", w, "done without any design version")
            return
        last = max(self.designs)
        latest = self.latest_judgements(last)
        unjudged = self._unjudged() or {}
        # HK4 / HK5's dead end (docs/contract.md): revisions used up with a lower stage red, the heavier stages are
        # not run and the run ends round_limit; the domains waiting behind it may stay unjudged (the stop
        # reason says so)
        behind_red = ending == "round_limit" and any(j.get("state") == "red" for j in latest.values())
        for pick in self.project.get("domain_picks", []):
            dom = pick.get("domain")
            j = latest.get(dom)
            if self._not_looked_at(pick):  # no test of it can be built in this run: not waited for
                if "見ない" not in str(pick.get("reason") or ""):
                    self.warn("HK4_no_stop_before_judged", w, f"{dom} is picked with no test_ids, but its reason "
                                                              f"does not say this run does not look at it")
                continue
            if (dom in unjudged or j is None or j.get("state") == "pending") and behind_red:
                if "低い段の赤" not in str(self.project.get("stop_reason") or ""):
                    self.warn("HK4_no_stop_before_judged", w, f"round_limit with {dom} not judged behind a red, but the "
                                                              f"stop reason does not say a lower stage's red is left")
            elif dom in unjudged:
                self.error("HK4_no_stop_before_judged", w,
                           f"{ending} while {dom} has no verdict for {', '.join(unjudged[dom])} in version {last}")
            elif j is None or j.get("state") == "pending":
                self.error("HK4_no_stop_before_judged", w, f"{ending} while the {dom} tile is not judged for version {last}")
            elif j.get("state") == "red" and ending == "done":
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
            margin = (f"{_num(v['margin_pct'])} %" if v.get("margin_pct") is not None
                      else _fmt(v["margin"]) if isinstance(v.get("margin"), dict) else "?")
            out.append(f"  {vid} {v.get('test_id')} {v.get('quantity')} {_fmt(v.get('value'))} {v.get('op')} "
                       f"{_fmt(v.get('limit'))}: {'pass' if v.get('pass') else 'fail'}, margin {margin}; "
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
        self.check_series()
        self.check_rule_checks()
        self.check_meshes()
        self.check_comparisons()
        self.check_causes()
        self.check_verdicts()
        self.check_judgements()
        self.check_report()
        self.check_h3()
        self.check_light_first()
        self.check_hooks()
        self.check_agents()
        self.check_modules()
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

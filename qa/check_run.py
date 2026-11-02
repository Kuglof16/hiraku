"""Check one run folder (runs/<run_id>/) against the contract and the hook rules.

    python -m qa.check_run runs/<run_id> [runs/<other> ...] [--all] [--strict] [--recompute]

Reads only. Lists every finding as `LEVEL rule where: message` and exits 1 when
there is an error (or a warning with --strict). What it looks at:

- shape: project.json, every line of events.jsonl and every record file fit
  hiraku.schema.json; seq runs 0, 1, 2, ... without gaps; t and ts never go back
- files and events: each record file has exactly one event whose data is the
  same object, and each record event has its file (layout.py)
- ids: unique, numbered 1..n within the run, file name = id, right v### folder
- H1: every analysis names predictions of the same version and load case that
  were recorded before it
- H2: a tool is used only after a tool.checked that says usable (warning when
  the run has no tool.checked for it at all)
- H3: after a mismatch, no analysis or comparison in that domain until a cause
- comparisons: values copied from the prediction and the analysis, diff and
  status recomputed, tolerance taken from the load case
- verdicts: only from a matching comparison; pass and margin recomputed; limit
  and op from the load case criterion
- judgements: red_count and state follow the listed verdicts
- H4: a run that finished as done has no picked domain still pending
- --recompute: each prediction value is reproduced by K0 from its formula and
  inputs (needs hiraku.solvers.k0_handcalc)
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
# Records that may be re-emitted with new content; the file holds the latest.
REWRITABLE = {"cause"}
# project.json fields the contract lets change after run.started.
PROJECT_MUTABLE = {"status", "stop_reason", "requirements", "cost_usd", "design_versions"}
OPS = {"<=": operator.le, ">=": operator.ge, "<": operator.lt, ">": operator.gt}
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
                if rid in self.recs[rkind] and rkind not in REWRITABLE:
                    self.error("ids.duplicate", w, f"{rid} recorded twice")
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
                    self._before("prediction", pid, i, self.where_of[i], "H1_prediction_first", "prediction")
        for aid, (i, a) in self.recs["analysis"].items():
            w = self.where_of[i]
            if aid not in self.started:
                self.warn("analysis.started", w, f"{aid} finished without an analysis.started event")
            elif self.started[aid] > i:
                self.error("analysis.started", w, f"{aid} started after it finished")
            if not a.get("prediction_ids"):
                self.error("H1_prediction_first", w, f"{aid} has no prediction")
            for pid in a.get("prediction_ids", []):
                p = self._before("prediction", pid, self.started.get(aid, i), w, "H1_prediction_first", "prediction")
                if p is None:
                    continue
                for f in ("design_version", "load_case_id"):
                    if p.get(f) != a.get(f):
                        self.error("H1_prediction_first", w, f"{aid} {f} {a.get(f)} but {pid} has {p.get(f)}")
            self._check_tool_usable(a, self.started.get(aid, i), w)
            if a.get("status") == "failed" and not str(a.get("error", "")).strip():
                self.error("analysis.error", w, f"{aid} failed without saying why")

    def _check_tool_usable(self, a: dict, i: int, w: str) -> None:
        name = a.get("tool")
        checks = [(j, s) for j, s in self.tool_checks.get(name, []) if j < i]
        if not checks:
            if name in self.tool_checks:
                self.error("H2_tool_usable", w, f"tool {name} was used before its tool.checked")
            else:
                self.warn("H2_tool_usable", w, f"no tool.checked for {name} in this run")
            return
        if not checks[-1][1].get("usable"):
            self.error("H2_tool_usable", w, f"tool {name} used while its last check says usable=false")

    def check_tool_specs(self) -> None:
        for name, specs in self.tool_checks.items():
            for i, s in specs:
                w = self.where_of[i]
                checks = s.get("checks", [])
                for c in checks:
                    got = c.get("got")
                    ok = (isinstance(got, (int, float)) and math.isfinite(got)
                          and abs(got - c["expected"]) <= c["tolerance_rel"] * abs(c["expected"]))
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
            if c.get("prediction_id") not in a.get("prediction_ids", []):
                self.warn("comparison.link", w, f"{cid}: {a.get('analysis_id')} was not run for {c.get('prediction_id')}")
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
        diff = abs(av - pv)
        if not _close(c.get("diff_abs", math.nan), diff):
            self.error("comparison.math", w, f"{cid} diff_abs {c.get('diff_abs')} but |{av} - {pv}| = {diff}")
        if pv != 0:
            rel = diff / abs(pv)
            if not _close(c.get("diff_rel", math.nan), rel):
                self.error("comparison.math", w, f"{cid} diff_rel {c.get('diff_rel')} but {rel}")
            status = "match" if rel <= c.get("tolerance_rel", -1) else "mismatch"
            if c.get("status") != status:
                self.error("comparison.math", w, f"{cid} status {c.get('status')} but diff_rel {rel:.6g} "
                                                 f"vs tolerance {c.get('tolerance_rel')} gives {status}")

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
            elif k.get("resolved"):
                self.warn("cause.resolved", w, f"{kid} says resolved but names no matching comparison")

    def check_verdicts(self) -> None:
        for vid, (i, v) in self.recs["verdict"].items():
            w = self.where_of[i]
            c = self._before("comparison", v.get("comparison_id"), i, w, "verdict.from_match", "comparison")
            if c is None:
                continue
            if c.get("status") != "match":
                self.error("verdict.from_match", w, f"{vid} made from {v.get('comparison_id')}, a {c.get('status')}")
            if v.get("value") != c.get("analyzed"):
                self.error("verdict.values", w, f"{vid} value {v.get('value')} is not the compared analysis value")
            p = self.recs["prediction"].get(c.get("prediction_id"))
            if p:
                for f in ("load_case_id", "test_id", "domain"):
                    if v.get(f) != p[1].get(f):
                        self.error("verdict.values", w, f"{vid} {f} {v.get(f)} but the prediction says {p[1].get(f)}")
            crit = (self.load_cases.get(v.get("load_case_id")) or {}).get("criterion")
            if crit is None:
                self.error("verdict.criterion", w, f"{vid}: load case {v.get('load_case_id')} has no criterion")
            else:
                for f in ("op", "limit", "quantity"):
                    if v.get(f) != crit.get(f):
                        self.error("verdict.criterion", w, f"{vid} {f} {v.get(f)} but the criterion says {crit.get(f)}")
            self._check_verdict_math(vid, v, w)

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
                    self.error("H3_cause_before_continue", w,
                               f"analysis in {dom} while {sorted(open_mismatch[dom])} have no cause")
            elif kind == "comparison.recorded":
                p = self.recs["prediction"].get(data.get("prediction_id"))
                dom = p[1].get("domain") if p else None
                if open_mismatch.get(dom):
                    self.error("H3_cause_before_continue", w,
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
        if fstatus != "done" or not self.project:
            return
        if not self.designs:
            self.error("H4_no_stop_before_judged", w, "done without any design version")
            return
        last = max(self.designs)
        latest = self.latest_judgements(last)
        for pick in self.project.get("domain_picks", []):
            dom = pick.get("domain")
            j = latest.get(dom)
            if j is None or j.get("state") == "pending":
                self.error("H4_no_stop_before_judged", w, f"done while {dom} is not judged for version {last}")
            elif j.get("state") == "red":
                self.warn("H4_no_stop_before_judged", w, f"done with {dom} red for version {last} (stopped expected)")

    def check_recompute(self) -> None:
        try:
            from hiraku.solvers import k0_handcalc
        except ModuleNotFoundError:
            self.warn("recompute", "-", "hiraku.solvers.k0_handcalc not available; predictions not recomputed")
            return
        reg = k0_handcalc
        if not callable(getattr(reg, "evaluate", None)):
            reg = next((o for o in vars(k0_handcalc).values() if callable(getattr(o, "evaluate", None))), None)
        if reg is None:
            self.error("recompute", "-", "k0_handcalc has no evaluate()")
            return
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
        self.check_comparisons()
        self.check_causes()
        self.check_verdicts()
        self.check_judgements()
        self.check_h3()
        self.check_hooks()
        self.check_finish()
        if self.recompute:
            self.check_recompute()
        return self.findings


def check_run(run_path: Path | str, recompute: bool = False) -> list[Finding]:
    return RunChecker(Path(run_path), recompute=recompute).run()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qa.check_run", description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="*", type=Path, help="run folders (runs/<run_id>)")
    ap.add_argument("--all", action="store_true", help="check every folder under runs/")
    ap.add_argument("--strict", action="store_true", help="fail on warnings too")
    ap.add_argument("--recompute", action="store_true", help="recompute predictions with K0")
    args = ap.parse_args(argv)
    paths = list(args.runs)
    if args.all:
        paths += sorted(p for p in layout.RUNS_DIR.iterdir() if p.is_dir()) if layout.RUNS_DIR.is_dir() else []
    if not paths:
        ap.error("give a run folder or --all")
    failed = False
    for p in paths:
        findings = check_run(p, recompute=args.recompute)
        errors = sum(f.level == "error" for f in findings)
        warns = len(findings) - errors
        print(f"{p}: {errors} error(s), {warns} warning(s)")
        for f in findings:
            print(f"  {f}")
        failed |= errors > 0 or (args.strict and warns > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

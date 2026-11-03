"""What a run's record shows a weaker model may have got wrong: checks that read only the record.

    python -m hiraku review <run folder>

review_run reads events.jsonl, project.json and v<NNN>/ (never the code that made them) and returns one
Finding per check. The checks are meant for comparing model mixes (data/catalog/model_mixes.yaml): run the
same requirements file with two mixes and set the findings side by side. Each check's docstring says what a
weaker model tends to do and how that shows in the record.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from hiraku.catalog import REPO_ROOT, read_data
from hiraku.contract import layout
from hiraku.tools import rules

Record = dict[str, Any]

N12_FILE = REPO_ROOT / "data" / "requirements" / "offroad-4w-n12.yaml"
N12_KEY = "Q41"  # the requirement N12 writes in the wrong unit (offroad-4w-n12.yaml's header)
N12_TEST = "S1"
UNIT_WORDS = (("cm", "mm"), ("単位",))  # a cause names the unit mistake when it has every word of one group
REPEATED_DENIAL = 2  # the same rule denying the same agent in the same domain this often: the reason was not read
# every tile judged (round_limit: judged, a red one left; red_left: judged, a red one left with revisions left)
JUDGED_STOP_CODES = ("done", "round_limit", "red_left")


@dataclass(frozen=True)
class Finding:
    name: str
    ok: bool
    detail: str


class RunRecord:
    """The record of one run folder, read once."""

    def __init__(self, run_dir: Path | str) -> None:
        self.path = Path(run_dir)
        self.project: Record = json.loads((self.path / layout.PROJECT_FILE).read_text(encoding="utf-8"))
        self.events: list[Record] = []
        with (self.path / layout.EVENTS_FILE).open(encoding="utf-8") as f:
            self.events = [json.loads(line) for line in f if line.strip()]
        versions = sorted(self.project.get("design_versions") or [])
        self.versions = versions
        self.last_version = versions[-1] if versions else 0
        self._records: dict[tuple[str, int], list[Record]] = {}

    def of_kind(self, kind: str) -> list[Record]:
        return [e for e in self.events if e["kind"] == kind]

    def records(self, kind: str, version: int | None = None) -> list[Record]:
        """Records of one kind in v<NNN>/, for one version or every version, in id order."""
        out = []
        for v in ([version] if version else self.versions):
            if (kind, v) not in self._records:
                folder = layout.version_dir(self.path, v) / layout.RECORD_DIRS[kind]
                self._records[(kind, v)] = ([json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]
                                            if folder.is_dir() else [])
            out.extend(self._records[(kind, v)])
        return out

    def predictions(self) -> dict[str, Record]:
        return {p["prediction_id"]: p for p in self.records("prediction")}

    def load_case_of(self, comparison: Record) -> str | None:
        p = self.predictions().get(comparison["prediction_id"])
        return p["load_case_id"] if p else None

    def finished(self) -> Record | None:
        done = self.of_kind("run.finished")
        return done[-1] if done else None


# ---------------------------------------------------------------------- checks

def check_hk1_denials(r: RunRecord) -> Finding:
    """HK1 (analyze without a prediction). A weaker model skips the hand calculation and calls analyze
    first; the hook denies it and says to record a prediction. One denial is the hook working (the
    scripted run shows one on purpose); denied again before that agent recorded any prediction, the
    model did not act on the reason (not ok)."""
    return _denials(r, rules.HK1, "HK1 denials (analyze without a prediction)", "prediction.recorded")


def check_hk3_denials(r: RunRecord) -> Finding:
    """HK3 (compare or analyze again before the mismatch's cause is written). A weaker model goes on
    predicting and comparing after a mismatch without saying why the numbers differ; each such call
    is a hook.denied with rule HK3. Denied again in the domain before any cause was recorded there,
    the model is looping instead of explaining (not ok)."""
    return _denials(r, rules.HK3, "HK3 denials (going on before a cause)", "cause.recorded")


def _denials(r: RunRecord, rule: str, name: str, progress: str) -> Finding:
    """Count the rule's denials by agent and domain; a repeat is a denial of the same agent (HK1) or in
    the same domain (HK3) while the previous one is still open (no `progress` event in between)."""
    by_agent = progress == "prediction.recorded"
    count: Counter = Counter()
    open_: set[str] = set()
    repeats: Counter = Counter()
    for e in r.events:
        who = (e.get("agent") or "?") if by_agent else (e.get("domain") or "-")
        if e["kind"] == progress:
            open_.discard(who)
        elif e["kind"] == "hook.denied" and (e.get("data") or {}).get("rule") == rule:
            count[(e.get("agent") or "?", e.get("domain") or "-")] += 1
            if who in open_:
                repeats[who] += 1
            open_.add(who)
    parts = [f"{a} ({d}) {n}" for (a, d), n in sorted(count.items())]
    detail = f"{sum(count.values())} denied" + (": " + ", ".join(parts) if parts else "")
    if repeats:
        what = "a prediction" if by_agent else "a cause"
        detail += f"; denied again before {what} was recorded: " + ", ".join(f"{w} {n}" for w, n in sorted(repeats.items()))
    return Finding(name, not repeats, detail)


def check_hk4_stops(r: RunRecord) -> Finding:
    """HK4 (stopping before every picked domain is judged). A weaker model ends its turn with domains
    left; each blocked stop is a hook.blocked_stop, and when the blocks run out the run finishes with
    stop_code not_judged. ok when the run finished with every tile judged (done, round_limit or red_left)."""
    blocked = r.of_kind("hook.blocked_stop")
    by = Counter(e.get("agent") or "?" for e in blocked)
    fin = r.finished()
    code = (fin.get("data") or {}).get("stop_code") if fin else None
    detail = (f"{len(blocked)} blocked stops" + (" (" + ", ".join(f"{a} {n}" for a, n in sorted(by.items())) + ")" if by else "")
              + f"; stop_code {code if fin else 'none (no run.finished)'}")
    return Finding("HK4 blocked stops and the final stop_code", code in JUDGED_STOP_CODES, detail)


def check_verdicts(r: RunRecord) -> Finding:
    """Every load case with a criterion has a verdict on the last version. A weaker model reports a
    domain as done after one load case, or stops after a match without the verdict the compare tool
    makes; the load case is then missing from v<last>/verdicts/."""
    with_criterion = [lc["load_case_id"] for lc in r.project.get("load_cases") or [] if lc.get("criterion")]
    have = {v["load_case_id"] for v in r.records("verdict", r.last_version)} if r.last_version else set()
    missing = [lc for lc in with_criterion if lc not in have]
    detail = f"{len(with_criterion) - len(missing)}/{len(with_criterion)} load cases with a criterion have a verdict on v{r.last_version:03d}"
    if missing:
        detail += ": no verdict for " + ", ".join(missing)
    return Finding("verdicts on the last version", not missing, detail)


def check_causes(r: RunRecord) -> Finding:
    """Every mismatch has a cause, and the predict-again rounds per load case (mismatches before the
    load case's comparisons ended). A weaker model leaves a mismatch without a cause (the hooks then
    keep the domain closed) or needs many rounds of predicting again to reach a match."""
    causes = {c["comparison_id"] for c in r.records("cause")}
    rounds: dict[str, int] = defaultdict(int)
    no_cause = []
    for c in r.records("comparison"):
        if c["status"] != "mismatch":
            continue
        rounds[f"v{_version_of(r, c):03d} {r.load_case_of(c) or '?'}"] += 1
        if c["comparison_id"] not in causes and not c.get("cause_id"):
            no_cause.append(c["comparison_id"])
    detail = f"{sum(rounds.values())} mismatches, {len(no_cause)} without a cause"
    if no_cause:
        detail += " (" + ", ".join(no_cause) + ")"
    if rounds:
        detail += "; predict-again rounds: " + ", ".join(f"{k} {n}" for k, n in sorted(rounds.items()))
    return Finding("causes of the mismatches", not no_cause, detail)


def _version_of(r: RunRecord, comparison: Record) -> int:
    p = r.predictions().get(comparison["prediction_id"])
    return int(p.get("design_version") or 0) if p else 0


def check_failed_analyses(r: RunRecord) -> Finding:
    """Failed analyses. A weaker model passes the wrong load case or tool, or inputs the tool cannot
    use; the analysis is recorded with status failed and its error."""
    failed = [a for a in r.records("analysis") if a.get("status") != "ok"]
    detail = f"{len(failed)} of {len(r.records('analysis'))} analyses failed"
    if failed:
        detail += ": " + ", ".join(f"{a.get('analysis_id', '?')} ({a.get('load_case_id')}: {str(a.get('error') or '')[:80]})"
                                   for a in failed)
    return Finding("failed analyses", not failed, detail)


def check_uncompared(r: RunRecord) -> Finding:
    """Predictions whose formula output was never compared. A weaker model records hand calculations
    and moves on to the next load case without analysing or comparing them, so the number stays
    unchecked. Counted: a prediction with no comparison that was never passed to analyze, or whose
    analysis has a result of its quantity, unless a later prediction of the same quantity on the same
    version and load case replaced it (predicting again after a mismatch drops the rest of the set).
    Only listed: a prediction whose analysis has no result of that quantity (it cannot be compared),
    and one whose analysis already gave a mismatch with a cause (the cause decides what comes next:
    a safe-side verdict or a new prediction, so the rest of that set is not compared)."""
    comparisons = r.records("comparison")
    compared = {c["prediction_id"] for c in comparisons}
    caused = {c["analysis_id"] for c in comparisons if c["status"] == "mismatch" and c.get("cause_id")}
    preds = r.records("prediction")
    analyses = r.records("analysis")
    left, no_result, after_cause = [], [], []
    for p in preds:
        if p["prediction_id"] in compared:
            continue
        later = any(q["prediction_id"] > p["prediction_id"] and q.get("design_version") == p.get("design_version")
                    and q["load_case_id"] == p["load_case_id"] and q.get("quantity") == p.get("quantity") for q in preds)
        if later:
            continue
        used = [a for a in analyses if p["prediction_id"] in (a.get("prediction_ids") or [])]
        if used and not any(res.get("quantity") == p.get("quantity") for a in used for res in a.get("results") or []):
            no_result.append(p)
        elif any(a.get("analysis_id") in caused for a in used):
            after_cause.append(p)
        else:
            left.append(p)
    detail = f"{len(left)} of {len(preds)} predictions never compared"
    if left:
        detail += ": " + ", ".join(f"{p['prediction_id']} ({p['load_case_id']} {p.get('quantity')})" for p in left)
    if no_result:
        detail += f"; {len(no_result)} with no analysis result of their quantity to compare with"
    if after_cause:
        detail += f"; {len(after_cause)} left after a mismatch with a cause on the same analysis"
    return Finding("predictions never compared", not left, detail)


def is_n12_run(r: RunRecord) -> bool:
    """A run of offroad-4w-n12.yaml: the project's Q41 is that file's (the unit mistake) and an S1 load
    case is in the run. (The record does not name the requirements file.)"""
    want = next((q for q in read_data(N12_FILE)["requirements"] if q["key"] == N12_KEY), None)
    have = next((q for q in r.project.get("requirements") or [] if q.get("key") == N12_KEY), None)
    return (want is not None and have is not None and have.get("value") == want["value"]
            and have.get("unit") == want.get("unit")
            and any(lc.get("test_id") == N12_TEST for lc in r.project.get("load_cases") or []))


def check_n12(r: RunRecord) -> Finding:
    """N12 (a run of offroad-4w-n12.yaml): the S1 cause names the unit mistake (cm and mm, or 単位) and
    the comparison after predicting again matches. A weaker model blames the tool or the tolerance, or
    changes the hand calculation until it agrees without finding the unit."""
    s1 = [c for c in r.records("comparison")
          if (r.predictions().get(c["prediction_id"]) or {}).get("test_id") == N12_TEST]
    first_bad = next((c for c in s1 if c["status"] == "mismatch"), None)
    if first_bad is None:
        return Finding("N12 unit mistake", False, f"no S1 mismatch among {len(s1)} S1 comparisons (the unit mistake was not found)")
    cause = next((c for c in r.records("cause") if c["comparison_id"] == first_bad["comparison_id"]), None)
    text = (cause or {}).get("cause") or ""
    named = any(all(w in text for w in group) for group in UNIT_WORDS)
    after = [c for c in s1 if c["comparison_id"] > first_bad["comparison_id"]]
    matched = bool(after) and after[0]["status"] == "match"
    detail = (f"cause of {first_bad['comparison_id']}: " + ("names the unit mistake" if named else
                                                           ("does not name the unit mistake" if cause else "none"))
              + "; next S1 comparison: " + (f"{after[0]['comparison_id']} {after[0]['status']}" if after else "none"))
    return Finding("N12 unit mistake", named and matched, detail)


def check_cost(r: RunRecord) -> Finding:
    """Cost and turns from the last cost.updated (a Claude run's ResultMessage: cost_usd, and since
    model mixes, num_turns and the tokens per model). A weaker model can cost more than expected by
    taking more turns; informational (always ok)."""
    ups = r.of_kind("cost.updated")
    if not ups:
        return Finding("cost and turns", True, "no cost.updated (a scripted run, or one stopped before Claude reported)")
    d = ups[-1].get("data") or {}
    parts = [f"cost_usd {d.get('cost_usd'):.4f}" if isinstance(d.get("cost_usd"), (int, float)) else "cost_usd ?"]
    parts.append(f"num_turns {d['num_turns']}" if d.get("num_turns") is not None else "num_turns not recorded")
    for model, u in sorted((d.get("model_usage") or {}).items()):
        parts.append(f"{model}: in {u.get('inputTokens')}, out {u.get('outputTokens')}, cache read "
                     f"{u.get('cacheReadInputTokens')}, cache write {u.get('cacheCreationInputTokens')}, "
                     f"{u.get('costUSD')} USD")
    return Finding("cost and turns", True, "; ".join(parts))


def check_models(r: RunRecord) -> Finding:
    """The model and effort per role, from agent.started (a scripted run records model null).
    Informational (always ok): it says which mix the findings above belong to."""
    seen: dict[str, str] = {}
    for e in r.of_kind("agent.started"):
        d = e.get("data") or {}
        agent = e.get("agent") or d.get("agent_id") or "?"
        if "model" not in d:
            seen.setdefault(agent, "not recorded")
        elif d["model"] is None:
            seen.setdefault(agent, "no model (scripted)")
        else:
            seen.setdefault(agent, f"{d['model']} ({d.get('effort')})")
    return Finding("model per role", True, ", ".join(f"{a}: {m}" for a, m in seen.items()) or "no agent.started")


CHECKS: tuple[Callable[[RunRecord], Finding], ...] = (
    check_hk1_denials, check_hk3_denials, check_hk4_stops, check_verdicts, check_causes, check_failed_analyses,
    check_uncompared, check_cost, check_models,
)


def review_run(run_dir: Path | str, *, n12: bool | None = None) -> list[Finding]:
    """Every check on the run's record. n12: whether to check the N12 unit mistake (None: is_n12_run)."""
    r = RunRecord(run_dir)
    out = [check(r) for check in CHECKS]
    if n12 if n12 is not None else is_n12_run(r):
        out.insert(len(out) - 2, check_n12(r))
    return out

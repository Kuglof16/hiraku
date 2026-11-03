"""Compare two run folders record by record and event by event (a guard for refactoring).

    python -m qa.compare_runs RUN_A RUN_B [--rel TOL] [--max N]
    python -m qa.compare_runs --make-baseline REQUIREMENTS [--baseline DIR]
    python -m qa.compare_runs --check REQUIREMENTS [--baseline DIR] [--rel TOL] [--max N]
    python -m qa.compare_runs --twice REQUIREMENTS [--rel TOL] [--max N]

--make-baseline makes a scripted run (python -m hiraku run REQUIREMENTS --scripted,
no API key) with the code as it is now, under runs/baseline/<requirements name>/.
After the change, --check makes a new scripted run under runs/compare/<requirements
name>/ and compares it with the newest baseline of the same requirements. --twice
makes two scripted runs now and compares them (is the scripted run deterministic?).

What is compared: every file of the two run folders (project.json, design.json, each
record file, judgements.json, report.json, tools/), and events.jsonl line by line.
Left out, because they differ from one run to the next by nature:
- times: ts and t of an event; created_at and wall_time_s at any depth (a t inside
  an event's data, such as time in a simulated series, is compared)
- the run: run_id at any depth, and the run id or run folder wherever a text names it
- cost: cost_usd at any depth
Anything else that differs is listed with the file (a record file is named by its
id), the events.jsonl line, and the field path, and the exit code is 1. Without
--rel one byte counts (key order, spacing and number spelling too). With --rel TOL,
numbers (also numbers written in a text) whose relative difference is at most TOL
are counted apart and do not fail the comparison, and neither does a mode shape
(field kind mode_shape) given with the other sign: its sign is arbitrary, the
contract fixes only its largest displacement at 1. Both are counted in the summary.

Exit codes: 0 the same, 1 different, 2 could not compare (no run folder, no baseline).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hiraku.contract import layout
from qa import procs

ROOT = Path(__file__).resolve().parents[1]
MASKED = {"ts", "created_at", "wall_time_s", "run_id", "cost_usd"}
EVENT_MASKED = MASKED | {"t"}  # at the top of an event line only
HIDDEN = "<masked>"
_STRING = r'"(?:[^"\\]|\\.)*"'
_NUMBER = r"-?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
# "key": value of a masked key in a JSON text (the byte check masks a nested t as well:
# its value is compared field by field)
MASKED_VALUE_RE = re.compile(r'"(%s)"(\s*:\s*)(?:%s|%s|null|true|false)'
                             % ("|".join(sorted(EVENT_MASKED)), _STRING, _NUMBER))
NUMBER_RE = re.compile(_NUMBER)


@dataclass
class Report:
    a: Path
    b: Path
    rel: float = 0.0
    max_per_file: int = 20
    lines: list[str] = field(default_factory=list)
    differences: int = 0
    files_compared: int = 0
    files_differing: int = 0
    near: int = 0  # numbers within --rel
    near_worst: float = 0.0
    near_where: str = ""
    flipped: list[str] = field(default_factory=list)  # mode shapes with the other sign (--rel)

    @property
    def same(self) -> bool:
        return self.differences == 0

    def summary(self) -> str:
        out = (f"{self.differences} difference(s) in {self.files_differing} of {self.files_compared} file(s)"
               if self.differences else f"the same: {self.files_compared} file(s)")
        if self.near:
            out += (f"; {self.near} number(s) within --rel {self.rel:g} "
                    f"(largest relative difference {self.near_worst:.2e} at {self.near_where})")
        if self.flipped:
            out += f"; {len(self.flipped)} mode shape(s) with the other sign (first at {self.flipped[0]})"
        return out


class _Side:
    """How one run's own id and folder are written, to be read as <run_id> and <run>."""

    def __init__(self, run: Path):
        full = run.resolve()
        forms = {str(full), full.as_posix(), json.dumps(str(full))[1:-1]}
        self.subs = [(f, "<run>") for f in sorted(forms, key=len, reverse=True)] + [(run.name, "<run_id>")]

    def text(self, s: str) -> str:
        for old, new in self.subs:
            s = s.replace(old, new)
        return s


def _mask(x: Any, side: _Side, top: set[str] | None = None) -> Any:
    """x with the masked keys' values hidden (top: the keys masked at this level only) and texts read."""
    if isinstance(x, dict):
        keys = MASKED | (top or set())
        return {k: HIDDEN if k in keys else _mask(v, side) for k, v in x.items()}
    if isinstance(x, list):
        return [_mask(v, side) for v in x]
    if isinstance(x, str):
        return side.text(x)
    return x


def _masked_bytes(text: str, side: _Side, rel: float) -> str:
    text = MASKED_VALUE_RE.sub(lambda m: f'"{m.group(1)}"{m.group(2)}"{HIDDEN}"', side.text(text))
    if rel > 0:  # numbers, also those in texts, are compared field by field within rel
        text = NUMBER_RE.sub("#", text)
    return text


def _show(x: Any) -> str:
    s = json.dumps(x, ensure_ascii=False)
    return s if len(s) <= 80 else s[:77] + "..."


class _Walk:
    """Field-by-field differences of two JSON values: (path, message)."""

    def __init__(self, report: Report, where: str):
        self.r, self.where = report, where
        self.out: list[tuple[str, str]] = []

    def numbers_near(self, a: float, b: float, path: str) -> bool:
        if self.r.rel <= 0 or not (math.isfinite(a) and math.isfinite(b)):
            return False
        d = abs(a - b) / max(abs(a), abs(b))
        if d > self.r.rel:
            return False
        self.r.near += 1
        if d >= self.r.near_worst:
            self.r.near_worst, self.r.near_where = d, f"{self.where} {path}"
        return True

    def texts_near(self, a: str, b: str, path: str) -> bool:
        """Two texts that differ only in numbers within rel (a value written into a sentence)."""
        if self.r.rel <= 0 or NUMBER_RE.split(a) != NUMBER_RE.split(b):
            return False
        pairs = list(zip(NUMBER_RE.findall(a), NUMBER_RE.findall(b)))
        return all(x == y or self.numbers_near(float(x), float(y), path) for x, y in pairs)

    def flipped(self, a: dict, b: dict, path: str) -> set[str]:
        """The values of a mode shape that B gives with the other sign (within rel).

        The sign of a mode shape is arbitrary: the contract fixes only its largest
        displacement at 1. Read so only with --rel."""
        if self.r.rel <= 0 or a.get("kind") != "mode_shape" or b.get("kind") != "mode_shape":
            return set()
        out = set()
        for k in ("per_node", "per_mesh_node"):
            if k in a and k in b and not _near(a[k], b[k], self.r.rel) and _near(a[k], _negative(b[k]), self.r.rel):
                out.add(k)
                self.r.flipped.append(f"{self.where} {path}.{k}" if path else f"{self.where} {k}")
        return out

    def __call__(self, a: Any, b: Any, path: str = "") -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            flipped = self.flipped(a, b, path)
            for k in list(a) + [k for k in b if k not in a]:
                p = f"{path}.{k}" if path else k
                if k in flipped:
                    continue
                if k not in b:
                    self.out.append((p, f"only in A: {_show(a[k])}"))
                elif k not in a:
                    self.out.append((p, f"only in B: {_show(b[k])}"))
                else:
                    self(a[k], b[k], p)
            if set(a) == set(b) and list(a) != list(b):
                self.out.append((path or "(top)", "the same keys in another order"))
        elif isinstance(a, list) and isinstance(b, list):
            for n, (x, y) in enumerate(zip(a, b)):
                self(x, y, f"{path}[{n}]")
            if len(a) != len(b):
                self.out.append((path or "(top)", f"{len(a)} items in A, {len(b)} in B"))
        elif type(a) is not type(b):
            self.out.append((path or "(top)", f"{_show(a)} -> {_show(b)} (type)"))
        elif isinstance(a, float):
            if not (a == b or (math.isnan(a) and math.isnan(b)) or self.numbers_near(a, b, path)):
                rel = f" (relative {abs(a - b) / max(abs(a), abs(b)):.2e})" if a and b and math.isfinite(a - b) else ""
                self.out.append((path or "(top)", f"{a!r} -> {b!r}{rel}"))
        elif isinstance(a, str):
            if a != b and not self.texts_near(a, b, path):
                self.out.append((path or "(top)", f"{_show(a)} -> {_show(b)}"))
        elif a != b:
            self.out.append((path or "(top)", f"{_show(a)} -> {_show(b)}"))


def _near(a: Any, b: Any, rel: float) -> bool:
    """The same shape, and numbers within rel."""
    if isinstance(a, dict) and isinstance(b, dict):
        return list(a) == list(b) and all(_near(a[k], b[k], rel) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_near(x, y, rel) for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        return a == b or (math.isfinite(a - b) and abs(a - b) <= rel * max(abs(a), abs(b)))
    return a == b


def _negative(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _negative(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_negative(v) for v in x]
    return -x if isinstance(x, (int, float)) and not isinstance(x, bool) else x


def _first_line_differing(a: str, b: str) -> int:
    la, lb = a.splitlines(), b.splitlines()
    for n, (x, y) in enumerate(zip(la, lb), start=1):
        if x != y:
            return n
    return min(len(la), len(lb)) + 1


def _files(run: Path) -> set[str]:
    return {p.relative_to(run).as_posix() for p in run.rglob("*") if p.is_file()}


def _read(p: Path) -> str | bytes:
    raw = p.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw


def _compare_json(r: Report, where: str, ta: str, tb: str, sa: _Side, sb: _Side,
                  top: set[str] | None = None) -> list[str]:
    try:
        ja, jb = json.loads(ta), json.loads(tb)
    except json.JSONDecodeError:
        return _compare_text(r, where, ta, tb, sa, sb)
    walk = _Walk(r, where)
    walk(_mask(ja, sa, top), _mask(jb, sb, top))
    found = [f"DIFF  {where}  {p}: {m}" for p, m in walk.out]
    if not found and _masked_bytes(ta, sa, r.rel) != _masked_bytes(tb, sb, r.rel):
        n = _first_line_differing(_masked_bytes(ta, sa, r.rel), _masked_bytes(tb, sb, r.rel))
        found.append(f"DIFF  {where}  line {n}: the same values written differently (spacing, number spelling or escaping)")
    return found


def _compare_text(r: Report, where: str, ta: str | bytes, tb: str | bytes, sa: _Side, sb: _Side) -> list[str]:
    if isinstance(ta, bytes) or isinstance(tb, bytes):
        return [] if ta == tb else [f"DIFF  {where}: the bytes differ"]
    xa, xb = sa.text(ta), sb.text(tb)
    if xa == xb:
        return []
    n = _first_line_differing(xa, xb)
    la, lb = (xa.splitlines() + [""] * n)[n - 1], (xb.splitlines() + [""] * n)[n - 1]
    if _Walk(r, where).texts_near(xa, xb, f"line {n}"):
        return []
    return [f"DIFF  {where}  line {n}: {_show(la)} -> {_show(lb)}"]


def _compare_events(r: Report, ta: str, tb: str, sa: _Side, sb: _Side) -> list[str]:
    la, lb = ta.splitlines(), tb.splitlines()
    found = []
    for n, (x, y) in enumerate(zip(la, lb), start=1):
        e = _event(x)
        label = f"{layout.EVENTS_FILE}:{n} seq={e.get('seq')} {e.get('kind')}" if e else f"{layout.EVENTS_FILE}:{n}"
        found += _compare_json(r, label, x, y, sa, sb, top=EVENT_MASKED)
    if len(la) != len(lb):
        kinds = [[_event(s).get("kind") for s in lines[min(len(la), len(lb)):][:5]] for lines in (la, lb)]
        found.append(f"DIFF  {layout.EVENTS_FILE}: {len(la)} lines in A, {len(lb)} in B "
                     f"(the next kinds: A {kinds[0]}, B {kinds[1]})")
    return found


def _event(line: str) -> dict:
    try:
        e = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return e if isinstance(e, dict) else {}


def compare(a: Path, b: Path, rel: float = 0.0, max_per_file: int = 20) -> Report:
    """Every file of run folders a and b, and each line of their events.jsonl."""
    r = Report(Path(a), Path(b), rel=rel, max_per_file=max_per_file)
    sa, sb = _Side(r.a), _Side(r.b)
    fa, fb = _files(r.a), _files(r.b)
    for name in sorted(fa | fb):
        r.files_compared += 1
        if name not in fb or name not in fa:
            found = [f"ONLY  {name}: only in {'A' if name in fa else 'B'}"]
        else:
            ta, tb = _read(r.a / name), _read(r.b / name)
            if name == layout.EVENTS_FILE and isinstance(ta, str) and isinstance(tb, str):
                found = _compare_events(r, ta, tb, sa, sb)
            elif name.endswith(".json") and isinstance(ta, str) and isinstance(tb, str):
                found = _compare_json(r, name, ta, tb, sa, sb)
            else:
                found = _compare_text(r, name, ta, tb, sa, sb)
        if found:
            r.files_differing += 1
            r.differences += len(found)
            r.lines += found[:max_per_file]
            if len(found) > max_per_file:
                r.lines.append(f"      ... and {len(found) - max_per_file} more in {name}")
    return r


# --- scripted runs -------------------------------------------------------------------
def scripted_run(requirements: Path, runs: Path, design: Path | None = None, timeout: float | None = None) -> Path:
    """python -m hiraku run REQUIREMENTS --scripted --runs RUNS (without the API key); the new run folder.
    With timeout (s), a run still going then is stopped with everything it started (RuntimeError)."""
    requirements, runs = requirements.resolve(), runs.resolve()
    design = design.resolve() if design else None
    runs.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in runs.iterdir()}
    env = dict(os.environ, PYTHONUTF8="1")
    env.pop("ANTHROPIC_API_KEY", None)
    cmd = [sys.executable, "-m", "hiraku", "run", str(requirements), "--scripted", "--runs", str(runs)]
    if design:
        cmd += ["--design", str(design)]
    try:
        proc = procs.run(cmd, cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"no run within {timeout:g} s (stopped): {requirements.name}\n{(e.stderr or '')[-1500:]}") from None
    new = sorted(p for p in runs.iterdir() if p.is_dir() and p.name not in before)
    if len(new) != 1:
        raise RuntimeError(f"expected one new run folder in {runs}, got {len(new)} (exit {proc.returncode})\n"
                           f"{proc.stdout[-1500:]}\n{proc.stderr[-1500:]}")
    return new[0]


def _made(run: Path) -> Path:
    try:
        status = json.loads((run / layout.PROJECT_FILE).read_text(encoding="utf-8")).get("status")
    except (OSError, json.JSONDecodeError):
        status = "?"
    print(f"scripted run {run} (status {status})")
    return run


def newest_baseline(baseline: Path, requirements: Path) -> Path | None:
    folder = baseline / requirements.stem
    runs = sorted(p for p in folder.iterdir() if p.is_dir()) if folder.is_dir() else []
    return runs[-1] if runs else None  # run ids start with the date and time


def _print(r: Report) -> None:
    print(f"A {r.a}\nB {r.b}")
    for s in r.lines:
        print(s)
    print(r.summary())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qa.compare_runs", description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="*", type=Path, help="two run folders to compare")
    ap.add_argument("--make-baseline", type=Path, metavar="REQUIREMENTS",
                    help="make a scripted run of REQUIREMENTS under the baseline folder")
    ap.add_argument("--check", type=Path, metavar="REQUIREMENTS",
                    help="make a scripted run of REQUIREMENTS and compare it with its newest baseline")
    ap.add_argument("--twice", type=Path, metavar="REQUIREMENTS",
                    help="make two scripted runs of REQUIREMENTS and compare them")
    ap.add_argument("--baseline", type=Path, default=layout.RUNS_DIR / "baseline",
                    help="where baselines are kept (default runs/baseline)")
    ap.add_argument("--out", type=Path, default=layout.RUNS_DIR / "compare",
                    help="where --check and --twice put their runs (default runs/compare)")
    ap.add_argument("--design", type=Path, help="design JSON for the scripted runs (default: the requirements' own)")
    ap.add_argument("--rel", type=float, default=0.0,
                    help="numbers within this relative difference count as the same (default 0: byte for byte)")
    ap.add_argument("--max", type=int, default=20, help="differences listed per file (default 20)")
    args = ap.parse_args(argv)
    modes = [m for m in (args.make_baseline, args.check, args.twice) if m] + ([args.runs] if args.runs else [])
    if len(modes) != 1 or (args.runs and len(args.runs) != 2):
        ap.error("give two run folders, or one of --make-baseline, --check, --twice")
    try:
        if args.make_baseline:
            _made(scripted_run(args.make_baseline, args.baseline / args.make_baseline.stem, args.design))
            return 0
        if args.check:
            base = newest_baseline(args.baseline, args.check)
            if base is None:
                print(f"no baseline under {args.baseline / args.check.stem}; "
                      f"make one first with --make-baseline {args.check}", file=sys.stderr)
                return 2
            a, b = base, _made(scripted_run(args.check, args.out / args.check.stem, args.design))
        elif args.twice:
            out = args.out / f"{args.twice.stem}-twice"
            a = _made(scripted_run(args.twice, out / "a", args.design))
            b = _made(scripted_run(args.twice, out / "b", args.design))
        else:
            a, b = args.runs
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2
    for run in (a, b):
        if not (run / layout.EVENTS_FILE).is_file():
            print(f"{run} is not a run folder (no {layout.EVENTS_FILE})", file=sys.stderr)
            return 2
    r = compare(a, b, rel=args.rel, max_per_file=args.max)
    _print(r)
    return 0 if r.same else 1


if __name__ == "__main__":
    sys.exit(main())

"""Make a tool from code written during a run, check it, and keep it on the run's shelf.

make_tool(code) runs the code's check() in its own process (runner.py, under
guard.py's limits) and returns a Made: the tool_spec (origin "claude"), the
checks with `pass` worked out again from expected, got and tolerance_rel,
usable (at least one check and every one passes), the problems in sentences
for the author, and, when usable, a SandboxedTool that analyze can call like
the built-in tools (its run() also goes to its own process).

RunShelf keeps the tools made in one run and writes each attempt to
<run_dir>/tools/ (hiraku.contract.layout.TOOLS_DIR): <name>-<n>.py (the
code) and <name>-<n>.check.json (the checks, problems and the reason given).
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from hiraku import contract
from hiraku.contract.layout import TOOLS_DIR

from . import guard
from .runner import MARK

Record = dict[str, Any]

RUNNER = Path(__file__).with_name("runner.py")
REPO = Path(__file__).resolve().parents[3]
# time limits of a tool's process (HIRAKU's choice, an assumption: the built-in checks
# take seconds, a tool made in a run should not stall the run)
CHECK_TIMEOUT_S = 60.0
RUN_TIMEOUT_S = 120.0
SECRET_ENV = ("ANTHROPIC_API_KEY",)  # not passed to the tool's process


def _child(code: str, action: str, args: Record | None, timeout_s: float) -> Record:
    """Run the tool's code in its own process; {"ok": False, "error": sentence} on any failure."""
    req = json.dumps({"repo": str(REPO), "code": code, "action": action, "args": args or {}}, ensure_ascii=True)
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    with tempfile.TemporaryDirectory(prefix="hiraku-tool-") as cwd:
        try:
            p = subprocess.run([sys.executable, "-I", str(RUNNER)], input=req.encode("ascii"), capture_output=True,
                               timeout=timeout_s, cwd=cwd, env=env)
        except subprocess.TimeoutExpired:
            what = "check()" if action == "check" else "run()"
            return {"ok": False, "error": f"{what} が時間の上限 {timeout_s:g} s のうちに終わりませんでした"}
    lines = [ln for ln in p.stdout.decode("ascii", "replace").splitlines() if ln.startswith(MARK)]
    if not lines:
        tail = p.stderr.decode("utf-8", "replace")[-800:]
        return {"ok": False, "error": f"道具のプロセスが答えを返さずに終わりました (終了コード {p.returncode}): {tail}"}
    return json.loads(lines[-1][len(MARK):])


def check_passes(c: Record) -> bool:
    """A check passes when |got - expected| <= tolerance_rel |expected| (tolerance_rel itself when expected is 0)."""
    got = c.get("got")
    if not isinstance(got, (int, float)) or isinstance(got, bool) or not math.isfinite(got):
        return False
    scale = abs(c["expected"]) if c["expected"] != 0 else 1.0
    return abs(got - c["expected"]) <= c["tolerance_rel"] * scale


def _miss_text(c: Record) -> str:
    got = c.get("got")
    if not isinstance(got, (int, float)) or isinstance(got, bool) or not math.isfinite(got):
        return f"照合「{c['case']}」が通りません: {c['quantity']} の値が出ていません (got = {got!r})。出典: {c['source']}"
    exp = c["expected"]
    rel = (got - exp) / abs(exp) if exp != 0 else got - exp
    diff = f"差 {rel * 100:+.2f} %、許す幅 {c['tolerance_rel'] * 100:.2f} %" if exp != 0 else \
        f"差 {rel:+.4g} {c['unit']}、許す幅 {c['tolerance_rel']:.4g} {c['unit']}"
    return (f"照合「{c['case']}」が通りません: {c['quantity']} が {got:.6g} {c['unit']} で、答えは {exp:.6g} {c['unit']} "
            f"({diff})。出典: {c['source']}")


@dataclass
class Made:
    """What make_tool found: the spec and checks as the code gave them (pass worked out again),
    whether the tool is usable, the problems in sentences, and the tool when usable."""

    code: str
    name: str | None = None
    spec: Record | None = None
    checks: list[Record] = field(default_factory=list)
    usable: bool = False
    problems: list[str] = field(default_factory=list)
    printed: str = ""
    tool: "SandboxedTool | None" = None
    reason: str | None = None  # why the tool was made (make_tool's reason), kept with the attempt

    def record(self) -> Record:
        """For the check file of the run folder."""
        out = {"name": self.name, "usable": self.usable, "problems": self.problems, "spec": self.spec,
               "printed": self.printed, "made_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if self.reason:
            out["reason"] = self.reason
        return out


def make_tool(code: str, *, taken: tuple[str, ...] = (), timeout_s: float = CHECK_TIMEOUT_S,
              run_timeout_s: float = RUN_TIMEOUT_S) -> Made:
    """Check code written during a run; taken: names already on the shelf."""
    made = Made(code=code)
    made.problems = guard.static_problems(code)
    if made.problems:
        return made
    out = _child(code, "check", None, timeout_s)
    made.printed = out.get("printed", "")
    if not out.get("ok"):
        made.problems.append(out.get("error", "道具が走りませんでした"))
        return made
    spec, checks = out.get("spec"), out.get("checks")
    described = out.get("describe")
    if described is not None and not isinstance(described, dict):
        made.problems.append("describe() は dict を返してください (load case params と predicts。棚の道具と同じ形)")
        described = None
    if not isinstance(spec, dict) or not isinstance(checks, list):
        made.problems.append("spec() は tool_spec の dict を、check() は tool_check の list を返してください")
        return made
    made.name = spec.get("name")
    for c in checks:
        errs = contract.errors("tool_check", c) if isinstance(c, dict) else ["not a dict"]
        if errs:
            made.problems.append(f"check() の 1 件が tool_check の形に合いません: {'; '.join(errs[:3])}")
            continue
        real = check_passes(c)
        if c["pass"] != real:
            made.problems.append(f"照合「{c['case']}」の pass は {c['pass']} と書かれていますが、expected・got・"
                                 f"tolerance_rel から計算すると {real} です。HIRAKU は計算した方を使います")
            c = dict(c, **{"pass": real})
        if not real:
            made.problems.append(_miss_text(c))
        made.checks.append(c)
    if not checks:
        made.problems.append("答えの分かっている例がありません。check() に、閉じた式や教科書の値と比べる例を少なくとも 1 つ書いてください")
    spec = dict(spec, origin="claude", checks=made.checks)
    spec["usable"] = bool(made.checks) and len(made.checks) == len(checks) and all(c["pass"] for c in made.checks)
    errs = contract.errors("tool_spec", spec)
    if errs:
        made.problems.append(f"spec() の形が契約の tool_spec に合いません: {'; '.join(errs[:3])}")
        spec["usable"] = False
    if made.name in taken:
        made.problems.append(f"道具の名前 {made.name!r} は棚にもうあります。別の名前にしてください")
        spec["usable"] = False
    made.spec = spec
    made.usable = spec["usable"]
    if made.usable:
        made.tool = SandboxedTool(code, spec, run_timeout_s, described)
    return made


class SandboxedTool:
    """A tool made during a run (hiraku.contract.protocols.AnalysisTool): spec() and check()
    give what make_tool found; run() runs the code in its own process."""

    def __init__(self, code: str, spec: Record, run_timeout_s: float = RUN_TIMEOUT_S, described: Record | None = None):
        self.code = code
        self._spec = spec
        self.name = spec["name"]
        self.run_timeout_s = run_timeout_s
        self._described = described

    def spec(self) -> Record:
        return json.loads(json.dumps(self._spec))

    def describe(self) -> Record:
        """What the tool's describe() gave when make_tool checked it (its load case params and predicts), with its
        name, title, domain, tests and formulas from the spec; predicts [] when the code has no describe()."""
        out = {k: self._spec.get(k) for k in ("name", "title", "domain", "tests", "formulas") if k in self._spec}
        out.update({"params": [], "predicts": []})
        if self._described:
            out.update(json.loads(json.dumps(self._described)))
            out["name"] = self.name
        return out

    def check(self) -> list[Record]:
        return self.spec()["checks"]

    def run(self, design: Record, load_case: Record, predictions: list[Record], params: dict[str, Any]) -> Record:
        args = {"design": design, "load_case": load_case, "predictions": predictions, "params": params or {}}
        out = _child(self.code, "run", args, self.run_timeout_s)
        rec = out.get("analysis") if out.get("ok") else None
        if not isinstance(rec, dict):
            error = out.get("error") or "run() は analysis の dict を返してください"
            return {"tool": self.name, "base": self._spec.get("base", "K0"),
                    "load_case_id": load_case.get("load_case_id", ""),
                    "prediction_ids": [p["prediction_id"] for p in predictions if "prediction_id" in p],
                    "status": "failed", "error": error, "results": []}
        return rec


class RunShelf:
    """The tools made in one run. add() writes every attempt to <run_dir>/tools/ and keeps the
    usable ones; tools() gives them like hiraku.solvers.builtin_tools()."""

    def __init__(self, run_dir: Path | str | None = None, emit: Callable[[str, Record], Any] | None = None):
        self.dir = None if run_dir is None else Path(run_dir) / TOOLS_DIR
        # emit("tool.checked", spec) for every attempt whose code ran far enough to give a spec,
        # usable or not (the screen shows made -> failed its checks -> fixed -> passed)
        self.emit = emit
        self._tools: dict[str, SandboxedTool] = {}
        self._attempts: dict[str, int] = {}

    def make(self, code: str, *, taken: tuple[str, ...] = (), reason: str | None = None, **kwargs) -> Made:
        """make_tool, then add() it. reason: why the tool is made (which test, and why the
        shelf's tools do not do), kept in the attempt's check file."""
        made = make_tool(code, taken=tuple(taken) + tuple(self._tools), **kwargs)
        made.reason = reason
        self.add(made)
        return made

    def add(self, made: Made) -> Path | None:
        """Write the attempt; keep the tool when usable. Returns the code file written."""
        name = made.name if made.name and made.name.replace("_", "").isalnum() else "unnamed"
        n = self._attempts.get(name, 0) + 1
        self._attempts[name] = n
        path = None
        if self.dir is not None:
            self.dir.mkdir(parents=True, exist_ok=True)
            path = self.dir / f"{name}-{n}.py"
            path.write_text(made.code, encoding="utf-8")
            (self.dir / f"{name}-{n}.check.json").write_text(
                json.dumps(made.record(), ensure_ascii=False, indent=1), encoding="utf-8")
        if made.usable and made.tool is not None:
            self._tools[made.tool.name] = made.tool
        if self.emit is not None and made.spec is not None:
            self.emit("tool.checked", made.spec)
        return path

    def tools(self) -> list[SandboxedTool]:
        return list(self._tools.values())

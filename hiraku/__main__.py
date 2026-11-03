"""Command line: python -m hiraku run <requirements.yaml> [--scripted] | check <run> | mcp | hook <Event>.

mcp and hook are the Claude Code plugin's (plugins/hiraku, hiraku.agent.plugin)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from hiraku import contract
from hiraku.contract import layout


def _cmd_run(a: argparse.Namespace) -> int:
    from hiraku.agent.scripted import SCRIPTED_SLUG_SUFFIX, run_scripted
    from hiraku.agent.session import STOPPED_BY_USER, start_run
    from hiraku.catalog import load_requirements

    from hiraku.catalog.mixes import UnknownMix, get_mix

    try:
        get_mix(a.mix)  # before a run folder is made
    except UnknownMix as e:
        print(e, file=sys.stderr)
        return 2
    if not a.scripted:
        from hiraku.agent.runner import MissingApiKey, require_api_key

        try:
            require_api_key()  # before a run folder is made
        except MissingApiKey as e:
            print(e, file=sys.stderr)
            return 2

    from hiraku.catalog import CatalogError
    from hiraku.tools import SolversMissing

    try:
        req = load_requirements(a.requirements)
        slug = req["product"] + (SCRIPTED_SLUG_SUFFIX if a.scripted else "")
        done = json.loads(a.intake_events.read_text(encoding="utf-8")) if a.intake_events else None
        ctx = start_run(a.requirements, design_path=a.design, root=a.runs, slug=slug,
                        intake=not a.no_intake, intake_events=done,
                        driver="scripted" if a.scripted else "agent_sdk")
    except (FileNotFoundError, SolversMissing, CatalogError, ValueError) as e:  # json errors are ValueErrors
        print(f"cannot start the run: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # start_run has marked the folder stopped, if it was made
        print(f"{STOPPED_BY_USER} while the run was starting", file=sys.stderr)
        return 130
    print(f"run folder: {ctx.store.path}")
    try:
        if a.scripted:
            run_scripted(ctx)
        else:
            from hiraku.agent.runner import run_with_claude

            asyncio.run(run_with_claude(ctx, model=a.model, effort=a.effort, mix=a.mix, max_budget_usd=a.budget,
                                        max_turns=a.max_turns))
    except KeyboardInterrupt:
        if ctx.store.project["status"] == "running":  # stopped between the run's own handlers
            ctx.store.finish("stopped", stop_reason=STOPPED_BY_USER, stop_code="user_stop")
        _print_end(ctx, scripted=a.scripted)
        return 130
    _print_end(ctx, scripted=a.scripted)
    return 0 if ctx.store.project["status"] == "done" else 1


def _print_end(ctx, *, scripted: bool) -> None:
    """run_id, status, cost and the judgement tiles."""
    p = ctx.store.project
    print(f"run_id: {p['run_id']}")
    print(f"status: {p['status']}" + (f" ({p['stop_reason']})" if p.get("stop_reason") else ""))
    if scripted:
        print("cost_usd: 0 (scripted: Claude is not called)")
    elif any(e["kind"] == "cost.updated" for e in ctx.store.events()):
        print(f"cost_usd: {p['cost_usd']:.4f}")
    else:
        print("cost_usd: not reported (Claude reports the cost when it finishes; this run stopped before that)")
    for j in ctx.store.judgements():
        print(f"  {contract.DOMAIN_NAMES_JA[j['domain']]}: {j['state']}  {j['summary']}")


def _cmd_check(a: argparse.Namespace) -> int:
    """Check a run folder: every event fits the contract, seq has no gaps, t never goes back."""
    path = Path(a.run)
    lines = (path / layout.EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    bad = 0
    prev_t = 0.0
    for i, line in enumerate(lines):
        e = json.loads(line)
        errs = contract.errors("event", e)
        if e.get("seq") != i:
            errs.append(f"seq {e.get('seq')} where {i} was expected")
        if e.get("t", 0) < prev_t:
            errs.append(f"t {e.get('t')} goes back from {prev_t}")
        prev_t = e.get("t", prev_t)
        for err in errs:
            bad += 1
            print(f"line {i + 1}: {err}")
    print(f"{len(lines)} events, {bad} problems")
    return 1 if bad else 0


def _cmd_mcp(a: argparse.Namespace) -> int:
    """The plugin's MCP server over stdio (hiraku.agent.plugin). Started by the person's Claude Code."""
    import os

    from hiraku.agent.plugin import default_runs, serve
    from hiraku.catalog import REPO_ROOT

    runs = (a.runs or default_runs()).resolve()
    os.chdir(REPO_ROOT)  # Claude Code starts it in the person's project folder; HIRAKU's data is here
    return serve(runs)


def _cmd_hook(a: argparse.Namespace) -> int:
    """One hook call of the plugin (hooks/hooks.json): input JSON on stdin, the answer on stdout."""
    from hiraku.agent.plugin import hook_main

    return hook_main(a.event, a.runs.resolve() if a.runs else None)


def _cmd_review(a: argparse.Namespace) -> int:
    """Print the record checks of hiraku.agent.review; exit 1 when one is not ok."""
    from hiraku.agent.review import review_run

    if not (Path(a.run) / layout.EVENTS_FILE).is_file():
        print(f"no {layout.EVENTS_FILE} in {a.run}", file=sys.stderr)
        return 2
    findings = review_run(a.run)
    for f in findings:
        print(f"[{'ok' if f.ok else 'NG'}] {f.name}: {f.detail}")
    bad = sum(not f.ok for f in findings)
    print(f"{len(findings)} checks, {bad} not ok")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hiraku")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run HIRAKU on a requirements file")
    run.add_argument("requirements", type=Path)
    run.add_argument("--scripted", action="store_true",
                     help="no Claude: call the same tools in a fixed order (data/catalog/scripted.yaml)")
    run.add_argument("--design", type=Path, help="design JSON (default: design_file of the requirements)")
    run.add_argument("--runs", type=Path, default=layout.RUNS_DIR, help="folder for run folders (default: runs)")
    run.add_argument("--mix", default="default",
                     help="model and effort per role: a name in data/catalog/model_mixes.yaml (default: default)")
    run.add_argument("--model", help="the lead's model instead of the mix's")
    run.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                     help="the lead's effort instead of the mix's")
    run.add_argument("--budget", type=float, help="cost limit in USD (default: cost_limit_usd of the requirements)")
    run.add_argument("--max-turns", type=int, default=200)
    run.add_argument("--no-intake", action="store_true",
                     help="leave out the intake events at the start (domains picked, hearing, built-in knowledge)")
    run.add_argument("--intake-events", type=Path,
                     help="JSON list of the intake steps' events already done (the pipeline's, for a requirements file "
                          "it wrote): emitted as they are instead of running the intake again")
    run.set_defaults(fn=_cmd_run)

    check = sub.add_parser("check", help="check a run folder's events.jsonl against the contract")
    check.add_argument("run", type=Path)
    check.set_defaults(fn=_cmd_check)

    review = sub.add_parser("review", help="what the run's record shows a weaker model may have got wrong")
    review.add_argument("run", type=Path)
    review.set_defaults(fn=_cmd_review)

    mcp = sub.add_parser("mcp", help="the Claude Code plugin's MCP server over stdio (plugins/hiraku)")
    mcp.add_argument("--runs", type=Path, help="folder for run folders (default: runs/ of this repository)")
    mcp.set_defaults(fn=_cmd_mcp)

    hook = sub.add_parser("hook", help="one hook call of the Claude Code plugin (input JSON on stdin)")
    hook.add_argument("event", choices=["PreToolUse", "PostToolUse", "Stop", "SubagentStart", "SubagentStop"])
    hook.add_argument("--runs", type=Path, help="folder for run folders (default: runs/ of this repository)")
    hook.set_defaults(fn=_cmd_hook)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    # a piped stdout on Windows defaults to the locale code page (cp932 here), which cannot
    # write the summaries' "≤"; write UTF-8 so pipes and files get every character. stdin too:
    # Claude Code sends the plugin's hook commands UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    sys.exit(main())

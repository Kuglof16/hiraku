"""Where the time of a run goes (measurement only; nothing here is used by the tools).

    python -m hiraku.solvers.timing tools RUN_DIR            # per tool: wall_time_s of the records, and the checks
    python -m hiraku.solvers.timing shell DESIGN_JSON [--size 10] [--step 100]
                                                             # frame_shell's steps on one design (R1-like step)
    python -m hiraku.solvers.timing profile RUN_DIR TOOL [--top 25]
                                                             # cProfile of one tool on the run's design and the
                                                             # load case of its first analysis
    python -m hiraku.solvers.timing rerun RUN_DIR TOOL      # run TOOL again on the run's analyses of it and
                                                             # say whether the records' fields come out the same

RUN_DIR is a run folder (runs/<run id>). The times are those of this PC at the time it is
measured, with whatever else runs on it.
"""

from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import time
from collections import defaultdict
from pathlib import Path


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def _events(run: Path) -> list[dict]:
    return [json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines() if line]


def tool_times(run: Path) -> dict:
    """{"tools": {tool: [wall_time_s, ...]}, "checks": {tool: s}, "total_s": run time, "other_s": ...}."""
    tools: dict[str, list[float]] = defaultdict(list)
    for f in sorted(run.glob("v*/analyses/*.json")):
        rec = _load(f)
        tools[rec["tool"]].append(float(rec.get("wall_time_s", 0.0)))
    ev = _events(run)
    checks, prev = {}, None
    for e in ev:
        if e["kind"] == "tool.checked" and e.get("agent") == "system":
            name = (e.get("data") or {}).get("name", "?")
            checks[name] = e["t"] - (prev if prev is not None else e["t"])
            prev = e["t"]
        elif prev is None and e["kind"] == "design.created":
            prev = e["t"]
    gaps = defaultdict(float)  # time between an event and the next, by the next event's kind
    for a, b in zip(ev, ev[1:]):
        gaps[b["kind"]] += b["t"] - a["t"]
    total = ev[-1]["t"] if ev else 0.0
    return {"tools": dict(tools), "checks": checks, "total_s": total, "gaps": dict(gaps)}


def print_tools(run: Path) -> None:
    tt = tool_times(run)
    print(f"run {run.name}: {tt['total_s']:.1f} s")
    print("| tool | runs | wall_time_s total | each |")
    print("|---|---|---|---|")
    rows = sorted(tt["tools"].items(), key=lambda kv: -sum(kv[1]))
    for name, ts in rows:
        print(f"| {name} | {len(ts)} | {sum(ts):.1f} | {', '.join(f'{t:.1f}' for t in ts)} |")
    print(f"| (all tools) | {sum(len(t) for t in tt['tools'].values())} | "
          f"{sum(sum(t) for t in tt['tools'].values()):.1f} | |")
    print("\n| check (known answers, tool.checked) | s |\n|---|---|")
    for name, s in sorted(tt["checks"].items(), key=lambda kv: -kv[1]):
        print(f"| {name} | {s:.1f} |")
    print(f"| (all checks) | {sum(tt['checks'].values()):.1f} |")
    print("\n| time before an event of kind | s |\n|---|---|")
    for k, s in sorted(tt["gaps"].items(), key=lambda kv: -kv[1])[:12]:
        print(f"| {k} | {s:.1f} |")


def shell_steps(design: dict, size: float = 10.0, step: float | None = 100.0) -> list[tuple[str, float]]:
    """frame_shell's steps on one design, timed one by one (front supports on a step of `step` mm)."""
    import numpy as np  # noqa: F401  (numpy import time is not counted below)

    from .k1_shell.convergence import zz_error
    from .k1_shell.frame import ShellFrame, analyze_shell
    from .k1_shell.mesh import build_mesh
    from .shelf.frame_shell import convergence_sizes
    from .shelf.frame_static import front_supports

    out = []
    sup = front_supports(design) if step is not None else None
    t = time.perf_counter()
    build_mesh(design, size)
    out.append((f"build_mesh {size:g} mm (as frame_mesh)", time.perf_counter() - t))
    for s in convergence_sizes(size):
        t = time.perf_counter()
        fr = ShellFrame.build(design, s)
        out.append((f"{s:g} mm: mesh + model ({fr.mesh.n_elements} elements)", time.perf_counter() - t))
        if fr.mesh.n_elements > 100_000:
            out.append((f"{s:g} mm: left out (> 100000 elements)", 0.0))
            continue
        t = time.perf_counter()
        res = analyze_shell(design, step_mm=step, step_supports=sup, frame=fr, element_size_mm=s)
        tm = res["timings"]
        out.append((f"{s:g} mm: analyze_shell (free DOF {fr.model.timings.get('n_dof_free')})",
                    time.perf_counter() - t))
        for k in ("element_matrices_s", "assemble_s", "reduce_s", "factor_s", "solve_s"):
            if k in tm:
                out.append((f"    {k}", tm[k]))
        t = time.perf_counter()
        zz_error(res["frame"].model, res["stress"])
        out.append((f"{s:g} mm: zz_error", time.perf_counter() - t))
    return out


def profile_tool(run: Path, tool_name: str, top: int = 25) -> str:
    from . import builtin_tools

    tools = {t.name: t for t in builtin_tools()}
    tool = tools[tool_name]
    first = next(_load(f) for f in sorted(run.glob("v*/analyses/*.json")) if _load(f)["tool"] == tool_name)
    v = f"v{first['design_version']:03d}"
    design = _load(run / v / "design.json")
    lcs = {lc["load_case_id"]: lc for lc in _load(run / "project.json").get("load_cases", [])}
    lc = lcs.get(first["load_case_id"], {"load_case_id": first["load_case_id"], "params": {}})
    pr = cProfile.Profile()
    t = time.perf_counter()
    pr.enable()
    tool.analyse(design, tool.declared(lc), {})
    pr.disable()
    wall = time.perf_counter() - t
    s = io.StringIO()
    pstats.Stats(pr, stream=s).sort_stats("cumulative").print_stats(top)
    s2 = io.StringIO()
    pstats.Stats(pr, stream=s2).sort_stats("tottime").print_stats(top)
    return f"{tool_name} on {first['load_case_id']}: {wall:.1f} s\n" + s.getvalue() + s2.getvalue()


COMPARED = ("results", "fields", "vectors", "assembly", "interferences", "mesh_convergence", "assumptions", "scene")


def rerun(run: Path, tool_name: str, compare: tuple[str, ...] = COMPARED) -> list[str]:
    """Run a tool again on every analysis of it in the run (the version's design and mesh, the load
    case; no upstream analyses) and compare the fields `compare` with the record. One line per analysis."""
    from . import builtin_tools

    tool = {t.name: t for t in builtin_tools()}[tool_name]
    lcs = {lc["load_case_id"]: lc for lc in _load(run / "project.json").get("load_cases", [])}
    lines = []
    by_id = {}
    for f in sorted(run.glob("v*/analyses/*.json")):
        rec = _load(f)
        by_id[rec.get("analysis_id", f.stem)] = rec
    for f in sorted(run.glob("v*/analyses/*.json")):
        rec = _load(f)
        if rec["tool"] != tool_name:
            continue
        v = run / f"v{rec['design_version']:03d}"
        params = {}
        if rec.get("inputs_from"):
            params["upstream_analyses"] = {by_id[a]["load_case_id"]: by_id[a] for a in rec["inputs_from"]}
        meshes = sorted(v.glob("meshes/*.json"))
        if getattr(tool, "needs_mesh", False) and meshes:
            params["mesh"] = _load(meshes[-1])
        t = time.perf_counter()
        out = tool.analyse(_load(v / "design.json"), tool.declared(lcs[rec["load_case_id"]]), params)
        dt = time.perf_counter() - t
        out = json.loads(json.dumps(out))  # as written to the record (tuples as lists)
        bad = [k for k in compare if json.dumps(out.get(k), sort_keys=True) != json.dumps(rec.get(k), sort_keys=True)]
        lines.append(f"{f.parent.parent.name}/{f.name} {rec['load_case_id']}: {dt:.1f} s (record {rec['wall_time_s']:.1f} s) "
                     + ("same" if not bad else "DIFFERENT in " + ", ".join(bad)))
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hiraku.solvers.timing", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("tools")
    a.add_argument("run", type=Path)
    b = sub.add_parser("shell")
    b.add_argument("design", type=Path)
    b.add_argument("--size", type=float, default=10.0)
    b.add_argument("--step", type=float, default=100.0)
    c = sub.add_parser("profile")
    c.add_argument("run", type=Path)
    c.add_argument("tool")
    c.add_argument("--top", type=int, default=25)
    r = sub.add_parser("rerun")
    r.add_argument("run", type=Path)
    r.add_argument("tool")
    args = ap.parse_args(argv)
    if args.cmd == "tools":
        print_tools(args.run)
    elif args.cmd == "rerun":
        for line in rerun(args.run, args.tool):
            print(line, flush=True)
    elif args.cmd == "shell":
        for name, s in shell_steps(_load(args.design), args.size, args.step):
            print(f"{s:8.2f} s  {name}")
    else:
        print(profile_tool(args.run, args.tool, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

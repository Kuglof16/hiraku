"""Copy a run folder into showcase/<run_id>/ for the screen to replay, smaller.

    python showcase/prepare.py <run folder> [<run folder> ...] [--points 160] [--check]

The screen (hiraku.webserve) reads a saved run's events.jsonl, project.json, judgements.json,
report.json and tools/ only; the record folders v00N/ are not copied (every record is also in
its event). Each analysis' time series (data.series) is thinned to at most --points samples per
channel, keeping the first, the last and each channel's smallest and largest value, so a curve
keeps its ends and its peaks; nothing else in an event changes (results, predictions,
comparisons and verdicts stay exactly as recorded). --check only prints the sizes.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COPIED = ("project.json", "judgements.json", "report.json")  # beside events.jsonl; tools/ as a folder
POINTS = 160


def thin(series: list[dict], points: int) -> list[dict]:
    """Each channel at most `points` samples: an even spread of indices plus the first, last, smallest and
    largest (t_s and values thinned alike)."""
    out = []
    for s in series:
        vals, ts = s.get("values") or [], s.get("t_s")
        n = len(vals)
        if n <= points or not isinstance(ts, list) or len(ts) != n:
            out.append(s)
            continue
        keep = {round(i * (n - 1) / (points - 1)) for i in range(points)}
        nums = [i for i, v in enumerate(vals) if isinstance(v, (int, float))]
        if nums:
            keep |= {min(nums, key=lambda i: vals[i]), max(nums, key=lambda i: vals[i])}
        idx = sorted(keep)
        out.append({**s, "t_s": [ts[i] for i in idx], "values": [vals[i] for i in idx]})
    return out


def trimmed_events(path: Path, points: int) -> tuple[bytes, int]:
    """The events file with each analysis' series thinned, as bytes, and the number of events."""
    lines, n = [], 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        e = json.loads(raw)
        n += 1
        d = e.get("data")
        if e.get("kind") == "analysis.finished" and isinstance(d, dict) and d.get("series"):
            e["data"] = {**d, "series": thin(d["series"], points)}
            raw = json.dumps(e, ensure_ascii=False)
        lines.append(raw)
    return ("\n".join(lines) + "\n").encode("utf-8"), n


def prepare(run: Path, points: int, check: bool) -> tuple[int, int]:
    """(bytes, gzip bytes) of the run as saved."""
    events, n = trimmed_events(run / "events.jsonl", points)
    size, packed = len(events), len(gzip.compress(events, 6))
    files = [run / f for f in COPIED if (run / f).exists()]
    tools = sorted((run / "tools").glob("*")) if (run / "tools").is_dir() else []
    for f in files + tools:
        b = f.read_bytes()
        size, packed = size + len(b), packed + len(gzip.compress(b, 6))
    print(f"{run.name}: {n} events, {(run / 'events.jsonl').stat().st_size / 1e6:.1f} MB -> {size / 1e6:.1f} MB "
          f"(gzip {packed / 1e6:.1f} MB), tools {len(tools)}")
    if not check:
        out = HERE / run.name
        if out.exists():
            shutil.rmtree(out)
        (out / "tools").mkdir(parents=True) if tools else out.mkdir(parents=True)
        (out / "events.jsonl").write_bytes(events)
        for f in files:
            shutil.copy2(f, out / f.name)
        for f in tools:
            shutil.copy2(f, out / "tools" / f.name)
    return size, packed


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("runs", nargs="+", type=Path)
    p.add_argument("--points", type=int, default=POINTS)
    p.add_argument("--check", action="store_true")
    args = p.parse_args(argv)
    total = packed = 0
    for run in args.runs:
        s, g = prepare(run, args.points, args.check)
        total, packed = total + s, packed + g
    print(f"total {total / 1e6:.1f} MB (gzip {packed / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

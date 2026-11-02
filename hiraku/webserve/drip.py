"""Write an existing events.jsonl into a new run folder at the pace of its `t`.

For trying the live view without running the agents:

    python -m hiraku.webserve.drip hiraku/contract/examples/events.sample.jsonl --runs runs [--speed 2]

The copy gets a new run id (now + slug) and new `ts` values so the screen
follows it as a run that is being written. Nothing else is changed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from hiraku.contract.layout import EVENTS_FILE, PROJECT_FILE, new_run_id


def drip(src: Path, runs_dir: Path, speed: float = 1.0, slug: str | None = None, sleep=time.sleep, now=None) -> Path:
    lines = [json.loads(s) for s in Path(src).read_text(encoding="utf-8").splitlines() if s.strip()]
    if not lines:
        raise ValueError(f"{src} has no events")
    start = now or datetime.now().astimezone()
    first = lines[0]
    if slug is None:
        slug = (first.get("data") or {}).get("product") or "drip"
    run_id = new_run_id(start, f"{slug}-live")
    run_path = Path(runs_dir) / run_id
    run_path.mkdir(parents=True, exist_ok=False)
    out = run_path / EVENTS_FILE
    t_prev = 0.0
    with out.open("w", encoding="utf-8", newline="\n") as f:
        for ev in lines:
            t = float(ev.get("t", 0.0))
            wait = max(0.0, (t - t_prev) / speed)
            t_prev = t
            if wait:
                sleep(wait)
            ev = dict(ev)
            ev["run_id"] = run_id
            ev["ts"] = (start + timedelta(seconds=t / speed)).isoformat(timespec="milliseconds")
            ev["t"] = t / speed
            if ev.get("kind") == "run.started" and isinstance(ev.get("data"), dict):
                ev["data"] = {**ev["data"], "run_id": run_id, "created_at": ev["ts"]}
                (run_path / PROJECT_FILE).write_text(json.dumps(ev["data"], ensure_ascii=False, indent=2), encoding="utf-8")
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            f.flush()
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m hiraku.webserve.drip", description=__doc__.splitlines()[0])
    p.add_argument("src", help="an events.jsonl to copy")
    p.add_argument("--runs", default="runs", help="folder of runs (default ./runs)")
    p.add_argument("--speed", type=float, default=1.0, help="2 = twice as fast")
    p.add_argument("--slug", default=None, help="name part of the new run id")
    args = p.parse_args(argv)
    out = drip(Path(args.src), Path(args.runs), args.speed, args.slug, sleep=lambda s: (print(".", end="", flush=True), time.sleep(s)))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""A rough cost of a real Claude run, from the tool calls its scripted run made.

The scripted run calls the same tools in the same order a Claude run would (hiraku.agent.scripted), so the
calls that write a record (a prediction, an analysis, a comparison, a cause) are counted there and priced
at the cost per such call of the one real Claude run measured so far:

    runs/20261103-013120-offroad-4w (2026-11-03): 0.7644 USD (project.json cost_usd) for 5 such calls
    (2 prediction.recorded, 1 analysis.started, 2 comparison.recorded), with the lead and two roles started.

An estimate, not a measurement (HIRAKU's assumption): the context, and so the tokens per call, grows over a
long run; Claude reads (get_context, list_tools) and writes more or fewer calls than the script; one run is
a thin base. Re-measure after the first long real run and change the constants here.

    python -m hiraku.catalog.cost <scripted run folder> [...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CALIBRATION_RUN = "20261103-013120-offroad-4w"
CALIBRATION_USD = 0.7644  # its project.json cost_usd, rounded to 0.1 cent
CALIBRATION_CALLS = 5
COUNTED = {"prediction.recorded": "prediction_id", "analysis.started": "analysis_id",
           "comparison.recorded": "comparison_id", "cause.recorded": "cause_id"}


def record_calls(run_dir: Path | str) -> int:
    """The calls of a run that wrote a record: distinct ids of the counted kinds (a record emitted again
    when it is updated counts once)."""
    seen: set[tuple[str, str]] = set()
    with (Path(run_dir) / "events.jsonl").open(encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            key = COUNTED.get(e["kind"])
            if key and key in (e.get("data") or {}):
                seen.add((e["kind"], e["data"][key]))
    return len(seen)


def estimate_usd(run_dir: Path | str) -> float:
    """record_calls times the calibration run's cost per call."""
    return record_calls(run_dir) * CALIBRATION_USD / CALIBRATION_CALLS


def main(argv: list[str]) -> int:
    for arg in argv:
        print(f"{Path(arg).name}: {record_calls(arg)} calls, about {estimate_usd(arg):.1f} USD")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

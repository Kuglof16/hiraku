"""A rough cost of a real Claude run, from the tool calls its scripted run made, for each model mix.

The scripted run calls the same tools in the same order a Claude run would (hiraku.agent.scripted), so the
calls that write a record (a prediction, an analysis, a comparison, a cause) are counted there and priced
at the cost per such call of the one real Claude run measured so far:

    runs/20261103-013120-offroad-4w (2026-11-03): 0.7644 USD (project.json cost_usd) for 5 such calls
    (2 prediction.recorded, 1 analysis.started, 2 comparison.recorded, all by cae.strength), with the lead
    and two roles started, every role on claude-opus-5-5. It recorded no token counts.

Per mix (data/catalog/model_mixes.yaml, estimate_mix): each agent's calls (the event's agent field) are
priced at the calibration cost per call times its role's model's price relative to claude-opus-5-5
(price_ratio; PRICES below). The lead's and the designer's own tokens are inside the calibration cost
per call (they write no counted record), so they are priced at the model of the role that wrote the
record: a mix that changes only the lead's model is not seen, and one that keeps the lead on Opus while
the roles move to a cheaper model is estimated too low by the lead's share.

An estimate, not a measurement (HIRAKU's assumption): it takes the same tokens per call for every role and
every model, and the calibration is one run. The context, and so the tokens per call, grows over a long
run; Claude reads (get_context, list_tools) and writes more or fewer calls than the script; a smaller model
may take more turns per call. Re-measure after the first long real run (its cost.updated now carries the
token counts per model) and change the constants here.

    python -m hiraku.catalog.cost <scripted run folder> [...]
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from hiraku.catalog.mixes import get_mix, load_mixes, role_key

CALIBRATION_RUN = "20261103-013120-offroad-4w"
CALIBRATION_USD = 0.7644  # its project.json cost_usd, rounded to 0.1 cent
CALIBRATION_CALLS = 5
CALIBRATION_MODEL = "claude-opus-5-5"
COUNTED = {"prediction.recorded": "prediction_id", "analysis.started": "analysis_id",
           "comparison.recorded": "comparison_id", "cause.recorded": "cause_id"}

# USD per million tokens. Source: https://claude.com/pricing (anthropic.com/pricing redirects there),
# read on 2026-11-03. cache_write is the 5-minute cache write.
PRICE_KINDS = ("input", "output", "cache_write", "cache_read")
PRICES = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_write": 5.0, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_write": 2.50, "cache_read": 0.10},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_write": 0.125, "cache_read": 0.01},  # prompts up to 100K tokens
}
PRICES_ABOVE_100K = {  # the same source: Haiku 5.5's price for prompts above 100K tokens
    "claude-haiku-5-5": {"input": 0.50, "output": 2.50, "cache_write": 0.625, "cache_read": 0.05},
}


def record_calls(run_dir: Path | str) -> int:
    """The calls of a run that wrote a record: distinct ids of the counted kinds (a record emitted again
    when it is updated counts once)."""
    return sum(record_calls_by_agent(run_dir).values())


def record_calls_by_agent(run_dir: Path | str) -> Counter:
    """record_calls per agent: each distinct record counted for the agent of its first event."""
    seen: dict[tuple[str, str], str] = {}
    with (Path(run_dir) / "events.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            key = COUNTED.get(e["kind"])
            if key and key in (e.get("data") or {}):
                seen.setdefault((e["kind"], e["data"][key]), e.get("agent") or "lead")
    return Counter(seen.values())


def estimate_usd(run_dir: Path | str) -> float:
    """record_calls times the calibration run's cost per call."""
    return record_calls(run_dir) * CALIBRATION_USD / CALIBRATION_CALLS


def price_ratios(model: str, *, above_100k: bool = False) -> dict[str, float]:
    """The model's price over claude-opus-5-5's, per kind of token (PRICE_KINDS)."""
    table = PRICES_ABOVE_100K if above_100k and model in PRICES_ABOVE_100K else PRICES
    if model not in table:
        raise KeyError(f"no price for model {model!r}")
    base = PRICES[CALIBRATION_MODEL]
    return {k: table[model][k] / base[k] for k in PRICE_KINDS}


def price_ratio(model: str, *, above_100k: bool = False) -> float:
    """The model's price over claude-opus-5-5's, on input tokens. Sonnet's ratio is the same on every
    kind; Haiku's is the same on input, output and cache write, but on cache read it is twice that
    (0.01 / 0.20 = 0.05 up to 100K, 0.05 / 0.20 = 0.25 above), so a run that is mostly cache reads
    costs more on Haiku than this ratio says."""
    return price_ratios(model, above_100k=above_100k)["input"]


def estimate_mix(run_dir: Path | str, mix: str) -> dict[str, float]:
    """{"usd", "upper_usd"} of a real run with the mix: each agent's record calls times the calibration
    cost per call times its model's price_ratio; upper_usd with the above-100K prices (the same as usd
    when no role is on a model with such a price)."""
    roles = get_mix(mix)
    per_call = CALIBRATION_USD / CALIBRATION_CALLS
    out = {"usd": 0.0, "upper_usd": 0.0}
    for agent, n in record_calls_by_agent(run_dir).items():
        model = roles[role_key(agent)]["model"]
        out["usd"] += n * per_call * price_ratio(model)
        out["upper_usd"] += n * per_call * price_ratio(model, above_100k=True)
    return out


def estimates(run_dir: Path | str, *, digits: int = 2) -> dict[str, dict[str, float]]:
    """estimate_mix for every mix, rounded (the shape of estimates_usd in data/catalog/demo_runs.yaml)."""
    return {name: {k: round(v, digits) for k, v in estimate_mix(run_dir, name).items()} for name in load_mixes()}


def main(argv: list[str]) -> int:
    for arg in argv:
        by_agent = record_calls_by_agent(arg)
        print(f"{Path(arg).name}: {sum(by_agent.values())} calls ({', '.join(f'{a} {n}' for a, n in sorted(by_agent.items()))})")
        for name, e in estimates(arg).items():
            upper = f" (up to {e['upper_usd']:.2f} above 100K tokens)" if e["upper_usd"] != e["usd"] else ""
            print(f"  {name}: about {e['usd']:.2f} USD{upper}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

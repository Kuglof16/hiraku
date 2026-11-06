"""A rough cost of a real Claude run, from the tool calls its scripted run made, for each model mix.

The scripted run calls the same tools in the same order a Claude run would (hiraku.agent.scripted), so the
calls that write a record (a prediction, an analysis, a comparison, a cause) are counted there and priced
at the cost per such call of the one long real Claude run measured so far:

    showcase/20261105-160725-offroad-4w (2026-11-05, the Claude Code plugin, done on version 7): 434 such
    calls; its Claude Code transcript (the main session and its 30 subagents, every message on
    claude-opus-5-5) holds 446 API messages with 896 input, 312,349 output, 30,772,960 cache read and
    2,594,623 cache write tokens (144,861 of them the 1-hour cache write, priced here at the 5-minute
    price: the 1-hour price is not in PRICES), 25.38 USD at PRICES (python -m hiraku.catalog.cost
    --transcript <session>.jsonl). A transcript writes one API message on several lines (one per content
    block, each with the message's usage), so tokens are counted once per message id.

    Before it: runs/20261103-013120-offroad-4w (2026-11-03), 0.7644 USD (project.json cost_usd) for 5 such
    calls, about 2.6 times the cost per call above.

Per mix (data/catalog/model_mixes.yaml, estimate_mix): each agent's calls (the event's agent field) are
priced at the calibration cost per call times its role's model's price relative to claude-opus-5-5,
as a range: the lowest and the highest ratio over the kinds of token and prompt sizes (ratio_range;
PRICES below), since the calibration run's token mix is not known. The lead's and the designer's own tokens are inside the calibration cost
per call (they write no counted record), so they are priced at the model of the role that wrote the
record: a mix that changes only the lead's model is not seen, and one that keeps the lead on Opus while
the roles move to a cheaper model is estimated too low by the lead's share.

An estimate, not a measurement (HIRAKU's assumption): it takes the same tokens per call for every role and
every model, and the calibration is one run. The context, and so the tokens per call, grows over a long
run; Claude reads (get_context, list_tools) and writes more or fewer calls than the script; a smaller model
may take more turns per call. Re-measure after the first long real run (its cost.updated now carries the
token counts per model) and change the constants here.

    python -m hiraku.catalog.cost <scripted run folder> [...]
    python -m hiraku.catalog.cost --transcript <Claude Code session>.jsonl [...]
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from hiraku.catalog.mixes import get_mix, load_mixes, role_key

CALIBRATION_RUN = "20261105-160725-offroad-4w"
CALIBRATION_USD = 25.38  # its transcript's tokens at PRICES (transcript_usd), rounded to 1 cent
CALIBRATION_CALLS = 434  # record_calls of showcase/20261105-160725-offroad-4w
CALIBRATION_MODEL = "claude-opus-5-5"
COUNTED = {"prediction.recorded": "prediction_id", "analysis.started": "analysis_id",
           "comparison.recorded": "comparison_id", "cause.recorded": "cause_id"}

# USD per million tokens. Source: https://claude.com/pricing (anthropic.com/pricing redirects there),
# read on 2026-11-03 and checked again on 2026-11-05 (every value below as on the page). cache_write is the
# 5-minute cache write.
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
    costs more on Haiku than this ratio says (ratio_range)."""
    return price_ratios(model, above_100k=above_100k)["input"]


def ratio_range(model: str) -> tuple[float, float]:
    """(lowest, highest) of the model's price ratios to claude-opus-5-5 over the kinds of token, the
    highest also over the above-100K prices. The calibration run recorded no token counts, so its mix of
    input, output, cache write and cache read is not known: under the same tokens per call, the run's cost
    on this model lies between the two (Sonnet 0.5 and 0.5; Haiku 0.025 and 0.25, its cache read above 100K)."""
    ratios = list(price_ratios(model).values()) + list(price_ratios(model, above_100k=True).values())
    return min(price_ratios(model).values()), max(ratios)


def estimate_mix(run_dir: Path | str, mix: str) -> dict[str, float]:
    """{"usd", "upper_usd"} of a real run with the mix: each agent's record calls times the calibration
    cost per call times its model's lowest price ratio (usd) and highest (upper_usd), ratio_range. The two
    are equal for a model whose price is the same fraction of Opus 5.5's on every kind of token."""
    roles = get_mix(mix)
    per_call = CALIBRATION_USD / CALIBRATION_CALLS
    out = {"usd": 0.0, "upper_usd": 0.0}
    for agent, n in record_calls_by_agent(run_dir).items():
        low, high = ratio_range(roles[role_key(agent)]["model"])
        out["usd"] += n * per_call * low
        out["upper_usd"] += n * per_call * high
    return out


def estimates(run_dir: Path | str, *, digits: int = 2) -> dict[str, dict[str, float]]:
    """estimate_mix for every mix, rounded (the shape of estimates_usd in data/catalog/demo_runs.yaml)."""
    return {name: {k: round(v, digits) for k, v in estimate_mix(run_dir, name).items()} for name in load_mixes()}


def transcript_tokens(session_jsonl: Path | str) -> dict[str, dict[str, int]]:
    """The tokens of a Claude Code session, per model: its transcript and the subagents' transcripts
    beside it (<session>/subagents/*.jsonl). An API message is written on one line per content block,
    each carrying the message's usage, so each message id is counted once (its last line). Keys: input,
    output, cache_read, cache_write (all cache creation), cache_write_1h (the 1-hour part of it), messages."""
    main_file = Path(session_jsonl)
    files = [main_file, *sorted((main_file.with_suffix("") / "subagents").glob("*.jsonl"))]
    last: dict[str, tuple[str, dict]] = {}
    for path in files:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                m = e.get("message") if isinstance(e, dict) else None
                if e.get("type") == "assistant" and isinstance(m, dict) and m.get("usage") and m.get("id"):
                    last[m["id"]] = (m.get("model") or "?", m["usage"])
    out: dict[str, dict[str, int]] = {}
    for model, u in last.values():
        t = out.setdefault(model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cache_write_1h": 0,
                                   "messages": 0})
        t["input"] += u.get("input_tokens") or 0
        t["output"] += u.get("output_tokens") or 0
        t["cache_read"] += u.get("cache_read_input_tokens") or 0
        t["cache_write"] += u.get("cache_creation_input_tokens") or 0
        t["cache_write_1h"] += (u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens") or 0
        t["messages"] += 1
    return out


def transcript_usd(session_jsonl: Path | str) -> float:
    """transcript_tokens priced at PRICES (every cache write at the 5-minute price). A model not in
    PRICES raises KeyError."""
    total = 0.0
    for model, t in transcript_tokens(session_jsonl).items():
        if model not in PRICES:
            raise KeyError(f"no price for model {model!r}")
        total += sum(t[k] * PRICES[model][k] for k in PRICE_KINDS) / 1e6
    return total


def main(argv: list[str]) -> int:
    if argv[:1] == ["--transcript"]:
        for arg in argv[1:]:
            for model, t in transcript_tokens(arg).items():
                print(f"{Path(arg).name} {model}: " + ", ".join(f"{k} {v:,}" for k, v in t.items()))
            print(f"  at PRICES: {transcript_usd(arg):.2f} USD")
        return 0
    for arg in argv:
        by_agent = record_calls_by_agent(arg)
        print(f"{Path(arg).name}: {sum(by_agent.values())} calls ({', '.join(f'{a} {n}' for a, n in sorted(by_agent.items()))})")
        for name, e in estimates(arg).items():
            upper = f" (up to {e['upper_usd']:.2f})" if e["upper_usd"] != e["usd"] else ""
            print(f"  {name}: about {e['usd']:.2f} USD{upper}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

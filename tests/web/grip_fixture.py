"""A handling run for the tyre view's tests (js/grip_view.js): the K2 tools' own records, cut small.

grip_fixture.json holds three analyses of the demo design (data/designs/offroad-4w-v1.json, its
corners on kc_table): kc_bench, lane_change (S3, 40 km/h) and braking (S4, 35 km/h, mu 0.6), as
the tools wrote them, with fewer samples and channels (cut below). Nothing in it is typed by hand:
every value is a recorded value, only some are left out. How it was made:

  - kc_bench: main's tool (1a155201), called directly (tests/web/dev_k2_run.py's way, no agent, no
    API key)
  - lane_change and braking: the K2 branch claude/k2-handling-data (0ee34218), called the same way
    from a copy outside the repository: it adds slip_angle, slip_ratio (%), tyre_fy_curve and
    tyre_fx_curve, which main's tools do not write yet. Their other channels are main's.

    python -m tests.web.grip_fixture <events.jsonl>   # writes grip_fixture.json from such a run

The cut: every STEP-th sample of a time series, plus the last one, the one of the largest friction
use and (for the mounts) the one of mount_force_peak; the wheel channels the view reads; the mounts
of the support named by mount_force_peak only (every 3rd sample and the peak's), with its joint
vectors; the tyre curves at every 3rd slip, Fy for the lane change and Fx for the braking (CURVES). grip_events() puts them in a run with times far enough apart for each motion to play.
"""

from __future__ import annotations

import copy
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "data" / "designs" / "offroad-4w-v1.json"
FIXTURE = Path(__file__).with_name("grip_fixture.json")
RUN_ID = "20261106-120000-grip"
STEP = {"lane_change": 16, "braking": 5}
# the tyre curves kept: Fy against slip angle for the lane change, Fx against slip ratio for the braking
CURVES = {"lane_change": ("tyre_fy_curve.",), "braking": ("tyre_fx_curve.",)}
BODY = ("body_x", "body_y", "body_z", "roll", "pitch", "yaw", "cg_x", "cg_y", "cg_z", "speed")
WHEEL = ("wheel_load", "wheel_fx", "wheel_fy", "friction_limit", "friction_use", "sliding", "slip_angle",
         "slip_ratio", "wheel_stroke", "wheel_steer", "contact_x", "contact_y", "contact_z")
# when each analysis arrives in the run (s): a motion plays from its analysis' arrival
TIMES = {"kc_bench": 1.0, "lane_change": 2.0, "braking": 20.0}
JST = timezone(timedelta(hours=9))


def _pick(n: int, step: int, keep: set[int]) -> list[int]:
    return sorted(set(range(0, n, step)) | {n - 1} | {k for k in keep if 0 <= k < n})


def _cut_series(series: list[dict], idx: list[int]) -> list[dict]:
    return [{**s, "t_s": [s["t_s"][k] for k in idx], "values": [s["values"][k] for k in idx]} for s in series]


def cut_analysis(a: dict) -> dict:
    """One analysis.finished record, cut (see the top of the file)."""
    a = copy.deepcopy(a)
    tool = a["tool"]
    if tool == "kc_bench":
        a["series"] = [s for s in a["series"] if s.get("x", {}).get("quantity") == "travel"]
        a["series"] = [{**s, "x": {**s["x"], "values": s["x"]["values"][::2]}, "values": s["values"][::2]} for s in a["series"]]
        a["assumptions"] = a.get("assumptions", [])[-1:]
        return a
    ch = {s["channel"]: s for s in a["series"]}
    wheels = sorted({n.split(".", 1)[1] for n in ch if n.startswith("wheel_load.")})
    timed = [ch[n] for n in BODY if n in ch] + [ch[f"{b}.{w}"] for b in WHEEL for w in wheels if f"{b}.{w}" in ch]
    use = [max(ch[f"friction_use.{w}"]["values"][k] for w in wheels) for k in range(len(timed[0]["t_s"]))] if f"friction_use.{wheels[0]}" in ch else []
    top = {use.index(max(use))} if use else set()
    out = _cut_series(timed, _pick(len(timed[0]["t_s"]), STEP[tool], top))
    peak = next((r for r in a.get("results", []) if r.get("quantity") == "mount_force_peak"), None)
    if peak:
        m = re.match(r"(\S+)\s+at t\s+([-\d.eE+]+)\s*s", peak.get("where", ""))
        where, t_peak = m.group(1), float(m.group(2))
        sup = where.split(".")[0]
        mounts = [s for n, s in ch.items() if re.match(rf"mount_[xyz]\.{re.escape(sup)}\.", n)]
        ts = mounts[0]["t_s"]
        k = min(range(len(ts)), key=lambda i: abs(ts[i] - t_peak))
        out += _cut_series(mounts, _pick(len(ts), 3, {k}))
        a["vectors"] = [v for v in a.get("vectors", []) if v["kind"] != "joint" or v["at"].split(".")[0] == sup]
    else:
        a["vectors"] = [v for v in a.get("vectors", []) if v["kind"] != "joint"]
    for s in a["series"]:
        if s["channel"].startswith(CURVES[tool]):
            out.append({**s, "x": {**s["x"], "values": s["x"]["values"][::3]}, "values": s["values"][::3]})
    a["series"] = out
    a.pop("hot_spots", None)
    a["assumptions"] = a.get("assumptions", [])[:3]
    return a


def write_fixture(events_path: Path) -> Path:
    evs = [json.loads(line) for line in Path(events_path).read_text(encoding="utf-8").splitlines()]
    lcs = evs[0]["data"]["load_cases"]
    anas = [cut_analysis(e["data"]) for e in evs if e["kind"] == "analysis.finished" and e["data"]["tool"] in TIMES]
    doc = {"about": "K2 records of offroad-4w-v1, cut by tests/web/grip_fixture.py (see there)", "load_cases": lcs, "analyses": anas}
    FIXTURE.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return FIXTURE


def grip_events(drop: tuple[str, ...] = (), source: dict | None = None, run_id: str = RUN_ID) -> list[dict]:
    """The run: started, the design, the analyses (TIMES), each with a screen.focus.
    drop: channel name prefixes to leave out of the motions (e.g. ("friction_", "sliding") for a
    record without the friction circle, ("mount_",) without mounts) or "kc_bench" for no bench."""
    doc = copy.deepcopy(source or json.loads(FIXTURE.read_text(encoding="utf-8")))
    # the dev runs' titles end in an aside saying where the load case came from; the log is narrower without it
    for lc in doc["load_cases"]:
        lc["title"] = re.sub(r"\s*\((カタログの荷重条件|試験のコードの例の値)\)$", "", lc["title"])
    design = json.loads(DESIGN.read_text(encoding="utf-8"))
    now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=JST)
    events = []

    def emit(kind, t, data, **extra):
        events.append({"seq": len(events), "ts": (now + timedelta(seconds=t)).isoformat(timespec="milliseconds"), "t": t,
                       "run_id": run_id, "kind": kind, **extra, "data": data})

    emit("run.started", 0.0, {"run_id": run_id, "created_at": now.isoformat(timespec="milliseconds"), "want": "操安の記録 (試験用に切り出した K2 の記録)",
                              "product": "offroad-4w", "round_limit": 1, "cost_limit_usd": 0, "load_cases": doc["load_cases"],
                              "domain_picks": [], "status": "done"})
    emit("design.created", 0.5, design, agent="designer")
    last = 0.0
    for a in doc["analyses"]:
        if a["tool"] in drop:
            continue
        a = copy.deepcopy(a)
        if drop:
            a["series"] = [s for s in a["series"] if not s["channel"].startswith(drop)]
            if "mount_" in drop:
                a["vectors"] = [v for v in a.get("vectors", []) if v["kind"] != "joint"]
                a["results"] = [r for r in a.get("results", []) if r.get("quantity") != "mount_force_peak"]
        t = TIMES[a["tool"]]
        meta = {"agent": "cae.handling", "domain": "handling", "design_version": 1}
        emit("analysis.started", t - 0.2, {"analysis_id": a["analysis_id"], "tool": a["tool"], "load_case_id": a["load_case_id"],
                                           "prediction_ids": a.get("prediction_ids", [])}, **meta)
        emit("analysis.finished", t, a, **meta)
        emit("screen.focus", t + 0.05, {"screen": "A3", "load_case_id": a["load_case_id"]})
        last = t
    emit("run.finished", last + 15.0, {"status": "done"})
    return events


def write_run(root: Path, events: list[dict]) -> Path:
    run = Path(root) / events[0]["run_id"]
    run.mkdir(parents=True, exist_ok=True)
    (run / "events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    return run


if __name__ == "__main__":
    print(write_fixture(Path(sys.argv[1])))

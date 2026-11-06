"""A run with K2's records of the step test's run 2 (rolling at the step at 10 km/h), for N06.

runup_records.json holds two step_climb_dynamic records K2 made with the run option
record_run = speed on offroad-4w version 1 (2026-11-06): P1-step330 (the vehicle meets the
330 mm step, does not climb it and stands against it to the record's end, 8.72 s) and
P1-step100 (it climbs the 100 mm step, climb_time 1.82 s, record to 2.60 s). They are cut
down for the tests: only the channels the cut draws (body_x/y/z, roll, pitch, yaw, cg_x/y/z,
wheel_load.<id>, wheel_center_x/y/z.<id>), every third sample of P1-step330 and every second
of P1-step100 (and the last), positions rounded to 0.1 mm, angles to 0.001 deg, loads to 1 N,
one t_s per record; results, scene and assumptions as recorded; vectors and hot_spots left out.
The values are the tool's, not made up.

The run: the contract sample's run.started with these two load cases (P1-step330 judged on
climbable >= 1 as the catalog writes P1 past the limit; P1-step100 without a criterion, as
the catalog's P1 at Q15) and the frozen copy of offroad-4w v1 (zu_designs/offroad-4w-v1.json),
the design the records were made on.
"""

import copy
import json
from pathlib import Path

from hiraku.webserve.server import SAMPLE_EVENTS

HERE = Path(__file__).resolve().parent
RECORDS = HERE / "runup_records.json"
DESIGN = HERE / "zu_designs" / "offroad-4w-v1.json"
RUN_ID = "20261106-120000-n06-runup"
SPEED = {"value": 10.0, "unit": "km/h"}


def records():
    """The two records with their series in the contract's shape, by load case id."""
    out = {}
    for lc_id, r in json.loads(RECORDS.read_text(encoding="utf-8")).items():
        rec = {k: v for k, v in r.items() if k not in ("t_s", "channels")}
        rec["series"] = [{"channel": n, "unit": u, "t_s": list(r["t_s"]), "values": vals} for n, (u, vals) in r["channels"].items()]
        out[lc_id] = rec
    return out


def load_cases(criterion=True):
    p1 = lambda lc_id, h, title, **kw: {  # noqa: E731
        "load_case_id": lc_id, "test_id": "P1", "domain": "powertrain", "title": title,
        "params": {"step_height": {"value": h, "unit": "mm"}, "speed": dict(SPEED), "loading": "two_cargo"},
        "tolerance_rel": 0.1, **kw}
    crit = {"criterion": {"quantity": "climbable", "op": ">=", "limit": {"value": 1, "unit": "1"},
                          "basis": "the vehicle climbs the step (1 climbs, 0 does not)"}} if criterion else {}
    return [p1("P1-step100", 100.0, "段差 100 mm を2 人 + 荷で越える"),
            p1("P1-step330", 330.0, "限界の外: 段差 330 mm を2 人 + 荷で越える", **crit)]


def runup_events(run_id=RUN_ID, criterion=True, recs=None):
    """run.started (the two load cases), the design, the two records, run.finished."""
    src = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    run = copy.deepcopy(src[0])
    created = copy.deepcopy(next(e for e in src if e["kind"] == "design.created"))
    run["run_id"] = run_id
    run["data"]["run_id"] = run_id
    run["data"]["load_cases"] = load_cases(criterion)
    created["data"] = json.loads(DESIGN.read_text(encoding="utf-8"))
    events = [run, created]
    recs = records() if recs is None else recs
    for i, (lc_id, rec) in enumerate(recs.items()):
        events.append({"kind": "analysis.finished", "agent": "cae.powertrain", "domain": "powertrain", "design_version": 1,
                       "data": {"analysis_id": f"ana-{i + 1:04d}", **rec}})
    events.append({"kind": "run.finished", "agent": "system", "data": {"status": "stopped", "stop_reason": "round_limit"}})
    out = []
    for k, ev in enumerate(events):
        out.append({**ev, "run_id": run_id, "ts": run["ts"], "t": 0.0 if k == 0 else 1.0 + 0.5 * k, "seq": k})
    return out

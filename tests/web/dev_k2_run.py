"""A run folder for working on the 3D test scenes: the real tools called directly
on the demo design, written so the screen can replay them.

    python -m tests.web.dev_k2_run <out_dir> [--only S1-tilt,P1-step100]
    python -m hiraku.webserve --runs <out_dir>

Not a HIRAKU run: no agent, no predictions or comparisons. Each analysis is the
tool's own output; its prediction id is a placeholder (the tools want one), and
the run says so in its title. Do not put the output under runs/ (the saved-run
checks expect real runs there).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from hiraku.contract.layout import new_run_id
from hiraku.solvers import builtin_tools

ROOT = Path(__file__).resolve().parents[2]
DESIGN = ROOT / "data" / "designs" / "offroad-4w-v1.json"
JST = timezone(timedelta(hours=9))


def q(v, u):
    return {"value": v, "unit": u}


# (load case id, test id, domain, title, tool, params when the catalog cannot build
# the test yet). The catalog's own load case is used when it builds (the same one a
# HIRAKU run would get); otherwise the K2 team's examples (tests/solvers/k2/test_tools.py
# CASES) or the solver tests' values (tests/solvers/test_k3.py LOOP), and the title
# says so. The 250 mm step is the one the demo vehicle is not meant to climb.
REQUIREMENTS = ROOT / "data" / "requirements" / "offroad-4w.yaml"
LOOP = {"distance": q(20, "km"), "grade_low": q(7, "%"), "grade_high": q(12, "%"), "speed_low": q(20, "km/h"), "speed_high": q(40, "km/h")}
STEP100 = {"loading": "two_cargo", "step_height": q(100.0, "mm"), "speed": q(10.0, "km/h"), "mu": q(0.8, "1")}
STEP250 = {**STEP100, "step_height": q(250.0, "mm")}
# A load case may be analysed by more than one tool (the first entry gives its params):
# the steps get the K1 hand-balance tool (the frame's stress at the push) before the K2
# drive (the motion), so the motion is the newest analysis of the case.
CASES = [
    ("P1-step100", "P1", "powertrain", "段差 100 mm を越える", "step_climb", STEP100),
    ("P1-step100", "P1", "powertrain", "段差 100 mm を越える", "step_climb_dynamic", STEP100),
    ("P1-step250", "P1", "powertrain", "段差 250 mm を越える", "step_climb", STEP250),
    ("P1-step250", "P1", "powertrain", "段差 250 mm を越える", "step_climb_dynamic", STEP250),
    # the strength test's load case only (no analysis): its criterion is the frame's allowable stress
    ("R1-step100", "R1", "strength", "静的強度", None, {}),
    ("S1-tilt", "S1", "handling", "傾斜台", "tilt", {"loading": "two_cargo"}),
    ("F1-wind", "F1", "aero", "横風 30 m/s", "crosswind",
     {"loading": "empty", "wind_speed": q(30.0, "m/s"), "speed": q(40.0, "km/h")}),
    ("S5-rough", "S5", "handling", "凸凹の横斜面 15°", "offroad_roll",
     {"loading": "two_cargo", "speed": q(20.0, "km/h"), "side_slope": q(15.0, "deg"), "length": q(40.0, "m")}),
    ("S3-dlc", "S3", "handling", "ダブルレーンチェンジ 40 km/h", "lane_change",
     {"loading": "two", "speed": q(40.0, "km/h"), "mu": q(0.8, "1"), "find_limit": False}),
    ("S4-brake", "S4", "handling", "急ブレーキ 35 km/h", "braking",
     {"loading": "two_cargo", "speed": q(35.0, "km/h"), "mu": q(0.6, "1")}),
    ("P4-long", "P4", "powertrain", "長時間 (20 km の坂道)", "drive_energy", {"loading": "two_cargo", **LOOP}),
    ("P2-launch", "P2", "powertrain", "坂で発進", "launch", {"loading": "two_cargo", "target_speed": q(40.0, "km/h")}),
    ("C1-wall", "C1", "crash", "壁に 40 km/h", "crash", {"loading": "two_cargo", "speed": q(40.0, "km/h")}),
    ("N1-modes", "N1", "nvh", "固有振動数とモード", "modal", {"loading": "two_cargo"}),
    ("R3-fatigue", "R3", "strength", "悪路 5000 km の疲労", "fatigue",
     {"loading": "two_cargo", "distance": q(5000, "km"), "road_class": "C", "speed": q(20, "km/h"), "sample_length": q(200, "m")}),
    ("T1-heat", "T1", "thermal", "40 ℃ の坂道で部品の温度", "thermal",
     {"loading": "two_cargo", **LOOP, "ambient_temperature": q(40, "degC")}),
]


def catalog_load_case(test_id: str, design: dict) -> dict | None:
    """The catalog's load case for a test, or None when it cannot be built yet."""
    try:
        from hiraku.catalog.build import build_load_cases
        from hiraku.catalog.loader import load_catalog, load_requirements

        cases = build_load_cases(load_catalog(), load_requirements(REQUIREMENTS), design, [test_id])
        return cases[0] if cases else None
    except Exception:  # noqa: BLE001 - any failure means: use the fallback values
        return None


def build(out_dir: Path, only: list[str] | None = None, log=print) -> Path:
    design = json.loads(DESIGN.read_text(encoding="utf-8"))
    tools = {t.name: t for t in builtin_tools()}
    cases = [c for c in CASES if not only or c[0] in only]
    now = datetime.now(JST)
    run_id = new_run_id(now, "offroad-4w-dev3d")
    run = Path(out_dir) / run_id
    run.mkdir(parents=True)
    load_cases = []
    for lc, test, domain, title, _tool, params in cases:
        if any(c["load_case_id"] == lc for c in load_cases):
            continue
        cat = catalog_load_case(test, design) if lc != "P1-step250" else None
        if cat:
            load_cases.append({**cat, "load_case_id": lc, "title": f"{title} (カタログの荷重条件)"})
        else:
            load_cases.append({"load_case_id": lc, "test_id": test, "domain": domain, "title": f"{title} (試験のコードの例の値)",
                               "tolerance_rel": 0.1, "params": params})
    project = {
        "run_id": run_id, "created_at": now.isoformat(timespec="milliseconds"),
        "want": "開発用: 3D の試験の場面 (道具を直接呼んだ記録。予測と照合は無い)", "product": "offroad-4w",
        "round_limit": 1, "cost_limit_usd": 0, "load_cases": load_cases, "domain_picks": [], "status": "done",
    }
    events = []

    def emit(kind, t, data=None, **extra):
        ev = {"seq": len(events), "ts": (now + timedelta(seconds=t)).isoformat(timespec="milliseconds"), "t": t,
              "run_id": run_id, "kind": kind, **extra}
        if data is not None:
            ev["data"] = data
        events.append(ev)

    emit("run.started", 0.0, project)
    emit("design.created", 0.5, design, agent="designer")
    t = 1.0
    for i, (lc, _test, domain, _title, tool, _params) in enumerate(cases, start=1):
        if tool is None:
            continue
        agent = f"cae.{domain}"
        load_case = next(c for c in load_cases if c["load_case_id"] == lc)
        aid = f"ana-{i:04d}"
        emit("analysis.started", t, {"analysis_id": aid, "tool": tool, "load_case_id": lc, "prediction_ids": [f"pred-{i:04d}"]},
             agent=agent, domain=domain, design_version=1)
        log(f"{lc}: {tool} ...")
        rec = tools[tool].run(design, load_case, [{"prediction_id": f"pred-{i:04d}"}], {})
        log(f"  {rec['status']} {rec.get('error', '')} {len(rec.get('series') or [])} channels, {rec.get('wall_time_s', 0):.1f} s")
        emit("analysis.finished", t + 0.5, {"analysis_id": aid, **rec}, agent=agent, domain=domain, design_version=1)
        emit("screen.focus", t + 0.6, {"screen": "A3", "load_case_id": lc})
        t += 1.0
    emit("run.finished", t, {"status": "done"})
    with (run / "events.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    (run / "project.json").write_text(json.dumps(project, ensure_ascii=False, indent=1), encoding="utf-8")
    return run


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m tests.web.dev_k2_run", description=__doc__.split("\n\n")[0])
    p.add_argument("out_dir")
    p.add_argument("--only", default="", help="comma-separated load case ids")
    args = p.parse_args(argv)
    run = build(Path(args.out_dir), [s for s in args.only.split(",") if s] or None)
    print(run)
    return 0


if __name__ == "__main__":
    sys.exit(main())

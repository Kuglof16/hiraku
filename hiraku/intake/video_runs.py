"""Records for the video scenes N09 (picking the domains) and N14 (hearing, knowledge to rules), made without Claude.

    python -m hiraku.intake.video_runs n09 [--runs runs]    four runs, one per product, that stop after the domains are picked
    python -m hiraku.intake.video_runs n14 [--folder DIR]   the files for the N14 run (requirements and intake events)

N09: a run of each product (guitar, care bed, serving robot, off-road vehicle) with run.started, domains.picked
(the cards, their reasons, and the line of the tests that have no load-case builder) and run.finished; no design,
no analysis. The picks are the code's (pick_domains with the product), the reasons are the code's too.

N14: the hearing (the missing requirements asked together, then the answers of the product's sample answers file) and the
built-in knowledge are the intake events of the run; the video's scripted plan (data/catalog/scripted.yaml,
offroad-4w-n14) then drops a file of rules and applies the rules to the parts. Run it with
    python -m hiraku run <folder>/offroad-4w-n14.yaml --scripted --no-intake --intake-events <folder>/intake.json
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from hiraku.catalog import REPO_ROOT, load_catalog, load_requirements, project_fields
from hiraku.contract import layout
from hiraku.intake import events as ev
from hiraku.intake import hearing, pipeline
from hiraku.intake.domains import pick_domains
from hiraku.intake.products import answers_file, load_product
from hiraku.store import RunStore

Record = dict[str, Any]

N09_PRODUCTS = ("electric-guitar", "care-bed", "serving-robot", "offroad-4w")
N14_FILE = REPO_ROOT / "data" / "requirements" / "offroad-4w-n14.yaml"
N14_NAME = "offroad-4w-n14.yaml"  # the scripted plan is looked up by the file's name


def make_n09(product: str, runs: Path | str = layout.RUNS_DIR) -> Path:
    """The run folder of one product: run.started, domains.picked, run.finished (made on the run store, below the agent)."""
    req = load_product(product)
    picked = pick_domains(req["want"], req["flags"], req.get("speed_class"), product=req.get("product"))
    catalog = load_catalog()
    keep = {k: v for k, v in req.items() if k not in ("design_file", "tests", "beyond")}  # no design: stop at the picks
    agents = [{"agent_id": a, "role": a, "prop": catalog.prop(a)} for a in ("lead", "designer")]
    store = RunStore.create(dict(project_fields(catalog, keep, []), agents=agents), root=Path(runs), slug=f"{product}-n09")
    event = ev.picked_event(picked)
    store.emit(event["kind"], agent=event.get("agent"), text=event.get("text"), data=event["data"])
    store.finish("stopped", stop_reason="分野を選ぶところまでの記録です (設計も解析もしていません)",
                 text="分野を選ぶところまでの記録です", stop_code="not_judged")
    return store.path


def prepare_n14(folder: Path | str) -> tuple[Path, Path]:
    """folder/offroad-4w-n14.yaml (the video's requirements and the hearing's results) and folder/intake.json."""
    req = load_requirements(N14_FILE)
    answers, note = hearing.load_answers(answers_file("offroad-4w"))
    result = pipeline.intake(req["want"], req["flags"], req.get("speed_class"), product=req["product"],
                             requirements=req["requirements"], answers=answers, note=note)
    merged = {**req, "requirements": result.requirements_file["requirements"]}  # keeps tests, design_file, limits
    folder = Path(folder)
    req_path, events_path = pipeline.save(result, folder, requirements_file=merged)
    target = folder / N14_NAME
    target.write_bytes(req_path.read_bytes())
    req_path.unlink()
    return target, events_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hiraku.intake.video_runs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a09 = sub.add_parser("n09")
    a09.add_argument("--runs", type=Path, default=layout.RUNS_DIR)
    a14 = sub.add_parser("n14")
    a14.add_argument("--folder", type=Path, default=layout.RUNS_DIR / "_video_files" / "n14")
    a = ap.parse_args(argv)
    if a.cmd == "n09":
        for p in N09_PRODUCTS:
            print(make_n09(p, a.runs))
    else:
        for p in prepare_n14(a.folder):
            print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

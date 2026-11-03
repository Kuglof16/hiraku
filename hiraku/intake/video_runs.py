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

N14 with a real Claude (the voice says "where I do not know, I ask"): the run asks for itself.
    python -m hiraku.intake.video_runs n14-real [--folder DIR]    DIR/offroad-4w-n14-real.yaml: the vehicle's requirements
        with the items a hearing asks for missing (Q03 Q20 Q34 Q64 Q74 Q80 Q91), no intake events, no stop_after
    python -m hiraku run DIR/offroad-4w-n14-real.yaml --no-intake       (Claude calls pick_domains, ask_user with
        wait_seconds, import_knowledge, rule_check itself); meanwhile, in another window:
    python -m hiraku.intake.video_runs answer RUN_FOLDER [--wait S]     answers the hearing as A2 would (waits for the
        question, then writes hearing.answer.json from data/requirements/products/offroad-4w.answers.yaml)
    python -m hiraku.intake.video_runs drop RUN_FOLDER [--after-kind K] [--wait S]   drops the notes file
        (data/knowledge/samples/bending-notes.md) as A5 would, once an event of kind K (default comparison.recorded) exists
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any

import yaml

from hiraku.catalog import REPO_ROOT, load_catalog, load_requirements, project_fields
from hiraku.contract import layout
from hiraku.intake import events as ev
from hiraku.intake import hearing, pipeline
from hiraku.intake.domains import pick_domains
from hiraku.intake.products import answers_file, load_product
from hiraku.store import RunStore

Record = dict[str, Any]

N09_PRODUCTS = ("electric-guitar", "care-bed", "serving-robot", "offroad-4w")
# The sentence a person would say, with what the flags say (the file's flags are what the product is): Claude reads the
# properties from the sentence, so a sentence that leaves out the battery makes it drop the thermal domain.
N09_WANTS = {
    "offroad-4w": "林道を走れる 2 人乗りの小さな電動のオフロード車両 (電池で走る 4 輪、最高 40 km/h、500 kg まで、積載 150 kg)",
}
N14_FILE = REPO_ROOT / "data" / "requirements" / "offroad-4w-n14.yaml"
N14_NAME = "offroad-4w-n14.yaml"  # the scripted plan is looked up by the file's name


def n09_requirements(product: str) -> Record:
    """The product's requirements for the N09 scene: no design or tests (it stops at the picks), the scene's sentence."""
    req = {k: v for k, v in load_product(product).items() if k not in ("design_file", "tests", "beyond")}
    return {**req, "want": N09_WANTS.get(product, req["want"]), "stop_after": "picks"}  # the run ends once the domains are picked


def write_n09_file(product: str, runs: Path | str = layout.RUNS_DIR) -> Path:
    """runs/_video_files/<product>-n09.yaml: the file a real run of the scene starts from."""
    folder = Path(runs) / "_video_files"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{product}-n09.yaml"
    path.write_text(yaml.safe_dump(n09_requirements(product), allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")
    return path


def make_n09(product: str, runs: Path | str = layout.RUNS_DIR) -> Path:
    """The run folder of one product: run.started, domains.picked, run.finished (made on the run store, below the agent)."""
    write_n09_file(product, runs)
    keep = n09_requirements(product)
    req = keep
    picked = pick_domains(req["want"], req["flags"], req.get("speed_class"), product=req.get("product"))
    catalog = load_catalog()
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


N14_REAL_NAME = "offroad-4w-n14-real.yaml"
KNOWLEDGE_SAMPLE = REPO_ROOT / "data" / "knowledge" / "samples" / "bending-notes.md"
DROP_TEXT = "板金と電池のメモです。規則にしてください"


def prepare_n14_real(folder: Path | str) -> Path:
    """The vehicle's N14 requirements as a person left them: the hearing's items are missing, no intake events."""
    req = load_requirements(N14_FILE)
    req = {k: v for k, v in req.items() if k != "stop_after"}
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / N14_REAL_NAME
    path.write_text(yaml.safe_dump(req, allow_unicode=True, sort_keys=False), encoding="utf-8", newline="\n")
    return path


def _wait_for(ready, wait_s: float, what: str) -> None:
    deadline = time.monotonic() + wait_s
    while not ready():
        if time.monotonic() >= deadline:
            raise TimeoutError(what)
        time.sleep(0.5)


def answer_hearing(run: Path | str, wait_s: float = 0.0) -> Path:
    """Answer the run's hearing as A2 does (hearing.answer.json from the vehicle's sample answers file)."""
    run = Path(run)
    if wait_s:
        _wait_for(lambda: (run / hearing.REQUEST_FILE).exists(), wait_s, f"no {hearing.REQUEST_FILE} in {run}")
    answers, note = hearing.load_answers(answers_file("offroad-4w"))
    return hearing.save_answer(run, {"answers": answers, "note": note or "", "use_defaults": True})


def drop_knowledge(run: Path | str, after_kind: str | None = None, wait_s: float = 0.0) -> Path:
    """Drop the notes file as A5 does: knowledge/<file>, then inbox/<n>-knowledge.json (the run turns it into rules)."""
    run = Path(run)
    if after_kind:
        def seen() -> bool:
            events = run / "events.jsonl"
            return events.exists() and any(json.loads(line).get("kind") == after_kind
                                           for line in events.read_text(encoding="utf-8").splitlines() if line.strip())

        _wait_for(seen, wait_s, f"no {after_kind} event in {run}")
    folder = run / layout.KNOWLEDGE_DIR
    folder.mkdir(exist_ok=True)
    name = KNOWLEDGE_SAMPLE.name
    (folder / name).write_bytes(KNOWLEDGE_SAMPLE.read_bytes())
    box = run / layout.INBOX_DIR
    box.mkdir(exist_ok=True)
    n = max((int(m.group(1)) for p in box.iterdir() if (m := re.match(r"^(\d+)-.*\.json$", p.name))), default=0) + 1
    tmp = box / f"{n}-knowledge.json.tmp"
    tmp.write_text(json.dumps({"kind": "knowledge", "file": name, "text": DROP_TEXT}, ensure_ascii=False), encoding="utf-8")
    final = box / f"{n}-knowledge.json"
    tmp.replace(final)
    return final


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m hiraku.intake.video_runs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a09 = sub.add_parser("n09")
    a09.add_argument("--runs", type=Path, default=layout.RUNS_DIR)
    a14 = sub.add_parser("n14")
    a14.add_argument("--folder", type=Path, default=layout.RUNS_DIR / "_video_files" / "n14")
    real = sub.add_parser("n14-real")
    real.add_argument("--folder", type=Path, default=layout.RUNS_DIR / "_video_files" / "n14-real")
    ans = sub.add_parser("answer")
    ans.add_argument("run", type=Path)
    ans.add_argument("--wait", type=float, default=0.0)
    drop = sub.add_parser("drop")
    drop.add_argument("run", type=Path)
    drop.add_argument("--after-kind", default="comparison.recorded")
    drop.add_argument("--wait", type=float, default=0.0)
    a = ap.parse_args(argv)
    if a.cmd == "n14-real":
        print(prepare_n14_real(a.folder))
        return 0
    if a.cmd == "answer":
        print(answer_hearing(a.run, a.wait))
        return 0
    if a.cmd == "drop":
        print(drop_knowledge(a.run, a.after_kind, a.wait))
        return 0
    if a.cmd == "n09":
        for p in N09_PRODUCTS:
            print(make_n09(p, a.runs))
    else:
        for p in prepare_n14(a.folder):
            print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""The viewer's data for any one analysis of a run:

    python -m viewer.src.build <run dir> <analysis id> [--json out.json | --out <dir>]

for_analysis(run_dir, analysis_id) finds the record (in the run's version folders, or a one-record folder with
analysis.json), picks the field from the record's tool (TOOL_FIELD) and calls that field's builder (viewer/src/<field>.py
build(args)) with the arguments its own command would get for that analysis (common.parser's defaults, plus
--version / --analysis of the record), so the data is the same object the builder's command writes into
viewer/data/<key>.js (after ja.translate, as common.write_data does). It returns
    {"field": <the page's data key: VIEW_DATA[field], data/<field>.js, index.html#<field>>, "data", "title", "source"}
or, when the record cannot be shown, {"field": None, "reason": "<one plain Japanese line>"}.
frame_shell (the page needs the frames a step-hit run saved, not in the record) returns a reason. rops_pushover
returns a reason in a run with version folders; in a run of events.jsonl only (below) the rops field is built for the
record's version by viewer/src/rops.py, which re-runs the run's tools/rops_pushover-*.py (seconds).

A run folder with events.jsonl and no version folders (the showcase runs) is laid out from its events first
(viewer/src/from_events.py materialize, cached in the temp folder by the events file's size and time); the result's
source.run is still the run's own name.

--json writes that dict as JSON (index.html?data=<url>#<field> reads it); --out writes a ready viewer folder
(index.html, js/, data/<field>.js).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

from viewer.src import common

Record = dict[str, Any]

# tool -> the page's data key (each builder's own reader: the tools it looks for)
TOOL_FIELD = {
    "frame_static": "strength", "frame_shell": "strength",
    "modal": "nvh", "modal_shell": "nvh_shell",
    "crash": "crash", "crash_beam": "crash",
    "tilt": "handling", "braking": "handling", "lane_change": "handling", "offroad_roll": "handling",
    "launch": "powertrain", "step_climb_dynamic": "powertrain", "drive_energy": "powertrain",
    "crosswind": "aero",
    "fatigue": "fatigue",
    "thermal": "thermal",
    "assembly": "assembly", "entry_opening": "assembly", "spray_reach": "assembly",
    "rops_pushover": "rops",
}

NOT_HERE = {   # tools whose field needs more than the record (a reason, nothing solved)
    "rops_pushover": "ROPS の画面は run のツールをもう一度動かして押したときの形を求める (記録に変位がない)。ここでは解かないので見せられない",
    "frame_shell": "シェルの応力の画面は段差の計算が保存したフレーム (frames.npz・frames_meta.pkl・load_paths.json) から作る。記録にはそれがない",
}


def _reason(text: str) -> Record:
    return {"field": None, "reason": text}


def find_record(run: Path, analysis_id: str) -> tuple[Path, Record] | None:
    """(version folder, record) of the analysis id: the last one with that id, as common.find_analysis takes it."""
    run = Path(run)
    one = run / "analysis.json"
    if one.is_file():
        rec = common.read_json(one)
        rec["_path"] = str(one)
        return (run, rec) if rec.get("analysis_id") == analysis_id else None
    found = None
    for vd in common.version_dirs(run):
        for a in common.analyses(vd):
            if a.get("analysis_id") == analysis_id:
                found = (vd, a)
    return found


def _args(field: str, run: Path, vdir: Path, analysis_id: str, extra=None, **opts) -> argparse.Namespace:
    """The arguments the field's command would get for this analysis (its parser's defaults)."""
    argv = [str(run), "--analysis", analysis_id]
    if Path(vdir).resolve() != Path(run).resolve():
        argv += ["--version", Path(vdir).name]
    args = common.parser(field, extra).parse_args(argv)
    for k, v in opts.items():
        setattr(args, k, v)
    return args


def _has_field(rec: Record, kind: str, key: str) -> bool:
    return any(f.get("kind") == kind and key in f for f in rec.get("fields", []) or [])


def _has_series(rec: Record, *channels: str) -> bool:
    have = {s.get("channel") for s in rec.get("series", []) or []}
    return all(c in have for c in channels)


# ---------------------------------------------------------------- one function per field: (run, vdir, rec) -> data

def _strength(run, vdir, rec):
    from viewer.src import strength
    if not (_has_field(rec, "stress", "per_member") and _has_field(rec, "displacement", "per_node")):
        return "記録に部材ごとの応力 (per_member) と節点ごとの変位 (per_node) がない"
    return strength.build(_args("strength", run, vdir, rec["analysis_id"], strength._extra))


def _nvh(run, vdir, rec):
    from viewer.src import nvh
    if not _has_field(rec, "mode_shape", "per_node"):
        return "記録にフレームの節点のモードの形 (mode_shape の per_node) がない"
    return nvh.build(_args("nvh", run, vdir, rec["analysis_id"]))


def _nvh_shell(run, vdir, rec):
    from viewer.src import nvh_shell
    mid = next((f.get("mesh_id") for f in rec.get("fields", []) or [] if f.get("mesh_id")), None)
    if mid is None:
        return "記録にメッシュの上のモードの形がない (別のメッシュの大きさで解いた記録)"
    if not (Path(vdir) / "meshes" / f"{mid}.json").is_file():
        return f"記録のメッシュ ({mid}) が run のフォルダにない"
    one = (Path(run) / "analysis.json").is_file()
    if one and not (Path(run) / "design.json").is_file():
        return "記録だけのフォルダに設計 (design.json) がない"
    args = argparse.Namespace(run=Path(run), out=common.DEFAULT_OUT, design=None,
                              version=None if one or Path(vdir).resolve() == Path(run).resolve() else Path(vdir).name,
                              analysis=rec["analysis_id"])
    return nvh_shell.build(args)


def _crash(run, vdir, rec):
    from viewer.src import crash
    if rec["tool"] == "crash_beam" and not any(f.get("kind") == "node_motion" for f in rec.get("fields", []) or []):
        return "衝突 (B) の記録に節点の動き (node_motion) がない"
    if rec["tool"] == "crash" and not (_has_field(rec, "stress", "per_member") and _has_field(rec, "displacement", "per_node")):
        return "衝突 (A) の記録に部材ごとの応力と節点ごとの変位がない"
    return crash.build(_args("crash", run, vdir, rec["analysis_id"], crash._extra, tool=rec["tool"]))


def _k2_ok(rec):
    return _has_series(rec, "body_x")


def _handling(run, vdir, rec):
    from viewer.src import handling
    if not _k2_ok(rec):
        return "記録に車体の動きの時系列 (body_x など) がない"
    return handling.build(_args("handling", run, vdir, rec["analysis_id"], handling.extra))


def _powertrain(run, vdir, rec):
    from viewer.src import powertrain
    if rec["tool"] == "drive_energy":
        if not _has_series(rec, "speed", "grade", "power.battery", "current.battery", "soc"):
            return "周回の電力の記録に速さ・勾配・電池の電力と電流・残量の時系列がない"
    elif not _k2_ok(rec):
        return "記録に車体の動きの時系列 (body_x など) がない"
    return powertrain.build(_args("powertrain", run, vdir, rec["analysis_id"], powertrain.extra))


def _aero(run, vdir, rec):
    from viewer.src import aero
    if not _k2_ok(rec):
        return "記録に車体の動きの時系列 (body_x など) がない"
    return aero.build(_args("aero", run, vdir, rec["analysis_id"], aero.extra))


def _fatigue(run, vdir, rec):
    from viewer.src import fatigue
    if not _has_field(rec, "damage", "per_member"):
        return "記録に部材ごとの損傷 (damage の per_member) がない"
    return fatigue.build(_args("fatigue", run, vdir, rec["analysis_id"]))


def _thermal(run, vdir, rec):
    from viewer.src import thermal
    if not any(str(s.get("channel", "")).startswith("temp.") for s in rec.get("series", []) or []):
        return "記録に節点の温度の時系列 (temp.<節点>) がない"
    return thermal.build(_args("thermal", run, vdir, rec["analysis_id"], thermal._extra))


def _assembly(run, vdir, rec):
    from viewer.src import assembly
    if rec["tool"] != "assembly":
        # entry_opening / spray_reach: numbers on the assembly field of the same version
        try:
            _, asm = common.find_analysis(vdir, "assembly")
        except LookupError:
            return "この版に組み立ての記録がない (乗り降りの開口・洗車の届きは組み立ての画面に数で出す)"
        rec = asm
    return assembly.build(_args("assembly", run, vdir, rec["analysis_id"], assembly.extra, load_case=rec.get("load_case_id")))


BUILD: dict[str, Callable] = {"strength": _strength, "nvh": _nvh, "nvh_shell": _nvh_shell, "crash": _crash,
                              "handling": _handling, "powertrain": _powertrain, "aero": _aero, "fatigue": _fatigue,
                              "thermal": _thermal, "assembly": _assembly}


def _rops(orig: Path, rec: Record) -> Record | str:
    """The rops field for the one version of this record: rops.build re-runs the run's tools/rops_pushover-*.py from
    events.jsonl (seconds for one version)."""
    from viewer.src import rops
    if not (orig / "events.jsonl").is_file() or len(list((orig / "tools").glob(f"{rops.TOOL}-*.py"))) != 1:
        return NOT_HERE["rops_pushover"] + " (run に events.jsonl と tools/rops_pushover-*.py がない)"
    args = argparse.Namespace(run=orig, versions=str(rec["design_version"]), tool_file=None, out=common.DEFAULT_OUT)
    try:
        with contextlib.redirect_stdout(io.StringIO()):   # rops.build logs each case's re-run on stdout
            obj = rops.build(args)
    except (LookupError, RuntimeError, StopIteration) as e:
        return f"ROPS の計算をもう一度動かせなかった ({type(e).__name__})"
    if obj["source"].get("analysis_id") != rec["analysis_id"]:
        return f"この版の ROPS の画面は別の記録 ({obj['source'].get('analysis_id')}) から作られる"
    return obj


def for_analysis(run_dir, analysis_id: str) -> Record:
    """See the module docstring. A run folder with events.jsonl and no version folders (the showcase runs) is first
    laid out from its events (from_events.materialize, cached in the temp folder); source still names the run."""
    from viewer.src import from_events, ja
    orig = run = Path(run_dir)
    if from_events.needs_materialize(run):
        run = from_events.materialize(run)
    try:
        got = find_record(run, analysis_id)
    except FileNotFoundError:
        return _reason("run のフォルダに版 (v001 など) も記録もない")
    if got is None:
        return _reason(f"この run に解析 {analysis_id} がない")
    vdir, rec = got
    tool = rec.get("tool", "")
    if rec.get("status") != "ok":
        return _reason(f"解析 {analysis_id} は終わっていない (状態 {rec.get('status')})")
    if tool == "rops_pushover" and run != orig:
        field = "rops"
    elif tool in NOT_HERE:
        return _reason(NOT_HERE[tool])
    else:
        field = TOOL_FIELD.get(tool)
        if field is None or field not in BUILD:
            return _reason(f"この道具 ({tool}) の記録を見せる画面がない")
    if not (Path(vdir) / "design.json").is_file():
        return _reason("記録の版のフォルダに設計 (design.json) がない")
    try:
        obj = _rops(orig, rec) if field == "rops" else BUILD[field](run, vdir, rec)
    except LookupError:
        return _reason("記録に画面に要るもの (場・時系列・荷重の条件) が足りない")
    if isinstance(obj, str):
        return _reason(obj)
    data = ja.translate(obj)
    return {"field": field, "data": data, "title": data.get("title", ""), "source": data.get("source", {})}


def write_viewer(got: Record, out: Path) -> Path:
    """A ready viewer folder: index.html and js/ of this viewer, and data/<field>.js."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    shutil.copy2(common.VIEWER / "index.html", out / "index.html")
    shutil.copytree(common.VIEWER / "js", out / "js", dirs_exist_ok=True)
    (out / "data").mkdir(exist_ok=True)
    path = out / "data" / f"{got['field']}.js"
    path.write_text(common.data_js(got["field"], got["data"]), encoding="ascii")
    return path


def main(argv=None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="python -m viewer.src.build", description=__doc__.split("\n")[0])
    ap.add_argument("run", type=Path, help="run folder (with v###/), a version folder, or a folder with analysis.json")
    ap.add_argument("analysis_id")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--json", type=Path, help="write the {field, data, title, source} JSON here")
    g.add_argument("--out", type=Path, help="write a ready viewer folder here")
    args = ap.parse_args(argv)
    got = for_analysis(args.run, args.analysis_id)
    if args.out and got["field"] is not None:
        p = write_viewer(got, args.out)
        print(f"wrote {p.parent.parent} ({got['field']}: {got['title']})")
    elif args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(got, ensure_ascii=True, separators=(",", ":")), encoding="ascii")
        print(f"wrote {args.json} ({got['field'] or got['reason']})")
    else:
        print(json.dumps({k: got[k] for k in got if k != "data"}, ensure_ascii=False))
    if got["field"] is None:
        print(got["reason"], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""R4 roll bar push-over (rops_pushover) for the viewer: the run's own tool re-run on its designs, with the shape at
every plastic hinge event.

    python -m viewer.src.rops <run dir> [--versions 3,5,6] [--tool-file <path>] [--out viewer/data]

<run dir>: a run folder with events.jsonl (the designs from design.created, the load cases from load_cases.created,
the records from analysis.finished of tool rops_pushover) and the tool's file in tools/rops_pushover-*.py (the run
wrote it; it is imported by path and not changed).

What is re-run and how:
- the tool's RopsPushover().analyse(design, load_case, params) is called as the run called it, with the module's push
  and rollbar.occupant_clearance wrapped so their arguments and results are kept (the wrapped functions are the
  tool's own; nothing else changes). Each re-run clearance is checked against the record's
  rops_occupant_clearance.<case> (CHECK_MM) before anything is written.
- push() returns only the final displacements. It is an event-to-event solution: between two hinge events the
  stiffness does not change, so the displacements are linear in the load between them, and after a mechanism the
  frame moves along the one mechanism shape at a constant load. So the shape at each event's load factor, the
  final shape, and straight lines between them are the tool's solution itself (no approximation). The shape at
  event k is got by calling the same push() with kind "force" and the target at that event's load factor: it runs
  the same steps and stops at that load (the steps do not depend on kind or target before they stop).
  `segment_check` compares one mid-segment push() with the straight line between its events (tests).

Data (viewer/data/rops.js), on top of common's shared keys for the first version (frame = that version's model:
the design's members with rollbar_top cut at the tool's extra nodes, parts, ledger, source):
    versions [{v, analysis_id, frame {node_ids, nodes, members, member_ids, member_names, radius_mm}, parts,
               spaces [{occupant, lo, hi}], ground, ledger, record {case: clearance}, worst {case, value, member,
               occupant}, cases {name: case}}]
    case: {load_node (index), dir, kind, target, unit, n_states, F [N per state], d [mm per state], energy_J, U
           (packed f32 states x nodes x 3, mm), hinges [{elem, end, axis, F, state}], mechanism, collapse_F,
           clear [mm per state], member, occupant, sub (steps per segment), sub_clear [per occupant: mm per sub-step],
           sub_member [member per sub-step], record_clear, rerun_clear}
    states: 0 = unloaded, 1..n_events = each hinge event, last = the final (the energy reached or the force).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

from viewer.src import common, ledgers, names

TOOL = "rops_pushover"
CHECK_MM = 0.01          # the re-run clearance must equal the record's within this
SUB = 8                  # sub-steps per segment for the clearance curve (the shape is linear between events)
CASES_JA = {"side_left": "横 (左の角を右へ)", "side_right": "横 (右の角を左へ)", "rear_fwd": "後ろ (前へ押す)",
            "rear_back": "後ろ (後ろへ押す)", "top": "上 (下へ押す)"}
# the order and directions of analyse()'s runs (the tool's code, lines `runs = {...}`), to name the captured calls
RUN_DIRS = {"side_left": [0, -1, 0], "side_right": [0, 1, 0], "rear_fwd": [1, 0, 0], "rear_back": [-1, 0, 0],
            "top": [0, 0, -1]}


def read_events(run: Path) -> list[dict]:
    return [json.loads(l) for l in (Path(run) / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def tool_file(run: Path, given: str | None) -> Path:
    if given:
        return Path(given)
    found = sorted((Path(run) / "tools").glob(f"{TOOL}-*.py"))
    if len(found) != 1:
        raise LookupError(f"{run}: expected one tools/{TOOL}-*.py, found {len(found)} (give --tool-file)")
    return found[0]


def load_tool(path: Path):
    spec = importlib.util.spec_from_file_location(f"run_tool_{TOOL}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def inputs_of(events: list[dict], version: int) -> tuple[dict, dict, dict]:
    """(design, load case, record) of the version's rops_pushover record, from the run's events."""
    rec = next((e["data"] for e in events if e["kind"] == "analysis.finished" and e["data"].get("tool") == TOOL
                and e["data"].get("design_version") == version and e["data"].get("status") == "ok"), None)
    if rec is None:
        raise LookupError(f"no ok {TOOL} record of v{version}")
    design = next(e["data"] for e in events if e["kind"] == "design.created" and e["data"]["design_version"] == version)
    lc = None
    for e in events:
        if e["kind"] == "load_cases.created" and e["data"].get("design_version", e.get("design_version")) <= version:
            for c in e["data"]["load_cases"]:
                if c["load_case_id"] == rec["load_case_id"]:
                    lc = c   # the newest set made at or before this version
    if lc is None:
        raise LookupError(f"no load case {rec['load_case_id']} for v{version}")
    return design, lc, rec


class Capture:
    """The tool module with push and rollbar.occupant_clearance wrapped: their calls kept in order."""

    def __init__(self, mod):
        self.mod, self.pushes, self.clears = mod, [], []
        self._push, self._rollbar = mod.push, mod.rollbar
        cap = self

        def push(*a, **k):
            r = cap._push(*a, **k)
            cap.pushes.append((a, k, r))
            return r

        class Rollbar:
            def __getattr__(self, name):
                return getattr(cap._rollbar, name)

            def occupant_clearance(self, *a, **k):
                r = cap._rollbar.occupant_clearance(*a, **k)
                cap.clears.append((a, k, r))
                return r

        mod.push, mod.rollbar = push, Rollbar()

    def restore(self):
        self.mod.push, self.mod.rollbar = self._push, self._rollbar


def rerun(mod, design: dict, lc: dict) -> dict:
    """analyse() as the run called it; returns {results, assumptions, runs {name: {args, kwargs, result, clear_args,
    clear}}}."""
    cap = Capture(mod)
    try:
        out = mod.RopsPushover().analyse(design, lc, lc.get("params", {}))
    finally:
        cap.restore()
    names_ = list(RUN_DIRS)
    if len(cap.pushes) != len(names_) or len(cap.clears) != len(names_):
        raise RuntimeError(f"analyse made {len(cap.pushes)} pushes and {len(cap.clears)} clearance calls, expected "
                           f"{len(names_)} each (the tool's runs changed?)")
    runs = {}
    for name, (a, k, r), (ca, ck, cr) in zip(names_, cap.pushes, cap.clears):
        dv = np.asarray(a[4], float)
        if not np.allclose(dv / np.linalg.norm(dv), RUN_DIRS[name]):
            raise RuntimeError(f"push {name}: direction {a[4]} is not the tool's {RUN_DIRS[name]}")
        runs[name] = {"args": a, "kwargs": k, "result": r, "clear_args": (ca, ck), "clear": cr}
    return {"results": out["results"], "assumptions": out["assumptions"], "runs": runs}


def top_chain(elems: list[dict]) -> list[str]:
    """The nodes of rollbar_top in order (rollbar_top#0, #1, ...: the tool's split of the top beam)."""
    parts = sorted((e for e in elems if e["id"].startswith("rollbar_top#")), key=lambda e: int(e["id"].split("#")[1]))
    return [parts[0]["a"]] + [e["b"] for e in parts]


def states_of(mod, run: dict) -> dict:
    """The push's states: unloaded, each hinge event (push() again with kind force capped at the event's load
    factor) and the final (the captured result)."""
    a, k, r = run["args"], run["kwargs"], run["result"]
    nodes, elems, fixed, ln, dv = a[:5]
    lams = sorted({h[3] for h in r["hinges"]})
    ids = list(nodes)
    U = [np.zeros((len(ids), 3))]
    F, d = [0.0], [0.0]
    for lam in lams:
        rk = mod.push(nodes, elems, fixed, ln, dv, "force", lam, max_events=k.get("max_events", 150))
        U.append(np.array([rk["U"][n] for n in ids]))
        F.append(float(rk["F"]))
        d.append(float(rk["d"]))
    U.append(np.array([r["U"][n] for n in ids]))
    F.append(float(r["F"]))
    d.append(float(r["d"]))
    return {"ids": ids, "lams": lams, "U": U, "F": F, "d": d}


def segment_check(mod, run: dict, st: dict, seg: int) -> float:
    """Largest |push() at the middle of segment seg (between events seg and seg + 1) - the straight line| (mm)."""
    a, k = run["args"], run["kwargs"]
    nodes, elems, fixed, ln, dv = a[:5]
    lam = 0.5 * (st["F"][seg] + st["F"][seg + 1])
    rk = mod.push(nodes, elems, fixed, ln, dv, "force", lam, max_events=k.get("max_events", 150))
    um = np.array([rk["U"][n] for n in st["ids"]])
    return float(np.abs(um - 0.5 * (st["U"][seg] + st["U"][seg + 1])).max())


def clearance_at(mod, run: dict, st: dict, U: np.ndarray) -> dict:
    """rollbar.occupant_clearance as analyse() calls it (the design nodes moved, the top beam's deformed line)."""
    (ca, ck) = run["clear_args"]
    d_on = ca[0]
    elems = run["args"][1]
    design_nodes = {n["id"] for n in d_on["nodes"]}
    ix = {n: i for i, n in enumerate(st["ids"])}
    nodes = run["args"][0]
    disp = {n: U[ix[n]].tolist() for n in st["ids"] if n in design_nodes}
    curve = [[nodes[n][j] + U[ix[n]][j] for j in range(3)] for n in top_chain(elems)]
    return mod.rollbar.occupant_clearance(d_on, displacements=disp, curves={"rollbar_top": curve})


def per_occupant(c: dict, occ: list[str]) -> list[float]:
    return [min(p["clearance_mm"] for p in c["pairs"] if p["occupant"] == o) for o in occ]


def model_frame(design: dict, run: dict) -> dict:
    """The tool's model as beam lines: its nodes (with the top beam's extra nodes) and elements."""
    nodes, elems = run["args"][0], run["args"][1]
    ids = list(nodes)
    ix = {n: i for i, n in enumerate(ids)}
    mem = {m["id"]: m for m in design["members"]}
    secs = design.get("sections", {})
    base = lambda eid: eid.split("#")[0]
    return {"node_ids": ids, "nodes": common.pack_f32([c for n in ids for c in nodes[n]], [len(ids), 3]),
            "members": [[ix[e["a"]], ix[e["b"]]] for e in elems], "member_ids": [e["id"] for e in elems],
            "member_names": [mem[base(e["id"])].get("name", base(e["id"])) for e in elems],
            "radius_mm": [common._radius(secs.get(mem[base(e["id"])].get("section"))) for e in elems]}


def rops_ledger(design: dict, lc: dict, rec: dict, parts: list[dict]) -> dict:
    """The push has no masses and no gravity: only the frame's members carry the load. The occupants' boxes and the
    seats under them set the occupants' spaces (k4_geometry.rollbar.occupant_spaces); every other part is not in
    the calculation."""
    out, dparts, on_ids = ledgers._base(TOOL, design, lc, names_categories())
    for p in parts:
        e = ledgers._entry(p, dparts.get(p["id"], {}))
        if p["id"] not in on_ids:
            out["parts"].append(ledgers._off(e, out["loading"]))
            continue
        if p.get("kind") in ("occupant", "seat"):
            e.update(cat="body", fn="hiraku.solvers.k4_geometry.rollbar.occupant_spaces",
                     how=("乗員の箱: 前後はこの箱、左右はこの箱と AIST の肩幅 (男性 95 パーセンタイル) の広い方、上は座席の上面から AIST "
                          "の座高 (同) まで。この空間とロールバーの部材のすき間を測る" if p.get("kind") == "occupant" else
                          "座席: 上面が乗員の空間の床になる (重さは使わない)"))
        else:
            e.update(how="押す計算には質量も重力もない (部材だけが力を受ける)")
        out["parts"].append(e)
    out["members"] = {"count": len(design.get("members", [])), "mass_kg": None,
                      "fn": "rops_pushover push (the run's tool)", "label": "フレームの部材 (梁、塑性ヒンジ)",
                      "how": "3 次元の梁。部材の端に塑性ヒンジが一つずつ入る。支持点の節点で固定"}
    out["notes"].append("押す計算は質量を使わない (照合なし)")
    return ledgers._finish(out, None, names_categories())


def names_categories() -> dict:
    return {"body": "乗員の空間を決める (質量なし)", "not_in": common.CATEGORIES["not_in"]}


def case_data(mod, design: dict, run: dict, rec_clear: float, occ: list[str]) -> tuple[dict, dict]:
    a, r = run["args"], run["result"]
    nodes, elems, fixed, ln, dv, kind, tgt = a[:7]
    st = states_of(mod, run)
    ix = {n: i for i, n in enumerate(st["ids"])}
    eix = {e["id"]: i for i, e in enumerate(elems)}
    lam_state = {lam: i + 1 for i, lam in enumerate(st["lams"])}
    clear, member, occupant = [], [], []
    for U in st["U"]:
        c = clearance_at(mod, run, st, U)
        clear.append(c["clearance_mm"])
        member.append(c["member"])
        occupant.append(c["occupant"])
    sub_clear, sub_member = [[] for _ in occ], []
    for s in range(len(st["U"]) - 1):
        for j in range(SUB + (1 if s == len(st["U"]) - 2 else 0)):
            U = st["U"][s] + (j / SUB) * (st["U"][s + 1] - st["U"][s])
            c = clearance_at(mod, run, st, U)
            for o, v in zip(occ, per_occupant(c, occ)):
                sub_clear[occ.index(o)].append(v)
            sub_member.append(c["member"])
    # the energy (area under force-deflection; the segments are straight lines)
    en = [0.0]
    for i in range(1, len(st["F"])):
        en.append(en[-1] + 0.5 * (st["F"][i] + st["F"][i - 1]) * (st["d"][i] - st["d"][i - 1]))
    out = {"load_node": ix[ln], "dir": [float(v) for v in dv], "kind": kind,
           "target": float(tgt) / (1000.0 if kind == "energy" else 1.0), "unit": "J" if kind == "energy" else "N",
           "n_states": len(st["U"]), "F": st["F"], "d": st["d"], "energy_J": [v / 1000.0 for v in en],
           "U": common.pack_f32([c for U in st["U"] for c in U.ravel()], [len(st["U"]), len(st["ids"]), 3]),
           "hinges": [{"elem": eix[h[0]], "end": h[1], "axis": h[2], "F": float(h[3]), "state": lam_state[h[3]]}
                      for h in r["hinges"]],
           "mechanism": bool(r["mechanism"]), "collapse_F": r["collapse_F"], "clear": clear, "member": member,
           "occupant": occupant, "sub": SUB, "sub_clear": sub_clear, "sub_member": sub_member,
           "record_clear": rec_clear, "rerun_clear": run["clear"]["clearance_mm"]}
    return out, st


def version_data(mod, events: list[dict], v: int, log=print) -> dict:
    design, lc, rec = inputs_of(events, v)
    rr = rerun(mod, design, lc)
    recd = {r["quantity"].split(".", 1)[1]: float(r["value"]) for r in rec["results"]
            if r["quantity"].startswith("rops_occupant_clearance.")}
    bad = []
    for name, run in rr["runs"].items():
        got = run["clear"]["clearance_mm"]
        if name not in recd or abs(got - recd[name]) > CHECK_MM:
            bad.append(f"{name}: re-run {got:.4f} mm, record {recd.get(name)}")
    if bad:
        raise RuntimeError(f"v{v} {rec['analysis_id']}: the re-run does not give the record's clearances: " + "; ".join(bad))
    first = next(iter(rr["runs"].values()))
    spaces = first["clear"]["spaces"]
    occ = [s["occupant"] for s in spaces]
    cases = {}
    for name, run in rr["runs"].items():
        cases[name], st = case_data(mod, design, run, recd[name], occ)
        if abs(cases[name]["clear"][-1] - recd[name]) > CHECK_MM:
            raise RuntimeError(f"v{v} {name}: the final state's clearance {cases[name]['clear'][-1]} is not the record's")
        log(f"v{v} {name}: {len(st['lams'])} events, F {st['F'][-1]:.0f} N, d {st['d'][-1]:.1f} mm, clearance "
            f"{cases[name]['clear'][-1]:+.3f} mm (record {recd[name]:+.3f})")
    worst = min(recd, key=recd.get)
    wr = next(r for r in rec["results"] if r["quantity"] == f"rops_occupant_clearance.{worst}")
    parts = common.parts_of(design)
    return {"v": v, "analysis_id": rec["analysis_id"], "load_case_id": rec["load_case_id"], "design_name": design.get("name", ""),
            "design_key": common.design_key(design), "frame": model_frame(design, first), "parts": parts,
            "spaces": [{"occupant": s["occupant"], "lo": s["lo_mm"], "hi": s["hi_mm"]} for s in spaces],
            "space_basis": spaces[0]["basis"] if spaces else "",
            "ground": common.ground_of(design), "ledger": rops_ledger(design, lc, rec, parts),
            "record": recd, "worst": {"case": worst, "value": recd[worst], "where": wr.get("where", "")},
            "cases": cases, "results": common.results_of(rec), "assumptions": list(rec.get("assumptions", [])),
            "_lc": lc, "_rec": rec, "_design": design}


NOTES = [
    "何の試験か: 横転したときに乗員を守る枠 (ロールバー) を、横・後ろ・上から押す静的な試験 (OECD Code 4 を借りる)。横と後ろは"
    "決まったエネルギー (力 × たわみの面積) を吸うまで押し、上は決まった力で押す。押したあとのロールバーが乗員の空間に入らないか "
    "(すき間が 0 mm 以上か) を見る。",
    "ヒンジを一つずつ (event to event): 部材の端の曲げやねじりが、その断面の全塑性の値に届くと、そこに塑性ヒンジ (曲がっても力が"
    "増えない関節) が入る。次のヒンジが入るまでは骨組の硬さが変わらないので、たわみは力に比例して増える。だから各ヒンジの瞬間の形と、"
    "そのあいだの直線が、この計算そのもの (補間による近似ではない)。ヒンジが揃って骨組が動けるようになる (機構) と、力はそのまま、"
    "その形の向きにたわみだけが増える。",
    "ここで描く形は、実行で書かれたツールの関数をそのまま呼び直したもの (ツールのファイルは変えない)。記録には変位がないので、"
    "各ヒンジの瞬間の形は、同じ関数をその瞬間の力で止めて求めた。呼び直したすき間は記録の値と 0.01 mm 以内で一致する。",
    "前提 (ツールの前提から): 骨組全体を 3 次元の梁にし、支持点 (車輪の取付) の節点で固定する (サスペンションは入れない)。"
    "ヒンジは部材の端で、各軸の曲げは全塑性モーメント Z_p f_y、ねじりは薄肉の全塑性トルク。硬化なし、変形は小さいとする。"
    "長方形と丸の管のほかの断面は弾性のまま。",
    "乗員の空間: 乗員の箱を、AIST の肩幅と座高 (男性 95 パーセンタイル) まで広げた直方体。部材は管の太さのカプセルとして、"
    "その直方体とのすき間を測る (上の横棒は曲がった線で測る)。負の値はその深さまで入っている。",
    "変形は実寸 (×1)。倍率を変えると形だけが大きくなり、すき間の数字は実寸の値のまま。",
]


def build(args) -> dict:
    run = Path(args.run)
    events = read_events(run)
    mod = load_tool(tool_file(run, args.tool_file))
    vs = ([int(x) for x in args.versions.split(",")] if args.versions else
          sorted({e["data"]["design_version"] for e in events if e["kind"] == "analysis.finished"
                  and e["data"].get("tool") == TOOL and e["data"].get("status") == "ok"}))
    vers = [version_data(mod, events, v) for v in vs]
    # the version shown first (and whose design fills the shared keys): the last one that fails (NG), else the last
    ng = [x for x in vers if x["worst"]["value"] < float(x["_lc"]["criterion"]["limit"]["value"])]
    first = (ng or vers)[-1]
    design, lc, rec = first["_design"], first["_lc"], first["_rec"]
    crit = lc["criterion"]
    ok = lambda v: {"<": v < lim, "<=": v <= lim, ">": v > lim, ">=": v >= lim}[crit["op"]]
    lim = float(crit["limit"]["value"])
    for x in vers:
        x["ok"] = ok(x["worst"]["value"])
        x["case_ok"] = {k: ok(v) for k, v in x["record"].items()}
        for k in ("_design", "_lc", "_rec"):
            x.pop(k)
    obj = {
        "field": "rops", "title": names.title("rops", "ロールバーを押して乗員の空間とのすき間を見る", "ROPS"),
        "source": {"run": run.resolve().name, "version": f"v{first['v']:03d}", "analysis_id": first["analysis_id"],
                   "tool": TOOL, "load_case_id": first["load_case_id"], "design_name": first["design_name"],
                   "design_version": first["v"], "design_key": first["design_key"]},
        "frame": first["frame"], "parts": first["parts"], "scene": [], "ground": first["ground"], "vectors": [],
        "results": first["results"], "assumptions": first["assumptions"],
        "inputs": common.case_inputs(lc, rec["load_case_id"]) + [
            {"label": "ツール", "value": f"{TOOL} (実行で書かれたもの: {Path(tool_file(run, args.tool_file)).name})",
             "source": "記録の tool、実行の tools/"},
            {"label": "版", "value": ", ".join(f"v{x['v']} ({x['analysis_id']})" for x in vers),
             "source": "events.jsonl の design.created と analysis.finished"}],
        "notes": NOTES, "ledger": first["ledger"], "tyre": None,
        "criterion": {"quantity": lc["criterion"]["quantity"], "op": lc["criterion"]["op"],
                      "limit": float(lc["criterion"]["limit"]["value"]), "unit": lc["criterion"]["limit"].get("unit", "")},
        "cases_ja": CASES_JA, "versions": vers, "default_v": first["v"], "check_mm": CHECK_MM,
    }
    return obj


def main(argv=None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run")
    ap.add_argument("--versions", default=None, help="design versions, e.g. 3,5,6 (default: every version with a record)")
    ap.add_argument("--tool-file", default=None)
    ap.add_argument("--out", default=str(common.DEFAULT_OUT))
    args = ap.parse_args(argv)
    obj = build(args)
    p = common.write_data("rops", obj, Path(args.out))
    print(f"wrote {p} ({p.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()

"""Assembly (H1/H3/H6/H7) data for the viewer: python -m viewer.src.assembly <run dir> [--out viewer/data]

Field keys added to the common object (see common.py):
    steps [{order, part, pi (index in parts or -1), dir, path [[x, y, z], ...], off [dx, dy, dz],
            status, text, note, depends_on, fastenings [{to, kind, at, gap}], gap_min}]
        sorted by order. status: ok | blocked (no_interference false) | no_tool (tool_clearance false)
        | no_reach (hand_reach false), the first that applies. off = part box centre - last path point
        (the page moves the box along path + off, so it ends where the design puts it).
    interferences [{a, b, depth, state, condition, ends [{kind: part|member|other, index}, ...]}]
    checks [{tool, load_case_id, analysis_id, results [{q, v, u, where}]}]
        entry_opening and spray_reach records of the same version (numbers only)
"""

from __future__ import annotations

import math
import sys

from viewer.src import common

STATUS_ORDER = (("no_interference", "blocked"), ("tool_clearance", "no_tool"), ("hand_reach", "no_reach"))


def _end(name: str, parts: dict, members: dict) -> dict:
    s = str(name)
    if s.startswith("member "):
        mid = s[len("member "):]
        return {"kind": "member", "index": members.get(mid, -1), "id": mid}
    if s.startswith("part "):
        s = s[len("part "):]
    if s in parts:
        return {"kind": "part", "index": parts[s], "id": s}
    if s in members:
        return {"kind": "member", "index": members[s], "id": s}
    return {"kind": "other", "index": -1, "id": s}


def build(args) -> dict:
    vdir, rec = common.find_analysis(args.run, "assembly", load_case=getattr(args, "load_case", None) or "H1-interference",
                                     version=args.version, analysis_id=args.analysis)
    design = common.read_json(vdir / "design.json")
    obj = common.base_object("assembly", "組み立て: 手順・経路・干渉", args.run, vdir, rec, design)
    pidx = {p["id"]: i for i, p in enumerate(obj["parts"])}
    midx = {m: i for i, m in enumerate(obj["frame"]["member_ids"])}
    steps, offs = [], []
    for s in sorted(rec.get("assembly", []) or [], key=lambda s: s.get("order", 0)):
        pi = pidx.get(s["part"], -1)
        path = [[float(c) for c in p] for p in s.get("path_mm", []) or []]
        off = [0.0, 0.0, 0.0]
        if pi >= 0 and path:
            off = [obj["parts"][pi]["c"][a] - path[-1][a] for a in range(3)]
            offs.append(math.sqrt(sum(o * o for o in off)))
        status = next((name for key, name in STATUS_ORDER if s.get(key) is False), "ok")
        fast = [{"to": f.get("to", ""), "kind": f.get("kind", ""), "at": f.get("at_mm"), "gap": f.get("tool_gap_mm")}
                for f in s.get("fastenings", []) or []]
        gaps = [f["gap"] for f in fast if f["gap"] is not None]
        steps.append({"order": s.get("order"), "part": s["part"], "pi": pi, "dir": s.get("direction", ""), "path": path,
                      "off": off, "status": status, "text": s.get("text", ""), "note": s.get("note", ""),
                      "depends_on": s.get("depends_on", []), "fastenings": fast, "gap_min": min(gaps) if gaps else None})
    obj["steps"] = steps
    obj["interferences"] = [
        {"a": str(i.get("a")), "b": str(i.get("b")), "depth": float(i.get("depth_mm", 0.0)), "state": i.get("state", ""),
         "condition": i.get("condition", ""), "ends": [_end(i.get("a"), pidx, midx), _end(i.get("b"), pidx, midx)]}
        for i in rec.get("interferences", []) or []]
    checks = []
    for a in common.analyses(vdir):
        if a.get("tool") in ("entry_opening", "spray_reach") and a.get("status") == "ok":
            checks.append({"tool": a["tool"], "load_case_id": a.get("load_case_id", ""), "analysis_id": a.get("analysis_id", ""),
                           "results": common.results_of(a)})
    obj["checks"] = checks
    lc = common.load_case_of(vdir, rec["load_case_id"])
    counts = {k: sum(1 for s in steps if s["status"] == k) for k in ("ok", "blocked", "no_tool", "no_reach")}
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"]) + [
        {"label": "sequence", "value": f"{len(steps)} steps: " + ", ".join(f"{v} {k}" for k, v in counts.items()),
         "source": "record assembly list (order, direction, path_mm, fastenings, flags)"},
        {"label": "parts", "value": f"{len(obj['parts'])} part boxes, {sum(1 for s in steps if s['pi'] >= 0)} of them in the sequence",
         "source": "design.json parts (box centre, size, rpy_deg)"},
        {"label": "interferences", "value": f"{len(obj['interferences'])} pairs in the record",
         "source": "record interferences (a, b, depth_mm, state, condition)"},
    ]
    obj["notes"].append("部品は記録の経路 (直線の折れ線) に沿って順番に動かす。動きは実寸 (倍率なし)。")
    if offs:
        obj["notes"].append(f"Box centre minus the last path point: largest {max(offs):.1f} mm over {len(offs)} steps (the page adds it to the path).")
    obj["ledger"] = common.parts_ledger("assembly", design, lc, rec, obj["parts"])
    return obj


def extra(ap):
    ap.add_argument("--load-case", dest="load_case", help="load case id prefix of the assembly record (default H1-interference)")


if __name__ == "__main__":
    common.main("assembly", build, extra, argv=sys.argv[1:])

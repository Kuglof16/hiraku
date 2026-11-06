"""Crash (C1) data for the viewer: python -m viewer.src.crash <run dir> [--out viewer/data]

Field keys added to the common object (see common.py):
    stress {label, unit, per_member (list in frame.member_ids order, null where the record has none), max}
        the record's per_member stress, at the peak deceleration only
    disp {label, unit, per_node (packed f32, n x 3, frame.node_ids order), max}
        the record's per_node displacement, at the peak deceleration only
    t (packed f32), decel, crush, wall_force (packed f32, same length): the record's series
    a_max: the largest deceleration in the series (the instant of the stored fields)
    x_front_mm: the vehicle's front-most x (frame nodes and part boxes), where it meets the wall
The page scales the stored fields by a(t) / a_max (the tool solves the frame once, linearly, at the
peak; that scaling is done on the page and said on screen) and moves the whole vehicle by crush(t).
"""

from __future__ import annotations

import math
import sys

from viewer.src import common


def build(args) -> dict:
    vdir, rec = common.find_analysis(args.run, "crash", version=args.version, analysis_id=args.analysis)
    design = common.read_json(vdir / "design.json")
    obj = common.base_object("crash", "Crash: frontal wall impact", args.run, vdir, rec, design)
    fr = obj["frame"]
    st = next((f for f in rec.get("fields", []) if f.get("kind") == "stress" and "per_member" in f), None)
    dp = next((f for f in rec.get("fields", []) if f.get("kind") == "displacement" and "per_node" in f), None)
    if st is None or dp is None:
        raise LookupError(f"{rec['_path']}: needs a per_member stress field and a per_node displacement field")
    pm = [st["per_member"].get(m) for m in fr["member_ids"]]
    obj["stress"] = {"label": st.get("label", "stress"), "unit": st.get("unit", "MPa"), "per_member": pm,
                     "max": max((v for v in pm if v is not None), default=0.0)}
    flat, dmax = [], 0.0
    for nid in fr["node_ids"]:
        v = dp["per_node"].get(nid, [0.0, 0.0, 0.0])
        flat.extend(float(c) for c in v)
        dmax = max(dmax, math.sqrt(sum(float(c) ** 2 for c in v)))
    obj["disp"] = {"label": dp.get("label", "displacement"), "unit": dp.get("unit", "mm"),
                   "per_node": common.pack_f32(flat, [len(fr["node_ids"]), 3]), "max": dmax}
    chans = {}
    for name in ("deceleration", "crush", "wall_force"):
        s = common.series(rec, name)
        if s is None:
            raise LookupError(f"{rec['_path']}: no series {name}")
        chans[name] = s
    t = chans["deceleration"]["t_s"]
    obj["t"] = common.pack_f32(t)
    obj["decel"] = common.pack_f32(chans["deceleration"]["values"])
    obj["crush"] = common.pack_f32(chans["crush"]["values"])
    obj["wall_force"] = common.pack_f32(chans["wall_force"]["values"])
    obj["units"] = {k: chans[k]["unit"] for k in chans}
    obj["a_max"] = max(chans["deceleration"]["values"])
    xs = [n["xyz_mm"][0] for n in design.get("nodes", [])]
    xs += [p["c"][0] + p["s"][0] / 2.0 for p in obj["parts"]]
    obj["x_front_mm"] = max(xs)
    yld = sorted({float(m.get("yield_MPa")) for m in design.get("materials", {}).values() if m.get("yield_MPa")})
    obj["marks"] = [{"v": y, "label": f"yield {y:g}"} for y in yld[:2]]
    lc = common.load_case_of(vdir, rec["load_case_id"])
    if lc is None and getattr(args, "speed", None):
        lc = None
    wall = next((s for s in obj["scene"] if s["kind"] == "wall"), None)
    mass = common.result(rec, "vehicle_mass")
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"]) + [
        {"label": "wall", "value": (f"rigid wall, centre ({', '.join(f'{v:g}' for v in wall['c'])}) mm, size "
                                     f"{' x '.join(f'{v:g}' for v in wall['s'])} mm") if wall else "no wall in the record's scene",
         "source": "record scene (the tool's scene.wall)"},
        {"label": "mass", "value": f"{mass:g} kg" if mass is not None else "not in the record",
         "source": "record result vehicle_mass (design as loaded, the case's loading)"},
        {"label": "peak deceleration", "value": f"{obj['a_max']:.2f} {obj['units']['deceleration']}",
         "source": "the series' largest value; the frame was solved at this instant"},
    ]
    obj["not_computed"] = [
        "The crush is a lumped number (1-D mass and spring), shown as a translation of the whole vehicle into the wall, not a crushed shape.",
        "Stress and displacement were solved once, at the peak deceleration; at other times they are the stored values x a(t) / a_max (linear frame).",
        "The rebound after the standstill is not followed; wall loads at the front nodes are the tool's share of the wall force.",
    ]
    obj["notes"] = ["Frame displacement is the deflection with the wheel-centre rigid motion removed, multiplied by the scale; "
                    "the translation by crush(t) is real size."]
    return obj


if __name__ == "__main__":
    common.main("crash", build, argv=sys.argv[1:])

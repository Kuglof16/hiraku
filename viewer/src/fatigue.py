"""Fatigue (R3, strength reliability) data for the viewer: python -m viewer.src.fatigue <run dir>

Reads the newest fatigue record:
    damage {label, unit, per_member (frame.member_ids order, null where none), max, min_pos, worst}
    criterion {quantity, op, limit, value, ok} | null; life_km (record result fatigue_life)
    stress {member, unit, t (packed), values (packed)}: the worst point's stress over the simulated road
    wheels [{support, t (packed), values (packed), c (support position mm), r (radius mm)}]: wheel_load.<support>
    sn {x (cycles to failure), values (MPa)}; ranges {member, x (cycles reaching at least that range), values (MPa)}
    road {label, x (packed, mm), tracks [{label, y_mm, z (packed, mm)}], ground_z_mm, x_front_mm, speed_mm_s} | null:
        the scene's road_profile; on the page road point x is drawn at vehicle x = x_front + x - v t (as
        shelf.fatigue.road_wheel_forces meets it), so it passes under the wheels at the case's speed
    bands [{name, value, share}]: the record's damage per stress-range band at the worst point
"""

from __future__ import annotations

import re
import sys

from viewer.src import common, ledgers


def _ok(op: str, v: float, lim: float) -> bool:
    return {"<": v < lim, "<=": v <= lim, ">": v > lim, ">=": v >= lim}[op]


def build(args) -> dict:
    vdir, rec = common.find_analysis(args.run, "fatigue", version=args.version, analysis_id=args.analysis)
    design = common.read_json(vdir / "design.json")
    lc = common.load_case_of(vdir, rec["load_case_id"])
    obj = common.base_object("fatigue", "疲労: 悪路を走ったときの溶接の損傷 (マイナー則)", args.run, vdir, rec, design)
    fld = next((f for f in rec.get("fields", []) or [] if f.get("kind") == "damage" and "per_member" in f), None)
    if fld is None:
        raise LookupError(f"{rec['_path']}: no per_member damage field")
    pm = [fld["per_member"].get(m) for m in obj["frame"]["member_ids"]]
    vals = [v for v in pm if v is not None]
    worst = next((r.get("where") for r in rec.get("results", []) if r["quantity"] == "fatigue_damage"), None)
    obj["damage"] = {"label": fld.get("label", "damage"), "unit": fld.get("unit", "1"), "per_member": pm,
                     "max": max(vals, default=0.0), "min_pos": min((v for v in vals if v > 0), default=1e-12),
                     "worst": worst}
    crit = (lc or {}).get("criterion")
    D = common.result(rec, "fatigue_damage")
    obj["criterion"] = ({"quantity": crit["quantity"], "op": crit["op"], "limit": float(crit["limit"]["value"]),
                         "value": D, "ok": _ok(crit["op"], D, float(crit["limit"]["value"])) if D is not None else None,
                         "basis": crit.get("basis", "")} if crit and crit.get("quantity") == "fatigue_damage" else None)
    obj["life_km"] = common.result(rec, "fatigue_life")
    ser = rec.get("series", []) or []
    st = next((s for s in ser if s["channel"].startswith("stress.")), None)
    obj["stress"] = ({"member": st["channel"][len("stress."):], "unit": st.get("unit", "MPa"), "t": common.pack_f32(st["t_s"]),
                      "values": common.pack_f32(st["values"])} if st else None)
    sup = {s["id"]: s for s in design.get("supports", [])}
    obj["wheels"] = []
    for s in ser:
        if s["channel"].startswith("wheel_load."):
            sid = s["channel"][len("wheel_load."):]
            sp = sup.get(sid, {})
            obj["wheels"].append({"support": sid, "unit": s.get("unit", "N"), "t": common.pack_f32(s["t_s"]),
                                  "values": common.pack_f32(s["values"]),
                                  "c": [float(v) for v in sp.get("position_mm", [0, 0, 0])],
                                  "r": float(sp.get("radius_mm", 0.0)), "width": float(sp.get("width_mm", 150.0) or 150.0)})
    sn = next((s for s in ser if s["channel"] == "sn_curve"), None)
    obj["sn"] = {"x": sn["x"]["values"], "values": sn["values"], "unit": sn.get("unit", "MPa")} if sn else None
    rg = next((s for s in ser if s["channel"].startswith("stress_ranges.")), None)
    obj["ranges"] = ({"member": rg["channel"][len("stress_ranges."):], "x": rg["x"]["values"], "values": rg["values"]}
                     if rg else None)
    road = next((s for s in rec.get("scene", []) or [] if s.get("kind") == "road_profile"), None)
    if road:
        prof = road["profile"]
        ground = min((s["position_mm"][2] - float(s.get("radius_mm", 0.0)) for s in design.get("supports", [])), default=0.0)
        tracks = []
        for label, z in prof["z_mm"].items():
            m = re.match(r"y\s+(-?[\d.]+)\s*mm", label)
            tracks.append({"label": label, "y_mm": float(m.group(1)) if m else 0.0, "z": common.pack_f32(z)})
        v = (((lc or {}).get("params") or {}).get("speed") or {}).get("value")
        # road_wheel_forces: the front wheels meet road x = v t, a wheel lag (x_front - x_wheel) behind it later, so the
        # road point x_road is under vehicle x = x_front + x_road - v t
        x_front = max((s["position_mm"][0] for s in design.get("supports", [])), default=0.0)
        obj["road"] = {"label": road.get("label", "road"), "x": common.pack_f32(prof["x_mm"]), "tracks": tracks,
                       "ground_z_mm": ground, "x_front_mm": x_front,
                       "speed_mm_s": float(v) / 3.6 * 1000.0 if v else None}
    else:
        obj["road"] = None
    obj["bands"] = [{"name": e["name"], "value": e["value"], "share": e.get("share")} for e in rec.get("effects", []) or []
                    if e["name"].startswith("stress ranges")]
    src = next((e["name"] for e in rec.get("effects", []) or [] if e["name"].startswith("loads:")), "")
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"]) + [
        {"label": "loads", "value": src.replace("loads: ", ""), "source": "record effects"},
        {"label": "sample", "value": f"{common.result(rec, 'sample_length') or 0:g} m simulated, scaled to the distance",
         "source": "record result sample_length"},
    ] + [{"label": "S-N", "value": a, "source": "record assumptions"} for a in rec.get("assumptions", []) or []]
    obj["notes"] = [
        "色は部材ごとのマイナー損傷 (部材の溶接点の中で一番大きい値、対数目盛)。1 で寿命。",
        "応力は輪の力 → フレームの影響係数 (単位の力ごとに K1 で解いた応力) で求めたもの。フレームの変形は描かない。",
        "路面は記録の road_profile。時間を進めると、場合の速さで車の下を流れる。",
    ]
    obj["ledger"] = ledgers.k1_ledger("fatigue", design, lc, rec, obj["parts"])
    return obj


if __name__ == "__main__":
    common.main("fatigue", build, argv=sys.argv[1:])

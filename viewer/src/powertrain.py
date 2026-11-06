"""Powertrain data for the viewer: python -m viewer.src.powertrain <run dir> [--also <run dir> ...] [--out viewer/data]

Each run dir gives its newest record of launch (P3), step_climb_dynamic (P1) or drive_energy (P4).
launch / step_climb_dynamic: the K2 replay (handling.k2_case) plus, per wheel,
    drive {torque (packed nT x nw, N m: the record's wheel_torque, the driven wheels' total, shared equally by the
           driven wheels), slip (packed nT x nw: (omega r - v) / max(|v|, 0.5 m/s), omega from the record's
           wheel_spin by central differences, r the wheel's radius, v the body's speed), driven [corner ids]}
drive_energy (K3, no motion): kind "energy", series {t, speed, grade, power (battery), current, soc} (packed),
    battery {part (the record's per_part field), used_pct}, the parts standing still, the battery coloured by the
    share of its capacity used up to t (soc(0) - soc(t)).
"""

from __future__ import annotations

import math
import sys

from viewer.src import common
from viewer.src.handling import extra, find_any, k2_case

TOOLS = ("launch", "step_climb_dynamic", "drive_energy")


def drive_of(rec: dict, motion: dict) -> dict:
    ids = [c["id"] for c in motion["corners"]]
    driven = [c["id"] for c in motion["corners"] if c["drive"]] or ids
    t = common.unpack_f32(motion["t"])
    tq = (common.series(rec, "wheel_torque") or {}).get("values") or [0.0] * len(t)
    if len(tq) != len(t):
        tq = [0.0] * len(t)
    v = [x / 3.6 for x in (common.series(rec, "speed") or {"values": [0.0] * len(t)})["values"]]
    torque, slip = [], []
    spins = {i: (common.series(rec, f"wheel_spin.{i}") or {"values": [0.0] * len(t)})["values"] for i in ids}
    r = {c["id"]: c["r"] / 1000.0 for c in motion["corners"]}
    for k in range(len(t)):
        a, b = max(0, k - 1), min(len(t) - 1, k + 1)
        for i in ids:
            torque.append(tq[k] / len(driven) if i in driven else 0.0)
            om = math.radians(spins[i][b] - spins[i][a]) / (t[b] - t[a]) if t[b] > t[a] else 0.0
            slip.append((om * r[i] - v[k]) / max(abs(v[k]), 0.5))
    return {"torque": common.pack_f32(torque, [len(t), len(ids)]), "slip": common.pack_f32(slip, [len(t), len(ids)]),
            "driven": driven}


def energy_case(run, vdir, rec, design) -> dict:
    lc = common.load_case_of(vdir, rec["load_case_id"])
    obj = common.base_object("powertrain", f"周回の電力 {rec['load_case_id'].split('-')[0]}: {(lc or {}).get('title', '')}",
                             run, vdir, rec, design)
    obj["tool"] = rec["tool"]
    obj["kind"] = "energy"
    obj["ledger"] = common.parts_ledger("drive_energy", design, lc, rec, obj["parts"])
    ser = {}
    for key, ch in (("speed", "speed"), ("grade", "grade"), ("power", "power.battery"), ("current", "current.battery"), ("soc", "soc")):
        s = common.series(rec, ch)
        if s is None:
            raise LookupError(f"drive_energy record without the series {ch}")
        ser[key] = common.pack_f32(s["values"])
        ser["t"] = common.pack_f32(s["t_s"])
        ser[key + "_unit"] = s.get("unit", "")
    obj["series"] = ser
    f = next((x for x in rec.get("fields", []) or [] if x.get("per_part")), None)
    part = next(iter(f["per_part"])) if f else None
    obj["battery"] = {"part": part, "index": next((i for i, p in enumerate(obj["parts"]) if p["id"] == part), -1),
                      "used_pct": f["per_part"][part] if f else None}
    obj["judge"] = common.judge(rec, lc)
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"])
    obj["notes"] = ["電力と熱の回路 (K3) の計算: 形と位置は使わない。車両は一つの質量として坂と速さの通りに走る。",
                    "電池の箱の色 = この時刻までに使った電池の容量の割合 (始めの残り − いまの残り)。"]
    return obj


def build(args) -> dict:
    cases = []
    for run in [args.run] + list(args.also or []):
        vdir, rec = find_any(run, TOOLS, args.version if run == args.run else None, args.analysis if run == args.run else None)
        design = common.read_json(vdir / "design.json")
        if rec["tool"] == "drive_energy":
            cases.append(energy_case(run, vdir, rec, design))
            continue
        c = k2_case("powertrain", run, vdir, rec, design)
        c["kind"] = "replay"
        c["drive"] = drive_of(rec, c["motion"])
        c["notes"].append("輪のトルク = 記録の駆動輪の合計トルク ÷ 駆動輪の数 (等しく分けたとき)。すべり = (ωr − v) / v、ω は記録の輪の回転角から求めた。")
        cases.append(c)
    return {**cases[0], "cases": cases}


if __name__ == "__main__":
    common.main("powertrain", build, extra=extra, argv=sys.argv[1:])

"""The surroundings each field draws under the vehicle, and how far the tyres are from them at t = 0.

The page (js/look.js HV.groundOf, js/vehicle.js, js/fatigue.js) draws the ground by the same rules as here:

- static fields (strength, NVH, thermal, assembly; crash and fatigue for the plane): the plane at `ground.z` (common.py
  ground_of: the design's supports, wheel centre z less radius); in data made before that key, the lowest of the
  record's own wheels (`wheels` c z less r: crash, NVH shell, fatigue), else of the wheel-kind part boxes (box centre
  z less half its height: the design's wheel boxes stand on their supports).
- the K2 replays (handling, powertrain, aero): K2's road is z = 0 (k2_vehicle: the road plane), with the record's scene
  on it: a step's top inside its box, the tilt table's top (c z + s z / 2), a `ground` item's z, a road profile's
  track height under the wheel (left track for y >= 0, right for y < 0, linear in x).
- fatigue: the road profile passing under the tyres at t = 0 (road point x = x_wheel - x_front), on `ground_z_mm`.

`contact_gaps(obj)` gives, per drawn tyre, its lowest point (centre z less radius) less the surface z under it; for the
K2 replays also the record's own contact point z less the surface (the tyre's deflection is r - (centre z - surface)).

Run: python -m viewer.src.ground [data dir]  (prints the largest gap per field of viewer/data)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from viewer.src import common

Record = dict[str, Any]
WHEEL_KINDS = ("wheel",)


def static_ground(obj: Record) -> tuple[float | None, str]:
    """The plane's z and where it comes from (the rules above)."""
    g = obj.get("ground")
    if g and g.get("z") is not None:
        return float(g["z"]), "supports"
    ws = [w for w in obj.get("wheels") or [] if isinstance(w, dict) and w.get("c") and w.get("r")]
    if ws:
        return min(float(w["c"][2]) - float(w["r"]) for w in ws), "wheels"
    ps = [p for p in obj.get("parts") or [] if str(p.get("kind", "")).lower() in WHEEL_KINDS]
    if ps:
        return min(float(p["c"][2]) - float(p["s"][2]) / 2.0 for p in ps), "wheel_parts"
    wall = next((s for s in obj.get("scene") or [] if s.get("kind") == "wall"), None)
    if wall:   # a crash record's wall stands on the ground: its foot
        return float(wall["c"][2]) - float(wall["s"][2]) / 2.0, "wall"
    return None, "none"


def _lerp(xs, zs, x: float) -> float:
    if x <= xs[0]:
        return float(zs[0])
    for i in range(1, len(xs)):
        if x <= xs[i]:
            f = (x - xs[i - 1]) / ((xs[i] - xs[i - 1]) or 1.0)
            return float(zs[i - 1] + f * (zs[i] - zs[i - 1]))
    return float(zs[-1])


def k2_surface(scene: list[Record], x: float, y: float) -> float:
    """The road's z under (x, y) in a K2 replay at the scene's rest (angle 0)."""
    z = 0.0
    for s in scene or []:
        k, c, sz = s.get("kind"), s.get("c", [0, 0, 0]), s.get("s", [0, 0, 0])
        inside = abs(x - c[0]) <= sz[0] / 2.0 and abs(y - c[1]) <= sz[1] / 2.0
        if k == "step" and inside:
            z = max(z, c[2] + sz[2] / 2.0)
        elif k == "tilt_table" and inside:
            z = c[2] + sz[2] / 2.0
        elif k == "ground" and inside:
            z = c[2]
        elif k == "road_profile" and s.get("profile"):
            p = s["profile"]
            xs = common.unpack_f32(p["x"])
            zs = common.unpack_f32(p["left"] if y >= 0 else p["right"])
            z = _lerp(xs, zs, x)
    return z


def k2_distance(scene: list[Record], cx: float, cy: float, cz: float) -> float:
    """The distance (mm) from a wheel centre to the drawn road in its wheel plane: the level under it, or a step's
    edge or face near it (k2_vehicle.road.BlockRoad.contacts: the corner at the step's x, its height)."""
    d = cz - k2_surface(scene, cx, cy)
    for s in scene or []:
        if s.get("kind") != "step":
            continue
        c, sz = s["c"], s["s"]
        if abs(cy - c[1]) > sz[1] / 2.0:
            continue
        top = c[2] + sz[2] / 2.0
        for qx in (c[0] - sz[0] / 2.0, c[0] + sz[0] / 2.0):
            qz = min(cz, top)
            d = min(d, ((cx - qx) ** 2 + (cz - qz) ** 2) ** 0.5)
    return d


def _k2_case(c: Record) -> list[Record]:
    """Per tyre at t = 0: how deep the tyre's circle goes into the drawn road (r less the distance from its centre),
    against the tyre's own deflection under the record's wheel load (wheel_load / K2's tyre stiffness): their
    difference is the gap; the record's contact point (the force-weighted mean of the tyre's contacts) is kept."""
    M = c.get("motion")
    if not M:
        return []
    from hiraku.solvers.k2_vehicle.model import DEFAULTS
    k = DEFAULTS["tyre_stiffness"][0] / 1000.0   # N/mm (the design of these runs has no tyre_stiffness)
    W = common.unpack_f32(M["wheels"])
    nc = len(M["channels"])
    ch = {n: i for i, n in enumerate(M["channels"])}
    out = []
    for i, cn in enumerate(M["corners"]):
        b = i * nc
        wc = [W[b + ch["wheel_center_x"] + a] for a in range(3)]
        cp = [W[b + ch["contact_x"] + a] for a in range(3)]
        pen = cn["r"] - k2_distance(c.get("scene"), *wc)
        defl = W[b + ch["wheel_load"]] / k
        out.append({"id": cn["id"], "tyre_bottom_gap_mm": defl - pen, "penetration_mm": pen,
                    "deflection_mm": defl, "contact": cp})
    return out


def contact_gaps(obj: Record) -> list[Record]:
    """Per drawn tyre at t = 0: the gaps (mm) between the tyre and the surface drawn under it."""
    f = obj.get("field")
    if obj.get("cases"):
        return [dict(g, case=c.get("source", {}).get("load_case_id", "")) for c in obj["cases"] for g in _k2_case(c)]
    if obj.get("motion") and obj["motion"].get("corners"):
        return _k2_case(obj)
    z, _ = static_ground(obj)
    if z is None:
        return []
    if f == "fatigue" and obj.get("road"):
        R = obj["road"]
        xs = common.unpack_f32(R["x"])
        out = []
        for w in obj.get("wheels") or []:
            tr = min(R["tracks"], key=lambda t: abs(t["y_mm"] - w["c"][1]))
            road = R["ground_z_mm"] + _lerp(xs, common.unpack_f32(tr["z"]), w["c"][0] - R["x_front_mm"])
            out.append({"id": w["support"], "tyre_bottom_gap_mm": w["c"][2] - w["r"] - road})
        return out
    if f == "crash" and obj.get("motion", {}).get("pos") and obj.get("wheels"):
        P = common.unpack_f32(obj["motion"]["pos"])
        return [{"id": w["id"], "tyre_bottom_gap_mm": P[3 * w["node"] + 2] - w["r"] - z} for w in obj["wheels"]]
    ws = [w for w in obj.get("wheels") or [] if isinstance(w, dict) and w.get("c") and w.get("r")]
    if ws:
        return [{"id": w.get("id", w.get("support", "")), "tyre_bottom_gap_mm": w["c"][2] - w["r"] - z} for w in ws]
    if (obj.get("ground") or {}).get("wheels"):
        return [{"id": w["id"], "tyre_bottom_gap_mm": w["contact_z"] - z} for w in obj["ground"]["wheels"]]
    return [{"id": p["id"], "tyre_bottom_gap_mm": p["c"][2] - p["s"][2] / 2.0 - z}
            for p in obj.get("parts") or [] if str(p.get("kind", "")).lower() in WHEEL_KINDS]


def largest(gaps: list[Record]) -> tuple[float, float]:
    """The largest |gap| and the largest tyre deflection (mm)."""
    a = max((abs(g["tyre_bottom_gap_mm"]) for g in gaps), default=0.0)
    b = max((g.get("deflection_mm", 0.0) for g in gaps), default=0.0)
    return a, b


if __name__ == "__main__":
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data"
    for p in sorted(d.glob("*.js")):
        field, o = common.read_data_js(p)
        gs = contact_gaps(o)
        a, b = largest(gs)
        print(f"{p.stem:11s} ground={static_ground(o)}  tyres={len(gs)}  largest gap {a:.2f} mm  "
              f"(largest tyre deflection {b:.2f} mm)")

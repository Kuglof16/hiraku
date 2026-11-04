"""The K0 inputs of the entry formulas (library_entry; test H7, proposed) read off a design: each side seat's span,
the side frame beside it (the members labelled part = side_guard by ladder_4w: posts straight up, bars along x),
and the floor and rails for the first step. The H7 builder's inputs_from.

The seat's span and heights are read as hiraku.solvers.k4_geometry.entry reads them (seat_spaces): the seat box's
rear face to the front of its person's parts, the seat box's top. The side frame alone is used here: the posts' x
and outer diameter, the bars' centre heights; a net of kind side_net that does not open (k4_geometry.entry.
openable_parts) between the bars is counted as one more fixed thing. Nothing else (brackets, other parts) is seen.
"""

from __future__ import annotations

import numpy as np

from . import units
from .vehicle import DesignError, node_table

GUARD_PART = "side_guard"  # the label ladder_4w gives the side frame's members


def _q(design: dict, name: str, unit: str) -> float:
    v = design.get("variables", {}).get(name)
    if v is None:
        raise DesignError(f"the design has no variable {name!r}")
    return units.to_unit(v, unit, name)


def side_frame(design: dict, side: float) -> dict | None:
    """The side frame's posts {x: (z0, z1)}, bars [(z, x0, x1)] and tube diameter on one side (+1 left, -1 right),
    None when there is none."""
    nodes = node_table(design)
    secs = design.get("sections", {})
    posts, bars, ds = {}, [], set()
    for m in design.get("members", []):
        if m.get("part") != GUARD_PART:
            continue
        a, b = nodes[m["node_a"]], nodes[m["node_b"]]
        if np.sign(a[1]) != side or np.sign(b[1]) != side:
            continue
        d = secs[m["section"]].get("dims_mm", {}).get("d")
        if d is None:
            continue  # the outriggers (rectangular tubes at the rails' height)
        if abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6:  # a post (in pieces)
            z0, z1 = sorted((float(a[2]), float(b[2])))
            lo, hi = posts.get(float(a[0]), (z0, z1))
            posts[float(a[0])] = (min(lo, z0), max(hi, z1))
            ds.add(float(d))
        elif abs(a[2] - b[2]) < 1e-6 and abs(a[1] - b[1]) < 1e-6:
            bars.append((float(a[2]), *sorted((float(a[0]), float(b[0])))))
            ds.add(float(d))
    if not posts or not bars:
        return None
    return {"posts": posts, "bars": sorted(bars), "d": max(ds)}


def _spaces(design: dict):
    from ..k4_geometry import entry as geo

    boxes = geo.aabb(design)
    spaces = geo.seat_spaces(design, boxes, geo.openable_parts(design, boxes))
    if not spaces:
        raise DesignError("the design has no seat off its middle, so there is no side opening")
    return geo, boxes, spaces


def _frame_inputs(design: dict, sp, cap: float) -> dict:
    f = side_frame(design, sp.side)
    if f is None:  # nothing beside the seat: the whole span, nothing above
        return {"x_f": sp.x_front, "x_b": sp.x_back, "x_p": sp.x_back, "d": 0.0, "h_low": cap, "frame": None}
    ahead = [x for x in f["posts"] if x <= sp.x_front]
    x_p = max(ahead) if ahead else min(f["posts"])
    return {"x_f": sp.x_front, "x_b": sp.x_back, "x_p": x_p, "d": f["d"],
            "h_low": min(z for z, _, _ in f["bars"]) - sp.z_seat, "frame": f}


def opening_width(x_f: float, x_b: float, x_p: float, d: float) -> float:
    """entry.opening_width's W (the clear stretch ahead of the side frame's front post)."""
    return max(0.0, x_f - max(x_b, x_p + d / 2.0))


def entry_width_inputs(design: dict, load_case: dict) -> dict:
    """x_f, x_b, x_p, d of the seat whose clear width is the least (k4_geometry.entry for the seat's span)."""
    _, _, spaces = _spaces(design)
    cap = _cap(load_case)
    rows = [(_frame_inputs(design, sp, cap), sp) for sp in spaces]
    got, sp = min(rows, key=lambda r: opening_width(r[0]["x_f"], r[0]["x_b"], r[0]["x_p"], r[0]["d"]))
    return {k: units.quantity(got[k], "mm") for k in ("x_f", "x_b", "x_p", "d")} | {"seat": sp.seat}


def _cap(load_case: dict) -> float:
    from ..k4_geometry.entry import HEIGHT_CAP_MM

    q = (load_case.get("params") or {}).get("height_cap")
    return units.to_unit(q, "mm", "height_cap") if q else HEIGHT_CAP_MM


def entry_height_inputs(design: dict, load_case: dict) -> dict:
    """entry_width_inputs with h_low (the lowest side bar's centre over the seat top; the cap without a side frame)
    and H_cap."""
    _, _, spaces = _spaces(design)
    cap = _cap(load_case)
    rows = [(_frame_inputs(design, sp, cap), sp) for sp in spaces]
    got, sp = min(rows, key=lambda r: opening_width(r[0]["x_f"], r[0]["x_b"], r[0]["x_p"], r[0]["d"]))
    return ({k: units.quantity(got[k], "mm") for k in ("x_f", "x_b", "x_p", "d", "h_low")}
            | {"H_cap": units.quantity(cap, "mm"), "seat": sp.seat})


def entry_step_inputs(design: dict, load_case: dict) -> dict:
    """z_rail, h_rail (the design's rail_height and rail_h), the floor's thickness t and its gap over the rails' top
    (from the floor part under a side seat), z_g the ground (wheel centre less radius, the lowest)."""
    _, boxes, spaces = _spaces(design)
    z_rail, h_rail = _q(design, "rail_height", "mm"), _q(design, "rail_h", "mm")
    sp = spaces[0]
    floors = [b for b in boxes.values() if b.kind == "body" and abs(b.hi[2] - sp.z_floor) < 1e-6]
    if not floors:
        raise DesignError("no floor part under the seat")
    fl = floors[0]
    wheels = [s for s in design.get("supports", []) if s.get("kind") == "wheel"]
    if not wheels:
        raise DesignError("no wheels: no ground under the tyres")
    z_g = min(float(s["position_mm"][2]) - float(s["radius_mm"]) for s in wheels)
    got = {"z_rail": z_rail, "h_rail": h_rail, "gap": float(fl.lo[2]) - (z_rail + h_rail / 2.0),
           "t": float(fl.hi[2] - fl.lo[2]), "z_g": z_g}
    return {k: units.quantity(v, "mm") for k, v in got.items()}


def entry_handhold_inputs(design: dict, load_case: dict) -> dict:
    """n_grip: round members (outside diameter grip_min to grip_max) on a side seat's side, outboard of its middle,
    meeting its span along x and reaching below its floor + grip_reach; the fewest over the side seats."""
    _, _, spaces = _spaces(design)
    p = load_case.get("params") or {}
    try:
        lo, hi, reach = (units.to_unit(p[k], "mm", k) for k in ("grip_min", "grip_max", "grip_reach"))
    except KeyError as e:
        raise DesignError(f"the load case has no {e.args[0]}") from None
    nodes = node_table(design)
    secs = design.get("sections", {})
    best = None
    for sp in spaces:
        n = 0
        for m in design.get("members", []):
            d = secs[m["section"]].get("dims_mm", {}).get("d")
            if d is None or not lo <= float(d) <= hi:
                continue
            a, b = nodes[m["node_a"]], nodes[m["node_b"]]
            r = float(d) / 2.0
            x0, x1 = min(a[0], b[0]) - r, max(a[0], b[0]) + r
            out = (max(a[1], b[1]) + r > sp.y_mid) if sp.side > 0 else (min(a[1], b[1]) - r < sp.y_mid)
            if out and x1 > sp.x_back and x0 < sp.x_front and min(a[2], b[2]) - r < sp.z_floor + reach:
                n += 1
        if best is None or n < best["n_grip"]:
            best = {"n_grip": float(n), "seat": sp.seat}
    best["n_grip"] = units.quantity(best["n_grip"], "1")
    return best


def entry_blocked_inputs(design: dict, load_case: dict) -> dict:
    """n_frame, n_fixed: the side frame's posts and bars, and the nets that do not open, in the doorway placed at
    x0 = max(x_b, x_p + d / 2) kept within [x_b, x_f - width_upper]; the most over the side seats."""
    geo, boxes, spaces = _spaces(design)
    p = load_case.get("params") or {}
    try:
        w_up, w_low, h_s, h_f = (units.to_unit(p[k], "mm", k) for k in ("width_upper", "width_lower", "height_seat",
                                                                           "height_floor"))
    except KeyError as e:
        raise DesignError(f"the load case has no {e.args[0]}") from None
    opens = geo.openable_parts(design, boxes)
    best = None
    for sp in spaces:
        r = _frame_inputs(design, sp, geo.HEIGHT_CAP_MM)
        x0 = min(max(r["x_b"], r["x_p"] + r["d"] / 2.0), max(r["x_b"], r["x_f"] - w_up))
        top = max(sp.z_seat + h_s, sp.z_floor + h_f)
        up = (x0, x0 + w_up, sp.z_seat, top)
        low = (x0 + w_up - w_low, x0 + w_up, sp.z_floor, sp.z_seat)

        def hits(x0_, x1_, z0_, z1_, box) -> bool:  # box (x0, x1, z0, z1) into the template box by more than a touch
            t = geo.TOUCH_MM
            return box[1] > x0_ + t and box[0] < x1_ - t and box[3] > z0_ + t and box[2] < z1_ - t

        n_frame = 0
        f = r["frame"]
        if f is not None:
            h = f["d"] / 2.0
            things = [(x - h, x + h, z0, z1) for x, (z0, z1) in f["posts"].items()]
            things += [(x0b - h, x1b + h, z - h, z + h) for z, x0b, x1b in f["bars"]]
            n_frame = sum(1 for t in things if hits(*up, t) or hits(*low, t))
        n_fixed = 0
        for pid, b in ((k.split(":", 1)[1], v) for k, v in boxes.items() if k.startswith("part:")):
            if b.kind in geo.NET_KINDS and pid not in opens and \
                    ((b.hi[1] > sp.y_out + geo.TOUCH_MM) if sp.side > 0 else (b.lo[1] < sp.y_out - geo.TOUCH_MM)):
                t = (float(b.lo[0]), float(b.hi[0]), float(b.lo[2]), float(b.hi[2]))
                n_fixed += int(hits(*up, t) or hits(*low, t))
        if best is None or n_frame + n_fixed > best["n_frame"] + best["n_fixed"]:
            best = {"n_frame": float(n_frame), "n_fixed": float(n_fixed), "seat": sp.seat, "x0": x0}
    return best | {k: units.quantity(best[k], "1") for k in ("n_frame", "n_fixed")}

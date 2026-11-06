"""Crash (C1) data for the viewer: python -m viewer.src.crash <run dir> [--tool crash|crash_beam] [--out viewer/data]

Reads either record kind (by default crash_beam when the run has one, else crash):

model "A" (tool crash: 1-D mass and spring, the frame solved once, linearly, at the peak):
    stress {label, unit, per_member (frame.member_ids order, null where none), max}   at the peak only
    disp {label, unit, per_node (packed f32, n x 3), max}                           at the peak only
    t, decel, crush, wall_force (packed f32, same length); a_max; units
    x_front_mm, wall_x_mm: the vehicle's front-most x and the wall's face. The page does not move the
    vehicle (A has no deformed shape); crush(t) is a plot only.

model "B" (tool crash_beam: co-rotational beams with plastic hinges, its node_motion field):
    frame: the run's mesh (node_motion nodes and elements; member_ids = each element's member id)
    motion {t (packed, nF), pos (packed f32, nF x n x 3, mm), broken [[member id, ...] per frame]}
    colour {strain (packed nF x ne, %, element axial strain from its length change),
            fold (packed nF x ne, deg, largest change of the angle to a neighbouring element at its ends),
            strain_absmax, fold_max}
    part_off (packed nF x nparts x 3): each drawn part box moved by the mean motion of its carried_by nodes
    wall_x_mm; beyond {nodes [per frame count], depth [per frame, mm], parts [...], part_depth [...],
                       nodes_any, node_depth_max, parts_any, part_depth_max, tol_mm}
    series {t, decel, crush} (packed) and units
    from the record's own results when it has them (crash_beam from K2 182f148f):
    colour.stress / colour.hinge (packed nF x ne): the member's axial force (series member_axial_force.<m>) / its
        section's area A from the design (MPa, + tension), and the larger of its end hinge rotations (series
        member_hinge.<m>.<end>, deg); colour.force {member: packed nF, N}, area_mm2, hinge_ends, stress_absmax, hinge_max
    stop {t_s, member, piece_pct, limit_pct, text}: the run stops when a piece breaks off the seats' side
        (result frame_split_time and its assumption)
    from K2 fcd3f2b8 (element contact): stop.goes_on (the run goes on past the split) and stop.ok (the piece under
        the limit); wheels [{id, node, r, width, kg, parts, mounts, mount_ids, c, t_wall_s, ...}] from the node_motion
        nodes '<wheel>~centre' on no element and the record's mass assumption (the parts on a wheel move with its
        node); summary.margin (occupant_space_margin judged by the load case's criterion), summary.after (the
        "after the split" band), crush_at_split, crumple_length, wall_free_after_s
    from K2 8a24cd15 (C1 judged with contact): summary.margin also member (the hot spot with the least room) and
        t_s (its instant); summary.split (frame_split_fraction judged by the C1-split load case when the run has it,
        the broken-off pieces the record lists); summary.crush_margin (crumple_length - max_crush, a plain value)
    hot_spots [{where, quantity, value, unit, limit, ok}] least first
    summary {t_end_s, peak_decel, mean_decel (the deceleration series' mean: speed lost / time), crush_end,
             crush_max, least_room (the first hot spot)}
Every number here is computed from the record by this script; nothing is added that the run did not give.
"""

from __future__ import annotations

import math
import re
import sys

from viewer.src import common

TOL_MM = 0.05  # node_motion positions are rounded to 0.1 mm: beyond the wall means more than half of that


# ---------------------------------------------------------------- shared
def _wall(obj) -> dict | None:
    return next((s for s in obj["scene"] if s["kind"] == "wall"), None)


def _wall_row(wall) -> dict:
    return {"label": "壁", "value": (f"rigid wall, face at x = {wall['c'][0] - wall['s'][0] / 2:g} mm, box centre "
                                       f"({', '.join(f'{v:g}' for v in wall['c'])}) mm, size "
                                       f"{' x '.join(f'{v:g}' for v in wall['s'])} mm") if wall else "no wall in the record's scene",
            "source": "record scene (the tool's scene.wall)"}


def _find(args):
    tool = getattr(args, "tool", None)
    if tool:
        return common.find_analysis(args.run, tool, version=args.version, analysis_id=args.analysis)
    try:
        return common.find_analysis(args.run, "crash_beam", version=args.version, analysis_id=args.analysis)
    except LookupError:
        return common.find_analysis(args.run, "crash", version=args.version, analysis_id=args.analysis)


def build(args) -> dict:
    vdir, rec = _find(args)
    design = common.read_json(vdir / "design.json")
    if rec["tool"] == "crash_beam":
        return build_b(args, vdir, rec, design)
    return build_a(args, vdir, rec, design)


# ---------------------------------------------------------------- A: crash (1-D)
def build_a(args, vdir, rec, design) -> dict:
    obj = common.base_object("crash", "衝突: 壁への正面衝突 (A: 1次元の質量とばね)", args.run, vdir, rec, design)
    obj["model"] = "A"
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
    obj["t"] = common.pack_f32(chans["deceleration"]["t_s"])
    obj["decel"] = common.pack_f32(chans["deceleration"]["values"])
    obj["crush"] = common.pack_f32(chans["crush"]["values"])
    obj["wall_force"] = common.pack_f32(chans["wall_force"]["values"])
    obj["units"] = {k: chans[k]["unit"] for k in chans}
    obj["a_max"] = max(chans["deceleration"]["values"])
    xs = [n["xyz_mm"][0] for n in design.get("nodes", [])]
    xs += [p["c"][0] + p["s"][0] / 2.0 for p in obj["parts"]]
    obj["x_front_mm"] = max(xs)
    wall = _wall(obj)
    obj["wall_x_mm"] = wall["c"][0] - wall["s"][0] / 2.0 if wall else None
    yld = sorted({float(m.get("yield_MPa")) for m in design.get("materials", {}).values() if m.get("yield_MPa")})
    obj["marks"] = [{"v": y, "label": f"yield {y:g}"} for y in yld[:2]]
    lc = common.load_case_of(vdir, rec["load_case_id"])
    mass = common.result(rec, "vehicle_mass")
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"]) + [
        _wall_row(wall),
        {"label": "質量", "value": f"{mass:g} kg" if mass is not None else "not in the record",
         "source": "record result vehicle_mass (design as loaded, the case's loading)"},
        {"label": "減速度のピーク", "value": f"{obj['a_max']:.2f} {obj['units']['deceleration']}",
         "source": "the series' largest value; the frame was solved at this instant"},
    ]
    obj["notes"] = ["A は 1 次元モデル (質量とばね)。車体はその場に置いたまま描き、つぶれ量は下のグラフにだけ出す。",
                    "応力はピークの瞬間に 1 回解いた値 × a(t)/a_max。弾性たわみ (既定は倍率 0) はつぶれた形ではない。"]
    obj["ledger"] = common.parts_ledger("crash", design, lc, rec, obj["parts"])
    return obj


# ---------------------------------------------------------------- B: crash_beam (beams)
def _assumption(rec, pattern) -> str | None:
    for a in rec.get("assumptions", []) or []:
        if re.search(pattern, a):
            return a
    return None


def _fold_pairs(n_nodes, con):
    """For each element, the (other element, shared node, other end of this, other end of that) at its ends."""
    at = [[] for _ in range(n_nodes)]
    for e, (a, b) in enumerate(con):
        at[a].append(e)
        at[b].append(e)
    pairs = []
    for e, (a, b) in enumerate(con):
        lst = []
        for nd in (a, b):
            me = b if nd == a else a
            for o in at[nd]:
                if o != e:
                    oa, ob = con[o]
                    lst.append((nd, me, ob if oa == nd else oa))
        pairs.append(lst)
    return pairs


def _angle(p, nd, i, j):
    u = [p[i][k] - p[nd][k] for k in range(3)]
    v = [p[j][k] - p[nd][k] for k in range(3)]
    nu, nv = math.sqrt(sum(c * c for c in u)), math.sqrt(sum(c * c for c in v))
    if nu < 1e-9 or nv < 1e-9:
        return None
    c = sum(u[k] * v[k] for k in range(3)) / (nu * nv)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _at_frames(s: dict, t_frames: list[float]) -> list[float]:
    """A series' values at the frame times: the same samples when the times agree (crash_beam writes them at
    the node_motion frames), else the nearest sample (no interpolation)."""
    t, v = s.get("t_s") or [], s["values"]
    if len(t) == len(t_frames) and all(abs(a - b) <= 1e-9 for a, b in zip(t, t_frames)):
        return [float(x) for x in v]
    out = []
    for tf in t_frames:
        j = min(range(len(t)), key=lambda i: abs(t[i] - tf))
        out.append(float(v[j]))
    return out


def member_results(rec: dict, t_frames: list[float], members: list[str], design: dict) -> dict | None:
    """Per member at the frames: axial force (N, + tension, series member_axial_force.<m>), axial stress =
    force / A (MPa; A of the member's section in the design, viewer.src.nvh.section_props), and the larger of
    its end hinge rotations (deg, series member_hinge.<m>.<end node>). None when the record has neither."""
    from viewer.src.nvh import section_props
    ser = {s["channel"]: s for s in rec.get("series", []) or []}
    if not any(c.startswith(("member_axial_force.", "member_hinge.")) for c in ser):
        return None
    dmem = {m["id"]: m for m in design.get("members", [])}
    secs = design.get("sections", {})
    nF = len(t_frames)
    out = {"force": {}, "stress": {}, "hinge": {}, "area": {}, "ends": {}}
    for m in members:
        s = ser.get(f"member_axial_force.{m}")
        f = _at_frames(s, t_frames) if s else [0.0] * nF
        sp = section_props(secs.get(dmem.get(m, {}).get("section")))
        A = sp["A"] if sp else None
        out["area"][m] = A
        if s:
            out["force"][m] = f
        out["stress"][m] = [x / A for x in f] if A else [0.0] * nF
        ends = [c for c in ser if c.startswith(f"member_hinge.{m}.")]
        out["ends"][m] = [c.rsplit(".", 1)[1] for c in ends]
        hs = [_at_frames(ser[c], t_frames) for c in ends]
        out["hinge"][m] = [max((h[k] for h in hs), default=0.0) for k in range(nF)]
    return out


def stop_of(rec: dict) -> dict | None:
    """The split, from the record: frame_split_time (its where = the member that broke) and the assumption
    'the frame split at ... a piece with X % of the whole mass ...'. K2 182f148f stops there ('stops at a piece
    of L %'); from fcd3f2b8 (element contact) the run goes on ('a split off of a piece of L % of the mass or
    more is NG for C1', 'the run goes on past the split'): goes_on. ok: the piece is under the limit."""
    t = next((r for r in rec.get("results", []) if r["quantity"] == "frame_split_time"), None)
    a = _assumption(rec, r"^the frame split at")
    if t is None and a is None:
        return None
    pct = re.search(r"a piece with ([\d.]+) % of the whole mass", a or "")
    lim = (re.search(r"stops at a piece of ([\d.]+) %", a or "")
           or re.search(r"a piece of ([\d.]+) % of the mass or more is NG", a or ""))
    member = (t or {}).get("where") or (re.search(r"when (\S+) broke", a or "") or [None, None])[1]
    piece, limit = (float(pct.group(1)) if pct else None), (float(lim.group(1)) if lim else None)
    return {"t_s": float(t["value"]) if t else None, "member": member, "piece_pct": piece, "limit_pct": limit,
            "goes_on": "run goes on past the split" in (a or ""),
            "ok": (piece < limit) if piece is not None and limit is not None else None, "text": a or ""}


WHEEL_RE = re.compile(r"(\w+) \(([^()]*?), ([\d.]+) kg, radius ([\d.]+) mm\) to ([^()]+?) \(")


def wheels_of(rec: dict, ids: list[str], linked: set[str], design: dict) -> list[dict]:
    """The wheel nodes of the node_motion field (K2 fcd3f2b8: '<wheel>~centre', on no element) with their
    radius, mass, the parts on them and the frame nodes their links go to, as the record's mass assumption
    says them ('wheel_FL (wheel_FL + knuckle_FL + caliper_FL, 18.0 kg, radius 300 mm) to LAl1, LAl2, ...').
    Without that text: the wheel part's box (radius = half its x size), no links."""
    index = {n: i for i, n in enumerate(ids)}
    said = {m.group(1): m for m in WHEEL_RE.finditer(_assumption(rec, r"wheels as nodes at their centres") or "")}
    parts = {p["id"]: p for p in design.get("parts", [])}
    out = []
    for nid in ids:
        if not nid.endswith("~centre") or nid in linked:
            continue
        wid = nid[: -len("~centre")]
        box = (parts.get(wid) or {}).get("box") or {}
        size = box.get("size_mm") or [0.0, 0.0, 0.0]
        m = said.get(wid)
        mounts = [s.strip() for s in m.group(5).split(",")] if m else []
        out.append({"id": wid, "node": index[nid], "r": float(m.group(4)) if m else float(size[0]) / 2.0,
                    "width": float(size[1]), "kg": float(m.group(3)) if m else None,
                    "parts": [s.strip() for s in m.group(2).split("+")] if m else [wid],
                    "mounts": [index[s] for s in mounts if s in index], "mount_ids": [s for s in mounts if s in index]})
    return out


def judge(value, op: str, limit) -> bool | None:
    if value is None or limit is None:
        return None
    return {">=": value >= limit, ">": value > limit, "<=": value <= limit, "<": value < limit}.get(op)


def _criterion(lcs: list[dict | None], quantity: str) -> tuple[str | None, object, str | None]:
    """(op, limit value, load case id) of the first load case judging this quantity."""
    for lc in lcs:
        crit = (lc or {}).get("criterion") or {}
        if crit.get("quantity") == quantity:
            return crit.get("op"), (crit.get("limit") or {}).get("value"), lc.get("load_case_id")
    return None, None, None


def margin_of(rec: dict, lc: dict | None, lcs: list[dict | None] = ()) -> dict | None:
    """occupant_space_margin (the record's result) judged by the load case's criterion (op, limit). From K2 8a24cd15
    it is the least room over the run to the occupant boxes: where = the box; member = the hot spot (a frame member's
    least room) with the same value; t_s = the instant the record's assumption names ('the least over the run at')."""
    r = next((x for x in rec.get("results", []) if x["quantity"] == "occupant_space_margin"), None)
    if r is None:
        return None
    op, lim, lc_id = _criterion([lc, *lcs], "occupant_space_margin")
    op = op or ">="
    v = float(r["value"])
    member = next((h.get("where") for h in rec.get("hot_spots", []) or []
                   if h.get("quantity") == "occupant_space_margin" and isinstance(h.get("value"), dict)
                   and abs(float(h["value"]["value"]) - v) <= 1e-6), None)
    at = re.search(r"\(the least over the run at ([\d.]+) ms\)", _assumption(rec, r"^occupant_space_left at the start") or "")
    return {"value": v, "unit": r.get("unit", "mm"), "where": r.get("where", ""), "member": member,
            "t_s": float(at.group(1)) / 1000.0 if at else None, "op": op, "limit": lim, "load_case_id": lc_id,
            "ok": judge(v, op, lim)}


PIECE_RE = re.compile(r"at ([\d.]+) ms, (\d+) nodes \(([\d.]+) % of the mass: ([^)]*)\)")


def split_of(rec: dict, lcs: list[dict | None], stop: dict | None) -> dict | None:
    """frame_split_fraction (K2 8a24cd15, the result) judged by the load case judging it (C1-split): the largest
    share of the mass in a piece broken off the seats' side that can come into the occupants' space. member / t_s:
    frame_split_time's where and value when the record has one. pieces: the smaller broken-off pieces the record's
    assumption lists (time, nodes, share, members); not_counted: the pieces it says cannot reach the occupants."""
    r = next((x for x in rec.get("results", []) if x["quantity"] == "frame_split_fraction"), None)
    if r is None:
        return None
    op, lim, lc_id = _criterion(lcs, "frame_split_fraction")
    v = float(r["value"])
    pieces = [{"t_s": float(m.group(1)) / 1000.0, "nodes": int(m.group(2)), "pct": float(m.group(3)),
               "members": [s.strip() for s in m.group(4).split(",")]}
              for m in PIECE_RE.finditer(_assumption(rec, r"^smaller pieces broken off") or "")]
    nc = re.search(r"(\d+) other piece\(s\) cannot", _assumption(rec, r"^frame_split_fraction:") or "")
    return {"value": v, "pct": 100.0 * v, "op": op, "limit": lim, "limit_pct": 100.0 * lim if lim is not None else None,
            "load_case_id": lc_id, "ok": judge(v, op, lim) if op else None,
            "member": (stop or {}).get("member"), "t_s": (stop or {}).get("t_s"), "pieces": pieces,
            "not_counted": int(nc.group(1)) if nc else None}


def hot_spots_of(rec: dict) -> list[dict]:
    out = []
    for h in rec.get("hot_spots", []) or []:
        v, lim = h.get("value"), h.get("limit")
        out.append({"where": h.get("where", ""), "quantity": h.get("quantity", ""),
                    "value": float(v["value"]) if isinstance(v, dict) else v, "unit": v.get("unit", "") if isinstance(v, dict) else "",
                    "limit": float(lim["value"]) if isinstance(lim, dict) else lim,
                    "ok": (float(v["value"]) >= float(lim["value"])) if isinstance(v, dict) and isinstance(lim, dict) else None})
    return sorted(out, key=lambda h: h["value"] if isinstance(h["value"], (int, float)) else 0.0)


def build_b(args, vdir, rec, design) -> dict:
    obj = common.base_object("crash", "衝突: 壁への正面衝突 (B: 梁が降伏して折れる)", args.run, vdir, rec, design)
    obj["model"] = "B"
    nm = next((f for f in rec.get("fields", []) or [] if f.get("kind") == "node_motion"), None)
    if nm is None:
        raise LookupError(f"{rec['_path']}: a crash_beam record without a node_motion field (the mesh may have had "
                          "more nodes than the contract allows; see its assumptions)")
    ids = list(nm["nodes"])
    index = {n: i for i, n in enumerate(ids)}
    con = [[index[a], index[b]] for a, b, _m in nm["members"]]
    emem = [m for _a, _b, m in nm["members"]]
    dmem = {m["id"]: m for m in design.get("members", [])}
    sections = design.get("sections", {})
    frames = nm["frames"]
    P = frames["positions_mm"]
    nF, n, ne = len(P), len(ids), len(con)
    obj["frame"] = {"node_ids": ids, "nodes": common.pack_f32([c for p in P[0] for c in p], [n, 3]), "members": con,
                    "member_ids": emem, "member_names": [dmem.get(m, {}).get("name", m) for m in emem],
                    "radius_mm": [common._radius(sections.get(dmem.get(m, {}).get("section"))) for m in emem]}
    obj["motion"] = {"t": common.pack_f32(frames["t_s"]), "pos": common.pack_f32([c for F in P for p in F for c in p], [nF, n, 3]),
                     "broken": frames.get("broken") or [[] for _ in range(nF)]}
    # colour from the same run: element axial strain (length change) and fold angle at its ends
    L0 = [math.dist(P[0][a], P[0][b]) for a, b in con]
    pairs = _fold_pairs(n, con)
    ang0 = [[_angle(P[0], nd, i, j) for nd, i, j in pr] for pr in pairs]
    strain, fold = [], []
    for F in P:
        for e, (a, b) in enumerate(con):
            strain.append(100.0 * (math.dist(F[a], F[b]) - L0[e]) / L0[e] if L0[e] > 0 else 0.0)
            best = 0.0
            for (nd, i, j), a0 in zip(pairs[e], ang0[e]):
                a1 = _angle(F, nd, i, j)
                if a0 is not None and a1 is not None:
                    best = max(best, abs(a1 - a0))
            fold.append(best)
    # the colour range leaves out members broken by that frame (a removed element's ends fly apart)
    brk = frames.get("broken") or [[] for _ in range(nF)]
    whole = [k * ne + e for k in range(nF) for e in range(ne) if emem[e] not in set(brk[k])]
    obj["colour"] = {"strain": common.pack_f32(strain, [nF, ne]), "fold": common.pack_f32(fold, [nF, ne]),
                     "strain_absmax": max((abs(strain[i]) for i in whole), default=0.0),
                     "fold_max": max((fold[i] for i in whole), default=0.0)}
    # the record's own member results at the node_motion frames: axial force (-> stress = N / A of the design's
    # section) and the plastic hinge rotation at each member end (the larger end per member)
    mem = member_results(rec, frames["t_s"], sorted(set(emem)), design)
    if mem is not None:
        stress = [mem["stress"][emem[e]][k] for k in range(nF) for e in range(ne)]
        hinge = [mem["hinge"][emem[e]][k] for k in range(nF) for e in range(ne)]
        obj["colour"].update({
            "stress": common.pack_f32(stress, [nF, ne]), "hinge": common.pack_f32(hinge, [nF, ne]),
            "stress_absmax": max((abs(stress[i]) for i in whole), default=0.0),
            "hinge_max": max((hinge[i] for i in whole), default=0.0),
            "force": {m: common.pack_f32(v) for m, v in mem["force"].items()},
            "area_mm2": mem["area"], "hinge_ends": mem["ends"]})
    # part boxes moved by the mean motion of the nodes the tool puts their mass on (translation only): the
    # carried_by nodes, or for a part without carried_by its support's frame node (common.parts_ledger); a part
    # the loading leaves off stays where it is
    lc = common.load_case_of(vdir, rec["load_case_id"])
    # the other C1 load case judged from the same run (C1-split), when the run stored it
    lcs = [x for x in [common.load_case_of(vdir, "C1-split")] if x and x.get("load_case_id") != rec["load_case_id"]]
    obj["ledger"] = common.parts_ledger("crash_beam", design, lc, rec, obj["parts"])
    # the wheel nodes (K2 fcd3f2b8): on no element of the field (their links are not in its members); the parts
    # the record puts on a wheel node move with it
    linked = {x for a, b, _m in nm["members"] for x in (a, b)}
    wheels = wheels_of(rec, ids, linked, design)
    obj["wheels"] = [{"id": w["id"], "node": w["node"], "r": w["r"], "width": w["width"], "kg": w["kg"],
                      "parts": w["parts"], "mounts": w["mounts"], "mount_ids": w["mount_ids"],
                      "c": P[0][w["node"]]} for w in wheels]
    on_wheel = {pid: w for w in wheels for pid in w["parts"]}
    dparts = {p["id"]: p for p in design.get("parts", [])}
    for e in obj["ledger"]["parts"]:
        w = on_wheel.get(e.get("id"))
        if w is None or e["cat"] == "not_in":
            continue
        e.update(cat="unsprung", nodes=[[ids[w["node"]], float(dparts.get(e["id"], {}).get("mass_kg") or 0.0)]],
                 how=(f"in the wheel node {ids[w['node']]} ({' + '.join(w['parts'])}"
                      + (f", {w['kg']:g} kg" if w["kg"] is not None else "") + f"), joined to the frame nodes "
                      f"{', '.join(w['mount_ids']) or '(not said)'} by pin-ended links (axial springs only); the wheel "
                      f"touches the wall at its centre's x + {w['r']:g} mm and stops there (record assumptions)"))
    if wheels:
        # the ledger's per-support checks again with the wheel parts on their wheel nodes, and one row per wheel
        L = obj["ledger"]
        for s in L.get("stated", []):
            m = re.match(r"unsprung at \S+ \((\S+)\)$", s["label"])
            if m:
                s["computed_kg"] = sum(e["mass_kg"] for e in L["parts"] if e["cat"] == "unsprung" and e["nodes"]
                                       and e["nodes"][0][0] == m.group(1))
        for w in wheels:
            if w["kg"] is None:
                continue
            L.setdefault("stated", []).append(
                {"label": f"wheel node {ids[w['node']]}", "stated_kg": w["kg"], "tol_kg": 0.051,
                 "computed_kg": sum(e["mass_kg"] for e in L["parts"] if e["nodes"] and e["nodes"][0][0] == ids[w["node"]]),
                 "source": "record assumption (the wheels as nodes), parts " + " + ".join(w["parts"])})
        for s in L.get("stated", []):
            s["diff_kg"] = None if s["computed_kg"] is None else s["computed_kg"] - s["stated_kg"]
            s["agrees"] = s["diff_kg"] is not None and abs(s["diff_kg"]) <= s["tol_kg"]
            if s["agrees"]:
                s.pop("why", None)
            elif "why" not in s:
                s["why"] = "the design.json read here is not the design the tool weighed, or the loading differs"
    offs = []
    keep = []
    for p, e in zip(obj["parts"], obj["ledger"]["parts"]):
        cb = [index[nd] for nd, _kg in e["nodes"] if nd in index] if e["cat"] != "not_in" else []
        keep.append((p, cb))
    for F in P:
        for p, cb in keep:
            for k in range(3):
                offs.append(sum(F[i][k] - P[0][i][k] for i in cb) / len(cb) if cb else 0.0)
    obj["part_off"] = common.pack_f32(offs, [nF, len(keep), 3])
    dropped = [p["id"] for p, cb in keep if not cb]
    # the wall and what is beyond it
    wall = _wall(obj)
    wx = wall["c"][0] - wall["s"][0] / 2.0 if wall else max(p[0] for p in P[0])
    obj["wall_x_mm"] = wx
    bn, bd, bp, bpd, any_nodes = [], [], [], [], set()
    any_parts = set()
    wnodes = {w["node"] for w in wheels}
    fnodes = [i for i in range(n) if i not in wnodes]
    # each wheel's front (centre x + r) less the wall face per frame; the first frame at the wall (within TOL_MM)
    for w, ow in zip(wheels, obj["wheels"]):
        gap = [F[w["node"]][0] + w["r"] - wx for F in P]
        k_hit = next((k for k, g in enumerate(gap) if g >= -TOL_MM), None)
        ow.update(front_gap_min=min(-g for g in gap), beyond_max=max(max(gap), 0.0),
                  t_wall_s=float(frames["t_s"][k_hit]) if k_hit is not None else None)
    for k, F in enumerate(P):
        over = [(F[i][0] - wx, i) for i in fnodes if F[i][0] > wx + TOL_MM]
        bn.append(len(over))
        bd.append(max((d for d, _ in over), default=0.0))
        any_nodes |= {i for _, i in over}
        po = []
        for j, (p, _cb) in enumerate(keep):
            if not _cb:
                continue
            front = p["c"][0] + p["s"][0] / 2.0 + offs[(k * len(keep) + j) * 3]
            if front > wx + TOL_MM:
                po.append(front - wx)
                any_parts.add(p["id"])
        bp.append(len(po))
        bpd.append(max(po, default=0.0))
    obj["beyond"] = {"nodes": bn, "depth": bd, "parts": bp, "part_depth": bpd, "nodes_any": len(any_nodes),
                     "node_depth_max": max(bd, default=0.0), "parts_any": sorted(any_parts),
                     "part_depth_max": max(bpd, default=0.0), "tol_mm": TOL_MM}
    dec, cr = common.series(rec, "deceleration"), common.series(rec, "crush")
    if dec is None or cr is None:
        raise LookupError(f"{rec['_path']}: no deceleration or crush series")
    obj["series"] = {"t": common.pack_f32(dec["t_s"]), "decel": common.pack_f32(dec["values"]),
                     "crush": common.pack_f32(cr["values"])}
    obj["units"] = {"deceleration": dec.get("unit", "g"), "crush": cr.get("unit", "mm")}
    obj["stop"] = st = stop_of(rec)
    goes_on = bool(st and st["goes_on"])
    obj["hot_spots"] = hot_spots_of(rec)
    dv = dec["values"]
    least = next((h for h in obj["hot_spots"] if isinstance(h["value"], (int, float))), None)
    max_crush, at_split = common.result(rec, "max_crush"), common.result(rec, "crush_at_split")
    free = re.search(r"nothing touched the wall after ([\d.]+) ms", _assumption(rec, r"nothing touched the wall after") or "")
    obj["summary"] = {
        "t_end_s": float(frames["t_s"][-1]),
        "peak_decel": common.result(rec, "peak_decel") if common.result(rec, "peak_decel") is not None else max(dv, default=0.0),
        # the record's mean_decel (speed lost / time to the stop); a record without it: the series' mean (the series
        # is the wall's impulse per 1 ms / (mass x 1 ms) at even steps)
        "mean_decel": (common.result(rec, "mean_decel") if common.result(rec, "mean_decel") is not None
                       else sum(dv) / len(dv) if dv else None),
        # a run stopped at the split: the crush then; a run going on: the record's max_crush over the run
        "crush_end": (max_crush if goes_on and max_crush is not None else at_split if at_split is not None
                      else (cr["values"][-1] if cr["values"] else None)),
        "crush_at_split": at_split,
        "crush_max": max(cr["values"], default=None),
        "least_room": least,
        "margin": margin_of(rec, lc, lcs),
        "split": split_of(rec, [lc, *lcs], st),
        "crumple_length": common.result(rec, "crumple_length"),
        "crush_margin": next(({"value": float(x["value"]), "unit": x.get("unit", "mm"), "where": x.get("where", "")}
                              for x in rec.get("results", []) if x["quantity"] == "crush_margin"), None),
        "goes_on": goes_on,
        # after the split (the run going on): from the split to the end of the record
        "after": {"t0_s": st["t_s"], "t1_s": float(frames["t_s"][-1])} if goes_on and st["t_s"] is not None else None,
        "wall_free_after_s": float(free.group(1)) / 1000.0 if free else None,
    }
    mass = _assumption(rec, r"^mass [\d.]+ kg")
    obj["inputs"] = common.case_inputs(lc, rec["load_case_id"]) + [
        _wall_row(wall),
        {"label": "壁の摩擦", "value": _assumption(rec, r"^wall friction") or "given in the case (see params)",
         "source": "record assumptions (the tool's default when the case does not give wall_mu)"},
        {"label": "梁が折れる角度", "value": _assumption(rec, r"breaks \(is removed\)") or "given in the case (see params)",
         "source": "record assumptions (the tool's default when the case does not give hinge_break_angle)"},
        {"label": "質量", "value": mass.split(":")[0] if mass else "not in the record",
         "source": "record assumptions (frame on its element nodes, parts on their carried_by nodes)"},
        {"label": "梁の分割", "value": f"{n} nodes, {ne} beam elements of {len(set(emem))} members; {nF} frames kept of the run",
         "source": "record node_motion field"},
    ]
    b = obj["beyond"]
    obj["notes"] = [
        "形は計算の節点位置そのもの (変形 ×1、実寸)。色はその位置から求めた要素の軸ひずみ、または端の折れ角。",
        (f"壁面 (x = {wx:g} mm) より先へ出たフレーム節点: 全体で {b['nodes_any']} 個、最大 {b['node_depth_max']:.1f} mm。"
         if b["nodes_any"] else f"壁面 (x = {wx:g} mm) より先へ出たフレーム節点はない (許容 {TOL_MM} mm)。")
        + (f" 部品の箱は壁に当たらない扱い (節点と一緒に動かすだけ): 壁面の先に出た箱 {', '.join(b['parts_any'][:6])}"
           f"{' ほか' if len(b['parts_any']) > 6 else ''}、最大 {b['part_depth_max']:.1f} mm (t = 0 で既に {b['parts'][0]} 個)。"
           if b["parts_any"] else ""),
    ] + ([f"箱を動かす節点がない部品 (その場に置く): {len(dropped)} 個"] if dropped else [])
    obj["notes"].append("部品の箱は壁面で切って描く (箱は壁に当たらない扱いなので、壁の先は描かない)。")
    full = st and st["t_s"] is not None and st["piece_pct"] is not None and st["limit_pct"] is not None
    if st and goes_on:
        obj["notes"].append(f"フレームの分離: {st['t_s'] * 1000:.1f} ms、{st['member']} が折れて、座席側から質量の "
                            f"{st['piece_pct']:g} % の塊が離れた ({st['limit_pct']:g} % 以上は C1 で NG)。塊どうしは要素の接触で"
                            f"押し合うので、計算は {obj['summary']['t_end_s'] * 1000:.0f} ms まで続く (グラフの網かけ = 分離の後)。"
                            if full else "フレームの分離: " + st["text"][:200])
    elif st:
        obj["notes"].append(f"計算の停止: {st['t_s'] * 1000:.1f} ms、{st['member']} が折れて、座席側から質量の "
                            f"{st['piece_pct']:g} % の塊が離れた (K2 は塊どうしの接触がないため、{st['limit_pct']:g} % 以上で止める)。"
                            if full else "計算の停止: " + st["text"][:200])
    sp = obj["summary"]["split"]
    if sp and sp["pieces"]:
        obj["notes"].append("座席側から離れた塊: " + "、".join(
            f"{p['t_s'] * 1000:.1f} ms に {', '.join(p['members'])} ({p['nodes']} 節点、質量の {p['pct']:g} %)" for p in sp["pieces"])
            + "。frame_split_fraction は乗員の箱の前 (y-z の範囲) に来うる塊だけを数える"
            + (f" (数えない塊 {sp['not_counted']} 個)" if sp["not_counted"] else "") + "。")
    # K2 43c1efed: the wheels touch the wall through the tyre's radial spring (not stopped in one step)
    tyre = bool(_assumption(rec, r"the wheel nodes through the tyre"))
    if obj["wheels"]:
        obj["notes"].append("車輪は中心の節点 1 つ (半径 " + "、".join(sorted({f"{w['r']:g}" for w in obj["wheels"]})) + " mm の輪で描く)。"
                            "取付の節点へは細い線 (ピン結合のリンク: 伸び縮みだけ、曲がらない) でつなぐ。"
                            + ("中心の x + 半径 が壁を越えた分だけタイヤのばね (半径方向の剛性) で押し返される。" if tyre else
                               "中心の x + 半径 で壁に当たり、そこで止まる (はね返らない)。")
                            + "車輪と部品の箱はフレームの要素には当たらない。")
    obj["tyre_spring"] = tyre
    if obj["summary"]["wall_free_after_s"] is not None:
        obj["notes"].append(f"{obj['summary']['wall_free_after_s'] * 1000:.1f} ms より後は何も壁に当たっていない "
                            "(座席側の塊は前に何もないまま進む)。")
    obj["notes"].append("ピークの減速度は乗員への入力: 車全体の質量の減速度を 1 ms ごとにならした値 (壁が受けた力積 ÷ 質量 ÷ 1 ms)。"
                        + ("車輪が壁に当たった瞬間は、その車輪が 1 ステップで止まるので短いピークが出る。"
                           if not tyre and any(w["t_wall_s"] is not None for w in obj["wheels"]) else ""))
    if "stress" in obj["colour"]:
        obj["notes"].append("軸応力 = 記録の部材の軸力 / 設計の断面積 (引張 +)。ヒンジ回転 = 記録の部材の両端の塑性ヒンジ回転の大きい方。"
                            "どちらも部材ごとの値で、部材の要素は同じ色。")
    return obj


def _extra(ap):
    ap.add_argument("--tool", choices=["crash", "crash_beam"], help="record kind (default: crash_beam if the run has one)")


if __name__ == "__main__":
    common.main("crash", build, extra=_extra, argv=sys.argv[1:])

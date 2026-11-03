"""Load cases for the tests the load_path tool takes: R7 (the controls' loads) and R8 (the load path).

R7: the hand control's grip pushed with Q66 in three directions in turn (deflection <= Q67), and each pedal's face
with Q68 (deflection <= Q69): one load case each, one criterion each.
R8: every part's m a (sideways Q75, fore-and-aft Q76, down 1 g) and the occupants' inertia (Q33 times those)
on what they rest on, judged on free_parts = 0 and on max_utilisation <= 1 (each member's stress over its own
material's yield / Q61, load case param safety_factor = Q61); and Q77 outward at point R
(hip and shoulder), judged on occupant_push_deflection <= Q78. One load case per criterion.

A product whose requirements lack a key a case needs gets no such case; with none left the test is not
evaluated (BuildError, its message naming the missing keys). Q75 and Q76 are not taken from elsewhere.

Next to the builders: the readers of the K0 inputs (control_inputs, free_inputs) and the plans.
"""

from __future__ import annotations

import numpy as np

from hiraku.catalog.builders.common import BuildError, Record, entry, loading, number, q, tolerance
from hiraku.catalog.loading import LOADING_JA

# R7: the hand calculation follows the shortest chain of contacts to the frame and takes the member it lands on as
# a cantilever from its nearest node (the rest of the frame rigid); the model has every contact in parallel and the
# whole frame. Measured (2026-11-03): the demo's version 1 grip, hand 0.87 mm against the model's 1.05 mm (+20 %:
# the joints' rotation is 99.9 % of the hand value; the model adds the dash members' own bending); version 1 with
# both pedals moved onto what is nearest to them (the bulkhead, tower_post_RA2), hand 0.19 mm against 0.16 mm
# (-16 %: the model shares the load over the parallel contacts). 30 %.
R7_TOL = 0.3
# R8: free_parts is a count; the graph search and rigid_body_check find the same parts (the contacts are the same).
R8_TOL = 0.05
VERTICAL_G = 1.0  # no requirement gives a vertical acceleration for the parts: gravity only (1 g)

NOT_BORROWED = "乗らない製品の加速度は別の値なので借りません"
KEY_NAMES = {"Q66": "手の操作部に掛かる力", "Q67": "手の操作部の動きの上限", "Q68": "足の操作部に掛かる力",
             "Q69": "足の操作部の動きの上限", "Q75": "横の加速度", "Q76": "前後の加速度", "Q61": "安全率",
             "Q77": "点 R を外へ押す力", "Q78": "点 R の永久の変形の上限", "Q33": "体重"}


def _missing(req: Record, keys: tuple[str, ...]) -> list[str]:
    return [k for k in keys if entry(req, k) is None]


def _absent(keys: list[str], why: str = "") -> str:
    return "、".join(f"{k} {KEY_NAMES.get(k, '')}".strip() for k in keys) + "が要件に無い" + (f": {why}" if why else "")


# ---------------------------------------------------------------- R7 the controls


def r7_controls(spec: Record, req: Record, design: Record) -> list[Record]:
    from hiraku.solvers.shelf.load_path import foot_controls, hand_controls

    cases, why = [], []
    hand_miss, foot_miss = _missing(req, ("Q66", "Q67")), _missing(req, ("Q68", "Q69"))
    if hand_miss and foot_miss:
        raise BuildError("評価できない: " + _absent(hand_miss + foot_miss))
    load, keys = loading(req, design)
    tol = tolerance(spec, R7_TOL)
    forces = {}
    if not hand_miss:
        forces["hand_force"] = q(number(req, "Q66", "N"), "N")
    if not foot_miss:
        forces["foot_force"] = q(number(req, "Q68", "N"), "N")
    hands, feet = hand_controls(design), foot_controls(design)
    if hand_miss:
        why.append("手: " + _absent(hand_miss))
    elif not hands:
        why.append("手: 設計に手の操作部 (ハンドル・レバー) が無い")
    else:
        F, lim = number(req, "Q66", "N"), number(req, "Q67", "mm")
        part = hands[0]["id"]
        cases.append({
            "load_case_id": f"R7-hand-{F:g}N",
            "test_id": "R7",
            "domain": spec["domain"],
            "title": f"手の操作部 ({part}) の握る点を {F:g} N で前後・上下・回す向きに押す ({LOADING_JA[load]})",
            "params": {"load_kind": "hand", **forces, "hand_part": part, "loading": load},
            "requirement_keys": ["Q66", "Q67", *keys],
            "criterion": {"quantity": "grip_deflection", "op": "<=", "limit": q(lim, "mm"),
                          "basis": (f"Q67: the grip moves at most {lim:g} mm under Q66 {F:g} N in each of the three "
                                    "directions; a grip on a part joined to nothing has no deflection and shows as "
                                    "free_parts"),
                          "requirement_keys": ["Q67"]},
            "tolerance_rel": tol,
        })
    if foot_miss:
        why.append("足: " + _absent(foot_miss))
    elif not feet:
        why.append("足: 設計に足の操作部 (ペダル) が無い")
    else:
        F, lim = number(req, "Q68", "N"), number(req, "Q69", "mm")
        cases.append({
            "load_case_id": f"R7-foot-{F:g}N",
            "test_id": "R7",
            "domain": spec["domain"],
            "title": f"足の操作部 ({', '.join(p['id'] for p in feet)}) の踏む面を {F:g} N で踏む ({LOADING_JA[load]})",
            "params": {"load_kind": "foot", **forces, "foot_parts": ",".join(p["id"] for p in feet), "loading": load},
            "requirement_keys": ["Q68", "Q69", *keys],
            "criterion": {"quantity": "pedal_deflection", "op": "<=", "limit": q(lim, "mm"),
                          "basis": (f"Q69: each pedal's face moves at most {lim:g} mm under Q68 {F:g} N; a pedal joined "
                                    "to nothing has no deflection and shows as free_parts"),
                          "requirement_keys": ["Q69"]},
            "tolerance_rel": tol,
        })
    if not cases:
        raise BuildError("評価できない: " + "。".join(why))
    return cases


# ---------------------------------------------------------------- R8 the load path


def r8_params(req: Record, load: str) -> tuple[Record, list[str]]:
    """The inertia params (accelerations in g, the occupants' forces in N worked out here) and their keys."""
    from hiraku.catalog.builders.common import body_mass_in_kg
    from hiraku.catalog.loading import OCCUPANTS
    from hiraku.solvers.k0_handcalc.units import STANDARD_GRAVITY

    lat, lon = number(req, "Q75", "g"), number(req, "Q76", "g")
    params = {"accel_vertical": q(VERTICAL_G, "g"), "accel_lateral": q(lat, "g"), "accel_longitudinal": q(lon, "g")}
    keys = ["Q75", "Q76"]
    if OCCUPANTS[load]:
        m = number(body_mass_in_kg(req), "Q33", "kg")
        params.update(occupant_mass=q(m, "kg"), occupant_force_lateral=q(m * lat * STANDARD_GRAVITY, "N"),
                      occupant_force_longitudinal=q(m * lon * STANDARD_GRAVITY, "N"))
        keys.append("Q33")
    return params, keys


def member_yields(design: Record) -> list[str]:
    """The yields of the materials the members use, for the basis: "<name> <yield> MPa (n members[, not checked])"."""
    mats = design.get("materials", {})
    used: dict[str, int] = {}
    for m in design.get("members", []):
        used[m["material"]] = used.get(m["material"], 0) + 1
    if not used:
        raise BuildError("the design has no members, so there is no member stress to judge")
    missing = sorted(k for k in used if k not in mats or mats[k].get("yield_MPa") is None)
    if missing:
        raise BuildError(f"members use materials with no yield_MPa in design.materials: {missing}")
    return [f"{mats[k].get('name', k)} {float(mats[k]['yield_MPa']):g} MPa ({n} members"
            f"{'' if mats[k].get('verified') else ', not checked'})" for k, n in sorted(used.items())]


def r8_load_path(spec: Record, req: Record, design: Record) -> list[Record]:
    from hiraku.catalog.loading import OCCUPANTS
    from hiraku.solvers.shelf.load_path import occupant_trunks

    miss = _missing(req, ("Q75", "Q76"))
    if miss:
        raise BuildError("評価できない: " + _absent(miss, NOT_BORROWED))
    load, keys = loading(req, design)
    params, pkeys = r8_params(req, load)
    tol = tolerance(spec, R8_TOL)
    a = f"横 {params['accel_lateral']['value']:g} g・前後 {params['accel_longitudinal']['value']:g} g・下 {VERTICAL_G:g} g"
    base = {"test_id": "R8", "domain": spec["domain"], "tolerance_rel": tol}
    inertia = {"load_kind": "inertia", **params, "loading": load}
    cases = [{
        **base,
        "load_case_id": "R8-free",
        "title": f"どの部品も、枠まで繋がっている ({a} を全部の部品と乗員に、{LOADING_JA[load]})",
        "params": dict(inertia),
        "requirement_keys": [*pkeys, *keys],
        "criterion": {"quantity": "free_parts", "op": "<=", "limit": q(0.0, "1"),
                      "basis": ("every part with carried_by reaches a frame member through parts its box touches "
                                "(load_path: a part joined to nothing is a free rigid body, found by "
                                "fe.precheck.rigid_body_check)"),
                      "requirement_keys": ["Q75", "Q76"]},
    }]
    if entry(req, "Q61") is not None:
        sf = number(req, "Q61", "1")
        if sf <= 0:
            raise BuildError(f"Q61 safety factor must be positive, got {sf:g}")
        yields = member_yields(design)
        cases.append({
            **base,
            "load_case_id": "R8-stress",
            "title": f"部品と乗員の重さと加速度 ({a}) で、各部材の応力をその材料の許しと比べる ({LOADING_JA[load]})",
            "params": {**inertia, "safety_factor": q(sf, "1")},
            "requirement_keys": [*pkeys, "Q61", *keys],
            "criterion": {"quantity": "max_utilisation", "op": "<=", "limit": q(1.0, "1"),
                          "basis": ("each member's von Mises stress over its own material's yield / Q61 "
                                    f"{sf:g} (largest of these ratios): " + "; ".join(yields)),
                          "requirement_keys": ["Q61"]},
        })
    push_miss = _missing(req, ("Q77", "Q78"))
    if not push_miss and OCCUPANTS[load] and occupant_trunks(design):
        F, lim = number(req, "Q77", "N"), number(req, "Q78", "mm")
        cases.append({
            **base,
            "load_case_id": f"R8-push-{F:g}N",
            "title": f"乗員の肩・腰 (点 R) を {F:g} N で外へ押す ({LOADING_JA[load]})",
            "params": {"load_kind": "push", "push_force": q(F, "N"), "loading": load},
            "requirement_keys": ["Q77", "Q78", *keys],
            "criterion": {"quantity": "occupant_push_deflection", "op": "<=", "limit": q(lim, "mm"),
                          "basis": (f"Q78: what stands outboard of point R moves less than {lim:g} mm under Q77 "
                                    f"{F:g} N (the elastic movement taken as the bound of the permanent one); a point "
                                    "with nothing outboard counts in occupant_push_open"),
                          "requirement_keys": ["Q78"]},
        })
    return cases


# ---------------------------------------------------------------- the K0 inputs


def _chain(contacts, start: str):
    """The shortest chain of contacts from part `start` to a member: [Contact, ...] (the member's last), or None."""
    from collections import deque

    by = {}
    for c in contacts:
        by.setdefault(c.a, []).append((c.b, c))
        by.setdefault(c.b, []).append((c.a, c))
    prev = {f"part:{start}": None}
    todo = deque([f"part:{start}"])
    while todo:
        x = todo.popleft()
        for y, c in by.get(x, []):
            if y in prev:
                continue
            prev[y] = (x, c)
            if y.startswith("member:"):
                out = []
                while prev[y] is not None:
                    y, c2 = prev[y]
                    out.append(c2)
                return out[::-1]
            todo.append(y)
    return None


def plate_strip_inputs(design: Record, chain, start: str, point, force) -> list[tuple[str, Record]]:
    """load_path.plate_strip's inputs for each plate part the chain of contacts passes through (in at one contact,
    out at the next): the strip from the plate's mid-plane point under the contact the load comes in by to the plate's
    centre, as the load_path tool lays a plate's strips (plate_parts, strip_widths: the contact's width, spread at
    PLATE_SPREAD_DEG each side, at most the plate's width across the strip), loaded by `force` (N, a vector) at
    `point`. A contact within the plate's thickness of its centre has no strip (the tool joins it to the centre)."""
    import math

    from hiraku.solvers.shelf.load_path import PLATE_SPREAD_DEG, plate_parts, plate_point

    plates = plate_parts(design)
    F, p = np.asarray(force, float), np.asarray(point, float)
    c_grow = 2.0 * math.tan(math.radians(PLATE_SPREAD_DEG))
    out, body = [], f"part:{start}"
    for i, c in enumerate(chain):
        nxt = c.b if c.a == body else c.a
        pid = nxt.split(":", 1)[1]
        if nxt.startswith("part:") and pid in plates and i + 1 < len(chain):
            pl = plates[pid]
            end = plate_point(pl, c.point)
            d = pl.center - end
            L = float(np.linalg.norm(d))
            if L > pl.t:
                a = d / L
                across = np.cross(pl.normal, a)
                b_plate = 2.0 * float(np.sum(pl.half * np.abs(pl.inplane @ across)))
                w0 = math.sqrt(c.area)
                w1 = max(w0, min(b_plate, w0 + c_grow * L))
                r = p - end
                out.append((pid, {
                    "F_n": q(float(F @ pl.normal), "N"), "F_a": q(float(F @ a), "N"), "F_c": q(float(F @ across), "N"),
                    "r_n": q(float(r @ pl.normal), "mm"), "r_a": q(float(r @ a), "mm"), "r_c": q(float(r @ across), "mm"),
                    "L": q(L, "mm"), "w_0": q(w0, "mm"), "w_1": q(w1, "mm"), "t": q(pl.t, "mm"), "E": q(pl.E, "MPa"),
                    "G": q(pl.G, "MPa"), "c": q(c_grow, "1")}))
        body = nxt
    return out


def chain_inputs(design: Record, part_id: str, point, F: float, directions=None) -> tuple[Record, list[str]]:
    """load_path.control_deflection's inputs for a load F at `point` on part `part_id`, and what was assumed.
    Without a chain of contacts (the part floats), the designer's intent: one bolted contact at the part's
    carried_by nodes and no cantilever (the hand calculation of a part held where carried_by says).
    directions: the load's unit directions (the largest plate term of them is taken); without them a plate in the
    chain is loaded across (along its normal)."""
    from hiraku.solvers import formula_registry
    from hiraku.solvers.k0_handcalc.vehicle import member_props
    from hiraku.solvers.shelf.load_path import AREA_PER_BOLT_MM2, contact_stiffness, find_contacts, plate_parts

    props = member_props(design)
    p = np.asarray(point, float)
    chain = _chain(find_contacts(design), part_id)
    notes = []
    d_plate = 0.0
    if chain is None:
        part = next(x for x in design["parts"] if x["id"] == part_id)
        nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
        at = np.mean([nodes[n] for n in part.get("carried_by") or []], axis=0)
        k_t, k_rot, _ = contact_stiffness(AREA_PER_BOLT_MM2)
        joints = [(k_t, k_rot, at)]
        mid = next(m for m, mp in props.items() if set(part["carried_by"]) & {mp["member"]["node_a"], mp["member"]["node_b"]})
        L = 0.0
        notes.append(f"{part_id} は触れている物で枠まで繋がらないので、carried_by の節に 1 本のボルトで留めたとみた (設計の意図)")
    else:
        joints = [(c.k_t, c.k_r, c.point) for c in chain]
        last = chain[-1]
        mid = last.b.split(":", 1)[1]
        m = props[mid]["member"]
        near, s = (m["node_a"], last.t) if last.t <= 0.5 else (m["node_b"], 1.0 - last.t)
        if sum(near in (x["node_a"], x["node_b"]) for x in design["members"]) == 1:
            s = 1.0 - s  # the nearest end is joined to nothing else (a free end): the cantilever is from the other
            notes.append(f"{mid} のいちばん近い端 {near} はほかの部材に繋がらないので、もう一方の端からの片持ち")
        L = s * props[mid]["length"]
        notes.append("鎖: " + ", ".join(f"{c.a} - {c.b}" for c in chain))
        strips = plate_strip_inputs(design, chain, part_id, p, np.zeros(3))
        if strips:
            reg = formula_registry()
            plates = plate_parts(design)
            dirs = [np.asarray(x, float) for x in directions] if directions is not None else None
            best = 0.0
            for u in (dirs or [None]):
                total = 0.0
                for pid, _ in strips:
                    vec = F * (u if u is not None else plates[pid].normal)
                    one = dict(plate_strip_inputs(design, chain, part_id, p, vec))[pid]
                    total += reg.evaluate("load_path.plate_strip", one)["outputs"]["delta"]["value"]
                best = max(best, total)
            d_plate = best
            notes.append("板の帯: " + ", ".join(pid for pid, _ in strips))
    s = props[mid]["section"]
    E = float(props[mid]["material"]["E_MPa"])
    ct = sum(1.0 / k for k, _, _ in joints)
    cr = sum(float(np.dot(p - x, p - x)) / kr for _, kr, x in joints)
    k_r = 1.0 / cr if cr > 0 else 1e12  # every contact on the load's line: its rotation does not move the point
    return {"F": q(F, "N"), "k_t": q(1.0 / ct, "N/mm"), "k_r": q(k_r, "N/mm"), "L": q(L, "mm"), "E": q(E, "MPa"),
            "I": q(min(s["Iy"], s["Iz"]), "mm^4"), "delta_plate": q(d_plate, "mm")}, notes


def _force(load_case: Record, name: str, default: float) -> float:
    from hiraku.solvers.k0_handcalc import units

    v = (load_case.get("params") or {}).get(name)
    return default if v is None else units.to_unit(v, "N", name)


def grip_inputs(design: Record, load_case: Record) -> Record:
    """control_deflection's inputs for the hand control's grip (load_case params hand_part, hand_force)."""
    from hiraku.solvers.shelf.load_path import grip, hand_controls

    pid = (load_case.get("params") or {}).get("hand_part") or next((p["id"] for p in hand_controls(design)), None)
    if pid is None:
        raise BuildError("the design has no hand control")
    point, dirs = grip(next(p for p in design["parts"] if p["id"] == pid))
    return chain_inputs(design, pid, point, _force(load_case, "hand_force", 300.0), list(dirs.values()))[0]


def pedal_inputs(design: Record, load_case: Record) -> Record:
    """control_deflection's inputs for the pedal whose hand calculation moves the most (params foot_parts,
    foot_force)."""
    from hiraku.solvers import formula_registry
    from hiraku.solvers.shelf.load_path import foot_controls, pedal_face

    given = (load_case.get("params") or {}).get("foot_parts")
    ids = [s.strip() for s in given.split(",")] if given else [p["id"] for p in foot_controls(design)]
    if not ids:
        raise BuildError("the design has no pedal")
    reg = formula_registry()
    F = _force(load_case, "foot_force", 500.0)
    best = None
    for pid in ids:
        face, n = pedal_face(next(p for p in design["parts"] if p["id"] == pid))
        inputs = chain_inputs(design, pid, face, F, [n])[0]
        d = reg.evaluate("load_path.control_deflection", inputs)["outputs"]["delta"]["value"]
        if best is None or d > best[0]:
            best = (d, inputs)
    return best[1]


def free_inputs(design: Record, load_case: Record) -> Record:
    """load_path.free_parts' inputs: the parts modelled and those a chain of contacts joins to a member."""
    from hiraku.solvers.shelf.load_path import contact_groups, find_contacts, modelled_parts

    held, _ = contact_groups(design, find_contacts(design))
    return {"n_parts": q(len(modelled_parts(design)), "1"), "n_held": q(len(held), "1")}


_CHAIN = ["部品は剛体。触れている所 (すき間 2 mm 以下) をボルト留めのばねとみる",
          "握る点 (踏む面) から枠までの、触れている所のいちばん短い鎖 1 本",
          "鎖が板を通るときは、荷重の来る所から板の中心までの板の帯の曲げとねじれ (中心で固定とみる: 仮定)",
          "着いた部材は、いちばん近い節からの片持ち (その節がほかに繋がらなければ、もう一方の端から)"]
_FREE = ["触れている所を部材からたどって、届かない部品を数える (carried_by は見ない)"]
FREE_PREDICTION = {"quantity": "free_parts", "formula_id": "load_path.free_parts", "output": "n", "assumptions": _FREE,
                   "inputs": {"n_parts": "from.n_parts", "n_held": "from.n_held"}, "inputs_from": free_inputs}
_CONTROL = {k: f"from.{k}" for k in ("F", "k_t", "k_r", "L", "E", "I", "delta_plate")}

R7_PLAN = {
    "tool": "load_path",
    "say": ("操作部から枠まで、触れている部品をたどります。繋がらない部品を数え、繋がっていれば、触れている所のばねを直列に"
            "つないだ鎖と、着いた部材の片持ちで、握る点と踏む面の動きを先に出します"),
    "predictions": [
        FREE_PREDICTION,
        {"quantity": "grip_deflection", "formula_id": "load_path.control_deflection", "output": "delta",
         "assumptions": _CHAIN, "inputs": _CONTROL, "inputs_from": grip_inputs},
        {"quantity": "pedal_deflection", "formula_id": "load_path.control_deflection", "output": "delta",
         "assumptions": _CHAIN + ["ペダルが 2 つ以上あれば、手計算でいちばん動くもの"], "inputs": _CONTROL,
         "inputs_from": pedal_inputs},
    ],
}
R8_PLAN = {
    "tool": "load_path",
    "say": "どの部品も、触れている物をたどって枠まで繋がっているかを先に数えます",
    "predictions": [FREE_PREDICTION],
}

BUILDERS = {"R7": r7_controls, "R8": r8_load_path}
PLANS = {"R7": R7_PLAN, "R8": R8_PLAN}

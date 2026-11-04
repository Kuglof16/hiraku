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
# (-16 %: the model shares the load over the parallel contacts). 30 %. Since the bulkhead is a 2.3 mm sheet
# (2026-11-04) the pedal on it bends the plate: with load_path.plate_strip K0 18.4 mm against the tool's 24.0 mm
# for that v1 case, 45.0 against 49.5 mm with version 2's stands on the bulkhead (tests/solvers/
# test_load_path_plate_k0). With the pedals on the post's beam (pedal_mount = post, 2026-11-04) the beam's twist
# (F r_t^2 L / (G J)) and the post node (load_path.post_node) were added: version 2 at track 1220, K0 0.282 mm
# (0.080 before: bending only) against the tool's 0.338 mm (+20 %). The rest is the frame under the post: in the
# tool the post's foot drops 0.087 mm and tips 2.5e-4 rad, which K0 takes as rigid. The twist term moved the
# demo's grip from 0.87 to 0.95 mm (the tool 1.05 mm, +10 %).
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
    mat = props[mid]["material"]
    E = float(mat["E_MPa"])
    G = E / (2.0 * (1.0 + float(mat.get("nu", 0.3))))
    r_t, d_root = 0.0, 0.0
    if chain is not None and L > 0.0:
        r_t, d_root, root_note = _twist_and_root(design, props, mid, last.t, near, s, p, F, directions)
        notes += root_note
    ct = sum(1.0 / k for k, _, _ in joints)
    cr = sum(float(np.dot(p - x, p - x)) / kr for _, kr, x in joints)
    k_r = 1.0 / cr if cr > 0 else 1e12  # every contact on the load's line: its rotation does not move the point
    return {"F": q(F, "N"), "k_t": q(1.0 / ct, "N/mm"), "k_r": q(k_r, "N/mm"), "L": q(L, "mm"), "E": q(E, "MPa"),
            "I": q(min(s["Iy"], s["Iz"]), "mm^4"), "delta_plate": q(d_plate, "mm"), "G": q(G, "MPa"),
            "J": q(float(s["J"]), "mm^4"), "r_t": q(r_t, "mm"), "delta_root": q(d_root, "mm")}, notes


def _column_at(design: Record, props: dict, node: str, mid: str):
    """The two other members at `node` when they make one straight column through it (the cantilever's root
    partway up a post): [(member, far node, length)] x 2, else None."""
    others = [m for m in design["members"] if m["id"] != mid and node in (m["node_a"], m["node_b"])]
    if len(others) != 2:
        return None
    xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    ends = [(m["id"], m["node_b"] if m["node_a"] == node else m["node_a"]) for m in others]
    d = [xyz[far] - xyz[node] for _, far in ends]
    u = [v / np.linalg.norm(v) for v in d]
    if float(np.dot(u[0], u[1])) > -1.0 + 1e-6 or props[ends[0][0]]["section"] != props[ends[1][0]]["section"]:
        return None
    return [(m, far, float(np.linalg.norm(v))) for (m, far), v in zip(ends, d)]


def _twist_and_root(design: Record, props: dict, mid: str, t: float, near: str, sec: Record, p, F: float,
                    directions) -> tuple[float, float, list[str]]:
    """control_deflection's r_t (the load's lever about the landing member's axis at the landing point) and
    delta_root (load_path.post_node, when the cantilever's root node is partway up a straight column), for the
    load direction that moves the point most of `directions` (none given: r_t 0, the root rigid)."""
    from hiraku.solvers import formula_registry

    if not directions:
        return 0.0, 0.0, []
    m = props[mid]["member"]
    xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    A_, B_ = xyz[m["node_a"]], xyz[m["node_b"]]
    axis = (B_ - A_) / np.linalg.norm(B_ - A_)
    land = A_ + t * (B_ - A_)
    others = {x["node_a"] for x in design["members"] if x["id"] != mid} | {x["node_b"] for x in design["members"]
                                                                         if x["id"] != mid}
    root = near if near in others else (m["node_b"] if near == m["node_a"] else m["node_a"])
    col = _column_at(design, props, root, mid)
    reg = formula_registry()
    E_m = float(props[mid]["material"]["E_MPa"])
    G_m = E_m / (2.0 * (1.0 + float(props[mid]["material"].get("nu", 0.3))))
    best = (-1.0, 0.0, 0.0)
    for u in directions:
        f = np.asarray(u, float)
        f = f / np.linalg.norm(f)
        r_t = abs(float(axis @ np.cross(p - land, f)))
        d_root = 0.0
        if col is not None:
            e = (xyz[col[1][1]] - xyz[root]) / col[1][2]
            u1 = np.cross(e, [0.0, 0.0, 1.0]) if abs(e[2]) < 0.9 else np.cross(e, [1.0, 0.0, 0.0])
            u1 /= np.linalg.norm(u1)
            u2 = np.cross(e, u1)
            Fv = F * f
            M = np.cross(p - xyz[root], Fv)
            cs, cm = props[col[0][0]]["section"], props[col[0][0]]["material"]
            Ec = float(cm["E_MPa"])
            I = min(cs["Iy"], cs["Iz"])
            ins = {"F": q(F, "N"), "N": q(float(Fv @ e), "N"), "T": q(float(M @ e), "N*mm"),
                   "P_1": q(float(Fv @ u1), "N"), "M_1": q(float(M @ np.cross(e, u1)), "N*mm"),
                   "P_2": q(float(Fv @ u2), "N"), "M_2": q(float(M @ np.cross(e, u2)), "N*mm"),
                   "a": q(col[0][2], "mm"), "b": q(col[1][2], "mm"), "E": q(Ec, "MPa"),
                   "G": q(Ec / (2.0 * (1.0 + float(cm.get("nu", 0.3)))), "MPa"), "A": q(float(cs["A"]), "mm^2"),
                   "I_1": q(I, "mm^4"), "I_2": q(I, "mm^4"), "J": q(float(cs["J"]), "mm^4")}
            d_root = reg.evaluate("load_path.post_node", ins)["outputs"]["delta"]["value"]
        L_tw = float(np.linalg.norm(land - xyz[root]))
        score = F * r_t ** 2 * L_tw / (G_m * float(sec["J"])) + d_root
        if score > best[0]:
            best = (score, r_t, d_root)
    notes = []
    if col is not None:
        notes.append(f"根元の節 {root} は柱 ({col[0][0]}, {col[1][0]}) の途中: 柱の両端 {col[0][1]}・{col[1][1]} を固定とみて"
                     "、その節の動きを足した (load_path.post_node、柱の I は小さい方を両方向に)")
    return best[1], best[2], notes


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


def _post_under(design: Record, props: dict, node: str, bar: str) -> tuple[float, Record | None]:
    """The post under a bar's end node: the chain of near-vertical members (|dz| at least 0.9 of their length) going
    down from `node`; (its height from the lowest node it reaches up to `node`, its first member's props), or
    (0, None) when nothing goes down from the node."""
    xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    cur, first, used = node, None, {bar}
    while True:
        nxt = None
        for m in design["members"]:
            if m["id"] in used or cur not in (m["node_a"], m["node_b"]):
                continue
            other = m["node_b"] if m["node_a"] == cur else m["node_a"]
            d = xyz[other] - xyz[cur]
            if d[2] < 0 and -d[2] >= 0.9 * float(np.linalg.norm(d)):
                nxt = (m["id"], other)
                break
        if nxt is None:
            break
        used.add(nxt[0])
        first = first or props[nxt[0]]
        cur = nxt[1]
    return float(xyz[node][2] - xyz[cur][2]), first


def push_inputs(design: Record, load_case: Record) -> Record:
    """load_path.bar_on_posts' inputs for the point R whose hand calculation moves the most (params push_force):
    the push lands on the first member outboard of the point (as the tool finds it, load_path.first_outboard)."""
    from hiraku.solvers import formula_registry
    from hiraku.solvers.k0_handcalc.vehicle import member_props
    from hiraku.solvers.shelf.load_path import RIDER_KINDS, first_outboard, occupant_trunks, point_r

    F = _force(load_case, "push_force", 725.0)
    props = member_props(design)
    xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    riders = {f"part:{p['id']}" for p in design.get("parts", []) if p["kind"] in RIDER_KINDS}
    reg = formula_registry()
    best, others = None, []
    for t in occupant_trunks(design):
        for name, x, out in point_r(t):
            hit = first_outboard(design, x, out, riders)
            if hit is None or not hit[0].startswith("member:"):
                others.append(f"{t['id']}.{name}: {hit[0] if hit else 'nothing'}")
                continue
            mid = hit[0].split(":", 1)[1]
            m = props[mid]["member"]
            A, B = xyz[m["node_a"]], xyz[m["node_b"]]
            at = x + hit[1] * out
            u = (B - A) / np.linalg.norm(B - A)
            a = float(np.clip((at - A) @ u, 1.0, props[mid]["length"] - 1.0))
            b = props[mid]["length"] - a
            ins = {"F": q(F, "N"), "a": q(a, "mm"), "b": q(b, "mm"),
                   "E": q(float(props[mid]["material"]["E_MPa"]), "MPa"),
                   "I": q(min(props[mid]["section"]["Iy"], props[mid]["section"]["Iz"]), "mm^4")}
            for end, nid in (("A", m["node_a"]), ("B", m["node_b"])):
                h, post = _post_under(design, props, nid, mid)
                post = post or props[mid]
                ins.update({f"h_{end}": q(h, "mm"), f"E_{end}": q(float(post["material"]["E_MPa"]), "MPa"),
                            f"I_{end}": q(min(post["section"]["Iy"], post["section"]["Iz"]), "mm^4")})
            d = reg.evaluate("load_path.bar_on_posts", ins)["outputs"]["delta"]["value"]
            if best is None or d > best[0]:
                best = (d, ins)
    if best is None:
        raise ValueError("no point R has a member outboard of it, so the hand calculation of the push has nothing to "
                         "bend (" + "; ".join(others) + ")")
    return best[1]


UNSPRUNG_KINDS = ("wheel", "suspension")  # ride on the wheels, not on the rails (R8-stress's hand calculation)


def _beam_moment(x_r: float, x_f: float, pts, moments, grid: np.ndarray) -> np.ndarray:
    """M(x) on grid of a beam on supports at x_r < x_f (overhangs free) under point loads (x, P) and point
    moments (x, C) (statics)."""
    L = x_f - x_r
    Rf = (sum(P * (x - x_r) for x, P in pts) - sum(C for _, C in moments)) / L
    Rr = sum(P for _, P in pts) - Rf
    M = np.zeros_like(grid)
    for x, P in pts:
        M += np.where(grid > x, P * (grid - x), 0.0)
    for x, C in moments:
        M += np.where(grid > x, -C, 0.0)
    M -= np.where(grid > x_r, Rr * (grid - x_r), 0.0)
    M -= np.where(grid > x_f, Rf * (grid - x_f), 0.0)
    return M


def _shaft_torque(x_r: float, x_f: float, torques, grid: np.ndarray) -> np.ndarray:
    """T(x) on grid of a shaft held against twist at x_r and x_f (a uniform shaft between them: a torque between
    goes to each end by the other part's length; one outside goes to the nearer end)."""
    T = np.zeros_like(grid)
    L = x_f - x_r
    for x, t in torques:
        if x <= x_r:
            T += np.where((grid > x) & (grid <= x_r), t, 0.0)
        elif x >= x_f:
            T += np.where((grid >= x_f) & (grid < x), -t, 0.0)
        else:
            T += np.where((grid > x_r) & (grid < x), t * (x_f - x) / L, 0.0)
            T += np.where((grid >= x) & (grid < x_f), -t * (x - x_r) / L, 0.0)
    return T


def stress_inputs(design: Record, load_case: Record) -> Record:
    """load_path.rail_utilisation's inputs at the rail point and combination of the load case's accelerations
    (params accel_vertical, accel_lateral, accel_longitudinal in g, safety_factor) that use it most. The rails are
    the members named rail_L_* / rail_R_*, on the supports' two axle stations. Every member (its own mass at its
    middle) and every part but the unsprung ones (UNSPRUNG_KINDS) loads them: up and down by the lever rule across
    the rails, sideways by m a_y h / s between them, fore and aft as a pitch moment m a_x h (each rail its share)
    and an axial force (half each); the twist of each load is its share times its offset across from the rail."""
    from hiraku.contract.loading import on_board
    from hiraku.solvers.k0_handcalc.units import STANDARD_GRAVITY
    from hiraku.solvers.k0_handcalc.vehicle import member_props

    params = load_case.get("params") or {}

    def val(k, default):
        v = params.get(k)
        return default if v is None else float(v["value"] if isinstance(v, dict) else v)

    av, al, alon = val("accel_vertical", 1.0), val("accel_lateral", 0.0), val("accel_longitudinal", 0.0)
    sf = val("safety_factor", 1.0)
    loading_id = params.get("loading")
    d = on_board(design, loading_id) if loading_id else design
    props = member_props(d)
    rails = {side: [m for m in props if m.startswith(f"rail_{side}_")] for side in "LR"}
    if not rails["L"] or not rails["R"]:
        raise ValueError("the design has no rails named rail_L_* and rail_R_*, so the ladder frame's hand "
                         "calculation does not apply")
    xyz = {n["id"]: np.asarray(n["xyz_mm"], float) for n in d["nodes"]}
    xs = sorted({float(xyz[s["frame_node"]][0]) for s in d.get("supports", [])})
    if len(xs) < 2:
        raise ValueError("the supports do not stand at two axle stations")
    x_r, x_f = xs[0], xs[-1]
    y = {side: float(np.mean([xyz[props[m]["member"]["node_a"]][1] for m in rails[side]])) for side in "LR"}
    z_r = float(np.mean([xyz[props[m]["member"]["node_a"]][2] for m in rails["L"] + rails["R"]]))
    s = y["L"] - y["R"]
    g = STANDARD_GRAVITY
    loads = []
    for mid, p in props.items():
        a, b = xyz[p["member"]["node_a"]], xyz[p["member"]["node_b"]]
        loads.append(((a + b) / 2.0, p["section"]["A"] * p["length"] * float(p["material"]["density_kg_m3"]) * 1e-9))
    for part in d.get("parts", []):
        if part["kind"] not in UNSPRUNG_KINDS:
            loads.append((np.asarray(part["box"]["center_mm"], float), float(part["mass_kg"])))
    rail = props[rails["L"][0]]
    dims = d["sections"][rail["member"]["section"]]["dims_mm"]
    sec = rail["section"]
    vertical_is_z = sec["cz"] >= sec["cy"]  # the local axis whose extreme fibre is h/2
    I_v, I_h = (sec["Iy"], sec["Iz"]) if vertical_is_z else (sec["Iz"], sec["Iy"])
    bw, hh, t = float(dims["b"]), float(dims["h"]), float(dims["t"])
    A_m = (bw - t) * (hh - t)
    grid = np.linspace(min(x[0] for x, _ in loads), max(x[0] for x, _ in loads), 3001)
    best = None
    for lon, lat in ((0.0, 0.0), (alon, 0.0), (0.0, al), (0.0, -al)):
        for side, sgn in (("L", 1.0), ("R", -1.0)):
            pts, mom, tor, side_pts = [], [], [], []
            for x, m in loads:
                share = (x[1] - y["R"]) / s if side == "L" else (y["L"] - x[1]) / s
                P = m * g * (av * share + sgn * lat * (x[2] - z_r) / s)
                pts.append((x[0], P))
                mom.append((x[0], m * g * lon * share * (x[2] - z_r)))
                tor.append((x[0], P * (x[1] - y[side])))
                side_pts.append((x[0], m * g * lat))
            N = sum(m * g * lon for _, m in loads) / 2.0
            Mv = np.abs(_beam_moment(x_r, x_f, pts, mom, grid))
            Mh = np.abs(_beam_moment(x_r, x_f, side_pts, [], grid))
            T = np.abs(_shaft_torque(x_r, x_f, tor, grid))
            sig = Mv * (hh / 2) / I_v + Mh * (s / 2 + bw / 2) / (2 * (sec["A"] * (s / 2) ** 2 + I_h)) + N / sec["A"]
            vm = np.sqrt(sig ** 2 + 3 * (T / (2 * A_m * t)) ** 2)
            k = int(np.argmax(vm))
            if best is None or vm[k] > best[0]:
                best = (float(vm[k]), float(Mv[k]), float(Mh[k]), float(T[k]), float(N))
    _, Mv, Mh, T, N = best
    return {"M_v": q(Mv, "N*mm"), "M_h": q(Mh, "N*mm"), "T": q(T, "N*mm"), "N": q(N, "N"), "b": q(bw, "mm"),
            "h": q(hh, "mm"), "t": q(t, "mm"), "A": q(float(sec["A"]), "mm^2"), "I_v": q(float(I_v), "mm^4"),
            "I_h": q(float(I_h), "mm^4"), "s": q(s, "mm"), "f_y": q(float(rail["material"]["yield_MPa"]), "MPa"),
            "SF": q(sf, "1")}


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
_CONTROL = {k: f"from.{k}" for k in ("F", "k_t", "k_r", "L", "E", "I", "delta_plate", "G", "J", "r_t", "delta_root")}

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
_PUSH = {k: f"from.{k}" for k in ("F", "a", "b", "E", "I", "h_A", "E_A", "I_A", "h_B", "E_B", "I_B")}
PUSH_PREDICTION = {
    "quantity": "occupant_push_deflection", "formula_id": "load_path.bar_on_posts", "output": "delta",
    "assumptions": ["点 R から外へ、いちばん先に当たる部材 (横の棒) を押す。点 R が 2 つ以上あれば、手計算でいちばん動くもの",
                    "棒は両端固定の梁、棒の両端を受ける柱は足からの片持ち (柱の足と、ほかの棒が柱をつなぐ分は入れない)"],
    "inputs": _PUSH, "inputs_from": push_inputs}
R8_PLAN = {
    "tool": "load_path",
    "say": ("どの部品も、触れている物をたどって枠まで繋がっているかを先に数えます。乗員の肩・腰を外へ押すときは、当たる横の棒"
            "と、その両端の柱の曲げで、動きを先に出します"),
    "predictions": [FREE_PREDICTION, PUSH_PREDICTION],
    # Measured (2026-11-04) on version 2 ({part_mounts: brackets, pedal_mount: post, side_guard: bars_net}): K0
    # 11.2 mm (the post under the bar's near end, a cantilever 951 mm from its foot, 10.4 of it) against the tool's
    # 7.0 mm. The tool's posts are held more than a free cantilever: the other bars and the top bar tie them to the
    # far post, and the roll bar's hoop stands beside them.
    "expected_mismatch": {
        "quantity": "occupant_push_deflection",
        "source": "the analysis department's run of version 2 with bars_net (K0 11.2 mm, load_path 7.0 mm), 2026-11-04",
        "cause": "手計算は、横の棒の両端の柱を足からの片持ち (柱の先は自由) とみている。実際の枠では、ほかの棒と上の棒が柱を"
                 "向こうの柱とつなぎ、柱の先の動きを押さえるので、動きは手計算より小さい",
        "fix": "柱の先をつなぐ棒の硬さを入れた柱の式が要る (いまは無い)。手計算は動きを大きめに出すので、安全側の値として合否を出す",
        "safe_side": True,
        "safe_side_reason": ("柱を自由な片持ちとみると、つなぐ棒の分だけ柔らかくなり、動きは大きめに出る (不利な側)。"
                             "基準は occupant_push_deflection <= Q78 なので、大きい方が不利"),
    },
}
# R8-stress on its own: the tool gives max_utilisation on R8-free and R8-push too (other loads, safety factor 1 on
# R8-free), so this prediction must not sit in R8_PLAN, which every R8 load case shares. It needs a plan looked up
# by the load case (PLANS["R8-stress"]: tools.plan looks a load case's id up before its test id).
_RAIL = {k: f"from.{k}" for k in ("M_v", "M_h", "T", "N", "b", "h", "t", "A", "I_v", "I_h", "s", "f_y", "SF")}
R8_STRESS_PLAN = {
    "tool": "load_path",
    "say": ("部品と乗員の重さと加速度を、はしご形の枠の 2 本のレールに分けて、上下・横の曲げ、軸の力、ねじれを合わせた応力で、"
            "許しに対する割合を先に出します"),
    "predictions": [FREE_PREDICTION, {
        "quantity": "max_utilisation", "formula_id": "load_path.rail_utilisation", "output": "u",
        "assumptions": ["レールだけを見る (横の部材・柱・ロールバーの応力は手計算に入れない)",
                        "レールは車軸の 2 点で支えた梁と軸。上下は、てこの比で 2 本に分け、横の加速度は m a_y h / s で片側へ移す。"
                        "前後は、高さの分のモーメント m a_x h とレールの軸の力 (半分ずつ)",
                        "ねじれは、各荷重のレールへの分を、レールの中心からの横のずれに掛けたもの (横の部材が固く付く: 大きめ)",
                        "車輪とサスペンションの腕は車輪に乗る (レールに載せない)",
                        "4 つの組 (下・下+前・下+左・下+右) のうち、いちばん大きい点"],
        "inputs": _RAIL, "inputs_from": stress_inputs}],
    # Measured (2026-11-04) on version 2 ({part_mounts: brackets, pedal_mount: post, side_guard: bars_net}, Q75
    # 0.7 g, Q76 0.9 g, Q61 1.5): K0 0.969 (rail R, down + forward) against the tool's 0.614 (cross_B, down + left;
    # the rails 0.597); v1 K0 0.919.
    "expected_mismatch": {
        "quantity": "max_utilisation",
        "source": "the analysis department's run of version 2 with bars_net (K0 0.969, load_path 0.614), 2026-11-04",
        "cause": "手計算は応力を大きめに出す: 上下・横・軸の応力を角で全部重ね、ねじれは横の部材が固く付くとみ、前後の力の"
                 "モーメントを高さの分だけレールに掛ける。ツールの枠では横の部材・床・柱が荷重を分けて持つ",
        "fix": "レールの応力を分ける枠の式は手計算に無い。手計算は大きめに出るので、安全側の値として合否を出す",
        "safe_side": True,
        "safe_side_reason": ("レールについては、応力を角で重ね、ねじれを固い横の部材で受けるとみる分、大きめに出る (不利な側)。"
                             "横の部材の応力は手計算に入れていない (ツールでは cross_B がいちばん大きい) ので、横の部材が"
                             "レールより使われる設計では、安全側とは言い切れない"),
    },
}

BUILDERS = {"R7": r7_controls, "R8": r8_load_path}
PLANS = {"R7": R7_PLAN, "R8": R8_PLAN, "R8-stress": R8_STRESS_PLAN}  # a load case's own plan before its test's

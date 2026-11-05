import math

import numpy as np

from hiraku.solvers.k0_handcalc import sections as secs
from hiraku.solvers.k4_geometry import rollbar
from hiraku.solvers.shelf.base import (LOADING, BuiltinTool, LoadCaseError, Param, Predicts, check_record, on_board,
                                       param_value, result)

AX = {"x": 3, "y": 4, "z": 5}


def local_k(L, E, G, A, Iy, Iz, J):
    k = np.zeros((12, 12))
    a = E * A / L
    k[0, 0] = k[6, 6] = a
    k[0, 6] = -a
    t = G * J / L
    k[3, 3] = k[9, 9] = t
    k[3, 9] = -t
    z1, z2, z3, z4 = 12 * E * Iz / L ** 3, 6 * E * Iz / L ** 2, 4 * E * Iz / L, 2 * E * Iz / L
    k[1, 1] = k[7, 7] = z1
    k[1, 7] = -z1
    k[1, 5] = k[1, 11] = z2
    k[5, 7] = k[7, 11] = -z2
    k[5, 5] = k[11, 11] = z3
    k[5, 11] = z4
    y1, y2, y3, y4 = 12 * E * Iy / L ** 3, 6 * E * Iy / L ** 2, 4 * E * Iy / L, 2 * E * Iy / L
    k[2, 2] = k[8, 8] = y1
    k[2, 8] = -y1
    k[2, 4] = k[2, 10] = -y2
    k[4, 8] = k[8, 10] = y2
    k[4, 4] = k[10, 10] = y3
    k[4, 10] = y4
    return np.triu(k) + np.triu(k, 1).T


def axes(pa, pb):
    """Local axes as K1: a vertical member's local y along x, otherwise local y up (section h along local y)."""
    ex = (pb - pa) / np.linalg.norm(pb - pa)
    ey = np.array([1.0, 0.0, 0.0]) if abs(ex[2]) > 0.999 else np.array([0.0, 0.0, 1.0])
    ey = ey - ex * float(ex @ ey)
    ey = ey / np.linalg.norm(ey)
    return np.vstack([ex, ey, np.cross(ex, ey)])


def caps_of(section, fy):
    """Plastic capacities {x: T_p, y: M_p about local y, z: M_p about local z} (N*mm); 0: no hinge."""
    sh = section.get("shape")
    d = section.get("dims_mm", {})
    r3 = math.sqrt(3.0)
    if sh == "rect_tube":
        b, h, t = d["b"], d["h"], d["t"]
        zz = (b * h ** 2 - (b - 2 * t) * (h - 2 * t) ** 2) / 4.0
        zy = (h * b ** 2 - (h - 2 * t) * (b - 2 * t) ** 2) / 4.0
        return {"x": 2 * (b - t) * (h - t) * t * fy / r3, "y": zy * fy, "z": zz * fy}
    if sh == "round_tube":
        D, t = d["d"], d["t"]
        z = (D ** 3 - (D - 2 * t) ** 3) / 6.0
        return {"x": 2 * math.pi * ((D - t) / 2.0) ** 2 * t * fy / r3, "y": z * fy, "z": z * fy}
    return {"x": 0.0, "y": 0.0, "z": 0.0}


def condensed(k, rel):
    if not rel:
        return k
    r = sorted(rel)
    kp = [i for i in range(12) if i not in rel]
    kc = np.zeros((12, 12))
    kc[np.ix_(kp, kp)] = k[np.ix_(kp, kp)] - k[np.ix_(kp, r)] @ np.linalg.pinv(k[np.ix_(r, r)]) @ k[np.ix_(r, kp)]
    return kc


def push(nodes, elems, fixed, load_node, direction, kind, target, collapse_travel=None, max_events=150):
    """Event-to-event elastic-plastic push of a 3D beam frame by one point load.

    kind 'force' (target N) or 'energy' (target N*mm). After a mechanism the load stays at the collapse load along
    the mechanism's shape: an energy push goes on until the area is the target; a force push above the collapse
    load goes on until the load point's travel is collapse_travel (or stops at the mechanism when None).
    Returns {F, d, U: {node: xyz}, hinges, mechanism, collapse_F}."""
    ids = list(nodes)
    ix = {n: i for i, n in enumerate(ids)}
    nd = 6 * len(ids)
    fixed_d = set()
    for n in fixed:
        fixed_d.update(range(6 * ix[n], 6 * ix[n] + 6))
    free = np.array([i for i in range(nd) if i not in fixed_d])
    pre = []
    for e in elems:
        pa, pb = np.asarray(nodes[e["a"]], float), np.asarray(nodes[e["b"]], float)
        R = axes(pa, pb)
        T = np.zeros((12, 12))
        for q in range(4):
            T[3 * q:3 * q + 3, 3 * q:3 * q + 3] = R
        k = local_k(float(np.linalg.norm(pb - pa)), e["E"], e["G"], e["A"], e["Iy"], e["Iz"], e["J"])
        dofs = list(range(6 * ix[e["a"]], 6 * ix[e["a"]] + 6)) + list(range(6 * ix[e["b"]], 6 * ix[e["b"]] + 6))
        pre.append({"T": T, "k": k, "dofs": dofs, "rel": set(), "f": np.zeros(12)})
    dirv = np.asarray(direction, float)
    dirv = dirv / np.linalg.norm(dirv)
    P = np.zeros(nd)
    ld = 6 * ix[load_node]
    P[ld:ld + 3] = dirv
    lam, U = 0.0, np.zeros(nd)
    path = [(0.0, 0.0, U.copy())]
    hinges, d0, kreg, mech_shape, done, area = [], None, None, None, None, 0.0
    for _ in range(max_events):
        K = np.zeros((nd, nd))
        kcs = []
        for p in pre:
            kc = condensed(p["k"], p["rel"])
            kcs.append(kc)
            K[np.ix_(p["dofs"], p["dofs"])] += p["T"].T @ kc @ p["T"]
        if kreg is None:
            kreg = 1e-11 * float(np.mean(np.abs(np.diag(K))))
        u = np.zeros(nd)
        u[free] = np.linalg.solve(K[np.ix_(free, free)] + kreg * np.eye(len(free)), P[free])
        du = float(u[ld:ld + 3] @ dirv)
        if d0 is None:
            d0 = du
        if not np.isfinite(du) or du > 1e4 * d0:
            mech_shape = u / du
            break
        cand = []
        for ei, (p, kc) in enumerate(zip(pre, kcs)):
            fu = kc @ (p["T"] @ u[p["dofs"]])
            p["fu"] = fu
            for end in (0, 1):
                for axn, off in AX.items():
                    i = 6 * end + off
                    cap = elems[ei]["caps"][axn]
                    if cap <= 0 or i in p["rel"] or abs(fu[i]) < 1e-12 * cap:
                        continue
                    m, dm = p["f"][i], fu[i]
                    dl = (cap - m) / dm if dm > 0 else (-cap - m) / dm
                    cand.append((max(dl, 0.0), ei, i, axn, end))
        dl = min(c[0] for c in cand) if cand else math.inf
        F0 = lam
        if kind == "force" and lam + dl >= target:
            U = U + (target - lam) * u
            lam = target
            done = (lam, float(U[ld:ld + 3] @ dirv), U.copy())
            break
        if kind == "energy":
            s_need = -F0 + math.sqrt(F0 ** 2 + 2 * (target - area) / du)
            if s_need <= dl:
                U = U + s_need * u
                lam = F0 + s_need
                done = (lam, float(U[ld:ld + 3] @ dirv), U.copy())
                break
            area += (F0 + dl / 2.0) * dl * du
        if not math.isfinite(dl):
            raise LoadCaseError("the frame takes the load elastically with no member end reaching its plastic capacity")
        U = U + dl * u
        lam += dl
        for p in pre:
            p["f"] = p["f"] + dl * p["fu"]
        for c in cand:
            if c[0] <= dl * (1 + 1e-9) + 1e-12:
                pre[c[1]]["rel"].add(c[2])
                hinges.append((elems[c[1]]["id"], c[4], c[3], lam))
        path.append((lam, float(U[ld:ld + 3] @ dirv), U.copy()))
    if done is None:
        if mech_shape is None:
            raise LoadCaseError("the push did not reach its target or a mechanism within the event limit")
        Fc, dc, Uc = path[-1]
        if kind == "energy":
            area_c = sum((path[i][0] + path[i + 1][0]) / 2 * (path[i + 1][1] - path[i][1])
                         for i in range(len(path) - 1))
            extra = (target - area_c) / Fc
        else:
            extra = max(collapse_travel - dc, 0.0) if collapse_travel is not None else 0.0
        done = (Fc, dc + extra, Uc + extra * mech_shape)
    F, d, Ufin = done
    return {"F": F, "d": d, "U": {n: Ufin[6 * ix[n]:6 * ix[n] + 3].tolist() for n in ids}, "hinges": hinges,
            "mechanism": mech_shape is not None, "collapse_F": path[-1][0] if mech_shape is not None else None}


def tube_elem(eid, a, b, section, mat):
    pr = secs.properties(section)
    E, nu = mat["E_MPa"], mat.get("nu", 0.3)
    return {"id": eid, "a": a, "b": b, "E": E, "G": E / (2 * (1 + nu)), "A": pr["A"], "Iy": pr["Iy"],
            "Iz": pr["Iz"], "J": pr["J"], "caps": caps_of(section, mat.get("yield_MPa", 0.0))}


class RopsPushover(BuiltinTool):
    name = "rops_pushover"
    title = "横転の保護枠: 車の骨組全体を梁にし、塑性ヒンジを一つずつ入れてロールバーを横・後ろ・上から押し、乗員の空間との隙間を測る"
    domain = "strength"
    base = "K1"
    tests = ["R4"]
    formulas = ["frame_static.rops_occupant_clearance", "frame_static.portal_frame_side",
                "frame_static.portal_frame_rear", "frame_static.portal_frame_top"]
    predicts = (Predicts("rops_occupant_clearance", "frame_static.rops_occupant_clearance", "rops_occupant_clearance"),
                Predicts("rops_side_deflection", "frame_static.portal_frame_side", "delta_E"),
                Predicts("rops_rear_deflection", "frame_static.portal_frame_rear", "delta_E"),
                Predicts("rops_top_deflection", "frame_static.portal_frame_top", "delta"))
    load_case_params = (
        Param("side_energy", "J", "energy the roll bar absorbs under the side load", required=True),
        Param("rear_energy", "J", "energy under the rear load", required=True),
        Param("crush_force", "N", "the downward crushing force on the top beam", required=True),
        Param("rear_fraction", "1", "the rear load point, as a fraction of the top beam's span from the left corner",
              default=1.0 / 6.0),
        LOADING,
    )

    def known_answers(self):
        tube_r = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.0}}
        steel = {"E_MPa": 205000.0, "nu": 0.3, "yield_MPa": 235.0}
        el = tube_elem("m", "a", "b", tube_r, steel)
        el["caps"] = {"x": 0.0, "y": 0.0, "z": 0.0}
        r1 = push({"a": [0, 0, 0], "b": [1000.0, 0, 0]}, [el], ["a"], "b", [0, 0, -1], "force", 1000.0)
        b, h, t = 40.0, 80.0, 2.0
        I = (b * h ** 3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12.0
        exp1 = 1000.0 * 1000.0 ** 3 / (3 * 205000.0 * I)
        tube_o = {"shape": "round_tube", "dims_mm": {"d": 42.7, "t": 2.3}}
        st2 = {"E_MPa": 205000.0, "nu": 0.3, "yield_MPa": 215.0}
        nodes = {"a": [0, 0, 0], "b": [0, 1000.0, 0], "c": [0, 0, 1000.0], "d": [0, 1000.0, 1000.0],
                 "m": [0, 500.0, 1000.0]}
        els = [tube_elem("p1", "a", "c", tube_o, st2), tube_elem("p2", "b", "d", tube_o, st2),
               tube_elem("t1", "c", "m", tube_o, st2), tube_elem("t2", "m", "d", tube_o, st2)]
        r2 = push(nodes, els, ["a", "b"], "c", [0, 1, 0], "force", 1e7)
        D, tt = 42.7, 2.3
        Mp = (D ** 3 - (D - 2 * tt) ** 3) / 6.0 * 215.0
        r3 = push(nodes, els, ["a", "b"], "m", [0, 0, -1], "force", 1e7)
        return [
            check_record("1000 N across the tip of a 1000 mm cantilever, rect tube 40 x 80 x 2", "tip_deflection",
                         exp1, r1["d"], "mm", 1e-3, "closed form delta = P L^3 / (3 E I), "
                         "I = (b h^3 - (b - 2t)(h - 2t)^3) / 12 (textbook beam theory)"),
            check_record("fixed-base portal 1000 x 1000 mm, tube 42.7 x 2.3, sideways load at a corner",
                         "collapse_load", 4 * Mp / 1000.0, r2["collapse_F"], "N", 1e-3,
                         "plastic analysis, sway mechanism of a fixed-base portal H_c = 4 M_p / h, "
                         "M_p = Z_p f_y, Z_p = (d^3 - (d - 2t)^3) / 6 (Neal, The Plastic Methods of Structural Analysis)"),
            check_record("the same portal, vertical load at mid-span of the beam", "collapse_load",
                         8 * Mp / 1000.0, r3["collapse_F"], "N", 1e-3,
                         "plastic analysis, beam mechanism of a fixed-ended beam V_c = 8 M_p / w (Neal)"),
        ]

    def analyse(self, design, load_case, params):
        d = on_board(design, load_case)
        nodes = {n["id"]: [float(x) for x in n["xyz_mm"]] for n in d["nodes"]}
        mats, sects = d.get("materials", {}), d.get("sections", {})
        top = next((m for m in d["members"] if m["id"] == "rollbar_top"), None)
        if top is None:
            raise LoadCaseError("the design has no member rollbar_top: no roll bar to load")
        ca, cb = top["node_a"], top["node_b"]
        left, right = (ca, cb) if nodes[ca][1] >= nodes[cb][1] else (cb, ca)
        f = param_value(load_case, "rear_fraction", "1")
        pa, pb = np.array(nodes[ca]), np.array(nodes[cb])
        fq = f if left == ca else 1.0 - f
        extra = sorted({fq, 0.5})
        top_ids = [ca] + [f"_top_{i}" for i in range(len(extra))] + [cb]
        for i, s in enumerate(extra):
            nodes[f"_top_{i}"] = (pa + s * (pb - pa)).tolist()
        q_node = top_ids[1 + extra.index(fq)]
        mid_node = top_ids[1 + extra.index(0.5)]
        elems = []
        for m in d["members"]:
            sec, mat = sects[m["section"]], mats[m["material"]]
            if m["id"] == "rollbar_top":
                for i in range(len(top_ids) - 1):
                    elems.append(tube_elem(f"rollbar_top#{i}", top_ids[i], top_ids[i + 1], sec, mat))
            else:
                elems.append(tube_elem(m["id"], m["node_a"], m["node_b"], sec, mat))
        used = set()
        for e in elems:
            used.update((e["a"], e["b"]))
        nodes = {k: v for k, v in nodes.items() if k in used}
        fixed = sorted({s["frame_node"] for s in d.get("supports", []) if s.get("frame_node") in nodes})
        if not fixed:
            raise LoadCaseError("no support has a frame_node on the frame: nothing to hold the chassis")
        post = next((m for m in d["members"] if m["id"].startswith("rollbar_") and m["id"] != "rollbar_top"
                     and left in (m["node_a"], m["node_b"])), None)
        h_post = abs(nodes[post["node_a"]][2] - nodes[post["node_b"]][2]) if post else nodes[left][2]
        E_side = param_value(load_case, "side_energy", "J") * 1000.0
        E_rear = param_value(load_case, "rear_energy", "J") * 1000.0
        F_top = param_value(load_case, "crush_force", "N")
        runs = {
            "side_left": (left, [0, -1, 0], "energy", E_side, "side"),
            "side_right": (right, [0, 1, 0], "energy", E_side, "side"),
            "rear_fwd": (q_node, [1, 0, 0], "energy", E_rear, "rear"),
            "rear_back": (q_node, [-1, 0, 0], "energy", E_rear, "rear"),
            "top": (mid_node, [0, 0, -1], "force", F_top, "top"),
        }
        design_nodes = {n["id"] for n in d["nodes"]}
        out, defl, notes = {}, {"side": 0.0, "rear": 0.0, "top": 0.0}, []
        for name, (ln, dv, kind, tgt, grp) in runs.items():
            r = push(nodes, elems, fixed, ln, dv, kind, tgt, collapse_travel=h_post)
            if r["mechanism"]:
                notes.append(f"{name}: a mechanism forms at {r['collapse_F']:.0f} N "
                             f"({len(r['hinges'])} hinges, first at {r['hinges'][0][0] if r['hinges'] else '-'})")
            disp = {k: v for k, v in r["U"].items() if k in design_nodes}
            curve = [[nodes[k][j] + r["U"][k][j] for j in range(3)] for k in top_ids]
            c = rollbar.occupant_clearance(d, displacements=disp, curves={"rollbar_top": curve})
            out[name] = (c["clearance_mm"], c["member"], c["occupant"])
            defl[grp] = max(defl[grp], r["d"])
        worst = min(out, key=lambda k: out[k][0])
        res = [result("rops_occupant_clearance", out[worst][0], "mm",
                      where=f"{worst}: {out[worst][1]} / {out[worst][2]}"),
               result("rops_side_deflection", defl["side"], "mm"),
               result("rops_rear_deflection", defl["rear"], "mm"),
               result("rops_top_deflection", defl["top"], "mm")]
        for k, v in out.items():
            res.append(result(f"rops_occupant_clearance.{k}", v[0], "mm", where=f"{v[1]} / {v[2]}"))
        return {"results": res, "assumptions": [
            "the whole frame (every member) as 3D Euler-Bernoulli beams, clamped at the supports' frame nodes "
            "(the chassis held at its wheel mountings; the suspension left out)",
            "plastic hinges event to event at member ends: bending about each local axis at Z_p f_y (rect and round "
            "tubes), torsion at the thin-wall plastic torque 2 A_m t f_y / sqrt(3); capacities independent, no "
            "hardening, small displacements; other section shapes stay elastic",
            "side: at a top corner across the vehicle, both sides; rear: along the vehicle at rear_fraction of the top "
            "beam from the left corner, both senses; top: down at mid-span. Each from the frame as built",
            "energy loads pushed until the area under force-deflection is the energy (on the collapse plateau after a "
            "mechanism); the crush pushed to the force, or a post's length along the mechanism when it collapses first",
            "clearance: k4_geometry.rollbar.occupant_clearance with every node moved and the top beam's deformed line",
        ] + notes}

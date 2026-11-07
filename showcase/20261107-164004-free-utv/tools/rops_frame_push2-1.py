import math

import numpy as np

from hiraku.solvers.k0_handcalc import portal_pushover as pp
from hiraku.solvers.k0_handcalc.sections import properties
from hiraku.solvers.k4_geometry import occupant_clearance
from hiraku.solvers.shelf.base import (LOADING, BuiltinTool, LoadCaseError, Param, Predicts, check_record,
                                       on_board, param_value, result)

GAP_PREFIXES = ("rollbar_", "rops_")


def plastic_caps(section, fy):
    """{"x": T_p, "y": M_py, "z": M_pz} (N*mm) of a thin tube; local y = the h direction (K1 axes)."""
    s, d = section["shape"], section["dims_mm"]
    if s == "round_tube":
        D, t = d["d"], d["t"]
        zp = (D ** 3 - (D - 2 * t) ** 3) / 6.0
        am = math.pi * (D - t) ** 2 / 4.0
        return {"x": 2 * am * t * fy / math.sqrt(3), "y": zp * fy, "z": zp * fy}
    if s == "rect_tube":
        b, h, t = d["b"], d["h"], d["t"]
        zp_z = (b * h ** 2 - (b - 2 * t) * (h - 2 * t) ** 2) / 4.0   # bending in the h direction
        zp_y = (h * b ** 2 - (h - 2 * t) * (b - 2 * t) ** 2) / 4.0
        am = (b - t) * (h - t)
        return {"x": 2 * am * t * fy / math.sqrt(3), "y": zp_y * fy, "z": zp_z * fy}
    return None


def held_nodes(design):
    return [s["frame_node"] for s in design.get("supports", []) if s.get("frame_node")]


def build_frame(design, rear_fraction):
    xyz = {n["id"]: np.array(n["xyz_mm"], float) for n in design["nodes"]}
    mats, secs = design.get("materials", {}), design.get("sections", {})
    top = next((m for m in design["members"] if m["id"] == "rollbar_top"), None)
    if top is None:
        raise LoadCaseError("the design has no member rollbar_top (the roll bar's top beam)")
    a, b = top["node_a"], top["node_b"]
    left, right = (a, b) if xyz[a][1] >= xyz[b][1] else (b, a)
    q = xyz[left] + rear_fraction * (xyz[right] - xyz[left])
    mid = 0.5 * (xyz[left] + xyz[right])
    held = set(held_nodes(design))
    if not held:
        raise LoadCaseError("the design's supports have no frame_node to hold the frame at")
    kept, left_out = [], []
    E = None
    for m in design["members"]:
        sec, mat = secs.get(m.get("section")), mats.get(m.get("material"))
        if sec is None or mat is None or sec.get("shape") not in ("round_tube", "rect_tube"):
            left_out.append(m["id"])
            continue
        if math.dist(xyz[m["node_a"]], xyz[m["node_b"]]) < 1e-9:
            left_out.append(m["id"])
            continue
        if E is None:
            E, nu = mat["E_MPa"], mat.get("nu", 0.3)
        kept.append((m, sec, mat))
    adj = {}
    for m, _, _ in kept:
        adj.setdefault(m["node_a"], set()).add(m["node_b"])
        adj.setdefault(m["node_b"], set()).add(m["node_a"])
    reach, todo = set(), [n for n in held if n in adj]
    while todo:
        n = todo.pop()
        if n in reach:
            continue
        reach.add(n)
        todo.extend(adj.get(n, ()) - reach)
    fr = pp.Frame(E, E / (2 * (1 + nu)))
    caps, free_members = [], []
    for m, sec, mat in kept:
        if m["node_a"] not in reach:
            free_members.append(m["id"])
            continue
        p = properties(sec)
        props = {"A": p["A"], "Iy": p["Iy"], "Iz": p["Iz"], "J": p["J"]}
        cap = plastic_caps(sec, mat["yield_MPa"])
        if m["id"] == "rollbar_top":
            fr.nodes["rops_q"] = q
            fr.nodes["rops_mid"] = mid
            for nm in (left, right):
                fr.nodes[nm] = xyz[nm]
            for k, (u, v) in enumerate(((left, "rops_q"), ("rops_q", "rops_mid"), ("rops_mid", right))):
                fr.members.append((f"rollbar_top#{k}", u, v, dict(props)))
                caps.append(dict(cap))
            continue
        fr.nodes[m["node_a"]] = xyz[m["node_a"]]
        fr.nodes[m["node_b"]] = xyz[m["node_b"]]
        fr.members.append((m["id"], m["node_a"], m["node_b"], props))
        caps.append(cap)
    for n in held:
        if n in fr.nodes:
            fr.fixed.add(n)
    posts = [m for m in design["members"] if m["id"].startswith("rollbar_post")]
    post_len = max((math.dist(xyz[m["node_a"]], xyz[m["node_b"]]) for m in posts), default=0.0)
    return {"frame": fr, "caps": caps, "left": left, "right": right, "top": top, "xyz": xyz,
            "reach": reach, "left_out": left_out, "free_members": free_members, "post_len": post_len, "E": E}


def unsupported(fr, load_node, direction):
    """1 when a unit push at load_node does not reach the held nodes: the node is not joined to them, the
    frame's stiffness is singular, or the held nodes' reactions do not balance it (linear elastic statics)."""
    if load_node not in fr.nodes or not fr.fixed:
        return 1
    names = list(fr.nodes)
    ix = {n: i for i, n in enumerate(names)}
    K = np.zeros((6 * len(names), 6 * len(names)))
    G = fr.G if hasattr(fr, "G") else None
    for name, a, b, p in fr.members:
        xa, xb = np.asarray(fr.nodes[a], float), np.asarray(fr.nodes[b], float)
        L = float(np.linalg.norm(xb - xa))
        e = (xb - xa) / L
        y, z = pp.member_axes(e)
        R = np.vstack([e, y, z])
        T = np.kron(np.eye(4), R)
        k = T.T @ pp.local_stiffness(L, fr.E, G, p) @ T
        dofs = list(range(6 * ix[a], 6 * ix[a] + 6)) + list(range(6 * ix[b], 6 * ix[b] + 6))
        K[np.ix_(dofs, dofs)] += k
    fixed = [d for n in fr.fixed for d in range(6 * ix[n], 6 * ix[n] + 6)]
    fixed_set = set(fixed)
    free = [d for d in range(K.shape[0]) if d not in fixed_set]
    F = np.zeros(K.shape[0])
    dvec = np.asarray(direction, float) / np.linalg.norm(direction)
    F[6 * ix[load_node]:6 * ix[load_node] + 3] = dvec
    Kff = K[np.ix_(free, free)]
    if np.linalg.cond(Kff) > 1e14:
        return 1
    u = np.zeros(K.shape[0])
    u[free] = np.linalg.solve(Kff, F[free])
    reac = K[fixed, :] @ u
    tot = np.zeros(3)
    for i in range(0, len(fixed), 6):
        tot += reac[i:i + 3]
    return 0 if np.linalg.norm(tot + dvec) < 1e-6 else 1


def push_to(model, load_node, direction, kind, target):
    fr = model["frame"]
    watch = list(fr.nodes)
    res = pp.push(fr, model["caps"], load_node, direction, watch)
    path = res["path"]
    if kind == "force" and res["mechanism"] and target >= path[-1][0]:
        Uc, shape, extra = res["U"][-1], res["shape"], model["post_len"]
        U = {n: np.asarray(Uc[n]) + extra * np.asarray(shape[n]) for n in Uc}
        return {"d": path[-1][1] + extra, "U": U, "beyond": True}
    f = pp.follow(res, kind, target)
    return {"d": f["d"], "U": f["U"], "beyond": f["beyond"]}


def gap(design, model, U):
    xyz = model["xyz"]
    disp = {n: [float(c) for c in U[n]] for n in U if n in xyz}
    pos = {n: xyz[n] + np.asarray(U.get(n, np.zeros(3))) for n in xyz}
    qm = [model["frame"].nodes[n] + np.asarray(U.get(n, np.zeros(3))) for n in ("rops_q", "rops_mid")]
    top = model["top"]
    pts = [pos[model["left"]], qm[0], qm[1], pos[model["right"]]]
    if top["node_a"] != model["left"]:
        pts = pts[::-1]
    ids = [m["id"] for m in design["members"] if m["id"].startswith(GAP_PREFIXES)]
    r = occupant_clearance(design, displacements=disp, members=ids,
                           curves={top["id"]: [[float(c) for c in p] for p in pts]})
    return float(r["clearance_mm"]), r.get("member")


def tube_portal(h, w, fy):
    """A fixed-base portal of round tube 42.7 x 2.3 held at its feet (design records), for the known answers."""
    nodes = {"FL": (0, w / 2, 0), "FR": (0, -w / 2, 0), "CL": (0, w / 2, h), "CR": (0, -w / 2, h)}
    return {"nodes": [{"id": k, "xyz_mm": [float(c) for c in v]} for k, v in nodes.items()],
            "members": [{"id": "rollbar_post_L", "node_a": "FL", "node_b": "CL", "section": "s", "material": "m"},
                        {"id": "rollbar_post_R", "node_a": "FR", "node_b": "CR", "section": "s", "material": "m"},
                        {"id": "rollbar_top", "node_a": "CL", "node_b": "CR", "section": "s", "material": "m"}],
            "sections": {"s": {"shape": "round_tube", "dims_mm": {"d": 42.7, "t": 2.3}}},
            "materials": {"m": {"E_MPa": 205000.0, "nu": 0.3, "yield_MPa": fy}},
            "parts": [], "supports": [{"id": "a", "frame_node": "FL"}, {"id": "b", "frame_node": "FR"}]}


class RopsFramePush2(BuiltinTool):
    name = "rops_frame_push2"
    title = ("横転したときの乗員の空間: 骨組の全体 (ロールバー・前の柱・後ろの支え・レール) を車輪の所で固定し、"
             "ロールバーを横・後ろから押してエネルギーを吸わせ、上から押しつぶして (塑性ヒンジを順に入れる)、"
             "乗員の空間との距離を測る。押す力が支えまで届くか (unsupported_load) も出す")
    domain = "crash"
    base = "K1"
    tests = ["C3"]
    formulas = ["frame_static.rops_occupant_clearance", "frame_static.portal_frame_side",
                "frame_static.portal_frame_rear", "frame_static.portal_frame_top", "load_path.free_parts"]
    predicts = (
        Predicts("rops_occupant_clearance", "frame_static.rops_occupant_clearance", "rops_occupant_clearance",
                 "the hand calculation pushes the roll bar alone (portal on its feet); the tool pushes the whole "
                 "frame held at the wheels' frame nodes (front posts, roof rails and rear stays share the load) and "
                 "measures every rollbar_/rops_ member's gap to k4_geometry.occupant_spaces"),
        Predicts("rops_side_deflection", "frame_static.portal_frame_side", "delta_E",
                 "intermediate: the loaded corner's travel across the vehicle at side_energy"),
        Predicts("rops_rear_deflection", "frame_static.portal_frame_rear", "delta_E",
                 "intermediate: the rear load point's travel along the vehicle at rear_energy"),
        Predicts("rops_top_deflection", "frame_static.portal_frame_top", "delta",
                 "intermediate: the top beam's mid-span travel down at crush_force (the post's length on collapse)"),
        Predicts("free_parts", "load_path.free_parts", "n",
                 "the path check: members not joined to the held nodes (the push loads members, not parts)"),
    )
    load_case_params = (
        LOADING,
        Param("side_energy", "J", "energy the side push must absorb", default=875.0),
        Param("rear_energy", "J", "energy the rear push must absorb", default=700.0),
        Param("crush_force", "N", "force down at the top beam's middle", default=10000.0),
        Param("rear_fraction", "1", "rear load point from the left corner over the top beam's span",
              default=1.0 / 6.0),
    )

    def known_answers(self):
        fy = 235.0
        mp = (42.7 ** 3 - (42.7 - 2 * 2.3) ** 3) / 6.0 * fy
        m = build_frame(tube_portal(1000.0, 1000.0, fy), 1.0 / 6.0)
        side = pp.push(m["frame"], m["caps"], m["left"], [0, -1, 0], list(m["frame"].nodes))
        top = pp.push(m["frame"], m["caps"], "rops_mid", [0, 0, -1], list(m["frame"].nodes))
        held = unsupported(m["frame"], m["left"], [0, -1, 0])
        loose = tube_portal(1000.0, 1000.0, fy)
        loose["supports"] = [{"id": "a", "frame_node": "FL"}]
        loose["nodes"].append({"id": "X", "xyz_mm": [500.0, 0.0, 500.0]})
        loose["nodes"].append({"id": "Y", "xyz_mm": [500.0, 0.0, 900.0]})
        loose["members"].append({"id": "loose_bar", "node_a": "X", "node_b": "Y", "section": "s", "material": "m"})
        lm = build_frame(loose, 1.0 / 6.0)
        return [
            check_record("fixed-base portal h = w = 1000 mm, round tube 42.7 x 2.3, fy 235 MPa, pushed sideways at a "
                         "corner: sway mechanism", "collapse_load", 4 * mp / 1000.0, side["path"][-1][0], "N", 1e-3,
                         "closed form H_c = 4 M_p / h of the sway mechanism of a fixed-base portal, M_p = Z_p f_y, "
                         "Z_p = (d^3 - (d - 2t)^3) / 6 (B. G. Neal, The Plastic Methods of Structural Analysis; "
                         "J. Heyman, Plastic Design of Frames)"),
            check_record("the same portal, a force down at mid-span of the top beam: beam mechanism", "collapse_load",
                         8 * mp / 1000.0, top["path"][-1][0], "N", 1e-3,
                         "closed form P_c = 8 M_p / L of the beam mechanism (hinges at both beam ends and mid-span) "
                         "(Neal; Heyman)"),
            check_record("the same portal held at both feet, a push at a corner", "unsupported_load", 0.0,
                         float(held), "1", 0.0, "statics: the held feet carry the push (reactions balance it)"),
            check_record("a bar joined to nothing beside a portal held at one foot", "free_members", 1.0,
                         float(len(lm["free_members"])), "1", 0.0,
                         "graph search: a member with no chain of members to a held node is free"),
        ]

    def analyse(self, design, load_case, params):
        loaded = on_board(design, load_case)
        f = param_value(load_case, "rear_fraction", "1")
        Es = param_value(load_case, "side_energy", "J") * 1000.0
        Er = param_value(load_case, "rear_energy", "J") * 1000.0
        Fc = param_value(load_case, "crush_force", "N")
        model = build_frame(loaded, f)
        fr = model["frame"]
        if model["left"] not in model["reach"]:
            raise LoadCaseError("the roll bar's top beam is not joined to the held (wheel) nodes by members")
        cases = {"side": [(model["left"], [0, -1, 0]), (model["right"], [0, 1, 0])],
                 "rear": [("rops_q", [1, 0, 0]), ("rops_q", [-1, 0, 0])],
                 "top": [("rops_mid", [0, 0, -1])]}
        targets = {"side": ("energy", Es), "rear": ("energy", Er), "top": ("force", Fc)}
        best, n_unsup, notes = {}, 0, []
        for k, pushes in cases.items():
            for node, dvec in pushes:
                n_unsup += unsupported(fr, node, dvec)
                pr = push_to(model, node, dvec, *targets[k])
                c, where = gap(loaded, model, pr["U"])
                if k not in best or c < best[k][0]:
                    best[k] = (c, where, pr["d"], dvec, pr["beyond"])
        gov = min(best, key=lambda k: best[k][0])
        res = [result("rops_occupant_clearance", best[gov][0], "mm", where=best[gov][1])]
        res += [result(f"c_{k}", best[k][0], "mm", where=best[k][1]) for k in ("side", "rear", "top")]
        res += [result("rops_side_deflection", best["side"][2], "mm"),
                result("rops_rear_deflection", best["rear"][2], "mm"),
                result("rops_top_deflection", best["top"][2], "mm"),
                result("unsupported_load", float(n_unsup), "1"),
                result("free_parts", float(len(model["free_members"])), "1")]
        if best["top"][4]:
            notes.append("top push past the collapse load (mechanism): followed a further post length")
        return {"results": res, "assumptions": [
            "static pushes in the style of OECD Code 4 (borrowed), each from the frame as built (no set carried from "
            "one push to the next); side at either top corner inward, rear at rear_fraction of the top beam forward "
            "and backward, top down at mid-span; the smallest gap of each kept",
            "the whole frame as slender 3D beams (portal_pushover's element) held fixed (6 DOF) at the supports' "
            "frame nodes; small displacements; plastic hinges at member ends at Z_p f_y or T_p (thin tube, no "
            "interaction, no hardening); one E for every member (the first tube's material)",
            "the gap: k4_geometry.occupant_clearance over the rollbar_ and rops_ members with the moved nodes "
            "(members straight between nodes; the top beam through its split points)",
            "unsupported_load: pushes (of 5) whose unit load the held nodes do not carry in linear elastic statics "
            "(the load node not joined to them by members, a singular frame, or reactions not balancing it); "
            "free_parts: members with no chain of members to a held node (left out of the frame)",
            f"members left out (no tube section or material, or zero length): {', '.join(model['left_out']) or 'none'}",
            f"members not joined to the held nodes: {', '.join(model['free_members']) or 'none'}",
        ] + notes}


TOOL = RopsFramePush2()

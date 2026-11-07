import math

import numpy as np

from hiraku.solvers.k0_handcalc import portal_pushover as pp
from hiraku.solvers.k0_handcalc.sections import properties
from hiraku.solvers.k4_geometry.rollbar import occupant_clearance
from hiraku.solvers.shelf.base import (LOADING, BuiltinTool, LoadCaseError, Param, Predicts, check_record, on_board,
                                       param_value, result)

ROPS_PREFIXES = ("rollbar_", "rops_")


def plastic_caps(section, fy):
    """Plastic capacities {x: T_p, y: M_py, z: M_pz} (N*mm) of a thin tube; K1 local axes (y along h, z along b).
    Round tube Z_p = (d^3 - (d - 2t)^3) / 6; rect tube about z: b h^2 / 4 - (b - 2t)(h - 2t)^2 / 4, about y the same
    with b and h swapped; T_p = 2 A_m t f_y / sqrt 3 (Bredt, A_m the area inside the wall's mid line).
    Other shapes: no hinge (0)."""
    shape = section.get("shape")
    d = section.get("dims_mm", {})
    if shape == "round_tube":
        D, t = d["d"], d["t"]
        zp = (D ** 3 - (D - 2 * t) ** 3) / 6.0
        am = math.pi * (D - t) ** 2 / 4.0
        return {"x": 2 * am * t * fy / math.sqrt(3), "y": zp * fy, "z": zp * fy}
    if shape == "rect_tube":
        b, h, t = d["b"], d["h"], d["t"]
        zz = b * h ** 2 / 4.0 - (b - 2 * t) * (h - 2 * t) ** 2 / 4.0
        zy = h * b ** 2 / 4.0 - (h - 2 * t) * (b - 2 * t) ** 2 / 4.0
        am = (b - t) * (h - t)
        return {"x": 2 * am * t * fy / math.sqrt(3), "y": zy * fy, "z": zz * fy}
    return {"x": 0.0, "y": 0.0, "z": 0.0}


def frame_of(design, rear_fraction):
    """The whole frame as an event-to-event plastic frame, held fixed at the wheels' frame nodes; the roll bar's
    top beam split at the rear load point and at mid-span."""
    nodes = {n["id"]: np.array(n["xyz_mm"], dtype=float) for n in design["nodes"]}
    members = {m["id"]: m for m in design["members"]}
    if "rollbar_top" not in members:
        raise LoadCaseError("the design has no roll bar (member rollbar_top): C3 pushes the roll bar")
    top = members["rollbar_top"]
    a, b = top["node_a"], top["node_b"]
    left, right = (a, b) if nodes[a][1] >= nodes[b][1] else (b, a)
    mat = design["materials"][top["material"]]
    E, nu = float(mat["E_MPa"]), float(mat.get("nu", 0.3))
    fr = pp.Frame(E, E / (2 * (1 + nu)))
    for k, p in nodes.items():
        fr.nodes[k] = p
    fr.nodes["c3_q"] = nodes[left] + rear_fraction * (nodes[right] - nodes[left])
    fr.nodes["c3_mid"] = 0.5 * (nodes[left] + nodes[right])
    caps, skipped = [], []
    for m in design["members"]:
        try:
            sec = design["sections"][m["section"]]
            pr = properties(sec)
            fy = float(design["materials"][m["material"]]["yield_MPa"])
        except (KeyError, ValueError, TypeError):
            skipped.append(m["id"])
            continue
        if m["node_a"] not in nodes or m["node_b"] not in nodes:
            skipped.append(m["id"])
            continue
        cap = plastic_caps(sec, fy)
        props = {"A": pr["A"], "Iy": pr["Iy"], "Iz": pr["Iz"], "J": pr["J"],
                 "Mpy": cap["y"], "Mpz": cap["z"], "Tp": cap["x"]}
        if m["id"] == "rollbar_top":
            for i, (u, w) in enumerate([(left, "c3_q"), ("c3_q", "c3_mid"), ("c3_mid", right)]):
                fr.members.append((f"rollbar_top_{i}", u, w, dict(props)))
                caps.append(dict(cap))
        else:
            fr.members.append((m["id"], m["node_a"], m["node_b"], props))
            caps.append(cap)
    held = [s["frame_node"] for s in design.get("supports", []) if s.get("frame_node") in nodes]
    if not held:
        raise LoadCaseError("no support has a frame_node: the frame cannot be held where it stands on its wheels")
    for h in held:
        fr.fixed.add(h)
    used = {x for mem in fr.members for x in (mem[1], mem[2])}
    for k in list(fr.nodes):
        if k not in used:
            del fr.nodes[k]
    return fr, caps, {"left": left, "right": right, "q": "c3_q", "mid": "c3_mid", "held": held,
                      "skipped": skipped, "f": rear_fraction}


def post_length(design, corner):
    """Length of the roll bar post ending at this corner."""
    xyz = {n["id"]: np.array(n["xyz_mm"], dtype=float) for n in design["nodes"]}
    for m in design["members"]:
        if m["id"].startswith("rollbar_post") and corner in (m["node_a"], m["node_b"]):
            return float(np.linalg.norm(xyz[m["node_a"]] - xyz[m["node_b"]]))
    return None


def push_one(design, fr, caps, named, load_node, direction, kind, target):
    res = pp.push(fr, caps, load_node, np.array(direction, dtype=float), list(fr.nodes))
    at = pp.follow(res, kind, target)
    U = {k: np.array(v, dtype=float) for k, v in at["U"].items()}
    extra = 0.0
    if kind == "force" and at.get("beyond") and res.get("shape"):
        extra = post_length(design, named["left"]) or 0.0
        for k, s in res["shape"].items():
            U[k] = U.get(k, np.zeros(3)) + extra * np.array(s, dtype=float)
    return res, at, U, extra


def clearance_of(design, named, U, members):
    """k4_geometry.occupant_clearance with the moved nodes; the top beam drawn through its split points."""
    xyz = {n["id"]: np.array(n["xyz_mm"], dtype=float) for n in design["nodes"]}
    disp = {k: [float(x) for x in v] for k, v in U.items() if k in xyz}
    L, R = xyz[named["left"]], xyz[named["right"]]
    built = {named["left"]: L, "c3_q": L + named["f"] * (R - L), "c3_mid": 0.5 * (L + R), named["right"]: R}
    order = [named["left"], "c3_q", "c3_mid", named["right"]]
    curve = [[float(x) for x in built[k] + U.get(k, np.zeros(3))] for k in order]
    top = next(m for m in design["members"] if m["id"] == "rollbar_top")
    if top["node_a"] != order[0]:
        curve = list(reversed(curve))
    return occupant_clearance(design, displacements=disp, members=members, curves={"rollbar_top": curve})


class RopsFramePush(BuiltinTool):
    name = "rops_frame_push"
    title = "横転したときの乗員の空間: 骨組の全体 (ロールバー・前の柱・後ろの支え・レール) を車輪の所で固定し、ロールバーを横・後ろから押してエネルギーを吸わせ、上から押しつぶして (塑性ヒンジを順に入れる)、乗員の空間との距離を測る"
    domain = "crash"
    base = "K1"
    tests = ["C3"]
    formulas = ["frame_static.rops_occupant_clearance", "frame_static.portal_frame_side",
                "frame_static.portal_frame_rear", "frame_static.portal_frame_top"]
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
        fy, D, t, h, w = 235.0, 42.7, 2.3, 1000.0, 1000.0
        sec = {"shape": "round_tube", "dims_mm": {"d": D, "t": t}}
        mat = {"E_MPa": 205000.0, "nu": 0.3, "yield_MPa": fy, "density_kg_m3": 7850.0}
        design = {"nodes": [{"id": "fL", "xyz_mm": [0.0, w / 2, 0.0]}, {"id": "cL", "xyz_mm": [0.0, w / 2, h]},
                            {"id": "fR", "xyz_mm": [0.0, -w / 2, 0.0]}, {"id": "cR", "xyz_mm": [0.0, -w / 2, h]}],
                  "members": [{"id": "rollbar_post_L", "node_a": "fL", "node_b": "cL", "section": "s",
                               "material": "m"},
                              {"id": "rollbar_post_R", "node_a": "fR", "node_b": "cR", "section": "s",
                               "material": "m"},
                              {"id": "rollbar_top", "node_a": "cL", "node_b": "cR", "section": "s",
                               "material": "m"}],
                  "sections": {"s": sec}, "materials": {"m": mat},
                  "supports": [{"id": "a", "frame_node": "fL"}, {"id": "b", "frame_node": "fR"}], "parts": []}
        mp = (D ** 3 - (D - 2 * t) ** 3) / 6.0 * fy
        fr, caps, named = frame_of(design, 1.0 / 6.0)
        side = pp.push(fr, caps, named["left"], np.array([0.0, -1.0, 0.0]), list(fr.nodes))
        fr, caps, named = frame_of(design, 1.0 / 6.0)
        down = pp.push(fr, caps, named["mid"], np.array([0.0, 0.0, -1.0]), list(fr.nodes))
        return [
            check_record("fixed-base portal h = w = 1000 mm, round tube 42.7 x 2.3, fy 235 MPa, pushed sideways at a "
                         "corner: sway mechanism", "collapse_load", 4.0 * mp / h, side["path"][-1][0], "N", 1e-3,
                         "closed form H_c = 4 M_p / h of the sway mechanism of a fixed-base portal, M_p = Z_p f_y, "
                         "Z_p = (d^3 - (d - 2t)^3) / 6 (B. G. Neal, The Plastic Methods of Structural Analysis; "
                         "J. Heyman, Plastic Design of Frames)"),
            check_record("the same portal, a force down at mid-span of the top beam: beam mechanism", "collapse_load",
                         8.0 * mp / w, down["path"][-1][0], "N", 1e-3,
                         "closed form P_c = 8 M_p / L of the beam mechanism (hinges at both beam ends and mid-span) "
                         "(Neal; Heyman)"),
        ]

    def analyse(self, design, load_case, params):
        loaded = on_board(design, load_case)
        f = param_value(load_case, "rear_fraction", "1")
        E_side = param_value(load_case, "side_energy", "J") * 1000.0
        E_rear = param_value(load_case, "rear_energy", "J") * 1000.0
        F_top = param_value(load_case, "crush_force", "N")
        members = [m["id"] for m in loaded["members"] if m["id"].startswith(ROPS_PREFIXES)]
        out, defl, notes = {}, {}, []
        cases = [
            ("side", "left", [0.0, -1.0, 0.0], "energy", E_side),
            ("side", "right", [0.0, 1.0, 0.0], "energy", E_side),
            ("rear", "q", [1.0, 0.0, 0.0], "energy", E_rear),
            ("rear", "q", [-1.0, 0.0, 0.0], "energy", E_rear),
            ("top", "mid", [0.0, 0.0, -1.0], "force", F_top),
        ]
        named = None
        for name, where, direction, kind, target in cases:
            fr, caps, named = frame_of(loaded, f)
            res, at, U, extra = push_one(loaded, fr, caps, named, named[where], direction, kind, target)
            d = float(at["d"]) + extra
            c = clearance_of(loaded, named, U, members)
            if name not in out or c["clearance_mm"] < out[name]["clearance_mm"]:
                out[name] = {"clearance_mm": float(c["clearance_mm"]), "member": c["member"]}
            defl[name] = max(defl.get(name, 0.0), d)
            if at.get("beyond"):
                notes.append(f"{name} push toward {direction}: past the collapse load (mechanism)")
        worst = min(out, key=lambda k: out[k]["clearance_mm"])
        results = [result("rops_occupant_clearance", out[worst]["clearance_mm"], "mm", where=out[worst]["member"]),
                   result("c_side", out["side"]["clearance_mm"], "mm", where=out["side"]["member"]),
                   result("c_rear", out["rear"]["clearance_mm"], "mm", where=out["rear"]["member"]),
                   result("c_top", out["top"]["clearance_mm"], "mm", where=out["top"]["member"]),
                   result("rops_side_deflection", defl["side"], "mm"),
                   result("rops_rear_deflection", defl["rear"], "mm"),
                   result("rops_top_deflection", defl["top"], "mm")]
        return {"results": results,
                "assumptions": [
                    "static pushes in the style of OECD Code 4 (borrowed), each from the frame as built (no set "
                    "carried from one push to the next); side at either top corner inward, rear at rear_fraction of "
                    "the top beam forward and backward, top down at mid-span; the smallest gap of each kept",
                    "the whole frame as slender 3D beams held fixed (6 DOF) at the wheels' frame nodes "
                    + ", ".join(named["held"]) + "; small displacements; plastic hinges at member ends at Z_p f_y "
                    "or T_p (no interaction, no hardening); members of other shapes stay elastic",
                    "the gap: k4_geometry.occupant_clearance over the rollbar_ and rops_ members with the moved "
                    "nodes (members straight between nodes; the top beam through its split points)",
                    "a crushing force above the collapse load: the mechanism followed a further post length",
                    "members left out (no section or material): " + (", ".join(named["skipped"]) or "none"),
                ] + notes}


TOOL = RopsFramePush()

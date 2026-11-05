import math

import numpy as np

from hiraku.solvers.k0_handcalc import portal_pushover as pp
from hiraku.solvers.k0_handcalc import sections as secs
from hiraku.solvers.k0_handcalc.frame_inputs import plastic_modulus, plastic_torque
from hiraku.solvers.k4_geometry import occupant_clearance
from hiraku.solvers.shelf.base import (LOADING, BuiltinTool, LoadCaseError, Param, Predicts, check_record, on_board,
                                       param_value, result)

TOP = "rollbar_top"


def full_frame(design, f):
    """The whole design frame (every member) as a portal_pushover.Frame, the wheel frame nodes fixed, the top beam
    split at the rear load point (fraction f from the left corner) and at mid-span."""
    mats = design.get("materials", {})
    nu = 0.3
    E = None
    xyz = {n["id"]: np.array(n["xyz_mm"], dtype=float) for n in design["nodes"]}
    members = {m["id"]: m for m in design["members"]}
    if TOP not in members:
        raise LoadCaseError("the design has no member rollbar_top (the roll bar's top beam)")
    caps = []
    mlist = []
    for m in design["members"]:
        mat = mats.get(m.get("material"), {}) if m.get("material") else {}
        Em = float(mat.get("E_MPa", 205000.0))
        num = float(mat.get("nu", nu))
        fy = mat.get("yield_MPa")
        if fy is None:
            raise LoadCaseError(f"member {m['id']} has no yield_MPa in its material")
        if E is None:
            E, nu = Em, num
        sec = design["sections"][m["section"]]
        p = secs.properties(sec)
        zpz, zpy = plastic_modulus(sec)
        tp = plastic_torque(sec, float(fy) / math.sqrt(3.0))
        props = {"A": p["A"], "Iy": p["Iy"], "Iz": p["Iz"], "J": p["J"], "Mpy": zpy * float(fy),
                 "Mpz": zpz * float(fy), "Tp": tp}
        mlist.append((m, props))
    fr = pp.Frame(E, E / (2.0 * (1.0 + nu)))
    for k, v in xyz.items():
        fr.nodes[k] = v
    top = members[TOP]
    a, b = top["node_a"], top["node_b"]
    left, right = (a, b) if xyz[a][1] >= xyz[b][1] else (b, a)
    fr.nodes["q"] = xyz[left] + f * (xyz[right] - xyz[left])
    fr.nodes["mid"] = xyz[left] + 0.5 * (xyz[right] - xyz[left])
    for m, props in mlist:
        cap = {"x": props["Tp"], "y": props["Mpy"], "z": props["Mpz"]}
        if m["id"] == TOP:
            chain = [left, "q", "mid", right] if f < 0.5 else [left, "mid", "q", right]
            for i in range(3):
                fr.members.append((f"top_{i}", chain[i], chain[i + 1], props))
                caps.append(dict(cap))
        else:
            fr.members.append((m["id"], m["node_a"], m["node_b"], props))
            caps.append(dict(cap))
    held = [s.get("frame_node") for s in design.get("supports", []) if s.get("frame_node")]
    if not held:
        raise LoadCaseError("the design's supports name no frame_node to hold the frame on")
    for n in held:
        fr.fixed.add(n)
    h = float(np.linalg.norm(xyz[left] - xyz[_foot(design, members, left)]))
    return fr, caps, {"corner_L": left, "corner_R": right, "q": "q", "mid": "mid", "h": h,
                      "design_nodes": list(xyz)}


def _foot(design, members, corner):
    for mid, m in members.items():
        if mid.startswith("rollbar_post") and corner in (m["node_a"], m["node_b"]):
            return m["node_b"] if m["node_a"] == corner else m["node_a"]
    raise LoadCaseError(f"no rollbar_post member ends at the corner {corner}")


def clearance(design, U, nodes):
    missing = [n for n in nodes if n not in U]
    if missing:
        raise LoadCaseError(f"the push returned no displacement for nodes {missing[:5]}")
    disp = {n: [float(x) for x in U[n]] for n in nodes}
    c = occupant_clearance(design, displacements=disp)
    return float(c["clearance_mm"]), c.get("member"), c.get("occupant")


class RopsPushoverFrameAll(BuiltinTool):
    name = "rops_pushover_frame_all"
    title = "横転の保護枠: 骨組全体 (シャシーとロールバー) を塑性ヒンジで押し、全部の節を動かして乗員の空間との隙間を測る"
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
        Param("side_energy", "J", "energy the side loading puts in", default=875.0),
        Param("rear_energy", "J", "energy the rear loading puts in", default=700.0),
        Param("crush_force", "N", "the crushing force down on the top beam", default=10000.0),
        Param("rear_fraction", "1", "where the rear load acts along the top beam from the left corner", default=1 / 6),
        LOADING,
    )

    def known_answers(self):
        # a fixed-base portal (feet held), round tube, side load at a corner: the sway mechanism's collapse load
        d_o, t_o, fy, h = 48.6, 2.3, 215.0, 1000.0
        design = {"nodes": [{"id": "fL", "xyz_mm": [0.0, 400.0, 0.0]}, {"id": "cL", "xyz_mm": [0.0, 400.0, h]},
                            {"id": "cR", "xyz_mm": [0.0, -400.0, h]}, {"id": "fR", "xyz_mm": [0.0, -400.0, 0.0]}],
                  "members": [{"id": "rollbar_post_L", "node_a": "fL", "node_b": "cL", "section": "s", "material": "st"},
                              {"id": "rollbar_top", "node_a": "cL", "node_b": "cR", "section": "s", "material": "st"},
                              {"id": "rollbar_post_R", "node_a": "fR", "node_b": "cR", "section": "s", "material": "st"}],
                  "sections": {"s": {"shape": "round_tube", "dims_mm": {"d": d_o, "t": t_o}}},
                  "materials": {"st": {"E_MPa": 205000.0, "nu": 0.3, "yield_MPa": fy}},
                  "supports": [{"id": "a", "frame_node": "fL"}, {"id": "b", "frame_node": "fR"}]}
        fr, caps, named = full_frame(design, 1 / 6)
        res = pp.push(fr, caps, named["corner_L"], [0.0, -1.0, 0.0], list(fr.nodes))
        Hc = max(F for F, _ in res["path"])
        Zp = (d_o ** 3 - (d_o - 2 * t_o) ** 3) / 6.0
        return [check_record("fixed-base portal h 1000 mm, round tube 48.6 x 2.3, f_y 215 MPa, side load at a corner",
                             "side_collapse_load", 4.0 * Zp * fy / h, Hc, "N", 1e-3,
                             "plastic analysis of a fixed-base portal, sway mechanism H h = 4 M_p with M_p = Z_p f_y and "
                             "Z_p = (d^3 - (d - 2t)^3)/6 for a round tube (Neal, The Plastic Methods of Structural "
                             "Analysis; textbook plastic theory)")]

    def analyse(self, design, load_case, params):
        loaded = on_board(design, load_case)
        f = param_value(load_case, "rear_fraction", "1")
        E_side = param_value(load_case, "side_energy", "J") * 1000.0
        E_rear = param_value(load_case, "rear_energy", "J") * 1000.0
        F_top = param_value(load_case, "crush_force", "N")
        fr, caps, nm = full_frame(loaded, f)
        nodes = nm["design_nodes"]
        watch = list(fr.nodes)
        out = {}
        best = None
        for sgn in (-1.0, 1.0):
            res = pp.push(fr, caps, nm["corner_L"], [0.0, sgn, 0.0], watch)
            fo = pp.follow(res, "energy", E_side)
            c = clearance(loaded, fo["U"], nodes)
            if best is None or c[0] < best[0][0]:
                best = (c, fo, max(F for F, _ in res["path"]), sgn)
        out["side"] = best
        best = None
        for sgn in (1.0, -1.0):
            res = pp.push(fr, caps, nm["q"], [sgn, 0.0, 0.0], watch)
            fo = pp.follow(res, "energy", E_rear)
            c = clearance(loaded, fo["U"], nodes)
            if best is None or c[0] < best[0][0]:
                best = (c, fo, max(F for F, _ in res["path"]), sgn)
        out["rear"] = best
        res = pp.push(fr, caps, nm["mid"], [0.0, 0.0, -1.0], watch)
        Fc = max(F for F, _ in res["path"])
        fo = pp.follow(res, "force", F_top)
        if fo["beyond"] and res.get("shape") is not None:
            d_c = res["path"][-1][1]
            extra = max(nm["h"] - d_c, 0.0)
            U = {k: np.array(res["U"][-1][k]) + extra * np.array(res["shape"][k]) for k in res["U"][-1]}
            fo = {"d": d_c + extra, "F": Fc, "U": U, "beyond": True}
        c = clearance(loaded, fo["U"], nodes)
        out["top"] = (c, fo, Fc, -1.0)
        worst = min(out, key=lambda k: out[k][0][0])
        rs = [result("rops_occupant_clearance", out[worst][0][0], "mm", where=f"{worst}: {out[worst][0][1]}"),
              result("rops_side_deflection", float(out["side"][1]["d"]), "mm", where=nm["corner_L"]),
              result("rops_rear_deflection", float(out["rear"][1]["d"]), "mm", where="q"),
              result("rops_top_deflection", float(out["top"][1]["d"]), "mm", where="mid")]
        for k in ("side", "rear", "top"):
            rs.append(result(f"rops_clearance_{k}", out[k][0][0], "mm", where=str(out[k][0][1])))
            rs.append(result(f"rops_collapse_load_{k}", float(out[k][2]), "N"))
        return {"results": rs, "assumptions": [
            "the whole design frame (chassis and roll bar, every member) as slender 3D beams, the wheel frame nodes "
            "fully fixed; plastic hinges at member ends (portal_pushover's event-to-event push: bending about each "
            "local axis at Z_p f_y, torsion at the plastic torque with tau_y = f_y / sqrt 3, no interaction)",
            "side and rear: pushed until the energy is absorbed (both senses, the smaller clearance kept); top: pushed "
            "to the crushing force, and when the frame collapses below it, along the mechanism until the load point "
            "has travelled the post length (as the hand formula)",
            "each loading on the frame as built (no carried-over set); the clearance is k4_geometry's "
            "occupant_clearance with every design node moved (members straight between their nodes)"]}

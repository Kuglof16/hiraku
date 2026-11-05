import math

import numpy as np

from hiraku.solvers.k0_handcalc import portal_pushover as pp
from hiraku.solvers.k0_handcalc import sections as sec
from hiraku.solvers.k4_geometry.rollbar import occupant_clearance
from hiraku.solvers.shelf.base import (LOADING, BuiltinTool, LoadCaseError, Param, Predicts, check_record, on_board,
                                       param_value, result)

SQ3 = math.sqrt(3.0)
NOCAP = {"x": 0.0, "y": 0.0, "z": 0.0}


def plastic_caps(section, fy):
    """{x: T_p, y: M_py, z: M_pz} (N*mm) of a tube section at yield fy (MPa); zeros for other shapes (elastic)."""
    shape = section.get("shape")
    d = section.get("dims_mm", {})
    if shape == "rect_tube":
        b, h, t = d["b"], d["h"], d["t"]
        zz = b * h ** 2 / 4.0 - (b - 2 * t) * (h - 2 * t) ** 2 / 4.0
        zy = h * b ** 2 / 4.0 - (h - 2 * t) * (b - 2 * t) ** 2 / 4.0
        return {"x": 2.0 * (b - t) * (h - t) * t * fy / SQ3, "y": zy * fy, "z": zz * fy}
    if shape == "round_tube":
        D, t = d["d"], d["t"]
        z = (D ** 3 - (D - 2 * t) ** 3) / 6.0
        return {"x": 2.0 * math.pi * (D - t) ** 2 / 4.0 * t * fy / SQ3, "y": z * fy, "z": z * fy}
    return dict(NOCAP)


def build_frame(design, f):
    """The whole frame as a pushover Frame held fully at each support's frame node; rollbar_top split at the two
    rear load points (f of the span from each corner) and mid-span."""
    mats = design.get("materials", {})
    secs = design.get("sections", {})
    xyz = {n["id"]: np.array(n["xyz_mm"], float) for n in design["nodes"]}
    members = design["members"]
    top = next((m for m in members if m["id"] == "rollbar_top"), None)
    if top is None:
        raise LoadCaseError("the design has no member rollbar_top: no roll bar to push (R4)")
    mat0 = mats.get(top.get("material"), {})
    E = float(mat0.get("E_MPa", 205000.0))
    nu = float(mat0.get("nu", 0.3))
    fr = pp.Frame(E, E / (2.0 * (1.0 + nu)))
    a, b = top["node_a"], top["node_b"]
    L, R = (a, b) if xyz[a][1] >= xyz[b][1] else (b, a)
    for m in members:
        for k in (m["node_a"], m["node_b"]):
            if k not in fr.nodes:
                fr.node(k, xyz[k])
    extra = {"top_q_L": xyz[L] + f * (xyz[R] - xyz[L]), "top_mid": 0.5 * (xyz[L] + xyz[R]),
             "top_q_R": xyz[R] + f * (xyz[L] - xyz[R])}
    for k, p in extra.items():
        fr.node(k, p)
    caps, elastic = [], []

    def add(name, na, nb, m):
        s = secs.get(m["section"])
        if s is None:
            raise LoadCaseError(f"member {m['id']} has section {m['section']!r} that is not in design.sections")
        p = dict(sec.properties(s))
        fy = float(mats.get(m.get("material"), {}).get("yield_MPa", 0.0))
        c = plastic_caps(s, fy) if fy > 0 else dict(NOCAP)
        if c["y"] <= 0 and m["id"] not in elastic:
            elastic.append(m["id"])
        p.update({"Mpy": c["y"], "Mpz": c["z"], "Tp": c["x"]})
        fr.member(name, na, nb, p)
        caps.append(c)

    chain = [L, "top_q_L", "top_mid", "top_q_R", R]
    for m in members:
        if m["id"] == "rollbar_top":
            for i in range(4):
                add(f"rollbar_top#{i}", chain[i], chain[i + 1], m)
        else:
            add(m["id"], m["node_a"], m["node_b"], m)
    held = [s.get("frame_node") for s in design.get("supports", []) if s.get("frame_node") in fr.nodes]
    if len(held) < 2:
        raise LoadCaseError("fewer than two supports with a frame_node in the frame: nothing holds the frame")
    for k in held:
        fr.fixed.add(k)
    info = {"left": L, "right": R, "chain": chain, "held": held, "elastic": elastic,
            "xyz": {k: np.array(v, float) for k, v in fr.nodes.items()}, "top": top}
    return fr, caps, info


def pushed(design, f, load_node, direction, kind, target, travel_end=None):
    """Push one loading to the energy or force target: {d, F, U (every node), beyond, F_collapse, first_hinge}."""
    fr, caps, info = build_frame(design, f)
    node = info[load_node] if load_node in ("left", "right") else load_node
    res = pp.push(fr, caps, node, np.array(direction, float), list(fr.nodes))
    at = pp.follow(res, kind, target)
    U = {k: np.array(v, float) for k, v in at["U"].items()}
    d = float(at["d"])
    if kind == "force" and at.get("beyond") and travel_end is not None and res.get("shape"):
        more = max(travel_end - d, 0.0)
        U = {k: U[k] + more * np.array(res["shape"][k], float) for k in U}
        d = travel_end
    return {"d": d, "U": U, "beyond": bool(at.get("beyond")), "F_collapse": max(F for F, _ in res["path"]),
            "first_hinge": res["hinges"][0][0] if res["hinges"] else None, "info": info}


def gap(design, run):
    """occupant_clearance with every design node moved and rollbar_top bent through its split points."""
    info, U = run["info"], run["U"]
    disp = {k: U[k].tolist() for k in U if not k.startswith("top_")}
    chain = info["chain"]
    pos = [(info["xyz"][k] + U[k]).tolist() for k in chain]
    if info["top"]["node_a"] != chain[0]:
        pos = pos[::-1]
    c = occupant_clearance(design, displacements=disp, curves={"rollbar_top": pos})
    return float(c["clearance_mm"]), c.get("member")


class RopsPushover(BuiltinTool):
    name = "rops_frame_pushover"
    title = "横転の保護枠: 車体の骨組全部を塑性ヒンジで押し、動いた形で乗員の空間との隙間を測る"
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
        Param("side_energy", "J", "energy the side load puts in", default=875.0),
        Param("rear_energy", "J", "energy the rear load puts in", default=700.0),
        Param("crush_force", "N", "the crushing force down on the top beam", default=10000.0),
        Param("rear_fraction", "1", "the rear load point from the near corner, as a share of the span",
              default=1.0 / 6.0),
        LOADING,
    )

    def known_answers(self):
        h, w = 1000.0, 1000.0
        sect = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 2.3}}
        steel = {"E_MPa": 205000.0, "nu": 0.3, "yield_MPa": 215.0}
        pts = {"fL": (0, w / 2, 0), "cL": (0, w / 2, h), "cR": (0, -w / 2, h), "fR": (0, -w / 2, 0)}
        d = {"nodes": [{"id": k, "xyz_mm": [float(x) for x in p]} for k, p in pts.items()],
             "members": [{"id": "rollbar_post_L", "node_a": "fL", "node_b": "cL", "section": "s", "material": "st"},
                         {"id": "rollbar_top", "node_a": "cL", "node_b": "cR", "section": "s", "material": "st"},
                         {"id": "rollbar_post_R", "node_a": "fR", "node_b": "cR", "section": "s", "material": "st"}],
             "sections": {"s": sect}, "materials": {"st": steel},
             "supports": [{"id": "a", "frame_node": "fL"}, {"id": "b", "frame_node": "fR"}], "parts": []}
        Zp = 40.0 * 40.0 ** 2 / 4.0 - 35.4 * 35.4 ** 2 / 4.0
        Mp = Zp * 215.0
        side = pushed(d, 1.0 / 6.0, "left", [0.0, -1.0, 0.0], "energy", 1.0)
        topr = pushed(d, 1.0 / 6.0, "top_mid", [0.0, 0.0, -1.0], "energy", 1.0)
        return [
            check_record("fixed-base square portal 1000 x 1000 mm, square tube 40 x 40 x 2.3 mm, f_y 215 MPa: "
                         "side load at a top corner, collapse load", "rops_side_collapse_load", 4.0 * Mp / h,
                         side["F_collapse"], "N", 1e-3,
                         "closed form, textbook plastic analysis: sway mechanism of a fixed-base portal "
                         "H_c = 4 M_p / h, M_p = Z_p f_y, Z_p = (b h^2 - (b - 2t)(h - 2t)^2) / 4 "
                         "(Neal, The Plastic Methods of Structural Analysis), worked out by hand"),
            check_record("the same portal: load down at mid-span of the top beam, collapse load",
                         "rops_top_collapse_load", 8.0 * Mp / w, topr["F_collapse"], "N", 1e-3,
                         "closed form, textbook plastic analysis: beam mechanism with a central load "
                         "V_c = 8 M_p / L (Neal, The Plastic Methods of Structural Analysis), worked out by hand"),
        ]

    def analyse(self, design, load_case, params):
        d = on_board(design, load_case)
        f = param_value(load_case, "rear_fraction", "1")
        Es = param_value(load_case, "side_energy", "J") * 1000.0
        Er = param_value(load_case, "rear_energy", "J") * 1000.0
        Fc = param_value(load_case, "crush_force", "N")
        xyz = {n["id"]: np.array(n["xyz_mm"], float) for n in d["nodes"]}
        post = next((m for m in d["members"] if m["id"].startswith("rollbar_post")), None)
        h_post = float(np.linalg.norm(xyz[post["node_b"]] - xyz[post["node_a"]])) if post else None
        runs = {
            "side": pushed(d, f, "left", [0.0, -1.0, 0.0], "energy", Es),
            "side_mirror": pushed(d, f, "right", [0.0, 1.0, 0.0], "energy", Es),
            "rear": pushed(d, f, "top_q_L", [1.0, 0.0, 0.0], "energy", Er),
            "rear_mirror": pushed(d, f, "top_q_R", [1.0, 0.0, 0.0], "energy", Er),
            "top": pushed(d, f, "top_mid", [0.0, 0.0, -1.0], "force", Fc, travel_end=h_post),
        }
        gaps = {k: gap(d, r) for k, r in runs.items()}
        worst = min(gaps, key=lambda k: gaps[k][0])
        out = [result("rops_occupant_clearance", gaps[worst][0], "mm", where=gaps[worst][1])]
        out += [result(f"rops_occupant_clearance.{k}", g[0], "mm", where=g[1]) for k, g in gaps.items()]
        out.append(result("rops_side_deflection", runs["side"]["d"], "mm", where=runs["side"]["info"]["left"]))
        out.append(result("rops_rear_deflection", runs["rear"]["d"], "mm", where="top_q_L"))
        out.append(result("rops_top_deflection", runs["top"]["d"], "mm", where="top_mid"))
        for k in ("side", "rear", "top"):
            out.append(result(f"rops_{k}_collapse_load", runs[k]["F_collapse"], "N", where=runs[k]["first_hinge"]))
        el = runs["side"]["info"]["elastic"]
        return {"results": out, "assumptions": [
            "the whole frame (every design member) as slender 3D beams, held fully (six ways) at each support's "
            "frame_node (tyres and suspension taken as rigid)",
            "plastic hinges event to event at member ends (hiraku.solvers.k0_handcalc.portal_pushover.push): "
            "M_p = Z_p f_y per bending axis, T_p = 2 A_m t f_y / sqrt(3) for tubes, independent"
            + (("; members kept elastic (no tube section or no yield): " + ", ".join(el)) if el else ""),
            "each loading from the frame as built; side pushed in at either top corner, rear pushed forward at "
            "rear_fraction of the span from either corner, top down at mid-span; the smallest gap kept",
            "top: when the frame collapses below crush_force, it is carried along its mechanism until the load point "
            "has travelled a post's length (as rops_inputs)",
            "the gap is k4_geometry.rollbar.occupant_clearance with every node moved and the top beam bent through "
            "its split points; members straight between their nodes otherwise",
            "small displacements (no change of geometry), no strain hardening"]}

import math

from hiraku.solvers.k1_frame import build
from hiraku.solvers.shelf.base import (G_FACTOR, BuiltinTool, LoadCaseError, Param, Predicts, check_record,
                                       param_text, param_value, result)

G0 = 9.80665  # standard gravity, m/s^2 (CGPM 1901)

TUBE = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.0}}
# example material for the known answer only (structural steel typical values, not checked); the closed form
# for the stress does not use E, so the answer holds whatever they are
STEEL = {"E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 7850.0, "yield_MPa": 235.0}


def simple_beam_center_stress(P, L, b, h, t):
    """Closed form: simply supported rect tube, load P [N] at mid-span, sigma = (P L / 4)(h / 2) / I."""
    I = (b * h ** 3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12.0
    return P * L / 4.0 * (h / 2.0) / I


def collinear(points):
    if len(points) < 3:
        return True
    p0 = points[0]
    far = max(points, key=lambda p: math.dist(p, p0))
    d = [far[i] - p0[i] for i in range(3)]
    n = math.sqrt(sum(x * x for x in d)) or 1.0
    for p in points:
        v = [p[i] - p0[i] for i in range(3)]
        c = [v[1] * d[2] - v[2] * d[1], v[2] * d[0] - v[0] * d[2], v[0] * d[1] - v[1] * d[0]]
        if math.sqrt(sum(x * x for x in c)) / n > 1e-6:
            return False
    return True


class PartMountStatic(BuiltinTool):
    name = "part_mount_static"
    title = "部品の取付: 部品の重さ x 倍率を載る節にかけ、その節に着く部材の最大の応力 (K1 の骨組、支えはピン)"
    domain = "strength"
    base = "K1"
    tests = ["R6"]
    formulas = ["frame_static.simple_beam_center_load"]
    predicts = (Predicts("battery_mount_max_stress", "frame_static.simple_beam_center_load", "max_stress"),
                Predicts("part_mount_max_stress", "frame_static.simple_beam_center_load", "max_stress"))
    load_case_params = (
        G_FACTOR,
        Param("part", None, "id of the part (design.parts) whose weight times g_factor loads its mount",
              default="battery"),
    )

    def _solve(self, design, part_id, g_factor):
        parts = {p["id"]: p for p in design.get("parts", [])}
        if part_id not in parts:
            raise LoadCaseError(f"part {part_id!r} is not in the design")
        part = parts[part_id]
        xyz = {n["id"]: [float(c) for c in n["xyz_mm"]] for n in design["nodes"]}
        nodes = [n for n in (part.get("carried_by") or []) if n in xyz]
        if not nodes:
            raise LoadCaseError(f"part {part_id!r} has no carried_by frame node")
        held = []
        for s in design.get("supports", []):
            fn = s.get("frame_node")
            if fn in xyz and fn not in held:
                held.append(fn)
        if not held:
            raise LoadCaseError("the design has no support with a frame_node to hold the frame")
        line = collinear([xyz[n] for n in held])
        fm = build(design)
        for i, fn in enumerate(held):
            # pinned: translations held. Only when all held nodes lie on one line (a single beam along design x,
            # PyNite X) is the twist about x held at one of them, so the beam cannot spin about its own axis
            fm.model.def_support(fn, True, True, True, line and i == 0, False, False)
        W = float(part["mass_kg"]) * G0 * g_factor
        point = [float(x) for x in part["box"]["center_mm"]]
        share = W / len(nodes)
        for n in nodes:
            fm.add_force_at(n, point, [0.0, 0.0, -share])
        fm.solve()
        on = [m["id"] for m in design["members"] if m["node_a"] in nodes or m["node_b"] in nodes]
        stresses = {mid: float(fm.member_stress(mid)["max"]) for mid in on}
        worst = max(stresses, key=stresses.get)
        return W, stresses, worst, nodes, held

    def known_answers(self):
        L = 400.0
        d = {"nodes": [{"id": "a", "xyz_mm": [0.0, 0.0, 100.0]}, {"id": "c", "xyz_mm": [L / 2, 0.0, 100.0]},
                       {"id": "b", "xyz_mm": [L, 0.0, 100.0]}],
             "members": [{"id": "m0", "node_a": "a", "node_b": "c", "section": "s", "material": "steel"},
                         {"id": "m1", "node_a": "c", "node_b": "b", "section": "s", "material": "steel"}],
             "sections": {"s": TUBE}, "materials": {"steel": STEEL},
             "parts": [{"id": "battery", "kind": "battery", "mass_kg": 100.0, "carried_by": ["c"],
                        "box": {"center_mm": [L / 2, 0.0, 100.0], "size_mm": [10.0, 10.0, 10.0]}}],
             "supports": [{"id": "sa", "kind": "caster", "position_mm": [0.0, 0.0, 50.0], "frame_node": "a",
                           "radius_mm": 50.0},
                          {"id": "sb", "kind": "caster", "position_mm": [L, 0.0, 50.0], "frame_node": "b",
                           "radius_mm": 50.0}]}
        lc = self.declared({"load_case_id": "check", "params": {"g_factor": {"value": 1.0, "unit": "1"},
                                                                "part": "battery"}})
        got = next(r["value"] for r in self.analyse(d, lc, {})["results"]
                   if r["quantity"] == "battery_mount_max_stress")
        expected = simple_beam_center_stress(100.0 * G0, L, 40.0, 80.0, 2.0)
        return [check_record("100 kg x 1 g at mid-span of a 400 mm rect tube 40 x 80 x 2 mm, pinned ends",
                             "battery_mount_max_stress", expected, got, "MPa", 1e-3,
                             "closed form for a simply supported beam with a centre point load, "
                             "M = P L / 4, sigma = M (h / 2) / I, I = (b h^3 - (b - 2t)(h - 2t)^3) / 12 "
                             "(textbook beam tables)")]

    def analyse(self, design, load_case, params):
        part_id = param_text(load_case, "part")
        gf = param_value(load_case, "g_factor", "1")
        W, stresses, worst, nodes, held = self._solve(design, part_id, gf)
        q = "battery_mount_max_stress" if part_id == "battery" else "part_mount_max_stress"
        res = [result(q, stresses[worst], "MPa", where=worst), result("mount_load", W, "N")]
        res += [result(f"{q}.{mid}", v, "MPa", where=mid) for mid, v in stresses.items()]
        return {"results": res,
                "assumptions": [f"quasi-static: the weight of {part_id} times g_factor {gf}, straight down, "
                                f"at its box centre, shared equally over its carried_by nodes {nodes}",
                                "only that part is loaded (the frame's own weight and the other parts are not)",
                                f"the frame is pinned at the supports' frame nodes {held} (translations held)",
                                "the stress is read on the members that end at those nodes"]}

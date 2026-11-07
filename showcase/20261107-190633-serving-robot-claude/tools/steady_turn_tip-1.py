import numpy as np

from hiraku.contract.loading import on_board
from hiraku.solvers.k0_handcalc.vehicle import mass_items, support_contact
from hiraku.solvers.shelf.base import (LOADING, BuiltinTool, LoadCaseError, Param, Predicts, check_record,
                                       param_text, result)

TUBE = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 2.0}}
# Example material for the known answer only (typical structural steel values, not checked here);
# the known answer does not depend on them (the frame's own mass is uniform).
STEEL = {"E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 7850.0, "yield_MPa": 235.0}


def hull_2d(points):
    """Convex hull (counter-clockwise) of 2D points, Andrew's monotone chain."""
    pts = sorted(set((float(p[0]), float(p[1])) for p in points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def edge_y_at(hull, x, side):
    """The hull's boundary y on the vertical line through x: the largest (side +1, left) or smallest
    (side -1, right) y of the hull's edges crossing that line."""
    ys = []
    n = len(hull)
    for i in range(n):
        (x1, y1), (x2, y2) = hull[i], hull[(i + 1) % n]
        if min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9:
            if abs(x2 - x1) < 1e-12:
                ys.extend([y1, y2])
            else:
                ys.append(y1 + (y2 - y1) * (x - x1) / (x2 - x1))
    if not ys:
        raise LoadCaseError("the centre of gravity is outside the supports' polygon (seen from above)")
    return max(ys) if side > 0 else min(ys)


def frame(points, edges, supports):
    return {"nodes": [{"id": k, "xyz_mm": [float(v) for v in p]} for k, p in points.items()],
            "members": [{"id": f"m{i}", "node_a": a, "node_b": b, "section": "s", "material": "steel"}
                        for i, (a, b) in enumerate(edges)],
            "sections": {"s": TUBE}, "materials": {"steel": STEEL}, "parts": [],
            "supports": [{"id": f"s_{k}", "kind": "caster", "position_mm": [float(v) for v in p],
                          "radius_mm": 50.0, "frame_node": k} for k, p in supports.items()]}


class SteadyTurnTip(BuiltinTool):
    name = "steady_turn_tip"
    title = "旋回での転倒: 定常円の横加速度で、合力が接地点の多角形の外へ出る (内側の輪が浮く) 横加速度 (剛体)"
    domain = "handling"
    base = "K0"
    tests = ["S2"]
    formulas = ["lane_change.inner_wheel_load"]
    predicts = (Predicts("tip_lateral_accel", "lane_change.inner_wheel_load", "a_y_lift"),)
    load_case_params = (
        LOADING,
        Param("direction", None, "which way the robot turns: both, left (tips to the right) or right", default="both"),
    )

    def known_answers(self):
        # a 1000 x 500 mm rectangle of uniform tube at z 600 mm on four casters at its corners (contacts at z 0):
        # the centre of gravity is 250 mm from each side line and 600 mm high
        pts = {"a": (0, 0, 600), "b": (1000, 0, 600), "c": (1000, 500, 600), "d": (0, 500, 600)}
        sup = {k: (p[0], p[1], 50) for k, p in pts.items()}
        d = frame(pts, [("a", "b"), ("b", "c"), ("c", "d"), ("d", "a")], sup)
        lc = self.declared({"load_case_id": "check", "params": {"loading": "empty", "direction": "both"}})
        got = min(r["value"] for r in self.analyse(d, lc, {})["results"] if r["quantity"] == "tip_lateral_accel")
        return [check_record("rigid rectangle 1000 x 500 mm, centre of gravity 600 mm high, four corner supports",
                             "tip_lateral_accel", 0.416667, got, "g", 1e-4,
                             "closed form: the rigid vehicle's rollover threshold a_y/g = t / (2 h) (static "
                             "stability factor), T. D. Gillespie, Fundamentals of Vehicle Dynamics, SAE 1992, "
                             "ch. 9; t = 500 mm, h = 600 mm worked out by hand")]

    def analyse(self, design, load_case, params):
        loaded = on_board(design, param_text(load_case, "loading"))
        items = mass_items(loaded)
        m = sum(it.mass_kg for it in items)
        if m <= 0:
            raise LoadCaseError("the design has no mass")
        cg = sum(it.mass_kg * np.asarray(it.cg_mm, dtype=float) for it in items) / m
        contacts = {s["id"]: np.asarray(support_contact(s), dtype=float) for s in loaded["supports"]}
        if len(contacts) < 3:
            raise LoadCaseError("fewer than three supports: the robot cannot stand by itself")
        ground_z = float(np.mean([c[2] for c in contacts.values()]))
        h = float(cg[2]) - ground_z
        if h <= 0:
            raise LoadCaseError("the centre of gravity is not above the ground")
        hull = hull_2d([(c[0], c[1]) for c in contacts.values()])
        direction = param_text(load_case, "direction")
        sides = {"both": (+1, -1), "right": (+1,), "left": (-1,)}.get(direction)
        if sides is None:
            raise LoadCaseError(f"direction {direction!r} is not both, left or right")
        res = []
        for side in sides:
            y_edge = edge_y_at(hull, float(cg[0]), side)
            a = side * (y_edge - float(cg[1])) / h
            # turning right throws the body to the left (side +1): the tip line is on the left
            res.append(result("tip_lateral_accel", a, "g", where="tips_left" if side > 0 else "tips_right"))
        return {"results": res + [result("cg_height", h, "mm"), result("mass", m, "kg")],
                "assumptions": ["rigid body on rigid supports (the design's supports are rigid), steady turn: "
                                "the lateral acceleration acts at the centre of gravity",
                                "tips when the resultant of weight and inertia leaves the polygon of the "
                                "ground contacts (seen from above), along the line through the centre of gravity",
                                "the robot does not slide first (friction not checked here)"]}

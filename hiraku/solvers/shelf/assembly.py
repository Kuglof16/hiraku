"""assembly (H1, H3, H5): interference at rest and through the wheels' travel and
steering, occupant fit against body-size statistics, and an assembly order."""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq

from ..k4_geometry import bodies, disassemble, fit, interferences, steps, sweep
from ..k4_geometry.assembly import is_attach
from ..k4_geometry.capsule import capsule_box_overlap, capsule_overlap, from_points
from ..k4_geometry.model import TOUCH_MM
from ..k4_geometry.motion import max_steer
from ..k4_geometry.obb import from_box, overlap
from ..k4_geometry.people import SOURCE as AIST_SOURCE
from .base import LOADING, BuiltinTool, Predicts, Record, check_record, on_board, result


def _part(pid, kind, center, size, carried_by=None):
    p = {"id": pid, "name": pid, "kind": kind, "box": {"center_mm": list(center), "size_mm": list(size)}, "mass_kg": 1.0}
    if carried_by:
        p["carried_by"] = carried_by
    return p


def _plain(parts, nodes=(), members=(), supports=()):
    return {"design_version": 1, "name": "check", "variables": {}, "supports": list(supports), "parts": parts,
            "nodes": list(nodes), "members": list(members), "sections": {}, "materials": {}}


class Assembly(BuiltinTool):
    name = "assembly"
    title = "組み立て・干渉・人 (箱の分離軸の判定)"
    domain = "assembly"
    base = "K4"
    tests = ["H1", "H3", "H5"]
    formulas = ["geometry.free_steer_angle", "occupant.knee_room", "assembly.part_count"]
    load_case_params = (LOADING,)
    predicts = (
        Predicts("free_steer_angle", "geometry.free_steer_angle", "theta",
                 "w, l, c of the steered wheel from hiraku.solvers.k4_geometry.steer_room(design)"),
        Predicts("knee_room", "occupant.knee_room", "room",
                 "L from hiraku.solvers.k4_geometry.knee_space(design) (the smallest), "
                 "L_bk = AIST J5 buttock-knee length, male 95th percentile (k4_geometry.AIST)"),
        Predicts("assembly_steps", "assembly.part_count", "steps", "n = parts in the design, n_out = occupants and cargo"),
    )

    def known_answers(self) -> list[Record]:
        out = []
        a = from_box([0, 0, 0], [1000, 1000, 1000])
        out.append(check_record("two 1 m cubes 0.01 mm apart", "overlap", -0.01,
                                overlap(a, from_box([1000.01, 0, 0], [1000, 1000, 1000])), "mm", 1e-6,
                                "separating axis test: a gap of 0.01 mm is found as -0.01"))
        out.append(check_record("two 1 m cubes 0.01 mm into each other", "overlap", 0.01,
                                overlap(a, from_box([999.99, 0, 0], [1000, 1000, 1000])), "mm", 1e-6,
                                "separating axis test: an overlap of 0.01 mm is found as +0.01"))
        out.append(check_record("1 m cube and the same cube turned 45 deg about z, centres 1.2 m apart", "overlap",
                                500.0 * math.sqrt(2) + 500.0 - 1200.0, overlap(a, from_box([1200, 0, 0], [1000, 1000, 1000], [0, 0, 45])),
                                "mm", 1e-6, "the turned cube reaches 500 sqrt(2) mm along x"))
        rod = from_points([0, 0, 0], [1000, 0, 0], 20.0)
        out.append(check_record("two 1 m rods 40 mm across, crossed at right angles 100 mm apart", "capsule_overlap", -60.0,
                                capsule_overlap(rod, from_points([500, -500, 100], [500, 500, 100], 20.0)), "mm", 1e-9,
                                "capsules: the axes are 100 mm apart, less the two 20 mm radii"))
        out.append(check_record("rod end 30 mm off a 1 m cube's corner along the diagonal", "capsule_overlap",
                                20.0 - 30.0, capsule_box_overlap(from_points(np.full(3, 500 + 30 / math.sqrt(3)),
                                                                             np.full(3, 900.0), 20.0), a), "mm", 1e-9,
                                "capsule and box: the corner is the nearest point of the cube, 30 mm from the axis end"))
        # a cup (bottom and four walls) with something inside and a lid: the content goes in before the lid
        parts = [_part("bottom", "body", [0, 0, 5], [200, 200, 10]),
                 _part("wall_px", "body", [95, 0, 60], [10, 200, 100]), _part("wall_mx", "body", [-95, 0, 60], [10, 200, 100]),
                 _part("wall_py", "body", [0, 95, 60], [180, 10, 100]), _part("wall_my", "body", [0, -95, 60], [180, 10, 100]),
                 _part("content", "battery", [0, 0, 50], [100, 100, 80]), _part("lid", "body", [0, 0, 115], [200, 200, 10])]
        asm = disassemble(_plain(parts))
        order = [k for k, _ in asm["order"]]
        out.append(check_record("cup, content and lid", "lid_after_content",
                                1.0, float(order.index("part:lid") > order.index("part:content")), "1", 0.0,
                                "taking apart: the lid has to come off before the content can come out"))
        # a part shut in by fixed frame members on all six sides cannot come out: an 80 mm cube
        # inside six 20 mm plates 10 mm away (plates along y for the x sides, along x for the others)
        plates = {"px": ((60, -80, 0), (60, 80, 0), "side"), "mx": ((-60, -80, 0), (-60, 80, 0), "side"),
                  "py": ((-80, 60, 0), (80, 60, 0), "side"), "my": ((-80, -60, 0), (80, -60, 0), "side"),
                  "pz": ((-80, 0, 60), (80, 0, 60), "flat"), "mz": ((-80, 0, -60), (80, 0, -60), "flat")}
        nodes, members = [], []
        for k, (a_, b_, sec) in plates.items():
            nodes += [{"id": f"{k}a", "xyz_mm": list(a_)}, {"id": f"{k}b", "xyz_mm": list(b_)}]
            members.append({"id": f"plate_{k}", "node_a": f"{k}a", "node_b": f"{k}b", "section": sec, "material": "m"})
        shut = _plain([_part("inner", "battery", [0, 0, 0], [80, 80, 80])], nodes, members)
        shut["sections"] = {"side": {"shape": "rect_solid", "dims_mm": {"b": 20, "h": 160}},
                            "flat": {"shape": "rect_solid", "dims_mm": {"b": 160, "h": 20}}}
        shut["materials"] = {"m": {"name": "m", "E_MPa": 1.0, "nu": 0.3, "density_kg_m3": 1.0, "yield_MPa": 1.0, "source": "check", "verified": True}}
        asm = disassemble(shut)
        out.append(check_record("part shut in by fixed members on six sides", "blocked_parts", 1.0, float(len(asm["blocked"])),
                                "1", 0.0, "nothing can move it out along any of the six directions"))
        # a steered wheel next to a fixed bar: free steer where the inner corner reaches the bar
        clearance = 80.0
        wheel_y, half_w, half_l = 550.0, 100.0, 300.0
        bar_y = wheel_y - half_w - clearance - 20.0  # centre of a fixed bar 40 mm wide
        design = _plain([_part("wheel_X", "wheel", [0, wheel_y, 300], [600, 200, 600])],
                        nodes=[{"id": "b0", "xyz_mm": [-600, bar_y, 300]}, {"id": "b1", "xyz_mm": [600, bar_y, 300]}],
                        members=[{"id": "bar", "node_a": "b0", "node_b": "b1", "section": "bar", "material": "m"}],
                        supports=[{"id": "X", "kind": "wheel", "position_mm": [0, wheel_y, 300], "radius_mm": 300,
                                   "suspension": {"type": "rigid"}, "actuation": {"drive": False, "brake": True, "steer": True}}])
        design["sections"] = {"bar": {"shape": "rect_solid", "dims_mm": {"b": 40, "h": 100}}}
        design["materials"] = shut["materials"]
        design["variables"]["max_steer_angle"] = {"value": 45.0, "unit": "deg"}
        reach = clearance + TOUCH_MM  # touching up to TOUCH_MM is not contact
        expected = math.degrees(brentq(lambda t: half_w * math.cos(t) + half_l * math.sin(t) - half_w - reach, 0.0, 1.0))
        got = sweep(design)["free_steer"]["X"]
        out.append(check_record("600 x 200 mm wheel 80 mm from a bar, steered", "free_steer_angle", expected, got, "deg", 1e-3,
                                "the inner corner moves in by w cos(a) + l sin(a) - w; free until that equals the clearance "
                                "(plus the 1 mm counted as touching)"))
        return out

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: load_case_params. Design variable max_steer_angle (deg) turns on the steering sweep."""
        design = on_board(design, load_case)
        bs = bodies(design)
        static = interferences(design, bs)
        sw = sweep(design, bs)
        seq = steps(design, bs)
        people = fit(design)
        part_steps = [s for s in seq if not is_attach(s)]  # putting a corner group on is not a part of the design
        placed = [s for s in part_steps if s["no_interference"]]
        results = [
            result("interference_count", float(len(static)), "1"),
            result("worst_interference", static[0][2] if static else 0.0, "mm",
                   f"{static[0][0]} x {static[0][1]}" if static else None),
            result("motion_interference_count", float(len(sw["hits"])), "1"),
            result("worst_motion_interference", sw["hits"][0][2] if sw["hits"] else 0.0, "mm",
                   f"{sw['hits'][0][0]} x {sw['hits'][0][1]}" if sw["hits"] else None),
            result("assembly_steps", float(len(placed)), "1"),
            result("blocked_parts", float(len(part_steps) - len(placed)), "1"),
            result("steps_without_tool_clearance", float(sum(not s["tool_clearance"] for s in seq)), "1"),
            result("steps_out_of_reach", float(sum(not s["hand_reach"] for s in seq)), "1"),
        ]
        if sw["free_steer"]:
            sid = min(sw["free_steer"], key=sw["free_steer"].get)
            results.append(result("free_steer_angle", sw["free_steer"][sid], "deg", sid))
            results.append(result("max_steer_angle", max_steer(design), "deg"))
        groups: dict[str, tuple[float, str]] = {}
        for k, (v, where) in people.items():
            g = k.split(".")[0]
            if g not in groups or v < groups[g][0]:
                groups[g] = (float(v), where)
        for g, (v, where) in groups.items():
            results.append(result(g, v, "mm", where))
        found = [{"a": _name(a), "b": _name(b), "depth_mm": float(o), "state": "rest", "condition": "at rest"}
                 for a, b, o in static]
        found += [{"a": _name(a), "b": _name(b), "depth_mm": float(o), "state": "motion", "condition": c}
                  for a, b, o, c in sw["hits"]]
        out = {"results": results, "assembly": seq, "interferences": found}
        if people:
            out["effects"] = [{"name": f"body sizes: {AIST_SOURCE}"}]
        return out


def _name(body_id: str) -> str:
    """'part:battery' -> 'battery', 'member:rail_L_FA' -> 'member rail_L_FA'."""
    kind, name = body_id.split(":", 1)
    return name if kind == "part" else f"member {name}"

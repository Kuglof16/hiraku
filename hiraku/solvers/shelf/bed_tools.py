"""Tools for a care bed (designs of hiraku.designgen.care_bed): the frame at rest on its casters with one extra
point load (bed_static: the edge load of the tipping test, the outward push on a side rail, the deck under its
load), and the back and knee actuators' force over the raise (actuator_linkage).

actuator_linkage reads the design by the care bed generator's names: the hinge stations B (back) and K (knee) of
the upper frame (nodes LB, LK), the brackets bracket_back and bracket_knee (their box centre's x is the anchor pin's
x), the actuators actuator_back and actuator_knee (their box centre's z is the pin height at the flat position),
the variables back_lever, knee_lever, back_angle_max and knee_angle_max, and the parts on each board (deck_<board>,
mattress_<board>, occupant.<board>).
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import DesignError, mass_summary, support_contact
from ..k1_frame import build
from ..k1_frame.analyze import rigid_fit
from .base import LOADING, BuiltinTool, LoadCaseError, Param, Predicts, Record, check_record, on_board, param_text, \
    param_value, result
from .frame_static import frame_known_answers

LOAD_PARAMS = (
    Param("load_node", None, "frame node the extra load goes into (without it: the weights only)"),
    Param("load_x", "N", "extra load along x", default=0.0),
    Param("load_y", "N", "extra load along y", default=0.0),
    Param("load_z", "N", "extra load along z (down is negative)", default=0.0),
    Param("load_point_x", "mm", "where the extra load acts, x (default: the node's)"),
    Param("load_point_y", "mm", "where the extra load acts, y (default: the node's)"),
    Param("load_point_z", "mm", "where the extra load acts, z (default: the node's)"),
)


def _solve_once(design: Record, summ: dict, node: str | None, f: np.ndarray, point, shift: dict | None):
    fm = build(design)
    fm.add_gravity(summ["items"])
    p = None
    if node is not None:
        if node not in fm.model.nodes or node not in fm.frame_nodes:
            raise LoadCaseError(f"load_node {node!r} is not a node of the frame's members")
        p = fm.node_xyz(node) if point is None else np.asarray(point, float)
        fm.add_force_at(node, p, f)
    for sid, m in (shift or {}).items():
        fm.add_force(fm.wheel_nodes[sid], (0.0, 0.0, 0.0), m)
    fm.restrain_on_wheels()
    fm.solve()
    return fm, p


def solve_at_rest(design: Record, node: str | None = None, force=(0.0, 0.0, 0.0), point=None) -> dict:
    """The frame standing on its supports under its weights and one extra force at `point` (default: the node),
    carried into `node` with the moment of the offset. Returns the model, stresses, worst member, ground forces,
    displacements (support motion removed) and the masses.

    The frame model holds the supports sideways and fore-and-aft at the wheel centres; the floor holds them at the
    contact points. When the loads have a level part (a push on the side rail), the holds' level reactions are
    moved down to the contact points: their moment (contact - wheel centre) x reaction is added at the wheel nodes
    and the frame solved again. The level reactions are statically determinate (three holds), so the second solve
    leaves them as they are; the vertical ones then balance the push's moment about the floor, not about the
    wheel centres' height. floor_shift_N_mm gives the moments added (empty when there is no level load)."""
    summ = mass_summary(design)
    f = np.asarray(force, float)
    fm, p = _solve_once(design, summ, node, f, point, None)
    weight = summ["mass_kg"] * STANDARD_GRAVITY
    shift = {}
    for s in design["supports"]:
        r, _ = fm.reaction(fm.wheel_nodes[s["id"]])
        level = np.array([r[0], r[1], 0.0])
        if float(np.linalg.norm(level)) > 1e-9 * max(weight, 1.0):
            shift[s["id"]] = np.cross(np.asarray(support_contact(s), float) - fm.node_xyz(fm.wheel_nodes[s["id"]]), level)
    if shift:
        fm, p = _solve_once(design, summ, node, f, point, shift)
    ground = {}
    for s in design["supports"]:
        r, _ = fm.reaction(fm.wheel_nodes[s["id"]])
        ground[s["id"]] = {"force_N": r + fm.spring_force(s["id"]), "point_mm": support_contact(s)}
    stresses = {mid: fm.member_stress(mid) for mid in fm.props}
    worst = max(stresses, key=lambda k: stresses[k]["max"])
    pts = [fm.node_xyz(n) for n in fm.wheel_nodes.values()]
    t, th = rigid_fit(pts, [fm.displacement(n) for n in fm.wheel_nodes.values()])
    disp = {n: fm.displacement(n) - (t + np.cross(th, fm.node_xyz(n))) for n in fm.frame_nodes}
    return {"model": fm, "mass": summ, "stresses": stresses, "worst": worst, "ground": ground, "displacements": disp,
            "load_point": p, "load": f, "floor_shift_N_mm": {k: [float(x) for x in v] for k, v in shift.items()}}


def side_loads(design: Record, ground: dict) -> dict[str, float]:
    """Vertical load on the supports of each side (y > 0: left, y < 0: right)."""
    out = {"left": 0.0, "right": 0.0}
    for s in design["supports"]:
        out["left" if s["position_mm"][1] > 0 else "right"] += float(ground[s["id"]]["force_N"][2])
    return out


def _table_design(load_kg: float = 0.0) -> Record:
    """A triangle of tubes on three rigid supports at its corners (statically determinate in z)."""
    from .fixtures import STEEL

    light = dict(STEEL, density_kg_m3=1e-6)  # weightless members: the reactions are the load's alone
    nodes = [{"id": "A", "xyz_mm": [0.0, 0.0, 300.0]}, {"id": "B", "xyz_mm": [-1000.0, 0.0, 300.0]},
             {"id": "C", "xyz_mm": [0.0, 800.0, 300.0]}, {"id": "Q", "xyz_mm": [-250.0, 0.0, 300.0]}]
    members = [{"id": "AQ", "node_a": "A", "node_b": "Q", "section": "s", "material": "m"},
               {"id": "QB", "node_a": "Q", "node_b": "B", "section": "s", "material": "m"},
               {"id": "BC", "node_a": "B", "node_b": "C", "section": "s", "material": "m"},
               {"id": "CA", "node_a": "C", "node_b": "A", "section": "s", "material": "m"}]

    def sup(sid, node, xyz):
        return {"id": sid, "kind": "caster", "position_mm": [xyz[0], xyz[1], 50.0], "radius_mm": 50.0,
                "suspension": {"type": "rigid"}, "actuation": {"drive": False, "brake": True, "steer": False},
                "frame_node": node}

    return {"design_version": 1, "name": "check table", "variables": {}, "parts": [],
            "supports": [sup("SA", "A", [0.0, 0.0]), sup("SB", "B", [-1000.0, 0.0]), sup("SC", "C", [0.0, 800.0])],
            "nodes": nodes, "members": members,
            "sections": {"s": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 40.0, "t": 2.3}}},
            "materials": {"m": light}}


def bed_static_known_answers() -> list[Record]:
    out = frame_known_answers()
    P = 1000.0
    res = solve_at_rest(_table_design(), "Q", (0.0, 0.0, -P))
    g = {k: float(v["force_N"][2]) for k, v in res["ground"].items()}
    src = "three supports, a load on the line AB a quarter of the way from A: lever rule R_A = 3/4 P, R_B = 1/4 P, R_C = 0"
    out.append(check_record("triangle on three supports, 1 kN at a quarter of AB", "support_load_A", 0.75 * P, g["SA"],
                            "N", 1e-6, src))
    out.append(check_record("triangle on three supports, 1 kN at a quarter of AB", "support_load_B", 0.25 * P, g["SB"],
                            "N", 1e-6, src))
    # the same load put 200 mm to the side of Q (toward C) through the offset: R_C = P 200 / 800
    res = solve_at_rest(_table_design(), "Q", (0.0, 0.0, -P), point=(-250.0, 200.0, 300.0))
    g = {k: float(v["force_N"][2]) for k, v in res["ground"].items()}
    out.append(check_record("the load 200 mm off the line AB toward C, carried into Q with its moment",
                            "support_load_C", P * 200.0 / 800.0, g["SC"], "N", 1e-6,
                            "moment balance about the line AB: R_C 800 = P 200"))
    # a level push toward C at Q, 300 mm above the floor (the wheel centres are 50 mm up): the floor takes it at the
    # contact points, so the lever about the line AB on the floor is 300 mm, not 250 mm
    res = solve_at_rest(_table_design(), "Q", (0.0, P, 0.0))
    g = {k: float(v["force_N"][2]) for k, v in res["ground"].items()}
    out.append(check_record("1 kN level at Q toward C, 300 mm above the floor", "support_load_C", P * 300.0 / 800.0,
                            g["SC"], "N", 1e-6, "moment balance about the line AB on the floor: R_C 800 = P 300"))
    out.append(check_record("the same push", "support_load_A_plus_B", -P * 300.0 / 800.0, g["SA"] + g["SB"], "N", 1e-6,
                            "vertical balance: R_A + R_B = -R_C (weightless members)"))
    return out


class BedStatic(BuiltinTool):
    name = "bed_static"
    applies_to = ("care_bed",)
    title = "ベッドの枠の静的 (3D の梁、キャスターの上で 1 点の荷重)"
    domain = "strength"
    base = "K1"
    tests = ["R5", "S1"]
    formulas = ["bed.deck_frame_udl", "bed.side_rail_push", "bed.edge_load_tipping", "bed.side_push_tipping"]
    load_case_params = (LOADING,) + LOAD_PARAMS
    predicts = (
        Predicts("max_stress", "bed.deck_frame_udl", "sigma", "the deck's weights only (no load_node)"),
        Predicts("max_stress", "bed.side_rail_push", "sigma", "a push outward at a side rail's top bar"),
        Predicts("lighter_side_load", "bed.edge_load_tipping", "R_far", "an edge load straight down"),
        Predicts("lighter_side_load", "bed.side_push_tipping", "R_far",
                 "a level push outward (the side rail's top bar); the floor takes it at the contact points"),
    )

    def known_answers(self) -> list[Record]:
        return bed_static_known_answers()

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        design = on_board(design, load_case)
        if not design.get("supports"):
            raise DesignError("the design has no supports to stand on")
        node = param_text(load_case, "load_node")
        force = [param_value(load_case, f"load_{a}", "N") or 0.0 for a in "xyz"]
        pt = [param_value(load_case, f"load_point_{a}", "mm") for a in "xyz"]
        point = None
        if node is not None and any(p is not None for p in pt):
            base = {n["id"]: n["xyz_mm"] for n in design["nodes"]}.get(node)
            if base is None:
                raise LoadCaseError(f"load_node {node!r} is not in design.nodes")
            point = [b if p is None else p for p, b in zip(pt, base)]
        res = solve_at_rest(design, node, force, point)
        worst = res["worst"]
        fm = res["model"]
        safety = {m: float(fm.props[m]["material"]["yield_MPa"]) / s["max"] if s["max"] > 0 else math.inf
                  for m, s in res["stresses"].items()}
        weakest = min(safety, key=safety.get)
        dn = max(res["displacements"], key=lambda k: float(np.linalg.norm(res["displacements"][k])))
        sides = side_loads(design, res["ground"])
        lighter = min(sides, key=sides.get)
        loads = {sid: float(g["force_N"][2]) for sid, g in res["ground"].items()}
        low = min(loads, key=loads.get)
        results = [result("max_stress", res["stresses"][worst]["max"], "MPa", worst),
                   result("min_safety_factor", safety[weakest], "1", weakest),
                   result("max_displacement", float(np.linalg.norm(res["displacements"][dn])), "mm", dn),
                   result("lighter_side_load", sides[lighter], "N", lighter),
                   result("min_wheel_load", loads[low], "N", low),
                   result("total_mass", res["mass"]["mass_kg"], "kg")]
        results += [result(f"wheel_load_{sid}", v, "N", sid) for sid, v in loads.items()]
        fields = [{"kind": "stress", "label": "von Mises (max along each member)", "unit": "MPa",
                   "per_member": {m: s["max"] for m, s in res["stresses"].items()}},
                  {"kind": "displacement", "label": "frame deflection (support motion removed)", "unit": "mm",
                   "per_node": {n: [float(x) for x in d] for n, d in res["displacements"].items()}}]
        vectors = [{"kind": "reaction", "at": sid, "origin_mm": [float(x) for x in g["point_mm"]],
                    "vector_N": [float(x) for x in g["force_N"]]} for sid, g in res["ground"].items()]
        if node is not None and float(np.linalg.norm(res["load"])) > 0:
            vectors.append({"kind": "load", "at": node, "origin_mm": [float(x) for x in res["load_point"]],
                            "vector_N": [float(x) for x in res["load"]]})
        out = {"results": results, "fields": fields, "vectors": vectors,
               "effects": [{"name": f"{side} casters' load", "value": v, "unit": "N"} for side, v in sides.items()]}
        if res["floor_shift_N_mm"]:
            out["assumptions"] = ["the casters do not slide: the floor takes the level load at the contact points (the "
                                  "model's level holds at the wheel centres are moved down by their moments, "
                                  + ", ".join(f"{k} {np.linalg.norm(v):.0f} N*mm" for k, v in res["floor_shift_N_mm"].items())
                                  + ")"]
        return out


# ---------------------------------------------------------------- actuators


def board_loads(design: Record, board: str) -> list[tuple[float, float, float]]:
    """(mass kg, x mm, z mm) of the parts on a deck board (deck_<board>, mattress_<board>, occupant.<board>)."""
    out = []
    for p in design.get("parts", []):
        if p["id"] in (f"deck_{board}", f"mattress_{board}", f"occupant.{board}"):
            c = p["box"]["center_mm"]
            out.append((float(p["mass_kg"]), float(c[0]), float(c[2])))
    return out


def linkage_force(hinge, anchor, r: float, loads, theta_max_deg: float, sense: float, steps: int = 721,
                  extra=None) -> dict:
    """The actuator force over the raise by virtual work: F(theta) dl = M(theta) dtheta, with l(theta) the pin-to-pin
    length and M(theta) the moment of the loads (mass, x, z) turned with the board about the hinge (their heights
    above the hinge count). sense +1: the board rises by turning from +x toward +z (it lies on the +x side of the
    hinge); -1: it lies on the -x side. The lever tip starts r below the hinge. extra(theta) -> N*mm: a moment added
    to M (the leg board on the thigh's end). dl / dtheta by central differences."""
    h = np.asarray(hinge, float)
    a = np.asarray(anchor, float)
    th = np.radians(np.linspace(0.0, theta_max_deg, steps))
    dth = 1e-6

    def turn(v, t):
        c, s = math.cos(sense * t), math.sin(sense * t)
        return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])

    def length(t):
        return float(np.linalg.norm(h + turn(np.array([0.0, -r]), t) - a))

    def moment(t):
        m = 0.0
        for mass, x, z in loads:
            p = turn(np.array([x - h[0], z - h[1]]), t)
            m += mass * STANDARD_GRAVITY * abs(p[0])
        return m + (extra(t) if extra else 0.0)

    F = []
    for t in th:
        dl = (length(t + dth) - length(t - dth)) / (2 * dth)
        if abs(dl) < 1e-9:
            raise DesignError("the actuator's line passes through the hinge: it cannot raise the board there")
        F.append(moment(t) / abs(dl))
    F = np.asarray(F)
    k = int(np.argmax(F))
    return {"F_max": float(F[k]), "theta_at_max": float(np.degrees(th[k])), "F_flat": float(F[0]),
            "stroke": length(th[-1]) - length(th[0]), "theta": th, "F": F}


def actuator_known_answers() -> list[Record]:
    out = []
    M0, r, ax = 200_000.0, 100.0, 400.0
    mass = M0 / STANDARD_GRAVITY / 500.0  # one point at hinge height, 500 mm along the board
    res = linkage_force((0.0, 0.0), (-ax, -r), r, [(mass, 500.0, 0.0)], 45.0, 1.0, steps=2)
    th = math.radians(45.0)
    e = math.hypot(r * math.sin(th) + ax, r - r * math.cos(th))
    expected = M0 * math.cos(th) * e / (r * (r * math.sin(th) + ax * math.cos(th)))
    src = ("lever r below the hinge, anchor (-a, -r): moment arm of the actuator's line d = r (r sin t + a cos t) / |e|, "
           "F = M0 cos t / d")
    out.append(check_record("lever 100 mm, anchor 400 mm away level with the tip, raised 45 deg", "force_at_45deg",
                            expected, float(res["F"][-1]), "N", 1e-6, src))
    out.append(check_record("the same lever flat", "force_flat", M0 / r, res["F_flat"], "N", 1e-6,
                            "flat: the actuator is at right angles to the lever, F = M0 / r"))
    out.append(check_record("the same lever raised 45 deg", "stroke", e - ax, res["stroke"], "mm", 1e-9,
                            "stroke = |tip(45 deg) - anchor| - a"))
    return out


ACTUATORS = {"back": ("B", "back_lever", "back_angle_max", 1.0), "knee": ("K", "knee_lever", "knee_angle_max", -1.0)}


def actuator_over_raise(design: Record, which: str, tool: str = "actuator_linkage") -> dict:
    """linkage_force of the back or knee actuator of a care bed (the design as on board): the board's loads, for
    the knee half the leg board's at the thigh board's far end. Raises DesignError naming what a design lacks."""
    station, lever, angle, sense = ACTUATORS[which]
    v = {k: q["value"] for k, q in design.get("variables", {}).items()}
    nodes = {n["id"]: n["xyz_mm"] for n in design.get("nodes", [])}
    parts = {p["id"]: p for p in design.get("parts", [])}
    need = [f"L{station}", f"bracket_{which}", f"actuator_{which}", lever, angle]
    missing = [k for k in need if k not in nodes and k not in parts and k not in v]
    if missing:
        raise DesignError(f"{tool} reads a care bed (hiraku.designgen.care_bed); this design lacks {', '.join(missing)}")
    hinge = (nodes[f"L{station}"][0], nodes[f"L{station}"][2])
    anchor = (parts[f"bracket_{which}"]["box"]["center_mm"][0], parts[f"actuator_{which}"]["box"]["center_mm"][2])
    board = {"back": "back", "knee": "thigh"}[which]
    loads = board_loads(design, board)
    extra = None
    if which == "knee":  # the leg board rests on the thigh board's far end (half its weight) and on the frame
        leg = sum(m for m, _, _ in board_loads(design, "leg")) * STANDARD_GRAVITY / 2.0
        far = nodes["LT"][0] if "LT" in nodes else None
        if far is None:
            raise DesignError(f"{tool}: no thigh-leg hinge node LT")
        arm = abs(far - hinge[0])
        extra = lambda t: leg * arm * math.cos(t)  # noqa: E731
    return linkage_force(hinge, anchor, v[lever], loads, v[angle], sense, extra=extra)


class ActuatorLinkage(BuiltinTool):
    name = "actuator_linkage"
    applies_to = ("care_bed",)
    title = "背上げ・膝上げのアクチュエーターの力 (仮想仕事、板を起こす)"
    domain = "powertrain"
    base = "K0"
    tests = ["P5"]
    formulas = ["bed.lever_actuator_force"]
    load_case_params = (LOADING, Param("actuator", None, "which actuator: back or knee", required=True,
                                       choices=tuple(ACTUATORS)))
    predicts = (Predicts("actuator_force", "bed.lever_actuator_force", "F_max",
                         "M0 = the board's loads about the hinge, flat (their heights left out)"),
                Predicts("actuator_stroke", "bed.lever_actuator_force", "stroke"))

    def known_answers(self) -> list[Record]:
        return actuator_known_answers()

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        design = on_board(design, load_case)
        which = param_text(load_case, "actuator")
        res = actuator_over_raise(design, which, self.name)
        k = int(np.argmax(res["F"]))
        return {
            "results": [result("actuator_force", res["F_max"], "N", f"actuator_{which}"),
                        result("actuator_force_flat", res["F_flat"], "N", f"actuator_{which}"),
                        result("actuator_stroke", res["stroke"], "mm", f"actuator_{which}"),
                        result("angle_at_max_force", res["theta_at_max"], "deg", f"actuator_{which}")],
            "series": [{"channel": f"actuator_force.{which}", "unit": "N",
                        "x": {"quantity": "raise_angle", "unit": "deg",
                              "values": [float(x) for x in np.degrees(res["theta"][::10])]},
                        "values": [float(x) for x in res["F"][::10]]}],
            "effects": [{"name": f"largest force at {res['theta_at_max']:.1f} deg of the raise", "value": float(res["F"][k]),
                         "unit": "N"}],
            "assumptions": ["quasi-static, no friction in the hinge and the pins",
                            "the board's parts turn with it about the hinge (their heights above it count)"]
            + (["the leg board rests half on the thigh board's far end and half on the frame"] if which == "knee" else []),
        }


__all__ = ["ACTUATORS", "ActuatorLinkage", "BedStatic", "actuator_over_raise", "board_loads", "linkage_force",
           "side_loads", "solve_at_rest"]

"""spray_reach (H6): lines thrown off the wheels' treads, and whether they reach an occupant before anything else.

From the tread points of hiraku.solvers.k0_handcalc.spray_inputs.tread_lines (each wheel's arc from arc_front
ahead of the top to arc_rear behind it, lines_across across the tyre) a straight line leaves along the tangent
the way the tread moves going forward. It is followed up to line_length or the ground (the plane through the
lowest tyre bottom). The first body it enters (k4_geometry.bodies: part and member boxes, the load case's
occupants and cargo on board) decides: an occupant's body part (kind occupant) means the line reaches the
occupant; anything else (the fender, the body, the frame) covers it. The wheel's own part and any body the line
starts inside are skipped.

Results: spray_reaching_occupant (lines that reach an occupant, all wheels, cast from the whole tread above the
axle: reach_front ahead of the top to reach_rear behind it, 90 deg each by default), spray_uncovered_arc (deg, the
largest over the wheels: the bins of the borrowed rule's arc, arc_front to arc_rear, with a line that nothing covers,
times the bin's width).
"""

from __future__ import annotations

import math

from ..k0_handcalc.spray_inputs import ARC_STEP_DEG, LINE_LENGTH_MM, LINES_ACROSS, REACH_DEG, arc_bins, tread_lines
from ..k4_geometry.model import bodies
from ..k4_geometry.obb import from_box, ray_box
from .base import LOADING, BuiltinTool, Param, Predicts, Record, check_record, on_board, param_value, result

ARC_FRONT = Param("arc_front", "deg", "tread arc ahead of the top (straight above the axle) that throws spray "
                  "(requirement Q87)", required=True)
ARC_REAR = Param("arc_rear", "deg", "tread arc behind the top that throws spray (requirement Q88)", required=True)
REACH_FRONT = Param("reach_front", "deg", "tread ahead of the top whose lines are counted for spray_reaching_occupant "
                    "(default: all of it above the axle)", default=REACH_DEG)
REACH_REAR = Param("reach_rear", "deg", "tread behind the top whose lines are counted for spray_reaching_occupant "
                   "(default: all of it above the axle)", default=REACH_DEG)
ARC_STEP = Param("arc_step", "deg", "largest spacing of the tread points along the arc (assumption, not checked)",
                 default=ARC_STEP_DEG)
LINES = Param("lines_across", "1", "tread points across the tyre's width (assumption, not checked)",
              default=float(LINES_ACROSS))
LENGTH = Param("line_length", "mm", "how far a line is followed (assumption, not checked)", default=LINE_LENGTH_MM)

ASSUMPTIONS = [
    "仮定 (確かめていない): 踏面を離れた物は、前進のときに踏面が動く向き (車から見て) の接線に沿ってまっすぐ飛ぶ。重力と空気は"
    "入れない。後退は入れない",
    "仮定 (確かめていない): 車輪はまっすぐ前を向き、サスペンションは止まっているときの位置",
    "仮定 (確かめていない): 部品と部材は箱 (細い部品も箱のまま)。地面は、いちばん低いタイヤの下の面を通る水平な面",
    "弧 (車軸の真上から前 arc_front・後ろ arc_rear) は要件 Q87・Q88: 公道のほかの交通のための規則 (告示 第178条 第2項 "
    "第1号) を借りたもの。覆われない弧 (spray_uncovered_arc) だけに使う",
    "乗員に届く線 (spray_reaching_occupant) は、車軸より上の踏面ぜんぶ (真上から前 reach_front・後ろ reach_rear、既定 90 度) "
    "から数える (要件は「泥が乗員に届かない」)",
]


def _ground(design: Record) -> float:
    return min(float(s["position_mm"][2]) - float(s["radius_mm"]) for s in design["supports"] if s.get("kind") == "wheel")


def cast(design: Record, arc_front: float, arc_rear: float, step: float, across: int, length: float) -> Record:
    """Every line's fate: {"lines": [(line, "occupant" | "covered" | "free", body id or None)], "bins": n,
    "width": deg}. design: with the load case's occupants on board."""
    bs = bodies(design)
    ground = _ground(design)
    out = []
    for line in tread_lines(design, arc_front, arc_rear, step, across):
        best, who = length, None
        if line.direction[2] < -1e-12:
            best = min(best, (ground - line.origin[2]) / line.direction[2])
        own = f"part:{line.wheel_part}" if line.wheel_part else None
        for k, b in bs.items():
            if k == own:
                continue
            h = ray_box(line.origin, line.direction, b.obb)
            if h is None or h[1] <= 0.0 or h[0] < 0.0:  # behind the start, or the line starts inside it
                continue
            if h[0] < best:
                best, who = h[0], k
        if who is None:
            out.append((line, "free", None))
        else:
            out.append((line, "occupant" if bs[who].kind == "occupant" else "covered", who))
    mids, width = arc_bins(arc_front, arc_rear, step)
    return {"lines": out, "bins": len(mids), "width": width}


def summary(fates: Record) -> Record:
    """{"reach": n, "per_wheel": {support: {"reach", "uncovered_arc", "where"}}}."""
    per: dict[str, Record] = {}
    for line, fate, who in fates["lines"]:
        w = per.setdefault(line.support, {"reach": 0, "open": set(), "hit": {}})
        if fate != "covered":
            w["open"].add(line.bin)
        if fate == "occupant":
            w["reach"] += 1
            w["hit"][who] = w["hit"].get(who, 0) + 1
    out = {}
    for sid, w in per.items():
        top = max(w["hit"], key=w["hit"].get) if w["hit"] else None
        out[sid] = {"reach": w["reach"], "uncovered_arc": len(w["open"]) * fates["width"],
                    "where": top.split(":", 1)[1] if top else None}
    return {"reach": sum(w["reach"] for w in out.values()), "per_wheel": out}


def _scene(occupant_box: tuple, plate: tuple | None = None) -> Record:
    """One wheel (support W at (0, 0, 300), radius 300, 200 wide) and boxes: the occupant's, and a plate (kind
    body) when given; (centre, size) each."""
    parts = [{"id": "wheel_W", "name": "wheel", "kind": "wheel", "mass_kg": 1.0,
              "box": {"center_mm": [0.0, 0.0, 300.0], "size_mm": [600.0, 200.0, 600.0]}},
             {"id": "person", "name": "person", "kind": "occupant", "mass_kg": 1.0, "carried_by": ["n"],
              "box": {"center_mm": list(occupant_box[0]), "size_mm": list(occupant_box[1])}}]
    if plate:
        parts.append({"id": "plate", "name": "plate", "kind": "body", "mass_kg": 1.0, "carried_by": ["n"],
                      "box": {"center_mm": list(plate[0]), "size_mm": list(plate[1])}})
    return {"design_version": 1, "name": "check", "variables": {}, "nodes": [], "members": [], "sections": {},
            "materials": {}, "parts": parts,
            "supports": [{"id": "W", "kind": "wheel", "position_mm": [0.0, 0.0, 300.0], "radius_mm": 300.0,
                          "suspension": {"type": "rigid"}, "actuation": {"drive": True, "brake": True, "steer": False}}]}


class SprayReach(BuiltinTool):
    name = "spray_reach"
    stage = 0  # layout: straight lines against boxes, as the assembly tool's geometry
    title = "泥はね (踏面から接線に飛ばした線が、ほかの物より先に乗員に届くか)"
    domain = "assembly"
    base = "K4"
    tests = ["H6"]
    formulas = ["spray.uncovered_arc", "spray.reaching_lines"]
    load_case_params = (LOADING, ARC_FRONT, ARC_REAR, REACH_FRONT, REACH_REAR, ARC_STEP, LINES, LENGTH)
    predicts = (
        Predicts("spray_uncovered_arc", "spray.uncovered_arc", "arc",
                 "a_f, a_r = the load case's arc_front, arc_rear; f, r, g = the design's fender_front_deg, "
                 "fender_rear_deg, fender_gap (0 without fenders); R = the smallest wheel radius "
                 "(k0_handcalc.spray_inputs.spray_arc_inputs). The tool counts bins: within one bin's width"),
        Predicts("spray_reaching_occupant", "spray.reaching_lines", "n",
                 "n_aim, n_cover counted on the occupants' boxes and the fender's cylinder only "
                 "(k0_handcalc.spray_inputs.spray_reach_inputs): no fewer than the tool finds"),
    )

    def known_answers(self) -> list[Record]:
        out = []
        cube = from_box([0, 0, 0], [1000, 1000, 1000])
        h = ray_box([-2000, 0, 0], [1, 0, 0], cube)
        out.append(check_record("1 m cube, line along x from 2 m away", "t_enter", 1500.0, None if h is None else h[0],
                                "mm", 1e-12, "the near face is 500 mm from the centre",
                                case_ja="1 m の立方体に、2 m 離れた所から x の向きに線を飛ばす",
                                source_ja="手前の面は中心から 500 mm"))
        h = ray_box([-2000, 0, 0], [1, 0, 0], from_box([0, 0, 0], [1000, 1000, 1000], [0, 0, 45]))
        out.append(check_record("1 m cube turned 45 deg about z, line along x", "t_enter", 2000.0 - 500.0 * math.sqrt(2),
                                None if h is None else h[0], "mm", 1e-12, "the near edge is 500 sqrt(2) mm from the centre",
                                case_ja="z 軸のまわりに 45° 回した 1 m の立方体に、x の向きに線を飛ばす",
                                source_ja="手前の辺は中心から 500 √2 mm"))
        h = ray_box([-2000, 600, 0], [1, 0, 0], cube)
        out.append(check_record("1 m cube, line along x 600 mm to the side", "hits", 0.0, float(h is not None), "1", 0.0,
                                "the line passes 100 mm outside the face",
                                case_ja="1 m の立方体の 600 mm 横を、x の向きに線を飛ばす",
                                source_ja="線は面の 100 mm 外を通る (当たらない)"))
        # a wall of an occupant 1 m ahead of the wheel: every line of the 80 deg arc points forward and gets there
        # before the ground (the lowest, 30 deg down from 560 mm, meets the ground 970 mm ahead of its start)
        wall = ((1050.0, 0.0, 0.0), (100.0, 1000.0, 20000.0))
        s = summary(cast(_scene(wall), 30.0, 50.0, 5.0, 1, 3000.0))
        out.append(check_record("wheel and a wall of occupant 1 m ahead, no cover (arc 30 + 50 deg, 16 bins)",
                                "spray_reaching_occupant", 16.0, float(s["reach"]), "1", 0.0,
                                "every tangent line of the arc points forward (cos theta > 0)",
                                case_ja="車輪と、その 1 m 前に乗員の壁、覆いなし (弧は 30° + 50°、16 区切り)",
                                source_ja="弧のどの接線も前を向く (cos θ > 0)"))
        out.append(check_record("the same, uncovered arc", "spray_uncovered_arc", 80.0,
                                s["per_wheel"]["W"]["uncovered_arc"], "deg", 1e-12, "the whole arc: 30 + 50 deg",
                                case_ja="同じ形で、覆われない弧", source_ja="弧ぜんぶ: 30° + 50°"))
        # a flat cover with its underside 340 mm over the wheel centre, from 1000 mm behind to 900 mm ahead: a line
        # from theta (tread point (-R sin, R cos)) going up (theta > 0) meets that plane at
        # x = -R sin(theta) + (340 - R cos(theta)) / tan(theta); covered when x <= 900
        R, top, end = 300.0, 340.0, 900.0
        mids, width = arc_bins(30.0, 50.0, 5.0)
        open_bins = [th for th in mids if th <= 0
                     or -R * math.sin(math.radians(th)) + (top - R * math.cos(math.radians(th))) / math.tan(math.radians(th)) > end]
        plate = ((-50.0, 0.0, 300.0 + top + 1.5), (1900.0, 400.0, 3.0))
        s = summary(cast(_scene(wall, plate), 30.0, 50.0, 5.0, 1, 3000.0))
        out.append(check_record("the same with a flat cover over the wheel", "spray_reaching_occupant",
                                float(len(open_bins)), float(s["reach"]), "1", 0.0,
                                "lines going down or level pass under the cover; going up, they meet its plane "
                                "before its front end unless they rise too slowly",
                                case_ja="同じ形で、車輪の上に平らな覆い",
                                source_ja="下向きや水平の線は覆いの下を通る。上向きの線は、上がり方がゆるすぎなければ、"
                                          "覆いの前の端より手前でその面に当たる"))
        out.append(check_record("the same, uncovered arc", "spray_uncovered_arc", len(open_bins) * width,
                                s["per_wheel"]["W"]["uncovered_arc"], "deg", 1e-12,
                                "the bins whose line passes the cover, times the bin's width (5 deg)",
                                case_ja="同じ形 (覆いあり) で、覆われない弧",
                                source_ja=f"線が覆いを通り抜ける区切りの数 × 区切りの幅 ({width:g}°)"))
        return out

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: load_case_params (arc_front and arc_rear required)."""
        design = on_board(design, load_case)
        a_f = param_value(load_case, "arc_front", "deg")
        a_r = param_value(load_case, "arc_rear", "deg")
        step = param_value(load_case, "arc_step", "deg")
        across = int(round(param_value(load_case, "lines_across", "1")))
        length = param_value(load_case, "line_length", "mm")
        fates = cast(design, a_f, a_r, step, across, length)  # the borrowed rule's arc: the uncovered arc
        reach = cast(design, param_value(load_case, "reach_front", "deg"), param_value(load_case, "reach_rear", "deg"),
                     step, across, length)  # the whole tread above the axle: what reaches the occupants
        s, r_s = summary(fates), summary(reach)
        per = {sid: {**w, "reach": r_s["per_wheel"][sid]["reach"], "where": r_s["per_wheel"][sid]["where"]}
               for sid, w in s["per_wheel"].items()}
        s = {**s, "reach": r_s["reach"]}
        worst_reach = max(per, key=lambda k: per[k]["reach"])
        worst_arc = max(per, key=lambda k: per[k]["uncovered_arc"])
        where = (f"{worst_reach} → {per[worst_reach]['where']}" if per[worst_reach]["reach"] else None)
        results = [result("spray_reaching_occupant", float(s["reach"]), "1", where),
                   result("spray_uncovered_arc", per[worst_arc]["uncovered_arc"], "deg", worst_arc)]
        effects = []
        for sid in sorted(per):
            w = per[sid]
            text = (f"{sid}: 乗員に届く線 {w['reach']} 本" + (f" (多いのは {w['where']})" if w["where"] else "")
                    + f"、覆われない弧 {w['uncovered_arc']:.1f} deg")
            effects.append({"name": text})
        effects.append({"name": f"線: 弧を {fates['bins']} 個 ({fates['width']:.2f} deg ずつ) x 幅の方向に {across} 本、"
                                f"車輪ごと。長さ {length:g} mm まで"})
        return {"results": results, "effects": effects, "assumptions": list(ASSUMPTIONS)}

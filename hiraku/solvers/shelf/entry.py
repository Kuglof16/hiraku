"""entry_opening (H7, proposed): getting in and out of each side seat: the opening beside it, the fixed things in
it, the first step and the hand holds (hiraku.solvers.k4_geometry.entry).

For each seat off the vehicle's middle, its span along x (seat back to the front of the seated person's feet) and
the passage outboard of the seat's outer face. Fixed bodies (members, parts that are bolted or welded) block; a net
held only by quick-release clips and a door on hinges with a latch open (k4_geometry.entry.openable_parts), when the
load case's `openable` is yes (requirement Q47: the opening opens by hand, one hand and one action).

Results (the worst seat; per seat as <quantity>.<seat>):
- entry_blocked: the fewest fixed bodies in a doorway of the required size (upper box width_upper from the seat top
  up to max(seat top + height_seat, floor + height_floor), lower box width_lower from the floor to the seat top at
  its front end) over its places in the seat's span; needs the four sizes (Q93 to Q96)
- entry_opening_width: the longest clear stretch of the span above the seat top (up to the doorway's top, or
  height_cap); entry_opening_width_low: from the floor to the seat top
- entry_opening_height: the greatest clear height above the seat top over width_upper; entry_opening_height_floor:
  above the floor over width_lower (height_cap when nothing is above)
- entry_step_height: from the ground to the first surface a foot goes on (a step, else the floor); entry_step_rise
  between those surfaces when there are steps
- entry_handhold: members or hand-hold parts a hand closes round (grip_min to grip_max across) in the span,
  outboard of the seat's middle, up to grip_reach above the first surface (the fewest over the seats)
"""

from __future__ import annotations

import copy

from ..k4_geometry import entry as geo
from .base import LOADING, BuiltinTool, Param, Predicts, Record, check_record, on_board, param_text, param_value, result

W_UP = Param("width_upper", "mm", "the opening's least width along x above the seat top (requirement Q93)")
W_LOW = Param("width_lower", "mm", "its least width from the floor to the seat top (requirement Q94)")
H_SEAT = Param("height_seat", "mm", "its least clear height above the seat top (requirement Q95)")
H_FLOOR = Param("height_floor", "mm", "its least clear height above the floor (requirement Q96)")
OPENABLE = Param("openable", None, "yes: a net on quick-release clips and a door with hinges and a latch open and do "
                 "not block (requirement Q47: opened by hand, one hand, one action); no: everything blocks",
                 default="no", choices=("yes", "no"))
GRIP_MIN = Param("grip_min", "mm", "a hand hold's least size across (requirement Q49)")
GRIP_MAX = Param("grip_max", "mm", "a hand hold's greatest size across (requirement Q89)")
GRIP_REACH = Param("grip_reach", "mm", "a hand hold no higher than this over the first surface the feet are on "
                   "(requirement Q48)")
CAP = Param("height_cap", "mm", "a clear height with nothing above is reported as this (assumption, not checked)",
            default=geo.HEIGHT_CAP_MM)

ASSUMPTIONS = [
    "仮定 (確かめていない): 開口は座席ごとに、座席の箱の外側の面より外 (車の外へ) で、座席の箱の後ろの面から、座った人の体の部分の"
    "いちばん前 (足先) までの前後の範囲で探す。降りる人は座っている所から外へ出る",
    "仮定 (確かめていない): 部品と部材は軸にそろえた外接箱で見る (斜めの物は実物より大きめ)。箱が座席の外の面より 1 mm を超えて"
    "外に出て、高さの帯に 1 mm を超えて入る物がふさぐ。乗員と荷はふさがない",
    "仮定 (確かめていない、HIRAKU の読み方): 外せる留め具 (kind net_clip) だけで留まる網 (留め具が触れ、網の carried_by の節が"
    "どれも留め具の節) と、蝶番 (door_hinge) と掛け金 (door_latch) が触れる扉 (kind door) は開けられる。留め具・蝶番・掛け金は"
    "開口の縁にあり、数えない。ほかの部品 (ボルト・溶接) と部材は動かない",
    "開口の形 (Q93〜Q96): 座面から上へ幅 Q93 の箱 (上は座面 + Q95 と床 + Q96 の高いほう)、床から座面まで幅 Q94 の箱 (上の箱の前の端に"
    "そろえる。脚は腰より前に出る、仮定)。範囲の中で動かし、ふさぐ物がいちばん少ない所で数える",
    "地面は、いちばん低いタイヤの下の面を通る水平な面。床は、座席の真ん中の下にある kind body の部品の上面",
    "手がかり: 断面の外の寸法 (丸は径、角は二つの辺) が Q49 から Q89 の部材と、kind grab_handle の部品で、座席の真ん中より外、"
    "範囲の中、最初の足場から Q48 までの高さにあるもの。手の入る空き・長さ・強さは見ない",
    "Q93〜Q97・Q44〜Q49・Q89 は、ISO 4252:1983 と ISO 2867 (1980・1994) の古い版を受付部が読んだ値 (トラクタと土工機械の規格を"
    "借りたもの)、Q95 は AIST の座高 (仮定)、Q47 は CPSC 案の前文を開けるほうに当てはめた仮定",
]


def _short(body_id: str) -> str:
    return body_id.split(":", 1)[-1]


def measure(design: Record, sizes: dict, openable: bool, grip: tuple | None, cap: float) -> dict[str, Record]:
    """{seat id: its measures} (see the module docstring); sizes: width_upper, width_lower, height_seat,
    height_floor (any may be None)."""
    boxes = geo.aabb(design)
    opens = geo.openable_parts(design, boxes) if openable else {}
    ground = geo.ground_z(design)
    out = {}
    for sp in geo.seat_spaces(design, boxes, opens):
        w_up, w_low, h_s, h_f = (sizes.get(k) for k in ("width_upper", "width_lower", "height_seat", "height_floor"))
        top = max(sp.z_seat + (h_s if h_s is not None else cap), sp.z_floor + (h_f if h_f is not None else 0.0))
        m: Record = {"space": sp, "opens": sorted(k for k in opens if not opens[k].startswith(("clip of", "fitting of")))}
        m["width"], m["width_at"] = geo.clear_width(sp, sp.z_seat, top)
        m["width_low"], m["width_low_at"] = geo.clear_width(sp, sp.z_floor, sp.z_seat)
        if w_up is not None:
            m["height"], m["height_at"] = geo.clear_height(sp, sp.z_seat, w_up, cap)
        if w_low is not None:
            m["height_floor"], m["height_floor_at"] = geo.clear_height(sp, sp.z_floor, w_low, cap)
        if None not in (w_up, w_low, h_s, h_f):
            m["blocked"], m["blocked_at"] = geo.doorway(sp, w_up, w_low, h_s, h_f)
        surf = geo.step_surfaces(design, sp, boxes)
        m["surfaces"] = surf
        m["step"] = surf[0][1] - ground
        m["rise"] = max((b[1] - a[1] for a, b in zip(surf, surf[1:])), default=None)
        if grip is not None:
            m["hands"] = geo.handholds(design, sp, surf[0][1], grip[0], grip[1], grip[2], boxes)
        out[sp.seat] = m
    return out


def _scene(extra_parts: tuple = (), members: tuple = ()) -> Record:
    """A seat (box 500 x 500 x 260 on a floor whose top is at 433 mm), its person's trunk box reaching 200 mm ahead
    of the seat, a wheel making the ground z = 0, and extra parts; members: (id, a, b, d) round tubes."""
    parts = [
        {"id": "seat_L", "name": "seat", "kind": "seat", "mass_kg": 1.0, "carried_by": ["n0"],
         "box": {"center_mm": [-1045.0, 300.0, 563.0], "size_mm": [500.0, 500.0, 260.0]}},
        {"id": "floor", "name": "floor", "kind": "body", "mass_kg": 1.0, "carried_by": ["n0"],
         "box": {"center_mm": [-1045.0, 0.0, 431.5], "size_mm": [1000.0, 1100.0, 3.0]}},
        {"id": "occupant_L", "name": "person", "kind": "occupant", "mass_kg": 1.0, "carried_by": ["n0"],
         "box": {"center_mm": [-945.0, 300.0, 1000.0], "size_mm": [700.0, 400.0, 600.0]}},
        *extra_parts,
    ]
    nodes = {"n0": [0.0, 0.0, 0.0]}
    mems, secs = [], {}
    for mid, a, b, d in members:
        nodes[f"{mid}_a"], nodes[f"{mid}_b"] = list(a), list(b)
        secs[f"ct{d:g}"] = {"shape": "round_tube", "dims_mm": {"d": float(d), "t": 2.0}}
        mems.append({"id": mid, "node_a": f"{mid}_a", "node_b": f"{mid}_b", "section": f"ct{d:g}", "material": "steel"})
    return {"design_version": 1, "name": "check", "variables": {},
            "nodes": [{"id": k, "xyz_mm": v} for k, v in nodes.items()], "members": mems, "sections": secs,
            "materials": {"steel": {"name": "steel", "E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 7850.0,
                                    "yield_MPa": 235.0}},
            "parts": parts,
            "supports": [{"id": "W", "kind": "wheel", "position_mm": [0.0, 600.0, 300.0], "radius_mm": 300.0,
                          "suspension": {"type": "rigid"}, "actuation": {"drive": True, "brake": True, "steer": False}}]}


def _box(pid: str, kind: str, lo, hi, carried=("n0",)) -> Record:
    return {"id": pid, "name": pid, "kind": kind, "mass_kg": 0.1, "carried_by": list(carried),
            "box": {"center_mm": [(a + b) / 2.0 for a, b in zip(lo, hi)], "size_mm": [b - a for a, b in zip(lo, hi)]}}


SIZES = {"width_upper": 450.0, "width_lower": 300.0, "height_seat": 985.2, "height_floor": 1250.0}


class EntryOpening(BuiltinTool):
    name = "entry_opening"
    stage = 0  # layout: boxes along lines, as the assembly tool's geometry
    applies_to = ("ladder_4w", "space_frame_utv")  # the designs with seats
    title = "乗り降り (座席の横の開口、ふさぐ物、最初の足場、手がかり)"
    domain = "assembly"
    base = "K4"
    tests = ["H7"]
    formulas = ["entry.opening_width", "entry.opening_height", "entry.step_height", "entry.blocked", "entry.handholds"]
    load_case_params = (LOADING, W_UP, W_LOW, H_SEAT, H_FLOOR, OPENABLE, GRIP_MIN, GRIP_MAX, GRIP_REACH, CAP)
    predicts = (
        Predicts("entry_opening_width", "entry.opening_width", "W",
                 "x_f, x_b = the seat's span, x_p, d = the side frame's front post (k0_handcalc.entry_inputs); exact "
                 "when nothing else reaches into the band"),
        Predicts("entry_opening_height", "entry.opening_height", "H",
                 "the side frame's lowest bar over the opening, else nothing above (height_cap)"),
        Predicts("entry_step_height", "entry.step_height", "h",
                 "the floor's top over the ground from the design's rail and floor variables"),
        Predicts("entry_blocked", "entry.blocked", "n",
                 "the side frame's members and a fixed net only, at the place K0 picks: no more than the tool finds "
                 "when nothing else is in the way"),
        Predicts("entry_handhold", "entry.handholds", "n",
                 "round members of a graspable diameter beside the seat (k0_handcalc.entry_inputs); the tool also "
                 "counts hand-hold parts and rectangular tubes"),
    )

    def known_answers(self) -> list[Record]:
        out = []
        m = measure(_scene(), SIZES, True, None, geo.HEIGHT_CAP_MM)["seat_L"]
        out.append(check_record("one seat, nothing beside it: the opening is the person's span", "entry_opening_width",
                                700.0, m["width"], "mm", 1e-12,
                                "from the seat back (-1295) to the front of the person's box (-595)"))
        out.append(check_record("the same, nothing in the doorway", "entry_blocked", 0.0, float(len(m["blocked"])), "1",
                                0.0, "no body outboard of the seat"))
        out.append(check_record("the same, first step", "entry_step_height", 433.0, m["step"], "mm", 1e-12,
                                "floor top 433 mm over the ground at the tyre's bottom (300 - 300)"))
        bar = ("bar", [-1400.0, 570.0, 793.0], [-500.0, 570.0, 793.0], 40.0)  # 100 mm over the seat top (693)
        m = measure(_scene(members=(bar,)), SIZES, True, None, geo.HEIGHT_CAP_MM)["seat_L"]
        out.append(check_record("a 40 mm bar along the whole span 100 mm over the seat", "entry_opening_height",
                                80.0, m["height"], "mm", 1e-9, "the bar's underside: 100 - 40 / 2 over the seat top"))
        out.append(check_record("the same, fixed things in the doorway", "entry_blocked", 1.0, float(len(m["blocked"])),
                                "1", 0.0, "the bar crosses every place of the upper box"))
        post = ("post", [-1100.0, 570.0, 300.0], [-1100.0, 570.0, 1500.0], 40.0)
        m = measure(_scene(members=(post,)), SIZES, True, None, geo.HEIGHT_CAP_MM)["seat_L"]
        out.append(check_record("a 40 mm post at x = -1100 beside the seat", "entry_opening_width", -595.0 - (-1080.0),
                                m["width"], "mm", 1e-9, "from the post's front face (-1100 + 20) to the span's front"))
        net = _box("net", "side_net", [-1290.0, 568.5, 700.0], [-600.0, 571.5, 1300.0], ("a", "b"))
        clip = _box("clip", "net_clip", [-1310.0, 560.0, 990.0], [-1290.0, 580.0, 1010.0], ("a", "b"))
        for clips, opens, n, why in (((clip,), True, 0.0, "held by a clip only: it opens"),
                                     ((clip,), False, 1.0, "openable = no: everything blocks"),
                                     ((), True, 1.0, "no clip: the net is bolted on its nodes")):
            m = measure(_scene((net,) + clips), SIZES, opens, None, geo.HEIGHT_CAP_MM)["seat_L"]
            out.append(check_record(f"a net beside the seat ({why})", "entry_blocked", n, float(len(m["blocked"])), "1",
                                    0.0, why))
        door = _box("door", "door", [-1290.0, 568.5, 700.0], [-600.0, 571.5, 1300.0])
        hinge = _box("hinge", "door_hinge", [-1310.0, 560.0, 700.0], [-1290.0, 580.0, 720.0])
        latch = _box("latch", "door_latch", [-620.0, 560.0, 680.0], [-600.0, 580.0, 700.0])
        for fits, n, why in (((hinge, latch), 0.0, "a hinge and a latch touch it: it opens"),
                             ((hinge,), 1.0, "no latch: it is taken as fixed")):
            m = measure(_scene((door,) + fits), SIZES, True, None, geo.HEIGHT_CAP_MM)["seat_L"]
            out.append(check_record(f"a door beside the seat ({why})", "entry_blocked", n, float(len(m["blocked"])), "1",
                                    0.0, why))
        grips = (("g27", [-900.0, 600.0, 700.0], [-900.0, 600.0, 1300.0], 27.2),
                 ("g43", [-800.0, 600.0, 700.0], [-800.0, 600.0, 1300.0], 42.7))
        m = measure(_scene(members=grips), SIZES, True, (16.0, 38.0, 1600.0), geo.HEIGHT_CAP_MM)["seat_L"]
        out.append(check_record("a 27.2 and a 42.7 mm tube beside the seat, grips 16 to 38 mm", "entry_handhold", 1.0,
                                float(len(m["hands"])), "1", 0.0, "only the 27.2 mm tube is within 16 to 38 mm"))
        return out

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: load_case_params (all optional; the doorway needs the four sizes, the hand holds the
        three grip values)."""
        on_board(design, load_case)  # checks params.loading; the seats' span reads every occupant part
        design = copy.deepcopy(design)
        sizes = {p.name: param_value(load_case, p.name, "mm") for p in (W_UP, W_LOW, H_SEAT, H_FLOOR)}
        openable = param_text(load_case, "openable", "no") == "yes"
        g = [param_value(load_case, k, "mm") for k in ("grip_min", "grip_max", "grip_reach")]
        grip = tuple(g) if None not in g else None
        cap = param_value(load_case, "height_cap", "mm")
        per = measure(design, sizes, openable, grip, cap)
        if not per:
            from ..k0_handcalc.vehicle import DesignError

            raise DesignError("the design has no seat off its middle, so there is no side opening to measure")
        results, effects = [], []

        def worst(key: str, quantity: str, unit: str, pick, value=lambda m, k: m[k], at=None):
            seats = [s for s in per if per[s].get(key) is not None]
            if not seats:
                return
            vals = {s: float(value(per[s], key)) for s in seats}
            s = pick(vals, key=vals.get)
            results.append(result(quantity, vals[s], unit, at(s) if at else s))
            results.extend(result(f"{quantity}.{t}", vals[t], unit, at(t) if at else t) for t in sorted(seats))

        worst("blocked", "entry_blocked", "1", max, lambda m, k: len(m[k]),
              lambda s: f"{s}: " + (", ".join(_short(i) for i in per[s]["blocked"]) or "なし"))
        worst("width", "entry_opening_width", "mm", min)
        worst("width_low", "entry_opening_width_low", "mm", min)
        worst("height", "entry_opening_height", "mm", min)
        worst("height_floor", "entry_opening_height_floor", "mm", min)
        worst("step", "entry_step_height", "mm", max,
              at=lambda s: f"{s}: " + per[s]["surfaces"][0][0])
        worst("rise", "entry_step_rise", "mm", max)
        worst("hands", "entry_handhold", "1", min, lambda m, k: len(m[k]),
              lambda s: f"{s}: " + (", ".join(_short(i) for i in per[s]["hands"]) or "なし"))
        for s, m in sorted(per.items()):
            sp = m["space"]
            text = (f"{s} ({'左' if sp.side > 0 else '右'}): 範囲 x {sp.x_back:.0f}〜{sp.x_front:.0f} mm、座面 {sp.z_seat:.0f} mm、"
                    f"床 {sp.z_floor:.0f} mm。座面より上の空き {m['width']:.0f} mm (x {m['width_at'][0]:.0f}〜"
                    f"{m['width_at'][1]:.0f})、床から座面までの空き {m['width_low']:.0f} mm")
            if "blocked" in m:
                text += (f"。開口の形を x {m['blocked_at']:.0f} から置くと、ふさぐ物 {len(m['blocked'])} 個"
                         + (f" ({', '.join(_short(i) for i in m['blocked'])})" if m["blocked"] else ""))
            effects.append({"name": text})
            if m["opens"]:
                effects.append({"name": f"{s}: 開けられるので数えない物: {', '.join(m['opens'])}"})
        return {"results": results, "effects": effects, "assumptions": list(ASSUMPTIONS)}

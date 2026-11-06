"""Frontal crash into a rigid wall with the frame as nodes and beams (C1)."""

from __future__ import annotations

import math

import numpy as np

from ..checks import _check
from ..crash_beam import known, measure
from ..crash_beam.mesh import MIN_ELEMENT_RADII, N_SUB, build_mesh
from ..crash_beam.sim import (CONTACT_PENALTY, CONTACT_SKIN, COURANT, MAX_FIELD_NODES, PENALTY, REC_DT, SPLIT_STOP_SHARE,
                              STOP_SHARE, TYRE_PENALTY_MAX, WINDOW, contract_has_node_motion, default_picks, member_end_hinges, member_forces, node_motion,
                              run_crash)
from ..model import DesignError
from ..units import G0
from .base import K2Tool, P, Pair, _box, _res

# no source in the repository for these: HIRAKU's choices, written into every record as assumptions
HINGE_BREAK_DEG = 60.0  # a beam breaks when the plastic hinge rotation at one of its ends passes this
WALL_MU = 0.3           # Coulomb friction between the frame's nodes and the wall
SERIES_DT = 1e-3        # s, the series are thinned to the record row nearest each multiple of this


def _lone_nodes(mesh, alive) -> list[int]:
    """The frame nodes with elements none of which is alive."""
    has, any_ = np.zeros(mesh.n, bool), np.zeros(mesh.n, bool)
    for e in range(mesh.ne):
        for k in mesh.con[e]:
            any_[k] = True
            if alive[e]:
                has[k] = True
    return [k for k in range(mesh.n_frame) if any_[k] and not has[k]]


class CrashBeamTool(K2Tool):
    """Frontal crash into a rigid wall (C1) with the design's frame as co-rotational beams that yield and
    fold (hiraku.solvers.k2_vehicle.crash_beam): the frame moves at `speed` into a rigid plane square to x
    touching its front-most node, until it stops or moves back, or time_limit.

    Results: peak_decel and mean_decel (g; the whole mass's deceleration, from the wall's impulse,
    averaged over 1 ms; the mean is the speed lost over the time to the stop), max_crush (mm, the largest
    shortening between the front-most nodes and the occupants' seat mount nodes, or the rear-most nodes
    without occupants), crumple_length (mm, the front-most node to the front face of the front-most
    occupant part box, along x, at the start) and occupant_space_margin (mm, crumple_length less max_crush:
    the quantity the hand calculation predicts), occupant_space_left (mm, the least room between the moving
    structure and any occupant part box, where = the occupant part; crash_beam.measure says how it is
    measured), battery_mount_force (N, where = node), broken_members (1, where = the ids),
    max_plastic_rotation (deg, where = member). Series deceleration (g) and crush (mm) every 1 ms. A field
    of kind node_motion when the contract has that kind (read from the schema at run time) and the mesh
    has at most 300 nodes.
    """

    name = "crash_beam"
    title = "壁への正面衝突 (フレームを梁でつぶす)"
    domain = "crash"
    stage = 3
    tests = ["C1"]
    params = (P("speed", "km/h", 40.0, "speed when the front meets the wall"),
              P("wall_mu", "1", WALL_MU, f"friction between the frame and the wall ({WALL_MU:g} when not given; "
                "no source, not checked)"),
              P("hinge_break_angle", "deg", HINGE_BREAK_DEG, f"a beam breaks when a plastic hinge at its end turns "
                f"past this ({HINGE_BREAK_DEG:g} deg when not given; no source, not checked)"),
              P("time_limit", "s", 0.2, "the run stops here if the vehicle has not stopped", run_option=True),
              P("stop_at_split", None, False, "the run stops when a piece of 3 % of the mass or more breaks off the "
                "seats' side (false when not given: the pieces touch each other, so the run goes on)", run_option=True))
    pairs = (Pair("peak_decel", "crash.linear_spring", "a_max", (("v", "speed"),),
                  "k from the design variable crush_stiffness (one linear spring); the beams yield and fold instead"),
             Pair("max_crush", "crash.linear_spring", "x_max", (("v", "speed"),),
                  "the hand calculation's crush is the whole front's; the beam model's is to the seat mounts"),
             Pair("occupant_space_margin", "crash.plastic_occupant_space_margin", "margin", (("v", "speed"),),
                  "the hand calculation takes the front tubes crushing straight at their mean crushing force; the beam "
                  "model's margin is the least room left to any occupant part box (a tower post or the dash bending back "
                  "into the foot box counts)"),
             Pair("frame_split_fraction", "crash.rail_column_split", "fraction", (),
                  "the hand calculation checks each rail behind the crush zone as a straight column under the crush "
                  "force; the beam model breaks members where the towers and the arm brackets bend the rails"),
             Pair("unsupported_load", "load_path.free_parts", "n", (),
                  "the path check (hiraku.catalog.builders.load_path.PATH_PREDICTION): the parts the load case loads, "
                  "followed by their contacts to the members; the tool counts the parts with mass it put on no node"))

    def own_checks(self) -> list[dict]:
        out = []
        c = known.cantilever_deflection()
        out.append(_check(f"cantilever 40x80x2.3 steel tube, {c['L']:g} m, {c['elements']} elements, 50 N at the tip, "
                          "held by damping: tip deflection", "tip_deflection", c["expected"], c["got"], "m", 2e-3,
                          "P L^3 / (3 E I), Euler-Bernoulli cantilever",
                          case_ja=f"長さ {c['L']:g} m の角パイプ (40×80×2.3、鋼) の片持ちの梁の先に 50 N をかけ、揺れを止めて"
                                  "読んだ先のたわみ",
                          source_ja="片持ちの梁の先の荷重のたわみ P L³ / (3 E I)"))
        p = known.cantilever_collapse()
        out.append(_check(f"the same cantilever's tip pushed slowly {p['push'] * 1000:.0f} mm (first yield at "
                          f"{p['yield_push'] * 1000:.1f} mm): tip force on the plateau", "collapse_load", p["expected"],
                          p["got"], "N", 3e-2, "M_p / L, the plastic hinge at the root (M_p = Z_p f_y)",
                          case_ja=f"同じ片持ちの梁の先を、ゆっくり {p['push'] * 1000:.0f} mm 押し下げる (降伏は "
                                  f"{p['yield_push'] * 1000:.1f} mm で始まる): 押す力が頭打ちになった所の大きさ",
                          source_ja="根元に塑性ヒンジができて崩れる荷重 M_p / L (M_p = 塑性断面係数 × 降伏応力)"))
        s = known.mass_on_spring()
        case = (f"{s['m']:.0f} kg on a 0.5 m elastic 40x40x2.3 tube hits the wall at 1 m/s (k = E A / L in series with "
                f"the wall's penalty spring, {s['k'] / 1e6:.1f} kN/mm)")
        case_ja = (f"弾性の角パイプ (40×40×2.3、長さ 0.5 m) の後ろに {s['m']:.0f} kg を載せ、1 m/s で壁に当てる "
                   f"(ばね = パイプの E A / L と壁の押し返しのばねの直列、{s['k'] / 1e6:.1f} kN/mm)")
        out.append(_check(case + ": peak wall force", "peak_force", s["expected_force"], s["peak_force"], "N", 3e-2,
                          "undamped mass-spring impact: F = v sqrt(k m), m with a third of the tube's mass",
                          case_ja=case_ja + ": 壁の力のいちばん大きい値",
                          source_ja="減衰のない質量とばねの衝突: F = v √(k m) (m にはパイプの重さの 1/3 を足す)"))
        out.append(_check(case + ": contact time", "contact_time", s["expected_time"], s["contact_time"], "s", 3e-2,
                          "half a period of the mass on the spring: pi sqrt(m / k)",
                          case_ja=case_ja + ": 壁に触れている時間",
                          source_ja="質量とばねの揺れの半周期 π √(m / k)"))
        out.append(_check(case + ": speed change of the whole mass (momentum change / mass) = the wall's impulse / mass",
                          "momentum_change", s["wall_impulse"] / s["total_mass"], s["momentum_change"] / s["total_mass"],
                          "m/s", 1e-9, "Newton's second law over the run: the beams' internal forces cancel",
                          case_ja=case_ja + ": 全体の運動量の変わり (全体の重さで割って速さの変わりにした) が、壁から受けた力積と同じか",
                          source_ja="運動の法則: 梁の中の力は打ち消し合うので、運動量を変えるのは壁の力だけ"))
        h = known.tubes_head_on()
        case = ("two elastic 40x40x2.3 tubes 0.5 m long, not joined, on one axis: 2000 kg behind the moving one (1 m/s), "
                "1000 kg behind the other at rest; no wall (springs in "
                f"series: each tube's E A / L and the contact's penalty spring, {h['k'] / 1e6:.1f} kN/mm)")
        case_ja = ("つながっていない弾性の角パイプ (40×40×2.3、長さ 0.5 m) 2 本を同じ軸に並べ、後ろに 2000 kg を載せた片方を 1 m/s で"
                   f"止まっているもう片方 (後ろに 1000 kg) に当てる (壁なし。ばね = 2 本の E A / L と接触の押し返しのばねの直列、"
                   f"{h['k'] / 1e6:.1f} kN/mm)")
        out.append(_check(case + ": peak contact force", "peak_force", h["expected_force"], h["peak_force"], "N", 3e-2,
                          "undamped impact of two masses through a spring: F = v sqrt(k mu), mu the reduced mass (each "
                          "with a third of its tube)",
                          case_ja=case_ja + ": 接触の力のいちばん大きい値",
                          source_ja="減衰のない 2 つの質量のばねを介した衝突: F = v √(k μ) (μ は換算質量)"))
        out.append(_check(case + ": contact time", "contact_time", h["expected_time"], h["contact_time"], "s", 3e-2,
                          "half a period: pi sqrt(mu / k)", case_ja=case_ja + ": 触れている時間",
                          source_ja="半周期 π √(μ / k)"))
        out.append(_check(case + ": speed of the struck tube after", "rebound_speed", h["expected_v2"], h["v2"], "m/s",
                          1e-2, "elastic impact (momentum and energy kept): v2' = 2 M1 / (M1 + M2) v",
                          case_ja=case_ja + ": 当てられた側の、離れたあとの速さ",
                          source_ja="弾性衝突 (運動量とエネルギーが保たれる): v2' = 2 M1 / (M1 + M2) v"))
        out.append(_check(case + ": the total momentum after / before", "momentum_ratio", 1.0,
                          h["momentum_after"][0] / h["momentum_before"][0], "1", 1e-9,
                          "the contact forces are equal and opposite: the momentum is kept",
                          case_ja=case_ja + ": 全体の運動量の、後と前の比",
                          source_ja="接触の力は向きが反対で大きさが同じなので、運動量は保たれる"))
        c = known.tube_crosswise()
        cd = c["contact_distance"]
        out.append(_check(f"a 40x40x2.3 tube 0.6 m long moving at {c['speed']:g} m/s crosswise onto another at rest "
                          f"(no gravity): the least distance between the axes (the contact radii sum to {cd * 1000:.0f} mm; "
                          f"the penetration bound {c['bound'] * 1000:.2f} mm)", "least_axis_distance", cd, c["least"], "m",
                          c["bound"] / cd, "all the relative kinetic energy in the contact spring at most: "
                          "penetration <= v sqrt(mu / k), k the contact's penalty spring at the middles",
                          case_ja=f"角パイプ (40×40×2.3、長さ 0.6 m) を、止まっているもう 1 本の上に {c['speed']:g} m/s で"
                                  "十字に落とす (重力なし): 2 本の軸のいちばん近い距離 (接触の半径の和からのめり込みが"
                                  f"上限 {c['bound'] * 1000:.2f} mm 以内か)",
                          source_ja="相対の運動エネルギーが全部接触のばねに入っても、めり込みは v √(μ / k) まで"))
        w = known.wheel_into_wall()
        m, k, v = w["mass"], w["k_t"], w["speed"]
        case = (f"a wheel node ({m:g} kg, radius {w['radius']:g} m, tyre {k / 1000:g} N/mm, section height "
                f"{w['section'] * 1000:.0f} mm) moving at {v:g} m/s into the wall (the rim does not reach it)")
        case_ja = (f"車輪の点 ({m:g} kg、半径 {w['radius']:g} m、タイヤのばね {k / 1000:g} N/mm、タイヤの断面の高さ "
                   f"{w['section'] * 1000:.0f} mm) を {v:g} m/s で壁に当てる (リムは壁に届かない)")
        out.append(_check(case + ": peak wall force", "peak_force", v * math.sqrt(k * m), w["peak_force"], "N", 1e-3,
                          "undamped mass-spring impact: F = v sqrt(k_t m)", case_ja=case_ja + ": 壁の力のいちばん大きい値",
                          source_ja="減衰のない質量とばねの衝突: F = v √(k_t m)"))
        out.append(_check(case + ": contact time", "contact_time", math.pi * math.sqrt(m / k), w["contact_time"], "s",
                          1e-3, "half a period of the mass on the tyre's spring: pi sqrt(m / k_t)",
                          case_ja=case_ja + ": 壁に触れている時間", source_ja="質量とばねの揺れの半周期 π √(m / k_t)"))
        r = known.wheel_into_wall(speed=40.0 / 3.6)
        rim = r["wall"] - (r["radius"] - r["section"])
        out.append(_check(f"the same wheel node at 40 km/h (the free tyre would deflect "
                          f"{r['speed'] * math.sqrt(m / k) * 1000:.0f} mm, more than its section height): the deepest x "
                          "of the node", "deepest_x", rim, r["deepest"], "m", 1e-9,
                          "the rim (radius r - section height) reaches the wall and stops there, perfectly plastic: "
                          "x = wall - (r - h)",
                          case_ja="同じ車輪の点を 40 km/h で当てる (タイヤのたわみが断面の高さを超える): 点がいちばん進んだ x",
                          source_ja="リム (半径 = r - タイヤの断面の高さ) が壁に届いたらそこで止まる (跳ね返らない): "
                                    "x = 壁 - (r - h)"))
        return out

    def run(self, design: dict, load_case: dict, predictions: list[dict], params: dict | None = None) -> dict:
        self._fields = []
        rec = super().run(design, load_case, predictions, params)
        if self._fields and rec["status"] == "ok":
            rec["fields"] = self._fields
        return rec

    def _run(self, design: dict, lp: dict) -> dict:
        self._places = []
        speed = self.q(lp, "speed", "m/s")
        if speed is None or speed <= 0.0:
            raise DesignError("the crash needs a positive speed")
        mu = self.q(lp, "wall_mu", "1")
        lim = self.q(lp, "hinge_break_angle", "rad")
        t_lim = self.q(lp, "time_limit", "s")
        notes = []
        if not self.given(lp, "wall_mu"):
            notes.append(f"wall friction mu = {mu:g} (assumed; no source in the repository, 確かめていない)")
        if not self.given(lp, "hinge_break_angle"):
            notes.append(f"a beam element breaks (is removed) when the plastic hinge rotation at one of its ends passes "
                         f"{math.degrees(lim):g} deg (assumed; no source in the repository, 確かめていない)")
        stop_split = self.flag(lp, "stop_at_split")
        mesh = build_mesh(design)
        occ = [p for p in design.get("parts", []) if p.get("kind") == "occupant"]
        seat = sorted({k for p in occ for k in mesh.node_parts.get(p["id"], {})})
        xr = mesh.x0[:mesh.n_frame, 0]
        if seat:
            ref, ref_text = seat, "the occupants' seat mount nodes (" + ", ".join(mesh.node_ids[k] for k in seat) + ")"
        else:
            ref = [int(i) for i in np.nonzero(xr <= xr.min() + 1e-9)[0]]
            ref_text = "the rear-most nodes (no occupant on board: the vehicle's length)"
        front = [int(i) for i in np.nonzero(xr >= xr.max() - 1e-9)[0]]
        # the wall touches the front-most of the frame and the part boxes it carries, so no box starts past it
        carried = [p for p in design.get("parts", []) if p.get("box") and mesh.node_parts.get(p["id"])]
        box_x = max((measure.box_front(p) for p in carried), default=-math.inf)
        wall = max(float(xr.max()), box_x)
        run = run_crash(mesh, speed, t_lim, mu=mu, hinge_lim=lim, wall=wall, keep=ref, stop_at_split=stop_split)
        split = run.extra.get("split")
        cut = split is not None and stop_split  # the run stopped at the split
        M = run.total_mass
        dec = run.decel() / G0
        tt = run.t_all
        v_end = float(run.momentum[-1][0]) / M
        t_end = float(tt[-1])
        mean = (speed - max(v_end, 0.0)) / t_end / G0 if t_end > 0 else 0.0
        crush = measure.crush(run, ref)
        k_crush = int(np.argmax(crush))
        res = [_res("peak_decel", float(dec.max()), "g")]
        where_crush = "front-most nodes to " + ("seat mounts" if seat else "rear-most nodes")
        if not cut:
            res += [_res("mean_decel", mean, "g"), _res("max_crush", float(crush.max()) * 1000.0, "mm", where_crush)]
        if split is not None:
            split_members = sorted({mesh.member_of[e] for e in split["elements"]})
            k_split = int(np.searchsorted(tt, split["t"] - 1e-12))
            res += [_res("frame_split_time", split["t"], "s", ", ".join(split_members)),
                    _res("crush_at_split", float(crush[:k_split + 1].max()) * 1000.0, "mm", where_crush)]
            said = (f"the frame split at {split['t'] * 1000:.1f} ms when {', '.join(split_members)} broke: a piece with "
                    f"{split['share'] * 100:.1f} % of the whole mass came apart from {ref_text}; a split off of a piece "
                    f"of {SPLIT_STOP_SHARE * 100:g} % of the mass or more is NG for C1 (frame_split_fraction, "
                    "frame_split_time says when). ")
            if cut:
                notes.append(said + "The run stops there (stop_at_split): peak_decel is up to the split, crush_at_split "
                             "is the crush then (not the final crush), occupant_space_margin is the least room up to "
                             "then (an upper bound), and mean_decel, max_crush, crush_margin and occupant_space_left are "
                             "not computed (the vehicle had not stopped)")
            else:
                notes.append(said + "The pieces touch each other (element contact), so the run goes on past the split: "
                             "peak_decel, mean_decel, max_crush, occupant_space_margin and occupant_space_left are over "
                             "the whole run (the broken-off piece pushed on by the rest), crush_at_split is the largest "
                             "crush up to the split")
        small = [p for p in run.extra.get("pieces", []) if split is None or p["share"] < split["share"]]
        if small:
            notes.append("smaller pieces broken off the seats' side, touching the rest by the element contact: " + "; ".join(
                f"at {p['t'] * 1000:.1f} ms, {len(p['nodes'])} nodes ({p['share'] * 100:.1f} % of the mass: "
                + ", ".join(sorted({mesh.member_of[e] for e in mesh.frame_elements()
                                    if all(int(n) in set(p['nodes']) for n in mesh.con[e])}) or ["no whole member"]) + ")"
                for p in small))
        # the contract's unsupported_load (docs/contract.md 手計算の役目): the parts with mass the mesh put on no node
        lost = [p["id"] for p in design.get("parts", []) if float(p.get("mass_kg", 0.0)) > 0.0
                and not mesh.node_parts.get(p["id"])]
        res.append(_res("unsupported_load", float(len(lost)), "1", ", ".join(lost) if lost else None))
        notes.append("unsupported_load: the parts with mass put on no mesh node (each part's mass goes on its "
                     "carried_by nodes, or the nearest support's node; whether a part's shape touches the frame is the "
                     "hand calculation's path check, load_path.free_parts): " + (", ".join(lost) if lost else "none"))
        # C1's second criterion: the largest share of the whole mass in a piece broken off the seats' side that can
        # come into the occupants' space (0: none); pieces that cannot (e.g. the front cross member, below the feet)
        # are crushed debris, named in a note
        reaching = [p for p in run.extra.get("pieces", []) if measure.reaches_occupants(run, p["nodes"], occ)]
        res.append(_res("frame_split_fraction", max((p["share"] for p in reaching), default=0.0), "1"))
        notes.append("frame_split_fraction: the largest share of the whole mass in a piece broken off the seats' side "
                     "with a node in an occupant part box's y-z extent, ahead of its rear face, at the start (a piece that "
                     "can come into their space); "
                     + (f"{len(run.extra['pieces']) - len(reaching)} other piece(s) cannot and are not counted"
                        if len(run.extra.get("pieces", [])) > len(reaching) else "every broken-off piece is counted"))
        if occ:
            # the crush the structure ahead of the occupants can take, as the hand calculation reads it: from the
            # front-most node back to the front face of the front-most occupant part box, along x
            front_occ = max(occ, key=measure.box_front)
            crumple = float(xr.max()) - measure.box_front(front_occ)
            res.append(_res("crumple_length", crumple * 1000.0, "mm", front_occ["id"]))
            if not cut:
                res.append(_res("crush_margin", (crumple - float(crush.max())) * 1000.0, "mm", front_occ["id"]))
                notes.append("crush_margin: crumple_length (the front-most node to the front face of the front-most "
                             "occupant part box, along x, at the start) less max_crush")
        room = measure.occupant_room(run, occ, mesh.node_parts)
        if room is not None:
            # C1's criterion: the least room to any occupant part box over the run (up to the split, when the frame
            # split: an upper bound, the rest of the vehicle moving on after it)
            res.append(_res("occupant_space_margin", room["room"] * 1000.0, "mm", room["part"]))
            notes.append("occupant_space_margin: the least room over the run from the structure ahead of an occupant "
                         "part box to the box's front face (occupant_space_left; negative: the structure is inside the box)"
                         + ("; up to the split, so an upper bound (the vehicle moves on after it)" if cut else ""))
        # hot_spots: each frame member's least room to an occupant box, a place the hand calculation (the crush
        # from the front) does not look at (e.g. a side rail bending into the foot box); as the load case's
        # criterion quantity (occupant_space_margin), the quantity hot_spots reads at a place
        self._places = [{"where": mid, "quantity": "occupant_space_margin", "value": r_["room"] * 1000.0, "unit": "mm",
                         "judged": False}
                        for mid, r_ in sorted(measure.occupant_room_by_member(run, occ, mesh.node_parts).items())]
        if self._places:
            notes.append("hot_spots: each frame member's least room (over the run" + (", up to the split" if cut else "")
                         + ") to an occupant part box, read as occupant_space_left (the place's occupant_space_margin)")
        if room is not None and not cut:
            res.append(_res("occupant_space_left", room["room"] * 1000.0, "mm", room["part"]))
        elif occ and room is None:
            notes.append("no structure is ahead of the occupants' part boxes (in their y-z extent): occupant_space_left "
                         "is not given")
        past = measure.parts_past_wall(run, carried, mesh.node_parts)
        if past:
            notes.append("part boxes past the wall face, taken as crushed against it (the boxes have no contact; they "
                         "move with their carried_by nodes): " + ", ".join(f"{p} {d * 1000:.1f} mm" for p, d in
                                                                         sorted(past.items(), key=lambda kv: -kv[1])))
        bat = [p for p in design.get("parts", []) if p.get("kind") == "battery"]
        bat_row = None
        if bat:
            share = {}
            for p in bat:
                for k, kg in mesh.node_parts.get(p["id"], {}).items():
                    share[k] = share.get(k, 0.0) + kg
            f, node, bat_row = measure.mount_force(run, share)
            res.append(_res("battery_mount_force", f, "N", mesh.node_ids[node] if node >= 0 else None))
        broken = measure.broken_members(run)
        res.append(_res("broken_members", float(len(broken)), "1", ", ".join(broken) if broken else None))
        hinges = measure.member_hinges(run)
        worst = max(hinges, key=lambda m: hinges[m])
        res.append(_res("max_plastic_rotation", math.degrees(hinges[worst]), "deg", worst if hinges[worst] > 0 else None))
        # the record row nearest each whole ms, and the last row
        marks = np.arange(0.0, tt[-1] + 1e-12, SERIES_DT)
        rows = sorted({int(k) for k in np.clip(np.rint(marks / run.chunk), 0, len(tt) - 1)} | {len(tt) - 1})
        t_s = [round(float(tt[k]), 6) for k in rows]
        series = [{"channel": "deceleration", "unit": "g", "t_s": t_s, "values": [float(dec[k]) for k in rows]},
                  {"channel": "crush", "unit": "mm", "t_s": t_s, "values": [float(crush[k]) * 1000.0 for k in rows]}]
        # the members' axial force and the plastic hinge rotation at each end, at node_motion's frames
        picks = default_picks(run, dec)
        t_p = [round(float(tt[k]), 6) for k in picks]
        forces = [member_forces(run, k) for k in picks]
        hinges_t = [member_end_hinges(run, k) for k in picks]
        for m in sorted(mesh.members()):
            series.append({"channel": f"member_axial_force.{m}", "unit": "N", "t_s": t_p,
                           "values": [float(f.get(m, 0.0)) for f in forces]})
        for (m, end) in sorted(hinges_t[-1]):
            series.append({"channel": f"member_hinge.{m}.{end}", "unit": "deg", "t_s": t_p,
                           "values": [math.degrees(h[(m, end)]) for h in hinges_t]})
        notes.append("member_axial_force.<member> (N, + tension): of the member's elements not broken then, the axial "
                     "force E A / L0 (length - L0 - plastic elongation) of the largest size, 0 when all are broken; "
                     "member_hinge.<member>.<end node> (deg): the plastic hinge rotation sqrt(theta_y^2 + theta_z^2) at "
                     "that end of the member; both at the node_motion field's frames")
        lo, hi = mesh.x0.min(axis=0), mesh.x0.max(axis=0)
        width, height = hi[1] - lo[1], hi[2]
        scene = [_box("wall", "剛体の壁", (run.wall_x, lo[1] - 0.25 * width, 0.0),
                      (run.wall_x + 0.2, hi[1] + 0.25 * width, 1.5 * height))]
        if contract_has_node_motion():
            if mesh.n <= MAX_FIELD_NODES:
                self._fields = [node_motion(run, f"フレームの動き ({speed * 3.6:g} km/h で壁に当たる)", decel_g=dec)]
            else:
                notes.append(f"no node_motion field: the mesh has {mesh.n} nodes, more than the contract's "
                             f"{MAX_FIELD_NODES}")
        frame_kg = mesh.frame_mass
        cst = run.extra["contact"]
        n_beams = len(mesh.frame_elements())
        lone = _lone_nodes(mesh, run.final.alive)
        if lone:
            notes.append(f"{len(lone)} nodes ({float(mesh.mass[lone].sum()):.1f} kg) lost all their elements by the end; "
                         "such a node touches only the wall, not the elements: " + ", ".join(mesh.node_ids[k] for k in lone))
        notes += [
            f"the frame as {n_beams} co-rotational 3-D beam elements on {mesh.n_frame} nodes (each member in up to {N_SUB} "
            f"elements, none shorter than {MIN_ELEMENT_RADII:g} radii of gyration of its section), explicit central "
            f"differences, time step {run.dt * 1e6:.2f} us = {COURANT:g} (Courant factor) x the elements' stable step "
            "2 / omega_max; the rotational inertia of the nodes scaled up to at least (element mass / 2) x length^2 / 12 "
            "per element end (rotational mass scaling: no translational mass is added)",
            "elastic-perfectly plastic: axial yield at A f_y, plastic hinges at the element ends at M_p = Z_p f_y about "
            "each local axis on its own, reduced by the axial force as M_p (1 - (N / N_p)^2), torsion capped at the "
            "plastic torque; the plastic deformation stays (no spring back); no strain hardening, no strain-rate "
            "effect, no local buckling of the tube walls (a tube squashes at A f_y instead of folding progressively)",
            f"mass {M:.1f} kg: frame {frame_kg:.1f} kg on its element nodes, parts {M - frame_kg:.1f} kg; "
            + "; ".join(mesh.notes),
            f"the wall: a rigid plane x = {run.wall_x * 1000:.0f} mm, at the front-most of the frame's nodes "
            f"({', '.join(mesh.node_ids[k] for k in front)}, x = {xr.max() * 1000:.0f} mm) and the part boxes they carry; "
            f"it touches the frame's nodes by a penalty spring {PENALTY:g} x the node's mass / dt^2, and the wheel nodes "
            "through the tyre: when a wheel centre's x + its radius passes the wall, a spring of the tyre's radial "
            "stiffness on the deflection (compressive only, no damping: the tyre gives back what it holds); when the "
            "deflection reaches the tyre's section height the rim is at the wall and stops there, perfectly plastic "
            "(no spring-back from the rim; HIRAKU's choice, the rim's own crushing is not modelled). "
            + "; ".join(f"{wid}: {w['k_t'] / 1000:g} N/mm, section height {w['section'] * 1000:.0f} mm, rim radius "
                        f"{(w['radius'] - w['section']) * 1000:.0f} mm" for wid, w in mesh.wheels.items())
            + (f"; the tyre spring on the wheel node's mass (omega dt)^2 = k_t dt^2 / m = {run.extra['tyre_omega_dt2']:.2g} "
               f"at most (the kernel allows {TYRE_PENALTY_MAX:g})" if mesh.wheels else "")
            + "; the part boxes and the bumper do not touch the wall",
            *mesh.tyre_notes,
            f"contact between the frame's elements that share no node (pieces broken apart, a member folding onto "
            f"another), frictionless: each element a capsule of radius half its section's larger outer dimension "
            f"(pairs closer than that at the start, near the joints: from their starting distance); a penalty spring "
            f"{CONTACT_PENALTY:g} x the contact point's mass / dt^2 between the closest points of the two axes, the force "
            f"shared to the four nodes (momentum kept); the pairs looked at are listed again whenever a node has moved "
            f"{CONTACT_SKIN * 500:g} mm relative to the rest (the list keeps the pairs within {CONTACT_SKIN * 1000:g} mm "
            f"of touching). In this run: {cst['pairs_possible']} pairs can touch, the list made {cst['lists']} times, at "
            f"most {cst['longest']} pairs on it, {cst['pair_steps']} pair-steps in contact. The wheels and the part boxes "
            "do not touch the elements",
            "no gravity and no ground (the crash forces are many times the weight over the run)",
            # what this C1 model leaves out (the user's decision: C1 judged on this model to the submission)
            "not in this model: the floor and deck plates as plates (their mass is on the frame's nodes, but they "
            "do not brace the frame) and the rims' crushing (a wheel's rim stops at the wall once its tyre is "
            "through). Either can move the result a lot (not checked): with the floor as a rigid box the least room "
            "on demo v1 was -12 mm, without it -497 mm with a split, against -196 mm here (K2's runs, 2026-11-06). "
            "床の板が骨組みを支える力と、車輪のリムがつぶれることは、このモデルに入っていない (結果を大きく動かすことがある)",
            f"peak_decel and the deceleration series: the whole mass's deceleration, the wall's impulse over each "
            f"{WINDOW * 1000:g} ms / (the total mass x {WINDOW * 1000:g} ms), recorded every {REC_DT * 1000:g} ms; "
            "peak_decel is the input to the occupants (the deceleration of the whole vehicle they ride in, not of the "
            "seat mounts on their own); mean_decel: the speed lost / the time to the stop",
            f"max_crush and the crush series: how far {ref_text} moved toward the wall after the frame's front-most "
            "node reached it (the shortening of the structure ahead of them while the front is held at the wall; a "
            "broken-off front piece moving back from the wall is not counted)",
            "occupant_space_left: the occupant part boxes move with their carried_by nodes; the least x "
            "distance over the run from the structure ahead of a box (mesh nodes and element middles inside the box's "
            "y-z extent, ahead of its rear face) back to the box's front face; negative: the structure is inside the box",
            f"run: {run.stopped} at {t_end * 1000:.1f} ms ({run.steps} steps); speed at the end {v_end:.2f} m/s",
        ]
        if v_end > STOP_SHARE * speed:
            pushed = np.nonzero(run.impulse[:, 0] != 0.0)[0]
            t_wall = float(tt[pushed[-1] + 1]) if len(pushed) else 0.0
            notes.append(f"the vehicle had not stopped at the time limit ({v_end:.2f} m/s toward the wall, more than "
                         f"{STOP_SHARE * 100:g} % of the start): max_crush, mean_decel and the rooms are up to "
                         f"{t_end * 1000:.0f} ms and would change in a longer run"
                         + (f"; nothing touched the wall after {t_wall * 1000:.1f} ms (the frame, or the seats' piece of "
                            "it, moving on with nothing ahead of it that K2 makes touch)" if t_end - t_wall > 0.01 else ""))
        if room is not None:
            notes.append(f"occupant_space_left at the start: {room['start'] * 1000:.0f} mm "
                         f"(the least over the run at {tt[room['row']] * 1000:.1f} ms)")
        if bat_row is not None:
            notes.append(f"battery_mount_force: the battery's mass share on each of its carried_by nodes x the node's "
                         f"acceleration averaged over {WINDOW * 1000:g} ms (largest at {tt[bat_row] * 1000:.1f} ms)")
        if run.first_yield is not None:
            notes.append(f"first yield at {run.first_yield * 1000:.2f} ms")
        if broken:
            notes.append(f"first break at {min(run.broken_at.values()) * 1000:.2f} ms ({broken[0]})")
        notes.append(f"the largest crush at {tt[k_crush] * 1000:.1f} ms")
        return {"results": res, "series": series, "scene": scene, "assumptions": notes,
                "places": getattr(self, "_places", [])}

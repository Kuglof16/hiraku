"""Frontal crash into a rigid wall with the frame as nodes and beams (C1)."""

from __future__ import annotations

import math

import numpy as np

from ..checks import _check
from ..crash_beam import known, measure
from ..crash_beam.mesh import MIN_ELEMENT_RADII, N_SUB, build_mesh
from ..crash_beam.sim import (COURANT, MAX_FIELD_NODES, PENALTY, REC_DT, WINDOW, contract_has_node_motion, node_motion,
                              run_crash)
from ..model import DesignError
from ..units import G0
from .base import K2Tool, P, Pair, _box, _res

# no source in the repository for these: HIRAKU's choices, written into every record as assumptions
HINGE_BREAK_DEG = 60.0  # a beam breaks when the plastic hinge rotation at one of its ends passes this
WALL_MU = 0.3           # Coulomb friction between the frame's nodes and the wall
SERIES_DT = 1e-3        # s, the series are thinned to the record row nearest each multiple of this


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
              P("time_limit", "s", 0.2, "the run stops here if the vehicle has not stopped", run_option=True))
    pairs = (Pair("peak_decel", "crash.linear_spring", "a_max", (("v", "speed"),),
                  "k from the design variable crush_stiffness (one linear spring); the beams yield and fold instead"),
             Pair("max_crush", "crash.linear_spring", "x_max", (("v", "speed"),),
                  "the hand calculation's crush is the whole front's; the beam model's is to the seat mounts"))

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
        return out

    def run(self, design: dict, load_case: dict, predictions: list[dict], params: dict | None = None) -> dict:
        self._fields = []
        rec = super().run(design, load_case, predictions, params)
        if self._fields and rec["status"] == "ok":
            rec["fields"] = self._fields
        return rec

    def _run(self, design: dict, lp: dict) -> dict:
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
        mesh = build_mesh(design)
        run = run_crash(mesh, speed, t_lim, mu=mu, hinge_lim=lim)
        M = run.total_mass
        dec = run.decel() / G0
        tt = run.t_all
        v_end = float(run.momentum[-1][0]) / M
        t_end = float(tt[-1])
        mean = (speed - max(v_end, 0.0)) / t_end / G0 if t_end > 0 else 0.0
        occ =[p for p in design.get("parts", []) if p.get("kind") == "occupant"]
        seat = sorted({k for p in occ for k in mesh.node_parts.get(p["id"], {})})
        if seat:
            ref, ref_text = seat, "the occupants' seat mount nodes (" + ", ".join(mesh.node_ids[k] for k in seat) + ")"
        else:
            xr = mesh.x0[:, 0]
            ref = [int(i) for i in np.nonzero(xr <= xr.min() + 1e-9)[0]]
            ref_text = "the rear-most nodes (no occupant on board: the vehicle's length)"
        crush = measure.shortening(run, ref)
        k_crush = int(np.argmax(crush))
        res = [_res("peak_decel", float(dec.max()), "g"),
               _res("mean_decel", mean, "g"),
               _res("max_crush", float(crush.max()) * 1000.0, "mm", "front-most nodes to " + ("seat mounts" if seat
                                                                                               else "rear-most nodes"))]
        if occ:
            # the crush the structure ahead of the occupants can take, as the hand calculation reads it: from the
            # front-most node back to the front face of the front-most occupant part box, along x
            front_occ = max(occ, key=lambda p: p["box"]["center_mm"][0] + p["box"]["size_mm"][0] / 2.0)
            face = (front_occ["box"]["center_mm"][0] + front_occ["box"]["size_mm"][0] / 2.0) / 1000.0
            crumple = float(mesh.x0[:, 0].max()) - face
            res.append(_res("crumple_length", crumple * 1000.0, "mm", front_occ["id"]))
            res.append(_res("occupant_space_margin", (crumple - float(crush.max())) * 1000.0, "mm", front_occ["id"]))
            notes.append("occupant_space_margin: crumple_length (the front-most node to the front face of the "
                         "front-most occupant part box, along x, at the start) less max_crush; the hand calculation "
                         "crash_beam.occupant_margin reads the same")
        room = measure.occupant_room(run, occ, mesh.node_parts)
        if room is not None:
            res.append(_res("occupant_space_left", room["room"] * 1000.0, "mm", room["part"]))
        elif occ:
            notes.append("no structure is ahead of the occupants' part boxes (in their y-z extent): occupant_space_left "
                         "is not given")
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
        notes += [
            f"the frame as {mesh.ne} co-rotational 3-D beam elements on {mesh.n} nodes (each member in up to {N_SUB} "
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
            f"the wall: a rigid plane x = {run.wall_x * 1000:.0f} mm touching the front-most frame node(s) "
            f"({', '.join(mesh.node_ids[k] for k in measure.front_nodes(run))}); contact of the nodes only, by a penalty "
            f"spring {PENALTY:g} x the node's mass / dt^2; the part boxes, the wheels and the bumper do not touch the "
            "wall, and the frame's pieces do not touch each other (a broken-off piece does not push on the rest)",
            "no gravity and no ground (the crash forces are many times the weight over the run)",
            f"peak_decel and the deceleration series: the wall's impulse over each {WINDOW * 1000:g} ms / (the total mass x "
            f"{WINDOW * 1000:g} ms), recorded every {REC_DT * 1000:g} ms; mean_decel: the speed lost / the time to the "
            "stop",
            f"max_crush: the shortening of the x distance from the front-most nodes back to {ref_text}",
            "occupant_space_left: the occupant part boxes move with their carried_by nodes; the least x "
            "distance over the run from the structure ahead of a box (mesh nodes and element middles inside the box's "
            "y-z extent, ahead of its rear face) back to the box's front face; negative: the structure is inside the box",
            f"run: {run.stopped} at {t_end * 1000:.1f} ms ({run.steps} steps); speed at the end {v_end:.2f} m/s",
        ]
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
        return {"results": res, "series": series, "scene": scene, "assumptions": notes}

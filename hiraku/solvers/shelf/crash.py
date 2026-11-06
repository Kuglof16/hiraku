"""crash (C1): frontal impact into a rigid wall.

The body is a rigid mass. The bumper is a linear spring up to its stroke
(bumper_stiffness, bumper_stroke); behind it the crush zone, the front frame's
rails from the front of the frame back to the first cross member behind the
front wheels' mounts (library_crash.crush_zone), folds plastically at the sum of
its tubes' mean crushing forces (library_crash.tube_mean_crush_force). The crush
does not spring back. The crush can use 70 % of the crush zone's length; past
that the folded stack is a stiff spring (a hard stop). The motion is solved
exactly in pieces (bumper arc, constant-force crushing, hard stop) up to the
standstill. At the largest deceleration the wall force is put on the front frame
nodes and inertia relief loads every mass with its d'Alembert force, so K1 shows
the frame stress and what the battery mounts carry.

Design variables read: bumper_stiffness (N/mm), bumper_stroke (mm).
crush_stiffness is no longer read (the crush force comes from the tubes).

Comparison on the demo design (offroad-4w-v1, 646 kg two_cargo, 40 km/h): COMPARISON below, appended
to this docstring and written in every run's assumptions.
"""

from __future__ import annotations

import math

import numpy as np

from ..k0_handcalc import library_crash, units
from ..k0_handcalc.units import STANDARD_GRAVITY
from ..k0_handcalc.vehicle import DesignError, mass_summary, support_loads
from ..k1_frame import analyze
from . import fixtures, scene
from .base import LOADING, BuiltinTool, LoadCaseError, Param, Predicts, Record, check_record, on_board, param_value, result
from .frame_static import frame_outputs

COMPARISON = (
    "A (this tool: two rails folding at 48.4 kN each, 96.7 kN, 15.3 g while crushing, crush 489 mm = 80 mm "
    "bumper + 409 mm folds, 81.1 ms to the stop, mean 14.0 g (speed / stop time), battery mount force "
    "7.5 kN, occupant space left +256 mm of 745 mm, not bottomed: the usable length is 819 mm), the energy "
    "balance (crash.plastic_crush: the same 489 mm, 15.3 g and 81.1 ms) and B (the K2 crash_beam tool: "
    "beams with plastic hinges, axial force up to A fy, no local buckling of the tubes: 536 mm, peak 64.0 g, "
    "mean 11.9 g, 94.9 ms, battery mount force 15.4 kN, occupant_space_margin +194 mm). B's peak is about the "
    "rails' squash load A fy (531 mm^2 x 245 MPa = 130 kN per rail, 2.7 times the folding mean force), "
    "since without local buckling a tube does not fold; A's folding force is a mean over the folds and "
    "leaves out the first peak, so A's peak equals its mean. B crushes further at a lower mean deceleration, "
    "so after its peak its force is on average below A's folding force (how, in B's hinges, was not "
    "looked at). A stops the "
    "crush at 70 % of the crush zone's length (the folds' solid length) and then a stiff spring takes the "
    "rest (crush_bottomed); B has no such limit; neither bottoms out here. The occupant space margins "
    "compare: A's +256 mm and B's occupant_space_margin +194 mm (as K2 gave it; B crushes 47 mm further).")
__doc__ += "\n" + COMPARISON + "\n"

FRONT_VARS = {"bumper_stiffness": "N/mm", "bumper_stroke": "mm"}
NOT_READ = "crush_stiffness"


def bumper_from_design(design: Record) -> tuple[float, float]:
    """(k_b N/m, stroke m) from design.variables."""
    vals, missing = {}, []
    for k, u in FRONT_VARS.items():
        q = design.get("variables", {}).get(k)
        if q is None:
            missing.append(f"{k} [{u}]")
            continue
        try:
            vals[k] = units.to_unit(q, u, k)
        except ValueError as e:
            raise DesignError(f"design variable {e}") from None
    if missing:
        raise DesignError("the design has no crash data for: " + ", ".join(missing) + "; add them to design.variables")
    return vals["bumper_stiffness"] * 1000.0, vals["bumper_stroke"] / 1000.0


def plastic_impact(mass: float, speed: float, k_b: float, stroke: float, F_m: float, c_max: float,
                   k_hard: float, n_out: int = 300) -> dict:
    """Impact at speed (m/s) up to the standstill, SI. Bumper: linear (k_b) up to its stroke; crush zone:
    rigid until the force reaches F_m, then folding at F_m (no spring back) up to c_max; then a hard stop
    F = F_m + k_hard delta. Exact piecewise solution. Returns the series and the end values."""
    if mass <= 0 or speed <= 0:
        raise ValueError("mass and speed must be positive")
    if F_m <= 0 or k_hard <= 0:
        raise ValueError("the crush zone needs a positive crushing force and hard-stop stiffness")
    E0 = 0.5 * mass * speed * speed
    u_star = min(stroke, F_m / k_b) if k_b > 0 else 0.0  # bumper travel when the crush zone starts folding
    w = math.sqrt(k_b / mass) if k_b > 0 else 0.0
    phases = []  # (t0, t1, kind, data)
    t = 0.0
    if u_star > 0 and E0 <= 0.5 * k_b * u_star ** 2:  # the bumper alone stops the vehicle
        t1 = math.pi / 2.0 / w
        phases.append((0.0, t1, "bumper", {}))
        u_end, c_end, d_end, t_stop = speed / w, 0.0, 0.0, t1
    else:
        if u_star > 0:
            t1 = math.asin(min(1.0, w * u_star / speed)) / w
            phases.append((0.0, t1, "bumper", {}))
            t = t1
        v1 = math.sqrt(max(speed * speed - (k_b * u_star * u_star / mass if k_b > 0 else 0.0), 0.0))
        a = F_m / mass
        c_free = v1 * v1 / (2.0 * a)
        if c_free <= c_max:
            phases.append((t, t + v1 / a, "crush", {"v": v1}))
            u_end, c_end, d_end, t_stop = u_star, c_free, 0.0, t + v1 / a
        else:
            tb = (v1 - math.sqrt(v1 * v1 - 2.0 * a * c_max)) / a
            v2 = math.sqrt(v1 * v1 - 2.0 * a * c_max)
            phases.append((t, t + tb, "crush", {"v": v1}))
            wh = math.sqrt(k_hard / mass)
            th = math.atan2(v2 * k_hard, F_m * wh) / wh
            phases.append((t + tb, t + tb + th, "hard", {"v": v2, "w": wh}))
            d_end = (-F_m + math.sqrt(F_m * F_m + k_hard * mass * v2 * v2)) / k_hard
            u_end, c_end, t_stop = u_star, c_max, t + tb + th

    def state(tq: float, side: int = 1) -> tuple[float, float, float, float]:
        """(x front travel, crush c, delta, force) at tq; side -1: the left limit at a phase boundary."""
        for i, (t0, t1, kind, dat) in enumerate(phases):
            last = i == len(phases) - 1
            if tq < t1 or (tq == t1 and (side < 0 or last)):
                tau = tq - t0
                if kind == "bumper":
                    x = speed / w * math.sin(w * tau)
                    return x, 0.0, 0.0, k_b * x
                if kind == "crush":
                    c = dat["v"] * tau - 0.5 * F_m / mass * tau * tau
                    return u_star + c, c, 0.0, F_m
                d = F_m / k_hard * (math.cos(dat["w"] * tau) - 1.0) + dat["v"] / dat["w"] * math.sin(dat["w"] * tau)
                return u_star + c_max + d, c_max, d, F_m + k_hard * d
        return state(t_stop, -1)

    ts = sorted(set(np.linspace(0.0, t_stop, n_out).tolist()) | {p[1] for p in phases[:-1]})
    rows, tt = [], []
    for tq in ts:
        if any(abs(tq - p[1]) < 1e-15 for p in phases[:-1]) and 0.0 < tq < t_stop:
            eps = 1e-9 * t_stop  # both sides of a jump in force: the left limit just before
            tt.append(tq - eps)
            rows.append(state(tq, -1))
        tt.append(tq)
        rows.append(state(tq, 1))
    arr = np.array(rows)
    F = arr[:, 3]
    i_max = int(np.argmax(F))
    E_b = 0.5 * k_b * min(u_end, stroke) ** 2 if k_b > 0 else 0.0
    E_hard = F_m * d_end + 0.5 * k_hard * d_end * d_end
    return {"t": np.array(tt), "x": arr[:, 0], "c": arr[:, 1], "F": F, "a": F / mass,
            "t_stop": t_stop, "x_max": u_end + c_end + d_end, "c_max": c_end, "delta_max": d_end,
            "F_max": float(F[i_max]), "a_max": float(F[i_max]) / mass, "bottomed": d_end > 0.0,
            "E_kinetic": E0, "E_bumper": E_b, "E_plastic": F_m * c_end, "E_hard": E_hard,
            "E_absorbed": E_b + F_m * c_end + E_hard,
            "permanent_crush": c_end}  # unloading: the bumper and the hard stop spring back, the folds stay


def wall_loads(design: Record, force_N: float) -> list:
    """The wall force (backward) shared by the front-most frame nodes."""
    nodes = {n["id"]: np.asarray(n["xyz_mm"], float) for n in design["nodes"]}
    used = {m[k] for m in design["members"] for k in ("node_a", "node_b")}
    xmax = max(nodes[n][0] for n in used)
    front = [n for n in sorted(used) if abs(nodes[n][0] - xmax) < 1e-6]
    return [(n, nodes[n], np.array([-force_N / len(front), 0.0, 0.0])) for n in front]


def crash_frame(design: Record, a_wall: float) -> dict:
    """K1 at a deceleration a_wall (m/s^2): wheels carry the weight, the wall stops the mass."""
    summ = mass_summary(design)
    m = summ["mass_kg"]
    rest = support_loads(design, summary=summ)
    ground = {sid: [("reaction", sl["point_mm"], sl["force_N"])] for sid, sl in rest["supports"].items()}
    F_wall = m * a_wall
    return analyze(design, ground_forces=ground, node_loads=wall_loads(design, F_wall),
                   inertia_N=np.array([F_wall, 0.0, 0.0]))


PLASTIC_NOTE = ("the bumper bottoms out before the crush zone folds; the crush stays within the usable length "
                "(crush_bottomed 0); no first peak in the tool or the formula")


class Crash(BuiltinTool):
    name = "crash"
    title = "壁への正面衝突 (バンパーのばね → レールの塑性のつぶれ → フレーム)"
    domain = "crash"
    base = "K1"
    tests = ["C1"]
    formulas = ["crash.plastic_crush", "crash.plastic_occupant_space_margin", "crash.rect_tube_crush",
                "crash.round_tube_crush", "crash.mean_deceleration"]
    load_case_params = (Param("speed", "km/h", "speed when the front meets the wall", required=True), LOADING)
    predicts = (
        Predicts("peak_deceleration", "crash.plastic_crush", "a_max", PLASTIC_NOTE),
        Predicts("max_crush", "crash.plastic_crush", "x_max", PLASTIC_NOTE),
        Predicts("stop_time", "crash.plastic_crush", "t_stop", PLASTIC_NOTE),
        Predicts("crash_energy", "crash.plastic_crush", "E"),
        Predicts("crash_energy", "crash.mean_deceleration", "E"),
        Predicts("mean_crush_force", "crash.rect_tube_crush", "F_m",
                 "n = the number of rails, all of one rect_tube section and material; "
                 "hiraku.solvers.k0_handcalc.library_crash.crush_zone(design)"),
        Predicts("occupant_space_margin", "crash.plastic_occupant_space_margin", "margin",
                 "s from hiraku.solvers.shelf.crash.occupant_space(design); " + PLASTIC_NOTE),
    )  # the result mean_deceleration is speed / stop time; crash.plastic_crush's a_mean is while the folds crush

    def known_answers(self) -> list[Record]:
        out = []
        sec = {"shape": "rect_tube", "dims_mm": {"b": 50.0, "h": 50.0, "t": 2.0}}
        got = library_crash.tube_mean_crush_force(sec, {"yield_MPa": 300.0})
        out.append(check_record("50x50x2 square tube, flow stress 300 MPa", "mean_crush_force",
                                9.56 * 300.0 * 2.0 * 2.0 ** (2.0 / 3.0) * 50.0 ** (1.0 / 3.0), got, "N", 1e-9,
                                library_crash.SOURCE_RECT,
                                case_ja="50×50×2 mm の角管 (流れ応力 300 MPa) が軸の向きにつぶれるときの平均の力",
                                source_ja="Wierzbicki と Abramowicz (1983) の式: P_m = 9.56 σ₀ t^(5/3) C^(1/3)、"
                                          "C = (b + h) / 2 (出典は確かめていない)"))
        r = plastic_impact(500.0, 10.0, 0.0, 0.0, 50e3, 10.0, 1e9)
        src = "rigid-plastic stop at a constant force: s = m v^2 / (2 F), t = m v / F; 500 kg, 10 m/s, 50 kN"
        plastic_ja = "500 kg が 10 m/s で、50 kN の一定の力でつぶれる部分に当たる"
        src_ja = "一定の力で止まる剛塑性の衝突: つぶれ s = m v² / (2 F)、止まるまでの時間 t = m v / F"
        out.append(check_record("500 kg at 10 m/s on a 50 kN crushing force", "max_crush", 0.5, r["x_max"], "m", 1e-9, src,
                                case_ja=plastic_ja, source_ja=src_ja))
        out.append(check_record("500 kg at 10 m/s on a 50 kN crushing force", "stop_time", 0.1, r["t_stop"], "s", 1e-9, src,
                                case_ja=plastic_ja, source_ja=src_ja))
        r = plastic_impact(600.0, 10.0, 100e3, 0.08, 60e3, 10.0, 1e9)
        out.append(check_record("bumper then crush zone, 600 kg at 10 m/s", "energy_absorbed",
                                r["E_kinetic"], r["E_absorbed"], "J", 1e-9,
                                "work-energy: bumper energy + F_m c take m v^2 / 2 at the stop",
                                case_ja="バンパーのあとにつぶれる部分、600 kg が 10 m/s で当たる",
                                source_ja="仕事とエネルギー: 止まるまでに、バンパーのエネルギーと F_m × つぶれ が m v² / 2 を受け取る"))
        r = plastic_impact(500.0, 10.0, 0.0, 0.0, 50e3, 0.3, 1e8)
        d = (-50e3 + math.sqrt(50e3 ** 2 + 1e8 * 500.0 * (100.0 - 2 * 100.0 * 0.3))) / 1e8
        out.append(check_record("crush zone bottoms out at 0.3 m, then a 100 kN/mm hard stop", "max_crush",
                                0.3 + d, r["x_max"], "m", 1e-9,
                                "work-energy: m v^2/2 = F c_max + F d + k d^2/2",
                                case_ja="つぶれる部分が 0.3 m でつぶれ切り、そのあと 100 kN/mm の固い当たりで止まる",
                                source_ja="仕事とエネルギー: m v²/2 = F × つぶれ切る長さ + F d + k d²/2"))
        lad = fixtures.ladder_design()
        a_g = 20.0
        res = crash_frame(lad, a_g * STANDARD_GRAVITY)
        wall_ja = f"左右対称のはしご形のフレームが壁で {a_g:g} g の減速で止められる"
        out.append(check_record("symmetric ladder stopped by a wall at 20 g", "hold_reaction",
                                0.0, res["residual_ratio"], "1", 1e-6,
                                "inertia relief: wall force and d'Alembert loads balance, the holding restraints carry nothing",
                                case_ja=wall_ja,
                                source_ja="慣性リリーフ: 壁の力とダランベールの慣性力がつり合い、押さえ (拘束) は何も受けない"))
        out.append(check_record("symmetric ladder stopped by a wall at 20 g", "deceleration",
                                a_g * STANDARD_GRAVITY, -float(res["inertia_accel"][0]), "m/s^2", 1e-9,
                                "rigid-body acceleration from the wall force: a = F / m",
                                case_ja=wall_ja, source_ja="壁の力から出す剛体の加速度: a = F / m"))
        return out

    def analyse(self, design: Record, load_case: Record, params: dict) -> Record:
        """Load case params: load_case_params."""
        design = on_board(design, load_case)
        v = param_value(load_case, "speed", "m/s")
        if v is None or v <= 0:
            raise LoadCaseError("the crash needs the load case parameter speed (positive)")
        k_b, stroke = bumper_from_design(design)
        zone = library_crash.crush_zone(design)
        F_m = zone["force_N"]
        summ = mass_summary(design)
        m = summ["mass_kg"]
        imp = plastic_impact(m, v, k_b, stroke, F_m, zone["usable_mm"] / 1000.0,
                             zone["hard_stiffness_N_per_mm"] * 1000.0)
        res = crash_frame(design, imp["a_max"])
        out = frame_outputs(res)
        crash_results = [
            result("peak_deceleration", imp["a_max"] / STANDARD_GRAVITY, "g"),
            result("peak_force", imp["F_max"] / 1000.0, "kN"),
            result("max_crush", imp["x_max"] * 1000.0, "mm"),
            result("crash_energy", imp["E_kinetic"], "J"),
            result("stop_time", imp["t_stop"], "s"),
            result("mean_deceleration", v / imp["t_stop"] / STANDARD_GRAVITY, "g"),
            result("mean_crush_force", F_m, "N"),
            result("permanent_crush", imp["permanent_crush"] * 1000.0, "mm"),
            result("crush_bottomed", 1.0 if imp["bottomed"] else 0.0, "1"),
        ]
        margin = occupant_space(design)
        if margin is not None:
            crash_results.append(result("occupant_space_margin", margin - imp["x_max"] * 1000.0, "mm"))
        for p in design.get("parts", []):
            if p["kind"] == "battery":
                crash_results.append(result("battery_inertia_force", float(p["mass_kg"]) * imp["a_max"], "N", p["id"]))
        out["results"] = crash_results + out["results"]
        for n, p, f in wall_loads(design, m * imp["a_max"]):
            out["vectors"].append({"kind": "load", "at": n, "origin_mm": [float(x) for x in p], "vector_N": [float(x) for x in f]})
        t = [float(x) for x in imp["t"]]
        out["series"] = [
            {"channel": "deceleration", "unit": "g", "t_s": t, "values": [float(x) for x in imp["a"] / STANDARD_GRAVITY]},
            {"channel": "crush", "unit": "mm", "t_s": t, "values": [float(x) for x in imp["x"] * 1000.0]},
            {"channel": "wall_force", "unit": "kN", "t_s": t, "values": [float(x) for x in imp["F"] / 1000.0]},
        ]
        E0 = imp["E_kinetic"]
        effects = [
            {"name": "energy taken by the bumper", "value": imp["E_bumper"], "unit": "J", "share": imp["E_bumper"] / E0},
            {"name": "energy taken by the crush zone", "value": imp["E_plastic"], "unit": "J", "share": imp["E_plastic"] / E0},
        ]
        if imp["bottomed"]:
            effects.append({"name": "energy taken by the hard stop (the crush zone bottomed out)",
                            "value": imp["E_hard"], "unit": "J", "share": imp["E_hard"] / E0})
        for r in zone["rails"]:
            effects.append({"name": f"crushing rail {', '.join(r['members'])}: mean crushing force (weakest tube "
                                    f"{r['weakest']})", "value": r["force_N"], "unit": "N", "share": r["force_N"] / F_m})
        out["effects"] = effects + out["effects"]
        out["assumptions"] = list(out.get("assumptions", [])) + [
            "the body is rigid; the bumper is a linear spring up to bumper_stroke; the crush zone folds "
            "plastically at its mean crushing force and does not spring back (zero force when unloading, the crush stays)",
            f"crush zone: the front frame's rails from the front of the frame (x = {zone['x_front_mm']:g} mm) back to "
            f"the first cross member joining them behind the front wheels' mounts ({zone['end_member']}, x = "
            f"{zone['x_end_mm']:g} mm; mounts at x = {zone['x_mounts_mm']:g} mm, {zone['mounts_how']}); crushing "
            f"members: {', '.join(zone['members'])}",
            "mean crushing force per rail: its weakest tube; the rails crush side by side, so the forces add. "
            "Square/rectangular tubes: " + library_crash.SOURCE_RECT + "; round tubes: " + library_crash.SOURCE_ROUND
            + "; flow stress (yield + tensile)/2, or the yield stress without a tensile strength (not checked)",
            "no first peak: the force is the mean crushing force from the first fold on",
            f"usable crush length: {library_crash.USABLE_FRACTION:.0%} of the crush zone's length "
            f"({zone['usable_mm']:.0f} of {zone['length_mm']:.0f} mm), the rest is the folds' solid length "
            "(HIRAKU's assumption, not checked)",
            f"past the usable length the crush zone bottoms out: a hard stop, F = F_m + k delta, k = the rails' EA over "
            f"the solid length ({zone['hard_stiffness_N_per_mm']:.0f} N/mm), elastic; the bumper's remaining stroke "
            "is not counted in it" + (" — it bottomed out in this run" if imp["bottomed"] else ""),
            f"the design variable {NOT_READ} is not used (the crush force comes from the rails' tubes)",
            "rigid wall; the motion up to the standstill, the rebound is not followed",
            "comparison on the demo design: " + COMPARISON,
        ]
        out["scene"] = [scene.wall(design)]
        return out


def occupant_space(design: Record) -> float | None:
    """Room in front of the occupants: front-most point of the vehicle minus the occupants' front face (mm)."""
    occ = [p for p in design.get("parts", []) if p["kind"] == "occupant"]
    if not occ:
        return None
    xs = [n["xyz_mm"][0] for n in design["nodes"]]
    xs += [p["box"]["center_mm"][0] + p["box"]["size_mm"][0] / 2.0 for p in design.get("parts", [])]
    occ_front = max(p["box"]["center_mm"][0] + p["box"]["size_mm"][0] / 2.0 for p in occ)
    return max(xs) - occ_front

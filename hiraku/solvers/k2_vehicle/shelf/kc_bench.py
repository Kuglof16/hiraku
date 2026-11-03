"""K&C bench: a corner's kinematics against the wheel travel and its compliance against the forces."""

from __future__ import annotations

import numpy as np

from .. import kinematics
from ..checks import _check
from ..model import build_vehicle
from .base import K2Tool, P, Pair, _res


def _q(v: float, unit: str) -> dict:
    return {"value": float(v), "unit": unit}


def k0_inputs(table: dict) -> dict:
    """The inputs of K0's kc.roll_center_height and kc.aligning_steer (by formula id) from a K&C
    table: its hardpoints (left wheel) and its contact point at the design travel; k_t when the
    table has its bushings."""
    hp = kinematics.Hardpoints.from_json(table["hardpoints_mm"])
    li, ui = 0.5 * (hp.lca_front + hp.lca_rear), 0.5 * (hp.uca_front + hp.uca_rear)
    r0 = min(table["rows"], key=lambda r: abs(r["travel_mm"]))
    rc = {"y_li": _q(li[1] * 1000.0, "mm"), "z_li": _q(li[2] * 1000.0, "mm"),
          "y_lo": _q(hp.lca_outer[1] * 1000.0, "mm"), "z_lo": _q(hp.lca_outer[2] * 1000.0, "mm"),
          "y_ui": _q(ui[1] * 1000.0, "mm"), "z_ui": _q(ui[2] * 1000.0, "mm"),
          "y_uo": _q(hp.uca_outer[1] * 1000.0, "mm"), "z_uo": _q(hp.uca_outer[2] * 1000.0, "mm"),
          "y_c": _q(r0["contact_mm"][1], "mm"), "z_c": _q(r0["contact_mm"][2], "mm")}
    axis = (hp.uca_outer - hp.lca_outer) / np.linalg.norm(hp.uca_outer - hp.lca_outer)
    nt = (hp.tie_outer - hp.tie_inner) / np.linalg.norm(hp.tie_outer - hp.tie_inner)
    arm = abs(float(np.cross(hp.tie_outer - hp.lca_outer, nt) @ axis))
    steer = {"k_z": _q(axis[2], "1"), "a": _q(arm * 1000.0, "mm")}
    if "bushings" in table:
        steer["k_t"] = _q(table["bushings"]["tie_rod_N_per_mm"], "N/mm")
    return {"kc.roll_center_height": rc, "kc.aligning_steer": steer}


def _mount_checks() -> list[dict]:
    """Mount forces of kinematics.mount_forces on a layout with a closed-form answer: arms level and
    parallel in front view (lower arm 400 mm, its damper 300 mm out, both vertical), the kingpin
    upright with the ball joints 300 mm apart, the contact 200 mm below the lower one."""
    a = np.array
    hp = kinematics.Hardpoints(a([0.1, 0.2, 0.2]), a([-0.1, 0.2, 0.2]), a([0.0, 0.6, 0.2]),
                               a([0.1, 0.3, 0.5]), a([-0.1, 0.3, 0.5]), a([0.0, 0.6, 0.5]),
                               a([-0.12, 0.25, 0.35]), a([-0.12, 0.6, 0.35]), a([0.0, 0.65, 0.3]),
                               a([0.0, 1.0, 0.0]), 0.3, a([0.0, 0.5, 0.2]), a([0.0, 0.5, 0.6]))
    sol = dict(kinematics.DoubleWishboneBench(hp).solve(0.0), contact=a([0.0, 0.6, 0.0]))
    up = kinematics.mount_forces(hp, sol, a([0.0, 0.0, 2000.0]))
    side = kinematics.mount_forces(hp, sol, a([0.0, 800.0, 0.0]))
    gen = kinematics.generic_sla(a([0.0, 0.55, 0.3]), 0.3)
    f = a([300.0, -900.0, 2500.0])
    r = kinematics.mount_forces(gen, kinematics.solve_at_travel(gen, 0.03), f)
    total = sum(r[m] for m in kinematics.MOUNTS)
    return [
        _check("vertical 2 kN at the contact, level parallel arms: the damper carries it by the lever", "damper_force",
               -2000.0 * 0.4 / 0.3, up["damper_tension"], "N", 1e-9,
               "moment about the lower arm's axis: F_z L = D a (L = 400 mm to the ball joint, a = 300 mm to the damper)"),
        _check("lateral 800 N at the contact 200 mm below the lower ball joint: the upper arm's pull", "upper_arm_force",
               800.0 * 0.2 / 0.3, float(side["ball_joint_upper"][1]), "N", 1e-9,
               "moment about the lower ball joint: F_y c = F_u h (c = 200 mm, h = 300 mm between the ball joints)"),
        _check("generic layout at 30 mm bump, tyre force (300, -900, 2500) N: the forces on the body add up to it",
               "mount_force_sum_error", 0.0, float(np.linalg.norm(total - f)), "N", 1e-6,
               "balance of the whole corner: the body takes what the road gives"),
    ]


def _series(channel: str, unit: str, x_name: str, x_unit: str, xs, ys) -> dict:
    return {"channel": channel, "unit": unit, "x": {"quantity": x_name, "unit": x_unit, "values": [float(v) for v in xs]},
            "values": [float(v) for v in ys]}


class KcBenchTool(K2Tool):
    """K&C bench of one corner on a K&C table (suspension.type kc_table).

    Kinematics: camber, toe, roll centre height and the wheel centre's lateral motion against
    the wheel travel. Compliance (at the design travel, the wheel pad holding the travel): the
    toe and camber change from a force at the contact point along the body's y (lateral) and
    x (longitudinal) and from a moment about z (aligning). Values are for the corner as a LEFT
    wheel (a right wheel is its mirror image): camber + = top outward, toe + = toe-in, Fy + =
    toward +y (outward for a left wheel), Mz + = about +z.
    Results: roll_center_height (mm, design travel), camber_at_bump / camber_at_rebound and
    toe_at_bump / toe_at_rebound (deg, change at the ends of the table), and the compliance as
    the change at a round load: toe_at_1kN_lateral, camber_at_1kN_lateral,
    toe_at_1kN_longitudinal, camber_at_1kN_longitudinal (deg per 1 kN), toe_at_aligning_moment
    (deg at the load case's aligning_moment).
    series: against x = travel (mm): camber, toe, roll_center_height, track_change (the wheel
    centre's y, mm); against x = lateral_force (kN): toe_lateral, camber_lateral; against
    x = longitudinal_force (kN): toe_longitudinal, camber_longitudinal; against
    x = aligning_moment (N*m): toe_aligning.
    """

    name = "kc_bench"
    title = "K&C の台 (ストロークと力でキャンバー・トー)"
    domain = "handling"
    tests: list[str] = []
    params = (P("support", None, None, "the support id on the bench (default: the first support on a K&C table)"),
              P("lateral_force", "kN", 3.0, "the lateral force sweep goes from - to + this"),
              P("longitudinal_force", "kN", 3.0, "the longitudinal force sweep goes from - to + this"),
              P("aligning_moment", "N*m", 100.0, "the aligning moment sweep goes from - to + this"))
    pairs = (Pair("roll_center_height", "kc.roll_center_height", "h_rc",
                  note="inputs: the table's hardpoints and its contact point at the design travel "
                       "(hiraku.solvers.k2_vehicle.shelf.kc_bench.k0_inputs); K2 takes the roll centre from the "
                       "contact point's motion over the table"),
             Pair("toe_at_aligning_moment", "kc.aligning_steer", "toe", (("M", "aligning_moment"),),
                  "k_z, a and k_t from k0_inputs; K0 lets only the tie rod give way, K2 also the arm bushings, "
                  "so K2 turns more"))

    def own_checks(self) -> list[dict]:
        out = []
        hp = kinematics.generic_sla(np.array([0.0, 0.6, 0.3]), 0.3)
        sol = kinematics.DoubleWishboneBench(hp).solve(0.0)
        # 1. the roll centre from the contact point's motion = the line through the contact point and the
        # front-view instant centre of the arms, where it crosses the centre line
        table = kinematics.build_table(hp, 0.03, 13)
        rc = [r for r in table["rows"] if abs(r["travel_mm"]) < 1e-9][0]["roll_center_height_mm"]
        ic = kinematics.front_view_ic(hp)
        cy, cz = float(sol["contact"][1]), float(sol["contact"][2])
        expected = (cz + (0.0 - cy) * (ic[1] - cz) / (ic[0] - cy)) * 1000.0
        out.append(_check("generic short-long-arm corner at design travel: roll centre height", "roll_center_height",
                          expected, rc, "mm", 1e-3, "front-view instant centre: the line from the contact point "
                          "through the arms' instant centre crosses the centre line at the roll centre"))
        # 2. aligning-moment steer with stiff bushings: only the tie rod gives way, about the kingpin axis
        k_t = 10e6
        stiff = kinematics.Bushings(1e13, 1e13, k_t)
        c = kinematics.compliance(hp, sol, stiff)
        tro = sol["lbj"] + sol["rotation"] @ (hp.tie_outer - hp.lca_outer)
        axis = (sol["ubj"] - sol["lbj"]) / np.linalg.norm(sol["ubj"] - sol["lbj"])
        nt = (tro - hp.tie_inner) / np.linalg.norm(tro - hp.tie_inner)
        arm = float(np.cross(tro - sol["lbj"], nt) @ axis)
        kz = float(axis[2])
        expected = -kz * kz / (k_t * arm * arm)  # toe-in +: a +z moment turns a left wheel's nose out
        out.append(_check("aligning moment on the generic corner, arm bushings stiff, tie rod 10 kN/mm: toe per N m",
                          "toe_per_aligning_moment", expected, c["toe_per_mz"], "rad", 2e-3,
                          "rotation about the kingpin axis k: theta = M (k . z) / (k_t a^2), a = the tie rod's arm "
                          "about k; toe change = theta (k . z)"))
        # 3. linear springs: twice the stiffness, half the compliance
        b1, b2 = kinematics.Bushings(3e6, 0.5e6, 10e6), kinematics.Bushings(6e6, 1e6, 20e6)
        c1, c2 = kinematics.compliance(hp, sol, b1), kinematics.compliance(hp, sol, b2)
        worst = max(abs(c2[k] / c1[k] - 0.5) for k in kinematics.COMPLIANCE_KEYS if abs(c1[k]) > 0.0)
        out.append(_check("all joint stiffnesses doubled: compliance halves", "compliance_ratio_error", 0.0, worst, "1",
                          2e-3, "linear springs; the stiff ball joints and wheel pad stay the same, so a little off"))
        out += _mount_checks()
        return out

    def _run(self, design: dict, lp: dict) -> dict:
        veh = build_vehicle(design)
        on_table = [c for c in veh.corners if c.table is not None]
        if not on_table:
            return {"error": "no support is on a K&C table (suspension.type kc_table, e.g. kc_table "
                             "'builtin:double_wishbone'); the bench needs one", "assumptions": veh.assumptions}
        want = self.text(lp, "support")
        if want is not None:
            pick = [c for c in on_table if c.id == want]
            if not pick:
                return {"error": f"support {want} is not on a K&C table; on a table: {', '.join(c.id for c in on_table)}"}
            corner = pick[0]
        else:
            corner = on_table[0]
        rows = corner.table["rows"]
        travel = [r["travel_mm"] for r in rows]
        zero = min(range(len(rows)), key=lambda k: abs(rows[k]["travel_mm"]))
        r0 = rows[zero]
        series = [_series("camber", "deg", "travel", "mm", travel, [r["camber_deg"] - r0["camber_deg"] for r in rows]),
                  _series("toe", "deg", "travel", "mm", travel, [r["toe_deg"] - r0["toe_deg"] for r in rows]),
                  _series("roll_center_height", "mm", "travel", "mm", travel, [r["roll_center_height_mm"] for r in rows]),
                  _series("track_change", "mm", "travel", "mm", travel,
                          [r["wheel_center_mm"][1] - r0["wheel_center_mm"][1] for r in rows])]
        res = [_res("roll_center_height", r0["roll_center_height_mm"], "mm", corner.id),
               _res("camber_at_bump", rows[-1]["camber_deg"] - r0["camber_deg"], "deg", f"{travel[-1]:.0f} mm"),
               _res("camber_at_rebound", rows[0]["camber_deg"] - r0["camber_deg"], "deg", f"{travel[0]:.0f} mm"),
               _res("toe_at_bump", rows[-1]["toe_deg"] - r0["toe_deg"], "deg", f"{travel[-1]:.0f} mm"),
               _res("toe_at_rebound", rows[0]["toe_deg"] - r0["toe_deg"], "deg", f"{travel[0]:.0f} mm")]
        cols = {k: f"{k}_{kinematics.COMPLIANCE_UNIT[k]}" for k in kinematics.COMPLIANCE_KEYS}
        notes = list(veh.assumptions)
        if all(c in r0 for c in cols.values()):
            per = {k: r0[c] for k, c in cols.items()}  # deg per kN, deg per kN m
            moment = self.q(lp, "aligning_moment", "N*m")
            res += [_res("toe_at_1kN_lateral", per["toe_per_fy"], "deg"),
                    _res("camber_at_1kN_lateral", per["camber_per_fy"], "deg"),
                    _res("toe_at_1kN_longitudinal", per["toe_per_fx"], "deg"),
                    _res("camber_at_1kN_longitudinal", per["camber_per_fx"], "deg"),
                    _res("toe_at_aligning_moment", per["toe_per_mz"] * moment / 1000.0, "deg", f"{moment:g} N*m")]
            for name, unit, top, keys in (("lateral_force", "kN", self.q(lp, "lateral_force", "N") / 1000.0, ("fy",)),
                                          ("longitudinal_force", "kN", self.q(lp, "longitudinal_force", "N") / 1000.0,
                                           ("fx",)),
                                          ("aligning_moment", "N*m", self.q(lp, "aligning_moment", "N*m"), ("mz",))):
                xs = list(np.linspace(-top, top, 13))
                scale = 1e-3 if unit == "N*m" else 1.0  # per kN m from a moment in N m
                tag = {"fy": "lateral", "fx": "longitudinal", "mz": "aligning"}[keys[0]]
                series.append(_series(f"toe_{tag}", "deg", name, unit, xs, [per[f"toe_per_{keys[0]}"] * x * scale for x in xs]))
                if keys[0] != "mz":
                    series.append(_series(f"camber_{tag}", "deg", name, unit, xs,
                                          [per[f"camber_per_{keys[0]}"] * x * scale for x in xs]))
        else:
            notes.append(f"the K&C table of {corner.id} has no compliance columns: kinematics only")
        notes.append(f"bench of support {corner.id}, as a left wheel (a right wheel is its mirror image)")
        return {"results": res, "series": series, "assumptions": notes}

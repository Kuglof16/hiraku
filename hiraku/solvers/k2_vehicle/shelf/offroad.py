"""Rough side slope (S5)."""

from __future__ import annotations

import math

import numpy as np

from ..checks import _check
from ..model import box_design, build_vehicle
from ..scenario import new_sim, side_gravity
from ..units import G0
from .base import MU, K2Tool, P, Pair, _length, _min_load, _ramp_gravity, _res, _side_ids, _slope
from .lane_change import add_mounts, path_run


class OffroadRollTool(K2Tool):
    """Rough side slope (S5): an ISO 8608 road on a side slope, driven straight at a held speed.

    The slope is made first by turning gravity over slope_ramp_time with the brakes held
    (recorded, channel slope_angle); a vehicle that tips over then or while driving stops
    the run (the body has no contact with the ground in K2).
    Results: min_wheel_load (N, where), wheel_lift (1), two_wheel_lift_time (s, both
    uphill wheels off at once), min_uphill_load (N, the uphill wheels' sum),
    mean_uphill_load (N, its mean over the run), max_roll (deg, against the slope),
    rms_vertical_accel (m/s^2, body axis), tipped (1), and tip_slope_angle (deg, the slope
    at which the uphill wheels all left the ground while it was made, when they did).
    With K&C corners the drive also gives the force the body receives at each suspension mount
    (lane_change.add_mounts: mount_* series with gravity_x/y/z, mount_force_peak, joint vectors).
    hot_spots: tip_slope_angle on each side up where the wheels lift while the slope is made (the
    load case's slope_up judged; the other side by the same turn, brakes held, without the drive).
    """

    name = "offroad_roll"
    title = "凸凹の横斜面 (K2 で ISO 8608 の路面)"
    domain = "handling"
    tests = ["S5"]
    # side_slope 15 deg and speed 20 km/h: the defaults when a load case has none (the catalog's load cases give
    # them; S5's 20 km/h is cited in catalog/builders/k2_tests.py); test values
    params = (P("road_class", None, "C", "ISO 8608 road class (values not checked against the ISO text)",
                choices=tuple("ABCDEFGH")),
              P("side_slope", "deg", 15.0, "side slope"),
              P("slope_up", None, "left", "the side whose wheels are uphill", choices=("left", "right")),
              P("speed", "km/h", 20.0, "held speed"),
              P("length", "m", 100.0, "distance driven"),
              P("seed", "1", 1.0, "random phases of the road (an integer)"),
              P("slope_ramp_time", "s", 1.0, "time to turn from level to the side slope, brakes held "
                                             "(slow, e.g. 8 s, to find tip_slope_angle quasi-statically)"),
              MU)
    pairs = (Pair("tip_slope_angle", "tilt.spring_roll_angle", "theta",
                  note="a side slope at rest is a tilt table; K2 turns the slope in slope_ramp_time, so a fast "
                       "turn tips it a little early"),
             Pair("tip_slope_angle", "tilt.rigid_angle", "theta", note="rigid: above K2 by the body roll on the springs"),
             Pair("mean_uphill_load", "tilt.side_slope_uphill_load", "W_up", (("beta", "side_slope"),),
                  "rigid balance on the slope; K2's body also rolls downhill on its springs, and min_uphill_load "
                  "has no hand calculation (the road roughness)"),
             Pair("mean_uphill_load", "lane_change.inner_wheel_load", "W_inner",
                  note="a_y = tan(side_slope) in g, the slope taken as a lateral acceleration: that is high by "
                       "1 / cos(side_slope) (3.5 % at 15 deg)"))

    def own_checks(self) -> list[dict]:
        from scipy.signal import welch

        from ..road import ISO8608_GD_N0, RandomRoad, iso8608_psd
        road = RandomRoad("C", 0.6, seed=7, fade_start=-1.0, fade_end=-0.5)
        dx = 0.1
        x = np.arange(0.0, 3000.0, dx)
        z, _ = road.track(x, 0)
        f, pxx = welch(z, fs=1.0 / dx, nperseg=4096)
        band = (f >= 0.05) & (f <= 2.0)
        ratio = float(np.mean(pxx[band] / iso8608_psd(f[band], "C")))
        out = [_check("generated ISO 8608 class C track, Welch PSD / G_d(n0)(n/n0)^-2 over 0.05-2 cycle/m", "psd_ratio",
                      1.0, ratio, "1", 0.10, "G_d(n) = G_d(n0) (n / n0)^-w, n0 = 0.1 cycle/m, w = 2, "
                      f"G_d(n0) = {ISO8608_GD_N0['C']:g} m^3 (class C, not checked against the ISO text)")]
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, susp="rigid",
                       variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                  "tire_mu": {"value": 2.0, "unit": "1"}}, name="side slope check, rigid")
        veh = build_vehicle(d)
        sim = new_sim(veh, settle_s=1.0)
        beta = math.radians(15.0)
        _ramp_gravity(sim, side_gravity, beta)
        up = sum(sim.out["ntot"][i] for i in _side_ids(sim, True))
        w = veh.m_total * G0
        h = float(veh.cg_total[2])
        expected = w * (math.cos(beta) * 0.6 - math.sin(beta) * h) / 1.2
        out.append(_check("rigid vehicle at rest on a 15 deg side slope: uphill wheel loads", "uphill_load", expected, up,
                          "N", 5e-3, "moment about the downhill contact line: W (cos b t/2 - sin b h) / t"))
        return out

    def _run(self, design: dict, lp: dict) -> dict:
        from ..road import ISO8608_GD_N0, RandomRoad
        veh = self.vehicle(design, lp)
        if "road_class" in lp:
            lp = dict(lp, road_class=str(lp["road_class"]).upper())
        cls = self.text(lp, "road_class")
        notes = list(veh.assumptions) + [f"ISO 8608 class {cls}: G_d(n0) = {ISO8608_GD_N0[cls]:g} m^3, w = 2 "
                                         "(not checked against the ISO text); left and right tracks uncorrelated"]
        beta = self.q(lp, "side_slope", "rad")
        left_up = self.text(lp, "slope_up") == "left"
        speed = self.q(lp, "speed", "m/s")
        length = self.q(lp, "length", "m")
        seed = int(lp["seed"]) if isinstance(lp.get("seed"), str) else int(self.q(lp, "seed", "1"))
        ys = [abs(float(c.pos[1])) for c in veh.corners]
        road = RandomRoad(cls, max(ys) if ys else 0.6, seed=seed)
        g = (side_gravity, beta if left_up else -beta)
        st = path_run(veh, speed, lambda x: 0.0, length, dt=self.dt, rec_hz=self.record_hz, road=road, gravity=g,
                      ramp_s=self.q(lp, "slope_ramp_time", "s"))
        cid, mn = _min_load(st["min_load"])
        az = np.array(st["az"])
        res = [_res("min_wheel_load", mn, "N", cid), _res("wheel_lift", 1.0 if st["any_lift"] else 0.0, "1"),
               _res("two_wheel_lift_time", st["two_lift_time"], "s"),
               _res("min_uphill_load", st["min_side"]["left" if left_up else "right"], "N"),
               _res("mean_uphill_load", st["mean_side"]["left" if left_up else "right"], "N"),
               _res("max_roll", math.degrees(st["max_roll"]), "deg"),
               _res("rms_vertical_accel", float(np.sqrt(np.mean((az - az.mean()) ** 2))) if az.size else 0.0, "m/s^2"),
               _res("tipped", 1.0 if st["tipped"] else 0.0, "1")]
        if st["tip_angle"] is not None:
            res.append(_res("tip_slope_angle", math.degrees(st["tip_angle"]), "deg"))
        # the slope the other way up as well (the load case names one side, from the CG's offset): only
        # its making, brakes held, for the tip angle; a place the hand calculation does not look at
        other = new_sim(veh, road=road, dt=self.dt, settle_s=1.0)
        tip_other = _ramp_gravity(other, side_gravity, -beta if left_up else beta, self.q(lp, "slope_ramp_time", "s"),
                                  up=_side_ids(other, not left_up))
        side_name = {True: "left side up", False: "right side up"}
        places = [{"where": side_name[up_], "quantity": "tip_slope_angle", "value": math.degrees(a), "unit": "deg",
                   "judged": j} for up_, a, j in ((left_up, st["tip_angle"], True), (not left_up, tip_other, False))
                  if a is not None]
        if st["tipped"]:
            notes.append("the vehicle tipped over; the run stops there (K2 has no body-ground contact)")
        rec = st["recorder"]
        # the road under the two wheel tracks, every 0.1 m from behind the start to past the end
        x = np.arange(-_length(veh) - 1.0, length + _length(veh) + 1.0, 0.1)
        zl, _ = road.track(x, 0)
        zr, _ = road.track(x, 1)
        ht = road.half_track * 1000.0
        profile = {"x_mm": [round(float(v) * 1000.0, 1) for v in x],
                   "z_mm": {"left": [round(float(v) * 1000.0, 2) for v in zl],
                            "right": [round(float(v) * 1000.0, 2) for v in zr]}}
        side = "左" if left_up else "右"
        scene = [{"kind": "road_profile", "profile": profile,
                  "label": f"ISO 8608 クラス {cls} (seed {seed})。left は y = +{ht:.0f} mm、right は y = -{ht:.0f} mm の"
                           "通り道の高さ。その間は直線、外は近い方の通り道と同じ"},
                 dict(_slope(f"横 {math.degrees(beta):g}° の斜面 ({side}が上)。slope_angle の通りに水平から傾く", veh,
                             float(x[0]), float(x[-1]), roll=beta if left_up else -beta), angle_channel="slope_angle")]
        out = {"results": res, "series": rec.series(), "vectors": rec.vectors(-1), "scene": scene,
               "assumptions": notes, "places": places}
        add_mounts(veh, st, out)  # the drive over the rough slope (the slope's making is not in it)
        return out

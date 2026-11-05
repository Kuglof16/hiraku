"""Crosswind (F1)."""

from __future__ import annotations

import math

from ..checks import CHECK_WIND, _check
from ..drive import RHO_AIR, Aero, SpeedController, controls, start_rolling
from ..model import box_design, build_vehicle
from ..record import Recorder
from ..scenario import hold, new_sim
from ..units import G0
from .base import (CD, FRONT_AREA, MU, RHO, SIDE_AREA, SIDE_CP_HEIGHT, TIPPED_ROLL, K2Tool, P, Pair, _res, _side_ids,
                   _wind_world)


def wind_ramp(veh, aero: Aero, from_left: bool = True, dt: float = 1e-3, fast: float = 5.0, slow: float = 1.0,
              slow_below: float = 0.4, v_max: float = 200.0, stop_on_slide: bool = False) -> dict:
    """Vehicle held at rest; the side wind grows until the upwind wheels carry no load.

    v_crit is the first step at which the upwind wheels have lost all load (a wheel's load
    is never negative, so there is no crossing to interpolate); it lies at most one step
    (slow * dt) past the true crossing.
    """
    sim = new_sim(veh, dt=dt, settle_s=1.5)
    ctl = hold(sim)
    up = _side_ids(sim, from_left)
    n0 = sum(sim.out["ntot"][i] for i in up)
    v, prev, crit, slide = 0.0, (0.0, n0), None, None
    while v < v_max:
        v += (fast if prev[1] > slow_below * n0 else slow) * dt
        sim.step(ctl, aero.forces(sim, _wind_world(v, from_left)))
        nu = 0.0
        for i in up:
            nu += sim.out["ntot"][i]
        if slide is None and any(sim.out["sliding"][i] for i in range(sim.n) if i not in up):
            slide = v
            if stop_on_slide:
                break
        if nu <= 0.0:
            crit = v
            break
        prev = (v, nu)
    return {"v_crit": crit, "slide_speed": slide}


class CrosswindTool(K2Tool):
    """Crosswind (F1): side force 1/2 rho C_y A v^2 at the side centre of pressure.

    Results: critical_wind_speed (m/s: at rest, the tyres held from sliding (mu 2), the
    upwind wheels lift), slide_wind_speed (m/s: at rest with the road mu, the vehicle starts
    to slide sideways), slides_first (1), side_force (N at wind_speed),
    min_upwind_wheel_load (N, the upwind wheels together, a gust of wind_speed reaching the
    vehicle at `speed` with the steering held straight), lateral_drift (m, drift_time after
    the gust). hot_spots: critical_wind_speed with the wind from each side (the load case's
    wind_from judged, the other side found here) and the wheels' friction use and unloading.
    """

    name = "crosswind"
    title = "横風 (K2 に横風の力を入れる)"
    domain = "aero"
    tests = ["F1"]
    # wind_speed 30 m/s: the default when a load case has none (the catalog's load cases give it from the
    # requirements); a test value. The gust rises in 0.2 s (in _run): HIRAKU's choice, an assumption
    params = (P("wind_speed", "m/s", 30.0, "the gust (relative to the ground, across the vehicle)"),
              P("speed", "km/h", 0.0, "vehicle speed when the gust arrives"),
              P("cy", "1", 1.0, "side force coefficient (1.0 assumed when neither the load case nor the "
                                "design gives it)", var=("side_force_coefficient",), assumed=True),
              CD, RHO, SIDE_AREA, SIDE_CP_HEIGHT, FRONT_AREA,
              P("wind_from", None, "left", "side the wind comes from", choices=("left", "right")),
              P("drift_time", "s", 2.0, "time after the gust for lateral_drift"),
              MU)
    pairs = (Pair("critical_wind_speed", "crosswind.critical_wind_speed", "v_crit",
                  (("C_y", "cy"), ("A", "side_area"), ("rho", "rho"), ("h_cp", "side_cp_height")),
                  "K2 at rest with the tyres held (mu 2); the body also rolls on its springs. C_y, A, h_cp, rho: "
                  "the load case, else the design variables named in describe(), else K2's defaults (A and "
                  "h_cp from the side projection of the part boxes)"),
             Pair("side_force", "crosswind.side_force", "F",
                  (("C_y", "cy"), ("A", "side_area"), ("v", "wind_speed"), ("rho", "rho"))),
             Pair("min_upwind_wheel_load", "crosswind.windward_wheel_load", "W_windward", (("h_cp", "side_cp_height"),),
                  "F from crosswind.side_force; K2 is the minimum after a gust rising in 0.2 s, so the "
                  "roll overshoot can take it below the steady value"))

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, track=1.2, susp="rigid",
                       variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                  "tire_mu": {"value": 2.0, "unit": "1"}}, name="crosswind check, rigid")
        veh = build_vehicle(d)
        aero = Aero.of(veh, RHO_AIR, 0.0, 1.0)
        got = wind_ramp(veh, aero, True, **CHECK_WIND)["v_crit"]
        got_r = wind_ramp(veh, aero, False, **CHECK_WIND)["v_crit"]
        w = veh.m_total * G0
        expected = math.sqrt(w * 1.2 / (RHO_AIR * 1.0 * veh.side_area * veh.side_cp[2]))
        src = "moment about the downwind contact line: 1/2 rho C_y A v^2 h_cp = W t / 2"
        return [_check(f"rigid vehicle at rest, side area {veh.side_area:.3f} m^2 at {veh.side_cp[2]:.3f} m",
                       "critical_wind_speed", expected, got, "m/s", 5e-3, src),
                _check("wind from the left - wind from the right (bit for bit)", "critical_wind_speed_difference", 0.0,
                       None if got is None or got_r is None else got - got_r, "m/s", 0.0, "symmetry",
                       passed=got is not None and got == got_r)]

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        aero = self.aero(veh, lp)
        notes = list(veh.assumptions)
        from_left = self.text(lp, "wind_from") == "left"
        gust = self.q(lp, "wind_speed", "m/s")
        speed = self.q(lp, "speed", "m/s")
        drift_t = self.q(lp, "drift_time", "s")
        held = build_vehicle(design, {"mu": 2.0})
        ramp = wind_ramp(held, self.aero(held, lp), from_left, dt=self.dt)
        side_name = "wind from the " + ("left" if from_left else "right")
        res = []
        if ramp["v_crit"] is not None:
            res.append(_res("critical_wind_speed", ramp["v_crit"], "m/s", side_name))
        # the other side too (the load case names one, from the CG's offset): a place the hand
        # calculation does not look at, for the hot_spots (CAE の役目)
        other_name = "wind from the " + ("right" if from_left else "left")
        other = wind_ramp(held, self.aero(held, lp), not from_left, dt=self.dt)
        places = [{"where": n, "quantity": "critical_wind_speed", "value": r_["v_crit"], "unit": "m/s", "judged": j}
                  for n, r_, j in ((side_name, ramp, True), (other_name, other, False)) if r_["v_crit"] is not None]
        slide = wind_ramp(veh, aero, from_left, dt=self.dt, stop_on_slide=True)
        slide_v = slide["slide_speed"]
        if slide_v is not None:
            res.append(_res("slide_wind_speed", slide_v, "m/s", side_name))
        res.append(_res("slides_first", 1.0 if slide_v is not None and (ramp["v_crit"] is None or slide_v < ramp["v_crit"]) else 0.0, "1"))
        res.append(_res("side_force", 0.5 * aero.rho * aero.cy * aero.side_area * gust * gust, "N"))

        sim = new_sim(veh, dt=self.dt, settle_s=1.5)
        rec = Recorder(sim, self.record_hz)
        up = _side_ids(sim, from_left)
        ctrl = SpeedController(sim) if speed > 0.0 else None
        if speed > 0.0:
            start_rolling(sim, speed)
        t_g, t_end = 1.0, 1.0 + drift_t + 0.5
        y_g = y_d = None
        min_up = math.inf
        for k in range(int(round(t_end / sim.dt))):
            t = k * sim.dt
            vw = gust * min(1.0, max(0.0, (t - t_g) / 0.2))
            if ctrl is not None:
                drive, brake = ctrl.torques(sim, speed)
                ctl = controls(sim, 0.0, drive, brake)
            else:
                ctl = hold(sim)
            sim.step(ctl, aero.forces(sim, _wind_world(vw, from_left)))
            rec.tick({"wind_speed": ("m/s", vw)})
            if y_g is None and t >= t_g:
                y_g = float(sim.cg_world()[1])
            if y_d is None and t >= t_g + drift_t:
                y_d = float(sim.cg_world()[1])
            if t >= t_g:
                nu = sum(sim.out["ntot"][i] for i in up)
                if nu < min_up:
                    min_up = nu
                    rec.snap = []
                    rec.keep_snapshot()
            if abs(sim.attitude()[0]) > TIPPED_ROLL:
                notes.append("the vehicle tipped over in the gust; the run stops there (K2 has no body-ground contact)")
                break
        tipped = abs(sim.attitude()[0]) > TIPPED_ROLL
        if y_d is None:
            y_d = float(sim.cg_world()[1])
        res.append(_res("min_upwind_wheel_load", min_up, "N", ",".join(veh.corners[i].id for i in up)))
        res.append(_res("lateral_drift", abs(y_d - y_g), "m"))
        res.append(_res("tipped", 1.0 if tipped else 0.0, "1"))
        r_ = sim.rotation()
        cp_world = [sim.p[k] + float(r_[k] @ aero.side_cp) for k in range(3)]
        side = 0.5 * aero.rho * aero.cy * aero.side_area * gust * gust * (-1.0 if from_left else 1.0)
        wind_vec = [{"kind": "wind", "at": "side_cp", "origin_mm": [x * 1000.0 for x in cp_world],
                     "vector_N": [0.0, side, 0.0]}]
        return {"results": res, "series": rec.series(), "vectors": rec.vectors(-1, wind_vec), "assumptions": notes,
                "places": places}

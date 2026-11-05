"""Turning circle at full lock (S6)."""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import brentq

from ..checks import _check
from ..drive import SpeedController, controls, start_rolling
from ..model import DesignError, box_design, build_vehicle
from ..record import Recorder
from ..scenario import new_sim
from .base import MU, K2Tool, P, Pair, _res


def full_lock(veh, max_wheel: float, left: bool = True) -> float:
    """The bicycle-model angle delta (rad, + left) whose Ackermann wheel angles (veh.wheel_steer) put the
    inner steered wheel, the one turned most, at max_wheel (rad)."""
    if not any(c.steer and c.kind == "wheel" for c in veh.corners):
        raise DesignError("no support has actuation.steer; a turning circle needs a steered wheel")
    f = lambda d: float(np.max(np.abs(veh.wheel_steer(d)))) - max_wheel
    d = brentq(f, 1e-9, max_wheel, xtol=1e-14, rtol=1e-14)
    return d if left else -d


def fit_circle(xy: np.ndarray) -> tuple[np.ndarray, float]:
    """Least-squares circle through points (n, 2): (centre, radius) (algebraic fit: x^2 + y^2 + D x + E y + F = 0)."""
    a = np.column_stack([xy[:, 0], xy[:, 1], np.ones(len(xy))])
    b = -(xy[:, 0] ** 2 + xy[:, 1] ** 2)
    (d, e, f), *_ = np.linalg.lstsq(a, b, rcond=None)
    c = np.array([-d / 2.0, -e / 2.0])
    return c, float(math.sqrt(max(c @ c - f, 0.0)))


def body_points(design: dict) -> list[tuple[str, np.ndarray]]:
    """The plan corners (m, design frame) of every part box: (part id, point)."""
    out = []
    for p in design.get("parts", []):
        box = p.get("box")
        if not box:
            continue
        c, s = np.asarray(box["center_mm"], float) / 1000.0, np.asarray(box["size_mm"], float) / 2000.0
        for sx in (-1, 1):
            for sy in (-1, 1):
                out.append((p["id"], np.array([c[0] + sx * s[0], c[1] + sy * s[1], c[2]])))
    return out


TURN_RAD = math.pi  # the heading turns this much while the circles are measured (steady: half a turn is enough)


def circle_run(veh, design: dict, delta: float, speed: float, *, dt: float = 1e-3, rec_hz: float = 0.0,
               settle_s: float = 2.0) -> dict:
    """Drive at `speed` (m/s) with the bicycle angle `delta` held, from settle_s on while the heading turns
    TURN_RAD: the circle fitted to each wheel's contact centre (world x, y) and the largest distance of
    any part box's plan corner from the outer front wheel's circle's centre."""
    sim = new_sim(veh, dt=dt, settle_s=1.0)
    rec = Recorder(sim, rec_hz) if rec_hz > 0 else None
    start_rolling(sim, speed)
    ctrl = SpeedController(sim)
    pts = body_points(design)
    contacts, bodies, yaw0, turned, prev = [], [], None, 0.0, None
    t_max = settle_s + 4.0 * math.pi * 20.0 / max(speed, 0.1) + 10.0
    while sim.t < t_max:
        drive, brake = ctrl.torques(sim, speed)
        sim.step(controls(sim, delta, drive, brake))
        if rec is not None:
            rec.tick({"steer": ("deg", math.degrees(delta))})
        if sim.t < settle_s:
            continue
        yaw = sim.attitude()[2]
        if prev is not None:
            turned += (yaw - prev + math.pi) % (2.0 * math.pi) - math.pi
        prev = yaw
        contacts.append([sim.out["p"][i][:2] for i in range(sim.n)])
        r, o = sim.rotation(), sim.design_origin()
        bodies.append(np.array([(o + r @ p)[:2] for _, p in pts]))
        if abs(turned) >= TURN_RAD:
            break
    if abs(turned) < TURN_RAD:
        raise DesignError(f"the vehicle did not turn half a circle at full lock in {t_max:.0f} s")
    contacts = np.array(contacts)  # (n_steps, n_wheels, 2)
    circles = [fit_circle(contacts[:, i, :]) for i in range(sim.n)]
    steered = [i for i, c in enumerate(veh.corners) if c.steer and c.kind == "wheel"]
    outer = max(steered, key=lambda i: circles[i][1])
    centre = circles[outer][0]
    bodies = np.array(bodies)  # (n_steps, n_points, 2)
    dist = np.sqrt(((bodies - centre) ** 2).sum(axis=2))
    k = int(np.argmax(dist.max(axis=0)))
    return {"circles": circles, "outer": outer, "centre": centre, "body_radius": float(dist[:, k].max()),
            "body_part": pts[k][0] if pts else None, "recorder": rec, "sim": sim}


class TurningCircleTool(K2Tool):
    """Turning circle at full lock (S6): driven slowly with the steering at full lock, the inner front
    wheel at max_steer_angle and the outer one at its Ackermann angle (Vehicle.wheel_steer).

    Results (the larger of the two directions when both are run; where = which way and which wheel):
    turning_radius_wheel (m, the circle of the outer front wheel's contact centre), turning_radius_body
    (m, the circle of the farthest corner of any part box, plan view), turning_center_x / _y (m, the
    circle's centre in the road frame, the vehicle starting at its design origin), inner_steer_angle and
    outer_steer_angle (deg). The scene has both circles (kind circle). hot_spots: the radius each way.
    """

    name = "turning_circle"
    title = "小回り (K2 で全切りにして回る)"
    domain = "handling"
    tests = ["S6"]
    params = (P("speed", "km/h", 5.0, "held speed while turning (slow, so the tyres hardly slip)"),
              P("direction", None, "both", "which way to turn", choices=("both", "left", "right")),
              P("max_steer_angle", "deg", None, "the inner front wheel's largest steer angle (default: the design "
                "variable max_steer_angle, else K2's 35 deg)", var=("max_steer_angle",)),
              MU)
    pairs = (Pair("turning_radius_wheel", "turning.min_radius", "R", (("delta_i", "max_steer_angle"),),
                  "L and t from the K2 model (the steered axle to the unsteered one, the steered axle's track); "
                  "K2 drives slowly, so its tyres slip a little and the circle comes out a little larger"),)

    def own_checks(self) -> list[dict]:
        d = box_design(mass=500.0, cg_x=-0.9, cg_z=0.5, wheelbase=1.8, track=1.2, susp="rigid", wheel_mass=20.0,
                       variables={"tyre_stiffness": {"value": 10000.0, "unit": "N/mm"},
                                  "max_steer_angle": {"value": 30.0, "unit": "deg"}}, name="turning check")
        veh = build_vehicle(d)
        di = math.radians(30.0)
        speed = 2.0
        r = circle_run(veh, d, full_lock(veh, di), speed)
        L, t = abs(veh.l_steer), 1.2
        d_o = math.atan(1.0 / (1.0 / math.tan(di) + t / L))
        expected = L / math.sin(d_o)
        return [_check(f"rigid vehicle, wheelbase {L:g} m, track {t:g} m, inner wheel {math.degrees(di):g} deg, "
                       f"{speed:g} m/s: outer front wheel's turning radius", "turning_radius_wheel", expected,
                       r["circles"][r["outer"]][1], "m", 1e-2,
                       "Ackermann: cot(delta_o) = cot(delta_i) + t / L, R = L / sin(delta_o) (turning.min_radius)",
                       case_ja=f"サスペンションの無い車 (軸距 {L:g} m、輪距 {t:g} m) を、内側の前輪 {math.degrees(di):g}° の"
                               f"全切りで {speed:g} m/s で回す: 外側の前輪の回る円の半径",
                       source_ja="アッカーマンの舵: cot δ外 = cot δ内 + 輪距 / 軸距、半径 = 軸距 / sin δ外 "
                                 "(turning.min_radius)")]

    def _run(self, design: dict, lp: dict) -> dict:
        veh = self.vehicle(design, lp)
        notes = list(veh.assumptions)
        di = self.q(lp, "max_steer_angle", "rad")
        if di is None:
            di = veh.max_steer
            notes.append(f"max_steer_angle {math.degrees(di):g} deg read as the inner front wheel's largest angle "
                         "(K2's driver model reads it as the bicycle model's angle)")
        speed = self.q(lp, "speed", "m/s")
        sides = {"both": ["left", "right"], "left": ["left"], "right": ["right"]}[self.text(lp, "direction")]
        runs = {}
        for side in sides:
            delta = full_lock(veh, di, side == "left")
            runs[side] = (delta, circle_run(veh, design, delta, speed, dt=self.dt, rec_hz=self.record_hz))
        worst = max(sides, key=lambda s: runs[s][1]["circles"][runs[s][1]["outer"]][1])
        delta, r = runs[worst]
        cid = veh.corners[r["outer"]].id
        wheel_r = r["circles"][r["outer"]][1]
        angles = [abs(a) for a in veh.wheel_steer(delta)]
        steered = [a for a, c in zip(angles, veh.corners) if c.steer and c.kind == "wheel"]
        res = [_res("turning_radius_wheel", wheel_r, "m", f"turning {worst}, {cid}"),
               _res("turning_radius_body", r["body_radius"], "m", f"turning {worst}, {r['body_part']}"),
               _res("turning_center_x", float(r["centre"][0]), "m", f"turning {worst}"),
               _res("turning_center_y", float(r["centre"][1]), "m", f"turning {worst}"),
               _res("inner_steer_angle", math.degrees(max(steered)), "deg"),
               _res("outer_steer_angle", math.degrees(min(steered)), "deg")]
        cx, cy = (float(v) * 1000.0 for v in r["centre"])
        scene = [{"kind": "circle", "position_mm": [cx, cy, 0.0], "size_mm": [2000.0 * wheel_r, 2000.0 * wheel_r, 0.0],
                  "label": f"外側の前輪 ({cid}) の回る円 半径 {wheel_r:.2f} m"},
                 {"kind": "circle", "position_mm": [cx, cy, 0.0],
                  "size_mm": [2000.0 * r["body_radius"], 2000.0 * r["body_radius"], 0.0],
                  "label": f"車体のいちばん外の点 ({r['body_part']}) の回る円 半径 {r['body_radius']:.2f} m"}]
        # the record, the circles and the results are all of the same turn (the larger radius)
        rec = r["recorder"]
        places = [{"where": f"turning {s}", "quantity": "turning_radius_wheel",
                   "value": runs[s][1]["circles"][runs[s][1]["outer"]][1], "unit": "m", "judged": True} for s in sides]
        out = {"results": res, "scene": scene, "assumptions": notes, "places": places}
        if rec is not None:
            out["series"] = rec.series()
            out["vectors"] = rec.vectors(-1)
        return out

"""The flooded-road pass with the water and the K2 vehicle moving each other.

Every few milliseconds the water's force on each shape goes to K2, K2 moves the vehicle one interval ahead, and the water runs to
that pose. The vehicle slows, pitches and rises because of the water, and the water sees it do so. A one-way run cannot do this:
the forces of a rigid pass are near the vehicle's weight, and they would go on lifting a vehicle that has already left the water.

How the forces go in:
- A shape that is not a wheel: to the body, at the shape's centre.
- A wheel, along the road and across: to the body, at the hub.
- A wheel, upward: on the wheel, through its contact (FloodRoad), so the wheel rises and the tyre gives up that load; off the road,
  to the body at the hub. It is first multiplied by `lift_ratio`, the wheels' upward impulse not turning over turning at the same
  grid and sound speed. Turning, that impulse keeps growing with the sound speed (water caught in the wedge between the wheel and
  the road cannot leave at this grid), so it is not used as it stands.

In the water the whole vehicle takes the body's x, z and pitch; roll, yaw and sideways movement stay in K2 only (the case is the
same on both sides, and K2 gives them near zero). Each wheel's row in the shape table follows K2's suspension and spin.

Axes. Water: the vehicle runs along -x, shape x = mid - design x, shape y = -design y, z up, pitch > 0 nose up.
K2: x_k = x0 - x_water, y_k = -y_water, z up, pitch > 0 nose down.
"""

from __future__ import annotations

import copy
import json
import math
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

import numpy as np

from hiraku.cfd.cuda import Gpu, gpu_memory_mib
from hiraku.cfd.geometry import Cylinder
from hiraku.cfd.water import CFL, E_MAX, E_MIN, FRAME_DT, GRAVITY, RHO_WATER, STATEMENT, Pose, Road, WaterSim, _quantize, particles_inside, plan_case, shape_table, to_vehicle

WHEELS = ("FL", "FR", "RL", "RR")
TRAVEL_SLACK_M = 0.2  # a wheel's cells in the shape table reach this far, so its row can follow the suspension
COUPLE_INTERVAL_S = 1.0 / 270.0  # how often the water and K2 exchange, about
K2_STEPS_PER_INTERVAL = 4
# The wheels' upward impulse not turning over turning (lift_ratio_from): v5, 30 mm, sound 200 m/s, -3425.7 / -13406.6 N s, two passes
# of 2026-11-07. Other designs and grids: 確かめていない
LIFT_RATIO = 0.2555
FORCE_MEAN_INTERVALS = 3  # the force K2 gets is the mean of the last few intervals
LEAD_S = 0.6  # K2 runs this long on the ramp before the water starts, so the start is not a jolt
SETTLE_S = 2.0
LIFTED_TOO_FAR_RAD = 1.0  # the run stops rather than go on with the vehicle turned over


class TravellingWheel(Cylinder):
    """A wheel whose row in the table moves with the suspension: its cells reach TRAVEL_SLACK_M further."""

    def bounds(self):
        lo, hi = super().bounds()
        return lo - TRAVEL_SLACK_M, hi + TRAVEL_SLACK_M


def coupled_table(shapes: list[Any], dx: float, top: float) -> Any:
    """The shape table with one group per shape, so moments can be built from the shapes' centres."""
    out, sticky = [], []
    for s in shapes:
        c = copy.copy(s)
        if s.group == "wheels":
            sticky.append(s.name)
            c.__class__ = TravellingWheel
        c.group = s.name
        out.append(c)
    return shape_table(out, dx, top=top, sticky_groups=tuple(sticky))


class FloodRoad:
    """The water case's road, in the form K2's Sim asks a road for. K2 has no load on a wheel from outside, so a wheel on the road
    gets `lift` (N, upward) through its contact: the contact force is the tyre's plus the lift, and the wheel rises until the tyre
    has given up that much."""

    name = "flooded road"

    def __init__(self, profile: Road, x0: float, k_z: list[float]):
        self.profile, self.x0, self.k_z = profile, x0, k_z
        self.lift = [0.0] * len(k_z)
        self.depth = [0.0] * len(k_z)

    def height(self, x: float, y: float = 0.0) -> float:
        return float(self.profile.height(self.x0 - x))

    def contacts(self, centres: list, radii: list) -> list:
        out = []
        for i, ((cx, cy, cz), r) in enumerate(zip(centres, radii)):
            xw = self.x0 - cx
            slope = -float(self.profile.gradient(xw))  # dh / dx_k
            inv = 1.0 / math.sqrt(1.0 + slope * slope)
            nx, nz = -slope * inv, inv
            dist = (cz - float(self.profile.height(xw))) * inv
            self.depth[i] = r - dist
            on_road = r - dist > 0.0
            out.append([(cx - nx * dist, cy, cz - nz * dist, nx, 0.0, nz, r - dist + (self.lift[i] / self.k_z[i] if on_road else 0.0))])
        return out


def couple_loads(force: np.ndarray, moment: np.ndarray, arm: float = 1.0) -> list:
    """A force at the CG and a moment as pairs of forces, the form Sim.step takes."""
    mx, my, mz = (float(v) / (2.0 * arm) for v in moment)
    return [(tuple(float(v) for v in force), (0.0, 0.0, 0.0)),
            ((0.0, 0.0, mx), (0.0, arm, 0.0)), ((0.0, 0.0, -mx), (0.0, -arm, 0.0)),
            ((my, 0.0, 0.0), (0.0, 0.0, arm)), ((-my, 0.0, 0.0), (0.0, 0.0, -arm)),
            ((0.0, mz, 0.0), (arm, 0.0, 0.0)), ((0.0, -mz, 0.0), (-arm, 0.0, 0.0))]


def shape_centres(table: Any) -> np.ndarray:
    """Each group's centre in shape axes (one group per shape name; shapes of the same name share the mean)."""
    centre: dict[str, list[np.ndarray]] = {}
    for s in table.solids:
        c = s.center if isinstance(s, Cylinder) else 0.5 * (s.bounds()[0] + s.bounds()[1])
        centre.setdefault(s.group, []).append(np.asarray(c, float))
    return np.array([np.mean(centre[g], axis=0) for g in table.groups])


class CoupledVehicle:
    """K2 on the water case's road, driven by the design's motor toward the target speed, taking the water's forces. `k2` holds what
    is needed from K2 (hiraku.tools.cfd_tools.k2_parts): this package does not import it."""

    def __init__(self, k2: Any, design: dict[str, Any], profile: Road, target_ms: float, x_start: float, interval_s: float, centres: np.ndarray, groups: list[str], lift_ratio: float, loading: str | None = "two_cargo"):
        self.k2, self.target, self.interval, self.lift_ratio = k2, target_ms, interval_s, lift_ratio
        self.design, self.profile, self.loading = design, profile, loading
        v = design["variables"]
        self.motor = k2.Motor(float(v["motor_peak_torque"]["value"]), float(v["motor_peak_power"]["value"]) * 1000.0, float(v["gear_ratio"]["value"]),
                              float(v["drivetrain_efficiency"]["value"]), float(v["motor_max_speed"]["value"]) * 2.0 * math.pi / 60.0)
        self.veh = k2.build_vehicle(k2.on_board(design, loading))
        self.mid_x = float(np.mean([c.pos[0] for c in self.veh.corners]))
        self.wheels = [groups.index(f"wheel_{w}") for w in WHEELS]
        self.others = [i for i in range(len(groups)) if i not in self.wheels]
        cg = np.asarray(self.veh.cg_s, float)
        self.points = np.column_stack([self.mid_x - centres[:, 0], -centres[:, 1], centres[:, 2]]) - cg
        self.k_z = np.array([float(c.k_z) for c in self.veh.corners])
        guess = x_start + target_ms * LEAD_S - abs(self.mid_x) * math.cos(math.atan(profile.slope))
        self._start(guess)
        self._start(guess + x_start - self.pose_now()[0])

    def _start(self, x0: float) -> None:
        k2 = self.k2
        self.road = FloodRoad(self.profile, x0, self.k_z.tolist())
        self.sim = k2.Sim(self.veh, self.road, dt=self.interval / K2_STEPS_PER_INTERVAL)
        theta = math.atan(self.profile.slope)
        self.sim.q = k2.quat_from_euler(0.0, theta, 0.0)
        origin = np.array([0.0, 0.0, self.road.height(0.0)])
        self.sim.p = (origin + np.array(self.sim.rotation()) @ np.asarray(self.veh.cg_s, float)).tolist()
        k2.settle(self.sim, SETTLE_S)
        self.sim.t = 0.0
        k2.start_rolling(self.sim, self.target)
        self.speed_ctl = k2.SpeedController(self.sim, motor=self.motor)
        self.driver = k2.PathDriver(self.sim, lambda x: 0.0)
        zero = np.zeros((len(self.points), 3))
        for _ in range(int(round(LEAD_S / self.interval))):
            self.advance(zero, self.interval)

    def advance(self, force_water: np.ndarray, interval_s: float) -> None:
        """One interval of K2 with the force per group, on the vehicle, in water axes."""
        self.sim.dt = interval_s / K2_STEPS_PER_INTERVAL
        f = force_water.copy()
        f[:, :2] *= -1.0
        rot = np.array(self.sim.rotation())
        body = f @ rot  # R^T f, row by row
        force = body[self.others].sum(axis=0)
        moment = np.cross(self.points[self.others], body[self.others]).sum(axis=0)
        world_wheel = f[self.wheels].copy()
        world_wheel[:, 2] *= self.lift_ratio
        lift = world_wheel[:, 2]
        for _ in range(K2_STEPS_PER_INTERVAL):
            on_road = np.array(self.road.depth) > 0.0
            through_tyre = np.where(on_road, np.maximum(lift, 0.0), 0.0)  # off the road, or downward, it goes to the body at the hub
            self.road.lift = through_tyre.tolist()
            rest = world_wheel.copy()
            rest[:, 2] -= through_tyre
            rot = np.array(self.sim.rotation())
            rest_body = rest @ rot
            hubs = (self.sim.wheel_centres() - np.array(self.sim.p)) @ rot
            ext = couple_loads(force + rest_body.sum(axis=0), moment + np.cross(hubs, rest_body).sum(axis=0))
            drive, brake = self.speed_ctl.torques(self.sim, self.target)
            self.sim.step(self.k2.controls(self.sim, self.driver.steer(), drive, brake), ext)

    def pose_now(self) -> tuple[float, float, float]:
        """The body's x, z and pitch in water axes."""
        rot = np.array(self.sim.rotation())
        p = self.sim.design_origin() + rot @ np.array([self.mid_x, 0.0, 0.0])
        return self.road.x0 - float(p[0]), float(p[2]), -math.atan2(-rot[2, 0], rot[0, 0])

    def wheel_rows(self) -> tuple[np.ndarray, np.ndarray]:
        """Each wheel's centre in shape axes, and its spin over the spin at the target speed. The centre comes from where the wheel
        is in K2's world, not from the body's axes: the water's vehicle does not roll, and a wheel placed by the rolled body's axes
        would go into the road. K2's tyre squashes; the round wheel in the water and in the picture stays on the road, or it would
        squeeze the water under it harder than a wheel that only touches it."""
        centres = self.sim.wheel_centres()
        squash = np.maximum(np.array(self.road.depth), 0.0)
        x, z, pitch = self.pose_now()
        c, s = math.cos(pitch), math.sin(pitch)
        rx, rz = self.road.x0 - centres[:, 0] - x, centres[:, 2] + squash - z
        side = self.sim.design_origin()[1] - centres[:, 1]
        shape = np.column_stack([c * rx - s * rz, side, s * rx + c * rz])
        at_target = [self.target / c.radius for c in self.veh.corners]
        return shape, np.array([om / a for om, a in zip(self.sim.om, at_target)])

    def state(self) -> dict[str, Any]:
        sim = self.sim
        roll, pitch, yaw = sim.attitude()
        ground = [float(self.road.height(float(c[0]))) for c in sim.wheel_centres()]
        centre, spin = self.wheel_rows()
        return {"speed_ms": float(sim.forward_speed()), "roll_deg": math.degrees(roll), "pitch_k2_deg": math.degrees(pitch), "yaw_deg": math.degrees(yaw), "y_m": float(sim.design_origin()[1]),
                "tyre_n": [max(float(v) - lift, 0.0) for v, lift in zip(sim.out.get("ntot", [0.0] * sim.n), self.road.lift)], "travel_m": [float(v) for v in sim.s], "ground_m": ground, "wheel_centres": centre.tolist(), "wheel_spin": spin.tolist(),
                "wheel_water_lift_n": [float(v) for v in self.road.lift]}


def vehicle_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """How the vehicle moved, from the records of a coupled pass: against the road under it, so the ramps do not count."""
    t = np.array([r["t"] for r in records])
    pose = np.array([r["pose"] for r in records])
    state = [r["vehicle"] for r in records]
    speed = np.array([s["speed_ms"] for s in state])
    ground = np.array([s["ground_m"] for s in state])  # FL FR RL RR
    centres = np.array([s["wheel_centres"] for s in state])
    wheelbase = float(np.mean(centres[:, 2:, 0]) - np.mean(centres[:, :2, 0]))
    pitch = np.degrees(pose[:, 2] - np.arctan2(ground[:, :2].mean(axis=1) - ground[:, 2:].mean(axis=1), wheelbase))
    height = (pose[:, 1] - ground.mean(axis=1)) * 1000.0
    tyres = np.array([s["tyre_n"] for s in state])
    front_off = np.flatnonzero((tyres[:, 0] <= 0.0) & (tyres[:, 1] <= 0.0))
    travel = np.array([s["travel_m"] for s in state]) * 1000.0
    return {"speed_kmh": {"start": float(speed[0] * 3.6), "lowest": float(speed.min() * 3.6), "lowest_at_s": float(t[speed.argmin()]), "end": float(speed[-1] * 3.6)},
            "time_s": float(t[-1]), "pitch_deg_nose_up_against_road": {"min": float(pitch.min()), "max": float(pitch.max()), "max_at_s": float(t[pitch.argmax()])},
            "height_mm_against_road": {"min": float(height.min() - height[0]), "max": float(height.max() - height[0])},
            "roll_deg_largest": float(max(abs(s["roll_deg"]) for s in state)), "yaw_deg_largest": float(max(abs(s["yaw_deg"]) for s in state)),
            "front_wheels_off_road_s": [float(t[front_off[0]]), float(t[front_off[-1]])] if len(front_off) else None,
            "suspension_travel_mm": {"front": [float(travel[:, :2].min()), float(travel[:, :2].max())], "rear": [float(travel[:, 2:].min()), float(travel[:, 2:].max())]}}


def lift_ratio_from(locked_case: str | Path, turning_case: str | Path) -> float:
    def wheels_z(path: str | Path) -> float:
        c = json.loads((Path(path) / "case.json").read_text(encoding="utf-8"))
        return float(c["records"][-1]["impulse"][c["groups"].index("wheels")][2])
    return wheels_z(locked_case) / wheels_z(turning_case)


def run_coupled(gpu: Gpu, k2: Any, design: dict[str, Any], lift_ratio: float, depth_m: float = 0.2, speed_ms: float = 5.0, dx: float = 0.03, out: str | Path = "water_coupled", loading: str | None = "two_cargo",
                frame_dt: float = FRAME_DT, t_max_s: float = 5.0, reserve_mib: int = 600, progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """A pass with the vehicle free to answer the water. Saves case.json and frames/p*.npz in `out`, as run_flooded_road does; each
    record also has `vehicle`, K2's state at that frame. Stops when the rear is clear of the water, at t_max_s, or if it turns over."""
    say = progress or (lambda s: None)
    memory_at_start = gpu_memory_mib()
    plan = plan_case(design, depth_m, speed_ms, dx, True, loading, frame_dt=frame_dt)
    grid, road, dt, per_frame = plan["grid"], plan["road"], plan["dt"], plan["per_frame"]
    table = coupled_table(plan["shapes"], dx, grid.height_m)
    intervals = max(1, int(round(per_frame * dt / COUPLE_INTERVAL_S)))
    interval_steps = [per_frame // intervals] * intervals
    interval_steps[-1] += per_frame - sum(interval_steps)
    interval_s = interval_steps[0] * dt
    car = CoupledVehicle(k2, design, road, speed_ms, plan["x_start"], interval_s, shape_centres(table), table.groups, lift_ratio, loading)
    x_end = plan["x_start"] - plan["duration"] * speed_ms
    now = np.array(car.pose_now())
    pose0 = Pose(*now.tolist(), -speed_ms, 0.0, 0.0)
    particles = plan["particles"]
    world0 = particles[:, :3].astype(np.float64) - grid.offset
    particles = particles[~particles_inside(plan["solid"], to_vehicle(world0, pose0))]
    folder = Path(out)
    (folder / "frames").mkdir(parents=True, exist_ok=True)
    sim = WaterSim(gpu, grid, particles, road, dt, plan["sound"], table, open_ends=True, reserve_mib=reserve_mib)
    wheel_rows = [next(k for k, row in enumerate(table.shapes) if table.groups[row[2]] == f"wheel_{w}") for w in WHEELS]
    cyl_index = [int(table.shapes[k][0]) for k in wheel_rows]
    cyl = table.cyl.copy()
    spin0 = cyl[cyl_index, 8:11].copy()

    def set_wheels() -> None:
        centre, spin = car.wheel_rows()
        for k, i in enumerate(cyl_index):
            cyl[i, :3] = centre[k]
            cyl[i, 8:11] = spin0[k] * spin[k]
        sim.buf["cyl"].write(cyl)

    records: list[dict[str, Any]] = []
    bounds_lo = -grid.offset
    bounds_hi = np.array(grid.nodes) * grid.dx - grid.offset
    recent: deque[np.ndarray] = deque(maxlen=FORCE_MEAN_INTERVALS)
    n_groups = len(table.groups)
    last_impulse = np.zeros((n_groups, 3))
    fault = None
    t0 = time.perf_counter()
    frame = 0
    force = np.zeros((n_groups, 3))
    try:
        sim.set_pose(pose0)
        set_wheels()
        while True:
            part = sim.read_particles()
            t = frame * per_frame * dt
            bad = int((~np.isfinite(part[:, :6])).any(axis=1).sum())
            if bad:
                fault = f"{t:.3f} 秒で {bad} 個の粒子が数でなくなった"
                break
            world = part[:, :3].astype(np.float64) - grid.offset
            in_box = np.abs(world[:, 0]) <= 0.5 * grid.length_m
            momentum = [float(v) for v in sim.mass * part[:, 3:6].sum(axis=0, dtype=np.float64)]
            speed = np.sqrt((part[in_box, 3:6].astype(np.float64) ** 2).sum(axis=1))
            world = world[in_box]
            records.append({"t": t, "pose": now.tolist(), "particles_left_box": int((~in_box).sum()), "max_speed": float(speed.max()), "top_m": float(world[:, 2].max()), "momentum": momentum,
                            "impulse": [[float(v) for v in row] for row in sim.read_impulse()], "vehicle": car.state()})
            xyz, scale = _quantize(world, bounds_lo, bounds_hi)
            np.savez(folder / "frames" / f"p{frame:04d}.npz", xyz=xyz, speed=np.clip(np.rint(speed * 10.0), 0, 255).astype(np.uint8), lo=bounds_lo, scale=scale)
            v = records[-1]["vehicle"]
            say(f"frame {frame} t {t:.3f} x {now[0]:.2f} v {v['speed_ms']:.2f} z {now[1]:.3f} pitch {math.degrees(now[2]):.2f} tyres {[round(n) for n in v['tyre_n']]} wall {time.perf_counter() - t0:.0f}s")
            if now[0] <= x_end or t >= t_max_s:
                break
            if abs(now[2]) > LIFTED_TOO_FAR_RAD:
                fault = f"{t:.3f} 秒で車が立ち上がった (ピッチ {math.degrees(now[2]):.0f} 度)"
                break
            for steps in interval_steps:
                car.advance(force, steps * dt)
                ahead = np.array(car.pose_now())
                rate = (ahead - now) / (steps * dt)
                start_n, start_pose = sim.steps, now.copy()
                sim.step(steps, lambda n, sn=start_n, p=start_pose, r=rate: Pose(*(p + r * (n - sn) * dt).tolist(), float(r[0]), float(r[1]), float(r[2])))
                set_wheels()
                now = ahead
                impulse = sim.read_impulse()[:n_groups]
                recent.append(-(impulse - last_impulse) / (steps * dt))
                last_impulse = impulse
                force = np.mean(recent, axis=0)
            frame += 1
    finally:
        sim.close()
    case = {
        "statement": STATEMENT,
        "coupling": {"lift_ratio": lift_ratio, "interval_s": interval_s, "k2_steps_per_interval": K2_STEPS_PER_INTERVAL, "force_mean_intervals": FORCE_MEAN_INTERVALS, "target_speed_ms": speed_ms, "mass_kg": float(car.veh.m_total)},
        "inputs": {"design_version": design.get("design_version"), "depth_m": depth_m, "speed_ms": speed_ms, "speed_kmh": speed_ms * 3.6, "dx_m": dx, "rotate_wheels": True, "loading": loading},
        "road": {"water_length_m": 2.0 * road.half_length_m, "slope": road.slope, "level_m": plan["level"], "box_m": [grid.length_m, grid.width_m, grid.height_m]},
        "memory": {"at_start_mib": memory_at_start, "reserve_mib": reserve_mib},
        "particles": len(particles), "cells": list(grid.nodes), "step_s": dt, "steps": sim.steps, "steps_per_frame": per_frame, "frames": len(records), "frame_s": per_frame * dt, "run_wall_s": time.perf_counter() - t0,
        "device": gpu.name, "sound_speed_ms": plan["sound"], "density_kg_m3": RHO_WATER, "gravity_ms2": GRAVITY, "cfl": CFL, "e_max": E_MAX, "e_min": E_MIN, "particle_mass_kg": sim.mass, "groups": table.groups, "faulted": fault,
        "assumptions": ["水の力と波の高さは実験と比べておらず、確かめていない",
                        "車輪の上向きの力は、回す計算では音速を上げるほど大きくなり続けるので、回さない計算との比を掛けて K2 に返す (coupling.lift_ratio)",
                        "水の側の車は K2 の車体の x、z、ピッチで動く。ロール、ヨー、横の動きは K2 の側だけにある",
                        "車輪と路面の摩擦、サスペンションなどは K2 の設定のまま。水で滑りやすくなることは入れていない"],
        "records": records,
    }
    (folder / "case.json").write_text(json.dumps(case, ensure_ascii=False, indent=1), encoding="utf-8")
    return case

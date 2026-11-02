"""What acts on the vehicle besides the road: air, the motor, the driver, the load."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass

from hiraku.contract.protocols import DEFAULT_LOADING, LOADINGS

from .model import DesignError, Vehicle
from .sim import Controls, Sim, quat_to_r

OCCUPANTS = {"empty": 0, "driver": 1, "two": 2, "two_cargo": 2}
CARGO = {"empty": False, "driver": False, "two": False, "two_cargo": True}

# Air density at sea level, 15 degC, in the ICAO / ISO 2533 standard atmosphere
# (1.225 kg/m^3; not checked against the ISO 2533 text).
RHO_AIR = 1.225


def on_board(design: dict, loading: str | None) -> dict:
    """A copy of the design with the occupants and cargo of `loading` (contract LOADINGS).

    Occupants take their seats in the order of design.parts (the first one drives);
    cargo parts ride only with two_cargo.
    """
    loading = loading or DEFAULT_LOADING
    if loading not in LOADINGS:
        raise DesignError(f"unknown loading {loading!r}; use one of {', '.join(LOADINGS)}")
    out = copy.deepcopy(design)
    seated = 0
    parts = []
    for p in out.get("parts", []):
        kind = str(p.get("kind", "")).lower()
        if kind == "occupant":
            if seated >= OCCUPANTS[loading]:
                continue
            seated += 1
        elif kind == "cargo" and not CARGO[loading]:
            continue
        parts.append(p)
    if seated < OCCUPANTS[loading] and any(str(p.get("kind", "")).lower() == "occupant" for p in design.get("parts", [])):
        raise DesignError(f"loading {loading} needs {OCCUPANTS[loading]} occupant parts, the design has {seated}")
    out["parts"] = parts
    return out


@dataclass
class Aero:
    """Drag and side force: F = 1/2 rho C A u|u| on the air speed relative to the body."""

    rho: float
    cd: float
    cy: float
    front_area: float
    side_area: float
    front_cp: tuple   # body frame, relative to the sprung CG (m)
    side_cp: tuple

    @classmethod
    def of(cls, veh: Vehicle, rho: float, cd: float, cy: float, front_area: float | None = None,
           side_area: float | None = None, side_cp_height: float | None = None) -> "Aero":
        """Areas and centres of pressure from the part boxes unless given; side_cp_height
        (m above the ground under the wheels) replaces the side centre's height."""
        fcp = tuple(float(x) for x in (veh.front_cp - veh.cg_s))
        scp = tuple(float(x) for x in (veh.side_cp - veh.cg_s))
        if veh.symmetric:
            fcp = (fcp[0], 0.0, fcp[2])
            scp = (scp[0], 0.0, scp[2])
        if side_cp_height is not None:
            ground = min(float(c.pos[2]) - c.radius for c in veh.corners)
            scp = (scp[0], scp[1], ground + side_cp_height - float(veh.cg_s[2]))
        # the drag acts at the frontal centre of pressure's height, on the body centreline length-wise
        fcp = (scp[0], fcp[1], fcp[2])
        return cls(rho, cd, cy, veh.front_area if front_area is None else front_area,
                   veh.side_area if side_area is None else side_area, fcp, scp)

    def forces(self, sim: Sim, wind_world=(0.0, 0.0, 0.0)) -> list:
        r = quat_to_r(sim.q)
        rel = [sim.v[k] - wind_world[k] for k in range(3)]
        ux = r[0, 0] * rel[0] + r[1, 0] * rel[1] + r[2, 0] * rel[2]
        uy = r[0, 1] * rel[0] + r[1, 1] * rel[1] + r[2, 1] * rel[2]
        q = 0.5 * self.rho
        fx = -q * self.cd * self.front_area * ux * abs(ux)
        fy = -q * self.cy * self.side_area * uy * abs(uy)
        return [((float(fx), 0.0, 0.0), self.front_cp), ((0.0, float(fy), 0.0), self.side_cp)]


@dataclass
class Motor:
    """Peak torque up to the base speed, then constant power; wheel torque split equally (open differential)."""

    torque: float          # N m at the motor
    power: float           # W
    gear: float            # motor turns per wheel turn
    efficiency: float = 0.9
    max_speed: float = math.inf  # rad/s at the motor

    def wheel_torque_total(self, wheel_speed: float, throttle: float = 1.0) -> float:
        wm = abs(wheel_speed) * self.gear
        if wm >= self.max_speed:
            return 0.0
        t = self.torque if wm * self.torque <= self.power else self.power / wm
        return throttle * t * self.gear * self.efficiency


class SpeedController:
    """PI on the forward speed: positive output drives, negative brakes (N m per wheel)."""

    def __init__(self, sim: Sim, kp: float | None = None, ki: float | None = None, max_drive: float = math.inf,
                 max_brake: float = math.inf):
        veh = sim.veh
        r = sum(c.radius for c in veh.corners) / veh.n
        self.driven = [i for i, c in enumerate(veh.corners) if c.drive]
        self.braked = [i for i, c in enumerate(veh.corners) if c.brake]
        if not self.driven:
            raise DesignError("no support has actuation.drive; a driven wheel is needed to move the vehicle")
        # per m/s of speed error: enough to give ~1 m/s^2 per 0.2 m/s
        m = veh.m_total
        self.kp = kp if kp is not None else 5.0 * m * r / len(self.driven)
        self.ki = ki if ki is not None else 2.0 * m * r / len(self.driven)
        self.integ = 0.0
        self.max_drive = max_drive
        self.max_brake = max_brake

    def torques(self, sim: Sim, target: float, ff: float = 0.0) -> tuple[list, list]:
        err = target - sim.forward_speed()
        self.integ += err * sim.dt
        u = ff + self.kp * err + self.ki * self.integ
        drive = [0.0] * sim.n
        brake = [0.0] * sim.n
        if u >= 0.0:
            t = min(u, self.max_drive)
            for i in self.driven:
                drive[i] = t
        else:
            t = min(-u, self.max_brake)
            for i in self.braked:
                brake[i] = t * len(self.driven) / max(len(self.braked), 1)
        return drive, brake


class PathDriver:
    """Pure pursuit on a reference line y_ref(x) (road frame), steering-rate limited."""

    def __init__(self, sim: Sim, y_ref, preview_time: float = 0.6, min_preview: float = 3.0,
                 steer_rate: float = math.radians(400.0)):
        self.sim = sim
        self.y_ref = y_ref
        self.tp = preview_time
        self.lmin = min_preview
        self.rate = steer_rate
        self.delta = 0.0
        veh = sim.veh
        self.l = veh.l_steer if any(c.steer for c in veh.corners) else 1.0
        self.max = veh.max_steer

    def steer(self) -> float:
        sim = self.sim
        r = quat_to_r(sim.q)
        org = sim.design_origin()
        # reference point: the non-steered axle centre
        ref = org + r @ [self.sim.veh.x_ref, 0.0, 0.0]
        yaw = math.atan2(r[1, 0], r[0, 0])
        v = max(abs(sim.forward_speed()), 0.5)
        ld = max(self.lmin, self.tp * v)
        xt = ref[0] + ld
        yt = self.y_ref(xt)
        dx, dy = xt - ref[0], yt - ref[1]
        c, s = math.cos(yaw), math.sin(yaw)
        bx, by = c * dx + s * dy, -s * dx + c * dy
        alpha = math.atan2(by, bx)
        dist = math.sqrt(bx * bx + by * by)
        target = math.atan2(2.0 * self.l * math.sin(alpha), dist)
        target = max(-self.max, min(self.max, target))
        step = self.rate * sim.dt
        self.delta += max(-step, min(step, target - self.delta))
        return self.delta


def start_rolling(sim: Sim, speed: float):
    """Set the settled vehicle moving forward at `speed` with free-rolling wheels."""
    r = quat_to_r(sim.q)
    fwd = r[:, 0]
    sim.v = [float(fwd[0] * speed), float(fwd[1] * speed), float(fwd[2] * speed)]
    for i, c in enumerate(sim.veh.corners):
        if c.kind == "wheel":
            re = c.radius - c.static_load / c.k_z
            sim.om[i] = speed / re


def controls(sim: Sim, delta: float, drive: list, brake: list) -> Controls:
    return Controls(sim.veh.wheel_steer(delta).tolist(), drive, brake)

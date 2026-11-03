"""The drive from the motor to the driven wheels: motor -> reducer -> a driveshaft to each
driven wheel.

    info = drivetrain.place(lay, total_mass=25.0, station="B", shaft=(30.0, 5.0), rho=7850.0)

Where the parts go (each rule from the K2 hard points of the driven supports and the frame):
- each driveshaft's outer joint is its wheel's centre (hard point wheel_center) and its inner
  joint is at the y of the lower arm's inner pivots (the middle of lca_front and lca_rear), at
  that middle's x and at the wheel centre's height in the design position;
- the reducer sits between the two inner joints (its side faces at the joints, its output on
  their line), its top REDUCER_BELOW_MM below the frame's cross member at the axle station;
- the motor sits ahead of the reducer, its back face on the reducer's front face, on the same
  height (motor axis parallel to the outputs: assumption).

Masses: the given total (the motor and reducer of the earlier versions) is split: each
driveshaft is a steel tube (outer diameter and wall given) from joint to joint plus two joints of
CV_JOINT_KG; of the rest, REDUCER_SHARE is the reducer and the remainder the motor. The
driveshafts have no carried_by: they ride with their wheel (the outer joint moves with it).
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from hiraku.designgen.kit.links import put_link
from hiraku.designgen.kit.package import Item, Layout, rear_at

Record = dict[str, Any]

CV_JOINT_KG = 0.8  # one constant velocity joint with its boot (assumption)
REDUCER_SHARE = 0.4  # the reducer's share of the motor and reducer, without the shafts (assumption)
REDUCER_LENGTH_MM = 160.0  # reducer case along x (assumption)
REDUCER_BELOW_MM = 10.0  # the reducer's top this far below the cross member at the axle station (assumption)
MOTOR_SIZE_MM = (250.0, 300.0, 220.0)  # motor case, x y z (the v1 motor box)
# Turning inertia (assumptions): the rotor a solid cylinder of ROTOR_SHARE of the motor's mass and
# radius ROTOR_RADIUS_MM; the reducer's output gear a disc of GEAR_SHARE of the reducer's mass and
# radius GEAR_RADIUS_MM, turning with the wheels.
ROTOR_SHARE = 0.35
ROTOR_RADIUS_MM = 50.0
GEAR_SHARE = 0.3
GEAR_RADIUS_MM = 70.0


def driven(design: Record) -> list[Record]:
    return [s for s in design.get("supports", []) if s["actuation"]["drive"]]


def driveshaft_joints(design: Record, sid: str) -> tuple[list[float], list[float]]:
    """(inner joint, outer joint) of the driveshaft of support sid, mm (see the module doc)."""
    from hiraku.solvers.k2_vehicle.model import hardpoints_of

    hp = hardpoints_of(design, sid)
    mid = (np.asarray(hp["lca_front"], float) + np.asarray(hp["lca_rear"], float)) / 2.0
    wc = [float(v) for v in hp["wheel_center"]]
    return [float(mid[0]), float(mid[1]), wc[2]], wc


def tube_mass(length_mm: float, od_mm: float, wall_mm: float, rho: float) -> float:
    area = math.pi / 4.0 * (od_mm ** 2 - (od_mm - 2.0 * wall_mm) ** 2)
    return rho * area * length_mm * 1e-9


def cross_member_bottom(lay: Layout, station: str) -> float:
    """Lowest z of the member running across at a frame station."""
    x = lay.station_x(station)
    nodes = {n["id"]: n["xyz_mm"] for n in lay.design["nodes"]}
    out = []
    for m in lay.design["members"]:
        a, b = nodes[m["node_a"]], nodes[m["node_b"]]
        if abs(a[0] - x) < 1e-6 and abs(b[0] - x) < 1e-6 and abs(a[1] - b[1]) > 1e-6:
            dims = lay.design["sections"][m["section"]]["dims_mm"]
            out.append(min(a[2], b[2]) - dims.get("h", dims.get("d", 0.0)) / 2.0)
    if not out:
        raise ValueError(f"no member runs across at station {station}")
    return float(min(out))


def place(lay: Layout, total_mass: float, station: str, shaft: tuple[float, float], rho: float) -> Record:
    """Put the motor, the reducer and the driveshafts in; returns their masses and the turning inertias
    (kg*m^2): {"motor", "reducer", "shafts", "rotor_inertia", "gear_inertia"}."""
    od, wall = shaft
    sups = driven(lay.design)
    if not sups:
        raise ValueError("no driven wheel to drive")
    joints = {s["id"]: driveshaft_joints(lay.design, s["id"]) for s in sups}
    shafts = {}
    for s in sups:
        a, b = joints[s["id"]]
        length = float(np.linalg.norm(np.subtract(b, a)))
        shafts[s["id"]] = tube_mass(length, od, wall, rho) + 2 * CV_JOINT_KG
    rest = total_mass - sum(shafts.values())
    if rest <= 0:
        raise ValueError(f"the driveshafts ({sum(shafts.values()):.1f} kg) weigh more than the drive ({total_mass:.1f} kg)")
    m_red = REDUCER_SHARE * rest
    m_mot = rest - m_red
    inner = np.array([j[0] for j in joints.values()])
    axis_z = float(inner[:, 2].mean())
    axis_x = float(inner[:, 0].mean())
    width = float(inner[:, 1].max() - inner[:, 1].min()) if len(inner) > 1 else 2 * abs(float(inner[0, 1]))
    height = 2.0 * (cross_member_bottom(lay, station) - REDUCER_BELOW_MM - axis_z)
    if height <= 0:
        raise ValueError("the driveshafts' line is above the cross member at the axle: no room for the reducer")
    red = lay.put(Item("reducer", "減速機と差動", "reducer", (REDUCER_LENGTH_MM, width, height), m_red),
                  x=axis_x, y=float(inner[:, 1].mean()) if len(inner) > 1 else 0.0, z=axis_z, carried_by=lay.at(station))
    lay.put(Item("motor", "モーター", "motor", MOTOR_SIZE_MM, m_mot),
            x=rear_at(red["box"]["center_mm"][0] + REDUCER_LENGTH_MM / 2.0), y=0.0, z=axis_z, carried_by=lay.at(station))
    for s in sups:
        a, b = joints[s["id"]]
        corner = "左" if s["position_mm"][1] > 0 else "右"
        put_link(lay, Item(f"driveshaft_{s['id']}", f"{corner}のドライブシャフト", "driveshaft", (0, 0, 0), shafts[s["id"]]),
                 a, b, od / 2.0, carried_by=None)
    return {"motor": m_mot, "reducer": m_red, "shafts": shafts,
            "rotor_inertia": ROTOR_SHARE * m_mot * (ROTOR_RADIUS_MM / 1000.0) ** 2 / 2.0,
            "gear_inertia": GEAR_SHARE * m_red * (GEAR_RADIUS_MM / 1000.0) ** 2 / 2.0}

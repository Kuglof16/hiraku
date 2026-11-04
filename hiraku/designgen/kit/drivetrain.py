"""The drive from the motor to the driven wheels: motor -> reducer -> a driveshaft to each
driven wheel.

    info = drivetrain.place(lay, total_mass=25.0, station="B", shaft=(30.0, 5.0), rho=7850.0)

Where the parts go (each rule from the K2 hard points of the driven supports and the frame):
- each driveshaft's outer joint is its wheel's centre (hard point wheel_center) and its inner
  joint is at the y of the lower arm's inner pivots (the middle of lca_front and lca_rear), at
  that middle's x and at the wheel centre's height in the design position, less the design
  variable driveshaft_inner_drop when the design has it (the generator sets it with
  drop_to_clear, so that the shafts pass under the rails at full bump);
- the reducer sits between the two inner joints (its side faces at the joints, its output on
  their line), its top REDUCER_BELOW_MM below the frame's cross member at the axle station;
- the motor sits ahead of the reducer, its back face on the reducer's front face, on the same
  height (motor axis parallel to the outputs: assumption).

Masses: each driveshaft is a steel tube (outer diameter and wall given) from joint to joint plus
two joints of CV_JOINT_KG; the motor and the reducer share the given total less the shafts'
reference mass (`reference_shafts`: the shafts the total was split with; the caller passes those of
its first version, so the motor does not get lighter when a wider track makes the shafts longer),
REDUCER_SHARE to the reducer and the rest to the motor. With the reference equal to the shafts'
own mass the parts add up to the total. The driveshafts have no carried_by: they ride with their
wheel (the outer joint moves with it).

Without driveshafts (place(..., shafts=False): a design missing a link of the drive on purpose)
the motor and the reducer are placed as with them and the shafts' mass is added to the reducer,
so the total and the turning inertias stay the same; nothing carries the torque on to the wheels.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from hiraku.designgen.kit.links import put_link
from hiraku.designgen.kit.package import Item, Layout, rear_at

Record = dict[str, Any]

DROP_VARIABLE = "driveshaft_inner_drop"  # design variable (mm): the inner joints below the wheel centres
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


def inner_drop(design: Record) -> float:
    """How far (mm) the inner joints sit below the wheel centres: the design variable
    driveshaft_inner_drop (written by the generator), else 0."""
    q = (design.get("variables") or {}).get(DROP_VARIABLE)
    return float(q["value"]) if q else 0.0


def driveshaft_joints(design: Record, sid: str, drop: float | None = None) -> tuple[list[float], list[float]]:
    """(inner joint, outer joint) of the driveshaft of support sid, mm (see the module doc); drop:
    the inner joint this far below the wheel centre (None: inner_drop(design))."""
    from hiraku.solvers.k2_vehicle.model import hardpoints_of

    hp = hardpoints_of(design, sid)
    mid = (np.asarray(hp["lca_front"], float) + np.asarray(hp["lca_rear"], float)) / 2.0
    wc = [float(v) for v in hp["wheel_center"]]
    dz = inner_drop(design) if drop is None else drop
    return [float(mid[0]), float(mid[1]), wc[2] - dz], wc


def drop_to_clear(design: Record, rail_y: float, rail_bottom: float, shaft_radius: float, gap: float) -> float:
    """The least drop (mm, at least 0) of the inner joints below the wheel centres that keeps each driven
    wheel's driveshaft `gap` below the rail bottom where it passes under the rail's outer face
    (|y| = rail_y) at full bump (the wheel half its suspension travel up, as K4 sweeps it; the inner joint
    stays). Straight shafts; the rail runs past the axle along x."""
    out = 0.0
    for s in driven(design):
        (xi, yi, zi), (xo, yo, zo) = driveshaft_joints(design, s["id"], drop=0.0)
        bump = float((s.get("suspension") or {}).get("travel_mm") or 0.0) / 2.0
        f = (abs(rail_y) - abs(yi)) / (abs(yo) - abs(yi))
        if not 0.0 < f < 1.0:
            continue
        # z at the rail face: (zi - d) + (zo + bump - zi + d) f <= rail_bottom - r - gap
        need = ((zi + (zo + bump - zi) * f) + shaft_radius + gap - rail_bottom) / (1.0 - f)
        out = max(out, need)
    return float(out)


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


def shaft_masses(design: Record, shaft: tuple[float, float], rho: float) -> dict[str, float]:
    """{driven support id: mass of its driveshaft (kg)}: the tube from joint to joint and two joints."""
    od, wall = shaft
    out = {}
    for s in driven(design):
        a, b = driveshaft_joints(design, s["id"])
        out[s["id"]] = tube_mass(float(np.linalg.norm(np.subtract(b, a))), od, wall, rho) + 2 * CV_JOINT_KG
    return out


def place(lay: Layout, total_mass: float, station: str, shaft: tuple[float, float], rho: float,
          shafts: bool = True, reference_shafts: float | None = None) -> Record:
    """Put the motor, the reducer and (with shafts) the driveshafts in; returns their masses and the
    turning inertias (kg*m^2): {"motor", "reducer", "shafts" (those placed), "rotor_inertia", "gear_inertia"}.
    reference_shafts: the shafts' mass the total was split with (None: these shafts' own mass).
    The inner joints' drop is read from lay.design's variables (inner_drop)."""
    od, wall = shaft
    sups = driven(lay.design)
    if not sups:
        raise ValueError("no driven wheel to drive")
    joints = {s["id"]: driveshaft_joints(lay.design, s["id"]) for s in sups}
    shaft_mass = shaft_masses(lay.design, shaft, rho)
    ref = sum(shaft_mass.values()) if reference_shafts is None else reference_shafts
    rest = total_mass - ref
    if rest <= 0:
        raise ValueError(f"the driveshafts ({ref:.1f} kg) weigh more than the drive ({total_mass:.1f} kg)")
    m_red = REDUCER_SHARE * rest
    m_mot = rest - m_red
    inner = np.array([j[0] for j in joints.values()])
    axis_z = float(inner[:, 2].mean())
    axis_x = float(inner[:, 0].mean())
    width = float(inner[:, 1].max() - inner[:, 1].min()) if len(inner) > 1 else 2 * abs(float(inner[0, 1]))
    height = 2.0 * (cross_member_bottom(lay, station) - REDUCER_BELOW_MM - axis_z)
    if height <= 0:
        raise ValueError("the driveshafts' line is above the cross member at the axle: no room for the reducer")
    m_case = m_red if shafts else m_red + sum(shaft_mass.values())
    red = lay.put(Item("reducer", "減速機と差動", "reducer", (REDUCER_LENGTH_MM, width, height), m_case),
                  x=axis_x, y=float(inner[:, 1].mean()) if len(inner) > 1 else 0.0, z=axis_z, carried_by=lay.at(station))
    lay.put(Item("motor", "モーター", "motor", MOTOR_SIZE_MM, m_mot),
            x=rear_at(red["box"]["center_mm"][0] + REDUCER_LENGTH_MM / 2.0), y=0.0, z=axis_z, carried_by=lay.at(station))
    for s in sups if shafts else []:
        a, b = joints[s["id"]]
        corner = "左" if s["position_mm"][1] > 0 else "右"
        put_link(lay, Item(f"driveshaft_{s['id']}", f"{corner}のドライブシャフト", "driveshaft", (0, 0, 0),
                           shaft_mass[s["id"]]), a, b, od / 2.0, carried_by=None)
    return {"motor": m_mot, "reducer": m_case, "shafts": shaft_mass if shafts else {},
            "rotor_inertia": ROTOR_SHARE * m_mot * (ROTOR_RADIUS_MM / 1000.0) ** 2 / 2.0,
            "gear_inertia": GEAR_SHARE * m_red * (GEAR_RADIUS_MM / 1000.0) ** 2 / 2.0}

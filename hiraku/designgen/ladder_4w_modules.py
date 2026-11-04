"""ladder_4w split into modules (docs/contract.md, モジュールに分けた設計): which module each part kind, each member and
each revisable variable and choice belongs to. The frame's nodes are welded (the generator joins the tubes by
welding). A part kind or member name not in the tables makes split refuse, so that a new one is placed on purpose."""

from __future__ import annotations

from . import modules as M

ORDER = ("package", "frame", "suspension", "steering", "occupant", "powertrain", "body")

KIND_MODULE = {
    # frame: the welded pieces and the frame's fittings (brackets and mounts welded to it)
    "frame": "frame", "gusset": "frame", "bracket": "frame",
    # suspension: the corners (the wheel, its upright, the arms and damper, the brake on the upright)
    "wheel": "suspension", "knuckle": "suspension", "suspension": "suspension", "brake_caliper": "suspension",
    "upper_arm": "suspension", "lower_arm": "suspension", "damper": "suspension", "spring": "suspension",
    "link": "suspension", "ball_joint": "suspension",  # kit/arms.py: the arms' legs and the ball joints
    # steering and the pedals
    "steering": "steering", "steering_column": "steering", "steering_shaft": "steering", "steering_rack": "steering",
    "tie_rod": "steering", "accelerator_pedal": "steering", "brake_pedal": "steering",
    "brake_master_cylinder": "steering", "pedal_stand": "steering",
    # occupant: the seats and what keeps the people in
    "seat": "occupant", "seat_belt": "occupant", "side_net": "occupant", "net_clip": "occupant", "door": "occupant",
    "door_hinge": "occupant", "door_latch": "occupant", "occupant": "occupant", "rollbar": "occupant",
    "grab_handle": "occupant",
    # powertrain
    "motor": "powertrain", "reducer": "powertrain", "driveshaft": "powertrain", "battery": "powertrain",
    "controller": "powertrain", "electrical": "powertrain", "switch": "powertrain", "wiring": "powertrain",
    "fuse": "powertrain", "contactor": "powertrain", "breaker": "powertrain",
    # the lamps sit on the front body and behind the frame's rear end (kit/safety.py)
    "lamp": "body", "headlamp": "body", "taillamp": "body", "stoplamp": "body", "turn_signal": "body",
    # body
    "body": "body", "fender": "body", "cargo": "body",
}
# by id, before the kind: the rear toe links (kind tie_rod) are the rear corners'; the pedals' stands are the pedals'
ID_MODULE = (("toe_link_", "suspension"), ("stand_pedal", "steering"), ("bracket_column", "frame"))

MEMBER_MODULE = (("rail_", "frame"), ("cross_", "frame"), ("lower_", "frame"), ("tower_", "frame"), ("dash_", "frame"),
                 ("pedal_beam", "steering"), ("rollbar_", "occupant"), ("rops_", "occupant"), ("side_", "occupant"),
                 ("grab_", "occupant"))

VARIABLE_MODULE = {
    **dict.fromkeys(("wheelbase", "track", "rail_spacing", "rail_height", "front_overhang", "rear_overhang", "seat_x",
                     "seat_y", "steering_x", "battery_x", "pedal_x", "bulkhead_dx"), "package"),
    **dict.fromkeys(("rail_b", "rail_h", "rail_t", "cross_b", "cross_h", "cross_t", "tower_b", "tower_t", "lower_b",
                     "lower_t", "lower_cross", "gusset_leg", "gusset_thickness", "cross_members_through"), "frame"),
    **dict.fromkeys(("wheel_radius", "wheel_width", "front_spring_rate", "rear_spring_rate", "suspension_travel"),
                    "suspension"),
    **dict.fromkeys(("steering_column_angle", "steering_wheel_height", "max_steer_angle", "pedal_mount"), "steering"),
    **dict.fromkeys(("seat_height", "seat_slide", "seat_depth", "rollbar", "rollbar_height", "rollbar_width",
                     "rollbar_offset", "rollbar_square", "rollbar_d", "rollbar_b", "rollbar_t", "mount_seats",
                     "side_bars", "side_bar_count", "side_bar_low", "side_bar_high", "side_bar_d", "side_bar_t",
                     "side_net", "side_net_wire_d", "side_net_pitch", "rollbar_foot_b", "rollbar_foot_h",
                     "rollbar_foot_t", "rollbar_stays", "rollbar_stay_d", "rollbar_stay_t", "rollbar_front_posts",
                     "side_opening", "side_bar_reach", "grab_bar", "grab_bar_d", "grab_bar_t", "grab_bar_gap"),
                    "occupant"),
    **dict.fromkeys(("motor_peak_power", "motor_peak_torque", "motor_max_speed", "gear_ratio", "battery_capacity",
                     "driveshafts", "battery_split", "mount_electrics", "mount_drive"), "powertrain"),
    **dict.fromkeys(("bumper_stroke", "crush_stiffness", "lamps", "mount_floor", "fenders", "fender_front_deg",
                     "fender_rear_deg", "fender_width", "fender_gap", "floor_t", "deck_t", "bulkhead_t"), "body"),
}
CHOICE_MODULE = {"joints": "frame", "part_mounts": "frame", "pedal_mount": "steering", "side_guard": "occupant",
                 "rops": "occupant", "side_opening": "occupant", "grab_bar": "occupant", "fenders": "body"}


def part_module(part: dict) -> str:
    for prefix, mid in ID_MODULE:
        if part["id"].startswith(prefix):
            return mid
    mid = KIND_MODULE.get(part["kind"])
    if mid is None:
        raise M.ModuleError(f"part {part['id']}: kind {part['kind']!r} has no module in ladder_4w_modules.KIND_MODULE")
    return mid


def member_module(member: dict) -> str:
    for prefix, mid in MEMBER_MODULE:
        if member["id"].startswith(prefix):
            return mid
    raise M.ModuleError(f"member {member['id']} has no module in ladder_4w_modules.MEMBER_MODULE")


def split(design: dict) -> dict:
    return M.split(design, part_module, member_module, VARIABLE_MODULE, CHOICE_MODULE, ORDER, joint="weld")

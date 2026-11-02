"""Wheels and their suspension from the wheelbase, track, wheel size and suspension type.

    sup = four_wheels(wheelbase=1700, track=1100, radius=300, width=200,
                      front=Suspension("spring", 20, 1.2, 150), rear=Suspension("spring", 20, 1.2, 150))

Supports are named FL, FR, RL, RR, the front axle at x = 0 and the rear at
x = -wheelbase (the contract's origin), each tied to the frame node of its axle
station on its side (L<front_station>, ...). Suspension types are those the K2
vehicle model reads: rigid, spring (wheel rate, damping, travel) and kc_table
(a kinematics-and-compliance table, a path or 'builtin:double_wishbone').
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Record = dict[str, Any]
SUSPENSION_TYPES = ("rigid", "spring", "kc_table")


@dataclass(frozen=True)
class Suspension:
    type: str = "spring"
    stiffness_N_per_mm: float | None = None  # wheel rate
    damping_Ns_per_mm: float | None = None
    travel_mm: float | None = None  # bump to rebound
    kc_table: str | None = None

    def record(self) -> Record:
        if self.type not in SUSPENSION_TYPES:
            raise ValueError(f"suspension type {self.type!r}; use one of {', '.join(SUSPENSION_TYPES)}")
        if self.type in ("spring", "kc_table") and not self.stiffness_N_per_mm:
            raise ValueError(f"a {self.type} suspension needs its wheel rate (stiffness_N_per_mm)")
        if self.type == "kc_table" and not self.kc_table:
            raise ValueError("a kc_table suspension needs kc_table (a table path or 'builtin:double_wishbone')")
        out: Record = {"type": self.type}
        for k in ("stiffness_N_per_mm", "damping_Ns_per_mm", "travel_mm", "kc_table"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        return out


def four_wheels(wheelbase: float, track: float, radius: float, width: float, front: Suspension, rear: Suspension,
                front_station: str = "A", rear_station: str = "B", steer: str = "front", drive: str = "rear") -> list[Record]:
    """Four wheel supports; steer and drive are "front", "rear" or "all"."""
    for name, v in (("steer", steer), ("drive", drive)):
        if v not in ("front", "rear", "all"):
            raise ValueError(f"{name} must be front, rear or all, got {v!r}")

    def wheel(sid: str, x: float, y: float, is_front: bool) -> Record:
        susp = front if is_front else rear
        axle = "front" if is_front else "rear"
        return {
            "id": sid, "kind": "wheel", "position_mm": [x, y, radius], "radius_mm": radius, "width_mm": width,
            "suspension": susp.record(),
            "actuation": {"drive": drive in (axle, "all"), "brake": True, "steer": steer in (axle, "all")},
            "frame_node": ("L" if y > 0 else "R") + (front_station if is_front else rear_station),
        }

    return [wheel("FL", 0.0, track / 2, True), wheel("FR", 0.0, -track / 2, True),
            wheel("RL", -wheelbase, track / 2, False), wheel("RR", -wheelbase, -track / 2, False)]


def corner_name(support: Record, supports: list[Record]) -> str:
    """前左, 後右, ... for a support among the others (front half by x)."""
    xs = [s["position_mm"][0] for s in supports]
    mid = (max(xs) + min(xs)) / 2.0
    x, y, _ = support["position_mm"]
    return ("前" if x > mid else "後") + ("左" if y > 0 else "右")

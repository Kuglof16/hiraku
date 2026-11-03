"""K4: geometry, interference and people (oriented boxes and the separating axis test, and
capsules for long thin parts, in numpy)."""

from .assembly import disassemble, steps
from .capsule import Capsule, capsule_box_overlap, capsule_overlap
from .model import bodies, corners, interferences, shape_overlap
from .motion import steer_room, sweep
from .obb import OBB, from_box, from_member, overlap
from .people import AIST, fit, knee_space

__all__ = ["AIST", "OBB", "Capsule", "bodies", "capsule_box_overlap", "capsule_overlap", "corners", "disassemble", "fit",
           "from_box", "from_member", "interferences", "knee_space", "overlap", "shape_overlap", "steer_room", "steps",
           "sweep"]

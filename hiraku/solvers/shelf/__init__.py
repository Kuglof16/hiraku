"""The built-in analysis tools (the shelf, screen A4)."""

from .assembly import Assembly
from .bed_tools import ActuatorLinkage, BedStatic
from .crash import Crash
from .fatigue import Fatigue
from .frame_shell import FrameShell
from .frame_static import FrameStatic
from .k3_tools import DriveEnergy, Thermal
from .modal import Modal
from .step_climb import StepClimb

BUILTIN_TOOLS = (FrameStatic, FrameShell, StepClimb, DriveEnergy, Thermal, Crash, Modal, Fatigue, Assembly, BedStatic,
                 ActuatorLinkage)

__all__ = ["BUILTIN_TOOLS", "ActuatorLinkage", "Assembly", "BedStatic", "Crash", "DriveEnergy", "Fatigue", "FrameShell", "FrameStatic", "Modal",
           "StepClimb", "Thermal"]

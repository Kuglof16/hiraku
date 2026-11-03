"""The built-in analysis tools (the shelf, screen A4)."""

from .assembly import Assembly
from .bed_tools import ActuatorLinkage, BedStatic
from .bed_use import ActuatorDuty, UseFatigue
from .crash import Crash
from .fatigue import Fatigue
from .frame_shell import FrameShell
from .frame_static import FrameStatic
from .frame_tubes import FrameTubes
from .k3_tools import DriveEnergy, Thermal
from .modal import Modal
from .step_climb import StepClimb

BUILTIN_TOOLS = (FrameStatic, FrameShell, FrameTubes, StepClimb, DriveEnergy, Thermal, Crash, Modal, Fatigue, Assembly,
                 BedStatic, ActuatorLinkage, UseFatigue, ActuatorDuty)

__all__ = ["BUILTIN_TOOLS", "ActuatorDuty", "ActuatorLinkage", "Assembly", "BedStatic", "Crash", "DriveEnergy", "Fatigue",
           "FrameShell", "FrameStatic", "FrameTubes", "Modal", "StepClimb", "Thermal", "UseFatigue"]

"""The built-in analysis tools (the shelf, screen A4)."""

from .assembly import Assembly
from .crash import Crash
from .fatigue import Fatigue
from .frame_static import FrameStatic
from .k3_tools import DriveEnergy, Thermal
from .modal import Modal
from .step_climb import StepClimb

BUILTIN_TOOLS = (FrameStatic, StepClimb, DriveEnergy, Thermal, Crash, Modal, Fatigue, Assembly)

__all__ = ["BUILTIN_TOOLS", "Assembly", "Crash", "DriveEnergy", "Fatigue", "FrameStatic", "Modal", "StepClimb", "Thermal"]

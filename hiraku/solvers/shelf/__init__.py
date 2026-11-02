"""The built-in analysis tools (the shelf, screen A4)."""

from .crash import Crash
from .frame_static import FrameStatic
from .k3_tools import DriveEnergy, Thermal
from .modal import Modal
from .step_climb import StepClimb

BUILTIN_TOOLS = (FrameStatic, StepClimb, DriveEnergy, Thermal, Crash, Modal)

__all__ = ["BUILTIN_TOOLS", "Crash", "DriveEnergy", "FrameStatic", "Modal", "StepClimb", "Thermal"]

"""The built-in analysis tools (the shelf, screen A4)."""

from .frame_static import FrameStatic
from .k3_tools import DriveEnergy, Thermal
from .step_climb import StepClimb

BUILTIN_TOOLS = (FrameStatic, StepClimb, DriveEnergy, Thermal)

__all__ = ["BUILTIN_TOOLS", "DriveEnergy", "FrameStatic", "StepClimb", "Thermal"]

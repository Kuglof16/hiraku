"""The built-in analysis tools (the shelf, screen A4)."""

from .frame_static import FrameStatic
from .step_climb import StepClimb

BUILTIN_TOOLS = (FrameStatic, StepClimb)

__all__ = ["BUILTIN_TOOLS", "FrameStatic", "StepClimb"]

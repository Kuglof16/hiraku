"""The built-in analysis tools (the shelf, screen A4)."""

from .assembly import Assembly
from .bed_tools import ActuatorLinkage, BedStatic
from .bed_use import ActuatorDuty, UseFatigue
from .crash import Crash
from .entry import EntryOpening
from .fatigue import Fatigue
from .frame_shell import FrameShell
from .frame_static import FrameStatic
from .frame_tubes import FrameTubes
from .k3_tools import DriveEnergy, Thermal
from .load_path import LoadPath
from .modal import Modal
from .modal_shell import ModalShell
from .spray import SprayReach
from .step_climb import StepClimb

BUILTIN_TOOLS = (FrameStatic, FrameShell, FrameTubes, StepClimb, DriveEnergy, Thermal, Crash, Modal, ModalShell, Fatigue, Assembly,
                 BedStatic, ActuatorLinkage, UseFatigue, ActuatorDuty, LoadPath, SprayReach, EntryOpening)

__all__ = ["BUILTIN_TOOLS", "ActuatorDuty", "ActuatorLinkage", "Assembly", "BedStatic", "Crash", "DriveEnergy", "EntryOpening", "Fatigue",
           "FrameShell", "FrameStatic", "FrameTubes", "LoadPath", "Modal", "ModalShell", "SprayReach", "StepClimb", "Thermal", "UseFatigue"]

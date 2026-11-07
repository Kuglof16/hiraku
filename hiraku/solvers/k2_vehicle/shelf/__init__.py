"""K2 scenarios as contract AnalysisTools (hiraku.contract.protocols.AnalysisTool).

Each tool: spec() -> tool_spec, check() -> tool_check list (the seven pre-run
checks plus the tool's own known answers), run(design, load_case,
predictions, params) -> analysis record without analysis_id.

Load-case params: each tool declares the ones it reads in `params` (name,
contract unit, default in that unit, design variables that stand in when the
load case does not give it) and reads no others; describe()["params"] gives
them as records. params.loading (empty, driver, two, two_cargo; default
two_cargo) chooses the occupant and cargo parts on board for every tool. A
load-case param the tool does not read is named in the record's effects; the
values K2 assumed because neither the load case nor the design gave them are
the record's assumptions.
Tool options in `params` (not the load case): dt (s), record_hz.

Predictions: `pairs` names, for a result quantity, a K0 formula output that
predicts it in the same unit, and which formula inputs come straight from the
load-case params; spec()["formulas"] lists those formula ids.

base.py holds K2Tool and the shared params and helpers; each scenario has its own module.
"""

from .base import CD, FRONT_AREA, LOADING, MOTOR, MU, RHO, SIDE_AREA, SIDE_CP_HEIGHT, TIPPED_ROLL, K2Tool, P, Pair
from .braking import BrakingTool, brake_stop
from .crash_beam import CrashBeamTool
from .crosswind import CrosswindTool, wind_ramp
from .kc_bench import KcBenchTool
from .lane_change import DLC_LANE_WIDTH, DLC_OFFSET, DLC_SECTIONS, LaneChangeTool, dlc_path, dlc_scene, path_run
from .launch import LaunchTool, launch_run
from .offroad import OffroadRollTool
from .rollover import RolloverTiltTool
from .rollover_beam import RolloverBeamTool
from .step import StepClimbTool, step_push
from .tilt import TiltTool, tilt_closed_form
from .turning import SteadyCircleTool, TurningCircleTool

# rollover_tilt is planned_only (base.K2Tool): checked in a run with S1, named only by the plan of S1's drive record.
# rollover_beam (stage 3) is R4's only tool: the rollover with the frame as beams
TOOLS = {t.name: t for t in (TiltTool(), CrosswindTool(), BrakingTool(), LaunchTool(), LaneChangeTool(),
                             OffroadRollTool(), StepClimbTool(), KcBenchTool(), TurningCircleTool(), SteadyCircleTool(),
                             CrashBeamTool(), RolloverTiltTool(), RolloverBeamTool())}

__all__ = ["TOOLS", "K2Tool", "P", "Pair", "LOADING", "MU", "RHO", "CD", "FRONT_AREA", "SIDE_AREA", "SIDE_CP_HEIGHT",
           "MOTOR", "TIPPED_ROLL", "TiltTool", "CrosswindTool", "BrakingTool", "LaunchTool", "LaneChangeTool",
           "OffroadRollTool", "StepClimbTool", "KcBenchTool", "TurningCircleTool", "SteadyCircleTool", "RolloverTiltTool",
           "RolloverBeamTool", "tilt_closed_form", "wind_ramp", "brake_stop", "launch_run",
           "path_run", "dlc_path", "dlc_scene", "step_push", "DLC_SECTIONS", "DLC_OFFSET", "DLC_LANE_WIDTH"]

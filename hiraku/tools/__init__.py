"""Agent-facing tools: plain functions (functions.py), the rules the hooks
enforce (rules.py) and a thin MCP wrapper (mcp.py, imports the Agent SDK)."""

from hiraku.tools.attribution import Attribution
from hiraku.tools.context import RunContext, ToolError
from hiraku.tools.functions import FUNCTIONS
from hiraku.tools.shelf import SolversMissing, check_shelf, check_tool, load_designgen, load_solvers

__all__ = [
    "FUNCTIONS",
    "Attribution",
    "RunContext",
    "SolversMissing",
    "ToolError",
    "check_shelf",
    "check_tool",
    "load_designgen",
    "load_solvers",
]

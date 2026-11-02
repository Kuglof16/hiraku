"""K2: 3D vehicle physics, solved directly in time with numpy.

Sprung body (6 DOF) + per-corner travel along K&C paths + wheel spin + tyre
deflection states + road. No physics-engine joints. See sim.py for the
equations, checks.py for the checks that must pass before any scenario runs,
shelf.py for the scenarios as analysis tools.

Importing this package computes nothing; tools() builds the tool objects and
their checks run only when check() or spec() is called.
"""


def tools() -> list:
    """The K2 analysis tools (hiraku.contract.protocols.AnalysisTool), for hiraku.solvers.builtin_tools()."""
    from .shelf import TOOLS

    return list(TOOLS.values())

"""Tools made during a run (N13): code Claude writes in the AnalysisTool shape, checked on
its own known answers in a separate process before it may be used.

    from hiraku.solvers import toolmaker
    made = toolmaker.make_tool(code)          # Made: spec, checks, usable, problems, tool
    shelf = toolmaker.RunShelf(run_dir, emit) # the run's own shelf, kept in <run_dir>/tools/
    shelf.make(code); shelf.tools()           # like hiraku.solvers.builtin_tools()

What the code may import and touch: guard.py. Importing this package loads
nothing heavy (the tool's process imports it too).
"""

from __future__ import annotations


def make_tool(code: str, **kwargs):
    from .make import make_tool as f
    return f(code, **kwargs)


def __getattr__(name: str):
    if name in ("Made", "RunShelf", "SandboxedTool", "check_passes"):
        from . import make
        return getattr(make, name)
    raise AttributeError(name)


__all__ = ["Made", "RunShelf", "SandboxedTool", "check_passes", "make_tool"]

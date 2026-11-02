"""What a tool written during a run may use, checked before it runs and enforced while it runs.

The tool's code may import only ALLOWED_MODULES (and their submodules, but
not DENIED_MODULES); it has no open/exec/eval/compile/__import__/getattr and
the like, and may not touch attributes named __x__ (but __init__) or the
numpy and scipy names that read or write files. It runs in its own process
(hiraku.solvers.toolmaker.runner) with a time limit, in an empty working
folder and without ANTHROPIC_API_KEY in its environment.

This is a guard against mistakes in code written in good faith, not a
sandbox against hostile code: Python cannot be fully closed from the inside.
"""

from __future__ import annotations

import ast
import builtins
from typing import Any

ALLOWED_MODULES = (
    "__future__", "math", "cmath", "statistics", "fractions", "decimal", "numbers", "functools", "itertools",
    "operator", "collections", "dataclasses", "typing", "enum", "abc", "copy", "re", "json",
    "numpy",
    "scipy.optimize", "scipy.integrate", "scipy.linalg", "scipy.interpolate", "scipy.special", "scipy.signal",
    "scipy.stats", "scipy.sparse", "scipy.spatial",
    # the HIRAKU bases a tool may build on: hand calculations, the frame, geometry, the
    # shelf helpers (BuiltinTool, check_record, param_value) and the contract
    "hiraku.solvers.k0_handcalc", "hiraku.solvers.k1_frame", "hiraku.solvers.k4_geometry",
    "hiraku.solvers.shelf.base", "hiraku.contract",
)
DENIED_MODULES = ("numpy.lib.npyio", "numpy.ctypeslib", "numpy.f2py", "numpy.distutils", "numpy.testing")

# builtins the tool's code does not get
DENIED_BUILTINS = ("open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr",
                   "setattr", "delattr", "input", "breakpoint", "help", "exit", "quit", "memoryview")
# attribute names it may not use: reading and writing files (numpy, scipy, paths), and
# modules reachable as attributes of other modules (the ways out of the guard)
DENIED_ATTRIBUTES = ("tofile", "fromfile", "dump", "npyio", "memmap", "savez", "savez_compressed", "savetxt",
                     "loadtxt", "genfromtxt", "DataSource", "_datasource", "ctypeslib", "f2py", "io", "open",
                     "write_text", "write_bytes", "read_text", "read_bytes", "unlink", "rmdir", "mkdir", "touch",
                     "rename", "chmod", "os", "sys", "subprocess", "shutil", "pathlib", "Path", "socket",
                     "importlib", "ctypes", "system", "popen", "modules", "builtins")
ALLOWED_DUNDERS = ("__init__",)
# numpy functions replaced in the tool's process so that they raise
NUMPY_FILE_FUNCTIONS = ("load", "save", "savez", "savez_compressed", "savetxt", "loadtxt", "genfromtxt", "fromfile",
                        "memmap")


def module_allowed(name: str) -> bool:
    if any(name == d or name.startswith(d + ".") for d in DENIED_MODULES):
        return False
    return any(name == a or name.startswith(a + ".") for a in ALLOWED_MODULES)


def allowed_text() -> str:
    return ", ".join(ALLOWED_MODULES[1:])


def _import_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    if node.level:
        return ["." * node.level + (node.module or "")]
    base = node.module or ""
    # from scipy import optimize: the module is scipy.optimize unless scipy itself is allowed
    if module_allowed(base):
        return [base]
    return [f"{base}.{a.name}" for a in node.names]


def static_problems(code: str) -> list[str]:
    """What is wrong with the code before it runs, in sentences for the author."""
    try:
        tree = ast.parse(code, filename="<tool>")
    except SyntaxError as e:
        return [f"コードが Python として読めません: {e.msg} ({e.lineno} 行目)"]
    out: list[str] = []

    def add(text: str) -> None:
        if text not in out:
            out.append(text)

    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for name in _import_names(node):
                if name.startswith("."):
                    add(f"相対の import ({name}) は使えません ({line} 行目)")
                elif not module_allowed(name):
                    add(f"import {name} は使えません ({line} 行目)。使えるのは {allowed_text()} です")
        elif isinstance(node, ast.Name) and node.id in DENIED_BUILTINS:
            add(f"{node.id} は使えません ({line} 行目): ファイル・コードの実行・名前での属性の出し入れはできません")
        elif isinstance(node, ast.Attribute):
            a = node.attr
            if a.startswith("__") and a.endswith("__") and a not in ALLOWED_DUNDERS:
                add(f"属性 {a} は使えません ({line} 行目)。__ で囲んだ名前は __init__ だけ使えます")
            elif a in DENIED_ATTRIBUTES:
                add(f"属性 {a} は使えません ({line} 行目): ファイルの読み書きや外への出口になります")
    return out


def safe_import(name: str, globals_: Any = None, locals_: Any = None, fromlist: Any = (), level: int = 0):
    """__import__ for the tool's code: only the allowed modules."""
    if level:
        raise ImportError("relative imports are not allowed in a tool")
    names = [name] if (not fromlist or module_allowed(name)) else [f"{name}.{f}" for f in fromlist]
    for n in names:
        if not module_allowed(n):
            raise ImportError(f"import {n} is not allowed in a tool (allowed: {allowed_text()})")
    return builtins.__import__(name, globals_, locals_, fromlist, level)


def tool_builtins() -> dict:
    """The builtins the tool's code sees."""
    out = {k: v for k, v in vars(builtins).items() if k not in DENIED_BUILTINS}
    out["__import__"] = safe_import
    return out


def lock_down() -> None:
    """In the tool's process: numpy's file functions raise, scipy.io cannot be loaded."""
    import sys

    import numpy as np

    def blocked(name):
        def f(*args, **kwargs):
            raise PermissionError(f"numpy.{name} reads or writes files, which a tool may not do")
        return f

    for name in NUMPY_FILE_FUNCTIONS:
        setattr(np, name, blocked(name))
    sys.modules["scipy.io"] = None  # importing it (also lazily, as scipy.io) raises ImportError

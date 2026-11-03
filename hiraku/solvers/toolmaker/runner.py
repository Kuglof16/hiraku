"""The process a tool written during a run lives in (started by hiraku.solvers.toolmaker.make).

Run as a script: python -I runner.py. stdin: ASCII JSON
{"repo", "code", "action": "check" | "run", "args": {design, load_case, predictions, params}}.
stdout: the last line is MARK + ASCII JSON {"ok", "error"?, "spec"?, "checks"?, "analysis"?, "printed"}.
What the tool's code may use is set by guard.py.
"""

import io
import json
import sys
import traceback
import types

MARK = "TOOLMAKER_RESULT "
PRINT_LIMIT = 2000  # characters of the tool's own print output kept (HIRAKU's choice, an assumption)


def _jsonable(x):
    try:
        import numpy as np
    except ImportError:  # pragma: no cover
        np = None
    if np is not None and isinstance(x, np.generic):
        return x.item()
    if np is not None and isinstance(x, np.ndarray):
        return x.tolist()
    raise TypeError(f"{type(x).__name__} is not JSON")


def _explain(e: BaseException, phase: str) -> str:
    lines = [f.lineno for f in traceback.extract_tb(e.__traceback__) if f.filename == "<tool>"]
    where = f" (tool.py の {lines[-1]} 行目)" if lines else ""
    return f"{phase} の途中で {type(e).__name__} が起きました: {e}{where}"


def find_tool(module):
    """The module's TOOL, else the one class defined in it that has spec, check and run."""
    tool = vars(module).get("TOOL")
    if tool is not None:
        return tool
    classes = [v for v in vars(module).values()
               if isinstance(v, type) and v.__module__ == module.__name__
               and all(callable(getattr(v, m, None)) for m in ("spec", "check", "run"))
               and not getattr(v, "__abstractmethods__", None)]
    if len(classes) != 1:
        found = ", ".join(c.__name__ for c in classes) or "なし"
        raise LookupError("道具が 1 つに決まりません (spec・check・run を持つクラス: " + found + ")。"
                          "TOOL = その道具のインスタンス を書いてください")
    return classes[0]()


def main() -> None:
    req = json.loads(sys.stdin.read())
    sys.path.insert(0, req["repo"])
    real_out = sys.stdout
    printed = io.StringIO()
    phase = "準備"
    try:
        from hiraku.solvers.toolmaker import guard
        guard.lock_down()
        module = types.ModuleType("<tool>")
        module.__dict__["__builtins__"] = guard.tool_builtins()
        sys.modules["<tool>"] = module  # dataclasses and typing look the module up
        sys.stdout = printed
        phase = "コードの読み込み"
        exec(compile(req["code"], "<tool>", "exec"), module.__dict__)
        tool = find_tool(module)
        if req["action"] == "check":
            phase = "check()"
            checks = list(tool.check())
            phase = "spec()"
            result = {"ok": True, "spec": tool.spec(), "checks": checks}
            if callable(getattr(tool, "describe", None)):  # load case params and predicts, as the shelf's tools give
                phase = "describe()"
                result["describe"] = tool.describe()
        else:
            a = req["args"]
            phase = "run()"
            result = {"ok": True, "analysis": tool.run(a["design"], a["load_case"], a["predictions"], a["params"])}
        text = json.dumps(result, default=_jsonable, ensure_ascii=True)
    except BaseException as e:  # SystemExit and KeyboardInterrupt from the tool are its failures too
        text = json.dumps({"ok": False, "error": _explain(e, phase)}, ensure_ascii=True)
    finally:
        sys.stdout = real_out
    out = json.loads(text)
    out["printed"] = printed.getvalue()[-PRINT_LIMIT:]
    sys.stdout.buffer.write((MARK + json.dumps(out, ensure_ascii=True) + "\n").encode("ascii"))
    sys.stdout.flush()


if __name__ == "__main__":
    main()

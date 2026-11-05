"""Run `python -m hiraku <args>` from this repository, wherever Claude Code starts the process.

The plugin's MCP server (mcpServers in .claude-plugin/plugin.json) and hooks (hooks/hooks.json) call this file with
the `python` on the person's PATH. That interpreter may lack HIRAKU's requirements (the mcp package above all), so
this file runs HIRAKU with the repository's own interpreter (choose_python):

1. the one named by the environment variable HIRAKU_PYTHON, if set;
2. else <repository>/.venv (Scripts/python.exe on Windows, bin/python elsewhere);
3. else, when the repository is a git worktree (its .git is a file "gitdir: ..."), the main checkout's .venv.

When the running interpreter is not that one, the same arguments run again under it as a child process with stdin,
stdout and stderr inherited (the MCP server speaks over stdio; os.exec* is not used, as on Windows it does not
replace the process), and this process exits with the child's exit code. When none is found and the running
interpreter cannot import mcp, one line on stderr says what to do, and the exit code is 1.

The repository is three folders above this file (plugins/hiraku/scripts), and it goes first on sys.path. So the
plugin must be loaded in place: `claude --plugin-dir <repository>/plugins/hiraku`, or a marketplace added from a
local path. Claude Code copies a plugin from any other source into its plugin cache, where the repository is not
above it (Claude Code docs, plugin loading reference, "In-place and copied plugins", read 2026-11-03).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ENV_PYTHON = "HIRAKU_PYTHON"
ENV_CHILD = "HIRAKU_CLI_CHILD"  # set in the child: it runs HIRAKU itself and never starts another child
NO_PYTHON = ("hiraku plugin: HIRAKU の Python が見つかりません。{repo} で python -m venv .venv を作り、"
             ".venv の pip で requirements.txt を入れるか、環境変数 HIRAKU_PYTHON に requirements の入った "
             "python のパスを入れてください")


def venv_python(root: Path, windows: bool | None = None) -> Path:
    """<root>/.venv's interpreter: Scripts/python.exe on Windows, bin/python elsewhere."""
    windows = os.name == "nt" if windows is None else windows
    return root / ".venv" / ("Scripts/python.exe" if windows else "bin/python")


def main_checkout(repo: Path) -> Path | None:
    """The main checkout of a git worktree (its .git is a file "gitdir: <main>/.git/worktrees/<name>"),
    or None when repo is not a worktree."""
    git = repo / ".git"
    if not git.is_file():
        return None
    try:
        line = git.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = Path(line.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        gitdir = (repo / gitdir).resolve()
    common = gitdir.parent.parent  # <main>/.git/worktrees/<name> -> <main>/.git
    try:
        named = (gitdir / "commondir").read_text(encoding="utf-8").strip()
        common = Path(named) if Path(named).is_absolute() else (gitdir / named).resolve()
    except OSError:
        pass
    return common.parent


def choose_python(repo: Path = REPO, env: dict | None = None, windows: bool | None = None) -> Path | None:
    """The interpreter HIRAKU runs with: HIRAKU_PYTHON, else <repo>/.venv, else the main checkout's .venv
    when repo is a git worktree; None when there is none."""
    env = os.environ if env is None else env
    if env.get(ENV_PYTHON):
        return Path(env[ENV_PYTHON])
    own = venv_python(repo, windows)
    if own.is_file():
        return own
    main = main_checkout(repo)
    if main is not None and venv_python(main, windows).is_file():
        return venv_python(main, windows)
    return None


def same_file(a: Path | str, b: Path | str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def say(text: str) -> None:
    """One line on stderr in UTF-8, as Claude Code reads it (on a Japanese Windows a pipe's default is cp932)."""
    raw = getattr(sys.stderr, "buffer", None)
    if raw is None:  # a stream without bytes under it (a test's StringIO)
        sys.stderr.write(text + "\n")
    else:
        raw.write((text + "\n").encode("utf-8"))
    sys.stderr.flush()


def relaunch(python: Path, args: list[str]) -> int:
    """Run python with args as a child process with stdin, stdout and stderr inherited; its exit code. The child
    runs in Python's UTF-8 mode (PYTHONUTF8=1, unless the person set it), so what it writes on stderr (an error's
    traceback) reaches Claude Code as UTF-8 too."""
    env = dict(os.environ, **{ENV_CHILD: "1"})
    env.setdefault("PYTHONUTF8", "1")
    return subprocess.call([str(python), *args], env=env)


def run_hiraku(args: list[str]) -> None:
    if not (REPO / "hiraku" / "__main__.py").is_file():
        sys.exit(f"hiraku plugin: the HIRAKU repository is not at {REPO}. Load the plugin in place: "
                 "claude --plugin-dir <repository>/plugins/hiraku")
    import runpy

    sys.path.insert(0, str(REPO))
    sys.argv = ["hiraku", *args]
    runpy.run_module("hiraku", run_name="__main__", alter_sys=True)


def main(argv: list[str]) -> int:
    args = argv[1:]
    if not os.environ.get(ENV_CHILD):
        python = choose_python()
        if python is not None and not same_file(python, sys.executable):
            if not python.is_file():
                say(f"hiraku plugin: {ENV_PYTHON} の {python} がありません")
                return 1
            return relaunch(python, [str(Path(__file__).resolve()), *args])
        if python is None and importlib.util.find_spec("mcp") is None:
            say(NO_PYTHON.format(repo=REPO))
            return 1
    run_hiraku(args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

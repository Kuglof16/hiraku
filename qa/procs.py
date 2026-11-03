"""Run a command with a time limit that also stops everything the command started.

subprocess.run's timeout stops only the process it started, not the processes that
one started in turn: a test worker stopped this way leaves its scripted run (a
`python -m hiraku run`) running and holding memory. (On Windows the venv's python.exe
is a launcher, and killing it does end the interpreter behind it.) run() stops the
whole tree (taskkill /T on Windows, the process group elsewhere) before it raises
subprocess.TimeoutExpired.

one_blas_thread() gives a child process one BLAS thread: numpy and scipy each bundle
OpenBLAS, which reserves commit memory for every thread it may start, so many test
workers and scripted runs at once ran the PC out of commit memory.
"""

from __future__ import annotations

import os
import signal
import subprocess

ONE_THREAD = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}


def one_blas_thread(env: dict | None = None) -> dict:
    """env (default os.environ) with OPENBLAS_NUM_THREADS and OMP_NUM_THREADS 1 where they are not set."""
    out = dict(os.environ if env is None else env)
    for key, value in ONE_THREAD.items():
        out.setdefault(key, value)
    return out


def kill_tree(proc: subprocess.Popen) -> None:
    """Stop proc and every process it started."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    proc.kill()


def run(cmd: list[str], *, timeout: float | None = None, input: str | None = None, **kw) -> subprocess.CompletedProcess:
    """subprocess.run(cmd, capture_output=True, timeout=timeout, **kw), stopping the whole tree on timeout."""
    if os.name != "nt":
        kw.setdefault("start_new_session", True)  # its own process group, so killpg reaches the children
    with subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else None, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **kw) as proc:
        try:
            out, err = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            try:
                out, err = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                out, err = None, None
            raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err) from None
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)

"""Run rigid-body scenarios in Rapier (Node) from Python, for QA only.

Rapier is a second opinion for scenes with no closed form (steps, rough road).
The right answers stay the closed forms and hand calculations. When K2 and
Rapier disagree, set a tolerance and find the cause; Rapier alone does not
decide which is right (it has its own habits: sub-stepping, solver order).
Tyre and suspension forces fed from the same Python code carry the same
errors into both, so a replay through Rapier checks only the rigid-body motion
and the contacts.

Needs Node and `npm install` in this folder (node_modules is not committed).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRIVER = HERE / "driver.mjs"
PACKAGE = HERE / "node_modules" / "@dimforge" / "rapier3d-compat"


def unavailable() -> str | None:
    """Why Rapier cannot run here, or None when it can."""
    if shutil.which("node") is None:
        return "node is not on PATH"
    if not PACKAGE.is_dir():
        return f"@dimforge/rapier3d-compat is not installed: run `npm install` in {HERE.name}/ (qa/rapier)"
    return None


def run(scenario: dict, timeout: float = 120.0) -> dict:
    """Run one scenario (see driver.mjs) and return the recorded trajectories."""
    reason = unavailable()
    if reason:
        raise RuntimeError(reason)
    proc = subprocess.run(["node", str(DRIVER)], cwd=HERE, input=json.dumps(scenario), capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"driver.mjs failed: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout)

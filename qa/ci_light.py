"""The light tests that GitHub Actions runs on every push to main (.github/workflows/light-tests.yml).

A sign that main's light tests pass, not a gate: the full suite before main (python -m qa.run_tests)
stays as it is. Left out: the solvers and tools that run analyses, the browser tests, the scripted
runs, the Rapier cross-check (Node) and the slow tests. About a minute and a half on the PC that
measured it (one process, without the tools' check cache).

    python -m qa.ci_light          # run them here, as the workflow does
    python -m qa.ci_light --list   # the module names
"""

from __future__ import annotations

import argparse
import sys
import unittest

LIGHT = [
    # contract
    "tests.contract.test_contract",
    "tests.contract.test_layers",
    "tests.contract.test_launcher",  # its cmd.exe test is skipped off Windows
    # intake
    "tests.intake.test_cli",
    "tests.intake.test_domains",
    "tests.intake.test_expression",
    "tests.intake.test_hearing",
    "tests.intake.test_knowledge",
    "tests.intake.test_pipeline",
    "tests.intake.test_request",
    "tests.intake.test_rulecheck",
    "tests.intake.test_tools",
    "tests.intake.test_video_runs",
    "tests.intake.test_web",
    # store
    "tests.store.test_run_store",
    # tools: the shelf, catalog, stages, MCP, report and export (not the tools' own analyses)
    "tests.tools.test_care_bed_inputs",
    "tests.tools.test_catalog",
    "tests.tools.test_cost",
    "tests.tools.test_export",
    "tests.tools.test_mcp",
    "tests.tools.test_report",
    "tests.tools.test_shelf_reuse",
    "tests.tools.test_stages",
    # agent: hooks, the loop's pieces and the plugin (not the first design's analyses)
    "tests.agent.test_builders_revise",
    "tests.agent.test_cli",
    "tests.agent.test_conversation",
    "tests.agent.test_hooks",
    "tests.agent.test_inbox",
    "tests.agent.test_mixes",
    "tests.agent.test_plugin",
    "tests.agent.test_runner",
    # verification: the record check, determinism comparison, judge's scan, hand calculations
    "tests.verification.test_check_run",
    "tests.verification.test_compare_runs",
    "tests.verification.test_first_look",
    "tests.verification.test_judge_scan",
    "tests.verification.test_k0_handcalc",
    "tests.verification.test_known_answers",
    "tests.verification.test_mesh_quality",
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="print the module names and stop")
    args = ap.parse_args(argv)
    if args.list:
        print("\n".join(LIGHT))
        return 0
    suite = unittest.defaultTestLoader.loadTestsFromNames(LIGHT)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())

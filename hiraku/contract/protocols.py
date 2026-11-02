"""Python-side signatures between departments.

Solvers (解析部) implement these; the MCP tools (設計部) call them and
never compute numbers themselves. All dicts are contract records
(see hiraku.schema.json); validate them at the boundary.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

Record = dict[str, Any]


class FormulaRegistry(Protocol):
    """K0 hand-calculation formulas (hiraku.solvers.k0_handcalc)."""

    def list_formulas(self) -> list[Record]:
        """Every formula as a `formula` record."""

    def evaluate(self, formula_id: str, inputs: dict[str, Record]) -> Record:
        """Evaluate one formula.

        inputs: {name: quantity}. Units are converted to the formula's units
        (a wrong dimension raises ValueError naming the input).
        Returns {"outputs": {name: quantity}, "terms": [term], "expression": str}.
        """


@runtime_checkable
class AnalysisTool(Protocol):
    """One CAE-stage tool on the shelf (frame_static, step_climb, tilt, ...)."""

    name: str

    def spec(self) -> Record:
        """The `tool_spec` record, with `checks` filled by check()."""

    def check(self) -> list[Record]:
        """Run the known-answer cases; return `tool_check` records."""

    def run(self, design: Record, load_case: Record, predictions: list[Record], params: dict[str, Any]) -> Record:
        """Run the analysis.

        Returns an `analysis` record without analysis_id (the store assigns it).
        Must not raise for a physically bad design: return status "failed"
        with `error` explaining what was wrong and what to change.
        """


# Rules the hooks enforce (設計部 implements them; ids are used in hook.denied events).
HOOK_RULES = {
    "H1_prediction_first": "analyze needs prediction_ids that exist for the same design_version and load_case_id",
    "H2_tool_usable": "analyze needs a tool whose spec has usable=true (all known-answer checks pass)",
    "H3_cause_before_continue": "while a comparison in a domain is a mismatch without a cause, analyze and compare in that domain are denied",
    "H4_no_stop_before_judged": "the run may not stop while a picked domain is pending, unless the round or cost limit is reached (blocks are counted and capped)",
}

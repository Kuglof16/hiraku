"""Python-side signatures between departments.

Solvers (解析部) implement these; the MCP tools (設計部) call them and
never compute numbers themselves. All dicts are contract records
(see hiraku.schema.json); validate them at the boundary.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

Record = dict[str, Any]


class ToolError(Exception):
    """A tool call that cannot go ahead. The message is shown to Claude as is,
    so it says what is wrong and what to do next. Defined here so every layer
    (intake, tools, agent) raises the same class; hiraku.tools.context re-exports it."""

# Entry points of hiraku.solvers (hiraku/solvers/__init__.py). Callers use only
# these two names; importing hiraku.solvers must have no side effects.
#   formula_registry() -> FormulaRegistry
#   builtin_tools()    -> list[AnalysisTool]
SOLVER_ENTRY_POINTS = ("formula_registry", "builtin_tools")
# Optional entry point (callers check hasattr first):
#   frame_mesh(design) -> a `mesh` record without mesh_id (the shell mesh of the frame)
OPTIONAL_SOLVER_ENTRY_POINTS = ("frame_mesh",)

# params["loading"] of a load case: who and what is on board (catalog Q30).
LOADINGS = ("empty", "driver", "two", "two_cargo")
DEFAULT_LOADING = "two_cargo"


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
    # Optional attribute needs_mesh (False when absent): True when run() draws
    # on the version's shell mesh (params["mesh"]). Not a protocol member, so
    # tools without it still pass isinstance(tool, AnalysisTool).

    def spec(self) -> Record:
        """The `tool_spec` record, with `checks` filled by check()."""

    def check(self) -> list[Record]:
        """Run the known-answer cases; return `tool_check` records."""

    def run(self, design: Record, load_case: Record, predictions: list[Record], params: dict[str, Any]) -> Record:
        """Run the analysis.

        params["upstream_analyses"] = {load_case_id: analysis} carries the
        matched analyses of the load cases that feed this one (e.g. the P1
        wheel force into R1). The caller adds their ids to inputs_from.

        params["mesh"] is the version's `mesh` record (with mesh_id) when the
        tool needs_mesh: the caller (analyze) makes it once per design version
        with frame_mesh() and records it (mesh.created) before the first run.
        The tool checks it was made from this design, and names its mesh_id in
        the fields it draws on it. Without params["mesh"] (known-answer checks,
        unit tests) the tool returns results without mesh fields.

        Returns an `analysis` record without analysis_id (the store assigns it).
        Must not raise for a physically bad design: return status "failed"
        with `error` explaining what was wrong and what to change.
        """


# Rules the hooks enforce (設計部 implements them; ids are used in hook.denied events).
HOOK_RULES = {
    "HK1_prediction_first": "analyze needs prediction_ids that exist for the same design_version and load_case_id",
    "HK2_tool_usable": "analyze needs a tool whose spec has usable=true (all known-answer checks pass)",
    "HK3_cause_before_continue": "while a comparison in a domain is a mismatch without a cause, analyze and compare in that domain are denied",
    "HK4_no_stop_before_judged": "the run may not stop while a picked domain of the latest version is pending (the round limit only ends revisions; the latest version is still judged), unless the cost or turn limit is reached, no tool or formula can judge it, or the round limit is used and the pending domains wait behind a red of a lower stage (HK5); a case no K0 formula can predict does not hold the stop even with an analysis result past its criterion, which stays on its tile and in the report as past the limit and not judged (blocks are counted and capped)",
    "HK5_light_first": "in a version, analyze with a tool of stage n is denied while any other load case checked with a tool of a lower stage is red or not yet judged, or the same load case has a red verdict from a lower-stage tool (load cases past the limit, made to fail, do not count; nor does a load case that cannot be judged: no K0 formula predicts it, or its mismatch has a cause recorded with ends_case (a scripted plan's expected_mismatch end), and it stays on its tile as not judged); a red goes to revise_design before heavier checks; when the round limit is used, the heavier checks stay unrun and the run ends (tools of the same stage may run in parallel; stage 0 is layout and rules)",
    "HK6_module_scope": "for a design with modules: a module designer's revise_design changes only its own module's variables and choices and is a proposal (design.change_proposed), not a version; only the packager changes package variables, envelopes and interfaces, and only build_version makes the next version from the round's proposals; a red goes to the designers of the modules its load case names, who work in parallel; build_version is refused while two proposals change the same variable or a module's parts would leave its envelope",
}

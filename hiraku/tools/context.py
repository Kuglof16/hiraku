"""What every tool function needs: the store, the formulas and the tool shelf."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hiraku.catalog import owner_agent
from hiraku.contract.protocols import AnalysisTool, FormulaRegistry
from hiraku.store import RunStore

Record = dict[str, Any]


class ToolError(Exception):
    """A tool call that cannot go ahead. The message is shown to Claude as is,
    so it says what is wrong and what to do next."""


@dataclass
class RunContext:
    store: RunStore
    formulas: FormulaRegistry
    tools: dict[str, AnalysisTool]
    specs: dict[str, Record] = field(default_factory=dict)  # checked tool_spec records

    # ---------------------------------------------------------------- project views

    @property
    def project(self) -> Record:
        return self.store.project

    @property
    def version(self) -> int:
        return self.store.design_version

    @property
    def load_cases(self) -> list[Record]:
        return self.project["load_cases"]

    @property
    def picked(self) -> list[str]:
        return [p["domain"] for p in self.project["domain_picks"]]

    def load_case(self, load_case_id: str) -> Record:
        for lc in self.load_cases:
            if lc["load_case_id"] == load_case_id:
                return lc
        ids = ", ".join(lc["load_case_id"] for lc in self.load_cases)
        raise ToolError(f"荷重条件 {load_case_id!r} はありません。使えるのは {ids} です (get_context の load_cases)")

    def has_load_case(self, load_case_id: str) -> bool:
        return any(lc["load_case_id"] == load_case_id for lc in self.load_cases)

    def owner(self, load_case_id: str) -> str:
        return owner_agent(self.load_cases, self.picked, load_case_id)

    def upstream_of(self, load_case_id: str) -> list[Record]:
        """Load cases whose results this one takes as input (feeds)."""
        return [lc for lc in self.load_cases if load_case_id in lc.get("feeds", [])]

    # ---------------------------------------------------------------- record views

    def prediction(self, prediction_id: str) -> Record | None:
        return self.store.get("prediction", prediction_id)

    def comparison_domain(self, cmp: Record) -> str | None:
        pred = self.prediction(cmp["prediction_id"])
        if pred is None:
            return None
        return pred["domain"]

    def causes_by_comparison(self) -> dict[str, Record]:
        return {c["comparison_id"]: c for c in self.store.records("cause")}

    def open_mismatches(self, domain: str | None = None) -> list[Record]:
        """Mismatching comparisons that have no cause yet (in one domain, or all)."""
        causes = self.causes_by_comparison()
        out = []
        for cmp in self.store.records("comparison"):
            if cmp["status"] != "mismatch" or cmp["comparison_id"] in causes:
                continue
            if domain is None or self.comparison_domain(cmp) == domain:
                out.append(cmp)
        return out

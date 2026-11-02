"""Load data/catalog/ and requirements files."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from hiraku import contract
from hiraku.catalog.expr import applies

Record = dict[str, Any]

REPO_ROOT = Path(__file__).resolve().parents[2]
CATALOG_DIR = REPO_ROOT / "data" / "catalog"


def read_data(path: Path) -> Any:
    """Read a YAML or JSON file."""
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        if path.suffix.lower() == ".json":
            return json.load(f)
        return yaml.safe_load(f)


def load_requirements(path: Path) -> Record:
    """Read a requirements file and check it against the contract."""
    req = read_data(path)
    contract.validate("requirements_file", req)
    return req


@dataclass
class Catalog:
    tests: list[Record]
    agents: Record
    _by_id: dict[str, Record] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {t["id"]: t for t in self.tests}

    def test(self, test_id: str) -> Record:
        try:
            return self._by_id[test_id]
        except KeyError:
            raise KeyError(f"test {test_id} is not in the catalog") from None

    def select_tests(self, flags: dict[str, bool], speed_class: str | None) -> list[str]:
        """Tests whose applies_if holds for these flags."""
        return [t["id"] for t in self.tests if applies(t["applies_if"], flags, speed_class)]

    def prop(self, agent_id: str) -> str:
        if agent_id.startswith("cae."):
            return self.agents["cae"][agent_id.split(".", 1)[1]]
        return self.agents[agent_id]["prop"]


def load_catalog(folder: Path = CATALOG_DIR) -> Catalog:
    folder = Path(folder)
    tests = read_data(folder / "tests.yaml")["tests"]
    agents = read_data(folder / "agents.yaml")
    for t in tests:
        applies(t["applies_if"], {})  # fail early on a bad expression
        if t["domain"] not in contract.DOMAINS:
            raise ValueError(f"test {t['id']}: unknown domain {t['domain']!r}")
    return Catalog(tests=tests, agents=agents)

"""Packages of hiraku import only packages below them, so the departments'
code stays layered: contract < solvers < designgen < store < catalog < intake
< tools < agent < export < webserve and __main__ (docs/contract.md)."""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "hiraku"

LAYERS = ["contract", "cad", "solvers", "designgen", "store", "catalog", "intake", "tools", "agent", "export", "webserve", "__main__"]

# Upward imports that exist today and are being removed (lead asked the owners).
KNOWN_UPWARD = {("catalog", "tools")}


def _package(module: str) -> str | None:
    parts = module.split(".")
    if parts[0] != "hiraku" or len(parts) < 2:
        return None
    return parts[1]


def _imports(path: Path, own: list[str]):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = own[: len(own) - node.level + (1 if path.name == "__init__.py" else 0)]
                yield ".".join(base + ([node.module] if node.module else []))
            elif node.module == "hiraku":
                for a in node.names:  # from hiraku import contract
                    yield f"hiraku.{a.name}"
            elif node.module:
                yield node.module


def upward_imports() -> set[tuple[str, str]]:
    rank = {name: i for i, name in enumerate(LAYERS)}
    found = set()
    for f in ROOT.rglob("*.py"):
        own = ["hiraku", *f.relative_to(ROOT).with_suffix("").parts]
        if own[-1] == "__init__":
            own = own[:-1]
        src = own[1] if len(own) > 1 else None
        if src not in rank:
            continue
        for mod in _imports(f, own):
            dst = _package(mod)
            if dst in rank and dst != src and rank[dst] > rank[src]:
                found.add((src, dst))
    return found


class LayerTest(unittest.TestCase):
    def test_every_package_has_a_layer(self):
        packages = {p.stem if p.is_file() else p.name for p in ROOT.iterdir()
                    if (p.is_dir() and (p / "__init__.py").exists()) or (p.suffix == ".py" and p.stem != "__init__")}
        self.assertEqual(sorted(packages - set(LAYERS)), [])

    def test_no_new_upward_imports(self):
        self.assertEqual(sorted(upward_imports() - KNOWN_UPWARD), [])


if __name__ == "__main__":
    unittest.main()

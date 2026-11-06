"""hiraku.cad is loosely coupled: only the adapter hiraku/tools/cad_tools.py imports it, and it
imports only hiraku.contract, the standard library and numpy."""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "hiraku"
CAD = ROOT / "cad"
ADAPTER = ROOT / "tools" / "cad_tools.py"


def imported(path: Path) -> list[tuple[int, str]]:
    """(line, module) of every import in the file; a relative import is made absolute."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rel = path.relative_to(ROOT.parent) if path.is_relative_to(ROOT.parent) else Path(path.name)
    pkg = ".".join(rel.parent.parts)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.split(".")[: len(pkg.split(".")) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            # `from hiraku import cad` imports hiraku.cad as well
            out += [(node.lineno, mod)] + [(node.lineno, f"{mod}.{a.name}") for a in node.names]
        elif isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) in (
                "__import__", "import_module") and node.args and isinstance(node.args[0], ast.Constant):
            out.append((node.lineno, str(node.args[0].value)))
    return out


def is_cad(mod: str) -> bool:
    return mod == "hiraku.cad" or mod.startswith("hiraku.cad.")


class CouplingTest(unittest.TestCase):
    def test_only_the_adapter_imports_hiraku_cad(self):
        bad = []
        files = [p for p in ROOT.rglob("*.py") if not p.is_relative_to(CAD)]
        self.assertGreater(len(files), 50)
        for p in files:
            if p == ADAPTER:
                continue
            bad += [f"{p.relative_to(ROOT.parent)}:{n} {m}" for n, m in imported(p) if is_cad(m)]
        self.assertEqual(bad, [])
        if ADAPTER.exists():
            self.assertTrue(any(is_cad(m) for _, m in imported(ADAPTER)), "the adapter should use hiraku.cad")

    def test_the_package_imports_only_contract_stdlib_and_numpy(self):
        files = sorted(CAD.rglob("*.py"))
        self.assertGreaterEqual(len(files), 7)
        bad = []
        for p in files:
            for n, m in imported(p):
                top = m.split(".")[0]
                ok = (is_cad(m) or m == "hiraku.contract" or m.startswith("hiraku.contract.")
                      or top in ("numpy", "__future__") or top in sys.stdlib_module_names)
                if m == "hiraku":  # `from hiraku import contract` names hiraku itself as well
                    ok = True
                if not ok:
                    bad.append(f"{p.relative_to(ROOT.parent)}:{n} {m}")
        self.assertEqual(bad, [])

    def test_the_check_sees_a_forbidden_import(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.py"
            p.write_text("from hiraku import cad\nimport hiraku.cad.step_read\n", encoding="utf-8")
            mods = [m for _, m in imported(p)]
        self.assertIn("hiraku.cad", mods)
        self.assertIn("hiraku.cad.step_read", mods)


if __name__ == "__main__":
    unittest.main()

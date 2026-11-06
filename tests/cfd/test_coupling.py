"""hiraku.cfd is loosely coupled: only the adapter hiraku/tools/cfd_tools.py imports it, and it imports only
hiraku.contract, the standard library and numpy."""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "hiraku"
CFD = ROOT / "cfd"
ADAPTER = ROOT / "tools" / "cfd_tools.py"
# The picture scripts (render_*.py), which nothing in the solver path imports, may use what Blender and their picture steps bring.
RENDER_SIDE = ("bpy", "mathutils", "PIL", "scipy")


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
            # `from hiraku import cfd` imports hiraku.cfd as well
            out += [(node.lineno, mod)] + [(node.lineno, f"{mod}.{a.name}") for a in node.names]
        elif isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) in (
                "__import__", "import_module") and node.args and isinstance(node.args[0], ast.Constant):
            out.append((node.lineno, str(node.args[0].value)))
    return out


def is_cfd(mod: str) -> bool:
    return mod == "hiraku.cfd" or mod.startswith("hiraku.cfd.")


class CouplingTest(unittest.TestCase):
    def test_only_the_adapter_imports_hiraku_cfd(self):
        bad = []
        files = [p for p in ROOT.rglob("*.py") if not p.is_relative_to(CFD)]
        self.assertGreater(len(files), 50)
        for p in files:
            if p == ADAPTER:
                continue
            bad += [f"{p.relative_to(ROOT.parent)}:{n} {m}" for n, m in imported(p) if is_cfd(m)]
        self.assertEqual(bad, [])
        self.assertTrue(any(is_cfd(m) for _, m in imported(ADAPTER)), "the adapter should use hiraku.cfd")

    def test_the_package_imports_only_contract_stdlib_and_numpy(self):
        files = sorted(CFD.rglob("*.py"))
        self.assertGreaterEqual(len(files), 8)
        bad = []
        for p in files:
            extra = RENDER_SIDE if p.name.startswith("render_") else ()
            for n, m in imported(p):
                top = m.split(".")[0]
                ok = (is_cfd(m) or m == "hiraku.contract" or m.startswith("hiraku.contract.")
                      or top in ("numpy", "__future__") or top in sys.stdlib_module_names or top in extra)
                if m == "hiraku":  # `from hiraku import contract` names hiraku itself as well
                    ok = True
                if not ok:
                    bad.append(f"{p.relative_to(ROOT.parent)}:{n} {m}")
        self.assertEqual(bad, [])

    def test_mcp_names_the_adapter_in_one_line_under_the_cad_one(self):
        lines = (ROOT / "tools" / "mcp.py").read_text(encoding="utf-8").splitlines()
        at = [i for i, line in enumerate(lines) if "cfd_tools" in line]
        self.assertEqual(len(at), 1)
        self.assertIn("cad_tools", lines[at[0] - 1])

    def test_the_check_sees_a_forbidden_import(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.py"
            p.write_text("from hiraku import cfd\nimport hiraku.cfd.solver\n", encoding="utf-8")
            mods = [m for _, m in imported(p)]
        self.assertIn("hiraku.cfd", mods)
        self.assertIn("hiraku.cfd.solver", mods)


if __name__ == "__main__":
    unittest.main()

"""The product does not import the QA department's code.

qa/ checks hiraku/ against answers written apart from it (qa.known_answers); if hiraku/ read
those answers, a check would compare the product with itself.
"""

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def qa_imports(path: Path) -> list[str]:
    """The import statements of a Python file that name the qa package, as "line: module"."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        out += [f"{node.lineno}: {n}" for n in names if n == "qa" or n.startswith("qa.")]
    return out


class ProductIndependentTest(unittest.TestCase):
    def test_hiraku_does_not_import_qa(self):
        found = {p.relative_to(ROOT).as_posix(): qa_imports(p) for p in sorted((ROOT / "hiraku").rglob("*.py"))}
        self.assertEqual({p: x for p, x in found.items() if x}, {})

    def test_the_finder_sees_an_import(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "m.py"
            f.write_text("import os\nfrom qa.known_answers import kirsch\nimport qa\nfrom .qa import x\n",
                         encoding="utf-8")
            self.assertEqual(qa_imports(f), ["2: qa.known_answers", "3: qa"])


if __name__ == "__main__":
    unittest.main()

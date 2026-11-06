"""Read a static export (python -m hiraku.webserve.export_static --out DIR) for what must not be published.

The export is read as qa.judge_scan reads a branch (Scan.check_line and its private patterns), plus local
drive paths; gzipped records are read unpacked. hiraku/ does not import qa, so the exporter runs this as
its own process:

    python -m qa.export_scan DIR [--words-from HOOK]

Exit status 1 when there is an error, 0 otherwise.
"""

from __future__ import annotations

import argparse
import gzip
import subprocess
import sys
from pathlib import Path

from hiraku.webserve.export_static import DRIVE_PATH_RE, MARK_FILE, SCAN_SUFFIXES
from qa.judge_scan import Finding, Scan, default_hook

ROOT = Path(__file__).resolve().parents[1]


def scan(out: Path | str, words_from: Path | None = None, repo: Path = ROOT) -> tuple[list[Finding], bool]:
    """The findings in the export at `out`, and whether private word patterns were found to check with."""
    out = Path(out)
    hook = words_from
    if hook is None:
        try:
            hook = default_hook(repo)
        except (OSError, subprocess.CalledProcessError):
            hook = repo / ".private" / "pre-commit"
    s = Scan(repo, "HEAD", hook)
    checked = bool(s.private)
    s.findings = []  # (the missing-patterns note is returned as `checked` instead)
    for p in sorted(out.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(out).as_posix()
        for pat in s.private:
            if pat.search(rel):
                s.add("error", "private.word", rel, "in the file name")
        if p.suffix == ".gz":
            text = gzip.decompress(p.read_bytes()).decode("utf-8", errors="replace")
        elif p.suffix.lower() in SCAN_SUFFIXES or p.name == MARK_FILE:
            text = p.read_text(encoding="utf-8", errors="replace")
        else:
            continue
        for n, line in enumerate(text.splitlines(), start=1):
            s.check_line(f"{rel}:{n}", line, doc=False)
            m = DRIVE_PATH_RE.search(line)
            if m:
                s.findings.append(Finding("error", "local.path", f"{rel}:{n}", m.group(0)))
    return s.findings, checked


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qa.export_scan", description=__doc__.split("\n\n")[0])
    ap.add_argument("out", type=Path, help="the export's folder")
    ap.add_argument("--words-from", type=Path, help="private pre-commit hook with WORDS= and PATHS= lines")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    findings, checked = scan(args.out, args.words_from)
    errors = [f for f in findings if f.level == "error"]
    if not checked:
        print("scan: no private word patterns found (the private hook); private words not checked")
    print(f"scan: {len(errors)} error(s)")
    for f in findings:
        print(f"  {f}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

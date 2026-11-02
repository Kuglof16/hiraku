"""Read a branch the way a judge would and list what should not be public.

    python -m qa.judge_scan [--ref main] [--words-from PATH]

Reads only (git ls-files / git show / git log on the ref). Prints findings as
`LEVEL rule where: text` and exits 1 when there is an error.

errors (must be fixed before publishing):
- private words and paths. The word and path patterns are read at run time
  from the private pre-commit hook kept outside the repository (the WORDS= and
  PATHS= lines), so this file names none of them. Default: .private/pre-commit
  next to the main checkout; override with --words-from.
- e-mail addresses other than GitHub noreply ones, in files, commit messages
  and commit author/committer fields
- paths into a home folder (the Users folder on Windows and macOS, home on Linux)
- things that look like keys (sk-ant-..., a filled ANTHROPIC_API_KEY=...)
- tracked files that should stay local (.env, CLAUDE.local.md, runs/, .private/)
- a commit dated before the start of the event (2026-11-02 12:00 JST)

review (a person decides):
- words that suggest quoted speech or a diary (devlog) in commit messages and docs
- stubs: TODO, FIXME, NotImplementedError, functions whose body is only `pass`
- numbers with three or more significant digits in code and data (outside
  string literals and the contract's shape examples), without a source note
  (source, 出典, closed form, ...) on the line or the four lines above it
  (code) or within six lines either side (data)
- records with verified=false, and data files with no `source`
- whether README says which values were not checked (確かめていない)
- binary files (rights of images and other material)
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

START = datetime(2026, 11, 2, 12, 0, tzinfo=timezone(timedelta(hours=9)))
TEXT_SUFFIXES = {".py", ".md", ".json", ".jsonl", ".txt", ".yaml", ".yml", ".toml", ".cfg", ".ini",
                 ".js", ".mjs", ".cjs", ".ts", ".html", ".css", ".sh", ".ps1", ""}
CODE_DIRS = ("hiraku/", "data/", "web/", "plugins/", "promo/")
NOT_CODE = ("hiraku/contract/examples/", "hiraku/contract/hiraku.schema.json")
SELF = "qa/judge_scan.py"  # holds the patterns themselves
NO_SPEECH_CHECK = ("CLAUDE.md",)  # states the rule against quoting
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(r"(@users\.noreply\.github\.com|noreply@anthropic\.com|@example\.(com|org))$")
HOME_RE = re.compile(r"(?i)([a-z]:[\\/]+users[\\/]+[^\\/\s]+|/Users/[^/\s]+|/home/[^/\s]+)")
KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]{10,}|ANTHROPIC_API_KEY\s*=\s*['\"]?[A-Za-z0-9_-]{12,}")
LOCAL_FILES = re.compile(r"(^|/)(\.env(\..*)?|CLAUDE\.local\.md|[^/]*\.local\.[^/]+)$|^runs/|^\.private/")
SPEECH_RE = re.compile(r"本人|発言|と言(った|われ)|って言|さんが|devlog|日記|今日は|昨日は|"
                       r"\b(he|she|they) said\b|\bsaid that\b|\bI think\b|\bwe decided\b", re.I)
STUB_RE = re.compile(r"\b(TODO|FIXME|XXX)\b|NotImplementedError|raise NotImplemented\b")
NUMBER_RE = re.compile(r"(?<![\w.])\d+(\.\d+)?(e[-+]?\d+)?(?![\w.])", re.I)
STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"' + r"|'(?:[^'\\]|\\.)*'")
VERIFIED_FALSE = re.compile(r'"verified"\s*:\s*false')
SOURCE_HINT = re.compile(r"(?i)source|basis|出典|closed[- ]form|閉じた式|教科書|textbook|derived|導出|"
                         r"確かめていない|not (yet )?checked|unverified|assum|仮定|scipy\.constants|CODATA|ISO|JIS|SI\b")


@dataclass(frozen=True)
class Finding:
    level: str  # "error" or "review"
    rule: str
    where: str
    text: str

    def __str__(self) -> str:
        return f"{self.level.upper():6} {self.rule:16} {self.where}: {self.text}"


def _significant(token: str) -> int:
    """Significant digits of a number literal: 205000.0 -> 3, 0.15 -> 2, 900.0 -> 1."""
    mantissa = re.split(r"[eE]", token)[0].replace(".", "").lstrip("0").rstrip("0")
    return len(mantissa)


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def private_patterns(hook: Path) -> list[re.Pattern]:
    """The WORDS and PATHS regexes from the private pre-commit hook."""
    out = []
    if not hook.exists():
        return out
    for line in hook.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*(WORDS|PATHS)='(.*)'\s*$", line)
        if m:
            out.append(re.compile(m.group(2), re.I if m.group(1) == "PATHS" else 0))
    return out


def default_hook(repo: Path) -> Path:
    common = Path(git("rev-parse", "--git-common-dir", cwd=repo).strip())
    if not common.is_absolute():
        common = (repo / common).resolve()
    return common.parent / ".private" / "pre-commit"


class Scan:
    def __init__(self, repo: Path, ref: str, hook: Path):
        self.repo, self.ref = repo, ref
        self.private = private_patterns(hook)
        self.findings: list[Finding] = []
        if not self.private:
            self.add("error", "private.patterns", str(hook.name), "no WORDS/PATHS patterns found; private words not checked")

    def add(self, level: str, rule: str, where: str, text: str) -> None:
        self.findings.append(Finding(level, rule, where, text[:200]))

    # --- lines of text --------------------------------------------------------
    def check_line(self, where: str, line: str, doc: bool) -> None:
        for pat in self.private:
            m = pat.search(line)
            if m:
                self.add("error", "private.word", where, f"{m.group(0)!r} in: {line.strip()}")
        for m in EMAIL_RE.finditer(line):
            if not EMAIL_OK.search(m.group(0)):
                self.add("error", "email", where, m.group(0))
        m = HOME_RE.search(line)
        if m:
            self.add("error", "home.path", where, m.group(0))
        if KEY_RE.search(line):
            self.add("error", "secret", where, "looks like a key")
        if doc and SPEECH_RE.search(line):
            self.add("review", "speech.diary", where, line.strip())

    def files(self) -> list[str]:
        return [p for p in git("ls-tree", "-r", "--name-only", self.ref, cwd=self.repo).splitlines() if p]

    def scan_files(self) -> None:
        names = self.files()
        readme_has_unchecked = False
        for name in names:
            if LOCAL_FILES.search(name):
                self.add("error", "local.file", name, "should not be tracked")
            for pat in self.private:
                if pat.search(name):
                    self.add("error", "private.word", name, "in the file name")
            suffix = Path(name).suffix.lower()
            if name == SELF:
                continue
            if suffix not in TEXT_SUFFIXES:
                self.add("review", "binary", name, "check the rights to this file")
                continue
            try:
                text = git("show", f"{self.ref}:{name}", cwd=self.repo)
            except subprocess.CalledProcessError:
                continue
            doc = (suffix in (".md", ".txt") or name.startswith(("docs/", "promo/"))) and name not in NO_SPEECH_CHECK
            code = name.startswith(CODE_DIRS) and not name.startswith(NOT_CODE)
            lines = text.splitlines()
            for n, line in enumerate(lines, start=1):
                self.check_line(f"{name}:{n}", line, doc)
                if code:
                    self.check_code_line(name, n, line, lines, suffix)
            if name == "README.md" and "確かめていない" in text:
                readme_has_unchecked = True
            if suffix == ".json" and name.startswith("data/"):
                self.check_data_json(name, text)
        if "README.md" in names and not readme_has_unchecked:
            self.add("review", "readme.unchecked", "README.md", "no 確かめていない list yet")
        if "LICENSE" not in names:
            self.add("error", "license", "LICENSE", "missing")

    def check_code_line(self, name: str, n: int, line: str, lines: list[str], suffix: str) -> None:
        where = f"{name}:{n}"
        if STUB_RE.search(line):
            self.add("review", "stub", where, line.strip())
        if suffix == ".py" and re.match(r"\s*pass\s*(#.*)?$", line):
            prev = next((lines[k] for k in range(n - 2, -1, -1) if lines[k].strip()), "")
            if re.match(r"\s*def\s", prev) or prev.rstrip().endswith('"""'):
                self.add("review", "stub", where, "function body is only pass")
        if suffix in (".py", ".json", ".js", ".mjs"):
            bare = STRING_RE.sub('""', line.split("#")[0] if suffix == ".py" else line)
            if any(_significant(m.group(0)) >= 3 for m in NUMBER_RE.finditer(bare)):
                # code: the line and the comment lines above it; data: the surrounding object
                above, below = (4, 0) if suffix == ".py" else (6, 6)
                near = "\n".join(lines[max(0, n - 1 - above):n + below])
                if not SOURCE_HINT.search(near):
                    self.add("review", "number.source", where, line.strip())

    def check_data_json(self, name: str, text: str) -> None:
        if VERIFIED_FALSE.search(text):
            self.add("review", "unverified", name, "has verified=false values; README must list them")
        if '"source"' not in text:
            self.add("review", "data.source", name, "data file without any source field")

    # --- commits --------------------------------------------------------------
    def scan_commits(self) -> None:
        sep = "\x1e"
        log = git("log", self.ref, f"--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1f%aI%x1f%cI%x1f%B{sep}", cwd=self.repo)
        for rec in filter(str.strip, log.split(sep)):
            h, an, ae, cn, ce, ad, cd, body = rec.strip("\n").split("\x1f", 7)
            short = h[:7]
            for who in (ae, ce):
                if not EMAIL_OK.search(who):
                    self.add("error", "email", f"commit {short}", f"author/committer address {who}")
            for d in (ad, cd):
                if datetime.fromisoformat(d) < START:
                    self.add("error", "date", f"commit {short}", f"dated {d}, before {START.isoformat()}")
            for n, line in enumerate(body.splitlines(), start=1):
                self.check_line(f"commit {short} msg:{n}", line, doc=True)

    def run(self) -> list[Finding]:
        self.scan_files()
        self.scan_commits()
        return self.findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qa.judge_scan", description=__doc__.split("\n\n")[0])
    ap.add_argument("--ref", default="main")
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--words-from", type=Path, help="private pre-commit hook with WORDS= and PATHS= lines")
    ap.add_argument("--errors-only", action="store_true")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    repo = args.repo.resolve()
    hook = args.words_from or default_hook(repo)
    findings = Scan(repo, args.ref, hook).run()
    errors = [f for f in findings if f.level == "error"]
    shown = errors if args.errors_only else sorted(findings, key=lambda f: (f.level != "error", f.rule, f.where))
    print(f"{args.ref}: {len(errors)} error(s), {len(findings) - len(errors)} to review")
    for f in shown:
        print(f"  {f}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

"""Read a branch the way a judge would and list what should not be public.

    python -m qa.judge_scan [--ref main] [--words-from PATH]

Reads only (git ls-files / git show / git log on the ref). Prints findings as
`LEVEL rule where: text` and exits 1 when there is an error.

errors (must be fixed before publishing):
- private words and paths. The word and path patterns are read at run time
  from the private pre-commit hook kept outside the repository (the WORDS= and
  PATHS= lines), so this file names none of them. Default: .private/pre-commit
  next to the main checkout; override with --words-from.
- e-mail addresses other than GitHub noreply ones (made-up ones on the test domains
  .invalid, .example and .test are left out and counted in one line), in files, commit messages
  and commit author/committer fields
- paths into a home folder (the Users folder on Windows and macOS, home on Linux)
- things that look like keys (sk-ant-..., a filled ANTHROPIC_API_KEY=...)
- tracked files that should stay local (.env, CLAUDE.local.md, runs/, .private/)
- a commit dated before the start of the event (2026-11-02 12:00 JST)

review (a person decides):
- words that suggest quoted speech or a diary (devlog) in commit messages and docs
- stubs: TODO, FIXME, NotImplementedError (not a method whose whole body raises it,
  which a base class leaves to its subclasses), functions whose body is only `pass`
- numbers with three or more significant digits in the solvers, catalog, tools
  and data (outside string literals), without a source note
  (source, 出典, closed form, a requirement key such as Q33, ...) on the line or
  the four lines above it (code) or within six lines either side (data).
  Not counted, as they are not values taken from outside: docstrings, a tool's
  own known-answer cases (functions named *known_answer*, *check*, test*),
  constants exact by definition (9.80665, 273.15, ...), guards such as 0.999,
  integer counts of a numerical method (SCENE_POINTS = 801, nperseg=4096),
  integer coefficients of a closed form (384 in 5 w L^4 / (384 E I)),
  subscripts, design variables with their range (Var(unit, default, lo, hi) in
  hiraku/designgen/), and the design JSON that hiraku/designgen/ writes
  (data/designs/*.json; its values are checked in the generator)
- data files with verified=false values that README's 確かめていない list does not
  name (by the file's path, or for a design JSON by hiraku/designgen/, which writes
  it), and data files with no `source`
- whether README says which values were not checked (確かめていない)
- binary files (rights of images and other material)
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
import tokenize
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

START = datetime(2026, 11, 2, 12, 0, tzinfo=timezone(timedelta(hours=9)))
TEXT_SUFFIXES = {".py", ".md", ".json", ".jsonl", ".txt", ".yaml", ".yml", ".toml", ".cfg", ".ini",
                 ".js", ".mjs", ".cjs", ".ts", ".html", ".css", ".sh", ".ps1", ""}
CODE_DIRS = ("hiraku/", "data/", "web/", "plugins/", "promo/")
NOT_CODE = ("hiraku/contract/examples/", "hiraku/contract/hiraku.schema.json")
# where values taken from outside (material data, catalog limits, constants) live;
# screen sizes, ports and buffer sizes elsewhere are not such values
NUMBER_DIRS = ("hiraku/solvers/", "hiraku/catalog/", "hiraku/tools/", "hiraku/designgen/", "data/")
SELF = "qa/judge_scan.py"  # holds the patterns themselves
NO_SPEECH_CHECK = ("CLAUDE.md",)  # states the rule against quoting
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMAIL_OK = re.compile(r"(@users\.noreply\.github\.com|noreply@anthropic\.com|@example\.(com|org))$")
# made-up addresses in tests: top-level domains reserved for testing and examples (RFC 2606), never real
EMAIL_RESERVED = re.compile(r"\.(invalid|example|test)$", re.IGNORECASE)
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
                         r"確かめていない|checked|unverified|assum|仮定|scipy\.constants|CODATA|ISO|JIS|SI\b|"
                         r"\bISA\b|defin(ed|ition)|CGPM|"
                         r"(?-i:\bQ[0-9]{2,}\b)")  # a requirement key: its basis is in the requirements file
# Numbers that are not values taken from outside, so they need no source note:
# - inside a tool's own known-answer cases (inputs and expected results of its checks)
SELF_CHECK_FN = re.compile(r"(?i)known_answer|check|^_?test")
# - exact by definition (SI or the unit's definition)
DEFINED = {"9.80665", "273.15", "101325", "101325.0", "25.4", "0.0254", "0.3048", "0.45359237", "1.609344"}
# - numerical guards just below or above 1 (0.999, 1.001)
GUARD_RE = re.compile(r"^(0\.9{3,}|1\.0{2,}1)$")
# - counts of a numerical method (points, steps, samples, terms, bins, iterations)
COUNT_RE = re.compile(r"(?i)\b(\w*(?:points|steps|samples|terms|bins|iters?|iterations|nperseg)\w*\s*[:=]\s*)\d+\b")
# - a design JSON is written by hiraku/designgen/ (data/designs/make_*.py), whose code
#   holds the values and their sources and is checked itself
GENERATED_JSON = re.compile(r"^data/designs/[^/]+\.json$")
# - a design variable with its range, Var(unit, default, lo, hi, ...) in hiraku/designgen/:
#   the design chooses it (a Var without a range is a fixed value and needs its note)
DESIGN_VAR_RE = re.compile(r"\bVar\(\s*\"[^\"]*\"\s*,\s*-?[\d.]+\s*,\s*-?[\d.]+\s*,\s*-?[\d.]+")


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


def _text_lines(source: str) -> set[int]:
    """Line numbers inside multi-line string literals (docstrings, long texts) of Python source."""
    kinds = {tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}
    out: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in kinds and tok.end[0] > tok.start[0]:
                out.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, SyntaxError):
        pass
    return out


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _enclosing_def(lines: list[str], n: int) -> str | None:
    """Name of the function that line n (1-based) is in, or None at module or class level."""
    ind = _indent(lines[n - 1])
    for k in range(n - 2, -1, -1):
        s = lines[k]
        if not s.strip() or s.lstrip().startswith(("#", "@")) or _indent(s) >= ind:
            continue
        m = re.match(r"\s*(?:async\s+)?def\s+(\w+)", s)
        if m:
            return m.group(1)
        if _indent(s) == 0:
            return None
        ind = _indent(s)  # inside some other block: keep looking outward
    return None


def _yaml_item(lines: list[str], n: int) -> str:
    """The YAML list item that line n (1-based) is in, from its "- " line to the next line as
    far left, so a source written anywhere in that item counts for its numbers ("" outside a list)."""
    ind = _indent(lines[n - 1])
    for k in range(n - 2, -1, -1):
        s = lines[k]
        if not s.strip() or s.lstrip().startswith("#") or _indent(s) >= ind:
            continue
        if s.lstrip().startswith("- "):
            top = _indent(s)
            end = next((j for j in range(k + 1, len(lines)) if lines[j].strip() and _indent(lines[j]) <= top),
                       len(lines))
            return "\n".join(lines[k:end])
        ind = _indent(s)  # a key one level up: keep looking outward
    return ""


def _unchecked_list(readme: str) -> str:
    """The part of README that lists the values not checked: from the first heading with
    確かめていない (or, without one, the first line with it) to the next heading ("" when
    there is none). A sentence before the list may use the same word."""
    lines = readme.splitlines()
    has = [k for k, s in enumerate(lines) if "確かめていない" in s]
    start = next((k for k in has if re.match(r"#{1,6}\s", lines[k])), has[0] if has else None)
    if start is None:
        return ""
    end = next((k for k in range(start + 1, len(lines)) if re.match(r"#{1,6}\s", lines[k])), len(lines))
    return "\n".join(lines[start:end])


def _abstract_method(lines: list[str], n: int, texts: set[int]) -> bool:
    """Line n is `raise NotImplementedError` as the whole body of a method: a base class
    leaving it to its subclasses, not unfinished work."""
    if not re.match(r"\s*raise NotImplementedError\b", lines[n - 1]):
        return False
    for k in range(n - 2, -1, -1):
        s = lines[k]
        if not s.strip() or (k + 1) in texts or re.fullmatch(r'\s*[rRuUbB]?("""|\'\'\'|"|\').*\1\s*', s):
            continue  # blank or the docstring
        for j in range(k, max(-1, k - 6), -1):  # the def line, maybe above a signature spread over lines
            m = re.match(r"(\s*)(?:async\s+)?def\s", lines[j])
            if m:
                return len(m.group(1)) > 0 and (j == k or s.rstrip().endswith(":"))
        return False
    return False


def _value_numbers(bare: str) -> list[str]:
    """Number literals with three or more significant digits that could be values from
    outside: not defined constants, numerical guards, integer coefficients of a closed
    form (next to * or /) or subscripts."""
    out = []
    for m in NUMBER_RE.finditer(bare):
        tok = m.group(0)
        if _significant(tok) < 3 or tok in DEFINED or GUARD_RE.match(tok):
            continue
        before, after = bare[:m.start()].rstrip(), bare[m.end():].lstrip()
        if tok.isdigit() and (before.endswith(("*", "/")) or after.startswith(("*", "/"))):
            continue  # 5 * w * L**4 / (384 * E * I)
        if tok.isdigit() and re.search(r"[\w\])]\[-?$", before) and after.startswith("]"):
            continue  # hist[-1001]
        out.append(tok)
    return out


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
        self.reserved_emails = 0  # addresses on .invalid / .example / .test, left out
        self.unchecked = ""  # README's list of values not checked (確かめていない), read by scan_files
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
            if EMAIL_RESERVED.search(m.group(0)):
                self.reserved_emails += 1
            elif not EMAIL_OK.search(m.group(0)):
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
        readme = git("show", f"{self.ref}:README.md", cwd=self.repo) if "README.md" in names else ""
        self.unchecked = _unchecked_list(readme)
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
            texts = _text_lines(text) if suffix == ".py" else set()
            for n, line in enumerate(lines, start=1):
                self.check_line(f"{name}:{n}", line, doc)
                if code:
                    self.check_code_line(name, n, line, lines, suffix, texts)
            if suffix == ".json" and name.startswith("data/"):
                self.check_data_json(name, text)
        if "README.md" in names and not self.unchecked:
            self.add("review", "readme.unchecked", "README.md", "no 確かめていない list yet")
        if "LICENSE" not in names:
            self.add("error", "license", "LICENSE", "missing")

    def check_code_line(self, name: str, n: int, line: str, lines: list[str], suffix: str,
                        texts: set[int] = frozenset()) -> None:
        where = f"{name}:{n}"
        if STUB_RE.search(line) and not (suffix == ".py" and _abstract_method(lines, n, texts)):
            self.add("review", "stub", where, line.strip())
        if suffix == ".py" and re.match(r"\s*pass\s*(#.*)?$", line):
            prev = next((lines[k] for k in range(n - 2, -1, -1) if lines[k].strip()), "")
            if re.match(r"\s*def\s", prev) or prev.rstrip().endswith('"""'):
                self.add("review", "stub", where, "function body is only pass")
        if suffix in (".py", ".json", ".yaml", ".yml") and name.startswith(NUMBER_DIRS) and "/tests/" not in f"/{name}":
            if n in texts or (suffix == ".py" and SELF_CHECK_FN.search(_enclosing_def(lines, n) or "")):
                return  # a docstring, or a tool's own known-answer cases
            if GENERATED_JSON.match(name) or DESIGN_VAR_RE.search(line):
                return  # written by hiraku/designgen/ (checked there), or a design variable with its range
            bare = STRING_RE.sub('""', line.split("#")[0] if suffix == ".py" else line)
            bare = COUNT_RE.sub(r"\1", bare)  # SCENE_POINTS = 801, nperseg=4096: chosen by the code
            if _value_numbers(bare):
                # code: the line and the comment lines above it; data: the surrounding object
                # (in YAML also the whole list item it belongs to)
                above, below = (4, 0) if suffix == ".py" else (6, 6)
                near = "\n".join(lines[max(0, n - 1 - above):n + below])
                if suffix in (".yaml", ".yml"):
                    near += "\n" + _yaml_item(lines, n)
                if not SOURCE_HINT.search(near):
                    self.add("review", "number.source", where, line.strip())

    def check_data_json(self, name: str, text: str) -> None:
        if VERIFIED_FALSE.search(text):
            # listed by its path, or a design JSON by the generator that writes it
            listed = name in self.unchecked or bool(GENERATED_JSON.match(name) and "hiraku/designgen/" in self.unchecked)
            if not listed:
                self.add("review", "unverified", name,
                         "has verified=false values that README's 確かめていない list does not name")
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
    scan = Scan(repo, args.ref, hook)
    findings = scan.run()
    errors = [f for f in findings if f.level == "error"]
    shown = errors if args.errors_only else sorted(findings, key=lambda f: (f.level != "error", f.rule, f.where))
    print(f"{args.ref}: {len(errors)} error(s), {len(findings) - len(errors)} to review")
    if scan.reserved_emails:
        print(f"  left out {scan.reserved_emails} address(es) on domains reserved for tests (.invalid, .example, .test)")
    for f in shown:
        print(f"  {f}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

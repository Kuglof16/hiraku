"""Pick the browser test classes (tests.web) that a change can touch.

python -m qa.browser_pick [--base REV] [--map DIR] [--list]

Reads the map a mapped browser run wrote (python -m qa.run_tests --browser --map):
runs/browser-map/py.json (the repository files each test's process opened or imported) and
runs/browser-map/js/*.json (the JS function ranges each page ran). The change is
`git diff --name-only REV` against the working tree plus the untracked files under the screen's
folders; a changed web/js file the JS map covers is narrowed to the functions its hunks touch.

Prints `--only <class>` arguments for `python -m qa.run_tests --browser` (default; `--only tests.web` when
every class is picked), or the picked names one per line (--list):

    py -m qa.run_tests --browser $(py -m qa.browser_pick)
stderr: the reasons and the map's commit and age. Exit 0; 2 when git or the map cannot be read
(then every test is picked).
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAP_DIR = ROOT / "runs" / "browser-map"
BROWSER_SWITCH = "HIRAKU_WEB_BROWSER"
BROWSER_PREFIX = "tests.web"

# A change to any of these can touch every browser test (fnmatch; "*" also crosses "/").
ALWAYS_PATTERNS = [
    "web/index.html",
    "web/*.css",
    "web/css/*",
    "web/cut.html",
    "web/*three*",             # three.js vendor files and loaders
    "tests/web/__init__.py",
    "tests/web/browser.py",
    "tests/web/_*.py",
    "tests/web/helpers*.py",
    "tests/web/*.js",          # scripts the test helpers put into pages (layout_lint.js)
]

# Untracked files in these folders count as changed.
UNTRACKED_DIRS = ["tests/web", "web", "hiraku", "data", "promo"]


class PickError(Exception):
    """git or the map could not be read."""


# --- pure core ---------------------------------------------------------------------------

def line_starts(text: bytes) -> list[int]:
    """Byte offset of each line's start (lines split by b"\\n")."""
    starts = [0]
    i = text.find(b"\n")
    while i != -1:
        starts.append(i + 1)
        i = text.find(b"\n", i + 1)
    return starts


def parse_hunks(diff_text: str) -> list[tuple[int, int]]:
    """(old_start, old_count) of each hunk in a unified diff."""
    out = []
    for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", diff_text, re.M):
        out.append((int(m.group(1)), 1 if m.group(2) is None else int(m.group(2))))
    return out


def utf16_line_starts(old_text: bytes) -> tuple[list[int], int]:
    """Each line's start as the browser counts a script's positions (UTF-16 code units of the decoded
    text; the DevTools coverage ranges use them), and the text's length in those units."""
    text = old_text.decode("utf-8", "replace")
    starts, pos = [0], 0
    for line in text.splitlines(keepends=True):
        pos += len(line.encode("utf-16-le")) // 2
        starts.append(pos)
    if text and not text.endswith(("\n", "\r")):
        starts.pop()  # the last line has no end: no line starts after it
    return starts, len(text.encode("utf-16-le")) // 2


def byte_ranges(old_text: bytes, hunks: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Each hunk's old-file lines as a range [start, end) of old_text's positions in UTF-16 code units (as
    the coverage records them; for ASCII text they are its bytes). A pure insertion (old_count 0, inserted
    after line old_start) is (p, p) at the position where it goes in."""
    starts, n = utf16_line_starts(old_text)

    def at(line_index: int) -> int:  # 0-based line index -> byte offset (n past the end)
        return starts[line_index] if line_index < len(starts) else n

    out = []
    for old_start, count in hunks:
        if count == 0:
            p = at(old_start)  # after 1-based line old_start = start of 0-based line old_start
            out.append((p, p))
        else:
            out.append((at(old_start - 1), at(old_start - 1 + count)))
    return out


def _touches(rng: tuple[int, int], change: tuple[int, int]) -> bool:
    s, e = rng
    cs, ce = change
    if cs == ce:  # insertion point: strictly inside the range
        return s < cs < e
    return s < ce and cs < e


def own_file(name: str, classes_now: set[str]) -> str:
    """The test module's file for a class or method name (tests.web.test_x.C[.m] -> tests/web/test_x.py)."""
    cls = _class_of(name, classes_now) or name
    return cls.rsplit(".", 1)[0].replace(".", "/") + ".py"


def _class_of(name: str, classes_now: set[str]) -> str | None:
    """The class now (from classes_now) a map name belongs to, or None when it is gone."""
    if name in classes_now:
        return name
    parent = name.rsplit(".", 1)[0]
    return parent if parent in classes_now else None


def _related(a: str, b: str) -> bool:
    """Same test, or one is a method of the other."""
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


def pick(classes_now, py_map, js_map, changed_files, changed_ranges, sizes=None):
    """The tests to run, and the reasons (lines for stderr).

    classes_now: discovered class names now. py_map: the parsed py.json or None (missing).
    js_map: {test name: {file: [(start, end, fn), ...]}} (pages of one test merged).
    changed_files: repo-relative POSIX paths. changed_ranges: {file: [(start, end), ...]} in bytes of
    the file at the map's commit, for the JS files narrowed by function. sizes: {file: bytes at the map's
    commit}; a recorded range from 0 to that size (or, without a size, to the file's largest recorded end)
    is the module's top level and any change to the file hits it.
    Returns (sorted names, reasons)."""
    classes_now = set(classes_now)
    sizes = sizes or {}
    changed = sorted(set(changed_files))
    everything = sorted(classes_now)
    if py_map is None:
        return everything, ["everything: no browser map (run python -m qa.run_tests --browser --map)"]
    if py_map.get("dirty"):
        return everything, ["everything: the map was made on a tree with uncommitted changes"]
    always = [f for f in changed if any(fnmatch.fnmatchcase(f, p) for p in ALWAYS_PATTERNS)]
    if always:
        more = f" (+{len(always) - 1} more)" if len(always) > 1 else ""
        return everything, [f"everything: {always[0]} changed{more}"]

    mapped: dict[str, list[str]] = py_map.get("classes", {}) or {}
    changed_set = set(changed)
    by_own, by_py, by_js = set(), set(), set()
    gone = 0
    for name, files in mapped.items():
        if _class_of(name, classes_now) is None:
            gone += 1
            continue
        if own_file(name, classes_now) in changed_set:
            by_own.add(name)
        js_of = {}  # file -> ranges this test's pages ran
        for t, scripts in js_map.items():
            if _related(t, name):
                for f, rngs in scripts.items():
                    js_of.setdefault(f, []).extend(rngs)
        for f in files:
            if f not in changed_set:
                continue
            if f in changed_ranges and f in js_of:
                if _js_hit(js_of[f], changed_ranges[f], sizes.get(f), _max_end(js_map, f)):
                    by_js.add(name)
            else:
                by_py.add(name)
    new = {c for c in classes_now if not any(_related(c, k) and _class_of(k, classes_now) == c for k in mapped)}
    picked = sorted(by_own | by_py | by_js | new)
    reasons = [f"{len(picked)} picked of {len(classes_now)} classes: {len(by_own)} by their own file, "
               f"{len(by_py)} by py files, {len(by_js)} by JS functions, {len(new)} new"]
    if gone:
        reasons.append(f"{gone} map names no longer found (left out)")
    return picked, reasons


def _max_end(js_map, f: str) -> int:
    return max((r[1] for s in js_map.values() for r in s.get(f, [])), default=0)


def _js_hit(recorded, changes, size, max_end) -> bool:
    top = size if size is not None else max_end
    for r in recorded:
        s, e = r[0], r[1]
        if s == 0 and e >= top:
            return True
        if any(_touches((s, e), c) for c in changes):
            return True
    return False


# --- map and git (thin wrappers) ---------------------------------------------------------

def read_map(map_dir: Path):
    """(py_map or None when missing, js_map). PickError when present but unreadable."""
    py = map_dir / "py.json"
    if not py.exists():
        return None, {}
    try:
        py_map = json.loads(py.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise PickError(f"cannot read {py}: {e}")
    js_map: dict[str, dict[str, list]] = {}
    js_dir = map_dir / "js"
    if js_dir.is_dir():
        for p in sorted(js_dir.glob("*.json")):
            try:
                rec = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError) as e:
                raise PickError(f"cannot read {p}: {e}")
            scripts = js_map.setdefault(rec.get("test", ""), {})
            for f, rngs in (rec.get("scripts") or {}).items():
                scripts.setdefault(f, []).extend(tuple(r) for r in rngs)
    return py_map, js_map


def git(*args: str, binary: bool = False):
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        raise PickError(f"git {' '.join(args)}: {e}")
    if r.returncode != 0:
        raise PickError(f"git {' '.join(args)}: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def changed_files(base: str) -> list[str]:
    tracked = git("-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", base).splitlines()
    untracked = git("-c", "core.quotepath=off", "ls-files", "--others", "--exclude-standard", "--",
                    *UNTRACKED_DIRS).splitlines()
    return sorted({f.strip() for f in tracked + untracked if f.strip()})


def js_changes(commit: str, files: list[str], js_map) -> tuple[dict, dict]:
    """For changed web/js files with recorded ranges that existed at the map's commit:
    ({file: changed byte ranges}, {file: size at the commit})."""
    covered = {f for s in js_map.values() for f in s}
    ranges, sizes = {}, {}
    for f in files:
        if not (fnmatch.fnmatchcase(f, "web/js/*.js") and f in covered):
            continue
        try:
            old = git("show", f"{commit}:{f}", binary=True)
        except PickError:
            continue  # added after the map's commit: file level
        try:  # the server served the working copy: its line ends (CRLF on a Windows checkout) are in the offsets
            crlf = b"\r\n" in (ROOT / f).read_bytes()
        except OSError:
            crlf = False
        if crlf and b"\r\n" not in old:
            old = old.replace(b"\n", b"\r\n")
        diff = git("diff", "-U0", "--no-renames", commit, "--", f)
        ranges[f] = byte_ranges(old, parse_hunks(diff))
        sizes[f] = utf16_line_starts(old)[1]
    return ranges, sizes


def classes_now() -> list[str]:
    os.environ[BROWSER_SWITCH] = "1"  # the browser classes import as they do in a browser run
    from qa import run_tests
    return sorted(run_tests.discover([BROWSER_PREFIX]))


def _age(made: str) -> str:
    try:
        s = time.time() - time.mktime(time.strptime(made, "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return "age unknown"
    return f"{s / 3600:.1f} h old" if s >= 3600 else f"{s / 60:.0f} min old"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m qa.browser_pick", description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", help="the revision to diff the working tree against (default: the map's commit)")
    ap.add_argument("--map", default=str(MAP_DIR), help=f"the map folder (default {MAP_DIR})")
    ap.add_argument("--list", action="store_true", help="one name per line")
    args = ap.parse_args(argv)

    now = classes_now()
    code = 0
    try:
        py_map, js_map = read_map(Path(args.map))
        if py_map is None:
            code = 2
            picked, reasons = pick(now, None, {}, [], {})
        else:
            commit = py_map.get("commit") or ""
            if not commit and not args.base:
                raise PickError("the map has no commit")
            files = changed_files(args.base or commit)
            ranges, sizes = js_changes(commit, files, js_map) if commit else ({}, {})
            picked, reasons = pick(now, py_map, js_map, files, ranges, sizes)
            reasons.append(f"map: commit {commit[:10] or '?'}, {_age(py_map.get('made'))}; "
                           f"{len(files)} changed files since {args.base or 'the map commit'}")
    except PickError as e:
        code = 2
        picked, reasons = sorted(now), [f"everything: {e}"]
    for r in reasons:
        print(r, file=sys.stderr)
    print("\n".join(picked) if args.list else " ".join(only_args(picked, now)))
    return code


def only_args(picked: list[str], classes_now) -> list[str]:
    """`--only` arguments for python -m qa.run_tests --browser: the picked tests' classes (qa.run_tests filters
    classes by prefix, so a method's name would match none), or `--only tests.web` when every class is picked."""
    now = set(classes_now)
    classes = sorted({_class_of(n, now) or n for n in picked})
    if now and set(classes) >= now:
        return ["--only", BROWSER_PREFIX]
    return [a for c in classes for a in ("--only", c)]


if __name__ == "__main__":
    sys.exit(main())

"""Write the screen as a static site that only replays saved runs:

    python -m hiraku.webserve.export_static --out <folder> [--saved showcase] [--run <run_id> ...]
                                            [--default-run <run_id>] [--no-scan] [--words-from <hook>]

The folder holds web/ (the files git knows, tracked or new, so nothing ignored), its pages marked
<meta name="hiraku-static" content="1"> (web/js/source.js), and under data/ what the screen GETs
from hiraku.webserve, written beforehand by the server's own functions so the answers are the same:

    data/runs.json                       GET /api/runs (list_runs over the saved runs; no runs/)
    data/launch.json                     GET /api/launch as without a launcher ({"available": false})
    data/shelf.json                      GET /api/shelf once ready (Shelf.compute: the known-answer checks run here)
    data/assets.json                     GET /api/assets (list_assets)
    data/modules.json                    GET /api/modules (modules_info)
    data/formulas.json                   GET /api/formulas (formula_titles)
    data/runs/<id>/light.jsonl.gz        the run's events.jsonl (its complete lines), gzipped, with the
                                         heavy parts taken out (split_record): what the first screen and the
                                         run's state need. The page reads it first and unpacks it itself
                                         (DecompressionStream), so no host compression is needed
    data/runs/<id>/heavy/<sha256 16>.json.gz
                                         one heavy value (a mesh's nodes, an analysis' fields...), named by
                                         the hash of its JSON; read when the screen draws it (web/js/heavy.js)
    data/runs/<id>/tools.json            GET /api/runs/<id>/tools (tool_versions)
    data/runs/<id>/tools/<file>          GET /api/runs/<id>/tools/<file>
    data/static.json                     the export's other pages (extras), for the run list, and the
                                         run a bare page opens on ① (first: first_run)
    layout.json, pets/pet3d.js           GET /layout.json, GET /pets/pet3d.js (promo's, as they are)
    n10/                                 the saved N10 answers (showcase/n10) and a page that shows them

Every path in the pages is relative, so the folder works under any path of a host (/hiraku/...).

Versions (version_site): every reference to a JS, CSS or data file carries ?v=<short sha256>, so a
browser never mixes files of two exports. A JS file's version is the hash of everything it imports,
itself included (its import closure), so a change anywhere below it changes its own address too.
The pages list the data files' versions in <script type="application/json" id="hiraku-versions">
(web/js/source.js reads it); a heavy part's name is the hash of what it holds.

At the end the folder is read the way qa.judge_scan reads a branch (Scan.check_line: the private
words and paths from the private hook, e-mail addresses, home folders, keys) plus any drive path
(C:/...), the gzipped records unpacked. Errors make the exit code 1; nothing is removed by itself.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from hiraku.contract.layout import EVENTS_FILE, TOOLS_DIR
from hiraku.webserve.server import (
    LAYOUT_FILE,
    PETS_FILE,
    REPO_DIR,
    SAMPLE_EVENTS,
    SAMPLE_ID,
    SAVED_DIR,
    WEB_DIR,
    _TOOL_FILE_RE,
    formula_titles,
    list_assets,
    list_runs,
    modules_info,
    tool_versions,
)
from hiraku.webserve.shelf import Shelf

STATIC_META = '<meta name="hiraku-static" content="1">'
MARK_FILE = ".hiraku-static"  # an export's folder: a new export may empty it
PAGES = ("index.html", "cut.html")
N10_DIR = "n10"
DRIVE_PATH_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:(?:\\\\|\\|/)+[A-Za-z]")
SCAN_SUFFIXES = {".html", ".js", ".mjs", ".css", ".json", ".jsonl", ".md", ".txt", ".py", ".svg"}


def _json_bytes(obj: Any) -> bytes:
    """As the server writes a JSON answer (Handler._send_json)."""
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def web_files(web_dir: Path) -> list[Path]:
    """The screen's files: those git knows under web_dir (tracked, or new and not ignored); every
    file when web_dir is not in a git checkout."""
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "."],
                             cwd=web_dir, capture_output=True, check=True).stdout
        names = [n for n in out.decode("utf-8").split("\0") if n]
        files = [web_dir / n for n in names]
        return sorted(p for p in files if p.is_file())
    except (OSError, subprocess.CalledProcessError):
        return sorted(p for p in web_dir.rglob("*") if p.is_file())


def mark_static(text: str) -> str:
    """A page with the static mark right after its charset (source.js reads it)."""
    if STATIC_META in text:
        return text
    head = '<meta charset="utf-8">'
    if head in text:
        return text.replace(head, f"{head}\n{STATIC_META}", 1)
    return text.replace("<head>", f"<head>\n{STATIC_META}", 1)


def complete_lines(data: bytes) -> bytes:
    """The complete lines of a record (the server leaves a line still being written)."""
    end = data.rfind(b"\n")
    return data[: end + 1] if end >= 0 else b""


KIND_RE = re.compile(rb'"kind"\s*:\s*"([a-z_.]+)"')
# what ① and ② show (web/js/tabs.js TAB_NEEDS A1, A2): the request (run.started's want), the picked
# domains, and the hearing asked and answered
FIRST_NEEDS = ("run.started", "domains.picked", "hearing.asked", "hearing.answered")


def record_kinds(path: Path) -> set[str]:
    """The event kinds in a record (read from the start of each line, where `kind` is written)."""
    kinds: set[str] = set()
    with path.open("rb") as f:
        for line in f:
            m = KIND_RE.search(line, 0, 400) or KIND_RE.search(line)
            if m:
                kinds.add(m.group(1).decode("ascii"))
    return kinds


def first_run(runs: list[dict[str, Any]], saved_dir: Path) -> dict[str, Any] | None:
    """The run a bare static page opens on ①: a finished saved run whose record has the request and
    everything ① and ② show (FIRST_NEEDS); the recommended run (default) when it has, else the
    newest that has. None when no run has (the page then opens on the run list)."""
    ok = [r for r in runs if r["saved"] and r["finished"] and r.get("want")
          and set(FIRST_NEEDS) <= record_kinds(saved_dir / r["run_id"] / EVENTS_FILE)]
    if not ok:
        return None
    pick = next((r for r in ok if r["default"]), None) or max(ok, key=lambda r: r["run_id"])
    why = "the recommended run" if pick["default"] else "the newest run"
    return {"run": pick["run_id"], "why": f"{why} whose record has {', '.join(FIRST_NEEDS)}"}


# --- the record split in a light part and heavy parts --------------------------------------------
LIGHT_FILE = "light.jsonl.gz"
HEAVY_DIR = "heavy"
# the kinds whose big data values are read only when drawn (a mesh's nodes and elements; an
# analysis' fields, series, assembly...); a value is heavy when its JSON has HEAVY_MIN_BYTES or more
HEAVY_KINDS = ("mesh.created", "analysis.finished")
HEAVY_MIN_BYTES = 16_000
# kept in the light record whatever their size: what web/js/state.js writes in the log (an analysis' results)
LIGHT_KEYS = ("results",)
_KEY_RE = re.compile(r"[A-Za-z0-9_]+")
# web/js/series.js motionSpan: the channels that make a motion
_POSE = ("body_x", "body_y", "body_z", "roll", "pitch", "yaw")
NODE_MOTION_PLAY_S = 4  # web/js/node_motion.js NODE_MOTION_PLAY_S
# web/js/meshkind.js kindCounts
_KINDS = ("quad4", "tri3", "hex8", "hex20")
_KIND_COUNT = {"tri3": 3, "quad4": 4, "hex8": 8, "hex20": 20}
_BY_COUNT = {3: "tri3", 4: "quad4", 8: "hex8", 20: "hex20"}


def short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _no_constant(name: str) -> Any:
    raise ValueError(name)  # NaN / Infinity: JSON.parse refuses them, so such a line is kept as it is


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _js_truthy(v: Any) -> bool:
    return v is not None and v is not False and v != "" and not (_finite(v) and v == 0)


def motion_span(d: dict[str, Any]) -> float | None:
    """web/js/series.js motionSpan: seconds of motion in an analysis' series, or None."""
    ch: dict[Any, dict] = {}
    for s in d.get("series") or []:
        if not isinstance(s, dict):
            continue
        t, v = s.get("t_s"), s.get("values")
        if isinstance(t, list) and isinstance(v, list) and t and len(t) == len(v):
            ch[s.get("channel")] = s
    if not any(isinstance(n, str) and (n in _POSE or n.startswith("wheel_center_")) for n in ch):
        return None
    return max(s["t_s"][-1] for s in ch.values()) - min(s["t_s"][0] for s in ch.values())


def has_node_motion(d: dict[str, Any]) -> bool:
    """web/js/node_motion.js nodeMotionOf: is there a well-formed node_motion field?"""
    if d.get("status") != "ok":
        return False
    field = next((f for f in d.get("fields") or [] if isinstance(f, dict) and f.get("kind") == "node_motion"), None)
    fr = field.get("frames") if field else None
    t = fr.get("t_s") if isinstance(fr, dict) else None
    pos = fr.get("positions_mm") if isinstance(fr, dict) else None
    if not isinstance(field.get("nodes") if field else None, list) or not isinstance(t, list) or not t \
            or not isinstance(pos, list) or len(pos) != len(t):
        return False
    n = len(field["nodes"])
    if not all(_finite(x) for x in t) or any(t[i] < t[i - 1] for i in range(1, len(t))):
        return False
    return all(isinstance(lst, list) and len(lst) == n
               and all(isinstance(p, list) and len(p) == 3 and all(_finite(c) for c in p) for p in lst) for lst in pos)


def kind_counts(d: dict[str, Any]) -> dict[str, int]:
    """web/js/meshkind.js kindCounts: elements of each kind ("other" for none known)."""
    out: dict[str, int] = {}
    given = d.get("element_kind")
    for i, e in enumerate(d.get("elements") or []):
        n = len(e) if isinstance(e, list) else -1
        g = given[i] if isinstance(given, list) and i < len(given) else None
        k = g if g in _KINDS and n == _KIND_COUNT[g] else _BY_COUNT.get(n, "other")
        out[k] = out.get(k, 0) + 1
    return out


def lite_of(kind: str, d: dict[str, Any]) -> dict[str, Any]:
    """What the screen reads of an event's heavy values before they are there (web/js/state.js's log
    line of a mesh, app.js's pace after an analysis), computed as the screen computes it."""
    if kind == "mesh.created":
        n = lambda v: len(v) if isinstance(v, list) else 0  # noqa: E731
        return {"nodes": n(d.get("nodes_mm")), "elements": n(d.get("elements")), "kinds": kind_counts(d)}
    pace = motion_span(d) if _js_truthy(d.get("series")) else None
    if pace is None and has_node_motion(d):
        pace = NODE_MOTION_PLAY_S
    return {"pace": pace}


def split_record(data: bytes) -> tuple[bytes, dict[str, bytes]]:
    """A record's complete lines -> (the light lines, {heavy file name: gzipped JSON}). A line of a
    HEAVY_KINDS event loses its data values of HEAVY_MIN_BYTES or more; it gets `_heavy` (key ->
    the part's file under heavy/, named by its hash) and `_lite` (lite_of, and arrays: the length
    of each value that is a list). Every other line is kept as
    it is, byte for byte (a line that is not a JSON object too: the screen counts it as before)."""
    lines: list[bytes] = []
    parts: dict[str, bytes] = {}
    for line in data.splitlines():
        try:
            ev = json.loads(line, parse_constant=_no_constant)
        except ValueError:
            ev = None
        d = ev.get("data") if isinstance(ev, dict) else None
        if not isinstance(d, dict) or ev.get("kind") not in HEAVY_KINDS:
            lines.append(line)
            continue
        big = {}
        for key, value in d.items():
            if not _KEY_RE.fullmatch(key) or key in LIGHT_KEYS:
                continue
            text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if len(text) >= HEAVY_MIN_BYTES:
                big[key] = text
        if not big:
            lines.append(line)
            continue
        refs = {}
        for key, text in big.items():
            # named by the hash of what it holds: the name is its version, and the same value in two
            # events (an assembly sent again) is one file
            raw = text.encode("utf-8")
            name = f"{hashlib.sha256(raw).hexdigest()[:16]}.json.gz"
            if name not in parts:
                parts[name] = gzip.compress(raw, compresslevel=9, mtime=0)
            refs[key] = f"{HEAVY_DIR}/{name}"
        # (arrays: the keys whose value is a list and its length; the screen has [] for them until read)
        lite = {**lite_of(ev["kind"], d), "arrays": {k: len(d[k]) for k in big if isinstance(d[k], list)}}
        light = {**ev, "data": {k: v for k, v in d.items() if k not in big}, "_heavy": refs, "_lite": lite}
        lines.append(json.dumps(light, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return b"".join(line + b"\n" for line in lines), parts


def join_record(light: bytes, parts: dict[str, bytes]) -> list[Any]:
    """The events of a split record with their heavy values back in (for the tests): what the
    screen has once every part is read."""
    out = []
    for line in light.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            out.append(None)
            continue
        refs = ev.pop("_heavy", None) if isinstance(ev, dict) else None
        if refs:
            ev.pop("_lite", None)
            for key, ref in refs.items():
                ev["data"][key] = json.loads(gzip.decompress(parts[ref.split("/", 1)[1]]))
        out.append(ev)
    return out


# --- versions --------------------------------------------------------------------------------------
# a relative module specifier: import ... from "./x.js", export ... from "../x.js", import "./x.js",
# import("./x.js"), new URL("./x.css", import.meta.url)
_SPEC_RES = (
    re.compile(r"""(\bfrom\s*)(["'])(\.\.?/[^"'?#\s]+)\2"""),
    re.compile(r"""(\bimport\s*)(["'])(\.\.?/[^"'?#\s]+)\2"""),
    re.compile(r"""(\bimport\(\s*)(["'])(\.\.?/[^"'?#\s]+)\2"""),
    re.compile(r"""(\bnew\s+URL\(\s*)(["'])(\.\.?/[^"'?#\s]+)\2(?=\s*,\s*import\.meta\.url)"""),
)
# a page's own references: <script src="js/app.js">, <link rel="stylesheet" href="css/app.css">
_PAGE_REF_RE = re.compile(r"""(<(?:script|link)\b[^>]*?\b(?:src|href)=)(["'])([^"':?#]+\.(?:js|css))\2""")
VERSIONS_ID = "hiraku-versions"


def _specs(text: str) -> list[str]:
    return [m.group(3) for r in _SPEC_RES for m in r.finditer(text)]


def version_site(out: Path) -> dict[str, str]:
    """Put ?v=<hash> on every reference to a JS or CSS file (the pages' script and link tags, every
    relative import of every module, inline modules too) and the data files' versions into the
    pages (VERSIONS_ID). A module's version is the hash of its import closure (its own text and every
    module it reaches), so no export's module is ever read with another export's imports. Returns
    {path: version} of every file but the pages and the heavy parts (theirs are in the light records)."""
    files = {p.relative_to(out).as_posix(): p for p in sorted(out.rglob("*")) if p.is_file()}
    text = {rel: p.read_text(encoding="utf-8") for rel, p in files.items() if p.suffix in (".js", ".mjs", ".html")}

    def target(rel: str, spec: str) -> str | None:
        parts = rel.split("/")[:-1]
        for seg in spec.split("/"):
            if seg == "..":
                if not parts:
                    return None
                parts.pop()
            elif seg not in (".", ""):
                parts.append(seg)
        t = "/".join(parts)
        return t if t in files else None

    deps = {rel: {t for s in _specs(text[rel]) if (t := target(rel, s))} for rel in text if not rel.endswith(".html")}
    own = {rel: short_hash(p.read_bytes()) for rel, p in files.items()}
    versions: dict[str, str] = {}
    for rel in files:
        if rel.endswith(".html") or rel == MARK_FILE or f"/{HEAVY_DIR}/" in rel:
            continue
        seen, todo = set(), [rel]
        while todo:
            r = todo.pop()
            if r not in seen:
                seen.add(r)
                todo.extend(deps.get(r, ()))
        versions[rel] = own[rel] if seen == {rel} else short_hash("\n".join(f"{r} {own[r]}" for r in sorted(seen)).encode("utf-8"))

    def with_v(rel: str, m: re.Match) -> str:
        t = target(rel, m.group(3))
        if t is None or t not in versions:
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{m.group(3)}?v={versions[t]}{m.group(2)}"

    data_map = json.dumps(dict(sorted(versions.items())), ensure_ascii=False, separators=(",", ":"))
    for rel, src in text.items():
        new = src
        for r in _SPEC_RES:
            new = r.sub(lambda m, rel=rel: with_v(rel, m), new)
        if rel.endswith(".html"):
            new = _PAGE_REF_RE.sub(lambda m, rel=rel: with_v(rel, m), new)
            if STATIC_META in new:
                new = new.replace(STATIC_META, f'{STATIC_META}\n<script type="application/json" id="{VERSIONS_ID}">{data_map}</script>', 1)
        if new != src:
            files[rel].write_text(new, encoding="utf-8", newline="")
    return versions


def _prepare_out(out: Path) -> None:
    if out.exists():
        if not out.is_dir():
            raise SystemExit(f"{out} is a file")
        if any(out.iterdir()):
            if not (out / MARK_FILE).is_file():
                raise SystemExit(f"{out} is not empty and not an earlier export (no {MARK_FILE}): give an empty folder")
            shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / MARK_FILE).write_text("written by python -m hiraku.webserve.export_static\n", encoding="utf-8")


def _n10_page(src: Path) -> str:
    """A page with the N10 questions and Claude's answers (showcase/n10), escaped as text."""
    questions = [
        ("段差 100 mm: 前輪 1 輪にかかる水平の力", "prompt.txt", "answer"),
        ("傾斜台: 上側の輪が浮く角度", "prompt_tilt.txt", "tilt_answer"),
        ("段差 100 mm: フレームの最大の応力", "prompt_stress.txt", "stress_answer"),
    ]
    parts = []
    for title, prompt, stem in questions:
        if not (src / prompt).is_file():
            continue
        parts.append(f"<h2>{html.escape(title)}</h2><h3>問い (<a href=\"{prompt}\">{prompt}</a>)</h3>"
                     f"<pre>{html.escape((src / prompt).read_text(encoding='utf-8'))}</pre>")
        for k in (1, 2, 3):
            f = src / f"{stem}_{k}.json"
            if not f.is_file():
                continue
            try:
                result = json.loads(f.read_text(encoding="utf-8")).get("result", "")
            except (OSError, ValueError):
                result = ""
            parts.append(f"<h3>答え {k} (<a href=\"{f.name}\">{f.name}</a>)</h3><pre>{html.escape(str(result))}</pre>")
    return (
        "<!doctype html>\n<html lang=\"ja\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>N10: ツールなしの Claude の答え</title>\n<link rel=\"icon\" href=\"data:,\">\n"
        "<style>body{font-family:sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;line-height:1.6}"
        "pre{white-space:pre-wrap;background:#f4f1ea;padding:.8rem;border-radius:4px}</style>\n</head>\n<body>\n"
        "<p><a href=\"../?list=1\">☰ 一覧へ</a></p>\n"
        "<h1>N10: ツールなしの Claude に、同じ問いを聞いた答え</h1>\n"
        "<p>HIRAKU のツールを使わずに Claude に聞いた答え (各 3 回)。くわしくは <a href=\"README.md\">README.md</a>。</p>\n"
        + "\n".join(parts) + "\n</body>\n</html>\n"
    )


def export(
    out: Path | str,
    saved_dir: Path | str = SAVED_DIR,
    run_ids: list[str] | None = None,
    default_run: str | None = None,
    web_dir: Path | str = WEB_DIR,
    layout_file: Path | str = LAYOUT_FILE,
    pets_file: Path | str = PETS_FILE,
    shelf: Shelf | None = None,
    assets: bool = True,
    sample: bool = True,
) -> dict[str, Any]:
    """Write the static site to `out` (see the module's doc). run_ids: the saved runs to take
    (None: all). Returns {"out", "runs", "files": {relative path: bytes}}."""
    out, saved_dir, web_dir = Path(out), Path(saved_dir), Path(web_dir)
    _prepare_out(out)
    # the screen's files, its pages marked
    for src in web_files(web_dir):
        rel = src.relative_to(web_dir)
        if rel.parts[0] in ("data", N10_DIR, "pets") or rel.name in ("layout.json", MARK_FILE):
            raise SystemExit(f"web/{rel.as_posix()} would be overwritten by the export's own files")
        if rel.as_posix() in PAGES:
            _write(out / rel, mark_static(src.read_text(encoding="utf-8")).encode("utf-8"))
        else:
            _write(out / rel, src.read_bytes())
    data = out / "data"
    # the run list as the server makes it (no runs/: the saved runs and the sample)
    with tempfile.TemporaryDirectory() as none:
        runs = list_runs(Path(none) / "runs", saved_dir, default_run)
    runs = [r for r in runs if (r["sample"] and sample) or (r["saved"] and (run_ids is None or r["run_id"] in run_ids))]
    if run_ids is not None:
        missing = sorted(set(run_ids) - {r["run_id"] for r in runs})
        if missing:
            raise SystemExit(f"no saved run {', '.join(missing)} in {saved_dir}")
    if runs and not any(r["default"] for r in runs):
        pick = next((r for r in runs if r["saved"]), None)  # (the server's rule, over the runs taken)
        if pick:
            pick["default"] = True
    _write(data / "runs.json", _json_bytes(runs))
    for r in runs:
        rid = r["run_id"]
        src = SAMPLE_EVENTS if rid == SAMPLE_ID else saved_dir / rid / EVENTS_FILE
        light, parts = split_record(complete_lines(src.read_bytes()))
        _write(data / "runs" / rid / LIGHT_FILE, gzip.compress(light, compresslevel=9, mtime=0))
        for name, gz in parts.items():
            _write(data / "runs" / rid / HEAVY_DIR / name, gz)
        if rid == SAMPLE_ID:
            continue
        tools = src.parent / TOOLS_DIR
        _write(data / "runs" / rid / "tools.json", _json_bytes({"tools": tool_versions(tools)}))
        if tools.is_dir():
            for f in sorted(tools.iterdir()):
                if f.is_file() and _TOOL_FILE_RE.match(f"/api/runs/{rid}/tools/{f.name}"):
                    _write(data / "runs" / rid / "tools" / f.name, f.read_bytes())
    # the other GET answers
    _write(data / "launch.json", _json_bytes({"available": False}))
    _write(data / "shelf.json", _json_bytes((shelf or Shelf()).compute()))
    _write(data / "assets.json", _json_bytes({"files": list_assets(web_dir) if assets else []}))
    _write(data / "modules.json", _json_bytes(modules_info()))
    _write(data / "formulas.json", _json_bytes(formula_titles()))
    if Path(layout_file).is_file():
        _write(out / "layout.json", _json_bytes(json.loads(Path(layout_file).read_text(encoding="utf-8"))))
    if Path(pets_file).is_file():
        _write(out / "pets" / "pet3d.js", Path(pets_file).read_bytes())
    extras = []
    n10 = saved_dir / N10_DIR
    if n10.is_dir():
        for f in sorted(n10.iterdir()):
            if f.is_file():
                _write(out / N10_DIR / f.name, f.read_bytes())
        _write(out / N10_DIR / "index.html", _n10_page(n10).encode("utf-8"))
        extras.append({"href": f"{N10_DIR}/index.html", "title": "N10: ツールなしの Claude に同じ問いを聞いた答え"})
    first = first_run(runs, saved_dir)
    _write(data / "static.json", _json_bytes({"extras": extras, "first": first}))
    version_site(out)
    files ={p.relative_to(out).as_posix(): p.stat().st_size for p in sorted(out.rglob("*")) if p.is_file()}
    return {"out": str(out), "runs": [r["run_id"] for r in runs], "first": first, "files": files}


# --- what must not be published ------------------------------------------------------------------
def scan(out: Path | str, words_from: Path | None = None) -> tuple[list[Any], bool]:
    """The export read as qa.judge_scan reads a branch (Scan.check_line, its patterns) plus drive
    paths; gzipped records are read unpacked. Returns (findings, private words checked)."""
    from qa.judge_scan import Finding, Scan, default_hook

    out = Path(out)
    hook = words_from
    if hook is None:
        try:
            hook = default_hook(REPO_DIR)
        except (OSError, subprocess.CalledProcessError):
            hook = REPO_DIR / ".private" / "pre-commit"
    s = Scan(REPO_DIR, "HEAD", hook)
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
    p = argparse.ArgumentParser(prog="python -m hiraku.webserve.export_static", description=__doc__.split("\n\n")[0])
    p.add_argument("--out", required=True, help="folder to write (empty, or an earlier export, which is replaced)")
    p.add_argument("--saved", default=str(SAVED_DIR), help="folder of saved runs (default: showcase/ of this repository)")
    p.add_argument("--run", action="append", dest="runs", help="a saved run to take (repeat; default: all)")
    p.add_argument("--default-run", default=None, help="the run the run list recommends (default: the newest saved run)")
    p.add_argument("--no-sample", action="store_true", help="leave out the contract's sample record")
    p.add_argument("--no-scan", action="store_true", help="do not read the export for what must not be published")
    p.add_argument("--words-from", type=Path, help="private pre-commit hook with WORDS= and PATHS= lines (as qa.judge_scan)")
    args = p.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    got = export(args.out, saved_dir=args.saved, run_ids=args.runs, default_run=args.default_run, sample=not args.no_sample)
    files = got["files"]
    total = sum(files.values())
    big = max(files, key=files.get)
    print(f"wrote {len(files)} files, {total / 1e6:.1f} MB, to {got['out']}")
    print(f"  runs: {', '.join(got['runs'])}")
    first = got["first"]
    print(f"  first screen: {'① of ' + first['run'] + ' (' + first['why'] + ')' if first else 'the run list (no run has ① and ② in its record)'}")
    print(f"  largest: {big} ({files[big] / 1e6:.1f} MB)")
    if args.no_scan:
        return 0
    findings, checked = scan(args.out, args.words_from)
    errors = [f for f in findings if f.level == "error"]
    if not checked:
        print("  scan: no private word patterns found (the private hook); private words not checked")
    print(f"  scan: {len(errors)} error(s)")
    for f in findings:
        print(f"    {f}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

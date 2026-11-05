"""The browser tests' Playwright, with two things on every page they open.

Tests import `sync_playwright` from here instead of from playwright.sync_api. It is
Playwright's own; the only difference is that `pw.chromium.launch(...)` returns a browser
whose `new_page` and `new_context` give pages that

1. take three.js from a cache outside the repository instead of the CDN. Each page is a
   new browser context, so the browser's own HTTP cache is not shared between pages; every
   page used to load three and its addons from cdn.jsdelivr.net again. The cache folder is
   HIRAKU_THREE_CACHE, or <temp>/hiraku-three-cache; the path under it is the URL after
   /npm/ (three@0.170.0/...), so the version is part of the path. A file that is not there
   yet is fetched once, written under a temporary name and moved into place with
   os.replace, so tests running in parallel never read half a file. HIRAKU_THREE_CACHE=off: no
   route (the CDN, as before). Each page's default timeout is 120 s (DEFAULT_TIMEOUT_MS).
2. with HIRAKU_WEB_MAP=<folder> only: record which functions of web/js ran (the Chrome
   DevTools protocol's precise coverage), one JSON file per page,
   <folder>/<pid>-<n>.json. Without HIRAKU_WEB_MAP nothing is started.

It works the same under `python -m unittest` and `python -m tests.web.browser`: the change
is in the import, not in the runner.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from urllib.parse import urlparse

try:
    from playwright.sync_api import sync_playwright as _sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    _sync_playwright = None

THREE_URL = "https://cdn.jsdelivr.net/npm/three@0.170.0/**"
_NPM = "https://cdn.jsdelivr.net/npm/"
_TYPES = {".js": "text/javascript; charset=utf-8", ".json": "application/json", ".wasm": "application/wasm"}

# What the route did in this process: files given from the cache / fetched from the CDN.
STATS = {"cache": 0, "fetched": 0}
_lock = threading.Lock()


def cache_dir() -> Path:
    env = os.environ.get("HIRAKU_THREE_CACHE")
    return Path(env) if env else Path(tempfile.gettempdir()) / "hiraku-three-cache"


def cache_on() -> bool:
    """HIRAKU_THREE_CACHE=off: no route, three.js from the CDN as without this module."""
    return os.environ.get("HIRAKU_THREE_CACHE", "").lower() != "off"


# Playwright's default for a navigation or an action that was given no timeout is 30 s. With
# several browser tests side by side a heavy page's goto took longer than that (2026-11-05: four
# heavy screen tests repeated beside two more, goto timeouts in tests that pass alone; the pages
# open in about 3 s each with six side by side, with or without this cache). A timeout written in
# a test is kept as written.
DEFAULT_TIMEOUT_MS = 120_000


def _route(context) -> None:
    context.set_default_timeout(DEFAULT_TIMEOUT_MS)
    context.set_default_navigation_timeout(DEFAULT_TIMEOUT_MS)
    if cache_on():
        context.route(THREE_URL, _three_route)


def cache_path(url: str) -> Path | None:
    """Where the file of a CDN URL is kept, or None when the URL is not one to keep."""
    if not url.startswith(_NPM):
        return None
    rel = url[len(_NPM):].split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in rel.split("/") if p]
    if not parts or not parts[0].startswith("three@") or any(p in (".", "..") or ":" in p or "\\" in p for p in parts):
        return None
    return cache_dir().joinpath(*parts)


def _store(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.part")
    part.write_bytes(body)
    try:
        os.replace(part, path)
    except OSError:  # another process put the same file there and has it open (Windows)
        try:
            part.unlink()
        except OSError:
            pass


def _read(path: Path) -> bytes | None:
    """The kept file, or None when it is not there. On Windows, opening a file at the moment
    another process os.replace()s it raises PermissionError; that is tried again."""
    for _ in range(40):
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except PermissionError:
            time.sleep(0.05)
    return None


def _three_route(route) -> None:
    url = route.request.url
    path = cache_path(url)
    if path is None:
        route.continue_()
        return
    body = _read(path)
    if body is not None:
        with _lock:
            STATS["cache"] += 1
        route.fulfill(status=200, body=body, headers={
            "content-type": _TYPES.get(path.suffix, "application/octet-stream"),
            "access-control-allow-origin": "*",  # module scripts from another origin need it
        })
        return
    resp = route.fetch()
    body = resp.body()
    if resp.ok:
        _store(path, body)
    with _lock:
        STATS["fetched"] += 1
    route.fulfill(response=resp, body=body)


# --- the map (HIRAKU_WEB_MAP) ---

_seq = 0


def map_dir() -> Path | None:
    env = os.environ.get("HIRAKU_WEB_MAP")
    return Path(env) if env else None


def current_test() -> str:
    """The unittest test this call is in: its id(), or the class in setUpClass."""
    f = sys._getframe(1)
    while f is not None:
        loc = f.f_locals
        me = loc.get("self")
        if isinstance(me, unittest.TestCase):
            return me.id()
        cls = loc.get("cls")
        if isinstance(cls, type) and issubclass(cls, unittest.TestCase):
            return f"{cls.__module__}.{cls.__qualname__}"
        f = f.f_back
    return ""


def repo_path(url: str) -> str | None:
    """web/js/... for a script the test server served from /js/..., else None."""
    u = urlparse(url)
    if u.hostname not in ("127.0.0.1", "localhost") or not u.path.startswith("/js/"):
        return None
    return "web" + u.path


def coverage_map(result: list[dict]) -> dict[str, list]:
    """Profiler.takePreciseCoverage's result -> {web/js/x.js: [[start, end, name], ...]}.

    Only each function's outer range (ranges[0]) and only those that ran (count > 0)."""
    out: dict[str, list] = {}
    for script in result:
        key = repo_path(script.get("url", ""))
        if key is None:
            continue
        seen = out.setdefault(key, [])
        for fn in script.get("functions", []):
            ranges = fn.get("ranges") or []
            if not ranges or ranges[0].get("count", 0) <= 0:
                continue
            item = [ranges[0]["startOffset"], ranges[0]["endOffset"], fn.get("functionName", "")]
            if item not in seen:
                seen.append(item)
    for v in out.values():
        v.sort()
    return out


class _Map:
    """One page's coverage session; write() takes it once and writes the file."""

    def __init__(self, page, folder: Path):
        self.page, self.folder, self.test = page, folder, current_test()
        self.done = False
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.send("Profiler.enable")
        self.cdp.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})

    def write(self) -> None:
        if self.done:
            return
        self.done = True
        global _seq
        try:
            url = self.page.url
            result = self.cdp.send("Profiler.takePreciseCoverage").get("result", [])
        except Exception:  # the page or the browser is gone; nothing to write
            return
        with _lock:
            _seq += 1
            n = _seq
        self.folder.mkdir(parents=True, exist_ok=True)
        doc = {"test": self.test, "url": url, "scripts": coverage_map(result)}
        (self.folder / f"{os.getpid()}-{n}.json").write_text(json.dumps(doc), encoding="utf-8")


def _watch_page(page, maps: list) -> None:
    folder = map_dir()
    if folder is None:
        return
    m = _Map(page, folder)
    maps.append(m)
    real_close = page.close

    def close(*a, **kw):
        m.write()
        return real_close(*a, **kw)

    page.close = close


def _flush(maps: list, context=None) -> None:
    for m in list(maps):
        if context is None or m.page.context is context:
            m.write()
            maps.remove(m)


def _wrap_context(context, maps: list):
    _route(context)
    real_new_page, real_close = context.new_page, context.close

    def new_page(*a, **kw):
        page = real_new_page(*a, **kw)
        _watch_page(page, maps)
        return page

    def close(*a, **kw):
        _flush(maps, context)
        return real_close(*a, **kw)

    context.new_page, context.close = new_page, close
    return context


def _wrap_browser(browser):
    maps: list = []
    real_new_page, real_new_context, real_close = browser.new_page, browser.new_context, browser.close

    def new_page(*a, **kw):
        page = real_new_page(*a, **kw)
        ctx = page.context  # the page's own context (closed with the page)
        _route(ctx)
        _watch_page(page, maps)
        if map_dir() is not None:
            real_ctx_close = ctx.close

            def ctx_close(*a, **kw):
                _flush(maps, ctx)
                return real_ctx_close(*a, **kw)

            ctx.close = ctx_close
        return page

    def new_context(*a, **kw):
        return _wrap_context(real_new_context(*a, **kw), maps)

    def close(*a, **kw):
        _flush(maps)  # pages the test left open
        return real_close(*a, **kw)

    browser.new_page, browser.new_context, browser.close = new_page, new_context, close
    return browser


def _wrap_playwright(pw):
    real_launch = pw.chromium.launch

    def launch(*a, **kw):
        return _wrap_browser(real_launch(*a, **kw))

    pw.chromium.launch = launch
    return pw


class _Starter:
    def __init__(self):
        self._cm = _sync_playwright()

    def start(self):
        return _wrap_playwright(self._cm.start())

    def __enter__(self):
        return _wrap_playwright(self._cm.__enter__())

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)


def _sync_playwright_wrapped():
    return _Starter()


sync_playwright = _sync_playwright_wrapped if _sync_playwright is not None else None

"""Open viewer/index.html#<field> from disk in headless Chromium (Python Playwright), wait for the
page, step the time and pick a member, and print a JSON summary (console errors, what the page shows).

Usage: python tests/viewer/page_check.py <viewer dir> <field> [screenshot.png] [time index]
Playwright is installed for the system Python (py), not the venv."""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def check(viewer: Path, field: str, shot: str | None = None, t: int | None = None) -> dict:
    url = (Path(viewer).resolve() / "index.html").as_uri() + "#" + field
    errors: list[str] = []
    out: dict = {"field": field}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"])
        page = b.new_page(viewport={"width": 1480, "height": 900})
        page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto(url)
        page.wait_for_function("window.__ready === true", timeout=120000)
        page.wait_for_timeout(300)
        out["shown_field"] = page.evaluate("window.__field")
        out["title"] = page.text_content("#title")
        out["nT"] = page.evaluate("HV.state.ctl ? HV.state.ctl.nT : 0")
        if t is not None:
            page.eval_on_selector("#time", f"e => {{ e.value = {t}; e.dispatchEvent(new Event('input')); }}")
            page.wait_for_timeout(200)
        out["tline"] = page.text_content("#tline")
        out["hud"] = page.text_content("#hudlines")
        out["notes"] = page.text_content("#notes")
        out["inputs"] = page.text_content("#inputs")[:600]
        out["legend_shown"] = page.evaluate("getComputedStyle(document.getElementById('legend')).display !== 'none'")
        # coloured pixels in the 3D view (not the background)
        out["drawn_px"] = page.evaluate("""() => { HV.view.draw(); const c = HV.view.renderer.domElement; const g = document.createElement('canvas');
            g.width = c.width; g.height = c.height; const x = g.getContext('2d'); x.drawImage(c, 0, 0);
            const d = x.getImageData(0, 0, g.width, g.height).data; let n = 0;
            for (let i = 0; i < d.length; i += 4) if (Math.abs(d[i] - 244) + Math.abs(d[i+1] - 244) + Math.abs(d[i+2] - 242) > 30) n++; return n; }""")
        # click the middle of the view until something is picked
        box = page.eval_on_selector("#view", "e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.width, r.height]; }")
        out["info"] = ""
        for fx, fy in ((0.5, 0.5), (0.45, 0.55), (0.55, 0.45), (0.4, 0.5), (0.6, 0.55), (0.5, 0.6), (0.35, 0.45), (0.65, 0.5)):
            page.mouse.click(box[0] + fx * box[2], box[1] + fy * box[3])
            page.wait_for_timeout(100)
            if page.is_visible("#info"):
                out["info"] = page.text_content("#info")
                break
        if shot:
            Path(shot).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=shot)
        out["errors"] = errors
        b.close()
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    r = check(Path(a[0]), a[1], a[2] if len(a) > 2 else None, int(a[3]) if len(a) > 3 else None)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(r, indent=1, ensure_ascii=False))

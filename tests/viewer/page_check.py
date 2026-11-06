"""Open viewer/index.html#<field> from disk in headless Chromium (Python Playwright), wait for the
page, step the time and pick a member, and print a JSON summary (console errors, what the page shows).

Usage: python tests/viewer/page_check.py <viewer dir> <field> [screenshot.png] [time index] [mode index]
<field> may be a data variant (nvh_shell opens the NVH field on the shell record).
Playwright is installed for the system Python (py), not the venv."""

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def check(viewer: Path, field: str, shot: str | None = None, t: int | None = None, mode: int | None = None) -> dict:
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
        out["variant"] = page.evaluate("HV.state.variant")
        if mode is not None:
            page.evaluate(f"HV.state.ctl.selectMode({mode})")
            page.wait_for_timeout(200)
        if t is not None:
            page.eval_on_selector("#time", f"e => {{ e.value = {t}; e.dispatchEvent(new Event('input')); }}")
            page.wait_for_timeout(200)
        out["tline"] = page.text_content("#tline")
        out["hud"] = page.text_content("#hudlines")
        out["notes"] = page.text_content("#notes") or ""
        out["notes_open"] = page.evaluate("!!(document.getElementById('notesBox') || {}).open")
        out["panel"] = page.text_content("#panel")
        out["page_text"] = page.evaluate("document.body.innerText")
        # visible text boxes that overlap the 3D view (none may)
        out["over_view"] = page.evaluate("""() => { const v = document.getElementById('view').getBoundingClientRect(); const bad = [];
            for (const id of ['hud', 'info', 'legend', 'notes', 'panel', 'bottom']) { const e = document.getElementById(id); if (!e) continue;
              const r = e.getBoundingClientRect(); if (r.width === 0 || r.height === 0 || getComputedStyle(e).display === 'none') continue;
              if (r.left < v.right - 1 && r.right > v.left + 1 && r.top < v.bottom - 1 && r.bottom > v.top + 1) bad.push(id); } return bad; }""")
        # the header, the plots, the inputs and the 3D view do not overlap each other
        out["layout_overlap"] = page.evaluate("""() => { const ids = ['hud', 'view', 'plotwrap', 'inputs', 'panel'], R = {}, bad = [];
            for (const id of ids) { const e = document.getElementById(id); if (e) R[id] = e.getBoundingClientRect(); }
            for (let i = 0; i < ids.length; i++) for (let j = i + 1; j < ids.length; j++) { const a = R[ids[i]], b = R[ids[j]]; if (!a || !b) continue;
              if (a.left < b.right - 1 && a.right > b.left + 1 && a.top < b.bottom - 1 && a.bottom > b.top + 1) bad.push(ids[i] + '/' + ids[j]); }
            const c = document.getElementById('plot').getBoundingClientRect(), w = R.plotwrap;
            if (w && (c.right > w.right + 1 || c.bottom > w.bottom + 1)) bad.push('plot canvas outside its box');
            return bad; }""")
        out["speed"] = page.evaluate("document.getElementById('speed').value")
        out["speed_shown"] = page.evaluate("getComputedStyle(document.getElementById('speedrow')).display !== 'none'")
        out["boxes_moved"] = page.evaluate("(() => { const b = HV.state.ctx && HV.state.ctx.boxes; return b ? b.off.filter(o => Math.hypot(o[0], o[1], o[2]) > 1e-3).length : 0; })()")
        out["tyres"] = page.evaluate("(() => { let n = 0; HV.state.ctx.root.traverse(o => { if (o.geometry && o.geometry.type === 'CylinderGeometry') n++; }); return n; })()")
        # the ground drawn (js/look.js) and, at t = 0, the lowest point of each drawn tyre (its world box) above it
        out["ground"] = page.evaluate("""() => { const T = window.THREE, ctx = HV.state.ctx, g = ctx && ctx.groundSpec;
            if (!g || !ctx.ground) return null; const tyres = [];
            HV.state.ctl.setTime(0); ctx.root.updateMatrixWorld(true);
            ctx.root.traverse(o => { if (o.isMesh && o.geometry && o.geometry.type === 'CylinderGeometry' && o.geometry.parameters.radiusTop > 150) {
              const b = new T.Box3().setFromObject(o, true); tyres.push(b.min.z - g.z); } });
            HV.state.ctl.setTime(HV.state.t);
            return { z: g.z, from: g.from, visible: ctx.ground.visible, tyre_gaps: tyres }; }""")
        out["surround"] = page.evaluate("(() => { const b = document.getElementById('surroundBox'); return b ? { open: b.open, text: b.textContent } : null; })()")
        out["inputs"] = page.text_content("#inputs")[:2000]
        out["switch"] = page.evaluate("[...document.querySelectorAll('#switch button')].map(b => b.textContent)")
        # the code names after the Japanese quantity names (small grey type) in the inputs pane
        out["codes"] = page.evaluate("[...document.querySelectorAll('#inputs .code')].map(e => e.textContent)")
        out["contour"] = page.evaluate("(() => { const c = HV.state.ctl && HV.state.ctl.contour && HV.state.ctl.contour(); return c ? { min: c.min, max: c.max, note: c.note || '' } : null; })()")
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
        # click a part box: the screen point of the first shown part box's centre (largest box first)
        out["part_pick"], out["part_info"] = {}, ""
        pts = page.evaluate("""() => { const T = window.THREE, ctx = HV.state.ctx, b = ctx && ctx.boxes; if (!b) return [];
            const r = HV.view.renderer.domElement.getBoundingClientRect(), M = new T.Matrix4(), v = new T.Vector3(), out = [];
            const order = b.parts.map((p, i) => i).filter(i => b.show[i]).sort((i, j) => b.parts[j].s[0] * b.parts[j].s[1] * b.parts[j].s[2] - b.parts[i].s[0] * b.parts[i].s[1] * b.parts[i].s[2]);
            for (const i of order.slice(0, 6)) { b.mesh.getMatrixAt(i, M); v.setFromMatrixPosition(M).project(HV.view.camera);
              out.push([r.left + (v.x + 1) / 2 * r.width, r.top + (1 - v.y) / 2 * r.height]); } return out; }""")
        for x, y in pts:
            page.evaluate("window.__lastPick = null")
            page.mouse.click(x, y)
            page.wait_for_timeout(100)
            pick = page.evaluate("window.__lastPick")
            if pick and pick.get("cat"):
                out["part_pick"], out["part_info"] = pick, page.text_content("#info")
                break
        if shot:
            Path(shot).parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=shot)
        out["errors"] = errors
        b.close()
    return out


if __name__ == "__main__":
    a = sys.argv[1:]
    r = check(Path(a[0]), a[1], a[2] or None if len(a) > 2 else None, int(a[3]) if len(a) > 3 else None,
              int(a[4]) if len(a) > 4 else None)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(r, indent=1, ensure_ascii=False))

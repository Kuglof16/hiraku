"""Check the layout of a screen at set times.

Opens a screen with export.Capture, moves it to each time, and collects the
boxes (getBoundingClientRect, in 1920x1080 design px) of the named elements
(``data-check="name"``) and of every visible element that holds text. Then
lists:

    band      a box reaches into the subtitle band (y >= band.top)
    outside   a box sticks out of the screen (0..1920 x 0..1080)
    overlap   two named elements overlap (pairs allowed in layout.json skipped)
    overflow  text overflows its box (scrollWidth > clientWidth + 1, or height)
    small     text smaller than the minimum size at 1080p (layout.json min_text)

An element counts as visible unless its opacity (with its ancestors') is below
0.05, it is visibility: hidden, it has no size, or an ancestor that clips
(overflow other than visible) hides it completely. Text drawn on a canvas
(WebGL) is not seen.

Writes result.json, report.md, one picture per time (long side 1280 px, the
boxes with problems in red) and sheet.jpg (all pictures on one sheet) into
OUT_DIR. Exits with 1 when there is any problem.

    py promo/layout_check.py promo/screens/n05.html OUT_DIR
    py promo/layout_check.py all OUT_DIR
    py promo/layout_check.py "http://127.0.0.1:8781/?run=sample&video=1" OUT_DIR --at 2 8 15

Times come from layout.json (check.times, by the page's name; "app" for the
app's root URL) unless --at is given. Needs playwright (as export.py) and Pillow.
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from promo import export  # noqa: E402

SCREENS = Path(__file__).resolve().parent / "screens"
LAYOUT = SCREENS / "layout.json"
CUTS = ["n00", "n05", "n08b", "n18"]
THUMB_LONG = 1280
SHEET_LONG = 1500
TOL = 0.5  # design px; boxes touching an edge are not counted

# Collects the boxes in the page.
# cfg: {w, h, bandExempt: [selector], overflowAllow: [{selector, axis, max_em}]}.
# Boxes and scroll sizes come back in design px.
COLLECT = r"""(cfg) => {
  const W = cfg.w, H = cfg.h;
  const u = Math.min(innerWidth / W, innerHeight / H);
  const ox = (innerWidth - W * u) / 2, oy = (innerHeight - H * u) / 2;
  const design = (r) => ({ x: (r.left - ox) / u, y: (r.top - oy) / u, w: r.width / u, h: r.height / u });
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "TITLE", "defs", "clipPath", "mask", "title"]);
  const JA = /[぀-ヿ㐀-鿿豈-﫿＀-￯]/;

  const ownText = (el) => [...el.childNodes].filter((n) => n.nodeType === 3)
    .map((n) => n.textContent).join("").replace(/\s+/g, " ").trim();
  const opacity = (el) => {
    let o = 1;
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) o *= parseFloat(getComputedStyle(e).opacity);
    return o;
  };
  const clippedAway = (el, r) => {
    let l = r.left, t = r.top, rr = r.right, b = r.bottom;
    // overflow on <html> and <body> clips the viewport, not their own boxes
    for (let e = el.parentElement; e && e !== document.body && e !== document.documentElement; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.overflowX === "visible" && cs.overflowY === "visible") continue;
      const q = e.getBoundingClientRect();
      l = Math.max(l, q.left); t = Math.max(t, q.top); rr = Math.min(rr, q.right); b = Math.min(b, q.bottom);
      if (rr - l <= 0 || b - t <= 0) return true;
    }
    return false;
  };
  const transparent = (c) => !c || c === "transparent" || /^rgba\(.*,\s*0\)$/.test(c);
  // Does the element (or something inside it) put any ink on the screen?
  const paints = (el, acc) => {
    if (SKIP.has(el.tagName)) return false;
    const cs = getComputedStyle(el);
    if (cs.display === "none") return false;
    const o = acc * parseFloat(cs.opacity);
    if (o < 0.05) return false;
    if (cs.visibility !== "hidden") {
      if (ownText(el)) return true;
      if (el instanceof SVGGeometryElement || el instanceof SVGImageElement) return true;
      if (/^(IMG|CANVAS|VIDEO|IFRAME)$/.test(el.tagName)) return true;
      if (cs.backgroundImage !== "none" || !transparent(cs.backgroundColor)) return true;
      for (const side of ["Top", "Right", "Bottom", "Left"])
        if (parseFloat(cs[`border${side}Width`]) > 0 && cs[`border${side}Style`] !== "none"
            && !transparent(cs[`border${side}Color`])) return true;
    }
    return [...el.children].some((c) => paints(c, o));
  };
  const label = (el) => {
    const one = (e) => {
      const cls = typeof e.className === "string" ? e.className.trim().split(/\s+/).filter(Boolean) : [];
      return e.tagName.toLowerCase() + (e.id ? "#" + e.id : "") + cls.slice(0, 2).map((c) => "." + c).join("");
    };
    const parts = [];
    for (let e = el; e && e !== document.body && parts.length < 3; e = e.parentElement) parts.unshift(one(e));
    return parts.join(" > ");
  };
  const fontPx = (el, cs) => {
    let size = parseFloat(cs.fontSize);
    if (el instanceof SVGElement && el.getScreenCTM) {
      const m = el.getScreenCTM();
      if (m) size *= Math.hypot(m.a, m.b);
    }
    return size / u;
  };

  const items = [];
  for (const el of document.body.querySelectorAll("*")) {
    if (SKIP.has(el.tagName)) continue;
    const name = el.getAttribute("data-check");
    const text = ownText(el);
    if (!name && !text) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none") continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (opacity(el) < 0.05) continue;
    if (clippedAway(el, r)) continue;
    if (name && !text && !paints(el, 1)) continue;
    const inside = [];
    for (let e = el.parentElement; e; e = e.parentElement) {
      const n = e.getAttribute && e.getAttribute("data-check");
      if (n) inside.push(n);
    }
    const block = el.clientWidth > 0 || el.clientHeight > 0;
    items.push({
      name: name || null,
      inside,
      label: name || label(el),
      text: text.slice(0, 60),
      box: design(r),
      font: text ? fontPx(el, cs) : null,
      script: text ? (JA.test(text) ? "ja" : "latin") : null,
      scroll: text && block ? { sw: el.scrollWidth / u, cw: el.clientWidth / u,
                                sh: el.scrollHeight / u, ch: el.clientHeight / u } : null,
      band_exempt: cfg.bandExempt.some((s) => el.closest(s)),
      overflow_allow: cfg.overflowAllow.filter((a) => el.matches(a.selector))
        .map((a) => ({ axis: a.axis, max_em: a.max_em })),
    });
  }
  return items;
}"""


# --- judging (pure: boxes in, problems out) -----------------------------------

def bottom(box: dict) -> float:
    return box["y"] + box["h"]


def right(box: dict) -> float:
    return box["x"] + box["w"]


def in_band(box: dict, band_top: float, tol: float = TOL) -> bool:
    """The box reaches below the top of the subtitle band."""
    return bottom(box) > band_top + tol


def outside(box: dict, w: float, h: float, tol: float = TOL) -> bool:
    """The box sticks out of the w x h screen on any side."""
    return box["x"] < -tol or box["y"] < -tol or right(box) > w + tol or bottom(box) > h + tol


def intersection(a: dict, b: dict) -> dict | None:
    """The common part of two boxes, or None when they do not overlap by more than TOL."""
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1, y1 = min(right(a), right(b)), min(bottom(a), bottom(b))
    if x1 - x0 <= TOL or y1 - y0 <= TOL:
        return None
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def overflow_excess(scroll: dict | None, tol: float = 1.0) -> dict:
    """How far text runs past its box, by axis ({"x": px, "y": px}, only axes over tol)."""
    if not scroll:
        return {}
    out = {}
    if scroll["sw"] > scroll["cw"] + tol:
        out["x"] = scroll["sw"] - scroll["cw"]
    if scroll["sh"] > scroll["ch"] + tol:
        out["y"] = scroll["sh"] - scroll["ch"]
    return out


def overflows(scroll: dict | None, font: float | None = None, allow: list[dict] | None = None,
              tol: float = 1.0) -> dict:
    """The overflow left after the allowances ({axis: px}; empty when the text fits).

    An allowance {axis, max_em} lets that axis run up to max_em x font size past the box.
    """
    left = {}
    for axis, px in overflow_excess(scroll, tol).items():
        room = max((a["max_em"] * (font or 0) for a in (allow or []) if a["axis"] == axis), default=0.0)
        if px > room + tol:
            left[axis] = px
    return left


def too_small(font: float | None, script: str | None, min_text: dict) -> bool:
    """Font size (design px) below the minimum for its script ("ja" or "latin")."""
    if font is None or script is None:
        return False
    return font < min_text[script] - 1e-6


def overlap_allowed(cut: str, a: str, b: str, allow: list[dict]) -> bool:
    """A pair is allowed when a rule of this cut ("*" for any) names both (glob patterns)."""
    for rule in allow:
        if not fnmatch.fnmatchcase(cut, rule.get("cut", "*")):
            continue
        pa, pb = rule["a"], rule["b"]
        if (fnmatch.fnmatchcase(a, pa) and fnmatch.fnmatchcase(b, pb)) or \
           (fnmatch.fnmatchcase(a, pb) and fnmatch.fnmatchcase(b, pa)):
            return True
    return False


def judge(items: list[dict], layout: dict, cut: str) -> list[dict]:
    """All problems among the collected items of one frame."""
    w, h = layout["design"]["w"], layout["design"]["h"]
    band_top = layout["band"]["top"]
    min_text = layout["min_text"]
    allow = layout["check"].get("allow_overlap", [])
    out = []

    def problem(kind, item, box, detail):
        out.append({"kind": kind, "element": item["label"], "text": item.get("text", ""),
                    "box": {k: round(v, 1) for k, v in box.items()}, "detail": detail})

    for it in items:
        box = it["box"]
        if in_band(box, band_top) and not it.get("band_exempt"):
            problem("band", it, box, f"bottom {bottom(box):.1f} > {band_top}")
        if outside(box, w, h):
            problem("outside", it, box, f"x {box['x']:.1f}..{right(box):.1f}, y {box['y']:.1f}..{bottom(box):.1f}")
        over = overflows(it.get("scroll"), it.get("font"), it.get("overflow_allow"))
        if over:
            s = it["scroll"]
            problem("overflow", it, box, f"scroll {s['sw']:.0f}x{s['sh']:.0f} > client {s['cw']:.0f}x{s['ch']:.0f} "
                    f"(" + ", ".join(f"{k} +{v:.1f}" for k, v in over.items()) + ")")
        if too_small(it.get("font"), it.get("script"), min_text):
            problem("small", it, box, f"{it['font']:.1f} px < {min_text[it['script']]} px ({it['script']})")

    named = [it for it in items if it.get("name")]
    for i, a in enumerate(named):
        for b in named[i + 1:]:
            if a["name"] in b.get("inside", []) or b["name"] in a.get("inside", []):
                continue  # one holds the other
            common = intersection(a["box"], b["box"])
            if common and not overlap_allowed(cut, a["name"], b["name"], allow):
                out.append({"kind": "overlap", "element": f"{a['name']} × {b['name']}", "text": "",
                            "box": {k: round(v, 1) for k, v in common.items()},
                            "detail": f"{common['w']:.1f} x {common['h']:.1f} px"})
    return out


# --- report -------------------------------------------------------------------

def markdown(result: dict) -> str:
    lines = ["# 配置の点検", "",
             f"問題: {result['problems']} 件", "",
             "| 画面 | t (s) | 種類 | 要素 | 文字 | 箱 (x, y, w, h) | 詳細 |",
             "|---|---|---|---|---|---|---|"]
    for sc in result["screens"]:
        for fr in sc["frames"]:
            for p in fr["problems"]:
                b = p["box"]
                text = p["text"].replace("|", "\\|")
                lines.append(f"| {sc['cut']} | {fr['t']:g} | {p['kind']} | `{p['element']}` | {text} | "
                             f"{b['x']:g}, {b['y']:g}, {b['w']:g}, {b['h']:g} | {p['detail']} |")
    lines += ["", "| 画面 | t (s) | 見た要素 | 問題 |", "|---|---|---|---|"]
    for sc in result["screens"]:
        for fr in sc["frames"]:
            lines.append(f"| {sc['cut']} | {fr['t']:g} | {fr['items']} | {len(fr['problems'])} |")
        for e in sc.get("errors", []):
            lines.append(f"| {sc['cut']} | | page error | {e} |")
    return "\n".join(lines) + "\n"


def draw_thumb(png: bytes, problems: list[dict], design_w: int, path: Path) -> None:
    from PIL import Image, ImageDraw

    img = Image.open(io.BytesIO(png)).convert("RGB")
    img.thumbnail((THUMB_LONG, THUMB_LONG), Image.LANCZOS)
    k = img.width / design_w
    d = ImageDraw.Draw(img)
    for p in problems:
        b = p["box"]
        x0, y0 = b["x"] * k, b["y"] * k
        x1, y1 = (b["x"] + b["w"]) * k, (b["y"] + b["h"]) * k
        d.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=3)
        d.text((max(0, x0) + 3, max(0, y0) + 2), p["kind"], fill=(255, 0, 0))
    img.save(path)


def contact_sheet(rows: list[list[Path]], path: Path) -> None:
    from PIL import Image, ImageDraw

    cols = max(len(r) for r in rows)
    cell_w = SHEET_LONG // cols
    cell_h = cell_w * 9 // 16
    pad = 18
    sheet = Image.new("RGB", (cell_w * cols, (cell_h + pad) * len(rows)), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    for r, row in enumerate(rows):
        for c, p in enumerate(row):
            im = Image.open(p).convert("RGB")
            im = im.resize((cell_w - 4, cell_h - 4), Image.LANCZOS)
            x, y = c * cell_w + 2, r * (cell_h + pad) + pad
            sheet.paste(im, (x, y))
            d.text((x, y - pad + 3), p.stem, fill=(0, 0, 0))
    sheet.save(path, quality=85)


# --- running ------------------------------------------------------------------

def load_layout(path: Path = LAYOUT) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def times_for(cut: str, layout: dict, at: list[float] | None) -> list[float]:
    if at:
        return at
    times = layout["check"]["times"].get(cut)
    if not times:
        raise SystemExit(f"no check times for {cut!r} in layout.json: pass --at")
    return times


def check_screen(screen: str | Path, out_dir: Path, layout: dict, at: list[float] | None) -> dict:
    cut = export.screen_name(screen)
    times = times_for(cut, layout, at)
    chk = layout["check"]
    cfg = {"w": layout["design"]["w"], "h": layout["design"]["h"],
           "bandExempt": [e["selector"] for e in chk.get("band_exempt", [])],
           "overflowAllow": [{"selector": a["selector"], "axis": a["axis"], "max_em": a["max_em"]}
                             for a in chk.get("allow_overflow", [])
                             if fnmatch.fnmatchcase(cut, a.get("cut", "*"))]}
    cap = export.Capture(screen, scale=1.0)
    frames, errors = [], []
    try:
        for t in times:
            cap.page.evaluate("t => window.__export.seek(t)", t)
            items = cap.page.evaluate(COLLECT, cfg)
            problems = judge(items, layout, cut)
            pic = out_dir / f"{cut}_t{t:06.2f}.png"
            draw_thumb(cap.page.screenshot(type="png"), problems, cfg["w"], pic)
            frames.append({"t": t, "items": len(items), "problems": problems, "image": pic.name})
            print(f"{cut} t={t:g}: {len(items)} boxes, {len(problems)} problems", flush=True)
        errors = list(cap.errors)
    finally:
        cap.close()
    return {"screen": str(screen), "cut": cut, "frames": frames, "errors": errors}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("screen", help='a page, an http URL, or "all" (the four cuts)')
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--at", type=float, nargs="+", default=None, help="times (s); default: layout.json")
    ap.add_argument("--layout", type=Path, default=LAYOUT)
    a = ap.parse_args(argv)

    layout = load_layout(a.layout)
    if a.screen == "all":
        screens: list[str | Path] = [SCREENS / f"{c}.html" for c in CUTS]
    else:
        screens = [a.screen if export.is_url(a.screen) else Path(a.screen)]
    a.out_dir.mkdir(parents=True, exist_ok=True)

    result = {"layout": str(a.layout), "screens": [], "problems": 0}
    for s in screens:
        result["screens"].append(check_screen(s, a.out_dir, layout, a.at))
    result["problems"] = sum(len(f["problems"]) for sc in result["screens"] for f in sc["frames"]) + \
        sum(len(sc["errors"]) for sc in result["screens"])

    (a.out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (a.out_dir / "report.md").write_text(markdown(result), encoding="utf-8")
    contact_sheet([[a.out_dir / f["image"] for f in sc["frames"]] for sc in result["screens"]],
                  a.out_dir / "sheet.jpg")
    print(f"{result['problems']} problems; wrote {a.out_dir / 'report.md'}")
    return 1 if result["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())

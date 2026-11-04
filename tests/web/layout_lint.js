// Layout lint, run inside a page of the screen: page.evaluate(<this file>, opts) -> [problem, ...].
//
// It looks at what is visible inside opts.root (default: body): has boxes, is not hidden, and is
// not covered by something on top (hit-tested at a few points of its box). Lengths are in the
// stage's own px (the screen is a 1920x1080 stage scaled to the window), so SLACK means the same
// at every window width.
//
//   overlap   the boxes of two pressable things (button, a, input, select, textarea, summary) cross
//             by more than SLACK px on both axes. One inside the other (label > input) is not counted.
//   wrap      a thing that is one line by its kind or class (ONE_LINE below) takes 2 or more lines.
//             Lines are counted from the text's own boxes (Range.getClientRects, grouped by height),
//             not from scrollHeight: with line-height: normal the line's height is the font's.
//   overflow  text runs out of its box: scrollWidth > clientWidth + SLACK on an element that holds
//             text (or holds such an element), except a box cut with an ellipsis (text-overflow).
//             A box that scrolls (overflow auto) is counted only sideways: the lists scroll down.
//   lonely    a line of a wrapped text holds one letter only (終わっ / た): characters are grouped
//             into lines by their boxes (one Range per character).
//   paren     (with lonely, the same lines) a short aside in brackets, up to 30 characters a side,
//             broken across lines: (無料・ / Claude なし), 形の見本 (数値は計算結果 / ではない).
//   cover     a heading or label (LABEL below: h1-h4, .pill .chip .badge .tag .vb .vbox .tb .state
//             .wordmark) or an agent's pet or bubble (.petbox .say) crosses the text of another
//             element, or a pressable thing, by more than SLACK px on both axes (a badge drawn
//             over the HIRAKU name). Text boxes are cut to the box that clips them.
//
// One line by kind or class (ONE_LINE): buttons, select, summary, tabs, table heads, headings, and the
// screen's labels/states: .pill .chip .badge .tag .vb .cut .drv .def .tb .state, and an <a> drawn as
// a button (with a border or a background).
//
// Exceptions, on the element or an ancestor: data-lint="wrap" (may take more lines; no lonely/paren check),
// "overlap", "overflow", "lonely", "cover", or "off" (nothing), space separated. opts.skip: selectors whose
// subtrees are not looked at (another department's, already reported; the test says which).
(opts = {}) => {
  const SLACK = opts.slack ?? 3;
  const root = document.querySelector(opts.root ?? "body");
  if (!root) return [{ rule: "root", sel: opts.root, text: "the root is not on the page" }];
  const skip = opts.skip ?? [];
  const stage = document.getElementById("stage");
  const scale = stage && stage.offsetWidth ? stage.getBoundingClientRect().width / stage.offsetWidth : 1;
  const px = (v) => v / scale; // window px -> stage px
  const ONE_LINE = [
    "button", "select", "summary", "[role=tab]", ".tab", "th", "h1", "h2", "h3", "h4",
    ".pill", ".chip", ".badge", ".tag", ".vb", ".cut", ".drv", ".def", ".tb", ".state",
  ].join(",");
  const PRESSABLE = "button, a[href], input:not([type=hidden]), select, textarea, summary";
  // headings and labels, and the agents' pets and bubbles (js/agent_pets.js), for the cover rule
  const LABEL = "h1, h2, h3, h4, .pill, .chip, .badge, .tag, .vb, .vbox, .tb, .state, .wordmark, .petbox, .say";

  const exempt = (el, what) => {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      const v = e.getAttribute("data-lint");
      if (v && (v.split(/\s+/).includes(what) || v.split(/\s+/).includes("off"))) return true;
    }
    return skip.some((s) => el.closest(s));
  };

  const sel = (el) => {
    const parts = [];
    for (let e = el; e && e.nodeType === 1 && e !== document.body && parts.length < 6; e = e.parentElement) {
      if (e.id) {
        parts.unshift(`#${e.id}`);
        break;
      }
      let p = e.tagName.toLowerCase() + [...e.classList].slice(0, 2).map((c) => `.${c}`).join("");
      for (const k of ["tab", "demo", "launch", "screen", "box", "view", "pick"]) {
        if (e.dataset[k] !== undefined) {
          p += `[data-${k}${e.dataset[k] ? `="${e.dataset[k]}"` : ""}]`;
          break;
        }
      }
      const same = e.parentElement ? [...e.parentElement.children].filter((c) => c.tagName === e.tagName) : [];
      if (same.length > 1 && !e.classList.length) p += `:nth-child(${[...e.parentElement.children].indexOf(e) + 1})`;
      parts.unshift(p);
    }
    return parts.join(" > ");
  };
  const words = (el) => (el.innerText ?? el.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 60);

  const inView = (r) => r.width > 0 && r.height > 0 && r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;
  // visible: shown, and on top at one of a few points of one of its boxes
  const seen = new Map();
  const visible = (el) => {
    if (seen.has(el)) return seen.get(el);
    let ok = el.checkVisibility ? el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) : true;
    if (ok) {
      ok = false;
      for (const r of el.getClientRects()) {
        if (!inView(r)) continue;
        const x0 = Math.max(r.left, 0), x1 = Math.min(r.right, innerWidth), y0 = Math.max(r.top, 0), y1 = Math.min(r.bottom, innerHeight);
        for (const [fx, fy] of [[0.5, 0.5], [0.2, 0.5], [0.8, 0.5], [0.5, 0.25], [0.5, 0.75]]) {
          const hit = document.elementFromPoint(x0 + (x1 - x0) * fx, y0 + (y1 - y0) * fy);
          if (hit && (hit === el || el.contains(hit) || hit.contains(el))) {
            ok = true;
            break;
          }
        }
        if (ok) break;
      }
    }
    seen.set(el, ok);
    return ok;
  };

  // the element that lays out a text node's lines (its nearest ancestor that is not inline)
  const blockOf = (node) => {
    for (let e = node.parentElement; e; e = e.parentElement) {
      const d = getComputedStyle(e).display;
      if (d !== "inline" && d !== "contents") return e;
    }
    return null;
  };
  const textNodes = (el) => {
    const out = [];
    const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.textContent.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
    });
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const p = n.parentElement;
      if (p && !["SCRIPT", "STYLE", "TEMPLATE"].includes(p.tagName) && p.closest("svg") === null) out.push(n);
    }
    return out;
  };
  // group boxes into lines: a box joins a line it overlaps by half of the smaller height
  const lines = (boxes) => {
    const out = [];
    for (const b of boxes) {
      const line = out.find((l) => Math.min(l.bottom, b.bottom) - Math.max(l.top, b.top) > 0.5 * Math.min(l.bottom - l.top, b.bottom - b.top));
      if (line) {
        line.top = Math.min(line.top, b.top);
        line.bottom = Math.max(line.bottom, b.bottom);
        line.items.push(b);
      } else out.push({ top: b.top, bottom: b.bottom, items: [b] });
    }
    return out.sort((a, b) => a.top - b.top);
  };
  const range = document.createRange();
  const nodeBoxes = (nodes) => {
    const out = [];
    for (const n of nodes) {
      range.selectNodeContents(n);
      for (const r of range.getClientRects()) if (r.width > 0.5 && r.height > 0) out.push({ top: r.top, bottom: r.bottom });
    }
    return out;
  };
  const charBoxes = (nodes) => {
    const out = [];
    for (const n of nodes) {
      const s = n.textContent;
      for (let i = 0; i < s.length && out.length < 4000; i++) {
        if (/\s/.test(s[i])) continue;
        range.setStart(n, i);
        range.setEnd(n, i + 1);
        const r = range.getClientRects()[0];
        if (r && r.width > 0 && r.height > 0) out.push({ top: r.top, bottom: r.bottom, left: r.left, ch: s[i] });
      }
    }
    return out;
  };

  const problems = new Map(); // one entry per rule, element and partner, however many scroll positions show it
  const add = (rule, el, extra = {}) => {
    const p = { rule, sel: sel(el), text: words(el), ...extra };
    const key = `${p.rule}|${p.sel}|${p.text}|${p.with ?? ""}`;
    if (!problems.has(key)) problems.set(key, p);
  };
  const all = [root, ...root.querySelectorAll("*")].filter((e) => !e.closest("svg") && e.tagName !== "CANVAS");

  // One pass over what is visible now (pass() below; what scrolls is passed over at each position).
  const pass = () => {
  seen.clear();

  // (a) overlap
  const press = all.filter((e) => e.matches(PRESSABLE) && !exempt(e, "overlap") && visible(e));
  const boxesOf = (e) => [...e.getClientRects()].filter(inView);
  for (let i = 0; i < press.length; i++) {
    for (let j = i + 1; j < press.length; j++) {
      const a = press[i], b = press[j];
      if (a.contains(b) || b.contains(a)) continue;
      let worst = null;
      for (const ra of boxesOf(a))
        for (const rb of boxesOf(b)) {
          const w = px(Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left));
          const h = px(Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top));
          if (w > SLACK && h > SLACK && (!worst || w * h > worst.w * worst.h)) worst = { w, h };
        }
      if (worst) add("overlap", a, { with: sel(b), withText: words(b), by: `${worst.w.toFixed(0)}x${worst.h.toFixed(0)}px` });
    }
  }

  // (b) wrap
  const looksPressed = (a) => {
    const cs = getComputedStyle(a);
    return parseFloat(cs.borderTopWidth) > 0 || (cs.backgroundColor !== "rgba(0, 0, 0, 0)" && cs.backgroundColor !== "transparent");
  };
  for (const e of all) {
    if (!(e.matches(ONE_LINE) || (e.matches("a") && looksPressed(e)))) continue;
    if (exempt(e, "wrap") || !visible(e)) continue;
    const ls = lines(nodeBoxes(textNodes(e)));
    if (ls.length > 1) add("wrap", e, { lines: ls.length });
  }

  // the text blocks: elements that lay out lines of text
  const blocks = new Map();
  for (const n of textNodes(root)) {
    const b = blockOf(n);
    if (!b || !root.contains(b)) continue;
    if (!blocks.has(b)) blocks.set(b, []);
    blocks.get(b).push(n);
  }

  // (c) overflow: the text blocks and the boxes they sit in
  const boxes = new Set();
  for (const b of blocks.keys()) {
    boxes.add(b);
    if (b.parentElement && root.contains(b.parentElement)) boxes.add(b.parentElement);
  }
  for (const e of boxes) {
    if (exempt(e, "overflow") || !visible(e)) continue;
    const cs = getComputedStyle(e);
    if (cs.display === "inline" || cs.display === "table-row" || e.tagName === "TR" || e.tagName === "TBODY") continue;
    if (cs.textOverflow === "ellipsis") continue;
    const over = px(e.scrollWidth - e.clientWidth);
    if (e.clientWidth > 0 && over > SLACK) add("overflow", e, { by: `${over.toFixed(0)}px`, overflow: cs.overflowX });
  }

  // (d) lonely: one letter on a line of its own
  const LETTER = /[\p{L}\p{N}]/u;
  for (const [b, nodes] of blocks) {
    if (exempt(b, "lonely") || exempt(b, "wrap") || !visible(b)) continue;
    if (lines(nodeBoxes(nodes)).length < 2) continue;
    const ls = lines(charBoxes(nodes));
    if (ls.length < 2) continue;
    const rows = ls.map((x) => x.items.sort((p, q) => p.left - q.left).map((c) => c.ch).join(""));
    const split = rows.join(" / ");
    const lone = rows.find((r) => [...r].filter((c) => LETTER.test(c)).length === 1 && [...r].length <= 3);
    if (lone !== undefined) {
      add("lonely", b, { line: lone, split: split.slice(0, 160) });
      continue;
    }
    // a short aside in brackets broken across lines: (無料・ / Claude なし)
    const m = split.match(/[(（「][^()（）「」]{0,30}? \/ [^()（）「」]{0,30}?[)）」]/);
    if (m && !exempt(b, "lonely")) add("paren", b, { line: m[0], split: split.slice(0, 160) });
  }
  // (e) cover: a heading, a label or a pet / bubble crosses another element's text or a
  // pressable thing (not inside it, not holding it). Shown is enough here (not the hit test:
  // what is covered fails the hit test); a text's boxes are cut to the box that clips it (its own too).
  const shown = (el) => (el.checkVisibility ? el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) : true);
  const clipOf = (el) => {
    for (let e = el; e && e !== document.documentElement; e = e.parentElement) {
      const cs = getComputedStyle(e);
      if (cs.overflowX !== "visible" || cs.overflowY !== "visible") return e.getBoundingClientRect();
    }
    return null;
  };
  const cut = (r, c) => (c ? { left: Math.max(r.left, c.left), right: Math.min(r.right, c.right), top: Math.max(r.top, c.top), bottom: Math.min(r.bottom, c.bottom) } : r);
  const labels = all.filter((e) => e.matches(LABEL) && !exempt(e, "cover") && shown(e));
  const texts = [];
  for (const n of textNodes(root)) {
    const p = n.parentElement;
    if (!p || exempt(p, "cover") || !shown(p)) continue;
    const c = clipOf(p);
    range.selectNodeContents(n);
    for (const r of range.getClientRects()) {
      const k = cut(r, c);
      if (k.right - k.left > 0.5 && k.bottom - k.top > 0.5 && inView(r)) texts.push({ el: p, r: k });
    }
  }
  for (const e of all) if (e.matches(PRESSABLE) && !exempt(e, "cover") && shown(e)) for (const r of e.getClientRects()) if (inView(r)) texts.push({ el: e, r });
  for (const l of labels) {
    const lr = cut(l.getBoundingClientRect(), clipOf(l));
    if (!(lr.right > lr.left && lr.bottom > lr.top)) continue;
    for (const { el, r } of texts) {
      if (l.contains(el) || el.contains(l)) continue;
      const w = px(Math.min(lr.right, r.right) - Math.max(lr.left, r.left));
      const h = px(Math.min(lr.bottom, r.bottom) - Math.max(lr.top, r.top));
      if (w > SLACK && h > SLACK) add("cover", l, { with: sel(el), withText: words(el), by: `${w.toFixed(0)}x${h.toFixed(0)}px` });
    }
  }
  }; // pass

  // What scrolls (the list, the log) is looked at from top to bottom, most of a window at a time.
  const scrollers = all.filter((e) => {
    const o = getComputedStyle(e).overflowY;
    return (o === "auto" || o === "scroll") && e.scrollHeight > e.clientHeight + 2;
  });
  pass();
  for (const s of scrollers) {
    const was = s.scrollTop;
    for (let y = 0; y < s.scrollHeight; y += Math.max(40, s.clientHeight * 0.8)) {
      s.scrollTop = y;
      pass();
    }
    s.scrollTop = was;
  }
  return [...problems.values()];
}

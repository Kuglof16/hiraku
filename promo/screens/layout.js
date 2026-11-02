// Coordinates and times of the video-only screens, computed from the layout
// rules (window.PROMO_LAYOUT, generated from layout.json by promo/layout_build.py).
//
//   <script src="layout.data.js"></script>
//   <script src="layout.js" data-cut="n05"></script>
//
// With data-cut, the cut's rules (layout.json, top-level key of the cut) are
// put on <html> as CSS variables:
//   lengths  n05.title.name.y -> --title-name-y: calc(62 * var(--u));
//            n05.tile.label.size_px -> --tile-label-size-px (the path, _ -> -)
//   times    n05.times.burst_s -> --t-burst: 0.95s
// Keys starting with "_", keys ending in "_s" outside "times", arrays and
// strings are skipped. Values the rules only imply (a centred box's left
// edge, ...) come from DERIVED below. Every page also gets --u, --stage-w,
// --stage-h, --band-y, --band-h, --safe-x, --safe-y, --safe-w, --safe-h and
// the colours (colors.tile_bg -> --tile-bg).
//
//   promoLayout.px(62)          "calc(62 * var(--u))"
//   promoLayout.gridCells(g)    the cells of a grid rule, in reading order
//   promoLayout.centred(w)      left edge of a box of width w centred on the stage

(function () {
  const L = window.PROMO_LAYOUT;
  if (!L) throw new Error("layout.js: load layout.data.js first");
  const W = L.stage.w, H = L.stage.h;

  const px = (v) => `calc(${v} * var(--u))`;
  const centred = (w) => (W - w) / 2;
  const cssName = (k) => k.replace(/_/g, "-");

  // Cells of a grid rule {x, y, cols, rows, tile_w, tile_h, gap_x, gap_y}:
  // [{n, col, row, x, y, w, h, cx, cy}], n in reading order.
  function gridCells(g) {
    const cells = [];
    for (let row = 0; row < g.rows; row++) {
      for (let col = 0; col < g.cols; col++) {
        const x = g.x + col * (g.tile_w + g.gap_x);
        const y = g.y + row * (g.tile_h + g.gap_y);
        cells.push({ n: cells.length, col, row, x, y, w: g.tile_w, h: g.tile_h,
                     cx: x + g.tile_w / 2, cy: y + g.tile_h / 2 });
      }
    }
    return cells;
  }

  // Inline placement of an absolutely positioned box.
  const boxStyle = (c) => `left:${px(c.x)};top:${px(c.y)};width:${px(c.w)};height:${px(c.h)}`;

  // Values a cut's rules imply, in design px.
  const DERIVED = {
    n00: (c) => ({ "rule-x": centred(c.rule.w) }),
    n08b: (c) => ({
      "fig-left-x": W / 2 - c.fig.gap_px / 2 - c.fig.w,
      "fig-right-x": W / 2 + c.fig.gap_px / 2,
      "divider-x": centred(c.divider.w),
    }),
    n18: (c) => ({ "light-x": centred(c.light.w), "vehicle-x": centred(c.vehicle.w) }),
  };

  function flatten(obj, prefix, out) {
    for (const [k, v] of Object.entries(obj)) {
      if (k.startsWith("_") || k === "times" || k.endsWith("_s")) continue;
      const key = prefix + cssName(k);
      if (typeof v === "number") out[key] = v;
      else if (v && typeof v === "object" && !Array.isArray(v)) flatten(v, key + "-", out);
    }
    return out;
  }

  function rulesOf(cut) {
    const rules = L[cut];
    if (!rules || typeof rules !== "object") throw new Error(`layout.js: no cut "${cut}" in layout.json`);
    return rules;
  }

  // The length variables of a cut, as {name: design px}.
  function vars(cut) {
    const rules = rulesOf(cut);
    return {
      "stage-w": W,
      "stage-h": H,
      "band-y": L.subtitle_band.y,
      "band-h": L.subtitle_band.h,
      "safe-x": L.safe.x,
      "safe-y": L.safe.y,
      "safe-w": L.safe.w,
      "safe-h": L.safe.h,
      ...flatten(rules, "", {}),
      ...(DERIVED[cut] ? DERIVED[cut](rules) : {}),
    };
  }

  // The time variables of a cut, as {name: seconds} (times.burst_s -> "t-burst").
  function times(cut) {
    const out = {};
    for (const [k, v] of Object.entries(rulesOf(cut).times || {})) {
      if (k.startsWith("_")) continue;
      if (!k.endsWith("_s")) throw new Error(`layout.js: ${cut}.times.${k} must end in _s`);
      out["t-" + k.slice(0, -2).replace(/_/g, "-")] = v;
    }
    return out;
  }

  function apply(cut) {
    const style = document.documentElement.style;
    style.setProperty("--u", `min(calc(100vw / ${W}), calc(100vh / ${H}))`);
    for (const [k, v] of Object.entries(L.colors)) if (!k.startsWith("_")) style.setProperty(`--${cssName(k)}`, v);
    for (const [k, v] of Object.entries(vars(cut))) style.setProperty(`--${k}`, px(v));
    for (const [k, v] of Object.entries(times(cut))) style.setProperty(`--${k}`, `${v}s`);
  }

  const script = document.currentScript;
  const cut = script && script.dataset.cut;
  window.promoLayout = { rules: L, cut, px, centred, gridCells, boxStyle, vars, times, apply,
                         cutRules: cut ? rulesOf(cut) : null };
  if (cut) apply(cut);
})();

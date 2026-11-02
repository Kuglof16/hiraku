// Coordinates of the video-only screens, computed from the layout rules
// (window.PROMO_LAYOUT, generated from layout.json by promo/layout_build.py).
//
//   <script src="layout.data.js"></script>
//   <script src="layout.js" data-cut="n05"></script>
//
// With data-cut, the cut's rules are put on <html> as CSS variables, each a
// length in design px: cuts.n05.name.top -> --name-top: calc(62 * var(--u)).
// Keys starting with "_" are notes and are skipped. Values the rules only
// imply (a centred box's left edge, ...) come from DERIVED below. Every page
// also gets --u, --stage-w, --stage-h, --band-top, --band-h, --margin-side
// and --margin-top.
//
//   promoLayout.px(62)          "calc(62 * var(--u))"
//   promoLayout.gridCells(g)    the cells of a grid rule, in reading order
//   promoLayout.centred(w)      left edge of a box of width w centred on the stage

(function () {
  const L = window.PROMO_LAYOUT;
  if (!L) throw new Error("layout.js: load layout.data.js first");
  const W = L.design.w, H = L.design.h;

  const px = (v) => `calc(${v} * var(--u))`;
  const centred = (w) => (W - w) / 2;

  // Cells of a grid rule {cols, rows, tile: {w, h}, origin: {x, y}, gap: {x, y}}:
  // [{n, col, row, x, y, w, h, cx, cy}], n in reading order.
  function gridCells(g) {
    const cells = [];
    for (let row = 0; row < g.rows; row++) {
      for (let col = 0; col < g.cols; col++) {
        const x = g.origin.x + col * (g.tile.w + g.gap.x);
        const y = g.origin.y + row * (g.tile.h + g.gap.y);
        cells.push({ n: cells.length, col, row, x, y, w: g.tile.w, h: g.tile.h,
                     cx: x + g.tile.w / 2, cy: y + g.tile.h / 2 });
      }
    }
    return cells;
  }

  // Inline placement of an absolutely positioned box.
  const boxStyle = (c) => `left:${px(c.x)};top:${px(c.y)};width:${px(c.w)};height:${px(c.h)}`;

  // Values a cut's rules imply, in design px.
  const DERIVED = {
    n00: (c) => ({ "rule-left": centred(c.rule.w) }),
    n05: (c) => ({ "hero-left": centred(c.hero.w), "hero-top": L.margin.top }),
    n08b: (c) => ({
      "fig-left-x": W / 2 - c.fig.gap / 2 - c.fig.w,
      "fig-right-x": W / 2 + c.fig.gap / 2,
      "divider-left": centred(c.divider.w),
    }),
    n18: (c) => ({ "light-left": centred(c.light.w), "vehicle-left": centred(c.vehicle.w) }),
  };

  function flatten(obj, prefix, out) {
    for (const [k, v] of Object.entries(obj)) {
      if (k.startsWith("_")) continue;
      const key = prefix + k.replace(/_/g, "-");
      if (typeof v === "number") out[key] = v;
      else if (v && typeof v === "object" && !Array.isArray(v)) flatten(v, key + "-", out);
    }
    return out;
  }

  // The CSS variables of a cut, as {name: design px}.
  function vars(cut) {
    const rules = L.cuts[cut];
    if (!rules) throw new Error(`layout.js: no cut "${cut}" in layout.json`);
    return {
      "stage-w": W,
      "stage-h": H,
      "band-top": L.band.top,
      "band-h": H - L.band.top,
      "margin-side": L.margin.side,
      "margin-top": L.margin.top,
      ...flatten(rules, "", {}),
      ...(DERIVED[cut] ? DERIVED[cut](rules) : {}),
    };
  }

  function apply(cut) {
    const style = document.documentElement.style;
    style.setProperty("--u", `min(calc(100vw / ${W}), calc(100vh / ${H}))`);
    for (const [k, v] of Object.entries(vars(cut))) style.setProperty(`--${k}`, px(v));
  }

  const script = document.currentScript;
  const cut = script && script.dataset.cut;
  window.promoLayout = { rules: L, cut, px, centred, gridCells, boxStyle, vars, apply,
                         cutRules: cut ? L.cuts[cut] : null };
  if (cut) apply(cut);
})();

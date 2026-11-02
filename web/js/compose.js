// Laying out the video's cut pages from the promo department's layout rules
// (/layout.json, served from promo/screens/layout.json). No position is written
// here: grids, tile rectangles, the fly-in, the time squeeze and its label are
// computed from the rules, so the pages follow when the rules change.

export async function loadLayout(url = "/layout.json") {
  const res = await fetch(url, { cache: "no-store" });
  const body = await res.json();
  if (!res.ok) throw new Error(`layout: ${body.error ?? res.status}`);
  return body;
}

// The rectangles of a grid {x, y, cols, rows, tile_w, tile_h, gap_x, gap_y}, row by row.
export function gridRects(g) {
  const out = [];
  for (let r = 0; r < g.rows; r += 1) {
    for (let c = 0; c < g.cols; c += 1) {
      out.push({ x: g.x + c * (g.tile_w + g.gap_x), y: g.y + r * (g.tile_h + g.gap_y), w: g.tile_w, h: g.tile_h, col: c, row: r });
    }
  }
  return out;
}

// CSS cubic-bezier(x1, y1, x2, y2) as a function of progress 0..1 (y may overshoot).
export function cubicBezier(x1, y1, x2, y2) {
  const bx = (s) => 3 * x1 * s * (1 - s) ** 2 + 3 * x2 * s * s * (1 - s) + s ** 3;
  const by = (s) => 3 * y1 * s * (1 - s) ** 2 + 3 * y2 * s * s * (1 - s) + s ** 3;
  return (p) => {
    if (p <= 0) return 0;
    if (p >= 1) return 1;
    let lo = 0;
    let hi = 1;
    for (let i = 0; i < 40; i += 1) {
      const mid = (lo + hi) / 2;
      if (bx(mid) < p) lo = mid;
      else hi = mid;
    }
    return by((lo + hi) / 2);
  };
}

const centre = (r) => [r.x + r.w / 2, r.y + r.h / 2];

// For each rectangle, its place in the order of distance from a point (the grid opens outward).
export function outwardOrder(rects, from) {
  const dist = rects.map((r) => Math.hypot(centre(r)[0] - from.x, centre(r)[1] - from.y));
  const sorted = [...dist.keys()].sort((a, b) => dist[a] - dist[b]);
  return rects.map((_, i) => sorted.indexOf(i));
}

// One tile flying in at time t: from `from` at a fifth of its size and clear, to its
// place (the promo page's keyframes: translate(dx, dy) scale(0.2), opacity 0 -> none).
export function flyIn(rect, from, t, start, dur, ease) {
  const p = Math.max(0, Math.min(1, (t - start) / dur));
  const e = ease(p);
  const [cx, cy] = centre(rect);
  return {
    p,
    dx: (from.x - cx) * (1 - e),
    dy: (from.y - cy) * (1 - e),
    scale: 0.2 + 0.8 * e,
    opacity: Math.max(0, Math.min(1, e)),
  };
}

// A record's time span [t0, t1] played between stage times [a, b]: the record time at
// stage time t (held at the ends) and the squeeze (record seconds per stage second).
export function squeeze(t, a, b, t0, t1) {
  const rate = (t1 - t0) / Math.max(1e-9, b - a);
  const tau = t0 + Math.max(0, Math.min(b - a, t - a)) * rate;
  return { tau, rate };
}

// The squeeze in words for the tile's corner: "×16", "×2.4", "×0.6", "×1/55", "×1".
export function rateLabel(rate) {
  if (!(rate > 0)) return "";
  if (rate >= 1.05) return `×${rate >= 10 ? Math.round(rate) : rate.toFixed(1)}`;
  if (rate <= 0.95) return rate >= 0.5 ? `×${rate.toFixed(1)}` : `×1/${Math.round(1 / rate)}`;
  return "×1";
}

// Does rectangle a stay out of the subtitle band and inside the stage? (for the page's own checks)
export function clearOfBand(r, layout) {
  const band = layout.subtitle_band;
  return r.x >= 0 && r.y >= 0 && r.x + r.w <= layout.stage.w && r.y + r.h <= (band ? band.y : layout.stage.h);
}

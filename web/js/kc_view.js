// The K&C lines beside a driving record (grip_panel.js): the kc_bench analysis of the same design
// version (its series against x = travel, mm, bump +: camber, toe, track_change, ...) and on each
// line the wheels' points at their stroke now (wheel_stroke, mm, bump +; the same sign as travel).
// The bench measures one corner as a left wheel; a right wheel is its mirror image, so its camber
// (top out +) and toe (toe-in +) follow the same line. A point's height is the line's value at the
// stroke (read off the line between its samples); a stroke beyond the line's ends is marked at the
// end, open. No bench in the record: no K&C graphs (kcFor returns null).

import { esc, fmt, quantityName, unitLabel } from "./labels.js";
import { extent } from "./series.js";

// The lines shown first (then any other line against travel), with short names.
const ORDER = ["camber", "toe", "track_change", "roll_center_height", "wheelbase_change", "damper_length", "motion_ratio"];
const SHORT = { camber: "キャンバー", toe: "トー", track_change: "輪の中心の横", roll_center_height: "ロールセンター", wheelbase_change: "輪の中心の前後", damper_length: "ダンパーの長さ", motion_ratio: "レバー比" };

// The newest finished kc_bench analysis of the shown analysis' design version, or null.
export function kcFor(s, ana) {
  if (!s || !ana) return null;
  let best = null;
  for (const a of Object.values(s.analyses ?? {})) {
    if (a.tool !== "kc_bench" || a.status !== "ok" || a.design_version !== ana.design_version) continue;
    if (!best || (s.firstSeqOf?.[a.analysis_id] ?? 0) > (s.firstSeqOf?.[best.analysis_id] ?? 0)) best = a;
  }
  return best;
}

// Its lines against travel, in ORDER: [{channel, name, unit, xs, ys}].
export function kcLines(kc) {
  const lines = (kc?.series ?? []).filter((s) => s.x?.quantity === "travel" && Array.isArray(s.x.values) && s.x.values.length === s.values?.length && s.values.length > 1);
  const rank = (c) => (ORDER.includes(c) ? ORDER.indexOf(c) : ORDER.length);
  return lines
    .sort((a, b) => rank(a.channel) - rank(b.channel))
    .map((s) => ({ channel: s.channel, name: SHORT[s.channel] ?? quantityName(s.channel), unit: s.unit, xUnit: s.x.unit, xs: s.x.values, ys: s.values }));
}

// The line's value at x (between its samples), and whether x is past its ends.
export function lineAt(line, x) {
  const xs = line.xs;
  const ys = line.ys;
  const lo = Math.min(xs[0], xs.at(-1));
  const hi = Math.max(xs[0], xs.at(-1));
  const cx = Math.max(lo, Math.min(hi, x));
  for (let i = 0; i + 1 < xs.length; i += 1) {
    const a = xs[i];
    const b = xs[i + 1];
    if ((cx >= a && cx <= b) || (cx <= a && cx >= b)) {
      const f = b === a ? 0 : (cx - a) / (b - a);
      return { x: cx, y: ys[i] + (ys[i + 1] - ys[i]) * f, out: cx !== x };
    }
  }
  return { x: cx, y: ys.at(-1), out: cx !== x };
}

// One small graph: the line and the wheels' points ([{id, stroke, mark}]: mark "fill" or "ring").
export function kcSvg(line, points, w, h) {
  const pad = { l: 34, r: 6, t: 14, b: 16 };
  const [x0, x1] = extent(line.xs);
  let [y0, y1] = extent(line.ys);
  if (y1 - y0 < 1e-9) [y0, y1] = [y0 - 1, y1 + 1];
  const X = (x) => pad.l + ((x - x0) / (x1 - x0 || 1)) * (w - pad.l - pad.r);
  const Y = (y) => h - pad.b - ((y - y0) / (y1 - y0)) * (h - pad.t - pad.b);
  const path = line.xs.map((x, i) => `${i ? "L" : "M"}${X(x).toFixed(1)},${Y(line.ys[i]).toFixed(1)}`).join("");
  const zero = x0 < 0 && x1 > 0 ? `<line class="g" x1="${X(0).toFixed(1)}" x2="${X(0).toFixed(1)}" y1="${pad.t}" y2="${h - pad.b}"/>` : "";
  const dots = points
    .map((p) => {
      const v = lineAt(line, p.stroke);
      const cls = p.mark === "fill" ? "kc-pt" : "kc-pt ring";
      return `<circle class="${cls}${v.out ? " out" : ""}" data-wheel="${esc(p.id)}" data-x="${v.x}" data-y="${v.y}" cx="${X(v.x).toFixed(1)}" cy="${Y(v.y).toFixed(1)}" r="3.6"/>`;
    })
    .join("");
  const unit = unitLabel(line.unit);
  return (
    `<svg width="${w}" height="${h}" data-kc="${esc(line.channel)}">` +
    `<text class="gt" x="${pad.l}" y="10">${esc(line.name)} (${esc(unit)})</text>` +
    `<line class="ax" x1="${pad.l}" x2="${w - pad.r}" y1="${h - pad.b}" y2="${h - pad.b}"/>${zero}` +
    `<text class="yl" x="${pad.l - 3}" y="${pad.t + 4}">${fmt(y1, 2)}</text><text class="yl" x="${pad.l - 3}" y="${h - pad.b}">${fmt(y0, 2)}</text>` +
    `<text class="xl" x="${pad.l}" y="${h - 3}" text-anchor="start">${fmt(x0, 3)}</text><text class="xl" x="${w - pad.r}" y="${h - 3}" text-anchor="end">${fmt(x1, 3)} ${esc(unitLabel(line.xUnit))}</text>` +
    `<path class="kc-ln" d="${path}"/>${dots}</svg>`
  );
}

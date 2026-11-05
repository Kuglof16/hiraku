// The graph popup (N03): one group of series channels against time, with a
// cursor at the animation time, or against another quantity (series with x, e.g.
// a K&C bench's camber against wheel travel), named from x.quantity and x.unit.
// Values printed next to the legend are the recorded samples nearest to the cursor.

import { esc, fmt, quantityName, unitLabel } from "./labels.js";
import { abscissa, chartGroups, extent, nearestAt, seriesName } from "./series.js";

const W = 380;
const H = 150;
const M = { l: 54, r: 12, t: 10, b: 24 };
const COLORS = ["#2b59c3", "#8e6bbf", "#2f7d4f", "#e9893a", "#c8402a", "#2fa7d6", "#6b665c", "#e8c547"];
const MAX_POINTS = 800;
const MAX_CHIPS = 5; // chips shown before ほか N (screen choice)

function niceStep(span, count) {
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const r = raw / mag;
  return (r >= 5 ? 10 : r >= 2 ? 5 : r >= 1 ? 2 : 1) * mag;
}

function ticks(min, max, count) {
  const step = niceStep(max - min || Math.abs(max) || 1, count);
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return out;
}

// Ticks of a log axis over [lo, hi] (log10 of the values): the powers of ten inside, every other one
// when there are many; with fewer than two, 2 and 5 times them too. -> [{ v: log10 value, p: the power or null, m: 1, 2 or 5 }]
function logTicks(lo, hi, most = 6) {
  const out = [];
  const p0 = Math.ceil(lo - 1e-9), p1 = Math.floor(hi + 1e-9);
  const every = Math.max(1, Math.ceil((p1 - p0 + 1) / most));
  for (let p = p0; p <= p1; p++) if ((p - p0) % every === 0) out.push({ v: p, p, m: 1 });
  if (out.length < 2)
    for (let p = Math.floor(lo); p <= Math.ceil(hi); p++)
      for (const m of [2, 5]) {
        const v = p + Math.log10(m);
        if (v >= lo && v <= hi) out.push({ v, p, m });
      }
  return out.sort((a, b) => a.v - b.v);
}
// A tick's label: 10ⁿ as 10 with a raised n (m × 10ⁿ for 2 and 5); y: where the text's baseline is
const SUP = (n) => `<tspan dy="-5" font-size="9">${n}</tspan>`;
const logLabel = (t) => (t.m === 1 ? `10${SUP(t.p)}` : `${t.m}×10${SUP(t.p)}`);

// A chart against a count of cycles (an S-N curve, the stress ranges counted) is drawn on log axes:
// the counts span decades. The values are the record's; only the axes are log. y too when all its
// values are above zero.
const isCycles = (x) => /cycle/i.test(x?.quantity ?? "");
const allPositive = (arrs) => arrs.every((a) => a.length > 0 && a.every((v) => v > 0));

const shortName = seriesName;

export class SeriesChart {
  constructor(el, onPick) {
    this.el = el;
    this.onPick = onPick;
    this.key = null;
    this.cursorT = null;
  }

  // Draw a group of channels (a Map, channel -> series); marks are times to flag (e.g.
  // wheel lift) on a chart against time.
  set(channels, groupKey, marks = []) {
    this.last = [channels, groupKey, marks]; // (drawn again with these when ほか / たたむ is pressed)
    const groups = chartGroups(channels);
    const group = groups.find((g) => g.key === groupKey) ?? groups[0];
    const key = `${groups.map((g) => g.key).join(",")}|${group?.key}`;
    if (this.channels === channels && this.key === key) return group?.key;
    this.channels = channels;
    this.key = key;
    this.group = group;
    if (!group) {
      this.el.innerHTML = "";
      return null;
    }
    const series = group.channels.map((n) => channels.get(n));
    const timed = !group.x;
    // log axes for a chart against cycles (isCycles), each when all its values are above zero
    const logX = !timed && isCycles(group.x) && allPositive(series.map((s) => abscissa(s)));
    const logY = logX && allPositive(series.map((s) => s.values));
    this.log = { x: logX, y: logY };
    const fx = logX ? Math.log10 : (v) => v; // value -> where on the axis
    const fy = logY ? Math.log10 : (v) => v;
    const xs = series.map((s) => extent(abscissa(s)));
    const t0 = fx(Math.min(...xs.map((e) => e[0])));
    const t1 = fx(Math.max(...xs.map((e) => e[1])));
    const ys = series.map((s) => extent(s.values));
    let lo = fy(Math.min(...ys.map((e) => e[0])));
    let hi = fy(Math.max(...ys.map((e) => e[1])));
    if (lo === hi) {
      lo -= 1;
      hi += 1;
    }
    const pad = (hi - lo) * 0.06;
    lo -= pad;
    hi += pad;
    this.scale = { t0, t1, lo, hi };
    const X = (t) => M.l + ((t - t0) / (t1 - t0 || 1)) * (W - M.l - M.r); // axis position -> px
    const Y = (v) => H - M.b - ((v - lo) / (hi - lo)) * (H - M.t - M.b);
    const x = (t) => X(fx(t)); // a value -> px
    const y = (v) => Y(fy(v));
    this.x = x;

    const yTicks = logY ? logTicks(lo, hi, 4).map((t) => ({ at: t.v, label: logLabel(t) })) : ticks(lo, hi, 4).map((v) => ({ at: v, label: esc(fmt(v, 3)) }));
    const xTicks = logX ? logTicks(t0, t1, 6).map((t) => ({ at: t.v, label: logLabel(t) })) : ticks(t0, t1, 5).map((t) => ({ at: t, label: esc(fmt(t, 3)) }));
    const grid = yTicks
      .map(({ at, label }) => `<line x1="${M.l}" x2="${W - M.r}" y1="${Y(at)}" y2="${Y(at)}" class="g"/><text x="${M.l - 6}" y="${Y(at) + 4}" class="yl">${label}</text>`)
      .join("");
    // a label near the right edge ends at its tick (not centred on it: cut by the box)
    const xAnchor = (px) => (px > W - M.r - 24 ? ` style="text-anchor: end"` : "");
    const tgrid = xTicks
      .map(({ at, label }) => `<line x1="${X(at)}" x2="${X(at)}" y1="${M.t}" y2="${H - M.b}" class="g"/><text x="${X(at)}" y="${H - 6}" class="xl"${xAnchor(X(at))}>${label}</text>`)
      .join("");
    const lines = series
      .map((s, i) => {
        const a = abscissa(s);
        const step = Math.max(1, Math.ceil(a.length / MAX_POINTS));
        const pts = [];
        for (let k = 0; k < a.length; k += step) pts.push(`${x(a[k]).toFixed(1)},${y(s.values[k]).toFixed(1)}`);
        const last = a.length - 1;
        if (last % step) pts.push(`${x(a[last]).toFixed(1)},${y(s.values[last]).toFixed(1)}`);
        return `<polyline points="${pts.join(" ")}" stroke="${COLORS[i % COLORS.length]}" class="ln"/>`;
      })
      .join("");
    const markHtml = (timed ? marks : [])
      .filter((m) => m.t >= t0 && m.t <= t1)
      .map((m) => `<line x1="${x(m.t)}" x2="${x(m.t)}" y1="${M.t}" y2="${H - M.b}" class="mk"/><text x="${x(m.t) + 4}" y="${M.t + 10}" class="mkl">${esc(m.label)}</text>`)
      .join("");
    const chipName = (g) => (g.x ? quantityName(g.ja) : g.ja);
    // many groups: the first MAX_CHIPS (and the one shown) and a button for the rest, so the chips
    // do not grow the box over the 3D
    const folded = groups.length > MAX_CHIPS + 1 && !this.expanded;
    const listed = folded ? groups.filter((g, i) => i < MAX_CHIPS || g.key === group.key) : groups;
    const more = groups.length > MAX_CHIPS + 1 ? `<button data-more="1" class="more">${folded ? `ほか ${groups.length - listed.length} ▾` : "たたむ ▴"}</button>` : "";
    const chips = listed.map((g) => `<button data-group="${esc(g.key)}" class="${g.key === group.key ? "on" : ""}">${esc(chipName(g))}</button>`).join("") + more;
    const legend = series
      .map((s, i) => `<span><i style="background:${COLORS[i % COLORS.length]}"></i>${esc(shortName(s.channel))} <b data-ch="${esc(s.channel)}"></b></span>`)
      .join("");
    const across = timed ? "時間 [s]" : `${esc(quantityName(group.x.quantity))} [${esc(unitLabel(group.x.unit) || "1")}]`;
    this.el.innerHTML =
      `<div class="gr-head"><div class="gr-chips">${chips}</div><span class="gr-unit">[${esc(unitLabel(group.unit) || "1")}]・${across}</span></div>` +
      `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${tgrid}${grid}` +
      `<line x1="${M.l}" x2="${M.l}" y1="${M.t}" y2="${H - M.b}" class="ax"/><line x1="${M.l}" x2="${W - M.r}" y1="${H - M.b}" y2="${H - M.b}" class="ax"/>` +
      `${markHtml}${lines}${timed ? `<line class="cur" x1="0" x2="0" y1="${M.t}" y2="${H - M.b}"/>` : ""}</svg>` +
      `<div class="gr-legend">${legend}</div>`;
    this.el.querySelector(".gr-chips").addEventListener("click", (ev) => {
      if (ev.target.closest("button[data-more]")) {
        this.expanded = !this.expanded;
        this.key = null;
        if (this.last) this.set(...this.last); // now, not on the next change of the replay
        return;
      }
      const b = ev.target.closest("button[data-group]");
      if (b) this.onPick(b.dataset.group);
    });
    this.cursorT = null;
    return group.key;
  }

  // The cursor at time t (a chart against another quantity has none).
  cursor(t) {
    if (!this.group || this.group.x || t === this.cursorT) return;
    this.cursorT = t;
    const cur = this.el.querySelector(".cur");
    const xx = this.x(Math.max(this.scale.t0, Math.min(this.scale.t1, t)));
    cur.setAttribute("x1", xx);
    cur.setAttribute("x2", xx);
    for (const b of this.el.querySelectorAll("b[data-ch]")) {
      const s = this.channels.get(b.dataset.ch);
      b.textContent = s ? fmt(nearestAt(s, t), 4) : "";
    }
  }
}

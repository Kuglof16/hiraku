// The graph popup (N03): one group of series channels against time, with a
// cursor at the animation time. Values printed next to the legend are the
// recorded samples nearest to the cursor.

import { esc, fmt, unitLabel } from "./labels.js";
import { chartGroups, nearestAt, wheelName } from "./series.js";

const W = 380;
const H = 150;
const M = { l: 54, r: 12, t: 10, b: 24 };
const COLORS = ["#2b59c3", "#8e6bbf", "#2f7d4f", "#e9893a", "#c8402a", "#2fa7d6", "#6b665c", "#e8c547"];
const MAX_POINTS = 800;

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

function shortName(channel) {
  const dot = channel.indexOf(".");
  return dot > 0 ? wheelName(channel.slice(dot + 1)) : channel;
}

export class SeriesChart {
  constructor(el, onPick) {
    this.el = el;
    this.onPick = onPick;
    this.key = null;
    this.cursorT = null;
  }

  // Draw a group of the motion's channels; marks are times to flag (e.g. wheel lift).
  set(motion, groupKey, marks = []) {
    const groups = chartGroups(motion.channels);
    const group = groups.find((g) => g.key === groupKey) ?? groups[0];
    const key = `${groups.map((g) => g.key).join(",")}|${group?.key}`;
    if (this.motion === motion && this.key === key) return group?.key;
    this.motion = motion;
    this.key = key;
    this.group = group;
    if (!group) {
      this.el.innerHTML = "";
      return null;
    }
    const series = group.channels.map((n) => motion.channels.get(n));
    const t0 = Math.min(...series.map((s) => s.t_s[0]));
    const t1 = Math.max(...series.map((s) => s.t_s.at(-1)));
    let lo = Math.min(...series.map((s) => Math.min(...s.values)));
    let hi = Math.max(...series.map((s) => Math.max(...s.values)));
    if (lo === hi) {
      lo -= 1;
      hi += 1;
    }
    const pad = (hi - lo) * 0.06;
    lo -= pad;
    hi += pad;
    this.scale = { t0, t1, lo, hi };
    const x = (t) => M.l + ((t - t0) / (t1 - t0 || 1)) * (W - M.l - M.r);
    const y = (v) => H - M.b - ((v - lo) / (hi - lo)) * (H - M.t - M.b);
    this.x = x;

    const grid = ticks(lo, hi, 4)
      .map((v) => `<line x1="${M.l}" x2="${W - M.r}" y1="${y(v)}" y2="${y(v)}" class="g"/><text x="${M.l - 6}" y="${y(v) + 4}" class="yl">${fmt(v, 3)}</text>`)
      .join("");
    const tgrid = ticks(t0, t1, 5)
      .map((t) => `<line x1="${x(t)}" x2="${x(t)}" y1="${M.t}" y2="${H - M.b}" class="g"/><text x="${x(t)}" y="${H - 6}" class="xl">${fmt(t, 3)}</text>`)
      .join("");
    const lines = series
      .map((s, i) => {
        const step = Math.max(1, Math.ceil(s.t_s.length / MAX_POINTS));
        const pts = [];
        for (let k = 0; k < s.t_s.length; k += step) pts.push(`${x(s.t_s[k]).toFixed(1)},${y(s.values[k]).toFixed(1)}`);
        const last = s.t_s.length - 1;
        if (last % step) pts.push(`${x(s.t_s[last]).toFixed(1)},${y(s.values[last]).toFixed(1)}`);
        return `<polyline points="${pts.join(" ")}" stroke="${COLORS[i % COLORS.length]}" class="ln"/>`;
      })
      .join("");
    const markHtml = marks
      .filter((m) => m.t >= t0 && m.t <= t1)
      .map((m) => `<line x1="${x(m.t)}" x2="${x(m.t)}" y1="${M.t}" y2="${H - M.b}" class="mk"/><text x="${x(m.t) + 4}" y="${M.t + 10}" class="mkl">${esc(m.label)}</text>`)
      .join("");
    const chips = groups.map((g) => `<button data-group="${esc(g.key)}" class="${g.key === group.key ? "on" : ""}">${esc(g.ja)}</button>`).join("");
    const legend = series
      .map((s, i) => `<span><i style="background:${COLORS[i % COLORS.length]}"></i>${esc(shortName(s.channel))} <b data-ch="${esc(s.channel)}"></b></span>`)
      .join("");
    this.el.innerHTML =
      `<div class="gr-head"><div class="gr-chips">${chips}</div><span class="gr-unit">[${esc(unitLabel(group.unit) || "1")}]・時間 [s]</span></div>` +
      `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${tgrid}${grid}` +
      `<line x1="${M.l}" x2="${M.l}" y1="${M.t}" y2="${H - M.b}" class="ax"/><line x1="${M.l}" x2="${W - M.r}" y1="${H - M.b}" y2="${H - M.b}" class="ax"/>` +
      `${markHtml}${lines}<line class="cur" x1="0" x2="0" y1="${M.t}" y2="${H - M.b}"/></svg>` +
      `<div class="gr-legend">${legend}</div>`;
    this.el.querySelector(".gr-chips").addEventListener("click", (ev) => {
      const b = ev.target.closest("button[data-group]");
      if (b) this.onPick(b.dataset.group);
    });
    this.cursorT = null;
    return group.key;
  }

  cursor(t) {
    if (!this.group || t === this.cursorT) return;
    this.cursorT = t;
    const cur = this.el.querySelector(".cur");
    const xx = this.x(Math.max(this.scale.t0, Math.min(this.scale.t1, t)));
    cur.setAttribute("x1", xx);
    cur.setAttribute("x2", xx);
    for (const b of this.el.querySelectorAll("b[data-ch]")) {
      const s = this.motion.channels.get(b.dataset.ch);
      b.textContent = s ? fmt(nearestAt(s, t), 4) : "";
    }
  }
}

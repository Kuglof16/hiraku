// Plot pane for series: one or more panels side by side, each with its own y axis.
// panel = {title, xlabel, unit, x: [...], ys: [{name, v: [...], col}], marks: [{x, label, col}],
//          bands: [{x0, x1, label, col}], cursor: x value or null, logy: bool}
window.HV = window.HV || {};
(function (HV) {
  'use strict';
  HV.Plot = class {
    constructor(canvas, onClick) {
      this.cv = canvas; this.panels = []; this.boxes = [];
      canvas.addEventListener('click', e => {
        const r = canvas.getBoundingClientRect(), px = e.clientX - r.left, py = e.clientY - r.top;
        const i = this.boxes.findIndex(b => px >= b.L && px <= b.L + b.w && py >= b.T - 10 && py <= b.T + b.h + 20);
        if (i >= 0 && onClick) onClick(i, this.boxes[i].inv(px));
      });
    }
    draw(panels) {
      if (panels) this.panels = panels;
      const cv = this.cv, dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
      if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) { cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); }
      const g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0); g.clearRect(0, 0, W, H);
      this.boxes = [];
      const n = this.panels.length;
      if (!n) { g.fillStyle = '#777'; g.font = '12px "BIZ UDPGothic", "Yu Gothic", Meiryo, sans-serif'; g.fillText('この分野のグラフはない', 12, 20); return; }
      const pw = W / n;
      this.panels.forEach((p, i) => this.box(g, p, i * pw, pw, H));
    }
    box(g, p, X0, PW, H) {
      const L = X0 + 54, R = 12, T = 20, B = 30, w = PW - 54 - R, h = H - T - B;
      let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      for (const v of p.x) { x0 = Math.min(x0, v); x1 = Math.max(x1, v); }
      for (const s of p.ys) for (const v of s.v) { if (p.logy && v <= 0) continue; y0 = Math.min(y0, v); y1 = Math.max(y1, v); }
      if (!(x1 > x0)) x1 = x0 + 1;
      if (!p.logy) { y0 = Math.min(0, y0); y1 = Math.max(0, y1); }
      if (!(y1 > y0)) y1 = y0 + 1;
      const ly = v => Math.log10(Math.max(v, 1e-30));
      const X = v => L + w * (v - x0) / (x1 - x0);
      const Y = p.logy ? v => T + h * (1 - (ly(v) - ly(y0)) / (ly(y1) - ly(y0))) : v => T + h * (1 - (v - y0) / (y1 - y0));
      this.boxes.push({ L, T, w, h, inv: px => x0 + (px - L) / w * (x1 - x0) });
      g.font = '10px "BIZ UDPGothic", "Yu Gothic", Meiryo, sans-serif'; g.lineWidth = 1;
      // grid and axes
      const yt = p.logy ? logTicks(y0, y1) : niceTicks(y0, y1, 5), xt = niceTicks(x0, x1, Math.max(3, Math.floor(w / 70)));
      g.strokeStyle = '#e2e2e2'; g.fillStyle = '#555';
      for (const v of yt) { const y = Y(v); g.beginPath(); g.moveTo(L, y); g.lineTo(L + w, y); g.stroke(); g.fillText(HV.fmt(v), X0 + 4, y + 3); }
      for (const v of xt) { const x = X(v); g.beginPath(); g.moveTo(x, T); g.lineTo(x, T + h); g.stroke(); g.fillText(HV.fmt(v), x - 8, T + h + 12); }
      g.strokeStyle = '#888'; g.strokeRect(L, T, w, h);
      g.fillStyle = '#333'; g.font = 'bold 11px "BIZ UDPGothic", "Yu Gothic", Meiryo, sans-serif';
      g.fillText(p.title + (p.unit ? ` [${p.unit}]` : ''), L, 13);
      g.font = '10px "BIZ UDPGothic", "Yu Gothic", Meiryo, sans-serif'; g.fillStyle = '#555'; g.fillText(p.xlabel || '', L + w - g.measureText(p.xlabel || '').width, T + h + 25);
      // bands (a shaded x range, e.g. after the frame split)
      for (const b of p.bands || []) {
        const a = X(Math.max(b.x0, x0)), z = X(Math.min(b.x1, x1));
        if (!(z > a)) continue;
        g.fillStyle = b.col || 'rgba(160,160,160,0.18)'; g.fillRect(a, T, z - a, h);
        if (b.label) { g.fillStyle = '#666'; g.fillText(b.label, Math.min(a + 4, L + w - g.measureText(b.label).width - 2), T + h - 4); }
      }
      // marks
      for (const m of p.marks || []) {
        if (m.x < x0 || m.x > x1) continue;
        const x = X(m.x); g.strokeStyle = m.col || '#999'; g.setLineDash(m.dash || [3, 3]); g.lineWidth = m.w || 1;
        g.beginPath(); g.moveTo(x, T); g.lineTo(x, T + h); g.stroke(); g.setLineDash([]); g.lineWidth = 1;
        if (m.label) { g.save(); g.translate(x + 3, T + 4); g.rotate(Math.PI / 2); g.fillStyle = m.col || '#777'; g.fillText(m.label, 0, 0); g.restore(); }
      }
      // series
      g.save(); g.beginPath(); g.rect(L, T, w, h); g.clip();
      for (const s of p.ys) {
        g.strokeStyle = s.col || '#1f4e9c'; g.lineWidth = s.w || 1.6; g.beginPath();
        let started = false;
        s.v.forEach((v, i) => { if (p.logy && v <= 0) return; const x = X(p.x[i]), y = Y(v); if (started) g.lineTo(x, y); else { g.moveTo(x, y); started = true; } });
        g.stroke();
      }
      g.restore();
      // cursor and the values there
      if (p.cursor !== null && p.cursor !== undefined) {
        const x = X(p.cursor); g.strokeStyle = '#1d1d1f'; g.lineWidth = 2; g.beginPath(); g.moveTo(x, T); g.lineTo(x, T + h); g.stroke(); g.lineWidth = 1;
        let k = 0, bd = Infinity; p.x.forEach((v, i) => { const d = Math.abs(v - p.cursor); if (d < bd) { bd = d; k = i; } });
        g.font = 'bold 10.5px "BIZ UDPGothic", "Yu Gothic", Meiryo, sans-serif';
        p.ys.forEach((s, j) => { g.fillStyle = s.col || '#1f4e9c'; const t = `${s.name ? s.name + ' ' : ''}${HV.fmt(s.v[k])}`; g.fillText(t, Math.min(x + 5, L + w - g.measureText(t).width - 2), T + 12 + 12 * j); });
      }
    }
  };
  function niceTicks(a, b, n) {
    const span = b - a, raw = span / Math.max(1, n), p = Math.pow(10, Math.floor(Math.log10(raw))), m = raw / p;
    const step = (m < 1.5 ? 1 : m < 3.5 ? 2 : m < 7.5 ? 5 : 10) * p, out = [];
    for (let v = Math.ceil(a / step) * step; v <= b + step * 1e-6; v += step) out.push(+v.toPrecision(12));
    return out;
  }
  function logTicks(a, b) { const out = []; for (let e = Math.floor(Math.log10(a)); e <= Math.ceil(Math.log10(b)); e++) { const v = Math.pow(10, e); if (v >= a && v <= b) out.push(v); } return out.length ? out : [a, b]; }
})(window.HV);

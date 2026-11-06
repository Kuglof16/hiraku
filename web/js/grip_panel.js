// The tyre view's side panel and its wiring (toggle タイヤ, ?on=grip; ?tyre=fx opens on Fx
// against slip ratio instead of Fy against slip angle): grip_view.js gives the record's values at
// the time and puts them on the 3D; here, at the right of the 3D:
//   - the key (colours, the scales: the record's largest force and load and their drawn lengths)
//     and what the record does not have (記録に無い: ...)
//   - the body's roll (deg) on a small scale as wide as the record's largest roll
//   - per wheel, the tyre's force against slip: the record's curves (tyre_fy_curve.<ID>.<Fz>
//     against slip_angle, or tyre_fx_curve against slip_ratio) at each recorded load, the one
//     nearest the wheel's load now drawn darker with its peak marked, and the wheel's point
//     (slip, force) now with its last second of samples; without curves the points only, without
//     the slip channel nothing (and the note says so)
//   - K&C (kc_view.js): the same version's kc_bench lines against travel and the wheels' strokes now
// Every number is a recorded one (a text: the sample nearest the time; a drawn point: between the
// samples). Sliding is orange, never red.

import { esc, fmt } from "./labels.js";
import { extent, lerpAt, wheelName } from "./series.js";
import { CIRCLE_MM, GripLayer, LOAD_MM, MOUNT_MM, gripAt, gripOf } from "./grip_view.js";
import { kcLines, kcSvg } from "./kc_view.js";
import { VIEWS } from "./workbench3d.js";

const TRAIL_S = 1; // the last second of a wheel's samples on its tyre graph
const GRIP_ROOM_PX = 396; // the panel (grip.css: 372 px wide, 12 px from the right) and a gap
const GRIP_VIEW = [0.75, 0.75, 1.05]; // the camera's direction from the vehicle (ISO): front left, from above
const GRIP_BACK = 1.3; // and this many times farther than the workbench's fit
// the panel's sizes, largest first: the first whose content fits the panel's height is used
const SIZES = [
  { tyre: 100, kc: 74, kcLines: 4 },
  { tyre: 86, kc: 64, kcLines: 2 },
  { tyre: 72, kc: 56, kcLines: 2 },
  { tyre: 62, kc: 0, kcLines: 0 },
];

// The range a tyre graph shows: around the curves' peaks and the record's slips (not the whole
// curve: its ends are far past where the tyre runs), within the curves' own range.
function tyreRange(g, motion, kind) {
  const curves = g.curves[kind];
  const slip = kind === "fy" ? "slip_angle" : "slip_ratio";
  const force = kind === "fy" ? "wheel_fy" : "wheel_fx";
  let peakX = 0;
  let curveX = 0;
  let yMax = 0;
  for (const list of Object.values(curves)) {
    for (const c of list) {
      let best = 0;
      c.xs.forEach((x, i) => {
        curveX = Math.max(curveX, Math.abs(x));
        if (x > 0 && Math.abs(c.ys[i]) > Math.abs(c.ys[best])) best = i;
      });
      peakX = Math.max(peakX, Math.abs(c.xs[best]));
    }
  }
  let slipMax = 0;
  let fMax = 0;
  for (const id of g.ids) {
    const s = motion.channels.get(`${slip}.${id}`);
    if (s) slipMax = Math.max(slipMax, ...extent(s.values).map(Math.abs));
    const f = motion.channels.get(`${force}.${id}`);
    if (f) fMax = Math.max(fMax, ...extent(f.values).map(Math.abs));
  }
  let r = Math.max(peakX * 1.3, slipMax * 1.15);
  if (curveX > 0) r = Math.min(r, curveX);
  if (!(r > 0)) r = 1;
  for (const list of Object.values(curves)) for (const c of list) c.xs.forEach((x, i) => Math.abs(x) <= r && (yMax = Math.max(yMax, Math.abs(c.ys[i]))));
  yMax = Math.max(yMax, fMax) * 1.05 || 1;
  return { r, yMax };
}

function tyreSvg(g, motion, f, w, kind, range, W, H) {
  const pad = { l: 30, r: 4, t: 4, b: 14 };
  const X = (x) => pad.l + ((Math.max(-range.r, Math.min(range.r, x)) + range.r) / (2 * range.r)) * (W - pad.l - pad.r);
  const Y = (y) => pad.t + ((range.yMax - y) / (2 * range.yMax)) * (H - pad.t - pad.b);
  const curves = g.curves[kind][w.id] ?? [];
  const load = w.load ?? 0;
  const near = curves.length ? curves.reduce((b, c) => (Math.abs(c.fz - load) < Math.abs(b.fz - load) ? c : b), curves[0]) : null;
  let out = `<line class="g" x1="${pad.l}" x2="${W - pad.r}" y1="${Y(0).toFixed(1)}" y2="${Y(0).toFixed(1)}"/><line class="g" x1="${X(0).toFixed(1)}" x2="${X(0).toFixed(1)}" y1="${pad.t}" y2="${H - pad.b}"/>`;
  for (const c of curves) {
    const pts = c.xs.map((x, i) => [x, c.ys[i]]).filter(([x]) => Math.abs(x) <= range.r);
    const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join("");
    out += `<path class="ty-ln${c === near ? " near" : ""}" data-fz="${c.fz}" d="${d}"/>`;
  }
  if (near) {
    let best = 0;
    near.xs.forEach((x, i) => x > 0 && Math.abs(near.ys[i]) > Math.abs(near.ys[best]) && (best = i));
    if (Math.abs(near.xs[best]) <= range.r) out += `<circle class="ty-peak" cx="${X(near.xs[best]).toFixed(1)}" cy="${Y(near.ys[best]).toFixed(1)}" r="2.6"/>`;
  }
  const slipS = motion.channels.get(`${kind === "fy" ? "slip_angle" : "slip_ratio"}.${w.id}`);
  const fS = motion.channels.get(`${kind === "fy" ? "wheel_fy" : "wheel_fx"}.${w.id}`);
  const x = kind === "fy" ? w.slip : w.slipRatio;
  if (slipS && x !== null) {
    // the last second of samples, fading
    for (let i = 0; i < slipS.t_s.length; i += 1) {
      const t = slipS.t_s[i];
      if (t > f.t || t < f.t - TRAIL_S) continue;
      const op = 0.15 + 0.5 * (1 - (f.t - t) / TRAIL_S);
      out += `<circle class="ty-tr" cx="${X(slipS.values[i]).toFixed(1)}" cy="${Y(lerpAt(fS, t)).toFixed(1)}" r="1.8" opacity="${op.toFixed(2)}"/>`;
    }
    const y = kind === "fy" ? w.fy : w.fx;
    const out_ = Math.abs(x) > range.r;
    out += `<circle class="ty-pt${w.sliding ? " slide" : ""}${out_ ? " out" : ""}" data-wheel="${esc(w.id)}" data-x="${x}" data-y="${y}" cx="${X(x).toFixed(1)}" cy="${Y(y).toFixed(1)}" r="4"/>`;
  }
  const xu = kind === "fy" ? "°" : "%";
  out += `<text class="yl" x="${pad.l - 3}" y="${pad.t + 8}">${fmt(range.yMax / 1000, 2)}</text><text class="yl" x="${pad.l - 3}" y="${H - pad.b}">${fmt(-range.yMax / 1000, 2)}</text>`;
  out += `<text class="xl" x="${pad.l}" y="${H - 2}" text-anchor="start">${fmt(-range.r, 2)}${xu}</text><text class="xl" x="${W - pad.r}" y="${H - 2}" text-anchor="end">${fmt(range.r, 2)}${xu}</text>`;
  return `<svg width="${W}" height="${H}" data-tyre="${kind}" data-wheel="${esc(w.id)}">${out}</svg>`;
}

function rollSvg(g, f, W) {
  const R = Math.max(1, Math.ceil(g.rollMax));
  const step = R <= 6 ? 1 : R <= 12 ? 2 : 5;
  const H = 26;
  const X = (d) => 8 + ((Math.max(-R, Math.min(R, d)) + R) / (2 * R)) * (W - 16);
  let ticks = "";
  for (let d = -Math.floor(R / step) * step; d <= R + 1e-9; d += step) {
    const x = X(d).toFixed(1);
    ticks += `<line class="${d === 0 ? "ax" : "g"}" x1="${x}" x2="${x}" y1="${d === 0 ? 4 : 8}" y2="16"/>`;
  }
  const n = X(f.roll).toFixed(1);
  return (
    `<svg width="${W}" height="${H}" data-roll="${f.roll}"><line class="ax" x1="8" x2="${W - 8}" y1="16" y2="16"/>${ticks}` +
    `<path class="roll-n" d="M${n},15 l-5,-9 h10 z"/>` +
    `<text class="xl" x="8" y="${H}" text-anchor="start">-${R}°</text><text class="xl" x="${W - 8}" y="${H}" text-anchor="end">+${R}°</text></svg>`
  );
}

const sw = (cls) => `<i class="sw ${cls}"></i>`;

export class GripView {
  constructor(bench, viewport) {
    this.bench = bench;
    this.viewport = viewport;
    this.layer = new GripLayer(bench.iso);
    this.el = null;
    this.g = null;
    this.key = null;
    this.frame = null;
    this.html = "";
    this.reserved = 0;
    this.size = 0; // SIZES index
    this.sizeFor = null; // the panel height it was found for
    const want = new URLSearchParams(location.search).get("tyre");
    this.mode = want === "fx" || want === "fy" ? want : null; // null: the larger of the record's forces
  }

  resize(w, h) {
    this.layer.resize(w, h);
  }

  panel() {
    if (this.el) return this.el;
    const el = document.createElement("div");
    el.className = "grip-panel";
    el.dataset.kind = "grip";
    el.addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-tyre-mode]");
      if (!b) return;
      this.mode = b.dataset.tyreMode;
      this.html = "";
    });
    this.viewport.append(el);
    return (this.el = el);
  }

  hide() {
    this.layer.place(null);
    if (this.el) this.el.hidden = true;
    this.frame = null;
    this.reserve(0);
  }

  // Keep the panel's strip at the right of the 3D clear (workbench3d.js setReserve), so the
  // vehicle is framed in the rest; not while two versions are compared (app.js setCompare
  // keeps its own strip then, and reads this.reserved otherwise).
  // With the panel on, the vehicle is framed from higher up (GRIP_VIEW: the circles on the road
  // read as circles); off again, from the test's own view.
  reserve(px, refit = false) {
    const b = this.bench;
    if (this.viewport.classList.contains("compare") || (this.reserved === px && !refit)) return;
    this.reserved = px;
    b.setReserve(px);
    if (!b.design || b.focused) return;
    b.fit(px ? GRIP_VIEW : VIEWS[b.view ?? "default"]);
    if (px) {
      // back a little, so the circles round the wheels stay in the view
      const t = b.controls.target;
      b.camera.position.sub(t).multiplyScalar(GRIP_BACK).add(t);
      b.controls.update();
    }
  }

  // The panel's top: under the toggles and the line of what goes on (it moves under the toggles
  // when there is no room beside them).
  place(el) {
    let top = 52;
    for (const id of ["toggles", "now"]) {
      const o = document.getElementById(id);
      if (o && !o.hidden && o.offsetParent === this.viewport && o.offsetLeft + o.offsetWidth > el.offsetLeft) top = Math.max(top, o.offsetTop + o.offsetHeight + 8);
    }
    if (el.style.top !== `${top}px`) el.style.top = `${top}px`;
  }

  // on: the toggle; ana / motion: the shown analysis and its motion; kc: the bench analysis (or
  // null); tau: the record's time the workbench shows.
  update(on, ana, motion, kc, tau) {
    if (!on || !ana || !motion) return this.hide();
    const key = `${ana.analysis_id}|${kc?.analysis_id ?? ""}`;
    let fresh = false;
    if (key !== this.key || this.g?.analysisId !== ana.analysis_id) {
      this.key = key;
      this.g = gripOf(ana, motion);
      this.kc = kcLines(kc);
      this.layer.set(this.g);
      this.html = "";
      fresh = true;
    }
    if (!this.g) return this.hide();
    const f = gripAt(this.g, motion, tau);
    this.frame = f;
    this.layer.place(f);
    const el = this.panel();
    el.hidden = false;
    this.reserve(GRIP_ROOM_PX, fresh);
    this.place(el);
    // the largest size whose content fits (found again when the panel's height changes)
    const H = el.clientHeight;
    if (H !== this.sizeFor) {
      this.sizeFor = H;
      this.size = 0;
    }
    for (;;) {
      const html = this.panelHtml(motion, f, SIZES[this.size]);
      if (html !== this.html) el.innerHTML = this.html = html;
      if (el.scrollHeight <= el.clientHeight + 1 || this.size === SIZES.length - 1) break;
      this.size += 1;
    }
  }

  tyreKind(motion) {
    const g = this.g;
    if (this.mode) return this.mode;
    // the larger of the record's |wheel_fx| and |wheel_fy|
    let fx = 0;
    let fy = 0;
    for (const id of g.ids) {
      fx = Math.max(fx, ...extent(motion.channels.get(`wheel_fx.${id}`).values).map(Math.abs));
      fy = Math.max(fy, ...extent(motion.channels.get(`wheel_fy.${id}`).values).map(Math.abs));
    }
    return fx > fy ? "fx" : "fy";
  }

  panelHtml(motion, f, size) {
    const g = this.g;
    const tileH = size.tyre;
    const W = 168;
    const missing = [];
    if (!g.has.friction) missing.push("摩擦円");
    if (!g.has.load) missing.push("荷重");
    if (!g.has.mounts) missing.push("取付点の力");
    else if (!g.has.mountPlaces) missing.push("取付点の位置");
    const key =
      `<div class="gp-key">${sw("force")}力${g.has.friction ? `${sw("ring")}摩擦円 μN${sw("slide")}滑り` : ""}${g.has.load ? `${sw("load")}荷重` : ""}${g.has.mountPlaces ? `${sw("mount")}取付点` : ""}</div>` +
      `<div class="gp-scale">${g.has.friction ? "円と矢印" : "矢印"}: 最大 ${fmt(g.fmax / 1000, 3)} kN を ${fmt(CIRCLE_MM / 1000, 2)} m${g.has.load ? `・荷重: 最大 ${fmt(g.loadMax / 1000, 3)} kN を ${fmt(LOAD_MM / 1000, 2)} m` : ""}${g.has.mountPlaces ? `・取付点: 最大 ${fmt(g.mountMax / 1000, 3)} kN を ${fmt(MOUNT_MM / 1000, 2)} m` : ""}</div>` +
      (g.has.friction ? "" : `<div class="gp-none" data-none="friction">摩擦円の記録が無い</div>`) +
      (missing.length ? `<div class="gp-none" data-none="${esc(missing.join(","))}">記録に無い: ${esc(missing.join("・"))}</div>` : "");
    const roll =
      f.roll === null
        ? `<div class="gp-none" data-none="roll">ロールの記録が無い</div>`
        : `<div class="gp-roll"><span class="gp-rv">ロール <b>${f.rollText >= 0 ? "+" : ""}${fmt(f.rollText, 3)}°</b><small>${f.rollText > 0 ? "右が下がる" : f.rollText < 0 ? "左が下がる" : ""}</small></span>${rollSvg(g, f, 170)}</div>`;
    // the tyres
    const kind = this.tyreKind(motion);
    const hasSlip = kind === "fy" ? g.has.slip : g.has.slipRatio;
    const hasCurve = kind === "fy" ? g.has.fyCurve : g.has.fxCurve;
    const chips =
      `<div class="gp-chips">` +
      ["fy", "fx"].map((k) => `<button data-tyre-mode="${k}" class="${k === kind ? "on" : ""}">${k === "fy" ? "横 Fy・スリップ角" : "前後 Fx・スリップ率"}</button>`).join("") +
      `</div>`;
    let tyres = "";
    if (!hasSlip) tyres = `<div class="gp-none" data-none="slip">${kind === "fy" ? "スリップ角" : "スリップ率"}の記録が無い</div>`;
    else {
      const range = tyreRange(g, motion, kind);
      const tiles = f.wheels
        .map((w) => {
          const head = `<div class="ty-h"><b>${esc(wheelName(w.id))}</b>${w.loadText !== null ? ` ${fmt(w.loadText / 1000, 3)} kN` : ""}${w.useText !== null ? ` ・${fmt(w.useText * 100, 3)} %` : ""}${w.sliding ? ` <span class="ty-slide">滑り</span>` : ""}</div>`;
          return `<div class="ty-tile">${head}${tyreSvg(g, motion, f, w, kind, range, W, tileH)}</div>`;
        })
        .join("");
      tyres =
        `<div class="ty-grid">${tiles}</div>` +
        `<div class="gp-foot">${kind === "fy" ? "横 Fy kN・スリップ角 °" : "前後 Fx kN・スリップ率 %"}${hasCurve ? "・線は記録の荷重ごと、濃い線が今の荷重に近い" : "・タイヤの線の記録が無い、点だけ"}${f.wheels.some((w) => w.useText !== null) ? "・% は摩擦円を使った割合" : ""}</div>`;
    }
    // K&C
    let kc = "";
    const lines = this.kc ?? [];
    if (lines.length && g.has.stroke && size.kcLines) {
      const shown = lines.slice(0, size.kcLines);
      const pts = f.wheels.map((w) => ({ id: w.id, stroke: w.stroke, mark: (w.contact[1] ?? 0) >= 0 ? "fill" : "ring" }));
      kc =
        `<div class="gp-sec">K&amp;C <small>同じ設計の台・左の輪として測り、右はその鏡・横はストローク mm、縮みが +</small></div>` +
        `<div class="kc-grid">${shown.map((l) => `<div class="kc-tile">${kcSvg(l, pts, W, size.kc)}</div>`).join("")}</div>` +
        `<div class="gp-foot">● 左の輪 ○ 右の輪</div>`;
    }
    return (
      `<div class="gp-head"><b>タイヤ</b><small>記録の値・${fmt(f.t, 4)} 秒</small></div>` +
      key +
      roll +
      `<div class="gp-sec">タイヤの力とスリップ</div>` +
      chips +
      tyres +
      kc
    );
  }
}

// N04 and N15: the grid of eight tiles (seven domains and assembly, as the app's A6)
// of a run (cut.html?cut=n04|n15&run=<id>[&version=<n>]). Each tile draws its test's
// record of a design version; its corner mark is the domain's judgement of that
// version (judgement.updated: green ✓, red ✕, else none). The header says the version
// and how many rounds it took. Positions, sizes, times and the order of the tiles come
// only from layout.json (n04 / n15); a missing key is named on the page.
//
// N04: one version (?version=, else the run's last), a version on the way. The crash
// and NVH tiles are lit at times.crash_s / nvh_s while they are red; from
// times.assembly_s the grid gives way to the assembly's box (n16.view, filled by
// another page part later).
// N15: the run's versions in order (up to ?version=), one after the other between
// times.rounds_from_s and times.all_green_s; the last one from all_green_s to the end.
// Nothing is coloured by hand: a last version that is not all green stays as it is.

import * as THREE from "three";
import { clearOfBand, gridRects, rateLabel, squeeze } from "./compose.js";
import { contourOf, fieldsOf } from "./fields.js";
import { DOMAIN_JA, esc, fmt, quantityName, unitLabel } from "./labels.js";
import { abscissa, channelsOf, extent, motionOf, xSeriesOf } from "./series.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);

// Every value the grid cuts read (dotted paths in their layout section).
const COMMON_KEYS = [
  "duration_s",
  "header.x", "header.y", "header.w", "header.h", "header.size_px",
  "grid.x", "grid.y", "grid.cols", "grid.rows", "grid.tile_w", "grid.tile_h", "grid.gap_x", "grid.gap_y",
  "tests",
  "tile.border_px", "tile.radius_px", "tile.label.x", "tile.label.y", "tile.label.size_px",
  "tile.mark.size_px", "tile.mark.right", "tile.mark.top",
  "tile.pet.w", "tile.pet.h", "tile.pet.right", "tile.pet.bottom", "tile.rate.size_px",
  "times.lead_s",
];
const CUT_KEYS = {
  n04: ["times.crash_s", "times.nvh_s", "times.assembly_s", "colors.bg", "colors.panel", "colors.rule", "overlay.color"],
  n15: ["times.rounds_from_s", "times.all_green_s"],
};
const COLOR_KEYS = ["bg", "panel", "rule"];
const TOP_KEYS = ["min_text_px", "stage.w", "stage.h"];
const N16_KEYS = ["view.x", "view.y", "view.w", "view.h"];

const at = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

// Series pickers: the channels a tile charts, from the analysis it draws.
const timed = (ana, test) => [...channelsOf(ana).values()].filter((s) => test(s.channel));
const against = (ana, test) => [...xSeriesOf(ana).values()].filter((s) => test(s.x));
const isFrequency = (x) => /freq/i.test(x.quantity ?? "") || x.unit === "Hz";
const isCycles = (x) => /cycle/i.test(x.quantity ?? "");

// What each tile shows (keys: the contract's domains, as layout.json n04/n15.tests): the
// test whose record it draws (else the domain's first test with a record), the field
// drawn as a contour and the charts.
const TILES = {
  handling: { test: "S3", what: "ダブルレーンチェンジ" },
  powertrain: { test: "P2", what: "発進" },
  aero: { test: "F1", what: "横風" },
  crash: { test: "C1", what: "応力と減速度", field: "stress",
    charts: [{ name: "減速度", pick: (a) => timed(a, (c) => c === "deceleration") }] },
  strength: { test: "R3", what: "疲労度", field: "damage", side: true,
    charts: [
      { name: "悪路の輪の荷重", pick: (a) => timed(a, (c) => c.startsWith("wheel_load.")) },
      { name: "S-N 線図", pick: (a) => against(a, isCycles), log: true },
    ] },
  nvh: { test: "N1", what: "モードの形", field: "mode_shape",
    charts: [{ name: "周波数特性", pick: (a) => against(a, isFrequency) }] },
  thermal: { test: "T1", what: "部品の温度", field: "temperature", tags: true },
  assembly: { what: "干渉", assembly: true },
};
const BEYOND = /-beyond$/;
const LINE_COLORS = ["#2b59c3", "#8e6bbf", "#2f7d4f", "#e9893a"];
const RED = "#c8402a";

// The field of a kind a tile colours by (a frame mode first for mode shapes, as N05).
function fieldFor(ana, kind) {
  const fields = fieldsOf(ana, { mesh: false }).filter((f) => f.kind === kind);
  if (kind === "mode_shape") return fields.find((f) => /frame|フレーム/i.test(f.label ?? "")) ?? fields[0] ?? null;
  return fields[0] ?? null;
}

// Does an analysis have anything the tile draws?
function draws(def, ana) {
  if (def.field && fieldFor(ana, def.field)) return true;
  if ((def.charts ?? []).some((c) => c.pick(ana).length)) return true;
  return !!motionOf(ana);
}

// The newest ok analysis of the version for the tile's test (its first load case in the
// project's order that has one; the cases past the limit are left out), else for the
// first such load case of the tile's domain.
function recordFor(s, domain, def, version) {
  const cases = (s.project?.load_cases ?? []).filter((c) => !BEYOND.test(c.load_case_id));
  const anas = s.order.analyses.map((id) => s.analyses[id]).filter((a) => a.status === "ok" && a.design_version === version);
  const pick = (list) => {
    for (const lc of list) {
      const ana = anas.filter((a) => a.load_case_id === lc.load_case_id && draws(def, a)).at(-1);
      if (ana) return { lc, ana };
    }
    return null;
  };
  return (def.test ? pick(cases.filter((c) => c.test_id === def.test)) : null) ?? pick(cases.filter((c) => c.domain === domain));
}

// The assembly of the version: the steps of its newest analysis that has them, and the
// interferences of all its assembly analyses (each pair, state and condition once).
function assemblyFor(s, version) {
  const ids = new Set((s.project?.load_cases ?? []).filter((c) => c.domain === "assembly").map((c) => c.load_case_id));
  const anas = s.order.analyses.map((id) => s.analyses[id]).filter((a) => a.status === "ok" && a.design_version === version && ids.has(a.load_case_id));
  if (!anas.length) return null;
  const steps = anas.filter((a) => Array.isArray(a.assembly)).at(-1)?.assembly ?? [];
  const seen = new Set();
  const hits = [];
  for (const a of anas) {
    for (const x of a.interferences ?? []) {
      const key = `${x.a}|${x.b}|${x.state ?? ""}|${x.condition ?? ""}`;
      if (seen.has(key)) continue;
      seen.add(key);
      hits.push(x);
    }
  }
  return { anas, steps, hits };
}

// "member rail_L_AS1" / "part:wheel_FL" -> the id in the design.
const bareId = (ref) => String(ref ?? "").replace(/^(member|part|node)[: ]\s*/, "");

function thingName(design, ref) {
  const id = bareId(ref);
  const p = (design?.parts ?? []).find((x) => x.id === id);
  return p ? String(p.name ?? id).replace(/\s*[（(].*$/, "") || id : id;
}

// A small chart of series (SVG in its own pixels): the name and unit at the top left,
// the abscissa's name at the bottom left when it is not time; log axes when asked and
// every value is positive. Returns the SVG and, against time, the cursor's x of a time.
function plotSvg(list, w, h, textPx, name, log) {
  const useLog = log && list.every((s) => [...abscissa(s), ...s.values].every((v) => v > 0));
  const f = useLog ? Math.log10 : (v) => v;
  const xs = list.map((s) => extent(abscissa(s).map(f)));
  const ys = list.map((s) => extent(s.values.map(f)));
  const x0 = Math.min(...xs.map((e) => e[0]));
  const x1 = Math.max(...xs.map((e) => e[1]));
  const lo = Math.min(...ys.map((e) => e[0]));
  const hi = Math.max(...ys.map((e) => e[1]));
  const xq = list[0].x;
  const top = textPx + 8;
  const bottom = h - (xq ? textPx + 8 : 4);
  const X = (v) => 4 + ((f(v) - x0) / (x1 - x0 || 1)) * (w - 8);
  const Y = (v) => bottom - ((f(v) - lo) / (hi - lo || 1)) * (bottom - top);
  const lines = list
    .map((s, i) => {
      const a = abscissa(s);
      const step = Math.max(1, Math.ceil(a.length / 200));
      const pts = [];
      for (let k = 0; k < a.length; k += step) pts.push(`${X(a[k]).toFixed(1)},${Y(s.values[k]).toFixed(1)}`);
      return `<polyline points="${pts.join(" ")}" fill="none" stroke="${LINE_COLORS[i % LINE_COLORS.length]}" stroke-width="2"/>`;
    })
    .join("");
  const unit = unitLabel(list[0].unit) || "1";
  const head = `<text x="4" y="${textPx}" font-size="${textPx}" fill="#5c5a54">${esc(name)} [${esc(unit)}]</text>`;
  const foot = xq ? `<text x="4" y="${h - 4}" font-size="${textPx}" fill="#5c5a54">${esc(quantityName(xq.quantity))} [${esc(unitLabel(xq.unit) || "1")}]</text>` : "";
  const cur = xq ? "" : `<line class="cur" x1="0" x2="0" y1="${top}" y2="${bottom}" stroke="${RED}" stroke-width="1.5"/>`;
  return { svg: head + lines + foot + cur, X: xq ? null : (t) => X(t) };
}

export class CutGrid {
  constructor(name, layout, s) {
    const spec = layout[name] ?? {};
    // colours: the cut's own, else (N15) those of N04
    const colors = spec.colors ?? (name === "n15" ? layout.n04?.colors : undefined);
    const missing = [...COMMON_KEYS, ...CUT_KEYS[name]].filter((k) => at(spec, k) === undefined);
    if (name === "n15") missing.push(...COLOR_KEYS.filter((k) => colors?.[k] === undefined).map((k) => `colors.${k}`));
    const why = [missing.length && `layout.json の ${name} に無い: ${missing.join(", ")}`];
    if (name === "n04") {
      const n16 = N16_KEYS.filter((k) => at(layout.n16, k) === undefined);
      why.push(n16.length && `layout.json の n16 に無い: ${n16.join(", ")}`);
    }
    const top = TOP_KEYS.filter((k) => at(layout, k) === undefined);
    why.push(top.length && `layout.json に無い: ${top.join(", ")}`);
    if (why.some(Boolean)) throw new Error(why.filter(Boolean).join(" / "));

    this.name = name;
    this.layout = layout;
    this.spec = spec;
    this.colors = colors;
    this.s = s;
    const all = [...s.versions].sort((a, b) => a - b);
    const asked = params.has("version") ? Number(params.get("version")) : all.at(-1);
    if (!s.designs[asked]) throw new Error(`版 ${params.get("version") ?? asked} の記録が無い (この記録の版: ${all.join(", ") || "無し"})`);
    // N04 draws one version; N15 the versions in order up to the asked one
    this.versions = name === "n15" ? all.filter((v) => v <= asked) : [asked];
    this.textPx = Math.max(layout.min_text_px, spec.tile.rate.size_px);
    this.stage = $("stage");
    this.stage.style.background = colors.bg;
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.build();
  }

  // Rounds it took to reach a version: the versions before it in the run (as roundsUsed).
  roundsTo(v) {
    return this.s.versions.filter((x) => x < v).length;
  }

  // The version shown at stage time t (an index into this.versions).
  versionAt(t) {
    const n = this.versions.length;
    if (this.name !== "n15" || n === 1) return n - 1;
    const T = this.spec.times;
    if (t >= T.all_green_s) return n - 1;
    if (t < T.rounds_from_s) return 0;
    // the versions before the last share the time from rounds_from_s to all_green_s
    const k = Math.floor(((t - T.rounds_from_s) / (T.all_green_s - T.rounds_from_s)) * (n - 1));
    return Math.min(n - 2, Math.max(0, k));
  }

  build() {
    const sp = this.spec;
    const st = sp.tile;
    const H = sp.header;
    this.header = document.createElement("div");
    this.header.className = "grid-header";
    this.header.dataset.box = `${this.name}.header`;
    Object.assign(this.header.style, { left: `${H.x}px`, top: `${H.y}px`, width: `${H.w}px`, height: `${H.h}px`, fontSize: `${H.size_px}px`, lineHeight: `${H.h}px` });
    this.stage.append(this.header);

    const rects = gridRects(sp.grid);
    if (sp.tests.length > rects.length) throw new Error(`layout.json の ${this.name}.tests は ${sp.tests.length} 枚、grid は ${rects.length} 枚分`);
    this.tiles = sp.tests.map((key, i) => {
      const r = rects[i];
      const def = TILES[key];
      const box = `${this.name}.tile.${key}`;
      const el = document.createElement("div");
      el.className = "tile grid-tile";
      el.dataset.box = box;
      el.dataset.domain = key;
      Object.assign(el.style, {
        left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`,
        background: this.colors.panel, border: `${st.border_px}px solid ${this.colors.rule}`, borderRadius: `${st.radius_px}px`,
      });
      if (!clearOfBand(r, this.layout)) el.dataset.outside = "1";
      // the rate keeps clear of the pet's box in the corner
      el.innerHTML =
        `<div class="pet" data-box="${box}.pet" style="right:${st.pet.right}px;bottom:${st.pet.bottom}px;width:${st.pet.w}px;height:${st.pet.h}px"></div>` +
        `<span class="rate" data-box="${box}.rate" style="right:${st.pet.right + st.pet.w + st.label.x}px;bottom:${st.label.y}px;font-size:${st.rate.size_px}px"></span>`;
      this.stage.append(el);
      const tile = { key, def, el, rect: r, inner: { w: r.w - 2 * st.border_px, h: r.h - 2 * st.border_px }, rateEl: el.querySelector(".rate") };
      // one layer per version: its label, mark and drawing
      tile.layers = this.versions.map((v) => this.makeLayer(tile, v));
      return tile;
    });

    if (this.name === "n04") {
      const V = this.layout.n16.view;
      this.asmBox = document.createElement("div");
      this.asmBox.className = "n04-assembly";
      this.asmBox.dataset.box = "n04.assembly";
      Object.assign(this.asmBox.style, { left: `${V.x}px`, top: `${V.y}px`, width: `${V.w}px`, height: `${V.h}px`, background: this.colors.panel, border: `${st.border_px}px solid ${this.colors.rule}`, borderRadius: `${st.radius_px}px` });
      this.stage.append(this.asmBox);
    }
  }

  makeLayer(tile, v) {
    const def = tile.def;
    const box = tile.el.dataset.box;
    const el = document.createElement("div");
    el.className = "layer";
    el.dataset.version = String(v);
    tile.el.append(el);
    const state = this.s.judgements[`${v}|${tile.key}`]?.state ?? null;
    const layer = { v, el, state, area: null };
    el.innerHTML = this.markHtml(box, state);
    if (!def) {
      el.insertAdjacentHTML("afterbegin", this.labelHtml(box, tile.key));
      el.insertAdjacentHTML("beforeend", `<span class="none" style="font-size:${this.textPx}px">この札の中身はまだ決めていない (${esc(tile.key)})</span>`);
    } else {
      el.insertAdjacentHTML("afterbegin", `<canvas></canvas>` + this.labelHtml(box, `${DOMAIN_JA[tile.key] ?? tile.key}・${def.what}`));
      layer.canvas = el.querySelector("canvas");
      if (def.assembly) this.makeAssembly(tile, layer);
      else {
        layer.rec = recordFor(this.s, tile.key, def, v);
        if (layer.rec) this.makeView(tile, layer);
        else el.insertAdjacentHTML("beforeend", `<span class="none" style="font-size:${this.textPx}px">版 ${v} の記録が無い (${esc(def.test ?? tile.key)})</span>`);
      }
    }
    this.fitLabel(tile, el.querySelector("b"));
    el.style.display = "none";
    return layer;
  }

  labelHtml(box, text) {
    const L = this.spec.tile.label;
    return `<b data-box="${box}.label" style="left:${L.x}px;top:${L.y}px;font-size:${L.size_px}px">${esc(text)}</b>`;
  }

  // The label ends short of the mark's corner: its text steps down a pixel at a time, not
  // below min_text_px (measured while the layer is shown, before it is hidden).
  fitLabel(tile, b) {
    const st = this.spec.tile;
    const room = tile.inner.w - st.label.x - (st.mark.right + st.mark.size_px + st.label.x / 2);
    let px = st.label.size_px;
    while (b.offsetWidth > room && px > this.layout.min_text_px) {
      px -= 1;
      b.style.fontSize = `${px}px`;
    }
  }

  // The domain's judgement of the version: green ✓, red ✕, none when it has not been judged.
  markHtml(box, state) {
    const sign = state === "green" ? "✓" : state === "red" ? "✕" : null;
    if (!sign) return "";
    const M = this.spec.tile.mark;
    return `<span class="mark ${state === "green" ? "ok" : "miss"}" data-box="${box}.mark" data-state="${state}" style="right:${M.right}px;top:${M.top}px;font-size:${M.size_px}px">${sign}</span>`;
  }

  // The top of the space under the tile's top line (its label and mark).
  belowHead() {
    const st = this.spec.tile;
    return Math.max(st.label.y + st.label.size_px, st.mark.top + st.mark.size_px) + st.label.y / 2;
  }

  // A 3D view of the design through the shared renderer in a part of the tile.
  newView(tile, layer, design, x, w, y = 0, h = tile.inner.h) {
    const v = new Workbench3D(null, params.get("tilelook") === "real" ? "real" : "zu", { renderer: this.renderer });
    v.setDesign(design);
    layer.area = { x, y, w, h };
    Object.assign(layer.canvas.style, { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px` });
    v.resize(w, h);
    return v;
  }

  makeView(tile, layer) {
    const { lc, ana } = layer.rec;
    const def = tile.def;
    const el = layer.el;
    const pad = this.spec.tile.label.x;
    const top = this.belowHead();
    // charts with something to draw; a side tile keeps them in a column at its left
    const charts = (def.charts ?? []).map((c) => ({ ...c, list: c.pick(ana) })).filter((c) => c.list.length);
    const side = def.side && charts.length;
    const colW = side ? Math.round(tile.inner.w * 0.5) : 0;
    const v = this.newView(tile, layer, this.s.designs[ana.design_version] ?? this.s.designs[layer.v], colW, tile.inner.w - colW);
    v.setLoading(lc.params?.loading ?? null);
    const motion = motionOf(ana);
    v.setMotion(motion);
    v.setScene(lc, ana);
    const field = def.field ? fieldFor(ana, def.field) : null;
    // A temperature criterion names one part's quantity (T1: temp_battery_max), so it is
    // not laid on every part: temperatures are coloured on their own range.
    const ct = field ? contourOf(ana, field, field.kind === "temperature" ? null : lc.criterion) : null;
    v.setContour(ct);
    v.setVectors(null);
    v.setToggles({ parts: true, contour: true, arrows: false, cg: true, names: false, limit: false });
    // the camera takes in the vehicle and what the scene puts beside it (a wall's face)
    const pts = v.contentPoints();
    const d = v.sceneDesc;
    if (d?.wall) pts.push(new THREE.Vector3(d.wall.x + 300, 0, 1200));
    for (const o of d?.objects ?? []) if (o.kind === "wall") pts.push(new THREE.Vector3(o.position_mm[0] - o.size_mm[0] / 2, o.position_mm[1], o.position_mm[2]));
    v.fitPoints(pts, VIEWS[v.view ?? "default"], 1.06);
    layer.view = v;
    layer.motion = motion;
    if (ct?.frequency) el.querySelector("b").insertAdjacentHTML("beforeend", ` <small>${fmt(ct.frequency, 3)} Hz</small>`);

    // charts: stacked in the left column, or one at the top right over the view
    layer.cursors = [];
    const bottomRoom = this.spec.tile.rate.size_px + this.spec.tile.label.y * 2;
    const boxes = side
      ? charts.map((c, k) => {
          const h = Math.floor((tile.inner.h - top - bottomRoom - (charts.length - 1) * pad) / charts.length);
          return { c, left: pad, top: top + k * (h + pad), w: colW - 2 * pad, h };
        })
      : charts.slice(0, 1).map((c) => ({ c, right: pad, top, w: Math.round(tile.inner.w * 0.42), h: Math.round(tile.inner.h * 0.36) }));
    let span = motion ? [motion.t0, motion.t1] : null;
    for (const b of boxes) {
      const { svg, X } = plotSvg(b.c.list, b.w, b.h, this.textPx, b.c.name, b.c.log);
      const pos = b.left !== undefined ? `left:${b.left}px` : `right:${b.right}px`;
      el.insertAdjacentHTML("beforeend", `<svg class="chart" data-box="${tile.el.dataset.box}.chart" style="${pos};top:${b.top}px" width="${b.w}" height="${b.h}" viewBox="0 0 ${b.w} ${b.h}">${svg}</svg>`);
      if (X) {
        layer.cursors.push({ X, el: el.querySelector("svg.chart:last-of-type .cur") });
        const ts = b.c.list.flatMap((s) => [s.t_s[0], s.t_s.at(-1)]);
        span ??= [Math.min(...ts), Math.max(...ts)];
      }
    }
    layer.span = span;

    // part values (temperatures): the view's own tags, drawn here as text at their places
    if (def.tags) {
      layer.tags = v.overGhosts.children
        .filter((o) => o.isCSS2DObject)
        .map((o) => {
          const tag = document.createElement("span");
          tag.className = `tag${o.element.classList.contains("lbl-over") ? " over" : ""}`;
          tag.style.fontSize = `${this.textPx}px`;
          tag.innerHTML = o.element.innerHTML;
          el.append(tag);
          return { o, el: tag };
        });
    }
  }

  // Assembly: the design of the version with what interferes boxed in red, and in words
  // the interferences (two pairs, then how many more) or ✓ when there are none, with the
  // steps' count.
  makeAssembly(tile, layer) {
    const asm = assemblyFor(this.s, layer.v);
    const el = layer.el;
    if (!asm) {
      el.insertAdjacentHTML("beforeend", `<span class="none" style="font-size:${this.textPx}px">版 ${layer.v} の組み立ての記録が無い</span>`);
      return;
    }
    const design = this.s.designs[layer.v];
    const L = this.spec.tile.label;
    const steps = asm.steps.length ? `・手順 ${asm.steps.length}` : "";
    const moving = asm.hits.filter((x) => x.state === "motion").length;
    const pair = (x) => `${esc(thingName(design, x.a))} × ${esc(thingName(design, x.b))} ${esc(fmt(x.depth_mm, 3))} mm`;
    const lines = asm.hits.length
      ? [`<span class="miss">干渉 ${asm.hits.length} 件</span>${moving ? ` (動いたとき ${moving})` : ""}${steps}`,
         ...asm.hits.slice(0, 2).map(pair), ...(asm.hits.length > 2 ? [`ほか ${asm.hits.length - 2} 件`] : [])]
      : [`<span class="ok">✓ 干渉なし</span>${steps}`];
    // the words end short of the pet's box (their width) and above it (their rows)
    const textW = tile.inner.w - 2 * L.x;
    el.insertAdjacentHTML("beforeend", `<div class="asm" data-box="${tile.el.dataset.box}.asm" style="left:${L.x}px;top:${this.belowHead()}px;width:${textW}px;font-size:${this.textPx}px">${lines.map((t) => `<div>${t}</div>`).join("")}</div>`);
    const below = this.belowHead() + el.querySelector(".asm").offsetHeight + L.y / 2;
    const v = this.newView(tile, layer, design, 0, tile.inner.w, below, tile.inner.h - below);
    v.setScene(null, null);
    v.setContour(null);
    v.setToggles({ parts: true, contour: false, arrows: false, cg: false, names: false, limit: false });
    v.fitPoints(v.contentPoints(), VIEWS.default, 1.1);
    const marks = new THREE.Group();
    const mat = new THREE.MeshBasicMaterial({ color: RED, transparent: true, opacity: 0.45, depthWrite: false });
    for (const ref of new Set(asm.hits.flatMap((x) => [x.a, x.b]))) {
      const id = bareId(ref);
      const m = v.members[id];
      const p = (design.parts ?? []).find((x) => x.id === id);
      const w = v.wheelObjs[id];
      let mesh = null;
      if (m) {
        const k = Math.max(m.width, m.height) * 2.2;
        mesh = new THREE.Mesh(new THREE.BoxGeometry(m.length + k * 0.4, k, k), mat);
        mesh.matrixAutoUpdate = false;
        mesh.matrix.copy(m.mesh.matrix);
      } else if (p?.box) {
        mesh = new THREE.Mesh(new THREE.BoxGeometry(...p.box.size_mm.map((x) => x * 1.1)), mat);
        mesh.position.set(...p.box.center_mm);
      } else if (w) {
        mesh = new THREE.Mesh(new THREE.BoxGeometry(w.r * 2.2, w.w * 1.2, w.r * 2.2), mat);
        mesh.position.copy(w.base);
      }
      if (mesh) marks.add(mesh);
    }
    v.body.add(marks);
    layer.view = v;
  }

  // Part value tags at their parts' places in the view; a tag that would cover one
  // already placed (lower ones first) goes up above it.
  placeTags(layer) {
    const A = layer.area;
    const placed = [];
    const items = layer.tags.map((g) => {
      const q = g.o.getWorldPosition(new THREE.Vector3()).project(layer.view.camera);
      return { g, x: A.x + ((q.x + 1) / 2) * A.w, y: A.y + ((1 - q.y) / 2) * A.h, w: g.el.offsetWidth, h: g.el.offsetHeight };
    });
    for (const it of items.sort((a, b) => b.y - a.y)) {
      for (;;) {
        const hit = placed.find((p) => Math.abs(p.x - it.x) < (p.w + it.w) / 2 && it.y > p.y - p.h && it.y - it.h < p.y);
        if (!hit) break;
        it.y = hit.y - hit.h - 2;
      }
      placed.push(it);
      it.g.el.style.left = `${it.x.toFixed(1)}px`;
      it.g.el.style.top = `${it.y.toFixed(1)}px`;
    }
  }

  resize(scale) {
    this.scale = scale;
    const areas = this.tiles.flatMap((t) => t.layers.map((l) => l.area).filter(Boolean));
    const w = Math.max(1, ...areas.map((a) => a.w));
    const h = Math.max(1, ...areas.map((a) => a.h));
    this.renderer.setSize(Math.ceil(w * scale), Math.ceil(h * scale), false);
    for (const t of this.tiles) {
      for (const l of t.layers) {
        if (!l.canvas || !l.area) continue;
        l.canvas.width = Math.round(l.area.w * scale);
        l.canvas.height = Math.round(l.area.h * scale);
      }
    }
  }

  draw(t) {
    const sp = this.spec;
    const T = sp.times;
    // N04 gives way to the assembly's box at assembly_s
    const gridShown = this.name !== "n04" || t < T.assembly_s;
    if (this.asmBox) this.asmBox.style.visibility = gridShown ? "hidden" : "visible";
    this.header.style.visibility = gridShown ? "visible" : "hidden";
    const k = this.versionAt(t);
    const v = this.versions[k];
    document.documentElement.dataset.version = String(v);
    this.header.textContent = `版 ${v}・繰り返し ${this.roundsTo(v)} 回`;
    const end = this.name === "n04" ? T.assembly_s : sp.duration_s;
    for (const tile of this.tiles) {
      tile.el.style.visibility = gridShown ? "visible" : "hidden";
      tile.layers.forEach((l, i) => (l.el.style.display = i === k ? "" : "none"));
      const layer = tile.layers[k];
      // N04: the crash and NVH tiles are lit from the time the voice names them, while red
      const litAt = this.name === "n04" ? { crash: T.crash_s, nvh: T.nvh_s }[tile.key] : undefined;
      const hot = litAt !== undefined && t >= litAt && layer.state === "red";
      tile.el.dataset.hot = hot ? "1" : "";
      tile.el.style.borderColor = hot ? sp.overlay.color : this.colors.rule;
      tile.el.style.boxShadow = hot ? `0 0 0 ${sp.tile.border_px * 2}px ${sp.overlay.color}` : "none";
      if (!gridShown || !layer.view) continue;
      // the record plays from lead_s to the grid's end, squeezed to fit
      let tau = 0;
      if (layer.span) {
        const q = squeeze(t, T.lead_s, end, layer.span[0], layer.span[1]);
        tau = q.tau;
        tile.rateEl.textContent = rateLabel(q.rate);
      } else tile.rateEl.textContent = "";
      if (layer.motion) layer.view.setFrame(layer.motion.frame(tau));
      layer.view.animate(t, false); // a mode shape swings with the stage clock
      for (const c of layer.cursors ?? []) c.el?.setAttribute("transform", `translate(${c.X(tau).toFixed(1)} 0)`);
      layer.view.renderInto(layer.canvas.getContext("2d"), layer.canvas.width, layer.canvas.height, this.scale);
      if (layer.tags) this.placeTags(layer);
    }
  }
}

export class CutN04 extends CutGrid {
  constructor(layout, s) {
    super("n04", layout, s);
  }
}

export class CutN15 extends CutGrid {
  constructor(layout, s) {
    super("n15", layout, s);
  }
}

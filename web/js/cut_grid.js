// N04 and N15: the grid of eight tiles (seven domains and assembly, as the app's A6)
// for one design version of a run (cut.html?cut=n04&run=<id>&version=<n>; without
// ?version= the run's last version). N04 is a version on the way (some red), N15 the
// version where every tile is green: the two differ only in the run and version
// they are given. Each tile draws its test's record of that version; its corner mark
// is the domain's judgement of that version (judgement.updated: green ✓, red ✕, else
// none). Positions, sizes, times and the order of the tiles come only from
// layout.json (n04 / n15); a missing key is named on the page.

import * as THREE from "three";
import { clearOfBand, gridRects, rateLabel, squeeze } from "./compose.js";
import { contourOf, fieldsOf } from "./fields.js";
import { DOMAIN_JA, esc, fmt, quantityName, unitLabel } from "./labels.js";
import { abscissa, channelsOf, extent, motionOf, xSeriesOf } from "./series.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);

// Every value the grid cuts read from their layout section (dotted paths), and from the top.
export const GRID_KEYS = [
  "duration_s",
  "grid.x", "grid.y", "grid.cols", "grid.rows", "grid.tile_w", "grid.tile_h", "grid.gap_x", "grid.gap_y",
  "tests",
  "tile.border_px", "tile.radius_px", "tile.label.x", "tile.label.y", "tile.label.size_px", "tile.rate.size_px", "tile.mark.size_px",
  "times.tiles_s", "times.tile_in_s", "times.tile_stagger_s",
];
const TOP_KEYS = ["min_text_px", "stage.w", "stage.h"];

const at = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

// Series pickers: the channels a tile charts, from the analysis it draws.
const timed = (ana, test) => [...channelsOf(ana).values()].filter((s) => test(s.channel));
const against = (ana, test) => [...xSeriesOf(ana).values()].filter((s) => test(s.x));
const isFrequency = (x) => /freq/i.test(x.quantity ?? "") || x.unit === "Hz";
const isCycles = (x) => /cycle/i.test(x.quantity ?? "");

// What each tile shows (keys as in layout.json n04/n15.tests): the domain whose judgement
// marks it, the test whose record it draws (else the domain's first test with a record),
// the field drawn as a contour and the charts.
const TILES = {
  crash: { domain: "crash", test: "C1", what: "応力と減速度", field: "stress",
    charts: [{ name: "減速度", pick: (a) => timed(a, (c) => c === "deceleration") }] },
  nvh: { domain: "nvh", test: "N1", what: "モードの形", field: "mode_shape",
    charts: [{ name: "周波数特性", pick: (a) => against(a, isFrequency) }] },
  strength: { domain: "strength", test: "R3", what: "疲労度", field: "damage", side: true,
    charts: [
      { name: "悪路の輪の荷重", pick: (a) => timed(a, (c) => c.startsWith("wheel_load.")) },
      { name: "S-N 線図", pick: (a) => against(a, isCycles), log: true },
    ] },
  thermal: { domain: "thermal", test: "T1", what: "部品の温度", field: "temperature", tags: true },
  handling: { domain: "handling", test: "S3", what: "ダブルレーンチェンジ" },
  power: { domain: "powertrain", test: "P2", what: "発進" },
  aero: { domain: "aero", test: "F1", what: "横風" },
  assembly: { domain: "assembly", what: "干渉", assembly: true },
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
function recordFor(s, def, version) {
  const cases = (s.project?.load_cases ?? []).filter((c) => !BEYOND.test(c.load_case_id));
  const anas = s.order.analyses.map((id) => s.analyses[id]).filter((a) => a.status === "ok" && a.design_version === version);
  const pick = (list) => {
    for (const lc of list) {
      const ana = anas.filter((a) => a.load_case_id === lc.load_case_id && draws(def, a)).at(-1);
      if (ana) return { lc, ana };
    }
    return null;
  };
  return (def.test ? pick(cases.filter((c) => c.test_id === def.test)) : null) ?? pick(cases.filter((c) => c.domain === def.domain));
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
    const missing = GRID_KEYS.filter((k) => at(spec, k) === undefined);
    const top = TOP_KEYS.filter((k) => at(layout, k) === undefined);
    const why = [missing.length && `layout.json の ${name} に無い: ${missing.join(", ")}`, top.length && `layout.json に無い: ${top.join(", ")}`].filter(Boolean);
    if (why.length) throw new Error(why.join(" / "));
    this.name = name;
    this.layout = layout;
    this.spec = spec;
    this.s = s;
    const v = params.has("version") ? Number(params.get("version")) : s.currentVersion;
    if (!s.designs[v]) throw new Error(`版 ${params.get("version") ?? v} の記録が無い (この記録の版: ${s.versions.join(", ") || "無し"})`);
    this.version = v;
    this.design = s.designs[v];
    this.textPx = Math.max(layout.min_text_px, spec.tile.rate.size_px);
    this.stage = $("stage");
    this.stage.style.background = layout.colors?.night ?? "#131311";
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.build();
  }

  build() {
    const sp = this.spec;
    const st = sp.tile;
    const rects = gridRects(sp.grid);
    if (sp.tests.length > rects.length) throw new Error(`layout.json の ${this.name}.tests は ${sp.tests.length} 枚、grid は ${rects.length} 枚分`);
    document.documentElement.dataset.version = String(this.version);
    this.tiles = sp.tests.map((key, i) => {
      const r = rects[i];
      const def = TILES[key];
      const box = `${this.name}.tile.${key}`;
      const el = document.createElement("div");
      el.className = "tile grid-tile";
      el.dataset.box = box;
      el.dataset.domain = def?.domain ?? "";
      Object.assign(el.style, {
        left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`,
        background: this.layout.colors?.tile_bg ?? "#f7f4ec",
        border: `${st.border_px}px solid ${this.layout.colors?.tile_border ?? "#3b3934"}`,
        borderRadius: `${st.radius_px}px`,
      });
      if (!clearOfBand(r, this.layout)) el.dataset.outside = "1";
      const inner = { w: r.w - 2 * st.border_px, h: r.h - 2 * st.border_px };
      const tile = { key, def, el, rect: r, inner, i };
      tile.start = sp.times.tiles_s + i * sp.times.tile_stagger_s;
      tile.land = tile.start + sp.times.tile_in_s;
      this.stage.append(el);
      if (!def) {
        el.innerHTML = this.labelHtml(box, key);
        el.insertAdjacentHTML("beforeend", `<span class="none" style="font-size:${this.textPx}px">この札の中身はまだ決めていない (${esc(key)})</span>`);
        return tile;
      }
      el.innerHTML = `<canvas></canvas>` + this.labelHtml(box, `${DOMAIN_JA[def.domain] ?? def.domain}・${def.what}`) + this.markHtml(box, def.domain) +
        `<span class="rate" data-box="${box}.rate" style="right:${st.label.x}px;bottom:${st.label.y}px;font-size:${st.rate.size_px}px"></span>`;
      tile.canvas = el.querySelector("canvas");
      tile.rateEl = el.querySelector(".rate");
      if (def.assembly) this.makeAssembly(tile);
      else {
        tile.rec = recordFor(this.s, def, this.version);
        if (tile.rec) this.makeView(tile);
        else el.insertAdjacentHTML("beforeend", `<span class="none" style="font-size:${this.textPx}px">版 ${this.version} の記録が無い (${esc(def.test ?? def.domain)})</span>`);
      }
      return tile;
    });
  }

  labelHtml(box, text) {
    const L = this.spec.tile.label;
    return `<b data-box="${box}.label" style="left:${L.x}px;top:${L.y}px;font-size:${L.size_px}px">${esc(text)}</b>`;
  }

  // The domain's judgement of the version: green ✓, red ✕, none when it has not been judged.
  markHtml(box, domain) {
    const state = this.s.judgements[`${this.version}|${domain}`]?.state;
    const sign = state === "green" ? "✓" : state === "red" ? "✕" : null;
    if (!sign) return "";
    const L = this.spec.tile.label;
    return `<span class="mark ${state === "green" ? "ok" : "miss"}" data-box="${box}.mark" data-state="${state}" style="right:${L.x}px;top:${L.y}px;font-size:${this.spec.tile.mark.size_px}px">${sign}</span>`;
  }

  // The top of the space under the tile's top line (its label and mark).
  belowHead() {
    const st = this.spec.tile;
    return st.label.y + Math.max(st.label.size_px, st.mark.size_px) + st.label.y / 2;
  }

  // A 3D view of the design through the shared renderer in a part of the tile.
  newView(tile, design, x, w, y = 0, h = tile.inner.h) {
    const v = new Workbench3D(null, params.get("tilelook") === "real" ? "real" : "zu", { renderer: this.renderer });
    v.setDesign(design);
    tile.area = { x, y, w, h };
    Object.assign(tile.canvas.style, { left: `${x}px`, top: `${y}px`, width: `${w}px`, height: `${h}px` });
    v.resize(w, h);
    return v;
  }

  makeView(tile) {
    const { lc, ana } = tile.rec;
    const def = tile.def;
    const pad = this.spec.tile.label.x;
    const top = this.belowHead();
    // charts with something to draw; a side tile keeps them in a column at its left
    const charts = (def.charts ?? []).map((c) => ({ ...c, list: c.pick(ana) })).filter((c) => c.list.length);
    const side = def.side && charts.length;
    const colW = side ? Math.round(tile.inner.w * 0.5) : 0;
    const v = this.newView(tile, this.s.designs[ana.design_version] ?? this.design, colW, tile.inner.w - colW);
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
    tile.view = v;
    tile.motion = motion;
    if (ct?.frequency) tile.el.querySelector("b").insertAdjacentHTML("beforeend", ` <small>${fmt(ct.frequency, 3)} Hz</small>`);

    // charts: stacked in the left column, or one at the top right over the view
    tile.cursors = [];
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
      tile.el.insertAdjacentHTML("beforeend", `<svg class="chart" data-box="${this.name}.tile.${tile.key}.chart" style="${pos};top:${b.top}px" width="${b.w}" height="${b.h}" viewBox="0 0 ${b.w} ${b.h}">${svg}</svg>`);
      if (X) {
        tile.cursors.push({ X, el: tile.el.querySelector("svg.chart:last-of-type .cur") });
        const ts = b.c.list.flatMap((s) => [s.t_s[0], s.t_s.at(-1)]);
        span ??= [Math.min(...ts), Math.max(...ts)];
      }
    }
    tile.span = span;

    // part values (temperatures): the view's own tags, drawn here as text at their places
    if (def.tags) {
      tile.tags = v.overGhosts.children
        .filter((o) => o.isCSS2DObject)
        .map((o) => {
          const span = document.createElement("span");
          span.className = `tag${o.element.classList.contains("lbl-over") ? " over" : ""}`;
          span.style.fontSize = `${this.textPx}px`;
          span.innerHTML = o.element.innerHTML;
          tile.el.append(span);
          return { o, el: span };
        });
    }
  }

  // Assembly: the design of the version with what interferes boxed in red, and in words
  // the interferences (each pair once) or ✓ when there are none, with the steps' count.
  makeAssembly(tile) {
    const asm = assemblyFor(this.s, this.version);
    const el = tile.el;
    if (!asm) {
      el.insertAdjacentHTML("beforeend", `<span class="none" style="font-size:${this.textPx}px">版 ${this.version} の組み立ての記録が無い</span>`);
      return;
    }
    // the words first (at most two pairs, then how many more), the view under them
    const L = this.spec.tile.label;
    const steps = asm.steps.length ? `・手順 ${asm.steps.length}` : "";
    const moving = asm.hits.filter((x) => x.state === "motion").length;
    const pair = (x) => `${esc(thingName(this.design, x.a))} × ${esc(thingName(this.design, x.b))} ${esc(fmt(x.depth_mm, 3))} mm`;
    const lines = asm.hits.length
      ? [`<span class="miss">干渉 ${asm.hits.length} 件</span>${moving ? ` (動いたとき ${moving})` : ""}${steps}`,
         ...asm.hits.slice(0, 2).map(pair), ...(asm.hits.length > 2 ? [`ほか ${asm.hits.length - 2} 件`] : [])]
      : [`<span class="ok">✓ 干渉なし</span>${steps}`];
    const textW = tile.inner.w - 2 * L.x;
    el.insertAdjacentHTML("beforeend", `<div class="asm" data-box="${this.name}.tile.${tile.key}.asm" style="left:${L.x}px;top:${this.belowHead()}px;width:${textW}px;font-size:${this.textPx}px">${lines.map((t) => `<div>${t}</div>`).join("")}</div>`);
    const below = this.belowHead() + el.querySelector(".asm").offsetHeight + L.y / 2;
    const v = this.newView(tile, this.design, 0, tile.inner.w, below, tile.inner.h - below);
    v.setScene(null, null);
    v.setContour(null);
    v.setToggles({ parts: true, contour: false, arrows: false, cg: false, names: false, limit: false });
    v.fitPoints(v.contentPoints(), VIEWS.default, 1.1);
    const marks = new THREE.Group();
    const mat = new THREE.MeshBasicMaterial({ color: RED, transparent: true, opacity: 0.45, depthWrite: false });
    for (const ref of new Set(asm.hits.flatMap((x) => [x.a, x.b]))) {
      const id = bareId(ref);
      const m = v.members[id];
      const p = (this.design.parts ?? []).find((x) => x.id === id);
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
    tile.view = v;
  }

  // Part value tags at their parts' places in the view; a tag that would cover one
  // already placed (lower ones first) goes up above it.
  placeTags(tile) {
    const A = tile.area;
    const placed = [];
    const items = tile.tags.map((g) => {
      const q = g.o.getWorldPosition(new THREE.Vector3()).project(tile.view.camera);
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
    const w = Math.max(...this.tiles.map((t) => t.area?.w ?? 0), 1);
    const h = Math.max(...this.tiles.map((t) => t.area?.h ?? 0), 1);
    this.renderer.setSize(Math.ceil(w * scale), Math.ceil(h * scale), false);
    for (const t of this.tiles) {
      if (!t.canvas || !t.area) continue;
      t.canvas.width = Math.round(t.area.w * scale);
      t.canvas.height = Math.round(t.area.h * scale);
    }
  }

  draw(t) {
    const sp = this.spec;
    for (const tile of this.tiles) {
      const p = Math.max(0, Math.min(1, (t - tile.start) / sp.times.tile_in_s));
      tile.el.style.opacity = String(p);
      tile.el.style.visibility = p > 0 ? "visible" : "hidden";
      if (!tile.view || p <= 0) continue;
      // the record plays from the landing to the end, squeezed to fit
      let tau = 0;
      if (tile.span) {
        const q = squeeze(t, tile.land, sp.duration_s, tile.span[0], tile.span[1]);
        tau = q.tau;
        tile.rateEl.textContent = rateLabel(q.rate);
      }
      if (tile.motion) tile.view.setFrame(tile.motion.frame(tau));
      tile.view.animate(t, false); // a mode shape swings with the stage clock
      for (const c of tile.cursors ?? []) c.el?.setAttribute("transform", `translate(${c.X(tau).toFixed(1)} 0)`);
      tile.view.renderInto(tile.canvas.getContext("2d"), tile.canvas.width, tile.canvas.height, this.scale);
      if (tile.tags) this.placeTags(tile);
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

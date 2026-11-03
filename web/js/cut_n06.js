// N06 (cut.html?cut=n06&run=<id>): designs that do not work, side by side.
//
// One tile per key of layout.json n06.tests, in the layout's grid: the record of the
// test's load case past the limit (its id ends in -beyond) plays in the tile, from the
// time the tile comes in to the end of the cut. Where the limit is passed is drawn in
// the same red as N01 / N02: a wheel that leaves the road (its ring, and the pose at the
// end in see-through red), members over the allowable stress (the frame coloured by the
// static analysis of the same load case on the allowable's band, as N01), and for a
// distance limit (a stop) the stretch of road past the limit. The tile's words say what
// happened, read from the record's results and its criterion; no number is shown.
//
// Positions, sizes, times and colours come only from layout.json n06 (N06_KEYS).

import * as THREE from "three";
import { clearOfBand, cubicBezier, gridRects, squeeze } from "./compose.js";
import { contourOf, fieldsOf } from "./fields.js";
import { LENGTH, motionOf } from "./series.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";
import { requireKeys } from "./cut_n03.js";

const $ = (id) => document.getElementById(id);

export const N06_KEYS = [
  "duration_s",
  "grid.x", "grid.y", "grid.cols", "grid.rows", "grid.tile_w", "grid.tile_h", "grid.gap_x", "grid.gap_y",
  "tests",
  "tile.border_px", "tile.radius_px", "tile.label.x", "tile.label.y", "tile.label.size_px",
  "times.tiles_s", "times.tile_in_s", "times.tile_stagger_s",
  "colors.bg", "colors.panel", "colors.rule",
  "overlay.color", "overlay.opacity",
];

// What each tile shows (keys as in layout.json n06.tests): the test whose case past the
// limit it plays, and its name.
const TILES = {
  step: { test: "P1", name: "段差" },
  frame: { test: "R1", name: "段差の力とフレーム" },
  slope: { test: "S5", name: "凸凹の横斜面" },
  wind: { test: "F1", name: "横風" },
  brake: { test: "S4", name: "急ブレーキ" },
  tilt: { test: "S1", name: "傾斜台" },
  dlc: { test: "S3", name: "ダブルレーンチェンジ" },
  heat: { test: "T1", name: "電池の熱" },
};

// What happened, in words, from a result of the record (a flag 0 / 1).
const FLAGS = [
  { q: "climbed", hit: (v) => v < 1, ja: "越えられない" },
  { q: "tipped", hit: (v) => v >= 1, ja: "倒れる" },
  { q: "wheel_lift", hit: (v) => v >= 1, ja: "輪が浮く" },
  { q: "slides_first", hit: (v) => v >= 1, ja: "横に流される" },
];
// What a criterion that is not met means, by its quantity.
const CRITERION_JA = {
  stopping_distance: "止まりきれない",
  max_stress: "フレームが許容を超える",
  critical_wind_speed: "風に耐えられない",
  tip_slope_angle: "斜面で倒れる",
  tilt_angle: "傾いて倒れる",
  min_side_load: "片側の輪が浮く",
  temp_battery_max: "電池が熱くなりすぎる",
};

const BEYOND = /-beyond$/;
const ok = (a) => a?.status === "ok";
const resultOf = (ana, q) => (ana?.results ?? []).find((r) => r.quantity === q) ?? null;

// Whether a recorded result meets a criterion ({quantity, op, limit}); null when it cannot say.
function meets(criterion, ana) {
  const r = resultOf(ana, criterion?.quantity);
  const lim = criterion?.limit;
  if (!r || !lim || r.unit !== lim.unit) return null;
  const v = r.value;
  const L = lim.value;
  return { "<=": v <= L, "<": v < L, ">=": v >= L, ">": v > L }[criterion.op] ?? null;
}

const easeOut = cubicBezier(0.2, 0.8, 0.3, 1);

export class CutN06 {
  constructor(layout, s) {
    this.spec = requireKeys(layout, "n06", N06_KEYS);
    this.layout = layout;
    this.s = s;
    this.stage = $("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.build();
  }

  // The newest ok analyses of a test's load case past the limit: one with a motion, one
  // with a stress field (a still).
  casesOf(testId) {
    const lc = (this.s.project?.load_cases ?? []).find((c) => c.test_id === testId && BEYOND.test(c.load_case_id));
    if (!lc) return null;
    const anas = this.s.order.analyses.map((id) => this.s.analyses[id]).filter((a) => a.load_case_id === lc.load_case_id && ok(a));
    const dyn = anas.filter((a) => !!motionOf(a)).at(-1) ?? null;
    const still = anas.filter((a) => fieldsOf(a, { mesh: false }).some((f) => f.kind === "stress")).at(-1) ?? null;
    const any = anas.at(-1) ?? null;
    return any ? { lc, dyn, still, any } : { lc, dyn: null, still: null, any: null };
  }

  // The allowable stress criterion of the run (a load case judged on max_stress in the unit).
  allowable(unit) {
    return (this.s.project?.load_cases ?? []).find((c) => c.criterion?.quantity === "max_stress" && c.criterion?.limit?.unit === unit)?.criterion ?? null;
  }

  // What happened, in words: the flags of the records, then the criteria not met.
  outcome(c) {
    const words = [];
    for (const a of [c.dyn, c.still, c.any].filter(Boolean)) {
      for (const f of FLAGS) {
        const r = resultOf(a, f.q);
        if (r && f.hit(r.value) && !words.includes(f.ja)) words.push(f.ja);
      }
    }
    const crits = [c.lc.criterion, c.still ? this.allowable(resultOf(c.still, "max_stress")?.unit) : null].filter(Boolean);
    for (const cr of crits) {
      for (const a of [c.dyn, c.still, c.any].filter(Boolean)) {
        if (meets(cr, a) === false) {
          const ja = CRITERION_JA[cr.quantity] ?? "限界を超える";
          // a flag's words inside the criterion's ("倒れる" in "斜面で倒れる") are said once
          for (let k = words.length - 1; k >= 0; k -= 1) if (ja.includes(words[k])) words.splice(k, 1);
          if (!words.includes(ja)) words.push(ja);
          break;
        }
      }
    }
    return words;
  }

  build() {
    const sp = this.spec;
    const st = sp.tile;
    const rects = gridRects(sp.grid);
    if (sp.tests.length > rects.length) throw new Error(`layout.json の n06.tests は ${sp.tests.length} 枚、grid は ${rects.length} 枚分`);
    this.tiles = sp.tests.map((key, i) => {
      const r = rects[i];
      const def = TILES[key];
      const box = `n06.tile.${key}`;
      const el = document.createElement("div");
      el.className = "n06-tile";
      el.dataset.box = box;
      Object.assign(el.style, {
        left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`,
        background: sp.colors.panel, border: `${st.border_px}px solid ${sp.colors.rule}`, borderRadius: `${st.radius_px}px`,
      });
      if (!clearOfBand(r, this.layout)) el.dataset.outside = "1";
      el.innerHTML = `<canvas></canvas>`;
      this.stage.append(el);
      const inner = { w: r.w - 2 * st.border_px, h: r.h - 2 * st.border_px };
      const tile = { key, def, el, rect: r, inner, canvas: el.querySelector("canvas"), start: sp.times.tiles_s + i * sp.times.tile_stagger_s };
      tile.land = tile.start + sp.times.tile_in_s;
      const c = def ? this.casesOf(def.test) : null;
      const words = c?.any ? this.outcome(c) : [];
      tile.miss = words.length > 0;
      el.dataset.miss = tile.miss ? "1" : "0";
      // the mark at the top right, the name and what happened at the top left, short of the mark
      const L = st.label;
      if (tile.miss) {
        const mark = document.createElement("span");
        mark.className = "mark";
        mark.dataset.box = `${box}.mark`;
        Object.assign(mark.style, { right: `${L.x}px`, top: `${L.y}px`, fontSize: `${L.size_px}px`, color: sp.overlay.color });
        mark.textContent = "✕";
        el.append(mark);
      }
      const label = document.createElement("b");
      label.dataset.box = `${box}.label`;
      Object.assign(label.style, { left: `${L.x}px`, top: `${L.y}px`, fontSize: `${L.size_px}px` });
      label.textContent = def ? def.name : key;
      if (words.length) {
        const why = document.createElement("span");
        why.className = "why";
        why.style.color = sp.overlay.color;
        why.textContent = words.join("・");
        label.append(document.createElement("br"), why);
      }
      el.append(label);
      this.fitLabel(tile, label);
      if (!def) this.note(tile, `この札の中身はまだ決めていない (${key})`);
      else if (!c?.any) this.note(tile, `記録がまだ無い (${def.test} の限界の外)`);
      else this.makeView(tile, c);
      return tile;
    });
  }

  // The label ends short of the mark: its text steps down a pixel at a time, not below min_text_px.
  fitLabel(tile, b) {
    const L = this.spec.tile.label;
    const room = tile.inner.w - 2 * L.x - (tile.miss ? L.size_px + L.x : 0);
    let px = L.size_px;
    b.style.whiteSpace = "nowrap";
    while (b.offsetWidth > room && px > this.layout.min_text_px) {
      px -= 1;
      b.style.fontSize = `${px}px`;
    }
    // still too wide at min_text_px: the lines wrap inside the room
    b.style.maxWidth = `${room}px`;
    b.style.whiteSpace = "normal";
  }

  note(tile, text) {
    const n = document.createElement("span");
    n.className = "none";
    Object.assign(n.style, { left: `${this.spec.tile.label.x}px`, bottom: `${this.spec.tile.label.y}px`, fontSize: `${this.layout.min_text_px}px` });
    n.textContent = text;
    tile.el.append(n);
  }

  makeView(tile, c) {
    const ana = c.dyn ?? c.still ?? c.any;
    const v = new Workbench3D(null, "zu", { renderer: this.renderer });
    v.setDesign(this.s.designs[ana.design_version] ?? this.s.designs[this.s.currentVersion]);
    v.setLoading(c.lc.params?.loading ?? null);
    const motion = c.dyn ? motionOf(c.dyn) : null;
    v.setMotion(motion);
    v.setScene(c.lc, ana);
    // the frame on the allowable stress's band, when the case has a still with stress (as N01)
    const field = c.still ? fieldsOf(c.still, { mesh: false }).find((f) => f.kind === "stress") : null;
    const ct = field ? contourOf(c.still, field, this.allowable(field.unit) ?? c.lc.criterion) : null;
    v.setContour(ct);
    v.setVectors(null);
    v.setToggles({ parts: true, contour: true, arrows: false, cg: true, names: false, limit: true });
    v.resize(tile.inner.w, tile.inner.h);
    if (motion) v.setFrame(motion.frame(motion.t0));
    const pts = v.contentPoints();
    for (const o of v.sceneDesc?.objects ?? []) if (o.kind === "step") pts.push(new THREE.Vector3(o.position_mm[0] - o.size_mm[0] / 2, o.position_mm[1], o.position_mm[2]));
    v.fitPoints(pts, VIEWS[v.view ?? "default"], 1.15);
    tile.view = v;
    tile.motion = motion;
    tile.contour = ct;
    tile.el.dataset.over = ct?.limitKnown && Object.values(ct.values ?? {}).some((x) => x > ct.max) ? "1" : "0";
    tile.el.dataset.lift = motion?.liftT !== null && motion?.liftT !== undefined && v.liftIsLimit ? "1" : "0";
    tile.el.dataset.past = this.markPast(v, c, motion) ? "1" : "0";
  }

  // A distance limit (op <= / <, in a length unit) that the record passes: the road from
  // the limit to the recorded distance, counted back from where the body stops, in red.
  markPast(v, c, motion) {
    const cr = c.lc.criterion;
    const r = resultOf(c.dyn, cr?.quantity);
    if (!motion || !r || !cr?.limit || !(cr.op === "<=" || cr.op === "<") || !LENGTH[r.unit] || r.unit !== cr.limit.unit) return false;
    const lim = cr.limit.value * LENGTH[r.unit];
    const got = r.value * LENGTH[r.unit];
    if (!(got > lim)) return false;
    // the stop is where the body is at the record's end; the limit's line is got - lim behind it
    const x1 = motion.frame(motion.t1).body[0];
    const dir = Math.sign(x1 - motion.frame(motion.t0).body[0]) || 1;
    const x0 = x1 - dir * got;
    const ys = (v.design?.supports ?? []).map((s) => s.position_mm[1]);
    const width = (ys.length ? Math.max(...ys) - Math.min(...ys) : 1000) * 1.6;
    const yc = ys.length ? (Math.max(...ys) + Math.min(...ys)) / 2 : 0;
    const O = this.spec.overlay;
    // drawn over the ground (it lies on it), under the vehicle
    const mat = (opacity) => new THREE.MeshBasicMaterial({ color: O.color, transparent: true, opacity, depthWrite: false, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4 });
    const strip = new THREE.Mesh(new THREE.PlaneGeometry(got - lim, width), mat(O.opacity));
    strip.position.set(x0 + (dir * (lim + got)) / 2, yc, 5);
    const edge = new THREE.Mesh(new THREE.PlaneGeometry(40, width), mat(1));
    edge.position.set(x0 + dir * lim, yc, 6);
    for (const m of [strip, edge]) m.renderOrder = 1;
    v.iso.add(strip, edge);
    // the camera stays on where the vehicle stops and the limit's line (it drives in and runs past it)
    const end = motion.frame(motion.t1).body;
    const pts = v.contentPoints().map((p) => p.add(new THREE.Vector3(end[0], end[1], end[2])));
    pts.push(new THREE.Vector3(x0 + dir * lim, yc - width / 2, 0), new THREE.Vector3(x0 + dir * lim, yc + width / 2, 0));
    v.fitPoints(pts, VIEWS[v.view ?? "side"], 1.15);
    v.noFollow = true;
    return true;
  }

  resize(scale) {
    this.scale = scale;
    const g = this.spec.grid;
    this.renderer.setSize(Math.ceil(g.tile_w * scale), Math.ceil(g.tile_h * scale), false);
    for (const t of this.tiles) {
      t.canvas.width = Math.round(t.inner.w * scale);
      t.canvas.height = Math.round(t.inner.h * scale);
    }
  }

  draw(t) {
    const sp = this.spec;
    for (const tile of this.tiles) {
      const p = Math.max(0, Math.min(1, (t - tile.start) / sp.times.tile_in_s));
      const e = easeOut(p);
      tile.el.style.opacity = String(e);
      tile.el.style.transform = `scale(${0.92 + 0.08 * e})`;
      tile.el.style.visibility = p > 0 ? "visible" : "hidden";
      tile.el.dataset.p = String(p);
      if (!tile.view || p <= 0) continue;
      if (tile.motion) {
        const m = tile.motion;
        const { tau } = squeeze(t, tile.land, sp.duration_s, m.t0, m.t1);
        tile.el.dataset.tau = String(tau);
        tile.view.setFrame(m.frame(tau));
      }
      tile.view.renderInto(tile.canvas.getContext("2d"), tile.canvas.width, tile.canvas.height, this.scale);
    }
  }
}

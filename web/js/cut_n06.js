// N06 (cut.html?cut=n06&run=<id>): designs that do not work, side by side.
//
// One tile per key of layout.json n06.tests, in the layout's grid. A test tile plays the
// record of its test's load case past the limit (its id ends in -beyond), from the time
// the tile lands to the end of the cut. Where the limit is passed is drawn in the same red
// as N01 / N02: a wheel that leaves the road (its ring, and the pose at the end in
// see-through red), members over the allowable stress (the frame coloured by the static
// analysis of the same load case on the allowable's band, as N01), a part over its
// temperature limit, and for a distance limit (a stop) the stretch of road past it. The
// assembly tile builds the version whose assembly records hold interferences (assembly.js
// AssemblyStage) with the parts that interfere boxed in red. The tile's one tag is ✕ and
// what happened, read from the record's results, criteria and interferences (the test's
// name only when there is nothing to say); no number is shown.
//
// With the story's limit on things moving at once (layout.json story), a tile moves while
// it comes in and while its record plays, and each record plays over limits.moving x
// times.tile_stagger_s less times.tile_in_s from its landing, then holds its end: so no more
// than limits.moving tiles move at one time. Without a story the records play to the end
// of the cut.
//
// Positions, sizes, times and colours come only from layout.json n06 (N06_KEYS).

import * as THREE from "three";
import { clearOfBand, cubicBezier, gridRects, squeeze } from "./compose.js";
import { contourOf, fieldsOf, overOf } from "./fields.js";
import { LENGTH, motionOf } from "./series.js";
import { AssemblyStage } from "./assembly.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";
import { requireKeys, storyOf } from "./cut_n03.js";

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

// Not in layout.json (written here until the promo department gives keys for them):
// how a tile comes in, from this scale to 1 along this CSS cubic-bezier over times.tile_in_s.
export const N06_APPEAR = { from_scale: 0.92, ease: [0.2, 0.8, 0.3, 1] };

// What each tile shows (keys as in layout.json n06.tests): the tests whose case past the
// limit it plays (the first that has a record), its name, and what it reads:
//   field   the field drawn on the vehicle ("stress": on the allowable's band, as N01;
//           "temperature": on its own range, a part over its limit in red; null: none)
//   from    the records its words come from ("motion", "still", else both)
//   assembly  the assembly's interferences instead of a test
const TILES = {
  step_break: { tests: ["P1", "R1"], name: "段差とフレーム", field: "stress", from: "still" },
  tilt_over: { tests: ["S1", "S5"], name: "坂", field: null },
  step_stuck: { tests: ["P1"], name: "段差", field: null, from: "motion" },
  battery_hot: { tests: ["T1"], name: "電池の熱", field: "temperature" },
  parts_clash: { name: "組み立て", assembly: true },
  rollover: { tests: ["S3"], name: "曲がる", field: null },
  // earlier keys
  step: { tests: ["P1"], name: "段差", field: "stress" },
  frame: { tests: ["R1"], name: "段差の力とフレーム", field: "stress" },
  slope: { tests: ["S5"], name: "凸凹の横斜面", field: null },
  wind: { tests: ["F1"], name: "横風", field: null },
  brake: { tests: ["S4"], name: "急ブレーキ", field: null },
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
// "member rail_L_AS1" / "part:wheel_FL" -> the id in the design.
const bareId = (ref) => String(ref ?? "").replace(/^(member|part|node)[: ]\s*/, "");

// Whether a recorded result meets a criterion ({quantity, op, limit}); null when it cannot say.
function meets(criterion, ana) {
  const r = resultOf(ana, criterion?.quantity);
  const lim = criterion?.limit;
  if (!r || !lim || r.unit !== lim.unit) return null;
  const v = r.value;
  const L = lim.value;
  return { "<=": v <= L, "<": v < L, ">=": v >= L, ">": v > L }[criterion.op] ?? null;
}

const easeIn = cubicBezier(...N06_APPEAR.ease);

export class CutN06 {
  constructor(layout, s) {
    this.spec = requireKeys(layout, "n06", N06_KEYS);
    this.layout = layout;
    this.story = storyOf(layout, "n06");
    const T = this.spec.times;
    // how long each record plays from its landing (to the end of the cut without a limit)
    this.play = this.story.limits ? Math.max(0, this.story.limits.moving * T.tile_stagger_s - T.tile_in_s) : null;
    this.s = s;
    this.stage = $("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.build();
  }

  // The newest ok analyses of the first of the tests' load cases past the limit that has
  // one: one with a motion, one with the tile's field (a still), and the newest of all.
  casesOf(def) {
    const kind = def.field ?? "stress";
    for (const testId of def.tests) {
      const lc = (this.s.project?.load_cases ?? []).find((c) => c.test_id === testId && BEYOND.test(c.load_case_id));
      if (!lc) continue;
      const anas = this.s.order.analyses.map((id) => this.s.analyses[id]).filter((a) => a.load_case_id === lc.load_case_id && ok(a));
      if (!anas.length) continue;
      const dyn = anas.filter((a) => !!motionOf(a)).at(-1) ?? null;
      const still = anas.filter((a) => fieldsOf(a, { mesh: false }).some((f) => f.kind === kind)).at(-1) ?? null;
      return { lc, dyn, still, any: anas.at(-1) };
    }
    return null;
  }

  // The allowable stress criterion of the run (a load case judged on max_stress in the unit).
  allowable(unit) {
    return (this.s.project?.load_cases ?? []).find((c) => c.criterion?.quantity === "max_stress" && c.criterion?.limit?.unit === unit)?.criterion ?? null;
  }

  // What happened, in words: the flags of the records, then the criteria not met.
  outcome(c, def) {
    const sources = (def.from === "motion" ? [c.dyn] : def.from === "still" ? [c.still] : [c.dyn, c.still, c.any]).filter(Boolean);
    const words = [];
    for (const a of sources) {
      for (const f of FLAGS) {
        const r = resultOf(a, f.q);
        if (r && f.hit(r.value) && !words.includes(f.ja)) words.push(f.ja);
      }
    }
    const still = sources.includes(c.still) ? c.still : null;
    const crits = [c.lc.criterion, still ? this.allowable(resultOf(still, "max_stress")?.unit) : null].filter(Boolean);
    for (const cr of crits) {
      for (const a of sources) {
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

  // The assembly of the newest version whose assembly records hold interferences: the
  // steps of its newest analysis that has them, and every interference of the version
  // (each pair, state and condition once).
  clashOf() {
    const ids = new Set((this.s.project?.load_cases ?? []).filter((c) => c.domain === "assembly").map((c) => c.load_case_id));
    const all = this.s.order.analyses.map((id) => this.s.analyses[id]).filter((a) => ok(a) && (ids.has(a.load_case_id) || Array.isArray(a.assembly)));
    const versions = [...new Set(all.filter((a) => (a.interferences ?? []).length).map((a) => a.design_version))].sort((a, b) => a - b);
    const v = versions.at(-1);
    if (v === undefined) return null;
    const anas = all.filter((a) => a.design_version === v);
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
    const withSteps = anas.filter((a) => Array.isArray(a.assembly) && a.assembly.length).at(-1) ?? null;
    const design = this.s.designs[v] ?? null;
    const rec = withSteps && design ? { ana: withSteps, steps: [...withSteps.assembly].sort((x, y) => x.order - y.order), interferences: hits, design } : null;
    return { version: v, hits, rec, design };
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
      let words = [];
      let c = null;
      let clash = null;
      if (def?.assembly) {
        clash = this.clashOf();
        if (clash?.hits.length) words = [clash.hits.every((x) => x.state === "motion") ? "動くと部品がぶつかる" : "部品がぶつかる"];
      } else if (def) {
        c = this.casesOf(def);
        if (c) words = this.outcome(c, def);
      }
      tile.miss = words.length > 0;
      el.dataset.miss = tile.miss ? "1" : "0";
      this.labelTile(tile, def ? def.name : key, words);
      if (!def) this.note(tile, `この札の中身はまだ決めていない (${key})`);
      else if (def.assembly) {
        if (clash) this.makeAssembly(tile, clash);
        else this.note(tile, "記録がまだ無い (組み立ての干渉)");
      } else if (!c) this.note(tile, `記録がまだ無い (${def.tests.join("・")} の限界の外)`);
      else this.makeView(tile, c, def);
      return tile;
    });
  }

  // One tag at the top left: ✕ and what happened when the records say it, else the test's name.
  labelTile(tile, name, words) {
    const sp = this.spec;
    const L = sp.tile.label;
    const box = tile.el.dataset.box;
    const label = document.createElement("b");
    label.dataset.box = `${box}.label`;
    label.dataset.kind = "label";
    Object.assign(label.style, { left: `${L.x}px`, top: `${L.y}px`, fontSize: `${Math.max(L.size_px, this.story.bodyPx)}px` });
    if (words.length) {
      const mark = document.createElement("span");
      mark.className = "mark";
      mark.dataset.box = `${box}.mark`;
      mark.style.color = sp.overlay.color;
      mark.textContent = "✕";
      const why = document.createElement("span");
      why.className = "why";
      why.style.color = sp.overlay.color;
      why.textContent = words.join("・");
      label.append(mark, " ", why);
    } else label.textContent = name;
    tile.el.append(label);
    this.fitLabel(tile, label);
  }

  // The tag ends inside the tile: its text steps down a pixel at a time, not below the
  // story's body text size (min_text_px without a story); then it wraps.
  fitLabel(tile, b) {
    const L = this.spec.tile.label;
    const room = tile.inner.w - 2 * L.x;
    let px = parseFloat(b.style.fontSize);
    b.style.whiteSpace = "nowrap";
    while (b.offsetWidth > room && px > this.story.bodyPx) {
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

  makeView(tile, c, def) {
    const ana = c.dyn ?? c.still ?? c.any;
    const v = new Workbench3D(null, "zu", { renderer: this.renderer });
    v.setDesign(this.s.designs[ana.design_version] ?? this.s.designs[this.s.currentVersion]);
    v.setLoading(c.lc.params?.loading ?? null);
    const motion = c.dyn ? motionOf(c.dyn) : null;
    v.setMotion(motion);
    v.setScene(c.lc, ana);
    // stress on the allowable's band (as N01); a temperature on its own range, the part the
    // load case limits held to it
    const field = def.field && c.still ? fieldsOf(c.still, { mesh: false }).find((f) => f.kind === def.field) : null;
    const crit = def.field === "stress" ? this.allowable(field?.unit) ?? c.lc.criterion : c.lc.criterion;
    const ct = field ? contourOf(c.still, field, crit) : null;
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
    const overMember = ct?.limitKnown && Object.values(ct.values ?? {}).some((x) => x > ct.max);
    const overPart = Object.entries(ct?.parts ?? {}).some(([id, x]) => overOf(ct, id, x));
    tile.el.dataset.over = overMember || overPart ? "1" : "0";
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

  // The assembly of the version with interferences: its parts go in one after another
  // (AssemblyStage, from the tile's landing to the end of the cut), and each part or member
  // that interferes is boxed in the overlay's red at its place from when its step starts
  // (as the N04 tile's boxes).
  makeAssembly(tile, clash) {
    const v = new Workbench3D(null, "zu", { renderer: this.renderer });
    const O = this.spec.overlay;
    let stage = null;
    if (clash.rec) {
      stage = new AssemblyStage(v);
      stage.setRecord(clash.rec);
    } else {
      v.setDesign(clash.design);
      v.setScene(null, null);
      v.setToggles({ parts: true, contour: false, arrows: false, cg: false, names: false, limit: false });
    }
    v.resize(tile.inner.w, tile.inner.h);
    if (stage) stage.fit(1.1, VIEWS.default);
    else v.fitPoints(v.contentPoints(), VIEWS.default, 1.1);
    // the step that brings each id in (members come with the frame's step, else are there from the start)
    const stepOf = {};
    (clash.rec?.steps ?? []).forEach((s, i) => (stepOf[s.part] = i + 1));
    const frameStep = stepOf.frame ?? 0;
    const design = clash.design;
    const mat = new THREE.MeshBasicMaterial({ color: O.color, transparent: true, opacity: Math.min(1, O.opacity * 2), depthWrite: false });
    const marks = [];
    for (const ref of new Set(clash.hits.flatMap((x) => [x.a, x.b]))) {
      const id = bareId(ref);
      const m = v.members[id];
      const p = (design?.parts ?? []).find((x) => x.id === id);
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
      if (!mesh) continue;
      mesh.renderOrder = 11;
      v.body.add(mesh);
      marks.push({ mesh, after: stepOf[id] ?? (m ? frameStep : 0) });
    }
    tile.view = v;
    tile.asm = { stage, n: clash.rec?.steps.length ?? 0, marks };
    tile.el.dataset.clash = marks.length ? "1" : "0";
    tile.el.dataset.version = String(clash.version);
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
    const A = N06_APPEAR;
    for (const tile of this.tiles) {
      const p = Math.max(0, Math.min(1, (t - tile.start) / sp.times.tile_in_s));
      const e = easeIn(p);
      tile.el.style.opacity = String(Math.min(1, e));
      tile.el.style.transform = `scale(${A.from_scale + (1 - A.from_scale) * e})`;
      tile.el.style.visibility = p > 0 ? "visible" : "hidden";
      tile.el.dataset.p = String(p);
      // the record plays from the landing over this.play (to the end of the cut without a limit)
      const end = this.play === null ? sp.duration_s : tile.land + this.play;
      const plays = !!(tile.motion || tile.asm?.stage) && t < end;
      const moving = p > 0 && (p < 1 || plays);
      // what moves is the tile's picture (its tag is not moving text)
      if (moving) tile.canvas.dataset.kind = "moving";
      else delete tile.canvas.dataset.kind;
      if (!tile.view || p <= 0) continue;
      // a span of no length holds the record's end
      const along = (t0, t1) => (end > tile.land ? squeeze(t, tile.land, end, t0, t1).tau : t1);
      if (tile.motion) {
        const m = tile.motion;
        const tau = along(m.t0, m.t1);
        tile.el.dataset.tau = String(tau);
        tile.view.setFrame(m.frame(tau));
      }
      if (tile.asm) {
        // the whole order from the landing over the same span
        const a = tile.asm;
        const u = a.stage ? along(0, a.n) : a.n;
        tile.el.dataset.tau = String(u);
        a.stage?.pose(u);
        a.stage?.lineResolution(tile.inner.w, tile.inner.h);
        let shown = 0;
        for (const mk of a.marks) {
          // from when its step starts to go in (step k goes in while u runs from k - 1 to k)
          mk.mesh.visible = mk.after === 0 || u > mk.after - 1;
          shown += mk.mesh.visible ? 1 : 0;
        }
        tile.el.dataset.marks = String(shown);
      }
      tile.view.renderInto(tile.canvas.getContext("2d"), tile.canvas.width, tile.canvas.height, this.scale);
    }
  }
}

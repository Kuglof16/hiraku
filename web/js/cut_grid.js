// N04 and N15: the grid of eight tiles (seven domains and assembly, as the app's A6)
// of a run (cut.html?cut=n04|n15&run=<id>[&version=<n>]). Each tile draws its test's
// record of a design version and nothing else in words or figures (the story: a picture
// and a tick or cross); its corner mark is the domain's judgement of that version
// (judgement.updated: green ✓, red ✕, else none). The header is one line with one number:
// N04 how many tiles are still red, N15 how many rounds it took. Positions, sizes, times
// and the order of the tiles come only from layout.json (n04 / n15); a missing key is
// named on the page.
//
// N04: one version (?version=, else the run's last), a version on the way. The tiles the
// voice names (crash, NVH, assembly) are lit from times.crash_s / nvh_s / assembly_s in
// the colour of their judgement in the record (green or red; not lit without one); from
// times.assembly_s the grid gives way to the assembly's box (n16.view), where the newest
// assembly record's steps go in one after another to the end of the cut (assembly.js).
// N15: the run's versions in order (up to ?version=), one after the other between
// times.rounds_from_s and times.all_green_s; the last one from all_green_s to the end.
// Nothing is coloured by hand: a last version that is not all green stays as it is.

import * as THREE from "three";
import { clearOfBand, squeeze } from "./compose.js";
import { contourOf, fieldsOf } from "./fields.js";
import { esc } from "./labels.js";
import { channelsOf, motionOf, xSeriesOf } from "./series.js";
import { AssemblyStage, assemblyRecord } from "./assembly.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";
import { FEATURE_KEYS, listRects, storyOf } from "./cut_n03.js";
import { TilePets, loadPets, missingPetKeys } from "./cut_pets.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);

// Every value the grid cuts read (dotted paths in their layout section).
const COMMON_KEYS = [
  "duration_s",
  "header.x", "header.y", "header.w", "header.h", "header.size_px",
  ...FEATURE_KEYS,
  "tests",
  "tile.border_px", "tile.radius_px", "tile.label.x", "tile.label.y", "tile.label.size_px",
  "tile.mark.size_px", "tile.mark.right", "tile.mark.top",
  "tile.pet.w", "tile.pet.h", "tile.pet.right", "tile.pet.bottom", "tile.pet.feature.w", "tile.pet.feature.h", "tile.rate.size_px",
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
// test whose record it draws (else the domain's first test with a record) and the field
// drawn as a contour. The charts are not drawn; a record with one of them counts as one
// the tile can draw.
const TILES = {
  handling: { test: "S3" },
  powertrain: { test: "P2" },
  aero: { test: "F1" },
  crash: { test: "C1", field: "stress",
    charts: [{ name: "減速度", pick: (a) => timed(a, (c) => c === "deceleration") }] },
  strength: { test: "R3", field: "damage",
    charts: [
      { name: "悪路の輪の荷重", pick: (a) => timed(a, (c) => c.startsWith("wheel_load.")) },
      { name: "S-N 線図", pick: (a) => against(a, isCycles), log: true },
    ] },
  nvh: { test: "N1", field: "mode_shape",
    charts: [{ name: "周波数特性", pick: (a) => against(a, isFrequency) }] },
  thermal: { test: "T1", field: "temperature" },
  assembly: { assembly: true },
};
const BEYOND = /-beyond$/;
const RED = "#c8402a";
const GREEN = "#2f7d4f"; // the pages' colour of a pass (as .ok in cut.html)

// N04: the tiles the voice names, with the key of layout.json n04.times saying when.
const NAMED = [["crash", "crash_s"], ["nvh", "nvh_s"], ["assembly", "assembly_s"]];

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
    const top = [...TOP_KEYS.filter((k) => at(layout, k) === undefined), ...missingPetKeys(layout)];
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
    this.story = storyOf(layout, name);
    this.stage = $("stage");
    this.stage.style.background = colors.bg;
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.build();
    // the pets come with /pets/pet3d.js; the page waits for them (cut.js awaits loaded)
    this.loaded = this.attachPets();
  }

  // The tiles' 3D pets (cut_pets.js), when the server serves pet3d.js; else none.
  async attachPets() {
    const root = document.documentElement;
    let HP = null;
    try {
      HP = await loadPets();
      if (!HP) {
        root.dataset.pets = "none";
        return;
      }
      this.pets = new TilePets(HP, this.layout, this.tiles.map((tile) => ({ key: tile.key, box: tile.petBox, w: tile.petSize.w, h: tile.petSize.h, changes: this.colourChanges(tile) })));
      this.pets.resize(this.scale);
      root.dataset.pets = "on";
    } catch (e) {
      this.pets = null;
      root.dataset.pets = `error: ${e}`;
    }
  }

  // A tile's colour over the cut, for its pet: [{ at, state }] in time order; the first
  // (at null) is its colour from the start, each later one a change. N04: the named tiles
  // take their judgement's colour when the voice names them (lit), the others none; N15:
  // the judgement of each version from when that version is shown.
  colourChanges(tile) {
    const st = (v) => this.s.judgements[`${v}|${tile.key}`]?.state ?? null;
    if (this.name === "n04") {
      const v = this.versions.at(-1);
      const s = st(v);
      if (s === "pending") return [{ at: null, state: "pending" }];
      const lit = this.namedTimes().find(([k]) => k === tile.key)?.[1];
      return lit === undefined || (s !== "green" && s !== "red") ? [{ at: null, state: null }] : [{ at: null, state: null }, { at: lit, state: s }];
    }
    const n = this.versions.length;
    const T = this.spec.times;
    const startOf = (k) => (k === 0 ? null : k === n - 1 ? T.all_green_s : T.rounds_from_s + (k * (T.all_green_s - T.rounds_from_s)) / (n - 1));
    const out = [{ at: null, state: st(this.versions[0]) }];
    for (let k = 1; k < n; k += 1) {
      const s = st(this.versions[k]);
      if (s !== out.at(-1).state) out.push({ at: startOf(k), state: s });
    }
    return out;
  }

  // The tile shown large, chosen for the cut's message (layout.json story):
  //   N04 "衝突・振動・組み立てまで、全部を 1 度に見る (呼ばれた札が記録の色のまま灯る)":
  //     the last tile the voice names (times.crash_s, nvh_s, assembly_s in time order) that
  //     has a record to draw. The message is that everything is looked at at once, not
  //     that something is red, so the colour does not choose; the voice ends its list on
  //     it ("…組み立てまで"), and the large tile is the one being named when the grid
  //     gives way. Without one, the first tile with a record in the layout's order;
  //   N15 "全部の分野が緑になるまで": the tile that turned green last, the one the rounds
  //     were for (the first version from which it stays green to the last is the latest);
  //   else the first tile. A tile with a record to draw for the last version shown comes
  //   before one without (a large empty tile says nothing).
  featuredKey() {
    const keys = this.spec.tests;
    const v = this.versions.at(-1);
    const state = (x, k) => this.s.judgements[`${x}|${k}`]?.state ?? null;
    const drawn = (k) => {
      const def = TILES[k];
      if (!def) return false;
      return def.assembly ? !!assemblyFor(this.s, v) : !!recordFor(this.s, k, def, v);
    };
    const first = (list) => list.find(drawn) ?? list[0];
    if (this.name === "n04") {
      const named = this.namedTimes().filter(([k]) => keys.includes(k)).map(([k]) => k);
      return [...named].reverse().find(drawn) ?? first(keys);
    }
    const vs = this.versions;
    // the first version from which each green tile stays green, the latest first
    const greens = keys
      .filter((k) => state(vs.at(-1), k) === "green")
      .map((k) => {
        let from = vs.length - 1;
        while (from > 0 && state(vs[from - 1], k) === "green") from -= 1;
        return { k, from };
      })
      .sort((a, b) => b.from - a.from);
    const latest = greens.filter((g) => g.from === greens[0]?.from).map((g) => g.k);
    return first(latest) ?? first(greens.map((g) => g.k)) ?? keys[0];
  }

  // N04: [tile, time] of the tiles the voice names, in time order.
  namedTimes() {
    const T = this.spec.times;
    return NAMED.map(([k, key]) => [k, T[key]]).filter(([, at]) => typeof at === "number").sort((a, b) => a[1] - b[1]);
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
    Object.assign(this.header.style, { left: `${H.x}px`, top: `${H.y}px`, width: `${H.w}px`, height: `${H.h}px`, fontSize: `${Math.max(H.size_px, this.story.bodyPx)}px`, lineHeight: `${H.h}px` });
    this.stage.append(this.header);

    const slots = listRects(sp.list);
    if (sp.tests.length - 1 > slots.length) throw new Error(`layout.json の ${this.name}.tests の一覧は ${sp.tests.length - 1} 枚、list は ${slots.length} 枠`);
    // one representative tile in the feature box, the others in the list's slots, in order
    this.featured = this.featuredKey();
    let j = 0;
    const rects = sp.tests.map((key) => (key === this.featured ? sp.feature : slots[j++]));
    this.tiles = sp.tests.map((key, i) => {
      const r = rects[i];
      const def = TILES[key];
      const box = `${this.name}.tile.${key}`;
      const el = document.createElement("div");
      el.className = "tile grid-tile";
      el.dataset.box = box;
      el.dataset.domain = key;
      if (key === this.featured) el.dataset.featured = "1";
      Object.assign(el.style, {
        left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`,
        background: this.colors.panel, border: `${st.border_px}px solid ${this.colors.rule}`, borderRadius: `${st.radius_px}px`,
      });
      if (!clearOfBand(r, this.layout)) el.dataset.outside = "1";
      // the pet's box: tile.pet at the bottom right, tile.pet.feature's size in the large tile
      const petSize = key === this.featured ? { w: st.pet.feature.w, h: st.pet.feature.h } : { w: st.pet.w, h: st.pet.h };
      el.innerHTML = `<div class="pet" data-box="${box}.pet" style="right:${st.pet.right}px;bottom:${st.pet.bottom}px;width:${petSize.w}px;height:${petSize.h}px"></div>`;
      this.stage.append(el);
      const tile = { key, def, el, rect: r, inner: { w: r.w - 2 * st.border_px, h: r.h - 2 * st.border_px }, petBox: el.querySelector(".pet"), petSize };
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
      // the assembly fast-forward (the drawing of A7 and N16): every step of the version's
      // newest assembly record with steps (the box is lit in that version's colour), else
      // of the run's newest
      const own = assemblyFor(this.s, this.versions.at(-1));
      const withSteps = own?.anas.filter((a) => Array.isArray(a.assembly) && a.assembly.length).at(-1);
      const rec = withSteps && this.s.designs[withSteps.design_version]
        ? { ana: withSteps, steps: [...withSteps.assembly].sort((x, y) => x.order - y.order), interferences: own.hits, design: this.s.designs[withSteps.design_version] }
        : assemblyRecord(this.s);
      this.asmBox.dataset.version = rec ? String(rec.ana.design_version) : "";
      if (rec) {
        const canvas = document.createElement("canvas");
        Object.assign(canvas.style, { position: "absolute", left: "0", top: "0", width: "100%", height: "100%" });
        this.asmBox.append(canvas);
        const view = new Workbench3D(null, "zu", { renderer: this.renderer });
        const stage = new AssemblyStage(view);
        stage.setRecord(rec);
        view.resize(V.w, V.h);
        stage.fit(1.04, VIEWS.default);
        this.asm = { canvas, view, stage, n: rec.steps.length, w: V.w, h: V.h };
      }
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
      el.insertAdjacentHTML("beforeend", `<span class="none" style="font-size:${this.textPx}px">この札の中身はまだ決めていない (${esc(tile.key)})</span>`);
    } else {
      el.insertAdjacentHTML("afterbegin", `<canvas></canvas>`);
      layer.canvas = el.querySelector("canvas");
      if (def.assembly) this.makeAssembly(tile, layer);
      else {
        layer.rec = recordFor(this.s, tile.key, def, v);
        if (layer.rec) this.makeView(tile, layer);
        else el.insertAdjacentHTML("beforeend", `<span class="none" style="font-size:${this.textPx}px">版 ${v} の記録が無い (${esc(def.test ?? tile.key)})</span>`);
      }
    }
    el.style.display = "none";
    return layer;
  }

  // The domain's judgement of the version: green ✓, red ✕, none when it has not been judged.
  markHtml(box, state) {
    const sign = state === "green" ? "✓" : state === "red" ? "✕" : null;
    if (!sign) return "";
    const M = this.spec.tile.mark;
    const px = Math.max(M.size_px, this.story.bodyPx);
    return `<span class="mark ${state === "green" ? "ok" : "miss"}" data-box="${box}.mark" data-state="${state}" data-kind="label" style="right:${M.right}px;top:${M.top}px;font-size:${px}px">${sign}</span>`;
  }

  // The top of the space under the tile's top line (its mark).
  belowHead() {
    const st = this.spec.tile;
    return st.mark.top + Math.max(st.mark.size_px, this.story.bodyPx) + st.label.y / 2;
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

  // The test's record in 3D over the whole tile: its motion, its field as a contour.
  makeView(tile, layer) {
    const { lc, ana } = layer.rec;
    const def = tile.def;
    const v = this.newView(tile, layer, this.s.designs[ana.design_version] ?? this.s.designs[layer.v], 0, tile.inner.w);
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
    layer.span = motion ? [motion.t0, motion.t1] : null;
    // moving: a motion, or a mode shape swinging with the stage clock
    if (motion || field?.kind === "mode_shape") layer.canvas.dataset.kind = "moving";
  }

  // Assembly: the design of the version with what interferes boxed in red (the mark says
  // whether the domain is judged green or red; no words).
  makeAssembly(tile, layer) {
    const asm = assemblyFor(this.s, layer.v);
    const el = layer.el;
    if (!asm) {
      el.insertAdjacentHTML("beforeend", `<span class="none" style="font-size:${this.textPx}px">版 ${layer.v} の組み立ての記録が無い</span>`);
      return;
    }
    const design = this.s.designs[layer.v];
    const below = this.belowHead();
    el.dataset.hits = String(asm.hits.length);
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

  resize(scale) {
    this.scale = scale;
    this.pets?.resize(scale);
    const areas = this.tiles.flatMap((t) => t.layers.map((l) => l.area).filter(Boolean));
    const w = Math.max(1, this.asm?.w ?? 1, ...areas.map((a) => a.w));
    const h = Math.max(1, this.asm?.h ?? 1, ...areas.map((a) => a.h));
    if (this.asm) {
      this.asm.canvas.width = Math.round(this.asm.w * scale);
      this.asm.canvas.height = Math.round(this.asm.h * scale);
    }
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
    if (this.asmBox) {
      this.asmBox.style.visibility = gridShown ? "hidden" : "visible";
      // the voice names 組み立て as the grid gives way: the box that takes the assembly
      // tile's place is lit in the assembly's judgement colour of the version (none without one)
      const st = this.s.judgements[`${this.versions.at(-1)}|assembly`]?.state ?? null;
      const tone = st === "green" ? GREEN : st === "red" ? sp.overlay.color : null;
      this.asmBox.dataset.lit = tone ? st : "";
      this.asmBox.style.borderColor = tone ?? this.colors.rule;
      this.asmBox.style.boxShadow = tone ? `0 0 0 ${sp.tile.border_px * 2}px ${tone}` : "none";
    }
    if (this.asm && !gridShown) {
      // the whole order from assembly_s to the end of the cut
      const a = this.asm;
      a.stage.pose((a.n * (t - T.assembly_s)) / Math.max(1e-9, sp.duration_s - T.assembly_s));
      a.stage.lineResolution(a.w, a.h);
      a.view.renderInto(a.canvas.getContext("2d"), a.canvas.width, a.canvas.height, this.scale);
    }
    this.header.style.visibility = gridShown ? "visible" : "hidden";
    const k = this.versionAt(t);
    const v = this.versions[k];
    document.documentElement.dataset.version = String(v);
    this.setHeader(v);
    const end = this.name === "n04" ? T.assembly_s : sp.duration_s;
    if (gridShown) this.pets?.draw(t);
    for (const tile of this.tiles) {
      tile.el.style.visibility = gridShown ? "visible" : "hidden";
      tile.layers.forEach((l, i) => (l.el.style.display = i === k ? "" : "none"));
      const layer = tile.layers[k];
      // N04: a tile the voice names is lit from that time in its judgement's colour in the
      // record (green or red as it is; nothing is turned red), not lit without a judgement
      const litAt = this.name === "n04" ? this.namedTimes().find(([k]) => k === tile.key)?.[1] : undefined;
      const tone = litAt === undefined ? null : layer.state === "green" ? GREEN : layer.state === "red" ? sp.overlay.color : null;
      const hot = tone !== null && t >= litAt;
      tile.el.dataset.hot = hot ? "1" : "";
      tile.el.dataset.lit = hot ? layer.state : "";
      tile.el.style.borderColor = hot ? tone : this.colors.rule;
      tile.el.style.boxShadow = hot ? `0 0 0 ${sp.tile.border_px * 2}px ${tone}` : "none";
      if (!gridShown || !layer.view) continue;
      // the record plays from lead_s to the grid's end, squeezed to fit
      const tau = layer.span ? squeeze(t, T.lead_s, end, layer.span[0], layer.span[1]).tau : 0;
      if (layer.motion) layer.view.setFrame(layer.motion.frame(tau));
      layer.view.animate(t, false); // a mode shape swings with the stage clock
      layer.view.renderInto(layer.canvas.getContext("2d"), layer.canvas.width, layer.canvas.height, this.scale);
    }
  }

  // One line, one number: N04 the tiles of the version still red, N15 the rounds to it.
  setHeader(v) {
    if (this.headerFor === v) return;
    this.headerFor = v;
    const num = (n) => `<span class="num" data-kind="number">${esc(String(n))}</span>`;
    if (this.name === "n15") this.header.innerHTML = `繰り返し ${num(this.roundsTo(v))} 回`;
    else {
      const reds = this.tiles.filter((x) => this.s.judgements[`${v}|${x.key}`]?.state === "red").length;
      this.header.innerHTML = reds ? `まだ赤が ${num(reds)} 枚` : "全部が緑";
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

// N04 and N15: the grid of eight tiles (seven domains and assembly, as the app's A6)
// of a run (cut.html?cut=n04|n15&run=<id>[&version=<n>]). Each tile draws its test's
// record of a design version and nothing else in words or figures (the story: a picture
// and a tick or cross); its corner mark is the domain's judgement of that version
// (judgement.updated: green ✓, red ✕, else none). The header is one line with one number:
// N04 the version (and the rounds to it, no count of red tiles), N15 how many rounds it
// took. Positions, sizes, times and the order of the tiles come only from layout.json
// (n04 / n15); a missing key is named on the page.
//
// N04: one version (?version=, else the run's last), a version on the way. The tiles the
// voice names (crash, NVH, assembly) are lit from times.crash_s / nvh_s / assembly_s in
// the colour of their judgement in the record (green or red; not lit without one), and the
// large tile is the one being named (featuredAt); the ticks and crosses are pale until
// their tile is lit. From times.assembly_s the grid gives way to the assembly's box
// (n04.assembly), where the version's assembly record's steps go in one after another to the
// end of the cut (assembly.js).
// N15: the run's versions in order (up to ?version=): the rounds share the time from
// times.rounds_from_s to times.all_green_s, and in a round's slot the tiles take the new
// version one by one in the order the record judged them (switchTimes); from all_green_s
// every tile is on the last version, to the end. The header counts the round in progress.
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
  n04: ["times.crash_s", "times.nvh_s", "times.assembly_s", "colors.bg", "colors.panel", "colors.rule", "overlay.color",
        "assembly.x", "assembly.y", "assembly.w", "assembly.h"],
  n15: ["times.rounds_from_s", "times.all_green_s"],
};
const COLOR_KEYS = ["bg", "panel", "rule"];
const TOP_KEYS = ["min_text_px", "stage.w", "stage.h"];

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

// The colours of a tile's field, so they say what its tick or cross says (the review of
// the second stills: a green tile with red members). The bands' top is the tile's load
// case's allowable value (its criterion's upper limit) when the field is in the limit's
// unit, even when the criterion's quantity is another (a hot-spot criterion over an
// element stress): then only what is past the allowable is red. A criterion on one part
// (T1: temp_battery_max) puts the top at that limit too. When the units differ (a crash
// judged on a distance, NVH on a frequency, over a stress or a mode shape) the field has
// no allowable to be read against: its values are not coloured (the drawing keeps its
// shape and motion, e.g. the mode swinging), so no red comes from a scale of its own.
export function tileContour(ana, field, criterion) {
  const ct = contourOf(ana, field, criterion);
  if (!ct) return null;
  const lim = criterion?.limit;
  const upper = lim && lim.unit === field.unit && Number.isFinite(lim.value) && (criterion.op === "<=" || criterion.op === "<");
  if (upper) {
    // a criterion on one part judges that part only: the other parts (their own limits are
    // not this one) are left uncoloured
    const parts = ct.partLimit ? { [ct.partLimit.part]: ct.parts[ct.partLimit.part] } : ct.parts;
    const min = field.kind === "temperature" ? ct.min : Math.min(ct.min, 0);
    return { ...ct, parts, max: lim.value, min, limitKnown: true, crit: criterion, judgedScale: true, key: `${ct.key}|limit ${lim.value}` };
  }
  return { ...ct, values: {}, nodes: {}, parts: {}, key: `${ct.key}|shape`, judgedScale: false };
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
    // N15: the tile takes versions[k] at its time in round k's slot (switchTimes)
    const at = tile.switchAt ?? this.switchTimes(tile.key);
    const out = [{ at: null, state: st(this.versions[0]) }];
    for (let k = 1; k < this.versions.length; k += 1) {
      const s = st(this.versions[k]);
      if (s !== out.at(-1).state) out.push({ at: at[k], state: s });
    }
    return out;
  }

  // The tile shown large at stage time t, chosen for the cut's message (layout.json story):
  //   N04 "衝突・振動・組み立てまで、全部を 1 度に見る (呼ばれた札が記録の色のまま灯る)":
  //     the tile the voice is naming now: crash, NVH, assembly in the order of their times
  //     (times.crash_s, nvh_s, assembly_s), each from its time; before the first, the
  //     first. A named tile without a record to draw is passed over. So the large tile is
  //     what the viewer hears, not one still to come (the review of the first cuts: the
  //     assembly's red ✕ large from the start read as "it failed"). Without a named tile
  //     with a record, the first tile with one in the layout's order;
  //   N15 "全部の分野が緑になるまで": the tile that turned green last, the one the rounds
  //     were for (the first version from which it stays green to the last is the latest);
  //   else the first tile. A tile with a record to draw for the last version shown comes
  //   before one without (a large empty tile says nothing).
  featuredAt(t) {
    if (this.name !== "n04") return this.featuredKey();
    const keys = this.spec.tests;
    const named = this.namedTimes().filter(([k]) => keys.includes(k) && this.drawn(k));
    if (!named.length) return this.featuredKey();
    return (named.filter(([, at]) => at <= t).at(-1) ?? named[0])[0];
  }

  // Whether a tile has a record to draw for the last version shown.
  drawn(k) {
    const def = TILES[k];
    if (!def) return false;
    const v = this.versions.at(-1);
    return def.assembly ? !!assemblyFor(this.s, v) : !!recordFor(this.s, k, def, v);
  }

  featuredKey() {
    const keys = this.spec.tests;
    const state = (x, k) => this.s.judgements[`${x}|${k}`]?.state ?? null;
    const drawn = (k) => this.drawn(k);
    const first = (list) => list.find(drawn) ?? list[0];
    if (this.name === "n04") return first(keys);
    const vs = this.versions;
    // the first version from which each green tile stays green, the latest first; within a
    // version, the tile that turns green latest in its round (switchTimes, the record's order)
    const greens = keys
      .filter((k) => state(vs.at(-1), k) === "green")
      .map((k) => {
        let from = vs.length - 1;
        while (from > 0 && state(vs[from - 1], k) === "green") from -= 1;
        return { k, from, at: this.switchTimes(k)[from] };
      })
      .sort((a, b) => b.from - a.from || b.at - a.at);
    return first(greens.map((g) => g.k)) ?? keys[0];
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

  // N15: the rounds share the time from rounds_from_s to all_green_s, one slot each
  // (round k brings versions[k]: slot k of n - 1). The round in progress at stage time t
  // (an index into this.versions): 0 before the first slot, k from the start of slot k.
  slotOf(k) {
    const T = this.spec.times;
    const n = this.versions.length;
    const span = (T.all_green_s - T.rounds_from_s) / Math.max(1, n - 1);
    return [T.rounds_from_s + (k - 1) * span, T.rounds_from_s + k * span];
  }

  versionAt(t) {
    const n = this.versions.length;
    if (this.name !== "n15" || n === 1) return n - 1;
    let k = 0;
    for (let i = 1; i < n; i += 1) if (t >= this.slotOf(i)[0]) k = i;
    return k;
  }

  // N15: when a tile takes each version (an array by version index; 0 for the first). In
  // round k's slot the tiles change one by one in the order the record judged them: a
  // tile's time in the slot is as far along as its domain's judgement of versions[k] is
  // between the earliest and the latest judgement of that version (the record's event
  // times), so the slot ends with every tile on versions[k].
  switchTimes(key) {
    const out = [0];
    for (let k = 1; k < this.versions.length; k += 1) {
      const v = this.versions[k];
      const ts = this.spec.tests.map((x) => this.s.judgements[`${v}|${x}`]?.t).filter(Number.isFinite);
      const lo = Math.min(...ts);
      const hi = Math.max(...ts);
      const own = this.s.judgements[`${v}|${key}`]?.t;
      const f = Number.isFinite(own) && hi > lo ? (own - lo) / (hi - lo) : 1;
      const [a, b] = this.slotOf(k);
      out.push(a + f * (b - a));
    }
    return out;
  }

  // The version index a tile shows at stage time t.
  tileVersionAt(tile, t) {
    if (this.name !== "n15") return this.versions.length - 1;
    let k = 0;
    tile.switchAt.forEach((at, i) => {
      if (i > 0 && t >= at) k = i;
    });
    return k;
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
    this.featured = this.featuredAt(0);
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
      const tile = { key, def, el, rect: r, inner: { w: r.w - 2 * st.border_px, h: r.h - 2 * st.border_px }, petBox: el.querySelector(".pet"), petSize, switchAt: this.switchTimes(key) };
      // one layer per version: its label, mark and drawing
      tile.layers = this.versions.map((v) => this.makeLayer(tile, v));
      return tile;
    });

    if (this.name === "n04") {
      // the assembly fast-forward's own box (n04.assembly: the grid's place)
      const V = sp.assembly;
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
        stage.ngChecks = ["no_interference"]; // red only for a part that does not fit (as N16)
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
    // what is missing is said as a note for the makers (data-kind "note": not body text)
    if (!def) {
      el.insertAdjacentHTML("beforeend", `<span class="none" data-kind="note" style="font-size:${this.textPx}px">この札の中身はまだ決めていない (${esc(tile.key)})</span>`);
    } else {
      el.insertAdjacentHTML("afterbegin", `<canvas></canvas>`);
      layer.canvas = el.querySelector("canvas");
      if (def.assembly) this.makeAssembly(tile, layer);
      else {
        layer.rec = recordFor(this.s, tile.key, def, v);
        if (layer.rec) this.makeView(tile, layer);
        else el.insertAdjacentHTML("beforeend", `<span class="none" data-kind="note" style="font-size:${this.textPx}px">版 ${v} の記録が無い (${esc(def.test ?? tile.key)})</span>`);
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

  // A 3D view of the design through the shared renderer in a part of the tile: areaOf(tile)
  // gives that part (from the tile's inner size, so it follows the tile when the tile is
  // placed again in the other box, placeTiles).
  newView(tile, layer, design, areaOf) {
    const v = new Workbench3D(null, params.get("tilelook") === "real" ? "real" : "zu", { renderer: this.renderer });
    v.setDesign(design);
    layer.areaOf = areaOf;
    this.placeArea(tile, layer, v);
    return v;
  }

  // The layer's canvas in its part of the tile, the view sized to it and (once fitted) its
  // camera fitted again to the same points.
  placeArea(tile, layer, v = layer.view) {
    const a = layer.areaOf(tile);
    layer.area = a;
    Object.assign(layer.canvas.style, { left: `${a.x}px`, top: `${a.y}px`, width: `${a.w}px`, height: `${a.h}px` });
    v.resize(a.w, a.h);
    if (layer.fit) v.fitPoints(layer.fit.pts, layer.fit.view, layer.fit.margin);
    if (this.scale) {
      layer.canvas.width = Math.round(a.w * this.scale);
      layer.canvas.height = Math.round(a.h * this.scale);
    }
  }

  // The tiles in their boxes with `featured` in the large one (the others in the list's
  // slots, in the layout's order): each tile's box, its pet's box, and its views.
  placeTiles(featured) {
    const sp = this.spec;
    const st = sp.tile;
    const slots = listRects(sp.list);
    this.featured = featured;
    let j = 0;
    for (const tile of this.tiles) {
      const r = tile.key === featured ? sp.feature : slots[j++];
      tile.rect = r;
      tile.inner = { w: r.w - 2 * st.border_px, h: r.h - 2 * st.border_px };
      Object.assign(tile.el.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
      if (tile.key === featured) tile.el.dataset.featured = "1";
      else delete tile.el.dataset.featured;
      if (!clearOfBand(r, this.layout)) tile.el.dataset.outside = "1";
      else delete tile.el.dataset.outside;
      tile.petSize = tile.key === featured ? { w: st.pet.feature.w, h: st.pet.feature.h } : { w: st.pet.w, h: st.pet.h };
      Object.assign(tile.petBox.style, { width: `${tile.petSize.w}px`, height: `${tile.petSize.h}px` });
      this.pets?.setBox(tile.key, tile.petSize.w, tile.petSize.h);
      for (const l of tile.layers ?? []) if (l.view && l.areaOf) this.placeArea(tile, l);
    }
  }

  // The test's record in 3D over the whole tile: its motion, its field as a contour.
  makeView(tile, layer) {
    const { lc, ana } = layer.rec;
    const def = tile.def;
    const v = this.newView(tile, layer, this.s.designs[ana.design_version] ?? this.s.designs[layer.v], (t) => ({ x: 0, y: 0, w: t.inner.w, h: t.inner.h }));
    v.setLoading(lc.params?.loading ?? null);
    const motion = motionOf(ana);
    v.setMotion(motion);
    v.setScene(lc, ana);
    const field = def.field ? fieldFor(ana, def.field) : null;
    const ct = field ? tileContour(ana, field, lc.criterion) : null;
    layer.el.dataset.scale = ct ? (ct.judgedScale ? "allowable" : "shape") : "none";
    v.setContour(ct);
    v.setVectors(null);
    v.setToggles({ parts: true, contour: true, arrows: false, cg: true, names: false, limit: false });
    // the camera takes in the vehicle and what the scene puts beside it (a wall's face)
    const pts = v.contentPoints();
    const d = v.sceneDesc;
    if (d?.wall) pts.push(new THREE.Vector3(d.wall.x + 300, 0, 1200));
    for (const o of d?.objects ?? []) if (o.kind === "wall") pts.push(new THREE.Vector3(o.position_mm[0] - o.size_mm[0] / 2, o.position_mm[1], o.position_mm[2]));
    layer.fit = { pts, view: VIEWS[v.view ?? "default"], margin: 1.06 };
    v.fitPoints(pts, layer.fit.view, layer.fit.margin);
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
      el.insertAdjacentHTML("beforeend", `<span class="none" data-kind="note" style="font-size:${this.textPx}px">版 ${layer.v} の組み立ての記録が無い</span>`);
      return;
    }
    const design = this.s.designs[layer.v];
    const below = this.belowHead();
    el.dataset.hits = String(asm.hits.length);
    const v = this.newView(tile, layer, design, (t) => ({ x: 0, y: below, w: t.inner.w, h: t.inner.h - below }));
    v.setScene(null, null);
    v.setContour(null);
    v.setToggles({ parts: true, contour: false, arrows: false, cg: false, names: false, limit: false });
    layer.fit = { pts: v.contentPoints(), view: VIEWS.default, margin: 1.1 };
    v.fitPoints(layer.fit.pts, layer.fit.view, layer.fit.margin);
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
    // N04: the large tile follows the voice
    const featured = this.featuredAt(t);
    if (featured !== this.featured) this.placeTiles(featured);
    if (gridShown) this.pets?.draw(t);
    for (const tile of this.tiles) {
      tile.el.style.visibility = gridShown ? "visible" : "hidden";
      // N15: each tile takes the next version at its own time in the round (switchTimes)
      const kt = this.tileVersionAt(tile, t);
      tile.layers.forEach((l, i) => (l.el.style.display = i === kt ? "" : "none"));
      tile.el.dataset.version = String(this.versions[kt]);
      const layer = tile.layers[kt];
      // N04: a tile the voice names is lit from that time in its judgement's colour in the
      // record (green or red as it is; nothing is turned red), not lit without a judgement
      const litAt = this.name === "n04" ? this.namedTimes().find(([k]) => k === tile.key)?.[1] : undefined;
      const tone = litAt === undefined ? null : layer.state === "green" ? GREEN : layer.state === "red" ? sp.overlay.color : null;
      const hot = tone !== null && t >= litAt;
      tile.el.dataset.hot = hot ? "1" : "";
      tile.el.dataset.lit = hot ? layer.state : "";
      tile.el.style.borderColor = hot ? tone : this.colors.rule;
      tile.el.style.boxShadow = hot ? `0 0 0 ${sp.tile.border_px * 2}px ${tone}` : "none";
      // N04: a tick or cross is pale (gray) until its tile is named and lit, so lighting up
      // is what shows the record's colour; a tile the voice does not name stays pale
      const mark = layer.el.querySelector(".mark");
      if (mark) mark.classList.toggle("dim", this.name === "n04" && !hot);
      if (!gridShown || !layer.view) continue;
      // the record plays from lead_s to the grid's end, squeezed to fit
      const tau = layer.span ? squeeze(t, T.lead_s, end, layer.span[0], layer.span[1]).tau : 0;
      if (layer.motion) layer.view.setFrame(layer.motion.frame(tau));
      layer.view.animate(t, false); // a mode shape swings with the stage clock
      layer.view.renderInto(layer.canvas.getContext("2d"), layer.canvas.width, layer.canvas.height, this.scale);
    }
  }

  // One line, one number: N15 the rounds to the version; N04 the version and the rounds to
  // it (no count of red tiles: the voice says what can be checked, not what failed).
  setHeader(v) {
    if (this.headerFor === v) return;
    this.headerFor = v;
    const num = (n) => `<span class="num" data-kind="number">${esc(String(n))}</span>`;
    if (this.name === "n15") this.header.innerHTML = `繰り返し ${num(this.roundsTo(v))} 回`;
    else this.header.innerHTML = `設計の版 ${num(v)}・直し ${esc(String(this.roundsTo(v)))} 回`;
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

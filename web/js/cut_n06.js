// N06 (cut.html?cut=n06&run=<id>): designs that do not work, side by side.
//
// One tile per key of layout.json n06.tests. A test tile plays the
// record of its test's load case past the limit (its id ends in -beyond; the newest version
// whose record passes the limit, see casesOf), from the time
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
// One at a time, large (the story: one representative large, the rest a small list): the
// tiles come in one after another as a small list (layout.json n06.list, row by row), and
// each takes its turn in the large box (n06.feature), in the layout's order
// — the message is "作ってみると、うまくいかない", every failure in turn, so none is the
// representative of the others. The cut from times.tiles_s to its end is shared equally by
// the turns; in its turn the record plays in the large box (from tile_in_s after the turn
// starts to its end), then holds its end there until the next turn. In the list a tile holds
// the record's first pose until its turn, plays along with the large box in its turn (so it
// does not jump from the first pose to the last when the turn ends), then holds its last pose;
// its tag is hidden while the large box shows it. The record is paced (pace.js) so nothing
// turns faster than 90 deg/s on the screen (a tip-over plays slower, the rest faster). So
// three things move at most: the large picture, its tile in the list and a tile coming in.
//
// The step tile driven at the step (layout.json n06.runup, optional): when the run holds
// step_climb_dynamic records of run 2 (rolling at the load case's speed from before the step:
// the record says record_run = speed in its assumptions, or the load case gives it in its
// params) for the tile's tests, one that does not climb (its result climbed 0) and one that
// does (climbed 1), in the same design version, the tile runup.tile is split into two panes
// stacked in runup.order ("stops", "climbs"; runup.gap_px apart), in the list and in the large
// box. Each pane plays its own record; both on one clock (the two runs start the same distance
// before their steps at the same speed, so they meet them together), under one camera from the
// side that holds still over both runs. The stop is the highest step that is not climbed, the
// climb the lowest that is (the newest version with both; the newest record of each case). The
// pace: the stretch both records have (from the start to the shorter record's end: the
// approach, the hit and the climb) gets runup.lead_share of the play, the rest (the stopped
// run's tries after it and its standstill to the record's end) what is left, each stretch at an
// even rate, and no step faster than pace.js's turn limit (pacer's weight; the limit comes
// first, so the stopped run's rebounds may take some of the lead's share). Each pane's tag is
// read from its record: the height from the scene's step (else the load case's step_height),
// the speed from the load case's params: "✕ 段差 330 mm で止まる (10 km/h で向かう)",
// "○ 段差 100 mm は越える" in the large box, the height only in the list's small panes ("✕ 330 mm
// で止まる", one line). Red only for a judgement that fails (answerOf: the newest verdict on
// the load case, else its result against the criterion): the ✕ of a stop judged NG is in the
// overlay's red, a stop not judged in ink, the climb in ink; the pair's border while it is in
// the large box is in ink, and no lifted-wheel ring is drawn (on a step the wheels leave the
// road to climb). Without such records the tile is as above.
//
// Positions, sizes, times and colours come only from layout.json n06 (N06_KEYS, RUNUP_KEYS).

import * as THREE from "three";
import { clearOfBand, cubicBezier, squeeze } from "./compose.js";
import { fieldsOf, overOf } from "./fields.js";
import { LENGTH, motionOf } from "./series.js";
import { AssemblyStage } from "./assembly.js";
import { VIEWS, cutView } from "./workbench3d.js";
import { FEATURE_KEYS, answerOf, listRects, requireKeys, storyOf } from "./cut_n03.js";
import { tileContour } from "./cut_grid.js";
import { PACE_LIMIT_DEG_S, pacer } from "./pace.js";
import { fmt, fmtQ } from "./labels.js";

const $ = (id) => document.getElementById(id);

export const N06_KEYS = [
  "duration_s",
  ...FEATURE_KEYS,
  "tests",
  "tile.border_px", "tile.radius_px", "tile.label.x", "tile.label.y", "tile.label.size_px",
  "times.tiles_s", "times.tile_in_s", "times.tile_stagger_s",
  "colors.bg", "colors.panel", "colors.rule",
  "overlay.color", "overlay.opacity",
];

// layout.json n06.runup's keys, when it is there (the pair of runs at the step, see the top).
export const RUNUP_KEYS = ["runup.tile", "runup.order", "runup.gap_px", "runup.lead_share"];
const RUNUP_ORDER = ["stops", "climbs"];

// A record of run 2 of the step test (driving at the step at the load case's speed).
const RUN2 = /record_run = speed/;
const secondRun = (a, lc) => lc?.params?.record_run === "speed" || (a?.assumptions ?? []).some((x) => RUN2.test(String(x)));

// Not in layout.json (written here until the promo department gives keys for them):
// how a tile comes in, from this scale to 1 along this CSS cubic-bezier over times.tile_in_s.
export const N06_APPEAR = { from_scale: 0.92, ease: [0.2, 0.8, 0.3, 1] };

// What each tile shows (keys as in layout.json n06.tests): the tests whose cases past the
// limit it plays (see casesOf), its name, and what it reads:
//   field   the field drawn on the vehicle ("stress": on the allowable's band, as N01;
//           "temperature": on its own range, a part over its limit in red; null: none)
//   from    the records its words come from ("motion", "still", else both)
//   motion  the test whose case of the same name past the limit gives the motion
//           (R1-step250-beyond -> P1-step250-beyond: the frame's stress played on the climb)
//   assembly  the assembly's interferences instead of a test
const TILES = {
  step_break: { tests: ["R1", "P1"], motion: "P1", name: "段差とフレーム", field: "stress", from: "still" },
  tilt_over: { tests: ["S1", "S5"], name: "坂", field: null },
  step_stuck: { tests: ["P1"], name: "段差", field: null, from: "motion" },
  battery_hot: { tests: ["T1"], name: "電池の熱", field: "temperature" },
  parts_clash: { name: "組み立て", assembly: true },
  wind: { tests: ["F1"], name: "横風", field: null },
  // earlier keys (rollover was the sixth tile before wind)
  rollover: { tests: ["S3"], name: "曲がる", field: null },
  step: { tests: ["P1"], name: "段差", field: "stress" },
  frame: { tests: ["R1"], name: "段差の力とフレーム", field: "stress" },
  slope: { tests: ["S5"], name: "凸凹の横斜面", field: null },
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
  max_stress: "フレームが持たない", // past the allowable stress ("許容" is not a viewer's word)
  critical_wind_speed: "風に耐えられない",
  tip_slope_angle: "斜面で倒れる",
  tilt_angle: "傾いて倒れる",
  min_side_load: "片側の輪が浮く",
  temp_battery_max: "電池が熱くなりすぎる",
  climbable: "段差を越えられない",
};

const BEYOND = /-beyond$/;
const ok = (a) => a?.status === "ok";
const resultOf = (ana, q) => (ana?.results ?? []).find((r) => r.quantity === q) ?? null;
// "member rail_L_AS1" / "part:wheel_FL" -> the id in the design.
const bareId = (ref) => String(ref ?? "").replace(/^(member|part|node)[: ]\s*/, "");

// The step's height (mm) a record met: its scene's step (size_mm along z), else the load
// case's step_height; null without either.
function stepHeightOf(ana, lc) {
  const o = (ana?.scene ?? []).find((x) => x?.kind === "step" && Array.isArray(x.size_mm));
  if (o && Number.isFinite(o.size_mm[2])) return o.size_mm[2];
  const q = lc?.params?.step_height;
  return q && LENGTH[q.unit] && Number.isFinite(q.value) ? q.value * LENGTH[q.unit] : null;
}

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
    if (this.spec.runup !== undefined) {
      requireKeys(layout, "n06", RUNUP_KEYS);
      const bad = [].concat(this.spec.runup.order).filter((k) => !RUNUP_ORDER.includes(k));
      if (bad.length || !Array.isArray(this.spec.runup.order) || !this.spec.runup.order.length) {
        throw new Error(`layout.json の n06 の runup.order が分からない: ${bad.join(", ") || JSON.stringify(this.spec.runup.order)} (${RUNUP_ORDER.join(" / ")})`);
      }
    }
    this.layout = layout;
    this.story = storyOf(layout, "n06");
    this.s = s;
    this.stage = $("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.build();
  }

  // The records a tile plays: for one load case past the limit and one design version, the
  // newest ok analyses with a motion (from the def.motion test's case of the same name when
  // it has one), with the tile's field (a still), and the newest of all.
  //
  // Which case and version: the cut says "作ってみると、うまくいかない", so a tile shows a
  // record in which the limit is passed. The candidates go by the tests' order, then the
  // cases' order in the project, then the versions from the newest; the first whose records
  // say what went wrong (outcome has words) is taken. A later version that was fixed (in the
  // video run, version 2 takes the frame under the allowable and climbs the lower step)
  // would otherwise hide the failure. Without any, the first candidate (its name is shown).
  casesOf(def) {
    const kind = def.field ?? "stress";
    const cases = this.s.project?.load_cases ?? [];
    const all = this.s.order.analyses.map((id) => this.s.analyses[id]).filter(ok);
    const found = [];
    for (const testId of def.tests) {
      for (const lc of cases.filter((c) => c.test_id === testId && BEYOND.test(c.load_case_id))) {
        const anas = all.filter((a) => a.load_case_id === lc.load_case_id);
        if (!anas.length) continue;
        const sibling = def.motion && def.motion !== testId ? cases.find((c) => c.load_case_id === `${def.motion}${lc.load_case_id.slice(String(lc.test_id).length)}`) : null;
        const moving = sibling ? all.filter((a) => a.load_case_id === sibling.load_case_id) : [];
        for (const v of [...new Set(anas.map((a) => a.design_version))].sort((a, b) => b - a)) {
          const mine = anas.filter((a) => a.design_version === v);
          const dynOwn = mine.filter((a) => !!motionOf(a)).at(-1) ?? null;
          const dynSib = moving.filter((a) => a.design_version === v && !!motionOf(a)).at(-1) ?? null;
          const still = mine.filter((a) => fieldsOf(a, { mesh: false }).some((f) => f.kind === kind)).at(-1) ?? null;
          const dyn = dynOwn ?? dynSib;
          found.push({ lc, dyn, still, any: mine.at(-1), version: v, sceneLc: dynOwn || !dynSib ? lc : sibling });
        }
      }
    }
    return found.find((c) => this.outcome(c, def).length) ?? found[0] ?? null;
  }

  // The pair of run-2 records the runup tile shows (see the top): { version, runs: [{ lc, ana,
  // climbs, h }] in runup.order }, or null when the run has no such pair.
  runupOf(def) {
    const tests = def?.tests ?? [];
    const cases = (this.s.project?.load_cases ?? []).filter((c) => tests.includes(c.test_id));
    const all = this.s.order.analyses.map((id) => this.s.analyses[id]).filter(ok);
    const runs = [];
    for (const lc of cases) {
      for (const a of all.filter((x) => x.load_case_id === lc.load_case_id)) {
        const climbed = resultOf(a, "climbed");
        const h = stepHeightOf(a, lc);
        if (!secondRun(a, lc) || !climbed || h === null || !motionOf(a)) continue;
        runs.push({ lc, ana: a, version: a.design_version, climbs: climbed.value >= 1, h });
      }
    }
    for (const v of [...new Set(runs.map((r) => r.version))].sort((a, b) => b - a)) {
      const newest = new Map();
      for (const r of runs.filter((x) => x.version === v)) newest.set(r.lc.load_case_id, r); // the later record of a case wins
      const list = [...newest.values()];
      const stops = list.filter((r) => !r.climbs).sort((a, b) => b.h - a.h)[0];
      const climbs = list.filter((r) => r.climbs).sort((a, b) => a.h - b.h)[0];
      if (stops && climbs) return { version: v, runs: this.spec.runup.order.map((k) => (k === "stops" ? stops : climbs)) };
    }
    return null;
  }

  // The runup tile's two panes in a box (the list's slot or the large box), stacked in order
  // runup.gap_px apart, each with its record's view and tag; one camera for both.
  makePair(box, pair, px, full) {
    const R = this.spec.runup;
    const n = pair.runs.length;
    const h = (box.inner.h - (n - 1) * R.gap_px) / n;
    box.canvas.remove();
    box.canvas = null;
    box.el.dataset.runup = "1";
    box.subs = pair.runs.map((r, i) => {
      const el = document.createElement("div");
      el.className = "n06-sub";
      el.dataset.box = `${box.el.dataset.box}.${r.climbs ? "climbs" : "stops"}`;
      Object.assign(el.style, { left: "0px", top: `${Math.round(i * (h + R.gap_px))}px`, width: `${box.inner.w}px`, height: `${Math.floor(h)}px` });
      el.innerHTML = "<canvas></canvas>";
      box.el.append(el);
      const sub = { el, rect: { w: box.inner.w, h: Math.floor(h) }, inner: { w: box.inner.w, h: Math.floor(h) }, canvas: el.querySelector("canvas"), run: r };
      const ng = !r.climbs && answerOf(this.s, { lc: r.lc, ana: r.ana }) === false;
      el.dataset.case = r.lc.load_case_id;
      el.dataset.version = String(r.ana.design_version);
      el.dataset.climbs = r.climbs ? "1" : "0";
      el.dataset.ng = ng ? "1" : "0";
      el.dataset.heightMm = String(r.h);
      sub.label = this.labelRun(sub, r, ng, Math.max(this.story.bodyPx, Math.round((px * h) / box.inner.h)), full);
      this.makeRunView(sub, r);
      return sub;
    });
    // one camera over both runs: each vehicle where it starts and where its record ends, and
    // the near top edge of each step
    const pts = [];
    for (const sub of box.subs) {
      const { view: v, motion: m } = sub;
      for (const t of [m.t0, m.t1]) {
        const b = m.frame(t).body;
        pts.push(...v.contentPoints().map((p) => p.add(new THREE.Vector3(b[0], b[1], b[2]))));
      }
      for (const o of v.sceneDesc?.objects ?? []) {
        if (o.kind !== "step") continue;
        const x0 = o.position_mm[0] - o.size_mm[0] / 2;
        for (const s of [-1, 1]) pts.push(new THREE.Vector3(x0, o.position_mm[1] + (s * o.size_mm[1]) / 4, o.position_mm[2] + o.size_mm[2] / 2));
      }
    }
    for (const sub of box.subs) {
      sub.view.fitPoints(pts, VIEWS.side, 1.06);
      sub.view.noFollow = true;
    }
  }

  makeRunView(sub, r) {
    const v = cutView(this, "n06");
    v.setDesign(this.s.designs[r.ana.design_version] ?? this.s.designs[this.s.currentVersion]);
    v.setLoading(r.lc.params?.loading ?? null);
    const motion = motionOf(r.ana);
    v.setMotion(motion);
    v.setScene(r.lc, r.ana);
    v.setContour(null);
    v.setVectors(null);
    v.setToggles({ parts: true, contour: false, arrows: false, cg: true, names: false, limit: false });
    v.resize(sub.inner.w, sub.inner.h);
    v.setFrame(motion.frame(motion.t0));
    sub.view = v;
    sub.motion = motion;
  }

  // A run's tag: ✕ / ○ and what the record says, with the step's height and the speed.
  labelRun(sub, r, ng, px, full) {
    const L = this.spec.tile.label;
    const ink = this.layout.colors?.ink ?? "#1b1a17";
    const color = ng ? this.spec.overlay.color : ink;
    const b = document.createElement("b");
    b.dataset.box = `${sub.el.dataset.box}.label`;
    b.dataset.kind = "label";
    Object.assign(b.style, { left: `${L.x}px`, top: `${L.y}px`, fontSize: `${px}px` });
    const mark = document.createElement("span");
    mark.className = "mark";
    mark.dataset.box = `${sub.el.dataset.box}.mark`;
    mark.style.color = color;
    mark.textContent = r.climbs ? "○" : "✕";
    const why = document.createElement("span");
    why.className = "why";
    why.style.color = color;
    // in full in the large box; in the list's small panes the height only (one line)
    const height = full ? `段差 ${fmt(Math.round(r.h))} mm` : `${fmt(Math.round(r.h))} mm`;
    const speed = full && r.lc.params?.speed ? fmtQ(r.lc.params.speed) : null;
    // a line breaks only between phrases
    const parts = r.climbs ? [`${height} は`, "越える"] : [`${height} で`, "止まる", ...(speed ? [` (${speed} で向かう)`] : [])];
    for (const part of parts) {
      const ph = document.createElement("span");
      ph.style.display = "inline-block";
      ph.style.whiteSpace = "pre";
      ph.textContent = part;
      why.append(ph);
    }
    b.append(mark, " ", why);
    sub.el.append(b);
    this.fitLabel(sub, b);
    return b;
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
    const T = sp.times;
    const n = sp.tests.length;
    // the large box and one small slot per tile
    const slots = listRects(sp.list);
    if (n > slots.length) throw new Error(`layout.json の n06.tests は ${n} 枚、list は ${slots.length} 枠`);
    const fr = { big: sp.feature, small: slots };
    this.big = fr.big;
    const turn = (sp.duration_s - T.tiles_s) / Math.max(1, n);
    const box = (cls, key, r) => {
      const el = document.createElement("div");
      el.className = cls;
      el.dataset.box = key;
      Object.assign(el.style, {
        left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`,
        background: sp.colors.panel, border: `${st.border_px}px solid ${sp.colors.rule}`, borderRadius: `${st.radius_px}px`,
      });
      if (!clearOfBand(r, this.layout)) el.dataset.outside = "1";
      el.innerHTML = `<canvas></canvas>`;
      this.stage.append(el);
      return { el, rect: r, inner: { w: r.w - 2 * st.border_px, h: r.h - 2 * st.border_px }, canvas: el.querySelector("canvas") };
    };
    this.tiles = sp.tests.map((key, i) => {
      const def = TILES[key];
      const small = box("n06-tile", `n06.tile.${key}`, fr.small[i]);
      const big = box("n06-big", `n06.big.${key}`, fr.big);
      const tile = { key, def, small, big, el: small.el, start: T.tiles_s + i * T.tile_stagger_s };
      tile.land = tile.start + T.tile_in_s;
      tile.turn = { from: T.tiles_s + i * turn, to: i === n - 1 ? sp.duration_s : T.tiles_s + (i + 1) * turn };
      tile.play = { from: tile.turn.from + T.tile_in_s, to: T.tiles_s + (i + 1) * turn };
      const L0 = st.label;
      const px0 = Math.max(L0.size_px, this.story.bodyPx);
      const pair = def && !def.assembly && sp.runup?.tile === key ? this.runupOf(def) : null;
      if (pair) {
        tile.pair = pair;
        tile.miss = true;
        small.el.dataset.miss = "1";
        this.makePair(small, pair, px0, false);
        this.makePair(big, pair, Math.round(px0 * (fr.big.h / slots[0].h)), true);
        small.label = null;
        big.label = null;
        return tile;
      }
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
      small.el.dataset.miss = tile.miss ? "1" : "0";
      // the large box's tag: as much larger as the box is taller than a list slot
      const L = st.label;
      const px = Math.max(L.size_px, this.story.bodyPx);
      small.label = this.labelTile(small, def ? def.name : key, words, px);
      big.label = this.labelTile(big, def ? def.name : key, words, Math.round(px * (fr.big.h / slots[0].h)));
      for (const pane of [small, big]) {
        if (!def) this.note(pane, `この札の中身はまだ決めていない (${key})`);
        else if (def.assembly) {
          if (clash) this.makeAssembly(pane, clash);
          else this.note(pane, "記録がまだ無い (組み立ての干渉)");
        } else if (!c) this.note(pane, `記録がまだ無い (${def.tests.join("・")} の限界の外)`);
        else this.makeView(pane, c, def);
      }
      return tile;
    });
  }

  // One tag at the top left: ✕ and what happened when the records say it, else the test's name.
  labelTile(tile, name, words, px) {
    const sp = this.spec;
    const L = sp.tile.label;
    const box = tile.el.dataset.box;
    const label = document.createElement("b");
    label.dataset.box = `${box}.label`;
    label.dataset.kind = "label";
    Object.assign(label.style, { left: `${L.x}px`, top: `${L.y}px`, fontSize: `${px}px` });
    if (words.length) {
      const mark = document.createElement("span");
      mark.className = "mark";
      mark.dataset.box = `${box}.mark`;
      mark.style.color = sp.overlay.color;
      mark.textContent = "✕";
      const why = document.createElement("span");
      why.className = "why";
      why.style.color = sp.overlay.color;
      // a line breaks only between phrases (after が, を and ・), never inside a word ("超え|る")
      for (const part of words.join("・").split(/(?<=[がを・])/)) {
        const ph = document.createElement("span");
        ph.style.display = "inline-block";
        ph.textContent = part;
        why.append(ph);
      }
      label.append(mark, " ", why);
    } else label.textContent = name;
    tile.el.append(label);
    this.fitLabel(tile, label);
    return label;
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
    // a line breaks only before the parenthesis, never inside it or inside a word
    for (const part of String(text).split(/(?= \()/)) {
      const ph = document.createElement("span");
      Object.assign(ph.style, { display: "inline-block", whiteSpace: "nowrap" });
      ph.textContent = part.trim();
      n.append(ph, " ");
    }
    tile.el.append(n);
  }

  makeView(tile, c, def) {
    const ana = c.dyn ?? c.still ?? c.any;
    const v = cutView(this, "n06");
    v.setDesign(this.s.designs[ana.design_version] ?? this.s.designs[this.s.currentVersion]);
    const sceneLc = c.sceneLc ?? c.lc;
    v.setLoading(sceneLc.params?.loading ?? null);
    const motion = c.dyn ? motionOf(c.dyn) : null;
    v.setMotion(motion);
    v.setScene(sceneLc, ana);
    // stress on the allowable's band (as N01); a temperature on its own range, the part the
    // load case limits held to it
    const field = def.field && c.still ? fieldsOf(c.still, { mesh: false }).find((f) => f.kind === def.field) : null;
    const crit = def.field === "stress" ? this.allowable(field?.unit) ?? c.lc.criterion : c.lc.criterion;
    // the bands' top is the allowable value (as the N04 tiles, tileContour), so only what is
    // past it is red, and that is also boxed in translucent red (overMarks) to be seen small
    const ct = field ? tileContour(c.still, field, crit) : null;
    v.setContour(ct);
    tile.el.dataset.scale = ct ? (ct.judgedScale ? "allowable" : "shape") : "none";
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
    tile.el.dataset.overMarks = String(this.overMarks(v, ct));
    tile.el.dataset.lift = motion?.liftT !== null && motion?.liftT !== undefined && v.liftIsLimit ? "1" : "0";
    tile.el.dataset.past = this.markPast(v, c, motion) ? "1" : "0";
    tile.el.dataset.case = c.lc.load_case_id;
    tile.el.dataset.version = String(c.version ?? ana.design_version);
  }

  // A translucent red box (the overlay's colour and opacity) round each member and part
  // whose recorded value is past the allowable (the contour's top): the place the ✕ is
  // about, seen even when small in the picture. Returns how many.
  overMarks(v, ct) {
    if (!ct?.limitKnown) return 0;
    const O = this.spec.overlay;
    const mat = new THREE.MeshBasicMaterial({ color: O.color, transparent: true, opacity: O.opacity * 2, depthWrite: false });
    const marks = new THREE.Group();
    for (const [id, x] of Object.entries(ct.values ?? {})) {
      const m = v.members[id];
      if (!m || !(x > ct.max)) continue;
      const k = Math.max(m.width, m.height) * 2.6;
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(m.length + k * 0.4, k, k), mat);
      mesh.matrixAutoUpdate = false;
      mesh.matrix.copy(m.mesh.matrix);
      mesh.renderOrder = 11;
      marks.add(mesh);
    }
    const parts = v.design?.parts ?? [];
    for (const [id, x] of Object.entries(ct.parts ?? {})) {
      const p = parts.find((q) => q.id === id);
      if (!p?.box || !overOf(ct, id, x)) continue;
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(...p.box.size_mm.map((s) => s * 1.15)), mat);
      mesh.position.set(...p.box.center_mm);
      mesh.renderOrder = 11;
      marks.add(mesh);
    }
    v.body.add(marks);
    return marks.children.length;
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
    const v = cutView(this, "n06");
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
    this.renderer.setSize(Math.ceil(this.big.w * scale), Math.ceil(this.big.h * scale), false);
    for (const t of this.tiles) {
      for (const pane of [t.small, t.big].flatMap((b) => b.subs ?? [b])) {
        pane.canvas.width = Math.round(pane.inner.w * scale);
        pane.canvas.height = Math.round(pane.inner.h * scale);
      }
    }
  }

  // The runup pair's panes at share u of the play: both records on one clock (see the top).
  posePair(box, u) {
    const subs = box.subs;
    if (box.playS && !box.pace) {
      const lead = Math.min(...subs.map((s) => s.motion.duration));
      const span = Math.max(...subs.map((s) => s.motion.duration));
      const f = this.spec.runup.lead_share;
      const weight = span - lead > 1e-6 && lead > 0 ? (x) => (x < lead ? f / lead : (1 - f) / (span - lead)) : null;
      box.pace = pacer(subs.map((s) => ({ motion: s.motion, view: s.view })), box.playS, PACE_LIMIT_DEG_S, undefined, weight);
    }
    const along = box.pace ? box.pace(u) : Math.max(...subs.map((s) => s.motion.duration)) * u;
    for (const sub of subs) {
      const m = sub.motion;
      const tau = Math.min(m.t1, m.t0 + along);
      sub.el.dataset.tau = String(tau);
      sub.view.setFrame(m.frame(tau));
      // on a step the wheels leave the road to climb: no lifted-wheel ring (red is for a limit)
      for (const o of Object.values(sub.view.wheelObjs ?? {})) o.ring.visible = false;
      sub.view.renderInto(sub.canvas.getContext("2d"), sub.canvas.width, sub.canvas.height, this.scale);
    }
  }

  // A pane's record at its share u of the play (0: its first pose, 1: its last).
  pose(pane, u) {
    if (pane.subs) return this.posePair(pane, u);
    if (!pane.view) return;
    if (pane.motion) {
      const m = pane.motion;
      // paced over the play's stage time (pane.playS)
      if (pane.playS && !pane.pace) pane.pace = pacer([{ motion: m, view: pane.view }], pane.playS, PACE_LIMIT_DEG_S);
      const tau = m.t0 + (pane.pace ? pane.pace(u) : (m.t1 - m.t0) * u);
      pane.el.dataset.tau = String(tau);
      pane.view.setFrame(m.frame(tau));
    }
    if (pane.asm) {
      const a = pane.asm;
      const k = a.stage ? a.n * u : a.n;
      pane.el.dataset.tau = String(k);
      a.stage?.pose(k);
      a.stage?.lineResolution(pane.inner.w, pane.inner.h);
      let shown = 0;
      for (const mk of a.marks) {
        // from when its step starts to go in (step k goes in while u runs from k - 1 to k)
        mk.mesh.visible = mk.after === 0 || k > mk.after - 1;
        shown += mk.mesh.visible ? 1 : 0;
      }
      pane.el.dataset.marks = String(shown);
    }
    pane.view.renderInto(pane.canvas.getContext("2d"), pane.canvas.width, pane.canvas.height, this.scale);
  }

  draw(t) {
    const sp = this.spec;
    const A = N06_APPEAR;
    const share = (a, b) => (b > a ? Math.max(0, Math.min(1, (t - a) / (b - a))) : 1);
    for (const tile of this.tiles) {
      const { small, big } = tile;
      // the list: comes in, then the first pose until its turn is over, then the last
      const p = Math.max(0, Math.min(1, (t - tile.start) / sp.times.tile_in_s));
      const e = easeIn(p);
      small.el.style.opacity = String(Math.min(1, e));
      small.el.style.transform = `scale(${A.from_scale + (1 - A.from_scale) * e})`;
      small.el.style.visibility = p > 0 ? "visible" : "hidden";
      small.el.dataset.p = String(p);
      const now = t >= tile.turn.from && t < tile.turn.to;
      small.playS = big.playS = tile.play.to - tile.play.from;
      const u = share(tile.play.from, tile.play.to);
      const plays = !!(big.motion || big.asm?.stage || big.subs);
      // the list's tile plays along with the large box in its turn
      const along = now && plays && t >= tile.play.from && u < 1;
      // what moves: a box's canvas, or the pair's box as one thing (its two runs move together)
      const smallMover = small.canvas ?? small.el;
      const bigMover = big.canvas ?? big.el;
      if ((p > 0 && p < 1) || along) smallMover.dataset.kind = "moving";
      else delete smallMover.dataset.kind;
      small.el.dataset.featured = now ? "1" : "";
      // the pair holds a run that climbs: its border in ink, not the red of a limit
      small.el.style.borderColor = now ? (tile.pair ? this.layout.colors?.ink ?? sp.colors.rule : sp.overlay.color) : sp.colors.rule;
      for (const lb of small.subs ? small.subs.map((x) => x.label) : [small.label]) lb.style.visibility = now ? "hidden" : "";
      if (p > 0) this.pose(small, t >= tile.turn.to ? 1 : now ? u : 0);
      // the large box: in its turn, the record plays, then holds its end
      big.el.style.visibility = now ? "visible" : "hidden";
      const q = easeIn(share(tile.turn.from, tile.turn.from + sp.times.tile_in_s));
      big.el.style.opacity = String(Math.min(1, q));
      if (now && (q < 1 || (plays && u < 1))) bigMover.dataset.kind = "moving";
      else delete bigMover.dataset.kind;
      if (now) this.pose(big, u);
    }
  }
}

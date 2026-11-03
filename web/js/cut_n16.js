// N16: the assembly order. The parts go in one after another along their recorded
// paths in the 3D view and the bar over the view's lower edge follows. Then the bar is
// moved back and forth as a hand would drag it (the state follows, step by step) and
// stops on the last step. Positions, sizes, colours and times come from layout.json n16;
// a missing key is named on the page.
//
// What is read (the review of the first cuts: ids, a red ✕ on most steps, 12 steps a
// second): the caption names the part going in by its name in the design (no node or
// member ids, no specification in brackets); while the steps run fast (steps_from_s to
// steps_to_s) it holds one sentence for the whole order ("全 47 手順を、入る順に組む"),
// and from steps_to_s it names the step shown. The column at the right says in words
// what the record's checks found over all the steps (counted from the record): parts
// that do not fit, steps a tool or a hand can hardly reach; and, while the order is not
// running fast, the step shown with what its checks found. A part that does not fit is
// marked red in the view and on the bar; a step hard for a tool or a hand keeps the
// orange of the part going in (it can be done, with care).
//
// times: steps_from_s..steps_to_s the steps go in one by one (as many as the record
// has); scrub_s..scrub_to_s the bar goes back by SCRUB_BACK of the steps and forward
// again to the last one; lead_s is when the caption (the step's sentence) appears.

import * as THREE from "three";
import { AssemblySeek, AssemblyStage, assemblyRecord, nameOf, partNames, shortName, stepAt } from "./assembly.js";
import { clearOfBand } from "./compose.js";
import { storyOf } from "./cut_n03.js";
import { esc } from "./labels.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";

// The checks of a step in the viewers' words, when they fail: interference stops the
// part (red); tool and hand make the step hard (orange).
const FINDINGS = [
  { key: "no_interference", ja: "ぶつかって入らない", ok: "ぶつからずに入る", hard: true },
  { key: "tool_clearance", ja: "工具が入りにくい", ok: "工具が入る", hard: false },
  { key: "hand_reach", ja: "手が届きにくい", ok: "手が届く", hard: false },
];
const STOPS = FINDINGS.filter((f) => f.hard).map((f) => f.key);
const OK_GREEN = "#2f7d4f";
const HARD_RED = "#c8402a";

const $ = (id) => document.getElementById(id);

// Every value the cut reads (nested ones by their path); the top's min_text_px too.
const N16_KEYS = [
  "duration_s",
  "view.x", "view.y", "view.w", "view.h",
  "seek.x", "seek.y", "seek.w", "seek.h",
  "steps.x", "steps.y", "steps.w", "steps.h", "steps.title_px", "steps.text_px", "steps.row_gap",
  "times.lead_s", "times.steps_from_s", "times.steps_to_s", "times.scrub_s", "times.scrub_to_s",
  "colors.bg", "colors.panel", "colors.rule",
];

// How far the scrub drags the bar back: this share of the steps (at least one step).
const SCRUB_BACK = 0.4;
export const scrubBack = (n) => Math.max(1, Math.min(n, Math.round(n * SCRUB_BACK)));

const smooth = (p) => 0.5 - 0.5 * Math.cos(Math.PI * Math.max(0, Math.min(1, p)));

// Progress through the order (0..n) at cut time t for the times of layout.json n16.
export function n16Progress(T, n, t) {
  if (t < T.steps_from_s) return 0;
  if (t < T.steps_to_s) return (n * (t - T.steps_from_s)) / Math.max(1e-9, T.steps_to_s - T.steps_from_s);
  if (t < T.scrub_s || t >= T.scrub_to_s) return n;
  // a hand on the bar: back, then forward again; the state lands on whole steps as in A7
  const p = (t - T.scrub_s) / Math.max(1e-9, T.scrub_to_s - T.scrub_s);
  const back = scrubBack(n);
  const along = p < 0.5 ? n - back * smooth(p / 0.5) : n - back + back * smooth((p - 0.5) / 0.5);
  return Math.round(along);
}

const get = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

export function missingN16Keys(layout) {
  const out = N16_KEYS.filter((k) => get(layout.n16, k) === undefined);
  if (layout.min_text_px === undefined) out.push("min_text_px");
  return out;
}

export class CutN16 {
  constructor(layout, s) {
    const missing = missingN16Keys(layout);
    if (missing.length) throw new Error(`layout.json の n16 に無い: ${missing.join(", ")}`);
    this.layout = layout;
    this.spec = layout.n16;
    this.minPx = layout.min_text_px;
    this.textPx = Math.max(this.minPx, this.spec.steps.text_px);
    this.story = storyOf(layout, "n16");
    this.rec = assemblyRecord(s);
    if (!this.rec?.design) throw new Error("組み立ての記録がまだ無い (analysis.finished の data.assembly)");
    this.stage = $("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.build();
  }

  place(el, r) {
    Object.assign(el.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
    if (!clearOfBand(r, this.layout)) el.dataset.outside = "1";
  }

  build() {
    const sp = this.spec;
    // 3D
    const view = document.createElement("div");
    view.className = "n16-view";
    view.dataset.box = "n16.view";
    this.place(view, sp.view);
    Object.assign(view.style, { background: sp.colors.panel, borderColor: sp.colors.rule });
    this.canvas = document.createElement("canvas");
    this.caption = document.createElement("div");
    this.caption.className = "n16-caption";
    this.caption.dataset.box = "n16.caption";
    this.caption.dataset.kind = "label";
    this.caption.style.fontSize = `${Math.max(this.textPx, this.story.bodyPx)}px`;
    this.canvas.dataset.kind = "moving";
    view.append(this.canvas, this.caption);
    this.stage.append(view);
    this.view = new Workbench3D(null, "zu", { renderer: this.renderer });
    this.asm = new AssemblyStage(this.view);
    this.asm.ngChecks = STOPS; // red only for a part that does not fit
    this.asm.setRecord(this.rec);
    this.view.resize(sp.view.w, sp.view.h);
    // room under the vehicle for the bar laid over the view's lower edge
    const V = sp.view;
    const covered = Math.max(0, Math.min(V.h, V.y + V.h - sp.seek.y)); // from the bar's top to the view's bottom
    this.asm.fit(1.04, VIEWS.default, (covered / V.h) * 3.5);
    this.asm.lineResolution(sp.view.w, sp.view.h);

    // the bar over the view's lower edge (moved by the time, not by a hand)
    const seek = document.createElement("div");
    seek.dataset.box = "n16.seek";
    seek.classList.add("n16-seek");
    this.place(seek, sp.seek);
    seek.style.borderColor = sp.colors.rule;
    this.stage.append(seek);
    // the bar's step count is a note (the makers'), its red ticks only parts that do not fit
    seek.dataset.kind = "note";
    this.seek = new AssemblySeek(seek, { interactive: false, textPx: this.minPx });
    this.seek.setSteps(this.rec.steps, STOPS);

    // the column: what the checks found over the whole order, then the step shown
    const list = document.createElement("div");
    list.className = "n16-steps";
    list.dataset.box = "n16.steps";
    this.place(list, sp.steps);
    const px = Math.max(this.textPx, this.story.bodyPx);
    const n = this.rec.steps.length;
    const count = (key) => this.rec.steps.filter((s) => s[key] === false).length;
    // the main line: whether the assembly holds (every part goes in without interference);
    // steps hard for a tool or a hand are small notes under it (they can be done, with care:
    // the review of the second stills, 37 crosses read as "mostly broken")
    const rows = FINDINGS.map((f) => {
      const k = count(f.key);
      if (!f.hard) {
        const words = k ? `${f.ja}手順 ${k}` : `${f.ok} (全手順)`;
        return `<div class="n16-sum n16-soft" data-box="n16.sum.${f.key}" data-count="${k}" data-kind="note" style="font-size:${this.minPx}px">${esc(words)}</div>`;
      }
      const [mark, color] = k ? ["✕", HARD_RED] : ["✓", OK_GREEN];
      return `<div class="n16-sum" data-box="n16.sum.${f.key}" data-count="${k}"><b style="color:${color}">${mark}</b> ${esc(k ? f.ja : f.ok)}<span>${esc(k ? `${k} 手順` : "全手順")}</span></div>`;
    }).join("");
    list.innerHTML =
      `<h3 class="n16-title" data-box="n16.steps.title" style="font-size:${Math.max(this.minPx, sp.steps.title_px)}px">組み立ての手順</h3>` +
      `<div class="n16-total" data-box="n16.total">全 ${n} 手順</div>` +
      `<div class="n16-sums">${rows}</div>` +
      `<div class="n16-now" data-box="n16.now"></div>`;
    Object.assign(list.style, { fontSize: `${px}px` });
    for (const r of list.querySelectorAll(".n16-sum, .n16-total")) r.style.marginBottom = `${sp.steps.row_gap}px`;
    this.stage.append(list);
    this.list = list;
    this.now = list.querySelector(".n16-now");
    this.names = partNames(this.rec.design);
    this.current = null;
    this.mode = null;
    this.drawText(1, "start");
  }

  // The part of a step by its name in the design (the part before any bracket).
  partName(step) {
    return shortName(nameOf(this.names, step.part));
  }

  // The caption and the step shown in the column. mode "run": the steps go in fast, one
  // sentence for the whole order and no step in the column; else the step shown.
  drawText(current, mode) {
    if (current === this.current && mode === this.mode) return;
    this.current = current;
    this.mode = mode;
    const step = this.rec.steps[current - 1];
    this.caption.replaceChildren();
    this.now.replaceChildren();
    if (mode === "run") {
      this.caption.textContent = `全 ${this.rec.steps.length} 手順を、入る順に組む`;
      return;
    }
    if (!step) return;
    const b = document.createElement("b");
    b.textContent = `手順 ${step.order}`;
    this.caption.append(b, ` ${this.partName(step)}を付ける`);
    const found = FINDINGS.filter((f) => step[f.key] === false);
    const h = document.createElement("div");
    h.className = "n16-now-name";
    h.textContent = `手順 ${step.order} ${this.partName(step)}`;
    // what this step's checks found: a part that does not fit in red; otherwise "入る" in
    // green, with a step hard for a tool or a hand as a small note under it
    const hard = found.filter((x) => x.hard);
    const soft = found.filter((x) => !x.hard);
    const f = document.createElement("div");
    f.className = "n16-now-found";
    f.textContent = hard.length ? hard.map((x) => x.ja).join("・") : "入る";
    f.style.color = hard.length ? HARD_RED : OK_GREEN;
    this.now.append(h, f);
    if (soft.length) {
      const n = document.createElement("div");
      n.className = "n16-now-note";
      n.dataset.kind = "note";
      Object.assign(n.style, { fontSize: `${this.minPx}px`, color: "#8a857a" });
      n.textContent = soft.map((x) => x.ja).join("・");
      this.now.append(n);
    }
  }

  progressAt(t) {
    return n16Progress(this.spec.times, this.rec.steps.length, t);
  }

  resize(scale) {
    this.scale = scale;
    const V = this.spec.view;
    this.renderer.setSize(Math.ceil(V.w * scale), Math.ceil(V.h * scale), false);
    this.canvas.width = Math.round(V.w * scale);
    this.canvas.height = Math.round(V.h * scale);
  }

  draw(t) {
    const u = this.progressAt(t);
    const { current } = stepAt(u, this.rec.steps.length);
    const T = this.spec.times;
    this.drawText(current, t >= T.steps_from_s && t < T.steps_to_s ? "run" : "step");
    this.caption.style.visibility = t >= this.spec.times.lead_s ? "visible" : "hidden";
    this.seek.set(u, false);
    this.asm.pose(u);
    this.asm.lineResolution(this.spec.view.w, this.spec.view.h);
    this.view.renderInto(this.canvas.getContext("2d"), this.canvas.width, this.canvas.height, this.scale);
    document.documentElement.dataset.n16Step = String(current);
  }
}

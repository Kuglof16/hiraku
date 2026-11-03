// N16: the assembly order. The parts go in one after another along their recorded
// paths in the 3D view, the bar over the view's lower edge follows, and the list at
// the right marks the step going in with its checks (interference, tool clearance,
// hand reach) as recorded. Then the bar is moved back and forth as a hand would drag
// it (the state follows, step by step) and stops on the last step. Positions, sizes,
// colours and times come from layout.json n16; a missing key is named on the page.
//
// times: steps_from_s..steps_to_s the steps go in one by one (as many as the record
// has); scrub_s..scrub_to_s the bar goes back by SCRUB_BACK of the steps and forward
// again to the last one; lead_s is when the caption (the step's sentence) appears.

import * as THREE from "three";
import { AssemblySeek, AssemblyStage, assemblyRecord, partNames, shortName, stepAt, stepRowsHtml, summaryText } from "./assembly.js";
import { clearOfBand } from "./compose.js";
import { esc } from "./labels.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";

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
    this.caption.style.fontSize = `${this.textPx}px`;
    view.append(this.canvas, this.caption);
    this.stage.append(view);
    this.view = new Workbench3D(null, "zu", { renderer: this.renderer });
    this.asm = new AssemblyStage(this.view);
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
    this.seek = new AssemblySeek(seek, { interactive: false, textPx: this.minPx });
    this.seek.setSteps(this.rec.steps);

    // the list of steps
    const list = document.createElement("div");
    list.className = "n16-steps";
    list.dataset.box = "n16.steps";
    this.place(list, sp.steps);
    list.innerHTML =
      `<h3 class="n16-title" data-box="n16.steps.title" style="font-size:${Math.max(this.minPx, sp.steps.title_px)}px">組み立ての手順</h3>` +
      `<div class="n16-head" style="font-size:${this.minPx}px">${esc(summaryText(this.rec))}</div>` +
      `<ol class="asm-steps compact"></ol>`;
    this.stage.append(list);
    this.list = list;
    this.ol = list.querySelector("ol");
    this.current = null;
    this.drawList(1);
    this.fitList();
  }

  // The rows' text steps down a pixel at a time (not below min_text_px) until every row
  // ends inside the box; if they still do not, only the rows around the current step
  // are shown (window).
  fitList() {
    const box = this.spec.steps;
    const rows = () => [...this.ol.children];
    const bottom = () => {
      const top = this.list.getBoundingClientRect().top;
      const k = this.list.getBoundingClientRect().height / this.list.offsetHeight || 1;
      return Math.max(0, ...rows().filter((r) => r.style.display !== "none").map((r) => (r.getBoundingClientRect().bottom - top) / k));
    };
    let px = this.textPx;
    const apply = () => {
      this.ol.style.fontSize = `${px}px`;
      for (const r of rows()) r.style.marginBottom = `${box.row_gap}px`;
    };
    apply();
    while (bottom() > box.h && px > this.minPx) {
      px -= 1;
      apply();
    }
    this.rowPx = px;
    this.list.dataset.textPx = String(px);
    // when some rows must still be left out: as many as end inside the box, around the current step
    this.window = this.rec.steps.length;
    this.showWindow();
    while (bottom() > box.h && this.window > 1) {
      this.window -= 1;
      this.showWindow();
    }
  }

  showWindow() {
    const n = this.rec.steps.length;
    const w = this.window ?? n;
    const c = this.current ?? 1;
    const from = Math.max(0, Math.min(n - w, c - 1 - Math.floor(w / 2)));
    [...this.ol.children].forEach((r, i) => {
      r.style.display = i >= from && i < from + w ? "" : "none";
    });
  }

  drawList(current) {
    this.current = current;
    this.ol.innerHTML = stepRowsHtml(this.rec, current, { compact: true, box: "n16.step" });
    if (this.rowPx) {
      this.ol.style.fontSize = `${this.rowPx}px`;
      for (const r of this.ol.children) r.style.marginBottom = `${this.spec.steps.row_gap}px`;
      this.showWindow();
    }
    const step = this.rec.steps[current - 1];
    const name = step ? shortName(partNames(this.rec.design)[step.part] ?? step.part) : "";
    this.caption.replaceChildren();
    if (step) {
      const b = document.createElement("b");
      b.textContent = `手順 ${step.order}`;
      this.caption.append(b, ` ${step.text ?? name}`);
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
    if (current !== this.current) this.drawList(current);
    this.caption.style.visibility = t >= this.spec.times.lead_s ? "visible" : "hidden";
    this.seek.set(u, false);
    this.asm.pose(u);
    this.asm.lineResolution(this.spec.view.w, this.spec.view.h);
    this.view.renderInto(this.canvas.getContext("2d"), this.canvas.width, this.canvas.height, this.scale);
    document.documentElement.dataset.n16Step = String(current);
  }
}

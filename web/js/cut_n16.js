// N16: the assembly order. The parts go in one after another along their recorded
// paths in the 3D view, the bar over the view's lower edge follows (the video moves
// it, not a hand), and the list at the right marks the step going in with its checks
// (interference, tool clearance, hand reach) as recorded. Positions, sizes and times
// come from layout.json n16; a missing key is named on the page.

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
  "steps.x", "steps.y", "steps.w", "steps.h", "steps.text_px", "steps.row_gap",
  "times.lead_s", "times.step_s",
];

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
    this.stage.style.background = layout.colors?.n16_bg ?? "#f4f1ea";
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
    const overlap = Math.max(0, Math.min(V.y + V.h, sp.seek.y + sp.seek.h) - Math.max(V.y, sp.seek.y));
    this.asm.fit(1.04, VIEWS.default, (overlap / V.h) * 2.5);
    this.asm.lineResolution(sp.view.w, sp.view.h);

    // the bar over the view's lower edge (moved by the time, not by a hand)
    const seek = document.createElement("div");
    seek.dataset.box = "n16.seek";
    seek.classList.add("n16-seek");
    this.place(seek, sp.seek);
    this.stage.append(seek);
    this.seek = new AssemblySeek(seek, { interactive: false, textPx: this.minPx });
    this.seek.setSteps(this.rec.steps);

    // the list of steps
    const list = document.createElement("div");
    list.className = "n16-steps";
    list.dataset.box = "n16.steps";
    this.place(list, sp.steps);
    list.innerHTML =
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
    // rows that fit when some must be left out
    const n = this.rec.steps.length;
    this.window = n;
    if (bottom() > box.h) {
      const per = (this.ol.children[0]?.offsetHeight ?? px) + box.row_gap;
      const head = this.ol.offsetTop;
      this.window = Math.max(1, Math.floor((box.h - head) / per));
    }
    this.showWindow();
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

  // Progress through the order at stage time t: lead_s of the vehicle as it starts, then
  // step_s per step, held at the end.
  progressAt(t) {
    const T = this.spec.times;
    const n = this.rec.steps.length;
    return Math.max(0, Math.min(n, (t - T.lead_s) / T.step_s));
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
    this.seek.set(u, false);
    this.asm.pose(u);
    this.asm.lineResolution(this.spec.view.w, this.spec.view.h);
    this.view.renderInto(this.canvas.getContext("2d"), this.canvas.width, this.canvas.height, this.scale);
    document.documentElement.dataset.n16Step = String(current);
  }
}

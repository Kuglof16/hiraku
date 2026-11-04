// N14: the maker drops a file of know-how, it becomes rules for checking the design, and one
// of them catches a part. cut.html?cut=n14&run=<id>&t=<s>
//
// Boxes (layout.json n14): knowledge (the list of rules), view (the part caught, in 3D), ask
// (HIRAKU's question back to the maker, as a speech bubble).
//
// What is read from the run (events in seq order):
// - the rules there from the start: the knowledge.imported of origin "builtin" (the last per
//   doc_id), counted;
// - the dropped file: the last knowledge.imported of origin "dropped"; its doc is read as its
//   last import (the same doc imported again replaces it, docs/contract.md), so its rules and
//   the lines not made into rules (doc.skipped, with their reasons) are the last ones; the
//   maker's words with it are the last user.said before its first import;
// - the catch: the first rule_check.recorded of one of that doc's rules with hits; its first
//   hit's part (in the design of the check's version) and the values the check read;
// - the question: the last agent.said of the lead after the rule checks that ends in a
//   question, in the viewers' words (labels.js plainSentence: rule ids out, the part's id
//   replaced by its name).
// No rule id or part id is shown: the rules are their sentences, the part is its name.
//
// times (layout.json n14.times): from lead_s the list holds the count of the rules there from
// the start; at drop_s the file drops into the list (over DROP_S); at rule_s its rules join the
// list as rows, with the lines not made into rules and why; at hit_s the rule that catches a
// part is marked in the list and the part turns red in the view with a tag (its name and the
// value the rule read); at ask_s the question comes in. The vehicle turns slowly all along.
//
// The vehicle's size (layout.json n14.view.fill): at every frame the camera stands so that
// the vehicle on screen (the box round the projected points of view.contentPoints(): the frame,
// the parts' boxes, the wheels) takes `fill` of the view in the direction that binds (the
// larger of its shares of the view's width and height), with that box's middle at the view's
// middle (fillView). Measured on the demo vehicle from the default view: it stands about as
// tall as it is wide on screen, so `fill` of the view's long side (its width, 1000 px) would
// make it about 860 px tall in a view 556 px high, its roll bar and near wheel cut off; the
// binding direction is its height.
//
// Positions, sizes, colours, the length and the times come from layout.json n14; a missing
// key is named on the page.

import * as THREE from "three";
import { OVER_COLOR } from "./colormap.js";
import { clearOfBand } from "./compose.js";
import { esc, fmt, plainSentence } from "./labels.js";
import { VIEWS, cutView, isoToThree } from "./workbench3d.js";
import { storyOf } from "./cut_n03.js";

const $ = (id) => document.getElementById(id);
const BORDER_PX = 2;
const PAD_PX = 20;
const DROP_S = 0.5; // how long the file takes to drop into the list
const TURN_DEG = 30;
const FAINT = 0.12; // the other parts' opacity once one is caught

const N14_KEYS = [
  "duration_s",
  "knowledge.x", "knowledge.y", "knowledge.w", "knowledge.h", "knowledge.title_px", "knowledge.text_px",
  "view.x", "view.y", "view.w", "view.h", "view.fill",
  "ask.x", "ask.y", "ask.w", "ask.h", "ask.text_px",
  "times.lead_s", "times.drop_s", "times.rule_s", "times.hit_s", "times.ask_s",
  "colors.bg", "colors.panel", "colors.rule",
];
const TOP_KEYS = ["min_text_px", "stage", "subtitle_band"];

const get = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

export function missingN14Keys(layout) {
  return [...N14_KEYS.filter((k) => get(layout.n14, k) === undefined), ...TOP_KEYS.filter((k) => layout[k] === undefined)];
}

const shortName = (p) => String(p?.name ?? "").replace(/\s*[（(].*$/, "") || null;

// A value the rule read, with the unit its name ends in (mass_kg -> kg).
function valueText(key, v) {
  const unit = /_kg$/.test(key) ? "kg" : /_mm$/.test(key) ? "mm" : /_deg$/.test(key) ? "°" : "";
  return unit ? `${fmt(v, 4)} ${unit}` : fmt(v, 4);
}

// What the cut shows, read from the events (see the top).
export function n14Records(events, designs) {
  const imports = events.filter((e) => e.kind === "knowledge.imported");
  const lastOf = {};
  for (const e of imports) lastOf[e.data?.doc?.doc_id] = e.data;
  const builtin = Object.values(lastOf).filter((d) => d.doc?.origin === "builtin");
  const dropEv = imports.filter((e) => e.data?.doc?.origin === "dropped").at(-1) ?? null;
  const docId = dropEv?.data?.doc?.doc_id;
  const doc = docId ? lastOf[docId] : null;
  const firstImport = docId ? imports.find((e) => e.data?.doc?.doc_id === docId) : null;
  const said = firstImport ? events.filter((e) => e.kind === "user.said" && e.seq < firstImport.seq).at(-1) ?? null : null;
  const ruleIds = new Set((doc?.rules ?? []).map((r) => r.rule_id));
  const checks = events.filter((e) => e.kind === "rule_check.recorded");
  const hitEv = checks.find((e) => ruleIds.has(e.data?.rule_id) && (e.data?.hits ?? []).length) ?? null;
  const hit = hitEv?.data ?? null;
  const design = hit ? designs[hit.design_version] ?? null : null;
  const h0 = hit?.hits?.[0] ?? null;
  const part = h0 ? (design?.parts ?? []).find((p) => p.id === h0.part_id) ?? null : null;
  const lastCheck = checks.at(-1)?.seq ?? -1;
  const askEv = events.filter((e) => e.kind === "agent.said" && e.agent === "lead" && e.seq > lastCheck && /[か?？]\s*[。]?$/.test(String(e.text ?? "").trim())).at(-1) ?? null;
  return {
    baseCount: builtin.reduce((n, d) => n + (d.rules ?? []).length, 0),
    doc,
    said: said?.text ?? null,
    rules: doc?.rules ?? [],
    skipped: doc?.doc?.skipped ?? [],
    hit,
    hitRule: hit ? (doc?.rules ?? []).find((r) => r.rule_id === hit.rule_id) : null,
    part,
    partName: shortName(part),
    values: h0?.values ?? {},
    design,
    ask: askEv ? plainAsk(askEv.text, doc?.rules ?? [], design) : null,
  };
}

// The question in the viewers' words: "<id>「text」" -> "「text」", "(<part id>)" -> "(<name>)",
// then the ids that are left taken out (plainSentence).
export function plainAsk(text, rules, design) {
  let s = String(text ?? "");
  for (const r of rules) s = s.split(`${r.rule_id}「`).join("「");
  for (const p of design?.parts ?? []) {
    const name = shortName(p);
    if (name) s = s.split(`(${p.id})`).join(`(${name})`);
  }
  return plainSentence(s);
}

export class CutN14 {
  constructor(layout, s, events) {
    const missing = missingN14Keys(layout);
    if (missing.length) throw new Error(`layout.json の n14 に無い: ${missing.join(", ")}`);
    this.layout = layout;
    this.s = s;
    this.spec = layout.n14;
    this.story = storyOf(layout, "n14");
    this.rec = n14Records(events ?? [], s.designs);
    if (!this.rec.doc) throw new Error("渡されたナレッジの記録 (knowledge.imported) が無い");
    this.stage = $("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.scale = 1;
    this.build();
  }

  place(el, r) {
    Object.assign(el.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
    if (!clearOfBand(r, this.layout)) el.dataset.outside = "1";
  }

  panel(cls, box, r) {
    const el = document.createElement("div");
    el.className = cls;
    el.dataset.box = box;
    this.place(el, r);
    Object.assign(el.style, { background: this.spec.colors.panel, border: `${BORDER_PX}px solid ${this.spec.colors.rule}` });
    this.stage.append(el);
    return el;
  }

  build() {
    const sp = this.spec;
    const rec = this.rec;
    const body = this.story.bodyPx;
    const K = sp.knowledge;
    const tp = Math.max(K.title_px, body);
    const xp = Math.max(K.text_px, body);
    const k = this.panel("n14-knowledge", "n14.knowledge", K);
    Object.assign(k.style, { padding: `${PAD_PX}px`, fontSize: `${xp}px` });
    const rows = rec.rules
      .map((r) => `<li data-rule="${esc(r.rule_id)}"${r.rule_id === rec.hit?.rule_id ? ' class="catch"' : ""}><span class="mark"></span>${esc(plainSentence(r.text))}</li>`)
      .join("");
    const skipped = rec.skipped.map((x) => `<li>${esc(plainSentence(x.text))}<small>${esc(plainSentence(x.reason))}</small></li>`).join("");
    k.innerHTML =
      `<h3 data-kind="label" style="font-size:${tp}px">設計のチェックの規則</h3>` +
      `<p class="base">最初から入っている規則 <span class="count" data-n="${rec.baseCount}">${rec.baseCount}</span> 件</p>` +
      `<div class="file" data-box="n14.file"><span class="name">${esc(rec.doc.doc?.filename ?? "")}</span>` +
      (rec.said ? `<span class="said">${esc(plainSentence(rec.said))}</span>` : "") +
      `</div>` +
      `<div class="new" data-box="n14.rules">` +
      `<p class="head">メモから <b class="num" data-kind="number" data-n="${rec.rules.length}">${rec.rules.length}</b> 件の規則</p>` +
      `<ul class="rules">${rows}</ul>` +
      (rec.skipped.length ? `<p class="head skip-head">規則にしなかった <span data-n="${rec.skipped.length}">${rec.skipped.length}</span> 文</p><ul class="skipped">${skipped}</ul>` : "") +
      `</div>`;
    this.fileEl = k.querySelector(".file");
    this.newEl = k.querySelector(".new");
    this.catchEl = k.querySelector("li.catch");
    this.buildView();
    this.buildAsk();
  }

  buildView() {
    const sp = this.spec;
    const V = sp.view;
    this.viewEl = this.panel("n14-view", "n14.view", V);
    this.inner = { w: V.w - 2 * BORDER_PX, h: V.h - 2 * BORDER_PX };
    const design = this.rec.design ?? this.s.designs[this.s.currentVersion] ?? null;
    if (!design) {
      this.viewEl.insertAdjacentHTML("beforeend", `<p class="none" style="font-size:${this.layout.min_text_px}px">この記録に設計が無い</p>`);
      return;
    }
    this.canvas = document.createElement("canvas");
    this.canvas.dataset.kind = "moving";
    this.viewEl.append(this.canvas);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    const v = cutView(this, "n14");
    v.setDesign(design);
    v.setLoading("empty"); // the parts the rules look at; the people and the load are drawn faint
    v.setScene(null, null);
    v.setToggles({ parts: true, contour: false, arrows: false, cg: false, names: false, limit: false });
    v.resize(this.inner.w, this.inner.h);
    v.fitPoints(v.contentPoints(), VIEWS.default, 1.1);
    this.view = v;
    this.orbit = v.cameraView();
    this.points = v.contentPoints().map((q) => isoToThree(q.toArray()));
    this.span = new THREE.Box3().setFromPoints(this.points).getSize(new THREE.Vector3()).length();
    // each part mesh gets its own material, so one part can turn red and the rest faint
    this.meshes = [];
    for (const [id, o] of Object.entries(v.partObjs)) {
      o.group.traverse((x) => {
        if (!x.isMesh) return;
        x.material = x.material.clone();
        this.meshes.push({ id, mesh: x, color: x.material.color.clone(), opacity: x.material.opacity, transparent: x.material.transparent });
      });
    }
    const pid = this.rec.part?.id;
    this.partCenter = pid && v.partObjs[pid] ? v.partObjs[pid].center.clone() : null;
    // the tag: the part's name and the value the rule read
    if (this.rec.part) {
      const vals = Object.entries(this.rec.values).map(([key, x]) => valueText(key, x)).join("・");
      this.tag = document.createElement("div");
      this.tag.className = "n14-tag";
      this.tag.dataset.box = "n14.tag";
      this.tag.dataset.kind = "label";
      Object.assign(this.tag.style, { fontSize: `${Math.max(this.spec.knowledge.title_px, this.story.bodyPx)}px`, borderColor: OVER_COLOR });
      this.tag.innerHTML = `${esc(this.rec.partName ?? "")} <span class="val">${esc(vals)}</span>`;
      this.viewEl.append(this.tag);
    }
  }

  buildAsk() {
    const sp = this.spec;
    const A = sp.ask;
    const el = document.createElement("div");
    el.className = "n14-ask";
    el.dataset.box = "n14.ask";
    this.place(el, A);
    Object.assign(el.style, { background: sp.colors.panel, borderColor: sp.colors.rule });
    const px = Math.max(A.text_px, this.story.bodyPx);
    el.innerHTML = this.rec.ask
      ? `<b class="who" data-kind="label" style="font-size:${px}px">HIRAKU</b><p class="q" style="font-size:${px}px">${esc(this.rec.ask)}</p>`
      : `<p class="none" style="font-size:${this.layout.min_text_px}px">記録に問いかけが無い</p>`;
    this.stage.append(el);
    this.askEl = el;
    const q = el.querySelector(".q");
    if (q) {
      // as large as the bubble allows, down to the body size
      const room = A.h - 2 * BORDER_PX - 2 * PAD_PX - (el.querySelector(".who")?.offsetHeight ?? 0);
      let p = px;
      while (q.scrollHeight > room && p > this.story.bodyPx) q.style.fontSize = `${(p -= 1)}px`;
    }
  }

  resize(scale) {
    this.scale = scale;
    if (!this.view) return;
    this.renderer.setSize(Math.ceil(this.inner.w * scale), Math.ceil(this.inner.h * scale), false);
    this.canvas.width = Math.round(this.inner.w * scale);
    this.canvas.height = Math.round(this.inner.h * scale);
  }

  // The caught part red and the others faint (on), or every part as it was (off).
  paintHit(on) {
    if (this.painted === on) return;
    this.painted = on;
    const pid = this.rec.part?.id;
    for (const m of this.meshes ?? []) {
      const mat = m.mesh.material;
      if (on && m.id === pid) {
        mat.color.set(OVER_COLOR);
        mat.opacity = 1;
        mat.transparent = false;
      } else if (on) {
        mat.color.copy(m.color);
        mat.transparent = true;
        mat.opacity = Math.min(m.opacity, FAINT);
      } else {
        mat.color.copy(m.color);
        mat.opacity = m.opacity;
        mat.transparent = m.transparent;
      }
      mat.needsUpdate = true;
    }
  }

  // Where the vehicle is on screen with the camera as it stands: the box round its projected
  // points in normalised device coordinates (-1..1).
  extentNdc() {
    const cam = this.view.camera;
    cam.updateMatrixWorld();
    const e = { x0: Infinity, x1: -Infinity, y0: Infinity, y1: -Infinity };
    for (const p of this.points) {
      const q = p.clone().project(cam);
      e.x0 = Math.min(e.x0, q.x);
      e.x1 = Math.max(e.x1, q.x);
      e.y0 = Math.min(e.y0, q.y);
      e.y1 = Math.max(e.y1, q.y);
    }
    return e;
  }

  // The camera looking along -off (from target + off) at the distance and the aim where the
  // vehicle takes `fill` of the view in the binding direction, its box centred (see the top).
  // A few steps of moving the aim by the box's offset and scaling the distance by its share;
  // each step starts from the same place, so the frame at t is always the same.
  fillView(target0, off, fill) {
    const v = this.view;
    const cam = v.camera;
    const dir = off.clone().normalize();
    let target = target0.clone();
    let dist = off.length();
    const tv = Math.tan((cam.fov * Math.PI) / 360);
    for (let i = 0; i < 8; i += 1) {
      v.lookFrom({ target, position: target.clone().addScaledVector(dir, dist), near: dist / 50, far: dist + this.span * 3 });
      const e = this.extentNdc();
      const share = Math.max((e.x1 - e.x0) / 2, (e.y1 - e.y0) / 2);
      const right = new THREE.Vector3(1, 0, 0).applyQuaternion(cam.quaternion);
      const up = new THREE.Vector3(0, 1, 0).applyQuaternion(cam.quaternion);
      target = target.clone()
        .addScaledVector(right, ((e.x0 + e.x1) / 2) * dist * tv * cam.aspect)
        .addScaledVector(up, ((e.y0 + e.y1) / 2) * dist * tv);
      dist *= share / fill;
    }
    v.lookFrom({ target, position: target.clone().addScaledVector(dir, dist), near: dist / 50, far: dist + this.span * 3 });
    // the box in the view's own pixels (for the tests: its share and its middle)
    const e = this.extentNdc();
    this.extent = { x0: ((e.x0 + 1) / 2) * this.inner.w, x1: ((e.x1 + 1) / 2) * this.inner.w, y0: ((1 - e.y1) / 2) * this.inner.h, y1: ((1 - e.y0) / 2) * this.inner.h, w: this.inner.w, h: this.inner.h };
  }

  draw(t) {
    const sp = this.spec;
    const T = sp.times;
    // the file drops in from above over DROP_S from drop_s
    const d = Math.max(0, Math.min(1, (t - T.drop_s) / DROP_S));
    const e = 1 - (1 - d) ** 3;
    this.fileEl.style.visibility = t >= T.drop_s ? "visible" : "hidden";
    this.fileEl.style.opacity = String(e);
    this.fileEl.style.transform = `translateY(${(-80 * (1 - e)).toFixed(1)}px)`;
    if (t >= T.drop_s && d < 1) this.fileEl.dataset.kind = "moving";
    else delete this.fileEl.dataset.kind;
    this.newEl.style.visibility = t >= T.rule_s ? "visible" : "hidden";
    const hit = !!this.rec.hit && t >= T.hit_s;
    if (this.catchEl) {
      this.catchEl.dataset.hit = hit ? "1" : "0";
      this.catchEl.querySelector(".mark").textContent = hit ? "✕" : "";
    }
    this.askEl.style.visibility = t >= T.ask_s ? "visible" : "hidden";
    document.documentElement.dataset.n14Step = t >= T.ask_s ? "ask" : hit ? "hit" : t >= T.rule_s ? "rules" : t >= T.drop_s ? "drop" : "start";
    if (!this.view) return;
    this.paintHit(hit);
    const p = Math.max(0, Math.min(1, (t - T.lead_s) / Math.max(1e-9, sp.duration_s - T.lead_s)));
    const o = this.orbit;
    const ang = ((TURN_DEG * (0.5 - 0.5 * Math.cos(Math.PI * p))) * Math.PI) / 180;
    const off = o.position.clone().sub(o.target).applyAxisAngle(new THREE.Vector3(0, 1, 0), ang);
    this.fillView(o.target, off, this.spec.view.fill);
    this.view.renderInto(this.canvas.getContext("2d"), this.canvas.width, this.canvas.height, this.scale);
    if (this.tag) {
      this.tag.style.visibility = hit ? "visible" : "hidden";
      const at = this.partCenter ? this.view.screenOf(this.partCenter) : null;
      if (at) {
        // above the part, kept inside the view
        const x = at.x / this.scale;
        const y = at.y / this.scale;
        const w = this.tag.offsetWidth;
        const h = this.tag.offsetHeight;
        const left = Math.max(12, Math.min(this.inner.w - w - 12, x - w / 2));
        const top = Math.max(12, Math.min(this.inner.h - h - 12, y - h - 60));
        Object.assign(this.tag.style, { left: `${left}px`, top: `${top}px` });
        this.tagAt = { x, y };
      }
    }
  }
}

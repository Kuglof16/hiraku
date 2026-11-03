// N07: the contour of a stress analysis on the frame's plate mesh. The vehicle turns
// slowly in the colours of the version before the reinforcement, the camera comes to
// that version's largest element, and the colours go over to the version after it, on
// one colour scale; then it stays there. The words on the page are only "補強の前" /
// "補強の後" and the stress's name and unit: the difference is shown by the colours and
// the shape, not by naming the analyses. One tag, one colour bar (its two ends) and one
// number (the largest value of the state shown), as the story's limits ask; the
// notes under them are small. Positions, sizes, colours, the length and the
// times come from layout.json n07; a missing key is named on the page.
// cut.html?cut=n07&run=<id>&t=<s>[&lc=<load case>][&before=<version>&after=<version>]
//
// times: lead_s..turn_to_s the turn (turn_deg in all); zoom_s..zoom_to_s the camera
// comes to the largest element (its neighbourhood then takes zoom.fill of the view's
// height); swap_s..swap_to_s the version after fades in over the one before (same
// camera, same scale). Before lead_s and after each step the picture holds still.

import * as THREE from "three";
import { BAND_COLORS, BANDS, OVER_COLOR } from "./colormap.js";
import { clearOfBand } from "./compose.js";
import { FIELD_JA, contourOf, fieldsOf, onSharedScale, preferredField, sameField } from "./fields.js";
import { esc, fmt, fmtVU, unitLabel } from "./labels.js";
import { VIEWS, Workbench3D, isoToThree } from "./workbench3d.js";
import { storyOf } from "./cut_n03.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const BORDER_PX = 2; // the view's rule line
const STATE_JA = ["補強の前", "補強の後"];

const N07_KEYS = [
  "duration_s",
  "view.x", "view.y", "view.w", "view.h",
  "legend.x", "legend.y", "legend.w", "legend.h", "legend.title_px", "legend.text_px",
  "label.x", "label.y", "label.size_px",
  "times.lead_s", "times.turn_to_s", "times.zoom_s", "times.zoom_to_s", "times.swap_s", "times.swap_to_s",
  "turn_deg", "zoom.fill",
  "colors.bg", "colors.panel", "colors.rule",
  "overlay.color", "overlay.opacity",
];
const TOP_KEYS = ["min_text_px", "stage", "subtitle_band"];

const get = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

export function missingN07Keys(layout) {
  return [...N07_KEYS.filter((k) => get(layout.n07, k) === undefined), ...TOP_KEYS.filter((k) => layout[k] === undefined)];
}

const clamp01 = (p) => Math.max(0, Math.min(1, p));
const smooth = (p) => 0.5 - 0.5 * Math.cos(Math.PI * clamp01(p));
const along = (t, a, b) => clamp01((t - a) / Math.max(1e-9, b - a));

// Where the cut is at time t (layout.json n07.times): the turn's angle (deg) and the
// shares of the zoom and of the swap (0..1).
export function n07Progress(sp, t) {
  const T = sp.times;
  return {
    turnDeg: sp.turn_deg * smooth(along(t, T.lead_s, T.turn_to_s)),
    zoom: smooth(along(t, T.zoom_s, T.zoom_to_s)),
    mix: smooth(along(t, T.swap_s, T.swap_to_s)),
  };
}

// The fields on a mesh (with its record) of an analysis, of the first kind that has one.
function meshFields(s, ana) {
  const on = fieldsOf(ana).filter((f) => f.mesh_id && s.meshes[f.mesh_id]);
  return on.filter((f) => f.kind === on[0]?.kind);
}

// The newest ok analysis of a load case and version with a field on a mesh.
function meshAnalysis(s, lcId, version) {
  return s.order.analyses.map((id) => s.analyses[id]).filter((a) => a.status === "ok" && a.load_case_id === lcId && a.design_version === version && meshFields(s, a).length).at(-1) ?? null;
}

export class CutN07 {
  constructor(layout, s) {
    const missing = missingN07Keys(layout);
    if (missing.length) throw new Error(`layout.json の n07 に無い: ${missing.join(", ")}`);
    this.layout = layout;
    this.s = s;
    this.spec = layout.n07;
    this.minPx = layout.min_text_px;
    this.px = (v) => Math.max(this.minPx, v);
    this.story = storyOf(layout, "n07");
    this.pick();
    this.stage = $("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.build();
  }

  // The load case (?lc=, else the first in the project's order with a mesh field) and the
  // versions (?before= / ?after=, else the first and the last with one on that case).
  pick() {
    const s = this.s;
    const cases = (s.project?.load_cases ?? []).map((c) => c.load_case_id);
    const withMesh = (lc) => [...s.versions].sort((a, b) => a - b).filter((v) => meshAnalysis(s, lc, v));
    const lc = params.get("lc") ?? cases.find((c) => withMesh(c).length) ?? null;
    const versions = lc ? withMesh(lc) : [];
    if (!versions.length) throw new Error(`メッシュの上の値の記録が無い${lc ? ` (${lc})` : ""}`);
    const num = (k) => (params.has(k) ? Number(params.get(k)) : null);
    const vb = num("before") ?? versions[0];
    const va = num("after") ?? versions.at(-1);
    this.lc = (s.project?.load_cases ?? []).find((c) => c.load_case_id === lc) ?? null;
    const before = meshAnalysis(s, lc, vb);
    if (!before) throw new Error(`版 ${vb} のメッシュの上の値の記録が無い (${lc})`);
    const fb = preferredField(meshFields(s, before), s.meshes);
    const after = va !== vb ? meshAnalysis(s, lc, va) : null;
    if (va !== vb && !after) throw new Error(`版 ${va} のメッシュの上の値の記録が無い (${lc})`);
    const fa = after ? fieldsOf(after).find((f) => sameField(f, fb) && s.meshes[f.mesh_id]) : null;
    if (after && !fa) throw new Error(`版 ${va} に版 ${vb} と同じ field が無い`);
    const crit = this.lc?.criterion ?? null;
    const [cb, ca] = onSharedScale([contourOf(before, fb, crit, s.meshes), fa ? contourOf(after, fa, crit, s.meshes) : null]);
    this.cases = [
      { version: vb, name: STATE_JA[0], ana: before, field: fb, ct: cb },
      ...(fa ? [{ version: va, name: STATE_JA[1], ana: after, field: fa, ct: ca }] : []),
    ];
  }

  place(el, r) {
    Object.assign(el.style, { left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px` });
    if (!clearOfBand(r, this.layout)) el.dataset.outside = "1";
  }

  build() {
    const sp = this.spec;
    const V = sp.view;
    const box = document.createElement("div");
    box.className = "n07-view";
    box.dataset.box = "n07.view";
    this.place(box, V);
    Object.assign(box.style, { background: sp.colors.panel, border: `${BORDER_PX}px solid ${sp.colors.rule}` });
    this.inner = { w: V.w - 2 * BORDER_PX, h: V.h - 2 * BORDER_PX };
    this.stage.append(box);
    // the mark round the largest element once the camera is there: a ring in the overlay's
    // colour, its fill clear in the middle (the colours it points at stay as they are) and
    // the overlay's opacity only toward the rim
    this.mark = document.createElement("div");
    this.mark.className = "n07-mark";
    const [r, g, b] = new THREE.Color(sp.overlay.color).toArray().map((x) => Math.round(x * 255));
    const rim = `rgba(${r}, ${g}, ${b}, ${sp.overlay.opacity})`;
    Object.assign(this.mark.style, { borderColor: sp.overlay.color, background: `radial-gradient(circle, transparent 60%, ${rim} 100%)`, opacity: "0" });
    for (const c of this.cases) {
      c.el = document.createElement("canvas");
      c.el.dataset.kind = "moving";
      box.append(c.el);
      const v = new Workbench3D(null, "zu", { renderer: this.renderer });
      v.setDesign(this.s.designs[c.version] ?? null);
      v.setLoading(this.lc?.params?.loading ?? null);
      v.setScene(this.lc, c.ana);
      v.setContour(c.ct);
      v.setVectors(null);
      v.setToggles({ parts: false, contour: true, arrows: false, cg: false, names: false, limit: false });
      v.resize(this.inner.w, this.inner.h);
      c.view = v;
    }
    box.append(this.mark);
    const first = this.cases[0];
    // the turn: about the vertical through the vehicle's middle, from the default view
    const bv = first.view;
    bv.fitPoints(bv.contentPoints(), VIEWS.default, 1.12);
    this.orbit = bv.cameraView();
    this.focus = bv.maxFocus(first.ct);
    this.focusView = bv.focusView(this.focus, sp.zoom.fill, "height");
    this.buildLegend();
    this.buildLabel();
  }

  // A tall legend: the stress's name and unit, the colour bar upright with its two ends (the
  // top says when it is the allowable value), the largest recorded value of the state shown
  // (the one emphasised number), and small notes: the colour past the allowable value, one
  // scale for both states.
  buildLegend() {
    const sp = this.spec;
    const L = sp.legend;
    const el = document.createElement("div");
    el.className = "n07-legend";
    el.dataset.box = "n07.legend";
    this.place(el, L);
    Object.assign(el.style, { background: sp.colors.panel, borderColor: sp.colors.rule });
    const ct = this.cases[0].ct;
    const f = this.cases[0].field;
    const unit = unitLabel(f.unit);
    // what is read is at least the story's body text size
    const tp = Math.max(this.px(L.title_px), this.story.bodyPx);
    const xp = Math.max(this.px(L.text_px), this.story.bodyPx);
    const ticks = [
      `<span style="top:0%">${esc(ct.limitKnown ? `許容 ${fmt(ct.max, 4)}` : fmt(ct.max, 3))}</span>`,
      `<span style="top:100%">${esc(fmt(ct.min, 3))}</span>`,
    ];
    const rows = this.cases
      .map((c) => `<div class="row" data-version="${c.version}"><b>${esc(c.name)}</b><span>最大 ${esc(fmtVU(c.ct.fieldMax, f.unit))}</span></div>`)
      .join("");
    el.innerHTML =
      `<h3 style="font-size:${tp}px">${esc(FIELD_JA[f.kind] ?? f.kind)} [${esc(unit || "1")}]</h3>` +
      `<div class="scale" data-kind="legend" style="font-size:${xp}px"><i class="bar" style="background:linear-gradient(0deg, ${BAND_COLORS.map((c, i) => `${c} ${(i * 100) / BANDS}% ${((i + 1) * 100) / BANDS}%`).join(",")})"></i><div class="ticks">${ticks.join("")}</div></div>` +
      `<div class="rows" style="font-size:${xp}px">${rows}</div>` +
      (ct.limitKnown ? `<p style="font-size:${this.minPx}px"><i class="sw" style="background:${OVER_COLOR}"></i>許容を超えた所</p>` : "") +
      (this.cases.length > 1 ? `<p class="src" style="font-size:${this.minPx}px">前と後で同じ目盛り</p>` : "");
    this.stage.append(el);
    this.legend = el;
  }

  buildLabel() {
    const sp = this.spec;
    const el = document.createElement("div");
    el.className = "n07-label";
    el.dataset.box = "n07.label";
    el.dataset.kind = "label";
    Object.assign(el.style, { left: `${sp.label.x}px`, top: `${sp.label.y}px`, fontSize: `${Math.max(this.px(sp.label.size_px), this.story.bodyPx)}px` });
    this.stage.append(el);
    this.label = el;
    this.labelFor = null;
  }

  setLabel(c) {
    if (this.labelFor === c) return;
    this.labelFor = c;
    this.label.textContent = c.name;
    // only the state shown has its largest value on the legend (the one emphasised number)
    for (const row of this.legend.querySelectorAll(".row")) {
      const on = Number(row.dataset.version) === c.version;
      row.classList.toggle("on", on);
      row.style.display = on ? "" : "none";
      if (on) row.dataset.kind = "number";
      else delete row.dataset.kind;
    }
  }

  resize(scale) {
    this.scale = scale;
    this.renderer.setSize(Math.ceil(this.inner.w * scale), Math.ceil(this.inner.h * scale), false);
    for (const c of this.cases) {
      c.el.width = Math.round(this.inner.w * scale);
      c.el.height = Math.round(this.inner.h * scale);
    }
  }

  // The camera: on the turn, then on the way to the largest element.
  cameraAt(p) {
    const o = this.orbit;
    const off = o.position.clone().sub(o.target).applyAxisAngle(new THREE.Vector3(0, 1, 0), (p.turnDeg * Math.PI) / 180);
    if (p.zoom <= 0) return { target: o.target.clone(), position: o.target.clone().add(off), near: o.near, far: o.far };
    const f = this.focusView;
    const fOff = f.position.clone().sub(f.target);
    const u = p.zoom;
    const target = o.target.clone().lerp(f.target, u);
    const dir = off.clone().normalize().lerp(fOff.clone().normalize(), u).normalize();
    const dist = Math.exp(Math.log(off.length()) * (1 - u) + Math.log(fOff.length()) * u);
    return { target, position: target.clone().addScaledVector(dir, dist), near: o.near + (f.near - o.near) * u, far: o.far + (f.far - o.far) * u };
  }

  draw(t) {
    const p = n07Progress(this.spec, t);
    this.now = { t, ...p };
    const [b, a] = this.cases;
    b.view.lookFrom(this.cameraAt(p));
    const mix = a ? p.mix : 0;
    if (mix < 1) b.view.renderInto(b.el.getContext("2d"), b.el.width, b.el.height, this.scale);
    b.el.style.opacity = mix < 1 ? "1" : "0";
    if (a) {
      a.el.style.opacity = String(mix);
      a.view.copyCamera(b.view);
      if (mix > 0) a.view.renderInto(a.el.getContext("2d"), a.el.width, a.el.height, this.scale);
    }
    this.setLabel(a && mix >= 0.5 ? a : b);
    // the ring round the largest element (of the version before), as wide as zoom.fill of the view's height
    const at = isoToThree(this.focus.center.toArray()).project(b.view.camera);
    const d = this.spec.zoom.fill * this.inner.h;
    Object.assign(this.mark.style, {
      left: `${BORDER_PX + ((at.x + 1) / 2) * this.inner.w - d / 2}px`,
      top: `${BORDER_PX + ((1 - at.y) / 2) * this.inner.h - d / 2}px`,
      width: `${d}px`,
      height: `${d}px`,
      opacity: p.zoom >= 1 ? "1" : "0",
    });
    document.documentElement.dataset.n07Version = String(this.labelFor.version);
  }
}

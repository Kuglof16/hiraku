// N07: the contour of a shell analysis. The vehicle turns slowly while the stress
// colours run over its frame (a front of colour from the front to the rear), then the
// camera comes close to the largest element of the version before the change, and
// the colours go over to the version after it, on one colour scale. Positions, sizes,
// colours and times come from layout.json n07; a missing key is named on the page.
// cut.html?cut=n07&run=<id>&t=<s>[&lc=<load case>][&before=<version>&after=<version>]
//
// times: 0..turn_to_s the turn (turn_deg in all); lead_s..turn_to_s the colours run
// over the frame; zoom_s..zoom_to_s the camera comes to the largest element (its
// neighbourhood then takes `zoom` of the view's smaller side); swap_s..swap_to_s the
// version after the change fades in over the one before (same camera, same scale).
//
// The front of colour leaves the elements it has not reached plain, rather than
// showing them at a share of their values: the record has one load step, so values in
// between would be made up.

import * as THREE from "three";
import { BAND_COLORS, OVER_COLOR } from "./colormap.js";
import { clearOfBand } from "./compose.js";
import { FIELD_JA, SURFACE_JA, contourOf, fieldsOf, onSharedScale, preferredField, sameField } from "./fields.js";
import { esc, fmt, fmtVU, unitLabel } from "./labels.js";
import { VIEWS, Workbench3D, isoToThree } from "./workbench3d.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const BORDER_PX = 2; // the view's rule line

const N07_KEYS = [
  "duration_s",
  "view.x", "view.y", "view.w", "view.h",
  "legend.x", "legend.y", "legend.w", "legend.h", "legend.title_px", "legend.text_px",
  "label.x", "label.y", "label.size_px",
  "times.lead_s", "times.turn_to_s", "times.zoom_s", "times.zoom_to_s", "times.swap_s", "times.swap_to_s",
  "turn_deg", "zoom",
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

// Where the cut is at time t (layout.json n07.times): the turn's angle (deg), the share
// of the colour front, of the zoom and of the swap (0..1).
export function n07Progress(sp, t) {
  const T = sp.times;
  return {
    turnDeg: sp.turn_deg * smooth(along(t, 0, T.turn_to_s)),
    sweep: along(t, T.lead_s, T.turn_to_s),
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
function shellAnalysis(s, lcId, version) {
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
    const px = (v) => Math.max(this.minPx, v);
    this.px = px;
    this.pick();
    this.stage = $("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.scale = 1;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    this.build();
  }

  // The load case (?lc=, else the first in the project's order with a shell analysis) and
  // the versions (?before= / ?after=, else the first and the last with one on that case).
  pick() {
    const s = this.s;
    const cases = (s.project?.load_cases ?? []).map((c) => c.load_case_id);
    const withShell = (lc) => [...s.versions].sort((a, b) => a - b).filter((v) => shellAnalysis(s, lc, v));
    const lc = params.get("lc") ?? cases.find((c) => withShell(c).length) ?? null;
    const versions = lc ? withShell(lc) : [];
    if (!versions.length) throw new Error(`シェルの解析の記録が無い${lc ? ` (${lc})` : ""}`);
    const num = (k) => (params.has(k) ? Number(params.get(k)) : null);
    const vb = num("before") ?? versions[0];
    const va = num("after") ?? versions.at(-1);
    this.lc = (s.project?.load_cases ?? []).find((c) => c.load_case_id === lc) ?? null;
    const before = shellAnalysis(s, lc, vb);
    if (!before) throw new Error(`版 ${vb} のシェルの解析の記録が無い (${lc})`);
    const fb = preferredField(meshFields(s, before), s.meshes);
    const after = va !== vb ? shellAnalysis(s, lc, va) : null;
    if (va !== vb && !after) throw new Error(`版 ${va} のシェルの解析の記録が無い (${lc})`);
    const fa = after ? fieldsOf(after).find((f) => sameField(f, fb) && s.meshes[f.mesh_id]) : null;
    if (after && !fa) throw new Error(`版 ${va} に版 ${vb} と同じ field が無い`);
    const crit = this.lc?.criterion ?? null;
    const [cb, ca] = onSharedScale([contourOf(before, fb, crit, s.meshes), fa ? contourOf(after, fa, crit, s.meshes) : null]);
    this.cases = [
      { version: vb, ana: before, field: fb, ct: cb },
      ...(fa ? [{ version: va, ana: after, field: fa, ct: ca }] : []),
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
    this.focusView = bv.focusView(this.focus, sp.zoom);
    // the colour front runs along x (ISO) from the front of the mesh to its rear
    const P = first.ct.mesh.rec.nodes_mm;
    let lo = Infinity;
    let hi = -Infinity;
    for (const p of P) {
      lo = Math.min(lo, p[0]);
      hi = Math.max(hi, p[0]);
    }
    this.front = { axis: 0, from: hi, to: lo };
    this.sweepK = null;
    this.buildLegend();
    this.buildLabel();
  }

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
    const tp = this.px(L.title_px);
    const xp = this.px(L.text_px);
    const where = f.surface ? `シェル・${SURFACE_JA[f.surface] ?? f.surface}` : "シェル";
    const top = ct.limitKnown ? `許容 ${fmt(ct.max, 4)}` : fmt(ct.max, 3);
    const rows = this.cases
      .map((c) => `<tr data-version="${c.version}"><td>版 ${c.version}</td><td>最大 ${esc(fmtVU(c.ct.fieldMax, f.unit))}</td></tr>`)
      .join("");
    el.innerHTML =
      `<h3 style="font-size:${tp}px">${esc(FIELD_JA[f.kind] ?? f.kind)} [${esc(unit || "1")}]</h3>` +
      `<p style="font-size:${xp}px">${esc(where)}</p>` +
      `<i class="bar" style="background:linear-gradient(90deg, ${BAND_COLORS.join(",")})"></i>` +
      `<div class="ends" style="font-size:${xp}px"><span>${esc(fmt(ct.min, 3))}</span><span>${esc(top)}</span></div>` +
      (ct.limitKnown ? `<p style="font-size:${xp}px"><i class="sw" style="background:${OVER_COLOR}"></i> 許容を超えた所</p>` : "") +
      `<table style="font-size:${xp}px">${rows}</table>` +
      `<p class="src" style="font-size:${this.minPx}px">色の目盛りは ${this.cases.length} つの版で同じ</p>`;
    this.stage.append(el);
    this.legend = el;
  }

  buildLabel() {
    const sp = this.spec;
    const el = document.createElement("div");
    el.className = "n07-label";
    el.dataset.box = "n07.label";
    // the layout gives no width: the words wrap before the stage's right margin, as wide as the view's left one
    const maxW = this.layout.stage.w - sp.view.x - sp.label.x;
    Object.assign(el.style, { left: `${sp.label.x}px`, top: `${sp.label.y}px`, maxWidth: `${maxW}px`, fontSize: `${this.px(sp.label.size_px)}px` });
    this.stage.append(el);
    this.label = el;
    this.labelFor = null;
  }

  // The words on the label: the version shown, with its recorded reason for the change.
  setLabel(c) {
    if (this.labelFor === c) return;
    this.labelFor = c;
    const why = c.version !== this.cases[0].version ? this.s.designs[c.version]?.change_reason : null;
    const b = document.createElement("b");
    b.textContent = `版 ${c.version}`;
    this.label.replaceChildren(b, why ? ` ${why}` : "");
    const r = this.label.getBoundingClientRect();
    const k = r.height / (this.label.offsetHeight || 1) || 1;
    const box = { x: this.spec.label.x, y: this.spec.label.y, w: r.width / k, h: r.height / k };
    if (!clearOfBand(box, this.layout)) this.label.dataset.outside = "1";
    else delete this.label.dataset.outside;
    for (const tr of this.legend.querySelectorAll("tr")) tr.classList.toggle("on", Number(tr.dataset.version) === c.version);
  }

  resize(scale) {
    this.scale = scale;
    this.renderer.setSize(Math.ceil(this.inner.w * scale), Math.ceil(this.inner.h * scale), false);
    for (const c of this.cases) {
      c.el.width = Math.round(this.inner.w * scale);
      c.el.height = Math.round(this.inner.h * scale);
    }
  }

  // The camera at time t: on the turn, then on the way to the largest element.
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
    const k = p.sweep;
    if (k !== this.sweepK) {
      b.view.sweepShell({ ...this.front, k });
      this.sweepK = k;
    }
    const cam = this.cameraAt(p);
    b.view.lookFrom(cam);
    const mix = a ? p.mix : 0;
    b.el.style.opacity = "1";
    if (mix < 1) b.view.renderInto(b.el.getContext("2d"), b.el.width, b.el.height, this.scale);
    if (a) {
      a.el.style.opacity = String(mix);
      a.view.copyCamera(b.view);
      if (mix > 0) a.view.renderInto(a.el.getContext("2d"), a.el.width, a.el.height, this.scale);
    }
    this.setLabel(a && mix >= 0.5 ? a : b);
    // the mark: a ring round the largest element (of the version before) at the zoom's end
    const at = isoToThree(this.focus.center.toArray()).project(b.view.camera);
    const d = this.spec.zoom * Math.min(this.inner.w, this.inner.h);
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

// N07: the contour of a stress analysis on the frame's plate mesh, the version before the
// reinforcement and the version after it side by side in the view's box (left and right, on
// one colour scale, through one camera). The vehicle turns slowly, the camera comes to the
// version before's largest element, and it stays there. What the version after added or
// changed is boxed in the highlight's colour (DIFF_COLOR), found from the two design records
// (designDiff: parts and members by id; nothing is drawn that the design has no shape for).
// The viewers do not know CAE: the words on the page are only "補強の前" / "補強の後" (with
// "足した部品 N・変えた部品 M" under the second), and a legend of two things, "応力 [MPa]"
// and "許容 <the load case's criterion limit>" (no names of analyses or of stress measures).
// The tags are large (label.of_view_h), and from the swap's middle the version after's says
// in green "✓ OK" when the records judge that version a pass on the load case (its verdicts,
// else the domain's judgement of the version); otherwise nothing is added. Positions,
// sizes, colours, the length and the times come from layout.json n07 (label.x / label.y
// from the view's top left are kept on each side); a missing key is named on the page.
// cut.html?cut=n07&run=<id>&t=<s>[&lc=<load case>][&before=<version>&after=<version>]
//
// The colours: each element's shell stress (the surface "max" field, from the record), on
// bands whose top is the allowable value. Elements above it keep the colour past the top
// (also in the version after, e.g. an inside corner), with no words added.
//
// The weak spot: a ring round the element with the largest recorded value of the version
// before (its contour's maxAt), at the same place on both sides, no number or word with it.
// It is shown from lead_s to the end: small while the vehicle turns, growing with the zoom,
// held at zoom.fill of the view's height from zoom_to_s (so the eye can follow the weak
// spot), and on the version after green once it says "✓ OK". It carries no data-kind (it
// marks the picture, as the colours do).
//
// times: lead_s..turn_to_s the turn (turn_deg in all); zoom_s..zoom_to_s the camera
// comes to the largest element (its neighbourhood then takes zoom.fill of the view's
// height); from the middle of swap_s..swap_to_s the version after says "✓ OK" (when judged
// a pass) and its ring is green. Before lead_s and after each step the picture holds still.
// On the way in the camera's direction goes round by its azimuth (about the vertical) and its
// elevation, each evenly (the camera keeps its up, so the picture turns by the azimuth's
// change even where the camera looks steeply down), and by no more than the zoom's time allows
// at 90 deg/s (with the smooth ease the fastest moment is pi/2 times the mean): when the
// element is seen from farther round than that, the camera ends the zoom that much short of
// the element's own direction, on the side it comes from. Its end is at most
// ZOOM_MAX_ELEV_DEG above the ground's plane.

import * as THREE from "three";
import { BAND_COLORS, BANDS } from "./colormap.js";
import { clearOfBand } from "./compose.js";
import { FIELD_JA, contourOf, fieldsOf, onSharedScale, preferredField, sameField } from "./fields.js";
import { OK_WORD, esc, fmt, unitLabel } from "./labels.js";
import { OVERLAY, VIEWS, cutView, isoToThree } from "./workbench3d.js";
import { storyOf } from "./cut_n03.js";
import { PACE_LIMIT_DEG_S } from "./pace.js";

const ZOOM_MAX_ELEV_DEG = 70;

// A direction (three.js, y up) no steeper than elev degrees, keeping its side; one straight up
// or down takes the side of `like`.
function notSteeper(d, elev, like) {
  const lim = Math.sin(THREE.MathUtils.degToRad(elev));
  if (Math.abs(d.y) <= lim) return d.clone();
  let h = new THREE.Vector3(d.x, 0, d.z);
  if (h.lengthSq() < 1e-8) h = new THREE.Vector3(like.x, 0, like.z);
  if (h.lengthSq() < 1e-8) h = new THREE.Vector3(1, 0, 0);
  h.normalize().multiplyScalar(Math.cos(THREE.MathUtils.degToRad(elev)));
  return new THREE.Vector3(h.x, Math.sign(d.y) * lim, h.z).normalize();
}

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const BORDER_PX = 2; // the view's rule line
const GREEN = "#2f7d4f"; // the pages' colour of a pass (as .ok in cut.html)
const STATE_JA = ["補強の前", "補強の後"];
const SIDE_GAP_PX = 16; // between the two sides in the view's box
// The highlight of what the version after added or changed: apart from the contour's bands
// (blue to red), the dark red past the top and the ring's red and green.
const DIFF_COLOR = "#d01fd0";

const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// What design b added, changed and took away against design a (both design records): parts
// and members by id. A part changed when any of its values differ; a member when its ends'
// nodes, their places, its section (or that section's values) or its material differ.
export function designDiff(a, b) {
  const byId = (list) => new Map((list ?? []).map((x) => [x.id, x]));
  const out = { parts: { added: [], changed: [], removed: [] }, members: { added: [], changed: [], removed: [] } };
  const pa = byId(a?.parts);
  const pb = byId(b?.parts);
  for (const [id, p] of pb) {
    if (!pa.has(id)) out.parts.added.push(id);
    else if (!sameJson(pa.get(id), p)) out.parts.changed.push(id);
  }
  for (const id of pa.keys()) if (!pb.has(id)) out.parts.removed.push(id);
  const node = (d, id) => (d?.nodes ?? []).find((n) => n.id === id)?.xyz_mm ?? null;
  const memberKey = (d, m) => [node(d, m.node_a), node(d, m.node_b), m.section, d?.sections?.[m.section] ?? null, m.material];
  const ma = byId(a?.members);
  const mb = byId(b?.members);
  for (const [id, m] of mb) {
    if (!ma.has(id)) out.members.added.push(id);
    else if (!sameJson(memberKey(a, ma.get(id)), memberKey(b, m))) out.members.changed.push(id);
  }
  for (const id of ma.keys()) if (!mb.has(id)) out.members.removed.push(id);
  return out;
}

// The diff in words for the label: what was added and changed, with counts ("" for none).
export function diffWords(diff) {
  if (!diff) return "";
  const added = diff.parts.added.length + diff.members.added.length;
  const changed = diff.parts.changed.length + diff.members.changed.length;
  return [added ? `足した部品 ${added}` : "", changed ? `変えた部品 ${changed}` : ""].filter(Boolean).join("・");
}

const N07_KEYS = [
  "duration_s",
  "view.x", "view.y", "view.w", "view.h",
  "legend.x", "legend.y", "legend.w", "legend.h", "legend.title_px", "legend.text_px",
  "label.x", "label.y", "label.size_px", "label.of_view_h",
  "ring.of_view_h",
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
    // the element stress (the "max" surface first, preferredField) on the allowable's band
    const [cb, ca] = onSharedScale([contourOf(before, fb, crit, s.meshes), fa ? contourOf(after, fa, crit, s.meshes) : null]);
    this.cases = [
      { version: vb, name: STATE_JA[0], ana: before, field: fb, ct: cb },
      ...(fa ? [{ version: va, name: STATE_JA[1], ana: after, field: fa, ct: ca, fixed: this.passes(va) }] : []),
    ];
  }

  // Does the record judge version v a pass on the cut's load case? Its verdicts on that load
  // case for an analysis of v (all pass), else the judgement of the load case's domain for v
  // (green); false when the records say neither.
  passes(v) {
    const s = this.s;
    const lc = this.lc;
    if (!lc) return false;
    const vs = Object.values(s.verdicts).filter((x) => {
      if (x.load_case_id !== lc.load_case_id) return false;
      const cmp = s.comparisons[x.comparison_id];
      return s.analyses[cmp?.analysis_id]?.design_version === v;
    });
    if (vs.length) return vs.every((x) => x.pass === true);
    return s.judgements[`${v}|${lc.domain}`]?.state === "green";
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
    this.stage.append(box);
    // the view's box shared by the versions side by side (one alone fills it)
    const n = this.cases.length;
    const w = Math.floor((V.w - (n - 1) * SIDE_GAP_PX) / n);
    this.inner = { w: w - 2 * BORDER_PX, h: V.h - 2 * BORDER_PX };
    const rgba = (hex) => `rgba(${new THREE.Color(hex).toArray().map((x) => Math.round(x * 255)).join(", ")}, ${sp.overlay.opacity})`;
    this.rim = rgba(sp.overlay.color);
    this.greenRim = rgba(GREEN);
    // what the second version added or changed, from the two designs (designDiff)
    this.diff = n > 1 ? designDiff(this.s.designs[this.cases[0].version], this.s.designs[this.cases[1].version]) : null;
    this.cases.forEach((c, i) => {
      const side = document.createElement("div");
      side.className = "n07-side";
      side.dataset.box = `n07.view.${i ? "after" : "before"}`;
      Object.assign(side.style, { left: `${i * (w + SIDE_GAP_PX)}px`, top: "0px", width: `${w}px`, height: `${V.h}px`, background: sp.colors.panel, border: `${BORDER_PX}px solid ${sp.colors.rule}` });
      box.append(side);
      c.side = side;
      c.el = document.createElement("canvas");
      c.el.dataset.kind = "moving";
      side.append(c.el);
      // the mark round the largest element once the camera is there: a ring in the overlay's
      // colour, its fill clear in the middle (the colours it points at stay as they are) and
      // the overlay's opacity only toward the rim
      c.mark = document.createElement("div");
      c.mark.className = "n07-mark";
      Object.assign(c.mark.style, { borderColor: sp.overlay.color, background: `radial-gradient(circle, transparent 60%, ${this.rim} 100%)`, opacity: "0" });
      side.append(c.mark);
      const v = cutView(this, "n07");
      v.setDesign(this.s.designs[c.version] ?? null);
      v.setLoading(this.lc?.params?.loading ?? null);
      v.setScene(this.lc, c.ana);
      v.setContour(c.ct);
      v.setVectors(null);
      v.setToggles({ parts: false, contour: true, arrows: false, cg: false, names: false, limit: false });
      v.resize(this.inner.w, this.inner.h);
      c.view = v;
      if (i === 1 && this.diff) c.marks = this.diffMarks(v, this.s.designs[c.version], this.diff);
    });
    this.mark = this.cases[0].mark;
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

  // A tall legend of two things: the stress's name and unit, and the colour bar upright
  // with the allowable value at its top (the bands' top).
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
    // the allowable value is the load case's criterion limit (in the field's unit)
    const lim = this.lc?.criterion?.limit;
    const allowable = lim && lim.unit === f.unit && Number.isFinite(lim.value) ? lim.value : null;
    // the bands, the allowable at the top (without an allowable: the top value)
    const bar = `linear-gradient(0deg, ${BAND_COLORS.map((c, i) => `${c} ${(i * 100) / BANDS}% ${((i + 1) * 100) / BANDS}%`).join(",")})`;
    const tick = allowable !== null ? `許容 ${fmt(allowable, 4)}` : fmt(ct.max, 3);
    el.innerHTML =
      `<h3 style="font-size:${tp}px">${esc(FIELD_JA[f.kind] ?? f.kind)} [${esc(unit || "1")}]</h3>` +
      `<div class="scale" data-kind="legend" style="font-size:${xp}px"><i class="bar" style="background:${bar}"></i>` +
      `<div class="ticks"><span class="allowable" style="top:0%">${esc(tick)}</span></div></div>`;
    this.stage.append(el);
    this.legend = el;
  }

  // One label per side, at label.x / label.y from its side's top left (as from the view's):
  // the version's name, and on the version after "✓ OK" once it is shown judged a pass
  // (setFixed) and, under it, what it added or changed (the highlight's colour, counts from
  // designDiff).
  buildLabel() {
    const sp = this.spec;
    const px = Math.max(this.px(sp.label.size_px), this.story.bodyPx, Math.round(sp.view.h * sp.label.of_view_h));
    // (inside the side's border, which its children are placed from)
    const dx = sp.label.x - sp.view.x - BORDER_PX;
    const dy = sp.label.y - sp.view.y - BORDER_PX;
    this.cases.forEach((c, i) => {
      const el = document.createElement("div");
      el.className = "n07-label";
      el.dataset.box = `n07.label.${i ? "after" : "before"}`;
      el.dataset.kind = "label";
      Object.assign(el.style, { left: `${dx}px`, top: `${dy}px`, fontSize: `${px}px` });
      el.innerHTML = `<span class="name">${esc(c.name)}</span>`;
      const words = i === 1 ? diffWords(this.diff) : "";
      if (words) el.insertAdjacentHTML("beforeend", `<small class="diff" style="font-size:${Math.max(this.story.bodyPx, this.px(sp.legend.text_px))}px;color:${DIFF_COLOR}"><i style="border-color:${DIFF_COLOR}"></i>${esc(words)}</small>`);
      c.side.append(el);
      c.label = el;
    });
    this.label = this.cases.at(-1).label;
    this.labelFor = null;
  }

  // "✓ OK" on the version after when the record judges it a pass, shown from the swap's middle.
  setFixed(on) {
    if (this.labelFor === on) return;
    this.labelFor = on;
    const a = this.cases[1];
    if (!a) return;
    a.label.querySelector(".fixed")?.remove();
    if (on) a.label.querySelector(".name").insertAdjacentHTML("afterend", ` <span class="fixed" style="color:${GREEN}">${OK_WORD}</span>`);
    a.label.dataset.fixed = on ? "1" : "0";
  }

  // Boxes round what the version after added or changed (the diff's ids), drawn from that
  // design's own data: a part by its box, a member by its drawn tube; an id with no shape in
  // the design gets none. In the highlight's colour, an outline seen through the plates only
  // (no fill: the plates' colours stay as the legend reads them).
  diffMarks(v, design, diff) {
    const group = new THREE.Group();
    const line = new THREE.LineBasicMaterial({ color: DIFF_COLOR, depthTest: false, transparent: true });
    const add = (geo, matrix) => {
      for (const obj of [new THREE.LineSegments(new THREE.EdgesGeometry(geo), line)]) {
        obj.matrixAutoUpdate = false;
        obj.matrix.copy(matrix);
        obj.renderOrder = 10;
        // in the overlay's pass, after the contour's plates (workbench3d renderOverlay)
        obj.layers.set(OVERLAY);
        group.add(obj);
      }
    };
    const ids = [...diff.parts.added, ...diff.parts.changed];
    for (const id of ids) {
      const p = (design.parts ?? []).find((x) => x.id === id);
      if (!p?.box) continue;
      const pad = Math.max(...p.box.size_mm) * 0.08;
      add(new THREE.BoxGeometry(...p.box.size_mm.map((x) => x + 2 * pad)), new THREE.Matrix4().makeTranslation(...p.box.center_mm));
    }
    for (const id of [...diff.members.added, ...diff.members.changed]) {
      const m = v.members[id];
      if (!m) continue;
      const k = Math.max(m.width, m.height) * 1.6;
      add(new THREE.BoxGeometry(m.length, k, k), m.mesh.matrix);
    }
    v.body.add(group);
    v.diffMarks = group; // laid over everything, as the view's centre-of-gravity mark
    return group;
  }

  resize(scale) {
    this.scale = scale;
    this.renderer.setSize(Math.ceil(this.inner.w * scale), Math.ceil(this.inner.h * scale), false);
    for (const c of this.cases) {
      c.el.width = Math.round(this.inner.w * scale);
      c.el.height = Math.round(this.inner.h * scale);
    }
  }

  // The camera's direction at share u of the zoom: from a toward b by azimuth and elevation
  // (the shorter way round), both brought back toward a's so the whole turn takes no more
  // than the zoom's time allows.
  zoomDir(a, bIn, u) {
    const T = this.spec.times;
    const b = notSteeper(bIn, ZOOM_MAX_ELEV_DEG, a);
    const most = THREE.MathUtils.degToRad((PACE_LIMIT_DEG_S * (T.zoom_to_s - T.zoom_s) * 2) / Math.PI);
    const sph = (d) => ({ az: Math.atan2(d.x, d.z), el: Math.asin(Math.max(-1, Math.min(1, d.y))) });
    const A = sph(a);
    const B = sph(b);
    let dAz = B.az - A.az;
    dAz = Math.atan2(Math.sin(dAz), Math.cos(dAz)); // the shorter way round
    let dEl = B.el - A.el;
    const turn = Math.hypot(dAz, dEl);
    if (turn > most) {
      dAz *= most / turn;
      dEl *= most / turn;
    }
    const az = A.az + dAz * u;
    const el = A.el + dEl * u;
    return new THREE.Vector3(Math.cos(el) * Math.sin(az), Math.sin(el), Math.cos(el) * Math.cos(az));
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
    const dir = this.zoomDir(off.clone().normalize(), fOff.clone().normalize(), u);
    const dist = Math.exp(Math.log(off.length()) * (1 - u) + Math.log(fOff.length()) * u);
    return { target, position: target.clone().addScaledVector(dir, dist), near: o.near + (f.near - o.near) * u, far: o.far + (f.far - o.far) * u };
  }

  draw(t) {
    const p = n07Progress(this.spec, t);
    this.now = { t, ...p };
    const [b, a] = this.cases;
    // the two sides through one camera: the turn, then the way to the largest element
    b.view.lookFrom(this.cameraAt(p));
    // close in, the wheels are left out: the near tyre would stand between the camera and
    // the joint (it is seen through the spokes otherwise)
    const close = p.zoom >= 0.5;
    for (const c of this.cases) c.view.wheels.visible = !close;
    this.mark.dataset.wheels = close ? "hidden" : "shown";
    b.view.renderInto(b.el.getContext("2d"), b.el.width, b.el.height, this.scale);
    if (a) {
      a.view.copyCamera(b.view);
      a.view.renderInto(a.el.getContext("2d"), a.el.width, a.el.height, this.scale);
    }
    // from the swap's middle the version after says "✓ OK" when the record judges it a pass
    const fixed = !!(a && a.fixed && p.mix >= 0.5);
    this.setFixed(fixed);
    // the ring round the largest element (of the version before), at the same place on both
    // sides: from lead_s a small one (ring.of_view_h of the view's height) shows where the
    // weak spot is while the vehicle turns, it grows with the zoom to zoom.fill of the view's
    // height; on the version after it turns green with its "✓ OK"
    const at = isoToThree(this.focus.center.toArray()).project(b.view.camera);
    const small = this.spec.ring.of_view_h;
    const d = (small + (this.spec.zoom.fill - small) * p.zoom) * this.inner.h;
    this.cases.forEach((c, i) => {
      const green = i === 1 && fixed;
      Object.assign(c.mark.style, {
        left: `${((at.x + 1) / 2) * this.inner.w - d / 2}px`,
        top: `${((1 - at.y) / 2) * this.inner.h - d / 2}px`,
        width: `${d}px`,
        height: `${d}px`,
        borderColor: green ? GREEN : this.spec.overlay.color,
        background: `radial-gradient(circle, transparent 60%, ${green ? this.greenRim : this.rim} 100%)`,
        opacity: t >= this.spec.times.lead_s ? "1" : "0",
      });
      c.mark.dataset.tone = green ? "green" : "red";
    });
    document.documentElement.dataset.n07Version = this.cases.map((c) => c.version).join(" ");
  }
}

// A design split into modules on the 3D (the workbench, A7): two switches beside the others.
//   モジュール       parts and members in their module's colour (proposals.js moduleColor), and a
//                    legend with each module's name (the server's /api/modules: the contract's
//                    MODULE_NAMES_JA) and how many parts and members it has
//   モジュールの空間  each module's space (design.modules[].envelope: boxes) see-through in its
//                    colour; a part or member not inside its module's space is red, and the
//                    legend says how many
// Neither is offered for a design without modules (outsideOf, moduleCounts return nothing).
//
// Inside a space: as the analysis department's check (hiraku/solvers/k4_geometry/joints.py
// outside_envelopes at 7fc0ffc): the axis-aligned box round the thing (a part's box turned by its
// rpy_deg; a member from end to end, grown by half its section's larger side) must be inside one
// of the module's boxes (not their union), to ENVELOPE_TOL_MM. package has no space.
//
// The colours are drawn on the bench's own meshes as unlit copies (children of each mesh, so they
// move with it); nothing of the design or of the bench's materials is changed.

import * as THREE from "three";
import { MODULE_JA, esc, fmt, moduleName } from "./labels.js";
import { OUTSIDE_COLOR, moduleColor } from "./proposals.js";

export const ENVELOPE_TOL_MM = 0.5; // as k4_geometry.joints.ENVELOPE_TOL_MM

// The module names from the server (the contract's), once. Without the route: the ids as written.
let namesLoaded = null;
export function loadModuleNames() {
  namesLoaded ??= fetch("/api/modules", { cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((info) => {
      if (info?.names) Object.assign(MODULE_JA, info.names);
      return info;
    })
    .catch(() => null);
  return namesLoaded;
}

export const hasModules = (design) => Array.isArray(design?.modules) && design.modules.length > 0;

function rotation(rpy) {
  const [r, p, y] = (rpy ?? [0, 0, 0]).map((a) => (a * Math.PI) / 180);
  const [cr, sr, cp, sp, cy, sy] = [Math.cos(r), Math.sin(r), Math.cos(p), Math.sin(p), Math.cos(y), Math.sin(y)];
  return [
    [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
    [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
    [-sp, cp * sr, cp * cr],
  ];
}

// [lo, hi] (mm) of a part's turned box.
export function partExtent(p) {
  const c = p.box.center_mm;
  const s = p.box.size_mm;
  const R = rotation(p.box.rpy_deg);
  const half = [0, 1, 2].map((i) => R[i].reduce((a, rij, j) => a + (Math.abs(rij) * s[j]) / 2, 0));
  return [c.map((v, i) => v - half[i]), c.map((v, i) => v + half[i])];
}

// [lo, hi] (mm) of a member: its ends grown by half its section's larger side.
export function memberExtent(design, m, nodes) {
  const a = nodes[m.node_a];
  const b = nodes[m.node_b];
  if (!a || !b) return null;
  const d = design.sections?.[m.section]?.dims_mm ?? {};
  const r = Math.max(d.b ?? 0, d.h ?? 0, d.d ?? 0, d.D ?? 0, d.w ?? 0) / 2;
  return [a.map((v, i) => Math.min(v, b[i]) - r), a.map((v, i) => Math.max(v, b[i]) + r)];
}

const inBox = ([lo, hi], box) => [0, 1, 2].every((i) => lo[i] >= box.center_mm[i] - box.size_mm[i] / 2 - ENVELOPE_TOL_MM && hi[i] <= box.center_mm[i] + box.size_mm[i] / 2 + ENVELOPE_TOL_MM);

// How far (mm) the extent is out of the nearest box (0 when inside one).
function outBy([lo, hi], boxes) {
  let best = Infinity;
  for (const b of boxes) {
    let worst = 0;
    for (let i = 0; i < 3; i += 1) worst = Math.max(worst, b.center_mm[i] - b.size_mm[i] / 2 - lo[i], hi[i] - (b.center_mm[i] + b.size_mm[i] / 2));
    best = Math.min(best, worst);
  }
  return best;
}

// The parts and members outside their module's space: [{ kind: "part"|"member", id, module, by_mm }],
// farthest first. [] for a design without modules.
export function outsideOf(design) {
  if (!hasModules(design)) return [];
  const env = Object.fromEntries(design.modules.map((m) => [m.module_id, m.envelope ?? []]));
  const nodes = Object.fromEntries((design.nodes ?? []).map((n) => [n.id, n.xyz_mm]));
  const out = [];
  const check = (kind, id, mod, ext) => {
    if (!mod || mod === "package" || !ext) return;
    const boxes = env[mod] ?? [];
    if (boxes.some((b) => inBox(ext, b))) return;
    out.push({ kind, id, module: mod, by_mm: boxes.length ? outBy(ext, boxes) : null });
  };
  for (const p of design.parts ?? []) check("part", p.id, p.module, partExtent(p));
  for (const m of design.members ?? []) check("member", m.id, m.module, memberExtent(design, m, nodes));
  return out.sort((a, b) => (b.by_mm ?? Infinity) - (a.by_mm ?? Infinity));
}

// Each module of the design (design.modules' order) with its parts and members counted.
export function moduleCounts(design) {
  if (!hasModules(design)) return [];
  return design.modules.map((m) => ({
    module: m.module_id,
    owner: m.owner,
    parts: (design.parts ?? []).filter((p) => p.module === m.module_id).length,
    members: (design.members ?? []).filter((x) => x.module === m.module_id).length,
    boxes: (m.envelope ?? []).length,
  }));
}

const partName = (design, id) => {
  const p = (design.parts ?? []).find((x) => x.id === id);
  return p ? String(p.name ?? id).replace(/\s*[（(].*$/, "") || id : id;
};

// The legend (html) for the switches that are on.
export function moduleLegendHtml(design, { colors, spaces }) {
  if (!hasModules(design) || !(colors || spaces)) return "";
  const rows = moduleCounts(design)
    .filter((c) => c.parts || c.members || (spaces && c.boxes))
    .map((c) => {
      const counts = [c.parts ? `部品 ${c.parts}` : "", c.members ? `部材 ${c.members}` : "", spaces && c.boxes ? `空間 ${c.boxes}` : ""].filter(Boolean).join("・");
      return `<li data-module="${esc(c.module)}" data-parts="${c.parts}" data-members="${c.members}" data-boxes="${c.boxes}"><span class="mdot" style="--mod:${moduleColor(c.module)}"></span><span class="mn">${esc(moduleName(c.module))}</span><span class="mc">${esc(counts)}</span></li>`;
    })
    .join("");
  let out = "";
  if (spaces) {
    const outs = outsideOf(design);
    const names = outs.slice(0, 3).map((o) => `${o.kind === "part" ? partName(design, o.id) : o.id}${o.by_mm != null ? ` ${fmt(o.by_mm, 3)} mm` : ""}`);
    out = outs.length
      ? `<div class="mout" data-outside="${outs.length}"><span class="mdot" style="--mod:${OUTSIDE_COLOR}"></span>はみ出し ${outs.length} 個: ${esc(names.join("・"))}${outs.length > 3 ? " ほか" : ""}</div>`
      : `<div class="mout none" data-outside="0">はみ出し 0 個 (どれも自分のモジュールの空間に収まっている)</div>`;
  }
  return `<div class="ttl">${colors ? "モジュールの色" : "モジュールの空間"}</div><ul class="mlist">${rows}</ul>${out}`;
}

// The colours and spaces on a Workbench3D (bench: its design, partObjs, members, wheelObjs, body).
export class ModuleLayer {
  constructor(bench) {
    this.bench = bench;
    this.key = "";
    this.added = [];
    this.spaces = new THREE.Group();
    this.spaces.name = "module-spaces";
    this.mats = new Map();
  }

  mat(color, opts = {}) {
    const k = `${color}|${JSON.stringify(opts)}`;
    if (!this.mats.has(k)) this.mats.set(k, new THREE.MeshBasicMaterial({ color, toneMapped: false, fog: false, polygonOffset: true, polygonOffsetFactor: -1, polygonOffsetUnits: -2, ...opts }));
    return this.mats.get(k);
  }

  clear() {
    for (const o of this.added) o.parent?.remove(o);
    this.added = [];
    for (const o of [...this.spaces.children]) {
      this.spaces.remove(o);
      o.geometry?.dispose();
    }
    this.spaces.parent?.remove(this.spaces);
  }

  // A coloured copy of every solid mesh under obj (a child of each mesh).
  cover(obj, color) {
    const meshes = [];
    obj.traverse((x) => {
      // (not the contour's unlit twins, which live in the overlay's layer only)
      if (x.isMesh && !x.userData.moduleCover && x.layers.isEnabled(0) && x.material) meshes.push(x);
    });
    for (const x of meshes) {
      // a see-through part (the diagram look's boxes) stays see-through, in its module's colour
      const see = x.material.transparent ? { transparent: true, opacity: 0.6, depthWrite: false } : {};
      const c = new THREE.Mesh(x.geometry, this.mat(color, { side: x.material.side ?? THREE.FrontSide, ...see }));
      c.userData.moduleCover = true;
      c.renderOrder = 3;
      x.add(c);
      this.added.push(c);
    }
  }

  // on: { colors, spaces } (the switches). Drawn again only when the design or the switches change.
  set(on) {
    const b = this.bench;
    const d = b.design;
    const colors = !!on.colors && hasModules(d);
    const spaces = !!on.spaces && hasModules(d);
    const key = `${d?.design_version}|${d?.name}|${colors}|${spaces}|${Object.keys(b.partObjs ?? {}).length}|${b.loading}`;
    if (key === this.key && this.design === d) return;
    this.key = key;
    this.design = d;
    this.clear();
    if (!colors && !spaces) return;
    const out = spaces ? new Set(outsideOf(d).map((o) => `${o.kind}:${o.id}`)) : new Set();
    const wheelOf = (p) => {
      const c = p.box.center_mm;
      return (d.supports ?? []).find((s) => Math.hypot(s.position_mm[0] - c[0], s.position_mm[1] - c[1], s.position_mm[2] - c[2]) < 50);
    };
    for (const p of d.parts ?? []) {
      const red = out.has(`part:${p.id}`);
      if (!colors && !red) continue;
      const color = red ? OUTSIDE_COLOR : moduleColor(p.module);
      const o = b.partObjs?.[p.id];
      if (o?.onBoard === false && !red) continue; // off board for the load case: left faint
      if (o) this.cover(o.group, color);
      else if (p.kind === "wheel") {
        const s = wheelOf(p);
        const w = s ? b.wheelObjs?.[s.id] : null;
        if (w) for (const x of w.group.children) if (x !== w.ring && x.isMesh) this.cover(x, color);
      }
    }
    for (const m of d.members ?? []) {
      const red = out.has(`member:${m.id}`);
      if (!colors && !red) continue;
      const info = b.members?.[m.id];
      if (info) this.cover(info.mesh, red ? OUTSIDE_COLOR : moduleColor(m.module));
    }
    if (spaces) {
      for (const mod of d.modules) {
        for (const box of mod.envelope ?? []) {
          const geo = new THREE.BoxGeometry(...box.size_mm);
          const fill = new THREE.Mesh(geo, this.mat(moduleColor(mod.module_id), { transparent: true, opacity: 0.1, depthWrite: false, polygonOffset: false, side: THREE.DoubleSide }));
          fill.position.set(...box.center_mm);
          fill.renderOrder = 4;
          fill.userData.module = mod.module_id;
          const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), new THREE.LineBasicMaterial({ color: moduleColor(mod.module_id), transparent: true, opacity: 0.8, toneMapped: false }));
          edges.position.copy(fill.position);
          edges.userData.module = mod.module_id;
          this.spaces.add(fill, edges);
        }
      }
      b.body.add(this.spaces);
    }
  }

  // What is drawn now, for the tests: covers by colour, and the space boxes.
  shown() {
    const covers = {};
    for (const c of this.added) {
      const hex = "#" + c.material.color.getHexString();
      covers[hex] = (covers[hex] ?? 0) + 1;
    }
    return { covers, boxes: this.spaces.children.filter((o) => o.isMesh).length, onBench: !!this.spaces.parent };
  }
}

// The legend's look (its own, so the app's css is not touched).
const STYLE = `
.modlegend { position: absolute; right: 14px; bottom: 76px; z-index: 5; background: rgba(251, 249, 244, 0.95); border: 1.5px solid var(--line, #c9c3b6); border-radius: 12px; padding: 8px 12px; font-size: 14px; max-width: 330px; }
.modlegend .ttl { font-weight: 700; font-size: 15px; margin-bottom: 4px; }
.modlegend .mlist { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
.modlegend .mlist li { display: grid; grid-template-columns: 14px auto 1fr; gap: 6px; align-items: center; white-space: nowrap; }
.modlegend .mc { color: var(--muted, #6b665c); font-size: 13px; text-align: right; }
.modlegend .mout { margin-top: 6px; color: #b3170f; font-weight: 700; font-size: 13.5px; line-height: 1.35; }
.modlegend .mout.none { color: var(--muted, #6b665c); font-weight: 400; }
.mdot { display: inline-block; width: 12px; height: 12px; border-radius: 3px; background: var(--mod); flex: none; vertical-align: -1px; margin-right: 4px; }
.roundcard { position: absolute; left: 16px; top: 64px; z-index: 6; max-width: min(560px, calc(100% - 400px)); background: rgba(251, 249, 244, 0.95); border: 2px solid #8e6bbf; border-radius: 12px; padding: 8px 12px; font-size: 14.5px; line-height: 1.4; }
.roundcard .rc-head { font-weight: 700; font-size: 15.5px; margin-bottom: 4px; }
.roundcard .rc-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
.roundcard .rc-list li { display: grid; grid-template-columns: 16px 1fr; column-gap: 4px; }
.roundcard .rc-list .mdot { grid-row: span 2; margin-top: 4px; }
.roundcard .pr { color: var(--muted, #6b665c); font-size: 13px; }
.roundcard .rc-foot { margin-top: 4px; color: var(--muted, #6b665c); font-size: 13px; }
.asm-3d .modtoggles { top: 60px; }
.asm-3d .modlegend { bottom: 80px; }
.petbox .modmark { position: absolute; left: 0; bottom: 0; width: 13px; height: 13px; border-radius: 3px; background: var(--mod); border: 1.5px solid #fff; z-index: 1; }
.pet.modpet { box-shadow: 0 0 0 3px var(--mod) inset; }
`;
let styled = false;
export function moduleStyle() {
  if (styled || typeof document === "undefined") return;
  styled = true;
  const el = document.createElement("style");
  el.dataset.modules = "1";
  el.textContent = STYLE;
  document.head.append(el);
}

// The legend element in a host (made once), filled for the design and switches; hidden when empty.
export function renderModuleLegend(host, design, on) {
  moduleStyle();
  let el = host.querySelector(":scope > .modlegend");
  if (!el) {
    el = document.createElement("div");
    el.className = "modlegend";
    el.dataset.kind = "legend";
    host.append(el);
  }
  const html = moduleLegendHtml(design, on);
  el.hidden = !html;
  if (el.innerHTML !== html) el.innerHTML = html;
  return el;
}

// A7 (assembly.js AssemblyPanel): the two switches over its 3D, its own layer and legend.
// update() each frame: the switches only while the view's design has modules.
export function mountAssemblySwitch(panel) {
  moduleStyle();
  const host = panel.q(".asm-3d");
  const bar = document.createElement("div");
  bar.className = "toggles modtoggles";
  host.append(bar);
  const on = { colors: false, spaces: false };
  const layer = new ModuleLayer(panel.view);
  bar.addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-toggle]");
    if (!b) return;
    const k = b.dataset.toggle === "modules" ? "colors" : "spaces";
    on[k] = !on[k];
  });
  let drawn = "";
  return {
    on,
    layer,
    update() {
      const d = panel.view.design;
      const show = hasModules(d);
      const html = show
        ? `<button data-toggle="modules" class="${on.colors ? "on" : ""}">モジュール</button><button data-toggle="spaces" class="${on.spaces ? "on" : ""}">モジュールの空間</button>`
        : "";
      if (html !== drawn) bar.innerHTML = drawn = html;
      bar.hidden = !show;
      layer.set(show ? on : { colors: false, spaces: false });
      renderModuleLegend(host, d, show ? on : {});
    },
  };
}

// The round card (proposals.js roundCardHtml) in a host.
export function renderRoundCard(host, html) {
  moduleStyle();
  let el = host.querySelector(":scope > .roundcard");
  if (!el) {
    el = document.createElement("div");
    el.className = "roundcard";
    el.id = "roundcard";
    host.append(el);
  }
  el.hidden = !html;
  if (el.innerHTML !== html) el.innerHTML = html;
  return el;
}

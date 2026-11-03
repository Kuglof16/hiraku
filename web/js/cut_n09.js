// N09: the maker's request in their own words, and the domains Claude picks from it.
// cut.html?cut=n09&run=<run with domains.picked>&design_run=<run with a design>&t=<s>
//
// Boxes (layout.json n09): request (the run's want), view (the product in 3D), chips (the
// seven domains and assembly, one chip a row, in the fixed order of labels.js DOMAINS).
//
// Which product: layout.json n09._note says only the off-road vehicle, so the cut plays
// the one run named by ?run= (its domains.picked, the newest one) and no other. The run that
// picks the domains holds no design (it stops after the pick), so the vehicle in the view
// is the first design of the run named by ?design_run= (a run of the same product); without
// one the view says it has none.
//
// The chips light one at a time from times.chips_from_s, every times.chip_step_s, in the
// order of the record: the picks as listed, then the domains left off (off), then assembly.
// A pick with tests that apply (test_ids) lights, with the tests in the viewers' words on one
// line under its name (labels.js testPlainName); assembly lights when the record lists
// assembly_test_ids. A pick with no test to apply, or a domain left off, stays pale and
// at its turn shows the record's reason in one line (labels.js plainReason: the sentence
// that says why, without ids). Until its turn a chip is pale with its name only.
//
// Story (layout.json story.cuts.n09): label 7, "the seven domains light up". The names of
// the seven domains are the labels; assembly's chip is the eighth row the chips box asks for,
// but it is not a domain (docs/contract.md: assembly_test_ids are not counted as domains) and
// carries no data-kind, so the labels stay at 7. The reason lines are notes (data-kind note,
// text_px x 0.7, at least min_text_px, as the chips box says).
//
// Positions, sizes, colours, the length and the times come from layout.json n09; a missing
// key is named on the page.

import * as THREE from "three";
import { clearOfBand } from "./compose.js";
import { DOMAINS, DOMAIN_PLAIN_JA, esc, plainReason, testPlainName } from "./labels.js";
import { VIEWS, Workbench3D } from "./workbench3d.js";
import { storyOf } from "./cut_n03.js";
import { applyEvent, initialState } from "./state.js";

const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const BORDER_PX = 2;
const PAD_PX = 16;
const TURN_DEG = 40; // the vehicle turns this much over the cut (slowly, so it reads as one picture)

const N09_KEYS = [
  "duration_s",
  "request.x", "request.y", "request.w", "request.h", "request.title_px", "request.text_px",
  "view.x", "view.y", "view.w", "view.h",
  "chips.x", "chips.y", "chips.w", "chips.h", "chips.text_px", "chips.row_gap",
  "times.lead_s", "times.request_s", "times.chips_from_s", "times.chip_step_s",
  "colors.bg", "colors.panel", "colors.rule",
];
const TOP_KEYS = ["min_text_px", "stage", "subtitle_band"];

const get = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

export function missingN09Keys(layout) {
  return [...N09_KEYS.filter((k) => get(layout.n09, k) === undefined), ...TOP_KEYS.filter((k) => layout[k] === undefined)];
}

// The newest domains.picked of a run's events (in seq order), or null.
function pickedOf(events) {
  return (events ?? []).filter((e) => e.kind === "domains.picked").at(-1)?.data ?? null;
}

// The eight chips in the order they light (see the top): { domain, lit, line, kind }.
// kind: "pick" (tests apply), "none" (picked, no test to apply), "off", "assembly", "absent".
export function chipsOf(picked) {
  const out = [];
  for (const p of picked?.picks ?? []) {
    const ids = p.test_ids ?? [];
    if (ids.length) {
      const names = ids.map(testPlainName).filter(Boolean);
      out.push({ domain: p.domain, lit: true, kind: "pick", line: names.length ? names.join("・") : `当てはまる試験 ${ids.length} つ` });
    } else out.push({ domain: p.domain, lit: false, kind: "none", line: plainReason(p.reason) });
  }
  for (const o of picked?.off ?? []) out.push({ domain: o.domain, lit: false, kind: "off", line: plainReason(o.reason) });
  const asm = picked?.assembly_test_ids ?? [];
  const asmNames = asm.map(testPlainName).filter(Boolean);
  out.push(asm.length ? { domain: "assembly", lit: true, kind: "assembly", line: asmNames.length ? asmNames.join("・") : `当てはまる試験 ${asm.length} つ` } : { domain: "assembly", lit: false, kind: "absent", line: "" });
  // a domain the record names nowhere: pale, no reason (the record gives none)
  for (const d of DOMAINS) if (!out.some((c) => c.domain === d)) out.push({ domain: d, lit: false, kind: "absent", line: "" });
  return out.filter((c) => DOMAINS.includes(c.domain));
}

async function eventsOf(runId) {
  const events = [];
  let offset = 0;
  for (;;) {
    const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/events?offset=${offset}`, { cache: "no-store" });
    const body = await res.json();
    if (!res.ok) throw new Error(body.error ?? res.status);
    events.push(...body.events);
    if (body.next_offset >= body.size || body.next_offset === offset) return events;
    offset = body.next_offset;
  }
}

export class CutN09 {
  constructor(layout, s, events) {
    const missing = missingN09Keys(layout);
    if (missing.length) throw new Error(`layout.json の n09 に無い: ${missing.join(", ")}`);
    this.layout = layout;
    this.s = s;
    this.spec = layout.n09;
    this.story = storyOf(layout, "n09");
    this.picked = pickedOf(events);
    if (!this.picked) throw new Error("分野を選んだ記録 (domains.picked) が無い");
    this.stage = $("stage");
    this.stage.style.background = this.spec.colors.bg;
    this.scale = 1;
    this.build();
    this.loaded = this.loadDesign();
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
    const body = this.story.bodyPx;
    // request: who asks, then their words (as large as the box allows, down to the body size)
    const R = sp.request;
    const req = this.panel("n09-request", "n09.request", R);
    const want = this.picked.want ?? this.s.project?.want ?? "";
    req.innerHTML =
      `<span class="who" style="font-size:${Math.max(R.title_px, body)}px">作り手の依頼</span>` +
      `<p class="want" data-box="n09.request.want" style="font-size:${Math.max(R.text_px, body)}px">${esc(want)}</p>`;
    this.wantEl = req.querySelector(".want");
    const room = R.h - 2 * BORDER_PX - 2 * PAD_PX;
    let px = Math.max(R.text_px, body);
    while (this.wantEl.scrollHeight > room && px > body) this.wantEl.style.fontSize = `${(px -= 1)}px`;

    // view: the product in 3D (its design from ?design_run=)
    const V = sp.view;
    this.viewEl = this.panel("n09-view", "n09.view", V);
    this.inner = { w: V.w - 2 * BORDER_PX, h: V.h - 2 * BORDER_PX };

    // chips: one row each, the domains' order fixed; they light in the record's order
    const C = sp.chips;
    const box = document.createElement("div");
    box.className = "n09-chips";
    box.dataset.box = "n09.chips";
    this.place(box, C);
    this.stage.append(box);
    const rowH = (C.h - (DOMAINS.length - 1) * C.row_gap) / DOMAINS.length;
    const namePx = Math.max(C.text_px, body);
    const linePx = Math.max(Math.round(C.text_px * 0.7), this.layout.min_text_px);
    const T = sp.times;
    this.chips = chipsOf(this.picked).map((c, k) => ({ ...c, at: T.chips_from_s + k * T.chip_step_s }));
    this.rows = {};
    DOMAINS.forEach((d, i) => {
      const c = this.chips.find((x) => x.domain === d);
      const row = document.createElement("div");
      row.className = "n09-chip";
      row.dataset.box = `n09.chip.${d}`;
      row.dataset.domain = d;
      row.dataset.kindOf = c.kind;
      Object.assign(row.style, { top: `${i * (rowH + C.row_gap)}px`, height: `${rowH}px`, borderColor: this.spec.colors.rule, background: this.spec.colors.panel });
      // the seven domains' names are the story's labels; assembly's is not a domain (see the top)
      const kind = d === "assembly" ? "" : ` data-kind="label"`;
      row.innerHTML =
        `<b class="name"${kind} style="font-size:${namePx}px">${esc(DOMAIN_PLAIN_JA[d])}</b>` +
        `<span class="line" data-kind="note" style="font-size:${linePx}px">${esc(c.line)}</span>`;
      box.append(row);
      this.rows[d] = { row, chip: c, line: row.querySelector(".line") };
    });
    this.linePx = linePx;
    this.lineRoom = C.w - 2 * BORDER_PX - 2 * PAD_PX;
  }

  // A reason line in one line: a pixel at a time down to min_text_px; one that still does not
  // fit drops its subject (the tests' names before " は"; the chip names the domain), and only
  // then is it cut short with an ellipsis. Done at each frame (measured once the page's font
  // is in), from the record's words each time, so the frame at t is always the same.
  fitLine({ chip, line }) {
    const shrink = (text) => {
      line.textContent = text;
      let lp = this.linePx;
      line.style.fontSize = `${lp}px`;
      while (line.scrollWidth > this.lineRoom && lp > this.layout.min_text_px) line.style.fontSize = `${(lp -= 1)}px`;
    };
    shrink(chip.line);
    const cut = chip.line.indexOf(" は");
    if (line.scrollWidth > this.lineRoom && !chip.lit && cut > 0) shrink(chip.line.slice(cut + 2).trim());
  }

  async loadDesign() {
    const id = params.get("design_run");
    let design = null;
    if (id) {
      let s = initialState();
      for (const ev of (await eventsOf(id)).sort((a, b) => a.seq - b.seq)) s = applyEvent(s, ev);
      const v = [...s.versions].sort((a, b) => a - b)[0];
      design = v !== undefined ? s.designs[v] : null;
    }
    if (!design) {
      this.viewEl.insertAdjacentHTML("beforeend", `<p class="none" style="font-size:${this.layout.min_text_px}px">この記録に設計が無い</p>`);
      return;
    }
    this.canvas = document.createElement("canvas");
    this.canvas.dataset.kind = "moving";
    this.viewEl.append(this.canvas);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true });
    this.renderer.setPixelRatio(1);
    const v = new Workbench3D(null, "zu", { renderer: this.renderer });
    v.setDesign(design);
    v.setLoading(null);
    v.setScene(null, null);
    v.setToggles({ parts: true, contour: false, arrows: false, cg: false, names: false, limit: false });
    v.resize(this.inner.w, this.inner.h);
    v.fitPoints(v.contentPoints(), VIEWS.default, 1.1);
    this.view = v;
    this.orbit = v.cameraView();
    this.resize(this.scale);
  }

  resize(scale) {
    this.scale = scale;
    if (!this.view) return;
    this.renderer.setSize(Math.ceil(this.inner.w * scale), Math.ceil(this.inner.h * scale), false);
    this.canvas.width = Math.round(this.inner.w * scale);
    this.canvas.height = Math.round(this.inner.h * scale);
  }

  draw(t) {
    const sp = this.spec;
    let lit = 0;
    for (const r of Object.values(this.rows)) {
      const { row, chip, line } = r;
      const on = t >= chip.at;
      // at its turn a chip lights (tests apply) or stays pale with its reason
      row.dataset.state = !on ? "wait" : chip.lit ? "lit" : "pale";
      line.style.visibility = on && chip.line ? "visible" : "hidden";
      if (on && chip.line) this.fitLine(r);
      if (on && chip.lit) lit += 1;
    }
    document.documentElement.dataset.n09Lit = String(lit);
    if (!this.view) return;
    // the vehicle turns slowly from lead_s to the end, about the vertical through its middle
    const T = sp.times;
    const p = Math.max(0, Math.min(1, (t - T.lead_s) / Math.max(1e-9, sp.duration_s - T.lead_s)));
    const o = this.orbit;
    const ang = ((TURN_DEG * (0.5 - 0.5 * Math.cos(Math.PI * p))) * Math.PI) / 180;
    const off = o.position.clone().sub(o.target).applyAxisAngle(new THREE.Vector3(0, 1, 0), ang);
    this.view.lookFrom({ target: o.target.clone(), position: o.target.clone().add(off), near: o.near, far: o.far });
    this.view.renderInto(this.canvas.getContext("2d"), this.canvas.width, this.canvas.height, this.scale);
  }
}

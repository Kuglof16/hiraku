// N09: the maker's request in their own words, and the domains Claude picks from it.
// cut.html?cut=n09&run=<run with domains.picked>&design_run=<run with a design>&t=<s>
//
// Boxes (layout.json n09): request (the run's want), view (the product in 3D), chips (the
// seven domains and assembly, one chip a row, in the fixed order of labels.js DOMAINS).
//
// Which product: layout.json n09._note says only the off-road vehicle, so the cut plays
// the one run named by ?run= (its domains.picked, the newest one: a run Claude picks for holds
// two, the catalog's first at start_run and Claude's after it) and no other. The run that
// picks the domains holds no design (it stops after the pick), so the vehicle in the view
// is the first design of the run named by ?design_run= (a run of the same product); without
// one the view says it has none.
//
// A chip's words: the domain's name as the other cuts give it (labels.js DOMAIN_JA, as the
// N00 chips and the N04 / N15 tiles), and one line under it. The eight rows of the box leave
// room for two lines (about 63 px), so the second line is one of: the domain in the viewers'
// words (DOMAIN_PLAIN_JA), or at a lit chip's turn Claude's reason in their place, or at a
// pale chip's turn the plain words and the record's reason after them. The tests that apply
// are not listed.
//
// Which reason: a pale chip's is the record's (why it is not looked at; labels.js
// plainReason). A lit chip's is the pick's reason (picks[].reason, labels.js plainSentence)
// only when it is Claude's words: the record carries flag_reasons (Claude's picker writes
// them; the catalog's pick at start_run does not) and the reason names none of the pick's
// tests (the sentence the code writes lists them). Assembly has no reason in the record, so
// it keeps its plain words. Ids are out either way.
//
// Claude's reason is shown whole, at min_text_px (18 px) on every chip: not shortened, not
// cut with an ellipsis. One that does not fit the chip's width in one line goes on two,
// broken where its sentence joins (see breakOf). A chip whose words then do not fit its row
// is marked (data-over on the chip, its domain in <html data-n09-over>), for the layout's
// chips box to be made taller; nothing is cut to hide it.
//
// The chips light one at a time from times.chips_from_s, every times.chip_step_s, in the
// order of the record: the picks as listed, then the domains left off (off), then assembly.
// A pick with tests that apply (test_ids) lights; assembly lights when the record lists
// assembly_test_ids. A pick with no test to apply, or a domain left off, stays pale. Until
// its turn a chip waits with its plain words.
//
// Story (layout.json story.cuts.n09): label 8, the seven domains and assembly. The eight
// names are the labels; the second lines are notes (data-kind note, text_px x 0.7, at least
// min_text_px, as the chips box says; Claude's reasons at min_text_px).
//
// Positions, sizes, colours, the length and the times come from layout.json n09; a missing
// key is named on the page.

import * as THREE from "three";
import { clearOfBand } from "./compose.js";
import { DOMAINS, DOMAIN_JA, DOMAIN_PLAIN_JA, esc, plainReason, plainSentence } from "./labels.js";
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

// The join a pale chip's reason is cut at when it does not fit (see fitLine): without its
// subject, the tests' names before " は".
const after = (s, sep) => (s.indexOf(sep) > 0 ? s.slice(s.indexOf(sep) + sep.length).trim() : s);
const PALE_CUTS = [(s) => after(s, " は")];

// Where a sentence may go on to a second line: after "、" (preferred), or after a particle
// that follows a word and comes before the next one (の・と・が・を・に・で・へ・や・は・も
// between a kanji, katakana, latin letter, digit or space and a kanji, katakana, latin
// letter or digit; so not inside a verb's ending such as "しないかを確かめる").
const WORD = "\\p{Script=Han}\\p{Script=Katakana}ー々A-Za-z0-9";
const PARTICLE_RE = new RegExp(`(?<=[${WORD}\\s])[のとがをにでへやはも](?=[${WORD}])`, "gu");
export function breaksOf(s) {
  const at = [];
  for (let i = s.indexOf("、"); i >= 0; i = s.indexOf("、", i + 1)) if (i + 1 < s.length) at.push({ i: i + 1, comma: true });
  for (const m of s.matchAll(PARTICLE_RE)) at.push({ i: m.index + 1, comma: false });
  return at;
}

const get = (obj, path) => path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj);

export function missingN09Keys(layout) {
  return [...N09_KEYS.filter((k) => get(layout.n09, k) === undefined), ...TOP_KEYS.filter((k) => layout[k] === undefined)];
}

// The newest domains.picked of a run's events (in seq order), or null.
function pickedOf(events) {
  return (events ?? []).filter((e) => e.kind === "domains.picked").at(-1)?.data ?? null;
}

// Whether a lit pick's reason is Claude's words (see the top).
function claudesReason(picked, p) {
  const reason = String(p.reason ?? "");
  if (!reason.trim() || !Object.keys(picked?.flag_reasons ?? {}).length) return false;
  const words = reason.split(/[^A-Za-z0-9-]+/);
  return !(p.test_ids ?? []).some((id) => words.includes(id));
}

// The eight chips in the order they light (see the top): { domain, lit, reason, kind }.
// kind: "pick" (tests apply), "none" (picked, no test to apply), "off", "assembly", "absent".
// reason: the second line at the chip's turn ("" when the record gives none to show).
export function chipsOf(picked) {
  const out = [];
  for (const p of picked?.picks ?? []) {
    if ((p.test_ids ?? []).length) out.push({ domain: p.domain, lit: true, kind: "pick", reason: claudesReason(picked, p) ? plainSentence(p.reason) : "" });
    else out.push({ domain: p.domain, lit: false, kind: "none", reason: plainReason(p.reason) });
  }
  for (const o of picked?.off ?? []) out.push({ domain: o.domain, lit: false, kind: "off", reason: plainReason(o.reason) });
  const asm = picked?.assembly_test_ids ?? [];
  out.push({ domain: "assembly", lit: asm.length > 0, kind: asm.length ? "assembly" : "absent", reason: "" });
  // a domain the record names nowhere: pale, no reason (the record gives none)
  for (const d of DOMAINS) if (!out.some((c) => c.domain === d)) out.push({ domain: d, lit: false, kind: "absent", reason: "" });
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
      // the eight names are the story's labels (the seven domains and assembly); the line
      // under each is a note
      row.innerHTML =
        `<b class="name" data-kind="label" style="font-size:${namePx}px">${esc(DOMAIN_JA[d])}</b>` +
        `<span class="line" data-kind="note" style="font-size:${linePx}px">${esc(DOMAIN_PLAIN_JA[d])}</span>`;
      box.append(row);
      this.rows[d] = { row, chip: c, line: row.querySelector(".line") };
    });
    this.linePx = linePx;
    this.lineRoom = C.w - 2 * BORDER_PX - 2 * PAD_PX;
  }

  // The second line (see the top). Done at each frame (measured once the page's font is in),
  // from the record's words each time, so the frame at t is always the same.
  fitLine(r, on) {
    const { chip, line } = r;
    line.style.whiteSpace = "";
    if (on && chip.lit && chip.reason) return this.fitReason(r);
    // the plain words, or a pale chip's: in one line, a pixel at a time down to min_text_px;
    // a pale reason that still does not fit drops its subject, and only then is it cut short
    // with an ellipsis
    const plain = DOMAIN_PLAIN_JA[chip.domain];
    const text = (reason) => (!on || !reason ? plain : `${plain}：${reason}`);
    const fits = (reason) => {
      line.textContent = text(reason);
      let lp = this.linePx;
      line.style.fontSize = `${lp}px`;
      while (line.scrollWidth > this.lineRoom && lp > this.layout.min_text_px) line.style.fontSize = `${(lp -= 1)}px`;
      return line.scrollWidth <= this.lineRoom;
    };
    let reason = chip.reason;
    if (fits(reason) || !on) return;
    for (const cut of PALE_CUTS) {
      const shorter = cut(reason);
      if (shorter && shorter !== reason) {
        reason = shorter;
        if (fits(reason)) return;
      }
    }
  }

  // Claude's reason, whole, at min_text_px: in one line when it fits the chip's width, else on
  // two, broken at breakOf's place.
  fitReason({ chip, line }) {
    const s = chip.reason;
    line.style.fontSize = `${this.layout.min_text_px}px`;
    line.textContent = s;
    if (line.scrollWidth <= this.lineRoom) return;
    const k = this.breakOf(line, s);
    if (k === null) {
      // no join leaves both parts within the width: the browser wraps it (never cut)
      line.style.whiteSpace = "normal";
      return;
    }
    line.replaceChildren(s.slice(0, k), document.createElement("br"), s.slice(k));
  }

  // Of the joins whose two parts each fit the width, one after "、" if there is any, and of
  // those the one whose longer part is shortest (the two lines as even as they can be); null
  // when no join fits.
  breakOf(line, s) {
    const width = (t) => {
      line.textContent = t;
      return line.scrollWidth;
    };
    const ok = breaksOf(s)
      .map((b) => ({ ...b, w: Math.max(width(s.slice(0, b.i)), width(s.slice(b.i))) }))
      .filter((b) => b.w <= this.lineRoom);
    const pool = ok.some((b) => b.comma) ? ok.filter((b) => b.comma) : ok;
    pool.sort((a, b) => a.w - b.w || a.i - b.i);
    return pool.length ? pool[0].i : null;
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
    const over = [];
    for (const r of Object.values(this.rows)) {
      const { row, chip } = r;
      const on = t >= chip.at;
      // at its turn a chip lights (tests apply) or stays pale with its reason
      row.dataset.state = !on ? "wait" : chip.lit ? "lit" : "pale";
      this.fitLine(r, on);
      if (on && chip.lit) lit += 1;
      // words past the row (see the top): marked, not hidden
      if (row.scrollHeight > row.clientHeight + 0.5) {
        row.dataset.over = "1";
        over.push(chip.domain);
      } else delete row.dataset.over;
    }
    document.documentElement.dataset.n09Lit = String(lit);
    document.documentElement.dataset.n09Over = over.join(" ");
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

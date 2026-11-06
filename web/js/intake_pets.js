// The CAE agents' 3D pets on the intake screens (js/intake/, the intake department's; this file only
// mounts into the places it leaves for the host):
//   A1: each domain card's pet place (intake.petSlots: domain -> <div class="in-pet">), the pet of
//       cae.<domain>, as big as the intake's CSS makes the canvas (88 x 88; 104 x 104 with ?video=1).
//       Its pose follows the card's state (slot.dataset.state): wait (thinking), off and draft idle;
//       lit working, with pet3d's pass hop for MOTIONS.pass.react seconds from the time the card
//       answers (the record's pick, then REPLY_STEP_S apart: a1LitTimes). Off is grey by the intake's CSS.
//   A2: the row at the top of the left column (intake.petsSlot): one pet per CAE agent of the record
//       (an event's agent or data.agent_id cae.<domain>, <domain> one of labels.js DOMAINS), in the
//       order of their first analysis.started, each with its domain's name under it. Idle before
//       that agent's first analysis.started, working from it on. A record without a CAE agent leaves
//       the slot empty (the intake's CSS hides it then).
// Which pet and when it moves come from the record's events only, at the replay's time t (display
// time, as the agents' pets: ?t= draws the same picture). Drawing is the app's AgentPets (one
// PetStage, its pixel ratio, its still pictures without a GPU unless ?petmotion=on), so a pet is
// drawn again only when its pose or size changes, at most PET_FPS times a second while it moves.

import { DOMAINS, DOMAIN_JA } from "./labels.js";
import { MOTIONS, PET_FPS, petPose } from "./agent_pets.js";
import { inTurn } from "./intake/a1.js";
import { domainCards } from "./intake/model.js";

// A1's card order (js/intake/a1.js renderA1 sorts the cards so before inTurn): the time each picked
// domain answers at t, as the card shows it (with a playback time, REPLY_STEP_S apart).
const CARD_ORDER = { lit: 0, draft: 0, off: 1, wait: 2 };
export function a1LitTimes(model, t) {
  const out = new Map();
  if (!model) return out;
  const cards = inTurn(domainCards(model).sort((a, b) => CARD_ORDER[a.state] - CARD_ORDER[b.state]), t);
  for (const c of cards) if (c.state === "lit") out.set(c.domain, c.litAt ?? null);
  return out;
}

const CAE = new Set(DOMAINS.filter((d) => d !== "assembly").map((d) => `cae.${d}`));
export const isCaeAgent = (id) => CAE.has(id);

export const A2_PETS_H = 120; // the A2 row's height (px at stage scale 1)

// The A2 row of a list of events: [{ id, domain, startAt }] (startAt: the agent's first
// analysis.started, null when it has none), ordered by startAt (none last, in the order met).
export function caeRow(events) {
  const met = new Map(); // id -> { id, domain, startAt, n }
  for (const ev of events ?? []) {
    for (const id of [ev.agent, ev.data?.agent_id]) {
      if (isCaeAgent(id) && !met.has(id)) met.set(id, { id, domain: id.slice(4), startAt: null, n: met.size });
    }
    if (ev.kind === "analysis.started" && isCaeAgent(ev.agent)) {
      const a = met.get(ev.agent);
      if (a.startAt === null) a.startAt = ev.t;
    }
  }
  const key = (a) => (a.startAt === null ? Infinity : a.startAt);
  return [...met.values()].sort((x, y) => key(x) - key(y) || x.n - y.n).map(({ id, domain, startAt }) => ({ id, domain, startAt }));
}

// An A2 pet's motion at t: idle before its first analysis.started, working from it.
export function a2MotionAt(startAt, t) {
  return startAt !== null && t >= startAt ? { motion: "working", since: startAt } : { motion: "idle", since: 0 };
}

// An A1 pet's motion at t from its card's state and the time its domain was picked (litAt).
export function a1MotionAt(state, litAt, t, hop) {
  if (state !== "lit") return { motion: "idle", since: 0 };
  if (litAt === null || litAt === undefined) return { motion: "working", since: 0 };
  if (t >= litAt && t - litAt < MOTIONS.pass.react(hop)) return { motion: "pass", since: litAt };
  return { motion: "working", since: litAt };
}

const CSS = `
.a2-pets.ipets { display: flex; align-items: flex-end; gap: 6px; height: ${A2_PETS_H}px; margin: 0 0 8px; overflow: hidden; }
.ipet { flex: 0 1 92px; min-width: 0; display: flex; flex-direction: column; align-items: center; }
.ipet .ipet-box { position: relative; width: 100%; height: 92px; }
.ipet .ipet-box canvas.pet3d { position: absolute; inset: 0; width: 100%; height: 100%; }
.ipet .ipet-name { max-width: 100%; font-size: 14px; font-weight: 700; line-height: 20px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.ipet[data-pet-state="idle"] { opacity: 0.55; }`;
function ensureStyle() {
  if (document.getElementById("intake-pets-css")) return;
  const s = document.createElement("style");
  s.id = "intake-pets-css";
  s.textContent = CSS;
  document.head.append(s);
}

export class IntakePets {
  // pets: the app's AgentPets (its stage, pixel ratio, motion settings and still flag)
  constructor(pets) {
    this.pets = pets;
    this.roles = new Map(); // role -> { pet, i }
    this.a1 = new Map(); // domain -> box (A1's slot)
    this.a2 = { key: null, boxes: [] };
    this.rowOf = { events: null, n: -1, last: null, row: [] };
    this.frame = 0;
    ensureStyle();
  }

  role(id) {
    let r = this.roles.get(id);
    if (!r) {
      r = { pet: this.pets.stage.add(id), i: this.pets.HP.ROLES.findIndex((x) => x.id === id) };
      this.roles.set(id, r);
    }
    return r;
  }

  canvas(id) {
    const c = document.createElement("canvas");
    c.className = "pet3d";
    c.dataset.role = id;
    c.dataset.petState = "idle";
    c.dataset.petDraws = "0";
    return c;
  }

  // A1: a canvas in each domain's place (the place is kept across the intake's redraws, so is the canvas)
  mountA1(slots) {
    for (const [domain, slot] of slots ?? []) {
      const id = `cae.${domain}`;
      if (!isCaeAgent(id) || !this.pets.has(id)) continue;
      let b = this.a1.get(domain);
      if (!b) {
        const canvas = this.canvas(id);
        b = { id, el: canvas, canvas, ctx: canvas.getContext("2d"), last: null, at: null, state: null };
        this.a1.set(domain, b);
      }
      if (b.canvas.parentElement !== slot) slot.prepend(b.canvas);
    }
  }

  // A2: the row of the record's CAE agents in the slot (made again only when the agents change)
  mountA2(slot, row) {
    const shown = row.filter((a) => this.pets.has(a.id));
    const key = shown.map((a) => a.id).join(",");
    if (key === this.a2.key && slot.childElementCount === shown.length) return;
    this.a2 = { key, boxes: [] };
    slot.replaceChildren();
    slot.classList.toggle("ipets", shown.length > 0);
    for (const a of shown) {
      const el = document.createElement("div");
      el.className = "ipet";
      el.dataset.agent = a.id;
      el.dataset.domain = a.domain;
      el.dataset.petState = "idle";
      const box = document.createElement("span");
      box.className = "ipet-box";
      const name = document.createElement("span");
      name.className = "ipet-name";
      name.textContent = DOMAIN_JA[a.domain] ?? a.domain;
      el.append(box, name);
      const canvas = this.canvas(a.id);
      box.append(canvas);
      slot.append(el);
      this.a2.boxes.push({ id: a.id, el, canvas, ctx: canvas.getContext("2d"), last: null, at: null, state: null });
    }
  }

  // Draw the pets of the shown intake screen at t. intake: mountIntake's object; events: the
  // player's (display time); budget: pets drawn in this frame (playing: PET_PAINTS; stopped: all).
  tick(intake, events, t, budget = Infinity) {
    if (this.rowOf.events !== events || this.rowOf.n !== events.length || this.rowOf.last !== events.at(-1)) {
      this.rowOf = { events, n: events.length, last: events.at(-1), row: caeRow(events) };
    }
    const row = this.rowOf.row;
    if (intake.petsSlot) this.mountA2(intake.petsSlot, row);
    this.mountA1(intake.petSlots);
    const hop = this.pets.motion.hop_period_s;
    const todo = [];
    row.forEach((a, k) => {
      const b = this.a2.boxes.find((x) => x.id === a.id);
      if (b) todo.push({ b, m: a2MotionAt(a.startAt, t), mark: b.el, k });
    });
    const litAt = this.a1.size ? a1LitTimes(intake.model, t) : null;
    for (const [domain, b] of this.a1) {
      const slot = b.canvas.parentElement;
      if (!slot) continue;
      todo.push({ b, m: a1MotionAt(slot.dataset.state, litAt.get(domain) ?? null, t, hop), mark: null });
    }
    this.frame += 1;
    const n = this.pets.HP.ROLES.length;
    const px = this.pets.pixel;
    const due = [];
    for (const { b, m, mark } of todo) {
      if (!b.canvas.isConnected || b.canvas.offsetParent === null) continue;
      const how = MOTIONS[m.motion];
      if (b.state !== m.motion) {
        b.state = m.motion;
        b.canvas.dataset.petState = m.motion;
        if (mark) mark.dataset.petState = m.motion;
      }
      const { pet, i } = this.role(b.id);
      const off = i / n;
      const tq = how.still ? t : (Math.floor(t * PET_FPS + off) - off) / PET_FPS;
      const idleT = this.pets.motion.idle_shift_s * i;
      const w = Math.max(1, Math.round(b.canvas.clientWidth * px));
      const h = Math.max(1, Math.round(b.canvas.clientHeight * px));
      if (this.pets.still) {
        const k = `still|${w}x${h}`;
        if (k !== b.last) due.push({ b, pet, k, pose: idleT, o: null, look: "idle", w, h, first: true });
        continue;
      }
      const { pose, o } = petPose(m.motion, m.since, tq, idleT, hop);
      const k = `${how.pet}|${pose.toFixed(4)}|${o ? o.since.toFixed(4) : "-"}|${w}x${h}`;
      if (k === b.last) continue;
      const first = !b.last || b.last.split("|")[0] !== how.pet || !b.last.endsWith(`|${w}x${h}`);
      due.push({ b, pet, k, pose, o, look: how.pet, w, h, first });
    }
    due.sort((x, y) => Number(y.first) - Number(x.first) || (x.b.at ?? -1) - (y.b.at ?? -1));
    const stage = this.pets.stage;
    for (const { b, pet, k, pose, o, look, w, h } of due.slice(0, budget)) {
      b.last = k;
      b.at = this.frame;
      if (b.canvas.width !== w || b.canvas.height !== h) {
        b.canvas.width = w;
        b.canvas.height = h;
        stage.grow(w, h);
      }
      stage.paint(pet, b.ctx, pose, look, o);
      b.canvas.dataset.petDraws = String(Number(b.canvas.dataset.petDraws) + 1);
      b.canvas.dataset.petLook = look;
    }
  }
}

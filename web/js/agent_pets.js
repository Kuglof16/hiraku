// The agents as their 3D pets on the app's screen: the workbench's agent list (#agents)
// and the all-domains grid (each domain's tile corner; lead, designer and any other
// agent in the grid's head). One pet per agent of the record (s.agentOrder: agent.started's
// agent_id and every event's agent), as the role of promo/pets/pet3d.js (ROLES).
//
// Drawing is cut_pets.js's PetStage (the same as the cut pages): one WebGL renderer off the
// page, each pet copied into its own 2D canvas. Without a GPU (WebGL drawn by software, as
// SwiftShader: isSoftwareRenderer) each pet is one still picture, drawn once. A pet is drawn again only when its pose
// changes: a still pet (idle, and blocked once it has settled) once; a moving one at most PET_FPS
// times a second, the pets' clocks staggered so they do not all draw in the same frame. The pets
// are small (34-60 px): pet3d.js's light build (lite, v9). Without
// /pets/pet3d.js (404) or n00's camera in /layout.json, the app keeps its circles and names.
//
// What the pets do comes from the record only, at the replay's time t (?t= draws the same):
// PET_EVENTS says which event moves which pet how; MOTIONS how each motion is drawn with
// pet3d's states and how long a reaction lasts. A pet's bubble is the agent's newest log
// line (the same words as the log), cut short, for SAY_S seconds after its event.

import { AGENT_STOP_SHORT_JA, DESIGNER_ROLES_JA, DOMAINS, agentName, designerModule, designerRole, esc } from "./labels.js";
import { PetStage, loadPets, missingPetKeys, poseTime } from "./cut_pets.js";
import { loadLayout } from "./compose.js";
import { designerColor } from "./proposals.js"; // modules:
import { moduleStyle } from "./modules.js"; // modules:

export const SAY_S = 5; // seconds a bubble stays (as the 3D's bubble: app.js SHOW_S)
export const SAY_CHARS = 26; // a bubble shows at most this many characters of the line
export const PET_FPS = 15; // a moving pet is drawn at most this many times a second
const PET_SHADOW = 256; // the pets' shadow map (px a side): the list's pets are at most 60 px tall
// pet3d.js v9 lite (fewer segments, no thin outlines, fewer shadow casters) and the stage's
// antialias, chosen by comparing 60 px pictures of the 16 roles: lite looks the same as without;
// without antialias the eyes and outlines break up into pixels, so it stays on.
export const PET_LITE = true;
export const PET_ANTIALIAS = true;
export const PET_PAINTS = 1; // pets drawn in one frame while the replay plays (agent_pets.tick budget)
const PASS_HOPS = 3; // a pass reaction: this many hops of pet3d's pass (n00.motion.hop_period_s each)

const BLOCK_SETTLE_S = 0.8; // pet3d.js v9 blocked: the pose no longer changes from this long after the block (promo/pets/README.md)

// How each motion is drawn: pet (pet3d.js state), still (one fixed pose, drawn once), react
// (a reaction: how long it lasts, s; null: it lasts until the next event that changes it),
// settle (s after since from which the pose holds: drawn once more, then not again).
// The marks above the head (…, the stop sign, ✓, ?) are pet3d.js's own (v9); the screen adds none.
export const MOTIONS = {
  working: { ja: "作業中", pet: "working", still: false, react: null },
  waiting: { ja: "待ち", pet: "waiting", still: false, react: null },
  idle: { ja: "終わり", pet: "idle", still: true, react: null },
  pass: { ja: "合格", pet: "pass", still: false, react: (hop) => PASS_HOPS * hop },
  mismatch: { ja: "食い違い", pet: "mismatch", still: false, react: () => 3 },
  blocked: { ja: "止められた", pet: "blocked", still: false, react: () => 3, settle: BLOCK_SETTLE_S },
  report: { ja: "報告", pet: "report", still: false, react: () => SAY_S },
};

// The pet3d time of an agent's pose at t (tq: t stepped to PET_FPS) and the update options:
// idle one fixed pose (idleT); waiting its own calm clock from 0, shifted by idleT (the pets do
// not sway together); blocked as the others (poseTime) with o.since the pose time of the block
// itself (ceil(since / hop + 1) hop, pet3d README), so the flinch comes at the event, and held
// from settle on; the others poseTime.
export function petPose(motion, since, tq, idleT, hop) {
  const how = MOTIONS[motion];
  if (how.pet === "idle") return { pose: idleT, o: null };
  if (how.pet === "waiting") return { pose: idleT + tq, o: null };
  const t = how.settle != null ? Math.min(tq, since + how.settle) : tq;
  const pose = poseTime(how.pet, since, Math.max(since, t), idleT, hop);
  return { pose, o: how.pet === "blocked" ? { since: Math.ceil(since / hop + 1) * hop } : null };
}

// Which event moves which pet: the one table. who: the event's agent (agent), data.agent_id
// (agent_id), the agent of the judged domain (domain: cae.<domain>, packager for assembly) or
// every agent (all). base: what the agent does from then on (until another base event);
// otherwise a reaction over it for MOTIONS[motion].react seconds. when: the condition.
export const PET_EVENTS = [
  { kind: "agent.started", who: "agent_id", base: true, motion: "working", when: "動き始めた" },
  { kind: "analysis.started", who: "agent", base: true, motion: "working", when: "解析を始めた (analysis.finished まで)" },
  { kind: "analysis.finished", who: "agent", base: true, motion: null, when: "解析が終わった: その前の作業中 / 待ちにもどる" },
  { kind: "hearing.asked", who: "agent", base: true, motion: "waiting", when: "答え待ち (hearing.answered まで)" },
  { kind: "hearing.answered", who: "all", base: true, motion: null, when: "答えがそろった: 答え待ちが終わる" },
  { kind: "agent.stopped", who: "agent_id", base: true, motion: "idle", when: "手を止めた" },
  { kind: "run.finished", who: "all", base: true, motion: "idle", when: "実行が終わった" },
  { kind: "verdict.recorded", who: "agent", base: false, motion: "pass", when: "判定が合格になった (同じ verdict_id で前は不合格・無し)" },
  { kind: "judgement.updated", who: "domain", base: false, motion: "pass", when: "分野の判定が緑になった (その分野の前の判定が緑でない)" },
  { kind: "comparison.recorded", who: "agent", base: false, motion: "mismatch", when: "照合が食い違った (同じ comparison_id で前は一致・無し)" },
  { kind: "hook.denied", who: "agent", base: false, motion: "blocked", when: "hooks に止められた" },
  { kind: "hook.blocked_stop", who: "agent", base: false, motion: "blocked", when: "hooks が終わるのを止めた" },
  { kind: "agent.said", who: "agent", base: false, motion: "report", when: "話した (吹き出しと同じ間)" },
];
const BY_KIND = Object.fromEntries(PET_EVENTS.map((r) => [r.kind, r]));

const realAgent = (id) => !!id && id !== "system" && id !== "user";
const domainAgent = (d) => (d === "assembly" ? "packager" : `cae.${d}`);

// The pets' timeline of a list of events (the player's, in display time): per agent the
// changes [{ t, seq, motion, base }], and per event seq the agents it concerns (whose bubble
// its log line is). Events are taken in order; only t decides what is shown at t.
export function petTimeline(events) {
  const per = new Map(); // agent id -> [{ t, seq, motion, base }]
  const owners = new Map(); // seq -> [agent id]
  const flags = new Map(); // agent id -> { active, asking, running: Set, done }
  const verdictPass = new Map();
  const cmpOk = new Map();
  const domainGreen = new Map();
  const list = (id) => (per.has(id) ? per.get(id) : per.set(id, []).get(id));
  const fl = (id) => (flags.has(id) ? flags.get(id) : flags.set(id, { active: false, asking: false, running: new Set(), done: false, stopped: false }).get(id));
  // running an analysis > waiting for answers > started and not stopped > stopped or the run over
  const baseOf = (f) => (f.running.size ? "working" : f.asking ? "waiting" : f.active && !f.done ? "working" : f.done || f.stopped ? "idle" : "waiting");
  const own = (seq, id) => {
    if (!realAgent(id)) return;
    if (!owners.has(seq)) owners.set(seq, []);
    if (!owners.get(seq).includes(id)) owners.get(seq).push(id);
  };
  const seen = new Set(); // every agent so far (who: all)
  for (const ev of events) {
    const d = ev.data ?? {};
    const evAgent = realAgent(ev.agent) ? ev.agent : null;
    if (evAgent) own(ev.seq, evAgent);
    for (const id of [evAgent, d.agent_id, ...(ev.kind === "run.started" || ev.kind === "project.updated" ? (d.agents ?? []).map((a) => a.agent_id) : [])]) if (realAgent(id)) seen.add(id);
    if (ev.kind === "agent.started" || ev.kind === "agent.stopped") own(ev.seq, d.agent_id ?? ev.agent);
    if (ev.kind === "judgement.updated" && d.domain) own(ev.seq, domainAgent(d.domain));
    const row = BY_KIND[ev.kind];
    if (!row) continue;
    const who = row.who === "agent_id" ? [d.agent_id ?? ev.agent] : row.who === "domain" ? [d.domain ? domainAgent(d.domain) : null] : row.who === "all" ? [...seen] : [evAgent];
    for (const id of who) {
      if (!realAgent(id)) continue;
      if (row.base) {
        const f = fl(id);
        if (ev.kind === "agent.started") Object.assign(f, { active: true, stopped: false, done: false });
        else if (ev.kind === "agent.stopped") Object.assign(f, { active: false, stopped: true });
        else if (ev.kind === "analysis.started") f.running.add(d.analysis_id ?? ev.seq);
        else if (ev.kind === "analysis.finished") f.running.delete(d.analysis_id ?? null);
        else if (ev.kind === "hearing.asked") f.asking = true;
        else if (ev.kind === "hearing.answered") f.asking = false;
        else if (ev.kind === "run.finished") Object.assign(f, { done: true, active: false, asking: false, running: new Set() });
        list(id).push({ t: ev.t, seq: ev.seq, motion: baseOf(f), base: true });
        continue;
      }
      let fire = true;
      if (ev.kind === "verdict.recorded") {
        fire = d.pass === true && verdictPass.get(d.verdict_id) !== true;
        verdictPass.set(d.verdict_id, d.pass === true);
      } else if (ev.kind === "judgement.updated") {
        fire = d.state === "green" && domainGreen.get(d.domain) !== true;
        domainGreen.set(d.domain, d.state === "green");
      } else if (ev.kind === "comparison.recorded") {
        const ok = d.status === "match";
        fire = !ok && cmpOk.get(d.comparison_id) !== false;
        cmpOk.set(d.comparison_id, ok);
      }
      if (fire) list(id).push({ t: ev.t, seq: ev.seq, motion: row.motion, base: false });
    }
  }
  return { per, owners };
}

// What an agent's pet does at t: { motion, since } (since: when that motion started).
// hop: n00.motion.hop_period_s (a pass lasts PASS_HOPS hops). Before any event moves it,
// an agent waits (the lead watches: 見守り; the others: 待ち).
export function petMotionAt(changes, t, hop) {
  let base = { motion: "waiting", since: 0 };
  let react = null;
  for (const c of changes ?? []) {
    if (c.t > t) break;
    if (c.base) {
      if (c.motion !== base.motion) base = { motion: c.motion, since: c.t };
    } else react = { motion: c.motion, since: c.t };
  }
  if (react && t - react.since < MOTIONS[react.motion].react(hop)) return react;
  return base;
}

// The agent's bubble at t from the log (state.js s.log, display time): its newest line from
// an event it is in (owners), shown for SAY_S seconds, cut to SAY_CHARS characters.
export function sayAt(log, owners, id, t) {
  for (let i = log.length - 1; i >= 0; i--) {
    const e = log[i];
    if (e.t > t) continue;
    if (t - e.t >= SAY_S) return null;
    if (!(owners.get(e.seq) ?? []).includes(id)) continue;
    // its stop: no bubble (「…が手を止めた (…)」 in a frame on every row); the why line under the name says it
    if (e.kind === "agent.stopped") return null;
    return { seq: e.seq, t: e.t, text: shortSay(e.text, id), full: e.text };
  }
  return null;
}

// A log line as a bubble: without the speaker's name in front, its first line, cut short.
export function shortSay(text, id) {
  let s = String(text ?? "").split("\n")[0].trim();
  const name = agentName(id);
  if (name && s.startsWith(`${name}: `)) s = s.slice(name.length + 2);
  const chars = [...s];
  return chars.length > SAY_CHARS ? `${chars.slice(0, SAY_CHARS - 1).join("")}…` : s;
}

// Where an agent's pet stands in the grid: its domain's tile, else the head.
export function gridPlaceOf(id) {
  if (id === "packager") return "assembly";
  if (id.startsWith("cae.") && DOMAINS.includes(id.slice(4))) return id.slice(4);
  return "head";
}

// A pet's row (the workbench's list, the grid's head): the pet, the agent's name and what it
// does now (state.js status), and under them its bubble.
// name: the words for the agent (default its short name; the agents' pane gives the role's long name)
// why: state.js a.stopWhy (agent.stopped's stop_code / stop_reason): a line under the name while the
// pet has no bubble (stopWhyHtml); none for a record without either.
export function petRowHtml(id, status, { tag = "li", cls = "", name = null, why = null } = {}) {
  return (
    `<${tag} class="petrow ${cls}" data-agent="${esc(id)}" data-say-host><span class="petslot" data-pet-slot="${esc(id)}"></span>` +
    `<span class="nm">${esc(name ?? agentName(id))}</span><span class="st">${esc(status ?? "")}</span><span class="say" data-say="${esc(id)}"></span>${stopWhyHtml(why)}</${tag}>`
  );
}

// Why an agent stopped, one line: the code's short words (labels.js AGENT_STOP_SHORT_JA), else the reason cut
// short; the whole reason is the title.
export function stopWhyHtml(why) {
  if (!why) return "";
  const chars = [...(AGENT_STOP_SHORT_JA[why.code] ?? why.words ?? why.reason ?? "")];
  const text = chars.length > SAY_CHARS ? `${chars.slice(0, SAY_CHARS - 1).join("")}…` : chars.join("");
  const title = why.reason && why.reason !== text ? why.reason : "";
  return `<span class="why" data-stop-code="${esc(why.code ?? "")}"${title ? ` title="${esc(title)}"` : ""}>${esc(text)}</span>`;
}

// WebGL drawn by software (no GPU): SwiftShader (Chromium's), llvmpipe / softpipe / lavapipe (Mesa),
// Microsoft Basic Render Driver (Windows without a GPU driver). There each pet is one still picture:
// drawn once (pet3d.js idle, at its box's size) and not moved, so the 3D keeps its pace.
const SOFTWARE_GL = /swiftshader|llvmpipe|softpipe|lavapipe|basic render|software/i;
export function isSoftwareRenderer(name) {
  return SOFTWARE_GL.test(String(name ?? ""));
}
// The renderer's name as the driver gives it (UNMASKED_RENDERER_WEBGL; without the extension, RENDERER).
export function glRendererName(gl) {
  if (!gl) return "";
  const ext = gl.getExtension("WEBGL_debug_renderer_info");
  return String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
}

const fade = (dt) => (dt < 0 || dt >= SAY_S ? 0 : Math.min(1, dt / 0.25, (SAY_S - dt) / 0.6));

export class AgentPets {
  // Loads pet3d.js and /layout.json's n00 camera and motion; null when either is missing.
  static async load() {
    const [HP, layout] = await Promise.all([loadPets(), loadLayout().catch(() => null)]);
    if (!HP || !layout || missingPetKeys(layout).length) return null;
    return new AgentPets(HP, layout);
  }

  constructor(HP, layout) {
    this.HP = HP;
    this.roles = new Set(HP.ROLES.map((r) => r.id));
    this.motion = layout.n00.motion;
    this.stage = new PetStage(HP, layout.n00.camera, { W: 64, H: 64 }, { shadow: PET_SHADOW, lite: PET_LITE, antialias: PET_ANTIALIAS });
    this.stage.setScale(1);
    this.pets = new Map(); // agent id -> { pet, i, boxes: { bench, grid } }
    this.timeline = { events: null, n: -1, per: new Map(), owners: new Map() };
    this.pixel = 1; // canvas px per stage px (stage scale x device pixel ratio)
    this.stats = { paints: 0, ms: 0 };
    this.frame = 0;
    // no GPU (a software renderer): the pets are still pictures (isSoftwareRenderer)
    // (?petmotion=on moves them anyway, ?petmotion=off keeps them still anyway: the tests' and a comparison's)
    this.rendererName = glRendererName(this.stage.renderer?.getContext?.());
    const force = new URLSearchParams(globalThis.location?.search ?? "").get("petmotion");
    this.still = force === "on" ? false : force === "off" ? true : isSoftwareRenderer(this.rendererName);
    document.documentElement.dataset.petsStill = this.still ? "1" : "0";
  }

  // Can the agent be drawn as a pet (its role is one of pet3d.js ROLES)?
  has(id) {
    return this.roles.has(this.roleOf(id));
  }

  // modules: the pet3d.js role an agent is drawn as: its own id when ROLES has it (v10 has the six
  // designer.<module> of older records), else a module designer (the design roles designer.structure,
  // .mechanism, .electric: no row in pet3d.js) is the designer's pet with its colour mark (box).
  roleOf(id) {
    if (this.roles.has(id)) return id;
    return id?.startsWith("designer.") ? "designer" : id;
  }

  setPixel(k) {
    if (Math.abs(k - this.pixel) < 1e-6) return;
    this.pixel = k;
    for (const p of this.pets.values()) for (const b of Object.values(p.boxes)) b.last = null;
  }

  // The agent's box for a place (bench / grid), made once and moved into the slots.
  box(id, place) {
    let p = this.pets.get(id);
    if (!p) {
      // i: the role's place in pet3d.js ROLES, not the order the pets were made in (a page
      // opened at ?t= makes them in another order than one played up to t)
      const role = this.roleOf(id);
      p = { pet: this.stage.add(role), i: this.HP.ROLES.findIndex((r) => r.id === role), boxes: {} };
      this.pets.set(id, p);
    }
    let b = p.boxes[place];
    if (!b) {
      const el = document.createElement("span");
      el.className = "petbox";
      el.dataset.agent = id;
      el.dataset.role = p.pet.role.id;
      el.dataset.petState = "waiting";
      el.dataset.petDraws = "0";
      el.title = `${agentName(id)}のペット`;
      const canvas = document.createElement("canvas");
      canvas.className = "pet3d";
      el.append(canvas);
      // modules: a module designer drawn as the designer's pet carries a colour mark: a design role
      // (designer.structure) its own colour and the role's first letter (構・機・電), an older record's
      // designer.<module> (when pet3d.js has no row for it) its module's colour
      if (p.pet.role.id !== id && designerColor(id)) {
        moduleStyle();
        const mark = document.createElement("span");
        mark.className = "modmark";
        const r = designerRole(id);
        if (r) {
          mark.dataset.designerRole = r;
          mark.textContent = [...DESIGNER_ROLES_JA[r]][0];
        } else mark.dataset.module = designerModule(id);
        mark.style.setProperty("--mod", designerColor(id));
        el.append(mark);
      }
      b = p.boxes[place] = { el, canvas, ctx: canvas.getContext("2d"), last: null, state: null };
    }
    return b.el;
  }

  // Put the boxes into the slots ([data-pet-slot] = agent id) under root.
  mount(root, place) {
    for (const slot of root.querySelectorAll("[data-pet-slot]")) {
      const el = this.box(slot.dataset.petSlot, place);
      if (el.parentElement !== slot) slot.append(el);
    }
  }

  // Draw the pets and bubbles of one place at t (only those on the page and shown). budget:
  // how many pets may be drawn in this frame (playing: PET_PAINTS; a frame stopped at t, as
  // ?t= or a seek: all). Those whose motion or size changed go first, then the moving ones
  // that waited longest; the others keep their picture until a later frame.
  tick(s, events, t, place, budget = Infinity) {
    // (the player re-times events into new objects: the last one tells a change)
    if (this.timeline.events !== events || this.timeline.n !== events.length || this.timeline.last !== events.at(-1)) {
      const { per, owners } = petTimeline(events);
      this.timeline = { events, n: events.length, last: events.at(-1), per, owners };
    }
    this.frame += 1;
    const hop = this.motion.hop_period_s;
    const n = this.HP.ROLES.length;
    const due = [];
    for (const [id, p] of this.pets) {
      const b = p.boxes[place];
      if (!b || !b.el.isConnected || b.el.offsetParent === null) continue;
      const m = petMotionAt(this.timeline.per.get(id), t, hop);
      const how = MOTIONS[m.motion];
      if (b.state !== m.motion) b.el.dataset.petState = b.state = m.motion;
      // a moving pet's clock steps PET_FPS times a second, each pet's steps shifted by i / n
      const off = p.i / n;
      const tq = how.still ? t : (Math.floor(t * PET_FPS + off) - off) / PET_FPS;
      const idleT = this.motion.idle_shift_s * p.i;
      const w = Math.max(1, Math.round(b.el.clientWidth * this.pixel));
      const h = Math.max(1, Math.round(b.el.clientHeight * this.pixel));
      if (this.still) {
        // one picture per box (again only when its size changes): the idle pose, whatever the motion
        const k = `still|${w}x${h}`;
        if (k !== b.last) due.push({ p, b, k, pose: idleT, o: null, pet: "idle", w, h, first: true });
        continue;
      }
      const { pose, o } = petPose(m.motion, m.since, tq, idleT, hop);
      const k = `${how.pet}|${pose.toFixed(4)}|${o ? o.since.toFixed(4) : "-"}|${w}x${h}`;
      if (k === b.last) continue;
      const first = !b.last || b.last.split("|")[0] !== how.pet || !b.last.endsWith(`|${w}x${h}`);
      due.push({ p, b, k, pose, o, pet: how.pet, w, h, first });
    }
    due.sort((x, y) => Number(y.first) - Number(x.first) || (x.b.at ?? -1) - (y.b.at ?? -1));
    for (const { p, b, k, pose, o, pet, w, h } of due.slice(0, budget)) {
      b.last = k;
      b.at = this.frame;
      if (b.canvas.width !== w || b.canvas.height !== h) {
        b.canvas.width = w;
        b.canvas.height = h;
        this.stage.grow(w, h);
      }
      const t0 = performance.now();
      this.stage.paint(p.pet, b.ctx, pose, pet, o);
      this.stats.ms += performance.now() - t0;
      this.stats.paints += 1;
      b.el.dataset.petDraws = String(Number(b.el.dataset.petDraws) + 1);
      b.el.dataset.petLook = pet; // the pet3d.js state drawn last
    }
    this.says(s, t, place);
  }

  // The bubbles ([data-say] = agent id) of the place's rows and tiles.
  says(s, t, place) {
    const root = place === "grid" ? document.getElementById("view-grid") : document.getElementById("agents");
    if (!root) return;
    for (const el of root.querySelectorAll("[data-say]")) {
      const id = el.dataset.say;
      const say = sayAt(s.log, this.timeline.owners, id, t);
      const a = say ? fade(t - say.t) : 0;
      const key = say ? String(say.seq) : "";
      if (el.dataset.seq !== key) {
        el.dataset.seq = key;
        el.innerHTML = say ? esc(say.text) : "";
        el.title = say ? say.full : "";
      }
      const op = a.toFixed(2);
      if (el.style.opacity !== op) el.style.opacity = op;
      const on = a > 0;
      if ((el.dataset.on === "1") !== on) {
        el.dataset.on = on ? "1" : "0";
        el.closest("[data-say-host]")?.classList.toggle("saying", on);
      }
    }
  }
}

// Timeline over events: replay (play, pause, speed, seek) and live follow.
// Both modes move one playhead and the state is always "every event whose
// time <= playhead, applied in seq order", so live and replay draw the same way.
//
// The playhead runs on display time, on one of three clocks (`clock`):
//   "event" (the default): the recorded gaps are dropped; each event stays on screen for its own
//     time (dweller(ev) -> { s, key }) and the next one follows. A finished record is fitted to
//     fitS seconds: the routine events (key false) are shortened first (down to ROUTINE_MIN of
//     their time), then the key ones (down to KEY_MIN), then all alike. So a run of an hour and a thousand events plays
//     in one to two minutes, every event in its order.
//   "paced": each recorded gap stretched to at least pacer(ev) and cut to at most maxGap.
//   "real": the recorded `t`.
// Whichever, the recorded value stays in `t_rec` and each event's display time until the next
// in `dwell`; the seek bar, ?t= and window.hiraku.seek all use display time.

import { fetchEvents } from "./eventread.js";
import { applyEvent, initialState } from "./state.js";

export const SPEEDS = [0.5, 1, 2, 4, 8];
export const CLOCKS = ["event", "paced", "real"];
const ROUTINE_MIN = 0.15; // the event clock's fit shortens a routine event to no less than this share
const KEY_MIN = 0.35; // nor a key event below this share (then all alike)

export class Player {
  constructor() {
    this.raw = []; // events as recorded, in seq order
    this.events = []; // the same events with t = display time and t_rec = recorded t
    this.state = initialState();
    this.applied = 0; // number of events applied to state
    this.playhead = 0;
    this.playing = false;
    this.speed = 1;
    this.live = false; // following a run that is still being written
    this.liveAttached = false; // playhead glued to the live edge
    this.startWallMs = null;
    this.minEnd = 0; // the replay lasts at least this long (a test's motion may play past the last event)
    this.clock = "event";
    this.dweller = () => ({ s: 0.5, key: false }); // the event clock: an event's seconds on screen
    this.fitS = 110; // the event clock: a finished record plays in at most about this long
    this.fit = { routine: 1, key: 1 }; // the event clock's shares (kept while following a live run)
    this.pacer = () => 0; // "paced": seconds an event needs on screen before the next one
    this.maxGap = 3; // "paced": a longer recorded gap is cut to this
  }

  // (the old switch: paced or real)
  get pacing() {
    return this.clock !== "real";
  }

  set pacing(on) {
    this.clock = on ? (this.clock === "real" ? "event" : this.clock) : "real";
  }

  // The event clock's shares for the record as it is now: 1 while it fits in fitS (or a live run
  // is followed: the events already on screen keep their times).
  fitNow() {
    if (this.live) return this.fit;
    let key = 0;
    let routine = 0;
    for (const e of this.raw) {
      const d = this.dweller(e);
      if (d.key) key += d.s;
      else routine += d.s;
    }
    const target = this.fitS;
    if (key + routine <= target) return { routine: 1, key: 1 };
    const r = Math.max(ROUTINE_MIN, Math.min(1, (target - key) / Math.max(routine, 1e-9)));
    const k = key + r * routine <= target ? 1 : Math.max(KEY_MIN, (target - r * routine) / Math.max(key, 1e-9));
    // still longer at both floors: everything shortened alike to fit
    const over = Math.min(1, target / (k * key + r * routine));
    return { routine: r * over, key: k * over };
  }

  dwellOf(e) {
    const d = this.dweller(e);
    return Math.max(0, d.s) * (d.key ? this.fit.key : this.fit.routine);
  }

  get duration() {
    const last = this.events.at(-1);
    // (the event clock: the last event is on screen for its own time too; a run followed live
    // is not over, its newest event's time is the wait in tick)
    const hold = this.clock === "event" && last && !this.live ? last.dwell ?? 0 : 0;
    const end = Math.max(last ? last.t + hold : 0, this.minEnd);
    return this.live && this.liveAttached ? Math.max(end, this.playhead) : end;
  }

  get finished() {
    return this.events.some((e) => e.kind === "run.finished");
  }

  // Add events (any order, duplicates by seq are dropped). Returns how many were new.
  append(evs) {
    const seen = new Set(this.raw.map((e) => e.seq));
    const from = this.raw.length;
    let added = 0;
    let outOfOrder = false;
    for (const e of evs) {
      if (typeof e?.seq !== "number" || typeof e?.t !== "number" || seen.has(e.seq)) continue;
      seen.add(e.seq);
      if (this.raw.length && e.seq < this.raw.at(-1).seq) outOfOrder = true;
      this.raw.push(e);
      added += 1;
      if (e.kind === "run.started" && e.ts) this.startWallMs = Date.parse(e.ts);
    }
    if (outOfOrder) {
      this.raw.sort((a, b) => a.seq - b.seq);
      this.retime(0);
      this.rebuild();
    } else if (this.retime(from) && this.applied > 0) {
      this.rebuild(); // the event clock's fit changed: every time moved
    }
    return added;
  }

  // Display times from index i on (earlier ones are already on screen). Returns true when it
  // timed all of them again (the event clock's fit changed).
  retime(i) {
    let again = false;
    if (this.clock === "event") {
      const f = this.fitNow();
      if (f.routine !== this.fit.routine || f.key !== this.fit.key) {
        this.fit = f;
        again = i > 0;
        i = 0;
      }
    }
    this.events.length = i;
    for (let k = i; k < this.raw.length; k += 1) {
      const e = this.raw[k];
      let t = this.clock === "event" ? 0 : e.t;
      if (k > 0) {
        const prev = this.raw[k - 1];
        let gap = e.t - prev.t;
        if (this.clock === "event") gap = this.dwellOf(prev);
        else if (this.clock === "paced") {
          const lo = this.pacer(prev) ?? 0;
          gap = Math.min(Math.max(gap, lo), Math.max(this.maxGap, lo));
        }
        t = this.events[k - 1].t + Math.max(0, gap);
        this.events[k - 1].dwell = t - this.events[k - 1].t;
      }
      this.events.push({ ...e, t, t_rec: e.t, dwell: this.clock === "event" ? this.dwellOf(e) : 0 });
    }
    return again;
  }

  // Another clock; the event on screen stays on screen (the playhead goes to its new time).
  setClock(clock) {
    if (!CLOCKS.includes(clock) || clock === this.clock) return;
    const at = this.applied > 0 ? this.events[this.applied - 1] : null;
    const into = at ? Math.min(1, (this.playhead - at.t) / Math.max(at.dwell, 1e-9)) : 0;
    this.clock = clock;
    this.fit = { routine: 1, key: 1 };
    this.retime(0);
    const k = at ? this.events.findIndex((e) => e.seq === at.seq) : -1;
    this.playhead = k >= 0 ? this.events[k].t + Math.max(0, into) * (this.events[k].dwell || 0) : 0;
    this.rebuild();
  }

  setPacing(on) {
    this.setClock(on ? "paced" : "real");
  }

  // The first event after the playhead that `test` accepts, or null.
  nextWhere(test) {
    const now = this.playhead;
    return this.events.find((e) => e.t > now + 1e-6 && test(e)) ?? null;
  }

  // Jump to the next event `test` accepts; false when there is none.
  seekNext(test) {
    const e = this.nextWhere(test);
    if (e) this.seek(e.t);
    return !!e;
  }

  clear() {
    this.raw = [];
    this.events = [];
  }

  rebuild() {
    this.state = initialState();
    this.applied = 0;
    this.sync();
  }

  // Count of events whose t <= playhead (events are in seq order; t should not go back).
  targetCount() {
    let n = this.applied;
    while (n < this.events.length && this.events[n].t <= this.playhead + 1e-9) n += 1;
    while (n > 0 && this.events[n - 1].t > this.playhead + 1e-9) n -= 1;
    return n;
  }

  sync() {
    const target = this.targetCount();
    if (target < this.applied) {
      this.state = initialState();
      this.applied = 0;
    }
    while (this.applied < target) {
      applyEvent(this.state, this.events[this.applied]);
      this.applied += 1;
    }
  }

  seek(t) {
    this.playhead = Math.max(0, Math.min(t, this.duration));
    if (this.live) this.liveAttached = false;
    this.sync();
  }

  // seek(t) with the events applied in steps of at most sliceMs, awaited between them, so building a
  // long record's state does not hold the page (the caller draws nothing until it resolves).
  async seekInSteps(t, sliceMs = 25) {
    this.playhead = Math.max(0, Math.min(t, this.duration));
    if (this.live) this.liveAttached = false;
    const target = this.targetCount();
    if (target < this.applied) {
      this.state = initialState();
      this.applied = 0;
    }
    let t0 = performance.now();
    while (this.applied < target) {
      applyEvent(this.state, this.events[this.applied]);
      this.applied += 1;
      if (performance.now() - t0 > sliceMs) {
        await new Promise((r) => setTimeout(r, 0));
        t0 = performance.now();
      }
    }
  }

  // Jump to the previous / next event time.
  step(dir) {
    const ts = this.events.map((e) => e.t);
    const now = this.playhead;
    const t = dir > 0 ? ts.find((x) => x > now + 1e-6) : [...ts].reverse().find((x) => x < now - 1e-6);
    if (t !== undefined) this.seek(t);
  }

  play() {
    if (!this.live && this.playhead >= this.duration - 1e-6) this.seek(0);
    this.playing = true;
  }

  pause() {
    this.playing = false;
    if (this.live) this.liveAttached = false;
  }

  toggle() {
    this.playing ? this.pause() : this.play();
  }

  // Follow the live edge: jump to the newest event, then move on in real time.
  attachLive() {
    if (!this.live) return;
    this.liveAttached = true;
    this.playing = true;
    this.playhead = Math.max(this.playhead, this.events.at(-1)?.t ?? 0);
    this.sync();
  }

  cycleSpeed() {
    const i = SPEEDS.indexOf(this.speed);
    this.speed = SPEEDS[(i + 1) % SPEEDS.length];
  }

  // Advance by wall-clock dt (seconds). Returns true when the playhead moved.
  tick(dt, nowMs = Date.now()) {
    const before = this.playhead;
    if (this.live && this.finished) {
      // The run ended while we followed it: from here on it is a replay that
      // plays out what is left (paced events may still be ahead).
      this.live = false;
      this.liveAttached = false;
      this.playing = this.playhead < this.duration;
    } else if (this.live && this.liveAttached) {
      const lastT = this.events.at(-1)?.t ?? 0;
      if (this.clock !== "real") {
        // Real time, but wait at most maxGap (the event clock: the newest event's own time) past
        // the newest event, so the next one is never already behind the playhead.
        const wait = this.clock === "event" ? this.events.at(-1)?.dwell ?? 0 : this.maxGap;
        this.playhead = Math.max(this.playhead, Math.min(this.playhead + dt, lastT + wait));
      } else {
        const wall = this.startWallMs ? (nowMs - this.startWallMs) / 1000 : lastT;
        this.playhead = Math.max(lastT, wall, this.playhead);
      }
    }
    if (!(this.live && this.liveAttached) && this.playing) {
      this.playhead = Math.min(this.playhead + dt * this.speed, this.duration);
      if (this.playhead >= this.duration) this.playing = false;
    }
    if (this.playhead !== before || this.targetCount() !== this.applied) this.sync();
    return this.playhead !== before;
  }
}

// Read /api/runs/<id>/events from a byte offset until the run finishes.
export class EventFeed {
  // onProgress(fraction 0..1): how far the first read of the record is (eventread.js)
  constructor(runId, onEvents, { intervalMs = 500, onProgress = null } = {}) {
    this.runId = runId;
    this.onEvents = onEvents;
    this.onProgress = onProgress;
    this.intervalMs = intervalMs;
    this.offset = 0;
    this.timer = null;
    this.stopped = false;
    this.error = null;
  }

  async readOnce() {
    // (fetchEvents throws "<status> <path>" with .status on a failed request, as main's read did)
    const body = await fetchEvents(this.runId, this.offset, this.loaded ? null : this.onProgress);
    const moved = body.next_offset !== this.offset || !!body.reset;
    this.offset = body.next_offset;
    // ask again at once only when this read got somewhere; a line still being written
    // (next_offset did not move) waits for the next poll
    const more = moved && body.next_offset < body.size;
    this.onEvents(body.events, body);
    return more;
  }

  // Read everything that is there now (in as many requests as needed; stops at a half-written line).
  async readAll() {
    while (await this.readOnce()) {
      /* keep reading */
    }
  }

  follow(isFinished) {
    const loop = async () => {
      if (this.stopped) return;
      try {
        await this.readAll();
        this.error = null;
      } catch (e) {
        this.error = String(e);
      }
      if (!this.stopped && !isFinished()) this.timer = setTimeout(loop, this.intervalMs);
    };
    loop();
  }

  stop() {
    this.stopped = true;
    clearTimeout(this.timer);
  }
}

export function parseJsonl(text) {
  const out = [];
  for (const line of text.split(/\r?\n/)) {
    const s = line.trim();
    if (!s) continue;
    try {
      const obj = JSON.parse(s);
      if (obj && typeof obj === "object" && !Array.isArray(obj)) out.push(obj);
    } catch {
      /* skip a bad line */
    }
  }
  return out;
}

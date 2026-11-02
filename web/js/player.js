// Timeline over events: replay (play, pause, speed, seek) and live follow.
// Both modes move one playhead and the state is always "every event whose
// time <= playhead, applied in seq order", so live and replay draw the same way.
//
// The playhead runs on display time. With pacing (the default) each gap
// between two events is stretched to at least the time the earlier one needs
// on screen (pacer) and cut to at most maxGap, so a scripted run that wrote
// everything in a fraction of a second and a real run that thought for
// minutes both replay at a watchable pace. Without pacing, display time is
// the recorded `t`. Either way the recorded value stays in `t_rec`, the seek
// bar, ?t= and window.hiraku.seek all use display time.

import { applyEvent, initialState } from "./state.js";

export const SPEEDS = [0.5, 1, 2, 4, 8];

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
    this.pacing = true;
    this.pacer = () => 0; // seconds an event needs on screen before the next one
    this.maxGap = 3; // a longer recorded gap is cut to this
  }

  get duration() {
    const last = this.events.at(-1);
    const end = Math.max(last ? last.t : 0, this.minEnd);
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
    } else {
      this.retime(from);
    }
    return added;
  }

  // Display times from index i on (earlier ones are already on screen).
  retime(i) {
    this.events.length = i;
    for (let k = i; k < this.raw.length; k += 1) {
      const e = this.raw[k];
      let t = e.t;
      if (k > 0) {
        let gap = e.t - this.raw[k - 1].t;
        if (this.pacing) {
          const lo = this.pacer(this.raw[k - 1]) ?? 0;
          gap = Math.min(Math.max(gap, lo), Math.max(this.maxGap, lo));
        }
        t = this.events[k - 1].t + Math.max(0, gap);
      }
      this.events.push({ ...e, t, t_rec: e.t });
    }
  }

  setPacing(on) {
    this.pacing = on;
    this.retime(0);
    this.rebuild();
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
      if (this.pacing) {
        // Real time, but wait at most maxGap past the newest event, so the next
        // one (placed at most maxGap later) is never already behind the playhead.
        this.playhead = Math.max(this.playhead, Math.min(this.playhead + dt, lastT + this.maxGap));
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
  constructor(runId, onEvents, { intervalMs = 500 } = {}) {
    this.runId = runId;
    this.onEvents = onEvents;
    this.intervalMs = intervalMs;
    this.offset = 0;
    this.timer = null;
    this.stopped = false;
    this.error = null;
  }

  async readOnce() {
    const url = `/api/runs/${encodeURIComponent(this.runId)}/events?offset=${this.offset}`;
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`${res.status} ${url}`);
    const body = await res.json();
    this.offset = body.next_offset;
    const more = body.next_offset < body.size;
    this.onEvents(body.events, body);
    return more;
  }

  // Read everything that is there now (in as many requests as needed).
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

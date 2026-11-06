// Reading a run's events from the server: /api/runs/<id>/events?offset=N&format=ndjson
// answers with the bytes of complete lines as they are in events.jsonl (the server does
// not parse them) and puts where to read next in headers. The lines are parsed here the
// way the server's JSON form (read_events) parses them, so both give the same events:
// split at \n, \r\n or \r, strip ASCII white space, skip empty lines, keep JSON objects,
// count everything else (and lines that are not UTF-8) in bad_lines.
// (One difference that cannot show up in practice: Python's json accepts NaN and
// Infinity, JSON.parse does not; the JSON form could not carry such a line either.)
// In a static export (source.js) the record is its light part, one gzipped file read whole; its
// heavy parts are read when drawn (heavy.js).
// Reading and parsing report how far they got (onProgress(fraction 0..1)) and give the page back
// every few tens of milliseconds, so a long record does not hold the page still while it is read.

import { STATIC, staticEventsPath, staticRunDir } from "./source.js";
import { attachHeavy, gunzipped, loadAll } from "./heavy.js";

const ASCII_SPACE = /^[ \t\n\r\v\f]+|[ \t\n\r\v\f]+$/g;
const LINE_BREAK = /\r\n|\r|\n/;

// JSON.parse itself allows space, tab, CR and LF around the value, so the strip is needed only
// when a line does not parse as it is (a vertical tab or form feed at an end, or only white space).
function parseLine(s, out) {
  let obj;
  try {
    obj = JSON.parse(s);
  } catch {
    const t = s.replace(ASCII_SPACE, "");
    if (!t) return 0; // an empty line: skipped, not bad
    if (t === s) return 1;
    try {
      obj = JSON.parse(t);
    } catch {
      return 1;
    }
  }
  if (obj && typeof obj === "object" && !Array.isArray(obj)) {
    out.push(obj);
    return 0;
  }
  return 1;
}

// {events, bad}: the JSON objects of the lines in `bytes` (a Uint8Array of whole lines).
export function parseEventLines(bytes) {
  const events = [];
  let bad = 0;
  let text = null;
  try {
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    /* some line is not UTF-8: decode line by line below, so only that line is bad */
  }
  if (text !== null) {
    for (const line of text.split(LINE_BREAK)) bad += parseLine(line, events);
    return { events, bad };
  }
  const dec = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
  let start = 0;
  for (let i = 0; i <= bytes.length; i++) {
    const b = i < bytes.length ? bytes[i] : 10;
    if (b !== 10 && b !== 13) continue;
    if (i > start) {
      let line = null;
      try {
        line = dec.decode(bytes.subarray(start, i));
      } catch {
        bad += 1;
      }
      if (line !== null) bad += parseLine(line, events);
    }
    start = i + 1;
  }
  return { events, bad };
}

const SLICE_MS = 25; // the longest a step of parsing keeps the page before giving it back (a line is one step)
const yieldNow = () => new Promise((r) => setTimeout(r, 0));

// parseEventLines in steps of at most SLICE_MS, awaiting between them; onProgress(fraction of the
// lines) after each step. A record with a line that is not UTF-8 is parsed at once (parseEventLines).
export async function parseEventLinesAsync(bytes, onProgress = null) {
  let text = null;
  try {
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    return parseEventLines(bytes);
  }
  const events = [];
  let bad = 0;
  const lines = text.split(LINE_BREAK);
  text = null;
  let t0 = performance.now();
  for (let i = 0; i < lines.length; i++) {
    bad += parseLine(lines[i], events);
    lines[i] = null;
    if (performance.now() - t0 > SLICE_MS) {
      onProgress?.((i + 1) / lines.length);
      await yieldNow();
      t0 = performance.now();
    }
  }
  onProgress?.(1);
  return { events, bad };
}

// An answer's body as bytes, with onProgress(bytes so far) while it comes.
async function bodyBytes(res, onProgress) {
  if (!onProgress || !res.body) return new Uint8Array(await res.arrayBuffer());
  const reader = res.body.getReader();
  const chunks = [];
  let n = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    n += value.length;
    onProgress(n);
  }
  const out = new Uint8Array(n);
  let at = 0;
  for (const c of chunks) {
    out.set(c, at);
    at += c.length;
  }
  return out;
}

// How far a read is: READ_SHARE of it the bytes coming, the rest the parse.
const READ_SHARE = 0.8;

// A static export (source.js STATIC): the light record in one read from data/runs/<id>/light.jsonl.gz,
// its events' heavy parts as getters (heavy.js). Offsets count the unpacked bytes; a read past the
// start finds nothing more (the record is finished).
async function fetchStaticEvents(runId, offset, onProgress) {
  if (offset > 0) return { events: [], next_offset: offset, size: offset, reset: false, bad_lines: 0, run_id: runId };
  const path = staticEventsPath(runId);
  const res = await fetch(path);
  if (!res.ok) {
    const err = new Error(`${res.status} ${path}`);
    err.status = res.status;
    err.serverError = res.status;
    throw err;
  }
  const total = Number(res.headers.get("Content-Length")) || 0;
  const packed = await bodyBytes(res, onProgress && total ? (n) => onProgress(READ_SHARE * Math.min(1, n / total)) : null);
  let bytes;
  try {
    bytes = await gunzipped(new Response(packed));
  } catch (e) {
    // (it came, but it does not unpack: the file is broken, not the network)
    const err = new Error(`broken ${path}: ${e?.message ?? e}`);
    err.broken = true;
    throw err;
  }
  const { events, bad } = await parseEventLinesAsync(bytes, onProgress && ((f) => onProgress(READ_SHARE + (1 - READ_SHARE) * f)));
  const base = staticRunDir(runId);
  for (const ev of events) attachHeavy(ev, base);
  return { events, next_offset: bytes.length, size: bytes.length, reset: false, bad_lines: bad, run_id: runId };
}

// One read from `offset`: {events, next_offset, size, reset, bad_lines} (the JSON form's fields).
// A failed request throws an Error "<status> <path>" that carries .status and .serverError
// (the server's error text, else the status). onProgress(fraction of the whole record read).
export async function fetchEvents(runId, offset, onProgress = null) {
  if (STATIC) return fetchStaticEvents(runId, offset, onProgress);
  const path = `/api/runs/${encodeURIComponent(runId)}/events?offset=${offset}`;
  const res = await fetch(`${path}&format=ndjson`, { cache: "no-store" });
  if (!res.ok) {
    const err = new Error(`${res.status} ${path}`);
    err.status = res.status;
    err.serverError = res.status;
    try {
      err.serverError = (await res.json()).error ?? res.status;
    } catch {
      /* not JSON */
    }
    throw err;
  }
  const h = (name) => Number(res.headers.get(name));
  const size = h("X-Hiraku-Size");
  const next = h("X-Hiraku-Next-Offset");
  // (this answer is the bytes from offset to next of size; its lines are parsed when it is in)
  const read = (n) => (size > 0 ? READ_SHARE * Math.min(1, (offset + n) / size) : 0);
  const bytes = await bodyBytes(res, onProgress && ((n) => onProgress(read(n))));
  const done = size > 0 ? Math.min(1, next / size) : 1;
  const { events, bad } = await parseEventLinesAsync(bytes, onProgress && ((f) => onProgress(READ_SHARE * done + (1 - READ_SHARE) * done * f)));
  return {
    events,
    next_offset: next,
    size,
    reset: res.headers.get("X-Hiraku-Reset") === "1",
    bad_lines: h("X-Hiraku-Bad-Lines") + bad,
    run_id: runId,
  };
}

// Every event there is now, read from the start (as many reads as it takes; stops at a
// half-written line). A failed read throws Error(the server's error text, else the status).
// In a static export every heavy part is read too (a page that reads the whole record: cut.html).
export async function fetchAllEvents(runId) {
  const events = [];
  let offset = 0;
  for (;;) {
    let body;
    try {
      body = await fetchEvents(runId, offset);
    } catch (e) {
      throw "serverError" in e ? new Error(e.serverError) : e;
    }
    for (const ev of body.events) events.push(ev);
    if (body.next_offset >= body.size || body.next_offset === offset) {
      if (STATIC) await loadAll(events);
      return events;
    }
    offset = body.next_offset;
  }
}

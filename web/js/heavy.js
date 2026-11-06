// The heavy parts of a static export's record (hiraku/webserve/export_static.py split_record): a
// mesh's nodes and elements, an analysis' fields, series, assembly... are files of their own under
// data/runs/<id>/heavy/ (named by the hash of what they hold), named in the light record's event as
// _heavy {key: "heavy/<hash>.json.gz"}.
//
// attachHeavy() puts each such key on the event's data as a getter. Read before its file is there,
// it asks for every part of that record (a record is drawn whole, or not yet) and answers the empty
// value ([] for a list, else undefined), as for a record without it; once read, the key is a plain
// value and every listener (onHeavy) hears it, so the screen draws again. Only what is drawn is read:
// ① and ② read none, ③ the shown analysis and its mesh, a replay what it comes to (prefetch).
// quietly(fn): reads inside fn answer the empty value without asking (a view that is hidden);
// tracked(fn): the same, and which records with parts not read yet fn read (the screen waits for
// them before it draws the 3D from them).

const listeners = new Set();
const parts = new Map(); // url -> { state: "wait" | "loading" | "ok" | "failed", targets: [{data, key}], asked }
const pendingKeys = new WeakMap(); // data -> Map(key -> url) of the keys not read yet
const lengths = new WeakMap(); // data -> {key: length} of the keys whose value is a list
const queue = []; // urls waiting for a slot, the asked-for first
const MAX_LOADING = 4;
let loading = 0;
let quiet = 0;
let touched = null; // the Set tracked() collects into
export const heavyStats = { asked: 0, read: 0, failed: 0, bytes: 0 };

export function onHeavy(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// Run fn with the heavy getters silent (they answer the empty value and ask for nothing).
export function quietly(fn) {
  quiet += 1;
  try {
    return fn();
  } finally {
    quiet -= 1;
  }
}

// Run fn quietly; returns [fn's value, the records (data objects) with parts not read that it read].
export function tracked(fn) {
  const outer = touched;
  const mine = new Set();
  touched = mine;
  try {
    return [quietly(fn), mine];
  } finally {
    touched = outer;
    if (outer) for (const d of mine) outer.add(d);
  }
}

// Has this record object (an event's data) keys still to be read?
export const heavyPending = (data) => !!data && typeof data === "object" && (pendingKeys.get(data)?.size ?? 0) > 0;

// Is every heavy part of this event read (an event without any: yes)?
export const heavyReady = (ev) => !ev?._heavy || !heavyPending(ev.data);

// The length of a list-valued key without reading it: the light record's while it is not read
// (null: not a heavy key of this record, or read already; then read the value itself).
export function pendingLength(data, key) {
  if (!pendingKeys.get(data)?.has(key)) return null;
  return lengths.get(data)?.[key] ?? null;
}

// The parts asked for and not read yet (for the screen's note).
export function heavyWaiting() {
  let n = 0;
  for (const p of parts.values()) if (p.state === "loading" || (p.state === "wait" && p.asked)) n += 1;
  return n;
}

// base: the run's folder (data/runs/<id>/), ending with "/".
export function attachHeavy(ev, base) {
  const refs = ev?._heavy;
  const data = ev?.data;
  if (!refs || !data || typeof data !== "object") return;
  const keys = new Map();
  const arrays = ev._lite?.arrays && typeof ev._lite.arrays === "object" ? ev._lite.arrays : {};
  for (const [key, ref] of Object.entries(refs)) {
    if (typeof ref !== "string" || Object.getOwnPropertyDescriptor(data, key)) continue;
    const url = base + ref;
    let p = parts.get(url);
    if (!p) parts.set(url, (p = { state: "wait", targets: [], asked: false }));
    const list = key in arrays;
    p.targets.push({ data, key, list });
    keys.set(key, url);
    Object.defineProperty(data, key, {
      configurable: true,
      enumerable: false, // (a copy or JSON of the event does not read it)
      get() {
        if (touched) touched.add(data);
        if (!quiet) askAll(data, true);
        return list ? [] : undefined;
      },
      set(value) {
        settle(data, key, value);
      },
    });
  }
  if (keys.size) {
    pendingKeys.set(data, keys);
    lengths.set(data, arrays);
  }
}

function settle(data, key, value) {
  Object.defineProperty(data, key, { configurable: true, enumerable: true, writable: true, value });
  pendingKeys.get(data)?.delete(key);
}

// Ask for every part of a record not read yet; first: before the others waiting.
export function askAll(data, first = false) {
  for (const url of pendingKeys.get(data)?.values() ?? []) ask(url, first);
}

// Ask for every part of these events (a replay coming to them); first: before the others waiting.
export function prefetch(events, first = false) {
  for (const ev of events) if (ev?._heavy) askAll(ev.data, first);
}

// Read every part of these events (for a page that needs all of them: cut.html). Throws when one
// cannot be read.
export async function loadAll(events) {
  const urls = new Set();
  for (const ev of events) for (const url of pendingKeys.get(ev?.data)?.values() ?? []) urls.add(url);
  await Promise.all([...urls].map((u) => read(u)));
  const failed = [...urls].filter((u) => parts.get(u).state === "failed");
  // (the parts that could not be read are empty: say so)
  if (failed.length) throw new Error(`${parts.get(failed[0]).error}`);
}

function ask(url, first) {
  const p = parts.get(url);
  if (!p || p.state !== "wait") return;
  const i = queue.indexOf(url);
  if (first) {
    p.asked = true;
    if (i >= 0) queue.splice(i, 1);
    queue.unshift(url);
  } else if (i < 0) queue.push(url);
  pump();
}

function pump() {
  while (loading < MAX_LOADING && queue.length) read(queue.shift());
}

function read(url) {
  const p = parts.get(url);
  if (p.state === "ok" || p.state === "failed") return Promise.resolve();
  if (p.promise) return p.promise;
  const i = queue.indexOf(url);
  if (i >= 0) queue.splice(i, 1);
  p.state = "loading";
  loading += 1;
  heavyStats.asked += 1;
  p.promise = (async () => {
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`${res.status} ${url}`);
      const bytes = await gunzipped(res);
      const text = new TextDecoder().decode(bytes);
      await idle(); // (the parse is one long step: after what the page has to draw now)
      const value = JSON.parse(text);
      for (const t of p.targets) settle(t.data, t.key, value);
      p.state = "ok";
      heavyStats.read += 1;
      heavyStats.bytes += bytes.length;
    } catch (e) {
      // (a part that cannot be read is empty from now on, so what waits for it draws without it)
      p.state = "failed";
      heavyStats.failed += 1;
      p.error = String(e?.message ?? e);
      for (const t of p.targets) settle(t.data, t.key, t.list ? [] : undefined);
    } finally {
      loading -= 1;
      p.promise = null;
      for (const fn of listeners) fn(p.state === "ok", url);
      pump();
    }
  })();
  return p.promise;
}

const idle = () => new Promise((r) => setTimeout(r, 0));

// The bytes of a gzipped answer, unpacked (a host that sent it with Content-Encoding: gzip has the
// browser unpack it already: then the bytes do not start with gzip's magic and are kept).
export async function gunzipped(res) {
  const buf = new Uint8Array(await res.arrayBuffer());
  if (buf[0] !== 0x1f || buf[1] !== 0x8b) return buf;
  const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

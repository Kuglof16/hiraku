// Reading a run's events from the server: /api/runs/<id>/events?offset=N&format=ndjson
// answers with the bytes of complete lines as they are in events.jsonl (the server does
// not parse them) and puts where to read next in headers. The lines are parsed here the
// way the server's JSON form (read_events) parses them, so both give the same events:
// split at \n, \r\n or \r, strip ASCII white space, skip empty lines, keep JSON objects,
// count everything else (and lines that are not UTF-8) in bad_lines.
// (One difference that cannot show up in practice: Python's json accepts NaN and
// Infinity, JSON.parse does not; the JSON form could not carry such a line either.)

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

// One read from `offset`: {events, next_offset, size, reset, bad_lines} (the JSON form's fields).
// A failed request throws an Error "<status> <path>" that carries .status and .serverError
// (the server's error text, else the status).
export async function fetchEvents(runId, offset) {
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
  const { events, bad } = parseEventLines(new Uint8Array(await res.arrayBuffer()));
  return {
    events,
    next_offset: h("X-Hiraku-Next-Offset"),
    size: h("X-Hiraku-Size"),
    reset: res.headers.get("X-Hiraku-Reset") === "1",
    bad_lines: h("X-Hiraku-Bad-Lines") + bad,
    run_id: runId,
  };
}

// Every event there is now, read from the start (as many reads as it takes; stops at a
// half-written line). A failed read throws Error(the server's error text, else the status).
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
    if (body.next_offset >= body.size || body.next_offset === offset) return events;
    offset = body.next_offset;
  }
}

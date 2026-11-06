// ④ 解析結果: one domain's analyses replayed inside the grid's tab (a tile pressed, or ?domain=).
// The span of a domain is the replay's time from its first prediction to its last comparison
// (display time, as the play bar), from the events only: a load case's domain is the one the
// records' load_cases give it (run.started, project.updated, load_cases.created); a comparison
// (no load_case_id in it) is its analysis' or prediction's load case. Within the
// span the load case shown is the domain's one last touched at the playhead (prediction,
// analysis, comparison or verdict), so the 3D and the 予測 → 解析 → 照合 cards follow it.

const KINDS = new Set(["prediction.recorded", "analysis.started", "analysis.finished", "comparison.recorded", "verdict.recorded"]);

// domain -> { domain, t0, t1, marks: [{ t, lc, kind }] } for each domain with any of KINDS.
// t0: the first prediction (else the first mark), t1: the last comparison (else the last mark).
export function domainSpans(events) {
  const dom = new Map();
  for (const ev of events) for (const c of ev.data?.load_cases ?? []) if (c?.load_case_id && c.domain) dom.set(c.load_case_id, c.domain);
  const out = {};
  const byRecord = new Map(); // analysis_id / prediction_id -> load case (a comparison names those, not the load case)
  for (const ev of events) {
    if (!KINDS.has(ev.kind)) continue;
    const d0 = ev.data ?? {};
    if (d0.load_case_id) {
      if (d0.analysis_id) byRecord.set(d0.analysis_id, d0.load_case_id);
      if (d0.prediction_id) byRecord.set(d0.prediction_id, d0.load_case_id);
    }
    const lc = d0.load_case_id ?? byRecord.get(d0.analysis_id) ?? byRecord.get(d0.prediction_id) ?? (d0.prediction_ids ?? []).map((id) => byRecord.get(id)).find(Boolean);
    const d = dom.get(lc);
    if (!d) continue;
    const sp = (out[d] ??= { domain: d, t0: null, t1: null, marks: [], pred: null, cmp: null });
    sp.marks.push({ t: ev.t, lc, kind: ev.kind });
    if (ev.kind === "prediction.recorded" && sp.pred === null) sp.pred = ev.t;
    if (ev.kind === "comparison.recorded") sp.cmp = ev.t;
  }
  for (const sp of Object.values(out)) {
    sp.t0 = sp.pred ?? sp.marks[0].t;
    sp.t1 = Math.max(sp.t0, sp.cmp ?? sp.marks.at(-1).t);
    delete sp.pred;
    delete sp.cmp;
  }
  return out;
}

// The domain's load case at t: the last one touched at or before t, else its first.
export function domainLcAt(span, t) {
  let lc = span.marks[0]?.lc ?? null;
  for (const m of span.marks) {
    if (m.t > t + 1e-9) break;
    lc = m.lc;
  }
  return lc;
}

// The playhead held inside the span.
export const clampToSpan = (span, t) => Math.max(span.t0, Math.min(span.t1, t));

// 「ビューアで見る」: one analysis opened in the result viewer (viewer/, the analysis department's) in a
// new tab, at viewer/index.html?data=<the analysis' viewer data>#<field>. The data comes from
// GET /api/runs/<run>/viewer/<analysis> (hiraku/webserve/viewer_link.py), or in the static site from
// data/viewer/<run>/<analysis>.json written beforehand.
//
// Whether the button can be pressed is decided from the tool's name (VIEWER_FIELD, the same table as
// viewer_link.py TOOL_FIELD; a test keeps them equal) and from GET /api/viewer: while the answers are
// provisional (the analysis department's for_analysis not there yet) they are the viewer's own sample
// of the field, which the line under the button says, and a field with no sample cannot be opened.

import { esc } from "./labels.js";

export const VIEWER_FIELD = {
  frame_static: "strength",
  frame_shell: "strength",
  modal: "nvh",
  modal_shell: "nvh_shell",
  crash: "crash",
  crash_beam: "crash",
  tilt: "handling",
  braking: "handling",
  lane_change: "handling",
  offroad_roll: "handling",
  launch: "powertrain",
  step_climb_dynamic: "powertrain",
  crosswind: "aero",
  fatigue: "fatigue",
  thermal: "thermal",
  drive_energy: "thermal",
  assembly: "assembly",
  rops_pushover: "rops",
};
export const ROPS_PREFIX = "rops_";
// (the same lines as viewer_link.py)
export const VIEWER_WORDS = {
  button: "ビューアで見る",
  newTab: "別のタブで開きます",
  noField: "この解析の種類はビューアにまだ無い",
  noSample: "ビューアの見本のデータがまだ無い",
  notOk: "この解析は通らなかったので絵が無い",
  sample: "見本 (この解析から作った絵ではない)",
};

// what GET /api/viewer said (until it answers: provisional, every sample assumed there)
const info = { provisional: true, missing: [], loaded: false };
export const viewerInfo = () => info;

const isStatic = () => typeof document !== "undefined" && !!document.querySelector('meta[name="hiraku-static"]');

// Ask the server (or the static site's data/viewer.json) once; then() runs after (to redraw).
export function loadViewerInfo(then) {
  if (info.loaded) return Promise.resolve(info);
  const url = isStatic() ? "data/viewer.json" : "/api/viewer";
  return fetch(url)
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => {
      if (j) Object.assign(info, { provisional: !!j.provisional, missing: j.missing ?? [] });
      info.loaded = true;
      if (then) then(info);
      return info;
    })
    .catch(() => info);
}

export function viewerFieldOf(tool) {
  if (!tool) return null;
  if (VIEWER_FIELD[tool]) return VIEWER_FIELD[tool];
  return tool.startsWith(ROPS_PREFIX) ? "rops" : null;
}

// {field, reason}: field null with the one line why the button cannot be pressed
export function viewerTarget(ana) {
  if (!ana) return { field: null, reason: VIEWER_WORDS.noField };
  if (ana.status === "failed") return { field: null, reason: VIEWER_WORDS.notOk };
  const field = viewerFieldOf(ana.tool);
  if (!field) return { field: null, reason: VIEWER_WORDS.noField };
  if (info.provisional && info.missing.includes(field)) return { field: null, reason: VIEWER_WORDS.noSample };
  return { field, reason: null };
}

// The run's id as the server knows it (the page's ?run=, else the record's run_id).
export function viewerRunId(s) {
  const q = typeof location !== "undefined" ? new URLSearchParams(location.search).get("run") : null;
  return q || s?.runId || null;
}

// The viewer's URL for one analysis (relative in the static site, so it works under any path).
export function viewerUrl(runId, analysisId, field) {
  const enc = encodeURIComponent;
  if (isStatic()) return `viewer/index.html?data=${enc(`../data/viewer/${runId}/${analysisId}.json`)}#${field}`;
  return `/viewer/index.html?data=${enc(`/api/runs/${runId}/viewer/${analysisId}`)}#${field}`;
}

// The button with its small notes, as HTML. A place (the workbench card, a tile's list row, the
// overview's data-role="viewer" cell) puts it where it wants.
export function viewerButtonHtml(runId, ana, opts = {}) {
  const t = viewerTarget(ana);
  const id = esc(ana?.analysis_id ?? "");
  const cls = `vlink${opts.compact ? " compact" : ""}`;
  if (!t.field || !runId) {
    return `<span class="${cls} off" data-role="viewer-link" data-ana="${id}"><button type="button" disabled>${VIEWER_WORDS.button}</button><span class="vwhy">${esc(t.reason ?? VIEWER_WORDS.noField)}</span></span>`;
  }
  const href = viewerUrl(runId, ana.analysis_id, t.field);
  return (
    `<span class="${cls}" data-role="viewer-link" data-ana="${id}" data-field="${esc(t.field)}">` +
    `<a class="vbtn" href="${esc(href)}" target="_blank" rel="noopener">${VIEWER_WORDS.button}</a>` +
    `<span class="vnote">${VIEWER_WORDS.newTab}</span>` +
    (info.provisional ? `<span class="vsample">${VIEWER_WORDS.sample}</span>` : "") +
    `</span>`
  );
}

// Put the button into every [data-role="viewer"] under root that names an analysis (data-ana); the
// overview's grid leaves such cells. s: the screen's state (runId, analyses).
export function fillViewerCells(root, s) {
  for (const el of root.querySelectorAll('[data-role="viewer"][data-ana]')) {
    el.innerHTML = viewerButtonHtml(viewerRunId(s), s.analyses?.[el.dataset.ana], { compact: true });
  }
}

// Where the screen reads what it GETs, in one place.
//
// Served by hiraku.webserve: the server's routes as they are (/api/runs, /api/shelf, ...).
// A static export (python -m hiraku.webserve.export_static) marks its pages with
// <meta name="hiraku-static" content="1">: then the same answers are files next to the page,
// written beforehand by the server's own functions, and read by relative paths, so the export
// works from any folder of a host (e.g. under /hiraku/). Nothing is POSTed there: the
// screen leaves out what would start, stop or send something (app.js, STATIC).

export const STATIC = typeof document !== "undefined" && document.querySelector('meta[name="hiraku-static"]')?.content === "1";

// the server's GET route -> the export's file (relative to the page)
const FIXED = {
  "/api/runs": "data/runs.json",
  "/api/launch": "data/launch.json",
  "/api/shelf": "data/shelf.json",
  "/api/assets": "data/assets.json",
  "/api/modules": "data/modules.json",
  "/api/formulas": "data/formulas.json",
  "/layout.json": "layout.json",
  "/pets/pet3d.js": "pets/pet3d.js",
};
const TOOLS_RE = /^\/api\/runs\/([^/]+)\/tools(?:\/([^/]+))?\/?$/;

// The export's file for a server path (null: the export has none).
export function staticPath(path) {
  const p = path.startsWith("/") ? path : `/${path}`;
  if (p in FIXED) return FIXED[p];
  const m = TOOLS_RE.exec(p);
  if (m) return m[2] ? `data/runs/${m[1]}/tools/${m[2]}` : `data/runs/${m[1]}/tools.json`;
  return null;
}

// What to fetch for a server GET path: the path itself under the server, the export's file in a static page.
export function getUrl(path) {
  return STATIC ? staticPath(path) ?? path.replace(/^\//, "") : path;
}

// A run's whole record in the export: events.jsonl gzipped (read in the browser, DecompressionStream).
export const staticEventsPath = (runId) => `data/runs/${encodeURIComponent(runId)}/events.jsonl.gz`;

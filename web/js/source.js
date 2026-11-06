// Where the screen reads what it GETs, in one place.
//
// Served by hiraku.webserve: the server's routes as they are (/api/runs, /api/shelf, ...).
// A static export (python -m hiraku.webserve.export_static) marks its pages with
// <meta name="hiraku-static" content="1">: then the same answers are files next to the page,
// written beforehand by the server's own functions, and read by relative paths, so the export
// works from any folder of a host (e.g. under /hiraku/). Nothing is POSTed there: the
// screen leaves out what would start, stop or send something (app.js, STATIC).

export const STATIC = typeof document !== "undefined" && document.querySelector('meta[name="hiraku-static"]')?.content === "1";

// An export's files' versions (export_static.py version_site: <script id="hiraku-versions">, path ->
// a short hash of the file): every file it reads carries ?v=<its version>, so a browser never mixes
// two exports' files. (The modules and style sheets have theirs written into their addresses.)
const VERSIONS = (() => {
  if (!STATIC) return {};
  try {
    return JSON.parse(document.getElementById("hiraku-versions")?.textContent || "{}");
  } catch {
    return {};
  }
})();
export function versioned(rel) {
  const v = VERSIONS[rel];
  return v ? `${rel}?v=${v}` : rel;
}

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
  return STATIC ? versioned(staticPath(path) ?? path.replace(/^\//, "")) : path;
}

// A run's folder in the export, and its light record (export_static.py split_record: every event, the
// heavy parts left out; heavy.js reads those from the folder when they are drawn).
export const staticRunDir = (runId) => `data/runs/${encodeURIComponent(runId)}/`;
export const staticEventsPath = (runId) => versioned(`data/runs/${runId}/light.jsonl.gz`).replace(`data/runs/${runId}/`, staticRunDir(runId));

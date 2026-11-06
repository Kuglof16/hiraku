// The crash tool's note on the break angle's edge (an analysis' assumptions entry that starts with
// "break angle band": hiraku/solvers/k2_vehicle/shelf/crash_beam.py break_band_note on the K2 branch).
// The run is made again with the break angle a little higher; the note says whose verdict changes
// between the runs, and which members stayed unbroken within the band under the break angle.
// The screen reads it in one place (parseBreakBand) and says it in one line each:
//  - 境目: on the verdict of a quantity the note names as changing (orange, not red: it is not a NG)
//  - 参考: a member a little under the break angle, how far (computed here from the note's angles)
import { esc, fmt, judgedWords, memberName } from "./labels.js";

export const EDGE_NOTE = "break angle band";

const NUM = String.raw`[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?`;

// text -> {flips: [quantity], bandDeg, near: [{member, deg}], limDeg} | null (not the note).
// flips: the first word of each "<quantity> <op> <limit>" the note says changes inside the band;
// bandDeg: the size of the first change of the angle the run was made again with (+1 deg -> 1);
// near: the members unbroken with a hinge within the band under limDeg (the break angle), each with
// its hinge's angle (deg).
export function parseBreakBand(text) {
  const t = String(text ?? "").trim();
  if (!t.startsWith(EDGE_NOTE)) return null;
  const again = new RegExp(String.raw`^${EDGE_NOTE} \([^)]*?(${NUM}) deg`).exec(t);
  const bandDeg = again ? Math.abs(Number(again[1])) : null;
  const fl = /C1's verdict changes inside the band \(([^)]*)\)/.exec(t);
  const flips = fl ? fl[1].split(",").map((x) => x.trim().split(/\s+/)[0]).filter(Boolean) : [];
  let near = [];
  let limDeg = null;
  const un = new RegExp(String.raw`Unbroken at (${NUM}) deg with a hinge within (${NUM}) deg under it: (.*?)\.(?:\s|$)`).exec(t);
  if (un) {
    limDeg = Number(un[1]);
    near = un[3].split(",").map((x) => new RegExp(String.raw`^\s*(\S+) (${NUM}) deg\s*$`).exec(x)).filter(Boolean)
      .map((m) => ({ member: m[1], deg: Number(m[2]) }));
  }
  return { flips, bandDeg, near, limDeg };
}

// The note of an analysis, parsed (the first assumptions entry that is one), or null.
export function analysisEdge(ana) {
  for (const a of ana?.assumptions ?? []) {
    const p = parseBreakBand(a);
    if (p) return p;
  }
  return null;
}

// Whether the note says the verdict of this quantity changes inside the band.
export const edgeFlips = (edge, quantity) => !!edge && quantity != null && edge.flips.includes(String(quantity));

const degWord = (d) => (d == null ? "?" : fmt(d, 4));

// The 境目 line (plain text).
export const edgeText = (edge) => `境目: 壊れる角度が ${degWord(edge.bandDeg)}° 違うと判定が変わる (計算の最後の桁でも変わりうる)`;

// The 参考 line (plain text), or null without a member near the edge. The gap: the break angle
// minus the member's hinge angle, 2 decimals.
export function nearText(edge, design) {
  if (!edge?.near?.length || edge.limDeg == null) return null;
  const each = edge.near.map((n) => `${memberName(design, n.member)}、あと ${(edge.limDeg - n.deg).toFixed(2)} °`);
  return `参考: もう少しで壊れる部材がある (${each.join(" / ")})`;
}

// The lines as HTML (orange: app.css .edge-line), each with the record's words as the tooltip.
export function edgeLineHtml(edge, raw = "") {
  return `<div class="edge-line" data-role="edge"${raw ? ` title="${esc(raw)}"` : ""}>${esc(edgeText(edge))}</div>`;
}
export function nearLineHtml(edge, design) {
  const t = nearText(edge, design);
  return t ? `<div class="edge-near" data-role="edge-near">${esc(t)}</div>` : "";
}

// One assumption in words for the screen: the note as one Japanese line (境目 when a verdict
// changes, else 参考 when a member is near, else the record's words), the others as written
// (English is not translated) with the judged words replaced.
export function assumptionText(text, design) {
  const p = parseBreakBand(text);
  if (p) {
    if (p.flips.length) return edgeText(p);
    const n = nearText(p, design);
    if (n) return n;
  }
  return judgedWords(String(text ?? ""));
}

// The raw note of an analysis (for a tooltip), or "".
export const edgeRaw = (ana) => (ana?.assumptions ?? []).find((a) => parseBreakBand(a)) ?? "";

// ?zoom=<name or CSS selector>:<seconds> — from that time of the replay the screen moves in on
// one element (a CSS transform on the stage, so the words are drawn again at the larger size).
// Decided by the replay's time only: the same ?t= gives the same picture.
//   names: asm-tile (④'s assembly tile), cmp (③ the comparison and its verdict, card-cmp)
// The selector and the seconds are split at the last ":" (a selector may hold ":" itself).

export const ZOOM_NAMES = {
  "asm-tile": '.tile[data-domain="assembly"]',
  cmp: "#card-cmp",
};
export const ZOOM_IN_S = 1.2; // seconds to move in
const FILL = 0.8; // the element takes at most this part of the stage's width or height
const MAX_SCALE = 3;

/** "cmp:12.5" -> { selector: "#card-cmp", at: 12.5 }; null when absent or not understood. */
export function parseZoom(text) {
  if (!text) return null;
  const cut = text.lastIndexOf(":");
  if (cut <= 0) return null;
  const at = Number(text.slice(cut + 1));
  const what = text.slice(0, cut).trim();
  if (!Number.isFinite(at) || !what) return null;
  return { selector: ZOOM_NAMES[what] ?? what, at };
}

const ease = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));

/** The stage's own transform followed by the zoom at time t (stage px, origin 0 0). */
export function zoomTransform(stage, base, zoom, t, w, h) {
  const e = zoom ? ease((t - zoom.at) / ZOOM_IN_S) : 0;
  stage.style.transform = base;
  if (e <= 0) return base;
  let el = null;
  try {
    el = stage.querySelector(zoom.selector);
  } catch {
    return base; // not a selector
  }
  if (!el || !el.checkVisibility()) return base;
  const s = stage.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  const k0 = s.width / w; // the stage's own scale
  const cx = (r.left + r.width / 2 - s.left) / k0;
  const cy = (r.top + r.height / 2 - s.top) / k0;
  const k = Math.max(1, Math.min(MAX_SCALE, (FILL * w) / (r.width / k0), (FILL * h) / (r.height / k0)));
  const kp = 1 + (k - 1) * e;
  const px = w / 2 + (cx - w / 2) * e;
  const py = h / 2 + (cy - h / 2) * e;
  const out = `${base} translate(${w / 2}px, ${h / 2}px) scale(${kp}) translate(${-px}px, ${-py}px)`;
  stage.style.transform = out;
  return out;
}

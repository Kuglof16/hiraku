// Banded contour colours (blue -> red), shared by the 3D view, the A6 tiles and the legend.

export const BANDS = 10;
const STOPS = ["#2b59c3", "#2fa7d6", "#4cbf7a", "#e8c547", "#e9893a", "#c8402a"];
export const OVER_COLOR = "#6d1b3b"; // above the allowable value
export const GRAY = "#d8d2c4";

function hexToRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function rgbToHex([r, g, b]) {
  return "#" + [r, g, b].map((x) => Math.round(x).toString(16).padStart(2, "0")).join("");
}

function lerpStops(u) {
  const x = Math.max(0, Math.min(1, u)) * (STOPS.length - 1);
  const i = Math.min(STOPS.length - 2, Math.floor(x));
  const f = x - i;
  const a = hexToRgb(STOPS[i]);
  const b = hexToRgb(STOPS[i + 1]);
  return rgbToHex(a.map((v, k) => v + (b[k] - v) * f));
}

export const BAND_COLORS = Array.from({ length: BANDS }, (_, i) => lerpStops((i + 0.5) / BANDS));

// Colour of v on a [min, max] scale; values above max are OVER_COLOR.
export function colorOf(v, max, min = 0) {
  if (v === null || v === undefined || !Number.isFinite(v) || !(max > min)) return GRAY;
  if (v > max + Math.abs(max) * 1e-9) return OVER_COLOR;
  const i = Math.min(BANDS - 1, Math.max(0, Math.floor(((v - min) / (max - min)) * BANDS)));
  return BAND_COLORS[i];
}

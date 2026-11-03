// What the cuts N11, N12 and N13 share (js/cut_n11.js, cut_n12.js, cut_n13.js): a panel
// placed on a box of layout.json, its title, and the steps of time. Positions and sizes
// come from the box; the colours from the cut's colors.

import { clearOfBand } from "./compose.js";
import { esc } from "./labels.js";

export const PANEL_BORDER_PX = 2;
export const PANEL_RADIUS_PX = 12;
export const GREEN = "#2f7d4f"; // the pages' colour of a pass (as .ok in cut.html)
export const RED = "#c8402a"; // and of a miss (.miss)
export const MUTED = "#8a857a";

// A panel on box `name` of the cut's section (data-box "<cut>.<name>"), with its title
// (none when title is null) at the box's title_px; marked data-outside when it reaches the
// subtitle band.
export function panel(stage, layout, cut, name, title, story) {
  const sp = layout[cut];
  const r = sp[name];
  const el = document.createElement("div");
  el.className = `cut-panel ${cut}-${name}`;
  el.dataset.box = `${cut}.${name}`;
  Object.assign(el.style, {
    left: `${r.x}px`, top: `${r.y}px`, width: `${r.w}px`, height: `${r.h}px`,
    background: sp.colors.panel, border: `${PANEL_BORDER_PX}px solid ${sp.colors.rule}`, borderRadius: `${PANEL_RADIUS_PX}px`,
  });
  if (!clearOfBand(r, layout)) el.dataset.outside = "1";
  if (title !== null && title !== undefined) {
    const px = Math.max(layout.min_text_px, r.title_px ?? r.text_px, story.bodyPx);
    el.insertAdjacentHTML("beforeend", `<h3 class="cut-title" style="font-size:${px}px">${esc(title)}</h3>`);
  }
  stage.append(el);
  return el;
}

// The size of text to be read in a box: its text_px, at least the story's body text.
export const textPx = (layout, box, story) => Math.max(layout.min_text_px, box.text_px, story.bodyPx);

// Shown from time `at` on (visibility, so it keeps its place).
export function showFrom(el, t, at) {
  el.style.visibility = t >= at ? "visible" : "hidden";
  return t >= at;
}

// n times spread evenly over [a, b): a, a + (b - a) / n, ...
export const spread = (a, b, n) => Array.from({ length: n }, (_, i) => a + (i * (b - a)) / Math.max(1, n));

// A setting of the load case that came from the request as written: a parameter
// ({value, unit}) equal to one of the load case's requirements (project.requirements, by
// lc.requirement_keys). { name: the requirement's name, value, unit } or null.
export function requestedSetting(s, lc) {
  const reqs = s.project?.requirements ?? [];
  for (const key of lc?.requirement_keys ?? []) {
    const r = reqs.find((x) => x.key === key);
    if (!r || typeof r.value !== "number") continue;
    for (const p of Object.values(lc.params ?? {})) {
      if (p && typeof p === "object" && p.value === r.value && p.unit === r.unit) return { name: r.name ?? null, value: r.value, unit: r.unit };
    }
  }
  return null;
}

// The name of a tool for viewers: its title before any bracket ("介護ベッドの使う回数の疲労 (…)").
export const toolTitle = (tool) => String(tool?.title ?? "").replace(/\s*[（(].*$/, "") || null;

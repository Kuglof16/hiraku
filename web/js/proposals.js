// A design split into modules (docs/contract.md, モジュールに分けた設計): the module colours and one
// round of revision in words. In a round each module's designer proposes changes to its own
// module (design.change_proposed, data module_change) and the packager builds them into the next
// version (design.created with module_changes). What the screen shows of a round comes from
// those records at the replay's time (state.js keeps them), so ?t= draws the same.
//
// No three.js here: state.js uses proposalText for the log lines.

import { agentName, designNumberName, esc, fmt, moduleName, unitLabel } from "./labels.js";

// The module colours: pet3d.js v10 ROLES (the rows with a module) when the page has them, else
// the same values here. package is the packager's (its pet's colour, app.js PET_COLORS.packager).
const MODULE_COLORS = {
  package: "#8e6bbf",
  frame: "#2b6e40",
  suspension: "#8db33a",
  steering: "#e2759f",
  occupant: "#917430",
  powertrain: "#7d2240",
  body: "#57b8a0",
};
export const OUTSIDE_COLOR = "#e0201a"; // a part outside its module's space

export function moduleColor(m) {
  return readable(petModuleColor(m));
}

function petModuleColor(m) {
  const roles = globalThis.window?.HirakuPets?.ROLES;
  const row = Array.isArray(roles) ? roles.find((r) => r.module === m && r.color) : null;
  if (row) return row.color;
  if (m === "package") {
    const p = Array.isArray(roles) ? roles.find((r) => r.id === "packager" && r.color) : null;
    if (p) return p.color;
  }
  return MODULE_COLORS[m] ?? "#9a948a";
}

// A colour too dark to tell apart on the 3D's shaded parts (relative luminance under MIN_LUMINANCE,
// WCAG's formula) is mixed toward white, a step at a time, until it reaches it (hue kept, so a dark
// red does not turn into the bright red of OUTSIDE_COLOR). Screen choice: the pet keeps its own
// colour; the 3D and the legend use this one. Others as they are.
export const MIN_LUMINANCE = 0.1;
const lin = (c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
export function luminance(hex) {
  const n = parseInt(String(hex).slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((x) => lin(x / 255));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function readable(hex) {
  if (!/^#[0-9a-f]{6}$/i.test(String(hex)) || luminance(hex) >= MIN_LUMINANCE) return hex;
  const n = parseInt(hex.slice(1), 16);
  const rgb = [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  const mix = (t) => "#" + rgb.map((v) => Math.round(v + (255 - v) * t).toString(16).padStart(2, "0")).join("");
  let t = 0;
  while (t < 1 && luminance(mix(t)) < MIN_LUMINANCE) t += 0.02;
  return mix(t);
}

// "前のばねの固さ 25 → 30 N/mm" for each changed variable (before: the base design's value),
// "part_mounts を brackets に" for each choice.
export function changeWords(chg, base) {
  const out = [];
  for (const [name, q] of Object.entries(chg.changes ?? {})) {
    // the design's variable_names, else the screen's table (labels.js VARIABLE_JA), else the id
    const said = designNumberName(`variables.${name}`, base) ?? name;
    const was = base?.variables?.[name];
    const u = unitLabel(q.unit ?? was?.unit ?? "");
    const unit = u && u !== "1" ? ` ${u}` : "";
    out.push(was && typeof was.value === "number" ? `${said} ${fmt(was.value)} → ${fmt(q.value)}${unit}` : `${said}を ${fmt(q.value)}${unit} に`);
  }
  for (const [name, v] of Object.entries(chg.choices ?? {})) out.push(`${name} を ${String(v)} に`);
  return out;
}

// The log line of a proposal (after the designer's name).
export function proposalText(chg, base) {
  const what = changeWords(chg, base).join("・");
  return `${what || "変更"} を提案した${chg.reason ? `。理由: ${chg.reason}` : ""}`;
}

// The proposals not built into a version yet (design.created's module_changes).
export function pendingProposals(s) {
  const built = new Set(Object.values(s.designs).flatMap((d) => (d.module_changes ?? []).map((c) => c.change_id)));
  return (s.proposals ?? []).filter((p) => !built.has(p.change_id));
}

// One row: the designer (with the module's colour), what it changes and why.
function rowHtml(chg, base) {
  const who = agentName(chg.agent_id ?? `designer.${chg.module_id}`);
  return (
    `<li data-change="${esc(chg.change_id)}" data-module="${esc(chg.module_id)}"><span class="mdot" style="--mod:${moduleColor(chg.module_id)}"></span>` +
    `<span class="pw"><b>${esc(who)}</b>: ${esc(changeWords(chg, base).join("・") || "変更")}</span>` +
    (chg.reason ? `<span class="pr">${esc(chg.reason)}</span>` : "") +
    `</li>`
  );
}

export const ROUND_SHOW_S = 12; // seconds of replay a built round stays on the workbench

// The round on the workbench at the replay's time (playhead): the proposals waiting for the
// packager, else (for ROUND_SHOW_S after it) the version the packager built from them. "" when none.
export function roundCardHtml(s, playhead) {
  const pending = pendingProposals(s);
  if (pending.length) {
    const base = s.designs[pending[0].baseVersion] ?? s.designs[s.currentVersion];
    const who = new Set(pending.map((p) => p.agent_id)).size;
    return (
      `<div class="rc-head" data-round="pending">版 ${esc(pending[0].baseVersion ?? s.currentVersion)} を直す提案: 設計役 ${who} 人が並べて出した</div>` +
      `<ol class="rc-list">${pending.map((p) => rowHtml(p, base)).join("")}</ol>` +
      `<div class="rc-foot">${esc(agentName("packager"))}が 1 つの版にまとめるのを待っている</div>`
    );
  }
  const v = s.currentVersion;
  const d = v != null ? s.designs[v] : null;
  const list = d?.module_changes ?? [];
  const t = s.builtT?.[v];
  if (!list.length || t == null || playhead - t > ROUND_SHOW_S) return "";
  const base = s.designs[d.parent_version];
  return (
    `<div class="rc-head" data-round="built" data-version="${v}">${esc(agentName("packager"))}が ${list.length} つの提案を版 ${v} にまとめた</div>` +
    `<ol class="rc-list">${list.map((c) => rowHtml(c, base)).join("")}</ol>`
  );
}

// The grid head's line for a version built from proposals: "版 1 → 2: 足まわり 前のばねの固さ 25 → 30・…".
export function builtLine(s, v) {
  const d = s.designs[v];
  const list = d?.module_changes ?? [];
  if (!list.length) return "";
  const base = s.designs[d.parent_version];
  return `版 ${d.parent_version} → ${v} で${esc(agentName("packager"))}がまとめた ${list.length} つの提案: ` + list.map((c) => `${esc(moduleName(c.module_id))} ${esc(changeWords(c, base).join("・"))}`).join(" ／ ");
}

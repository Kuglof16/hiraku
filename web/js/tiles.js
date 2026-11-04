// A6: the eight tiles (seven domains + assembly) of one design version, the
// version flow and the round-limit banner. Each tile draws a side view of the
// design (seen from the left: front on the left) coloured by the domain's
// latest analysis (stress / damage / temperature / mode shape), its
// interferences in red, and a small chart of its first series group. Every
// number printed is a recorded value.

import { DOMAINS, DOMAIN_JA, esc, fmt, fmtVU, unitLabel } from "./labels.js";
import { OVER_COLOR, colorOf } from "./colormap.js";
import { FIELD_JA, atFrame, contourOf, fieldsOf, frameIndex, overOf } from "./fields.js";
import { abscissa, channelsOf, chartGroups, extent, wheelName, xSeriesOf } from "./series.js";
import { domainVerdicts, isSafeSide, judgementState, roundsUsed } from "./state.js";
import { gridPlaceOf, petRowHtml } from "./agent_pets.js";
import { heldBandHtml, heldCases } from "./held.js";

const W = 300;
const H = 150;
const PAD = 14;
const SPARK = { w: 300, h: 64 };
const LINE_COLORS = ["#2b59c3", "#8e6bbf", "#2f7d4f", "#e9893a", "#c8402a", "#2fa7d6"];
const STATE_WORD = { green: "緑 ✓", red: "赤", pending: "まだ", off: "選んでいない" };
// Top row: domains drawn as contours; bottom row: as charts, then assembly (interference).
export const TILE_ORDER = ["crash", "nvh", "strength", "thermal", "handling", "powertrain", "aero", "assembly"];

// A part's name for people (before any bracket), or the id of a member or anything else.
function thingName(design, id) {
  const p = (design?.parts ?? []).find((x) => x.id === id);
  return p ? String(p.name ?? id).replace(/\s*[（(].*$/, "") || id : id;
}

// The latest analysis (status ok) of a domain for a version, with its load case.
export function domainAnalysis(s, domain, version) {
  const cases = (s.project?.load_cases ?? []).filter((c) => c.domain === domain);
  const ids = new Set(cases.map((c) => c.load_case_id));
  const list = s.order.analyses.map((id) => s.analyses[id]).filter((a) => a.status === "ok" && ids.has(a.load_case_id));
  const ana = list.filter((a) => a.design_version === version).at(-1) ?? null;
  return ana ? { ana, loadCase: cases.find((c) => c.load_case_id === ana.load_case_id) ?? null } : { ana: null, loadCase: cases[0] ?? null };
}

function sideView(design) {
  const pts = [];
  const nodes = Object.fromEntries((design.nodes ?? []).map((n) => [n.id, n.xyz_mm]));
  for (const p of Object.values(nodes)) pts.push([p[0], p[2]]);
  for (const s of design.supports ?? []) {
    const r = s.radius_mm ?? 0;
    pts.push([s.position_mm[0] - r, s.position_mm[2] - r], [s.position_mm[0] + r, s.position_mm[2] + r]);
  }
  for (const p of design.parts ?? []) {
    const c = p.box.center_mm;
    const z = p.box.size_mm;
    pts.push([c[0] - z[0] / 2, c[2] - z[2] / 2], [c[0] + z[0] / 2, c[2] + z[2] / 2]);
  }
  pts.push([0, 0]);
  const xs = pts.map((p) => p[0]);
  const zs = pts.map((p) => p[1]);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  const z0 = Math.min(0, ...zs);
  const z1 = Math.max(...zs);
  const k = Math.min((W - 2 * PAD) / (x1 - x0 || 1), (H - 2 * PAD) / (z1 - z0 || 1));
  const ox = (W - (x1 - x0) * k) / 2;
  const oz = (H - (z1 - z0) * k) / 2;
  // Seen from the left side: forward (+x) is to the left of the picture.
  const X = (x) => W - ox - (x - x0) * k;
  const Y = (z) => H - oz - (z - z0) * k;
  return { X, Y, k, nodes, x0, x1 };
}

function partRects(design, view, ct, interfering) {
  const out = [];
  for (const p of design.parts ?? []) {
    if (p.kind === "wheel") continue;
    const c = p.box.center_mm;
    const z = p.box.size_mm;
    const x = Math.min(view.X(c[0] + z[0] / 2), view.X(c[0] - z[0] / 2));
    const y = view.Y(c[2] + z[2] / 2);
    const w = z[0] * view.k;
    const h = z[2] * view.k;
    const v = ct?.parts?.[p.id];
    const hit = interfering.has(p.id);
    const fill = typeof v === "number" ? (overOf(ct, p.id, v) ? OVER_COLOR : colorOf(v, ct.max, ct.min)) : hit ? "#f3c9bd" : "#efe9dc";
    const stroke = hit ? "#c8402a" : typeof v === "number" ? "#1d1d1f" : "#c9c3b6";
    out.push(`<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${w.toFixed(1)}" height="${h.toFixed(1)}" fill="${fill}" stroke="${stroke}" stroke-width="${hit ? 2 : 1}" opacity="${typeof v === "number" || hit ? 0.9 : 0.7}"/>`);
  }
  return out.join("");
}

function memberLines(design, view, ct, interfering) {
  const out = [];
  for (const m of design.members ?? []) {
    const a = view.nodes[m.node_a];
    const b = view.nodes[m.node_b];
    if (!a || !b) continue;
    let color = "#1d1d1f";
    const v = ct?.values?.[m.id];
    const va = ct?.nodes?.[m.node_a];
    const vb = ct?.nodes?.[m.node_b];
    if (typeof v === "number") color = colorOf(v, ct.max, ct.min);
    else if (typeof va === "number" && typeof vb === "number") color = colorOf((va + vb) / 2, ct.max, ct.min);
    if (interfering.has(m.id)) color = "#c8402a";
    const mode = ct?.modes ? ` data-a="${esc(m.node_a)}" data-b="${esc(m.node_b)}"` : "";
    out.push(`<line x1="${view.X(a[0]).toFixed(1)}" y1="${view.Y(a[2]).toFixed(1)}" x2="${view.X(b[0]).toFixed(1)}" y2="${view.Y(b[2]).toFixed(1)}" stroke="${color}" stroke-width="3" stroke-linecap="round"${mode}/>`);
  }
  return out.join("");
}

function wheels(design, view) {
  const seen = new Set();
  return (design.supports ?? [])
    .filter((s) => {
      const key = `${s.position_mm[0]}|${s.position_mm[2]}`;
      if (seen.has(key)) return false; // left and right wheels fall on the same circle
      seen.add(key);
      return true;
    })
    .map((s) => {
      const r = (s.radius_mm ?? 0) * view.k;
      return `<circle cx="${view.X(s.position_mm[0]).toFixed(1)}" cy="${view.Y(s.position_mm[2]).toFixed(1)}" r="${r.toFixed(1)}" fill="#fbf9f4" stroke="#1d1d1f" stroke-width="2"/><circle cx="${view.X(s.position_mm[0]).toFixed(1)}" cy="${view.Y(s.position_mm[2]).toFixed(1)}" r="${(r * 0.4).toFixed(1)}" fill="none" stroke="#1d1d1f" stroke-width="1"/>`;
    })
    .join("");
}

function partValueTags(design, view, ct) {
  if (!ct?.parts) return "";
  return (design.parts ?? [])
    .filter((p) => typeof ct.parts[p.id] === "number")
    .map((p) => {
      const c = p.box.center_mm;
      const over = overOf(ct, p.id, ct.parts[p.id]);
      const name = String(p.name ?? p.id).replace(/\s*[（(].*$/, "");
      return `<text x="${view.X(c[0]).toFixed(1)}" y="${(view.Y(c[2] + p.box.size_mm[2] / 2) - 4).toFixed(1)}" class="tv${over ? " over" : ""}">${esc(name)} ${esc(fmtVU(ct.parts[p.id], ct.field.unit, 3))}</text>`;
    })
    .join("");
}

// The first chart group of an analysis' series (against time, else against another quantity).
function sparkline(ana) {
  const timed = channelsOf(ana);
  const ch = timed.size ? timed : xSeriesOf(ana);
  const g = chartGroups(ch)[0];
  if (!g) return "";
  const series = g.channels.slice(0, 6).map((n) => ch.get(n));
  const xs = series.map((s) => extent(abscissa(s)));
  const ys = series.map((s) => extent(s.values));
  const t0 = Math.min(...xs.map((e) => e[0]));
  const t1 = Math.max(...xs.map((e) => e[1]));
  const lo = Math.min(...ys.map((e) => e[0]));
  const hi = Math.max(...ys.map((e) => e[1]));
  const X = (t) => 30 + ((t - t0) / (t1 - t0 || 1)) * (SPARK.w - 36);
  const Y = (v) => SPARK.h - 6 - ((v - lo) / (hi - lo || 1)) * (SPARK.h - 16);
  const lines = series
    .map((s, i) => {
      const a = abscissa(s);
      const step = Math.max(1, Math.ceil(a.length / 150));
      const pts = [];
      for (let k = 0; k < a.length; k += step) pts.push(`${X(a[k]).toFixed(1)},${Y(s.values[k]).toFixed(1)}`);
      return `<polyline points="${pts.join(" ")}" fill="none" stroke="${LINE_COLORS[i % LINE_COLORS.length]}" stroke-width="1.8"/>`;
    })
    .join("");
  const names = series.map((s) => {
    const dot = s.channel.indexOf(".");
    return dot > 0 ? wheelName(s.channel.slice(dot + 1)) : s.channel;
  });
  return (
    `<svg class="spark" viewBox="0 0 ${SPARK.w} ${SPARK.h}" width="100%" preserveAspectRatio="xMidYMid meet">` +
    `<line x1="30" y1="${SPARK.h - 6}" x2="${SPARK.w - 6}" y2="${SPARK.h - 6}" stroke="#1d1d1f" stroke-width="1"/>` +
    `<line x1="30" y1="6" x2="30" y2="${SPARK.h - 6}" stroke="#1d1d1f" stroke-width="1"/>` +
    `<text x="26" y="12" class="ax">${esc(fmt(hi, 3))}</text><text x="26" y="${SPARK.h - 6}" class="ax">${esc(fmt(lo, 3))}</text>` +
    `${lines}<text x="34" y="12" class="lg">${esc(g.ja)} [${esc(unitLabel(g.unit) || "1")}]・${esc(names.slice(0, 4).join("・"))}</text></svg>`
  );
}

function tileSvg(s, domain, design, info) {
  const { ana, loadCase } = info;
  const field = fieldsOf(ana, { mesh: false })[0];
  // a field with frames (a time history): the moment with the largest value, as the workbench
  // shows it when not playing (the caption names the moment)
  const whole = field ? contourOf(ana, field, loadCase?.criterion) : null;
  const ct = whole?.frames ? atFrame(whole, frameIndex(whole, null)) : whole;
  const interfering = new Set((ana?.interferences ?? []).flatMap((x) => [x.a, x.b]));
  const view = sideView(design);
  const ground = `<line x1="4" y1="${view.Y(0).toFixed(1)}" x2="${W - 4}" y2="${view.Y(0).toFixed(1)}" stroke="#c9c3b6" stroke-width="1.5"/>`;
  // A crash test hits a wall in front of the vehicle.
  const wall = domain === "crash" ? `<rect x="2" y="${(view.Y(0) - 90).toFixed(1)}" width="9" height="90" fill="#d8d2c4" stroke="#1d1d1f"/>` : "";
  const body =
    `<svg class="side" viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet" data-domain="${domain}">` +
    ground +
    wall +
    partRects(design, view, ct, interfering) +
    memberLines(design, view, ct, interfering) +
    wheels(design, view) +
    partValueTags(design, view, ct) +
    `</svg>`;
  return { body, ct, view };
}

// The tile's pet: its domain's agent when the record has it (agent_pets.js), else none; without
// pets the circle as before.
function tilePet(s, d, pets) {
  if (!pets) return `<span class="pet" style="--pet:#4cbf7a"></span>`;
  const id = s.agentOrder.find((x) => gridPlaceOf(x) === d);
  if (!id || !pets.has(id)) return "";
  return `<span class="say tilesay" data-say="${esc(id)}"></span><span class="petslot tilepet" data-pet-slot="${esc(id)}"></span>`;
}

export class TileBoard {
  constructor(head, tiles) {
    this.head = head;
    this.tiles = tiles;
    this.modeViews = []; // { svg lines, view, modes, scale } for mode shapes to swing
    this.key = null;
  }

  // pets (js/agent_pets.js, or null without them): { has(id), order (the agents in the list's
  // order) }. With pets, each domain's agent stands in its tile's corner (when the record has
  // that agent) and the other agents in the head; without, the tiles keep their circles.
  render(s, pets = null) {
    const v = s.currentVersion;
    const reds = DOMAINS.filter((d) => judgementState(s, d).state === "red");
    const flow = s.versions
      .map((ver) => {
        const r = DOMAINS.filter((d) => s.judgements[`${ver}|${d}`]?.state === "red").length;
        return `<span class="vbox ${r ? "red" : "green"} ${ver === v ? "cur" : ""}">版 ${ver} 赤 ${r}</span>`;
      })
      .join(`<span class="varrow">→</span>`);
    const design = v != null ? s.designs[v] : null;
    const change = design && design.parent_version != null && design.change_reason ? `<span class="change">版 ${design.parent_version} → ${v} で変えたこと: ${esc(design.change_reason)}</span>` : "";
    this.head.innerHTML =
      `<h2>版 ${v ?? "—"} の判定</h2><span class="muted">繰り返し ${roundsUsed(s)} / ${s.project?.round_limit ?? "—"} 回・赤 ${reds.length}</span>` +
      `<span class="flow">${flow}</span>${change}` +
      (pets ? `<div class="headpets">${pets.order.filter((id) => gridPlaceOf(id) === "head" && pets.has(id)).map((id) => petRowHtml(id, s.agents[id]?.status, { tag: "div" })).join("")}</div>` : "");

    const picks = Object.fromEntries((s.project?.domain_picks ?? []).map((p) => [p.domain, p]));
    this.modeViews = [];
    const html = TILE_ORDER.map((d) => {
      const j = judgementState(s, d);
      const info = design ? domainAnalysis(s, d, v) : { ana: null, loadCase: null };
      let drawing = "";
      let caption = "";
      // A domain not picked is still drawn when a test of it was analysed (it fed another one).
      if (design && (j.state !== "off" || info.ana)) {
        const { body, ct } = tileSvg(s, d, design, info);
        drawing = body;
        if (ct) caption = `${FIELD_JA[ct.kind]}${ct.kind === "mode_shape" && ct.frequency ? ` ${fmt(ct.frequency, 4)} Hz` : ""}${ct.result ? `・最大 ${fmtVU(ct.result.value, ct.result.unit, 4)}` : ""}${ct.frameT !== undefined ? ` (t ${fmt(ct.frameT, 3)} s)` : ""}`;
        for (const x of info.ana?.interferences ?? []) {
          caption += `${caption ? "・" : ""}${thingName(design, x.a)} × ${thingName(design, x.b)} ${fmt(x.depth_mm, 3)} mm 干渉${x.state === "motion" ? ` (${x.condition ?? "動いたとき"})` : ""}`;
        }
      }
      const spark = info.ana ? sparkline(info.ana) : "";
      const summary = j.judgement?.summary ?? picks[d]?.reason ?? "";
      const nv = j.judgement?.verdict_ids?.length ?? 0;
      const badge = j.state === "green" ? `<span class="tb ok">✓</span>` : j.state === "red" ? `<span class="tb fix">直す</span>` : "";
      // Tests judged from an explained safe-side mismatch (not from a match) get a small mark.
      const ns = v != null ? domainVerdicts(s, d, v).filter(isSafeSide).length : 0;
      const safe = ns ? `<span class="tb safe" title="一致ではなく、説明された安全側の食い違いから判定した試験 ${ns} 件">安全側 ${ns}</span>` : "";
      // a load case without a criterion whose count is not 0: a 保留 band over the tile's colour (held.js)
      const held = heldCases(s, d, v);
      return (
        `<div class="tile ${j.state}${held.length ? " held" : ""}" data-domain="${d}"><div class="th"><h3>${esc(DOMAIN_JA[d])}</h3><span class="state">${STATE_WORD[j.state]}</span></div>` +
        (held.length ? `<div class="heldband">${heldBandHtml(held)}</div>` : "") +
        `<div class="draw">${drawing}</div>${spark}` +
        `<div class="cap">${esc(caption)}</div>` +
        `<div class="tf" data-say-host><span class="sum">${esc(summary)}${nv ? `<small>判定 ${nv} 件${ns ? `・うち安全側 ${ns} 件` : ""}${info.loadCase ? `・${esc(info.loadCase.test_id)}` : ""}</small>` : ""}</span>${safe}${badge}${tilePet(s, d, pets)}</div></div>`
      );
    }).join("");
    this.tiles.innerHTML = html;

    // Keep what a mode shape needs to swing its lines each frame.
    for (const d of DOMAINS) {
      const info = design ? domainAnalysis(s, d, v) : null;
      const field = info?.ana ? fieldsOf(info.ana, { mesh: false })[0] : null;
      if (!field || field.kind !== "mode_shape") continue;
      const ct = contourOf(info.ana, field, info.loadCase?.criterion);
      if (!ct?.modes) continue;
      const svg = this.tiles.querySelector(`svg.side[data-domain="${d}"]`);
      const view = sideView(design);
      const dmax = Math.max(...Object.values(ct.modes).map((x) => Math.hypot(...x)));
      if (!(dmax > 0)) continue;
      const span = Math.max(...Object.values(view.nodes).map((p) => p[0])) - Math.min(...Object.values(view.nodes).map((p) => p[0]));
      this.modeViews.push({ lines: [...svg.querySelectorAll("line[data-a]")], view, modes: ct.modes, scale: (0.12 * (span || 1000)) / dmax });
    }

    const limit = s.project?.round_limit;
    const atLimit = !!s.stopReason || (limit != null && roundsUsed(s) >= limit && s.status !== "running");
    return { reds, atLimit };
  }

  // Swing mode shapes with the replay clock (once a second), like the 3D view.
  tick(playhead) {
    const k = Math.sin(playhead * Math.PI * 2);
    for (const m of this.modeViews) {
      for (const line of m.lines) {
        const at = (id) => {
          const p = m.view.nodes[id];
          const d = m.modes[id] ?? [0, 0, 0];
          return [p[0] + d[0] * k * m.scale, p[2] + d[2] * k * m.scale];
        };
        const a = at(line.dataset.a);
        const b = at(line.dataset.b);
        line.setAttribute("x1", m.view.X(a[0]).toFixed(1));
        line.setAttribute("y1", m.view.Y(a[1]).toFixed(1));
        line.setAttribute("x2", m.view.X(b[0]).toFixed(1));
        line.setAttribute("y2", m.view.Y(b[1]).toFixed(1));
      }
    }
  }
}

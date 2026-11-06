// A6: the eight tiles (seven domains + assembly) of one design version, the
// version flow and the round-limit banner. Each tile draws a side view of the
// design (seen from the left: front on the left) coloured by the domain's
// latest analysis (stress / damage / temperature / mode shape), its
// interferences in red, and a small chart of its first series group. Every
// number printed is a recorded value.

import { DOMAINS, DOMAIN_JA, JUDGED_JA, esc, fmt, fmtQ, fmtVU, judgedWords, keepAsides, opLabel, quantityName, unitLabel, verLabel } from "./labels.js";
import { OVER_COLOR, colorOf } from "./colormap.js";
import { FIELD_JA, atFrame, contourOf, fieldsOf, frameIndex, overOf } from "./fields.js";
import { abscissa, channelsOf, chartGroups, extent, seriesName, xSeriesOf } from "./series.js";
import { domainVerdicts, isSafeSide, judgementState, roundsUsed } from "./state.js";
import { gridPlaceOf, petRowHtml } from "./agent_pets.js";
import { heldBandHtml, heldCases, referenceCases, referenceRowsHtml } from "./held.js";
import { builtLine } from "./proposals.js"; // modules:
import { judgedContour } from "./judged.js";
import { MATERIAL_JA, pictureOf } from "./tile_pictures.js";

const W = 300;
const H = 150;
const PAD = 14;
const SPARK = { w: 300, h: 64 };
const LINE_COLORS = ["#2b59c3", "#8e6bbf", "#2f7d4f", "#e9893a", "#c8402a", "#2fa7d6"];
const STATE_WORD = { green: "OK ✓", red: "NG", pending: "まだ", off: "選んでいない" };
// Top row: domains drawn as contours; bottom row: as charts, then assembly (interference).
export const TILE_ORDER = ["crash", "nvh", "strength", "thermal", "handling", "powertrain", "aero", "assembly"];

// A run whose tests the requirements file chose (its `tests:`, e.g. offroad-4w-n12.yaml: [S1]): the
// record says so in each pick's reason (「要件のファイルが試験 S1 … を選んだ」). A domain not picked
// there is not left out by mistake: its tile says which tests this run checks. null otherwise.
const FILE_CHOSE = /^要件のファイルが/;
export function onlyTestsLine(s) {
  const picks = s.project?.domain_picks ?? [];
  if (!picks.length || !picks.every((p) => FILE_CHOSE.test(p.reason ?? ""))) return null;
  const ids = [...new Set((s.project?.load_cases ?? []).map((c) => c.test_id).filter(Boolean))];
  return ids.length ? `この実行は ${ids.join("・")} だけを確かめます (要件のファイルが選んだ試験)` : null;
}

const caseTitle = (s, id) => (s.project?.load_cases ?? []).find((c) => c.load_case_id === id)?.title ?? "";

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

// The width of a tag's text in the side view's units (font 10: a CJK letter 10, others 5.6).
const tagWidth = (s) => [...s].reduce((w, ch) => w + (/[　-鿿＀-￯]/.test(ch) ? 10 : 5.6), 0);

// Each part's value over its box. Tags that would cross one already placed go up a line (up to
// TAG_LIFTS lines), else are left out: the caption and the workbench keep every value.
const TAG_LIFTS = 3;
function partValueTags(design, view, ct) {
  if (!ct?.parts) return "";
  const placed = [];
  const out = [];
  const parts = (design.parts ?? []).filter((p) => typeof ct.parts[p.id] === "number").sort((a, b) => ct.parts[b.id] - ct.parts[a.id]);
  for (const p of parts) {
    const c = p.box.center_mm;
    const over = overOf(ct, p.id, ct.parts[p.id]);
    const name = String(p.name ?? p.id).replace(/\s*[（(].*$/, "");
    const text = `${name} ${fmtVU(ct.parts[p.id], ct.field.unit, 3)}`;
    const x = view.X(c[0]);
    const w = tagWidth(text);
    let y = view.Y(c[2] + p.box.size_mm[2] / 2) - 4;
    let k = 0;
    const hits = () => placed.some((b) => x - w / 2 < b.x1 && x + w / 2 > b.x0 && y - 10 < b.y1 && y > b.y0);
    while (hits() && k < TAG_LIFTS) {
      y -= 12;
      k += 1;
    }
    if (hits()) continue;
    placed.push({ x0: x - w / 2, x1: x + w / 2, y0: y - 10, y1: y });
    out.push(`<text x="${x.toFixed(1)}" y="${y.toFixed(1)}" class="tv${over ? " over" : ""}" data-part="${esc(p.id)}">${esc(text)}</text>`);
  }
  return out.join("");
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
  const names = series.map((s) => seriesName(s.channel)).filter((n) => n !== g.ja); // (a lone channel is the chart's own name)
  return (
    `<svg class="spark" viewBox="0 0 ${SPARK.w} ${SPARK.h}" width="100%" preserveAspectRatio="xMidYMid meet">` +
    `<line x1="30" y1="${SPARK.h - 6}" x2="${SPARK.w - 6}" y2="${SPARK.h - 6}" stroke="#1d1d1f" stroke-width="1"/>` +
    `<line x1="30" y1="6" x2="30" y2="${SPARK.h - 6}" stroke="#1d1d1f" stroke-width="1"/>` +
    `<text x="26" y="12" class="ax">${esc(fmt(hi, 3))}</text><text x="26" y="${SPARK.h - 6}" class="ax">${esc(fmt(lo, 3))}</text>` +
    `${lines}<text x="34" y="12" class="lg">${esc(g.ja)} [${esc(unitLabel(g.unit) || "1")}]${names.length ? `・${esc(names.slice(0, 4).join("・"))}` : ""}</text></svg>`
  );
}

function tileSvg(s, domain, design, info) {
  const { ana, loadCase } = info;
  const field = fieldsOf(ana, { mesh: false })[0];
  // a field with frames (a time history): the moment with the largest value, as the workbench
  // shows it when not playing (the caption names the moment)
  // held to the version's allowable only when the field is the judged quantity (judged.js)
  const whole = field ? judgedContour(s, ana, field, loadCase?.load_case_id ?? ana?.load_case_id) : null;
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

// The tile's one number: the verdict that decides the domain as recorded (a failed one first,
// else the one with the smallest margin, margin_pct; else the first). null without verdicts.
export function headline(verdicts) {
  if (!verdicts.length) return null;
  const failed = verdicts.filter((x) => x.pass === false);
  const list = failed.length ? failed : verdicts;
  const m = (x) => (Number.isFinite(x.margin_pct) ? x.margin_pct : Infinity);
  return list.reduce((a, b) => (m(b) < m(a) ? b : a));
}

// The tiles' columns for n tiles (app.css .tiles uses --cols): two side by side up to four
// (2 x 2), then three a row (seven domains and assembly: 3, 3, 2).
export function gridColumns(n) {
  if (n <= 2) return 2;
  if (n === 3) return 3;
  if (n === 4) return 2;
  return 3;
}

// The line over the tiles that names the domains not picked (none: nothing). Why each was not
// picked (the record's domains.picked off[].reason, or the tests the file chose) only when it is
// opened. A domain among them whose test was analysed (it fed another one) is a chip that can
// replay it (app.js renderGrid marks it, as a tile).
function offLine(s, off, design, v) {
  if (!off.length) return "";
  const reasons = Object.fromEntries((s.intake?.picked?.off ?? []).map((x) => [x.domain, x.reason]));
  const only = onlyTestsLine(s);
  const rows = off.map((d) => `<li><b>${esc(DOMAIN_JA[d])}</b> ${esc(reasons[d] ?? only ?? "選ばなかった理由は記録に無い")}</li>`).join("");
  const chips = off
    .filter((d) => design && domainAnalysis(s, d, v).ana)
    .map((d) => `<span class="offchip" data-domain="${d}" title="選んでいないが、ほかの試験のために解析した (参考)">${esc(DOMAIN_JA[d])} (参考)</span>`)
    .join("");
  return (
    `<div class="offline" data-role="not-picked"><span class="offwords">今回は選んでいない: ${off.map((d) => esc(DOMAIN_JA[d])).join("・")}</span>${chips}` +
    `<details class="offwhy"><summary>理由</summary>${only ? `<p class="only">${esc(only)}</p>` : ""}<ul>${rows}</ul></details></div>`
  );
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
    // each version's failed load cases (its verdicts as recorded): their ids in the box, and in its
    // tooltip which were fixed since the version before and which are still red
    const redCases = (ver) => new Set(DOMAINS.flatMap((d) => domainVerdicts(s, d, ver).filter((x) => x.pass === false).map((x) => x.test_id ?? x.load_case_id)));
    let before = null;
    const flow = s.versions
      .map((ver) => {
        const r = DOMAINS.filter((d) => s.judgements[`${ver}|${d}`]?.state === "red").length;
        const now = redCases(ver);
        const fixed = before ? [...before].filter((x) => !now.has(x)) : [];
        const kept = before ? [...now].filter((x) => before.has(x)) : [];
        const tip = [now.size ? `NG の試験: ${[...now].join("・")}` : "NG の試験なし", fixed.length ? `1 つ前の設計から直った: ${fixed.join("・")}` : "", kept.length ? `1 つ前の設計から残った: ${kept.join("・")}` : ""].filter(Boolean).join("\n");
        before = now;
        return `<span class="vbox ${r ? "red" : "green"} ${ver === v ? "cur" : ""}" title="${esc(tip)}">${verLabel(ver)} NG ${r}${now.size ? `<small class="vred">${esc([...now].join("・"))}</small>` : ""}</span>`;
      })
      .join(`<span class="varrow">→</span>`);
    const design = v != null ? s.designs[v] : null;
    // modules: a version built from module designers' proposals names each module's change (proposals.js)
    const built = design ? builtLine(s, v) : "";
    const change = built
      ? `<span class="change" data-built="${v}">${built}</span>`
      : design && design.parent_version != null && design.change_reason ? `<span class="change long" title="${esc(judgedWords(design.change_reason))}">${verLabel(design.parent_version)} → ${verLabel(v)} で変えたこと: ${keepAsides(judgedWords(design.change_reason))}</span>` : "";
    // The domains not picked are not tiles (below): an agent placed on one of them stands in the
    // head with the agents of no domain
    const off = TILE_ORDER.filter((d) => judgementState(s, d).state === "off");
    const headIds = pets ? pets.order.filter((id) => (gridPlaceOf(id) === "head" || off.includes(gridPlaceOf(id))) && pets.has(id)) : [];
    const headN = headIds.length;
    this.head.innerHTML =
      `<h2>${verLabel(v)} の判定</h2><span class="muted">繰り返し ${roundsUsed(s)} / ${s.project?.round_limit ?? "—"} 回・NG ${reds.length}</span>` +
      `<span class="flow">${flow}</span>${change}` +
      (pets ? `<div class="headpets${headN > 3 ? " many" : ""}">${headIds.map((id) => petRowHtml(id, s.agents[id]?.status, { tag: "div", why: s.agents[id]?.stopWhy })).join("")}</div>` : "");
    this.head.classList.toggle("wrap", headN > 3); // modules: the module designers' pets on their own row

    const picks = Object.fromEntries((s.project?.domain_picks ?? []).map((p) => [p.domain, p]));
    this.modeViews = [];
    this.pictures = [];
    // The domains not picked are not tiles: one line names them (why, when it is opened); one of
    // them with a test analysed (it fed another one) is a small chip there that can replay it.
    const shown = TILE_ORDER.filter((d) => !off.includes(d));
    const html = shown.map((d) => {
      const j = judgementState(s, d);
      const info = design ? domainAnalysis(s, d, v) : { ana: null, loadCase: null };
      let drawing = "";
      let caption = "";
      let ct = null;
      // the 3D picture: the domain's own material (tile_pictures.js), what was used and not found
      let picAttrs = "";
      let picWords = "";
      if (design) {
        const t = tileSvg(s, d, design, info);
        drawing = t.body;
        ct = t.ct;
        const pic = pictureOf(s, d, v, design);
        this.pictures.push({ ...pic, lc: pic.loadCase });
        picAttrs = ` data-ana="${esc(pic.ana?.analysis_id ?? "")}" data-used="${pic.used.join(" ")}" data-missing="${pic.missing.join(" ")}"`;
        picWords = `絵: ${pic.used.length ? pic.used.map((m) => MATERIAL_JA[m]).join("・") : "車体だけ"}${pic.ana ? ` (${esc(pic.loadCase?.test_id ?? pic.ana.load_case_id)})` : ""}` +
          (pic.missing.length ? `・記録に無い: ${pic.missing.map((m) => MATERIAL_JA[m]).join("・")}` : "");
        // (参考 unless the value is the verdict's own quantity: judged.js ct.role)
        if (ct) caption = `${FIELD_JA[ct.kind]}${ct.kind === "mode_shape" && ct.frequency ? ` ${fmt(ct.frequency, 4)} Hz` : ""}${ct.result ? `・${ct.role !== "judged" ? `${JUDGED_JA.refTag} ` : ""}最大 ${fmtVU(ct.result.value, ct.result.unit, 4)}` : ""}${ct.frameT !== undefined ? ` (t ${fmt(ct.frameT, 3)} s)` : ""}`;
        for (const x of info.ana?.interferences ?? []) {
          caption += `${caption ? "・" : ""}${thingName(design, x.a)} × ${thingName(design, x.b)} ${fmt(x.depth_mm, 3)} mm 干渉${x.state === "motion" ? ` (${x.condition ?? "動いたとき"})` : ""}`;
        }
      }
      const spark = info.ana ? sparkline(info.ana) : "";
      // The verdicts of the version as recorded (pass, value, limit: not judged again here), each
      // with the limit it was held against; the record's own summary is the tooltip.
      const verdicts = v != null ? domainVerdicts(s, d, v) : [];
      const failed = verdicts.filter((x) => x.pass === false);
      // named by the quantity compared (frame_shell: the hot-spot stress for a max_stress criterion)
      const vline = (x) => `${x.test_id ?? x.load_case_id} ${quantityName(s.comparisons[x.comparison_id]?.quantity ?? x.quantity)} ${fmtQ(x.value)} ${opLabel(x.op)} ${fmtQ(x.limit)}${x.pass === false ? " ✕" : ""}`;
      const recorded = j.judgement?.summary != null ? judgedWords(j.judgement.summary) : picks[d]?.reason ?? "";
      // a tile still まだ with no word from the record: why, as far as the record tells
      const why = j.state === "pending" && !j.judgement?.summary
        ? info.ana ? `${verLabel(v)} の解析は記録にあるが、判定がまだ記録に無い` : `${verLabel(v)} の解析がまだ記録に無い`
        : "";
      // (a tile still まだ with some verdicts: those are the ones judged so far, not the tile's pass)
      const lead = failed.length ? `NG ${failed.length} 件: ` : j.state === "green" ? "OK: " : "判定のある分: ";
      const only = j.state === "off" && !info.ana ? onlyTestsLine(s) : null;
      const summary = verdicts.length ? `${lead}${(failed.length ? failed : verdicts).map(vline).join("、")}` : recorded || why || only || "";
      const nv = j.judgement?.verdict_ids?.length ?? 0;
      // Tests judged from an explained safe-side mismatch (not from a match) get a small mark.
      const ns = v != null ? domainVerdicts(s, d, v).filter(isSafeSide).length : 0;
      const safe = ns ? `<span class="tb safe" title="予想通りではなく、説明された安全側の不一致から判定した試験 ${ns} 件">安全側 ${ns}</span>` : "";
      // a load case without a criterion whose count is not 0: a 保留 band over the tile's colour (held.js)
      const held = heldCases(s, d, v);
      // the other quantities without a criterion (渡すだけ・比べるだけ・参考): rows not coloured (held.js)
      const refs = referenceCases(s, d, v);
      // the state as recorded, with what goes with it: green while a count is held says both; red
      // names the load cases whose verdict failed
      const failedIds = [...new Set(failed.map((x) => x.test_id ?? x.load_case_id))];
      const word =
        j.state === "green" && held.length
          ? `OK (判定のある分だけ)・保留 ${held.length}`
          : j.state === "red" && failedIds.length
            ? `NG・${failedIds.join("・")}`
            : STATE_WORD[j.state];
      const wordTitle = j.state === "red" && failed.length ? ` title="${esc(failed.map((x) => `${x.load_case_id}: ${caseTitle(s, x.load_case_id)}`).join("\n"))}"` : "";
      // the large mark: OK / NG (red only for NG), else まだ; with a count held, 保留 beside it
      const mark = j.state === "green" ? "OK" : j.state === "red" ? "NG" : "まだ";
      const big = headline(verdicts);
      const number = big
        ? `<div class="num" data-role="number"><span class="nq">${esc(`${big.test_id ?? big.load_case_id ?? ""} ${quantityName(s.comparisons[big.comparison_id]?.quantity ?? big.quantity)}`.trim())}</span>` +
          `<b class="nv${big.pass === false ? " ng" : ""}">${esc(fmtQ(big.value, 3))}</b><span class="nl">${esc(`${opLabel(big.op)} ${fmtQ(big.limit, 3)}`)}</span></div>`
        : `<div class="num none" data-role="number"><span class="nq">${esc(why)}</span></div>`;
      return (
        `<div class="tile ${j.state}${held.length ? " held" : ""}" data-domain="${d}">` +
        `<div class="th"><h3>${esc(DOMAIN_JA[d])}</h3><span class="mark ${j.state}" data-role="mark">${mark}${held.length ? `<small>保留 ${held.length}</small>` : ""}</span></div>` +
        `<div class="tbody"><div class="pic"><canvas class="tile3d" data-domain="${d}"${picAttrs}></canvas></div>` +
        `<div class="side">${number}<div class="tf" data-say-host>${safe}${tilePet(s, d, pets)}</div></div></div>` +
        // the words and the small drawings: only when the tile is opened
        `<details class="more"><summary>くわしく</summary>` +
        `<div class="state"${wordTitle}>${esc(word)}</div>` +
        (held.length ? `<div class="heldband">${heldBandHtml(held)}</div>` : "") +
        referenceRowsHtml(refs) +
        `<span class="sum"${recorded && recorded !== summary ? ` title="${esc(`記録の要約: ${recorded}`)}"` : ""}>${esc(summary)}${nv ? `<small>判定 ${nv} 件${ns ? `・うち安全側 ${ns} 件` : ""}${info.loadCase ? `・${esc(info.loadCase.test_id)}` : ""}</small>` : ""}</span>` +
        `<div class="cap">${esc(caption)}</div>` +
        (picWords ? `<div class="picwords" data-role="picture">${picWords}</div>` : "") +
        // the strength tile: the mounts are not judged (labels.js JUDGED_JA.mountsNotJudged)
        (d === "strength" && (s.project?.load_cases ?? []).some((c) => c.domain === "strength") ? `<div class="not-judged" data-role="not-judged">${esc(JUDGED_JA.mountsNotJudged)}</div>` : "") +
        `<div class="draw">${drawing}</div>${spark}` +
        `</details></div>`
      );
    }).join("");
    this.tiles.dataset.n = String(shown.length);
    this.tiles.style.setProperty("--cols", String(gridColumns(shown.length)));
    this.tiles.classList.toggle("dense", shown.length > 4); // (app.css: smaller words beside the pictures)
    this.tiles.innerHTML = offLine(s, off, design, v) + html;

    // Keep what a mode shape needs to swing its lines each frame.
    for (const d of DOMAINS) {
      const info = design ? domainAnalysis(s, d, v) : null;
      const field = info?.ana ? fieldsOf(info.ana, { mesh: false })[0] : null;
      if (!field || field.kind !== "mode_shape") continue;
      const ct = contourOf(info.ana, field, info.loadCase?.criterion);
      if (!ct?.modes) continue;
      const svg = this.tiles.querySelector(`svg.side[data-domain="${d}"]`);
      if (!svg) continue; // (a domain not picked: no tile)
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

  // The tiles' 3D pictures (tile3d.js TileViews), once the tiles are laid out; scale: device
  // pixels per CSS pixel of the tiles. newMax: how many pictures not kept yet may be drawn now
  // (0: only the ones kept are copied); returns whether every picture is shown.
  drawPictures(views, scale, newMax = Infinity) {
    let all = true;
    let left = newMax;
    for (const p of this.pictures ?? []) {
      const canvas = this.tiles.querySelector(`canvas.tile3d[data-domain="${p.domain}"]`);
      // (none, or not laid out: the grid hidden; drawn when the grid is rendered again)
      if (!canvas || canvas.clientWidth < 2 || canvas.clientHeight < 2) continue;
      if (views.draw(canvas, p.domain, { ...p, scale }, true)) continue;
      if (left > 0 && views.draw(canvas, p.domain, { ...p, scale })) left -= 1;
      else all = false;
    }
    return all;
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

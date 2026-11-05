// A4 - the tool shelf: the analysis tools as a tree (main domain -> tool, or base K0..K5 -> tool),
// one row per tool (catalog tests, hand-calculation formulas, CAE base, checks passed), and the
// chosen tool's known-answer checks (and, for a tool Claude wrote in the run, its code).
//
// The shelf comes from GET /api/shelf (every builtin tool and the K0 formulas; the server needs
// some seconds the first time and answers {"ready": false} until then). The run's tool.checked
// records are laid over it by name (the run's are newer); tools only the run has are added.
// The only numbers made here are counts (tools, checks passed); the rest are the records' values.
//
// Words for people: a formula by its title (the K0 Formula's title in /api/shelf's formulas, or in
// /api/formulas while the shelf is being built; an id without a title as it is), a quantity by quantityName, a check's case and source by case_ja and
// source_ja when the record has them (the English as written when not). The ids (the tool's name,
// the formula's id, the quantity's key) and the English beside a Japanese case or source are shown
// only under ?dev=1, inside くわしく.

import { BASE_JA, DOMAINS, DOMAIN_JA, fmt, fmtPct, quantityName, unitLabel } from "./labels.js";
import { fill, h } from "./intake/dom.js";

const BASES = ["K0", "K1", "K2", "K3", "K4", "K5"];
const MODES = [["domain", "分野から"], ["base", "土台から"]];
const FILTERS = [["all", "すべて"], ["claude", "Claude が作った"], ["notok", "OK でない"], ["unusable", "使えない"]];
const POLL_MS = 2000; // asking again while the server builds the shelf

// A check's got is null when the tool could not give that value (e.g. tilt.py finds no tipping angle);
// its pass is then false. Not "still checking": the record is final.
const noValue = (c) => c.got === null;
const isClaude = (t) => t.origin === "claude";
const hasNoValue = (t) => (t.checks ?? []).some(noValue);
// OK でない: at least one check that did not pass or gave no value (a tool without checks is not counted)
const isNotOk = (t) => (t.checks ?? []).some((c) => !c.pass || noValue(c));
const isUnusable = (t) => t.usable === false;
const passed = (t) => (t.checks ?? []).filter((c) => c.pass && !noValue(c)).length;
const allPassed = (t) => (t.checks ?? []).length > 0 && passed(t) === t.checks.length;
const FILTER_FN = { all: () => true, claude: isClaude, notok: isNotOk, unusable: isUnusable };

/** The shelf's tools with the run's tool.checked records laid over them by name (run's first wins). */
export function mergeTools(shelfTools, runTools) {
  const out = new Map();
  for (const t of shelfTools ?? []) out.set(t.name, { ...t, fromRun: false });
  for (const t of Object.values(runTools ?? {})) out.set(t.name, { ...t, fromRun: true });
  return [...out.values()];
}

function matches(t, query) {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [t.name, t.title, ...(t.tests ?? [])].some((s) => String(s ?? "").toLowerCase().includes(q));
}

/** Branches of the tree: [{key, label, tools}] in the order of the domains or the bases. */
export function shelfTree(tools, { mode = "domain", filter = "all", query = "" } = {}) {
  const shown = tools.filter((t) => (FILTER_FN[filter] ?? FILTER_FN.all)(t) && matches(t, query));
  const keyOf = (t) => (mode === "base" ? t.base : t.domain);
  const order = mode === "base" ? BASES : DOMAINS;
  const keys = [...order, ...[...new Set(shown.map(keyOf))].filter((k) => !order.includes(k))];
  const label = (k) => (mode === "base" ? `${k} ${BASE_JA[k] ?? ""}`.trim() : DOMAIN_JA[k] ?? String(k));
  return keys
    .map((k) => ({ key: k, label: label(k), tools: shown.filter((t) => keyOf(t) === k).sort((a, b) => a.name.localeCompare(b.name)) }))
    .filter((b) => b.tools.length);
}

function checkState(t) {
  const n = (t.checks ?? []).length;
  const p = passed(t);
  const cls = !n ? "none" : hasNoValue(t) ? "noval" : p === n ? "ok" : "bad";
  const title = !n ? "答えの分かっている問題が無い" : hasNoValue(t) ? "ツールの値が出ない問題がある"
    : p === n ? "答えの分かっている問題で全部 OK" : "OK でない問題がある";
  return h("span", { class: `sh-chk ${cls}`, title }, `OK ${p} / ${n}`);
}

// ?dev=1: the ids and the English are shown, folded under くわしく; without it, not at all.
const DEV = new URLSearchParams(globalThis.location?.search ?? "").get("dev") === "1";

// A formula's expression as K0 writes it (plain ASCII), shown with the usual signs: only a sign is put
// for a sign, the expression is not rewritten. A name written in full (mu, theta) becomes its letter;
// ^2 ^3 ... become raised digits; sqrt becomes √; * becomes ×; a name with an underscore (t_r,
// mu_all) is written with what follows the first underscore lowered (<sub>).
export const EXPR_SIGNS = {
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", Delta: "Δ", epsilon: "ε", zeta: "ζ", eta: "η", theta: "θ",
  kappa: "κ", lambda: "λ", mu: "μ", nu: "ν", pi: "π", rho: "ρ", sigma: "σ", tau: "τ", phi: "φ", psi: "ψ",
  omega: "ω", Omega: "Ω", sqrt: "√", "*": "×",
};
const RAISED = { "-": "⁻", 0: "⁰", 1: "¹", 2: "²", 3: "³", 4: "⁴", 5: "⁵", 6: "⁶", 7: "⁷", 8: "⁸", 9: "⁹" };

/** The expression as text nodes and <sub> elements (EXPR_SIGNS, raised powers, lowered parts after "_"). */
export function exprNodes(expr) {
  const out = [];
  for (const [tok] of String(expr ?? "").matchAll(/[A-Za-z][A-Za-z0-9_]*|\^-?\d+|\*|[^A-Za-z^*]+|./g)) {
    if (tok.startsWith("^") && tok.length > 1) {
      out.push([...tok.slice(1)].map((ch) => RAISED[ch]).join(""));
    } else if (tok === "*") {
      out.push(EXPR_SIGNS["*"]);
    } else if (/^[A-Za-z]/.test(tok)) {
      const cut = tok.indexOf("_");
      const base = cut > 0 ? tok.slice(0, cut) : tok;
      const low = cut > 0 ? tok.slice(cut + 1) : "";
      out.push(EXPR_SIGNS[base] ?? base);
      if (low) out.push(h("sub", {}, EXPR_SIGNS[low] ?? low));
    } else {
      out.push(tok);
    }
  }
  return out;
}

function marks(t) {
  return [
    isClaude(t) ? h("span", { class: "sh-mark claude", title: "実行の中で Claude が作ったツール" }, "Claude が作った") : null,
    isUnusable(t) ? h("span", { class: "sh-mark off", title: "確かめが OK でないので使えない" }, "使えない") : null,
  ];
}

export function mountShelf(el, api) {
  const ui = { mode: "domain", filter: "all", query: "", collapsed: new Set(), selected: null };
  const shelf = { ready: false, tools: [], formulas: [], archetypes: {}, error: null, asked: false, failed: null };
  const code = new Map(); // `${runId}|${name}` -> {state: "loading"|"none"|"ok"|"error", n, text}
  const opened = new Set(); // the くわしく left open, by key (they stay open across the redraws)
  let timer = null;

  // A fold (details, shut at first) that stays open or shut across the redraws; key: what keeps it.
  function fold(key, cls, label, ...kids) {
    return h("details", { class: cls, open: opened.has(key), dataset: { fold: key },
        ontoggle: (e) => { if (e.target.open) opened.add(key); else opened.delete(key); } },
      h("summary", {}, label), ...kids);
  }

  // A くわしく (only under ?dev=1) holding the ids or the English.
  function more(key, ...kids) {
    return DEV ? fold(key, "more sh-more", "くわしく", ...kids) : null;
  }

  const search = h("input", { class: "sh-search", type: "search", placeholder: "ツールの名前・試験の番号で探す",
    oninput: (e) => { ui.query = e.target.value; render(); } });
  const modeSeg = h("div", { class: "sh-seg sh-mode" });
  const filterSeg = h("div", { class: "sh-seg sh-filter" });
  const sum = h("span", { class: "sh-sum" });
  const note = h("p", { class: "sh-note" });
  const tree = h("ul", { class: "sh-tree", dataset: { keepScroll: "a4-tree" } });
  const treeBox = h("section", { class: "sh-left" }, note, tree);
  const detailBox = h("section", { class: "sh-right" });
  fill(el, h("div", { class: "sh-host" },
    h("div", { class: "sh-bar" }, search, modeSeg, filterSeg, sum),
    h("div", { class: "sh-body" }, treeBox, detailBox)));

  // The formulas' titles from GET /api/formulas (light: ids and titles only), so a run's tool names its
  // formulas while the shelf is still being built. Asked once; on a failure the ids stay as they are.
  const titles = new Map();
  async function askTitles() {
    try {
      const got = await (await fetch("/api/formulas", { cache: "no-store" })).json();
      for (const f of got.formulas ?? []) if (typeof f.title === "string" && f.title) titles.set(f.formula_id, f.title);
    } catch {
      return;
    }
    render();
  }

  async function ask() {
    if (!shelf.asked) askTitles();
    shelf.asked = true;
    clearTimeout(timer);
    try {
      const got = await (await fetch("/api/shelf", { cache: "no-store" })).json();
      if (got.ready) {
        Object.assign(shelf, { ready: true, tools: got.tools ?? [], formulas: got.formulas ?? [], archetypes: got.archetypes ?? {},
          error: got.error ?? null, failed: null });
      } else {
        timer = setTimeout(ask, POLL_MS);
      }
    } catch (e) {
      shelf.failed = String(e.message ?? e);
      timer = setTimeout(ask, POLL_MS * 2);
    }
    render();
  }

  function formulaOf(id) {
    return shelf.formulas.find((f) => f.formula_id === id) ?? null;
  }

  // A formula's words: its title (K0's Formula.title, from the shelf or /api/formulas), or the id when it has none.
  function formulaName(id) {
    const f = formulaOf(id);
    return f && typeof f.title === "string" && f.title ? f.title : titles.get(id) ?? id;
  }

  function formulaChip(id) {
    const f = formulaOf(id);
    return h("span", { class: "sh-f", dataset: { formula: id }, title: f ? `${f.expression}` : "棚の式の一覧に無い" },
      formulaName(id), DEV ? h("code", {}, id) : null);
  }

  // A tool's applies_to (the kinds of design it can analyse; none: any design) as one line of names:
  // the generator's TITLE up to its first " (" (the whole title on hover), or the kind's key as it is.
  function appliesTo(t) {
    const kinds = t.applies_to ?? [];
    if (!kinds.length) return null;
    const names = kinds.map((k) => {
      const title = shelf.archetypes[k];
      return typeof title === "string" && title ? h("span", { class: "sh-arch", dataset: { kind: k }, title }, title.split(" (")[0])
        : h("span", { class: "sh-arch", dataset: { kind: k } }, k);
    });
    return h("div", { class: "sh-applies", title: "このツールが使える設計の型 (applies_to)" },
      "使える設計: ", names.flatMap((n, i) => (i ? ["・", n] : [n])));
  }

  function row(t) {
    return h("li", { class: `sh-tool${ui.selected === t.name ? " sel" : ""}${isUnusable(t) ? " is-off" : ""}${isClaude(t) ? " is-claude" : ""}`,
        dataset: { tool: t.name }, onclick: () => { ui.selected = t.name; render(); } },
      h("div", { class: "sh-name" }, h("b", {}, t.title ?? t.name), DEV ? h("code", {}, t.name) : null, marks(t)),
      h("div", { class: "sh-cols" },
        h("span", { class: "sh-tests", title: "対応する試験 (カタログの番号)" }, (t.tests ?? []).length ? t.tests.map((x) => h("span", { class: "sh-tid" }, x)) : h("span", { class: "sh-dim" }, "試験なし")),
        h("span", { class: "sh-k0", title: "手計算の式 (K0)" }, (t.formulas ?? []).length ? t.formulas.map(formulaChip) : h("span", { class: "sh-dim" }, "式なし")),
        h("span", { class: "sh-base", title: "CAE の段 (土台)" }, h("b", {}, t.base), ` ${BASE_JA[t.base] ?? ""}`),
        checkState(t)),
      appliesTo(t));
  }

  function branch(b) {
    const key = `${ui.mode}:${b.key}`;
    const closed = ui.collapsed.has(key);
    const ok = b.tools.filter(allPassed).length;
    return h("li", { class: "sh-node", dataset: { branch: b.key } },
      h("button", { class: "sh-head", type: "button", onclick: () => { if (closed) ui.collapsed.delete(key); else ui.collapsed.add(key); render(); } },
        h("span", { class: "sh-caret" }, closed ? "▸" : "▾"), h("b", {}, b.label),
        h("span", { class: "sh-n", title: "ツールの数" }, `${b.tools.length} ツール`),
        h("span", { class: "sh-nok", title: "確かめが全部 OK のツールの数" }, `OK ${ok}`)),
      closed ? null : h("ul", {}, b.tools.map(row)));
  }

  function codeOf(t) {
    const runId = api.runId();
    if (!runId || runId === "sample") return { state: "none" };
    const key = `${runId}|${t.name}`;
    if (!code.has(key)) {
      code.set(key, { state: "loading" });
      // the versions the run's tools/ holds (GET /api/runs/<id>/tools), then only the newest file:
      // no guessing <name>-<n+1>.py and a 404 for it in the console
      (async () => {
        try {
          const lr = await fetch(`/api/runs/${encodeURIComponent(runId)}/tools`, { cache: "no-store" });
          if (!lr.ok) throw new Error(`${lr.status}`);
          const versions = (await lr.json()).tools?.[t.name] ?? [];
          const n = versions.length ? versions[versions.length - 1] : null;
          let last = null;
          if (n !== null) {
            const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/tools/${t.name}-${n}.py`, { cache: "no-store" });
            if (!res.ok) throw new Error(`${res.status}`);
            last = { n, text: await res.text() };
          }
          code.set(key, last ? { state: "ok", ...last } : { state: "none" });
        } catch (e) {
          code.set(key, { state: "error", text: String(e.message ?? e) });
        }
        render();
      })();
    }
    return code.get(key);
  }

  function codeBlock(t) {
    const c = codeOf(t);
    const head = h("h4", {}, "実行が書いたコード", c.state === "ok" ? h("small", {}, ` tools/${t.name}-${c.n}.py`) : null);
    if (c.state === "loading") return [head, h("p", { class: "sh-dim" }, "読んでいます…")];
    if (c.state === "error") return [head, h("p", { class: "sh-err" }, `読めませんでした: ${c.text}`)];
    if (c.state === "none") return [head, h("p", { class: "sh-dim" }, "この実行の tools/ にコードがありません")];
    return [head, h("pre", { class: "sh-code" }, c.text)];
  }

  function detail(t) {
    if (!t) return h("p", { class: "sh-dim" }, "左のツールを選ぶと、答えの分かっている問題で確かめた結果が出ます");
    const checks = t.checks ?? [];
    const k = (what) => `${t.name}|${what}`;
    // a check's case or source: the Japanese when the record has it (the English under くわしく), else the English
    const words = (ja, en, key) => (typeof ja === "string" && ja ? [ja, more(key, h("div", { class: "sh-en" }, en))] : en);
    return h("div", { class: "sh-detail", dataset: { tool: t.name } },
      h("header", {}, h("h3", {}, t.title ?? t.name), marks(t), checkState(t), more(k("name"), h("code", {}, t.name))),
      h("dl", {},
        h("dt", {}, "分野"), h("dd", {}, DOMAIN_JA[t.domain] ?? t.domain),
        h("dt", {}, "試験"), h("dd", {}, (t.tests ?? []).join("・") || "—"),
        h("dt", {}, "手計算の式"), h("dd", {}, (t.formulas ?? []).length
          ? t.formulas.map((id) => {
            const f = formulaOf(id);
            return h("div", { class: "sh-fl", dataset: { formula: id } }, formulaName(id),
              f?.expression ? fold(k(`x:${id}`), "sh-see", "式を見る", h("div", { class: "sh-expr" }, exprNodes(f.expression))) : null,
              more(k(`f:${id}`), h("code", {}, id)));
          })
          : "—"),
        h("dt", {}, "CAE の段"), h("dd", {}, `${t.base} ${BASE_JA[t.base] ?? ""}`),
        h("dt", {}, "出どころ"), h("dd", {}, isClaude(t) ? "この実行で Claude が作った" : "標準のツール", t.fromRun ? h("small", { class: "sh-dim" }, " (この実行で確かめた記録)") : null)),
      h("h4", {}, `答えの分かっている問題で確かめた結果 (${checks.length} 問、OK ${passed(t)}${checks.length - passed(t) ? `、OK でない ${checks.length - passed(t)}` : ""})`),
      h("p", { class: "sh-what" }, "ツールを使う前に、式で答えが出る簡単な問題を解かせ、答えと合うかを確かめた結果です。設計を確かめる手計算とは別のものです。"),
      checks.length
        ? h("table", { class: "sh-checks" },
            h("thead", {}, h("tr", {}, ["確かめに使った問題", "比べた値", "式で出した答え", "ツールの答え", "単位", "ずれてよい幅", "結果", "答えの出し方"].map((x) => h("th", {}, x)))),
            h("tbody", {}, checks.map((c, i) => h("tr", { class: noValue(c) ? "noval" : c.pass ? "ok" : "bad" },
              h("td", { class: "case" }, words(c.case_ja, c.case, k(`case:${i}`))),
              h("td", { class: "qty" }, quantityName(c.quantity), quantityName(c.quantity) !== c.quantity ? more(k(`q:${i}`), h("code", {}, c.quantity)) : null),
              h("td", { class: "num", title: String(c.expected) }, fmt(c.expected, 6)),
              h("td", { class: "num", title: noValue(c) ? "ツールがこの問題の値を出せなかった" : String(c.got) }, noValue(c) ? "値が出ない" : fmt(c.got, 6)),
              h("td", {}, unitLabel(c.unit)),
              h("td", { class: "num", title: String(c.tolerance_rel) }, fmtPct(c.tolerance_rel, 1)),
              h("td", {}, c.pass && !noValue(c) ? "OK" : "×"),
              h("td", { class: "src" }, words(c.source_ja, c.source, k(`src:${i}`)))))))
        : h("p", { class: "sh-dim" }, "答えの分かっている問題がありません"),
      isClaude(t) ? codeBlock(t) : null);
  }

  function render() {
    const tools = mergeTools(shelf.tools, api.tools());
    fill(modeSeg, MODES.map(([k, label]) => h("button", { type: "button", class: ui.mode === k ? "on" : "", dataset: { mode: k }, onclick: () => { ui.mode = k; render(); } }, label)));
    fill(filterSeg, FILTERS.map(([k, label]) => h("button", { type: "button", class: ui.filter === k ? "on" : "", dataset: { filter: k }, onclick: () => { ui.filter = k; render(); } }, label)));
    const ok = tools.filter(allPassed).length;
    sum.textContent = `${tools.length} ツール・OK ${ok}`;
    note.hidden = shelf.ready && !shelf.error && !shelf.failed;
    note.className = `sh-note${shelf.error || shelf.failed ? " sh-err" : ""}`;
    note.textContent = shelf.error ? `棚を読めませんでした: ${shelf.error}`
      : shelf.failed ? `棚を読めませんでした (${shelf.failed})。もう一度聞いています…`
      : shelf.ready ? "" : "棚を読み込んでいます… (初めの 1 回は、ツールの照合を回すので少しかかります)";
    const branches = shelfTree(tools, ui);
    fill(tree, branches.length ? branches.map(branch) : h("li", { class: "sh-dim" }, tools.length ? "条件に合うツールがありません" : shelf.ready ? "ツールがありません" : ""));
    fill(detailBox, detail(tools.find((t) => t.name === ui.selected) ?? null));
  }

  return {
    /** Draw again (the run's events changed, or the tab opened); asks the server for the shelf once. */
    render() {
      if (!shelf.asked) ask();
      render();
    },
  };
}

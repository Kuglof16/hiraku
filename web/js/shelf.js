// A4 - the tool shelf: the analysis tools as a tree (main domain -> tool, or base K0..K5 -> tool),
// one row per tool (catalog tests, hand-calculation formulas, CAE base, checks passed), and the
// chosen tool's known-answer checks (and, for a tool Claude wrote in the run, its code).
//
// The shelf comes from GET /api/shelf (every builtin tool and the K0 formulas; the server needs
// some seconds the first time and answers {"ready": false} until then). The run's tool.checked
// records are laid over it by name (the run's are newer); tools only the run has are added.
// The only numbers made here are counts (tools, checks passed); the rest are the records' values.

import { BASE_JA, DOMAINS, DOMAIN_JA, fmt, fmtPct, unitLabel } from "./labels.js";
import { fill, h } from "./intake/dom.js";

const BASES = ["K0", "K1", "K2", "K3", "K4", "K5"];
const MODES = [["domain", "分野から"], ["base", "土台から"]];
const FILTERS = [["all", "すべて"], ["claude", "Claude が作った"], ["checking", "照合中"], ["unusable", "使えない"]];
const POLL_MS = 2000; // asking again while the server builds the shelf

const isClaude = (t) => t.origin === "claude";
const isChecking = (t) => (t.checks ?? []).some((c) => c.got === null);
const isUnusable = (t) => t.usable === false;
const passed = (t) => (t.checks ?? []).filter((c) => c.pass).length;
const allPassed = (t) => (t.checks ?? []).length > 0 && passed(t) === t.checks.length;
const FILTER_FN = { all: () => true, claude: isClaude, checking: isChecking, unusable: isUnusable };

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
  const cls = !n ? "none" : isChecking(t) ? "wait" : p === n ? "ok" : "bad";
  const title = !n ? "照合の例が無い" : isChecking(t) ? "照合中 (まだ値の無い例がある)" : p === n ? "照合が全部通った" : "通らない例がある";
  return h("span", { class: `sh-chk ${cls}`, title }, `照合 ${p} / ${n}`);
}

function marks(t) {
  return [
    isClaude(t) ? h("span", { class: "sh-mark claude", title: "実行の中で Claude が作ったツール" }, "Claude が作った") : null,
    isUnusable(t) ? h("span", { class: "sh-mark off", title: "照合が通っていないので使えない (HK2)" }, "使えない") : null,
  ];
}

export function mountShelf(el, api) {
  const ui = { mode: "domain", filter: "all", query: "", collapsed: new Set(), selected: null };
  const shelf = { ready: false, tools: [], formulas: [], archetypes: {}, error: null, asked: false, failed: null };
  const code = new Map(); // `${runId}|${name}` -> {state: "loading"|"none"|"ok"|"error", n, text}
  let timer = null;

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

  async function ask() {
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

  function formulaChip(id) {
    const f = formulaOf(id);
    return h("span", { class: "sh-f", title: f ? `${f.expression}` : "棚の式の一覧に無い" }, f ? f.title : "", h("code", {}, id));
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
      h("div", { class: "sh-name" }, h("b", {}, t.title ?? t.name), h("code", {}, t.name), marks(t)),
      h("div", { class: "sh-cols" },
        h("span", { class: "sh-tests", title: "対応する試験 (カタログの番号)" }, (t.tests ?? []).length ? t.tests.map((x) => h("span", { class: "sh-tid" }, x)) : h("span", { class: "sh-dim" }, "試験なし")),
        h("span", { class: "sh-k0", title: "手計算の段 (K0 の式)" }, (t.formulas ?? []).length ? t.formulas.map(formulaChip) : h("span", { class: "sh-dim" }, "式なし")),
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
        h("span", { class: "sh-nok", title: "照合が全部通ったツールの数" }, `照合が通った ${ok}`)),
      closed ? null : h("ul", {}, b.tools.map(row)));
  }

  function codeOf(t) {
    const runId = api.runId();
    if (!runId || runId === "sample") return { state: "none" };
    const key = `${runId}|${t.name}`;
    if (!code.has(key)) {
      code.set(key, { state: "loading" });
      (async () => {
        let last = null;
        try {
          for (let n = 1; n < 1000; n += 1) {
            const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/tools/${t.name}-${n}.py`, { cache: "no-store" });
            if (res.status === 404) break;
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
    if (!t) return h("p", { class: "sh-dim" }, "左のツールを選ぶと、照合の表が出ます");
    const checks = t.checks ?? [];
    return h("div", { class: "sh-detail", dataset: { tool: t.name } },
      h("header", {}, h("h3", {}, t.title ?? t.name), h("code", {}, t.name), marks(t), checkState(t)),
      h("dl", {},
        h("dt", {}, "分野"), h("dd", {}, DOMAIN_JA[t.domain] ?? t.domain),
        h("dt", {}, "試験"), h("dd", {}, (t.tests ?? []).join("・") || "—"),
        h("dt", {}, "手計算の段"), h("dd", {}, (t.formulas ?? []).length
          ? t.formulas.map((id) => { const f = formulaOf(id); return h("div", { class: "sh-fl" }, f ? f.title : "", h("code", {}, id), f ? h("code", { class: "sh-expr" }, f.expression) : null); })
          : "—"),
        h("dt", {}, "CAE の段"), h("dd", {}, `${t.base} ${BASE_JA[t.base] ?? ""}`),
        h("dt", {}, "出どころ"), h("dd", {}, isClaude(t) ? "この実行で Claude が作った" : "標準のツール", t.fromRun ? h("small", { class: "sh-dim" }, " (この実行の照合の記録)") : null)),
      h("h4", {}, "照合の表", h("small", {}, ` 答えの分かっている例 ${checks.length}`)),
      checks.length
        ? h("table", { class: "sh-checks" },
            h("thead", {}, h("tr", {}, ["例", "量", "答え", "ツールの値", "単位", "許す幅", "結果", "答えの出どころ"].map((x) => h("th", {}, x)))),
            h("tbody", {}, checks.map((c) => h("tr", { class: c.got === null ? "wait" : c.pass ? "ok" : "bad" },
              h("td", {}, c.case), h("td", {}, h("code", {}, c.quantity)),
              h("td", { class: "num", title: String(c.expected) }, fmt(c.expected, 6)),
              h("td", { class: "num", title: String(c.got) }, c.got === null ? "照合中" : fmt(c.got, 6)),
              h("td", {}, unitLabel(c.unit)),
              h("td", { class: "num", title: String(c.tolerance_rel) }, fmtPct(c.tolerance_rel, 1)),
              h("td", {}, c.got === null ? "—" : c.pass ? "合格 ✓" : "不合格 ✗"),
              h("td", { class: "src" }, c.source)))))
        : h("p", { class: "sh-dim" }, "照合の例がありません"),
      isClaude(t) ? codeBlock(t) : null);
  }

  function render() {
    const tools = mergeTools(shelf.tools, api.tools());
    fill(modeSeg, MODES.map(([k, label]) => h("button", { type: "button", class: ui.mode === k ? "on" : "", dataset: { mode: k }, onclick: () => { ui.mode = k; render(); } }, label)));
    fill(filterSeg, FILTERS.map(([k, label]) => h("button", { type: "button", class: ui.filter === k ? "on" : "", dataset: { filter: k }, onclick: () => { ui.filter = k; render(); } }, label)));
    const ok = tools.filter(allPassed).length;
    sum.textContent = `${tools.length} ツール・照合が通った ${ok}`;
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

// The judges' page: one sentence on what HIRAKU is, a large play button and two links in the header that stay
// (the introduction page, and 「？ 使い方」 that brings the card back). The page's own tabs and its
// 「この記録を再生する」 button do the work; this only adds the card and presses them.
// Only on a static export's index.html (hiraku/webserve/export_static.py adds the script there): the screen
// with a server and the film's pages (cut.html, ?video=1) are as they are.
(function () {
  if (document.querySelector('meta[name="hiraku-static"]')?.content !== "1") return;
  if (/cut\.html$/.test(location.pathname) || new URLSearchParams(location.search).get("video") === "1") return;
  // the introduction page: the export's --intro-url (a meta in the exported index.html); none, no links to it
  const LP = document.querySelector('meta[name="hiraku-intro"]')?.content || "";
  // (the bare address: app.js opens the export's first run (export_static --default-run) as it ended, on ④)
  const params = new URLSearchParams(location.search);
  const HEAD = "<b>HIRAKU</b> は、作りたいものを文で書くと、Claude が設計を作り、CAE の計算で確かめ、NG を直すまで繰り返すツールです。";
  const wordsFor = (run) =>
    /-scripted$/.test(run)
      ? HEAD + "これは台本の運転 (Claude を呼ばずに、同じ道を決まった手順でなぞる) の記録で、8 分野すべてが OK になった v5 の判定です。タイルを押すと、解析を 3D で見られます。"
      : run === "20261105-160725-offroad-4w"
        ? HEAD + "下は、Claude が回した 2 人乗りの小さなオフロード車の記録です (v1 の NG を v7 で全部 OK にしました)。"
        : HEAD + "下は、保存した記録の最後の判定です。タイルを押すと、解析を 3D で見られます。";

  const bar = document.createElement("div");
  bar.id = "judge-intro";
  bar.innerHTML = '<p class="ji-text">' + wordsFor(params.get("run") || "") + "</p>" +
    '<div class="ji-buttons"><button type="button" class="ji-play" id="ji-play">▶ 最初から再生して見る</button>' +
    '<button type="button" class="ji-result" id="ji-result">結果を見る</button>' +
    (LP ? '<a class="ji-lp" href="' + LP + '">HIRAKU の紹介ページへ</a>' : "") + "</div>" +
    '<button type="button" class="ji-close" id="ji-close" title="閉じる (右下の「？ 使い方」でまた開けます)">×</button>';
  const css = document.createElement("style");
  // fixed over the page, bottom right, so the page's own grid keeps its rows
  css.textContent =
    "#judge-intro{position:fixed;right:20px;bottom:20px;z-index:9999;max-width:520px;display:flex;flex-direction:column;gap:12px;" +
    "padding:16px 20px;background:#1c1f24;color:#fff;font-size:15px;line-height:1.6;border-radius:14px;box-shadow:0 8px 30px rgba(0,0,0,.35)}" +
    "#judge-intro .ji-text{margin:0;padding-right:22px}" +
    "#judge-intro .ji-buttons{display:flex;gap:10px;flex-wrap:wrap;align-items:center}" +
    "#judge-intro button{font:inherit;font-weight:700;border-radius:999px;padding:9px 20px;cursor:pointer;border:2px solid #fff}" +
    "#judge-intro .ji-play{background:#fff;color:#1c1f24;font-size:17px}" +
    "#judge-intro .ji-result{background:transparent;color:#fff}" +
    "#judge-intro .ji-lp{color:#cfd6df;font-size:14px;margin-left:4px}" +
    "#judge-intro .ji-close{position:absolute;top:8px;right:10px;border:0;background:transparent;color:#aaa;font-size:18px;padding:2px 6px}" +
    ".ji-head{margin-left:10px;font:inherit;font-size:14px;font-weight:700;color:#1c1f24;background:#fff;border:1.5px solid #1c1f24;" +
    "border-radius:999px;padding:4px 12px;text-decoration:none;cursor:pointer;white-space:nowrap}" +
    ".ji-head.dark{background:#1c1f24;color:#fff}";
  document.head.appendChild(css);

  const tab = (word) => [...document.querySelectorAll("#tabs button, #tabs a, #tabs [role=tab]")].find((b) => b.textContent.includes(word));
  const later = (fn, tries = 60) => { const t = fn(); if (!t && tries > 0) setTimeout(() => later(fn, tries - 1), 250); };

  function openCard() {
    if (!document.getElementById("judge-intro")) document.body.appendChild(bar);
    document.getElementById("ji-close").onclick = () => bar.remove();
    document.getElementById("ji-result").onclick = () => { bar.remove(); const t = tab("解析結果"); if (t) t.click(); };
    document.getElementById("ji-play").onclick = () => {
      bar.remove();
      const t = tab("作りたいもの");
      if (t) t.click();
      later(() => { const b = document.querySelector("[data-static-replay]"); if (b) { b.click(); return true; } return false; });
    };
  }
  // two small buttons fixed at the bottom right, shown while the card is closed: the card back, and the
  // introduction page (the header has no room: the request's words by the wordmark need it)
  const dock = document.createElement("div");
  dock.id = "ji-dock";
  dock.style.cssText = "position:fixed;right:20px;bottom:20px;z-index:9998;display:flex;gap:8px";
  function headerLinks() {
    if (document.getElementById("ji-help")) return;
    const help = document.createElement("button");
    help.type = "button"; help.id = "ji-help"; help.className = "ji-head dark"; help.textContent = "？ 使い方";
    help.onclick = openCard;
    dock.append(help);
    if (LP) {
      const lp = document.createElement("a");
      lp.className = "ji-head"; lp.href = LP; lp.textContent = "HIRAKU の紹介ページ";
      dock.append(lp);
    }
    document.body.appendChild(dock);
    new MutationObserver(() => { dock.style.display = document.getElementById("judge-intro") ? "none" : "flex"; })
      .observe(document.body, { childList: true });
  }
  // the run list: each record opens on its last moment, ④ (the results), by a full page load so the card and the
  // tab opener run; the page behind the list is covered so it does not look like an empty screen
  function listLinks() {
    if (!params.has("list")) return;
    const st = document.createElement("style");
    st.textContent = ".stage > *:not(#runlist){visibility:hidden}";
    document.head.appendChild(st);
    document.addEventListener("click", (e) => {
      const a = e.target.closest && e.target.closest('#runlist a[href^="?run="]');
      if (!a) return;
      e.preventDefault(); e.stopPropagation();
      const href = a.getAttribute("href");
      location.href = href + (href.includes("tab=") ? "" : "&tab=grid");
    }, true);
  }
  function start() {
    headerLinks();
    listLinks();
    if (params.has("list")) return;
    openCard();
    // the bare address: the run is the export's first (data/static.json), whose words the card then says
    if (!params.get("run")) {
      fetch("data/static.json").then((r) => r.json()).then((j) => {
        const run = j?.first?.run;
        const p = bar.querySelector(".ji-text");
        if (run && p) p.innerHTML = wordsFor(run);
      }).catch(() => {});
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
  // open on ④ once the tabs are there (not the run list; ?tab= from a link wins)
  if (!params.has("list") && !params.has("tab")) later(() => { const t = tab("解析結果"); if (t) { t.click(); return true; } return false; });
})();

// A small numbered label over one of the opening quick looks (N01-N04),
// on a transparent ground (?layer=fg) to lay over the app's cut.
//
//   labels.html?cut=n01            the label of N01 (n01 ... n04)
//   labels_n01.html                the same, for export.py and layout_check.py
//                                  (they take a file path, which cannot carry ?cut=)
//
// Words, placement and times: layout.json labels (CSS variables from
// layout.js with data-cut="labels"); the cut's length: layout.json
// <cut>.duration_s. The label comes in at labels.times.in_s and has faded
// out labels.times.out_before_end_s before the end of the cut.
// Load after layout.js and before timeline.js (timeline.js reads the length
// from <body data-duration> when it loads).

(function () {
  const { rules: L, cutRules: R } = window.promoLayout;
  const params = new URLSearchParams(location.search);
  const cut = params.get("cut") || document.body.dataset.labelCut || "n01";
  const words = R.cuts[cut];
  if (!words || !L[cut]) throw new Error(`labels: no label for cut "${cut}" in layout.json`);
  const duration = L[cut].duration_s;
  document.body.dataset.duration = String(duration);

  // the fade out ends out_before_end_s before the end of the cut
  const out = duration - R.times.out_before_end_s - R.out_dur_s;
  document.documentElement.style.setProperty("--t-label-out", `${out.toFixed(3)}s`);

  const style = document.createElement("style");
  style.textContent = `
    .label-ground {
      position: absolute;
      inset: 0;
      background: var(--paper);
    }
    .label {
      position: absolute;
      left: var(--box-x);
      top: var(--box-y);
      height: var(--box-h);
      max-width: var(--box-w);
      display: flex;
      align-items: center;
      gap: var(--gap-px);
      padding: 0 var(--pad-x-px);
      background: rgba(251, 249, 244, 0.9);
      border: var(--line-px) solid rgba(27, 26, 23, 0.5);
      border-radius: calc(2 * var(--u));
      color: var(--ink);
      white-space: nowrap;
      animation:
        label-in var(--in-dur) cubic-bezier(.2, .8, .3, 1) both,
        label-out var(--out-dur) ease-in forwards;
      animation-delay: var(--t-in), var(--t-label-out);
    }
    @keyframes label-in {
      0%   { clip-path: inset(0 100% 0 0); opacity: 0; }
      30%  { opacity: 1; }
      100% { clip-path: inset(0 0 0 0); opacity: 1; }
    }
    @keyframes label-out {
      from { opacity: 1; }
      to   { opacity: 0; }
    }
    .label-rule {
      width: var(--rule-w);
      height: var(--line-px);
      background: var(--ink);
    }
    .label-num {
      font: 600 var(--num-size-px) / 1.3 var(--font-mono);
      letter-spacing: 0.06em;
      color: var(--ink-soft);
    }
    .label-bar {
      width: var(--line-px);
      height: calc(0.5 * var(--box-h));
      background: rgba(27, 26, 23, 0.45);
    }
    .label-text {
      font: 500 var(--text-size-px) / 1.3 var(--font-ja);
      letter-spacing: 0.04em;
    }`;
  document.head.appendChild(style);

  document.querySelector(".stage").insertAdjacentHTML("beforeend",
    `<div class="label" data-box="labels.${cut}">
       <i class="label-rule"></i><span class="label-num">${words.num}</span><i class="label-bar"></i><span
         class="label-text">${words.text}</span></div>`);
})();

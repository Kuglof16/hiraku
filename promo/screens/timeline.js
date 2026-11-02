// One clock per screen: every frame is a function of t (seconds).
//
//   screen.html               plays in real time and loops (preview)
//   screen.html?t=12.5        frozen at t = 12.5 s (frame export, stills)
//   screen.html?guides=1      shows the subtitle band and the clock
//   screen.html?layer=fg      transparent ground, to lay over another clip
//
//   await window.promo.seek(t)   move to t; resolves once that frame is painted
//   window.promo.duration        length of the screen in seconds
//   window.promo.onSeek(fn)      a page part drawn by script, called with t
//
// CSS animations are driven through the Web Animations API: each one is
// paused and its currentTime is set from t, so CSS and script never drift.
// Animations must exist when the page loads (no classes toggled later).

(function () {
  const params = new URLSearchParams(location.search);
  const root = document.documentElement;
  if (params.get("layer") === "fg") root.classList.add("layer-fg");
  if (params.get("guides") === "1") root.classList.add("guides");

  const hooks = [];
  let current = 0;

  function apply(t) {
    current = t;
    for (const a of document.getAnimations()) {
      a.pause();
      a.currentTime = t * 1000;
    }
    for (const fn of hooks) fn(t);
    const clock = document.querySelector(".clock");
    if (clock) clock.textContent = t.toFixed(3) + " s";
  }

  function painted() {
    return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  }

  const promo = {
    duration: Number(document.body.dataset.duration || 0),
    params,
    get t() { return current; },
    onSeek(fn) { hooks.push(fn); },
    async ready() { await document.fonts.ready; },
    async seek(t) {
      await promo.ready();
      apply(Math.max(0, t));
      await painted();
      return current;
    },
  };
  window.promo = promo;

  function live() {
    let paused = false;
    let origin = performance.now();
    function tick(now) {
      if (!paused) {
        const span = promo.duration > 0 ? promo.duration : Infinity;
        apply(((now - origin) / 1000) % span);
      }
      requestAnimationFrame(tick);
    }
    document.addEventListener("keydown", (e) => {
      const step = 1 / 30;
      if (e.key === " ") {
        paused = !paused;
        origin = performance.now() - current * 1000;
      } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        paused = true;
        apply(Math.max(0, current + (e.key === "ArrowRight" ? step : -step)));
      } else if (e.key === "Home") {
        origin = performance.now();
        apply(0);
      }
    });
    requestAnimationFrame(tick);
  }

  window.addEventListener("load", () => {
    if (params.has("t")) promo.seek(Number(params.get("t")) || 0);
    else live();
  });
})();

// Side-view line drawing of the demo vehicle for the title screens: a
// one- or two-seat tube frame with no roof or doors, double wishbones,
// a battery box under the floor. Drawn in a 600x300 box, wheels on y = 228.
// Every stroke has pathLength="1" so a page can draw it on with
// stroke-dashoffset 1 -> 0; --k numbers the strokes in drawing order.

(function () {
  const circle = (cx, cy, r) =>
    `M${cx - r} ${cy}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0`;

  const WHEEL_R = 52, RIM_R = 31, HUB_R = 8;
  const REAR = [140, 228], FRONT = [468, 228];

  // [class, path] in drawing order
  const STROKES = [
    ["tube", "M78 196L532 191"],                                   // lower main rail
    ["tube", "M205 196L222 70L366 72L440 160L532 170L532 191"],    // cage hoop, top, A pillar, nose
    ["tube", "M78 196L84 150L208 150"],                             // rear deck
    ["tube", "M214 140L428 150"],                                   // side bar
    ["thin", "M222 70L248 150M366 72L352 150"],                     // cage braces
    ["thin", "M236 186L228 112Q227 102 237 102L248 102Q256 102 257 112L262 168L312 168Q320 168 320 176L320 186"], // seat
    ["thin", "M356 146L388 112M381 99L395 125"],                    // steering column and wheel
    ["box", "M252 202H398V216Q398 220 394 220H256Q252 220 252 216Z"], // battery box
    ["thin", "M432 176L468 214M434 192L468 236M422 140L462 214"],  // front wishbones, damper
    ["thin", "M190 176L140 214M188 192L140 236M200 140L146 214"],  // rear wishbones, damper
    ["tyre", circle(REAR[0], REAR[1], WHEEL_R)],
    ["tyre", circle(FRONT[0], FRONT[1], WHEEL_R)],
    ["thin", circle(REAR[0], REAR[1], RIM_R) + circle(REAR[0], REAR[1], HUB_R)],
    ["thin", circle(FRONT[0], FRONT[1], RIM_R) + circle(FRONT[0], FRONT[1], HUB_R)],
  ];

  // Stroke widths in the 600x300 box.
  const WIDTH = { tube: 6, thin: 3.5, box: 3.5, tyre: 7 };

  function drawVehicle(opts = {}) {
    const halo = opts.halo; // draw a dark stroke under each line (reads over a light band)
    const scale = opts.strokeScale || 1;
    const lines = STROKES.map(([cls, d], k) => {
      const w = WIDTH[cls] * scale;
      const under = halo
        ? `<path class="v-halo" d="${d}" pathLength="1" style="--k:${k}" stroke-width="${w + 7 * scale}"/>`
        : "";
      return `${under}<path class="v-line v-${cls}" d="${d}" pathLength="1" style="--k:${k}" stroke-width="${w}"/>`;
    }).join("");
    return `<svg class="vehicle-svg" viewBox="0 0 600 300" fill="none"
      stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${lines}</svg>`;
  }

  window.drawVehicle = drawVehicle;
  window.VEHICLE_STROKES = STROKES.length;
})();

// Flat drawing of an agent pet for the CSS screens: a round body made of
// triangle elements with nodes, faces tinted in stress colours, white eyes,
// a marker node on the antenna in the agent's colour, and the agent's prop.
// Drawn in a 200x200 box; the body is centred at (90, 108).

(function () {
  const CX = 90, CY = 108, R_OUT = 52, R_IN = 27;

  // Small fixed offsets so the body reads as a hand-made mesh, not a polygon.
  const RIM_JITTER = [0, 3, -2, 2, -3, 1, 3, -1, -3, 2, 0, -2];

  function ring(n, r, phaseDeg, jitter) {
    const pts = [];
    for (let i = 0; i < n; i++) {
      const a = ((phaseDeg + (360 * i) / n) * Math.PI) / 180;
      const rr = r + (jitter ? jitter[i % jitter.length] : 0);
      pts.push([CX + rr * Math.cos(a), CY + rr * Math.sin(a)]);
    }
    return pts;
  }

  // centre + inner ring of 6 + outer ring of 12 -> 24 triangles
  function meshTriangles() {
    const c = [CX + 2, CY - 1];
    const inner = ring(6, R_IN, -90 + 8, null);
    const outer = ring(12, R_OUT, -90 + 8, RIM_JITTER);
    const tris = [];
    for (let i = 0; i < 6; i++) {
      const j = (i + 1) % 6;
      tris.push([c, inner[i], inner[j]]);
      const o0 = outer[2 * i], o1 = outer[2 * i + 1], o2 = outer[(2 * i + 2) % 12];
      tris.push([inner[i], o0, o1]);
      tris.push([inner[i], o1, inner[j]]);
      tris.push([inner[j], o1, o2]);
    }
    return tris;
  }

  // Face colour from height: low faces blue, high faces green-yellow.
  const STOPS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)"];
  function faceColour(tri) {
    const y = (tri[0][1] + tri[1][1] + tri[2][1]) / 3;
    const v = 1 - (y - (CY - R_OUT)) / (2 * R_OUT); // 0 bottom .. 1 top
    const k = Math.min(STOPS.length - 1, Math.max(0, Math.floor(v * 3.2)));
    return STOPS[k];
  }

  const f = (p) => p[0].toFixed(1) + "," + p[1].toFixed(1);

  const PROPS = {
    // steering wheel
    handling: `<circle cx="164" cy="136" r="15" class="pr"/>
      <path d="M149 136h30M164 136v15" class="pr"/>
      <circle cx="164" cy="136" r="4" class="pr fill-ink"/>`,
    // battery with a bolt
    powertrain: `<rect x="152" y="117" width="24" height="36" rx="3" class="pr fill-paper"/>
      <rect x="159" y="112" width="10" height="5" rx="1" class="pr fill-ink"/>
      <path d="M167 122l-9 14h7l-4 12 10-16h-7z" fill="var(--d-powertrain)" stroke="var(--ink)" stroke-width="1.6" stroke-linejoin="round"/>`,
    // windsock on a pole
    aero: `<path d="M150 160V114" class="pr"/>
      <path d="M150 116l34 6v10l-34 6z" class="pr fill-paper"/>
      <path d="M161 118v18M172 120v14" stroke="var(--d-aero)" stroke-width="5"/>
      <path d="M150 116l34 6v10l-34 6z" class="pr"/>`,
    // crash-test target, four quadrants
    crash: `<circle cx="165" cy="136" r="15" fill="var(--paper)"/>
      <path d="M165 136V121A15 15 0 0 1 180 136zM165 136V151A15 15 0 0 1 150 136z" fill="var(--ink)"/>
      <path d="M165 136H180A15 15 0 0 1 165 151zM165 136H150A15 15 0 0 1 165 121z" fill="var(--d-powertrain)"/>
      <circle cx="165" cy="136" r="15" class="pr"/>`,
    // I-beam section
    strength: `<path d="M150 118h30v7h-11v22h11v7h-30v-7h11v-22h-11z" fill="var(--ink-soft)" stroke="var(--ink)" stroke-width="2" stroke-linejoin="round"/>`,
    // tuning fork with sound arcs
    nvh: `<path d="M157 112v18a8 8 0 0 0 16 0v-18M165 138v18" class="pr"/>
      <path d="M181 116a9 9 0 0 1 0 12M148 116a9 9 0 0 0 0 12" stroke="var(--d-nvh)" stroke-width="2.4" fill="none" stroke-linecap="round"/>`,
    // thermometer
    thermal: `<path d="M160 112a5 5 0 0 1 10 0v26a10 10 0 1 1-10 0z" class="pr fill-paper"/>
      <path d="M165 122v22" stroke="var(--d-thermal)" stroke-width="4" stroke-linecap="round"/>
      <circle cx="165" cy="147" r="6.5" fill="var(--d-thermal)"/>
      <path d="M160 112a5 5 0 0 1 10 0v26a10 10 0 1 1-10 0z" class="pr"/>`,
  };

  function drawPet(domain) {
    const faces = meshTriangles()
      .map((t) => `<path d="M${f(t[0])}L${f(t[1])}L${f(t[2])}Z" fill="${faceColour(t)}"/>`)
      .join("");
    return `<svg class="pet-svg" viewBox="0 0 200 200" aria-hidden="true">
      <g class="pet-legs" stroke="var(--ink)" stroke-width="4" stroke-linecap="round">
        <path d="M76 154v22M104 156v20"/>
      </g>
      <path class="pet-antenna" d="M90 57L97 31" stroke="var(--ink)" stroke-width="3" stroke-linecap="round"/>
      <circle class="pet-node" cx="97" cy="27" r="8" fill="var(--d-${domain})" stroke="var(--ink)" stroke-width="2.5"/>
      <g class="pet-mesh" stroke="var(--ink)" stroke-width="2" stroke-linejoin="round">${faces}</g>
      <g class="pet-eyes">
        <circle cx="78" cy="101" r="9.5" fill="#fff" stroke="var(--ink)" stroke-width="2.2"/>
        <circle cx="104" cy="101" r="9.5" fill="#fff" stroke="var(--ink)" stroke-width="2.2"/>
        <circle cx="80" cy="102" r="4.2" fill="var(--ink)"/>
        <circle cx="106" cy="102" r="4.2" fill="var(--ink)"/>
      </g>
      <path class="pet-arm" d="M139 124L153 133" stroke="var(--ink)" stroke-width="3.5" stroke-linecap="round"/>
      <g class="pet-prop" fill="none" stroke-linecap="round" stroke-linejoin="round">${PROPS[domain] || ""}</g>
    </svg>`;
  }

  window.drawPet = drawPet;
})();

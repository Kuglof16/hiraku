// Line-drawn loops that show how each preset test is run, for the N05 tiles.
// Each tile is a 400x170 SVG box with the ground on y = 152. The motion is
// in tests.css (CSS animations only, so timeline.js can seek them); this
// file only draws the parts and gives them the classes that move.
// The pictures show the method of a test, never a result: no numbers,
// no ticks, no pass/fail marks.

(function () {
  const S = 250 / 600;                       // vehicle.js box -> tile
  const GROUND_Y = 152;

  // Split the vehicle into body and wheels so a wheel can move on its own.
  const strokes = drawVehicle({ strokeScale: 1.6 }).match(/<path[^>]*\/>/g);
  const kOf = (p) => Number(/--k:(\d+)/.exec(p)[1]);
  const pick = (keep) => strokes.filter((p) => keep(kOf(p))).join("");
  const WHEEL_K = [10, 11, 12, 13];          // tyres, rims and hubs
  const SUSP_K = [8, 9];                     // wishbones and dampers
  const ALL = strokes.join("");
  const BODY = pick((k) => !WHEEL_K.includes(k) && !SUSP_K.includes(k));
  const WHEELS = pick((k) => WHEEL_K.includes(k) || SUSP_K.includes(k));
  const BOX_D = /d="([^"]+)"/.exec(strokes.find((p) => p.includes("v-box")))[1];
  const HOT = `<path class="hot" d="${BOX_D}"/>`;  // fill under the battery box line

  // Vehicle (600x300 box) placed with its rear-left corner at x0 and its
  // wheels resting on `base`. Rear wheel centre: x0 + 58.3, front: x0 + 195.
  const vehicle = (x0, inner = ALL, base = GROUND_Y) =>
    `<g transform="translate(${x0} ${base - 280 * S}) scale(${S})" fill="none"
        stroke-linecap="round" stroke-linejoin="round">${inner}</g>`;

  const arrow = (x1, y1, x2, y2, cls = "mark") => {
    const a = Math.atan2(y2 - y1, x2 - x1), h = 10;
    const p = (s) => `${x2 - h * Math.cos(a + s)},${y2 - h * Math.sin(a + s)}`;
    return `<path class="${cls}" d="M${x1} ${y1}L${x2} ${y2}M${p(0.5)}L${x2},${y2}L${p(-0.5)}"/>`;
  };
  const ground = (x1 = 20, x2 = 380) => `<path class="mark" d="M${x1} ${GROUND_Y}H${x2}"/>`;
  // a smooth wave of `n` half-periods of length `w` starting at (x, y)
  const wave = (x, y, w, amp, n) =>
    `M${x} ${y}q${w / 2} ${-2 * amp} ${w} 0` + `t${w} 0`.repeat(n - 1);
  // a wavy line of heat rising from (x, 108)
  const riser = (x) => `M${x} 108q-8 -6 0 -12t0 -12t0 -12`;

  // Top view of the vehicle for the lane change, centred on (0, 0).
  const TOP_VIEW = `
    <rect class="solid-ink" x="-26" y="-21" width="15" height="6" rx="2"/>
    <rect class="solid-ink" x="-26" y="15" width="15" height="6" rx="2"/>
    <rect class="solid-ink" x="15" y="-21" width="15" height="6" rx="2"/>
    <rect class="solid-ink" x="15" y="15" width="15" height="6" rx="2"/>
    <path class="ink" d="M-36 -13H26Q38 -13 38 -4V4Q38 13 26 13H-36Z"/>
    <path class="ink-thin" d="M-14 -13V13M6 -13L12 0L6 13"/>`;

  const TILES = [
    ["段差越え", "step",
      `<path class="mark" d="M20 152H286V134H384"/>
       <g class="lp car">${vehicle(60)}</g>`],

    ["傾斜台", "tilt",
      `${ground()}
       <path class="solid" d="M50 146L40 152H60Z"/>
       <g class="lp deck">
         <path class="solid" d="M50 140H350V148H50Z"/>
         ${vehicle(100, ALL, 140)}
       </g>`],

    ["横風", "wind",
      `${ground()}
       <path class="lp gust" d="M8 70H172"/>
       <path class="lp gust" d="M40 96H172"/>
       <path class="lp gust" d="M22 122H172"/>
       <g class="lp car">${vehicle(160)}</g>`],

    ["オフロード走行", "rough",
      `<path class="lp terrain mark" d="${wave(-40, 148, 40, 6, 16)}"/>
       <g class="lp car">${vehicle(90, ALL, 146)}</g>`],

    ["DLC", "dlc",
      [[30, 106], [70, 106], [110, 106], [30, 150], [70, 150], [110, 150],
       [190, 54], [230, 54], [190, 100], [230, 100],
       [310, 106], [350, 106], [390, 106], [310, 150], [350, 150], [390, 150]]
        .map(([x, y]) => `<circle class="cone" cx="${x}" cy="${y}" r="4"/>`).join("") +
      `<g class="lp car">${TOP_VIEW}</g>`],

    ["急ブレーキ", "brake",
      `${ground()}
       <path class="lp skid" pathLength="1" d="M78 158H148"/>
       <path class="lp skid" pathLength="1" d="M255 158H325"/>
       <g class="lp car">${vehicle(100)}</g>`],

    ["長時間", "long",
      `<path class="lp road" d="M20 152H380"/>
       <g class="lp spin">
         <path class="mark" d="M370 62a20 20 0 1 1-6-14"/>
         <path class="mark" d="M354 40l10 8-12 4"/>
       </g>
       ${vehicle(80, HOT + ALL)}`],

    ["発進", "launch",
      `${ground()}
       <g class="lp car">
         <g class="lp streaks">
           <path class="mark" d="M30 96H68M14 116H66M30 136H62"/>
         </g>
         <g class="lp squat">${vehicle(40)}</g>
       </g>`],

    ["衝突", "crash",
      `${ground()}
       <path class="solid" d="M352 40H376V152H352Z"/>
       <g class="lp car">${vehicle(130)}</g>
       <g class="lp burst">
         <path class="spark" d="M344 84L330 74M342 104H324M344 124L330 134"/>
       </g>`],

    ["音振", "nvh",
      `${ground()}
       <clipPath id="nvh-clip"><rect x="262" y="38" width="124" height="44"/></clipPath>
       <g clip-path="url(#nvh-clip)">
         <path class="lp hum" d="${wave(238, 52, 12, 5, 14)}"/>
         <path class="lp hum" d="${wave(238, 72, 12, 5, 14)}"/>
       </g>
       <g class="lp shake-body">${vehicle(75, BODY)}</g>
       <g class="lp shake-wheel">${vehicle(75, WHEELS)}</g>`],

    ["疲労", "fatigue",
      `<path class="solid" d="M28 66H44V144H28Z"/>
       <path class="mark" d="M28 78l-8 8M28 98l-8 8M28 118l-8 8M28 138l-6 6"/>
       <g class="lp arm"><path class="solid" d="M44 98H186Q194 98 194 105Q194 112 186 112H44Z"/></g>
       <g class="lp push">${arrow(188, 52, 188, 92)}</g>
       <path class="mark" d="M246 44V140H384"/>
       <path class="lp sn" pathLength="1" d="M256 54C280 56 292 116 378 122"/>`],

    ["熱", "heat",
      `${ground()}
       ${vehicle(110, HOT + ALL)}
       <path class="lp rise" d="${riser(224)}"/>
       <path class="lp rise r2" d="${riser(246)}"/>
       <path class="lp rise r3" d="${riser(268)}"/>`],
  ];

  window.TEST_TILES = TILES;
})();

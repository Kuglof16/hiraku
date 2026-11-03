// 3D agent pets for HIRAKU, built with three.js.
//
// A plain script (no ES module imports) so it can be loaded from file:// with
// <script src>. The caller passes the THREE namespace in:
//
//   const pet = window.HirakuPets.create(THREE, { role: "cae.crash" });
//   scene.add(pet.group);
//   pet.update(t, "idle", camera);   // pose depends only on t and the state
//   pet.contour(t, "working");       // { quantity, max, min, info } for a legend
//
// v3: every role has its own body, drawn the way a result of that analysis
// looks in a post-processor (BODIES below): a flow body with streamlines for
// aero, a stress wave for crash, a mode shape for NVH, and so on. The eyes,
// the antenna with the role colour, the legs, the arms and the prop are
// shared, so the ten pets stay one family.
// v4: lead is the v2 body again (triangles, stress from the limb loads);
// packager is an assembly drawing without a contour; handling, powertrain
// (combustion) and crash are new bodies; designer has no dimension lines;
// the strength arrow sits outside the surface.
// v5: designer is a CAD model (one colour, fillet tangent edges, parameter
// lines; no contour); packager is one tightly fitted unit in two colours;
// handling is a multibody model (chassis, wishbones, knuckles, wheels,
// joints, spring-dampers); strength is a plate with a hole pulled from both
// sides (stress concentration at the hole edge).
// v6: nvh moves slower and smoother and its undeformed lines no longer blink;
// aero shows the pressure (stagnation at the nose = high); packager is a 3D
// puzzle of interlocking pieces; the handling chassis is metal; a new state
// "run" (in place, or across with opts.run = "across").
// v7: packager is a six-piece burr (six square bars along the three axes
// that interlock in the middle); opts.packagerStyle = "rings" gives three
// interlocked metal rings instead.
//
// Every value shown in a contour is computed here from where things act on the
// body (loads, flow direction, heat source, crush, mode shape...). These are
// small toy models chosen to place the colours; none of them is a solved field.
//
// Colours: the contour bands (BANDS = 10, STOPS blue -> red) are the same as
// web/js/colormap.js; the CAE domain colours --d-*, --paper, --night,
// --pass, --miss, --s4/--s5 (props) and --ink-* come from
// promo/screens/common.css; lead / designer / packager colours come from
// PET_COLORS in web/js/app.js.

(function () {
  "use strict";

  const C = {
    ink: 0x1b1a17,
    inkSoft: 0x5c5a54,
    inkFaint: 0xa8a49a,
    paper: 0xf4f1ea,
    night: 0x131311,
    onNightSoft: 0xb9b3a5,
    glow: 0xf6ead0,
    pass: 0x2e7d4f,
    miss: 0xe0472a,
    s: [0x3461c8, 0x2fa2c3, 0x45b07a, 0xe2b13d, 0xd6492f],
  };

  const ROLES = [
    { id: "lead", name: "まとめ役", color: "#e9b872", prop: "clipboard", propName: "バインダー" },
    { id: "designer", name: "設計役", color: "#2fa7d6", prop: "pencil", propName: "鉛筆" },
    { id: "packager", name: "パッケージ役", color: "#8e6bbf", prop: "spanner", propName: "スパナ" },
    { id: "cae.handling", name: "操安", color: "#3461c8", prop: "wheel", propName: "ハンドル" },
    { id: "cae.powertrain", name: "動力", color: "#e2b13d", prop: "battery", propName: "電池" },
    { id: "cae.aero", name: "空力", color: "#2fa2c3", prop: "windsock", propName: "吹き流し" },
    { id: "cae.crash", name: "衝突", color: "#d6492f", prop: "target", propName: "衝突試験の的" },
    { id: "cae.strength", name: "強度信頼性", color: "#55524b", prop: "ibeam", propName: "I 形の梁" },
    { id: "cae.nvh", name: "音振", color: "#8a5cc2", prop: "fork", propName: "音叉" },
    { id: "cae.thermal", name: "熱", color: "#e0682a", prop: "thermometer", propName: "温度計" },
  ];

  // One body per role: what analysis picture it is and what the colour shows.
  // quantity null = no contour on that body (and no legend).
  const BODIES = {
    "lead": { kind: "fe-sphere", quantity: "相対の応力 (自重と手足)", analysis: "三角の要素の丸い体 (v2 の体)。節点の荷重から計算した応力" },
    "designer": { kind: "cad-model", quantity: null, analysis: "CAD のモデル。落ち着いた単色の面、フィレットの面の境の稜線、平らな面の薄いパラメータの線" },
    "packager": { kind: "assembly", quantity: null, analysis: "立体のパズル (6 本組木)。断面が四角い棒 6 本が 3 つの向きに貫き合い、切り欠きで噛み合って 1 つの塊になる。色は向きごとに 3 色" },
    "cae.handling": { kind: "multibody", quantity: "ばねの力 (相対)", analysis: "マルチボディの機構。金属の車体の剛体に上下のアーム・ナックル・車輪がジョイントでつながり、ばねとダンパー。重心の座標の軸と横加速度の矢印" },
    "cae.powertrain": { kind: "combustion", quantity: "燃焼の温度 (相対)", analysis: "エンジンの燃焼室。点火プラグから広がる火炎面の温度、下はピストン、脇に電池のセル" },
    "cae.aero": { kind: "flow", quantity: "圧力 (相対)", analysis: "流体でできたしずく形の体と流線 (CFD の圧力。先端のよどみ点が高く、肩が低い)" },
    "cae.crash": { kind: "impact-wave", quantity: "衝撃の応力 (相対)", analysis: "前の面に当たった所から広がる応力の波、小さなへこみ、衝撃の輪、減速度の線" },
    "cae.strength": { kind: "stress-concentration", quantity: "応力集中 (公称からの上がり分)", analysis: "穴のある板の体を両側から引っぱる。穴の上下のふちに応力が集中する" },
    "cae.nvh": { kind: "mode-shape", quantity: "変位 (相対)", analysis: "固有モードで揺れる網目と、変形前の形の薄い線" },
    "cae.thermal": { kind: "heat", quantity: "温度 (相対)", analysis: "下から温まる温度のコンターと等温線、立ちのぼる熱の線" },
  };

  const STATES = ["idle", "report", "pass", "mismatch", "working", "run"];
  // run: one stride (each leg forward and back once) in RUN.period seconds;
  // "across" crosses RUN.span pet units to the right in RUN.cross seconds
  const RUN = { period: 0.6, swing: 0.8, bob: 0.09, lean: 0.17, span: 7.2, cross: 3.0, yaw: 0.75 };

  // icosphere subdivision per mesh level for the stress body (three.js
  // IcosahedronGeometry detail)
  const DETAIL = { coarse: 1, fine: 2 };

  // Layout in pet units (ground at y = 0, body centre at y = BODY_Y).
  const BODY_Y = 1.22;
  const SHAPE = { sx: 1.0, sy: 0.93, sz: 0.9, egg: 0.07 };
  // Box the viewer frames: covers feet, prop and the marks above the head.
  const FRAME = { cx: 0.32, cy: 1.87, w: 3.6, h: 4.0 };

  // ---- contour colours: same values as web/js/colormap.js ------------------
  const BANDS = 10;
  const STOPS = ["#2b59c3", "#2fa7d6", "#4cbf7a", "#e8c547", "#e9893a", "#c8402a"];

  function hexToRgb(h) {
    const n = parseInt(h.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function rgbToHex(c) {
    return "#" + c.map((x) => Math.round(x).toString(16).padStart(2, "0")).join("");
  }
  function lerpStops(u) {
    const x = Math.max(0, Math.min(1, u)) * (STOPS.length - 1);
    const i = Math.min(STOPS.length - 2, Math.floor(x));
    const f = x - i;
    const a = hexToRgb(STOPS[i]);
    const b = hexToRgb(STOPS[i + 1]);
    return rgbToHex(a.map((v, k) => v + (b[k] - v) * f));
  }
  const BAND_COLORS = Array.from({ length: BANDS }, (_, i) => lerpStops((i + 0.5) / BANDS));

  // ---- loads (relative units, toy model) -----------------------------------
  const LOAD = {
    bodyWeight: 1.0,   // carried half by each leg root (settled case)
    armWeight: 0.06,   // the empty left arm
    spread: 0.45,      // decay length of a root concentration (pet units)
    lever: 0.11,       // arm root amplitude = weight * arm length / lever
    membrane: 0.12,    // self-weight membrane term, zero at the top
    kt: 4.0,           // mismatch: whole weight on the left leg, notch factor kt
  };
  // weight of each prop held in the right hand
  const PROP_MASS = {
    clipboard: 0.3, pencil: 0.24, spanner: 0.34, wheel: 0.4, battery: 0.48,
    windsock: 0.3, target: 0.36, ibeam: 0.5, fork: 0.26, thermometer: 0.24,
  };

  // Load factor over time: the working state steps the load up in 10
  // increments (0 -> 1 in 3.2 s), holds it, and starts again every 4.4 s.
  function loadFactor(t, state) {
    if (state !== "working") return 1;
    const k = Math.floor((((t % 4.4) + 4.4) % 4.4) / 0.32);
    return Math.min(1, k / 10);
  }
  // Smooth progress of the working cycle: 0 -> 1 in 3.2 s, hold, repeat 4.4 s.
  function ramp(t) {
    const x = Math.min(1, (((t % 4.4) + 4.4) % 4.4) / 3.2);
    return x * x * (3 - 2 * x);
  }

  // ---- deterministic helpers -------------------------------------------

  function mulberry32(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function hashString(s) {
    let h = 2166136261;
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  const clamp = (x, a, b) => Math.min(b, Math.max(a, x));
  const TAU = Math.PI * 2;

  // erfc, Abramowitz & Stegun 7.1.26 (|error| < 1.5e-7)
  function erfc(x) {
    const s = x < 0 ? -1 : 1;
    const z = Math.abs(x);
    const k = 1 / (1 + 0.3275911 * z);
    const y = k * (0.254829592 + k * (-0.284496736 + k * (1.421413741 + k * (-1.453152027 + k * 1.061405429))));
    const erf = 1 - y * Math.exp(-z * z);
    return 1 - s * erf;
  }

  // Body surface point for a unit direction (slightly egg shaped, wider low).
  function shapePoint(THREE, d, egg) {
    const k = 1 + (egg ?? SHAPE.egg) * -d.y;
    return new THREE.Vector3(d.x * SHAPE.sx * k, d.y * SHAPE.sy, d.z * SHAPE.sz * k);
  }
  // Superellipsoid: scale a unit direction onto |x|^n + |y|^n + |z|^n = 1.
  function superDir(d, n) {
    const s = Math.pow(Math.pow(Math.abs(d.x), n) + Math.pow(Math.abs(d.y), n) + Math.pow(Math.abs(d.z), n), -1 / n);
    return d.clone().multiplyScalar(s);
  }
  function latDir(THREE, lat, phi) {
    return new THREE.Vector3(Math.cos(lat) * Math.sin(phi), Math.sin(lat), Math.cos(lat) * Math.cos(phi));
  }

  // Raw nodal stress at body point p for a list of loads
  // [{ at: Vector3 (a node), amp }]. Each root load decays as a point load on
  // a shell, amp / (1 + (d / spread)^2)^1.5. The membrane term is the weight
  // of the shell above a horizontal cut over the cut length,
  // sqrt((1 - y) / (1 + y)), taken constant below the leg roots (yLow).
  function rawStress(p, loads, yLow) {
    const y = clamp(p.y / SHAPE.sy, yLow, 1);
    let v = LOAD.membrane * Math.sqrt((1 - y) / (1 + y));
    for (const L of loads) {
      const q = p.distanceTo(L.at) / LOAD.spread;
      v += L.amp / Math.pow(1 + q * q, 1.5);
    }
    return v;
  }

  // Nodal fields for the settled case and the mismatch case. `roots` are the
  // limb roots in body coordinates; each load goes to the nearest node.
  function stressFields(pts, roots, propMass) {
    const nearest = (r) => pts.reduce((b, p) => (p.distanceTo(r) < b.distanceTo(r) ? p : b), pts[0]);
    const at = {};
    for (const k of Object.keys(roots)) if (roots[k].isVector3) at[k] = nearest(roots[k]);
    const armR = (propMass * roots.armLenR) / LOAD.lever;
    const armL = (LOAD.armWeight * roots.armLenL) / LOAD.lever;
    const settled = [
      { name: "legL", at: at.legL, amp: LOAD.bodyWeight / 2 },
      { name: "legR", at: at.legR, amp: LOAD.bodyWeight / 2 },
      { name: "armR", at: at.armR, amp: armR },
      { name: "armL", at: at.armL, amp: armL },
    ];
    const mismatch = [
      { name: "legL", at: at.legL, amp: LOAD.bodyWeight * LOAD.kt },
      { name: "armR", at: at.armR, amp: armR },
      { name: "armL", at: at.armL, amp: armL },
    ];
    const yLow = roots.legL.y / SHAPE.sy;
    const a = pts.map((p) => rawStress(p, settled, yLow));
    const b = pts.map((p) => rawStress(p, mismatch, yLow));
    const ref = Math.max(...a);
    return {
      settled: a.map((v) => v / ref),
      mismatch: b.map((v) => v / ref),
      loads: settled.map((L) => ({ name: L.name, node: pts.indexOf(L.at), amp: L.amp / ref })),
      mismatchLoads: mismatch.map((L) => ({ name: L.name, node: pts.indexOf(L.at), amp: L.amp / ref })),
    };
  }

  // Weld a non-indexed geometry into nodes + triangles.
  function weld(positions) {
    const map = new Map();
    const nodes = [];
    const tris = [];
    for (let i = 0; i < positions.length; i += 9) {
      const tri = [];
      for (let k = 0; k < 3; k++) {
        const x = positions[i + 3 * k], y = positions[i + 3 * k + 1], z = positions[i + 3 * k + 2];
        const key = Math.round(x * 1e4) + "," + Math.round(y * 1e4) + "," + Math.round(z * 1e4);
        let idx = map.get(key);
        if (idx === undefined) {
          idx = nodes.length;
          map.set(key, idx);
          nodes.push([x, y, z]);
        }
        tri.push(idx);
      }
      tris.push(tri);
    }
    return { nodes, tris };
  }

  // Smooth-normal copy of any geometry (for outline hulls).
  function smoothCopy(THREE, geom) {
    const g = geom.index ? geom.toNonIndexed() : geom;
    const w = weld(g.attributes.position.array);
    const out = new THREE.BufferGeometry();
    out.setAttribute("position", new THREE.Float32BufferAttribute(w.nodes.flat(), 3));
    out.setIndex(w.tris.flat());
    out.computeVertexNormals();
    return out;
  }

  // ---- meshes made of nodes and elements ---------------------------------

  // Surface of revolution: prof[0] and prof[m] are the poles, the rings in
  // between have nu nodes. map(p, phi) -> Vector3. visU(i) / visV(j) say
  // whether the meridian i / ring j is drawn as a line.
  function revolve(THREE, prof, nu, map, visU, visV) {
    visU = visU || (() => true);
    visV = visV || (() => true);
    const m = prof.length - 1;
    const pts = [map(prof[0], 0)];
    const par = [[0, 0]];
    for (let j = 1; j < m; j++) {
      for (let i = 0; i < nu; i++) {
        pts.push(map(prof[j], (TAU * i) / nu));
        par.push([i, j]);
      }
    }
    pts.push(map(prof[m], 0));
    par.push([0, m]);
    const id = (i, j) => 1 + (j - 1) * nu + (((i % nu) + nu) % nu);
    const vu = (i) => visU(((i % nu) + nu) % nu);
    const N = pts.length - 1;
    const elems = [];
    for (let i = 0; i < nu; i++) {
      elems.push({ n: [0, id(i, 1), id(i + 1, 1)], vis: [vu(i), visV(1), vu(i + 1)] });
      for (let j = 1; j < m - 1; j++) {
        elems.push({ n: [id(i, j), id(i + 1, j), id(i + 1, j + 1), id(i, j + 1)], vis: [visV(j), vu(i + 1), visV(j + 1), vu(i)] });
      }
      elems.push({ n: [id(i, m - 1), id(i + 1, m - 1), N], vis: [visV(m - 1), vu(i + 1), vu(i)] });
    }
    return { pts, elems, par, nu, m };
  }

  // Box surface with quad elements, centred at the origin; half sizes h,
  // divisions n = [nx, ny, nz].
  function boxGrid(THREE, h, n) {
    const map = new Map();
    const pts = [];
    const node = (p) => {
      const key = p.map((v) => Math.round(v * 1e4)).join(",");
      let i = map.get(key);
      if (i === undefined) {
        i = pts.length;
        map.set(key, i);
        pts.push(new THREE.Vector3(p[0], p[1], p[2]));
      }
      return i;
    };
    const elems = [];
    for (let a = 0; a < 3; a++) {
      const b = (a + 1) % 3, c = (a + 2) % 3;
      for (const s of [-1, 1]) {
        const id = (i, j) => {
          const p = [0, 0, 0];
          p[a] = s * h[a];
          p[b] = -h[b] + (2 * h[b] * i) / n[b];
          p[c] = -h[c] + (2 * h[c] * j) / n[c];
          return node(p);
        };
        for (let i = 0; i < n[b]; i++) for (let j = 0; j < n[c]; j++) elems.push({ n: [id(i, j), id(i + 1, j), id(i + 1, j + 1), id(i, j + 1)] });
      }
    }
    return { pts, elems };
  }

  // Unique element edges (not the quad diagonals): [[i, j], ...]
  function elemEdges(elems) {
    const seen = new Set();
    const out = [];
    for (const e of elems) {
      const n = e.n || e;
      for (let k = 0; k < n.length; k++) {
        const a = n[k], b = n[(k + 1) % n.length];
        const key = a < b ? a + ":" + b : b + ":" + a;
        if (!seen.has(key)) {
          seen.add(key);
          out.push([a, b]);
        }
      }
    }
    return out;
  }

  // A finite-element style surface: nodes + tri / quad elements, drawn with
  // per-node values (aVal) and element edges from barycentrics (aBary). A quad
  // is two triangles whose shared diagonal is never drawn; an element edge
  // flagged invisible is not drawn either.
  // o.keepWinding: the elements are already wound outward (bodies with a
  // hole, where "away from the centre" is not outward). An element may carry
  // w = [weight per element edge] (0 none, 0.5 parameter line, 1 edge line)
  // for the CAD material; it goes to aEdgeW (per triangle, the edge opposite
  // each corner).
  function feMesh(THREE, pts0, elems, o) {
    o = Object.assign({ smooth: false, center: null, keepWinding: false }, o || {});
    const center = o.center || new THREE.Vector3();
    const tris = [];
    for (const e of elems) {
      const n = e.n, vis = e.vis || [true, true, true, true], w = e.w || [0, 0, 0, 0];
      if (n.length === 3) tris.push({ n: [n[0], n[1], n[2]], hide: [!vis[1], !vis[2], !vis[0]], w: [w[1], w[2], w[0]] });
      else {
        tris.push({ n: [n[0], n[1], n[2]], hide: [!vis[1], true, !vis[0]], w: [w[1], 0, w[0]] });
        tris.push({ n: [n[0], n[2], n[3]], hide: [!vis[2], !vis[3], true], w: [w[2], w[3], 0] });
      }
    }
    // outward winding, decided once on the reference shape
    const ab = new THREE.Vector3(), ac = new THREE.Vector3(), cr = new THREE.Vector3(), cen = new THREE.Vector3();
    for (const T of o.keepWinding ? [] : tris) {
      const a = pts0[T.n[0]], b = pts0[T.n[1]], c = pts0[T.n[2]];
      cr.crossVectors(ab.subVectors(b, a), ac.subVectors(c, a));
      cen.copy(a).add(b).add(c).multiplyScalar(1 / 3).sub(center);
      if (cr.dot(cen) < 0) {
        [T.n[1], T.n[2]] = [T.n[2], T.n[1]];
        [T.hide[1], T.hide[2]] = [T.hide[2], T.hide[1]];
        [T.w[1], T.w[2]] = [T.w[2], T.w[1]];
      }
    }
    const nT = tris.length, nN = pts0.length;
    const pos = new Float32Array(nT * 9), nor = new Float32Array(nT * 9), bar = new Float32Array(nT * 9), val = new Float32Array(nT * 3);
    const ew = new Float32Array(nT * 9);
    tris.forEach((T, i) => {
      for (let k = 0; k < 3; k++) for (let j = 0; j < 3; j++) {
        bar[i * 9 + k * 3 + j] = j === k || T.hide[j] ? 1 : 0;
        ew[i * 9 + k * 3 + j] = T.w[j];
      }
    });
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geom.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
    geom.setAttribute("aBary", new THREE.BufferAttribute(bar, 3));
    geom.setAttribute("aEdgeW", new THREE.BufferAttribute(ew, 3));
    geom.setAttribute("aVal", new THREE.BufferAttribute(val, 1));
    const hp = new Float32Array(nN * 3);
    const hullGeom = new THREE.BufferGeometry();
    hullGeom.setAttribute("position", new THREE.BufferAttribute(hp, 3));
    hullGeom.setIndex(tris.flatMap((T) => T.n));

    const pts = pts0.map((p) => p.clone());
    const nn = pts0.map(() => new THREE.Vector3());
    const fn = tris.map(() => new THREE.Vector3());
    // NN: optional exact node normals (used for smooth shading)
    function setNodes(P, NN) {
      for (let i = 0; i < nN; i++) {
        pts[i].copy(P[i]);
        hp[i * 3] = P[i].x; hp[i * 3 + 1] = P[i].y; hp[i * 3 + 2] = P[i].z;
        nn[i].set(0, 0, 0);
      }
      tris.forEach((T, i) => {
        const a = pts[T.n[0]], b = pts[T.n[1]], c = pts[T.n[2]];
        fn[i].crossVectors(ab.subVectors(b, a), ac.subVectors(c, a));
        for (const k of T.n) nn[k].add(fn[i]);
        fn[i].normalize();
      });
      for (const v of nn) v.normalize();
      if (NN) for (let i = 0; i < nN; i++) nn[i].copy(NN[i]);
      tris.forEach((T, i) => {
        for (let k = 0; k < 3; k++) {
          const p = pts[T.n[k]], q = o.smooth ? nn[T.n[k]] : fn[i];
          pos[i * 9 + k * 3] = p.x; pos[i * 9 + k * 3 + 1] = p.y; pos[i * 9 + k * 3 + 2] = p.z;
          nor[i * 9 + k * 3] = q.x; nor[i * 9 + k * 3 + 1] = q.y; nor[i * 9 + k * 3 + 2] = q.z;
        }
      });
      geom.attributes.position.needsUpdate = true;
      geom.attributes.normal.needsUpdate = true;
      hullGeom.attributes.position.needsUpdate = true;
      hullGeom.computeVertexNormals();
      geom.computeBoundingSphere();
      hullGeom.computeBoundingSphere();
    }
    function setValues(v) {
      tris.forEach((T, i) => {
        for (let k = 0; k < 3; k++) val[i * 3 + k] = v[T.n[k]];
      });
      geom.attributes.aVal.needsUpdate = true;
    }
    // Point on the surface along dir from origin (outermost hit).
    const ray = new THREE.Ray();
    const hit = new THREE.Vector3();
    function surface(dir, origin) {
      ray.set(origin ? origin.clone() : new THREE.Vector3(), dir.clone().normalize());
      let best = null;
      tris.forEach((T, i) => {
        if (ray.intersectTriangle(pts[T.n[0]], pts[T.n[1]], pts[T.n[2]], false, hit)) {
          const d = hit.distanceTo(ray.origin);
          if (!best || d > best.d) best = { d, point: hit.clone(), normal: fn[i].clone() };
        }
      });
      return best;
    }
    setNodes(pts0, o.normals);
    return { geom, hullGeom, pts, nn, tris, setNodes, setValues, surface, nodeCount: nN, elemCount: elems.length, edges: elemEdges(elems) };
  }

  // Rounded box (a CAD block with constant-radius fillets on every edge).
  // The cube [-N/2, N/2]^3 grid has, per axis, nf divisions across each
  // fillet and nm across the flat part. place(H, r) puts every node on the
  // box of half sizes H with fillet radius r: inner = clamp(p, -(H - r),
  // H - r), point = inner + r n, n = normalize(p - inner) (exact normal).
  // Element edges carry weights for the CAD material: the tangent edges of
  // the fillets (where a flat face meets a fillet) = 1, iso-parameter lines
  // of the flat faces at the param indices = 0.5, every other edge = 0.
  function roundedBox(THREE, nf, nm, paramAt) {
    const N = 2 * nf + nm;
    const map = new Map();
    const grid = [];
    const node = (g) => {
      const key = g.join(",");
      let i = map.get(key);
      if (i === undefined) {
        i = grid.length;
        map.set(key, i);
        grid.push(g);
      }
      return i;
    };
    const tangent = (k) => k === nf || k === nf + nm;
    const flat = (k) => k > nf && k < nf + nm;
    const inFlat = (m) => m >= nf && m + 1 <= nf + nm;
    // edge at constant index k, running over m..m+1 of the other axis
    const weight = (k, m) => (tangent(k) ? 1 : flat(k) && inFlat(m) && paramAt.includes(k - nf) ? 0.5 : 0);
    const elems = [];
    for (let a = 0; a < 3; a++) {
      const b = (a + 1) % 3, c = (a + 2) % 3;
      for (const s of [0, N]) {
        const id = (i, j) => {
          const g = [0, 0, 0];
          g[a] = s; g[b] = i; g[c] = j;
          return node(g);
        };
        for (let i = 0; i < N; i++) for (let j = 0; j < N; j++) {
          elems.push({
            n: [id(i, j), id(i + 1, j), id(i + 1, j + 1), id(i, j + 1)],
            w: [weight(j, i), weight(i + 1, j), weight(j + 1, i), weight(i, j)],
          });
        }
      }
    }
    const coord = (i, h, r) => (i <= nf ? -h + (r * i) / nf : i >= nf + nm ? h - (r * (N - i)) / nf : -(h - r) + (2 * (h - r) * (i - nf)) / nm);
    function place(H, r) {
      const h = [H.x, H.y, H.z];
      const P = [], NN = [];
      for (const g of grid) {
        const p = g.map((i, k) => coord(i, h[k], r));
        const inner = p.map((v, k) => clamp(v, -(h[k] - r), h[k] - r));
        const n = new THREE.Vector3(p[0] - inner[0], p[1] - inner[1], p[2] - inner[2]).normalize();
        NN.push(n);
        P.push(new THREE.Vector3(inner[0], inner[1], inner[2]).addScaledVector(n, r));
      }
      return { P, N: NN };
    }
    return { elems, place, count: grid.length };
  }

  // Point on any closed mesh along dir from origin (outermost hit), in the
  // geometry's own coordinates shifted by offset.
  function geomSurface(THREE, geom, offset) {
    const pos = geom.attributes.position, idx = geom.index;
    const n = idx ? idx.count : pos.count;
    const off = offset || new THREE.Vector3();
    const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), hit = new THREE.Vector3();
    const ray = new THREE.Ray();
    const at = (k, v) => v.fromBufferAttribute(pos, idx ? idx.getX(k) : k).add(off);
    return (dir, origin) => {
      ray.set(origin ? origin.clone() : new THREE.Vector3(), dir.clone().normalize());
      let best = null;
      for (let k = 0; k < n; k += 3) {
        at(k, a); at(k + 1, b); at(k + 2, c);
        if (ray.intersectTriangle(a, b, c, false, hit)) {
          const d = hit.distanceTo(ray.origin);
          if (!best || d > best.d) best = { d, point: hit.clone(), normal: new THREE.Vector3().crossVectors(b.clone().sub(a), c.clone().sub(a)).normalize() };
        }
      }
      if (best && best.normal.dot(ray.direction) < 0) best.normal.negate();
      return best;
    };
  }

  // ---- materials --------------------------------------------------------

  function hullMaterial(THREE, width, color) {
    const m = new THREE.MeshBasicMaterial({ color, side: THREE.BackSide });
    m.userData.width = { value: width };
    m.onBeforeCompile = (sh) => {
      sh.uniforms.uHull = m.userData.width;
      sh.vertexShader = "uniform float uHull;\n" + sh.vertexShader.replace(
        "#include <begin_vertex>",
        "#include <begin_vertex>\n  transformed += normalize(normal) * uHull;"
      );
    };
    return m;
  }

  // Banded contour (BANDS steps of the nodal value interpolated inside each
  // triangle), element edges, optional iso-lines on the band borders.
  function contourMaterial(THREE, U, o) {
    o = Object.assign({ line: 1, always: false, iso: 0, self: 0.5, rough: 0.62, metal: 0, opacity: 1, lineColor: C.ink }, o || {});
    const m = new THREE.MeshStandardMaterial({ roughness: o.rough, metalness: o.metal, transparent: o.opacity < 1, opacity: o.opacity });
    const L = {
      uLineW: { value: o.line },
      uIso: { value: o.iso },
      uSelf: { value: o.self },
      uLineCol: { value: new THREE.Color(o.lineColor) },
    };
    m.userData.hk = { L, line: o.line, always: o.always };
    m.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, { uPal: U.uPal, uRimColor: U.uRimColor, uRim: U.uRim, uLinePx: U.uLinePx }, L);
      sh.vertexShader =
        "attribute float aVal;\nattribute vec3 aBary;\nvarying float vVal;\nvarying vec3 vBary;\n" +
        sh.vertexShader.replace("#include <begin_vertex>", "#include <begin_vertex>\n  vVal = aVal;\n  vBary = aBary;");
      sh.fragmentShader =
        [
          `uniform vec3 uPal[${BANDS}];`,
          "uniform vec3 uLineCol;",
          "uniform vec3 uRimColor;",
          "uniform float uRim;",
          "uniform float uSelf;",
          "uniform float uLinePx;",
          "uniform float uLineW;",
          "uniform float uIso;",
          "varying float vVal;",
          "varying vec3 vBary;",
        ].join("\n") + "\n" +
        sh.fragmentShader
          .replace(
            "#include <color_fragment>",
            `#include <color_fragment>
  float hkB = clamp(vVal, 0.0, 0.99999) * ${BANDS}.0;
  int hkI = int(floor(hkB));
  int hkJ = min(hkI + 1, ${BANDS - 1});
  float hkW = max(fwidth(hkB), 1e-5);
  float hkT = smoothstep(1.0 - hkW, 1.0, fract(hkB));
  diffuseColor.rgb = mix(uPal[hkI], uPal[hkJ], hkI < ${BANDS - 1} ? hkT : 0.0);
  vec3 hkBand = diffuseColor.rgb;
  vec3 hkD = vBary / max(fwidth(vBary), vec3(1e-5));
  float hkM = min(min(hkD.x, hkD.y), hkD.z);
  float hkLine = (1.0 - smoothstep(uLinePx * 0.5 - 0.6, uLinePx * 0.5 + 0.6, hkM)) * uLineW;
  float hkF = fract(hkB);
  float hkE = min(hkF, 1.0 - hkF) / hkW;
  float hkIso = (1.0 - smoothstep(0.5, 1.5, hkE)) * uIso * step(0.5, hkB) * step(hkB, ${BANDS - 0.5});
  hkLine = max(hkLine, hkIso * 0.8);
  diffuseColor.rgb = mix(diffuseColor.rgb, uLineCol, hkLine);`
          )
          .replace(
            "#include <opaque_fragment>",
            `float hkFr = pow(1.0 - clamp(dot(normal, normalize(vViewPosition)), 0.0, 1.0), 3.0);
  outgoingLight += uRimColor * uRim * hkFr * (1.0 - hkLine);
  // keep the band colour close to the legend: part lit, part flat colour
  outgoingLight = mix(outgoingLight, hkBand, uSelf * (1.0 - hkLine));
  #include <opaque_fragment>`
          );
    };
    m.customProgramCacheKey = () => "hk-contour";
    return m;
  }

  // A CAD view: one calm colour, smooth lighting, and lines drawn from the
  // element edge weights (aEdgeW): weight 1 = an edge line (fillet tangent
  // edges) in uEdgeCol, weight 0.5 = a faint iso-parameter line in uParamCol.
  // No contour bands.
  function cadMaterial(THREE, U, o) {
    o = Object.assign({ color: 0x8b93a5, rough: 0.42, metal: 0.08, edge: C.ink, param: 0x2a3a52, paramA: 0.4 }, o || {});
    const m = new THREE.MeshStandardMaterial({ color: o.color, roughness: o.rough, metalness: o.metal });
    const L = { uEdgeCol: { value: new THREE.Color(o.edge) }, uParamCol: { value: new THREE.Color(o.param) }, uParamA: { value: o.paramA } };
    m.userData.cad = L;
    m.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, { uLinePx: U.uLinePx, uRimColor: U.uRimColor, uRim: U.uRim }, L);
      sh.vertexShader =
        "attribute vec3 aBary;\nattribute vec3 aEdgeW;\nvarying vec3 vBary;\nvarying vec3 vEdgeW;\n" +
        sh.vertexShader.replace("#include <begin_vertex>", "#include <begin_vertex>\n  vBary = aBary;\n  vEdgeW = aEdgeW;");
      sh.fragmentShader =
        "uniform vec3 uEdgeCol;\nuniform vec3 uParamCol;\nuniform float uParamA;\nuniform float uLinePx;\nuniform vec3 uRimColor;\nuniform float uRim;\nvarying vec3 vBary;\nvarying vec3 vEdgeW;\n" +
        sh.fragmentShader
          .replace(
            "#include <color_fragment>",
            `#include <color_fragment>
  vec3 cdD = vBary / max(fwidth(vBary), vec3(1e-5));
  vec3 cdE = 1.0 - smoothstep(vec3(uLinePx * 0.55 - 0.6), vec3(uLinePx * 0.55 + 0.6), cdD);
  vec3 cdP = 1.0 - smoothstep(vec3(uLinePx * 0.3 - 0.5), vec3(uLinePx * 0.3 + 0.5), cdD);
  vec3 cdKe = step(vec3(0.75), vEdgeW);
  vec3 cdKp = step(vec3(0.25), vEdgeW) * (1.0 - cdKe);
  float cdEdge = max(max(cdE.x * cdKe.x, cdE.y * cdKe.y), cdE.z * cdKe.z);
  float cdPar = max(max(cdP.x * cdKp.x, cdP.y * cdKp.y), cdP.z * cdKp.z);
  diffuseColor.rgb = mix(diffuseColor.rgb, uParamCol, cdPar * uParamA);
  diffuseColor.rgb = mix(diffuseColor.rgb, uEdgeCol, cdEdge);`
          )
          .replace(
            "#include <opaque_fragment>",
            `float cdFr = pow(1.0 - clamp(dot(normal, normalize(vViewPosition)), 0.0, 1.0), 3.0);
  outgoingLight += uRimColor * uRim * 0.6 * cdFr * (1.0 - cdEdge);
  outgoingLight = mix(outgoingLight, uEdgeCol, cdEdge * 0.85);
  #include <opaque_fragment>`
          );
    };
    m.customProgramCacheKey = () => "hk-cad";
    return m;
  }

  // Moving dashes along tubes (streamlines, current, rising heat, a track).
  // The dash phase is s * uDens - t * uSpeed; colour = contour band of aVal.
  // aWake = 1 marks a region whose dashes run backwards when uSep = 1.
  function flowMaterial(THREE, U, o) {
    o = Object.assign({ speed: 1, dens: 2.2, duty: 0.55 }, o || {});
    const m = new THREE.MeshBasicMaterial({ color: 0xffffff });
    const L = { uSpeed: { value: o.speed }, uDens: { value: o.dens }, uDuty: { value: o.duty }, uSep: { value: 0 }, uGain: { value: 1 } };
    m.userData.flow = L;
    m.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, { uPal: U.uPal, uT: U.uT }, L);
      sh.vertexShader =
        "attribute float aS;\nattribute float aVal;\nattribute float aWake;\nvarying float vS;\nvarying float vVal;\nvarying float vWake;\n" +
        sh.vertexShader.replace("#include <begin_vertex>", "#include <begin_vertex>\n  vS = aS;\n  vVal = aVal;\n  vWake = aWake;");
      sh.fragmentShader =
        [
          `uniform vec3 uPal[${BANDS}];`,
          "uniform float uT;",
          "uniform float uSpeed;",
          "uniform float uDens;",
          "uniform float uDuty;",
          "uniform float uSep;",
          "uniform float uGain;",
          "varying float vS;",
          "varying float vVal;",
          "varying float vWake;",
        ].join("\n") + "\n" +
        sh.fragmentShader.replace(
          "#include <color_fragment>",
          `#include <color_fragment>
  float hkPh = vS * uDens - uT * uSpeed * (1.0 - 2.0 * uSep * vWake);
  float hkF = fract(hkPh);
  if (hkF > uDuty) discard;
  int hkI = int(floor(clamp(vVal * uGain, 0.0, 0.99999) * ${BANDS}.0));
  diffuseColor.rgb = mix(uPal[hkI], vec3(1.0), 0.55 * smoothstep(0.45 * uDuty, uDuty, hkF));`
        );
    };
    m.customProgramCacheKey = () => "hk-flow";
    return m;
  }

  // Merge tubes along paths [{ pts: [Vector3], vals: [number], wake(p) }]
  // into one geometry with aS (arc length), aVal and aWake per vertex.
  function tubes(THREE, paths, radius, radial) {
    const P = [], N = [], S = [], V = [], W = [], I = [];
    let base = 0;
    for (const path of paths) {
      const curve = new THREE.CatmullRomCurve3(path.pts, false, "centripetal");
      const segs = Math.max(12, path.pts.length * 4);
      const g = new THREE.TubeGeometry(curve, segs, radius, radial || 5, false);
      const len = curve.getLength();
      const cum = [0];
      for (let i = 1; i < path.pts.length; i++) cum.push(cum[i - 1] + path.pts[i].distanceTo(path.pts[i - 1]));
      const tot = cum[cum.length - 1];
      const pos = g.attributes.position, nor = g.attributes.normal, uv = g.attributes.uv;
      for (let i = 0; i < pos.count; i++) {
        P.push(pos.getX(i), pos.getY(i), pos.getZ(i));
        N.push(nor.getX(i), nor.getY(i), nor.getZ(i));
        const f = uv.getX(i);
        S.push(f * len + (path.s0 || 0));
        const target = f * tot;
        let k = 0;
        while (k < cum.length - 2 && cum[k + 1] < target) k++;
        const w = cum[k + 1] > cum[k] ? clamp((target - cum[k]) / (cum[k + 1] - cum[k]), 0, 1) : 0;
        V.push(path.vals[k] + (path.vals[k + 1] - path.vals[k]) * w);
        W.push(path.wake ? path.wake(pos.getX(i), pos.getY(i), pos.getZ(i)) : 0);
      }
      for (const ix of g.index.array) I.push(ix + base);
      base += pos.count;
      g.dispose();
    }
    const out = new THREE.BufferGeometry();
    out.setAttribute("position", new THREE.Float32BufferAttribute(P, 3));
    out.setAttribute("normal", new THREE.Float32BufferAttribute(N, 3));
    out.setAttribute("aS", new THREE.Float32BufferAttribute(S, 1));
    out.setAttribute("aVal", new THREE.Float32BufferAttribute(V, 1));
    out.setAttribute("aWake", new THREE.Float32BufferAttribute(W, 1));
    out.setIndex(I);
    return out;
  }

  // ---- small builders -----------------------------------------------------

  function addHull(THREE, mesh, width, hulls) {
    const h = new THREE.Mesh(smoothCopy(THREE, mesh.geometry), null);
    h.userData.hullWidth = width;
    h.position.copy(mesh.position);
    h.quaternion.copy(mesh.quaternion);
    h.scale.copy(mesh.scale);
    mesh.parent.add(h);
    hulls.push(h);
    return h;
  }

  function capsuleBetween(THREE, p0, p1, r, mat, capSegs, radial) {
    const len = p0.distanceTo(p1);
    const m = new THREE.Mesh(new THREE.CapsuleGeometry(r, len, capSegs ?? 4, radial ?? 10), mat);
    m.position.copy(p0).add(p1).multiplyScalar(0.5);
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), p1.clone().sub(p0).normalize());
    return m;
  }

  function rodBetween(THREE, p0, p1, r, mat, radial) {
    const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, p0.distanceTo(p1), radial ?? 8), mat);
    m.position.copy(p0).add(p1).multiplyScalar(0.5);
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), p1.clone().sub(p0).normalize());
    return m;
  }

  function std(THREE, color, rough, metal) {
    return new THREE.MeshStandardMaterial({ color, roughness: rough ?? 0.55, metalness: metal ?? 0 });
  }

  // fe mesh + material + outline hull into a parent
  function addFe(ctx, parent, fe, matOpts, hullW) {
    const { THREE } = ctx;
    const mat = contourMaterial(THREE, ctx.U, matOpts);
    ctx.contourMats.push(mat);
    const mesh = new THREE.Mesh(fe.geom, mat);
    mesh.castShadow = true;
    mesh.frustumCulled = false;
    parent.add(mesh);
    const hull = new THREE.Mesh(fe.hullGeom, null);
    hull.userData.hullWidth = hullW ?? 0.035;
    hull.frustumCulled = false;
    parent.add(hull);
    ctx.hulls.push(hull);
    return mesh;
  }

  // Node dots (shown on the meshed bodies when the pet is drawn large).
  function addDots(ctx, parent, fe, r) {
    const { THREE } = ctx;
    const dots = new THREE.InstancedMesh(new THREE.SphereGeometry(r, 8, 6), ctx.M.ink, fe.nodeCount);
    const m4 = new THREE.Matrix4();
    fe.pts.forEach((p, i) => {
      const n = fe.nn[i];
      m4.makeTranslation(p.x + n.x * 0.006, p.y + n.y * 0.006, p.z + n.z * 0.006);
      dots.setMatrixAt(i, m4);
    });
    parent.add(dots);
    ctx.dots.push(dots);
    return dots;
  }

  const stats = (a) => ({ max: Math.max(...a), min: Math.min(...a) });

  // ---- bodies ---------------------------------------------------------------
  // Each builder returns { group, anchor (eyes), antennaAnchor, top,
  // surface(dir), deforms, counts, caption, update(t, state), contour(t, state) }
  // in body coordinates (body centre at the origin).

  // Nodes of a jittered icosphere (three.js IcosahedronGeometry, one vertex on
  // +y so the antenna grows from a node), each placed on the body by place(d).
  // The jitter is seeded by the role id, as in v2.
  function icoNodes(ctx, place) {
    const { THREE, opts } = ctx;
    const detail = DETAIL[opts.mesh] ?? DETAIL.coarse;
    const ico = new THREE.IcosahedronGeometry(1, detail);
    ico.rotateX(-Math.atan2((1 + Math.sqrt(5)) / 2, 1));
    const w = weld(ico.attributes.position.array);
    ico.dispose();
    // edge length of the subdivided icosahedron on the unit sphere
    const edge = 1.0515 / (detail + 1);
    const rnd = mulberry32(ctx.seed);
    const pts = [];
    for (const n of w.nodes) {
      const d = new THREE.Vector3(n[0], n[1], n[2]).normalize();
      if (d.y > 0.9999) {
        pts.push(place(d));
        continue;
      }
      const r = new THREE.Vector3(rnd() * 2 - 1, rnd() * 2 - 1, rnd() * 2 - 1);
      r.addScaledVector(d, -r.dot(d)).multiplyScalar(0.16 * edge);
      const radial = 1 + (rnd() - 0.5) * 0.03;
      pts.push(place(d.clone().add(r).normalize()).multiplyScalar(radial));
    }
    return { pts, tris: w.tris };
  }

  // A triangle-element body whose stress comes from the limb loads (the v2
  // model, stressFields): settled = both legs share the weight, the prop arm
  // root carries the prop; mismatch = the weight on the left leg; working
  // steps the load up in increments.
  function stressBody(ctx, pts, tris) {
    const { THREE, opts } = ctx;
    const fe = feMesh(THREE, pts, tris.map((t) => ({ n: t })));
    const group = new THREE.Group();
    addFe(ctx, group, fe, { line: 1, self: 0.5 });
    addDots(ctx, group, fe, opts.mesh === "fine" ? 0.028 : 0.034);
    const field = stressFields(pts, ctx.roots, ctx.propMass);
    const vals = (t, s) => (s === "mismatch" ? field.mismatch : field.settled.map((v) => v * loadFactor(t, s)));
    const top = pts.reduce((b, p) => (p.y > b.y ? p : b), pts[0]);
    return {
      fe, group, surface: (d) => fe.surface(d), top: top.clone(),
      counts: { nodes: fe.nodeCount, elements: fe.elemCount },
      field: { nodes: pts.map((p) => [p.x, p.y, p.z]), settled: field.settled, mismatch: field.mismatch, loads: field.loads, mismatchLoads: field.mismatchLoads },
      update(t, s) { fe.setValues(vals(t, s)); },
      contour(t, s) {
        const load = loadFactor(t, s);
        const lines = [s === "mismatch" ? "荷重: 体重が左足に偏る (食い違い)" : `荷重 ${Math.round(load * 100)} %` + (s === "working" ? " (増分)" : "")];
        return Object.assign(stats(vals(t, s)), { load, loadCase: s === "mismatch" ? "mismatch" : "settled", info: lines });
      },
    };
  }

  // lead: the v2 body. A round, slightly egg shaped body of triangle
  // elements; stress from the weight on the legs and the prop on the arm.
  function bodyLead(ctx) {
    const { THREE } = ctx;
    const N = icoNodes(ctx, (d) => shapePoint(THREE, d));
    const B = stressBody(ctx, N.pts, N.tris);
    B.caption = `節点 ${B.counts.nodes} · 要素 ${B.counts.elements} (三角) · 手足の荷重から計算`;
    const base = B.contour;
    B.contour = (t, s) => {
      const c = base(t, s);
      c.info = c.info.concat(["足 2 本が体重を分け、右腕が小道具を持つ"]);
      return c;
    };
    return B;
  }

  // strength: stress concentration at a hole. The body is a thick plate
  // (rounded outline, a superellipse with n = 5, filleted front and back
  // edges) with a round hole through it below the face, meshed as an O-grid
  // of quad elements around the hole (rings closer near the hole). It is
  // pulled from both sides along x (tension sigma, arrows outside the body
  // pointing away from it).
  // Value: the Kirsch solution for a round hole of radius a in a wide plate
  // under tension (r, theta from the hole centre, theta from the load axis):
  //   s_rr = 1/2 (1 - q) + 1/2 (1 - 4q + 3q^2) cos 2th
  //   s_tt = 1/2 (1 + q) - 1/2 (1 + 3q^2) cos 2th
  //   t_rt = -1/2 (1 + 2q - 3q^2) sin 2th,     q = a^2 / r^2 (per sigma)
  // von Mises s_vm = sqrt(s_rr^2 - s_rr s_tt + s_tt^2 + 3 t_rt^2), and the
  // colour is the rise over the nominal stress, v = L (s_vm - 1) / (Kt - 1)
  // with Kt = 3 (the edge value at theta = 90 deg), clipped at 0: 1.0 at the
  // top and bottom edges of the hole, 0 far from it. L = the load factor.
  // Mismatch: a crack runs up from the top edge of the hole; its tip adds
  // 1.0 exp(-(d / 0.08)^2).
  function bodyStrength(ctx) {
    const { THREE } = ctx;
    const W = 0.95, HH = 0.92, D = 0.6, NSE = 5, F = 0.1;
    const HC = { x: 0, y: -0.48 }, A = 0.24, KT = 3;
    const nt = ctx.fine ? 48 : 32, nr = ctx.fine ? 10 : 6, nf = 2, nz = ctx.fine ? 6 : 4, nh = ctx.fine ? 6 : 4;
    const se = (x, y) => Math.pow(Math.abs(x / (W - F)), NSE) + Math.pow(Math.abs(y / (HH - F)), NSE);
    // where the ray from the hole centre at angle th meets the outline of the
    // flat face (the fillet starts there), and the outline normal
    const outlineAt = (th) => {
      const c = Math.cos(th), s = Math.sin(th);
      let lo = 0, hi = 3;
      for (let i = 0; i < 50; i++) {
        const m = (lo + hi) / 2;
        if (se(HC.x + c * m, HC.y + s * m) > 1) hi = m;
        else lo = m;
      }
      const x = HC.x + c * lo, y = HC.y + s * lo;
      const gx = Math.sign(x) * Math.pow(Math.abs(x / (W - F)), NSE - 1) / (W - F);
      const gy = Math.sign(y) * Math.pow(Math.abs(y / (HH - F)), NSE - 1) / (HH - F);
      const g = Math.hypot(gx, gy) || 1;
      return { x, y, nx: gx / g, ny: gy / g };
    };
    // one closed loop of nodes per angle: front face (hole edge -> outline),
    // front fillet, side wall, back fillet, back face (outline -> hole edge),
    // hole wall (back -> front)
    const loop = (th) => {
      const O = outlineAt(th);
      const ex = HC.x + A * Math.cos(th), ey = HC.y + A * Math.sin(th);
      const P = [];
      const face = (j, z) => {
        const g = Math.pow(j / nr, 1.9);
        return [ex + (O.x - ex) * g, ey + (O.y - ey) * g, z];
      };
      const fil = (k, sz) => {
        const f = ((k / nf) * Math.PI) / 2;
        return [O.x + F * Math.sin(f) * O.nx, O.y + F * Math.sin(f) * O.ny, sz * (D - F + F * Math.cos(f))];
      };
      for (let j = 0; j <= nr; j++) P.push(face(j, D));
      for (let k = 1; k <= nf; k++) P.push(fil(k, 1));
      for (let m = 1; m <= nz; m++) P.push([O.x + F * O.nx, O.y + F * O.ny, D - F - (2 * (D - F) * m) / nz]);
      for (let k = nf - 1; k >= 0; k--) P.push(fil(k, -1));
      for (let j = nr - 1; j >= 0; j--) P.push(face(j, -D));
      for (let m = 1; m < nh; m++) P.push([ex, ey, -D + (2 * D * m) / nh]);
      return P;
    };
    const cols = Array.from({ length: nt }, (_, i) => loop((TAU * i) / nt));
    const Lk = cols[0].length;
    const pts = cols.flat().map((p) => new THREE.Vector3(p[0], p[1], p[2]));
    const id = (i, k) => (((i % nt) + nt) % nt) * Lk + (((k % Lk) + Lk) % Lk);
    const elems = [];
    // wound outward: front face (radial, then around) gives +z
    for (let i = 0; i < nt; i++) for (let k = 0; k < Lk; k++) elems.push({ n: [id(i, k), id(i, k + 1), id(i + 1, k + 1), id(i + 1, k)] });
    const fe = feMesh(THREE, pts, elems, { keepWinding: true });
    const group = new THREE.Group();
    addFe(ctx, group, fe, { line: 0.6, self: 0.5 });

    const kirsch = (r, th) => {
      const q = (A * A) / (r * r), q2 = q * q, c2 = Math.cos(2 * th), s2 = Math.sin(2 * th);
      const srr = 0.5 * (1 - q) + 0.5 * (1 - 4 * q + 3 * q2) * c2;
      const stt = 0.5 * (1 + q) - 0.5 * (1 + 3 * q2) * c2;
      const trt = -0.5 * (1 + 2 * q - 3 * q2) * s2;
      return Math.sqrt(srr * srr - srr * stt + stt * stt + 3 * trt * trt);
    };
    const base = pts.map((p) => {
      const dx = p.x - HC.x, dy = p.y - HC.y;
      const r = Math.max(A, Math.hypot(dx, dy));
      return Math.max(0, (kirsch(r, Math.atan2(dy, dx)) - 1) / (KT - 1));
    });
    const TIP = new THREE.Vector2(HC.x, HC.y + A + 0.1);
    const crackAdd = pts.map((p) => 1.0 * Math.exp(-((Math.hypot(p.x - TIP.x, p.y - TIP.y) / 0.08) ** 2)));
    const vals = (t, s) => {
      const L = loadFactor(t, s);
      return s === "mismatch" ? base.map((v, i) => v + crackAdd[i]) : base.map((v) => v * L);
    };

    // tension arrows outside both sides, pointing away (two per side, above
    // the arms); the tail stays GAP off the side wall
    const red = new THREE.MeshBasicMaterial({ color: C.miss });
    const GAP = 0.05, ALEN = 0.45, CONE = 0.17;
    const arrows = [];
    const arrowObjs = [];
    for (const sx of [-1, 1]) {
      for (const y of [0.42, 0.1]) {
        const xs = (W - F) * Math.pow(Math.max(0, 1 - Math.pow(Math.abs(y / (HH - F)), NSE)), 1 / NSE) + F;
        const tail = new THREE.Vector3(sx * (xs + GAP), y, 0);
        const dir = new THREE.Vector3(sx, 0, 0);
        const g = new THREE.Group();
        g.position.copy(tail);
        g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
        const rod = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 1, 10), red);
        const cone = new THREE.Mesh(new THREE.ConeGeometry(0.075, CONE, 14), red);
        g.add(rod, cone);
        group.add(g);
        arrowObjs.push({ rod, cone });
        arrows.push({ tail: tail.clone(), dir, length: ALEN });
      }
    }
    const setArrows = (L) => {
      const len = 0.2 + (ALEN - 0.2) * L;
      for (const a of arrowObjs) {
        a.rod.scale.y = len - CONE * 0.9;
        a.rod.position.y = (len - CONE * 0.9) / 2;
        a.cone.position.y = len - CONE / 2;
      }
    };
    // the crack (mismatch), drawn on the front and the back face
    const crack = new THREE.Group();
    for (const z of [D + 0.004, -D - 0.004]) {
      const pp = [[0, 0], [0.02, 0.035], [-0.01, 0.07], [0.01, 0.1]].map(([dx, dy]) => new THREE.Vector3(HC.x + dx, HC.y + A + dy, z));
      crack.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pp), 16, 0.013, 5, false), ctx.M.ink));
    }
    group.add(crack);
    const nodesXYZ = pts.map((p) => [p.x, p.y, p.z]);
    return {
      group, surface: (d) => fe.surface(d), top: new THREE.Vector3(0, HH, 0),
      counts: { nodes: fe.nodeCount, elements: fe.elemCount },
      caption: `節点 ${fe.nodeCount} · 要素 ${fe.elemCount} (四角) · 矢印 = 両側から引っぱる荷重`,
      field: { nodes: nodesXYZ, settled: base, mismatch: base.map((v, i) => v + crackAdd[i]), hole: { center: [HC.x, HC.y], radius: A, depth: D }, kt: KT },
      arrows,
      update(t, s) {
        fe.setValues(vals(t, s));
        setArrows(s === "mismatch" ? 1 : loadFactor(t, s));
        crack.visible = s === "mismatch";
      },
      contour(t, s) {
        const L = loadFactor(t, s);
        const info = [
          s === "mismatch" ? "穴のふちから亀裂が伸びた (食い違い)" : `引っぱる荷重 ${Math.round(L * 100)} %` + (s === "working" ? " (増分)" : ""),
          "穴の上下のふちに集中 (公称の 3 倍 = 1.0)",
        ];
        return Object.assign(stats(vals(t, s)), { info });
      },
    };
  }

  // aero: a teardrop of translucent fluid; surface streamlines run from the
  // nose to the tail and streamlines of the flow around a sphere pass by.
  // Speed on the surface: U sin(theta) (theta = angle between the surface
  // normal and the flow) times a blockage factor 1 + 0.5 (r / r_max)^2, so it
  // is 1.5 U at the widest section as on a sphere and recovers toward U on
  // the tail.
  // v6 colours the pressure, not the speed: Cp = 1 - (V / U)^2 (Bernoulli),
  // 1 at the stagnation point on the nose, -1.25 at the shoulder (1.5 U).
  // Behind the widest section the pressure recovers only partly (a real
  // flow loses energy in the boundary layer), so Cp is capped at CP_REAR
  // there (no second red spot on the tail). Shown relative:
  // v = (Cp - CP_MIN) / (1 - CP_MIN), 1.0 at the nose, 0 at the shoulder,
  // 0.56 in the free stream (the capped tail, 0.59, sits near it). Mismatch: separated wake behind the shoulder,
  // Cp = -0.45 + 0.08 sin(...) (a low, flat base pressure).
  const CP_MIN = -1.25, CP_REAR = 0.08;
  const cpRel = (cp) => (cp - CP_MIN) / (1 - CP_MIN);
  function bodyAero(ctx) {
    const { THREE } = ctx;
    // built in flow coordinates (flow axis z, nose at +z); toBody turns the
    // axis to body +x so the teardrop points at the prop side and the tail
    // sticks out to the left, readable from the front
    const A = 0.98, LT = 1.5, RX = 0.86, RY = 0.93;
    const toBody = (p) => new THREE.Vector3(p.z, p.y, -p.x);
    const rad = (z) => (z >= 0 ? Math.sqrt(Math.max(0, 1 - (z / A) ** 2)) : Math.pow(Math.max(0, Math.cos((Math.PI / 2) * (-z / LT))), 1.6));
    const slope = (z) => (rad(z + 1e-3) - rad(z - 1e-3)) / 2e-3;
    const speed = (z) => (z >= A - 1e-4 ? 0 : (1 + 0.5 * rad(z) ** 2) / 1.5 / Math.sqrt(1 + slope(Math.min(z, A - 2e-3)) ** 2));
    const nv = ctx.fine ? 20 : 14, nu = ctx.fine ? 24 : 16;
    const prof = Array.from({ length: nv + 1 }, (_, j) => {
      const v = j / nv;
      return v <= 0.5 ? -LT * (1 - 2 * v) : A * Math.sin(Math.PI * (v - 0.5));
    });
    const mapF = (z, phi) => new THREE.Vector3(RX * rad(z) * Math.cos(phi), RY * rad(z) * Math.sin(phi), z);
    const map = (z, phi) => toBody(mapF(z, phi));
    const R = revolve(THREE, prof, nu, map);
    const fe = feMesh(THREE, R.pts, R.elems, { smooth: true });
    const group = new THREE.Group();
    addFe(ctx, group, fe, { line: 0.3, self: 0.42, rough: 0.12, opacity: 0.72 }, 0.03);
    // pale core inside the translucent fluid (hides the outline shell)
    const core = new THREE.Mesh(fe.geom, std(THREE, C.paper, 0.8));
    core.scale.setScalar(0.9);
    core.frustumCulled = false;
    group.add(core);

    // pressure from the normalised speed s = V / 1.5 U at flow coordinate z
    const press = (s, z) => {
      const cp = 1 - (1.5 * s) ** 2;
      return cpRel(z < 0 ? Math.min(cp, CP_REAR) : cp);
    };
    // flow axis coordinate of a body point = body x
    const baseVals = R.pts.map((p) => press(speed(p.x), p.x));
    const noseIdx = R.pts.reduce((b, p, i) => (p.x > R.pts[b].x ? i : b), 0);
    // separated wake behind the shoulder (mismatch): low, flat base pressure
    const sepVals = (t) => R.pts.map((p, i) => (p.x < -0.05 ? cpRel(-0.45 + 0.08 * Math.sin(3 * Math.atan2(p.y, -p.z) + 2.4 * t + 4 * p.x)) : baseVals[i]));

    // surface streamlines (meridians, half a step off the element edges)
    const paths = [];
    const nLines = 12;
    for (let k = 0; k < nLines; k++) {
      const phi = (TAU * (k + 0.5)) / nLines;
      const pts = [], vals = [];
      for (let j = nv - 1; j >= 1; j--) {
        const z = prof[j];
        const p = mapF(z, phi);
        const c = new THREE.Vector3(0, 0, z >= 0 ? 0 : z);
        pts.push(toBody(p.clone().add(p.clone().sub(c).normalize().multiplyScalar(0.025))));
        vals.push(press(speed(z), z));
      }
      paths.push({ pts, vals, wake: (x) => (x < -0.05 ? 1 : 0), s0: k * 0.37 });
    }
    // streamlines around: Stokes stream function of flow past a sphere of
    // radius a, psi = U rho^2 (1 - a^3 / R^3) / 2; each line keeps its far
    // radius rho_inf. Speed from the same potential flow.
    const a = 0.93;
    const rhoAt = (rinf, z) => {
      let lo = Math.sqrt(Math.max(0, a * a - z * z)) + 1e-4, hi = rinf + 2;
      for (let i = 0; i < 48; i++) {
        const m = (lo + hi) / 2;
        const f = m * m * (1 - a ** 3 / Math.pow(m * m + z * z, 1.5)) - rinf * rinf;
        if (f > 0) hi = m;
        else lo = m;
      }
      return (lo + hi) / 2;
    };
    const flowSpeed = (rho, z) => {
      const Rr = Math.hypot(rho, z), c = z / Rr, s = rho / Rr, k = (a / Rr) ** 3;
      return Math.hypot(c * (1 - k), s * (1 + k / 2)) / 1.5;
    };
    // [far radius, azimuth in degrees]; azimuth 90 = up, 180 = the face side
    const around = [[0.62, 60], [0.62, 125], [0.95, 90], [0.85, 215], [0.75, 330], [1.2, 20], [1.2, 152]];
    around.forEach(([rinf, deg], k) => {
      const phi = (deg * Math.PI) / 180;
      const pts = [], vals = [];
      for (let z = 1.55; z >= -1.85; z -= 0.1) {
        const rho = rhoAt(rinf, z);
        pts.push(toBody(new THREE.Vector3(rho * Math.cos(phi), rho * Math.sin(phi) * RY, z)));
        vals.push(press(flowSpeed(rho, z), z));
      }
      paths.push({ pts, vals, wake: (x) => (x < -0.4 ? 1 : 0), s0: k * 0.53 });
    });
    const flowMat = flowMaterial(THREE, ctx.U, { speed: 0.7, dens: 2.4, duty: 0.55 });
    ctx.flowMats.push(flowMat);
    const lines = new THREE.Mesh(tubes(THREE, paths, 0.026, 5), flowMat);
    lines.frustumCulled = false;
    group.add(lines);

    const top = R.pts.reduce((b, p) => (p.y > b.y ? p : b), R.pts[0]);
    const spd = { idle: 0.7, report: 0.7, pass: 0.9, mismatch: 0.6, working: 2.0, run: 1.4 };
    const vals = (t, s) => (s === "mismatch" ? sepVals(t) : baseVals);
    return {
      group, surface: (d) => fe.surface(d), top: top.clone(),
      counts: { nodes: fe.nodeCount, elements: fe.elemCount },
      caption: `流線 ${paths.length} 本 · 面の節点 ${fe.nodeCount} · 色 = 圧力`,
      field: { nodes: R.pts.map((p) => [p.x, p.y, p.z]), settled: baseVals, nose: noseIdx, cpMin: CP_MIN, cpRear: CP_REAR },
      update(t, s) {
        fe.setValues(vals(t, s));
        flowMat.userData.flow.uSpeed.value = spd[s] ?? 0.7;
        flowMat.userData.flow.uSep.value = s === "mismatch" ? 1 : 0;
      },
      contour(t, s) {
        const info = s === "mismatch" ? ["流れ: 肩の後ろではく離 (食い違い)", "後ろは低い圧力のまま (流れが逆に回る)"] : ["先端のよどみ点がいちばん高い (Cp = 1)", s === "working" ? "肩は流れが速く低い · 流れの線が速い (作業中)" : "肩は流れが速く低い (Cp = −1.25)"];
        return Object.assign(stats(vals(t, s)), { info });
      },
    };
  }

  // crash: the body is not crushed. A rounded-box shell of quad elements is
  // hit on the bottom front edge (well below the face); a stress wave runs out from the
  // impact point over the shell, a small dent stays there, shock rings leave
  // the point and a deceleration pulse is plotted beside the feet.
  // Stress at a node, d = distance to the impact point, tau = time since the
  // impact: residual 0.3 exp(-(d / 0.25)^2) + wave a exp(-((d - c tau) / w)^2)
  // exp(-d / 3) + a second, weaker wave 0.5 s later (0.4 a), w = 0.22,
  // c = 1.1 per s (1.6 when working). Values above 1.0 are red.
  function bodyCrash(ctx) {
    const { THREE } = ctx;
    const nu = ctx.fine ? 32 : 24, nv = ctx.fine ? 16 : 12;
    const prof = Array.from({ length: nv + 1 }, (_, j) => -Math.PI / 2 + (Math.PI * j) / nv);
    const R = revolve(THREE, prof, nu, (lat, phi) => {
      const q = superDir(latDir(THREE, lat, phi), 4);
      return new THREE.Vector3(q.x * 0.97, q.y * 0.92, q.z * 0.92);
    });
    const P0 = R.pts;
    const fe = feMesh(THREE, P0, R.elems);
    const N0 = fe.nn.map((v) => v.clone());
    const group = new THREE.Group();
    addFe(ctx, group, fe, { line: 0.8, self: 0.5 });
    // impact points: straight on the lower front, or the front right corner
    // (offset impact, mismatch)
    const IMPACT = {
      centre: fe.surface(new THREE.Vector3(0, -0.74, 0.68)),
      offset: fe.surface(new THREE.Vector3(0.66, -0.55, 0.52)),
    };
    const DENT = { depth: 0.055, radius: 0.3 };
    const W_WAVE = 0.22;
    const cWave = (s) => (s === "working" ? 1.6 : 1.1);
    const cycle = (s) => (s === "working" ? 1.6 : 2.6);
    const tauOf = (t, s) => (((t % cycle(s)) + cycle(s)) % cycle(s));
    const ampOf = (t, s) => (s === "working" ? 0.75 + 0.35 * ramp(t) : 0.85);
    const shapes = {};
    const shapeFor = (key) => {
      if (shapes[key]) return shapes[key];
      const I = IMPACT[key].point;
      const d = P0.map((p) => p.distanceTo(I));
      const P = P0.map((p, i) => {
        const f = Math.max(0, 1 - (d[i] / DENT.radius) ** 2);
        return p.clone().addScaledVector(N0[i], -DENT.depth * f * f);
      });
      shapes[key] = { I, n: IMPACT[key].normal.clone().normalize(), d, P };
      return shapes[key];
    };
    const keyOf = (s) => (s === "mismatch" ? "offset" : "centre");
    const vals = (t, s) => {
      const S = shapeFor(keyOf(s));
      const tau = tauOf(t, s), a = ampOf(t, s);
      const c = cWave(s);
      const wave = (d, tt) => (tt < 0 ? 0 : Math.exp(-(((d - c * tt) / W_WAVE) ** 2)) * Math.exp(-d / 3));
      return S.d.map((d) => 0.3 * Math.exp(-((d / 0.25) ** 2)) + a * wave(d, tau) + 0.4 * a * wave(d, tau - 0.5));
    };
    // shock rings: three ripples running out from the impact point
    const ringMats = [];
    const rings = [0, 1, 2].map(() => {
      const m = new THREE.MeshBasicMaterial({ color: C.ink, transparent: true, opacity: 0.8, depthWrite: false });
      ctx.inkMats.push({ mat: m, paper: C.ink, night: C.onNightSoft });
      ringMats.push(m);
      const r = new THREE.Mesh(new THREE.TorusGeometry(1, 0.04, 6, 48), m);
      group.add(r);
      return r;
    });
    // deceleration pulse beside the left foot (stays on the ground)
    const ground = new THREE.Group();
    const X0 = -1.42, X1 = -0.74, Y0 = 0.06, Y1 = 0.46, ZP = 0.62, TP = 0.9;
    const axisMat = new THREE.MeshBasicMaterial({ color: C.ink });
    ctx.inkMats.push({ mat: axisMat, paper: C.ink, night: C.onNightSoft });
    ground.add(rodBetween(THREE, new THREE.Vector3(X0, Y0, ZP), new THREE.Vector3(X1 + 0.04, Y0, ZP), 0.009, axisMat, 6));
    ground.add(rodBetween(THREE, new THREE.Vector3(X0, Y0, ZP), new THREE.Vector3(X0, Y1 + 0.03, ZP), 0.009, axisMat, 6));
    // pulse: g = sin(pi tau / 0.7)^2 (1 + 0.15 sin(14 tau)) for tau < 0.7, then 0
    const pulse = (tau) => (tau < 0.7 ? Math.sin((Math.PI * tau) / 0.7) ** 2 * (1 + 0.15 * Math.sin(14 * tau)) : 0);
    const gMax = Math.max(...Array.from({ length: 141 }, (_, i) => pulse(i * 0.005)));
    const plotPt = (tau) => new THREE.Vector3(X0 + 0.015 + (Math.min(tau, TP) / TP) * (X1 - X0 - 0.03), Y0 + 0.02 + (pulse(Math.min(tau, TP)) / gMax) * (Y1 - Y0 - 0.04), ZP);
    const curve = Array.from({ length: 46 }, (_, i) => plotPt((i / 45) * TP));
    const curveMat = new THREE.MeshBasicMaterial({ color: C.miss });
    ground.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(curve), 90, 0.014, 6, false), curveMat));
    const nowDot = new THREE.Mesh(new THREE.SphereGeometry(0.035, 14, 10), axisMat);
    ground.add(nowDot);
    let lastKey = null;
    return {
      group, ground, top: P0[P0.length - 1].clone(),
      surface: (d) => fe.surface(d),
      counts: { nodes: fe.nodeCount, elements: fe.elemCount },
      caption: `殻の要素 ${fe.elemCount} · 輪 = 衝撃 · 左下 = 減速度`,
      update(t, s) {
        const key = keyOf(s);
        const S = shapeFor(key);
        if (key !== lastKey) {
          fe.setNodes(S.P);
          lastKey = key;
        }
        fe.setValues(vals(t, s));
        const tau = tauOf(t, s);
        const rate = s === "working" ? 1.4 : 0.8;
        rings.forEach((r, k) => {
          const f = (((t * rate + k / 3) % 1) + 1) % 1;
          const rad = 0.06 + 0.38 * f;
          r.position.copy(S.I).addScaledVector(S.n, 0.05 + 0.12 * f);
          r.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), S.n);
          r.scale.set(rad, rad, rad);
          ringMats[k].opacity = 0.9 * (1 - f);
        });
        nowDot.position.copy(plotPt(tau));
      },
      contour(t, s) {
        const tau = tauOf(t, s);
        const info = [s === "mismatch" ? "右の角に当たった (オフセット、食い違い)" : "前の下に当たった (正面)", `当たってから ${tau.toFixed(2)} 秒 · 応力の波が広がる` + (s === "working" ? " (くり返し)" : "")];
        return Object.assign(stats(vals(t, s)), { info });
      },
    };
  }

  // nvh: an eigenmode. The mesh moves with an exaggerated mode shape
  // w = A Y(n) cos(omega t) along the normal; the undeformed mesh stays as
  // faint lines. Displacement = |w| / A_max.
  // v6 (no flicker): slower vibration (0.4 cycles per second at rest, 0.55
  // when working); the working amplitude swells and fades smoothly
  // (A = 0.06 + 0.09 (1 - cos(2 pi t / 8.8)) / 2, no jump at the cycle end);
  // the body is opaque and the undeformed lines are drawn on top without a
  // depth test, faded on the far side by their own normal, so they no longer
  // blink in and out as the surface passes through them. Lines in front of
  // the eyes are left out.
  function bodyNvh(ctx) {
    const { THREE } = ctx;
    const ico = new THREE.IcosahedronGeometry(1, ctx.fine ? 3 : 2);
    ico.rotateX(-Math.atan2((1 + Math.sqrt(5)) / 2, 1));
    const w = weld(ico.attributes.position.array);
    ico.dispose();
    const dirs = w.nodes.map((n) => new THREE.Vector3(n[0], n[1], n[2]).normalize());
    const P0 = dirs.map((d) => shapePoint(THREE, d));
    const MODES = {
      // (2,2)-like: wider <-> taller, seen from the front
      a: (d) => d.x * d.x - d.y * d.y,
      // (3,3)-like: three lobes around the view axis
      b: (d) => d.x * (d.x * d.x - 3 * d.y * d.y),
    };
    const shape = {};
    for (const k of Object.keys(MODES)) {
      const raw = dirs.map(MODES[k]);
      const m = Math.max(...raw.map(Math.abs));
      shape[k] = raw.map((v) => v / m);
    }
    const AMAX = 0.17;
    const motion = (t, s) => {
      if (s === "working") return { mode: "a", amp: 0.06 + 0.09 * (1 - Math.cos((TAU * t) / 8.8)) / 2, f: 0.55 };
      if (s === "mismatch") return { mode: "b", amp: 0.1, f: 0.45 };
      return { mode: "a", amp: 0.09, f: 0.4 };
    };
    const solve = (t, s) => {
      const mo = motion(t, s);
      const c = Math.cos(TAU * mo.f * t);
      const Y = shape[mo.mode];
      return {
        P: P0.map((p, i) => p.clone().addScaledVector(dirs[i], mo.amp * Y[i] * c)),
        // colour: displacement amplitude of the mode at each node
        v: Y.map((y) => (Math.abs(y) * mo.amp) / AMAX),
        mo,
      };
    };
    const fe = feMesh(THREE, P0, w.tris.map((t) => ({ n: t })));
    const group = new THREE.Group();
    addFe(ctx, group, fe, { line: 0.45, self: 0.5 });
    // undeformed shape: faint lines, always on top, faded on the far side
    const eyeDirs = [-1, 1].map((sx) => new THREE.Vector3(sx * 0.28, 0.17, 0.95).normalize());
    const lp = [], ln = [];
    for (const [i, j] of fe.edges) {
      const mid = dirs[i].clone().add(dirs[j]).normalize();
      if (eyeDirs.some((e) => mid.angleTo(e) < 0.27)) continue;
      lp.push(P0[i].x, P0[i].y, P0[i].z, P0[j].x, P0[j].y, P0[j].z);
      ln.push(dirs[i].x, dirs[i].y, dirs[i].z, dirs[j].x, dirs[j].y, dirs[j].z);
    }
    const lg = new THREE.BufferGeometry();
    lg.setAttribute("position", new THREE.Float32BufferAttribute(lp, 3));
    lg.setAttribute("aNrm", new THREE.Float32BufferAttribute(ln, 3));
    const ghostMat = new THREE.LineBasicMaterial({ color: C.ink, transparent: true, opacity: 0.4, depthWrite: false, depthTest: false });
    ghostMat.onBeforeCompile = (sh) => {
      sh.vertexShader = "attribute vec3 aNrm;\nvarying float vFace;\n" + sh.vertexShader.replace(
        "#include <project_vertex>",
        "#include <project_vertex>\n  vFace = dot(normalize(normalMatrix * aNrm), normalize(-mvPosition.xyz));"
      );
      sh.fragmentShader = "varying float vFace;\n" + sh.fragmentShader.replace(
        "#include <color_fragment>",
        "#include <color_fragment>\n  diffuseColor.a *= smoothstep(0.08, 0.4, vFace);"
      );
    };
    ghostMat.customProgramCacheKey = () => "hk-ghost";
    ctx.inkMats.push({ mat: ghostMat, paper: C.ink, night: C.onNightSoft });
    const ghost = new THREE.LineSegments(lg, ghostMat);
    ghost.renderOrder = 2;
    ghost.frustumCulled = false;
    group.add(ghost);
    const topIdx = P0.reduce((b, p, i) => (p.y > P0[b].y ? i : b), 0);
    const body = {
      group, deforms: true, top: P0[topIdx].clone(),
      surface: (d) => fe.surface(d),
      counts: { nodes: fe.nodeCount, elements: fe.elemCount },
      caption: `節点 ${fe.nodeCount} · 要素 ${fe.elemCount} · 薄い線 = 変形前`,
      update(t, s) {
        const r = solve(t, s);
        fe.setNodes(r.P);
        fe.setValues(r.v);
        body.top.copy(r.P[topIdx]);
      },
      contour(t, s) {
        const r = solve(t, s);
        const info = [s === "mismatch" ? "別のモード (3 つの山) が出た (食い違い)" : "2 次のモード (横長 ⇄ 縦長)", `振幅 ${(r.mo.amp / AMAX).toFixed(2)} · ${r.mo.f.toFixed(2)} 回/秒 (誇張)`];
        return Object.assign(stats(r.v), { info });
      },
    };
    return body;
  }

  // thermal: heated from below. Steady: a fin with convection,
  // T = cosh(mL (1 - s)) / cosh(mL), s = height fraction from the bottom.
  // Working: transient heating, T = erfc(s / (2 sqrt(kt))). Band borders are
  // drawn as isotherms; heat lines rise around the body.
  function bodyThermal(ctx) {
    const { THREE } = ctx;
    const nu = ctx.fine ? 24 : 16, nv = ctx.fine ? 16 : 12;
    const prof = Array.from({ length: nv + 1 }, (_, j) => -Math.PI / 2 + (Math.PI * j) / nv);
    const R = revolve(THREE, prof, nu, (lat, phi) => {
      const p = shapePoint(THREE, latDir(THREE, lat, phi), 0.16);
      p.y = Math.max(p.y, -0.86) * 1.0;
      return p;
    });
    const fe = feMesh(THREE, R.pts, R.elems, { smooth: true });
    const group = new THREE.Group();
    addFe(ctx, group, fe, { line: 0.22, iso: 1, self: 0.5, rough: 0.5 });
    const ys = R.pts.map((p) => p.y);
    const y0 = Math.min(...ys), y1 = Math.max(...ys);
    const sOf = (y) => (y - y0) / (y1 - y0);
    const ML = 1.3;
    const steady = (s) => Math.cosh(ML * (1 - s)) / Math.cosh(ML);
    const HOT = new THREE.Vector3(0.82, 0.05, 0.42);
    const temp = (p, t, st) => {
      const s = sOf(p.y);
      if (st === "working") return erfc(s / (2 * Math.sqrt(0.012 + 0.5 * ramp(t))));
      let T = steady(s);
      if (st === "mismatch") T += 0.75 * Math.exp(-p.distanceToSquared(HOT) / 0.12);
      return T;
    };
    const vals = (t, s) => R.pts.map((p) => temp(p, t, s));
    // rising heat lines around the body (air cooling as it rises)
    const starts = [[-1.12, -0.35, 0.15, 1.6], [1.12, 0.42, -0.15, 1.15], [-0.62, 0.72, -0.5, 1.35], [0.58, 0.8, -0.55, 1.3], [0.0, 1.0, -0.72, 1.05]];
    const paths = starts.map(([x0, ys0, z0, len], k) => {
      const pts = [], v = [];
      const n = 16;
      for (let i = 0; i <= n; i++) {
        const f = i / n;
        pts.push(new THREE.Vector3(x0 + 0.07 * Math.sin(f * 9 + k * 1.7), ys0 + f * len, z0 + 0.04 * Math.cos(f * 7 + k)));
        v.push(0.78 - 0.5 * f);
      }
      return { pts, vals: v, s0: k * 0.41 };
    });
    const flowMat = flowMaterial(THREE, ctx.U, { speed: 0.55, dens: 2.0, duty: 0.6 });
    ctx.flowMats.push(flowMat);
    const lines = new THREE.Mesh(tubes(THREE, paths, 0.02, 5), flowMat);
    lines.frustumCulled = false;
    group.add(lines);
    return {
      group, surface: (d) => fe.surface(d), top: R.pts[R.pts.length - 1].clone(),
      counts: { nodes: fe.nodeCount, elements: fe.elemCount },
      caption: `節点 ${fe.nodeCount} · 要素 ${fe.elemCount} · 線 = 等温線`,
      update(t, s) {
        fe.setValues(vals(t, s));
        const f = flowMat.userData.flow;
        f.uSpeed.value = s === "working" ? 1.1 : 0.55;
        f.uGain.value = s === "working" ? 0.35 + 0.65 * ramp(t) : 1;
      },
      contour(t, s) {
        const info = s === "working" ? [`下から温まっている ${Math.round(ramp(t) * 100)} %`, "非定常の熱伝導 (erfc)"] : s === "mismatch" ? ["右の脇に熱だまり (食い違い)", "下から加熱・まわりへ放熱"] : ["下から加熱・まわりへ放熱 (定常)", "線 = 等温線"];
        return Object.assign(stats(vals(t, s)), { info });
      },
    };
  }

  // powertrain: an engine combustion chamber with a battery cell pack beside
  // it. The upper body is the gas in the cylinder (domed head, straight
  // liner); the lower body is the piston with ring grooves. A spark plug on
  // the head lights a flame that spreads through the gas, like the
  // temperature of a combustion CFD run. Progress at a node,
  // c = (1 - tanh((d - rf (1 + wrinkle)) / 0.08)) / 2 (d = distance to the
  // plug, rf = flame radius growing over the burn), T = Tu + (Tb - Tu) c with
  // Tu = 0.1 and Tb = 0.82 + 0.16 (1 - d / dmax) (gas burnt early is hotter).
  // Mismatch adds an auto-ignition hot spot in the end gas (knock).
  function bodyPowertrain(ctx) {
    const { THREE, role } = ctx;
    const RC = 0.86, DZ = 0.93, F = 0.18;
    const YT = 0.9, YD = 0.76, YP = -0.3, YB = -0.86;
    // profile from the head centre (top pole) down to the piston crown centre
    const prof = [];
    const capN = 4, filN = 3, sideN = ctx.fine ? 8 : 6;
    // domed head: y = YD + (YT - YD) (1 - (r / (RC - F))^2)
    for (let k = 0; k <= capN; k++) {
      const r = ((RC - F) * k) / capN;
      prof.push({ r, y: YD + (YT - YD) * (1 - (r / (RC - F)) ** 2) });
    }
    for (let k = 1; k <= filN; k++) { const a = (Math.PI / 2) * (k / filN); prof.push({ r: RC - F + F * Math.sin(a), y: YD - F + F * Math.cos(a) }); }
    for (let k = 1; k < sideN; k++) prof.push({ r: RC, y: YD - F - ((YD - F - YP) * k) / sideN });
    prof.push({ r: RC, y: YP });
    for (let k = 1; k <= 3; k++) prof.push({ r: RC * (1 - k / 3), y: YP });
    prof.reverse(); // bottom pole first, as revolve expects
    const nu = ctx.fine ? 28 : 20;
    const map = (p, phi) => new THREE.Vector3(p.r * Math.sin(phi), p.y, p.r * Math.cos(phi) * DZ);
    const R = revolve(THREE, prof, nu, map);
    const fe = feMesh(THREE, R.pts, R.elems, { smooth: true });
    const group = new THREE.Group();
    addFe(ctx, group, fe, { line: 0.3, iso: 1, self: 0.5, rough: 0.45 });
    // piston below the gas: skirt, ring grooves, pin boss
    const metal = ctx.M.metal;
    const piston = new THREE.Mesh(new THREE.CylinderGeometry(RC * 0.975, RC * 0.975, YP - YB - 0.015, nu * 2), metal);
    piston.scale.z = DZ;
    piston.position.y = (YP + YB) / 2 - 0.0075;
    piston.castShadow = true;
    piston.userData.body = true;
    group.add(piston);
    addHull(THREE, piston, 0.035, ctx.hulls);
    const grooveMat = ctx.M.ink;
    for (const y of [YP - 0.08, YP - 0.15, YP - 0.22]) {
      const g = new THREE.Mesh(new THREE.TorusGeometry(RC * 0.978, 0.013, 6, nu * 2), grooveMat);
      g.rotation.x = Math.PI / 2;
      g.scale.y = DZ;
      g.position.y = y;
      group.add(g);
    }
    const boss = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.03, 20), ctx.M.inkSoft);
    boss.rotation.x = Math.PI / 2;
    boss.position.set(0, -0.6, RC * 0.975 * DZ + 0.005);
    group.add(boss);
    // spark plug on the head, off the centre (the antenna is on the centre)
    const PX = -0.34, PZ = 0.16;
    const headY = (x, z) => {
      const r = Math.hypot(x, z / DZ);
      return YD + (YT - YD) * (1 - (r / (RC - F)) ** 2);
    };
    const plugY = headY(PX, PZ);
    const plug = new THREE.Group();
    plug.position.set(PX, plugY, PZ);
    group.add(plug);
    const hex = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.07, 6), metal);
    hex.position.y = 0.035;
    plug.add(hex);
    addHull(THREE, hex, 0.014, ctx.hulls);
    const ins = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.05, 0.16, 16), ctx.M.paper);
    ins.position.y = 0.15;
    plug.add(ins);
    addHull(THREE, ins, 0.014, ctx.hulls);
    const capT = new THREE.Mesh(new THREE.CylinderGeometry(0.026, 0.026, 0.05, 12), ctx.M.ink);
    capT.position.y = 0.255;
    plug.add(capT);
    // battery cells strapped on the left of the piston (hybrid)
    const cellMat = std(THREE, C.paper, 0.6);
    const band = std(THREE, role.color, 0.45);
    const cells = new THREE.Group();
    group.add(cells);
    [0.2, -0.06].forEach((z) => {
      const x = -RC - 0.1;
      const c = new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.42, 20), cellMat);
      c.position.set(x, (YP + YB) / 2 - 0.03, z);
      c.castShadow = true;
      cells.add(c);
      addHull(THREE, c, 0.016, ctx.hulls);
      const b = new THREE.Mesh(new THREE.CylinderGeometry(0.102, 0.102, 0.1, 20), band);
      b.position.copy(c.position);
      cells.add(b);
      const tip = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 0.04, 12), metal);
      tip.position.set(x, c.position.y + 0.23, z);
      cells.add(tip);
    });
    const bus = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.02, 0.34), ctx.M.ink);
    bus.position.set(-RC - 0.1, (YP + YB) / 2 - 0.03 + 0.255, 0.07);
    cells.add(bus);

    // flame
    const P0 = new THREE.Vector3(PX, plugY - 0.02, PZ);
    const dist = R.pts.map((p) => p.distanceTo(P0));
    const dmax = Math.max(...dist);
    const wr = R.pts.map((p) => {
      const v = p.clone().sub(P0);
      const phi = Math.atan2(v.z, v.x), th = Math.atan2(v.y, Math.hypot(v.x, v.z));
      return 0.1 * Math.sin(5 * phi + 1.3) * Math.sin(3 * th + 0.7);
    });
    const KNOCK = new THREE.Vector3(0.66, -0.12, 0.42);
    const cycle = (s) => (s === "working" ? { P: 1.6, burn: 1.1 } : { P: 3.0, burn: 2.0 });
    const progress = (t, s) => {
      const c = cycle(s);
      const tau = ((t % c.P) + c.P) % c.P;
      return Math.min(1, tau / c.burn);
    };
    const vals = (t, s) => {
      const rf = 1.12 * dmax * progress(t, s);
      return R.pts.map((p, i) => {
        const c = 0.5 * (1 - Math.tanh((dist[i] - rf * (1 + wr[i])) / 0.08));
        const Tb = 0.82 + 0.16 * (1 - dist[i] / dmax);
        let T = 0.1 + (Tb - 0.1) * c;
        if (s === "mismatch") T += 1.0 * Math.exp(-p.distanceToSquared(KNOCK) / 0.06);
        return T;
      });
    };
    const sparkMat = new THREE.MeshBasicMaterial({ color: C.s[3] });
    const spark = new THREE.Mesh(new THREE.IcosahedronGeometry(0.05, 1), sparkMat);
    spark.position.copy(P0).add(new THREE.Vector3(0, 0.005, 0));
    group.add(spark);
    return {
      group, surface: (d) => fe.surface(d), top: new THREE.Vector3(0, YT, 0),
      counts: { nodes: fe.nodeCount, elements: fe.elemCount },
      caption: `節点 ${fe.nodeCount} · 線 = 等温線 · ピストンと電池のセル`,
      update(t, s) {
        fe.setValues(vals(t, s));
        const p = progress(t, s);
        spark.visible = p < 0.25;
      },
      contour(t, s) {
        const p = progress(t, s);
        const info = [`火炎が燃焼室の ${Math.round(Math.min(1, p * 1.12) * 100)} % まで広がった` + (s === "working" ? " (速い回転)" : ""), s === "mismatch" ? "奥で自己着火 (ノッキング、食い違い)" : "点火プラグから火炎面が広がる"];
        return Object.assign(stats(vals(t, s)), { info });
      },
    };
  }

  // Mix two 0xRRGGBB colours, f = share of b.
  function mixHex(a, b, f) {
    const ch = (x, s) => (x >> s) & 255;
    const m = (s) => Math.round(ch(a, s) + (ch(b, s) - ch(a, s)) * f);
    return (m(16) << 16) | (m(8) << 8) | m(0);
  }
  const hexNum = (h) => parseInt(h.slice(1), 16);

  // designer: a CAD model, not an analysis result. A block with a fillet of
  // radius r on every edge (roundedBox), in one calm colour (grey blue:
  // --ink-faint with 30 % of --s1), lit smoothly; the tangent edges of the
  // fillets are drawn as edge lines and the flat faces carry faint
  // iso-parameter lines (thirds), as a CAD viewer shows a part. No contour,
  // no mesh lines, no legend. The design parameter is the fillet radius
  // r = 0.30. Working: r is being changed (0.30 +- 0.12, 4.4 s); mismatch:
  // r is off the design value (0.10) and the edge lines turn red.
  function bodyDesigner(ctx) {
    const { THREE } = ctx;
    const HALF = new THREE.Vector3(0.96, 0.92, 0.84);
    const R0 = 0.3;
    const RB = roundedBox(THREE, 3, 6, [2, 4]);
    const first = RB.place(HALF, R0);
    const fe = feMesh(THREE, first.P, RB.elems, { smooth: true, normals: first.N });
    const group = new THREE.Group();
    const mat = cadMaterial(THREE, ctx.U, { color: mixHex(C.inkFaint, C.s[0], 0.3), rough: 0.38, metal: 0.1 });
    const mesh = new THREE.Mesh(fe.geom, mat);
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    mesh.frustumCulled = false;
    mesh.userData.body = true;
    group.add(mesh);
    const hull = new THREE.Mesh(fe.hullGeom, null);
    hull.userData.hullWidth = 0.035;
    hull.frustumCulled = false;
    group.add(hull);
    ctx.hulls.push(hull);
    const edgeCol = mat.userData.cad.uEdgeCol.value;
    const radius = (t, s) => (s === "working" ? R0 + 0.12 * Math.sin((TAU * t) / 4.4) : s === "mismatch" ? 0.1 : R0);
    let last = R0;
    return {
      group, deforms: true, top: new THREE.Vector3(0, HALF.y, 0),
      surface: (d) => fe.surface(d),
      counts: { faces: 26, nodes: fe.nodeCount },
      caption: "CAD のモデル · 面 26 (平面 6・フィレット 12・角 8) · 線 = 面の境とパラメータの線",
      update(t, s) {
        const r = radius(t, s);
        if (r !== last) {
          const p = RB.place(HALF, r);
          fe.setNodes(p.P, p.N);
          last = r;
        }
        edgeCol.setHex(s === "mismatch" ? C.miss : C.ink);
      },
      contour(t, s) {
        const r = radius(t, s);
        const info = [`フィレットの半径 r = ${r.toFixed(2)}` + (s === "working" ? " (動かしている)" : ""), s === "mismatch" ? "r が設計の値 0.30 からずれた (食い違い)" : "色は面の色 (コンターではない)"];
        return { info };
      },
    };
  }

  // Surface nets: a mesh of the level f = 0 sampled on the grid lo + h (i, j, k),
  // n = samples per axis. One vertex per cell that changes sign (the mean of
  // the crossings on its edges), one quad per grid edge that changes sign.
  // Normals are the gradient of f; triangles are turned to agree with them.
  function surfaceNets(THREE, f, lo, n, h) {
    const [nx, ny, nz] = n;
    const val = new Float32Array(nx * ny * nz);
    const id = (i, j, k) => i + nx * (j + ny * k);
    for (let k = 0; k < nz; k++) for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      val[id(i, j, k)] = f(lo[0] + i * h, lo[1] + j * h, lo[2] + k * h);
    }
    const cx = nx - 1, cy = ny - 1;
    const cell = new Int32Array(cx * cy * (nz - 1)).fill(-1);
    const cid = (i, j, k) => i + cx * (j + cy * k);
    const corner = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0], [0, 0, 1], [1, 0, 1], [0, 1, 1], [1, 1, 1]];
    const edges = [];
    for (let a = 0; a < 8; a++) for (let b = a + 1; b < 8; b++) {
      if (Math.abs(corner[a][0] - corner[b][0]) + Math.abs(corner[a][1] - corner[b][1]) + Math.abs(corner[a][2] - corner[b][2]) === 1) edges.push([a, b]);
    }
    const pos = [];
    const cv = new Float32Array(8);
    for (let k = 0; k < nz - 1; k++) for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
      let neg = 0;
      for (let c = 0; c < 8; c++) {
        cv[c] = val[id(i + corner[c][0], j + corner[c][1], k + corner[c][2])];
        if (cv[c] < 0) neg++;
      }
      if (neg === 0 || neg === 8) continue;
      let sx = 0, sy = 0, sz = 0, m = 0;
      for (const [a, b] of edges) {
        if ((cv[a] < 0) === (cv[b] < 0)) continue;
        const s = cv[a] / (cv[a] - cv[b]);
        sx += corner[a][0] + (corner[b][0] - corner[a][0]) * s;
        sy += corner[a][1] + (corner[b][1] - corner[a][1]) * s;
        sz += corner[a][2] + (corner[b][2] - corner[a][2]) * s;
        m++;
      }
      cell[cid(i, j, k)] = pos.length / 3;
      pos.push(lo[0] + (i + sx / m) * h, lo[1] + (j + sy / m) * h, lo[2] + (k + sz / m) * h);
    }
    const idx = [];
    const quad = (a, b, c, d) => {
      if (a < 0 || b < 0 || c < 0 || d < 0) return;
      idx.push(a, b, c, a, c, d);
    };
    for (let k = 0; k < nz; k++) for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const v0 = val[id(i, j, k)] < 0;
      if (i < nx - 1 && j > 0 && k > 0 && v0 !== val[id(i + 1, j, k)] < 0) {
        quad(cell[cid(i, j - 1, k - 1)], cell[cid(i, j, k - 1)], cell[cid(i, j, k)], cell[cid(i, j - 1, k)]);
      }
      if (j < ny - 1 && i > 0 && k > 0 && v0 !== val[id(i, j + 1, k)] < 0) {
        quad(cell[cid(i - 1, j, k - 1)], cell[cid(i - 1, j, k)], cell[cid(i, j, k)], cell[cid(i, j, k - 1)]);
      }
      if (k < nz - 1 && i > 0 && j > 0 && v0 !== val[id(i, j, k + 1)] < 0) {
        quad(cell[cid(i - 1, j - 1, k)], cell[cid(i, j - 1, k)], cell[cid(i, j, k)], cell[cid(i - 1, j, k)]);
      }
    }
    // normals from the gradient of f
    const nrm = new Float32Array(pos.length);
    const e = h * 0.5;
    for (let v = 0; v < pos.length; v += 3) {
      const x = pos[v], y = pos[v + 1], z = pos[v + 2];
      const gx = f(x + e, y, z) - f(x - e, y, z), gy = f(x, y + e, z) - f(x, y - e, z), gz = f(x, y, z + e) - f(x, y, z - e);
      const L = Math.hypot(gx, gy, gz) || 1;
      nrm[v] = gx / L; nrm[v + 1] = gy / L; nrm[v + 2] = gz / L;
    }
    for (let t = 0; t < idx.length; t += 3) {
      const a = 3 * idx[t], b = 3 * idx[t + 1], c = 3 * idx[t + 2];
      const ux = pos[b] - pos[a], uy = pos[b + 1] - pos[a + 1], uz = pos[b + 2] - pos[a + 2];
      const wx = pos[c] - pos[a], wy = pos[c + 1] - pos[a + 1], wz = pos[c + 2] - pos[a + 2];
      const fx = uy * wz - uz * wy, fy = uz * wx - ux * wz, fz = ux * wy - uy * wx;
      const g = fx * (nrm[a] + nrm[b] + nrm[c]) + fy * (nrm[a + 1] + nrm[b + 1] + nrm[c + 1]) + fz * (nrm[a + 2] + nrm[b + 2] + nrm[c + 2]);
      if (g < 0) { const s = idx[t + 1]; idx[t + 1] = idx[t + 2]; idx[t + 2] = s; }
    }
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    geom.setAttribute("normal", new THREE.Float32BufferAttribute(nrm, 3));
    geom.setIndex(idx);
    return geom;
  }

  // Signed distance to a box (centre c, half sizes s) with its edges rounded by r.
  function roundBoxSdf(x, y, z, c, s, r) {
    const qx = Math.abs(x - c[0]) - (s[0] - r), qy = Math.abs(y - c[1]) - (s[1] - r), qz = Math.abs(z - c[2]) - (s[2] - r);
    return Math.hypot(Math.max(qx, 0), Math.max(qy, 0), Math.max(qz, 0)) + Math.min(Math.max(qx, qy, qz), 0) - r;
  }

  // packager (v7): a six-piece burr (a kumiki / bar puzzle). Six square bars,
  // two along each axis, pass through each other in the middle and hold
  // together as one cross-shaped unit. Unit u = 0.28: a bar is 2u x 2u in
  // section and BURR.len = 2.0 long (the ends stick out 0.44 = 1.6 u). The middle cube (4u on a side) is split into
  // 4 x 4 x 4 cells; a cell that more than one bar passes through goes to one
  // of them and the others carry a notch there, so no two bars overlap. Each
  // bar keeps one whole line of cells along its length (its outer line), so
  // it stays in one piece; the other shared cells go round by (i + j + k).
  // This is a computed fill without overlaps, not a checked solvable puzzle.
  // Pairs: the X bars lie side by side in y, the Y bars in z, the Z bars in x
  // (the two Z ends face the front and carry the eyes). A bar is drawn as a
  // rounded solid: the distance to the union of its cells (a cell box is
  // pushed 1.5 r into the bar's own neighbours so flush cells leave no
  // groove) with the edges rounded by r, meshed by surface nets (step h).
  // Wood colours by axis: X = --s4 half with --paper; Y = --s4 with 45 %
  // --s5, then 30 % --ink-soft; Z = the role colour half with --paper.
  // Working: the bars slide out one at a time along their own axis (0.36,
  // far enough for a notch to come out of the middle) and back, 1.1 s each, all six in 6.6 s. Pass: the front Y bar snaps in
  // (0.14 -> 0 in 0.4 s, speeding up) and the unit lights up once in the
  // bars' own colours (every 3 s). Mismatch: the upper X bar sticks halfway (0.34 out to -x); it is
  // pushed in a little and stops again, turned slightly, tinted red. Run:
  // the bars rattle a little with the stride (0.02).
  // opts.packagerStyle = "rings": three interlocked metal rings instead
  // (Borromean rings: ellipses in the three planes, see bodyPackagerRings).
  const BURR = { u: 0.28, len: 2.0, r: 0.05, h: 0.02, slide: 0.36, step: 1.1, stuck: 0.34, pass: 3.0, rattle: 0.02 };
  function bodyPackager(ctx) {
    if (ctx.opts.packagerStyle === "rings") return bodyPackagerRings(ctx);
    const { THREE, role } = ctx;
    const { u, len, r, h } = BURR;
    const V = (x, y, z) => new THREE.Vector3(x, y, z);
    const lightWood = mixHex(C.s[3], C.paper, 0.5);
    const darkWood = mixHex(mixHex(C.s[3], C.s[4], 0.45), C.inkSoft, 0.3);
    const lilac = mixHex(hexNum(role.color), C.paper, 0.5);
    // a = the bar's axis; cb / cc = its cells on the next two axes
    // ((a + 1) % 3 and (a + 2) % 3); line = the cells of its outer line;
    // dir = the way it slides out
    const DEFS = [
      { name: "x-upper", label: "横の上", a: 0, cb: [2, 3], cc: [1, 2], line: [3, 1], dir: -1, color: lightWood },
      { name: "x-lower", label: "横の下", a: 0, cb: [0, 1], cc: [1, 2], line: [0, 2], dir: 1, color: lightWood },
      { name: "y-front", label: "縦の前", a: 1, cb: [2, 3], cc: [1, 2], line: [3, 2], dir: 1, color: darkWood },
      { name: "y-back", label: "縦の後ろ", a: 1, cb: [0, 1], cc: [1, 2], line: [0, 1], dir: 1, color: darkWood },
      { name: "z-right", label: "前後の右", a: 2, cb: [2, 3], cc: [1, 2], line: [3, 2], dir: 1, color: lilac },
      { name: "z-left", label: "前後の左", a: 2, cb: [0, 1], cc: [1, 2], line: [0, 1], dir: 1, color: lilac },
    ];
    const ORDER = ["y-front", "x-upper", "z-right", "y-back", "x-lower", "z-left"];
    const claims = (d, ijk) => d.cb.includes(ijk[(d.a + 1) % 3]) && d.cc.includes(ijk[(d.a + 2) % 3]);
    const onLine = (d, ijk) => ijk[(d.a + 1) % 3] === d.line[0] && ijk[(d.a + 2) % 3] === d.line[1];
    // owner of each middle cell (index into DEFS, or -1 = empty corner)
    const owner = new Map();
    for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) for (let k = 0; k < 4; k++) {
      const ijk = [i, j, k];
      const cl = DEFS.map((d, n) => (claims(d, ijk) ? n : -1)).filter((n) => n >= 0);
      const ln = cl.filter((n) => onLine(DEFS[n], ijk));
      owner.set(ijk.join(","), ln.length ? ln[0] : cl.length ? cl[(i + j + k) % cl.length] : -1);
    }
    // cell edges along any axis: slice -1 and 4 are the ends outside the middle
    const span = (s) => (s < 0 ? [-len / 2, -2 * u] : s > 3 ? [2 * u, len / 2] : [(s - 2) * u, (s - 1) * u]);
    const group = new THREE.Group();
    const bars = DEFS.map((d, n) => {
      const b = (d.a + 1) % 3, c = (d.a + 2) % 3;
      const has = (s, jb, jc) => {
        if (!d.cb.includes(jb) || !d.cc.includes(jc)) return false;
        if (s < 0 || s > 3) return s >= -1 && s <= 4;
        const ijk = [0, 0, 0];
        ijk[d.a] = s; ijk[b] = jb; ijk[c] = jc;
        return owner.get(ijk.join(",")) === n;
      };
      const boxes = [];
      let notches = 0;
      for (let s = -1; s <= 4; s++) for (const jb of d.cb) for (const jc of d.cc) {
        if (!has(s, jb, jc)) { notches++; continue; }
        const lo = [0, 0, 0], hi = [0, 0, 0];
        [lo[d.a], hi[d.a]] = span(s);
        [lo[b], hi[b]] = span(jb);
        [lo[c], hi[c]] = span(jc);
        // push into the neighbours of the same bar
        const nb = [[s - 1, jb, jc, d.a, 0], [s + 1, jb, jc, d.a, 1], [s, jb - 1, jc, b, 0], [s, jb + 1, jc, b, 1], [s, jb, jc - 1, c, 0], [s, jb, jc + 1, c, 1]];
        for (const [ss, bb, cc, ax, up] of nb) if (has(ss, bb, cc)) { if (up) hi[ax] += 1.5 * r; else lo[ax] -= 1.5 * r; }
        boxes.push({ c: lo.map((v, q) => (v + hi[q]) / 2), s: lo.map((v, q) => (hi[q] - v) / 2) });
      }
      const sdf = (x, y, z) => {
        let m = Infinity;
        for (const B of boxes) {
          const v = roundBoxSdf(x, y, z, B.c, B.s, r);
          if (v < m) m = v;
        }
        return m;
      };
      // bounding box of the bar (+ 2 grid steps)
      const blo = [0, 0, 0], bhi = [0, 0, 0];
      [blo[d.a], bhi[d.a]] = [-len / 2, len / 2];
      [blo[b], bhi[b]] = [span(d.cb[0])[0], span(d.cb[1])[1]];
      [blo[c], bhi[c]] = [span(d.cc[0])[0], span(d.cc[1])[1]];
      const lo = blo.map((v) => v - 2 * h);
      const nn = bhi.map((v, q) => Math.ceil((v + 2 * h - lo[q]) / h) + 1);
      const g = surfaceNets(THREE, sdf, lo, nn, h);
      const centre = V(...blo.map((v, q) => (v + bhi[q]) / 2));
      const obj = new THREE.Group();
      obj.position.copy(centre);
      group.add(obj);
      const mat = std(THREE, d.color, 0.58, 0.02);
      const mesh = new THREE.Mesh(g, mat);
      mesh.position.copy(centre).negate();
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData.body = true;
      mesh.userData.bar = d.name;
      mesh.userData.axis = "xyz"[d.a];
      obj.add(mesh);
      addHull(THREE, mesh, 0.026, ctx.hulls);
      const axis = V(0, 0, 0);
      axis.setComponent(d.a, 1);
      return { def: d, obj, mat, centre, axis, notches };
    });
    const byName = Object.fromEntries(bars.map((x) => [x.def.name, x]));
    const sm = (x) => {
      const q = clamp(x, 0, 1);
      return q * q * (3 - 2 * q);
    };
    // working: which bar is out and how far (0 .. BURR.slide)
    const workOf = (t) => {
      const T = BURR.step * ORDER.length;
      const q = ((t % T) + T) % T;
      const k = Math.floor(q / BURR.step);
      const f = (q - k * BURR.step) / BURR.step;
      const out = f < 0.4 ? sm(f / 0.4) : f < 0.6 ? 1 : 1 - sm((f - 0.6) / 0.4);
      return { name: ORDER[k], d: BURR.slide * out };
    };
    // pass: the front Y bar snaps in, then one flash
    const passOf = (t) => {
      const q = ((t % BURR.pass) + BURR.pass) % BURR.pass;
      const d = q < 0.4 ? 0.14 * (1 - (q / 0.4) ** 2) : 0;
      const glow = q >= 0.4 && q < 1.2 ? Math.sin((Math.PI * (q - 0.4)) / 0.8) ** 2 : 0;
      return { d, glow };
    };
    // mismatch: pushed in a little every 1.2 s, stops at the notch
    const stuckOf = (t) => {
      const q = (((t % 1.2) + 1.2) % 1.2) / 1.2;
      return BURR.stuck - (q < 0.5 ? 0.05 * Math.sin((Math.PI * q) / 0.5) : 0);
    };
    const top = V(0, len / 2, -0.12);
    const place = (t, s) => {
      const w = s === "working" ? workOf(t) : null;
      const p = s === "pass" ? passOf(t) : null;
      const ph = (TAU * t) / RUN.period;
      bars.forEach((bar, i) => {
        const d = bar.def;
        let off = 0;
        if (w && w.name === d.name) off = w.d;
        if (p && d.name === "y-front") off = p.d;
        if (s === "mismatch" && d.name === "x-upper") off = stuckOf(t);
        if (s === "run") off = BURR.rattle * Math.sin(2 * ph + 1.1 * i);
        bar.obj.position.copy(bar.centre).addScaledVector(bar.axis, d.dir * off);
        const mis = s === "mismatch" && d.name === "x-upper";
        bar.obj.rotation.set(0, mis ? 0.07 : 0, mis ? -0.06 : 0);
        // pass: the flash is each bar's own colour (brighter, not whiter)
        bar.mat.emissive.setHex(mis ? C.miss : p ? d.color : 0x000000);
        bar.mat.emissiveIntensity = mis ? 0.55 : p ? 0.45 * p.glow : 0;
      });
      const yb = byName["y-back"];
      top.set(0, len / 2, -0.12).add(yb.obj.position).sub(yb.centre);
    };
    place(0, "idle");
    // the eyes sit on the front ends of the two Z bars (left eye on z-left)
    const surface = (dir) => {
      const bar = dir.x < 0 ? byName["z-left"] : byName["z-right"];
      bar.obj.updateMatrix();
      const p = V(dir.x < 0 ? -u : u, 0.05, len / 2).sub(bar.centre).applyMatrix4(bar.obj.matrix);
      const n = V(0, 0, 1).applyQuaternion(bar.obj.quaternion);
      return { d: p.length(), point: p, normal: n };
    };
    const name = (n) => byName[n].def.label;
    return {
      group, top, surface, deforms: true, style: "burr",
      shoulders: { R: V(0.8, -0.28, 0.12), L: V(-0.8, -0.28, 0.12) },
      parts: bars.map((b) => ({ name: b.def.name, axis: "xyz"[b.def.a], color: "#" + b.def.color.toString(16).padStart(6, "0"), notches: b.notches })),
      counts: { bars: 6, axes: 3 },
      caption: "棒 6 本 · 3 つの向きに噛み合う組木 (6 本組木)",
      update(t, s) { place(t, s); },
      contour(t, s) {
        const info = [
          s === "working" ? `組み立ての順番を試している · ${name(workOf(t).name)}の棒を抜いて戻す`
            : s === "pass" ? "最後の棒がぴたっと収まった"
              : s === "mismatch" ? "横の上の棒が途中で引っかかって入らない (食い違い)"
                : "6 本の棒が 3 つの向きに噛み合っている",
          "色は棒の向きごと (コンターではない)",
        ];
        return { info };
      },
    };
  }

  // packager, opts.packagerStyle = "rings": three metal rings that hold each
  // other and do not come apart (Borromean rings). Ring k is an ellipse in a
  // coordinate plane, semi axis RINGS.b on the first axis and RINGS.a on the
  // second, in the cyclic order (x, y), (y, z), (z, x): a tall ring facing
  // the front (the legs hang from its lower arc), a ring front to back (in
  // front of the face) and a wide flat ring that carries the eyes. Metals:
  // steel (white with 12 % --s1), brass (--s4 with 25 % --paper) and the role
  // colour (half with white). Working: the rings turn a little one at a time
  // (0.12 rad, 1.4 s each); pass: one flash every 3 s; mismatch: the front to
  // back ring is turned and stuck (0.22 rad), tinted red; run: they rattle.
  const RINGS = { a: 0.84, b: 0.5, tube: 0.14, step: 1.4, turn: 0.12, stuck: 0.22 };
  function ringPoint(k, th, out) {
    const p = [0, 0, 0];
    p[k] = RINGS.b * Math.cos(th);
    p[(k + 1) % 3] = RINGS.a * Math.sin(th);
    return out.set(p[0], p[1], p[2]);
  }
  function bodyPackagerRings(ctx) {
    const { THREE, role } = ctx;
    const V = (x, y, z) => new THREE.Vector3(x, y, z);
    const { a, b, tube } = RINGS;
    const DEFS = [
      { name: "ring-xy", label: "正面の輪", color: mixHex(0xffffff, C.s[0], 0.12), spin: V(0, 1, 0) },
      { name: "ring-yz", label: "前後の輪", color: mixHex(C.s[3], C.paper, 0.25), spin: V(0, 0, 1) },
      { name: "ring-zx", label: "横の輪", color: mixHex(hexNum(role.color), 0xffffff, 0.5), spin: V(1, 0, 0) },
    ];
    const group = new THREE.Group();
    const rings = DEFS.map((d, k) => {
      class Ring extends THREE.Curve {
        getPoint(s, out) { return ringPoint(k, TAU * s, out || new THREE.Vector3()); }
      }
      const g = new THREE.TubeGeometry(new Ring(), 160, tube, 20, true);
      const obj = new THREE.Group();
      group.add(obj);
      const mat = new THREE.MeshPhysicalMaterial({ color: d.color, metalness: 0.85, roughness: 0.3, clearcoat: 0.5, clearcoatRoughness: 0.15 });
      const mesh = new THREE.Mesh(g, mat);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData.body = true;
      mesh.userData.ring = d.name;
      obj.add(mesh);
      addHull(THREE, mesh, 0.024, ctx.hulls);
      return { def: d, obj, mat };
    });
    const sm = (x) => {
      const q = clamp(x, 0, 1);
      return q * q * (3 - 2 * q);
    };
    const workOf = (t) => {
      const T = RINGS.step * 3;
      const q = ((t % T) + T) % T;
      const k = Math.floor(q / RINGS.step);
      const f = (q - k * RINGS.step) / RINGS.step;
      return { k, ang: RINGS.turn * Math.sin(Math.PI * sm(f)) };
    };
    const place = (t, s) => {
      const w = s === "working" ? workOf(t) : null;
      const q = ((t % 3) + 3) % 3;
      const glow = s === "pass" && q < 0.8 ? Math.sin((Math.PI * q) / 0.8) ** 2 : 0;
      const ph = (TAU * t) / RUN.period;
      rings.forEach((R, k) => {
        let ang = 0;
        if (w && w.k === k) ang = w.ang;
        const mis = s === "mismatch" && k === 1;
        if (mis) ang = RINGS.stuck + 0.03 * Math.sin((TAU * t) / 0.6);
        if (s === "run") ang = 0.03 * Math.sin(2 * ph + 1.3 * k);
        R.obj.quaternion.setFromAxisAngle(R.def.spin, ang);
        R.mat.emissive.setHex(mis ? C.miss : glow ? R.def.color : 0x000000);
        R.mat.emissiveIntensity = mis ? 0.55 : 0.45 * glow;
      });
    };
    place(0, "idle");
    // the eyes sit on the front arc of the flat ring (ring-zx, k = 2)
    const flat = rings[2];
    const surface = (dir) => {
      const x = Math.sign(dir.x || 1) * 0.27;
      const th = Math.asin(x / a);
      const c = ringPoint(2, th, V());
      const n = V(c.x / (a * a), 0, c.z / (b * b)).normalize();
      flat.obj.updateMatrix();
      const p = c.addScaledVector(n, tube).applyMatrix4(flat.obj.matrix);
      return { d: p.length(), point: p, normal: n.applyQuaternion(flat.obj.quaternion) };
    };
    const shoulder = (sx) => {
      const c = ringPoint(2, Math.asin((sx * 0.78) / a), V());
      return c.add(V(0, -0.02, 0));
    };
    return {
      group, top: V(0, a + 0.8 * tube, 0), surface, deforms: true, style: "rings",
      shoulders: { R: shoulder(1), L: shoulder(-1) },
      parts: rings.map((R) => ({ name: R.def.name, color: "#" + R.def.color.toString(16).padStart(6, "0") })),
      counts: { rings: 3 },
      caption: "金属の輪 3 つ · 絡み合って外れない知恵の輪 (ボロミアンの輪)",
      update(t, s) { place(t, s); },
      contour(t, s) {
        const info = [
          s === "working" ? `輪を外そうと試している · ${DEFS[workOf(t).k].label}を回す`
            : s === "pass" ? "輪がきれいに収まった"
              : s === "mismatch" ? "前後の輪が引っかかって戻らない (食い違い)"
                : "3 つの輪が絡み合って外れない",
          "色は輪ごと (コンターではない)",
        ];
        return { info };
      },
    };
  }

  // handling: a multibody (mechanism) model of the car. The body is the
  // chassis, one rigid body (rounded box). On each side a double wishbone:
  // upper and lower A-arms on revolute joints at the chassis (short
  // cylinders along the car), ball joints (spheres) at the knuckle, the
  // knuckle carrying the wheel on its spindle, and a coil spring around a
  // damper from the lower arm to the chassis. Each rigid body has its own
  // calm colour; the springs take the band colour of their force (the
  // contour of this role). On the front of the chassis a small x / y / z
  // triad at the height of the centre of gravity and a red arrow for the
  // lateral acceleration.
  // Lateral acceleration a(t): idle = 0.45 sin(2 pi t / 3.2); working = a
  // double lane change (4.4 s, peak 1); mismatch = a steady corner
  // beyond the limit, 1.3 + 0.06 sin(2 pi t / 1.6). The chassis rolls 0.1 a toward the outside of the corner
  // (torso roll); the wheels and knuckles stay on the ground and the links
  // follow their joints. Spring force (relative) F = 0.5 - 0.35 a sx
  // (sx = +1 on the prop side), 0.5 = the static share; when F < 0.12 the
  // inner wheel lifts by 0.1 (0.12 - F) / 0.06 (up to 0.1).
  function bodyHandling(ctx) {
    const { THREE } = ctx;
    const HALF = new THREE.Vector3(0.66, 0.92, 0.8);
    const RB = roundedBox(THREE, 3, 4, []);
    const first = RB.place(HALF, 0.3);
    const fe = feMesh(THREE, first.P, RB.elems, { smooth: true, normals: first.N });
    const group = new THREE.Group();
    // v6: the chassis is a machined metal part: high metalness, low
    // roughness and a clear coat, so it mirrors the environment light
    // (white mixed with 12 % --s1 for a cool, bright steel)
    const steel = new THREE.MeshPhysicalMaterial({
      color: mixHex(0xffffff, C.s[0], 0.12),
      metalness: 0.9, roughness: 0.26, clearcoat: 0.7, clearcoatRoughness: 0.1,
    });
    const chassis = new THREE.Mesh(fe.geom, steel);
    chassis.castShadow = true;
    chassis.receiveShadow = true;
    chassis.frustumCulled = false;
    chassis.userData.body = true;
    chassis.userData.metal = true;
    group.add(chassis);
    const hull = new THREE.Mesh(fe.hullGeom, null);
    hull.userData.hullWidth = 0.035;
    hull.frustumCulled = false;
    group.add(hull);
    ctx.hulls.push(hull);
    // machined details: a parting line around the housing (a thin dark
    // groove on the rounded section at y = 0.5) and four hex bolt heads on
    // the flat top
    {
      const rr = 0.3 + 0.004, hx = HALF.x - 0.3, hz = HALF.z - 0.3, yP = 0.5;
      const loop = [];
      for (let k = 0; k < 4; k++) {
        const cx = (k === 0 || k === 3 ? 1 : -1) * hx, cz = (k < 2 ? 1 : -1) * hz;
        for (let i = 0; i <= 8; i++) {
          const a = (k * Math.PI) / 2 + (i / 8) * (Math.PI / 2);
          loop.push(new THREE.Vector3(cx + rr * Math.cos(a), yP, cz + rr * Math.sin(a)));
        }
      }
      const groove = new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(loop, true), 120, 0.009, 5, true), ctx.M.ink);
      group.add(groove);
      for (const [x, z] of [[-0.22, 0.32], [0.22, 0.32], [-0.22, -0.32], [0.22, -0.32]]) {
        const bolt = new THREE.Mesh(new THREE.CylinderGeometry(0.055, 0.055, 0.04, 6), steel);
        bolt.position.set(x, HALF.y + 0.015, z);
        bolt.castShadow = true;
        group.add(bolt);
        addHull(THREE, bolt, 0.01, ctx.hulls);
      }
    }

    const bump = (u, a, b) => {
      const v = (u - a) / (b - a);
      return v < 0 || v > 1 ? 0 : 1 - 2 * v;
    };
    const dlcA = (u) => bump(u, 0, 0.25) - bump(u, 0.45, 0.7);
    const accel = (t, s) => {
      const u = (((t % 4.4) + 4.4) % 4.4) / 4.4;
      if (s === "working") return dlcA(u);
      // mismatch: a steady corner beyond the limit, the inner wheel lifts
      if (s === "mismatch") return 1.3 + 0.06 * Math.sin((TAU * t) / 1.6);
      return 0.45 * Math.sin((TAU * t) / 3.2);
    };
    const ROLL = 0.1;
    const force = (a, sx) => Math.max(0, 0.5 - 0.35 * a * sx);
    const lift = (F) => 0.1 * clamp((0.12 - F) / 0.06, 0, 1);

    // CG triad and lateral acceleration arrow on the front of the chassis
    const V = (x, y, z) => new THREE.Vector3(x, y, z);
    const O = V(0, -0.42, HALF.z + 0.012);
    const triad = new THREE.Group();
    triad.position.copy(O);
    group.add(triad);
    const axisCol = [C.s[4], C.pass, C.s[0]];
    [V(1, 0, 0), V(0, 1, 0), V(0, 0, 1)].forEach((d, k) => {
      const m = new THREE.MeshBasicMaterial({ color: axisCol[k] });
      triad.add(rodBetween(THREE, V(0, 0, 0), d.clone().multiplyScalar(0.17), 0.016, m, 8));
      const tip = new THREE.Mesh(new THREE.ConeGeometry(0.038, 0.08, 12), m);
      tip.position.copy(d).multiplyScalar(0.2);
      tip.quaternion.setFromUnitVectors(V(0, 1, 0), d);
      triad.add(tip);
    });
    const cgDot = new THREE.Mesh(new THREE.SphereGeometry(0.035, 14, 10), ctx.M.ink);
    triad.add(cgDot);
    const red = new THREE.MeshBasicMaterial({ color: C.miss });
    const ay = new THREE.Group();
    ay.position.set(0, -0.66, HALF.z + 0.05);
    group.add(ay);
    const ayRod = new THREE.Mesh(new THREE.CylinderGeometry(0.024, 0.024, 1, 10), red);
    ayRod.rotation.z = -Math.PI / 2;
    const ayHead = new THREE.Mesh(new THREE.ConeGeometry(0.07, 0.16, 14), red);
    ayHead.rotation.z = -Math.PI / 2;
    ay.add(ayRod, ayHead);

    // suspension (in the frame of the feet: not rolled with the chassis)
    const susp = new THREE.Group();
    const Z0 = -0.2;
    const armMat = std(THREE, mixHex(C.s[0], C.paper, 0.2), 0.45, 0.1);
    const knuckleMat = std(THREE, mixHex(C.s[3], C.paper, 0.35), 0.5, 0.05);
    const tyreMat = std(THREE, C.inkSoft, 0.8);
    const damperMat = std(THREE, C.inkSoft, 0.4, 0.3);
    const jointMat = ctx.M.ink;
    const metal = ctx.M.metal;
    // outline shell as a child, so it follows a link that moves
    const childHull = (m, w) => {
      const h = new THREE.Mesh(smoothCopy(THREE, m.geometry), null);
      h.userData.hullWidth = w;
      m.add(h);
      ctx.hulls.push(h);
    };
    const unitRod = (r, mat, radial) => {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, 1, radial || 10), mat);
      m.castShadow = true;
      susp.add(m);
      return m;
    };
    const Y = V(0, 1, 0);
    const spanRod = (m, a, b) => {
      const d = b.clone().sub(a);
      m.position.copy(a).addScaledVector(d, 0.5);
      m.scale.set(1, Math.max(1e-3, d.length()), 1);
      m.quaternion.setFromUnitVectors(Y, d.normalize());
    };
    // coil spring of unit length along y (6 turns)
    const coil = [];
    for (let i = 0; i <= 120; i++) {
      const u = i / 120;
      coil.push(V(0.055 * Math.cos(TAU * 6 * u), u, 0.055 * Math.sin(TAU * 6 * u)));
    }
    const coilGeom = new THREE.TubeGeometry(new THREE.CatmullRomCurve3(coil), 240, 0.013, 5, false);
    const sides = [-1, 1].map((sx) => {
      const S = { sx };
      // chassis points (body coordinates)
      S.lowIn = [V(sx * 0.64, -0.8, Z0 + 0.16), V(sx * 0.64, -0.8, Z0 - 0.16)];
      S.upIn = [V(sx * 0.67, -0.3, Z0 + 0.16), V(sx * 0.67, -0.3, Z0 - 0.16)];
      S.mount = V(sx * 0.71, 0.08, Z0);
      // knuckle side (feet frame), before the lift
      S.wheel = V(sx * 1.29, 0.39, Z0);
      S.lowBall = V(sx * 1.1, 0.3, Z0);
      S.upBall = V(sx * 1.06, 0.84, Z0);
      S.lowArm = [unitRod(0.036, armMat), unitRod(0.036, armMat)];
      S.upArm = [unitRod(0.03, armMat), unitRod(0.03, armMat)];
      S.revs = [0, 1, 2, 3].map(() => unitRod(0.05, jointMat, 14));
      S.balls = [0, 1, 2, 3].map(() => {
        const b = new THREE.Mesh(new THREE.SphereGeometry(0.055, 16, 12), jointMat);
        susp.add(b);
        return b;
      });
      S.knuckle = unitRod(0.05, knuckleMat, 12);
      childHull(S.knuckle, 0.012);
      for (const m of [...S.lowArm, ...S.upArm]) childHull(m, 0.01);
      S.spindle = unitRod(0.03, knuckleMat, 10);
      S.tyre = new THREE.Group();
      susp.add(S.tyre);
      // the wheel spins about its axle (x) when running; five spokes on the
      // rim make the turn visible
      S.spin = new THREE.Group();
      S.tyre.add(S.spin);
      const tyre = new THREE.Mesh(new THREE.CylinderGeometry(0.39, 0.39, 0.19, 40), tyreMat);
      tyre.rotation.z = Math.PI / 2;
      tyre.castShadow = true;
      S.spin.add(tyre);
      addHull(THREE, tyre, 0.02, ctx.hulls);
      const rim = new THREE.Mesh(new THREE.CylinderGeometry(0.24, 0.24, 0.02, 32), metal);
      rim.rotation.z = Math.PI / 2;
      rim.position.x = sx * 0.098;
      S.spin.add(rim);
      const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.03, 16), jointMat);
      hub.rotation.z = Math.PI / 2;
      hub.position.x = sx * 0.11;
      S.spin.add(hub);
      for (let k = 0; k < 5; k++) {
        const a = (k * TAU) / 5;
        const spoke = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.17, 0.035), jointMat);
        spoke.position.set(sx * 0.112, 0.13 * Math.cos(a), 0.13 * Math.sin(a));
        spoke.rotation.x = a;
        S.spin.add(spoke);
      }
      S.springMat = std(THREE, BAND_COLORS[5], 0.4, 0.1);
      S.spring = new THREE.Mesh(coilGeom, S.springMat);
      S.spring.castShadow = true;
      susp.add(S.spring);
      S.damper = unitRod(0.032, damperMat, 12);
      S.rod = unitRod(0.013, metal, 8);
      return S;
    });
    let lastA = 0, lastS = "idle";
    const lerpV = (a, b, f) => a.clone().lerp(b, f);
    return {
      group, hopGroup: susp, top: V(0, HALF.y, 0),
      surface: (d) => fe.surface(d),
      shoulders: { R: V(0.62, -0.25, 0.27), L: V(-0.62, -0.27, 0.24) },
      counts: { bodies: 9, joints: 10 },
      caption: "剛体 9 · ジョイント 10 · ばねとダンパー 2",
      roll(t, s) { return ROLL * accel(t, s); },
      update(t, s) {
        const a = accel(t, s);
        lastA = a;
        lastS = s;
        const L = 0.25 + 0.45 * Math.min(1.35, Math.abs(a));
        ay.scale.x = a >= 0 ? 1 : -1;
        ayRod.scale.y = L - 0.14;
        ayRod.position.x = (L - 0.14) / 2;
        ayHead.position.x = L - 0.08;
        ay.visible = Math.abs(a) > 0.03;
        for (const S of sides) {
          const F = force(a, S.sx);
          const k = Math.min(BANDS - 1, Math.floor(clamp(F, 0, 0.99999) * BANDS));
          S.springMat.color.set(BAND_COLORS[k]);
          // run: the wheels roll forward (+z), 1.6 turns per second
          S.spin.rotation.x = s === "run" ? TAU * 1.6 * t : 0;
        }
      },
      // after the torso has moved: links between the chassis joints (toFeet
      // maps body coordinates to the feet frame) and the knuckle joints
      after(toFeet) {
        for (const S of sides) {
          const up = V(0, lift(force(lastA, S.sx)), 0);
          const wheel = S.wheel.clone().add(up), lowBall = S.lowBall.clone().add(up), upBall = S.upBall.clone().add(up);
          const lowIn = S.lowIn.map(toFeet), upIn = S.upIn.map(toFeet), mount = toFeet(S.mount);
          const zAx = toFeet(V(0, 0, 1)).sub(toFeet(V(0, 0, 0))).normalize();
          for (let i = 0; i < 2; i++) {
            spanRod(S.lowArm[i], lowIn[i], lowBall);
            spanRod(S.upArm[i], upIn[i], upBall);
          }
          [lowIn[0], lowIn[1], upIn[0], upIn[1]].forEach((p, i) => spanRod(S.revs[i], p.clone().addScaledVector(zAx, -0.04), p.clone().addScaledVector(zAx, 0.04)));
          const low = lerpV(lowIn[0].clone().lerp(lowIn[1], 0.5), lowBall, 0.62);
          [lowBall, upBall, low, mount].forEach((p, i) => S.balls[i].position.copy(p));
          spanRod(S.knuckle, lowBall, upBall);
          const mid = lowBall.clone().lerp(upBall, 0.25);
          spanRod(S.spindle, mid, wheel);
          S.tyre.position.copy(wheel);
          // spring and damper from the lower arm to the chassis mount
          const d = mount.clone().sub(low);
          S.spring.position.copy(low);
          S.spring.scale.set(1, d.length(), 1);
          S.spring.quaternion.setFromUnitVectors(Y, d.clone().normalize());
          spanRod(S.damper, low, lerpV(low, mount, 0.55));
          spanRod(S.rod, low, mount);
        }
      },
      contour(t, s) {
        const a = accel(t, s);
        const v = [force(a, -1), force(a, 1)];
        const info = [s === "working" ? "ダブルレーンチェンジ (作業中)" : s === "mismatch" ? "曲がりすぎて内側の車輪が浮いた (食い違い)" : "ゆるいコーナー", `横加速度 ${a.toFixed(2)} · ロール ${(ROLL * a).toFixed(3)} rad · 0.5 = 静止の荷重`];
        return Object.assign(stats(v), { info });
      },
    };
  }

  const BUILDERS = {
    "fe-sphere": bodyLead,
    "cad-model": bodyDesigner,
    "assembly": bodyPackager,
    "multibody": bodyHandling,
    "combustion": bodyPowertrain,
    "flow": bodyAero,
    "impact-wave": bodyCrash,
    "stress-concentration": bodyStrength,
    "mode-shape": bodyNvh,
    "heat": bodyThermal,
  };

  // Each prop is built around its grip at the origin, about 0.6 units tall.
  function buildProp(THREE, role, M, hulls, live) {
    const g = new THREE.Group();
    const add = (geom, mat, x, y, z, hull) => {
      const m = new THREE.Mesh(geom, mat);
      m.position.set(x || 0, y || 0, z || 0);
      m.castShadow = true;
      g.add(m);
      if (hull) addHull(THREE, m, hull, hulls);
      return m;
    };
    const accent = std(THREE, role.color, 0.45);
    const H = 0.022; // prop outline width

    switch (role.prop) {
      case "clipboard": {
        add(new THREE.BoxGeometry(0.5, 0.64, 0.045), accent, 0.2, 0.12, 0, H);
        add(new THREE.BoxGeometry(0.41, 0.5, 0.012), M.paper, 0.2, 0.08, 0.028);
        for (let i = 0; i < 4; i++) add(new THREE.BoxGeometry(i === 0 ? 0.2 : 0.3, 0.022, 0.006), M.inkFaint, i === 0 ? 0.14 : 0.2, 0.2 - i * 0.09, 0.036);
        add(new THREE.BoxGeometry(0.2, 0.08, 0.06), M.metal, 0.2, 0.42, 0.012, H);
        g.rotation.set(0, -0.35, 0.12);
        break;
      }
      case "pencil": {
        const body = add(new THREE.CylinderGeometry(0.058, 0.058, 0.5, 6), accent, 0, 0.1, 0, H);
        body.rotation.y = Math.PI / 6;
        add(new THREE.ConeGeometry(0.058, 0.13, 6), M.paper, 0, 0.415, 0, H).rotation.y = Math.PI / 6;
        add(new THREE.ConeGeometry(0.02, 0.045, 12), M.ink, 0, 0.462, 0);
        add(new THREE.CylinderGeometry(0.062, 0.062, 0.05, 16), M.metal, 0, -0.17, 0, H);
        add(new THREE.CylinderGeometry(0.058, 0.058, 0.07, 16), std(THREE, C.miss, 0.7), 0, -0.23, 0, H);
        g.rotation.set(0.15, 0, -0.55);
        break;
      }
      case "spanner": {
        const R = 0.13, w = 0.05, depth = 0.11;
        const yTop = Math.sqrt(R * R - w * w);
        const jaw = new THREE.Shape();
        jaw.moveTo(w, yTop);
        jaw.absarc(0, 0, R, Math.atan2(yTop, w), Math.atan2(yTop, -w), true);
        jaw.lineTo(-w, yTop - depth);
        jaw.lineTo(w, yTop - depth);
        jaw.closePath();
        const ext = { depth: 0.045, bevelEnabled: false, curveSegments: 20 };
        const j = add(new THREE.ExtrudeGeometry(jaw, ext), accent, 0, 0.42, -0.022, H);
        const ring = new THREE.Shape();
        ring.absarc(0, 0, 0.1, 0, TAU, false);
        const hole = new THREE.Path();
        hole.absarc(0, 0, 0.05, 0, TAU, true);
        ring.holes.push(hole);
        add(new THREE.ExtrudeGeometry(ring, ext), accent, 0, -0.2, -0.022, H);
        add(new THREE.BoxGeometry(0.085, 0.5, 0.04), accent, 0, 0.1, 0, H);
        j.rotation.z = 0;
        g.rotation.set(0, -0.2, -0.45);
        break;
      }
      case "wheel": {
        const w = new THREE.Group();
        g.add(w);
        const rim = new THREE.Mesh(new THREE.TorusGeometry(0.27, 0.042, 12, 48), M.ink);
        rim.castShadow = true;
        w.add(rim);
        for (let i = 0; i < 3; i++) {
          const a = Math.PI / 2 + (i * TAU) / 3 + Math.PI / 3;
          const s = new THREE.Mesh(new THREE.BoxGeometry(0.25, 0.05, 0.03), M.inkSoft);
          s.position.set(Math.cos(a) * 0.13, Math.sin(a) * 0.13, 0);
          s.rotation.z = a;
          w.add(s);
        }
        const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.085, 0.085, 0.07, 24), accent);
        hub.rotation.x = Math.PI / 2;
        w.add(hub);
        addHull(THREE, hub, H, hulls);
        w.position.set(0.12, 0.27, 0);
        w.rotation.set(0.1, 0.3, 0);
        break;
      }
      case "battery": {
        add(new THREE.BoxGeometry(0.32, 0.46, 0.2), M.paper, 0, 0.17, 0, H);
        add(new THREE.BoxGeometry(0.13, 0.06, 0.1), M.ink, 0, 0.43, 0);
        const bolt = new THREE.Shape();
        const P = [[0.05, 0.17], [-0.07, -0.01], [0.0, -0.01], [-0.05, -0.17], [0.08, 0.03], [0.01, 0.03]];
        bolt.moveTo(P[0][0], P[0][1]);
        for (let i = 1; i < P.length; i++) bolt.lineTo(P[i][0], P[i][1]);
        bolt.closePath();
        add(new THREE.ExtrudeGeometry(bolt, { depth: 0.03, bevelEnabled: false }), accent, 0, 0.16, 0.1, 0.012);
        g.rotation.set(0, -0.3, 0.08);
        break;
      }
      case "windsock": {
        add(new THREE.CylinderGeometry(0.022, 0.022, 0.96, 10), M.metal, 0, 0.3, 0, H);
        add(new THREE.SphereGeometry(0.04, 12, 8), M.ink, 0, 0.79, 0);
        const sock = new THREE.Group();
        sock.position.set(0, 0.72, 0);
        g.add(sock);
        const segs = 4, L = 0.62, r0 = 0.14, r1 = 0.07;
        for (let i = 0; i < segs; i++) {
          const ra = r0 + ((r1 - r0) * i) / segs, rb = r0 + ((r1 - r0) * (i + 1)) / segs;
          const seg = new THREE.Mesh(
            new THREE.CylinderGeometry(ra, rb, L / segs, 18, 1, true),
            i % 2 === 0 ? M.paperDouble : new THREE.MeshStandardMaterial({ color: role.color, roughness: 0.6, side: THREE.DoubleSide })
          );
          seg.rotation.z = -Math.PI / 2;
          seg.position.set(0.03 + (L / segs) * (i + 0.5), -0.1, 0);
          seg.castShadow = true;
          sock.add(seg);
          addHull(THREE, seg, 0.012, hulls);
        }
        live.sock = sock;
        g.rotation.set(0, 0, -0.08);
        break;
      }
      case "target": {
        add(new THREE.CylinderGeometry(0.022, 0.022, 0.3, 10), M.metal, 0, 0.0, 0, H);
        const disc = new THREE.Group();
        disc.position.set(0, 0.36, 0);
        g.add(disc);
        const yellow = std(THREE, C.s[3], 0.5);
        for (let i = 0; i < 4; i++) {
          const q = new THREE.Mesh(
            new THREE.CylinderGeometry(0.22, 0.22, 0.04, 16, 1, false, (i * Math.PI) / 2, Math.PI / 2),
            i % 2 === 0 ? yellow : M.ink
          );
          q.rotation.x = Math.PI / 2;
          q.castShadow = true;
          disc.add(q);
        }
        const ring = new THREE.Mesh(new THREE.TorusGeometry(0.22, 0.018, 8, 48), M.ink);
        disc.add(ring);
        disc.rotation.set(0, -0.35, Math.PI / 4);
        break;
      }
      case "ibeam": {
        const s = new THREE.Shape();
        const W = 0.36, Hh = 0.38, tf = 0.07, tw = 0.07;
        s.moveTo(-W / 2, -Hh / 2);
        s.lineTo(W / 2, -Hh / 2);
        s.lineTo(W / 2, -Hh / 2 + tf);
        s.lineTo(tw / 2, -Hh / 2 + tf);
        s.lineTo(tw / 2, Hh / 2 - tf);
        s.lineTo(W / 2, Hh / 2 - tf);
        s.lineTo(W / 2, Hh / 2);
        s.lineTo(-W / 2, Hh / 2);
        s.lineTo(-W / 2, Hh / 2 - tf);
        s.lineTo(-tw / 2, Hh / 2 - tf);
        s.lineTo(-tw / 2, -Hh / 2 + tf);
        s.lineTo(-W / 2, -Hh / 2 + tf);
        s.closePath();
        const beam = add(
          new THREE.ExtrudeGeometry(s, { depth: 0.42, bevelEnabled: true, bevelSize: 0.008, bevelThickness: 0.008, bevelSegments: 1 }),
          std(THREE, C.inkFaint, 0.34, 0.5), 0.06, 0.2, -0.21, H
        );
        beam.userData.k = 1;
        g.rotation.set(0.05, -0.55, 0);
        break;
      }
      case "fork": {
        const steel = M.metal;
        add(new THREE.CylinderGeometry(0.024, 0.024, 0.36, 10), steel, -0.065, 0.36, 0, 0.01);
        add(new THREE.CylinderGeometry(0.024, 0.024, 0.36, 10), steel, 0.065, 0.36, 0, 0.01);
        const u = add(new THREE.TorusGeometry(0.065, 0.024, 8, 20, Math.PI), steel, 0, 0.18, 0, 0.01);
        u.rotation.z = Math.PI;
        add(new THREE.CylinderGeometry(0.03, 0.03, 0.3, 10), accent, 0, -0.03, 0, H);
        const arcs = new THREE.Group();
        arcs.position.set(0, 0.47, 0);
        g.add(arcs);
        const arcMat = new THREE.MeshStandardMaterial({ color: role.color, roughness: 0.5 });
        for (const side of [-1, 1]) {
          for (let k = 0; k < 2; k++) {
            const a = new THREE.Mesh(new THREE.TorusGeometry(0.15 + k * 0.08, 0.014, 6, 20, Math.PI * 0.45), arcMat);
            a.rotation.z = side > 0 ? -Math.PI * 0.225 : Math.PI - Math.PI * 0.225;
            arcs.add(a);
          }
        }
        live.arcs = arcs;
        g.scale.setScalar(1.2);
        g.rotation.set(0, -0.2, -0.12);
        break;
      }
      case "thermometer": {
        add(new THREE.CylinderGeometry(0.055, 0.055, 0.5, 18), M.paper, 0, 0.28, 0, H);
        add(new THREE.SphereGeometry(0.055, 18, 10), M.paper, 0, 0.53, 0, H);
        const red = std(THREE, C.s[4], 0.35);
        add(new THREE.SphereGeometry(0.1, 20, 14), red, 0, 0, 0, H);
        const liquid = add(new THREE.CylinderGeometry(0.03, 0.03, 1, 12), red, 0, 0.0, 0.035);
        live.liquid = liquid;
        for (let i = 0; i < 4; i++) add(new THREE.BoxGeometry(0.035, 0.012, 0.012), M.ink, 0.06, 0.16 + i * 0.09, 0.03);
        g.rotation.set(0, -0.2, -0.1);
        break;
      }
    }
    return g;
  }

  function buildCheck(THREE, M, hulls) {
    const g = new THREE.Group();
    const badge = new THREE.Mesh(new THREE.CylinderGeometry(0.27, 0.27, 0.05, 40), M.paper);
    badge.rotation.x = Math.PI / 2;
    g.add(badge);
    addHull(THREE, badge, 0.03, hulls);
    const green = new THREE.MeshStandardMaterial({ color: C.pass, roughness: 0.45 });
    const p0 = new THREE.Vector3(-0.13, 0.0, 0.05), p1 = new THREE.Vector3(-0.035, -0.1, 0.05), p2 = new THREE.Vector3(0.14, 0.12, 0.05);
    g.add(capsuleBetween(THREE, p0, p1, 0.035, green));
    g.add(capsuleBetween(THREE, p1, p2, 0.035, green));
    return g;
  }

  function buildQuestion(THREE, M, hulls) {
    const g = new THREE.Group();
    const badge = new THREE.Mesh(new THREE.CylinderGeometry(0.27, 0.27, 0.05, 40), M.paper);
    badge.rotation.x = Math.PI / 2;
    g.add(badge);
    addHull(THREE, badge, 0.03, hulls);
    const red = new THREE.MeshStandardMaterial({ color: C.miss, roughness: 0.45 });
    const pts = [
      [-0.085, 0.07], [-0.07, 0.13], [-0.02, 0.165], [0.045, 0.16], [0.085, 0.115],
      [0.08, 0.06], [0.035, 0.025], [0.0, -0.01], [0.0, -0.05],
    ].map((p) => new THREE.Vector3(p[0], p[1], 0.05));
    g.add(new THREE.Mesh(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 40, 0.032, 10, false), red));
    for (const p of [pts[0], pts[pts.length - 1]]) {
      const cap = new THREE.Mesh(new THREE.SphereGeometry(0.032, 12, 8), red);
      cap.position.copy(p);
      g.add(cap);
    }
    const dot = new THREE.Mesh(new THREE.SphereGeometry(0.04, 14, 10), red);
    dot.position.set(0, -0.125, 0.05);
    g.add(dot);
    return g;
  }

  // Contact shadow: radial falloff drawn into a small data texture.
  function blobTexture(THREE) {
    const N = 64;
    const data = new Uint8Array(N * N * 4);
    for (let y = 0; y < N; y++) {
      for (let x = 0; x < N; x++) {
        const dx = (x + 0.5) / N * 2 - 1, dy = (y + 0.5) / N * 2 - 1;
        const r = Math.min(1, Math.hypot(dx, dy));
        const a = Math.pow(1 - r * r, 2.2);
        const i = (y * N + x) * 4;
        data[i] = data[i + 1] = data[i + 2] = 255;
        data[i + 3] = Math.round(a * 255);
      }
    }
    const tex = new THREE.DataTexture(data, N, N, THREE.RGBAFormat);
    tex.needsUpdate = true;
    tex.magFilter = THREE.LinearFilter;
    tex.minFilter = THREE.LinearFilter;
    return tex;
  }

  // ---- create -------------------------------------------------------------

  function create(THREE, opts) {
    opts = Object.assign({ role: "lead", mesh: "coarse", edges: true, nodes: true, theme: "paper" }, opts || {});
    const role = ROLES.find((r) => r.id === opts.role) || ROLES[0];
    const spec = BODIES[role.id];
    const seed = hashString(role.id);

    const U = {
      uPal: { value: BAND_COLORS.map((h) => new THREE.Color(h)) },
      uRimColor: { value: new THREE.Color(C.glow) },
      uRim: { value: 0.2 },
      uLinePx: { value: 1.5 },
      uT: { value: 0 },
    };

    const M = {
      ink: std(THREE, C.ink, 0.6),
      inkSoft: std(THREE, C.inkSoft, 0.5),
      inkFaint: std(THREE, C.inkFaint, 0.7),
      paper: std(THREE, C.paper, 0.7),
      paperDouble: new THREE.MeshStandardMaterial({ color: C.paper, roughness: 0.7, side: THREE.DoubleSide }),
      metal: std(THREE, 0xc9c5bb, 0.32, 0.55),
      white: std(THREE, 0xffffff, 0.3),
      limb: new THREE.MeshStandardMaterial({ color: C.ink, roughness: 0.55, flatShading: true }),
    };
    const hulls = [];
    const live = {};

    const root = new THREE.Group();
    root.name = "pet:" + role.id;
    // mover carries the whole pet with its shadow and marks across the
    // ground when it runs across (root itself is left to the caller)
    const mover = new THREE.Group();
    root.add(mover);
    const hop = new THREE.Group();
    mover.add(hop);

    // torso pivots at the bottom of the body so leaning and tilting look planted
    const PIVOT_Y = BODY_Y - SHAPE.sy * 0.92;
    const torso = new THREE.Group();
    torso.position.y = PIVOT_Y;
    hop.add(torso);
    const core = new THREE.Group();
    core.position.y = BODY_Y - PIVOT_Y;
    torso.add(core);

    // limb roots in body coordinates (centre of the body at the origin);
    // the loads of the stress contours act here
    const legTop = (sx) => new THREE.Vector3(sx * 0.34, PIVOT_Y + 0.22, 0.05);
    const toBody = (p) => p.clone().sub(new THREE.Vector3(0, BODY_Y, 0));
    let shoulderR = shapePoint(THREE, new THREE.Vector3(0.9, -0.28, 0.3).normalize()).multiplyScalar(0.95);
    const handR = new THREE.Vector3(1.2, -0.52, 0.36);
    let shoulderL = shapePoint(THREE, new THREE.Vector3(-0.92, -0.3, 0.25).normalize()).multiplyScalar(0.95);
    const handL = new THREE.Vector3(-1.1, -0.6, 0.28);
    // "R" is the prop side (+x) and "L" the other side (-x), as for the arms
    const roots = {
      legL: shapePoint(THREE, toBody(legTop(-1)).normalize()),
      legR: shapePoint(THREE, toBody(legTop(1)).normalize()),
      armR: shoulderR.clone(),
      armL: shoulderL.clone(),
      armLenR: shoulderR.distanceTo(handR),
      armLenL: shoulderL.distanceTo(handL),
    };

    const ctx = {
      THREE, U, M, opts, role, seed, roots, hulls,
      fine: opts.mesh === "fine",
      propMass: PROP_MASS[role.prop] ?? 0.3,
      contourMats: [], flowMats: [], inkMats: [], dots: [],
    };
    const B = BUILDERS[spec.kind](ctx);
    core.add(B.group);
    if (B.ground) mover.add(B.ground);
    // parts that stand on the feet and do not roll or lean with the torso
    if (B.hopGroup) hop.add(B.hopGroup);
    for (const d of ctx.dots) d.visible = !!opts.nodes;
    const anchor = B.anchor || B.group;
    const antAnchor = B.antennaAnchor || anchor;
    if (B.shoulders) {
      shoulderR = B.shoulders.R;
      shoulderL = B.shoulders.L;
    }

    // legs and feet (stay on the ground); each leg turns about its hip
    // (the top of the leg) when running
    const legs = new THREE.Group();
    hop.add(legs);
    const hips = [];
    for (const sx of [-1, 1]) {
      const top = legTop(sx);
      const hip = new THREE.Group();
      hip.position.copy(top);
      legs.add(hip);
      hips.push({ hip, sx });
      const bot = new THREE.Vector3(sx * 0.36, 0.08, 0.06).sub(top);
      const leg = capsuleBetween(THREE, bot, new THREE.Vector3(), 0.075, M.limb, 2, 6);
      leg.castShadow = true;
      hip.add(leg);
      const foot = new THREE.Mesh(new THREE.SphereGeometry(1, 8, 5), M.limb);
      foot.scale.set(0.14, 0.075, 0.19);
      foot.position.set(sx * 0.37, 0.075, 0.1).sub(top);
      foot.castShadow = true;
      hip.add(foot);
    }

    // eyes, sitting on the body surface (re-placed each frame on bodies that
    // change shape)
    const eyes = [];
    for (const sx of [-1, 1]) {
      const dir = new THREE.Vector3(sx * 0.28, 0.17, 0.95).normalize();
      const eye = new THREE.Group();
      anchor.add(eye);
      const lid = new THREE.Group();
      eye.add(lid);
      const white = new THREE.Mesh(new THREE.SphereGeometry(0.19, 32, 20), M.white);
      white.scale.set(1, 1.08, 0.5);
      lid.add(white);
      const ring = new THREE.Mesh(new THREE.SphereGeometry(0.19, 32, 20), null);
      ring.scale.copy(white.scale);
      ring.userData.hullWidth = 0.032;
      lid.add(ring);
      hulls.push(ring);
      const pupil = new THREE.Group();
      lid.add(pupil);
      const pm = new THREE.Mesh(new THREE.SphereGeometry(0.088, 24, 16), std(THREE, C.ink, 0.25));
      pm.scale.set(1, 1.1, 0.42);
      pm.position.z = 0.074;
      pupil.add(pm);
      const shine = new THREE.Mesh(new THREE.SphereGeometry(0.024, 12, 8), new THREE.MeshBasicMaterial({ color: 0xffffff }));
      shine.position.set(-0.03, 0.04, 0.112);
      pupil.add(shine);
      eyes.push({ eye, lid, pupil, side: sx, dir });
    }
    const zAxis = new THREE.Vector3(0, 0, 1);
    function placeFace() {
      for (const e of eyes) {
        const s = B.surface(e.dir);
        if (!s) continue;
        const nrm = e.dir.clone().lerp(s.normal, 0.5).normalize();
        e.eye.position.copy(s.point).addScaledVector(nrm, 0.02);
        e.eye.quaternion.setFromUnitVectors(zAxis, nrm);
      }
      antenna.position.copy(B.top);
    }

    // antenna from the top of the body, tip in the role colour
    const antenna = new THREE.Group();
    antAnchor.add(antenna);
    const curve = new THREE.QuadraticBezierCurve3(
      new THREE.Vector3(0, -0.04, 0), new THREE.Vector3(-0.03, 0.3, 0), new THREE.Vector3(0.1, 0.56, 0)
    );
    const stalk = new THREE.Mesh(new THREE.TubeGeometry(curve, 8, 0.028, 6, false), M.limb);
    stalk.castShadow = true;
    antenna.add(stalk);
    const tipMat = new THREE.MeshStandardMaterial({ color: role.color, roughness: 0.3, flatShading: true, emissive: new THREE.Color(role.color), emissiveIntensity: 0 });
    const tip = new THREE.Mesh(new THREE.IcosahedronGeometry(0.15, 1), tipMat);
    tip.position.set(0.1, 0.66, 0);
    tip.castShadow = true;
    antenna.add(tip);
    addHull(THREE, tip, 0.03, hulls);
    placeFace();

    // arm on +x (the prop hand) holding the prop low, and a short arm on -x;
    // each arm hangs from a shoulder pivot so it can swing when running
    const shoulders = {};
    for (const [k, sh, hand] of [["R", shoulderR, handR], ["L", shoulderL, handL]]) {
      const pivot = new THREE.Group();
      pivot.position.copy(sh);
      core.add(pivot);
      const arm = capsuleBetween(THREE, new THREE.Vector3(), hand.clone().sub(sh), 0.06, M.limb, 2, 6);
      arm.castShadow = true;
      pivot.add(arm);
      const h = new THREE.Mesh(new THREE.IcosahedronGeometry(0.088, 1), M.limb);
      h.position.copy(hand).sub(sh);
      pivot.add(h);
      shoulders[k] = pivot;
    }
    const prop = buildProp(THREE, role, M, hulls, live);
    prop.position.copy(handR).add(new THREE.Vector3(0.04, -0.02, 0.06)).sub(shoulderR);
    prop.scale.multiplyScalar(1.25);
    shoulders.R.add(prop);

    // marks above the head, clear of the antenna tip (top of the tip is at
    // about BODY_Y + 1.73; the badge radius is 0.24 after scaling)
    // billboarded in update when a camera is given
    const MARK_Y = BODY_Y + 2.1;
    const marks = new THREE.Group();
    marks.position.set(0.3, MARK_Y, 0.1);
    mover.add(marks);
    const check = buildCheck(THREE, M, hulls);
    const question = buildQuestion(THREE, M, hulls);
    check.scale.setScalar(0.9);
    question.scale.setScalar(0.9);
    marks.add(check, question);

    // contact shadow
    const blob = new THREE.Mesh(
      new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({ map: blobTexture(THREE), color: C.ink, transparent: true, opacity: 0.32, depthWrite: false })
    );
    blob.rotation.x = -Math.PI / 2;
    blob.position.y = 0.004;
    blob.renderOrder = -1;
    mover.add(blob);

    function applyTheme(theme) {
      const night = theme === "night";
      const lineCol = night ? C.onNightSoft : C.ink;
      for (const h of hulls) {
        const wdt = h.userData.hullWidth ?? 0.022;
        if (!h.material || h.material.userData.theme !== theme) {
          const m = hullMaterial(THREE, wdt, lineCol);
          m.userData.theme = theme;
          if (h.material) h.material.dispose();
          h.material = m;
        }
      }
      for (const im of ctx.inkMats) im.mat.color.setHex(night ? im.night : im.paper);
      M.limb.color.setHex(night ? 0x2a2925 : C.ink);
      U.uRim.value = night ? 0.85 : 0.3;
      blob.material.opacity = night ? 0.55 : 0.3;
      blob.material.color.setHex(night ? 0x000000 : C.ink);
    }
    applyTheme(opts.theme);

    const qTmp = new THREE.Quaternion();

    // run: the legs swing in turn, sin(2 pi t / period) (one stride per
    // period), the body bounces twice per stride (lowest when a leg is
    // straight down, highest with the legs spread), leans forward, and the
    // arms swing against the legs. Across: the pet turns toward +x and
    // crosses RUN.span units in RUN.cross seconds, then starts again on the
    // left. Everything depends on t only.
    function runPose(t, across) {
      const ph = (TAU * t) / RUN.period;
      return {
        swing: RUN.swing * Math.sin(ph),
        hopY: (RUN.bob * (1 - Math.cos(2 * ph))) / 2,
        lean: RUN.lean + 0.03 * Math.sin(2 * ph),
        x: across ? -RUN.span / 2 + RUN.span * ((((t / RUN.cross) % 1) + 1) % 1) : 0,
        yaw: across ? RUN.yaw : 0,
      };
    }

    // o.across (or opts.run = "across" at create): run across instead of in place
    function update(t, state, camera, o) {
      state = STATES.includes(state) ? state : "idle";
      const across = o && o.across !== undefined ? !!o.across : opts.run === "across";
      U.uT.value = t;
      B.update(t, state);
      if (B.deforms || B.anchor) placeFace();

      // idle base motion: bob, breathe, antenna sway, blink
      const bob = 0.025 * Math.sin((TAU * t) / 2.6);
      const breathe = Math.sin((TAU * t) / 2.6 + 0.6);
      let hopY = 0, lean = 0, tilt = 0, squash = 0;
      const run = state === "run" ? runPose(t, across) : null;
      mover.position.x = run ? run.x : 0;
      hop.rotation.y = run ? run.yaw : 0;
      for (const h of hips) h.hip.rotation.x = run ? h.sx * run.swing : 0;
      shoulders.L.rotation.x = run ? 0.75 * run.swing : 0;
      shoulders.R.rotation.x = run ? -0.3 * run.swing : 0;
      if (run) {
        hopY = run.hopY;
        lean = run.lean;
      } else if (state === "pass") {
        const ph = (t / 0.9) % 1;
        hopY = 0.24 * Math.sin(Math.PI * ph);
        squash = Math.max(0, 1 - Math.min(ph, 1 - ph) / 0.12) * 0.07;
      } else if (state === "report") {
        lean = 0.2 + 0.025 * Math.sin((TAU * t) / 1.8);
      } else if (state === "mismatch") {
        tilt = 0.24 + 0.03 * Math.sin((TAU * t) / 2.2);
      }
      hop.position.y = hopY;
      torso.position.y = PIVOT_Y + (state === "pass" || run ? 0 : bob);
      // bodies that lean by themselves (handling rolls in the corner)
      torso.rotation.set(lean, 0, tilt + (B.roll ? B.roll(t, state) : 0));
      const sy = 1 + 0.018 * breathe - squash;
      const sxz = 1 - 0.012 * breathe + squash * 0.6;
      torso.scale.set(sxz, sy, sxz);

      antenna.rotation.z = 0.07 * Math.sin((TAU * t) / 1.9 + 0.4) - tilt * 0.3 - (state === "pass" ? 0.12 * Math.sin(Math.PI * ((t / 0.9) % 1)) : 0);
      antenna.rotation.x = 0.04 * Math.sin((TAU * t) / 2.7) - lean * 0.4 - (run ? 0.5 * run.hopY : 0);

      const bp = t % 4.3;
      const blink = bp < 0.16 ? 1 - 0.9 * Math.sin((Math.PI * bp) / 0.16) : 1;
      const lookX = state === "working" ? 0.035 : state === "mismatch" ? 0.02 : 0;
      const lookY = state === "working" ? -0.04 : state === "report" ? -0.005 : 0.005;
      for (const e of eyes) {
        e.lid.scale.y = blink;
        e.pupil.position.set(lookX - e.side * 0.008, lookY, 0);
      }

      tipMat.emissiveIntensity = state === "working" ? 0.25 + 0.25 * Math.sin(TAU * t * 1.2) : 0;

      check.visible = state === "pass";
      question.visible = state === "mismatch";
      marks.position.y = MARK_Y + 0.035 * Math.sin((TAU * t) / 1.4) + hopY;
      marks.rotation.z = state === "mismatch" ? 0.12 * Math.sin((TAU * t) / 2.2) : 0;
      if (camera) {
        root.updateWorldMatrix(true, true);
        marks.parent.getWorldQuaternion(qTmp).invert();
        marks.quaternion.copy(qTmp.multiply(camera.quaternion));
        if (state === "mismatch") marks.rotateZ(0.12 * Math.sin((TAU * t) / 2.2));
      }

      const s = 1.55 * (1 - hopY * 1.1);
      blob.scale.set(s * 1.25, s, 1);

      // links between the moved torso and the feet frame (handling)
      if (B.after) {
        root.updateMatrixWorld(true);
        B.after((p) => B.hopGroup.worldToLocal(B.group.localToWorld(p.clone())));
      }

      if (live.sock) {
        live.sock.rotation.y = 0.18 * Math.sin((TAU * t) / 1.3);
        live.sock.rotation.z = -0.06 + 0.05 * Math.sin((TAU * t) / 0.7);
      }
      if (live.arcs) {
        const k = 1 + 0.08 * Math.sin(TAU * t * 1.5);
        live.arcs.scale.set(k, k, 1);
      }
      if (live.liquid) {
        const lvl = state === "working" ? 0.34 + 0.08 * Math.sin(TAU * t * 0.5) : 0.3;
        live.liquid.scale.y = lvl;
        live.liquid.position.y = lvl / 2;
      }
    }

    // pxPerUnit: framebuffer pixels per pet unit. Small pets get lighter
    // element edges and no node dots so the contour bands still read.
    let showEdges = !!opts.edges, showNodes = !!opts.nodes, px = 50;
    function applyScale() {
      U.uLinePx.value = clamp(px * 0.024, 0.8, 2.4);
      const k = clamp((px - 6) / 24, 0.3, 1);
      for (const m of ctx.contourMats) {
        const hk = m.userData.hk;
        hk.L.uLineW.value = hk.always || showEdges ? hk.line * k : 0;
      }
      for (const d of ctx.dots) d.visible = showNodes && px >= 20;
    }
    function setPixelScale(pxPerUnit) {
      px = pxPerUnit;
      applyScale();
    }

    function setOptions(o) {
      if (o.edges !== undefined) showEdges = !!o.edges;
      if (o.nodes !== undefined) showNodes = !!o.nodes;
      if (o.theme !== undefined) applyTheme(o.theme);
      applyScale();
    }

    function dispose() {
      root.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
        if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach((m) => { if (m.map) m.map.dispose(); m.dispose(); });
      });
    }

    // What the contour shows at (t, state): quantity name, largest and
    // smallest nodal value, and lines for the legend.
    function contour(t, state) {
      state = STATES.includes(state) ? state : "idle";
      return Object.assign({ quantity: spec.quantity }, B.contour(t, state));
    }

    applyScale();
    update(0, "idle");
    return {
      group: root, role, body: { kind: spec.kind, quantity: spec.quantity, analysis: spec.analysis, caption: B.caption, parts: B.parts || null, style: B.style || null },
      counts: B.counts, field: B.field || null,
      // strength: the tension arrows in body coordinates (frame = the body group)
      arrows: B.arrows ? B.arrows.map((a) => Object.assign({ frame: B.group }, a)) : null,
      update, contour, setPixelScale, setOptions, dispose,
    };
  }

  window.HirakuPets = { ROLES, BODIES, STATES, FRAME, BANDS, STOPS, BAND_COLORS, LOAD, PROP_MASS, loadFactor, create };
})();

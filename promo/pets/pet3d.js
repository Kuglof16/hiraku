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
// aero, a crushed shell mesh for crash, a mode shape for NVH, and so on. The
// eyes, the antenna with the role colour, the legs, the arms and the prop are
// shared, so the ten pets stay one family.
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
  const BODIES = {
    "lead": { kind: "quad-sphere", quantity: "相対の応力 (自重)", analysis: "四角の要素の素直な球。自重と足の荷重の応力" },
    "designer": { kind: "cad-surface", quantity: "曲率 (相対)", analysis: "CAD のなめらかな面とアイソパラメトリックの線・寸法の線" },
    "packager": { kind: "assembly", quantity: "部品どうしの近さ (相対)", analysis: "箱の部品をボルトで組んだ組立。少し分解した隙間" },
    "cae.handling": { kind: "multibody", quantity: "ロールの振れ (相対)", analysis: "剛体 2 つをジョイントとばねでつないだマルチボディ。走った軌跡とヨーの矢印" },
    "cae.powertrain": { kind: "cell", quantity: "電流密度 (相対)", analysis: "円柱のセル。端子から流れる電流の線" },
    "cae.aero": { kind: "flow", quantity: "速さ (相対)", analysis: "流体でできたしずく形の体と流線 (CFD の速度)" },
    "cae.crash": { kind: "crushed-shell", quantity: "塑性ひずみ (相対)", analysis: "前が潰れて座屈した殻の要素のメッシュ" },
    "cae.strength": { kind: "stress-solid", quantity: "相対の応力", analysis: "三角の要素の角ばった塊。荷重から計算した応力" },
    "cae.nvh": { kind: "mode-shape", quantity: "変位 (相対)", analysis: "固有モードで揺れる網目と、変形前の形の薄い線" },
    "cae.thermal": { kind: "heat", quantity: "温度 (相対)", analysis: "下から温まる温度のコンターと等温線、立ちのぼる熱の線" },
  };

  const STATES = ["idle", "report", "pass", "mismatch", "working"];

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
  function feMesh(THREE, pts0, elems, o) {
    o = Object.assign({ smooth: false, center: null }, o || {});
    const center = o.center || new THREE.Vector3();
    const tris = [];
    for (const e of elems) {
      const n = e.n, vis = e.vis || [true, true, true, true];
      if (n.length === 3) tris.push({ n: [n[0], n[1], n[2]], hide: [!vis[1], !vis[2], !vis[0]] });
      else {
        tris.push({ n: [n[0], n[1], n[2]], hide: [!vis[1], true, !vis[0]] });
        tris.push({ n: [n[0], n[2], n[3]], hide: [!vis[2], !vis[3], true] });
      }
    }
    // outward winding, decided once on the reference shape
    const ab = new THREE.Vector3(), ac = new THREE.Vector3(), cr = new THREE.Vector3(), cen = new THREE.Vector3();
    for (const T of tris) {
      const a = pts0[T.n[0]], b = pts0[T.n[1]], c = pts0[T.n[2]];
      cr.crossVectors(ab.subVectors(b, a), ac.subVectors(c, a));
      cen.copy(a).add(b).add(c).multiplyScalar(1 / 3).sub(center);
      if (cr.dot(cen) < 0) {
        [T.n[1], T.n[2]] = [T.n[2], T.n[1]];
        [T.hide[1], T.hide[2]] = [T.hide[2], T.hide[1]];
      }
    }
    const nT = tris.length, nN = pts0.length;
    const pos = new Float32Array(nT * 9), nor = new Float32Array(nT * 9), bar = new Float32Array(nT * 9), val = new Float32Array(nT * 3);
    tris.forEach((T, i) => {
      for (let k = 0; k < 3; k++) for (let j = 0; j < 3; j++) bar[i * 9 + k * 3 + j] = j === k || T.hide[j] ? 1 : 0;
    });
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geom.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
    geom.setAttribute("aBary", new THREE.BufferAttribute(bar, 3));
    geom.setAttribute("aVal", new THREE.BufferAttribute(val, 1));
    const hp = new Float32Array(nN * 3);
    const hullGeom = new THREE.BufferGeometry();
    hullGeom.setAttribute("position", new THREE.BufferAttribute(hp, 3));
    hullGeom.setIndex(tris.flatMap((T) => T.n));

    const pts = pts0.map((p) => p.clone());
    const nn = pts0.map(() => new THREE.Vector3());
    const fn = tris.map(() => new THREE.Vector3());
    function setNodes(P) {
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
    setNodes(pts0);
    return { geom, hullGeom, pts, nn, tris, setNodes, setValues, surface, nodeCount: nN, elemCount: elems.length, edges: elemEdges(elems) };
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

  // lead: a plain, regular sphere of quad elements under internal pressure.
  // Membrane stress of a pressurised shell, sigma = p r / (2 t), with r the
  // distance from the centre; shown against an allowable value (max / 0.62),
  // so the settled body is one calm, nearly even band.
  function bodyLead(ctx) {
    const { THREE } = ctx;
    const nu = ctx.fine ? 16 : 12, nv = ctx.fine ? 10 : 8;
    const prof = Array.from({ length: nv + 1 }, (_, j) => -Math.PI / 2 + (Math.PI * j) / nv);
    const R = revolve(THREE, prof, nu, (lat, phi) => shapePoint(THREE, latDir(THREE, lat, phi)));
    const fe = feMesh(THREE, R.pts, R.elems);
    const group = new THREE.Group();
    addFe(ctx, group, fe, { line: 1, self: 0.5 });
    addDots(ctx, group, fe, ctx.fine ? 0.028 : 0.034);
    const rr = R.pts.map((p) => p.length());
    const allow = Math.max(...rr) / 0.62;
    const settled = rr.map((r) => r / allow);
    // mismatch: the whole body weight on the left leg root, as for strength
    const legL = ctx.roots.legL;
    const mismatch = R.pts.map((p, i) => {
      const q = p.distanceTo(legL) / LOAD.spread;
      return settled[i] + 0.9 / Math.pow(1 + q * q, 1.5);
    });
    const vals = (t, s) => (s === "mismatch" ? mismatch : settled.map((v) => v * loadFactor(t, s)));
    return {
      group, surface: (d) => fe.surface(d), top: R.pts[R.pts.length - 1].clone(),
      counts: { nodes: fe.nodeCount, elements: fe.elemCount },
      caption: `節点 ${fe.nodeCount} · 要素 ${fe.elemCount} (四角)`,
      update(t, s) { fe.setValues(vals(t, s)); },
      contour(t, s) {
        const load = loadFactor(t, s);
        return Object.assign(stats(vals(t, s)), {
          load, loadCase: s === "mismatch" ? "mismatch" : "settled",
          info: [s === "mismatch" ? "左足に体重が偏る (食い違い)" : `内圧 ${Math.round(load * 100)} %` + (s === "working" ? " (増分)" : ""), "許容値を 1.0 とした膜の応力"],
        });
      },
    };
  }

  // strength: the v2 body made boxy (a faceted block); stress from the loads.
  // Like a pre-processor picture, the load on the prop arm root is drawn as an
  // arrow and the leg supports as constraint triangles on the ground.
  function bodyStrength(ctx) {
    const { THREE, opts } = ctx;
    const detail = DETAIL[opts.mesh] ?? DETAIL.coarse;
    const ico = new THREE.IcosahedronGeometry(1, detail);
    // put one vertex of the icosahedron on +y so the antenna grows from a node
    ico.rotateX(-Math.atan2((1 + Math.sqrt(5)) / 2, 1));
    const w = weld(ico.attributes.position.array);
    ico.dispose();
    const edge = 1.0515 / (detail + 1);
    const rnd = mulberry32(ctx.seed);
    const blocky = (d) => {
      const q = superDir(d, 5);
      return shapePoint(THREE, q.multiplyScalar(0.93), 0);
    };
    const pts = [];
    for (const n of w.nodes) {
      const d = new THREE.Vector3(n[0], n[1], n[2]).normalize();
      if (d.y > 0.9999) {
        pts.push(blocky(d));
        continue;
      }
      const r = new THREE.Vector3(rnd() * 2 - 1, rnd() * 2 - 1, rnd() * 2 - 1);
      r.addScaledVector(d, -r.dot(d)).multiplyScalar(0.16 * edge);
      const radial = 1 + (rnd() - 0.5) * 0.03;
      pts.push(blocky(d.clone().add(r).normalize()).multiplyScalar(radial));
    }
    const fe = feMesh(THREE, pts, w.tris.map((t) => ({ n: t })));
    const group = new THREE.Group();
    addFe(ctx, group, fe, { line: 1, self: 0.5 });
    addDots(ctx, group, fe, opts.mesh === "fine" ? 0.028 : 0.034);
    const field = stressFields(pts, ctx.roots, ctx.propMass);
    const vals = (t, s) => (s === "mismatch" ? field.mismatch : field.settled.map((v) => v * loadFactor(t, s)));
    const top = pts.reduce((b, p) => (p.y > b.y ? p : b), pts[0]);
    // load arrow onto the prop arm root
    const red = new THREE.MeshBasicMaterial({ color: C.miss });
    const loadArrow = new THREE.Group();
    const at = ctx.roots.armR.clone();
    const from = at.clone().add(new THREE.Vector3(0.1, 0.62, 0.12));
    const dir = at.clone().sub(from).normalize();
    loadArrow.add(rodBetween(THREE, from, at.clone().addScaledVector(dir, -0.17), 0.028, red, 8));
    const cone = new THREE.Mesh(new THREE.ConeGeometry(0.075, 0.2, 12), red);
    cone.position.copy(at).addScaledVector(dir, -0.1);
    cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
    loadArrow.add(cone);
    group.add(loadArrow);
    // constraint triangles beside the feet
    const ground = new THREE.Group();
    const bcMat = new THREE.MeshBasicMaterial({ color: C.s[0] });
    for (const sx of [-1, 1]) {
      for (const dz of [-0.16, 0.36]) {
        const tri = new THREE.Mesh(new THREE.ConeGeometry(0.075, 0.13, 3), bcMat);
        tri.position.set(sx * 0.37, 0.065, 0.1 + dz);
        ground.add(tri);
      }
    }
    return {
      group, ground, surface: (d) => fe.surface(d), top: top.clone(),
      counts: { nodes: fe.nodeCount, elements: fe.elemCount },
      caption: `節点 ${fe.nodeCount} · 要素 ${fe.elemCount} · 矢印 = 荷重 · 三角 = 拘束`,
      field: { nodes: pts.map((p) => [p.x, p.y, p.z]), settled: field.settled, mismatch: field.mismatch, loads: field.loads, mismatchLoads: field.mismatchLoads },
      update(t, s) { fe.setValues(vals(t, s)); },
      contour(t, s) {
        const load = loadFactor(t, s);
        const lines = [s === "mismatch" ? "荷重: 体重が左足に偏る (食い違い)" : `荷重 ${Math.round(load * 100)} %` + (s === "working" ? " (増分)" : "")];
        return Object.assign(stats(vals(t, s)), { load, loadCase: s === "mismatch" ? "mismatch" : "settled", info: lines });
      },
    };
  }

  // aero: a teardrop of translucent fluid; surface streamlines run from the
  // nose to the tail and streamlines of the flow around a sphere pass by.
  // Speed on the surface: U sin(theta) (theta = angle between the surface
  // normal and the flow) times a blockage factor 1 + 0.5 (r / r_max)^2, so it
  // is 1.5 U at the widest section as on a sphere and recovers toward U on
  // the tail. Normalised by 1.5 U (the free stream is 0.67).
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

    // flow axis coordinate of a body point = body x
    const baseVals = R.pts.map((p) => speed(p.x));
    // separated wake behind the shoulder (mismatch): low, uneven speed
    const sepVals = (t) => R.pts.map((p, i) => (p.x < -0.05 ? 0.12 + 0.07 * (0.5 + 0.5 * Math.sin(3 * Math.atan2(p.y, -p.z) + 2.4 * t + 4 * p.x)) : baseVals[i]));

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
        vals.push(speed(z));
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
        vals.push(flowSpeed(rho, z));
      }
      paths.push({ pts, vals, wake: (x) => (x < -0.4 ? 1 : 0), s0: k * 0.53 });
    });
    const flowMat = flowMaterial(THREE, ctx.U, { speed: 0.7, dens: 2.4, duty: 0.55 });
    ctx.flowMats.push(flowMat);
    const lines = new THREE.Mesh(tubes(THREE, paths, 0.026, 5), flowMat);
    lines.frustumCulled = false;
    group.add(lines);

    const top = R.pts.reduce((b, p) => (p.y > b.y ? p : b), R.pts[0]);
    const spd = { idle: 0.7, report: 0.7, pass: 0.9, mismatch: 0.6, working: 2.0 };
    const vals = (t, s) => (s === "mismatch" ? sepVals(t) : baseVals);
    return {
      group, surface: (d) => fe.surface(d), top: top.clone(),
      counts: { nodes: fe.nodeCount, elements: fe.elemCount },
      caption: `流線 ${paths.length} 本 · 面の節点 ${fe.nodeCount}`,
      update(t, s) {
        fe.setValues(vals(t, s));
        flowMat.userData.flow.uSpeed.value = spd[s];
        flowMat.userData.flow.uSep.value = s === "mismatch" ? 1 : 0;
      },
      contour(t, s) {
        const info = s === "mismatch" ? ["流れ: 肩の後ろではく離 (食い違い)", "後ろでは流れが逆に回る"] : ["流れ: 前から一様 (ポテンシャル流れ)", s === "working" ? "流れの線が速い (解析中)" : "鼻先は止まり、肩でいちばん速い"];
        return Object.assign(stats(vals(t, s)), { info });
      },
    };
  }

  // crash: a rounded-box shell of quad elements whose front (the face side)
  // is crushed: shortened, folded in two lobes along the sides and crumpled
  // at the front plate. Plastic strain per node = 0.35 * membrane strain
  // (mean |l / l0 - 1| of the element edges at the node) + bending (change of
  // the normal angle to the neighbours per unit length, times a shell
  // thickness 0.25), normalised by the 90 % point of the full symmetric
  // crush. The fold hinges carry the largest values.
  function bodyCrash(ctx) {
    const { THREE } = ctx;
    const nu = ctx.fine ? 28 : 20, nv = ctx.fine ? 14 : 10;
    const prof = Array.from({ length: nv + 1 }, (_, j) => -Math.PI / 2 + (Math.PI * j) / nv);
    const R = revolve(THREE, prof, nu, (lat, phi) => {
      const q = superDir(latDir(THREE, lat, phi), 4);
      return new THREE.Vector3(q.x * 0.97, q.y * 0.92, q.z * 0.92);
    });
    const P0 = R.pts;
    const Z0 = 0.05, ZMAX = 0.92;
    const rnd = mulberry32(ctx.seed);
    const jag = P0.map(() => rnd());
    const deform = (c, asym) => P0.map((p, i) => {
      if (p.z <= Z0) return p.clone();
      const s = (p.z - Z0) / (ZMAX - Z0);
      const cr = 0.42 * c * (1 + asym * p.x * 0.9);
      const q = p.clone();
      q.z = Z0 + (p.z - Z0) * (1 - cr);
      // folds: two lobes along the crushed length, growing to the front
      const fold = 0.2 * c * Math.sin(2 * Math.PI * s) * Math.min(1, 2 * s);
      const rxy = Math.hypot(p.x, p.y) || 1;
      const side = Math.min(1, rxy / 0.6);
      q.x += (p.x / rxy) * side * (fold + 0.07 * c * s);
      q.y += (p.y / rxy) * side * (fold * 0.85 + 0.05 * c * s);
      // crumpled front plate (seeded pattern)
      if (s > 0.7) q.z -= c * ((s - 0.7) / 0.3) * (0.05 + 0.2 * jag[i]);
      return q;
    });
    const fe = feMesh(THREE, P0, R.elems);
    const edges = fe.edges.map(([i, j]) => [i, j, P0[i].distanceTo(P0[j])]);
    const tris = fe.tris.map((T) => T.n);
    const normals = (P) => {
      const n = P.map(() => new THREE.Vector3());
      const a = new THREE.Vector3(), b = new THREE.Vector3(), f = new THREE.Vector3();
      for (const [i, j, k] of tris) {
        f.crossVectors(a.subVectors(P[j], P[i]), b.subVectors(P[k], P[i]));
        n[i].add(f); n[j].add(f); n[k].add(f);
      }
      return n.map((v) => v.normalize());
    };
    const N0 = normals(P0);
    const strain = (P) => {
      const N = normals(P);
      const acc = new Float64Array(P.length), cnt = new Float64Array(P.length);
      for (const [i, j, l0] of edges) {
        const mem = Math.abs(P[i].distanceTo(P[j]) / l0 - 1);
        const bend = (Math.abs(N[i].angleTo(N[j]) - N0[i].angleTo(N0[j])) / l0) * 0.25;
        const e = 0.35 * mem + bend;
        acc[i] += e; acc[j] += e; cnt[i]++; cnt[j]++;
      }
      return Array.from(acc, (v, i) => v / Math.max(1, cnt[i]));
    };
    // 1.0 = the 90 % point of the full symmetric crush (the top tenth is red)
    const full = strain(deform(1, 0)).slice().sort((a, b) => a - b);
    const ref = full[Math.floor(full.length * 0.9)];
    const group = new THREE.Group();
    addFe(ctx, group, fe, { line: 1, self: 0.5 });
    const crush = (t, s) => (s === "working" ? ramp(t) : 1);
    let last = null;
    const solve = (t, s) => {
      const key = s === "working" ? crush(t, s).toFixed(5) : s;
      if (last && last.key === key) return last;
      const P = deform(crush(t, s), s === "mismatch" ? 1 : 0);
      last = { key, P, v: strain(P).map((v) => v / ref) };
      return last;
    };
    const body = {
      group, deforms: true, top: P0[P0.length - 1].clone(),
      surface: (d) => fe.surface(d),
      counts: { nodes: fe.nodeCount, elements: fe.elemCount },
      caption: `節点 ${fe.nodeCount} · 殻の要素 ${fe.elemCount}`,
      update(t, s) {
        const r = solve(t, s);
        fe.setNodes(r.P);
        fe.setValues(r.v);
      },
      contour(t, s) {
        const c = crush(t, s);
        const info = [s === "mismatch" ? "潰れ方: 右に偏る (食い違い)" : `潰れ ${Math.round(c * 100)} %` + (s === "working" ? " (進行中)" : ""), "ひずみ = 伸び縮み + 折れ曲がり"];
        return Object.assign(stats(solve(t, s).v), { info });
      },
    };
    return body;
  }

  // nvh: an eigenmode. The mesh moves with an exaggerated mode shape
  // w = A Y(n) cos(omega t) along the normal; the undeformed mesh stays as
  // faint lines. Displacement = |w| / A_max.
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
      if (s === "working") return { mode: "a", amp: 0.05 + 0.12 * ramp(t), f: 1.25 };
      if (s === "mismatch") return { mode: "b", amp: 0.11, f: 0.8 };
      return { mode: "a", amp: s === "pass" ? 0.11 : 0.1, f: 0.62 };
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
    addFe(ctx, group, fe, { line: 0.6, self: 0.5, opacity: 0.86 });
    // undeformed shape: faint lines
    const lp = [];
    for (const [i, j] of fe.edges) lp.push(P0[i].x, P0[i].y, P0[i].z, P0[j].x, P0[j].y, P0[j].z);
    const lg = new THREE.BufferGeometry();
    lg.setAttribute("position", new THREE.Float32BufferAttribute(lp, 3));
    const ghostMat = new THREE.LineBasicMaterial({ color: C.ink, transparent: true, opacity: 0.55, depthWrite: false });
    ctx.inkMats.push({ mat: ghostMat, paper: C.ink, night: C.onNightSoft });
    const ghost = new THREE.LineSegments(lg, ghostMat);
    ghost.scale.setScalar(1.004);
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

  // powertrain: a cylindrical cell. Current enters at the top terminal,
  // spreads over the cap, runs down the wall and leaves at the bottom.
  // Current density on a cap ~ I / (2 pi r) (capped at the terminal radius),
  // on the wall I / (2 pi R).
  function bodyPowertrain(ctx) {
    const { THREE } = ctx;
    const RC = 0.86, HH = 0.86, F = 0.2, RT = 0.22, DZ = 0.93;
    const prof = [];
    const capN = 3, filN = 3, sideN = ctx.fine ? 7 : 5;
    prof.push({ r: 0, y: -HH });
    for (let k = 1; k <= capN; k++) prof.push({ r: ((RC - F) * k) / capN, y: -HH });
    for (let k = 1; k <= filN; k++) { const a = -Math.PI / 2 + (Math.PI / 2) * (k / filN); prof.push({ r: RC - F + F * Math.cos(a), y: -HH + F + F * Math.sin(a) }); }
    for (let k = 1; k < sideN; k++) prof.push({ r: RC, y: -HH + F + ((2 * HH - 2 * F) * k) / sideN });
    for (let k = 0; k < filN; k++) { const a = (Math.PI / 2) * (k / filN); prof.push({ r: RC - F + F * Math.cos(a), y: HH - F + F * Math.sin(a) }); }
    for (let k = 0; k <= capN; k++) prof.push({ r: (RC - F) * (1 - k / capN), y: HH });
    const nu = ctx.fine ? 24 : 16;
    const map = (p, phi) => new THREE.Vector3(p.r * Math.sin(phi), p.y, p.r * Math.cos(phi) * DZ);
    const R = revolve(THREE, prof, nu, map);
    const fe = feMesh(THREE, R.pts, R.elems, { smooth: true });
    const group = new THREE.Group();
    addFe(ctx, group, fe, { line: 0.55, self: 0.5, rough: 0.38, metal: 0.1 });
    // + terminal on top
    const term = new THREE.Mesh(new THREE.CylinderGeometry(0.17, 0.19, 0.1, 20), ctx.M.metal);
    term.position.y = HH + 0.05;
    term.castShadow = true;
    group.add(term);
    addHull(THREE, term, 0.022, ctx.hulls);
    const PHI0 = Math.PI / 2;
    const density = (p, I, mis) => {
      const r = Math.hypot(p.x, p.z / DZ);
      let J = Math.abs(p.y) > HH - F * 0.6 ? RT / Math.max(r, RT) : RT / RC;
      if (mis && Math.abs(p.y) <= HH - F * 0.6) J *= 1 + 2.6 * Math.pow(Math.max(0, Math.cos(Math.atan2(p.x, p.z) - PHI0)), 4);
      return J * I;
    };
    const current = (t, s) => (s === "working" ? 0.35 + 0.65 * ramp(t) : s === "pass" ? 0.85 : 0.8);
    const vals = (t, s) => R.pts.map((p) => density(p, current(t, s), s === "mismatch"));
    // current lines: from the terminal over the cap, down the wall, out below
    const paths = [];
    [45, 135, 225, 315, 90, 270].forEach((deg, k) => {
      const phi = (deg * Math.PI) / 180;
      const pts = [], v = [];
      for (let j = prof.length - 2; j >= 1; j--) {
        const p = prof[j], q0 = prof[j + 1], q1 = prof[j - 1];
        const tr = q1.r - q0.r, ty = q1.y - q0.y, tl = Math.hypot(tr, ty) || 1;
        // outward normal of the profile (walking top -> bottom)
        const nr = -ty / tl, ny = tr / tl;
        const pp = map({ r: p.r + 0.03 * nr, y: p.y + 0.03 * ny }, phi);
        pts.push(pp);
        v.push(density(map(p, phi), 1, false));
      }
      paths.push({ pts, vals: v, s0: k * 0.29 });
    });
    const flowMat = flowMaterial(THREE, ctx.U, { speed: 0.9, dens: 3.0, duty: 0.5 });
    ctx.flowMats.push(flowMat);
    const lines = new THREE.Mesh(tubes(THREE, paths, 0.022, 5), flowMat);
    lines.frustumCulled = false;
    group.add(lines);
    return {
      group, surface: (d) => fe.surface(d), top: new THREE.Vector3(0, HH + 0.1, 0),
      counts: { nodes: fe.nodeCount, elements: fe.elemCount },
      caption: `節点 ${fe.nodeCount} · 要素 ${fe.elemCount} · 電流の線 ${paths.length} 本`,
      update(t, s) {
        fe.setValues(vals(t, s));
        const f = flowMat.userData.flow;
        const I = current(t, s);
        f.uSpeed.value = 1.6 * I;
        f.uGain.value = I;
      },
      contour(t, s) {
        const I = current(t, s);
        const info = [`電流 ${Math.round(I * 100)} %` + (s === "working" ? " (上げている)" : ""), s === "mismatch" ? "右の壁に電流が集まる (食い違い)" : "端子のまわりで濃く、壁は一様"];
        return Object.assign(stats(vals(t, s)), { info });
      },
    };
  }

  // designer: a CAD surface (superellipsoid) with isoparametric lines and
  // dimension lines; no mesh. Colour = curvature from the surface normals,
  // kappa = mean |n_i - n_j| / |p_i - p_j| over neighbouring samples.
  function bodyDesigner(ctx) {
    const { THREE } = ctx;
    const nu = 48, nv = 24;
    const prof = Array.from({ length: nv + 1 }, (_, j) => -Math.PI / 2 + (Math.PI * j) / nv);
    const HALF = new THREE.Vector3(0.98, 0.92, 0.86);
    const shapeAt = (n) => (lat, phi) => {
      const q = superDir(latDir(THREE, lat, phi), n);
      return new THREE.Vector3(q.x * HALF.x, q.y * HALF.y, q.z * HALF.z);
    };
    const R = revolve(THREE, prof, nu, shapeAt(3), (i) => i % 6 === 0, (j) => j % 4 === 0);
    const fe = feMesh(THREE, R.pts, R.elems, { smooth: true });
    const group = new THREE.Group();
    addFe(ctx, group, fe, { line: 0.9, always: true, self: 0.32, rough: 0.3, metal: 0.05, lineColor: 0x2a3a52 });
    const nb = R.pts.map(() => []);
    for (const [i, j] of fe.edges) { nb[i].push(j); nb[j].push(i); }
    const curvature = () => fe.pts.map((p, i) => {
      let k = 0;
      for (const j of nb[i]) k += fe.nn[i].distanceTo(fe.nn[j]) / Math.max(1e-4, p.distanceTo(fe.pts[j]));
      return k / nb[i].length;
    });
    const kRef = Math.max(...curvature()) * 0.85;
    const expo = (t, s) => (s === "working" ? 3 + 0.9 * Math.sin((TAU * t) / 4.4) : 3);
    let last = null;
    const solve = (t, s) => {
      const n = expo(t, s);
      if (last && last.n === n) return last;
      const P = R.par.map(([i, j]) => shapeAt(n)(prof[j], (TAU * i) / nu));
      fe.setNodes(P);
      last = { n, v: curvature().map((k) => k / kRef) };
      return last;
    };
    // dimension lines (ink): height on the left, width in front at the feet
    const dimMat = new THREE.MeshBasicMaterial({ color: C.ink });
    const missMat = new THREE.MeshBasicMaterial({ color: C.miss });
    ctx.inkMats.push({ mat: dimMat, paper: C.ink, night: C.onNightSoft });
    const dims = new THREE.Group();
    group.add(dims);
    const r = 0.012;
    const arrow = (g, tip, dir, mat) => {
      const c = new THREE.Mesh(new THREE.ConeGeometry(0.038, 0.11, 10), mat);
      c.position.copy(tip).addScaledVector(dir, -0.055);
      c.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
      g.add(c);
    };
    const dimension = (p0, p1, ext0, ext1, mat) => {
      const g = new THREE.Group();
      const d = p1.clone().sub(p0).normalize();
      g.add(rodBetween(THREE, p0, p1, r, mat, 6));
      arrow(g, p0, d.clone().negate(), mat);
      arrow(g, p1, d, mat);
      g.add(rodBetween(THREE, ext0[0], ext0[1], r * 0.8, mat, 6));
      g.add(rodBetween(THREE, ext1[0], ext1[1], r * 0.8, mat, 6));
      // value plate
      const mid = p0.clone().add(p1).multiplyScalar(0.5);
      const plate = new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.09, 0.012), mat);
      plate.position.copy(mid);
      g.add(plate);
      return g;
    };
    const X = -1.32, ZD = -0.1;
    const hDim = dimension(
      new THREE.Vector3(X, -HALF.y, ZD), new THREE.Vector3(X, HALF.y, ZD),
      [new THREE.Vector3(-0.2, -HALF.y, ZD), new THREE.Vector3(X - 0.1, -HALF.y, ZD)],
      [new THREE.Vector3(-0.2, HALF.y, ZD), new THREE.Vector3(X - 0.1, HALF.y, ZD)], dimMat
    );
    hDim.children.forEach((c) => { if (c.geometry.type === "BoxGeometry") c.rotation.z = Math.PI / 2; });
    dims.add(hDim);
    const YW = -1.06, ZW = 0.95;
    dims.add(dimension(
      new THREE.Vector3(-HALF.x, YW, ZW), new THREE.Vector3(HALF.x, YW, ZW),
      [new THREE.Vector3(-HALF.x, -0.45, ZW * 0.55), new THREE.Vector3(-HALF.x, YW - 0.08, ZW)],
      [new THREE.Vector3(HALF.x, -0.45, ZW * 0.55), new THREE.Vector3(HALF.x, YW - 0.08, ZW)], dimMat
    ));
    // mismatch: the height dimension does not match the shape
    const hMis = hDim.clone();
    hMis.traverse((o) => { if (o.isMesh) o.material = missMat; });
    hMis.position.y = 0.16;
    dims.add(hMis);
    return {
      group, deforms: true, top: new THREE.Vector3(0, HALF.y, 0),
      surface: (d) => fe.surface(d),
      counts: { nodes: fe.nodeCount, elements: 1 },
      caption: "なめらかな面 1 枚 · パラメータの線 u 8 · v 6",
      update(t, s) {
        fe.setValues(solve(t, s).v);
        hDim.visible = s !== "mismatch";
        hMis.visible = s === "mismatch";
      },
      contour(t, s) {
        const n = expo(t, s);
        const info = [`形のパラメータ n = ${n.toFixed(2)}` + (s === "working" ? " (動かしている)" : ""), s === "mismatch" ? "高さの寸法が形と合わない (食い違い)" : "角の丸みで曲率が大きい"];
        return Object.assign(stats(solve(t, s).v), { info });
      },
    };
  }

  // packager: an assembly of boxes held by bolts, slightly exploded.
  // Colour = closeness to the nearest other part, max(0, 1 - d / 0.5) (d = distance
  // to that part's box; inside another part counts as touching or more).
  function bodyPackager(ctx) {
    const { THREE } = ctx;
    const f = ctx.fine ? 2 : 1;
    const parts = [
      { name: "base", h: new THREE.Vector3(0.95, 0.24, 0.85), n: [6 * f, 2 * f, 5 * f] },
      { name: "main", h: new THREE.Vector3(0.86, 0.4, 0.8), n: [6 * f, 4 * f, 5 * f] },
      { name: "lid", h: new THREE.Vector3(0.76, 0.2, 0.7), n: [5 * f, 2 * f, 4 * f] },
    ];
    const group = new THREE.Group();
    let nodes = 0, elems = 0;
    for (const p of parts) {
      const B = boxGrid(THREE, [p.h.x, p.h.y, p.h.z], p.n);
      p.fe = feMesh(THREE, B.pts, B.elems);
      p.g = new THREE.Group();
      group.add(p.g);
      addFe(ctx, p.g, p.fe, { line: 1, self: 0.5 }, 0.03);
      nodes += p.fe.nodeCount;
      elems += p.fe.elemCount;
    }
    const [base, main, lid] = parts;
    base.g.position.y = -0.93 + base.h.y;
    // bolts through the corners, hex heads on the lid
    const corners = [[0.58, 0.52], [-0.58, 0.52], [0.58, -0.52], [-0.58, -0.52]];
    const shafts = corners.map(([x, z]) => {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 1, 6), ctx.M.metal);
      m.position.set(x, 0, z);
      m.castShadow = true;
      group.add(m);
      return m;
    });
    const heads = corners.map(([x, z]) => {
      const m = new THREE.Mesh(new THREE.CylinderGeometry(0.085, 0.085, 0.07, 6), ctx.M.metal);
      m.position.set(x, lid.h.y + 0.035, z);
      m.castShadow = true;
      lid.g.add(m);
      addHull(THREE, m, 0.014, ctx.hulls);
      return m;
    });
    const gapOf = (t, s) => (s === "working" ? 0.08 + 0.3 * (0.5 - 0.5 * Math.cos((TAU * t) / 4.4)) : 0.08);
    const place = (t, s) => {
      const g = gapOf(t, s);
      const baseTop = base.g.position.y + base.h.y;
      main.g.position.y = baseTop + g + main.h.y;
      lid.g.position.set(0, main.g.position.y + main.h.y + g + lid.h.y, 0);
      lid.g.rotation.z = 0;
      if (s === "mismatch") {
        lid.g.position.y -= 0.17;
        lid.g.position.x = 0.1;
        lid.g.rotation.z = -0.07;
      }
      group.updateMatrixWorld(true);
    };
    const box3 = (p) => new THREE.Box3().setFromCenterAndSize(new THREE.Vector3(), p.h.clone().multiplyScalar(2)).applyMatrix4(p.g.matrix);
    const v = new THREE.Vector3();
    const closeness = () => {
      const boxes = parts.map(box3);
      return parts.map((p, pi) => p.fe.pts.map((q) => {
        v.copy(q).applyMatrix4(p.g.matrix);
        let best = Infinity;
        boxes.forEach((b, bi) => {
          if (bi === pi) return;
          const d = b.distanceToPoint(v);
          if (d > 0) best = Math.min(best, d);
          else {
            const pen = Math.min(v.x - b.min.x, b.max.x - v.x, v.y - b.min.y, b.max.y - v.y, v.z - b.min.z, b.max.z - v.z);
            best = Math.min(best, -pen);
          }
        });
        return Math.max(0, 1 - best / 0.5);
      }));
    };
    const solve = (t, s) => {
      place(t, s);
      return closeness();
    };
    const updateBolts = () => {
      const y0 = base.g.position.y - base.h.y + 0.05;
      const y1 = lid.g.position.y + lid.h.y;
      shafts.forEach((m) => { m.position.y = (y0 + y1) / 2; m.scale.y = y1 - y0; });
    };
    solve(0, "idle");
    return {
      group, anchor: main.g, antennaAnchor: lid.g, top: new THREE.Vector3(0, lid.h.y, 0),
      surface: (d) => main.fe.surface(d),
      counts: { nodes, elements: elems },
      caption: `部品 3 · ボルト 4 · 要素 ${elems}`,
      update(t, s) {
        const vals = solve(t, s);
        parts.forEach((p, i) => p.fe.setValues(vals[i]));
        updateBolts();
      },
      contour(t, s) {
        const all = solve(t, s).flat();
        const info = [`隙間 ${gapOf(t, s).toFixed(2)}` + (s === "working" ? " (分解と組立)" : ""), s === "mismatch" ? "ふたが本体に食い込む (食い違い)" : "1.0 = 部品が触れている"];
        return Object.assign(stats(all), { info });
      },
    };
  }

  // handling: a multibody model. Two rigid bodies (chassis and cabin) joined
  // by a ball joint and two springs; the cabin rolls, the chassis yaws.
  // Colour = roll / yaw swing, distance from the rotation axis * angle,
  // normalised by the largest swing of the working (slalom) case. On the
  // ground: the track driven (colour = lateral acceleration v^2 * curvature)
  // and a yaw arrow.
  function bodyHandling(ctx) {
    const { THREE } = ctx;
    const nu = ctx.fine ? 24 : 16, nv = ctx.fine ? 12 : 8;
    const prof = Array.from({ length: nv + 1 }, (_, j) => -Math.PI / 2 + (Math.PI * j) / nv);
    const rounded = (n, h) => (lat, phi) => {
      const q = superDir(latDir(THREE, lat, phi), n);
      return new THREE.Vector3(q.x * h[0], q.y * h[1], q.z * h[2]);
    };
    const YJ = -0.29;
    const chH = [1.0, 0.25, 0.9], cbH = [0.86, 0.55, 0.78];
    const chR = revolve(THREE, prof, nu, rounded(6, chH));
    const cbR = revolve(THREE, prof, nu, rounded(4, cbH));
    const chFe = feMesh(THREE, chR.pts, chR.elems, { smooth: true });
    const cbFe = feMesh(THREE, cbR.pts, cbR.elems, { smooth: true });
    const group = new THREE.Group();
    const rig = new THREE.Group();          // yaws
    group.add(rig);
    const chassis = new THREE.Group();
    chassis.position.y = -0.66;
    rig.add(chassis);
    addFe(ctx, chassis, chFe, { line: 0.0, self: 0.5, rough: 0.4 }, 0.035);
    const pivot = new THREE.Group();        // rolls about the joint
    pivot.position.y = YJ;
    rig.add(pivot);
    const cabin = new THREE.Group();
    cabin.position.y = 0.38 - YJ;
    pivot.add(cabin);
    addFe(ctx, cabin, cbFe, { line: 0.0, self: 0.5, rough: 0.4 }, 0.035);
    // ball joint
    const ball = new THREE.Mesh(new THREE.SphereGeometry(0.11, 16, 12), ctx.M.metal);
    ball.position.y = YJ;
    rig.add(ball);
    addHull(THREE, ball, 0.018, ctx.hulls);
    // springs (helix tubes), stretched each frame
    const helix = [];
    for (let i = 0; i <= 64; i++) {
      const a = (i / 64) * TAU * 4;
      helix.push(new THREE.Vector3(0.075 * Math.cos(a), i / 64, 0.075 * Math.sin(a)));
    }
    const springGeom = new THREE.TubeGeometry(new THREE.CatmullRomCurve3(helix), 128, 0.016, 5, false);
    const springs = [-0.68, 0.68].map((x) => {
      const m = new THREE.Mesh(springGeom, ctx.M.ink);
      m.position.set(x, -0.41, 0);
      rig.add(m);
      return { m, x };
    });
    // ground: track and yaw arrow (in the pet's root, not leaning with it)
    const ground = new THREE.Group();
    const track = [], tv = [];
    for (let z = -2.6; z <= 0.21; z += 0.1) {
      const k = 1.5;
      track.push(new THREE.Vector3(0.5 * Math.sin(k * z), 0.016, z));
      tv.push(Math.abs(0.5 * k * k * Math.sin(k * z)) / (0.5 * k * k));
    }
    const trackMat = flowMaterial(THREE, ctx.U, { speed: 0.8, dens: 2.0, duty: 0.6 });
    ctx.flowMats.push(trackMat);
    const trackMesh = new THREE.Mesh(tubes(THREE, [{ pts: track, vals: tv }], 0.03, 5), trackMat);
    trackMesh.frustumCulled = false;
    ground.add(trackMesh);
    const yawG = new THREE.Group();
    yawG.position.y = 0.07;
    ground.add(yawG);
    const arrowMat = new THREE.MeshBasicMaterial({ color: C.ink });
    ctx.inkMats.push({ mat: arrowMat, paper: C.ink, night: C.onNightSoft });
    const arcLen = 4.0, RA = 1.18;
    const arc = new THREE.Mesh(new THREE.TorusGeometry(RA, 0.024, 6, 64, arcLen), arrowMat);
    arc.rotation.x = Math.PI / 2;
    yawG.add(arc);
    const head = new THREE.Mesh(new THREE.ConeGeometry(0.075, 0.2, 12), arrowMat);
    // torus arc in its xy plane, rotated onto the ground: angle a -> (cos a, 0, sin a)
    head.position.set(RA * Math.cos(arcLen), 0, RA * Math.sin(arcLen));
    head.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), new THREE.Vector3(-Math.sin(arcLen), 0, Math.cos(arcLen)));
    yawG.add(head);

    const AMP = {
      idle: { roll: 0.05, yaw: 0.03, P: 1.8, rate: 0.35 },
      report: { roll: 0.05, yaw: 0.03, P: 1.8, rate: 0.35 },
      pass: { roll: 0.035, yaw: 0.02, P: 1.8, rate: 0.35 },
      mismatch: { roll: 0.13, yaw: 0.06, P: 1.6, rate: -0.5 },
      working: { roll: 0.13, yaw: 0.06, P: 1.1, rate: 1.1 },
    };
    const W = AMP.working;
    const rCab = cbFe.pts.map((p) => Math.hypot(p.x, p.y + cabin.position.y));
    const rCh = chFe.pts.map((p) => Math.hypot(p.x, p.z));
    const ref = Math.max(...rCab) * W.roll;
    const vals = (s) => {
      const A = AMP[s];
      return [chFe.pts.map((p, i) => (rCh[i] * A.yaw) / ref), cbFe.pts.map((p, i) => (rCab[i] * A.roll) / ref)];
    };
    return {
      group, ground, anchor: cabin, antennaAnchor: cabin, top: new THREE.Vector3(0, cbH[1], 0),
      surface: (d) => cbFe.surface(d),
      shoulders: { R: new THREE.Vector3(0.84, -0.02, 0.24), L: new THREE.Vector3(-0.84, -0.04, 0.2) },
      counts: { nodes: chFe.nodeCount + cbFe.nodeCount, elements: chFe.elemCount + cbFe.elemCount },
      caption: "剛体 2 · 球ジョイント 1 · ばね 2",
      update(t, s) {
        const A = AMP[s];
        const ph = (TAU * t) / A.P;
        const roll = A.roll * Math.sin(ph) * (s === "mismatch" ? 1 + 0.4 * Math.sin(ph * 0.5) : 1);
        pivot.rotation.z = roll;
        pivot.rotation.x = s === "mismatch" ? 0.05 * Math.sin(ph + 1) : 0;
        rig.rotation.y = A.yaw * Math.sin(ph + Math.PI / 2);
        for (const sp of springs) {
          const yTop = YJ + sp.x * Math.sin(roll) + (-0.17 - YJ) * Math.cos(roll);
          sp.m.scale.y = yTop - -0.41;
        }
        yawG.rotation.y = -A.rate * t;
        const [a, b] = vals(s);
        chFe.setValues(a);
        cbFe.setValues(b);
        trackMat.userData.flow.uSpeed.value = s === "working" ? 1.8 : 0.8;
      },
      contour(t, s) {
        const [a, b] = vals(s);
        const info = [s === "working" ? "スラローム (解析中): ロールが大きい" : s === "mismatch" ? "ロールが予測より大きく、向きも逆 (食い違い)" : "まっすぐ走る: ロールは小さい", "地面の線 = 走った軌跡 (色 = 横加速度)"];
        return Object.assign(stats(a.concat(b)), { info });
      },
    };
  }

  const BUILDERS = {
    "quad-sphere": bodyLead,
    "cad-surface": bodyDesigner,
    "assembly": bodyPackager,
    "multibody": bodyHandling,
    "cell": bodyPowertrain,
    "flow": bodyAero,
    "crushed-shell": bodyCrash,
    "stress-solid": bodyStrength,
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
    const hop = new THREE.Group();
    root.add(hop);

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
    if (B.ground) root.add(B.ground);
    for (const d of ctx.dots) d.visible = !!opts.nodes;
    const anchor = B.anchor || B.group;
    const antAnchor = B.antennaAnchor || anchor;
    if (B.shoulders) {
      shoulderR = B.shoulders.R;
      shoulderL = B.shoulders.L;
    }

    // legs and feet (stay on the ground)
    const legs = new THREE.Group();
    hop.add(legs);
    for (const sx of [-1, 1]) {
      const top = legTop(sx);
      const bot = new THREE.Vector3(sx * 0.36, 0.08, 0.06);
      const leg = capsuleBetween(THREE, bot, top, 0.075, M.limb, 2, 6);
      leg.castShadow = true;
      legs.add(leg);
      const foot = new THREE.Mesh(new THREE.SphereGeometry(1, 8, 5), M.limb);
      foot.scale.set(0.14, 0.075, 0.19);
      foot.position.set(sx * 0.37, 0.075, 0.1);
      foot.castShadow = true;
      legs.add(foot);
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

    // arm on +x (the prop hand) holding the prop low, and a short arm on -x
    const armR = capsuleBetween(THREE, shoulderR, handR, 0.06, M.limb, 2, 6);
    armR.castShadow = true;
    core.add(armR);
    const armL = capsuleBetween(THREE, shoulderL, handL, 0.06, M.limb, 2, 6);
    armL.castShadow = true;
    core.add(armL);
    for (const p of [handR, handL]) {
      const hand = new THREE.Mesh(new THREE.IcosahedronGeometry(0.088, 1), M.limb);
      hand.position.copy(p);
      core.add(hand);
    }
    const prop = buildProp(THREE, role, M, hulls, live);
    prop.position.copy(handR).add(new THREE.Vector3(0.04, -0.02, 0.06));
    prop.scale.multiplyScalar(1.25);
    core.add(prop);

    // marks above the head, clear of the antenna tip (top of the tip is at
    // about BODY_Y + 1.73; the badge radius is 0.24 after scaling)
    // billboarded in update when a camera is given
    const MARK_Y = BODY_Y + 2.1;
    const marks = new THREE.Group();
    marks.position.set(0.3, MARK_Y, 0.1);
    root.add(marks);
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
    root.add(blob);

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

    function update(t, state, camera) {
      state = STATES.includes(state) ? state : "idle";
      U.uT.value = t;
      B.update(t, state);
      if (B.deforms || B.anchor) placeFace();

      // idle base motion: bob, breathe, antenna sway, blink
      const bob = 0.025 * Math.sin((TAU * t) / 2.6);
      const breathe = Math.sin((TAU * t) / 2.6 + 0.6);
      let hopY = 0, lean = 0, tilt = 0, squash = 0;
      if (state === "pass") {
        const ph = (t / 0.9) % 1;
        hopY = 0.24 * Math.sin(Math.PI * ph);
        squash = Math.max(0, 1 - Math.min(ph, 1 - ph) / 0.12) * 0.07;
      } else if (state === "report") {
        lean = 0.2 + 0.025 * Math.sin((TAU * t) / 1.8);
      } else if (state === "mismatch") {
        tilt = 0.24 + 0.03 * Math.sin((TAU * t) / 2.2);
      }
      hop.position.y = hopY;
      torso.position.y = PIVOT_Y + (state === "pass" ? 0 : bob);
      torso.rotation.set(lean, 0, tilt);
      const sy = 1 + 0.018 * breathe - squash;
      const sxz = 1 - 0.012 * breathe + squash * 0.6;
      torso.scale.set(sxz, sy, sxz);

      antenna.rotation.z = 0.07 * Math.sin((TAU * t) / 1.9 + 0.4) - tilt * 0.3 - (state === "pass" ? 0.12 * Math.sin(Math.PI * ((t / 0.9) % 1)) : 0);
      antenna.rotation.x = 0.04 * Math.sin((TAU * t) / 2.7) - lean * 0.4;

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
        root.updateWorldMatrix(true, false);
        marks.parent.getWorldQuaternion(qTmp).invert();
        marks.quaternion.copy(qTmp.multiply(camera.quaternion));
        if (state === "mismatch") marks.rotateZ(0.12 * Math.sin((TAU * t) / 2.2));
      }

      const s = 1.55 * (1 - hopY * 1.1);
      blob.scale.set(s * 1.25, s, 1);

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
      group: root, role, body: { kind: spec.kind, quantity: spec.quantity, analysis: spec.analysis, caption: B.caption },
      counts: B.counts, field: B.field || null, update, contour, setPixelScale, setOptions, dispose,
    };
  }

  window.HirakuPets = { ROLES, BODIES, STATES, FRAME, BANDS, STOPS, BAND_COLORS, LOAD, PROP_MASS, loadFactor, create };
})();

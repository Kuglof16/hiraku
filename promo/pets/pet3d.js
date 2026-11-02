// 3D agent pets for HIRAKU, built with three.js.
//
// A plain script (no ES module imports) so it can be loaded from file:// with
// <script src>. The caller passes the THREE namespace in:
//
//   const pet = window.HirakuPets.create(THREE, { role: "cae.crash" });
//   scene.add(pet.group);
//   pet.update(t, "idle", camera);   // pose depends only on t and the state
//   pet.contour(t, "working");       // { load, loadCase, max, min } for a legend
//
// Body: a low-poly finite-element style mesh. An icosphere (coarse: 1
// subdivision, 42 nodes / 80 elements; fine: 2 subdivisions, 92 / 180) is
// welded into nodes and triangle elements, every node except the top one is
// moved a little with a seeded random generator (same seed -> same body every
// time), and every element is shaded with its own face normal (flat facets).
//
// Contour: a nodal "relative stress" computed from the loads on the pet
// (body weight carried by the two leg roots, the prop weight as a moment at
// the right arm root, the left arm's own weight, and a self-weight membrane
// term that is zero at the top). Each load is applied at the node nearest its
// root and decays with distance. The values are normalised so that the
// largest nodal value of the settled load case is 1.0. The fragment shader
// interpolates the nodal value linearly inside each triangle and cuts it into
// BANDS steps, so band borders (iso-lines) run across the elements, as in the
// banded contour plots of a CAE post-processor. This is a toy model to place
// the colours by where the forces act; it is not a solved field.
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

  const STATES = ["idle", "report", "pass", "mismatch", "working"];

  // icosphere subdivision per mesh level (three.js IcosahedronGeometry detail)
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

  // Body surface point for a unit direction (slightly egg shaped, wider low).
  function shapePoint(THREE, d) {
    const k = 1 + SHAPE.egg * -d.y;
    return new THREE.Vector3(d.x * SHAPE.sx * k, d.y * SHAPE.sy, d.z * SHAPE.sz * k);
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
    for (const k of Object.keys(roots)) at[k] = nearest(roots[k]);
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

  function bodyMaterial(THREE, U) {
    const m = new THREE.MeshStandardMaterial({ roughness: 0.62, metalness: 0.0 });
    m.onBeforeCompile = (sh) => {
      Object.assign(sh.uniforms, U);
      // nodal value per vertex; the varying is interpolated linearly inside
      // each triangle, then cut into BANDS steps per fragment
      sh.vertexShader =
        "attribute float aSettled;\nattribute float aMismatch;\nattribute vec3 aBary;\n" +
        "uniform float uLoad;\nuniform float uMis;\nvarying float vStress;\nvarying vec3 vBary;\n" +
        sh.vertexShader.replace(
          "#include <begin_vertex>",
          "#include <begin_vertex>\n  vStress = mix(uLoad * aSettled, aMismatch, uMis);\n  vBary = aBary;"
        );
      sh.fragmentShader =
        [
          `uniform vec3 uPal[${BANDS}];`,
          "uniform vec3 uInk;",
          "uniform vec3 uRimColor;",
          "uniform float uRim;",
          "uniform float uSelf;",
          "uniform float uLinePx;",
          "uniform float uEdges;",
          "varying float vStress;",
          "varying vec3 vBary;",
        ].join("\n") + "\n" +
        sh.fragmentShader
          .replace(
            "#include <color_fragment>",
            `#include <color_fragment>
  float hkB = clamp(vStress, 0.0, 0.99999) * ${BANDS}.0;
  int hkI = int(floor(hkB));
  int hkJ = min(hkI + 1, ${BANDS - 1});
  float hkW = fwidth(hkB);
  float hkT = smoothstep(1.0 - hkW, 1.0, fract(hkB));
  diffuseColor.rgb = mix(uPal[hkI], uPal[hkJ], hkI < ${BANDS - 1} ? hkT : 0.0);
  vec3 hkBand = diffuseColor.rgb;
  vec3 hkD = vBary / max(fwidth(vBary), vec3(1e-5));
  float hkM = min(min(hkD.x, hkD.y), hkD.z);
  float hkLine = (1.0 - smoothstep(uLinePx * 0.5 - 0.6, uLinePx * 0.5 + 0.6, hkM)) * uEdges;
  diffuseColor.rgb = mix(diffuseColor.rgb, uInk, hkLine);`
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
    return m;
  }

  // ---- builders -----------------------------------------------------------

  function buildBody(THREE, opts, seed, U, roots, propMass) {
    const detail = DETAIL[opts.mesh] ?? DETAIL.coarse;
    const ico = new THREE.IcosahedronGeometry(1, detail);
    // put one vertex of the icosahedron on +y so the antenna grows from a node
    ico.rotateX(-Math.atan2((1 + Math.sqrt(5)) / 2, 1));
    const w = weld(ico.attributes.position.array);
    ico.dispose();

    // edge length of the subdivided icosahedron on the unit sphere
    const edge = 1.0515 / (detail + 1);
    const rnd = mulberry32(seed);
    const pts = [];
    for (const n of w.nodes) {
      const d = new THREE.Vector3(n[0], n[1], n[2]).normalize();
      if (d.y > 0.9999) {
        pts.push(shapePoint(THREE, d));
        continue;
      }
      const r = new THREE.Vector3(rnd() * 2 - 1, rnd() * 2 - 1, rnd() * 2 - 1);
      r.addScaledVector(d, -r.dot(d)).multiplyScalar(0.16 * edge);
      const radial = 1 + (rnd() - 0.5) * 0.03;
      const dj = d.clone().add(r).normalize();
      pts.push(shapePoint(THREE, dj).multiplyScalar(radial));
    }
    const field = stressFields(pts, roots, propMass);

    // indexed, smooth normals: used for the outline hull and the node dots
    const smooth = new THREE.BufferGeometry();
    smooth.setAttribute("position", new THREE.Float32BufferAttribute(pts.flatMap((p) => [p.x, p.y, p.z]), 3));
    smooth.setIndex(w.tris.flat());
    smooth.computeVertexNormals();
    const sn = smooth.attributes.normal;

    // non-indexed faces: one face normal per element (flat facets),
    // barycentrics for the edge lines, and the two nodal fields
    const nTri = w.tris.length;
    const pos = new Float32Array(nTri * 9);
    const nor = new Float32Array(nTri * 9);
    const bar = new Float32Array(nTri * 9);
    const fs = new Float32Array(nTri * 3);
    const fm = new Float32Array(nTri * 3);
    const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3(), fn = new THREE.Vector3();
    const v = new THREE.Vector3();
    w.tris.forEach((tri, i) => {
      a.copy(pts[tri[0]]); b.copy(pts[tri[1]]); c.copy(pts[tri[2]]);
      fn.subVectors(c, b).cross(v.subVectors(a, b)).normalize();
      for (let k = 0; k < 3; k++) {
        const id = tri[k];
        const p = pts[id];
        pos.set([p.x, p.y, p.z], i * 9 + k * 3);
        nor.set([fn.x, fn.y, fn.z], i * 9 + k * 3);
        bar.set([k === 0 ? 1 : 0, k === 1 ? 1 : 0, k === 2 ? 1 : 0], i * 9 + k * 3);
        fs[i * 3 + k] = field.settled[id];
        fm[i * 3 + k] = field.mismatch[id];
      }
    });
    const faces = new THREE.BufferGeometry();
    faces.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    faces.setAttribute("normal", new THREE.BufferAttribute(nor, 3));
    faces.setAttribute("aBary", new THREE.BufferAttribute(bar, 3));
    faces.setAttribute("aSettled", new THREE.BufferAttribute(fs, 1));
    faces.setAttribute("aMismatch", new THREE.BufferAttribute(fm, 1));

    // point on the faceted surface along a direction from the centre
    const ray = new THREE.Ray();
    function surface(dir) {
      ray.set(new THREE.Vector3(), dir.clone().normalize());
      const hit = new THREE.Vector3();
      for (const tri of w.tris) {
        if (ray.intersectTriangle(pts[tri[0]], pts[tri[1]], pts[tri[2]], false, hit)) {
          const n = new THREE.Vector3().subVectors(pts[tri[2]], pts[tri[1]]).cross(new THREE.Vector3().subVectors(pts[tri[0]], pts[tri[1]])).normalize();
          return { point: hit, normal: n };
        }
      }
      return { point: shapePoint(THREE, dir.clone().normalize()), normal: dir.clone().normalize() };
    }

    const group = new THREE.Group();
    const body = new THREE.Mesh(faces, bodyMaterial(THREE, U));
    body.castShadow = true;
    group.add(body);

    const hull = new THREE.Mesh(smooth, null);
    group.add(hull);

    const dotR = opts.mesh === "fine" ? 0.028 : 0.034;
    const dotGeom = new THREE.SphereGeometry(dotR, 8, 6);
    const nodes = new THREE.InstancedMesh(dotGeom, null, pts.length);
    const m4 = new THREE.Matrix4();
    pts.forEach((p, i) => {
      v.set(sn.getX(i), sn.getY(i), sn.getZ(i));
      m4.makeTranslation(p.x + v.x * 0.006, p.y + v.y * 0.006, p.z + v.z * 0.006);
      nodes.setMatrixAt(i, m4);
    });
    group.add(nodes);

    const top = pts.reduce((best, p) => (p.y > best.y ? p : best), pts[0]);
    return {
      group, body, hull, nodes, surface, top: top.clone(),
      counts: { nodes: pts.length, elements: nTri },
      field: { nodes: pts.map((p) => [p.x, p.y, p.z]), settled: field.settled, mismatch: field.mismatch, loads: field.loads, mismatchLoads: field.mismatchLoads },
    };
  }

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

  function std(THREE, color, rough, metal) {
    return new THREE.MeshStandardMaterial({ color, roughness: rough ?? 0.55, metalness: metal ?? 0 });
  }

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
    const seed = hashString(role.id);

    const U = {
      uPal: { value: BAND_COLORS.map((h) => new THREE.Color(h)) },
      uInk: { value: new THREE.Color(C.ink) },
      uRimColor: { value: new THREE.Color(C.glow) },
      uRim: { value: 0.2 },
      uSelf: { value: 0.5 },
      uLoad: { value: 1 },
      uMis: { value: 0 },
      uLinePx: { value: 1.5 },
      uEdges: { value: opts.edges ? 1 : 0 },
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
    // the loads of the contour act here
    const legTop = (sx) => new THREE.Vector3(sx * 0.34, PIVOT_Y + 0.22, 0.05);
    const toBody = (p) => p.clone().sub(new THREE.Vector3(0, BODY_Y, 0));
    const shoulderR = shapePoint(THREE, new THREE.Vector3(0.9, -0.28, 0.3).normalize()).multiplyScalar(0.95);
    const handR = new THREE.Vector3(1.2, -0.52, 0.36);
    const shoulderL = shapePoint(THREE, new THREE.Vector3(-0.92, -0.3, 0.25).normalize()).multiplyScalar(0.95);
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

    const B = buildBody(THREE, opts, seed, U, roots, PROP_MASS[role.prop] ?? 0.3);
    core.add(B.group);
    B.hull.userData.hullWidth = 0.035;
    hulls.push(B.hull);
    B.nodes.material = M.ink;
    B.nodes.visible = !!opts.nodes;

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

    // eyes
    const eyes = [];
    for (const sx of [-1, 1]) {
      const d = new THREE.Vector3(sx * 0.28, 0.17, 0.95).normalize();
      const p = shapePoint(THREE, d);
      // sit the eye on the flat element it lands on, half turned to that facet
      const s = B.surface(p);
      const nrm = new THREE.Vector3(p.x / (SHAPE.sx * SHAPE.sx), p.y / (SHAPE.sy * SHAPE.sy), p.z / (SHAPE.sz * SHAPE.sz)).normalize();
      nrm.lerp(s.normal, 0.5).normalize();
      const eye = new THREE.Group();
      eye.position.copy(s.point).addScaledVector(nrm, 0.02);
      eye.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), nrm);
      core.add(eye);
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
      eyes.push({ lid, pupil, side: sx });
    }

    // antenna from the top node, tip node in the role colour
    const antenna = new THREE.Group();
    antenna.position.copy(B.top);
    core.add(antenna);
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
      M.limb.color.setHex(night ? 0x2a2925 : C.ink);
      U.uRim.value = night ? 0.85 : 0.3;
      blob.material.opacity = night ? 0.55 : 0.3;
      blob.material.color.setHex(night ? 0x000000 : C.ink);
    }
    applyTheme(opts.theme);

    const qTmp = new THREE.Quaternion();

    function update(t, state, camera) {
      state = STATES.includes(state) ? state : "idle";
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

      // contour: load factor (working steps it up), mismatch load case
      U.uLoad.value = loadFactor(t, state);
      U.uMis.value = state === "mismatch" ? 1 : 0;
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
      U.uEdges.value = showEdges ? clamp((px - 6) / 24, 0.3, 1) : 0;
      B.nodes.visible = showNodes && px >= 20;
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

    // What the contour shows at (t, state): load factor, load case, and the
    // largest nodal value (1.0 for the settled case by construction).
    function contour(t, state) {
      state = STATES.includes(state) ? state : "idle";
      const load = loadFactor(t, state);
      const mis = state === "mismatch";
      const vals = mis ? B.field.mismatch : B.field.settled.map((v) => v * load);
      return { load, loadCase: mis ? "mismatch" : "settled", max: Math.max(...vals), min: Math.min(...vals) };
    }

    applyScale();
    update(0, "idle");
    return { group: root, role, counts: B.counts, field: B.field, update, contour, setPixelScale, setOptions, dispose };
  }

  window.HirakuPets = { ROLES, STATES, FRAME, BANDS, STOPS, BAND_COLORS, LOAD, PROP_MASS, loadFactor, create };
})();

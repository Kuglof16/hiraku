// HIRAKU's look=real for the viewer's surroundings: the ground, the scene's items (wall, step, tilt table, gates,
// cones, road), the sky light and the sun's shadow. The values and the canvas drawings are those of web/js/look.js
// (the screen's look=real: earthTexture, concreteTexture, hazardTexture, plateHeight / normalMapFrom, the FINISH
// table, setUp's lights) and web/js/scene.js (REAL_OF, CONE_DRAW, GATE_POST_DRAW, hazardEdge), written again here
// as a classic script (a file:// page cannot load web/'s ES modules; web/ is not meant to be imported from here).
// Poly Haven files are not read (the screen reads them through its server); the drawn textures are what the
// screen shows without them. The frame members and the part boxes keep the viewer's contour colours.
window.HV = window.HV || {};
(function (HV) {
  'use strict';
  const L = HV.Look = {};

  // ---- canvas textures (web/js/look.js) ------------------------------------
  function rng(seed) {   // web/js/look.js rng: the same frame every time
    let s = seed >>> 0 || 1;
    return () => { s ^= s << 13; s ^= s >>> 17; s ^= s << 5; return ((s >>> 0) % 100000) / 100000; };
  }
  function drawCanvas(w, h, draw) { const c = document.createElement('canvas'); c.width = w; c.height = h; draw(c.getContext('2d'), w, h); return c; }
  function canvasTexture(canvas) {
    const T = window.THREE, tex = new T.CanvasTexture(canvas);
    tex.wrapS = tex.wrapT = T.RepeatWrapping; tex.anisotropy = 4; return tex;
  }
  // earth: speckles and soft blotches of brown and grey (web/js/look.js earthTexture)
  function earth(g, n) {
    const r = rng(7);
    g.fillStyle = '#8a7a62'; g.fillRect(0, 0, n, n);
    for (let i = 0; i < 260; i++) {
      const x = r() * n, y = r() * n, rad = 10 + r() * 50, grad = g.createRadialGradient(x, y, 0, x, y, rad);
      const tone = r() < 0.5 ? '120,104,80' : '150,136,112';
      grad.addColorStop(0, `rgba(${tone},0.35)`); grad.addColorStop(1, `rgba(${tone},0)`);
      g.fillStyle = grad; g.fillRect(x - rad, y - rad, rad * 2, rad * 2);
    }
    for (let i = 0; i < 9000; i++) {
      const v = 70 + Math.floor(r() * 110);
      g.fillStyle = `rgba(${v},${v - 8},${v - 22},${0.25 + r() * 0.4})`; g.fillRect(r() * n, r() * n, 1 + r() * 2, 1 + r() * 2);
    }
  }
  // concrete: a pale grey with fine grit and faint trowel marks (web/js/look.js concreteTexture)
  function concrete(g, n) {
    const r = rng(31);
    g.fillStyle = '#a7a49c'; g.fillRect(0, 0, n, n);
    for (let i = 0; i < 60; i++) {
      const x = r() * n, y = r() * n, rad = 8 + r() * 30, grad = g.createRadialGradient(x, y, 0, x, y, rad);
      const tone = r() < 0.5 ? '140,137,130' : '185,182,174';
      grad.addColorStop(0, `rgba(${tone},0.3)`); grad.addColorStop(1, `rgba(${tone},0)`);
      g.fillStyle = grad; g.fillRect(x - rad, y - rad, rad * 2, rad * 2);
    }
    for (let i = 0; i < 5000; i++) { const v = 120 + Math.floor(r() * 90); g.fillStyle = `rgba(${v},${v},${v - 6},${0.15 + r() * 0.3})`; g.fillRect(r() * n, r() * n, 1, 1); }
  }
  // yellow and black stripes at 45 degrees, the painted edge of a step (web/js/look.js hazardTexture)
  function hazard(g, n) {
    g.fillStyle = '#e8b81c'; g.fillRect(0, 0, n, n); g.fillStyle = '#1b1b1b';
    for (let x = -n; x < 2 * n; x += 32) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x + 16, 0); g.lineTo(x + 16 + n, n); g.lineTo(x + n, n); g.closePath(); g.fill(); }
  }
  // diamond plate height -> tangent-space normal map (web/js/look.js plateHeight, normalMapFrom)
  function plateNormal() {
    const h = drawCanvas(128, 128, (g, n) => {
      g.fillStyle = '#404040'; g.fillRect(0, 0, n, n); g.fillStyle = '#ffffff';
      for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) {
        g.save(); g.translate((x + (y % 2) * 0.5) * (n / 8), (y + 0.5) * (n / 8)); g.rotate(((y % 2 ? 1 : -1) * Math.PI) / 4); g.fillRect(-5, -1.5, 10, 3); g.restore();
      }
    });
    const w = 128, src = h.getContext('2d').getImageData(0, 0, w, w).data, H = (x, y) => src[(((y + w) % w) * w + ((x + w) % w)) * 4] / 255;
    return drawCanvas(w, w, g => {
      const img = g.createImageData(w, w);
      for (let y = 0; y < w; y++) for (let x = 0; x < w; x++) {
        const du = (H(x + 1, y) - H(x - 1, y)) * 4, dv = (H(x, y - 1) - H(x, y + 1)) * 4, len = Math.hypot(du, dv, 1), i = (y * w + x) * 4;
        img.data[i] = Math.round((-du / len) * 127.5 + 127.5); img.data[i + 1] = Math.round((-dv / len) * 127.5 + 127.5); img.data[i + 2] = Math.round((1 / len) * 127.5 + 127.5); img.data[i + 3] = 255;
      }
      g.putImageData(img, 0, 0);
    });
  }
  const CANVAS = {};
  function tex(name, span, side) {   // a texture of `name` whose uv [0, 1] spans span [u, v] mm, one tile = side mm
    const T = window.THREE;
    if (!CANVAS[name]) CANVAS[name] = name === 'plate' ? plateNormal() : drawCanvas(name === 'earth' ? 512 : name === 'hazard' ? 128 : 256, name === 'earth' ? 512 : name === 'hazard' ? 128 : 256, { earth, concrete, hazard }[name]);
    const t = canvasTexture(CANVAS[name]);
    if (name !== 'plate') t.colorSpace = T.SRGBColorSpace;
    t.repeat.set(span[0] / side, span[1] / side);
    return t;
  }

  // ---- values of the screen ------------------------------------------------
  // the drawn texture's side (web/js/look.js surfaceMaterial drawnSide_mm 2500), the hazard square (HAZARD_SIDE_MM
  // 400) and band (HAZARD_BAND_MM 100), the drawing-only cone and gate sizes (web/js/scene.js CONE_DRAW, GATE_POST_DRAW)
  L.DRAWN_SIDE_MM = 2500; L.HAZARD_SIDE_MM = 400; L.HAZARD_BAND_MM = 100;
  L.CONE_DRAW = { base: 300, height: 450 }; L.GATE_POST_DRAW = { radius: 25, height: 1000 };
  // web/js/scene.js REAL_OF (cone, gate, tilt table) and the floor and pivot under the table
  L.REAL = { cone: { color: '#e2682a', roughness: 0.6, metalness: 0 }, gate: { color: '#e2682a', roughness: 0.6, metalness: 0 },
             tilt_table: { color: '#9aa0a6', roughness: 0.45, metalness: 0.75 }, floor: { color: '#8a8780', roughness: 0.95, metalness: 0 } };
  // web/css/app.css --paper: the page and the 3D view's background (the real look off the stage has no sky behind)
  L.PAPER = 0xf4f1ea;

  // ---- materials -------------------------------------------------------------
  const T = () => window.THREE;
  L.earthMaterial = span => new (T().MeshStandardMaterial)({ map: tex('earth', span, L.DRAWN_SIDE_MM), roughness: 1, metalness: 0 });
  L.concreteMaterial = span => new (T().MeshStandardMaterial)({ map: tex('concrete', span, L.DRAWN_SIDE_MM), roughness: 0.95, metalness: 0 });
  L.plateMaterial = span => { const m = new (T().MeshStandardMaterial)(L.REAL.tilt_table); m.normalMap = tex('plate', span, 400); m.normalScale.set(0.8, 0.8); return m; };
  L.solid = spec => new (T().MeshStandardMaterial)(spec);
  L.hazardMaterial = span => new (T().MeshStandardMaterial)({ map: tex('hazard', span, L.HAZARD_SIDE_MM), roughness: 0.6, metalness: 0, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });
  function shadowed(o, cast) { o.traverse(m => { if (m.isMesh) { m.receiveShadow = true; m.castShadow = !!cast; } }); return o; }

  // ---- the view's light (web/js/look.js Look.setUp, the real look): sky light (three.js RoomEnvironment, the
  // screen's fall-back without the HDRI), a hemisphere light and a sun with a shadow. The viewer's own ambient and
  // camera light stay, so the contour colours of the members and parts read as before.
  L.setUp = function (view) {
    const TH = T(), r = view.renderer, s = view.scene;
    r.setClearColor(L.PAPER);
    r.shadowMap.enabled = true; r.shadowMap.type = TH.PCFSoftShadowMap;
    if (window.RoomEnvironment) {
      const pm = new TH.PMREMGenerator(r);
      s.environment = pm.fromScene(new window.RoomEnvironment(), 0.04).texture; s.environmentIntensity = 0.55; pm.dispose();
    }
    s.add(new TH.HemisphereLight(0xdfe8f2, 0x6b5b45, 0.5));
    const sun = new TH.DirectionalLight(0xfff4e5, 1.0); sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
    s.add(sun, sun.target); view.sun = sun;
  };
  // the sun over the box [lo, hi] (mm; from above, front-left as web's 2.5, 6, 3.5 in three's y-up), its shadow box fitted
  L.aimSun = function (view, lo, hi) {
    const sun = view.sun; if (!sun) return;
    const c = [0, 1, 2].map(a => (lo[a] + hi[a]) / 2), R = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], 1000) * 0.75 + 600;
    sun.position.set(c[0] + 0.35 * R * 2, c[1] + 0.5 * R * 2, c[2] + 0.85 * R * 2); sun.target.position.set(c[0], c[1], c[2]); sun.target.updateMatrixWorld();
    const cam = sun.shadow.camera; cam.left = -R; cam.right = R; cam.top = R; cam.bottom = -R; cam.near = 10; cam.far = 6 * R; cam.updateProjectionMatrix();
    sun.shadow.bias = -0.0005; sun.shadow.normalBias = 2;
  };
  L.castShadows = function (objects) { for (const o of objects) if (o) o.traverse(m => { if (m.isMesh && !m.material.transparent) m.castShadow = true; }); };

  // ---- the ground ------------------------------------------------------------------
  // Where the page puts the ground (viewer/src/ground.py says the same and tests it): D.ground (the design's supports:
  // wheel centre z less radius), else the record's own wheels (c z less r), else the design's wheel part boxes
  // (centre z less half the height). Returns {z, from} or null.
  HV.groundOf = function (D) {
    const g = D.ground;
    if (g && g.z !== null && g.z !== undefined) return { z: g.z, from: g.from || '設計の支持点: 車輪の中心の高さ − 半径' };
    const ws = (D.wheels || []).filter(w => w && w.c && w.r);
    if (ws.length) return { z: Math.min(...ws.map(w => w.c[2] - w.r)), from: '記録の車輪: 中心の高さ − 半径' };
    const ps = (D.parts || []).filter(p => String(p.kind).toLowerCase() === 'wheel');
    if (ps.length) return { z: Math.min(...ps.map(p => p.c[2] - p.s[2] / 2)), from: '設計の車輪の部品: 中心の高さ − 半径 (箱の高さの半分)' };
    const wall = (D.scene || []).find(s => s.kind === 'wall');
    if (wall) return { z: wall.c[2] - wall.s[2] / 2, from: '記録の壁の下の面 (壁は地面に立つ)' };
    return null;
  };
  // a ground plane of earth over [x0, x1] x [y0, y1] at z (mm), less the bands `holes` [[ya, yb], ...] across y (a
  // road's tracks drawn on their own); not clipped by the section planes, not pickable
  L.ground = function (x0, x1, y0, y1, z, holes = []) {
    const TH = T(), g = new TH.Group(), p = [];
    let ys = [[y0, y1]];
    for (const [a, b] of holes) ys = ys.flatMap(([u, v]) => (b <= u || a >= v) ? [[u, v]] : [[u, Math.max(u, a)], [Math.min(v, b), v]].filter(([s, e]) => e - s > 1));
    for (const [u, v] of ys) {
      const span = [x1 - x0, v - u], tex0 = L.earthMaterial(span);
      tex0.map.offset.set(x0 / L.DRAWN_SIDE_MM, u / L.DRAWN_SIDE_MM);   // the strips' texture lines up
      const m = new TH.Mesh(new TH.PlaneGeometry(span[0], span[1]), tex0);
      m.position.set((x0 + x1) / 2, (u + v) / 2, z); m.receiveShadow = true; m.userData.kind = 'ground'; g.add(m);
      // a faint 1 m grid on it, so lengths read (as the diagram look's ruled ground, web/js/look.js "zu")
      for (let i = Math.ceil(x0 / 1000); i <= Math.floor(x1 / 1000); i++) p.push(i * 1000, u, z + 0.5, i * 1000, v, z + 0.5);
      for (let j = Math.ceil(u / 1000); j <= Math.floor(v / 1000); j++) p.push(x0, j * 1000, z + 0.5, x1, j * 1000, z + 0.5);
    }
    const lg = new TH.BufferGeometry(); lg.setAttribute('position', new TH.Float32BufferAttribute(p, 3));
    g.add(new TH.LineSegments(lg, new TH.LineBasicMaterial({ color: 0x5e5446, transparent: true, opacity: 0.22 })));
    g.userData.kind = 'ground'; return g;
  };

  // ---- the scene's items ------------------------------------------------------------
  // A box item (wall, step, tilt table, ground) of the record's scene {kind, c, s}: concrete for a wall and a step
  // (web/js/scene.js REAL_OF), a step's face toward the vehicle painted (hazardEdge), the table a metal plate.
  L.box = function (s, view) {
    const TH = T(), sz = [Math.max(s.s[0], 1), Math.max(s.s[1], 1), Math.max(s.s[2], 10)];
    const geo = new TH.BoxGeometry(...sz);
    const mat = s.kind === 'tilt_table' ? L.plateMaterial([sz[0], sz[1]]) : s.kind === 'ground' ? L.earthMaterial([sz[0], sz[1]]) : L.concreteMaterial([Math.max(sz[0], sz[1]), sz[2]]);
    if (view) view.material(mat);
    const m = new TH.Mesh(geo, mat); m.userData = { kind: 'scene', item: s };
    const g = new TH.Group(); g.add(m); g.position.set(s.c[0], s.c[1], s.s[2] > 0 ? s.c[2] : s.c[2] - 5);
    if (s.kind === 'step') g.add(L.hazardEdge(sz[0], sz[1], sz[2], s.c[0] > 0 ? -1 : 1, view));
    g.userData = { kind: 'scene', item: s };
    return shadowed(g, s.kind !== 'ground');
  };
  // web/js/scene.js hazardEdge: a band down the face the vehicle meets (toward -1: the -x face) and back over the top
  L.hazardEdge = function (sx, sy, sz, toward, view) {
    const TH = T(), g = new TH.Group(), band = Math.min(L.HAZARD_BAND_MM, sz, sx), t = toward < 0 ? -1 : 1;
    const plane = (u, v, n, at) => {
      const geo = new TH.PlaneGeometry(sy, band).applyMatrix4(new TH.Matrix4().makeBasis(u, v, n).setPosition(at));
      const mat = L.hazardMaterial([sy, band]); if (view) view.material(mat);
      g.add(new TH.Mesh(geo, mat));
    };
    plane(new TH.Vector3(0, t, 0), new TH.Vector3(0, 0, 1), new TH.Vector3(t, 0, 0), new TH.Vector3((t * sx) / 2, 0, sz / 2 - band / 2));
    plane(new TH.Vector3(0, 1, 0), new TH.Vector3(-t, 0, 0), new TH.Vector3(0, 0, 1), new TH.Vector3(t * (sx / 2 - band / 2), 0, sz / 2));
    return g;
  };
  // a cone (a point: drawing-only size, its base on the road) and a gate (two posts at its ends), web/js/scene.js
  L.cone = function (s, z0 = 0) {
    const TH = T(), d = L.CONE_DRAW, m = new TH.Mesh(new TH.ConeGeometry(d.base / 2, d.height, 24).rotateX(Math.PI / 2), L.solid(L.REAL.cone));
    m.position.set(s.c[0], s.c[1], z0 + d.height / 2); return shadowed(m, true);
  };
  L.gate = function (s, z0 = 0) {
    const TH = T(), d = L.GATE_POST_DRAW, w = s.s[1] || 1500, g = new TH.Group(), mat = L.solid(L.REAL.gate);
    for (const sy of [-1, 1]) { const p = new TH.Mesh(new TH.CylinderGeometry(d.radius, d.radius, d.height, 12).rotateX(Math.PI / 2), mat); p.position.set(s.c[0], s.c[1] + sy * w / 2, z0 + d.height / 2); g.add(p); }
    // the line across the road between the posts (the gate's own line; paint, drawn 100 mm wide)
    const line = new TH.Mesh(new TH.PlaneGeometry(100, w), new TH.MeshStandardMaterial({ color: 0xf4f1ea, roughness: 0.8, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }));
    line.position.set(s.c[0], s.c[1], z0 + 1); g.add(line);
    return shadowed(g, true);
  };
})(window.HV);

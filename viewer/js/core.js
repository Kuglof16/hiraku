// Shared parts of the result viewer: data unpacking, colour map and contour bar, the 3D view
// (renderer, camera, clipping planes, picking), beams, part boxes, scene boxes and arrows.
// Classic script (file:// pages cannot load local ES modules); three.js is read from window.THREE,
// which index.html sets from the pinned jsDelivr module before HV.main runs.
window.HV = window.HV || {};
(function (HV) {
  'use strict';

  // ---------------------------------------------------------------- data
  // Packed array {dtype: 'f32', shape, b64}: base64 of gzip of little-endian float32.
  HV.unpack = async function (p) {
    const bin = atob(p.b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    const stream = new Blob([u8]).stream().pipeThrough(new DecompressionStream('gzip'));
    const buf = await new Response(stream).arrayBuffer();
    if (p.dtype === 'i32') return new Int32Array(buf);
    if (p.dtype === 'i16') { const q = new Int16Array(buf), out = new Float32Array(q.length), s = p.scale; for (let i = 0; i < q.length; i++) out[i] = q[i] * s; return out; }
    return new Float32Array(buf);
  };

  // ---------------------------------------------------------------- shell mesh
  // Quads of the shell mesh, each with its own 4 vertices (flat colour per element), normals per quad.
  HV.Shell = class {
    constructor(view, nodes, quads) {
      const T = window.THREE;
      this.nodes0 = nodes; this.quads = quads; this.nE = quads.length / 4;
      const nV = this.nE * 4;
      this.pos = new Float32Array(nV * 3); this.nrm = new Float32Array(nV * 3); this.col = new Float32Array(nV * 3).fill(0.7);
      const idx = new Uint32Array(this.nE * 6);
      for (let e = 0; e < this.nE; e++) { const b = 4 * e; idx.set([b, b + 1, b + 2, b, b + 2, b + 3], 6 * e); }
      this.geo = new T.BufferGeometry();
      this.posAttr = new T.BufferAttribute(this.pos, 3); this.nrmAttr = new T.BufferAttribute(this.nrm, 3); this.colAttr = new T.BufferAttribute(this.col, 3);
      this.geo.setAttribute('position', this.posAttr); this.geo.setAttribute('normal', this.nrmAttr); this.geo.setAttribute('color', this.colAttr);
      this.geo.setIndex(new T.BufferAttribute(idx, 1));
      this.mesh = new T.Mesh(this.geo, view.material(new T.MeshLambertMaterial({ vertexColors: true, side: T.DoubleSide })));
      this.mesh.userData.kind = 'shell';
      this.group = new T.Group(); this.group.add(this.mesh);
      this.setNodes(nodes);
    }
    setNodes(p) {   // p: node positions now (n x 3)
      const q = this.quads, P = this.pos, N = this.nrm;
      for (let e = 0; e < this.nE; e++) {
        for (let k = 0; k < 4; k++) { const n = 3 * q[4 * e + k], j = 12 * e + 3 * k; P[j] = p[n]; P[j + 1] = p[n + 1]; P[j + 2] = p[n + 2]; }
        const j = 12 * e;   // normal: cross of the diagonals
        const ax = P[j + 6] - P[j], ay = P[j + 7] - P[j + 1], az = P[j + 8] - P[j + 2];
        const bx = P[j + 9] - P[j + 3], by = P[j + 10] - P[j + 4], bz = P[j + 11] - P[j + 5];
        let nx = ay * bz - az * by, ny = az * bx - ax * bz, nz = ax * by - ay * bx; const l = Math.hypot(nx, ny, nz) || 1;
        nx /= l; ny /= l; nz /= l;
        for (let k = 0; k < 4; k++) { N[j + 3 * k] = nx; N[j + 3 * k + 1] = ny; N[j + 3 * k + 2] = nz; }
      }
      this.posAttr.needsUpdate = true; this.nrmAttr.needsUpdate = true; this.geo.computeBoundingSphere();
    }
    colourElements(fn) { for (let e = 0; e < this.nE; e++) { const c = fn(e); for (let k = 0; k < 4; k++) this.col.set(c, 12 * e + 3 * k); } this.colAttr.needsUpdate = true; }
    colourNodes(fn) { const q = this.quads; for (let e = 0; e < this.nE; e++) for (let k = 0; k < 4; k++) this.col.set(fn(q[4 * e + k]), 12 * e + 3 * k); this.colAttr.needsUpdate = true; }
    elementOf(hit) { return Math.floor(hit.faceIndex / 2); }
  };

  HV.esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  HV.fmt = (v, d = 3) => {
    if (v === null || v === undefined || Number.isNaN(v)) return '-';
    const a = Math.abs(v);
    if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(2);
    return (+v.toPrecision(d + 1)).toLocaleString('en-US', { maximumFractionDigits: 3 });
  };

  // ---------------------------------------------------------------- colour
  // Turbo colour map, polynomial approximation (Mikhailov 2019, Google AI blog "Turbo").
  HV.turbo = function (x) {
    x = Math.min(1, Math.max(0, x));
    const r = 0.13572138 + x * (4.61539260 + x * (-42.66032258 + x * (132.13108234 + x * (-152.94239396 + x * 59.28637943))));
    const g = 0.09140261 + x * (2.19418839 + x * (4.84296658 + x * (-14.18503333 + x * (4.27729857 + x * 2.82956604))));
    const b = 0.10667330 + x * (12.64194608 + x * (-60.58204836 + x * (110.36276771 + x * (-89.90310912 + x * 27.34824973))));
    return [Math.min(1, Math.max(0, r)), Math.min(1, Math.max(0, g)), Math.min(1, Math.max(0, b))];
  };
  // contour spec {min, max, log, unit, title, marks: [{v, label}]} -> position 0..1
  HV.norm = function (c, v) {
    if (c.log) {
      const lo = Math.log10(Math.max(c.min, 1e-30)), hi = Math.log10(Math.max(c.max, 1e-30));
      return hi > lo ? (Math.log10(Math.max(v, c.min)) - lo) / (hi - lo) : 0;
    }
    return c.max > c.min ? (v - c.min) / (c.max - c.min) : 0;
  };
  HV.colour = (c, v) => HV.turbo(HV.norm(c, v));

  HV.drawContourBar = function (cv, c) {
    const g = cv.getContext('2d'), W = cv.width, H = cv.height;
    g.clearRect(0, 0, W, H);
    if (!c) { cv.style.display = 'none'; return; }
    cv.style.display = 'block';
    g.fillStyle = 'rgba(255,255,255,.88)'; g.fillRect(0, 0, W, H);
    const x0 = 10, w = 16, y0 = 30, h = H - 70;
    g.fillStyle = '#000'; g.font = 'bold 11px system-ui, sans-serif';
    wrap(g, c.title + (c.unit ? ` [${c.unit}]` : ''), 4, 12, W - 8, 12);
    for (let i = 0; i < h; i++) { const k = HV.turbo(1 - i / h); g.fillStyle = `rgb(${k.map(v => Math.round(255 * v)).join(',')})`; g.fillRect(x0, y0 + i, w, 1); }
    g.strokeStyle = '#000'; g.lineWidth = 1; g.strokeRect(x0, y0, w, h);
    g.font = '11px system-ui, sans-serif';
    const ticks = [];
    if (c.log) {
      for (let e = Math.floor(Math.log10(c.min)); e <= Math.ceil(Math.log10(c.max)); e++) { const v = Math.pow(10, e); if (v >= c.min * 0.999 && v <= c.max * 1.001) ticks.push(v); }
      if (!ticks.length || ticks[0] > c.min * 1.001) ticks.unshift(c.min);
      if (ticks[ticks.length - 1] < c.max * 0.999) ticks.push(c.max);
    } else {
      for (let i = 0; i <= 5; i++) ticks.push(c.min + (c.max - c.min) * i / 5);
    }
    for (const v of ticks) {
      const y = y0 + h * (1 - HV.norm(c, v));
      g.beginPath(); g.moveTo(x0 + w, y); g.lineTo(x0 + w + 4, y); g.stroke();
      g.fillStyle = '#000'; g.fillText(HV.fmt(v), x0 + w + 7, y + 4);
    }
    for (const m of c.marks || []) {
      if (m.v < c.min || m.v > c.max) continue;
      const y = y0 + h * (1 - HV.norm(c, m.v));
      g.lineWidth = 2; g.beginPath(); g.moveTo(x0 - 4, y); g.lineTo(x0 + w + 4, y); g.stroke(); g.lineWidth = 1;
      g.font = 'bold 10px system-ui, sans-serif'; g.fillText(m.label, x0 + w + 7, y - 4); g.font = '11px system-ui, sans-serif';
    }
    g.fillStyle = '#444'; g.font = '10px system-ui, sans-serif';
    wrap(g, (c.log ? '対数目盛' : '線形目盛') + (c.note ? '、' + c.note : ''), 4, y0 + h + 14, W - 8, 11);
  };
  function wrap(g, text, x, y, maxW, lh) {
    let line = '';
    for (const word of String(text).split(' ')) {
      const t = line ? line + ' ' + word : word;
      if (g.measureText(t).width > maxW && line) { g.fillText(line, x, y); y += lh; line = word; } else line = t;
    }
    if (line) g.fillText(line, x, y);
  }

  // ---------------------------------------------------------------- 3D view
  HV.View = class {
    constructor(el) {
      const T = window.THREE;
      this.el = el;
      this.renderer = new T.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
      this.renderer.setPixelRatio(window.devicePixelRatio || 1);
      this.renderer.localClippingEnabled = true;
      this.renderer.setClearColor(0xf4f4f2);
      el.appendChild(this.renderer.domElement);
      this.scene = new T.Scene();
      this.camera = new T.PerspectiveCamera(35, 1, 5, 200000);
      this.camera.up.set(0, 0, 1);
      this.scene.add(this.camera);
      this.scene.add(new T.AmbientLight(0xffffff, 1.6));
      const dl = new T.DirectionalLight(0xffffff, 1.3); dl.position.set(0.3, 0.6, 1); this.camera.add(dl);
      this.controls = new window.OrbitControls(this.camera, this.renderer.domElement);
      this.controls.addEventListener('change', () => this.render());
      this.clip = [];          // active planes; the same array is given to every material
      this.materials = [];
      this.planes = { x: new T.Plane(), y: new T.Plane(), z: new T.Plane() };
      this.clipState = { x: { on: false, flip: false, v: 0 }, y: { on: false, flip: false, v: 0 }, z: { on: false, flip: false, v: 0 } };
      this.pending = false;
      this.afterRender = [];
      window.addEventListener('resize', () => this.resize());
    }
    // extra: planes of this material only (e.g. the wall face for part boxes), on top of the section planes
    material(m, extra) {
      m.userData.extra = extra || null;
      m.clippingPlanes = extra && extra.length ? this.clip.concat(extra) : this.clip;
      this.materials.push(m); return m;
    }
    updateClip() {
      const T = window.THREE;
      this.clip.length = 0;
      ['x', 'y', 'z'].forEach((k, a) => {
        const c = this.clipState[k]; if (!c.on) return;
        const n = new T.Vector3(); n.setComponent(a, c.flip ? 1 : -1);
        this.planes[k].set(n, c.flip ? -c.v : c.v);   // not flipped: keep k <= v; flipped: keep k >= v
        this.clip.push(this.planes[k]);
      });
      for (const m of this.materials) { if (m.userData.extra && m.userData.extra.length) m.clippingPlanes = this.clip.concat(m.userData.extra); m.needsUpdate = true; }
      this.render();
    }
    visible(p) { for (const pl of this.clip) if (pl.distanceToPoint(p) < 0) return false; return true; }
    resize() {
      const w = this.el.clientWidth, h = this.el.clientHeight;
      this.renderer.setSize(w, h); this.camera.aspect = w / Math.max(1, h); this.camera.updateProjectionMatrix(); this.render();
    }
    fit(lo, hi, dir = [0.9, -1.05, 0.75]) {   // default: from front-right-above
      const c = [0, 1, 2].map(a => (lo[a] + hi[a]) / 2), r = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], 100);
      this.controls.target.set(c[0], c[1], c[2]);
      this.camera.position.set(c[0] + dir[0] * r, c[1] + dir[1] * r, c[2] + dir[2] * r);
      this.controls.update();
    }
    render() {
      if (this.pending) return; this.pending = true;
      requestAnimationFrame(() => { this.pending = false; this.draw(); });
    }
    draw() { this.renderer.render(this.scene, this.camera); for (const f of this.afterRender) f(); }
    pick(ev, objects) {
      const T = window.THREE, r = this.renderer.domElement.getBoundingClientRect();
      const ray = new T.Raycaster();
      ray.setFromCamera(new T.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1), this.camera);
      const hits = ray.intersectObjects(objects.filter(o => o && o.visible), false);
      return hits.find(h => this.visible(h.point)) || null;
    }
  };

  // ---------------------------------------------------------------- beams
  // Each member is a tube of K sides between its two (moved) end nodes; colours per vertex,
  // so a node value shades along the member and a member value is flat.
  const K = 8;
  HV.Beams = class {
    constructor(view, frame, nodes) {
      const T = window.THREE;
      this.view = view; this.frame = frame; this.nodes0 = nodes;
      this.nM = frame.members.length;
      const nV = this.nM * 2 * K;
      this.pos = new Float32Array(nV * 3); this.col = new Float32Array(nV * 3).fill(0.6);
      const idx = [];
      for (let m = 0; m < this.nM; m++) {
        const b = m * 2 * K;
        for (let k = 0; k < K; k++) { const k1 = (k + 1) % K; idx.push(b + k, b + k1, b + K + k, b + k1, b + K + k1, b + K + k); }
      }
      this.geo = new T.BufferGeometry();
      this.posAttr = new T.BufferAttribute(this.pos, 3); this.colAttr = new T.BufferAttribute(this.col, 3);
      this.geo.setAttribute('position', this.posAttr); this.geo.setAttribute('color', this.colAttr); this.geo.setIndex(idx);
      this.mesh = new T.Mesh(this.geo, view.material(new T.MeshLambertMaterial({ vertexColors: true, side: T.DoubleSide })));
      this.mesh.userData.kind = 'beam';
      // the undeformed frame as thin lines
      const lp = new Float32Array(this.nM * 6);
      frame.members.forEach(([a, b], m) => { for (let c = 0; c < 3; c++) { lp[6 * m + c] = nodes[3 * a + c]; lp[6 * m + 3 + c] = nodes[3 * b + c]; } });
      const lg = new T.BufferGeometry(); lg.setAttribute('position', new T.BufferAttribute(lp, 3));
      this.ghost = new T.LineSegments(lg, view.material(new T.LineBasicMaterial({ color: 0x777777, transparent: true, opacity: 0.55 })));
      this.ghost.visible = false;
      this.group = new T.Group(); this.group.add(this.mesh, this.ghost);
      this.radius = frame.radius_mm.map(r => Math.max(6, r));
      this.cur = new Float32Array(nodes);
      this.setNodes(nodes);
    }
    setNodes(p) {   // p: Float32Array n x 3, positions now
      this.cur = p;
      const T = window.THREE, A = new T.Vector3(), B = new T.Vector3(), d = new T.Vector3(), u = new T.Vector3(), v = new T.Vector3(), ref = new T.Vector3();
      this.frame.members.forEach(([a, b], m) => {
        A.set(p[3 * a], p[3 * a + 1], p[3 * a + 2]); B.set(p[3 * b], p[3 * b + 1], p[3 * b + 2]);
        d.subVectors(B, A); if (d.lengthSq() < 1e-9) d.set(1, 0, 0); d.normalize();
        ref.set(0, 0, 1); if (Math.abs(d.z) > 0.9) ref.set(1, 0, 0);
        u.crossVectors(d, ref).normalize(); v.crossVectors(d, u);
        const r = this.radius[m], base = m * 2 * K;
        for (let k = 0; k < K; k++) {
          const th = 2 * Math.PI * k / K, cx = Math.cos(th) * r, sy = Math.sin(th) * r;
          for (let e = 0; e < 2; e++) {
            const P = e ? B : A, j = 3 * (base + e * K + k);
            this.pos[j] = P.x + cx * u.x + sy * v.x; this.pos[j + 1] = P.y + cx * u.y + sy * v.y; this.pos[j + 2] = P.z + cx * u.z + sy * v.z;
          }
        }
      });
      this.posAttr.needsUpdate = true; this.geo.computeVertexNormals(); this.geo.computeBoundingSphere();
    }
    colourNodes(fn) {   // fn(node index) -> [r, g, b]
      this.frame.members.forEach(([a, b], m) => {
        const ca = fn(a), cb = fn(b), base = m * 2 * K;
        for (let k = 0; k < K; k++) { this.col.set(ca, 3 * (base + k)); this.col.set(cb, 3 * (base + K + k)); }
      });
      this.colAttr.needsUpdate = true;
    }
    colourMembers(fn) { // fn(member index) -> [r, g, b]
      for (let m = 0; m < this.nM; m++) { const c = fn(m), base = m * 2 * K; for (let k = 0; k < 2 * K; k++) this.col.set(c, 3 * (base + k)); }
      this.colAttr.needsUpdate = true;
    }
    memberOf(hit) { return Math.floor(hit.faceIndex / (2 * K)); }
  };

  // ---------------------------------------------------------------- boxes
  // Part boxes: one instanced mesh (instance colour) plus their edges; a box is posed by its
  // centre offset (off[i]) and an extra rotation (rot[i], a quaternion applied after its own rpy, or null),
  // and hidden with show[i] = false. opts.clip: planes cutting the boxes only (the wall face).
  HV.Boxes = class {
    constructor(view, parts, opts = {}) {
      const T = window.THREE;
      this.view = view; this.parts = parts; this.n = parts.length;
      this.mat = view.material(new T.MeshLambertMaterial({ transparent: true, opacity: opts.opacity ?? 0.5, depthWrite: false, side: T.DoubleSide }), opts.clip);
      this.mesh = new T.InstancedMesh(new T.BoxGeometry(1, 1, 1), this.mat, Math.max(1, this.n));
      this.mesh.count = this.n; this.mesh.userData.kind = 'box';
      this.mesh.instanceColor = new T.InstancedBufferAttribute(new Float32Array(Math.max(1, this.n) * 3).fill(0.7), 3);
      this.quat = parts.map(p => new T.Quaternion().setFromEuler(new T.Euler(p.r[0] * Math.PI / 180, p.r[1] * Math.PI / 180, p.r[2] * Math.PI / 180, 'ZYX')));
      this.off = parts.map(() => [0, 0, 0]); this.show = parts.map(() => true); this.rot = parts.map(() => null);
      this.epos = new Float32Array(this.n * 24 * 3);
      const eg = new T.BufferGeometry(); this.eattr = new T.BufferAttribute(this.epos, 3); eg.setAttribute('position', this.eattr);
      this.edges = new T.LineSegments(eg, view.material(new T.LineBasicMaterial({ color: 0x333333, transparent: true, opacity: 0.45 }), opts.clip));
      this.group = new T.Group(); this.group.add(this.mesh, this.edges);
      this.update();
    }
    colour(i, c) { this.mesh.instanceColor.setXYZ(i, c[0], c[1], c[2]); }
    update() {
      const T = window.THREE, M = new T.Matrix4(), S = new T.Vector3(), P = new T.Vector3(), v = new T.Vector3(), Q = new T.Quaternion();
      const E = [[0, 1], [1, 3], [3, 2], [2, 0], [4, 5], [5, 7], [7, 6], [6, 4], [0, 4], [1, 5], [2, 6], [3, 7]];
      this.parts.forEach((p, i) => {
        const on = this.show[i], o = this.off[i];
        P.set(p.c[0] + o[0], p.c[1] + o[1], p.c[2] + o[2]);
        S.set(on ? Math.max(p.s[0], 1) : 1e-6, on ? Math.max(p.s[1], 1) : 1e-6, on ? Math.max(p.s[2], 1) : 1e-6);
        if (this.rot[i]) Q.multiplyQuaternions(this.rot[i], this.quat[i]); else Q.copy(this.quat[i]);
        M.compose(P, Q, S); this.mesh.setMatrixAt(i, M);
        const cs = [];
        for (let k = 0; k < 8; k++) { v.set((k & 1 ? 0.5 : -0.5), (k & 2 ? 0.5 : -0.5), (k & 4 ? 0.5 : -0.5)).applyMatrix4(M); cs.push([v.x, v.y, v.z]); }
        E.forEach(([a, b], e) => { const j = (i * 24 + 2 * e) * 3; this.epos.set(cs[a], j); this.epos.set(cs[b], j + 3); });
      });
      this.mesh.instanceMatrix.needsUpdate = true; this.mesh.instanceColor.needsUpdate = true; this.eattr.needsUpdate = true;
      this.mesh.computeBoundingSphere(); this.edges.geometry.computeBoundingSphere();
    }
  };

  // A part moved by a fitted rigid motion (viewer/src/common.py rigid_fit): fit centre c, translation t and
  // rotation vector w per unit of the shape, times k. Returns the box centre offset and the rotation (a proper
  // rotation by the angle |k w|, so a large display scale turns the box without stretching it).
  HV.rigidPose = function (pc, c, t, w, k) {
    const T = window.THREE, ang = k * Math.hypot(w[0], w[1], w[2]);
    const q = new T.Quaternion();
    if (ang > 1e-12) q.setFromAxisAngle(new T.Vector3(w[0], w[1], w[2]).normalize(), ang);
    const r = new T.Vector3(pc[0] - c[0], pc[1] - c[1], pc[2] - c[2]).applyQuaternion(q);
    return { off: [c[0] + k * t[0] + r.x - pc[0], c[1] + k * t[1] + r.y - pc[1], c[2] + k * t[2] + r.z - pc[2]], q: ang > 1e-12 ? q : null };
  };

  // Tyres (a ring of the support's radius and width) and their springs (a coil from the wheel node to a fixed
  // ground point, as K1 has it). set(i, u): the wheel moved by u (mm).
  HV.Wheels = class {
    constructor(view, wheels) {
      const T = window.THREE;
      this.view = view; this.w = wheels; this.group = new T.Group(); this.tyres = []; this.coils = [];
      const tm = view.material(new T.MeshLambertMaterial({ color: 0x303030, transparent: true, opacity: 0.55, side: T.DoubleSide, depthWrite: false }));
      const cm = view.material(new T.LineBasicMaterial({ color: 0x0b6e4f }));
      for (const w of wheels) {
        const g = new T.CylinderGeometry(w.r, w.r, Math.max(w.width, 20), 40, 1, true);   // axis along y (the axle)
        const m = new T.Mesh(g, tm); m.position.set(...w.c); this.group.add(m); this.tyres.push(m);
        const N = 120, lg = new T.BufferGeometry(); lg.setAttribute('position', new T.BufferAttribute(new Float32Array(N * 3), 3));
        const l = new T.Line(lg, cm); l.visible = !!w.spring; this.group.add(l); this.coils.push(l);
        this.set(this.tyres.length - 1, [0, 0, 0]);
      }
    }
    set(i, u) {
      const w = this.w[i], m = this.tyres[i], l = this.coils[i];
      m.position.set(w.c[0] + u[0], w.c[1] + u[1], w.c[2] + u[2]);
      if (!w.spring) return;
      // coil from the ground point (fixed) to the wheel centre (moved), offset outboard of the tyre so it shows
      const a = w.ground, b = [w.c[0] + u[0], w.c[1] + u[1], w.c[2] + u[2]], side = Math.sign(w.c[1]) || 1, dy = side * (w.width / 2 + 60);
      const p = l.geometry.attributes.position, N = p.count, turns = 8, R = 35;
      for (let j = 0; j < N; j++) {
        const s = j / (N - 1), th = 2 * Math.PI * turns * s, ends = s < 0.06 || s > 0.94 ? 0 : 1;
        p.setXYZ(j, a[0] + (b[0] - a[0]) * s + ends * R * Math.cos(th), a[1] + dy + (b[1] - a[1]) * s + ends * R * Math.sin(th),a[2] + (b[2] - a[2]) * s);
      }
      p.needsUpdate = true; l.geometry.computeBoundingSphere();
    }
  };

  // ---------------------------------------------------------------- parts in the calculation
  // D.ledger (viewer/src/common.py parts_ledger): per part (D.parts order) its category in this field's
  // calculation, mass, the nodes it sits on (kg each) and the deciding function; the mass table against the
  // record's stated mass. Shared by every field: colours the part boxes, the legend, the table, the click text.
  HV.LEDGER_COL = { frame_nodes: [0.16, 0.42, 0.85], unsprung: [0.95, 0.55, 0.08], stiffness: [0.15, 0.65, 0.3],
                    mass_only: [0.6, 0.35, 0.78], body: [0.4, 0.62, 0.72], not_in: [0.86, 0.86, 0.86],
                    sprung: [0.25, 0.5, 0.8], half: [0.85, 0.75, 0.2] };   // K2: the rigid body, half with the wheel
  HV.GREY = [0.8, 0.8, 0.82];
  const rgb = c => `rgb(${c.map(v => Math.round(255 * v)).join(',')})`;
  const kg = v => (v === null || v === undefined) ? '-' : `${(+v).toFixed(1)} kg`;
  HV.Ledger = class {
    constructor(D) { this.D = D; this.L = D.ledger || null; }
    has() { return !!(this.L && this.L.parts && this.L.parts.length); }
    cat(i) { return this.L && this.L.parts[i] ? this.L.parts[i].cat : null; }
    paint(boxes, mode) {   // mode: 'category' | 'grey' (anything else: the field's own colours)
      if (!boxes || !this.has() || (mode !== 'category' && mode !== 'grey')) return false;
      for (let i = 0; i < boxes.n; i++) boxes.colour(i, mode === 'grey' ? HV.GREY : (HV.LEDGER_COL[this.cat(i)] || HV.GREY));
      return true;
    }
    legendHtml() {
      if (!this.has()) return '<div class="muted">部品の扱いのデータがない</div>';
      const L = this.L;
      return L.table.map(r => `<div class="lg"><span class="sw" style="background:${rgb(HV.LEDGER_COL[r.cat])}"></span>` +
        `${HV.esc(L.categories[r.cat])}: ${r.count} 個` + (L.tool === 'assembly' ? '' : `、${kg(r.mass_kg)}`) + '</div>').join('');
    }
    tableHtml() {
      if (!this.has()) return '';
      const L = this.L, m = L.members || {};
      let h = '<table class="mt"><tr><th>内訳</th><th>個数</th><th>質量</th></tr>';
      for (const r of L.table) h += `<tr><td>${HV.esc(L.categories[r.cat])}</td><td>${r.count}</td><td>${L.tool === 'assembly' ? '-' : kg(r.mass_kg)}</td></tr>`;
      h += `<tr><td>${HV.esc(m.label || 'フレームの部材 (剛性あり)')}</td><td>${m.count ?? '-'}</td><td>${kg(m.mass_kg)}</td></tr>`;
      if (L.total_kg !== null && L.total_kg !== undefined) h += `<tr class="tot"><td>計算に入る合計</td><td></td><td>${kg(L.total_kg)}</td></tr>`;
      h += '</table>';
      if ((L.stated || []).length) {
        h += '<table class="mt"><tr><th>記録の値</th><th>記録</th><th>ここで計算</th><th>判定</th></tr>' +
          L.stated.map(s => `<tr><td>${HV.esc(s.label)}</td><td>${kg(s.stated_kg)}</td><td>${kg(s.computed_kg)}</td>` +
            `<td class="${s.agrees ? 'ok' : 'ng'}">${s.agrees ? 'OK' : 'NG'}${s.agrees ? '' : ` (差 ${s.diff_kg === null ? '-' : (s.diff_kg >= 0 ? '+' : '') + s.diff_kg.toFixed(1)} kg)`}</td></tr>`).join('') + '</table>';
        const bad = L.stated.filter(s => !s.agrees);
        if (bad.length) h += `<div class="ng">差の出どころ: ${HV.esc(bad[0].why || '')}</div>`;
      } else {
        h += `<div class="muted">記録に質量の値がない (${L.tool === 'assembly' ? '組み立ては質量を使わない' : '照合なし'})</div>`;
      }
      h += `<div class="muted">乗員・荷物: ${HV.esc(L.loading)}</div>`;
      if (L.error) h += `<div class="ng">${HV.esc(L.error)}</div>`;
      return h;
    }
    listsHtml() {
      if (!this.has()) return '';
      const L = this.L, ids = a => a.slice(0, 12).map(HV.esc).join(', ') + (a.length > 12 ? ` ほか ${a.length - 12}` : '');
      const ns = L.no_stiffness || [], ni = L.not_in || [];
      return `<div class="pl"><b>剛性なしの部品</b> ${ns.length} 個${ns.length ? ': ' + ids(ns) : ''}</div>` +
             `<div class="pl"><b>計算に入っていない部品</b> ${ni.length} 個${ni.length ? ': ' + ids(ni.map(x => x.id)) : ''}</div>` +
             (L.members && L.members.crush_members ? `<div class="pl"><b>つぶれる部材</b> ${ids(L.members.crush_members)}</div>` : '');
    }
    info(i) {
      if (!this.has() || !this.L.parts[i]) return '';
      const e = this.L.parts[i], L = this.L;
      const nodes = (e.nodes || []).map(([n, w]) => w === null ? HV.esc(n) : `${HV.esc(n)} ${(+w).toFixed(2)} kg`).join(', ');
      return `<div class="lc"><span class="sw" style="background:${rgb(HV.LEDGER_COL[e.cat])}"></span><b>${HV.esc(L.categories[e.cat])}</b></div>` +
             `${HV.esc(e.id)} (${HV.esc(e.kind)}) ${kg(e.mass_kg)}` + (nodes ? `<br>節点: ${nodes}` : '') +
             `<br><span class="muted">${HV.esc(e.fn)}</span>` +
             `<details><summary>計算での扱い (詳しく)</summary>${HV.esc(e.how)}</details>`;
    }
  };

  // scene boxes (wall, step, table): translucent, with edges
  HV.sceneBoxes = function (view, list) {
    const T = window.THREE, g = new T.Group();
    for (const s of list) {
      const geo = new T.BoxGeometry(s.s[0], s.s[1], s.s[2]);
      const m = new T.Mesh(geo, view.material(new T.MeshLambertMaterial({ color: 0x8c8c96, transparent: true, opacity: 0.35, depthWrite: false })));
      const e = new T.LineSegments(new T.EdgesGeometry(geo), view.material(new T.LineBasicMaterial({ color: 0x404050 })));
      m.position.set(...s.c); e.position.set(...s.c); m.userData = { kind: 'scene', item: s };
      g.add(m, e);
    }
    return g;
  };

  // ---------------------------------------------------------------- arrows
  HV.Arrows = class {
    constructor(view) {
      const T = window.THREE;
      this.view = view; this.group = new T.Group(); this.items = [];
      this.shaft = new T.CylinderGeometry(1, 1, 1, 10); this.shaft.translate(0, 0.5, 0);
      this.cone = new T.ConeGeometry(1, 1, 14); this.cone.translate(0, -0.5, 0);
    }
    clear() { for (const a of this.items) this.group.remove(a); this.items = []; }
    add(colour) {
      const T = window.THREE;
      const mat = new T.MeshBasicMaterial({ color: colour, depthTest: false, transparent: true, opacity: 0.9 });
      const a = new T.Group(), s = new T.Mesh(this.shaft, mat), c = new T.Mesh(this.cone, mat);
      s.renderOrder = c.renderOrder = 8; a.add(s, c); a.s = s; a.c = c;
      this.group.add(a); this.items.push(a); return a;
    }
    set(a, o, f, mmPerN) {   // o: origin mm, f: force N
      const T = window.THREE, len = Math.hypot(f[0], f[1], f[2]) * mmPerN;
      a.visible = len > 1;
      if (!a.visible) return;
      const head = Math.min(60, 0.35 * len), r = 6;
      a.position.set(o[0], o[1], o[2]);
      a.quaternion.setFromUnitVectors(new T.Vector3(0, 1, 0), new T.Vector3(f[0], f[1], f[2]).normalize());
      a.s.scale.set(r, len - head, r); a.c.position.set(0, len, 0); a.c.scale.set(head * 0.4, head, head * 0.4);
    }
  };
})(window.HV);

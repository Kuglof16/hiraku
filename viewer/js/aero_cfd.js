// Aerodynamics, the CFD variant of the aero field (viewer/src/aero_cfd.py, data key aero_cfd, モデル switch K2 / CFD):
// the opaque vehicle on a dark background, streamlines of the computed mean field coloured by speed or Cp, and lights
// that run only along the stored polylines, each point reached at its stored travel time. The page always says how far
// the accuracy has been checked (D.status). Loaded after js/aero.js: this file adds the variant to its field.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
(function (HV) {
  'use strict';
  // added when the page starts, so the order of the script tags of aero.js and this file does not matter
  const start = HV.main;
  HV.main = function (...args) {
    const field = HV.fields.aero;
    if (field) {   // a page without js/aero.js has no aero field, and this variant stays out of it
      const k2 = field.init;
      field.variants = [{ key: 'aero', label: 'K2' }, { key: 'aero_cfd', label: 'CFD', always: true }];
      field.init = function (D, ctx) { return D.model === 'cfd' ? cfd(D, ctx) : k2.call(this, D, ctx); };
    }
    return start.apply(this, args);
  };

  const LOOK = { body: [0.55, 0.57, 0.6], frame: [0.1, 0.11, 0.13], occupant: [0.28, 0.3, 0.33], cargo: [0.17, 0.22, 0.29], other: [0.19, 0.2, 0.22], tyre: 0x1b1b1e, net: 0xaeb2b8, ground: 0x101216, back: 0x08090c };
  const lookOf = p => (['body', 'fender', 'rollbar'].includes(p.kind) ? LOOK.body : p.kind === 'occupant' || p.module === 'occupant' ? LOOK.occupant : p.kind === 'cargo' ? LOOK.cargo : LOOK.other);
  const toLinear = c => (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));   // HV.colour gives display values; vertex colours are linear
  const SIDES = 5, TUBE_MM = 4, TAIL = 7, TAIL_S = 0.16, EVERY_S = 1.1;   // tube radius; the lights' tail: rings, its length in flow time; a light per this much flow time

  // rings around a polyline point: e1, e2 from the tangent (ax, ay, az) and a fixed up
  function ring(pos, at, x, y, z, ax, ay, az, r) {
    let ux = ay, uy = -ax, uz = 0;   // tangent x (0, 0, 1)
    if (Math.abs(az) > 0.9 * (Math.hypot(ax, ay, az) || 1)) { ux = -az; uy = 0; uz = ax; }   // near vertical: tangent x (0, 1, 0)
    const l = Math.hypot(ux, uy, uz) || 1; ux /= l; uy /= l; uz /= l;
    let vx = ay * uz - az * uy, vy = az * ux - ax * uz, vz = ax * uy - ay * ux;
    const m = Math.hypot(vx, vy, vz) || 1; vx /= m; vy /= m; vz /= m;
    for (let k = 0; k < SIDES; k++) {
      const th = 2 * Math.PI * k / SIDES, c = Math.cos(th) * r, s = Math.sin(th) * r, j = at + 3 * k;
      pos[j] = x + c * ux + s * vx; pos[j + 1] = y + c * uy + s * vy; pos[j + 2] = z + c * uz + s * vz;
    }
  }

  class Lines {   // one yaw's streamlines: tubes (coloured per point) and the lights
    constructor(view, Y, a) {
      const T = window.THREE;
      this.Y = Y; this.a = a; this.n = a.offsets.length - 1;
      const np = a.time.length, pts = a.points, off = a.offsets;
      this.pos = new Float32Array(np * SIDES * 3); this.col = new Float32Array(np * SIDES * 3);
      for (let l = 0; l < this.n; l++) {
        const s = off[l], e = off[l + 1];
        for (let i = s; i < e; i++) {
          const p = i > s ? i - 1 : i, q = i < e - 1 ? i + 1 : i;
          ring(this.pos, 3 * SIDES * i, pts[3 * i], pts[3 * i + 1], pts[3 * i + 2], pts[3 * q] - pts[3 * p], pts[3 * q + 1] - pts[3 * p + 1], pts[3 * q + 2] - pts[3 * p + 2], TUBE_MM);
        }
      }
      this.geo = new T.BufferGeometry();
      this.geo.setAttribute('position', new T.BufferAttribute(this.pos, 3));
      this.colAttr = new T.BufferAttribute(this.col, 3); this.geo.setAttribute('color', this.colAttr);
      // one index buffer, rewritten by the filter (a new buffer per slider step would pile up on the graphics card)
      this.index = new T.BufferAttribute(new Uint32Array((np - this.n) * SIDES * 6), 1); this.geo.setIndex(this.index);
      this.mesh = new T.Mesh(this.geo, view.material(new T.MeshBasicMaterial({ vertexColors: true, toneMapped: false })));
      this.mesh.userData.kind = 'ground'; this.mesh.frustumCulled = false;   // kind 'ground': main.js leaves it out of the sun's shadow
      // the lights: per line one per EVERY_S of its travel time, each a short tube of TAIL rings
      this.lights = [];
      for (let l = 0; l < this.n; l++) {
        const Tl = a.time[off[l + 1] - 1], k = Math.max(1, Math.floor(Tl / EVERY_S)), phase = (l * 0.618034) % 1;
        for (let j = 0; j < k; j++) this.lights.push({ line: l, t0: (phase + j) / k * Tl, T: Tl });
      }
      const nL = this.lights.length, nv = nL * TAIL * SIDES;
      this.lpos = new Float32Array(nv * 3); this.lcol = new Float32Array(nv * 3);
      const idx = new Uint32Array(nL * (TAIL - 1) * SIDES * 6); let w = 0;
      for (let i = 0; i < nL; i++) for (let r = 0; r < TAIL - 1; r++) for (let k = 0; k < SIDES; k++) {
        const b = (i * TAIL + r) * SIDES, k1 = (k + 1) % SIDES;
        idx[w++] = b + k; idx[w++] = b + k1; idx[w++] = b + SIDES + k; idx[w++] = b + k1; idx[w++] = b + SIDES + k1; idx[w++] = b + SIDES + k;
      }
      this.lgeo = new T.BufferGeometry();
      this.lposAttr = new T.BufferAttribute(this.lpos, 3); this.lcolAttr = new T.BufferAttribute(this.lcol, 3);
      this.lgeo.setAttribute('position', this.lposAttr); this.lgeo.setAttribute('color', this.lcolAttr); this.lgeo.setIndex(new T.BufferAttribute(idx, 1));
      this.lmesh = new T.Mesh(this.lgeo, view.material(new T.MeshBasicMaterial({ vertexColors: true, toneMapped: false, transparent: true, depthWrite: false, blending: T.AdditiveBlending })));
      this.lmesh.frustumCulled = false; this.lmesh.userData.kind = 'lights';
      this.group = new T.Group(); this.group.add(this.mesh, this.lmesh);
      this.heads = new Float32Array(nL * 3);
      this.filter(Infinity);
    }
    // keep the lines that come within limit (mm) of the vehicle's box and do not pass between the camera and the vehicle
    // (hide: a bit of the data's per-line side: 1 with the camera on the side axis's minus side, 2 on its plus side, 0 no line hidden)
    filter(limit, hide) {
      const off = this.a.offsets, keep = this.keep = new Uint8Array(this.n), idx = this.index.array, side = this.Y.lines.side; let w = 0;
      for (let l = 0; l < this.n; l++) if (this.Y.lines.near[l] <= limit && !(side[l] & hide)) {
        keep[l] = 1;
        for (let i = off[l]; i < off[l + 1] - 1; i++) for (let k = 0; k < SIDES; k++) {
          const b = i * SIDES, k1 = (k + 1) % SIDES;
          idx[w++] = b + k; idx[w++] = b + k1; idx[w++] = b + SIDES + k; idx[w++] = b + k1; idx[w++] = b + SIDES + k1; idx[w++] = b + SIDES + k;
        }
      }
      this.index.needsUpdate = true; this.geo.setDrawRange(0, w);
      this.shown = keep.reduce((s, v) => s + v, 0);
    }
    paint(fn) {   // fn(point index) -> [r, g, b]
      const c = this.col;
      for (let i = 0; i < this.a.time.length; i++) { const k = fn(i); for (let s = 0; s < SIDES; s++) c.set(k, 3 * (SIDES * i + s)); }
      this.colAttr.needsUpdate = true;
    }
    // where a line is at flow time t: the stored polyline, between the two points whose stored times hold t
    at(l, t, out) {
      const a = this.a, off = a.offsets, tm = a.time, pts = a.points;
      let lo = off[l], hi = off[l + 1] - 1;
      if (t <= tm[lo]) hi = lo + 1;
      else if (t >= tm[hi]) lo = hi - 1;
      else while (hi - lo > 1) { const m = (lo + hi) >> 1; if (tm[m] <= t) lo = m; else hi = m; }
      const f = Math.min(1, Math.max(0, (t - tm[lo]) / Math.max(tm[hi] - tm[lo], 1e-9)));
      for (let c = 0; c < 3; c++) out[c] = pts[3 * lo + c] + f * (pts[3 * hi + c] - pts[3 * lo + c]);
      out[3] = f < 0.5 ? lo : hi;
      return out;
    }
    move(now) {   // now: flow time (s)
      const lp = this.lpos, lc = this.lcol, col = this.col, P = [0, 0, 0, 0], Q = [0, 0, 0, 0];
      this.lights.forEach((L, i) => {
        const b0 = i * TAIL * SIDES * 3;
        if (!this.keep[L.line]) { lp.fill(0, b0, b0 + TAIL * SIDES * 3); return; }
        const head = ((now + L.t0) % L.T + L.T) % L.T, width = TUBE_MM * 1.8;
        for (let r = 0; r < TAIL; r++) {
          const t = Math.max(0, head - TAIL_S * r / (TAIL - 1));
          this.at(L.line, t, P); this.at(L.line, t + 0.01, Q);
          ring(lp, b0 + 3 * SIDES * r, P[0], P[1], P[2], Q[0] - P[0], Q[1] - P[1], Q[2] - P[2], width * (1 - 0.65 * r / (TAIL - 1)));
          const fade = Math.pow(1 - r / (TAIL - 1), 1.5), at = 3 * SIDES * P[3];   // the colour of the line at that point, brightened
          for (let s = 0; s < SIDES; s++) { const j = b0 + 3 * (SIDES * r + s); lc[j] = fade * (0.55 + 0.6 * col[at]); lc[j + 1] = fade * (0.55 + 0.6 * col[at + 1]); lc[j + 2] = fade * (0.55 + 0.6 * col[at + 2]); }
          if (r === 0) { this.heads[3 * i] = P[0]; this.heads[3 * i + 1] = P[1]; this.heads[3 * i + 2] = P[2]; }
        }
      });
      this.lposAttr.needsUpdate = true; this.lcolAttr.needsUpdate = true;
    }
    dispose() { this.geo.dispose(); this.lgeo.dispose(); }
  }

  async function cfd(D, ctx) {
    const T = window.THREE, view = ctx.view;
    const beams = await ctx.addBeams();
    beams.colourMembers(() => LOOK.frame);
    const boxes = ctx.addParts(D.parts, { opacity: 1 });
    boxes.mat.transparent = false; boxes.mat.depthWrite = true; boxes.mat.needsUpdate = true;   // the vehicle is never see-through
    const paintParts = () => { D.parts.forEach((p, i) => boxes.colour(i, lookOf(p))); boxes.mesh.instanceColor.needsUpdate = true; };
    // the calculation's shapes: a wheel is a cylinder and a side net its wires (below), not a box
    D.parts.forEach((p, i) => { boxes.show[i] = !!D.on_board[i] && p.kind !== 'side_net' && p.kind !== 'wheel'; });
    paintParts(); boxes.update();
    const extras = new T.Group(); ctx.root.add(extras);
    // the wheels: opaque cylinders with the axle along y (CylinderGeometry's own axis)
    const tyre = view.material(new T.MeshLambertMaterial({ color: LOOK.tyre }));
    for (const w of D.wheels) { const m = new T.Mesh(new T.CylinderGeometry(w.r, w.r, w.width, 48), tyre); m.position.set(...w.c); m.userData.kind = 'tyre'; extras.add(m); }
    // the side nets: their wires
    const netPts = await HV.unpack(D.nets);
    if (netPts.length) { const g = new T.BufferGeometry(); g.setAttribute('position', new T.BufferAttribute(netPts, 3)); extras.add(new T.LineSegments(g, view.material(new T.LineBasicMaterial({ color: LOOK.net })))); }
    // the dark background and floor belong to this field: they leave with it (ctx.root), the page's background stays as it is
    const backdrop = new T.Mesh(new T.SphereGeometry(120000, 24, 12), new T.MeshBasicMaterial({ color: LOOK.back, side: T.BackSide, depthWrite: false }));
    backdrop.renderOrder = -10; backdrop.frustumCulled = false; backdrop.userData.kind = 'ground'; extras.add(backdrop);
    const floor = new T.Mesh(new T.CircleGeometry(60000, 48), new T.MeshBasicMaterial({ color: LOOK.ground }));
    floor.position.set(0, 0, D.ground.z - 1); floor.userData.kind = 'ground'; extras.add(floor);

    const S = { yaw: 0, by: 'speed', near: null, rate: 0.25, clock: 0, last: null, lines: null, clear: true, hide: 0 };
    // which lines the camera's side hides: the data's side axis of this yaw (horizontal, square to the air), from the moment centre
    const hideNow = () => {
      if (!S.clear) return 0;
      const c = view.camera.position, a = D.yaws[S.yaw].side_axis, m = D.centre_mm;
      return (c.x - m[0]) * a[0] + (c.y - m[1]) * a[1] + (c.z - m[2]) * a[2] >= 0 ? 2 : 1;
    };
    const refilter = () => { S.hide = hideNow(); S.lines.filter(S.near === null ? Infinity : S.near, S.hide); S.lines.move(S.clock); mark(); };
    const onOrbit = () => { if (S.lines && hideNow() !== S.hide) refilter(); };
    view.controls.addEventListener('change', onOrbit);
    // the time row's play and slider do nothing here (a mean field); they come back when this variant leaves
    const timeRows = [document.getElementById('play'), document.getElementById('time')].map(e => e && e.closest('.row')).filter(Boolean);
    timeRows.forEach(r => { r.style.display = 'none'; });
    const yawText = v => `${v > 0 ? '+' : ''}${v}°`;
    const cache = new Map();
    const nearMax = Math.max(1000, ...D.yaws.flatMap(y => y.lines.near));
    const specOf = () => S.by === 'speed'
      ? { min: 0, max: D.yaws[S.yaw].speed_top, unit: 'm/s', title: '空気の速さ', note: `遠くの空気の速さ ${HV.fmt(D.yaws[S.yaw].air_speed_ms)} m/s` }
      : { min: -1, max: 1, unit: '1', title: '圧力係数 Cp', note: 'Cp = (p - p∞) / (ρ U² / 2)、1 より上と -1 より下は端の色' };
    const paint = () => { const a = S.lines.a, c = specOf(), v = S.by === 'speed' ? a.speed : a.cp; S.lines.paint(i => HV.colour(c, v[i]).map(toLinear)); };
    async function linesOf(i) {
      if (cache.has(i)) return cache.get(i);
      const Y = D.yaws[i], L = Y.lines, a = { points: await HV.unpack(L.points), speed: await HV.unpack(L.speed), cp: await HV.unpack(L.cp), time: await HV.unpack(L.time), offsets: await HV.unpack(L.offsets) };
      const o = new Lines(view, Y, a); cache.set(i, o); return o;
    }
    const setYaw = async i => {
      S.yaw = i;
      const next = await linesOf(i);
      if (S.lines) extras.remove(S.lines.group);
      S.lines = next; extras.add(next.group); paint(); refilter();
      if (HV.state && HV.state.ctl === ctl) HV.refresh();
    };
    function mark() {
      document.querySelectorAll('#cfdyaws button').forEach((b, i) => b.classList.toggle('on', i === S.yaw));
      document.querySelectorAll('#cfdtable tr[data-i]').forEach((r, i) => { r.style.fontWeight = i === S.yaw ? '600' : ''; });
      const c = document.getElementById('cfdcount'); if (c && S.lines) c.textContent = `${S.lines.shown} / ${S.lines.n} 本`;
    }
    const row = () => D.table[S.yaw];
    const ctl = {
      nT: 1, t0: 0, partMode: 'field', ground: null, viewDir: [1.25, 2.1, 0.7],
      timeNote: '平均の流れの場で、時間の変化はない。光は流線の上を、各点の速さから求めた到達時間で動く',
      scale: { min: 0, max: 1, step: 0.05, value: S.rate, label: '光の動く速さ (実時間の倍率)', note: '1 で実際の空気の速さ、0 で止まる' },
      setScale(v) { S.rate = +v; },
      setTime() { if (ctx.partMode === 'field') paintParts(); },
      timeText() { const r = row(); return `横風の角度 ${yawText(r.yaw_deg)}   Cd ${HV.fmt(r.Cd)}   CdA ${HV.fmt(r.CdA_m2)} m²   Cy ${HV.fmt(r.Cy)}   Cl ${HV.fmt(r.Cl)}`; },
      hud() {
        return [`検証: ${D.status.text}`,
                `色: ${S.by === 'speed' ? '空気の速さ' : '圧力係数 Cp'}   線 ${S.lines ? S.lines.shown : 0} 本は計算した平均の流れをたどった線、光はその上だけを動く`];
      },
      contour: specOf,
      plot() {
        const order = D.table.map((r, i) => i).filter(i => D.table[i].speed_ms > 0).sort((a, b) => D.table[a].yaw_deg - D.table[b].yaw_deg), x = order.map(i => D.table[i].yaw_deg);
        if (order.length < 2) return [];
        const mk = () => [{ x: row().yaw_deg, label: `${row().yaw_deg}°`, col: '#555', w: 2 }];
        const line = (k, name, col) => ({ name, v: order.map(i => D.table[i][k]), col });
        return [{ title: '抗力・横力・揚力の係数 (クリックで角度)', unit: '1', xlabel: '横風の角度 [deg]', x, ys: [line('Cd', 'Cd', '#2b59c3'), line('Cy', 'Cy', '#4cbf7a'), line('Cl', 'Cl', '#e9893a')], marks: mk(), cursor: null, logy: false }];
      },
      plotClick(_, x) { let b = 0, bd = Infinity; D.table.forEach((r, i) => { const d = Math.abs(r.yaw_deg - x); if (d < bd) { bd = d; b = i; } }); setYaw(b); },
      pickables: () => [beams.mesh, boxes.mesh],
      info(hit) {
        if (hit.object === boxes.mesh) return `<b>${HV.esc(D.parts[hit.instanceId].id)}</b> ${HV.esc(D.parts[hit.instanceId].name)}`;
        const m = beams.memberOf(hit);
        return `<b>${HV.esc(D.frame.member_ids[m])}</b> ${HV.esc(D.frame.member_names[m])}<br>流れの中では形だけ`;
      },
      extraInputs() {
        const num = v => (v === null || v === undefined ? '-' : HV.fmt(v));
        const hd = ['角度', 'Fx N', 'Fy N', 'Fz N', 'Mx N·m', 'My N·m', 'Mz N·m', 'Cd', 'CdA m²', 'Cy', 'Cl', 'ゆれ %'];
        return `<details id="cfdforces"><summary>角度ごとの力と係数 (${D.table.length})</summary><table><tr>${hd.map(h => `<td><b>${h}</b></td>`).join('')}</tr>` +
          D.table.map(r => `<tr><td>${HV.esc(r.yaw_deg)}°</td>` + ['Fx', 'Fy', 'Fz', 'Mx', 'My', 'Mz', 'Cd', 'CdA_m2', 'Cy', 'Cl', 'spread_pct'].map(k => `<td>${num(r[k])}</td>`).join('') + '</tr>').join('') +
          '</table><div class="muted">ゆれ: 平均の区間を 4 つに分けたときの Fx の差。力は車の座標、回転の中心は前後の軸の中央・左右の中央・地面</div></details>';
      },
      panel(div) {
        const h = document.createElement('h3'); h.textContent = '検証'; div.appendChild(h);
        const st = document.createElement('div'); st.id = 'cfdstatus'; st.textContent = D.status.text; st.title = D.status.source; st.style.fontWeight = '600'; div.appendChild(st);
        const h1 = document.createElement('h3'); h1.textContent = '横風の角度'; div.appendChild(h1);
        const ys = document.createElement('div'); ys.className = 'row'; ys.id = 'cfdyaws';
        D.yaws.forEach((y, i) => { const b = document.createElement('button'); b.textContent = yawText(y.yaw_deg) + (y.speed_ms > 0 ? '' : ' 止まった車'); b.onclick = () => setYaw(i); ys.appendChild(b); });
        div.appendChild(ys);
        const tb = document.createElement('table'); tb.className = 'mt'; tb.id = 'cfdtable';
        const cells = (tag, texts) => { const tr = document.createElement('tr'); for (const t of texts) { const c = document.createElement(tag); c.textContent = t; tr.appendChild(c); } return tr; };
        tb.appendChild(cells('th', ['角度', 'Cd', 'CdA m²', 'Cy', 'Cl']));
        D.table.forEach((r, i) => { const tr = cells('td', [`${r.yaw_deg}°`, HV.fmt(r.Cd), HV.fmt(r.CdA_m2), HV.fmt(r.Cy), HV.fmt(r.Cl)]); tr.dataset.i = i; tb.appendChild(tr); });
        div.appendChild(tb);
        const h2 = document.createElement('h3'); h2.textContent = '線の色'; div.appendChild(h2);
        for (const [k, lab] of [['speed', '空気の速さ'], ['cp', '圧力係数 Cp']]) {
          const l = document.createElement('label'); l.style.display = 'block';
          const r = document.createElement('input'); r.type = 'radio'; r.name = 'cfdcol'; r.id = 'cfdcol_' + k; r.checked = S.by === k;
          r.onchange = () => { S.by = k; paint(); HV.refresh(); };
          l.append(r, ' ' + lab); div.appendChild(l);
        }
        const h3 = document.createElement('h3'); h3.textContent = '車の近くの線だけ'; div.appendChild(h3);
        const f = document.createElement('div'); f.className = 'row';
        const inp = document.createElement('input'), val = document.createElement('span');
        inp.type = 'range'; inp.id = 'cfdnear'; inp.min = 0; inp.max = Math.ceil(nearMax / 100) * 100; inp.step = 50; inp.value = inp.max;
        val.id = 'cfdnearval'; val.className = 'val';
        f.append(inp, val); div.appendChild(f);
        const note = document.createElement('div'); note.className = 'muted'; note.textContent = '車を囲む箱からの距離がこれ以内を通る線 ';
        const count = document.createElement('span'); count.id = 'cfdcount'; note.appendChild(count); div.appendChild(note);
        const apply = () => { S.near = +inp.value; val.textContent = `${inp.value} mm`; refilter(); HV.refresh(); };
        val.textContent = `${inp.value} mm`; inp.oninput = apply;
        const lab = document.createElement('label'); lab.style.display = 'block';
        const clear = document.createElement('input'); clear.type = 'checkbox'; clear.id = 'cfdclear'; clear.checked = S.clear;
        clear.onchange = () => { S.clear = clear.checked; refilter(); HV.refresh(); };
        lab.append(clear, ' 手前の線を隠す (車が見えるように、線を丸ごと)'); div.appendChild(lab);
        mark();
      },
      setYaw,
      state: () => ({ yaw: S.yaw, clock: S.clock, rate: S.rate, by: S.by, shown: S.lines.shown, heads: Array.from(S.lines.heads), lights: S.lines.lights.map(L => ({ line: L.line, t0: L.t0, T: L.T })), keep: Array.from(S.lines.keep) }),
      linesData: () => S.lines.a,
    };
    await setYaw(0);
    // the lights' own clock: runs while this field is on the page
    const tick = now => {
      if (HV.state && HV.state.ctl && HV.state.ctl !== ctl) {   // another field or variant is on the page
        view.controls.removeEventListener('change', onOrbit);
        timeRows.forEach(r => { r.style.display = ''; });
        cache.forEach(o => o.dispose());
        return;
      }
      if (S.last !== null && S.rate > 0 && HV.state && HV.state.ctl === ctl) { S.clock += (now - S.last) / 1000 * S.rate; S.lines.move(S.clock); view.render(); }
      S.last = now; requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
    return ctl;
  }
})(window.HV);

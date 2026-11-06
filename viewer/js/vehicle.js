// K2 replays shared by handling, powertrain and aero (viewer/src/common.py k2_motion, handling.k2_case):
// the vehicle (frame and part boxes as one rigid body, tyres per corner) posed from the record's series, the
// record's scene (tilt table or slope turned by its angle channel, ground, gates, cones, road profile, step),
// reaction / grip arrows at the contacts, a case switch (D.cases), and the camera following the vehicle.
window.HV = window.HV || {};
(function (HV) {
  'use strict';
  const K2 = HV.K2 = {};
  const SCENE_COL = { tilt_table: 0x9a8f7a, slope: 0x9aa07a, ground: 0xb8b8b0, step: 0x8c8c96 };

  // the case shown: D.cases[sel] copied onto D (main.js reads D.title, D.source, D.inputs, D.ledger after init)
  K2.useCase = function (name, D) {
    const f = HV.fields[name], n = (D.cases || []).length;
    f.sel = Math.min(f.sel || 0, Math.max(0, n - 1));
    if (n) Object.assign(D, D.cases[f.sel]);
    return f.sel;
  };
  K2.casePanel = function (name, D, div) {
    if (!(D.cases || []).length) return;
    const h = document.createElement('h3'); h.textContent = '試験'; div.appendChild(h);
    D.cases.forEach((c, i) => {
      const b = document.createElement('button'); b.id = `case_${name}_${i}`;
      b.textContent = c.source.load_case_id.split('-')[0] + ' ' + (HV.K2.TOOL_JA[c.tool] || c.tool);
      b.className = i === HV.fields[name].sel ? 'on' : ''; b.style.margin = '1px';
      b.onclick = () => { HV.fields[name].sel = i; HV.open(name); };
      div.appendChild(b);
    });
  };
  K2.TOOL_JA = { tilt: '傾斜台', braking: '制動', lane_change: 'レーンチェンジ', offroad_roll: '横斜面の悪路',
                 launch: '発進加速', step_climb_dynamic: '段差', crosswind: '横風', drive_energy: '周回の電力' };
  K2.unpackPlots = async function (D) {
    const out = {};
    for (const [ch, s] of Object.entries(D.plots || {})) out[ch] = { unit: s.unit, t: Array.from(await HV.unpack(s.t)), v: Array.from(await HV.unpack(s.v)) };
    return out;
  };
  K2.judgeText = function (j) {
    if (!j) return '';
    return `判定 ${j.ok === null ? '-' : j.ok ? 'OK' : 'NG'}: ${j.quantity} ${HV.fmt(j.value)} ${j.op} ${HV.fmt(j.limit)} ${j.unit}`;
  };
  K2.judgeRow = function (D) {
    const j = D.judge, hs = D.hot_spots || [];
    return (j ? `<div class="h" style="margin-top:4px">判定</div><table><tr><td>${HV.esc(j.quantity)}</td><td>${HV.fmt(j.value)} ${HV.esc(j.op)} ${HV.fmt(j.limit)} ${HV.esc(j.unit)}</td>` +
      `<td class="${j.ok ? 'ok' : 'ng'}">${j.ok === null ? '-' : j.ok ? 'OK' : 'NG'}</td></tr></table>` : '') +
      (hs.length ? `<details><summary>ホットスポット (${hs.length})</summary><table>${hs.map(h => `<tr><td>${HV.esc(h.where)}</td><td>${HV.esc(h.quantity)}</td><td>${HV.fmt(h.value)} ${HV.esc(h.unit)}</td></tr>`).join('')}</table></details>` : '');
  };

  // ---------------------------------------------------------------- the replay
  K2.Replay = class {
    static async make(D, ctx) { const r = new K2.Replay(D, ctx); await r.init(); return r; }
    constructor(D, ctx) { this.D = D; this.ctx = ctx; this.M = D.motion; }
    async init() {
      const T = window.THREE, D = this.D, ctx = this.ctx, M = this.M;
      this.beams = await ctx.addBeams();
      this.boxes = ctx.addParts(D.parts, { opacity: 0.22 });   // light, so the tyres' colour shows through
      [this.t, this.pose, this.W] = await Promise.all([M.t, M.pose, M.wheels].map(HV.unpack));
      this.nT = this.t.length; this.nw = M.corners.length; this.nc = M.channels.length;
      this.ch = Object.fromEntries(M.channels.map((c, i) => [c, i]));
      this.plots = await K2.unpackPlots(D);
      this.cur = new Float32Array(this.beams.nodes0.length);
      // tyres
      this.tyreGroup = new T.Group(); ctx.root.add(this.tyreGroup); this.tyres = [];
      for (const c of M.corners) {
        const mat = ctx.view.material(new T.MeshLambertMaterial({ color: 0x303030, side: T.DoubleSide }));
        const g = new T.CylinderGeometry(c.r, c.r, Math.max(c.width, 40), 32, 1, false);
        const m = new T.Mesh(g, mat); m.userData = { kind: 'tyre', corner: c.id }; this.tyreGroup.add(m); this.tyres.push(m);
        // spokes so the spin shows
        const sp = new T.Mesh(new T.BoxGeometry(c.r * 1.9, Math.max(c.width, 40) * 1.02, c.r * 0.12), ctx.view.material(new T.MeshLambertMaterial({ color: 0xdddddd })));
        m.add(sp);
      }
      this.arrows = ctx.addArrows(); this.arr = M.corners.map(() => ({ n: this.arrows.add('#0a6b2a'), g: this.arrows.add('#c0392b') }));
      this.sceneGroup();
      // the camera box: the parts at the first pose
      const o = this.origin(0);
      ctx.lo = ctx.lo.map((v, a) => v + o[a]); ctx.hi = ctx.hi.map((v, a) => v + o[a]);
      this.follow = true; this.mmPerN = 0.15; this.k = 0; this.last = null;
    }
    origin(k) { return [this.pose[6 * k], this.pose[6 * k + 1], this.pose[6 * k + 2]]; }
    quat(k) {
      const T = window.THREE, d = Math.PI / 180, p = this.pose;
      return new T.Quaternion().setFromEuler(new T.Euler(p[6 * k + 3] * d, p[6 * k + 4] * d, p[6 * k + 5] * d, 'ZYX'));
    }
    w(k, i, name) { return this.W[(k * this.nw + i) * this.nc + this.ch[name]]; }
    wv(k, i, a) { const b = (k * this.nw + i) * this.nc + this.ch[a]; return [this.W[b], this.W[b + 1], this.W[b + 2]]; }
    seriesAt(ch, k) {   // a plot series at the replay time k (nearest sample)
      const s = this.plots[ch]; if (!s) return null;
      const tv = this.t[k]; let j = 0, bd = Infinity;
      for (let i = 0; i < s.t.length; i++) { const d = Math.abs(s.t[i] - tv); if (d < bd) { bd = d; j = i; } }
      return s.v[j];
    }
    near(tv) { let b = 0, bd = Infinity; for (let i = 0; i < this.nT; i++) { const d = Math.abs(this.t[i] - tv); if (d < bd) { bd = d; b = i; } } return b; }
    sceneGroup() {
      const T = window.THREE, ctx = this.ctx, view = ctx.view, g = new T.Group();
      for (const s of this.D.scene || []) {
        if (s.kind === 'gate') {
          const w = s.s[1] || 1500, x = s.c[0], y = s.c[1], mat = view.material(new T.MeshLambertMaterial({ color: 0xd04020 }));
          for (const sy of [-1, 1]) { const p = new T.Mesh(new T.CylinderGeometry(15, 15, 900, 8), mat); p.rotation.x = Math.PI / 2; p.position.set(x, y + sy * w / 2, 450); g.add(p); }
          const bar = new T.Mesh(new T.BoxGeometry(20, w, 20), mat); bar.position.set(x, y, 900); g.add(bar);
        } else if (s.kind === 'cone') {
          const c = new T.Mesh(new T.ConeGeometry(120, 450, 16), view.material(new T.MeshLambertMaterial({ color: 0xff7a00 })));
          c.rotation.x = Math.PI / 2; c.position.set(s.c[0], s.c[1], 225); g.add(c);
        } else if (s.kind === 'road_profile' && s.profile) {
          this.profile(g, s.profile);
        } else if (s.s && (s.s[0] > 0 || s.s[1] > 0)) {
          const sz = [Math.max(s.s[0], 1), Math.max(s.s[1], 1), Math.max(s.s[2], 10)];
          const geo = new T.BoxGeometry(...sz);
          const m = new T.Mesh(geo, view.material(new T.MeshLambertMaterial({ color: SCENE_COL[s.kind] || 0x8c8c96, transparent: true, opacity: s.kind === 'step' ? 0.6 : 0.35, depthWrite: false })));
          // the slope and the tilt table are the road frame: drawn flat, the whole road turned by its angle
          const z = s.s[2] > 0 ? s.c[2] : s.c[2] - 5;
          m.position.set(s.c[0], s.c[1], z); m.userData = { kind: 'scene', item: s };
          const e = new T.LineSegments(new T.EdgesGeometry(geo), view.material(new T.LineBasicMaterial({ color: 0x404050 })));
          e.position.copy(m.position); g.add(m, e);
          if (s.angle_channel) this.turn = s;
        }
      }
      ctx.sceneGroup = g; ctx.root.add(g); ctx.has.scene = true;
      if (this.turn) {   // a fixed level floor under the turned road, so the angle reads
        const s = this.turn, fl = new T.Mesh(new T.PlaneGeometry(s.s[0] + 4000, s.s[1] + 6000), view.material(new T.MeshBasicMaterial({ color: 0xd8d8d0, transparent: true, opacity: 0.5, depthWrite: false, side: T.DoubleSide })));
        fl.position.set(s.c[0], s.c[1], s.c[2] - Math.max(s.s[2], 10) / 2 - 2); ctx.fixed = new T.Group(); ctx.fixed.add(fl); view.scene.add(ctx.fixed);
      }
    }
    async profile(g, p) {
      const T = window.THREE, [x, l, r] = await Promise.all([p.x, p.left, p.right].map(HV.unpack)), ht = p.half_track || 600;
      const n = x.length, pos = new Float32Array(n * 4 * 3), idx = [];
      for (let i = 0; i < n; i++) {   // a ribbon: outside left, left track, right track, outside right
        const ys = [ht + 700, ht, -ht, -ht - 700], zs = [l[i], l[i], r[i], r[i]];
        for (let j = 0; j < 4; j++) pos.set([x[i], ys[j], zs[j]], 3 * (4 * i + j));
        if (i) for (let j = 0; j < 3; j++) { const a = 4 * (i - 1) + j, b = 4 * i + j; idx.push(a, b, a + 1, b, b + 1, a + 1); }
      }
      const geo = new T.BufferGeometry(); geo.setAttribute('position', new T.BufferAttribute(pos, 3)); geo.setIndex(idx); geo.computeVertexNormals();
      g.add(new T.Mesh(geo, this.ctx.view.material(new T.MeshLambertMaterial({ color: 0x9c8f72, side: T.DoubleSide, transparent: true, opacity: 0.7, depthWrite: false }))));
      HV.view.render();
    }
    // the road frame's turn (tilt table / slope): about the x axis through the low edge of the item
    turnRoot(k) {
      const T = window.THREE, s = this.turn, root = this.ctx.root;
      if (!s) return 0;
      const a = this.seriesAt(s.angle_channel, k) || 0, rad = a * Math.PI / 180;
      const hy = s.c[1] - Math.sign(a || 1) * s.s[1] / 2, hz = s.c[2] - Math.max(s.s[2], 10) / 2;
      root.quaternion.setFromAxisAngle(new T.Vector3(1, 0, 0), rad);
      const h = new T.Vector3(0, hy, hz), hr = h.clone().applyQuaternion(root.quaternion);
      root.position.set(0, h.y - hr.y, h.z - hr.z); root.updateMatrixWorld(true);
      return a;
    }
    // tyre colour: fn(corner index) -> [r, g, b] or null (dark)
    setFrame(k, tyreCol) {
      const T = window.THREE, D = this.D, M = this.M, q = this.quat(k), o = this.origin(k), v = new T.Vector3();
      this.k = k;
      const n0 = this.beams.nodes0, c = this.cur;
      for (let i = 0; i < n0.length; i += 3) { v.set(n0[i], n0[i + 1], n0[i + 2]).applyQuaternion(q); c[i] = o[0] + v.x; c[i + 1] = o[1] + v.y; c[i + 2] = o[2] + v.z; }
      this.beams.setNodes(c);
      const b = this.boxes;
      D.parts.forEach((p, j) => {
        const mode = M.part_mode[j], ci = M.part_corner[j];
        v.set(p.c[0], p.c[1], p.c[2]).applyQuaternion(q);
        let P = [o[0] + v.x, o[1] + v.y, o[2] + v.z];
        if (mode) {
          const cb = M.corners[ci].cb, wc = this.wv(k, ci, 'wheel_center_x');
          v.set(p.c[0] - cb[0], p.c[1] - cb[1], p.c[2] - cb[2]).applyQuaternion(q);
          const U = [wc[0] + v.x, wc[1] + v.y, wc[2] + v.z];
          P = mode === 1 ? U : P.map((x, a) => (x + U[a]) / 2);
        }
        b.off[j] = [P[0] - p.c[0], P[1] - p.c[1], P[2] - p.c[2]]; b.rot[j] = q;
      });
      b.update();
      M.corners.forEach((cn, i) => {
        const m = this.tyres[i], wc = this.wv(k, i, 'wheel_center_x');
        const st = this.w(k, i, 'wheel_steer') * Math.PI / 180, sp = this.w(k, i, 'wheel_spin') * Math.PI / 180;
        m.position.set(wc[0], wc[1], wc[2]);
        m.quaternion.copy(q).multiply(new T.Quaternion().setFromAxisAngle(new T.Vector3(0, 0, 1), st)).multiply(new T.Quaternion().setFromAxisAngle(new T.Vector3(0, 1, 0), sp));
        const col = tyreCol ? tyreCol(i) : null;
        m.material.color.setRGB(...(col || [0.19, 0.19, 0.19]));
        const cp = this.wv(k, i, 'contact_x');
        this.arrows.set(this.arr[i].n, cp, this.wv(k, i, 'reaction_x'), this.mmPerN);
        this.arrows.set(this.arr[i].g, cp, this.wv(k, i, 'grip_x'), this.mmPerN);
      });
      const ang = this.turnRoot(k);
      this.followCam(o);
      return ang;
    }
    followCam(o) {
      const view = this.ctx.view, T = window.THREE;
      const w = this.ctx.root.localToWorld(new T.Vector3(o[0], o[1], o[2]));
      if (this.follow && this.last) {
        const d = w.clone().sub(this.last);
        view.controls.target.add(d); view.camera.position.add(d); view.controls.update();
      }
      this.last = w;
    }
    tyreInfo(i) {
      const k = this.k, c = this.M.corners[i], f = n => HV.fmt(this.w(k, i, n));
      return `<b>輪 ${HV.esc(c.id)}</b> (${c.drive ? '駆動' : '従動'}${c.brake ? '、ブレーキ' : ''}${c.steer ? '、かじ取り' : ''}${c.dof ? '' : '、ばねなし'})<br>` +
        `荷重 ${f('wheel_load')} N、摩擦の使用 ${f('friction_use')}${this.w(k, i, 'sliding') > 0.5 ? ' (すべり)' : ''}<br>` +
        `ストローク ${f('wheel_stroke')} mm、かじ角 ${f('wheel_steer')}°<br><span class="muted">半径 ${HV.fmt(c.r)} mm、幅 ${HV.fmt(c.width)} mm</span>`;
    }
    pickables() { return [this.beams.mesh, this.boxes.mesh, ...this.tyres]; }
    info(hit) {
      if (hit.object.userData.kind === 'tyre') return this.tyreInfo(this.tyres.indexOf(hit.object));
      if (hit.object === this.beams.mesh) {
        const m = this.beams.memberOf(hit);
        return `<b>${HV.esc(this.D.frame.member_ids[m])}</b> ${HV.esc(this.D.frame.member_names[m])}<br>車体と一緒に剛体として動く (重さだけ車体に入る)`;
      }
      return '';
    }
    // panel: tyre colour choice and follow
    panel(div, choices, cur, onPick) {
      const h = document.createElement('h3'); h.textContent = 'タイヤの色'; div.appendChild(h);
      for (const [k, lab] of choices) {
        const l = document.createElement('label'); l.style.display = 'block';
        const r = document.createElement('input'); r.type = 'radio'; r.name = 'tyrecol'; r.id = 'tyrecol_' + k; r.checked = cur === k;
        r.onchange = () => onPick(k); l.append(r, ' ' + lab); div.appendChild(l);
      }
      const l = document.createElement('label'); l.style.display = 'block';
      const c = document.createElement('input'); c.type = 'checkbox'; c.checked = this.follow; c.id = 'k2follow';
      c.onchange = () => { this.follow = c.checked; }; l.append(c, ' 車を追って見る'); div.appendChild(l);
    }
    // plot helpers
    line(ch, col, name) { const s = this.plots[ch]; return s ? { name, v: s.v, col } : null; }
    wheelPanel(prefix, title, unit, cursor) {
      const cols = ['#1f4e9c', '#a03000', '#0a6b2a', '#8e44ad', '#555'], ids = this.M.corners.map(c => c.id);
      const ys = ids.map((id, i) => this.line(`${prefix}.${id}`, cols[i % cols.length], id)).filter(Boolean);
      const s = this.plots[`${prefix}.${ids[0]}`];
      return s ? { title, unit, xlabel: 't [s]', x: s.t, ys, marks: [], cursor } : null;
    }
    panelOf(chs, title, unit, cursor) {
      const cols = ['#1f4e9c', '#a03000', '#0a6b2a'];
      const ys = chs.map(([ch, name], i) => this.line(ch, cols[i], name)).filter(Boolean);
      const s = this.plots[chs[0][0]];
      return s && ys.length ? { title, unit: unit || s.unit, xlabel: 't [s]', x: s.t, ys, marks: [], cursor } : null;
    }
    pathPanel() {
      const x = this.plots.body_x, y = this.plots.body_y; if (!x || !y) return null;
      return { title: '走った線 (横の位置 / 前の位置)', unit: 'm', xlabel: 'x [m]', x: x.v, ys: [{ v: y.v, col: '#1f4e9c' }], marks: [], cursor: this.pose[6 * this.k] / 1000, path: true };
    }
    plotClick(panel, xv) {
      if (panel && panel.path) { let b = 0, bd = Infinity; for (let i = 0; i < this.nT; i++) { const d = Math.abs(this.pose[6 * i] / 1000 - xv); if (d < bd) { bd = d; b = i; } } return b; }
      return this.near(xv);
    }
  };

  // tyre colour specs
  K2.loadSpec = D => ({ min: 0, max: Math.max(D.loads_max || 1, 1), log: false, unit: 'N', title: 'タイヤの色: 輪の荷重 (路面の垂直力)', note: '0 = 浮いた輪', marks: [] });
  K2.useSpec = () => ({ min: 0, max: 1.2, log: false, unit: '', title: 'タイヤの色: 摩擦の使用 |F| / μN', note: '1 = 摩擦の限り (すべる)', marks: [{ v: 1, label: '限り' }] });
})(window.HV);

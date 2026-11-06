// K2 replays shared by handling, powertrain and aero (viewer/src/common.py k2_motion, handling.k2_case):
// the vehicle (frame and part boxes as one rigid body, tyres per corner) posed from the record's series, the
// record's scene (tilt table or slope turned by its angle channel, ground, gates, cones, road profile, step),
// reaction / grip arrows at the contacts, a case switch (D.cases), and the camera following the vehicle.
window.HV = window.HV || {};
(function (HV) {
  'use strict';
  const K2 = HV.K2 = {};

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
      // normal force (web/css/app.css --green) and friction force (--blue); red is kept for a criterion's NG
      this.arrows = ctx.addArrows(); this.arr = M.corners.map(() => ({ n: this.arrows.add('#2f7d4f'), g: this.arrows.add('#2b59c3') }));
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
    // The record's scene in the screen's look=real (js/look.js), and the ground: K2's road is the plane z = 0
    // (k2_vehicle.road: flat / step / the random road's tracks) under the whole run (the vehicle's path from the
    // record's body_x / body_y, 4 m round it), the tilt table's floor at the table's foot, or the road profile itself.
    sceneGroup() {
      const T = window.THREE, ctx = this.ctx, view = ctx.view, g = new T.Group(), D = this.D;
      this.surround = [];
      let profile = false;
      for (const s of D.scene || []) {
        if (s.kind === 'gate') g.add(HV.Look.gate(s));
        else if (s.kind === 'cone') g.add(HV.Look.cone(s));
        else if (s.kind === 'road_profile' && s.profile) { this.profile(g, s.profile); profile = true; }
        else if (s.kind === 'ground') {
          // the road strip of the record (its friction): concrete laid on the ground, its top at the item's z
          const m = new T.Mesh(new T.PlaneGeometry(Math.max(s.s[0], 1), Math.max(s.s[1], 1)), HV.Look.concreteMaterial([s.s[0], s.s[1]]));
          m.material.polygonOffset = true; m.material.polygonOffsetFactor = -1; m.material.polygonOffsetUnits = -1;
          m.position.set(s.c[0], s.c[1], s.c[2]); m.receiveShadow = true; m.userData = { kind: 'scene', item: s }; g.add(m);
        } else if (s.s && (s.s[0] > 0 || s.s[1] > 0)) {
          // the slope and the tilt table are the road frame: drawn flat, the whole road turned by its angle
          g.add(HV.Look.box(s, view));
          if (s.angle_channel) this.turn = s;
        }
      }
      ctx.sceneGroup = g; ctx.root.add(g); ctx.has.scene = true;
      ctx.surround = this.surround;
      // the ground (main.js draws ctx.groundOverride): the path's box
      const P = this.pose, lo = [Infinity, Infinity], hi = [-Infinity, -Infinity];
      for (let k = 0; k < this.nT; k++) for (let a = 0; a < 2; a++) { lo[a] = Math.min(lo[a], P[6 * k + a]); hi[a] = Math.max(hi[a], P[6 * k + a]); }
      for (const s of D.scene || []) for (let a = 0; a < 2; a++) { lo[a] = Math.min(lo[a], s.c[a] - (s.s ? s.s[a] / 2 : 0)); hi[a] = Math.max(hi[a], s.c[a] + (s.s ? s.s[a] / 2 : 0)); }
      const x = [Math.floor((lo[0] - 6000) / 1000) * 1000, Math.ceil((hi[0] + 6000) / 1000) * 1000], y = [Math.floor((lo[1] - 5000) / 1000) * 1000, Math.ceil((hi[1] + 5000) / 1000) * 1000];
      if (this.turn) {
        const s = this.turn;   // a fixed level floor under the turned table, so the angle reads
        this.groundSpec = { z: s.c[2] - Math.max(s.s[2], 10) / 2 - 2, from: '傾斜台の下の床 (台の大きさと高さは記録の scene、床は台の下面の 2 mm 下に置いた)', fixed: true,
                            x: [s.c[0] - s.s[0] / 2 - 3000, s.c[0] + s.s[0] / 2 + 3000], y: [s.c[1] - s.s[1] / 2 - 4000, s.c[1] + s.s[1] / 2 + 4000] };
        this.surround.push(['傾斜台のちょうつがい', '台の低い側の縁 (傾く向きの側)', '台の大きさと位置は記録の scene、角度は記録の ' + s.angle_channel]);
      } else if (profile) {
        this.groundSpec = null;   // the road profile is the ground (drawn wide, see profile())
      } else {
        this.groundSpec = { z: 0, from: 'K2 の路面 (z = 0 の平面)。走った範囲は記録の車体の位置 (body_x, body_y) から', x, y };
      }
      ctx.groundOverride = this.groundSpec;
    }
    // the road profile (two wheel tracks, the record's scene): earth, linear across between the tracks, flat beyond
    async profile(g, p) {
      const T = window.THREE, [x, l, r] = await Promise.all([p.x, p.left, p.right].map(HV.unpack)), ht = p.half_track || 600;
      const n = x.length, pos = new Float32Array(n * 4 * 3), uv = new Float32Array(n * 4 * 2), idx = [], W = 5000;
      for (let i = 0; i < n; i++) {   // a ribbon: outside left, left track, right track, outside right
        const ys = [ht + W, ht, -ht, -ht - W], zs = [l[i], l[i], r[i], r[i]];
        for (let j = 0; j < 4; j++) { pos.set([x[i], ys[j], zs[j]], 3 * (4 * i + j)); uv.set([x[i] / HV.Look.DRAWN_SIDE_MM, ys[j] / HV.Look.DRAWN_SIDE_MM], 2 * (4 * i + j)); }
        if (i) for (let j = 0; j < 3; j++) { const a = 4 * (i - 1) + j, b = 4 * i + j; idx.push(a, b, a + 1, b, b + 1, a + 1); }
      }
      const geo = new T.BufferGeometry(); geo.setAttribute('position', new T.BufferAttribute(pos, 3)); geo.setAttribute('uv', new T.BufferAttribute(uv, 2)); geo.setIndex(idx); geo.computeVertexNormals();
      const mat = HV.Look.earthMaterial([HV.Look.DRAWN_SIDE_MM, HV.Look.DRAWN_SIDE_MM]); mat.side = T.DoubleSide;
      const m = new T.Mesh(geo, mat); m.receiveShadow = true; g.add(m);
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
      // the sun's shadow box follows the vehicle (the parts' box at the first pose, moved by the origin)
      const o0 = this.origin(0), dv = [0, 1, 2].map(a => o[a] - o0[a]);
      HV.Look.aimSun(view, this.ctx.lo.map((v, a) => v + dv[a]), this.ctx.hi.map((v, a) => v + dv[a]));
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
      const cols = ['#2b59c3', '#e9893a', '#2f7d4f', '#8e6bbf', '#6b665c'], ids = this.M.corners.map(c => c.id);
      const ys = ids.map((id, i) => this.line(`${prefix}.${id}`, cols[i % cols.length], id)).filter(Boolean);
      const s = this.plots[`${prefix}.${ids[0]}`];
      return s ? { title, unit, xlabel: 't [s]', x: s.t, ys, marks: [], cursor } : null;
    }
    panelOf(chs, title, unit, cursor) {
      const cols = ['#2b59c3', '#e9893a', '#2f7d4f'];   // web/css/app.css --blue, colormap orange, --green
      const ys = chs.map(([ch, name], i) => this.line(ch, cols[i], name)).filter(Boolean);
      const s = this.plots[chs[0][0]];
      return s && ys.length ? { title, unit: unit || s.unit, xlabel: 't [s]', x: s.t, ys, marks: [], cursor } : null;
    }
    pathPanel() {
      const x = this.plots.body_x, y = this.plots.body_y; if (!x || !y) return null;
      return { title: '走った線 (横の位置 / 前の位置)', unit: 'm', xlabel: 'x [m]', x: x.v, ys: [{ v: y.v, col: '#2b59c3' }], marks: [], cursor: this.pose[6 * this.k] / 1000, path: true };
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

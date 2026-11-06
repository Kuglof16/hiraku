// Strength (R1 / R1s, the step hit; viewer/src/strength.py), two models:
//  beam  (frame_static record): the beams coloured by each member's von Mises, deformed by the record's node
//        displacement x scale; load arrows; part boxes moved with the nodes the tool loads them on.
//  shell (saved frames of a frame_shell step-hit run): the shell mesh coloured by the element von Mises at each saved
//        instant, deformed x scale; the IIW hot spot marked; the mount forces as arrows and the mesh nodes each is
//        spread on (the load path); jumps to the joints; part boxes moved with the mesh nodes at their design nodes.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.strength = {
  label: '強度',
  async init(D, ctx) { return D.model === 'shell' ? HV.strengthShell(D, ctx) : HV.strengthBeam(D, ctx); },
};

// typed arrays of strength.py's pack_np (f32, u32, u16, i16, u8); 'i16s' = the time-differenced, byte-shuffled
// displacements, returned summed back as Int16Array T x N x 3 (q; times disp_scale = mm)
HV.unpackTyped = async function (p) {
  const bin = atob(p.b64), u8 = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
  const buf = await new Response(new Blob([u8]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
  if (p.dtype === 'f32') return new Float32Array(buf);
  if (p.dtype === 'u32') return new Uint32Array(buf);
  if (p.dtype === 'u16') return new Uint16Array(buf);
  if (p.dtype === 'i16') return new Int16Array(buf);
  if (p.dtype === 'u8') return new Uint8Array(buf);
  if (p.dtype === 'i16s') {
    const [T, N, C] = p.shape, M = N * C, b = new Uint8Array(buf), out = new Int16Array(T * M);
    for (let k = 0; k < T; k++) {
      const lo = k * 2 * M, hi = lo + M, o = k * M;
      for (let i = 0; i < M; i++) {
        let d = b[lo + i] | (b[hi + i] << 8); if (d > 32767) d -= 65536;
        out[o + i] = (k ? out[o - M + i] : 0) + d;
      }
    }
    return out;
  }
  throw new Error('unknown dtype ' + p.dtype);
};

const okngS = v => v === null || v === undefined ? '' : (v ? 'OK' : 'NG');

HV.strengthBeam = async function (D, ctx) {
  const beams = await ctx.addBeams();
  const boxes = ctx.addParts(D.parts, { opacity: 0.25 });
  const arrows = ctx.addArrows();
  const n0 = beams.nodes0, disp = await HV.unpack(D.disp.per_node), cur = new Float32Array(n0.length);
  const [pc, pt, pw] = await Promise.all(['c', 't', 'w'].map(k => HV.unpack(D.part_motion[k])));
  const pm = D.stress.per_member, S = { scale: 100, log: false };
  const COL = { load: '#c0392b', inertia: '#6a1b9a', reaction: '#0a6b2a', grip: '#a05a00' };
  const arr = D.vectors.map(v => ({ v, a: arrows.add(COL[v.kind] || '#333') }));
  const MM_PER_N = 0.1;   // 1 kN = 100 mm
  const spec = () => ({ min: S.log ? Math.max(0.1, D.stress.max * 1e-3) : 0, max: Math.max(D.stress.max, 1e-9), log: S.log, unit: D.stress.unit,
                        title: '部材の相当応力 (部材の中の最大)', marks: D.marks || [], note: `最大 ${HV.fmt(D.stress.max)} ${D.stress.unit}` });
  function update() {
    for (let i = 0; i < n0.length; i++) cur[i] = n0[i] + S.scale * disp[i];
    beams.setNodes(cur);
    const c = spec();
    beams.colourMembers(m => pm[m] === null ? [0.6, 0.6, 0.6] : HV.colour(c, pm[m]));
    for (let j = 0; j < D.parts.length; j++) {
      const r = HV.rigidPose(D.parts[j].c, [pc[3 * j], pc[3 * j + 1], pc[3 * j + 2]], [pt[3 * j], pt[3 * j + 1], pt[3 * j + 2]], [pw[3 * j], pw[3 * j + 1], pw[3 * j + 2]], S.scale);
      boxes.off[j] = r.off; boxes.rot[j] = r.q;
    }
    boxes.update();
    for (const o of arr) arrows.set(o.a, o.v.o, o.v.f, MM_PER_N);
  }
  const C = D.criterion;
  return {
    nT: 1, t0: 0, playMs: 100, viewDir: [0.9, -1.05, 0.75],
    timeNote: '1 時刻 (静的)',
    scale: { min: 0, max: 1000, step: 10, value: S.scale, label: '変形の倍率', note: `最大の変位 ${HV.fmt(D.disp.max)} mm (実寸)` },
    setScale(v) { S.scale = v; update(); },
    setTime() { update(); },
    setLog(on) { S.log = on; update(); },
    timeText() { return `静的 (K1 の梁)   最大の応力 ${HV.fmt(D.stress.max)} ${D.stress.unit}   最大の変位 ${HV.fmt(D.disp.max)} mm`; },
    hud() {
      return [(C ? `判定 ${C.quantity} ${HV.fmt(C.value)} ${C.op} ${HV.fmt(C.limit)} ${C.unit}: ${okngS(C.ok)}   ` : '') + `変形 ×${S.scale}、実寸ではない`,
              `色: 部材の相当応力   矢印 1 kN = 100 mm`];
    },
    contour: spec,
    plot() {
      const order = pm.map((v, m) => [v ?? 0, m]).sort((a, b) => b[0] - a[0]).slice(0, 20);
      return [{ title: '応力の大きい部材 (上位 20、横軸 = 順位)', unit: D.stress.unit, xlabel: '順位', x: order.map((_, i) => i + 1),
                ys: [{ v: order.map(o => o[0]), col: '#c0392b' }], marks: [], cursor: null }];
    },
    plotClick() { return null; },
    pickables: () => [beams.mesh],
    info(hit) {
      const m = beams.memberOf(hit), s = pm[m];
      return `<b>${HV.esc(D.frame.member_ids[m])}</b> ${HV.esc(D.frame.member_names[m])}<br>` + (s === null ? '応力の値なし' : `相当応力 ${HV.fmt(s)} ${HV.esc(D.stress.unit)}`);
    },
    extraInputs() {
      const hs = D.hot_spots || [];
      return (C ? `<div class="h" style="margin-top:4px">判定</div><table><tr><td>${HV.esc(C.quantity)}</td><td>${HV.fmt(C.value)} ${C.op} ${HV.fmt(C.limit)} ${HV.esc(C.unit)} <span class="${C.ok ? 'ok' : 'ng'}">${okngS(C.ok)}</span></td></tr></table>` : '') +
        (hs.length ? `<details><summary>ホットスポット (${hs.length})</summary><table>${hs.map(h => `<tr><td>${HV.esc(h.where)}</td><td>${HV.esc(h.quantity)}</td><td>${HV.fmt(h.value)} ${HV.esc(h.unit)}</td></tr>`).join('')}</table></details>` : '');
    },
  };
};

HV.strengthShell = async function (D, ctx) {
  const T = window.THREE, view = ctx.view;
  const [X, Q, mem, excl, vm, dq, poff] = await Promise.all([D.mesh.nodes, D.mesh.quads, D.mesh.member, D.mesh.excluded, D.vm, D.disp, D.part_off].map(HV.unpackTyped));
  const N = X.length / 3, E = Q.length / 4, nT = D.t.length, np = D.parts.length, ds = D.disp_scale;
  for (let i = 0; i < X.length; i += 3) for (let a = 0; a < 3; a++) { ctx.lo[a] = Math.min(ctx.lo[a], X[i + a]); ctx.hi[a] = Math.max(ctx.hi[a], X[i + a]); }
  // the mesh: 4 own vertices per quad (flat colour per element), two triangles
  const pos = new Float32Array(E * 12), col = new Float32Array(E * 12), idx = new Uint32Array(E * 6);
  for (let e = 0; e < E; e++) { const b = 4 * e; idx.set([b, b + 1, b + 2, b, b + 2, b + 3], 6 * e); }
  const geo = new T.BufferGeometry();
  const pA = new T.BufferAttribute(pos, 3), cA = new T.BufferAttribute(col, 3);
  geo.setAttribute('position', pA); geo.setAttribute('color', cA); geo.setIndex(new T.BufferAttribute(idx, 1));
  const mesh = new T.Mesh(geo, view.material(new T.MeshLambertMaterial({ vertexColors: true, side: T.DoubleSide })));
  ctx.root.add(mesh);
  const boxes = ctx.addParts(D.parts, { opacity: 0.18 });
  const arrows = ctx.addArrows();
  // load paths: the mesh nodes each mount force is spread on
  const paths = [];
  for (const m of D.mounts) paths.push({ m, xyz: await HV.unpackTyped(m.xyz), f: await HV.unpackTyped(m.f) });
  const pp = new Float32Array(paths.reduce((a, p) => a + p.xyz.length, 0)), pc = new Float32Array(pp.length);
  const MCOL = { FL: [0.85, 0.15, 0.1], FR: [0.1, 0.35, 0.85], RL: [0.1, 0.6, 0.2], RR: [0.6, 0.2, 0.7] };
  let o = 0; for (const p of paths) { pp.set(p.xyz, o); const c = MCOL[p.m.support] || [0.2, 0.2, 0.2]; for (let i = 0; i < p.xyz.length; i += 3) pc.set(c, o + i); p.o = o; o += p.xyz.length; }
  const pg = new T.BufferGeometry(); pg.setAttribute('position', new T.BufferAttribute(pp, 3)); pg.setAttribute('color', new T.BufferAttribute(pc, 3));
  const pts = new T.Points(pg, view.material(new T.PointsMaterial({ size: 5, sizeAttenuation: false, vertexColors: true, depthTest: false })));
  pts.renderOrder = 7; arrows.group.add(pts);
  const arr = paths.map(p => ({ p, a: arrows.add('#c0392b') }));
  for (const o of arr) arrows.set(o.a, o.p.m.hp, o.p.m.F, 0.1);
  // hot spot marker
  const hotM = new T.Mesh(new T.SphereGeometry(12, 16, 12), new T.MeshBasicMaterial({ color: 0xff00ff, depthTest: false, transparent: true, opacity: 0.85 }));
  hotM.renderOrder = 9; ctx.root.add(hotM);
  const S = { k: Math.max(0, D.hot.reduce((b, h, i, a) => (h && (!a[b] || Math.abs(h.value) > Math.abs(a[b].value)) ? i : b), 0)), scale: 50,
              range: (D.marks || []).length ? 'yield' : 'all', grey: true, log: false, sel: -1 };
  const vmAt = (k, e) => vm[k * E + e] / 10;
  const frameMax = k => { let m = 0; for (let e = 0; e < E; e++) if (!excl[e]) m = Math.max(m, vm[k * E + e]); return Math.max(m / 10, 1e-6); };
  const yieldTop = () => Math.max(...(D.marks || []).map(m => m.v), 1);
  const RNOTE = { yield: '上端 = フレームの降伏 (それ以上は赤)', all: '範囲は全時刻の最大', now: '範囲はこの時刻の最大' };
  const spec = () => { const mx = S.range === 'yield' ? yieldTop() : S.range === 'all' ? D.vm_max : frameMax(S.k);
    return { min: S.log ? Math.max(0.1, mx * 1e-3) : 0, max: mx, log: S.log, unit: 'MPa', title: '要素の相当応力 (外面と内面の大きい方)', marks: D.marks || [],
             note: RNOTE[S.range] + (S.grey ? '。灰色 = 除外の要素' : '') }; };
  const cur = new Float32Array(N * 3);
  function update() {
    const k = S.k, s = S.scale * ds, base = k * N * 3;
    for (let i = 0; i < N * 3; i++) cur[i] = X[i] + s * dq[base + i];
    for (let e = 0; e < E; e++) for (let c = 0; c < 4; c++) { const n = Q[4 * e + c]; pos[12 * e + 3 * c] = cur[3 * n]; pos[12 * e + 3 * c + 1] = cur[3 * n + 1]; pos[12 * e + 3 * c + 2] = cur[3 * n + 2]; }
    pA.needsUpdate = true; geo.computeVertexNormals(); geo.computeBoundingSphere();
    const sp = spec();
    for (let e = 0; e < E; e++) {
      const c = excl[e] && S.grey ? [0.75, 0.75, 0.75] : HV.colour(sp, Math.max(vmAt(k, e), sp.min));
      for (let j = 0; j < 4; j++) col.set(c, 12 * e + 3 * j);
    }
    cA.needsUpdate = true;
    for (let j = 0; j < np; j++) { const b = (k * np + j) * 3; boxes.off[j] = [S.scale * poff[b], S.scale * poff[b + 1], S.scale * poff[b + 2]]; }
    boxes.update();
    const h = D.hot[k]; hotM.visible = !!h; if (h) hotM.position.set(...h.pos);
    paths.forEach((p, j) => { const c = j === S.sel ? [0, 0, 0] : (MCOL[p.m.support] || [0.2, 0.2, 0.2]); for (let i = 0; i < p.xyz.length; i += 3) pc.set(c, p.o + i); });
    pg.attributes.color.needsUpdate = true;
  }
  const jump = c => { const t = view.controls.target, cam = view.camera.position, d = [cam.x - t.x, cam.y - t.y, cam.z - t.z], L = Math.hypot(...d) || 1;
                      t.set(c[0], c[1], c[2]); cam.set(c[0] + d[0] / L * 700, c[1] + d[1] / L * 700, c[2] + d[2] / L * 700); view.controls.update(); view.render(); };
  const hotText = k => { const h = D.hot[k]; return h ? `ホットスポット ${HV.fmt(h.value)} MPa (${h.joint}, ${h.member})` : 'ホットスポットなし'; };
  const ctl = {
    nT, t0: S.k, playMs: 220, viewDir: [0.9, -1.05, 0.75],
    timeNote: `保存した ${nT} 時刻 (各輪が段差に当たる前後、${HV.fmt(D.t[0])} - ${HV.fmt(D.t[nT - 1])} s)。最初はホットスポットが一番大きい時刻`,
    scale: { min: 0, max: 300, step: 5, value: S.scale, label: '変形の倍率', note: `最大の変位 ${HV.fmt(D.disp_max)} mm (実寸)` },
    setScale(v) { S.scale = v; update(); },
    setTime(i) { S.k = i; update(); },
    setLog(on) { S.log = on; update(); },
    timeText(k) { return `t = ${D.t[k].toFixed(3)} s   ${hotText(k)}   要素の最大 ${HV.fmt(D.el_max[k][0])} MPa (${D.el_max[k][2]})`; },
    hud() { return [`変形 ×${S.scale}、実寸ではない   矢印 = 取付の力 (t = ${D.forces_at_t_s} s、1 kN = 100 mm)、点 = その力を受け持つ節点`,
                    `色: 要素の相当応力   紫の球 = ホットスポット`]; },
    contour: spec,
    plot() {
      const tx = D.t, c = D.t[S.k];
      return [{ title: 'ホットスポットの応力 (IIW)', unit: 'MPa', xlabel: 't [s]', x: tx, ys: [{ v: D.hot.map(h => h ? Math.abs(h.value) : NaN), col: '#8e44ad' }], marks: [], cursor: c },
              { title: '要素の最大 (除外を除く)', unit: 'MPa', xlabel: 't [s]', x: tx, ys: [{ v: D.el_max.map(m => m[0]), col: '#c0392b' }], marks: [], cursor: c }];
    },
    plotClick(_, tv) { let b = 0, bd = Infinity; D.t.forEach((v, i) => { const d = Math.abs(v - tv); if (d < bd) { bd = d; b = i; } }); return b; },
    pickables: () => [mesh],
    info(hit) {
      const e = Math.floor(hit.faceIndex / 2), m = D.mesh.members[mem[e]];
      return `<b>${HV.esc(m)}</b> 要素 ${e}<br>相当応力 ${HV.fmt(vmAt(S.k, e))} MPa (t = ${D.t[S.k].toFixed(3)} s)` + (excl[e] ? '<br>除外の要素 (車輪の取付の当たり面)' : '');
    },
    extraInputs() {
      return `<details><summary>取付の力と荷重の経路 (${D.mounts.length})</summary><table>` +
        D.mounts.map(m => `<tr><td>${HV.esc(m.support)} ${HV.esc(m.mount)}</td><td>${HV.fmt(Math.hypot(...m.F))} N</td><td class="muted">${HV.esc(m.how)} (${m.n} 節点)</td></tr>`).join('') + '</table></details>';
    },
    panel(div) {
      const add = (tag, html) => { const e = document.createElement(tag); e.innerHTML = html; div.appendChild(e); return e; };
      add('h3', '色と表示 (シェル)');
      const r1 = add('div', `<label><input type="checkbox" id="stGrey" checked> 除外の要素を灰色</label> <select id="stRange"><option value="yield">降伏までで色</option><option value="all">全時刻の最大で色</option><option value="now">この時刻の最大で色</option></select>`);
      r1.className = 'row'; div.querySelector('#stRange').value = S.range;
      div.querySelector('#stGrey').onchange = e => { S.grey = e.target.checked; update(); HV.refresh(); };
      div.querySelector('#stRange').onchange = e => { S.range = e.target.value; update(); HV.refresh(); };
      add('h3', '部位へ');
      const j = add('div', ''); j.className = 'row';
      const hb = document.createElement('button'); hb.textContent = 'ホットスポット'; hb.onclick = () => { const h = D.hot[S.k]; if (h) jump(h.pos); }; j.appendChild(hb);
      for (const q of D.joints) { const b = document.createElement('button'); b.textContent = q.name; b.onclick = () => jump(q.centre); j.appendChild(b); }
      const all = document.createElement('button'); all.textContent = '全体'; all.onclick = () => { view.fit(ctx.lo, ctx.hi, ctl.viewDir); view.render(); }; j.appendChild(all);
      add('h3', '荷重の経路 (取付)');
      const list = add('div', ''); list.className = 'list';
      D.mounts.forEach((m, i) => {
        const d = document.createElement('div'); d.textContent = `${m.support} ${m.mount}: ${HV.fmt(Math.hypot(...m.F))} N`;
        d.onclick = () => { S.sel = S.sel === i ? -1 : i; list.querySelectorAll('div').forEach((x, k) => x.classList.toggle('on', k === S.sel)); update(); HV.refresh();
          if (S.sel >= 0) { jump(m.hp); const b = document.getElementById('info'); b.innerHTML = `<b>${HV.esc(m.support)} ${HV.esc(m.mount)}</b><br>力 (${m.F.map(v => HV.fmt(v)).join(', ')}) N<br>${HV.esc(m.how)}、${m.n} 節点 (黒い点)`; b.style.display = 'block'; document.getElementById('infohint').style.display = 'none'; } };
        list.appendChild(d);
      });
    },
  };
  update();
  return ctl;
};

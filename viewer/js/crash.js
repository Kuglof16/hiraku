// Crash (C1), two record kinds (viewer/src/crash.py):
//  A (crash, 1-D): the frame stays where it stands (A has no deformed shape); colour = the stored peak
//    stress x a(t)/a_max; the elastic deflection x a(t)/a_max x scale is off by default; crush(t) is a plot.
//  B (crash_beam): the run's own node positions over time (real size); colour = element axial strain or
//    fold angle computed from those positions; broken members marked; part boxes moved with their nodes;
//    the count and depth of anything beyond the wall face shown.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.crash = {
  label: '衝突',
  async init(D, ctx) { return D.model === 'B' ? HV.crashB(D, ctx) : HV.crashA(D, ctx); },
};

HV.crashA = async function (D, ctx) {
  const beams = await ctx.addBeams();
  const boxes = ctx.addParts(D.parts, { opacity: 0.25 });
  ctx.addScene();
  const arrows = ctx.addArrows();
  const nodes0 = beams.nodes0;
  const [t, a, x, F, disp] = await Promise.all([HV.unpack(D.t), HV.unpack(D.decel), HV.unpack(D.crush), HV.unpack(D.wall_force), HV.unpack(D.disp.per_node)]);
  const nT = t.length, amax = D.a_max || 1;
  let kPeak = 0; a.forEach((v, i) => { if (v > a[kPeak]) kPeak = i; });
  const S = { k: kPeak, scale: 0, log: false };
  const cur = new Float32Array(nodes0.length);
  const pm = D.stress.per_member;
  for (let i = 0; i < D.parts.length; i++) boxes.colour(i, [0.72, 0.72, 0.75]);
  const COL = { load: '#c0392b', inertia: '#6a1b9a', reaction: '#0a6b2a' };
  const arr = D.vectors.map(v => ({ v, a: arrows.add(COL[v.kind] || '#333') }));
  const MM_PER_N = 0.005;   // 1 kN = 5 mm
  const ratio = () => Math.max(0, a[S.k]) / amax;
  const spec = () => ({ min: S.log ? Math.max(0.1, D.stress.max * 1e-3) : 0, max: Math.max(D.stress.max, 1e-9), log: S.log, unit: D.stress.unit,
                        title: '部材の相当応力 × a(t)/a_max', marks: D.marks || [], note: `範囲はピーク時の最大 ${HV.fmt(D.stress.max)} ${D.stress.unit}` });
  function update() {
    const r = ratio(), f = S.scale * r;
    for (let i = 0; i < nodes0.length; i++) cur[i] = nodes0[i] + f * disp[i];
    beams.setNodes(cur);
    const c = spec();
    beams.colourMembers(m => pm[m] === null ? [0.6, 0.6, 0.6] : HV.colour(c, pm[m] * r));
    for (const o of arr) {
      const s = o.v.kind === 'reaction' || (o.v.kind === 'load' && o.v.at === 'cg') ? 1 : r;
      arrows.set(o.a, o.v.o, o.v.f.map(c => c * s), MM_PER_N);
    }
  }
  const tx = Array.from(t);
  return {
    nT, t0: kPeak, playMs: 30, viewDir: [0.25, -1.25, 0.45],
    timeNote: `t = 0 で接触、${nT} 点`,
    scale: { min: 0, max: 50, step: 1, value: S.scale, label: '弾性たわみの倍率 (A)', note: '0 = 描かない。つぶれた形ではない' },
    setScale(v) { S.scale = v; update(); },
    setTime(i) { S.k = i; update(); },
    setLog(on) { S.log = on; update(); },
    timeText(i) { return `t = ${(1000 * t[i]).toFixed(1)} ms   減速度 ${a[i].toFixed(2)} ${D.units.deceleration}   つぶれ ${x[i].toFixed(1)} ${D.units.crush}`; },
    hud() {
      return ['A: 1次元モデル。車体はその場に描く (つぶれはグラフだけ)' + (S.scale > 0 ? `   弾性たわみ ×${S.scale}、実寸ではない` : ''),
              `色: 部材の相当応力 (ピーク時の値 × a(t)/a_max = ×${ratio().toFixed(2)})   矢印 1 kN = 5 mm`];
    },
    contour: spec,
    plot() {
      const c = t[S.k], mk = [{ x: t[kPeak], label: 'ピーク', col: '#c0c' }];
      return [
        { title: '減速度', unit: D.units.deceleration, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(a), col: '#1f4e9c' }], marks: mk, cursor: c },
        { title: 'つぶれ (1次元)', unit: D.units.crush, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(x), col: '#a03000' }], marks: mk, cursor: c },
        { title: '壁の力', unit: D.units.wall_force, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(F), col: '#0a6b2a' }], marks: mk, cursor: c },
      ];
    },
    plotClick(_, tv) { let b = 0, bd = Infinity; t.forEach((v, i) => { const d = Math.abs(v - tv); if (d < bd) { bd = d; b = i; } }); return b; },
    pickables: () => [beams.mesh, boxes.mesh],
    info(hit) {
      const r = ratio();
      if (hit.object === boxes.mesh) { const p = D.parts[hit.instanceId]; return `<b>${HV.esc(p.id)}</b> ${HV.esc(p.name)}<br>動かさない (A は 1次元モデル)`; }
      const m = beams.memberOf(hit), s = pm[m], [na, nb] = D.frame.members[m], ids = D.frame.node_ids;
      const d = n => Math.hypot(disp[3 * n], disp[3 * n + 1], disp[3 * n + 2]);
      return `<b>${HV.esc(D.frame.member_ids[m])}</b> ${HV.esc(D.frame.member_names[m])}<br>` +
             (s === null ? 'no stress in the record' : `von Mises ${HV.fmt(s * r)} ${HV.esc(D.stress.unit)} now (stored peak ${HV.fmt(s)} x ${r.toFixed(3)})`) +
             `<br>elastic deflection now: ${HV.esc(ids[na])} ${HV.fmt(d(na) * r)} mm, ${HV.esc(ids[nb])} ${HV.fmt(d(nb) * r)} mm (real, before the scale)`;
    },
  };
};

HV.crashB = async function (D, ctx) {
  const beams = await ctx.addBeams();
  const boxes = ctx.addParts(D.parts, { opacity: 0.22 });
  ctx.addScene();
  const M = D.motion, B = D.beyond;
  const [tf, pos, strain, fold, poff, ts, dec, cr] = await Promise.all([M.t, M.pos, D.colour.strain, D.colour.fold, D.part_off,
    D.series.t, D.series.decel, D.series.crush].map(HV.unpack));
  const nF = tf.length, n = beams.nodes0.length / 3, ne = D.frame.members.length, np = D.parts.length;
  // the grown box for the camera: every frame
  for (let i = 0; i < pos.length; i += 3) for (let a = 0; a < 3; a++) { ctx.lo[a] = Math.min(ctx.lo[a], pos[i + a]); ctx.hi[a] = Math.max(ctx.hi[a], pos[i + a]); }
  const S = { k: 0, what: 'fold', log: false };
  const near = tv => { let b = 0, bd = Infinity; ts.forEach((v, i) => { const d = Math.abs(v - tv); if (d < bd) { bd = d; b = i; } }); return b; };
  let kPeak = 0; dec.forEach((v, i) => { if (v > dec[kPeak]) kPeak = i; });
  let fPeak = 0; tf.forEach((v, k) => { if (Math.abs(v - ts[kPeak]) < Math.abs(tf[fPeak] - ts[kPeak])) fPeak = k; });
  for (let i = 0; i < np; i++) boxes.colour(i, [0.72, 0.72, 0.75]);
  const brokenSet = k => new Set(M.broken[k] || []);
  const val = (k, e) => S.what === 'strain' ? strain[k * ne + e] : fold[k * ne + e];
  // colour range: the largest of this frame (members not broken), so the shape at each instant reads;
  // the run's largest is said in the note
  const frameMax = k => { let mx = 0; const br = brokenSet(k); for (let e = 0; e < ne; e++) if (!br.has(D.frame.member_ids[e])) mx = Math.max(mx, Math.abs(val(k, e))); return Math.max(mx, 1e-6); };
  const spec = () => { const m = frameMax(S.k); return S.what === 'strain'
    ? { min: -m, max: m, log: false, unit: '%', title: '要素の軸ひずみ (− 縮み、+ 伸び)',
        note: `範囲はこの時刻の最大 (全体の最大 ${HV.fmt(D.colour.strain_absmax)} %)。薄い灰色 = 破断` }
    : { min: S.log ? Math.max(0.01, m * 1e-3) : 0, max: m, log: S.log, unit: '°', title: '要素の端の折れ角 (t = 0 から)',
        note: `範囲はこの時刻の最大 (全体の最大 ${HV.fmt(D.colour.fold_max)}°)。薄い灰色 = 破断` }; };
  function update() {
    const k = S.k;
    beams.setNodes(pos.subarray(k * n * 3, (k + 1) * n * 3));
    const c = spec(), br = brokenSet(k);
    beams.colourMembers(e => br.has(D.frame.member_ids[e]) ? [0.8, 0.8, 0.8] : HV.colour(c, val(k, e)));
    boxes.off = D.parts.map((_, j) => [poff[(k * np + j) * 3], poff[(k * np + j) * 3 + 1], poff[(k * np + j) * 3 + 2]]); boxes.update();
  }
  const tx = Array.from(ts);
  const ctl = {
    nT: nF, t0: fPeak, playMs: 60, viewDir: [0.25, -1.25, 0.45],
    timeNote: `t = 0 で接触、記録の ${nF} 時刻 (補間なし)`,
    scale: { min: 1, max: 1, step: 1, value: 1, label: '変形 ×1 (実寸)', note: '計算の節点位置そのもの' },
    setScale() {},
    setTime(i) { S.k = i; update(); },
    setLog(on) { S.log = on; update(); },
    timeText(k) { const j = near(tf[k]); return `t = ${(1000 * tf[k]).toFixed(2)} ms   減速度 ${dec[j].toFixed(2)} ${D.units.deceleration}   つぶれ ${cr[j].toFixed(1)} ${D.units.crush}`; },
    hud() {
      const k = S.k, br = M.broken[k] || [];
      return [`変形 ×1 (実寸)   色: ${S.what === 'strain' ? '要素の軸ひずみ %' : '要素の端の折れ角 °'}   破断した部材 ${br.length}`,
              `壁面の先のフレーム節点 ${B.nodes[k]}` + (B.nodes[k] ? ` (最大 ${B.depth[k].toFixed(1)} mm)` : '') + `   壁面の先の部品の箱 ${B.parts[k]} (箱は壁に当たらない扱い)`];
    },
    contour: spec,
    plot() {
      const c = tf[S.k], mk = [{ x: ts[kPeak], label: 'ピーク', col: '#c0c' }];
      return [
        { title: '減速度', unit: D.units.deceleration, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(dec), col: '#1f4e9c' }], marks: mk, cursor: c },
        { title: 'つぶれ (前端から座席の取付まで)', unit: D.units.crush, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(cr), col: '#a03000' }], marks: mk, cursor: c },
        { title: '壁面の先のフレーム節点', unit: '個', xlabel: 't [s]', x: Array.from(tf), ys: [{ v: B.nodes, col: '#c00' }], marks: [], cursor: c },
      ];
    },
    plotClick(_, tv) { let b = 0, bd = Infinity; tf.forEach((v, i) => { const d = Math.abs(v - tv); if (d < bd) { bd = d; b = i; } }); return b; },
    pickables: () => [beams.mesh, boxes.mesh],
    info(hit) {
      const k = S.k;
      if (hit.object === boxes.mesh) { const p = D.parts[hit.instanceId], o = boxes.off[hit.instanceId]; return `<b>${HV.esc(p.id)}</b> ${HV.esc(p.name)}<br>移動 (${o.map(v => v.toFixed(1)).join(', ')}) mm、箱は変形しない`; }
      const e = beams.memberOf(hit), mid = D.frame.member_ids[e], [na, nb] = D.frame.members[e], ids = D.frame.node_ids;
      return `<b>${HV.esc(mid)}</b> ${HV.esc(D.frame.member_names[e])}<br>要素 ${HV.esc(ids[na])} - ${HV.esc(ids[nb])}: 軸ひずみ ${HV.fmt(strain[k * ne + e])} %、折れ角 ${HV.fmt(fold[k * ne + e])}°` +
             (brokenSet(k).has(mid) ? '<br><b>この時刻までに破断</b>' : '');
    },
    panel(div) {
      const h = document.createElement('h3'); h.textContent = '色の量 (B)'; div.appendChild(h);
      for (const [k, lab] of [['strain', '要素の軸ひずみ %'], ['fold', '要素の端の折れ角 °']]) {
        const l = document.createElement('label'); l.style.display = 'block';
        const r = document.createElement('input'); r.type = 'radio'; r.name = 'crashcol'; r.id = 'crashcol_' + k; r.checked = S.what === k;
        r.onchange = () => { S.what = k; update(); HV.refresh(); };
        l.append(r, ' ' + lab); div.appendChild(l);
      }
    },
  };
  update();
  return ctl;
};

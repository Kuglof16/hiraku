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
  const T = window.THREE;
  const beams = await ctx.addBeams();
  // part boxes cut at the wall face (they have no contact with the wall: past it they are not drawn)
  const wallPlane = new T.Plane(new T.Vector3(-1, 0, 0), D.wall_x_mm);
  const boxes = ctx.addParts(D.parts, { opacity: 0.22, clip: [wallPlane] });
  ctx.addScene();
  const M = D.motion, B = D.beyond, C = D.colour, hasRec = !!C.stress;
  const [tf, pos, strain, fold, poff, ts, dec, cr] = await Promise.all([M.t, M.pos, C.strain, C.fold, D.part_off,
    D.series.t, D.series.decel, D.series.crush].map(HV.unpack));
  const [stress, hinge] = hasRec ? await Promise.all([HV.unpack(C.stress), HV.unpack(C.hinge)]) : [null, null];
  const force = {};
  if (hasRec) for (const [m, p] of Object.entries(C.force)) force[m] = await HV.unpack(p);
  const nF = tf.length, n = beams.nodes0.length / 3, ne = D.frame.members.length, np = D.parts.length;
  // the grown box for the camera: every frame
  for (let i = 0; i < pos.length; i += 3) for (let a = 0; a < 3; a++) { ctx.lo[a] = Math.min(ctx.lo[a], pos[i + a]); ctx.hi[a] = Math.max(ctx.hi[a], pos[i + a]); }
  const stopMember = D.stop && D.stop.member;
  const S = { k: 0, what: hasRec ? 'stress' : 'fold', log: false, sel: stopMember && force[stopMember] ? stopMember : null };
  const near = tv => { let b = 0, bd = Infinity; ts.forEach((v, i) => { const d = Math.abs(v - tv); if (d < bd) { bd = d; b = i; } }); return b; };
  let kPeak = 0; dec.forEach((v, i) => { if (v > dec[kPeak]) kPeak = i; });
  for (let i = 0; i < np; i++) boxes.colour(i, [0.72, 0.72, 0.75]);
  const brokenSet = k => new Set(M.broken[k] || []);
  const arr = { stress, hinge, strain, fold };
  const val = (k, e) => arr[S.what][k * ne + e];
  // colour range: the largest of this frame (members not broken), so the shape at each instant reads;
  // the run's largest is said in the note
  const frameMax = k => { let mx = 0; const br = brokenSet(k); for (let e = 0; e < ne; e++) if (!br.has(D.frame.member_ids[e])) mx = Math.max(mx, Math.abs(val(k, e))); return Math.max(mx, 1e-6); };
  const SPEC = {
    stress: m => ({ min: -m, max: m, log: false, unit: 'MPa', title: '部材の軸応力 (− 圧縮、+ 引張)', note: `軸力 / 断面積。範囲はこの時刻の最大 (全体 ${HV.fmt(C.stress_absmax)})。灰色 = 破断` }),
    hinge: m => ({ min: S.log ? Math.max(0.01, m * 1e-3) : 0, max: m, log: S.log, unit: '°', title: '部材の端のヒンジ回転 (大きい方)', note: `範囲はこの時刻の最大 (全体 ${HV.fmt(C.hinge_max)}°)。灰色 = 破断` }),
    strain: m => ({ min: -m, max: m, log: false, unit: '%', title: '要素の軸ひずみ (− 縮み、+ 伸び)', note: `範囲はこの時刻の最大 (全体 ${HV.fmt(C.strain_absmax)} %)。灰色 = 破断` }),
    fold: m => ({ min: S.log ? Math.max(0.01, m * 1e-3) : 0, max: m, log: S.log, unit: '°', title: '要素の端の折れ角 (t = 0 から)', note: `範囲はこの時刻の最大 (全体 ${HV.fmt(C.fold_max)}°)。灰色 = 破断` }),
  };
  const LABEL = { stress: '部材の軸応力 MPa', hinge: '端のヒンジ回転 °', strain: '要素の軸ひずみ %', fold: '要素の端の折れ角 °' };
  const spec = () => SPEC[S.what](frameMax(S.k));
  function update() {
    const k = S.k;
    beams.setNodes(pos.subarray(k * n * 3, (k + 1) * n * 3));
    const c = spec(), br = brokenSet(k);
    beams.colourMembers(e => br.has(D.frame.member_ids[e]) ? [0.8, 0.8, 0.8] : HV.colour(c, val(k, e)));
    boxes.off = D.parts.map((_, j) => [poff[(k * np + j) * 3], poff[(k * np + j) * 3 + 1], poff[(k * np + j) * 3 + 2]]); boxes.update();
  }
  const tx = Array.from(ts), tfx = Array.from(tf), sm = D.summary || {}, u = D.units;
  const stopText = () => { const s = D.stop; return s && s.t_s !== null ? `停止 ${(1000 * s.t_s).toFixed(1)} ms: ${s.member} が折れ、座席側から ${s.piece_pct ?? '?'} % の塊が離れた` : `記録の終わり ${(1000 * tf[nF - 1]).toFixed(1)} ms`; };
  const ctl = {
    nT: nF, t0: 0, playMs: 60, viewDir: [0.25, -1.25, 0.45], times: tfx,
    timeNote: `t = 0 で接触、記録の ${nF} 時刻 (補間なし)。再生は実時間の倍率`,
    scale: { min: 1, max: 1, step: 1, value: 1, label: '変形 ×1 (実寸)', note: '計算の節点位置そのもの' },
    setScale() {},
    setTime(i) { S.k = i; update(); },
    setLog(on) { S.log = on; update(); },
    timeText(k) { const j = near(tf[k]); return `t = ${(1000 * tf[k]).toFixed(2)} ms   減速度 ${dec[j].toFixed(1)} ${u.deceleration}   つぶれ ${cr[j].toFixed(0)} ${u.crush}   破断 ${(M.broken[k] || []).length}`; },
    hud() {
      return [stopText(),
              `ピーク ${HV.fmt(sm.peak_decel)} ${u.deceleration}、平均 ${HV.fmt(sm.mean_decel)} ${u.deceleration}、つぶれ ${HV.fmt(sm.crush_end)} ${u.crush} (停止まで)   色: ${LABEL[S.what]}`];
    },
    contour: spec,
    plot() {
      const c = tf[S.k], mk = [{ x: ts[kPeak], label: 'ピーク', col: '#c0c' }];
      const P = [
        { title: '減速度', unit: u.deceleration, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(dec), col: '#1f4e9c' }], marks: mk, cursor: c },
        { title: 'つぶれ (前端から座席の取付まで)', unit: u.crush, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(cr), col: '#a03000' }], marks: mk, cursor: c },
      ];
      if (S.sel && force[S.sel]) P.push({ title: `軸力 ${S.sel} (部材をクリックで切替)`, unit: 'kN', xlabel: 't [s]', x: tfx, ys: [{ v: Array.from(force[S.sel], v => v / 1000), col: '#0a6b2a' }], marks: [], cursor: c });
      return P;
    },
    plotClick(_, tv) { let b = 0, bd = Infinity; tf.forEach((v, i) => { const d = Math.abs(v - tv); if (d < bd) { bd = d; b = i; } }); return b; },
    pickables: () => [beams.mesh, boxes.mesh],
    info(hit) {
      const k = S.k;
      if (hit.object === boxes.mesh) { const p = D.parts[hit.instanceId], o = boxes.off[hit.instanceId]; return `<b>${HV.esc(p.id)}</b> ${HV.esc(p.name)}<br>移動 (${o.map(v => v.toFixed(1)).join(', ')}) mm、箱は変形しない。壁面の先は描かない`; }
      const e = beams.memberOf(hit), mid = D.frame.member_ids[e], [na, nb] = D.frame.members[e], ids = D.frame.node_ids;
      if (force[mid]) { S.sel = mid; setTimeout(HV.refresh, 0); }
      return `<b>${HV.esc(mid)}</b> ${HV.esc(D.frame.member_names[e])}<br>` +
             (hasRec ? `軸力 ${force[mid] ? HV.fmt(force[mid][k] / 1000) + ' kN' : '-'}、軸応力 ${HV.fmt(stress[k * ne + e])} MPa (断面積 ${HV.fmt(C.area_mm2[mid])} mm²)、` +
                       `端のヒンジ回転 ${HV.fmt(hinge[k * ne + e])}° (${(C.hinge_ends[mid] || []).map(HV.esc).join(', ')})<br>` : '') +
             `要素 ${HV.esc(ids[na])} - ${HV.esc(ids[nb])}: 軸ひずみ ${HV.fmt(strain[k * ne + e])} %、折れ角 ${HV.fmt(fold[k * ne + e])}°` +
             (brokenSet(k).has(mid) ? '<br><b>この時刻までに破断</b>' : '') + (mid === stopMember ? '<br><b>この部材が折れて計算が止まった</b>' : '');
    },
    extraInputs() {
      const hs = D.hot_spots || [], lr = sm.least_room;
      const okng = h => h.ok === null || h.ok === undefined ? '' : `<span class="${h.ok ? 'ok' : 'ng'}">${h.ok ? 'OK' : 'NG'}</span>`;
      return `<div class="h" style="margin-top:4px">結果 (停止まで)</div><table>` +
        `<tr><td>停止</td><td>${HV.esc(stopText())}</td></tr>` +
        `<tr><td>減速度</td><td>ピーク ${HV.fmt(sm.peak_decel)} ${u.deceleration}、平均 ${HV.fmt(sm.mean_decel)} ${u.deceleration}</td></tr>` +
        `<tr><td>つぶれ</td><td>${HV.fmt(sm.crush_end)} ${u.crush}</td></tr>` +
        (lr ? `<tr><td>乗員の箱までのすき間</td><td>最小 ${HV.fmt(lr.value)} ${HV.esc(lr.unit)} (${HV.esc(lr.where)}) ${okng(lr)}</td></tr>` : '') + '</table>' +
        (hs.length ? `<details><summary>ホットスポット (${hs.length})</summary><table>${hs.map(h => `<tr><td>${HV.esc(h.where)}</td><td>${HV.fmt(h.value)} ${HV.esc(h.unit)}</td><td>${okng(h)}</td></tr>`).join('')}</table></details>` : '');
    },
    panel(div) {
      const h = document.createElement('h3'); h.textContent = '色の量 (B)'; div.appendChild(h);
      for (const [k, lab] of [['stress', '部材の軸応力 (軸力 / 断面積)'], ['hinge', '部材の端のヒンジ回転 (大きい方)'], ['fold', '要素の端の折れ角 (形から)']]) {
        if (!hasRec && k !== 'fold') continue;
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

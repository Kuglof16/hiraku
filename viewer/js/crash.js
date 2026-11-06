// Crash (C1), two record kinds (viewer/src/crash.py):
//  A (crash, 1-D): the frame stays where it stands (A has no deformed shape); colour = the stored peak
//    stress x a(t)/a_max; the elastic deflection x a(t)/a_max x scale is off by default; crush(t) is a plot.
//  B (crash_beam): the run's own node positions over time (real size); colour = element axial strain or
//    fold angle computed from those positions; broken members marked; part boxes moved with their nodes;
//    the count and depth of anything beyond the wall face shown.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.crash = {
  label: 'Crash',
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
                        title: 'von Mises per member, x a(t)/a_max', marks: D.marks || [], note: `stored at the peak (${HV.fmt(D.stress.max)} ${D.stress.unit}); colour range fixed to it` });
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
    timeNote: `${nT} instants of the tool's series (t = 0 at contact). The vehicle is not moved: A is a 1-D model.`,
    scale: { min: 0, max: 50, step: 1, value: S.scale, label: 'Elastic frame deflection scale (A)', note: 'The linear frame deflection solved at the peak, x a(t)/a_max x this factor. Not a crushed shape; 0 = not drawn.' },
    setScale(v) { S.scale = v; update(); },
    setTime(i) { S.k = i; update(); },
    setLog(on) { S.log = on; update(); },
    timeText(i) { return `t = ${(1000 * t[i]).toFixed(1)} ms   deceleration ${a[i].toFixed(2)} ${D.units.deceleration}   crush ${x[i].toFixed(1)} ${D.units.crush}   wall force ${F[i].toFixed(1)} ${D.units.wall_force}`; },
    hud() {
      const r = ratio();
      let mx = 0, mm = -1; pm.forEach((v, m) => { if (v !== null && v * r > mx) { mx = v * r; mm = m; } });
      return ['A is a 1-D model: no deformed shape is drawn. The vehicle stands still; crush(t) = ' + x[S.k].toFixed(1) + ' mm is in the plot only.',
              `stress now = stored peak stress x a(t)/a_max = x ${r.toFixed(3)} (frame solved once, at t = ${(1000 * t[kPeak]).toFixed(1)} ms)`,
              `largest member stress now ${HV.fmt(mx)} ${D.stress.unit}` + (mm >= 0 ? ` (${D.frame.member_ids[mm]})` : '') +
              (S.scale > 0 ? `; elastic deflection x ${S.scale} (not to scale), largest stored ${HV.fmt(D.disp.max)} ${D.disp.unit}` : '; deflection not drawn (scale 0)'),
              'arrows: wall loads (red) x a(t)/a_max, inertia (purple) x a(t)/a_max, wheel reactions and gravity (green, red at the cg) as stored; 1 kN = 5 mm'];
    },
    contour: spec,
    plot() {
      const c = t[S.k], mk = [{ x: t[kPeak], label: 'peak (frame solved here)', col: '#c0c' }];
      return [
        { title: 'deceleration', unit: D.units.deceleration, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(a), col: '#1f4e9c' }], marks: mk, cursor: c },
        { title: 'crush (1-D, not drawn in 3D)', unit: D.units.crush, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(x), col: '#a03000' }], marks: mk, cursor: c },
        { title: 'wall force', unit: D.units.wall_force, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(F), col: '#0a6b2a' }], marks: mk, cursor: c },
      ];
    },
    plotClick(_, tv) { let b = 0, bd = Infinity; t.forEach((v, i) => { const d = Math.abs(v - tv); if (d < bd) { bd = d; b = i; } }); return b; },
    pickables: () => [beams.mesh, boxes.mesh],
    info(hit) {
      const r = ratio();
      if (hit.object === boxes.mesh) { const p = D.parts[hit.instanceId]; return `<b>${HV.esc(p.id)}</b> ${HV.esc(p.name)} (${HV.esc(p.kind)})<br>not moved (A is a 1-D model)`; }
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
    ? { min: -m, max: m, log: false, unit: '%', title: 'element axial strain (from its length change)',
        note: `range = this frame's largest; the run's largest ${HV.fmt(D.colour.strain_absmax)} %. Computed from the run's node positions; - shortened, + stretched; broken members light grey` }
    : { min: S.log ? Math.max(0.01, m * 1e-3) : 0, max: m, log: S.log, unit: 'deg', title: 'fold angle at the element ends (change from t = 0)',
        note: `range = this frame's largest; the run's largest ${HV.fmt(D.colour.fold_max)} deg. Computed from the run's node positions; a plastic hinge shows here; broken members light grey` }; };
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
    timeNote: `${nF} frames stored by the run (t = 0 at contact); the shape is the run's own node positions, real size, not interpolated.`,
    scale: { min: 1, max: 1, step: 1, value: 1, label: 'Shape scale (B: fixed at 1, real size)', note: 'The deformed shape is the run\'s node positions; it is not scaled.' },
    setScale() {},
    setTime(i) { S.k = i; update(); },
    setLog(on) { S.log = on; update(); },
    timeText(k) { const j = near(tf[k]); return `t = ${(1000 * tf[k]).toFixed(2)} ms   deceleration ${dec[j].toFixed(2)} ${D.units.deceleration}   crush ${cr[j].toFixed(1)} ${D.units.crush} (series at ${(1000 * ts[j]).toFixed(1)} ms)`; },
    hud() {
      const k = S.k, br = M.broken[k] || [];
      let mx = 0, me = -1; for (let e = 0; e < ne; e++) { if (brokenSet(k).has(D.frame.member_ids[e])) continue; const v = Math.abs(val(k, e)); if (v > mx) { mx = v; me = e; } }
      return [
        `WALL: frame nodes beyond the wall face (x = ${HV.fmt(D.wall_x_mm)} mm) now ${B.nodes[k]}` + (B.nodes[k] ? `, deepest ${B.depth[k].toFixed(1)} mm` : '') +
          `; over the run ${B.nodes_any} nodes, deepest ${B.node_depth_max.toFixed(1)} mm (tolerance ${B.tol_mm} mm)`,
        `part boxes past the wall face now ${B.parts[k]}` + (B.parts[k] ? `, up to ${B.part_depth[k].toFixed(1)} mm` : '') +
          ` (${B.parts[0]} already at t = 0; part boxes do not touch the wall in the tool)`,
        `largest ${S.what === 'strain' ? 'axial strain' : 'fold'} now ${HV.fmt(S.what === 'strain' ? val(k, me) : mx)} ${S.what === 'strain' ? '%' : 'deg'}` + (me >= 0 ? ` (${D.frame.member_ids[me]})` : '') +
          `; broken members by now ${br.length}${br.length ? ': ' + br.join(', ') : ''}`,
        'shape: the run\'s node positions, real size; colour: computed from the same positions (the record holds no member stresses)'];
    },
    contour: spec,
    plot() {
      const c = tf[S.k], mk = [{ x: ts[kPeak], label: 'peak deceleration', col: '#c0c' }];
      return [
        { title: 'deceleration (crash_beam)', unit: D.units.deceleration, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(dec), col: '#1f4e9c' }], marks: mk, cursor: c },
        { title: 'crush (front-most nodes to the seat mounts)', unit: D.units.crush, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(cr), col: '#a03000' }], marks: mk, cursor: c },
        { title: 'frame nodes beyond the wall face', unit: 'nodes', xlabel: 't [s]', x: Array.from(tf), ys: [{ v: B.nodes, col: '#c00' }], marks: [], cursor: c },
      ];
    },
    plotClick(_, tv) { let b = 0, bd = Infinity; tf.forEach((v, i) => { const d = Math.abs(v - tv); if (d < bd) { bd = d; b = i; } }); return b; },
    pickables: () => [beams.mesh, boxes.mesh],
    info(hit) {
      const k = S.k;
      if (hit.object === boxes.mesh) { const p = D.parts[hit.instanceId], o = boxes.off[hit.instanceId]; return `<b>${HV.esc(p.id)}</b> ${HV.esc(p.name)} (${HV.esc(p.kind)})<br>moved with its carried_by nodes by (${o.map(v => v.toFixed(1)).join(', ')}) mm; not deformed`; }
      const e = beams.memberOf(hit), mid = D.frame.member_ids[e], [na, nb] = D.frame.members[e], ids = D.frame.node_ids;
      return `<b>${HV.esc(mid)}</b> ${HV.esc(D.frame.member_names[e])}<br>element ${HV.esc(ids[na])} - ${HV.esc(ids[nb])}: axial strain ${HV.fmt(strain[k * ne + e])} %, fold ${HV.fmt(fold[k * ne + e])} deg` +
             (brokenSet(k).has(mid) ? '<br><b>member broken by now</b> (an element of it removed)' : '');
    },
    panel(div) {
      const h = document.createElement('h3'); h.textContent = 'Colour (B)'; div.appendChild(h);
      for (const [k, lab] of [['strain', 'element axial strain %'], ['fold', 'fold angle at the ends, deg']]) {
        const l = document.createElement('label'); l.style.display = 'block';
        const r = document.createElement('input'); r.type = 'radio'; r.name = 'crashcol'; r.id = 'crashcol_' + k; r.checked = S.what === k;
        r.onchange = () => { S.what = k; update(); HV.refresh(); };
        l.append(r, ' ' + lab); div.appendChild(l);
      }
    },
    extraInputs() { return `<div class="muted">B: ${HV.esc(D.notes[0])}</div>`; },
  };
  ctx.layers.parts = false;
  update();
  return ctl;
};

// Crash (C1): frame beams coloured by the per-member stress, deformed by the per-node displacement,
// both the stored peak values x a(t) / a_max; the whole vehicle moved forward by crush(t) into the
// wall box; plots of deceleration, crush and wall force over t.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.crash = {
  label: 'Crash',
  async init(D, ctx) {
    const beams = await ctx.addBeams();
    const boxes = ctx.addParts(D.parts, { opacity: 0.25 });
    ctx.addScene();
    const arrows = ctx.addArrows();
    const nodes0 = beams.nodes0;
    const [t, a, x, F, disp] = await Promise.all([HV.unpack(D.t), HV.unpack(D.decel), HV.unpack(D.crush), HV.unpack(D.wall_force), HV.unpack(D.disp.per_node)]);
    const nT = t.length, amax = D.a_max || 1;
    let kPeak = 0; a.forEach((v, i) => { if (v > a[kPeak]) kPeak = i; });
    const S = { k: kPeak, scale: 10, log: false };
    const cur = new Float32Array(nodes0.length);
    const pm = D.stress.per_member;
    for (const p of D.parts) boxes.colour(D.parts.indexOf(p), [0.72, 0.72, 0.75]);
    // arrows: wall loads scale with a(t) like the stored fields; reactions and gravity stay
    const COL = { load: '#c0392b', inertia: '#6a1b9a', reaction: '#0a6b2a' };
    const arr = D.vectors.map(v => ({ v, a: arrows.add(COL[v.kind] || '#333') }));
    const MM_PER_N = 0.005;   // 1 kN = 5 mm
    const ratio = () => Math.max(0, a[S.k]) / amax;
    const spec = () => ({ min: S.log ? Math.max(0.1, D.stress.max * 1e-3) : 0, max: Math.max(D.stress.max, 1e-9), log: S.log, unit: D.stress.unit,
                          title: 'von Mises per member, x a(t)/a_max', marks: D.marks || [], note: `stored at the peak (${HV.fmt(D.stress.max)} ${D.stress.unit}); colour range fixed to it` });
    function update() {
      const r = ratio(), dx = x[S.k], f = S.scale * r;
      for (let i = 0; i < nodes0.length; i += 3) {
        cur[i] = nodes0[i] + dx + f * disp[i]; cur[i + 1] = nodes0[i + 1] + f * disp[i + 1]; cur[i + 2] = nodes0[i + 2] + f * disp[i + 2];
      }
      beams.setNodes(cur);
      const c = spec();
      beams.colourMembers(m => pm[m] === null ? [0.6, 0.6, 0.6] : HV.colour(c, pm[m] * r));
      boxes.off = D.parts.map(() => [dx, 0, 0]); boxes.update();
      beams.ghost.position.set(dx, 0, 0);
      for (const o of arr) {
        const s = o.v.kind === 'reaction' || (o.v.kind === 'load' && o.v.at === 'cg') ? 1 : r;   // gravity at the cg and reactions do not scale
        arrows.set(o.a, [o.v.o[0] + dx, o.v.o[1], o.v.o[2]], o.v.f.map(c => c * s), MM_PER_N);
      }
    }
    const tx = Array.from(t);
    const ctl = {
      nT, t0: kPeak, playMs: 30, viewDir: [0.25, -1.25, 0.45],   // from the right side, so the wall is not in front of the camera
      timeNote: `${nT} instants of the tool's series (t = 0 at contact). Stress and deflection at t = stored peak values x a(t)/a_max.`,
      scale: { min: 0, max: 50, step: 1, value: S.scale, label: 'Frame deflection scale', note: 'Deflection x this factor (not to scale). The move into the wall, crush(t), is real size.' },
      setScale(v) { S.scale = v; update(); },
      setTime(i) { S.k = i; update(); },
      setLog(on) { S.log = on; update(); },
      timeText(i) { return `t = ${(1000 * t[i]).toFixed(1)} ms   deceleration ${a[i].toFixed(2)} ${D.units.deceleration}   crush ${x[i].toFixed(1)} ${D.units.crush}   wall force ${F[i].toFixed(1)} ${D.units.wall_force}`; },
      hud() {
        const r = ratio();
        let mx = 0, mm = -1; pm.forEach((v, m) => { if (v !== null && v * r > mx) { mx = v * r; mm = m; } });
        return [`stress now = stored peak stress x a(t)/a_max = x ${r.toFixed(3)} (the frame was solved once, at the peak, t = ${(1000 * t[kPeak]).toFixed(1)} ms)`,
                `largest member stress now ${HV.fmt(mx)} ${D.stress.unit}` + (mm >= 0 ? ` (${D.frame.member_ids[mm]})` : '') + `; deflection x ${S.scale} (not to scale), largest stored ${HV.fmt(D.disp.max)} ${D.disp.unit}`,
                `vehicle moved forward by crush(t) = ${x[S.k].toFixed(1)} mm into the wall (a translation, not a crushed shape)`,
                'arrows: wall loads (red) x a(t)/a_max, inertia (purple) x a(t)/a_max, wheel reactions and gravity (green, red at the cg) as stored; 1 kN = 5 mm'];
      },
      contour: spec,
      plot() {
        const cur = t[S.k], mk = [{ x: t[kPeak], label: 'peak (frame solved here)', col: '#c0c' }];
        return [
          { title: 'deceleration', unit: D.units.deceleration, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(a), col: '#1f4e9c' }], marks: mk, cursor: cur },
          { title: 'crush', unit: D.units.crush, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(x), col: '#a03000' }], marks: mk, cursor: cur },
          { title: 'wall force', unit: D.units.wall_force, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(F), col: '#0a6b2a' }], marks: mk, cursor: cur },
        ];
      },
      plotClick(_, tv) { let b = 0, bd = Infinity; t.forEach((v, i) => { const d = Math.abs(v - tv); if (d < bd) { bd = d; b = i; } }); return b; },
      pickables: () => [beams.mesh, boxes.mesh],
      info(hit) {
        const r = ratio();
        if (hit.object === boxes.mesh) { const p = D.parts[hit.instanceId]; return `<b>${HV.esc(p.id)}</b> ${HV.esc(p.name)} (${HV.esc(p.kind)})<br>moved with the vehicle by crush(t) = ${x[S.k].toFixed(1)} mm`; }
        const m = beams.memberOf(hit), s = pm[m], [na, nb] = D.frame.members[m], ids = D.frame.node_ids;
        const d = n => Math.hypot(disp[3 * n], disp[3 * n + 1], disp[3 * n + 2]);
        return `<b>${HV.esc(D.frame.member_ids[m])}</b> ${HV.esc(D.frame.member_names[m])}<br>` +
               (s === null ? 'no stress in the record' : `von Mises ${HV.fmt(s * r)} ${HV.esc(D.stress.unit)} now (stored peak ${HV.fmt(s)} x ${r.toFixed(3)})`) +
               `<br>deflection now: ${HV.esc(ids[na])} ${HV.fmt(d(na) * r)} mm, ${HV.esc(ids[nb])} ${HV.fmt(d(nb) * r)} mm (real, before the scale)`;
      },
    };
    update();
    return ctl;
  },
};

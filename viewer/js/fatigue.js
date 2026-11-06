// Fatigue (R3, strength reliability; viewer/src/fatigue.py): the frame beams coloured by each member's Miner damage
// over the distance (the largest weld point of the member, log scale); the road profile of the record's scene passing
// under the wheels at the case's speed (road point x drawn at x_front + x - v t, as the tool meets it); the tyres at
// the supports. Plots: the worst point's stress and the wheel loads over the simulated road, and the S-N curve with
// the stress ranges counted at the worst point (log-log, x = log10 of cycles).
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.fatigue = {
  label: '疲労',
  async init(D, ctx) {
    const T = window.THREE;
    const beams = await ctx.addBeams();
    ctx.addParts(D.parts, { opacity: 0.18 });
    const dm = D.damage, pm = dm.per_member;
    const S = { k: 0, log: true };
    const st = D.stress ? { t: await HV.unpack(D.stress.t), v: await HV.unpack(D.stress.values) } : null;
    const wl = [];
    for (const w of D.wheels) wl.push({ ...w, tv: await HV.unpack(w.t), vv: await HV.unpack(w.values) });
    const tt = st ? st.t : (wl[0] ? wl[0].tv : new Float32Array([0, 1])), nT = tt.length, tx = Array.from(tt);
    // tyres
    const tyres = new HV.Wheels(ctx.view, D.wheels.map(w => ({ c: w.c, r: w.r, width: w.width, spring: false })));
    ctx.root.add(tyres.group);
    // the road: one line per track, moved along x with time
    const R = D.road, road = [];
    if (R) {
      const rx = await HV.unpack(R.x);
      // each track a strip (the tyre's width + 100 mm) of the screen's concrete following the record's heights; the
      // ground plane (main.js) at ground_z_mm leaves the strips' bands open
      const tw = Math.max(...D.wheels.map(w => w.width || 150)) + 100;
      for (const tr of R.tracks) {
        const rz = await HV.unpack(tr.z), pos = new Float32Array(rx.length * 6), uv = new Float32Array(rx.length * 4), idx = [];
        for (let i = 0; i < rx.length; i++) { uv.set([rx[i] / 2500, 0, rx[i] / 2500, tw / 2500], 4 * i); if (i) { const a = 2 * (i - 1); idx.push(a, a + 2, a + 1, a + 2, a + 3, a + 1); } }
        const g = new T.BufferGeometry(); g.setAttribute('position', new T.BufferAttribute(pos, 3)); g.setAttribute('uv', new T.BufferAttribute(uv, 2)); g.setIndex(idx);
        const mat = HV.Look.concreteMaterial([2500, 2500]); mat.side = T.DoubleSide;
        const l = new T.Mesh(g, mat); l.receiveShadow = true;
        road.push({ rx, rz, pos, l, y: tr.y_mm, hw: tw / 2 });
      }
      ctx.has.scene = true; ctx.sceneGroup = new T.Group(); for (const r of road) ctx.sceneGroup.add(r.l); ctx.root.add(ctx.sceneGroup);
      ctx.lo[2] = Math.min(ctx.lo[2], R.ground_z_mm - 60);
      ctx.groundOverride = { z: R.ground_z_mm, from: '設計の支持点: 車輪の中心の高さ − 半径 (路面の凹凸の基準の高さ)', holes: road.map(r => [r.y - r.hw, r.y + r.hw]), x: [R.x_front_mm - 6000, R.x_front_mm + 4000] };
      ctx.surround = [['路面の凹凸', `2 本の通り道 (y = ${road.map(r => HV.fmt(r.y)).join(', ')} mm)、幅 ${HV.fmt(tw)} mm で描く`,
                       `記録の scene の road_profile。速さ ${HV.fmt((R.speed_mm_s || 0) * 3.6 / 1000)} km/h で車輪の下を流れる (条件の speed)。車輪は動かない (応力は影響係数から)`]];
    }
    const XSPAN = 6000;   // road drawn from 2 m behind the rear wheels to 4 m ahead of the front
    function moveRoad() {
      if (!R) return;
      const vt = (R.speed_mm_s || 0) * tt[S.k];
      for (const r of road) {
        for (let i = 0; i < r.rx.length; i++) {
          const x = R.x_front_mm + r.rx[i] - vt, z = R.ground_z_mm + r.rz[i];
          r.pos.set([x, r.y - r.hw, z, x, r.y + r.hw, z], 6 * i);
        }
        r.l.geometry.attributes.position.needsUpdate = true; r.l.geometry.computeVertexNormals(); r.l.geometry.computeBoundingSphere();
        const i0 = Math.max(0, r.rx.findIndex(x => R.x_front_mm + x - vt > R.x_front_mm - XSPAN)), i1 = r.rx.findIndex(x => R.x_front_mm + x - vt > R.x_front_mm + 4000);
        r.l.geometry.setDrawRange(6 * i0, 6 * Math.max(1, (i1 < 0 ? r.rx.length : i1) - i0 - 1));
      }
    }
    const lo = () => S.log ? Math.max(dm.min_pos, dm.max * 1e-6) : 0;
    const marks = [{ v: 1, label: '寿命 (D = 1)' }];
    const spec = () => ({ min: lo(), max: Math.max(dm.max, 1e-12), log: S.log, unit: '', title: 'マイナー損傷 (部材の中の最大)',
                          marks, note: `${HV.fmt((D.criterion || {}).limit ?? 1)} で寿命。灰色 = 値なし` });
    function update() {
      const c = spec();
      beams.colourMembers(m => pm[m] === null ? [0.7, 0.7, 0.7] : HV.colour(c, Math.max(pm[m], c.min)));
      moveRoad();
      // the wheel load at this time lifts nothing (the frame does not move); the tyres stand at the supports
    }
    const okng = v => v === null || v === undefined ? '' : (v ? 'OK' : 'NG');
    const C = D.criterion;
    const near = (arr, v) => { let b = 0, bd = Infinity; arr.forEach((x, i) => { const d = Math.abs(x - v); if (d < bd) { bd = d; b = i; } }); return b; };
    const ctl = {
      nT, t0: 0, playMs: 30, viewDir: [0.55, -1.2, 0.5],
      timeNote: `路面 ${HV.fmt(tx[nT - 1])} s 分 (記録の ${nT} 点)。損傷はこれを距離まで伸ばした値`,
      scale: { min: 1, max: 1, step: 1, value: 1, label: '変形なし', note: 'フレームの変形は記録にない (応力は影響係数から)' },
      setScale() {},
      setTime(i) { S.k = i; update(); },
      setLog(on) { S.log = on; update(); },
      timeText(k) {
        const j = st ? k : 0;
        return `t = ${HV.fmt(tt[k])} s   ` + (st ? `${D.stress.member} ${HV.fmt(st.v[j])} MPa   ` : '') +
               wl.map(w => `${w.support} ${HV.fmt(w.vv[near(w.tv, tt[k])] / 1000)} kN`).join('  ');
      },
      hud() {
        return [(C ? `判定 損傷 ${HV.fmt(C.value)} ${C.op} ${HV.fmt(C.limit)}: ${okng(C.ok)}   ` : '') + `一番傷む部材 ${dm.worst}   寿命 ${HV.fmt(D.life_km)} km`,
                `色: マイナー損傷 (部材の溶接点の最大、${S.log ? '対数' : '線形'})   路面 ${R ? R.label : 'なし'}`];
      },
      contour: spec,
      plot() {
        const c = tt[S.k], P = [];
        if (st) P.push({ title: `応力 ${D.stress.member} (一番傷む点)`, unit: 'MPa', xlabel: 't [s]', x: tx, ys: [{ v: Array.from(st.v), col: '#e9893a' }], marks: [], cursor: c });
        if (wl.length) P.push({ title: '輪の力 (上下)', unit: 'kN', xlabel: 't [s]', x: Array.from(wl[0].tv),
          ys: wl.map((w, j) => ({ name: w.support, v: Array.from(w.vv, v => v / 1000), col: ['#1f4e9c', '#0a6b2a', '#a05a00', '#8e44ad'][j % 4] })), marks: [], cursor: c });
        if (D.sn) {
          // one x axis (log10 of cycles) for both: the union of their points, each series 0 where it has none (the log
          // plot leaves out values <= 0, so each line runs through its own points only)
          const pts = D.sn.x.map((x, i) => [Math.log10(x), D.sn.values[i], 0]);
          if (D.ranges) D.ranges.x.forEach((x, i) => pts.push([Math.log10(Math.max(x, 1)), 0, D.ranges.values[i]]));
          pts.sort((a, b) => a[0] - b[0]);
          P.push({ title: 'S-N 曲線と数えた応力範囲', unit: 'MPa', xlabel: 'log10 回数', x: pts.map(p => p[0]), logy: true,
                   ys: [{ name: 'S-N', v: pts.map(p => p[1]), col: '#333' }, { name: '範囲', v: pts.map(p => p[2]), col: '#e9893a' }], marks: [], cursor: null });
        }
        return P;
      },
      plotClick(i, xv) { return i < 2 ? near(tx, xv) : null; },
      pickables: () => [beams.mesh],
      info(hit) {
        const m = beams.memberOf(hit), mid = D.frame.member_ids[m], v = pm[m];
        return `<b>${HV.esc(mid)}</b> ${HV.esc(D.frame.member_names[m])}<br>` +
               (v === null ? '損傷の値なし' : `マイナー損傷 ${HV.fmt(v)} (寿命の ${HV.fmt(v * 100)} %)`) + (mid === dm.worst ? '<br><b>一番傷む部材</b>' : '');
      },
      extraInputs() {
        return `<div class="h" style="margin-top:4px">結果</div><table>` +
          (C ? `<tr><td>判定</td><td>損傷 ${HV.fmt(C.value)} ${C.op} ${HV.fmt(C.limit)} <span class="${C.ok ? 'ok' : 'ng'}">${okng(C.ok)}</span></td></tr>` : '') +
          `<tr><td>寿命</td><td>${HV.fmt(D.life_km)} km (${HV.esc(dm.worst)})</td></tr></table>` +
          (D.bands.length ? `<details><summary>応力範囲ごとの損傷 (${D.bands.length})</summary><table>${D.bands.map(b => `<tr><td>${HV.esc(b.name)}</td><td>${HV.fmt(b.value)}</td><td>${HV.fmt(100 * (b.share || 0))} %</td></tr>`).join('')}</table></details>` : '');
      },
    };
    update();
    return ctl;
  },
};

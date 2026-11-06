// Powertrain (viewer/src/powertrain.py): the K2 replays of launch (P3) and the step climb (P1) with the wheel
// torque and slip per wheel; drive_energy (P4, K3, no motion): the parts standing still, the battery coloured by
// the share of its capacity used up to t, and plots of speed, grade, battery power, current and charge.
window.HV = window.HV || {};
HV.fields = HV.fields || {};
HV.fields.powertrain = {
  label: HV.FIELD_LABEL.powertrain,   // js/names.js: the contract's DOMAIN_NAMES_JA
  async init(D, ctx) {
    HV.K2.useCase('powertrain', D);
    const C = D.cases[HV.fields.powertrain.sel];
    if (C.kind === 'energy') return HV.energyField(C, ctx);
    const R = await HV.K2.Replay.make(D, ctx), dr = C.drive;
    const [tq, sl] = await Promise.all([HV.unpack(dr.torque), HV.unpack(dr.slip)]);
    const nw = D.motion.corners.length, ids = D.motion.corners.map(c => c.id), tt = Array.from(R.t);
    const per = (a, i) => tt.map((_, k) => a[k * nw + i]);
    const cols = ['#2b59c3', '#e9893a', '#2f7d4f', '#8e6bbf'];
    // wheel power = sum over wheels of torque x spin rate (from the slip's own omega is not kept: d spin / dt here)
    const omega = (k, i) => { const a = Math.max(0, k - 1), b = Math.min(R.nT - 1, k + 1); return (R.w(b, i, 'wheel_spin') - R.w(a, i, 'wheel_spin')) * Math.PI / 180 / Math.max(1e-9, R.t[b] - R.t[a]); };
    const power = tt.map((_, k) => ids.reduce((s, _i, i) => s + tq[k * nw + i] * omega(k, i), 0) / 1000);
    const stepTxt = (D.scene || []).find(s => s.kind === 'step');
    return HV.K2.controller('powertrain', D, R, {
      col: 'slip',
      cols: { slip: ['すべり (ωr − v) / v', { min: -0.3, max: 0.3, log: false, unit: '', title: 'タイヤの色: すべり (ωr − v) / v', note: '+ 駆動で空転、− 制動', marks: [] }, (k, i) => sl[k * nw + i]] },
      hud: () => (stepTxt ? '段差: ' + stepTxt.label : `駆動輪 ${dr.driven.join(', ')}`),
      timeExtra: (R, k) => `   輪のトルク (合計) ${HV.fmt(ids.reduce((s, _i, i) => s + tq[k * nw + i], 0))} N·m   輪の出力 ${HV.fmt(power[k])} kW`,
      plots(R, c) {
        return [R.panelOf([['speed', '速さ']], '速さ', 'km/h', c),
                { title: '輪のトルク (駆動輪で等分)', unit: 'N·m', xlabel: 't [s]', x: tt, ys: ids.map((id, i) => ({ name: id, v: per(tq, i), col: cols[i % 4] })), marks: [], cursor: c },
                { title: 'すべり (ωr − v) / v', unit: '', xlabel: 't [s]', x: tt, ys: ids.map((id, i) => ({ name: id, v: per(sl, i), col: cols[i % 4] })), marks: [], cursor: c },
                { title: '輪の出力 (トルク × 回転の速さ)', unit: 'kW', xlabel: 't [s]', x: tt, ys: [{ v: power, col: '#e9893a' }], marks: [], cursor: c }];
      },
    });
  },
};

HV.energyField = async function (C, ctx) {
  const boxes = ctx.addParts(C.parts, { opacity: 0.35 }), S = C.series, B = C.battery;
  await ctx.addBeams();
  const [t, v, g, p, i, soc] = await Promise.all([S.t, S.speed, S.grade, S.power, S.current, S.soc].map(HV.unpack));
  const nT = t.length, used = k => soc[0] - soc[k], usedEnd = Math.max(1e-9, B.used_pct || used(nT - 1));
  const spec = () => ({ min: 0, max: usedEnd, log: false, unit: '%', title: '電池の箱の色: 使った容量の割合', note: `終わりで ${HV.fmt(usedEnd)} %`, marks: [] });
  const tx = Array.from(t), st = { k: 0 };
  const update = () => { for (let j = 0; j < C.parts.length; j++) boxes.colour(j, j === B.index ? HV.colour(spec(), used(st.k)) : HV.GREY); boxes.update(); };
  update();
  const P = (title, unit, a, col) => ({ title, unit, xlabel: 't [s]', x: tx, ys: [{ v: Array.from(a), col }], marks: [], cursor: t[st.k] });
  return {
    nT, t0: 0, times: tx, speed: 50, partMode: 'field', viewDir: [0.9, -1.2, 0.7],
    timeNote: `記録の ${nT} 時刻 (${HV.fmt(t[nT - 1])} s)。再生は実時間の倍率`,
    scale: { min: 1, max: 1, step: 1, value: 1, label: '形は動かさない', note: '電力の計算は形と位置を使わない' },
    setScale() {}, setTime(k) { st.k = k; update(); },
    timeText: k => `t = ${HV.fmt(t[k])} s   速さ ${HV.fmt(v[k])} km/h   坂 ${HV.fmt(g[k])} %   電池の出力 ${HV.fmt(p[k])} kW   残り ${HV.fmt(soc[k])} %`,
    hud: () => [HV.K2.judgeText(C.judge) || C.title, `色: 電池 (${B.part || '-'}) = この時刻までに使った容量の割合、ほかの部品は灰色 (部品の扱いの色は切替で)`],
    contour: spec,
    plot: () => [P('速さ', 'km/h', v, '#1f4e9c'), P('坂', '%', g, '#0a6b2a'), P('電池の出力', 'kW', p, '#e9893a'), P('電池の電流', 'A', i, '#8e44ad'), P('電池の残り', '%', soc, '#1f4e9c')],
    plotClick(_, x) { let b = 0, bd = Infinity; t.forEach((tv, j) => { const d = Math.abs(tv - x); if (d < bd) { bd = d; b = j; } }); return b; },
    pickables: () => [boxes.mesh],
    info(hit) { const j = hit.instanceId, pp = C.parts[j]; return `<b>${HV.esc(pp.id)}</b> ${HV.esc(pp.name)}` + (j === B.index ? `<br>電池: 使った容量 ${HV.fmt(used(st.k))} % (終わり ${HV.fmt(usedEnd)} %)` : ''); },
    extraInputs: () => HV.K2.judgeRow(C),
    panel: div => HV.K2.casePanel('powertrain', ctx.D, div),
  };
};
